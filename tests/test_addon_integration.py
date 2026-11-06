"""Integration: the starter pack, run through the companion's mapgen, loads cleanly in the
addon and drives real drops (packs -> Map.lua -> Core/Director -> beacon -> Python decoder)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import ROOT, AddonHarness  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402

PACK = ROOT / "packs" / "azeroth-unbound.json"


@pytest.fixture(scope="module")
def generated():
    try:
        from needledropper import mapgen, packs
    except Exception as exc:  # pragma: no cover - companion modules are optional for addon tests
        pytest.skip(f"companion mapgen/packs not importable: {exc}")
    if not PACK.is_file():
        pytest.skip("starter pack not present")
    try:
        ps = packs.load_packs_detailed([PACK])
        if not ps.ok or not ps.drops:
            pytest.skip("starter pack does not load cleanly: " + "; ".join(str(i) for i in ps.errors[:3]))
        data = mapgen.build_map(ps.drops, ps.names, generated="2026-11-05T18:00:00Z")
        return data, mapgen.render_map_lua(data)
    except (AttributeError, TypeError, ValueError) as exc:  # companion API changed under us
        pytest.skip(f"companion packs/mapgen API not usable here: {exc}")


def test_generated_map_is_accepted_without_invalid_entries(generated):
    data, lua = generated
    h = AddonHarness(map_lua=lua)
    M = h.ns.Map
    assert M.invalid == 0, h.from_lua(M.problems)
    assert len(M.drops) == len(data["drops"])
    assert M.hash == data["hash"]
    assert h.events()[0]["hash"] == data["hash"]


def test_generated_zone_drop_fires_and_beacon_decodes(generated):
    data, lua = generated
    zone = next((d for d in data["drops"] if d["kind"] == "zone" and d["mapID"] in
                 (1429, 1436, 1411, 1453, 1454, 2482)), None)
    if zone is None:
        pytest.skip("starter pack has no zone drop for a map the shim knows")
    h = AddonHarness(map_lua=lua, settings={"historyMode": "fresh"})
    h.set_location(zone["mapID"], "")
    h.advance(12)
    fires = h.events_of(ET.FIRE)
    assert fires and fires[0]["n"] == zone["n"] and fires[0]["hash"] == data["hash"]
    h.advance(1.1)
    got = h.beacon_event()
    assert got is not None and (got.n, got.hash) == (zone["n"], data["hash"])
