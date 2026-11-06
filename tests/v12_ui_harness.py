"""Loader for the NeedleDropper window tests (A2, DESIGN-v1.2 §4.3-4.10).

Runs the addon in lupa's Lua 5.1 like ``tests/addon_harness.py`` does, with two additions:

* ``tests/lua/shim_ui.lua`` is loaded right after ``wow_shim.lua``: a recording frame mock of the
  Blizzard widget kit (template allowlist enforced by ``CreateFrame``), ScrollBox / Blizzard_Menu
  / StaticPopup / GameTooltip / UISpecialFrames, and input helpers (``SHIM.ui.Click``, ``Type``,
  ``Paste``, ``PressEscape`` ...);
* the A1 model (``ns.Soundtracks`` / ``ns.Share`` / ``ns.Companion``) is either the stand-in in
  ``SHIM.uiFakes`` (exactly the CONTRACTS 2.5-2.7 signatures; the default) or, with
  ``real=True``, the real ``Locale.lua`` / ``Sets.lua`` / ``Soundtracks.lua`` / ``Share.lua`` /
  ``Companion.lua`` once they exist.

The v1.2 TOC is the integrator's, so the file list is spelled out here: today's v1 core files
(``Protocol.lua`` is still needed by ``Director.lua``), ``Protocol2.lua`` for the link parsers, then
the five ``UI/*.lua`` files in CONTRACTS 2.1 order.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import ADDON_DIR, AddonHarness, lua_literal  # noqa: E402

SHIM_UI = Path(__file__).resolve().parent / "lua" / "shim_ui.lua"
SHIM_ENCODING = Path(__file__).resolve().parent / "lua" / "shim_encoding.lua"

UI_FILES = ["UI/Window.lua", "UI/Drops.lua", "UI/Soundtracks.lua", "UI/Journal.lua", "UI/ShareDialogs.lua"]

# Today's core (v1 Director needs v1 Protocol.lua); Map.lua is replaced by an injected value.
# Beacon.lua / ChatLink.lua / Emit.lua belong to A3 and are being rewritten concurrently; the
# Director reaches them only through `if ns.Beacon` / `if ns.ChatLink`, so they are left out.
CORE_BEFORE = ["Protocol.lua", "Protocol2.lua", "Core.lua"]
CORE_AFTER = ["Location.lua", "Discovery.lua", "Audio.lua", "Toast.lua", "Director.lua", "Options.lua"]

# CONTRACTS 2.1 order of the A1 modules (used with real=True, when the files exist).
A1_EARLY = ["Locale.lua", "Json.lua", "Base64.lua"]
A1_MODEL = ["Sets.lua", "Sets/AzerothMixtape.lua", "Sets/AzerothUnbound.lua", "Soundtracks.lua", "Share.lua",
            "Companion.lua"]

A1_REQUIRED = ["Locale.lua", "Sets.lua", "Soundtracks.lua", "Share.lua", "Companion.lua"]


def a1_available() -> bool:
    """True when A1's model files exist (the real-module smoke tests run then)."""
    return all((ADDON_DIR / f).exists() for f in A1_REQUIRED)


def ui_files_present() -> list[str]:
    return [f for f in UI_FILES if (ADDON_DIR / f).exists()]


class UIHarness(AddonHarness):
    """A fake client session with the NeedleDropper window loaded (but not built)."""

    def __init__(self, *, real: bool = False, builtins: Optional[list] = None,
                 location: Optional[tuple] = (1429, "Goldshire"), login: bool = True, settle: float = 5.0,
                 load_ui: bool = True, db: Optional[dict] = None, char_db: Optional[dict] = None,
                 settings: Optional[dict] = None, known_files: Optional[list] = None, **kw: Any) -> None:
        self.real = real
        early, model = [], []
        if real:
            early = [f for f in A1_EARLY if (ADDON_DIR / f).exists()]
            model = [f for f in A1_MODEL if (ADDON_DIR / f).exists()]
        files = early + CORE_BEFORE + model + CORE_AFTER
        dedup: list[str] = []
        for f in files:
            if f not in dedup:
                dedup.append(f)

        def before(h: "UIHarness") -> None:
            h._run(SHIM_UI.read_text(encoding="utf-8"), "@shim_ui.lua")
            if real and SHIM_ENCODING.exists():
                # A1's zlib bridge for C_EncodingUtil (tests/v12_model_harness.py), hex mode.
                from v12_model_harness import make_bridge
                compress, decompress = make_bridge("hex")
                h.g.SHIM_ENCODING_BRIDGE = h.rt.table_from({"mode": "hex", "compress": compress,
                                                             "decompress": decompress})
                h.rt.execute('SHIM.encoding = { failMode = "error", absent = false }')
                h._run(SHIM_ENCODING.read_text(encoding="utf-8"), "@shim_encoding.lua")
            for path in known_files or []:
                h.shim.state.knownFiles[path] = True

        super().__init__(map=None, files=dedup, login=False, before_load=before, location=location,
                         db=db, char_db=char_db, settings=settings, **kw)
        self.ui_shim = self.g.SHIM.ui
        self.fakes = self.g.SHIM.uiFakes
        self._same = self.rt.execute("return function(a, b) return rawequal(a, b) end")
        if not real:
            opts = {"builtins": builtins} if builtins is not None else {}
            self.fakes.Install(self.ns, self.rt.execute("return " + lua_literal(opts)))
        self.frames_before_ui = len(list(self.shim.frames.values()))
        self.created_before_ui = len(list(self.ui_shim.created.values()))
        if load_ui:
            for f in ui_files_present():
                self.load_file(f)
        self.frames_after_ui = len(list(self.shim.frames.values()))
        self.fakes.InstallDirector(self.ns)
        self.check_errors()
        if login:
            self.login(settle=settle)

    # ---------------------------------------------------------------- access --

    @property
    def UI(self):
        return self.ns.UI

    @property
    def S(self):
        return self.ns.Soundtracks

    @property
    def window(self):
        return self.g.NeedleDropperFrame

    def tab_panel(self, key: str):
        return self.window[key]

    def drops(self):
        return self.window.drops

    def form(self):
        return self.window.drops.FormPane.Form

    def attic(self, index: int):
        """The top-bar frame of tab `index` (1-based)."""
        return self.UI._W.Tabs()[index].attic

    def buttons(self, index: int):
        return self.UI._W.Tabs()[index].buttons

    def eval(self, expr: str):
        return self.rt.execute("return " + expr)

    def table(self, value):
        """A Python list / dict as a Lua table (lupa passes Python containers as userdata)."""
        return self.rt.execute("return " + lua_literal(value))

    def set_active(self, refs: list[str]):
        res = self.S.SetActive(self.table(refs))
        self.check_errors()
        return res

    def same(self, a, b) -> bool:
        """Lua identity (lupa proxies do not compare with ==)."""
        return bool(self._same(a, b))

    def focused(self):
        return self.ui_shim.focus

    def has_focus(self, box) -> bool:
        f = self.ui_shim.focus
        return f is not None and self.same(f, box)

    def shown(self, frame) -> bool:
        return bool(frame is not None and frame.IsShown(frame))

    def visible(self, frame) -> bool:
        return bool(frame is not None and frame.IsVisible(frame))

    def text(self, region) -> str:
        t = region.GetText(region)
        return "" if t is None else str(t)

    def enabled(self, widget) -> bool:
        return bool(widget.IsEnabled(widget))

    def checked(self, check) -> bool:
        return bool(check.GetChecked(check))

    def open(self, tab: Optional[str] = None):
        res = self.UI.Open(tab)
        self.check_errors()
        self.flush()
        return res

    # ---------------------------------------------------------------- input --

    def _do(self, fn, *args):
        res = fn(*args)
        self.check_errors()
        return res

    def click(self, btn, button: str = "LeftButton"):
        return self._do(self.ui_shim.Click, btn, button)

    def right_click(self, btn):
        return self.click(btn, "RightButton")

    def type(self, box, text: str):
        return self._do(self.ui_shim.Type, box, text)

    def paste(self, box, text: str):
        return self._do(self.ui_shim.Paste, box, text)

    def enter(self, box=None):
        return self._do(self.ui_shim.PressEnter, box)

    def tab_key(self, box=None, shift: bool = False):
        return self._do(self.ui_shim.PressTab, box, shift)

    def escape(self) -> str:
        return str(self._do(self.ui_shim.PressEscape))

    def focus(self, box):
        return self._do(self.ui_shim.Focus, box)

    def hover(self, frame):
        return self._do(self.ui_shim.Enter, frame)

    def unhover(self, frame):
        return self._do(self.ui_shim.Leave, frame)

    def drag(self, frame, dx: float, dy: float):
        return self._do(self.ui_shim.Drag, frame, dx, dy)

    def resize(self, grip, w: float, h: float):
        return self._do(self.ui_shim.Resize, grip, w, h)

    def set_slider(self, slider, value: float):
        return self._do(self.ui_shim.SetSlider, slider, value)

    def flush(self):
        """Run what is due on the next frame (C_Timer.After(0, ...))."""
        self.advance(0)

    # ---------------------------------------------------------------- menus --

    def dropdown_items(self, dd) -> list[dict]:
        items = self._do(self.ui_shim.DropdownItems, dd)
        return [self.from_lua(i) for i in items.values()]

    def dropdown_pick(self, dd, *path: str):
        return self._do(self.ui_shim.DropdownPick, dd, *path)

    def context_items(self) -> list[dict]:
        root = self.ui_shim.ContextMenu()
        assert root is not None, "no context menu is open"
        return [self.from_lua(i) for i in self.ui_shim.MenuItems(root).values()]

    def context_pick(self, *path: str):
        return self._do(self.ui_shim.ContextPick, *path)

    # --------------------------------------------------------------- popups --

    def popup(self, which: str):
        return self.ui_shim.Popup(which)

    def popup_accept(self, which: str):
        return self._do(self.ui_shim.PopupAccept, which)

    def popup_cancel(self, which: str):
        return self._do(self.ui_shim.PopupCancel, which)

    # -------------------------------------------------------------- reading --

    def tooltip(self) -> list[str]:
        return [str(x) for x in self.ui_shim.TooltipLines().values()]

    def texts(self, frame) -> list[str]:
        return [str(x) for x in self.ui_shim.Texts(frame).values()]

    def calls(self, obj, name: Optional[str] = None) -> list[list]:
        return [list(c.values()) for c in self.ui_shim.Calls(obj, name).values()]

    def count(self, obj, name: str) -> int:
        return int(self.ui_shim.CallCount(obj, name))

    def fake_calls(self, name: str) -> list[list]:
        """Calls of a SHIM.uiFakes model function: each a list [name, arg1, ...]."""
        out = []
        for c in self.fakes.Called(name).values():
            out.append([self.from_lua(v) for v in c.values()])
        return out

    def play_tests(self) -> list[dict]:
        return [self.from_lua(d) for d in self.fakes.playTests.values()]

    def created(self) -> list[dict]:
        out = []
        for c in self.ui_shim.created.values():
            out.append({"frameType": c.frameType, "name": c.name, "template": c.template, "frame": c.frame})
        return out

    def bad_escapes(self) -> list[dict]:
        return [self.from_lua(e) for e in self.ui_shim.badEscapes.values()]

    def forbidden(self) -> list[dict]:
        return [self.from_lua(e) for e in self.ui_shim.forbidden.values()]

    def special_frames(self) -> list[str]:
        return [str(x) for x in self.g.UISpecialFrames.values()]

    def selected(self) -> Optional[tuple]:
        sel = self.UI._W.Drops.state.selected
        if sel is None:
            return None
        return (sel.ref, sel.id)

    def rows(self) -> list:
        """The Drops list's visible row frames, top to bottom."""
        return list(self.drops().List.GetFrames(self.drops().List).values())

    def row_for(self, ref: str, drop_id: str):
        for r in self.rows():
            d = r.ndData
            if d is not None and d.ref == ref and d.id == drop_id:
                return r
        raise KeyError((ref, drop_id))

    def user_set(self, set_id: str) -> Optional[dict]:
        v = self.g.NeedleDropperDB.soundtracks[set_id]
        return self.from_lua(v) if v is not None else None

    def active_refs(self) -> list[str]:
        return [str(x) for x in self.S.ActiveRefs().values()]
