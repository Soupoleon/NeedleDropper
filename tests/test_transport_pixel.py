"""Pixel beacon: pure decoder on synthetic BGRA frames + PixelTransport state logic."""
from __future__ import annotations

import math
import random
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import protocol  # noqa: E402
from needledropper.models import Event, EventType, Flags  # noqa: E402
from needledropper.transport import pixel  # noqa: E402
from needledropper.transport.pixel import PixelTransport, find_and_decode, scan  # noqa: E402

# ------------------------------------------------------------------ images --

EVENTS = [
    Event(EventType.FIRE, 7, 12, 40213, Flags.LOCAL_PLAYBACK | Flags.DROP_ACTIVE),
    Event(EventType.HELLO, 0, 0, 1, Flags.NONE),
    Event(EventType.CANCEL, 255, 65535, 65535, Flags(0x7F)),
    Event(EventType.PING, 128, 1, 2, Flags.TEST),
    Event(EventType.ARM, 1, 300, 513, Flags.IN_COMBAT | Flags.ON_TAXI),
    Event(EventType.NONE, 42, 4242, 31337, Flags.IN_INSTANCE | Flags.DEAD),
    Event(EventType.FIRE, 200, 2, 65000, Flags.TEST),
]


def _span(origin: float, i: int, pitch: float) -> tuple[int, int]:
    """Pixels whose centres fall in [origin + i*pitch, origin + (i+1)*pitch) (GPU rasterization rule)."""
    return (int(math.ceil(origin + i * pitch - 0.5)), int(math.ceil(origin + (i + 1) * pitch - 0.5)))


class Canvas:
    """A BGRA image under construction."""

    def __init__(self, width: int, height: int, *, background: str = "game", seed: int = 0) -> None:
        self.w, self.h = width, height
        self.rng = random.Random(seed)
        self.px = bytearray(width * height * 4)
        if background == "game":
            self._game_background()
        elif background == "black":
            pass
        elif background == "white":
            self.px[:] = b"\xff" * len(self.px)
        else:
            raise ValueError(background)

    def _game_background(self) -> None:
        """Busy, game-like background: random-length runs of random colours, including
        stray pure magenta / green runs so the header finder is genuinely exercised."""
        rng = self.rng
        palette_extra = [(255, 0, 255), (0, 255, 0), (240, 10, 250), (5, 250, 12)]
        for y in range(self.h):
            x = 0
            while x < self.w:
                run = rng.randint(1, 7)
                if rng.random() < 0.15:
                    r, g, b = rng.choice(palette_extra)
                else:
                    r, g, b = rng.randrange(256), rng.randrange(256), rng.randrange(256)
                for xx in range(x, min(self.w, x + run)):
                    o = (y * self.w + xx) * 4
                    self.px[o:o + 4] = bytes((b, g, r, 255))
                x += run

    def put(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            o = (y * self.w + x) * 4
            self.px[o:o + 4] = bytes((rgb[2], rgb[1], rgb[0], 255))  # BGRA

    def get(self, x: int, y: int) -> tuple[int, int, int]:
        o = (y * self.w + x) * 4
        return self.px[o + 2], self.px[o + 1], self.px[o]

    def fill(self, x0: int, y0: int, x1: int, y1: int, rgb: tuple[int, int, int]) -> None:
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                self.put(x, y, rgb)

    def strip(self, cells: list[tuple[int, int, int]], *, pitch: float, ox: float = 0.0, oy: float = 0.0,
              noise: int = 0, tone: tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None = None) -> None:
        rng = self.rng
        ya, yb = _span(oy, 0, pitch)
        for i, rgb in enumerate(cells):
            xa, xb = _span(ox, i, pitch)
            for y in range(ya, yb):
                for x in range(xa, xb):
                    c = list(rgb)
                    if tone is not None:
                        c = [lo + v * (hi - lo) // 255 for v, (lo, hi) in zip(c, tone)]
                    if noise:
                        c = [max(0, v - rng.randint(0, noise)) if v >= 128 else min(255, v + rng.randint(0, noise))
                             for v in c]
                    self.put(x, y, tuple(c))

    def frame(self) -> tuple[bytes, int, int, int]:
        return bytes(self.px), self.w, self.h, self.w * 4


def make_frame(ev: Event, *, pitch: float = 4.0, ox: float = 0.0, oy: float = 0.0, width: int | None = None,
               height: int = 32, noise: int = 0, tone=None, seed: int = 1, background: str = "game"):
    if width is None:
        width = max(64, int(math.ceil(ox + protocol.TOTAL_CELLS * pitch)) + 9)
    height = max(height, int(math.ceil(oy + pitch)) + 2)
    cv = Canvas(width, height, background=background, seed=seed)
    cv.strip(protocol.encode_beacon_cells(ev), pitch=pitch, ox=ox, oy=oy, noise=noise, tone=tone)
    return cv


def nn_resample(cv: Canvas, s: float, *, centre: bool = True) -> Canvas:
    """Nearest-neighbour resample by factor ``s`` (pixel-centre or left-edge convention)."""
    w2, h2 = int(round(cv.w * s)), int(round(cv.h * s))
    out = Canvas(w2, h2, background="black")

    def src(j: int, n: int) -> int:
        v = int(math.floor((j + 0.5) / s)) if centre else int(math.floor(j / s))
        return min(n - 1, v)

    xmap = [src(j, cv.w) for j in range(w2)]
    for y in range(h2):
        sy = src(y, cv.h)
        row = cv.px[sy * cv.w * 4:(sy + 1) * cv.w * 4]
        out.px[y * w2 * 4:(y + 1) * w2 * 4] = b"".join(bytes(row[x * 4:x * 4 + 4]) for x in xmap)
    return out


def base(ev: Event) -> Event:
    """What the beacon can carry (no id string)."""
    return Event(ev.type, ev.seq, ev.n, ev.hash, ev.flags)


def decode(cv: Canvas, **kw):
    rep = scan(*cv.frame(), **kw)
    assert not rep.reason.startswith("decoder error"), rep.reason  # internal bugs must not hide as None
    assert find_and_decode(*cv.frame(), **kw) == rep.result
    return rep.result


# --------------------------------------------------------- decoder: exact --

@pytest.mark.parametrize("cell", range(1, 9))
@pytest.mark.parametrize("ev", EVENTS, ids=lambda e: f"{e.type.name}{e.seq}")
def test_exact_cells_at_origin(ev, cell):
    cv = make_frame(ev, pitch=cell, width=max(256, 31 * cell + 4), seed=cell)
    res = decode(cv)
    assert res is not None
    assert res.event == base(ev)
    assert res.cell_px == pytest.approx(cell, abs=0.05)
    assert (res.x, res.y) == (0, 0)


@pytest.mark.parametrize("cell", range(1, 9))
@pytest.mark.parametrize("off", [(1, 0), (0, 1), (3, 2), (7, 5), (12, 9), (31, 20), (48, 3)])
def test_offsets(cell, off):
    ev = EVENTS[cell % len(EVENTS)]
    cv = make_frame(ev, pitch=cell, ox=off[0], oy=off[1], height=40, seed=100 + cell)
    res = decode(cv)
    assert res is not None and res.event == base(ev)
    assert (res.x, res.y) == off


@pytest.mark.parametrize("cell", range(1, 9))
@pytest.mark.parametrize("scale", [1.1, 1.125, 1.25, 1.333, 1.5, 1.75])
@pytest.mark.parametrize("evi", [0, 2, 4])
def test_fractional_pitch(cell, scale, evi):
    """Non-integer cell sizes (display scaling): cells rendered as alternating run lengths."""
    ev = EVENTS[evi]
    pitch = cell * scale
    cv = make_frame(ev, pitch=pitch, ox=(evi * 0.7) % 3, oy=evi % 3, seed=int(pitch * 100) + evi)
    res = decode(cv)
    assert res is not None, f"pitch {pitch}"
    assert res.event == base(ev)
    assert res.cell_px == pytest.approx(pitch, abs=0.1)


@pytest.mark.parametrize("cell", [1, 2, 3, 4, 5, 6, 8])
@pytest.mark.parametrize("scale", [1.125, 1.25, 1.5])
@pytest.mark.parametrize("centre", [True, False], ids=["centre", "edge"])
def test_nearest_neighbour_resample(cell, scale, centre):
    """Whole-frame NN resample of an integer-cell strip (offset included)."""
    ev = EVENTS[cell % len(EVENTS)]
    src = make_frame(ev, pitch=cell, ox=2, oy=1, seed=cell * 7)
    cv = nn_resample(src, scale, centre=centre)
    res = decode(cv)
    assert res is not None
    assert res.event == base(ev)


@pytest.mark.parametrize("cell", [1, 2, 4, 4.5, 6, 8])
@pytest.mark.parametrize("ev", EVENTS[:5], ids=lambda e: e.type.name)
def test_colour_noise(ev, cell):
    """255 -> 230..255 and 0 -> 0..25 per channel per pixel."""
    cv = make_frame(ev, pitch=cell, ox=1, oy=1, noise=25, seed=int(cell * 10))
    res = decode(cv)
    assert res is not None and res.event == base(ev)


@pytest.mark.parametrize("tone", [
    ((30, 225), (25, 230), (40, 210)),   # HDR-ish compressed range, tinted
    ((60, 200), (60, 200), (60, 200)),   # washed out
    ((0, 160), (0, 170), (0, 150)),      # dim
    ((90, 255), (100, 255), (80, 255)),  # lifted blacks
])
@pytest.mark.parametrize("cell", [2, 4, 5.5])
def test_tone_shift(tone, cell):
    ev = EVENTS[0]
    cv = make_frame(ev, pitch=cell, ox=2, oy=0, noise=8, tone=tone, seed=3)
    res = decode(cv)
    assert res is not None and res.event == base(ev)


def test_threshold_parameter():
    """A strip whose 'on' level is below 128 decodes only with a lower threshold."""
    ev = EVENTS[3]
    cv = make_frame(ev, pitch=4, tone=((0, 110), (0, 110), (0, 110)), seed=5)
    assert decode(cv) is None
    res = decode(cv, threshold=60)
    assert res is not None and res.event == base(ev)


def test_blended_edges():
    """Bilinear-ish blending: one intermediate pixel at every cell boundary."""
    ev = EVENTS[0]
    pitch = 6
    cells = protocol.encode_beacon_cells(ev)
    cv = Canvas(260, 24, background="game", seed=11)
    cv.strip(cells, pitch=pitch, ox=3, oy=2)
    for i in range(1, len(cells)):
        x = 3 + i * pitch
        a, b = cells[i - 1], cells[i]
        if a != b:
            mix = tuple((u + v) // 2 for u, v in zip(a, b))
            for y in range(2, 2 + pitch):
                cv.put(x, y, mix)
    res = decode(cv)
    assert res is not None and res.event == base(ev)


def test_stride_padding_and_input_types():
    ev = EVENTS[1]
    cv = make_frame(ev, pitch=3, ox=4, oy=2, width=150, seed=9)
    buf, w, h, stride = cv.frame()
    pad = 20
    padded = b"".join(buf[y * stride:(y + 1) * stride] + b"\x07" * pad for y in range(h))
    res = find_and_decode(padded, w, h, stride + pad)
    assert res is not None and res.event == base(ev) and (res.x, res.y) == (4, 2)
    assert find_and_decode(bytearray(buf), w, h, stride).event == base(ev)
    assert find_and_decode(memoryview(buf), w, h, stride).event == base(ev)


def test_max_offset_respected():
    ev = EVENTS[0]
    cv = make_frame(ev, pitch=2, ox=40, oy=0, width=160, seed=4)
    assert decode(cv, max_offset=48) is not None
    assert decode(cv, max_offset=30) is None


def test_scan_reports_truncated_strip():
    ev = EVENTS[0]
    cv = make_frame(ev, pitch=8, ox=10, width=200, seed=2)  # strip needs 10 + 248 px
    rep = scan(*cv.frame())
    assert rep.result is None
    assert rep.header is not None and rep.header.cell_px == pytest.approx(8, abs=0.3)
    assert rep.reason == "strip truncated"
    assert rep.needed_width is not None and rep.needed_width >= 258


# ------------------------------------------------------ decoder: garbage --

def test_garbage_and_degenerate_inputs():
    rng = random.Random(1234)
    for seed in range(30):
        cv = Canvas(256, 32, background="game", seed=seed)
        assert decode(cv) is None
    assert find_and_decode(rng.randbytes(256 * 32 * 4), 256, 32, 1024) is None
    assert find_and_decode(b"\x00" * 4096, 32, 32, 128) is None
    assert find_and_decode(b"\xff" * 4096, 32, 32, 128) is None
    assert find_and_decode(b"", 0, 0, 0) is None
    assert find_and_decode(b"", 256, 32, 1024) is None
    assert find_and_decode(b"\x00" * 10, 256, 32, 1024) is None  # shorter than declared
    assert find_and_decode(b"\x00" * 4096, 32, 32, 64) is None  # stride < width*4
    assert find_and_decode(b"\x00" * 4, 1, 1, 4) is None
    assert find_and_decode(b"\x00" * 4096, -5, 32, 128) is None
    full = make_frame(EVENTS[0], pitch=4).frame()
    assert find_and_decode(full[0], full[1], full[2], full[3], threshold=0) is None  # invalid threshold


def test_checkerboard_and_header_only():
    # 1-px magenta/green checkerboard: endless header candidates, none valid.
    cv = Canvas(256, 32, background="black")
    for y in range(32):
        for x in range(256):
            cv.put(x, y, (255, 0, 255) if (x + y) % 2 == 0 else (0, 255, 0))
    t = time.perf_counter()
    assert decode(cv) is None
    assert time.perf_counter() - t < 0.25
    # header with nothing valid after it
    cv = Canvas(256, 32, background="game", seed=8)
    cv.strip(list(protocol.HEADER), pitch=4)
    rep = scan(*cv.frame())
    assert rep.result is None and rep.header is not None


@pytest.mark.parametrize("cells", [(10, 20), (0, 3), (26, 27), (5, 6)])
def test_partially_occluded(cells):
    """A window/tooltip covering part of the payload must yield None (checksum), not garbage."""
    ev = EVENTS[0]
    cv = make_frame(ev, pitch=4, seed=21)
    a, b = cells
    cv.fill(4 * (4 + a), 0, 4 * (4 + b), 32, (30, 30, 34))
    res = decode(cv)
    # Occluding cells whose true colour happens to equal the cover colour class can
    # leave the frame valid; anything returned must then be the true event.
    assert res is None or res.event == base(ev)


def test_corrupted_bits_rejected():
    ev = EVENTS[0]
    cells = protocol.encode_beacon_cells(ev)
    for i in range(4, 4 + 26):  # last cell carries pad bits only partially
        bad = list(cells)
        r, g, b = bad[i]
        bad[i] = (255 - r, g, b)
        cv = Canvas(160, 16, background="game", seed=i)
        cv.strip(bad, pitch=4)
        res = decode(cv)
        assert res is None or res.event != base(ev)
        assert res is None


def test_two_strips_returns_top_one():
    a, b = EVENTS[0], EVENTS[1]
    cv = Canvas(256, 40, background="game", seed=31)
    cv.strip(protocol.encode_beacon_cells(a), pitch=4, ox=0, oy=0)
    cv.strip(protocol.encode_beacon_cells(b), pitch=4, ox=0, oy=20)
    assert decode(cv).event == base(a)


def test_header_row_damaged_but_centre_row_ok():
    """Top rows of the strip unreadable (e.g. blended); a lower row still decodes."""
    ev = EVENTS[2]
    cv = make_frame(ev, pitch=6, ox=0, oy=3, seed=17)
    for x in range(0, 6 * 31):
        cv.put(x, 3, (128, 128, 128))
    res = decode(cv)
    assert res is not None and res.event == base(ev)


# ---------------------------------------------------------- decoder: speed --

def _best_of(fn, n=15):
    best = float("inf")
    for _ in range(n):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


def test_decode_speed_valid_frame():
    cv = make_frame(EVENTS[0], pitch=4, ox=2, oy=1, width=256, height=32, noise=20, seed=77)
    buf, w, h, s = cv.frame()
    assert (w, h) == (256, 32)
    assert find_and_decode(buf, w, h, s) is not None
    assert _best_of(lambda: find_and_decode(buf, w, h, s)) < 0.020


def test_decode_speed_garbage_frame():
    """No strip at all is the worst case: every row is scanned."""
    cv = Canvas(256, 32, background="game", seed=78)
    buf, w, h, s = cv.frame()
    assert find_and_decode(buf, w, h, s) is None
    assert _best_of(lambda: find_and_decode(buf, w, h, s)) < 0.020


def test_decode_speed_fractional_noisy():
    cv = make_frame(EVENTS[2], pitch=4.5, ox=3, oy=2, width=256, height=32, noise=25, seed=79)
    buf, w, h, s = cv.frame()
    assert find_and_decode(buf, w, h, s) is not None
    assert _best_of(lambda: find_and_decode(buf, w, h, s)) < 0.020


def test_decode_speed_small_fractional_cells():
    """1.1 px cells need the exact (slowest) search path; still well inside the budget."""
    cv = make_frame(EVENTS[4], pitch=1.1, ox=2.8, oy=1, width=256, height=32, seed=114)
    buf, w, h, s = cv.frame()
    assert find_and_decode(buf, w, h, s) is not None
    assert _best_of(lambda: find_and_decode(buf, w, h, s), n=5) < 0.020


def test_randomised_robustness_sweep():
    """Random events / pitches 1-9 px / fractional offsets / noise: exact decode, never a wrong event."""
    rng = random.Random(2026)
    misses = []
    for t in range(150):
        ev = Event(EventType(rng.randrange(6)), rng.randrange(256), rng.randrange(65536), rng.randrange(1, 65536),
                   Flags(rng.randrange(128)))
        pitch = rng.choice([rng.uniform(1.0, 9.0), float(rng.randint(1, 8))])
        ox, oy = rng.uniform(0, 12), rng.uniform(0, 6)
        width = max(256, int(math.ceil(ox + 31 * pitch)) + 4)
        cv = make_frame(ev, pitch=pitch, ox=ox, oy=oy, width=width, noise=rng.choice([0, 10, 25]), seed=t)
        res = decode(cv)
        if res is None:
            misses.append((round(pitch, 3), round(ox, 2), round(oy, 2)))
        else:
            assert res.event == base(ev)
    assert not misses, misses


# ------------------------------------------------------ PixelTransport ----

class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeScreen:
    """Fake OS: one game window whose client area shows the current beacon event.

    Captures render on demand at the requested size, like the real screen. Set
    ``screen_event`` / ``window_event`` to None for a frame without a strip.
    """

    def __init__(self, ev: Event | None = None, *, pitch: float = 4.0, ox: float = 0.0, oy: float = 0.0) -> None:
        self.hwnd = 0x5150
        self.window = True
        self.minimized = False
        self.rect = (100, 40, 1920, 1080)
        self.screen_event = ev
        self.window_event = ev
        self.pitch, self.ox, self.oy = pitch, ox, oy
        self.calls: list[tuple] = []
        self.finds = 0
        self.screen_error: Exception | None = None
        self.window_error: Exception | None = None
        self._cache: dict = {}

    def _render(self, ev, w, h):
        key = (ev, w, h, self.pitch, self.ox, self.oy)
        if key not in self._cache:
            cv = Canvas(w, h, background="game", seed=w * 31 + h)
            if ev is not None:
                cv.strip(protocol.encode_beacon_cells(ev), pitch=self.pitch, ox=self.ox, oy=self.oy)
            self._cache[key] = cv.frame()
        return self._cache[key]

    # CaptureBackend
    def available(self):
        return True, ""

    def prepare(self):
        return "fake-dpi"

    def find_window(self):
        self.finds += 1
        return self.hwnd if self.window else None

    def window_alive(self, hwnd):
        return self.window and hwnd == self.hwnd

    def is_minimized(self, hwnd):
        return self.minimized

    def client_rect(self, hwnd):
        return self.rect

    def capture_screen(self, x, y, w, h):
        self.calls.append(("screen", x, y, w, h))
        if self.screen_error:
            raise self.screen_error
        return self._render(self.screen_event, w, h)

    def capture_window(self, hwnd, w, h):
        self.calls.append(("window", hwnd, w, h))
        if self.window_error:
            raise self.window_error
        return self._render(self.window_event, w, h)

    def describe(self, hwnd):
        return "'World of Warcraft' pid 4242 (fake)"


class Harness:
    def __init__(self, screen: FakeScreen | None = None, **cfg) -> None:
        self.screen = screen or FakeScreen()
        self.clock = FakeClock()
        self.events: list[tuple[Event, str]] = []
        self.diag: list[str] = []
        config = {"enabled": True, "method": "auto", "hz": 4, "regionW": 256, "regionH": 32, **cfg}
        self.t = PixelTransport(config, SimpleNamespace(game_exe=None), lambda e, n: self.events.append((e, n)),
                                self.diag.append, backend=self.screen, clock=self.clock)

    def show(self, ev: Event | None) -> None:
        self.screen.screen_event = ev
        self.screen.window_event = ev

    def step(self, n: int = 1, dt: float = 0.25) -> list[str]:
        out = []
        for _ in range(n):
            out.append(self.t.step())
            self.clock.advance(dt)
        return out

    def emitted(self) -> list[Event]:
        return [e for e, _n in self.events]


EV = [Event(EventType.HELLO, i, 0, 40213, Flags.NONE) for i in range(10)]
FIRE7 = Event(EventType.FIRE, 7, 12, 40213, Flags.DROP_ACTIVE)


def test_feed_baseline_and_two_consecutive_rule():
    h = Harness()
    t = h.t
    a, b, c = EV[0], EV[1], EV[2]
    assert t.feed(a) == ("baseline", None)
    assert t.feed(a) == ("same", None)
    assert t.feed(b) == ("pending", None)
    assert h.emitted() == []
    assert t.feed(b) == ("emitted", b)
    assert h.events == [(b, "pixel")]
    assert t.feed(b) == ("same", None)
    # an unreadable frame breaks the streak
    assert t.feed(c)[0] == "pending"
    assert t.feed(None)[0] == "invalid"
    assert t.feed(c)[0] == "pending"
    assert t.feed(c)[0] == "emitted"
    assert h.emitted() == [b, c]


def test_feed_alternating_frames_never_emit():
    h = Harness()
    h.t.feed(EV[0])
    for i in range(10):
        h.t.feed(EV[1 + i % 2])
    assert h.emitted() == []


def test_feed_flags_only_change_is_a_change():
    h = Harness()
    a = Event(EventType.FIRE, 3, 5, 40213, Flags.DROP_ACTIVE)
    a2 = Event(EventType.FIRE, 3, 5, 40213, Flags.DROP_ACTIVE | Flags.IN_COMBAT)
    h.t.feed(a)
    h.t.feed(a2)
    h.t.feed(a2)
    assert h.emitted() == [a2]


def test_feed_survives_callback_errors():
    def boom(ev, name):
        raise RuntimeError("engine exploded")

    t = PixelTransport({}, SimpleNamespace(), boom, backend=FakeScreen())
    t.feed(EV[0])
    t.feed(EV[1])
    assert t.feed(EV[1])[0] == "emitted"
    assert t.status()["eventsEmitted"] == 1


def test_step_end_to_end_with_fake_screen():
    h = Harness(FakeScreen(EV[0], pitch=4, ox=0, oy=0))
    assert h.step(2) == ["baseline", "same"]
    assert h.emitted() == []
    # capture is the client area's top-left corner, region-sized
    assert h.screen.calls[0] == ("screen", 100, 40, 256, 32)
    h.show(FIRE7)
    assert h.step(2) == ["pending", "emitted"]
    assert h.events == [(FIRE7, "pixel")]
    st = h.t.status()
    assert st["window"] and not st["minimized"] and st["method"] == "screen"
    assert st["cellPx"] == pytest.approx(4.0) and st["stripAt"] == [0, 0]
    assert st["lastEvent"].startswith("FIRE seq=7")
    assert st["frames"] == 4 and st["validFrames"] == 4 and st["decodeFailures"] == 0
    assert st["ok"]
    assert any("found WoW window" in d for d in h.diag)
    assert any("baseline" in d for d in h.diag)


def test_stale_fire_at_startup_is_not_replayed():
    h = Harness(FakeScreen(FIRE7))
    h.step(5)
    assert h.emitted() == []
    assert h.t.status()["baseline"]


def test_window_lost_rebaselines_and_refinds_every_5s():
    h = Harness(FakeScreen(EV[0]))
    h.step(2)
    h.show(EV[1])
    h.step(2)
    assert h.emitted() == [EV[1]]
    h.screen.window = False
    finds = h.screen.finds
    assert h.step() == ["no-window"]
    assert h.screen.finds == finds + 1  # immediate re-find attempt on loss
    assert not h.t.status()["window"]
    h.screen.window = True
    h.show(EV[2])
    assert h.step(4, dt=1.0) == ["no-window"] * 4  # next search only 5 s later
    assert h.screen.finds == finds + 1
    h.clock.advance(1.0)
    assert h.step() == ["baseline"]  # found again: first frame is a new baseline
    assert h.step() == ["same"]
    assert h.emitted() == [EV[1]]
    h.show(EV[3])
    h.step(2)
    assert h.emitted() == [EV[1], EV[3]]
    assert any("window lost" in d for d in h.diag)


def test_no_window_at_start_polls_every_5s():
    s = FakeScreen(EV[0])
    s.window = False
    h = Harness(s)
    assert h.step(20) == ["no-window"] * 20  # 5 s of frames at 0.25 s
    assert s.finds == 1
    h.step()
    assert s.finds == 2
    assert "waiting for the World of Warcraft window" in h.t.status()["detail"]


def test_minimized_pauses_capture_but_keeps_baseline():
    h = Harness(FakeScreen(EV[0]))
    h.step(2)
    h.screen.minimized = True
    n = len(h.screen.calls)
    assert h.step(3) == ["minimized"] * 3
    assert len(h.screen.calls) == n
    st = h.t.status()
    assert st["minimized"] and not st["ok"] and "minimized" in st["detail"]
    h.show(FIRE7)  # happened while minimized
    h.screen.minimized = False
    assert h.step(2) == ["pending", "emitted"]
    assert h.emitted() == [FIRE7]
    assert any("minimized" in d for d in h.diag) and any("restored" in d for d in h.diag)


def test_zero_client_area_treated_as_minimized():
    h = Harness(FakeScreen(EV[0]))
    h.screen.rect = (0, 0, 0, 0)
    assert h.step() == ["minimized"]


def test_auto_switches_to_printwindow_when_screen_shows_nothing():
    s = FakeScreen(EV[0])
    s.screen_event = None  # e.g. another window covers the corner on screen
    h = Harness(s)
    out = h.step(PixelTransport.SWITCH_AFTER)
    assert out == ["invalid"] * PixelTransport.SWITCH_AFTER
    assert h.t.status()["method"] == "printwindow"
    assert any("switching capture to printwindow" in d for d in h.diag)
    assert h.step(2) == ["baseline", "same"]
    assert s.calls[-1][0] == "window"
    s.window_event = FIRE7
    h.step(2)
    assert h.emitted() == [FIRE7]


def test_auto_flips_back_when_printwindow_fails():
    s = FakeScreen(EV[0])
    s.screen_event = None
    s.window_error = RuntimeError("PrintWindow failed (Win32 error 0)")
    h = Harness(s)
    h.step(PixelTransport.SWITCH_AFTER)
    assert h.t.status()["method"] == "printwindow"
    assert h.step() == ["capture-error"]
    assert h.t.status()["method"] == "screen"
    assert h.t.status()["captureErrors"] == 1
    assert "PrintWindow failed" in h.t.status()["lastError"]


def test_fixed_screen_method_never_switches():
    s = FakeScreen(EV[0])
    s.screen_event = None
    h = Harness(s, method="screen")
    h.step(PixelTransport.SWITCH_AFTER * 2)
    assert h.t.status()["method"] == "screen"
    assert all(c[0] == "screen" for c in s.calls)


def test_printwindow_method_config():
    h = Harness(FakeScreen(EV[0]), method="printwindow")
    h.step()
    assert h.screen.calls[0] == ("window", 0x5150, 256, 32)


def test_unknown_method_falls_back_to_auto():
    h = Harness(FakeScreen(EV[0]), method="dxgi")
    assert h.t.method_cfg == "auto"
    h.t.start()  # warning is reported via diag; the thread runs on the fake backend
    h.t.stop()
    assert any("unknown transport.pixel.method" in d for d in h.diag)


def test_capture_errors_are_reported_not_raised():
    s = FakeScreen(EV[0])
    s.screen_error = RuntimeError("BitBlt failed (Win32 error 6)")
    h = Harness(s, method="screen")
    assert h.step(3) == ["capture-error"] * 3
    st = h.t.status()
    assert st["captureErrors"] == 3 and "BitBlt failed" in st["lastError"]


def test_region_clipped_to_small_client_area():
    s = FakeScreen(EV[0], pitch=2)
    s.rect = (0, 0, 200, 20)
    h = Harness(s)
    h.step()
    assert s.calls[0] == ("screen", 0, 0, 200, 20)
    assert h.t.status()["validFrames"] == 1


def test_wide_strip_widens_capture_region():
    """cellPx 10 -> 310 px strip: wider than the 256 px default region."""
    s = FakeScreen(EV[0], pitch=10, ox=0, oy=0)
    h = Harness(s)
    assert h.step() == ["invalid"]
    assert any("widening capture" in d for d in h.diag)
    assert h.step() == ["baseline"]
    assert s.calls[-1][3] >= 312
    assert h.t.status()["region"][0] >= 312


def test_offset_strip_from_window_chrome():
    h = Harness(FakeScreen(EV[0], pitch=4, ox=3, oy=2))
    h.step()
    assert h.t.status()["stripAt"] == [3, 2]


def test_status_is_json_serialisable_and_health_line():
    import json

    h = Harness(FakeScreen(EV[0]))
    h.step(3)
    json.dumps(h.t.status())
    line = h.t.health_line()
    assert line.startswith("pixel: window=yes") and "fps=" in line and "valid=3" in line


def test_fps_measurement():
    h = Harness(FakeScreen(EV[0]))
    h.step(9, dt=0.25)
    assert h.t.status()["fps"] == pytest.approx(4.0)


def test_disabled_and_unavailable_do_not_start():
    diag: list[str] = []
    t = PixelTransport({"enabled": False}, SimpleNamespace(), lambda e, n: None, diag.append, backend=FakeScreen())
    t.start()
    assert not t.status()["running"] and t.status()["detail"] == "disabled in config"

    class NoGui(FakeScreen):
        def available(self):
            return False, "the pixel beacon needs Windows"

    t2 = PixelTransport({}, SimpleNamespace(), lambda e, n: None, diag.append, backend=NoGui())
    t2.start()
    st = t2.status()
    assert not st["running"] and "needs Windows" in st["detail"]


def test_thread_runs_and_stops():
    s = FakeScreen(EV[0])
    got = threading.Event()
    events = []
    diag: list[str] = []

    def on_event(ev, name):
        events.append((ev, name))
        got.set()

    t = PixelTransport({"hz": 50}, SimpleNamespace(game_exe=None), on_event, diag.append, backend=s)
    t.HEALTH_INTERVAL = 0.05
    t.start()
    try:
        assert t.status()["running"]
        deadline = time.monotonic() + 3
        while t.status()["validFrames"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        s.screen_event = FIRE7
        assert got.wait(3), "event not delivered by the capture thread"
        assert events[0] == (FIRE7, "pixel")
    finally:
        t.stop()
    assert not t.status()["running"]
    assert any(d.startswith("pixel: window=yes") for d in diag), "periodic health summary"
    assert any("DPI awareness fake-dpi" in d for d in diag)


def test_default_backend_uses_wincapture():
    t = PixelTransport({}, SimpleNamespace(game_exe=Path("C:/nope/_forever_/Wow.exe")), lambda e, n: None)
    assert isinstance(t.backend, pixel.Win32CaptureBackend)
    ok, _why = t.backend.available()
    assert ok == (sys.platform == "win32")


def test_restart_rebaselines():
    """start() makes the next valid frame a baseline, even if an older baseline exists."""
    s = FakeScreen(FIRE7)
    events = []
    t = PixelTransport({"hz": 50}, SimpleNamespace(), lambda e, n: events.append(e), backend=s)
    t.feed(EV[0])  # baseline from an earlier run
    t.start()
    try:
        deadline = time.monotonic() + 3
        while t.status()["validFrames"] < 6 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        t.stop()
    assert t.status()["validFrames"] >= 6
    assert events == [], "a FIRE already on screen at (re)start must not be replayed"
