"""The protocol cutover with the real v1.1 Director, Location, Toast, Options and SelfTest.

Loads the addon in final-TOC order without Protocol.lua (v1), after applying in memory exactly the
patch the integrator is asked to make (tests/v12_beacon_harness.py ``integrated_source``):

* Director.lua: delete ``ns.Events`` / ``ns.LastEvent``, ``addFlag``, ``D.ComputeFlags`` and the v1
  ``ns.Emit`` (Emit.lua provides them), and read ``EV`` / ``FLAG`` / ``HasFlag`` / ``EV_NAME`` from
  ``ns.Protocol2``;
* SelfTest.lua: ``local P = ns.Protocol2`` (it uses ``EV.PING`` and ``PadLine``).

Then real play -- login, arriving in a zone, the drop firing, combat, the self-tests -- must reach
the companion's decoders as v2 frames over both transports.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import make_map  # noqa: E402
from v12_beacon_harness import BeaconHarness, integrated_source  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402
from needledropper.transport import beacon2  # noqa: E402

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "title": "I'll Keep Coming",
     "artist": "Low Roar", "durationMs": 60000},
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "title": "Old Pine",
     "artist": "Ben Howard", "durationMs": 30000},
]


def make(**settings) -> BeaconHarness:
    try:
        integrated_source("Director.lua")
        integrated_source("SelfTest.lua")
    except LookupError as exc:  # the integrator changed these files: this check has done its job
        pytest.skip(str(exc))
    s = {"chatlog": True, "cooldownMin": 0, "beaconCellPx": 3}
    s.update(settings)
    return BeaconHarness(map=make_map(DROPS), integrated=True, settings=s)


def test_patch_removes_every_v1_emit_piece():
    src = integrated_source("Director.lua")
    assert "function ns.Emit" not in src and "D.ComputeFlags" not in src and "ns.Protocol\n" not in src
    assert "local P = ns.Protocol2" in src and "ns.Emit(EV.ARM, d)" in src
    assert "local P = ns.Protocol2" in integrated_source("SelfTest.lua")


def test_real_play_reaches_both_decoders_as_v2():
    h = make()
    assert h.ns.Protocol is None and h.ns.Emit is not None
    hello = h.last_event()
    assert hello["type"] == ET.HELLO and h.beacon_decoded() == p2.frame_from_event(hello)
    h.set_location(1429, "Northshire Valley")
    arm = h.last_event()
    assert arm["type"] == ET.ARM and arm["dropId"] == "zone:1429" and arm["label"] == "Elwynn Forest"
    h.advance(11)
    fire = h.last_event()
    assert fire["type"] == ET.FIRE and fire["flags"] & Flags.DROP_ACTIVE
    shown = h.beacon_decoded()
    assert shown == p2.frame_from_event(fire, max_bytes=p2.capacity(3))
    assert (shown.artist, shown.title, shown.length_ms) == ("Low Roar", "I'll Keep Coming", 60000)
    res = beacon2.locate_and_decode(*h.beacon_bgra(scale=1.25, width=640, height=60, seed=3))
    assert res is not None and res.frame == shown
    h.advance(3.1)
    after = h.beacon_decoded()
    assert after.type == ET.HELLO and after.has(Flags.DROP_ACTIVE) and after.seq == fire["seq"]
    h.combat(True)
    h.advance(6.5)  # long combat: the Director cancels
    cancel = h.last_event()
    assert cancel["type"] == ET.CANCEL
    idle = h.beacon_decoded()  # the CANCEL's second is over: HELLO with its seq, nothing playing
    assert (idle.type, idle.seq) == (ET.HELLO, cancel["seq"]) and not idle.has(Flags.DROP_ACTIVE)
    h.combat(False)
    frames = [p2.parse_chat_line(x) for x in h.chat_lines() if x.startswith("NDROP2 ")]
    assert frames == [p2.frame_from_event(e) for e in h.events()]
    assert {f.type for f in frames} >= {ET.HELLO, ET.ARM, ET.FIRE, ET.CANCEL}
    assert not any(x.startswith("NDROP1") for x in h.chat_lines())


def test_status_and_selftests_work_on_v2():
    h = make(beacon=False)
    out = h.slash("selftest beacon")
    ping = h.last_event()
    assert ping["type"] == ET.PING and any(f"PING seq {ping['seq']}" in line for line in out)
    assert h.beacon_shown() and h.beacon_decoded() == p2.frame_from_event(ping)
    out = h.slash("status")
    assert any("transports: beacon off (3 px cells, last PING" in line for line in out), out
    h.slash("test")
    test_fire = h.last_event()
    assert test_fire["type"] == ET.FIRE and test_fire["flags"] & Flags.TEST and test_fire["dropId"] == "test"
    assert p2.frame_from_event(test_fire).query is None  # the test drop carries no song
    h.slash("selftest chatlog")
    pings = [p2.parse_chat_line(x) for x in h.chat_lines() if x.startswith("NDROP2 ")]
    assert pings[-1].type == ET.PING
    h.advance(2.0)
    assert any(x.startswith(f"NDPAD {pings[-1].seq} ") for x in h.chat_lines())
