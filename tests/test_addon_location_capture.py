"""Execute real Lua capture, editor and Director paths for landmark re-entry.

The Zoram Strand names reproduce the reported place; area 77001 is a synthetic
test ID, not a claimed game database ID. No saved variables or live client used.
"""
import pytest

from addon_harness import user_db
from needledropper.models import EventType as ET
from v12_ui_harness import UIHarness


ZONE = "Ashenvale"
LANDMARK = "The Zoram Strand"
AREA = 77001
APP = {"schema": 1, "appVersion": "1.2.1", "written": 1762365000,
       "status": {"spotify": "connected", "youtube": True}}


def strand(*, subzone="", zone_text=LANDMARK, zone_name=ZONE, landmark=LANDMARK,
           explored=True, **kw):
    def before(h):
        h.shim.maps[1440] = h.rt.table_from(
            {"mapID": 1440, "name": zone_name, "mapType": 3, "parentMapID": 1414})
        h.shim.areas[AREA] = landmark
        h.lua("GetRealZoneText = function() return SHIM.maps[1440].name end")

    state = {"zoneTextOverride": zone_text, "exploredAreas": {1440: [AREA]} if explored else {}}
    return UIHarness(real=True, sets=[], location=(1440, subzone), state=state,
                     before_load=before, settings={"historyMode": "fresh", "cooldownMin": 0},
                     companion_data=APP, **kw)


@pytest.mark.parametrize("subzone", ["", LANDMARK, ZONE])
def test_zone_text_landmark_uses_canonical_zone_for_all_capture_paths(subzone):
    h = strand(subzone=subzone)
    snap = h.call1("Location.Snapshot")
    assert snap["zoneName"] == ZONE
    assert snap["subzone"] == LANDMARK
    place = h.call1("Soundtracks.PlaceHere")
    ui_place = h.call1("UI._W.Drops.CurrentPlace")
    for p in (place, ui_place):
        assert (p["kind"], p["mapID"], p["areaID"], p["subzone"]) == (
            "subzone", 1440, AREA, LANDMARK)


@pytest.mark.parametrize("zone_name, landmark", [
    ("Ashenvale", "The Zoram Strand"),
    ("Orneval", "La grève de Zoram"),
    ("灰谷", "佐拉姆海岸"),
])
def test_localized_zone_text_capture_keeps_area_identity(zone_name, landmark):
    h = strand(zone_name=zone_name, landmark=landmark, zone_text=landmark)
    assert h.call1("Soundtracks.PlaceHere")["id"] == f"area:{AREA}"
    assert h.call1("UI._W.Drops.CurrentPlace")["areaID"] == AREA


def test_unknown_area_is_saved_by_name_and_matches_on_reentry():
    h = strand(explored=False)
    place = h.call1("Soundtracks.PlaceHere")
    assert place["id"] == "sub:1440:the-zoram-strand" and place["byName"]
    ref, drop_id, _ = h.call("Soundtracks.AddHere", None, {"artist": "Band", "title": "Beach"})
    h.advance(10)
    assert not h.events_of(ET.FIRE)
    leave_and_return(h)
    h.advance(5)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == [drop_id]


def leave_and_return(h):
    h.state.zoneTextOverride = ZONE
    h.set_location(1440, "")
    assert not h.ns.Location.state.subzone
    h.state.zoneTextOverride = LANDMARK
    h.set_location(1440, "")


def test_use_my_location_releases_edit_suppression_on_landmark_reentry():
    old = {"id": "zone:1436", "kind": "zone", "mapID": 1436,
           "artist": "Band", "title": "Beach", "durationMs": 1000}
    h = strand(db=user_db([old]))
    h.open()
    h.call("UI._W.Drops.Select", "user:test", "zone:1436")
    h.click(h.form().UseLocation)
    assert h.selected() == ("user:test", f"area:{AREA}")
    h.advance(12)
    assert not h.events_of(ET.FIRE)  # editing here stays quiet
    entered = h.ns.Location.state.subEnteredAt
    leave_and_return(h)
    assert h.ns.Location.state.zoneMapID == 1440
    assert h.ns.Location.state.subEnteredAt > entered
    h.advance(5)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == [f"area:{AREA}"]
    assert h.char_db()["done"][f"area:{AREA}"]["how"] == "played"
    leave_and_return(h)
    h.advance(8)
    assert len(h.events_of(ET.FIRE)) == 1  # Fresh still means once per character


@pytest.mark.parametrize("edit", ["add", "move"])
def test_capture_during_zone_event_debounce_stays_quiet_until_next_visit(edit):
    old = {"id": "zone:1436", "kind": "zone", "mapID": 1436,
           "artist": "Band", "title": "Beach"}
    h = strand(zone_text=ZONE, db=user_db([old]))
    h.state.zoneTextOverride = LANDMARK
    h.set_location(1440, "", settle=False)
    assert h.ns.Location.state.subzone == ""  # the reader has not caught up
    if edit == "move":
        h.open()
        h.call("UI._W.Drops.Select", "user:test", "zone:1436")
        h.click(h.form().UseLocation)
    else:
        h.call("Soundtracks.AddHere", None, {"artist": "Band", "title": "Beach"})
    h.advance(10)
    assert not h.events_of(ET.FIRE)
    assert h.ns.Director.suppress[f"area:{AREA}"] == h.ns.Location.state.subEnteredAt
    assert any("held for this visit" in line for line in h.call1("Location.Describe"))
    leave_and_return(h)
    h.advance(5)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == [f"area:{AREA}"]


@pytest.mark.parametrize("elsewhere", [ZONE, "Astranaar"])
def test_leave_captured_place_before_debounced_read_does_not_hold_a_future_visit(elsewhere):
    h = strand(zone_text=ZONE)
    h.state.zoneTextOverride = LANDMARK
    h.set_location(1440, "", settle=False)
    h.call("Soundtracks.AddHere", None, {"artist": "Band", "title": "Beach"})
    h.state.zoneTextOverride = elsewhere
    h.set_location(1440, "")
    h.advance(10)
    assert not h.events_of(ET.FIRE)
    h.state.zoneTextOverride = LANDMARK
    h.set_location(1440, "")
    h.advance(5)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == [f"area:{AREA}"]


def test_reentry_keeps_cooldown_and_dwell_gates():
    h = strand()
    _, drop_id, _ = h.call("Soundtracks.AddHere", None, {"artist": "Band", "title": "Beach"})
    h.call("SetSetting", "cooldownMin", 1)
    h.ns.cdb.lastFireAt = h.g.time()
    leave_and_return(h)
    assert h.ns.Director.armed.drop.id == drop_id
    h.advance(6)
    assert not h.events_of(ET.FIRE)
    h.advance(55)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == [drop_id]


def test_whole_zone_text_does_not_invent_a_landmark():
    h = strand(zone_text=ZONE)
    assert h.call1("Soundtracks.PlaceHere")["id"] == "zone:1440"
    assert h.call1("UI._W.Drops.CurrentPlace")["kind"] == "zone"
    assert not h.ns.Location.state.subzone


def test_minimap_caption_is_diagnostic_only():
    h = strand(zone_text=ZONE)
    h.lua("GetMinimapZoneText = function() return 'The Zoram Strand' end")
    assert h.call1("Soundtracks.PlaceHere")["id"] == "zone:1440"
    lines = h.call1("Location.Describe")
    assert any("minimap='The Zoram Strand'" in line for line in lines)


def test_use_my_location_resolves_known_area_without_exploration_answer():
    h = UIHarness(real=True, location=(1429, "Goldshire"))
    assert h.call1("UI._W.Drops.CurrentPlace")["areaID"] == 87


def test_use_my_location_tries_raw_map_position_for_area_identity():
    h = UIHarness(real=True, location=(9002, "Fargodeep Mine"), sets=[],
                  state={"exploredAreas": {9002: [57]}})
    h.lua("""local original = C_Map.GetPlayerMapPosition
      C_Map.GetPlayerMapPosition = function(map, unit)
        if map == 1429 then return nil end
        return original(map, unit)
      end""")
    place = h.call1("UI._W.Drops.CurrentPlace")
    assert place["mapID"] == 1429 and place["areaID"] == 57


def test_secret_or_failing_zone_text_is_never_used_as_a_landmark():
    h = strand(zone_text=ZONE)
    h.lua("""GetZoneText = function() return SHIM.MakeSecret('zone') end
      GetRealZoneText = function() error('unavailable') end
      GetSubZoneText = function() return SHIM.MakeSecret('subzone') end
      GetMinimapZoneText = function() return SHIM.MakeSecret('minimap') end""")
    assert h.call1("Soundtracks.PlaceHere")["id"] == "zone:1440"
    h.call("Location.Describe")
    assert not list(h.log.secretTouches.values())
