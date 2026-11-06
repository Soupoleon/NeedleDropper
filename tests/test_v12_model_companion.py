"""v1.2 Companion.lua: reading NeedleDropper_CompanionData (DESIGN-v1.2 section 2.6,
CONTRACTS-v1.2 section 2.7)."""
from __future__ import annotations

import pytest

from addon_harness import lua_literal
from v12_model_harness import ModelHarness, ref_encode

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
LOGIN_TIME = 1762365600  # time() at login in the shim (SHIM.EPOCH)


def data(**kw):
    d = {"schema": 1, "appVersion": "1.2.0", "written": LOGIN_TIME - 125,
         "status": {"spotify": "connected", "youtube": True, "transports": {"pixel": True, "chatlog": False}},
         "tracks": {}}
    d.update(kw)
    return d


def harness(companion_data="unset", **kw):
    return ModelHarness(profile="model", companion_data=companion_data, **kw)


def pack(pid="bobs-mix", revision=3, name="Bob's Mix"):
    return {"format": "needledropper-pack", "version": 1, "id": pid, "name": name, "author": "Bob",
            "description": "", "tags": [], "revision": revision,
            "drops": [{"zone": 1429, "artist": "Low Roar", "title": "I'll Keep Coming"}]}


# ------------------------------------------------------------------ status --

def test_no_app_data():
    h = harness()
    assert h.call1("Companion.Data") is None
    st = h.call1("Companion.Status")
    assert st["present"] is False and st["youtube"] is False and st["written"] == 0 and st["outdated"] is False
    assert h.call1("Companion.StatusLine") == "NeedleDropper app not found: Spotify and YouTube songs need it"
    assert h.call1("Companion.Imports") in ({}, []) and h.call1("Companion.SpotifyConnected") is False


def test_current_app_status_line():
    h = harness(data())
    st = h.call1("Companion.Status")
    assert st == {"present": True, "appVersion": "1.2.0", "written": LOGIN_TIME - 125, "spotify": "connected",
                  "youtube": True, "transports": {"pixel": True, "chatlog": False}, "outdated": False}
    assert h.call1("Companion.StatusLine") == "App seen 2 min before login · Spotify connected"
    assert h.call1("Companion.SpotifyConnected") is True


@pytest.mark.parametrize("age, spotify, line", [
    (5, "not-connected", "App seen just before login · Spotify not connected"),
    (3 * 3600 + 5, "premium-required", "App seen 3 h before login · Spotify needs Premium"),
    (4 * 86400, "error", "App seen 4 days before login · Spotify needs you to sign in again"),
    (-30, None, "App seen just before login"),
])
def test_status_line_variants(age, spotify, line):
    status = {"youtube": True}
    if spotify:
        status["spotify"] = spotify
    h = harness(data(written=LOGIN_TIME - age, status=status))
    assert h.call1("Companion.StatusLine") == line


@pytest.mark.parametrize("version, outdated", [
    ("1.1.0", True), ("1.1.9-beta", True), ("0.9", True), ("1", True), ("garbage", True),
    ("1.2.0", False), ("1.2", False), ("1.10.0", False), ("2.0.0", False), ("1.2.0-rc1", False),
])
def test_old_apps_are_asked_to_update(version, outdated):
    h = harness(data(appVersion=version))
    assert h.call1("Companion.IsOutdated", version) is outdated
    line = h.call1("Companion.StatusLine")
    if outdated:
        assert line == "Update the NeedleDropper app to 1.2 to play Spotify and YouTube songs"
    else:
        assert line.startswith("App seen")


def test_app_that_has_not_reported_yet():
    h = harness(data(written=0))
    assert h.call1("Companion.StatusLine") == "NeedleDropper app installed: it hasn't reported in yet"


@pytest.mark.parametrize("raw", ["a string", 42, True, {"schema": "one"}, {"schema": 0}])
def test_malformed_data_means_no_app_data(raw):
    h = harness(raw)
    assert h.call1("Companion.Data") is None and h.call1("Companion.Status")["present"] is False


def test_fields_are_checked_one_by_one():
    raw = data(appVersion="1.2.0; DROP TABLE", written="yesterday",
               status={"spotify": "hacked", "youtube": "yes", "transports": {"pixel": 1, "chatlog": True}},
               tracks={"q:ok": {"spotify": SPOTIFY_ID, "durationMs": 295000, "title": "Africa", "artist": "Toto"},
                       "q:bad": {"spotify": "short", "durationMs": -5, "title": 7, "notFound": "true"},
                       "q:flags": {"notFound": True, "notEmbeddable": True}, "q:junk": "no", 7: {"title": "x"},
                       "q:" + "x" * 600: {"title": "long key"}},
               imports=[{"code": "", "active": True}, {"code": 5}, "x", {"code": "!NeedleDropper:1!x!", "active": 1}])
    h = harness(raw)
    d = h.call1("Companion.Data")
    assert "appVersion" not in d and d["written"] == 0
    assert d["status"] == {"youtube": False, "transports": {"pixel": False, "chatlog": True}}
    assert d["tracks"]["q:ok"] == {"spotify": SPOTIFY_ID, "durationMs": 295000, "title": "Africa", "artist": "Toto"}
    assert d["tracks"]["q:bad"] in ({}, [])
    assert d["tracks"]["q:flags"] == {"notFound": True, "notEmbeddable": True}
    assert set(d["tracks"]) == {"q:ok", "q:bad", "q:flags"}
    assert d["imports"] == [{"code": "!NeedleDropper:1!x!", "active": False}]
    # an app version that can't be read counts as outdated
    assert h.call1("Companion.StatusLine").startswith("Update the NeedleDropper app")


def test_data_is_read_at_player_login_and_announced():
    h = harness(login=False)
    h.rt.execute("NeedleDropper_CompanionData = " + lua_literal(data()))  # the data addon loads later
    h.login()
    assert h.call1("Companion.Status")["present"] is True
    h.record_fires()
    h.rt.execute("NeedleDropper_CompanionData = nil")
    h.call("Companion.Read")
    assert h.fired("ND_COMPANION_DATA") == [(None,)]
    assert h.call1("Companion.Status")["present"] is False


# ------------------------------------------------------------------- tracks --

def test_track_lookup_by_spotify_youtube_query_and_legacy_id():
    tracks = {"sp:" + SPOTIFY_ID: {"durationMs": 352000},
              "yt:QAo_Ycocl1E": {"notEmbeddable": True},
              "q:toto\x1fafrica": {"spotify": SPOTIFY_ID, "title": "Africa", "artist": "TOTO", "durationMs": 295000},
              "zone:1436": {"title": "legacy"}}
    h = harness(data(tracks=tracks))
    track = h.fn("Companion.Track")
    assert h.to_py(track(h.to_lua({"spotify": SPOTIFY_ID, "artist": "Toto", "title": "Africa"}))) == {"durationMs": 352000}
    assert h.to_py(track(h.to_lua({"youtube": "QAo_Ycocl1E"}))) == {"notEmbeddable": True}
    assert h.to_py(track(h.to_lua({"artist": "Toto", "title": "Africa"})))["durationMs"] == 295000
    assert h.to_py(track(h.to_lua({"artist": "TOTO", "title": "AFRICA"})))["title"] == "Africa"
    assert h.to_py(track(h.to_lua({"id": "zone:1436", "artist": "x", "title": "y"}))) == {"title": "legacy"}
    assert track(h.to_lua({"artist": "Nobody", "title": "Nothing"})) is None


def test_track_keys_match_python_lowercasing():
    key = "q:" + "Édith Piaf".lower() + "\x1f" + "Non, Je Ne Regrette Rien".lower()
    h = harness(data(tracks={key: {"notFound": True}}))
    found = h.to_py(h.fn("Companion.Track")(h.to_lua({"artist": "Édith Piaf", "title": "Non, Je Ne Regrette Rien"})))
    assert found == {"notFound": True}
    keys = h.call1("Companion.TrackKeys", "  Édith\tPiaf ", "Title")
    assert keys[0] == "q:édith piaf\x1ftitle"  # cleaned like the beacon query (P2.BuildQuery)
    assert "q:Édith piaf\x1ftitle" in keys  # ASCII-only lowercasing as a second try


def test_lowercasing_equals_python_for_the_covered_alphabets():
    h = harness()
    lower = h.fn("Companion.Lower")
    covered = (list(range(0x41, 0x5B)) + list(range(0xC0, 0xDF)) + list(range(0x100, 0x180)) +
               list(range(0x386, 0x3AC)) + list(range(0x400, 0x430)))
    mismatches = [hex(cp) for cp in covered if lower(chr(cp)) != chr(cp).lower()]
    assert mismatches == []
    # outside those alphabets characters are left alone (never mangled)
    for cp in list(range(0x80, 0xC0)) + list(range(0x180, 0x370)) + list(range(0x4E00, 0x4E10)) + [0x1F3B5]:
        assert lower(chr(cp)) in (chr(cp), chr(cp).lower())


# ------------------------------------------------------------------ imports --

def test_imports_are_offered_once():
    codes = [ref_encode(pack("bobs-mix", 3)), ref_encode(pack("alt-mix", 1, "Alt Mix")),
             ref_encode(pack("bobs-mix", 3)), "!NeedleDropper:1!garbage!"]
    h = harness(data(imports=[{"code": codes[0], "active": True}, {"code": codes[1], "active": False},
                              {"code": codes[2], "active": True}, {"code": codes[3], "active": True}]))
    offers = h.call1("Companion.Imports")
    assert [(o["key"], o["active"], o["name"]) for o in offers] == [("bobs-mix@3", True, "Bob's Mix"),
                                                                     ("alt-mix@1", False, "Alt Mix")]
    assert offers[0]["code"] == codes[0] and offers[0]["pack"]["drops"][0]["zone"] == 1429
    assert h.fn("Companion.MarkImportHandled")(h.fn("Companion.Imports")()[1]) is True
    assert [o["key"] for o in h.call1("Companion.Imports")] == ["alt-mix@1"]
    assert h.account_db()["importsHandled"] == {"bobs-mix@3": True}
    assert h.call1("Companion.MarkImportHandled", "alt-mix@1") is True
    assert h.call1("Companion.Imports") in ({}, [])
    assert h.call1("Companion.MarkImportHandled", "") is False


def test_a_newer_revision_is_offered_again():
    h = harness(data(imports=[{"code": ref_encode(pack("bobs-mix", 4)), "active": False}]),
                db={"schema": 2, "importsHandled": {"bobs-mix@3": True}})
    assert [o["key"] for o in h.call1("Companion.Imports")] == ["bobs-mix@4"]
