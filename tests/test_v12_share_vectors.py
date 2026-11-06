"""Share-code golden vectors (DESIGN-v1.2 sections 5.2-5.4, 6.3).

The vectors in tests/vectors/share/*.json were written by the Python reference encoder of
DESIGN 5.4 (v12_model_harness.ref_encode). They are regenerated when missing or when
ND_REGEN_SHARE_VECTORS=1 is set. Every vector holds a canonical pack and the code written for it:

* the Python code decodes in Lua (Share.Decode) to the same pack, and in Python;
* the pack is canonical: Share.Validate returns it unchanged, without warnings;
* a code written by Lua (Share.Encode) decodes in Python to the same pack, and with the same
  zlib the two codes are byte-identical (the real client's zlib may differ, so other tests
  compare packs, never codes);
* decode / encode round trips are idempotent and every paste variant decodes.
"""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

from v12_model_harness import (VECTORS_DIR, BytesRuntime, canon, discord_fenced, ref_code_uncompressed,
                               ref_decode, ref_encode, urlsafe_unpadded, with_zero_width, wrapped)

SPOTIFY = "4uLU6hMCjMI75M1A2tKUQC"

DESIGN_EXAMPLE = {
    "author": "", "description": "Songs for my alts", "drops": [
        {"areaID": 87, "artist": "Toto", "label": "Goldshire", "subzone": "Goldshire", "tags": ["funny"],
         "title": "Africa", "youtube": "QAo_Ycocl1E", "zone": 1429},
        {"artist": "Low Roar", "durationMs": 352000, "spotify": "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
         "title": "I'll Keep Coming", "zone": 1429},
        {"disabled": True, "zone": 1426},
        {"artist": "Me", "file": "stormwind.mp3", "inGameFile": True, "title": "Home", "zone": 1453}],
    "format": "needledropper-pack", "id": "my-drops", "name": "My Drops", "revision": 4, "tags": [], "version": 1}

UNICODE = {
    "format": "needledropper-pack", "version": 1, "id": "unicode-test", "name": "Ünïcödé · 東京 🎵",
    "author": "Zoë «Ž» O'Brien",
    "description": "Line one\nLine two — \"quotes\", \\backslashes\\ and |cffff0000colour codes|r kept raw",
    "tags": ["café", "日本"], "revision": 7, "drops": [
        {"zone": 1429, "artist": "Sigur Rós", "title": "Hoppípolla", "label": "Forêt d'Elwynn", "note": "Ægir\nÞór"},
        {"areaID": 88, "zone": 1429, "subzone": "狮王之傲旅店", "artist": "久石譲", "title": "Summer",
         "youtube": "UBU1fUfm5Cs"},
        {"instanceID": 36, "zone": 1436, "label": "Die Todesminen", "artist": "Rammstein", "title": "Du hast",
         "spotify": "spotify:track:5awDvzxWfd53SSrsRZ8pXO"},
        {"zone": 1411, "artist": "فيروز", "title": "نسم علينا الهوى", "tags": ["fitting"]},
        {"zone": 1454, "artist": "|TInterface\\Icons\\Spell_Fire_Fire:0|t Fire", "title": "🔥 Burn 🔥 \U0001F3B5"},
        {"zone": 1453, "subzone": "Café Ünder", "label": "Café Ünder", "artist": "Édith Piaf", "title": "La Vie en rose"},
    ]}

EVERY_FIELD = {
    "format": "needledropper-pack", "version": 1, "id": "every-field", "name": "Every Field", "author": "Tester",
    "description": "All the keys a pack drop can have.", "tags": ["fitting", "funny"],
    "homepage": "https://example.com/soundtracks/every-field", "revision": 123456, "drops": [
        {"zone": 1429, "subzone": "Goldshire", "areaID": 87, "label": "Goldshire", "artist": "A", "title": "T",
         "spotify": "spotify:track:" + SPOTIFY, "youtube": "QAo_Ycocl1E", "startMs": 15500, "durationMs": 245000,
         "volume": 80, "tags": ["funny"], "note": "every song field"},
        {"zone": 1429, "subzone": "Lion's Pride Inn", "label": "Lion's Pride Inn", "artist": "By", "title": "Name"},
        {"instanceID": 36, "label": "The Deadmines", "artist": "Inst", "title": "Without a zone"},
        {"areaID": 150, "zone": 1437, "subzone": "Menethil Harbor", "artist": "Area", "title": "With its zone"},
        {"zone": 1453, "file": "stormwind (live).mp3", "inGameFile": True, "artist": "Me", "title": "Home",
         "volume": 0},
        {"zone": 1426, "disabled": True},
        {"zone": 1429, "subzone": "Fargodeep Mine", "disabled": True},
        {"instanceID": 48, "disabled": True},
        {"zone": 2147483647, "artist": "Max", "title": "Ids", "startMs": 2147483647, "durationMs": 1},
    ]}

MINIMAL = {"format": "needledropper-pack", "version": 1, "id": "minimal", "name": "Minimal", "author": "",
           "description": "", "tags": [], "revision": 0,
           "drops": [{"zone": 1429, "artist": "Low Roar", "title": "I'll Keep Coming"}]}


def max_300():
    drops = []
    for i in range(300):
        drops.append({"zone": 10000 + i, "artist": "Artist %d" % (i % 37), "title": "Song title number %d" % i,
                      "youtube": "QAo_Ycocl1E", "durationMs": 180000 + i * 1000, "label": "Zone %d" % (10000 + i),
                      "note": "Why it fits: number %d of three hundred." % i})
    return {"format": "needledropper-pack", "version": 1, "id": "max-300", "name": "Three Hundred", "author": "Big",
            "description": "The most drops a code may carry.", "tags": ["big"], "revision": 1, "drops": drops}


def mixtape_pack(rt):
    pack, omitted = rt.call("Share.FromSet", "builtin:azeroth-mixtape")
    assert omitted == 0
    return canon(pack)


def vector_specs(rt):
    return [
        ("design-example", "The pack of DESIGN-v1.2 section 5.2.", DESIGN_EXAMPLE, ref_encode),
        ("azeroth-mixtape", "The built-in default soundtrack (Share.FromSet).", mixtape_pack(rt), ref_encode),
        ("unicode", "Accents, CJK, Arabic, emoji, quotes, backslashes and raw | sequences.", UNICODE, ref_encode),
        ("every-field", "Every pack key, the largest numbers and turned-off places.", EVERY_FIELD, ref_encode),
        ("minimal", "The smallest pack.", MINIMAL, ref_encode),
        ("max-300", "300 drops: the limit.", max_300(), ref_encode),
        ("uncompressed-json", "A payload of raw JSON ('{'): readers accept it, NeedleDropper never writes it.",
         DESIGN_EXAMPLE, ref_code_uncompressed),
    ]


@pytest.fixture(scope="module")
def rt():
    return BytesRuntime()


@pytest.fixture(scope="module")
def vectors(rt):
    regen = os.environ.get("ND_REGEN_SHARE_VECTORS") == "1"
    out = {}
    for name, about, pack, writer in vector_specs(rt):
        path = VECTORS_DIR / (name + ".json")
        if regen or not path.is_file():
            VECTORS_DIR.mkdir(parents=True, exist_ok=True)
            doc = {"name": name, "about": about, "writer": "tests/v12_model_harness.py " + writer.__name__,
                   "pack": pack, "code": writer(pack)}
            if name == "design-example":
                doc["variants"] = {"urlsafe-unpadded": urlsafe_unpadded(doc["code"]),
                                   "wrapped-76": wrapped(doc["code"]), "discord": discord_fenced(doc["code"])}
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        out[name] = json.loads(path.read_text(encoding="utf-8"))
    return out


NAMES = ["design-example", "azeroth-mixtape", "unicode", "every-field", "minimal", "max-300", "uncompressed-json"]


@pytest.mark.parametrize("name", NAMES)
def test_python_code_decodes_in_lua_to_the_same_pack(rt, vectors, name):
    v = vectors[name]
    pack, warnings, detail = rt.decode(v["code"])
    assert detail is None and canon(warnings) == []
    assert canon(pack) == v["pack"]


@pytest.mark.parametrize("name", NAMES)
def test_python_reference_decodes_the_vector(vectors, name):
    v = vectors[name]
    assert ref_decode(v["code"]) == v["pack"]


@pytest.mark.parametrize("name", NAMES)
def test_vector_packs_are_canonical(rt, vectors, name):
    pack, warnings = rt.call("Share.Validate", vectors[name]["pack"])
    assert canon(pack) == vectors[name]["pack"] and canon(warnings) == []


@pytest.mark.parametrize("name", NAMES)
def test_lua_code_decodes_in_python(rt, vectors, name):
    code, err = rt.encode(vectors[name]["pack"])
    assert err is None and code.startswith("!NeedleDropper:1!") and code.endswith("!")
    assert ref_decode(code) == vectors[name]["pack"]
    pack, _, _ = rt.decode(code)
    assert canon(pack) == vectors[name]["pack"]


@pytest.mark.parametrize("name", NAMES)
def test_lua_and_python_codes_are_identical_with_the_same_zlib(rt, vectors, name):
    code, _ = rt.encode(vectors[name]["pack"])
    assert code == ref_encode(vectors[name]["pack"])


@pytest.mark.parametrize("name", NAMES)
def test_round_trips_are_idempotent(rt, vectors, name):
    first, _, _ = rt.decode(vectors[name]["code"])
    code, _ = rt.encode(first)
    second, _, _ = rt.decode(code)
    assert canon(second) == canon(first)
    code2, _ = rt.encode(second)
    assert code2 == code


@pytest.mark.parametrize("variant", ["urlsafe-unpadded", "wrapped-76", "discord"])
def test_stored_paste_variants_decode(rt, vectors, variant):
    v = vectors["design-example"]
    text = v["variants"][variant]
    pack, _, _ = rt.decode(text)
    assert canon(pack) == v["pack"] and ref_decode(text) == v["pack"]


def test_vector_sizes(vectors):
    assert len(vectors["azeroth-mixtape"]["code"]) < 6600  # DESIGN 5.3 measured 6,290 characters
    assert len(vectors["max-300"]["code"]) < 65536
    assert len(vectors["azeroth-mixtape"]["pack"]["drops"]) == 38


def test_vectors_pass_packs_py(vectors):
    """DESIGN 6.3: codes written by Lua decode in Python and packs.py validates them."""
    from needledropper import packs
    known = getattr(packs, "KNOWN_DROP_KEYS", set())
    for name in ("design-example", "azeroth-mixtape", "unicode", "every-field", "minimal", "max-300"):
        pack = vectors[name]["pack"]
        result = packs.parse_pack(pack, name_hint=name)
        errors = [str(i) for i in result.issues if i.level == "error"]
        if errors and not {"disabled", "tags"} <= set(known):
            pytest.xfail("packs.py (owned by C1) does not know the v1.2 drop keys yet: " + errors[0])
        assert errors == [], (name, errors)


def test_companion_sharecode_module_agrees(rt, vectors):
    """Cross-check with C1's companion/needledropper/sharecode.py once it exists."""
    if importlib.util.find_spec("needledropper.sharecode") is None:
        pytest.skip("needledropper.sharecode (C1) does not exist yet")
    from needledropper import sharecode
    for name in NAMES:
        v = vectors[name]
        try:
            decoded = sharecode.decode(v["code"])
        except Exception as exc:  # noqa: BLE001 - report which vector the companion refuses
            pytest.fail(f"sharecode.decode refused vector {name}: {exc!r}")
        decoded = getattr(decoded, "raw", decoded)
        if isinstance(decoded, dict):
            assert decoded == v["pack"], name
        lua_pack, _, _ = rt.decode(sharecode.encode(v["pack"]))
        assert canon(lua_pack) == v["pack"], name


def test_a_python_code_with_zero_width_characters_decodes(rt, vectors):
    v = vectors["unicode"]
    text = with_zero_width(v["code"])
    pack, _, _ = rt.decode(text)
    assert canon(pack) == v["pack"] and ref_decode(text) == v["pack"]
