"""v1.2.1 Music services in the Settings panel (DESIGN-v1.2.1 1.2, 5.2 item 7): the section's rows in order,
their function-valued texts and tooltips for each status, search tags and modify predicates, clicks that
send the right CONFIG (decoded here), and the old Settings API that has no buttons."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import AddonHarness, make_set  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402

LOGIN = 1762365600
CID = "0123456789abcdef0123456789abcdef"
SET = make_set([{"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest"}])
MUSIC = {"spotify": {"clientId": CID, "redirectUri": "http://127.0.0.1:8321/callback", "account": "Ethan",
                     "product": "premium", "state": "connected", "mode": "app"},
         "youtube": {"enabled": True, "player": "chrome"}}


def app(version="1.2.1", spotify="connected", music=MUSIC):
    d = {"schema": 1, "appVersion": version, "written": LOGIN - 60,
         "status": {"spotify": spotify, "youtube": True, "transports": {"pixel": True}}}
    if music is not None:
        d["music"] = music
    return d


def make(data=None, **kw) -> AddonHarness:
    if data is not None:
        kw["companion_data"] = data
    return AddonHarness(sets=[SET], **kw)


def rendered(h) -> list[dict]:
    out = h.from_lua(h.shim.settings.Render())
    h.check_errors()
    return out


def section(h) -> list[dict]:
    els = rendered(h)
    i = next(i for i, e in enumerate(els) if e["kind"] == "header" and e["name"] == "Music services")
    j = next(j for j in range(i + 1, len(els)) if els[j]["kind"] == "header")
    return els[i + 1:j]


def raw_elements(h) -> list:
    return list(h.shim.settings.elements.values())


def music_buttons(h) -> dict:
    return {e.name: e for e in raw_elements(h) if e.kind == "button"}


def test_the_section_sits_between_needle_drops_and_playback_with_ten_rows():
    h = make()
    headers = [e["name"] for e in rendered(h) if e["kind"] == "header"]
    assert headers[:3] == ["Needle drops", "Music services", "Playback"]
    rows = [(e["kind"], e.get("variable") or e["name"]) for e in section(h)]
    assert rows == [("button", "NeedleDropper app"), ("button", "Spotify account"), ("button", "YouTube account"),
                    ("dropdown", "NeedleDropper_musicOrder"), ("dropdown", "NeedleDropper_youtubeCorner"),
                    ("checkbox", "NeedleDropper_musicFade"), ("checkbox", "NeedleDropper_resumeMyMusic"),
                    ("button", "Test Spotify"), ("button", "Test YouTube"), ("button", "Refresh status")]
    assert [e["text"] for e in section(h) if e["kind"] == "button"] == [
        "App not found", "Set up Spotify…", "Sign in to YouTube…", "Test Spotify", "Test YouTube", "Reload UI"]
    assert h.ns.Options.controlErrors == 0


def test_option_texts():
    h = make()
    dd = {c[2].variable: c for c in h.shim.settings.controls.values() if c[1] == "dropdown"}
    order = [(o["value"], o["label"]) for o in h.from_lua(dd["NeedleDropper_musicOrder"][3]())]
    assert order == [("spotify-youtube", "Spotify, then YouTube"), ("youtube-spotify", "YouTube, then Spotify"),
                     ("spotify", "Spotify only"), ("youtube", "YouTube only"),
                     ("none", "Neither: title cards and in-game files only")]
    corner = [o["value"] for o in h.from_lua(dd["NeedleDropper_youtubeCorner"][3]())]
    assert corner == ["topright", "topleft", "bottomright", "bottomleft"]


def test_search_tags_and_predicates():
    h = make()
    b = music_buttons(h)
    tags = {name: list(e.searchTags.values()) for name, e in b.items()}
    assert tags["NeedleDropper app"] == ["NeedleDropper app", "app", "companion", "status", "music"]
    assert tags["Spotify account"] == ["Spotify account", "Spotify", "account", "Client ID", "connect", "sign in",
                                       "Premium"]          # the function-valued text is never indexed
    assert tags["YouTube account"][-4:] == ["YouTube", "sign in", "Premium", "ads"]
    assert tags["Refresh status"] == ["Refresh status", "Reload UI", "reload", "refresh", "status"]
    preds = {name: len(list(e.modifyPredicates.values())) for name, e in b.items()}
    assert preds["YouTube account"] == preds["Test Spotify"] == preds["Test YouTube"] == 1
    assert preds["NeedleDropper app"] == preds["Spotify account"] == preds["Refresh status"] == 0
    controls = {e.variable: list(e.searchTags.values()) for e in raw_elements(h) if e.kind == "dropdown"}
    assert controls["NeedleDropper_musicOrder"] == ["Spotify", "YouTube"]
    assert controls["NeedleDropper_youtubeCorner"] == ["YouTube", "corner", "player"]


def test_rows_follow_the_apps_report():
    cases = [
        (None, "App not found", "Set up Spotify…", False),
        (app(version="1.2.0"), "Update the app", "Manage…", False),     # Manage... opens the dialog, which explains
        (app(), "Spotify + YouTube ready", "Manage…", True),
        (app(spotify="error", music={"spotify": {"clientId": CID, "state": "needs-relogin"},
                                     "youtube": {"enabled": True, "player": "chrome"}}),
         "YouTube ready", "Sign in again", True),
        (app(spotify="not-connected", music={"spotify": {"clientId": CID, "state": "not-connected"}}),
         "YouTube ready", "Connect Spotify", True),
    ]
    for data, word, spotify, enabled in cases:
        h = make(data)
        rows = {e["name"]: e for e in section(h) if e["kind"] == "button"}
        assert rows["NeedleDropper app"]["text"] == word, data
        assert rows["Spotify account"]["text"] == spotify, data
        assert [rows[n]["enabled"] for n in ("YouTube account", "Test Spotify", "Test YouTube")] == [enabled] * 3
        assert rows["NeedleDropper app"]["enabled"] and rows["Spotify account"]["enabled"] and \
            rows["Refresh status"]["enabled"]


def test_tooltips_are_the_status_lines_and_the_reasons():
    h = make()
    rows = {e["name"]: e for e in section(h) if e["kind"] == "button"}
    tip = rows["NeedleDropper app"]["tooltip"].split("\n")
    assert tip[0].startswith("NeedleDropper app not found.") and tip[-1] == "Click to open Music services."
    assert rows["YouTube account"]["tooltip"].endswith(
        "\n\nNot sent: install the NeedleDropper app first (from the page you got NeedleDropper from).")
    assert rows["Spotify account"]["tooltip"].startswith("Create your own free Spotify developer app")
    h = make(app())
    rows = {e["name"]: e for e in section(h) if e["kind"] == "button"}
    assert rows["Spotify account"]["tooltip"] == ("Connected as Ethan (Premium) · Client ID …cdef. Click for "
                                                  "Disconnect and Test.")
    assert "in its own Spotify player (no Spotify app needed)" in rows["Test Spotify"]["tooltip"]
    assert "\n\n" not in rows["Test Spotify"]["tooltip"]
    assert rows["Refresh status"]["tooltip"].startswith("The game reads the NeedleDropper app's report only")


def test_clicks_send_the_right_requests():
    h = make(app(), settings={"chatlog": True})
    b = music_buttons(h)
    mark = h.mark()
    b["Test Spotify"].click()
    b["Test YouTube"].click()
    evs = h.events_since(mark)
    assert [e["action"]["name"] for e in evs] == ["test-spotify", "test-youtube"]
    frames = [f for f in h.chat_frames() if f.type == ET.CONFIG]
    assert [f.action.name for f in frames] == ["test-spotify", "test-youtube"]
    assert any(line.startswith("NeedleDropper: Sent (") and "Toto" in line for line in h.prints())
    # YouTube account: a confirmation first
    b["YouTube account"].click()
    assert h.lua("return SHIM.ui.Popup('NEEDLEDROPPER_YOUTUBE_SIGNIN') ~= nil")
    h.lua("SHIM.ui.PopupAccept('NEEDLEDROPPER_YOUTUBE_SIGNIN')")
    h.check_errors()
    assert h.events_of(ET.CONFIG)[-1]["action"]["name"] == "youtube-sign-in"
    # Spotify account while connected: Manage... opens the dialog, sends nothing
    n = len(h.events_of(ET.CONFIG))
    b["Spotify account"].click()
    d = h.g.NeedleDropperMusicDialog
    assert d.IsShown(d) and len(h.events_of(ET.CONFIG)) == n
    b["NeedleDropper app"].click()
    assert d.IsShown(d)


def test_spotify_row_connects_when_a_client_id_is_known():
    h = make(app(spotify="not-connected", music={"spotify": {"clientId": CID, "state": "not-connected"}}))
    music_buttons(h)["Spotify account"].click()
    cfg = h.events_of(ET.CONFIG)[-1]
    assert cfg["action"]["name"] == "connect-spotify" and "clientId" not in cfg    # the app uses its own
    rows = {e["name"]: e for e in section(h) if e["kind"] == "button"}
    assert rows["Spotify account"]["text"] == "Waiting for browser…"


def test_refresh_status_reloads_once_inside_pcall():
    h = make()
    music_buttons(h)["Refresh status"].click()
    assert int(h.lua("return SHIM.ui.reloads")) == 1


def test_settings_rows_change_the_choices():
    h = make(app())
    order = h.shim.settings.settings["NeedleDropper_musicOrder"]
    order.SetValue(order, "youtube")
    assert h.settings()["musicOrder"] == "youtube" and h.account_db()["music"]["prefsSet"] is True
    h.advance(1.1)
    cfg = h.events_of(ET.CONFIG)[-1]
    assert p2.frame_from_event(cfg).prefs.order == "youtube"


def test_old_settings_api_gets_the_four_settings_only():
    h = AddonHarness(sets=[SET], before_load=lambda hh: hh.shim.RemoveSettingsExtras())
    els = rendered(h)
    assert not any(e["kind"] == "button" for e in els)
    variables = [e.get("variable") for e in els]
    i = variables.index("NeedleDropper_musicOrder")
    assert variables[i:i + 4] == ["NeedleDropper_musicOrder", "NeedleDropper_youtubeCorner",
                                  "NeedleDropper_musicFade", "NeedleDropper_resumeMyMusic"]
    h.slash("music")
    d = h.g.NeedleDropperMusicDialog
    assert d.IsShown(d)
