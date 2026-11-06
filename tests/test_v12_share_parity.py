"""Share.Validate (Lua) normalizes packs exactly like the companion's sharecode.validate
(Python, owned by C1): DESIGN-v1.2 section 5.5 says the limits and validation are the same in
both. Skipped until companion/needledropper/sharecode.py exists.

Known, deliberate differences (reported to the integrator):
* Python fills in the zone of a known area (zones.KNOWN_AREAS) when a drop has an areaID but
  no zone; the game has no such table, so Lua leaves it out (the drop id is area:<id> either way).
* A pack "version" above 1 is "newer" in Lua (update the addon) and "invalid" in Python.
* A landmark saved by a name without a-z / 0-9 (and no areaID) is refused in Lua: its id
  "sub:<map>:" would be empty after the colon (gen_sets refuses it too); Python accepts it.
"""
from __future__ import annotations

import copy
import importlib.util

import pytest

from v12_model_harness import BytesRuntime, canon

pytestmark = pytest.mark.skipif(importlib.util.find_spec("needledropper.sharecode") is None,
                                reason="needledropper.sharecode (C1) does not exist yet")

SPOTIFY = "4uLU6hMCjMI75M1A2tKUQC"


def base(**top):
    p = {"format": "needledropper-pack", "version": 1, "id": "parity", "name": "Parity", "author": "A",
         "description": "", "tags": [], "revision": 2,
         "drops": [{"zone": 1429, "artist": "Low Roar", "title": "I'll Keep Coming"}]}
    p.update(top)
    return p


def with_drop(**drop):
    d = {"zone": 1436, "artist": "Toto", "title": "Africa"}
    d.update(drop)
    p = base()
    p["drops"] = p["drops"] + [d]
    return p


ACCEPTED = {
    "spotify link": with_drop(spotify="https://open.spotify.com/intl-de/track/%s?si=abc" % SPOTIFY),
    "spotify uri": with_drop(spotify="spotify:track:" + SPOTIFY),
    "youtube link": with_drop(youtube="https://www.youtube.com/watch?v=QAo_Ycocl1E&t=83"),
    "zero times": with_drop(startMs=0, durationMs=0),
    "digit-string times": with_drop(startMs="1500", durationMs=" 2000 ", volume="70"),
    "integral floats": with_drop(startMs=1500.0, zone=1436.0),
    "off with a song": with_drop(disabled=True, label="Westfall", note="kept?", spotify="nope"),
    "off landmark": with_drop(disabled=True, subzone="Sentinel Hill", artist=None, title=None),
    "off area": with_drop(disabled=True, zone=None, areaID=108),
    "off without letters": with_drop(disabled=True, zone=1429, subzone="東京"),
    "file without inGameFile": with_drop(file="westfall.ogg"),
    "file path": with_drop(file="..\\..\\Wow.exe", inGameFile=True),
    "file with spaces": with_drop(file="  my song (live).MP3 ", inGameFile=True),
    "duplicate tags": with_drop(tags=["funny", " funny ", "fitting"]),
    "unknown keys": with_drop(youtub="QAo_Ycocl1E", n=5),
    "comment keys": with_drop(_comment="x", **{"$schema": "y", "//": "z"}),
    "apple music": with_drop(appleMusic="https://music.apple.com/x"),
    "empty label": with_drop(label=""),
    "controls": with_drop(title="Af\x00ri\x1fca\x7f", note="line\r\nnext\ttab"),
    "unicode spaces": with_drop(artist=" 　Toto ", label="\u0085Westfall "),
    "landmark by name": with_drop(subzone="Sentinel Hill"),
    "instance": with_drop(zone=None, instanceID=36, label="The Deadmines"),
    "id cross-check": with_drop(id="zone:9999"),
    "no format": {k: v for k, v in base().items() if k != "format"},
    "no revision": {k: v for k, v in base().items() if k != "revision"},
    "no id": {k: v for k, v in base(name="Café Ünder Ǆungla").items() if k != "id"},
    "empty id": base(id="", name="Zoë's Mix"),
    "homepage": base(homepage=" https://example.com/x?y=1 "),
    "description newlines": base(description="One\r\nTwo\n\n"),
    "name spaces": base(name="  Parity  "),
    "top unknown": base(colour="red", _note="x"),
    "top duplicate tags": base(tags=["a", "a", "b"]),
    "version string": base(version="1"),
}

REFUSED = {
    "zone digit string": with_drop(zone="1436"),
    "zone name": with_drop(zone="Westfall"),
    "zone zero": with_drop(zone=0),
    "zone fraction": with_drop(zone=1436.5),
    "area string": with_drop(areaID="108", subzone="Sentinel Hill"),
    "inGameFile string": with_drop(file="a.mp3", inGameFile="yes"),
    "inGameFile without file": with_drop(inGameFile=True),
    "file number": with_drop(file=5),
    "tags string": with_drop(tags="funny"),
    "tag newline": with_drop(tags=["two\nlines"]),
    "tag empty": with_drop(tags=["  "]),
    "nine tags": with_drop(tags=["t%d" % i for i in range(9)]),
    "long tag": with_drop(tags=["x" * 25]),
    "no name": {k: v for k, v in base().items() if k != "name"},
    "blank name": base(name="   "),
    "long name": base(name="n" * 65),
    "ftp homepage": base(homepage="ftp://example.com"),
    "upper-case scheme": base(homepage="HTTP://example.com"),
    "homepage with space": base(homepage="https://example.com/a b"),
    "blank homepage": base(homepage="   "),
    "revision string": base(revision="3"),
    "revision negative": base(revision=-1),
    "revision fraction": base(revision=1.5),
    "bad id": base(id="Not An Id"),
    "same place twice": with_drop(zone=1429),
    "301 drops": base(drops=[{"zone": 5000 + i, "artist": "a", "title": "t"} for i in range(301)]),
    "no drops": base(drops=[]),
    "drops object": base(drops={"a": 1}),
    "long title": with_drop(title="t" * 201),
    "missing artist": with_drop(artist=None),
    "blank title": with_drop(title="   "),
    "number title": with_drop(title=5),
    "http apple music": with_drop(appleMusic="http://music.apple.com/x"),
    "loud": with_drop(volume=101),
    "negative start": with_drop(startMs=-5),
    "huge start": with_drop(startMs=2147483648),
    "album link": with_drop(spotify="https://open.spotify.com/album/1A2GTWGtFfWp7KSQTwWOyo"),
    "playlist link": with_drop(youtube="https://www.youtube.com/playlist?list=PL1"),
    "subzone without zone": with_drop(zone=None, subzone="Sentinel Hill"),
    "empty subzone": with_drop(subzone=""),
    "disabled string": with_drop(disabled="yes"),
    "drop list": base(drops=[["zone", 1429]]),
    "wrong format": base(format="needledropper-set"),
    "label number": with_drop(label=7),
}


def strip_none(v):
    """with_drop(key=None) means "leave the key out"."""
    if isinstance(v, dict):
        return {k: strip_none(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [strip_none(x) for x in v]
    return v


@pytest.fixture(scope="module")
def rt():
    return BytesRuntime()


def python_validate(pack):
    """sharecode.validate after the integral-float step its decoder applies (as Lua numbers)."""
    from needledropper import sharecode
    try:
        norm, warnings = sharecode.validate(sharecode.ints_from_integral_floats(copy.deepcopy(pack)))
        return norm, warnings, None
    except sharecode.ShareCodeError as exc:
        return None, None, exc.reason


def without_filled_zones(py_pack, lua_pack):
    """Python adds the zone of a known area; drop it where Lua (rightly) has none."""
    out = copy.deepcopy(py_pack)
    for pd, ld in zip(out.get("drops", []), lua_pack.get("drops", [])):
        if "zone" in pd and "zone" not in ld and "areaID" in pd:
            del pd["zone"]
    return out


@pytest.mark.parametrize("case", sorted(ACCEPTED))
def test_accepted_packs_normalize_identically(rt, case):
    pack = strip_none(ACCEPTED[case])
    py_norm, py_warn, py_reason = python_validate(pack)
    lua_norm, lua_warn = rt.call("Share.Validate", pack)[:2]
    assert py_reason is None, f"Python refused {case}: {py_reason}"
    assert lua_norm is not None, f"Lua refused {case}: {lua_warn}"
    lua_norm = canon(lua_norm)
    assert lua_norm == without_filled_zones(py_norm, lua_norm)
    assert bool(canon(lua_warn)) == bool(py_warn), (case, lua_warn, py_warn)


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_refused_packs_are_refused_by_both(rt, case):
    pack = strip_none(REFUSED[case])
    _, _, py_reason = python_validate(pack)
    res = rt.call("Share.Validate", pack)
    assert res[0] is None, f"Lua accepted {case}"
    assert py_reason is not None, f"Python accepted {case}"
    assert res[1] == py_reason == "invalid", (case, res, py_reason)
    assert res[2], "Lua says why"


def test_known_differences(rt):
    newer = rt.call("Share.Validate", base(version=2))
    assert newer[:2] == (None, "newer") and python_validate(base(version=2))[2] == "invalid"
    cjk = strip_none(with_drop(zone=1429, subzone="東京"))
    assert rt.call("Share.Validate", cjk)[1] == "invalid"
    py_norm, _, _ = python_validate(cjk)
    assert py_norm is not None
    # Dalaran (areaID 279) is in the companion's zones.KNOWN_AREAS, in zone 1416
    area_only = strip_none(with_drop(zone=None, areaID=279, subzone="Dalaran"))
    lua_norm = canon(rt.call1("Share.Validate", area_only))
    py_norm, _, _ = python_validate(area_only)
    assert "zone" not in lua_norm["drops"][1] and py_norm["drops"][1].get("zone") == 1416
    assert without_filled_zones(py_norm, lua_norm) == lua_norm


def test_derived_pack_ids_fold_accents_like_python(rt):
    from needledropper import packs
    for name in ["Café Ünder", "Ǆungla Ĳssel", "Forêt d'Elwynn", "Ölmühle 2", "ſtraße", "東京 Mix", "Zoë"]:
        assert rt.call1("Share.DerivePackId", name) == packs.derive_pack_id(name), name
