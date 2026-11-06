"""The YouTube player window never takes the focus from the game (real-client findings #8, #11, #13;
review findings YT-PLAYER-FOCUS-PRIME, journey F1 and F2; docs/research/no-focus-playback.md):

* deck/window.py finds the launched browser's window by its process tree, makes it never-activating
  and keeps it out of the taskbar, hides it, and shows it always on top at a corner of the game's
  monitor only while a YouTube song plays -- with a fake Win32, nothing here touches the desktop;
* the deck makes the window when the app starts, never while the game is in front, never in the
  default browser; a browser that dies at once (or never connects) is logged and the other one is
  tried; Edge redirected by Image File Execution Options is skipped; the child's environment is clean;
* on ARM the YouTube provider cues the video in that window.
"""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from needledropper.deck import server, window as W  # noqa: E402
from needledropper.deck.server import Deck  # noqa: E402
from needledropper.models import Drop  # noqa: E402
from needledropper.providers.base import ProviderError  # noqa: E402
from needledropper.providers.youtube import YouTubeProvider  # noqa: E402

STATIC = Path(server.__file__).resolve().parent / "static"


class FakeWin32:
    """Records every call; top-level windows and the process tree are set by the test."""

    def __init__(self):
        self.windows = []          # (hwnd, pid, class, title, visible, owned)
        self.tree = {}             # pid -> parent pid
        self.styles = {}
        self.calls = []
        self.monitors = {None: (0, 0, 1920, 1080), 555: (1920, 0, 5760, 2160)}
        self.sizes = {}
        self.alive = set()

    def top_windows(self):
        return list(self.windows)

    def parents(self):
        return dict(self.tree)

    def is_window(self, hwnd):
        return hwnd in self.alive

    def window_size(self, hwnd):
        return self.sizes.get(hwnd, (420, 380))

    def get_exstyle(self, hwnd):
        return self.styles.get(hwnd, W.WS_EX_APPWINDOW)

    def set_exstyle(self, hwnd, style):
        self.calls.append(("exstyle", hwnd, style))
        self.styles[hwnd] = style

    def set_pos(self, hwnd, after, x, y, cx, cy, flags):
        self.calls.append(("pos", hwnd, after, x, y, flags))
        return True

    def close(self, hwnd):
        self.calls.append(("close", hwnd))

    def monitor_rect(self, hwnd):
        return self.monitors.get(hwnd, self.monitors[None])

    def dpi_scale(self, hwnd):
        return 1.0


def make_window(**kw):
    win32 = FakeWin32()
    pw = W.PlayerWindow(win32, sleep=lambda s: None, **kw)
    return win32, pw


# ------------------------------------------------------------- deck/window --

@pytest.mark.parametrize("corner,expect", [("topright", (1484, 16)), ("topleft", (16, 16)),
                                           ("bottomright", (1484, 684)), ("bottomleft", (16, 684)),
                                           ("nonsense", (1484, 16))])
def test_corner_positions(corner, expect):
    assert W.corner_position(corner, (0, 0, 1920, 1080), (420, 380)) == expect


def test_the_window_is_found_by_the_launched_process_tree_and_made_never_activating():
    win32, pw = make_window()
    win32.tree = {101: 100, 102: 101, 900: 4}
    win32.windows = [(7, 900, W.CHROME_CLASS, "Some page - Google Chrome", True, False),   # someone else's
                     (8, 102, "Chrome_WidgetWin_0", "", False, False),                     # a helper
                     (9, 102, W.CHROME_CLASS, "NeedleDropper", True, False)]
    win32.alive = {7, 9}
    assert pw.adopt(100, timeout=0.1)
    assert pw.hwnd == 9
    style = win32.styles[9]
    assert style & W.WS_EX_NOACTIVATE and style & W.WS_EX_TOOLWINDOW and not style & W.WS_EX_APPWINDOW
    hide = win32.calls[-1]
    assert hide[0] == "pos" and hide[1] == 9 and hide[5] & W.SWP_HIDEWINDOW and hide[5] & W.SWP_NOACTIVATE
    assert not pw.shown


def test_a_window_handed_to_a_running_browser_is_found_by_the_page_title():
    win32, pw = make_window()
    win32.windows = [(11, 4321, W.CHROME_CLASS, "Elwynn Forest \u2014 NeedleDropper", True, False)]
    win32.alive = {11}
    assert pw.adopt(100, timeout=0.1) and pw.hwnd == 11


def test_no_window_means_no_adoption():
    win32, pw = make_window()
    win32.windows = [(7, 900, W.CHROME_CLASS, "Some page - Google Chrome", True, False)]
    clock = iter([0.0, 0.05, 0.2, 0.3])
    pw.clock = lambda: next(clock)
    assert not pw.adopt(100, timeout=0.1) and pw.hwnd is None


def test_show_is_topmost_at_the_games_monitor_corner_and_never_activates():
    win32, pw = make_window(game_window=lambda: 555)
    win32.windows = [(9, 100, W.CHROME_CLASS, "NeedleDropper", True, False)]
    win32.alive = {9}
    pw.adopt(100, timeout=0.1)
    win32.calls.clear()
    assert pw.show()
    (kind, hwnd, after, x, y, flags), = win32.calls
    assert (hwnd, after) == (9, W.HWND_TOPMOST)
    assert (x, y) == (5760 - 16 - 420, 16)                         # top-right of the game's monitor
    assert flags & W.SWP_NOACTIVATE and flags & W.SWP_SHOWWINDOW and flags & W.SWP_NOSIZE
    pw.corner = "bottomleft"
    pw.show()
    assert win32.calls[-1][3:5] == (1920 + 16, 2160 - 16 - 380)
    pw.position = (100, 200)                                        # deck.playerPosition wins
    pw.show()
    assert win32.calls[-1][3:5] == (100, 200)
    assert pw.hide() and win32.calls[-1][5] & W.SWP_HIDEWINDOW


def test_hide_later_is_cancelled_by_a_new_song():
    win32, pw = make_window()
    win32.windows = [(9, 100, W.CHROME_CLASS, "NeedleDropper", True, False)]
    win32.alive = {9}
    pw.adopt(100, timeout=0.1)
    gate = threading.Event()
    pw.sleep = lambda s: gate.wait(2)
    pw.show()
    pw.hide_later(0.5)
    pw.show()                                                       # the next YouTube song started
    win32.calls.clear()
    gate.set()
    time.sleep(0.2)
    assert not any(c[0] == "pos" and c[5] & W.SWP_HIDEWINDOW for c in win32.calls)


def test_a_closed_window_is_forgotten_and_close_posts_wm_close():
    win32, pw = make_window()
    win32.windows = [(9, 100, W.CHROME_CLASS, "NeedleDropper", True, False)]
    win32.alive = {9}
    pw.adopt(100, timeout=0.1)
    pw.close()
    assert ("close", 9) in win32.calls and pw.hwnd is None
    win32.alive = set()
    assert not pw.show()


def test_the_window_manager_is_off_in_tests():
    assert W.make_player_window() is None                           # conftest: NEEDLEDROPPER_NO_WINDOW_MANAGER


# ---------------------------------------------------------------- the deck --

class Proc:
    def __init__(self, pid, code=None):
        self.pid, self.code = pid, code

    def poll(self):
        return self.code


def test_the_deck_shows_the_window_for_a_youtube_song_and_hides_it_afterwards():
    d = Deck(port=0)
    shown = []
    d.window = types.SimpleNamespace(show=lambda: shown.append("show"),
                                     hide_later=lambda s: shown.append(("hide", round(s, 1))))
    d.send({"cmd": "play", "playId": "p1", "source": "youtube", "videoId": "dQw4w9WgXcQ"})
    d.send({"cmd": "play", "playId": "p2", "source": "local", "url": "x"})          # not a YouTube song
    d.send({"cmd": "stop", "playId": "p1", "fadeOutMs": 3000})
    d.send({"cmd": "hide", "dropId": "zone:1429"})
    assert shown == ["show", ("hide", 3.6), ("hide", server.HIDE_AFTER_HIDE_S)]


def test_no_player_window_is_created_while_the_game_is_in_front(monkeypatch):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": f"C:\\Fake\\{kind}.exe")
    launched = []
    d = Deck(port=0, launcher=lambda a: launched.append(a) or Proc(1)).start()
    try:
        d.game_in_front = lambda: True
        assert d.open_player() == "none" and launched == []
        assert d.ensure_player(0.3) is False and launched == []
        yt = YouTubeProvider({}, None, d, connect_timeout=0.2)
        with pytest.raises(ProviderError, match="none is made while the game is in front"):
            yt.play(Drop(n=0, id="zone:1429", kind="zone", title="t", artist="a", youtube="dQw4w9WgXcQ"))
        d.game_in_front = lambda: False
        assert d.open_player() == "edge" and len(launched) == 1
    finally:
        d.stop()


def test_a_browser_that_dies_at_once_is_replaced_by_the_other_one(monkeypatch):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": f"C:\\Fake\\{kind}.exe")
    launched = []

    def launcher(args):
        launched.append(args[0])
        return Proc(len(launched), code=1 if "edge" in args[0] else None)   # Edge exits with an error
    d = Deck(port=0, launcher=launcher).start()
    try:
        t0 = time.monotonic()
        assert d.ensure_player(1.5) is False                      # Chrome never connects in this test
        assert launched == ["C:\\Fake\\edge.exe", "C:\\Fake\\chrome.exe"]
        assert time.monotonic() - t0 < 2.5                         # the switch came at once, not after 8 s
        assert d._browser_order() == ["chrome"]                    # Edge is not tried again
    finally:
        d.stop()


def test_failover_also_covers_a_browser_that_never_connects(monkeypatch):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": f"C:\\Fake\\{kind}.exe")
    launched = []
    d = Deck(port=0, launcher=lambda a: launched.append(a[0]) or Proc(1)).start()
    try:
        assert d.ensure_player(0.3, failover=True) is False
        assert launched == ["C:\\Fake\\edge.exe", "C:\\Fake\\chrome.exe"]
    finally:
        d.stop()


def test_the_player_command_line_keeps_a_hidden_page_working(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": "C:\\Fake\\chrome.exe")
    launched = []
    d = Deck(port=0, launcher=lambda a: launched.append(a) or Proc(1)).start()
    try:
        d.configure_player(browser="chrome", profile_dir=tmp_path / "deck-browser", corner="bottomleft")
        assert d.open_player() == "chrome"
        args = launched[-1]
        for flag in server.NO_THROTTLE_FLAGS + ("--autoplay-policy=no-user-gesture-required", "--no-first-run"):
            assert flag in args
        assert d.player_corner == "bottomleft"
    finally:
        d.stop()


def test_prepare_player_makes_the_window_at_start_and_stops_with_the_deck(monkeypatch):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": f"C:\\Fake\\{kind}.exe")
    launched = []
    d = Deck(port=0, launcher=lambda a: launched.append(a[0]) or Proc(1)).start()
    t = threading.Thread(target=d.prepare_player, daemon=True)
    t.start()
    time.sleep(0.3)
    d.stop()                                                        # the app quits: the wait ends too
    t.join(3)
    assert not t.is_alive() and launched == ["C:\\Fake\\edge.exe"]
    d2 = Deck(port=0, launcher=lambda a: launched.append(a[0]) or Proc(1))
    d2.configure_player(auto_open="never")
    assert d2.prepare_player() is False and len(launched) == 1


def test_edge_redirected_by_image_file_execution_options_is_skipped(monkeypatch):
    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    reg = {r"msedge.exe": {}, r"msedge.exe\0": {"Debugger": r"C:\Wedge\wedge.exe", "FilterFullPath": "x"}}

    def open_key(root, path):
        if isinstance(root, Key):
            path = root.path + "\\" + path
        rel = path.split("Image File Execution Options\\")[-1]
        if rel not in reg:
            raise OSError("no key")
        return Key(rel)

    def query(key, name):
        if name not in reg[key.path]:
            raise OSError("no value")
        return reg[key.path][name], 1

    def enum(key, i):
        subs = [p.split("\\", 1)[1] for p in reg if p.startswith(key.path + "\\")]
        if i >= len(subs):
            raise OSError("no more")
        return subs[i]

    fake = types.SimpleNamespace(HKEY_LOCAL_MACHINE=object(), OpenKey=open_key, QueryValueEx=query, EnumKey=enum,
                                 CloseKey=lambda k: None)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(server.sys, "platform", "win32")
    assert server.edge_redirected()
    assert server.find_browser("edge") is None
    reg[r"msedge.exe\0"].pop("Debugger")
    assert not server.edge_redirected()


def test_the_browser_gets_a_clean_environment(monkeypatch):
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "C:\\Temp\\_MEI123")
    monkeypatch.setenv("_MEIPASS2", "C:\\Temp\\_MEI123")
    monkeypatch.setenv("NEEDLEDROPPER_HOME", "C:\\somewhere")
    env = server.child_environment()
    assert "_PYI_APPLICATION_HOME_DIR" not in env and "_MEIPASS2" not in env
    assert env["NEEDLEDROPPER_HOME"] == "C:\\somewhere"


# -------------------------------------------------------- cue at ARM (prime) --

def test_arm_cues_the_video_in_the_open_player():
    sent = []
    deck = types.SimpleNamespace(page_open=True, connected=True, auto_open="youtube", send=sent.append,
                                 ensure_player=lambda t: True)
    yt = YouTubeProvider({}, None, deck)
    yt.prepare(Drop(n=0, id="zone:1429", kind="zone", title="t", artist="a", youtube="dQw4w9WgXcQ", startMs=12000))
    assert sent == [{"cmd": "cue", "videoId": "dQw4w9WgXcQ", "dropId": "zone:1429", "startMs": 12000}]
    yt.prepare(Drop(n=0, id="zone:1411", kind="zone", title="t", artist="a"))     # no video: nothing
    assert len(sent) == 1


def test_the_player_page_cues_and_starts_a_cued_video():
    js = (STATIC / "player.js").read_text(encoding="utf-8")
    assert 'case "cue": cueYoutube(cmd)' in js and "cueVideoById" in js
    assert "if (wasCued) yt.player.playVideo();" in js
    assert "window.focus" not in js                                 # the page never focuses itself
    assert "cue" in server.PLAYER_ONLY_CMDS


def test_the_app_prepares_the_player_window_when_youtube_is_on():
    from needledropper.panel_api import Companion

    calls = []
    fake = types.SimpleNamespace(deck=types.SimpleNamespace(prepare_player=lambda: calls.append(1),
                                                            auto_open="youtube", page_open=False,
                                                            player_opening=False),
                                 providers=[types.SimpleNamespace(name="youtube")], _player_thread=None)
    fake._youtube_enabled = lambda: Companion._youtube_enabled(fake)
    Companion._prepare_player(fake)
    fake._player_thread.join(2)
    assert calls == [1]
    fake.deck.auto_open = "never"
    fake._player_thread = None
    Companion._prepare_player(fake)
    assert fake._player_thread is None and calls == [1]
    fake.deck.auto_open = "youtube"
    fake.providers = [types.SimpleNamespace(name="spotify")]       # no YouTube: no window
    Companion._prepare_player(fake)
    assert fake._player_thread is None



def test_children_get_the_normal_dll_search_order_and_the_app_keeps_its_own(monkeypatch):
    """Real-client #11: the frozen app's bootloader sets its unpack folder as the DLL directory, which
    a browser it starts would inherit. The launch clears it for the child and puts it back after."""
    import ctypes

    calls = []
    mei = r"C:\Temp\_MEI123"

    class K32:
        def GetDllDirectoryW(self, n, buf):
            buf.value = mei
            calls.append(("get",))
            return len(buf.value)

        def SetDllDirectoryW(self, path):
            calls.append(("set", path))
            return 1

    monkeypatch.setattr(server.sys, "frozen", True, raising=False)
    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=K32()), raising=False)
    with server._NormalDllSearch():
        assert calls == [("get",), ("set", None)]            # the child is created with the normal order
    assert calls[-1] == ("set", mei)                          # ... and the app's own setting is back
    monkeypatch.setattr(server.sys, "frozen", False)
    calls.clear()
    with server._NormalDllSearch():
        pass
    assert calls == []                                        # not frozen: nothing to do


def test_the_launch_position_is_only_an_explicit_one(monkeypatch):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": r"C:\Fake\chrome.exe")
    launched = []
    d = Deck(port=0, launcher=lambda a: launched.append(a) or Proc(1)).start()
    try:
        d.window = types.SimpleNamespace(win32=FakeWin32(), corner="topright", position=None,
                                         adopt=lambda pid: True, close=lambda: None)
        d.open_player()
        assert not any(a.startswith("--window-position") for a in launched[-1])   # placed later, physically
        d.configure_player(position=(10, 20))
        d.open_player()
        assert "--window-position=10,20" in launched[-1]
    finally:
        d.stop()
