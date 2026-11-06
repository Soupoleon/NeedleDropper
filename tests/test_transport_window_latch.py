"""PIX-WINDOW-LATCH (docs/review/baseline-findings.md) and real-client finding #2 (the reader latched onto a
transient "World of Warcraft" window at game start), plus the faster BitBlt <-> PrintWindow switching.

Hermetic: fake backends only (no real window is enumerated, even though WoW may be running on the PC).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_transport_pixel import EV, FIRE7, FakeClock, FakeScreen, Harness  # noqa: E402

from needledropper.transport import wincapture  # noqa: E402
from needledropper.transport.pixel import PixelTransport  # noqa: E402

GAME_EXE = r"C:\Program Files (x86)\World of Warcraft\_forever_\Wow.exe"
WANT = wincapture._norm(GAME_EXE)
TITLE = "World of Warcraft"


# ----------------------------------------------------------------- scoring --

def test_strict_scoring_never_accepts_a_title_only_window():
    score = wincapture._score
    # the finding's reproduction: a python.exe window titled exactly "World of Warcraft"
    assert score(TITLE, "TkTopLevel", r"C:\Python314\python.exe", WANT, TITLE) == 30      # the old rule took it
    assert score(TITLE, "TkTopLevel", r"C:\Python314\python.exe", WANT, TITLE, strict=True) == 0
    # an Explorer window on the install folder, a browser tab, the same without a known exe
    assert score(TITLE, "CabinetWClass", r"C:\Windows\explorer.exe", WANT, TITLE, strict=True) == 0
    assert score("World of Warcraft - Google Chrome", "Chrome_WidgetWin_1",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe", None, TITLE, strict=True) == 0
    assert score(TITLE, "CabinetWClass", r"C:\Windows\explorer.exe", None, TITLE, strict=True) == 0


def test_strict_scoring_keeps_every_real_game_window():
    score = wincapture._score
    exact = score(TITLE, "GxWindowClass", GAME_EXE.lower(), WANT, TITLE, strict=True)
    other_root = score(TITLE, "GxWindowClass", r"D:\Games\WoW\_forever_\Wow.exe", WANT, TITLE, strict=True)
    elevated = score(TITLE, "GxWindowClass", None, WANT, TITLE, strict=True)  # image unreadable: class decides
    no_exe_known = score(TITLE, "GxWindowClass", r"E:\WoW\_forever_\Wow.exe", None, TITLE, strict=True)
    assert exact > other_root >= wincapture.EXE_MATCH_SCORE > elevated > 0 and no_exe_known > 0
    # the configured exe's own short-lived window (another class) still matches by path, but less well
    transient = score(TITLE, "BlizzardSplash", GAME_EXE, WANT, TITLE, strict=True)
    assert 0 < transient < exact
    # a Wow*.exe somewhere else that is not even the game's window class: not the configured game
    assert score(TITLE, "LauncherWindow", r"C:\Tools\WowUp\WowUp.exe", WANT, TITLE, strict=True) == 0


# --------------------------------------------------------------- transport --

class Windows(FakeScreen):
    """FakeScreen with several candidate windows: ``wins = {hwnd: score}``; ``beacon_on`` names the
    window whose client area shows the strip (the others show the game background only)."""

    def __init__(self, ev, wins: dict[int, int], beacon_on: int) -> None:
        super().__init__(ev)
        self.wins = dict(wins)
        self.beacon_on = beacon_on
        self.current = None
        self.lists = 0
        self.scores: dict[int, int] = {}

    def list_windows(self):  # like wincapture.list_game_windows: today's strict score, matches only
        self.lists += 1
        scored = [(hwnd, self.window_score(hwnd)) for hwnd in self.wins]
        return sorted(((hw, sc) for hw, sc in scored if sc > 0), key=lambda kv: -kv[1])

    def window_score(self, hwnd):
        return self.scores.get(hwnd, self.wins.get(hwnd, 0))

    def find_window(self):  # must not be needed when list_windows exists
        raise AssertionError("find_window called although list_windows is available")

    def window_alive(self, hwnd):
        return hwnd in self.wins

    def describe(self, hwnd):
        return f"hwnd {hwnd:#x}"

    def capture_screen(self, x, y, w, h):
        self.calls.append(("screen", x, y, w, h))
        return self._render(self.screen_event if self._showing() else None, w, h)

    def capture_window(self, hwnd, w, h):
        self.calls.append(("window", hwnd, w, h))
        return self._render(self.window_event if hwnd == self.beacon_on else None, w, h)

    def _showing(self):
        return self.current == self.beacon_on


def harness(screen: Windows) -> Harness:
    h = Harness.__new__(Harness)
    h.screen = screen
    h.clock = FakeClock()
    h.events, h.diag = [], []
    config = {"enabled": True, "method": "screen", "hz": 4, "regionW": 256, "regionH": 32}
    h.t = PixelTransport(config, SimpleNamespace(game_exe=GAME_EXE), lambda e, n: h.events.append((e, n)),
                         h.diag.append, backend=screen, clock=h.clock)
    orig = h.t._ensure_window

    def track(now):
        hwnd = orig(now)
        screen.current = hwnd
        return hwnd
    h.t._ensure_window = track
    return h


def test_a_short_lived_window_at_game_start_is_replaced_by_the_game_window():
    launcher, game = 0x1001, 0x2002
    s = Windows(EV[0], {launcher: 37}, beacon_on=game)
    h = harness(s)
    assert h.step() == ["invalid"]                       # latched onto the only candidate: no beacon there
    assert h.t.status()["hwnd"] == launcher
    s.wins[game] = 135                                   # the real _forever_\Wow.exe window appears
    out = h.step(int(PixelTransport.REFIND_INTERVAL / 0.25) + 2)
    assert h.t.status()["hwnd"] == game and "baseline" in out
    assert any("better match" in d for d in h.diag)
    h.show(FIRE7)
    h.step(2)
    assert [e for e, _ in h.events] == [FIRE7]           # the game's events flow


def test_no_more_searching_once_the_beacon_was_seen():
    game = 0x2002
    s = Windows(EV[0], {game: 135}, beacon_on=game)
    h = harness(s)
    h.step(2)
    lists = s.lists
    s.wins[0x3003] = 500                                 # even a "better" window later is not looked for
    h.step(int(3 * PixelTransport.REFIND_INTERVAL / 0.25))
    assert s.lists == lists and h.t.status()["hwnd"] == game


def test_a_cached_handle_that_stops_being_the_game_counts_as_lost():
    game, other = 0x2002, 0x4004
    s = Windows(EV[0], {game: 135}, beacon_on=game)
    h = harness(s)
    h.step(2)
    s.scores[game] = 0                                   # the handle was reused by another program
    s.wins[other] = 40
    h.step(int(PixelTransport.REVALIDATE_INTERVAL / 0.25) + 1)
    assert h.t.status()["hwnd"] == other
    assert any("not the game's window any more" in d for d in h.diag)


def test_backends_without_scores_keep_the_old_behaviour():
    h = Harness(FakeScreen(EV[0]))
    h.step(int(3 * PixelTransport.REFIND_INTERVAL / 0.25))
    assert h.screen.finds == 1 and h.t.status()["window"]


# ------------------------------------------------------------ capture method --

def test_auto_goes_to_printwindow_fast_and_back_to_the_screen_when_it_shows_the_strip():
    s = FakeScreen(EV[0])
    s.screen_event = None                                # character select: BitBlt sees nothing
    h = Harness(s)
    h.step(PixelTransport.SWITCH_TO_PRINT_AFTER)
    assert h.t.status()["method"] == "printwindow"       # ~1.5 s instead of 5 s
    assert h.step(2) == ["baseline", "same"]
    s.screen_event = EV[0]                               # in the world: the screen shows the strip again
    h.step(PixelTransport.PROBE_EVERY)
    assert h.t.status()["method"] == "screen"
    assert any("visible on screen again" in d for d in h.diag)
    n = len(s.calls)
    h.step(3)
    assert all(c[0] == "screen" for c in s.calls[n:])   # back on the cheap path
    h.show(FIRE7)
    h.step(2)
    assert h.emitted() == [FIRE7]                        # same window, same baseline: nothing replayed


def test_probe_that_sees_nothing_stays_on_printwindow():
    s = FakeScreen(EV[0])
    s.screen_event = None
    h = Harness(s)
    h.step(PixelTransport.SWITCH_TO_PRINT_AFTER + 3 * PixelTransport.PROBE_EVERY)
    assert h.t.status()["method"] == "printwindow"
    probes = [c for c in s.calls[PixelTransport.SWITCH_TO_PRINT_AFTER:] if c[0] == "screen"]
    assert len(probes) == 3                              # one cheap probe every PROBE_EVERY frames


def test_with_no_strip_anywhere_printwindow_is_not_tried_every_1_5_s():
    """Loading screen / Alt+Z / beacon off: neither method sees a header. Only the first switch is fast;
    after that the long streak applies, so the expensive PrintWindow is not used most of the time."""
    s = FakeScreen(None)                                 # nothing on screen, nothing in the window
    h = Harness(s)
    h.step(PixelTransport.SWITCH_TO_PRINT_AFTER)
    assert h.t.status()["method"] == "printwindow"       # the first switch: fast
    h.step(PixelTransport.SWITCH_AFTER)
    assert h.t.status()["method"] == "screen"            # PrintWindow saw nothing either
    h.step(PixelTransport.SWITCH_TO_PRINT_AFTER)
    assert h.t.status()["method"] == "screen"            # not fast again ...
    h.step(PixelTransport.SWITCH_AFTER - PixelTransport.SWITCH_TO_PRINT_AFTER)
    assert h.t.status()["method"] == "printwindow"       # ... the long streak
    s.screen_event = s.window_event = EV[0]              # a strip again: back to the fast rule
    h.step(PixelTransport.PROBE_EVERY + 2)
    assert h.t.status()["method"] == "screen"
    s.screen_event = None
    h.step(PixelTransport.SWITCH_TO_PRINT_AFTER)
    assert h.t.status()["method"] == "printwindow"


def test_printwindow_failure_backs_off_before_trying_it_again():
    s = FakeScreen(EV[0])
    s.screen_event = None
    s.window_error = RuntimeError("PrintWindow failed (Win32 error 5)")
    h = Harness(s)
    h.step(PixelTransport.SWITCH_TO_PRINT_AFTER + 1)
    assert h.t.status()["method"] == "screen" and h.t.status()["captureErrors"] == 1
    h.step(PixelTransport.SWITCH_TO_PRINT_AFTER + 1)
    assert h.t.status()["captureErrors"] == 1            # not again after another 1.5 s ...
    h.step(PixelTransport.SWITCH_AFTER)
    assert h.t.status()["captureErrors"] == 2            # ... only after the long streak
