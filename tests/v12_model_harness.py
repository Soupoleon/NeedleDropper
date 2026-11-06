"""Loaders for the v1.2 addon model (Locale, Sets, Soundtracks, Share, Companion) under lupa.

Two runtimes, because binary data cannot cross a UTF-8 lupa runtime intact:

* :class:`ModelHarness` is the unified ``addon_harness.AddonHarness`` (UTF-8 runtime, every
  shim, zlib through the hex bridge of ``tests/lua/shim_encoding.lua``) with the A1 helpers.
  ``profile="full"`` (default) loads the whole addon in the real TOC order (CONTRACTS-v1.2 2.1);
  ``profile="model"`` loads only the TOC prefix the model needs (Locale ... Companion).

* :class:`BytesRuntime` is a ``LuaRuntime(encoding=None)``: wow_shim + shim_encoding (bytes
  bridge) + the model files, driven directly (no AddonHarness, whose helpers pass ``str``). Used
  for share-code vectors, the mutation corpus and zlib bombs.

Also here: the Python reference share codec of DESIGN-v1.2 section 5.4, exactly as written
there (:func:`ref_encode`, :func:`ref_decode`, :class:`ShareCodeError`), used to write and
check the golden vectors in ``tests/vectors/share/``.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from pathlib import Path
from typing import Any, Iterable, Optional

import lupa.lua51 as lua51

from addon_harness import (ADDON_DIR, ADDON_NAME, SHIM_ENCODING_PATH, SHIM_PATH, UNSET, AddonHarness,  # noqa: F401
                           lua_literal, lua_to_py, make_bridge, toc_files)

ROOT = Path(__file__).resolve().parents[1]
ENCODING_SHIM_PATH = SHIM_ENCODING_PATH
VECTORS_DIR = Path(__file__).resolve().parent / "vectors" / "share"

#: CONTRACTS-v1.2 section 2.1: the final TOC (read from NeedleDropper.toc).
FINAL_TOC = toc_files()
#: The model and what it needs at load time: the TOC up to Companion.lua.
MODEL_FILES = FINAL_TOC[:FINAL_TOC.index("Companion.lua") + 1]
#: My own modules (A1).
A1_FILES = ["Locale.lua", "Sets.lua", "Soundtracks.lua", "Share.lua", "Companion.lua"]

DECOMPRESS_LIMIT = 104857600  # EncodingUtilConstantsDocumentation.lua: EncodingDecompressSizeLimit


def files_for(profile: str) -> list[str]:
    """Addon files (TOC order) for a harness profile: "model" or "full" (the whole TOC)."""
    if profile == "model":
        return list(MODEL_FILES)
    if profile != "full":
        raise ValueError(f"unknown profile {profile!r}")
    return list(FINAL_TOC)


# ------------------------------------------------------------------- converters --

def canon(v: Any) -> Any:
    """Normalise a Lua-converted value for comparisons: an empty table is an empty list."""
    if isinstance(v, dict):
        if not v:
            return []
        return {k: canon(x) for k, x in v.items()}
    if isinstance(v, list):
        return [canon(x) for x in v]
    return v


_lua_to_py = lua_to_py


# ===================================================================== harness ==

class ModelHarness(AddonHarness):
    """The unified AddonHarness for the model tests (see the module docstring).

    Extra keyword arguments:
      profile          "full" (default: the whole TOC) or "model"
      companion_data   value for NeedleDropper_CompanionData (set before the files load), or
                       the string "unset" to leave it absent
      fail_mode        C_EncodingUtil failure style: "error" (raise) or "nil" (return nothing)
      encoding_absent  True: C_EncodingUtil does not exist
      extra_files      more addon files to load after the profile's files
    """

    def __init__(self, *, profile: str = "full", companion_data: Any = "unset", fail_mode: str = "error",
                 encoding_absent: bool = False, extra_files: Optional[Iterable[str]] = None,
                 before_load=None, settle: float = 1.0, **kw) -> None:
        files = files_for(profile) + list(extra_files or [])
        super().__init__(files=files, companion_data=UNSET if companion_data == "unset" else companion_data,
                         fail_mode=fail_mode, encoding_absent=encoding_absent, before_load=before_load,
                         settle=settle, **kw)


# =============================================================== bytes runtime ==

class LuaError(AssertionError):
    pass


class BytesRuntime:
    """encoding=None runtime with the WoW shim, the zlib shim (bytes bridge) and the model.

    Strings passed in may be ``str`` (encoded as UTF-8) or ``bytes``; results come back
    decoded (bytes -> str with surrogateescape, empty tables -> {}).
    """

    def __init__(self, *, files: Optional[list[str]] = None, db: Optional[dict] = None,
                 char_db: Optional[dict] = None, fail_mode: str = "error", encoding_absent: bool = False,
                 login: bool = True, decompress_limit: Optional[int] = None) -> None:
        self.rt = lua51.LuaRuntime(unpack_returned_tuples=True, encoding=None)
        self.g = self.rt.globals()
        self._loader = self.rt.execute(b"return function(src, name) return loadstring(src, name) end")
        self._run(SHIM_PATH.read_bytes(), b"@wow_shim.lua")
        self.shim = self.g[b"SHIM"]
        compress, decompress = make_bridge("bytes")
        self.g[b"SHIM_ENCODING_BRIDGE"] = self.rt.table_from({b"mode": b"bytes", b"compress": compress,
                                                              b"decompress": decompress})
        settings = "failMode = %s, absent = %s" % (lua_literal(fail_mode), "true" if encoding_absent else "false")
        if decompress_limit is not None:
            settings += ", decompressLimit = %d" % decompress_limit
        self.rt.execute(("SHIM.encoding = { %s }" % settings).encode())
        self._run(ENCODING_SHIM_PATH.read_bytes(), b"@shim_encoding.lua")
        self.ns = self.rt.table()
        for f in (files if files is not None else MODEL_FILES):
            src = (ADDON_DIR / f).read_bytes()
            fn = self._compile(src, ("@" + f).encode())
            fn(ADDON_NAME.encode(), self.ns)
        if db is not None:
            self.rt.execute(b"NeedleDropperDB = " + lua_literal(db).encode())
        if char_db is not None:
            self.rt.execute(b"NeedleDropperCharDB = " + lua_literal(char_db).encode())
        self.check()
        if login:
            self.fire("ADDON_LOADED", ADDON_NAME)
            self.fire("PLAYER_LOGIN")

    def _compile(self, src: bytes, name: bytes):
        res = self._loader(src, name)
        if isinstance(res, tuple):
            fn, err = res[0], (res[1] if len(res) > 1 else None)
        else:
            fn, err = res, None
        if fn is None:
            raise SyntaxError(f"{name!r}: {err!r}")
        return fn

    def _run(self, src: bytes, name: bytes):
        return self._compile(src, name)()

    def errors(self) -> list[str]:
        return [e.decode("utf-8", "replace") for e in self.shim[b"log"][b"errors"].values()]

    def check(self) -> None:
        errs = self.errors()
        if errs:
            raise LuaError("Lua errors:\n" + "\n----\n".join(errs))

    def fire(self, event: str, *args) -> None:
        self.shim[b"FireEvent"](event.encode(), *[a.encode() if isinstance(a, str) else a for a in args])
        self.check()

    def advance(self, seconds: float) -> None:
        self.shim[b"Advance"](float(seconds))
        self.check()

    def to_lua(self, v: Any) -> Any:
        if isinstance(v, str):
            return v.encode("utf-8", errors="surrogateescape")
        if isinstance(v, (dict, list, tuple)):
            return self.rt.execute(b"return " + lua_literal(v).encode())
        return v

    def to_py(self, v: Any) -> Any:
        return _lua_to_py(v, decode=True)

    def fn(self, path: str):
        target = self.ns
        for part in path.split("."):
            target = target[part.encode()]
            if target is None:
                raise AttributeError(f"ns.{path} is nil")
        return target

    def call(self, path: str, *args) -> tuple:
        res = self.fn(path)(*[self.to_lua(a) for a in args])
        self.check()
        if not isinstance(res, tuple):
            res = (res,)
        return tuple(self.to_py(r) for r in res)

    def call1(self, path: str, *args) -> Any:
        return self.call(path, *args)[0]

    def call_raw(self, path: str, *args) -> tuple:
        """Like call() but returns raw Lua values (bytes / Lua tables)."""
        res = self.fn(path)(*[self.to_lua(a) for a in args])
        self.check()
        return res if isinstance(res, tuple) else (res,)

    # -- share code shortcuts --

    def decode(self, text) -> tuple:
        """Share.Decode -> (pack, warnings, None) or (None, reason, detail)."""
        res = self.call("Share.Decode", text)
        res = tuple(res) + (None,) * (3 - len(res))
        return res[:3]

    def encode(self, pack: dict) -> tuple:
        """Share.Encode -> (code, None) or (None, message)."""
        res = self.call("Share.Encode", pack)
        res = tuple(res) + (None,) * (2 - len(res))
        return res[:2]


# ============================================== reference codec (DESIGN 5.4) ==

PREFIX = "!NeedleDropper:1!"
CODE_RE = re.compile(r"!NeedleDropper:(\d+)!([A-Za-z0-9+/=_\-\s]*)(!?)")

FOREIGN_ANYWHERE = [("!WA:", "WeakAuras"), ("!E1!", "ElvUI"), ("!E2!", "ElvUI"), ("!PLATER:", "Plater"),
                    ("!~MDT2~", "MDT"), ("D!ProfileV2-", "Details"), ("!CELL:", "Cell")]
FOREIGN_AT_START = [("BW2:", "BigWigs"), ("BWB1:", "BigWigs"), ("1|", "Blizzard")]


class ShareCodeError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def foreign_prefix(text: str) -> Optional[str]:
    """DESIGN 5.1: known prefixes anywhere / at the start of the trimmed text."""
    best = None
    for p, name in FOREIGN_ANYWHERE:
        at = text.find(p)
        if at >= 0 and (best is None or at < best[0]):
            best = (at, name)
    if best:
        return "foreign:" + best[1]
    t = text.lstrip(" \t\r\n\f\v")
    for p, name in FOREIGN_AT_START:
        if t.startswith(p):
            return "foreign:" + name
    return None


def _reject_nan(name):
    raise ValueError(f"{name} is not valid JSON")


def ints_from_integral_floats(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        return [ints_from_integral_floats(x) for x in v]
    if isinstance(v, dict):
        return {k: ints_from_integral_floats(x) for k, x in v.items()}
    return v


def ref_encode(pack: dict) -> str:
    raw = json.dumps(pack, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return PREFIX + base64.b64encode(zlib.compress(raw, 9)).decode("ascii") + "!"


def ref_decode(text: str) -> Any:
    if len(text) > 65536:
        raise ShareCodeError("toobig")
    text = text.replace("​", "").replace("﻿", "").replace(" ", " ")
    m = CODE_RE.search(text)
    if not m:
        raise ShareCodeError(foreign_prefix(text) or "notcode")
    if int(m.group(1)) != 1:
        raise ShareCodeError("newer" if int(m.group(1)) > 1 else "damaged")
    cut = m.group(3) == ""
    body = re.sub(r"[\s=]", "", m.group(2)).replace("-", "+").replace("_", "/")
    try:
        blob = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
        if len(blob) > 49152:
            raise ShareCodeError("toobig")
        if blob[:1] == b"{":
            raw = blob
        else:
            d = zlib.decompressobj()
            raw = d.decompress(blob, 262145)
            if len(raw) > 262144:
                raise ShareCodeError("toobig")
            if not d.eof:
                raise ShareCodeError("cutoff" if cut else "damaged")
        pack = json.loads(raw.decode("utf-8"), parse_constant=_reject_nan)
    except ShareCodeError:
        raise
    except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError) as exc:
        raise ShareCodeError("cutoff" if cut else "damaged") from exc
    return ints_from_integral_floats(pack)


def ref_code_uncompressed(pack: dict) -> str:
    """A code whose payload is the raw JSON (readers accept it; NeedleDropper never writes it)."""
    raw = json.dumps(pack, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return PREFIX + base64.b64encode(raw).decode("ascii") + "!"


# ------------------------------------------------------------- code variants --

def urlsafe_unpadded(code: str) -> str:
    body = code[len(PREFIX):-1]
    return PREFIX + body.replace("+", "-").replace("/", "_").rstrip("=") + "!"


def wrapped(code: str, width: int = 76, newline: str = "\n") -> str:
    body = code[len(PREFIX):-1]
    lines = [body[i:i + width] for i in range(0, len(body), width)]
    return PREFIX + newline.join(lines) + "!"


def discord_fenced(code: str) -> str:
    return "here's my soundtrack, enjoy :)\n```\n" + code + "\n```\nping me if it breaks"


def with_zero_width(code: str, every: int = 50) -> str:
    body = code[len(PREFIX):-1]
    parts = [body[i:i + every] for i in range(0, len(body), every)]
    return "﻿" + PREFIX + "​".join(parts) + "! "
