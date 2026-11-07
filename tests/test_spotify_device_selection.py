"""Connect output selection never silently routes an explicit choice to another device."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_spotify_fakes import DROP_TRACK, PC, PHONE, make_drop, make_rig, ok_routes, player_state
from test_spotify_inapp import make_app_rig, plays_and_ticks
from needledropper.providers.base import ProviderError
from needledropper.providers.spotify import SpotifyStuckError, pick_device


def test_explicit_id_wins_over_name_and_duplicate_names():
    other = dict(PC, id="other-pc")
    assert pick_device([other, PC], PC["name"]) is None
    assert pick_device([other, PC], "Old display name", PC["id"]) == PC
    assert pick_device([PC], PC["name"], "missing-id") is None
    assert pick_device([PC], "", PC["id"].upper()) is None
    assert pick_device([dict(PC, is_restricted=True), other], PC["name"], PC["id"]) is None
    assert pick_device([PC], "  gaming-pc  ") == PC
    assert pick_device([PC, PC], PC["name"]) == PC  # duplicate rows for one ID are not two outputs


@pytest.mark.parametrize("cfg", [{"deviceId": PC["id"], "deviceName": PC["name"]},
                                 {"deviceName": PC["name"]}])
def test_offline_selection_never_uses_another_device_cache_or_wake(tmp_path, cfg):
    rig = make_rig(tmp_path, cfg=cfg)
    ok_routes(rig.http, devices=[PC, PHONE])
    rig.provider.prepare(make_drop())
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": [PHONE]}))
    rig.provider.prepare(make_drop())  # no background wake for selected targets
    with pytest.raises(ProviderError, match="Refresh"):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert rig.http.puts() == [] and rig.started == []
    assert rig.provider._wake_thread is None and rig.provider._cached_device() is None
    assert rig.provider.available() and not rig.provider.stuck()


def test_ambiguous_legacy_name_requires_selection_by_id(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceName": PC["name"]})
    ok_routes(rig.http, devices=[PC, dict(PC, id="other-pc")])
    with pytest.raises(ProviderError, match="More than one"):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert rig.http.puts() == [] and rig.started == []


def test_restricted_explicit_device_is_not_used_from_an_old_cache(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http, devices=[PC])
    rig.provider.prepare(make_drop())
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": [dict(PC, is_restricted=True), PHONE]}))
    with pytest.raises(ProviderError, match="cannot be controlled"):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert rig.http.puts() == [] and rig.started == []


def test_a_404_does_not_retarget_a_pinned_id_even_to_the_same_name(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"], "deviceName": PC["name"]})
    ok_routes(rig.http)
    replacement = dict(PC, id="replacement")
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": [PC]}),
                 (200, {"devices": [replacement, PHONE]}))
    rig.http.set("PUT", "/v1/me/player/play", (404, {"error": {"message": "Device not found"}}))
    with pytest.raises(ProviderError, match="Refresh"):
        rig.provider.play(make_drop(), fade_in_ms=0)
    plays = rig.http.find("PUT", "/v1/me/player/play")
    assert [c.query["device_id"] for c in plays] == [PC["id"]]
    assert rig.started == [] and not rig.provider.stuck()


def test_a_404_retries_the_same_listed_pinned_id_once_without_waking(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http)
    rig.http.set("PUT", "/v1/me/player/play", (404, {"error": {"message": "Device not found"}}), (204, None))
    handle = rig.provider.play(make_drop(), fade_in_ms=0)
    assert handle.data["deviceId"] == PC["id"]
    assert [c.query["device_id"] for c in rig.http.find("PUT", "/v1/me/player/play")] == [PC["id"], PC["id"]]
    assert rig.started == []


def test_legacy_exact_name_can_recover_its_unique_replacement_id(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceName": PC["name"]})
    replacement = dict(PC, id="replacement")
    ok_routes(rig.http, playing=player_state(DROP_TRACK, 1200, device=replacement))
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": [PC]}),
                 (200, {"devices": [PHONE, replacement]}))
    rig.http.set("PUT", "/v1/me/player/play", (404, {"error": {"message": "Device not found"}}), (204, None))
    handle = rig.provider.play(make_drop(), fade_in_ms=0)
    assert handle.data["deviceId"] == replacement["id"] and rig.started == []


@pytest.mark.parametrize("cfg", [{}, {"deviceId": PC["id"]}, {"deviceName": PC["name"]}])
def test_same_song_on_another_device_never_confirms_the_requested_start(tmp_path, cfg):
    rig = make_rig(tmp_path, cfg=cfg)
    ok_routes(rig.http, playing=player_state(DROP_TRACK, 1200, device=PHONE))
    with pytest.raises(ProviderError, match="another device") as raised:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not isinstance(raised.value, SpotifyStuckError) and not rig.provider.stuck()
    assert all(c.query.get("device_id") == PC["id"] or c.json == {"device_ids": [PC["id"]], "play": True}
               for c in rig.http.puts())
    if cfg:
        before = len(rig.http.calls)
        rig.provider.stop(raised.value.cleanup)
        assert not [c for c in rig.http.calls[before:] if c.method == "PUT"]


def test_stale_other_device_with_the_same_song_can_settle_on_the_selected_output(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 5000, device=PHONE)),
                 (200, player_state(DROP_TRACK, 5100, device=PHONE)),
                 (200, player_state(DROP_TRACK, 600, device=PC)))
    handle = rig.provider.play(make_drop(), fade_in_ms=0)
    assert handle.data["deviceId"] == PC["id"] and not rig.provider.stuck()
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 1


def test_other_devices_stuck_state_does_not_abort_wait_for_selected_device(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http)
    stale = {"device": PHONE, "item": None, "is_playing": False,
             "actions": {"disallows": {"pausing": True, "resuming": True}}}
    rig.http.set("GET", "/v1/me/player", (204, None), (200, stale), (200, stale),
                 (200, player_state(DROP_TRACK, 600, device=PC)))
    handle = rig.provider.play(make_drop(), fade_in_ms=0)
    assert handle.data["deviceId"] == PC["id"] and not rig.provider.stuck()
    assert all(c.query.get("device_id") == PC["id"] for c in rig.http.puts())


@pytest.mark.parametrize("cfg", [{"deviceId": PC["id"]}, {"deviceName": PC["name"]}])
@pytest.mark.parametrize("ended_already", [False, True])
def test_selected_output_move_is_user_takeover_and_never_pauses_other_device(tmp_path, cfg, ended_already):
    rig = make_rig(tmp_path, cfg=cfg)
    ok_routes(rig.http)
    handle = rig.provider.play(make_drop(durationMs=60_000), fade_in_ms=0)
    session = handle.data["session"]
    if ended_already:
        session.outcome = "end"  # poll reached the end; the user transfers just before stop
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 2000, device=PHONE)))
    rig.clock.advance(3)
    if not ended_already:
        assert rig.provider.poll(handle) == "ended"
    before = len(rig.http.calls)
    rig.provider.stop(handle)
    assert session.device_id == PC["id"]
    assert not [c for c in rig.http.calls[before:] if c.method == "PUT"]


def test_other_devices_health_does_not_mark_selected_player_stuck(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", (200, {"device": PHONE, "item": None,
                 "actions": {"disallows": {"pausing": True, "resuming": True}}}))
    assert rig.provider.check_health() is None and not rig.provider.stuck()


def test_unreadable_state_does_not_claim_selected_device_played(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", ConnectionError("offline"))
    with pytest.raises(ProviderError, match="Could not confirm") as raised:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not isinstance(raised.value, SpotifyStuckError) and not rig.provider.stuck()
    assert all(c.query.get("device_id") == PC["id"] for c in rig.http.puts())


def test_pinned_output_still_restores_the_previous_music_when_untouched(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    previous = dict(DROP_TRACK, uri="spotify:track:" + "previous".ljust(22, "0"))
    ok_routes(rig.http, snapshot=(200, player_state(previous, 5000, device=PHONE)))
    handle = rig.provider.play(make_drop(durationMs=60_000), fade_in_ms=0)
    before = len(rig.http.calls)
    rig.provider.stop(handle)
    commands = [c for c in rig.http.calls[before:] if c.method == "PUT"]
    assert [(c.path, c.query["device_id"]) for c in commands] == [
        ("/v1/me/player/pause", PC["id"]), ("/v1/me/player/play", PHONE["id"])]


def test_app_mode_ignores_saved_connect_selection(tmp_path):
    rig = make_app_rig(tmp_path, cfg={"deviceId": "offline-device", "deviceName": "Offline speaker"})
    plays_and_ticks(rig)
    handle = rig.provider.play(make_drop(), fade_in_ms=0)
    assert handle.data["app"] and handle.data["deviceId"] in rig.page.registered
    assert rig.provider.status_dict()["deviceId"] == "offline-device"
