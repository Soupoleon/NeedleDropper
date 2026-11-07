"""Guard Chromium's profile singleton without trusting window titles or reused PIDs.

Only a browser with the selected executable, exact private profile and a loopback
NeedleDropper player URL can be retired. Unknown holders get a fresh profile instead.
The Windows adapter is lazy; tests inject a process table and never touch the desktop.
"""
from __future__ import annotations

import ctypes
import json
import logging
import ntpath
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

log = logging.getLogger("needledropper.deck.profile")


def _path(value: Any) -> str:
    return ntpath.normcase(ntpath.abspath(str(value))).rstrip("\\/")


def _option(args: tuple[str, ...], name: str) -> Optional[str]:
    for index, arg in enumerate(args):
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
        if arg == name and index + 1 < len(args):
            return args[index + 1]
    return None


@dataclass(frozen=True)
class BrowserProcess:
    pid: int
    parent: int
    image: str
    created: int  # Windows FILETIME, not a rounded timestamp
    args: tuple[str, ...]

    def holds(self, profile: Path) -> bool:
        value = _option(self.args, "--user-data-dir")
        return value is not None and _path(value) == _path(profile)

    def player(self, image: str, profile: Path) -> bool:
        if _path(self.image) != _path(image) or not self.holds(profile) or not self.created:
            return False
        try:
            url = urlsplit(_option(self.args, "--app") or "")
            return (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost")
                    and url.path == "/player" and not url.username and not url.password)
        except ValueError:
            return False


class WindowsProcesses:
    """Read browser command lines through CIM; signal only handles with checked identities."""

    def __init__(self, windows: Any = None) -> None:
        from ctypes import wintypes as wt

        self.wt = wt
        self.windows = windows
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        k = self.kernel
        k.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
        k.OpenProcess.restype = wt.HANDLE
        k.CloseHandle.argtypes = (wt.HANDLE,)
        k.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
        k.GetProcessTimes.argtypes = (wt.HANDLE, *([ctypes.POINTER(wt.FILETIME)] * 4))
        k.TerminateProcess.argtypes = (wt.HANDLE, wt.UINT)
        k.CreateFileW.argtypes = (wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE)
        k.CreateFileW.restype = wt.HANDLE
        self.shell.CommandLineToArgvW.argtypes = (wt.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        self.shell.CommandLineToArgvW.restype = ctypes.POINTER(wt.LPWSTR)
        k.LocalFree.argtypes = (ctypes.c_void_p,)

    def _args(self, line: str) -> tuple[str, ...]:
        count = ctypes.c_int()
        argv = self.shell.CommandLineToArgvW(line, ctypes.byref(count))
        if not argv:
            return ()
        try:
            return tuple(argv[i] for i in range(count.value))
        finally:
            self.kernel.LocalFree(argv)

    def snapshot(self) -> list[BrowserProcess]:
        # Fixed code, no interpolated user paths or arguments; never log the command lines.
        script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                  "@(Get-CimInstance Win32_Process -Filter "
                  "\"Name='chrome.exe' OR Name='msedge.exe'\" | ForEach-Object { "
                  "[pscustomobject]@{pid=$_.ProcessId; parent=$_.ParentProcessId; image=$_.ExecutablePath; "
                  "created=$_.CreationDate.ToUniversalTime().ToFileTimeUtc().ToString(); "
                  "command=$_.CommandLine} }) | ConvertTo-Json -Compress")
        result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise OSError("browser process inspection failed")
        rows = json.loads(result.stdout.lstrip("\ufeff").strip() or "[]")
        if isinstance(rows, dict):
            rows = [rows]
        processes = []
        for row in rows:
            pid, image = int(row["pid"]), str(row.get("image") or "")
            stamp = int(row.get("created") or 0)
            handle = self.kernel.OpenProcess(0x1000, False, pid)
            if handle:
                try:
                    actual_image, actual_stamp = self._metadata(handle)
                    # CIM's datetime has microsecond precision; retain the exact FILETIME from
                    # this handle for every later check. A reused PID fails either comparison.
                    if _path(actual_image) == _path(image) and 0 <= actual_stamp - stamp < 10:
                        stamp = actual_stamp
                    else:
                        stamp = 0
                finally:
                    self.kernel.CloseHandle(handle)
            else:
                stamp = 0
            processes.append(BrowserProcess(pid, int(row["parent"]), image, stamp,
                                             self._args(str(row.get("command") or ""))))
        return processes

    def _metadata(self, handle: Any) -> tuple[str, int]:
        wt, k = self.wt, self.kernel
        image, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
        stamps = [wt.FILETIME() for _ in range(4)]
        ok = k.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size))
        ok = ok and k.GetProcessTimes(handle, *(ctypes.byref(stamp) for stamp in stamps))
        created = (int(stamps[0].dwHighDateTime) << 32) | int(stamps[0].dwLowDateTime)
        return (image.value, created) if ok else ("", 0)

    def _checked_handle(self, process: BrowserProcess, terminate: bool = False) -> Any:
        k = self.kernel
        handle = k.OpenProcess(0x1000 | (0x0001 if terminate else 0), False, process.pid)
        if not handle:
            return None
        image, created = self._metadata(handle)
        if not created or _path(image) != _path(process.image) or created != process.created:
            k.CloseHandle(handle)
            return None
        return handle

    def same(self, process: BrowserProcess) -> bool:
        handle = self._checked_handle(process)
        if not handle:
            return False
        self.kernel.CloseHandle(handle)
        return True

    def terminate(self, process: BrowserProcess) -> bool:
        handle = self._checked_handle(process, terminate=True)
        if not handle:
            return False
        try:
            return bool(self.kernel.TerminateProcess(handle, 0))
        finally:
            self.kernel.CloseHandle(handle)

    def close_windows(self, process: BrowserProcess) -> None:
        if self.windows is None:
            return
        for hwnd, pid, _cls, _title, _visible, _owned in self.windows.top_windows():
            if pid == process.pid and self.same(process) and self.windows.window_pid(hwnd) == pid:
                self.windows.close(hwnd)

    def locked(self, profile: Path) -> bool:
        # An old *file* is harmless; an exclusively held file is not. Never remove either.
        for name in ("lockfile", "SingletonLock"):
            handle = self.kernel.CreateFileW(str(profile / name), 0x80000000, 0, None, 3, 0, None)
            if handle == ctypes.c_void_p(-1).value:
                if ctypes.get_last_error() not in (2, 3):
                    return True  # sharing violation, access denied, or unknown: isolate
            else:
                self.kernel.CloseHandle(handle)
        return False


class ProfileGuard:
    def __init__(self, processes: Any, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.processes, self.clock, self.sleep = processes, clock, sleep

    def _fresh(self, profile: Path) -> Path:
        fresh = profile.parent / (profile.name + "-" + uuid.uuid4().hex)
        log.warning("the player profile is occupied or cannot be verified; using a fresh player profile")
        return fresh

    def select(self, image: str, profile: Path) -> Path:
        try:
            holders = [p for p in self.processes.snapshot() if p.holds(profile) and not _option(p.args, "--type")]
            if any(not p.player(image, profile) for p in holders):
                return self._fresh(profile)
            for holder in holders:
                log.info("closing an old NeedleDropper player browser (pid %s)", holder.pid)
                self.retire(holder, wait_s=3.0)
            if any(self.processes.same(p) for p in holders) or self.processes.locked(profile):
                return self._fresh(profile)
            return profile
        except Exception as exc:  # inspection must fail closed, without logging command lines
            log.warning("could not verify the player profile holder (%s)", type(exc).__name__)
            return self._fresh(profile)

    def identify(self, pid: int, image: str, profile: Path) -> Optional[BrowserProcess]:
        try:
            for process in self.processes.snapshot():
                if process.pid == pid and process.player(image, profile) and self.processes.same(process):
                    return process
        except Exception:  # an unverified window is never adopted
            pass
        return None

    def retire(self, holder: BrowserProcess, *, wait_s: float = 3.0) -> None:
        """Close, then end only checked browser descendants from this process incarnation."""
        rows = self.processes.snapshot()
        if holder not in rows or not self.processes.same(holder):
            return
        tree = [holder]
        known = {holder.pid}
        while True:
            more = [p for p in rows if p.pid not in known and p.parent in known
                    and _path(p.image) == _path(holder.image) and p.created >= holder.created]
            if not more:
                break
            tree.extend(more)
            known.update(p.pid for p in more)
        for process in tree:
            self.processes.close_windows(process)
        deadline = self.clock() + max(0.0, wait_s)
        while self.processes.same(holder) and self.clock() < deadline:
            self.sleep(0.1)
        # PIDs may have been reused during the wait: the adapter checks image + creation time
        # again on the very handle it terminates. Never use taskkill /T on an unchecked PID.
        for process in reversed(tree):
            self.processes.terminate(process)
