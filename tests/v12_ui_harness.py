"""Loader for the NeedleDropper window tests (A2, DESIGN-v1.2 4.3-4.10).

A thin wrapper over the unified :class:`addon_harness.AddonHarness`, which loads
``tests/lua/shim_ui.lua`` (a recording frame mock of the Blizzard widget kit the window is built
from: template allowlist enforced by ``CreateFrame``, ScrollBox / Blizzard_Menu / StaticPopup /
GameTooltip / UISpecialFrames, and input helpers ``SHIM.ui.Click``, ``Type``, ``Paste``,
``PressEscape`` ...) and the addon in the real TOC order. The A1 model (``ns.Soundtracks`` /
``ns.Share`` / ``ns.Companion``) is either the stand-in in ``SHIM.uiFakes`` (exactly the
CONTRACTS 2.5-2.7 signatures; the default: the model files are not loaded) or, with
``real=True``, the real ``Sets.lua`` / ``Soundtracks.lua`` / ``Share.lua`` / ``Companion.lua``.
``SHIM.uiFakes.InstallDirector`` records the window's Director calls (PlayTest, SuppressHere) and
passes them on to the real Director.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import (ADDON_DIR, MODEL_FILES, SHIM_ENCODING_PATH, AddonHarness, is_set_file,  # noqa: E402
                           lua_literal, toc_files)

SHIM_UI = Path(__file__).resolve().parent / "lua" / "shim_ui.lua"
SHIM_ENCODING = SHIM_ENCODING_PATH

UI_FILES = [f for f in toc_files() if f.startswith("UI/")]

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
                 settings: Optional[dict] = None, known_files: Optional[list] = None, before_load=None,
                 **kw: Any) -> None:
        self.real = real
        files = toc_files()
        if not real:
            files = [f for f in files if f not in MODEL_FILES and not is_set_file(f)]
        if not load_ui:
            files = [f for f in files if not f.startswith("UI/")]
        self._builtins = builtins
        self._ui_split = False

        def before(h: "UIHarness") -> None:
            for path in known_files or []:
                h.shim.state.knownFiles[path] = True
            if before_load:
                before_load(h)

        super().__init__(files=files, login=False, before_load=before, location=location,
                         db=db, char_db=char_db, settings=settings, **kw)
        if not self._ui_split:
            self._split_point()
        self.frames_after_ui = len(list(self.shim.frames.values()))
        self.fakes.InstallDirector(self.ns)
        self.check_errors()
        if login:
            self.login(settle=settle)

    def _split_point(self) -> None:
        """Before the first UI file: count the frames so far, install the model stand-ins."""
        self._ui_split = True
        self.ui_shim = self.g.SHIM.ui
        self.fakes = self.g.SHIM.uiFakes
        if not self.real:
            opts = {"builtins": self._builtins} if self._builtins is not None else {}
            self.fakes.Install(self.ns, self.rt.execute("return " + lua_literal(opts)))
        self.frames_before_ui = len(list(self.shim.frames.values()))
        self.created_before_ui = len(list(self.ui_shim.created.values()))

    def load_file(self, fname: str) -> None:
        if fname.startswith("UI/") and not self._ui_split:
            self._split_point()
        super().load_file(fname)

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

    def set_active(self, refs: list[str]):
        res = self.S.SetActive(self.table(refs))
        self.check_errors()
        return res

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
