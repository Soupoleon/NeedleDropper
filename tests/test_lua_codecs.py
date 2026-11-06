"""Cross-language tests for the addon's pure-Lua codecs: ``Json.lua`` (``ns.Json``) and
``Base64.lua`` (``ns.Base64``).

Both files are loaded into lupa's Lua 5.1 the way the WoW client loads addon files -- each
chunk compiled with ``loadstring`` and called with the ``(ADDON, ns)`` varargs -- in a bare
runtime without the WoW shim, which also proves they need nothing but the Lua 5.1 standard
library. They are then checked against Python's ``json`` and ``base64`` modules:

* JSON encode: hundreds of seeded random documents (unicode incl. the astral plane, every
  escape and control character, nesting, big/tiny/random-bit numbers) encoded by Lua parse
  in Python to the same value, and are byte-identical to ``json.dumps`` wherever the number
  formatting rules coincide (compact, ``indent`` and ``ensure_ascii`` variants);
* JSON decode: Python output in every dump style decodes in Lua to the same value; Lua
  round trips are idempotent; malformed input (each case also rejected by Python unless
  noted), depth bombs and size bombs fail cleanly with a positioned message;
* base64: byte-identical to Python for both alphabets with and without padding, decodes
  Python's output (also MIME-wrapped, whitespace-mangled, mixed alphabets) and rejects
  invalid input;
* robustness: a foreign C locale (decimal comma, non-byte collation) does not leak into the
  JSON text or key order, and WoW 12.x secret values are refused, never inspected;
* performance budgets (64 KB base64 each way in < 50 ms) and static hygiene (no ``bit`` /
  ``bit32`` / ``utf8`` / ``loadstring`` / ``goto``, no globals, Lua 5.1 only).
"""
from __future__ import annotations

import base64
import json
import locale
import math
import random
import re
import struct
import time
from pathlib import Path
from typing import Any, Optional

import pytest

lua51 = pytest.importorskip("lupa.lua51")

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "addon" / "NeedleDropper"
CODEC_FILES = ("Json.lua", "Base64.lua")
ADDON_NAME = b"NeedleDropper"
MAX_EXACT = 2 ** 53
MB = 1048576


# ===================================================================== runtime ==

def unpack(res) -> tuple[Any, Optional[bytes]]:
    """Normalise a codec call result to ``(value, err)``."""
    if isinstance(res, tuple):
        return res[0], (res[1] if len(res) > 1 else None)
    return res, None


class Lua:
    """A bare Lua 5.1 runtime with Json.lua and Base64.lua loaded like addon files.

    The runtime uses ``encoding=None``: Lua strings come back as ``bytes`` and every string
    passed in must be ``bytes`` (a ``str`` would arrive as userdata).
    """

    def __init__(self, prelude: str = "") -> None:
        self.rt = lua51.LuaRuntime(unpack_returned_tuples=True, encoding=None)
        if prelude:
            self.rt.execute(prelude)
        self.globals_before = self.global_names()
        self._loadstring = self.rt.execute("return function(src, name) return loadstring(src, name) end")
        self.ns = self.rt.table()
        self.returned = {}
        for name in CODEC_FILES:
            fn, err = self.compile(name)
            assert fn is not None, f"{name} does not compile under Lua 5.1: {err}"
            self.returned[name] = fn(ADDON_NAME, self.ns)
        self.json = self.ns[b"Json"]
        self.b64 = self.ns[b"Base64"]
        self.null = self.json[b"null"]
        self.rawequal = self.rt.eval("rawequal")
        self._encode_held = self.rt.eval("function(J, held, opts) return J.Encode(held[1], opts) end")
        self._reencode = self.rt.eval(
            "function(J, text, dopts, eopts)"
            "  local v, err = J.Decode(text, dopts)"
            "  if err then return nil, err end"
            "  return J.Encode(v, eopts)"
            " end")

    # -- plumbing --

    def compile(self, name: str):
        res = self._loadstring((ADDON_DIR / name).read_bytes(), b"@" + name.encode())
        return unpack(res)

    def global_names(self) -> set:
        return set(self.rt.globals().keys())

    def run(self, src: str, *args):
        """Executes Lua code with ``J``, ``B`` and ``NULL`` in scope and ``args`` as ``...``."""
        wrapped = "local J, B, NULL = ...\nreturn (function(...)\n" + src + "\nend)(select(4, ...))"
        return self.rt.execute(wrapped, self.json, self.b64, self.null, *args)

    def held(self, src: str):
        """A one-element Lua table holding the value of Lua expression ``src`` (``NULL`` in
        scope). Numbers stay in Lua, so values beyond int64 never pass through Python."""
        return self.rt.execute("local NULL = ...\nreturn {" + src + "}", self.null)

    def opts(self, opts: Optional[dict]):
        if opts is None:
            return None
        return self.rt.table_from({k.encode(): (v.encode() if isinstance(v, str) else v) for k, v in opts.items()})

    # -- codec calls --

    def encode_src(self, src: str, opts: Optional[dict] = None):
        """``Json.Encode(<Lua expression>, opts)`` -> ``(text, err)``."""
        return unpack(self._encode_held(self.json, self.held(src), self.opts(opts)))

    def encode(self, value: Any, opts: Optional[dict] = None):
        """``Json.Encode`` of a Python value rebuilt in Lua (None -> Json.null)."""
        return self.encode_src(to_lua(value), opts)

    def decode(self, text: bytes, opts: Optional[dict] = None):
        return unpack(self.json.Decode(text, self.opts(opts)))

    def reencode(self, text: bytes, dopts: Optional[dict] = None, eopts: Optional[dict] = None):
        """Decode then encode again, entirely inside Lua."""
        return unpack(self._reencode(self.json, text, self.opts(dopts), self.opts(eopts)))

    # -- comparisons --

    def is_null(self, v) -> bool:
        return lua51.lua_type(v) == "table" and bool(self.rawequal(v, self.null))

    def assert_matches(self, lv, expected, path: str = "$") -> None:
        """Asserts that Lua value ``lv`` is what decoding JSON for Python ``expected`` must
        give (``None`` <-> ``Json.null``, list <-> keys exactly 1..n, dict <-> string keys)."""
        if expected is None:
            assert self.is_null(lv), f"{path}: expected Json.null, got {lv!r}"
        elif isinstance(expected, bool):
            assert lv is expected, f"{path}: expected {expected!r}, got {lv!r}"
        elif isinstance(expected, (int, float)):
            assert isinstance(lv, (int, float)) and not isinstance(lv, bool), f"{path}: expected a number, got {lv!r}"
            assert lv == as_double(expected), f"{path}: {lv!r} != {expected!r}"
        elif isinstance(expected, str):
            assert lv == expected.encode("utf-8"), f"{path}: {lv!r} != {expected!r}"
        elif isinstance(expected, list):
            assert lua51.lua_type(lv) == "table" and not self.is_null(lv), f"{path}: expected a table, got {lv!r}"
            keys = list(lv.keys())
            assert all(isinstance(k, int) for k in keys), f"{path}: non-integer keys {keys!r}"
            assert sorted(keys) == list(range(1, len(expected) + 1)), f"{path}: keys {sorted(keys)!r}"
            for i, item in enumerate(expected):
                self.assert_matches(lv[i + 1], item, f"{path}[{i}]")
        elif isinstance(expected, dict):
            assert lua51.lua_type(lv) == "table" and not self.is_null(lv), f"{path}: expected a table, got {lv!r}"
            keys = list(lv.keys())
            assert all(isinstance(k, bytes) for k in keys), f"{path}: non-string keys {keys!r}"
            assert set(keys) == {k.encode("utf-8") for k in expected}, f"{path}: keys differ"
            for k, item in expected.items():
                self.assert_matches(lv[k.encode("utf-8")], item, f"{path}.{k}")
        else:  # pragma: no cover - generator bug
            raise TypeError(type(expected))


@pytest.fixture(scope="module")
def lua() -> Lua:
    return Lua()


# ============================================================ Python helpers ==

def lua_str(b: bytes) -> str:
    """Lua 5.1 string literal for arbitrary bytes (3-digit decimal escapes: never ambiguous)."""
    return '"' + "".join(chr(c) if 32 <= c < 127 and c not in (34, 92) else "\\%03d" % c for c in b) + '"'


def to_lua(v: Any) -> str:
    """Lua source for a Python value; None -> NULL (ns.Json.null)."""
    if v is None:
        return "NULL"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return "(%d)" % v  # Lua reads it as the nearest double, like float(v)
    if isinstance(v, float):
        assert math.isfinite(v), v
        assert not (v == 0 and math.copysign(1.0, v) < 0), "build -0.0 at run time (a literal may be folded to 0)"
        return "(%s)" % repr(v)
    if isinstance(v, str):
        return lua_str(v.encode("utf-8"))
    if isinstance(v, bytes):
        return lua_str(v)
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(to_lua(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ", ".join("[%s] = %s" % (to_lua(k), to_lua(x)) for k, x in v.items()) + "}"
    raise TypeError(type(v))


def as_double(n):
    """What a Python number becomes as a Lua 5.1 number (a double)."""
    if isinstance(n, int) and abs(n) > MAX_EXACT:
        return float(n)
    return n


def lua_image(v: Any, empty=list) -> Any:
    """The Python value that JSON encoded by Lua must parse back to: numbers as doubles, and
    every empty container as ``empty()`` (Lua cannot tell an empty array from an empty object)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return as_double(v)
    if isinstance(v, list):
        return [lua_image(x, empty) for x in v] if v else empty()
    if isinstance(v, dict):
        return {k: lua_image(x, empty) for k, x in v.items()} if v else empty()
    return v


def py_loads(text: bytes) -> Any:
    """Strict Python parse: the text must be valid UTF-8 and use no NaN/Infinity tokens."""
    def bad_constant(name):
        raise AssertionError(f"non-standard JSON constant {name}")
    return json.loads(text.decode("utf-8"), parse_constant=bad_constant)


def kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def assert_same(actual: Any, expected: Any, path: str = "$") -> None:
    """Deep equality that, unlike ``==``, keeps True apart from 1 and lists apart from dicts."""
    ka, ke = kind(actual), kind(expected)
    assert ka == ke, f"{path}: {ka} {actual!r} != {ke} {expected!r}"
    if ka == "array":
        assert len(actual) == len(expected), f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            assert_same(a, e, f"{path}[{i}]")
    elif ka == "object":
        assert actual.keys() == expected.keys(), f"{path}: keys {sorted(actual)!r} != {sorted(expected)!r}"
        for k in expected:
            assert_same(actual[k], expected[k], f"{path}.{k}")
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


# ================================================================ generators ==

CHAR_RANGES = [(0x20, 0x7E), (0x00, 0x1F), (0x7F, 0x9F), (0xA0, 0x7FF), (0x800, 0xD7FF),
               (0xE000, 0xFFFF), (0x10000, 0x10FFFF)]  # every UTF-8 length; no surrogates
SPECIAL_CHARS = ('"\\/\b\f\n\r\t\x00\x1f\x7f\x80\x9f\xa0\xe9'
                 '\U00002028\U00002029\U0000FEFF\U0000FFFD\U0000FFFE\U0000FFFF\U00004E2D'
                 '\U0001F3B5\U0001F600\U00010000\U0010FFFF')
INTERESTING_FLOATS = [0.0, 0.1, 0.2, 0.3, 0.30000000000000004, 1 / 3, 2 / 3, 1e-7, 1e-300, 5e-324,
                      2.2250738585072014e-308, 1.7976931348623157e308, 1e15 + 0.5, 1e16, 1e21, 1e22,
                      123456.789, -0.5, math.pi, -math.e, 4503599627370495.5, 9007199254740993.0, 1e100]
BIG_INTS = [MAX_EXACT, -MAX_EXACT, MAX_EXACT + 1, MAX_EXACT + 2, 2 ** 63 - 1, -(2 ** 63), 2 ** 64,
            10 ** 20, -(10 ** 30), 10 ** 308, 2 ** 1023, 12345678901234567890]


def gen_str(rng: random.Random, maxlen: int = 10) -> str:
    out = []
    for _ in range(rng.randint(0, maxlen)):
        r = rng.random()
        if r < 0.35:
            out.append(chr(rng.randint(0x20, 0x7E)))
        elif r < 0.5:
            out.append(rng.choice(SPECIAL_CHARS))
        else:
            lo, hi = rng.choice(CHAR_RANGES)
            out.append(chr(rng.randint(lo, hi)))
    return "".join(out)


def gen_key(rng: random.Random) -> str:
    r = rng.random()
    if r < 0.15:
        return str(rng.randint(0, 20))  # digit strings stay string keys in Lua
    if r < 0.25:
        return rng.choice(["", "a", "A", "_", "aa", "a\x00", "Z", "z", "é", "\U0001F3B5"])
    return gen_str(rng, 8)


def random_double(rng: random.Random) -> float:
    while True:
        f = struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))[0]
        if math.isfinite(f) and f != 0.0:
            return f


def gen_number(rng: random.Random, floats: bool = True, big: bool = True):
    """A random number; ``floats=False, big=False`` restricts it to integers within 2^53."""
    r = rng.random()
    if r < 0.3:
        return rng.randint(-1000, 1000)
    if r < 0.45 or not (floats or big):
        return rng.randint(-MAX_EXACT, MAX_EXACT)
    if r < 0.55 and big:
        return rng.choice(BIG_INTS)
    if not floats:
        return rng.randint(-MAX_EXACT, MAX_EXACT)
    if r < 0.7:
        return rng.uniform(-1e6, 1e6)
    if r < 0.85:
        return rng.choice(INTERESTING_FLOATS)
    return random_double(rng)


def gen_value(rng: random.Random, depth: int = 0, *, floats: bool = True, big: bool = True,
              nulls: bool = True) -> Any:
    if depth < 5 and rng.random() < (0.85 if depth == 0 else 0.3):
        n = rng.randint(0, 6)
        if rng.random() < 0.5:
            return [gen_value(rng, depth + 1, floats=floats, big=big, nulls=nulls) for _ in range(n)]
        return {gen_key(rng): gen_value(rng, depth + 1, floats=floats, big=big, nulls=nulls) for _ in range(n)}
    r = rng.random()
    if r < 0.35:
        return gen_str(rng)
    if r < 0.75:
        return gen_number(rng, floats, big)
    if r < 0.9 or not nulls:
        return rng.random() < 0.5
    return None


def docs(seed: int, count: int, **kw) -> list:
    rng = random.Random(seed)
    return [gen_value(rng, **kw) for _ in range(count)]


RANDOM_DOCS = docs(20261105, 400)
TEXT_SAFE_DOCS = docs(1105, 300, floats=False, big=False)  # same text as json.dumps


# ============================================================ loading / static ==

def test_files_load_like_addon_files():
    lua = Lua()
    assert lua.global_names() == lua.globals_before, "the codecs must not create globals"
    assert set(lua.ns.keys()) == {b"Json", b"Base64"}
    assert lua.rawequal(lua.returned["Json.lua"], lua.json)
    assert lua.rawequal(lua.returned["Base64.lua"], lua.b64)
    for name in ("Encode", "Decode"):
        assert lua51.lua_type(lua.json[name.encode()]) == "function"
        assert lua51.lua_type(lua.b64[name.encode()]) == "function"
    assert lua.json[b"DEFAULT_MAX_BYTES"] == MB and lua.json[b"DEFAULT_MAX_DEPTH"] == 64
    # Loading again (a /reload re-runs every file) just replaces the modules.
    for name in CODEC_FILES:
        fn, _ = lua.compile(name)
        fn(ADDON_NAME, lua.ns)
    assert lua.encode([1, "a"])[0] == b'[1,"a"]'


@pytest.mark.parametrize("only", CODEC_FILES)
def test_each_file_stands_alone(only):
    """Neither codec needs the other (any TOC order works)."""
    rt = lua51.LuaRuntime(unpack_returned_tuples=True, encoding=None)
    fn = rt.execute("return function(src, name) return loadstring(src, name) end")((ADDON_DIR / only).read_bytes(), b"@x")
    ns = rt.table()
    fn(ADDON_NAME, ns)
    if only == "Json.lua":
        assert list(ns.keys()) == [b"Json"]
        assert ns[b"Json"].Encode(ns[b"Json"].Decode(b'{"a":[1]}')) == b'{"a":[1]}'
    else:
        assert list(ns.keys()) == [b"Base64"]
        assert ns[b"Base64"].Decode(ns[b"Base64"].Encode(b"\x00\xff")) == b"\x00\xff"


def test_files_work_without_an_addon_namespace(lua):
    for name, key in (("Json.lua", b"Encode"), ("Base64.lua", b"Encode")):
        fn, _ = lua.compile(name)
        module = fn()
        assert lua51.lua_type(module[key]) == "function"


_LONG_OPEN = re.compile(r"\[(=*)\[")


def strip_lua(src: str) -> tuple[str, list[str]]:
    """(code with comments removed and string literals replaced by "", raw short-string bodies)."""
    out, strings, i, n = [], [], 0, len(src)
    while i < n:
        if src.startswith("--", i):
            m = _LONG_OPEN.match(src, i + 2)
            if m:
                j = src.index("]" + m.group(1) + "]", m.end())
                i = j + len(m.group(1)) + 2
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j
            continue
        m = _LONG_OPEN.match(src, i)
        if m:
            j = src.index("]" + m.group(1) + "]", m.end())
            out.append('""')
            i = j + len(m.group(1)) + 2
            continue
        c = src[i]
        if c in "\"'":
            j, body = i + 1, []
            while src[j] != c:
                if src[j] == "\\":
                    body.append(src[j:j + 2])
                    j += 2
                    continue
                assert src[j] != "\n", "unterminated string"
                body.append(src[j])
                j += 1
            strings.append("".join(body))
            out.append('""')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), strings


FORBIDDEN_ANYWHERE = re.compile(r"\bbit32\b|\bbit\s*[.\[:]|\butf8\b|\bloadstring\b|\bgoto\b", re.IGNORECASE)
FORBIDDEN_IDENTIFIERS = {"bit", "bit32", "utf8", "loadstring", "load", "loadfile", "dofile", "require",
                         "goto", "setfenv", "getfenv", "debug", "io", "os", "newproxy", "collectgarbage",
                         "module", "string.dump", "unpack"}


@pytest.mark.parametrize("fname", CODEC_FILES)
def test_sources_use_plain_lua51_only(fname):
    raw = (ADDON_DIR / fname).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "a BOM breaks the client's loader"
    src = raw.decode("utf-8")
    assert src.splitlines()[0] == "local ADDON, ns = ...", "every addon file starts with the varargs line"
    # Not even mentioned in comments: no bit/bit32/utf8 library, no loadstring, no goto.
    hits = [m.group(0) for m in FORBIDDEN_ANYWHERE.finditer(src)]
    assert not hits, hits
    code, strings = strip_lua(src)
    dotted = {re.sub(r"\s+", "", n)
              for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)?", code)}
    names = dotted | {d.split(".")[0] for d in dotted}
    assert not (names & FORBIDDEN_IDENTIFIERS), sorted(names & FORBIDDEN_IDENTIFIERS)
    # Lua 5.2+ syntax and operators (strings and comments excluded).
    for rx, what in [(r"//", "integer division"), (r"&|\|", "bitwise and/or"), (r"~(?!=)", "bitwise not/xor"),
                     (r"<<|>>", "shift"), (r"::", "label")]:
        assert not re.search(rx, code), f"{fname}: {what}"
    for body in strings:
        for esc in re.findall(r"\\(.)", body, re.DOTALL):
            assert esc in "abfnrtv\\\"'\n0123456789", f"{fname}: escape \\{esc} is not Lua 5.1"


# ================================================================ JSON encode ==

ENCODE_VARIANTS = {
    "default": ({}, list),
    "unsorted": ({"sortKeys": False}, list),
    "empty_object": ({"emptyTable": "object"}, dict),
    "indent": ({"indent": 2}, list),
    "ascii": ({"asciiOnly": True, "indent": "\t"}, list),
}


@pytest.mark.parametrize("variant", sorted(ENCODE_VARIANTS))
def test_lua_encoded_json_parses_in_python_to_the_same_value(lua, variant):
    opts, empty = ENCODE_VARIANTS[variant]
    for i, doc in enumerate(RANDOM_DOCS):
        text, err = lua.encode(doc, opts)
        assert err is None, f"doc {i}: {err!r}"
        assert_same(py_loads(text), lua_image(doc, empty), f"doc{i}")
        if opts.get("asciiOnly"):
            assert max(text, default=0) < 0x7F, f"doc {i}: non-ASCII output"


def test_lua_output_is_byte_identical_to_python(lua):
    """Where the number rules coincide (integers within 2^53), Lua's output equals Python's
    json.dumps byte for byte: same escapes, same key order, same pretty-printing."""
    for i, doc in enumerate(TEXT_SAFE_DOCS):
        norm = lua_image(doc)
        cases = [
            ({}, dict(separators=(",", ":"), ensure_ascii=False)),
            ({"asciiOnly": True}, dict(separators=(",", ":"), ensure_ascii=True)),
            ({"indent": 2}, dict(indent=2, ensure_ascii=False)),
            ({"indent": "\t", "asciiOnly": True}, dict(indent="\t", ensure_ascii=True)),
            ({"indent": 0}, dict(indent=0, ensure_ascii=False)),
        ]
        for opts, pyopts in cases:
            text, err = lua.encode(doc, opts)
            assert err is None, err
            expected = json.dumps(norm, sort_keys=True, **pyopts).encode("utf-8")
            assert text == expected, f"doc {i} {opts}: {text!r} != {expected!r}"


BOUNDARY_CHARS = [0x00, 0x08, 0x09, 0x0A, 0x0C, 0x0D, 0x1F, 0x20, 0x22, 0x2F, 0x5C, 0x7E, 0x7F, 0x80, 0x9F, 0xA0,
                  0xFF, 0x100, 0x7FF, 0x800, 0xFFF, 0x1000, 0xD7FF, 0xE000, 0xFEFF, 0xFFFD, 0xFFFE, 0xFFFF,
                  0x10000, 0x1F3B5, 0xFFFFF, 0x100000, 0x10FFFF]


def test_every_ascii_character_and_utf8_boundary_matches_python(lua):
    """Each character class edge (1-4 byte UTF-8 limits, surrogate neighbours, every ASCII
    byte) encodes exactly like json.dumps and decodes back from Python's escapes."""
    samples = [chr(c) for c in range(128)] + [chr(c) for c in BOUNDARY_CHARS]
    samples.append("".join(samples))
    for s in samples:
        for ascii_only in (False, True):
            text, err = lua.encode(s, {"asciiOnly": ascii_only})
            assert (text, err) == (json.dumps(s, ensure_ascii=ascii_only).encode("utf-8"), None), repr(s)
            assert lua.decode(text) == (s.encode("utf-8"), None)
        if "\\" not in s:  # same escapes with upper-case hex digits
            upper_hex = re.sub(r"\\u([0-9a-f]{4})", lambda m: "\\u" + m.group(1).upper(), json.dumps(s))
            assert lua.decode(upper_hex.encode()) == (s.encode("utf-8"), None)


def test_encode_leaves_the_input_untouched(lua):
    out = lua.run("local t = {b = {1, 2, {c = 'x'}}, a = 'y'}\n"
                  "local first = J.Encode(t)\n"
                  "local n = 0 for _ in pairs(t) do n = n + 1 end\n"
                  "return first == J.Encode(t), n, #t.b, getmetatable(t) == nil, getmetatable(t.b) == nil")
    assert out == (True, 2, 3, True, True)


def test_encode_is_deterministic_and_sorted_by_bytes(lua):
    keys = ["b", "B", "a", "_", "Z", "z", "aa", "a\x00", "é", "\U0001F3B5", "~", "1", "10", "9", ""]
    rng = random.Random(7)
    outputs = set()
    for _ in range(10):
        order = keys[:]
        rng.shuffle(order)
        # Insert in a different order each time, with deletions in between to vary the hash layout.
        src = ("local t = {}\n" + "".join("t[%s] = %d\nt['tmp%d'] = 1\n" % (lua_str(k.encode()), keys.index(k), n)
                                          for n, k in enumerate(order))
               + "".join("t['tmp%d'] = nil\n" % n for n in range(len(order)))
               + "return J.Encode(t)")
        outputs.add(lua.run(src))
    assert len(outputs) == 1
    text = outputs.pop()
    assert list(json.loads(text)) == sorted(keys)  # Python sorts str by code point = UTF-8 byte order
    unsorted, err = lua.encode({k: 1 for k in keys}, {"sortKeys": False})
    assert err is None and sorted(json.loads(unsorted)) == sorted(keys)


def test_encode_numbers(lua):
    rng = random.Random(99)
    values = [random_double(rng) for _ in range(1500)] + INTERESTING_FLOATS + \
        [rng.randint(-MAX_EXACT, MAX_EXACT) for _ in range(300)] + BIG_INTS
    text, err = lua.encode(values)
    assert err is None, err
    pieces = text[1:-1].split(b",")
    assert len(pieces) == len(values)
    number = re.compile(rb"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?")
    for piece, v in zip(pieces, values):
        assert number.fullmatch(piece), piece
        parsed = json.loads(piece)
        assert parsed == as_double(v), (piece, v)  # exact round trip of the double
        d = float(as_double(v))
        if d.is_integer() and abs(d) <= MAX_EXACT:
            assert re.fullmatch(rb"-?[0-9]+", piece), f"integer {v!r} written as {piece!r}"
        mantissa = re.sub(rb"[eE].*", b"", piece).replace(b"-", b"").replace(b".", b"").lstrip(b"0")
        assert len(mantissa) <= 17 or re.fullmatch(rb"-?[0-9]+", piece), piece
    exact = {"0": b"0", "-7": b"-7", "2^53": b"9007199254740992", "-(2^53)": b"-9007199254740992",
             "1e15": b"1000000000000000", "0.1": b"0.1", "1/3": b"0.3333333333333333", "-0.5": b"-0.5",
             "123456.789": b"123456.789", "1e300": b"1e+300", "2.5": b"2.5", "100": b"100"}
    for src, expected in exact.items():
        assert lua.encode_src(src) == (expected, None), src
    # Negative zero survives (built at run time: a -0.0 literal may be folded to 0).
    assert lua.run("local z = 0; z = -z; return J.Encode(z)") == b"-0"
    assert lua.run("local v = J.Decode('-0'); return 1 / v < 0") is True


@pytest.mark.parametrize("src, where", [
    ("0/0", "$"), ("1/0", "$"), ("-1/0", "$"), ("{1, 2, 0/0}", "$[3]"),
    ("{a = {b = {math.huge}}}", "$.a.b[1]"), ('{["x y"] = -math.huge}', '$["x y"]'),
])
def test_encode_rejects_non_finite_numbers(lua, src, where):
    text, err = lua.encode_src(src)
    assert text is None
    assert err.startswith(b"Json.Encode: cannot encode") and b"(JSON has no non-finite numbers)" in err, err
    assert err.endswith(b" at " + where.encode()), err


def test_encode_detects_cycles_but_allows_shared_tables(lua):
    assert lua.run("local t = {} t.me = t return J.Encode(t)") == (None, b"Json.Encode: cycle detected (a table contains itself) at $.me")
    text, err = lua.run("local a, b = {}, {} a.b = b b.list = {1, a} return J.Encode({root = a})")
    assert text is None and err == b"Json.Encode: cycle detected (a table contains itself) at $.root.b.list[2]"
    text, err = lua.run("local t = {} t[1] = t return J.Encode(t)")
    assert text is None and err.endswith(b"at $[1]")
    shared = lua.run("local s = {x = 1} return J.Encode({a = s, b = s, c = {s, s}})")
    assert shared == b'{"a":{"x":1},"b":{"x":1},"c":[{"x":1},{"x":1}]}'


@pytest.mark.parametrize("src, fragment", [
    ("print", "cannot encode a value of type function at $"),
    ("{f = print}", "cannot encode a value of type function at $.f"),
    ("{1, {2, coroutine.create(function() end)}}", "cannot encode a value of type thread at $[2][2]"),
    ("{u = newproxy()}", "cannot encode a value of type userdata at $.u"),
    ("{[true] = 1}", "cannot encode an object key of type boolean"),
    ("{x = {[{}] = 1}}", "cannot encode an object key of type table (keys must be strings or numbers) at $.x"),
    ("{[1/0] = 1}", "cannot encode a non-finite number as an object key at $"),
    ('{[1] = "a", ["1"] = "b"}', 'duplicate object key "1"'),
    ('{x = {[2] = "a", ["2"] = "b"}}', 'duplicate object key "2" (a string key and a number key both become this key) at $.x'),
])
def test_encode_rejects_unsupported_values(lua, src, fragment):
    text, err = lua.encode_src(src)
    assert text is None and fragment.encode() in err, err


@pytest.mark.parametrize("src, expected", [
    ("nil", b"null"), ("NULL", b"null"), ("true", b"true"), ("false", b"false"), ('""', b'""'),
    ("{}", b"[]"), ("{{}, {{}}}", b"[[],[[]]]"), ("{NULL, 1, NULL}", b"[null,1,null]"),
    ('{a = NULL}', b'{"a":null}'),
    ('{[1] = "a", [3] = "c"}', b'{"1":"a","3":"c"}'),              # sparse -> object
    ('{"a", "b", n = 2}', b'{"1":"a","2":"b","n":2}'),              # mixed -> object
    ('{[1429] = true, [0] = 1, [-2] = 2, [1.5] = 3}', b'{"-2":2,"0":1,"1.5":3,"1429":true}'),
    ('{[2] = "b", [1] = "a"}', b'["a","b"]'),                       # keys 1..n in any order
    ('{"x\\0y\\1\\31\\127"}', b'["x\\u0000y\\u0001\\u001f\x7f"]'),
    ('"\\226\\128\\168\\226\\128\\169"', b'"\xe2\x80\xa8\xe2\x80\xa9"'),
    ('"/<>&\'"', b'"/<>&\'"'),
])
def test_encode_table_shapes_and_scalars(lua, src, expected):
    assert lua.encode_src(src) == (expected, None)


def test_encode_empty_table_option(lua):
    assert lua.encode_src("{a = {}, b = {{}}}", {"emptyTable": "object"}) == (b'{"a":{},"b":[{}]}', None)
    assert lua.encode_src("{}", {"emptyTable": "array"}) == (b"[]", None)
    assert lua.encode_src("{}", {"indent": 2}) == (b"[]", None)


def test_encode_depth_limit(lua):
    nest = ("local function nest(n) local t = {} local cur = t for i = 2, n do local x = {} cur[1] = x cur = x end"
            " return t end\n")
    assert lua.run(nest + "return J.Encode(nest(64))") == b"[" * 64 + b"]" * 64
    text, err = lua.run(nest + "return J.Encode(nest(65))")
    assert text is None and err == b"Json.Encode: nesting deeper than the 64-level limit at $" + b"[1]" * 64
    text, err = lua.run(nest + "return J.Encode(nest(4), {maxDepth = 3})")
    assert text is None and b"nesting deeper than the 3-level limit" in err
    assert lua.run(nest + "return J.Encode(nest(200), {maxDepth = 200})") == b"[" * 200 + b"]" * 200


MALFORMED_UTF8 = [b"\xff", b"\xfe\xff", b"\x80", b"\xbf\xbf", b"\xc0\x80", b"\xc1\xbf", b"\xc2", b"\xc2A",
                  b"\xe0\x80\x80", b"\xe0\xa0", b"\xe2\x82", b"\xe2\x82A", b"\xed\xa0\x80", b"\xed\xbf\xbf",
                  b"\xf0\x80\x80\x80", b"\xf0\x90\x80", b"\xf4\x90\x80\x80", b"\xf5\x80\x80\x80",
                  b"\xf8\x88\x80\x80\x80", b"ok\xe9t\xe9", "café".encode("latin-1"),
                  b"a\xf0\x9f\x8e\xb5b\xf0\x9f\x8e", b"\xc3\xa9\xa9\xc3"]


def test_encode_rejects_invalid_utf8_with_its_position(lua):
    for bad in MALFORMED_UTF8:
        with pytest.raises(UnicodeDecodeError) as info:
            bad.decode("utf-8")
        text, err = lua.encode_src("{s = %s}" % lua_str(bad))
        # The reported byte is where Python's strict decoder fails too.
        assert (text, err) == (None, b"Json.Encode: invalid UTF-8 at byte %d of a string at $.s" % (info.value.start + 1))
    text, err = lua.encode_src("{[%s] = 1}" % lua_str(b"k\xff"))
    assert text is None and err.startswith(b"Json.Encode: invalid UTF-8 at byte 2 of an object key at $")


def test_encode_replace_invalid_matches_python(lua):
    rng = random.Random(3)
    samples = MALFORMED_UTF8 + [bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 24))) for _ in range(300)]
    samples += ["valid é中\U0001F3B5".encode("utf-8")]
    for bad in samples:
        for ascii_only in (False, True):
            text, err = lua.encode_src(lua_str(bad), {"replaceInvalid": True, "asciiOnly": ascii_only})
            assert err is None, err
            expected = json.dumps(bad.decode("utf-8", "replace"), ensure_ascii=ascii_only).encode("utf-8")
            assert text == expected, (bad, text, expected)


def test_encode_option_validation(lua):
    assert lua.encode_src("1", {"emptyTable": "list"})[1] == b'Json.Encode: opts.emptyTable must be "array" or "object"'
    assert lua.encode_src("1", {"maxDepth": 0})[1] == b"Json.Encode: opts.maxDepth must be a number >= 1"
    assert lua.encode_src("1", {"indent": lua.null})[1] == b"Json.Encode: opts.indent must be a number or a string"
    assert unpack(lua.json.Encode(1, b"x")) == (None, b"Json.Encode: opts must be a table")
    assert lua.encode_src("{1}", {"indent": True}) == (b"[\n  1\n]", None)
    assert lua.encode_src("{1}", {"indent": -3}) == (b"[\n1\n]", None)


def test_null_sentinel_is_read_only(lua):
    assert lua.run("return tostring(NULL), getmetatable(NULL), pcall(function() NULL.x = 1 end)")[:3] == \
        (b"Json.null", b"Json.null", False)
    assert lua.run("return (pcall(setmetatable, NULL, {}))") is False
    assert lua.run("return next(NULL)") is None


# ================================================================ JSON decode ==

DUMP_STYLES = {
    "compact": dict(separators=(",", ":"), ensure_ascii=False),
    "python_default": dict(),
    "indent2": dict(indent=2, ensure_ascii=False),
    "indent_tab_ascii_sorted": dict(indent="\t", ensure_ascii=True, sort_keys=True),
    "crlf_spaced": dict(indent=" ", separators=(" , ", " : "), ensure_ascii=False),
}


@pytest.mark.parametrize("style", sorted(DUMP_STYLES))
def test_python_json_decodes_in_lua(lua, style):
    for i, doc in enumerate(RANDOM_DOCS):
        text = json.dumps(doc, **DUMP_STYLES[style])
        if style == "crlf_spaced":
            text = "\r\n\t " + text.replace("\n", "\r\n") + " \n"  # raw newlines only occur between tokens
        value, err = lua.decode(text.encode("utf-8"), {"null": lua.null})
        assert err is None, f"doc {i}: {err!r}"
        lua.assert_matches(value, doc, f"doc{i}")


def test_lua_round_trip_is_idempotent(lua):
    for i, doc in enumerate(RANDOM_DOCS):
        text, err = lua.encode(doc)
        assert err is None, err
        again, err = lua.reencode(text, {"null": lua.null})
        assert (again, err) == (text, None), f"doc {i}"
        pretty, err = lua.encode(doc, {"indent": 2, "asciiOnly": True})
        assert err is None and lua.reencode(pretty, {"null": lua.null}) == (text, None), f"doc {i}"
        value, err = lua.decode(text, {"null": lua.null})
        assert err is None
        lua.assert_matches(value, py_loads(text), f"doc{i}")


@pytest.mark.parametrize("text", [
    "0", "-0", "1", "-1", "10", "1.5", "-1.5", "1e2", "1E2", "1e+2", "1e-2", "0.5e-3", "1.25E+10",
    "123456789012345678901234567890", "-9007199254740993", "9007199254740992", "1e-400", "2.5e-324",
    "1.7976931348623157e308", "0.30000000000000004", "4.9406564584125e-324", "1" + "0" * 300,
    "0." + "0" * 300 + "1", "3.141592653589793238462643383279",
])
def test_decode_numbers(lua, text):
    value, err = lua.decode(text.encode())
    assert err is None, err
    assert isinstance(value, (int, float)) and value == float(text)
    value, err = lua.decode(b"[" + text.encode() + b"]")
    assert err is None and value[1] == float(text)


def test_decode_strings_and_escapes(lua):
    bs = "\\"  # JSON text below is built with explicit backslashes: every escape is a real escape
    cases = {
        '"' + bs + '"' + bs + bs + bs + "/" + bs + "b" + bs + "f" + bs + "n" + bs + "r" + bs + "t" + '"':
            '"\\/\b\f\n\r\t',
        '"' + bs + "u0000" + bs + "u001f" + bs + "u007f" + bs + "u0080" + '"': "\x00\x1f\x7f\x80",
        '"' + bs + "u00e9" + bs + "u00E9" + bs + "u4e2d" + bs + "uFFFF" + bs + "uFeFf" + '"':
            "\xe9\xe9\U00004E2D\U0000FFFF\U0000FEFF",
        '"' + bs + "ud83c" + bs + "udfb5" + bs + "uD83C" + bs + "uDFB5" + bs + "udbff" + bs + "udfff"
        + bs + "ud800" + bs + "udc00" + '"': "\U0001F3B5\U0001F3B5\U0010FFFF\U00010000",
        '"a' + bs + "u0062c" + '"': "abc",
        '"raw \xe9 \U00004E2D \U0001F3B5 \x7f \U00002028"': "raw \xe9 \U00004E2D \U0001F3B5 \x7f \U00002028",
        '""': "",
        '"' + "x" * 5000 + bs + "n" + "y" * 5000 + '"': "x" * 5000 + "\n" + "y" * 5000,
    }
    for text, expected in cases.items():
        assert json.loads(text) == expected  # the oracle agrees
        value, err = lua.decode(text.encode("utf-8"))
        assert (value, err) == (expected.encode("utf-8"), None), ascii(text)
    escaped = [t for t in cases if bs + "u" in t]
    assert len(escaped) == 4, "the \\u cases must really contain escapes"


def test_decode_null_handling(lua):
    assert lua.decode(b"null") == (None, None)  # a value of nil, and no error
    assert lua.is_null(lua.decode(b"null", {"null": lua.null})[0])
    obj, _ = lua.decode(b'{"a":null,"b":1,"c":[null]}')
    assert set(obj.keys()) == {b"b", b"c"} and list(obj[b"c"].keys()) == []
    arr, _ = lua.decode(b"[1,null,3,null]")
    assert dict(arr.items()) == {1: 1, 3: 3}  # holes keep later elements at their index
    arr, _ = lua.decode(b"[1,null,3]", {"null": lua.null})
    assert list(arr.keys()) == [1, 2, 3] and lua.is_null(arr[2])
    assert lua.decode(b"[null]", {"null": False})[0][1] is False
    # A sentinel null survives a full round trip.
    assert lua.reencode(b'{"a":null,"b":[null,{"c":null}]}', {"null": lua.null}) == (b'{"a":null,"b":[null,{"c":null}]}', None)


def test_decode_duplicate_keys_last_one_wins(lua):
    obj, err = lua.decode(b'{"a":1,"b":2,"a":3}')
    assert err is None and obj[b"a"] == 3 and obj[b"b"] == 2
    obj, err = lua.decode(b'{"a":1,"a":null}')
    assert err is None and list(obj.keys()) == []  # same as Python: the later null wins
    assert json.loads('{"a":1,"b":2,"a":3}') == {"a": 3, "b": 2}


def test_decode_byte_order_mark_is_skipped(lua):
    value, err = lua.decode(b'\xef\xbb\xbf {"a":[1]}')
    assert err is None and value[b"a"][1] == 1
    assert b"unexpected byte 0xEF" in lua.decode(b'[1,\xef\xbb\xbf2]')[1]


# (input, message fragment, byte position, Python's json also rejects it)
MALFORMED_JSON = [
    (b"", "unexpected end of input (expected a value)", 1, True),
    (b"   ", "unexpected end of input (expected a value)", 4, True),
    (b"[1,2,]", "trailing comma in array", 6, True),
    (b'{"a":1,}', "trailing comma in object", 8, True),
    (b"[1 2]", "expected ',' or ']' after array element, found character '2'", 4, True),
    (b"[1,,2]", "unexpected character ',' (expected a value)", 4, True),
    (b"[,1]", "unexpected character ',' (expected a value)", 2, True),
    (b'{"a" 1}', "expected ':' after object key, found character '1'", 6, True),
    (b'{"a":1,"b"}', "expected ':' after object key, found character '}'", 11, True),
    (b"{'a':1}", "expected a string key in object, found character", 2, True),
    (b"{a:1}", "expected a string key in object, found character 'a'", 2, True),
    (b'{"a":1 "b":2}', "expected ',' or '}' after object member, found character '\"'", 8, True),
    (b'{"a":}', "unexpected character '}' (expected a value)", 6, True),
    (b"[01]", "leading zeros are not allowed", 2, True),
    (b"[-01]", "leading zeros are not allowed", 3, True),
    (b"01.5", "leading zeros are not allowed", 1, True),
    (b"[1.]", "expected a digit after '.'", 4, True),
    (b"[.5]", "unexpected character '.' (expected a value)", 2, True),
    (b"[1e]", "expected a digit in the exponent", 4, True),
    (b"[1e+]", "expected a digit in the exponent", 4, True),
    (b"1.5e", "expected a digit in the exponent", 5, True),
    (b"[+1]", "unexpected character '+' (expected a value)", 2, True),
    (b"[-]", "expected a digit after '-'", 2, True),
    (b"[- 1]", "expected a digit after '-'", 2, True),
    (b"--1", "expected a digit after '-'", 1, True),
    (b"0x10", "unexpected character 'x' after the JSON value", 2, True),
    (b"1 2", "unexpected character '2' after the JSON value", 3, True),
    (b"[1]]", "unexpected character ']' after the JSON value", 4, True),
    (b"tru", "invalid literal (expected true)", 1, True),
    (b"fals", "invalid literal (expected false)", 1, True),
    (b"nul", "invalid literal (expected null)", 1, True),
    (b"True", "unexpected character 'T' (expected a value)", 1, True),
    (b"nulll", "unexpected character 'l' after the JSON value", 5, True),
    (b'"abc', "unterminated string", 1, True),
    (b'["a\\"]', "unterminated string", 2, True),
    (b'"a\\', "unterminated string", 1, True),
    (b'"a\tb"', "unescaped control character (byte 0x09) in string", 3, True),
    (b'"a\nb"', "unescaped control character (byte 0x0A) in string", 3, True),
    (b'"a\x00b"', "unescaped control character (byte 0x00) in string", 3, True),
    (b'"\x1f"', "unescaped control character (byte 0x1F) in string", 2, True),
    (b'"\\x41"', "invalid escape sequence (backslash followed by character 'x')", 2, True),
    (b'"\\U0041"', "invalid escape sequence (backslash followed by character 'U')", 2, True),
    (b'"\\u12"', "invalid \\u escape (expected 4 hex digits)", 2, True),
    (b'"\\u12G4"', "invalid \\u escape (expected 4 hex digits)", 2, True),
    (b'"\\ud83c\\u12"', "invalid \\u escape (expected 4 hex digits)", 8, True),
    (b"/* c */ 1", "unexpected character '/' (expected a value)", 1, True),
    (b"// c\n1", "unexpected character '/' (expected a value)", 1, True),
    (b"[1] // c", "unexpected character '/' after the JSON value", 5, True),
    (b"[1] x", "unexpected character 'x' after the JSON value", 5, True),
    (b'{"a":1}{"b":2}', "unexpected character '{' after the JSON value", 8, True),
    (b"[", "unexpected end of input (expected a value)", 2, True),
    (b"{", "unexpected end of input (unclosed object)", 2, True),
    (b"[1,[2,[3]", "unexpected end of input (unclosed array)", 10, True),
    (b'{"a"', "expected ':' after object key, found end of input", 5, True),
    (b'{"a":1', "unexpected end of input (unclosed object)", 7, True),
    (b"\x0b1", "unexpected byte 0x0B (expected a value)", 1, True),     # \v is not JSON whitespace
    (b"\x0c1", "unexpected byte 0x0C (expected a value)", 1, True),     # nor is \f
    (b"\xc2\xa01", "unexpected byte 0xC2 (expected a value)", 1, True),  # nor NBSP
    (b"\xc3\xa9", "unexpected byte 0xC3 (expected a value)", 1, True),
    (b"\xef\xbb\xbf\xef\xbb\xbf1", "unexpected byte 0xEF (expected a value)", 4, True),
    (b'"\xff"', "invalid UTF-8 in string", 2, True),
    (b'"\xc0\x80"', "invalid UTF-8 in string", 2, True),
    (b'"\xed\xa0\x80"', "invalid UTF-8 in string", 2, True),
    (b'"\xe2\x82"', "invalid UTF-8 in string", 2, True),
    (b'"ok\xf4\x90\x80\x80"', "invalid UTF-8 in string", 4, True),
    (b'"a\\n\xe9"', "invalid UTF-8 in string", 5, True),
    # Stricter than Python's json module (which accepts these):
    (b"NaN", "unexpected character 'N' (expected a value)", 1, False),
    (b"Infinity", "unexpected character 'I' (expected a value)", 1, False),
    (b"-Infinity", "expected a digit after '-'", 1, False),
    (b"[1e400]", "number out of range (too large for a double)", 2, False),
    (b"-1e400", "number out of range (too large for a double)", 1, False),
    (b'"\\ud800"', "unpaired surrogate in \\u escape (high surrogate not followed by a low one)", 2, False),
    (b'"\\ud800x"', "unpaired surrogate in \\u escape (high surrogate not followed by a low one)", 2, False),
    (b'"\\udc00"', "unpaired surrogate in \\u escape (low surrogate without a high one)", 2, False),
    (b'"\\ud800\\u0041"', "invalid surrogate pair in \\u escape (expected a low surrogate)", 8, False),
    (b'"\\ud800\\ud800"', "invalid surrogate pair in \\u escape (expected a low surrogate)", 8, False),
]


@pytest.mark.parametrize("text, fragment, pos, python_rejects", MALFORMED_JSON,
                         ids=[repr(c[0])[:40] for c in MALFORMED_JSON])
def test_malformed_json_is_rejected_with_position(lua, text, fragment, pos, python_rejects):
    value, err = lua.decode(text)
    assert value is None and err is not None
    line = text[:pos - 1].count(b"\n") + 1
    col = pos - (text.rfind(b"\n", 0, pos - 1) + 1)
    assert err.startswith(b"Json.Decode: ") and fragment.encode() in err, err
    assert err.endswith(f" at line {line}, column {col} (byte {pos})".encode()), err
    if python_rejects:
        with pytest.raises(ValueError):
            json.loads(text.decode("utf-8"))  # UnicodeDecodeError is a ValueError too
    else:
        json.loads(text.decode("utf-8"))


def test_decode_error_reports_line_and_column(lua):
    text = b'{\n  "songs": [\n    {"title": "A"},\n    {"title": "B",}\n  ]\n}'
    value, err = lua.decode(text)
    assert value is None
    assert err == b"Json.Decode: trailing comma in object at line 4, column 19 (byte 54)"
    assert text[53:54] == b"}" and text.split(b"\n")[3][18:19] == b"}"


def test_depth_bomb(lua):
    assert lua.decode(b"[" * 64 + b"]" * 64)[1] is None
    value, err = lua.decode(b"[" * 65 + b"]" * 65)
    assert value is None and err == b"Json.Decode: nesting deeper than the 64-level limit at line 1, column 65 (byte 65)"
    assert lua.decode(b'{"a":' * 64 + b"1" + b"}" * 64)[1] is None
    value, err = lua.decode(b'{"a":' * 65 + b"1" + b"}" * 65)
    assert value is None and err.endswith(b"(byte %d)" % (64 * 5 + 1))
    # A megabyte of brackets fails fast at level 65 (no recursion, stack or time blow-up).
    bomb = b"[" * 1_000_000
    started = time.perf_counter()
    value, err = lua.decode(bomb)
    assert value is None and b"nesting deeper than the 64-level limit" in err
    assert time.perf_counter() - started < 0.5
    mixed = (b'[{"a":' * 40) + b"0" + (b"}]" * 40)
    assert b"nesting deeper than the 64-level limit" in lua.decode(mixed)[1]
    assert lua.decode(b"[[[1]]]", {"maxDepth": 3})[1] is None
    assert b"nesting deeper than the 3-level limit" in lua.decode(b"[[[[1]]]]", {"maxDepth": 3})[1]
    assert lua.decode(b"[" * 300 + b"]" * 300, {"maxDepth": 300})[1] is None


def test_size_bomb(lua):
    def doc(size: int) -> bytes:
        return b'["' + b"a" * (size - 4) + b'"]'

    value, err = lua.decode(doc(MB))
    assert err is None and len(value[1]) == MB - 4
    value, err = lua.decode(doc(MB + 1))
    assert value is None and err == b"Json.Decode: input is 1048577 bytes, over the limit of 1048576 bytes"
    huge = b"[" + b"1," * (8 * MB) + b"1]"
    started = time.perf_counter()
    value, err = lua.decode(huge)
    assert value is None and b"over the limit" in err
    assert time.perf_counter() - started < 0.5  # rejected before any parsing
    assert lua.decode(b"[1,2,3,4,5]", {"maxBytes": 10})[1] == b"Json.Decode: input is 11 bytes, over the limit of 10 bytes"
    assert lua.decode(b"[1,2,3,4]", {"maxBytes": 10})[1] is None
    value, err = lua.decode(doc(3 * MB), {"maxBytes": 4 * MB})
    assert err is None and len(value[1]) == 3 * MB - 4


def test_decode_argument_validation(lua):
    for bad in (None, 42, True):
        assert unpack(lua.json.Decode(bad)) == (None, b"Json.Decode: expected a string, got " +
                                                {type(None): b"nil", int: b"number", bool: b"boolean"}[type(bad)])
    assert unpack(lua.json.Decode(lua.rt.table())) == (None, b"Json.Decode: expected a string, got table")
    assert unpack(lua.json.Decode(b"1", b"x")) == (None, b"Json.Decode: opts must be a table")
    assert lua.decode(b"1", {"maxBytes": -1})[1] == b"Json.Decode: opts.maxBytes must be a number >= 0"
    assert lua.decode(b"1", {"maxBytes": math.nan})[1] == b"Json.Decode: opts.maxBytes must be a number >= 0"
    assert lua.decode(b"1", {"maxDepth": 0})[1] == b"Json.Decode: opts.maxDepth must be a number >= 1"
    assert lua.decode(b"1", {"maxDepth": math.nan})[1] == b"Json.Decode: opts.maxDepth must be a number >= 1"
    assert lua.decode(b"[[1]]", {"maxDepth": 1.9})[1] == \
        b"Json.Decode: nesting deeper than the 1-level limit at line 1, column 2 (byte 2)"
    assert lua.decode(b"1", {"maxBytes": math.inf, "maxDepth": math.inf}) == (1, None)


# ===================================================================== base64 ==

def b64_variants(data: bytes) -> dict:
    std = base64.b64encode(data)
    url = base64.urlsafe_b64encode(data)
    return {"std_pad": std, "std_nopad": std.rstrip(b"="), "url_pad": url, "url_nopad": url.rstrip(b"=")}


BASE64_SAMPLES = ([bytes(range(256)), bytes(range(255, -1, -1)), b"\x00" * 7, b"\xff" * 9]
                  + [bytes(random.Random(n).getrandbits(8) for _ in range(n)) for n in range(0, 200)]
                  + [random.Random(n).randbytes(n) for n in (1000, 4095, 4096, 4097, 65535, 65536, 100003)])


def test_base64_encode_matches_python(lua):
    enc = lua.b64.Encode
    for data in BASE64_SAMPLES:
        v = b64_variants(data)
        assert enc(data) == v["std_pad"]
        assert enc(data, False, True) == v["std_pad"]
        assert enc(data, False, False) == v["std_nopad"]
        assert enc(data, True) == v["url_nopad"]  # url-safe defaults to no padding
        assert enc(data, True, True) == v["url_pad"]
        assert enc(data, True, False) == v["url_nopad"]
        assert enc(data, None, None) == v["std_pad"]


def test_base64_decodes_python_output(lua):
    dec = lua.b64.Decode
    rng = random.Random(5)
    for data in BASE64_SAMPLES:
        for text in b64_variants(data).values():
            assert dec(text) == data
        assert dec(base64.encodebytes(data)) == data  # MIME: newline every 76 characters
        if len(data) < 300:
            text = bytearray(base64.b64encode(data))
            for _ in range(rng.randint(1, 8)):  # whitespace anywhere, even inside the padding
                text[rng.randint(0, len(text)):0] = rng.choice([b" ", b"\t", b"\r\n", b"\n", b"\f\v", b"  \n "])
            assert dec(bytes(text)) == data
            mixed = bytes(rng.choice((c, {43: 45, 47: 95}.get(c, c))) for c in base64.b64encode(data))
            assert dec(mixed) == data  # standard and url-safe characters mixed


def test_base64_round_trips_in_lua(lua):
    check = lua.rt.eval("function(B, s, u, p) local e = B.Encode(s, u, p) return B.Decode(e) == s end")
    for data in BASE64_SAMPLES:
        for urlsafe in (False, True):
            for pad in (False, True):
                assert check(lua.b64, data, urlsafe, pad) is True


@pytest.mark.parametrize("text, fragment", [
    (b"QQ*=", "invalid character '*' at position 3"),
    (b"QUJD.", "invalid character '.' at position 5"),
    (b"QUJD\xc3\xa9", "invalid character byte 0xC3 at position 5"),
    (b"QQ\x00=", "invalid character byte 0x00 at position 3"),
    (b"QU%D", "invalid character '%' at position 3"),
    (b"Q", "invalid length 1"),
    (b"QUJDR", "invalid length 5"),
    (b"QQ=", "incorrect padding (2 data characters followed by 1 '=')"),
    (b"QQ===", "incorrect padding (2 data characters followed by 3 '=')"),
    (b"QUJD=", "incorrect padding (4 data characters followed by 1 '=')"),
    (b"QUJD====", "incorrect padding (4 data characters followed by 4 '=')"),
    (b"QUI==", "incorrect padding (3 data characters followed by 2 '=')"),
    (b"Q===", "incorrect padding (1 data characters followed by 3 '=')"),
    (b"==", "incorrect padding (0 data characters followed by 2 '=')"),
    (b"QQ=A", "unexpected character 'A' after '=' padding at position 4"),
    (b"=QQ", "unexpected character 'Q' after '=' padding at position 2"),
    (b"QQ== QQ==", "unexpected character 'Q' after '=' padding at position 6"),
    (b"QR==", "non-canonical encoding"),
    (b"QR", "non-canonical encoding"),
    (b"QUJ=", "non-canonical encoding"),
    (b"QUJ", "non-canonical encoding"),
])
def test_base64_rejects_invalid_input(lua, text, fragment):
    value, err = unpack(lua.b64.Decode(text))
    assert value is None and err.startswith(b"Base64.Decode: ") and fragment.encode() in err, err


def test_base64_tail_bits_must_be_canonical(lua):
    """Exhaustive over every final 2- and 3-character group: accepted iff its unused low bits
    are zero, and then equal to Python's decoding."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    dec = lua.b64.Decode
    for a in alphabet:
        for i, b in enumerate(alphabet):
            text = ("QUJD" + a + b).encode()
            value, err = unpack(dec(text))
            if i % 16 == 0:
                assert err is None and value == base64.b64decode(text + b"==")
            else:
                assert value is None and b"non-canonical" in err
    for a in "AQgw":
        for b in alphabet:
            for i, c in enumerate(alphabet):
                text = (a + b + c).encode()
                value, err = unpack(dec(text))
                if i % 4 == 0:
                    assert err is None and value == base64.b64decode(text + b"=")
                else:
                    assert value is None and b"non-canonical" in err


def test_base64_edge_cases_and_argument_validation(lua):
    assert lua.b64.Encode(b"") == b"" and lua.b64.Decode(b"") == b""
    assert lua.b64.Decode(b" \r\n\t ") == b""
    assert lua.b64.Decode(b"Q Q = =") == b"A"
    assert unpack(lua.b64.Encode(42)) == (None, b"Base64.Encode: expected a string, got number")
    assert unpack(lua.b64.Encode(None)) == (None, b"Base64.Encode: expected a string, got nil")
    assert unpack(lua.b64.Decode(lua.rt.table())) == (None, b"Base64.Decode: expected a string, got table")
    assert lua.b64[b"STANDARD_ALPHABET"].endswith(b"+/") and lua.b64[b"URLSAFE_ALPHABET"].endswith(b"-_")


# =========================================================== share codes / misc ==

def test_share_code_round_trip_with_python(lua):
    """The intended use: an in-game share code is base64url(JSON) of a soundtrack set."""
    soundtrack = {
        "format": "needledropper-set", "version": 1, "name": "Azeroth Unbound · ★",
        "songs": [
            {"zone": 1429, "title": "I'll Keep Coming", "artist": "Low Roar", "spotify": "4uLU6hMCjMI75M1A2tKUQC",
             "startMs": 0, "durationMs": 245000},
            {"zone": "Mulgore", "title": "Café \"Tauren\" \\ \U0001F3B5", "artist": "中文",
             "youtube": "dQw4w9WgXcQ", "startMs": 15500, "durationMs": 0},
        ],
        "empty": [],
    }
    code = lua.run("local held = ... return B.Encode(J.Encode(held[1]), true)", lua.held(to_lua(soundtrack)))
    assert re.fullmatch(rb"[A-Za-z0-9_-]+", code), "url-safe, unpadded"
    padded = code + b"=" * (-len(code) % 4)
    assert_same(json.loads(base64.urlsafe_b64decode(padded)), soundtrack)
    # And a code made by Python decodes in Lua.
    py_code = base64.urlsafe_b64encode(json.dumps(soundtrack).encode()).rstrip(b"=")
    value, err = unpack(lua.run("local code = ... local text, e = B.Decode(code) if not text then return nil, e end"
                                " return J.Decode(text)", py_code))
    assert err is None
    lua.assert_matches(value, soundtrack)


def test_secret_values_are_never_touched():
    lua = Lua(prelude="issecretvalue = function(v) return v == 'SECRET' end")
    assert lua.encode({"a": [1, "ok"]}) == (b'{"a":[1,"ok"]}', None)
    text, err = lua.encode({"a": [1, "SECRET"]})
    assert text is None and err == b"Json.Encode: cannot encode a secret value at $.a[2]"
    text, err = lua.encode({"SECRET": 1})
    assert text is None and b"secret table key" in err
    assert unpack(lua.json.Decode(b"SECRET")) == (None, b"Json.Decode: refusing to decode a secret value")
    assert unpack(lua.b64.Encode(b"SECRET")) == (None, b"Base64.Encode: refusing to encode a secret value")
    assert unpack(lua.b64.Decode(b"SECRET")) == (None, b"Base64.Decode: refusing to decode a secret value")
    assert lua.b64.Decode(lua.b64.Encode(b"fine")) == b"fine"
    broken = Lua(prelude="issecretvalue = function() error('boom') end")
    assert unpack(broken.json.Decode(b"1"))[0] is None  # an erroring probe counts as secret
    assert unpack(broken.b64.Decode(b"QQ=="))[0] is None


def test_foreign_c_locale_does_not_leak_into_json():
    """A C locale with a decimal comma (changes string.format/tonumber) and a non-byte
    collation (changes Lua's string "<") must not change the JSON text or the key order."""
    saved = {cat: locale.setlocale(cat) for cat in (locale.LC_NUMERIC, locale.LC_COLLATE)}
    try:
        for name in ("de-DE", "de_DE.UTF-8", "de_DE", "German_Germany.1252", "fr-FR", "fr_FR.UTF-8"):
            try:
                locale.setlocale(locale.LC_NUMERIC, name)
                locale.setlocale(locale.LC_COLLATE, name)
                break
            except locale.Error:
                continue
        else:
            pytest.skip("no decimal-comma locale available")
        lua = Lua()
        if lua.rt.eval("string.format('%.1f', 0.5)") != b"0,5":
            pytest.skip("the C locale used by Lua did not change")
        assert lua.encode([1.5, -0.25, 1e300, 1 / 3, 42]) == (b"[1.5,-0.25,1e+300,0.3333333333333333,42]", None)
        value, err = lua.decode(b"[1.5,-2.5e-3,7]")
        assert err is None and [value[1], value[2], value[3]] == [1.5, -0.0025, 7]
        keys = ["b", "B", "a", "_", "Z", "é", "e", "f"]
        text, err = lua.encode({k: 0 for k in keys})
        assert err is None and list(json.loads(text)) == sorted(keys)
        if lua.rt.eval('"a" < "B"'):  # the locale's collation really differs from byte order
            assert sorted(keys) != sorted(keys, key=locale.strxfrm)
    finally:
        for cat, value in saved.items():
            locale.setlocale(cat, value)


# ================================================================ performance ==

def best_of(fn, *args, repeat: int = 5) -> float:
    best = math.inf
    for _ in range(repeat):
        started = time.perf_counter()
        fn(*args)
        best = min(best, time.perf_counter() - started)
    return best


def test_base64_64kb_under_50ms(lua):
    data = random.Random(64).randbytes(65536)
    encoded = base64.b64encode(data)
    mime = base64.encodebytes(data)
    assert lua.b64.Encode(data) == encoded and lua.b64.Decode(mime) == data
    timings = {
        "encode": best_of(lua.b64.Encode, data),
        "encode urlsafe": best_of(lua.b64.Encode, data, True),
        "decode": best_of(lua.b64.Decode, encoded),
        "decode mime-wrapped": best_of(lua.b64.Decode, mime),
    }
    slow = {k: round(v * 1000, 1) for k, v in timings.items() if v >= 0.050}
    assert not slow, f"64 KB budget is 50 ms; took {slow} ms"


def test_json_performance_budget(lua):
    """A large soundtrack library (2000 songs, ~0.5 MB of JSON) must encode and decode well
    within a frame budget a player would notice (~0.25 s each, measured best-of-3)."""
    rng = random.Random(11)
    library = {"format": "needledropper-set", "version": 1, "songs": [
        {"id": "zone:%d" % rng.randint(1, 3000), "title": gen_str(rng, 30) or "t", "artist": "Artist é %d" % i,
         "spotify": "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(22)),
         "youtube": "".join(rng.choice("ABCDEFGHabcdefgh0123456789-_") for _ in range(11)),
         "startMs": rng.randint(0, 60000), "durationMs": rng.randint(60000, 400000), "volume": 0.8,
         "tags": ["calm", "arrival", "night"], "note": "why it fits — " + gen_str(rng, 40)}
        for i in range(2000)]}
    held = lua.held(to_lua(library))
    encode = lua.rt.eval("function(J, held) return J.Encode(held[1]) end")
    text = encode(lua.json, held)
    assert isinstance(text, bytes) and len(text) > 400_000
    decode = lua.json.Decode
    t_enc = best_of(encode, lua.json, held, repeat=3)
    t_dec = best_of(decode, text, repeat=3)
    assert t_enc < 0.25 and t_dec < 0.25, f"encode {t_enc * 1000:.0f} ms, decode {t_dec * 1000:.0f} ms"
    assert_same(py_loads(text), library)
