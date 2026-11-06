"""Tests for ``needledropper.savedvars`` (safe SavedVariables reader).

Lua 5.1 itself (lupa.lua51, sandboxed with an empty environment) is the oracle:
whenever the reader accepts a file, executing that file must produce exactly the
same globals. The optional last section checks the real files of a local WoW
install read-only and is skipped when the game is not installed.
"""
from __future__ import annotations

import builtins
import io
import math
import os
import random
import re
import time
from pathlib import Path

import pytest

from needledropper import savedvars as sv
from needledropper.savedvars import SavedVariablesError, parse_savedvariables as parse

import lupa.lua51 as lua51

# ------------------------------------------------------------- lua oracle --

_LUA = lua51.LuaRuntime(encoding=None, unpack_returned_tuples=True, register_eval=False,
                        register_builtins=False)
_RUN = _LUA.eval("""
function(src)
  local f, err = loadstring(src, "=sv")
  if not f then return false, err end
  local env = {}
  setfenv(f, env)          -- nothing but the file's own assignments is reachable
  local ok, e2 = pcall(f)
  if not ok then return false, e2 end
  return true, env
end
""")


def _from_lua(value):
    """A Lua value as the reader maps it, with strings left as bytes."""
    if lua51.lua_type(value) == "table":
        d = {_from_lua(k): _from_lua(v) for k, v in value.items()}
        if d and all(type(k) is int for k in d) and min(d) == 1 and max(d) == len(d):
            return [d[i] for i in range(1, len(d) + 1)]
        return d
    return value


def lua_globals(data: bytes):
    """Execute ``data`` in an empty Lua 5.1 environment; ``(ok, globals or error bytes)``."""
    ok, env = _RUN(data)
    if not ok:
        return False, env
    return True, {k.decode("utf-8", "surrogateescape"): _from_lua(v) for k, v in env.items()}


def as_bytes(value):
    """The reader's output with strings turned back into their exact bytes."""
    if isinstance(value, str):
        return value.encode("utf-8", "surrogateescape")
    if isinstance(value, list):
        return [as_bytes(v) for v in value]
    if isinstance(value, dict):
        return {as_bytes(k): as_bytes(v) for k, v in value.items()}
    return value


def has_non_finite(value) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, list):
        return any(has_non_finite(v) for v in value)
    if isinstance(value, dict):
        return any(has_non_finite(k) or has_non_finite(v) for k, v in value.items())
    return False


def assert_same_as_lua(data: bytes, **kw):
    """Parse ``data`` (lossless strings) and require Lua to agree exactly."""
    ours = parse(data, string_errors="surrogateescape", **kw)
    ok, theirs = lua_globals(data)
    assert ok, f"the reader accepted what Lua rejects ({theirs!r}): {data[:200]!r}"
    mine = {as_bytes(k).decode("utf-8", "surrogateescape"): as_bytes(v) for k, v in ours.items() if v is not None}
    assert mine == theirs, data[:300]
    return ours


def first_difference(a, b, path="$"):
    """A short description of where two parsed values differ (never prints the values)."""
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        if set(a) != set(b):
            return f"{path}: keys differ ({len(set(a) ^ set(b))} keys)"
        for k in a:
            d = first_difference(a[k], b[k], f"{path}[{k!r:.40}]")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return None
    return None if a == b else f"{path}: values differ"


# ------------------------------------------------- realistic client files --

# The 12.x client (WoW: Forever): CRLF, a leading blank line, no indentation,
# no "-- [n]" markers, "nil," holes in sparse arrays, functions skipped.
MODERN = (
    b"\r\n"
    b"NeedleDropperDB = {\r\n"
    b"[\"schema\"] = 1,\r\n"
    b"[\"settings\"] = {\r\n"
    b"[\"enabled\"] = true,\r\n"
    b"[\"dwellSec\"] = 10,\r\n"
    b"[\"toastScale\"] = 1.05,\r\n"
    b"[\"toastY\"] = 0.22,\r\n"
    b"[\"historyMode\"] = \"respect\",\r\n"
    b"[\"chatlog\"] = false,\r\n"
    b"[\"maxDropSec\"] = 300,\r\n"
    b"},\r\n"
    b"[\"accountDone\"] = {\r\n"
    b"[\"zone:1429\"] = {\r\n"
    b"[\"t\"] = 1762380000,\r\n"
    b"[\"how\"] = \"played\",\r\n"
    b"},\r\n"
    b"},\r\n"
    b"[\"marks\"] = {\r\n"
    b"{\r\n"
    b"[\"mapID\"] = 1429,\r\n"
    b"[\"zone\"] = \"Elwynn Forest\",\r\n"
    b"[\"subzone\"] = \"Lion's Pride Inn\",\r\n"
    b"[\"areaID\"] = 87,\r\n"
    b"[\"t\"] = 1762380000,\r\n"
    b"[\"note\"] = \"the \\\"best\\\" inn\\nsecond line \\\\ done\",\r\n"
    b"},\r\n"
    b"{\r\n"
    b"[\"mapID\"] = 1453,\r\n"
    b"[\"zone\"] = \"Hurlevent\",\r\n"
    b"[\"subzone\"] = \"Quartier des Mages\",\r\n"
    b"[\"t\"] = 1762300000,\r\n"
    b"[\"note\"] = \"caf\xc3\xa9 \xe6\x97\xa5\xe6\x9c\xac \xf0\x9f\x8e\xb5\",\r\n"
    b"},\r\n"
    b"},\r\n"
    b"[\"sets\"] = {\r\n"
    b"[\"my-mixtape\"] = {\r\n"
    b"[\"name\"] = \"My \\\"Mixtape\\\"\",\r\n"
    b"[\"drops\"] = {\r\n"
    b"{\r\n"
    b"[\"id\"] = \"zone:1429\",\r\n"
    b"[\"title\"] = \"I'll Keep Coming\",\r\n"
    b"[\"artist\"] = \"Low Roar\",\r\n"
    b"[\"spotify\"] = \"4uLU6hMCjMI75M1A2tKUQC\",\r\n"
    b"[\"startMs\"] = 0,\r\n"
    b"[\"durationMs\"] = 245000,\r\n"
    b"},\r\n"
    b"{\r\n"
    b"[\"id\"] = \"sub:1429:lion-s-pride-inn\",\r\n"
    b"[\"title\"] = \"Tavern \\\\ Song\\r\\n\",\r\n"
    b"[\"artist\"] = \"Bj\xc3\xb6rk\",\r\n"
    b"[\"youtube\"] = \"dQw4w9WgXcQ\",\r\n"
    b"[\"volume\"] = -0.5,\r\n"
    b"},\r\n"
    b"},\r\n"
    b"},\r\n"
    b"},\r\n"
    b"[\"schoolColoring\"] = {\r\n"
    b"{\r\n"
    b"[\"a\"] = 1,\r\n"
    b"[\"r\"] = 1,\r\n"
    b"},\r\n"
    b"{\r\n"
    b"[\"a\"] = 1,\r\n"
    b"[\"b\"] = 0.5,\r\n"
    b"},\r\n"
    b"nil,\r\n"
    b"{\r\n"
    b"[\"g\"] = 0.05,\r\n"
    b"},\r\n"
    b"[0] = {\r\n"
    b"[\"r\"] = 1,\r\n"
    b"},\r\n"
    b"[8] = {\r\n"
    b"[\"r\"] = 0.3,\r\n"
    b"},\r\n"
    b"},\r\n"
    b"[\"unitColoring\"] = {\r\n"
    b"[2147483648] = 4.57763671875e-05,\r\n"
    b"[32334] = -3.0517578125e-05,\r\n"
    b"},\r\n"
    b"[\"callbacks\"] = {\r\n"
    b"[\"onclick\"] = nil --[[ skipped inline function ]],\r\n"
    b"[\"keep\"] = true,\r\n"
    b"},\r\n"
    b"}\r\n"
    b"NeedleDropper_Unset = nil\r\n"
)

MODERN_EXPECTED = {
    "NeedleDropperDB": {
        "schema": 1,
        "settings": {"enabled": True, "dwellSec": 10, "toastScale": 1.05, "toastY": 0.22,
                     "historyMode": "respect", "chatlog": False, "maxDropSec": 300},
        "accountDone": {"zone:1429": {"t": 1762380000, "how": "played"}},
        "marks": [
            {"mapID": 1429, "zone": "Elwynn Forest", "subzone": "Lion's Pride Inn", "areaID": 87,
             "t": 1762380000, "note": 'the "best" inn\nsecond line \\ done'},
            {"mapID": 1453, "zone": "Hurlevent", "subzone": "Quartier des Mages", "t": 1762300000,
             "note": "caf\u00e9 \u65e5\u672c \U0001F3B5"},
        ],
        "sets": {"my-mixtape": {"name": 'My "Mixtape"', "drops": [
            {"id": "zone:1429", "title": "I'll Keep Coming", "artist": "Low Roar",
             "spotify": "4uLU6hMCjMI75M1A2tKUQC", "startMs": 0, "durationMs": 245000},
            {"id": "sub:1429:lion-s-pride-inn", "title": "Tavern \\ Song\r\n", "artist": "Bj\u00f6rk",
             "youtube": "dQw4w9WgXcQ", "volume": -0.5},
        ]}},
        "schoolColoring": {1: {"a": 1, "r": 1}, 2: {"a": 1, "b": 0.5}, 4: {"g": 0.05}, 0: {"r": 1},
                           8: {"r": 0.3}},
        "unitColoring": {2147483648: 4.57763671875e-05, 32334: -3.0517578125e-05},
        "callbacks": {"keep": True},
    },
    "NeedleDropper_Unset": None,
}

# Older clients (Classic Era 1.15 and earlier retail): tabs and "-- [n]" markers.
CLASSIC = (
    b"\r\n"
    b"NeedleDropperCharDB = {\r\n"
    b"\t[\"schema\"] = 1,\r\n"
    b"\t[\"done\"] = {\r\n"
    b"\t\t[\"zone:1429\"] = {\r\n"
    b"\t\t\t[\"t\"] = 1762380000,\r\n"
    b"\t\t\t[\"how\"] = \"played\",\r\n"
    b"\t\t},\r\n"
    b"\t\t[\"area:87\"] = {\r\n"
    b"\t\t\t[\"t\"] = 1762380100,\r\n"
    b"\t\t\t[\"how\"] = \"history\",\r\n"
    b"\t\t},\r\n"
    b"\t},\r\n"
    b"\t[\"scanned\"] = {\r\n"
    b"\t\t[\"zone:1429\"] = true,\r\n"
    b"\t},\r\n"
    b"\t[\"journal\"] = {\r\n"
    b"\t\t{\r\n"
    b"\t\t\t[\"id\"] = \"zone:1429\",\r\n"
    b"\t\t\t[\"t\"] = 1762380000,\r\n"
    b"\t\t\t[\"label\"] = \"Elwynn Forest\",\r\n"
    b"\t\t\t[\"title\"] = \"I'll Keep Coming\",\r\n"
    b"\t\t\t[\"artist\"] = \"Low Roar\",\r\n"
    b"\t\t}, -- [1]\r\n"
    b"\t\t{\r\n"
    b"\t\t\t[\"id\"] = \"inst:36\",\r\n"
    b"\t\t\t[\"t\"] = 1762390000,\r\n"
    b"\t\t\t[\"label\"] = \"The Deadmines\",\r\n"
    b"\t\t\t[\"title\"] = \"Ship \\\"Wreck\\\"\",\r\n"
    b"\t\t\t[\"artist\"] = \"Sigur R\xc3\xb3s\",\r\n"
    b"\t\t}, -- [2]\r\n"
    b"\t},\r\n"
    b"\t[\"lastFireAt\"] = 1762390000,\r\n"
    b"\t[\"position\"] = {\r\n"
    b"\t\t108.2961502075195, -- [1]\r\n"
    b"\t\t-3.0517578125e-05, -- [2]\r\n"
    b"\t\t\"CENTER\", -- [3]\r\n"
    b"\t\t\"\\000\\000\x02\x01raw\x7fcontrols\\r\", -- [4]\r\n"
    b"\t},\r\n"
    b"\t[\"empty\"] = {\r\n"
    b"\t},\r\n"
    b"}\r\n"
)

CLASSIC_EXPECTED = {
    "NeedleDropperCharDB": {
        "schema": 1,
        "done": {"zone:1429": {"t": 1762380000, "how": "played"}, "area:87": {"t": 1762380100, "how": "history"}},
        "scanned": {"zone:1429": True},
        "journal": [
            {"id": "zone:1429", "t": 1762380000, "label": "Elwynn Forest", "title": "I'll Keep Coming",
             "artist": "Low Roar"},
            {"id": "inst:36", "t": 1762390000, "label": "The Deadmines", "title": 'Ship "Wreck"',
             "artist": "Sigur R\u00f3s"},
        ],
        "lastFireAt": 1762390000,
        "position": [108.2961502075195, -3.0517578125e-05, "CENTER", "\x00\x00\x02\x01raw\x7fcontrols\r"],
        "empty": {},
    },
}


def test_modern_client_format():
    assert parse(MODERN) == MODERN_EXPECTED


def test_classic_client_format():
    assert parse(CLASSIC) == CLASSIC_EXPECTED


@pytest.mark.parametrize("data", [MODERN, CLASSIC], ids=["modern", "classic"])
def test_samples_match_lua(data):
    assert_same_as_lua(data)


def test_str_input_equals_bytes_input():
    assert parse(MODERN.decode("utf-8")) == parse(MODERN)
    assert parse(CLASSIC.decode("utf-8")) == CLASSIC_EXPECTED


def test_lf_and_mixed_line_endings():
    assert parse(MODERN.replace(b"\r\n", b"\n")) == MODERN_EXPECTED
    mixed = CLASSIC.replace(b"\r\n", b"\n", 7).replace(b"\r\n", b"\r", 3)
    assert parse(mixed) == CLASSIC_EXPECTED


def test_types_of_numbers():
    db = parse(MODERN)["NeedleDropperDB"]
    assert type(db["schema"]) is int and type(db["settings"]["toastScale"]) is float
    assert type(db["settings"]["enabled"]) is bool


# ------------------------------------------------------------------ strings --

@pytest.mark.parametrize("literal, expected", [
    (rb'"plain"', "plain"),
    (rb"'single'", "single"),
    (rb'"a\"b"', 'a"b'),
    (rb"'a\'b'", "a'b"),
    (rb"'a\"b'", 'a"b'),
    (rb'"back\\slash"', "back\\slash"),
    (rb'"\n\r\t"', "\n\r\t"),
    (rb'"\a\b\f\v"', "\a\b\f\v"),
    (rb'"\065\066"', "AB"),
    (rb'"\0651"', "A1"),
    (rb'"\1234"', "{4"),
    (rb'"\000x"', "\x00x"),
    (rb'"\0"', "\x00"),
    (rb'"\195\169"', "\u00e9"),
    (rb'"\q\x41"', "qx41"),
    (b'"a\\\nb"', "a\nb"),
    (b'"a\\\r\nb"', "a\nb"),
    (b'"a\\\n\rb"', "a\nb"),
    (b'"a\\\rb"', "a\nb"),
    (b'"tab\there\x01\x1f\x7f"', "tab\there\x01\x1f\x7f"),
    (b'"caf\xc3\xa9 \xe2\x80\x94 \xf0\x9f\x8e\xb5"', "caf\u00e9 \u2014 \U0001F3B5"),
    (b'""', ""),
    (b"[[long]]", "long"),
    (b"[==[a]]b]=]c]==]", "a]]b]=]c"),
    (b"[[\nfirst newline dropped]]", "first newline dropped"),
    (b"[[\r\nCRLF dropped]]", "CRLF dropped"),
    (b"[[a\r\nb\rc\n\rd]]", "a\nb\nc\nd"),
    (b"[=[ [[ inside a level-1 string ]=]", " [[ inside a level-1 string "),
    (b"[[\\n is not an escape]]", "\\n is not an escape"),
])
def test_string_literals(literal, expected):
    data = b"X = " + literal
    assert parse(data) == {"X": expected}
    assert_same_as_lua(data)


@pytest.mark.parametrize("data, message", [
    (b'X = "abc', "unfinished string"),
    (b'X = "abc\ndef"', "unfinished string"),
    (b"X = 'abc\"", "unfinished string"),
    (b'X = "\\256"', "escape sequence too large"),
    (b'X = "\\999"', "escape sequence too large"),
    (b"X = [[abc", "unfinished long string"),
    (b"X = [==[abc]=]", "unfinished long string"),
    (b"X = [[a [[ b ]]", "nesting of [[...]] is deprecated"),
])
def test_bad_strings(data, message):
    with pytest.raises(SavedVariablesError, match=re.escape(message)):
        parse(data)
    assert not lua_globals(data)[0]


def test_invalid_utf8_handling():
    data = b'X = "ok \xff\xfe \\200 end"'
    assert parse(data) == {"X": "ok \ufffd\ufffd \ufffd end"}
    lossless = parse(data, string_errors="surrogateescape")["X"]
    assert lossless.encode("utf-8", "surrogateescape") == b"ok \xff\xfe \xc8 end"  # \ddd is decimal
    with pytest.raises(SavedVariablesError) as info:
        parse(b'\nX = "\xff"', string_errors="strict")
    assert info.value.line == 2 and "UTF-8" in str(info.value)


def test_long_string_key_and_value_in_table():
    data = b"X = { [ [[k]] ] = [=[v]=], [ 'q' ] = 1 }"
    assert parse(data) == {"X": {"k": "v", "q": 1}}
    assert_same_as_lua(data)


# ------------------------------------------------------------------ numbers --

@pytest.mark.parametrize("literal, expected, kind", [
    (b"1", 1, int),
    (b"-1", -1, int),
    (b"0", 0, int),
    (b"08", 8, int),
    (b"0.5", 0.5, float),
    (b".5", 0.5, float),
    (b"5.", 5.0, float),
    (b"1e3", 1000.0, float),
    (b"1E-2", 0.01, float),
    (b"-4.57763671875e-05", -4.57763671875e-05, float),
    (b"108.2961502075195", 108.2961502075195, float),
    (b"1e+20", 1e20, float),
    (b"0x10", 16, int),
    (b"0XfF", 255, int),
    (b"-0x10", -16, int),
    (b"2147483648", 2147483648, int),
    (b"1836185819070905", 1836185819070905, int),
    (b"9007199254740992", 9007199254740992, int),
    (b"9007199254740993", 9007199254740992, int),
    (b"12345678901234567890", 1.2345678901234567e19, float),
    (b"- 5", -5, int),
    (b"- -5", 5, int),
    (b"-\n5", -5, int),
    (b"1/2", 0.5, float),
])
def test_numbers(literal, expected, kind):
    data = b"X = " + literal
    value = parse(data)["X"]
    assert value == expected and type(value) is kind
    assert_same_as_lua(data)


@pytest.mark.parametrize("literal, expected", [
    (b"inf", math.inf),
    (b"-inf", -math.inf),
    (b"1.#INF", math.inf),
    (b"-1.#INF", -math.inf),
    (b"1.#INF00", math.inf),
    (b"1/0", math.inf),
    (b"-1/0", -math.inf),
    (b"(1/0)", math.inf),
    (b"-(1/0)", -math.inf),
    (b"( -1 / 0 )", -math.inf),
    (b"math.huge", math.inf),
    (b"-math.huge", -math.inf),
    (b"nan", math.nan),
    (b"-nan", math.nan),
    (b"nan(ind)", math.nan),
    (b"-nan(ind)", math.nan),
    (b"1.#IND", math.nan),
    (b"-1.#IND", math.nan),
    (b"1.#QNAN", math.nan),
    (b"0/0", math.nan),
])
def test_infinity_and_nan_spellings(literal, expected):
    value = parse(b"X = { " + literal + b" }")["X"][0]
    if math.isnan(expected):
        assert math.isnan(value)
    else:
        assert value == expected


@pytest.mark.parametrize("literal", [b"1e", b"1e+", b"1..2", b"1.2.3", b"0x", b"0xg", b"12abc", b"0x1p4",
                                     b"1.#XYZ", b"3e5e"])
def test_malformed_numbers(literal):
    with pytest.raises(SavedVariablesError, match="malformed number|unexpected symbol|expected"):
        parse(b"X = " + literal)


def test_number_with_many_digits_does_not_hit_int_conversion_limits():
    digits = b"7" * 5000
    assert parse(b"X = " + digits)["X"] == math.inf
    assert parse(b"X = 0." + digits)["X"] == pytest.approx(0.7777777777777778)
    assert parse(b"X = 0x" + b"f" * 300)["X"] == math.inf


# ------------------------------------------------------------------- tables --

@pytest.mark.parametrize("literal, expected", [
    (b"{}", {}),
    (b"{ }", {}),
    (b"{1, 2, 3}", [1, 2, 3]),
    (b"{1, 2, 3,}", [1, 2, 3]),
    (b"{1; 2; 3;}", [1, 2, 3]),
    (b"{[1] = 'a', [2] = 'b'}", ["a", "b"]),
    (b"{[2] = 'b', [1] = 'a'}", ["a", "b"]),
    (b"{[1.0] = 'a'}", ["a"]),
    (b"{1, nil, 3}", {1: 1, 3: 3}),
    (b"{nil, nil}", {}),
    (b"{[0] = 0, 1}", {0: 0, 1: 1}),
    (b"{[2] = 'b'}", {2: "b"}),
    (b"{a = 1, ['b c'] = 2, [3] = 3, [-1] = 4, [0.5] = 5}", {"a": 1, "b c": 2, 3: 3, -1: 4, 0.5: 5}),
    (b"{[true] = 1, [false] = 0}", {True: 1, False: 0}),
    (b"{[1/0] = 'inf'}", {math.inf: "inf"}),
    (b"{{}, {{}}, {1, {2}}}", [{}, [{}], [1, [2]]]),
    (b"{ x --[[c]] = --[[d]] 1 -- e\n }", {"x": 1}),
    (b"{ inf = 1, nan = 2, math = 3 }", {"inf": 1, "nan": 2, "math": 3}),
])
def test_tables(literal, expected):
    data = b"X = " + literal
    assert parse(data) == {"X": expected}
    if not has_non_finite(expected):
        assert_same_as_lua(data)


def test_empty_table_policy():
    assert parse(b"X = {}") == {"X": {}}
    assert parse(b"X = { {}, a = {} }", empty_table="list") == {"X": {1: [], "a": []}}


@pytest.mark.parametrize("literal, expected", [
    # Positional entries are stored after the keyed ones around them (Lua 5.1).
    (b"{ [1] = 'x', 'y' }", ["y"]),
    (b"{ 'y', [1] = 'x' }", ["y"]),
    (b"{ [1] = 'x', nil }", {}),
    (b"{ a = 1, a = nil }", {}),
    (b"{ a = 1, a = 2 }", {"a": 2}),
    (b"{ [1] = 'a', [true] = nil }", ["a"]),
    (b"{ [true] = 'a', [1] = nil }", {True: "a"}),
])
def test_constructor_semantics(literal, expected):
    data = b"X = " + literal
    assert parse(data) == {"X": expected}
    assert_same_as_lua(data)


def test_positional_entries_are_flushed_in_batches_of_50():
    head = b", ".join(str(i).encode() for i in range(1, 51))
    data = b"X = { " + head + b", [1] = 'late', 51 }"  # the first 50 are already stored
    value = assert_same_as_lua(data)["X"]
    assert value[0] == "late" and value[50] == 51
    data = b"X = { " + head[:-4] + b", [1] = 'early', 50 }"  # 49 pending: positional wins
    assert assert_same_as_lua(data)["X"][0] == 1


@pytest.mark.parametrize("literal, message", [
    (b"{ end = 1 }", "unsupported value 'end'"),
    (b"{ nil = 1 }", "'}' expected"),
    (b"{ [nil] = 1 }", "table index is nil"),
    (b"{ [0/0] = 1 }", "table index is NaN"),
    (b"{ [{}] = 1 }", "table used as a table key"),
    (b"{ [true] = 1, [1] = 2 }", "both a boolean key"),
    (b"{ 'a', [true] = 2 }", "both a boolean key"),
    (b"{ [false] = 1, [0] = 2 }", "both a boolean key"),
    (b"{ 1 2 }", "'}' expected"),
    (b"{ , }", "unexpected symbol"),
    (b"{ ; }", "unexpected symbol"),
    (b"{ [1] 2 }", "'=' expected"),
    (b"{ [1 = 2 }", "']' expected"),
    (b"{ a == 1 }", "unsupported value 'a'"),
    (b"{ 1, 2", "unfinished table"),
    (b"{ f() }", "unsupported value 'f'"),
])
def test_bad_tables(literal, message):
    with pytest.raises(SavedVariablesError, match=re.escape(message)):
        parse(b"X = " + literal)


def test_depth_limit():
    parse(b"X = " + b"{" * 64 + b"}" * 64)
    with pytest.raises(SavedVariablesError, match="nested more than 64 levels"):
        parse(b"X = " + b"{" * 65 + b"}" * 65)
    assert parse(b"X = {{1}}", max_depth=2) == {"X": [[1]]}
    with pytest.raises(SavedVariablesError, match="nested more than 2 levels"):
        parse(b"X = {{{1}}}", max_depth=2)


def test_depth_value_at_limit_is_exact():
    value = parse(b"X = " + b"{" * 64 + b"7" + b"}" * 64)["X"]
    for _ in range(63):
        value = value[0]
    assert value == [7]


# ---------------------------------------------------------------- top level --

def test_top_level_forms():
    data = (b"\xef\xbb\xbf-- comment\r\n--[==[ block\r\n comment ]==]\r\n"
            b"A = 1; B = 'two';\r\nC = nil\r\nA = 3 D = {} ;;\r\n-- trailing comment without newline")
    assert parse(data) == {"A": 3, "B": "two", "C": None, "D": {}}


@pytest.mark.parametrize("data", [b"", b"   \r\n\t  ", b"-- only a comment", b"--[[ block ]]", b";"])
def test_empty_files(data):
    assert parse(data) == {}


@pytest.mark.parametrize("data, message", [
    (b"local x = 1", "unsupported statement 'local'"),
    (b"return { }", "unsupported statement 'return'"),
    (b"function f() end", "unsupported statement 'function'"),
    (b"X = foo", "unsupported value 'foo'"),
    (b"X = foo()", "unsupported value 'foo'"),
    (b"X = os.exit()", "unsupported value 'os'"),
    (b"X = math.floor", "only math.huge"),
    (b"X.y = 1", "'=' expected after the global name 'X'"),
    (b"X, Y = 1, 2", "'=' expected after the global name 'X'"),
    (b"X == 1", "'=' expected after the global name 'X'"),
    (b"X = 1 + 2", "unexpected symbol"),
    (b"X = 'a' .. 'b'", "unexpected symbol"),
    (b"X = -'5'", "number expected"),
    (b"X = -{}", "number expected"),
    (b"X = ...", "unexpected symbol"),
    (b"X = 1 Y", "'=' expected after the global name 'Y'"),
    (b"X = ", "a value is missing"),
    (b"X = (1", "')' expected"),
    (b"X = (1/0", "')' expected"),
    (b"X = ('a')", "number expected"),
    (b"X = 1 --[[ unfinished", "unfinished long comment"),
    (b"X = 1 --[[ a [[ b ]]", "nesting of"),
    (b"1 = 2", "unexpected symbol"),
    (b"\x00", "unexpected symbol"),
    (b"\xc3\xa9t\xc3\xa9 = 1", "unexpected symbol"),
    (b"#!/bin/lua\nX = 1", "unexpected symbol"),
    (b"X = nan(1)", "unexpected symbol"),
])
def test_unsupported_and_malformed(data, message):
    with pytest.raises(SavedVariablesError, match=re.escape(message)):
        parse(data)


def test_error_line_and_column():
    data = b"\r\nX = {\r\n[\"a\"] = 1,\r\n\t[\"b\"] = \"unterminated,\r\n}\r\n"
    with pytest.raises(SavedVariablesError) as info:
        parse(data)
    exc = info.value
    assert (exc.line, exc.column) == (4, 10)
    assert exc.detail.startswith("line 4, column 10: unfinished string near '\"unterminated,'")
    assert str(exc) == exc.detail and exc.path is None


def test_error_line_counts_crlf_lfcr_cr_as_one_line():
    for nl in (b"\r\n", b"\n\r", b"\r", b"\n"):
        with pytest.raises(SavedVariablesError) as info:
            parse(b"A = 1" + nl + b"B = 2" + nl + b"  C = @")
        assert (info.value.line, info.value.column) == (3, 7)


def test_unfinished_table_mentions_opening_line():
    with pytest.raises(SavedVariablesError, match=r"to close '\{' at line 2"):
        parse(b"\nX = {\n1,\n2,\n")


# ---------------------------------------------------------------- arguments --

@pytest.mark.parametrize("kwargs", [
    {"max_depth": 0}, {"max_depth": 1000}, {"max_depth": True}, {"max_bytes": -1}, {"max_bytes": 1.5},
    {"empty_table": "set"}, {"string_errors": "no-such-handler"},
])
def test_bad_options(kwargs):
    with pytest.raises(ValueError):
        parse(b"X = 1", **kwargs)


def test_bad_input_type():
    with pytest.raises(TypeError):
        parse(12)  # type: ignore[arg-type]


# --------------------------------------------------------------- size caps --

def test_size_cap_rejects_before_parsing():
    assert sv.MAX_BYTES == 8 * 1024 * 1024
    assert parse(b" " * sv.MAX_BYTES) == {}
    t0 = time.perf_counter()
    with pytest.raises(SavedVariablesError, match="too large"):
        parse(b"{" * (sv.MAX_BYTES + 1))
    assert time.perf_counter() - t0 < 1.0
    with pytest.raises(SavedVariablesError, match="too large"):
        parse(b"X = 1      ", max_bytes=10)


def test_read_file_checks_size_with_stat_before_reading(tmp_path, monkeypatch):
    big = tmp_path / "NeedleDropper.lua"
    with open(big, "wb") as fh:
        fh.truncate(sv.MAX_BYTES + 1)

    def no_open(*a, **k):
        raise AssertionError("the file must not be opened")

    monkeypatch.setattr(sv, "open", no_open, raising=False)
    with pytest.raises(SavedVariablesError, match="too large") as info:
        sv.read_file(big)
    assert info.value.path == str(big)


def test_read_file_reports_path_and_position(tmp_path):
    p = tmp_path / "NeedleDropper.lua"
    p.write_bytes(b"\r\nNeedleDropperDB = {\r\n[\"x\"] = ,\r\n}\r\n")
    with pytest.raises(SavedVariablesError) as info:
        sv.read_file(p)
    assert info.value.path == str(p) and info.value.line == 3
    assert str(info.value).startswith(f"{p}: line 3, column 9: ")
    p.write_bytes(CLASSIC)
    assert sv.read_file(p) == CLASSIC_EXPECTED
    with pytest.raises(OSError):
        sv.read_file(tmp_path / "missing.lua")


def test_huge_but_valid_file():
    entries = b"".join(
        b"{\r\n[\"id\"] = \"zone:%d\",\r\n[\"t\"] = %d,\r\n[\"title\"] = \"Song \\\"%d\\\" \xc3\xa9\",\r\n"
        b"[\"x\"] = %d.25,\r\n}, -- [%d]\r\n" % (i, 1762380000 + i, i, i, i + 1) for i in range(30000))
    blob = b"\\000\\\\\\\"\x01" * 50000
    data = (b"\r\nNeedleDropperCharDB = {\r\n[\"journal\"] = {\r\n" + entries + b"},\r\n[\"blob\"] = \"" + blob
            + b"\",\r\n}\r\n")
    assert 2_500_000 < len(data) < sv.MAX_BYTES
    t0 = time.perf_counter()
    db = parse(data)["NeedleDropperCharDB"]
    elapsed = time.perf_counter() - t0
    assert len(db["journal"]) == 30000
    assert db["journal"][29999] == {"id": "zone:29999", "t": 1762409999, "title": 'Song "29999" \u00e9',
                                    "x": 29999.25}
    assert db["blob"] == "\x00\\\"\x01" * 50000
    assert elapsed < 30  # typically well under 2 s


@pytest.mark.parametrize("data", [
    b"X = " + b"{" * 100000,
    b"X = " + b"(" * 100000 + b"1",
    b"X = " + b"- " * 100000 + b"(",
    b"X = \"" + b"a" * 1000000,
    b"X = \"" + b"\\a" * 500000,
    b"X = [==[" + b"]=]" * 300000,
    b"X = 1 --[[" + b"x" * 1000000,
    b"X = {" + b"1," * 200000,
    b"X = " + b"1" * 1000000 + b"e",
])
def test_pathological_inputs_fail_fast(data):
    t0 = time.perf_counter()
    with pytest.raises(SavedVariablesError):
        parse(data)
    assert time.perf_counter() - t0 < 10


def test_long_unary_chain_is_not_recursive():
    assert parse(b"X = " + b"- " * 100000 + b"5") == {"X": 5}


# --------------------------------------------------------------------- fuzz --

_ALPHABET = list(b"{}[]=,;\"'\\-./()#\n\r\t xX0123456789eE+abcfnrtuvlsdmhi_") + [0, 0xC3, 0xA9, 0xFF, 0x80]


def test_fuzz_random_bytes_only_raise_savedvariables_errors():
    rng = random.Random(1429)
    for _ in range(2500):
        n = rng.randint(0, 80)
        if rng.random() < 0.7:
            data = bytes(rng.choice(_ALPHABET) for _ in range(n))
        else:
            data = bytes(rng.randrange(256) for _ in range(n))
        data = rng.choice([b"", b"X = ", b"X = {", b"X = {[\""]) + data
        try:
            result = parse(data, string_errors="surrogateescape")
        except SavedVariablesError:
            continue
        if not has_non_finite(result) and not data.startswith(sv._BOM):
            ok, theirs = lua_globals(data)
            assert ok, data
            mine = {k: as_bytes(v) for k, v in result.items() if v is not None}
            assert mine == theirs, data


@pytest.mark.parametrize("sample", [MODERN, CLASSIC], ids=["modern", "classic"])
def test_fuzz_every_truncation(sample):
    accepted = 0
    for cut in range(len(sample) + 1):
        data = sample[:cut]
        try:
            result = parse(data, string_errors="surrogateescape")
        except SavedVariablesError:
            continue
        accepted += 1
        ok, theirs = lua_globals(data)
        assert ok, cut
        assert {k: as_bytes(v) for k, v in result.items() if v is not None} == theirs, cut
    assert accepted >= 3  # at least: empty, the leading newline, the complete file


@pytest.mark.parametrize("sample", [MODERN, CLASSIC], ids=["modern", "classic"])
def test_fuzz_mutations_match_lua_when_accepted(sample):
    rng = random.Random(87)
    accepted = 0
    for _ in range(700):
        data = bytearray(sample)
        for _ in range(rng.randint(1, 4)):
            op = rng.random()
            pos = rng.randrange(len(data) + 1)
            if op < 0.4 and data:
                pos = min(pos, len(data) - 1)
                data[pos] = rng.choice(_ALPHABET)
            elif op < 0.7:
                data[pos:pos] = bytes([rng.choice(_ALPHABET)])
            elif data:
                del data[min(pos, len(data) - 1)]
        data = bytes(data)
        try:
            result = parse(data, string_errors="surrogateescape")
        except SavedVariablesError:
            continue
        if has_non_finite(result):
            continue
        accepted += 1
        ok, theirs = lua_globals(data)
        assert ok, data
        assert {k: as_bytes(v) for k, v in result.items() if v is not None} == theirs, data
    assert accepted > 50


# A serializer that writes like the WoW client does: '"' strings escaping only
# backslash, quote, LF, CR and NUL (other control bytes and UTF-8 raw), numbers
# as %.16g-style literals, every key in brackets.

def _wow_quote(s: str) -> bytes:
    out = bytearray(b'"')
    for b in s.encode("utf-8"):
        out += {0x5C: b"\\\\", 0x22: b'\\"', 0x0A: b"\\n", 0x0D: b"\\r", 0x00: b"\\000"}.get(b, bytes((b,)))
    return bytes(out + b'"')


def _wow_scalar(v) -> bytes:
    if v is True:
        return b"true"
    if v is False:
        return b"false"
    if isinstance(v, int):
        return str(v).encode()
    if isinstance(v, float):
        return repr(v).encode()
    return _wow_quote(v)


def _wow_value(v, depth: int, classic: bool) -> bytes:
    if not isinstance(v, (dict, list)):
        return _wow_scalar(v)
    pad = b"\t" * (depth + 1) if classic else b""
    lines = [b"{"]
    if isinstance(v, list):
        for i, item in enumerate(v, 1):
            mark = b" -- [%d]" % i if classic else b""
            lines.append(pad + _wow_value(item, depth + 1, classic) + b"," + mark)
    else:
        for k, item in v.items():
            lines.append(pad + b"[" + _wow_scalar(k) + b"] = " + _wow_value(item, depth + 1, classic) + b",")
    lines.append((b"\t" * depth if classic else b"") + b"}")
    return b"\r\n".join(lines)


def wow_dump(values: dict, *, classic: bool) -> bytes:
    out = [b""]
    for name, v in values.items():
        out.append(name.encode() + b" = " + (b"nil" if v is None else _wow_value(v, 0, classic)))
    return b"\r\n".join(out) + b"\r\n"


_TEXT = ["", "a", "Elwynn Forest", 'q"uote', "back\\slash", "line\nbreak", "cr\rlf\r\n", "nul\x00byte",
         "\x01\x02\x1f\x7f", "tab\there", "caf\u00e9", "\u65e5\u672c\u8a9e", "\U0001F3B5", "]]", "[[", "--",
         "\\000", "\\n", "zone:1429", "'single'", "\u202e\u2028\ufeff"]


def _random_text(rng: random.Random) -> str:
    return "".join(rng.choice(_TEXT) for _ in range(rng.randint(0, 4)))


def _random_value(rng: random.Random, depth: int = 0):
    r = rng.random()
    if depth >= 5 or r < 0.45:
        kind = rng.randrange(5)
        if kind == 0:
            return rng.random() < 0.5
        if kind == 1:
            return rng.choice([0, 1, -1, 7, 2147483648, -(2 ** 53), 2 ** 53, rng.randint(-10 ** 9, 10 ** 9)])
        if kind == 2:
            return rng.choice([0.5, -0.25, 1e-05, 4.57763671875e-05, 1e20, rng.uniform(-1e6, 1e6)])
        return _random_text(rng)
    if r < 0.7:
        return [_random_value(rng, depth + 1) for _ in range(rng.randint(1, 5))]
    d = {}
    for _ in range(rng.randint(0, 6)):
        k = rng.choice([_random_text(rng) or "k", rng.randint(2, 99), -rng.randint(1, 9), 0.5])
        d[k] = _random_value(rng, depth + 1)
    return d


def _expected(v):
    """How the reader shows ``v`` after a round trip (dicts keyed 1..n are lists)."""
    if isinstance(v, list):
        return [_expected(x) for x in v]
    if isinstance(v, dict):
        d = {k: _expected(x) for k, x in v.items()}
        if d and all(type(k) is int for k in d) and min(d) == 1 and max(d) == len(d):
            return [d[i] for i in range(1, len(d) + 1)]
        return d
    return v


@pytest.mark.parametrize("classic", [False, True], ids=["modern", "classic"])
def test_round_trip_random_structures_written_like_the_client(classic):
    rng = random.Random(1453 + classic)
    for _ in range(150):
        values = {f"Var{i}": _random_value(rng) for i in range(rng.randint(1, 3))}
        data = wow_dump(values, classic=classic)
        result = parse(data, string_errors="strict")
        assert result == {k: _expected(v) for k, v in values.items()}, data[:300]
        assert_same_as_lua(data)


# --------------------------------------------------------------- helpers --

def test_as_list_and_as_dict():
    assert sv.as_list([1, 2]) == [1, 2]
    assert sv.as_list({}) == [] and sv.as_list(None) == [] and sv.as_list("x") == []
    assert sv.as_list({3: "c", 1: "a", "k": "v", 0: "z", -1: "n"}) == ["a", "c"]
    assert sv.as_dict(["a", "b"]) == {1: "a", 2: "b"}
    assert sv.as_dict({"a": 1}) == {"a": 1} and sv.as_dict(None) == {} and sv.as_dict(5) == {}


# ------------------------------------------------------- files & loading --

def _write(path: Path, data: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _account_file(db: bytes) -> bytes:
    return b"\r\nNeedleDropperDB = " + db + b"\r\n"


def _char_file(db: bytes) -> bytes:
    return b"\r\nNeedleDropperCharDB = " + db + b"\r\n"


@pytest.fixture
def wtf(tmp_path):
    """A WTF tree shaped like the real one (account folder with '#', a realm named '91', ...)."""
    root = tmp_path / "_forever_"
    acct = root / "WTF" / "Account" / "70515559#1"
    _write(acct / "SavedVariables" / "NeedleDropper.lua", _account_file(b"{\r\n[\"schema\"] = 1,\r\n}"))
    _write(acct / "SavedVariables" / "NeedleDropper.lua.bak", b"garbage {{{")
    _write(acct / "SavedVariables" / "NeedleDropper_Companion.lua", b"garbage {{{")
    _write(acct / "SavedVariables" / "Blizzard_CombatLog.lua", b"\r\nBlizzard_CombatLog_Filters = {\r\n}\r\n")
    _write(acct / "bindings-cache.wtf", b"")
    _write(acct / "91" / "Kojima-Productions" / "SavedVariables" / "NeedleDropper.lua",
           _char_file(b"{\r\n[\"journal\"] = {\r\n},\r\n}"))
    _write(acct / "91" / "Kojima-Productions" / "AddOns.txt", b"NeedleDropper: enabled\r\n")
    _write(acct / "Lone Wolf" / "Soupreme" / "SavedVariables" / "NeedleDropper.lua", CLASSIC)
    _write(acct / "Kel'Thuzad" / "Z\u00e9phyr" / "SavedVariables" / "needledropper.LUA",
           _char_file(b"{ [\"done\"] = { [\"zone:1429\"] = { [\"how\"] = \"played\" } } }"))
    _write(acct / "Twisting Nether" / "\u00c6r\u00f8n" / "SavedVariables" / "Blizzard_RaidUI.lua", b"")
    (acct / "Twisting Nether" / "Nofolder").mkdir(parents=True)
    (acct / "Argent Dawn" / "Dirchar" / "SavedVariables" / "NeedleDropper.lua").mkdir(parents=True)
    _write(root / "WTF" / "Account" / "SavedVariables" / "NeedleDropper.lua", _account_file(b"{}"))
    _write(root / "WTF" / "Account" / "MYACCOUNT" / "SavedVariables" / "NeedleDropper.lua",
           _account_file(b"{ [\"settings\"] = { [\"enabled\"] = false } }"))
    _write(root / "WTF" / "SavedVariables" / "NeedleDropper.lua", _account_file(b"{}"))
    _write(root / "WTF" / "Account" / "loose-file.txt", b"")
    return root


def test_find_files(wtf):
    acct = wtf / "WTF" / "Account" / "70515559#1"
    found = sv.find_files(wtf)
    assert found == [
        ("account", "70515559#1", None, None, acct / "SavedVariables" / "NeedleDropper.lua"),
        ("character", "70515559#1", "91", "Kojima-Productions",
         acct / "91" / "Kojima-Productions" / "SavedVariables" / "NeedleDropper.lua"),
        ("character", "70515559#1", "Kel'Thuzad", "Z\u00e9phyr",
         acct / "Kel'Thuzad" / "Z\u00e9phyr" / "SavedVariables" / "needledropper.LUA"),
        ("character", "70515559#1", "Lone Wolf", "Soupreme",
         acct / "Lone Wolf" / "Soupreme" / "SavedVariables" / "NeedleDropper.lua"),
        ("account", "MYACCOUNT", None, None, wtf / "WTF" / "Account" / "MYACCOUNT" / "SavedVariables" /
         "NeedleDropper.lua"),
    ]
    assert found[1].scope == sv.SCOPE_CHARACTER and found[1].realm == "91"
    assert all(isinstance(f.path, Path) for f in found)


def test_find_files_other_addon_and_missing_tree(wtf, tmp_path):
    found = sv.find_files(wtf, "Blizzard_CombatLog")
    assert [(f.scope, f.account) for f in found] == [("account", "70515559#1")]
    assert sv.find_files(tmp_path / "nowhere") == []
    assert sv.find_files(wtf, "NeedleDropper_Companion")[0].path.name == "NeedleDropper_Companion.lua"


@pytest.mark.parametrize("addon", ["", "..", "a/b", "a\\b", "C:x", " pad", "nul\0"])
def test_find_files_rejects_bad_addon_names(wtf, addon):
    with pytest.raises(ValueError):
        sv.find_files(wtf, addon)


def test_load_needledropper(wtf):
    result = sv.load_needledropper(wtf)
    assert result["errors"] == []
    assert result["accounts"] == {
        "70515559#1": {
            "db": {"schema": 1},
            "characters": {
                "91/Kojima-Productions": {"journal": {}},
                "Kel'Thuzad/Z\u00e9phyr": {"done": {"zone:1429": {"how": "played"}}},
                "Lone Wolf/Soupreme": CLASSIC_EXPECTED["NeedleDropperCharDB"],
            },
        },
        "MYACCOUNT": {"db": {"settings": {"enabled": False}}, "characters": {}},
    }
    assert sv.as_list(result["accounts"]["70515559#1"]["characters"]["91/Kojima-Productions"]["journal"]) == []


def test_load_needledropper_reports_bad_files_and_keeps_going(wtf):
    acct = wtf / "WTF" / "Account" / "70515559#1"
    _write(acct / "SavedVariables" / "NeedleDropper.lua", b"\r\nNeedleDropperDB = {\r\n[\"a\"] = \"open\r\n}")
    _write(acct / "91" / "Kojima-Productions" / "SavedVariables" / "NeedleDropper.lua",
           _char_file(b"\"just a string\""))
    _write(acct / "Lone Wolf" / "Soupreme" / "SavedVariables" / "NeedleDropper.lua",
           b"\r\nNeedleDropperCharDB = nil\r\n")
    _write(wtf / "WTF" / "Account" / "MYACCOUNT" / "SavedVariables" / "NeedleDropper.lua",
           b"NeedleDropperDB = { 1, 2 }")
    result = sv.load_needledropper(wtf)
    assert result["accounts"] == {
        "70515559#1": {"db": None, "characters": {"Kel'Thuzad/Z\u00e9phyr": {"done": {"zone:1429": {"how": "played"}}}}},
        "MYACCOUNT": {"db": None, "characters": {}},
    }
    errors = {(e["account"], e["realm"], e["character"]): e for e in result["errors"]}
    assert len(errors) == 3
    syntax = errors[("70515559#1", None, None)]
    assert syntax["scope"] == "account" and syntax["line"] == 3 and syntax["column"] == 9
    assert syntax["message"].startswith("line 3, column 9: unfinished string")
    assert syntax["path"] == str(acct / "SavedVariables" / "NeedleDropper.lua")
    assert "is a string" in errors[("70515559#1", "91", "Kojima-Productions")]["message"]
    assert "is a list" in errors[("MYACCOUNT", None, None)]["message"]


def test_load_needledropper_missing_tree(tmp_path):
    assert sv.load_needledropper(tmp_path) == {"accounts": {}, "errors": []}


def test_load_needledropper_too_large_file(wtf):
    big = wtf / "WTF" / "Account" / "MYACCOUNT" / "SavedVariables" / "NeedleDropper.lua"
    with open(big, "r+b") as fh:
        fh.truncate(sv.MAX_BYTES + 1)
    result = sv.load_needledropper(wtf)
    assert result["accounts"]["MYACCOUNT"] == {"db": None, "characters": {}}
    assert [e["account"] for e in result["errors"]] == ["MYACCOUNT"]
    assert "too large" in result["errors"][0]["message"]


# ------------------------------------------------- backups (v1.2, read-only) --

def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_backup_copies_every_needledropper_file_and_only_reads_the_game_folder(wtf, tmp_path):
    before = _snapshot(wtf)
    backups = tmp_path / "app" / "backups"
    made = sv.backup_savedvariables(wtf, backups, now=1762380000)
    assert _snapshot(wtf) == before  # nothing below the game folder changed
    rel = sorted(p.relative_to(backups).parts[:-1] for p in made)
    assert rel == [("70515559#1",), ("70515559#1", "91", "Kojima-Productions"),
                   ("70515559#1", "Kel'Thuzad", "Zéphyr"), ("70515559#1", "Lone Wolf", "Soupreme"),
                   ("MYACCOUNT",)]
    src = wtf / "WTF" / "Account" / "70515559#1" / "SavedVariables" / "NeedleDropper.lua"
    account_copy = next(p for p in made if p.parent == backups / "70515559#1")
    assert account_copy.read_bytes() == src.read_bytes() and account_copy.name.startswith("NeedleDropper-")
    assert not list(backups.rglob("*.tmp"))


def test_backup_skips_unchanged_files_and_rolls(wtf, tmp_path):
    backups = tmp_path / "backups"
    src = wtf / "WTF" / "Account" / "MYACCOUNT" / "SavedVariables" / "NeedleDropper.lua"
    sv.backup_savedvariables(wtf, backups, keep=3, now=1000)
    assert sv.backup_savedvariables(wtf, backups, keep=3, now=2000) == []  # nothing changed
    for i in range(5):
        src.write_bytes(_account_file(b'{ ["lastSeq"] = %d }' % i))
        made = sv.backup_savedvariables(wtf, backups, keep=3, now=3000 + i)
        assert [p.parent.name for p in made] == ["MYACCOUNT"]
    copies = sorted((backups / "MYACCOUNT").glob("NeedleDropper-*.lua"))
    assert len(copies) == 3 and copies[-1].read_bytes() == src.read_bytes()
    # two saves within the same second do not overwrite each other
    src.write_bytes(_account_file(b'{ ["lastSeq"] = 99 }'))
    again = sv.backup_savedvariables(wtf, backups, keep=10, now=3004)
    assert again and again[0].name.endswith("-2.lua")


def test_backup_refuses_a_folder_inside_the_game_folder(wtf):
    with pytest.raises(ValueError, match="must not be inside"):
        sv.backup_savedvariables(wtf, wtf / "WTF" / "backups")
    with pytest.raises(ValueError):
        sv.backup_savedvariables(wtf, wtf.parent / "b", keep=0)


def test_backup_of_a_missing_tree_does_nothing(tmp_path):
    assert sv.backup_savedvariables(tmp_path / "nowhere", tmp_path / "b") == []
    assert not (tmp_path / "b").exists()


def test_search_only_songs(wtf):
    acct = wtf / "WTF" / "Account" / "MYACCOUNT" / "SavedVariables" / "NeedleDropper.lua"
    acct.write_bytes(_account_file(
        b'{\r\n["soundtracks"] = {\r\n["my-drops"] = {\r\n["drops"] = {\r\n'
        b'{ ["id"] = "area:87", ["artist"] = "Toto", ["title"] = "Africa" },\r\n'
        b'{ ["id"] = "zone:1429", ["artist"] = "Low Roar", ["title"] = "Gosia", ["spotify"] = "4uLU6hMCjMI75M1A2tKUQC" },\r\n'
        b'{ ["id"] = "zone:1426", ["disabled"] = true, ["artist"] = "X", ["title"] = "Y" },\r\n'
        b'{ ["id"] = "zone:1411", ["artist"] = "", ["title"] = "draft" },\r\n'
        b'{ ["id"] = "zone:1412", ["artist"] = " Toto ", ["title"] = "Africa" },\r\n'
        b'},\r\n},\r\n["broken"] = 5,\r\n},\r\n}'))
    assert sv.search_only_songs(wtf) == [("Toto", "Africa")]
    assert sv.search_only_songs(wtf.parent / "nowhere") == []


# ------------------------------------------ real installs (read-only, opt.) --

WOW_ROOT = Path(r"C:\Program Files (x86)\World of Warcraft")


def _real_wtf_roots() -> list[Path]:
    try:
        flavors = sorted(p for p in WOW_ROOT.iterdir() if p.is_dir() and p.name.startswith("_")
                         and p.name.endswith("_"))
    except OSError:
        return []
    return [p / "WTF" for p in flavors if (p / "WTF").is_dir()]


@pytest.fixture
def forbid_writes_under_wow(monkeypatch):
    """Fail the test on any attempt to create, change or delete anything below the WoW folder.

    (Comparing mtimes instead would be flaky: the game may save while the tests run.)
    """
    root = os.path.normcase(os.path.abspath(WOW_ROOT))

    def inside(path) -> bool:
        if not isinstance(path, (str, bytes, os.PathLike)):
            return False  # a file descriptor
        try:
            full = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        except (TypeError, ValueError):
            return False
        return full == root or full.startswith(root + os.sep)

    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if inside(file) and any(c in mode for c in "wax+"):
            raise AssertionError(f"tried to open {file!r} for writing")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    for name in ("replace", "rename", "remove", "unlink", "rmdir", "mkdir", "makedirs", "utime", "truncate"):
        real = getattr(os, name)

        def guard(*args, _real=real, _name=name, **kwargs):
            if any(inside(a) for a in args[:2]):
                raise AssertionError(f"os.{_name}{args!r} below the WoW folder")
            return _real(*args, **kwargs)

        monkeypatch.setattr(os, name, guard)


@pytest.mark.skipif(not (WOW_ROOT / "_forever_" / "WTF").is_dir(), reason="WoW: Forever is not installed here")
def test_real_savedvariables_parse_exactly_like_lua(forbid_writes_under_wow):
    files = []
    for wtf_root in _real_wtf_roots():
        for p in wtf_root.rglob("*"):
            name = p.name.lower()
            if p.is_file() and (name.endswith(".lua") or name.endswith(".lua.bak")) and "savedvariables" in \
                    [part.lower() for part in p.parts]:
                files.append(p)
    assert files, "no SavedVariables files found"
    problems = []
    for p in files:
        data = p.read_bytes()
        if len(data) > sv.MAX_BYTES:
            continue
        try:
            ours = parse(data, string_errors="surrogateescape")
        except SavedVariablesError as exc:
            ok, err = lua_globals(data)
            if ok:  # only acceptable when Lua rejects the file too (e.g. a truncated write)
                problems.append(f"{p}: the reader failed but Lua loads it: {exc.detail}")
            continue
        ok, theirs = lua_globals(data)
        if not ok:
            problems.append(f"{p}: the reader accepted a file Lua rejects")
            continue
        mine = {k: as_bytes(v) for k, v in ours.items() if v is not None}
        diff = first_difference(mine, theirs)
        if diff:
            problems.append(f"{p}: differs at {diff}")
    assert not problems, "\n".join(problems[:20])


@pytest.mark.skipif(not (WOW_ROOT / "_forever_" / "WTF").is_dir(), reason="WoW: Forever is not installed here")
def test_real_forever_install_find_and_load(forbid_writes_under_wow):
    flavor = WOW_ROOT / "_forever_"
    found = sv.find_files(flavor)
    for f in found:
        assert f.path.name.lower() == "needledropper.lua" and f.path.is_file()
    result = sv.load_needledropper(flavor)
    assert result["errors"] == []
    assert set(result["accounts"]) == {f.account for f in found}
    blizzard = sv.find_files(flavor, "Blizzard_CombatLog")
    for f in blizzard:
        assert isinstance(sv.read_file(f.path), dict)
