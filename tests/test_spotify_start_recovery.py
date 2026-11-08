"""Hermetic regressions for unconfirmed Connect starts and manual-playback ownership."""
import logging
import json

import pytest

from needledropper.providers import spotify as sp
from needledropper.providers.base import ProviderError
from test_spotify_fakes import DROP_TRACK, DROP_URI, PC, PHONE, make_drop, make_rig, ok_routes, player_state, track

MANUAL = track("manual", "Manual song", ["A band"])


def test_default_http_preserves_target_method_and_track_body(tmp_path, monkeypatch):
    from needledropper.providers import spotify_auth

    rig = make_rig(tmp_path)
    rig.provider._http = spotify_auth.urllib_http
    sent = []

    class Response:
        status, headers = 204, {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b""

    def receive(request, timeout):
        sent.append(request)
        return Response()

    monkeypatch.setattr(spotify_auth.urllib.request, "urlopen", receive)
    rig.provider._play_command(PC["id"], DROP_URI, 30_000)
    assert len(sent) == 1 and sent[0].get_method() == "PUT"
    assert sent[0].full_url == "https://api.spotify.com/v1/me/player/play?device_id=dev-pc"
    assert sent[0].get_header("Content-type") == "application/json"
    assert json.loads(sent[0].data) == {"uris": [DROP_URI], "position_ms": 30_000}


def empty_state():
    return {"device": dict(PC), "item": None, "is_playing": False, "progress_ms": 0,
            "actions": {"disallows": {"pausing": True, "resuming": True, "transferring_playback": True}}}


def test_failed_start_does_not_pause_manual_song_and_restores_volume(tmp_path, caplog):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    manual = player_state(MANUAL, 42_000)
    ok_routes(rig.http, snapshot=(200, manual), playing=manual)
    rig.http.add("PUT", "/v1/me/player", (204, None))
    caplog.set_level(logging.INFO, logger="needledropper.spotify")
    with pytest.raises(sp.SpotifyStuckError) as raised:
        rig.provider.play(make_drop(), fade_in_ms=2500)
    assert not raised.value.loaded
    assert not rig.http.find("PUT", "/v1/me/player/pause")
    assert [c.query["volume_percent"] for c in rig.http.find("PUT", "/v1/me/player/volume")] == ["7", "70"]
    messages = [r.getMessage() for r in caplog.records]
    baseline = next(i for i, m in enumerate(messages) if "phase=before-play" in m)
    first_command = next(i for i, m in enumerate(messages) if "Spotify PUT" in m)
    assert baseline < first_command
    assert "target=dev-pc observed=dev-pc" in messages[baseline]
    assert "item=" + MANUAL["uri"] in messages[baseline]
    assert "is_playing=True progress_ms=42000 active=False restricted=False" in messages[baseline]
    before = len(rig.http.calls)
    rig.provider.stop(raised.value.cleanup, fade_out_ms=0)
    assert not [c for c in rig.http.calls[before:] if c.method == "PUT"]  # manual song still plays


@pytest.mark.parametrize("observed", [player_state(MANUAL, 42_000), player_state(DROP_TRACK, 500, device=PHONE)])
def test_generic_verification_failure_does_not_pause_unowned_playback(tmp_path, monkeypatch, observed):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http, snapshot=(200, player_state(MANUAL, 40_000)), playing=observed)

    def fail(*args):
        raise ProviderError("verification could not finish")

    monkeypatch.setattr(rig.provider, "_verify_started", fail)
    with pytest.raises(ProviderError) as raised:
        rig.provider.play(make_drop(), fade_in_ms=2500)
    assert raised.value.cleanup.data["parked"]
    assert not rig.http.find("PUT", "/v1/me/player/pause")
    assert rig.http.find("PUT", "/v1/me/player/volume")[-1].query["volume_percent"] == "70"


def test_generic_failure_still_pauses_confirmed_late_start_on_our_device(tmp_path, monkeypatch):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)

    def fail(*args):
        raise ProviderError("verification could not finish")

    monkeypatch.setattr(rig.provider, "_verify_started", fail)
    with pytest.raises(ProviderError) as raised:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert [c.query["device_id"] for c in rig.http.find("PUT", "/v1/me/player/pause")] == [PC["id"]]
    rig.provider.stop(raised.value.cleanup, fade_out_ms=0)
    assert len(rig.http.find("PUT", "/v1/me/player/pause")) == 2  # late-start end cleanup retained


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("observed_device", [PHONE, {}, None])
def test_late_cleanup_never_pauses_same_track_without_our_device_identity(tmp_path, pinned, observed_device):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"] if pinned else ""})
    ok_routes(rig.http, playing=empty_state())
    with pytest.raises(sp.SpotifyStuckError) as raised:
        rig.provider.play(make_drop(), fade_in_ms=0)
    late = player_state(DROP_TRACK, 5000)
    late["device"] = observed_device
    rig.http.set("GET", "/v1/me/player", (200, late))
    before = len(rig.http.calls)
    rig.provider.stop(raised.value.cleanup, fade_out_ms=0)
    assert not [c for c in rig.http.calls[before:] if c.method == "PUT"]


def test_transient_target_empty_state_can_start_without_resend_or_transfer(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", (204, None), (200, empty_state()), (200, empty_state()),
                 (200, player_state(DROP_TRACK, 500)))
    handle = rig.provider.play(make_drop(), fade_in_ms=0)
    assert handle.data["deviceId"] == PC["id"] and not rig.provider.stuck()
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 1
    assert not rig.http.find("PUT", "/v1/me/player")
    assert sum(rig.clock.sleeps) == 2.0


def test_persistent_empty_target_is_observed_for_bounded_budget_without_commands(tmp_path, caplog):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http, playing=empty_state())
    caplog.set_level(logging.INFO, logger="needledropper.spotify")
    with pytest.raises(sp.SpotifyStuckError):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert sum(rig.clock.sleeps) == 6.5
    assert [c.path for c in rig.http.puts()] == ["/v1/me/player/play"]
    assert "phase=initial target=dev-pc observed=dev-pc" in caplog.text
    assert "phase=observe target=dev-pc observed=dev-pc" in caplog.text


def test_no_active_state_does_not_claim_the_selected_device_disappeared(tmp_path):
    rig = make_rig(tmp_path, cfg={"deviceId": PC["id"]})
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", (204, None))
    rig.http.add("PUT", "/v1/me/player", (204, None))
    with pytest.raises(ProviderError, match="never reported playback") as raised:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not isinstance(raised.value, sp.SpotifyStuckError)
    assert not rig.http.find("PUT", "/v1/me/player/pause")
