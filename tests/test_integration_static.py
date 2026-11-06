"""Static checks over the whole shipped addon and companion (SPEC 4 hard constraints).

* every addon ``.lua`` (Protocol.lua and the generated Map.lua included) compiles with Lua 5.1
  ``loadstring`` and uses no Lua 5.2+ syntax, operators, escapes or library functions, checked on
  real tokens (comments and string literals stripped, so ``|cff..|r`` colour codes and ``--``
  comments cannot hide or fake a hit);
* every companion ``.py`` file compiles (in memory: nothing is written next to the sources).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import ADDON_DIR, ROOT, toc_files  # noqa: E402

lua51 = pytest.importorskip("lupa.lua51")

LUA_FILES = sorted(p.name for p in ADDON_DIR.glob("*.lua"))
COMPANION_PY = sorted((ROOT / "companion").rglob("*.py"))

_LONG_OPEN = re.compile(r"\[(=*)\[")


def tokenize_lua(src: str) -> tuple[str, list[str]]:
    """Split Lua source into (code without comments, with every string literal replaced by
    ``""``) and the list of short-string bodies (raw, escapes not interpreted).

    Follows the Lua 5.1 lexer: ``--[[ ]]`` / ``--[==[ ]==]`` long comments, ``--`` line
    comments, ``[[ ]]`` / ``[=[ ]=]`` long strings and ``'..'`` / ``".."`` short strings.
    """
    out: list[str] = []
    strings: list[str] = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith("--", i):
            m = _LONG_OPEN.match(src, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                if j < 0:
                    raise SyntaxError(f"unterminated long comment at offset {i}")
                i = j + len(close)
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j
            continue
        c = src[i]
        if c == "[":
            m = _LONG_OPEN.match(src, i)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                if j < 0:
                    raise SyntaxError(f"unterminated long string at offset {i}")
                out.append('""')
                i = j + len(close)
                continue
        if c in "\"'":
            j = i + 1
            body: list[str] = []
            while j < n and src[j] != c:
                if src[j] == "\\" and j + 1 < n:
                    body.append(src[j:j + 2])
                    j += 2
                    continue
                if src[j] == "\n":
                    raise SyntaxError(f"unterminated string at offset {i}")
                body.append(src[j])
                j += 1
            strings.append("".join(body))
            out.append('""')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), strings


# Lua 5.2+ only: goto/labels, integer division, bitwise operators, newer library functions.
FORBIDDEN_CODE = [
    (re.compile(r"\bgoto\b"), "goto (Lua 5.2)"),
    (re.compile(r"::[A-Za-z_]\w*::"), "label (Lua 5.2)"),
    (re.compile(r"//"), "integer division // (Lua 5.3)"),
    (re.compile(r"&"), "bitwise & (Lua 5.3)"),
    (re.compile(r"\|"), "bitwise | (Lua 5.3)"),
    (re.compile(r"~(?!=)"), "bitwise ~ (Lua 5.3)"),
    (re.compile(r"<<|>>"), "shift operator (Lua 5.3)"),
    (re.compile(r"\b(?:table\.(?:unpack|pack|move)|string\.(?:pack|unpack|packsize)|math\.(?:type|tointeger|ult))\b"),
     "Lua 5.2+/5.3 library function"),
    (re.compile(r"\butf8\s*\."), "utf8 library (Lua 5.3)"),
    (re.compile(r"\bbit32\s*\.|\bbit\s*\."), "bit library (SPEC 4: pure arithmetic only)"),
    (re.compile(r"\brawlen\s*\("), "rawlen (Lua 5.2)"),
    (re.compile(r"\b(?:require|import)\s*[\(\"']"), "require/import (not available to addons)"),
    (re.compile(r"\b0[xX][0-9A-Fa-f]*\.[0-9A-Fa-f]*|\b0[xX][0-9A-Fa-f]+[pP]"), "hex float (Lua 5.2)"),
]
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
LUA51_ESCAPES = set("abfnrtv\\\"'\n") | set("0123456789")


def test_tokenizer_strips_comments_and_strings():
    code, strings = tokenize_lua('local a = "x // y | z" -- a // comment\n'
                                 "--[==[ goto & ]==] local b = [[ ~ << ]] .. 'q\\'s'\n")
    assert "//" not in code and "goto" not in code and "|" not in code and "~" not in code
    assert strings == ["x // y | z", "q\\'s"]
    code, _ = tokenize_lua("if a ~= b then return a end")
    assert "~=" in code


def scan_problems(src: str) -> list[str]:
    code, strings = tokenize_lua(src)
    found = [what for line in code.splitlines() for rx, what in FORBIDDEN_CODE if rx.search(line)]
    found += [f"escape \\{m.group(1)}" for s in strings for m in _ESCAPE.finditer(s)
              if m.group(1) not in LUA51_ESCAPES]
    return found


@pytest.mark.parametrize("bad", [
    "local x = a // b", "goto done", "::done::", "local y = a & b", "local y = a | b", "local y = ~a",
    "local y = a << 2", "local t = table.unpack(t)", "local c = utf8.char(65)", "local z = bit.band(1, 2)",
    "local m = require('x')", 'local s = "\\x41"', 'local s = "a\\z  b"', "local f = 0x1p4",
])
def test_scanner_flags_lua52_constructs(bad):
    assert scan_problems(bad), bad


def test_scanner_accepts_lua51_idioms():
    ok = ('local ADDON, ns = ...\nlocal s = "|cff9fd3ffNeedle|r \\194\\183 \\"q\\" \\n"\n'
          "if a ~= b and #t > 0 then x = math.floor(a / 2) % 256 end -- comment // | &\n"
          "local l = [==[ goto ]==]\n")
    assert scan_problems(ok) == []


def test_scan_covers_every_toc_file():
    assert set(toc_files()) <= set(LUA_FILES)
    assert {"Protocol.lua", "Map.lua", "Core.lua", "Director.lua"} <= set(LUA_FILES)


@pytest.mark.parametrize("fname", LUA_FILES)
def test_addon_file_is_lua51(fname):
    raw = (ADDON_DIR / fname).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM breaks loadstring and the client"
    src = raw.decode("utf-8")
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    res = rt.execute("return function(s, n) local f, e = loadstring(s, n); return {f ~= nil, e} end")(src, "@" + fname)
    assert res[1], f"{fname} does not compile under Lua 5.1: {res[2]}"
    code, strings = tokenize_lua(src)
    problems = []
    for lineno, line in enumerate(code.splitlines(), 1):
        for rx, what in FORBIDDEN_CODE:
            if rx.search(line):
                problems.append(f"{fname}:{lineno}: {what}: {line.strip()}")
    for s in strings:
        for m in _ESCAPE.finditer(s):
            if m.group(1) not in LUA51_ESCAPES:
                problems.append(f"{fname}: string escape \\{m.group(1)} is not Lua 5.1 (\\x/\\z/\\u are 5.2+)")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("fname", [f for f in LUA_FILES if f != "Map.lua"])
def test_addon_file_declares_addon_varargs(fname):
    """SPEC 4: every addon file receives ``(ADDON, ns)``. (Map.lua is generated data and
    starts with the SPEC 3.2 'Generated by' header instead.)"""
    code, _ = tokenize_lua((ADDON_DIR / fname).read_text(encoding="utf-8"))
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first == "local ADDON, ns = ...", fname


@pytest.mark.parametrize("path", COMPANION_PY, ids=lambda p: str(p.relative_to(ROOT)))
def test_companion_module_compiles(path):
    compile(path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True)
