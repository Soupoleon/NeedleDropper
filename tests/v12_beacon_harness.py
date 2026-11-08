"""Harness for the v1.2 protocol cutover (A3): Protocol2 + Emit + Beacon v2 + ChatLink v2.

A thin wrapper over the unified :class:`addon_harness.AddonHarness`, which already loads
``tests/lua/shim_beacon2.lua`` (EventRegistry, the Chat addon restriction, fake PlayerFrame /
TargetFrame art for the clearance guard) and has the beacon readers (the drawn grid, the decoded
frame, a synthetic BGRA capture for :func:`needledropper.transport.beacon2.locate_and_decode`).

* default: only A3's modules and the core services they need (``V12_FILES``), for unit tests
  of the transports without the Director driving them;
* ``integrated=True``: the whole addon in the real TOC order (CONTRACTS-v1.2 2.1).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from addon_harness import ADDON_DIR, AddonHarness, lua_literal, toc_files  # noqa: E402,F401
from needledropper import protocol2 as p2  # noqa: E402,F401

#: Files the default harness loads, in final-TOC order (CONTRACTS-v1.2 2.1), restricted to A3's
#: modules and the core services they need.
V12_FILES = ["Locale.lua", "Protocol2.lua", "Core.lua", "Emit.lua", "Beacon.lua", "ChatLink.lua"]

#: The whole addon in TOC order, for the integration check.
INTEGRATED_FILES = toc_files()


def integrated_source(fname: str) -> str:
    """Source of ``fname`` as it ships (the Director / SelfTest cutover to Protocol2 is done)."""
    return (ADDON_DIR / fname).read_text(encoding="utf-8")


class BeaconHarness(AddonHarness):
    """AddonHarness with A3's file list (see the module docstring)."""

    def __init__(self, map: Any = None, *, files: Optional[list[str]] = None, before_load=None,
                 integrated: bool = False, **kw) -> None:
        self._integrated = integrated
        if files is None:
            files = INTEGRATED_FILES if integrated else V12_FILES
        super().__init__(files=files, before_load=before_load, **kw)

    # ------------------------------------------------------------ helpers --
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

    def status(self) -> dict:
        st = self.ns.Beacon.Status()
        self.check_errors()
        return self.from_lua(st)

    # ------------------------------------------------------- beacon readers --
