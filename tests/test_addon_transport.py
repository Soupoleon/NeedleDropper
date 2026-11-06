"""Addon transports: the pixel beacon and the chat-log channel, checked against the
companion's Python decoders (cross-language contract, SPEC 3.5 / 3.6)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import AddonHarness, make_map  # noqa: E402
from needledropper import protocol  # noqa: E402
from needledropper.models import Event, EventType as ET, Flags  # noqa: E402

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
     "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 60000},
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "durationMs": 30000},
    {"id": "sub:1429:lion-s-pride-inn", "kind": "subzone", "mapID": 1429, "subzone": "Lion's Pride Inn",
     "label": "Lion's Pride Inn", "durationMs": 20000},
    {"id": "inst:36", "kind": "instance", "instanceID": 36, "label": "The Deadmines", "durationMs": 30000},
]
MAP = make_map(DROPS)
HOLD = 1.1  # > Beacon.MIN_HOLD


def ev_tuple(d: dict):
    return (int(d["type"]), d["seq"], d["n"], d["hash"], d["flags"])


def decoded_tuple(e: Event):
    return (int(e.type), e.seq, e.n, e.hash, int(e.flags))


def check_beacon_matches_last_event(h: AddonHarness):
    got = h.beacon_event()
    assert got is not None, "beacon strip did not decode"
    assert decoded_tuple(got) == ev_tuple(h.last_event())


# ---------------------------------------------------------------- beacon --

def test_beacon_cells_decode_with_python_decoder_after_each_event():
    h = AddonHarness(map=MAP, settings={"chatlog": True, "cooldownMin": 0})
    steps = [
        lambda: None,                                            # HELLO at login
        lambda: h.set_location(1429, "Northshire Valley"),       # ARM
        lambda: h.advance(11),                                   # FIRE
        lambda: (h.combat(True), h.advance(6.5)),                # CANCEL (combat)
        lambda: (h.combat(False), h.set_location(1436, "")),     # ARM zone:1436
        lambda: h.advance(11),                                   # FIRE zone:1436
        lambda: h.advance(31),                                   # HELLO (ended)
        lambda: h.slash("test"),                                 # FIRE n=0 TEST
        lambda: h.slash("stop"),                                 # CANCEL TEST
        lambda: h.slash("selftest beacon"),                      # PING
        lambda: h.set_location(1429, "Lion's Pride Inn"),        # ARM landmark (sub:)
        lambda: h.set_location(291, "", instance={"id": 36, "name": "The Deadmines"}),  # CANCEL+ARM inst
        lambda: h.advance(4),                                    # FIRE inst (IN_INSTANCE)
    ]
    seen_types = set()
    for step in steps:
        step()
        h.advance(HOLD)
        check_beacon_matches_last_event(h)
        seen_types.add(h.last_event()["type"])
    assert seen_types == {ET.HELLO, ET.ARM, ET.FIRE, ET.CANCEL, ET.PING}
    assert h.beacon_shown()
    # header cells and the companion-side encoder agree cell for cell
    last = h.as_event(h.last_event())
    assert h.beacon_cells() == protocol.encode_beacon_cells(last)


def test_beacon_frame_geometry_and_strata():
    h = AddonHarness(map=MAP)
    f = h.beacon_frame()
    assert f is not None and f.GetParent(f) is None             # no parent: survives Alt+Z
    assert f.GetFrameStrata(f) == "TOOLTIP"
    assert f.GetFrameLevel(f) >= 1000
    assert not f.IsMouseEnabled(f)
    point, rel, relPoint, x, y = f.GetPoint(f, 1)
    assert (point, relPoint, x, y) == ("TOPLEFT", "TOPLEFT", 0, 0)
    assert h.lua("return select(2, NeedleDropperBeacon:GetPoint(1)) == WorldFrame") is True
    texs = h.beacon_textures()
    assert len(texs) == 31
    cell = 4 * 768 / 1440
    for i, t in enumerate(texs):
        w, hh = t.GetSize(t)
        assert w == pytest.approx(cell) and hh == pytest.approx(cell)
        p = t["__points"]["TOPLEFT"]
        assert p[1] == "TOPLEFT" and p[4] == pytest.approx(i * cell) and p[5] == 0
    assert h.lua("for _, t in ipairs(NeedleDropperBeacon.__regions) do "
                 "if t.__points.TOPLEFT[2] ~= NeedleDropperBeacon then return false end end return true") is True
    assert f.GetWidth(f) == pytest.approx(31 * cell)
    assert h.beacon_cells()[:4] == [(255, 0, 255), (0, 255, 0), (255, 0, 255), (0, 255, 0)]


def test_beacon_relayout_on_display_and_cell_size_changes():
    h = AddonHarness(map=MAP)
    h.state.physH = 1080
    h.state.physW = 1920
    h.fire("DISPLAY_SIZE_CHANGED")
    t = h.beacon_textures()[5]
    assert t.GetWidth(t) == pytest.approx(4 * 768 / 1080)
    h.slash("set beaconCellPx 6")
    t = h.beacon_textures()[5]
    assert t.GetWidth(t) == pytest.approx(6 * 768 / 1080)
    p = t["__points"]["TOPLEFT"]
    assert p[4] == pytest.approx(5 * 6 * 768 / 1080)
    # UI scale changes do not move a parentless frame's pixels
    h.state.uiScale = 0.71
    h.fire("UI_SCALE_CHANGED")
    t = h.beacon_textures()[5]
    assert t.GetWidth(t) == pytest.approx(6 * 768 / 1080)


def test_beacon_uses_screen_size_when_pixelutil_missing():
    h = AddonHarness(map=MAP, before_load=lambda hh: hh.lua("PixelUtil = nil"), state={"physH": 1200})
    t = h.beacon_textures()[0]
    assert t.GetWidth(t) == pytest.approx(4 * 768 / 1200)


def test_beacon_hidden_when_disabled_and_shown_again():
    h = AddonHarness(map=MAP, settings={"beacon": False})
    assert not h.beacon_shown()
    h.set_location(1429, "Northshire Valley")
    assert not h.beacon_shown()
    h.slash("beacon on")
    assert h.beacon_shown()
    check_beacon_matches_last_event(h)
    h.slash("beacon off")
    assert not h.beacon_shown()


def test_selftest_beacon_forces_strip_visible_temporarily():
    h = AddonHarness(map=MAP, settings={"beacon": False})
    out = h.slash("selftest beacon")
    assert h.beacon_shown()
    ping = h.last_event()
    assert ping["type"] == ET.PING
    assert any(f"PING seq {ping['seq']}" in line for line in out)
    h.advance(61)
    assert not h.beacon_shown()


def test_beacon_holds_cancel_before_next_arm():
    h = AddonHarness(map=MAP)
    h.set_location(1429, "Northshire Valley")
    h.advance(3)
    h.set_location(1436, "")                    # CANCEL zone:1429 + ARM zone:1436 in one evaluation
    evs = h.events()[-2:]
    assert [e["type"] for e in evs] == [ET.CANCEL, ET.ARM]
    got = h.beacon_event()
    assert decoded_tuple(got) == ev_tuple(evs[0])  # CANCEL stays visible first
    h.advance(HOLD)
    assert decoded_tuple(h.beacon_event()) == ev_tuple(evs[1])


def test_seq_wraps_mod_256():
    h = AddonHarness(map=MAP, db={"lastSeq": 254})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    seqs = [e["seq"] for e in h.events()]
    assert seqs == [255, 0, 1]
    h.advance(HOLD)
    assert h.beacon_event().seq == 1


# -------------------------------------------------------------- chat log --

def run_chat_scenario(h: AddonHarness):
    h.set_location(1429, "Northshire Valley")   # ARM
    h.advance(11)                               # FIRE
    h.combat(True)
    h.advance(6.5)                              # CANCEL
    h.combat(False)
    h.slash("selftest beacon")                  # PING
    h.set_location(1436, "")                    # ARM
    h.advance(11)                               # FIRE
    h.advance(31)                               # HELLO


def test_chat_lines_parse_with_python_parser_and_padding_matches():
    h = AddonHarness(map=MAP, settings={"chatlog": True, "cooldownMin": 0})
    run_chat_scenario(h)
    events = h.events()
    lines = h.chat_lines()
    pad_count = protocol.pad_line_count(50000)
    assert pad_count == 56
    parsed = []
    i = 0
    while i < len(lines):
        line = lines[i]
        assert line.startswith("NDROP1 "), line
        ev = protocol.parse_chat_line(line)
        assert ev is not None, line
        parsed.append(ev)
        i += 1
        pads = 0
        while i < len(lines) and lines[i].startswith("NDPAD "):
            assert len(lines[i]) == 900
            assert lines[i].startswith(f"NDPAD {ev.seq} ")
            assert set(lines[i][len(f"NDPAD {ev.seq} "):]) == {"."}
            pads += 1
            i += 1
        expected = pad_count if ev.type in (ET.FIRE, ET.CANCEL, ET.PING) else 0
        assert pads == expected, (ev, pads)
    assert len(parsed) == len(events)
    for ev, src in zip(parsed, events):
        assert (int(ev.type), ev.seq, ev.n, ev.hash, int(ev.flags)) == ev_tuple(src)
        assert ev.id == protocol.sanitize_id(src["id"]) if src["id"] else ev.id == ""
    for a, b in zip(parsed, parsed[1:]):
        assert b.seq == (a.seq + 1) % 256
    assert {e.type for e in parsed} == {ET.HELLO, ET.ARM, ET.FIRE, ET.CANCEL, ET.PING}
    # every NeedleDropper line is hidden from the chat window and reached the log file
    assert all(e["hidden"] for e in h.chat_entries())
    assert h.chatlog_file() == lines
    assert h.state.loggingChat is True


def test_chat_line_id_is_sanitized():
    h = AddonHarness(map=MAP, settings={"chatlog": True, "historyMode": "fresh"})
    h.set_location(1429, "Lion's Pride Inn")
    line = h.chat_lines()[-1]
    ev = protocol.parse_chat_line(line)
    assert ev.type == ET.ARM and ev.id == "sub:1429:lion-s-pride-inn"
    assert h.last_event()["n"] == ev.n


def test_chatlog_off_by_default_sends_nothing_and_never_toggles_logging():
    h = AddonHarness(map=MAP)
    run_chat_scenario(h)
    assert h.chat_lines() == []
    assert h.from_lua(h.log.logging) == {}


def test_chatlog_slash_toggle_turns_logging_on_and_off():
    h = AddonHarness(map=MAP)
    h.slash("chatlog on")
    assert h.state.loggingChat is True and h.settings()["chatlog"] is True
    h.set_location(1429, "Northshire Valley")
    assert protocol.parse_chat_line(h.chat_lines()[-1]).type == ET.ARM
    h.slash("chatlog off")
    assert h.state.loggingChat is False
    assert h.from_lua(h.log.logging) == [True, False]
    n = len(h.chat_lines())
    h.advance(11)
    assert len(h.chat_lines()) == n


def test_chatlog_does_not_turn_off_logging_it_did_not_enable():
    h = AddonHarness(map=MAP, state={"loggingChat": True})
    h.slash("chatlog on")
    h.slash("chatlog off")
    assert h.state.loggingChat is True


def test_chatlog_padding_zero_disables_padding():
    h = AddonHarness(map=MAP, settings={"chatlog": True, "chatlogPadBytes": 0})
    run_chat_scenario(h)
    assert not any(line.startswith("NDPAD") for line in h.chat_lines())
    assert len(h.chat_lines()) == len(h.events())


def test_chat_lines_deferred_during_messaging_lockdown():
    h = AddonHarness(map=MAP, settings={"chatlog": True}, state={"chatLockdown": True})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert h.chat_lines() == []
    h.state.chatLockdown = False
    h.advance(1.1)
    lines = [protocol.parse_chat_line(x) for x in h.chat_lines() if x.startswith("NDROP1")]
    assert [e.seq for e in lines] == [e["seq"] for e in h.events()]
    assert len(h.chat_lines()) == len(h.events()) + 56


def test_chat_filter_only_hides_needledropper_lines():
    h = AddonHarness(map=MAP)
    run = h.shim.RunChatFilters
    assert run("CHAT_MSG_SYSTEM", "NDROP1 1 1 0 1 0 - 0000") is True
    assert run("CHAT_MSG_SYSTEM", "NDPAD 3 ....") is True
    assert run("CHAT_MSG_SYSTEM", "Discovered: Goldshire") is False
    assert run("CHAT_MSG_SYSTEM", "xNDROP1 1") is False


def test_chatlog_selftest_unpadded_ping_then_padding_steps():
    h = AddonHarness(map=MAP)
    out = h.slash("selftest chatlog")
    assert h.state.loggingChat is True
    lines = h.chat_lines()
    assert len(lines) == 1
    ping = protocol.parse_chat_line(lines[0])
    assert ping.type == ET.PING and ping.seq == h.last_event()["seq"]
    assert any("PING seq" in line for line in out)
    totals = []
    for step in range(8):
        h.advance(1.0)
        pads = [x for x in h.chat_lines() if x.startswith("NDPAD")]
        totals.append(sum(len(x) for x in pads))
    assert totals == sorted(totals) and len(set(totals)) == 8
    assert totals[0] >= 8192 and totals[-1] >= 65536 and totals[-1] < 65536 + 900
    assert all(x.startswith(f"NDPAD {ping.seq} ") for x in h.chat_lines()[1:])
    assert any("done" in line for line in h.prints()[-3:])
    h.advance(5)
    assert sum(len(x) for x in h.chat_lines() if x.startswith("NDPAD")) == totals[-1]


def test_chatlog_selftest_reports_logging_that_does_not_stick():
    h = AddonHarness(map=MAP, state={"loggingChatBlocked": True})
    out = h.slash("selftest chatlog")
    assert any("/chatlog" in line for line in out)
