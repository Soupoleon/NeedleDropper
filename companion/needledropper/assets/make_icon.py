"""Generate the companion's notification-area (tray) icons, standard library only.

Writes, next to this script:

* ``NeedleDropper.ico``        - a vinyl record with the needle dropped on it (the
  normal tray icon);
* ``NeedleDropper-paused.ico`` - the same record with the tone arm parked beside it
  and a grey label (needle drops paused).

Each file holds 16, 20, 24, 32, 40, 48 and 64 px images. The small sizes cover the
notification-area icon (``SM_CXSMICON``) at 100/125/150/200/250/300 % display
scaling, the larger ones the balloon/toast icon. Every size is rendered on its own
with 4 x 4 supersampling (strokes keep a minimum width in pixels, so 16 px stays
legible) and stored as a 32-bit BMP entry with an AND mask, which ``LoadImageW``
reads on every Windows version.

The design follows the exe icon (``assets/make_icon.py`` at the repository root):
charcoal vinyl, amber label, off-white tone arm. For the notification area it adds a
light rim, so the dark record still reads on the dark Windows 11 taskbar, and one dark
outline around the whole tone arm for light taskbars.

The output is deterministic: geometry is given as literal numbers, colours are summed
as integers, and the per-sample maths uses only ``+ - * /`` and ``sqrt``, which IEEE 754
rounds identically on every platform. A re-run therefore produces byte-identical
files, and ``tests/test_tray.py`` checks the committed icons against this script.

    python companion\\needledropper\\assets\\make_icon.py                  # (re)write both .ico files
    python companion\\needledropper\\assets\\make_icon.py --check          # exit 1 if a file is stale
    python companion\\needledropper\\assets\\make_icon.py --preview p.png  # contact sheet, dark + light
"""
from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: Image sizes stored in every .ico (pixels, square).
SIZES: Tuple[int, ...] = (16, 20, 24, 32, 40, 48, 64)
#: Samples per pixel along each axis.
SUPERSAMPLE = 4
#: Icon variants and their file names.
VARIANTS: Tuple[str, ...] = ("playing", "paused")
FILENAMES: Dict[str, str] = {"playing": "NeedleDropper.ico", "paused": "NeedleDropper-paused.ico"}

Color = Tuple[int, int, int]
Pixel = Tuple[int, int, int, int]  # straight (non-premultiplied) RGBA
Point = Tuple[float, float]

# ------------------------------------------------------------------ palette --
VINYL: Color = (24, 24, 28)
GROOVE: Color = (60, 60, 70)
RIM: Color = (168, 168, 180)  # light edge: keeps the record visible on a dark taskbar
LABEL: Color = (227, 163, 59)  # amber, as on the exe icon and the in-game title card
LABEL_RING: Color = (176, 116, 30)
LABEL_PAUSED: Color = (156, 156, 160)
LABEL_RING_PAUSED: Color = (108, 108, 114)
ARM: Color = (238, 236, 228)
HEAD: Color = (250, 248, 242)
PIVOT: Color = (206, 204, 198)
PIVOT_CORE: Color = (112, 110, 106)
OUTLINE: Color = (20, 20, 24)  # around the tone arm: keeps it visible on a light taskbar

# ----------------------------------------------------------------- geometry --
# Unit square, x to the right, y down. Literal numbers only (see the module docstring).
CENTER: Point = (0.44, 0.56)
RADIUS = 0.42
LABEL_R = 0.15
HOLE_R = 0.03
GROOVE_RINGS: Tuple[float, ...] = (0.215, 0.285, 0.355)
PIVOT_C: Point = (0.84, 0.16)
#: Needle down on the record, about 0.28 from the centre.
STYLUS_PLAYING: Point = (0.68, 0.42)
#: Needle parked on its rest, clear of the record's edge.
STYLUS_PARKED: Point = (0.905, 0.45)


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    dx = ax - bx
    dy = ay - by
    return math.sqrt(dx * dx + dy * dy)


def _seg_dist(px: float, py: float, a: Point, b: Point) -> float:
    """Distance from (px, py) to the segment a-b."""
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    qx = ax + t * dx - px
    qy = ay + t * dy - py
    return math.sqrt(qx * qx + qy * qy)


def _towards(a: Point, b: Point, dist: float) -> Point:
    """The point ``dist`` away from ``a`` in the direction of ``b`` (negative: away from b)."""
    length = _dist(a[0], a[1], b[0], b[1])
    return a[0] + (b[0] - a[0]) * dist / length, a[1] + (b[1] - a[1]) * dist / length


class Strokes:
    """Size-dependent widths in unit coordinates; the pixel minimums keep 16 px legible.

    (A plain class rather than a dataclass, so the script also loads when imported
    straight from its file path without being registered in ``sys.modules``.)
    """

    __slots__ = ("rim", "groove_half", "arm_half", "head_half", "head_len", "pivot_r", "pivot_core_r",
                 "outline", "label_ring", "grooves")

    def __init__(self, size: int) -> None:
        px = 1.0 / size
        self.rim = max(0.035, 1.0 * px)
        self.groove_half = max(0.008, 0.3 * px)
        self.arm_half = max(0.022, 0.7 * px)
        self.head_half = max(0.045, 1.25 * px)
        self.head_len = max(0.09, 2.2 * px)
        self.pivot_r = max(0.075, 2.0 * px)
        self.pivot_core_r = self.pivot_r * 0.45
        self.outline = max(0.014, 0.55 * px)
        self.label_ring = max(0.014, 0.5 * px)
        self.grooves = size >= 32


class Glyph:
    """The icon at one size: :meth:`sample` gives the colour at a point (None = transparent)."""

    def __init__(self, size: int, variant: str = "playing") -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown icon variant {variant!r} (expected one of {', '.join(VARIANTS)})")
        self.strokes = Strokes(size)
        paused = variant == "paused"
        self.stylus = STYLUS_PARKED if paused else STYLUS_PLAYING
        self.label = LABEL_PAUSED if paused else LABEL
        self.label_ring = LABEL_RING_PAUSED if paused else LABEL_RING
        # The headshell is a short, thicker segment at the stylus end of the arm.
        self.head_a = _towards(self.stylus, PIVOT_C, self.strokes.head_len)
        self.head_b = _towards(self.stylus, PIVOT_C, -0.012)

    def _record(self, u: float, v: float) -> Optional[Color]:
        s = self.strokes
        d = _dist(u, v, CENTER[0], CENTER[1])
        if d > RADIUS:
            return None
        if d >= RADIUS - s.rim:
            return RIM
        if d <= HOLE_R:
            return VINYL
        if d <= LABEL_R:
            return self.label_ring if d >= LABEL_R - s.label_ring else self.label
        if s.grooves:
            for ring in GROOVE_RINGS:
                if abs(d - ring) <= s.groove_half:
                    return GROOVE
        return VINYL

    def sample(self, u: float, v: float) -> Optional[Color]:
        s = self.strokes
        # Tone arm parts, top-most first; one outline around their union.
        dp = _dist(u, v, PIVOT_C[0], PIVOT_C[1])
        if dp <= s.pivot_r:
            return PIVOT_CORE if dp <= s.pivot_core_r else PIVOT
        dh = _seg_dist(u, v, self.head_a, self.head_b)
        if dh <= s.head_half:
            return HEAD
        da = _seg_dist(u, v, PIVOT_C, self.stylus)
        if da <= s.arm_half:
            return ARM
        ow = s.outline
        if dp <= s.pivot_r + ow or dh <= s.head_half + ow or da <= s.arm_half + ow:
            return OUTLINE
        return self._record(u, v)


def render(size: int, variant: str = "playing", ss: int = SUPERSAMPLE) -> List[Pixel]:
    """Top-down RGBA pixels of the icon at ``size`` x ``size``."""
    glyph = Glyph(size, variant)
    n = size * ss
    inv = 1.0 / n
    acc = [[0, 0, 0, 0] for _ in range(size * size)]  # integer sums: r, g, b, covered samples
    for sy in range(n):
        v = (sy + 0.5) * inv
        row = (sy // ss) * size
        for sx in range(n):
            color = glyph.sample((sx + 0.5) * inv, v)
            if color is None:
                continue
            cell = acc[row + sx // ss]
            cell[0] += color[0]
            cell[1] += color[1]
            cell[2] += color[2]
            cell[3] += 1
    total = ss * ss
    out: List[Pixel] = []
    for r, g, b, k in acc:
        if k == 0:
            out.append((0, 0, 0, 0))
        else:  # rounded integer division (half up)
            out.append(((2 * r + k) // (2 * k), (2 * g + k) // (2 * k), (2 * b + k) // (2 * k),
                        (510 * k + total) // (2 * total)))
    return out


# --------------------------------------------------------------- encoders --
def dib_bytes(size: int, pixels: Sequence[Pixel]) -> bytes:
    """One 32-bit icon image: BITMAPINFOHEADER, bottom-up BGRA rows, 1-bit AND mask."""
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
    # biSize, biWidth, biHeight (XOR + AND halves), biPlanes, biBitCount, biCompression=BI_RGB,
    # biSizeImage, biXPelsPerMeter, biYPelsPerMeter, biClrUsed, biClrImportant
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + bytes(xor) + bytes(mask)


def ico_bytes(images: Sequence[Tuple[int, bytes]]) -> bytes:
    """An .ico file (ICONDIR + ICONDIRENTRYs + images) from ``(size, image bytes)`` pairs."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = len(head) + 16 * len(images)
    entries = bytearray()
    blobs = bytearray()
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    return head + bytes(entries) + bytes(blobs)


def build_ico(variant: str = "playing", sizes: Sequence[int] = SIZES) -> bytes:
    """The complete .ico file for one variant."""
    return ico_bytes([(size, dib_bytes(size, render(size, variant))) for size in sizes])


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    """A truecolour + alpha PNG from top-down RGBA bytes."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        raw += rgba[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


#: Taskbar colours used by the preview (Windows 11 dark and light themes).
PREVIEW_BACKGROUNDS: Tuple[Color, ...] = ((32, 32, 32), (243, 243, 243))


def contact_sheet(scale: int = 4, gap: int = 12) -> bytes:
    """PNG preview: every variant and size, magnified ``scale`` x, on dark and light taskbars."""
    rows = [(variant, bg) for variant in VARIANTS for bg in PREVIEW_BACKGROUNDS]
    biggest = max(SIZES)
    width = gap + sum(s * scale + gap for s in SIZES)
    row_h = biggest * scale + 2 * gap
    height = row_h * len(rows)
    canvas = bytearray(width * height * 4)
    for r, (variant, bg) in enumerate(rows):
        top = r * row_h
        for y in range(top, top + row_h):
            for x in range(width):
                i = (y * width + x) * 4
                canvas[i:i + 4] = bytes((bg[0], bg[1], bg[2], 255))
        x0 = gap
        for size in SIZES:
            pixels = render(size, variant)
            y0 = top + gap + (biggest - size) * scale // 2
            for py in range(size):
                for px in range(size):
                    cr, cg, cb, ca = pixels[py * size + px]
                    out = bytes(((cr * ca + bg[0] * (255 - ca) + 127) // 255,
                                 (cg * ca + bg[1] * (255 - ca) + 127) // 255,
                                 (cb * ca + bg[2] * (255 - ca) + 127) // 255, 255))
                    for dy in range(scale):
                        base = ((y0 + py * scale + dy) * width + x0 + px * scale) * 4
                        canvas[base:base + 4 * scale] = out * scale
            x0 += size * scale + gap
    return png_bytes(width, height, bytes(canvas))


# --------------------------------------------------------------------- CLI --
def icon_paths(out_dir: Path) -> Dict[str, Path]:
    return {variant: Path(out_dir) / FILENAMES[variant] for variant in VARIANTS}


def write_icons(out_dir: Path) -> List[Path]:
    """(Re)write every variant into ``out_dir``; returns the paths written."""
    written = []
    for variant, path in icon_paths(out_dir).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(build_ico(variant))
        written.append(path)
    return written


def stale_icons(out_dir: Path) -> List[Path]:
    """Icons in ``out_dir`` that are missing or differ from what this script renders."""
    stale = []
    for variant, path in icon_paths(out_dir).items():
        if not path.is_file() or path.read_bytes() != build_ico(variant):
            stale.append(path)
    return stale


def main(argv: Optional[Sequence[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Generate the NeedleDropper tray icons (.ico).")
    ap.add_argument("--out-dir", default=str(here), help="where to write the icons (default: next to this script)")
    ap.add_argument("--check", action="store_true", help="only verify the icons are up to date (exit 1 if not)")
    ap.add_argument("--preview", metavar="PNG", help="also write a magnified contact sheet as a PNG")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    status = 0
    if args.check:
        stale = stale_icons(out_dir)
        for path in stale:
            print(f"stale: {path} (run make_icon.py to regenerate)")
        print("icons are up to date" if not stale else f"{len(stale)} icon(s) out of date")
        status = 1 if stale else 0
    else:
        for path in write_icons(out_dir):
            print(f"wrote {path} ({path.stat().st_size} bytes, sizes {', '.join(map(str, SIZES))})")
    if args.preview:
        Path(args.preview).write_bytes(contact_sheet())
        print(f"wrote {args.preview}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
