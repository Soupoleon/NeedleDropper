"""A muted drop never sends play to a Connect device it cannot first silence."""
import pytest

from needledropper.providers.base import ProviderError
from test_spotify_fakes import DROP_TRACK, PC, make_drop, make_rig, ok_routes, player_state
from test_spotify_provider import err


@pytest.mark.parametrize("device", [dict(PC, supports_volume=False), dict(PC, volume_percent=None)])
def test_muted_drop_refuses_device_without_restorable_volume_control(tmp_path, device):
    rig = make_rig(tmp_path)
    ok_routes(rig.http, devices=[device])
    with pytest.raises(ProviderError, match="cannot mute.*"):
        rig.provider.play(make_drop(volume=0), fade_in_ms=2500)
    assert not rig.http.find("PUT", "/v1/me/player/play")
    assert not rig.http.find("PUT", "/v1/me/player/pause")
    assert not rig.http.find("PUT", "/v1/me/player/volume")


@pytest.mark.parametrize("status", [403, 404, 500])
def test_muted_drop_refuses_play_if_setting_zero_fails(tmp_path, status):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("PUT", "/v1/me/player/volume", err(status, message="Cannot control volume"))
    with pytest.raises(ProviderError, match="NeedleDropper \\(this app\\)"):
        rig.provider.play(make_drop(volume=0), fade_in_ms=0)
    assert not rig.http.find("PUT", "/v1/me/player/play")
    assert not rig.http.find("PUT", "/v1/me/player/pause")


def test_fixed_zero_volume_has_same_mute_guarantee(tmp_path):
    rig = make_rig(tmp_path, cfg={"volume": 0})
    ok_routes(rig.http, devices=[dict(PC, supports_volume=False)])
    with pytest.raises(ProviderError, match="cannot mute"):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not rig.http.find("PUT", "/v1/me/player/play")


def test_already_muted_device_does_not_need_volume_control(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http, devices=[dict(PC, supports_volume=False, volume_percent=0)])
    rig.provider.play(make_drop(volume=0), fade_in_ms=0)
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 1
    assert not rig.http.find("PUT", "/v1/me/player/volume")


def test_failed_play_restores_volume_after_successful_mute(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("PUT", "/v1/me/player/play", err(403, "RESTRICTION_VIOLATED"))
    with pytest.raises(ProviderError):
        rig.provider.play(make_drop(volume=0), fade_in_ms=0)
    assert [c.query["volume_percent"] for c in rig.http.find("PUT", "/v1/me/player/volume")] == ["0", "70"]


@pytest.mark.parametrize("replacement_controls", [False, True])
def test_404_replacement_is_muted_before_retry_and_original_volume_restored(tmp_path, replacement_controls):
    rig = make_rig(tmp_path)
    replacement = dict(PC, id="replacement", volume_percent=60, supports_volume=replacement_controls)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player/devices", (200, {"devices": [PC]}),
                 (200, {"devices": [replacement]}))
    rig.http.set("PUT", "/v1/me/player/play", err(404), (204, None))
    rig.http.set("GET", "/v1/me/player", (204, None),
                 (200, player_state(DROP_TRACK, 1200, device=replacement)))
    if replacement_controls:
        handle = rig.provider.play(make_drop(volume=0), fade_in_ms=0)
        assert handle.data["session"].volume_device == "replacement"
        calls = rig.http.calls
        muted = next(i for i, c in enumerate(calls) if c.path == "/v1/me/player/volume"
                     and c.query.get("device_id") == "replacement" and c.query.get("volume_percent") == "0")
        played = next(i for i, c in enumerate(calls) if c.path == "/v1/me/player/play"
                      and c.query.get("device_id") == "replacement")
        assert muted < played
        rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 5000,
                                                                device=dict(replacement, volume_percent=0))))
        rig.provider.stop(handle, fade_out_ms=0)
        assert rig.http.find("PUT", "/v1/me/player/volume")[-1].query == {
            "volume_percent": "60", "device_id": "replacement"}
    else:
        with pytest.raises(ProviderError, match="cannot mute"):
            rig.provider.play(make_drop(volume=0), fade_in_ms=0)
        assert [c.query["device_id"] for c in rig.http.find("PUT", "/v1/me/player/play")] == ["dev-pc"]
    assert any(c.query == {"volume_percent": "70", "device_id": "dev-pc"}
               for c in rig.http.find("PUT", "/v1/me/player/volume"))
