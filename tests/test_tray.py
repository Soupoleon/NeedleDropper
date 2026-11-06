"""Tray icon (``needledropper.tray``): menu model, Win32 layer, bundled icons.

* Everywhere: the menu model and command dispatch, text fitting, and that the module
  imports and refuses to start off Windows.
* Windows (default run, nothing visible): struct layouts and ctypes prototypes, the .ico
  files, popup-menu construction, and the whole tray thread against a *fake*
  ``Shell_NotifyIconW`` (hidden window, message loop, tooltip/notification/icon updates,
  clicks, TaskbarCreated, retries, WNDPROC lifetime, stop/restart).
* Windows + ``NEEDLEDROPPER_LIVE_TESTS=1``: a real icon in the notification area (tooltip,
  a silent notification, the popup menu), removed again at the end.
"""
from __future__ import annotations

import gc
import importlib.util
import logging
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import tray  # noqa: E402
from needledropper.tray import SEPARATOR, MenuItem, MenuLayout  # noqa: E402

ASSETS = ROOT / "companion" / "needledropper" / "assets"
WINDOWS = sys.platform == "win32"
LIVE = os.environ.get("NEEDLEDROPPER_LIVE_TESTS") == "1"

windows_only = pytest.mark.skipif(not WINDOWS, reason="Win32 API")
live_only = pytest.mark.skipif(not (WINDOWS and LIVE),
                               reason="shows a real tray icon: set NEEDLEDROPPER_LIVE_TESTS=1 (Windows)")

if WINDOWS:
    import ctypes
    from ctypes import wintypes as wt

    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    def _fn(name, restype, *argtypes):
        fn = getattr(_u32, name)
        fn.restype, fn.argtypes = restype, list(argtypes)
        return fn

    SendMessageW = _fn("SendMessageW", ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    PostMessageW = _fn("PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    IsWindow = _fn("IsWindow", wt.BOOL, wt.HWND)
    IsWindowVisible = _fn("IsWindowVisible", wt.BOOL, wt.HWND)
    GetAncestor = _fn("GetAncestor", wt.HWND, wt.HWND, wt.UINT)
    GetDesktopWindow = _fn("GetDesktopWindow", wt.HWND)
    RegisterWindowMessageW = _fn("RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)
    FindWindowW = _fn("FindWindowW", wt.HWND, wt.LPCWSTR, wt.LPCWSTR)
    GetWindowThreadProcessId = _fn("GetWindowThreadProcessId", wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
    GetClassInfoExW = _fn("GetClassInfoExW", wt.BOOL, wt.HINSTANCE, wt.LPCWSTR, ctypes.POINTER(tray.WNDCLASSEXW))
    _ptr64 = ctypes.sizeof(ctypes.c_void_p) == 8
    GetClassLongPtrW = _fn("GetClassLongPtrW" if _ptr64 else "GetClassLongW", ctypes.c_size_t, wt.HWND,
                           ctypes.c_int)
    GetWindowLongPtrW = _fn("GetWindowLongPtrW" if _ptr64 else "GetWindowLongW", ctypes.c_ssize_t, wt.HWND,
                            ctypes.c_int)
    GetMenuItemCount = _fn("GetMenuItemCount", ctypes.c_int, wt.HMENU)
    GetMenuStringW = _fn("GetMenuStringW", ctypes.c_int, wt.HMENU, wt.UINT, wt.LPWSTR, ctypes.c_int, wt.UINT)
    GetMenuState = _fn("GetMenuState", wt.UINT, wt.HMENU, wt.UINT, wt.UINT)
    GetMenuItemID = _fn("GetMenuItemID", wt.UINT, wt.HMENU, ctypes.c_int)
    GetMenuDefaultItem = _fn("GetMenuDefaultItem", wt.UINT, wt.HMENU, wt.UINT, wt.UINT)
    GetSubMenu = _fn("GetSubMenu", wt.HMENU, wt.HMENU, ctypes.c_int)
    DestroyMenu = _fn("DestroyMenu", wt.BOOL, wt.HMENU)

GCLP_WNDPROC = -24
GWL_EXSTYLE = -20
GA_PARENT = 1
MF_BYPOSITION = 0x0400
WM_CANCELMODE = 0x001F


def makelong(lo: int, hi: int) -> int:
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.01):
    """Poll until ``predicate()`` is truthy; returns its last value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


# =================================================================== model ==
def _noop(*_args):
    return None


def test_layout_numbers_commands_in_display_order_and_tidies_separators():
    hidden = MenuItem("Hidden", _noop, visible=False)
    items = [SEPARATOR, MenuItem("A", _noop), SEPARATOR, SEPARATOR, hidden, SEPARATOR, MenuItem("B", _noop),
             SEPARATOR, MenuItem("Gone", _noop, visible=lambda: False), SEPARATOR]
    layout = MenuLayout.build(items)
    assert layout.lines() == ["A", "---", "B"]
    assert sorted(layout.commands) == [1, 2]
    assert [layout.commands[c][0].text for c in (1, 2)] == ["A", "B"]
    assert MenuLayout.build([]).entries == [] and MenuLayout.build([SEPARATOR]).entries == []


def test_plain_item_dispatch():
    calls = []
    layout = MenuLayout.build([MenuItem("Open", lambda: calls.append("open")), MenuItem("Label only")])
    assert layout.dispatch(1) is True and calls == ["open"]
    assert layout.dispatch(2) is False  # no action
    assert layout.dispatch(0) is False and layout.dispatch(99) is False


def test_check_item_with_a_bool_flips_after_the_action():
    seen = []
    item = MenuItem("Pause needle drops", seen.append, checkable=True, checked=False)
    layout = MenuLayout.build([item])
    assert layout.lines() == ["[ ] Pause needle drops"]
    assert layout.dispatch(1) is True
    assert seen == [True] and item.checked is True
    layout = MenuLayout.build([item])
    assert layout.lines() == ["[x] Pause needle drops"]
    layout.dispatch(1)
    assert seen == [True, False] and item.checked is False


def test_check_item_without_action_just_toggles():
    item = MenuItem("Flag", checkable=True)
    assert MenuLayout.build([item]).dispatch(1) is True and item.checked is True


def test_check_item_uses_the_state_shown_when_the_menu_opened():
    """The user clicks what they saw: a state change while the menu was open doesn't flip the intent."""
    state = {"on": False}
    seen = []

    def set_on(value):
        seen.append(value)
        state["on"] = value

    item = MenuItem("Start with Windows", set_on, checkable=True, checked=lambda: state["on"])
    layout = MenuLayout.build([item])  # shown unticked
    state["on"] = True  # changed behind the menu's back
    layout.dispatch(1)
    assert seen == [True]  # still "tick it"
    assert callable(item.checked)  # a callable state is never replaced
    assert MenuLayout.build([item]).lines() == ["[x] Start with Windows"]


def test_failed_action_leaves_a_bool_state_unchanged():
    def boom(_value):
        raise OSError("registry says no")

    item = MenuItem("Start with Windows", boom, checkable=True, checked=False)
    with pytest.raises(OSError):
        MenuLayout.build([item]).dispatch(1)
    assert item.checked is False


def test_disabled_items_are_never_dispatched():
    calls = []
    item = MenuItem("Open", lambda: calls.append(1), enabled=False)
    layout = MenuLayout.build([item])
    assert layout.lines() == ["Open (disabled)"]
    assert layout.dispatch(1) is False and calls == []


def test_callables_are_evaluated_when_the_menu_is_built(caplog):
    count = {"n": 0}

    def text():
        count["n"] += 1
        return f"Status #{count['n']}"

    def broken():
        raise RuntimeError("flag exploded")

    items = [MenuItem(text, enabled=False), MenuItem("Shown", _noop, visible=broken),
             MenuItem("Greyed", _noop, enabled=broken), MenuItem("Check", _noop, checkable=True, checked=broken)]
    with caplog.at_level(logging.ERROR, logger="needledropper.tray"):
        assert MenuLayout.build(items).lines()[0] == "Status #1 (disabled)"
        lines = MenuLayout.build(items).lines()
    assert lines == ["Status #2 (disabled)", "Shown", "Greyed", "[ ] Check"]  # failures fall back to defaults
    assert "flag exploded" in caplog.text


def test_submenus_get_unique_commands():
    calls = []
    items = [MenuItem("Top", lambda: calls.append("top")),
             MenuItem("Sets", submenu=[MenuItem("Classic", lambda: calls.append("classic")), SEPARATOR,
                                       MenuItem("Kojima", lambda: calls.append("kojima"))]),
             MenuItem("Empty", submenu=[MenuItem("x", _noop, visible=False)])]
    layout = MenuLayout.build(items)
    assert layout.lines() == ["Top", "Sets >", "    Classic", "    ---", "    Kojima"]
    assert sorted(layout.commands) == [1, 2, 3]
    layout.dispatch(3)
    layout.dispatch(2)
    assert calls == ["kojima", "classic"]
    assert items[1].activate() is False  # a submenu itself does nothing


def test_menu_item_validation():
    with pytest.raises(TypeError):
        MenuItem(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        MenuItem("x", action="not callable")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        MenuItem("x", submenu=["nope"])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        MenuItem("x", checkable=True, submenu=[])
    with pytest.raises(TypeError):
        MenuLayout.build(["nope"])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        tray.TrayIcon("NeedleDropper", None, ["nope"])  # type: ignore[list-item]


def test_companion_menu_layout_and_ids():
    state = {"paused": False, "autostart": True}
    calls = []
    items = tray.companion_menu(
        on_open_setup=lambda: calls.append("setup"),
        on_pause=lambda v: (calls.append(("pause", v)), state.__setitem__("paused", v)),
        paused=lambda: state["paused"],
        on_autostart=lambda v: calls.append(("autostart", v)),
        autostart=lambda: state["autostart"],
        on_open_log_folder=lambda: calls.append("logs"),
        on_quit=lambda: calls.append("quit"),
    )
    layout = MenuLayout.build(items)
    assert layout.lines() == ["Open setup\u2026 (default)", "---", "[ ] Pause needle drops",
                              "[x] Start with Windows", "---", "Open log folder", "---", "Quit"]
    ids = {item.id: command for command, (item, _shown, _enabled) in layout.commands.items()}
    assert set(ids) == {tray.ITEM_SETUP, tray.ITEM_PAUSE, tray.ITEM_AUTOSTART, tray.ITEM_LOG_FOLDER, tray.ITEM_QUIT}
    assert layout.default_command == ids[tray.ITEM_SETUP]
    for key in (tray.ITEM_SETUP, tray.ITEM_PAUSE, tray.ITEM_AUTOSTART, tray.ITEM_LOG_FOLDER, tray.ITEM_QUIT):
        assert layout.dispatch(ids[key]) is True
    assert calls == ["setup", ("pause", True), ("autostart", False), "logs", "quit"]
    assert MenuLayout.build(items).lines()[2] == "[x] Pause needle drops"
    assert tray.default_item(items) is tray.find_item(items, tray.ITEM_SETUP)


def test_companion_menu_leaves_out_missing_callbacks_and_shows_status():
    items = tray.companion_menu(on_quit=_noop, status=lambda: "Connected to Spotify")
    assert MenuLayout.build(items).lines() == ["Connected to Spotify (disabled)", "---", "Quit"]
    assert tray.default_item(items) is None
    assert tray.companion_menu() == []
    only_pause = tray.companion_menu(on_pause=_noop, paused=True)
    assert MenuLayout.build(only_pause).lines() == ["[x] Pause needle drops"]


def test_default_item_skips_hidden_and_disabled():
    a = MenuItem("A", _noop, default=True, enabled=False)
    b = MenuItem("B", _noop, default=True, visible=False)
    c = MenuItem("C", _noop, default=True)
    assert tray.default_item([a, b, SEPARATOR, c]) is c
    assert tray.default_item([a, b]) is None


# ==================================================================== text ==
def test_fit_text_limits_utf16_units_and_keeps_pairs_whole():
    assert tray.fit_text("NeedleDropper", tray.TOOLTIP_MAX) == "NeedleDropper"
    assert tray.fit_text(None, 10) == "" and tray.fit_text(12, 10) == "12"
    long = "x" * 300
    fitted = tray.fit_text(long, tray.TOOLTIP_MAX)
    assert len(fitted) == tray.TOOLTIP_MAX and fitted.endswith("\u2026")
    assert tray.fit_text("a\x00b", 10) == "ab"
    exact = "y" * tray.TOOLTIP_MAX
    assert tray.fit_text(exact, tray.TOOLTIP_MAX) == exact
    # "\U0001F3B5" (a musical note) takes two UTF-16 units; never split it
    notes = "\U0001F3B5" * 100
    fitted = tray.fit_text(notes, 9)
    assert fitted == "\U0001F3B5" * 4 + "\u2026"
    assert len(fitted.encode("utf-16-le")) // 2 <= 9
    assert tray.fit_text("abc", 0) == ""


# ======================================================== other platforms ==
def test_imports_and_refuses_to_start_off_windows():
    code = (
        "import sys; sys.platform = 'linux'; sys.path.insert(0, sys.argv[1])\n"
        "from needledropper import tray as t\n"
        "assert not t.IS_WINDOWS and t.PROTOTYPES == {} and not hasattr(t, 'NOTIFYICONDATAW')\n"
        "icon = t.TrayIcon('NeedleDropper', None, t.companion_menu(on_quit=lambda: None))\n"
        "icon.set_tooltip('waiting'); icon.set_icon(t.PAUSED_ICON_PATH)\n"
        "assert icon.tooltip == 'waiting' and icon.icon_path == t.PAUSED_ICON_PATH\n"
        "assert icon.notify('a', 'b') is False and icon.show_menu() is False\n"
        "assert not icon.running and not icon.visible\n"
        "try:\n"
        "    icon.start()\n"
        "except RuntimeError as e:\n"
        "    assert 'requires Windows' in str(e), e\n"
        "else:\n"
        "    raise SystemExit('start did not raise')\n"
        "icon.stop()\n"
        "layout = t.MenuLayout.build(icon.menu_items)\n"
        "assert layout.lines() == ['Quit'] and layout.dispatch(1)\n"
        "try:\n"
        "    t.build_hmenu(layout.entries)\n"
        "except RuntimeError as e:\n"
        "    assert 'requires Windows' in str(e), e\n"
        "else:\n"
        "    raise SystemExit('build_hmenu did not raise')\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code, str(ROOT / "companion")], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_constructor_validation():
    with pytest.raises(ValueError):
        tray.TrayIcon("  ")
    with pytest.raises(ValueError):
        tray.TrayIcon("NeedleDropper", icon_id=70000)
    with pytest.raises(TypeError):
        tray.TrayIcon("NeedleDropper", on_click="nope")  # type: ignore[arg-type]
    icon = tray.TrayIcon("NeedleDropper")
    assert icon.icon_path == tray.ICON_PATH and icon.tooltip == "NeedleDropper"
    assert tray.default_icon_path() == tray.ICON_PATH and tray.default_icon_path(paused=True) == tray.PAUSED_ICON_PATH


# ============================================================ icon assets ==
def _load_make_icon():
    spec = importlib.util.spec_from_file_location("nd_tray_make_icon", ASSETS / "make_icon.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # deliberately not registered in sys.modules: must still load
    return mod


def parse_ico(data: bytes) -> list[tuple[int, int, bytes]]:
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1) and count > 0
    images = []
    for i in range(count):
        w, h, colors, res, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        w, h = w or 256, h or 256
        assert (colors, res, planes, bpp) == (0, 0, 1, 32)
        blob = data[offset:offset + size]
        assert len(blob) == size
        images.append((w, h, blob))
    return images


def test_tray_icons_are_well_formed_32bit_icons():
    assert tray.ICON_PATH == ASSETS / "NeedleDropper.ico"
    assert tray.PAUSED_ICON_PATH == ASSETS / "NeedleDropper-paused.ico"
    for path in (tray.ICON_PATH, tray.PAUSED_ICON_PATH):
        images = parse_ico(path.read_bytes())
        sizes = [w for w, _h, _b in images]
        assert {16, 32, 48} <= set(sizes)
        assert sizes == [16, 20, 24, 32, 40, 48, 64]  # every SM_CXSMICON size up to 300 %
        for w, h, blob in images:
            assert w == h
            hdr = struct.unpack_from("<IiiHHI", blob, 0)
            assert hdr == (40, w, 2 * h, 1, 32, 0)  # BITMAPINFOHEADER, XOR + AND height, BI_RGB
            mask_stride = ((w + 31) // 32) * 4
            assert len(blob) == 40 + w * h * 4 + mask_stride * h
            alphas = blob[40 + 3:40 + w * h * 4:4]
            assert 0 in alphas and 255 in alphas  # transparent corners, opaque record
    assert tray.ICON_PATH.read_bytes() != tray.PAUSED_ICON_PATH.read_bytes()


def test_tray_icons_are_reproducible_from_make_icon():
    mod = _load_make_icon()
    for variant, name in mod.FILENAMES.items():
        assert (ASSETS / name).read_bytes() == mod.build_ico(variant), (
            f"{name} is stale: run python companion/needledropper/assets/make_icon.py")


def test_make_icon_check_mode_spots_a_stale_file(tmp_path, capsys):
    mod = _load_make_icon()
    for name in mod.FILENAMES.values():
        shutil.copy(ASSETS / name, tmp_path / name)
    paused = tmp_path / mod.FILENAMES["paused"]
    data = bytearray(paused.read_bytes())
    data[-100] ^= 0xFF
    paused.write_bytes(bytes(data))
    assert mod.main(["--check", "--out-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "NeedleDropper-paused.ico" in out and f"stale: {tmp_path / 'NeedleDropper.ico'}" not in out
    with pytest.raises(ValueError):
        mod.Glyph(16, "spinning")


@windows_only
@pytest.mark.parametrize("size", [16, 20, 24, 32, 48, 64])
def test_windows_loads_the_icons_at_every_size(size):
    for path in (tray.ICON_PATH, tray.PAUSED_ICON_PATH):
        handle = tray._LoadImageW(None, str(path), tray.IMAGE_ICON, size, size, tray.LR_LOADFROMFILE)
        assert handle, f"LoadImageW({path.name}, {size}) failed: {ctypes.get_last_error()}"
        assert tray._DestroyIcon(handle)


# ======================================================== Win32 structures ==
@windows_only
def test_struct_sizes_match_the_documented_layout():
    ptr64 = struct.calcsize("P") == 8
    nid = tray.NOTIFYICONDATAW
    assert ctypes.sizeof(nid) == (976 if ptr64 else 956) == tray.NOTIFYICONDATAW_SIZE
    offsets = {
        "cbSize": (0, 0), "hWnd": (8, 4), "uID": (16, 8), "uFlags": (20, 12), "uCallbackMessage": (24, 16),
        "hIcon": (32, 20), "szTip": (40, 24), "dwState": (296, 280), "dwStateMask": (300, 284),
        "szInfo": (304, 288), "uTimeout": (816, 800), "uVersion": (816, 800), "szInfoTitle": (820, 804),
        "dwInfoFlags": (948, 932), "guidItem": (952, 936), "hBalloonIcon": (968, 952),
    }
    for name, (off64, off32) in offsets.items():
        assert getattr(nid, name).offset == (off64 if ptr64 else off32), name
    # shellapi.h: NOTIFYICONDATAW_V1_SIZE = FIELD_OFFSET(szTip[64]), V2 = FIELD_OFFSET(guidItem),
    # V3 = FIELD_OFFSET(hBalloonIcon)
    assert nid.szTip.offset + 64 * 2 == (168 if ptr64 else 152)
    assert nid.guidItem.offset == (952 if ptr64 else 936)
    assert nid.hBalloonIcon.offset == (968 if ptr64 else 952)
    assert nid.szTip.size == 128 * 2 and nid.szInfo.size == 256 * 2 and nid.szInfoTitle.size == 64 * 2
    assert ctypes.sizeof(tray.GUID) == 16
    assert ctypes.sizeof(tray.WNDCLASSEXW) == (80 if ptr64 else 48)
    assert ctypes.sizeof(tray.MSG) == (48 if ptr64 else 28)
    pointer = ctypes.sizeof(ctypes.c_void_p)
    for t in (wt.HWND, wt.HICON, wt.HMENU, wt.HINSTANCE, wt.WPARAM, wt.LPARAM, tray.LRESULT, tray.UINT_PTR):
        assert ctypes.sizeof(t) == pointer, t


@windows_only
def test_every_win32_prototype_is_explicit_and_pointer_sized():
    protos = tray.PROTOTYPES
    expected = {"RegisterClassExW", "UnregisterClassW", "CreateWindowExW", "DestroyWindow", "DefWindowProcW",
                "GetMessageW", "TranslateMessage", "DispatchMessageW", "PostMessageW", "PostQuitMessage",
                "RegisterWindowMessageW", "SetTimer", "KillTimer", "CreatePopupMenu", "AppendMenuW",
                "SetMenuDefaultItem", "TrackPopupMenu", "EndMenu", "DestroyMenu", "SetForegroundWindow",
                "GetCursorPos", "GetSystemMetrics", "LoadImageW", "LoadIconW", "DestroyIcon", "Shell_NotifyIconW",
                "ExtractIconExW", "GetModuleHandleW", "IsWindow", "GetDoubleClickTime"}
    assert expected <= set(protos)
    for name, fn in protos.items():
        assert isinstance(fn.argtypes, (list, tuple)), name
    pointer = ctypes.sizeof(ctypes.c_void_p)
    for name in ("DefWindowProcW", "PostMessageW"):
        assert [ctypes.sizeof(t) for t in protos[name].argtypes] == [pointer, 4, pointer, pointer], name
    assert ctypes.sizeof(protos["DefWindowProcW"].restype) == pointer
    assert ctypes.sizeof(protos["DispatchMessageW"].restype) == pointer
    for name, restype in (("CreateWindowExW", wt.HWND), ("CreatePopupMenu", wt.HMENU), ("LoadImageW", wt.HANDLE),
                          ("LoadIconW", wt.HICON), ("GetModuleHandleW", wt.HMODULE), ("SetTimer", tray.UINT_PTR)):
        assert protos[name].restype is restype, name
    assert protos["AppendMenuW"].argtypes[2] is tray.UINT_PTR  # an id or an HMENU
    assert protos["Shell_NotifyIconW"].argtypes[1]._type_ is tray.NOTIFYICONDATAW
    assert protos["PostQuitMessage"].restype is None
    assert tray.WNDPROC._restype_ is tray.LRESULT
    assert tray.WNDPROC._argtypes_ == (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


@windows_only
def test_build_hmenu_renders_text_checks_grey_default_and_submenus():
    items = [MenuItem("Connected", enabled=False), SEPARATOR, MenuItem("&Open setup\u2026", _noop, default=True),
             MenuItem("Pause needle drops", _noop, checkable=True, checked=True),
             MenuItem("Sets", submenu=[MenuItem("Classic", _noop), MenuItem("Kojima", _noop)]),
             SEPARATOR, MenuItem("Quit", _noop)]
    layout = MenuLayout.build(items)
    hmenu = tray.build_hmenu(layout.entries)
    try:
        assert GetMenuItemCount(hmenu) == 7

        def text(menu, pos):
            buf = ctypes.create_unicode_buffer(256)
            GetMenuStringW(menu, pos, buf, 256, MF_BYPOSITION)
            return buf.value

        assert [text(hmenu, i) for i in range(7)] == ["Connected", "", "&Open setup\u2026", "Pause needle drops",
                                                      "Sets", "", "Quit"]
        states = [GetMenuState(hmenu, i, MF_BYPOSITION) for i in range(7)]
        assert states[0] & tray.MF_GRAYED
        assert states[1] & tray.MF_SEPARATOR and states[5] & tray.MF_SEPARATOR
        assert states[3] & tray.MF_CHECKED and not states[2] & tray.MF_CHECKED
        assert states[4] & tray.MF_POPUP
        ids = [GetMenuItemID(hmenu, i) for i in (0, 2, 3, 6)]
        assert ids == [1, 2, 3, 6]
        assert GetMenuDefaultItem(hmenu, 0, 0) == layout.default_command == 2
        sub = GetSubMenu(hmenu, 4)
        assert sub and GetMenuItemCount(sub) == 2
        assert [GetMenuItemID(sub, i) for i in range(2)] == [4, 5] and text(sub, 1) == "Kojima"
    finally:
        assert DestroyMenu(hmenu)  # also destroys the submenu


# ============================================== tray thread, fake shell ==
class FakeShell:
    """Stands in for Shell_NotifyIconW: records every call and simulates the shell's icon table."""

    def __init__(self, fail_adds: int = 0, refuse_version: bool = False) -> None:
        self.calls: list[dict] = []
        self.icons: dict[tuple, dict] = {}
        self.fail_adds = fail_adds
        self.refuse_version = refuse_version
        self.threads: set[str] = set()
        self.cond = threading.Condition()

    def __call__(self, message: int, nid) -> bool:
        record = {"message": message, "flags": nid.uFlags, "cbsize": nid.cbSize, "hwnd": nid.hWnd, "uid": nid.uID,
                  "callback": nid.uCallbackMessage, "hicon": nid.hIcon, "tip": nid.szTip, "title": nid.szInfoTitle,
                  "info": nid.szInfo, "info_flags": nid.dwInfoFlags, "version": nid.uVersion,
                  "balloon_icon": nid.hBalloonIcon}
        key = (nid.hWnd, nid.uID)
        with self.cond:
            self.threads.add(threading.current_thread().name)
            if message == tray.NIM_ADD:
                if self.fail_adds > 0:
                    self.fail_adds -= 1
                    ok = False
                elif key in self.icons:
                    ok = False  # the real shell refuses a duplicate
                else:
                    self.icons[key] = record
                    ok = True
            elif message == tray.NIM_MODIFY:
                ok = key in self.icons
            elif message == tray.NIM_DELETE:
                ok = self.icons.pop(key, None) is not None
            elif message == tray.NIM_SETVERSION:
                ok = key in self.icons and not self.refuse_version
            else:
                ok = False
            record["ok"] = ok
            self.calls.append(record)
            self.cond.notify_all()
        return ok

    def of(self, message: int) -> list[dict]:
        with self.cond:
            return [c for c in self.calls if c["message"] == message]

    def wait_for(self, predicate, timeout: float = 5.0) -> bool:
        with self.cond:
            return self.cond.wait_for(lambda: any(predicate(c) for c in self.calls), timeout)


@pytest.fixture
def make_icon():
    """Build TrayIcons with a fake shell (and no DPI change); stops them all afterwards."""
    icons = []

    def factory(*args, shell=None, **kwargs):
        shell = shell or FakeShell()
        kwargs.setdefault("dpi_aware", False)
        if not args:
            args = ("NeedleDropper test",)
        icon = tray.TrayIcon(*args, shell_notify=shell, **kwargs)
        icons.append(icon)
        return icon, shell

    yield factory
    for icon in icons:
        icon.stop()


def select(icon, x: int = 100, y: int = 200) -> None:
    """Simulate a NOTIFYICON_VERSION_4 left click on the icon."""
    SendMessageW(icon.hwnd, tray.WM_TRAY_CALLBACK, makelong(x, y), makelong(tray.NIN_SELECT, icon.icon_id))


@windows_only
def test_tray_lifecycle_with_a_fake_shell(make_icon):
    clicks = []
    icon, shell = make_icon("NeedleDropper test", tray.ICON_PATH, tray.companion_menu(on_quit=_noop),
                            on_click=lambda: clicks.append(threading.current_thread().name),
                            tooltip="NeedleDropper - starting", icon_id=7)
    assert icon.start() is icon
    assert icon.start() is icon  # idempotent
    assert icon.wait_visible(5) and icon.running and icon.uses_version_4
    hwnd = icon.hwnd
    add = shell.of(tray.NIM_ADD)[0]
    assert add["flags"] == tray.NIF_MESSAGE | tray.NIF_ICON | tray.NIF_TIP | tray.NIF_SHOWTIP
    assert add["cbsize"] == tray.NOTIFYICONDATAW_SIZE
    assert (add["hwnd"], add["uid"], add["callback"]) == (hwnd, 7, tray.WM_TRAY_CALLBACK)
    assert add["hicon"] and add["tip"] == "NeedleDropper - starting"
    version = shell.of(tray.NIM_SETVERSION)[0]
    assert version["version"] == tray.NOTIFYICON_VERSION_4 and version["ok"]
    assert shell.threads == {"NeedleDropper-tray"}  # every shell call happens on the tray thread

    # the window: hidden, top-level (receives broadcasts), a tool window (no taskbar button)
    assert not IsWindowVisible(hwnd)
    assert GetAncestor(hwnd, GA_PARENT) == GetDesktopWindow()
    assert GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & tray.WS_EX_TOOLWINDOW

    # tooltip updates from another thread (rapid ones collapse; the last one wins)
    for i in range(25):
        icon.set_tooltip(f"NeedleDropper - tip {i}")
    assert shell.wait_for(lambda c: c["message"] == tray.NIM_MODIFY and c["tip"] == "NeedleDropper - tip 24")
    tip_calls = [c for c in shell.of(tray.NIM_MODIFY) if c["flags"] == tray.NIF_TIP | tray.NIF_SHOWTIP]
    assert 1 <= len(tip_calls) <= 25
    icon.set_tooltip("x" * 500)
    assert shell.wait_for(lambda c: c["tip"] == "x" * 126 + "\u2026")

    # notifications
    assert icon.notify("Needle drop", "ELWYNN FOREST \u2014 Low Roar", kind="warning", sound=False,
                       realtime=True)
    assert shell.wait_for(lambda c: c["flags"] & tray.NIF_INFO and c["title"] == "Needle drop")
    info = [c for c in shell.calls if c["flags"] & tray.NIF_INFO][0]
    assert info["message"] == tray.NIM_MODIFY and info["flags"] == tray.NIF_INFO | tray.NIF_REALTIME
    assert info["info"] == "ELWYNN FOREST \u2014 Low Roar"
    assert info["info_flags"] == tray.NIIF_WARNING | tray.NIIF_NOSOUND | tray.NIIF_RESPECT_QUIET_TIME
    assert icon.notify("App", "", kind="app", respect_quiet_time=False)
    assert shell.wait_for(lambda c: c["title"] == "App")
    app = [c for c in shell.calls if c["title"] == "App"][0]
    assert app["info"] == " "  # an empty text would hide the balloon
    assert app["info_flags"] == tray.NIIF_USER | tray.NIIF_LARGE_ICON and app["balloon_icon"]
    with pytest.raises(ValueError):
        icon.notify("x", "y", kind="loud")

    # icon swap
    old_icon = add["hicon"]
    icon.set_icon(tray.PAUSED_ICON_PATH)
    assert shell.wait_for(lambda c: c["message"] == tray.NIM_MODIFY and c["flags"] == tray.NIF_ICON)
    swapped = [c for c in shell.calls if c["flags"] == tray.NIF_ICON][-1]
    assert swapped["hicon"] and swapped["hicon"] != old_icon and icon.icon_path == tray.PAUSED_ICON_PATH

    # clicks: the callback runs on the actions thread; a double-click acts once
    select(icon)
    assert wait_until(lambda: clicks == ["NeedleDropper-tray-actions"])
    select(icon)
    time.sleep(0.1)
    assert len(clicks) == 1
    icon._double_click_s = 0.05
    time.sleep(0.1)
    SendMessageW(hwnd, tray.WM_TRAY_CALLBACK, 0, makelong(tray.NIN_KEYSELECT, 7))  # Enter on the icon
    assert wait_until(lambda: len(clicks) == 2)
    # events for another icon id, and mouse messages (handled via NIN_SELECT in version 4), are ignored
    time.sleep(0.1)
    SendMessageW(hwnd, tray.WM_TRAY_CALLBACK, 0, makelong(tray.NIN_SELECT, 8))
    SendMessageW(hwnd, tray.WM_TRAY_CALLBACK, 0, makelong(tray.WM_LBUTTONUP, 7))
    time.sleep(0.2)
    assert len(clicks) == 2

    icon.stop()
    assert not icon.running and not icon.visible and icon.hwnd is None
    assert shell.of(tray.NIM_DELETE) and shell.icons == {}
    assert not IsWindow(hwnd)
    assert icon.notify("late", "ignored") is False
    icon.stop()  # idempotent


@windows_only
def test_left_click_without_on_click_runs_the_default_item(make_icon):
    opened = threading.Event()
    icon, _shell = make_icon("NeedleDropper test", None, tray.companion_menu(on_open_setup=opened.set,
                                                                             on_quit=_noop))
    icon.start()
    assert icon.wait_visible(5)
    select(icon)
    assert opened.wait(5)


@windows_only
def test_notification_clicks_and_callback_errors(make_icon, caplog):
    clicked = threading.Event()
    calls = []

    def boom():
        calls.append("boom")
        raise RuntimeError("callback exploded")

    icon, _shell = make_icon(on_click=boom, on_notification_click=clicked.set)
    icon.start()
    assert icon.wait_visible(5)
    with caplog.at_level(logging.ERROR, logger="needledropper.tray"):
        select(icon)
        assert wait_until(lambda: "callback exploded" in caplog.text)
    SendMessageW(icon.hwnd, tray.WM_TRAY_CALLBACK, 0, makelong(tray.NIN_BALLOONUSERCLICK, icon.icon_id))
    assert clicked.wait(5)  # the actions thread survived the exception
    assert icon.running and calls == ["boom"]


@windows_only
def test_taskbar_created_adds_the_icon_again(make_icon):
    icon, shell = make_icon(tooltip="before")
    icon.start()
    assert icon.wait_visible(5)
    taskbar_created = RegisterWindowMessageW("TaskbarCreated")
    assert taskbar_created
    icon.set_tooltip("after")
    shell.wait_for(lambda c: c["tip"] == "after")
    with shell.cond:
        shell.icons.clear()  # Explorer restarted: the new shell knows no icons
    SendMessageW(icon.hwnd, taskbar_created, 0, 0)
    assert icon.visible
    adds = shell.of(tray.NIM_ADD)
    assert len(adds) == 2 and adds[-1]["ok"] and adds[-1]["tip"] == "after"
    assert len(shell.of(tray.NIM_SETVERSION)) == 2 and icon.uses_version_4
    # the same broadcast while the icon still exists (e.g. a DPI change): NIM_MODIFY takes over
    SendMessageW(icon.hwnd, taskbar_created, 0, 0)
    assert icon.visible and not shell.of(tray.NIM_ADD)[-1]["ok"] and shell.of(tray.NIM_MODIFY)[-1]["ok"]


@windows_only
def test_adding_is_retried_until_the_shell_accepts(make_icon, caplog):
    shell = FakeShell(fail_adds=3)
    icon, _ = make_icon(shell=shell)
    icon.retry_delays_ms = (20, 40)
    with caplog.at_level(logging.WARNING, logger="needledropper.tray"):
        icon.start()
        assert icon.wait_visible(5)
    adds = shell.of(tray.NIM_ADD)
    assert [a["ok"] for a in adds] == [False, False, False, True]
    assert "retrying" in caplog.text
    # notifications while the icon is missing are dropped, not queued forever
    with shell.cond:
        shell.icons.clear()
    icon._visible = False
    assert icon.notify("lost", "dropped")
    time.sleep(0.2)
    assert not [c for c in shell.calls if c["title"] == "lost"]


@windows_only
def test_legacy_callbacks_when_version_4_is_refused(make_icon):
    clicks = []
    shell = FakeShell(refuse_version=True)
    icon, _ = make_icon(shell=shell, on_click=lambda: clicks.append(1), icon_id=3)
    icon.start()
    assert icon.wait_visible(5) and not icon.uses_version_4
    SendMessageW(icon.hwnd, tray.WM_TRAY_CALLBACK, 3, tray.WM_LBUTTONUP)  # wParam = uID, lParam = message
    assert wait_until(lambda: clicks == [1])
    SendMessageW(icon.hwnd, tray.WM_TRAY_CALLBACK, 4, tray.WM_LBUTTONUP)  # another icon
    time.sleep(0.1)
    assert clicks == [1]


@windows_only
def test_stop_from_a_callback_does_not_deadlock(make_icon):
    holder = {}
    icon, shell = make_icon(on_click=lambda: holder["icon"].stop())
    holder["icon"] = icon
    icon.start()
    assert icon.wait_visible(5)
    select(icon)
    assert icon.join(5)
    assert not icon.running and shell.of(tray.NIM_DELETE)


@windows_only
def test_wm_close_from_outside_calls_on_close_only(make_icon):
    closed = threading.Event()
    icon, _ = make_icon(on_close=closed.set)
    icon.start()
    assert icon.wait_visible(5)
    PostMessageW(icon.hwnd, tray.WM_CLOSE, 0, 0)
    assert closed.wait(5)
    assert icon.running and icon.visible  # the app decides whether to quit
    other, _ = make_icon()  # no on_close: WM_CLOSE is ignored
    other.start()
    assert other.wait_visible(5)
    SendMessageW(other.hwnd, tray.WM_CLOSE, 0, 0)
    assert other.running and other.visible


@windows_only
def test_restart_after_stop(make_icon):
    icon, shell = make_icon()
    icon.start()
    assert icon.wait_visible(5)
    first = icon.hwnd
    icon.stop()
    icon.start()
    assert icon.wait_visible(5)
    assert icon.hwnd and icon.hwnd != first
    assert [c["ok"] for c in shell.of(tray.NIM_ADD)] == [True, True]


@windows_only
def test_missing_icon_file_falls_back_to_the_executable_icon(make_icon, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="needledropper.tray"):
        icon, shell = make_icon("NeedleDropper test", tmp_path / "missing.ico")
        icon.start()
        assert icon.wait_visible(5)
    assert shell.of(tray.NIM_ADD)[0]["hicon"]  # python.exe's (or NeedleDropper.exe's) icon
    assert "icon file not found" in caplog.text


@windows_only
def test_wndproc_stays_alive_while_running_and_is_released_after_stop(make_icon):
    clicks = []
    icon, _shell = make_icon(on_click=lambda: clicks.append(1))
    icon.start()
    assert icon.wait_visible(5)
    hwnd, class_name, hinstance = icon.hwnd, icon._class_name, icon._hinstance
    proc = icon._wndproc
    address = ctypes.cast(proc, ctypes.c_void_p).value
    assert address and GetClassLongPtrW(hwnd, GCLP_WNDPROC) == address  # the class calls exactly this object
    proc_ref, icon_ref = weakref.ref(proc), weakref.ref(icon)
    del proc, icon
    gc.collect()
    # Nothing in the test holds the icon or the callback any more, yet both are alive: the
    # tray thread owns them, and the window procedure still works after a full collection.
    assert proc_ref() is not None and icon_ref() is not None
    SendMessageW(hwnd, tray.WM_TRAY_CALLBACK, 0, makelong(tray.NIN_SELECT, 1))
    assert wait_until(lambda: clicks == [1])
    assert GetClassLongPtrW(hwnd, GCLP_WNDPROC) == address
    icon = icon_ref()
    icon.stop()
    assert icon._wndproc is None and not IsWindow(hwnd)
    wc = tray.WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(tray.WNDCLASSEXW)
    assert not GetClassInfoExW(hinstance, class_name, ctypes.byref(wc))  # class unregistered
    gc.collect()
    assert proc_ref() is None  # released only once nothing can call it


@windows_only
def test_stop_while_the_menu_is_open_ends_the_menu_first(make_icon):
    """WM_TRAY_STOP arriving inside TrackPopupMenu's modal loop: the window is destroyed after it."""
    icon, shell = make_icon(menu_items=[MenuItem("Quit", _noop)])
    icon.start()
    assert icon.wait_visible(5)
    icon._menu_open = True  # as if TrackPopupMenu were running
    SendMessageW(icon.hwnd, tray.WM_TRAY_STOP, 0, 0)
    assert icon._stop_after_menu is True and IsWindow(icon.hwnd)
    icon._menu_open = False
    icon._stop_after_menu = False
    icon.stop()
    assert not icon.running and shell.of(tray.NIM_DELETE)


@windows_only
def test_start_reports_window_creation_failures(make_icon, monkeypatch):
    icon, _ = make_icon()

    def fail():
        raise RuntimeError("CreateWindowExW failed (Win32 error 8: Not enough memory)")

    monkeypatch.setattr(icon, "_create_window", fail)
    with pytest.raises(RuntimeError, match="could not create the tray icon"):
        icon.start()
    assert not icon.running


# ==================================================================== live ==
def _our_popup_menu():
    """The visible popup-menu window (class ``#32768``) of this process, or None."""
    menu = FindWindowW("#32768", None)
    if not menu or not IsWindowVisible(menu):
        return None
    pid = wt.DWORD()
    GetWindowThreadProcessId(menu, ctypes.byref(pid))
    return menu if pid.value == os.getpid() else None


@live_only
def test_live_tray_icon_in_the_notification_area():
    """Shows the real icon briefly: tooltip change, a silent notification, the popup menu."""
    icon = tray.TrayIcon(f"NeedleDropper live test {os.getpid()}", tray.ICON_PATH,
                         tray.companion_menu(on_open_setup=_noop, on_quit=_noop),
                         tooltip="NeedleDropper live test: starting")
    hwnd = None
    try:
        try:
            icon.start()
        except RuntimeError as exc:
            pytest.skip(f"no interactive desktop: {exc}")
        if not icon.wait_visible(10):
            pytest.skip("the shell did not show the icon (no notification area in this session?)")
        hwnd = icon.hwnd
        assert icon.uses_version_4
        icon.set_tooltip("NeedleDropper live test: tooltip updated")
        assert icon.notify("NeedleDropper live test", "Tray notifications work. (This was a test.)",
                           sound=False, realtime=True)
        time.sleep(1.5)
        icon.set_icon(tray.PAUSED_ICON_PATH)
        time.sleep(0.5)
        # Explorer-restart handling against the real shell (the icon still exists, so NIM_MODIFY is used)
        SendMessageW(hwnd, RegisterWindowMessageW("TaskbarCreated"), 0, 0)
        assert icon.visible
        # the popup menu opens and closes again; the tray keeps working afterwards
        assert icon.show_menu()
        assert wait_until(_our_popup_menu, timeout=3), "the popup menu did not appear"
        PostMessageW(hwnd, WM_CANCELMODE, 0, 0)  # DefWindowProc ends the menu loop
        assert wait_until(lambda: not icon._menu_open and not _our_popup_menu(), timeout=3)
        icon.set_tooltip("NeedleDropper live test: done")
        time.sleep(0.3)
        assert icon.running and icon.visible
        # stop() while the menu is open (EndMenu path)
        assert icon.show_menu()
        assert wait_until(_our_popup_menu, timeout=3)
    finally:
        icon.stop()
    assert not icon.running and not icon.visible
    assert not _our_popup_menu()
    if hwnd:
        nid = tray.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(tray.NOTIFYICONDATAW)
        nid.hWnd, nid.uID = hwnd, icon.icon_id
        assert not tray._Shell_NotifyIconW(tray.NIM_MODIFY, ctypes.byref(nid))  # the shell forgot it


# ------------------------------------------------------------ notifications --
class _RecordingIcon:
    def __init__(self, accept: bool = True) -> None:
        self.notes: list = []
        self.accept = accept

    def notify(self, title, text, *, kind="info"):
        self.notes.append((title, text, kind))
        return self.accept


def test_notifier_policies_once_per_session_and_cooldowns():
    now = [100.0]
    icon = _RecordingIcon()
    notes = tray.Notifier(icon, clock=lambda: now[0])
    assert notes.notify(tray.NOTE_LINKED, "Linked") is True
    assert notes.notify(tray.NOTE_LINKED, "Linked again") is False  # once per session
    assert notes.notify(tray.NOTE_TEST, "Test 1") is True
    now[0] += 5
    assert notes.notify(tray.NOTE_TEST, "Test 2") is False  # both transports, a repeated frame
    now[0] += tray.NOTE_POLICIES[tray.NOTE_TEST].cooldown_s
    assert notes.notify(tray.NOTE_TEST, "Test 3") is True
    assert notes.notify(tray.NOTE_NOT_FOUND, "a", key="q:toto\x1fafrica") is True
    assert notes.notify(tray.NOTE_NOT_FOUND, "b", key="q:other") is True  # per song
    assert notes.notify(tray.NOTE_NOT_FOUND, "c", key="q:toto\x1fafrica") is False
    assert [t for (_h, t, _k) in icon.notes] == ["Linked", "Test 1", "Test 3", "a", "b"]
    assert all(h == "NeedleDropper" for (h, _t, _k) in icon.notes)
    assert notes.kinds() == ["linked", "test", "test", "spotify-not-found", "spotify-not-found"]


def test_notifier_reset_clicks_titles_and_levels():
    icon = _RecordingIcon()
    notes = tray.Notifier(icon)
    clicked: list = []
    notes.notify(tray.NOTE_SPOTIFY_EXPIRED, "expired", title="Spotify sign-in expired", level="warning",
                 action=lambda: clicked.append("setup"))
    assert icon.notes[-1] == ("Spotify sign-in expired", "expired", "warning")
    assert notes.notify(tray.NOTE_SPOTIFY_EXPIRED, "again") is False
    notes.reset(tray.NOTE_SPOTIFY_EXPIRED)  # signed in again: a later expiry is news again
    assert notes.notify(tray.NOTE_SPOTIFY_EXPIRED, "again", action=lambda: clicked.append("second")) is True
    notes.clicked()
    assert clicked == ["second"]  # the most recent notification's action
    notes.notify(tray.NOTE_TEST, "no action")
    notes.clicked()
    assert clicked == ["second"]
    notes.notify(tray.NOTE_FAULT, "odd level", level="purple")
    assert icon.notes[-1][2] == "info"


def test_notifier_without_an_icon_only_logs(caplog):
    notes = tray.Notifier()
    with caplog.at_level(logging.INFO, logger="needledropper.tray"):
        assert notes.notify(tray.NOTE_UPDATE, "NeedleDropper 9 is available", key="9") is True
    assert "NeedleDropper 9 is available" in caplog.text and notes.history[-1].shown is False

    class Broken:
        def notify(self, *a, **k):
            raise OSError("shell gone")

    notes.attach(Broken())
    with caplog.at_level(logging.ERROR, logger="needledropper.tray"):
        assert notes.notify(tray.NOTE_LINKED, "still fine") is True
    assert notes.history[-1].shown is False


def test_message_box_falls_back_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(tray, "IS_WINDOWS", False)
    assert tray.message_box("NeedleDropper could not start", "Port 8322 is busy.") is False
    assert "NeedleDropper could not start: Port 8322 is busy." in capsys.readouterr().err
    monkeypatch.setattr(sys, "stderr", None)
    assert tray.message_box("t", "x") is False  # no console at all: nothing to write to, no crash
