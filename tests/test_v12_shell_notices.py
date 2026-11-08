"""Shell notifications added in the v1.2 integration: the engine's notices reach the tray with the
song as the drop spells it, a stuck Spotify desktop app is reported (real-client finding #4), and a
Spotify search failure caused by Spotify itself being unusable re-checks Spotify at once."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import make_shell  # noqa: E402
from test_v12_engine_flows import FakeSpotify, Rig  # noqa: E402

from needledropper import tray  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402
from needledropper.providers.spotify import STUCK_MESSAGE  # noqa: E402


@pytest.fixture
def shell(tmp_path, _needledropper_home):
    s = make_shell(tmp_path, Path(_needledropper_home))
    try:
        yield s
    finally:
        s.close()


def test_not_found_names_the_song_as_the_drop_spells_it(shell):
    r = Rig(spotify=FakeSpotify(matches={}))
    r.send(ET.ARM, drop_id="zone:1429", artist="Low Roar", title="I'll Keep Coming", label="Elwynn Forest")
    change = r.changes("notFound")[0]
    assert (change["artist"], change["title"], change["label"]) == ("Low Roar", "I'll Keep Coming", "Elwynn Forest")
    shell.engines.current.on_status({"running": True, "change": change})
    note = [n for n in shell.app.notifier.history if n.kind == tray.NOTE_NOT_FOUND][-1]
    assert "No match for Low Roar – I'll Keep Coming (Elwynn Forest) on Spotify" in note.text


def test_a_stuck_spotify_app_is_reported_once_per_half_hour(tmp_path, _needledropper_home):
    # connect mode (the desktop app): 1.2.1 plays Spotify in the app by default, which words it otherwise
    shell = make_shell(tmp_path, Path(_needledropper_home), cfg_extra={"spotify": {"device": "connect"}})
    try:
        _stuck_spotify_app_reported_once(shell)
    finally:
        shell.close()


def test_a_stuck_in_app_spotify_names_no_spotify_app(shell):
    on_status = shell.engines.current.on_status
    on_status({"running": True, "change": {"kind": "spotifyStuck", "provider": "spotify", "artist": "Toto",
                                           "title": "Africa", "detail": "no progress within 3 s"}})
    note = [n for n in shell.app.notifier.history if n.kind == tray.NOTE_SPOTIFY_STUCK][-1]
    assert note.title == "Spotify didn't start" and "NeedleDropper's player" in note.text
    assert "restart the Spotify app" not in note.text and "YouTube when allowed by your music settings" in note.text


def _stuck_spotify_app_reported_once(shell):
    on_status = shell.engines.current.on_status
    change = {"kind": "spotifyStuck", "provider": "spotify", "artist": "Toto", "title": "Africa",
              "detail": STUCK_MESSAGE}
    on_status({"running": True, "change": change})
    on_status({"running": True, "change": dict(change, title="Rosanna")})  # the next drop: not again so soon
    notes = [n for n in shell.app.notifier.history if n.kind == tray.NOTE_SPOTIFY_STUCK]
    assert len(notes) == 1
    assert notes[0].title == "Spotify isn't starting songs" and STUCK_MESSAGE in notes[0].text
    assert "Toto – Africa" in notes[0].text


def test_service_only_failure_does_not_claim_youtube_was_tried(shell):
    shell.engines.current.on_status({"change": {"kind": "spotifyStuck", "provider": "spotify",
                                               "test": True, "dropId": "test-spotify"}})
    note = [n for n in shell.app.notifier.history if n.kind == tray.NOTE_SPOTIFY_STUCK][-1]
    assert "YouTube wasn't tried; Test Spotify checks only Spotify." in note.text


def test_spotify_only_mode_does_not_promise_youtube_playback(tmp_path, _needledropper_home):
    shell = make_shell(tmp_path, Path(_needledropper_home),
                       cfg_extra={"spotify": {"device": "connect"}, "music": {"order": "spotify"}})
    try:
        shell.engines.current.on_status({"change": {"kind": "spotifyStuck", "provider": "spotify",
                                                   "test": True, "dropId": "test-spotify"}})
        note = [n for n in shell.app.notifier.history if n.kind == tray.NOTE_SPOTIFY_STUCK][-1]
        assert "YouTube when allowed by your music settings" in note.text
        assert "your needle drops play on YouTube" not in note.text
        assert "YouTube wasn't tried" in note.text
    finally:
        shell.close()


def test_repeated_old_player_pages_show_one_actionable_notice(shell):
    shell.deck._note_stale_player()
    shell.deck._note_stale_player()
    shell.deck._note_stale_player()
    notes = [n for n in shell.app.notifier.history if n.title == "Old player window"]
    assert len(notes) == 1 and "close it" in notes[0].text


def test_search_failed_because_spotify_is_unusable_rechecks_spotify_now(shell):
    shell.app._next_spotify_watch = 10 ** 9  # the 30 s check is far away
    shell.app._hk_wake.clear()
    shell.engines.current.on_status({"running": True, "change": {"kind": "searchFailed", "key": "q:a\x1fb",
                                                                 "detail": "unavailable: revoked",
                                                                 "unavailable": True}})
    assert shell.app._next_spotify_watch == 0.0 and shell.app._hk_wake.is_set()
    shell.app._next_spotify_watch = 10 ** 9
    shell.app._hk_wake.clear()
    shell.engines.current.on_status({"running": True, "change": {"kind": "searchFailed", "key": "q:a\x1fb",
                                                                 "detail": "timed out", "unavailable": False}})
    assert shell.app._next_spotify_watch == 10 ** 9      # a plain network hiccup changes nothing
