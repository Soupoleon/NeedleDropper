"""Share codes in and out of the model (DESIGN-v1.2 sections 5.2, 5.5, 5.6; CONTRACTS-v1.2 2.6):
export (FromSet / Encode), import (never overwrites, never turns on), updates (same id, same
author, higher revision; on/off state and order kept), the import preview and undo."""
from __future__ import annotations

import pytest

from v12_model_harness import ModelHarness, canon, ref_decode, ref_encode

MIX = "builtin:azeroth-mixtape"
MY = "user:my-drops"
SPOTIFY = "4uLU6hMCjMI75M1A2tKUQC"


def pack(**top):
    p = {"author": "Ethan", "description": "Songs for my alts", "drops": [
        {"areaID": 87, "artist": "Toto", "label": "Goldshire", "subzone": "Goldshire", "tags": ["funny"],
         "title": "Africa", "youtube": "QAo_Ycocl1E", "zone": 1429},
        {"artist": "Low Roar", "durationMs": 352000, "spotify": "spotify:track:" + SPOTIFY,
         "title": "I'll Keep Coming", "zone": 1436},
        {"disabled": True, "zone": 1426},
        {"artist": "Me", "file": "stormwind.mp3", "inGameFile": True, "title": "Home", "zone": 1453}],
        "format": "needledropper-pack", "id": "my-drops", "name": "My Drops", "revision": 4, "tags": [],
        "version": 1}
    p.update(top)
    return p


@pytest.fixture
def h():
    return ModelHarness(profile="model")


def encode(h, p):
    """Share.Encode -> (code, None) or (None, message)."""
    res = h.call("Share.Encode", p)
    return (res + (None,))[:2]


def err_of(res):
    assert res[0] is None, f"expected an error, got {res!r}"
    return res[1]


# ------------------------------------------------------------------- import --

def test_import_never_overwrites_and_never_turns_it_on(h):
    before = h.active()
    ref = h.call1("Share.Import", pack())
    assert ref == "user:my-drops-2"  # "my-drops" is the player's own layer: never taken by an import
    assert h.active() == before
    s = h.user_set("my-drops-2")
    assert s["name"] == "My Drops" and s["author"] == "Ethan" and s["description"] == "Songs for my alts"
    assert s["packId"] == "my-drops" and s["packRevision"] == 4 and s["revision"] == 4 and s["imported"] > 0
    ids = [d["id"] for d in s["drops"]]
    assert ids == ["area:87", "zone:1436", "zone:1426", "zone:1453"]
    by_id = {d["id"]: d for d in s["drops"]}
    assert by_id["zone:1436"]["spotify"] == SPOTIFY and by_id["zone:1436"]["label"] == "Westfall"
    assert by_id["zone:1426"]["disabled"] is True and by_id["zone:1426"]["label"] == "Zone 1426"
    assert by_id["zone:1453"]["file"] == "stormwind.mp3" and by_id["area:87"]["tags"] == ["funny"]
    h.rebuild()
    assert h.map_drop("zone:1436")["setRef"] == MIX  # off: nothing changed in what plays
    again = h.call1("Share.Import", pack())
    assert again == "user:my-drops-3" and h.user_set("my-drops-3")["name"] == "My Drops (2)"
    assert h.user_set("my-drops-2")["name"] == "My Drops"


def test_built_in_ids_and_names_are_never_taken(h):
    code_pack, _ = h.call("Share.FromSet", MIX)
    ref = h.call1("Share.Import", code_pack)
    assert ref == "user:azeroth-mixtape-2"
    s = h.user_set("azeroth-mixtape-2")
    assert s["name"] == "Azeroth Mixtape (2)" and len(s["drops"]) == 38


def test_the_import_dialog_can_rename(h):
    assert h.call1("Share.Import", pack(), {"name": "  Bob's Alts  "}) == "user:my-drops-2"
    assert h.user_set("my-drops-2")["name"] == "Bob's Alts"
    assert "64" in err_of(h.call("Share.Import", pack(), {"name": "x" * 65}))


def test_an_imported_soundtrack_plays_once_turned_on(h):
    ref = h.call1("Share.Import", pack())
    h.call("Soundtracks.SetOn", ref, True)
    h.rebuild()
    assert h.map_drop("zone:1436")["title"] == "I'll Keep Coming" and h.map_drop("zone:1436")["setRef"] == ref
    assert h.map_drop("zone:1426") is None  # turned off there
    assert h.map_drop("zone:1453")["file"].endswith("NeedleDropper_Local\\media\\stormwind.mp3")


def test_import_refuses_invalid_packs_and_respects_limits():
    h = ModelHarness(profile="model")
    assert "name" in err_of(h.call("Share.Import", pack(name="")))
    sets = {"s%d" % i: {"id": "s%d" % i, "name": "S %d" % i, "drops": []} for i in range(50)}
    full = ModelHarness(profile="model", db={"schema": 2, "soundtracks": sets, "active": [MIX]})
    assert "50" in err_of(full.call("Share.Import", pack()))
    drops = {"big%d" % s: {"id": "big%d" % s, "name": "B%d" % s, "drops": [
        {"id": "zone:%d" % (100000 + s * 300 + i), "kind": "zone", "mapID": 100000 + s * 300 + i, "label": "z",
         "title": "t", "artist": "a"} for i in range(300)]} for s in range(10)}
    crowded = ModelHarness(profile="model", db={"schema": 2, "soundtracks": drops, "active": [MIX]})
    assert "3000" in err_of(crowded.call("Share.Import", pack()))


def test_undoing_an_import_removes_it(h):
    ref = h.call1("Share.Import", pack())
    assert h.call("Soundtracks.CanUndo") == (True, "Import My Drops")
    assert h.call("Soundtracks.Undo")[0] is True
    assert h.call1("Soundtracks.Exists", ref) is False


# ------------------------------------------------------------------- update --

def test_can_update_needs_the_same_id_and_author_and_a_higher_revision(h):
    ref = h.call1("Share.Import", pack())
    assert h.call1("Share.CanUpdate", pack(revision=5)) == ref
    assert h.call1("Share.CanUpdate", pack(revision=4)) is None
    assert h.call1("Share.CanUpdate", pack(revision=3)) is None
    assert h.call1("Share.CanUpdate", pack(revision=9, author="Mallory")) is None
    assert h.call1("Share.CanUpdate", pack(revision=9, id="other")) is None
    anon = h.call1("Share.Import", pack(id="anon", author=""))
    assert anon and h.call1("Share.CanUpdate", pack(id="anon", author="", revision=99)) is None
    # local edits do not hide an update from the author
    h.call("Soundtracks.UpdateDrop", ref, "zone:1436", {"title": "Edited here"})
    h.call("Soundtracks.UpdateDrop", ref, "zone:1436", {"title": "And again"})
    assert h.user_set("my-drops-2")["revision"] == 6
    assert h.call1("Share.CanUpdate", pack(revision=5)) == ref


def test_your_own_soundtrack_can_be_updated_from_its_code(h):
    ref = h.call1("Soundtracks.Create", "Westfall Mix")
    h.call("Soundtracks.SetDetails", ref, {"author": "Ethan"})
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "Toto", "title": "Africa"})
    code_pack, _ = h.call("Share.FromSet", ref)
    assert code_pack["id"] == "westfall-mix" and code_pack["revision"] == 2
    newer = dict(code_pack, revision=7)
    assert h.call1("Share.CanUpdate", newer) == ref


def test_update_keeps_on_off_state_order_and_your_name(h):
    ref = h.call1("Share.Import", pack())
    h.call("Soundtracks.SetActive", ["builtin:azeroth-unbound", ref, MIX])
    newer = pack(revision=5, name="My Drops v2", description="New description",
                 drops=[{"zone": 1411, "artist": "New", "title": "Song"}])
    assert h.call("Share.Update", ref, newer) == (True,)
    assert h.active() == ["builtin:azeroth-unbound", ref, MIX]
    s = h.user_set("my-drops-2")
    assert [d["id"] for d in s["drops"]] == ["zone:1411"] and s["description"] == "New description"
    assert s["packRevision"] == 5 and s["revision"] == 5 and s["name"] == "My Drops v2"
    # a soundtrack the player renamed keeps its name
    h.call("Soundtracks.Rename", ref, "Mine Now")
    h.call("Share.Update", ref, pack(revision=6, name="My Drops v3"))
    assert h.user_set("my-drops-2")["name"] == "Mine Now" and len(h.user_set("my-drops-2")["drops"]) == 4
    assert h.call("Soundtracks.Undo") == (True, "Update Mine Now")
    assert h.user_set("my-drops-2")["drops"][0]["id"] == "zone:1411"
    assert "no longer exists" in err_of(h.call("Share.Update", MIX, newer))


# ------------------------------------------------------------------- export --

def test_from_set_writes_the_pack_format(h):
    ref = h.call1("Soundtracks.Create", "Export Me")
    h.call("Soundtracks.SetDetails", ref, {"author": "Ethan", "tags": ["calm"], "homepage": "https://example.com"})
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "Toto", "title": "Africa", "spotify": SPOTIFY,
                                        "startMs": 0, "durationMs": 295000, "volume": 70, "tags": ["funny"],
                                        "note": "why"})
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1453, "artist": "Me", "title": "Home", "file": "home.ogg"})
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1429, "subzone": "Goldshire", "artist": "Draft only"})
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1411, "artist": "Off", "title": "Song", "disabled": True})
    p, omitted = h.call("Share.FromSet", ref)
    assert omitted == 1
    assert p["format"] == "needledropper-pack" and p["version"] == 1 and p["id"] == "export-me"
    assert p["name"] == "Export Me" and p["author"] == "Ethan" and p["tags"] == ["calm"]
    assert p["homepage"] == "https://example.com" and p["revision"] == 5
    d0, d1, d2 = p["drops"]
    assert d0 == {"zone": 1436, "label": "Westfall", "artist": "Toto", "title": "Africa",
                  "spotify": "spotify:track:" + SPOTIFY, "durationMs": 295000, "volume": 70, "tags": ["funny"],
                  "note": "why"}
    assert d1 == {"zone": 1453, "label": "Stormwind City", "artist": "Me", "title": "Home", "file": "home.ogg",
                  "inGameFile": True}
    assert d2 == {"zone": 1411, "disabled": True}
    code, err = encode(h, p)
    assert err is None and ref_decode(code) == canon(p)


def test_nothing_to_share_says_so(h):
    ref = h.call1("Soundtracks.Create", "Drafts")
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "Only an artist"})
    p, omitted = h.call("Share.FromSet", ref)
    assert omitted == 1 and p["drops"] in ({}, [])
    assert "nothing to share" in err_of(h.call("Share.Encode", p))
    assert h.call("Share.FromSet", "user:nope") == (None, 0)


def test_a_soundtrack_survives_a_round_trip(h):
    ref = h.call1("Soundtracks.Create", "Round Trip")
    h.call("Soundtracks.SetDetails", ref, {"author": "Ethan", "description": "Two\nlines", "tags": ["a", "b"]})
    drops = [
        {"mapID": 1436, "artist": "Toto", "title": "Africa", "spotify": SPOTIFY, "youtube": "QAo_Ycocl1E",
         "startMs": 1500, "durationMs": 295000, "volume": 0, "tags": ["funny"], "note": "n\nn"},
        {"mapID": 1429, "subzone": "Lion's Pride Inn", "artist": "By", "title": "Name", "label": "The Inn"},
        {"mapID": 1429, "areaID": 87, "subzone": "Goldshire", "artist": "Area", "title": "Id"},
        {"instanceID": 36, "mapID": 1436, "artist": "In", "title": "Stance", "label": "The Deadmines"},
        {"mapID": 1453, "artist": "File", "title": "Drop", "file": "a (b)_c-d.e.mp3"},
        {"mapID": 1411, "disabled": True},
    ]
    for d in drops:
        assert h.call("Soundtracks.AddDrop", ref, d)[0]
    code, err = encode(h, h.call1("Share.FromSet", ref))
    assert err is None
    decoded, warnings = h.call("Share.Decode", code)
    assert canon(warnings) == []
    new = h.call1("Share.Import", decoded)
    keys = ("id", "kind", "mapID", "areaID", "subzone", "instanceID", "label", "title", "artist", "spotify", "youtube",
            "file", "startMs", "durationMs", "volume", "tags", "note", "disabled")
    mine = [{k: d.get(k) for k in keys} for d in h.user_set(ref.split(":")[1])["drops"]]
    theirs = [{k: d.get(k) for k in keys} for d in h.user_set(new.split(":")[1])["drops"]]
    assert theirs == mine
    s = h.user_set(new.split(":")[1])
    assert s["description"] == "Two\nlines" and s["tags"] == ["a", "b"] and s["author"] == "Ethan"


# ------------------------------------------------------------------ preview --

def test_import_preview(h):
    many = pack(drops=pack()["drops"] + [{"zone": 50000 + i, "artist": "a", "title": "t%d" % i} for i in range(15)]
                + [{"zone": 1429, "subzone": "Lion's Pride Inn", "artist": "x", "title": "y", "youtub": "typo"}])
    info = h.call1("Share.Preview", many)
    assert info["name"] == "My Drops" and info["author"] == "Ethan" and info["revision"] == 4
    assert info["dropCount"] == 20 and len(info["places"]) == 12 and info["id"] == "my-drops"
    first = info["places"][0]
    assert first == {"id": "area:87", "label": "Goldshire", "sources": "Y", "disabled": False}
    assert info["places"][1]["sources"] == "S" and info["places"][2]["disabled"] is True
    assert info["places"][3]["sources"] == "F"
    assert info["unknownZones"] == 16 and info["files"] == 1 and info["byName"] == 1
    assert info["overrides"] == 4  # area:87, zone:1436, zone:1426 and zone:1453 are in Azeroth Mixtape
    text = "\n".join(info["warnings"])
    assert "Zones this game doesn't know" in text and "1426" in text
    assert "in-game files" in text and "saved by name" in text and "4 places" in text
    assert 'Unknown field "youtub" was ignored.' in text
    assert "update" not in info
    h.call("Share.Import", pack())
    assert h.call1("Share.Preview", pack(revision=9))["update"] == "user:my-drops-2"
    bad = h.call("Share.Preview", pack(name=""))
    assert bad[0] is None and bad[1] == "invalid"


# ------------------------------------------------------- companion migration --

def test_companion_imports_go_through_the_normal_import():
    code = ref_encode(pack(id="v11-pack", name="Old Pack", revision=1))
    h = ModelHarness(profile="model", companion_data={"schema": 1, "appVersion": "1.2.0", "written": 1,
                                                      "imports": [{"code": code, "active": True}]})
    offer = h.call1("Companion.Imports")[0]
    ref = h.call1("Share.Import", offer["pack"])
    assert ref == "user:v11-pack" and ref not in h.active()
    h.fn("Companion.MarkImportHandled")(h.fn("Companion.Imports")()[1])
    assert h.call1("Companion.Imports") in ({}, [])
    if offer["active"]:  # the player confirmed the app's "turn these on" in the prompt
        h.call("Soundtracks.SetOn", ref, True)
    assert ref in h.active()


def test_a_name_from_the_dialog_that_is_taken_gets_a_number(h):
    assert h.call1("Share.Import", pack(), {"name": "Azeroth Mixtape"}) == "user:my-drops-2"
    assert h.user_set("my-drops-2")["name"] == "Azeroth Mixtape (2)"
    h.call("Share.Import", pack(id="other"), {"name": "azeroth mixtape (2)"})
    assert h.user_set("other")["name"] == "azeroth mixtape (2) (2)"


def test_an_app_offer_that_cannot_be_imported_stays_on_offer():
    code = ref_encode(pack(id="v11-pack", name="Old Pack", revision=1))
    sets = {"s%d" % i: {"id": "s%d" % i, "name": "S %d" % i, "drops": []} for i in range(50)}
    h = ModelHarness(profile="model", db={"schema": 2, "soundtracks": sets, "active": [MIX]},
                     companion_data={"schema": 1, "appVersion": "1.2.0", "written": 1,
                                     "imports": [{"code": code, "active": False}]})
    offer = h.call1("Companion.Imports")[0]
    assert "50" in err_of(h.call("Share.Import", offer["pack"]))
    assert [o["key"] for o in h.call1("Companion.Imports")] == ["v11-pack@1"]  # still offered next time
    h.call("Soundtracks.Delete", "user:s0")
    assert h.call1("Share.Import", offer["pack"]) == "user:v11-pack"
