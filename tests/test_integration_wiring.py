"""Cross-component wiring: the CLI builds the real provider/transport classes with the
constructor shapes they actually have, status lines use the providers' own reasons, and a
``/needle test`` FIRE flows through the engine and every real provider without touching the
network (the built-in test card names no real song, so Spotify must not search for it)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from needledropper import cli, config, mapgen  # noqa: E402
from needledropper.engine import Engine  # noqa: E402
from needledropper.models import TEST_DROP, Event, EventType, Flags  # noqa: E402
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
    assert (pix.name, pix.hz, pix.region_w, pix.region_h, pix.method_cfg) == ("pixel", 4.0, 256, 32, "auto")
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
    sp = SpotifyProvider({"clientId": "cid"}, type("P", (), {"app_dir": tmp_path})(), http=http)
    assert sp.available()
    assert is_placeholder_test_drop(TEST_DROP)
    assert not sp.can_play(TEST_DROP)
    sp.prepare(TEST_DROP)
    assert http.calls == []
    # a test track configured by the user (config "testDrop") is still played / searched
    cfg_test = mapgen.configured_test_drop({"testDrop": {"title": "Ludens", "artist": "Bring Me The Horizon"}})
    assert not is_placeholder_test_drop(cfg_test) and sp.can_play(cfg_test)
    assert sp.can_play(mapgen.configured_test_drop({"testDrop": {"spotify": "spotify:track:" + "a" * 22}}))
    assert sp.can_play(make_drop(spotify=None))  # ordinary drops: search by title + artist


def test_needle_test_fire_shows_the_card_without_any_network(tmp_path, _needledropper_home):
    """/needle test -> FIRE n=0 TEST: every configured provider is consulted, none can play the
    built-in card (Spotify skips it, no player window is open, it has no file or video) -> card only."""
    save_tokens(Path(_needledropper_home))
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {"wowRoot": str(tmp_path / "WoW"), "spotify": {"clientId": "cid"}})
    paths = config.paths_from_config(cfg)
    deck = Deck(connected=False)
    provs = cli.build_providers(cfg, paths, deck)
    assert isinstance(provs[0], SpotifyProvider)
    http = FakeHTTP()
    provs[0] = SpotifyProvider(cfg["spotify"], paths, http=http)  # same config; scripted HTTP, no network
    assert provs[0].available()
    logs: list[str] = []
    engine = Engine(provs, mapgen.MapRegistry(paths), deck, log=logs.append)
    engine.handle(Event(EventType.FIRE, 9, 0, 48207, Flags.TEST | Flags.DROP_ACTIVE, "test"), "chatlog")
    assert http.calls == []
    shows = [c for c in deck.sent if c["cmd"] == "show"]
    assert shows and shows[-1]["test"] is True and shows[-1]["label"] == TEST_DROP.label
    assert shows[-1]["hint"] == "No playable source for this drop"
    assert engine.is_playing and engine.status()["playing"]["provider"] is None
    assert any("no provider could play test" in ln for ln in logs)
    engine.stop()
