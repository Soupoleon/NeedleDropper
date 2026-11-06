"""Pixel beacon v2: locate and decode the addon's multi-row colour strip (docs/PROTOCOL-v2.md s4).

The v2 strip is a grid of ``cols`` x ``rows`` square cells anchored to one corner of the WoW
client area. Row 0 starts with ``MAGENTA GREEN MAGENTA GREEN``, then three geometry cells
(``cols/16``, ``(rows-1)//8``, ``(rows-1)%8``), then the whitened frame bits, 3 per cell
(R, G, B; see :mod:`needledropper.protocol2`). Rows read left-to-right, top-to-bottom for
every corner. :func:`locate_and_decode` finds such a strip in a captured BGRA buffer and
returns the decoded :class:`~needledropper.protocol2.Frame`; :func:`scan` additionally says
*why* nothing decoded (for diagnostics and capture-region sizing).

Decoder outline (the robustness techniques of the v1 locator, :mod:`.pixel`, generalised):

1. Classify every pixel to a 3-bit code ``(R>=t)<<2 | (G>=t)<<1 | (B>=t)`` with C-speed
   ``bytes.translate`` + big-integer addition and render rows as letter strings
   (``k b g c r m y w``).
2. Find header candidates with a regex over each scanned row (runs ``m+ g+ m+ g+``, up to two
   blended pixels between runs, first three runs of similar length L) and cluster them over
   consecutive pixel rows; the rows of a cluster are the pixel rows of strip row 0.
3. Fit the column lattice ``x = x0 + k * pitch`` by progressive least squares, starting from
   the header boundaries and adding colour transitions of ever more cells: a sub-pixel *float*
   pitch, so non-integer cell sizes (display scaling) decode correctly. The column and row
   lattices are separable: every pixel row inside the strip shows the same column boundaries
   and every pixel column the same row boundaries, so transitions are pooled over all rows
   (and, vertically, all columns). Whitened data makes such transitions dense.
4. Read the geometry cells and confirm the candidate with the four fixed (whitened magic and
   version) cells that follow them, before any expensive work. Those cells' colour runs also
   pin the lattice when the geometry cells form one long run (v1 pinned its payload prefix
   the same way).
5. Sample every cell at the middle pixel of its span, de-whiten and validate: header,
   geometry, rows consistent with the frame length, zero pad bits, magic, version, TLV
   structure and the 4-byte frame check (:func:`~needledropper.protocol2.frame_check`) -- a
   partially covered or half-drawn strip yields None.
6. Only if nothing decoded cheaply (bounded work per frame): per-channel thresholds calibrated
   on the header (HDR / tone shifts), small lattice perturbations, and for crisp 1-3 px cells
   an exact rasterisation solver (:func:`_exact_lattices`) that intersects the constraints
   ``t - 0.5 < x0 + k * pitch <= t + 0.5`` of every transition in (x0, pitch) space, assigning
   transitions to boundaries best-first (fewest boundaries without a transition), with the
   known colours of row 0 enforced; the cheapest few lattices are each checksum-validated.

Limits: cells must be at least 1 px; crisp fractional pitches below ~1.2 px on one- or two-row
strips are occasionally ambiguous (a boundary without a transition can be read as a 2 px cell
of a slightly different pitch) and then fail rather than decode wrongly. Buffers are limited
to 4096 x 2048 px; capture the corner region, not the whole window.

Capture assumptions (the v1.2 addon, DESIGN-v1.2 2.3 / docs/PROTOCOL-v2.md 4.5):

* The strip sits at the client area's top-left pixel (0, 0); ``beaconCorner`` can move it to
  another corner, and the capture must then use that corner (``corner=``).
* Cells are 2, 3 (default) or 4 physical px; the strip is 16..112 cells wide, chosen per frame
  (:func:`~needledropper.protocol2.adaptive_layout`), and at most 9 px tall (2 px x 4 rows,
  3 px x 3, 4 px x 2; the clearance guard may lower that, and a frame it cannot shrink enough
  for one row is drawn as two 2 px rows). The widest strip is 112 x 4 = 448 px.
* So the capture region is the top-left :data:`CAPTURE_WIDTH` x :data:`CAPTURE_HEIGHT`
  (512 x 48) px of the client area, in physical pixels. A capture that is magnified by
  display scaling (DPI virtualisation) must grow the region by the same factor:
  :func:`capture_size` (a 448 px strip is 672 px wide at 150 %). Nothing about the geometry
  has to be configured: header and geometry cells say everything, and the pitch is fitted.
* Song frames are on screen only briefly: the full ARM for 3 s, FIRE for at least 3 s
  (then a HELLO with DROP_ACTIVE, 9..19 bytes), CANCEL / PING for at least 1 s. At 4 Hz that is
  about 12 captures for a song frame and 4 for a CANCEL: plenty for the transport's "two
  identical consecutive reads" rule, even with a brief overlay on top of the strip.

Pixel order is **B, G, R, A** (GDI's 32-bpp DIB layout), rows top-down, ``stride`` bytes apart.
"""
from __future__ import annotations

import functools
import heapq
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Sequence

from .. import protocol2 as p2
from ..protocol2 import Frame

log = logging.getLogger(__name__)

__all__ = ["Beacon2Result", "HeaderHit", "ScanReport", "locate_and_decode", "scan", "CORNERS",
           "CAPTURE_WIDTH", "CAPTURE_HEIGHT", "MAX_STRIP_WIDTH", "MAX_STRIP_HEIGHT", "capture_size"]

CORNERS = p2.CORNERS

#: The v1.2 addon's strip: at most 112 cells of 4 px wide, and 9 px tall (a frame squeezed by
#: the clearance guard beyond what it can shrink is two 2 px rows: 4 px).
MAX_STRIP_WIDTH = p2.MAX_COLS * 4  # 448
MAX_STRIP_HEIGHT = p2.MAX_STRIP_PX  # 9
#: Capture region at the beacon's corner of the client area, in physical pixels (DESIGN-v1.2 2.3).
CAPTURE_WIDTH = 512
CAPTURE_HEIGHT = 48


def capture_size(scale: float = 1.0) -> tuple[int, int]:
    """``(width, height)`` of the capture region for a capture magnified by ``scale``
    (1.0 = physical pixels; 1.25 when a 125 % display scale is applied to the capture)."""
    s = max(1.0, float(scale))
    return int(math.ceil(CAPTURE_WIDTH * s)), int(math.ceil(CAPTURE_HEIGHT * s))

_CODE_CHARS = "kbgcrmyw"  # index = R*4 + G*2 + B
_CODE_TO_CHAR = bytes.maketrans(bytes(range(8)), _CODE_CHARS.encode("ascii"))
_CHAR_CODE = {ch: i for i, ch in enumerate(_CODE_CHARS)}
_HEADER_RE = re.compile(r"(?<!m)(?=(m+)([^mg]{0,2})(g+)([^mg]{0,2})(m+)([^mg]{0,2})(g+))")
_RUN_RE = re.compile(r"(.)\1*", re.S)
_PREFIX = "".join(_CODE_CHARS[c] for c in p2.KNOWN_PREFIX_CODES)  # grid cells 7..10
_LEAD = p2.LEAD_CELLS  # 7
_PROBE = _LEAD + len(_PREFIX)  # 11 cells: header, geometry, known prefix
assert all(a != b for a, b in zip(_PREFIX, _PREFIX[1:])), "prefix cells must differ pairwise"
# The known prefix as colour runs (up to two blended pixels between runs); its inner
# boundaries 8, 9, 10 pin the lattice even when the geometry cells form one long run.
_PREFIX_RUNS_RE = re.compile("({0}+)([^{0}{1}]{{0,2}})({1}+)([^{1}{2}]{{0,2}})({2}+)([^{2}{3}]{{0,2}}){3}".format(*_PREFIX))

DEFAULT_MAX_CANDIDATES = 32  # header clusters examined per frame (bounds worst-case time)
_MAX_MATCHES = 600  # header regex matches processed per frame
_HEAVY_BUDGET = 6  # calibrated / perturbed retries per frame
_PROBE_BUDGET = 12  # exact small-cell probes (known-colour check of cells 0..10) per frame
_EXACT_BUDGET = 2  # full exact small-cell solves per frame (after a successful probe)
_EXACT_MAX_CELL = 3.6  # the exact solver is for crisp small cells; bigger cells fit by least squares
# Wall-clock backstop: no new fallback attempt begins this long after the scan started. The node
# budgets above already bound the work deterministically (worst cases ~35 ms here); this only
# guards against a pathologically slow machine and never decides an outcome in normal use.
_DEADLINE = 0.300
_TOP_SLACK = 24  # rows scanned below max_offset for top corners (the header's own height)
_MAX_W = 4096
_MAX_H = 2048


# ------------------------------------------------------------------ results --

@dataclass(frozen=True)
class Beacon2Result:
    """A decoded strip: ``frame``; the fitted cell pitch ``cell_px`` in physical pixels (float:
    3.0 by default, 3.75 for a 125 %-scaled capture); ``x, y`` the strip's top-left in the
    buffer; its ``rows`` and ``cols``."""

    frame: Frame
    cell_px: float
    x: int
    y: int
    rows: int
    cols: int


@dataclass(frozen=True)
class HeaderHit:
    """Where a confirmed v2 header (header cells, geometry and the known prefix cells) was
    seen, even if the frame did not decode."""

    x: int
    y: int
    cell_px: float
    cols: Optional[int] = None
    rows: Optional[int] = None


@dataclass(frozen=True)
class ScanReport:
    """Full outcome of :func:`scan`. ``reason``: ``"ok"``, ``"no header"``, ``"payload invalid"``,
    ``"strip truncated"`` (``needed_width`` / ``needed_height`` say how large the capture must
    be), ``"wrong corner"`` (a strip decoded but outside ``max_offset`` of the corner),
    ``"bad buffer"``, ``"bad corner"`` or ``"decoder error: ..."``."""

    result: Optional[Beacon2Result]
    header: Optional[HeaderHit]
    reason: str
    needed_width: Optional[int] = None
    needed_height: Optional[int] = None
    candidates: int = 0


# --------------------------------------------------------------- primitives --

@functools.lru_cache(maxsize=32)
def _tables(threshold: int) -> tuple[bytes, bytes, bytes]:
    t = int(threshold)
    return (bytes(4 if v >= t else 0 for v in range(256)),
            bytes(2 if v >= t else 0 for v in range(256)),
            bytes(1 if v >= t else 0 for v in range(256)))


def _classify(data: bytes, threshold: int) -> str:
    """BGRA bytes -> one code letter per pixel without a per-pixel Python loop: the 0/4, 0/2
    and 0/1 channel byte strings are added as big integers (no byte sum exceeds 7, so there are
    no carries and the addition is element-wise)."""
    n = len(data) // 4
    if n == 0:
        return ""
    tr, tg, tb = _tables(threshold)
    total = (int.from_bytes(data[2::4].translate(tr), "big")
             + int.from_bytes(data[1::4].translate(tg), "big")
             + int.from_bytes(data[0::4].translate(tb), "big"))
    return total.to_bytes(n, "big").translate(_CODE_TO_CHAR).decode("latin-1")


def _edges(s: str, base: int) -> list[int]:
    """Positions ``base + i`` (i >= 1) where ``s[i] != s[i-1]`` (colour transitions)."""
    return [base + m.start() for m in _RUN_RE.finditer(s)][1:]


def _lsq(pts: dict[int, float]) -> tuple[float, float]:
    """Least-squares line ``x = x0 + k * pitch`` through ``{k: x}`` (needs two distinct k)."""
    n = len(pts)
    km = sum(pts) / n
    xm = sum(pts.values()) / n
    sxx = sum((k - km) ** 2 for k in pts)
    sxy = sum((k - km) * (x - xm) for k, x in pts.items())
    pitch = sxy / sxx
    return xm - pitch * km, pitch


def _fit(edges: Sequence[float], pts: dict[int, float], o: float, p: float, n: int,
         first: int) -> tuple[float, float]:
    """Progressive least squares: assign every transition within ``max(0.5, 0.45 * pitch)`` of
    lattice boundary ``k <= kmax`` to it (several per boundary -- a blended pixel gives two
    transitions 1 px apart -- are averaged), keep the trusted ``pts``, refit, grow ``kmax``
    by ~35 % up to ``n``. A float pitch that stays accurate to the strip's far end."""
    kmax = min(n, first)
    while True:
        tol = max(0.5, 0.45 * p)
        groups: dict[int, list[float]] = {}
        for e in edges:
            k = int(math.floor((e - o) / p + 0.5))
            if 0 <= k <= kmax and abs(e - (o + k * p)) <= tol:
                groups.setdefault(k, []).append(e)
        fit = dict(pts)
        for k, xs in groups.items():
            if k not in fit:
                fit[k] = sum(xs) / len(xs)
        if len(fit) >= 2:
            o2, q = _lsq(fit)
            if q > 0.5:
                o, p = o2, q
        if kmax >= n:
            return o, p
        kmax = min(n, int(kmax * 1.35) + 2)


def _mids(o: float, p: float, n: int) -> list[int]:
    """Middle pixel of each of ``n`` cells: pixel j belongs to cell k when its centre j + 0.5
    lies in ``[o + k*p, o + (k+1)*p)`` (the GPU rasterisation rule). A 2 px first cell is
    sampled on its inner pixel: the strip's outer edge is the least certain boundary (the
    background next to it may share the cell's colour)."""
    a = [math.ceil(o + k * p - 0.5) for k in range(n + 1)]
    mids = [(a[k] + max(a[k], a[k + 1] - 1)) // 2 for k in range(n)]
    if n and a[1] - a[0] == 2:
        mids[0] += 1
    return mids


def _safe_mids(poly: Sequence[tuple[float, float]], n: int, fallback: list[int]) -> list[int]:
    """Per cell, the middle of the pixels that EVERY lattice of the feasible polygon assigns to
    that cell (a boundary without a transition leaves its pixel ambiguous; such pixels are
    avoided), else the ``fallback`` sample."""
    out = []
    for k in range(n):
        first = max(math.ceil(o + k * q - 0.5) for o, q in poly)
        last = min(math.ceil(o + (k + 1) * q - 0.5) for o, q in poly) - 1
        out.append((first + last) // 2 if first <= last else fallback[k])
    return out


def _header_geometry(row: str, m: "re.Match[str]") -> Optional[tuple[dict[int, float], float]]:
    """Known cell boundaries ``{cell index: x}`` and a cell-length estimate from the four header
    runs, or None if the runs are not a plausible header."""
    l1, g1, l2, g2, l3, g3, l4 = (len(m.group(i)) for i in range(1, 8))
    b0 = float(m.start(1))
    b1 = b0 + l1 + g1 / 2
    b2 = b1 + g1 / 2 + l2 + g2 / 2
    b3 = b2 + g2 / 2 + l3 + g3 / 2
    cell = (b3 - b0) / 3
    tol = max(1.0, 0.25 * cell) + 1e-9
    left_ok = True
    if abs((b1 - b0) - cell) > tol or abs((b2 - b1) - cell) > tol or abs((b3 - b2) - cell) > tol:
        # Whatever is left of the strip may itself be magenta (merging into run 1):
        # trust the inner boundaries only.
        cell = (b3 - b1) / 2
        tol = max(1.0, 0.25 * cell) + 1e-9
        if (b1 - b0) <= cell + tol or abs((b2 - b1) - cell) > tol or abs((b3 - b2) - cell) > tol:
            return None
        left_ok = False
    elif l1 > max(l2, l3):
        # Run 1 is longer than both inner runs: it has probably absorbed magenta pixels left
        # of the strip (common with 1-2 px cells), so measure the cell from the inner runs.
        left_ok = False
        cell = (b3 - b1) / 2
        tol = max(1.0, 0.25 * cell) + 1e-9
    if cell < 0.75:
        return None
    # Run 4 is header cell 3 plus every following green geometry cell (cols 32 -> G0 green,
    # rows 17..24 -> G1 green, ...): between one and four cells long.
    run4 = l4 + g3 / 2
    if run4 < cell - tol or run4 > 4 * cell + 2 * tol + 1:
        return None
    if max(g1, g2, g3) > 0 and cell < 2:
        return None  # 1-px cells cannot have blended gaps between them
    pts = {1: b1, 2: b2, 3: b3}
    if left_ok:
        pts[0] = b0
    if cell >= 3 and g3 == 0 and run4 <= 1.4 * cell:
        # G0 is not green, so run 4 is exactly one cell (only unambiguous for cells >= 3 px:
        # with 1.8 px cells a 3 px run may be a 1 px header cell plus a 2 px green G0).
        pts[4] = float(m.end(7))
    return pts, cell


# -------------------------------------------------- exact small-cell lattices --

_TIE = 0.02  # cell edges exactly on a pixel centre (exact rational scales) are ties
_SKIP_COST = 4  # a stray transition inside a crisp strip is rarer than an empty boundary


def _clip(poly: list[tuple[float, float]], a: float, b: float, c: float) -> Optional[list[tuple[float, float]]]:
    """Convex polygon ``poly`` in (x0, pitch) space clipped to ``a*x0 + b*pitch <= c``."""
    out = []
    n = len(poly)
    for i in range(n):
        po, pp = poly[i]
        qo, qp = poly[(i + 1) % n]
        fp = a * po + b * pp - c
        fq = a * qo + b * qp - c
        if fp <= 0:
            out.append((po, pp))
        if (fp < 0 < fq) or (fq < 0 < fp):
            s = fp / (fp - fq)
            out.append((po + s * (qo - po), pp + s * (qp - pp)))
    return out if len(out) >= 3 else None


def _constrain(poly: list[tuple[float, float]], k: int, t: int) -> Optional[list[tuple[float, float]]]:
    """Add "pixel ``t`` is the first pixel of cell ``k``": t - 0.5 < x0 + k*pitch <= t + 0.5."""
    q = _clip(poly, 1.0, float(k), t + 0.5 + _TIE)
    return None if q is None else _clip(q, -1.0, -float(k), -(t - 0.5 - _TIE))


def _centroid(poly: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(poly)
    return sum(v[0] for v in poly) / n, sum(v[1] for v in poly) / n


#: Known colours of strip row 0: the header (cells 0-3) and the whitened magic + version
#: (cells 7-10); cell 4 (cols/16) is never black.
_ROW0_PATTERN = {0: "m", 1: "g", 2: "m", 3: "g", **{_LEAD + i: ch for i, ch in enumerate(_PREFIX)}}


_HEAD_CELLS = 14  # data cells holding the 5 head bytes
_KS_HEAD = tuple((b[0] << 2) | (b[1] << 1) | b[2] for b in
                 (p2.keystream_bits(3 * _HEAD_CELLS)[3 * j:3 * j + 3] for j in range(_HEAD_CELLS)))


@functools.lru_cache(maxsize=None)
def _single_row_lengths(cols: int) -> tuple[int, ...]:
    """Frame lengths that fit in one row of ``cols`` cells, 9 (an empty HELLO / PING) first. A
    body is empty or holds at least one 2-byte TLV head, so no frame is 10 bytes long."""
    lengths = [p2.FRAME_OVERHEAD] + list(range(p2.FRAME_OVERHEAD + 2, p2.MAX_FRAME + 1))
    return tuple(n for n in lengths if p2.rows_for(n, cols) == 1)


@functools.lru_cache(maxsize=None)
def _pad_pattern(frame_len: int, cols: int) -> dict[int, str]:
    """Known colours of the pad cells of a one-row strip carrying ``frame_len`` bytes: pad bits
    are zero, so each whole pad cell shows its whitening keystream code."""
    first = -(-frame_len * 8 // 3)  # first data cell holding no frame bit
    ks = p2.keystream_bits(3 * (cols - _LEAD))
    return {_LEAD + j: _CODE_CHARS[(ks[3 * j] << 2) | (ks[3 * j + 1] << 1) | ks[3 * j + 2]]
            for j in range(first, cols - _LEAD)}


def _runs_ok(pattern: Optional[dict[int, str]], row: Optional[str], k_from: int, k_to: int, t_from: int) -> bool:
    """Cells ``k_from .. k_to-1`` all show the colour of the run starting at pixel ``t_from``
    (no transition in between) -- consistent with the known colours of ``pattern``?"""
    if pattern is None or row is None or not 0 <= t_from < len(row):
        return True
    colour = row[t_from]
    for j in range(k_from, k_to):
        want = pattern.get(j)
        if want is not None and want != colour:
            return False
        if j == 4 and colour == "k":
            return False
    return True


def _exact_lattices(edges: Sequence[int], seeds: Sequence[tuple[int, int]], o_rng: tuple[float, float],
                    p_rng: tuple[float, float], n: int, *, pattern: Optional[dict[int, str]] = None,
                    row: Optional[str] = None, max_solutions: int = 4, budget: int = 2500,
                    max_skips: int = 2) -> Iterator[tuple[float, float, list[tuple[float, float]]]]:
    """Lattices ``(x0, pitch)`` under which every transition is a cell boundary of crisp
    rasterised cells (pitch >= 1 px).

    For 1-3 px cells (e.g. 1.25 px after display scaling) a centre sample is a fraction of a
    pixel from the next cell, so the lattice must be exact, which no fit of quantised edges
    can promise over a 100-cell row. The rasterisation rule gives, for a transition at pixel t
    that starts cell k, the strip ``t - 0.5 < x0 + k*pitch <= t + 0.5`` in (x0, pitch) space;
    the feasible region is the intersection of all strips -- a small convex polygon. Seeds (the
    header boundaries) start the polygon; the remaining transitions are taken left to right and
    assigned the boundary indices the polygon still allows (best-first by the number of
    boundaries left without a transition, ``max_skips`` stray pixels at most, bounded by
    ``budget`` nodes); transitions past boundary ``n`` cost nothing. Boundaries with no
    transition (equal neighbours in every scanned row) need no index: any lattice in the
    polygon samples them the same. Each complete assignment yields ``(x0, pitch, polygon)``
    at the polygon's centroid.

    Transitions alone cannot tell 1.1 px cells from 1.0 px cells with some equal neighbours, so
    the known colours of row 0 (``pattern`` over the reference ``row``) are enforced too: the
    cells between two assigned boundaries all have the colour of the run between them.
    """
    (o_lo, o_hi), (p_lo, p_hi) = o_rng, p_rng
    poly: Optional[list[tuple[float, float]]] = [(o_lo, p_lo), (o_hi, p_lo), (o_hi, p_hi), (o_lo, p_hi)]
    seeds = sorted(seeds)
    for i, (k, t) in enumerate(seeds):
        poly = _constrain(poly, k, t)  # type: ignore[arg-type]
        if poly is None:
            return
        if i and not _runs_ok(pattern, row, seeds[i - 1][0], k, seeds[i - 1][1]):
            return
    k0, t0 = seeds[-1]
    items = sorted(e for e in set(edges) if e > t0)
    # Best-first over partial assignments, cheapest first: each boundary without a transition
    # costs 1 (equal neighbours in every scanned row, prior ~ 8**-rows), each discarded stray
    # transition _SKIP_COST; ties go deeper first. A too-small pitch can always "explain" the
    # data with extra empty boundaries, so assignments come out in likelihood order and the
    # caller's checksum picks the right one among the first few.
    tick = 0
    heap: list[tuple[int, int, int, list[tuple[float, float]], int, int, int, int]] = [
        (0, 0, 0, poly, k0, t0, 0, 0)]  # type: ignore[list-item]
    nodes = found = 0
    while heap:
        cost, _negi, _tick, poly_i, kl, tl, i, skips = heapq.heappop(heap)
        nodes += 1
        if nodes > budget:
            return
        if i == len(items):
            co, cp = _centroid(poly_i)
            yield co, cp, poly_i
            found += 1
            if found >= max_solutions:
                return
            continue
        t = items[i]
        lo = min((t - 0.5 - o) / q for o, q in poly_i)
        hi = max((t + 0.5 - o) / q for o, q in poly_i)
        if kl >= n or lo > n:  # past the last boundary of interest (or beyond it under every lattice): free
            tick += 1
            heapq.heappush(heap, (cost, -(i + 1), tick, poly_i, kl, tl, i + 1, skips))
            continue
        for k in range(max(kl + 1, int(math.floor(lo))), min(n, int(math.ceil(hi))) + 1)[:6]:
            if not _runs_ok(pattern, row, kl, k, tl):
                continue
            q2 = _constrain(poly_i, k, t)
            if q2 is not None:
                tick += 1
                heapq.heappush(heap, (cost + (k - kl - 1), -(i + 1), tick, q2, k, t, i + 1, skips))
        if skips < max_skips:
            tick += 1
            heapq.heappush(heap, (cost + _SKIP_COST, -(i + 1), tick, poly_i, kl, tl, i + 1, skips + 1))


# ---------------------------------------------------------------- the scan --

class _Cluster:
    """One header candidate seen over consecutive pixel rows (= the pixel rows of strip row 0)."""

    __slots__ = ("x", "cell", "top", "bottom", "hits")

    def __init__(self, x: float, cell: float, y: int, hit: tuple[Any, dict[int, float], float]) -> None:
        self.x, self.cell, self.top, self.bottom = x, cell, y, y
        self.hits = {y: hit}

    def matches(self, x: float, cell: float, y: int) -> bool:
        # Consecutive rows only (one noisy row may be skipped for big cells): with small cells
        # a header-like pattern a row or two lower is strip data, not more of row 0.
        gap = 2 if self.cell >= 4 else 1
        return (y - self.bottom <= gap and abs(x - self.x) <= max(1.5, 0.3 * self.cell)
                and abs(cell - self.cell) <= max(1.0, 0.3 * self.cell))

    def add(self, y: int, hit: tuple[Any, dict[int, float], float]) -> None:
        if y not in self.hits:
            self.hits[y] = hit
            self.bottom = max(self.bottom, y)

    def ref_row(self) -> int:
        mid = (self.top + self.bottom) / 2
        return min(self.hits, key=lambda y: (abs(y - mid), y))


class _Cand:
    """Decoding state for one cluster."""

    __slots__ = ("cl", "y_ref", "pts", "cell", "edges0", "o", "p", "cols", "rows", "exact_cands")

    def __init__(self, cl: _Cluster) -> None:
        self.cl = cl
        self.y_ref = cl.ref_row()
        self.pts, self.cell = self._robust_header(cl)
        self.edges0: list[int] = []
        self.o, self.p = _lsq(self.pts)
        self.cols = self.rows = 0
        # (header seeds, (G0, G1, G2), colour pattern) confirmed by the exact probe
        self.exact_cands: list[tuple[list[tuple[int, int]], tuple[int, int, int], dict[int, str]]] = []

    def header_variants(self) -> list[dict[int, float]]:
        """Trusted header boundaries, then (if boundary 0 was measured) the same without it:
        boundary 0 is the only one next to the background, and a magenta background pixel
        merged into run 1 is undetectable when the runs are 1-2 px of uneven length."""
        if 0 in self.pts:
            return [self.pts, {k: v for k, v in self.pts.items() if k != 0}]
        return [self.pts]

    @staticmethod
    def _robust_header(cl: _Cluster) -> tuple[dict[int, float], float]:
        """Per-boundary medians over every pixel row of the header: one row whose first run
        absorbed a magenta background pixel (a 1 px error the per-row tolerance accepts) must
        not skew the pitch. Boundary 0 is kept only if most rows measured it."""
        hits = list(cl.hits.values())
        if len(hits) == 1:
            return dict(hits[0][1]), hits[0][2]

        def median(vals: list[float]) -> float:
            s = sorted(vals)
            n = len(s)
            return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

        pts: dict[int, float] = {}
        for k in range(5):
            vals = [h[1][k] for h in hits if k in h[1]]
            if vals and (k in (1, 2, 3) or 2 * len(vals) > len(hits)):
                pts[k] = median(vals)
        return pts, median([h[2] for h in hits])


class _Scanner:
    def __init__(self, buf: Any, width: int, height: int, stride: int, corner: str, threshold: int,
                 max_offset: int, max_candidates: int) -> None:
        self.buf = buf
        self.w, self.h, self.stride = width, height, stride
        self.corner = corner
        self.left = corner.endswith("LEFT")
        self.top = corner.startswith("TOP")
        self.threshold = threshold
        self.max_offset = max(0, max_offset)
        self.max_candidates = max(1, max_candidates)
        self.codes = ""
        self.rows: list[str] = []
        self.first_hit: Optional[HeaderHit] = None
        self.reason = "no header"
        self.need_w = 0
        self.need_h = 0
        self.examined = 0
        self.deadline = time.perf_counter() + _DEADLINE

    # -------------------------------------------------------------- setup --
    def prepare(self) -> bool:
        w, h, stride = self.w, self.h, self.stride
        if w <= 0 or h <= 0 or w > _MAX_W or h > _MAX_H or stride < w * 4 or not 1 <= self.threshold <= 255:
            return False
        buf = self.buf
        if not isinstance(buf, bytes):
            buf = bytes(buf)
            self.buf = buf
        row_bytes = w * 4
        if len(buf) < row_bytes:
            return False
        h = min(h, (len(buf) - row_bytes) // stride + 1)
        self.h = h
        data = buf[:h * stride] if stride == row_bytes else b"".join(
            buf[y * stride:y * stride + row_bytes] for y in range(h))
        self.codes = _classify(data, self.threshold)
        self.rows = [self.codes[y * w:(y + 1) * w] for y in range(h)]
        return True

    def clusters(self) -> list[_Cluster]:
        limit_x = self.max_offset + 1 if self.left else self.w
        ys = range(min(self.h, self.max_offset + _TOP_SLACK)) if self.top else range(self.h)
        found: list[_Cluster] = []
        active: list[_Cluster] = []
        matches = 0
        for y in ys:
            row = self.rows[y]
            if "m" in row:
                for m in _HEADER_RE.finditer(row):
                    if m.start() > limit_x or matches >= _MAX_MATCHES:
                        break
                    matches += 1
                    geom = _header_geometry(row, m)
                    if geom is None:
                        continue
                    pts, cell = geom
                    x = pts.get(0, pts[1] - cell)
                    cl = next((c for c in active if c.matches(x, cell, y)), None)
                    if cl is None:
                        cl = _Cluster(x, cell, y, (m, pts, cell))
                        found.append(cl)
                        active.append(cl)
                    else:
                        cl.add(y, (m, pts, cell))
            active = [c for c in active if y - c.bottom < 2]
        return found

    # ------------------------------------------------------------ sampling --
    def _sample(self, xm: Sequence[int], ym: Sequence[int]) -> Optional[list[int]]:
        if xm[0] < 0 or xm[-1] >= self.w or ym[0] < 0 or ym[-1] >= self.h:
            return None
        cc = _CHAR_CODE
        out: list[int] = []
        for y in ym:
            row = self.rows[y]
            out.extend([cc[row[x]] for x in xm])
        return out

    def _pixel(self, x: int, y: int) -> tuple[int, int, int]:
        o = y * self.stride + x * 4
        b = self.buf
        return b[o + 2], b[o + 1], b[o]

    def _sample_calibrated(self, xm: Sequence[int], ym: Sequence[int]) -> Optional[list[int]]:
        """Per-channel thresholds from the header cells: magenta gives the "on" level of R and B
        and the "off" level of G, green the opposite; mid-points of those levels copes with
        tone-mapped (HDR), tinted or dimmed captures."""
        if xm[0] < 0 or xm[-1] >= self.w or ym[0] < 0 or ym[-1] >= self.h:
            return None
        m1, g1, m2, g2 = (self._pixel(xm[i], ym[0]) for i in range(4))
        on = ((m1[0] + m2[0]) / 2, (g1[1] + g2[1]) / 2, (m1[2] + m2[2]) / 2)
        off = ((g1[0] + g2[0]) / 2, (m1[1] + m2[1]) / 2, (g1[2] + g2[2]) / 2)
        if min(on[c] - off[c] for c in range(3)) < 24:
            return None
        tr, tg, tb = ((on[c] + off[c]) / 2 for c in range(3))
        out: list[int] = []
        b, stride = self.buf, self.stride
        for y in ym:
            base = y * stride
            for x in xm:
                o = base + x * 4
                out.append(((b[o + 2] >= tr) << 2) | ((b[o + 1] >= tg) << 1) | (b[o] >= tb))
        return out

    def _decode(self, c: _Cand, o: float, p: float, y0: float, py: float, *, calibrated: bool = False,
                xm: Optional[list[int]] = None, ym: Optional[list[int]] = None) -> Optional[Beacon2Result]:
        if xm is None:
            xm = _mids(o, p, c.cols)
        if ym is None:
            ym = _mids(y0, py, c.rows)
        codes = self._sample_calibrated(xm, ym) if calibrated else self._sample(xm, ym)
        if codes is None:
            return None
        frame = p2.decode_cell_codes(codes, c.cols, c.rows)
        if frame is None:
            return None
        return self._result(frame, c, o, p, y0, py)

    def _result(self, frame: Frame, c: _Cand, o: float, p: float, y0: float, py: float) -> Optional[Beacon2Result]:
        """Apply the corner constraint: the strip's corner must be within max_offset of the
        buffer's same corner."""
        lim = self.max_offset + 1.0
        x_gap = o if self.left else self.w - (o + c.cols * p)
        y_gap = y0 if self.top else self.h - (y0 + c.rows * py)
        if x_gap > lim or y_gap > lim:
            self.reason = "wrong corner"
            return None
        return Beacon2Result(frame, round(p, 2), int(round(o)), int(round(y0)), c.rows, c.cols)

    # ------------------------------------------------------------- lattice --
    def _row0_edges(self, c: _Cand) -> None:
        cl = c.cl
        lo = max(0, int(cl.x) - 2)
        hi = min(self.w, int(cl.x + p2.MAX_COLS * c.cell * 1.5) + 4)
        edges: set[int] = set()
        for y in cl.hits:  # pixel rows where the header was seen lie inside strip row 0
            edges.update(_edges(self.rows[y][lo:hi], lo))
        c.edges0 = sorted(edges)

    def _geometry_codes(self, c: _Cand, o: float, p: float) -> Optional[tuple[int, int, int]]:
        """``(G0, G1, G2)`` if cells 0..10 on the reference row sample as header + known prefix
        at lattice (o, p) and G0 (cols / 16) is not zero, else None."""
        xm = _mids(o, p, _PROBE)
        if xm[0] < 0 or xm[-1] >= self.w:
            return None
        row = self.rows[c.y_ref]
        s = "".join(row[x] for x in xm)
        if s[:4] != "mgmg" or s[_LEAD:_PROBE] != _PREFIX:
            return None
        g = (_CHAR_CODE[s[4]], _CHAR_CODE[s[5]], _CHAR_CODE[s[6]])
        return g if g[0] else None

    def _set_geometry(self, c: _Cand, g: tuple[int, int, int], o: float, p: float) -> None:
        c.cols = p2.COL_STEP * g[0]
        c.rows = g[1] * 8 + g[2] + 1
        if self.first_hit is None or self.first_hit.cols is None:
            self.first_hit = HeaderHit(int(round(o)), c.cl.top, round(p, 2), c.cols, c.rows)

    def _read_geometry(self, c: _Cand, o: float, p: float) -> bool:
        """Sample cells 0..10 on the reference row: header, geometry, known prefix."""
        g = self._geometry_codes(c, o, p)
        if g is None:
            return False
        self._set_geometry(c, g, o, p)
        return True

    def _fits(self, c: _Cand, o: float, p: float, y0: float, py: float) -> bool:
        """False (and the needed capture size recorded) when the strip runs off the buffer."""
        need_w = int(math.ceil(o + c.cols * p - 0.5))
        need_h = int(math.ceil(y0 + c.rows * py - 0.5))
        if need_w > self.w or need_h > self.h or o < -0.5 * p or y0 < -0.5 * py:
            self.reason = "strip truncated"
            self.need_w = max(self.need_w, need_w + 1 if need_w > self.w else 0)
            self.need_h = max(self.need_h, need_h + 1 if need_h > self.h else 0)
            return False
        return True

    def _col_edges(self, xm: Sequence[int], ya: int, yb: int) -> list[int]:
        w, codes = self.w, self.codes
        edges: set[int] = set()
        for x in xm:
            if 0 <= x < w:
                edges.update(_edges(codes[ya * w + x:yb * w + x:w], ya))
        return sorted(edges)

    def _fit_rows(self, c: _Cand, o: float, p: float) -> tuple[float, float]:
        """Row lattice (y0, pitch_y): start from the header's pixel rows (square cells), refine
        with row-boundary transitions pooled over every column; y0 must keep the header rows
        inside strip row 0."""
        cl = c.cl
        py = p
        y0 = (cl.top + cl.bottom + 1 - py) / 2.0
        lo_y, hi_y = cl.bottom + 0.5 - py - 0.6, cl.top + 0.5 + 0.6
        ya = max(0, int(math.floor(y0)) - 1)
        yb = min(self.h, int(math.ceil(y0 + c.rows * py)) + 2)
        if yb - ya < 2:
            return y0, py
        vedges = self._col_edges(_mids(o, p, c.cols), ya, yb)
        for _ in range(2):
            tol = max(0.5, 0.45 * py)
            groups: dict[int, list[int]] = {}
            for e in vedges:
                k = int(math.floor((e - y0) / py + 0.5))
                if 0 <= k <= c.rows and abs(e - (y0 + k * py)) <= tol:
                    groups.setdefault(k, []).append(e)
            if not groups:
                break
            pts = {k: sum(v) / len(v) for k, v in groups.items()}
            ny0, npy = y0, py
            if len(pts) >= 3:
                a, b = _lsq(pts)
                if abs(b - p) <= 0.06 * p:
                    ny0, npy = a, b
                else:
                    ny0 = sum(x - k * py for k, x in pts.items()) / len(pts)
            else:
                ny0 = sum(x - k * py for k, x in pts.items()) / len(pts)
            if not lo_y <= ny0 <= hi_y:
                break
            y0, py = ny0, npy
        return y0, py

    def _all_rows_fit(self, c: _Cand, o: float, p: float, y0: float, py: float) -> tuple[float, float]:
        """Refine the column lattice with transitions pooled over the centre line of every row."""
        lo = max(0, int(math.floor(o)) - 1)
        hi = min(self.w, int(math.ceil(o + c.cols * p)) + 2)
        edges = set(c.edges0)
        for y in _mids(y0, py, c.rows):
            if 0 <= y < self.h:
                edges.update(_edges(self.rows[y][lo:hi], lo))
        return _fit(sorted(edges), c.pts, o, p, c.cols, c.cols)

    def _prefix_pts(self, c: _Cand) -> dict[int, float]:
        """Boundaries 8, 9, 10 from the known prefix's colour runs on the reference row (as v1
        pinned its fixed payload prefix): about five cells after header boundary 3, with run
        lengths that agree with the header's cell estimate. {} if not found."""
        row = self.rows[c.y_ref]
        b3, cell = c.pts[3], c.cell
        lo = max(0, int(b3 + 3.0 * cell))
        hi = min(len(row), int(b3 + 9.0 * cell * 1.4) + 3)
        tol = max(1.0, 0.45 * cell)
        for m in _PREFIX_RUNS_RE.finditer(row, lo, hi):
            x8 = m.end(1) + len(m.group(2)) / 2
            x9 = m.end(3) + len(m.group(4)) / 2
            x10 = m.end(5) + len(m.group(6)) / 2
            if abs((x9 - x8) - cell) > tol or abs((x10 - x9) - cell) > tol:
                continue
            if abs((x9 - x8) - (x10 - x9)) > max(1.5, 0.3 * cell):
                continue
            step = (x10 - x8) / 2
            if not 4.0 * step <= x8 - b3 <= 6.0 * step:
                continue  # boundary 8 is five cells after boundary 3
            return {8: x8, 9: x9, 10: x10}
        return {}

    # -------------------------------------------------------------- passes --
    def cheap(self, c: _Cand) -> Optional[Beacon2Result]:
        """Least-squares lattices + centre sampling: the normal case for a clean strip."""
        self._row0_edges(c)
        pre = self._prefix_pts(c)
        starts: list[tuple[dict[int, float], float, float]] = []
        for pts in c.header_variants():
            if pre:
                starts.append((pts, *_lsq({**pts, **pre})))
            starts.append((pts, *_lsq(pts)))
        for pts, o0, p0 in starts:
            o, p = _fit(c.edges0, pts, o0, p0, _PROBE, 6)
            if self._read_geometry(c, o, p):
                break
            if self._read_geometry(c, o0, p0):
                o, p = o0, p0
                break
        else:
            return None
        c.pts = pts
        o, p = _fit(c.edges0, c.pts, o, p, c.cols, 15)
        y0, py = (c.cl.top + c.cl.bottom + 1 - p) / 2.0, p
        if not self._fits(c, o, p, y0, py):
            return None
        y0, py = self._fit_rows(c, o, p)
        if not self._fits(c, o, p, y0, py):
            return None
        if c.rows > 1:
            o, p = self._all_rows_fit(c, o, p, y0, py)
        c.o, c.p = o, p
        res = self._decode(c, o, p, y0, py)
        if res is None and self.reason != "wrong corner":
            self.reason = "payload invalid"
        return res

    def heavy(self, c: _Cand) -> Optional[Beacon2Result]:
        """Calibrated thresholds and small lattice perturbations around the cheap fit."""
        if not c.cols:
            return None
        o, p = c.o, c.p
        y0, py = self._fit_rows(c, o, p)
        got = self._decode(c, o, p, y0, py, calibrated=True)
        if got is not None:
            return got
        steps = [d * 0.25 / c.cols for d in (1, -1, 2, -2, 3, -3)]
        for dp in steps:
            got = self._decode(c, o, p * (1 + dp), y0, py)
            if got is not None:
                return got
        for dx in (0.2, -0.2):
            got = self._decode(c, o + dx * p, p, y0, py)
            if got is not None:
                return got
        for dy in (0.25, -0.25):
            got = self._decode(c, o, p, y0 + dy * py, py)
            if got is not None:
                return got
        return None

    @staticmethod
    def _exact_ranges(c: _Cand) -> tuple[tuple[float, float], tuple[float, float]]:
        p_rng = (max(1.0, 0.7 * c.cell - 0.3), 1.4 * c.cell + 0.4)
        return (c.cl.x - 2.0, c.cl.x + 2.0), p_rng

    def exact_probe(self, c: _Cand) -> bool:
        """Exact solve, step 1 (cheap): exact lattices of the known region (cells 0..10,
        colours enforced) and the geometry cells they read. Cells 4..6 have no known colour,
        so a lattice a fraction of a cell off may misread them while header and prefix still
        match; each distinct reading is therefore confirmed by solving again with cells 4..6
        added to the pattern. Fills ``c.exact_cands``; True if any reading survived."""
        if c.cell > _EXACT_MAX_CELL:
            return False
        if not c.edges0:
            self._row0_edges(c)
        o_rng, p_rng = self._exact_ranges(c)
        row = self.rows[c.y_ref]
        # Cover the known region even at the largest pitch (geometry cells are often one long
        # green or black run, so the prefix transitions are what pin the pitch); two more
        # boundaries are allowed past it, and anything beyond those is ignored for free.
        near = [e for e in c.edges0 if e < c.cl.x + (_PROBE + 1) * p_rng[1]]
        readings: list[tuple[list[tuple[int, int]], tuple[int, int, int], float, float]] = []
        for pts in c.header_variants():  # with boundary 0 first, then without it
            seeds = sorted((k, int(x)) for k, x in pts.items() if float(x).is_integer())
            if len(seeds) < 2:
                continue
            for o1, p1, poly in _exact_lattices(near, seeds, o_rng, p_rng, _PROBE + 2, pattern=_ROW0_PATTERN,
                                                row=row, max_solutions=3, budget=1500, max_skips=0):
                for o, q in [(o1, p1)] + poly:
                    g = self._geometry_codes(c, o, q)
                    if g is not None and all(r[0] != seeds or r[1] != g for r in readings):
                        readings.append((seeds, g, o, q))
        for seeds, g, o, q in readings:
            pat = dict(_ROW0_PATTERN)
            pat.update({4: _CODE_CHARS[g[0]], 5: _CODE_CHARS[g[1]], 6: _CODE_CHARS[g[2]]})
            if next(_exact_lattices(near, seeds, o_rng, p_rng, _PROBE + 2, pattern=pat, row=row,
                                    max_solutions=1, budget=1500, max_skips=0), None) is not None:
                c.exact_cands.append((seeds, g, pat))
                if len(c.exact_cands) == 1:
                    self._set_geometry(c, g, o, q)
        return bool(c.exact_cands)

    def exact(self, c: _Cand) -> Optional[Beacon2Result]:
        """Exact solve, step 2, after a successful :meth:`exact_probe` (see
        :func:`_exact_lattices`): columns of the whole strip from transitions pooled over every
        pixel row that must lie inside it, out to the longest possible strip end -- a pitch
        that is too small and ends the strip early pays for every true 2 px cell it cannot
        model, so the true lattice is (nearly always) among the cheapest -- then rows from
        transitions pooled over the middle pixel column of every cell. With one or two rows a
        boundary may show no transition at all, and a slightly different pitch can explain that
        as a 2 px cell instead; only the checksum can tell, so the cheapest few candidates are
        each decoded."""
        o_rng, p_rng = self._exact_ranges(c)
        for seeds, g, pat in c.exact_cands[:4]:
            c.cols, c.rows = p2.COL_STEP * g[0], g[1] * 8 + g[2] + 1
            edges = self._pooled_strip_edges(c, p_rng)
            tried: list[tuple[float, float]] = []
            got = self._exact_solve(c, edges, seeds, o_rng, p_rng, pat, solutions=10, budget=3000, tried=tried)
            if got is not None:
                return got
            if c.rows == 1:
                # One row: its tail is pad cells whose colours (the whitening keystream) depend
                # only on the frame length. Try 9 bytes (an empty HELLO / PING, shown most) and
                # the lengths the candidate lattices read from the frame head, with those colours.
                lengths = [p2.FRAME_OVERHEAD]
                for o2, q2 in tried:
                    n = self._head_len(c, o2, q2)
                    if n is not None and n not in lengths and n in _single_row_lengths(c.cols):
                        lengths.append(n)
                for flen in lengths[:4]:
                    if time.perf_counter() > self.deadline:
                        break
                    got = self._exact_solve(c, edges, seeds, o_rng, p_rng, {**pat, **_pad_pattern(flen, c.cols)},
                                            solutions=3, budget=2000)
                    if got is not None:
                        return got
        return None

    def _exact_solve(self, c: _Cand, edges: list[int], seeds: list[tuple[int, int]], o_rng: tuple[float, float],
                     p_rng: tuple[float, float], pattern: dict[int, str], *, solutions: int, budget: int,
                     tried: Optional[list[tuple[float, float]]] = None) -> Optional[Beacon2Result]:
        for o2, q2, poly in _exact_lattices(edges, seeds, o_rng, p_rng, c.cols, pattern=pattern,
                                            row=self.rows[c.y_ref], max_solutions=solutions, budget=budget,
                                            max_skips=1):
            if tried is not None:
                tried.append((o2, q2))
            rows = self._exact_rows(c, o2, q2)
            if rows and not self._fits(c, o2, q2, rows[0][0], rows[0][1]):
                return None
            plain_x = _mids(o2, q2, c.cols)
            safe_x = _safe_mids(poly, c.cols, plain_x)
            for y0, py, ypoly in rows:
                plain_y = _mids(y0, py, c.rows)
                safe_y = _safe_mids(ypoly, c.rows, plain_y) if ypoly else plain_y
                for xm, ym in ((safe_x, safe_y), (plain_x, plain_y)):
                    got = self._decode(c, o2, q2, y0, py, xm=xm, ym=ym)
                    if got is not None:
                        return got
                    if safe_x == plain_x and safe_y == plain_y:
                        break
        return None

    def _head_len(self, c: _Cand, o: float, p: float) -> Optional[int]:
        """Frame length read from the five head bytes (cells 7..20 of row 0) under lattice
        (o, p), if magic and version check out."""
        xm = _mids(o, p, _LEAD + _HEAD_CELLS)
        if xm[0] < 0 or xm[-1] >= self.w:
            return None
        row = self.rows[c.y_ref]
        v = 0
        for j in range(_HEAD_CELLS):
            v = (v << 3) | (_CHAR_CODE[row[xm[_LEAD + j]]] ^ _KS_HEAD[j])
        head = v >> 2  # 42 bits sampled, 40 used
        if head >> 32 != p2.MAGIC or (head >> 28) & 0x0F != p2.VERSION:
            return None
        return p2.FRAME_OVERHEAD + (head & 0xFF)

    def _pooled_strip_edges(self, c: _Cand, p_rng: tuple[float, float]) -> list[int]:
        """Column-boundary transitions pooled over every pixel row that lies inside the strip
        even at the smallest feasible pitch, out to the longest possible strip end."""
        cl = c.cl
        x_hi = min(self.w, int(math.ceil(cl.x + 2.0 + c.cols * p_rng[1])) + 2)
        y_hi = min(self.h - 1, int(math.floor(cl.top + c.rows * p_rng[0] - 1.0)))
        lo = max(0, int(cl.x) - 2)
        edges: set[int] = set(c.edges0)
        for y in range(cl.top, max(cl.bottom, y_hi) + 1):
            edges.update(_edges(self.rows[y][lo:x_hi], lo))
        return sorted(e for e in edges if e < x_hi)

    def _exact_rows(self, c: _Cand, o: float, p: float) -> list[tuple[float, float, list[tuple[float, float]]]]:
        """Exact row lattices from row-boundary transitions pooled over the middle pixel column
        of every inner cell (inside the strip even if the first or last column is off by a
        pixel), over the strip's full possible height (what lies below the last row boundary
        is ignored)."""
        cl = c.cl
        if c.rows == 1:
            return [((cl.top + cl.bottom + 1 - p) / 2.0, p, [])]
        yb = min(self.h, int(math.ceil(cl.top + c.rows * p * 1.04)) + 2)
        vedges = [e for e in self._col_edges(_mids(o, p, c.cols)[1:-1], cl.top, yb) if e > cl.bottom + 1]
        seeds = [(0, cl.top), (1, cl.bottom + 1)]
        return list(_exact_lattices(vedges, seeds, (cl.top - 1.0, cl.top + 1.0), (p * 0.96, p * 1.04), c.rows,
                                    max_solutions=2, budget=1500))

    # ----------------------------------------------------------------- run --
    def run(self) -> ScanReport:
        if not self.prepare():
            return ScanReport(None, None, "bad buffer")
        clusters = self.clusters()
        if not clusters:
            return ScanReport(None, None, "no header")
        cands: list[_Cand] = []
        for cl in clusters[: self.max_candidates]:
            self.examined += 1
            c = _Cand(cl)
            if c.p <= 0.5:
                continue
            got = self.cheap(c)
            if got is not None:
                return self._ok(got)
            cands.append(c)
        # Pass 2 (only when nothing decoded cheaply), bounded per frame. Confirmed headers
        # (geometry and known prefix read) get the calibrated / perturbed retries; small-cell
        # candidates get the cheap exact probe (known colours of cells 0..10), and only those
        # that pass it the expensive exact solve.
        heavy, probes, exact = _HEAVY_BUDGET, _PROBE_BUDGET, _EXACT_BUDGET
        for c in cands:
            if time.perf_counter() > self.deadline:
                break
            if c.cols and heavy > 0:
                heavy -= 1
                got = self.heavy(c)
                if got is not None:
                    return self._ok(got)
        for c in cands:
            if time.perf_counter() > self.deadline:
                break
            if c.cell > _EXACT_MAX_CELL or not (c.cols or self._plausible(c)) or probes <= 0 or exact <= 0:
                continue
            probes -= 1
            if not self.exact_probe(c):
                continue
            exact -= 1
            got = self.exact(c)
            if got is not None:
                return self._ok(got)
        if self.need_w or self.need_h:
            reason = "strip truncated"
        elif self.reason == "wrong corner":
            reason = "wrong corner"
        elif self.first_hit is not None:
            reason = "payload invalid"
        else:
            reason = "no header"
        return ScanReport(None, self.first_hit, reason, self.need_w or None, self.need_h or None, self.examined)

    def _plausible(self, c: _Cand) -> bool:
        """A cluster worth an exact solve although the cheap lattice could not read its geometry:
        integer header boundaries and a header seen on about as many rows as the cell is tall."""
        height = c.cl.bottom - c.cl.top + 1
        return (sum(1 for x in c.pts.values() if float(x).is_integer()) >= 3
                and abs(height - c.cell) <= max(1.0, 0.5 * c.cell))

    def _ok(self, got: Beacon2Result) -> ScanReport:
        hit = HeaderHit(got.x, got.y, got.cell_px, got.cols, got.rows)
        return ScanReport(got, hit, "ok", None, None, self.examined)


# ----------------------------------------------------------------- public --

def scan(buf: bytes | bytearray | memoryview, width: int, height: int, stride: int, corner: str = "TOPLEFT", *,
         threshold: int = 128, max_offset: int = 48, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> ScanReport:
    """Locate and decode a v2 strip; never raises (see :func:`locate_and_decode`). The report
    also says why nothing decoded and how large the capture must be for a truncated strip."""
    try:
        c = str(corner or "TOPLEFT").upper()
        if c not in CORNERS:
            return ScanReport(None, None, "bad corner")
        sc = _Scanner(buf, int(width), int(height), int(stride), c, int(threshold), int(max_offset),
                      int(max_candidates))
        return sc.run()
    except Exception as e:  # the decoder must never take the capture thread down
        log.debug("beacon2 decoder error", exc_info=True)
        return ScanReport(None, None, f"decoder error: {e.__class__.__name__}: {e}")


def locate_and_decode(buf: bytes | bytearray | memoryview, width: int, height: int, stride: int,
                      corner: str = "TOPLEFT", *, threshold: int = 128,
                      max_offset: int = 48) -> Optional[Beacon2Result]:
    """Find the v2 beacon strip in a top-down BGRA buffer and decode it.

    ``buf`` holds ``height`` rows of ``width`` BGRA pixels, ``stride`` bytes apart, captured at
    ``corner`` (``TOPLEFT``, ``TOPRIGHT``, ``BOTTOMLEFT`` or ``BOTTOMRIGHT``) of the WoW client
    area; the strip's own corner must lie within ``max_offset`` px of the buffer's. A channel
    is "on" when ``>= threshold`` (header-calibrated thresholds are tried as a fallback).
    Tolerates offsets, colour noise, 1-8 px cells and mild non-integer scaling. Returns None
    (never raises) for garbage, partially covered or truncated strips and checksum failures.
    """
    return scan(buf, width, height, stride, corner, threshold=threshold, max_offset=max_offset).result
