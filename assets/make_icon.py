"""Generate ``assets/NeedleDropper.ico``: a vinyl record with the needle dropped on it.

Standard library only (no Pillow). Every size is rendered separately with 4x4
supersampling, so strokes stay legible at 16 px. Sizes up to 64 px are stored as
32-bit BMP entries, larger ones as PNG (the usual layout of Windows icons).

    python assets\\make_icon.py                      # writes assets\\NeedleDropper.ico
    python assets\\make_icon.py out.ico --png p.png  # also a 256 px PNG preview
"""
from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_MIN_SIZE = 128  # entries this large are PNG-compressed
SUPERSAMPLE = 4

# Palette (title-card warm off-white, amber label, charcoal vinyl).
VINYL = (22, 22, 26)
GROOVE = (44, 44, 52)
RIM = (92, 92, 104)
LABEL = (227, 163, 59)
LABEL_RING = (176, 116, 30)
ARM = (236, 233, 224)
HEAD = (248, 246, 240)
PIVOT = (200, 198, 192)
PIVOT_CORE = (120, 118, 112)
OUTLINE = (34, 34, 40)

# Geometry in unit coordinates (x right, y down).
CENTER = (0.43, 0.57)
RADIUS = 0.415
LABEL_R = 0.145
HOLE_R = 0.022
PIVOT_C = (0.845, 0.145)
STYLUS = (0.43 + 0.30 * math.cos(math.radians(-24)), 0.57 + 0.30 * math.sin(math.radians(-24)))

Color = tuple[int, int, int]


def _seg_dist(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _towards(a: tuple[float, float], b: tuple[float, float], dist: float) -> tuple[float, float]:
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    return a[0] + (b[0] - a[0]) * dist / length, a[1] + (b[1] - a[1]) * dist / length


class Shapes:
    """Size-dependent stroke widths (minimums in pixels keep small icons readable)."""

    def __init__(self, size: int) -> None:
        px = 1.0 / size
        self.rim_w = max(0.02, 1.0 * px)
        self.arm_half = max(0.02, 0.75 * px)
        self.head_half = max(0.042, 1.3 * px)
        self.pivot_r = max(0.075, 2.2 * px)
        self.pivot_core_r = self.pivot_r * 0.45
        self.label_ring_w = max(0.012, 0.6 * px)
        self.outline_w = max(0.012, 0.6 * px)
        self.grooves = size >= 32
        self.head_a = _towards(STYLUS, PIVOT_C, max(0.075, 2.0 * px))
        self.head_b = _towards(STYLUS, PIVOT_C, -0.012)

    def sample(self, u: float, v: float) -> tuple[Color, float] | None:
        """(color, alpha) of the topmost shape at (u, v), or None for transparent."""
        hit: tuple[Color, float] | None = None
        d = math.hypot(u - CENTER[0], v - CENTER[1])
        if d <= RADIUS:
            color = VINYL
            if d >= RADIUS - self.rim_w:
                color = RIM
            elif self.grooves and LABEL_R + 0.03 < d < RADIUS - 0.03 and (d * 90.0) % 4.0 < 1.0:
                color = GROOVE
            if d <= LABEL_R:
                color = LABEL_RING if d >= LABEL_R - self.label_ring_w else LABEL
            if d <= HOLE_R:
                color = VINYL
            # soft reflection band across the vinyl
            angle = math.atan2(v - CENTER[1], u - CENTER[0])
            if LABEL_R < d < RADIUS - self.rim_w and abs(math.sin(angle + math.radians(40))) < 0.22:
                color = tuple(min(255, int(c * 0.8 + 255 * 0.2 * 0.35)) for c in color)  # type: ignore[assignment]
            hit = (color, 1.0)
        # tonearm, headshell and pivot, each with a thin dark outline (visible on light backgrounds)
        ow = self.outline_w
        da = _seg_dist(u, v, PIVOT_C, STYLUS)
        if da <= self.arm_half + ow:
            hit = (ARM if da <= self.arm_half else OUTLINE, 1.0)
        dh = _seg_dist(u, v, self.head_a, self.head_b)
        if dh <= self.head_half + ow:
            hit = (HEAD if dh <= self.head_half else OUTLINE, 1.0)
        dp = math.hypot(u - PIVOT_C[0], v - PIVOT_C[1])
        if dp <= self.pivot_r + ow:
            if dp > self.pivot_r:
                hit = (OUTLINE, 1.0)
            else:
                hit = (PIVOT_CORE if dp <= self.pivot_core_r else PIVOT, 1.0)
        return hit


def render(size: int, ss: int = SUPERSAMPLE) -> list[tuple[int, int, int, int]]:
    """Top-down RGBA pixels of the icon at ``size`` x ``size``."""
    shapes = Shapes(size)
    n = size * ss
    acc = [[0.0, 0.0, 0.0, 0.0] for _ in range(size * size)]
    for sy in range(n):
        v = (sy + 0.5) / n
        row = (sy // ss) * size
        for sx in range(n):
            hit = shapes.sample((sx + 0.5) / n, v)
            if hit is None:
                continue
            (r, g, b), a = hit
            cell = acc[row + sx // ss]
            cell[0] += r * a
            cell[1] += g * a
            cell[2] += b * a
            cell[3] += a
    samples = float(ss * ss)
    out = []
    for r, g, b, a in acc:
        if a <= 0:
            out.append((0, 0, 0, 0))
        else:
            out.append((round(r / a), round(g / a), round(b / a), round(255 * a / samples)))
    return out


def png_bytes(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        for r, g, b, a in pixels[y * size:(y + 1) * size]:
            raw += bytes((r, g, b, a))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def dib_bytes(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    """32-bit BMP icon image: BITMAPINFOHEADER + bottom-up BGRA + 1-bit AND mask."""
    mask_stride = ((size + 31) // 32) * 4
    xor = bytearray()
    mask = bytearray()
    for y in range(size - 1, -1, -1):
        row = pixels[y * size:(y + 1) * size]
        for r, g, b, a in row:
            xor += bytes((b, g, r, a))
        bits = bytearray(mask_stride)
        for x, (_, _, _, a) in enumerate(row):
            if a == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + bytes(xor) + bytes(mask)


def ico_bytes(images: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = len(head) + 16 * len(images)
    entries = bytearray()
    blobs = bytearray()
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    return head + bytes(entries) + bytes(blobs)


def build_icon(sizes: tuple[int, ...] = SIZES) -> tuple[bytes, dict[int, list[tuple[int, int, int, int]]]]:
    rendered = {s: render(s) for s in sizes}
    images = [(s, png_bytes(s, px) if s >= PNG_MIN_SIZE else dib_bytes(s, px)) for s, px in rendered.items()]
    return ico_bytes(images), rendered


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default=str(here / "NeedleDropper.ico"))
    ap.add_argument("--png", metavar="FILE", help="also write the 256 px image as a PNG preview")
    args = ap.parse_args(argv)
    data, rendered = build_icon()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"wrote {out} ({len(data)} bytes, sizes {', '.join(map(str, SIZES))})")
    if args.png:
        biggest = max(rendered)
        Path(args.png).write_bytes(png_bytes(biggest, rendered[biggest]))
        print(f"wrote {args.png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
