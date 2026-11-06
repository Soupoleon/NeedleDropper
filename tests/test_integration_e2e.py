"""End-to-end integration: the REAL addon (every file in TOC order, the shipped Map.lua that
``build-map`` generated from the starter pack) running in the lupa WoW shim, talking to the
REAL companion pipeline:

    addon Director -> Beacon cells -> synthetic BGRA screen -> transport.pixel (find_and_decode,
    PixelTransport acceptance rules)                                   \
    addon ChatLink -> WoWChatLog.txt lines -> transport.chatlog (tail) -> Engine (dedupe) ->
    MapRegistry (shipped Map.json + starter pack) -> provider.play / provider.stop + deck card

Nothing here touches the network, %APPDATA% or a real WoW install: the addon folder is only
read, everything written goes to pytest's tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import ADDON_DIR, ROOT, AddonHarness, lua_literal  # noqa: E402
from needledropper import cli, mapgen, packs, protocol, zones  # noqa: E402
from needledropper.config import Paths  # noqa: E402
from needledropper.engine import Engine  # noqa: E402
from needledropper.models import Drop, Event, EventType as ET, Flags  # noqa: E402
from needledropper.providers.base import NowPlaying, PlayHandle, Provider  # noqa: E402
from needledropper.transport.chatlog import ChatLogTransport  # noqa: E402
from needledropper.transport.pixel import PixelTransport, find_and_decode  # noqa: E402

lua51 = pytest.importorskip("lupa.lua51")

PACK = ROOT / "packs" / "azeroth-unbound.json"
SHIPPED_LUA = ADDON_DIR / "Map.lua"
SHIPPED_JSON = ADDON_DIR / "Map.json"
ELWYNN = "zone:1429"
WESTFALL = "zone:1436"
CELL_PX = 4
STRIP_ORIGIN = (3, 2)  # strip top-left inside the captured client-area region (window chrome offset)
FRAME_W, FRAME_H = 256, 32  # companion default capture region (transport.pixel regionW/regionH)


# ------------------------------------------------------------------ fixtures --

@pytest.fixture(scope="module")
def sidecar() -> dict:
    """The shipped Map.json (SPEC 3.2: ships next to the generated Map.lua)."""
    if not SHIPPED_JSON.is_file():
        pytest.fail(f"{SHIPPED_JSON} is missing: run `python -m needledropper build-map --out "
                    f"{ADDON_DIR} --no-registry` (see docs)")
    return json.loads(SHIPPED_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pack_set():
    ps = packs.load_packs_detailed([PACK])
    assert ps.ok and ps.drops, [str(i) for i in ps.errors]
    return ps


@pytest.fixture(scope="module")
def pack_raw() -> dict:
    return json.loads(PACK.read_text(encoding="utf-8"))


def pack_entry(pack_raw: dict, zone: str) -> dict:
    """The starter pack's own JSON entry for a zone drop (no subzone)."""
    return next(d for d in pack_raw["drops"] if d.get("zone") == zone and "subzone" not in d)


def map_entry(sidecar: dict, drop_id: str) -> dict:
    return next(d for d in sidecar["drops"] if d["id"] == drop_id)


# ----------------------------------------------------------------- companion --

def companion_paths(tmp_path: Path, *, addon_dir: Path = ADDON_DIR) -> Paths:
    """Companion paths for a fake WoW install under tmp_path; the addon dir is the repo's
    (read-only), so the registry reads the shipped Map.json the harness's Map.lua pairs with."""
    flavor = tmp_path / "WoW" / "_forever_"
    addons = flavor / "Interface" / "AddOns"
    app = tmp_path / "app"
    return Paths(wow_root=tmp_path / "WoW", flavor_root=flavor, addons_dir=addons, addon_dir=addon_dir,
                 local_addon_dir=addons / "NeedleDropper_Local", chat_log=flavor / "Logs" / "WoWChatLog.txt",
                 game_exe=flavor / "Wow.exe", app_dir=app, cache_dir=app / "cache", maps_dir=app / "maps")


class FakeClock:
    def __init__(self, t: float = 5000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class FakeDeck:
    """Records the deck commands the engine sends (the page itself is tested elsewhere)."""

    connected = True

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, cmd: dict) -> int:
        self.sent.append(dict(cmd))
        return len(self.sent)

    def cmds(self, kind: str) -> list[dict]:
        return [c for c in self.sent if c.get("cmd") == kind]


class RecordingProvider(Provider):
    """A provider that plays everything and remembers exactly what it was asked."""

    def __init__(self, name: str = "spotify") -> None:
        self.name = name
        self.calls: list[tuple] = []

    def available(self) -> bool:
        return True

    def can_play(self, drop: Drop) -> bool:
        return True

    def prepare(self, drop: Drop) -> None:
        self.calls.append(("prepare", drop))

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop, fade_in_ms))
        np = NowPlaying(title=drop.title, artist=drop.artist, label=drop.display_label, provider=self.name,
                        attribution="Spotify", link="https://open.spotify.com/track/x")
        return PlayHandle(provider=self.name, drop=drop, now_playing=np)

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop, fade_out_ms))

    def poll(self, handle: PlayHandle) -> str:
        return "playing"

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


def make_engine(tmp_path: Path, pack_set) -> tuple[Engine, RecordingProvider, FakeDeck, FakeClock, list[str]]:
    """A real Engine + MapRegistry (shipped Map.json, enriched from the starter pack)."""
    registry = mapgen.MapRegistry(companion_paths(tmp_path), pack_set.drops)
    provider = RecordingProvider()
    deck = FakeDeck()
    clock = FakeClock()
    logs: list[str] = []
    engine = Engine([provider], registry, deck, clock=clock, fade_in_ms=2500, fade_out_ms=3000, log=logs.append)
    return engine, provider, deck, clock, logs


# -------------------------------------------------------------- game screen --

def render_frame(cells: Optional[list[tuple[int, int, int]]], *, cell_px: int = CELL_PX,
                 origin: tuple[int, int] = STRIP_ORIGIN, size: tuple[int, int] = (FRAME_W, FRAME_H)
                 ) -> tuple[bytes, int, int, int]:
    """A top-down BGRA frame of the client area's top-left corner: a dark, textured 'game'
    background with the beacon strip drawn cell by cell at ``origin`` (None = no strip)."""
    w, h = size
    buf = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            # every channel stays below the decoder's 128 threshold: reads as black
            buf[o:o + 4] = bytes((40 + (x * 7) % 60, 30 + (y * 11) % 50, 20 + ((x + y) * 5) % 70, 255))
    if cells:
        ox, oy = origin
        for i, (r, g, b) in enumerate(cells):
            for yy in range(oy, oy + cell_px):
                for xx in range(ox + i * cell_px, ox + (i + 1) * cell_px):
                    o = (yy * w + xx) * 4
                    buf[o:o + 4] = bytes((b, g, r, 255))
    return bytes(buf), w, h, w * 4


class GameScreen:
    """Capture backend for PixelTransport: 'the WoW window' shows the harness's beacon."""

    HWND = 0x4E44

    def __init__(self, harness: Optional[AddonHarness] = None) -> None:
        self.harness = harness
        self.captures = 0

    def frame(self) -> tuple[bytes, int, int, int]:
        h = self.harness
        cells = h.beacon_cells() if (h is not None and h.beacon_shown()) else None
        return render_frame(cells)

    # CaptureBackend protocol
    def available(self) -> tuple[bool, str]:
        return True, "fake screen"

    def prepare(self) -> str:
        return "per-monitor-v2"

    def find_window(self) -> int:
        return self.HWND

    def window_alive(self, hwnd: int) -> bool:
        return hwnd == self.HWND

    def is_minimized(self, hwnd: int) -> bool:
        return False

    def client_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        return (0, 0, 1920, 1080)

    def capture_screen(self, x: int, y: int, w: int, h: int) -> tuple[bytes, int, int, int]:
        self.captures += 1
        assert (x, y) == (0, 0) and w >= FRAME_W and h >= FRAME_H
        return self.frame()

    def capture_window(self, hwnd: int, w: int, h: int) -> tuple[bytes, int, int, int]:
        return self.capture_screen(0, 0, w, h)

    def describe(self, hwnd: int) -> str:
        return "'World of Warcraft' (fake)"


class ClientChatLog:
    """Writes what the client would append to WoWChatLog.txt ("M/D HH:MM:SS.mmm  text", CRLF)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.written = 0
        path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def stamp(t: float) -> str:
        secs = 18 * 3600 + max(0.0, t - 1000.0)  # the shim's GetTime() starts at 1000
        hh, rem = divmod(int(secs), 3600)
        mm, ss = divmod(rem, 60)
        return f"11/5 {hh:02d}:{mm:02d}:{ss:02d}.{int((secs % 1) * 1000):03d}"

    def write_raw(self, lines: list[str], t: float = 900.0) -> None:
        with open(self.path, "ab") as f:
            for ln in lines:
                f.write(f"{self.stamp(t)}  {ln}\r\n".encode("utf-8"))

    def sync(self, h: AddonHarness) -> int:
        """Append the lines the addon logged since the last sync; returns how many."""
        lines = h.chatlog_file()[self.written:]
        self.written += len(lines)
        self.write_raw(lines, h.now)
        return len(lines)


def advance_until(h: AddonHarness, pred: Callable[[], bool], *, limit: float = 30.0, step: float = 0.5) -> float:
    waited = 0.0
    while not pred():
        if waited >= limit:
            raise AssertionError(f"condition not met after {limit} s of game time; events: "
                                 f"{[(e['type'], e['n'], e['id']) for e in h.events()]}")
        h.advance(step)
        waited += step
    return waited


def ndrop_lines(h: AddonHarness) -> list[str]:
    return [ln for ln in h.chatlog_file() if ln.startswith(protocol.CHAT_PREFIX + " ")]


def two_reads(pix: PixelTransport) -> list[str]:
    """The companion polls at 4 Hz and needs 2 identical valid reads before acting."""
    return [pix.step(), pix.step()]


# =============================================================== map tests ==

def _lua_to_py(v):
    if lua51.lua_type(v) != "table":
        return v
    keys = list(v.keys())
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [_lua_to_py(v[i]) for i in range(1, len(keys) + 1)]
    if not keys:
        return []
    return {k: _lua_to_py(v[k]) for k in keys}


def compile_map_lua(src: str) -> dict:
    """loadstring() the Map.lua source under Lua 5.1 exactly like the client, return the table."""
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    res = rt.execute("return function(s) local f, e = loadstring(s, '@Map.lua'); return {f, e} end")(src)
    chunk, err = res[1], res[2]
    assert chunk is not None, f"Map.lua does not compile under Lua 5.1: {err}"
    chunk()
    return _lua_to_py(rt.globals().NeedleDropper_Map)


def test_shipped_map_is_current_with_the_starter_pack(sidecar, pack_set):
    """addon/NeedleDropper/Map.lua + Map.json are exactly what build-map makes from the pack today
    (rebuild them whenever packs/azeroth-unbound.json changes)."""
    fresh = mapgen.build_map(pack_set.drops, pack_set.names, generated=sidecar["generated"])
    regen = ("shipped Map.lua/Map.json are stale (or were built with a Spotify resolve cache): regenerate with "
             "NEEDLEDROPPER_HOME=<empty dir> python -m needledropper build-map --out addon/NeedleDropper --no-registry")
    assert fresh["hash"] == sidecar["hash"], regen
    assert fresh == sidecar, regen
    assert compile_map_lua(SHIPPED_LUA.read_text(encoding="utf-8")) == sidecar
    raw = SHIPPED_LUA.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    non_ascii = [s for d in sidecar["drops"] for s in (d["title"], d["artist"], d["label"]) if not s.isascii()]
    assert all(s in text for s in non_ascii)  # UTF-8 kept as-is through the Lua escaper


def test_map_built_by_cli_from_full_pack_compiles_and_every_drop_resolves(tmp_path, sidecar, pack_set):
    # the real CLI (NEEDLEDROPPER_HOME is a tmp dir via conftest; default packs = the starter pack)
    out = tmp_path / "addon"
    assert cli.main(["build-map", "--out", str(out), "--no-registry"]) == 0
    lua_src = (out / "Map.lua").read_text(encoding="utf-8")
    js = json.loads((out / "Map.json").read_text(encoding="utf-8"))
    assert compile_map_lua(lua_src) == js
    assert js["hash"] == sidecar["hash"] and js["drops"] == sidecar["drops"]

    drops = js["drops"]
    assert [d["n"] for d in drops] == list(range(1, len(drops) + 1))
    assert [d["id"] for d in drops] == sorted(d["id"] for d in drops)  # SPEC 3.2: n by plain id sort
    assert len(drops) == len(pack_set.drops) == 62

    # companion: every (hash, n) and every chat-channel id resolves to the same song
    registry = mapgen.MapRegistry(companion_paths(tmp_path, addon_dir=out), pack_set.drops)
    assert registry.installed_hash == js["hash"]
    for d in drops:
        got = registry.resolve(js["hash"], d["n"])
        assert got is not None, d["id"]
        assert (got.id, got.title, got.artist, got.display_label, got.kind) == \
               (d["id"], d["title"], d["artist"], d["label"], d["kind"])
        ev = Event(ET.FIRE, 1, d["n"], js["hash"], Flags.DROP_ACTIVE)
        assert registry.resolve_event(ev).id == d["id"]
        stale = Event(ET.FIRE, 1, d["n"], (js["hash"] + 1) % 65536 or 2, Flags.NONE, protocol.sanitize_id(d["id"]))
        assert registry.resolve_event(stale).id == d["id"]  # unknown hash -> chat id fallback
        assert registry.find(d["id"]).id == d["id"]

    # zones.py agrees with every location in the map
    for d in drops:
        z = zones.ZONES_BY_ID.get(d.get("mapID"))
        assert z is not None, f"{d['id']}: mapID {d.get('mapID')} is not a known zone"
        if d["kind"] == "zone":
            assert d["id"] == f"zone:{z.id}" and d["label"] == z.name
        else:
            assert d["subzone"] and d["label"] == d["subzone"]

    # addon: the generated file loads with no malformed entries, and its indexes find every drop
    h = AddonHarness(map_lua=lua_src, login=False)
    h.fire("ADDON_LOADED", "NeedleDropper")
    M = h.ns.Map
    assert M.loaded and M.invalid == 0, h.from_lua(M.problems)
    assert M.hash == js["hash"] and len(M.drops) == len(drops)
    for d in drops:
        assert M.byN[d["n"]].id == d["id"]
        if d["kind"] == "zone":
            assert M.zoneDrops[d["mapID"]].id == d["id"]
        else:
            found = h.ns.FindSubzoneDrop(d["mapID"], d["subzone"].lower())
            assert found is not None and found.id == d["id"], d["id"]
            if d.get("areaID"):
                assert M.areaDrops[d["areaID"]].id == d["id"]


# ============================================================ the needle drop ==

def test_fresh_character_elwynn_needle_drop_end_to_end(tmp_path, sidecar, pack_set, pack_raw):
    """A brand-new human (level 1, Northshire pre-explored) logs in to Elwynn Forest with the
    companion already running: ARM -> dwell -> FIRE reaches the provider exactly once via two
    transports, then /needle stop sends CANCEL and the provider fades out."""
    want = pack_entry(pack_raw, "Elwynn Forest")
    elwynn = map_entry(sidecar, ELWYNN)
    paths = companion_paths(tmp_path)
    engine, provider, deck, clock, logs = make_engine(tmp_path, pack_set)

    # --- companion starts first: an old session's lines are in the chat log already
    chat_log = ClientChatLog(paths.chat_log)
    stale = protocol.format_chat_line(Event(ET.FIRE, 7, elwynn["n"], sidecar["hash"], Flags.DROP_ACTIVE, ELWYNN))
    chat_log.write_raw(["Welcome to World of Warcraft!", stale] + [protocol.PAD_PREFIX + " 7 ......"])
    chat = ChatLogTransport({"enabled": True, "pollMs": 250}, paths, engine.submit)
    assert chat.poll() == 0  # history is skipped, never replayed
    screen = GameScreen()
    pixel_diag: list[str] = []
    pix = PixelTransport({"enabled": True, "method": "auto", "hz": 4, "regionW": FRAME_W, "regionH": FRAME_H},
                         paths, engine.submit, diag=pixel_diag.append, backend=screen)
    assert pix.step() == "invalid"  # game window shows no beacon yet

    # --- the game: a fresh character logs in at Northshire Valley, Elwynn Forest (uiMapID 1429)
    h = AddonHarness(use_map_file=True, location=(1429, "Northshire Valley"),
                     state={"level": 1, "exploredTextures": {1429: 1}}, settings={"chatlog": True}, settle=0)
    screen.harness = h
    assert h.ns.Map.hash == sidecar["hash"] and h.ns.Map.invalid == 0
    assert h.char_db()["done"] == {}
    assert [e["type"] for e in h.events()] == [ET.HELLO]
    assert pix.step() == "baseline"  # first readable frame after start is never acted on
    assert chat_log.sync(h) == 1 and chat.poll() == 1  # HELLO via the chat log
    engine.drain()

    # --- history scan (~3 s) -> ARM; the companion prewarms the provider
    advance_until(h, lambda: any(e["type"] == ET.ARM for e in h.events()), limit=6)
    arm = h.events()[-1]
    assert (arm["type"], arm["n"], arm["id"], arm["hash"]) == (ET.ARM, elwynn["n"], ELWYNN, sidecar["hash"])
    assert two_reads(pix) == ["pending", "emitted"]
    chat_log.sync(h)
    assert chat.poll() == 1
    engine.drain()
    assert [c[1].id for c in provider.of("prepare")] == [ELWYNN]
    assert engine.status()["armed"] == [ELWYNN]

    # --- dwell (10 s in the zone, all gates clear) -> FIRE
    assert not h.events_of(ET.FIRE)
    advance_until(h, lambda: bool(h.events_of(ET.FIRE)), limit=12)
    fire = h.events_of(ET.FIRE)[0]
    assert fire["n"] == elwynn["n"] and fire["id"] == ELWYNN and fire["hash"] == sidecar["hash"]
    assert fire["flags"] == int(Flags.DROP_ACTIVE)  # first-visit drop: not TEST, not LOCAL_PLAYBACK
    assert h.ns.Director.active is not None
    assert h.char_db()["done"][ELWYNN]["how"] == "played"
    assert [j["id"] for j in h.char_db()["journal"]] == [ELWYNN]
    fire_ev = h.as_event(fire)

    # primary channel: decode the beacon from a synthetic screen grab (cell 4 px, offset 3,2)
    cells = h.beacon_cells()
    assert len(cells) == protocol.TOTAL_CELLS and tuple(cells[:4]) == protocol.HEADER
    got = find_and_decode(*render_frame(cells))
    assert got is not None, "find_and_decode could not read the addon's beacon"
    assert got.event == fire_ev
    assert (got.x, got.y) == STRIP_ORIGIN and got.cell_px == pytest.approx(CELL_PX, abs=0.01)
    assert find_and_decode(*render_frame(cells, cell_px=2, origin=(0, 0))).event == fire_ev

    # secondary channel: the chat line parses to the same event plus the drop id; 56 pad lines follow
    line = ndrop_lines(h)[-1]
    parsed = protocol.parse_chat_line(f"{chat_log.stamp(h.now)}  {line}")
    assert parsed is not None and parsed.key == fire_ev.key and parsed.flags == fire_ev.flags
    assert parsed.id == ELWYNN
    new = h.chatlog_file()[chat_log.written:]
    assert new[0] == line and len(new) == 1 + protocol.pad_line_count(50000)
    assert all(x.startswith(protocol.PAD_PREFIX + " ") and len(x) == 900 for x in new[1:])

    # both transports deliver it; the engine plays it once
    assert two_reads(pix) == ["pending", "emitted"]
    chat_log.sync(h)
    assert chat.poll() == 1  # NDPAD lines are ignored
    engine.drain()
    plays = provider.of("play")
    assert len(plays) == 1
    drop, fade_in = plays[0][1], plays[0][2]
    assert (drop.id, drop.n, drop.kind, drop.mapID) == (ELWYNN, elwynn["n"], "zone", 1429)
    assert (drop.title, drop.artist) == (want["title"], want["artist"]) == ("I'll Keep Coming", "Low Roar")
    assert drop.display_label == "Elwynn Forest"
    assert drop.durationMs == want["durationMs"] and drop.note == want["note"]  # pack-only field via enrich
    assert fade_in == 2500
    assert engine.played == 1 and engine.counts["FIRE"] == 1  # the stale history FIRE never arrived
    assert engine.duplicates == 2  # ARM + FIRE seen on both transports
    assert engine.is_playing and engine.status()["playing"]["id"] == ELWYNN
    card = deck.cmds("show")[-1]
    assert (card["label"], card["title"], card["artist"]) == ("Elwynn Forest", want["title"], want["artist"])
    assert card["attribution"] == "Spotify" and card["test"] is False

    # the title card in game
    h.advance(1.6)
    texts = h.toast_texts()
    assert h.toast_visible() and texts[0].replace(" ", "") == "ELWYNNFOREST"
    assert want["artist"] in texts[1] and want["title"] in texts[1]

    # --- CANCEL: /needle stop while it plays (FIRE has been on the beacon > MIN_HOLD)
    out = h.slash("stop")
    assert out and "stopped Elwynn Forest" in out[-1]
    cancel = h.last_event()
    assert (cancel["type"], cancel["n"], cancel["id"]) == (ET.CANCEL, elwynn["n"], ELWYNN)
    assert not Flags(cancel["flags"]) & Flags.DROP_ACTIVE
    assert h.beacon_event() == h.as_event(cancel)
    assert find_and_decode(*render_frame(h.beacon_cells())).event == h.as_event(cancel)
    assert two_reads(pix) == ["pending", "emitted"]
    chat_log.sync(h)
    assert chat.poll() == 1
    engine.drain()
    stops = provider.of("stop")
    assert len(stops) == 1 and stops[0][1].id == ELWYNN and stops[0][2] == 3000
    assert deck.cmds("hide")[-1]["dropId"] == ELWYNN
    assert not engine.is_playing
    assert engine.duplicates == 3 and engine.counts["CANCEL"] == 1
    assert h.char_db()["done"][ELWYNN]["how"] == "played"  # stop keeps the drop heard
    assert any("baseline" in ln for ln in pixel_diag)

    # nothing else happens: Elwynn is done, so no re-ARM, and the companion stays quiet
    h.advance(30)
    assert h.events_of(ET.FIRE) == [fire] and len(h.events_of(ET.ARM)) == 1
    assert pix.step() == "same"
    chat_log.sync(h)
    assert chat.poll() == 0
    engine.drain()
    assert len(provider.of("play")) == 1 and h.errors() == []


def test_leaving_before_dwell_cancels_the_arm_without_playing(tmp_path, sidecar, pack_set, pack_raw):
    """ARM is only a prewarm: walking out of Elwynn before the dwell sends CANCEL for the armed
    drop (not marked done); the companion plays nothing until Westfall's own drop fires."""
    engine, provider, deck, clock, logs = make_engine(tmp_path, pack_set)
    h = AddonHarness(use_map_file=True, location=(1429, "Northshire Valley"), state={"level": 1},
                     settings={"chatlog": True}, settle=5)
    elwynn, westfall = map_entry(sidecar, ELWYNN), map_entry(sidecar, WESTFALL)
    assert [(e["type"], e["n"]) for e in h.events()] == [(ET.HELLO, 0), (ET.ARM, elwynn["n"])]

    h.set_location(1436, "")  # Westfall, ~5.6 s after arriving: well before the 10 s dwell
    h.advance(2)
    tail = [(e["type"], e["id"]) for e in h.events()[2:]]
    assert tail[:2] == [(ET.CANCEL, ELWYNN), (ET.ARM, WESTFALL)]
    assert ELWYNN not in h.char_db()["done"]

    def feed_chat(start: int) -> int:
        lines = ndrop_lines(h)
        for ln in lines[start:]:
            ev = protocol.parse_chat_line(ln)
            assert ev is not None, ln
            engine.handle(ev, "chatlog")
        return len(lines)

    seen = feed_chat(0)
    engine.tick()
    assert [c[1].id for c in provider.of("prepare")] == [ELWYNN, WESTFALL]
    assert provider.of("play") == [] and provider.of("stop") == []
    assert engine.status()["armed"] == [WESTFALL]
    assert any(f"ARM n={elwynn['n']} abandoned" in ln for ln in logs)

    advance_until(h, lambda: bool(h.events_of(ET.FIRE)), limit=12)
    assert h.events_of(ET.FIRE)[0]["id"] == WESTFALL
    feed_chat(seen)
    want = pack_entry(pack_raw, "Westfall")
    plays = provider.of("play")
    assert len(plays) == 1 and plays[0][1].id == WESTFALL and plays[0][1].n == westfall["n"]
    assert (plays[0][1].title, plays[0][1].artist) == (want["title"], want["artist"])
    assert engine.status()["armed"] == []


def test_every_starter_pack_drop_fires_in_game_and_resolves_in_companion(tmp_path, sidecar):
    """All 62 shipped drops are reachable by the addon's location logic and resolve back to the
    same song in the companion (zone keys, landmark names and (hash, n) all line up)."""
    zone_maps = sorted({d["mapID"] for d in sidecar["drops"]})

    def add_pack_zones(h: AddonHarness) -> None:
        # The shim only knows a handful of maps; teach it every zone the pack uses (mapType 3 = Zone).
        for mid in zone_maps:
            h.lua("if not SHIM.maps[%d] then SHIM.maps[%d] = { mapID = %d, name = %s, mapType = 3, "
                  "parentMapID = 947 } end" % (mid, mid, mid, lua_literal(zones.ZONES_BY_ID[mid].name)))

    fast = {"historyMode": "fresh", "cooldownMin": 0, "dwellSec": 1, "minDwellSec": 1, "landmarkDwellSec": 1,
            "toast": False, "chatlog": True, "chatlogPadBytes": 0}
    h = AddonHarness(use_map_file=True, location=(9100, ""), before_load=add_pack_zones, settings=fast)
    assert h.events_of(ET.ARM) == []  # an orphan map has no zone key: nothing pending at login
    registry = mapgen.MapRegistry(companion_paths(tmp_path))  # Map.json only, no pack enrichment
    provider = RecordingProvider()
    clock = FakeClock()
    engine = Engine([provider], registry, None, clock=clock, log=lambda s: None)

    order = sorted(sidecar["drops"], key=lambda d: (d["kind"] != "zone", d["n"]))
    for d in order:
        mark = h.mark()
        h.set_location(d["mapID"], d.get("subzone") or "")
        advance_until(h, lambda: any(e["type"] == ET.FIRE for e in h.events_since(mark)), limit=8)
        fire = [e for e in h.events_since(mark) if e["type"] == ET.FIRE][-1]
        assert (fire["n"], fire["id"], fire["hash"]) == (d["n"], d["id"], sidecar["hash"])
        parsed = protocol.parse_chat_line(ndrop_lines(h)[-1])  # Lua SanitizeId + CRC vs the Python parser
        assert parsed is not None and parsed.key == h.as_event(fire).key and parsed.id == d["id"]
        engine.handle(h.as_event(fire), "pixel")
        h.slash("stop")
        cancel = h.last_event()
        assert (cancel["type"], cancel["n"]) == (ET.CANCEL, d["n"])
        engine.handle(h.as_event(cancel), "pixel")
        clock.advance(20)  # let the 15 s dedupe window pass before seq numbers can wrap

    played = [c[1] for c in provider.of("play")]
    assert [p.id for p in played] == [d["id"] for d in order]
    for p, d in zip(played, order):
        assert (p.title, p.artist, p.display_label) == (d["title"], d["artist"], d["label"])
    assert [c[1].id for c in provider.of("stop")] == [d["id"] for d in order]
    assert set(h.char_db()["done"]) == {d["id"] for d in sidecar["drops"]}


def test_elwynn_drop_works_without_optional_client_apis(sidecar):
    """SPEC 4: optional/possibly-missing APIs are guarded. With all of them gone the addon still
    loads, fires the Elwynn drop and both transports still carry it."""
    def strip_optional_apis(h: AddonHarness) -> None:
        h.lua("Settings = nil; C_RestrictedActions = nil; C_MapExplorationInfo = nil; issecretvalue = nil; "
              "C_ChatInfo = nil; PixelUtil = nil; if C_CVar then C_CVar.SetTempCVar = nil end")

    h = AddonHarness(use_map_file=True, location=(1429, "Northshire Valley"), before_load=strip_optional_apis,
                     settings={"chatlog": True, "duckMethod": "volume"})
    advance_until(h, lambda: bool(h.events_of(ET.FIRE)), limit=12)
    fire = h.events_of(ET.FIRE)[0]
    assert fire["id"] == ELWYNN and fire["hash"] == sidecar["hash"]
    assert find_and_decode(*render_frame(h.beacon_cells())).event == h.as_event(fire)
    parsed = protocol.parse_chat_line(ndrop_lines(h)[-1])
    assert parsed is not None and parsed.id == ELWYNN and parsed.key == h.as_event(fire).key
    assert h.cvar("Sound_MusicVolume") == "0"  # SetCVar fallback when SetTempCVar is missing
    out = h.slash("options")
    assert any("not available" in ln for ln in out)
    assert h.slash("status")
    h.advance(1.1)
    h.slash("stop")
    assert h.last_event()["type"] == ET.CANCEL
    assert h.cvar("Sound_MusicVolume") == "0.4"  # restored
    assert h.errors() == []
