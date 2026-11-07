"""v1.2.1 follow-up pass ("polish"), the app's side: the CI failures on Python 3.10 / 3.12 (a coarse monotonic
clock), Play on switched away from the app while an in-app song plays (RCOMP-10), the Spotify callback page's
wording (ON-7), the no-player cause for the game (ON-9), the test songs' volume, the launch key on every player
report, the parked player window after a display change, the YouTube box's clearance at every beacon corner and a
refused request body (the deflaked test_api_caps_request_bodies). The addon's side: test_v121_polish_addon.py."""
from __future__ import annotations

import json
import math
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import SSE, EngineFactory, FakeEngine, make_shell, wait_for  # noqa: E402
from test_spotify_fakes import FakeHTTP, save_tokens  # noqa: E402
from test_spotify_inapp import (DROP_TRACK, PHONE, PREV, SDK_DEVICE, PageDeck, make_app_rig, make_drop,  # noqa: E402
                                player_state, request)

from needledropper import gamedata, protocol2 as p2  # noqa: E402
from needledropper.deck import server, window as W  # noqa: E402
from needledropper.deck.server import Deck  # noqa: E402
from needledropper.engine import Engine, _Job  # noqa: E402
from needledropper.models import Drop, EventType as ET  # noqa: E402
from needledropper.providers import spotify as sp  # noqa: E402
from needledropper.providers.base import NowPlaying, PlayHandle, ProviderUnavailable  # noqa: E402
from needledropper.providers.spotify import SpotifyProvider  # noqa: E402
from needledropper.providers.spotify_auth import LoopbackCallbackServer  # noqa: E402
from needledropper.providers.youtube import YouTubeProvider  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fast(monkeypatch):
    """Short in-app waits (the logic is the same; the tests stay quick)."""
    monkeypatch.setattr(sp, "APP_READY_WAIT_S", 0.3)
    monkeypatch.setattr(sp, "APP_REGISTRATION_WAIT_S", 0.3)
    monkeypatch.setattr(sp, "APP_START_TIMEOUT_S", 0.5)
    monkeypatch.setattr(sp, "APP_API_CHECK_S", 0.2)
    monkeypatch.setattr(sp, "APP_STOP_SLACK_S", 0.3)


# ------------------------------------------------- 0a: a coarse monotonic clock --

class CoarseClock:
    """``time.monotonic`` as Windows has it before Python 3.13 (GetTickCount64: 15.625 ms steps). ``hold()``
    keeps the current tick: what follows happens within one tick, as on a fast PC."""

    STEP = 0.015625

    def __init__(self, real=time.monotonic) -> None:
        self.real = real
        self.held = None

    def __call__(self) -> float:
        if self.held is not None:
            return self.held
        return math.floor(self.real() / self.STEP) * self.STEP

    def hold(self) -> float:
        self.held = self()
        return self.held


def test_a_ready_in_the_same_clock_tick_as_the_stuck_verdict_still_clears_it(tmp_path, fast, monkeypatch):
    """CI failed test_a_start_that_never_progresses_falls_back_and_parks_the_song on Python 3.10 and 3.12 only:
    the stuck verdict was cleared by a "ready" whose time stamp was later than the verdict's, and with a 15.6 ms
    clock both had the same one. The player's report sequence orders them, whatever the clock's resolution:
    for the SDK listener and for the ARM's check_health."""
    clock = CoarseClock()
    monkeypatch.setattr(time, "monotonic", clock)  # the provider and the deck (threading keeps its own)
    rig = make_app_rig(tmp_path)
    rig.http.add("GET", "/v1/me/player", (200, player_state(PREV, 10_000, device=PHONE)),
                 (200, dict(player_state(DROP_TRACK, 0, playing=False), device={"id": SDK_DEVICE})))
    rig.http.add("PUT", "/v1/me/player/play", (204, None))
    mark = rig.provider._mark_stuck
    verdict: list[float] = []

    def mark_and_hold(reason):
        mark(reason)
        verdict.append(clock.hold())  # the rest of this happens within the verdict's tick
    monkeypatch.setattr(rig.provider, "_mark_stuck", mark_and_hold)
    with pytest.raises(sp.SpotifyStuckError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    rig.provider.stop(ei.value.cleanup)
    assert rig.provider.stuck()
    rig.page.report("ready", deviceId=SDK_DEVICE)              # the listener path
    assert rig.page.spotify.ready_at == verdict[0]            # the same time stamp as the verdict ...
    rig.provider._wait_registered(0)                         # ready must also be listed
    assert not rig.provider.stuck()                           # ... and the verdict is cleared all the same
    # check_health: a ready the listener never saw (another owner's listener), recorded in the same tick
    clock.held = None
    rig.provider._mark_stuck("no progress")
    sdk = rig.page.spotify
    listener, sdk._listener = sdk._listener, None
    rig.page.report("ready", deviceId=SDK_DEVICE)
    sdk._listener = listener
    assert sdk.ready_at == verdict[-1] and rig.provider.stuck()
    assert rig.provider.check_health() is True and not rig.provider.stuck()


def test_a_ready_reported_before_the_stuck_verdict_does_not_clear_it(tmp_path, monkeypatch):
    """The other side of the sequence rule: the player's ready from before the start that did not progress is
    not news; the verdict stays until a later one (the re-check's reconnect)."""
    clock = CoarseClock()
    monkeypatch.setattr(time, "monotonic", clock)
    rig = make_app_rig(tmp_path)
    rig.page._ready = False                                   # the re-check's reconnect gets no answer yet
    rig.page.report("ready", deviceId=SDK_DEVICE)
    clock.hold()
    rig.provider._mark_stuck("no progress within 3 s")
    assert rig.provider.check_health() is None and rig.provider.stuck()
    assert "reload" in rig.page.ops()                  # re-checked (at most once a minute)
    rig.page.registered.add(SDK_DEVICE)
    rig.page.report("ready", deviceId=SDK_DEVICE)              # its answer, in the same tick
    rig.provider._wait_registered(0)
    assert not rig.provider.stuck()


# ------------------------------------------------ RCOMP-10: Play on away from the app --

CID = "0123456789abcdef0123456789abcdef"


class SwapEngine(FakeEngine):
    """The fake engine plus what a live provider swap uses: ``set_providers`` (the real engine keeps the drop
    that plays on its provider), ``current_provider`` (``current``: the provider of that drop) and
    ``wait_idle`` (``idle``: False from a drop's start until its stop -- fade-out, clean-ups -- has finished)."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.current: Any = None
        self.idle = True

    def set_providers(self, providers: list) -> list:
        old, self.providers = self.providers, list(providers)
        return old

    def current_provider(self) -> Any:
        return self.current

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        return self.idle

    def set_music_order(self, order: str) -> str:
        return order


def _in_app_shell(tmp_path, home, *, housekeeping_s: float = 60.0):
    """A Companion on a real deck whose chain is one real SpotifyProvider (in-app or connect, by config), signed
    in. The housekeeping loop only runs when something wakes it (housekeeping_s), so the test sees what wakes
    it; no player window is ever launched (the launcher is a no-op, and the app is not asked to prepare one)."""
    save_tokens(Path(home), client_id=CID)

    def providers(cfg: dict, paths: Any, deck: Any) -> list:
        return [SpotifyProvider(cfg.get("spotify") or {}, paths, http=FakeHTTP(), deck=deck,
                                game_in_front=lambda: False)]

    engines = EngineFactory(SwapEngine)
    ns = make_shell(tmp_path, Path(home), cfg_extra={"spotify": {"clientId": CID, "device": "app"},
                                                     "providers": ["spotify"]},
                    provider_factory=providers, engine_factory=engines, housekeeping_s=housekeeping_s, start=False)
    ns.engines = engines
    sent: list[dict] = []
    real_send = ns.deck.send

    def send(cmd: dict) -> int:
        sent.append(dict(cmd))
        return real_send(cmd)
    ns.deck.send = send
    ns.sent = sent
    ns.app._prepare_player = lambda: None
    ns.app.start()
    return ns


def _sdk_ops(ns) -> list[str]:
    return [c.get("op") for c in ns.sent if c.get("cmd") == "spotify"]


def _token_status(deck: Deck) -> int:
    return request(deck, "GET", server.SPOTIFY_TOKEN_PATH,
                   {"Sec-Fetch-Site": "same-origin", "X-ND-Token": deck.token, "X-ND-Key": deck.player_key})[0]


def test_switching_play_on_away_from_the_app_never_cuts_the_song_and_closes_the_player_after_it(
        tmp_path, _needledropper_home):
    """Review RCOMP-10: switching Play on from the app to another device (spotify.device app -> connect) while an
    in-app song played left the old provider open: its player stayed a Spotify Connect device, the token endpoint
    kept answering and the player window kept needing Chrome, until a restart. The song plays on; once it ends
    the old provider is closed (its player disconnects), and switching back connects it again."""
    ns = _in_app_shell(tmp_path, _needledropper_home)
    try:
        sdk, eng = ns.deck.spotify, ns.engines.current
        old = ns.app.providers[0]
        assert old.mode == "app" and sdk.owned_by(old) and sdk.app_mode and _token_status(ns.deck) == 200
        eng.current, eng.idle = old, False                        # an in-app song plays
        assert ns.call("POST", "/api/config", {"spotify": {"device": "connect"}})[0] == 200
        new = ns.app.providers[0]
        assert new is not old and new.mode == "connect"
        assert sdk.owned_by(old) and "disconnect" not in _sdk_ops(ns)    # the song is not cut
        assert _token_status(ns.deck) == 200
        eng.current = None                     # the engine lets go of the drop, then fades it out (not idle yet)
        eng.on_status({"running": True, "change": {"kind": "playing"}})
        ns.app._close_retired()
        assert sdk.owned_by(old) and "disconnect" not in _sdk_ops(ns)    # its fade-out is not cut either
        eng.idle = True                                           # the stop is done: the engine's note says so
        eng.on_status({"running": True, "change": {"kind": "stopped", "dropId": "zone:1411"}})
        assert wait_for(lambda: not sdk.wanted, timeout=5)
        assert not sdk.app_mode and "disconnect" in _sdk_ops(ns)  # no Connect device, no Chrome first
        assert _token_status(ns.deck) == 404                      # the token endpoint refuses
        ns.sent.clear()
        assert ns.call("POST", "/api/config", {"spotify": {"device": "app"}})[0] == 200
        back = ns.app.providers[0]
        assert back.mode == "app" and sdk.owned_by(back) and sdk.wanted and sdk.app_mode
        assert _sdk_ops(ns) == ["connect"] and _token_status(ns.deck) == 200
    finally:
        ns.close()


def test_switching_away_and_back_during_one_song_keeps_the_player_connected(tmp_path, _needledropper_home):
    """The other order: away and back while the same song plays. The provider that owns the player again keeps
    it when the old one is closed after the song."""
    ns = _in_app_shell(tmp_path, _needledropper_home)
    try:
        sdk, eng = ns.deck.spotify, ns.engines.current
        old = ns.app.providers[0]
        eng.current, eng.idle = old, False
        ns.call("POST", "/api/config", {"spotify": {"device": "connect"}})
        ns.call("POST", "/api/config", {"spotify": {"device": "app"}})
        back = ns.app.providers[0]
        assert sdk.owned_by(back) and "disconnect" not in _sdk_ops(ns)
        eng.current, eng.idle = None, True
        eng.on_status({"running": True, "change": {"kind": "stopped"}})
        assert wait_for(lambda: not ns.app._retired, timeout=5)
        assert sdk.wanted and sdk.owned_by(back) and "disconnect" not in _sdk_ops(ns)
        assert _token_status(ns.deck) == 200
    finally:
        ns.close()


def test_switching_away_while_nothing_plays_closes_the_player_at_once(tmp_path, _needledropper_home):
    ns = _in_app_shell(tmp_path, _needledropper_home)
    try:
        sdk = ns.deck.spotify
        assert sdk.wanted
        ns.call("POST", "/api/config", {"spotify": {"device": "connect"}})
        assert not sdk.wanted and not sdk.app_mode and _sdk_ops(ns)[-1] == "disconnect"
        assert ns.app._retired == [] and _token_status(ns.deck) == 404
    finally:
        ns.close()


# ------------------------------- RCOMP-10 follow-up (polish-fix): the real engine, RAPP-1 / RAPP-2 --

SONG = Drop(n=1, id="zone:1411", kind="zone", title="9 to 5", artist="Dolly Parton",
            spotify="spotify:track:4w3tQBXhn5345eUXDGBWZG")


def _real_engine_shell(tmp_path, home, *, play: Any = None):
    """A Companion with the REAL engine (its async pre-warm worker included) on a real deck, whose chain is one
    real SpotifyProvider (in-app or connect, by config), signed in. Only playback itself is a stand-in: play()
    (``play``, or one that returns at once: the song then plays until the engine stops it), stop(), available()
    and can_play(). ``ns.built`` lists every provider built, in order. No player window is launched."""
    save_tokens(Path(home), client_id=CID)
    built: list = []

    def providers(cfg: dict, paths: Any, deck: Any) -> list:
        p = SpotifyProvider(cfg.get("spotify") or {}, paths, http=FakeHTTP(), deck=deck, game_in_front=lambda: False)
        built.append(p)
        p.play = play or (lambda drop, *, fade_in_ms=0: PlayHandle(provider="spotify", drop=drop, now_playing=NowPlaying(
            title=drop.title, artist=drop.artist, provider="spotify")))
        p.stop = lambda handle, *, fade_out_ms=0: None
        p.available = lambda: True
        p.can_play = lambda drop: True
        return [p]

    ns = make_shell(tmp_path, Path(home), cfg_extra={"spotify": {"clientId": CID, "device": "app"},
                                                     "providers": ["spotify"]},
                    provider_factory=providers, engine_factory=None, housekeeping_s=60.0, start=False)
    sent: list[dict] = []
    real_send = ns.deck.send

    def send(cmd: dict) -> int:
        sent.append(dict(cmd))
        return real_send(cmd)
    ns.deck.send = send
    ns.sent, ns.built = sent, built
    ns.app._prepare_player = lambda: None
    ns.app.start()
    assert isinstance(ns.app.engine, Engine)
    return ns


def _arm_next_place(ns) -> None:
    """The next place's ARM, read from the game: its pre-warm is queued for the chain's Spotify provider."""
    arm = p2.decode_frame(p2.encode_frame(p2.build_frame(ET.ARM, seq=5, drop_id="zone:1413", artist="Toto",
                                                          title="Africa", spotify="2374M0fQpWi3dLnB54qaLX")))
    ns.transports.live("pixel").emit(arm)


def test_play_on_away_back_and_away_again_during_one_song_never_cuts_it(tmp_path, _needledropper_home):
    """Review RAPP-1 (a): away, back and away again while one in-app song played. The provider built on the way
    back owned the player that song plays in, and the second switch closed it at once ("stale"), so the song was
    cut. Every old provider now waits for the song; after it, the player disconnects once."""
    ns = _real_engine_shell(tmp_path, _needledropper_home)
    try:
        eng, sdk = ns.app.engine, ns.deck.spotify
        p1 = ns.app.providers[0]
        eng.play_now(SONG, test=False, only="spotify")
        assert wait_for(lambda: eng.current_provider() is p1, timeout=5)
        ns.sent.clear()
        for device in ("connect", "app", "connect"):
            ns.app._write_config({"spotify": {"device": device}})
            assert "disconnect" not in _sdk_ops(ns) and sdk.wanted        # the song plays on in the app's player
        assert eng.current_provider() is p1 and not eng.wait_idle(0) and ns.app.providers[0].mode == "connect"
        assert sdk.owned_by(ns.built[2])                                   # the one built on the way back
        eng.stop_playback()
        assert wait_for(lambda: not ns.app._retired and not sdk.wanted, timeout=5)
        assert _sdk_ops(ns) == ["disconnect"] and not sdk.app_mode and _token_status(ns.deck) == 404
    finally:
        ns.close()


def test_play_on_switched_while_an_in_app_song_is_starting_never_cuts_it(tmp_path, _needledropper_home):
    """Review RAPP-1 (b): the engine names a drop's provider only once its play() returned (an in-app start takes
    up to 4.5 s), and the rebuild asked for it before swapping the chain: nothing played yet, so the provider whose
    song had just started was closed at once."""
    started, release = threading.Event(), threading.Event()

    def slow_play(drop, *, fade_in_ms=0):  # the in-app start: SDK ready wait, Web API play, start check
        started.set()
        assert release.wait(10)
        return PlayHandle(provider="spotify", drop=drop,
                          now_playing=NowPlaying(title=drop.title, artist=drop.artist, provider="spotify"))

    ns = _real_engine_shell(tmp_path, _needledropper_home, play=slow_play)
    try:
        eng, sdk = ns.app.engine, ns.deck.spotify
        p1 = ns.app.providers[0]
        swapping = threading.Event()
        real_swap = eng.set_providers

        def set_providers(providers):  # the rebuild got this far while play() still runs
            swapping.set()
            return real_swap(providers)
        eng.set_providers = set_providers
        eng.play_now(SONG, test=False, only="spotify")
        assert started.wait(5) and eng.current_provider() is None
        ns.sent.clear()
        switch = threading.Thread(target=ns.app._write_config, args=({"spotify": {"device": "connect"}},),
                                  daemon=True)
        switch.start()
        assert swapping.wait(5)
        release.set()                                                       # the song starts now
        switch.join(10)
        assert not switch.is_alive()
        assert eng.current_provider() is p1 and any(p is p1 for p in ns.app._retired)
        assert "disconnect" not in _sdk_ops(ns) and sdk.wanted and sdk.owned_by(p1)
        eng.stop_playback()
        assert wait_for(lambda: not ns.app._retired and not sdk.wanted, timeout=5)
        assert _sdk_ops(ns) == ["disconnect"] and _token_status(ns.deck) == 404
    finally:
        release.set()
        ns.close()


def test_a_closed_in_app_provider_never_switches_the_player_on_again(tmp_path):
    """Review RAPP-2: close() was not terminal. A pre-warm, a health check or a play that ran on a closed in-app
    provider called sync_sdk() and connected the player again under it -- nothing closed it after that."""
    rig = make_app_rig(tmp_path)
    p, sdk = rig.provider, rig.page.spotify
    assert sdk.wanted and sdk.owned_by(p) and rig.page.ops() == ["connect"]
    p.close()
    assert not sdk.wanted and not sdk.app_mode and rig.page.ops() == ["connect", "disconnect"]
    for run in (p.sync_sdk, p.check_health, lambda: p.prepare(make_drop()), p.reset_session):
        run()
        assert not sdk.wanted and not sdk.app_mode and not sdk.owned_by(p)
    with pytest.raises(ProviderUnavailable):
        p.play(make_drop())
    assert not p.available() and rig.page.ops() == ["connect", "disconnect"]
    with pytest.raises(LookupError):
        sdk.token()                                                         # the token endpoint refuses (404)
    p.close()                                                               # idempotent
    assert rig.page.ops() == ["connect", "disconnect"]


def test_a_pre_warm_queued_before_the_switch_never_switches_the_old_player_on(tmp_path, _needledropper_home):
    """Review RAPP-2 with the real engine: an ARM's pre-warm, bound to the in-app provider when the ARM came, ran
    after Play on was switched away while nothing played (that provider closed at once) and connected the player
    again: Spotify device, Chrome first, token endpoint answering, until the app restarted."""
    ns = _real_engine_shell(tmp_path, _needledropper_home)
    gate = threading.Event()
    try:
        eng, sdk = ns.app.engine, ns.deck.spotify
        p1 = ns.app.providers[0]
        eng._worker.submit(_Job(lambda: gate.wait(10), None))               # the worker is busy (a slow search)
        _arm_next_place(ns)
        assert wait_for(lambda: len(eng._worker._pending) >= 1, timeout=5)
        ns.app._write_config({"spotify": {"device": "connect"}})            # nothing plays: P1 closes at once
        assert not sdk.wanted and _token_status(ns.deck) == 404
        gate.set()                                                          # the queued pre-warm runs, on P1
        assert eng.wait_prewarm(10)
        ns.app._close_retired()
        assert not sdk.wanted and not sdk.app_mode and not sdk.owned_by(p1)
        assert _sdk_ops(ns).count("connect") == 1 and _token_status(ns.deck) == 404
    finally:
        gate.set()
        ns.close()


def test_a_pre_warm_on_the_old_provider_never_takes_the_player_from_the_new_one(tmp_path, _needledropper_home):
    """Away and back during one in-app song, with an ARM pre-warm still queued for the provider that plays it.
    That provider stays open for its song, and the pre-warm used to take the player back from the chain's new
    in-app provider (sync_sdk), so the old one's close after the song disconnected the chain's own player.
    Taken out of the chain, it never touches the player's connection again (retire)."""
    ns = _real_engine_shell(tmp_path, _needledropper_home)
    gate = threading.Event()
    try:
        eng, sdk = ns.app.engine, ns.deck.spotify
        p1 = ns.app.providers[0]
        eng.play_now(SONG, test=False, only="spotify")
        assert wait_for(lambda: eng.current_provider() is p1, timeout=5)
        eng._worker.submit(_Job(lambda: gate.wait(10), None))
        _arm_next_place(ns)
        assert wait_for(lambda: len(eng._worker._pending) >= 1, timeout=5)
        ns.sent.clear()
        ns.app._write_config({"spotify": {"device": "connect"}})
        ns.app._write_config({"spotify": {"device": "app"}})
        p3 = ns.app.providers[0]
        assert sdk.owned_by(p3)
        gate.set()                                                          # P1's check_health and prepare run
        assert eng.wait_prewarm(10)
        assert sdk.owned_by(p3) and not sdk.owned_by(p1)
        eng.stop_playback()
        assert wait_for(lambda: not ns.app._retired, timeout=5)
        assert sdk.wanted and sdk.owned_by(p3) and "disconnect" not in _sdk_ops(ns)
        assert _token_status(ns.deck) == 200
    finally:
        gate.set()
        ns.close()


# ------------------------------------------------- ON-9: the no-player cause for the game --

def test_the_app_tells_the_game_why_its_spotify_player_is_missing(tmp_path, _needledropper_home, monkeypatch):
    """Review ON-9: the game's music.spotify.problem names the cause of state no-player (gamedata accepts each)."""
    ns = _in_app_shell(tmp_path, _needledropper_home, housekeeping_s=0.05)
    try:
        monkeypatch.setattr(sp, "SDK_READY_GRACE_S", -1.0)       # the player page never got ready
        player = SSE(ns.deck.port, "player", key=ns.deck.player_key)
        try:
            assert wait_for(lambda: ns.deck.page_open)
            server_status = {"state": "ready", "browser": "chrome"}
            body = json.dumps(server_status).encode()
            request(ns.deck, "POST", "/status", {"X-ND-Token": ns.deck.token, "X-ND-Key": ns.deck.player_key,
                                                 "Content-Type": "application/json",
                                                 "Content-Length": str(len(body))}, body)
            music = ns.app._game_music()["spotify"]
            assert music["state"] == "no-player" and music["problem"] == "not-ready"
        finally:
            player.close()
        assert wait_for(lambda: not ns.deck.page_open)
        ns.deck.chrome_available = lambda: True
        assert ns.app._game_music()["spotify"]["problem"] == "no-window"   # no player window at all
        ns.deck.chrome_available = lambda: False
        assert ns.app._game_music()["spotify"]["problem"] == "no-chrome"
    finally:
        ns.close()
    for problem in sp.NO_PLAYER_PROBLEMS:
        out = gamedata.normalize_companion_data({"music": {"spotify": {"state": "no-player", "problem": problem}}})
        assert out["music"]["spotify"]["problem"] == problem


def _written_spotify(ns, status: dict) -> dict:
    """What NeedleDropper_Companion's Data.lua says in music.spotify after the app pushed ``status``."""
    ns.app._spotify_status = lambda: {"badge": status.get("state"), "mode": "app", **status}
    ns.app.push_game_data(flush=True)
    return (gamedata.read_companion_data(ns.app.paths.addons_dir) or {})["music"]["spotify"]


def test_the_game_loses_a_spotify_cause_once_it_is_gone(tmp_path, _needledropper_home):
    """Review RG-1: music.spotify.problem was written only when there was a cause, and set_music leaves a field
    the call leaves out unchanged, so an old cause stayed in Data.lua: after "allowlist" (state error) a later
    error for another reason still said "add the account under User Management", and a no-player state the app
    couldn't name kept the cause of an earlier one. Without a cause the field is removed now."""
    ns = make_shell(tmp_path, Path(_needledropper_home), cfg_extra={"spotify": {"clientId": CID}})
    try:
        assert _written_spotify(ns, {"state": "error", "problem": "allowlist"})["problem"] == "allowlist"
        assert "problem" not in _written_spotify(ns, {"state": "connected", "problem": None})
        assert "problem" not in _written_spotify(ns, {"state": "error", "problem": None})
        assert _written_spotify(ns, {"state": "no-player", "problem": "no-window"})["problem"] == "no-window"
        assert "problem" not in _written_spotify(ns, {"state": "no-player", "problem": None})
        assert _written_spotify(ns, {"state": "error", "problem": "allowlist"})["problem"] == "allowlist"
        assert "problem" not in _written_spotify(ns, {"state": "error", "problem": "no-window"})  # not with error
    finally:
        ns.close()


def test_a_cause_an_earlier_run_left_in_the_file_is_removed_by_the_next(tmp_path, _needledropper_home):
    """The same across a restart: Data.lua is the state (each write merges into the file read back), so a cause an
    earlier run of the app left there is removed by the first music block a new run writes."""
    ns = make_shell(tmp_path, Path(_needledropper_home), cfg_extra={"spotify": {"clientId": CID}}, start=False)
    try:
        addons = ns.app.paths.addons_dir
        gamedata.set_music(addons, spotify={"clientId": CID, "state": "error", "problem": "allowlist", "mode": "app"})
        gamedata.flush(addons)
        assert gamedata.read_companion_data(addons)["music"]["spotify"]["problem"] == "allowlist"
        ns.app.start()
        spotify = _written_spotify(ns, {"state": "connected", "problem": None})
        assert spotify["state"] == "connected" and "problem" not in spotify
    finally:
        ns.close()


# ------------------------------------------------- the test songs' volume --

class _BoxDeck:
    """What YouTubeProvider.play needs of the deck: an open player page that starts every song."""

    page_open, player_opening, auto_open = True, False, "youtube"

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, cmd: dict) -> int:
        self.sent.append(dict(cmd))
        return 1

    def wait_status(self, *, play_id: Optional[str] = None, states: Any = None, timeout: float = 5.0,
                    after_seq: Optional[int] = None) -> dict:
        return {"state": "playing", "playId": play_id, "seq": 1}

    def ensure_player(self, timeout: float = 8.0, **kw: Any) -> bool:
        return True


class _CaptureEngine(SwapEngine):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.drops: list = []

    def play_now(self, drop: Any, *, test: bool = True, only: Optional[str] = None) -> None:
        self.drops.append(drop)


def test_the_test_songs_play_at_the_volume_of_a_drop_without_its_own(tmp_path, _needledropper_home):
    """The lead's item 7: "Test YouTube plays at volume 100 because TEST_SONG has no volume". Not a bug: a drop
    without its own volume (no VOLUME in its frame) plays at 100 on YouTube and at spotify.volume in the app's
    Spotify player, and the test songs take exactly that path. Pinned, for the game's tests and the Connect
    page's, YouTube and Spotify, with and without a spotify.volume."""
    engines = EngineFactory(_CaptureEngine)
    ns = make_shell(tmp_path, Path(_needledropper_home), engine_factory=engines)
    try:
        ns.app.music._wall = lambda: 1_762_380_000.0
        player = SSE(ns.deck.port, "player", key=ns.deck.player_key)
        try:
            assert wait_for(lambda: ns.deck.page_open)
            assert ns.call("POST", "/api/music/test", {"service": "youtube"})[0] == 202          # the Connect page
            ns.transports.live("pixel").emit(p2.decode_frame(p2.encode_frame(p2.build_frame(       # the game
                ET.CONFIG, seq=3, action={"name": "test-spotify", "at": 1_762_380_000}))))
            assert wait_for(lambda: len(engines.current.drops) == 2)
        finally:
            player.close()
    finally:
        ns.close()
    tests = {d.id: d for d in engines.current.drops}
    fire = p2.decode_frame(p2.encode_frame(p2.build_frame(ET.FIRE, seq=9, drop_id="zone:1411", youtube="d_vPJjqWxZM",
                                                          spotify=DROP_TRACK["id"], artist="Dolly Parton",
                                                          title="9 to 5")))
    drop = Engine._drop_from_frame(SimpleNamespace(_label=lambda fr: "Durotar"), fire)
    assert fire.volume is None and drop.volume is None
    assert tests["test-youtube"].volume is None and tests["test-spotify"].volume is None

    def box_volume(d) -> int:
        deck = _BoxDeck()
        YouTubeProvider({}, None, deck).play(d)
        return next(c for c in deck.sent if c.get("cmd") == "play")["volume"]
    assert box_volume(tests["test-youtube"]) == box_volume(drop) == 100
    for configured in (None, 60):
        provider = SpotifyProvider({"clientId": CID, "device": "app", "volume": configured},
                                   SimpleNamespace(app_dir=tmp_path), http=FakeHTTP(), deck=PageDeck())
        assert provider._app_volume(tests["test-spotify"]) == provider._app_volume(drop) == (configured or 100)


# ---------------------------------------------- the launch key on every player report --

def test_every_player_report_needs_the_launch_key(tmp_path):
    """YouTube and local status reports (they end songs, hide and show the box) were accepted with the X-ND-Token
    alone, which any local process can read from /player; like the SDK's (RCOMP-3), every report needs the
    player window's launch key, and player.js sends it with each one (its post()). A page without it is
    refused (401) and retires itself, as on its event stream."""
    deck = Deck(port=0, launcher=lambda a: None, url_opener=lambda u: False).start()
    try:
        deck.send({"cmd": "play", "playId": "p1", "source": "youtube", "videoId": "abcdefghijk", "dropId": "zone:1"})

        def post(payload: dict, key: Optional[str]) -> int:
            body = json.dumps(payload).encode()
            hdrs = {"X-ND-Token": deck.token, "Content-Type": "application/json", "Content-Length": str(len(body))}
            if key is not None:
                hdrs["X-ND-Key"] = key
            return request(deck, "POST", "/status", hdrs, body)[0]
        for payload in ({"state": "playing", "playId": "p1", "detail": "youtube"},
                        {"state": "ended", "playId": "p1"}, {"state": "ready", "browser": "chrome"},
                        {"state": "csp", "blocked": "https://evil.example", "directive": "script-src"},
                        {"state": "spotify", "event": "ready", "deviceId": "dev"}):
            assert post(payload, None) == 401 and post(payload, "wrong") == 401, payload
        assert deck.last_status is None and deck.spotify.device_id is None
        assert post({"state": "playing", "playId": "p1", "detail": "youtube"}, deck.player_key) == 204
        assert deck.status_for("p1")["state"] == "playing"
    finally:
        deck.stop()
    js = (Path(server.STATIC_DIR) / "player.js").read_text(encoding="utf-8")
    report = js.split("function post(", 1)[1].split("\n  }\n", 1)[0]
    assert '"X-ND-Key": KEY' in report and "r.status === 401) retire()" in report
    assert js.count('fetch("/status"') == 1                      # every report goes through post()


# ------------------------------------------------- the parked player window --

class DisplayWin32:
    """A fake user32 whose virtual screen (the rectangle around every monitor) and window position a test can
    change, as a display change does."""

    PLAYER = 900

    def __init__(self) -> None:
        self.vs = (0, 0, 3840, 2160)
        self.size = (1260, 1140)
        self.at = (0, 0)
        self.calls: list = []

    def is_window(self, h):
        return h == self.PLAYER

    def window_size(self, h):
        return self.size

    def window_rect(self, h):
        return (self.at[0], self.at[1], self.at[0] + self.size[0], self.at[1] + self.size[1])

    def virtual_screen(self):
        return self.vs

    def monitor_rect(self, h):
        return (0, 0, 3840, 2160)

    def dpi_scale(self, h):
        return 3.0

    def set_pos(self, hwnd, after, x, y, cx, cy, flags):
        self.calls.append((after, x, y, flags))
        self.at = (x, y)
        return True


def test_a_display_change_while_the_player_is_parked_parks_it_again():
    """D14 parks the window beyond the virtual screen's top-left corner between songs. A monitor added above or to
    the left (the virtual screen grows there), or a resolution change after which Windows moves the window onto a
    monitor, could show it: the app's housekeeping re-checks it every few seconds and parks it again, never
    activating it, and leaves a box that a YouTube song shows alone."""
    w32 = DisplayWin32()
    pw = W.PlayerWindow(w32, game_window=lambda: None)
    pw.hwnd = w32.PLAYER
    assert pw.park()
    first = w32.at
    assert first == (0 - 1260 - W.PARK_GAP_PX, 0 - 1140 - W.PARK_GAP_PX)
    assert pw.keep_parked() is False and len(w32.calls) == 1   # nothing changed: nothing done
    w32.vs = (-1920, -1080, 5760, 3240)                         # a monitor above and to the left
    assert pw.keep_parked() is True
    after, x, y, flags = w32.calls[-1]
    assert (x, y) == (-1920 - 1260 - W.PARK_GAP_PX, -1080 - 1140 - W.PARK_GAP_PX)
    assert after == W.HWND_NOTOPMOST and flags & W.SWP_NOACTIVATE and not pw.shown
    w32.at = (100, 100)                                          # Windows put it on a monitor
    assert pw.keep_parked() is True and w32.at == (x, y)
    assert pw.keep_parked() is False
    assert pw.show() and pw.shown                                # a YouTube song shows the box ...
    w32.vs = (0, 0, 3840, 2160)
    n = len(w32.calls)
    assert pw.keep_parked() is False and len(w32.calls) == n     # ... and the check leaves it there
    deck = Deck(port=0, launcher=lambda a: None, url_opener=lambda u: False)
    deck.window = pw
    pw.hide()
    w32.vs = (-3840, 0, 7680, 2160)
    assert deck.keep_player_parked() is True                     # what the housekeeping step calls
    assert all(c[3] & W.SWP_NOACTIVATE for c in w32.calls)       # never activated


def test_the_housekeeping_loop_keeps_the_player_parked(tmp_path, _needledropper_home):
    ns = make_shell(tmp_path, Path(_needledropper_home))
    try:
        calls: list = []
        ns.deck.keep_player_parked = lambda: calls.append(1) or False
        assert wait_for(lambda: len(calls) >= 2)                 # every housekeeping round
    finally:
        ns.close()


# ------------------------------------------------- the box clears the beacon at its corner --

@pytest.mark.parametrize("beacon, box, expected", [
    # the beacon's corner, the box's corner, where the box goes in a 1920 x 1080 area (box 420 x 380, 16 px)
    ("TOPLEFT", "topleft", (16, 64)),                 # below the beacon
    ("TOPLEFT", "topright", (1484, 16)),              # clear already
    ("TOPRIGHT", "topright", (1484, 64)),             # below the beacon
    ("TOPRIGHT", "topleft", (16, 16)),
    ("BOTTOMLEFT", "bottomleft", (16, 1080 - 48 - 16 - 380)),   # above the beacon
    ("BOTTOMLEFT", "bottomright", (1484, 684)),
    ("BOTTOMRIGHT", "bottomright", (1484, 1080 - 48 - 16 - 380)),
    ("BOTTOMRIGHT", "topleft", (16, 16)),
])
def test_the_youtube_box_keeps_clear_of_the_beacon_at_its_configured_corner(beacon, box, expected):
    """DESIGN-v1.2.1 3.9 kept only the default top-left beacon clear; the beacon's corner is configurable (the
    addon's beaconCorner, the app's transport.pixel.corner), so a box in the beacon's corner covered it and
    nothing was read while it showed. The clearance follows the app's configured corner."""
    area, size = (0, 0, 1920, 1080), (420, 380)
    pos = W.corner_position(box, area, size, 16, W.BEACON_RECT_PX, beacon)
    assert pos == expected
    bw, bh = W.BEACON_RECT_PX
    at = W.normalize_beacon_corner(beacon)
    bx = 0 if at.endswith("left") else 1920 - bw
    by = 0 if at.startswith("top") else 1080 - bh
    x, y = pos
    assert not (x < bx + bw and x + size[0] > bx and y < by + bh and y + size[1] > by)  # never over the beacon


def test_the_app_hands_its_beacon_corner_to_the_box(tmp_path, _needledropper_home):
    """transport.pixel.corner reaches the window manager, at start and when config.json changes."""
    w32 = DisplayWin32()
    w32.client_rect = lambda hwnd: (0, 0, 1920, 1080)
    w32.dpi_scale = lambda hwnd: 1.0
    w32.size = (420, 380)
    pw = W.PlayerWindow(w32, corner="bottomright", game_window=lambda: 4242)
    pw.hwnd = w32.PLAYER
    ns = make_shell(tmp_path, Path(_needledropper_home), start=False,
                    cfg_extra={"transport": {"pixel": {"corner": "BOTTOMRIGHT"}},
                               "deck": {"port": 0, "playerCorner": "bottomright", "autoOpen": "never"}})
    ns.deck.window = pw
    try:
        ns.app.start()
        assert ns.deck.beacon_corner == "bottomright" and pw.beacon_corner == "bottomright"
        assert pw.target() == (1484, 1080 - 48 - 16 - 380)      # above the beacon in the same corner
        assert pw.show()
        n = len(w32.calls)
        ns.app._write_config({"transport": {"pixel": {"corner": "TOPLEFT"}}})  # config.json edited (advanced)
        assert pw.beacon_corner == "topleft" and len(w32.calls) > n  # an open box moves at once
        assert w32.calls[-1][1:3] == (1484, 1080 - 16 - 380)
    finally:
        ns.close()


# ------------------------------------------------- a refused body never reaches the API --

def test_a_refused_body_never_reaches_the_api_even_when_the_answer_is_lost(tmp_path):
    """test_api_caps_request_bodies failed with WinError 10053 under load (fix/logs/run3.log): its Content-Length
    -5 request. The deck refuses it without reading the body and closes; a body that arrives after that (a second
    segment, as http.client sends it) makes Windows reset the connection, and a client that reads late loses the
    400. The body is refused either way, and a request sent in one write always gets the 400."""
    calls: list = []
    deck = Deck(port=0, launcher=lambda a: None, url_opener=lambda u: False)
    deck.api = lambda method, path, query, body: (calls.append(body), (200, {"ok": True}))[1]
    deck.start()
    try:
        def send(split: bool) -> str:
            s = socket.create_connection(("127.0.0.1", deck.port), timeout=5)
            try:
                head = (f"POST /api/config HTTP/1.1\r\nHost: 127.0.0.1:{deck.port}\r\nX-ND-Token: {deck.token}\r\n"
                        "Content-Type: application/json\r\nContent-Length: -5\r\n\r\n").encode()
                if split:
                    s.sendall(head)
                    time.sleep(0.2)                              # the deck has answered and closed by now
                    try:
                        s.sendall(b"{}")
                    except OSError as exc:
                        return type(exc).__name__
                    time.sleep(0.2)
                else:
                    s.sendall(head + b"{}")
                return s.recv(4096).split(b"\r\n", 1)[0].decode() or "closed"
            except OSError as exc:
                return type(exc).__name__
            finally:
                s.close()
        assert send(split=False) == "HTTP/1.0 400 Bad Request"
        assert send(split=True) in ("HTTP/1.0 400 Bad Request", "ConnectionAbortedError", "ConnectionResetError",
                                    "BrokenPipeError", "closed")
        assert calls == []  # the API never saw a refused body
    finally:
        deck.stop()


# ------------------------------------------------------- ON-7: the callback page --

def _page_text(query: str) -> str:
    srv = LoopbackCallbackServer(0, expected_state="S", context="panel")
    srv.start()
    try:
        with urllib.request.urlopen(f"{srv.redirect_uri}?{query}", timeout=5) as r:
            return r.read().decode("utf-8")
    finally:
        srv.close()


def test_the_spotify_callback_page_points_to_the_game_and_the_connect_page():
    """Review ON-7: after Agree the page sent the player back to "the NeedleDropper control panel" (gone since
    1.2), and after an error or a decline to its "Connect Spotify" button (the Connect page has "Sign in"). The
    page can't tell where the sign-in started, so it names both places."""
    ok = _page_text("code=C&state=S")
    assert "control panel" not in ok and "close this tab" in ok
    assert "<b>Refresh status</b> in Music services" in ok and "Connect page" in ok
    for query in ("error=access_denied&state=S", "error=server_error&state=S"):
        bad = _page_text(query)
        assert "control panel" not in bad
        assert "<b>Connect Spotify</b> in the game's Music services" in bad and "<b>Sign in</b> on" in bad
        assert "Connect page" in bad


# ------------------------------------------------------- ON-8: what confirms a sign-in --

def test_the_docs_say_which_sign_in_a_windows_notification_confirms():
    """Review ON-8: PROVIDERS.md promised a Windows notification after a Connect-page sign-in; only a sign-in
    the game asked for gets one (music_config notifies requests from the game), the Connect page shows its
    own on the Spotify card."""
    text = (ROOT / "docs" / "PROVIDERS.md").read_text(encoding="utf-8")
    step2 = text.split("### Step 2: Sign in", 1)[1].split("\n### ", 1)[0]
    assert "A Windows notification confirms it too." not in step2
    flat = " ".join(step2.split())
    assert "A sign-in you started on the Connect page shows there" in flat
    assert "A sign-in you started in game is confirmed by a Windows notification" in flat
