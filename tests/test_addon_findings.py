"""Regression tests for the defects found before and during the v1.2 integration:

* the owner's real-client run of 2026-11-05 (docs/review/real-client-2026-11-05.md): #1 DEAD flag
  computed during loading, #6 (in test_addon_audio.py), #7 gamepad UI;
* the independently verified baseline findings (docs/review/baseline-findings.md): ND-DIR-01
  (/reload mid-drop), ND-DIR-03 (manual play of the pending drop), ND-DIR-04 (stop fires
  another drop), ND-DIR-06 (in test_addon_toast.py), ND-DIR-07 (account scope + history),
  ND-DIR-08 (landmark check without an answer), ND-DIR-09 (subzone retry backdates arrival);
  ND-DIR-02 and ND-AUDIO-TEMPCVAR are in test_addon_audio.py;
* the v1.2 Director contract (CONTRACTS-v1.2 2.2 / 2.9, DESIGN-v1.2 4.2 / 4.4 / 4.6):
  PlayTest(drop) for drafts, SuppressHere, ND_PLAYBACK, ND_MAP_CHANGED, history for built-in
  drops only, the journal's song, Location.CurrentAreaID.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import ADDON_NAME, MEDIA_PATH, AddonHarness, make_set, user_db  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
     "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 60000, "youtube": "dQw4w9WgXcQ"},
    {"id": "area:87", "kind": "subzone", "mapID": 1429, "areaID": 87, "subzone": "Goldshire",
     "label": "Goldshire", "title": "Inn Song", "artist": "Bard", "durationMs": 30000},
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall",
     "title": "Dust", "artist": "Farmer", "durationMs": 30000},
]
SET = make_set(DROPS)
APP = {"schema": 1, "appVersion": "1.2.0", "written": 1762365000, "status": {"spotify": "connected", "youtube": True}}


def ids(evs):
    return [e.get("dropId") for e in evs]


def names(evs):
    return [ET(e["type"]).name for e in evs]


# ----------------------------------------------- real-client finding 1: DEAD --

def test_live_flags_are_not_read_while_the_loading_screen_is_up():
    """The first HELLO after login carried DEAD for a living character: the unit APIs answered
    during loading. Live flags are only read in the world (after the loading screen)."""
    h = AddonHarness(sets=[SET], login=False, state={"dead": True, "inCombat": True, "taxi": True})
    h.state.loggedIn = True
    h.fire("ADDON_LOADED", ADDON_NAME)
    h.fire("PLAYER_LOGIN")
    h.fire("PLAYER_ENTERING_WORLD", True, False)      # the HELLO goes out during the loading screen
    hello = h.events()[0]
    assert hello["type"] == ET.HELLO and hello["flags"] == 0
    h.state.dead, h.state.inCombat, h.state.taxi = False, False, False
    h.fire("LOADING_SCREEN_DISABLED")                 # in the world: the unit APIs are right now
    h.state.dead = True                               # ...and really dead
    ev = h.emit(int(ET.PING))
    assert ev["flags"] & int(Flags.DEAD)


def test_live_flags_return_even_without_a_loading_screen_event():
    h = AddonHarness(sets=[SET], login=False, state={"inCombat": True})
    h.state.loggedIn = True
    h.fire("ADDON_LOADED", ADDON_NAME)
    h.fire("PLAYER_LOGIN")
    h.fire("PLAYER_ENTERING_WORLD", False, True)      # a /reload that never reports the loading screen
    assert not h.events()[0]["flags"] & int(Flags.IN_COMBAT)
    h.advance(3.5)
    assert h.emit(int(ET.PING))["flags"] & int(Flags.IN_COMBAT)


def test_a_loading_screen_mid_session_also_hides_the_unit_flags():
    h = AddonHarness(sets=[SET])
    h.fire("LOADING_SCREEN_ENABLED")
    h.state.dead = True
    assert not h.emit(int(ET.PING))["flags"] & int(Flags.DEAD)
    h.fire("LOADING_SCREEN_DISABLED")
    assert h.emit(int(ET.PING))["flags"] & int(Flags.DEAD)


# --------------------------------------------------- ND-DIR-01: /reload --

def test_a_reload_mid_drop_keeps_the_drop_playing_and_reports_it():
    h = AddonHarness(sets=[SET], companion_data=APP, settings={"historyMode": "fresh"})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    fire = h.events_of(ET.FIRE)[0]
    assert fire["dropId"] == "zone:1429"
    assert h.char_db()["playing"]["id"] == "zone:1429"
    h.advance(20)                                     # 40 s of the 60 s song left
    h2 = h.reload(location=(1429, "Northshire Valley"), companion_data=APP)
    hello = h2.events()[0]
    assert hello["type"] == ET.HELLO and hello["flags"] & int(Flags.DROP_ACTIVE)
    assert h2.ns.Director.active.restored is True
    assert ["play", h2.lua("return ns_SILENCE") or "Interface\\AddOns\\NeedleDropper\\media\\silence.mp3"] in h2.music()
    assert h2.char_db()["done"]["zone:1429"]["how"] == "played" and len(h2.char_db()["journal"]) == 1
    mark = h2.mark()
    h2.advance(30)
    assert not h2.events_since(mark)                  # still playing (no HELLO without DROP_ACTIVE)
    h2.advance(12)
    ended = h2.events_since(mark)
    assert names(ended) == ["HELLO"] and not ended[0]["flags"] & int(Flags.DROP_ACTIVE)
    assert "playing" not in h2.char_db()


def test_a_real_login_never_restores_an_old_drop():
    h = AddonHarness(sets=[SET], settings={"historyMode": "fresh"})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h.logout()
    h2 = AddonHarness(sets=[SET], db=h.account_db(), char_db=h.char_db(), location=(1429, "Northshire Valley"))
    assert not h2.events()[0]["flags"] & int(Flags.DROP_ACTIVE) and h2.ns.Director.active is None
    assert "playing" not in h2.char_db()


def test_a_reload_after_the_song_ended_restores_nothing():
    h = AddonHarness(sets=[SET], settings={"historyMode": "fresh"})
    h.set_location(1436, "")
    h.advance(11)
    h.lua("NeedleDropperCharDB.playing.endsAtWall = time() - 5")   # the song ended during the reload
    h2 = h.reload(location=(1436, ""))
    assert h2.ns.Director.active is None and not h2.events()[0]["flags"] & int(Flags.DROP_ACTIVE)


def test_an_in_game_file_is_stopped_by_a_reload():
    file = MEDIA_PATH + "inn.mp3"
    db = user_db([{"id": "zone:1453", "kind": "zone", "mapID": 1453, "title": "Home", "artist": "Me",
                   "file": "inn.mp3", "durationMs": 60000}])
    h = AddonHarness(sets=[SET], db=db, state={"playableFiles": {file: True}}, settings={"historyMode": "fresh"})
    h.set_location(1453, "")
    h.advance(11)
    handle = h.sounds()[0]["handle"]
    assert h.char_db()["playing"]["handle"] == handle
    h2 = h.reload(location=(1453, ""), state={"playableFiles": {file: True}})
    assert h2.ns.Director.active is None
    assert [s["handle"] for s in h2.stopped_sounds()] == [handle]


# ------------------------------------------- ND-DIR-03: play the pending drop --

def pending_westfall(**settings):
    s = {"historyMode": "fresh", "cooldownMin": 10}
    s.update(settings)
    h = AddonHarness(sets=[SET], settings=s, db={"schema": 2, "active": ["builtin:test-set"]},
                     char_db={"lastFireAt": 1762365600})   # cooldown running: Westfall stays pending
    h.set_location(1436, "")
    h.advance(11)
    assert h.ns.Director.armed.drop.id == "zone:1436" and not h.events_of(ET.FIRE)
    return h


def test_playing_the_pending_drop_by_hand_does_not_play_it_twice():
    h = AddonHarness(sets=[SET], settings={"historyMode": "fresh", "dwellSec": 30})
    h.set_location(1436, "")
    h.advance(5)
    assert h.ns.Director.armed.drop.id == "zone:1436"
    h.slash("play zone:1436")
    test_fire = h.last_event()
    assert test_fire["type"] == ET.FIRE and test_fire["flags"] & int(Flags.TEST)
    mark = h.mark()
    h.advance(31)                                     # the test play ends...
    h.advance(30)                                     # ...and the first-visit drop does not follow
    assert ET.FIRE not in [e["type"] for e in h.events_since(mark)]


def test_stopping_a_manual_play_of_the_pending_drop_leaves_it_be():
    h = pending_westfall()
    h.slash("play zone:1436")
    mark = h.mark()
    out = h.slash("stop")
    assert any("stopped Westfall" in line for line in out)
    h.lua("NeedleDropperCharDB.lastFireAt = 0")      # cooldown over: still nothing this visit
    h.advance(20)
    assert ET.FIRE not in [e["type"] for e in h.events_since(mark)]


def test_skipping_a_manual_play_marks_a_place_not_heard_yet_as_skipped():
    h = pending_westfall()
    h.slash("play zone:1436")
    out = h.slash("skip")
    assert any("skipped Westfall; it will not play on this character" in line for line in out)
    assert h.char_db()["done"]["zone:1436"]["how"] == "skipped"
    h.lua("NeedleDropperCharDB.lastFireAt = 0")
    h.set_location(1429, "")
    h.set_location(1436, "")
    h.advance(20)
    assert "zone:1436" not in ids(e for e in h.events_of(ET.FIRE) if not e["flags"] & int(Flags.TEST))


def test_skipping_the_test_drop_says_so():
    h = AddonHarness(sets=[SET])
    h.slash("test")
    assert any("stopped the test drop" in line for line in h.slash("skip"))


def test_leaving_during_a_manual_play_of_the_pending_drop_does_not_cancel_the_song():
    h = pending_westfall()
    h.slash("play zone:1436")
    mark = h.mark()
    h.set_location(1429, "Northshire Valley")
    evs = h.events_since(mark)
    # no abandon-CANCEL for the drop that is playing (the companion matches CANCEL by placeKey)
    assert not [e for e in evs if e["type"] == ET.CANCEL and e.get("dropId") == "zone:1436"]
    assert h.ns.Director.active.drop.id == "zone:1436"


# ----------------------------------------- ND-DIR-04: stop means quiet here --

def test_stop_on_a_pending_landmark_does_not_fire_the_zone_drop_instead():
    h = AddonHarness(sets=[SET], settings={"historyMode": "fresh", "cooldownMin": 0, "dwellSec": 1})
    h.set_location(1429, "Goldshire")
    h.advance(2)
    assert h.ns.Director.armed.drop.id == "area:87"
    out = h.slash("stop")
    assert any("no needle drop plays here until you move on" in line for line in out)
    mark = h.mark()
    h.advance(20)
    assert not [e for e in h.events_since(mark) if e["type"] in (ET.ARM, ET.FIRE)]
    assert any("quiet here" in line for line in h.slash("status"))
    h.set_location(1429, "Northshire Valley")         # moved on: the zone drop may play again
    h.advance(3)
    assert "zone:1429" in ids(h.events_of(ET.FIRE))


def test_stop_during_the_landmark_history_check_is_quiet_too():
    h = AddonHarness(sets=[SET], settings={"cooldownMin": 0, "dwellSec": 0})
    h.set_location(1429, "Goldshire", settle=False)
    h.advance(0.6)                                    # the 1.5 s landmark check is pending
    assert h.ns.Director.lastPick == "checking"
    h.slash("stop")
    h.advance(10)
    assert not h.events_of(ET.FIRE)


# --------------------------------------- ND-DIR-07: account scope + history --

def test_history_stays_per_character_in_account_scope():
    main = AddonHarness(sets=[SET], settings={"scope": "account"}, state={"exploredTextures": {1436: 4}})
    assert main.char_db()["done"]["zone:1436"]["how"] == "history"
    assert "zone:1436" not in main.account_db()["accountDone"]
    # an alt arriving in Westfall for the first time hears it (the app is running: without it
    # only the title card shows and the place is marked "card", see the journey F4 tests)
    alt = AddonHarness(sets=[SET], db=main.account_db(), state={"level": 5}, companion_data=APP)
    alt.set_location(1436, "")
    alt.advance(11)
    assert "zone:1436" in ids(alt.events_of(ET.FIRE))
    # and a played drop is shared by the account again
    assert alt.account_db()["accountDone"]["zone:1436"]["how"] == "played"


def test_old_account_wide_history_marks_are_ignored():
    db = {"schema": 2, "settings": {"scope": "account"}, "accountDone": {"zone:1436": {"t": 5, "how": "history"}}}
    h = AddonHarness(sets=[SET], db=db)
    assert h.call1("Director.IsDone", h.map_drop("zone:1436")) is False


# ------------------------------------- ND-DIR-08: landmark check without an answer --

def test_a_landmark_check_without_a_position_is_retried():
    h = AddonHarness(sets=[SET], settings={"cooldownMin": 0}, state={"exploredAreas": {1429: [87]}, "noPosition": True})
    h.set_location(1429, "Goldshire")
    h.advance(1.5)
    assert not h.char_db()["scanned"].get("area:87")  # no answer: not checked yet
    h.state.noPosition = False                         # the position comes back: explored long ago
    h.advance(1.1)
    assert h.char_db()["done"]["area:87"]["how"] == "history"
    assert h.char_db()["scanned"]["area:87"] is True


# ----------------------------------------- ND-DIR-09: subzone retry backdating --

def test_a_zone_crossed_just_after_login_gets_its_real_arrival_time():
    h = AddonHarness(sets=[SET], login=False, location=(1436, ""), settings={"historyMode": "fresh"})
    h.login(settle=0)
    h.advance(1.2)                                     # the first subzone retry is due at ~1.5 s
    crossed = h.now
    h.set_location(1429, "", settle=False)            # crosses into Elwynn before that retry
    h.advance(1.0)
    st = h.ns.Location.state
    assert st.zoneMapID == 1429
    assert st.zoneEnteredAt >= crossed - 0.01, (st.zoneEnteredAt, crossed)


# ------------------------------------------------- the v1.2 Director contract --

def test_play_test_plays_an_unsaved_draft_with_its_song_and_never_marks_it():
    h = AddonHarness(sets=[SET], location=(1436, ""))
    mark = h.mark()
    draft = {"id": "sub:1436:moonbrook", "kind": "subzone", "mapID": 1436, "subzone": "Moonbrook",
             "artist": "Ben Howard", "title": "", "spotify": "4uLU6hMCjMI75M1A2tKUQC"}
    h.call("Director.PlayTest", draft)
    fire = h.events_since(mark)[-1]
    assert fire["type"] == ET.FIRE and fire["flags"] & int(Flags.TEST)
    assert (fire["dropId"], fire["spotify"], fire["artist"]) == ("sub:1436:moonbrook", "4uLU6hMCjMI75M1A2tKUQC", "Ben Howard")
    assert fire["label"] == "Moonbrook"
    assert not h.char_db()["done"] and not h.char_db()["journal"] and h.char_db()["lastFireAt"] == 0
    assert not any(line.startswith("NeedleDropper: playing") for line in h.prints())   # the window shows it


def test_play_test_of_an_in_game_file_plays_it_locally():
    file = MEDIA_PATH + "goldshire.mp3"
    h = AddonHarness(sets=[SET], state={"playableFiles": {file: True}})
    h.call("Director.PlayTest", {"id": "area:87", "kind": "subzone", "areaID": 87, "mapID": 1429,
                                 "artist": "Me", "title": "Inn", "file": file, "fileName": "goldshire.mp3"})
    fire = h.last_event()
    assert fire["flags"] & int(Flags.LOCAL_PLAYBACK) and fire["flags"] & int(Flags.TEST)
    assert h.sounds()[-1]["path"] == file


def test_play_test_without_a_drop_is_the_test_card():
    h = AddonHarness(sets=[SET])
    h.call("Director.PlayTest")
    fire = h.last_event()
    assert (fire["dropId"], fire.get("artist")) == ("test", None) and fire["flags"] & int(Flags.TEST)


def test_suppress_here_lets_a_drop_go_for_this_visit_only():
    h = AddonHarness(sets=[SET], settings={"historyMode": "fresh"})
    h.set_location(1436, "")
    assert h.ns.Director.armed.drop.id == "zone:1436"
    h.call("Director.SuppressHere", "zone:1436")
    assert h.ns.Director.armed is None and h.last_event()["type"] == ET.CANCEL
    h.advance(20)
    assert not h.events_of(ET.FIRE)
    h.set_location(1429, "")
    h.set_location(1436, "")
    h.advance(11)
    assert "zone:1436" in ids(h.events_of(ET.FIRE))


def test_playback_state_changes_are_announced():
    h = AddonHarness(sets=[SET], settings={"historyMode": "fresh"})
    h.record_fires()
    h.set_location(1436, "")
    h.advance(11)
    h.advance(31)
    states = [args[0] for args in h.fired("ND_PLAYBACK")]
    assert states == ["armed", "active", "ended"]


def test_map_changes_disarm_a_pending_drop_that_is_gone_or_changed():
    db = user_db([{"id": "zone:1436", "kind": "zone", "mapID": 1436, "title": "Old", "artist": "Band"}],
                 active=["user:test", "builtin:test-set"])
    h = AddonHarness(sets=[SET], db=db, settings={"historyMode": "fresh", "dwellSec": 30})
    h.set_location(1436, "")
    assert h.ns.Director.armed.drop.title == "Old"
    # an edit that keeps the song (the note) only refreshes the armed copy
    mark = h.mark()
    h.call("Soundtracks.UpdateDrop", "user:test", "zone:1436", {"note": "a note"})
    h.rebuild()
    assert h.events_since(mark) == [] and h.same(h.ns.Director.armed.drop, h.ns.Map.byId["zone:1436"])
    # a new song: CANCEL the old ARM, ARM the new song at once (no /reload)
    h.call("Soundtracks.UpdateDrop", "user:test", "zone:1436", {"title": "New"})
    h.rebuild()
    evs = h.events_since(mark)
    assert names(evs) == ["CANCEL", "ARM"] and evs[1]["title"] == "New"
    # removed: the place falls back to the built-in drop below it
    mark = h.mark()
    h.call("Soundtracks.RemoveDrop", "user:test", "zone:1436")
    h.rebuild()
    evs = h.events_since(mark)
    assert names(evs) == ["CANCEL", "ARM"] and (evs[1]["title"], evs[1]["setId"]) == ("Dust", "test-set")


def test_exploration_history_never_hides_a_drop_you_made():
    db = user_db([{"id": "zone:1436", "kind": "zone", "mapID": 1436, "title": "Mine", "artist": "Me"}],
                 active=["user:test", "builtin:test-set"])
    h = AddonHarness(sets=[SET], db=db, state={"exploredTextures": {1436: 6, 1429: 2}})
    cdb = h.char_db()
    assert cdb["done"].get("zone:1429", {}).get("how") == "history"   # built-in: explored = heard
    assert "zone:1436" not in cdb["done"]                              # yours: never scanned
    h.set_location(1436, "")
    h.advance(11)
    assert ids(h.events_of(ET.FIRE)) == ["zone:1436"]


def test_an_old_history_mark_does_not_hide_a_drop_you_made_later():
    db = user_db([{"id": "zone:1436", "kind": "zone", "mapID": 1436, "title": "Mine", "artist": "Me"}])
    h = AddonHarness(sets=[SET], db=db, char_db={"done": {"zone:1436": {"t": 5, "how": "history"}}},
                     companion_data=APP)
    h.set_location(1436, "")
    h.advance(11)
    assert ids(h.events_of(ET.FIRE)) == ["zone:1436"]
    assert h.char_db()["done"]["zone:1436"]["how"] == "played"   # the real play replaces the mark


def test_replay_uses_the_song_in_the_journal_after_the_drop_is_gone():
    db = user_db([{"id": "zone:1436", "kind": "zone", "mapID": 1436, "title": "Mine", "artist": "Me",
                   "youtube": "QAo_Ycocl1E", "startMs": 5000, "durationMs": 20000}])
    h = AddonHarness(sets=[SET], db=db, settings={"historyMode": "fresh"})
    h.set_location(1436, "")
    h.advance(11)
    entry = h.char_db()["journal"][-1]
    assert (entry["youtube"], entry["startMs"], entry["durationMs"], entry["setId"]) == ("QAo_Ycocl1E", 5000, 20000, "test")
    h.advance(21)
    h.call("Soundtracks.RemoveDrop", "user:test", "zone:1436")
    h.rebuild()
    h.slash("replay")
    fire = h.last_event()
    assert fire["type"] == ET.FIRE and fire["flags"] & int(Flags.TEST)
    assert (fire["dropId"], fire["youtube"], fire["title"], fire["startMs"]) == ("zone:1436", "QAo_Ycocl1E", "Mine", 5000)


def test_current_area_id_from_exploration_then_from_known_landmarks():
    h = AddonHarness(state={"exploredAreas": {1411: [362]}})
    assert h.call1("Location.CurrentAreaID", 1411, "Razor Hill") == 362
    h2 = AddonHarness()                                  # no exploration answer: a landmark Mixtape knows
    assert h2.call1("Location.CurrentAreaID", 1429, "Goldshire") == 87
    assert h2.call1("Location.CurrentAreaID", 1429, "Nowhere Special") is None
    h2.set_location(1429, "Goldshire")
    assert h2.call1("Soundtracks.PlaceHere")["id"] == "area:87"


# ------------------------------------------------ real-client finding 7: gamepad --

def gamepad_harness(**kw):
    return AddonHarness(sets=[SET], before_load=lambda hh: hh.shim.EnableGamepadUI(True), **kw)


def gp(h, what):
    return list(h.shim.gamepad[what].values())


def test_the_window_joins_the_gamepad_ui_while_it_is_shown():
    h = gamepad_harness()
    h.slash("")
    f = h.g.NeedleDropperFrame
    assert [h.same(x, f) for x in gp(h, "shown")] == [True]
    h.ns.UI._W.SelectTab(2, True)                      # the Soundtracks tab: the buttons are re-read
    assert any(h.same(x, f) for x in gp(h, "refreshed"))
    h.slash("")                                        # closed again
    assert [h.same(x, f) for x in gp(h, "hidden")] == [True]


def test_the_dialogs_join_the_gamepad_ui_and_never_grab_the_cursor():
    h = gamepad_harness()
    h.slash("import")
    d = h.g.NeedleDropperImportDialog
    assert any(h.same(x, d) for x in gp(h, "shown"))
    assert h.lua("return SHIM.ui.focus") is None       # no edit box took the controller's input
    h.slash("add")                                     # Add drop here: a draft, Artist not focused
    assert h.lua("return SHIM.ui.focus") is None
    plain = AddonHarness(sets=[SET])                   # mouse and keyboard: Ctrl+V ready at once
    plain.slash("import")
    assert plain.lua("return SHIM.ui.focus") is not None


def test_combat_never_calls_the_gamepad_ui_and_catches_up_afterwards():
    h = gamepad_harness()
    h.slash("")
    f = h.g.NeedleDropperFrame
    h.combat(True)                                     # the window hides in combat
    assert not f.IsShown(f)
    assert gp(h, "blocked") == [] and gp(h, "hidden") == []   # no "Interface action failed" in combat
    h.combat(False)                                    # it comes back: hidden, then shown afresh
    assert f.IsShown(f)
    assert gp(h, "blocked") == []
    assert [h.same(x, f) for x in gp(h, "hidden")] == [True]
    assert [h.same(x, f) for x in gp(h, "shown")] == [True, True]
    assert h.lua("return SHIM.gamepad.frames[NeedleDropperFrame]") is True


def test_a_dialog_closed_for_good_in_combat_is_reported_hidden_afterwards():
    h = gamepad_harness()
    h.slash("import")
    d = h.g.NeedleDropperImportDialog
    h.combat(True)
    d.ndReopen = None                                  # the player dismissed it meanwhile
    h.lua("NeedleDropperImportDialog.ndReopen = nil")
    h.combat(False)
    assert not d.IsShown(d)
    assert any(h.same(x, d) for x in gp(h, "hidden"))
    assert h.lua("return SHIM.gamepad.frames[NeedleDropperImportDialog]") is None


def test_without_the_gamepad_ui_nothing_is_called():
    h = AddonHarness(sets=[SET])
    h.slash("")
    assert h.lua("return GamepadMode") is None and h.ns.UI._W.IsGamepadRegistered(h.g.NeedleDropperFrame) is False
