"""Cross-language contract: addon Protocol.lua must agree with companion protocol.py."""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))

from needledropper.models import Event, EventType, Flags  # noqa: E402
from needledropper import protocol  # noqa: E402

lua51 = pytest.importorskip("lupa.lua51")


@pytest.fixture(scope="module")
def P():
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    src = (ROOT / "addon" / "NeedleDropper" / "Protocol.lua").read_text(encoding="utf-8")
    chunk = rt.execute("return function(src) return loadstring(src, '=Protocol.lua') end")(src)
    ns = rt.table()
    chunk("NeedleDropper", ns)
    return rt, ns.Protocol


EVENTS = [
    Event(EventType.FIRE, 7, 12, 40213, Flags.LOCAL_PLAYBACK | Flags.DROP_ACTIVE, "zone:1429"),
    Event(EventType.HELLO, 0, 0, 1, Flags.NONE, ""),
    Event(EventType.CANCEL, 255, 65535, 65535, Flags(0x7F), "sub:1429:lion-s-pride-inn"),
    Event(EventType.PING, 128, 1, 2, Flags.TEST, "test"),
    Event(EventType.ARM, 1, 300, 513, Flags.IN_COMBAT | Flags.ON_TAXI, "inst:36"),
]


def lua_event(rt, ev: Event):
    return rt.table_from({"type": int(ev.type), "seq": ev.seq, "n": ev.n, "hash": ev.hash,
                          "flags": int(ev.flags), "id": ev.id})


@pytest.mark.parametrize("ev", EVENTS)
def test_cells_match(P, ev):
    rt, proto = P
    lua_cells = proto.EncodeCells(lua_event(rt, ev))
    got = [tuple(255 * int(lua_cells[i + 1][c + 1]) for c in range(3)) for i in range(len(lua_cells))]
    assert got == protocol.encode_beacon_cells(ev)
    assert protocol.decode_cells(got[len(protocol.HEADER):]) == Event(ev.type, ev.seq, ev.n, ev.hash, ev.flags)


@pytest.mark.parametrize("ev", EVENTS)
def test_chat_line_match(P, ev):
    rt, proto = P
    line = proto.FormatChatLine(lua_event(rt, ev))
    assert line == protocol.format_chat_line(ev)
    parsed = protocol.parse_chat_line("11/5 18:01:02.345  " + line)
    assert parsed is not None and parsed.key == ev.key and parsed.flags == ev.flags


def test_fletcher_and_pad(P):
    rt, proto = P
    for s in ["", "a", "NeedleDropper", "x" * 1000]:
        assert proto.Fletcher16String(s) == protocol.fletcher16(s.encode())
    assert len(proto.PadLine(3)) == 900
    assert proto.PadLineCount(50000) == protocol.pad_line_count(50000) == 56
    assert proto.HasFlag(48, 16) and not proto.HasFlag(48, 8)


def test_corruption_rejected():
    ev = EVENTS[0]
    cells = protocol.encode_beacon_cells(ev)[len(protocol.HEADER):]
    for i, c in itertools.product(range(len(cells) - 1), range(3)):  # last cell holds the pad bit only partially
        bad = list(cells)
        flipped = list(bad[i])
        flipped[c] = 255 - flipped[c]
        bad[i] = tuple(flipped)
        assert protocol.decode_cells(bad) != Event(ev.type, ev.seq, ev.n, ev.hash, ev.flags) or (i == 26)
    line = protocol.format_chat_line(ev)
    assert protocol.parse_chat_line(line.replace(" 12 ", " 13 ")) is None


def test_map_hash_stable():
    drops = [{"n": 1, "id": "zone:1429", "title": "T", "artist": "A", "durationMs": 1000}]
    assert protocol.map_hash(drops) == protocol.map_hash([dict(drops[0])])
    assert protocol.map_hash(drops) != protocol.map_hash([{**drops[0], "title": "U"}])
