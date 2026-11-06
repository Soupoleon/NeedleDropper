"""v1.2 model: the schema 1 -> 2 migration, history continuity from v1.1 and the history rule
(DESIGN-v1.2 sections 3 and 4.2)."""
from __future__ import annotations

import json

import pytest

from addon_harness import ADDON_DIR
from v12_model_harness import ModelHarness

MIX = "builtin:azeroth-mixtape"
UNB = "builtin:azeroth-unbound"

#: The 62 drop ids of the v1.1 Map.lua / Map.json (generated from Azeroth Unbound, hash 39761),
#: pinned here because the integrator deletes Map.json in v1.2.
V11_MAP_IDS = [
    'area:150', 'area:16727', 'area:17045', 'area:2255', 'area:2268', 'area:2361', 'area:271',
    'area:279', 'area:3425', 'area:35', 'area:541', 'area:976', 'zone:1411', 'zone:1412',
    'zone:1413', 'zone:1416', 'zone:1417', 'zone:1418', 'zone:1419', 'zone:1420', 'zone:1421',
    'zone:1422', 'zone:1423', 'zone:1424', 'zone:1425', 'zone:1426', 'zone:1427', 'zone:1428',
    'zone:1429', 'zone:1430', 'zone:1431', 'zone:1432', 'zone:1433', 'zone:1434', 'zone:1435',
    'zone:1436', 'zone:1437', 'zone:1438', 'zone:1439', 'zone:1440', 'zone:1441', 'zone:1442',
    'zone:1443', 'zone:1444', 'zone:1445', 'zone:1446', 'zone:1447', 'zone:1448', 'zone:1449',
    'zone:1450', 'zone:1451', 'zone:1452', 'zone:1453', 'zone:1454', 'zone:1455', 'zone:1456',
    'zone:1457', 'zone:1458', 'zone:2482', 'zone:2521', 'zone:2548', 'zone:2652',
]

V11_SETTINGS = {"enabled": True, "dwellSec": 12, "cooldownMin": 7, "historyMode": "fresh", "scope": "account",
                "beaconCellPx": 4, "chatlog": True, "toastScale": 1.2, "debug": False}


def v11_db(**settings):
    s = dict(V11_SETTINGS)
    s.update(settings)
    return {"schema": 1, "settings": s, "lastSeq": 12,
            "accountDone": {"zone:1429": {"t": 1700000000, "how": "played"}, "zone:1437": {"t": 1700000100, "how": "history"}}}


def v11_char():
    return {"schema": 1, "lastFireAt": 1700000000, "scanned": {"zone:1429": True},
            "done": {"zone:1429": {"t": 1700000000, "how": "played"}, "area:150": {"t": 1700000050, "how": "played"},
                     "zone:1437": {"t": 1700000100, "how": "history"}},
            "journal": [{"id": "zone:1429", "t": 1700000000, "label": "Elwynn Forest", "title": "I'll Keep Coming",
                         "artist": "Low Roar"}]}


# ----------------------------------------------------------------- migration --

def test_v11_saved_variables_migrate_to_schema_2():
    h = ModelHarness(profile="model", db=v11_db(), char_db=v11_char())
    a, c = h.account_db(), h.char_db()
    assert a["schema"] == 2 and c["schema"] == 2
    assert h.call1("Soundtracks.MigratedFrom") == 1
    expected = dict(V11_SETTINGS, beaconCellPx=3)
    assert {k: a["settings"][k] for k in expected} == expected  # every setting kept, beaconCellPx 4 -> 3
    assert a["accountDone"] == v11_db()["accountDone"] and a["lastSeq"] == 12
    assert a["active"] == [MIX]  # v1.1 kept no list: everyone starts on the new default
    assert a["soundtracks"] in ({}, []) and a["importsHandled"] in ({}, [])
    assert c["done"] == v11_char()["done"] and c["journal"] == v11_char()["journal"]
    assert c["scanned"] == {"zone:1429": True} and c["lastFireAt"] == 1700000000
    assert "active" not in c  # nil = use the account list
    assert h.call1("Soundtracks.UsesCharacterList") is False


@pytest.mark.parametrize("stored, expected", [(1, 2), (2, 2), (3, 3), (4, 3), (5, 4), (8, 4), (16, 4)])
def test_beacon_cell_px_moves_to_3_and_is_clamped(stored, expected):
    h = ModelHarness(profile="model", db=v11_db(beaconCellPx=stored))
    assert h.account_db()["settings"]["beaconCellPx"] == expected


def test_migrate_account_on_a_raw_table():
    h = ModelHarness(profile="model")
    t = h.rt.execute("return { settings = { beaconCellPx = 0/0 } }")
    assert h.fn("Soundtracks.MigrateAccount")(t) == 1
    assert t.schema == 2 and t.settings.beaconCellPx == 3 and t.active[1] == MIX
    assert h.fn("Soundtracks.MigrateAccount")(t) is None  # already schema 2


def test_fresh_install():
    h = ModelHarness(profile="model")
    a = h.account_db()
    assert a["schema"] == 2 and a["active"] == [MIX] and a["settings"]["beaconCellPx"] == 3
    assert h.char_db()["schema"] == 2
    # Today's Core writes schema 1 into a fresh table, so the migration runs; once the
    # integrator sets ns.SCHEMA = 2 a fresh install starts at 2 and MigratedFrom() is nil.
    assert h.call1("Soundtracks.MigratedFrom") in (1, None)


def test_schema_2_data_is_not_migrated_again():
    db = {"schema": 2, "settings": {"beaconCellPx": 4}, "active": [UNB], "soundtracks": {}}
    h = ModelHarness(profile="model", db=db, char_db={"schema": 2})
    assert h.account_db()["settings"]["beaconCellPx"] == 4  # a 1.2 player's own choice
    assert h.account_db()["active"] == [UNB] and h.call1("Soundtracks.MigratedFrom") is None


def test_data_from_a_newer_version_is_read_but_not_rewritten():
    db = {"schema": 3, "active": ["user:future", "builtin:also-future"], "soundtracks": {"future": "opaque"},
          "settings": {}}
    h = ModelHarness(profile="model", db=db)
    assert h.get("Soundtracks.newerData") is True
    assert h.active() == [MIX]  # nothing valid: the default plays
    h.logout()
    a = h.account_db()
    assert a["schema"] == 3 and a["active"] == ["user:future", "builtin:also-future"]
    assert a["soundtracks"] == {"future": "opaque"}


def test_malformed_saved_data_is_repaired():
    db = {"schema": 2, "active": "not a list", "importsHandled": 5,
          "soundtracks": {"ok": {"name": 7, "author": False, "tags": "x", "revision": -3, "drops": "x"},
                          "Bad Id": {"id": "x", "name": "n", "drops": []}, "fine": "not a table"}}
    h = ModelHarness(profile="model", db=db, char_db={"schema": 2, "active": "nope"})
    a = h.account_db()
    assert a["active"] == [MIX] and a["importsHandled"] in ({}, [])
    assert set(a["soundtracks"]) == {"ok"}
    ok = a["soundtracks"]["ok"]
    assert ok["id"] == "ok" and ok["name"] == "ok" and ok["author"] == "" and ok["revision"] == 0
    assert ok["tags"] in ({}, []) and ok["drops"] in ({}, [])
    assert "active" not in h.char_db()


def test_welcome_line_names_the_soundtrack():
    h = ModelHarness(profile="model")
    assert h.call1("Soundtracks.WelcomeLine") == ("NeedleDropper 1.2: your soundtrack is Azeroth Mixtape. "
                                                  "Type /needle to change a song or pick another soundtrack.")
    h.call("Soundtracks.SetActive", [])
    assert "no soundtrack is on" in h.call1("Soundtracks.WelcomeLine")


# --------------------------------------------------------- history continuity --

def test_v11_map_ids_equal_azeroth_unbound_ids():
    h = ModelHarness(profile="model")
    unbound = h.call1("Sets.GetBuiltin", "azeroth-unbound")
    assert sorted(d["id"] for d in unbound["drops"]) == sorted(V11_MAP_IDS)
    assert len(V11_MAP_IDS) == 62
    path = ADDON_DIR / "Map.json"
    if path.is_file():  # still in the repo until the integrator deletes it
        m = json.loads(path.read_text(encoding="utf-8"))
        assert m["hash"] == 39761 and m["packs"] == ["Azeroth Unbound"]
        assert [d["id"] for d in m["drops"]] == V11_MAP_IDS


def test_heard_status_carries_over_by_place():
    h = ModelHarness(db=v11_db(scope="character"), char_db=v11_char())
    is_done = h.rt.execute("local ns, id = ... return ns.Director.IsDone(ns.Map.byId[id])", h.ns, "area:150")
    assert is_done is True  # Menethil Harbor: in both Unbound (v1.1) and Mixtape (1.2)
    for drop_id, heard in (("zone:1429", True), ("area:150", True), ("area:87", False)):
        got = h.rt.execute("local ns, id = ... return ns.Director.IsDone(ns.Map.byId[id])", h.ns, drop_id)
        assert got is heard, drop_id  # area:87 Goldshire is new in Mixtape: not heard yet
    assert h.map_drop("zone:1437") is None  # Wetlands is Unbound-only; its history waits there
    h.call("Soundtracks.SetActive", [UNB])
    h.rebuild()
    assert h.rt.execute("local ns, id = ... return ns.Director.IsDone(ns.Map.byId[id])", h.ns, "zone:1437") is True


# ------------------------------------------------------------- history rule --

def setup_user_drops(h):
    ref = h.call1("Soundtracks.Create", "Mine")
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "Toto", "title": "Africa"})  # overrides Mixtape
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1437, "artist": "Low Roar", "title": "Wetlands"})  # user-only
    h.rebuild()
    return ref


def test_history_applies_to_built_in_drops_only():
    h = ModelHarness(profile="model")
    setup_user_drops(h)
    applies = h.fn("Soundtracks.HistoryApplies")
    M = h.ns.Map
    assert applies(M.byId["zone:1429"]) is True
    assert applies(M.byId["zone:1436"]) is False and applies(M.byId["zone:1437"]) is False
    assert applies(h.to_lua({"id": "zone:1429"})) is True  # looked up in the map
    assert applies(h.to_lua({"id": "zone:1437"})) is False
    assert applies(h.to_lua({"id": "zone:1436", "builtin": True})) is True  # explicit flag wins
    assert applies(None) is False
    counts = h.fn("Soundtracks.DoneCounts")
    history, played = h.to_lua({"t": 1, "how": "history"}), h.to_lua({"t": 1, "how": "played"})
    assert counts(M.byId["zone:1429"], history) is True
    assert counts(M.byId["zone:1437"], history) is False  # an old history mark never hides a player's drop
    assert counts(M.byId["zone:1437"], played) is True  # really heard is heard
    assert counts(M.byId["zone:1437"], True) is True and counts(M.byId["zone:1437"], None) is False
    zones = [d["id"] for d in h.call1("Soundtracks.HistoryZoneDrops")]
    mix_zones = {d["id"] for d in h.call1("Sets.GetBuiltin", "azeroth-mixtape")["drops"] if d["kind"] == "zone"}
    assert set(zones) == mix_zones - {"zone:1436"}
    assert all(z.startswith("zone:") for z in zones)


def test_history_scan_built_on_the_helper_skips_user_drops():
    """What the integrator's Director change does with the helpers: the zone scan only marks
    built-in drops, and IsDone ignores history marks for the player's own drops."""
    h = ModelHarness(settings={"historyMode": "respect"}, char_db={"schema": 2, "done": {
        "zone:1437": {"t": 1, "how": "history"}}})
    setup_user_drops(h)
    h.set_state("exploredTextures", {1429: 5, 1436: 5, 1437: 5})
    marked = h.rt.execute("""
      local ns = ...
      local S, D = ns.Soundtracks, ns.Director
      local n = 0
      for _, d in ipairs(S.HistoryZoneDrops()) do
        if ns.Discovery.ExploredZone(d.mapID) and not ns.cdb.done[d.id] then D.MarkDone(d, "history"); n = n + 1 end
      end
      return n
    """, h.ns)
    done = h.char_db()["done"]
    assert "zone:1429" in done and done["zone:1429"]["how"] == "history"
    assert "zone:1436" not in done  # the player's Westfall drop stays unheard
    assert marked >= 1
    counts = h.rt.execute("local ns, id = ... return ns.Soundtracks.DoneCounts(ns.Map.byId[id], ns.cdb.done[id])",
                          h.ns, "zone:1437")
    assert counts is False  # the v1.1 history mark for Wetlands does not hide the new drop
