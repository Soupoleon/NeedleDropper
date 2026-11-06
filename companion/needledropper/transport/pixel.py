"""Pixel beacon transport (docs/SPEC.md 3.5, docs/DESIGN-v1.2.md 2.3): read the addon's colour strip off the screen.

The NeedleDropper 1.2 addon draws a **v2** strip: a grid of 3-px cells (2-4 px), 16-112 columns and
up to 9 px high, at the client area's top-left, carrying a whole Protocol2 frame (the song itself);
it is located and decoded by :func:`needledropper.transport.beacon2.locate_and_decode`. Older addons
(1.1) draw the **v1** strip decoded here: 31 cells in one row,
``MAGENTA, GREEN, MAGENTA, GREEN`` then 27 payload cells carrying 81 bits
(3 bits per cell, R/G/B each 0 or 255). This module has two halves:

* a **pure v1 decoder** (:func:`find_and_decode` / :func:`scan`) that finds the
  v1 strip in a BGRA buffer and turns it into an :class:`~needledropper.models.Event`;
* :class:`PixelTransport`, a daemon thread that captures the corner of the game
  window a few times per second (via :mod:`.wincapture`, or an injected
  backend), decodes v2 first and v1 as a fallback, and applies the acceptance
  rules before calling ``on_event`` with a :class:`~needledropper.protocol2.Frame`
  (v2) or an :class:`~needledropper.models.Event` (v1).

Decoder outline
---------------
1. Classify every pixel of the scanned rows to a 3-bit code
   ``(R>=t)<<2 | (G>=t)<<1 | (B>=t)`` using C-speed bytes/int operations, and
   render each row as a string of letters (``m`` magenta, ``g`` green, ...).
2. Find the header with a regex: runs ``m+ g+ m+ g+`` (up to two blended
   "transition" pixels allowed between runs) whose first three runs have
   similar lengths L (+-1 px, more for big cells) and whose first run starts at
   x <= ``max_offset``. The payload always starts GREEN, CYAN, RED, BLUE (magic
   byte 0x4E + version nibble), which pins three more boundaries when visible.
3. Fit the cell lattice ``x = x0 + k * pitch`` by least squares, first to those
   known boundaries and then, progressively, to every colour transition along
   the strip. This gives a sub-pixel *float* pitch, so non-integer cell sizes
   (e.g. 4.5 px from display scaling, drawn as 4,5,4,5... px runs) decode
   correctly; sampling with an integer L would drift by pixels at cell 30.
4. Sample each payload cell at its centre ``x0 + (4 + i + 0.5) * pitch`` and
   decode with :func:`needledropper.protocol.decode_cells`.
5. Only if no candidate decoded that way (bounded work per frame): a
   lattice-coherence fit over all transitions, an exact rasterisation search for
   1-2 px cells, then retries with per-channel thresholds calibrated on the
   header (HDR / tone shifts), run-based sampling and tiny pitch/offset
   perturbations. The magic byte, version and Fletcher-16 gate every attempt.

Pixel order is **B, G, R, A** (GDI's 32-bpp DIB layout), rows top-down,
``stride`` bytes apart.
"""
from __future__ import annotations

import collections
import functools
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol as _TypingProtocol, Union

from .. import protocol
from ..models import Event
from ..protocol2 import CORNERS, Frame as Frame2
from . import beacon2, wincapture
from .base import EventCallback, Transport

log = logging.getLogger(__name__)

DiagCallback = Callable[[str], None]
Frame = tuple[bytes, int, int, int]  # (bgra, width, height, stride)
AnyEvent = Union[Event, Frame2]
#: The v2 capture region (DESIGN-v1.2 2.3): the client area's top-left 512 x 48 px.
V2_REGION = (512, 48)

# ------------------------------------------------------------------ decoder --

_CODE_CHARS = b"kbgcrmyw"  # index = R*4 + G*2 + B  (k = black ... w = white)
_CODE_TO_CHAR = bytes.maketrans(bytes(range(8)), _CODE_CHARS)
_CHAR_RGB = {
    "k": (0, 0, 0), "b": (0, 0, 255), "g": (0, 255, 0), "c": (0, 255, 255),
    "r": (255, 0, 0), "m": (255, 0, 255), "y": (255, 255, 0), "w": (255, 255, 255),
}
# Header candidate: zero-width match at the start of a magenta run, capturing
# the four runs and the (blended) gaps between them.
_HEADER_RE = re.compile(r"(?<!m)(?=(m+)([^mg]{0,2})(g+)([^mg]{0,2})(m+)([^mg]{0,2})(g+))")

_HEADER_CELLS = len(protocol.HEADER)  # 4
_TOTAL_CELLS = protocol.TOTAL_CELLS  # 31
_ROW_SLACK = 17  # rows scanned below max_offset (strip top <= max_offset; centre row lower)
DEFAULT_MAX_CANDIDATES = 32  # bound on header candidates examined per frame (worst-case time)
_PHASE_BUDGET = 6  # expensive lattice searches per frame
_ARC_BUDGET = 2  # exact small-cell searches per frame


@dataclass(frozen=True)
class DecodeResult:
    """A successfully decoded beacon frame.

    ``cell_px`` is the measured cell pitch in physical pixels (float: 4.0 for the
    default, 4.5 for a scaled strip); ``x, y`` is the strip's top-left in the
    scanned buffer.
    """

    event: Event
    cell_px: float
    x: int
    y: int


@dataclass(frozen=True)
class HeaderHit:
    """Where a plausible header was seen (even if the payload did not decode)."""

    x: int
    y: int
    cell_px: float


@dataclass(frozen=True)
class ScanReport:
    """Full outcome of :func:`scan` (``result`` is what :func:`find_and_decode` returns)."""

    result: Optional[DecodeResult]
    header: Optional[HeaderHit]
    reason: str  # "ok" | "no header" | "payload invalid" | "strip truncated" | "bad buffer" | ...
    needed_width: Optional[int] = None  # set when the strip runs past the right edge
    candidates: int = 0


@functools.lru_cache(maxsize=32)
def _tables(threshold: int) -> tuple[bytes, bytes, bytes]:
    t = int(threshold)
    return (bytes(4 if v >= t else 0 for v in range(256)),
            bytes(2 if v >= t else 0 for v in range(256)),
            bytes(1 if v >= t else 0 for v in range(256)))


def _classify(data: bytes, threshold: int) -> str:
    """BGRA bytes -> one code letter per pixel (no per-pixel Python loop).

    The per-channel 0/4, 0/2, 0/1 byte strings are added as big integers; since
    no byte sum exceeds 7 there are no carries, so the addition is element-wise.
    """
    n = len(data) // 4
    if n == 0:
        return ""
    tr, tg, tb = _tables(threshold)
    total = (int.from_bytes(data[2::4].translate(tr), "big")
             + int.from_bytes(data[1::4].translate(tg), "big")
             + int.from_bytes(data[0::4].translate(tb), "big"))
    return total.to_bytes(n, "big").translate(_CODE_TO_CHAR).decode("latin-1")


def _lsq(pts: dict[int, float]) -> tuple[float, float]:
    """Least-squares line x = x0 + k * pitch through {cell index k: boundary x}."""
    n = len(pts)
    km = sum(pts) / n
    xm = sum(pts.values()) / n
    sxx = sum((k - km) ** 2 for k in pts)
    sxy = sum((k - km) * (x - xm) for k, x in pts.items())
    pitch = sxy / sxx
    return xm - pitch * km, pitch


_PREFIX_RE = re.compile(r"([^gc]{0,2})(c+)([^cr]{0,2})(r+)([^rb]{0,2})b")


def _header_geometry(row: str, m: "re.Match[str]") -> Optional[tuple[dict[int, float], float]]:
    """Known cell boundaries ``{cell index: x}`` and a cell-length estimate, or None.

    Uses the 4 header runs and, when visible, the fixed start of the payload:
    payload cells 0-3 are always GREEN, CYAN, RED, BLUE (magic byte 0x4E followed
    by the version nibble), which adds boundaries at cell indices 5, 6 and 7 and
    makes the initial pitch estimate far more precise.
    """
    l1, g1, l2, g2, l3, g3, l4 = (len(m.group(i)) for i in range(1, 8))
    b0 = float(m.start(1))
    b1 = b0 + l1 + g1 / 2
    b2 = b1 + g1 / 2 + l2 + g2 / 2
    b3 = b2 + g2 / 2 + l3 + g3 / 2
    run4 = l4 + g3 / 2
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
    if cell < 0.75:
        return None
    # Run 4 is the last header cell plus payload cell 0, which is always green,
    # so it should be ~2 cells long; it can be shorter only if the capture is
    # truncated right after the header.
    if run4 < cell - tol or run4 > 2 * cell + 2 * tol + 1:
        return None
    if max(g1, g2, g3) > 0 and cell < 2:
        return None  # 1-px cells cannot have blended gaps between them
    pts = {1: b1, 2: b2, 3: b3}
    if left_ok:
        pts[0] = b0
    p4 = m.end(7)
    pm = _PREFIX_RE.match(row, p4)
    if pm is not None:
        b5 = p4 + len(pm.group(1)) / 2
        b6 = pm.end(2) + len(pm.group(3)) / 2
        b7 = pm.end(4) + len(pm.group(5)) / 2
        if (abs((b5 - b3) - 2 * cell) <= 2 * tol and abs((b6 - b5) - cell) <= tol
                and abs((b7 - b6) - cell) <= tol):
            pts.update({5: b5, 6: b6, 7: b7})
            if 0 in pts and l1 > min(l2, l3):
                del pts[0]  # run 1 may have merged with magenta pixels left of the strip
            cell = _lsq(pts)[1]
    return pts, cell


def _transitions(row: str, lo: int, hi: int) -> list[int]:
    return [x for x in range(max(1, lo), hi) if row[x] != row[x - 1]]


def _fit_lattice(row: str, pts: dict[int, float], width: int) -> tuple[float, float]:
    """Cheap refinement of (x0, pitch): progressive least squares over transitions."""
    x0, pitch = _lsq(pts)
    edges = _transitions(row, int(x0 + 4.5 * pitch), min(width, int(x0 + _TOTAL_CELLS * pitch + 2)))
    if not edges:
        return x0, pitch
    for kmax in (10, 14, 19, 25, _TOTAL_CELLS):
        groups: dict[int, list[int]] = {}
        tol = max(0.5, 0.45 * pitch)
        for e in edges:
            k = int(math.floor((e - x0) / pitch + 0.5))
            if k < 5 or k > kmax:
                continue  # k=4 (header green | payload green) never has a real edge
            if abs(e - (x0 + k * pitch)) > tol:
                continue
            groups.setdefault(k, []).append(e)
        if not groups:
            continue
        fit = dict(pts)
        for k, xs in groups.items():
            if k not in pts:
                fit[k] = sum(xs) / len(xs)  # a blended pixel gives two edges 1 px apart: average
        x0, pitch = _lsq(fit)
        if pitch <= 0.5:
            break
    return x0, pitch


def _phase_lattice(row: str, pts: dict[int, float], cell: float, width: int) -> Optional[tuple[float, float]]:
    """Robust (x0, pitch) by maximising lattice coherence of all transitions.

    For a trial pitch P every transition x is mapped to the angle 2*pi*x/P; the
    length of their vector sum peaks when the transitions sit on a lattice of
    pitch P. This needs no initial assignment of edges to cells, so it recovers
    from a poor header estimate (1-2 px cells, fractional scaling) where the
    progressive fit can lock onto the wrong cell. Followed by a least-squares
    polish.
    """
    x_ref, p_ref = _lsq(pts)
    span = 0.2 if 7 in pts else 0.3
    base = max(0.75, min(cell, p_ref) if p_ref > 0 else cell)
    pmin, pmax = max(0.75, base * (1 - span)), max(cell, p_ref) * (1 + span)
    hi = min(width, int(x_ref + _TOTAL_CELLS * pmax) + 2)
    edges = [float(x) for x in _transitions(row, int(x_ref), hi)]
    edges.extend(pts.values())
    if len(edges) < 6:
        return None
    n = max(8, int((pmax - pmin) / (pmin / 120.0)) + 1)
    cos, sin, tau = math.cos, math.sin, 2.0 * math.pi
    best_r = -1.0
    best = (pmin, 1.0, 0.0)
    for i in range(n + 1):
        p = pmin + (pmax - pmin) * i / n
        w = tau / p
        c = s = 0.0
        for e in edges:
            a = w * e
            c += cos(a)
            s += sin(a)
        r = c * c + s * s
        if r > best_r:
            best_r, best = r, (p, c, s)
    p, c, s = best
    phase = math.atan2(s, c) * p / tau  # lattice points sit at phase + k*p
    x0 = phase + p * math.floor((x_ref - phase) / p + 0.5)
    tol = max(0.5, 0.45 * p)
    groups: dict[int, list[float]] = {}
    for e in edges:
        k = int(math.floor((e - x0) / p + 0.5))
        if 0 <= k <= _TOTAL_CELLS and k != 4 and abs(e - (x0 + k * p)) <= tol:
            groups.setdefault(k, []).append(e)
    if len(groups) >= 3:
        x0, p = _lsq({k: sum(v) / len(v) for k, v in groups.items()})
    return x0, p


def _sample_codes(row: str, x0: float, pitch: float, width: int) -> Optional[list[tuple[int, int, int]]]:
    cells = []
    for i in range(_HEADER_CELLS, _TOTAL_CELLS):
        xi = int(math.floor(x0 + (i + 0.5) * pitch))
        if xi < 0 or xi >= width:
            return None
        cells.append(_CHAR_RGB[row[xi]])
    return cells


def _pixel(buf: bytes, base: int, x: int) -> tuple[int, int, int]:
    o = base + x * 4
    return buf[o + 2], buf[o + 1], buf[o]  # BGRA -> (R, G, B)


def _sample_calibrated(buf: bytes, base: int, x0: float, pitch: float,
                       width: int) -> Optional[list[tuple[int, int, int]]]:
    """Sample payload cells using per-channel thresholds measured on the header.

    Magenta cells give the "on" level of R and B and the "off" level of G; green
    cells the opposite. Mid-points of those levels become the thresholds, which
    copes with tone-mapped (HDR), tinted or dimmed captures.
    """
    xs = [int(math.floor(x0 + (i + 0.5) * pitch)) for i in range(_TOTAL_CELLS)]
    if xs[0] < 0 or xs[-1] >= width:
        return None
    m1, g1, m2, g2 = (_pixel(buf, base, xs[i]) for i in range(4))
    on = ((m1[0] + m2[0]) / 2, (g1[1] + g2[1]) / 2, (m1[2] + m2[2]) / 2)
    off = ((g1[0] + g2[0]) / 2, (m1[1] + m2[1]) / 2, (g1[2] + g2[2]) / 2)
    if min(on[c] - off[c] for c in range(3)) < 24:
        return None  # no usable contrast
    thr = tuple((on[c] + off[c]) / 2 for c in range(3))
    cells = []
    for x in xs[_HEADER_CELLS:]:
        p = _pixel(buf, base, x)
        cells.append(tuple(255 if p[c] >= thr[c] else 0 for c in range(3)))
    return cells  # type: ignore[return-value]


_PERTURB = ((0.0, 1 / 150), (0.0, -1 / 150), (0.0, 2 / 150), (0.0, -2 / 150), (0.0, 3 / 150),
            (0.0, -3 / 150), (0.2, 0.0), (-0.2, 0.0))  # (dx0 in cells, dpitch relative)


def _try_decode(row: str, buf: bytes, base: int, x0: float, pitch: float, width: int, *,
                plain: bool = True, extra: bool = True) -> Optional[tuple[Event, float, float]]:
    """Sample + checksum-validate at (x0, pitch); ``extra`` adds calibrated/perturbed retries."""
    if plain:
        cells = _sample_codes(row, x0, pitch, width)
        if cells is not None:
            ev = protocol.decode_cells(cells)
            if ev is not None:
                return ev, x0, pitch
    if not extra:
        return None
    cells = _sample_calibrated(buf, base, x0, pitch, width)
    if cells is not None:
        ev = protocol.decode_cells(cells)
        if ev is not None:
            return ev, x0, pitch
    letters = _sample_runs(row, x0, pitch, width)
    if letters is not None:
        ev = protocol.decode_cells([_CHAR_RGB[c] for c in letters])
        if ev is not None:
            return ev, x0, pitch
    for dx, dp in _PERTURB:
        x1, p1 = x0 + dx * pitch, pitch * (1 + dp)
        cells = _sample_codes(row, x1, p1, width)
        if cells is not None:
            ev = protocol.decode_cells(cells)
            if ev is not None:
                return ev, x1, p1
    return None


_KNOWN_PREFIX = ("g", "c", "r", "b")  # payload cells 0-3: fixed by the magic byte + version nibble


def _sample_runs(row: str, x0: float, pitch: float, width: int,
                 edges: Optional[list[int]] = None) -> Optional[list[str]]:
    """Payload cell letters from colour *runs* instead of single centre pixels.

    Each transition is assigned to the nearest lattice boundary; a cell takes the
    colour that starts at its boundary, or keeps the previous cell's colour when
    no transition was seen there (equal neighbours). This only needs the pitch to
    within ~1.5% (boundary index error < half a cell at the strip's end), where
    centre sampling of 1-2 px cells needs ~0.2%.
    """
    first = int(math.floor(x0 + (_HEADER_CELLS + 0.5) * pitch))
    if first < 0 or x0 + (_TOTAL_CELLS - 0.5) * pitch >= width:
        return None
    if edges is None:
        edges = _transitions(row, int(x0 + 3.5 * pitch), min(width, int(x0 + _TOTAL_CELLS * pitch) + 1))
    starts: dict[int, int] = {}
    for e in edges:
        k = int(math.floor((e - x0) / pitch + 0.5))
        if _HEADER_CELLS < k < _TOTAL_CELLS:
            if e > starts.get(k, -1):
                starts[k] = e  # after a blended pixel, the later transition carries the colour
    cur = row[first]
    out = []
    for k in range(_HEADER_CELLS, _TOTAL_CELLS):
        s = starts.get(k)
        if s is not None:
            cur = row[int(s)]
        out.append(cur)
    return out


_ARC_MAX_CELL = 1.8  # below this, centre sampling needs the pitch to ~0.1%: search it exactly
_ARC_STEP = 0.001  # pitch step (px); decodable pitch windows are >= ~0.003 px wide
_ARC_TIE_SLACK = 0.02  # cell edges exactly on a pixel centre (exact rational scales) are ties


def _arc_search(row: str, pts: dict[int, float], cell: float,
                width: int) -> Optional[tuple[Event, float, float]]:
    """Exact lattice search for 1-2 px cells (e.g. 1.1 px after display scaling).

    With such cells a centre sample is only ~0.05 px from the neighbouring cell,
    so the pitch must be known to ~0.1%, which no fit of quantised edges can
    promise (and integer edges always look coherent at pitch 1.0). Instead use
    the rasterisation rule exactly: a pixel belongs to the cell containing its
    centre, so a transition at pixel ``e`` (first pixel of a new cell) means
    some boundary satisfies ``e - 0.5 < x0 + k*P <= e + 0.5``. For a trial pitch
    P that holds for every transition on the strip iff the phases
    ``(e - 0.5) mod P`` fit in a circular arc of length 1 (largest circular gap
    >= P - 1); the arc then gives the feasible x0. Each feasible trial is decoded
    from runs (:func:`_sample_runs`) and must read GREEN, CYAN, RED, BLUE in the
    fixed payload cells and pass the magic/version/Fletcher-16 checks. Only
    differences of transitions matter, so it is independent of the resampler's
    half-pixel convention. Requires the fixed payload prefix to be visible.
    """
    items = [(k, x) for k, x in pts.items() if float(x).is_integer()]
    if len(items) < 2:
        return None
    plo, phi = 1.0, cell * 1.35 + 0.35  # a cell narrower than a pixel cannot be rendered
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (ki, xi), (kj, xj) = items[i], items[j]
            dk, de = kj - ki, xj - xi
            if dk == 0:
                continue
            if dk < 0:
                dk, de = -dk, -de
            plo = max(plo, (de - 1.0) / dk)
            phi = min(phi, (de + 1.0) / dk)
    if plo >= phi:
        return None
    x_left = min(x - k * plo for k, x in items)
    edges = [float(x) for _k, x in items]
    edges.extend(float(e) for e in _transitions(row, max(1, int(max(x for _k, x in items)) + 1),
                                                 min(width, int(x_left + _TOTAL_CELLS * phi) + 2)))
    edges.sort()
    n_steps = int((phi - plo) / _ARC_STEP) + 1
    seen: set[str] = set()
    for i in range(n_steps + 1):
        p = plo + (phi - plo) * i / n_steps
        end = x_left + _TOTAL_CELLS * p + 0.75
        us = sorted((e - 0.5) % p for e in edges if e <= end)
        need = p - 1.0 - _ARC_TIE_SLACK
        gaps = [(us[0] + p - us[-1], 0)] if us[0] + p - us[-1] >= need else []
        gaps.extend((us[j] - us[j - 1], j) for j in range(1, len(us)) if us[j] - us[j - 1] >= need)
        if not gaps:
            continue
        x_ref = sum(x - k * p for k, x in items) / len(items)
        # Each wide-enough circular gap leaves the phases inside an arc of length
        # <= 1 and so defines one feasible x0 interval (a, b] (several when gaps tie).
        for _g, at in sorted(gaps, reverse=True)[:3]:
            lo_u = us[at]
            hi_u = us[at - 1] + p if at else us[-1]
            shift = p * math.floor((x_ref - (hi_u + lo_u + 1.0) / 2) / p + 0.5)
            a, b = hi_u + shift, lo_u + 1.0 + shift
            # The middle is safest; with edges tied to pixel centres the true origin
            # sits at an end of the interval, so try just inside both ends as well.
            for x0 in ((a + b) / 2, b - 0.01, a + 0.01):
                if x0 + (_TOTAL_CELLS - 0.5) * p >= width or x0 < -0.5 * p:
                    continue
                # Every transition sits within ~0.5 px of its boundary here, so
                # assigning transitions to boundaries (runs) is exact even where a
                # centre sample would be ambiguous (cell edges tied with pixel centres).
                letters = _sample_runs(row, x0, p, width, edges)
                if letters is None or tuple(letters[:4]) != _KNOWN_PREFIX:
                    continue
                key = "".join(letters)
                if key in seen:
                    continue  # neighbouring trial pitches mostly give the same assignment
                seen.add(key)
                ev = protocol.decode_cells([_CHAR_RGB[c] for c in letters])
                if ev is not None:
                    return ev, x0, p
    return None


def _attempt(row: str, buf: bytes, base: int, fit: Optional[tuple[float, float]], cell: float, width: int, *,
             plain: bool = True, extra: bool = True
             ) -> tuple[Optional[tuple[Event, float, float]], str, Optional[int]]:
    """Validate a lattice fit and try to decode it -> (result, reason, needed_width)."""
    if fit is None:
        return None, "payload invalid", None
    x0, pitch = fit
    if pitch < 0.75 or abs(pitch - cell) > max(1.0, 0.35 * cell):
        return None, "payload invalid", None
    if x0 + (_TOTAL_CELLS - 0.5) * pitch >= width:
        return None, "strip truncated", int(math.ceil(x0 + _TOTAL_CELLS * pitch)) + 2
    got = _try_decode(row, buf, base, x0, pitch, width, plain=plain, extra=extra)
    return got, ("ok" if got else "payload invalid"), None


class _Cluster:
    """One strip candidate seen across consecutive rows (dedupes per-row work)."""

    __slots__ = ("x", "cell", "top", "tries", "retry_row")

    def __init__(self, x: float, cell: float, top: int) -> None:
        self.x, self.cell, self.top, self.tries = x, cell, top, 0
        self.retry_row = top + max(1, int(cell / 2))

    def matches(self, x: float, cell: float) -> bool:
        return abs(x - self.x) <= max(1.5, 0.3 * self.cell) and abs(cell - self.cell) <= max(1.0, 0.3 * self.cell)


def scan(buf: bytes | bytearray | memoryview, width: int, height: int, stride: int, *,
         threshold: int = 128, max_offset: int = 48,
         max_candidates: int = DEFAULT_MAX_CANDIDATES) -> ScanReport:
    """Locate and decode the beacon strip; never raises.

    See :func:`find_and_decode`. The report also says *why* nothing decoded
    (no header / payload invalid / strip truncated) and where a header was seen,
    which the transport uses for diagnostics and to widen its capture region.
    """
    try:
        return _scan(buf, int(width), int(height), int(stride), int(threshold), int(max_offset),
                     int(max_candidates))
    except Exception as e:  # decoder must never take the capture thread down
        log.debug("beacon decoder error", exc_info=True)
        return ScanReport(None, None, f"decoder error: {e.__class__.__name__}: {e}")


def find_and_decode(buf: bytes | bytearray | memoryview, width: int, height: int, stride: int, *,
                    threshold: int = 128, max_offset: int = 48) -> Optional[DecodeResult]:
    """Find the beacon strip in a top-down BGRA buffer and decode it.

    ``buf`` holds ``height`` rows of ``width`` BGRA pixels, ``stride`` bytes
    apart. A channel is "on" when ``>= threshold``. The strip's top-left must
    lie within ``max_offset`` px of the buffer's top-left (window chrome or a
    slightly misplaced client origin). Returns None (never raises) for garbage,
    partial/occluded strips or checksum failures.
    """
    return scan(buf, width, height, stride, threshold=threshold, max_offset=max_offset).result


def _ok(got: tuple[Event, float, float], top: int, examined: int) -> ScanReport:
    ev, x1, p1 = got
    hit = HeaderHit(int(round(x1)), top, round(p1, 2))
    return ScanReport(DecodeResult(ev, hit.cell_px, hit.x, hit.y), hit, "ok", None, examined)


def _scan(buf: Any, width: int, height: int, stride: int, threshold: int, max_offset: int,
          max_candidates: int) -> ScanReport:
    if width <= 0 or height <= 0 or stride < width * 4 or not (1 <= threshold <= 255):
        return ScanReport(None, None, "bad buffer")
    if not isinstance(buf, bytes):
        buf = bytes(buf)
    row_bytes = width * 4
    rows = min(height, max(0, max_offset) + _ROW_SLACK)
    if len(buf) < row_bytes:
        return ScanReport(None, None, "bad buffer")
    rows = min(rows, (len(buf) - row_bytes) // stride + 1)
    if stride == row_bytes:
        data = buf[: rows * stride]
    else:
        data = b"".join(buf[y * stride: y * stride + row_bytes] for y in range(rows))
    codes = _classify(data, threshold)
    max_x = max(0, max_offset)

    clusters: list[_Cluster] = []
    first_hit: Optional[HeaderHit] = None
    reason = "no header"
    needed_width: Optional[int] = None
    examined = 0
    heavy_budget = _PHASE_BUDGET
    arc_budget = _ARC_BUDGET
    deferred: list[tuple[str, int, dict[int, float], float, tuple[float, float], int, str, Optional[int]]] = []

    for y in range(rows):
        row = codes[y * width:(y + 1) * width]
        head = row[: max_x + 1]
        if "m" not in head:
            continue
        for m in _HEADER_RE.finditer(row):
            if m.start() > max_x:
                break
            geom = _header_geometry(row, m)
            if geom is None:
                continue
            pts, cell = geom
            x_est = pts.get(0, pts[1] - cell)
            cl = next((c for c in clusters if c.matches(x_est, cell)), None)
            if cl is None:
                cl = _Cluster(x_est, cell, y)
                clusters.append(cl)
            elif cl.tries >= 2 or (cl.tries == 1 and y < cl.retry_row):
                continue
            if examined >= max_candidates:
                break
            examined += 1
            cl.tries += 1
            base = y * stride
            cheap = _fit_lattice(row, pts, width)
            if first_hit is None:
                first_hit = HeaderHit(int(round(cheap[0])), cl.top, round(cheap[1], 2))
            # Pass 1: cheap fit + plain centre sampling - the normal case for a clean strip.
            got, why, need = _attempt(row, buf, base, cheap, cell, width, extra=False)
            if got is not None:
                return _ok(got, cl.top, examined)
            deferred.append((row, base, pts, cell, cheap, cl.top, why, need))

    # Pass 2 (only when nothing decoded cheaply), top-most candidates first and
    # bounded per frame: lattice-coherence fit, exact small-cell search, then
    # calibrated / perturbed retries around the cheap fit.
    for row, base, pts, cell, cheap, top, why, need in deferred:
        got = None
        if heavy_budget > 0:
            heavy_budget -= 1
            fit = _phase_lattice(row, pts, cell, width)
            got, why2, need2 = _attempt(row, buf, base, fit, cell, width)
            if why2 == "strip truncated" or why != "strip truncated":
                why, need = why2, need2 or need
            if got is None and why != "strip truncated" and cell < _ARC_MAX_CELL and 7 in pts and arc_budget > 0:
                arc_budget -= 1
                got = _arc_search(row, pts, cell, width)
        if got is None and why != "strip truncated":
            got, why, need = _attempt(row, buf, base, cheap, cell, width, plain=False)
        if got is not None:
            return _ok(got, top, examined)
        if why == "strip truncated":
            reason = why
            needed_width = max(needed_width or 0, need or 0)
        elif reason != "strip truncated":
            reason = why
    return ScanReport(None, first_hit, reason, needed_width, examined)


def describe_event(ev: Optional[AnyEvent]) -> str:
    """Compact one-line rendering of a v1 event or a v2 frame for logs / diag."""
    if ev is None:
        return "-"
    if isinstance(ev, Frame2):
        return ev.describe()
    return f"{ev.type.name} seq={ev.seq} n={ev.n} hash={ev.hash} flags=0x{int(ev.flags):02x}"


def _frame_key(ev: AnyEvent) -> tuple:
    """Identity of a decoded strip for the "2 identical reads" rule: every byte of a v2 frame,
    ``(seq, type, n, hash, flags)`` of a v1 event."""
    if isinstance(ev, Frame2):
        return ("v2", ev.raw or ev.to_bytes())
    return (ev.seq, int(ev.type), ev.n, ev.hash, int(ev.flags))


# ------------------------------------------------------------ capture seam --

class CaptureBackend(_TypingProtocol):
    """What :class:`PixelTransport` needs from the OS. Tests inject a fake."""

    def available(self) -> tuple[bool, str]: ...

    def prepare(self) -> str: ...

    def find_window(self) -> Optional[int]: ...

    def window_alive(self, hwnd: int) -> bool: ...

    def is_minimized(self, hwnd: int) -> bool: ...

    def client_rect(self, hwnd: int) -> tuple[int, int, int, int]: ...

    def capture_screen(self, x: int, y: int, w: int, h: int) -> Frame: ...

    def capture_window(self, hwnd: int, w: int, h: int) -> Frame: ...

    def describe(self, hwnd: int) -> str: ...


class Win32CaptureBackend:
    """Default backend: real Win32 calls via :mod:`.wincapture`."""

    def __init__(self, game_exe: Any = None, title: str = "World of Warcraft") -> None:
        self.game_exe = game_exe
        self.title = title
        self._capturer = wincapture.WindowCapturer()  # PrintWindow bitmaps reused between frames

    def available(self) -> tuple[bool, str]:
        if not wincapture.IS_WINDOWS:
            return False, "the pixel beacon needs Windows (it reads the WoW window's pixels)"
        return True, ""

    def prepare(self) -> str:
        return wincapture.set_dpi_awareness()

    def find_window(self) -> Optional[int]:
        # strict: never a window that only has the game's title (PIX-WINDOW-LATCH)
        return wincapture.find_game_window(self.game_exe, self.title, strict=True)

    def list_windows(self) -> list[tuple[int, int]]:
        """``(hwnd, score)`` of every window that could be the game, best first (strict matching)."""
        return [(w.hwnd, w.score) for w in wincapture.list_game_windows(self.game_exe, self.title, strict=True)]

    def window_score(self, hwnd: int) -> int:
        """The window's strict score now; 0 when it is gone or no longer the game's (handle reused)."""
        return wincapture.window_matches(hwnd, self.game_exe, self.title)

    def window_alive(self, hwnd: int) -> bool:
        return wincapture.is_window(hwnd)

    def is_minimized(self, hwnd: int) -> bool:
        return wincapture.is_minimized(hwnd)

    def client_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        return wincapture.client_rect(hwnd)

    def capture_screen(self, x: int, y: int, w: int, h: int) -> Frame:
        return wincapture.capture_screen_region(x, y, w, h)

    def capture_window(self, hwnd: int, w: int, h: int) -> Frame:
        return self._capturer.capture(hwnd, w, h)

    def close(self) -> None:
        """Free cached capture bitmaps (called when the transport stops)."""
        self._capturer.close()

    def describe(self, hwnd: int) -> str:
        try:
            info = wincapture.describe_window(hwnd)
        except RuntimeError:
            info = None
        if info is None:
            return f"hwnd 0x{hwnd:x}"
        return f"'{info.title}' pid {info.pid} ({info.exe or 'exe unknown'})"


def _num(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return min(hi, max(lo, f))


# --------------------------------------------------------------- transport --

class PixelTransport(Transport):
    """Polls the WoW window's top-left corner and decodes the beacon strip.

    Config (``transport.pixel``): ``enabled`` (True), ``method`` ("auto" |
    "screen" | "printwindow"), ``hz`` (4), ``regionW`` (512), ``regionH`` (48);
    optional ``threshold`` (128), ``maxOffset`` (48), ``windowTitle``, ``corner``
    ("TOPLEFT"; the addon's advanced ``beaconCorner`` setting -- other corners are
    captured from the screen).

    Each capture is decoded as a v2 strip first (:func:`beacon2.scan`); only when
    no v2 header is visible is the v1 decoder tried (addons older than 1.2). A
    strip wider or taller than the capture region widens the region (up to the
    client area).

    Acceptance rules (SPEC 3.5, unchanged for v2):

    * a frame counts only after **2 consecutive identical valid reads**
      (an unreadable frame in between breaks the streak);
    * the first valid frame after start, or after the game window was lost and
      found again, is the **baseline**: remembered, never emitted (so a stale
      FIRE left on screen is not replayed when the companion restarts);
    * after that, ``on_event(event, "pixel")`` fires whenever the confirmed
      frame differs from the last one (v2: any byte of the frame; v1: the
      ``(seq, type, n, hash, flags)`` tuple). ``event`` is a
      :class:`~needledropper.protocol2.Frame` or a v1 ``Event``. Every type is
      passed through (HELLO, PING, ...); the engine filters/dedupes.

    Minimizing the game pauses capture but keeps the baseline, so an event that
    happened while minimized is still delivered when the window is restored.

    Capture method "auto" starts with a screen BitBlt (cheap, and what the player
    sees); after :attr:`SWITCH_TO_PRINT_AFTER` consecutive frames without a visible
    header (about 1.5 s: e.g. character select, or something covering the corner)
    it tries PrintWindow. While on PrintWindow it probes one BitBlt every
    :attr:`PROBE_EVERY` frames and switches back as soon as the strip shows on
    screen again; PrintWindow that also sees nothing for :attr:`SWITCH_AFTER`
    frames (or fails) flips back to the screen too.

    **The window** (PIX-WINDOW-LATCH, real-client finding #2): the backend's search
    never accepts a window by its title alone. The cached window is re-validated
    every :attr:`REVALIDATE_INTERVAL` seconds (a handle that now belongs to another
    program counts as lost), and until a beacon header has been seen on it the
    search runs again every :attr:`REFIND_INTERVAL` seconds and moves to a better
    match (the configured ``_forever_\\Wow.exe``) when one appears -- so a
    short-lived window at game start is never kept for the session.

    For tests and tools, :meth:`step` runs one capture/decode iteration
    synchronously and :meth:`feed` applies the acceptance rules to an already
    decoded frame; ``backend`` and ``clock`` are injectable.
    """

    name = "pixel"
    REFIND_INTERVAL = 5.0  # seconds between window searches while it is missing (or no header seen yet)
    REVALIDATE_INTERVAL = 5.0  # seconds between checks that the cached window is still the game's
    HEALTH_INTERVAL = 5.0  # seconds between diag health summaries
    SWITCH_TO_PRINT_AFTER = 6  # header-less screen frames (~1.5 s at 4 Hz) before "auto" tries PrintWindow
    SWITCH_AFTER = 20  # header-less PrintWindow frames before "auto" goes back to the screen
    PROBE_EVERY = 8  # on PrintWindow, one screen BitBlt every this many frames (back as soon as it sees the strip)
    LOST_AFTER = 8  # unreadable frames before diag reports the beacon as gone
    MAX_REGION_W = 2048

    def __init__(self, config_section: Optional[dict[str, Any]], paths: Any, on_event: EventCallback,
                 diag: Optional[DiagCallback] = None, *, backend: Optional[CaptureBackend] = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        super().__init__(dict(config_section or {}), paths, on_event)
        cfg = self.config
        self.diag = diag
        self.clock = clock
        self.wall = wall
        self.enabled = bool(cfg.get("enabled", True))
        self.hz = _num(cfg.get("hz", 4), 4.0, 0.2, 30.0)
        self.region_w = int(_num(cfg.get("regionW", V2_REGION[0]), V2_REGION[0], 16, self.MAX_REGION_W))
        self.region_h = int(_num(cfg.get("regionH", V2_REGION[1]), V2_REGION[1], 2, 512))
        self.threshold = int(_num(cfg.get("threshold", 128), 128, 1, 255))
        self.max_offset = int(_num(cfg.get("maxOffset", 48), 48, 0, 512))
        method = str(cfg.get("method", "auto") or "auto").strip().lower()
        method = {"bitblt": "screen", "gdi": "screen", "print": "printwindow"}.get(method, method)
        self._config_warning: Optional[str] = None
        if method not in ("auto", "screen", "printwindow"):
            self._config_warning = (f"unknown transport.pixel.method {cfg.get('method')!r}; "
                                    "using 'auto' (valid: auto, screen, printwindow)")
            method = "auto"
        self.method_cfg = method
        corner = str(cfg.get("corner") or "TOPLEFT").strip().upper()
        if corner not in CORNERS:
            self._config_warning = (f"unknown transport.pixel.corner {cfg.get('corner')!r}; using 'TOPLEFT' "
                                    f"(valid: {', '.join(CORNERS)})")
            corner = "TOPLEFT"
        self.corner = corner
        title = str(cfg.get("windowTitle") or "World of Warcraft")
        self.backend: CaptureBackend = backend or Win32CaptureBackend(getattr(paths, "game_exe", None), title)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._dpi: Optional[str] = None
        # window
        self._hwnd: Optional[int] = None
        self._hwnd_score: Optional[int] = None  # the backend's match score (None: it cannot tell)
        self._header_on_window = False  # a beacon header was seen on the current window
        self._window_desc = ""
        self._next_find = float("-inf")
        self._next_validate = float("-inf")
        self._minimized = False
        self._ever_found = False
        self._window_switches = 0
        # capture
        self._method = "printwindow" if method == "printwindow" else "screen"
        self._no_header_streak = 0
        self._switches_without_header = 0
        self._since_probe = 0
        self._print_failed = False  # PrintWindow raised: wait the long streak before trying it again
        self._needed_w = 0
        self._needed_h = 0
        #: v1.2.1 (DESIGN-v1.2.1 3.1): called as ``on_baseline(frame, "pixel")``, outside the lock, with
        #: every frame that becomes the baseline (never emitted): the app applies the music choices a
        #: baseline HELLO carries, and nothing else.
        self.on_baseline: Optional[Callable[[Any, str], None]] = None
        # acceptance state
        self._accepted: Optional[tuple] = None  # baseline / last confirmed
        self._candidate: Optional[tuple] = None
        self._candidate_count = 0
        self._protocol: Optional[int] = None  # 2 or 1: the last strip that decoded
        self._geometry: Optional[tuple[int, int]] = None  # (rows, cols) of the last v2 strip
        # stats
        self._frames = 0
        self._valid = 0
        self._failures = 0
        self._capture_errors = 0
        self._emitted = 0
        self._invalid_streak = 0
        self._beacon_visible = False
        self._last_frame_wall: Optional[float] = None
        self._last_valid_event: Optional[AnyEvent] = None
        self._last_emitted: Optional[AnyEvent] = None
        self._last_report: Optional[Any] = None  # ScanReport (v1) or beacon2.ScanReport
        self._strip: Optional[tuple[int, int, float]] = None
        self._last_error: Optional[str] = None
        self._last_error_diag = float("-inf")
        self._last_bad_diag = float("-inf")
        self._frame_times: collections.deque[float] = collections.deque()
        self._last_health = float("-inf")

    # ------------------------------------------------------------ lifecycle --
    def start(self) -> None:
        """Start the capture thread (no-op when disabled or already running)."""
        if self._config_warning:
            self._say(f"pixel: {self._config_warning}")
            self._config_warning = None
        if not self.enabled:
            self._say("pixel: disabled in config (transport.pixel.enabled = false)")
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            ok, why = self.backend.available()
            if not ok:
                self._last_error = why
                self._say(f"pixel: not started - {why}")
                return
            try:
                self._dpi = self.backend.prepare()
            except Exception as e:  # DPI awareness is best effort
                self._dpi = f"failed: {e}"
            self.reset_baseline()  # the first valid frame after (re)start is a baseline
            self._stop.clear()
            self._running = True
            self._thread = threading.Thread(target=self._run, name="needledropper-pixel", daemon=True)
            self._thread.start()
        self._say(f"pixel: started ({self.hz:g} Hz, method {self.method_cfg}, region "
                  f"{self.region_w}x{self.region_h}, DPI awareness {self._dpi})")

    def stop(self) -> None:
        """Stop the capture thread and wait briefly for it to exit."""
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=max(2.0, 3.0 / self.hz))
        with self._lock:
            self._thread = None
            self._running = False
            close = getattr(self.backend, "close", None)
            if callable(close) and not (t is not None and t.is_alive()):
                try:
                    close()
                except Exception as e:  # freeing GDI objects is best effort
                    log.debug("pixel: capture backend close failed: %s", e)

    def _run(self) -> None:
        interval = 1.0 / self.hz
        while not self._stop.is_set():
            t0 = self.clock()
            try:
                self.step()
            except Exception as e:  # pragma: no cover - step() already guards itself
                log.exception("pixel transport iteration failed")
                self._note_error(f"unexpected error: {e}")
            now = self.clock()
            if self.diag is not None and now - self._last_health >= self.HEALTH_INTERVAL:
                self._last_health = now
                self._say(self.health_line(), level=logging.DEBUG)
            self._stop.wait(max(0.0, interval - (now - t0)))
        with self._lock:
            self._running = False

    # ------------------------------------------------------------ one frame --
    def step(self) -> str:
        """Run one find-window/capture/decode iteration.

        Returns a short outcome: ``"no-window"``, ``"minimized"``,
        ``"capture-error"``, ``"invalid"``, ``"baseline"``, ``"pending"``,
        ``"same"`` or ``"emitted"``.
        """
        now = self.clock()
        hwnd = self._ensure_window(now)
        if hwnd is None:
            return "no-window"
        try:
            minimized = bool(self.backend.is_minimized(hwnd))
            rect = None if minimized else self.backend.client_rect(hwnd)
        except Exception as e:
            self._note_error(f"window query failed: {e}")
            return "capture-error"
        if rect is not None and (rect[2] <= 0 or rect[3] <= 0):
            minimized = True
        if minimized != self._minimized:
            self._minimized = minimized
            self._say("pixel: WoW window minimized - capture paused" if minimized
                      else "pixel: WoW window restored - capture resumed")
        if minimized or rect is None:
            return "minimized"

        cx, cy, cw, ch = rect
        w = max(1, min(max(self.region_w, self._needed_w), cw))
        h = max(1, min(max(self.region_h, self._needed_h), ch))
        corner = self.corner
        # PrintWindow renders the client area's top-left; other corners come from the screen.
        method = self._method if corner == "TOPLEFT" else "screen"
        x = cx if corner in ("TOPLEFT", "BOTTOMLEFT") else cx + cw - w
        y = cy if corner in ("TOPLEFT", "TOPRIGHT") else cy + ch - h
        decoded = None
        if method == "printwindow" and self.method_cfg == "auto":
            decoded = self._probe_screen(x, y, w, h, corner)
            if decoded is not None:
                method = "screen"
        if decoded is None:
            try:
                if method == "screen":
                    frame = self.backend.capture_screen(x, y, w, h)
                else:
                    frame = self.backend.capture_window(hwnd, w, h)
            except Exception as e:
                self._capture_errors += 1
                self._note_error(f"{method} capture failed: {e}")
                if self.method_cfg == "auto" and method == "printwindow":
                    self._print_failed = True
                    self._switch_method("screen", f"PrintWindow failed ({e})")
                else:
                    self._count_headerless()
                return "capture-error"
            if method == "printwindow":
                self._print_failed = False
            buf, fw, fh, stride = frame
            decoded = self._decode(buf, fw, fh, stride, corner)

        version, event, report = decoded
        self._last_report = report
        self._note_frame(now)
        if report.header is not None:
            self._no_header_streak = 0
            self._switches_without_header = 0
            if not self._header_on_window:
                self._header_on_window = True  # this is the game's window: stop looking for a better one
        else:
            self._count_headerless()
        self._maybe_widen(report, w, h, cw, ch)
        if event is None:
            self._note_bad_frame(now, report)
            return self.feed(None)[0]
        res = report.result
        strip = (res.x, res.y, res.cell_px)
        if self._strip is None or (strip[0], strip[1], round(strip[2])) != (self._strip[0], self._strip[1],
                                                                             round(self._strip[2])):
            what = f"v2, {res.cols} x {res.rows} cells" if version == 2 else "v1"
            self._say(f"pixel: beacon strip found at ({res.x},{res.y}) in the client area ({what}), "
                      f"cell {res.cell_px:g} px, via {method}")
        if version == 1 and self._protocol != 1:
            self._say("pixel: this is the beacon of a NeedleDropper addon older than 1.2 -- update the addon")
        self._strip = strip
        self._protocol = version
        self._geometry = (res.rows, res.cols) if version == 2 else None
        if not self._beacon_visible:
            self._beacon_visible = True
        self._invalid_streak = 0
        return self.feed(event)[0]

    def _decode(self, buf: Any, w: int, h: int, stride: int, corner: str) -> tuple[int, Optional[AnyEvent], Any]:
        """``(protocol, event or None, report)``: the v2 strip first; the v1 decoder only when no v2
        header is visible (a v1 strip never shows the v2 geometry and prefix cells)."""
        rep2 = beacon2.scan(buf, w, h, stride, corner, threshold=self.threshold, max_offset=self.max_offset)
        if rep2.result is not None:
            return 2, rep2.result.frame, rep2
        if rep2.header is not None or corner != "TOPLEFT":
            return 2, None, rep2  # a v2 strip that did not read cleanly (v1 strips are top-left only)
        rep1 = scan(buf, w, h, stride, threshold=self.threshold, max_offset=self.max_offset)
        if rep1.result is not None:
            return 1, rep1.result.event, rep1
        if rep1.header is not None:
            return 1, None, rep1  # a header-like strip that neither decoder could read
        return 2, None, rep2

    def _maybe_widen(self, report: Any, w: int, h: int, cw: int, ch: int) -> None:
        """Grow the capture region when a strip runs past its right (or bottom) edge."""
        need_w = getattr(report, "needed_width", None)
        if need_w and need_w > w and need_w <= min(cw, self.MAX_REGION_W) and need_w > self._needed_w:
            self._needed_w = need_w
            self._say(f"pixel: beacon strip is wider than the {w} px capture region; "
                      f"widening capture to {need_w} px")
        need_h = getattr(report, "needed_height", None)
        if need_h and need_h > h and need_h <= min(ch, 512) and need_h > self._needed_h:
            self._needed_h = need_h
            self._say(f"pixel: beacon strip is taller than the {h} px capture region; "
                      f"capturing {need_h} px")

    # ---------------------------------------------------------- state rules --
    def feed(self, event: Optional[Event]) -> tuple[str, Optional[Event]]:
        """Apply the acceptance rules to one decoded frame (None = unreadable).

        Returns ``(outcome, emitted_event_or_None)``; calls ``on_event`` when a
        new confirmed frame is accepted.
        """
        emit: Optional[Event] = None
        with self._lock:
            if event is None:
                self._failures += 1
                self._candidate = None
                self._candidate_count = 0
                return "invalid", None
            self._valid += 1
            self._last_frame_wall = self.wall()
            self._last_valid_event = event
            key = _frame_key(event)
            if key == self._candidate:
                self._candidate_count += 1
            else:
                self._candidate = key
                self._candidate_count = 1
            if self._accepted is None:
                self._accepted = key
                outcome = "baseline"
                msg = f"pixel: baseline frame {describe_event(event)} (not played)"
            elif key == self._accepted:
                return "same", None
            elif self._candidate_count < 2:
                outcome = "pending"
                msg = f"pixel: new frame {describe_event(event)} (waiting for a 2nd identical read)"
            else:
                self._accepted = key
                self._emitted += 1
                self._last_emitted = event
                emit = event
                outcome = "emitted"
                msg = f"pixel: event {describe_event(event)}"
        self._say(msg, level=logging.DEBUG if outcome == "pending" else logging.INFO)
        if emit is not None:
            try:
                self.on_event(emit, self.name)
            except Exception:
                log.exception("pixel on_event callback failed")
        elif outcome == "baseline" and self.on_baseline is not None:
            try:
                self.on_baseline(event, self.name)
            except Exception:
                log.exception("pixel on_baseline callback failed")
        return outcome, emit

    def reset_baseline(self) -> None:
        """Forget the baseline: the next valid frame is a new baseline (window lost)."""
        with self._lock:
            self._accepted = None
            self._candidate = None
            self._candidate_count = 0

    # --------------------------------------------------------------- window --
    def _window_score(self, hwnd: int) -> Optional[int]:
        """The backend's current score for ``hwnd`` (0 = not the game's any more), None if it cannot tell."""
        fn = getattr(self.backend, "window_score", None)
        if not callable(fn):
            return None
        try:
            return int(fn(hwnd) or 0)
        except Exception:
            return None

    def _candidates(self) -> Optional[list[tuple[int, int]]]:
        """``(hwnd, score)`` best first from the backend, or None when it only offers ``find_window``."""
        fn = getattr(self.backend, "list_windows", None)
        if not callable(fn):
            return None
        out = []
        for item in fn() or []:
            try:
                h, s = item
                out.append((int(h), int(s)))
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda c: -c[1])
        return out

    def _adopt(self, hwnd: int, score: Optional[int], now: float, *, replacing: Optional[int] = None) -> int:
        self._hwnd = int(hwnd)
        self._hwnd_score = score
        self._header_on_window = False
        self._ever_found = True
        self._next_validate = now + self.REVALIDATE_INTERVAL
        try:
            self._window_desc = self.backend.describe(self._hwnd)
        except Exception:
            self._window_desc = f"hwnd 0x{self._hwnd:x}"
        self.reset_baseline()
        self._no_header_streak = 0
        self._strip = None
        self._beacon_visible = False
        if replacing is not None:
            self._window_switches += 1
            self._say(f"pixel: switching to a better match for the WoW window: {self._window_desc} "
                      f"(the previous window showed no beacon)")
        else:
            self._say(f"pixel: found WoW window {self._window_desc}")
        return self._hwnd

    def _lose_window(self, now: float, why: str) -> None:
        self._hwnd = None
        self._hwnd_score = None
        self._header_on_window = False
        self._minimized = False
        self._strip = None
        self._beacon_visible = False
        self.reset_baseline()
        self._say(f"pixel: WoW window lost ({why}) - will look for it every {self.REFIND_INTERVAL:g} s; "
                  "next beacon frame becomes a new baseline")
        self._next_find = now

    def _ensure_window(self, now: float) -> Optional[int]:
        hwnd = self._hwnd
        if hwnd is not None:
            try:
                alive = bool(self.backend.window_alive(hwnd))
            except Exception:
                alive = False
            why = "game closed?"
            if alive and now >= self._next_validate:
                # Re-validate (PIX-WINDOW-LATCH): the handle may now belong to another program, or the
                # window stopped looking like the game (hidden, cloaked, another exe).
                self._next_validate = now + self.REVALIDATE_INTERVAL
                score = self._window_score(hwnd)
                if score == 0:
                    alive, why = False, "it is not the game's window any more"
                elif score is not None:
                    self._hwnd_score = score
            if alive:
                if not self._header_on_window and now >= self._next_find:
                    # No beacon seen on this window yet: a better match (the configured _forever_\Wow.exe)
                    # may have appeared since -- e.g. we found a short-lived window at game start.
                    self._next_find = now + self.REFIND_INTERVAL
                    try:
                        cands = self._candidates()
                    except Exception as e:
                        self._note_error(f"window search failed: {e}")
                        cands = None
                    if cands:
                        best_h, best_s = cands[0]
                        mine = self._hwnd_score if self._hwnd_score is not None else -1
                        if best_h != hwnd and best_s > mine:
                            return self._adopt(best_h, best_s, now, replacing=hwnd)
                return hwnd
            self._lose_window(now, why)
        if now < self._next_find:
            return None
        self._next_find = now + self.REFIND_INTERVAL
        try:
            cands = self._candidates()
            if cands is None:
                found = self.backend.find_window()
                best = (int(found), None) if found else None
            else:
                best = cands[0] if cands else None
        except Exception as e:
            self._note_error(f"window search failed: {e}")
            return None
        if not best:
            return None
        return self._adopt(best[0], best[1], now)

    # --------------------------------------------------------------- method --
    def _count_headerless(self) -> None:
        self._no_header_streak += 1
        if self.method_cfg != "auto":
            return
        if self._method == "screen":
            # Faster to PrintWindow (the real client needed it at character select) -- but only the first
            # time: once PrintWindow has also shown nothing (loading screen, Alt+Z, beacon off) or failed,
            # the long streak applies, so a strip-less game is not captured the expensive way most of the
            # time. Any frame with a header resets this (and the probe brings the screen back).
            fast = self._switches_without_header == 0 and not self._print_failed
            limit = self.SWITCH_TO_PRINT_AFTER if fast else self.SWITCH_AFTER
            if self._no_header_streak >= limit:
                self._switch_method("printwindow", f"no beacon header in {limit} frames via the screen")
        elif self._no_header_streak >= self.SWITCH_AFTER:
            self._switch_method("screen", f"no beacon header in {self.SWITCH_AFTER} frames via printwindow")

    def _probe_screen(self, x: int, y: int, w: int, h: int, corner: str) -> Optional[tuple]:
        """On PrintWindow ("auto"): every :attr:`PROBE_EVERY` frames one screen BitBlt; when it shows a
        beacon header the screen is used again (this frame's decode is returned), else None."""
        self._since_probe += 1
        if self._since_probe < self.PROBE_EVERY:
            return None
        self._since_probe = 0
        try:
            buf, fw, fh, stride = self.backend.capture_screen(x, y, w, h)
            decoded = self._decode(buf, fw, fh, stride, corner)
        except Exception as e:  # a failed probe changes nothing
            log.debug("pixel: screen probe failed: %s", e)
            return None
        if decoded[2].header is None:
            return None
        self._switch_method("screen", "the beacon is visible on screen again")
        return decoded

    def _switch_method(self, new: str, why: str) -> None:
        if new == self._method:
            return
        self._method = new
        self._no_header_streak = 0
        self._since_probe = 0
        self._switches_without_header += 1
        # Once both methods have come up empty, keep cycling quietly (the health
        # summary still says no beacon is visible).
        quiet = self._switches_without_header > 2
        self._say(f"pixel: {why}; switching capture to {new}", level=logging.DEBUG if quiet else logging.INFO)

    # ---------------------------------------------------------- bookkeeping --
    def _note_frame(self, now: float) -> None:
        self._frames += 1
        ft = self._frame_times
        ft.append(now)
        while ft and now - ft[0] > self.HEALTH_INTERVAL:
            ft.popleft()

    def _note_bad_frame(self, now: float, report: ScanReport) -> None:
        self._invalid_streak += 1
        if self._beacon_visible and self._invalid_streak >= self.LOST_AFTER:
            self._beacon_visible = False
            self._say(f"pixel: beacon no longer readable ({report.reason}) - covered, hidden "
                      "(/needle beacon on), or UI hidden (Alt+Z)?")
        if report.header is not None and now - self._last_bad_diag >= self.HEALTH_INTERVAL:
            self._last_bad_diag = now
            extra = (f"; needs a capture width of {report.needed_width} px"
                     if report.reason == "strip truncated" and report.needed_width else "")
            self._say(f"pixel: header seen at ({report.header.x},{report.header.y}) cell "
                      f"{report.header.cell_px:g} px but frame unreadable: {report.reason}{extra}")

    def _note_error(self, msg: str) -> None:
        self._last_error = msg
        now = self.clock()
        if now - self._last_error_diag >= self.HEALTH_INTERVAL:
            self._last_error_diag = now
            self._say(f"pixel: {msg}")
        log.debug("pixel: %s", msg)

    def _say(self, line: str, level: int = logging.INFO) -> None:
        log.log(level, line)
        if self.diag is not None:
            try:
                self.diag(line)
            except Exception:
                log.exception("pixel diag callback failed")

    def _fps(self) -> float:
        ft = self._frame_times
        if len(ft) < 2:
            return 0.0
        span = ft[-1] - ft[0]
        return round((len(ft) - 1) / span, 2) if span > 0 else 0.0

    # --------------------------------------------------------------- status --
    def _detail(self) -> str:
        if not self.enabled:
            return "disabled in config"
        if self._hwnd is None:
            if self._last_error and not self._ever_found and not self._running:
                return self._last_error
            return "waiting for the World of Warcraft window (is the game running?)"
        if self._minimized:
            return "WoW is minimized - capture paused"
        if self._beacon_visible and self._strip is not None:
            x, y, cell = self._strip
            if self._protocol == 1:
                return (f"beacon OK (old v1 addon: update NeedleDropper to 1.2): {cell:g} px cells at ({x},{y}) "
                        f"via {self._method}")
            return f"beacon OK: {cell:g} px cells at ({x},{y}) via {self._method}"
        rep = self._last_report
        if rep is not None and rep.header is not None:
            # A strip that keeps failing the frame check is most often an addon and an app that don't match:
            # 1.2.1 changed the signal format (review ON-2).
            return (f"beacon header visible but unreadable ({rep.reason}): an older or newer NeedleDropper addon? "
                    "Update the addon and the app together")
        if self._frames == 0:
            return "window found; no frame captured yet"
        return ("no beacon visible in the top-left corner - is the addon loaded, '/needle beacon on', "
                "and the UI shown?")

    def health_line(self) -> str:
        """One-line summary for diag output."""
        with self._lock:
            st = self.status()
        age = st["lastFrameAge"]
        return (f"pixel: window={'yes' if st['window'] else 'no'}"
                f"{' (minimized)' if st['minimized'] else ''} method={st['method']} fps={st['fps']:g} "
                f"frames={st['frames']} valid={st['validFrames']} fail={st['decodeFailures']} "
                f"captureErr={st['captureErrors']} lastFrame={'-' if age is None else f'{age:.1f}s ago'} "
                f"last={st['lastEvent'] or '-'} | {st['detail']}")

    def status(self) -> dict[str, Any]:
        with self._lock:
            now_wall = self.wall()
            age = None if self._last_frame_wall is None else max(0.0, now_wall - self._last_frame_wall)
            fresh = age is not None and age < max(3.0, 4.0 / self.hz)
            return {
                "name": self.name,
                "enabled": self.enabled,
                "running": bool(self._running and self._thread is not None and self._thread.is_alive()),
                "ok": bool(self._hwnd is not None and not self._minimized and fresh and self._beacon_visible),
                "window": self._hwnd is not None,
                "hwnd": self._hwnd,
                "windowInfo": self._window_desc or None,
                "windowScore": self._hwnd_score,
                "windowSwitches": self._window_switches,
                "minimized": self._minimized,
                "method": self._method,
                "configuredMethod": self.method_cfg,
                "corner": self.corner,
                "dpiAwareness": self._dpi,
                "region": [max(self.region_w, self._needed_w), max(self.region_h, self._needed_h)],
                "protocol": self._protocol,
                "rows": self._geometry[0] if self._geometry else None,
                "cols": self._geometry[1] if self._geometry else None,
                "lastFrame": self._last_frame_wall,
                "lastFrameAge": None if age is None else round(age, 2),
                "lastFrameEvent": describe_event(self._last_valid_event) if self._last_valid_event else None,
                "lastEvent": describe_event(self._last_emitted) if self._last_emitted else None,
                "baseline": self._accepted is not None,
                "frames": self._frames,
                "validFrames": self._valid,
                "decodeFailures": self._failures,
                "captureErrors": self._capture_errors,
                "eventsEmitted": self._emitted,
                "fps": self._fps(),
                "cellPx": self._strip[2] if self._strip else None,
                "stripAt": [self._strip[0], self._strip[1]] if self._strip else None,
                "lastError": self._last_error,
                "detail": self._detail(),
            }


__all__ = ["DecodeResult", "HeaderHit", "ScanReport", "scan", "find_and_decode", "describe_event",
           "CaptureBackend", "Win32CaptureBackend", "PixelTransport"]
