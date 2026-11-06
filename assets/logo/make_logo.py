"""Generate the NeedleDropper logo for the addon sites: a vinyl record with the needle dropped on it.

Standard library only (no Pillow), like ``assets/make_icon.py``, whose palette and layout it shares:
a charcoal record with an amber label, and a warm off-white tonearm coming in from a pivot at the
top right. The logo adds a full-bleed dark background, soft shadows, grooves and a little sheen.

Every shape is a signed distance field, sampled once per pixel and anti-aliased by its distance to
the edge, so even the 1024 px master renders in seconds. Stroke widths are in unit coordinates and
sized for the 64 px thumbnails the sites shrink the logo to: there the tonearm is still about 2.5 px
wide and the label about 16 px across.

    python assets\\logo\\make_logo.py                    # needledropper-logo-400.png and -1024.png here
    python assets\\logo\\make_logo.py --sizes 400 --out-dir some\\folder
"""
from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

SIZES = (400, 1024)  # 400 px for CurseForge and Wago; 1024 px as the master

Color = tuple[float, float, float]

# Palette: the icon's colours (make_icon.py), plus the background and the shading tints.
BG_GLOW = (44, 45, 56)       # background, around the light (top left of the record)
BG_EDGE = (14, 14, 19)       # background, in the corners
SHADOW = (4, 4, 7)
VINYL = (22, 22, 26)
GROOVE = (44, 44, 52)
RIM_DARK = (58, 58, 68)      # the rim is make_icon.py's RIM (92, 92, 104), lit from the top left
RIM_LIGHT = (124, 124, 138)
HOLE = (10, 10, 13)
LABEL = (227, 163, 59)       # the one accent colour
LABEL_SHADE = (205, 142, 46)
LABEL_RING = (176, 116, 30)
ARM = (236, 233, 224)        # warm off-white, as on the in-game title card
HEAD = (248, 246, 240)
PIVOT = (200, 198, 192)
PIVOT_CORE = (120, 118, 112)
OUTLINE = (34, 34, 40)

# Layout: make_icon.py's geometry, scaled about the middle of its artwork so the logo has a margin
# of about 9 % (the sites round the corners of the avatar; nothing important sits there).
FIT = 0.90


def _fit(x: float, y: float) -> tuple[float, float]:
    return 0.5 + FIT * (x - 0.4675), 0.5 + FIT * (y - 0.5275)


CENTER = _fit(0.43, 0.57)
RADIUS = 0.415 * FIT
LABEL_R = 0.145 * FIT
HOLE_R = 0.022 * FIT
PIVOT_C = _fit(0.845, 0.145)
_STYLUS_ANGLE = math.radians(-24)
STYLUS = (CENTER[0] + 0.30 * FIT * math.cos(_STYLUS_ANGLE), CENTER[1] + 0.30 * FIT * math.sin(_STYLUS_ANGLE))

# Strokes and details (unit coordinates).
RIM_W = 0.011
LABEL_RING_W = 0.008
PRINT_RING = (0.64 * LABEL_R, 0.0045)                  # radius, width: a thin printed ring on the label
GROOVE_START = LABEL_R + 0.018
GROOVE_END = RADIUS - RIM_W - 0.012
GROOVE_STEP = 0.0105
GROOVE_W = 0.0032
GROOVE_GAPS = (6, 12)                                  # skipped grooves: the gaps between tracks
GROOVE_MIX = 0.8
ARM_HALF = 0.0195
HEAD_HALF = 0.034
HEAD_BACK = 0.068                                       # the headshell reaches this far up the arm...
HEAD_FRONT = 0.010                                      # ...and this far past the stylus
PIVOT_R = 0.066
PIVOT_CORE_R = 0.029
OUTLINE_W = 0.006

# Light comes from the top left: shadows fall to the bottom right.
LIGHT = math.radians(-135)
DISC_SHADOW = (0.010, 0.018, 0.030, 0.60)              # offset x, offset y, blur, opacity
ARM_SHADOW = (0.008, 0.014, 0.012, 0.50)
SHEEN_WIDTH = 0.42                                      # |sin| of the angle off the sheen axis
SHEEN_MIX = 0.16
BG_FOCUS = (0.42, 0.40)
BG_SPAN = 0.78


def _towards(a: tuple[float, float], b: tuple[float, float], dist: float) -> tuple[float, float]:
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    return a[0] + (b[0] - a[0]) * dist / length, a[1] + (b[1] - a[1]) * dist / length


HEAD_A = _towards(STYLUS, PIVOT_C, HEAD_BACK)
HEAD_B = _towards(STYLUS, PIVOT_C, -HEAD_FRONT)


def _seg_dist(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _mix(a: Color, b: Color, t: float) -> Color:
    if t <= 0:
        return a
    if t >= 1:
        return b
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t


def _smooth(t: float) -> float:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


def _cover(d: float, px: float) -> float:
    """Coverage of a pixel by a shape whose signed distance (negative inside) is ``d``."""
    c = 0.5 - d / px
    return 0.0 if c < 0 else 1.0 if c > 1 else c


def _band(r: float, lo: float, hi: float, px: float) -> float:
    """Coverage of a pixel at radius ``r`` by the ring ``lo``..``hi`` (exact across the ring)."""
    overlap = min(r + px / 2, hi) - max(r - px / 2, lo)
    return 0.0 if overlap <= 0 else min(1.0, overlap / px)


def _grooves(r: float, px: float) -> float:
    if r < GROOVE_START - px or r > GROOVE_END + px:
        return 0.0
    reach = px / 2 + GROOVE_W / 2
    last = int((GROOVE_END - GROOVE_START) / GROOVE_STEP)
    lo = max(0, math.floor((r - reach - GROOVE_START) / GROOVE_STEP))
    hi = min(last, math.floor((r + reach - GROOVE_START) / GROOVE_STEP))
    cover = 0.0
    for k in range(lo, hi + 1):
        if k in GROOVE_GAPS:
            continue
        g = GROOVE_START + k * GROOVE_STEP
        cover += _band(r, g - GROOVE_W / 2, g + GROOVE_W / 2, px)
    return min(1.0, cover)


def _record(dx: float, dy: float, r: float, px: float) -> Color:
    """Colour of the record (vinyl, grooves, sheen, rim, label, hole) at offset (dx, dy) from its centre."""
    theta = math.atan2(dy, dx)
    col: Color = VINYL
    g = _grooves(r, px)
    if g > 0:
        col = _mix(col, GROOVE, g * GROOVE_MIX)
    # sheen: two soft wedges along the light's diagonal, brighter on the lit side
    off = abs(math.sin(theta - LIGHT))
    if off < SHEEN_WIDTH:
        lobe = 1.0 if math.cos(theta - LIGHT) > 0 else 0.55
        fade = _smooth((r - LABEL_R) / 0.05) * _smooth((RADIUS - RIM_W - r) / 0.05)
        col = _mix(col, ARM, SHEEN_MIX * lobe * fade * _smooth(1 - off / SHEEN_WIDTH))
    rim = _band(r, RADIUS - RIM_W, RADIUS + px, px)
    if rim > 0:
        col = _mix(col, _mix(RIM_DARK, RIM_LIGHT, 0.5 + 0.5 * math.cos(theta - LIGHT)), rim)
    if r < LABEL_R + px:
        lit = 0.5 + 0.5 * (dx * math.cos(LIGHT) + dy * math.sin(LIGHT)) / LABEL_R
        label = _mix(LABEL_SHADE, LABEL, lit)
        label = _mix(label, LABEL_RING, 0.7 * _band(r, PRINT_RING[0] - PRINT_RING[1] / 2,
                                                       PRINT_RING[0] + PRINT_RING[1] / 2, px))
        label = _mix(label, LABEL_RING, _band(r, LABEL_R - LABEL_RING_W, LABEL_R + px, px))
        col = _mix(col, label, _cover(r - LABEL_R, px))
        col = _mix(col, HOLE, _cover(r - HOLE_R, px))
    return col


def render(size: int) -> bytearray:
    """Top-down RGB bytes of the logo at ``size`` x ``size``."""
    px = 1.0 / size
    cx, cy = CENTER
    sx, sy, sblur, sopacity = DISC_SHADOW
    ax, ay, ablur, aopacity = ARM_SHADOW
    disc_reach = RADIUS + max(abs(sx), abs(sy)) + sblur + px
    arm_pad = PIVOT_R + OUTLINE_W + max(ax, ay) + ablur + px
    arm_box = (min(PIVOT_C[0], STYLUS[0]) - arm_pad, max(PIVOT_C[0], STYLUS[0]) + arm_pad,
               min(PIVOT_C[1], STYLUS[1]) - arm_pad, max(PIVOT_C[1], STYLUS[1]) + arm_pad)
    out = bytearray(size * size * 3)
    i = 0
    for y in range(size):
        v = (y + 0.5) * px
        for x in range(size):
            u = (x + 0.5) * px
            col: Color = _mix(BG_GLOW, BG_EDGE, _smooth(math.hypot(u - BG_FOCUS[0], v - BG_FOCUS[1]) / BG_SPAN))
            dx, dy = u - cx, v - cy
            if abs(dx) < disc_reach and abs(dy) < disc_reach:
                ds = math.hypot(dx - sx, dy - sy) - RADIUS
                if ds < sblur:
                    col = _mix(col, SHADOW, sopacity * _smooth(0.5 - ds / (2 * sblur)))
                r = math.hypot(dx, dy)
                cover = _cover(r - RADIUS, px)
                if cover > 0:
                    col = _mix(col, _record(dx, dy, r, px), cover)
            if arm_box[0] < u < arm_box[1] and arm_box[2] < v < arm_box[3]:
                # one soft shadow for tonearm, headshell and pivot together
                su, sv = u - ax, v - ay
                ds = min(_seg_dist(su, sv, PIVOT_C, STYLUS) - ARM_HALF,
                         _seg_dist(su, sv, HEAD_A, HEAD_B) - HEAD_HALF,
                         math.hypot(su - PIVOT_C[0], sv - PIVOT_C[1]) - PIVOT_R)
                if ds < ablur:
                    col = _mix(col, SHADOW, aopacity * _smooth(0.5 - ds / (2 * ablur)))
                # tonearm, headshell and pivot, each with a thin dark outline where they overlap
                d = _seg_dist(u, v, PIVOT_C, STYLUS)
                col = _mix(col, OUTLINE, _cover(d - ARM_HALF - OUTLINE_W, px))
                col = _mix(col, ARM, _cover(d - ARM_HALF, px))
                d = _seg_dist(u, v, HEAD_A, HEAD_B)
                col = _mix(col, OUTLINE, _cover(d - HEAD_HALF - OUTLINE_W, px))
                col = _mix(col, HEAD, _cover(d - HEAD_HALF, px))
                d = math.hypot(u - PIVOT_C[0], v - PIVOT_C[1])
                if d < PIVOT_R + OUTLINE_W + px:
                    lit = 0.5 + 0.5 * ((u - PIVOT_C[0]) * math.cos(LIGHT) + (v - PIVOT_C[1]) * math.sin(LIGHT)) / PIVOT_R
                    col = _mix(col, OUTLINE, _cover(d - PIVOT_R - OUTLINE_W, px))
                    col = _mix(col, _mix(PIVOT_CORE, PIVOT, 0.55 + 0.45 * lit), _cover(d - PIVOT_R, px))
                    col = _mix(col, PIVOT_CORE, _cover(d - PIVOT_CORE_R, px))
            for c in col:
                out[i] = 0 if c <= 0 else 255 if c >= 255 else int(c + 0.5)
                i += 1
    return out


def png_bytes(size: int, rgb: bytes) -> bytes:
    """An 8-bit RGB PNG (sRGB chunk, "Up" filter on every row) of a ``size`` x ``size`` image."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    stride = size * 3
    raw = bytearray()
    prev = bytes(stride)
    for y in range(size):
        row = rgb[y * stride:(y + 1) * stride]
        raw.append(2)  # filter: Up
        raw += bytes((a - b) & 0xFF for a, b in zip(row, prev))
        prev = row
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"sRGB", b"\x00")
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sizes", type=int, nargs="+", default=list(SIZES), metavar="PX",
                    help="sizes to write (default: %(default)s)")
    ap.add_argument("--out-dir", default=str(here), help="where needledropper-logo-<size>.png goes")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for size in args.sizes:
        if size < 16:
            ap.error(f"size {size} is too small")
        data = png_bytes(size, render(size))
        out = out_dir / f"needledropper-logo-{size}.png"
        out.write_bytes(data)
        print(f"wrote {out} ({size} x {size}, {len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
