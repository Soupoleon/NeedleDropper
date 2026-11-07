"""Regression tests for the v1.2.1 review findings fixed after the build (2026-11-06): the YouTube box after a
failed start (RCOMP-1), the restore of the player's own music across back-to-back in-app drops (RCOMP-2), the
player page's launch key (RCOMP-3, ON-1, a second player page), the in-app SDK's not_ready blips and silent
failures (RCOMP-5, RCOMP-6), YouTube-first FIREs (RCOMP-7), the D3 late start (RP-2, RP-4), the old-addon
warning (ON-2), the allowlist state (ON-3), the PREFS order of a chat-log batch (INGAME-4), the player window's
hand-back and the YouTube box placement (DESIGN-v1.2.1 3.9). No browser, no network, no real window."""
from __future__ import annotations

import dataclasses
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from needledropper import protocol2 as p2  # noqa: E402
from needledropper.deck import server  # noqa: E402
from needledropper.deck.server import CLIENT_PLAYER, Deck  # noqa: E402
from needledropper.engine import Engine  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402
from needledropper.providers.base import PlayHandle, Provider, ProviderError  # noqa: E402
from needledropper.providers.youtube import YouTubeProvider  # noqa: E402


class FakeBox:
    """The window manager's show / hide side, recorded (deck/window.PlayerWindow)."""

    def __init__(self) -> None:
        self.visible = False
        self.calls: list[str] = []
        self._gen = 0
        self._lock = threading.Lock()
        self.corner = "topright"
        self.position = None
        self.hwnd = None

    def show(self) -> bool:
        with self._lock:
            self._gen += 1
            self.visible = True
        self.calls.append("show")
        return True

    def hide_later(self, delay_s: float) -> None:
        with self._lock:
            self._gen += 1
            gen = self._gen
        self.calls.append(f"hide_later({delay_s:g})")

        def run() -> None:
            time.sleep(delay_s)
            with self._lock:
                if gen == self._gen:
                    self.visible = False

        threading.Thread(target=run, daemon=True).start()

    def hide(self) -> bool:
        self.visible = False
        return True

    def close(self) -> None:
        self.calls.append("close")

    def foreground(self) -> Optional[int]:
        return None

    def adopt(self, pid: Any, timeout: float = 0.0, *, prev_foreground: Any = None) -> bool:
        return False


class PlayerPage:
    """A player page registered on the deck in-process; ``answer(cmd)`` returns the status it posts."""

    def __init__(self, deck: Deck, answer) -> None:
        self.deck = deck
        self.answer = answer
        self.got: list[dict] = []
        self.client, replay = deck._add_client(CLIENT_PLAYER)
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self) -> None:
        while True:
            msg = self.client.q.get()
            if msg is None:
                return
            if not msg.startswith("data: "):
                continue
            cmd = json.loads(msg[len("data: "):])
            self.got.append(cmd)
            status = self.answer(cmd)
            if status:
                self.deck._on_status(status)

    def close(self) -> None:
        self.deck._remove_client(self.client)
        self.client.q.put(None)


class StubSpotify(Provider):
    name = "spotify"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def can_play(self, drop) -> bool:
        return bool(drop.spotify)

    def play(self, drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append("play")
        return PlayHandle(provider=self.name, drop=drop, data={})

    def poll(self, handle) -> str:
        return "playing"

    def stop(self, handle, *, fade_out_ms: int = 0) -> None:
        self.calls.append("stop")

    def now_playing(self, handle):
        return None


@pytest.fixture
def deck():
    d = Deck(port=0, heartbeat_sec=0.2, launcher=lambda a: None, url_opener=lambda u: False).start()
    yield d
    d.stop()


# ------------------------------------------------------------------ RCOMP-1 --

def test_a_youtube_start_that_fails_at_once_hides_the_box_before_the_next_provider_plays(deck, tmp_path):
    """Review RCOMP-1: the "play" command shows the box; the page answering with error 150 must hide it again
    (the provider stops the play, the deck hides on the error) -- never left on top of the game while
    Spotify plays the drop ("Play music with: YouTube, then Spotify")."""
    box = FakeBox()
    deck.window = box

    def answer(cmd):
        if cmd.get("cmd") == "play" and cmd.get("source") == "youtube":
            return {"state": "error", "playId": cmd["playId"], "code": 150, "detail": "not embeddable"}
        return None

    page = PlayerPage(deck, answer)
    sp = StubSpotify()
    yt = YouTubeProvider({}, SimpleNamespace(app_dir=tmp_path / "app"), deck)
    engine = Engine([sp, yt], deck, music_order="youtube-spotify", log=None)
    fr = p2.build_frame(ET.FIRE, seq=5, flags=Flags.DROP_ACTIVE, drop_id="zone:1411", label="Durotar",
                        artist="Dolly Parton", title="9 to 5", youtube="dQw4w9WgXcQ",
                        spotify="spotify:track:" + "a" * 22)
    engine.handle(p2.decode_frame(p2.encode_frame(fr)), "pixel")
    assert engine.status()["playing"]["provider"] == "spotify" and sp.calls == ["play"]
    deadline = time.monotonic() + 3
    while not any(c.get("cmd") == "stop" for c in page.got) and time.monotonic() < deadline:
        time.sleep(0.02)
    stops = [c for c in page.got if c.get("cmd") == "stop"]
    assert stops and stops[0]["fadeOutMs"] == 0  # the failed play is stopped at once
    while box.visible and time.monotonic() < deadline:
        time.sleep(0.02)
    assert box.calls[0] == "show" and not box.visible
    page.close()


def test_the_deck_hides_the_box_when_the_youtube_song_on_screen_reports_an_error(deck):
    """Belt and braces for RCOMP-1: an error report for the YouTube play on screen hides the box at once,
    whatever the provider does next; an error of an older play does not."""
    box = FakeBox()
    deck.window = box
    deck.send({"cmd": "play", "playId": "p1", "source": "youtube", "videoId": "dQw4w9WgXcQ"})
    assert box.visible
    deck._on_status({"state": "error", "playId": "old", "code": 150})
    time.sleep(0.1)
    assert box.visible
    deck._on_status({"state": "error", "playId": "p1", "code": 150})
    deadline = time.monotonic() + 2
    while box.visible and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not box.visible


# ------------------------------------------------ ON-1, RCOMP-3, second page --

def _http(deck: Deck, method: str, path: str, headers: Optional[dict] = None, body: Optional[bytes] = None):
    import http.client

    c = http.client.HTTPConnection("127.0.0.1", deck.port, timeout=5)
    try:
        c.request(method, path, body=body, headers={"Host": f"localhost:{deck.port}", **(headers or {})})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def test_a_player_page_left_by_an_earlier_start_is_retired_and_never_counts(deck):
    """Review ON-1: the browser an earlier app start left running reconnects its page to the new deck. Without
    this start's launch key it is told to retire (and closes itself), so it never becomes "the player": the
    app opens its own window, and plays never reach two pages."""
    from test_panel_fakes import SSE

    stale = SSE(deck.port, "player", key="key-of-an-earlier-start")
    assert stale.status == 200 and stale.next_data() == {"cmd": "retire"}
    with pytest.raises(EOFError):
        stale.next_data(timeout=2)
    stale.close()
    assert not deck.page_open and deck.player_count == 0
    fresh = SSE(deck.port, "player", key=deck.player_key)
    try:
        deadline = time.monotonic() + 3
        while not deck.page_open and time.monotonic() < deadline:
            time.sleep(0.02)
        assert deck.page_open and deck.player_count == 1
        assert deck.send({"cmd": "play", "playId": "p", "source": "youtube", "videoId": "dQw4w9WgXcQ"}) == 1
    finally:
        fresh.close()


def test_the_spotify_token_needs_the_launch_key_that_get_player_never_serves(deck):
    """Review RCOMP-3: any local process (another Windows account included) could GET /player, scrape the
    X-ND-Token and fetch the Spotify access token. The token endpoint also needs the launch key, which is
    only in the address the app opened its window with (a fragment, never sent to the server)."""
    deck.spotify.enable(lambda: "FAKE-ACCESS-TOKEN")
    st, page = _http(deck, "GET", "/player")
    assert st == 200 and deck.token.encode() in page and deck.player_key.encode() not in page
    forged = {"Sec-Fetch-Site": "same-origin", "X-ND-Token": deck.token}
    st, body = _http(deck, "GET", server.SPOTIFY_TOKEN_PATH, forged)
    assert st == 401 and b"FAKE-ACCESS-TOKEN" not in body
    st, body = _http(deck, "GET", server.SPOTIFY_TOKEN_PATH, {**forged, "X-ND-Key": deck.player_key})
    assert st == 200 and json.loads(body) == {"accessToken": "FAKE-ACCESS-TOKEN"}


def test_another_site_cannot_open_a_second_player_page(deck):
    """A website's window.open of the player address (or an old link) became a second player page, took the
    in-app Spotify device over and left its dead device id behind when it closed. Another site's navigation
    never gets /player, and a page without the launch key never registers as a player."""
    for site in ("cross-site", "same-site"):
        st, body = _http(deck, "GET", "/player", {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate"})
        assert st == 403 and deck.token.encode() not in body
    for site in ("none", "same-origin"):  # the app's own launch, and the page reloading itself
        assert _http(deck, "GET", "/player", {"Sec-Fetch-Site": site})[0] == 200
    from test_panel_fakes import SSE

    deck.spotify.enable(lambda: "T")
    hidden = SSE(deck.port, "player", key=deck.player_key)
    try:
        deadline = time.monotonic() + 3
        while not deck.page_open and time.monotonic() < deadline:
            time.sleep(0.02)
        deck._on_status({"state": "spotify", "event": "ready", "deviceId": "dev-hidden"})
        popup = SSE(deck.port, "player")  # no key: retired, never a player
        assert popup.next_data() == {"cmd": "retire"}
        popup.close()
        deck.spotify.verify_device("dev-hidden", deck.spotify.ready_seq, listed=True)
        assert deck.player_count == 1 and deck.spotify.wait_ready(0) == "dev-hidden"
    finally:
        hidden.close()


def test_quit_ends_the_player_browser_this_start_launched(tmp_path, monkeypatch):
    """Review ON-1: Deck.stop() only asked the window manager to close the window, so without it (or when the
    window was never found) the browser kept running, held the player profile and its page became the next
    start's player. A browser still running at quit is ended with its process tree; only a process the deck
    launched itself (a real Popen), never anything else."""
    import subprocess

    procs: list = []

    def launcher(args):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        return p

    monkeypatch.setattr(server, "find_browser", lambda kind="edge": f"C:/Fake/{kind}.exe")
    d = Deck(port=0, heartbeat_sec=0.2, launcher=launcher, url_opener=lambda u: False).start()
    try:
        d.configure_player(browser="chrome", profile_dir=tmp_path / "deck-browser")
        d.open_player()
    finally:
        d.stop()
    assert len(procs) == 1 and procs[0].wait(timeout=10) is not None
    # a stand-in that is not a Popen (a test's fake, or nothing) is never touched
    d2 = Deck(port=0, launcher=lambda a: SimpleNamespace(pid=4, poll=lambda: None), url_opener=lambda u: False)
    d2.start()
    d2._player_proc = SimpleNamespace(pid=4, poll=lambda: None)
    d2.stop()  # no error, no kill attempt


def test_the_player_page_sends_its_launch_key_and_retires_when_stale():
    """player.js: the key comes from the window's address fragment; it goes with the event stream and the
    Spotify token request; "retire" (or a 401 from the app) stops everything, never reconnects and closes."""
    js = (server.STATIC_DIR / "player.js").read_text(encoding="utf-8")
    assert "location.hash" in js and '"/events?client=player&k=" + encodeURIComponent(KEY)' in js
    assert '"X-ND-Key": KEY' in js
    assert 'case "retire": retire(); break;' in js and "function retire()" in js
    assert "window.close()" in js and "if (retired) return;" in js
    assert "r.status === 401" in js


# ------------------------------------------------- the player window manager --

class FakeWin32:
    """Fake user32 for deck/window.PlayerWindow on a fake clock: ``script(polls)`` moves the world on."""
    DESKTOP, GAME, PLAYER = 777, 100, 900

    def __init__(self, script) -> None:
        self.fg = self.DESKTOP
        self.windows: list = []
        self.alive = {self.DESKTOP, self.GAME}
        self.pids = {self.DESKTOP: 1, self.GAME: 4242}
        self.calls: list = []
        self.polls = 0
        self.script = script
        self.pos: list = []
        self.monitor = (0, 0, 3840, 2160)
        self.scale = 3.0
        self.size = (1260, 1140)

    def tick(self) -> None:
        self.polls += 1
        self.script(self, self.polls)

    def top_windows(self):
        return list(self.windows)

    def parents(self):
        return {51: 50}

    def is_window(self, h):
        return h in self.alive

    def window_size(self, h):
        return self.size

    def get_exstyle(self, h):
        return 0x00040000

    def set_exstyle(self, h, s):
        pass

    def set_pos(self, hwnd, after, x, y, cx, cy, flags):
        self.pos.append((x, y, flags))
        return True

    def close(self, h):
        pass

    def monitor_rect(self, h):
        return self.monitor

    def dpi_scale(self, h):
        return self.scale

    def foreground(self):
        return self.fg

    def window_pid(self, h):
        return self.pids.get(h, 0)

    def set_foreground(self, h):
        self.calls.append(("SetForegroundWindow", h))
        self.fg = h
        return True


def test_the_hand_back_goes_to_the_game_when_the_player_switched_to_it_while_chrome_started():
    """Chromium activates the window it creates; the hand-back gave the foreground to the window in front
    before the launch, even when the player had clicked into the game meanwhile (the check passed while the
    game was not in front). It goes to the window Chromium took the foreground from."""
    from needledropper.deck import window as W

    def script(w, polls):
        if polls == 3:
            w.fg = w.GAME  # the player clicks back into World of Warcraft while Chrome starts
        if polls == 6:
            w.windows = [(w.PLAYER, 51, W.CHROME_CLASS, "NeedleDropper", True, False)]
            w.alive.add(w.PLAYER)
            w.pids[w.PLAYER] = 51
            w.fg = w.PLAYER  # Chromium's creation activation

    win32 = FakeWin32(script)
    t = [0.0]

    def sleep(s):
        t[0] += s
        win32.tick()

    pw = W.PlayerWindow(win32, clock=lambda: t[0], sleep=sleep, game_window=lambda: None)
    assert pw.adopt(50, prev_foreground=win32.DESKTOP)
    assert ("SetForegroundWindow", win32.GAME) in win32.calls
    assert ("SetForegroundWindow", win32.DESKTOP) not in win32.calls
    assert win32.fg == win32.GAME and pw.took_foreground is True and pw.handed_back is True


def test_the_youtube_box_keeps_clear_of_the_beacon_and_moves_at_once_when_the_corner_changes():
    """DESIGN-v1.2.1 3.9 (was not built): the box sits in the game window's client area, and a box that would
    cover the beacon's capture rectangle (the top-left 512 x 48 physical pixels) moves 16 px (DPI-scaled)
    below it -- the "Top left (below the beacon)" option said so, but the box covered the strip, so frames
    sent during a YouTube song were not read. A corner change while the box is on screen moves it at once,
    with SWP_NOACTIVATE."""
    from needledropper.deck import window as W

    assert W.corner_position("topleft", (0, 0, 1920, 1080), (420, 380), 16, (512, 48)) == (16, 64)
    assert W.corner_position("topright", (0, 0, 1920, 1080), (420, 380), 16, (512, 48)) == (1484, 16)
    assert W.corner_position("topright", (0, 0, 800, 600), (420, 380), 16, (512, 48)) == (364, 64)  # narrow
    assert W.corner_position("bottomleft", (0, 0, 1920, 1080), (420, 380), 16, (512, 48)) == (16, 684)
    assert W.corner_position("topleft", (0, 0, 1920, 1080), (420, 380)) == (16, 16)  # no beacon given: as before

    win32 = FakeWin32(lambda w, polls: None)
    win32.alive.add(win32.PLAYER)
    win32.scale = 1.0
    win32.size = (420, 380)
    win32.client_rect = lambda hwnd: (100, 50, 1380, 770)  # a windowed game: its client area, not the monitor
    pw = W.PlayerWindow(win32, corner="topleft", game_window=lambda: win32.GAME)
    pw.hwnd = win32.PLAYER
    assert pw.target() == (116, 114)  # below the beacon rectangle at the client area's top-left
    pw.corner = "bottomright"
    assert pw.target() == (1380 - 16 - 420, 770 - 16 - 380)
    win32.scale = 3.0
    win32.size = (1260, 1140)
    win32.client_rect = lambda hwnd: (0, 0, 3840, 2160)  # the owner's 4K TV at 300 %: borderless game
    pw.corner = "topleft"
    assert pw.target() == (48, 48)  # 48 px margin: already clear of the 48 px tall rectangle

    d = Deck(port=0, launcher=lambda a: None, url_opener=lambda u: False)
    d.window = pw
    pw.corner = "topright"
    assert pw.show()
    win32.pos.clear()
    d.configure_player(corner="bottomleft")
    assert win32.pos and win32.pos[-1][:2] == (48, 2160 - 48 - 1140)
    assert win32.pos[-1][2] & W.SWP_NOACTIVATE and not win32.pos[-1][2] & W.SWP_SHOWWINDOW
    pw.hide()
    win32.pos.clear()
    d.configure_player(corner="topright")
    assert win32.pos == []  # a hidden box just takes the new corner the next time it shows


# ---------------------------------------------------------------- RP-2, RP-4 --

def _rig(order="spotify-youtube"):
    from test_v12_engine_flows import SP_ID, FakeSpotify, Rig

    r = Rig(spotify=FakeSpotify(matches={"9 to 5": (f"spotify:track:{SP_ID}", "9 to 5", "Dolly Parton", 165000)}))
    r.engine.set_music_order(order)
    return r


DUROTAR_FR = {"drop_id": "zone:1411", "artist": "Dolly Parton", "title": "9 to 5", "label": "Durotar",
              "youtube": "d_vPJjqWxZM"}


@pytest.mark.parametrize("arm_to_fire_s", [5, 26, 27, 60, 419, 1200])
def test_a_lost_fire_starts_late_however_long_after_its_arm_it_came(arm_to_fire_s):
    """Review RP-2: the D3 late start only trusted an ARM seen within 30 s, but the Director fires a place
    when the player may (the 10-minute cooldown, combat, a taxi): Razor Hill armed at 185 s fired at 605 s.
    The ARM is judged by sequence (the HELLO comes after it, no CANCEL or FIRE of the place since)."""
    from needledropper.engine import ARM_TTL_SEC

    r = _rig()
    pk = p2.place_key_for("zone:1411")
    r.send(ET.ARM, seq=5, **DUROTAR_FR)
    r.send(ET.ARM, seq=5, place_key=pk)          # the placeKey-only ARM: a duplicate key
    r.clock.advance(arm_to_fire_s)                # the FIRE (seq 6) is covered on screen ...
    r.clock.advance(3.5)
    r.send(ET.HELLO, seq=6, flags=Flags.DROP_ACTIVE, place_key=pk)  # ... its steady HELLO is read
    assert len(r.sp.of("play")) + len(r.yt.of("play")) == 1 and r.engine.is_playing
    assert arm_to_fire_s < ARM_TTL_SEC


def test_the_chat_log_arm_after_its_fire_never_rearms_the_playing_place():
    """Review RP-4: the chat log delivers [HELLO, ARM, FIRE] in one batch after the beacon's FIRE; its ARM
    copy (outside the dedupe window) re-armed the playing place, and once the song had ended a later HELLO
    with that placeKey started it a second time."""
    r = _rig()
    pk = p2.place_key_for("zone:1411")
    r.send(ET.ARM, seq=2, **DUROTAR_FR)
    r.clock.advance(20)
    r.send(ET.FIRE, seq=3, flags=Flags.DROP_ACTIVE, **DUROTAR_FR)
    assert len(r.sp.of("play")) == 1
    r.clock.advance(0.75)
    r.send(ET.ARM, seq=2, transport="chatlog", **DUROTAR_FR)   # the chat batch: a late ARM copy ...
    r.send(ET.FIRE, seq=3, flags=Flags.DROP_ACTIVE, transport="chatlog", **DUROTAR_FR)  # ... and the duplicate
    assert pk not in r.engine._armed2
    r.engine._stop_current(0, "provider reports end")           # the song ended early (a shorter video)
    r.clock.advance(20)
    r.send(ET.HELLO, seq=3, flags=Flags.DROP_ACTIVE, place_key=pk)  # the addon still plays the drop
    assert len(r.sp.of("play")) == 1 and not r.engine.is_playing
    # a later ARM of the same place (a new seq) arms it again
    r.send(ET.ARM, seq=9, **DUROTAR_FR)
    assert pk in r.engine._armed2


# ------------------------------------------------------------------- RCOMP-7 --

def test_youtube_first_never_waits_for_the_pre_warms_spotify_search():
    """Review RCOMP-7: with "YouTube, then Spotify" a FIRE that arrived during its ARM's Spotify search (ARM and
    FIRE in one chat-log batch, a portal, a slow or rate-limited Spotify) waited up to 3 s before YouTube --
    which plays first and needs no search -- started."""
    from test_v12_engine_flows import YT_ID, FakeSpotify, Rig

    gate = threading.Event()  # the Spotify search does not answer for a while
    r = Rig(spotify=FakeSpotify(gate=gate, matches={"9 to 5": ("spotify:track:" + "b" * 22, "9 to 5", "Dolly", 165000)}),
            prewarm_async=True)
    try:
        r.engine.set_music_order("youtube-spotify")
        song = dict(drop_id="zone:1411", artist="Dolly Parton", title="9 to 5", label="Durotar", youtube=YT_ID)
        r.send(ET.ARM, **song)
        deadline = time.monotonic() + 3
        while not r.sp.of("resolve") and time.monotonic() < deadline:
            time.sleep(0.01)
        t0 = time.monotonic()
        r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, **song)  # during the search
        waited = time.monotonic() - t0
        assert r.yt.of("play") and waited < 1.0
        # Spotify first still waits for the search it needs
        r2 = Rig(spotify=FakeSpotify(matches={"9 to 5": ("spotify:track:" + "b" * 22, "9 to 5", "Dolly", 165000)}),
                 prewarm_async=True)
        try:
            r2.send(ET.ARM, **song)
            r2.send(ET.FIRE, flags=Flags.DROP_ACTIVE, **song)
            assert r2.sp.of("play") and not r2.yt.of("play")
        finally:
            r2.engine.stop()
    finally:
        gate.set()
        r.engine.stop()


# ------------------------------------------------------------------ INGAME-4 --

def test_the_first_drop_after_a_choice_on_the_chat_log_plays_with_the_new_order(tmp_path, _needledropper_home):
    """Review INGAME-4: the chat log delivers a PREFS-only CONFIG in one batch with the next drop's ARM and FIRE.
    The music worker set the order only after it wrote config.json, so the engine routed that drop with the
    old order (Spotify although the player had just chosen YouTube only)."""
    from test_integration_e2e import RecordingYouTube
    from test_panel_fakes import FakeWinreg, make_fake_addon, make_fake_wow, no_registry, wait_for
    from test_v12_engine_flows import FakeSpotify

    from needledropper import config, tray
    from needledropper.panel_api import Companion

    home = Path(_needledropper_home)
    wow = make_fake_wow(tmp_path / "WoW")
    cfg_file = home / "config.json"
    cfg_file.write_text(json.dumps({"wowRoot": str(wow), "deck": {"port": 0}, "spotify": {"device": "connect"},
                                    "transport": {"pixel": {"enabled": False}, "chatlog": {"enabled": False}}}),
                        encoding="utf-8")
    cfg = config.load_config(cfg_file)
    d = Deck(port=0, heartbeat_sec=0.2, url_opener=lambda u: None, launcher=lambda a: None).start()
    sp, yt = FakeSpotify(), RecordingYouTube()
    app = Companion(cfg, config.paths_from_config(cfg), deck=d, config_file=cfg_file,
                    provider_factory=lambda c, p, dk: [sp, yt], transport_factory=None, opener=lambda t: None,
                    game_running=lambda p: False, wow_registry=no_registry, wow_default=str(tmp_path / "NoWoW"),
                    addon_source=make_fake_addon(tmp_path, "1.2.1"), autostart_winreg=FakeWinreg(),
                    notifier=tray.Notifier(), housekeeping_s=0.05)
    app.start()
    try:
        assert wait_for(lambda: app.engine is not None) and app.engine.music_order == "spotify-youtube"

        def frame(etype, **kw):
            return p2.decode_frame(p2.encode_frame(p2.build_frame(etype, **kw)))

        song = dict(drop_id="zone:1411", spotify="4w3tQBXhn5345eUXDGBWZG", youtube="d_vPJjqWxZM",
                    artist="Dolly Parton", title="9 to 5", label="Durotar")
        app.submit(frame(ET.CONFIG, seq=10, prefs={"order": "youtube", "corner": "topright", "fade": True,
                                                   "resume": True}), "chatlog")
        app.submit(frame(ET.ARM, seq=11, **song), "chatlog")
        app.submit(frame(ET.FIRE, seq=12, flags=Flags.DROP_ACTIVE, **song), "chatlog")
        assert app.music.wait_idle(5)
        assert wait_for(lambda: any(c[0] == "play" for c in sp.calls + yt.calls), timeout=5)
        assert [c[0] for c in sp.calls if c[0] == "play"] == [] and any(c[0] == "play" for c in yt.calls)
        assert json.loads(cfg_file.read_text(encoding="utf-8"))["music"]["order"] == "youtube"
    finally:
        app.stop()
        d.stop()


# ------------------------------------------------- RCOMP-2, RCOMP-5, RCOMP-6 --

@pytest.fixture
def fast_app(monkeypatch):
    from needledropper.providers import spotify as sp

    monkeypatch.setattr(sp, "APP_READY_WAIT_S", 0.3)
    monkeypatch.setattr(sp, "APP_REGISTRATION_WAIT_S", 0.3)
    monkeypatch.setattr(sp, "APP_START_TIMEOUT_S", 0.5)
    monkeypatch.setattr(sp, "APP_API_CHECK_S", 0.2)
    monkeypatch.setattr(sp, "APP_STOP_SLACK_S", 0.3)
    monkeypatch.setattr(sp, "RESTORE_HANDOVER_S", 0.3)
    return sp


def test_back_to_back_in_app_drops_resume_the_players_own_music_once_at_the_end(fast_app, tmp_path):
    """Review RCOMP-2: B's ARM read GET /me/player while A played in NeedleDropper's own player, so B's snapshot
    was A; A's restore resumed the phone between the drops (a blip), B's PUT took it over again, and at B's end
    nothing was resumed. B inherits A's snapshot (and the duty to restore it); A restores nothing."""
    from test_spotify_fakes import PHONE, make_drop, player_state, track
    from test_spotify_inapp import SDK_DEVICE, make_app_rig

    prev = track("prev", "Previous Song", ["Someone"], duration=300_000)
    a = track("aaaa", "Song A", ["Artist A"], duration=200_000)
    b = track("bbbb", "Song B", ["Artist B"], duration=200_000)
    ctx = {"type": "playlist", "uri": "spotify:playlist:" + ("pl" + "0" * 20)}
    rig = make_app_rig(tmp_path / "app")
    nd = {"id": SDK_DEVICE, "name": "NeedleDropper", "type": "Computer", "is_active": True, "is_restricted": False,
          "volume_percent": 100}
    world = {"now": player_state(prev, 30_000, device=PHONE, context=ctx)}
    rig.http.add("GET", "/v1/me/player", lambda call: (200, world["now"]))

    def on_play(call):
        dev, body = call.query.get("device_id"), call.json
        if dev == SDK_DEVICE:
            item = a if body["uris"][0] == a["uri"] else b
            world["now"] = player_state(item, body["position_ms"] + 400, device=nd)
            rig.page.tick(int(body["position_ms"]) + 400, uri=body["uris"][0])
        else:
            world["now"] = player_state(prev, body.get("position_ms", 0), device=PHONE, context=ctx)
        return (204, None)

    rig.http.add("PUT", "/v1/me/player/play", on_play)
    rig.http.add("PUT", "/v1/me/player/pause", (204, None))
    drop_a = make_drop(id="zone:1", spotify=a["uri"], startMs=0, durationMs=60_000)
    drop_b = make_drop(id="zone:2", spotify=b["uri"], startMs=0, durationMs=60_000)
    rig.provider.prepare(drop_a)
    ha = rig.provider.play(drop_a, fade_in_ms=0)
    rig.provider.prepare(drop_b)          # B's ARM while A plays in NeedleDropper's player
    rig.provider.stop(ha, fade_out_ms=0)  # the engine stops A when B fires ...
    hb = rig.provider.play(drop_b, fade_in_ms=0)  # ... and plays B at once
    rig.provider.stop(hb, fade_out_ms=0)
    plays = [(c.query.get("device_id"), (c.json.get("uris") or [c.json.get("context_uri")])[0])
             for c in rig.http.find("PUT", "/v1/me/player/play")]
    assert plays == [(SDK_DEVICE, a["uri"]), (SDK_DEVICE, b["uri"]), ("dev-phone", ctx["uri"])]
    time.sleep(0.5)  # past A's hand-over wait: still no second restore
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 3

    # A ends with B armed, and B does not follow at once: A's restore runs after the hand-over wait
    rig2 = make_app_rig(tmp_path / "app2")
    world["now"] = player_state(prev, 30_000, device=PHONE, context=ctx)
    rig2.http.add("GET", "/v1/me/player", lambda call: (200, world["now"]))
    rig = rig2
    rig2.http.add("PUT", "/v1/me/player/play", on_play)
    rig2.http.add("PUT", "/v1/me/player/pause", (204, None))
    rig2.provider.prepare(drop_a)
    ha = rig2.provider.play(drop_a, fade_in_ms=0)
    rig2.provider.prepare(drop_b)
    rig2.provider.stop(ha, fade_out_ms=0)
    deadline = time.monotonic() + 3
    while len(rig2.http.find("PUT", "/v1/me/player/play")) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    last = rig2.http.find("PUT", "/v1/me/player/play")[-1]
    assert last.query.get("device_id") == "dev-phone"


def test_a_short_not_ready_blip_is_not_no_player_and_never_reconnects_the_playing_song(fast_app, tmp_path,
                                                                                         monkeypatch):
    """Review RCOMP-5: one not_ready of a page whose SDK was ready read as no-player at once ("install Google
    Chrome..."), and the next sync (the 30 s watch, ARM, FIRE) reconnected the SDK, cutting the song."""
    from test_spotify_fakes import make_drop
    from test_spotify_inapp import SDK_DEVICE, make_app_rig, plays_and_ticks

    monkeypatch.setattr(fast_app, "SDK_READY_GRACE_S", 0.5)
    rig = make_app_rig(tmp_path / "app", cfg={"restorePrevious": False})
    plays_and_ticks(rig)
    h = rig.provider.play(make_drop(startMs=0, durationMs=120_000), fade_in_ms=0)
    time.sleep(0.6)  # the app has been up longer than the (scaled) grace
    rig.page.report("not_ready", deviceId=SDK_DEVICE)
    assert rig.provider.music_state() == "connected" and rig.provider.unavailable_reason() is None
    before = list(rig.page.ops())
    rig.provider.sync_sdk()
    assert rig.page.ops()[len(before):] == [] and rig.provider.poll(h) == "playing"
    time.sleep(0.6)  # offline for longer than the grace: now it is no-player, but the song is not cut
    assert rig.provider.music_state() == "no-player"
    rig.provider.sync_sdk()
    assert "reload" not in rig.page.ops()[len(before):]
    rig.provider.stop(h, fade_out_ms=0)


def test_an_sdk_that_never_gets_ready_stays_no_player_instead_of_flapping(fast_app, tmp_path, monkeypatch):
    """Review RCOMP-6: every re-check's reconnect restarted the 30 s grace, so a player that never got ready
    (Spotify's wss blocked) counted as connected half of the time: the pre-warm picked Spotify, a FIRE waited
    for it, and the status alternated between "connected" and "needs Google Chrome". A connect() that resolved
    false is no-player too."""
    from test_spotify_inapp import PageDeck, make_app_rig

    monkeypatch.setattr(fast_app, "SDK_READY_GRACE_S", 0.3)
    monkeypatch.setattr(fast_app, "RECHECK_INTERVAL_S", 0.6)
    rig = make_app_rig(tmp_path / "app", page=PageDeck(ready=False))
    t0 = time.monotonic()
    samples = []
    while time.monotonic() - t0 < 2.5:
        rig.provider.sync_sdk()
        samples.append((time.monotonic() - t0, rig.provider.available()))
        time.sleep(0.05)
    late = [ok for t, ok in samples if t > 0.4]
    assert late and not any(late)  # after the grace: no-player, whatever the re-checks do
    assert rig.page.ops().count("reload") >= 2  # it is still re-checked
    rig2 = make_app_rig(tmp_path / "app2", page=PageDeck(ready=False))
    rig2.page.report("connect", ok=False)
    assert rig2.provider.music_state() == "no-player"


# ------------------------------------------------------------------- RCOMP-4 --

def _ipv6_loopback() -> bool:
    import socket

    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _ipv6_loopback(), reason="no IPv6 loopback on this PC")
def test_the_deck_also_listens_on_the_ipv6_loopback_so_localhost_is_fast_and_cannot_be_squatted():
    """Review RCOMP-4: the player page is on http://localhost:<port> (D12), localhost resolves to ::1 first,
    and the deck listened on 127.0.0.1 only: every request of the page waited about 2 s for the refused [::1]
    connect, and a program listening on [::1]:<port> would have served the player page instead."""
    import socket

    d = Deck(port=0, launcher=lambda a: None, url_opener=lambda u: False).start()
    try:
        assert d._server6 is not None and d._server6.server_address[1] == d.port
        t0 = time.monotonic()
        c = socket.create_connection(("::1", d.port), timeout=5)
        c.sendall(f"GET /healthz HTTP/1.0\r\nHost: localhost:{d.port}\r\n\r\n".encode())
        reply = c.recv(4096)
        c.close()
        assert reply.startswith(b"HTTP/1.0 200") and time.monotonic() - t0 < 1.0
    finally:
        d.stop()
    # another program on [::1]:<port>: the deck does not start there (like a busy port)
    squat = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    squat.bind(("::1", 0))
    squat.listen(1)
    port = squat.getsockname()[1]
    try:
        with pytest.raises(OSError):
            Deck(port=port, launcher=lambda a: None, url_opener=lambda u: False).start()
        # a free port (port 0) just picks another one
        d2 = Deck(port=0, launcher=lambda a: None, url_opener=lambda u: False).start()
        assert d2.port != port or d2._server6 is not None
        d2.stop()
    finally:
        squat.close()


# ---------------------------------------------------------------------- ON-2 --

def test_a_1_2_0_addon_with_the_1_2_1_app_says_which_part_is_too_old(tmp_path, _needledropper_home):
    """Review ON-2: a 1.2.0 addon (installed by hand or by CurseForge: owner "other") and the 1.2.1 app can't
    read each other (D1/D2), yet the Connect page said "Ready" and the link check just waited. The card says
    the addon is too old (for every owner), the tray line says so while there is no link, and the pixel
    transport's detail for an unreadable strip suggests an addon/app mismatch."""
    from test_panel_fakes import make_shell

    ns = make_shell(tmp_path, Path(_needledropper_home), addon_version="1.2.1", start=True)
    try:
        dest = ns.app.paths.addon_dir
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "NeedleDropper.toc").write_text("## Interface: 16001\n## Title: NeedleDropper\n## Version: 1.2.0\n"
                                                "Core.lua\n", encoding="utf-8")
        (dest / "Core.lua").write_text("local ADDON, ns = ...\n", encoding="utf-8")
        st, state = ns.call("GET", "/api/state")
        a = state["addon"]
        assert a["installed"] and a["version"] == "1.2.0" and a["owner"] == "other"
        assert a["tooOld"] is True and a["minVersion"] == "1.2.1"
        ns.app._too_old_cache = None
        assert "Update the NeedleDropper addon to 1.2.1" in ns.app.status_line()
        (dest / "NeedleDropper.toc").write_text("## Interface: 16001\n## Version: 1.2.1\nCore.lua\n", encoding="utf-8")
        st, state = ns.call("GET", "/api/state")
        assert state["addon"]["tooOld"] is False
        ns.app._too_old_cache = None
        assert "Update the NeedleDropper addon" not in ns.app.status_line()
    finally:
        ns.close()
    js = (server.STATIC_DIR / "connect.js").read_text(encoding="utf-8")
    assert "addon.tooOld" in js and "must be updated together" in js

    from needledropper.transport.pixel import PixelTransport

    t = PixelTransport.__new__(PixelTransport)
    t.enabled, t._hwnd, t._minimized, t._beacon_visible, t._strip = True, 1, False, False, None
    t._last_report = SimpleNamespace(header=object(), reason="payload invalid")
    assert "older or newer NeedleDropper addon" in t._detail()


# ---------------------------------------------------------------------- ON-3 --

def test_an_account_not_on_the_user_management_list_says_so_at_once_in_the_app_and_the_game(
        tmp_path, _needledropper_home):
    """Review ON-3: Spotify lets an account that is not on the developer app's User Management list sign in,
    and /v1/me answers 403. The Connect page said "Connected" and the game "Spotify + YouTube ready" until the
    first song failed; then the game said "sign in again", which can't fix it. The card says "Account not
    allowed" at once, and the game gets state "error" with problem "allowlist" (D17 values unchanged)."""
    import http.client as hc
    import socket as so
    from urllib.parse import parse_qs, urlsplit

    from test_panel_fakes import make_shell, wait_for
    from test_spotify_fakes import FakeHTTP

    from needledropper import gamedata
    from needledropper.providers import spotify_auth as sa

    s = so.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    fake = FakeHTTP()
    fake.add("POST", "/api/token", (200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                                          "scope": " ".join(sa.SCOPES)}))
    fake.add("GET", "/v1/me", (403, {"error": {"status": 403, "message": "User not registered in the Developer Dashboard"}}))
    ns = make_shell(tmp_path, Path(_needledropper_home), cfg_extra={"spotify": {"redirectPort": port}}, spotify_http=fake)
    try:
        st, data = ns.call("POST", "/api/spotify/connect", {"clientId": "0123456789abcdef0123456789abcdef"})
        q = {k: v[0] for k, v in parse_qs(urlsplit(data["authorizeUrl"]).query).items()}
        parts = urlsplit(q["redirect_uri"])
        c = hc.HTTPConnection(parts.hostname, parts.port, timeout=5)
        c.request("GET", f"{parts.path}?code=C&state={q['state']}")
        c.getresponse().read()
        c.close()
        assert wait_for(lambda: ns.call("GET", "/api/state")[1]["spotify"]["state"] == "allowlist", timeout=8)
        assert ns.app._game_status()["spotify"] == "error"
        music = ns.app._game_music()["spotify"]
        assert music["state"] == "error" and music["problem"] == "allowlist"
    finally:
        ns.close()
    clean = gamedata.normalize_companion_data({"schema": 1, "music": {"spotify": {"state": "error",
                                                                                 "problem": "allowlist"}}})
    assert clean["music"]["spotify"]["problem"] == "allowlist"
    with pytest.raises(gamedata.GameDataError):
        gamedata.normalize_companion_data({"schema": 1, "music": {"spotify": {"problem": "bogus"}}})


def test_sdk_reports_need_the_launch_key_too(deck):
    """A local process that read the X-ND-Token from /player could post SDK reports and point the app at a
    device of its own (the second-page finding); the SDK's reports need the launch key."""
    deck.spotify.enable(lambda: "T")
    body = json.dumps({"state": "spotify", "event": "ready", "deviceId": "dev-forged"}).encode()
    hdrs = {"X-ND-Token": deck.token, "Content-Type": "application/json", "Origin": f"http://localhost:{deck.port}"}
    assert _http(deck, "POST", "/status", hdrs, body)[0] == 401
    assert deck.spotify.wait_ready(0) is None
    assert _http(deck, "POST", "/status", {**hdrs, "X-ND-Key": deck.player_key}, body)[0] == 204
    assert deck.spotify.wait_ready(0) is None
    deck.spotify.verify_device("dev-forged", deck.spotify.ready_seq, listed=True)
    assert deck.spotify.wait_ready(0) == "dev-forged"
    js = (server.STATIC_DIR / "player.js").read_text(encoding="utf-8")
    assert '"X-ND-Token": TOKEN, "X-ND-Key": KEY' in js


def test_a_player_window_that_was_there_before_the_launch_is_never_adopted_hidden_or_closed():
    """Found by the live check of the fixed exe (2026-11-06): another app's player window that was already on
    screen -- the owner's running 1.2.0 app leaves its "NeedleDropper" player window visible -- matched the
    title rule, so the new app hid and restyled it at once (14 ms after the launch), managed it instead of its
    own, and sent it WM_CLOSE at quit. Windows that exist before the launch are never candidates."""
    from needledropper.deck import window as W

    OTHER, OTHER_PID = 555, 33460

    def script(w, polls):
        if polls == 4:  # our browser's own window appears
            w.windows.append((w.PLAYER, 51, W.CHROME_CLASS, "NeedleDropper", True, False))
            w.alive.add(w.PLAYER)
            w.pids[w.PLAYER] = 51

    win32 = FakeWin32(script)
    win32.windows = [(OTHER, OTHER_PID, W.CHROME_CLASS, "NeedleDropper", True, False)]  # 1.2.0's, visible
    win32.alive.add(OTHER)
    win32.pids[OTHER] = OTHER_PID
    touched: list = []
    win32.set_exstyle = lambda h, s: touched.append(("style", h))
    real_set_pos = win32.set_pos

    def set_pos(hwnd, *a):
        touched.append(("pos", hwnd))
        return real_set_pos(hwnd, *a)

    win32.set_pos = set_pos
    closed: list = []
    win32.close = closed.append
    t = [0.0]

    def sleep(s):
        t[0] += s
        win32.tick()

    pw = W.PlayerWindow(win32, clock=lambda: t[0], sleep=sleep, game_window=lambda: None)
    d = Deck(port=0, launcher=lambda args: SimpleNamespace(pid=50, poll=lambda: None), url_opener=lambda u: False)
    d.window = pw
    before = pw.windows_now()
    assert before == {OTHER}
    assert pw.adopt(50, ignore=before)
    assert pw.hwnd == win32.PLAYER and all(h == win32.PLAYER for _k, h in touched)
    pw.close()
    assert closed == [win32.PLAYER]
    # and the deck passes the windows of the moment of the launch
    seen: dict = {}
    d.window = SimpleNamespace(foreground=lambda: None, windows_now=lambda: {OTHER},
                               adopt=lambda pid, **kw: seen.update(kw), corner="topright", position=None)
    d._adopt_later(SimpleNamespace(pid=50), None, d._windows_now())
    deadline = time.monotonic() + 2
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen == {"ignore": {OTHER}}


def test_between_songs_the_player_is_parked_off_every_monitor_not_hidden():
    """D14's fallback, from the live check of the fixed exe (2026-11-06): hidden with SWP_HIDEWINDOW, the Web
    Playback SDK got ready and Spotify accepted the song on device NeedleDropper, but the position never moved.
    The window is parked instead: shown, beyond the virtual screen's top-left corner (no monitor shows any of
    it), not topmost, never activated; the YouTube box goes topmost to its corner and back."""
    from needledropper.deck import window as W

    def script(w, polls):
        if polls == 2:
            w.windows = [(w.PLAYER, 51, W.CHROME_CLASS, "NeedleDropper", True, False)]
            w.alive.add(w.PLAYER)
            w.pids[w.PLAYER] = 51

    win32 = FakeWin32(script)
    win32.virtual_screen = lambda: (-1920, 0, 5760, 2160)  # a second monitor left of the 4K one
    t = [0.0]

    def sleep(s):
        t[0] += s
        win32.tick()

    pw = W.PlayerWindow(win32, clock=lambda: t[0], sleep=sleep, game_window=lambda: None)
    assert pw.adopt(50)
    x, y, flags = win32.pos[-1]
    w, h = win32.size
    assert x + w <= -1920 and y + h <= 0  # touches no monitor
    assert flags & W.SWP_SHOWWINDOW and flags & W.SWP_NOACTIVATE and not flags & W.SWP_HIDEWINDOW
    assert not pw.shown
    assert pw.show() and win32.pos[-1][0] > 0 and win32.pos[-1][2] & W.SWP_NOACTIVATE  # the YouTube corner
    assert pw.shown
    assert pw.hide() and win32.pos[-1][:2] == (x, y) and not pw.shown  # parked again
