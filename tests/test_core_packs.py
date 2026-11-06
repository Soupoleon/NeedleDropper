"""packs.py: the pack file format behind share codes and the packs CLI.

(Until v1.1 this file also tested mapgen.py, which generated Map.lua / Map.json for the addon. v1.2
retired both: the addon ships Sets/*.lua from tools/gen_sets.py and every beacon frame carries its
song, so the companion keeps no map -- DESIGN-v1.2 1.3. Those tests went with the module.)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from needledropper import packs
from needledropper.models import Drop

SPOT = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"


def write_pack(path: Path, drops, name="Test Pack", **top) -> Path:
    data = {"format": "needledropper-pack", "version": 1, "name": name, "drops": drops, **top}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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
