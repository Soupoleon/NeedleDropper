"""Wire protocol shared with the addon's Protocol.lua (docs/SPEC.md 3.4-3.6).

Cross-component contract: tests/test_protocol_contract.py checks that the Lua
and Python implementations agree bit-for-bit.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional, Sequence

from .models import Event, EventType, Flags

MAGIC = 0x4E
VERSION = 1
PAYLOAD_LEN = 10
PAYLOAD_CELLS = 27  # 80 bits + 1 pad bit = 81 bits = 27 cells * 3 bits
HEADER = ((255, 0, 255), (0, 255, 0), (255, 0, 255), (0, 255, 0))  # magenta, green, magenta, green
TOTAL_CELLS = len(HEADER) + PAYLOAD_CELLS  # 31

CHAT_PREFIX = "NDROP1"
PAD_PREFIX = "NDPAD"
#: ASCII only (CHAT-UNICODE-RAISE): ``\d`` / ``\S`` would also match other scripts' digits and letters,
#: and the record body is then encoded as ASCII for its checksum.
CHAT_LINE_RE = re.compile(r"NDROP1 ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([!-~]+) ([0-9a-f]{4})")
_ID_BAD = re.compile(r"[^A-Za-z0-9:_.\-]")


def fletcher16(data: bytes | bytearray | Iterable[int]) -> int:
    s1 = 0
    s2 = 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return s2 * 256 + s1


def map_hash(drops: Sequence[dict]) -> int:
    """SPEC 3.4 map hash over the drop list (dicts with n/id/title/artist/durationMs)."""
    canon = [[d["n"], d["id"], d.get("title", ""), d.get("artist", ""), int(d.get("durationMs", 0) or 0)]
             for d in drops]
    h = fletcher16(json.dumps(canon, separators=(",", ":"), ensure_ascii=True).encode("ascii"))
    return h or 1


# ---------------------------------------------------------------- beacon ----

def encode_beacon_bytes(ev: Event) -> bytes:
    if not (0 <= ev.seq <= 255 and 0 <= ev.n <= 0xFFFF and 0 <= ev.hash <= 0xFFFF):
        raise ValueError(f"event field out of range: {ev}")
    head = bytes([
        MAGIC,
        (VERSION << 4) | (int(ev.type) & 0x0F),
        ev.seq & 0xFF,
        (ev.n >> 8) & 0xFF, ev.n & 0xFF,
        (ev.hash >> 8) & 0xFF, ev.hash & 0xFF,
        int(ev.flags) & 0xFF,
    ])
    f = fletcher16(head)
    return head + bytes([(f >> 8) & 0xFF, f & 0xFF])


def bytes_to_bits(data: bytes) -> list[int]:
    bits: list[int] = []
    for b in data:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    return bits


def encode_beacon_cells(ev: Event) -> list[tuple[int, int, int]]:
    """Full strip (header + payload) as RGB tuples, left to right."""
    bits = bytes_to_bits(encode_beacon_bytes(ev)) + [0]
    assert len(bits) == PAYLOAD_CELLS * 3
    cells = [tuple(255 if bits[i * 3 + c] else 0 for c in range(3)) for i in range(PAYLOAD_CELLS)]
    return list(HEADER) + cells  # type: ignore[list-item]


def decode_beacon_bytes(data: bytes) -> Optional[Event]:
    """Validate a 10-byte payload; return the Event or None."""
    if len(data) != PAYLOAD_LEN or data[0] != MAGIC or (data[1] >> 4) != VERSION:
        return None
    if fletcher16(data[:8]) != (data[8] << 8 | data[9]):
        return None
    t = data[1] & 0x0F
    try:
        etype = EventType(t)
    except ValueError:
        return None
    return Event(type=etype, seq=data[2], n=(data[3] << 8) | data[4], hash=(data[5] << 8) | data[6],
                 flags=Flags(data[7] & 0x7F))


def decode_beacon_bits(bits: Sequence[int]) -> Optional[Event]:
    if len(bits) < PAYLOAD_LEN * 8:
        return None
    out = bytearray()
    for i in range(PAYLOAD_LEN):
        v = 0
        for b in bits[i * 8:(i + 1) * 8]:
            v = (v << 1) | (1 if b else 0)
        out.append(v)
    return decode_beacon_bytes(bytes(out))


def decode_cells(cells: Sequence[tuple[int, int, int]], threshold: int = 128) -> Optional[Event]:
    """Decode the 27 payload cells (header already stripped) sampled as RGB."""
    if len(cells) < PAYLOAD_CELLS:
        return None
    bits: list[int] = []
    for r, g, b in cells[:PAYLOAD_CELLS]:
        bits.extend((1 if r >= threshold else 0, 1 if g >= threshold else 0, 1 if b >= threshold else 0))
    return decode_beacon_bits(bits)


# -------------------------------------------------------------- chat log ----

def sanitize_id(drop_id: str) -> str:
    return _ID_BAD.sub("_", drop_id) or "_"


def format_chat_line(ev: Event) -> str:
    body = f"{CHAT_PREFIX} {ev.seq} {int(ev.type)} {ev.n} {ev.hash} {int(ev.flags)} {sanitize_id(ev.id or '-')}"
    return f"{body} {fletcher16(body.encode('ascii')):04x}"


def parse_chat_line(line: str) -> Optional[Event]:
    """Find and validate an NDROP1 record anywhere in a chat-log line."""
    m = CHAT_LINE_RE.search(line)
    if not m:
        return None
    seq, t, n, h, fl, did, crc = m.groups()
    body = f"{CHAT_PREFIX} {seq} {t} {n} {h} {fl} {did}"
    if not body.isascii():  # never true with the ASCII pattern; kept so this can never raise
        return None
    if fletcher16(body.encode("ascii")) != int(crc, 16):
        return None
    try:
        etype = EventType(int(t))
        seq_i, n_i, h_i, fl_i = int(seq), int(n), int(h), int(fl)
    except ValueError:
        return None
    if not (0 <= seq_i <= 255 and 0 <= n_i <= 0xFFFF and 0 <= h_i <= 0xFFFF and 0 <= fl_i <= 0xFF):
        return None
    return Event(type=etype, seq=seq_i, n=n_i, hash=h_i, flags=Flags(fl_i & 0x7F), id="" if did == "-" else did)


def pad_line_count(pad_bytes: int, line_len: int = 900) -> int:
    return 0 if pad_bytes <= 0 else -(-pad_bytes // line_len)
