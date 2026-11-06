"""The v1 wire protocol after the v1.2 cutover.

v1.2 migration: the addon speaks only Protocol2 (DESIGN-v1.2 2.4) and Protocol.lua is deleted,
so the old live comparison of Protocol.lua against companion/protocol.py has nothing left to run
on the addon side. Its intent -- the addon's v1 frames and the companion's v1 decoder agree --
still matters for one thing: the 1.2 companion keeps decoding a 1.1 addon. The vectors below are
the exact output of the retired v1.1 Protocol.lua (EncodeCells as 0/1 channel codes and
FormatChatLine), captured from git before it was deleted, and the companion must keep reading
them. The v2 contract (Lua Protocol2 against protocol2.py) is tests/test_protocol2_contract.py.
"""
from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "addon" / "NeedleDropper"
sys.path.insert(0, str(ROOT / "companion"))

from needledropper.models import Event, EventType, Flags  # noqa: E402
from needledropper import protocol  # noqa: E402

# (type, seq, n, hash, flags, id, v1 beacon cells as RGB 0/1 codes, v1 chat line) from v1.1's Protocol.lua
V11_VECTORS = [
    (3, 7, 12, 40213, 48, "zone:1429",
     "101010101010010011100001001100000111000000000000110010011101000101010011000010000101010101110",
     "NDROP1 7 3 12 40213 48 zone:1429 c874"),
    (1, 0, 0, 1, 0, "",
     "101010101010010011100001000100000000000000000000000000000000000000010000000011101011011000000",
     "NDROP1 0 1 0 1 0 - cf96"),
    (4, 255, 65535, 65535, 127, "sub:1429:lion-s-pride-inn",
     "101010101010010011100001010011111111111111111111111111111111111111110111111101111110111000010",
     "NDROP1 255 4 65535 65535 127 sub:1429:lion-s-pride-inn 548f"),
    (5, 128, 1, 2, 64, "test",
     "101010101010010011100001010110000000000000000000000100000000000000100100000001010001001001110",
     "NDROP1 128 5 1 2 64 test bcd6"),
    (2, 1, 300, 513, 5, "inst:36",
     "101010101010010011100001001000000001000000010010110000000010000000010000010110111001100101100",
     "NDROP1 1 2 300 513 5 inst:36 479f"),
]


def cells_of(code: str) -> list[tuple[int, int, int]]:
    return [tuple(255 * int(code[i + c]) for c in range(3)) for i in range(0, len(code), 3)]


def event_of(v) -> Event:
    t, seq, n, h, flags, drop_id = v[:6]
    return Event(EventType(t), seq, n, h, Flags(flags), drop_id)


@pytest.mark.parametrize("v", V11_VECTORS, ids=lambda v: v[7].split()[2] + "-" + str(v[1]))
def test_companion_still_decodes_a_v11_addon_beacon(v):
    cells = cells_of(v[6])
    ev = event_of(v)
    assert len(cells) == protocol.TOTAL_CELLS and tuple(cells[:4]) == protocol.HEADER
    assert protocol.decode_cells(cells[len(protocol.HEADER):]) == Event(ev.type, ev.seq, ev.n, ev.hash, ev.flags)
    assert protocol.encode_beacon_cells(ev) == cells          # the companion's encoder is unchanged too


@pytest.mark.parametrize("v", V11_VECTORS, ids=lambda v: v[7].split()[2] + "-" + str(v[1]))
def test_companion_still_parses_a_v11_addon_chat_line(v):
    ev = event_of(v)
    parsed = protocol.parse_chat_line("11/5 18:01:02.345  " + v[7])
    assert parsed is not None and parsed.key == ev.key and parsed.flags == ev.flags
    assert protocol.format_chat_line(ev) == v[7]


def test_v1_fletcher_and_pad_helpers():
    assert protocol.pad_line_count(50000) == 56
    for s, want in (("", 0), ("NeedleDropper", protocol.fletcher16(b"NeedleDropper"))):
        assert protocol.fletcher16(s.encode()) == want


def test_v1_corruption_is_rejected():
    ev = event_of(V11_VECTORS[0])
    cells = protocol.encode_beacon_cells(ev)[len(protocol.HEADER):]
    for i, c in itertools.product(range(len(cells) - 1), range(3)):  # last cell holds the pad bit only partially
        bad = list(cells)
        flipped = list(bad[i])
        flipped[c] = 255 - flipped[c]
        bad[i] = tuple(flipped)
        assert protocol.decode_cells(bad) != Event(ev.type, ev.seq, ev.n, ev.hash, ev.flags) or (i == 26)
    line = protocol.format_chat_line(ev)
    assert protocol.parse_chat_line(line.replace(" 12 ", " 13 ")) is None


@pytest.mark.parametrize("t", range(6, 16))
def test_v1_decoders_reject_types_above_5(t):
    """EventType gained CONFIG = 6 for protocol v2 (DESIGN-v1.2.1 2.5): the v1 decoders still
    accept only 0..5, as beacon bytes and as an NDROP1 line, even with a valid checksum."""
    head = bytes((protocol.MAGIC, (protocol.VERSION << 4) | t, 9, 0, 1, 0, 2, 0))
    f = protocol.fletcher16(head)
    assert protocol.decode_beacon_bytes(head + bytes((f >> 8, f & 0xFF))) is None
    body = f"NDROP1 9 {t} 1 2 0 zone:1"
    assert protocol.parse_chat_line(f"{body} {protocol.fletcher16(body.encode('ascii')):04x}") is None
    ok = "NDROP1 9 5 1 2 0 zone:1"  # the same record as a PING still parses
    assert protocol.parse_chat_line(f"{ok} {protocol.fletcher16(ok.encode('ascii')):04x}") is not None


def test_the_addon_no_longer_ships_or_uses_the_v1_protocol():
    assert not (ADDON_DIR / "Protocol.lua").exists()
    offenders = []
    for path in ADDON_DIR.rglob("*.lua"):
        src = path.read_text(encoding="utf-8")
        if re.search(r"ns\.Protocol(?![0-9A-Za-z_])", src) or "NDROP1 \" .." in src:
            offenders.append(str(path.relative_to(ADDON_DIR)))
    assert offenders == []
