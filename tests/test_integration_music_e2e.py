"""End to end for the music services (DESIGN-v1.2.1 5.4), in the style of test_integration_e2e.py: the REAL
addon (every file in TOC order) in the lupa WoW shim, the REAL companion (panel_api.Companion with its
MusicConfig, the real engine, a real deck) and the real links between them:

    MusicServices.Send / a music setting -> Emit -> Beacon v2 textures -> synthetic BGRA client area ->
    transport.pixel.PixelTransport (beacon2, two identical reads)  \\
    ChatLink "NDROP2 <frame>" -> WoWChatLog.txt -> transport.chatlog --+-> Companion.submit -> MusicConfig
    -> config.json (the choices) / Spotify's sign-in (a real PKCE login against scripted Spotify HTTP) /
    the engine's test play -> NeedleDropper_Companion\\Data.lua (status + the music block) -> /reload ->
    the real Companion.lua and MusicServices.lua read the result.

Nothing touches the network, a real browser, %APPDATA% or a real WoW install: the browser opener only
records the URL, the player page is a test SSE client, Spotify's HTTP is scripted, and the game's
``time()`` is the clock the app checks a request's freshness against.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

lua51 = pytest.importorskip("lupa.lua51")

from addon_harness import AddonHarness, make_set  # noqa: E402
from test_integration_e2e import DROPS, STAMP, FakeClock, GameScreen, RecordingYouTube  # noqa: E402
from test_panel_fakes import SSE, FakeWinreg, http_call, make_fake_addon, make_fake_wow, no_registry, wait_for  # noqa: E402
from test_panel_spotify import approve, free_port, token_routes  # noqa: E402
from test_spotify_fakes import FakeHTTP  # noqa: E402

from needledropper import config, gamedata as gd, music_config as mc, protocol2 as p2, tray  # noqa: E402
from needledropper.deck.server import Deck  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402
from needledropper.panel_api import Companion  # noqa: E402
from needledropper.transport.chatlog import ChatLogTransport  # noqa: E402
from needledropper.transport.pixel import PixelTransport  # noqa: E402

CID = "0123456789abcdef0123456789abcdef"


class World:
    """The PC (the app, its deck, its config.json and the game's AddOns folder) and one game session."""

    def __init__(self, tmp_path: Path, home: Path, *, beacon: bool = True, chatlog: bool = False) -> None:
        self.tmp = tmp_path
        wow = make_fake_wow(tmp_path / "WoW")
        self.addons = wow / "_forever_" / "Interface" / "AddOns"
        self.port = free_port()
        self.cfg_file = home / "config.json"
        self.cfg_file.write_text(json.dumps({
            "wowRoot": str(wow), "deck": {"port": 0},
            # connect mode: the in-app player (a real Chrome) has no place in a test
            "spotify": {"redirectPort": self.port, "device": "connect"},
            "transport": {"pixel": {"enabled": beacon}, "chatlog": {"enabled": chatlog}}}), encoding="utf-8")
        cfg = config.load_config(self.cfg_file)
        self.opened: list[str] = []
        self.deck = Deck(port=0, heartbeat_sec=0.2, url_opener=self.opened.append, launcher=lambda a: None)
        self.deck.start()
        self.yt = RecordingYouTube()
        self.http = token_routes(FakeHTTP())
        self.app = Companion(cfg, config.paths_from_config(cfg), deck=self.deck, config_file=self.cfg_file,
                             provider_factory=lambda c, p, d: [self.yt] if config.provider_enabled(c, "youtube") else [],
                             transport_factory=None, opener=lambda t: None, game_running=lambda p: False,
                             spotify_http=self.http, wow_registry=no_registry, wow_default=str(tmp_path / "NoWoW"),
                             addon_source=make_fake_addon(tmp_path, "1.2.1"), autostart_winreg=FakeWinreg(),
                             notifier=tray.Notifier(), housekeeping_s=0.05)
        self.app.start()
        # the hidden player page is up, in Chrome (the YouTube box can open; music.youtube.player = chrome)
        self.player = SSE(self.deck.port, "player", key=self.deck.player_key)  # v1.2.1 ON-1: the launch key
        assert wait_for(lambda: self.deck.page_open)
        http_call(self.deck.port, "POST", "/status", {"state": "ready", "browser": "chrome"}, token=self.deck.token)
        self.app.push_game_data(flush=True)
        assert self.data()["appVersion"] == "1.2.1"
        # the game starts with the app's NeedleDropper_Companion\Data.lua loaded
        settings = {"chatlog": chatlog, "beacon": beacon, "cooldownMin": 0, "historyMode": "fresh",
                    "beaconCellPx": 3}
        self.h = self._game(settings=settings)
        self.clock = FakeClock()
        self.pix = self.chat = None
        if beacon:
            self.screen = GameScreen(self.h)
            self.pix = PixelTransport({"enabled": True, "method": "screen", "hz": 4}, SimpleNamespace(game_exe=None),
                                      self.app.submit, backend=self.screen, clock=self.clock)
            self.app.hook_baseline(self.pix)
        if chatlog:
            self.chat_path = wow / "_forever_" / "Logs" / "WoWChatLog.txt"
            self.chat_path.parent.mkdir(parents=True)
            self.chat_path.write_text("", encoding="utf-8")
            self.chat_written = 0
            self.chat = ChatLogTransport({"enabled": True, "pollMs": 20}, SimpleNamespace(chat_log=self.chat_path),
                                         self.app.submit)
            self.chat.poll()

    def _game(self, **kw: Any) -> AddonHarness:
        text = self.data_text()
        h = AddonHarness(sets=[make_set(DROPS)], location=(1411, ""), before_load=lambda h: h.lua(text), **kw)
        # the game's time() and the app's freshness check read one clock, as on one PC
        self.app.music._wall = lambda h=h: float(h.lua("return time()"))
        return h

    def data_text(self) -> str:
        return (self.addons / gd.ADDON_FOLDER / gd.DATA_FILENAME).read_text(encoding="utf-8")

    def data(self) -> dict:
        return gd.read_companion_data(self.addons) or {}

    def raw_config(self) -> dict:
        return json.loads(self.cfg_file.read_text(encoding="utf-8"))

    def step(self, seconds: float, dt: float = 0.25) -> None:
        t = 0.0
        while t < seconds - 1e-9:
            self.h.advance(dt)
            self.clock.advance(dt)
            t += dt
            if self.pix is not None:
                self.pix.step()
            if self.chat is not None:
                new = self.h.chatlog_file()[self.chat_written:]
                if new:
                    self.chat_written += len(new)
                    with open(self.chat_path, "a", encoding="utf-8", newline="") as fh:
                        fh.write("".join(f"{STAMP}{line}\r\n" for line in new))
                self.chat.poll()
        assert self.app.music.wait_idle(5)

    def send(self, action: str, **opts: Any) -> None:
        """What the Settings row, the dialog's button and /needle music do: MusicServices.Send."""
        ev, copy = self.h.call("MusicServices.Send", action, dict(opts))[:2]
        assert ev is not None, f"the game refused {action}: {copy}"

    def reload(self) -> None:
        """/reload: the game reads the app's newest Data.lua."""
        self.h.logout()
        self.h = self._game(db=self.h.account_db(), char_db=self.h.char_db(),
                            epoch=int(self.h.lua("return time()")))

    def close(self) -> None:
        self.player.close()
        self.app.stop()
        self.deck.stop()


@pytest.fixture
def world(tmp_path, _needledropper_home):
    w = World(tmp_path, Path(_needledropper_home))
    try:
        yield w
    finally:
        w.close()


def music_word(h: AddonHarness) -> str:
    return str(h.call1("MusicServices.StatusWord"))


def test_choices_and_requests_travel_the_beacon_into_the_app_and_back_into_the_game(world):
    w = world
    w.step(2)
    assert w.pix.status()["baseline"]
    assert music_word(w.h) == "YouTube ready"                       # the app's first report, read at login

    # 1. A music choice in game: PREFS ride the steady HELLO; config.json follows
    w.h.set_setting("musicOrder", "youtube-spotify")
    w.h.set_setting("youtubeCorner", "bottomleft")
    w.step(3)
    raw = w.raw_config()
    assert raw["music"]["order"] == "youtube-spotify" and raw["music"]["fromGame"] is True
    assert raw["deck"]["playerCorner"] == "bottomleft"
    assert w.app.engine.music_order == "youtube-spotify" and w.deck.player_corner == "bottomleft"

    # 2. Connect Spotify with the Client ID typed in game: a CONFIG with ACTION + CLIENT_ID (one row)
    w.send("connect-spotify", clientId=CID)
    w.step(4)
    assert len(w.opened) == 1 and f"client_id={CID}" in w.opened[0]        # Spotify's page, built by the app
    assert w.raw_config()["spotify"]["clientId"] == CID
    approve(w.opened[0])                                                    # the player approves on Spotify
    assert wait_for(lambda: any(r["result"] == "connected" for r in w.app.music.recent()), timeout=8)

    # 3. Test YouTube: the real engine plays the test song on the (fake) YouTube provider
    w.step(6)                                                              # past the 5 s send gap in game
    w.send("test-youtube")
    w.step(4)
    assert ("play", "test-youtube", mc.TEST_YOUTUBE_ID) in w.yt.calls
    assert wait_for(lambda: any(r["action"] == "test-youtube" and r["result"] == "playing"
                                for r in w.app.music.recent()), timeout=5)
    gd.flush(w.addons)
    music = w.data()["music"]
    assert [(a["action"], a["result"]) for a in music["actions"]] == [("connect-spotify", "connected"),
                                                                       ("test-youtube", "playing")]
    assert music["spotify"]["clientId"] == CID and music["spotify"]["account"] == "Thrall"
    assert music["prefs"]["order"] == "youtube-spotify" and music["youtube"]["player"] == "chrome"

    # 4. /reload: the game reads the app's report
    w.reload()
    assert music_word(w.h) == "Spotify + YouTube ready"
    lua = w.h.from_lua(w.h.fn("Companion.Music")())
    assert lua["spotify"]["state"] == "connected" and lua["spotify"]["account"] == "Thrall"
    assert [a["result"] for a in lua["actions"]] == ["connected", "playing"]
    assert "bottom left" in lua["actions"][-1]["text"]


def test_the_same_request_over_the_chat_log_with_the_beacon_off(tmp_path, _needledropper_home):
    w = World(tmp_path, Path(_needledropper_home), beacon=False, chatlog=True)
    try:
        w.step(2)
        w.send("test-youtube")
        w.step(4)
        assert ("play", "test-youtube", mc.TEST_YOUTUBE_ID) in w.yt.calls
        frames = [p2.parse_chat_line(line) for line in w.h.chatlog_file() if line.startswith("NDROP2 ")]
        configs = [f for f in frames if f is not None and f.type == ET.CONFIG]
        assert configs and configs[-1].action.name == "test-youtube"
        assert len(p2.encode_frame(configs[-1])) <= p2.capacity(1)       # one strip row, even on the chat log
        assert wait_for(lambda: any(r["action"] == "test-youtube" and r["result"] == "playing"
                                    for r in w.app.music.recent()), timeout=5)
    finally:
        w.close()
