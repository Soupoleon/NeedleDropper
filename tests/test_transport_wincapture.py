"""Win32 capture helpers: import safety everywhere, read-only smoke tests on Windows."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper.transport import wincapture  # noqa: E402

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 API")


def test_imports_safely_when_not_windows():
    """With sys.platform faked, the module still imports and every call raises RuntimeError."""
    code = (
        "import sys; sys.platform = 'linux'; sys.path.insert(0, sys.argv[1])\n"
        "from needledropper.transport import wincapture as w\n"
        "assert not w.IS_WINDOWS\n"
        "for fn, args in [(w.set_dpi_awareness, ()), (w.find_game_window, (None,)), (w.client_rect, (1,)),\n"
        "                 (w.client_origin, (1,)), (w.capture_screen_region, (0, 0, 8, 8)),\n"
        "                 (w.capture_window_region, (1, 8, 8)), (w.list_game_windows, ()),\n"
        "                 (w.describe_window, (1,))]:\n"
        "    try:\n"
        "        fn(*args)\n"
        "    except RuntimeError as e:\n"
        "        assert 'requires Windows' in str(e), e\n"
        "    else:\n"
        "        raise SystemExit(f'{fn.__name__} did not raise')\n"
        "assert w.is_window(1) is False and w.is_minimized(1) is False and w.is_cloaked(1) is False\n"
        "cap = w.WindowCapturer()\n"
        "try:\n"
        "    cap.capture(1, 8, 8)\n"
        "except RuntimeError as e:\n"
        "    assert 'requires Windows' in str(e), e\n"
        "else:\n"
        "    raise SystemExit('WindowCapturer.capture did not raise')\n"
        "cap.close()\n"
        "from needledropper.transport import pixel, chatlog\n"
        "ok, why = pixel.Win32CaptureBackend().available()\n"
        "assert not ok and 'Windows' in why\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code, str(ROOT / "companion")], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


@windows_only
def test_dpi_awareness_and_screen_capture_smoke():
    mode = wincapture.set_dpi_awareness()
    assert mode in {"per-monitor-v2", "per-monitor", "system-aware", "already-set", "unaware"}
    assert wincapture.set_dpi_awareness() == mode  # idempotent
    try:
        buf, w, h, stride = wincapture.capture_screen_region(0, 0, 8, 8)
    except RuntimeError as e:  # no interactive desktop (service session / locked CI)
        pytest.skip(f"no desktop to capture: {e}")
    assert (w, h, stride) == (8, 8, 32)
    assert isinstance(buf, bytes) and len(buf) == 8 * 8 * 4


@windows_only
def test_capture_rejects_bad_sizes():
    for args in [(0, 0, 0, 8), (0, 0, 8, -1), (0, 0, 100000, 8)]:
        with pytest.raises(RuntimeError):
            wincapture.capture_screen_region(*args)


@windows_only
def test_invalid_window_handles():
    bogus = 0x7FFFFFF0
    assert wincapture.is_window(bogus) is False
    assert wincapture.is_minimized(0) is False
    assert wincapture.describe_window(bogus) is None
    with pytest.raises(RuntimeError, match="GetClientRect"):
        wincapture.client_rect(bogus)
    with pytest.raises(RuntimeError):
        wincapture.capture_window_region(bogus, 8, 8)


@windows_only
def test_find_window_without_match_returns_none():
    # Hermetic: the exe must not share a flavor folder + name with a real game. The old path
    # (<repo>/no/_forever_/Wow.exe) matched the owner's running WoW: Forever by design (score 60:
    # "same flavor dir + exe name, different install root"), so the test failed whenever the game ran.
    title = "NeedleDropper test window that does not exist 7f3a"
    assert wincapture.find_game_window(None, title=title) is None
    assert wincapture.find_game_window(ROOT / "no" / "_nd_none_7f3a_" / "NoSuchGame7f3a.exe", title=title) is None
    assert wincapture.find_game_window(None, title=title, strict=True) is None


@windows_only
def test_window_scoring_prefers_exact_exe():
    norm = wincapture._norm
    game = norm(r"C:\Program Files (x86)\World of Warcraft\_forever_\Wow.exe")
    exact = wincapture._score("World of Warcraft", "GxWindowClass",
                              r"c:\program files (x86)\world of warcraft\_forever_\wow.exe", game, "World of Warcraft")
    other_root = wincapture._score("World of Warcraft", "GxWindowClass", r"D:\Games\WoW\_forever_\Wow.exe", game,
                                   "World of Warcraft")
    retail = wincapture._score("World of Warcraft", "GxWindowClass",
                               r"C:\Program Files (x86)\World of Warcraft\_retail_\Wow.exe", game, "World of Warcraft")
    unrelated = wincapture._score("Notepad", "Notepad", r"C:\Windows\notepad.exe", game, "World of Warcraft")
    assert exact > other_root > retail > 0
    assert unrelated == 0


@windows_only
def test_list_windows_is_read_only_enumeration():
    """Enumerating with a title that matches nothing touches no window and returns []."""
    assert wincapture.list_game_windows(None, title="zz-no-such-window-zz") == []


@windows_only
@pytest.mark.skipif(not __import__("os").environ.get("NEEDLEDROPPER_LIVE_TESTS"),
                    reason="opens a small window on the desktop; set NEEDLEDROPPER_LIVE_TESTS=1 to run")
def test_live_capture_of_a_real_window():
    """Draw a beacon in a real (Tk) window, find it, capture it both ways and decode it."""
    tk = pytest.importorskip("tkinter")
    from needledropper import protocol
    from needledropper.models import Event, EventType, Flags
    from needledropper.transport.pixel import find_and_decode

    wincapture.set_dpi_awareness()
    ev = Event(EventType.FIRE, 77, 1234, 40213, Flags.DROP_ACTIVE | Flags.TEST)
    title = "NeedleDropper live capture test"
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    try:
        root.title(title)
        root.geometry("400x60+200+200")
        root.attributes("-topmost", True)
        canvas = tk.Canvas(root, width=400, height=60, highlightthickness=0, bd=0, bg="#202020")
        canvas.pack()
        for i, (r, g, b) in enumerate(protocol.encode_beacon_cells(ev)):
            canvas.create_rectangle(i * 4, 0, i * 4 + 3, 3, fill=f"#{r:02x}{g:02x}{b:02x}", width=0)
        for _ in range(20):
            root.update()
            __import__("time").sleep(0.02)
        hwnd = int(root.frame(), 16)
        assert wincapture.find_game_window(None, title=title) == hwnd
        x, y, cw, ch = wincapture.client_rect(hwnd)
        res_screen = find_and_decode(*wincapture.capture_screen_region(x, y, min(256, cw), min(32, ch)))
        res_window = find_and_decode(*wincapture.capture_window_region(hwnd, 256, 32))
        assert res_window is not None and res_window.event == ev and (res_window.x, res_window.y) == (0, 0)
        assert res_screen is None or res_screen.event == ev  # None only if something covers it on screen
    finally:
        root.destroy()


@windows_only
def test_window_capturer_reuses_its_bitmaps():
    """PrintWindow capture keeps one client-sized bitmap instead of allocating one per frame."""
    import ctypes

    hwnd = ctypes.windll.user32.GetShellWindow()  # the desktop (Progman): always there in a session
    if not hwnd:
        pytest.skip("no desktop shell window")
    cap = wincapture.WindowCapturer()
    try:
        try:
            first = cap.capture(hwnd, 16, 8)
        except RuntimeError as e:
            pytest.skip(f"PrintWindow not possible here: {e}")
        assert first[1:] == (16, 8, 64) and len(first[0]) == 16 * 8 * 4
        assert cap.allocations == 2 and cap.full_size == wincapture.client_rect(hwnd)[2:]
        for _ in range(5):
            cap.capture(hwnd, 16, 8)
        assert cap.allocations == 2  # same client size: nothing new allocated
        cap.capture(hwnd, 8, 4)  # a new crop size re-creates only the small bitmap
        assert cap.allocations == 3
    finally:
        cap.close()
    assert cap.full_size is None
    cap.close()  # idempotent


@windows_only
def test_cloaked_windows_are_skipped(monkeypatch):
    assert wincapture.is_cloaked(0x7FFFFFF0) is False  # bogus handle: unknown -> not cloaked
    monkeypatch.setattr(wincapture, "_score", lambda *a, **k: 1)  # pretend every window matches
    every = wincapture.list_game_windows(None, title="")
    if not every:
        pytest.skip("no visible top-level windows in this session")
    monkeypatch.setattr(wincapture, "is_cloaked", lambda hwnd: True)
    assert wincapture.list_game_windows(None, title="") == []
    assert wincapture.find_game_window(None, title="") is None
