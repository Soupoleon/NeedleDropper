"""gamedata.py's ``music`` block (DESIGN-v1.2.1 3.10, lead decision D17): validation, rendering,
the coalesced writer (``set_music``, ``record_music_action``), field-by-field leniency when the file
is read back, and the cross-language check: the rendered file is read by the real Companion.lua."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from needledropper import gamedata as gd  # noqa: E402

CID = "0123456789abcdef0123456789abcdef"
MUSIC = {
    "spotify": {"clientId": CID, "redirectUri": "http://127.0.0.1:8321/callback", "account": "Ethan",
                "product": "premium", "state": "connected", "mode": "app"},
    "youtube": {"enabled": True, "player": "chrome", "signInOpened": 1762380000},
    "prefs": {"order": "youtube-spotify", "corner": "topleft", "fade": False, "resume": True, "fromGame": True},
    "actions": [{"id": 1762380123, "action": "connect-spotify", "result": "connected",
                 "text": "Spotify connected as Ethan (Premium).", "at": 1762380150}],
}


def addons(tmp_path: Path) -> Path:
    d = tmp_path / "Interface" / "AddOns"
    d.mkdir(parents=True)
    return d


class Sched:
    """A schedule that runs nothing by itself: the test flushes."""

    def __call__(self, delay, fn):
        class H:
            def cancel(self):
                pass
        return H()


# --------------------------------------------------------------- validation --

def test_the_d17_values_match_the_app_modules():
    from needledropper import config, protocol2
    from needledropper.providers import spotify

    assert gd.MUSIC_SPOTIFY_STATES == spotify.MUSIC_STATES
    assert gd.MUSIC_SPOTIFY_MODES == config.SPOTIFY_DEVICE_MODES
    assert gd.MUSIC_ORDERS == config.MUSIC_ORDERS == protocol2.PREFS_ORDERS
    assert gd.MUSIC_CORNERS == config.PLAYER_CORNERS == protocol2.PREFS_CORNERS
    assert gd.MUSIC_ACTIONS == protocol2.ACTIONS
    assert gd.YOUTUBE_PLAYERS == ("chrome", "edge", "none")
    # status.spotify keeps its four v1.2 values (older readers stay valid)
    assert gd.SPOTIFY_STATES == ("connected", "not-connected", "premium-required", "error")
    assert {spotify.legacy_status(s) for s in gd.MUSIC_SPOTIFY_STATES} == set(gd.SPOTIFY_STATES)


def test_a_full_block_round_trips():
    out = gd.normalize_companion_data({"music": MUSIC}, now=0)
    assert out["music"] == MUSIC
    text = gd.render_data_lua({"music": MUSIC}, now=0)
    assert "music = {" in text and CID in text


@pytest.mark.parametrize("part, field, bad", [
    ("spotify", "clientId", "0123456789ABCDEF0123456789ABCDEF"),     # 32 *lowercase* hex
    ("spotify", "clientId", "abc123"),
    ("spotify", "redirectUri", "http://localhost:8321/callback"),
    ("spotify", "product", "Premium!"),
    ("spotify", "state", "stuck-forever"),
    ("spotify", "mode", "desktop"),
    ("youtube", "enabled", "yes"),
    ("youtube", "player", "default"),                                 # 1.2.1 never plays in the default browser
    ("youtube", "signInOpened", -1),
    ("prefs", "order", "loudest"),
    ("prefs", "corner", "center"),
    ("prefs", "fade", 1),
])
def test_wrong_values_are_refused(part, field, bad):
    with pytest.raises(gd.GameDataError, match=f"music.{part}.{field}"):
        gd.normalize_companion_data({"music": {part: {field: bad}}})


def test_every_d17_state_mode_and_player_is_accepted():
    for state in gd.MUSIC_SPOTIFY_STATES:
        for mode in gd.MUSIC_SPOTIFY_MODES:
            out = gd.normalize_companion_data({"music": {"spotify": {"state": state, "mode": mode}}})
            assert out["music"]["spotify"] == {"state": state, "mode": mode}
    for player in gd.YOUTUBE_PLAYERS:
        assert gd.normalize_companion_data({"music": {"youtube": {"player": player}}})["music"]["youtube"] == \
            {"player": player}


def test_unknown_fields_are_refused_and_empty_blocks_left_out():
    with pytest.raises(gd.GameDataError):
        gd.normalize_companion_data({"music": {"tokens": {}}})
    with pytest.raises(gd.GameDataError):
        gd.normalize_companion_data({"music": {"spotify": {"refreshToken": "x"}}})
    assert "music" not in gd.normalize_companion_data({"music": {}})
    assert "music" not in gd.normalize_companion_data({"music": {"spotify": {}, "actions": []}})


def test_text_is_cleaned_and_cut_and_actions_are_capped():
    out = gd.normalize_companion_data({"music": {
        "spotify": {"account": "Th\x01rall\n" + "x" * 100},
        "actions": [{"id": i, "action": "test-spotify", "result": "playing", "text": "t" * 400, "at": i}
                    for i in range(12)]}})
    assert out["music"]["spotify"]["account"] == ("Thrall" + "x" * 100)[:64]
    acts = out["music"]["actions"]
    assert [a["id"] for a in acts] == list(range(4, 12)) and len(acts[0]["text"]) == 300


def test_an_action_with_the_same_id_and_name_replaces_the_old_one():
    first = {"id": 7, "action": "connect-spotify", "result": "opened", "text": "Approve...", "at": 1}
    final = {"id": 7, "action": "connect-spotify", "result": "connected", "text": "Connected.", "at": 9}
    other = {"id": 7, "action": "test-spotify", "result": "playing", "at": 3}
    out = gd.normalize_companion_data({"music": {"actions": [first, other, final]}})
    assert out["music"]["actions"] == [other, final]


@pytest.mark.parametrize("bad", [
    {"id": 2 ** 32, "action": "test-spotify", "result": "playing", "at": 0},
    {"id": 1, "action": "dance", "result": "playing", "at": 0},
    {"id": 1, "action": "test-spotify", "result": "Playing!", "at": 0},
    {"id": 1, "action": "test-spotify", "result": "playing"},
])
def test_bad_actions_are_refused(bad):
    with pytest.raises(gd.GameDataError):
        gd.normalize_companion_data({"music": {"actions": [bad]}})


# ------------------------------------------------------------------ writer --

def test_set_music_merges_per_part_and_none_removes_a_field(tmp_path):
    root = addons(tmp_path)
    w = gd.CompanionDataWriter(root, schedule=Sched(), now=lambda: 100)
    w.set_music(spotify={"clientId": CID, "account": "Ethan", "state": "connected", "mode": "app"},
                youtube={"enabled": True, "player": "edge"})
    w.set_music(youtube={"signInOpened": 1762380000})
    w.flush()
    w.set_music(spotify={"account": None, "product": None, "state": "needs-relogin"})
    w.flush()
    music = gd.read_companion_data(root)["music"]
    assert music["spotify"] == {"clientId": CID, "state": "needs-relogin", "mode": "app"}
    assert music["youtube"] == {"enabled": True, "player": "edge", "signInOpened": 1762380000}
    with pytest.raises(gd.GameDataError):
        w.set_music(prefs={"order": "loudest"})
    with pytest.raises(gd.GameDataError):
        gd.set_music(root, tracks={})


def test_record_music_action_keeps_the_newest_eight_and_replaces_by_id(tmp_path):
    root = addons(tmp_path)
    w = gd.CompanionDataWriter(root, schedule=Sched())
    for i in range(10):
        w.record_music_action({"id": 100 + i, "action": "test-youtube", "result": "playing", "text": "ok", "at": i})
    w.record_music_action({"id": 109, "action": "test-youtube", "result": "failed", "text": "no", "at": 20})
    w.flush()
    acts = gd.read_companion_data(root)["music"]["actions"]
    assert [a["id"] for a in acts] == list(range(102, 110)) and acts[-1]["result"] == "failed"


def test_other_writes_keep_the_music_block_and_a_damaged_field_is_dropped_alone(tmp_path):
    root = addons(tmp_path)
    gd.write_companion_data(root, {"music": MUSIC}, now=5)
    path = root / gd.ADDON_FOLDER / gd.DATA_FILENAME
    text = path.read_text(encoding="utf-8").replace('player = "chrome"', 'player = "netscape"')
    path.write_text(text, encoding="utf-8")
    w = gd.CompanionDataWriter(root, schedule=Sched())
    w.record_track("q:toto\x1fafrica", {"notFound": True})
    w.set_status(spotify="connected")
    w.flush()
    music = gd.read_companion_data(root)["music"]
    assert music["spotify"] == MUSIC["spotify"] and music["prefs"] == MUSIC["prefs"]
    assert music["youtube"] == {"enabled": True, "signInOpened": 1762380000}   # only the bad field went
    assert music["actions"] == MUSIC["actions"]


# ------------------------------------------------------- the addon reads it --

def test_the_real_companion_lua_reads_the_rendered_block(tmp_path):
    lua51 = pytest.importorskip("lupa.lua51")  # noqa: F841
    from addon_harness import AddonHarness

    text = gd.render_data_lua({"appVersion": "1.2.1", "status": {"spotify": "error", "youtube": True},
                               "music": {**MUSIC, "spotify": {**MUSIC["spotify"], "state": "needs-relogin"}}},
                              now=1762380200)
    h = AddonHarness(before_load=lambda h: h.lua(text))  # the file as the client loads it, before the addon
    C = h.ns.Companion
    assert C is not None and C.Music is not None, "Companion.lua has no Music() yet"
    music = h.from_lua(C.Music())
    assert music["spotify"]["state"] == "needs-relogin" and music["spotify"]["clientId"] == CID
    assert music["spotify"]["account"] == "Ethan" and music["spotify"]["mode"] == "app"
    assert music["youtube"] == {"enabled": True, "player": "chrome", "signInOpened": 1762380000}
    assert music["prefs"] == MUSIC["prefs"]
    assert music["actions"][0]["result"] == "connected" and music["actions"][0]["id"] == 1762380123
    assert h.from_lua(C.Status())["spotify"] == "error"            # the v1.2 field keeps its four values
