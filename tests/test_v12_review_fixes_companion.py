"""Regression tests for the companion review of 2026-11-05 and the owner's real-client results
(docs/review/real-client-2026-11-05.md #8, #10, #13):

* SPOT-NO-SESSION-HEALTH: after one stuck Spotify attempt the engine plays YouTube directly for the
  rest of the session; a cheap health read on ARM re-checks Spotify (and spots the stuck signature
  before any song is sent); a new game session gives it another chance; the game is told.
* SPOT-VERIFY-RESEND-UNMANAGED: a resend / recovery command that fails, or a refusal after the
  accepted play, never leaves Spotify playing unmanaged.
* ENG-HELLO-SEQ-ROLLBACK: after a client crash the next session's lower seq numbers still end a drop;
  the drop also stops when the game closes.
* Journey F2 (Spotify side): the Spotify app is never launched while the game is the foreground window.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_spotify_fakes import DROP_TRACK, PC, make_drop, make_rig, ok_routes, player_state, tid  # noqa: E402
from test_v12_engine_flows import FakeClock, FakeDeck  # noqa: E402

from needledropper import protocol2 as p2  # noqa: E402
from needledropper.engine import Engine  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402
from needledropper.providers import spotify as sp  # noqa: E402
from needledropper.providers.base import NowPlaying, PlayHandle, Provider, ProviderError  # noqa: E402

YT = "QAo_Ycocl1E"
STUCK_STATE = {"device": dict(PC), "item": None, "progress_ms": 0, "is_playing": False,
               "actions": {"disallows": {"pausing": True, "resuming": True, "seeking": True}}}


class FakeYouTube(Provider):
    name = "youtube"

    def __init__(self):
        self.plays: list[str] = []
        self.prepared: list[str] = []

    def available(self):
        return True

    def can_play(self, drop):
        return bool(drop.youtube)

    def prepare(self, drop):
        self.prepared.append(drop.id)

    def play(self, drop, *, fade_in_ms=0):
        self.plays.append(drop.id)
        return PlayHandle(provider=self.name, drop=drop, now_playing=NowPlaying(drop.title, drop.artist))

    def stop(self, handle, *, fade_out_ms=0):
        pass


def fire(seq, drop_id, *, youtube=YT, spotify=None):
    return p2.build_frame(ET.FIRE, seq=seq, flags=0, drop_id=drop_id, spotify=spotify or tid("drop"),
                          youtube=youtube, artist="Low Roar", title="I'll Keep Coming", length_ms=60000)


def arm(seq, drop_id, *, youtube=YT, spotify=None):
    return p2.build_frame(ET.ARM, seq=seq, flags=0, drop_id=drop_id, spotify=spotify or tid("drop"),
                          youtube=youtube, artist="Low Roar", title="I'll Keep Coming", length_ms=60000)


def stuck_rig(tmp_path, state=STUCK_STATE):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", (200, state))
    rig.http.add("PUT", "/v1/me/player", (204, None))
    return rig


def spotify_plays(rig, since=0):
    return [c for c in rig.http.calls[since:] if c.method == "PUT" and c.path == "/v1/me/player/play"]


# ------------------------------------------------------ SPOT-NO-SESSION-HEALTH --

def test_after_one_stuck_attempt_youtube_plays_without_trying_spotify_again(tmp_path):
    rig = stuck_rig(tmp_path)
    yt = FakeYouTube()
    notes = []
    eng = Engine([rig.provider, yt], None, clock=rig.clock, prewarm_async=False, fade_in_ms=0,
                 on_status=lambda st: notes.append(st["change"]))
    eng.handle(fire(10, "zone:1429"), "pixel")
    assert yt.plays == ["zone:1429"] and rig.provider.stuck()
    assert sum(rig.clock.sleeps) <= 2.0                     # fast stuck detection, not 6.5 s of retries
    eng.stop_playback()
    eng.drain()
    before = len(rig.http.calls)
    rig.clock.sleeps.clear()
    eng.handle(fire(20, "zone:1411"), "pixel")
    assert yt.plays == ["zone:1429", "zone:1411"]
    assert rig.http.calls[before:] == [] and rig.clock.sleeps == []    # not a single Spotify call
    assert [n["kind"] for n in notes].count("spotifyStuck") == 1


def test_a_spotify_only_drop_still_tries_the_stuck_spotify_last(tmp_path):
    rig = stuck_rig(tmp_path)
    yt = FakeYouTube()
    eng = Engine([rig.provider, yt], None, clock=rig.clock, prewarm_async=False, fade_in_ms=0)
    eng.handle(fire(10, "zone:1429"), "pixel")
    eng.stop_playback()
    eng.drain()
    before = len(rig.http.calls)
    eng.handle(fire(20, "zone:1411", youtube=""), "pixel")         # no YouTube id: Spotify is all there is
    assert len(spotify_plays(rig, before)) == 1


def test_the_arm_health_read_spots_a_stuck_spotify_before_any_song_is_sent(tmp_path):
    rig = stuck_rig(tmp_path)
    yt = FakeYouTube()
    notes = []
    eng = Engine([rig.provider, yt], None, clock=rig.clock, prewarm_async=False, fade_in_ms=0,
                 on_status=lambda st: notes.append(st["change"]))
    eng.handle(arm(9, "zone:1429"), "pixel")
    assert rig.provider.stuck()
    assert yt.prepared == ["zone:1429"]                     # YouTube is warmed instead of Spotify
    assert [n["kind"] for n in notes].count("spotifyStuck") == 1
    eng.handle(fire(10, "zone:1429"), "pixel")
    assert spotify_plays(rig) == [] and yt.plays == ["zone:1429"]


def test_the_health_read_clears_the_verdict_once_spotify_plays_again(tmp_path):
    rig = stuck_rig(tmp_path)
    yt = FakeYouTube()
    eng = Engine([rig.provider, yt], None, clock=rig.clock, prewarm_async=False, fade_in_ms=0)
    eng.handle(fire(10, "zone:1429"), "pixel")
    eng.stop_playback()
    eng.drain()
    assert rig.provider.stuck()
    # The player restarted Spotify and plays something: the next ARM's health read sees it.
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 9000)))
    eng.handle(arm(19, "zone:1411"), "pixel")
    assert not rig.provider.stuck()


def test_a_new_game_session_gives_a_stuck_spotify_another_chance(tmp_path):
    rig = stuck_rig(tmp_path)
    eng = Engine([rig.provider, FakeYouTube()], None, clock=rig.clock, prewarm_async=False, fade_in_ms=0)
    eng.handle(p2.build_frame(ET.HELLO, seq=1), "pixel")
    eng.handle(fire(10, "zone:1429"), "pixel")
    eng.stop_playback()
    eng.drain()
    assert rig.provider.stuck()
    rig.clock.advance(600)                                  # the game was closed for 10 minutes
    eng.handle(p2.build_frame(ET.HELLO, seq=2), "pixel")
    assert not rig.provider.stuck()


def test_reset_session_clears_the_stuck_verdict(tmp_path):
    rig = stuck_rig(tmp_path)
    try:
        rig.provider.play(make_drop(), fade_in_ms=0)
    except sp.SpotifyStuckError:
        pass
    assert rig.provider.stuck() and rig.provider.status_dict()["stuck"]
    rig.provider.reset_session()
    assert not rig.provider.stuck()


def test_the_game_is_told_that_a_stuck_spotify_is_not_connected():
    from needledropper.panel_api import Companion

    st = {"badge": "connected", "stuck": True}
    fake = SimpleNamespace(_spotify_status=lambda: st, transports=[], providers=[])
    assert Companion._game_status(fake)["spotify"] == "error"
    st["stuck"] = False
    assert Companion._game_status(fake)["spotify"] == "connected"


# ------------------------------------------------ SPOT-VERIFY-RESEND-UNMANAGED --

def _slow_start(rig, *, resend, later):
    ok_routes(rig.http)
    no_progress = (200, player_state(DROP_TRACK, 0))
    rig.http.set("GET", "/v1/me/player", (204, None), no_progress, no_progress, *later)
    rig.http.set("PUT", "/v1/me/player/play", (204, None), resend, resend, resend)
    rig.http.add("PUT", "/v1/me/player", (204, None))


def test_a_failed_resend_does_not_leave_spotify_unmanaged(tmp_path):
    for i, resend in enumerate(((404, {"error": {"status": 404, "message": "Device not found"}}),
                                ConnectionError("reset"),
                                (502, {"error": {"status": 502, "message": "bad gateway"}}))):
        rig = make_rig(tmp_path / str(i))
        _slow_start(rig, resend=resend, later=[(200, player_state(DROP_TRACK, 0))] * 6)
        try:
            h = rig.provider.play(make_drop(), fade_in_ms=0)
        except sp.SpotifyStuckError as exc:                 # still not started: parked, never unmanaged
            assert isinstance(exc.cleanup, PlayHandle) and exc.cleanup.data["parked"]
            assert rig.http.find("PUT", "/v1/me/player/pause")
        else:
            raise AssertionError(f"{resend!r}: a song that never started returned {h}")


def test_a_late_start_after_a_failed_resend_is_managed(tmp_path):
    rig = make_rig(tmp_path)
    _slow_start(rig, resend=ConnectionError("reset"), later=[(200, player_state(DROP_TRACK, 5000))])
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    assert h.provider == "spotify" and not h.data.get("parked")
    assert h.data["session"] in rig.provider._sessions


def test_an_unavailable_error_after_the_accepted_play_parks_the_song(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", (204, None), (200, player_state(DROP_TRACK, 0)),
                 (403, {"error": {"status": 403, "message": "Player command failed: Premium required",
                                  "reason": "PREMIUM_REQUIRED"}}))
    try:
        rig.provider.play(make_drop(), fade_in_ms=0)
    except ProviderError as exc:
        assert isinstance(exc.cleanup, PlayHandle) and exc.cleanup.data["parked"]
    else:
        raise AssertionError("expected a refusal")


def test_the_engine_stops_a_cleanup_handle_from_an_unavailable_error():
    stopped = []

    class Refused(Provider):
        name = "spotify"

        def available(self):
            return True

        def can_play(self, drop):
            return True

        def play(self, drop, *, fade_in_ms=0):
            exc = sp.ProviderUnavailable("refused after the play command")
            exc.cleanup = PlayHandle(provider=self.name, drop=drop, data={"parked": True})
            raise exc

        def stop(self, handle, *, fade_out_ms=0):
            stopped.append(handle.data.get("parked"))

    clock = FakeClock()
    yt = FakeYouTube()
    eng = Engine([Refused(), yt], FakeDeck(), clock=clock, prewarm_async=False)
    eng.handle(fire(10, "zone:1429"), "pixel")
    assert yt.plays == ["zone:1429"]
    eng.stop_playback()
    eng.drain()
    assert stopped == [True]


# ------------------------------------------------------- ENG-HELLO-SEQ-ROLLBACK --

def test_a_crashed_sessions_lower_seq_still_ends_the_drop():
    clock = FakeClock()
    eng = Engine([FakeYouTube()], FakeDeck(), clock=clock, prewarm_async=False)
    eng.handle(p2.build_frame(ET.FIRE, seq=100, flags=0, drop_id="zone:1429", youtube=YT, artist="A",
                              title="B"), "pixel")
    assert eng.is_playing
    clock.advance(30)                                       # the client crashed and came back
    eng.handle(p2.build_frame(ET.HELLO, seq=61, flags=0), "pixel")
    clock.advance(6)
    eng.tick()
    assert not eng.is_playing


def test_a_late_chat_log_hello_right_after_the_fire_is_still_ignored():
    clock = FakeClock()
    eng = Engine([FakeYouTube()], FakeDeck(), clock=clock, prewarm_async=False)
    eng.handle(p2.build_frame(ET.FIRE, seq=100, flags=0, drop_id="zone:1429", youtube=YT, artist="A",
                              title="B"), "pixel")
    clock.advance(0.6)
    eng.handle(p2.build_frame(ET.HELLO, seq=99, flags=0), "chatlog")     # written before the FIRE
    for _ in range(8):
        clock.advance(1.0)
        eng.tick()
    assert eng.is_playing


def test_the_drop_stops_when_the_game_closes():
    from needledropper.panel_api import Companion

    calls = []
    fake = SimpleNamespace(engine=SimpleNamespace(is_playing=True, stop_playback=lambda: calls.append("stop")))
    Companion._game_closed(fake)
    assert calls == ["stop"]
    fake.engine.is_playing = False
    Companion._game_closed(fake)
    assert calls == ["stop"]


# ------------------------------------------------- journey F2: Spotify's window --

def test_the_spotify_app_is_not_launched_while_the_game_is_in_front(tmp_path):
    launched = []
    rig = make_rig(tmp_path)
    rig.provider._startfile = launched.append
    rig.provider._game_in_front = lambda: True
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": []}))
    assert rig.provider._wake_device() is None
    assert launched == [] and rig.clock.sleeps == []
    rig.provider._game_in_front = lambda: False
    rig.provider._wake_device()
    assert launched == ["spotify:"]


# ---------------------------------------------------- SHELL-FASTSTOP-BUSY-ENGINE --

def test_interrupt_ends_a_spotify_start_check_at_once(tmp_path):
    import threading
    import time

    from test_spotify_fakes import FakeHTTP, save_tokens
    from needledropper.providers.spotify import SpotifyProvider

    save_tokens(tmp_path)
    http = FakeHTTP()
    ok_routes(http)
    http.set("GET", "/v1/me/player", (204, None), *([(200, player_state(DROP_TRACK, 0))] * 20))
    # connect mode: the desktop app's start checks (the in-app ones: test_spotify_inapp.py)
    prov = SpotifyProvider({"clientId": "cid", "device": "connect"}, SimpleNamespace(app_dir=tmp_path), http=http,
                           startfile=lambda t: None, sleep=time.sleep)          # real waits
    threading.Timer(0.3, prov.interrupt).start()                                # Windows logs off
    t0 = time.monotonic()
    try:
        prov.play(make_drop(), fade_in_ms=0)
    except ProviderError as exc:
        assert isinstance(exc.cleanup, PlayHandle)        # accepted already: parked (paused), not left playing
    else:
        raise AssertionError("expected the start to be abandoned")
    assert time.monotonic() - t0 < 1.5                    # not the 6.5 s of start checks


def test_the_fast_stop_interrupts_providers_and_the_decks_waits():
    from needledropper.panel_api import Companion

    seen = []
    eng = SimpleNamespace(stop=lambda timeout: seen.append(("engine", timeout)))
    fake = SimpleNamespace(_hk_stop=SimpleNamespace(set=lambda: None), _hk_wake=SimpleNamespace(set=lambda: None),
                           providers=[SimpleNamespace(name="spotify", interrupt=lambda: seen.append("spotify")),
                                      SimpleNamespace(name="youtube")],
                           deck=SimpleNamespace(interrupt_waits=lambda: seen.append("deck")),
                           _lock=__import__("threading").RLock(), engine=eng)

    class Stop(Exception):
        pass

    def stop_after_engine(timeout):
        seen.append(("engine", timeout))
        raise Stop
    eng.stop = stop_after_engine
    try:
        Companion.stop(fake, fast=True)
    except Exception:                                     # the rest of stop() needs a real Companion
        pass
    assert seen[:3] == ["spotify", "deck", ("engine", 3.0)]


def test_deck_waits_end_when_interrupted():
    import time

    from needledropper.deck.server import Deck

    d = Deck(port=0)
    d.interrupt_waits()
    t0 = time.monotonic()
    assert d.wait_player(5) is False and d.wait_status(play_id="x", timeout=5) is None
    assert time.monotonic() - t0 < 0.5


# ---------------------------------------------------------- journey F6 / F10 / F8 --

def test_the_connect_page_asks_for_a_game_restart_when_the_data_addon_came_late():
    js = (Path(__file__).resolve().parents[1] / "companion" / "needledropper" / "deck" / "static" /
          "connect.js").read_text(encoding="utf-8")
    assert "addon.dataNeedsRestart" in js and "Restart World of Warcraft once" in js


def test_the_data_addon_made_while_the_game_runs_needs_a_restart(tmp_path):
    from needledropper.panel_api import Companion

    addons = tmp_path / "AddOns"
    addons.mkdir()
    running = [True]
    fake = SimpleNamespace(paths=SimpleNamespace(addons_dir=addons), game_running=lambda: running[0],
                           _data_needs_restart=False, _game_was_running=True, _game_closed=lambda: None)
    Companion._ensure_companion_data(fake)
    assert fake._data_needs_restart                       # made now: the running game can't see it
    Companion._ensure_companion_data(fake)                # already there: unchanged
    assert fake._data_needs_restart
    running[0] = False
    Companion._watch_game(fake)                           # the game closed: its next start loads it
    assert not fake._data_needs_restart
    other = tmp_path / "AddOns2"
    other.mkdir()
    fake2 = SimpleNamespace(paths=SimpleNamespace(addons_dir=other), game_running=lambda: False,
                            _data_needs_restart=False)
    Companion._ensure_companion_data(fake2)               # the game isn't running: nothing to say
    assert not fake2._data_needs_restart


def test_start_with_windows_refuses_a_temporary_copy_of_the_exe(monkeypatch, tmp_path):
    from needledropper import autostart

    monkeypatch.setenv("TEMP", str(tmp_path / "Temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "Temp"))
    zipped = tmp_path / "Temp" / "Temp1_NeedleDropper-1.2.0-win64.zip" / "NeedleDropper.exe"
    assert autostart.temporary_location(zipped)
    assert autostart.temporary_location(r"C:\Users\x\Downloads\Temp1_NeedleDropper.zip\NeedleDropper.exe")
    assert not autostart.temporary_location(tmp_path / "Documents" / "NeedleDropper" / "NeedleDropper.exe")
    monkeypatch.setattr(autostart, "is_frozen", lambda: True)
    monkeypatch.setattr(autostart.sys, "executable", str(zipped))
    assert autostart.temporary_location()

    class Reg:
        HKEY_CURRENT_USER = 1
        KEY_READ = KEY_WRITE = KEY_SET_VALUE = KEY_QUERY_VALUE = 0
        REG_SZ = REG_BINARY = 1
    with pytest.raises(autostart.AutostartError, match="temporary folder"):
        autostart.enable(winreg=Reg())
    with pytest.raises(autostart.AutostartError, match="temporary folder"):
        autostart.ensure(winreg=Reg())


def test_the_toc_names_the_curseforge_project():
    toc = (Path(__file__).resolve().parents[1] / "addon" / "NeedleDropper" / "NeedleDropper.toc").read_text(
        encoding="utf-8")
    assert "## X-Curse-Project-ID: 1786392" in toc.splitlines()


# -------------------------------------------------------- protocol review F5 --

def test_a_cancel_older_than_the_playing_fire_is_ignored():
    clock = FakeClock()
    yt = FakeYouTube()
    eng = Engine([yt], FakeDeck(), clock=clock, prewarm_async=False)
    replay = p2.build_frame(ET.FIRE, seq=3, flags=Flags.TEST, drop_id="zone:1411", youtube=YT, artist="A", title="B")
    eng.handle(replay, "pixel")
    clock.advance(30)                                       # a chat-log line that waited out a lockdown
    eng.handle(p2.build_frame(ET.CANCEL, seq=2, flags=0, drop_id="zone:1411"), "chatlog")
    assert eng.is_playing
    eng.handle(p2.build_frame(ET.CANCEL, seq=4, flags=0, drop_id="zone:1411"), "pixel")   # a real one
    assert not eng.is_playing
