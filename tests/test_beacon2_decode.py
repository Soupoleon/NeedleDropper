"""Pixel beacon v2 locator / decoder (docs/PROTOCOL-v2.md section 4) on synthetic BGRA frames.

Strips are rendered the way a GPU rasterises axis-aligned quads (a pixel belongs to the cell
that contains its centre), on busy game-like backgrounds that include stray pure magenta and
green runs, then decoded with :func:`needledropper.transport.beacon2.locate_and_decode`.
Covers all four corners, 1-8 px cells, offsets, colour noise, tone shifts, fractional pitch,
whole-frame nearest-neighbour resampling, blended edges, 32/48/64 cells per row, frames of
9..264 bytes, partial occlusion and tearing (must yield None, never a wrong frame), garbage,
truncation, the corner constraint, Lua-rendered strips, and timing.
"""
from __future__ import annotations

import dataclasses
import math
import random
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import protocol, protocol2 as p2  # noqa: E402
from needledropper.models import Event, EventType, Flags  # noqa: E402
from needledropper.transport import beacon2  # noqa: E402
from needledropper.transport.beacon2 import CORNERS, locate_and_decode, scan  # noqa: E402

# ------------------------------------------------------------------ frames --


def frame_of_size(n: int, seed: int = 0) -> bytes:
    """A valid v2 frame of exactly ``n`` bytes (9 or 11..264; was 7 or 9..262 before the 4-byte
    frame check, finding F1): a FIRE with real fields, padded with TLVs of tags this version does
    not know (decoders skip them). A body is empty or at least one 2-byte TLV head, so no frame
    is 10 bytes long."""
    assert n == p2.FRAME_OVERHEAD or p2.FRAME_OVERHEAD + 2 <= n <= p2.MAX_FRAME, n
    rng = random.Random(seed)
    base = p2.build_frame(EventType.FIRE, seq=rng.randrange(256), flags=rng.randrange(128),
                          drop_id=f"zone:{rng.randrange(1, 9999)}", artist="Low Roar", title="I'll Keep Coming")
    rest = n - len(p2.encode_frame(base))
    if rest < 0 or rest == 1:
        base = p2.Frame(EventType.FIRE, seq=rng.randrange(256))
        rest = n - p2.FRAME_OVERHEAD
    extras = []
    while rest > 0:
        ln = min(255, rest - 2)
        if rest - (ln + 2) == 1:
            ln -= 1
        extras.append(p2.TLV(0x20 + len(extras), bytes(rng.randrange(256) for _ in range(ln))))
        rest -= ln + 2
    out = p2.encode_frame(dataclasses.replace(base, extra=tuple(extras)))
    assert len(out) == n
    return out


REAL_FRAME = p2.encode_frame(p2.frame_from_event({
    "type": 3, "seq": 9, "flags": 0x20, "dropId": "zone:1429", "spotify": "4uLU6hMCjMI75M1A2tKUQC",
    "artist": "Sigur Rós", "title": "Hoppípolla", "label": "Elwynn Forest", "setId": "azeroth-mixtape",
    "lengthMs": 240000, "volume": 80}))


# ------------------------------------------------------------------ images --

def _span(origin: float, i: int, pitch: float) -> tuple[int, int]:
    """Pixels whose centres fall in [origin + i*pitch, origin + (i+1)*pitch) (GPU rasterisation)."""
    return int(math.ceil(origin + i * pitch - 0.5)), int(math.ceil(origin + (i + 1) * pitch - 0.5))


class Canvas:
    """A BGRA image under construction."""

    def __init__(self, width: int, height: int, *, background: str = "game", seed: int = 0) -> None:
        self.w, self.h = width, height
        self.rng = random.Random(seed)
        self.px = bytearray(width * height * 4)
        if background == "game":
            self._game_background()
        elif background == "white":
            self.px[:] = b"\xff" * len(self.px)
        elif background != "black":
            raise ValueError(background)

    def _game_background(self) -> None:
        """Busy, game-like background: random-length runs of random colours, including stray pure
        magenta / green runs so the header finder is genuinely exercised."""
        rng = self.rng
        extra = [(255, 0, 255), (0, 255, 0), (240, 10, 250), (5, 250, 12)]
        for y in range(self.h):
            row = bytearray()
            x = 0
            while x < self.w:
                n = min(rng.randint(1, 7), self.w - x)
                r, g, b = rng.choice(extra) if rng.random() < 0.15 else (
                    rng.randrange(256), rng.randrange(256), rng.randrange(256))
                row += bytes((b, g, r, 255)) * n
                x += n
            self.px[y * self.w * 4:(y + 1) * self.w * 4] = row

    def put(self, x: int, y: int, rgb) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            o = (y * self.w + x) * 4
            self.px[o:o + 4] = bytes((rgb[2], rgb[1], rgb[0], 255))

    def get(self, x: int, y: int) -> tuple[int, int, int]:
        o = (y * self.w + x) * 4
        return self.px[o + 2], self.px[o + 1], self.px[o]

    def fill(self, x0: int, y0: int, x1: int, y1: int, rgb) -> None:
        x0, x1 = max(0, x0), min(self.w, x1)
        if x1 <= x0:
            return
        pixel = bytes((rgb[2], rgb[1], rgb[0], 255))
        for y in range(max(0, y0), min(self.h, y1)):
            o = (y * self.w + x0) * 4
            self.px[o:o + (x1 - x0) * 4] = pixel * (x1 - x0)

    def cells(self, cells, cols: int, pitch: float, ox: float, oy: float, *, noise: int = 0, tone=None) -> None:
        """Draw a row-major cell grid (RGB 0/255) with its top-left at (ox, oy)."""
        rng = self.rng
        rows = len(cells) // cols
        for r in range(rows):
            ya, yb = _span(oy, r, pitch)
            for c in range(cols):
                xa, xb = _span(ox, c, pitch)
                rgb = cells[r * cols + c]
                if tone is not None:
                    rgb = tuple(lo + v * (hi - lo) // 255 for v, (lo, hi) in zip(rgb, tone))
                if not noise:
                    for y in range(ya, yb):
                        self.fill(xa, y, xb, y + 1, rgb)
                    continue
                for y in range(ya, yb):
                    for x in range(xa, xb):
                        self.put(x, y, [max(0, v - rng.randint(0, noise)) if v >= 128 else
                                        min(255, v + rng.randint(0, noise)) for v in rgb])

    def frame(self) -> tuple[bytes, int, int, int]:
        return bytes(self.px), self.w, self.h, self.w * 4


def place(corner: str, w: int, h: int, cols: int, rows: int, pitch: float, off: tuple[float, float]):
    """Top-left of a strip whose ``corner`` sits ``off`` px from the image's same corner."""
    ox, oy = off
    x = ox if corner.endswith("LEFT") else w - ox - cols * pitch
    y = oy if corner.startswith("TOP") else h - oy - rows * pitch
    return x, y


def make(frame: bytes, *, cols: int = 64, pitch: float = 3.0, corner: str = "TOPLEFT", off=(0.0, 0.0),
         width: int = 320, height: int = 48, noise: int = 0, tone=None, seed: int = 1, background: str = "game"):
    """Render ``frame`` as a v2 strip; returns (canvas, grid, (x, y) strip top-left)."""
    grid = p2.encode_cells(frame, p2.Geometry(cols))
    width = max(width, int(math.ceil(off[0] + cols * pitch)) + 3)
    height = max(height, int(math.ceil(off[1] + grid.rows * pitch)) + 3)
    cv = Canvas(width, height, background=background, seed=seed)
    x, y = place(corner, width, height, cols, grid.rows, pitch, off)
    cv.cells(grid.cells, cols, pitch, x, y, noise=noise, tone=tone)
    return cv, grid, (x, y)


def nn_resample(cv: Canvas, s: float, *, centre: bool = True) -> Canvas:
    """Nearest-neighbour resample of the whole image by ``s`` (pixel-centre or left-edge rule)."""
    w2, h2 = int(round(cv.w * s)), int(round(cv.h * s))
    out = Canvas(w2, h2, background="black")

    def src(j: int, n: int) -> int:
        return min(n - 1, int(math.floor((j + 0.5) / s)) if centre else int(math.floor(j / s)))

    xmap = [src(j, cv.w) for j in range(w2)]
    for y in range(h2):
        row = cv.px[src(y, cv.h) * cv.w * 4:(src(y, cv.h) + 1) * cv.w * 4]
        out.px[y * w2 * 4:(y + 1) * w2 * 4] = b"".join(bytes(row[x * 4:x * 4 + 4]) for x in xmap)
    return out


def box_down(cv: Canvas, f: int) -> Canvas:
    """Box-filter downscale by an integer factor: blended pixels at every cell boundary."""
    w2, h2 = cv.w // f, cv.h // f
    out = Canvas(w2, h2, background="black")
    for y in range(h2):
        for x in range(w2):
            acc = [0, 0, 0]
            for dy in range(f):
                for dx in range(f):
                    r, g, b = cv.get(x * f + dx, y * f + dy)
                    acc[0] += r
                    acc[1] += g
                    acc[2] += b
            out.put(x, y, tuple(a // (f * f) for a in acc))
    return out


def decode(cv: Canvas, corner: str = "TOPLEFT", **kw):
    rep = scan(*cv.frame(), corner, **kw)
    assert not rep.reason.startswith("decoder error"), rep.reason  # internal bugs must not hide as None
    assert locate_and_decode(*cv.frame(), corner, **kw) == rep.result
    return rep.result


def assert_decoded(cv: Canvas, frame: bytes, corner: str = "TOPLEFT", **kw):
    res = decode(cv, corner, **kw)
    assert res is not None, scan(*cv.frame(), corner, **kw)
    assert res.frame.raw == frame
    assert res.frame == p2.decode_frame(frame)
    return res


SIZES = (11, 33, 60, 120, 189, 264)  # was (10, ..., 262): frames are 9 or 11..264 bytes with the 4-byte check


# ------------------------------------------------------ decode: geometry --

@pytest.mark.parametrize("cols", (32, 48, 64))
@pytest.mark.parametrize("cell", range(1, 9))
@pytest.mark.parametrize("corner", CORNERS)
def test_integer_cells_every_corner(corner, cell, cols):
    size = SIZES[(cell + cols // 16) % len(SIZES)]
    frame = frame_of_size(size, seed=cell * 100 + cols)
    cv, grid, (x, y) = make(frame, cols=cols, pitch=cell, corner=corner, off=(3, 2), seed=cell + cols)
    res = assert_decoded(cv, frame, corner)
    assert (res.rows, res.cols) == (grid.rows, cols)
    assert (res.x, res.y) == (round(x), round(y))
    assert res.cell_px == pytest.approx(cell, abs=0.05)


@pytest.mark.parametrize("cell", (2, 3, 5))
@pytest.mark.parametrize("off", [(0, 0), (1, 0), (0, 1), (7, 5), (31, 20), (48, 3), (3, 48)])
@pytest.mark.parametrize("corner", CORNERS)
def test_offsets_from_each_corner(corner, off, cell):
    frame = frame_of_size(80, seed=cell)
    cv, grid, (x, y) = make(frame, cols=48, pitch=cell, corner=corner, off=off, height=96, seed=cell * 7)
    res = assert_decoded(cv, frame, corner)
    assert (res.x, res.y) == (round(x), round(y))


FRACTIONAL = [(cell, scale) for cell in range(1, 9) for scale in (1.125, 1.25, 1.5)] + \
             [(cell, scale) for cell in range(2, 9) for scale in (1.333, 1.75)]


@pytest.mark.parametrize("cell,scale", FRACTIONAL)
def test_fractional_pitch(cell, scale):
    """Non-integer cell sizes (display scaling): cells drawn as uneven runs."""
    i = FRACTIONAL.index((cell, scale))
    corner = CORNERS[i % 4]
    cols = (32, 48, 64)[i % 3]
    frame = frame_of_size(SIZES[i % len(SIZES)], seed=i)
    pitch = cell * scale
    cv, grid, _ = make(frame, cols=cols, pitch=pitch, corner=corner, off=(1.3 + i % 3, 0.6 + i % 2), seed=i)
    res = assert_decoded(cv, frame, corner)
    assert res.cell_px == pytest.approx(pitch, abs=0.1)


@pytest.mark.parametrize("centre", [True, False], ids=["centre", "edge"])
@pytest.mark.parametrize("scale", (1.125, 1.25, 1.5))
@pytest.mark.parametrize("cell", (1, 2, 3, 4, 6, 8))
def test_nearest_neighbour_resample(cell, scale, centre):
    """Whole-frame nearest-neighbour resample of an integer-cell strip (offset included)."""
    corner = CORNERS[(cell + int(scale * 8)) % 4]
    cols = 32 if cell * 64 > 300 else 64
    frame = frame_of_size(SIZES[cell % len(SIZES)], seed=cell * 31)
    src, _grid, _ = make(frame, cols=cols, pitch=cell, corner=corner, off=(2, 1), width=200, height=40, seed=cell)
    assert_decoded(nn_resample(src, scale, centre=centre), frame, corner)


@pytest.mark.parametrize("cell", (3, 4, 5, 6))
def test_blended_edges(cell):
    """A 2x render box-filtered down: half-pixel pitch and a blended pixel at every cell edge."""
    frame = frame_of_size(120, seed=cell)
    big, _grid, _ = make(frame, cols=48, pitch=cell * 2 + 1, off=(5, 3), width=640, height=96, seed=cell)
    res = assert_decoded(box_down(big, 2), frame)
    assert res.cell_px == pytest.approx(cell + 0.5, abs=0.1)


@pytest.mark.parametrize("cell", (1, 2, 3, 4.5, 6, 8))
@pytest.mark.parametrize("corner", CORNERS)
def test_colour_noise(corner, cell):
    """255 -> 230..255 and 0 -> 0..25 per channel per pixel."""
    frame = frame_of_size(100, seed=int(cell * 10))
    cv, _grid, _ = make(frame, cols=48, pitch=cell, corner=corner, off=(1, 1), noise=25, seed=int(cell * 3))
    assert_decoded(cv, frame, corner)


@pytest.mark.parametrize("tone", [
    ((30, 225), (25, 230), (40, 210)),   # HDR-ish compressed range, tinted
    ((60, 200), (60, 200), (60, 200)),   # washed out
    ((0, 160), (0, 170), (0, 150)),      # dim
    ((90, 255), (100, 255), (80, 255)),  # lifted blacks
], ids=["hdr", "washed", "dim", "lifted"])
@pytest.mark.parametrize("cell", (2, 3, 5.5))
def test_tone_shift(tone, cell):
    frame = frame_of_size(80, seed=9)
    cv, _grid, _ = make(frame, cols=48, pitch=cell, off=(2, 0), noise=8, tone=tone, seed=3)
    assert_decoded(cv, frame)


def test_threshold_parameter():
    """A strip whose 'on' level is below 128 decodes only with a lower threshold."""
    frame = frame_of_size(40, seed=2)
    cv, _grid, _ = make(frame, cols=32, pitch=4, tone=((0, 110), (0, 110), (0, 110)), seed=5)
    assert decode(cv) is None
    assert_decoded(cv, frame, threshold=60)


# the smallest frames (9: empty body, then 11, 12, 13), row boundaries and the largest; with the
# 4-byte check (finding F1) every frame is 2 bytes longer, so 7 and 10 no longer exist
@pytest.mark.parametrize("size", (9, 11, 12, 13, 15, 20, 47, 48, 100, 150, 189, 190, 220, 255, 262, 263, 264))
@pytest.mark.parametrize("cols", (32, 48, 64))
def test_payload_sizes(cols, size):
    corner = CORNERS[(size + cols) % 4]
    frame = frame_of_size(size, seed=size * 3 + cols)
    cv, grid, _ = make(frame, cols=cols, pitch=3, corner=corner, off=(2, 1), height=32, seed=size)
    res = assert_decoded(cv, frame, corner)
    assert res.rows == grid.rows == p2.rows_for(size, cols)


def test_real_frame_fields_survive_the_pixels():
    cv, _grid, _ = make(REAL_FRAME, cols=64, pitch=3, off=(1, 1), seed=4)
    f = assert_decoded(cv, REAL_FRAME).frame
    assert (f.type, f.drop_id, f.spotify_id, f.artist, f.title, f.label, f.set_id, f.length_ms, f.volume) == (
        EventType.FIRE, "zone:1429", "4uLU6hMCjMI75M1A2tKUQC", "Sigur Rós", "Hoppípolla", "Elwynn Forest",
        "azeroth-mixtape", 240000, 80)


def test_lua_rendered_strip_decodes():
    """End-to-end cross-language pixel contract: the addon's cells, rendered, decode here."""
    lua51 = pytest.importorskip("lupa.lua51")
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    src = (ROOT / "addon" / "NeedleDropper" / "Protocol2.lua").read_text(encoding="utf-8")
    ns = rt.table()
    rt.execute("return function(src) return loadstring(src, '=Protocol2.lua') end")(src)("NeedleDropper", ns)
    P = ns.Protocol2
    ev = {"type": 3, "seq": 77, "flags": 0x20, "dropId": "sub:1429:lion-s-pride-inn", "artist": "Ólafur Arnalds",
          "title": "Saman", "youtube": "https://youtu.be/dQw4w9WgXcQ", "label": "Lion's Pride Inn", "startMs": 4000}
    for corner, cols, px in (("TOPLEFT", 64, 3), ("BOTTOMRIGHT", 32, 2), ("TOPRIGHT", 48, 4), ("BOTTOMLEFT", 16, 5)):
        g = P.EncodeCells(rt.table_from(ev), rt.table_from({"cellsPerRow": cols, "cellPx": px, "corner": corner}))
        cells = [tuple(255 * int(g.cells[i][k]) for k in (1, 2, 3)) for i in range(1, len(g.cells) + 1)]
        w, h = max(320, g.widthPx + 8), max(48, g.heightPx + 8)
        cv = Canvas(w, h, seed=cols)
        x, y = place(corner, w, h, cols, g.rows, px, (2, 1))
        cv.cells(cells, cols, px, x, y)
        res = decode(cv, corner)
        assert res is not None and res.frame == p2.frame_from_event(ev)
        assert (res.rows, res.cols) == (g.rows, cols) and res.cell_px == pytest.approx(px, abs=0.05)


# --------------------------------------------------- decode: must fail --

def test_partial_occlusion_never_decodes_garbage():
    """Windows, tooltips and bars covering part of the strip: None (or, if the cover happened
    to leave every bit intact, the true frame) -- never a different frame."""
    frame = frame_of_size(200, seed=11)
    outcomes = {"none": 0, "intact": 0}
    rng = random.Random(4)
    for k in range(120):
        cv, grid, _ = make(frame, cols=64, pitch=3, off=(1, 1), seed=k)
        cw, ch = rng.randint(1, 30) * 3, rng.randint(1, 6) * 3
        cx, cy = rng.randrange(0, 64 * 3 - 3), rng.randrange(0, grid.rows * 3 - 3)
        cover = rng.choice([(0, 0, 0), (255, 255, 255), (30, 30, 34), (200, 180, 20), (255, 0, 255)])
        cv.fill(1 + cx, 1 + cy, 1 + cx + cw, 1 + cy + ch, cover)
        res = decode(cv)
        if res is None:
            outcomes["none"] += 1
        else:
            assert res.frame.raw == frame
            outcomes["intact"] += 1
    assert outcomes["none"] >= 110


@pytest.mark.parametrize("corner", CORNERS)
def test_bottom_rows_cut_off_or_covered(corner):
    frame = frame_of_size(264, seed=5)  # the largest frame (262 before the 4-byte check)
    cv, grid, (x, y) = make(frame, cols=64, pitch=3, corner=corner, off=(2, 2), seed=6)
    top = int(round(y))
    for keep in range(1, grid.rows):  # cover everything below row `keep`
        c2 = Canvas(cv.w, cv.h, background="black")
        c2.px[:] = cv.px
        c2.fill(0, top + keep * 3, cv.w, top + grid.rows * 3, (20, 20, 20))
        assert decode(c2, corner) is None


def test_tearing_between_two_frames_is_rejected():
    """A capture that caught the strip mid-update (top rows of one frame, bottom of the next)."""
    a = p2.encode_frame(p2.build_frame(EventType.FIRE, seq=10, drop_id="zone:1429", artist="A", title="Song A"))
    b = p2.encode_frame(p2.build_frame(EventType.FIRE, seq=11, drop_id="zone:1411", artist="B", title="Song B"))
    ga, gb = p2.encode_cells(a), p2.encode_cells(b)
    assert ga.rows == gb.rows
    for split in range(1, ga.rows):
        cells = ga.cells[:split * ga.cols] + gb.cells[split * gb.cols:]
        cv = Canvas(320, 48, seed=split)
        cv.cells(cells, ga.cols, 3, 1, 1)
        res = decode(cv)
        assert res is None or res.frame.raw in (a, b)
        assert res is None


def test_v1_strip_is_not_a_v2_strip():
    ev = Event(EventType.FIRE, 7, 12, 40213, Flags.DROP_ACTIVE)
    cv = Canvas(256, 32, seed=1)
    cv.cells(protocol.encode_beacon_cells(ev), protocol.TOTAL_CELLS, 4, 0, 0)
    rep = scan(*cv.frame())
    assert rep.result is None and rep.header is None and rep.reason == "no header"


def test_garbage_and_degenerate_inputs():
    rng = random.Random(1234)
    for seed in range(20):
        for corner in CORNERS:
            assert decode(Canvas(320, 48, seed=seed), corner) is None
    assert locate_and_decode(rng.randbytes(320 * 48 * 4), 320, 48, 1280) is None
    assert locate_and_decode(b"\x00" * 4096, 32, 32, 128) is None
    assert locate_and_decode(b"\xff" * 4096, 32, 32, 128) is None
    assert locate_and_decode(b"", 0, 0, 0) is None
    assert locate_and_decode(b"", 256, 32, 1024) is None
    assert locate_and_decode(b"\x00" * 10, 256, 32, 1024) is None  # shorter than declared
    assert locate_and_decode(b"\x00" * 4096, 32, 32, 64) is None  # stride < width * 4
    assert locate_and_decode(b"\x00" * 4, 1, 1, 4) is None
    assert locate_and_decode(b"\x00" * 4096, -5, 32, 128) is None
    full = make(REAL_FRAME)[0].frame()
    assert locate_and_decode(*full, threshold=0) is None  # invalid threshold
    assert scan(*full, corner="MIDDLE").reason == "bad corner"
    assert scan(*full, corner="topleft").result is not None  # case-insensitive


def test_checkerboard_and_stripes_are_bounded():
    for period, corner in ((1, "TOPLEFT"), (1, "BOTTOMRIGHT"), (3, "TOPLEFT"), (3, "BOTTOMLEFT")):
        cv = Canvas(320, 48, background="black")
        for y in range(48):
            for x in range(320):
                cv.put(x, y, (255, 0, 255) if ((x // period) + (y if period == 1 else 0)) % 2 == 0 else (0, 255, 0))
        t = time.perf_counter()
        assert decode(cv, corner) is None
        assert time.perf_counter() - t < 0.25


def test_header_with_nothing_valid_after_it():
    cv = Canvas(320, 48, seed=8)
    cv.cells(list(p2.HEADER) + [(0, 255, 0)] * 3 + [(255, 255, 255)] * 4, 11, 4, 0, 0)
    rep = scan(*cv.frame())
    assert rep.result is None and rep.header is None  # the known prefix never matched


# ---------------------------------------------------- corner and region --

@pytest.mark.parametrize("corner", CORNERS)
def test_max_offset_respected(corner):
    frame = frame_of_size(60, seed=3)
    cv, _grid, _ = make(frame, cols=32, pitch=2, corner=corner, off=(40, 40), height=120, seed=4)
    assert decode(cv, corner, max_offset=48) is not None
    rep = scan(*cv.frame(), corner, max_offset=30)
    assert rep.result is None and rep.reason in ("wrong corner", "no header")


def test_strip_at_the_other_corner_is_not_taken():
    frame = frame_of_size(60, seed=3)
    cv, _grid, _ = make(frame, cols=32, pitch=3, corner="TOPLEFT", off=(1, 1), width=640, height=200, seed=4)
    assert decode(cv, "TOPLEFT") is not None
    assert decode(cv, "BOTTOMRIGHT") is None


def test_truncated_strip_reports_needed_size():
    frame = frame_of_size(264, seed=2)  # the largest frame (262 before the 4-byte check)
    big, grid, _ = make(frame, cols=64, pitch=8, off=(10, 2), width=540, height=110, seed=2)
    assert grid.rows == 12
    buf, w, h, stride = big.frame()
    narrow = b"".join(buf[y * stride:y * stride + 320 * 4] for y in range(h))  # 10 + 512 px strip, 320 px region
    rep = scan(narrow, 320, h, 320 * 4)
    assert rep.result is None and rep.reason == "strip truncated"
    assert rep.needed_width is not None and rep.needed_width >= 10 + 64 * 8
    assert rep.header is not None and rep.header.cols == 64 and rep.header.rows == 12
    short = buf[:48 * stride]  # 2 + 96 px tall strip, 48 px region
    rep = scan(short, w, 48, stride)
    assert rep.reason == "strip truncated" and rep.needed_height is not None and rep.needed_height >= 98


def test_two_strips_returns_the_top_one():
    a, b = frame_of_size(40, seed=1), frame_of_size(40, seed=2)
    ga, gb = p2.encode_cells(a, p2.Geometry(32)), p2.encode_cells(b, p2.Geometry(32))
    cv = Canvas(320, 64, seed=31)
    cv.cells(ga.cells, 32, 3, 0, 0)
    cv.cells(gb.cells, 32, 3, 0, 30)
    assert decode(cv).frame.raw == a


def test_stride_padding_and_input_types():
    cv, _grid, _ = make(REAL_FRAME, cols=48, pitch=3, off=(4, 2), width=200, seed=9)
    buf, w, h, stride = cv.frame()
    pad = 20
    padded = b"".join(buf[y * stride:(y + 1) * stride] + b"\x07" * pad for y in range(h))
    res = locate_and_decode(padded, w, h, stride + pad)
    assert res is not None and res.frame.raw == REAL_FRAME and (res.x, res.y) == (4, 2)
    assert locate_and_decode(bytearray(buf), w, h, stride).frame.raw == REAL_FRAME
    assert locate_and_decode(memoryview(buf), w, h, stride).frame.raw == REAL_FRAME


def test_scan_report_reasons():
    assert scan(*Canvas(320, 48, seed=3).frame()).reason == "no header"
    assert scan(b"", 0, 0, 0).reason == "bad buffer"
    # a confirmed header whose payload is damaged
    cv, grid, _ = make(REAL_FRAME, cols=64, pitch=3, off=(0, 0), seed=12)
    cv.fill(3 * 20, 3, 3 * 40, 6, (0, 0, 0))
    rep = scan(*cv.frame())
    assert rep.result is None and rep.reason == "payload invalid"
    assert rep.header is not None and (rep.header.cols, rep.header.rows) == (64, grid.rows)
    ok = scan(*make(REAL_FRAME)[0].frame())
    assert ok.reason == "ok" and ok.header is not None and ok.candidates >= 1


# --------------------------------------------------------- randomised --

def _sweep(n: int, seed: int, pitch_fn):
    rng = random.Random(seed)
    misses, wrong = [], 0
    for t in range(n):
        corner = rng.choice(CORNERS)
        cols = rng.choice((32, 48, 64))
        pitch = pitch_fn(rng)
        size = rng.choice([rng.randrange(11, 265), 9, 264])  # was [9..262, 7, 262]: frames grew 2 bytes (F1)
        off = (rng.uniform(0, 30), rng.uniform(0, 10))
        frame = frame_of_size(size, seed=t)
        cv, _grid, _ = make(frame, cols=cols, pitch=pitch, corner=corner, off=off, noise=rng.choice((0, 10, 25)),
                            seed=t)
        res = decode(cv, corner)
        if res is None:
            misses.append((corner, cols, round(pitch, 3), size))
        elif res.frame.raw != frame:
            wrong += 1
    return misses, wrong


def test_randomised_sweep_supported_envelope():
    """Random corners / widths / sizes / offsets / noise with pitches from 1.5 to 9 px, plus
    integer 1-8 px cells: every strip decodes exactly, none decodes wrong."""
    misses, wrong = _sweep(220, 2026, lambda r: r.choice([r.uniform(1.5, 9.0), float(r.randint(1, 8))]))
    assert wrong == 0 and not misses, misses


def test_randomised_sweep_tiny_fractional_pitch():
    """Crisp 1.0-1.5 px pitches are the hardest case (a boundary without a transition can be
    read as a 2 px cell of a slightly different pitch on one- or two-row strips): never a wrong
    frame, and nearly always decoded."""
    misses, wrong = _sweep(100, 77, lambda r: r.uniform(1.0, 1.5))
    assert wrong == 0
    assert len(misses) <= 3, misses


# -------------------------------------------------------------- speed --

def _best_of(fn, n: int = 10) -> float:
    best = float("inf")
    for _ in range(n):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


def test_decode_speed_default_region():
    """The default geometry (64 cells per row, 3 px) with a maximum-size frame (12 rows) in the
    default 320 x 48 capture region, with noise: well under 30 ms in pure Python."""
    frame = frame_of_size(264, seed=77)  # the largest frame (262 before the 4-byte check)
    cv, grid, _ = make(frame, cols=64, pitch=3, off=(2, 1), width=320, height=48, noise=20, seed=77)
    buf, w, h, s = cv.frame()
    assert (w, h, grid.rows) == (320, 48, 12)
    assert locate_and_decode(buf, w, h, s) is not None
    assert _best_of(lambda: locate_and_decode(buf, w, h, s)) < 0.030


@pytest.mark.parametrize("corner", CORNERS)
def test_decode_speed_garbage_region(corner):
    """No strip at all: every row is searched (bottom corners scan the whole region)."""
    buf, w, h, s = Canvas(320, 48, seed=78).frame()
    assert locate_and_decode(buf, w, h, s, corner) is None
    assert _best_of(lambda: locate_and_decode(buf, w, h, s, corner)) < 0.030


def test_decode_speed_fractional_and_bottom_corner():
    frame = frame_of_size(150, seed=79)
    cv, _grid, _ = make(frame, cols=64, pitch=3.75, corner="BOTTOMRIGHT", off=(3, 2), width=320, height=48,
                        noise=25, seed=79)
    buf, w, h, s = cv.frame()
    assert locate_and_decode(buf, w, h, s, "BOTTOMRIGHT") is not None
    assert _best_of(lambda: locate_and_decode(buf, w, h, s, "BOTTOMRIGHT")) < 0.030


def test_decode_speed_small_fractional_cells():
    """1.25 px cells need the exact (slowest) lattice solver; still inside the frame budget."""
    frame = frame_of_size(100, seed=114)
    cv, _grid, _ = make(frame, cols=64, pitch=1.25, off=(2.8, 1), width=320, height=48, seed=114)
    buf, w, h, s = cv.frame()
    assert locate_and_decode(buf, w, h, s) is not None
    assert _best_of(lambda: locate_and_decode(buf, w, h, s), n=5) < 0.060


def test_module_exports():
    assert set(beacon2.__all__) >= {"locate_and_decode", "scan", "Beacon2Result", "ScanReport", "HeaderHit",
                                    "CAPTURE_WIDTH", "CAPTURE_HEIGHT", "capture_size"}
    assert beacon2.CORNERS == p2.CORNERS == ("TOPLEFT", "TOPRIGHT", "BOTTOMLEFT", "BOTTOMRIGHT")


# ------------------------------------------------- v1.2 capture region --

def test_capture_region_covers_every_v12_strip():
    """DESIGN-v1.2 2.3: the top-left 512 x 48 px hold the widest (112 x 4 px = 448 px) and the
    tallest (9 px) strip; a magnified capture grows the region by the same factor."""
    assert (beacon2.CAPTURE_WIDTH, beacon2.CAPTURE_HEIGHT) == (512, 48) == beacon2.capture_size(1.0)
    assert beacon2.MAX_STRIP_WIDTH == p2.MAX_COLS * 4 == 448 and beacon2.MAX_STRIP_HEIGHT == p2.MAX_STRIP_PX == 9
    for scale in (1.0, 1.25, 1.5, 2.0):
        w, h = beacon2.capture_size(scale)
        assert w >= beacon2.MAX_STRIP_WIDTH * scale and h >= beacon2.MAX_STRIP_HEIGHT * scale
    assert beacon2.capture_size(0.5) == (512, 48)


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5], ids=["100%", "125%", "150%"])
@pytest.mark.parametrize("px", [2, 3, 4])
def test_adaptive_strips_decode_in_the_capture_region(px, scale):
    """Strips as the v1.2 addon draws them -- adaptive columns, within the 9 px cap, at the client
    area's (0, 0) -- in the 512 x 48 region (magnified with the capture), every frame size that
    fits the cap, on a busy background."""
    w, h = beacon2.capture_size(scale)
    # the frames the addon draws, each 2 bytes longer with the 4-byte check (F1): HELLO 9, HELLO +
    # placeKey 13, + PREFS 15 / 19, CONFIG 23 / 35, CANCEL 24, one-row YouTube 34, Capacity(1) 39,
    # the irreducible 45, ..., and the 2 / 3 / 4 px capacities 165 / 123 / 81 (unchanged)
    sizes = [n for n in (9, 11, 13, 15, 19, 23, 24, 34, 35, 39, 45, 61, 62, 81, 88, 97, 115, 123, 143, 165)
             if n <= p2.capacity(p2.rows_cap(px))]
    for size in sizes:
        frame = frame_of_size(size, seed=size * 7 + px)
        grid = p2.encode_cells(frame, p2.Geometry.beacon(px))
        assert (grid.rows, grid.cols) == p2.adaptive_layout(size) and grid.height_px <= 9
        cv = Canvas(w, h, seed=size + px)
        cv.cells(grid.cells, grid.cols, px * scale, 0, 0)
        res = assert_decoded(cv, frame)
        assert (res.rows, res.cols) == (grid.rows, grid.cols)
        assert res.cell_px == pytest.approx(px * scale, abs=0.06)


def test_overflowing_strip_of_two_2px_rows_decodes():
    """The clearance guard's last resort: 45 bytes (placeKey, start, length, Spotify id; 43 with
    the old 2-byte trailer) in two rows of 2 px cells."""
    frame = p2.encode_frame(p2.build_frame(EventType.FIRE, seq=3, drop_id="zone:1429",
                                           spotify="4uLU6hMCjMI75M1A2tKUQC", start_ms=1000, length_ms=60000,
                                           max_bytes=39, prefer="spotify"))
    assert len(frame) == 45  # was 43: the 4-byte check (F1)
    grid = p2.encode_cells(frame, p2.Geometry.beacon(2, 1))
    assert grid.overflow and grid.rows == 2
    cv = Canvas(512, 48, seed=43)
    cv.cells(grid.cells, grid.cols, 2, 0, 0)
    assert_decoded(cv, frame)
