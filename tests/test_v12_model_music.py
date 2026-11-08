"""v1.2.1 Music services, the model (DESIGN-v1.2.1 1.1, 1.4-1.6, 2.6, 2.9, 4.2, 4.3, 5.2 items 3 and 6;
decisions D9, D17): MusicServices.lua and Companion.lua's music block, run in the full addon.

* the four settings, NeedleDropperDB.music and its validation, prefsSet and the echo of the app's choices;
* PREFS on HELLO, the debounced PREFS-only CONFIG, and the CONFIG requests (MusicServices.Send) with their
  refusals, request bookkeeping and the "Last request" line;
* "Play music with": the stripping matrix against app versions, the preferred provider, canPlay and the
  zone-music duck;
* every frame MusicServices can build is one strip row;
* the status lines for every music.spotify.state (D17), the Settings row texts;
* /needle music. (The version agreement of D15 is tests/test_core_version.py.)
"""
from __future__ import annotations

import time

import pytest

from addon_harness import AddonHarness, make_set, toc_files
from needledropper import protocol2 as p2
from needledropper.models import EventType as ET, Flags

LOGIN = 1762365600                      # time() at login (SHIM.EPOCH)
CID = "0123456789abcdef0123456789abcdef"
CID2 = "fedcba9876543210fedcba9876543210"
REDIRECT = "http://127.0.0.1:8321/callback"
SONG_SP = {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "artist": "Low Roar",
           "title": "I'll Keep Coming", "spotify": "4uLU6hMCjMI75M1A2tKUQC", "youtube": "mlM66FzjpgM",
           "durationMs": 60000}
SET = make_set([SONG_SP])
DASH = "–"


def music(**over):
    m = {"spotify": {"clientId": CID, "redirectUri": REDIRECT, "account": "Ethan", "product": "premium",
                     "state": "connected", "mode": "app"},
         "youtube": {"enabled": True, "player": "chrome"},
         "prefs": {"order": "spotify-youtube", "corner": "topright", "fade": True, "resume": True, "fromGame": False},
         "actions": []}
    for k, v in over.items():
        m[k] = v
    return m


def app(version="1.2.1", written=LOGIN - 120, spotify="connected", youtube=True, block=None, **extra):
    d = {"schema": 1, "appVersion": version, "written": written,
         "status": {"spotify": spotify, "youtube": youtube, "transports": {"pixel": True, "chatlog": False}}}
    if block is not None:
        d["music"] = block
    d.update(extra)
    return d


def make(companion=None, **kw) -> AddonHarness:
    if companion is not None:
        kw["companion_data"] = companion
    kw.setdefault("sets", [SET])
    return AddonHarness(**kw)


def hm(t: int) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def encoded(h: AddonHarness, ev) -> bytes:
    """A Lua event table encoded by Protocol2.EncodeFrame (Lua)."""
    return bytes(h.to_py(h.ns.Protocol2.EncodeFrame(ev)))


def last_lua_event(h: AddonHarness):
    evs = h.ns.Events
    return evs[len(list(evs.values()))]


def configs(h: AddonHarness) -> list[dict]:
    return h.events_of(ET.CONFIG)


def ms(h: AddonHarness, name: str, *args):
    return h.call1("MusicServices." + name, *args)


# ------------------------------------------------------------ saved data --

def test_defaults_and_the_saved_music_table():
    h = make()
    s = h.settings()
    assert (s["musicOrder"], s["youtubeCorner"], s["musicFade"], s["resumeMyMusic"]) == \
        ("spotify-youtube", "topright", True, True)
    m = h.account_db()["music"]
    assert m["prefsSet"] is False and m.get("requests") in ([], {}) and "clientId" not in m
    keys = [e["key"] for e in h.from_lua(h.ns.SETTINGS)]
    i = keys.index("instanceDrops")
    assert keys[i + 1:i + 6] == ["musicOrder", "youtubeCorner", "musicFade", "musicVolume", "resumeMyMusic"]


def test_saved_music_table_is_validated_at_load():
    reqs = [{"action": "test-spotify", "at": 100 + i} for i in range(10)]
    reqs += [{"action": "hack", "at": 1}, {"action": "test-youtube", "at": -5}, {"action": "test-youtube", "at": 1.5},
             "x", {"action": "connect-spotify", "at": 500}]
    h = make(db={"music": {"prefsSet": "yes", "clientId": "0123456789ABCDEF0123456789ABCDEF", "requests": reqs}})
    m = h.account_db()["music"]
    assert m["prefsSet"] is False
    assert "clientId" not in m                       # upper case is not stored form: 32 lowercase hex only
    assert [r["at"] for r in m["requests"]] == [103, 104, 105, 106, 107, 108, 109, 500]
    h2 = make(db={"music": {"prefsSet": True, "clientId": CID, "requests": []}})
    assert h2.account_db()["music"]["clientId"] == CID and h2.account_db()["music"]["prefsSet"] is True


def test_settings_reach_slash_set_and_coercion():
    h = make()
    assert h.slash("set musicOrder YouTube")[-1].endswith("musicOrder = youtube")
    assert "must be one of: spotify-youtube, youtube-spotify, spotify, youtube, none" in h.slash("set musicOrder all")[-1]
    h.slash("set youtubeCorner bottomleft")
    h.slash("set musicFade off")
    s = h.settings()
    assert (s["musicOrder"], s["youtubeCorner"], s["musicFade"]) == ("youtube", "bottomleft", False)


# ----------------------------------------------------- PREFS and prefsSet --

def test_prefs_travel_only_once_a_music_choice_was_made_in_game():
    h = make(app(block=music()), settings={"chatlog": True})
    assert ms(h, "WirePrefs") is None and not ms(h, "PrefsSet")
    hello = h.events_of(ET.HELLO)[-1]
    assert "prefs" not in hello and h.beacon_status()["size"] == "96x3"   # the idle strip stays 96 x 3
    assert h.beacon_status().get("prefs") is None
    h.set_setting("musicOrder", "youtube-spotify")           # the Settings panel writes and announces
    assert ms(h, "PrefsSet") is True and h.account_db()["music"]["prefsSet"] is True
    want = {"order": "youtube-spotify", "corner": "topright", "fade": True, "resume": True, "volume": 100}
    assert h.beacon_status()["prefs"] == want
    drawn = h.beacon_decoded()                                # the HELLO on screen changed at once
    assert drawn.type == ET.HELLO and drawn.prefs == p2.Prefs("youtube-spotify", "topright", True, True, 100)
    assert h.beacon_status()["size"] == "192x3"
    assert configs(h) == []
    h.advance(0.9)
    h.set_setting("youtubeCorner", "topleft")                 # debounced: one CONFIG 1 s after the last change
    h.advance(0.9)
    assert configs(h) == []
    h.advance(0.2)
    cfg = configs(h)
    assert len(cfg) == 1 and "action" not in cfg[0] and "clientId" not in cfg[0]
    assert cfg[0]["prefs"] == {"order": "youtube-spotify", "corner": "topleft", "fade": True, "resume": True, "volume": 100}
    line = [f for f in h.chat_frames() if f.type == ET.CONFIG][-1]
    assert line.prefs == p2.Prefs("youtube-spotify", "topleft", True, True, 100) and line.action is None
    h.emit(ET.HELLO)
    assert h.events_of(ET.HELLO)[-1]["prefs"]["corner"] == "topleft"
    assert [f for f in h.chat_frames() if f.type == ET.HELLO][-1].prefs.corner == "topleft"


def test_a_settings_reset_counts_as_a_choice_and_sends_the_defaults():
    h = make(app(block=music()))
    h.slash("reset settings")
    assert ms(h, "PrefsSet") is True
    h.advance(1.1)
    assert configs(h)[-1]["prefs"] == {"order": "spotify-youtube", "corner": "topright", "fade": True, "resume": True, "volume": 100}


def test_the_echo_shows_the_apps_choices_until_the_player_sets_one():
    block = music(prefs={"order": "youtube", "corner": "bottomleft", "fade": False, "resume": False, "fromGame": True})
    h = make(app(block=block))
    s = h.settings()
    assert (s["musicOrder"], s["youtubeCorner"], s["musicFade"], s["resumeMyMusic"]) == \
        ("youtube", "bottomleft", False, False)
    assert ms(h, "PrefsSet") is False and configs(h) == []    # the echo is no choice: nothing is sent
    assert h.beacon_status().get("prefs") is None
    # once set in game, the game's values win at the next login
    h.set_setting("musicOrder", "spotify")
    h2 = h.reload()
    s = h2.settings()
    assert (s["musicOrder"], s["youtubeCorner"]) == ("spotify", "bottomleft")
    assert h2.events_of(ET.HELLO)[-1]["prefs"]["order"] == "spotify"


def test_unknown_echo_values_are_ignored():
    h = make(app(block=music(prefs={"order": "loud", "corner": "middle", "fade": "yes"})))
    s = h.settings()
    assert (s["musicOrder"], s["youtubeCorner"], s["musicFade"]) == ("spotify-youtube", "topright", True)


def test_music_volume_slider_echo_persistence_and_test_frames():
    h = make(app(block=music(prefs={"volume": 37})), settings={"chatlog": True})
    assert h.settings()["musicVolume"] == 37 and not ms(h, "PrefsSet")
    meta = h.ns.SETTINGS_BY_KEY["musicVolume"]
    assert (meta.min, meta.max, meta.step, meta.integer) == (0, 100, 1, True)
    assert h.ns.Options.FormatSliderValue(meta, 37) == "37%"
    assert "next drop or music test" in meta.tip
    h.set_setting("musicVolume", 0)
    assert h.beacon_decoded().prefs.volume == 0
    h.advance(1.1)
    assert configs(h)[-1]["prefs"]["volume"] == 0
    assert [f for f in h.chat_frames() if f.type == ET.CONFIG][-1].prefs.volume == 0
    h2 = h.reload(companion_data=app(block=music(prefs={"volume": 37})))
    assert h2.settings()["musicVolume"] == 0
    assert h2.beacon_decoded().prefs.volume == 0  # stale app echo cannot replace an explicit game choice
    h2.ns.MusicServices.Send("test-spotify")
    assert h2.events_of(ET.CONFIG)[-1]["prefs"]["volume"] == 0
    h2.slash("reset settings")
    assert h2.settings()["musicVolume"] == 100


@pytest.mark.parametrize("value", [-1, 101, 1.5, True, "50"])
def test_invalid_music_volume_echo_is_ignored(value):
    h = make(app(block=music(prefs={"volume": value})))
    assert h.settings()["musicVolume"] == 100


def test_the_prefs_config_is_skipped_for_an_app_that_cannot_read_it():
    h = make(app(version="1.2.0"))
    h.set_setting("musicFade", False)
    h.advance(1.5)
    assert configs(h) == []
    h = make()                                                # no app data: sent (it may be new)
    h.set_setting("musicFade", False)
    h.advance(1.5)
    assert len(configs(h)) == 1


# ------------------------------------------------------- Play music with --

@pytest.mark.parametrize("version", [None, "1.1.0", "1.2.0", "1.2.1", "1.10.0", "dev"])
@pytest.mark.parametrize("order", ["spotify-youtube", "youtube-spotify", "spotify", "youtube", "none"])
def test_stripping_matrix(version, order):
    data = app(version=version) if version else None
    h = make(data, settings={"musicOrder": order})
    supports = version in ("1.2.1", "1.10.0")
    for etype in (ET.ARM, ET.FIRE):
        ev = h.build_event(etype, SONG_SP)
        has = {k: k in ev for k in ("spotify", "youtube", "artist", "title")}
        if order == "none":
            want = {"spotify": False, "youtube": False, "artist": False, "title": False}
        elif order == "spotify" and not supports:
            want = {"spotify": True, "youtube": False, "artist": True, "title": True}
        elif order == "youtube" and not supports:
            want = {"spotify": False, "youtube": True, "artist": False, "title": False}
        else:
            want = {"spotify": True, "youtube": True, "artist": True, "title": True}
        assert has == want, (etype, version, order)
        assert ev["dropId"] == "zone:1429" and ev["label"] and ev["lengthMs"] == 60000   # the card keeps its place


def test_neither_never_strips_a_local_playback_frame_or_the_test_drop():
    h = make(settings={"musicOrder": "none"})
    ev = h.build_event(ET.FIRE, SONG_SP, int(Flags.LOCAL_PLAYBACK))
    assert ev["spotify"] and ev["youtube"] and ev["artist"]
    local = dict(SONG_SP, file="elwynn.mp3")
    # v1.2.1 review INGAME-5: under Neither the ARM of a drop with an in-game file is stripped too (the file
    # may not play at FIRE, and the app would merge this ARM into the stripped FIRE)
    assert "youtube" not in h.build_event(ET.ARM, local)
    h.slash("set localPlayback off")
    assert "youtube" not in h.build_event(ET.ARM, local)
    test = h.build_event(ET.FIRE, h.from_lua(h.ns.TEST_DROP))
    assert test["label"] == "NeedleDropper Test"


def test_neither_strips_a_window_test_of_a_real_drop():
    h = make(settings={"musicOrder": "none"})
    h.call("Director.PlayTest", SONG_SP)
    fire = h.events_of(ET.FIRE)[-1]
    assert fire["flags"] & int(Flags.TEST) and not any(k in fire for k in ("spotify", "youtube", "artist", "title"))


@pytest.mark.parametrize("order, spotify, want", [
    ("spotify-youtube", "connected", "spotify"), ("spotify-youtube", "not-connected", "youtube"),
    ("youtube-spotify", "connected", "youtube"), ("youtube", "connected", "youtube"),
    ("spotify", "not-connected", "spotify"), ("none", "connected", "youtube"),
])
def test_preferred_provider(order, spotify, want):
    h = make(app(spotify=spotify), settings={"musicOrder": order})
    assert h.call1("PreferredProvider") == want
    assert h.build_event(ET.FIRE, SONG_SP)["prefer"] == want


YT_ONLY = {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "youtube": "bbbbbbbbbbb"}
SP_ONLY = {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "artist": "A", "title": "T"}


@pytest.mark.parametrize("order, drop, data, can", [
    ("spotify-youtube", YT_ONLY, None, True),                 # no app data: assume it can
    ("spotify", YT_ONLY, None, False),                        # Spotify only, a YouTube-only drop
    ("youtube", SP_ONLY, None, False),
    ("none", SONG_SP, None, False),                           # Neither never fades the zone music
    ("spotify-youtube", SP_ONLY, "not-connected", False),     # Spotify not connected, no YouTube id
    ("spotify-youtube", SONG_SP, "not-connected", True),      # ...but YouTube can play it
    ("youtube-spotify", SP_ONLY, "connected", True),
])
def test_can_play_and_the_zone_music_duck(order, drop, data, can):
    h = make(app(spotify=data) if data else None, settings={"musicOrder": order, "duckWithoutApp": True})
    assert h.call1("MusicServices.Plan", drop)["canPlay"] is can
    assert h.call1("Director.ShouldDuck", drop, False) is can
    assert h.call1("Director.ShouldDuck", drop, True) is True  # an in-game file always fades it


def test_a_connect_sent_this_session_counts_as_spotify_ready():
    h = make(app(spotify="not-connected", block=music(spotify={"clientId": CID, "state": "not-connected"})))
    assert h.call1("MusicServices.Plan", SP_ONLY)["canPlay"] is False
    h.slash("music connect")
    assert h.call1("MusicServices.Plan", SP_ONLY)["canPlay"] is True


def test_neither_shows_the_card_and_keeps_the_zone_music():
    h = make(app(), settings={"musicOrder": "none", "historyMode": "fresh"})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    fire = h.events_of(ET.FIRE)[-1]
    assert not any(k in fire for k in ("spotify", "youtube", "artist", "title"))
    assert h.toast_texts()[0] == "E L W Y N N   F O R E S T"
    assert h.music() == []                                    # the zone music was never faded


def test_an_outdated_app_gets_the_update_hint_and_the_place_plays_again_later():
    """A 1.2.0 app reads no 1.2.1 frame: the drop shows its card, keeps the zone music, is marked "card"
    (it plays once a current app is there), and the one-time hint says to update, not to install."""
    h = make(app(version="1.2.0"), settings={"historyMode": "fresh"})
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    assert h.events_of(ET.FIRE) and h.music() == []
    assert h.char_db()["done"]["zone:1429"]["how"] == "card"
    hint = [line for line in h.prints() if "only the title card showed" in line]
    assert hint == ["NeedleDropper: only the title card showed: the NeedleDropper app on this PC is older than 1.2.1 "
                    "and can't read this addon's signals. Update it; places you visit before then play their song "
                    "once it is updated."]


# ------------------------------------------------------------- Client ID --

@pytest.mark.parametrize("text, norm, valid", [
    (CID, CID, True),
    (" 0123 4567 89ab cdef\t0123456789abcdef\n", CID, True),
    ("0123456789ABCDEF0123456789ABCDEF", CID, True),
    ("0123456789abcdef0123456789abcde", CID[:31], False),       # 31
    (CID + "0", CID + "0", False),                               # 33
    ("0123456789abcdefg123456789abcdef", "0123456789abcdefg123456789abcdef", False),
    ("", "", False),
    ("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", True),   # a secret looks the same
])
def test_client_id_normalisation(text, norm, valid):
    h = make()
    assert h.call("MusicServices.NormalizeClientId", text) == (norm, valid)


def test_client_id_storage():
    h = make()
    assert h.call1("MusicServices.SetClientId", "nope") is False
    assert h.call1("MusicServices.SetClientId", CID.upper()) is True
    assert h.account_db()["music"]["clientId"] == CID
    assert h.call1("MusicServices.SetClientId", "") is True and "clientId" not in h.account_db()["music"]


# --------------------------------------------------------------- requests --

def test_sending_is_refused_without_a_current_app():
    h = make()
    ev, why = h.call("MusicServices.Send", "test-spotify")
    assert ev is None and why == "Not sent: install the NeedleDropper app first (from the page you got NeedleDropper from)."
    assert h.prints()[-1] == "NeedleDropper: " + why and configs(h) == []
    h = make(app(version="1.2.0"))
    assert h.call("MusicServices.Send", "test-youtube")[1] == "Not sent: update the NeedleDropper app to 1.2.1 first."
    assert h.call1("MusicServices.CanSendActions") is False and configs(h) == []
    assert h.call1("MusicServices.Send", "make-coffee") is None


def test_a_request_is_one_config_frame_with_its_action():
    h = make(app(block=music()), settings={"chatlog": True})
    h.set_setting("musicOrder", "youtube")
    h.advance(1.5)                                             # the PREFS-only CONFIG goes first
    mark = h.mark()
    before = h.lua("return time()")
    ev, copy = h.call("MusicServices.Send", "test-spotify")
    assert copy == ("Sent (%s). In a few seconds you should hear 15 seconds of Toto %s Africa from the NeedleDropper "
                    "app's own Spotify player. If not, a Windows notification says why." % (hm(before), DASH))
    assert h.prints()[-1] == ("NeedleDropper: " + copy + " Nothing after 10 seconds? Check that the NeedleDropper app "
                              "is running (its tray icon), then press Send a test signal.")
    new = h.events_since(mark)
    assert [e["type"] for e in new] == [ET.CONFIG]
    raw = encoded(h, last_lua_event(h))
    fr = p2.decode_frame(raw)
    assert len(raw) == 26 and fr.type == ET.CONFIG and fr.action.name == "test-spotify"
    assert fr.action.issued_at == before and fr.prefs.order == "youtube" and fr.client_id is None
    assert not fr.has(Flags.TEST)
    assert h.account_db()["music"]["requests"][-1] == {"action": "test-spotify", "at": before}
    chat = [f for f in h.chat_frames() if f.type == ET.CONFIG and f.action is not None]
    assert chat and chat[-1].action.issued_at == before


def test_connect_and_disconnect_carry_the_client_id_and_never_prefs():
    h = make(app(block=music()), settings={"chatlog": True, "musicFade": False}, db={"music": {"prefsSet": True}})
    h.call("MusicServices.SetClientId", CID2)
    h.call("MusicServices.Send", "connect-spotify")
    fr = p2.decode_frame(encoded(h, last_lua_event(h)))
    assert fr.action.name == "connect-spotify" and fr.client_id == CID2 and fr.prefs is None
    h.advance(6)
    h.call("MusicServices.Send", "disconnect-spotify")
    raw = encoded(h, last_lua_event(h))
    fr = p2.decode_frame(raw)
    assert len(raw) == 35 and fr.client_id == CID and fr.prefs is None      # the app's active ID
    h.call("MusicServices.Send", "youtube-sign-in")
    fr = p2.decode_frame(encoded(h, last_lua_event(h)))
    assert fr.prefs == p2.Prefs("spotify-youtube", "topright", False, True, 100) and fr.client_id is None


def test_connect_needs_some_client_id_and_disconnect_the_apps():
    h = make(app(block=music(spotify={"state": "not-connected"})))
    ev, why = h.call("MusicServices.Send", "connect-spotify")
    assert ev is None and why == "Paste your Client ID first (step 3)."
    assert h.call("MusicServices.Send", "disconnect-spotify")[1] == "Disconnect it on the app's Connect page."
    # the app's own Client ID is enough for a connect: the frame then carries none
    h = make(app(block=music()))
    h.call("MusicServices.Send", "connect-spotify")
    fr = p2.decode_frame(encoded(h, last_lua_event(h)))
    assert fr.action.name == "connect-spotify" and fr.client_id is None


def test_the_same_request_waits_ten_seconds():
    # v1.2.1 review INGAME-8: the app's own limit (music_config.ACTION_INTERVAL_S = 10 s), not 5 s
    h = make(app(block=music()))
    assert h.call1("MusicServices.Send", "test-youtube") is not None
    h.advance(9.9)
    assert h.call("MusicServices.Send", "test-youtube")[1] == "Just sent: give it a moment."
    assert h.call1("MusicServices.Send", "test-spotify") is not None   # per request, not any send
    h.advance(0.2)
    assert h.call1("MusicServices.Send", "test-youtube") is not None
    assert len(configs(h)) == 3


def test_a_config_never_carries_test_even_during_a_test_drop():
    h = make(app(block=music()))
    h.slash("test")                                           # the test drop plays (TEST, DROP_ACTIVE)
    h.call("MusicServices.Send", "test-spotify")
    cfg = configs(h)[-1]
    assert not cfg["flags"] & int(Flags.TEST) and cfg["flags"] & int(Flags.DROP_ACTIVE)


def test_requests_keep_the_newest_eight():
    h = make(app(block=music()))
    for i in range(10):
        h.call("MusicServices.Send", ["test-spotify", "test-youtube"][i % 2])
        h.advance(6)
    reqs = h.account_db()["music"]["requests"]
    assert len(reqs) == 8 and reqs[0]["action"] == "test-spotify"


@pytest.mark.parametrize("case", ["matched", "late-report", "no-report"])
def test_last_request_line_after_a_reload(case):
    h = make(app(block=music()))
    assert h.call1("MusicServices.LastRequestLine") == ""
    h.call("MusicServices.Send", "test-spotify")
    at = h.account_db()["music"]["requests"][-1]["at"]
    line = h.call1("MusicServices.LastRequestLine")
    assert line == ("Test Spotify (%s): no report from the NeedleDropper app since. Press Refresh status after it "
                    "confirms in Windows." % hm(at))
    if case == "matched":
        block = music(actions=[{"id": at, "action": "test-spotify", "result": "playing",
                                "text": "Spotify test: playing Toto | Africa for 15 seconds.", "at": at + 3}])
        data, want = app(written=at + 4, block=block), \
            "Test Spotify (%s): Spotify test: playing Toto || Africa for 15 seconds." % hm(at)
    elif case == "late-report":
        data, want = app(written=at + 30, block=music()), \
            ("Test Spotify (%s): the NeedleDropper app didn't receive this request. Check the link with Send a test "
             "signal, then try again." % hm(at))
    else:
        data, want = app(written=at + 29, block=music()), line
    h2 = h.reload(companion_data=data)
    assert h2.call1("MusicServices.LastRequestLine") == want


# ----------------------------------------------------- frames are one row --

def test_every_frame_music_services_can_build_is_one_strip_row():
    """DESIGN-v1.2.1 5.2 item 3: 5 actions x with or without a Client ID x prefs set or not x flags, and
    every HELLO the beacon draws: at most Capacity(1) bytes, one row, and at 2, 3 and 4 px cells inside
    the 512 x 48 capture."""
    h = make()
    cap = int(h.ns.Protocol2.Capacity(1))
    assert cap == 39
    prefs = {"order": "youtube-spotify", "corner": "bottomright", "fade": False, "resume": True}
    events = []
    for name in h.from_lua(h.ns.MusicServices.ACTIONS):
        for cid in (None, CID):
            for pr in (None, prefs):
                if cid and pr:
                    continue                                  # never built (DESIGN-v1.2.1 2.2)
                for flags in (0, 0x3F):
                    ev = {"type": int(ET.CONFIG), "seq": 255, "flags": flags, "action": {"name": name, "at": 4294967295}}
                    if cid:
                        ev["clientId"] = cid
                    if pr:
                        ev["prefs"] = pr
                    events.append(ev)
    events.append({"type": int(ET.CONFIG), "seq": 255, "flags": 0x3F, "prefs": prefs})
    for pk in (None, 65535):
        for pr in (None, prefs):
            ev = {"type": int(ET.HELLO), "seq": 255, "flags": 0x3F, "prefs": pr}
            if pk is not None:
                ev["placeKey"] = pk
            events.append(ev)
    P2 = h.ns.Protocol2
    for ev in events:
        lev = h.table(ev)
        n = len(encoded(h, lev))
        assert n <= cap, ev
        rows = P2.AdaptiveLayout(n)
        assert int(rows[0] if isinstance(rows, tuple) else rows) == 1, ev
        for px in (2, 3, 4):
            grid = h.to_py(P2.EncodeCellsFromBytes(P2.EncodeFrame(lev), h.table({"cellPx": px, "maxRows": 1})))
            assert grid["rows"] == 1 and grid["widthPx"] <= 512 and grid["heightPx"] <= 48, (ev, px)


def test_send_refuses_a_two_row_config():
    """The emitter's own guard: a CONFIG that would not fit one row is never emitted."""
    h = make(app(block=music()))
    h.run("local ns = ... ns.MusicServices.ONE_ROW = 30", h.ns)   # pretend a smaller row
    # another Client ID than the app's (INGAME-1: the app's own ID is never sent back to it)
    h.call("MusicServices.SetClientId", CID2)
    ev, why = h.call("MusicServices.Send", "connect-spotify")
    assert ev is None and "did not fit the beacon" in why and configs(h) == []


# ------------------------------------------------------------ status text --

def lines(h) -> list[str]:
    return h.call1("MusicServices.StatusLines")


def test_status_lines_without_app_and_for_old_apps():
    h = make()
    assert lines(h)[:3] == [
        "NeedleDropper app not found. Spotify and YouTube songs need it: get it where you got NeedleDropper.",
        "Spotify: not set up.", "YouTube: plays through the NeedleDropper app."]
    assert ms(h, "StatusWord") == "App not found"
    h = make(app(version="1.1.0"))
    assert lines(h)[0] == "Update the NeedleDropper app to 1.2.1 to play Spotify and YouTube songs."
    h = make(app(version="1.2.0"))
    assert lines(h)[0] == ("NeedleDropper app 1.2.0 can't read this addon's signals: update it to 1.2.1 to play "
                           "Spotify and YouTube songs.")
    assert ms(h, "StatusWord") == "Update the app"
    h = make(app(written=0))
    assert lines(h)[0] == "NeedleDropper app installed: it hasn't reported in yet."
    assert ms(h, "StatusWord") == "Waiting for the app"


def test_status_lines_for_a_connected_app():
    h = make(app(block=music()))
    app_line, sp, yt, last = lines(h)
    assert app_line == "NeedleDropper app 1.2.1 · report from %s (2 min before login)" % hm(LOGIN - 120)
    assert sp == "Spotify: connected as Ethan (Premium)"
    assert yt == "YouTube: ready · player box top right (Chrome)"
    assert last == ""
    assert ms(h, "StatusWord") == "Spotify + YouTube ready"
    assert h.call("MusicServices.SpotifyButton") == ("Manage…", "open")
    h.set_setting("youtubeCorner", "bottomleft")
    h.set_setting("musicOrder", "youtube")
    _, sp, yt, _ = lines(h)
    assert sp.endswith(" · not used: Play music with is YouTube only") and "bottom left" in yt
    h.set_setting("musicOrder", "none")
    assert lines(h)[2].endswith(" · not used: Play music with is Neither")


@pytest.mark.parametrize("state, legacy, line, button", [
    ("needs-relogin", "error",
     "Spotify: sign in again to allow in-app playback (NeedleDropper's Music services).", ("Sign in again", "connect")),
    # v1.2.1 review ON-9: no-player without a cause (an older app) is worded neutrally, not "install Chrome"
    ("no-player", "error",
     "Spotify: NeedleDropper's player isn't ready (the app's Connect page says why).", ("Check Spotify…", "open")),
    ("stuck", "error",
     "Spotify: a song didn't start in NeedleDropper's player, so drops play YouTube until it works again (Test "
     "Spotify checks it).", ("Check Spotify…", "open")),
    ("error", "error", "Spotify: sign in again (the sign-in stopped working).", ("Sign in again", "connect")),
    ("premium-required", "premium-required", "Spotify: this account needs Premium to play songs.",
     ("Needs Premium…", "open")),
    ("not-connected", "not-connected", "Spotify: not connected (Client ID …cdef).", ("Connect Spotify", "connect")),
])
def test_status_lines_for_every_spotify_state(state, legacy, line, button):
    block = music(spotify={"clientId": CID, "state": state, "mode": "app"})
    h = make(app(spotify=legacy, block=block))
    assert lines(h)[1] == line
    assert h.call("MusicServices.SpotifyButton") == button
    assert ms(h, "StatusWord") in ("YouTube ready", "Spotify + YouTube ready")


def test_spotify_plan_and_mode_details():
    h = make(app(block=music(spotify={"account": "Ann", "product": "free", "state": "connected", "mode": "connect"})))
    assert lines(h)[1] == ("Spotify: connected as Ann, but Spotify free can't be controlled: playback needs Premium. · "
                           "plays on another Spotify device")
    assert h.call("MusicServices.SpotifyButton") == ("Needs Premium…", "open")
    assert ms(h, "StatusWord") == "YouTube ready"
    h = make(app(block=music(spotify={"state": "connected"})))
    assert lines(h)[1] == "Spotify: connected (no account name)"
    h = make(app(spotify="not-connected", block=music(spotify={"state": "not-connected"})))
    assert lines(h)[1] == "Spotify: not set up."
    assert h.call("MusicServices.SpotifyButton") == ("Set up Spotify…", "open")


def test_youtube_lines():
    h = make(app(block=music(youtube={"enabled": False, "player": "chrome"})))
    assert lines(h)[2] == "YouTube: switched off in the NeedleDropper app (its Connect page)."
    h = make(app(block=music(youtube={"enabled": True, "player": "none"})))
    assert lines(h)[2] == "YouTube: the NeedleDropper app found no browser for its player box: install Google Chrome."
    assert ms(h, "StatusWord") == "Spotify ready"
    opened = LOGIN - 86400 * 3
    h = make(app(block=music(youtube={"enabled": True, "player": "edge", "signInOpened": opened})))
    assert lines(h)[2] == "YouTube: ready · player box top right (Edge) · sign-in page opened %s" % \
        time.strftime("%d %b", time.localtime(opened))


def test_app_text_is_escaped():
    h = make(app(block=music(spotify={"account": "|cffff0000x|r", "state": "connected", "product": "premium"})))
    assert lines(h)[1] == "Spotify: connected as ||cffff0000x||r (Premium)"


def test_waiting_for_the_browser_after_a_connect():
    h = make(app(spotify="not-connected", block=music(spotify={"clientId": CID, "state": "not-connected"})))
    assert h.call("MusicServices.SpotifyButton") == ("Connect Spotify", "connect")
    h.call("MusicServices.SpotifyButtonClicked")
    assert configs(h)[-1]["action"]["name"] == "connect-spotify"
    assert h.call("MusicServices.SpotifyButton") == ("Waiting for browser…", "open")
    h.advance(301)
    assert h.call("MusicServices.SpotifyButton") == ("Connect Spotify", "connect")


def test_choices_copy():
    assert make().call1("MusicServices.ChoicesCopy") == "Saved. The NeedleDropper app picks it up when it sees the game."
    assert make(app(version="1.2.0")).call1("MusicServices.ChoicesCopy") == \
        "Saved. Update the NeedleDropper app to 1.2.1: the version you have can't read this addon's signals."
    assert make(app()).call1("MusicServices.ChoicesCopy") == \
        "The NeedleDropper app follows this within a second while the game runs."


def test_sent_copy_for_each_request():
    h = make(app(block=music()), settings={"youtubeCorner": "bottomright"}, db={"music": {"prefsSet": True}})
    t = LOGIN + 5
    assert ms(h, "SentCopy", "test-youtube", t) == (
        "Sent (%s). The YouTube box should open in the bottom right corner and play 15 seconds of Toto %s Africa. "
        "If not, a Windows notification says why." % (hm(t), DASH))
    assert ms(h, "SentCopy", "disconnect-spotify", t).startswith("Sent (%s). The NeedleDropper app forgets" % hm(t))
    assert ms(h, "SentCopy", "connect-spotify", t).startswith("Sent to the NeedleDropper app (%s)." % hm(t))
    assert "player browser" in ms(h, "SentCopy", "youtube-sign-in", t)
    h = make(app(block=music(spotify={"state": "connected", "mode": "connect"})))
    assert ms(h, "SentCopy", "test-spotify", t) == (
        "Sent (%s). You should hear 15 seconds of Toto %s Africa from Spotify in a few seconds. If not, a Windows "
        "notification says why." % (hm(t), DASH))


# ------------------------------------------------------- Companion's block --

def test_companion_music_block_is_checked_field_by_field():
    raw = {"spotify": {"clientId": CID.upper(), "redirectUri": "http://localhost:8321/callback",
                       "account": "Et\x01han\n", "product": "Premium", "state": "playing", "mode": "web"},
           "youtube": {"enabled": "yes", "player": "default", "signInOpened": -1},
           "prefs": {"order": "spotify-youtube", "corner": "center", "fade": 1, "resume": False, "fromGame": True},
           "actions": [{"id": i, "action": "test-spotify", "result": "playing", "text": "ok", "at": i}
                       for i in range(1, 11)]
           + [{"id": 2 ** 32, "action": "test-spotify", "result": "playing"},
              {"id": 5, "action": "explode", "result": "playing"},
              {"id": 6, "action": "test-youtube", "result": "Bad Result"},
              {"id": 7, "action": "test-youtube", "result": "failed", "text": "x" * 301}]}
    h = make(app(block=raw))
    m = h.call1("Companion.Music")
    assert m["spotify"] == {"account": "Ethan"}
    assert m["youtube"] in ({}, [])
    assert m["prefs"] == {"order": "spotify-youtube", "resume": False, "fromGame": True}
    ids = [a["id"] for a in m["actions"]]
    assert ids == [4, 5, 6, 7, 8, 9, 10, 7] and "text" not in m["actions"][-1]


@pytest.mark.parametrize("state", ["connected", "not-connected", "needs-relogin", "premium-required", "no-player",
                                   "stuck", "error"])
def test_companion_accepts_exactly_the_d17_states(state):
    h = make(app(block=music(spotify={"state": state, "mode": "connect"},
                             youtube={"player": "edge", "enabled": True})))
    m = h.call1("Companion.Music")
    assert m["spotify"] == {"state": state, "mode": "connect"} and m["youtube"]["player"] == "edge"
    assert h.call1("Companion.SpotifyState") == state


def test_companion_versions_and_features():
    h = make(app(version="1.2.1"))
    for v, a, b, c, want in [("1.2.1", 1, 2, 1, True), ("1.2.0", 1, 2, 1, False), ("1.10.0", 1, 2, 1, True),
                             ("2", 1, 9, 9, True), ("1.2", 1, 2, 0, True), ("dev", 0, 0, 0, False),
                             (None, 0, 0, 0, False), ("1.2.1-rc1", 1, 2, 1, True)]:
        assert h.call1("Companion.AtLeast", v, a, b, c) is want, v
    assert h.call1("Companion.Supports", "config") is True and h.call1("Companion.Supports", "teleport") is False
    assert make(app(version="1.2.0")).call1("Companion.Supports", "config") is False
    assert make().call1("Companion.Supports", "config") is False
    assert make().call1("Companion.Music") is None


def test_status_line_names_the_account_and_youtube():
    h = make(app(block=music()))
    assert h.call1("Companion.StatusLine") == "App seen 2 min before login · Spotify: Ethan (Premium) · YouTube ready"
    h = make(app(spotify="error", block=music(spotify={"state": "no-player"}, youtube={"enabled": True, "player": "none"})))
    # v1.2.1 review ON-9: no-player without a cause is "not ready"; only the cause "no-chrome" names Chrome
    assert h.call1("Companion.StatusLine") == \
        "App seen 2 min before login · Spotify player not ready · YouTube: no player browser"
    h = make(app(youtube=False))
    assert h.call1("Companion.StatusLine").endswith(" · YouTube off")


# ------------------------------------------------------------ /needle music --

def test_needle_music_subcommands():
    h = make(app(block=music(spotify={"state": "not-connected"})))
    out = h.slash("music status")
    assert out[0].startswith("NeedleDropper: NeedleDropper app 1.2.1") and out[1] == "NeedleDropper: Spotify: not set up."
    out = h.slash("music connect not-an-id")
    assert out == ["NeedleDropper: A Client ID is 32 characters: digits and the letters a-f. Copy the Client ID from "
                   "your app's Settings page, not the Client secret."]
    assert configs(h) == []
    h.slash("music connect " + CID.upper()[:16] + " " + CID.upper()[16:])
    assert h.account_db()["music"]["clientId"] == CID
    assert configs(h)[-1]["action"]["name"] == "connect-spotify" and configs(h)[-1]["clientId"] == CID
    for cmd, name in (("music test spotify", "test-spotify"), ("music test youtube", "test-youtube"),
                      ("music youtube", "youtube-sign-in")):
        h.slash(cmd)
        assert configs(h)[-1]["action"]["name"] == name
    assert "usage: /needle music" in h.slash("music dance")[-1]
    assert any("/needle music - [status|connect|disconnect|test spotify|test youtube|youtube]" in line
               for line in h.slash("help"))
    assert any("/needle report" in line for line in h.slash("help"))


def test_needle_music_opens_the_dialog():
    h = make(app(block=music()))
    h.slash("music")
    d = h.g.NeedleDropperMusicDialog
    assert d is not None and d.IsShown(d)


def test_needle_music_without_the_window_prints_the_status():
    files = [f for f in toc_files() if not f.startswith("UI/")]
    h = AddonHarness(sets=[SET], files=files, companion_data=app(block=music()))
    out = h.slash("music")
    assert out[1] == "NeedleDropper: Spotify: connected as Ethan (Premium)"

