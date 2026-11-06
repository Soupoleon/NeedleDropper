"""Addon scenario tests: the Director state machine (SPEC 4.3, DESIGN-v1.2 4.2) driven through
the WoW shim with the whole addon loaded in TOC order.

v1.2 migration: the v1 test map (Map.lua with numbered drops and a map hash) became a built-in
test soundtrack (``make_set``: built-in drops, so exploration history applies like it did to the
v1 map) plus, for the in-game file, a soundtrack of the player's own (only those carry files).
Events are Protocol2 event tables: a drop is identified by ``dropId`` instead of v1's ``n`` /
``hash``. The NeedleDropper app is "known" (NeedleDropper_CompanionData) so that drops fade the
zone music as before (real-client finding 6: without the app they leave it alone).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import MEDIA_PATH, AddonHarness, make_set, user_db  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

LOCAL_FILE = MEDIA_PATH + "sentinel.mp3"

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
     "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 60000},
    {"id": "area:87", "kind": "subzone", "mapID": 1429, "areaID": 87, "subzone": "Goldshire",
     "label": "Goldshire", "title": "Inn Song", "artist": "Bard", "durationMs": 30000},
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall",
     "title": "Dust", "artist": "Farmer", "durationMs": 30000},
    {"id": "inst:36", "kind": "instance", "instanceID": 36, "label": "The Deadmines",
     "title": "Below", "artist": "Miners", "durationMs": 30000},
]
# In-game files are a soundtrack-of-your-own feature (built-in drops never carry one).
LOCAL_DROPS = [
    {"id": "sub:1436:sentinel-hill", "kind": "subzone", "mapID": 1436, "subzone": "Sentinel Hill",
     "label": "Sentinel Hill", "title": "Watch", "artist": "Guard", "durationMs": 0, "file": "sentinel.mp3"},
]
SET = make_set(DROPS)
# 1.2.1: an app older than 1.2.1 counts as outdated (it reads no 1.2.1 frame, DESIGN-v1.2.1 2.11)
APP = {"schema": 1, "appVersion": "1.2.1", "written": 1762365000,
       "status": {"spotify": "connected", "youtube": True, "transports": {"pixel": True}}}


def make(**kw) -> AddonHarness:
    """The test soundtrack (built-in) under a soundtrack of your own with the in-game file drop."""
    db = kw.pop("db", None)
    settings = kw.pop("settings", None)
    base = user_db(LOCAL_DROPS, active=["user:test", "builtin:test-set"])
    if db:
        base.update(db)
    kw.setdefault("companion_data", APP)
    return AddonHarness(sets=[SET], db=base, settings=settings, **kw)


def names(evs):
    return [ET(e["type"]).name for e in evs]


def has(flags: int, f: Flags) -> bool:
    return bool(flags & int(f))


def ids(evs):
    return [e.get("dropId") for e in evs]


@pytest.fixture
def h():
    return make()


# --------------------------------------------------------------- arrival --

def test_login_emits_one_hello_without_drop_fields(h):
    evs = h.events()
    assert names(evs) == ["HELLO"]
    assert evs[0].get("dropId") is None and evs[0].get("placeKey") is None and evs[0]["flags"] == 0


def test_first_arrival_arms_then_fires_after_dwell_and_marks_done(h):
    m = h.mark()
    h.set_location(1429, "Northshire Valley")
    evs = h.events_since(m)
    assert names(evs) == ["ARM"]
    assert evs[0]["dropId"] == "zone:1429" and evs[0]["artist"] == "Low Roar"
    h.advance(7)                       # 7.6 s after arrival: dwell (10 s) not met yet
    assert not h.events_of(ET.FIRE)
    assert "zone:1429" not in h.char_db()["done"]
    h.advance(3.5)                     # 11.1 s
    fires = h.events_of(ET.FIRE)
    assert len(fires) == 1
    f = fires[0]
    assert f["dropId"] == "zone:1429" and (f["title"], f["lengthMs"]) == ("I'll Keep Coming", 60000)
    assert has(f["flags"], Flags.DROP_ACTIVE) and not has(f["flags"], Flags.TEST)
    cdb = h.char_db()
    assert cdb["done"]["zone:1429"]["how"] == "played"
    j = cdb["journal"][-1]
    assert j["id"] == "zone:1429"
    # DESIGN-v1.2 4.6: the journal keeps the song itself, so a replay works after edits
    assert (j["artist"], j["title"], j["durationMs"], j["setId"]) == ("Low Roar", "I'll Keep Coming", 60000, "test-set")
    assert cdb["lastFireAt"] > 0
    assert h.account_db()["accountDone"]["zone:1429"]["how"] == "played"


def test_arm_emitted_once_per_entry_while_waiting(h):
    h.set_location(1429, "Northshire Valley")
    h.combat(True)
    h.advance(30)
    h.set_location(1429, "Northshire Valley")   # same place re-read (event flapping)
    h.advance(5)
    assert len(h.events_of(ET.ARM)) == 1
    assert not h.events_of(ET.FIRE)


def test_drop_ends_after_duration_then_hello(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    m = h.mark()
    h.advance(58)
    assert not h.events_since(m)
    h.advance(3)
    evs = h.events_since(m)
    assert names(evs) == ["HELLO"]
    assert not has(evs[0]["flags"], Flags.DROP_ACTIVE)


def test_leaving_zone_does_not_stop_active_drop(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    m = h.mark()
    h.set_location(1411, "Razor Hill")
    h.advance(5)
    assert not h.events_of(ET.CANCEL)
    assert all(e["type"] != ET.CANCEL for e in h.events_since(m))


# ----------------------------------------------------------------- gates --

def test_combat_holds_then_fires_on_regen_enabled(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(2)
    h.combat(True)
    h.advance(20)
    assert not h.events_of(ET.FIRE)
    m = h.mark()
    h.combat(False)                      # the nudge fires without waiting for the ticker
    evs = h.events_since(m)
    assert names(evs) == ["FIRE"]
    assert evs[0]["dropId"] == "zone:1429"


def test_combat_flag_set_on_armed_event():
    h = make()
    h.combat(True)
    h.set_location(1429, "Northshire Valley")
    arm = h.events_of(ET.ARM)[0]
    assert has(arm["flags"], Flags.IN_COMBAT)


def test_taxi_holds_unless_disabled():
    h = make(state={"taxi": True})
    h.set_location(1429, "Northshire Valley")
    h.advance(20)
    assert not h.events_of(ET.FIRE)
    assert has(h.events_of(ET.ARM)[0]["flags"], Flags.ON_TAXI)
    h2 = make(state={"taxi": True}, settings={"suppressOnTaxi": False})
    h2.set_location(1429, "Northshire Valley")
    h2.advance(12)
    assert len(h2.events_of(ET.FIRE)) == 1


def test_dead_holds_until_alive():
    h = make(state={"dead": True})
    h.set_location(1429, "Northshire Valley")
    h.advance(20)
    assert not h.events_of(ET.FIRE)
    h.state.dead = False
    h.fire("PLAYER_ALIVE")
    assert len(h.events_of(ET.FIRE)) == 1


def test_cinematic_holds_new_character_until_cinematic_stop():
    h = make(state={"cinematic": True, "level": 1}, location=(1429, "Northshire Valley"))
    h.fire("CINEMATIC_START")
    h.advance(30)
    assert len(h.events_of(ET.ARM)) == 1
    assert not h.events_of(ET.FIRE)
    h.state.cinematic = False
    h.fire("CINEMATIC_STOP")
    assert len(h.events_of(ET.FIRE)) == 1


def test_movie_frame_holds():
    h = make()
    h.lua("MovieFrame:Show()")
    h.fire("PLAY_MOVIE", 1)
    h.set_location(1429, "Northshire Valley")
    h.advance(20)
    assert not h.events_of(ET.FIRE)
    h.lua("MovieFrame:Hide()")
    h.fire("STOP_MOVIE")
    assert len(h.events_of(ET.FIRE)) == 1


def test_encounter_restriction_holds():
    h = make(state={"encounterRestricted": True})
    h.set_location(1429, "Northshire Valley")
    h.advance(20)
    assert not h.events_of(ET.FIRE)
    h.state.encounterRestricted = False
    h.advance(1.1)
    assert len(h.events_of(ET.FIRE)) == 1


def test_disabled_addon_never_arms():
    h = make(settings={"enabled": False})
    h.set_location(1429, "Northshire Valley")
    h.advance(20)
    assert not h.events_of(ET.ARM) and not h.events_of(ET.FIRE)


# ---------------------------------------------------------------- cancel --

def test_leaving_before_dwell_cancels_and_does_not_mark_done(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(5)
    m = h.mark()
    h.set_location(1411, "Razor Hill")
    evs = h.events_since(m)
    assert names(evs) == ["CANCEL", "HELLO"]
    assert evs[0]["dropId"] == "zone:1429"
    h.advance(20)
    assert not h.events_of(ET.FIRE)
    assert "zone:1429" not in h.char_db()["done"]
    # a new entry arms again and gets the full dwell
    m = h.mark()
    h.set_location(1429, "Northshire Valley")
    assert names(h.events_since(m)) == ["ARM"]
    h.advance(11)
    assert len(h.events_of(ET.FIRE)) == 1


def test_combat_longer_than_fade_cancels_active_drop(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert h.events_of(ET.FIRE)
    m = h.mark()
    h.combat(True)
    h.advance(3)
    assert not h.events_since(m)
    h.advance(3.5)
    evs = h.events_since(m)
    assert names(evs) == ["CANCEL"]
    assert evs[0]["dropId"] == "zone:1429"
    assert has(evs[0]["flags"], Flags.IN_COMBAT) and not has(evs[0]["flags"], Flags.DROP_ACTIVE)
    assert h.char_db()["done"]["zone:1429"]["how"] == "played"   # cancel keeps done
    assert h.music()[-1] == ["stop"]
    h.combat(False)
    h.advance(5)
    assert len(h.events_of(ET.FIRE)) == 1


def test_short_combat_does_not_cancel(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h.combat(True)
    h.advance(3)
    h.combat(False)
    h.advance(10)
    assert not h.events_of(ET.CANCEL)


def test_combat_fade_zero_never_cancels():
    h = make(settings={"combatFadeSec": 0})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h.combat(True)
    h.advance(40)
    assert not h.events_of(ET.CANCEL)


def test_death_cancels_active_drop(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h.state.dead = True
    m = h.mark()
    h.fire("PLAYER_DEAD")
    evs = h.events_since(m)
    assert names(evs) == ["CANCEL"] and has(evs[0]["flags"], Flags.DEAD)


def test_entering_instance_cancels_active_zone_drop(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    m = h.mark()
    h.set_location(9100, "", instance={"id": 99, "name": "Somewhere Else"})
    evs = h.events_since(m)
    assert names(evs)[0] == "CANCEL" and evs[0]["dropId"] == "zone:1429"
    assert has(evs[0]["flags"], Flags.IN_INSTANCE)


def test_stop_lets_armed_drop_go_for_this_visit(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(3)
    m = h.mark()
    h.slash("stop")
    assert names(h.events_since(m)) == ["CANCEL"]
    h.advance(20)
    assert not h.events_of(ET.FIRE)
    assert "zone:1429" not in h.char_db()["done"]
    h.set_location(1411, "")
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert len(h.events_of(ET.FIRE)) == 1


def test_skip_armed_drop_marks_it_skipped(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(3)
    h.slash("skip")
    assert h.char_db()["done"]["zone:1429"]["how"] == "skipped"
    h.advance(20)
    assert not h.events_of(ET.FIRE)


def test_skip_and_stop_active_drop_keep_done(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    m = h.mark()
    h.slash("skip")
    assert names(h.events_since(m)) == ["CANCEL"]
    assert h.char_db()["done"]["zone:1429"]["how"] == "played"
    out = h.slash("stop")
    assert any("nothing is playing" in line for line in out)


# ------------------------------------------------------------- discovery --

def test_discovery_message_shortens_dwell_to_min_dwell(h):
    h.set_location(1429, "Northshire Valley", settle=False)
    h.discover("Northshire Valley")
    h.advance(0.6)
    assert names(h.events()[1:]) == ["ARM"]
    h.advance(3.0)                       # 3.6 s after arrival, far below dwellSec=10
    fires = h.events_of(ET.FIRE)
    assert len(fires) == 1 and fires[0]["dropId"] == "zone:1429"


def test_discovery_via_chat_backup_only(h):
    h.set_location(1429, "Northshire Valley", settle=False)
    h.discover("Northshire Valley", xp=45, info=False, exploration=False)
    assert h.ns.Discovery.last.name == "Northshire Valley"
    assert h.ns.Discovery.last.source == "chat"
    h.advance(4)
    assert len(h.events_of(ET.FIRE)) == 1


def test_discovery_info_message_parses_name(h):
    h.discover("Goldshire", xp=30, chat=False)
    last = h.ns.Discovery.last
    assert last.name == "Goldshire" and last.source == "info"


def test_unrelated_info_message_is_not_a_discovery(h):
    h.fire("UI_INFO_MESSAGE", 50, "Quest accepted: Discovered: Fake")
    h.fire("CHAT_MSG_SYSTEM", "You have learned a new spell.")
    assert h.ns.Discovery.last is None


def test_landmark_beats_zone(h):
    h.set_location(1429, "Goldshire")
    h.advance(1.5)                       # respect-history check for the landmark
    arms = h.events_of(ET.ARM)
    assert ids(arms) == ["area:87"]
    h.advance(3)                          # landmarkDwellSec = 4
    fires = h.events_of(ET.FIRE)
    assert ids(fires) == ["area:87"]
    assert "zone:1429" not in h.char_db()["done"]
    # the zone drop is pending next, held by the cooldown
    assert h.events()[-1]["type"] == ET.ARM and h.events()[-1]["dropId"] == "zone:1429"
    h.advance(60)
    assert len(h.events_of(ET.FIRE)) == 1


def test_landmark_discovery_fires_without_landmark_dwell(h):
    h.set_location(1429, "Goldshire", settle=False)
    h.discover("Goldshire")
    h.advance(2.0)
    fires = h.events_of(ET.FIRE)
    assert ids(fires) == ["area:87"]


def test_landmark_by_subzone_name_and_area_name():
    drops = [{"id": "area:88", "kind": "subzone", "areaID": 88, "label": "Lion's Pride Inn"}]
    h = AddonHarness(sets=[make_set(drops)], settings={"historyMode": "fresh"})
    h.set_location(1429, "Lion's Pride Inn")      # no mapID in the drop: matched by area name
    assert ids(h.events_of(ET.ARM)) == ["area:88"]


def test_micro_map_normalizes_to_zone(h):
    h.set_location(9002, "Fargodeep Mine")          # micro -> micro -> Elwynn Forest
    assert h.ns.Location.state.zoneMapID == 1429
    assert ids(h.events_of(ET.ARM)) == ["zone:1429"]


def test_continent_map_has_no_zone_key(h):
    h.set_location(1415, "")
    assert h.ns.Location.state.zoneMapID is None
    assert not h.events_of(ET.ARM)


# -------------------------------------------------------------- cooldown --

def test_cooldown_blocks_second_drop_then_allows_it_while_still_there():
    h = make(settings={"cooldownMin": 2})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    first = h.events_of(ET.FIRE)
    assert len(first) == 1
    h.advance(61)                          # first song (60 s) over
    assert h.events()[-1]["type"] == ET.HELLO
    h.set_location(1429, "Goldshire")
    h.advance(10)
    arms = [a for a in h.events_of(ET.ARM) if a["dropId"] == "area:87"]
    assert len(arms) == 1
    assert len(h.events_of(ET.FIRE)) == 1
    h.advance(40)                          # ~115 s after the first FIRE: still cooling down
    assert len(h.events_of(ET.FIRE)) == 1
    lines = h.slash("status")
    assert any("cooldown" in line and "left" in line for line in lines)
    h.advance(20)                          # > 120 s
    fires = h.events_of(ET.FIRE)
    assert len(fires) == 2 and fires[1]["dropId"] == "area:87"


def test_cooldown_persists_across_reload():
    h = make()
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h2 = make(db=h.account_db(), char_db=h.char_db(), location=(1436, ""))
    h2.advance(30)
    assert len(h2.events_of(ET.ARM)) == 1 and not h2.events_of(ET.FIRE)


def test_one_active_drop_at_a_time():
    h = make(settings={"cooldownMin": 0})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h.set_location(1436, "")
    h.advance(20)                          # zone:1429 still playing (60 s)
    assert len(h.events_of(ET.FIRE)) == 1
    h.advance(42)                          # first drop ended at +60 s; the pending one follows
    fires = h.events_of(ET.FIRE)
    assert len(fires) == 2 and fires[1]["dropId"] == "zone:1436"


# --------------------------------------------------------------- history --

def test_respect_history_marks_explored_zone_done():
    h = make(state={"exploredTextures": {1429: 3}})
    cdb = h.char_db()
    assert cdb["done"]["zone:1429"]["how"] == "history"
    assert cdb["scanned"]["zone:1429"] is True and cdb["scanned"]["zone:1436"] is True
    assert "zone:1436" not in cdb["done"]
    h.set_location(1429, "Northshire Valley")
    h.advance(15)
    assert not h.events_of(ET.ARM) and not h.events_of(ET.FIRE)


def test_history_scan_happens_once_per_drop():
    h = make()                       # nothing explored yet: all scanned as new
    assert h.char_db()["scanned"]["zone:1429"] is True
    # later the character explores Elwynn (drop pending) and logs out; on the next login
    # the scan must not turn the pending drop into "history".
    h2 = make(db=h.account_db(), char_db=h.char_db(), state={"exploredTextures": {1429: 2}})
    assert "zone:1429" not in h2.char_db()["done"]


def test_zone_does_not_arm_before_history_scan():
    h = make(state={"exploredTextures": {1429: 1}}, location=(1429, "Northshire Valley"),
                     settle=0)
    h.advance(2)
    assert not h.events_of(ET.ARM)
    h.advance(20)
    assert not h.events_of(ET.ARM) and not h.events_of(ET.CANCEL)


def test_unexplored_zone_at_login_arms_after_scan():
    h = make(location=(1429, "Northshire Valley"), settle=0)
    h.advance(2)
    assert not h.events_of(ET.ARM)
    h.advance(2)
    assert len(h.events_of(ET.ARM)) == 1
    h.advance(8)
    assert len(h.events_of(ET.FIRE)) == 1


def test_respect_history_marks_explored_landmark_done():
    h = make(state={"exploredAreas": {1429: [12, 87]}})
    h.set_location(1429, "Goldshire")
    assert not h.events_of(ET.ARM)            # checking history first
    h.advance(1.5)
    assert h.char_db()["done"]["area:87"]["how"] == "history"
    assert ids(h.events_of(ET.ARM)) == ["zone:1429"]


def test_fresh_discovery_of_landmark_is_not_history():
    h = make(state={"exploredAreas": {1429: [87]}})
    h.set_location(1429, "Goldshire", settle=False)
    h.discover("Goldshire")                   # just explored -> it shows up as explored
    h.advance(2)
    assert "history" not in str(h.char_db()["done"].get("area:87"))
    assert ids(h.events_of(ET.FIRE)) == ["area:87"]


def test_fresh_mode_ignores_history():
    h = make(settings={"historyMode": "fresh"},
                     state={"exploredTextures": {1429: 5}, "exploredAreas": {1429: [87]}})
    assert h.char_db()["done"] == {}
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert ids(h.events_of(ET.FIRE)) == ["zone:1429"]


def test_new_level_one_character_ignores_history():
    h = make(state={"level": 1, "exploredTextures": {1429: 1}})
    assert "zone:1429" not in h.char_db()["done"]
    assert h.char_db()["scanned"]["zone:1429"] is True


def test_switching_to_respect_rescans():
    h = make(settings={"historyMode": "fresh"}, state={"exploredTextures": {1436: 2}})
    assert "zone:1436" not in h.char_db()["done"]
    h.slash("set historyMode respect")
    h.advance(1.1)
    assert h.char_db()["done"]["zone:1436"]["how"] == "history"


# ----------------------------------------------------------------- scope --

def test_scope_account_uses_account_done():
    db = {"settings": {"scope": "account"}, "accountDone": {"zone:1429": {"t": 5, "how": "played"}}}
    h = make(db=db)
    h.set_location(1429, "Northshire Valley")
    h.advance(15)
    assert not h.events_of(ET.ARM)


def test_scope_account_ignores_character_done_and_records_account():
    char = {"done": {"zone:1429": {"t": 5, "how": "played"}}}
    h = make(settings={"scope": "account"}, char_db=char)
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert ids(h.events_of(ET.FIRE)) == ["zone:1429"]
    assert h.account_db()["accountDone"]["zone:1429"]["how"] == "played"


def test_scope_character_ignores_account_done():
    db = {"accountDone": {"zone:1429": {"t": 5, "how": "played"}}}
    h = make(db=db)
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert ids(h.events_of(ET.FIRE)) == ["zone:1429"]


# --------------------------------------------------------- manual plays --

def test_replay_play_and_test_do_not_change_done_and_set_test_flag(h):
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h.advance(61)
    before = h.char_db()
    acct_before = h.account_db()["accountDone"]

    m = h.mark()
    h.slash("replay")
    evs = h.events_since(m)
    assert names(evs) == ["FIRE"]
    assert evs[0]["dropId"] == "zone:1429" and has(evs[0]["flags"], Flags.TEST)
    h.slash("stop")
    assert has(h.last_event()["flags"], Flags.TEST) and h.last_event()["type"] == ET.CANCEL

    m = h.mark()
    h.slash("play zone:1436")
    evs = h.events_since(m)
    assert names(evs) == ["FIRE"] and evs[0]["dropId"] == "zone:1436" and has(evs[0]["flags"], Flags.TEST)

    m = h.mark()
    h.slash("play westfall")            # by place name, replaces the playing one
    evs = h.events_since(m)
    assert names(evs) == ["FIRE"] and evs[0]["dropId"] == "zone:1436"

    m = h.mark()
    h.slash("test")
    evs = h.events_since(m)
    assert names(evs) == ["FIRE"]
    assert evs[0]["dropId"] == "test" and evs[0].get("artist") is None and has(evs[0]["flags"], Flags.TEST)
    assert h.toast_texts() == ["N E E D L E D R O P P E R", "Test drop"]  # SPEC-v1.1 B test card

    after = h.char_db()
    assert after["done"] == before["done"]
    assert after["journal"] == before["journal"]
    assert after["lastFireAt"] == before["lastFireAt"]
    assert h.account_db()["accountDone"] == acct_before


def test_manual_play_ends_with_hello(h):
    h.slash("play zone:1436")
    h.advance(31)
    assert h.last_event()["type"] == ET.HELLO
    assert not has(h.last_event()["flags"], Flags.DROP_ACTIVE)


def test_replay_given_id_and_unknown(h):
    m = h.mark()
    h.slash("replay area:87")
    assert h.events_since(m)[0]["dropId"] == "area:87"
    out = h.slash("play nowhere-at-all")
    assert any("no drop matches" in line for line in out)
    h2 = make()
    out = h2.slash("replay")
    assert any("nothing to replay" in line for line in out)


def test_manual_play_does_not_start_cooldown(h):
    h.slash("play zone:1436")
    h.slash("stop")
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert [f["dropId"] for f in h.events_of(ET.FIRE) if not f["flags"] & int(Flags.TEST)] == ["zone:1429"]


# -------------------------------------------------------- local playback --

def test_local_file_drop_sets_local_playback_and_calls_play_sound_file():
    h = make(state={"playableFiles": {LOCAL_FILE: True}, "soundLengths": {LOCAL_FILE: 20}})
    h.set_location(1436, "Sentinel Hill")
    h.advance(5)
    fires = h.events_of(ET.FIRE)
    assert ids(fires) == ["sub:1436:sentinel-hill"]
    assert has(fires[0]["flags"], Flags.LOCAL_PLAYBACK) and has(fires[0]["flags"], Flags.DROP_ACTIVE)
    sounds = h.sounds()
    assert sounds[0]["path"] == LOCAL_FILE and sounds[0]["channel"] == "Music" and sounds[0]["willPlay"]
    m = h.mark()
    h.advance(22)                          # the file stops after 20 s (C_Sound.IsPlaying -> false)
    evs = h.events_since(m)
    assert names(evs)[0] == "HELLO"
    assert evs[0]["t"] < fires[0]["t"] + 23


def test_will_play_nil_falls_back_to_companion():
    h = make(settings={"maxDropSec": 40})
    h.set_location(1436, "Sentinel Hill")
    h.advance(5)
    fire = h.events_of(ET.FIRE)[0]
    assert not has(fire["flags"], Flags.LOCAL_PLAYBACK)
    assert h.sounds()[0]["willPlay"] is False
    m = h.mark()
    h.advance(30)
    assert not h.events_since(m)          # runs for maxDropSec (duration unknown)
    h.advance(12)
    assert h.events_since(m)[0]["type"] == ET.HELLO


def test_local_playback_disabled_leaves_it_to_companion():
    h = make(settings={"localPlayback": False}, state={"playableFiles": {LOCAL_FILE: True}})
    h.set_location(1436, "Sentinel Hill")
    h.advance(5)
    assert not has(h.events_of(ET.FIRE)[0]["flags"], Flags.LOCAL_PLAYBACK)
    assert h.sounds() == []


def test_cancel_local_drop_stops_sound_with_fade():
    h = make(state={"playableFiles": {LOCAL_FILE: True}})
    h.set_location(1436, "Sentinel Hill")
    h.advance(5)
    h.slash("stop")
    stopped = h.stopped_sounds()
    assert stopped and stopped[0]["handle"] == h.sounds()[0]["handle"] and stopped[0]["fade"] == 3000
    assert has(h.last_event()["flags"], Flags.LOCAL_PLAYBACK) and h.last_event()["type"] == ET.CANCEL


# -------------------------------------------------------------- instance --

def test_instance_drop_fires_after_min_dwell_with_instance_flag(h):
    h.set_location(291, "", instance={"id": 36, "name": "The Deadmines"})
    arms = h.events_of(ET.ARM)
    assert ids(arms) == ["inst:36"]
    assert has(arms[0]["flags"], Flags.IN_INSTANCE)
    h.advance(3)
    fires = h.events_of(ET.FIRE)
    assert ids(fires) == ["inst:36"] and has(fires[0]["flags"], Flags.IN_INSTANCE)


def test_instance_drops_setting_off():
    h = make(settings={"instanceDrops": False})
    h.set_location(291, "", instance={"id": 36, "name": "The Deadmines"})
    h.advance(10)
    assert not h.events_of(ET.ARM)


def test_zone_drop_not_armed_inside_instance(h):
    h.set_location(291, "", instance={"id": 77, "name": "Unmapped"})   # dungeon map under Westfall
    h.advance(20)
    assert not h.events_of(ET.ARM)


# ------------------------------------------------------- login / reload --

def test_map_api_nil_at_login_is_retried():
    h = make(state={"mapNilCalls": 3}, location=(1429, "Northshire Valley"), settle=0)
    h.advance(6)
    assert h.ns.Location.state.zoneMapID == 1429
    assert len(h.events_of(ET.ARM)) == 1
    h.advance(8)
    assert len(h.events_of(ET.FIRE)) == 1


def test_reload_keeps_done_and_continues_seq():
    h = make()
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    last_seq = h.last_event()["seq"]
    h2 = make(db=h.account_db(), char_db=h.char_db(), location=(1429, "Northshire Valley"))
    h2.advance(15)
    assert h2.events()[0]["type"] == ET.HELLO and h2.events()[0]["seq"] == (last_seq + 1) % 256
    assert not h2.events_of(ET.ARM)


def test_reset_char_allows_drop_again():
    h = make(settings={"cooldownMin": 0})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h.advance(61)
    assert len(h.char_db()["journal"]) == 1
    h.slash("reset char")                  # still standing in Elwynn: it plays again right away
    fires = h.events_of(ET.FIRE)
    assert len(fires) == 2 and fires[1]["dropId"] == "zone:1429"
    assert len(h.char_db()["journal"]) == 1


def test_reset_account_and_all():
    db = {"accountDone": {"zone:1436": {"t": 5, "how": "played"}}}
    char = {"done": {"zone:1429": {"t": 5, "how": "played"}}, "journal": [{"id": "zone:1429", "t": 5}]}
    h = make(db=db, char_db=char)
    h.slash("reset account")
    assert h.account_db()["accountDone"] == {} and "zone:1429" in h.char_db()["done"]
    h.slash("reset all")
    assert h.char_db()["done"] == {} and not h.char_db()["journal"]
    out = h.slash("reset bogus")
    assert any("usage" in line for line in out)
