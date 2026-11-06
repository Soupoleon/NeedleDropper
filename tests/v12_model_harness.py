"""Loaders for the v1.2 addon model (Locale, Sets, Soundtracks, Share, Companion) under lupa.

Two runtimes, because binary data cannot cross a UTF-8 lupa runtime intact:

* :class:`ModelHarness` subclasses ``addon_harness.AddonHarness`` (UTF-8 runtime, the full WoW
  shim) and loads the addon files in the final v1.2 TOC order (CONTRACTS-v1.2 section 2.1)
  plus ``tests/lua/shim_encoding.lua``. zlib crosses as hex from Lua to Python; Python always
  answers with ``bytes``, which lupa hands to Lua as raw strings.

  ``profile="model"`` loads only what the model needs (Locale ... Companion); ``profile="full"``
  adds Location, Discovery, Audio, Toast and Director so drops really arm and fire. Files owned
  by builders working concurrently (A3: Beacon, ChatLink, Emit; A2: UI\\*; the integrator's
  Options and SelfTest) are left out on purpose: the Director only reaches the transports
  through ``if ns.Beacon`` / ``if ns.ChatLink``. While Director.lua still uses the v1
  ``ns.Protocol``, ``Protocol.lua`` is loaded first. ``NeedleDropper_Map`` is nil, so Core's
  v1 map is empty until ``Soundtracks.Init`` (on ND_INIT) replaces ``ns.Map``.

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

from addon_harness import ADDON_DIR, ADDON_NAME, SHIM_PATH, AddonHarness, lua_literal

ROOT = Path(__file__).resolve().parents[1]
ENCODING_SHIM_PATH = Path(__file__).resolve().parent / "lua" / "shim_encoding.lua"
VECTORS_DIR = Path(__file__).resolve().parent / "vectors" / "share"

#: CONTRACTS-v1.2 section 2.1, the final TOC written by the integrator.
FINAL_TOC = [
    "Locale.lua", "Json.lua", "Base64.lua", "Protocol2.lua", "Core.lua", "Sets.lua",
    "Sets/AzerothMixtape.lua", "Sets/AzerothUnbound.lua", "Soundtracks.lua", "Share.lua", "Companion.lua",
    "Emit.lua", "Location.lua", "Discovery.lua", "Audio.lua", "Beacon.lua", "ChatLink.lua", "Toast.lua",
    "Director.lua", "Options.lua", "UI/Window.lua", "UI/Drops.lua", "UI/Soundtracks.lua", "UI/Journal.lua",
    "UI/ShareDialogs.lua", "SelfTest.lua",
]
#: The model and what it needs at load time.
MODEL_FILES = FINAL_TOC[:11]
#: Enough of the rest for drops to arm and fire (no transports, UI, options or self-test).
RUNTIME_FILES = ["Location.lua", "Discovery.lua", "Audio.lua", "Toast.lua", "Director.lua"]
#: My own modules (A1).
A1_FILES = ["Locale.lua", "Sets.lua", "Soundtracks.lua", "Share.lua", "Companion.lua"]

DECOMPRESS_LIMIT = 104857600  # EncodingUtilConstantsDocumentation.lua: EncodingDecompressSizeLimit
_LEVELS = {0: 6, 1: 1, 2: 9}  # Default, OptimizeForSpeed, OptimizeForSize
_WBITS = {0: -15, 1: 15, 2: 31}  # Deflate (raw), Zlib, Gzip


def files_for(profile: str) -> list[str]:
    """Addon files (TOC order) for a harness profile: "model" or "full"."""
    if profile == "model":
        return list(MODEL_FILES)
    if profile != "full":
        raise ValueError(f"unknown profile {profile!r}")
    files = list(MODEL_FILES) + list(RUNTIME_FILES)
    director = (ADDON_DIR / "Director.lua").read_text(encoding="utf-8")
    if (ADDON_DIR / "Protocol.lua").is_file() and re.search(r"ns\.Protocol(?![0-9A-Za-z_])", director):
        files.insert(0, "Protocol.lua")
    if "function ns.Emit(" not in director and (ADDON_DIR / "Emit.lua").is_file():
        files.insert(files.index("Companion.lua") + 1, "Emit.lua")  # after the integration (CONTRACTS 2.8)
    return files


# ------------------------------------------------------------------ zlib bridge --

def make_bridge(mode: str):
    """Python halves of the C_EncodingUtil shim. mode "hex": data arrives as hex text."""

    def unwrap(data) -> bytes:
        if mode == "hex":
            if isinstance(data, bytes):
                data = data.decode("ascii")
            return bytes.fromhex(data)
        return bytes(data)

    def compress(data, method, level):
        try:
            raw = unwrap(data)
            lvl = _LEVELS.get(int(level), 6)
            wbits = _WBITS[int(method)]
            if wbits == 15:
                return zlib.compress(raw, lvl)
            c = zlib.compressobj(lvl, zlib.DEFLATED, wbits)
            return c.compress(raw) + c.flush()
        except Exception as exc:  # noqa: BLE001 - reported to Lua like a failing C call
            return None, ("compress failed: %s" % exc).encode()

    def decompress(data, method, limit):
        try:
            raw = unwrap(data)
            d = zlib.decompressobj(_WBITS[int(method)])
            limit = int(limit)
            out = d.decompress(raw, limit + 1)
            if len(out) > limit:
                return None, b"decompressed data exceeds the size limit"
            if not d.eof:
                return None, b"failed to decompress: incomplete stream"
            return out
        except Exception as exc:  # noqa: BLE001
            return None, ("failed to decompress: %s" % exc).encode()

    return compress, decompress


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


def _lua_to_py(v: Any, decode: bool, depth: int = 0) -> Any:
    if depth > 40:
        raise RecursionError("Lua value nested too deeply")
    if isinstance(v, bytes) and decode:
        return v.decode("utf-8", errors="surrogateescape")
    if lua51.lua_type(v) == "table":
        items = list(v.items())
        conv = []
        for k, x in items:
            if isinstance(k, bytes) and decode:
                k = k.decode("utf-8", errors="surrogateescape")
            if isinstance(k, float) and k.is_integer():
                k = int(k)
            conv.append((k, _lua_to_py(x, decode, depth + 1)))
        keys = [k for k, _ in conv]
        if conv and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            d = dict(conv)
            return [d[i] for i in range(1, len(keys) + 1)]
        return {k: x for k, x in conv}
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v


# ===================================================================== harness ==

class ModelHarness(AddonHarness):
    """AddonHarness running the v1.2 model (see the module docstring).

    Extra keyword arguments:
      profile          "full" (default) or "model"
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
        compress, decompress = make_bridge("hex")

        def setup(h: "AddonHarness") -> None:
            h.g.SHIM_ENCODING_BRIDGE = h.rt.table_from({"mode": "hex", "compress": compress,
                                                         "decompress": decompress})
            h.rt.execute("SHIM.encoding = { failMode = %s, absent = %s }"
                         % (lua_literal(fail_mode), "true" if encoding_absent else "false"))
            h._run(ENCODING_SHIM_PATH.read_text(encoding="utf-8"), "@shim_encoding.lua")
            if companion_data != "unset":
                h.rt.execute("NeedleDropper_CompanionData = " + lua_literal(companion_data))
            if before_load:
                before_load(h)

        super().__init__(None, files=files, before_load=setup, settle=settle, **kw)

    # ------------------------------------------------------------- plumbing --

    def to_lua(self, v: Any) -> Any:
        if isinstance(v, (dict, list, tuple)):
            return self.rt.execute("return " + lua_literal(v))
        return v

    def to_py(self, v: Any) -> Any:
        return _lua_to_py(v, decode=False)

    def fn(self, path: str):
        target = self.ns
        for part in path.split("."):
            target = target[part]
            if target is None:
                raise AttributeError(f"ns.{path} is nil")
        return target

    def call(self, path: str, *args) -> tuple:
        """Calls ns.<path>(*args) (dicts / lists become Lua tables); returns every result,
        converted to Python, as a tuple."""
        res = self.fn(path)(*[self.to_lua(a) for a in args])
        self.check_errors()
        if not isinstance(res, tuple):
            res = (res,)
        return tuple(self.to_py(r) for r in res)

    def call1(self, path: str, *args) -> Any:
        return self.call(path, *args)[0]

    def get(self, path: str) -> Any:
        """ns.<path> converted to Python."""
        return self.to_py(self.fn(path))

    def run(self, src: str, *args) -> Any:
        """Executes Lua source with ``args`` as ``...`` (Python values converted) and returns the
        raw result."""
        res = self.rt.execute(src, *[self.to_lua(a) for a in args])
        self.check_errors()
        return res

    # ------------------------------------------------------------- shortcuts --

    def rebuild(self) -> None:
        """Let the debounced rebuild run (0.25 s)."""
        self.advance(0.3)

    def map_ids(self) -> list[str]:
        return [str(d["id"]) for d in self.ns.Map.drops.values()]

    def map_drop(self, drop_id: str) -> Optional[dict]:
        d = self.ns.Map.byId[drop_id]
        return None if d is None else self.to_py(d)

    def active(self) -> list[str]:
        return list(self.call1("Soundtracks.ActiveRefs") or [])

    def user_set(self, set_id: str) -> Optional[dict]:
        db = self.g.NeedleDropperDB
        s = db.soundtracks[set_id]
        return None if s is None else self.to_py(s)

    def fired(self, message: str) -> list[tuple]:
        """Arguments of every ns.Fire(message, ...) since record_fires() was called."""
        log = self.g.SHIM_FIRES
        if log is None:
            return []
        out = []
        for e in log.values():
            if e[1] == message:
                out.append(tuple(self.to_py(e[i]) for i in range(2, int(e.n) + 1)))
        return out

    def record_fires(self) -> None:
        """Starts logging every internal message (ns.Fire) into SHIM_FIRES."""
        self.rt.execute("""
          local ns = ...
          SHIM_FIRES = {}
          local fire = ns.Fire
          ns.Fire = function(msg, ...)
            SHIM_FIRES[#SHIM_FIRES + 1] = { msg, n = select('#', ...) + 1, ... }
            return fire(msg, ...)
          end
        """, self.ns)


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
