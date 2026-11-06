"""The YouTube player window's Win32 side: it never takes the focus from the game.

Real-client findings #8, #11 and #13 (docs/review/real-client-2026-11-05.md) and the measured recipe
in docs/research/no-focus-playback.md:

* Chromium activates a window once, when it creates it, and no launch flag prevents that (research
  3.1 / 3.2). So the player window is created **once**, while the game is not the foreground window
  (at the app's start, normally at Windows sign-in), and kept for the whole session.
* As soon as it appears it is found by the launched process tree (class ``Chrome_WidgetWin_1``,
  never by title alone: the page renames itself per drop), restyled ``WS_EX_NOACTIVATE |
  WS_EX_TOOLWINDOW`` minus ``WS_EX_APPWINDOW`` (no activation by clicks, not in the taskbar or
  Alt-Tab) and hidden with ``SWP_HIDEWINDOW``.
* While a YouTube song plays it is shown always on top at a corner of the monitor that holds the
  game (``deck.playerCorner``, top-right by default; YouTube's rules want the player visible),
  with ``SWP_NOACTIVATE | SWP_SHOWWINDOW``, which never activates; afterwards it is hidden again.

Everything Win32 goes through :class:`Win32` (ctypes, stdlib only) so tests use a fake. On other
systems, or when ctypes is missing, :func:`make_player_window` returns None and the deck keeps the
plain window it always had.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("needledropper.deck.window")

CORNERS = ("topright", "topleft", "bottomright", "bottomleft")
DEFAULT_CORNER = "topright"
CHROME_CLASS = "Chrome_WidgetWin_1"
TITLE_SUFFIX = "NeedleDropper"     # player.html's <title>, and every title player.js gives itself
MARGIN_PX = 16                     # from the monitor's edges, at 96 dpi (scaled with the monitor)
ADOPT_TIMEOUT_S = 15.0

# Win32 constants (winuser.h)
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
SWP_NOOWNERZORDER = 0x0200
WM_CLOSE = 0x0010
MONITOR_DEFAULTTOPRIMARY = 1


def normalize_corner(value: Any) -> str:
    v = str(value or "").strip().lower().replace("-", "").replace(" ", "")
    return v if v in CORNERS else DEFAULT_CORNER


def corner_position(corner: str, monitor: tuple[int, int, int, int], size: tuple[int, int],
                    margin: int = MARGIN_PX) -> tuple[int, int]:
    """Top-left (x, y) of a window of ``size`` at ``corner`` of ``monitor`` = (left, top, right,
    bottom), ``margin`` pixels from its edges (pure; physical pixels)."""
    left, top, right, bottom = monitor
    w, h = size
    corner = normalize_corner(corner)
    x = right - margin - w if corner.endswith("right") else left + margin
    y = bottom - margin - h if corner.startswith("bottom") else top + margin
    return int(x), int(y)


class Win32:
    """The handful of user32 / kernel32 calls the player window needs (ctypes only)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ctypes, self.wt = ctypes, wt
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u, k = self.user32, self.kernel32
        LONG_PTR = ctypes.c_ssize_t
        self.WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                        ("szExeFile", wt.WCHAR * 260)]

        self.MONITORINFO, self.PROCESSENTRY32W = MONITORINFO, PROCESSENTRY32W

        def proto(dll, name, restype, *argtypes):
            fn = getattr(dll, name)
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        self._EnumWindows = proto(u, "EnumWindows", wt.BOOL, self.WNDENUMPROC, wt.LPARAM)
        self._GetWindowThreadProcessId = proto(u, "GetWindowThreadProcessId", wt.DWORD, wt.HWND,
                                               ctypes.POINTER(wt.DWORD))
        self._GetClassNameW = proto(u, "GetClassNameW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
        self._GetWindowTextW = proto(u, "GetWindowTextW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
        self._IsWindow = proto(u, "IsWindow", wt.BOOL, wt.HWND)
        self._IsWindowVisible = proto(u, "IsWindowVisible", wt.BOOL, wt.HWND)
        self._GetWindow = proto(u, "GetWindow", wt.HWND, wt.HWND, wt.UINT)
        self._GetWindowRect = proto(u, "GetWindowRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
        self._GetWindowLongPtrW = proto(u, "GetWindowLongPtrW", LONG_PTR, wt.HWND, ctypes.c_int)
        self._SetWindowLongPtrW = proto(u, "SetWindowLongPtrW", LONG_PTR, wt.HWND, ctypes.c_int, LONG_PTR)
        self._SetWindowPos = proto(u, "SetWindowPos", wt.BOOL, wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.UINT)
        self._PostMessageW = proto(u, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        self._MonitorFromWindow = proto(u, "MonitorFromWindow", wt.HMONITOR, wt.HWND, wt.DWORD)
        self._MonitorFromPoint = proto(u, "MonitorFromPoint", wt.HMONITOR, wt.POINT, wt.DWORD)
        self._GetMonitorInfoW = proto(u, "GetMonitorInfoW", wt.BOOL, wt.HMONITOR, ctypes.POINTER(MONITORINFO))
        try:
            self._GetDpiForWindow = proto(u, "GetDpiForWindow", wt.UINT, wt.HWND)
        except AttributeError:  # pragma: no cover - before Windows 10 1607
            self._GetDpiForWindow = None
        self._CreateToolhelp32Snapshot = proto(k, "CreateToolhelp32Snapshot", wt.HANDLE, wt.DWORD, wt.DWORD)
        self._Process32FirstW = proto(k, "Process32FirstW", wt.BOOL, wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        self._Process32NextW = proto(k, "Process32NextW", wt.BOOL, wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        self._CloseHandle = proto(k, "CloseHandle", wt.BOOL, wt.HANDLE)

    # -- windows
    def top_windows(self) -> list[tuple[int, int, str, str, bool, bool]]:
        """(hwnd, pid, class, title, visible, owned) of every top-level window."""
        out: list[tuple[int, int, str, str, bool, bool]] = []
        ctypes, wt = self.ctypes, self.wt

        def cb(hwnd, _lparam):
            try:
                pid = wt.DWORD(0)
                self._GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                cls = ctypes.create_unicode_buffer(256)
                self._GetClassNameW(hwnd, cls, 256)
                title = ctypes.create_unicode_buffer(512)
                self._GetWindowTextW(hwnd, title, 512)
                out.append((int(hwnd), int(pid.value), cls.value, title.value, bool(self._IsWindowVisible(hwnd)),
                            bool(self._GetWindow(hwnd, 4))))  # GW_OWNER
            except Exception:  # noqa: BLE001 - never raise inside the Win32 callback
                pass
            return True

        self._EnumWindows(self.WNDENUMPROC(cb), 0)
        return out

    def parents(self) -> dict[int, int]:
        """pid -> parent pid for every process (Toolhelp snapshot)."""
        ctypes = self.ctypes
        snap = self._CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == ctypes.c_void_p(-1).value:
            return {}
        out: dict[int, int] = {}
        try:
            entry = self.PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = self._Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                out[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = self._Process32NextW(snap, ctypes.byref(entry))
        finally:
            self._CloseHandle(snap)
        return out

    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd) and bool(self._IsWindow(hwnd))

    def window_size(self, hwnd: int) -> tuple[int, int]:
        r = self.wt.RECT()
        if not self._GetWindowRect(hwnd, self.ctypes.byref(r)):
            return (0, 0)
        return (r.right - r.left, r.bottom - r.top)

    def get_exstyle(self, hwnd: int) -> int:
        return int(self._GetWindowLongPtrW(hwnd, GWL_EXSTYLE))

    def set_exstyle(self, hwnd: int, style: int) -> None:
        self._SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)

    def set_pos(self, hwnd: int, after: int, x: int, y: int, cx: int, cy: int, flags: int) -> bool:
        return bool(self._SetWindowPos(hwnd, after, x, y, cx, cy, flags))

    def close(self, hwnd: int) -> None:
        self._PostMessageW(hwnd, WM_CLOSE, 0, 0)

    # -- monitors
    def monitor_rect(self, hwnd: Optional[int]) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) of the monitor holding ``hwnd`` (the primary one without)."""
        wt, ctypes = self.wt, self.ctypes
        mon = self._MonitorFromWindow(hwnd, MONITOR_DEFAULTTOPRIMARY) if hwnd else \
            self._MonitorFromPoint(wt.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
        info = self.MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not mon or not self._GetMonitorInfoW(mon, ctypes.byref(info)):
            return (0, 0, 1920, 1080)
        r = info.rcMonitor
        return (r.left, r.top, r.right, r.bottom)

    def dpi_scale(self, hwnd: int) -> float:
        if self._GetDpiForWindow is None:
            return 1.0
        dpi = int(self._GetDpiForWindow(hwnd) or 96)
        return max(1.0, dpi / 96.0)


def _game_window() -> Optional[int]:
    try:
        from ..transport import wincapture
        return wincapture.find_game_window(strict=True)
    except Exception:  # noqa: BLE001 - placement only
        return None


class PlayerWindow:
    """The long-lived, never-activating player window (see the module docstring)."""

    def __init__(self, win32: Any, *, corner: str = DEFAULT_CORNER, game_window: Callable[[], Optional[int]] = _game_window,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.win32 = win32
        self.corner = normalize_corner(corner)
        self.position: Optional[tuple[int, int]] = None   # deck.playerPosition overrides the corner
        self.game_window = game_window
        self.clock, self.sleep = clock, sleep
        self.hwnd: Optional[int] = None
        self.shown = False
        self._lock = threading.Lock()
        self._hide_gen = 0

    # -- finding it
    def _candidates(self, pids: set[int]) -> list[tuple[int, int]]:
        found = []
        for hwnd, pid, cls, title, visible, owned in self.win32.top_windows():
            if cls != CHROME_CLASS or owned:
                continue
            ours = pid in pids
            named = title.strip().endswith(TITLE_SUFFIX)
            if ours and (visible or named) or (named and visible):
                score = (2 if ours else 0) + (1 if named else 0)
                found.append((score, hwnd))
        found.sort(reverse=True)
        return found

    def _tree(self, root: int) -> set[int]:
        try:
            parents = self.win32.parents()
        except Exception:  # noqa: BLE001
            parents = {}
        tree = {root}
        grew = True
        while grew:
            grew = False
            for pid, ppid in parents.items():
                if ppid in tree and pid not in tree:
                    tree.add(pid)
                    grew = True
        return tree

    def adopt(self, pid: Optional[int], timeout: float = ADOPT_TIMEOUT_S) -> bool:
        """Wait for the launched browser's window, restyle it and hide it. A browser that handed the
        window to an instance already running (same profile) is found by the page's title."""
        deadline = self.clock() + max(0.0, timeout)
        while True:
            pids = self._tree(int(pid)) if pid else set()
            for _score, hwnd in self._candidates(pids):
                self._take(hwnd)
                return True
            if self.clock() >= deadline:
                log.warning("the player window did not appear within %.0f s; it stays as the browser made it", timeout)
                return False
            self.sleep(0.1)

    def _take(self, hwnd: int) -> None:
        w = self.win32
        style = w.get_exstyle(hwnd)
        new = (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        if new != style:
            w.set_exstyle(hwnd, new)
        with self._lock:
            self.hwnd = hwnd
            self.shown = True
        self.hide()
        log.info("player window ready (hidden until a YouTube song plays)")

    def alive(self) -> bool:
        hwnd = self.hwnd
        if hwnd and not self.win32.is_window(hwnd):
            with self._lock:
                self.hwnd, self.shown = None, False
        return bool(self.hwnd)

    # -- showing it
    def target(self) -> Optional[tuple[int, int]]:
        hwnd = self.hwnd
        if not hwnd:
            return None
        if self.position is not None:
            return self.position
        mon = self.win32.monitor_rect(self.game_window())
        size = self.win32.window_size(hwnd)
        margin = int(round(MARGIN_PX * self.win32.dpi_scale(hwnd)))
        return corner_position(self.corner, mon, size, margin)

    def show(self) -> bool:
        """Always on top at the chosen corner, without ever activating it."""
        if not self.alive():
            return False
        with self._lock:
            self._hide_gen += 1
        pos = self.target()
        if pos is None:
            return False
        ok = self.win32.set_pos(self.hwnd, HWND_TOPMOST, pos[0], pos[1], 0, 0,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOSIZE | SWP_NOOWNERZORDER)
        with self._lock:
            self.shown = bool(ok) or self.shown
        return bool(ok)

    def hide(self) -> bool:
        if not self.alive():
            return False
        ok = self.win32.set_pos(self.hwnd, 0, 0, 0, 0, 0,
                                SWP_HIDEWINDOW | SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
        with self._lock:
            if ok:
                self.shown = False
        return bool(ok)

    def hide_later(self, delay_s: float) -> None:
        """Hide after ``delay_s`` (the page's fade-out), unless a song starts meanwhile."""
        with self._lock:
            self._hide_gen += 1
            gen = self._hide_gen

        def run() -> None:
            self.sleep(max(0.0, delay_s))
            with self._lock:
                stale = gen != self._hide_gen
            if not stale:
                self.hide()

        threading.Thread(target=run, name="needledropper-player-hide", daemon=True).start()

    def close(self) -> None:
        """Close the window (the app is quitting): a browser left running would hand the next
        app start's window to itself."""
        hwnd = self.hwnd
        if hwnd and self.win32.is_window(hwnd):
            try:
                self.win32.close(hwnd)
            except Exception as exc:  # noqa: BLE001
                log.debug("closing the player window failed: %s", exc)
        with self._lock:
            self.hwnd, self.shown = None, False


def make_player_window(corner: str = DEFAULT_CORNER) -> Optional[PlayerWindow]:
    """A :class:`PlayerWindow` on Windows (None elsewhere, or when ctypes is unusable)."""
    if sys.platform != "win32" or os.environ.get("NEEDLEDROPPER_NO_WINDOW_MANAGER"):
        return None
    try:
        from ..transport import wincapture
        wincapture.set_dpi_awareness()  # physical pixels for positions (a PyInstaller exe is DPI-unaware)
    except Exception:  # noqa: BLE001
        pass
    try:
        return PlayerWindow(Win32(), corner=corner)
    except Exception as exc:  # noqa: BLE001 - no ctypes / user32
        log.info("player window management unavailable: %s", exc)
        return None


__all__ = ["CORNERS", "DEFAULT_CORNER", "PlayerWindow", "Win32", "corner_position", "make_player_window",
           "normalize_corner"]
