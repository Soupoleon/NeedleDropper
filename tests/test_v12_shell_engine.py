"""How the shell builds and listens to the engine (CONTRACTS-v1.2 3.2):
``Engine(providers, deck, *, clock, fade_in_ms, fade_out_ms, log, on_link_check, on_status)``.
``construct_engine`` passes only what the engine declares (a pre-cutover engine still starts);
``on_status`` notices become the "song not found on Spotify" / "YouTube video can't be embedded" /
"Spotify sign-in expired" notifications; the deck's player reports catch YouTube's embedding
errors on their own."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import http_call, make_shell, wait_for  # noqa: E402

from needledropper import tray  # noqa: E402
from needledropper.panel_api import construct_engine, parse_engine_notice  # noqa: E402


class V12Engine:
    def __init__(self, providers, deck, *, clock=None, fade_in_ms=0, fade_out_ms=0, log=None, on_link_check=None,
                 on_status=None):
        self.args = (providers, deck)
        self.kw = dict(fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms, on_link_check=on_link_check,
                       on_status=on_status)


class V11Engine:
    def __init__(self, providers, registry, deck=None, *, clock=None, fade_in_ms=0, fade_out_ms=0, log=None):
        self.args = (providers, registry, deck)
        self.kw = dict(fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms)


class KwargsEngine:
    def __init__(self, providers, deck, **kw):
        self.args, self.kw = (providers, deck), kw


class CompatEngine:
    """C1's shape: the v1.2 call, with the legacy registry keyword-only."""

    def __init__(self, providers, deck=None, *legacy, registry=None, fade_in_ms=0, on_link_check=None,
                 addons_dir=None):
        self.args, self.kw = (providers, deck, legacy, registry), dict(fade_in_ms=fade_in_ms, addons_dir=addons_dir,
                                                                       on_link_check=on_link_check)


def test_construct_engine_follows_the_contract():
    cb = object()
    kw = {"fade_in_ms": 1, "fade_out_ms": 2, "log": None, "on_link_check": cb, "on_status": cb}
    e = construct_engine(V12Engine, ["p"], "deck", kw)
    assert e.args == (["p"], "deck") and e.kw["on_link_check"] is cb and e.kw["fade_out_ms"] == 2
    old = construct_engine(V11Engine, ["p"], "deck", kw)  # before the cutover: no registry, no callbacks
    assert old.args == (["p"], None, "deck") and old.kw == {"fade_in_ms": 1, "fade_out_ms": 2}
    loose = construct_engine(KwargsEngine, ["p"], "deck", kw)
    assert loose.kw == kw
    compat = construct_engine(CompatEngine, ["p"], "deck", {**kw, "addons_dir": "AddOns"})
    assert compat.args == (["p"], "deck", (), None) and compat.kw == {"fade_in_ms": 1, "addons_dir": "AddOns",
                                                                       "on_link_check": cb}


def test_the_real_engine_takes_the_shell_callbacks(tmp_path):
    from needledropper.engine import Engine

    params = inspect.signature(Engine).parameters
    if "on_link_check" not in params:
        pytest.skip("engine.Engine is not on the v1.2 contract yet (C1's cutover); construct_engine copes")
    positional = [n for n, p in params.items() if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    assert positional[:2] == ["providers", "deck"]
    assert {"on_link_check", "on_status", "fade_in_ms", "fade_out_ms"} <= set(params)
    link, status = object(), object()
    engine = construct_engine(Engine, [], "the-deck", {"on_link_check": link, "on_status": status,
                                                       "fade_in_ms": 10, "fade_out_ms": 20,
                                                       "addons_dir": tmp_path})
    assert engine.deck == "the-deck" and engine.on_link_check is link and engine.on_status is status
    assert engine.fade_in_ms == 10 and getattr(engine, "registry", None) is None  # never started: no thread


@pytest.mark.parametrize("args, kwargs, kind", [
    (("spotify-not-found", {"artist": "Toto", "title": "Africa"}), {}, tray.NOTE_NOT_FOUND),
    (({"notice": "spotify-not-found", "artist": "Toto"},), {}, tray.NOTE_NOT_FOUND),
    (({"notice": {"kind": "youtube-not-embeddable", "youtube": "abcdefghijk"}},), {}, tray.NOTE_NOT_EMBEDDABLE),
    (({"event": "notEmbeddable"},), {}, tray.NOTE_NOT_EMBEDDABLE),
    ((), {"kind": "spotify_auth_expired"}, tray.NOTE_SPOTIFY_EXPIRED),
    (({"playing": {"title": "x"}, "running": True},), {}, None),  # a plain status snapshot
    (("playing",), {}, None),
    # the engine's own form: Engine.status() plus a "change" entry
    (({"running": True, "change": {"kind": "notFound", "key": "q:toto\x1fafrica"}},), {}, tray.NOTE_NOT_FOUND),
    (({"running": True, "change": {"kind": "notEmbeddable", "youtube": "abcdefghijk", "code": 150}},), {},
     tray.NOTE_NOT_EMBEDDABLE),
    (({"running": True, "change": {"kind": "playing"}},), {}, None),
    (({"running": True, "change": {"kind": "match", "spotify": "x"}},), {}, None),  # matches are not notified
])
def test_parse_engine_notice(args, kwargs, kind):
    assert parse_engine_notice(args, kwargs)[0] == kind


@pytest.fixture
def shell(tmp_path, _needledropper_home):
    ns = make_shell(tmp_path, Path(_needledropper_home))
    try:
        yield ns
    finally:
        ns.close()


def test_engine_notices_become_notifications_with_cooldowns(shell):
    on_status = shell.engines.current.on_status
    assert on_status == shell.app.on_engine_status
    on_status("spotify-not-found", {"artist": "Toto", "title": "Africa", "label": "Goldshire"})
    on_status("spotify-not-found", {"artist": "Toto", "title": "Africa"})  # same song again: quiet
    on_status({"notice": "spotify-not-found", "query": "Low Roar\x1fI'll Keep Coming"})
    notes = [n for n in shell.app.notifier.history if n.kind == tray.NOTE_NOT_FOUND]
    assert len(notes) == 2
    assert notes[0].title == "Song not found on Spotify" and "Toto – Africa (Goldshire)" in notes[0].text
    assert "/needle" in notes[0].text and "Low Roar – I'll Keep Coming" in notes[1].text
    on_status({"notice": "youtube-not-embeddable", "youtube": "abcdefghijk", "artist": "A", "title": "B",
               "code": 150})
    yt = [n for n in shell.app.notifier.history if n.kind == tray.NOTE_NOT_EMBEDDABLE]
    assert len(yt) == 1 and "A – B" in yt[0].text and "outside YouTube" in yt[0].text
    on_status({"running": True, "playing": None})  # plain status: no notification
    assert len(shell.app.notifier.history) == 3


def test_the_engines_own_change_notices(shell):
    on_status = shell.engines.current.on_status
    on_status({"running": True, "playing": None, "change": {"kind": "notFound", "key": "q:toto\x1fafrica"}})
    on_status({"running": True, "change": {"kind": "notFound", "key": "sp:4uLU6hMCjMI75M1A2tKUQC"}})
    on_status({"running": True, "playing": {"id": "area:87", "artist": "Toto", "title": "Africa"},
               "change": {"kind": "notEmbeddable", "youtube": "abcdefghijk", "code": 101, "dropId": "area:87",
                          "label": "Goldshire"}})
    on_status({"running": True, "change": {"kind": "playing"}})
    texts = [(n.kind, n.text) for n in shell.app.notifier.history]
    assert texts[0][0] == tray.NOTE_NOT_FOUND and "No match for toto – africa on Spotify" in texts[0][1]
    assert "the Spotify track 4uLU6hMCjMI75M1A2tKUQC" in texts[1][1]
    assert texts[2][0] == tray.NOTE_NOT_EMBEDDABLE and "Toto – Africa" in texts[2][1]
    assert len(texts) == 3
    assert shell.engines.current.extra.get("addons_dir") == shell.app.paths.addons_dir  # tracks recorded there


def test_youtube_embedding_errors_from_the_player_are_notified(shell):
    deck = shell.deck
    deck.send({"cmd": "show", "dropId": "area:87", "label": "Goldshire", "title": "Africa", "artist": "Toto"})
    deck.send({"cmd": "play", "playId": "p1", "source": "youtube", "videoId": "abcdefghijk", "dropId": "area:87"})
    http_call(deck.port, "POST", "/status", {"state": "error", "playId": "p1", "code": 101}, token=deck.token)
    assert wait_for(lambda: tray.NOTE_NOT_EMBEDDABLE in shell.app.notifier.kinds())
    note = shell.app.notifier.history[-1]
    assert note.title == "YouTube video can't be played here" and "Toto – Africa" in note.text
    # the engine reporting the same video too: one notification, not two
    shell.app.on_engine_status({"notice": "youtube-not-embeddable", "youtube": "abcdefghijk"})
    assert shell.app.notifier.kinds().count(tray.NOTE_NOT_EMBEDDABLE) == 1
    # other player errors (a local file, autoplay, YouTube's "invalid id") are not this notification
    deck.send({"cmd": "play", "playId": "p2", "source": "local", "url": "u", "dropId": "zone:1"})
    http_call(deck.port, "POST", "/status", {"state": "error", "playId": "p2", "code": 150}, token=deck.token)
    deck.send({"cmd": "play", "playId": "p3", "source": "youtube", "videoId": "zzzzzzzzzzz", "dropId": "zone:2"})
    http_call(deck.port, "POST", "/status", {"state": "error", "playId": "p3", "code": 2}, token=deck.token)
    http_call(deck.port, "POST", "/status", {"state": "autoplay-blocked", "playId": "p3"}, token=deck.token)
    assert shell.app.notifier.kinds().count(tray.NOTE_NOT_EMBEDDABLE) == 1


def test_a_stale_title_card_never_names_the_wrong_song(shell):
    deck = shell.deck
    deck.send({"cmd": "show", "dropId": "zone:1", "label": "Old place", "title": "Old song", "artist": "Old"})
    deck.send({"cmd": "play", "playId": "p9", "source": "youtube", "videoId": "qqqqqqqqqqq", "dropId": "zone:2"})
    http_call(deck.port, "POST", "/status", {"state": "error", "playId": "p9", "code": 150}, token=deck.token)
    assert wait_for(lambda: tray.NOTE_NOT_EMBEDDABLE in shell.app.notifier.kinds())
    text = shell.app.notifier.history[-1].text
    assert "Old song" not in text and "qqqqqqqqqqq" in text
