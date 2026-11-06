"""Share-code robustness (DESIGN-v1.2 sections 5.1, 5.3, 5.5 and 6.3): whatever players paste,
Share.Decode either returns the soundtrack that was shared or says why not -- never a silently
wrong soundtrack -- and hostile codes are refused cheaply."""
from __future__ import annotations

import base64
import json
import random
import time
import zlib
from collections import Counter

import pytest

from v12_model_harness import (PREFIX, BytesRuntime, canon, discord_fenced, ref_code_uncompressed, ref_decode,
                               ref_encode, urlsafe_unpadded, with_zero_width, wrapped)

REASONS = {"notcode", "newer", "cutoff", "damaged", "toobig", "invalid"}

PACK = {
    "author": "Ethan", "description": "Songs for my alts", "drops": [
        {"areaID": 87, "artist": "Toto", "label": "Goldshire", "subzone": "Goldshire", "tags": ["funny"],
         "title": "Africa", "youtube": "QAo_Ycocl1E", "zone": 1429},
        {"artist": "Low Roar", "durationMs": 352000, "spotify": "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
         "title": "I'll Keep Coming", "zone": 1429},
        {"disabled": True, "zone": 1426},
        {"artist": "Me", "file": "stormwind.mp3", "inGameFile": True, "title": "Home", "zone": 1453}],
    "format": "needledropper-pack", "id": "my-drops", "name": "My Drops", "revision": 4, "tags": [], "version": 1}


@pytest.fixture(scope="module")
def rt():
    return BytesRuntime()


@pytest.fixture(scope="module")
def code():
    return ref_encode(PACK)


@pytest.fixture(scope="module")
def mixtape(rt):
    pack, _ = rt.call("Share.FromSet", "builtin:azeroth-mixtape")
    pack = canon(pack)
    return ref_encode(pack), pack


def ok(rt, text, pack=PACK):
    got, warnings, detail = rt.decode(text)
    assert got is not None, f"refused: {warnings!r} {detail!r}"
    assert canon(got) == pack
    return got


def reason(rt, text):
    got, why, detail = rt.decode(text)
    assert got is None, "expected a refusal"
    return why


def mk(pack: dict, compress=True) -> str:
    raw = json.dumps(pack, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    blob = zlib.compress(raw, 9) if compress else raw
    return PREFIX + base64.b64encode(blob).decode("ascii") + "!"


def mk_raw(json_text: str, compress=True) -> str:
    raw = json_text.encode("utf-8")
    blob = zlib.compress(raw, 9) if compress else raw
    return PREFIX + base64.b64encode(blob).decode("ascii") + "!"


# ---------------------------------------------------------------- pasting --

def test_discord_fences_and_chatter(rt, code):
    ok(rt, discord_fenced(code))
    ok(rt, "```" + code + "```")
    ok(rt, "> quoted: " + code + " <- that one")
    ok(rt, code + "!!! thanks!")  # the body ends at its own "!"


@pytest.mark.parametrize("width", [1, 4, 13, 64, 76])
@pytest.mark.parametrize("newline", ["\n", "\r\n", " \n  ", "\t"])
def test_wrapped_bodies(rt, code, width, newline):
    text = wrapped(code, width, newline)
    ok(rt, text)
    assert ref_decode(text) == PACK


def test_zero_width_characters_and_no_break_spaces(rt, code):
    ok(rt, with_zero_width(code, 7))
    ok(rt, "​".join(code))  # one between every character, prefix included
    ok(rt, " " + code + " ")


def test_url_safe_alphabet_and_padding(rt, code):
    ok(rt, urlsafe_unpadded(code))
    body = code[len(PREFIX):-1]
    if body.endswith("=="):
        ok(rt, PREFIX + body[:-1] + "!")  # partial padding
    mixed = PREFIX + body.replace("+", "-", 1).replace("/", "_", 1) + "!"
    ok(rt, mixed)


def test_uncompressed_json_payload_is_read(rt):
    ok(rt, ref_code_uncompressed(PACK))


def test_a_missing_final_bang_still_decodes(rt, code):
    ok(rt, code[:-1])
    ok(rt, code[:-1] + "\n```")


# ------------------------------------------------------------- truncation --

def test_truncated_codes_say_cut_off(rt, mixtape):
    text, pack = mixtape
    rng = random.Random(7)
    cuts = sorted({len(PREFIX), len(PREFIX) + 1, len(PREFIX) + 2, len(PREFIX) + 3, len(text) - 2, len(text) // 2}
                  | {rng.randrange(len(PREFIX), len(text) - 1) for _ in range(40)})
    for cut in cuts:
        assert reason(rt, text[:cut]) == "cutoff", cut
        with pytest.raises(Exception) as exc:
            ref_decode(text[:cut])
        assert exc.value.reason == "cutoff"
    for cut in range(1, len(PREFIX)):
        assert reason(rt, text[:cut]) == "notcode"


# --------------------------------------------------------------- mutations --

def test_1000_one_character_changes_never_give_a_wrong_soundtrack(rt, code, mixtape):
    rng = random.Random(1202)
    alphabet = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=-_" + "! \n:{}\"é​")
    codes = [(code, PACK), mixtape]
    silent, outcomes = [], Counter()
    for k in range(1000):
        text, pack = codes[k % 2]
        pos = rng.randrange(len(text))
        ch = rng.choice(alphabet)
        while ch == text[pos]:
            ch = rng.choice(alphabet)
        mutated = text[:pos] + ch + text[pos + 1:]
        got, why, _ = rt.decode(mutated)
        if got is None:
            assert why in REASONS or why.startswith("foreign:"), why
            outcomes[why] += 1
        elif canon(got) == pack:
            outcomes["same soundtrack"] += 1
        else:
            silent.append((k, pos, ch))
    assert silent == [], f"silently wrong soundtracks: {silent[:5]}"
    assert outcomes["damaged"] + outcomes["cutoff"] > 900, outcomes


# -------------------------------------------------------------- size caps --

def test_pasted_text_over_64k_is_too_big(rt, code):
    assert reason(rt, code + " " * (65537 - len(code))) == "toobig"
    ok(rt, code + " " * (65536 - len(code)))


def test_more_than_48k_of_data_is_too_big(rt):
    blob = b"\x78\xda" + bytes(random.Random(3).randbytes(49200))
    text = PREFIX + base64.b64encode(blob).decode("ascii") + "!"
    rt.rt.execute(b"local ns = ... ns.Share.MAX_TEXT = 1000000", rt.ns)  # past the text cap on purpose
    try:
        assert reason(rt, text) == "toobig"
    finally:
        rt.rt.execute(b"local ns = ... ns.Share.MAX_TEXT = 65536", rt.ns)


def test_json_over_256k_is_too_big(rt):
    padded = json.dumps(PACK)[:-1] + ',"description2":"' + "x" * 270000 + '"}'
    assert reason(rt, mk_raw(padded)) == "toobig"


def test_a_zlib_bomb_is_refused(rt):
    bomb = zlib.compress(b"{" + b" " * 50_000_000 + b"}", 9)
    assert len(bomb) <= 49152
    text = PREFIX + base64.b64encode(bomb).decode("ascii") + "!"
    assert len(text) <= 65536
    t0 = time.perf_counter()
    assert reason(rt, text) == "toobig"
    assert time.perf_counter() - t0 < 5
    with pytest.raises(Exception) as exc:
        ref_decode(text)
    assert exc.value.reason == "toobig"


def test_the_biggest_soundtrack_still_fits(rt):
    drops = [{"zone": 10000 + i, "artist": "Artist %d" % i, "title": "A song title of fair length %d" % i,
              "spotify": "spotify:track:4uLU6hMCjMI75M1A2tKUQC", "youtube": "QAo_Ycocl1E", "startMs": 15000,
              "durationMs": 240000, "label": "Some Place Name %d" % i, "tags": ["fitting"],
              "note": "Why it fits, in a sentence or two of ordinary length for a note."} for i in range(300)]
    pack = dict(PACK, drops=drops)
    text, err = rt.encode(pack)
    assert err is None and len(text) < 65536
    ok(rt, text, canon(rt.call1("Share.Validate", pack)))


def test_encode_refuses_what_the_reader_would_refuse(rt):
    rng = random.Random(9)

    def noise(n):
        return "".join(rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJ0123456789") for _ in range(n))

    drops = [{"zone": 20000 + i, "artist": noise(200), "title": noise(200), "label": noise(100),
              "note": noise(500), "subzone": noise(100)} for i in range(300)]
    code, err = rt.encode(dict(PACK, drops=drops))
    assert code is None and "too big" in err


# ---------------------------------------------------------------- versions --

def test_newer_and_odd_container_versions(rt, code):
    assert reason(rt, code.replace("!NeedleDropper:1!", "!NeedleDropper:2!")) == "newer"
    assert reason(rt, code.replace("!NeedleDropper:1!", "!NeedleDropper:99999999999999999999!")) == "newer"
    assert reason(rt, code.replace("!NeedleDropper:1!", "!NeedleDropper:0!")) == "damaged"
    ok(rt, code.replace("!NeedleDropper:1!", "!NeedleDropper:01!"))
    assert reason(rt, mk(dict(PACK, version=2))) == "newer"


# ---------------------------------------------------------- other strings --

FOREIGN = [
    ("!WA:2!S3xYZabc", "WeakAuras"), ("!E1!abcdef", "ElvUI"), ("!E2!abcdef", "ElvUI"),
    ("!PLATER:2!abcdef", "Plater"), ("!~MDT2~abcdef", "MDT"), ("D!ProfileV2-abcdef", "Details"),
    ("!CELL:abcdef", "Cell"), ("BW2:abcdef", "BigWigs"), ("BWB1:abcdef", "BigWigs"), ("1|abcdef", "Blizzard"),
    ("   \n BW2:abcdef", "BigWigs"), ("look at this: !WA:2!xyz", "WeakAuras"),
]


@pytest.mark.parametrize("text, name", FOREIGN)
def test_other_addons_strings_are_named(rt, text, name):
    assert reason(rt, text) == "foreign:" + name
    with pytest.raises(Exception) as exc:
        ref_decode(text)
    assert exc.value.reason == "foreign:" + name


@pytest.mark.parametrize("text", ["", "hello", "I have 1|2 cats and BW2: is a band", "!NeedleDropper!x!",
                                  "!needledropper:1!abc!", "NeedleDropper:1!abc!"])
def test_ordinary_text_is_not_a_code(rt, text):
    assert reason(rt, text) == "notcode"


def test_non_strings_are_not_codes(rt):
    assert rt.call("Share.Decode", 42)[:2] == (None, "notcode")
    assert rt.call("Share.Decode", None)[:2] == (None, "notcode")


# ---------------------------------------------------------- hostile fields --

def test_escape_sequences_stay_plain_text(rt):
    pack = dict(PACK, name="|cffff0000Red|r", drops=[{"zone": 1429, "artist": "|TInterface\\Icons\\X:0|t",
                                                      "title": "|Hitem:1|h[Link]|h"}])
    got = ok(rt, mk(pack), pack)
    assert got["drops"][0]["artist"] == "|TInterface\\Icons\\X:0|t"
    esc = rt.call1("EscapeText", got["drops"][0]["artist"])
    assert esc == "||TInterface\\Icons\\X:0||t"


def test_a_path_in_the_file_name_is_dropped_with_a_warning(rt):
    pack = dict(PACK, drops=[{"zone": 1429, "artist": "a", "title": "t", "file": "..\\..\\Wow.exe", "inGameFile": True}])
    got, warnings, _ = rt.decode(mk(pack))
    assert "file" not in got["drops"][0] and "inGameFile" not in got["drops"][0]
    assert any("Wow.exe" in w for w in warnings)


@pytest.mark.parametrize("json_text", [
    '{"name":"x","drops":[{"zone":NaN,"artist":"a","title":"t"}]}',
    '{"name":"x","drops":[{"zone":Infinity,"artist":"a","title":"t"}]}',
    '{"name":"x","drops":[{"zone":1e400,"artist":"a","title":"t"}]}',
    '{"name":"x","drops":[{"zone":1429,"artist":"a","title":"\\ud800"}]}',
    '{"name":"x","drops":' + "[" * 200 + "]" * 200 + "}",
    '{"name":"x",}',
    '[1,2,3]',
    '"just a string"',
    'null',
])
def test_malformed_json_is_damaged(rt, json_text):
    for compress in (True, False):
        text = mk_raw(json_text, compress)
        assert reason(rt, text) == "damaged", (json_text[:40], compress)


def test_invalid_utf8_is_damaged(rt):
    blob = zlib.compress(b'{"name":"\xff\xfe","drops":[]}', 9)
    assert reason(rt, PREFIX + base64.b64encode(blob).decode() + "!") == "damaged"


def test_the_300_drop_cap(rt):
    drops = [{"zone": 30000 + i, "artist": "a", "title": "t"} for i in range(300)]
    ok(rt, mk(dict(PACK, drops=drops)), canon(rt.call1("Share.Validate", dict(PACK, drops=drops))))
    drops.append({"zone": 40000, "artist": "a", "title": "t"})
    got, why, detail = rt.decode(mk(dict(PACK, drops=drops)))
    assert got is None and why == "invalid" and "300" in detail


def test_huge_hostile_fields_are_refused_quickly(rt):
    spaces = "a" + " " * 60000 + "b"
    for field in ("spotify", "youtube", "file", "title", "label"):
        pack = dict(PACK, drops=[{"zone": 1429, "artist": "a", "title": "t", field: spaces}])
        t0 = time.perf_counter()
        got, why, _ = rt.decode(mk(pack))
        assert time.perf_counter() - t0 < 1.0, field
        assert got is None or field == "file", field  # a bad file name is dropped with a warning


def test_invalid_fields_give_a_reason_naming_the_drop(rt):
    pack = dict(PACK, drops=[{"zone": 1429, "artist": "a", "title": "t" * 201, "label": "Goldshire"}])
    got, why, detail = rt.decode(mk(pack))
    assert got is None and why == "invalid" and "Goldshire" in detail and "200" in detail
    assert "Goldshire" in rt.call1("Share.ReasonText", why, detail)


def test_reason_texts(rt):
    for r in ("notcode", "foreign:WeakAuras", "newer", "cutoff", "damaged", "toobig", "invalid", "???"):
        text = rt.call1("Share.ReasonText", r)
        assert isinstance(text, str) and text
    assert "WeakAuras" in rt.call1("Share.ReasonText", "foreign:WeakAuras")


# ------------------------------------------------- the client's zlib behaviour --

@pytest.mark.parametrize("fail_mode", ["error", "nil"])
def test_both_failure_styles_of_the_client_compressor(fail_mode, code):
    rt = BytesRuntime(fail_mode=fail_mode)
    ok(rt, code)
    assert reason(rt, code[:-12] + "!") in ("damaged", "cutoff")
    assert reason(rt, code[:-12]) == "cutoff"
    body = code[len(PREFIX):-1]
    flipped = PREFIX + body[:30] + ("A" if body[30] != "A" else "B") + body[31:] + "!"
    assert reason(rt, flipped) == "damaged"
    new_code, err = rt.encode(PACK)
    assert err is None and ref_decode(new_code) == PACK


def test_without_c_encoding_util(code):
    rt = BytesRuntime(encoding_absent=True)
    new_code, err = rt.encode(PACK)
    assert new_code is None and "compressor" in err
    got, why, detail = rt.decode(code)
    assert got is None and why == "damaged" and "unpack" in detail
    ok(rt, ref_code_uncompressed(PACK))


def test_the_encoder_checks_its_own_output():
    rt = BytesRuntime()
    rt.rt.execute(b"local real = C_EncodingUtil.CompressString\n"
                  b"C_EncodingUtil.CompressString = function(s, m, l) return real(s, m, l):sub(1, -3) end")
    code, err = rt.encode(PACK)
    assert code is None and "self-check" in err
    rt2 = BytesRuntime()
    rt2.rt.execute(b"C_EncodingUtil.CompressString = function() return nil end")
    assert rt2.encode(PACK)[0] is None
    rt3 = BytesRuntime()
    rt3.rt.execute(b"C_EncodingUtil.DecompressString = function(s) return '{}' end")
    assert rt3.encode(PACK)[0] is None


def test_encode_uses_zlib_at_the_highest_compression():
    rt = BytesRuntime()
    rt.encode(PACK)
    calls = rt.to_py(rt.shim[b"log"][b"encoding"])
    assert calls[0]["op"] == "compress" and calls[0]["method"] == 1 and calls[0]["level"] == 2
    assert calls[1]["op"] == "decompress" and calls[1]["method"] == 1
