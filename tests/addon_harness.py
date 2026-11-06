"""Test harness that runs the NeedleDropper WoW addon (v1.2) inside lupa's Lua 5.1.

The game cannot run here, so a stack of Lua shims fakes the WoW API surface the addon uses and
this module drives it. ONE harness for every addon test:

* ``tests/lua/wow_shim.lua``      the base client: frames, events, timers, maps, audio, CVars,
                                   the Settings API, chat ...
* ``tests/lua/shim_beacon2.lua``  EventRegistry, the Chat addon restriction, PlayerFrame /
                                   TargetFrame art for the beacon's clearance guard
* ``tests/lua/shim_ui.lua``       the recording widget kit the window is built from (template
                                   allowlist, ScrollBox, Blizzard_Menu, StaticPopup, GameTooltip,
                                   UISpecialFrames, input helpers); ``ui=False`` leaves it out
* ``tests/lua/shim_encoding.lua`` C_EncodingUtil backed by Python's zlib (hex bridge: a binary
                                   Lua string cannot cross into this UTF-8 runtime intact)

then the addon files in the order of ``NeedleDropper.toc`` (CONTRACTS-v1.2 2.1), SavedVariables
after the files but before ``ADDON_LOADED`` (like the client), and a login. ``sets=`` replaces
the bundled ``Sets\\*.lua`` files by test soundtracks registered with ``ns.RegisterBuiltinSet``
(built-in semantics, exploration history applies); :func:`user_db` builds SavedVariables with a
soundtrack of the player's own. Helpers move the player around, advance the fake clock and read
what the addon emitted (events, the beacon strip, chat lines, music, the title card ...).

Usage::

    from addon_harness import AddonHarness, make_set
    h = AddonHarness(sets=[make_set([{"id": "zone:1429", "kind": "zone", "mapID": 1429}])])
    h.set_location(1429, "Northshire Valley")
    h.advance(12)
    assert h.last_event()["type"] == EventType.FIRE
"""
from __future__ import annotations

import math
import random
import re
import sys
import zlib
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "addon" / "NeedleDropper"
LUA_DIR = Path(__file__).resolve().parent / "lua"
SHIM_PATH = LUA_DIR / "wow_shim.lua"
SHIM_BEACON2_PATH = LUA_DIR / "shim_beacon2.lua"
SHIM_UI_PATH = LUA_DIR / "shim_ui.lua"
SHIM_ENCODING_PATH = LUA_DIR / "shim_encoding.lua"
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType, Flags  # noqa: E402

import lupa.lua51 as lua51  # noqa: E402

ADDON_NAME = "NeedleDropper"
SILENCE = "Interface\\AddOns\\NeedleDropper\\media\\silence.mp3"
MEDIA_PATH = "Interface\\AddOns\\NeedleDropper_Local\\media\\"
_COLOR_RE = re.compile(r"\|c[0-9a-fA-F]{8}|\|r")
UNSET = object()

#: Model files a test may swap for stand-ins (tests/v12_ui_harness.py with real=False).
MODEL_FILES = ["Sets.lua", "Soundtracks.lua", "Share.lua", "Companion.lua"]


# ------------------------------------------------------------------ helpers --

def toc_lines() -> list[str]:
    """Non-comment lines of NeedleDropper.toc, exactly as written (backslashes kept)."""
    out = []
    for line in (ADDON_DIR / "NeedleDropper.toc").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def toc_files() -> list[str]:
    """Lua files listed in NeedleDropper.toc, in load order, with ``/`` separators."""
    return [line.replace("\\", "/") for line in toc_lines()]


def toc_metadata() -> dict[str, str]:
    """``## Key: value`` lines of the TOC."""
    meta = {}
    for line in (ADDON_DIR / "NeedleDropper.toc").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s*([^:]+):\s*(.*)$", line.strip())
        if m:
            meta[m.group(1).strip()] = m.group(2).strip()
    return meta


def is_set_file(fname: str) -> bool:
    return fname.startswith("Sets/")


def lua_literal(v: Any) -> str:
    """Serialize a Python value into a Lua 5.1 table/scalar literal."""
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "(0/0)"
        if math.isinf(v):
            return "math.huge" if v > 0 else "(-math.huge)"
        return repr(v)
    if isinstance(v, str):
        out = []
        for b in v.encode("utf-8"):
            ch = chr(b)
            if ch == "\\":
                out.append("\\\\")
            elif ch == '"':
                out.append('\\"')
            elif 32 <= b < 127:
                out.append(ch)
            else:
                out.append("\\%03d" % b)  # three digits: a digit after it cannot extend the escape
        return '"' + "".join(out) + '"'
    if isinstance(v, dict):
        return "{" + ", ".join("[%s] = %s" % (lua_literal(k), lua_literal(val)) for k, val in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua_literal(x) for x in v) + "}"
    raise TypeError(f"cannot serialize {type(v).__name__} to Lua")


def _drop_defaults(d: dict, i: int) -> dict:
    d = dict(d)
    d.setdefault("title", f"Song {i}")
    d.setdefault("artist", f"Artist {i}")
    d.setdefault("label", d.get("subzone") or d["id"])
    if "kind" not in d:
        prefix = d["id"].split(":", 1)[0]
        d["kind"] = {"zone": "zone", "area": "subzone", "sub": "subzone", "inst": "instance"}.get(prefix, "zone")
    return d


def make_set(drops: Iterable[dict], *, set_id: str = "test-set", name: str = "Test Set",
             tags: Optional[list] = None, **extra) -> dict:
    """A built-in soundtrack (SETS-FORMAT) for ``AddonHarness(sets=[...])``: drops get a title,
    artist and label when they have none. Tagged "default", so a fresh account plays it."""
    out = {"id": set_id, "name": name, "version": 1, "author": "Tests", "description": "",
           "tags": ["default"] if tags is None else tags, "source": "tests",
           "drops": [_drop_defaults(d, i) for i, d in enumerate(drops, 1)]}
    out.update(extra)
    return out


def user_set(drops: Iterable[dict], *, set_id: str = "test", name: str = "Test Soundtrack") -> dict:
    """A soundtrack of the player's own as stored in NeedleDropperDB.soundtracks. ``file`` is the
    bare name of an in-game file (``Interface\\AddOns\\NeedleDropper_Local\\media\\<file>``)."""
    out_drops = []
    for i, d in enumerate(drops, 1):
        d = _drop_defaults(d, i)
        if isinstance(d.get("file"), str) and d["file"].startswith(MEDIA_PATH):
            d["file"] = d["file"][len(MEDIA_PATH):]
        out_drops.append(d)
    return {"id": set_id, "name": name, "author": "", "description": "", "tags": [], "revision": 1,
            "created": 0, "modified": 0, "drops": out_drops}


def user_db(drops: Iterable[dict] = (), *, set_id: str = "test", name: str = "Test Soundtrack",
            active: Optional[list] = None, settings: Optional[dict] = None, **extra) -> dict:
    """NeedleDropperDB (schema 2) with one soundtrack of the player's own, on at the top of the
    active list (``active`` overrides the list)."""
    db = {"schema": 2, "soundtracks": {set_id: user_set(drops, set_id=set_id, name=name)},
          "active": list(active) if active is not None else ["user:" + set_id]}
    if settings:
        db["settings"] = dict(settings)
    db.update(extra)
    return db


def set_source(set_dict: dict) -> str:
    """Lua source of a generated-style set file for ``set_dict``."""
    return "local ADDON, ns = ...\nns.RegisterBuiltinSet(" + lua_literal(set_dict) + ")\n"


def strip_colors(s: str) -> str:
    return _COLOR_RE.sub("", s)


def has_flag(flags: int, f: Flags) -> bool:
    return bool(int(flags) & int(f))


# ------------------------------------------------------- zlib bridge (hex) --

_LEVELS = {0: 6, 1: 1, 2: 9}     # Default, OptimizeForSpeed, OptimizeForSize
_WBITS = {0: -15, 1: 15, 2: 31}  # Deflate (raw), Zlib, Gzip


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


def _span(origin: float, i: int, pitch: float) -> tuple[int, int]:
    """Pixels whose centres fall in [origin + i*pitch, origin + (i+1)*pitch) (GPU rule)."""
    return int(math.ceil(origin + i * pitch - 0.5)), int(math.ceil(origin + (i + 1) * pitch - 0.5))


def lua_to_py(v: Any, decode: bool = False, depth: int = 0) -> Any:
    """Lua value -> Python: tables become lists (1..n keys) or dicts, integral floats ints."""
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
            conv.append((k, lua_to_py(x, decode, depth + 1)))
        keys = [k for k, _ in conv]
        if conv and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            d = dict(conv)
            return [d[i] for i in range(1, len(keys) + 1)]
        return {k: x for k, x in conv}
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v


class LuaErrors(AssertionError):
    pass


# ------------------------------------------------------------------ harness --

class AddonHarness:
    """One fake WoW client session running the addon.

    Keyword arguments:
      files           addon files to load (default: the TOC, see :func:`toc_files`)
      sets            built-in soundtracks (dicts, see :func:`make_set`) loaded instead of the
                      TOC's ``Sets/*.lua`` files
      db, char_db     NeedleDropperDB / NeedleDropperCharDB (dicts; see :func:`user_db`)
      settings        merged into ``db["settings"]``
      state, cvars    initial SHIM.state / SHIM.cvars values
      location        (mapID, subzone[, instance]) before login
      companion_data  NeedleDropper_CompanionData (the app's data addon); default absent
      ui              load tests/lua/shim_ui.lua (default True)
      fail_mode       C_EncodingUtil failure style: "error" (raise) or "nil" (return nothing)
      encoding_absent C_EncodingUtil does not exist
      epoch           time() at the start (default: the shim's fixed epoch)
      before_load     callable(h) run after the shims, before the addon files
      login, settle   log in (and advance ``settle`` seconds) at the end of construction
      strict          raise LuaErrors as soon as Lua reports an error
    """

    def __init__(self, *, files: Optional[list[str]] = None, sets: Optional[list[dict]] = None,
                 db: Optional[dict] = None, char_db: Optional[dict] = None, settings: Optional[dict] = None,
                 state: Optional[dict] = None, cvars: Optional[dict] = None, location: Optional[tuple] = None,
                 companion_data: Any = UNSET, ui: bool = True, fail_mode: str = "error",
                 encoding_absent: bool = False, decompress_limit: Optional[int] = None,
                 epoch: Optional[int] = None, login: bool = True, settle: float = 5.0, strict: bool = True,
                 before_load=None) -> None:
        self.rt = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.g = self.rt.globals()
        self.strict = strict
        self._seen_errors = 0
        self._loader = self.rt.execute("return function(src, name) return loadstring(src, name) end")
        self._same = self.rt.execute("return function(a, b) return rawequal(a, b) end")
        self._run(SHIM_PATH.read_text(encoding="utf-8"), "@wow_shim.lua")
        self.shim = self.g.SHIM
        self.state = self.shim.state
        self.log = self.shim.log
        if epoch is not None:
            self.shim.EPOCH = int(epoch)       # time() at login (a /reload keeps the wall clock going)
        meta = toc_metadata()
        self.rt.execute("SHIM.addonMetadata = " + lua_literal({k: v for k, v in meta.items()}))
        self._run(SHIM_BEACON2_PATH.read_text(encoding="utf-8"), "@shim_beacon2.lua")
        self.ui_loaded = bool(ui)
        if ui:
            self._run(SHIM_UI_PATH.read_text(encoding="utf-8"), "@shim_ui.lua")
        compress, decompress = make_bridge("hex")
        self.g.SHIM_ENCODING_BRIDGE = self.rt.table_from({"mode": "hex", "compress": compress,
                                                          "decompress": decompress})
        enc = {"failMode": fail_mode, "absent": bool(encoding_absent)}
        if decompress_limit is not None:
            enc["decompressLimit"] = int(decompress_limit)
        self.rt.execute("SHIM.encoding = " + lua_literal(enc))
        self._run(SHIM_ENCODING_PATH.read_text(encoding="utf-8"), "@shim_encoding.lua")
        if state:
            for k, v in state.items():
                self.set_state(k, v)
        if cvars:
            for k, v in cvars.items():
                self.shim.cvars[k] = v
        if location:
            self._apply_location(*location)
        if companion_data is not UNSET:
            self.rt.execute("NeedleDropper_CompanionData = " + lua_literal(companion_data))
        if before_load:
            before_load(self)
        self.check_errors()
        self._baseline_globals = set(self._global_names())

        self.sets = sets
        files = list(files) if files is not None else toc_files()
        self.files = files
        self.ns = self.rt.table()
        replaced = False
        for f in files:
            if sets is not None and is_set_file(f):
                if not replaced:
                    replaced = True
                    for s in sets:
                        self.load_source(set_source(s), "Sets/" + s.get("id", "test") + ".lua")
                continue
            self.load_file(f)
        if sets is not None and not replaced:
            for s in sets:
                self.load_source(set_source(s), "Sets/" + s.get("id", "test") + ".lua")

        # SavedVariables arrive after the files ran, before ADDON_LOADED (like the client).
        db = dict(db) if db else None
        if settings:
            db = db or {}
            merged = dict(db.get("settings") or {})
            merged.update(settings)
            db["settings"] = merged
        if db is not None:
            self.rt.execute("NeedleDropperDB = " + lua_literal(db))
        if char_db is not None:
            self.rt.execute("NeedleDropperCharDB = " + lua_literal(char_db))
        self.check_errors()
        if login:
            self.login(settle=settle)

    # -------------------------------------------------------------- loading --

    def _run(self, src: str, name: str):
        res = self._loader(src, name)
        if isinstance(res, tuple):
            fn, err = res[0], res[1] if len(res) > 1 else None
        else:
            fn, err = res, None
        if fn is None:
            raise SyntaxError(f"{name}: {err}")
        return fn()

    def compile_source(self, src: str, name: str):
        res = self._loader(src, "@" + name)
        if isinstance(res, tuple):
            return res[0], (res[1] if len(res) > 1 else None)
        return res, None

    def compile_file(self, fname: str):
        return self.compile_source((ADDON_DIR / fname).read_text(encoding="utf-8"), fname)

    def load_source(self, src: str, name: str) -> None:
        fn, err = self.compile_source(src, name)
        if fn is None:
            raise SyntaxError(f"{name}: {err}")
        fn(ADDON_NAME, self.ns)

    def load_file(self, fname: str) -> None:
        fn, err = self.compile_file(fname)
        if fn is None:
            raise SyntaxError(f"{fname}: {err}")
        fn(ADDON_NAME, self.ns)

    def login(self, settle: float = 5.0, initial: bool = True) -> None:
        self.state.loggedIn = True
        self.fire("ADDON_LOADED", ADDON_NAME)
        self.fire("PLAYER_LOGIN")
        self.fire("PLAYER_ENTERING_WORLD", initial, not initial)
        self.fire("LOADING_SCREEN_DISABLED")
        self.fire("MAP_EXPLORATION_UPDATED")
        if settle:
            self.advance(settle)

    def logout(self) -> None:
        self.fire("PLAYER_LOGOUT")

    def reload(self, settle: float = 5.0, **kw) -> "AddonHarness":
        """A /reload: logout here, then a new session with the saved data (login(initial=False))."""
        self.logout()
        kw.setdefault("sets", self.sets)
        kw.setdefault("files", self.files)
        kw.setdefault("ui", self.ui_loaded)
        kw.setdefault("epoch", int(self.lua("return time()")))
        h = type(self)(db=self.account_db(), char_db=self.char_db(), login=False, **kw)
        h.login(settle=settle, initial=False)
        return h

    # ---------------------------------------------------------------- state --

    def set_state(self, key: str, value: Any) -> None:
        if isinstance(value, (dict, list, tuple)):
            self.rt.execute("SHIM.state[%s] = %s" % (lua_literal(key), lua_literal(value)))
        else:
            self.state[key] = value

    def lua(self, src: str):
        """Execute Lua source in the runtime (statements) and return its result."""
        return self.rt.execute(src)

    def _apply_location(self, mapID, subzone="", instance=None):
        st = self.state
        st.mapID = mapID
        st.subzone = subzone or ""
        if instance:
            st.inInstance = True
            st.instanceID = instance.get("id")
            st.instanceType = instance.get("type", "party")
            st.instanceName = instance.get("name", "Instance")
        else:
            st.inInstance = False
            st.instanceID = None
            st.instanceType = "none"
            st.instanceName = None

    def set_location(self, mapID: Optional[int], subzone: str = "", instance: Optional[dict] = None,
                     settle: bool = True) -> None:
        """Move the player and fire the zone events the client would fire."""
        st = self.state
        old = (st.mapID, st.subzone, bool(st.inInstance), st.instanceID)
        self._apply_location(mapID, subzone, instance)
        new = (st.mapID, st.subzone, bool(st.inInstance), st.instanceID)
        if old[2:] != new[2:]:
            self.fire("LOADING_SCREEN_ENABLED")
            self.fire("PLAYER_LEAVING_WORLD")
            self.fire("PLAYER_ENTERING_WORLD", False, False)
            self.fire("LOADING_SCREEN_DISABLED")
            self.fire("ZONE_CHANGED_NEW_AREA")
        elif old[0] != new[0]:
            self.fire("ZONE_CHANGED_NEW_AREA")
            self.fire("PLAYER_MAP_CHANGED", old[0] or 0, new[0] or 0)
        elif old[1] != new[1]:
            self.fire("ZONE_CHANGED")
        else:
            self.fire("ZONE_CHANGED")
        if settle:
            self.advance(0.6)

    def discover(self, name: str, xp: Optional[int] = None, info: bool = True, chat: bool = True,
                 exploration: bool = True) -> None:
        self.shim.Discover(name, xp, self.rt.table_from({"noInfo": not info, "noChat": not chat,
                                                          "noExploration": not exploration}))
        self.check_errors()

    def combat(self, on: bool) -> None:
        self.state.inCombat = on
        self.fire("PLAYER_REGEN_DISABLED" if on else "PLAYER_REGEN_ENABLED")

    # ------------------------------------------------------------- driving --

    def fire(self, event: str, *args) -> None:
        self.shim.FireEvent(event, *args)
        self.check_errors()

    def advance(self, seconds: float) -> None:
        self.shim.Advance(float(seconds))
        self.check_errors()

    def flush(self) -> None:
        """Run what is due on the next frame (C_Timer.After(0, ...))."""
        self.advance(0)

    def slash(self, cmd: str) -> list[str]:
        """Run '/needle ...' (or '/nd ...', or just the arguments); returns new printed lines."""
        cmd = cmd.strip()
        for prefix in ("/needle", "/nd"):
            if cmd.lower() == prefix or cmd.lower().startswith(prefix + " "):
                cmd = cmd[len(prefix):].strip()
                break
        before = len(self.log.prints)
        self.g.SlashCmdList["NEEDLEDROPPER"](cmd)
        self.check_errors()
        return self.prints()[before:]

    @property
    def now(self) -> float:
        return float(self.shim.now)

    # -------------------------------------------------------------- errors --

    def errors(self) -> list[str]:
        return [str(e) for e in self.log.errors.values()]

    def check_errors(self) -> None:
        errs = self.errors()
        if self.strict and len(errs) > self._seen_errors:
            new = errs[self._seen_errors:]
            self._seen_errors = len(errs)
            raise LuaErrors("Lua errors:\n" + "\n----\n".join(new))
        self._seen_errors = len(errs)

    # ------------------------------------------------------- Lua <-> Python --

    def from_lua(self, v: Any, _depth: int = 0) -> Any:
        """Lua value -> Python (tables become lists/dicts). Frames and deep or cyclic
        structures are returned as the raw Lua table."""
        if lua51.lua_type(v) == "table":
            if _depth > 8 or v["__kind"] is not None:
                return v
            items = list(v.items())
            keys = [k for k, _ in items]
            if items and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
                return [self.from_lua(v[i], _depth + 1) for i in range(1, len(keys) + 1)]
            return {k: self.from_lua(val, _depth + 1) for k, val in items}
        return v

    def to_lua(self, v: Any) -> Any:
        if isinstance(v, (dict, list, tuple)):
            return self.rt.execute("return " + lua_literal(v))
        return v

    table = to_lua

    def to_py(self, v: Any) -> Any:
        return lua_to_py(v, decode=False)

    def same(self, a, b) -> bool:
        """Lua identity (lupa proxies do not compare with ==)."""
        return bool(self._same(a, b))

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

    def _global_names(self) -> list[str]:
        return [k for k in self.g.keys() if isinstance(k, str)]

    def new_globals(self) -> set[str]:
        """Globals the addon files created (relative to the moment before they were loaded)."""
        return set(self._global_names()) - self._baseline_globals

    # ------------------------------------------------------------- reading --

    def prints(self, raw: bool = False) -> list[str]:
        lines = [str(x) for x in self.log.prints.values()]
        return lines if raw else [strip_colors(x) for x in lines]

    def chat_lines(self) -> list[str]:
        """Every line the addon sent with SendSystemMessage, in order."""
        return [str(e.text) for e in self.log.chat.values()]

    def chat_entries(self) -> list[dict]:
        return [self.from_lua(e) for e in self.log.chat.values()]

    def chatlog_file(self) -> list[str]:
        """Lines that would have been written to WoWChatLog.txt."""
        return [str(x) for x in self.log.chatlog.values()]

    def music(self) -> list[list]:
        out = []
        for e in self.log.music.values():
            out.append([e[1]] + ([e[2]] if e[2] is not None else []))
        return out

    def sounds(self) -> list[dict]:
        return [self.from_lua(e) for e in self.log.sounds.values()]

    def stopped_sounds(self) -> list[dict]:
        return [self.from_lua(e) for e in self.log.stoppedSounds.values()]

    def cvar_sets(self) -> list[dict]:
        return [self.from_lua(e) for e in self.log.cvarSets.values()]

    def cvar(self, name: str) -> Optional[str]:
        """The stored (archived) value: what Config.wtf would get."""
        return self.shim.cvars[name]

    def effective_cvar(self, name: str) -> Optional[str]:
        """What GetCVar answers now (a temp value shadows the stored one)."""
        return self.g.GetCVar(name)

    def account_db(self) -> dict:
        v = self.g.NeedleDropperDB
        return self.from_lua(v) if v is not None else None

    def char_db(self) -> dict:
        v = self.g.NeedleDropperCharDB
        return self.from_lua(v) if v is not None else None

    def settings(self) -> dict:
        return self.account_db()["settings"]

    def set_setting(self, key: str, value: Any) -> None:
        """Write a setting straight into the table and announce it (like the Settings panel)."""
        self.ns.settings[key] = value
        self.ns.Fire("ND_SETTING", key, value)
        self.check_errors()

    # ------------------------------------------------------------ the model --

    def rebuild(self) -> None:
        """Let the debounced map rebuild run (0.25 s)."""
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

    # -------------------------------------------------------------- events --

    def events(self) -> list[dict]:
        """Events the addon emitted (ns.Events: Protocol2 event tables), oldest first."""
        evs = self.ns.Events
        if evs is None:
            return []
        return [self.from_lua(e) for e in evs.values()]

    def events_of(self, etype: EventType) -> list[dict]:
        return [e for e in self.events() if e["type"] == int(etype)]

    def last_event(self) -> Optional[dict]:
        evs = self.events()
        return evs[-1] if evs else None

    def mark(self) -> int:
        """Index to pass to events_since()."""
        return len(self.events())

    def events_since(self, mark: int) -> list[dict]:
        return self.events()[mark:]

    def emit(self, etype: int, drop: Optional[dict] = None, extra: int = 0, opts: Optional[dict] = None) -> dict:
        ev = self.ns.Emit(int(etype), None if drop is None else self.table(drop), extra,
                          None if opts is None else self.table(opts))
        self.check_errors()
        return self.from_lua(ev)

    def build_event(self, etype: int, drop: Optional[dict] = None, extra: int = 0,
                    opts: Optional[dict] = None) -> dict:
        ev = self.ns.BuildEvent(int(etype), None if drop is None else self.table(drop), extra,
                                None if opts is None else self.table(opts))
        self.check_errors()
        return self.from_lua(ev)

    @staticmethod
    def frame_of(ev: dict) -> p2.Frame:
        """A Protocol2 event table (as events() returns it) as the companion's Frame, through the
        full chat-line frame (every field, the 262-byte budget)."""
        return p2.frame_from_event(ev)

    def chat_frames(self) -> list[p2.Frame]:
        """Every NDROP2 line the addon sent, parsed with the companion's parser."""
        out = []
        for line in self.chat_lines():
            if line.startswith("NDROP2 "):
                fr = p2.parse_chat_line(line)
                assert fr is not None, f"unparsable chat line {line!r}"
                out.append(fr)
        return out

    # ------------------------------------------------------- beacon readers --

    def beacon_frame(self):
        return self.g.NeedleDropperBeacon

    def beacon_shown(self) -> bool:
        f = self.beacon_frame()
        return bool(f is not None and f.IsVisible(f))

    def beacon_status(self) -> dict:
        st = self.ns.Beacon.Status()
        self.check_errors()
        return self.from_lua(st)

    def chat_status(self) -> dict:
        return self.from_lua(self.ns.ChatLink.Status())

    def art(self, name: str = "PlayerFrame", **opts) -> None:
        """Place fake unit frame art (physical px from the screen top-left; see shim_beacon2)."""
        self.shim.SetUnitFrameArt(name, self.table(opts))

    def trigger_registry(self, event: str) -> None:
        self.shim.TriggerRegistry(event)
        self.check_errors()

    def pixel_factor(self) -> float:
        return 768.0 / float(self.state.physH)

    def beacon_textures(self) -> list:
        """Every cell texture of the beacon frame's pool (shown or not)."""
        f = self.beacon_frame()
        if f is None:
            return []
        return [r for r in f["__regions"].values() if r["__kind"] == "Texture"]

    def shown_textures(self) -> list:
        f = self.beacon_frame()
        if f is None:
            return []
        return [r for r in f["__regions"].values() if r["__kind"] == "Texture" and r.IsShown(r)]

    def texture_rects(self) -> list[tuple[float, float, float, float, tuple[int, int, int]]]:
        """(x, y, w, h, rgb) of every shown cell in PHYSICAL pixels from the strip's top-left."""
        f = self.beacon_frame()
        es = float(f.GetEffectiveScale(f))
        k = es / self.pixel_factor()
        out = []
        for t in self.shown_textures():
            p = t["__points"]["TOPLEFT"]
            assert p is not None and p[3] == "TOPLEFT", "cells are anchored by their TOPLEFT"
            w, h = t.GetSize(t)
            c = t["__color"]
            rgb = tuple(int(round(float(c[i]) * 255)) for i in (1, 2, 3))
            out.append((float(p[4]) * k, -float(p[5]) * k, float(w) * k, float(h) * k, rgb))
        return out

    def beacon_grid(self) -> Optional[list[list[tuple[int, int, int]]]]:
        """The drawn strip as rows of RGB cells (0/255), or None when nothing is drawn."""
        rects = self.texture_rects() if self.beacon_frame() is not None else []
        if not rects:
            return None
        cell = rects[0][2]
        rows: dict[int, dict[int, tuple[int, int, int]]] = {}
        for x, y, w, h, rgb in rects:
            assert abs(w - cell) < 1e-6 and abs(h - cell) < 1e-6, "every cell has the same size"
            r, c = int(round(y / cell)), int(round(x / cell))
            assert abs(r * cell - y) < 1e-6 and abs(c * cell - x) < 1e-6, "cells sit on the cell lattice"
            rows.setdefault(r, {})[c] = rgb
        n_rows = max(rows) + 1
        n_cols = max(max(r) for r in rows.values()) + 1
        assert sorted(rows) == list(range(n_rows)), "no missing rows"
        grid = [[rows[r][c] for c in range(n_cols)] for r in range(n_rows)]
        assert len(rects) == n_rows * n_cols, "a full rows x cols rectangle"
        return grid

    def beacon_cell_px(self) -> float:
        """Drawn cell size in physical pixels."""
        rects = self.texture_rects()
        return rects[0][2] if rects else 0.0

    def beacon_decoded(self) -> Optional[p2.Frame]:
        """The drawn strip decoded with the companion's grid decoder (None if not decodable)."""
        grid = self.beacon_grid()
        return None if grid is None else p2.decode_cells_grid(grid)

    def beacon_bgra(self, *, scale: float = 1.0, width: int = 512, height: int = 48, seed: int = 0,
                    background: str = "game") -> tuple[bytes, int, int, int]:
        """A synthetic top-down BGRA capture of the client area's top-left ``width x height``
        region: a busy game-like background with the strip's textures drawn at their physical
        pixel rectangles, everything magnified by ``scale`` (a Windows display scale applied by
        the capture: 1.25 = 125 %). Pixels belong to the cell containing their centre."""
        rng = random.Random(seed)
        px = bytearray(width * height * 4)
        if background == "game":
            extra = [(255, 0, 255), (0, 255, 0), (240, 10, 250), (5, 250, 12)]
            for y in range(height):
                x = 0
                while x < width:
                    n = min(rng.randint(1, 7), width - x)
                    r, g, b = rng.choice(extra) if rng.random() < 0.15 else (
                        rng.randrange(256), rng.randrange(256), rng.randrange(256))
                    o = (y * width + x) * 4
                    px[o:o + n * 4] = bytes((b, g, r, 255)) * n
                    x += n
        elif background == "white":
            px[:] = b"\xff" * len(px)
        f = self.beacon_frame()
        visible = f is not None and f.IsVisible(f)
        if visible:
            for x, y, w, h, rgb in self.texture_rects():
                xa, xb = _span(x * scale, 0, w * scale)
                ya, yb = _span(y * scale, 0, h * scale)
                pixel = bytes((rgb[2], rgb[1], rgb[0], 255))
                for yy in range(max(0, ya), min(height, yb)):
                    a, b = max(0, xa), min(width, xb)
                    if b > a:
                        o = (yy * width + a) * 4
                        px[o:o + (b - a) * 4] = pixel * (b - a)
        return bytes(px), width, height, width * 4

    def beacon_capture_decoded(self, **kw):
        """The strip as the companion's pixel transport reads it: a synthetic capture decoded by
        ``transport.beacon2.locate_and_decode`` (a protocol2.Frame, or None)."""
        from needledropper.transport import beacon2
        buf, w, h, stride = self.beacon_bgra(**kw)
        res = beacon2.locate_and_decode(buf, w, h, stride)
        return getattr(res, "frame", res)

    # ----------------------------------------------------- title card (toast) --

    TOAST_FRAME = "NeedleDropperTitleCard"

    def render(self) -> None:
        """Draw one frame at the current fake time (every visible OnUpdate, elapsed 0)."""
        self.shim.RenderFrame()
        self.check_errors()

    def toast_frame(self):
        """The title card frame, or None before the first card."""
        return self.g[self.TOAST_FRAME]

    def _toast_regions(self, kind: str) -> list:
        f = self.toast_frame()
        if f is None:
            return []
        return [r for r in f["__regions"].values() if r["__kind"] == kind]

    def toast_texts(self) -> list[str]:
        """Text of the card's font strings in creation order: [place line, credit line]."""
        return [str(r["__text"]) for r in self._toast_regions("FontString") if r["__text"] is not None]

    def toast_font_strings(self) -> list:
        return self._toast_regions("FontString")

    def toast_rule(self):
        """The card's rule texture (line 2), or None."""
        texs = self._toast_regions("Texture")
        return texs[0] if texs else None

    def toast_visible(self) -> bool:
        f = self.toast_frame()
        return bool(f is not None and f.IsVisible(f) and float(f.GetAlpha(f)) > 0)

    def toast_alpha(self) -> float:
        f = self.toast_frame()
        return float(f.GetAlpha(f)) if f is not None and f.IsShown(f) else 0.0

    def toast_anchor(self) -> Optional[tuple]:
        """(point, relativeTo, relativePoint, x, y) of the card's anchor, or None."""
        f = self.toast_frame()
        if f is None or f.GetNumPoints(f) == 0:
            return None
        return f.GetPoint(f, 1)

    def toast_y(self) -> float:
        a = self.toast_anchor()
        return float(a[4]) if a is not None else float("nan")

    def toast_rule_width(self) -> float:
        """Drawn width of the rule (0 while it is hidden)."""
        r = self.toast_rule()
        if r is None or not r.IsShown(r):
            return 0.0
        return float(r.GetWidth(r))

    def toast_fonts(self) -> list[tuple]:
        """(path, size, flags) of [place line, credit line]."""
        return [tuple(fs.GetFont(fs)) for fs in self.toast_font_strings()]

    def toast_card(self) -> Optional[dict]:
        """The card's animation / layout state (ns.Toast.card) as a dict, or None."""
        c = self.ns.Toast.card
        return None if c is None else self.from_lua(c)
