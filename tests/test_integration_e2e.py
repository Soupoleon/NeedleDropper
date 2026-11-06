"""End-to-end integration (v1.2): the REAL addon (every file in TOC order, a test soundtrack in place of the
bundled Sets) running in the lupa WoW shim, talking to the REAL companion pipeline -- with no map anywhere:

    Director -> Emit -> Beacon v2 textures -> synthetic BGRA client area -> transport.pixel.PixelTransport
    (beacon2 decode, two identical reads)                                         \\
    ChatLink "NDROP2 <base64url frame>" -> WoWChatLog.txt -> transport.chatlog ---+-> Engine (dedupe, v2
    routing: Spotify id -> Spotify search -> YouTube id) -> provider.play / stop + deck card
    + NeedleDropper_Companion\\Data.lua (gamedata: Spotify matches for the game)

The song travels in the frame (DESIGN-v1.2 2.1): the companion is given no soundtrack, no Map.json and
no registry. Nothing here touches the network, %APPDATA% or a real WoW install: the addon folder is only
read, everything written goes to pytest's tmp_path.

(Until v1.1 this file drove the v1 pipeline: the shipped Map.lua / Map.json built by ``build-map``, 31-cell
v1 strips, NDROP1 lines and mapgen.MapRegistry. All of those were retired with v1.2.)
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

lua51 = pytest.importorskip("lupa.lua51")

from addon_harness import AddonHarness, make_set  # noqa: E402
from needledropper import gamedata  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.engine import Engine  # noqa: E402
from needledropper.models import Drop, EventType as ET, Flags  # noqa: E402
from needledropper.providers.base import NowPlaying, PlayHandle, Provider  # noqa: E402
from needledropper.transport.chatlog import ChatLogTransport  # noqa: E402
from needledropper.transport.pixel import PixelTransport  # noqa: E402

SP_MATCH = "6EPRKhUOdiFSQwGBRBbvsZ"
YT_ID = "QAo_Ycocl1E"
DROPS = [
    # search-only: the companion searches Spotify at ARM and records the match for the game
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "title": "I'll Keep Coming",
     "artist": "Low Roar", "durationMs": 30000},
    # YouTube only
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "title": "Old Pine",
     "artist": "Ben Howard", "youtube": YT_ID, "durationMs": 20000},
]
STAMP = "11/5 18:01:02.345  "


# ------------------------------------------------------------------- fakes --

class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeDeck:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.connected = True

    def send(self, cmd: dict) -> int:
        self.sent.append(dict(cmd))
        return 1

    def cmds(self, kind: str) -> list[dict]:
        return [c for c in self.sent if c.get("cmd") == kind]


class RecordingSpotify(Provider):
    name = "spotify"

    def __init__(self, *, available: bool = True) -> None:
        self.avail = available
        self.calls: list[tuple] = []

    def available(self) -> bool:
        return self.avail

    def can_play(self, drop: Drop) -> bool:
        return self.avail and bool(drop.spotify or (drop.title and drop.artist))

    def resolve_drop(self, drop: Drop) -> Drop:
        self.calls.append(("search", drop.artist, drop.title))
        if (drop.artist, drop.title) == ("Low Roar", "I'll Keep Coming"):
            return dataclasses.replace(drop, spotify=f"spotify:track:{SP_MATCH}",
                                       extra={**drop.extra, "matchedTitle": "I'll Keep Coming",
                                              "matchedArtist": "Low Roar", "trackDurationMs": 245000})
        return drop

    def prepare(self, drop: Drop) -> None:
        self.calls.append(("prepare", drop.id, drop.spotify))

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop.id, drop.spotify, drop.durationMs))
        return PlayHandle(provider=self.name, drop=drop,
                          now_playing=NowPlaying(title=drop.title, artist=drop.artist, label=drop.display_label,
                                                 attribution="Spotify", provider=self.name))

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop.id, fade_out_ms))

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


class RecordingYouTube(Provider):
    name = "youtube"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def available(self) -> bool:
        return True

    def can_play(self, drop: Drop) -> bool:
        return bool(drop.youtube)

    def prepare(self, drop: Drop) -> None:
        self.calls.append(("prepare", drop.id, drop.youtube))

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop.id, drop.youtube))
        return PlayHandle(provider=self.name, drop=drop, data={"playId": "p1"},
                          now_playing=NowPlaying(title=drop.title, artist=drop.artist, label=drop.display_label,
                                                 attribution="YouTube", provider=self.name))

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop.id, fade_out_ms))

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


class GameScreen:
    """CaptureBackend: the game window whose client area shows what the addon's beacon draws now."""

    hwnd = 0x5150

    def __init__(self, h: AddonHarness) -> None:
        self.h = h
        self.captures = 0

    def available(self):
        return True, ""

    def prepare(self):
        return "fake-dpi"

    def find_window(self):
        return self.hwnd

    def window_alive(self, hwnd):
        return hwnd == self.hwnd

    def is_minimized(self, hwnd):
        return False

    def client_rect(self, hwnd):
        return (0, 0, 1920, 1080)

    def capture_screen(self, x, y, w, h):
        self.captures += 1
        return self.h.beacon_bgra(width=w, height=h, seed=self.captures % 7)

    def capture_window(self, hwnd, w, h):
        return self.capture_screen(0, 0, w, h)

    def describe(self, hwnd):
        return "'World of Warcraft' pid 4242 (fake)"


class World:
    """One addon session + the companion pipeline, stepped together (0.25 s = one 4 Hz capture)."""

    def __init__(self, tmp_path: Path, *, chatlog: bool = False, spotify: bool = True) -> None:
        settings = {"chatlog": chatlog, "cooldownMin": 0, "historyMode": "fresh", "beaconCellPx": 3,
                    "duckWithoutApp": True}
        self.h = AddonHarness(sets=[make_set(DROPS)], settings=settings, location=(1415, ""))
        self.clock = FakeClock()
        self.deck = FakeDeck()
        self.sp = RecordingSpotify(available=spotify)
        self.yt = RecordingYouTube()
        self.addons = tmp_path / "WoW" / "_forever_" / "Interface" / "AddOns"
        (self.addons / "NeedleDropper").mkdir(parents=True)
        self.logs: list[str] = []
        self.links: list[str] = []
        self.engine = Engine([self.sp, self.yt], self.deck, clock=self.clock, fade_in_ms=2500, fade_out_ms=3000,
                             log=self.logs.append, prewarm_async=False, addons_dir=self.addons,
                             on_link_check=lambda fr, t: self.links.append(t))
        self.delivered: list[tuple[object, str]] = []

        def on_event(ev, name):
            self.delivered.append((ev, name))
            self.engine.submit(ev, name)

        self.screen = GameScreen(self.h)
        self.pix = PixelTransport({"enabled": True, "method": "screen", "hz": 4}, SimpleNamespace(game_exe=None),
                                  on_event, backend=self.screen, clock=self.clock)
        self.chat_path = tmp_path / "WoW" / "_forever_" / "Logs" / "WoWChatLog.txt"
        self.chat_path.parent.mkdir(parents=True)
        self.chat_path.write_text("", encoding="utf-8")
        self.chat_written = 0
        self.chat = ChatLogTransport({"enabled": True, "pollMs": 20}, SimpleNamespace(chat_log=self.chat_path),
                                     on_event)
        self.chat.poll()  # positions at the (empty) end, like at start

    def step(self, seconds: float, dt: float = 0.25) -> None:
        t = 0.0
        while t < seconds - 1e-9:
            self.h.advance(dt)
            self.clock.advance(dt)
            t += dt
            self.pix.step()
            new = self.h.chatlog_file()[self.chat_written:]
            if new:
                self.chat_written += len(new)
                with open(self.chat_path, "a", encoding="utf-8", newline="") as fh:
                    fh.write("".join(f"{STAMP}{line}\r\n" for line in new))
            self.chat.poll()
            self.engine.drain()

    def by(self, transport: str) -> list[p2.Frame]:
        return [ev for ev, name in self.delivered if name == transport]


# ------------------------------------------------------------------- tests --

def test_a_search_only_drop_arms_fires_and_plays_the_spotify_match_over_the_beacon(tmp_path):
    w = World(tmp_path)
    w.step(2)
    assert w.pix.status()["baseline"]                     # the login HELLO is the baseline, never played
    w.h.set_location(1429, "Northshire Valley")           # arrive: the Director arms the drop
    w.step(2)
    armed = [f for f in w.by("pixel") if f.type == ET.ARM]
    assert armed and (armed[0].artist, armed[0].title) == ("Low Roar", "I'll Keep Coming")
    assert w.sp.of("search") == [("search", "Low Roar", "I'll Keep Coming")]    # pre-warm at ARM
    w.step(12)                                            # dwell: FIRE
    fires = [f for f in w.by("pixel") if f.type == ET.FIRE]
    assert fires and fires[0].has(Flags.DROP_ACTIVE)
    assert w.sp.of("play") == [("play", "zone:1429", f"spotify:track:{SP_MATCH}", 30000)]
    assert len(w.sp.of("search")) == 1                    # the ARM's search result was reused
    card = w.deck.cmds("show")[-1]
    assert (card["label"], card["title"], card["artist"]) == ("Elwynn Forest", "I'll Keep Coming", "Low Roar")
    # the match is written for the game (NeedleDropper_Companion), keyed as the addon looks it up
    gamedata.flush(w.addons)
    data = (w.addons / "NeedleDropper_Companion" / "Data.lua").read_text(encoding="utf-8")
    assert SP_MATCH in data and "low roar" in data
    assert w.links == []                                  # real drops are not test signals


def test_a_youtube_drop_plays_on_youtube_and_ends_with_the_drop_in_game(tmp_path):
    w = World(tmp_path, spotify=False)
    w.step(2)
    w.h.set_location(1436, "")
    w.step(14)
    assert w.yt.of("play") == [("play", "zone:1436", YT_ID)]
    assert w.sp.of("play") == []
    w.step(30)                                            # the 20 s drop ends: the engine stops it
    assert w.yt.of("stop") and not w.engine.is_playing


def test_both_transports_deliver_the_same_frames_and_the_drop_plays_once(tmp_path):
    w = World(tmp_path, chatlog=True)
    w.step(2)
    w.h.set_location(1429, "")
    w.step(14)
    chat_types = {f.type for f in w.by("chatlog")}
    assert {ET.ARM, ET.FIRE} <= chat_types                # NDROP2 lines, flushed by their NDPAD padding
    assert all(isinstance(f, p2.Frame) for f, _ in w.delivered)
    assert len(w.sp.of("play")) == 1                      # deduplicated across the two transports
    assert w.engine.status()["duplicates"] >= 1


def test_leaving_before_the_dwell_cancels_the_arm_without_playing(tmp_path):
    w = World(tmp_path)
    w.step(2)
    w.h.set_location(1429, "")
    w.step(2)
    assert w.engine.status()["armed"] == ["zone:1429"]
    w.h.set_location(1436, "")                            # gone before the drop fires
    w.step(2)
    assert "zone:1429" not in w.engine.status()["armed"]
    assert all(c[1] != "zone:1429" for c in w.sp.of("play"))


def test_needle_test_reaches_the_link_check_and_plays_nothing(tmp_path):
    w = World(tmp_path)
    w.step(2)
    w.h.slash("test")
    w.step(4)
    assert w.links == ["pixel"]
    assert w.sp.of("play") == [] and w.yt.of("play") == [] and not w.engine.is_playing
