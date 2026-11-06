"""Addon audio: zone-music ducking / restore, CVar crash safety (SPEC 4.4)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import SILENCE, AddonHarness, make_map  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

LOCAL_FILE = "Interface\\AddOns\\NeedleDropper_Local\\media\\elwynn.mp3"
DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "durationMs": 30000},
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "durationMs": 30000,
     "file": LOCAL_FILE},
]
MAP = make_map(DROPS)


def fire_elwynn(h: AddonHarness):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert h.events_of(ET.FIRE)


def test_duck_silence_plays_silence_and_restore_stops_music_and_kicks_zone_music():
    h = AddonHarness(map=MAP)
    fire_elwynn(h)
    assert h.music() == [["play", SILENCE]]
    assert h.cvar_sets() == []
    h.advance(28.45)                           # the 30 s drop (fired at +10 s) ends
    assert h.music() == [["play", SILENCE], ["stop"]]
    sets = [(c["name"], c["value"]) for c in h.cvar_sets()]
    assert sets == [("Sound_EnableMusic", "0")]
    assert h.account_db()["savedCVars"] == {"Sound_EnableMusic": "1"}   # crash-safe while kicking
    h.advance(0.2)
    sets = [(c["name"], c["value"]) for c in h.cvar_sets()]
    assert sets == [("Sound_EnableMusic", "0"), ("Sound_EnableMusic", "1")]
    assert h.cvar("Sound_EnableMusic") == "1"
    assert h.account_db().get("savedCVars") is None
    kick = h.cvar_sets()
    assert abs((kick[1]["t"] - kick[0]["t"]) - 0.1) < 1e-6


def test_no_kick_when_disabled_or_music_off():
    h = AddonHarness(map=MAP, settings={"kickZoneMusic": False})
    fire_elwynn(h)
    h.advance(30)
    assert h.music()[-1] == ["stop"] and h.cvar_sets() == []
    h2 = AddonHarness(map=MAP, cvars={"Sound_EnableMusic": "0"})
    fire_elwynn(h2)
    h2.advance(30)
    assert h2.cvar_sets() == [] and h2.cvar("Sound_EnableMusic") == "0"


def test_duck_volume_saves_and_restores_music_volume():
    h = AddonHarness(map=MAP, settings={"duckMethod": "volume"}, cvars={"Sound_MusicVolume": "0.55"})
    fire_elwynn(h)
    assert h.music() == []
    assert h.cvar("Sound_MusicVolume") == "0"
    assert h.account_db()["savedCVars"] == {"Sound_MusicVolume": "0.55"}
    h.advance(30)
    assert h.cvar("Sound_MusicVolume") == "0.55"
    assert h.account_db().get("savedCVars") is None
    assert h.cvar("Sound_EnableMusic") == "1"
    assert all(c["name"] != "Sound_EnableMusic" for c in h.cvar_sets())   # never mutes music


def test_duck_volume_prefers_temp_cvar():
    h = AddonHarness(map=MAP, settings={"duckMethod": "volume"},
                     before_load=lambda hh: hh.shim.EnableTempCVar())
    fire_elwynn(h)
    temp = [c for c in h.cvar_sets() if c.get("temp")]
    assert temp and temp[0]["name"] == "Sound_MusicVolume" and temp[0]["value"] == "0"
    h.advance(30)
    assert h.cvar("Sound_MusicVolume") == "0.4"


def test_duck_none_leaves_music_alone():
    h = AddonHarness(map=MAP, settings={"duckMethod": "none"})
    fire_elwynn(h)
    h.advance(30)
    assert h.music() == [] and h.cvar_sets() == []


def test_volume_method_switches_to_silence_for_in_game_files():
    h = AddonHarness(map=MAP, settings={"duckMethod": "volume"}, state={"playableFiles": {LOCAL_FILE: True}})
    h.set_location(1436, "")
    h.advance(11)
    fire = h.events_of(ET.FIRE)[0]
    assert fire["flags"] & int(Flags.LOCAL_PLAYBACK)
    assert h.music() == [["play", SILENCE]]
    assert h.cvar("Sound_MusicVolume") == "0.4"


def test_crash_left_cvars_are_restored_at_login():
    db = {"savedCVars": {"Sound_MusicVolume": "0.7", "Sound_EnableMusic": "1"}}
    h = AddonHarness(map=MAP, db=db, cvars={"Sound_MusicVolume": "0", "Sound_EnableMusic": "0"})
    assert h.cvar("Sound_MusicVolume") == "0.7"
    assert h.cvar("Sound_EnableMusic") == "1"
    assert h.account_db().get("savedCVars") is None
    assert any("restored sound settings" in line for line in h.prints())


def test_logout_mid_drop_restores_volume():
    h = AddonHarness(map=MAP, settings={"duckMethod": "volume"})
    fire_elwynn(h)
    assert h.cvar("Sound_MusicVolume") == "0"
    h.logout()
    assert h.cvar("Sound_MusicVolume") == "0.4"
    assert h.account_db().get("savedCVars") is None


def test_logout_during_kick_restores_music_enabled():
    h = AddonHarness(map=MAP)
    fire_elwynn(h)
    h.advance(28.45)
    assert h.cvar("Sound_EnableMusic") == "0"
    h.logout()
    assert h.cvar("Sound_EnableMusic") == "1"
    assert h.account_db().get("savedCVars") is None


def test_cancel_restores_audio():
    h = AddonHarness(map=MAP)
    fire_elwynn(h)
    h.slash("stop")
    assert h.music()[-1] == ["stop"]
    h.advance(0.2)
    assert h.cvar("Sound_EnableMusic") == "1"


def test_replacing_a_playing_drop_keeps_music_ducked_once():
    h = AddonHarness(map=MAP)
    fire_elwynn(h)
    h.slash("play zone:1436")
    assert h.music() == [["play", SILENCE]]
    h.slash("stop")
    assert h.music() == [["play", SILENCE], ["stop"]]


def test_selftest_music_ducks_for_eight_seconds():
    h = AddonHarness(map=MAP)
    out = h.slash("selftest music")
    assert h.music() == [["play", SILENCE]]
    assert any("8 s" in line for line in out)
    h.advance(7.5)
    assert h.music() == [["play", SILENCE]]
    h.advance(1)
    assert h.music()[-1] == ["stop"]
    assert any("come back" in line for line in h.prints()[-2:])


def test_selftest_music_refuses_while_drop_plays():
    h = AddonHarness(map=MAP)
    fire_elwynn(h)
    out = h.slash("selftest music")
    assert any("stop it first" in line for line in out)
