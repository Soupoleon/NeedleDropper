"""Safe reader for World of Warcraft SavedVariables files.

The WoW client stores an addon's saved variables as a Lua file that assigns
constants to global names, for example
``_forever_\\WTF\\Account\\<ACCOUNT>\\SavedVariables\\NeedleDropper.lua``::

    NeedleDropperDB = {
    ["schema"] = 1,
    ["settings"] = {
    ["enabled"] = true,
    ["toastScale"] = 1.05,
    },
    }

(the 12.x client writes CRLF line ends and no indentation; older clients
indent with tabs and add ``-- [n]`` after positional entries).

This module reads such files **without executing anything**. It is a parser
for the data subset of Lua 5.1 the client writes, not a Lua interpreter:
anything outside the grammar below -- variable references, function calls,
``local``, ``return``, operators other than the constant forms listed -- is a
:class:`SavedVariablesError` naming the line and column.

Grammar (whitespace and comments are allowed between all tokens)::

    file   := { Name '=' value [';'] }
    value  := 'nil' | 'true' | 'false' | number | string | table
    table  := '{' [ field { (',' | ';') field } [',' | ';'] ] '}'
    field  := '[' value ']' '=' value | Name '=' value | value
    number := decimal or hex numeral, optionally negated (see "Numbers")
    string := "..." | '...' | [[...]] | [==[...]==]

Comments are ``-- to end of line`` and ``--[[ block ]]`` / ``--[==[ block ]==]``;
the client writes ``-- [n]`` after positional entries and
``nil --[[ skipped inline function ]]`` for values it cannot save.

Mapping to Python:

* ``nil`` -> ``None``; ``true``/``false`` -> ``bool``; strings -> ``str``.
* Numbers: an integer numeral -> ``int`` (when exactly representable as a
  Lua 5.1 double, i.e. ``|n| <= 2**53``; larger ones become the ``float`` the
  game sees); a numeral with a fraction or exponent -> ``float``; hex
  ``0x1F`` -> ``int``.
* A table whose keys are exactly ``1..n`` -> ``list``; any other non-empty
  table -> ``dict`` (keys: ``str``, ``int``, ``float``, ``bool``). Integral
  float keys are normalised to ``int`` (``[1.0]`` and ``[1]`` are the same
  key in Lua). An **empty table** -> ``{}`` by default (most tables in a
  SavedVariables file are maps); pass ``empty_table="list"`` to get ``[]``.
  :func:`as_list` / :func:`as_dict` normalise a value either way.
* Table constructor semantics follow Lua 5.1 exactly: ``nil`` values leave
  (or remove) a key, a positional ``nil,`` still takes its index (the client
  writes ``{ a, b, nil, d }`` for sparse arrays, which becomes
  ``{1: a, 2: b, 4: d}``), and positional entries are stored in batches of 50
  after the keyed entries around them, so ``{ [1] = "x", "y" }`` has ``1 = "y"``.
* A top-level ``Name = nil`` (the client writes this for a declared but unset
  variable) maps to ``{"Name": None}``. A name assigned twice keeps the last value.

Strings:

* ``"..."`` and ``'...'`` with all Lua 5.1 escapes: ``\\a \\b \\f \\n \\r \\t \\v
  \\\\ \\" \\'``, ``\\<newline>``, ``\\ddd`` (1-3 decimal digits, at most 255);
  any other escaped character stands for itself (``\\q`` -> ``q``, as in 5.1).
  Raw control characters (tabs, ``\\x01``...) are fine; a raw line break is not.
* Long brackets ``[[...]]`` / ``[==[...]==]``: the first line break is
  dropped and CRLF / CR become LF, as Lua does. A ``[[`` inside a level-0 long
  string or comment is an error (Lua 5.1's "nesting of [[...]] is deprecated").
* The bytes of each finished string (after escapes, so ``\\195\\169`` is
  ``é``) are decoded as UTF-8 with the error handler ``string_errors``
  (default ``"replace"``: invalid bytes become U+FFFD; ``"surrogateescape"``
  keeps them losslessly; ``"strict"`` makes them an error).

Numbers -- besides plain Lua numerals (``12``, ``-3``, ``0.5``, ``.5``,
``5.``, ``1e+20``, ``4.57763671875e-05``, ``0x10``) the reader accepts the
spellings C runtimes and serializers use for infinity and NaN, so a stray
``math.huge`` saved by an addon cannot make a whole file unreadable:
``inf``, ``nan``, ``nan(ind)``, ``1.#INF``, ``1.#IND``, ``1.#QNAN``,
``1.#SNAN`` (each optionally negated), ``math.huge``, divisions of numeric
constants (``1/0``, ``-1/0``, ``0/0``) and parentheses around them. Note
that the game itself would load a bare ``inf`` as ``nil`` (an unset global);
this reader returns the value that was meant. Hex floats (``0x1p4``) are not
accepted (whether Lua 5.1 does depends on the C library).

Leniency beyond Lua: a UTF-8 byte-order mark at the start is skipped.

Limits: input larger than :data:`MAX_BYTES` (8 MB) is rejected before
parsing (:func:`read_file` checks the file size first); tables nested deeper
than :data:`MAX_DEPTH` (64) levels are rejected. Error columns count bytes.

Files: :func:`find_files` lists an addon's account-wide file
``WTF\\Account\\<ACCOUNT>\\SavedVariables\\<Addon>.lua`` and per-character files
``WTF\\Account\\<ACCOUNT>\\<Realm>\\<Character>\\SavedVariables\\<Addon>.lua``
(the client's ``.lua.bak`` backups and other addons' files are ignored);
:func:`load_needledropper` reads NeedleDropper's two databases from all of them.
:func:`backup_savedvariables` copies NeedleDropper's files into a backup folder of
the app's own (rolling, 10 copies per file) and :func:`search_only_songs` lists the
user drops that have no Spotify id (the app can look them up ahead of time).
Nothing here ever writes below the game folder.
"""
from __future__ import annotations

import codecs
import hashlib
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, NamedTuple, Optional, Union

__all__ = [
    "MAX_BYTES",
    "MAX_DEPTH",
    "ADDON_NAME",
    "ACCOUNT_DB",
    "CHARACTER_DB",
    "SCOPE_ACCOUNT",
    "SCOPE_CHARACTER",
    "SavedVariablesError",
    "SavedVariablesFile",
    "parse_savedvariables",
    "read_file",
    "find_files",
    "load_needledropper",
    "as_list",
    "as_dict",
    "backup_savedvariables",
    "search_only_songs",
]

#: Largest input accepted, in bytes (the client's own files for NeedleDropper are a few KB).
MAX_BYTES = 8 * 1024 * 1024
#: Deepest table nesting accepted.
MAX_DEPTH = 64
#: Upper bound for a caller-supplied ``max_depth``: Lua 5.1's own parser stops at
#: 200 nested levels (LUAI_MAXCCALLS), and it keeps the recursion well inside
#: Python's default limit.
_MAX_DEPTH_CEILING = 200

ADDON_NAME = "NeedleDropper"
#: NeedleDropper's account-wide SavedVariables (``## SavedVariables:``).
ACCOUNT_DB = "NeedleDropperDB"
#: NeedleDropper's per-character SavedVariables (``## SavedVariablesPerCharacter:``).
CHARACTER_DB = "NeedleDropperCharDB"

SCOPE_ACCOUNT = "account"
SCOPE_CHARACTER = "character"

Number = Union[int, float]

# Lua 5.1 reserved words: never names, never table keys of the ``key = v`` form.
_KEYWORDS = frozenset(
    b"and break do else elseif end false for function if in local nil not or "
    b"repeat return then true until while".split()
)

# Lua 5.1 stores positional constructor entries in batches of this size (LFIELDS_PER_FLUSH).
_FLUSH = 50
_MAX_EXACT_INT = 2 ** 53

_WS = re.compile(rb"[ \t\n\r\f\v]+")  # C-locale isspace(), as Lua's lexer uses
_LINE_REST = re.compile(rb"[^\r\n]*")
_LONG_OPEN = re.compile(rb"\[(=*)\[")
_NAME = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
# Quoted strings, "unrolled" (normal* (escape normal*)*) so a missing quote
# fails in linear time. An escaped CRLF / LFCR pair is one line break.
_DQ = re.compile(rb'"([^"\\\r\n]*(?:\\(?:\r\n|\n\r|[\s\S])[^"\\\r\n]*)*)"')
_SQ = re.compile(rb"'([^'\\\r\n]*(?:\\(?:\r\n|\n\r|[\s\S])[^'\\\r\n]*)*)'")
_ESCAPE = re.compile(rb"\\(?:([0-9]{1,3})|(\r\n|\n\r|\r|\n)|([\s\S]))")
# What Lua 5.1's read_numeral() consumes, then what it accepts.
_NUMERAL = re.compile(rb"(?:[0-9]|\.[0-9])[0-9.]*(?:[eE][+-]?)?[A-Za-z0-9_]*")
_DECIMAL = re.compile(rb"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_HEX = re.compile(rb"0[xX][0-9A-Fa-f]+")
# MSVC's printf spellings of infinity / NaN (old CRTs): 1.#INF, 1.#IND, 1.#QNAN, 1.#SNAN.
_MSVC_SPECIAL = re.compile(rb"1\.#(INF|IND|QNAN|SNAN)0*(?![A-Za-z0-9_])")
# The UCRT writes the default NaN as "-nan(ind)".
_NAN_SUFFIX = re.compile(rb"\((?:ind|snan|qnan)\)", re.IGNORECASE)
_NEWLINE = re.compile(rb"\r\n|\n\r|\r|\n")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

_BYTE = [bytes((i,)) for i in range(256)]
_SIMPLE_ESCAPES = {b"a": b"\a", b"b": b"\b", b"f": b"\f", b"n": b"\n", b"r": b"\r", b"t": b"\t",
                   b"v": b"\v"}
_BOM = b"\xef\xbb\xbf"

# Byte values used by the parser.
_LBRACE, _RBRACE = 0x7B, 0x7D
_LBRACKET, _RBRACKET = 0x5B, 0x5D
_LPAREN, _RPAREN = 0x28, 0x29
_EQUALS, _COMMA, _SEMICOLON = 0x3D, 0x2C, 0x3B
_DQUOTE, _SQUOTE = 0x22, 0x27
_MINUS, _SLASH, _DOT = 0x2D, 0x2F, 0x2E
_DIGITS = frozenset(range(0x30, 0x3A))


class SavedVariablesError(ValueError):
    """A SavedVariables file cannot be read: malformed, unsupported or too large.

    ``line`` and ``column`` (1-based; the column counts bytes) locate the
    problem when it has a position; ``path`` names the file when known.
    ``str(exc)`` reads like ``"<path>: line 3, column 9: unfinished string near '"abc'"``.
    """

    def __init__(self, message: str, *, line: Optional[int] = None, column: Optional[int] = None,
                 path: Optional[str] = None) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.path = path
        super().__init__(self._render(with_path=True))

    @property
    def detail(self) -> str:
        """The message with its position but without the path."""
        return self._render(with_path=False)

    def _render(self, *, with_path: bool) -> str:
        parts = []
        if with_path and self.path:
            parts.append(str(self.path))
        if self.line is not None:
            if self.column is not None:
                parts.append(f"line {self.line}, column {self.column}")
            else:
                parts.append(f"line {self.line}")
        parts.append(self.message)
        return ": ".join(parts)


class SavedVariablesFile(NamedTuple):
    """One SavedVariables file, as listed by :func:`find_files`."""

    scope: str  # SCOPE_ACCOUNT or SCOPE_CHARACTER
    account: str  # WTF\Account\<account> folder name, e.g. "70515559#1" or "MYACCOUNT"
    realm: Optional[str]  # realm folder name (character files only)
    character: Optional[str]  # character folder name (character files only)
    path: Path


# --------------------------------------------------------------- parsing ----

def _line_col(data: bytes, pos: int) -> tuple[int, int]:
    """1-based line and byte column of ``pos``; CRLF, LFCR, CR and LF each end one line (as in Lua)."""
    line = 1
    line_start = 0
    for m in _NEWLINE.finditer(data, 0, pos):
        line += 1
        line_start = m.end()
    return line, pos - line_start + 1


def _lua_int(value: int) -> Number:
    """An integer as Lua 5.1 holds it: exact up to 2**53, else the nearest double."""
    if -_MAX_EXACT_INT <= value <= _MAX_EXACT_INT:
        return value
    try:
        return float(value)
    except OverflowError:
        return math.inf if value > 0 else -math.inf


def _divide(a: Number, b: Number) -> float:
    """IEEE 754 division of two constants, as Lua computes ``a / b``."""
    x, y = float(a), float(b)
    if y == 0.0:
        if x == 0.0 or math.isnan(x):
            return math.nan
        return math.copysign(math.inf, x) * math.copysign(1.0, y)
    try:
        return x / y
    except OverflowError:  # pragma: no cover - float division saturates to inf on CPython
        return math.copysign(math.inf, x) * math.copysign(1.0, y)


class _Parser:
    """Recursive-descent parser over the raw bytes of one file."""

    __slots__ = ("data", "n", "pos", "max_depth", "errors", "empty_list")

    def __init__(self, data: bytes, *, max_depth: int, string_errors: str, empty_list: bool) -> None:
        self.data = data
        self.n = len(data)
        self.pos = 3 if data.startswith(_BOM) else 0
        self.max_depth = max_depth
        self.errors = string_errors
        self.empty_list = empty_list

    # -- diagnostics ---------------------------------------------------------

    def _near(self, pos: int) -> str:
        if pos >= self.n:
            return "near end of file"
        snippet = _LINE_REST.match(self.data, pos, min(self.n, pos + 24)).group()
        if not snippet:
            snippet = self.data[pos:pos + 1]
        text = _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", snippet.decode("utf-8", "backslashreplace"))
        return f"near '{text}'"

    def _error(self, message: str, pos: int, *, near: bool = False) -> SavedVariablesError:
        pos = max(0, min(pos, self.n))
        line, col = _line_col(self.data, pos)
        if near:
            message = f"{message} {self._near(pos)}"
        return SavedVariablesError(message, line=line, column=col)

    def _line_of(self, pos: int) -> int:
        return _line_col(self.data, pos)[0]

    # -- lexical helpers -----------------------------------------------------

    def _skip(self) -> None:
        """Skip whitespace and comments."""
        data = self.data
        pos = self.pos
        while True:
            m = _WS.match(data, pos)
            if m:
                pos = m.end()
            if data.startswith(b"--", pos):
                long_open = _LONG_OPEN.match(data, pos + 2)
                if long_open:
                    pos = self._long_bracket_end(long_open, comment_start=pos)
                else:
                    pos = _LINE_REST.match(data, pos + 2).end()
                continue
            break
        self.pos = pos

    def _long_bracket_end(self, m: "re.Match[bytes]", *, comment_start: Optional[int] = None) -> int:
        """Validate the long bracket opened by ``m`` and return the position after its close."""
        level = len(m.group(1))
        start = m.end()
        close = b"]" + b"=" * level + b"]"
        end = self.data.find(close, start)
        what = "long comment" if comment_start is not None else "long string"
        opened = comment_start if comment_start is not None else m.start()
        if end < 0:
            raise self._error(f"unfinished {what}", opened)
        if level == 0:
            nested = self.data.find(b"[[", start, end)
            if nested >= 0:
                raise self._error(f"nesting of [[...]] is deprecated (in the {what} that starts on "
                                  f"line {self._line_of(opened)})", nested)
        return end + len(close)

    def _expect(self, byte: int, what: str) -> None:
        self._skip()
        if self.pos < self.n and self.data[self.pos] == byte:
            self.pos += 1
            return
        raise self._error(f"{what} expected", self.pos, near=True)

    def _assign_after(self, end: int) -> Optional[int]:
        """If a single '=' follows position ``end`` (after whitespace/comments), the position after it."""
        saved = self.pos
        self.pos = end
        self._skip()
        p = self.pos
        self.pos = saved
        data = self.data
        if p < self.n and data[p] == _EQUALS and not (p + 1 < self.n and data[p + 1] == _EQUALS):
            return p + 1
        return None

    # -- grammar -------------------------------------------------------------

    def parse_file(self) -> dict[str, Any]:
        data = self.data
        result: dict[str, Any] = {}
        while True:
            self._skip()
            pos = self.pos
            if pos >= self.n:
                return result
            if data[pos] == _SEMICOLON:
                self.pos = pos + 1
                continue
            m = _NAME.match(data, pos)
            if not m:
                raise self._error("unexpected symbol", pos, near=True)
            name = m.group()
            if name in _KEYWORDS:
                raise self._error(f"unsupported statement '{name.decode('ascii')}' (a SavedVariables file "
                                  "only assigns constants to global names)", pos)
            after = self._assign_after(m.end())
            if after is None:
                self.pos = m.end()
                self._skip()
                raise self._error(f"'=' expected after the global name '{name.decode('ascii')}'", self.pos,
                                  near=True)
            self.pos = after
            result[name.decode("ascii")] = self._value(0)

    def _value(self, depth: int) -> Any:
        self._skip()
        data = self.data
        pos = self.pos
        if pos >= self.n:
            raise self._error("unexpected end of file (a value is missing)", pos)
        c = data[pos]
        if c == _LBRACE:
            return self._table(depth + 1)
        if c == _DQUOTE or c == _SQUOTE:
            return self._quoted()
        if c == _LBRACKET:
            m = _LONG_OPEN.match(data, pos)
            if m:
                return self._long_string(m)
            raise self._error("unexpected symbol", pos, near=True)
        if c in _DIGITS or c == _MINUS or c == _LPAREN or (
                c == _DOT and pos + 1 < self.n and data[pos + 1] in _DIGITS):
            return self._numeric(depth)
        m = _NAME.match(data, pos)
        if m:
            word = m.group()
            if word == b"true":
                self.pos = m.end()
                return True
            if word == b"false":
                self.pos = m.end()
                return False
            if word == b"nil":
                self.pos = m.end()
                return None
            if word in (b"inf", b"nan", b"math"):
                return self._numeric(depth)
            raise self._error(f"unsupported value '{word.decode('ascii')}' (a SavedVariables file holds "
                              "constants only, never variables or code)", pos)
        raise self._error("unexpected symbol", pos, near=True)

    def _table(self, depth: int) -> Any:
        open_pos = self.pos
        if depth > self.max_depth:
            raise self._error(f"tables are nested more than {self.max_depth} levels deep", open_pos)
        data = self.data
        self.pos += 1
        table: dict[Any, Any] = {}
        kinds: dict[int, bool] = {}  # slot 0/1 -> whether its current key is a boolean
        pending: list[tuple[int, Any]] = []
        count = 0
        while True:
            self._skip()
            pos = self.pos
            if pos >= self.n:
                raise self._error(f"unfinished table: '}}' expected (to close '{{' at line "
                                  f"{self._line_of(open_pos)})", pos, near=True)
            c = data[pos]
            if c == _RBRACE:
                self.pos = pos + 1
                break
            if len(pending) == _FLUSH:
                self._flush(table, kinds, pending, pos)
                pending = []
            if c == _LBRACKET and not _LONG_OPEN.match(data, pos):
                self.pos = pos + 1
                self._skip()
                key_pos = self.pos
                key = self._key(self._value(depth), key_pos)
                self._expect(_RBRACKET, "']'")
                self._expect(_EQUALS, "'='")
                self._store(table, kinds, key, self._value(depth), key_pos)
            else:
                m = _NAME.match(data, pos)
                after = None
                if m is not None and m.group() not in _KEYWORDS:
                    after = self._assign_after(m.end())
                if after is not None:
                    self.pos = after
                    self._store(table, kinds, m.group().decode("ascii"), self._value(depth), pos)
                else:
                    count += 1
                    pending.append((count, self._value(depth)))
            self._skip()
            pos = self.pos
            if pos >= self.n:
                raise self._error(f"unfinished table: '}}' expected (to close '{{' at line "
                                  f"{self._line_of(open_pos)})", pos, near=True)
            c = data[pos]
            if c == _COMMA or c == _SEMICOLON:
                self.pos = pos + 1
                continue
            if c == _RBRACE:
                self.pos = pos + 1
                break
            raise self._error(f"'}}' expected (to close '{{' at line {self._line_of(open_pos)})", pos,
                              near=True)
        if pending:
            self._flush(table, kinds, pending, self.pos)
        return self._finish(table)

    def _flush(self, table: dict, kinds: dict, pending: list, pos: int) -> None:
        for index, value in pending:
            self._store(table, kinds, index, value, pos)

    def _key(self, key: Any, pos: int) -> Any:
        if key is None:
            raise self._error("table index is nil", pos)
        if type(key) is float:
            if key != key:
                raise self._error("table index is NaN", pos)
            if key.is_integer():
                return int(key)
        elif isinstance(key, (dict, list)):
            raise self._error("a table used as a table key is not supported", pos)
        return key

    def _store(self, table: dict, kinds: dict, key: Any, value: Any, pos: int) -> None:
        # Python merges True/1 and False/0 into one dict key; Lua keeps them apart.
        # A table that really holds both cannot be represented, so it is an error
        # rather than silently losing one of the two entries.
        if key is True or key is False or (type(key) is int and (key == 0 or key == 1)):
            is_bool = type(key) is bool
            slot = int(key)
            present = slot in table
            if present and kinds[slot] != is_bool:
                if value is None:
                    return  # removing a key that is not there
                raise self._error("this table has both a boolean key and the number key "
                                  f"[{slot}]; Python dicts cannot keep both", pos)
            if value is None:
                if present:
                    del table[slot]
                return
            kinds[slot] = is_bool
            table[key] = value
            return
        if value is None:
            table.pop(key, None)
        else:
            table[key] = value

    def _finish(self, table: dict) -> Any:
        if not table:
            return [] if self.empty_list else {}
        n = len(table)
        if 1 in table and n in table and all(type(k) is int for k in table) \
                and min(table) == 1 and max(table) == n:
            return [table[i] for i in range(1, n + 1)]
        return table

    # -- strings -------------------------------------------------------------

    def _quoted(self) -> str:
        pos = self.pos
        m = (_DQ if self.data[pos] == _DQUOTE else _SQ).match(self.data, pos)
        if m is None:
            raise self._error("unfinished string", pos, near=True)
        self.pos = m.end()
        body = m.group(1)
        if b"\\" in body:
            body = self._unescape(body, m.start(1))
        return self._decode(body, pos)

    def _unescape(self, body: bytes, body_start: int) -> bytes:
        parts: list[bytes] = []
        last = 0
        for m in _ESCAPE.finditer(body):
            parts.append(body[last:m.start()])
            digits, newline, char = m.groups()
            if digits is not None:
                code = int(digits)
                if code > 255:
                    raise self._error(f"escape sequence too large ('\\{digits.decode('ascii')}' is more "
                                      "than 255)", body_start + m.start())
                parts.append(_BYTE[code])
            elif newline is not None:
                parts.append(b"\n")
            else:
                parts.append(_SIMPLE_ESCAPES.get(char, char))
            last = m.end()
        parts.append(body[last:])
        return b"".join(parts)

    def _long_string(self, m: "re.Match[bytes]") -> str:
        start = m.end()
        end = self._long_bracket_end(m)
        body = self.data[start:end - len(m.group(1)) - 2]
        self.pos = end
        first = _NEWLINE.match(body)
        if first:
            body = body[first.end():]
        if b"\r" in body:
            body = _NEWLINE.sub(b"\n", body)
        return self._decode(body, m.start())

    def _decode(self, raw: bytes, pos: int) -> str:
        try:
            return raw.decode("utf-8", self.errors)
        except UnicodeDecodeError as exc:
            raise self._error(f"string is not valid UTF-8 ({exc.reason} at byte {exc.start} of the string)",
                              pos) from None

    # -- numbers -------------------------------------------------------------

    def _numeric(self, depth: int) -> Number:
        """A numeric constant: unary minus, '/' between constants, parentheses."""
        value = self._unary(depth)
        while True:
            self._skip()
            if self.pos < self.n and self.data[self.pos] == _SLASH:
                self.pos += 1
                value = _divide(value, self._unary(depth))
            else:
                return value

    def _unary(self, depth: int) -> Number:
        negate = False
        while True:
            self._skip()
            if self.pos < self.n and self.data[self.pos] == _MINUS:
                negate = not negate
                self.pos += 1
            else:
                break
        value = self._primary(depth)
        return -value if negate else value

    def _primary(self, depth: int) -> Number:
        data = self.data
        pos = self.pos
        if pos >= self.n:
            raise self._error("unexpected end of file (a number is missing)", pos)
        c = data[pos]
        if c in _DIGITS or (c == _DOT and pos + 1 < self.n and data[pos + 1] in _DIGITS):
            return self._number()
        if c == _LPAREN:
            if depth >= self.max_depth:
                raise self._error(f"expression is nested more than {self.max_depth} levels deep", pos)
            self.pos = pos + 1
            value = self._numeric(depth + 1)
            self._skip()
            if self.pos < self.n and data[self.pos] == _RPAREN:
                self.pos += 1
                return value
            raise self._error(f"')' expected (to close '(' at line {self._line_of(pos)})", self.pos,
                              near=True)
        m = _NAME.match(data, pos)
        if m:
            word = m.group()
            if word == b"inf":
                self.pos = m.end()
                return math.inf
            if word == b"nan":
                suffix = _NAN_SUFFIX.match(data, m.end())
                self.pos = suffix.end() if suffix else m.end()
                return math.nan
            if word == b"math":
                self.pos = m.end()
                self._skip()
                if self.pos < self.n and data[self.pos] == _DOT:
                    self.pos += 1
                    self._skip()
                    huge = _NAME.match(data, self.pos)
                    if huge and huge.group() == b"huge":
                        self.pos = huge.end()
                        return math.inf
                raise self._error("unsupported value 'math' (only math.huge is understood)", pos)
        raise self._error("number expected", pos, near=True)

    def _number(self) -> Number:
        data = self.data
        pos = self.pos
        special = _MSVC_SPECIAL.match(data, pos)
        if special:
            self.pos = special.end()
            return math.inf if special.group(1) == b"INF" else math.nan
        word = _NUMERAL.match(data, pos).group()
        self.pos = pos + len(word)
        if _HEX.fullmatch(word):
            return _lua_int(int(word, 16))
        if _DECIMAL.fullmatch(word):
            if word.isdigit():
                if len(word) <= 15:
                    return int(word)
                value = float(word)  # Lua reads every numeral as a double
                return int(value) if value.is_integer() and value <= _MAX_EXACT_INT else value
            return float(word)
        shown = word[:32].decode("ascii", "backslashreplace") + ("..." if len(word) > 32 else "")
        raise self._error(f"malformed number near '{shown}'", pos)


def _to_bytes(text: Union[str, bytes, bytearray, memoryview]) -> bytes:
    if isinstance(text, str):
        try:
            return text.encode("utf-8", "surrogateescape")
        except UnicodeEncodeError:
            return text.encode("utf-8", "surrogatepass")
    if isinstance(text, (bytes, bytearray, memoryview)):
        return bytes(text)
    raise TypeError(f"SavedVariables text must be str or bytes, not {type(text).__name__}")


def _check_options(max_bytes: int, max_depth: int, string_errors: str, empty_table: str) -> None:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise ValueError(f"max_bytes must be a non-negative int, not {max_bytes!r}")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= _MAX_DEPTH_CEILING:
        raise ValueError(f"max_depth must be an int from 1 to {_MAX_DEPTH_CEILING}, not {max_depth!r}")
    if empty_table not in ("dict", "list"):
        raise ValueError(f"empty_table must be 'dict' or 'list', not {empty_table!r}")
    try:
        codecs.lookup_error(string_errors)
    except (LookupError, TypeError):
        raise ValueError(f"unknown string_errors handler {string_errors!r}") from None


def parse_savedvariables(text: Union[str, bytes], *, max_bytes: int = MAX_BYTES, max_depth: int = MAX_DEPTH,
                         string_errors: str = "replace", empty_table: str = "dict") -> dict[str, Any]:
    """Parse the text of a SavedVariables file into ``{global name: value}``.

    ``text`` may be ``bytes`` (preferred: exactly what is on disk) or ``str``
    (encoded to UTF-8 first). Nothing is executed. Raises
    :class:`SavedVariablesError` for input larger than ``max_bytes``, tables
    nested deeper than ``max_depth`` and anything that is not a SavedVariables
    file -- and no other exception, whatever the content (``TypeError`` /
    ``ValueError`` only for a non-text argument or invalid options).

    ``string_errors`` is the UTF-8 decoding error handler for string values
    (``"replace"``, ``"surrogateescape"``, ``"strict"``, ...); ``empty_table``
    chooses what ``{}`` becomes (``"dict"`` or ``"list"``).
    """
    _check_options(max_bytes, max_depth, string_errors, empty_table)
    data = _to_bytes(text)
    if len(data) > max_bytes:
        raise SavedVariablesError(f"file is too large ({len(data):,} bytes; the limit is {max_bytes:,})")
    parser = _Parser(data, max_depth=max_depth, string_errors=string_errors, empty_list=empty_table == "list")
    return parser.parse_file()


def read_file(path: Union[str, os.PathLike], *, max_bytes: int = MAX_BYTES, max_depth: int = MAX_DEPTH,
              string_errors: str = "replace", empty_table: str = "dict") -> dict[str, Any]:
    """Read and parse one SavedVariables file (see :func:`parse_savedvariables`).

    The size limit is checked with ``stat()`` before anything is read.
    Raises :class:`SavedVariablesError` (with ``path`` set) for content
    problems and ``OSError`` when the file cannot be read.
    """
    _check_options(max_bytes, max_depth, string_errors, empty_table)
    p = Path(path)
    size = p.stat().st_size
    if size > max_bytes:
        raise SavedVariablesError(f"file is too large ({size:,} bytes; the limit is {max_bytes:,})", path=str(p))
    with open(p, "rb") as fh:
        data = fh.read(max_bytes + 1)
    try:
        return parse_savedvariables(data, max_bytes=max_bytes, max_depth=max_depth, string_errors=string_errors,
                                    empty_table=empty_table)
    except SavedVariablesError as exc:
        raise SavedVariablesError(exc.message, line=exc.line, column=exc.column, path=str(p)) from None


# ---------------------------------------------------------------- values ----

def as_list(value: Any) -> list:
    """A Lua array as a list, whatever shape the parser gave it.

    A list is returned as is; a dict (an empty table, or an array with holes)
    gives the values of its positive integer keys in key order; ``None`` and
    anything else give ``[]``.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value[k] for k in sorted(k for k in value if type(k) is int and k >= 1)]
    return []


def as_dict(value: Any) -> dict:
    """A Lua table as a dict: a list becomes ``{1: first, 2: second, ...}``;
    ``None`` and non-tables give ``{}``."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {i: v for i, v in enumerate(value, 1)}
    return {}


# ----------------------------------------------------------------- files ----

def _check_addon_name(addon: str) -> None:
    if not isinstance(addon, str) or not addon or addon in (".", "..") or \
            any(ch in addon for ch in '/\\:\0') or addon != addon.strip():
        raise ValueError(f"not an addon name: {addon!r}")


def _sort_key(name: str) -> tuple[str, str]:
    return (name.casefold(), name)


def _subdirs(path: Path, errors: Optional[list]) -> list[tuple[str, Path]]:
    """Sorted ``(name, path)`` of the folders in ``path``; a missing folder has none."""
    try:
        with os.scandir(path) as it:
            found = []
            for entry in it:
                try:
                    if entry.is_dir():
                        found.append((entry.name, Path(entry.path)))
                except OSError:
                    continue
    except FileNotFoundError:
        return []
    except NotADirectoryError:
        return []
    except OSError as exc:
        if errors is not None:
            errors.append(_record(None, f"cannot list the folder: {exc.strerror or exc}", path=path))
        return []
    found.sort(key=lambda item: _sort_key(item[0]))
    return found


def _named_entry(folder: Path, name_casefold: str, *, want_dir: bool, errors: Optional[list]) -> Optional[Path]:
    """The entry of ``folder`` named ``name_casefold`` (case-insensitively), if it is a dir/file."""
    try:
        with os.scandir(folder) as it:
            matches = sorted(entry.name for entry in it if entry.name.casefold() == name_casefold)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        if errors is not None:
            errors.append(_record(None, f"cannot list the folder: {exc.strerror or exc}", path=folder))
        return None
    for name in matches:
        candidate = folder / name
        try:
            if (candidate.is_dir() if want_dir else candidate.is_file()):
                return candidate
        except OSError:
            continue
    return None


def _scan(flavor_root: Path, addon: str, errors: Optional[list]) -> list[SavedVariablesFile]:
    _check_addon_name(addon)
    target = (addon + ".lua").casefold()
    accounts_dir = flavor_root / "WTF" / "Account"
    found: list[SavedVariablesFile] = []
    for account, account_path in _subdirs(accounts_dir, errors):
        if account.casefold() == "savedvariables":
            continue  # WTF\Account\SavedVariables: the login screen's files, no account
        for name, path in _subdirs(account_path, errors):
            if name.casefold() == "savedvariables":
                f = _named_entry(path, target, want_dir=False, errors=errors)
                if f is not None:
                    found.append(SavedVariablesFile(SCOPE_ACCOUNT, account, None, None, f))
                continue
            for character, character_path in _subdirs(path, errors):
                sv = _named_entry(character_path, "savedvariables", want_dir=True, errors=errors)
                if sv is None:
                    continue
                f = _named_entry(sv, target, want_dir=False, errors=errors)
                if f is not None:
                    found.append(SavedVariablesFile(SCOPE_CHARACTER, account, name, character, f))
    found.sort(key=lambda f: (_sort_key(f.account), f.scope != SCOPE_ACCOUNT, _sort_key(f.realm or ""),
                              _sort_key(f.character or "")))
    return found


def find_files(flavor_root: Union[str, os.PathLike], addon: str = ADDON_NAME) -> list[SavedVariablesFile]:
    """All SavedVariables files of ``addon`` below ``<flavor_root>\\WTF\\Account``.

    ``flavor_root`` is the game flavor folder (``...\\World of Warcraft\\_forever_``).
    Returns :class:`SavedVariablesFile` tuples ``(scope, account, realm,
    character, path)`` -- the account-wide file
    ``<ACCOUNT>\\SavedVariables\\<addon>.lua`` (scope ``"account"``, realm and
    character ``None``) and every per-character file
    ``<ACCOUNT>\\<Realm>\\<Character>\\SavedVariables\\<addon>.lua`` (scope
    ``"character"``) -- sorted by account (its account file first), realm and
    character. Names are the client's folder names. File and folder names
    match case-insensitively; ``<addon>.lua.bak`` backups, other addons'
    files (including ``<addon>_Something.lua``) and the account-less
    ``WTF\\Account\\SavedVariables`` folder are ignored. A missing ``WTF``
    folder gives ``[]``; unreadable folders are skipped.
    """
    return _scan(Path(flavor_root), addon, None)


def _record(f: Optional[SavedVariablesFile], message: str, *, path: Optional[Path] = None,
            line: Optional[int] = None, column: Optional[int] = None) -> dict[str, Any]:
    return {
        "path": str(f.path if f is not None else path),
        "scope": f.scope if f is not None else None,
        "account": f.account if f is not None else None,
        "realm": f.realm if f is not None else None,
        "character": f.character if f is not None else None,
        "message": message,
        "line": line,
        "column": column,
    }


def _lua_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list (a table with only positional entries)"
    return type(value).__name__


def load_needledropper(flavor_root: Union[str, os.PathLike]) -> dict[str, Any]:
    """Read NeedleDropper's SavedVariables for every account and character.

    Returns::

        {"accounts": {"<ACCOUNT>": {"db": <NeedleDropperDB dict> or None,
                                    "characters": {"<Realm>/<Character>": <NeedleDropperCharDB dict>}}},
         "errors": [{"path", "scope", "account", "realm", "character", "message", "line", "column"}, ...]}

    An account appears when it has any NeedleDropper file; ``db`` is ``None``
    when it has no account file, the file sets ``NeedleDropperDB = nil``, or
    the file cannot be read. A character appears only when its file holds a
    ``NeedleDropperCharDB`` table. Files that cannot be read (malformed, too
    large, locked while the game writes them, or holding something other than
    a table of named fields) are listed in ``errors`` and never raise.
    """
    errors: list[dict[str, Any]] = []
    accounts: dict[str, dict[str, Any]] = {}
    for f in _scan(Path(flavor_root), ADDON_NAME, errors):
        entry = accounts.setdefault(f.account, {"db": None, "characters": {}})
        var = ACCOUNT_DB if f.scope == SCOPE_ACCOUNT else CHARACTER_DB
        try:
            values = read_file(f.path)
        except SavedVariablesError as exc:
            errors.append(_record(f, exc.detail, line=exc.line, column=exc.column))
            continue
        except OSError as exc:
            errors.append(_record(f, f"cannot read the file: {exc.strerror or exc}"))
            continue
        value = values.get(var)
        if value is not None and not isinstance(value, dict):
            errors.append(_record(f, f"{var} is {_lua_type_name(value)}, expected a table of named fields"))
            value = None
        if f.scope == SCOPE_ACCOUNT:
            entry["db"] = value
        elif value is not None:
            entry["characters"][f"{f.realm}/{f.character}"] = value
    return {"accounts": accounts, "errors": errors}


# --------------------------------------------------------------- backups ----

_BACKUP_SUFFIX = ".lua"
_UNSAFE_PART = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_part(name: str) -> str:
    """A backup folder name for an account / realm / character: the client's own folder name, with
    any character Windows does not allow in a name replaced (and never "." or "..")."""
    s = _UNSAFE_PART.sub("_", name).strip(" .") or "_"
    return s[:120]


def _inside(child: Path, parent: Path) -> bool:
    a = os.path.normcase(os.path.realpath(parent))
    b = os.path.normcase(os.path.realpath(child))
    try:
        return os.path.commonpath([a, b]) == a
    except ValueError:  # different drives
        return False


def backup_savedvariables(flavor_root: Union[str, os.PathLike], backup_dir: Union[str, os.PathLike], *,
                          keep: int = 10, addon: str = ADDON_NAME, now: Optional[float] = None) -> list[Path]:
    """Copy ``addon``'s SavedVariables files into ``backup_dir`` when they changed; rolling copies.

    Every file :func:`find_files` lists is copied to
    ``<backup_dir>\\<ACCOUNT>[\\<Realm>\\<Character>]\\<Addon>-<YYYYmmdd-HHMMSS>.lua`` unless the
    newest backup there already holds the same bytes; then only the newest ``keep`` copies of
    that file are kept. The game folder is only read (each file is read once, so a copy is
    never a mix of two saves). ``backup_dir`` must not be inside ``flavor_root``. Returns the
    new backups; unreadable files are skipped.
    """
    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
        raise ValueError(f"keep must be a positive int, not {keep!r}")
    root = Path(flavor_root)
    dest_root = Path(backup_dir)
    if _inside(dest_root, root):
        raise ValueError(f"the backup folder {dest_root} must not be inside the game folder {root}")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    made: list[Path] = []
    for f in find_files(root, addon):
        parts = [_safe_part(f.account)]
        if f.scope == SCOPE_CHARACTER:
            parts += [_safe_part(f.realm or ""), _safe_part(f.character or "")]
        folder = dest_root.joinpath(*parts)
        try:
            with open(f.path, "rb") as fh:
                data = fh.read(MAX_BYTES + 1)
        except OSError:
            continue
        if len(data) > MAX_BYTES:
            continue
        prefix = f"{addon}-"
        existing = sorted(folder.glob(f"{prefix}*{_BACKUP_SUFFIX}")) if folder.is_dir() else []
        if existing:
            try:
                same = hashlib.sha256(existing[-1].read_bytes()).digest() == hashlib.sha256(data).digest()
            except OSError:
                same = False
            if same:
                continue
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{prefix}{stamp}{_BACKUP_SUFFIX}"
        n = 1
        while target.exists():
            n += 1
            target = folder / f"{prefix}{stamp}-{n}{_BACKUP_SUFFIX}"
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "wb") as out:
            out.write(data)
        os.replace(tmp, target)
        made.append(target)
        for old in sorted(folder.glob(f"{prefix}*{_BACKUP_SUFFIX}"))[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    return made


def search_only_songs(flavor_root: Union[str, os.PathLike]) -> list[tuple[str, str]]:
    """``(artist, title)`` of every user drop without a Spotify id, from every account's
    ``NeedleDropperDB.soundtracks`` (turned-off drops and drafts excluded; each song once, in a
    stable order). The app can search Spotify for them ahead of time (DESIGN-v1.2 2.1)."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    state = load_needledropper(flavor_root)
    for _name, acct in sorted(state["accounts"].items()):
        db = acct.get("db")
        if not isinstance(db, dict):
            continue
        for _sid, st in sorted(as_dict(db.get("soundtracks")).items(), key=lambda kv: str(kv[0])):
            if not isinstance(st, dict):
                continue
            for drop in as_list(st.get("drops")):
                if not isinstance(drop, dict) or drop.get("disabled") is True:
                    continue
                artist, title, spotify = drop.get("artist"), drop.get("title"), drop.get("spotify")
                if not (isinstance(artist, str) and artist.strip() and isinstance(title, str) and title.strip()):
                    continue
                if isinstance(spotify, str) and spotify.strip():
                    continue
                key = (artist.strip(), title.strip())
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    return out
