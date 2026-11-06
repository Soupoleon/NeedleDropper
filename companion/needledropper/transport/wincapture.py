"""Win32 screen/window capture for the pixel beacon (ctypes only, no pywin32).

Everything here is read-only toward the game: we enumerate top-level windows,
read the client-area geometry and copy pixels with GDI. Nothing is injected.

Functions (all raise :class:`RuntimeError` on non-Windows hosts or on Win32
failure, except the predicates noted below):

* :func:`set_dpi_awareness` - make this process per-monitor DPI aware (once).
  Must run before any window geometry / GDI call, otherwise Windows virtualises
  coordinates and scales captured pixels on a high-DPI display (4K TV at 150%+).
* :func:`find_game_window` - locate the WoW: Forever window (exe path first,
  title second).
* :func:`client_rect` / :func:`client_origin` - client area in physical
  screen pixels.
* :func:`is_window` / :func:`is_minimized` - predicates (never raise; False on
  non-Windows).
* :func:`capture_screen_region` - BitBlt from the desktop DC (what is visible
  on screen, composited by DWM).
* :func:`capture_window_region` - PrintWindow(PW_RENDERFULLCONTENT) of the game
  window, for when something covers the strip on screen. :class:`WindowCapturer`
  does the same but keeps its GDI bitmaps between frames (re-created only when
  the client size changes); the pixel transport uses one per capture thread.

Captured frames are returned as ``(bgra_bytes, width, height, stride)``:
top-down rows, 4 bytes per pixel in **B, G, R, A** order, ``stride == width*4``.

Window search skips *cloaked* windows (DWM ``DWMWA_CLOAKED``: e.g. windows on
another virtual desktop or suspended UWP frames), which report as visible but
are never drawn.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

IS_WINDOWS = sys.platform == "win32"

Frame = tuple[bytes, int, int, int]  # (bgra, width, height, stride)

# --------------------------------------------------------------- constants --
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GW_OWNER = 4
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
BI_RGB = 0
DIB_RGB_COLORS = 0
E_ACCESSDENIED = -2147024891  # 0x80070005 as a signed HRESULT
ERROR_ACCESS_DENIED = 5
DWMWA_CLOAKED = 14

MAX_CAPTURE_DIM = 16384  # sanity bound for requested regions

GAME_WINDOW_CLASS = "GxWindowClass"  # WoW's top-level window class (all flavors)

_DwmGetWindowAttribute = None  # set below when dwmapi is available

if IS_WINDOWS:
    from ctypes import wintypes as wt

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        _shcore = ctypes.WinDLL("shcore", use_last_error=True)
    except OSError:  # pragma: no cover - Windows 7 and older
        _shcore = None

    class RECT(ctypes.Structure):
        _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wt.LONG), ("y", wt.LONG)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _proto(dll, name, restype, *argtypes):
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = list(argtypes)
        return fn

    # user32 ------------------------------------------------------------
    _EnumWindows = _proto(_user32, "EnumWindows", wt.BOOL, WNDENUMPROC, wt.LPARAM)
    _GetWindowThreadProcessId = _proto(_user32, "GetWindowThreadProcessId", wt.DWORD,
                                       wt.HWND, ctypes.POINTER(wt.DWORD))
    _IsWindowVisible = _proto(_user32, "IsWindowVisible", wt.BOOL, wt.HWND)
    _IsWindow = _proto(_user32, "IsWindow", wt.BOOL, wt.HWND)
    _IsIconic = _proto(_user32, "IsIconic", wt.BOOL, wt.HWND)
    _GetWindow = _proto(_user32, "GetWindow", wt.HWND, wt.HWND, wt.UINT)
    _GetWindowTextLengthW = _proto(_user32, "GetWindowTextLengthW", ctypes.c_int, wt.HWND)
    _GetWindowTextW = _proto(_user32, "GetWindowTextW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
    _GetClassNameW = _proto(_user32, "GetClassNameW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
    _GetClientRect = _proto(_user32, "GetClientRect", wt.BOOL, wt.HWND, ctypes.POINTER(RECT))
    _ClientToScreen = _proto(_user32, "ClientToScreen", wt.BOOL, wt.HWND, ctypes.POINTER(POINT))
    _GetDC = _proto(_user32, "GetDC", wt.HDC, wt.HWND)
    _ReleaseDC = _proto(_user32, "ReleaseDC", ctypes.c_int, wt.HWND, wt.HDC)
    _PrintWindow = _proto(_user32, "PrintWindow", wt.BOOL, wt.HWND, wt.HDC, wt.UINT)
    _GetForegroundWindow = _proto(_user32, "GetForegroundWindow", wt.HWND)
    _SetProcessDPIAware = _proto(_user32, "SetProcessDPIAware", wt.BOOL)
    try:
        _SetProcessDpiAwarenessContext = _proto(_user32, "SetProcessDpiAwarenessContext", wt.BOOL,
                                                ctypes.c_void_p)
    except AttributeError:  # pragma: no cover - before Windows 10 1703
        _SetProcessDpiAwarenessContext = None
    _SetProcessDpiAwareness = (_proto(_shcore, "SetProcessDpiAwareness", ctypes.c_long, ctypes.c_int)
                               if _shcore is not None else None)

    # kernel32 ----------------------------------------------------------
    _OpenProcess = _proto(_kernel32, "OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
    _CloseHandle = _proto(_kernel32, "CloseHandle", wt.BOOL, wt.HANDLE)
    _QueryFullProcessImageNameW = _proto(_kernel32, "QueryFullProcessImageNameW", wt.BOOL,
                                         wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))

    # gdi32 -------------------------------------------------------------
    _CreateCompatibleDC = _proto(_gdi32, "CreateCompatibleDC", wt.HDC, wt.HDC)
    _CreateCompatibleBitmap = _proto(_gdi32, "CreateCompatibleBitmap", wt.HBITMAP, wt.HDC,
                                     ctypes.c_int, ctypes.c_int)
    _SelectObject = _proto(_gdi32, "SelectObject", wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
    _BitBlt = _proto(_gdi32, "BitBlt", wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                     ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD)
    _GetDIBits = _proto(_gdi32, "GetDIBits", ctypes.c_int, wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                        ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wt.UINT)
    _DeleteObject = _proto(_gdi32, "DeleteObject", wt.BOOL, wt.HGDIOBJ)
    _DeleteDC = _proto(_gdi32, "DeleteDC", wt.BOOL, wt.HDC)

    # dwmapi (optional: missing on very old systems or stripped-down sessions) ---
    try:
        _dwmapi = ctypes.WinDLL("dwmapi")
        _DwmGetWindowAttribute = _proto(_dwmapi, "DwmGetWindowAttribute", ctypes.c_long, wt.HWND, wt.DWORD,
                                        ctypes.c_void_p, wt.DWORD)
    except (OSError, AttributeError):  # pragma: no cover - DWM is always there on Windows 8+
        _DwmGetWindowAttribute = None


def _require_windows(what: str) -> None:
    if not IS_WINDOWS:
        raise RuntimeError(f"{what} requires Windows (this is {sys.platform}); the pixel beacon "
                           "transport only works on the PC that runs World of Warcraft")


def _win_error(what: str) -> RuntimeError:
    code = ctypes.get_last_error() if IS_WINDOWS else 0
    msg = ctypes.FormatError(code).strip() if (IS_WINDOWS and code) else "unknown error"
    return RuntimeError(f"{what} failed (Win32 error {code}: {msg})")


# ----------------------------------------------------------- DPI awareness --
_dpi_lock = threading.Lock()
_dpi_result: Optional[str] = None


def set_dpi_awareness() -> str:
    """Make the process per-monitor DPI aware so geometry and pixels are physical.

    Tries, in order: ``SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)``
    (Windows 10 1703+), ``SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)``
    (8.1+), ``SetProcessDPIAware()`` (Vista+). Awareness can only be set once per
    process; "access denied" means it was already set (e.g. by a manifest or an
    earlier call) and is treated as success. Idempotent: later calls return the
    first result without touching Win32 again.

    Returns a short description of what happened (for diagnostics).
    """
    global _dpi_result
    _require_windows("set_dpi_awareness")
    with _dpi_lock:
        if _dpi_result is not None:
            return _dpi_result
        result = "unaware"
        if _SetProcessDpiAwarenessContext is not None:
            if _SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
                result = "per-monitor-v2"
            elif ctypes.get_last_error() == ERROR_ACCESS_DENIED:
                result = "already-set"
        if result == "unaware" and _SetProcessDpiAwareness is not None:
            hr = _SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
            if hr == 0:
                result = "per-monitor"
            elif hr == E_ACCESSDENIED:
                result = "already-set"
        if result == "unaware" and _SetProcessDPIAware():
            result = "system-aware"
        _dpi_result = result
        return result


# ------------------------------------------------------------ window search --
@dataclass(frozen=True)
class WindowInfo:
    """Description of a top-level window (for status / diagnostics)."""

    hwnd: int
    title: str
    class_name: str
    pid: int
    exe: Optional[str]  # full image path, None if the process could not be queried
    score: int = 0  # how well it matched the search (higher is better)


def _window_text(hwnd: int) -> str:
    n = _GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _GetClassNameW(hwnd, buf, 256)
    return buf.value


def _process_image(pid: int) -> Optional[str]:
    h = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wt.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        _CloseHandle(h)


def _norm(p: Union[str, os.PathLike]) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(p)))


def is_cloaked(hwnd: Optional[int]) -> bool:
    """True if DWM cloaks the window (visible flag set but never drawn: another virtual
    desktop, a suspended app frame). False when unknown or not on Windows."""
    if not IS_WINDOWS or not hwnd or _DwmGetWindowAttribute is None:
        return False
    cloaked = wt.DWORD(0)
    try:
        hr = _DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    except (OSError, ValueError, ctypes.ArgumentError):
        return False
    return hr == 0 and cloaked.value != 0


#: A window whose score reaches this matched the configured ``_forever_\\Wow.exe`` path itself (or the
#: same flavor folder + exe name under another install root): the strongest evidence there is.
EXE_MATCH_SCORE = 60


def _looks_like_game(cls: str, exe: Optional[str]) -> bool:
    """WoW's own top-level window class, or a process image named like the game (Wow*.exe)."""
    return cls == GAME_WINDOW_CLASS or bool(exe and Path(exe).name.lower().startswith("wow"))


def _score(info_title: str, cls: str, exe: Optional[str], game_exe: Optional[str], title: str, *,
           strict: bool = False) -> int:
    """How well a window matches the game (0 = not at all; higher is better).

    ``strict`` (what the pixel transport uses) never accepts a window on its title alone
    (PIX-WINDOW-LATCH, real-client finding #2): an Explorer window on the install folder is
    titled exactly "World of Warcraft", a browser tab can start with it, and the game's
    launcher shows a short-lived window of that name. A strict match needs the configured
    exe path (score >= :data:`EXE_MATCH_SCORE`) or the game's window class / a ``Wow*.exe``
    image -- and when the exe path is known, a ``Wow*.exe`` image from somewhere else counts
    only with the game's window class too.
    """
    score = 0
    if exe and game_exe:
        if _norm(exe) == game_exe:
            score += 100  # the exact _forever_ Wow.exe we were told about
        else:
            exe_p, want = Path(exe), Path(game_exe)
            if (exe_p.name.lower() == want.name.lower()
                    and exe_p.parent.name.lower() == want.parent.name.lower()):
                score += EXE_MATCH_SCORE  # same flavor dir + exe name, different install root
    if title:
        if info_title == title:
            score += 30
        elif info_title.lower().startswith(title.lower()):
            score += 10
    if score and cls == GAME_WINDOW_CLASS:
        score += 5
    if score and exe and Path(exe).name.lower().startswith("wow"):
        score += 2
    if strict and score and score < EXE_MATCH_SCORE:
        if not _looks_like_game(cls, exe):
            return 0  # a title-only match: an Explorer / browser / launcher window, never the game
        if game_exe and exe is not None and cls != GAME_WINDOW_CLASS:
            return 0  # the exe path is known and this is not it (nor the game's window class)
    return score


def list_game_windows(game_exe: Optional[Union[str, os.PathLike]] = None,
                      title: str = "World of Warcraft", *, strict: bool = False) -> list[WindowInfo]:
    """All visible, uncloaked, unowned top-level windows matching the exe path or the
    title, best match first. ``strict`` drops title-only matches (see :func:`_score`)."""
    _require_windows("list_game_windows")
    want = _norm(game_exe) if game_exe else None
    found: list[WindowInfo] = []

    def _cb(hwnd, _lparam):
        try:
            if not hwnd or not _IsWindowVisible(hwnd) or _GetWindow(hwnd, GW_OWNER):
                return True
            if is_cloaked(hwnd):
                return True
            text = _window_text(hwnd)
            pid = wt.DWORD(0)
            _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            # Only query the process image for windows that could plausibly be the game
            # (titled like it, or any window when we know the exact exe path).
            exe = _process_image(pid.value) if (want or (title and title.lower() in text.lower())) else None
            cls = _class_name(hwnd)
            s = _score(text, cls, exe, want, title, strict=strict)
            if s > 0:
                found.append(WindowInfo(int(hwnd), text, cls, int(pid.value), exe, s))
        except Exception:  # never let an exception escape into the Win32 callback
            pass
        return True

    cb = WNDENUMPROC(_cb)
    _EnumWindows(cb, 0)
    found.sort(key=lambda w: (-w.score, -_area(w.hwnd)))
    return found


def _area(hwnd: int) -> int:
    try:
        _x, _y, w, h = client_rect(hwnd)
        return w * h
    except RuntimeError:
        return 0


def find_game_window(game_exe: Optional[Union[str, os.PathLike]] = None,
                     title: str = "World of Warcraft", *, strict: bool = False) -> Optional[int]:
    """Return the HWND of the WoW: Forever window, or None.

    Preference: the window whose process image path equals ``game_exe`` (the
    ``_forever_\\Wow.exe``), then a ``Wow.exe`` in a folder of the same name
    under another install root, then a window titled exactly ``title``. Only
    visible, unowned top-level windows are considered; ``strict`` never accepts a
    title-only match (see :func:`_score`).
    """
    wins = list_game_windows(game_exe, title, strict=strict)
    return wins[0].hwnd if wins else None


def window_matches(hwnd: int, game_exe: Optional[Union[str, os.PathLike]] = None,
                   title: str = "World of Warcraft") -> int:
    """The strict score of one window right now (0: gone, hidden, cloaked, or no longer the game --
    e.g. its handle was reused by another program). Never raises."""
    if not IS_WINDOWS or not hwnd:
        return 0
    try:
        if not _IsWindow(hwnd) or not _IsWindowVisible(hwnd) or is_cloaked(hwnd):
            return 0
        pid = wt.DWORD(0)
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        want = _norm(game_exe) if game_exe else None
        return _score(_window_text(hwnd), _class_name(hwnd), _process_image(pid.value), want, title, strict=True)
    except Exception:  # noqa: BLE001 - a predicate
        return 0


def foreground_window() -> Optional[int]:
    """The foreground window's handle (None when there is none or not on Windows). Never raises."""
    if not IS_WINDOWS:
        return None
    try:
        hwnd = _GetForegroundWindow()
    except Exception:  # noqa: BLE001 - a probe
        return None
    return int(hwnd) if hwnd else None


def game_in_foreground(game_exe: Optional[Union[str, os.PathLike]] = None,
                       title: str = "World of Warcraft") -> bool:
    """True when the foreground window is the game's (the strict match of :func:`window_matches`:
    its exe path, or the game's window class / a ``Wow*.exe`` image). Used to keep anything that
    could take the focus -- launching the Spotify app -- away while the player is in the game."""
    hwnd = foreground_window()
    return bool(hwnd) and window_matches(hwnd, game_exe, title) > 0


def describe_window(hwnd: int) -> Optional[WindowInfo]:
    """Title / class / pid / exe for a window handle (None if it no longer exists)."""
    _require_windows("describe_window")
    if not hwnd or not _IsWindow(hwnd):
        return None
    pid = wt.DWORD(0)
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return WindowInfo(int(hwnd), _window_text(hwnd), _class_name(hwnd), int(pid.value),
                      _process_image(pid.value))


# --------------------------------------------------------- window geometry --
def is_window(hwnd: Optional[int]) -> bool:
    """True if ``hwnd`` still names an existing window (False on non-Windows)."""
    if not IS_WINDOWS or not hwnd:
        return False
    return bool(_IsWindow(hwnd))


def is_minimized(hwnd: Optional[int]) -> bool:
    """True if the window is minimized (iconic). False on non-Windows/invalid handle."""
    if not IS_WINDOWS or not hwnd:
        return False
    return bool(_IsIconic(hwnd))


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Client area as ``(screen_x, screen_y, width, height)`` in physical pixels
    (assuming :func:`set_dpi_awareness` ran)."""
    _require_windows("client_rect")
    rc = RECT()
    if not hwnd or not _GetClientRect(hwnd, ctypes.byref(rc)):
        raise _win_error("GetClientRect")
    pt = POINT(0, 0)
    if not _ClientToScreen(hwnd, ctypes.byref(pt)):
        raise _win_error("ClientToScreen")
    return pt.x, pt.y, rc.right - rc.left, rc.bottom - rc.top


def client_origin(hwnd: int) -> tuple[int, int]:
    """Screen coordinates of the top-left pixel of the window's client area."""
    x, y, _w, _h = client_rect(hwnd)
    return x, y


# ----------------------------------------------------------------- capture --
def _check_dims(w: int, h: int) -> None:
    if not (0 < w <= MAX_CAPTURE_DIM and 0 < h <= MAX_CAPTURE_DIM):
        raise RuntimeError(f"invalid capture size {w}x{h}")


def _read_bitmap(memdc: int, hbmp: int, w: int, h: int) -> bytes:
    """GetDIBits of a bitmap that is NOT currently selected into a DC -> top-down BGRA."""
    bmi = BITMAPINFO()
    hdr = bmi.bmiHeader
    hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    hdr.biWidth = w
    hdr.biHeight = -h  # negative = top-down rows
    hdr.biPlanes = 1
    hdr.biBitCount = 32
    hdr.biCompression = BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    lines = _GetDIBits(memdc, hbmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
    if lines != h:
        raise _win_error(f"GetDIBits ({lines}/{h} rows)")
    return buf.raw


def capture_screen_region(x: int, y: int, w: int, h: int) -> Frame:
    """Copy a ``w``x``h`` block of the composited desktop at screen ``(x, y)``.

    Uses GetDC(NULL) + BitBlt(SRCCOPY|CAPTUREBLT) + GetDIBits (32 bpp,
    top-down). Returns ``(bgra_bytes, w, h, stride)``. Every GDI object is
    released, even on failure.
    """
    _require_windows("capture_screen_region")
    _check_dims(w, h)
    screen = memdc = hbmp = old = None
    try:
        screen = _GetDC(None)
        if not screen:
            raise _win_error("GetDC(desktop)")
        memdc = _CreateCompatibleDC(screen)
        if not memdc:
            raise _win_error("CreateCompatibleDC")
        hbmp = _CreateCompatibleBitmap(screen, w, h)
        if not hbmp:
            raise _win_error("CreateCompatibleBitmap")
        old = _SelectObject(memdc, hbmp)
        if not _BitBlt(memdc, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT):
            raise _win_error("BitBlt")
        _SelectObject(memdc, old)  # GetDIBits wants the bitmap deselected
        old = None
        data = _read_bitmap(memdc, hbmp, w, h)
        return data, w, h, w * 4
    finally:
        if old and memdc:
            _SelectObject(memdc, old)
        if hbmp:
            _DeleteObject(hbmp)
        if memdc:
            _DeleteDC(memdc)
        if screen:
            _ReleaseDC(None, screen)


class WindowCapturer:
    """PrintWindow capture that keeps its GDI objects between frames.

    ``PrintWindow(PW_CLIENTONLY|PW_RENDERFULLCONTENT)`` draws the whole client
    area into a client-sized memory bitmap (DirectX content included where DWM
    can provide it); the top-left corner is then cropped with BitBlt into a
    small bitmap and read back. Allocating a full-screen bitmap for every frame
    (a 4K client is ~33 MB) at several frames per second is wasteful, so both
    bitmaps are kept and only re-created when the client (or crop) size
    changes. Calls are serialised by a lock; :meth:`close` frees everything.
    Construction touches no Win32 API.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._full: Optional[tuple[int, int, int, int, int]] = None  # (dc, bitmap, old bitmap, w, h)
        self._crop: Optional[tuple[int, int, int, int]] = None  # (dc, bitmap, w, h)
        self.allocations = 0  # bitmaps created so far (diagnostics/tests)

    @property
    def full_size(self) -> Optional[tuple[int, int]]:
        """Size of the cached client-area bitmap, if any."""
        full = self._full
        return (full[3], full[4]) if full else None

    def _ensure_full(self, cw: int, ch: int) -> int:
        full = self._full
        if full is not None and (full[3], full[4]) == (cw, ch):
            return full[0]
        self._free_full()
        screen = _GetDC(None)
        if not screen:
            raise _win_error("GetDC(desktop)")
        dc = bmp = None
        try:
            dc = _CreateCompatibleDC(screen)
            if not dc:
                raise _win_error("CreateCompatibleDC")
            # Compatible with the *screen* DC (a memory DC starts with a 1x1 mono bitmap).
            bmp = _CreateCompatibleBitmap(screen, cw, ch)
            if not bmp:
                raise _win_error("CreateCompatibleBitmap")
            old = _SelectObject(dc, bmp)
            self._full = (dc, bmp, old, cw, ch)
            self.allocations += 1
            dc = bmp = None
        finally:
            if bmp:
                _DeleteObject(bmp)
            if dc:
                _DeleteDC(dc)
            _ReleaseDC(None, screen)
        return self._full[0]

    def _ensure_crop(self, w: int, h: int) -> tuple[int, int]:
        crop = self._crop
        if crop is not None and (crop[2], crop[3]) == (w, h):
            return crop[0], crop[1]
        self._free_crop()
        screen = _GetDC(None)
        if not screen:
            raise _win_error("GetDC(desktop)")
        dc = bmp = None
        try:
            dc = _CreateCompatibleDC(screen)
            if not dc:
                raise _win_error("CreateCompatibleDC")
            bmp = _CreateCompatibleBitmap(screen, w, h)
            if not bmp:
                raise _win_error("CreateCompatibleBitmap")
            self._crop = (dc, bmp, w, h)
            self.allocations += 1
            dc = bmp = None
        finally:
            if bmp:
                _DeleteObject(bmp)
            if dc:
                _DeleteDC(dc)
            _ReleaseDC(None, screen)
        return self._crop[0], self._crop[1]

    def _free_full(self) -> None:
        full, self._full = self._full, None
        if full is not None:
            dc, bmp, old, _w, _h = full
            if old:
                _SelectObject(dc, old)
            _DeleteObject(bmp)
            _DeleteDC(dc)

    def _free_crop(self) -> None:
        crop, self._crop = self._crop, None
        if crop is not None:
            _DeleteObject(crop[1])
            _DeleteDC(crop[0])

    def capture(self, hwnd: int, w: int, h: int) -> Frame:
        """The client area's top-left ``w``x``h`` (clamped to the client size).
        Raises RuntimeError on failure."""
        _require_windows("capture_window_region")
        _check_dims(w, h)
        _cx, _cy, cw, ch = client_rect(hwnd)
        if cw <= 0 or ch <= 0:
            raise RuntimeError("window has an empty client area (minimized?)")
        w, h = min(w, cw), min(h, ch)
        with self._lock:
            fulldc = self._ensure_full(cw, ch)
            if not _PrintWindow(hwnd, fulldc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
                raise _win_error("PrintWindow")
            cropdc, cropbmp = self._ensure_crop(w, h)
            old = _SelectObject(cropdc, cropbmp)
            try:
                if not _BitBlt(cropdc, 0, 0, w, h, fulldc, 0, 0, SRCCOPY):
                    raise _win_error("BitBlt(crop)")
            finally:
                _SelectObject(cropdc, old)  # GetDIBits wants the bitmap deselected
            data = _read_bitmap(cropdc, cropbmp, w, h)
        return data, w, h, w * 4

    def close(self) -> None:
        """Free the cached GDI objects (the capturer can be used again afterwards)."""
        if not IS_WINDOWS:
            return
        with self._lock:
            self._free_crop()
            self._free_full()

    def __enter__(self) -> "WindowCapturer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def capture_window_region(hwnd: int, w: int, h: int) -> Frame:
    """Render the window with PrintWindow and return its client area's top-left ``w``x``h``.

    Used when the strip is hidden on screen (e.g. another window covers the
    corner). One-shot: allocates and frees its bitmaps; capture loops should
    keep a :class:`WindowCapturer` instead. Raises RuntimeError on failure.
    """
    _require_windows("capture_window_region")
    with WindowCapturer() as cap:
        return cap.capture(hwnd, w, h)


__all__ = [
    "IS_WINDOWS", "Frame", "WindowInfo", "WindowCapturer", "set_dpi_awareness", "find_game_window",
    "list_game_windows", "describe_window", "is_window", "is_minimized", "is_cloaked", "client_rect",
    "client_origin", "capture_screen_region", "capture_window_region", "foreground_window", "game_in_foreground",
    "window_matches",
]
