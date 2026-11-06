"""v1.2 soundtrack model: merge order, overrides, disabled entries, drafts, the active lists
and live apply (DESIGN-v1.2 section 4.2; CONTRACTS-v1.2 section 2.5)."""
from __future__ import annotations

import time

import pytest

from v12_model_harness import ModelHarness

MIX = "builtin:azeroth-mixtape"
UNB = "builtin:azeroth-unbound"
FIRE, ARM = 3, 2


def user_set(set_id, drops, name=None, **extra):
    s = {"id": set_id, "name": name or set_id.title(), "author": "", "description": "", "tags": [],
         "revision": 1, "created": 0, "modified": 0, "drops": drops}
    s.update(extra)
    return s


def zone(map_id, title="Song", artist="Artist", **kw):
    d = {"id": "zone:%d" % map_id, "kind": "zone", "mapID": map_id, "label": "Zone %d" % map_id,
         "title": title, "artist": artist}
    d.update(kw)
    return d


def db_with(sets, active, **extra):
    db = {"schema": 2, "soundtracks": {s["id"]: s for s in sets}, "active": active}
    db.update(extra)
    return db


# ------------------------------------------------------------------ the map --

def test_default_list_is_mixtape_and_map_keeps_the_v1_shape():
    h = ModelHarness(profile="model")
    assert h.active() == [MIX]
    M = h.ns.Map
    assert M.loaded is True and M.hash == 0 and M.version == 2
    assert list(M.packs.values()) == ["Azeroth Mixtape"] and list(M.refs.values()) == [MIX]
    drops = list(M.drops.values())
    assert len(drops) == 38 and M.invalid == 0 and len(list(M.problems.values())) == 0
    same = h.rt.eval("rawequal")
    for i, d in enumerate(drops, 1):
        assert d.n == i and same(M.byN[i], d) and same(M.byId[d.id], d)
        assert d.setRef == MIX and d.setId == "azeroth-mixtape" and d.builtin is True
        assert d.setName == "Azeroth Mixtape" and isinstance(d.songKey, str)
        assert d.file is None and d.volume is None
    assert M.zoneDrops[1429].id == "zone:1429"
    goldshire = M.areaDrops[87]
    assert goldshire.id == "area:87" and goldshire.subzoneLower == "goldshire"
    assert any(same(goldshire, x) for x in M.subDrops[1429].values())
    # Core's landmark lookup works on the new map
    assert h.run("local ns = ... return ns.FindSubzoneDrop(1429, 'goldshire').id", h.ns) == "area:87"


def test_top_of_the_list_wins_and_the_rest_is_overridden():
    h = ModelHarness(profile="model")
    mix_elwynn = h.map_drop("zone:1429")["title"]
    assert h.call("Soundtracks.SetActive", [UNB, MIX]) == (True,)
    h.rebuild()
    unb = h.call1("Sets.GetBuiltin", "azeroth-unbound")
    unb_elwynn = next(d for d in unb["drops"] if d["id"] == "zone:1429")
    assert h.map_drop("zone:1429")["title"] == unb_elwynn["title"] != mix_elwynn
    assert h.map_drop("zone:1429")["setRef"] == UNB
    # Mixtape-only places still play from Mixtape
    assert h.map_drop("area:87")["setRef"] == MIX
    merged = h.call1("Soundtracks.Merged")
    elwynn = [e for e in merged if e["id"] == "zone:1429"]
    assert len(elwynn) == 1  # what plays, by place
    e = elwynn[0]
    assert e["setRef"] == UNB and e["plays"] is True and e["priority"] == 1 and e["overrides"] == [MIX]
    assert e["overriddenBy"] in ({}, []) and e["drop"]["title"] == unb_elwynn["title"]
    assert len(merged) == len(h.map_ids()) and len({x["id"] for x in merged}) == len(merged)
    mix_ids = {d["id"] for d in h.call1("Sets.GetBuiltin", "azeroth-mixtape")["drops"]}
    unb_ids = {d["id"] for d in unb["drops"]}
    assert h.ns.Map.overridden == len(mix_ids & unb_ids)
    assert len(h.map_ids()) == len(mix_ids | unb_ids)
    # reversing the order reverses the winner
    h.call("Soundtracks.SetActive", [MIX, UNB])
    h.rebuild()
    assert h.map_drop("zone:1429")["title"] == mix_elwynn


def test_turned_off_entry_claims_the_place_and_plays_nothing():
    db = db_with([user_set("quiet", [{"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
                                      "disabled": True}])], ["user:quiet", MIX])
    h = ModelHarness(profile="model", db=db)
    assert h.map_drop("zone:1429") is None and h.ns.Map.zoneDrops[1429] is None
    assert h.ns.Map.disabled == 1
    merged = h.call1("Soundtracks.Merged")
    off = next(e for e in merged if e["id"] == "zone:1429")
    assert off["setRef"] == "user:quiet" and off["disabled"] is True and off["plays"] is False
    assert off["overrides"] == [MIX]
    assert len(merged) == 38  # the turned-off place is listed (it claims Elwynn), nothing else changes


def test_drafts_claim_nothing_and_never_play():
    draft = {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "artist": "Only an artist"}
    db = db_with([user_set("wip", [draft, zone(77001, title="", artist="")])], ["user:wip", MIX])
    h = ModelHarness(profile="model", db=db)
    assert h.map_drop("zone:1429")["setRef"] == MIX
    assert h.map_drop("zone:77001") is None
    assert h.ns.Map.drafts == 2
    merged = h.call1("Soundtracks.Merged")
    assert not [e for e in merged if e["setRef"] == "user:wip"]  # drafts are not listed
    mix = next(e for e in merged if e["id"] == "zone:1429")
    assert mix["setRef"] == MIX and mix["overrides"] in ({}, []) and mix["plays"] is True
    assert h.call1("Soundtracks.IsDraft", draft) is True
    assert h.call1("Soundtracks.IsDraft", {"disabled": True, "mapID": 1}) is False


def test_landmark_drop_of_a_higher_soundtrack_is_found_first():
    by_name = {"id": "sub:1429:goldshire", "kind": "subzone", "mapID": 1429, "subzone": "Goldshire",
               "label": "Goldshire", "title": "Mine", "artist": "Me"}
    h = ModelHarness(profile="model", db=db_with([user_set("mine", [by_name])], ["user:mine", MIX]))
    assert {"area:87", "sub:1429:goldshire"} <= set(h.map_ids())
    first = h.run("local ns = ... return ns.FindSubzoneDrop(1429, 'goldshire').id", h.ns)
    assert first == "sub:1429:goldshire"
    h.call("Soundtracks.SetActive", [MIX, "user:mine"])
    h.rebuild()
    assert h.run("local ns = ... return ns.FindSubzoneDrop(1429, 'goldshire').id", h.ns) == "area:87"


# --------------------------------------------------------------- active lists --

def test_missing_refs_are_skipped_and_pruned_when_the_game_saves():
    h = ModelHarness(profile="model", db=db_with([], ["user:gone", UNB, "builtin:nope", UNB, 42]))
    assert h.active() == [UNB]
    assert list(h.ns.Map.packs.values()) == ["Azeroth Unbound"]
    # non-strings and repeats are cleaned at login; missing soundtracks stay until the game saves
    assert h.account_db()["active"] == ["user:gone", UNB, "builtin:nope"]
    h.logout()
    assert h.account_db()["active"] == [UNB]


def test_a_list_left_with_nothing_valid_falls_back_to_the_default():
    h = ModelHarness(profile="model", db=db_with([], ["user:gone", "builtin:retired"]))
    assert h.active() == [MIX] and len(h.map_ids()) == 38
    h.logout()
    assert h.account_db()["active"] == [MIX]


def test_the_player_can_turn_every_soundtrack_off():
    h = ModelHarness(profile="model")
    assert h.call("Soundtracks.SetActive", []) == (True,)
    h.rebuild()
    assert h.active() == [] and h.map_ids() == []
    h.logout()
    assert h.account_db()["active"] in ({}, [])
    assert h.call("Soundtracks.SetOn", MIX, True) == (True,)
    assert h.active() == [MIX]


def test_set_active_validates():
    h = ModelHarness(profile="model")
    ok, err = h.call("Soundtracks.SetActive", [MIX, MIX])
    assert ok is None and "once" in err
    ok, err = h.call("Soundtracks.SetActive", ["user:nope"])
    assert ok is None and "user:nope" in err
    assert h.active() == [MIX]


def test_per_character_list():
    h = ModelHarness(profile="model")
    assert h.call1("Soundtracks.UsesCharacterList") is False
    assert h.call("Soundtracks.SetUseCharacterList", True) == (True,)
    assert h.char_db()["active"] == [MIX] and h.call1("Soundtracks.UsesCharacterList") is True
    h.call("Soundtracks.SetActive", [UNB])
    assert h.char_db()["active"] == [UNB] and h.account_db()["active"] == [MIX]
    h.rebuild()
    assert h.active() == [UNB] and list(h.ns.Map.packs.values()) == ["Azeroth Unbound"]
    assert h.run("local ns = ... return ns.ActiveRefs()[1]", h.ns) == UNB
    h.call("Soundtracks.SetUseCharacterList", False)
    assert "active" not in h.char_db()
    h.rebuild()
    assert h.active() == [MIX]


def test_a_character_list_from_saved_variables_is_used():
    h = ModelHarness(profile="model", db=db_with([], [MIX]), char_db={"schema": 2, "active": [UNB]})
    assert h.active() == [UNB] and h.call1("Soundtracks.UsesCharacterList") is True


def test_set_on_and_move():
    h = ModelHarness(profile="model")
    ref = h.call1("Soundtracks.Create", "Mine")
    assert h.active() == [ref, MIX]
    h.call("Soundtracks.SetOn", UNB, True)
    assert h.active() == [UNB, ref, MIX]
    h.call("Soundtracks.MoveActive", UNB, 5)
    assert h.active() == [ref, MIX, UNB]
    h.call("Soundtracks.MoveActive", UNB, -1)
    assert h.active() == [ref, UNB, MIX]
    h.call("Soundtracks.SetOn", ref, False)
    assert h.active() == [UNB, MIX]
    ok, err = h.call("Soundtracks.MoveActive", ref, 1)
    assert ok is None and err


def test_turning_on_keeps_my_drops_on_top():
    h = ModelHarness(profile="model")
    h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "X"})
    assert h.active() == ["user:my-drops", MIX]
    h.call("Soundtracks.SetOn", UNB, True)
    assert h.active() == ["user:my-drops", UNB, MIX]


# ---------------------------------------------------------------- live apply --

def test_edits_fire_at_once_and_rebuild_after_a_quarter_second():
    h = ModelHarness(profile="model")
    ref = h.call1("Soundtracks.Create", "Live")
    h.rebuild()
    h.record_fires()
    h.call("Soundtracks.AddDrop", ref, {"mapID": 77002, "artist": "A", "title": "One"})
    h.call("Soundtracks.AddDrop", ref, {"mapID": 77003, "artist": "A", "title": "Two"})
    changed = h.fired("ND_SOUNDTRACKS_CHANGED")
    assert [c[0] for c in changed] == ["add", "add"] and changed[0][1] == ref
    assert h.fired("ND_MAP_CHANGED") == [] and h.call1("Soundtracks.RebuildPending") is True
    assert h.map_drop("zone:77002") is None
    h.advance(0.2)
    assert h.fired("ND_MAP_CHANGED") == []
    h.advance(0.1)
    assert len(h.fired("ND_MAP_CHANGED")) == 1
    assert h.map_drop("zone:77002")["title"] == "One" and h.map_drop("zone:77003")["title"] == "Two"
    assert h.call1("Soundtracks.EditCount") == 3
    # an immediate rebuild cancels the pending one
    h.call("Soundtracks.UpdateDrop", ref, "zone:77002", {"title": "Uno"})
    h.call("Soundtracks.BuildActiveMap")
    assert h.map_drop("zone:77002")["title"] == "Uno"
    h.advance(0.5)
    assert len(h.fired("ND_MAP_CHANGED")) == 2


def test_a_new_user_drop_plays_on_arrival_without_a_reload():
    h = ModelHarness(settings={"historyMode": "fresh"})
    h.set_location(1436, "")
    ref = h.call1("Soundtracks.Create", "Westfall Mix")
    assert h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "Toto", "title": "Africa",
                                               "youtube": "QAo_Ycocl1E"}) == ("zone:1436",)
    h.rebuild()
    assert h.map_drop("zone:1436")["setRef"] == ref
    # the Director had armed Mixtape's Westfall drop: re-entering picks the new one up
    h.set_location(1429, "")
    h.set_location(1436, "")
    mark = h.mark()
    h.advance(15)
    fires = [e for e in h.events_since(mark) if e["type"] == FIRE]
    # v2 frames carry the song itself (Emit.lua): the user drop's id, song and soundtrack id
    assert fires and fires[0]["dropId"] == "zone:1436"
    assert (fires[0]["artist"], fires[0]["title"], fires[0]["youtube"]) == ("Toto", "Africa", "QAo_Ycocl1E")
    assert fires[0]["setId"] == h.map_drop("zone:1436")["setId"]
    assert h.char_db()["journal"][-1]["title"] == "Africa"


def test_map_drops_carry_the_in_game_file_path_and_a_song_key():
    h = ModelHarness(profile="model")
    ref = h.call1("Soundtracks.Create", "Files")
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1453, "artist": "Me", "title": "Home", "file": "stormwind.mp3",
                                        "volume": 70, "startMs": 1500})
    h.rebuild()
    d = h.map_drop("zone:1453")
    assert d["file"] == "Interface\\AddOns\\NeedleDropper_Local\\media\\stormwind.mp3"
    assert d["fileName"] == "stormwind.mp3" and d["volume"] == 70 and d["startMs"] == 1500
    key = d["songKey"]
    h.call("Soundtracks.UpdateDrop", ref, "zone:1453", {"volume": 80})
    h.rebuild()
    assert h.map_drop("zone:1453")["songKey"] != key
    h.call("Soundtracks.UpdateDrop", ref, "zone:1453", {"note": "a note does not change the song"})
    h.rebuild()
    key2 = h.map_drop("zone:1453")["songKey"]
    h.call("Soundtracks.UpdateDrop", ref, "zone:1453", {"note": "another"})
    h.rebuild()
    assert h.map_drop("zone:1453")["songKey"] == key2


def test_malformed_saved_drops_are_skipped_not_deleted():
    bad = {"id": "zone:5", "kind": "zone", "label": "no map id", "title": "t", "artist": "a"}
    db = db_with([user_set("odd", [bad, "not a table", zone(77004)])], ["user:odd"])
    h = ModelHarness(profile="model", db=db)
    assert h.ns.Map.invalid == 1 and "user:odd" in list(h.ns.Map.problems.values())[0]
    assert h.map_ids() == ["zone:77004"]
    stored = h.user_set("odd")["drops"]
    assert len(stored) == 2 and stored[0]["id"] == "zone:5"  # kept for the player to fix; junk removed


def test_list_reports_soundtracks_and_counts():
    draft = {"id": "zone:1426", "kind": "zone", "mapID": 1426, "label": "x", "title": "only a title"}
    off = {"id": "zone:1411", "kind": "zone", "mapID": 1411, "label": "Durotar", "disabled": True}
    sets = [user_set("zeta", [zone(1429), draft, off], name="zeta mix"),
            user_set("alpha", [zone(77005)], name="Alpha")]
    h = ModelHarness(profile="model", db=db_with(sets, ["user:zeta", MIX]))
    lst = h.call1("Soundtracks.List")
    assert [e["ref"] for e in lst] == [MIX, UNB, "user:alpha", "user:zeta"]
    mix, unb, alpha, zeta = lst
    assert mix["builtin"] is True and mix["active"] is True and mix["priority"] == 2 and mix["dropCount"] == 38
    assert mix["covered"] == 2  # zone:1429 (song) and zone:1411 (turned off) are covered from above
    assert unb["active"] is False and "priority" not in unb and unb["dropCount"] == 62
    assert alpha["active"] is False and alpha["places"] == 1
    assert zeta["priority"] == 1 and zeta["dropCount"] == 3 and zeta["places"] == 1 and zeta["drafts"] == 1
    assert zeta["covered"] == 0 and zeta["name"] == "zeta mix" and zeta["builtin"] is False


def test_rebuild_with_3000_drops_stays_under_20_ms():
    sets = []
    for s in range(10):
        drops = [zone(100000 + s * 300 + i, title="Title %d" % i, artist="Artist %d" % s,
                      youtube="QAo_Ycocl1E", durationMs=200000, note="why it fits " * 3, tags=["fitting"])
                 for i in range(300)]
        sets.append(user_set("big-%d" % s, drops))
    db = db_with(sets, ["user:big-%d" % s for s in range(10)] + [MIX])
    h = ModelHarness(profile="model", db=db)
    assert len(h.map_ids()) == 3000 + 38
    build = h.fn("Soundtracks.BuildActiveMap")
    best = float("inf")
    for _ in range(5):
        t0 = time.perf_counter()
        build()
        best = min(best, time.perf_counter() - t0)
    assert best < 0.020, f"rebuild took {best * 1000:.1f} ms"


def test_list_and_merged_hand_out_fresh_arrays():
    """The window may sort or trim what it gets; the model's cache must not change."""
    h = ModelHarness(profile="model")
    h.run("""local ns = ...
      local l = ns.Soundtracks.List()
      table.sort(l, function(a, b) return a.ref > b.ref end)
      local m = ns.Soundtracks.Merged()
      for i = #m, 1, -1 do m[i] = nil end""", h.ns)
    assert [e["ref"] for e in h.call1("Soundtracks.List")] == [MIX, UNB]
    assert len(h.call1("Soundtracks.Merged")) == 38
