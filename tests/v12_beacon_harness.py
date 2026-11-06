"""Harness for the v1.2 protocol cutover (A3): Protocol2 + Emit + Beacon v2 + ChatLink v2.

Wraps :class:`addon_harness.AddonHarness` with

* ``tests/lua/shim_beacon2.lua`` loaded right after the base shim (EventRegistry, the Chat
  addon restriction, fake PlayerFrame / TargetFrame art for the clearance guard);
* an explicit file list instead of the TOC (the integrator writes the final TOC): Protocol2,
  Core, Emit, Beacon, ChatLink -- the v1.1 Director still defines a v1 ``ns.Emit`` and needs
  ``ns.Protocol``, so it is not loaded here (``integrated_files`` /
  ``integrated_source`` reproduce the documented integrator patch for an end-to-end check);
* readers for the beacon strip: the drawn grid (rows of RGB, read from the shown textures'
  positions), the decoded frame, and a synthetic BGRA capture of the client's top-left corner
  rendered from the textures' physical-pixel rectangles (optionally at a Windows display
  scale), for :func:`needledropper.transport.beacon2.locate_and_decode`.
"""
from __future__ import annotations

import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from addon_harness import ADDON_DIR, AddonHarness, lua_literal, make_map  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402

SHIM_EXT = HERE / "lua" / "shim_beacon2.lua"

#: Files this harness loads, in final-TOC order (CONTRACTS-v1.2 2.1), restricted to A3's
#: modules and the core services they need.
V12_FILES = ["Protocol2.lua", "Core.lua", "Emit.lua", "Beacon.lua", "ChatLink.lua"]

#: The v1.1 modules plus A3's, in final-TOC order, for the integration check.
INTEGRATED_FILES = ["Protocol2.lua", "Core.lua", "Emit.lua", "Location.lua", "Discovery.lua", "Audio.lua",
                    "Beacon.lua", "ChatLink.lua", "Toast.lua", "Director.lua", "Options.lua", "SelfTest.lua"]

EMPTY_MAP = make_map([])

# The integrator's patch for Director.lua / SelfTest.lua (see the A3 report): delete the v1
# event history, flag helpers and ns.Emit; read the event constants from ns.Protocol2.
DIRECTOR_DELETIONS = [
    (r"\nns\.Events = \{\}[^\n]*\nns\.LastEvent = nil\n", "\n"),
    (r"\nlocal function addFlag\(f, bit\)\n.*?\nend\n", "\n"),
    (r"\n-- Flags from live state \(SPEC 3\.3\), plus any extra bits\.\nfunction D\.ComputeFlags\(extra\)\n.*?\n  return f\nend\n",
     "\n"),
    (r"\n-- Emits one event to every transport\. opts: \{ noPad = bool, forceChat = bool \}\.\n"
     r"function ns\.Emit\(evType, drop, extraFlags, opts\)\n.*?\n  return ev\nend\n", "\n"),
]
PROTOCOL_V1_REF = (r"local P = ns\.Protocol\n", "local P = ns.Protocol2\n")


def integrated_source(fname: str) -> str:
    """Source of ``fname`` as the integrator will ship it (only Director / SelfTest change)."""
    src = (ADDON_DIR / fname).read_text(encoding="utf-8")
    if fname == "Director.lua":
        for pat, rep in DIRECTOR_DELETIONS:
            src, n = re.subn(pat, rep, src, count=1, flags=re.S)
            if n != 1:
                raise LookupError(f"Director.lua no longer matches the documented patch: {pat[:60]!r}")
    if fname in ("Director.lua", "SelfTest.lua"):
        src, n = re.subn(PROTOCOL_V1_REF[0], PROTOCOL_V1_REF[1], src, count=1)
        if n != 1:
            raise LookupError(f"{fname} has no 'local P = ns.Protocol' line any more")
    return src


def _span(origin: float, i: int, pitch: float) -> tuple[int, int]:
    """Pixels whose centres fall in [origin + i*pitch, origin + (i+1)*pitch) (GPU rule)."""
    return int(math.ceil(origin + i * pitch - 0.5)), int(math.ceil(origin + (i + 1) * pitch - 0.5))


class BeaconHarness(AddonHarness):
    """AddonHarness with A3's shim extension, file list and beacon / chat readers."""

    def __init__(self, map: Any = None, *, files: Optional[list[str]] = None, before_load=None,
                 integrated: bool = False, **kw) -> None:
        self._integrated = integrated

        def setup(h: "BeaconHarness") -> None:
            h._run(SHIM_EXT.read_text(encoding="utf-8"), "@shim_beacon2.lua")
            if before_load is not None:
                before_load(h)

        if files is None:
            files = INTEGRATED_FILES if integrated else V12_FILES
        super().__init__(map=EMPTY_MAP if map is None else map, files=files, before_load=setup, **kw)

    # ------------------------------------------------------------ loading --
    def load_file(self, fname: str) -> None:
        if self._integrated and fname in ("Director.lua", "SelfTest.lua"):
            fn = self._loader(integrated_source(fname), "@" + fname)
            if isinstance(fn, tuple):
                fn, err = fn[0], (fn[1] if len(fn) > 1 else None)
                if fn is None:
                    raise SyntaxError(f"{fname}: {err}")
            fn("NeedleDropper", self.ns)
            return
        super().load_file(fname)

    # ------------------------------------------------------------ helpers --
    def table(self, value: Any):
        """Python dict / list -> Lua table (nested, via a literal)."""
        return self.rt.execute("return " + lua_literal(value))

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

    def set_director_active(self, active: Optional[dict]) -> None:
        """Fake the Director's active drop ({isLocal, isTest}) for the live flags."""
        if self.ns.Director is None:
            self.ns.Director = self.table({})
        self.ns.Director.active = None if active is None else self.table(active)

    def set_companion(self, spotify: Any = None, *, status: Optional[dict] = None) -> None:
        """A fake ns.Companion whose Status() reports Spotify as ``spotify``."""
        st = dict(status or {})
        if spotify is not None:
            st["spotify"] = spotify
        self.ns.Companion = self.rt.execute(
            "local st = ... return { Status = function() return st end }", self.table(st))

    def set_setting(self, key: str, value: Any) -> None:
        """Write a setting straight into the table and announce it (like the Settings panel)."""
        self.ns.settings[key] = value
        self.ns.Fire("ND_SETTING", key, value)
        self.check_errors()

    def art(self, name: str = "PlayerFrame", **opts) -> None:
        """Place fake unit frame art (physical px from the screen top-left; see shim_beacon2)."""
        self.shim.SetUnitFrameArt(name, self.table(opts))

    def trigger_registry(self, event: str) -> None:
        self.shim.TriggerRegistry(event)
        self.check_errors()

    def status(self) -> dict:
        st = self.ns.Beacon.Status()
        self.check_errors()
        return self.from_lua(st)

    def chat_status(self) -> dict:
        return self.from_lua(self.ns.ChatLink.Status())

    # ------------------------------------------------------- beacon readers --
    def pixel_factor(self) -> float:
        return 768.0 / float(self.state.physH)

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
