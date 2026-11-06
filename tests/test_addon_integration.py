"""Integration: the bundled soundtracks, generated from packs/*.json by tools/gen_sets.py, load
cleanly in the addon and drive real drops (packs -> Sets\\*.lua -> Soundtracks -> Director ->
beacon v2 -> the companion's Python decoders).

v1.2 migration: v1 ran the starter pack through the companion's mapgen into Map.lua and checked
the drop number and map hash on the beacon. Map.lua is retired; the same intent is checked on the
generated set files, and the beacon carries the song itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import ROOT, AddonHarness  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402

PACKS = {"azeroth-mixtape": ROOT / "packs" / "azeroth-mixtape.json",
         "azeroth-unbound": ROOT / "packs" / "azeroth-unbound.json"}


def pack_drop_count(path: Path) -> int:
    pack = json.loads(path.read_text(encoding="utf-8"))
    return len([d for d in pack["drops"] if not d.get("disabled")])


@pytest.mark.parametrize("set_id", sorted(PACKS))
def test_bundled_set_is_accepted_without_problems(set_id):
    h = AddonHarness(db={"schema": 2, "active": ["builtin:" + set_id]})
    assert not h.call1("Sets.Problems")
    stored = h.call1("Sets.GetBuiltin", set_id)
    assert len(stored["drops"]) == pack_drop_count(PACKS[set_id])
    M = h.ns.Map
    assert M.invalid == 0 and len(M.drops) == len(stored["drops"])
    assert all(d["builtin"] is True and d["setId"] == set_id for d in (h.map_drop(i) for i in h.map_ids()))


def test_bundled_zone_drop_fires_and_beacon_decodes():
    pack = json.loads(PACKS["azeroth-mixtape"].read_text(encoding="utf-8"))
    elwynn = next(d for d in pack["drops"] if d.get("zone") in ("Elwynn Forest", 1429, "zone:1429")
                  and not d.get("subzone"))
    h = AddonHarness(settings={"historyMode": "fresh"})
    h.set_location(1429, "")
    h.advance(12)
    fires = h.events_of(ET.FIRE)
    assert [f["dropId"] for f in fires] == ["zone:1429"]
    shown = h.beacon_decoded()
    assert shown is not None and shown.type == ET.FIRE and shown.drop_id == "zone:1429"
    assert (shown.artist, shown.title) == (elwynn["artist"], elwynn["title"])
    assert shown.youtube_id == p2.youtube_id_from_link(elwynn["youtube"])
    assert shown.set_id == "azeroth-mixtape" or shown.set_id is None    # setId is the first field the fit drops
    assert shown == p2.frame_from_event(fires[0], max_bytes=p2.capacity(p2.rows_cap(3)))
