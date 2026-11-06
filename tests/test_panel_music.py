"""The companion's music services wiring (DESIGN-v1.2.1 3.1, 3.2, 3.5, 3.10, 3.12): frames from the
transports (and the pixel link's baseline) reach MusicConfig before the pause gate; the choices go to
config.json and apply live (engine order and fades, the Spotify provider's fade, the box corner); the
``music`` block for the game; /api/state music and the Connect page's Test / Sign-in endpoints; and
Connect Spotify from the game with another Client ID, which keeps a working sign-in until the new one
succeeds. A real Companion on a real deck (ephemeral port), with a fake engine and fake transports."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import SSE, EngineFactory, FakeEngine, make_shell, raw_config, wait_for  # noqa: E402
from test_panel_spotify import approve, free_port, token_routes  # noqa: E402
from test_spotify_fakes import FakeHTTP, save_tokens  # noqa: E402

from needledropper import cli, config, gamedata as gd, music_config as mc, protocol2 as p2, tray  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402
from needledropper.providers import spotify_auth as sa  # noqa: E402

CID = "0123456789abcdef0123456789abcdef"
OTHER = "ffffffffffffffffffffffffffffffff"


class MusicEngine(FakeEngine):
    """The fake engine plus what the music services use: the order and play_now."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.orders: list[str] = []
        self.played: list[tuple] = []

    def set_music_order(self, order: str) -> str:
        self.orders.append(order)
        return order

    def play_now(self, drop: Any, *, test: bool = True, only: Optional[str] = None) -> None:
        self.played.append((drop.id, only))


def shell(tmp_path, home, **kw):
    engines = EngineFactory(MusicEngine)
    s = make_shell(tmp_path, Path(home), engine_factory=engines, **kw)
    s.engines = engines
    return s


def frame(etype: ET = ET.CONFIG, **fields: Any) -> p2.Frame:
    return p2.decode_frame(p2.encode_frame(p2.build_frame(etype, **fields)))


def music_data(ns) -> dict:
    gd.flush(ns.app.paths.addons_dir)
    return (gd.read_companion_data(ns.app.paths.addons_dir) or {}).get("music") or {}


@pytest.fixture
def ns(tmp_path, _needledropper_home):
    s = shell(tmp_path, _needledropper_home)
    try:
        yield s
    finally:
        s.close()


PREFS = {"order": "youtube", "corner": "bottomleft", "fade": False, "resume": False}


def test_prefs_from_a_transport_reach_config_json_and_apply_live(ns):
    eng = ns.engines.current
    assert eng.orders == ["spotify-youtube"]                       # set at start (1.2.0 behaviour)
    ns.transports.live("pixel").emit(frame(ET.HELLO, seq=1, prefs=PREFS))
    assert ns.app.music.wait_idle(5)
    raw = raw_config(ns.cfg_file)
    assert raw["music"] == {"order": "youtube", "fade": False, "fromGame": True}
    assert raw["deck"]["playerCorner"] == "bottomleft" and raw["deck"]["playerPosition"] == ""
    assert raw["spotify"] == {"restorePrevious": False} and raw["myOwnKey"] == {"keep": True}
    assert eng.orders[-1] == "youtube" and (eng.fade_in_ms, eng.fade_out_ms) == (0, 0)
    assert ns.deck.player_corner == "bottomleft"
    st = ns.call("GET", "/api/state")[1]
    assert {k: st["music"][k] for k in ("order", "corner", "fade", "resume", "fromGame")} == \
        {"order": "youtube", "corner": "bottomleft", "fade": False, "resume": False, "fromGame": True}
    # the game reads the choices in effect back (the echo), and the D17 detail
    music = music_data(ns)
    assert music["prefs"] == {"order": "youtube", "corner": "bottomleft", "fade": False, "resume": False,
                              "fromGame": True}
    assert music["spotify"]["state"] == "not-connected" and music["spotify"]["mode"] == "app"
    assert music["spotify"]["redirectUri"] == "http://127.0.0.1:8321/callback"
    assert music["youtube"]["enabled"] is True and music["youtube"]["player"] in ("chrome", "edge", "none")


def test_prefs_apply_while_paused_and_the_engine_never_sees_them(ns):
    ns.app.set_paused(True)
    ns.transports.live("chatlog").emit(frame(ET.CONFIG, seq=4, prefs={"order": "none"}))
    assert ns.app.music.wait_idle(5)
    assert raw_config(ns.cfg_file)["music"]["order"] == "none"
    assert ns.engines.current.submitted == []                       # the pause gate still holds frames back


def test_the_pixel_links_baseline_frame_delivers_its_prefs(tmp_path, _needledropper_home):
    class BaselineTransport:
        name = "pixel"

        def __init__(self, on_event):
            self.on_event = on_event
            self.on_baseline = None

        def start(self):
            pass

        def stop(self):
            pass

        def status(self):
            return {"running": True, "ok": True, "detail": "fake"}

    made: list = []

    def factory(cfg, paths, on_event):
        made.append(BaselineTransport(on_event))
        return list(made[-1:])

    s = shell(tmp_path, _needledropper_home, transport_factory=factory)
    try:
        t = made[-1]
        assert callable(t.on_baseline)
        t.on_baseline(frame(ET.HELLO, seq=9, prefs={"order": "spotify", "corner": "topleft", "fade": True,
                                                     "resume": True}), "pixel")
        assert s.app.music.wait_idle(5)
        assert raw_config(s.cfg_file)["music"]["order"] == "spotify"
        s.app.restart_transports()                                   # a rebuilt link is hooked again
        assert made[-1] is not t and callable(made[-1].on_baseline)
    finally:
        s.close()


def test_the_pixel_link_reports_each_baseline_once():
    from needledropper.transport.pixel import PixelTransport

    class NoBackend:
        def available(self):
            return True, ""

        def prepare(self):
            return "test"

    calls: list = []
    t = PixelTransport({"enabled": True}, None, lambda e, n: None, backend=NoBackend())
    t.on_baseline = lambda f, n: calls.append((f.seq, n))
    hello = frame(ET.HELLO, seq=1, prefs=PREFS)
    for _ in range(4):
        t.feed(hello)                                                # the baseline, then "same" reads
    assert calls == [(1, "pixel")]
    t.feed(frame(ET.HELLO, seq=2))
    t.feed(frame(ET.HELLO, seq=2))                                  # a new frame is emitted, not a baseline
    assert calls == [(1, "pixel")]
    t.reset_baseline()                                              # the game window was lost and found again
    t.feed(hello)
    assert calls == [(1, "pixel"), (1, "pixel")]


def test_config_defaults_and_normalisation_of_the_music_keys():
    cfg = config.normalize_config(config.deep_merge(config.DEFAULT_CONFIG, {
        "music": {"order": "LOUDEST", "fade": "yes", "fromGame": 1}, "deck": {"playerCorner": "Top-Left"}}))
    assert cfg["music"] == {"order": "spotify-youtube", "fade": True, "fromGame": False}
    assert cfg["deck"]["playerCorner"] == "topleft"
    assert config.normalize_config(config.deep_merge(config.DEFAULT_CONFIG, {"music": "x"}))["music"] == \
        config.DEFAULT_CONFIG["music"]
    # until the game chose, the app plays as 1.2.0 did: fadeInMs / fadeOutMs, Spotify first
    assert config.effective_fades(config.default_config()) == (2500, 3000)
    assert config.music_choices(config.default_config()) == {"order": "spotify-youtube", "corner": "topright",
                                                             "fade": True, "resume": True, "fromGame": False}


def test_the_engine_gets_the_order_and_fades_from_config_at_start(tmp_path, _needledropper_home):
    s = shell(tmp_path, _needledropper_home,
              cfg_extra={"music": {"order": "youtube-spotify", "fade": False, "fromGame": True}, "fadeInMs": 1234})
    try:
        eng = s.engines.current
        assert eng.orders == ["youtube-spotify"] and (eng.fade_in_ms, eng.fade_out_ms) == (0, 0)
        s.call("POST", "/api/config", {"youtube": {"enabled": True}})          # any edit re-applies
        assert s.engines.current.orders[-1] == "youtube-spotify"
    finally:
        s.close()
    assert config.effective_fades({"fadeInMs": 1234, "fadeOutMs": 99, "music": {"fade": True}}) == (1234, 99)


def test_the_spotify_providers_own_fade_follows_the_game_once_it_chose(tmp_path):
    paths = config.paths_from_config(config.default_config())
    base = config.default_config()
    base["providers"] = ["spotify"]
    base["spotify"]["fade"] = False                                 # an advanced user's choice in config.json
    sp = cli.build_providers(base, paths, None)[0]
    assert sp._fade is False                                         # kept while the game has not chosen
    base["music"] = {"order": "spotify-youtube", "fade": True, "fromGame": True}
    assert cli.build_providers(base, paths, None)[0]._fade is True


def test_the_connect_page_buttons_run_the_same_handlers(ns):
    player = SSE(ns.deck.port, "player", key=ns.deck.player_key)  # the hidden player page (with its launch key, ON-1)
    try:
        assert wait_for(lambda: ns.deck.page_open)
        st, data = ns.call("POST", "/api/music/test", {"service": "youtube"})
        assert st == 202 and data["action"] == "test-youtube"
        assert wait_for(lambda: ns.engines.current.played == [("test-youtube", "youtube")])
    finally:
        player.close()
    ns.app.music.on_engine_note({"kind": "playing", "dropId": "test-youtube"})
    assert wait_for(lambda: any(a["result"] == "playing" for a in ns.call("GET", "/api/state")[1]["music"]["actions"]))
    assert ns.call("POST", "/api/music/test", {"service": "tidal"})[0] == 400
    st, data = ns.call("POST", "/api/youtube/signin", {})
    assert st == 202 and data["action"] == "youtube-sign-in"
    assert ns.app.music.wait_idle(5)
    acts = ns.call("GET", "/api/state")[1]["music"]["actions"]
    assert acts[-1]["action"] == "youtube-sign-in" and acts[-1]["source"] == "page"
    # page requests are not written for the game (its "Last request" line is about its own requests)
    assert not music_data(ns).get("actions")


def test_a_request_from_the_game_is_written_for_the_game_and_notified(ns):
    ns.app.music._wall = lambda: 1_762_380_000.0
    ns.transports.live("pixel").emit(frame(ET.CONFIG, seq=3, action={"name": "test-spotify", "at": 1_762_380_000}))
    assert ns.app.music.wait_idle(5)
    # the fake chain's Spotify is a plain provider: available, so the test was started
    assert ns.engines.current.played == [("test-spotify", "spotify")]
    ns.app.music.on_engine_note({"kind": "noSource", "dropId": "test-spotify", "reasons": ["spotify: no device"]})
    assert ns.app.music.wait_idle(5)
    acts = music_data(ns)["actions"]
    assert acts == [{"id": 1_762_380_000, "action": "test-spotify", "result": "failed",
                     "text": "Spotify test failed: no device.", "at": 1_762_380_000}]
    notes = [n for n in ns.app.notifier.history if n.kind == tray.NOTE_MUSIC]
    assert notes[-1].title == "Spotify test" and notes[-1].key == "test-spotify:1762380000:result"


# ------------------------------------------------- Connect from the game --

@pytest.fixture
def sp(tmp_path, _needledropper_home):
    port = free_port()
    fake = token_routes(FakeHTTP())
    s = shell(tmp_path, _needledropper_home, cfg_extra={"spotify": {"redirectPort": port, "clientId": CID}},
              spotify_http=fake)
    s.port = port
    s.fake = fake
    try:
        yield s
    finally:
        s.close()


def test_connect_with_another_id_keeps_the_working_sign_in_until_the_new_one_succeeds(sp):
    save_tokens(sp.home, client_id=CID)                              # a working sign-in for the configured ID
    assert sa.TokenManager(CID, sa.TokenStore(sp.home)).usable()
    done: list = []
    session = sp.app.connect_spotify_from_game(OTHER, done.append)
    assert f"client_id={OTHER}" in session.authorize_url
    assert raw_config(sp.cfg_file)["spotify"]["clientId"] == CID     # not saved yet
    sp.app._next_spotify_watch = 0.0
    sp.app._watch_spotify(time.monotonic())                          # the expiry watcher stays quiet
    assert not [n for n in sp.app.notifier.history if n.kind == tray.NOTE_SPOTIFY_EXPIRED]
    approve(session.authorize_url)
    assert wait_for(lambda: done, timeout=8)
    assert raw_config(sp.cfg_file)["spotify"]["clientId"] == OTHER    # saved now
    assert sa.TokenStore(sp.home).load()["client_id"] == OTHER
    st = sp.call("GET", "/api/state")[1]["spotify"]
    assert st["clientId"] == OTHER and st["account"] == "Thrall"


def test_a_declined_sign_in_for_another_id_leaves_the_old_one_working(sp):
    save_tokens(sp.home, client_id=CID)
    done: list = []
    session = sp.app.connect_spotify_from_game(OTHER, done.append)
    approve(session.authorize_url, error="access_denied")
    assert wait_for(lambda: done, timeout=8)
    assert done[0].error == "access_denied"
    assert raw_config(sp.cfg_file)["spotify"]["clientId"] == CID
    assert sa.TokenManager(CID, sa.TokenStore(sp.home)).usable()     # the old tokens still work


def test_connect_with_another_id_and_no_working_sign_in_saves_it_at_once(sp):
    session = sp.app.connect_spotify_from_game(OTHER, lambda s: None)
    assert raw_config(sp.cfg_file)["spotify"]["clientId"] == OTHER
    session.cancel()


def test_connect_from_the_game_end_to_end_through_music_config(sp):
    sp.app.music._wall = lambda: 1_762_380_000.0
    opened: list[str] = []
    sp.deck.url_opener = opened.append
    sp.transports.live("chatlog").emit(frame(ET.CONFIG, seq=14, client_id=CID,
                                             action={"name": "connect-spotify", "at": 1_762_380_000}))
    assert sp.app.music.wait_idle(5) and len(opened) == 1 and f"client_id={CID}" in opened[0]
    approve(opened[0])
    assert wait_for(lambda: any(a["result"] == "connected" for a in sp.app.music.recent()), timeout=8)
    acts = music_data(sp)["actions"]
    assert [a["result"] for a in acts] == ["connected"] and "Thrall" in acts[0]["text"]
    music = music_data(sp)
    assert music["spotify"]["clientId"] == CID and music["spotify"]["account"] == "Thrall"
