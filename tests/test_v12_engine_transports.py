"""Transports on v2 frames: the pixel transport (beacon2 decoder first, v1 fallback, the 2-identical-reads
and baseline rules, region sizing, corners) on synthetic BGRA captures rendered from
``protocol2.encode_cells``, and the chat-log transport on ``NDROP2 <base64url>`` lines (strict matching),
each feeding a real :class:`~needledropper.engine.Engine`.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from needledropper import protocol, protocol2 as p2
from needledropper.engine import Engine
from needledropper.models import Drop, Event, EventType as ET, Flags
from needledropper.providers.base import NowPlaying, PlayHandle, Provider
from needledropper.transport.chatlog import ChatLogTransport, is_strict_record, parse_line
from needledropper.transport.pixel import PixelTransport

SP_ID = "4uLU6hMCjMI75M1A2tKUQC"
YT_ID = "QAo_Ycocl1E"


# ------------------------------------------------------------- rendering --

def background(w: int, h: int, seed: int = 0) -> bytearray:
    """A dark, textured game-like BGRA background (every channel below the 128 threshold)."""
    rng = random.Random(seed)
    buf = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            buf[o:o + 4] = bytes((20 + (x * 7 + rng.randrange(9)) % 70, 30 + (y * 11) % 60,
                                  25 + ((x + y) * 5) % 80, 255))
    return buf


def paint(buf: bytearray, w: int, h: int, rows: list[list[tuple[int, int, int]]], cell: int, ox: int = 0,
          oy: int = 0) -> None:
    for r, row in enumerate(rows):
        for c, (R, G, B) in enumerate(row):
            for yy in range(oy + r * cell, oy + (r + 1) * cell):
                if not 0 <= yy < h:
                    continue
                for xx in range(ox + c * cell, ox + (c + 1) * cell):
                    if 0 <= xx < w:
                        o = (yy * w + xx) * 4
                        buf[o:o + 4] = bytes((B, G, R, 255))


def v2_rows(frame: p2.Frame, cols: int = 64, cell: int = 3) -> list[list[tuple[int, int, int]]]:
    return p2.encode_cells(frame, p2.Geometry(cols, cell)).as_rows()


class Scene:
    """What the fake screen shows: a v2 frame, a v1 event or nothing, at a corner."""

    def __init__(self, what: Any = None, *, cols: int = 64, cell: int = 3, corner: str = "TOPLEFT") -> None:
        self.what, self.cols, self.cell, self.corner = what, cols, cell, corner

    def rows(self) -> tuple[list[list[tuple[int, int, int]]], int]:
        if isinstance(self.what, p2.Frame):
            return v2_rows(self.what, self.cols, self.cell), self.cell
        if isinstance(self.what, Event):
            return [protocol.encode_beacon_cells(self.what)], 4
        return [], self.cell


class FakeBackend:
    """A WoW window whose client area is ``rect``; renders ``scene`` at its corner."""

    def __init__(self, scene: Optional[Scene] = None, rect: tuple[int, int, int, int] = (100, 40, 1920, 1080)) -> None:
        self.scene = scene or Scene()
        self.rect = rect
        self.calls: list[tuple] = []
        self._cache: dict = {}

    def available(self):
        return True, ""

    def prepare(self):
        return "fake-dpi"

    def find_window(self):
        return 0x5150

    def window_alive(self, hwnd):
        return hwnd == 0x5150

    def is_minimized(self, hwnd):
        return False

    def client_rect(self, hwnd):
        return self.rect

    def _render(self, x: int, y: int, w: int, h: int):
        cx, cy, cw, ch = self.rect
        sc = self.scene
        key = (id(sc), sc.what, sc.cols, sc.cell, sc.corner, x, y, w, h)
        if key not in self._cache:
            buf = background(w, h, seed=w + h)
            rows, cell = sc.rows()
            if rows:
                sw, sh = len(rows[0]) * cell, len(rows) * cell
                # the strip sits at its corner of the client area; place it relative to the capture
                gx = cx if sc.corner in ("TOPLEFT", "BOTTOMLEFT") else cx + cw - sw
                gy = cy if sc.corner in ("TOPLEFT", "TOPRIGHT") else cy + ch - sh
                paint(buf, w, h, rows, cell, gx - x, gy - y)
            self._cache[key] = (bytes(buf), w, h, w * 4)
        return self._cache[key]

    def capture_screen(self, x, y, w, h):
        self.calls.append(("screen", x, y, w, h))
        return self._render(x, y, w, h)

    def capture_window(self, hwnd, w, h):
        self.calls.append(("window", hwnd, w, h))
        cx, cy, _cw, _ch = self.rect
        return self._render(cx, cy, w, h)

    def describe(self, hwnd):
        return "'World of Warcraft' (fake)"


class PixelRig:
    def __init__(self, scene: Optional[Scene] = None, **cfg: Any) -> None:
        self.backend = FakeBackend(scene)
        self.events: list[tuple[Any, str]] = []
        self.diag: list[str] = []
        config = {"enabled": True, "method": "screen", "hz": 4, **cfg}
        self.t = PixelTransport(config, SimpleNamespace(game_exe=None),
                                lambda e, n: self.events.append((e, n)), self.diag.append, backend=self.backend)

    def show(self, what: Any, **kw: Any) -> None:
        self.backend.scene = Scene(what, **kw)

    def step(self, n: int = 1) -> list[str]:
        return [self.t.step() for _ in range(n)]

    def emitted(self) -> list[Any]:
        return [e for e, _n in self.events]


def fire(seq: int = 9, **kw: Any) -> p2.Frame:
    fields = {"flags": Flags.DROP_ACTIVE, "drop_id": "area:87", "youtube": YT_ID, "artist": "Toto",
              "title": "Africa", "label": "Goldshire", "length_ms": 240000}
    fields.update(kw)
    return p2.build_frame(ET.FIRE, seq=seq, **fields)


HELLO = p2.build_frame(ET.HELLO, seq=1)


# ------------------------------------------------------------ pixel: v2 --

@pytest.mark.parametrize("cell", [2, 3, 4])
def test_v2_strip_baseline_then_two_identical_reads(cell):
    rig = PixelRig(Scene(HELLO, cell=cell))
    assert rig.step(2) == ["baseline", "same"]
    fr = fire()
    rig.show(fr, cell=cell, cols=112)
    assert rig.step(2) == ["pending", "emitted"]
    (ev, name), = rig.events
    assert name == "pixel" and isinstance(ev, p2.Frame)
    assert ev == fr and ev.raw == p2.encode_frame(fr)
    st = rig.t.status()
    assert st["protocol"] == 2 and st["cellPx"] == pytest.approx(cell) and st["cols"] == 112
    assert st["lastEvent"].startswith("FIRE seq=9") and st["ok"]
    json.dumps(st)


def test_v2_frames_that_differ_in_one_byte_are_new_frames():
    """The full ARM and the later placeKey-only ARM share seq/type/place: still two frames."""
    full = p2.build_frame(ET.ARM, seq=4, drop_id="area:87", youtube=YT_ID, artist="Toto", title="Africa")
    short = p2.build_frame(ET.ARM, seq=4, place_key=p2.place_key_for("area:87"))
    rig = PixelRig(Scene(HELLO))
    rig.step()
    rig.show(full)
    rig.step(2)
    rig.show(short)
    assert rig.step(2) == ["pending", "emitted"]
    assert rig.emitted() == [full, short]


def test_alternating_v2_frames_never_emit():
    rig = PixelRig(Scene(HELLO))
    rig.step()
    a, b = fire(seq=2), fire(seq=3)
    for i in range(6):
        rig.show(a if i % 2 else b)
        rig.step()
    assert rig.emitted() == []


def test_stale_v2_fire_at_startup_is_the_baseline():
    rig = PixelRig(Scene(fire()))
    rig.step(5)
    assert rig.emitted() == [] and rig.t.status()["baseline"]


def test_v1_strip_still_decodes_as_a_fallback():
    v1 = Event(ET.FIRE, 7, 12, 40213, Flags.DROP_ACTIVE)
    rig = PixelRig(Scene(Event(ET.HELLO, 1, 0, 40213)))
    assert rig.step() == ["baseline"]
    rig.show(v1)
    assert rig.step(2) == ["pending", "emitted"]
    assert rig.emitted() == [v1]
    st = rig.t.status()
    assert st["protocol"] == 1 and "update NeedleDropper" in st["detail"]
    assert any("older than 1.2" in d for d in rig.diag)


def test_switching_from_v1_to_v2_is_a_new_frame():
    rig = PixelRig(Scene(Event(ET.HELLO, 1, 0, 40213)))
    rig.step()
    rig.show(HELLO)
    assert rig.step(2) == ["pending", "emitted"] and isinstance(rig.emitted()[0], p2.Frame)


def test_default_capture_region_is_512_by_48():
    rig = PixelRig(Scene(HELLO))
    rig.step()
    assert rig.backend.calls[0] == ("screen", 100, 40, 512, 48)
    assert rig.t.status()["region"] == [512, 48]


def test_a_small_configured_region_widens_for_a_wide_strip():
    """config.json from v1.1 says 256 x 32; a 112-column strip at 4 px is 448 px wide."""
    rig = PixelRig(Scene(HELLO), regionW=256, regionH=32)
    rig.step()
    fr = fire(seq=5)
    rig.show(fr, cols=112, cell=4)
    out = rig.step(4)
    assert "emitted" in out and rig.emitted() == [fr]
    assert rig.t.status()["region"][0] >= 449
    assert any("widening capture" in d for d in rig.diag)


class CoveredScene(Scene):
    """A v2 strip with one row painted over (an overlay notification)."""

    def rows(self):
        rows, cell = super().rows()
        rows[1] = [(0, 0, 0)] * len(rows[1])
        return rows, cell


def test_partly_covered_v2_strip_is_never_read_as_v1_or_garbage():
    rig = PixelRig(Scene(HELLO))
    rig.step()
    rig.backend.scene = CoveredScene(fire(seq=6), cols=64)
    assert rig.step(3) == ["invalid", "invalid", "invalid"]
    assert rig.emitted() == [] and rig.t.status()["decodeFailures"] == 3


def test_bottom_left_corner_is_captured_from_the_screen():
    fr = fire(seq=8)
    rig = PixelRig(Scene(HELLO, corner="BOTTOMLEFT"), corner="bottomleft", method="auto")
    rig.step()
    x, y, w, h = rig.backend.calls[0][1:]
    assert rig.backend.calls[0][0] == "screen" and (x, y + h) == (100, 40 + 1080)
    rig.show(fr, corner="BOTTOMLEFT")
    assert rig.step(2) == ["pending", "emitted"] and rig.emitted() == [fr]
    assert rig.t.status()["corner"] == "BOTTOMLEFT"


def test_unknown_corner_falls_back_to_top_left_with_a_warning():
    rig = PixelRig(Scene(HELLO), corner="middle", enabled=False)
    rig.t.start()  # disabled: reports the config problem, starts no thread
    assert rig.t.corner == "TOPLEFT"
    assert any("unknown transport.pixel.corner" in d for d in rig.diag)


def test_pixel_to_engine_end_to_end():
    yt = RecordingYouTube()
    eng = Engine([yt], None, prewarm_async=False, log=lambda m: None)
    rig = PixelRig(Scene(HELLO))
    rig.t.on_event = eng.handle
    rig.step()
    rig.show(fire(seq=11), cols=112)
    rig.step(2)
    assert [c[1] for c in yt.calls if c[0] == "play"] == [YT_ID]
    assert eng.status()["playing"]["label"] == "Goldshire" and eng.status()["transports"] == {"pixel": 1}


# --------------------------------------------------------------- chatlog --

class RecordingYouTube(Provider):
    name = "youtube"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def available(self) -> bool:
        return True

    def can_play(self, drop: Drop) -> bool:
        return bool(drop.youtube)

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop.youtube, drop.id))
        return PlayHandle(provider=self.name, drop=drop, now_playing=NowPlaying(title=drop.title, artist=drop.artist))

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop.id))


class ChatRig:
    def __init__(self, tmp_path: Path, **cfg: Any) -> None:
        self.log = tmp_path / "Logs" / "WoWChatLog.txt"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_bytes(b"11/5 17:59:00.000  old history line\r\n")
        self.events: list[tuple[Any, str]] = []
        self.diag: list[str] = []
        self.t = ChatLogTransport({"enabled": True, "pollMs": 50, **cfg}, SimpleNamespace(chat_log=self.log),
                                  lambda e, n: self.events.append((e, n)), self.diag.append)
        self.t.poll()  # starts at the end of the existing log

    def write(self, *lines: str) -> int:
        with open(self.log, "ab") as fh:
            fh.write("".join(ln + "\r\n" for ln in lines).encode("utf-8"))
        return self.t.poll()


def test_ndrop2_system_line_becomes_a_frame(tmp_path):
    rig = ChatRig(tmp_path)
    fr = fire(seq=21)
    assert rig.write(f"11/5 18:01:02.345  {p2.format_chat_line(fr)}") == 1
    (ev, name), = rig.events
    assert name == "chatlog" and ev == fr and ev.raw == p2.encode_frame(fr)
    st = rig.t.status()
    assert st["events"] == 1 and st["lastEvent"].startswith("FIRE seq=21")
    assert any("-> FIRE seq=21" in d for d in rig.diag)


def test_ndrop2_typed_by_a_player_is_rejected_in_strict_mode(tmp_path):
    rig = ChatRig(tmp_path)
    line = p2.format_chat_line(fire(seq=22))
    assert rig.write(f"11/5 18:01:03.000  [Bob] says: {line}",
                     f"11/5 18:01:03.100  [2. Trade] [Eve]: WTS {line}") == 0
    st = rig.t.status()
    assert st["rejectedLines"] == 2 and st["events"] == 0
    assert any("contains NDROP2 but is not a NeedleDropper system message" in d for d in rig.diag)


def test_anywhere_matching_accepts_ndrop2_inside_a_line(tmp_path):
    rig = ChatRig(tmp_path, match="anywhere")
    fr = fire(seq=23)
    assert rig.write(f"odd prefix {p2.format_chat_line(fr)}") == 1 and rig.events[0][0] == fr


def test_damaged_ndrop2_line_is_ignored(tmp_path):
    rig = ChatRig(tmp_path)
    line = p2.format_chat_line(fire(seq=24))
    token = line.split(" ", 1)[1]
    bad = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    assert rig.write(f"11/5 18:01:04.000  NDROP2 {bad}", f"11/5 18:01:04.100  NDROP2 ***") == 0
    assert rig.t.status()["badLines"] >= 1
    assert any("ignored malformed NDROP2 line" in d for d in rig.diag)


def test_ndrop1_lines_still_work_next_to_ndrop2(tmp_path):
    rig = ChatRig(tmp_path)
    v1 = Event(ET.FIRE, 3, 12, 40213, Flags.DROP_ACTIVE, "zone:1429")
    fr = fire(seq=25)
    n = rig.write(f"11/5 18:02:00.000  {protocol.format_chat_line(v1)}",
                  f"11/5 18:02:00.100  NDPAD {'x' * 40}",
                  f"11/5 18:02:00.200  {p2.format_chat_line(fr)}")
    assert n == 2
    assert [type(e).__name__ for e, _n in rig.events] == ["Event", "Frame"]
    assert rig.events[0][0].key == v1.key and rig.events[1][0] == fr
    assert rig.t.status()["padLines"] == 1


def test_parse_line_helpers():
    fr = fire(seq=26)
    line = p2.format_chat_line(fr)
    assert parse_line(f"11/5 18:01:02.345  {line}") == fr
    assert parse_line(f"[Bob] says: {line}") is None
    assert parse_line(f"[Bob] says: {line}", match="anywhere") == fr
    assert is_strict_record(line) and not is_strict_record(f"x {line}")
    assert parse_line("nothing here") is None


def test_chatlog_and_pixel_deliver_one_drop(tmp_path):
    yt = RecordingYouTube()
    eng = Engine([yt], None, prewarm_async=False, log=lambda m: None)
    rig = ChatRig(tmp_path)
    rig.t.on_event = eng.handle
    pix = PixelRig(Scene(HELLO))
    pix.t.on_event = eng.handle
    pix.step()
    fr = fire(seq=31)
    pix.show(fr)
    pix.step(2)
    rig.write(f"11/5 18:03:00.000  {p2.format_chat_line(fr)}")
    assert [c for c in yt.calls if c[0] == "play"] == [("play", YT_ID, "area:87")]
    assert eng.status()["duplicates"] == 1
