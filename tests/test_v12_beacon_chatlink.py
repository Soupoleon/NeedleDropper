"""ChatLink v2 (DESIGN-v1.2 2.1, docs/PROTOCOL-v2.md section 3): "NDROP2 <base64url frame>" lines
through SendSystemMessage, NDPAD padding after FIRE / CANCEL / PING, the CHAT_MSG_SYSTEM filter,
deferral while chat is locked down or the Chat addon restriction is active (a FIRE line older than
10 s is dropped), and chat logging only when settings.chatlog is on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v12_beacon_harness import BeaconHarness  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

DROP = {"id": "sub:1429:lion-s-pride-inn", "kind": "subzone", "mapID": 1429, "subzone": "Lion's Pride Inn",
        "label": "Lion's Pride Inn", "title": "I'll Keep Coming", "artist": "Low Roar",
        "spotify": "4uLU6hMCjMI75M1A2tKUQC", "youtube": "dQw4w9WgXcQ", "startMs": 12000, "durationMs": 95000,
        "volume": 80, "setId": "my-drops"}
ZONE = {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "title": "Song", "artist": "Band"}


def make(**settings) -> BeaconHarness:
    s = {"chatlog": True, "beaconCellPx": 3}
    s.update(settings)
    return BeaconHarness(settings=s)


def records(h: BeaconHarness) -> list[str]:
    return [x for x in h.chat_lines() if x.startswith("NDROP2 ")]


def run(h: BeaconHarness) -> list[dict]:
    evs = [h.emit(ET.HELLO), h.emit(ET.ARM, DROP), h.emit(ET.FIRE, DROP), h.emit(ET.CANCEL, DROP),
           h.emit(ET.PING, None, int(Flags.TEST)), h.emit(ET.ARM, ZONE)]
    return evs


def test_lines_parse_strictly_and_carry_every_field():
    h = make()
    evs = run(h)
    lines = h.chat_lines()
    parsed, i = [], 0
    while i < len(lines):
        line = lines[i]
        assert line.startswith("NDROP2 ") and " " not in line[7:], line
        frame = p2.parse_chat_line(line)  # strict: the whole system message
        assert frame is not None
        assert p2.parse_chat_line("11/5 18:01:02.345  " + line) == frame  # as WoWChatLog.txt writes it
        assert p2.parse_chat_line(f"11/5 18:01:02.345  [Bob] says: {line}") is None
        parsed.append(frame)
        i += 1
        pads = 0
        while i < len(lines) and lines[i].startswith("NDPAD "):
            assert lines[i] == p2.pad_line(frame.seq) and len(lines[i]) == 900
            pads += 1
            i += 1
        assert pads == (p2.pad_line_count(50000) if frame.type in (ET.FIRE, ET.CANCEL, ET.PING) else 0), frame
    assert parsed == [p2.frame_from_event(e) for e in evs]  # the full frames, not the beacon's fitted ones
    fire = parsed[2]
    assert (fire.set_id, fire.label, fire.query) == ("my-drops", "Lion's Pride Inn", "Low Roar\x1fI'll Keep Coming")
    assert [f.seq for f in parsed] == [e["seq"] for e in evs] == list(range(1, 7))
    assert all(e["hidden"] for e in h.chat_entries())  # every line is hidden from the chat window
    assert h.chatlog_file() == lines and h.state.loggingChat is True


def test_chat_line_and_beacon_share_the_dedupe_key_when_the_beacon_is_fitted():
    h = make(beaconCellPx=4)
    ev = h.emit(ET.FIRE, DROP)
    chat = p2.parse_chat_line(records(h)[-1])
    shown = h.beacon_decoded()
    # 61 / 143 B: 59 / 141 before the 4-byte frame check (F1)
    assert len(p2.encode_frame(shown)) == 61 < len(p2.encode_frame(chat)) == 143
    assert chat.key == shown.key == (ev["seq"], int(ET.FIRE), p2.place_key_for(DROP["id"]), ev["flags"])


def test_no_padding_when_disabled_or_asked():
    h = make(chatlogPadBytes=0)
    run(h)
    assert not any(x.startswith("NDPAD") for x in h.chat_lines())
    h = make()
    h.emit(ET.PING, None, 0, {"noPad": True})
    assert len(h.chat_lines()) == 1


def test_chatlog_off_sends_nothing_and_never_touches_logging():
    h = make(chatlog=False)
    run(h)
    assert h.chat_lines() == [] and h.from_lua(h.log.logging) == {}
    ev = h.emit(ET.PING, None, int(Flags.TEST), {"forceChat": True, "noPad": True})  # the chat-log self-test
    assert [p2.parse_chat_line(x) for x in h.chat_lines()] == [p2.frame_from_event(ev)]
    assert h.from_lua(h.log.logging) == {}  # forcing a line never turns logging on by itself


def test_logging_follows_the_setting_only():
    h = make(chatlog=False)
    h.set_setting("chatlog", True)
    assert h.state.loggingChat is True and h.from_lua(h.log.logging) == [True]
    h.set_setting("chatlog", False)
    assert h.state.loggingChat is False and h.from_lua(h.log.logging) == [True, False]
    h2 = BeaconHarness(settings={"chatlog": False}, state={"loggingChat": True})
    h2.set_setting("chatlog", True)
    h2.set_setting("chatlog", False)
    assert h2.state.loggingChat is True  # it was on before NeedleDropper: left alone


@pytest.mark.parametrize("restriction", ["chatLockdown", "chatRestricted"])
def test_lines_wait_while_chat_is_restricted(restriction):
    h = make()
    h.state[restriction] = True
    evs = [h.emit(ET.ARM, DROP), h.emit(ET.CANCEL, DROP), h.emit(ET.HELLO)]
    h.advance(5)
    assert h.chat_lines() == [] and h.chat_status()["queued"] == 3 and h.chat_status()["lockdown"] is True
    h.state[restriction] = False
    h.advance(1.1)
    assert [p2.parse_chat_line(x) for x in records(h)] == [p2.frame_from_event(e) for e in evs]
    assert len(h.chat_lines()) == 3 + p2.pad_line_count(50000)
    assert h.chat_status()["queued"] == 0


def test_a_fire_line_older_than_10_seconds_is_dropped_with_its_padding():
    h = make()
    h.state.chatLockdown = True
    old_fire = h.emit(ET.FIRE, DROP)
    h.advance(6)
    cancel = h.emit(ET.CANCEL, DROP)
    young_fire = h.emit(ET.FIRE, ZONE)
    h.advance(4.5)                   # old FIRE waited 10.5 s, the others 4.5 s
    h.state.chatLockdown = False
    h.advance(1.0)
    got = [p2.parse_chat_line(x) for x in records(h)]
    assert got == [p2.frame_from_event(cancel), p2.frame_from_event(young_fire)]
    assert old_fire["seq"] not in [f.seq for f in got]
    assert len(h.chat_lines()) == 2 + 2 * p2.pad_line_count(50000)
    assert all(x == p2.pad_line(cancel["seq"]) or x == p2.pad_line(young_fire["seq"]) or x.startswith("NDROP2")
               for x in h.chat_lines())
    assert h.chat_status()["stale"] == 1


def test_a_cancel_waits_as_long_as_it_must():
    h = make()
    h.state.chatRestricted = True
    cancel = h.emit(ET.CANCEL, DROP)
    h.advance(60)
    h.state.chatRestricted = False
    h.advance(1.1)
    assert [p2.parse_chat_line(x) for x in records(h)] == [p2.frame_from_event(cancel)]


def test_filter_hides_only_needledropper_lines_and_never_touches_secrets():
    h = make()
    run_filters = h.shim.RunChatFilters
    hidden = ["NDROP2 TiEBAAAPcA", "NDROP1 1 1 0 1 0 - 0000", "NDPAD 3 ....", p2.pad_line(7)]
    shown = ["Discovered: Goldshire", "xNDROP2 TiEB", "NDROP2", "NDROP3 x", "ndrop2 TiEB", "NDPADDING", ""]
    for msg in hidden:
        assert run_filters("CHAT_MSG_SYSTEM", msg) is True, msg
    for msg in shown:
        assert run_filters("CHAT_MSG_SYSTEM", msg) is False, msg
    secret = h.shim.MakeSecret("chat message")
    assert run_filters("CHAT_MSG_SYSTEM", secret) is False
    assert h.from_lua(h.log.secretTouches) == {}
    assert h.ns.ChatLink.Filter(None, "CHAT_MSG_SYSTEM", 42) is False


def test_status_reports_the_queue():
    h = make()
    h.state.chatLockdown = True
    h.emit(ET.FIRE, DROP)
    st = h.chat_status()
    assert (st["queued"], st["queuedLines"], st["lockdown"], st["enabled"]) == (1, 1 + 56, True, True)
    h.state.chatLockdown = False
    h.advance(1.1)
    st = h.chat_status()
    assert (st["queued"], st["sent"], st["stale"], st["dropped"]) == (0, 57, 0, 0)
