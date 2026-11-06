"""Addon transports: the pixel beacon and the chat-log channel, checked against the companion's
Python decoders (cross-language contract, PROTOCOL-v2 sections 3 and 4).

v1.2 migration: the addon speaks only Protocol2 (DESIGN-v1.2 2.4). The strip is the adaptive
v2 beacon (3 px cells by default, rows x cell <= 9 px, width adapting to the frame, holds: FIRE
3 s, CANCEL / PING 1 s, then the steady state), decoded with protocol2's grid decoder; chat lines
are ``NDROP2 <base64url frame>`` parsed with protocol2.parse_chat_line. The intent of each v1 test
is kept: real play reaches both decoders, the frame geometry and strata, relayout, the beacon
switch, the self-test, holds, seq wrapping, padding, logging, lockdown and the chat filter.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import AddonHarness, make_set  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
     "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 60000},
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "durationMs": 30000},
    {"id": "sub:1429:lion-s-pride-inn", "kind": "subzone", "mapID": 1429, "subzone": "Lion's Pride Inn",
     "label": "Lion's Pride Inn", "durationMs": 20000},
    {"id": "inst:36", "kind": "instance", "instanceID": 36, "label": "The Deadmines", "durationMs": 30000},
]
SET = make_set(DROPS)
CELL_3PX = 3 * 768 / 1440                 # a 3 px cell in UI units at the shim's 2560x1440
NOMINAL = p2.capacity(p2.rows_cap(3))     # no player frame art in the way: the 9 px cap at 3 px


def make(**kw) -> AddonHarness:
    return AddonHarness(sets=[SET], **kw)


def check_beacon_shows_its_current_frame(h: AddonHarness, newest: bool = True) -> dict:
    """The strip decodes (companion grid decoder) to exactly the frame the beacon says it shows.
    newest: that frame belongs to the newest event (a synthesized steady frame reuses its seq);
    otherwise to one of the last three (a transient may still hold the screen: FIRE 3 s, CANCEL
    and PING 1 s, first in first out)."""
    st = h.beacon_status()
    cur = st["current"]
    got = h.beacon_decoded()
    assert got is not None, "beacon strip did not decode"
    assert got == p2.frame_from_event(cur, max_bytes=NOMINAL)
    if newest:
        # a PING is a transient only: afterwards the steady frame of the event before it returns
        steady = [e for e in h.events() if e["type"] != ET.PING][-1]
        assert got.seq == steady["seq"]
    else:
        assert got.seq in [e["seq"] for e in h.events()[-3:]]
    return cur


# ---------------------------------------------------------------- beacon --

def test_beacon_cells_decode_with_python_decoder_after_each_event():
    h = make(settings={"chatlog": True, "cooldownMin": 0, "historyMode": "fresh"})
    steps = [
        lambda: None,                                            # HELLO at login
        lambda: h.set_location(1429, "Northshire Valley"),       # ARM (full song)
        lambda: h.advance(11),                                   # FIRE
        lambda: (h.combat(True), h.advance(6.5)),                # CANCEL (combat)
        lambda: (h.combat(False), h.set_location(1436, "")),     # ARM zone:1436
        lambda: h.advance(11),                                   # FIRE zone:1436
        lambda: h.advance(31),                                   # HELLO (ended)
        lambda: h.slash("test"),                                 # FIRE test (no song)
        lambda: h.slash("stop"),                                 # CANCEL TEST
        lambda: (h.advance(1.1), h.call("SelfTest.Signal")),     # PING (Settings: Send a test signal)
        lambda: h.set_location(1429, "Lion's Pride Inn"),        # ARM landmark (sub:)
        lambda: h.set_location(291, "", instance={"id": 36, "name": "The Deadmines"}),  # CANCEL + ARM inst
        lambda: h.advance(4),                                    # FIRE inst (IN_INSTANCE)
    ]
    drawn_types = set()
    for step in steps:
        step()
        cur = check_beacon_shows_its_current_frame(h, newest=False)
        drawn_types.add(cur["type"])
        h.advance(3.1)                                           # every hold is over
        check_beacon_shows_its_current_frame(h)
    assert drawn_types == {ET.HELLO, ET.ARM, ET.FIRE, ET.CANCEL, ET.PING}
    assert h.beacon_shown()
    # the drawn strip and the companion-side encoder agree cell for cell
    cur = h.beacon_status()["current"]
    grid = p2.encode_event_cells(cur, p2.Geometry.beacon(cell_px=3, max_rows=3))
    assert h.beacon_grid() == grid.as_rows()


def test_beacon_frame_geometry_and_strata():
    h = make()
    f = h.beacon_frame()
    assert f is not None and f.GetParent(f) is None             # no parent: survives Alt+Z
    assert f.GetFrameStrata(f) == "TOOLTIP"
    assert f.GetFrameLevel(f) >= 1000
    assert not f.IsMouseEnabled(f)
    point, rel, relPoint, x, y = f.GetPoint(f, 1)
    assert (point, relPoint, x, y) == ("TOPLEFT", "TOPLEFT", 0, 0)
    assert h.lua("return select(2, NeedleDropperBeacon:GetPoint(1)) == WorldFrame") is True
    texs = h.shown_textures()
    assert len(texs) == 32                                      # the idle HELLO: 1 row of 32 cells, 96 x 3 px
    for t in texs:
        w, hh = t.GetSize(t)
        assert w == pytest.approx(CELL_3PX) and hh == pytest.approx(CELL_3PX)
    assert h.lua("for _, t in ipairs(NeedleDropperBeacon.__regions) do "
                 "if t.__points.TOPLEFT[2] ~= NeedleDropperBeacon then return false end end return true") is True
    assert f.GetWidth(f) == pytest.approx(32 * CELL_3PX) and f.GetHeight(f) == pytest.approx(CELL_3PX)
    st = h.beacon_status()
    assert (st["size"], st["rows"], st["cols"], st["cellPx"]) == ("96x3", 1, 32, 3)
    # v2 header: the first row starts with the whitened-header magenta / green pattern
    assert h.beacon_grid()[0][:2] == [(255, 0, 255), (0, 255, 0)]


def test_beacon_relayout_on_display_and_cell_size_changes():
    h = make()
    h.state.physH = 1080
    h.state.physW = 1920
    h.fire("DISPLAY_SIZE_CHANGED")
    t = h.shown_textures()[5]
    assert t.GetWidth(t) == pytest.approx(3 * 768 / 1080)
    h.slash("set beaconCellPx 4")
    t = h.shown_textures()[5]
    assert t.GetWidth(t) == pytest.approx(4 * 768 / 1080)
    assert h.beacon_cell_px() == pytest.approx(4)
    # UI scale changes do not move a parentless frame's pixels
    h.state.uiScale = 0.71
    h.fire("UI_SCALE_CHANGED")
    t = h.shown_textures()[5]
    assert t.GetWidth(t) == pytest.approx(4 * 768 / 1080)
    assert h.beacon_decoded() is not None


def test_beacon_uses_screen_size_when_pixelutil_missing():
    h = make(before_load=lambda hh: hh.lua("PixelUtil = nil"), state={"physH": 1200})
    t = h.shown_textures()[0]
    assert t.GetWidth(t) == pytest.approx(3 * 768 / 1200)


def test_beacon_hidden_when_disabled_and_shown_again():
    h = make(settings={"beacon": False})
    assert not h.beacon_shown()
    h.set_location(1429, "Northshire Valley")
    assert not h.beacon_shown()
    h.slash("beacon on")
    assert h.beacon_shown()
    check_beacon_shows_its_current_frame(h)
    h.slash("beacon off")
    assert not h.beacon_shown()


def test_selftest_beacon_forces_strip_visible_temporarily():
    h = make(settings={"beacon": False})
    out = h.slash("selftest beacon")
    assert h.beacon_shown()
    ping = h.last_event()
    assert ping["type"] == ET.PING and ping["flags"] & int(Flags.TEST)
    assert any(f"PING seq {ping['seq']}" in line for line in out)
    h.advance(11.5)                              # the PING (1 s) and the largest strip (10 s)
    assert h.beacon_shown()
    h.advance(1)
    assert not h.beacon_shown()


def test_beacon_holds_cancel_before_next_arm():
    h = make(settings={"historyMode": "fresh"})
    h.set_location(1429, "Northshire Valley")
    h.advance(3)
    h.set_location(1436, "")                    # CANCEL zone:1429 + ARM zone:1436 in one evaluation
    evs = h.events()[-2:]
    assert [e["type"] for e in evs] == [ET.CANCEL, ET.ARM]
    got = h.beacon_decoded()
    assert (got.type, got.seq, got.drop_id) == (ET.CANCEL, evs[0]["seq"], "zone:1429")  # CANCEL first
    h.advance(1.1)
    got = h.beacon_decoded()
    assert (got.type, got.seq, got.drop_id) == (ET.ARM, evs[1]["seq"], "zone:1436")


def test_seq_wraps_mod_256():
    h = make(db={"lastSeq": 254}, settings={"historyMode": "fresh"})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    seqs = [e["seq"] for e in h.events()]
    assert seqs == [255, 0, 1]
    assert h.beacon_decoded().seq == 1


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
    h = make(settings={"chatlog": True, "cooldownMin": 0, "historyMode": "fresh"})
    run_chat_scenario(h)
    events = h.events()
    lines = h.chat_lines()
    pad_count = p2.pad_line_count(50000)
    assert pad_count == 56
    parsed = []
    i = 0
    while i < len(lines):
        line = lines[i]
        assert line.startswith("NDROP2 "), line
        fr = p2.parse_chat_line(line)
        assert fr is not None, line
        parsed.append(fr)
        i += 1
        pads = 0
        while i < len(lines) and lines[i].startswith("NDPAD "):
            assert len(lines[i]) == 900
            assert lines[i].startswith(f"NDPAD {fr.seq} ")
            assert set(lines[i][len(f"NDPAD {fr.seq} "):]) == {"."}
            pads += 1
            i += 1
        expected = pad_count if fr.type in (ET.FIRE, ET.CANCEL, ET.PING) else 0
        assert pads == expected, (fr, pads)
    assert len(parsed) == len(events)
    for fr, src in zip(parsed, events):
        # the chat line carries the event's whole frame (every field, the 262-byte budget)
        assert fr == p2.frame_from_event(src)
    for a, b in zip(parsed, parsed[1:]):
        assert b.seq == (a.seq + 1) % 256
    assert {f.type for f in parsed} == {ET.HELLO, ET.ARM, ET.FIRE, ET.CANCEL, ET.PING}
    fire = next(f for f in parsed if f.type == ET.FIRE)
    assert (fire.drop_id, fire.artist, fire.title, fire.length_ms) == ("zone:1429", "Low Roar", "I'll Keep Coming", 60000)
    # every NeedleDropper line is hidden from the chat window and reached the log file
    assert all(e["hidden"] for e in h.chat_entries())
    assert h.chatlog_file() == lines
    assert h.state.loggingChat is True


def test_chat_line_id_is_sanitized():
    h = make(settings={"chatlog": True, "historyMode": "fresh"})
    h.set_location(1429, "Lion's Pride Inn")
    line = h.chat_lines()[-1]
    fr = p2.parse_chat_line(line)
    assert fr.type == ET.ARM and fr.drop_id == "sub:1429:lion-s-pride-inn"
    assert fr.place_key == h.last_event()["placeKey"] == p2.place_key_for("sub:1429:lion-s-pride-inn")


def test_chatlog_off_by_default_sends_nothing_and_never_toggles_logging():
    h = make()
    run_chat_scenario(h)
    assert h.chat_lines() == []
    assert h.from_lua(h.log.logging) == {}


def test_chatlog_slash_toggle_turns_logging_on_and_off():
    h = make(settings={"historyMode": "fresh"})
    h.slash("chatlog on")
    assert h.state.loggingChat is True and h.settings()["chatlog"] is True
    h.set_location(1429, "Northshire Valley")
    assert p2.parse_chat_line(h.chat_lines()[-1]).type == ET.ARM
    h.slash("chatlog off")
    assert h.state.loggingChat is False
    assert h.from_lua(h.log.logging) == [True, False]
    n = len(h.chat_lines())
    h.advance(11)
    assert len(h.chat_lines()) == n


def test_chatlog_does_not_turn_off_logging_it_did_not_enable():
    h = make(state={"loggingChat": True})
    h.slash("chatlog on")
    h.slash("chatlog off")
    assert h.state.loggingChat is True


def test_chatlog_padding_zero_disables_padding():
    h = make(settings={"chatlog": True, "chatlogPadBytes": 0, "historyMode": "fresh"})
    run_chat_scenario(h)
    assert not any(line.startswith("NDPAD") for line in h.chat_lines())
    assert len(h.chat_lines()) == len(h.events())


def test_chat_lines_deferred_during_messaging_lockdown():
    h = make(settings={"chatlog": True, "historyMode": "fresh"}, state={"chatLockdown": True})
    h.set_location(1429, "Northshire Valley")
    h.advance(9)
    assert h.chat_lines() == []
    h.state.chatLockdown = False
    h.advance(1.1)
    frames = [p2.parse_chat_line(x) for x in h.chat_lines() if x.startswith("NDROP2")]
    assert [f.seq for f in frames] == [e["seq"] for e in h.events()]
    assert len(h.chat_lines()) == len(h.events()) + 56 * len([e for e in h.events() if e["type"] == ET.FIRE])


def test_a_fire_line_that_waited_too_long_is_dropped():
    h = make(settings={"chatlog": True, "historyMode": "fresh"}, state={"chatLockdown": True})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)                                  # FIRE while chat is locked down
    h.advance(12)                                  # still locked: the FIRE line is now stale
    h.state.chatLockdown = False
    h.advance(1.1)
    types = [p2.parse_chat_line(x).type for x in h.chat_lines() if x.startswith("NDROP2")]
    assert ET.FIRE not in types and ET.ARM in types and h.chat_status()["stale"] == 1


def test_chat_filter_only_hides_needledropper_lines():
    h = make()
    run = h.shim.RunChatFilters
    assert run("CHAT_MSG_SYSTEM", "NDROP2 TiEAAAAMbw") is True
    assert run("CHAT_MSG_SYSTEM", "NDROP1 1 1 0 1 0 - 0000") is True   # v1.1 lines too
    assert run("CHAT_MSG_SYSTEM", "NDPAD 3 ....") is True
    assert run("CHAT_MSG_SYSTEM", "Discovered: Goldshire") is False
    assert run("CHAT_MSG_SYSTEM", "xNDROP2 1") is False


def test_chatlog_selftest_unpadded_ping_then_padding_steps():
    h = make()
    out = h.slash("selftest chatlog")
    assert h.state.loggingChat is True
    lines = h.chat_lines()
    assert len(lines) == 1
    ping = p2.parse_chat_line(lines[0])
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
    h = make(state={"loggingChatBlocked": True})
    out = h.slash("selftest chatlog")
    assert any("/chatlog" in line for line in out)
