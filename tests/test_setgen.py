"""Tests for ``tools/gen_sets.py``: soundtrack pack JSON -> bundled set Lua files.

The contract is docs/SETS-FORMAT.md. Covered here:

* the shipped ``addon/NeedleDropper/Sets/*.lua`` files compile under ``lupa.lua51``, run in an empty
  environment against a stub ``ns.RegisterBuiltinSet`` (one call, no globals touched), are up to date
  with their packs, and carry exactly the pack's data (checked against an independent oracle and,
  when importable, against the companion's own pack parser);
* drop ids / kinds / uiMapIDs derive like the companion; Spotify and YouTube links are normalised;
* determinism (input order, key order, current directory), Lua escaping and UTF-8 down to the bytes;
* invalid packs fail with the pack file, pack name and drop index; warnings never fail;
* the CLI: write / ``--check`` cycle, CRLF tolerance, stale and foreign files, per-pack independence,
  and a real subprocess run (including a cp1252 pipe).

Everything is hermetic: tmp dirs only, no network.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

lua51 = pytest.importorskip("lupa.lua51")

ROOT = Path(__file__).resolve().parents[1]
GEN_PATH = ROOT / "tools" / "gen_sets.py"
SETS_DIR = ROOT / "addon" / "NeedleDropper" / "Sets"
UNBOUND_JSON = ROOT / "packs" / "azeroth-unbound.json"
UNBOUND_LUA = SETS_DIR / "AzerothUnbound.lua"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_sets", GEN_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_sets"] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()
from needledropper import zones  # noqa: E402  (conftest puts companion/ on sys.path)

SET_KEYS = {"id", "name", "author", "description", "tags", "version", "source", "homepage", "drops"}
SET_REQUIRED = SET_KEYS - {"homepage"}
DROP_KEYS = {"id", "kind", "mapID", "areaID", "subzone", "instanceID", "label", "title", "artist",
             "durationMs", "startMs", "youtube", "spotify", "tags", "note"}
DROP_REQUIRED = {"id", "kind", "label", "title", "artist", "durationMs", "startMs"}
SOURCE = "packs/test-set.json"


# ------------------------------------------------------------------ helpers --

_RUNNER = """
return function(src, chunkname)
  local fn, err = loadstring(src, chunkname)
  if not fn then return { ok = false, err = err } end
  local captured, calls = nil, 0
  local ns = {}
  ns.RegisterBuiltinSet = function(t) calls = calls + 1; captured = t end
  local env = {}
  setfenv(fn, env)   -- no globals at all: the file may only use its (ADDON, ns) varargs
  local ok, perr = pcall(fn, "NeedleDropper", ns)
  if not ok then return { ok = false, err = perr } end
  local leaked, nsKeys = {}, {}
  for k in pairs(env) do leaked[#leaked + 1] = tostring(k) end
  for k in pairs(ns) do nsKeys[#nsKeys + 1] = tostring(k) end
  return { ok = true, set = captured, calls = calls, leaked = leaked, nsKeys = nsKeys }
end
"""


def to_py(v: Any) -> Any:
    """Lua value -> Python: sequences become lists (``{}`` -> ``[]``), other tables dicts."""
    if lua51.lua_type(v) != "table":
        return v
    keys = list(v.keys())
    if not keys:
        return []
    if all(isinstance(k, int) and not isinstance(k, bool) for k in keys) and \
            sorted(keys) == list(range(1, len(keys) + 1)):
        return [to_py(v[i]) for i in range(1, len(keys) + 1)]
    return {(k.decode("utf-8") if isinstance(k, bytes) else k): to_py(v[k]) for k in keys}


def run_set_lua(src: str, *, raw: bool = False) -> dict:
    """Load a set file the way the client does (``(ADDON, ns)`` varargs) with a capturing stub.

    ``raw=True`` keeps Lua strings as bytes (to check the exact UTF-8 bytes)."""
    rt = lua51.LuaRuntime(unpack_returned_tuples=True, encoding=None if raw else "UTF-8")
    if raw:
        res = to_py(rt.execute(_RUNNER.encode())(src.encode("utf-8"), b"=set"))
        res = {k: v for k, v in res.items()}
    else:
        res = to_py(rt.execute(_RUNNER)(src, "=set"))
    err = res.get("err")
    assert res["ok"], f"set file failed to load: {err!r}"
    assert res["calls"] == 1, "a set file must call ns.RegisterBuiltinSet exactly once"
    assert res["leaked"] == [], f"set file assigned globals: {res['leaked']}"
    assert res["nsKeys"] in (["RegisterBuiltinSet"], [b"RegisterBuiltinSet"]), "set file modified ns"
    return res["set"]


def base_pack() -> dict:
    return {
        "format": "needledropper-pack", "version": 1, "name": "Test Set", "author": "Tester",
        "description": "A test.",
        "drops": [
            {"zone": "Elwynn Forest", "title": "I'll Keep Coming", "artist": "Low Roar",
             "youtube": "mlM66FzjpgM", "durationMs": 352000},
            {"zone": "Elwynn Forest", "subzone": "Goldshire", "areaID": 87, "title": "Easy Way Out",
             "artist": "Low Roar"},
        ],
    }


def build(data: Any, source: str = SOURCE):
    return gen.build_set(data, source=source, name_hint=Path(source).stem)


def build_errors(data: Any) -> list[str]:
    with pytest.raises(gen.SetGenError) as ei:
        build(data)
    return [str(i) for i in ei.value.errors]


def roundtrip(data: Any) -> dict:
    """pack data -> set file text -> run in Lua -> captured table."""
    return run_set_lua(gen.render_lua(build(data)))


def write_pack(folder: Path, name: str, data: Any) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def generated_files() -> list[Path]:
    return sorted(p for p in SETS_DIR.glob("*.lua") if gen.generated_source(p))


# ------------------------------------------------------ shipped set files --

def test_azeroth_unbound_set_is_shipped():
    assert UNBOUND_LUA.is_file(), "run: python tools/gen_sets.py"
    assert gen.generated_source(UNBOUND_LUA) == "packs/azeroth-unbound.json"


@pytest.mark.parametrize("path", generated_files(), ids=lambda p: p.name)
def test_shipped_set_is_up_to_date(path):
    source = gen.generated_source(path)
    pack = ROOT / source
    assert pack.is_file(), f"{path.name} was generated from {source}, which no longer exists"
    s = gen.load_set(pack)
    assert path.name == s.filename
    on_disk = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert on_disk == gen.render_lua(s), f"{path.name} is out of date: run python tools/gen_sets.py"


@pytest.mark.parametrize("path", sorted(SETS_DIR.glob("*.lua")), ids=lambda p: p.name)
def test_shipped_set_file_is_valid_lua51_data(path):
    assert gen.generated_source(path), (
        f"{path.name}: addon/NeedleDropper/Sets/ holds only files generated by tools/gen_sets.py "
        "(docs/SETS-FORMAT.md); put the Sets module beside the folder, e.g. addon/NeedleDropper/Sets.lua")
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "a BOM breaks loadstring and the client"
    src = raw.decode("utf-8")
    lines = src.splitlines()
    assert lines[0] == "local ADDON, ns = ...", "SPEC 4: every addon file starts with the varargs line"
    assert gen.HEADER_RE.fullmatch(lines[1]), "second line must be the 'Generated by' header"
    t = run_set_lua(src)
    assert set(t) <= SET_KEYS and SET_REQUIRED <= set(t)
    assert t["version"] == 1
    assert re.fullmatch(r"[a-z0-9-]{1,40}", t["id"]) and path.name == gen.camel_case(t["id"]) + ".lua"
    assert isinstance(t["tags"], list) and all(isinstance(x, str) for x in t["tags"])
    ids = [d["id"] for d in t["drops"]]
    assert ids == sorted(ids) and len(ids) == len(set(ids)), "drops must be unique and sorted by id"
    for d in t["drops"]:
        assert set(d) <= DROP_KEYS and DROP_REQUIRED <= set(d), d
        assert d["kind"] in ("zone", "subzone", "instance")
        assert isinstance(d["durationMs"], int) and isinstance(d["startMs"], int)
        if "spotify" in d:
            assert re.fullmatch(r"[A-Za-z0-9]{22}", d["spotify"])
        if "youtube" in d:
            assert re.fullmatch(r"[A-Za-z0-9_-]{11}", d["youtube"])


def _unbound_oracle() -> list[dict]:
    """What AzerothUnbound.lua must contain, built straight from the JSON (every landmark in
    this pack has a zone name and an areaID; ids follow SPEC 3.1 by hand)."""
    raw = json.loads(UNBOUND_JSON.read_text(encoding="utf-8-sig"))
    out = []
    for r in raw["drops"]:
        if r.get("disabled"):
            continue
        z = zones.lookup(r["zone"])
        assert z is not None, r["zone"]
        if "areaID" in r:
            d = {"id": f"area:{r['areaID']}", "kind": "subzone", "mapID": z.id, "areaID": r["areaID"],
                 "subzone": r["subzone"], "label": r.get("label") or r["subzone"]}
        else:
            d = {"id": f"zone:{z.id}", "kind": "zone", "mapID": z.id, "label": r.get("label") or z.name}
        d.update(title=r["title"], artist=r["artist"], durationMs=r.get("durationMs", 0), startMs=r.get("startMs", 0))
        if r.get("youtube"):
            d["youtube"] = r["youtube"]
        if r.get("spotify"):
            d["spotify"] = gen.normalize_spotify_id(r["spotify"])
        if r.get("tags"):
            d["tags"] = r["tags"]
        if r.get("note"):
            d["note"] = r["note"]
        out.append(d)
    return sorted(out, key=lambda d: d["id"])


def test_azeroth_unbound_carries_the_pack_data():
    t = run_set_lua(UNBOUND_LUA.read_text(encoding="utf-8"))
    raw = json.loads(UNBOUND_JSON.read_text(encoding="utf-8-sig"))
    assert t["id"] == "azeroth-unbound" and t["name"] == raw["name"]
    assert t["author"] == raw["author"] and t["description"] == raw["description"]
    assert t["source"] == "packs/azeroth-unbound.json"
    assert t["drops"] == _unbound_oracle()
    by_id = {d["id"]: d for d in t["drops"]}
    elwynn = by_id["zone:1429"]
    assert (elwynn["label"], elwynn["title"], elwynn["artist"], elwynn["durationMs"], elwynn["youtube"]) == \
        ("Elwynn Forest", "I'll Keep Coming", "Low Roar", 352000, "mlM66FzjpgM")
    assert by_id["area:279"]["mapID"] == 1416 and by_id["area:279"]["subzone"] == "Dalaran"
    assert by_id["zone:1447"]["youtube"] == "iQnRCdtECl8", "Azshara gained a YouTube id in the 2026-11-05 pack audit"
    assert by_id["zone:2548"]["artist"] == "Ólafur Arnalds"


def test_azeroth_unbound_agrees_with_the_companion_parser():
    try:
        from needledropper import packs as companion_packs
    except Exception as exc:  # the companion may be mid-refactor (direction change); this is a cross-check
        pytest.skip(f"companion packs module unavailable: {exc}")
    pack = companion_packs.load_pack(UNBOUND_JSON)
    assert pack.ok, [str(i) for i in pack.issues]
    want = {d.id: d for d in pack.drops}
    got = run_set_lua(UNBOUND_LUA.read_text(encoding="utf-8"))["drops"]
    assert [d["id"] for d in got] == sorted(want)
    for d in got:
        c = want[d["id"]]
        assert d["kind"] == c.kind
        assert (d.get("mapID"), d.get("areaID"), d.get("subzone"), d.get("instanceID")) == \
            (c.mapID, c.areaID, c.subzone, c.instanceID)
        assert (d["label"], d["title"], d["artist"]) == (c.display_label, c.title, c.artist)
        assert (d["durationMs"], d["startMs"]) == (c.durationMs, c.startMs)
        assert d.get("youtube") == c.youtube
        assert d.get("spotify") == (c.spotify.rsplit(":", 1)[-1] if c.spotify else None)
        assert d.get("note", "") == c.note.strip()


def test_cli_check_passes_for_the_shipped_set(tmp_path):
    """The CI contract, as a real process started from another directory."""
    proc = subprocess.run([sys.executable, str(GEN_PATH), "--check", str(UNBOUND_JSON)], cwd=tmp_path,
                          capture_output=True, timeout=120)
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, out
    assert "up to date: addon/NeedleDropper/Sets/AzerothUnbound.lua" in out


# ----------------------------------------------------- the generated format --

def test_exact_output_format():
    """Pins the bytes the in-game Sets module consumes (docs/SETS-FORMAT.md)."""
    pack = {
        "format": "needledropper-pack", "version": 1, "id": "tiny", "name": "Tiny", "author": "Me",
        "description": "Small.", "tags": ["kojima"], "homepage": "https://example.com/tiny",
        "drops": [
            {"zone": "Elwynn Forest", "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 352000,
             "youtube": "https://youtu.be/mlM66FzjpgM", "spotify": "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
             "tags": ["fitting"], "note": "Why."},
            {"zone": "Elwynn Forest", "subzone": "Goldshire", "areaID": 87, "title": "T2", "artist": "A2",
             "tags": ["funny"]},
        ],
    }
    assert gen.render_lua(build(pack, "packs/tiny.json")) == (
        'local ADDON, ns = ...\n'
        '-- Generated by tools/gen_sets.py from packs/tiny.json. Edit the pack, not this file.\n'
        'ns.RegisterBuiltinSet({\n'
        '  id = "tiny", name = "Tiny", author = "Me", description = "Small.", tags = { "kojima" }, version = 1, '
        'source = "packs/tiny.json", homepage = "https://example.com/tiny",\n'
        '  drops = {\n'
        '    { id = "area:87", kind = "subzone", mapID = 1429, areaID = 87, subzone = "Goldshire", '
        'label = "Goldshire", title = "T2", artist = "A2", durationMs = 0, startMs = 0, tags = { "funny" } },\n'
        '    { id = "zone:1429", kind = "zone", mapID = 1429, label = "Elwynn Forest", title = "I\'ll Keep Coming", '
        'artist = "Low Roar", durationMs = 352000, startMs = 0, youtube = "mlM66FzjpgM", '
        'spotify = "4uLU6hMCjMI75M1A2tKUQC", tags = { "fitting" }, note = "Why." },\n'
        '  },\n'
        '})\n'
    )


def test_set_header_defaults():
    t = roundtrip({"format": "needledropper-pack", "version": 1, "name": "Azeroth Unbound",
                   "drops": [{"zone": 1429, "title": "T", "artist": "A"}]})
    assert t["id"] == "azeroth-unbound"
    assert (t["author"], t["description"], t["tags"]) == ("", "", [])
    assert "homepage" not in t
    assert t["source"] == SOURCE


def test_ids_kinds_and_labels_like_the_companion():
    pack = base_pack()
    pack["drops"] = [
        {"zone": "Elwynn Forest", "title": "a", "artist": "x"},
        {"zone": 1453, "title": "b", "artist": "x"},
        {"zone": "1411", "title": "c", "artist": "x"},
        {"zone": "the barrens", "title": "d", "artist": "x"},
        {"zone": "STV", "title": "e", "artist": "x"},
        {"zone": "Un'Goro", "title": "f", "artist": "x"},
        {"zone": "zone:1440", "title": "g", "artist": "x"},
        {"zone": "Elwynn Forest", "subzone": "Lion's Pride Inn", "title": "h", "artist": "x"},
        {"zone": "Elwynn", "subzone": "Goldshire", "areaID": 87, "title": "i", "artist": "x"},
        {"areaID": 279, "title": "j", "artist": "x"},
        {"areaID": 99999, "title": "k", "artist": "x"},
        {"instanceID": 36, "label": "The Deadmines", "title": "l", "artist": "x"},
        {"zone": "Ashenvale", "instanceID": 48, "label": "Blackfathom Deeps", "title": "m", "artist": "x"},
    ]
    got = {d["id"]: d for d in roundtrip(pack)["drops"]}
    common = {"artist": "x", "durationMs": 0, "startMs": 0}
    assert got == {
        "zone:1429": {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "title": "a", **common},
        "zone:1453": {"id": "zone:1453", "kind": "zone", "mapID": 1453, "label": "Stormwind City", "title": "b", **common},
        "zone:1411": {"id": "zone:1411", "kind": "zone", "mapID": 1411, "label": "Durotar", "title": "c", **common},
        "zone:1413": {"id": "zone:1413", "kind": "zone", "mapID": 1413, "label": "The Barrens", "title": "d", **common},
        "zone:1434": {"id": "zone:1434", "kind": "zone", "mapID": 1434, "label": "Stranglethorn Vale", "title": "e", **common},
        "zone:1449": {"id": "zone:1449", "kind": "zone", "mapID": 1449, "label": "Un'Goro Crater", "title": "f", **common},
        "zone:1440": {"id": "zone:1440", "kind": "zone", "mapID": 1440, "label": "Ashenvale", "title": "g", **common},
        "sub:1429:lion-s-pride-inn": {"id": "sub:1429:lion-s-pride-inn", "kind": "subzone", "mapID": 1429,
                                      "subzone": "Lion's Pride Inn", "label": "Lion's Pride Inn", "title": "h", **common},
        "area:87": {"id": "area:87", "kind": "subzone", "mapID": 1429, "areaID": 87, "subzone": "Goldshire",
                    "label": "Goldshire", "title": "i", **common},
        "area:279": {"id": "area:279", "kind": "subzone", "mapID": 1416, "areaID": 279, "subzone": "Dalaran",
                     "label": "Dalaran", "title": "j", **common},
        "area:99999": {"id": "area:99999", "kind": "subzone", "areaID": 99999, "label": "Area 99999", "title": "k",
                       **common},
        "inst:36": {"id": "inst:36", "kind": "instance", "instanceID": 36, "label": "The Deadmines", "title": "l",
                    **common},
        "inst:48": {"id": "inst:48", "kind": "instance", "mapID": 1440, "instanceID": 48,
                    "label": "Blackfathom Deeps", "title": "m", **common},
    }


def test_ids_match_the_companion_parser_on_a_mixed_pack():
    try:
        from needledropper import packs as companion_packs
    except Exception as exc:
        pytest.skip(f"companion packs module unavailable: {exc}")
    pack = base_pack()
    pack["drops"] += [
        {"zone": "1411", "title": "c", "artist": "x"},
        {"zone": "STV", "subzone": "Booty Bay", "title": "d", "artist": "x"},
        {"areaID": 279, "title": "j", "artist": "x"},
        {"instanceID": 36, "label": "The Deadmines", "title": "l", "artist": "x"},
    ]
    theirs = companion_packs.parse_pack(copy.deepcopy(pack), name_hint="test-set")
    assert theirs.ok
    want = {d.id: (d.kind, d.mapID, d.areaID, d.subzone, d.instanceID, d.display_label) for d in theirs.drops}
    got = {d["id"]: (d["kind"], d.get("mapID"), d.get("areaID"), d.get("subzone"), d.get("instanceID"), d["label"])
           for d in build(pack).drops}
    assert got == want


def test_drops_are_sorted_by_plain_string_order():
    pack = base_pack()
    pack["drops"] = [{"areaID": a, "zone": 1429, "title": "t", "artist": "a"} for a in (87, 1000, 9, 279)]
    pack["drops"] += [{"zone": 1411, "title": "t", "artist": "a"}, {"instanceID": 5, "label": "x", "title": "t", "artist": "a"}]
    ids = [d["id"] for d in roundtrip(pack)["drops"]]
    assert ids == ["area:1000", "area:279", "area:87", "area:9", "inst:5", "zone:1411"]


# ------------------------------------------------------------ normalisation --

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"


@pytest.mark.parametrize("value", [
    SPOTIFY_ID,
    f"  {SPOTIFY_ID}  ",
    f"spotify:track:{SPOTIFY_ID}",
    f"https://open.spotify.com/track/{SPOTIFY_ID}",
    f"https://open.spotify.com/track/{SPOTIFY_ID}?si=0a1b2c3d4e5f",
    f"https://open.spotify.com/intl-de/track/{SPOTIFY_ID}?si=x&context=y",
    f"http://open.spotify.com/track/{SPOTIFY_ID}/",
])
def test_spotify_links_normalise_to_the_bare_id(value):
    assert gen.normalize_spotify_id(value) == SPOTIFY_ID


@pytest.mark.parametrize("value", [
    f"spotify:album:{SPOTIFY_ID}",
    f"https://open.spotify.com/album/{SPOTIFY_ID}",
    f"https://open.spotify.com/playlist/{SPOTIFY_ID}",
    "spotify:track:tooShort",
    SPOTIFY_ID[:-1],
    SPOTIFY_ID + "X",
    f"https://example.com/track/{SPOTIFY_ID}",
    f"spotify:track:{SPOTIFY_ID}\nmore",
    "",
])
def test_spotify_rejects_non_tracks(value):
    assert gen.normalize_spotify_id(value) is None


YT_ID = "mlM66FzjpgM"


@pytest.mark.parametrize("value,want", [
    (YT_ID, YT_ID),
    ("-M9PnC6uJVs", "-M9PnC6uJVs"),
    ("__IQOzqUUZA", "__IQOzqUUZA"),
    (f"https://www.youtube.com/watch?v={YT_ID}", YT_ID),
    (f"https://www.youtube.com/watch?v={YT_ID}&t=42s&list=RDx", YT_ID),
    (f"https://www.youtube.com/watch?feature=shared&v={YT_ID}", YT_ID),
    (f"https://www.youtube.com/watch?v={YT_ID}&v=AAAAAAAAAAA", YT_ID),
    (f"https://m.youtube.com/watch?v={YT_ID}", YT_ID),
    (f"https://music.youtube.com/watch?v={YT_ID}&si=abc", YT_ID),
    (f"https://youtu.be/{YT_ID}", YT_ID),
    (f"https://youtu.be/{YT_ID}?si=abc&t=10", YT_ID),
    (f"https://www.youtube.com/embed/{YT_ID}?start=10", YT_ID),
    (f"https://www.youtube-nocookie.com/embed/{YT_ID}", YT_ID),
    (f"https://www.youtube.com/shorts/{YT_ID}", YT_ID),
    (f"https://www.youtube.com/live/{YT_ID}?feature=share", YT_ID),
    (f"http://youtube.com/v/{YT_ID}", YT_ID),
])
def test_youtube_links_normalise_to_the_video_id(value, want):
    assert gen.normalize_youtube_id(value) == want


@pytest.mark.parametrize("value", [
    YT_ID[:-1], YT_ID + "X", f"https://youtu.be/{YT_ID}X", "https://www.youtube.com/playlist?list=PL0123456789",
    "https://www.youtube.com/watch?v=short", "https://vimeo.com/123456789", "mlM66Fzjpg!", "",
])
def test_youtube_rejects_non_videos(value):
    assert gen.normalize_youtube_id(value) is None


def test_links_are_normalised_in_the_set_file():
    pack = base_pack()
    pack["drops"][0].update(youtube=f"https://www.youtube.com/watch?v={YT_ID}&t=3",
                            spotify=f"https://open.spotify.com/intl-fr/track/{SPOTIFY_ID}?si=1")
    pack["drops"][1].update(youtube=f"https://youtu.be/-M9PnC6uJVs?si=2", spotify=f"spotify:track:{SPOTIFY_ID}")
    pack["drops"].append({"zone": "Westfall", "title": "t", "artist": "a", "youtube": "", "spotify": ""})
    by_id = {d["id"]: d for d in roundtrip(pack)["drops"]}
    assert (by_id["zone:1429"]["youtube"], by_id["zone:1429"]["spotify"]) == (YT_ID, SPOTIFY_ID)
    assert (by_id["area:87"]["youtube"], by_id["area:87"]["spotify"]) == ("-M9PnC6uJVs", SPOTIFY_ID)
    assert "youtube" not in by_id["zone:1436"] and "spotify" not in by_id["zone:1436"], "empty links mean none"


# --------------------------------------------------------------- determinism --

def test_output_is_deterministic():
    pack = base_pack()
    pack["drops"] += [{"zone": z, "title": f"song {z}", "artist": "x", "tags": ["fitting", "funny"]}
                      for z in ("Durotar", "Mulgore", "Westfall", "Duskwood", "Tanaris", "Felwood")]
    pack["tags"] = ["kojima", "post-rock"]
    want = gen.render_lua(build(pack))
    assert gen.render_lua(build(copy.deepcopy(pack))) == want
    rng = random.Random(1234)
    for _ in range(5):
        shuffled = copy.deepcopy(pack)
        rng.shuffle(shuffled["drops"])
        shuffled["drops"] = [dict(reversed(list(d.items()))) for d in shuffled["drops"]]
        shuffled = dict(reversed(list(shuffled.items())))
        assert gen.render_lua(build(shuffled)) == want
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}", want), "no timestamps"


def test_output_does_not_depend_on_the_current_directory(tmp_path, monkeypatch):
    packs_dir = tmp_path / "packs"
    pack = write_pack(packs_dir, "test-set.json", base_pack())
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    assert gen.main([str(pack), "--out", str(out1)]) == 0
    sub = tmp_path / "elsewhere"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert gen.main([os.path.relpath(pack, sub), "--out", str(out2)]) == 0
    a, b = (out1 / "TestSet.lua").read_bytes(), (out2 / "TestSet.lua").read_bytes()
    assert a == b
    assert b"\r\n" not in a and not a.startswith(b"\xef\xbb\xbf") and a.endswith(b"})\n")
    assert gen.generated_source(out1 / "TestSet.lua") == "test-set.json", "outside the repo: file name only"


def test_source_is_repo_relative_posix():
    assert gen.source_name(UNBOUND_JSON) == "packs/azeroth-unbound.json"
    assert gen.source_name(ROOT / "packs" / ".." / "packs" / "azeroth-unbound.json") == "packs/azeroth-unbound.json"


def test_disabled_drops_are_skipped():
    pack = base_pack()
    pack["drops"] += [
        {"zone": "Nowhere Land", "disabled": True},  # a bare (even broken) disabled entry is never checked
        {"zone": "Westfall", "title": "t", "artist": "a", "disabled": True},
        {"zone": "Duskwood", "title": "t", "artist": "a", "disabled": False},
        {"zone": "Durotar", "title": "t", "artist": "a", "disabled": None},
    ]
    s = build(pack)
    assert s.skipped == 2
    assert [d["id"] for d in s.drops] == ["area:87", "zone:1411", "zone:1429", "zone:1431"]
    assert all("disabled" not in d for d in s.drops)
    # a disabled duplicate does not clash with the enabled one
    pack["drops"].append({**pack["drops"][0], "disabled": True})
    assert len(build(pack).drops) == 4


# ------------------------------------------------------------------ escaping --

NASTY = [
    'He said "hi" and \'bye\'',
    "back\\slash, \\n is not a newline, \\\\ two",
    "line1\nline2\r\nline3\ttab",
    "nul\x00byte, bell\x07, esc\x1b, del\x7f, vt\x0b ff\x0c (text is trimmed, so not at the ends)",
    "\x01" + "23 digits after a control char",
    "]] [[ ]=] --[[ not a comment ]] -- nor this",
    "Ólafur Arnalds · Sæglópur · Jóhann Jóhannsson · Hildur Guðnadóttir",
    "日本語のタイトル 🎵 🇮🇸 é (precomposed) é (combining)",
    "ends with a backslash \\",
]


def _nasty_pack() -> dict:
    pack = base_pack()
    pack["name"] = 'Set "quoted" \\ ünïcødé 🎧'
    pack["id"] = "nasty-set"
    pack["author"] = "Ælfrīc \"the\" Øresund"
    pack["description"] = "multi\nline\n\"description\"\t\\o/"
    pack["tags"] = ["kojima", "ünïcødé", 'with "quotes" \\']
    pack["drops"] = [{"zone": z, "title": s, "artist": s, "note": s, "label": s, "tags": ["fitting", "日本"]}
                     for z, s in zip(("Durotar", "Mulgore", "Westfall", "Duskwood", "Tanaris", "Felwood", "Wetlands",
                                      "Badlands", "Desolace"), NASTY)]
    pack["drops"].append({"zone": "Elwynn Forest", "subzone": 'Lion\'s "Pride" Inn \\ ö', "title": "t", "artist": "a"})
    return pack


def test_escaping_round_trips_through_lua():
    pack = _nasty_pack()
    s = build(pack)
    src = gen.render_lua(s)
    assert src.count("\n") == 7 + len(s.drops), "every drop stays on one line (no raw newline in a literal)"
    for m in re.finditer(r"\\(.)", src):
        assert m.group(1) in '\\"nrt0123456789', f"non-Lua-5.1 escape \\{m.group(1)}"
    t = run_set_lua(src)
    assert (t["name"], t["author"], t["description"]) == (pack["name"], pack["author"], pack["description"])
    assert t["tags"] == pack["tags"]
    by_zone = {d["mapID"]: d for d in t["drops"] if d["kind"] == "zone"}
    for raw in pack["drops"][:-1]:
        d = by_zone[zones.lookup(raw["zone"]).id]
        assert (d["title"], d["artist"], d["note"], d["label"]) == (raw["title"],) * 4
        assert d["tags"] == ["fitting", "日本"]
    inn = next(d for d in t["drops"] if d["kind"] == "subzone")
    assert inn["subzone"] == 'Lion\'s "Pride" Inn \\ ö' and inn["id"] == "sub:1429:lion-s-pride-inn"


def test_escaping_keeps_exact_utf8_bytes():
    pack = _nasty_pack()
    t = run_set_lua(gen.render_lua(build(pack)), raw=True)
    assert t["name"] == pack["name"].encode("utf-8")
    assert t["description"] == pack["description"].encode("utf-8")
    titles = sorted(d["title"] for d in t["drops"])
    assert titles == sorted([s.encode("utf-8") for s in NASTY] + [b"t"])
    olafur = next(x for x in titles if x.startswith("Ólafur".encode("utf-8")))
    assert len(olafur) == len(NASTY[6].encode("utf-8")), "Lua #s counts UTF-8 bytes, nothing re-encoded"


def test_lua_string_unit():
    assert gen.lua_string('a"b\\c\nd\re\tf') == '"a\\"b\\\\c\\nd\\re\\tf"'
    assert gen.lua_string("\x01" + "2") == '"\\0012"', "zero-padded so the next digit is not swallowed"
    assert gen.lua_string("\x00\x1f\x7f") == '"\\000\\031\\127"'
    assert gen.lua_string("Sæglópur 🎵") == '"Sæglópur 🎵"', "UTF-8 is written as-is"


def test_lua_string_fuzz_round_trip():
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    loader = rt.execute("return function(s) return loadstring('return ' .. s)() end")
    pool = [chr(i) for i in range(0, 128)] + list("éÆøß·—“”‘’") + ["日", "本", "🎵", "́", " ", "﻿"]
    rng = random.Random(51)
    for _ in range(300):
        s = "".join(rng.choice(pool) for _ in range(rng.randint(0, 24)))
        assert loader(gen.lua_string(s)) == s, repr(s)


# ------------------------------------------------------------ invalid packs --

INVALID = [
    pytest.param(lambda p: p["drops"][1].pop("title"), 1, 'missing "title"', id="missing-title"),
    pytest.param(lambda p: p["drops"][0].update(artist="   "), 0, 'missing "artist"', id="blank-artist"),
    pytest.param(lambda p: p["drops"][0].update(title=5), 0, '"title" must be text', id="title-not-text"),
    pytest.param(lambda p: p["drops"][0].update(zone="Elwyn Forest"), 0,
                 "unknown zone 'Elwyn Forest' -- did you mean 'Elwynn Forest'", id="unknown-zone"),
    pytest.param(lambda p: p["drops"][0].update(zone=""), 0, '"zone" is empty', id="empty-zone"),
    pytest.param(lambda p: p["drops"][0].update(zone=0), 0, '"zone" must be a positive whole uiMapID', id="zone-0"),
    pytest.param(lambda p: p["drops"][0].update(zone=1429.5), 0, "positive whole uiMapID", id="zone-fraction"),
    pytest.param(lambda p: p["drops"][0].update(zone=True), 0, '"zone" must be a zone name or uiMapID number',
                 id="zone-bool"),
    pytest.param(lambda p: p["drops"][0].update(zone=["Elwynn"]), 0, "got a list", id="zone-list"),
    pytest.param(lambda p: p["drops"].append({"title": "t", "artist": "a"}), 2, "needs a location", id="no-location"),
    pytest.param(lambda p: p["drops"].append({"subzone": "Goldshire", "title": "t", "artist": "a"}), 2,
                 "subzone 'Goldshire' needs a \"zone\"", id="subzone-without-zone"),
    pytest.param(lambda p: p["drops"][1].update(subzone=""), 1, '"subzone" must be a non-empty name', id="subzone-empty"),
    pytest.param(lambda p: p["drops"].append({"zone": "Elwynn Forest", "subzone": "Златоземье", "title": "t",
                                              "artist": "a"}), 2,
                 "subzone 'Златоземье' has no letters or digits (a-z, 0-9) to build its id from", id="subzone-no-slug"),
    pytest.param(lambda p: p["drops"][1].update(areaID=-87), 1, '"areaID" must be a positive whole number',
                 id="area-negative"),
    pytest.param(lambda p: p["drops"].append({"instanceID": "abc", "title": "t", "artist": "a"}), 2,
                 '"instanceID" must be a positive whole number', id="instance-text"),
    pytest.param(lambda p: p["drops"][0].update(durationMs=-1), 0,
                 '"durationMs" must be a whole number of milliseconds >= 0', id="duration-negative"),
    pytest.param(lambda p: p["drops"][0].update(startMs=1.5), 0, '"startMs" must be a whole number',
                 id="start-fraction"),
    pytest.param(lambda p: p["drops"][0].update(durationMs=2**31), 0, '"durationMs" is too large', id="duration-huge"),
    pytest.param(lambda p: p["drops"][0].update(youtube="not a video"), 0,
                 '"youtube" must be an 11-character video id', id="youtube-bad"),
    pytest.param(lambda p: p["drops"][0].update(youtube="https://www.youtube.com/playlist?list=PL0123"), 0,
                 '"youtube" is a playlist link', id="youtube-playlist"),
    pytest.param(lambda p: p["drops"][0].update(youtube=12345678901), 0, '"youtube" must be', id="youtube-number"),
    pytest.param(lambda p: p["drops"][0].update(spotify=f"https://open.spotify.com/album/{SPOTIFY_ID}"), 0,
                 '"spotify" is a Spotify album link', id="spotify-album"),
    pytest.param(lambda p: p["drops"][0].update(spotify="spotify:track:abc"), 0, '"spotify" must be a Spotify track',
                 id="spotify-short"),
    pytest.param(lambda p: p["drops"][0].update(label=["x"]), 0, '"label" must be text', id="label-not-text"),
    pytest.param(lambda p: p["drops"][0].update(note={"a": 1}), 0, '"note" must be text', id="note-not-text"),
    pytest.param(lambda p: p["drops"][0].update(tags="fitting"), 0, '"tags" must be a list', id="tags-not-list"),
    pytest.param(lambda p: p["drops"][0].update(tags=["fitting", ""]), 0, '"tags"[1] must be non-empty text',
                 id="tag-empty"),
    pytest.param(lambda p: p["drops"][0].update(tags=["a\nb"]), 0, "single line", id="tag-newline"),
    pytest.param(lambda p: p["drops"][0].update(disabled="yes"), 0, '"disabled" must be true or false',
                 id="disabled-not-bool"),
    pytest.param(lambda p: p["drops"][0].update(id="zone:1"), 0,
                 "\"id\" 'zone:1' does not match the id derived from the location ('zone:1429')", id="id-mismatch"),
    pytest.param(lambda p: p["drops"].append(dict(p["drops"][0])), 2,
                 "duplicate drop id 'zone:1429' (also drops[0])", id="duplicate-id"),
    pytest.param(lambda p: p["drops"].append("Elwynn Forest"), 2, "each drop must be a JSON object", id="drop-not-object"),
    pytest.param(lambda p: p["drops"][0].update(title="broken \ud83c"), 0, '"title" contains invalid text',
                 id="lone-surrogate"),
]


@pytest.mark.parametrize("mutate,index,message", INVALID)
def test_invalid_drop_fails_with_pack_and_index(mutate, index, message):
    pack = base_pack()
    mutate(pack)
    errors = build_errors(pack)
    prefix = f'{SOURCE}: pack "Test Set" drops[{index}]'
    assert any(e.startswith(prefix) and message in e for e in errors), errors


INVALID_TOP = [
    pytest.param(lambda p: p.update(format="something-else"), '"format" must be "needledropper-pack"', id="format"),
    pytest.param(lambda p: p.update(version=2), 'unsupported pack "version" 2', id="version"),
    pytest.param(lambda p: p.update(drops={}), '"drops" must be a list', id="drops-not-list"),
    pytest.param(lambda p: p.update(drops=[]), "the set has no drops", id="no-drops"),
    pytest.param(lambda p: [d.update(disabled=True) for d in p["drops"]], "the set has no enabled drops",
                 id="all-disabled"),
    pytest.param(lambda p: p.update(id="Azeroth Unbound"),
                 '"id" must be 1-40 lowercase letters, digits and dashes (for example "azeroth-unbound")', id="id-bad"),
    pytest.param(lambda p: p.update(id="a" * 41), '"id" must be 1-40', id="id-too-long"),
    pytest.param(lambda p: p.update(id="---"), '"id" must be 1-40', id="id-only-dashes"),
    pytest.param(lambda p: p.update(id="con"), "which Windows reserves", id="id-windows-reserved"),
    pytest.param(lambda p: p.update(name="日本語のセット"), 'cannot derive an "id" from the name', id="id-underivable"),
    pytest.param(lambda p: p.update(homepage="ftp://example.com"), '"homepage" must be an http:// or https:// link',
                 id="homepage"),
    pytest.param(lambda p: p.update(author=5), '"author" must be text', id="author-not-text"),
    pytest.param(lambda p: p.update(tags=[1]), '"tags"[0] must be non-empty text', id="set-tags"),
]


@pytest.mark.parametrize("mutate,message", INVALID_TOP)
def test_invalid_pack_header_fails_with_pack_name(mutate, message):
    pack = base_pack()
    mutate(pack)
    errors = build_errors(pack)
    name = pack["name"] if isinstance(pack.get("name"), str) else "test-set"
    assert any(e.startswith(f'{SOURCE}: pack "{name}": ') and message in e for e in errors), errors


def test_bad_name_and_top_level_type():
    pack = base_pack()
    pack["name"] = ["not", "text"]
    assert any(e.startswith(f'{SOURCE}: pack "test-set": "name" must be text') for e in build_errors(pack))
    pack["name"] = "broken \udc80 name"
    assert any(e.startswith(f'{SOURCE}: pack "test-set": "name" contains invalid text') for e in build_errors(pack))
    assert build_errors([1, 2]) == [f'{SOURCE}: pack "test-set": top level must be a JSON object with '
                                    '"format", "name" and "drops"']


def test_error_message_format_is_exact():
    pack = base_pack()
    del pack["drops"][1]["artist"]
    assert build_errors(pack) == [f'{SOURCE}: pack "Test Set" drops[1] (Goldshire): missing "artist" (text)']


def test_every_error_is_reported_at_once():
    pack = base_pack()
    pack["drops"][0]["zone"] = "Nowhere"
    pack["drops"][0]["youtube"] = "bad"
    del pack["drops"][1]["title"]
    errors = build_errors(pack)
    assert len(errors) == 3
    assert sum("drops[0]" in e for e in errors) == 2 and sum("drops[1]" in e for e in errors) == 1


def test_unreadable_pack_files(tmp_path):
    bad_json = tmp_path / "broken.json"
    bad_json.write_text('{"format": "needledropper-pack",\n "drops": [ {"zone": 1429}, ]\n}', encoding="utf-8")
    with pytest.raises(gen.SetGenError) as ei:
        gen.load_set(bad_json)
    msg = str(ei.value.errors[0])
    assert msg.startswith('broken.json: pack "broken": invalid JSON at line 2 column') and "trailing comma" in msg
    with pytest.raises(gen.SetGenError, match="pack file not found"):
        gen.load_set(tmp_path / "missing.json")
    latin1 = tmp_path / "latin1.json"
    latin1.write_bytes('{"name": "S\xe6gl\xf3pur"}'.encode("latin-1"))
    with pytest.raises(gen.SetGenError, match="not valid UTF-8"):
        gen.load_set(latin1)
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(base_pack()).encode("utf-8"))
    assert gen.load_set(bom).name == "Test Set", "a UTF-8 BOM (Notepad) is fine"


# ------------------------------------------------------------------ warnings --

def test_warnings_never_fail_and_ignored_keys_are_not_emitted():
    pack = base_pack()
    pack["_comment"] = "JSON has no comments"
    pack["$schema"] = "https://example.com/pack.schema.json"
    pack["licence"] = "MIT"
    pack["drops"][0].update({"youtub": "x", "appleMusic": "https://music.apple.com/x", "volume": 80,
                             "file": "elwynn.mp3", "inGameFile": True, "// why": "comment", "tags": ["fitting", "fitting"]})
    pack["drops"].append({"instanceID": 36, "title": "t", "artist": "a"})
    pack["drops"].append({"zone": 9999, "title": "t", "artist": "a"})
    pack["drops"].append({"zone": "Elwynn Forest", "areaID": 279, "title": "t", "artist": "a"})
    s = build(pack)
    warnings = [str(w) for w in s.warnings]
    text = "\n".join(warnings)
    assert 'unknown key "youtub" ignored (did you mean "youtube"?)' in text
    assert 'unknown top-level key "licence" ignored' in text
    assert text.count('"file"/"inGameFile" ignored') == 1
    assert '"volume" ignored' in text
    assert "appleMusic" not in text and "_comment" not in text and "$schema" not in text and "// why" not in text
    assert 'duplicate tag "fitting"' in text
    assert 'instance drops should have a "label"' in text
    assert "zone 9999 is not in the known zone table" in text
    assert "areaID 279 is 'Dalaran' in Alterac Mountains (1416), not in zone 1429" in text
    by_id = {d["id"]: d for d in roundtrip(pack)["drops"]}
    assert by_id["zone:1429"]["tags"] == ["fitting"]
    assert set(by_id["zone:1429"]) == {"id", "kind", "mapID", "label", "title", "artist", "durationMs", "startMs",
                                       "youtube", "tags"}
    assert by_id["inst:36"]["label"] == "Instance 36"
    assert by_id["zone:9999"]["label"] == "Zone 9999"


def test_missing_name_and_format_are_warnings(tmp_path):
    data = base_pack()
    del data["name"], data["format"]
    p = write_pack(tmp_path, "late-night-drives.json", data)
    s = gen.load_set(p)
    assert (s.name, s.id, s.filename) == ("late-night-drives", "late-night-drives", "LateNightDrives.lua")
    text = "\n".join(map(str, s.warnings))
    assert 'missing "name"' in text and 'missing "format"' in text


def test_duplicate_json_keys_warn(tmp_path):
    p = tmp_path / "dupes.json"
    p.write_text('{"format": "needledropper-pack", "version": 1, "name": "Dupes", "name": "Dupes", "drops": ['
                 '{"zone": "Elwynn Forest", "title": "a", "artist": "b", "youtube": "AAAAAAAAAAA", '
                 '"youtube": "mlM66FzjpgM"}]}', encoding="utf-8")
    s = gen.load_set(p)
    text = "\n".join(map(str, s.warnings))
    assert 'pack "Dupes": key "name" is given more than once' in text
    assert 'drops[0] (Elwynn Forest): key "youtube" is given more than once; the last one wins' in text
    assert s.drops[0]["youtube"] == "mlM66FzjpgM"


# ---------------------------------------------------------------- set ids ----

@pytest.mark.parametrize("set_id,camel", [
    ("azeroth-unbound", "AzerothUnbound"), ("azeroth-mixtape", "AzerothMixtape"), ("lo-fi-2", "LoFi2"),
    ("8bit", "8bit"), ("-a--b-", "AB"), ("x", "X"),
])
def test_camel_case(set_id, camel):
    assert gen.camel_case(set_id) == camel


@pytest.mark.parametrize("name,set_id", [
    ("Azeroth Unbound", "azeroth-unbound"), ("Azeroth Mixtape", "azeroth-mixtape"),
    ("Ólafur's Mix", "olafur-s-mix"), ("  Lo-Fi  //  Beats 2 ", "lo-fi-beats-2"),
    ("a" * 50, "a" * 40), ("a" * 39 + " bcd", "a" * 39),
])
def test_derived_set_id(name, set_id):
    assert gen.derive_set_id(name) == set_id


def test_explicit_set_id_names_the_file():
    pack = base_pack()
    pack["id"] = "my-soundtrack-2"
    s = build(pack)
    assert (s.id, s.filename) == ("my-soundtrack-2", "MySoundtrack2.lua")


# ----------------------------------------------------------------------- CLI --

def _cli(capsys, argv, **kw):
    code = gen.main(argv, **kw)
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_write_and_check_cycle(tmp_path, capsys):
    packs, out = tmp_path / "packs", tmp_path / "Sets"
    write_pack(packs, "test-set.json", base_pack())
    kw = {"packs_dir": packs, "out_dir": out}
    target = out / "TestSet.lua"

    code, _, err = _cli(capsys, ["--check"], **kw)
    assert code == 1 and "missing:" in err and not out.exists(), "--check never writes"

    code, stdout, _ = _cli(capsys, [], **kw)
    assert code == 0 and target.is_file() and "wrote" in stdout
    assert gen.generated_source(target) == "test-set.json"

    code, stdout, _ = _cli(capsys, ["--check"], **kw)
    assert code == 0 and "ok: 1 set file(s) up to date" in stdout

    mtime = target.stat().st_mtime_ns
    code, stdout, _ = _cli(capsys, [], **kw)
    assert code == 0 and "up to date" in stdout and target.stat().st_mtime_ns == mtime, "unchanged = untouched"

    good = target.read_bytes()
    target.write_bytes(good.replace(b"Low Roar", b"Low Roar (live)"))
    code, _, err = _cli(capsys, ["--check"], **kw)
    assert code == 1 and "out of date:" in err and "run: python tools/gen_sets.py" in err
    assert _cli(capsys, [], **kw)[0] == 0 and target.read_bytes() == good

    # a CRLF checkout (git core.autocrlf) is not a difference, and is not rewritten
    target.write_bytes(good.replace(b"\n", b"\r\n"))
    mtime = target.stat().st_mtime_ns
    assert _cli(capsys, ["--check"], **kw)[0] == 0
    assert _cli(capsys, [], **kw)[0] == 0 and target.stat().st_mtime_ns == mtime


def test_cli_stale_files(tmp_path, capsys):
    packs, out = tmp_path / "packs", tmp_path / "Sets"
    write_pack(packs, "test-set.json", base_pack())
    other = base_pack()
    other["name"] = "Other Set"
    other_path = write_pack(packs, "other.json", other)
    kw = {"packs_dir": packs, "out_dir": out}
    assert _cli(capsys, [], **kw)[0] == 0 and (out / "OtherSet.lua").is_file()

    other_path.unlink()
    code, _, err = _cli(capsys, ["--check"], **kw)
    assert code == 1 and "stale:" in err and "OtherSet.lua (its pack other.json no longer exists)" in err
    code, _, err = _cli(capsys, [], **kw)
    assert code == 0 and "warning: stale:" in err and (out / "OtherSet.lua").is_file(), "never deletes"
    # explicit pack lists don't know the full set: no stale report
    assert _cli(capsys, ["--check", str(packs / "test-set.json"), "--out", str(out)])[0] == 0

    (out / "OtherSet.lua").unlink()
    renamed = base_pack()
    renamed["id"] = "renamed-set"
    write_pack(packs, "test-set.json", renamed)
    assert _cli(capsys, [], **kw)[0] == 0 and (out / "RenamedSet.lua").is_file()
    code, _, err = _cli(capsys, ["--check"], **kw)
    assert code == 1 and "TestSet.lua (test-set.json now generates RenamedSet.lua)" in err


def test_cli_never_overwrites_foreign_files(tmp_path, capsys):
    packs, out = tmp_path / "packs", tmp_path / "Sets"
    write_pack(packs, "test-set.json", base_pack())
    out.mkdir()
    foreign = out / "TestSet.lua"
    foreign.write_text("local ADDON, ns = ...\n-- hand-written\n", encoding="utf-8")
    for argv in ([], ["--check"]):
        code, _, err = _cli(capsys, argv, packs_dir=packs, out_dir=out)
        assert code == 1 and "was not generated by tools/gen_sets.py; refusing to overwrite" in err
    assert foreign.read_text(encoding="utf-8") == "local ADDON, ns = ...\n-- hand-written\n"
    assert gen.generated_source(foreign) is None


def test_cli_never_clobbers_another_packs_file(tmp_path, capsys, monkeypatch):
    """A copied pack that kept the name (so the same set id), generated on its own, must not
    silently replace the original pack's file."""
    monkeypatch.setattr(gen, "ROOT", tmp_path)  # make tmp_path "the repo" so sources are packs/<file>
    packs, out = tmp_path / "packs", tmp_path / "Sets"
    original = write_pack(packs, "a.json", base_pack())
    copied = write_pack(packs, "b.json", base_pack())
    assert _cli(capsys, [str(original), "--out", str(out)])[0] == 0
    for argv in ([str(copied), "--out", str(out)], ["--check", str(copied), "--out", str(out)]):
        code, _, err = _cli(capsys, argv)
        assert code == 1 and "holds the set generated from packs/a.json, which still exists" in err
    assert gen.generated_source(out / "TestSet.lua") == "packs/a.json"
    original.unlink()  # the pack was renamed: taking over its file is fine
    assert _cli(capsys, [str(copied), "--out", str(out)])[0] == 0
    assert gen.generated_source(out / "TestSet.lua") == "packs/b.json"


def test_cli_invalid_pack_does_not_block_the_others(tmp_path, capsys):
    packs, out = tmp_path / "packs", tmp_path / "Sets"
    write_pack(packs, "good.json", base_pack())
    bad = base_pack()
    bad["name"] = "Sæglópur 🎵 Set"
    del bad["drops"][0]["artist"]
    write_pack(packs, "bad.json", bad)
    code, stdout, err = _cli(capsys, [], packs_dir=packs, out_dir=out)
    assert code == 1
    assert (out / "TestSet.lua").is_file() and not (out / "SaeglopurSet.lua").exists()
    assert 'error: bad.json: pack "Sæglópur 🎵 Set" drops[0] (Elwynn Forest): missing "artist" (text)' in err
    assert "FAILED bad.json: 1 error(s)" in err
    assert "1 written, 0 up to date, 1 failed" in stdout
    code, _, err = _cli(capsys, ["--check"], packs_dir=packs, out_dir=out)
    assert code == 1 and "check failed: 1 pack(s) failed, 0 file problem(s)" in err


def test_cli_clashing_packs_both_fail(tmp_path, capsys):
    packs, out = tmp_path / "packs", tmp_path / "Sets"
    write_pack(packs, "a.json", base_pack())
    write_pack(packs, "b.json", base_pack())
    code, _, err = _cli(capsys, [], packs_dir=packs, out_dir=out)
    assert code == 1 and not out.exists()
    assert 'a.json: pack "Test Set": set id "test-set" is also used by b.json' in err
    assert 'b.json: pack "Test Set": set id "test-set" is also used by a.json' in err

    one, two = base_pack(), base_pack()
    one["id"], two["id"] = "ab-c", "abc"  # AbC.lua vs Abc.lua: one file on Windows
    write_pack(packs, "a.json", one)
    write_pack(packs, "b.json", two)
    code, _, err = _cli(capsys, [], packs_dir=packs, out_dir=out)
    assert code == 1 and "output file AbC.lua collides with Abc.lua (b.json)" in err


def test_cli_explicit_paths_and_messages(tmp_path, capsys):
    pack = write_pack(tmp_path / "anywhere", "my-pack.json", base_pack())
    out = tmp_path / "out"
    code, stdout, _ = _cli(capsys, [str(pack), str(pack), "--out", str(out)])
    assert code == 0 and stdout.count("wrote") == 1, "the same pack twice is generated once"
    assert gen.generated_source(out / "TestSet.lua") == "my-pack.json"
    assert _cli(capsys, ["--check", str(pack), "--out", str(out)])[0] == 0
    code, _, err = _cli(capsys, [], packs_dir=tmp_path / "empty", out_dir=out)
    assert code == 1 and "no pack files" in err
    with pytest.raises(SystemExit) as ei:
        gen.main(["--bogus"])
    assert ei.value.code == 2
    capsys.readouterr()


def test_cli_warns_when_the_toc_does_not_load_a_set(tmp_path, capsys):
    addon = tmp_path / "NeedleDropper"
    packs, out = tmp_path / "packs", addon / "Sets"
    write_pack(packs, "test-set.json", base_pack())
    addon.mkdir()
    toc = addon / "NeedleDropper.toc"
    toc.write_text("## Interface: 16001\nCore.lua\n", encoding="utf-8")
    kw = {"packs_dir": packs, "out_dir": out}
    _, _, err = _cli(capsys, [], **kw)
    assert "warning: Sets\\TestSet.lua is not listed in" in err
    toc.write_text("## Interface: 16001\nCore.lua\nSets.lua\nSets\\TestSet.lua\n", encoding="utf-8")
    assert "not listed" not in _cli(capsys, ["--check"], **kw)[2]
    toc.write_text("## Interface: 16001\nCore.lua\nSets\\Sets.xml\n", encoding="utf-8")
    assert "not listed" not in _cli(capsys, ["--check"], **kw)[2], "an XML include may list the sets"


def test_cli_survives_a_cp1252_pipe(tmp_path):
    """Messages quote pack text and file names; a Windows pipe (cp1252) must not turn them into a
    UnicodeEncodeError traceback, on stdout ("wrote ... from mix🎵.json") or stderr."""
    good = write_pack(tmp_path, "mix🎵.json", base_pack())
    bad = base_pack()
    bad["name"], bad["id"] = "Sæglópur 🎵 日本", "bad-set"
    del bad["drops"][1]["title"]
    bad_path = write_pack(tmp_path, "bad.json", bad)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "cp1252"
    proc = subprocess.run([sys.executable, str(GEN_PATH), str(good), str(bad_path), "--out", str(tmp_path / "out")],
                          cwd=tmp_path, env=env, capture_output=True, timeout=120)
    out, err = proc.stdout.decode("cp1252", "replace"), proc.stderr.decode("cp1252", "replace")
    assert proc.returncode == 1, out + err
    assert "Traceback" not in out + err
    assert "wrote" in out and "mix\\U0001f3b5.json" in out, "unencodable text is escaped, not fatal"
    assert "drops[1]" in err and 'missing "title"' in err
    assert (tmp_path / "out" / "TestSet.lua").is_file()
