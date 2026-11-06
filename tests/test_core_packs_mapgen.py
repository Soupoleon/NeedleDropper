"""packs.py and mapgen.py (incl. Map.lua under lupa.lua51)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from needledropper import mapgen, packs, protocol
from needledropper.config import Paths
from needledropper.models import Drop, Event, EventType, Flags

SPOT = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"


def write_pack(path: Path, drops, name="Test Pack", **top) -> Path:
    data = {"format": "needledropper-pack", "version": 1, "name": name, "drops": drops, **top}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_paths(tmp_path: Path) -> Paths:
    fl = tmp_path / "WoW" / "_forever_"
    ad = fl / "Interface" / "AddOns"
    app = tmp_path / "app"
    return Paths(wow_root=tmp_path / "WoW", flavor_root=fl, addons_dir=ad, addon_dir=ad / "NeedleDropper",
                 local_addon_dir=ad / "NeedleDropper_Local", chat_log=fl / "Logs" / "WoWChatLog.txt",
                 game_exe=fl / "Wow.exe", app_dir=app, cache_dir=app / "cache", maps_dir=app / "maps")


# ------------------------------------------------------------------ packs --

def test_pack_drop_ids_kinds_and_labels(tmp_path):
    p = write_pack(tmp_path / "a.json", [
        {"zone": "Elwynn Forest", "title": "I'll Keep Coming", "artist": "Low Roar", "spotify": SPOT,
         "durationMs": 245000, "startMs": 1000, "volume": 80, "note": "why"},
        {"zone": 1429, "subzone": "Goldshire", "areaID": 87, "title": "G", "artist": "A"},
        {"zone": "Elwynn Forest", "subzone": "Lion's Pride Inn", "title": "L", "artist": "A"},
        {"instanceID": 36, "label": "The Deadmines", "title": "D", "artist": "A"},
        {"zone": "Riverglades", "areaID": 16727, "title": "P", "artist": "A"},
    ])
    drops = packs.load_packs([p])
    by = {d.id: d for d in drops}
    assert set(by) == {"zone:1429", "area:87", "sub:1429:lion-s-pride-inn", "inst:36", "area:16727"}
    z = by["zone:1429"]
    assert (z.kind, z.label, z.mapID, z.spotify, z.durationMs, z.startMs, z.volume, z.note, z.pack) == \
        ("zone", "Elwynn Forest", 1429, SPOT, 245000, 1000, 80, "why", "Test Pack")
    assert by["area:87"].kind == "subzone" and by["area:87"].label == "Goldshire" and by["area:87"].mapID == 1429
    assert by["sub:1429:lion-s-pride-inn"].subzone == "Lion's Pride Inn"
    assert by["inst:36"].kind == "instance" and by["inst:36"].label == "The Deadmines"
    assert by["area:16727"].subzone == "Powderfuse Port"  # known area name fills in
    # n assigned by plain sorted id, from 1
    assert [d.id for d in drops] == sorted(by)
    assert [d.n for d in drops] == [1, 2, 3, 4, 5]


def test_pack_normalizes_links_and_in_game_file(tmp_path):
    p = write_pack(tmp_path / "a.json", [
        {"zone": "Durotar", "title": "T", "artist": "A",
         "spotify": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc",
         "youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5", "file": "sub/durotar.mp3", "inGameFile": True,
         "appleMusic": "https://music.apple.com/us/album/x/1?i=2"},
        {"zone": "Mulgore", "title": "T", "artist": "A", "youtube": "https://youtu.be/dQw4w9WgXcQ"},
    ])
    by = {d.id: d for d in packs.load_packs([p])}
    d = by["zone:1411"]
    assert d.spotify == SPOT and d.youtube == "dQw4w9WgXcQ" and d.file == "sub/durotar.mp3"
    assert d.inGameFile == "Interface\\AddOns\\NeedleDropper_Local\\media\\sub\\durotar.mp3"
    assert d.appleMusic.startswith("https://music.apple.com/")
    assert by["zone:1412"].youtube == "dQw4w9WgXcQ"
    # Map.json round trip keeps the in-game path as inGameFile
    entry = d.to_map_entry()
    assert entry["file"] == d.inGameFile
    assert Drop.from_dict(entry).inGameFile == d.inGameFile


def test_validation_errors_name_pack_and_index(tmp_path):
    p = write_pack(tmp_path / "bad.json", [
        {"zone": "Elwynn Forest", "title": "ok", "artist": "ok"},
        {"zone": "Elwyn Forrest", "title": "t", "artist": "a"},
        {"zone": "Durotar", "artist": "a"},
        {"title": "t", "artist": "a"},
        {"zone": "Durotar", "title": "t", "artist": "a", "spotify": "spotify:album:xyz", "volume": 300},
        {"subzone": "Goldshire", "title": "t", "artist": "a"},
        {"zone": "Durotar", "title": "t", "artist": "a", "inGameFile": True},
        "not an object",
        {"zone": "Mulgore", "title": "t", "artist": "a", "durationMs": -5, "youtube": "nope"},
    ], name="Broken")
    ps = packs.load_packs_detailed([p])
    assert not ps.ok
    msgs = [str(i) for i in ps.errors]
    joined = "\n".join(msgs)
    assert 'pack "Broken" drops[1]' in joined and "did you mean 'Elwynn Forest'" in joined
    assert any(m.startswith('pack "Broken" drops[2]') and 'missing "title"' in m for m in msgs)
    assert any(m.startswith('pack "Broken" drops[3]') and "needs a location" in m for m in msgs)
    assert any("drops[4]" in m and '"spotify"' in m for m in msgs)
    assert any("drops[4]" in m and '"volume"' in m for m in msgs)
    assert any("drops[5]" in m and 'needs a "zone"' in m for m in msgs)
    assert any("drops[6]" in m and "inGameFile" in m for m in msgs)
    assert any("drops[7]" in m and "JSON object" in m for m in msgs)
    assert any("drops[8]" in m and "durationMs" in m for m in msgs)
    assert any("drops[8]" in m and "youtube" in m for m in msgs)
    # the valid drop still loads
    assert [d.id for d in ps.drops] == ["zone:1429"]
    with pytest.raises(packs.PackError) as ei:
        packs.load_packs([p])
    assert "Broken" in str(ei.value)
    lines, ok = packs.validate_report(ps)
    assert not ok and any("FAILED" in ln for ln in lines)


def test_bad_json_and_missing_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "needledropper-pack", "drops": [,]}', encoding="utf-8")
    ps = packs.load_packs_detailed([bad, tmp_path / "missing.json"])
    msgs = [str(i) for i in ps.errors]
    assert any("invalid JSON at line 1" in m for m in msgs)
    assert any("not found" in m for m in msgs)
    wrong = write_pack(tmp_path / "w.json", [], format="something-else")
    assert any('"format"' in str(i) for i in packs.load_packs_detailed([wrong]).errors)


def test_unknown_keys_and_unknown_numeric_zone_warn(tmp_path):
    p = write_pack(tmp_path / "a.json", [{"zone": 4242, "title": "t", "artist": "a", "spotfy": "x"}])
    ps = packs.load_packs_detailed([p])
    assert ps.ok
    warns = " ".join(str(w) for w in ps.warnings)
    assert "'spotfy'" in warns and "4242" in warns
    assert ps.drops[0].id == "zone:4242" and ps.drops[0].extra == {"spotfy": "x"}


def test_merge_later_pack_overrides(tmp_path):
    a = write_pack(tmp_path / "a.json", [
        {"zone": "Elwynn Forest", "title": "Old", "artist": "A", "spotify": SPOT},
        {"zone": "Durotar", "title": "Dur", "artist": "A"},
    ], name="A")
    b = write_pack(tmp_path / "b.json", [
        {"zone": 1429, "title": "New", "artist": "B"},
        {"zone": "Westfall", "title": "W", "artist": "B"},
    ], name="B")
    ps = packs.load_packs_detailed([a, b])
    by = ps.by_id()
    assert by["zone:1429"].title == "New" and by["zone:1429"].spotify is None and by["zone:1429"].pack == "B"
    assert set(by) == {"zone:1429", "zone:1411", "zone:1436"}
    assert [d.n for d in ps.drops] == [1, 2, 3]
    assert ps.names == ["A", "B"]
    # reversed order: A wins
    assert packs.load_packs_detailed([b, a]).by_id()["zone:1429"].title == "Old"


def test_duplicate_in_one_pack_warns_and_later_wins(tmp_path):
    p = write_pack(tmp_path / "a.json", [
        {"zone": "Durotar", "title": "first", "artist": "A"},
        {"zone": 1411, "title": "second", "artist": "A"},
    ])
    ps = packs.load_packs_detailed([p])
    assert ps.ok and len(ps.drops) == 1 and ps.drops[0].title == "second"
    assert any("duplicate" in str(w) for w in ps.warnings)


def test_resolved_cache_fills_missing_fields_only(tmp_path):
    p = write_pack(tmp_path / "a.json", [
        {"zone": "Durotar", "title": "T", "artist": "A"},
        {"zone": "Mulgore", "title": "T2", "artist": "A", "spotify": SPOT, "durationMs": 1000},
    ])
    cache = tmp_path / "resolved.json"
    other = "spotify:track:0000000000000000000000"
    cache.write_text(json.dumps({
        "zone:1411": {"spotify": SPOT, "durationMs": 200000, "artUrl": "https://i.scdn.co/image/x"},
        "zone:1412": {"spotify": other, "durationMs": 5},
        "zone:9999": {"spotify": SPOT},
    }), encoding="utf-8")
    by = {d.id: d for d in packs.load_packs([p], resolved_cache=cache)}
    assert by["zone:1411"].spotify == SPOT and by["zone:1411"].durationMs == 200000
    assert by["zone:1411"].extra["artUrl"] == "https://i.scdn.co/image/x"
    assert by["zone:1412"].spotify == SPOT and by["zone:1412"].durationMs == 1000  # pack wins
    assert "zone:9999" not in by
    # missing/garbage cache is ignored
    (tmp_path / "junk.json").write_text("nope", encoding="utf-8")
    assert len(packs.load_packs([p], resolved_cache=tmp_path / "junk.json")) == 2


def test_find_drop(tmp_path):
    p = write_pack(tmp_path / "a.json", [
        {"zone": "Elwynn Forest", "title": "T", "artist": "A"},
        {"zone": 1429, "subzone": "Goldshire", "areaID": 87, "title": "G", "artist": "A"},
        {"instanceID": 36, "label": "The Deadmines", "title": "D", "artist": "A"},
    ])
    drops = packs.load_packs([p])
    assert packs.find_drop(drops, "zone:1429").id == "zone:1429"
    assert packs.find_drop(drops, "elwynn").id == "zone:1429"
    assert packs.find_drop(drops, "Elwynn Forest").id == "zone:1429"
    assert packs.find_drop(drops, "goldshire").id == "area:87"
    assert packs.find_drop(drops, "deadmines").id == "inst:36"
    assert packs.find_drop(drops, "Durotar") is None


# ----------------------------------------------------------------- mapgen --

TRICKY = 'Quote " back\\slash\nnew line\ttab éè 日本 \x01'


def tricky_drops(tmp_path):
    p = write_pack(tmp_path / "t.json", [
        {"zone": "Elwynn Forest", "title": TRICKY, "artist": 'Sigur Rós "x"', "durationMs": 245000,
         "startMs": 5, "spotify": SPOT},
        {"zone": 1429, "subzone": "Lion's Pride Inn", "title": "end]]", "artist": "--[[ c", "file": "a.mp3",
         "inGameFile": True},
        {"instanceID": 36, "label": "The Deadmines", "title": "D", "artist": "A"},
        {"zone": "Riverglades", "areaID": 16727, "title": "P", "artist": "A"},
    ], name='Pack "One"')
    return packs.load_packs_detailed([p])


def lua_compile(rt, src):
    """(chunk, err) from Lua's loadstring."""
    loader = rt.execute("return function(s) local f, e = loadstring(s, '=Map.lua'); return {f, e} end")
    res = loader(src)
    return res[1], res[2]


def lua_to_py(rt, v):
    lua_type = rt.globals().type(v)
    if lua_type != "table":
        return v
    keys = list(v.keys())
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [lua_to_py(rt, v[i]) for i in range(1, len(keys) + 1)]
    if not keys:
        return []
    return {k: lua_to_py(rt, v[k]) for k in keys}


def test_map_lua_compiles_and_equals_json(tmp_path):
    lua51 = pytest.importorskip("lupa.lua51")
    ps = tricky_drops(tmp_path)
    assert ps.ok, [str(i) for i in ps.issues]
    m = mapgen.build_map(ps.drops, ps.names, generated="2026-11-05T18:00:00Z")
    res = mapgen.write_map(m, tmp_path / "addon", maps_dir=tmp_path / "maps")
    src = res.lua_path.read_text(encoding="utf-8")
    assert src.startswith("-- Generated by NeedleDropper companion ")
    assert "Edit packs, not this file." in src.splitlines()[0]
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    chunk, err = lua_compile(rt, src)
    assert chunk is not None, err
    chunk()
    got = lua_to_py(rt, rt.globals().NeedleDropper_Map)
    js = json.loads(res.json_path.read_text(encoding="utf-8"))
    assert got == js
    assert js == m
    titles = {d["id"]: d["title"] for d in js["drops"]}
    assert titles["zone:1429"] == TRICKY
    files = {d["id"]: d.get("file") for d in js["drops"]}
    assert files["sub:1429:lion-s-pride-inn"] == "Interface\\AddOns\\NeedleDropper_Local\\media\\a.mp3"
    # registry copy
    assert res.registry_path == tmp_path / "maps" / f"{m['hash']}.json"
    assert json.loads(res.registry_path.read_text(encoding="utf-8")) == js
    # hash is the protocol hash of the entries
    assert m["hash"] == protocol.map_hash(m["drops"])
    assert 1 <= m["hash"] <= 0xFFFF


def test_lua_escaping():
    assert mapgen.lua_string('a"b') == '"a\\"b"'
    assert mapgen.lua_string("a\\b") == '"a\\\\b"'
    assert mapgen.lua_string("a\nb\r") == '"a\\nb\\r"'
    assert mapgen.lua_string("\x00\x7f") == '"\\000\\127"'
    assert mapgen.lua_value({"end": 1, "ok": [1, "x"], "skip": None}) == '{ ["end"] = 1, ok = { 1, "x" } }'


def test_hash_stable_and_deterministic(tmp_path):
    ps = tricky_drops(tmp_path)
    m1 = mapgen.build_map(ps.drops, ps.names, generated="2026-01-01T00:00:00Z")
    m2 = mapgen.build_map(list(reversed(ps.drops)), ps.names, generated="2026-01-01T00:00:00Z")
    assert m1 == m2
    assert mapgen.render_map_lua(m1) == mapgen.render_map_lua(m2)
    m3 = mapgen.build_map(ps.drops, ps.names, generated="2030-01-01T00:00:00Z")
    assert m3["hash"] == m1["hash"]  # time does not affect the hash
    changed = [d if d.id != "inst:36" else Drop(**{**d.__dict__, "title": "Other"}) for d in ps.drops]
    assert mapgen.build_map(changed, ps.names)["hash"] != m1["hash"]


def test_build_map_rejects_unnumbered():
    with pytest.raises(ValueError):
        mapgen.build_map([Drop(n=0, id="zone:1", kind="zone", title="t", artist="a")])


def test_large_map_compiles(tmp_path):
    lua51 = pytest.importorskip("lupa.lua51")
    drops = packs.assign_numbers([Drop(n=0, id=f"zone:{i}", kind="zone", title=f"T{i}", artist="A", mapID=i,
                                       label=f"Zone {i}") for i in range(1, 3001)])
    src = mapgen.render_map_lua(mapgen.build_map(drops, ["big"]))
    rt = lua51.LuaRuntime()
    chunk, err = lua_compile(rt, src)
    assert chunk is not None, err
    chunk()
    assert len(rt.globals().NeedleDropper_Map.drops) == 3000


def test_write_map_retries_locked_file(tmp_path, monkeypatch):
    from needledropper import config as cfgmod

    calls = {"n": 0}
    real = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("Map.lua is locked")
        return real(src, dst)

    monkeypatch.setattr(cfgmod.os, "replace", flaky)
    monkeypatch.setattr(cfgmod.time, "sleep", lambda s: None)
    drops = packs.assign_numbers([Drop(n=0, id="zone:1429", kind="zone", title="t", artist="a", mapID=1429)])
    res = mapgen.write_map(mapgen.build_map(drops, ["p"]), tmp_path)
    assert res.lua_path.exists() and res.json_path.exists() and res.registry_path is None
    assert sorted(x.name for x in tmp_path.iterdir()) == ["Map.json", "Map.lua"]


def test_load_map_json_validation(tmp_path):
    p = tmp_path / "Map.json"
    p.write_text('{"drops": []}', encoding="utf-8")
    with pytest.raises(mapgen.MapError, match="hash"):
        mapgen.load_map_json(p)
    with pytest.raises(mapgen.MapError, match="not found"):
        mapgen.load_map_json(tmp_path / "nope.json")


# --------------------------------------------------------------- registry --

def _pack_drops(tmp_path, title="Song"):
    p = write_pack(tmp_path / f"p-{title}.json", [
        {"zone": "Elwynn Forest", "title": title, "artist": "Artist", "spotify": SPOT, "youtube": "dQw4w9WgXcQ",
         "file": "elwynn.mp3", "volume": 70, "appleMusic": "https://music.apple.com/x"},
        {"zone": "Durotar", "title": "D", "artist": "A"},
    ])
    return packs.load_packs([p])


def test_registry_resolves_installed_map_and_enriches(tmp_path):
    paths = make_paths(tmp_path)
    drops = _pack_drops(tmp_path)
    m = mapgen.build_map(drops, ["p"])
    mapgen.write_map(m, paths.addon_dir, maps_dir=paths.maps_dir)
    # Map.json itself carries only map fields
    entry = next(e for e in m["drops"] if e["id"] == "zone:1429")
    assert "spotify" not in entry
    reg = mapgen.MapRegistry(paths, drops)
    assert reg.installed_hash == m["hash"]
    d = reg.resolve(m["hash"], entry["n"])
    assert d.id == "zone:1429" and d.title == "Song"
    assert (d.spotify, d.youtube, d.file, d.volume, d.appleMusic) == (SPOT, "dQw4w9WgXcQ", "elwynn.mp3", 70,
                                                                     "https://music.apple.com/x")
    assert reg.resolve(m["hash"], 999) is None
    assert reg.resolve(12345 if m["hash"] != 12345 else 12346, 1) is None


def test_registry_old_hash_from_maps_dir_and_no_enrich_when_song_changed(tmp_path):
    paths = make_paths(tmp_path)
    old = _pack_drops(tmp_path, "Old Song")
    old_map = mapgen.build_map(old, ["p"])
    mapgen.write_map(old_map, paths.addon_dir, maps_dir=paths.maps_dir)
    new = _pack_drops(tmp_path, "New Song")
    new_map = mapgen.build_map(new, ["p"])
    assert new_map["hash"] != old_map["hash"]
    mapgen.write_map(new_map, paths.addon_dir, maps_dir=paths.maps_dir)  # installed is now the new map
    reg = mapgen.MapRegistry(paths, new)
    n = next(e["n"] for e in old_map["drops"] if e["id"] == "zone:1429")
    d_old = reg.resolve(old_map["hash"], n)  # addon not /reloaded yet: still the old map
    assert d_old.title == "Old Song" and d_old.spotify is None  # pack has another song for this id now
    d_new = reg.resolve(new_map["hash"], n)
    assert d_new.title == "New Song" and d_new.spotify == SPOT
    assert set(reg.known_hashes()) >= {old_map["hash"], new_map["hash"]}


def test_registry_falls_back_to_pack_map_and_event_id(tmp_path):
    paths = make_paths(tmp_path)  # nothing installed
    drops = _pack_drops(tmp_path)
    reg = mapgen.MapRegistry(paths, drops)
    h = reg.pack_hash
    d = reg.resolve_event(Event(EventType.FIRE, 1, 2, h, Flags.NONE))
    assert d is not None and d.id == drops[1].id
    # unknown hash, but the chat channel carried the id
    d2 = reg.resolve_event(Event(EventType.FIRE, 1, 9, (h + 1) % 65536 or 1, Flags.NONE, "zone:1429"))
    assert d2 is not None and d2.id == "zone:1429"
    # n=0 + TEST -> test drop
    t = reg.resolve_event(Event(EventType.FIRE, 1, 0, h, Flags.TEST))
    assert t.id == "test" and t.n == 0
    assert "build-map" in reg.describe_miss(Event(EventType.FIRE, 1, 1, (h + 7) % 65536 or 1))
    assert reg.find("Elwynn Forest").id == "zone:1429"
    assert reg.find("test").id == "test"


def test_registry_from_config_reloads_packs(tmp_path):
    from needledropper import config as cfgmod

    paths = make_paths(tmp_path)
    pack = write_pack(tmp_path / "live.json", [{"zone": "Durotar", "title": "One", "artist": "A"}])
    cfg = cfgmod.deep_merge(cfgmod.DEFAULT_CONFIG, {"packs": [str(pack)]})
    reg = mapgen.MapRegistry.from_config(cfg, paths)
    reg._refresh_interval = 0
    assert reg.find("Durotar").title == "One"
    write_pack(pack, [{"zone": "Durotar", "title": "Two", "artist": "A"}, {"zone": "Mulgore", "title": "M",
                                                                         "artist": "A"}])
    os.utime(pack, ns=(1, 10**18))
    assert reg.find("Durotar").title == "Two"
    assert reg.find("Mulgore") is not None


def test_configured_test_drop():
    d = mapgen.configured_test_drop({"testDrop": {"title": "X", "artist": "Y", "spotify": SPOT, "durationMs": 5}})
    assert (d.id, d.n, d.title, d.artist, d.spotify, d.durationMs) == ("test", 0, "X", "Y", SPOT, 5)
    assert mapgen.configured_test_drop({}).id == "test"
