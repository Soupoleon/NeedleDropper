"""Windows notification-area ("tray") icon for the companion: ctypes only, no pywin32.

The companion has no control panel of its own any more: players do everything in game,
so on the PC the companion is just this icon (plus the one-time connect screen and the
YouTube player window). This module provides:

* a **menu model** of plain dataclasses (:class:`MenuItem`, :data:`SEPARATOR`) that the CLI
  builds, :func:`companion_menu` for the standard menu (Open setup..., Pause needle drops,
  Start with Windows, Open log folder, Quit), and :class:`MenuLayout`, the platform-neutral
  snapshot + command dispatch that the Win32 code renders;
* :class:`TrayIcon`, which owns one notification-area icon: a left-click callback, a
  right-click popup menu with check items, a tooltip that can change (:meth:`TrayIcon.set_tooltip`),
  balloon / toast notifications (:meth:`TrayIcon.notify`), a swappable image
  (:meth:`TrayIcon.set_icon`, e.g. :data:`PAUSED_ICON_PATH` while paused) and a clean
  :meth:`TrayIcon.stop`.

Example (what the CLI does)::

    from needledropper import autostart, tray

    menu = tray.companion_menu(
        on_open_setup=open_setup_screen,
        on_pause=engine.set_paused, paused=lambda: engine.paused,
        on_autostart=autostart.set_enabled, autostart=autostart.is_enabled,
        on_open_log_folder=lambda: os.startfile(paths.app_dir),
        on_quit=quit_event.set,
    )
    icon = tray.TrayIcon("NeedleDropper", tray.ICON_PATH, menu, on_click=open_setup_screen,
                         on_close=quit_event.set)
    icon.start()
    icon.set_tooltip("NeedleDropper - waiting for World of Warcraft")
    icon.notify("NeedleDropper", "Connected to Spotify")
    quit_event.wait()
    icon.stop()

**Threads.** :meth:`TrayIcon.start` spawns the *tray thread*, which creates the window,
adds the icon and runs the Win32 message loop; every ``Shell_NotifyIconW`` call happens
there. The public methods may be called from any thread: they record the change and wake
the tray thread with a posted message (several quick tooltip changes collapse into one
update). Callbacks (``on_click``, menu actions, ``on_notification_click``, ``on_close``) run
one at a time on a separate *actions thread*, so a slow callback never freezes the icon or
the menu, and a callback may call :meth:`TrayIcon.stop` (the "Quit" item). Only the
``checked`` / ``enabled`` / ``visible`` / text callables of menu items run on the tray thread,
each time the menu opens: keep them quick (a flag, a registry read).

**Window.** The icon belongs to a hidden *top-level* tool window (``WS_POPUP`` with
``WS_EX_TOOLWINDOW``, never shown: no taskbar button, no Alt+Tab entry) rather than a
``HWND_MESSAGE`` message-only window. Message-only windows receive no broadcast messages,
so they would never hear ``TaskbarCreated`` (sent when Explorer restarts or the taskbar is
re-created) and the icon would vanish for good; and the ``SetForegroundWindow`` +
``WM_NULL`` workaround that makes the popup menu close when the user clicks elsewhere needs
a window that can become the foreground window. On ``TaskbarCreated`` the icon is added
again (``NIM_ADD`` + ``NIM_SETVERSION``); if the shell refuses the icon (Explorer still
starting at sign-in), adding is retried on a timer (:attr:`TrayIcon.retry_delays_ms`).

**Shell protocol.** ``NOTIFYICONDATAW`` (full Vista+ layout, :data:`NOTIFYICONDATAW_SIZE`)
with ``NOTIFYICON_VERSION_4``: left click / Enter arrive as ``NIN_SELECT`` /
``NIN_KEYSELECT`` (coalesced within the double-click time, so a double-click acts once),
the context menu as ``WM_CONTEXTMENU`` with the icon's anchor point in ``wParam`` (also
right for keyboard use), and ``NIF_SHOWTIP`` keeps the standard tooltip. If the shell
refuses version 4, the legacy mouse messages are handled instead. Icons are identified by
window + ``uID``, not by GUID (a GUID is bound to one executable path, which breaks
between ``python.exe`` installs and venvs).

**DPI.** Before creating its window the tray calls
:func:`needledropper.transport.wincapture.set_dpi_awareness` (idempotent, process wide;
the pixel beacon needs it anyway). A window keeps the DPI awareness it was created with,
so this makes the tray window per-monitor aware whichever component starts first: the
menu is drawn crisply, the anchor coordinates are physical pixels like the shell's, and
the icon is loaded at the real ``SM_CXSMICON`` size (16/20/24/32/... px, all present in
the bundled .ico files). Pass ``dpi_aware=False`` to leave the process alone.

**Notifications.** :class:`Notifier` decides which notifications reach the icon
(DESIGN-v1.2 1.4: only the ones that matter, each with its own once-per-session or
cooldown rule, :data:`NOTE_POLICIES`) and what a click on the last one does.
:func:`message_box` shows a blocking error dialog for problems that happen before the
icon exists (the windowed exe has no console to print to).

On other platforms the module imports fine and the menu model works, but
:meth:`TrayIcon.start` raises :class:`RuntimeError`.
"""
from __future__ import annotations

import atexit
import ctypes
import itertools
import logging
import os
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from . import resources

IS_WINDOWS = sys.platform == "win32"

log = logging.getLogger("needledropper.tray")

#: ``needledropper/assets`` (bundled by NeedleDropper.spec when frozen; see :mod:`.resources`).
ASSETS_DIR = resources.assets_dir()
#: The normal tray icon (generated by ``assets/make_icon.py``).
ICON_PATH = resources.tray_icon_path(False)
#: The "needle drops paused" variant (tone arm parked, grey label).
PAUSED_ICON_PATH = resources.tray_icon_path(True)

#: Text limits in UTF-16 code units, without the terminating NUL (``szTip[128]``,
#: ``szInfoTitle[64]``, ``szInfo[256]``).
TOOLTIP_MAX = 127
INFO_TITLE_MAX = 63
INFO_TEXT_MAX = 255

#: Documented ``sizeof(NOTIFYICONDATAW)`` (Vista+ layout) for this Python's bitness.
NOTIFYICONDATAW_SIZE = 976 if ctypes.sizeof(ctypes.c_void_p) == 8 else 956

# ----------------------------------------------------------- Win32 constants --
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_CONTEXTMENU = 0x007B
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_USER = 0x0400
WM_APP = 0x8000

#: Private messages of the tray window.
WM_TRAY_CALLBACK = WM_APP + 1  # uCallbackMessage: events from the shell
WM_TRAY_WAKE = WM_APP + 2  # pending tooltip / icon / notification updates
WM_TRAY_STOP = WM_APP + 3  # stop(): remove the icon, destroy the window, end the thread
WM_TRAY_MENU = WM_APP + 4  # show_menu()
TIMER_RETRY_ADD = 1

NIM_ADD = 0x0
NIM_MODIFY = 0x1
NIM_DELETE = 0x2
NIM_SETFOCUS = 0x3
NIM_SETVERSION = 0x4

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_STATE = 0x08
NIF_INFO = 0x10
NIF_GUID = 0x20
NIF_REALTIME = 0x40
NIF_SHOWTIP = 0x80

NIIF_NONE = 0x00
NIIF_INFO = 0x01
NIIF_WARNING = 0x02
NIIF_ERROR = 0x03
NIIF_USER = 0x04
NIIF_NOSOUND = 0x10
NIIF_LARGE_ICON = 0x20
NIIF_RESPECT_QUIET_TIME = 0x80

NOTIFYICON_VERSION_4 = 4

NIN_SELECT = WM_USER + 0
NINF_KEY = 0x1
NIN_KEYSELECT = NIN_SELECT | NINF_KEY
NIN_BALLOONSHOW = WM_USER + 2
NIN_BALLOONHIDE = WM_USER + 3
NIN_BALLOONTIMEOUT = WM_USER + 4
NIN_BALLOONUSERCLICK = WM_USER + 5
NIN_POPUPOPEN = WM_USER + 6
NIN_POPUPCLOSE = WM_USER + 7

WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080

MF_STRING = 0x0000
MF_UNCHECKED = 0x0000
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800

TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RIGHTALIGN = 0x0008
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

SM_CXICON = 11
SM_CYICON = 12
SM_MENUDROPALIGNMENT = 40
SM_CXSMICON = 49
SM_CYSMICON = 50

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
IDI_APPLICATION = 32512
MSGFLT_ALLOW = 1

#: ``notify(kind=...)`` -> NIIF_* icon flag.
NOTIFY_KINDS: Dict[str, int] = {"none": NIIF_NONE, "info": NIIF_INFO, "warning": NIIF_WARNING,
                                "error": NIIF_ERROR, "app": NIIF_USER}


# ------------------------------------------------------------------- text --
def fit_text(text: Any, limit: int) -> str:
    """``text`` as at most ``limit`` UTF-16 code units, for a fixed ``WCHAR[limit + 1]`` field.

    Longer text is cut on a character boundary (never between the two halves of a
    surrogate pair) and ends with an ellipsis; NUL characters are dropped.
    """
    s = "" if text is None else str(text)
    if "\x00" in s:
        s = s.replace("\x00", "")
    data = s.encode("utf-16-le", "surrogatepass")
    if len(data) <= 2 * limit:
        return s
    if limit <= 0:
        return ""
    cut = data[:2 * (limit - 1)]
    if len(cut) >= 2 and 0xD800 <= int.from_bytes(cut[-2:], "little") <= 0xDBFF:
        cut = cut[:-2]  # don't leave half a surrogate pair
    return cut.decode("utf-16-le", "surrogatepass") + "…"


# ------------------------------------------------------------- menu model --
Flag = Union[bool, Callable[[], bool]]
Text = Union[str, Callable[[], str]]


def _resolve_flag(value: Flag, default: bool, what: str) -> bool:
    if callable(value):
        try:
            return bool(value())
        except Exception:  # noqa: BLE001 - a broken callback must not break the menu
            log.exception("tray menu: the %s callback failed", what)
            return default
    return bool(value)


def _resolve_text(value: Text) -> str:
    if callable(value):
        try:
            return str(value())
        except Exception:  # noqa: BLE001
            log.exception("tray menu: a text callback failed")
            return "?"
    return str(value)


@dataclass(frozen=True)
class Separator:
    """A line between groups of menu items. Leading, trailing and doubled separators
    (e.g. around hidden items) are dropped when the menu is shown."""


#: The shared separator instance.
SEPARATOR = Separator()


@dataclass(eq=False)
class MenuItem:
    """One entry of the tray's right-click menu (a plain, mutable dataclass).

    * ``text`` - the label, or a callable returning it (evaluated each time the menu
      opens). ``&`` marks the keyboard mnemonic (``&&`` shows a literal ``&``).
    * ``action`` - what a click runs, on the actions thread. Plain items: ``action()``.
      Checkable items: ``action(new_state)`` with the state the user asked for (the
      opposite of the tick shown when the menu opened).
    * ``checkable`` / ``checked`` - a check item; ``checked`` is a bool, or a callable read
      each time the menu opens (e.g. ``autostart.is_enabled``). A bool is flipped
      automatically after ``action`` returns without raising; with a callable, ``action``
      is expected to change the underlying state.
    * ``enabled`` / ``visible`` - bools or callables; disabled items are greyed out,
      invisible ones left out.
    * ``default`` - shown in bold; a left click on the icon runs it when the
      :class:`TrayIcon` has no ``on_click``.
    * ``id`` - an optional stable key for :meth:`TrayIcon.find_item`.
    * ``submenu`` - a list of entries shown as a cascading submenu (``action`` is unused).
    """

    text: Text
    action: Optional[Callable[..., Any]] = None
    checkable: bool = False
    checked: Flag = False
    enabled: Flag = True
    visible: Flag = True
    default: bool = False
    id: Optional[str] = None
    submenu: Optional[List[Union["MenuItem", Separator]]] = None

    def __post_init__(self) -> None:
        if not (isinstance(self.text, str) or callable(self.text)):
            raise TypeError(f"MenuItem text must be a str or a callable, not {type(self.text).__name__}")
        if self.action is not None and not callable(self.action):
            raise TypeError(f"MenuItem action must be callable, not {type(self.action).__name__}")
        if self.submenu is not None:
            self.submenu = _checked_entries(self.submenu)
            if self.checkable:
                raise ValueError("a submenu item cannot be checkable")

    @property
    def label(self) -> str:
        """The current text."""
        return _resolve_text(self.text)

    def is_checked(self) -> bool:
        return self.checkable and _resolve_flag(self.checked, False, f"checked ({self.id or self.text!r})")

    def is_enabled(self) -> bool:
        return _resolve_flag(self.enabled, True, f"enabled ({self.id or self.text!r})")

    def is_visible(self) -> bool:
        return _resolve_flag(self.visible, True, f"visible ({self.id or self.text!r})")

    def activate(self, shown_checked: Optional[bool] = None) -> bool:
        """Run the item as if the user had picked it; True when something ran or changed.

        For a check item, ``shown_checked`` is the tick the user saw (default: the current
        state) and the action receives the opposite. Exceptions from the action propagate
        (and leave a bool ``checked`` unchanged).
        """
        if self.submenu is not None:
            return False
        if self.checkable:
            current = self.is_checked() if shown_checked is None else bool(shown_checked)
            new_state = not current
            if self.action is not None:
                self.action(new_state)
            if not callable(self.checked):
                self.checked = new_state
            return True
        if self.action is None:
            return False
        self.action()
        return True


MenuEntry = Union[MenuItem, Separator]


def _checked_entries(entries: Iterable[Any]) -> List[MenuEntry]:
    out: List[MenuEntry] = []
    for entry in entries:
        if not isinstance(entry, (MenuItem, Separator)):
            raise TypeError(f"menu entries must be MenuItem or Separator, not {type(entry).__name__}")
        out.append(entry)
    return out


def _tidy(entries: Iterable[MenuEntry]) -> List[MenuEntry]:
    """Drop leading, trailing and doubled separators."""
    out: List[MenuEntry] = []
    for entry in entries:
        if isinstance(entry, Separator) and (not out or isinstance(out[-1], Separator)):
            continue
        out.append(entry)
    while out and isinstance(out[-1], Separator):
        out.pop()
    return out


def find_item(entries: Iterable[MenuEntry], item_id: str) -> Optional[MenuItem]:
    """The first item (submenus included) whose ``id`` is ``item_id``."""
    for entry in entries:
        if isinstance(entry, MenuItem):
            if entry.id == item_id:
                return entry
            if entry.submenu:
                found = find_item(entry.submenu, item_id)
                if found is not None:
                    return found
    return None


def default_item(entries: Iterable[MenuEntry]) -> Optional[MenuItem]:
    """The first visible, enabled top-level item marked ``default``."""
    for entry in entries:
        if (isinstance(entry, MenuItem) and entry.default and entry.submenu is None
                and entry.is_visible() and entry.is_enabled()):
            return entry
    return None


@dataclass
class LayoutEntry:
    """One rendered row of a :class:`MenuLayout`."""

    kind: str  # "item", "separator" or "submenu"
    text: str = ""
    command: int = 0  # 0 for separators and submenus
    checkable: bool = False
    checked: bool = False
    enabled: bool = True
    default: bool = False
    children: List["LayoutEntry"] = field(default_factory=list)


class MenuLayout:
    """A snapshot of the menu as it is shown: rendered rows plus command id -> item.

    Built each time the menu opens (so callables are evaluated then); command ids are
    numbered from 1 in display order (0 means "nothing picked" for ``TrackPopupMenu``).
    :meth:`dispatch` runs the picked item with the state that was on screen.
    """

    FIRST_COMMAND = 1

    def __init__(self, entries: List[LayoutEntry], commands: Dict[int, Tuple[MenuItem, bool, bool]]) -> None:
        self.entries = entries
        #: command id -> (item, checked as shown, enabled as shown)
        self.commands = commands

    @classmethod
    def build(cls, items: Iterable[MenuEntry]) -> "MenuLayout":
        commands: Dict[int, Tuple[MenuItem, bool, bool]] = {}
        numbers = itertools.count(cls.FIRST_COMMAND)

        def walk(entries: Iterable[MenuEntry]) -> List[LayoutEntry]:
            rows: List[LayoutEntry] = []
            for entry in entries:
                if isinstance(entry, Separator):
                    if rows and rows[-1].kind != "separator":
                        rows.append(LayoutEntry("separator"))
                    continue
                if not isinstance(entry, MenuItem):
                    raise TypeError(f"menu entries must be MenuItem or Separator, not {type(entry).__name__}")
                if not entry.is_visible():
                    continue
                text = entry.label.replace("\x00", "")
                enabled = entry.is_enabled()
                if entry.submenu is not None:
                    children = walk(entry.submenu)
                    if children:
                        rows.append(LayoutEntry("submenu", text=text, enabled=enabled, children=children))
                    continue
                command = next(numbers)
                checked = entry.is_checked()
                commands[command] = (entry, checked, enabled)
                rows.append(LayoutEntry("item", text=text, command=command, checkable=entry.checkable,
                                        checked=checked, enabled=enabled, default=entry.default))
            while rows and rows[-1].kind == "separator":
                rows.pop()
            return rows

        return cls(walk(items), commands)

    @property
    def default_command(self) -> int:
        """Command id of the first enabled default item (0 when there is none)."""
        for command, (item, _checked, enabled) in sorted(self.commands.items()):
            if item.default and enabled:
                return command
        return 0

    def dispatch(self, command: int) -> bool:
        """Run the item behind ``command``; False for unknown or disabled commands."""
        found = self.commands.get(int(command))
        if found is None:
            return False
        item, shown_checked, enabled = found
        if not enabled:
            return False
        return item.activate(shown_checked)

    def lines(self, indent: str = "") -> List[str]:
        """Plain-text rendering, e.g. ``["Open setup... (default)", "---", "[x] Pause needle drops"]``."""
        out: List[str] = []

        def walk(rows: List[LayoutEntry], prefix: str) -> None:
            for row in rows:
                if row.kind == "separator":
                    out.append(prefix + "---")
                    continue
                mark = ("[x] " if row.checked else "[ ] ") if row.checkable else ""
                tags = [t for t, on in (("default", row.default), ("disabled", not row.enabled)) if on]
                suffix = f" ({', '.join(tags)})" if tags else ""
                out.append(f"{prefix}{mark}{row.text}{' >' if row.kind == 'submenu' else ''}{suffix}")
                if row.kind == "submenu":
                    walk(row.children, prefix + "    ")

        walk(self.entries, indent)
        return out


#: Item ids used by :func:`companion_menu`.
ITEM_STATUS = "status"
ITEM_SETUP = "setup"
ITEM_PAUSE = "pause"
ITEM_AUTOSTART = "autostart"
ITEM_LOG_FOLDER = "logs"
ITEM_QUIT = "quit"


def companion_menu(*, on_open_setup: Optional[Callable[[], Any]] = None,
                   on_pause: Optional[Callable[[bool], Any]] = None, paused: Flag = False,
                   on_autostart: Optional[Callable[[bool], Any]] = None, autostart: Flag = False,
                   on_open_log_folder: Optional[Callable[[], Any]] = None,
                   on_quit: Optional[Callable[[], Any]] = None,
                   status: Optional[Text] = None) -> List[MenuEntry]:
    """The companion's standard tray menu::

        <status>                  (optional, greyed out: e.g. "Connected to Spotify")
        ---
        Open setup...             (default: bold, also the left-click action)
        ---
        [ ] Pause needle drops    on_pause(paused: bool)
        [ ] Start with Windows    on_autostart(enabled: bool)
        ---
        Open log folder
        ---
        Quit

    ``paused`` / ``autostart`` give the tick: a bool, or a callable read each time the menu
    opens. Items whose callback is None are left out. Look items up by their ids
    (:data:`ITEM_SETUP`, :data:`ITEM_PAUSE`, ...) with :func:`find_item`.
    """
    entries: List[MenuEntry] = []
    if status is not None:
        entries += [MenuItem(status, enabled=False, id=ITEM_STATUS), SEPARATOR]
    if on_open_setup is not None:
        entries += [MenuItem("Open setup…", on_open_setup, default=True, id=ITEM_SETUP), SEPARATOR]
    if on_pause is not None:
        entries.append(MenuItem("Pause needle drops", on_pause, checkable=True, checked=paused, id=ITEM_PAUSE))
    if on_autostart is not None:
        entries.append(MenuItem("Start with Windows", on_autostart, checkable=True, checked=autostart,
                                id=ITEM_AUTOSTART))
    entries.append(SEPARATOR)
    if on_open_log_folder is not None:
        entries += [MenuItem("Open log folder", on_open_log_folder, id=ITEM_LOG_FOLDER), SEPARATOR]
    if on_quit is not None:
        entries.append(MenuItem("Quit", on_quit, id=ITEM_QUIT))
    return _tidy(entries)


# ------------------------------------------------------- Win32 declarations --
#: Every Win32 function this module calls, by name (each has explicit argtypes/restype).
PROTOTYPES: Dict[str, Any] = {}

if IS_WINDOWS:
    from ctypes import wintypes as wt

    LRESULT = ctypes.c_ssize_t  # LONG_PTR
    UINT_PTR = ctypes.c_size_t
    HCURSOR = wt.HANDLE

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.UINT),
            ("style", wt.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE),
            ("hIcon", wt.HICON),
            ("hCursor", HCURSOR),
            ("hbrBackground", wt.HBRUSH),
            ("lpszMenuName", wt.LPCWSTR),
            ("lpszClassName", wt.LPCWSTR),
            ("hIconSm", wt.HICON),
        ]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class _TimeoutOrVersion(ctypes.Union):
        _fields_ = [("uTimeout", wt.UINT), ("uVersion", wt.UINT)]

    class NOTIFYICONDATAW(ctypes.Structure):
        """``NOTIFYICONDATAW`` in its full (Vista and later) layout."""

        _anonymous_ = ("_u",)
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("hWnd", wt.HWND),
            ("uID", wt.UINT),
            ("uFlags", wt.UINT),
            ("uCallbackMessage", wt.UINT),
            ("hIcon", wt.HICON),
            ("szTip", wt.WCHAR * (TOOLTIP_MAX + 1)),
            ("dwState", wt.DWORD),
            ("dwStateMask", wt.DWORD),
            ("szInfo", wt.WCHAR * (INFO_TEXT_MAX + 1)),
            ("_u", _TimeoutOrVersion),
            ("szInfoTitle", wt.WCHAR * (INFO_TITLE_MAX + 1)),
            ("dwInfoFlags", wt.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wt.HICON),
        ]

    MSG = wt.MSG
    POINT = wt.POINT

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _proto(dll: Any, name: str, restype: Any, *argtypes: Any, optional: bool = False) -> Any:
        try:
            fn = getattr(dll, name)
        except AttributeError:
            if optional:
                return None
            raise
        fn.restype = restype
        fn.argtypes = list(argtypes)
        PROTOTYPES[name] = fn
        return fn

    # user32: window class, window and message loop
    _RegisterClassExW = _proto(_user32, "RegisterClassExW", wt.ATOM, ctypes.POINTER(WNDCLASSEXW))
    _UnregisterClassW = _proto(_user32, "UnregisterClassW", wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
    _CreateWindowExW = _proto(_user32, "CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
                              wt.HINSTANCE, wt.LPVOID)
    _DestroyWindow = _proto(_user32, "DestroyWindow", wt.BOOL, wt.HWND)
    _IsWindow = _proto(_user32, "IsWindow", wt.BOOL, wt.HWND)
    _DefWindowProcW = _proto(_user32, "DefWindowProcW", LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    _GetMessageW = _proto(_user32, "GetMessageW", wt.BOOL, ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT)
    _TranslateMessage = _proto(_user32, "TranslateMessage", wt.BOOL, ctypes.POINTER(MSG))
    _DispatchMessageW = _proto(_user32, "DispatchMessageW", LRESULT, ctypes.POINTER(MSG))
    _PostMessageW = _proto(_user32, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    _PostQuitMessage = _proto(_user32, "PostQuitMessage", None, ctypes.c_int)
    _RegisterWindowMessageW = _proto(_user32, "RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)
    _ChangeWindowMessageFilterEx = _proto(_user32, "ChangeWindowMessageFilterEx", wt.BOOL, wt.HWND, wt.UINT,
                                          wt.DWORD, wt.LPVOID, optional=True)  # Windows 7+
    _SetTimer = _proto(_user32, "SetTimer", UINT_PTR, wt.HWND, UINT_PTR, wt.UINT, wt.LPVOID)
    _KillTimer = _proto(_user32, "KillTimer", wt.BOOL, wt.HWND, UINT_PTR)
    # user32: popup menu
    _CreatePopupMenu = _proto(_user32, "CreatePopupMenu", wt.HMENU)
    _AppendMenuW = _proto(_user32, "AppendMenuW", wt.BOOL, wt.HMENU, wt.UINT, UINT_PTR, wt.LPCWSTR)
    _SetMenuDefaultItem = _proto(_user32, "SetMenuDefaultItem", wt.BOOL, wt.HMENU, wt.UINT, wt.UINT)
    _TrackPopupMenu = _proto(_user32, "TrackPopupMenu", wt.BOOL, wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, wt.HWND, wt.LPVOID)
    _EndMenu = _proto(_user32, "EndMenu", wt.BOOL)
    _DestroyMenu = _proto(_user32, "DestroyMenu", wt.BOOL, wt.HMENU)
    _SetForegroundWindow = _proto(_user32, "SetForegroundWindow", wt.BOOL, wt.HWND)
    _GetCursorPos = _proto(_user32, "GetCursorPos", wt.BOOL, ctypes.POINTER(POINT))
    _GetSystemMetrics = _proto(_user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
    _GetDoubleClickTime = _proto(_user32, "GetDoubleClickTime", wt.UINT)
    # user32: icons
    _LoadImageW = _proto(_user32, "LoadImageW", wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int,
                         ctypes.c_int, wt.UINT)
    _LoadIconW = _proto(_user32, "LoadIconW", wt.HICON, wt.HINSTANCE, wt.LPVOID)  # LPVOID: MAKEINTRESOURCE ids
    _DestroyIcon = _proto(_user32, "DestroyIcon", wt.BOOL, wt.HICON)
    # shell32
    _Shell_NotifyIconW = _proto(_shell32, "Shell_NotifyIconW", wt.BOOL, wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
    _ExtractIconExW = _proto(_shell32, "ExtractIconExW", wt.UINT, wt.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wt.HICON), ctypes.POINTER(wt.HICON), wt.UINT)
    # kernel32
    _GetModuleHandleW = _proto(_kernel32, "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)


def _require_windows(what: str) -> None:
    if not IS_WINDOWS:
        raise RuntimeError(f"{what} requires Windows (this is {sys.platform}); the tray icon is part of the "
                           "Windows companion")


def _win_error(what: str) -> RuntimeError:
    code = ctypes.get_last_error() if IS_WINDOWS else 0
    msg = ctypes.FormatError(code).strip() if (IS_WINDOWS and code) else "unknown error"
    return RuntimeError(f"{what} failed (Win32 error {code}: {msg})")


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _shell_notify(message: int, nid: Any) -> bool:
    """The real ``Shell_NotifyIconW`` (TrayIcon's default ``shell_notify``)."""
    return bool(_Shell_NotifyIconW(message, ctypes.byref(nid)))


def _ensure_dpi_awareness() -> None:
    """Make the process per-monitor DPI aware before the first window exists (see module doc)."""
    try:
        from .transport import wincapture

        log.debug("tray: DPI awareness: %s", wincapture.set_dpi_awareness())
    except Exception as exc:  # noqa: BLE001 - cosmetic; the tray works without it
        log.debug("tray: could not set DPI awareness: %s", exc)


def build_hmenu(entries: Sequence[LayoutEntry]) -> int:
    """A Win32 popup menu (``HMENU``) for rendered rows; the caller must ``DestroyMenu`` it.

    Items use their command ids, check marks and grey state; submenus are attached with
    ``MF_POPUP`` (destroyed with their parent); the default item is set with
    ``SetMenuDefaultItem``.
    """
    _require_windows("building a tray menu")
    hmenu = _CreatePopupMenu()
    if not hmenu:
        raise _win_error("CreatePopupMenu")
    try:
        for entry in entries:
            if entry.kind == "separator":
                if not _AppendMenuW(hmenu, MF_SEPARATOR, 0, None):
                    raise _win_error("AppendMenuW (separator)")
            elif entry.kind == "submenu":
                sub = build_hmenu(entry.children)
                flags = MF_STRING | MF_POPUP | (0 if entry.enabled else MF_GRAYED)
                if not _AppendMenuW(hmenu, flags, sub, entry.text):
                    error = _win_error("AppendMenuW (submenu)")
                    _DestroyMenu(sub)
                    raise error
            else:
                flags = MF_STRING | (MF_CHECKED if entry.checked else MF_UNCHECKED) | (0 if entry.enabled else MF_GRAYED)
                if not _AppendMenuW(hmenu, flags, entry.command, entry.text):
                    raise _win_error("AppendMenuW")
                if entry.default and entry.enabled:
                    _SetMenuDefaultItem(hmenu, entry.command, 0)
    except BaseException:
        _DestroyMenu(hmenu)
        raise
    return hmenu


@dataclass(frozen=True)
class _Notification:
    title: str
    text: str
    kind: str
    sound: bool
    realtime: bool
    respect_quiet_time: bool


_CLASS_SERIAL = itertools.count(1)
_STOP = None  # sentinel for the actions queue


class TrayIcon:
    """One notification-area icon with a menu, tooltip and notifications (see the module doc).

    ``TrayIcon(title, icon_path, menu_items, on_click=None)``:

    * ``title`` - the app name: the default tooltip and the hidden window's title;
    * ``icon_path`` - an ``.ico`` file (None: :data:`ICON_PATH`). If it cannot be loaded the
      icon of the running executable is used, then the generic application icon;
    * ``menu_items`` - :class:`MenuItem` / :data:`SEPARATOR` entries for the right-click menu;
    * ``on_click`` - called (no arguments) on a left click or Enter. Without it, a left
      click runs the menu's ``default`` item, or opens the menu when there is none.

    Keyword-only extras: ``tooltip`` (initial tooltip, default ``title``),
    ``on_notification_click`` (the user clicked a notification), ``on_close`` (Windows asked
    the app to close: ``WM_CLOSE`` from outside, e.g. an installer's Restart Manager, or the
    session ending; without it ``WM_CLOSE`` is ignored), ``icon_id`` (the ``uID``, 0..65535),
    ``dpi_aware`` (set process DPI awareness on start, default True) and ``shell_notify``
    (replaces ``Shell_NotifyIconW``: ``shell_notify(message, nid) -> bool``, called on the
    tray thread with a ``NOTIFYICONDATAW``; tests use it).

    Construction never touches Win32, so it works on any platform; :meth:`start` raises
    :class:`RuntimeError` off Windows or when the window cannot be created. After
    :meth:`stop` the same object can be started again. Also a context manager.
    """

    #: Delays (ms) between attempts to add the icon while the shell refuses it (e.g. Explorer is
    #: still starting at sign-in); the last delay repeats until it works.
    retry_delays_ms: Tuple[int, ...] = (500, 1000, 2000, 5000, 10000, 30000)

    def __init__(self, title: str, icon_path: Optional[Union[str, "os.PathLike[str]"]] = None,
                 menu_items: Iterable[MenuEntry] = (), on_click: Optional[Callable[[], Any]] = None, *,
                 tooltip: Optional[str] = None, on_notification_click: Optional[Callable[[], Any]] = None,
                 on_close: Optional[Callable[[], Any]] = None, icon_id: int = 1, dpi_aware: bool = True,
                 shell_notify: Optional[Callable[[int, Any], bool]] = None,
                 on_session_end: Optional[Callable[[], Any]] = None) -> None:
        title = str(title).strip()
        if not title:
            raise ValueError("TrayIcon needs a non-empty title")
        if not 0 <= int(icon_id) <= 0xFFFF:
            raise ValueError("icon_id must be in 0..65535 (it travels in a 16-bit field)")
        for name, fn in (("on_click", on_click), ("on_notification_click", on_notification_click),
                         ("on_close", on_close), ("shell_notify", shell_notify), ("on_session_end", on_session_end)):
            if fn is not None and not callable(fn):
                raise TypeError(f"{name} must be callable")
        self._title = title
        self._icon_id = int(icon_id)
        self._dpi_aware = bool(dpi_aware)
        self._shell_override = shell_notify
        self.on_click = on_click
        self.on_notification_click = on_notification_click
        self.on_close = on_close
        #: Called **synchronously** on the tray thread when Windows ends the session (log off, shut
        #: down, restart): the process may be killed as soon as WM_ENDSESSION returns, so this is the
        #: only place a clean-up (pause Spotify, restore its volume and the player's music) can run.
        #: Without it, ``on_close`` is dispatched instead (asynchronously, the v1.1 behaviour).
        self.on_session_end = on_session_end

        self._lock = threading.RLock()
        self._items: List[MenuEntry] = _checked_entries(menu_items)
        self._tooltip = title if tooltip is None else str(tooltip)
        self._icon_path: Path = Path(icon_path) if icon_path is not None else ICON_PATH

        # Runtime state; (re)initialised by start().
        self._thread: Optional[threading.Thread] = None
        self._actions: Optional["queue.SimpleQueue[Any]"] = None
        self._actions_thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None
        self._hinstance: Optional[int] = None
        self._class_name: Optional[str] = None
        self._wndproc: Any = None  # the WNDPROC callback object: must live as long as the window class
        self._msg_taskbar_created = 0
        self._hicon: Optional[int] = None
        self._hicon_large: Optional[int] = None
        self._owned_icons: List[int] = []
        self._visible = False
        self._visible_event = threading.Event()
        self._version4 = False
        self._retry_index = 0
        self._wake_posted = False
        self._dirty: Set[str] = set()
        self._notifications: Deque[_Notification] = deque()
        self._last_activate = float("-inf")
        self._double_click_s = 0.5
        self._menu_open = False
        self._stop_after_menu = False
        self._stopping = False
        self._startup_error: Optional[BaseException] = None

    # ---------------------------------------------------------- properties --
    @property
    def title(self) -> str:
        return self._title

    @property
    def tooltip(self) -> str:
        with self._lock:
            return self._tooltip

    @property
    def icon_path(self) -> Path:
        with self._lock:
            return self._icon_path

    @property
    def icon_id(self) -> int:
        return self._icon_id

    @property
    def hwnd(self) -> Optional[int]:
        """The hidden window's handle while running (diagnostics and tests)."""
        with self._lock:
            return self._hwnd

    @property
    def running(self) -> bool:
        """True from a successful :meth:`start` until :meth:`stop` (or a system close)."""
        thread = self._thread
        return thread is not None and thread.is_alive() and self.hwnd is not None and not self._stopping

    @property
    def visible(self) -> bool:
        """True while the shell shows the icon (False while it is being re-added)."""
        return self._visible

    @property
    def uses_version_4(self) -> bool:
        """True when the shell accepted ``NOTIFYICON_VERSION_4`` for the current icon."""
        return self._version4

    @property
    def menu_items(self) -> List[MenuEntry]:
        """A copy of the menu entries (the items themselves are shared and may be mutated)."""
        with self._lock:
            return list(self._items)

    # ------------------------------------------------------------- updates --
    def set_menu(self, menu_items: Iterable[MenuEntry]) -> None:
        """Replace the menu; it is rebuilt every time it opens, so no refresh is needed."""
        entries = _checked_entries(menu_items)
        with self._lock:
            self._items = entries

    def find_item(self, item_id: str) -> Optional[MenuItem]:
        """The menu item whose ``id`` is ``item_id`` (submenus included), or None."""
        return find_item(self.menu_items, item_id)

    def set_tooltip(self, text: str) -> None:
        """Change the hover text (cut to :data:`TOOLTIP_MAX` UTF-16 units); any thread."""
        with self._lock:
            self._tooltip = str(text)
            self._dirty.add("tip")
        self._wake()

    def set_icon(self, icon_path: Optional[Union[str, "os.PathLike[str]"]]) -> None:
        """Show another ``.ico`` (None: :data:`ICON_PATH`), e.g. :data:`PAUSED_ICON_PATH`; any thread."""
        with self._lock:
            self._icon_path = Path(icon_path) if icon_path is not None else ICON_PATH
            self._dirty.add("icon")
        self._wake()

    def notify(self, title: str, text: str, *, kind: str = "info", sound: bool = True, realtime: bool = False,
               respect_quiet_time: bool = True) -> bool:
        """Show a balloon (a toast on Windows 10/11) from the icon; any thread.

        ``kind``: ``"info"``, ``"warning"``, ``"error"``, ``"none"`` (no icon) or ``"app"``
        (this app's icon). ``sound=False`` keeps it silent; ``realtime=True`` drops it when it
        cannot be shown right away (instead of queueing it); ``respect_quiet_time`` skips it
        during the first hour after a new user's first sign-in. Title and text are cut to
        :data:`INFO_TITLE_MAX` / :data:`INFO_TEXT_MAX` UTF-16 units.

        Returns True when queued for the tray thread, False when the icon is not running.
        Windows itself may still suppress it (Focus Assist / Do not disturb, notification
        settings, a full-screen game).
        """
        if kind not in NOTIFY_KINDS:
            raise ValueError(f"unknown notification kind {kind!r} (expected one of {', '.join(NOTIFY_KINDS)})")
        note = _Notification(str(title), str(text), kind, bool(sound), bool(realtime), bool(respect_quiet_time))
        with self._lock:
            if not self._hwnd or self._stopping:
                return False
            self._notifications.append(note)
        self._wake()
        return True

    def show_menu(self) -> bool:
        """Open the popup menu at the mouse pointer (e.g. as an ``on_click``); any thread."""
        hwnd = self.hwnd
        if not hwnd or self._stopping:
            return False
        return bool(_PostMessageW(hwnd, WM_TRAY_MENU, 0, 0))

    # ----------------------------------------------------------- lifecycle --
    def start(self, timeout: float = 10.0) -> "TrayIcon":
        """Create the window on the tray thread and add the icon; returns ``self``.

        Returns once the window exists (the icon is added right after; see
        :meth:`wait_visible`). Idempotent while running. Raises :class:`RuntimeError` off
        Windows, or when the window cannot be created.
        """
        _require_windows("TrayIcon")
        with self._lock:
            previous = self._thread
            if previous is not None and previous.is_alive() and not self._stopping:
                return self
        if previous is not None and previous.is_alive():  # a stop() is still finishing
            previous.join(timeout)
            if previous.is_alive():
                raise RuntimeError("the previous tray thread is still running; cannot start again yet")
        with self._lock:
            self._stopping = False
            self._startup_error = None
            self._dirty.clear()
            self._notifications.clear()
            self._wake_posted = False
            self._retry_index = 0
            self._version4 = False
            self._last_activate = float("-inf")
            self._menu_open = self._stop_after_menu = False
            ready = threading.Event()
            thread = threading.Thread(target=self._tray_main, args=(ready,), name="NeedleDropper-tray",
                                      daemon=True)
            self._thread = thread
        self._start_actions()
        thread.start()
        if not ready.wait(timeout):
            self.stop(timeout=1.0)
            raise RuntimeError(f"the tray icon window was not created within {timeout:g} s")
        if self._startup_error is not None:
            error = self._startup_error
            thread.join(timeout)
            self._stop_actions(timeout)
            with self._lock:
                if self._thread is thread:
                    self._thread = None
            raise RuntimeError(f"could not create the tray icon: {error}") from error
        atexit.register(self._stop_at_exit)
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """Remove the icon, destroy the window and end the threads; any thread, idempotent.

        From a callback (e.g. the "Quit" item) it does not wait for the actions thread it
        runs on; that thread ends when the callback returns.
        """
        with self._lock:
            thread = self._thread
            hwnd = self._hwnd
            self._stopping = True
        if thread is not None:
            if hwnd and not _PostMessageW(hwnd, WM_TRAY_STOP, 0, 0):
                log.debug("tray: could not post the stop message (window already gone?)")
            if thread is not threading.current_thread():
                thread.join(timeout)
                if thread.is_alive():
                    log.warning("tray: the tray thread did not stop within %.1f s", timeout)
            with self._lock:
                if self._thread is thread and not thread.is_alive():
                    self._thread = None
        self._stop_actions(timeout)
        try:
            atexit.unregister(self._stop_at_exit)
        except Exception:  # noqa: BLE001 - interpreter shutting down
            pass

    def join(self, timeout: Optional[float] = None) -> bool:
        """Wait for the tray thread to end (after :meth:`stop`); True when it has."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def wait_visible(self, timeout: Optional[float] = None) -> bool:
        """Wait until the shell shows the icon; True when it does."""
        return self._visible_event.wait(timeout)

    def __enter__(self) -> "TrayIcon":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def __repr__(self) -> str:
        return f"<TrayIcon {self._title!r} running={self.running} visible={self.visible}>"

    def _stop_at_exit(self) -> None:
        self.stop(timeout=2.0)

    # ------------------------------------------------------- actions thread --
    def _start_actions(self) -> None:
        actions: "queue.SimpleQueue[Any]" = queue.SimpleQueue()
        worker = threading.Thread(target=self._actions_main, args=(actions,), name="NeedleDropper-tray-actions",
                                  daemon=True)
        with self._lock:
            self._actions, self._actions_thread = actions, worker
        worker.start()

    def _stop_actions(self, timeout: float) -> None:
        with self._lock:
            actions, worker = self._actions, self._actions_thread
            self._actions = self._actions_thread = None
        if actions is not None:
            actions.put(_STOP)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)

    @staticmethod
    def _actions_main(actions: "queue.SimpleQueue[Any]") -> None:
        while True:
            job = actions.get()
            if job is _STOP:
                return
            fn, args = job
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - report and keep serving callbacks
                log.exception("tray: callback %r failed", fn)

    def _dispatch(self, fn: Optional[Callable[..., Any]], *args: Any) -> None:
        """Run ``fn(*args)`` on the actions thread."""
        if fn is None:
            return
        with self._lock:
            actions = self._actions
        if actions is None:
            log.debug("tray: dropped callback %r (not running)", fn)
            return
        actions.put((fn, args))

    # ---------------------------------------------------------- tray thread --
    def _tray_main(self, ready: threading.Event) -> None:
        try:
            if self._dpi_aware:
                _ensure_dpi_awareness()
            self._create_window()
        except BaseException as exc:  # noqa: BLE001 - re-raised by start()
            self._startup_error = exc
            self._teardown()
            ready.set()
            return
        ready.set()
        try:
            self._release(self._load_icons())
            self._add_icon()
            if self._stopping:  # stop() ran before the window existed
                _PostMessageW(self._hwnd, WM_TRAY_STOP, 0, 0)
            self._message_loop()
        except BaseException:  # noqa: BLE001
            log.exception("tray: the tray thread failed")
        finally:
            self._teardown()

    def _create_window(self) -> None:
        self._hinstance = _GetModuleHandleW(None)
        self._msg_taskbar_created = _RegisterWindowMessageW("TaskbarCreated") or 0
        self._double_click_s = max(0.1, min(2.0, (_GetDoubleClickTime() or 500) / 1000.0))
        self._wndproc = WNDPROC(self._window_proc)
        name = f"NeedleDropper.TrayIcon.{os.getpid()}.{next(_CLASS_SERIAL)}"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinstance
        wc.lpszClassName = name
        if not _RegisterClassExW(ctypes.byref(wc)):
            raise _win_error("RegisterClassExW")
        self._class_name = name
        hwnd = _CreateWindowExW(WS_EX_TOOLWINDOW, name, self._title, WS_POPUP, 0, 0, 0, 0, None, None,
                                self._hinstance, None)
        if not hwnd:
            raise _win_error("CreateWindowExW")
        with self._lock:
            self._hwnd = hwnd
        if _ChangeWindowMessageFilterEx is not None:
            # Let Explorer's messages through UIPI if the companion runs elevated: the
            # TaskbarCreated broadcast and the icon's callback message.
            for message in (self._msg_taskbar_created, WM_TRAY_CALLBACK):
                if message:
                    _ChangeWindowMessageFilterEx(hwnd, message, MSGFLT_ALLOW, None)

    def _message_loop(self) -> None:
        msg = MSG()
        pmsg = ctypes.byref(msg)
        while True:
            result = _GetMessageW(pmsg, None, 0, 0)
            if result == 0:  # WM_QUIT
                return
            if result == -1:
                raise _win_error("GetMessageW")
            _TranslateMessage(pmsg)
            _DispatchMessageW(pmsg)

    def _teardown(self) -> None:
        """Undo everything _tray_main created (tray thread; also after a failed start)."""
        hwnd = self._hwnd
        if hwnd:
            self._delete_icon()
            _KillTimer(hwnd, TIMER_RETRY_ADD)
            if _IsWindow(hwnd):
                _DestroyWindow(hwnd)
        with self._lock:
            self._hwnd = None
            self._wake_posted = False
        if self._class_name:
            if not _UnregisterClassW(self._class_name, self._hinstance):
                log.debug("tray: %s", _win_error("UnregisterClassW"))
            self._class_name = None
        self._destroy_icons()
        self._set_visible(False)
        # Nothing can call the window procedure any more: no window of the class is left
        # and the class itself is gone.
        self._wndproc = None

    def _window_proc(self, hwnd: Any, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == WM_TRAY_CALLBACK:
                self._on_shell_event(wparam, lparam)
                return 0
            if msg == WM_TRAY_WAKE:
                self._drain()
                return 0
            if msg == WM_TRAY_STOP:
                if self._menu_open:  # close the menu first; _show_menu finishes the stop
                    self._stop_after_menu = True
                    _EndMenu()
                else:
                    _DestroyWindow(hwnd)
                return 0
            if msg == WM_TRAY_MENU:
                self._show_menu(None)
                return 0
            if self._msg_taskbar_created and msg == self._msg_taskbar_created:
                self._on_taskbar_created()
                return 0
            if msg == WM_TIMER and wparam == TIMER_RETRY_ADD:
                _KillTimer(hwnd, TIMER_RETRY_ADD)
                if not self._visible:
                    self._add_icon()
                return 0
            if msg == WM_CLOSE:  # never sent by this module: Windows or another program asks us to close
                if self.on_close is not None:
                    self._dispatch(self.on_close)
                else:
                    log.info("tray: ignoring WM_CLOSE (no on_close handler)")
                return 0
            if msg == WM_QUERYENDSESSION:
                return 1  # never block a log off / shut down; the clean-up runs at WM_ENDSESSION
            if msg == WM_ENDSESSION:
                if wparam:  # the session is ending: don't leave a ghost icon behind
                    self._delete_icon()
                    if self.on_session_end is not None:
                        try:
                            self.on_session_end()  # blocks (bounded by the callback) until cleaned up
                        except Exception:  # noqa: BLE001 - never escape into Win32
                            log.exception("tray: session-end clean-up failed")
                    else:
                        self._dispatch(self.on_close)
                return 0
            if msg == WM_DESTROY:
                self._delete_icon()
                _PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001 - an exception must never escape into Win32
            log.exception("tray: error while handling window message 0x%04X", msg)
            return 0
        return _DefWindowProcW(hwnd, msg, wparam, lparam)

    # -------------------------------------------------- shell events (tray) --
    def _on_shell_event(self, wparam: int, lparam: int) -> None:
        event = lparam & 0xFFFF
        if self._version4:
            if (lparam >> 16) & 0xFFFF != self._icon_id:
                return
            if event in (NIN_SELECT, NIN_KEYSELECT):
                self._on_activate(self._anchor(wparam))
            elif event == WM_CONTEXTMENU:
                self._show_menu(self._anchor(wparam))
            elif event == NIN_BALLOONUSERCLICK:
                self._dispatch(self.on_notification_click)
            return
        # Legacy callbacks (the shell refused version 4): wParam = uID, lParam = the message.
        if wparam != self._icon_id:
            return
        if event == WM_LBUTTONUP:
            self._on_activate(None)
        elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
            self._show_menu(None)
        elif event == NIN_BALLOONUSERCLICK:
            self._dispatch(self.on_notification_click)

    @staticmethod
    def _anchor(wparam: int) -> Optional[Tuple[int, int]]:
        """The icon's anchor point from a version-4 callback (signed: monitors left of / above
        the primary one have negative coordinates); None if absent."""
        x, y = _signed16(wparam), _signed16(wparam >> 16)
        return None if (x, y) == (0, 0) else (x, y)

    def _on_activate(self, anchor: Optional[Tuple[int, int]]) -> None:
        now = time.monotonic()
        if now - self._last_activate < self._double_click_s:
            return  # the second half of a double-click (or a repeated NIN_KEYSELECT)
        self._last_activate = now
        if self.on_click is not None:
            self._dispatch(self.on_click)
            return
        item = default_item(self.menu_items)
        if item is not None:
            self._dispatch(item.activate)
        else:
            self._show_menu(anchor)

    def _show_menu(self, anchor: Optional[Tuple[int, int]]) -> None:
        hwnd = self._hwnd
        if not hwnd or self._menu_open:
            return
        layout = MenuLayout.build(self.menu_items)
        if not layout.entries:
            return
        if anchor is None:
            pt = POINT()
            anchor = (pt.x, pt.y) if _GetCursorPos(ctypes.byref(pt)) else (0, 0)
        hmenu = build_hmenu(layout.entries)
        command = 0
        self._menu_open = True
        try:
            # KB135788: the window must be in the foreground, or the menu won't close when
            # the user clicks elsewhere; and WM_NULL afterwards, or the next one misbehaves.
            _SetForegroundWindow(hwnd)
            align = TPM_RIGHTALIGN if _GetSystemMetrics(SM_MENUDROPALIGNMENT) else TPM_LEFTALIGN
            command = _TrackPopupMenu(hmenu, align | TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                      anchor[0], anchor[1], 0, hwnd, None)
            _PostMessageW(hwnd, WM_NULL, 0, 0)
        finally:
            self._menu_open = False
            _DestroyMenu(hmenu)
        if self._stop_after_menu:
            self._stop_after_menu = False
            _DestroyWindow(hwnd)
            return
        if command:
            self._dispatch(layout.dispatch, command)

    def _on_taskbar_created(self) -> None:
        log.info("tray: the taskbar was (re)created; adding the icon again")
        self._set_visible(False)
        _KillTimer(self._hwnd, TIMER_RETRY_ADD)
        self._retry_index = 0
        old = self._load_icons()  # the new taskbar may use another DPI
        self._add_icon()
        self._release(old)

    # ------------------------------------------------------ icon management --
    def _set_visible(self, value: bool) -> None:
        self._visible = value
        if value:
            self._visible_event.set()
        else:
            self._visible_event.clear()

    def _shell(self, message: int, nid: Any) -> bool:
        fn = self._shell_override or _shell_notify
        try:
            return bool(fn(message, nid))
        except Exception:  # noqa: BLE001
            log.exception("tray: Shell_NotifyIconW(%d) raised", message)
            return False

    def _nid(self, flags: int) -> Any:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = self._icon_id
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.hIcon = self._hicon
        nid.szTip = fit_text(self.tooltip, TOOLTIP_MAX)
        return nid

    def _add_icon(self) -> bool:
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        ok = self._shell(NIM_ADD, nid)
        if not ok:
            # Maybe it is there already (an earlier NIM_ADD timed out but went through).
            ok = self._shell(NIM_MODIFY, nid)
        if not ok:
            self._set_visible(False)
            self._schedule_retry()
            return False
        version = self._nid(0)
        version.uVersion = NOTIFYICON_VERSION_4
        self._version4 = self._shell(NIM_SETVERSION, version)
        if not self._version4:
            log.info("tray: the shell refused NOTIFYICON_VERSION_4; using legacy mouse messages")
        if self._retry_index:
            log.info("tray: the notification-area icon was added")
        self._retry_index = 0
        self._set_visible(True)
        return True

    def _schedule_retry(self) -> None:
        delays = tuple(self.retry_delays_ms) or (30000,)
        delay = int(delays[min(self._retry_index, len(delays) - 1)])
        if self._retry_index == 0:
            log.warning("tray: the shell did not accept the notification-area icon; retrying (Explorer may "
                        "still be starting)")
        self._retry_index += 1
        if not _SetTimer(self._hwnd, TIMER_RETRY_ADD, max(10, delay), None):
            log.warning("tray: %s", _win_error("SetTimer"))

    def _delete_icon(self) -> None:
        if not self._visible:
            return
        self._shell(NIM_DELETE, self._nid(0))
        self._set_visible(False)

    def _load_icons(self) -> List[int]:
        """(Re)load the small (tray) and large (notification) icon images at the current DPI.

        Returns the previous handles: the caller destroys them (:meth:`_release`) once the
        shell has been given the new ones.
        """
        old = self._owned_icons
        self._owned_icons = []
        small_cx = _GetSystemMetrics(SM_CXSMICON) or 16
        small_cy = _GetSystemMetrics(SM_CYSMICON) or 16
        large_cx = _GetSystemMetrics(SM_CXICON) or 32
        large_cy = _GetSystemMetrics(SM_CYICON) or 32
        path = self.icon_path
        small = self._icon_from_file(path, small_cx, small_cy)
        if small is None:
            small = self._icon_from_exe()
        if small is None:
            small = _LoadIconW(None, IDI_APPLICATION)  # shared system icon: never destroyed
        self._hicon = small
        self._hicon_large = self._icon_from_file(path, large_cx, large_cy)
        return old

    @staticmethod
    def _release(handles: Iterable[int]) -> None:
        for handle in handles:
            _DestroyIcon(handle)

    def _icon_from_file(self, path: Path, cx: int, cy: int) -> Optional[int]:
        if not path.is_file():
            log.warning("tray: icon file not found: %s", path)
            return None
        handle = _LoadImageW(None, str(path), IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
        if not handle:
            log.warning("tray: could not load %s: %s", path, _win_error("LoadImageW"))
            return None
        self._owned_icons.append(handle)
        return handle

    def _icon_from_exe(self) -> Optional[int]:
        exe = sys.executable
        if not exe:
            return None
        small = wt.HICON()
        if _ExtractIconExW(exe, 0, None, ctypes.byref(small), 1) and small.value:
            self._owned_icons.append(small.value)
            return small.value
        return None

    def _destroy_icons(self) -> None:
        for handle in self._owned_icons:
            _DestroyIcon(handle)
        self._owned_icons = []
        self._hicon = self._hicon_large = None

    # --------------------------------------------- queued updates (tray) --
    def _wake(self) -> None:
        """Ask the tray thread to apply pending changes (one posted message at a time)."""
        with self._lock:
            hwnd = self._hwnd
            if not hwnd or self._wake_posted:
                return
            self._wake_posted = True
        if not _PostMessageW(hwnd, WM_TRAY_WAKE, 0, 0):
            with self._lock:
                self._wake_posted = False
            log.debug("tray: %s", _win_error("PostMessageW"))

    def _drain(self) -> None:
        with self._lock:
            self._wake_posted = False
            dirty, self._dirty = self._dirty, set()
            notes = list(self._notifications)
            self._notifications.clear()
        if "icon" in dirty:
            old = self._load_icons()
            if self._visible and not self._shell(NIM_MODIFY, self._nid(NIF_ICON)):
                log.debug("tray: could not change the icon image")
            self._release(old)
        if "tip" in dirty and self._visible and not self._shell(NIM_MODIFY, self._nid(NIF_TIP | NIF_SHOWTIP)):
            log.debug("tray: could not change the tooltip")
        for note in notes:
            self._show_notification(note)

    def _show_notification(self, note: _Notification) -> None:
        if not self._visible:
            log.info("tray: notification not shown (no icon in the notification area): %s", note.title)
            return
        nid = self._nid(NIF_INFO | (NIF_REALTIME if note.realtime else 0))
        nid.szInfoTitle = fit_text(note.title, INFO_TITLE_MAX)
        nid.szInfo = fit_text(note.text, INFO_TEXT_MAX) or " "  # an empty text would hide the balloon
        flags = NOTIFY_KINDS[note.kind]
        if note.kind == "app":
            nid.hBalloonIcon = self._hicon_large or self._hicon
            if self._hicon_large:
                flags |= NIIF_LARGE_ICON
        if not note.sound:
            flags |= NIIF_NOSOUND
        if note.respect_quiet_time:
            flags |= NIIF_RESPECT_QUIET_TIME
        nid.dwInfoFlags = flags
        if not self._shell(NIM_MODIFY, nid):
            log.info("tray: the shell did not accept the notification %r", note.title)


def default_icon_path(paused: bool = False) -> Path:
    """:data:`PAUSED_ICON_PATH` or :data:`ICON_PATH`."""
    return PAUSED_ICON_PATH if paused else ICON_PATH


# ------------------------------------------------------------ notifications --
#: The companion's notification kinds. DESIGN-v1.2 1.4 lists the only events that may
#: interrupt the player; the last two are the crash-safe thread wrapper and the update check.
NOTE_LINKED = "linked"  # the first frame from World of Warcraft this session
NOTE_TEST = "test"  # a TEST-flagged frame (/needle test, Settings > Send a test signal, Test / Replay)
NOTE_SPOTIFY_EXPIRED = "spotify-expired"  # the stored Spotify sign-in stopped working
NOTE_NOT_FOUND = "spotify-not-found"  # a search-only drop has no match on Spotify
NOTE_NOT_EMBEDDABLE = "youtube-not-embeddable"  # the video's owner does not allow embedding
NOTE_SPOTIFY_STUCK = "spotify-stuck"  # Spotify accepted a song but did not start it (restart the app)
NOTE_FAULT = "fault"  # a transport / engine thread died
NOTE_UPDATE = "update"  # a newer NeedleDropper release exists


@dataclass(frozen=True)
class NotePolicy:
    """How often one kind of notification may appear: once per app session, or not again
    for the same key within ``cooldown_s`` seconds (both transports deliver every frame,
    and an addon repeats a test frame for a few seconds)."""

    once_per_session: bool = False
    cooldown_s: float = 0.0


NOTE_POLICIES: Dict[str, NotePolicy] = {
    NOTE_LINKED: NotePolicy(once_per_session=True),
    NOTE_TEST: NotePolicy(cooldown_s=15.0),
    NOTE_SPOTIFY_EXPIRED: NotePolicy(once_per_session=True),
    NOTE_NOT_FOUND: NotePolicy(cooldown_s=6 * 3600.0),
    NOTE_NOT_EMBEDDABLE: NotePolicy(cooldown_s=6 * 3600.0),
    NOTE_SPOTIFY_STUCK: NotePolicy(cooldown_s=30 * 60.0),
    NOTE_FAULT: NotePolicy(once_per_session=True),
    NOTE_UPDATE: NotePolicy(once_per_session=True),
}


@dataclass(frozen=True)
class Note:
    """One notification that passed the policy (``shown``: the icon accepted it)."""

    kind: str
    title: str
    text: str
    key: Optional[str]
    at: float
    shown: bool


class Notifier:
    """The companion's notification policy in front of a :class:`TrayIcon` (any thread).

    :meth:`notify` drops what :data:`NOTE_POLICIES` says the player has already seen,
    logs every notification it lets through and hands it to the icon (when one is
    attached; without one, e.g. ``needledropper run``, it is only logged). The ``action``
    of the most recent notification runs on :meth:`clicked` (wire it to
    ``TrayIcon(on_notification_click=...)``), e.g. "open the Connect page" for an expired
    Spotify sign-in. ``history`` keeps the last 50 notes (tests, diagnostics).
    """

    def __init__(self, icon: Any = None, *, title: str = "NeedleDropper",
                 clock: Callable[[], float] = time.monotonic,
                 policies: Optional[Dict[str, NotePolicy]] = None) -> None:
        self.title = str(title)
        self._icon = icon
        self._clock = clock
        self._policies = dict(NOTE_POLICIES if policies is None else policies)
        self._lock = threading.Lock()
        self._seen: Dict[Tuple[str, Optional[str]], float] = {}
        self._action: Optional[Callable[[], Any]] = None
        self.history: Deque[Note] = deque(maxlen=50)

    def attach(self, icon: Any) -> None:
        """Deliver to ``icon`` from now on (None: log only)."""
        with self._lock:
            self._icon = icon

    def notify(self, kind: str, text: str, *, title: Optional[str] = None, key: Optional[str] = None,
               action: Optional[Callable[[], Any]] = None, level: str = "app") -> bool:
        """Show ``text`` unless the policy for ``kind`` (and ``key``) suppresses it; True when let through."""
        policy = self._policies.get(kind, NotePolicy())
        now = self._clock()
        seen_key = (str(kind), None if key is None else str(key))
        with self._lock:
            prev = self._seen.get(seen_key)
            if prev is not None and (policy.once_per_session or now - prev < policy.cooldown_s):
                return False
            self._seen[seen_key] = now
            self._action = action
            icon = self._icon
        heading = self.title if title is None else str(title)
        shown = False
        if icon is not None:
            try:
                shown = bool(icon.notify(heading, text, kind=level if level in NOTIFY_KINDS else "info"))
            except Exception:  # noqa: BLE001 - a notification must never break the caller
                log.exception("tray: could not show the notification %r", heading)
        log.info("notification [%s]: %s - %s", kind, heading, text)
        with self._lock:
            self.history.append(Note(str(kind), heading, str(text), seen_key[1], now, shown))
        return True

    def reset(self, kind: str, key: Optional[str] = None) -> None:
        """Let a once-per-session notification appear again (e.g. after Spotify was reconnected)."""
        with self._lock:
            self._seen.pop((str(kind), None if key is None else str(key)), None)

    def clicked(self) -> None:
        """The player clicked the last notification: run its action (if any)."""
        with self._lock:
            action = self._action
        if action is not None:
            action()

    def kinds(self) -> List[str]:
        """The kinds in :attr:`history`, oldest first."""
        with self._lock:
            return [n.kind for n in self.history]


MB_OK = 0x00000000
MB_ICONERROR = 0x00000010
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000
_MB_ICONS = {"error": MB_ICONERROR, "warning": MB_ICONWARNING, "info": MB_ICONINFORMATION}


def message_box(title: str, text: str, *, kind: str = "error") -> bool:
    """A modal message dialog (blocks until dismissed); True when shown.

    For problems the player must see before the tray icon exists, e.g. "port 8322 is
    busy": the windowed exe has no console. Off Windows (or if the dialog cannot be
    shown) the text goes to stderr when there is one, and False is returned.
    """
    if IS_WINDOWS:
        try:
            fn = _user32.MessageBoxW
            fn.restype = ctypes.c_int
            fn.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
            flags = MB_OK | _MB_ICONS.get(kind, MB_ICONERROR) | MB_SETFOREGROUND | MB_TOPMOST
            if fn(None, str(text), str(title), flags):
                return True
        except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
            log.debug("tray: MessageBoxW failed", exc_info=True)
    stream = sys.stderr
    if stream is not None:
        try:
            print(f"{title}: {text}", file=stream)
        except (OSError, ValueError):
            pass
    return False


__all__ = [
    "IS_WINDOWS", "ASSETS_DIR", "ICON_PATH", "PAUSED_ICON_PATH", "TOOLTIP_MAX", "INFO_TITLE_MAX", "INFO_TEXT_MAX",
    "NOTIFYICONDATAW_SIZE", "NOTIFY_KINDS", "PROTOTYPES", "fit_text", "Separator", "SEPARATOR", "MenuItem",
    "MenuEntry", "LayoutEntry", "MenuLayout", "find_item", "default_item", "companion_menu", "build_hmenu",
    "default_icon_path", "TrayIcon", "ITEM_STATUS", "ITEM_SETUP", "ITEM_PAUSE", "ITEM_AUTOSTART",
    "ITEM_LOG_FOLDER", "ITEM_QUIT", "NOTE_LINKED", "NOTE_TEST", "NOTE_SPOTIFY_EXPIRED", "NOTE_NOT_FOUND",
    "NOTE_NOT_EMBEDDABLE", "NOTE_SPOTIFY_STUCK", "NOTE_FAULT", "NOTE_UPDATE", "NotePolicy", "NOTE_POLICIES", "Note",
    "Notifier",
    "message_box",
]
