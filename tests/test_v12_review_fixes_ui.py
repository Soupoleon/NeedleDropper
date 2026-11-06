"""Regression tests for the in-game review of the v1.2 window (findings UI-* and journey F3/F5/F9,
2026-11-05), against the real model (Locale, Sets, Soundtracks, Share, Companion) and the real
Director:

* UI-EDIT-ARMED-FIRES: editing the drop where the player stands never fires it mid-edit;
* UI-ENSURE-MYDROPS-NAME / F3: an imported soundtrack called "My Drops" never blocks Add drop here;
* UI-UNDO-BAR-WRONG-RECORD: the "Deleted X. [Undo]" bar undoes that deletion, not the newest edit;
* UI-VOLUME-PER-STEP-COMMIT: one slider drag is one edit and one undo step;
* GAMEPAD-CONTEXT-ONLY-ACTIONS: Copy to / Move to are reachable without a right-click;
* UI-LINK-ONLY-DRAFT / F5: a link alone is a draft, and the window says so; a link typed into
  Artist goes into its own field;
* UI-OPEN-IN-COMBAT, UI-FOOTER-OVERLAP, UI-IDLESS-DROP-CRASH, F9 (Zephras Isle).
"""
from __future__ import annotations

import pytest

from addon_harness import ADDON_NAME
from needledropper.models import EventType as ET
from v12_ui_harness import UIHarness, a1_available

pytestmark = pytest.mark.skipif(not a1_available(), reason="A1's model modules are not in the repo yet")

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"


def real(**kw) -> UIHarness:
    return UIHarness(real=True, **kw)


def drop(h, ref, drop_id):
    v = h.S.GetDrop(ref, drop_id)
    return h.from_lua(v) if v is not None else None


def fires(h, mark, drop_id=None):
    return [e for e in h.events_since(mark) if e["type"] == int(ET.FIRE)
            and (drop_id is None or e.get("dropId") == drop_id)]


def make_drop(h, mapid, sub, artist, title):
    h.set_location(mapid, sub)
    h.click(h.attic(1).AddButton)
    h.flush()
    f = h.form()
    h.type(f.Artist, artist)
    h.tab_key()
    h.type(f.Title, title)
    h.enter()
    h.advance(0.3)
    return h.selected()


# --------------------------------------------------- UI-EDIT-ARMED-FIRES --

def westfall_armed():
    """Arrived in Westfall (Azeroth Mixtape's zone drop zone:1436 armed), cooldown 0."""
    h = real(location=(1411, ""), settings={"historyMode": "fresh", "cooldownMin": 0},
             char_db={"schema": 2, "done": {"zone:1411": {"t": 1, "how": "played"}}})
    h.advance(30)
    h.set_location(1436, "")
    h.advance(1.0)
    assert h.ns.Director.armed.drop.id == "zone:1436"
    return h


def test_change_song_here_on_the_armed_drop_does_not_fire_it_mid_edit():
    h = westfall_armed()
    h.open()
    assert h.selected() == ("builtin:azeroth-mixtape", "zone:1436")
    f = h.form()
    mark = h.mark()
    h.click(f.Change)
    h.flush()
    h.type(f.Artist, "Low Roar")
    h.tab_key()                                   # commits the half-changed song
    h.advance(0.5)
    h.type(f.Title, "I'll Keep")
    h.advance(12.0)                               # the zone dwell passes while typing
    assert fires(h, mark) == []
    assert "zone:1436" not in h.char_db()["done"]
    h.enter()
    h.advance(0.5)
    assert fires(h, mark) == []
    assert (drop(h, "user:my-drops", "zone:1436")["artist"], drop(h, "user:my-drops", "zone:1436")["title"]) == \
        ("Low Roar", "I'll Keep")
    # The next visit plays the finished song.
    h.set_location(1429, "")
    h.advance(2.0)
    mark = h.mark()
    h.set_location(1436, "")
    h.advance(12.0)
    got = fires(h, mark, "zone:1436")
    assert got and (got[0].get("artist"), got[0].get("title")) == ("Low Roar", "I'll Keep")


def test_editing_a_drop_elsewhere_does_not_hold_the_drop_here():
    h = westfall_armed()
    h.open()
    other = make_drop(h, 1429, "Some Farm", "Artist A", "Song A")   # moves the player to Elwynn
    h.set_location(1436, "")                                        # back in Westfall
    h.advance(0.5)
    h.UI._W.Drops.Select(other[0], other[1])
    h.flush()
    mark = h.mark()
    f = h.form()
    h.focus(f.Title)
    h.type(f.Title, "Song A (live)")
    h.enter()
    h.advance(12.0)
    assert [e.get("dropId") for e in fires(h, mark)] == ["zone:1436"]


def test_deleting_the_landmark_drop_here_does_not_start_the_zone_drop():
    h = real(location=(1411, "Razor Hill"), settings={"historyMode": "fresh", "cooldownMin": 0},
             state={"exploredAreas": {1411: [362]}},
             char_db={"schema": 2, "done": {"zone:1411": {"t": 1, "how": "played"}}})
    h.open()
    h.click(h.attic(1).AddButton)           # Razor Hill (area:362), held while editing
    h.flush()
    f = h.form()
    h.type(f.Artist, "ZZ Top")
    h.tab_key()
    h.type(f.Title, "Sharp Dressed Man")
    h.enter()
    h.advance(0.3)
    h.char_db()  # noqa: B018 - the zone drop zone:1411 was heard; forget it, so only the hold keeps it quiet
    h.lua('NeedleDropperCharDB.done["zone:1411"] = nil')
    mark = h.mark()
    h.click(h.buttons(1).Delete)
    h.advance(15.0)
    assert fires(h, mark) == []             # neither the deleted drop nor the zone drop it uncovered


# ------------------------------------------ UI-ENSURE-MYDROPS-NAME / F3 --

def shared_my_drops_code():
    a = real(location=(1411, "Razor Hill"), state={"exploredAreas": {1411: [362]}})
    a.open()
    make_drop(a, 1411, "Razor Hill", "Toto", "Africa")
    pack = a.ns.Share.FromSet("user:my-drops")
    pack = pack[0] if isinstance(pack, tuple) else pack
    code = a.ns.Share.Encode(pack)
    code = code[0] if isinstance(code, tuple) else code
    assert code and str(code).startswith("!NeedleDropper:1!")
    return code


def test_an_imported_my_drops_soundtrack_does_not_block_add_drop_here():
    code = shared_my_drops_code()
    b = real(location=(1429, "Some Uncovered Farm"))
    pack = b.ns.Share.Decode(code)
    pack = pack[0] if isinstance(pack, tuple) else pack
    ref = b.ns.Share.Import(pack, None)
    ref = ref[0] if isinstance(ref, tuple) else ref
    assert ref == "user:my-drops-2" and b.from_lua(b.S.Get(ref))["name"] == "My Drops"
    b.open()
    before = len(b.prints())
    b.click(b.attic(1).AddButton)
    b.flush()
    assert b.prints()[before:] == []
    assert b.selected() == ("user:my-drops", "sub:1429:some-uncovered-farm")
    assert b.from_lua(b.S.Get("user:my-drops"))["name"] == "My Drops (2)"
    assert b.active_refs()[0] == "user:my-drops"
    # /needle add and the key binding take the same road.
    b.set_location(1436, "Moonbrook Fields")
    out = b.slash("/needle add Low Roar - I'll Keep Coming")
    assert any("Added Moonbrook Fields: Low Roar" in line and "My Drops (2)" in line for line in out), out
    assert drop(b, "user:my-drops", "sub:1436:moonbrook-fields")["artist"] == "Low Roar"


def test_ensure_my_drops_keeps_an_existing_my_drops_and_puts_it_first():
    h = real(location=(1411, ""))
    ref = h.call1("Soundtracks.EnsureMyDrops")
    assert ref == "user:my-drops" and h.active_refs()[0] == "user:my-drops"
    h.set_active(["builtin:azeroth-mixtape", "user:my-drops"])
    assert h.call1("Soundtracks.EnsureMyDrops") == "user:my-drops"
    assert h.active_refs()[:2] == ["user:my-drops", "builtin:azeroth-mixtape"]


# ------------------------------------------------- UI-UNDO-BAR-WRONG-RECORD --

def test_undo_bar_brings_back_the_deleted_drop_and_keeps_later_edits():
    h = real(location=(1429, "Farm A"))
    h.open()
    a = make_drop(h, 1429, "Farm A", "Artist A", "Song A")
    b = make_drop(h, 1429, "Farm B", "Artist B", "Song B")
    h.UI._W.Drops.Select(a[0], a[1])
    h.flush()
    h.click(h.buttons(1).Delete)
    assert h.S.GetDrop(*a) is None
    assert h.text(h.drops().UndoBar.Text) == "Deleted Farm A."
    h.UI._W.Drops.Select(b[0], b[1])                # another edit inside the 15 s
    h.flush()
    f = h.form()
    h.focus(f.Title)
    h.type(f.Title, "Song B (live)")
    h.enter()
    h.advance(0.3)
    h.click(h.drops().UndoBar.Button)
    assert drop(h, *a)["title"] == "Song A"         # the deleted drop is back ...
    assert drop(h, *b)["title"] == "Song B (live)"  # ... and the later edit stays
    assert not h.shown(h.drops().UndoBar)
    assert h.selected() == a
    order = [d["id"] for d in h.user_set("my-drops")["drops"]]
    assert order == [a[1], b[1]]                    # back in its old place


def test_undo_remove_refuses_a_token_that_is_not_a_deletion():
    h = real(location=(1429, "Farm A"))
    h.open()
    make_drop(h, 1429, "Farm A", "Artist A", "Song A")
    token = h.call1("Soundtracks.LastUndoToken")
    res = h.S.UndoRemove(token)
    assert res[0] is None and res[2] == "gone"


# ----------------------------------------------- UI-VOLUME-PER-STEP-COMMIT --

def test_one_volume_drag_is_one_edit_and_one_undo_step():
    h = real(location=(1429, "Farm A"))
    h.open()
    a = make_drop(h, 1429, "Farm A", "Artist A", "Song A")
    h.UI._W.Drops.state.more = True
    h.UI._W.Drops.Select(a[0], a[1])
    h.flush()
    f = h.form()
    edits0 = int(h.S.EditCount())
    token0 = h.call1("Soundtracks.LastUndoToken")
    shown0 = int(h.UI._W.EditsSinceLogin())
    for v in range(100, -1, -5):                    # 100 % -> 0 % in 5 % steps: 20 values
        h.set_slider(f.Volume, v)
    assert h.text(f.Volume.RightText) == "0%"
    assert drop(h, *a).get("volume") is None        # nothing saved while the thumb moves
    h.advance(0.5)
    assert drop(h, *a)["volume"] == 0
    assert int(h.S.EditCount()) - edits0 == 1
    assert h.call1("Soundtracks.LastUndoToken") == token0 + 1
    assert int(h.UI._W.EditsSinceLogin()) - shown0 == 1


def test_a_volume_change_is_saved_before_the_selection_moves():
    h = real(location=(1429, "Farm A"))
    h.open()
    a = make_drop(h, 1429, "Farm A", "Artist A", "Song A")
    b = make_drop(h, 1429, "Farm B", "Artist B", "Song B")
    h.UI._W.Drops.state.more = True
    h.UI._W.Drops.Select(a[0], a[1])
    h.flush()
    h.set_slider(h.form().Volume, 40)
    h.UI._W.Drops.Select(b[0], b[1])                # straight to another drop
    h.flush()
    assert drop(h, *a)["volume"] == 40 and drop(h, *b).get("volume") is None


# ------------------------------------------- GAMEPAD-CONTEXT-ONLY-ACTIONS --

def test_copy_and_move_are_reachable_from_the_form_without_a_right_click():
    h = real(location=(1429, "Farm A"))
    trip = h.S.Create("Road Trip")
    h.check_errors()
    h.open()
    a = make_drop(h, 1429, "Farm A", "Artist A", "Song A")
    h.UI._W.Drops.Select(a[0], a[1])
    h.flush()
    f = h.form()
    assert not h.shown(f.copyLine)                  # inside More..., collapsed by default
    h.click(f.More)
    assert h.shown(f.copyLine)
    texts = [i["text"] for i in h.dropdown_items(f.CopyMove) if i["kind"] == "button"]
    assert texts == ["Copy to Road Trip", "Move to Road Trip"]
    h.dropdown_pick(f.CopyMove, "Copy to Road Trip")
    assert drop(h, trip, a[1])["title"] == "Song A" and drop(h, *a) is not None
    h.UI._W.Drops.Select(a[0], a[1])
    h.flush()
    h.click(f.More) if not h.shown(f.copyLine) else None
    assert [i["text"] for i in h.dropdown_items(f.CopyMove) if i["kind"] == "button"] == ["Copy to Road Trip",
                                                                                          "Move to Road Trip"]
    h.S.RemoveDrop(trip, a[1])                      # make room, then move
    h.check_errors()
    h.dropdown_pick(f.CopyMove, "Move to Road Trip")
    assert drop(h, *a) is None and drop(h, trip, a[1]) is not None
    assert h.selected() == (trip, a[1])


def test_a_built_in_drop_can_be_copied_but_not_moved():
    h = real(location=(1429, "Goldshire"))
    h.S.Create("Road Trip")
    h.check_errors()
    h.open()
    assert h.selected() == ("builtin:azeroth-mixtape", "area:87")
    f = h.form()
    h.click(f.More)
    texts = [i["text"] for i in h.dropdown_items(f.CopyMove) if i["kind"] == "button"]
    assert texts == ["Copy to Road Trip"]


# ---------------------------------------------- UI-LINK-ONLY-DRAFT / F5 --

def test_a_link_only_drop_says_it_is_unfinished():
    h = real(location=(1429, "Some Uncovered Farm"))
    h.open()
    h.click(h.attic(1).AddButton)
    h.flush()
    f = h.form()
    h.paste(f.Spotify, "https://open.spotify.com/track/%s?si=abc" % SPOTIFY_ID)
    h.enter()
    d = drop(h, "user:my-drops", "sub:1429:some-uncovered-farm")
    assert d["spotify"] == SPOTIFY_ID and "artist" not in d
    assert h.shown(f.bannerLine)
    assert h.text(f.BannerText).startswith("Type the artist and title to finish this drop")
    h.hover(h.buttons(1).Test)
    assert any("won't play when you arrive" in line for line in h.tooltip())
    h.unhover(h.buttons(1).Test)
    h.focus(f.Artist)
    h.type(f.Artist, "Low Roar")
    h.tab_key()
    h.type(f.Title, "I'll Keep Coming")
    h.enter()
    assert not h.shown(f.bannerLine)                # finished: the hint goes away


def test_a_link_pasted_into_artist_goes_into_its_own_field():
    h = real(location=(1429, "Some Uncovered Farm"))
    h.open()
    h.click(h.attic(1).AddButton)
    h.flush()
    f = h.form()
    assert h.has_focus(f.Artist)
    h.paste(f.Artist, "https://open.spotify.com/intl-de/track/%s?si=abc" % SPOTIFY_ID)
    h.tab_key()
    d = drop(h, "user:my-drops", "sub:1429:some-uncovered-farm")
    assert d["spotify"] == SPOTIFY_ID and "artist" not in d
    assert h.text(f.Artist) == ""                   # the box is free for the artist's name
    h.focus(f.Artist)
    h.paste(f.Artist, "https://youtu.be/QAo_Ycocl1E?t=83")
    h.enter()
    d = drop(h, "user:my-drops", "sub:1429:some-uncovered-farm")
    assert (d["youtube"], d.get("startMs"), d.get("artist")) == ("QAo_Ycocl1E", 83000, None)
    # An 11-letter artist is still an artist, not a YouTube id.
    h.focus(f.Artist)
    h.type(f.Artist, "Evanescence")
    h.enter()
    assert drop(h, "user:my-drops", "sub:1429:some-uncovered-farm")["artist"] == "Evanescence"


# ------------------------------------------------------ UI-OPEN-IN-COMBAT --

def test_opening_in_combat_waits_for_the_end_of_combat_and_joins_the_gamepad_ui():
    h = real(location=(1429, "Goldshire"), before_load=lambda hh: hh.shim.EnableGamepadUI(True))
    h.combat(True)
    before = len(h.prints())
    h.g.NeedleDropper_ToggleWindow()
    h.check_errors()
    assert not h.UI.IsShown()
    assert h.prints()[before:] == ["NeedleDropper: NeedleDropper opens when combat ends."]
    h.slash("/needle soundtracks")                  # asked again: still waiting, said once
    assert not h.UI.IsShown() and h.prints()[before:] == ["NeedleDropper: NeedleDropper opens when combat ends."]
    h.combat(False)
    assert h.UI.IsShown()
    assert h.UI._W.IsGamepadRegistered(h.window)
    assert list(h.ui_shim.blocked.values()) == []


def test_toggling_again_in_combat_cancels_the_pending_open():
    h = real(location=(1429, "Goldshire"))
    h.combat(True)
    h.g.NeedleDropper_ToggleWindow()
    h.g.NeedleDropper_ToggleWindow()
    h.check_errors()
    h.combat(False)
    assert not h.UI.IsShown()


def test_import_and_share_dialogs_wait_for_the_end_of_combat_too():
    h = real(location=(1429, "Goldshire"))
    h.combat(True)
    assert h.UI.OpenImport()
    assert h.UI.OpenShare("builtin:azeroth-mixtape")
    h.check_errors()
    assert not h.shown(h.g.NeedleDropperImportDialog) and not h.shown(h.g.NeedleDropperShareDialog)
    h.combat(False)
    assert h.shown(h.g.NeedleDropperImportDialog) and h.shown(h.g.NeedleDropperShareDialog)
    s = h.g.NeedleDropperShareDialog
    assert h.text(s.Code.GetEditBox(s.Code)).startswith("!NeedleDropper:1!")


# ------------------------------------------------------ UI-FOOTER-OVERLAP --

def _right(frame):
    return frame.GetLeft(frame) + frame.GetWidth(frame)


def test_footer_buttons_never_overlap_at_the_minimum_width():
    h = real(location=(1429, "Goldshire"))           # the Goldshire drop plays after login
    h.open("soundtracks")
    w = h.window
    h.resize(w.Grip, 620, 420)
    for _ in range(10):                              # 10 edits: Save now appears
        h.set_active(["builtin:azeroth-mixtape"])
    h.flush()
    assert h.shown(w.SaveButton)
    bar = h.buttons(2)
    save_right = 6 + w.SaveButton.GetWidth(w.SaveButton)
    bar_left = 620 - 22 - bar.GetWidth(bar)
    assert save_right <= bar_left, (save_right, bar_left)
    assert h.text(w.SaveButton.Text) == "Save now"   # the short label at this width
    assert not h.shown(w.StopButton)                 # no room for both: Save now wins
    h.resize(w.Grip, 900, 500)
    h.flush()
    assert h.text(w.SaveButton.Text) == "Save now (reloads UI)" and h.shown(w.StopButton)
    h.open("drops")
    assert not h.shown(w.StopButton)                 # the Drops tab has its own Stop


# ---------------------------------------------------- UI-IDLESS-DROP-CRASH --

def test_a_stored_drop_without_an_id_never_breaks_the_window():
    db = {"schema": 2, "active": ["user:mine", "builtin:azeroth-mixtape"],
          "soundtracks": {"mine": {"id": "mine", "name": "Mine", "drops": [
              {"kind": "zone", "mapID": 1429, "title": "Africa"},           # no id: repaired from its place
              {"title": "Nowhere"},                                          # no id, no place: unusable
              {"id": "zone:1436", "kind": "zone", "mapID": 1436, "artist": "A", "title": "B"},
          ]}}}
    h = real(location=(1429, ""), db=db)
    assert h.ns.Map.invalid == 1
    stored = h.user_set("mine")["drops"]
    assert stored[0]["id"] == "zone:1429" and stored[0]["kind"] == "zone"
    h.open()
    assert h.UI.IsShown()
    dd = h.attic(1).Showing
    h.dropdown_pick(dd, "Mine")                      # the soundtrack's own view lists its drops
    assert h.UI.IsShown()
    h.check_errors()


# ------------------------------------------------------------- F9 Zephras --

def test_zephras_isle_is_in_the_zone_list():
    h = real(location=(1411, "Razor Hill"), state={"exploredAreas": {1411: [362]}})
    h.open()
    h.click(h.attic(1).AddButton)
    h.flush()
    f = h.form()
    items = h.dropdown_items(f.Zone)
    assert "Zephras Isle" in [i["text"] for i in items if i["kind"] == "radio"]
    h.dropdown_pick(f.Zone, "Zephras Isle")
    assert h.selected()[1].startswith("sub:2521:") or h.selected()[1] == "zone:2521"


def test_window_still_loads_cleanly():
    h = real()
    h.open()
    assert h.ns is not None and ADDON_NAME
