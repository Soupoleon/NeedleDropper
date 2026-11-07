"""The YouTube player window's Win32 side: it never takes the focus from the game.

Real-client findings #8, #11 and #13 (docs/review/real-client-2026-11-05.md) and the measured recipe
in docs/research/no-focus-playback.md:

* Chromium activates a window once, when it creates it, and no launch flag prevents that (research
  3.1 / 3.2). So the player window is created **once**, while the game is not the foreground window
  (at the app's start, normally at Windows sign-in), and kept for the whole session.
* As soon as it appears it is found by the launched process tree (class ``Chrome_WidgetWin_1``,
  never by title alone: the page renames itself per drop), restyled ``WS_EX_NOACTIVATE |
  WS_EX_TOOLWINDOW`` minus ``WS_EX_APPWINDOW`` (no activation by clicks, not in the taskbar or
  Alt-Tab) and **parked**: shown, but off every monitor and not topmost (:meth:`PlayerWindow.park`).
  Not hidden: the in-app Spotify player (the Web Playback SDK in the same page) plays nothing in a
  hidden window -- measured on the owner's PC with the frozen 1.2.1 app (2026-11-06): hidden from
  218 ms after the launch, the SDK got ready and Spotify accepted the song on device NeedleDropper,
  but the position never moved; shown, it plays (the SDK prototype, research 15). This is D14's
  fallback. (An earlier "hidden works" measurement had adopted another app's visible player window.)
* While a YouTube song plays it is shown always on top at a corner of the monitor that holds the
  game (``deck.playerCorner``, top-right by default; YouTube's rules want the player visible),
  with ``SWP_NOACTIVATE | SWP_SHOWWINDOW``, which never activates; afterwards it is parked again.
  Nothing YouTube may *play* while it is parked (research 5.4: the page then reports itself
  visible): the ARM only cues the video (paused, silent), so a warm-up play must never be added.
* The one activation Chromium does when it creates the window is handed back (best effort) to the
  window that was in front before the launch, and :attr:`PlayerWindow.took_foreground` records
  whether the creation took the foreground at all.

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
#: Where the beacon is: the addon's advanced beaconCorner setting, which the app's transport.pixel.corner
#: must match ("TOPLEFT", ...). The box keeps clear of the beacon at that corner (DESIGN-v1.2.1 3.9).
BEACON_CORNERS = ("topleft", "topright", "bottomleft", "bottomright")
DEFAULT_BEACON_CORNER = "topleft"
CHROME_CLASS = "Chrome_WidgetWin_1"
TITLE_SUFFIX = "NeedleDropper"     # player.html's <title>, and every title player.js gives itself
MARGIN_PX = 16                     # from the monitor's edges, at 96 dpi (scaled with the monitor)
#: The beacon's capture rectangle at the game's top-left corner, in physical pixels (transport/beacon2
#: CAPTURE_WIDTH x CAPTURE_HEIGHT; the addon draws its strip in physical pixels at any display scale).
BEACON_RECT_PX = (512, 48)
ADOPT_TIMEOUT_S = 15.0
HANDBACK_WATCH_S = 1.5             # after the window is hidden, watch this long for Chromium's activation

# Win32 constants (winuser.h)
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
PARK_GAP_PX = 200                  # how far beyond the virtual screen's top-left corner it is parked
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


def normalize_beacon_corner(value: Any) -> str:
    """``transport.pixel.corner`` ("TOPLEFT", "BOTTOMRIGHT", ...) as a corner name; top left by default."""
    v = str(value or "").strip().lower().replace("-", "").replace(" ", "").replace("_", "")
    return v if v in BEACON_CORNERS else DEFAULT_BEACON_CORNER


def corner_position(corner: str, monitor: tuple[int, int, int, int], size: tuple[int, int],
                    margin: int = MARGIN_PX, beacon: Optional[tuple[int, int]] = None,
                    beacon_corner: str = DEFAULT_BEACON_CORNER) -> tuple[int, int]:
    """Top-left (x, y) of a window of ``size`` at ``corner`` of ``monitor`` = (left, top, right,
    bottom), ``margin`` pixels from its edges (pure; physical pixels).

    ``beacon`` (width, height): the beacon's capture rectangle at the area's ``beacon_corner``. A box that
    would overlap it moves ``margin`` past it, away from the area's edge (DESIGN-v1.2.1 3.9): below a beacon
    at the top, above one at the bottom. For the default top-left beacon that is always a top-left box at
    100 %, and a top-right one when the area is narrower than the box plus the rectangle."""
    left, top, right, bottom = monitor
    w, h = size
    corner = normalize_corner(corner)
    x = right - margin - w if corner.endswith("right") else left + margin
    y = bottom - margin - h if corner.startswith("bottom") else top + margin
    if beacon is not None:
        bw, bh = beacon
        at = normalize_beacon_corner(beacon_corner)
        bx = left if at.endswith("left") else right - bw
        by = top if at.startswith("top") else bottom - bh
        if x < bx + bw and x + w > bx and y < by + bh and y + h > by:
            y = by + bh + margin if at.startswith("top") else by - margin - h
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
        self._GetForegroundWindow = proto(u, "GetForegroundWindow", wt.HWND)
        self._SetForegroundWindow = proto(u, "SetForegroundWindow", wt.BOOL, wt.HWND)
        self._MonitorFromWindow = proto(u, "MonitorFromWindow", wt.HMONITOR, wt.HWND, wt.DWORD)
        self._MonitorFromPoint = proto(u, "MonitorFromPoint", wt.HMONITOR, wt.POINT, wt.DWORD)
        self._GetMonitorInfoW = proto(u, "GetMonitorInfoW", wt.BOOL, wt.HMONITOR, ctypes.POINTER(MONITORINFO))
        self._GetSystemMetrics = proto(u, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
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

    def window_rect(self, hwnd: int) -> Optional[tuple[int, int, int, int]]:
        """(left, top, right, bottom) of ``hwnd`` on the screen, or None."""
        r = self.wt.RECT()
        if not self._GetWindowRect(hwnd, self.ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right, r.bottom)

    def get_exstyle(self, hwnd: int) -> int:
        return int(self._GetWindowLongPtrW(hwnd, GWL_EXSTYLE))

    def set_exstyle(self, hwnd: int, style: int) -> None:
        self._SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)

    def set_pos(self, hwnd: int, after: int, x: int, y: int, cx: int, cy: int, flags: int) -> bool:
        return bool(self._SetWindowPos(hwnd, after, x, y, cx, cy, flags))

    def close(self, hwnd: int) -> None:
        self._PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def foreground(self) -> Optional[int]:
        h = self._GetForegroundWindow()
        return int(h) if h else None

    def set_foreground(self, hwnd: int) -> bool:
        """Best effort: Windows refuses it unless this process may set the foreground."""
        return bool(self._SetForegroundWindow(hwnd))

    def window_pid(self, hwnd: int) -> int:
        pid = self.wt.DWORD(0)
        self._GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return int(pid.value)

    def client_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) of ``hwnd``'s client area on the screen (physical pixels)."""
        from ..transport import wincapture

        x, y, w, h = wincapture.client_rect(hwnd)
        return (x, y, x + w, y + h)

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

    def virtual_screen(self) -> tuple[int, int, int, int]:
        """(left, top, width, height) of the rectangle that holds every monitor."""
        g = self._GetSystemMetrics
        return (int(g(SM_XVIRTUALSCREEN)), int(g(SM_YVIRTUALSCREEN)), int(g(SM_CXVIRTUALSCREEN)),
                int(g(SM_CYVIRTUALSCREEN)))

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
        self.beacon_corner = DEFAULT_BEACON_CORNER          # the box keeps clear of the beacon there
        self.game_window = game_window
        self.clock, self.sleep = clock, sleep
        self.hwnd: Optional[int] = None
        self.shown = False
        self._lock = threading.Lock()
        #: serialises placing the window (show / park / reposition / the hide timer / keep_parked), so a
        #: park decided a moment ago never lands on a box a YouTube song just showed
        self._place_lock = threading.RLock()
        self._parked_at: Optional[tuple[int, int]] = None  # where park() last put it (None: never parked)
        self._hide_gen = 0
        #: whether creating the window ever took the foreground (None: not watched), and whether the
        #: foreground went back to the window that had it before the launch
        self.took_foreground: Optional[bool] = None
        self.handed_back: Optional[bool] = None
        self.creations = 0

    # -- finding it
    def windows_now(self) -> set[int]:
        """The browser windows that exist now (taken before a launch: none of them is the new player window)."""
        try:
            return {hwnd for hwnd, _pid, cls, _t, _v, _o in self.win32.top_windows() if cls == CHROME_CLASS}
        except Exception:  # noqa: BLE001
            return set()

    def _candidates(self, pids: set[int], ignore: Optional[set[int]] = None,
                    accept_handoff: Optional[Callable[[int], bool]] = None) -> list[tuple[int, int]]:
        found = []
        for hwnd, pid, cls, title, visible, owned in self.win32.top_windows():
            if cls != CHROME_CLASS or owned:
                continue
            if ignore and hwnd in ignore:
                # A window that was there before the launch is never the new player window -- e.g. another
                # NeedleDropper's visible player (1.2.0 left one running), which must not be hidden or closed.
                continue
            ours = pid in pids
            named = title.strip().endswith(TITLE_SUFFIX)
            if not ours and named and visible and accept_handoff is not None and not accept_handoff(pid):
                continue
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

    def foreground(self) -> Optional[int]:
        """The foreground window now (None when unknown)."""
        fn = getattr(self.win32, "foreground", None)
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None

    def _ours(self, hwnd: Optional[int], pids: set[int]) -> bool:
        if not hwnd:
            return False
        if hwnd == self.hwnd:
            return True
        fn = getattr(self.win32, "window_pid", None)
        try:
            return callable(fn) and fn(hwnd) in pids
        except Exception:  # noqa: BLE001
            return False

    def adopt(self, pid: Optional[int], timeout: float = ADOPT_TIMEOUT_S, *,
              prev_foreground: Optional[int] = None, ignore: Optional[set[int]] = None,
              accept_handoff: Optional[Callable[[int], bool]] = None) -> bool:
        """Wait for the launched browser's window, restyle it and hide it. A browser that handed the
        window to an instance already running (same profile) is found by the page's title.

        With ``prev_foreground`` (the foreground window before the launch), the foreground is watched
        meanwhile and for :data:`HANDBACK_WATCH_S` afterwards: when Chromium's creation activation took
        it, it is handed back to ``prev_foreground`` (best effort, ``SetForegroundWindow``), and
        :attr:`took_foreground` / :attr:`handed_back` record what happened."""
        deadline = self.clock() + max(0.0, timeout)
        watch = prev_foreground is not None and callable(getattr(self.win32, "foreground", None))
        took = False
        # The hand-back goes to the window that was in front when Chromium took the foreground: the player
        # may have clicked into the game while the browser started (the window in front before the launch
        # would be the wrong one then).
        back_to = prev_foreground
        with self._lock:
            self.creations += 1
        while True:
            pids = self._tree(int(pid)) if pid else set()
            if watch:
                fg = self.foreground()
                if self._ours(fg, pids):
                    took = True
                elif fg and not took:
                    back_to = fg
            for _score, hwnd in self._candidates(pids, ignore, accept_handoff):
                owner = next((p for h, p, *_rest in self.win32.top_windows() if h == hwnd), None)
                if owner is not None and owner not in pids:
                    log.warning("player launch handed off to browser pid %s", owner)
                    pids.add(owner)
                self._take(hwnd)
                if watch:
                    self._hand_back(back_to, pids, took)
                return True
            if self.clock() >= deadline:
                log.warning("the player window did not appear within %.0f s; it stays as the browser made it", timeout)
                return False
            self.sleep(0.05 if watch else 0.1)

    def _hand_back(self, prev: Optional[int], pids: set[int], took: bool) -> None:
        """After the hidden window exists: give the foreground back to ``prev`` if Chromium took it."""
        end = self.clock() + HANDBACK_WATCH_S
        handed: Optional[bool] = None
        while True:
            fg = self.foreground()
            if self._ours(fg, pids):
                took = True
                if prev and self.win32.is_window(prev):
                    try:
                        handed = bool(self.win32.set_foreground(prev))
                    except Exception:  # noqa: BLE001 - best effort
                        handed = False
            elif took and fg == prev:
                handed = True
            if self.clock() >= end:
                break
            self.sleep(0.05)
        with self._lock:
            self.took_foreground = took
            self.handed_back = handed if took else None
        if took:
            log.info("creating the player window took the foreground for a moment; %s",
                     "it went back to the window in front before" if handed else "Windows did not hand it back")
        else:
            log.info("creating the player window never took the foreground")

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
        log.info("player window ready (off screen until a YouTube song plays)")

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
        game = self.game_window()
        area, beacon = self.win32.monitor_rect(game), None
        if game:
            # The game's client area (DESIGN-v1.2.1 3.9), and the beacon at its top-left corner stays clear.
            fn = getattr(self.win32, "client_rect", None)
            try:
                rect = fn(game) if callable(fn) else None
            except Exception:  # noqa: BLE001 - the monitor then
                rect = None
            if rect is not None and rect[2] > rect[0] and rect[3] > rect[1]:
                area = tuple(int(v) for v in rect)
            beacon = BEACON_RECT_PX
        size = self.win32.window_size(hwnd)
        margin = int(round(MARGIN_PX * self.win32.dpi_scale(hwnd)))
        return corner_position(self.corner, area, size, margin, beacon, self.beacon_corner)

    def reposition(self) -> bool:
        """A corner (or position) change while the box is on screen moves it at once, never activating it
        (DESIGN-v1.2.1 3.9). False when it is not shown."""
        with self._place_lock:
            if not (self.shown and self.alive()):
                return False
            pos = self.target()
            if pos is None:
                return False
            return bool(self.win32.set_pos(self.hwnd, 0, pos[0], pos[1], 0, 0,
                                           SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER))

    def show(self) -> bool:
        """Always on top at the chosen corner, without ever activating it."""
        with self._place_lock:
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

    def parking_spot(self) -> tuple[int, int]:
        """A top-left position where the window touches no monitor: up and left of the virtual screen."""
        hwnd = self.hwnd
        w, h = self.win32.window_size(hwnd) if hwnd else (0, 0)
        fn = getattr(self.win32, "virtual_screen", None)
        try:
            left, top = (fn()[:2] if callable(fn) else self.win32.monitor_rect(None)[:2])
        except Exception:  # noqa: BLE001
            left, top = 0, 0
        return (int(left) - max(w, 1) - PARK_GAP_PX, int(top) - max(h, 1) - PARK_GAP_PX)

    def park(self) -> bool:
        """Between songs (D14): shown, but off every monitor and not topmost, never activated -- the Web
        Playback SDK plays nothing in a hidden window. No taskbar entry or Alt-Tab (WS_EX_TOOLWINDOW)."""
        with self._place_lock:
            if not self.alive():
                return False
            x, y = self.parking_spot()
            ok = self.win32.set_pos(self.hwnd, HWND_NOTOPMOST, x, y, 0, 0,
                                    SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOSIZE | SWP_NOOWNERZORDER)
            with self._lock:
                if ok:
                    self.shown = False
                    self._parked_at = (x, y)
            return bool(ok)

    def _on_a_monitor(self) -> bool:
        """The window overlaps the virtual screen (some monitor may show it); False when unknown."""
        rect_fn = getattr(self.win32, "window_rect", None)
        vs_fn = getattr(self.win32, "virtual_screen", None)
        if not (callable(rect_fn) and callable(vs_fn) and self.hwnd):
            return False
        try:
            rect = rect_fn(self.hwnd)
            left, top, width, height = vs_fn()
        except Exception:  # noqa: BLE001
            return False
        if not rect:
            return False
        return rect[0] < left + width and rect[2] > left and rect[1] < top + height and rect[3] > top

    def keep_parked(self) -> bool:
        """Between songs the window must stay off every monitor (D14). A display change while it is parked --
        a monitor added above or to the left of the others, a resolution or scaling change -- moves the
        virtual screen, and Windows may bring the window onto a monitor. A light check, every few seconds
        (the app's housekeeping): when the parking spot moved, or the window overlaps the virtual screen, it
        is parked again, never activated. Nothing while a YouTube song shows it. True when it parked it."""
        with self._place_lock:
            if self.shown or self._parked_at is None or not self.alive():
                return False
            if self.parking_spot() == self._parked_at and not self._on_a_monitor():
                return False
            log.info("the display changed while the player window was parked: parking it off screen again")
            return self.park()

    def hide(self) -> bool:
        """Off the screen between songs: :meth:`park` (never ``SWP_HIDEWINDOW``, see the module doc)."""
        return self.park()

    def hide_later(self, delay_s: float) -> None:
        """Hide after ``delay_s`` (the page's fade-out), unless a song starts meanwhile."""
        with self._lock:
            self._hide_gen += 1
            gen = self._hide_gen

        def run() -> None:
            self.sleep(max(0.0, delay_s))
            with self._place_lock:  # a show() in between makes this stale before it can park the box
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


__all__ = ["BEACON_CORNERS", "CORNERS", "DEFAULT_BEACON_CORNER", "DEFAULT_CORNER", "PlayerWindow", "Win32",
           "corner_position", "make_player_window", "normalize_beacon_corner", "normalize_corner"]
