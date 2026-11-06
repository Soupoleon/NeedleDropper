"""Regression tests for review findings on the addon core (2026-11-05):

* journey F4: without the NeedleDropper app a first-arrival drop only shows its title card; the
  place is marked "card", not "played", and plays its song once the app is there;
* BEACON-TARGET-CLEARANCE-UNMEASURED (= protocol review F9): the clearance guard measures the
  target frame when the player targets something, not only at login / scale changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import MEDIA_PATH, AddonHarness, make_set, user_db  # noqa: E402
from needledropper.models import Flags  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402
from v12_beacon_harness import BeaconHarness  # noqa: E402

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
     "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 60000, "youtube": "dQw4w9WgXcQ"},
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall",
     "title": "Dust", "artist": "Farmer", "durationMs": 30000, "youtube": "QAo_Ycocl1E"},
]
SET = make_set(DROPS)
APP = {"schema": 1, "appVersion": "1.2.0", "written": 1762365000, "status": {"spotify": "connected", "youtube": True}}
HINT = ("NeedleDropper: only the title card showed: Spotify and YouTube songs play through the NeedleDropper app, "
        "which hasn't been seen on this PC. Places you visit before you install it play their song once it is there.")


def fires(h, drop_id=None):
    return [e for e in h.events_of(ET.FIRE) if drop_id is None or e.get("dropId") == drop_id]


# ------------------------------------------------------------ journey F4 --

def test_without_the_app_a_first_arrival_is_a_card_not_a_heard_song():
    h = AddonHarness(sets=[SET], location=(1436, ""), settings={"historyMode": "fresh"})
    h.advance(12)
    assert [e["dropId"] for e in fires(h)] == ["zone:1436"]
    assert h.char_db()["done"]["zone:1436"]["how"] == "card"
    assert h.account_db()["accountDone"]["zone:1436"]["how"] == "card"
    assert h.prints().count(HINT) == 1
    assert h.char_db()["journal"][-1]["id"] == "zone:1436"     # still in the journal (Replay works)
    # Heard for now: the card does not repeat while the app is still missing.
    assert h.ns.Director.IsDone(h.ns.Map.byId["zone:1436"])
    h.set_location(1429, "")
    h.advance(12)
    h.set_location(1436, "")
    h.advance(12)
    assert len(fires(h, "zone:1436")) == 1
    assert h.prints().count(HINT) == 1                         # said once


def test_a_card_place_plays_its_song_once_the_app_is_there():
    first = AddonHarness(sets=[SET], location=(1436, ""), settings={"historyMode": "fresh"})
    first.advance(12)
    assert first.char_db()["done"]["zone:1436"]["how"] == "card"
    # Next session: the app has written NeedleDropper_Companion.
    h = AddonHarness(sets=[SET], location=(1429, ""), db=first.account_db(), char_db=first.char_db(),
                     settings={"historyMode": "fresh", "cooldownMin": 0}, companion_data=APP)
    assert not h.ns.Director.IsDone(h.ns.Map.byId["zone:1436"])
    h.set_location(1436, "")
    h.advance(12)
    assert [e["dropId"] for e in fires(h, "zone:1436")] == ["zone:1436"]
    assert h.char_db()["done"]["zone:1436"]["how"] == "played"
    assert h.account_db()["accountDone"]["zone:1436"]["how"] == "played"
    assert h.ns.Director.IsDone(h.ns.Map.byId["zone:1436"])


def test_with_the_app_a_drop_is_played_as_before():
    h = AddonHarness(sets=[SET], location=(1436, ""), settings={"historyMode": "fresh"}, companion_data=APP)
    h.advance(12)
    assert h.char_db()["done"]["zone:1436"]["how"] == "played"
    assert HINT not in h.prints()


def test_an_in_game_file_counts_as_played_without_the_app():
    """The game plays an in-game file itself: that is a heard song, app or not."""
    db = user_db([{"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "durationMs": 30000,
                   "artist": "Farmer", "title": "Dust", "file": "dust.mp3"}], active=["user:test", "builtin:test-set"])
    h = AddonHarness(sets=[SET], db=db, location=(1436, ""), settings={"historyMode": "fresh"},
                     state={"playableFiles": {MEDIA_PATH + "dust.mp3": True}})
    h.advance(12)
    fire = fires(h, "zone:1436")
    assert fire and fire[0]["flags"] & int(Flags.LOCAL_PLAYBACK)
    assert h.char_db()["done"]["zone:1436"]["how"] == "played"
    assert HINT not in h.prints()


# ------------------------------------------------ BEACON-TARGET-CLEARANCE --

DUROTAR = {"id": "zone:1411", "kind": "zone", "mapID": 1411, "label": "Durotar", "title": "9 to 5",
           "artist": "Dolly Parton", "durationMs": 165547, "youtube": "d_vPJjqWxZM", "setId": "azeroth-mixtape"}


def test_the_target_frame_is_measured_when_something_is_targeted():
    def before(h):
        h.art("PlayerFrame", topPx=14, leftPx=20)
        h.art("TargetFrame", topPx=7, leftPx=250, shown=False)      # no target at login
    h = BeaconHarness(settings={"beaconCellPx": 3}, before_load=before)
    h.emit(ET.FIRE, DUROTAR)
    assert h.status()["gap"] == 14
    h.art("TargetFrame", topPx=7, leftPx=250, shown=True)           # the player targets something
    h.fire("PLAYER_TARGET_CHANGED")
    h.advance(0.05)
    st = h.status()
    assert st["gap"] == 7
    assert st["heightPx"] <= 7 - 2                                  # the strip clears the target art
    # Clearing the target gives the room back.
    h.art("TargetFrame", topPx=7, leftPx=250, shown=False)
    h.fire("PLAYER_TARGET_CHANGED")
    h.advance(0.05)
    assert h.status()["gap"] == 14


def test_target_changes_redraw_only_when_the_gap_changes():
    def before(h):
        h.art("PlayerFrame", topPx=14, leftPx=20)
    h = BeaconHarness(settings={"beaconCellPx": 3}, before_load=before)
    h.emit(ET.FIRE, DUROTAR)
    layouts = []
    real_layout = h.ns.Beacon.Layout

    def counting():
        layouts.append(1)
        return real_layout()
    h.ns.Beacon.Layout = counting
    for _ in range(20):                                             # tab-targeting in a fight
        h.fire("PLAYER_TARGET_CHANGED")
        h.advance(0.05)
    assert layouts == []


# ----------------------------------------------------------- journey F11 --

def test_frames_about_other_places_during_a_test_are_not_tests():
    h = AddonHarness(sets=[SET], location=(1411, ""), settings={"historyMode": "fresh", "cooldownMin": 0},
                     companion_data=APP)
    h.advance(2)
    draft = {"id": "sub:1436:sentinel-hill", "kind": "subzone", "mapID": 1436, "subzone": "Sentinel Hill",
             "label": "Sentinel Hill", "spotify": "4uLU6hMCjMI75M1A2tKUQC"}
    h.call("Director.PlayTest", draft)               # the window's Test of an unsaved draft
    test_fire = h.events_of(ET.FIRE)[-1]
    assert test_fire["flags"] & int(Flags.TEST) and test_fire["flags"] & int(Flags.DROP_ACTIVE)
    mark = h.mark()
    h.set_location(1436, "")                         # Westfall's own drop arms meanwhile
    h.advance(1.0)
    arms = [e for e in h.events_since(mark) if e["type"] == int(ET.ARM)]
    assert arms and arms[0]["dropId"] == "zone:1436"
    assert arms[0]["flags"] & int(Flags.DROP_ACTIVE)
    assert not arms[0]["flags"] & int(Flags.TEST)
    assert not arms[0]["flags"] & int(Flags.LOCAL_PLAYBACK)
    h.slash("stop")                                  # the Test's own CANCEL still says TEST
    cancel = [e for e in h.events_since(mark) if e["type"] == int(ET.CANCEL)
              and e.get("dropId") == "sub:1436:sentinel-hill"]
    assert cancel and cancel[-1]["flags"] & int(Flags.TEST)

