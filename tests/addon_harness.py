"""Test harness that runs the NeedleDropper WoW addon inside lupa's Lua 5.1.

The game cannot run here, so ``tests/lua/wow_shim.lua`` fakes the WoW API surface the
addon uses and this module drives it: it builds a runtime, loads the shim, injects
``NeedleDropper_Map`` and SavedVariables exactly like the client does (map file before
Core.lua, SavedVariables after all files but before ``ADDON_LOADED``), loads the addon
files in TOC order with the ``(ADDON, ns)`` varargs, logs in, and offers helpers to move
the player around, advance the fake clock and read what the addon emitted.

Usage::

    from addon_harness import AddonHarness, make_map
    h = AddonHarness(map=make_map([{"id": "zone:1429", "kind": "zone", "mapID": 1429, ...}]))
    h.set_location(1429, "Northshire Valley")
    h.advance(12)
    assert h.last_event()["type"] == EventType.FIRE
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "addon" / "NeedleDropper"
SHIM_PATH = Path(__file__).resolve().parent / "lua" / "wow_shim.lua"
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import protocol  # noqa: E402
from needledropper.models import Event, EventType, Flags  # noqa: E402

import lupa.lua51 as lua51  # noqa: E402

ADDON_NAME = "NeedleDropper"
SILENCE = "Interface\\AddOns\\NeedleDropper\\media\\silence.mp3"
_COLOR_RE = re.compile(r"\|c[0-9a-fA-F]{8}|\|r")


# ------------------------------------------------------------------ helpers --

def toc_files() -> list[str]:
    """Lua files listed in NeedleDropper.toc, in load order."""
    out = []
    for line in (ADDON_DIR / "NeedleDropper.toc").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


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
                out.append("\\%d" % b)
        return '"' + "".join(out) + '"'
    if isinstance(v, dict):
        return "{" + ", ".join("[%s] = %s" % (lua_literal(k), lua_literal(val)) for k, val in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua_literal(x) for x in v) + "}"
    raise TypeError(f"cannot serialize {type(v).__name__} to Lua")


def make_map(drops: Iterable[dict], *, version: int = 1, packs: Optional[list] = None,
             hash_override: Optional[int] = None) -> dict:
    """Build a Map like companion mapgen does: sort by id, number from 1, SPEC 3.4 hash."""
    entries = sorted((dict(d) for d in drops), key=lambda d: d["id"])
    for i, d in enumerate(entries, 1):
        d["n"] = i
        d.setdefault("title", f"Song {i}")
        d.setdefault("artist", f"Artist {i}")
        d.setdefault("durationMs", 0)
        d.setdefault("startMs", 0)
        d.setdefault("label", d["id"])
    h = protocol.map_hash(entries) if hash_override is None else hash_override
    return {"version": version, "hash": h, "generated": "2026-11-05T18:00:00Z",
            "packs": packs or ["Test Pack"], "drops": entries}


def strip_colors(s: str) -> str:
    return _COLOR_RE.sub("", s)


class LuaErrors(AssertionError):
    pass


# ------------------------------------------------------------------ harness --

class AddonHarness:
    """One fake WoW client session running the addon."""

    def __init__(self, map: Any = None, *, map_lua: Optional[str] = None, use_map_file: bool = False,
                 db: Optional[dict] = None, char_db: Optional[dict] = None, settings: Optional[dict] = None,
                 state: Optional[dict] = None, cvars: Optional[dict] = None, location: Optional[tuple] = None,
                 login: bool = True, settle: float = 5.0, strict: bool = True,
                 files: Optional[list[str]] = None, before_load=None) -> None:
        self.rt = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.g = self.rt.globals()
        self.strict = strict
        self._seen_errors = 0
        self._loader = self.rt.execute("return function(src, name) return loadstring(src, name) end")
        self._run(SHIM_PATH.read_text(encoding="utf-8"), "@wow_shim.lua")
        self.shim = self.g.SHIM
        self.state = self.shim.state
        self.log = self.shim.log
        self._baseline_globals = set(self._global_names())
        if state:
            for k, v in state.items():
                self.set_state(k, v)
        if cvars:
            for k, v in cvars.items():
                self.shim.cvars[k] = v
        if location:
            self._apply_location(*location)
        if before_load:
            before_load(self)

        self.map = map
        files = list(files) if files is not None else toc_files()
        if map_lua is not None:
            self.rt.execute(map_lua)
            files = [f for f in files if f != "Map.lua"]
        elif not use_map_file:
            self.rt.execute("NeedleDropper_Map = " + lua_literal(map))
            files = [f for f in files if f != "Map.lua"]
        self.ns = self.rt.table()
        for f in files:
            self.load_file(f)

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

    def compile_file(self, fname: str):
        src = (ADDON_DIR / fname).read_text(encoding="utf-8")
        res = self._loader(src, "@" + fname)
        if isinstance(res, tuple):
            return res[0], (res[1] if len(res) > 1 else None)
        return res, None

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

    # ------------------------------------------------------------- reading --

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

    def _global_names(self) -> list[str]:
        return [k for k in self.g.keys() if isinstance(k, str)]

    def new_globals(self) -> set[str]:
        return set(self._global_names()) - self._baseline_globals

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
        return self.shim.cvars[name]

    def account_db(self) -> dict:
        v = self.g.NeedleDropperDB
        return self.from_lua(v) if v is not None else None

    def char_db(self) -> dict:
        v = self.g.NeedleDropperCharDB
        return self.from_lua(v) if v is not None else None

    def settings(self) -> dict:
        return self.account_db()["settings"]

    # -------------------------------------------------------------- events --

    def events(self) -> list[dict]:
        """Events the addon emitted (ns.Events), oldest first."""
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

    def n_of(self, drop_id: str) -> int:
        """Drop number of `drop_id` in the map this harness was built with."""
        for d in self.map["drops"]:
            if d["id"] == drop_id:
                return d["n"]
        raise KeyError(drop_id)

    @staticmethod
    def as_event(d: dict) -> Event:
        return Event(EventType(d["type"]), d["seq"], d["n"], d["hash"], Flags(d["flags"] & 0x7F))

    # -------------------------------------------------------------- beacon --

    def beacon_frame(self):
        return self.g.NeedleDropperBeacon

    def beacon_textures(self) -> list:
        f = self.beacon_frame()
        if f is None:
            return []
        texs = [r for r in f["__regions"].values() if r["__kind"] == "Texture"]

        def xoff(t):
            p = t["__points"]["TOPLEFT"]
            return float(p[4]) if p is not None else 0.0

        return sorted(texs, key=xoff)

    def beacon_cells(self) -> list[tuple[int, int, int]]:
        """Beacon strip as RGB tuples (0/255), left to right, header included."""
        out = []
        for t in self.beacon_textures():
            c = t["__color"]
            if c is None:
                out.append((0, 0, 0))
            else:
                out.append(tuple(int(round(float(c[i]) * 255)) for i in (1, 2, 3)))
        return out

    def beacon_shown(self) -> bool:
        f = self.beacon_frame()
        return bool(f is not None and f.IsVisible(f))

    def beacon_event(self) -> Optional[Event]:
        """Decode the beacon strip with the companion's Python decoder."""
        cells = self.beacon_cells()
        if len(cells) != protocol.TOTAL_CELLS or tuple(cells[:4]) != protocol.HEADER:
            return None
        return protocol.decode_cells(cells[len(protocol.HEADER):])

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
