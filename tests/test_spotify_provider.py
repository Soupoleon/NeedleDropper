"""SpotifyProvider against a scripted fake Spotify Web API."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_spotify_fakes import (DROP_TRACK, DROP_URI, PC, PHONE, FakeHTTP, make_drop, make_rig,  # noqa: E402
                                ok_routes, player_state, save_tokens, tid, track)

from needledropper.providers import spotify as sp  # noqa: E402
from needledropper.providers.base import ProviderError, ProviderUnavailable  # noqa: E402
from needledropper.providers.spotify import (PREMIUM_MESSAGE, SpotifyProvider, choose_best_track,  # noqa: E402
                                             normalize_track_uri, pick_device)

PREV = track("prev", "Previous Song", ["Someone"], duration=300_000)
ALBUM_CTX = {"type": "album", "uri": "spotify:album:" + tid("alb")}


def err(status, reason=None, message="error"):
    e = {"status": status, "message": message}
    if reason:
        e["reason"] = reason
    return (status, {"error": e})


# ------------------------------------------------------------ pure parts ----

def test_normalize_track_uri_variants():
    i = tid("abc")
    assert normalize_track_uri(f"spotify:track:{i}") == f"spotify:track:{i}"
    assert normalize_track_uri(f"https://open.spotify.com/intl-de/track/{i}?si=xyz") == f"spotify:track:{i}"
    assert normalize_track_uri(i) == f"spotify:track:{i}"
    assert normalize_track_uri(f"spotify:album:{i}") is None
    assert normalize_track_uri("spotify:track:short") is None
    assert normalize_track_uri("") is None and normalize_track_uri(None) is None


def test_pick_device_order():
    restricted_pc = dict(PC, id="r", name="Locked PC", is_restricted=True)
    speaker = {"id": "spk", "name": "Kitchen", "type": "Speaker", "is_active": False}
    laptop = dict(PC, id="lap", name="Laptop", is_active=True)
    devs = [restricted_pc, PHONE, speaker, PC, laptop]
    assert pick_device(devs, "kitchen")["id"] == "spk"           # configured name (case-insensitive)
    assert pick_device(devs, "kitch")["id"] == "spk"             # partial name
    assert pick_device(devs, "nope")["id"] == "lap"              # Computer, active one first
    assert pick_device(devs)["id"] == "lap"
    assert pick_device([restricted_pc, PHONE, speaker])["id"] == "dev-phone"  # active
    assert pick_device([restricted_pc, speaker])["id"] == "spk"               # first non-restricted
    assert pick_device([restricted_pc]) is None and pick_device([]) is None


def test_available_and_can_play(tmp_path):
    drop = make_drop()
    # connect mode: without the in-app player (no deck) app mode could never play (test_spotify_inapp.py)
    cm = {"device": "connect"}
    no_id = SpotifyProvider({"clientId": "", **cm}, SimpleNamespace(app_dir=tmp_path), http=FakeHTTP())
    assert not no_id.available() and "setup" in no_id.unavailable_reason()
    not_logged = SpotifyProvider({"clientId": "cid", **cm}, SimpleNamespace(app_dir=tmp_path), http=FakeHTTP())
    assert not not_logged.available() and not not_logged.can_play(drop)
    assert "spotify-login" in not_logged.unavailable_reason()
    save_tokens(tmp_path)
    p = SpotifyProvider({"clientId": "cid", **cm}, SimpleNamespace(app_dir=tmp_path), http=FakeHTTP())
    assert p.available() and p.can_play(drop)
    assert p.can_play(make_drop(spotify=None))                      # title + artist -> search
    assert not p.can_play(make_drop(spotify=None, artist=""))
    other = SpotifyProvider({"clientId": "someone-else", **cm}, SimpleNamespace(app_dir=tmp_path), http=FakeHTTP())
    assert not other.available()                                     # token belongs to another app


# -------------------------------------------------------------- playback ----

def test_play_sends_uris_position_and_device(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http, playing=player_state(DROP_TRACK, 15_400))
    drop = make_drop(startMs=15_000, durationMs=60_000)
    h = rig.provider.play(drop, fade_in_ms=0)
    plays = rig.http.find("PUT", "/v1/me/player/play")
    assert len(plays) == 1
    assert plays[0].query == {"device_id": "dev-pc"}
    assert plays[0].json == {"uris": [DROP_URI], "position_ms": 15_000}
    assert plays[0].headers["Content-Type"] == "application/json" and plays[0].bearer == "Bearer AT1"
    assert rig.http.find("PUT", "/v1/me/player/volume") == []      # no fade, no fixed volume
    np = h.now_playing
    assert np.art_url == "https://i.scdn.co/drop/640"
    assert np.link == f"https://open.spotify.com/track/{tid('drop')}"
    assert np.attribution == "Spotify" and np.provider == "spotify"
    assert (np.title, np.artist, np.label) == ("I'll Keep Coming", "Low Roar", "Elwynn Forest")
    assert h.provider == "spotify" and h.data["endMs"] == 75_000 and h.data["deviceId"] == "dev-pc"
    assert rig.provider.now_playing(h) is np


def test_play_verifies_and_resends_once(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", (204, None), (200, player_state(PREV, 10)),
                 (200, player_state(PREV, 20)), (200, player_state(DROP_TRACK, 500)))
    rig.provider.play(make_drop(), fade_in_ms=0)
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 2


def test_play_accepts_relinked_track(tmp_path):
    relinked = dict(track("relink", "I'll Keep Coming", ["Low Roar"]), linked_from={"uri": DROP_URI, "id": tid("drop")})
    rig = make_rig(tmp_path)
    ok_routes(rig.http, playing=player_state(relinked, 100))
    rig.provider.play(make_drop(), fade_in_ms=0)
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 1


def test_play_fails_when_track_never_starts(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http, playing=player_state(PREV, 10))
    with pytest.raises(ProviderError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not isinstance(ei.value, ProviderUnavailable) and "instead of" in str(ei.value)


def test_fade_in_ramps_volume_in_at_most_8_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "FADE_STEP_MIN_MS", 5)
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    h = rig.provider.play(make_drop(volume=50), fade_in_ms=200)
    h.data["session"].fade_thread.join(5)
    puts = rig.http.puts()
    vols = [int(c.query["volume_percent"]) for c in puts if c.path == "/v1/me/player/volume"]
    first_vol = next(i for i, c in enumerate(puts) if c.path == "/v1/me/player/volume")
    first_play = next(i for i, c in enumerate(puts) if c.path == "/v1/me/player/play")
    assert first_vol < first_play                       # quiet before the music starts
    target = 35                                         # device volume 70 * drop volume 50%
    assert vols[0] == round(target * sp.FADE_FLOOR)
    ramp = vols[1:]
    assert 1 <= len(ramp) <= 8 and ramp == sorted(ramp) and ramp[-1] == target
    assert all(c.query.get("device_id") == "dev-pc" for c in puts if c.path == "/v1/me/player/volume")


def test_fade_tolerates_volume_control_disallowed(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("PUT", "/v1/me/player/volume", err(403, "VOLUME_CONTROL_DISALLOW", "Cannot control device volume"))
    h = rig.provider.play(make_drop(), fade_in_ms=2500)
    if h.data["session"].fade_thread:
        h.data["session"].fade_thread.join(5)
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 1
    assert rig.provider.available()


def test_fixed_volume_is_applied_and_restored(tmp_path):
    rig = make_rig(tmp_path, cfg={"volume": 55, "fade": False})
    ok_routes(rig.http)
    h = rig.provider.play(make_drop(), fade_in_ms=2500)
    assert [c.query["volume_percent"] for c in rig.http.find("PUT", "/v1/me/player/volume")] == ["55"]
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 5000, device=dict(PC, volume_percent=55))))
    rig.provider.stop(h, fade_out_ms=3000)
    assert [c.query["volume_percent"] for c in rig.http.find("PUT", "/v1/me/player/volume")] == ["55", "70"]


# ---------------------------------------------------------------- errors ----

def test_401_refreshes_token_once_and_retries(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player/devices", err(401, message="The access token expired"),
                 (200, {"devices": [PC]}))
    rig.http.add("POST", "/api/token", (200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600}))
    rig.provider.play(make_drop(), fade_in_ms=0)
    tok = rig.http.find("POST", "/api/token")
    assert len(tok) == 1 and tok[0].form["refresh_token"] == "RT1" and tok[0].form["client_id"] == "cid"
    devs = rig.http.find("GET", "/v1/me/player/devices")
    assert [c.bearer for c in devs] == ["Bearer AT1", "Bearer AT2"]
    assert all(c.bearer == "Bearer AT2" for c in rig.http.find("PUT", "/v1/me/player/play"))
    from needledropper.providers.spotify_auth import TokenStore
    assert TokenStore(tmp_path).load()["refresh_token"] == "RT2"


def test_revoked_refresh_token_makes_provider_unavailable(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", err(401))
    rig.http.add("POST", "/api/token", (400, {"error": "invalid_grant", "error_description": "Refresh token revoked"}))
    with pytest.raises(ProviderUnavailable) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert "spotify-login" in str(ei.value)
    assert not rig.provider.available()


def test_403_premium_required_marks_unavailable_for_session(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("PUT", "/v1/me/player/play", err(403, "PREMIUM_REQUIRED", "Player command failed: Premium required"))
    with pytest.raises(ProviderUnavailable) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert str(ei.value) == PREMIUM_MESSAGE
    assert not rig.provider.available() and not rig.provider.can_play(make_drop())
    assert rig.provider.unavailable_reason() == PREMIUM_MESSAGE
    n = len(rig.http.calls)
    with pytest.raises(ProviderUnavailable):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert len(rig.http.calls) == n                      # no further API traffic this session
    rig.provider.prepare(make_drop())                    # never raises


def test_403_not_allowlisted_without_reason_is_unavailable(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", err(403, message="User not registered in the Developer Dashboard"))
    with pytest.raises(ProviderUnavailable):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not rig.provider.available()


def test_403_restriction_violated_is_a_plain_error(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("PUT", "/v1/me/player/play", err(403, "UNKNOWN", "Player command failed: Restriction violated"))
    with pytest.raises(ProviderError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not isinstance(ei.value, ProviderUnavailable)
    assert rig.provider.available()


def test_no_device_wakes_spotify_and_polls(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": []}), (200, {"devices": []}),
                 (200, {"devices": [], }), (200, {"devices": [PC]}))
    rig.provider.play(make_drop(), fade_in_ms=0)
    assert rig.started == ["spotify:"]
    assert rig.http.find("PUT", "/v1/me/player/play")[0].query["device_id"] == "dev-pc"


def test_404_no_active_device_wakes_and_retries_once(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("PUT", "/v1/me/player/play", err(404, "NO_ACTIVE_DEVICE", "Player command failed: No active device found"),
                 (204, None))
    rig.provider.play(make_drop(), fade_in_ms=0)
    assert rig.started == ["spotify:"]
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 2


def test_404_with_new_device_id_retries_without_launching_spotify(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    renamed = dict(PC, id="dev-pc-2")
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": [PC]}), (200, {"devices": [renamed]}))
    rig.http.set("PUT", "/v1/me/player/play", err(404, message="Device not found"), (204, None))
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    assert rig.started == []
    assert [c.query["device_id"] for c in rig.http.find("PUT", "/v1/me/player/play")] == ["dev-pc", "dev-pc-2"]
    assert h.data["session"].volume_device == "dev-pc-2"


def test_no_device_at_all_is_provider_error(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http, devices=[])
    with pytest.raises(ProviderError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not isinstance(ei.value, ProviderUnavailable) and "Open the Spotify app" in str(ei.value)
    assert rig.started == ["spotify:"]
    assert sum(rig.clock.sleeps) <= sp.WAKE_TIMEOUT_S + sp.WAKE_POLL_S


def test_429_honours_short_retry_after(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player/devices", (429, None, {"Retry-After": "2"}), (200, {"devices": [PC]}))
    rig.provider.play(make_drop(), fade_in_ms=0)
    assert 2.0 in rig.clock.sleeps
    assert len(rig.http.find("GET", "/v1/me/player/devices")) == 2


def test_429_with_long_retry_after_fails(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player/devices", (429, None, {"Retry-After": "30"}))
    with pytest.raises(ProviderError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not isinstance(ei.value, ProviderUnavailable) and "rate" in str(ei.value).lower()
    assert 30.0 not in rig.clock.sleeps and rig.provider.available()


def test_network_errors_are_provider_errors_and_prepare_never_raises(tmp_path):
    rig = make_rig(tmp_path)
    for method, path in (("GET", "/v1/me/player"), ("GET", "/v1/me/player/devices"), ("GET", "/v1/search")):
        rig.http.add(method, path, ConnectionError("network is unreachable"))
    rig.provider.prepare(make_drop())
    rig.provider.prepare(make_drop(spotify=None))
    with pytest.raises(ProviderError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert "Could not reach Spotify" in str(ei.value)
    assert rig.provider.available()


def test_prepare_wakes_spotify_in_background_and_play_reuses_it(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": []}), (200, {"devices": []}),
                 (200, {"devices": [PC]}))
    rig.provider.prepare(make_drop())                  # returns without waiting for the device poll
    rig.provider._wake_thread.join(5)
    assert rig.started == ["spotify:"]
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": []}))   # idle PC omitted again
    rig.provider.play(make_drop(), fade_in_ms=0)
    assert rig.started == ["spotify:"]                 # no second launch
    assert rig.http.find("PUT", "/v1/me/player/play")[0].query["device_id"] == "dev-pc"


def test_prepare_resolves_and_caches_device(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.add("GET", "/v1/search", (200, {"tracks": {"items": [DROP_TRACK]}}))
    rig.provider.prepare(make_drop(spotify=None))
    assert len(rig.http.find("GET", "/v1/search")) == 1
    # the device list later omits the idle PC: the device seen on ARM is reused
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": []}))
    rig.provider.play(make_drop(spotify=None), fade_in_ms=0)
    assert rig.started == [] and len(rig.http.find("GET", "/v1/search")) == 1
    assert rig.http.find("PUT", "/v1/me/player/play")[0].json["uris"] == [DROP_URI]


# ------------------------------------------------------- stop & restore ----

def _play_then_stop(tmp_path, snapshot, *, cfg=None, at_stop=None, fade_out_ms=0):
    rig = make_rig(tmp_path, cfg=cfg)
    ok_routes(rig.http, snapshot=snapshot)
    h = rig.provider.play(make_drop(durationMs=60_000), fade_in_ms=0)
    if at_stop is not None:
        rig.http.set("GET", "/v1/me/player", at_stop)
    before = len(rig.http.calls)
    rig.provider.stop(h, fade_out_ms=fade_out_ms)
    return rig, h, rig.http.calls[before:]


def test_stop_restores_album_context_with_offset_and_position(tmp_path):
    snap = (200, player_state(PREV, 42_000, device=PHONE, context=ALBUM_CTX))
    rig, h, after = _play_then_stop(tmp_path, snap)
    puts = [c for c in after if c.method == "PUT"]
    assert [c.path for c in puts] == ["/v1/me/player/pause", "/v1/me/player/play"]
    assert puts[0].query["device_id"] == "dev-pc"
    assert puts[1].query["device_id"] == "dev-phone"               # back where the user was listening
    assert puts[1].json == {"context_uri": ALBUM_CTX["uri"], "offset": {"uri": PREV["uri"]}, "position_ms": 42_000}
    rig.provider.stop(h, fade_out_ms=0)                             # idempotent
    assert len([c for c in rig.http.calls if c.method == "PUT"]) == 3


def test_stop_restores_single_track_when_context_is_not_album_or_playlist(tmp_path):
    ctx = {"type": "artist", "uri": "spotify:artist:" + tid("art")}
    rig, h, after = _play_then_stop(tmp_path, (200, player_state(PREV, 9_000, context=ctx)))
    restore = [c for c in after if c.path == "/v1/me/player/play"]
    assert len(restore) == 1 and restore[0].json == {"uris": [PREV["uri"]], "position_ms": 9_000}


def test_stop_with_nothing_playing_before_only_pauses(tmp_path):
    rig, h, after = _play_then_stop(tmp_path, (204, None))
    assert [c.path for c in after if c.method == "PUT"] == ["/v1/me/player/pause"]


def test_stop_does_not_restore_a_paused_snapshot_or_when_disabled(tmp_path):
    rig, h, after = _play_then_stop(tmp_path / "a", (200, player_state(PREV, 9_000, playing=False)))
    assert [c.path for c in after if c.method == "PUT"] == ["/v1/me/player/pause"]
    rig, h, after = _play_then_stop(tmp_path / "b", (200, player_state(PREV, 9_000)), cfg={"restorePrevious": False})
    assert [c.path for c in after if c.method == "PUT"] == ["/v1/me/player/pause"]


def test_stop_fades_out_then_pauses_then_restores_volume(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "FADE_STEP_MIN_MS", 5)
    rig, h, after = _play_then_stop(tmp_path, (200, player_state(PREV, 1_000)), fade_out_ms=100)
    puts = [(c.path.rsplit("/", 1)[-1], c.query.get("volume_percent")) for c in after if c.method == "PUT"]
    vols = [int(v) for p, v in puts if p == "volume"]
    pause_i = puts.index(("pause", None))
    assert 1 <= len(vols) - 1 <= 8 and vols[:-1] == sorted(vols[:-1], reverse=True) and vols[-2] == 0
    assert puts[-2] == ("volume", "70") and puts[pause_i - 1] == ("volume", "0")
    assert puts[-1][0] == "play"                                    # snapshot restored last


def test_stop_after_404_on_snapshot_device_falls_back_to_ours(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http, snapshot=(200, player_state(PREV, 5_000, device=PHONE, context=ALBUM_CTX)))
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    rig.http.set("PUT", "/v1/me/player/play", err(404, "NO_ACTIVE_DEVICE"), (204, None))
    rig.provider.stop(h, fade_out_ms=0)
    restores = rig.http.find("PUT", "/v1/me/player/play")[1:]
    assert [c.query["device_id"] for c in restores] == ["dev-phone", "dev-pc"]


def test_stop_never_raises(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http, snapshot=(200, player_state(PREV, 5_000)))
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    for key in list(rig.http.routes):
        rig.http.set(*key, RuntimeError("boom"))
    rig.provider.stop(h, fade_out_ms=3000)
    rig.provider.stop(h, fade_out_ms=3000)
    assert rig.provider.poll(h) == "ended"


# ------------------------------------------------------------------ poll ----

def _playing_rig(tmp_path, **drop_kw):
    rig = make_rig(tmp_path)
    ok_routes(rig.http, snapshot=(200, player_state(PREV, 7_000, context=ALBUM_CTX)),
              playing=player_state(DROP_TRACK, drop_kw.get("startMs", 0) + 300))
    h = rig.provider.play(make_drop(**drop_kw), fade_in_ms=0)
    return rig, h


def test_poll_is_rate_limited_and_ends_at_start_plus_duration(tmp_path):
    rig, h = _playing_rig(tmp_path, startMs=10_000, durationMs=30_000)
    gets = lambda: len(rig.http.find("GET", "/v1/me/player"))  # noqa: E731
    n = gets()
    rig.clock.advance(1.0)
    assert rig.provider.poll(h) == "playing" and gets() == n       # < 2 s since last read: no request
    rig.clock.advance(1.5)
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 25_000)))
    assert rig.provider.poll(h) == "playing" and gets() == n + 1
    rig.clock.advance(2.0)
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 40_100)))
    assert rig.provider.poll(h) == "ended"
    before = len(rig.http.calls)
    rig.provider.stop(h, fade_out_ms=0)
    puts = [c.path for c in rig.http.calls[before:] if c.method == "PUT"]
    assert puts == ["/v1/me/player/pause", "/v1/me/player/play"]   # we reached the end: pause + restore


def test_poll_user_changed_track_stops_managing(tmp_path):
    rig, h = _playing_rig(tmp_path, durationMs=120_000)
    rig.clock.advance(2.5)
    rig.http.set("GET", "/v1/me/player", (200, player_state(PREV, 5_000)))
    assert rig.provider.poll(h) == "ended"
    before = len(rig.http.calls)
    rig.provider.stop(h, fade_out_ms=3000)
    assert [c for c in rig.http.calls[before:] if c.method == "PUT"] == []   # never override the user's choice


def test_poll_user_paused_stops_managing(tmp_path):
    rig, h = _playing_rig(tmp_path, durationMs=120_000)
    rig.clock.advance(2.5)
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 12_000, playing=False)))
    assert rig.provider.poll(h) == "ended"
    before = len(rig.http.calls)
    rig.provider.stop(h, fade_out_ms=0)
    assert [c for c in rig.http.calls[before:] if c.method == "PUT"] == []


def test_poll_autoplay_rollover_at_natural_end_still_pauses_and_restores(tmp_path):
    rig, h = _playing_rig(tmp_path)                                # durationMs 0 -> natural end 245 s
    s = h.data["session"]
    assert s.natural_end and s.end_ms == 245_000
    rig.clock.advance(2.0)
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 241_000)))
    assert rig.provider.poll(h) == "playing"
    rig.clock.advance(2.5)                                         # track ended; Spotify rolled into autoplay
    autoplay = track("auto", "Something Else", ["Radio"])
    rig.http.set("GET", "/v1/me/player", (200, player_state(autoplay, 600)))
    assert rig.provider.poll(h) == "ended"
    before = len(rig.http.calls)
    rig.provider.stop(h, fade_out_ms=3000)
    puts = [c for c in rig.http.calls[before:] if c.method == "PUT"]
    assert [c.path for c in puts] == ["/v1/me/player/pause", "/v1/me/player/play"]
    assert puts[1].json["context_uri"] == ALBUM_CTX["uri"]


def test_poll_ends_early_before_natural_track_end(tmp_path):
    rig, h = _playing_rig(tmp_path)
    rig.clock.advance(2.0)
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 243_000)))
    assert rig.provider.poll(h) == "ended"                         # within END_LEAD_MS of 245 s
    # the engine stops "provider reports end" drops with fade 0: we play out the last
    # moments under a fade and pause just before the track (and autoplay) would roll on
    before, slept = len(rig.http.calls), sum(rig.clock.sleeps)
    rig.provider.stop(h, fade_out_ms=0)
    puts = [(c.path.rsplit("/", 1)[-1], c.query.get("volume_percent")) for c in rig.http.calls[before:]
            if c.method == "PUT"]
    vols = [int(v) for p, v in puts if p == "volume"]
    assert vols[:-1] == sorted(vols[:-1], reverse=True) and vols[-2] == 0 and vols[-1] == 70
    assert ("pause", None) in puts and puts[-1][0] == "play"
    assert 1.0 <= sum(rig.clock.sleeps) - slept <= 2.0             # ~1.7 s left before the end


# SPOT-POLL-ERROR-FALLBACK (baseline-findings.md): these two tests used to expect "error" after a
# few failed polls / a 403. "error" makes the engine start the next provider while Spotify -- which
# the API outage also prevents us from pausing -- keeps playing the same drop (double audio, Spotify
# left unmanaged). A poll that cannot read the state now keeps "playing" on the estimated position
# and reports "ended" at the planned end point.

def test_poll_keeps_playing_through_an_outage_and_ends_on_schedule(tmp_path):
    rig, h = _playing_rig(tmp_path, durationMs=20_000)
    rig.http.set("GET", "/v1/me/player", ConnectionError("offline"))
    results = []
    for _ in range(sp.MAX_POLL_FAILURES + 3):
        rig.clock.advance(2.1)
        results.append(rig.provider.poll(h))
    assert results == ["playing"] * (sp.MAX_POLL_FAILURES + 3)      # never "error": no fallback over Spotify
    rig.clock.advance(10.0)                                         # past the planned 20 s
    assert rig.provider.poll(h) == "ended"
    assert h.data["session"].outcome is None                        # nothing was decided from a failed read
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 20_500)))
    before = len(rig.http.calls)
    rig.provider.stop(h, fade_out_ms=0)                             # the API is back: pause + restore
    assert [c.path for c in rig.http.calls[before:] if c.method == "PUT"][:1] == ["/v1/me/player/pause"]


def test_poll_403_or_rate_limit_is_not_a_playback_error(tmp_path):
    rig, h = _playing_rig(tmp_path, durationMs=120_000)
    rig.clock.advance(2.1)
    rig.http.set("GET", "/v1/me/player", err(403, "PREMIUM_REQUIRED"))
    assert rig.provider.poll(h) == "playing"
    rig.clock.advance(2.1)
    rig.http.set("GET", "/v1/me/player", (429, {"error": {"status": 429, "message": "rate"}}, {"Retry-After": "20"}))
    assert rig.provider.poll(h) == "playing"
    gets = len(rig.http.find("GET", "/v1/me/player"))
    rig.clock.advance(5.0)
    assert rig.provider.poll(h) == "playing" and len(rig.http.find("GET", "/v1/me/player")) == gets  # backs off


# --------------------------------------------------------------- resolve ----

SEARCH_ITEMS = [
    track("kar", "I'll Keep Coming (Karaoke Version)", ["Karaoke Hits Band"], album="Karaoke Hits Vol. 9"),
    track("live", "I'll Keep Coming - Live at Iceland Airwaves", ["Low Roar"], album="Live at Iceland Airwaves"),
    track("rmx", "I'll Keep Coming (Ólafur Remix)", ["Low Roar", "Ólafur"]),
    track("good", "I'll Keep Coming", ["Low Roar"], duration=245_000, album="0",
          images=[{"url": "https://i.scdn.co/good/64", "width": 64, "height": 64},
                  {"url": "https://i.scdn.co/good/640", "width": 640, "height": 640}]),
    track("other", "I'll Keep Coming", ["Some Other Band"]),
]


def test_choose_best_track_prefers_studio_original():
    best = choose_best_track(SEARCH_ITEMS, "I'll Keep Coming", "Low Roar")
    assert best["id"] == tid("good")
    remaster = track("rem", "Rhiannon - 2004 Remaster", ["Fleetwood Mac"])
    feat = track("ft", "Dreams (feat. Somebody)", ["Fleetwood Mac", "Somebody"])
    assert choose_best_track([remaster], "Rhiannon", "Fleetwood Mac")["id"] == tid("rem")
    assert choose_best_track([feat], "Dreams", "Fleetwood Mac")["id"] == tid("ft")
    assert choose_best_track([track("x", "Totally Different", ["Low Roar"])], "I'll Keep Coming", "Low Roar") is None
    assert choose_best_track(SEARCH_ITEMS[:1], "I'll Keep Coming", "Low Roar") is None   # karaoke only
    live_wanted = choose_best_track(SEARCH_ITEMS, "I'll Keep Coming (Live)", "Low Roar")
    assert live_wanted["id"] == tid("live")


def test_resolve_drop_best_match_and_cache_write(tmp_path):
    rig = make_rig(tmp_path)
    rig.http.add("GET", "/v1/search", (200, {"tracks": {"items": SEARCH_ITEMS}}))
    drop = make_drop(spotify=None, startMs=5_000, durationMs=0)
    out = rig.provider.resolve_drop(drop)
    q = rig.http.find("GET", "/v1/search")[0].query
    assert q["q"] == 'track:"I\'ll Keep Coming" artist:"Low Roar"'
    assert q["type"] == "track" and q["limit"] == "10"
    assert out.spotify == f"spotify:track:{tid('good')}"
    assert out.durationMs == 240_000                                 # length to play from startMs
    assert out.extra["artUrl"] == "https://i.scdn.co/good/640"
    assert drop.spotify is None                                      # input not mutated
    cache = json.loads((tmp_path / "cache" / "resolved.json").read_text(encoding="utf-8"))
    entry = cache["zone:1429"]
    assert entry["spotify"] == out.spotify and entry["durationMs"] == 240_000
    assert entry["trackDurationMs"] == 245_000
    assert entry["artUrl"] == "https://i.scdn.co/good/640"
    assert entry["matchedTitle"] == "I'll Keep Coming" and entry["matchedArtist"] == "Low Roar"
    # cache hit: no second search, also for a fresh provider instance
    assert rig.provider.resolve_drop(drop).spotify == out.spotify
    fresh = make_rig(tmp_path, http=rig.http, tokens=False).provider
    assert fresh.resolve_drop(drop).spotify == out.spotify
    assert len(rig.http.find("GET", "/v1/search")) == 1
    # pack changed the title for the same drop id -> stale entry ignored
    rig.http.set("GET", "/v1/search", (200, {"tracks": {"items": []}}))
    changed = rig.provider.resolve_drop(make_drop(spotify=None, title="Bones"))
    assert changed.spotify is None
    # durationMs already known is kept; a changed startMs is recomputed from the track length
    assert rig.provider.resolve_drop(make_drop(spotify=None, durationMs=90_000)).durationMs == 90_000
    assert rig.provider.resolve_drop(make_drop(spotify=None, startMs=0)).durationMs == 245_000
    assert len(rig.http.find("GET", "/v1/search")) == 3          # 1 + the two "Bones" queries


def test_resolve_falls_back_to_plain_query(tmp_path):
    rig = make_rig(tmp_path)
    rig.http.add("GET", "/v1/search", (200, {"tracks": {"items": []}}),
                 (200, {"tracks": {"items": [None, SEARCH_ITEMS[3]]}}))
    out = rig.provider.resolve_drop(make_drop(spotify=None))
    assert out.spotify == f"spotify:track:{tid('good')}"
    qs = [c.query["q"] for c in rig.http.find("GET", "/v1/search")]
    assert qs == ['track:"I\'ll Keep Coming" artist:"Low Roar"', "I'll Keep Coming Low Roar"]


def test_resolve_no_confident_match_leaves_drop_and_cache_alone(tmp_path):
    rig = make_rig(tmp_path)
    rig.http.add("GET", "/v1/search", (200, {"tracks": {"items": SEARCH_ITEMS[:1]}}))
    drop = make_drop(spotify=None)
    assert rig.provider.resolve_drop(drop).spotify is None
    assert not (tmp_path / "cache" / "resolved.json").exists()
    n = len(rig.http.find("GET", "/v1/search"))
    rig.provider.resolve_drop(drop)                                  # remembered for the session
    assert len(rig.http.find("GET", "/v1/search")) == n
    with pytest.raises(ProviderError):
        rig.provider.play(drop, fade_in_ms=0)


def test_resolve_with_known_uri_fetches_track_for_duration(tmp_path):
    rig = make_rig(tmp_path)
    rig.http.add("GET", f"/v1/tracks/{tid('drop')}", (200, DROP_TRACK))
    out = rig.provider.resolve_drop(make_drop(spotify=f"https://open.spotify.com/track/{tid('drop')}?si=1"))
    assert out.spotify == DROP_URI and out.durationMs == 245_000
    assert out.extra["artUrl"] == "https://i.scdn.co/drop/640"
    assert rig.http.find("GET", f"/v1/tracks/{tid('drop')}")[0].query["market"] == "from_token"


def test_corrupt_cache_file_is_tolerated(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "resolved.json").write_text("{not json", encoding="utf-8")
    rig = make_rig(tmp_path)
    rig.http.add("GET", "/v1/search", (200, {"tracks": {"items": [SEARCH_ITEMS[3]]}}))
    assert rig.provider.resolve_drop(make_drop(spotify=None)).spotify == f"spotify:track:{tid('good')}"
    assert "zone:1429" in json.loads((tmp_path / "cache" / "resolved.json").read_text(encoding="utf-8"))
