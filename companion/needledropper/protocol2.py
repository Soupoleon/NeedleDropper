"""Wire protocol v2: frames that carry the song identity (docs/PROTOCOL-v2.md).

Cross-component contract with the addon's ``Protocol2.lua``:
``tests/test_protocol2_contract.py`` runs both implementations (the Lua one through
``lupa.lua51``) and requires byte-identical frames, chat lines and cell grids.

Layers
------
* **Frame** (section 2): ``4E | 0x20+type | seq | flags | bodyLen | TLVs | check (4 bytes)``
  where the TLV body (``tag, len, value``; tags ascending, absent fields omitted) carries the
  place key, drop id, Spotify / YouTube ids, an ``artist 0x1F title`` search query, start, play
  length, volume, place label and soundtrack-set id -- or, in HELLO and CONFIG frames
  (DESIGN-v1.2.1 2), the music choices (PREFS), a request (ACTION) and a Spotify client id;
  the trailer is :func:`frame_check` over everything before it (a polynomial hash mod
  2^31 - 1: protocol-attack finding F1). :func:`build_frame` / :func:`frame_from_event` apply
  exactly the normalisation the Lua encoder applies (link parsing, sanitising, UTF-8-safe
  truncation, per-type field set, and the fit rule against a frame-byte budget: 264 for the
  chat line, the row capacity for the beacon -- see :func:`_fit_body`); :func:`encode_frame`
  serialises; :func:`decode_frame` validates.
* **Pixel cells** (section 4): rows of ``cols`` cells; row 0 starts with the header
  ``MAGENTA GREEN MAGENTA GREEN`` and three geometry cells (``cols/16``, ``(rows-1)//8``,
  ``(rows-1)%8``), then the frame bits MSB-first, 3 per cell (R, G, B), zero-padded to the end
  of the last row and XOR-ed with a fixed keystream ("whitening") so that a covered or
  half-drawn strip fails the checksum / pad check instead of decoding as garbage.
  :func:`encode_cells` (fixed columns, or adaptive with :attr:`Geometry.max_rows`),
  :func:`encode_event_cells` (fit + cells, ``Protocol2.EncodeCells``), :func:`capacity`,
  :func:`rows_cap`, :func:`adaptive_layout`, :func:`decode_cells_grid` /
  :func:`decode_cell_codes`.
* **Chat line** (section 5): ``NDROP2 <base64url frame>``; :func:`parse_chat_line` accepts a
  record only at a chat-log timestamp or at line start (strict mode, as v1's chat transport);
  :func:`pad_line` / :func:`pad_line_count` are the ``NDPAD`` filler lines.

Everything here is pure and thread-safe; the decoders never raise on bad input.
"""
from __future__ import annotations

import base64
import binascii
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional, Sequence, Union

from .models import EventType, Flags
from .protocol import fletcher16

__all__ = [
    "MAGIC", "VERSION", "CHAT_PREFIX", "TAG_PLACE_KEY", "TAG_DROP_ID", "TAG_SPOTIFY", "TAG_YOUTUBE",
    "TAG_QUERY", "TAG_START_DS", "TAG_LENGTH_DS", "TAG_VOLUME", "TAG_LABEL", "TAG_SET_ID", "TAG_PREFS",
    "TAG_ACTION", "TAG_CLIENT_ID", "TAG_MUSIC_VOLUME", "TAG_NAMES",
    "MAX_DROP_ID", "MAX_QUERY", "MAX_LABEL", "MAX_SET_ID", "MAX_BODY", "MAX_FRAME", "FRAME_OVERHEAD",
    "TRAILER_LEN", "CHECK_SEED", "CHECK_MUL", "CHECK_MOD", "CHECK_FINAL_ROUNDS", "MIN_QUERY_PART",
    "QUERY_SEP", "HEADER", "HEADER_CODES", "LEAD_CELLS", "MIN_COLS", "MAX_COLS", "COL_STEP", "MAX_ROWS",
    "MIN_CELL_PX", "MAX_CELL_PX", "CORNERS", "CODE_RGB", "KNOWN_PREFIX_CODES", "TYPE_TAGS",
    "MAX_STRIP_PX", "PAD_PREFIX", "PAD_LINE_LEN", "PREFER_SPOTIFY", "PREFER_YOUTUBE",
    "PREFS_ORDERS", "PREFS_CORNERS", "ACTIONS", "CLIENT_ID_LEN",
    "TLV", "Prefs", "Action", "Frame", "Geometry", "CellGrid", "DEFAULT_GEOMETRY",
    "frame_check", "build_frame", "frame_from_event", "encode_frame", "decode_frame",
    "rows_for", "strip_size", "keystream_bits", "encode_cells", "encode_event_cells", "decode_cell_codes",
    "decode_cells_grid", "capacity", "rows_cap", "adaptive_layout",
    "b64url_encode", "b64url_decode", "format_chat_line", "find_chat_record", "parse_chat_line",
    "pad_line", "pad_line_count",
    "clean_text", "utf8_clean_bytes", "utf8_truncate", "sanitize_id", "build_query", "place_key_for",
    "spotify_id_from_link", "youtube_id_from_link",
]

# ------------------------------------------------------------------ constants --

MAGIC = 0x4E
VERSION = 2
CHAT_PREFIX = "NDROP2"

TAG_PLACE_KEY = 0x01  # uint16 BE: Fletcher-16 of the full drop id string
TAG_DROP_ID = 0x02  # ASCII [A-Za-z0-9:_.-], 1..40 bytes
TAG_SPOTIFY = 0x03  # 22 base62 chars
TAG_YOUTUBE = 0x04  # 11 chars [A-Za-z0-9_-]
TAG_QUERY = 0x05  # UTF-8 "artist" 0x1F "title", 1..120 bytes
TAG_START_DS = 0x06  # uint16 BE, start offset in 1/10 s
TAG_LENGTH_DS = 0x07  # uint16 BE, play length in 1/10 s (absent / 0 = whole song)
TAG_VOLUME = 0x08  # uint8 0..100
TAG_LABEL = 0x09  # UTF-8 place label, 1..48 bytes
TAG_SET_ID = 0x0A  # ASCII [A-Za-z0-9:_.-], 1..32 bytes
TAG_PREFS = 0x0B  # 4 bytes: order, corner, fade, resume codes (DESIGN-v1.2.1 2.3); HELLO and CONFIG
TAG_ACTION = 0x0C  # 6 bytes: code, code XOR 0xFF, issuedAt uint32 BE; CONFIG
TAG_CLIENT_ID = 0x0D  # 16 bytes: the Spotify client id's 32 hex digits; CONFIG
TAG_MUSIC_VOLUME = 0x0E  # optional uint8 0..100 master percentage; HELLO and CONFIG
_MAX_KNOWN_TAG = TAG_MUSIC_VOLUME
TAG_NAMES = {TAG_PLACE_KEY: "placeKey", TAG_DROP_ID: "dropId", TAG_SPOTIFY: "spotify", TAG_YOUTUBE: "youtube",
             TAG_QUERY: "query", TAG_START_DS: "startDs", TAG_LENGTH_DS: "lengthDs", TAG_VOLUME: "volume",
             TAG_LABEL: "label", TAG_SET_ID: "setId", TAG_PREFS: "prefs", TAG_ACTION: "action",
             TAG_CLIENT_ID: "clientId", TAG_MUSIC_VOLUME: "musicVolume"}

MAX_DROP_ID = 40
MAX_QUERY = 120
MAX_LABEL = 48
MAX_SET_ID = 32
MAX_BODY = 255
TRAILER_LEN = 4  # the frame check, uint32 BE
FRAME_OVERHEAD = 5 + TRAILER_LEN  # 9: 5 head bytes + the 4-byte check
MAX_FRAME = MAX_BODY + FRAME_OVERHEAD  # 264
QUERY_SEP = "\x1f"
#: Fit-rule step 5 (finding F3): when the query must be shortened, artist and title each keep at
#: least this many bytes (or all of a shorter part); otherwise the whole query goes.
MIN_QUERY_PART = 8

#: The frame check (finding F1): h = CHECK_SEED; h = (h * CHECK_MUL + byte + 1) % CHECK_MOD over
#: every byte before the trailer, then :data:`CHECK_FINAL_ROUNDS` more rounds
#: h = (h * CHECK_MUL + 1) % CHECK_MOD (as if that many 0x00 bytes followed). 48271 is a primitive
#: root mod the prime 2^31 - 1; every intermediate stays below 2^47, so Lua 5.1 doubles compute it
#: exactly. The final rounds give the last body byte the weight 48271^2 mod (2^31 - 1) instead of 1:
#: without them, +d on the last body byte and +d on the trailer's low byte cancelled (review RP-1).
CHECK_SEED = 0x4E443243  # 1313092163, "ND2C"
CHECK_MUL = 48271
CHECK_MOD = 2147483647
CHECK_FINAL_ROUNDS = 2

#: PREFS codes (DESIGN-v1.2.1 2.3): code = index + 1; 0 = not set (the app keeps its value).
PREFS_ORDERS = ("spotify-youtube", "youtube-spotify", "spotify", "youtube", "none")
PREFS_CORNERS = ("topright", "topleft", "bottomright", "bottomleft")
#: ACTION codes: code = index + 1.
ACTIONS = ("connect-spotify", "disconnect-spotify", "test-spotify", "test-youtube", "youtube-sign-in")
CLIENT_ID_LEN = 16  # bytes on the wire (32 hex digits)

_SONG_TAGS = frozenset(range(TAG_PLACE_KEY, TAG_SET_ID + 1))
#: Fields each event type carries (anything else is dropped by :func:`build_frame`). Song frames
#: never carry PREFS; a HELLO carries the playing drop's placeKey (with DROP_ACTIVE, D3) and PREFS.
TYPE_TAGS: dict[EventType, frozenset[int]] = {
    EventType.NONE: frozenset(), EventType.PING: frozenset(),
    EventType.HELLO: frozenset((TAG_PLACE_KEY, TAG_PREFS, TAG_MUSIC_VOLUME)),
    EventType.ARM: _SONG_TAGS, EventType.FIRE: _SONG_TAGS,
    EventType.CANCEL: frozenset((TAG_PLACE_KEY, TAG_DROP_ID)),
    EventType.CONFIG: frozenset((TAG_PREFS, TAG_ACTION, TAG_CLIENT_ID, TAG_MUSIC_VOLUME)),
}

# Pixel layout.
CODE_RGB = ((0, 0, 0), (0, 0, 255), (0, 255, 0), (0, 255, 255),
            (255, 0, 0), (255, 0, 255), (255, 255, 0), (255, 255, 255))  # index = R*4 + G*2 + B
HEADER = ((255, 0, 255), (0, 255, 0), (255, 0, 255), (0, 255, 0))  # magenta, green, magenta, green
HEADER_CODES = (5, 2, 5, 2)
HEADER_CELLS = 4
GEOMETRY_CELLS = 3
LEAD_CELLS = HEADER_CELLS + GEOMETRY_CELLS  # 7, never whitened
COL_STEP = 16
MIN_COLS = 16
MAX_COLS = 112
MAX_ROWS = 64
MIN_CELL_PX = 1
MAX_CELL_PX = 8
MAX_STRIP_PX = 9  # beacon strips: rows * cell_px <= 9 px (2 px -> 4 rows, 3 px -> 3, 4 px -> 2)
CORNERS = ("TOPLEFT", "TOPRIGHT", "BOTTOMLEFT", "BOTTOMRIGHT")
PREFER_SPOTIFY = "spotify"
PREFER_YOUTUBE = "youtube"

# Chat-log padding lines (SPEC 3.6, unchanged from v1): "NDPAD <seq> ....", 900 characters.
PAD_PREFIX = "NDPAD"
PAD_LINE_LEN = 900
_HEAD_CELLS = 14  # cells holding the 5 head bytes (40 bits -> 42)

# Whitening keystream: Park-Miller minimal standard LCG, keystream bit = top bit of the state.
WHITEN_SEED = 1313100338  # 0x4E445232 "NDR2"
WHITEN_MUL = 16807
WHITEN_MOD = 2147483647
_KS_LEN = 4096  # bits; a valid strip needs at most (7 rows * 112 - 7) * 3 = 2331 (264 B at 112 cols)


def _make_keystream(n: int) -> bytes:
    out = bytearray(n)
    x = WHITEN_SEED
    for i in range(n):
        x = (x * WHITEN_MUL) % WHITEN_MOD
        out[i] = x >> 30
    return bytes(out)


_KS = _make_keystream(_KS_LEN)
#: Keystream as one 3-bit code per data cell (data cell j covers keystream bits 3j..3j+2).
_KS_CODES = tuple((_KS[3 * j] << 2) | (_KS[3 * j + 1] << 1) | _KS[3 * j + 2] for j in range(_KS_LEN // 3))
_BITS3 = tuple(format(i, "03b") for i in range(8))

#: Colour codes of grid cells 7..10: the whitened magic byte and version nibble. They are the
#: same in every v2 strip, so locators use them to confirm a header and pin the lattice.
KNOWN_PREFIX_CODES = tuple(int(format((MAGIC << 4) | VERSION, "012b")[3 * j:3 * j + 3], 2) ^ _KS_CODES[j]
                           for j in range(4))


def keystream_bits(n: int) -> bytes:
    """The first ``n`` whitening keystream bits (0/1 bytes); cached up to 4096 bits."""
    return _KS[:n] if n <= _KS_LEN else _make_keystream(n)


# ---------------------------------------------------------------- number utils --

def _num(v: Any) -> Optional[float]:
    """``v`` if it is a finite int/float (bools excluded, like Lua's ``type(v) == "number"``)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def _u16(v: int) -> bytes:
    return bytes(((v >> 8) & 0xFF, v & 0xFF))


def _deciseconds(ds: Any, ms: Any) -> Optional[int]:
    """Lua ``deciseconds``: direct 1/10 s value or milliseconds; None when absent or 0."""
    d, m = _num(ds), _num(ms)
    if d is not None:
        v = math.floor(d + 0.5)
    elif m is not None:
        v = math.floor((m + 50) / 100)
    else:
        return None
    v = _clamp(int(v), 0, 0xFFFF)
    return v or None


def frame_check(data: Union[bytes, bytearray, Sequence[int]]) -> int:
    """The frame trailer's value (``Protocol2.FrameCheck``): starting at :data:`CHECK_SEED`,
    ``h = (h * 48271 + b + 1) % (2**31 - 1)`` for every byte ``b``, then two final rounds
    ``h = (h * 48271 + 1) % (2**31 - 1)``; sent as uint32 BE.

    A polynomial hash over a prime field, so unlike Fletcher-16 it sees the column-cover
    pattern of finding F1 (``-d, +2d, -d`` in bytes ``i, i+k, i+2k``): that pattern is zero
    only if ``48271**k == 1``, which never holds for a primitive root and ``k < 2**31 - 2``.
    The final rounds keep every body byte's weight away from small powers of 256, so no change
    of a few cells across the last body byte and the trailer passes (review RP-1: without them,
    ``+d`` on the last body byte and ``+d`` on the trailer's low byte left the frame valid).
    """
    h = CHECK_SEED
    for b in data:
        h = (h * CHECK_MUL + b + 1) % CHECK_MOD
    for _ in range(CHECK_FINAL_ROUNDS):
        h = (h * CHECK_MUL + 1) % CHECK_MOD
    return h


# ------------------------------------------------------------------ text utils --

_CTRL_TO_SPACE = bytes.maketrans(bytes(list(range(0x20)) + [0x7F]), b" " * 33)


def _repair_utf8(data: bytes) -> bytes:
    """Byte-for-byte port of the Lua cleaner for input that is not valid UTF-8."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c < 0x80:
            out.append(0x20 if c < 0x20 or c == 0x7F else c)
            i += 1
            continue
        size = 0
        if 0xC2 <= c <= 0xDF:
            if i + 1 < n and 0x80 <= data[i + 1] <= 0xBF:
                size = 2
        elif 0xE0 <= c <= 0xEF:
            lo = 0xA0 if c == 0xE0 else 0x80
            hi = 0x9F if c == 0xED else 0xBF
            if i + 2 < n and lo <= data[i + 1] <= hi and 0x80 <= data[i + 2] <= 0xBF:
                size = 3
        elif 0xF0 <= c <= 0xF4:
            lo = 0x90 if c == 0xF0 else 0x80
            hi = 0x8F if c == 0xF4 else 0xBF
            if (i + 3 < n and lo <= data[i + 1] <= hi and 0x80 <= data[i + 2] <= 0xBF
                    and 0x80 <= data[i + 3] <= 0xBF):
                size = 4
        if size:
            out += data[i:i + size]
            i += size
        else:
            out.append(0x3F)  # "?"
            i += 1
    return bytes(out)


def utf8_clean_bytes(data: bytes) -> bytes:
    """Lua ``Utf8Clean`` on raw bytes: invalid UTF-8 bytes -> ``?``, C0 controls and DEL ->
    space, leading/trailing spaces trimmed. The result is always valid UTF-8."""
    data = bytes(data)
    try:
        data.decode("utf-8")  # Python's strict codec enforces the same RFC 3629 table
    except UnicodeDecodeError:
        out = _repair_utf8(data)
    else:
        out = data.translate(_CTRL_TO_SPACE)
    return out.strip(b" ")


def clean_text(value: Any) -> bytes:
    """Text field cleaning (artist, title, label) as UTF-8 bytes; non-strings -> ``b""``.

    Lone surrogates in ``value`` are encoded as their (invalid) UTF-8 bytes and then repaired
    exactly as the Lua side would repair the same bytes.
    """
    if not isinstance(value, str):
        return b""
    return utf8_clean_bytes(value.encode("utf-8", "surrogatepass"))


def utf8_truncate(data: Union[bytes, str], max_bytes: int) -> bytes:
    """Longest prefix of at most ``max_bytes`` bytes that does not end inside a UTF-8 sequence
    (code-point safe; grapheme clusters such as ZWJ emoji may still be split)."""
    b = data.encode("utf-8", "surrogatepass") if isinstance(data, str) else bytes(data)
    if len(b) <= max_bytes:
        return b
    if max_bytes <= 0:
        return b""
    n = int(max_bytes)
    while n > 0 and 0x80 <= b[n] < 0xC0:
        n -= 1
    return b[:n]


_ID_OK = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:_.-")
_ID_TABLE = bytes(c if c in _ID_OK else 0x5F for c in range(256))
_ID_RE = re.compile(rb"[A-Za-z0-9:_.\-]+\Z")


def sanitize_id(value: Any, max_len: Optional[int] = None) -> str:
    """ASCII id sanitizer, byte-wise like the Lua ``gsub``: every UTF-8 byte outside
    ``[A-Za-z0-9:_.-]`` becomes ``_`` (so ``é`` -> ``__``); then cut to ``max_len`` bytes."""
    if not isinstance(value, str):
        return ""
    out = value.encode("utf-8", "surrogatepass").translate(_ID_TABLE)
    if max_len is not None:
        out = out[:max_len]
    return out.decode("ascii")


def place_key_for(drop_id: Any) -> Optional[int]:
    """Place key of a drop id: Fletcher-16 of the full (unsanitized) id's UTF-8 bytes."""
    if not isinstance(drop_id, str) or not drop_id:
        return None
    return fletcher16(drop_id.encode("utf-8", "surrogatepass"))


def _split_parts(a: bytes, t: bytes, budget: int) -> tuple[bytes, bytes]:
    """Lua ``fairSplit``: artist ``a`` and title ``t`` within ``budget`` bytes together (the
    separator not counted). A part that fits in half the budget is kept whole and the other
    gets the rest; otherwise the artist gets half (rounded down) and the title the rest. Each
    cut is on a UTF-8 boundary."""
    if len(a) + len(t) <= budget:
        return a, t
    half = budget // 2
    if len(a) <= half:
        t = utf8_truncate(t, budget - len(a))
    elif len(t) <= budget - half:
        a = utf8_truncate(a, budget - len(t))
    else:
        a = utf8_truncate(a, half)
        t = utf8_truncate(t, budget - len(a))
    return a, t


def build_query(artist: Any, title: Any) -> Optional[bytes]:
    """``artist 0x1F title`` within :data:`MAX_QUERY` bytes (None when both are empty).

    When both do not fit, the title keeps at least 60 and the artist at least 59 bytes (each
    cut on a UTF-8 boundary) so the separator always survives.
    """
    a, t = clean_text(artist), clean_text(title)
    if not a and not t:
        return None
    a, t = _split_parts(a, t, MAX_QUERY - 1)
    return a + b"\x1f" + t


def _fit_query(q: bytes, budget: int) -> Optional[bytes]:
    """Fit-rule step 5 (Lua ``fitQuery``): the query ``artist 0x1F title`` shortened to at most
    ``budget`` bytes, separator included, by :func:`_split_parts` on its two parts -- or None
    when the budget cannot leave each part :data:`MIN_QUERY_PART` bytes (a shorter part: all of
    it). The separator always survives (finding F3)."""
    a, sep, t = q.partition(b"\x1f")
    if not sep:  # never built by build_query; read the whole value as the title
        a, t = b"", q
    room = budget - 1
    if room < min(MIN_QUERY_PART, len(a)) + min(MIN_QUERY_PART, len(t)):
        return None
    a, t = _split_parts(a, t, room)
    return a + b"\x1f" + t


# --------------------------------------------------------------- link parsers --
# Ported to operate on UTF-8 bytes so they behave exactly like the Lua (byte-string) versions.

_WS = b" \t\n\r\x0b\x0c"  # Lua %s in the C locale
_B62_RE = re.compile(rb"[A-Za-z0-9]+\Z")
_YT_FULL_RE = re.compile(rb"[A-Za-z0-9_\-]+\Z")
_YT_RUN_RE = re.compile(rb"[A-Za-z0-9_\-]*")
_URL_CUT_RE = re.compile(rb"[/?#]")
_SEG_RE = re.compile(rb"[^/]+")
_INTL_RE = re.compile(rb"intl-[a-z][a-z\-]*\Z")
_YT_PATH_RE = re.compile(rb"/([^/]+)/(.*)\Z", re.S)
_YT_HOSTS = frozenset((b"youtube.com", b"www.youtube.com", b"m.youtube.com", b"music.youtube.com",
                       b"youtube-nocookie.com", b"www.youtube-nocookie.com"))
_YT_PATHS = frozenset((b"shorts", b"embed", b"live", b"v"))


def _link_bytes(text: Any) -> Optional[bytes]:
    if not isinstance(text, str):
        return None
    s = text.encode("utf-8", "surrogatepass").strip(_WS)
    return s or None


def _is_b62(b: bytes, n: int) -> bool:
    return len(b) == n and _B62_RE.match(b) is not None


def _split_url(s: bytes) -> tuple[bytes, bytes]:
    ls = s.lower()
    start = 8 if ls.startswith(b"https://") else 7 if ls.startswith(b"http://") else 0
    m = _URL_CUT_RE.search(s, start)
    if m is None:
        return ls[start:], b""
    return ls[start:m.start()], s[m.start():]


def _path_and_query(rest: bytes) -> tuple[bytes, bytes]:
    q, h = rest.find(b"?"), rest.find(b"#")
    if h >= 0 and (q < 0 or h < q):
        return rest[:h], b""
    if q < 0:
        return rest, b""
    if h >= 0:
        return rest[:q], rest[q + 1:h]
    return rest[:q], rest[q + 1:]


def spotify_id_from_link(text: Any) -> Optional[str]:
    """22-char Spotify track id from ``spotify:track:<id>``, an ``open.spotify.com`` track link
    (scheme optional, ``/intl-xx/`` and ``/embed/`` allowed, ``?si=...`` ignored) or a bare id.
    Anything else (albums, playlists, short links, junk) -> None."""
    s = _link_bytes(text)
    if s is None:
        return None
    if s[:14].lower() == b"spotify:track:":
        rid = s[14:]
        return rid.decode("ascii") if _is_b62(rid, 22) else None
    host, rest = _split_url(s)
    if host in (b"open.spotify.com", b"play.spotify.com"):
        if rest[:1] != b"/":
            return None
        path, _query = _path_and_query(rest)
        segs = _SEG_RE.findall(path)
        i = 0
        if i < len(segs) and _INTL_RE.match(segs[i].lower()):
            i += 1
        if i < len(segs) and segs[i].lower() == b"embed":
            i += 1
        if len(segs) == i + 2 and segs[i].lower() == b"track" and _is_b62(segs[i + 1], 22):
            return segs[i + 1].decode("ascii")
        return None
    if _is_b62(s, 22):
        return s.decode("ascii")
    return None


def _yt_id(cand: Optional[bytes]) -> Optional[str]:
    if cand is None:
        return None
    rid = _YT_RUN_RE.match(cand).group(0)  # type: ignore[union-attr]  # always matches
    if len(rid) != 11:
        return None
    nxt = cand[11:12]
    if nxt and nxt not in (b"/", b"?", b"&", b"#"):
        return None
    return rid.decode("ascii")


def youtube_id_from_link(text: Any) -> Optional[str]:
    """11-char YouTube video id from a bare id, ``youtu.be/<id>``, ``youtube.com/watch?v=<id>``
    (any parameter position), or ``/shorts/``, ``/embed/``, ``/live/``, ``/v/`` paths on
    youtube.com (www., m., music.) and youtube-nocookie.com. Anything else -> None."""
    s = _link_bytes(text)
    if s is None:
        return None
    if len(s) == 11 and _YT_FULL_RE.match(s):
        return s.decode("ascii")
    host, rest = _split_url(s)
    if host in (b"youtu.be", b"www.youtu.be"):
        if rest[:1] != b"/":
            return None
        return _yt_id(rest[1:])
    if host not in _YT_HOSTS:
        return None
    path, query = _path_and_query(rest)
    if path.lower() in (b"/watch", b"/watch/"):
        for pair in query.split(b"&"):
            if not pair:
                continue
            k, sep, v = pair.partition(b"=")
            if sep and k == b"v":
                return _yt_id(v)
        return None
    m = _YT_PATH_RE.match(path)
    if m and m.group(1).lower() in _YT_PATHS:
        return _yt_id(m.group(2))
    return None


# --------------------------------------------------------------------- frames --

@dataclass(frozen=True)
class TLV:
    """One body field as it appears on the wire (used for tags this version does not know)."""

    tag: int
    value: bytes


def _ascii_lower(value: Any) -> Optional[bytes]:
    """``value`` as UTF-8 bytes with ASCII-only case folding (Lua ``string.lower``), or None."""
    return value.encode("utf-8", "surrogatepass").lower() if isinstance(value, str) else None


def _name_code(value: Any, names: tuple[str, ...]) -> int:
    """1-based code of ``value`` among ``names`` (ASCII case-insensitive), 0 when not one."""
    v = _ascii_lower(value)
    for i, name in enumerate(names):
        if v == name.encode("ascii"):
            return i + 1
    return 0


def _bool_code(value: Any) -> int:
    return 1 if value is True else 2 if value is False else 0


def _code_name(code: int, names: tuple[str, ...]) -> Optional[str]:
    return names[code - 1] if 1 <= code <= len(names) else None


@dataclass(frozen=True)
class Prefs:
    """The PREFS TLV (0x0B, DESIGN-v1.2.1 2.3): the player's music choices made in game.

    ``order`` is one of :data:`PREFS_ORDERS`, ``corner`` one of :data:`PREFS_CORNERS`, ``fade``
    and ``resume`` booleans; ``volume`` is the separate optional MUSIC_VOLUME TLV (0..100).
    None means "not set" (the app keeps its own value). Unknown codes from a newer addon decode as None too.
    """

    order: Optional[str] = None
    corner: Optional[str] = None
    fade: Optional[bool] = None
    resume: Optional[bool] = None
    volume: Optional[int] = None

    @classmethod
    def coerce(cls, value: Any) -> Optional["Prefs"]:
        """Lua ``EncodeFrame``'s normalisation of ``ev.prefs`` (a mapping with ``order``,
        ``corner``, ``fade``, ``resume``, ``volume`` or a :class:`Prefs`): names match ASCII
        case-insensitively, fade / resume must be booleans, anything else is not set. None
        when nothing is set (no TLV)."""
        if isinstance(value, Prefs):
            value = {"order": value.order, "corner": value.corner, "fade": value.fade, "resume": value.resume,
                     "volume": value.volume}
        if not isinstance(value, Mapping):
            return None
        codes = (_name_code(value.get("order"), PREFS_ORDERS), _name_code(value.get("corner"), PREFS_CORNERS),
                 _bool_code(value.get("fade")), _bool_code(value.get("resume")))
        volume = _num(value.get("volume"))
        volume = None if volume is None else _clamp(int(math.floor(volume + 0.5)), 0, 100)
        return None if not any(codes) and volume is None else replace(cls.from_codes(bytes(codes)), volume=volume)

    @classmethod
    def from_codes(cls, codes: bytes) -> "Prefs":
        """Decoder side: four code bytes -> Prefs (unknown or 0 codes -> None)."""
        o, c, f, r = codes
        return cls(_code_name(o, PREFS_ORDERS), _code_name(c, PREFS_CORNERS),
                   {1: True, 2: False}.get(f), {1: True, 2: False}.get(r))

    def codes(self) -> bytes:
        """The TLV value (strict: an unknown name or a non-bool raises ValueError)."""
        out = []
        for v, names, what in ((self.order, PREFS_ORDERS, "order"), (self.corner, PREFS_CORNERS, "corner")):
            if v is not None and v not in names:
                raise ValueError(f"prefs.{what} must be one of {', '.join(names)} or None, got {v!r}")
            out.append(0 if v is None else names.index(v) + 1)
        for v, what in ((self.fade, "fade"), (self.resume, "resume")):
            if v is not None and not isinstance(v, bool):
                raise ValueError(f"prefs.{what} must be a bool or None, got {v!r}")
            out.append(_bool_code(v))
        return bytes(out)

    def describe(self) -> str:
        """``order/corner/fade/resume``, e.g. ``youtube-spotify/topleft/fade/no-resume`` ("-" = not set)."""
        def onoff(v: Optional[bool], word: str) -> str:
            return "-" if v is None else word if v else "no-" + word
        text = "/".join((self.order or "-", self.corner or "-", onoff(self.fade, "fade"), onoff(self.resume, "resume")))
        return text if self.volume is None else f"{text}/volume={self.volume}"


@dataclass(frozen=True)
class Action:
    """The ACTION TLV (0x0C, DESIGN-v1.2.1 2.3): a request the player sent from the game.

    ``code`` is the wire code (1..5 are :data:`ACTIONS`; others decode with ``name`` None and are
    ignored by the app), ``issued_at`` the game's ``time()`` (Unix seconds) at the click.
    """

    code: int
    issued_at: int

    @property
    def name(self) -> Optional[str]:
        """``connect-spotify`` ... ``youtube-sign-in``, or None for a code this version does not know."""
        return _code_name(self.code, ACTIONS)

    @classmethod
    def named(cls, name: str, issued_at: int) -> "Action":
        """An action by name (strict: an unknown name raises ValueError)."""
        code = _name_code(name, ACTIONS)
        if not code:
            raise ValueError(f"action must be one of {', '.join(ACTIONS)}, got {name!r}")
        return cls(code, issued_at)

    @classmethod
    def coerce(cls, value: Any) -> Optional["Action"]:
        """Lua ``EncodeFrame``'s normalisation of ``ev.action`` (a mapping ``{name, at}`` or an
        :class:`Action`): the name must be one of :data:`ACTIONS` (ASCII case-insensitive) and
        ``at`` a number, floored and clamped to 0..4294967295; otherwise None (no TLV)."""
        if isinstance(value, Action):
            value = {"name": value.name, "at": value.issued_at}
        if not isinstance(value, Mapping):
            return None
        code, at = _name_code(value.get("name"), ACTIONS), _num(value.get("at"))
        if not code or at is None:
            return None
        return cls(code, _clamp(int(math.floor(at)), 0, 0xFFFFFFFF))

    def value(self) -> bytes:
        """The TLV value (strict: a code outside 0..255 or a time outside uint32 raises ValueError)."""
        if isinstance(self.code, bool) or not isinstance(self.code, int) or not 0 <= self.code <= 255:
            raise ValueError(f"action code must be 0..255, got {self.code!r}")
        if isinstance(self.issued_at, bool) or not isinstance(self.issued_at, int) or not 0 <= self.issued_at <= 0xFFFFFFFF:
            raise ValueError(f"action issued_at must be a uint32, got {self.issued_at!r}")
        return bytes((self.code, self.code ^ 0xFF)) + self.issued_at.to_bytes(4, "big")

    def describe(self) -> str:
        return f"{self.name or self.code}@{self.issued_at}"


_HEX32_RE = re.compile(rb"[0-9A-Fa-f]{32}\Z")


def _client_id(value: Any) -> Optional[str]:
    """Lua ``clientIdOf``: ASCII whitespace removed, then exactly 32 hex digits (either case) ->
    lowercase; anything else None (no TLV)."""
    if not isinstance(value, str):
        return None
    b = value.encode("utf-8", "surrogatepass").translate(None, _WS)
    return b.decode("ascii").lower() if _HEX32_RE.match(b) else None


@dataclass(frozen=True)
class Frame:
    """One v2 addon -> companion message (both transports carry exactly the same bytes).

    Absent fields are None. Text fields are ``str``; ``query`` keeps the ``0x1F`` separator
    (see :attr:`artist` / :attr:`title`). ``prefs`` / ``action`` / ``client_id`` are the
    DESIGN-v1.2.1 fields (PREFS in HELLO and CONFIG, ACTION and CLIENT_ID in CONFIG; decoders
    do not enforce which type carries what). ``extra`` holds TLVs with tags this version does
    not know (forward compatibility). ``raw`` is the frame as received (not part of equality).
    """

    type: EventType
    seq: int = 0
    flags: Flags = Flags.NONE
    place_key: Optional[int] = None
    drop_id: Optional[str] = None
    spotify_id: Optional[str] = None
    youtube_id: Optional[str] = None
    query: Optional[str] = None
    start_ds: Optional[int] = None
    length_ds: Optional[int] = None
    volume: Optional[int] = None
    label: Optional[str] = None
    set_id: Optional[str] = None
    prefs: Optional[Prefs] = None
    action: Optional[Action] = None
    client_id: Optional[str] = None  # 32 lowercase hex digits
    extra: tuple[TLV, ...] = ()
    raw: bytes = field(default=b"", compare=False, repr=False)

    # -- convenience views --
    @property
    def artist(self) -> str:
        """Artist part of :attr:`query` ("" when absent)."""
        if not self.query:
            return ""
        a, sep, _t = self.query.partition(QUERY_SEP)
        return a if sep else ""

    @property
    def title(self) -> str:
        """Title part of :attr:`query` (the whole query when it has no separator)."""
        if not self.query:
            return ""
        a, sep, t = self.query.partition(QUERY_SEP)
        return t if sep else a

    @property
    def search_text(self) -> str:
        """``"artist title"`` for a free-text music search ("" when there is no query)."""
        return " ".join(p for p in (self.artist, self.title) if p)

    @property
    def start_ms(self) -> int:
        return (self.start_ds or 0) * 100

    @property
    def length_ms(self) -> int:
        """Play length in ms; 0 = whole song."""
        return (self.length_ds or 0) * 100

    @property
    def spotify(self) -> Optional[str]:
        """The bare 22-character Spotify track id (CONTRACTS-v1.2 3.1 name of :attr:`spotify_id`)."""
        return self.spotify_id

    @property
    def youtube(self) -> Optional[str]:
        """The 11-character YouTube video id (CONTRACTS-v1.2 3.1 name of :attr:`youtube_id`)."""
        return self.youtube_id

    @property
    def spotify_uri(self) -> Optional[str]:
        return f"spotify:track:{self.spotify_id}" if self.spotify_id else None

    @property
    def youtube_url(self) -> Optional[str]:
        return f"https://www.youtube.com/watch?v={self.youtube_id}" if self.youtube_id else None

    @property
    def key(self) -> tuple[int, int, int, int]:
        """Dedupe key shared by both transports (CONTRACTS-v1.2 3.1):
        ``(seq, type, placeKey or 0, flags)``. The beacon's fitted frame and the chat line's
        fuller frame of one event share it, and so do the beacon's full ARM and the
        placeKey-only ARM it shows after 3 s."""
        return (self.seq, int(self.type), self.place_key or 0, int(self.flags))

    def has(self, flag: Flags) -> bool:
        return bool(self.flags & flag)

    def to_bytes(self) -> bytes:
        return encode_frame(self)

    def describe(self) -> str:
        """Compact one-line rendering for logs and ``diag``."""
        parts = [f"{self.type.name} seq={self.seq} flags=0x{int(self.flags):02x}"]
        if self.place_key is not None:
            parts.append(f"place={self.place_key}")
        for name, v in (("id", self.drop_id), ("spotify", self.spotify_id), ("youtube", self.youtube_id)):
            if v:
                parts.append(f"{name}={v}")
        if self.query:
            parts.append(f"song={self.artist!r}/{self.title!r}")
        if self.start_ds:
            parts.append(f"start={self.start_ds / 10:g}s")
        if self.length_ds:
            parts.append(f"len={self.length_ds / 10:g}s")
        if self.volume is not None:
            parts.append(f"vol={self.volume}")
        if self.label:
            parts.append(f"label={self.label!r}")
        if self.set_id:
            parts.append(f"set={self.set_id}")
        if self.prefs is not None:
            parts.append(f"prefs={self.prefs.describe()}")
        if self.action is not None:
            parts.append(f"action={self.action.describe()}")
        if self.client_id:
            parts.append(f"client=...{self.client_id[-4:]}")  # the tail only: logs never hold the whole id
        if self.extra:
            parts.append("extra=" + ",".join(f"0x{t.tag:02x}" for t in self.extra))
        return " ".join(parts)


def _frame_size(vals: dict[int, bytes]) -> int:
    return FRAME_OVERHEAD + sum(2 + len(v) for v in vals.values())


def _remove(vals: dict[int, bytes], tag: int, size: int) -> int:
    v = vals.pop(tag, None)
    return size if v is None else size - 2 - len(v)


def _fit_body(vals: dict[int, bytes], budget: int = MAX_FRAME, prefer: str = PREFER_YOUTUBE) -> int:
    """The fit rule (DESIGN-v1.2 2.3 with findings F3 / F4; Lua ``fitBody``): while the frame is
    over ``budget`` bytes, remove in order 1 setId, 2 label, 3 query (only when the ``prefer``-red
    provider's id is present: without it the companion may still need the query for a Spotify
    search), 4 dropId (placeKey stays), 5 shorten the query -- artist and title separately, each
    keeping :data:`MIN_QUERY_PART` bytes, else the whole query goes (:func:`_fit_query`) -- 6
    volume, 7 the provider id that is not preferred. placeKey, start, length, the flags, the last
    provider id and the HELLO / CONFIG fields (PREFS, ACTION, CLIENT_ID, MUSIC_VOLUME) are never removed.
    Returns the frame size, over the budget only when those alone exceed it."""
    size = _frame_size(vals)
    if size <= budget:
        return size
    size = _remove(vals, TAG_SET_ID, size)
    if size <= budget:
        return size
    size = _remove(vals, TAG_LABEL, size)
    if size <= budget:
        return size
    if (TAG_SPOTIFY if prefer == PREFER_SPOTIFY else TAG_YOUTUBE) in vals:
        size = _remove(vals, TAG_QUERY, size)
        if size <= budget:
            return size
    size = _remove(vals, TAG_DROP_ID, size)
    if size <= budget:
        return size
    q = vals.get(TAG_QUERY)
    if q is not None:
        nq = _fit_query(q, len(q) - (size - budget))
        if nq is None:
            size = _remove(vals, TAG_QUERY, size)
        else:
            vals[TAG_QUERY] = nq
            size -= len(q) - len(nq)
        if size <= budget:
            return size
    size = _remove(vals, TAG_VOLUME, size)
    if size <= budget:
        return size
    if TAG_SPOTIFY in vals and TAG_YOUTUBE in vals:
        size = _remove(vals, TAG_YOUTUBE if prefer == PREFER_SPOTIFY else TAG_SPOTIFY, size)
    return size


def _prefer(value: Any) -> str:
    """``"spotify"`` or ``"youtube"`` (anything else -> youtube), ASCII case-insensitive as Lua."""
    if isinstance(value, str) and value.encode("utf-8", "surrogatepass").lower() == b"spotify":
        return PREFER_SPOTIFY
    return PREFER_YOUTUBE


def _budget(max_bytes: Any) -> int:
    """Frame-byte budget like Lua: floored, clamped to 9..264; MAX_FRAME when not a number."""
    b = _num(max_bytes)
    return MAX_FRAME if b is None else _clamp(int(math.floor(b)), FRAME_OVERHEAD, MAX_FRAME)


def build_frame(type: Any, *, seq: Any = 0, flags: Any = 0, drop_id: Any = None, place_key: Any = None,
                spotify: Any = None, youtube: Any = None, artist: Any = None, title: Any = None,
                start_ms: Any = None, start_ds: Any = None, length_ms: Any = None, length_ds: Any = None,
                volume: Any = None, label: Any = None, set_id: Any = None, prefs: Any = None,
                action: Any = None, client_id: Any = None, max_bytes: Any = None, prefer: Any = None) -> Frame:
    """A :class:`Frame` normalised exactly like the addon's ``Protocol2.EncodeFrame(ev, maxBytes, prefer)``.

    * numbers must be int/float (anything else is absent); ``seq``/``flags`` wrap mod 256;
    * ``place_key`` defaults to :func:`place_key_for` ``(drop_id)``; ``drop_id`` / ``set_id``
      are sanitised (:func:`sanitize_id`) and cut to 40 / 32 bytes;
    * ``spotify`` / ``youtube`` accept links or ids (:func:`spotify_id_from_link`,
      :func:`youtube_id_from_link`); unparseable values are dropped;
    * ``artist`` + ``title`` -> ``query`` (:func:`build_query`); ``label`` cleaned and cut to
      48 bytes on a UTF-8 boundary;
    * ``start_ds`` / ``length_ds`` (1/10 s) win over ``start_ms`` / ``length_ms``; zero values are
      omitted (absent means 0: start of song / whole song); ``volume`` rounded, clamped 0..100;
    * ``prefs`` (``{order, corner, fade, resume}`` or :class:`Prefs`, :meth:`Prefs.coerce`),
      ``action`` (``{name, at}`` or :class:`Action`, :meth:`Action.coerce`) and ``client_id``
      (32 hex digits, ASCII whitespace ignored; stored lowercase) -- each dropped when invalid;
    * only the fields of :data:`TYPE_TAGS` for the event type are kept;
    * if the frame would exceed ``max_bytes`` (default and maximum :data:`MAX_FRAME`, i.e. a
      255-byte body; floored and clamped like Lua) the fit rule applies (:func:`_fit_body`),
      keeping the ``prefer`` provider id (``"spotify"`` / ``"youtube"``, default youtube) last.

    Raises ValueError for an event type outside 0..6 (Python cannot represent it).
    """
    tv = _num(type)
    t = math.floor(tv) % 16 if tv is not None else 0
    if t > int(EventType.CONFIG):
        raise ValueError(f"unknown event type {type!r}")
    etype = EventType(t)
    sv, fv = _num(seq), _num(flags)
    seq_i = math.floor(sv) % 256 if sv is not None else 0
    flags_i = math.floor(fv) % 256 if fv is not None else 0
    allow = TYPE_TAGS[etype]
    vals: dict[int, bytes] = {}
    has_id = isinstance(drop_id, str) and drop_id != ""
    if TAG_PLACE_KEY in allow:
        pk = _num(place_key)
        if pk is not None:
            vals[TAG_PLACE_KEY] = _u16(math.floor(pk) % 65536)
        elif has_id:
            vals[TAG_PLACE_KEY] = _u16(fletcher16(drop_id.encode("utf-8", "surrogatepass")))
    if TAG_DROP_ID in allow and has_id:
        vals[TAG_DROP_ID] = sanitize_id(drop_id, MAX_DROP_ID).encode("ascii")
    if TAG_SPOTIFY in allow:
        sp = spotify_id_from_link(spotify)
        if sp:
            vals[TAG_SPOTIFY] = sp.encode("ascii")
    if TAG_YOUTUBE in allow:
        yt = youtube_id_from_link(youtube)
        if yt:
            vals[TAG_YOUTUBE] = yt.encode("ascii")
    if TAG_QUERY in allow:
        q = build_query(artist, title)
        if q is not None:
            vals[TAG_QUERY] = q
    if TAG_START_DS in allow:
        v = _deciseconds(start_ds, start_ms)
        if v:
            vals[TAG_START_DS] = _u16(v)
    if TAG_LENGTH_DS in allow:
        v = _deciseconds(length_ds, length_ms)
        if v:
            vals[TAG_LENGTH_DS] = _u16(v)
    if TAG_VOLUME in allow:
        vol = _num(volume)
        if vol is not None:
            vals[TAG_VOLUME] = bytes((_clamp(int(math.floor(vol + 0.5)), 0, 100),))
    if TAG_LABEL in allow:
        lab = utf8_truncate(clean_text(label), MAX_LABEL)
        if lab:
            vals[TAG_LABEL] = lab
    if TAG_SET_ID in allow:
        sid = sanitize_id(set_id, MAX_SET_ID)
        if sid:
            vals[TAG_SET_ID] = sid.encode("ascii")
    pr = Prefs.coerce(prefs) if TAG_PREFS in allow else None
    if pr is not None:
        if any(pr.codes()):
            vals[TAG_PREFS] = pr.codes()
        if pr.volume is not None:
            vals[TAG_MUSIC_VOLUME] = bytes((pr.volume,))
    ac = Action.coerce(action) if TAG_ACTION in allow else None
    if ac is not None:
        vals[TAG_ACTION] = ac.value()
    cid = _client_id(client_id) if TAG_CLIENT_ID in allow else None
    if cid is not None:
        vals[TAG_CLIENT_ID] = bytes.fromhex(cid)
    _fit_body(vals, _budget(max_bytes), _prefer(prefer))

    def text(tag: int) -> Optional[str]:
        v = vals.get(tag)
        return None if v is None else v.decode("utf-8")

    def u16(tag: int) -> Optional[int]:
        v = vals.get(tag)
        return None if v is None else (v[0] << 8) | v[1]

    return Frame(type=etype, seq=seq_i, flags=Flags(flags_i), place_key=u16(TAG_PLACE_KEY),
                 drop_id=text(TAG_DROP_ID), spotify_id=text(TAG_SPOTIFY), youtube_id=text(TAG_YOUTUBE),
                 query=text(TAG_QUERY), start_ds=u16(TAG_START_DS), length_ds=u16(TAG_LENGTH_DS),
                 volume=vals[TAG_VOLUME][0] if TAG_VOLUME in vals else None, label=text(TAG_LABEL),
                 set_id=text(TAG_SET_ID), prefs=pr, action=ac, client_id=cid)


_EVENT_KEYS = {"seq": "seq", "flags": "flags", "dropId": "drop_id", "placeKey": "place_key",
               "spotify": "spotify", "youtube": "youtube", "artist": "artist", "title": "title",
               "startMs": "start_ms", "startDs": "start_ds", "lengthMs": "length_ms", "lengthDs": "length_ds",
               "volume": "volume", "label": "label", "setId": "set_id", "prefs": "prefs", "action": "action",
               "clientId": "client_id"}


def frame_from_event(ev: Mapping[str, Any], *, max_bytes: Any = None, prefer: Any = None) -> Frame:
    """:func:`build_frame` from an addon-style event table (camelCase keys as in Lua:
    ``type, seq, flags, dropId, placeKey, spotify, youtube, artist, title, startMs|startDs,
    lengthMs|lengthDs, volume, label, setId, prefs, action, clientId, prefer``; unknown keys are
    ignored), fitted to ``max_bytes`` like ``Protocol2.EncodeFrame(ev, maxBytes, prefer)``;
    ``prefer`` defaults to the event's own ``prefer``."""
    kwargs = {py: ev[lua] for lua, py in _EVENT_KEYS.items() if lua in ev and ev[lua] is not None}
    return build_frame(ev.get("type"), max_bytes=max_bytes,
                       prefer=ev.get("prefer") if prefer is None else prefer, **kwargs)


def _check_ascii_id(value: str, max_len: int, what: str) -> bytes:
    b = value.encode("ascii", "strict") if value.isascii() else b""
    if not (1 <= len(b) <= max_len) or not _ID_RE.match(b):
        raise ValueError(f"{what} must be 1..{max_len} chars of [A-Za-z0-9:_.-], got {value!r}")
    return b


def _check_text(value: str, max_len: int, what: str) -> bytes:
    b = value.encode("utf-8")
    if not 1 <= len(b) <= max_len:
        raise ValueError(f"{what} must be 1..{max_len} UTF-8 bytes, got {len(b)}")
    return b


def encode_frame(frame: Frame) -> bytes:
    """Serialise a frame (strict: out-of-range values raise ValueError; use
    :func:`build_frame` to normalise arbitrary input first)."""
    t = int(frame.type)
    seq, flags = int(frame.seq), int(frame.flags)
    if not (0 <= t <= 15 and 0 <= seq <= 255 and 0 <= flags <= 255):
        raise ValueError(f"type/seq/flags out of range: {t}, {seq}, {flags}")
    tlvs: list[tuple[int, bytes]] = []
    if frame.place_key is not None:
        if not 0 <= frame.place_key <= 0xFFFF:
            raise ValueError(f"place_key out of range: {frame.place_key}")
        tlvs.append((TAG_PLACE_KEY, _u16(frame.place_key)))
    if frame.drop_id is not None:
        tlvs.append((TAG_DROP_ID, _check_ascii_id(frame.drop_id, MAX_DROP_ID, "drop_id")))
    if frame.spotify_id is not None:
        b = frame.spotify_id.encode("ascii", "replace")
        if not _is_b62(b, 22):
            raise ValueError(f"spotify_id must be 22 base62 chars, got {frame.spotify_id!r}")
        tlvs.append((TAG_SPOTIFY, b))
    if frame.youtube_id is not None:
        b = frame.youtube_id.encode("ascii", "replace")
        if len(b) != 11 or not _YT_FULL_RE.match(b):
            raise ValueError(f"youtube_id must be 11 chars of [A-Za-z0-9_-], got {frame.youtube_id!r}")
        tlvs.append((TAG_YOUTUBE, b))
    if frame.query is not None:
        tlvs.append((TAG_QUERY, _check_text(frame.query, MAX_QUERY, "query")))
    for tag, v in ((TAG_START_DS, frame.start_ds), (TAG_LENGTH_DS, frame.length_ds)):
        if v is not None:
            if not 0 <= v <= 0xFFFF:
                raise ValueError(f"{TAG_NAMES[tag]} out of range: {v}")
            tlvs.append((tag, _u16(v)))
    if frame.volume is not None:
        if not 0 <= frame.volume <= 100:
            raise ValueError(f"volume out of range: {frame.volume}")
        tlvs.append((TAG_VOLUME, bytes((frame.volume,))))
    if frame.label is not None:
        tlvs.append((TAG_LABEL, _check_text(frame.label, MAX_LABEL, "label")))
    if frame.set_id is not None:
        tlvs.append((TAG_SET_ID, _check_ascii_id(frame.set_id, MAX_SET_ID, "set_id")))
    if frame.prefs is not None:
        if not isinstance(frame.prefs, Prefs):
            raise ValueError(f"prefs must be a Prefs, got {frame.prefs!r}")
        codes = frame.prefs.codes()
        if any(codes) or frame.prefs.volume is None:
            tlvs.append((TAG_PREFS, codes))
        volume = frame.prefs.volume
        if volume is not None:
            if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100:
                raise ValueError(f"prefs.volume must be an integer 0..100, got {volume!r}")
            tlvs.append((TAG_MUSIC_VOLUME, bytes((volume,))))
    if frame.action is not None:
        if not isinstance(frame.action, Action):
            raise ValueError(f"action must be an Action, got {frame.action!r}")
        tlvs.append((TAG_ACTION, frame.action.value()))
    if frame.client_id is not None:
        if not isinstance(frame.client_id, str) or not _HEX32_RE.match(frame.client_id.encode("ascii", "replace")):
            raise ValueError(f"client_id must be 32 hex digits, got {frame.client_id!r}")
        tlvs.append((TAG_CLIENT_ID, bytes.fromhex(frame.client_id)))
    for x in frame.extra:
        if not (_MAX_KNOWN_TAG < x.tag <= 255) or len(x.value) > 255:
            raise ValueError(f"extra TLV must use an unknown tag 0x{_MAX_KNOWN_TAG + 1:02x}..0xff and <= 255 bytes: "
                             f"{x!r}")
        tlvs.append((x.tag, bytes(x.value)))
    tlvs.sort(key=lambda tv: tv[0])
    if any(tlvs[i][0] == tlvs[i + 1][0] for i in range(len(tlvs) - 1)):
        raise ValueError("duplicate TLV tag")
    body = b"".join(bytes((tag, len(v))) + v for tag, v in tlvs)
    if len(body) > MAX_BODY:
        raise ValueError(f"frame body is {len(body)} bytes (max {MAX_BODY})")
    head = bytes((MAGIC, (VERSION << 4) | t, seq, flags, len(body))) + body
    return head + frame_check(head).to_bytes(TRAILER_LEN, "big")


def _strict_text(v: bytes) -> Optional[str]:
    """Decoder text (query, label): strict UTF-8 (D5) -- None for any invalid sequence, which
    rejects the frame (Python's codec applies the same RFC 3629 table as Lua ``Utf8Valid``)."""
    try:
        return v.decode("utf-8")
    except UnicodeDecodeError:
        return None


def decode_frame(data: Any) -> Optional[Frame]:
    """Validate a complete frame and return it, or None on any problem: magic, version, known
    event type (0..6), exact length, the 4-byte check (:func:`frame_check`), TLV structure
    (non-zero, strictly ascending tags that fit the body) and known-tag formats (lengths, id
    character sets, volume <= 100, strict UTF-8 query and label, PREFS 4 / ACTION 6 / CLIENT_ID
    16 bytes, MUSIC_VOLUME 1 byte <= 100, the ACTION check byte). Unknown PREFS / ACTION codes are not errors (the field
    decodes as None / an action without a name). TLVs with unknown tags are skipped into
    :attr:`Frame.extra`. Decoders do not enforce :data:`TYPE_TAGS`. Never raises."""
    try:
        b = bytes(data)
    except (TypeError, ValueError):
        return None
    n = len(b)
    if n < FRAME_OVERHEAD or b[0] != MAGIC or (b[1] >> 4) != VERSION:
        return None
    t = b[1] & 0x0F
    if t > int(EventType.CONFIG):
        return None
    body_len = b[4]
    if n != FRAME_OVERHEAD + body_len:
        return None
    stop = 5 + body_len
    if frame_check(b[:stop]) != int.from_bytes(b[stop:stop + TRAILER_LEN], "big"):
        return None
    f: dict[str, Any] = {}
    extra: list[TLV] = []
    i, last = 5, 0
    while i < stop:
        if i + 1 >= stop:
            return None
        tag, ln = b[i], b[i + 1]
        if tag == 0 or tag <= last or i + 2 + ln > stop:
            return None
        v = b[i + 2:i + 2 + ln]
        if tag == TAG_PLACE_KEY:
            if ln != 2:
                return None
            f["place_key"] = (v[0] << 8) | v[1]
        elif tag in (TAG_DROP_ID, TAG_SET_ID):
            if not (1 <= ln <= (MAX_DROP_ID if tag == TAG_DROP_ID else MAX_SET_ID)) or not _ID_RE.match(v):
                return None
            f["drop_id" if tag == TAG_DROP_ID else "set_id"] = v.decode("ascii")
        elif tag == TAG_SPOTIFY:
            if not _is_b62(v, 22):
                return None
            f["spotify_id"] = v.decode("ascii")
        elif tag == TAG_YOUTUBE:
            if ln != 11 or not _YT_FULL_RE.match(v):
                return None
            f["youtube_id"] = v.decode("ascii")
        elif tag == TAG_QUERY:
            text = _strict_text(v) if 1 <= ln <= MAX_QUERY else None
            if text is None:
                return None
            f["query"] = text
        elif tag in (TAG_START_DS, TAG_LENGTH_DS):
            if ln != 2:
                return None
            f["start_ds" if tag == TAG_START_DS else "length_ds"] = (v[0] << 8) | v[1]
        elif tag == TAG_VOLUME:
            if ln != 1 or v[0] > 100:
                return None
            f["volume"] = v[0]
        elif tag == TAG_LABEL:
            text = _strict_text(v) if 1 <= ln <= MAX_LABEL else None
            if text is None:
                return None
            f["label"] = text
        elif tag == TAG_PREFS:
            if ln != 4:
                return None
            f["prefs"] = Prefs.from_codes(v)
        elif tag == TAG_ACTION:
            if ln != 6 or v[1] != v[0] ^ 0xFF:
                return None
            f["action"] = Action(v[0], int.from_bytes(v[2:6], "big"))
        elif tag == TAG_CLIENT_ID:
            if ln != CLIENT_ID_LEN:
                return None
            f["client_id"] = v.hex()
        elif tag == TAG_MUSIC_VOLUME:
            if ln != 1 or v[0] > 100:
                return None
            f["prefs"] = replace(f.get("prefs", Prefs()), volume=v[0])
        else:
            extra.append(TLV(tag, v))
        last = tag
        i += 2 + ln
    return Frame(type=EventType(t), seq=b[2], flags=Flags(b[3]), extra=tuple(extra), raw=b, **f)


# ---------------------------------------------------------------- pixel cells --

def _cell_px(value: Any) -> int:
    px = _num(value)
    return _clamp(int(math.floor((3 if px is None else px) + 0.5)), MIN_CELL_PX, MAX_CELL_PX)


def rows_cap(cell_px: Any) -> int:
    """Rows a beacon strip of ``cell_px`` px cells may have under the 9 px cap:
    ``floor(9 / cell_px)``, at least 1 (``Protocol2.RowsCap``): 2 -> 4, 3 -> 3, 4 -> 2."""
    return max(1, MAX_STRIP_PX // _cell_px(cell_px))


def capacity(rows: Any) -> int:
    """Largest frame (bytes, the 9-byte overhead included) that fits ``rows`` rows of 112 cells:
    ``floor((rows * 112 - 7) * 3 / 8)``, at most :data:`MAX_FRAME` (``Protocol2.Capacity``).
    2 px (4 rows) = 165, 3 px (3) = 123, 4 px (2) = 81, one row = 39."""
    r = _num(rows)
    r = 1 if r is None else max(1, int(math.floor(r)))
    return min(MAX_FRAME, (r * MAX_COLS - LEAD_CELLS) * 3 // 8)


def adaptive_layout(frame_len: int) -> tuple[int, int]:
    """``(rows, cols)`` of the adaptive strip for a frame of ``frame_len`` bytes: with
    ``cells = 7 + ceil(8 * frame_len / 3)``, ``rows = ceil(cells / 112)`` and
    ``cols = 16 * ceil(ceil(cells / rows) / 16)`` (``Protocol2.AdaptiveLayout``).
    :func:`rows_for` ``(frame_len, cols)`` always equals ``rows``."""
    cells = LEAD_CELLS + -(-frame_len * 8 // 3)
    rows = -(-cells // MAX_COLS)
    per_row = -(-cells // rows)
    return rows, COL_STEP * -(-per_row // COL_STEP)


@dataclass(frozen=True)
class Geometry:
    """Beacon strip geometry: cells per row (16..112, step 16), cell size in physical pixels
    (1..8), the client-area corner the strip is anchored to, and ``max_rows``: when set
    (1 .. :func:`rows_cap` ``(cell_px)``) the strip is *adaptive* -- frames are fitted to
    :func:`capacity` ``(max_rows)`` by :func:`encode_event_cells` and the columns follow
    :func:`adaptive_layout` (``cells_per_row`` is unused). Rows always read left-to-right,
    top-to-bottom, whatever the corner."""

    cells_per_row: int = 64
    cell_px: int = 3
    corner: str = "TOPLEFT"
    max_rows: Optional[int] = None

    def __post_init__(self) -> None:
        if not (MIN_COLS <= self.cells_per_row <= MAX_COLS and self.cells_per_row % COL_STEP == 0):
            raise ValueError(f"cells_per_row must be a multiple of {COL_STEP} in {MIN_COLS}..{MAX_COLS}")
        if not MIN_CELL_PX <= self.cell_px <= MAX_CELL_PX:
            raise ValueError(f"cell_px must be in {MIN_CELL_PX}..{MAX_CELL_PX}")
        if self.corner not in CORNERS:
            raise ValueError(f"corner must be one of {', '.join(CORNERS)}")
        if self.max_rows is not None and not 1 <= self.max_rows <= rows_cap(self.cell_px):
            raise ValueError(f"max_rows must be in 1..{rows_cap(self.cell_px)} for {self.cell_px} px cells "
                             f"(rows * cell_px <= {MAX_STRIP_PX})")

    @classmethod
    def coerce(cls, cells_per_row: Any = None, cell_px: Any = None, corner: Any = None,
               max_rows: Any = None) -> "Geometry":
        """Clamp arbitrary values into a valid geometry exactly like ``Protocol2.NormalizeGeometry``."""
        w = _num(cells_per_row)
        w = 64 if w is None else w
        w = _clamp(int(math.floor(w / COL_STEP + 0.5)) * COL_STEP, MIN_COLS, MAX_COLS)
        px = _cell_px(cell_px)
        c = corner.upper() if isinstance(corner, str) else "TOPLEFT"
        mr = _num(max_rows)
        mr_i = None if mr is None else _clamp(int(math.floor(mr)), 1, rows_cap(px))
        return cls(w, px, c if c in CORNERS else "TOPLEFT", mr_i)

    @classmethod
    def normalize(cls, geom: Optional[Mapping[str, Any]]) -> "Geometry":
        """:meth:`coerce` from an addon-style table ``{cellsPerRow, cellPx, corner, maxRows}``."""
        g = geom or {}
        return cls.coerce(g.get("cellsPerRow"), g.get("cellPx"), g.get("corner"), g.get("maxRows"))

    @classmethod
    def beacon(cls, cell_px: int = 3, max_rows: Optional[int] = None, corner: str = "TOPLEFT") -> "Geometry":
        """The beacon's adaptive geometry: ``max_rows`` defaults to the 9 px cap."""
        return cls(64, cell_px, corner, rows_cap(cell_px) if max_rows is None else max_rows)


DEFAULT_GEOMETRY = Geometry()


def rows_for(frame_len: int, cols: int) -> int:
    """Strip rows for a frame of ``frame_len`` bytes at ``cols`` cells per row."""
    cells = LEAD_CELLS + -(-frame_len * 8 // 3)
    return -(-cells // cols)


def _layout(frame_len: int, geom: Geometry) -> tuple[int, int]:
    if geom.max_rows is not None:
        return adaptive_layout(frame_len)
    return rows_for(frame_len, geom.cells_per_row), geom.cells_per_row


def strip_size(frame_len: int, geom: Geometry = DEFAULT_GEOMETRY) -> tuple[int, int, int]:
    """``(width_px, height_px, rows)`` of the strip for a frame of ``frame_len`` bytes."""
    rows, cols = _layout(frame_len, geom)
    return cols * geom.cell_px, rows * geom.cell_px, rows


@dataclass(frozen=True)
class CellGrid:
    """A rendered strip: ``rows * cols`` cells, row-major, RGB with channels 0 or 255.
    ``overflow``: an adaptive strip that needs more rows than its ``max_rows`` (the fit rule
    could not shrink the frame enough; the grid is still complete and decodable)."""

    rows: int
    cols: int
    cells: tuple[tuple[int, int, int], ...]
    frame_len: int = 0
    cell_px: int = 3
    overflow: bool = False

    @property
    def width_px(self) -> int:
        return self.cols * self.cell_px

    @property
    def height_px(self) -> int:
        return self.rows * self.cell_px

    def row(self, r: int) -> tuple[tuple[int, int, int], ...]:
        return self.cells[r * self.cols:(r + 1) * self.cols]

    def as_rows(self) -> list[list[tuple[int, int, int]]]:
        return [list(self.row(r)) for r in range(self.rows)]

    def codes(self) -> list[int]:
        return [((r >= 128) << 2) | ((g >= 128) << 1) | (b >= 128) for r, g, b in self.cells]


def encode_cells(frame: Union[Frame, bytes, bytearray], geom: Geometry = DEFAULT_GEOMETRY) -> CellGrid:
    """Cell grid for a frame (or raw frame bytes), identical to ``Protocol2.EncodeCellsFromBytes``
    (fixed columns, or the adaptive layout when ``geom.max_rows`` is set).

    Raises ValueError if the frame cannot fit in :data:`MAX_ROWS` rows.
    """
    data = encode_frame(frame) if isinstance(frame, Frame) else bytes(frame)
    rows, cols = _layout(len(data), geom)
    if rows > MAX_ROWS:
        raise ValueError(f"a {len(data)}-byte frame needs {rows} rows at {cols} cells per row (max {MAX_ROWS})")
    n_data = rows * cols - LEAD_CELLS
    pad = n_data * 3 - len(data) * 8
    value = int.from_bytes(data, "big") << pad
    codes = [5, 2, 5, 2, cols // COL_STEP, (rows - 1) >> 3, (rows - 1) & 7]
    shift = 3 * n_data
    for j in range(n_data):
        shift -= 3
        codes.append(((value >> shift) & 7) ^ _KS_CODES[j])
    overflow = geom.max_rows is not None and rows > geom.max_rows
    return CellGrid(rows, cols, tuple(CODE_RGB[c] for c in codes), len(data), geom.cell_px, overflow)


def encode_event_cells(ev: Mapping[str, Any], geom: Geometry = DEFAULT_GEOMETRY) -> CellGrid:
    """``Protocol2.EncodeCells(ev, geom)``: the event fitted to :func:`capacity`
    ``(geom.max_rows)`` (or to :data:`MAX_FRAME` for a fixed-width geometry; the event's
    ``prefer`` keeps its id last), then :func:`encode_cells`."""
    budget = MAX_FRAME if geom.max_rows is None else capacity(geom.max_rows)
    return encode_cells(frame_from_event(ev, max_bytes=budget), geom)


def decode_cell_codes(codes: Sequence[int], cols: int, rows: int) -> Optional[Frame]:
    """Decode a full sampled grid given as 3-bit colour codes (``R*4 + G*2 + B``), row-major.

    Checks the header, that the geometry cells match ``cols`` / ``rows``, that ``rows`` is
    exactly what the frame's length needs, that every pad bit is zero after de-whitening, and
    finally :func:`decode_frame`. None on any problem; never raises.
    """
    try:
        if cols % COL_STEP or not MIN_COLS <= cols <= MAX_COLS or not 1 <= rows <= MAX_ROWS:
            return None
        if len(codes) != cols * rows or tuple(codes[:HEADER_CELLS]) != HEADER_CODES:
            return None
        if codes[4] != cols // COL_STEP or codes[5] != (rows - 1) >> 3 or codes[6] != (rows - 1) & 7:
            return None
        n_data = rows * cols - LEAD_CELLS
        if n_data < _HEAD_CELLS or n_data > len(_KS_CODES):
            return None
        data = codes[LEAD_CELLS:]
        head = int("".join([_BITS3[data[j] ^ _KS_CODES[j]] for j in range(_HEAD_CELLS)]), 2) >> 2
        if head >> 32 != MAGIC or (head >> 28) & 0x0F != VERSION:
            return None
        flen = FRAME_OVERHEAD + (head & 0xFF)
        if rows_for(flen, cols) != rows:
            return None
        bits = "".join([_BITS3[c ^ k] for c, k in zip(data, _KS_CODES)])
        nbits = 8 * flen
        if "1" in bits[nbits:]:
            return None  # pad bits must be zero: a covered or stale tail fails here
        return decode_frame(int(bits[:nbits], 2).to_bytes(flen, "big"))
    except (TypeError, ValueError, IndexError):
        return None


def decode_cells_grid(grid: Sequence[Sequence[Sequence[int]]], threshold: int = 128) -> Optional[Frame]:
    """Decode sampled cells given as rows of ``(r, g, b)`` (the whole grid, header included).

    A channel is "on" when ``>= threshold``. None on any problem; never raises.
    """
    try:
        rows = [list(r) for r in grid]
        if not rows:
            return None
        cols = len(rows[0])
        if any(len(r) != cols for r in rows):
            return None
        t = threshold
        codes = [((c[0] >= t) << 2) | ((c[1] >= t) << 1) | (c[2] >= t) for row in rows for c in row]
        return decode_cell_codes(codes, cols, len(rows))
    except (TypeError, ValueError, IndexError):
        return None


# ------------------------------------------------------------------ chat line --

_B64_ALPHABET_RE = re.compile(r"[A-Za-z0-9_\-]*\Z")
#: A system message in the chat log: the client's timestamp, whitespace, then the record alone.
STRICT_LINE_RE = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})? \d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\s+"
                            r"NDROP2 ([A-Za-z0-9_\-]+)\s*$")
LINE_START_RE = re.compile(r"^NDROP2 ([A-Za-z0-9_\-]+)\s*$")
ANYWHERE_RE = re.compile(r"(?<![A-Za-z0-9_\-])NDROP2 ([A-Za-z0-9_\-]+)")


def b64url_encode(data: bytes) -> str:
    """RFC 4648 section 5 base64url without padding."""
    return base64.urlsafe_b64encode(bytes(data)).rstrip(b"=").decode("ascii")


def b64url_decode(text: Any) -> Optional[bytes]:
    """Strict inverse of :func:`b64url_encode` (alphabet, length and canonical trailing bits
    checked); None on any problem."""
    if not isinstance(text, str) or not _B64_ALPHABET_RE.match(text) or len(text) % 4 == 1:
        return None
    try:
        out = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None
    return out if b64url_encode(out) == text else None


def format_chat_line(frame: Union[Frame, bytes, bytearray]) -> str:
    """``NDROP2 <base64url frame>`` (the same bytes the beacon shows)."""
    data = encode_frame(frame) if isinstance(frame, Frame) else bytes(frame)
    return f"{CHAT_PREFIX} {b64url_encode(data)}"


def find_chat_record(line: str, *, strict: bool = True) -> Optional[str]:
    """The base64url token of the NDROP2 record in a chat-log line, or None.

    Strict (default): the record must be the whole system message -- the line is the client's
    timestamp then ``NDROP2 <token>``, or starts with it -- so chat typed by other players
    (always prefixed by ``[Name]``/channel tags) can never trigger a drop. Otherwise the first
    ``NDROP2 <token>`` anywhere in the line.
    """
    if not isinstance(line, str):
        return None
    line = line.rstrip("\r\n")
    m = STRICT_LINE_RE.match(line) or LINE_START_RE.match(line)
    if m is None and not strict:
        m = ANYWHERE_RE.search(line)
    return m.group(1) if m else None


def parse_chat_line(line: str, *, strict: bool = True) -> Optional[Frame]:
    """Find, base64url-decode and validate an NDROP2 record (see :func:`find_chat_record`)."""
    token = find_chat_record(line, strict=strict)
    if token is None:
        return None
    data = b64url_decode(token)
    return None if data is None else decode_frame(data)


def pad_line(seq: Any, length: int = PAD_LINE_LEN) -> str:
    """``Protocol2.PadLine``: ``"NDPAD <seq mod 256> "`` filled with dots to ``length`` chars."""
    s = _num(seq)
    head = f"{PAD_PREFIX} {math.floor(s) % 256 if s is not None else 0} "
    return head + "." * (int(length) - len(head))


def pad_line_count(pad_bytes: Any, length: int = PAD_LINE_LEN) -> int:
    """``Protocol2.PadLineCount``: padding lines of ``length`` chars for ``pad_bytes`` bytes."""
    b = _num(pad_bytes)
    if b is None or b <= 0:
        return 0
    return int(math.ceil(b / (length if length > 0 else PAD_LINE_LEN)))
