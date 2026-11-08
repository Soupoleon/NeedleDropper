"""Master volume is snapshotted once per drop and leaves provider restoration intact."""
from __future__ import annotations

import pytest

from needledropper.engine import Engine
from needledropper.models import Drop, EventType as ET, Flags
from test_v12_engine_flows import AFRICA, SP_ID, YT_ID, FakeYouTube, Rig
from test_spotify_fakes import DROP_TRACK, PC, make_drop, make_rig, ok_routes, player_state


@pytest.mark.parametrize("master, per_drop, expected", [(100, None, None), (100, 80, 80), (50, None, 50),
                                                       (50, 80, 40), (0, 80, 0), (75, 50, 38)])
def test_master_multiplies_per_drop_without_changing_source(master, per_drop, expected):
    r = Rig(music_volume=master)
    drop = Drop(n=0, id="test-spotify", kind="test", title="Song", artist="Artist",
                spotify=f"spotify:track:{SP_ID}", volume=per_drop)
    r.engine.play_now(drop, only="spotify")
    r.engine.drain()
    assert r.sp.of("play")[-1][-1] == expected
    assert drop.volume == per_drop


@pytest.mark.parametrize("service", ["spotify", "youtube", "local"])
def test_master_applies_to_each_companion_provider_and_service_test(service):
    class Capture(FakeYouTube):
        name = service

        def can_play(self, drop):
            return True

        def play(self, drop, **kw):
            self.volume = drop.volume
            return super().play(drop, **kw)

    provider = Capture()
    engine = Engine([provider], music_volume=30)
    drop = Drop(n=0, id=f"test-{service}", kind="test", title="Sample", artist="Artist", volume=50, youtube=YT_ID)
    engine.play_now(drop, test=True, only=service)
    engine.drain()
    assert provider.volume == 15


def test_active_song_and_delayed_fallback_keep_the_original_master():
    class Capture(FakeYouTube):
        def play(self, drop, **kw):
            self.volume = drop.volume
            return super().play(drop, **kw)

    yt = Capture()
    r = Rig(youtube=yt, music_volume=50)
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, youtube=YT_ID, volume=80, **AFRICA)
    assert r.sp.of("play")[-1][-1] == 40
    r.engine.set_music_volume(10)
    assert r.engine.is_playing and not r.sp.of("stop")
    r.sp.poll_state = "error"
    r.clock.advance(1.1)
    r.engine.tick()
    assert yt.volume == 40  # this fallback belongs to the same drop
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, volume=80,
           drop_id="zone:1411", artist="Artist", title="Next")
    assert yt.volume == 8


@pytest.mark.parametrize("master", [0, 50])
def test_spotify_connect_restores_original_device_volume_after_master_scaled_drop(tmp_path, master):
    rig = make_rig(tmp_path, cfg={"fade": False})
    ok_routes(rig.http)
    engine = Engine([rig.provider], music_volume=master, fade_in_ms=0, fade_out_ms=0)
    engine.play_now(make_drop(volume=80), only="spotify")
    engine.drain()
    target = int(70 * (80 * master / 100) / 100 + 0.5)
    assert rig.http.find("PUT", "/v1/me/player/volume")[-1].query["volume_percent"] == str(target)
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 5000,
                                                            device=dict(PC, volume_percent=target))))
    engine.stop_playback()
    engine.drain()
    assert rig.http.find("PUT", "/v1/me/player/volume")[-1].query["volume_percent"] == "70"
