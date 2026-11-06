"""``needledropper.sharecode``: the ``!NeedleDropper:1!`` share code (DESIGN-v1.2 5.1-5.5, 6.3).

Vectors (both bundled packs, the DESIGN 5.2 example), the robustness corpus of DESIGN 6.3 (Discord
fences, wrapping, zero-width characters, URL-safe unpadded, truncation, random one-character damage,
size caps, newer containers, foreign prefixes, hostile fields, a zlib bomb), the validation rules,
and interop with the addon's own pure-Lua readers (``Base64.lua`` / ``Json.lua`` under lupa).
"""
from __future__ import annotations

import base64
import copy
import json
import random
import zlib
from pathlib import Path

import pytest

from needledropper import packs, sharecode as sc
from needledropper.sharecode import ShareCodeError

import lupa.lua51 as lua51

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "packs"
ADDON = ROOT / "addon" / "NeedleDropper"
VECTORS = ROOT / "tests" / "vectors" / "share"
SP_ID = "4uLU6hMCjMI75M1A2tKUQC"

#: DESIGN-v1.2 5.2, verbatim.
EXAMPLE = json.loads("""{"author":"","description":"Songs for my alts","drops":[
  {"areaID":87,"artist":"Toto","label":"Goldshire","subzone":"Goldshire","tags":["funny"],"title":"Africa","youtube":"QAo_Ycocl1E","zone":1429},
  {"artist":"Low Roar","durationMs":352000,"spotify":"spotify:track:4uLU6hMCjMI75M1A2tKUQC","title":"I'll Keep Coming","zone":1429},
  {"disabled":true,"zone":1426},
  {"artist":"Me","file":"stormwind.mp3","inGameFile":true,"title":"Home","zone":1453}],
 "format":"needledropper-pack","id":"my-drops","name":"My Drops","revision":4,"tags":[],"version":1}""")


def load(name: str) -> dict:
    return json.loads((PACKS / name).read_text(encoding="utf-8"))


def code_of(pack: dict) -> str:
    out, _w = sc.pack_for_sharing(pack)
    return sc.encode(out)


def raw_code(payload: bytes, *, version: str = "1", compress: bool = True, end: str = "!") -> str:
    """A code around arbitrary payload bytes (hostile-input tests)."""
    blob = zlib.compress(payload, 9) if compress else payload
    return f"!NeedleDropper:{version}!" + base64.b64encode(blob).decode("ascii") + end


def reason(text: str) -> str:
    with pytest.raises(ShareCodeError) as ei:
        sc.decode(text)
    return ei.value.reason


MIXTAPE_CODE = code_of(load("azeroth-mixtape.json"))


# ------------------------------------------------------------------ vectors --

@pytest.mark.parametrize("name", ["azeroth-mixtape.json", "azeroth-unbound.json"])
def test_bundled_packs_round_trip_and_are_idempotent(name):
    data = load(name)
    pack, warnings = sc.pack_for_sharing(data)
    assert warnings == []
    code = sc.encode(pack)
    assert code.startswith(sc.PREFIX) and code.endswith("!") and len(code) < 16000
    dec = sc.decode_detailed(code)
    assert dec.normalized == pack and dec.pack == pack and dec.warnings == () and dec.compressed
    assert sc.encode(dec.normalized) == code  # deterministic and idempotent
    assert sc.decode(code) == pack
    # the code's soundtrack is the pack file's soundtrack
    parsed = packs.parse_pack(copy.deepcopy(data))
    assert [d["zone"] for d in pack["drops"] if "zone" in d] == [d.mapID for d in parsed.drops if d.mapID]
    assert pack["id"] == parsed.id and len(pack["drops"]) == len(parsed.drops)


def test_writer_form_details():
    pack, _ = sc.pack_for_sharing(load("azeroth-unbound.json"))
    assert pack["id"] == "azeroth-unbound"  # derived from the name: Unbound has no "id"
    assert all(isinstance(d.get("zone", 0), int) for d in pack["drops"])  # zone names became uiMapIDs
    assert all("startMs" not in d or d["startMs"] > 0 for d in pack["drops"])
    text = json.dumps(pack, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert "null" not in text
    raw = zlib.decompress(base64.b64decode(sc.encode(pack)[len(sc.PREFIX):-1]))
    assert raw == text.encode("utf-8")  # sorted keys, compact, raw UTF-8


def test_design_example_validates_unchanged():
    out, warnings = sc.validate(copy.deepcopy(EXAMPLE))
    assert out == EXAMPLE and warnings == []
    assert sc.decode(sc.encode(EXAMPLE)) == EXAMPLE
    parsed = packs.parse_pack(copy.deepcopy(EXAMPLE))
    assert parsed.issues == [] and parsed.disabled_ids == ["zone:1426"] and parsed.revision == 4


def test_pack_file_conversion_rules():
    data = {"format": "needledropper-pack", "version": 1, "name": "Mine", "drops": [
        {"zone": "Elwynn Forest", "artist": "A", "title": "T", "spotify": f"https://open.spotify.com/track/{SP_ID}?si=x",
         "startMs": 0, "appleMusic": "https://music.apple.com/x", "youtub": "typo"},
        {"zone": "STV", "subzone": "Booty Bay", "artist": "", "title": "draft"},
        {"zone": 1429, "title": "no artist"},
        {"zone": "Durotar", "artist": "B", "title": "U", "file": "C:\\Music\\song.mp3"},
        {"disabled": True, "zone": "Westfall"},
    ]}
    pack, warnings = sc.pack_for_sharing(data)
    assert pack["id"] == "mine" and pack["tags"] == [] and pack["author"] == ""
    d0, d1, d2 = pack["drops"]
    assert d0 == {"zone": 1429, "artist": "A", "title": "T", "spotify": f"spotify:track:{SP_ID}"}
    assert d1 == {"zone": 1411, "artist": "B", "title": "U", "file": "song.mp3", "inGameFile": True}
    assert d2 == {"disabled": True, "zone": 1436}
    joined = "\n".join(warnings)
    assert "2 unfinished drops were not included" in joined
    assert "'youtub'" in joined and "appleMusic" in joined and "song.mp3" in joined


# --------------------------------------------------------------- robustness --

def test_paste_variants_all_decode():
    want = sc.decode(MIXTAPE_CODE)
    body = MIXTAPE_CODE[len(sc.PREFIX):-1]
    wrapped = "\n".join(MIXTAPE_CODE[i:i + 76] for i in range(0, len(MIXTAPE_CODE), 76))
    urlsafe = sc.PREFIX + body.rstrip("=").replace("+", "-").replace("/", "_") + "!"
    zw = MIXTAPE_CODE[:30] + "\u200b" + MIXTAPE_CODE[30:60] + "\ufeff" + MIXTAPE_CODE[60:90] + "\u00a0" + \
        MIXTAPE_CODE[90:]
    variants = [
        f"Here you go:\n```\n{MIXTAPE_CODE}\n```\nenjoy!",
        wrapped,
        f"```{wrapped}```",
        urlsafe,
        zw,
        MIXTAPE_CODE.rstrip("!"),  # a missing final "!" is fine when the body is complete
        "  \t" + MIXTAPE_CODE + "\r\n",
        MIXTAPE_CODE[:-1].rstrip("=") + "!",  # padding dropped
    ]
    for v in variants:
        assert sc.decode(v) == want, v[:60]


def test_truncated_codes_say_cut_off():
    for cut in (len(MIXTAPE_CODE) // 2, len(MIXTAPE_CODE) - 10, len(sc.PREFIX) + 8):
        assert reason(MIXTAPE_CODE[:cut]) == "cutoff"
    # the same damage with the terminator present is "damaged"
    assert reason(MIXTAPE_CODE[:len(MIXTAPE_CODE) // 2] + "!") == "damaged"


def test_random_one_character_damage_never_imports_a_different_soundtrack():
    rng = random.Random(1202)
    want = sc.decode_detailed(MIXTAPE_CODE).normalized
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=!-_ :"
    silent, errors, same = 0, 0, 0
    for _ in range(1000):
        i = rng.randrange(len(MIXTAPE_CODE))
        c = rng.choice(alphabet.replace(MIXTAPE_CODE[i], ""))
        text = MIXTAPE_CODE[:i] + c + MIXTAPE_CODE[i + 1:]
        try:
            got = sc.decode_detailed(text).normalized
        except ShareCodeError as exc:
            assert exc.reason in ("notcode", "newer", "cutoff", "damaged", "invalid", "toobig") or \
                exc.reason.startswith("foreign:")
            errors += 1
            continue
        if got == want:
            same += 1  # e.g. whitespace inserted, or the terminator replaced: still the same soundtrack
        else:
            silent += 1
    assert silent == 0
    assert errors > 900


def test_size_caps(monkeypatch):
    assert reason("x" * 65537) == "toobig"
    assert sc.decode(MIXTAPE_CODE + " " * (65536 - len(MIXTAPE_CODE))) == sc.decode(MIXTAPE_CODE)  # at the cap
    assert reason(MIXTAPE_CODE + " " * (65537 - len(MIXTAPE_CODE))) == "toobig"
    # 49,152 payload bytes are 65,536 Base64 characters: the payload cap (kept for parity with the
    # addon) only bites when the text cap is relaxed
    big = sc.PREFIX + base64.b64encode(b"x" + bytes(49152)).decode("ascii") + "!"
    monkeypatch.setattr(sc, "MAX_TEXT_CHARS", 10 ** 6)
    assert reason(big) == "toobig"


def test_zlib_bomb_is_rejected_after_256_kib():
    bomb = zlib.compress(b"{" + b" " * 20_000_000 + b"}", 9)
    assert len(bomb) <= 49152
    assert reason(sc.PREFIX + base64.b64encode(bomb).decode("ascii") + "!") == "toobig"
    assert reason(sc.PREFIX + base64.b64encode(bomb).decode("ascii")) == "toobig"  # not "cut off"


@pytest.mark.parametrize("version,want", [("2", "newer"), ("10", "newer"), ("0", "damaged"), ("9" * 5000, "newer"),
                                          ("0001", None)])
def test_container_versions(version, want):
    text = MIXTAPE_CODE.replace("!NeedleDropper:1!", f"!NeedleDropper:{version}!", 1)
    if want is None:
        assert sc.decode(text) == sc.decode(MIXTAPE_CODE)
    else:
        assert reason(text) == want


@pytest.mark.parametrize("text,name", [
    ("!WA:2!abcdef", "WeakAuras"), ("look: !E1!abc", "ElvUI"), ("!E2!xyz", "ElvUI"), ("!PLATER:2!abc", "Plater"),
    ("!~MDT2~abc", "MDT"), ("xx D!ProfileV2-abc", "Details"), ("!CELL:abc", "Cell"), ("  BW2:abc", "BigWigs"),
    ("BWB1:abc", "BigWigs"), ("1|abc|def", "Blizzard"),
])
def test_foreign_strings_are_named(text, name):
    with pytest.raises(ShareCodeError) as ei:
        sc.decode(text)
    assert ei.value.reason == f"foreign:{name}" and ei.value.kind == "foreign" and ei.value.foreign_name == name
    assert f"This is a {name} string" in str(ei.value)


@pytest.mark.parametrize("text", ["hello", "", "say BW2: later", "version 1|2", "!NeedleDropper!abc!",
                                  "!NeedleDropper:x!abc!"])
def test_not_a_code(text):
    assert reason(text) == "notcode"


def test_our_code_wins_over_a_foreign_prefix_in_the_chatter():
    assert sc.decode("!WA: is what I used before; now try " + MIXTAPE_CODE) == sc.decode(MIXTAPE_CODE)


# ------------------------------------------------------------ hostile fields --

def _pack(**drop) -> dict:
    return {"format": "needledropper-pack", "version": 1, "id": "x", "name": "X", "tags": [],
            "drops": [{"zone": 1429, "artist": "A", "title": "T", **drop}]}


def test_ui_escape_sequences_are_kept_as_plain_text():
    title = "|TInterface\\Icons\\INV_Misc_Drum_01:0|t |cffff0000Africa|r"
    pack = sc.decode(sc.encode(_pack(title=title)))
    assert pack["drops"][0]["title"] == title  # the addon escapes "|" before showing it


def test_path_like_file_names_are_dropped():
    for bad in ("..\\..\\Wow.exe", "../x.mp3", "C:x.mp3", "a/b.mp3", "song.wav", "x" * 101 + ".mp3", "..mp3"):
        dec = sc.decode_detailed(sc.encode(_pack(file=bad, inGameFile=True)))
        assert "file" not in dec.normalized["drops"][0], bad
        assert any('"file"' in w for w in dec.warnings)
    ok = sc.decode_detailed(sc.encode(_pack(file="Goldshire (live) v2_final-1.ogg", inGameFile=True)))
    assert ok.normalized["drops"][0]["file"] == "Goldshire (live) v2_final-1.ogg"


@pytest.mark.parametrize("payload", [
    b'{"name":"X","drops":[],"n":NaN}', b'{"n":Infinity}', b'{"n":-Infinity}', b'{"n":1e400}', b'{"n":-1e999}',
    b'{"n":' + b"9" * 400 + b"}", b'{"t":"\\ud800"}', b'{"a":1,}', b"[1,2]", b'"text"', b"\xff\xfe{}",
])
def test_json_the_addon_rejects_is_damaged(payload):
    for compress in (True, False):
        if not compress and payload[:1] != b"{":
            continue
        assert reason(raw_code(payload, compress=compress)) == "damaged", payload[:30]


def test_nesting_depth_is_capped_at_6():
    deep = b'{"a":' * 200 + b"1" + b"}" * 200
    assert reason(raw_code(deep)) == "damaged"
    seven = b'{"a":[[[[[[1]]]]]]}'  # 7 levels
    assert reason(raw_code(seven)) == "damaged"
    ok = copy.deepcopy(EXAMPLE)  # 4 levels
    assert sc.decode(sc.encode(ok)) == ok
    huge = b"[" * 200_000 + b"]" * 200_000  # recursion must not crash the reader
    assert reason(raw_code(huge)) in ("damaged", "toobig")


def test_uncompressed_payload_is_accepted():
    text = json.dumps(EXAMPLE, separators=(",", ":")).encode("utf-8")
    dec = sc.decode_detailed(raw_code(text, compress=False))
    assert dec.pack == EXAMPLE and not dec.compressed


def test_utf8_bom_in_the_json_is_skipped():
    text = b"\xef\xbb\xbf" + json.dumps(EXAMPLE).encode("utf-8")
    assert sc.decode(raw_code(text)) == EXAMPLE


def test_integral_floats_become_ints_and_nulls_are_left_out():
    data = copy.deepcopy(EXAMPLE)
    raw = json.dumps(data).replace('"zone": 1429', '"zone": 1429.0').replace('"revision": 4', '"revision": 4e0')
    raw = raw.replace('"author": ""', '"author": "", "homepage": null')
    pack = sc.decode(raw_code(raw.encode("utf-8")))
    assert pack["drops"][0]["zone"] == 1429 and isinstance(pack["drops"][0]["zone"], int)
    assert pack["revision"] == 4 and "homepage" not in pack


def test_three_hundred_drops_is_the_maximum():
    def many(n: int) -> dict:
        return {"format": "needledropper-pack", "version": 1, "id": "big", "name": "Big", "tags": [],
                "drops": [{"areaID": 10000 + i, "zone": 1429, "artist": "Artist", "title": f"Song {i}",
                           "youtube": "QAo_Ycocl1E"} for i in range(n)]}

    code = sc.encode(many(300))
    assert len(sc.decode(code)["drops"]) == 300
    with pytest.raises(ShareCodeError) as ei:
        sc.decode(sc.encode(many(301)))
    assert ei.value.reason == "invalid" and "1 to 300 drops" in " ".join(ei.value.issues)


# --------------------------------------------------------------- validation --

@pytest.mark.parametrize("change,needle", [
    (lambda p: p["drops"].append(dict(p["drops"][1])), "listed twice"),
    (lambda p: p["drops"][0].update(zone="Elwynn Forest"), "uiMapID number"),
    (lambda p: p["drops"][0].update(zone=0), "from 1 to"),
    (lambda p: p["drops"][0].update(areaID=2 ** 31), "from 1 to"),
    (lambda p: p["drops"][0].update(title="t" * 201), "at most 200"),
    (lambda p: p["drops"][0].update(note="n" * 501), "at most 500"),
    (lambda p: p["drops"][0].update(label="l" * 101), "at most 100"),
    (lambda p: p.update(name="n" * 65), "at most 64"),
    (lambda p: p.update(name=""), '"name"'),
    (lambda p: p.update(description="d" * 1001), "at most 1000"),
    (lambda p: p.update(homepage="ftp://x"), "homepage"),
    (lambda p: p.update(tags=[f"t{i}" for i in range(9)]), "at most 8 tags"),
    (lambda p: p.update(tags=["x" * 25]), "longer than 24"),
    (lambda p: p.update(tags=["two\nlines"]), "single line"),
    (lambda p: p.update(id="Not An Id"), '"id"'),
    (lambda p: p.update(revision=-1), "revision"),
    (lambda p: p.update(version=2), "version"),
    (lambda p: p.update(format="other"), "format"),
    (lambda p: p["drops"][1].update(spotify="spotify:album:" + "a" * 22), "spotify"),
    (lambda p: p["drops"][0].update(youtube="short"), "youtube"),
    (lambda p: p["drops"][1].update(volume=101), "volume"),
    (lambda p: p["drops"][1].update(durationMs=-1), "durationMs"),
    (lambda p: p["drops"][2].update(disabled="yes"), "disabled"),
    (lambda p: p.update(drops=[]), "1 to 300 drops"),
    (lambda p: p["drops"][0].pop("title"), "title"),
])
def test_invalid_soundtracks(change, needle):
    pack = copy.deepcopy(EXAMPLE)
    change(pack)
    with pytest.raises(ShareCodeError) as ei:
        sc.decode(sc.encode(pack))
    assert ei.value.reason == "invalid"
    assert needle in " ".join(ei.value.issues), ei.value.issues


def test_unknown_keys_and_control_characters_are_dropped_with_warnings():
    pack = copy.deepcopy(EXAMPLE)
    pack["future"] = 1
    pack["_comment"] = "ignored silently"
    pack["drops"][0]["mood"] = "sunny"
    pack["drops"][0]["title"] = "Afr\x07ica"
    pack["description"] = "line one\nline\ttwo"
    dec = sc.decode_detailed(sc.encode(pack))
    n = dec.normalized
    assert "future" not in n and "mood" not in n["drops"][0] and "_comment" not in n
    assert n["drops"][0]["title"] == "Africa" and n["description"] == "line one\nlinetwo"
    assert any("'future'" in w for w in dec.warnings) and any("'mood'" in w for w in dec.warnings)
    assert not any("_comment" in w for w in dec.warnings)
    assert dec.pack["future"] == 1  # decode() itself returns what the code holds


def test_turned_off_entry_without_a_place_is_dropped():
    pack = copy.deepcopy(EXAMPLE)
    pack["drops"].append({"disabled": True})
    dec = sc.decode_detailed(sc.encode(pack))
    assert len(dec.normalized["drops"]) == 4 and any("turned-off" in w for w in dec.warnings)


def test_encode_refuses_what_json_cannot_carry():
    bad = copy.deepcopy(EXAMPLE)
    bad["drops"][0]["volume"] = float("nan")
    with pytest.raises(ShareCodeError) as ei:
        sc.encode(bad)
    assert ei.value.reason == "invalid"
    bad["drops"][0]["volume"] = "x\ud800"
    with pytest.raises(ShareCodeError):
        sc.encode(bad)
    with pytest.raises(ShareCodeError) as ei:
        sc.encode({"blob": "x" * 300000})
    assert ei.value.reason == "toobig"


def test_error_messages_are_plain_english():
    assert sc.describe_reason("cutoff").startswith("The code was cut off")
    assert sc.describe_reason("newer").endswith("update the addon.")
    e = ShareCodeError("invalid", issues=["a", "b", "c", "d"])
    assert "a; b; c (+1 more)" in str(e) and e.kind == "invalid" and e.foreign_name is None
    assert set(sc.REASONS) == {"notcode", "foreign", "newer", "cutoff", "damaged", "toobig", "invalid"}


def test_find_code():
    assert sc.find_code(f"x\n```\n{MIXTAPE_CODE[:50]}\n{MIXTAPE_CODE[50:]}\n```") == MIXTAPE_CODE
    assert sc.find_code("nothing") is None and sc.find_code(None) is None  # type: ignore[arg-type]


# -------------------------------------------------------- Lua interop (lupa) --

class LuaCodecs:
    """The addon's Base64.lua and Json.lua in a bytes-mode Lua 5.1 runtime."""

    def __init__(self) -> None:
        self.rt = lua51.LuaRuntime(unpack_returned_tuples=True, encoding=None)
        loader = self.rt.execute("return function(src, name, ns) local f = assert(loadstring(src, name)); "
                                 "f('NeedleDropper', ns) end")
        self.ns = self.rt.table()
        for name in ("Json.lua", "Base64.lua"):
            loader((ADDON / name).read_bytes(), ("@" + name).encode(), self.ns)
        self.call = self.rt.eval("function(f, ...) return f(...) end")
        self.decode_json = self.rt.eval(
            "function(J, s) local v, e = J.Decode(s, { maxBytes = 262144, maxDepth = 6 }) "
            "if v == nil then return nil, e end return J.Encode(v, { sortKeys = true }) end")

    def b64_decode(self, text: str) -> bytes:
        out = self.call(self.ns[b"Base64"][b"Decode"], text.encode("ascii"))
        return out[0] if isinstance(out, tuple) else out

    def b64_encode(self, data: bytes) -> bytes:
        out = self.call(self.ns[b"Base64"][b"Encode"], data, False, True)
        return out[0] if isinstance(out, tuple) else out

    def json_roundtrip(self, data: bytes) -> bytes:
        out = self.decode_json(self.ns[b"Json"], data)
        value, err = (out if isinstance(out, tuple) else (out, None))
        assert value is not None, err
        return value


@pytest.fixture(scope="module")
def lua():
    if not (ADDON / "Base64.lua").is_file() or not (ADDON / "Json.lua").is_file():
        pytest.skip("the addon's codecs are not available")
    return LuaCodecs()


@pytest.mark.parametrize("name", ["azeroth-mixtape.json", "azeroth-unbound.json"])
def test_python_codes_read_back_identically_through_the_addons_codecs(lua, name):
    """Base64.lua decodes our body to the same bytes, and Json.lua reads the inflated JSON to a value
    that re-encodes (sorted keys) to exactly our JSON text: the addon sees the same soundtrack."""
    pack, _ = sc.pack_for_sharing(load(name))
    code = sc.encode(pack)
    body = code[len(sc.PREFIX):-1]
    blob = lua.b64_decode(body)
    assert blob == base64.b64decode(body)
    raw = zlib.decompress(blob)  # the client's zlib is native; Python's stands in for it here
    assert lua.json_roundtrip(raw) == raw


def test_codes_built_with_the_addons_codecs_decode_here(lua):
    text = json.dumps(EXAMPLE, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    lua_json = lua.json_roundtrip(text)  # Json.lua's own writer output
    for payload in (zlib.compress(lua_json, 9), lua_json):
        code = sc.PREFIX + lua.b64_encode(payload).decode("ascii") + "!"
        assert sc.decode(code) == EXAMPLE


def test_shared_vectors_never_crash_the_reader():
    """Golden vectors written by the addon side (tests/vectors/share, when present) decode or fail
    with one of the documented reasons."""
    if not VECTORS.is_dir():
        pytest.skip("no shared vectors yet")
    seen = 0
    for path in sorted(VECTORS.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".txt", ".code", ""):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "!NeedleDropper:" not in text:
            continue
        seen += 1
        try:
            sc.decode(text)
        except ShareCodeError as exc:
            assert exc.kind in sc.REASONS, path
    if not seen:
        pytest.skip("no share codes among the vectors")
