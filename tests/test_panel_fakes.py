"""Shared helpers for the companion shell tests (Connect page, tray mode, link check, faults):
real HTTP on ephemeral ports, and stand-ins for the engine, transports, tray icon and the
Windows registry. Contains no tests itself."""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for p in (ROOT / "companion", TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from needledropper import config, tray  # noqa: E402
from needledropper.models import Drop  # noqa: E402
from needledropper.providers.base import NowPlaying, PlayHandle, Provider  # noqa: E402
from test_autostart import FakeWinreg  # noqa: E402,F401 - re-exported for the shell tests


def http_call(port: int, method: str, path: str, body: Any = None, *, token: Optional[str] = None,
              headers: Optional[dict[str, str]] = None, raw: Optional[bytes] = None,
              timeout: float = 15.0) -> tuple[int, Any, Any]:
    """One request -> (status, parsed JSON or raw bytes, response headers)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    h: dict[str, str] = {}
    if token is not None:
        h["X-ND-Token"] = token
    data = raw
    if body is not None and raw is None:
        data = json.dumps(body).encode("utf-8")
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    try:
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        payload = resp.read()
        hdrs = resp.headers
        status = resp.status
    finally:
        conn.close()
    ctype = hdrs.get("Content-Type") or ""
    if ctype.startswith("application/json"):
        try:
            return status, json.loads(payload.decode("utf-8")), hdrs
        except ValueError:
            pass
    return status, payload, hdrs


def launch_key(args: list[str]) -> str:
    """The player's launch key from a deck launch command (``--app=<player url>#k=<key>``), as a browser
    would have it in the window's address."""
    for a in args:
        if a.startswith("--app=") and "#k=" in a:
            return a.split("#k=", 1)[1]
    return ""


class SSE:
    """A minimal EventSource client for /events?client=... (a player page sends the deck's launch key
    ``key``, as player.js does from its address; v1.2.1 review ON-1)."""

    def __init__(self, port: int, client: Optional[str] = "player", *, key: Optional[str] = None) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        query = [f"client={client}"] if client else []
        if key is not None:
            query.append(f"k={key}")
        self.conn.request("GET", "/events" + ("?" + "&".join(query) if query else ""))
        self.resp = self.conn.getresponse()
        self.status = self.resp.status
        self.messages: list[dict] = []
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    def next_data(self, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.resp.fp.readline().decode("utf-8")
            if not line:
                raise EOFError("stream closed")
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise TimeoutError

    def pump(self) -> "SSE":
        """Collect messages on a background thread (``messages``)."""
        def run() -> None:
            try:
                while not self._closed:
                    self.messages.append(self.next_data(timeout=30))
            except Exception:
                pass
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._closed = True
        sock = getattr(self.conn, "sock", None)
        if sock is not None:
            try:  # a reader thread keeps the socket alive through makefile(): force the disconnect
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            self.resp.close()
        finally:
            self.conn.close()


def wait_for(pred, timeout: float = 5.0, step: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


class FakePlayer:
    """Stands in for the browser: connects a player SSE client to the deck when "launched",
    answers ``play`` commands with a ``playing`` status like player.js does."""

    def __init__(self, deck: Any, *, answer: Optional[str] = "playing") -> None:
        self.deck = deck
        self.answer = answer
        self.launches: list[list[str]] = []
        self.clients: list[SSE] = []
        self.commands: list[dict] = []

    def __call__(self, args: list[str]) -> None:  # the deck's launcher
        self.launches.append(list(args))
        threading.Thread(target=self._run, args=(launch_key(args),), daemon=True).start()

    def _run(self, key: str = "") -> None:
        sse = SSE(self.deck.port, "player", key=key)  # the key from the --app address, as a browser has it
        self.clients.append(sse)
        try:
            while True:
                cmd = sse.next_data(timeout=30)
                self.commands.append(cmd)
                if cmd.get("cmd") == "play" and self.answer:
                    http_call(self.deck.port, "POST", "/status", {"state": self.answer, "playId": cmd["playId"],
                                                                   "detail": cmd.get("source", "")},
                              token=self.deck.token, headers={"X-ND-Key": key})  # v1.2.1 polish: every player report carries the launch key
        except Exception:
            pass

    def close(self) -> None:
        # The deck registers a player before its response headers reach us, so a launch may
        # still be inside SSE(); wait until every launched window has its client.
        wait_for(lambda: len(self.clients) >= len(self.launches), timeout=3)
        for c in list(self.clients):
            if c._closed:
                continue
            try:
                c.close()
            except Exception:
                pass


class FakeProvider(Provider):
    """A provider that records calls (for the provider chain)."""

    def __init__(self, name: str, *, avail: bool = True, can: bool = True) -> None:
        self.name = name
        self.avail = avail
        self.can = can
        self.calls: list[tuple] = []
        self.closed = False

    def available(self) -> bool:
        return self.avail

    def can_play(self, drop: Drop) -> bool:
        return self.can

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop.id))
        np = NowPlaying(title=drop.title, artist=drop.artist, provider=self.name, attribution=self.name.title())
        return PlayHandle(provider=self.name, drop=drop, now_playing=np)

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop.id))

    def close(self) -> None:
        self.closed = True


class FakeEngine:
    """Stands in for ``engine.Engine`` as CONTRACTS-v1.2 3.2 defines it:
    ``Engine(providers, deck, *, clock, fade_in_ms, fade_out_ms, log, on_link_check, on_status)``
    with ``submit`` / ``start`` / ``stop`` / ``status``. Records what it is given; tests call
    ``on_link_check`` / ``on_status`` the way the real engine does."""

    def __init__(self, providers: list, deck: Any, *, fade_in_ms: int = 0, fade_out_ms: int = 0, log: Any = None,
                 on_link_check: Any = None, on_status: Any = None, clock: Any = None, **extra: Any) -> None:
        self.providers = list(providers)
        self.deck = deck
        self.extra = extra  # e.g. addons_dir (where the real engine records tracks for the game)
        self.fade_in_ms = fade_in_ms
        self.fade_out_ms = fade_out_ms
        self.on_link_check = on_link_check
        self.on_status = on_status
        self.submitted: list[tuple[Any, str]] = []
        self.started = self.stopped = False
        self.stop_playback_calls = 0
        self.playing: Optional[dict] = None

    def start(self) -> "FakeEngine":
        self.started = True
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self.stopped = True

    def submit(self, event: Any, transport_name: str) -> None:
        self.submitted.append((event, transport_name))

    def stop_playback(self) -> None:
        self.stop_playback_calls += 1
        self.playing = None

    def status(self) -> dict:
        return {"running": self.started and not self.stopped, "playing": self.playing,
                "addon": {"seen": bool(self.submitted)}}


class EngineFactory:
    """``engine_factory`` for :class:`~needledropper.panel_api.Companion`; ``made`` keeps every engine."""

    def __init__(self, cls: type = FakeEngine) -> None:
        self.cls = cls
        self.made: list[Any] = []

    def __call__(self, providers: list, deck: Any, **kw: Any) -> Any:
        engine = self.cls(providers, deck, **kw)
        self.made.append(engine)
        return engine

    @property
    def current(self) -> Any:
        return self.made[-1]


class FakeTransport:
    """A transport that never reads the game: tests push events with :meth:`emit`."""

    def __init__(self, name: str, on_event: Any) -> None:
        self.name = name
        self.on_event = on_event
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def status(self) -> dict:
        return {"running": self.started and not self.stopped, "ok": True, "detail": "fake"}

    def emit(self, event: Any) -> None:
        self.on_event(event, self.name)


class TransportFactory:
    """``transport_factory(cfg, paths, on_event)``: a pixel and a chat-log :class:`FakeTransport`."""

    def __init__(self) -> None:
        self.made: list[FakeTransport] = []

    def __call__(self, cfg: dict, paths: Any, on_event: Any) -> list:
        built = [FakeTransport(name, on_event) for name in ("pixel", "chatlog")
                 if (cfg.get("transport") or {}).get(name, {}).get("enabled", True)]
        self.made.extend(built)
        return built

    def live(self, name: str) -> FakeTransport:
        return [t for t in self.made if t.name == name and t.started and not t.stopped][-1]


class FakeTrayIcon:
    """Records what the companion does with its tray icon (never touches Win32)."""

    instances: list["FakeTrayIcon"] = []

    def __init__(self, title: str, icon_path: Any = None, menu_items: Any = (), on_click: Any = None, *,
                 tooltip: Optional[str] = None, on_notification_click: Any = None, on_close: Any = None,
                 **kw: Any) -> None:
        self.title = title
        self.icon_path = icon_path
        self.menu_items = list(menu_items)
        self.on_click = on_click
        self.tooltip = tooltip
        self.on_notification_click = on_notification_click
        self.on_close = on_close
        self.kw = dict(kw)  # e.g. on_session_end (the synchronous log-off / shut-down clean-up)
        self.notes: list[tuple[str, str, str]] = []
        self.tooltips: list[str] = []
        self.icons: list[Any] = []
        self.started = self.stopped = False
        FakeTrayIcon.instances.append(self)

    def start(self) -> "FakeTrayIcon":
        self.started = True
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True

    def notify(self, title: str, text: str, *, kind: str = "info", **kw: Any) -> bool:
        self.notes.append((title, text, kind))
        return True

    def set_tooltip(self, text: str) -> None:
        self.tooltip = text
        self.tooltips.append(text)

    def set_icon(self, icon_path: Any) -> None:
        self.icon_path = icon_path
        self.icons.append(icon_path)

    # test helpers
    def lines(self) -> list[str]:
        return tray.MenuLayout.build(self.menu_items).lines()

    def pick(self, item_id: str) -> bool:
        """Click a menu item the way the tray does (with the state shown when the menu opened)."""
        layout = tray.MenuLayout.build(self.menu_items)
        for command, (item, _checked, _enabled) in layout.commands.items():
            if item.id == item_id:
                return layout.dispatch(command)
        raise KeyError(item_id)


def write_pack(path: Path, drops: list[dict], name: str = "Panel Pack") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": "needledropper-pack", "version": 1, "name": name, "drops": drops}),
                    encoding="utf-8")
    return path


PACK_DROPS = [
    {"zone": "Elwynn Forest", "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 245000,
     "youtube": "dQw4w9WgXcQ"},
    {"zone": 1429, "subzone": "Goldshire", "areaID": 87, "title": "Goldshire Song", "artist": "A",
     "spotify": "spotify:track:4uLU6hMCjMI75M1A2tKUQC"},
]


def make_fake_wow(root: Path, *, exe: bool = True, addons: bool = True) -> Path:
    """``<root>\\_forever_`` (+ ``Wow.exe``, ``Interface\\AddOns``) like a WoW: Forever install."""
    fl = root / "_forever_"
    fl.mkdir(parents=True, exist_ok=True)
    if exe:
        (fl / "Wow.exe").write_bytes(b"MZ")
    if addons:
        (fl / "Interface" / "AddOns").mkdir(parents=True, exist_ok=True)
    return root


def make_fake_addon(root: Path, version: str = "1.2.0", *, name: str = "addon_src") -> Path:
    """A v1.2-shaped addon source: TOC, a module, ``Sets/*.lua`` and the silence file."""
    src = root / name / "NeedleDropper"
    (src / "media").mkdir(parents=True, exist_ok=True)
    (src / "Sets").mkdir(parents=True, exist_ok=True)
    (src / "__pycache__").mkdir(exist_ok=True)
    (src / "NeedleDropper.toc").write_text(
        f"## Interface: 16001\n## Title: NeedleDropper\n## Version: {version}\nCore.lua\nSets.lua\n"
        "Sets\\AzerothMixtape.lua\n", encoding="utf-8")
    (src / "Core.lua").write_text("local ADDON, ns = ...\n", encoding="utf-8")
    (src / "Sets.lua").write_text("local ADDON, ns = ...\n", encoding="utf-8")
    (src / "Sets" / "AzerothMixtape.lua").write_text(
        "local ADDON, ns = ...\n-- Generated by tools/gen_sets.py from packs/azeroth-mixtape.json.\n"
        "ns.RegisterBuiltinSet({ id = \"azeroth-mixtape\", drops = {} })\n", encoding="utf-8")
    (src / "media" / "silence.mp3").write_bytes(b"\xff\xfb")
    (src / "__pycache__" / "junk.pyc").write_bytes(b"x")
    (src / "notes.bak").write_text("x", encoding="utf-8")
    return src


def install_v11_addon(addon_dir: Path, version: str = "1.1.0") -> Path:
    """An addon folder as the v1.1 companion left it (Map.lua era, no receipt)."""
    addon_dir.mkdir(parents=True, exist_ok=True)
    (addon_dir / "NeedleDropper.toc").write_text(f"## Interface: 16001\n## Version: {version}\nProtocol.lua\nMap.lua\n"
                                                 "Core.lua\n", encoding="utf-8")
    for name in ("Protocol.lua", "Map.lua", "Core.lua"):
        (addon_dir / name).write_text("local ADDON, ns = ...\n", encoding="utf-8")
    (addon_dir / "Map.json").write_text('{"hash": 1, "drops": []}', encoding="utf-8")
    return addon_dir


def no_registry(hive: str, subkey: str, name: str) -> None:
    return None


def make_shell(tmp_path: Path, home: Path, *, cfg_extra: Optional[dict] = None, wow: bool = True,
               addon_version: str = "1.2.0", running: Optional[dict] = None, start: bool = True,
               deck_kwargs: Optional[dict] = None, **companion_kw: Any) -> SimpleNamespace:
    """A started :class:`~needledropper.panel_api.Companion` on a real deck (ephemeral port) with
    a fake engine, fake transports, a fake registry and a fake WoW folder. ``ns.call(method,
    path, body)`` talks to its API; ``ns.close()`` stops everything."""
    from needledropper.deck.server import Deck
    from needledropper.panel_api import Companion

    wow_root = make_fake_wow(tmp_path / "WoW") if wow else tmp_path / "NoWoW"
    cfg_data = {"wowRoot": str(wow_root), "deck": {"port": 0}, "myOwnKey": {"keep": True}}
    for key, value in (cfg_extra or {}).items():
        cfg_data[key] = value
    cfg_file = home / "config.json"
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")
    cfg = config.load_config(cfg_file)
    paths = config.paths_from_config(cfg)
    deck = Deck(port=0, heartbeat_sec=0.2, url_opener=lambda u: False, launcher=lambda a: None,
                **(deck_kwargs or {}))
    deck.start()
    engines = EngineFactory()
    transports = TransportFactory()
    reg = FakeWinreg()
    opened: list[str] = []
    providers: dict[str, FakeProvider] = {}
    game = running if running is not None else {"v": False}

    def provider_factory(cfg: dict, paths: Any, deck: Any) -> list:
        out = []
        for name in cfg["providers"]:
            if config.provider_enabled(cfg, name):
                providers[name] = FakeProvider(name)
                out.append(providers[name])
        return out

    kw: dict[str, Any] = dict(provider_factory=provider_factory, transport_factory=transports,
                              engine_factory=engines, opener=opened.append, game_running=lambda p: game["v"],
                              wow_registry=no_registry, wow_default=str(tmp_path / "NoDefaultWoW"),
                              addon_source=make_fake_addon(tmp_path, addon_version),
                              autostart_winreg=reg, notifier=tray.Notifier(), housekeeping_s=0.05)
    kw.update(companion_kw)
    app = Companion(cfg, paths, deck=deck, config_file=cfg_file, **kw)
    if start:
        app.start()

    def call(method: str, path: str, body: Any = None, **kwargs: Any) -> tuple[int, Any]:
        st, data, _h = http_call(deck.port, method, path, body, token=deck.token, **kwargs)
        return st, data

    def close() -> None:
        app.stop()
        deck.stop()

    return SimpleNamespace(app=app, deck=deck, call=call, close=close, cfg_file=cfg_file, home=home, wow=wow_root,
                           engines=engines, transports=transports, reg=reg, opened=opened, providers=providers,
                           game=game, addon_src=kw["addon_source"], paths=lambda: app.paths)


def raw_config(cfg_file: Path) -> dict:
    return json.loads(cfg_file.read_text(encoding="utf-8"))
