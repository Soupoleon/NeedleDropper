"""Addon audio: zone-music ducking / restore, CVar crash safety (SPEC 4.4), with the v1.2 rules:

* real-client finding 6 (2026-11-05): a Spotify / YouTube drop fades the zone music only while
  the NeedleDropper app is known (NeedleDropper_CompanionData) or with ``duckWithoutApp``;
  in-game files always fade it, songless drops (the test card) never do;
* baseline ND-AUDIO-TEMPCVAR: a volume duck made with C_CVar.SetTempCVar is undone with
  RemoveTempCVar (the shim models the temp layer: GetCVar reads through it, SetCVar does not
  clear it);
* baseline ND-DIR-02: a drop never starts inside the 0.1 s Sound_EnableMusic kick of a restore.

v1.2 migration: the v1 test map became a built-in test soundtrack (Elwynn) under a soundtrack of
the player's own (Westfall with an in-game file: only those carry files).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import MEDIA_PATH, SILENCE, AddonHarness, make_set, user_db  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

LOCAL_FILE = MEDIA_PATH + "elwynn.mp3"
SET = make_set([{"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "durationMs": 30000}])
LOCAL_DROPS = [{"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "durationMs": 30000,
                "file": "elwynn.mp3"}]
APP = {"schema": 1, "appVersion": "1.2.0", "written": 1762365000, "status": {"spotify": "connected", "youtube": True}}


def make(app=APP, **kw) -> AddonHarness:
    db = user_db(LOCAL_DROPS, active=["user:test", "builtin:test-set"])
    db.update(kw.pop("db", None) or {})
    if app is not None:
        kw["companion_data"] = app
    return AddonHarness(sets=[SET], db=db, **kw)


def fire_elwynn(h: AddonHarness):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert h.events_of(ET.FIRE)


def test_duck_silence_plays_silence_and_restore_stops_music_and_kicks_zone_music():
    h = make()
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
    h = make(settings={"kickZoneMusic": False})
    fire_elwynn(h)
    h.advance(30)
    assert h.music()[-1] == ["stop"] and h.cvar_sets() == []
    h2 = make(cvars={"Sound_EnableMusic": "0"})
    fire_elwynn(h2)
    h2.advance(30)
    assert h2.cvar_sets() == [] and h2.cvar("Sound_EnableMusic") == "0"


def test_duck_volume_saves_and_restores_music_volume():
    h = make(settings={"duckMethod": "volume"}, cvars={"Sound_MusicVolume": "0.55"})
    fire_elwynn(h)
    assert h.music() == []
    assert h.cvar("Sound_MusicVolume") == "0"
    assert h.account_db()["savedCVars"] == {"Sound_MusicVolume": "0.55"}
    h.advance(30)
    assert h.cvar("Sound_MusicVolume") == "0.55"
    assert h.account_db().get("savedCVars") is None
    assert h.cvar("Sound_EnableMusic") == "1"
    assert all(c["name"] != "Sound_EnableMusic" for c in h.cvar_sets())   # never mutes music


def test_duck_volume_prefers_temp_cvar_and_undoes_it_with_remove_temp_cvar():
    h = make(settings={"duckMethod": "volume"}, before_load=lambda hh: hh.shim.EnableTempCVar())
    fire_elwynn(h)
    temp = [c for c in h.cvar_sets() if c.get("temp")]
    assert temp and temp[0]["name"] == "Sound_MusicVolume" and temp[0]["value"] == "0"
    assert h.effective_cvar("Sound_MusicVolume") == "0"      # zone music silent...
    assert h.cvar("Sound_MusicVolume") == "0.4"              # ...the stored setting never touched
    h.advance(30)
    assert [c for c in h.cvar_sets() if c.get("removeTemp")], "RemoveTempCVar was not called"
    assert not [c for c in h.cvar_sets() if c["name"] == "Sound_MusicVolume" and not c.get("temp")
                and not c.get("removeTemp")], "SetCVar must never undo a temp value"
    assert h.effective_cvar("Sound_MusicVolume") == "0.4" and h.cvar("Sound_MusicVolume") == "0.4"
    assert h.account_db().get("savedCVars") is None


def test_temp_volume_duck_never_corrupts_the_players_music_volume():
    """ND-AUDIO-TEMPCVAR: two volume-ducked drops, the player moves the slider, logout: the
    effective and the stored volume are the player's value (the temp layer is gone)."""
    h = make(settings={"duckMethod": "volume", "cooldownMin": 0}, before_load=lambda hh: hh.shim.EnableTempCVar())
    fire_elwynn(h)
    h.advance(30)                                   # drop 1 over
    h.slash("replay")                               # drop 2 (test play), volume ducked again
    assert h.effective_cvar("Sound_MusicVolume") == "0"
    assert h.account_db()["savedCVars"] == {"Sound_MusicVolume": "0.4"}  # never saved "0" as the original
    h.advance(31)
    h.lua('SetCVar("Sound_MusicVolume", "0.8")')    # the player's slider (Settings.SetupCVarSlider)
    h.logout()
    assert h.effective_cvar("Sound_MusicVolume") == "0.8" and h.cvar("Sound_MusicVolume") == "0.8"
    assert len([c for c in h.cvar_sets() if c.get("removeTemp")]) == 2


def test_logout_mid_temp_volume_duck_removes_the_temp_value():
    h = make(settings={"duckMethod": "volume"}, before_load=lambda hh: hh.shim.EnableTempCVar())
    fire_elwynn(h)
    h.logout()
    assert h.effective_cvar("Sound_MusicVolume") == "0.4"
    assert h.account_db().get("savedCVars") is None


def test_duck_none_leaves_music_alone():
    h = make(settings={"duckMethod": "none"})
    fire_elwynn(h)
    h.advance(30)
    assert h.music() == [] and h.cvar_sets() == []


def test_volume_method_switches_to_silence_for_in_game_files():
    h = make(settings={"duckMethod": "volume"}, state={"playableFiles": {LOCAL_FILE: True}})
    h.set_location(1436, "")
    h.advance(11)
    fire = h.events_of(ET.FIRE)[0]
    assert fire["flags"] & int(Flags.LOCAL_PLAYBACK)
    assert h.music() == [["play", SILENCE]]
    assert h.cvar("Sound_MusicVolume") == "0.4"


def test_crash_left_cvars_are_restored_at_login():
    db = {"savedCVars": {"Sound_MusicVolume": "0.7", "Sound_EnableMusic": "1"}}
    h = make(db=db, cvars={"Sound_MusicVolume": "0", "Sound_EnableMusic": "0"})
    assert h.cvar("Sound_MusicVolume") == "0.7"
    assert h.cvar("Sound_EnableMusic") == "1"
    assert h.account_db().get("savedCVars") is None
    assert any("restored sound settings" in line for line in h.prints())


def test_logout_mid_drop_restores_volume():
    h = make(settings={"duckMethod": "volume"})
    fire_elwynn(h)
    assert h.cvar("Sound_MusicVolume") == "0"
    h.logout()
    assert h.cvar("Sound_MusicVolume") == "0.4"
    assert h.account_db().get("savedCVars") is None


def test_logout_during_kick_restores_music_enabled():
    h = make()
    fire_elwynn(h)
    h.advance(28.45)
    assert h.cvar("Sound_EnableMusic") == "0"
    h.logout()
    assert h.cvar("Sound_EnableMusic") == "1"
    assert h.account_db().get("savedCVars") is None


def test_cancel_restores_audio():
    h = make()
    fire_elwynn(h)
    h.slash("stop")
    assert h.music()[-1] == ["stop"]
    h.advance(0.2)
    assert h.cvar("Sound_EnableMusic") == "1"


def test_replacing_a_playing_drop_keeps_music_ducked_once():
    h = make()
    fire_elwynn(h)
    h.slash("play zone:1436")
    assert h.music() == [["play", SILENCE]]
    h.slash("stop")
    assert h.music() == [["play", SILENCE], ["stop"]]


def test_selftest_music_ducks_for_eight_seconds():
    h = make()
    out = h.slash("selftest music")
    assert h.music() == [["play", SILENCE]]
    assert any("8 s" in line for line in out)
    h.advance(7.5)
    assert h.music() == [["play", SILENCE]]
    h.advance(1)
    assert h.music()[-1] == ["stop"]
    assert any("come back" in line for line in h.prints()[-2:])


def test_selftest_music_refuses_while_drop_plays():
    h = make()
    fire_elwynn(h)
    out = h.slash("selftest music")
    assert any("stop it first" in line for line in out)


# ------------------------------------------------- real-client finding 6 --

def test_without_the_app_a_spotify_or_youtube_drop_shows_its_card_and_leaves_zone_music_on():
    h = make(app=None)
    fire_elwynn(h)
    assert h.toast_visible() or h.toast_card() is not None
    assert h.music() == [] and h.cvar_sets() == []
    assert any("card only" in line for line in h.slash("status"))
    h.advance(30)
    assert h.music() == [] and h.cvar_sets() == []


def test_duck_without_app_setting_restores_the_v1_behaviour():
    h = make(app=None, settings={"duckWithoutApp": True})
    fire_elwynn(h)
    assert h.music() == [["play", SILENCE]]


def test_an_outdated_app_cannot_play_v2_songs_so_nothing_is_ducked():
    h = make(app={"schema": 1, "appVersion": "1.1.0", "written": 1762365000})
    fire_elwynn(h)
    assert h.music() == []


def test_in_game_files_always_duck_even_without_the_app():
    h = make(app=None, state={"playableFiles": {LOCAL_FILE: True}})
    h.set_location(1436, "")
    h.advance(11)
    assert h.events_of(ET.FIRE)[0]["flags"] & int(Flags.LOCAL_PLAYBACK)
    assert h.music() == [["play", SILENCE]]


def test_the_songless_test_card_never_ducks():
    h = make()
    h.slash("test")
    assert h.events_of(ET.FIRE)[-1]["dropId"] == "test"
    assert h.music() == []


def test_a_handover_to_a_drop_that_must_not_duck_brings_the_zone_music_back():
    h = make(app=None, settings={"duckWithoutApp": True})
    fire_elwynn(h)
    assert h.music() == [["play", SILENCE]]
    h.set_setting("duckWithoutApp", False)
    h.slash("play zone:1429")                      # replaces the playing drop
    assert h.music()[-1] == ["stop"]


# ----------------------------------------------------------- ND-DIR-02 --

def test_a_drop_never_starts_inside_the_music_kick_window():
    """A manual play ends while an in-game-file drop is pending with the cooldown clear: the
    restore's 0.1 s Sound_EnableMusic=0 kick would mute the Music channel (willPlay nil) just
    when the file starts. FinishKick turns music back on first."""
    h = make(settings={"cooldownMin": 0}, state={"playableFiles": {LOCAL_FILE: True}})
    h.slash("play zone:1429")                      # 30 s test play, ducked with the silent track
    h.set_location(1436, "")                       # the in-game drop is pending meanwhile
    h.advance(31)
    fires = [e for e in h.events_of(ET.FIRE) if e["dropId"] == "zone:1436"]
    assert fires, "the pending drop fired after the test play ended"
    assert fires[0]["flags"] & int(Flags.LOCAL_PLAYBACK), "the in-game file must have played"
    played = [s for s in h.sounds() if s["path"] == LOCAL_FILE]
    assert played and played[-1]["willPlay"] and not played[-1].get("muted")
    assert h.cvar("Sound_EnableMusic") == "1"
    h.advance(0.5)                                  # the cancelled kick never turns music off again
    assert h.cvar("Sound_EnableMusic") == "1" and h.account_db().get("savedCVars") is None
