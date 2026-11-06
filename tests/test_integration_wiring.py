"""Cross-component wiring: the CLI builds the real provider/transport classes with the
constructor shapes they actually have, status lines use the providers' own reasons, and a
``/needle test`` frame flows through the engine and every real provider without touching the
network (v1.2: the drop id ``test`` carries no song, so nothing is played or searched; an old
v1 addon's test FIRE is reported as outdated). No map is involved anywhere (mapgen was retired)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from needledropper import cli, config, protocol2  # noqa: E402
from needledropper.engine import Engine  # noqa: E402
from needledropper.models import TEST_DROP, Drop, Event, EventType, Flags  # noqa: E402
from needledropper.providers.local import LocalProvider  # noqa: E402
from needledropper.providers.spotify import SpotifyProvider, is_placeholder_test_drop  # noqa: E402
from needledropper.providers.youtube import YouTubeProvider  # noqa: E402
from needledropper.transport.chatlog import ChatLogTransport  # noqa: E402
from needledropper.transport.pixel import PixelTransport  # noqa: E402
from test_spotify_fakes import FakeHTTP, make_drop, save_tokens  # noqa: E402


class Deck:
    def __init__(self, connected: bool = False) -> None:
        self.connected = connected
        self.sent: list[dict] = []
        self.url = "http://127.0.0.1:8322/"

    def send(self, cmd: dict) -> int:
        self.sent.append(dict(cmd))
        return len(self.sent)


@pytest.fixture
def cfg_paths(tmp_path, _needledropper_home):
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {"wowRoot": str(tmp_path / "WoW")})
    return cfg, config.paths_from_config(cfg)


def test_cli_builds_every_real_provider_in_configured_order(cfg_paths):
    cfg, paths = cfg_paths
    said: list[str] = []
    deck = Deck()
    provs = cli.build_providers(cfg, paths, deck, out=said.append)
    assert said == []
    assert [type(p) for p in provs] == [SpotifyProvider, LocalProvider, YouTubeProvider]
    assert [p.name for p in provs] == list(config.KNOWN_PROVIDERS) == cfg["providers"]
    assert all(getattr(p, "deck", deck) is deck for p in provs)
    for p in provs:
        p.close()


def test_cli_builds_both_real_transports_without_starting_them(cfg_paths):
    cfg, paths = cfg_paths
    said: list[str] = []
    diag: list[str] = []
    got: list = []
    ts = cli.build_transports(cfg, paths, lambda ev, name: got.append((ev, name)), diag=diag.append,
                              out=said.append)
    assert said == [] and got == []
    assert [type(t) for t in ts] == [PixelTransport, ChatLogTransport]
    pix, chat = ts
    # v1.2 capture region: the client area's top-left 512 x 48 px (DESIGN-v1.2 2.3)
    assert (pix.name, pix.hz, pix.region_w, pix.region_h, pix.method_cfg) == ("pixel", 4.0, 512, 48, "auto")
    assert chat.name == "chatlog" and chat.path == paths.chat_log and chat.poll_s == pytest.approx(0.25)
    assert pix.diag is chat.diag and pix.diag is not None
    assert pix.status()["running"] is False and chat.status()["running"] is False


def test_status_lines_use_the_providers_own_reasons(cfg_paths):
    cfg, paths = cfg_paths
    sp = SpotifyProvider({"clientId": ""}, paths, http=FakeHTTP())
    assert "spotify.clientId" in cli._provider_line(sp)  # not "not logged in": there is no app yet
    sp2 = SpotifyProvider({"clientId": "cid"}, paths, http=FakeHTTP())
    assert "spotify-login" in cli._provider_line(sp2)
    assert cli._provider_line(LocalProvider({}, paths, Deck(connected=False))) == \
        "local (plays in an open player window)"
    assert cli._provider_line(YouTubeProvider({}, paths, Deck(connected=True))) == "youtube (ready)"


def test_spotify_never_searches_for_the_placeholder_test_drop(tmp_path):
    save_tokens(tmp_path)
    http = FakeHTTP()
    # connect mode: app mode would also need the in-app player (no deck here)
    sp = SpotifyProvider({"clientId": "cid", "device": "connect"}, type("P", (), {"app_dir": tmp_path})(), http=http)
    assert sp.available()
    assert is_placeholder_test_drop(TEST_DROP)
    assert not sp.can_play(TEST_DROP)
    sp.prepare(TEST_DROP)
    assert http.calls == []
    # a test drop that names a real song (Test on a drop in game, `test-drop "Artist - Title"`) is searched
    real_test = Drop(n=0, id="test", kind="test", title="Ludens", artist="Bring Me The Horizon")
    assert not is_placeholder_test_drop(real_test) and sp.can_play(real_test)
    assert sp.can_play(Drop(n=0, id="test", kind="test", title="", artist="", spotify="spotify:track:" + "a" * 22))
    assert sp.can_play(make_drop(spotify=None))  # ordinary drops: search by title + artist


def _real_providers(tmp_path, home):
    save_tokens(Path(home))
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {"wowRoot": str(tmp_path / "WoW"), "spotify": {"clientId": "cid"}})
    paths = config.paths_from_config(cfg)
    deck = Deck(connected=False)
    provs = cli.build_providers(cfg, paths, deck)
    assert isinstance(provs[0], SpotifyProvider)
    http = FakeHTTP()
    # same config, but connect mode (this fake deck has no in-app player); scripted HTTP, no network
    provs[0] = SpotifyProvider({**cfg["spotify"], "device": "connect"}, paths, http=http)
    assert provs[0].available()
    return provs, deck, http


def test_needle_test_frame_plays_nothing_and_only_checks_the_link(tmp_path, _needledropper_home):
    """/needle test (v1.2) -> FIRE drop id "test" + TEST with no song, over the chat log: the engine
    plays and searches nothing (no network), and the Connect page's link check ticks."""
    provs, deck, http = _real_providers(tmp_path, _needledropper_home)
    logs: list[str] = []
    links: list[str] = []
    engine = Engine(provs, deck, log=logs.append, on_link_check=lambda fr, t: links.append(t))
    fr = protocol2.build_frame(EventType.FIRE, seq=9, flags=int(Flags.TEST | Flags.DROP_ACTIVE), drop_id="test",
                               label="NeedleDropper test")
    engine.handle(protocol2.parse_chat_line(protocol2.format_chat_line(fr)), "chatlog")
    assert http.calls == [] and deck.sent == [] and not engine.is_playing
    assert links == ["chatlog"] and any("test signal" in ln for ln in logs)
    assert engine.status()["addon"]["protocol"] == 2
    engine.stop()


def test_an_old_addons_test_fire_is_reported_as_outdated_without_any_network(tmp_path, _needledropper_home):
    """A 1.1 addon's v1 test FIRE (n 0): no map exists any more, so it is not played; the link check
    still ticks and the shell learns that the addon is outdated."""
    provs, deck, http = _real_providers(tmp_path, _needledropper_home)
    logs: list[str] = []
    links: list[str] = []
    engine = Engine(provs, deck, log=logs.append, on_link_check=lambda fr, t: links.append(t))
    engine.handle(Event(EventType.FIRE, 9, 0, 48207, Flags.TEST | Flags.DROP_ACTIVE, "test"), "chatlog")
    assert http.calls == [] and deck.sent == [] and not engine.is_playing
    assert links == ["chatlog"] and any("Update the NeedleDropper addon" in ln for ln in logs)
    assert engine.status()["addon"]["outdated"] is True
    engine.stop()
