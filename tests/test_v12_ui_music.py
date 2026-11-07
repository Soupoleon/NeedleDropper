"""v1.2.1 the Music services dialog and the report dialog (DESIGN-v1.2.1 1.3, 5.2 item 8; decision D16):
templates, autofocus, UISpecialFrames and the replaced CloseButton; the read-only boxes; the Client ID box,
its messages, the buttons' states and reasons; the popups; Refresh; combat and the gamepad UI; escaping of
the app's text; /needle report."""
from __future__ import annotations

import time

from v12_ui_harness import UIHarness
from needledropper import protocol2 as p2
from needledropper.models import EventType as ET

LOGIN = 1762365600
CID = "0123456789abcdef0123456789abcdef"
CID2 = "fedcba9876543210fedcba9876543210"
REDIRECT = "http://127.0.0.1:8399/callback"


def app(version="1.2.1", spotify="connected", account="Ethan", state="connected", **sp):
    m = {"spotify": {"clientId": CID, "redirectUri": REDIRECT, "account": account, "product": "premium",
                     "state": state, "mode": "app"},
         "youtube": {"enabled": True, "player": "chrome"}}
    m["spotify"].update(sp)
    return {"schema": 1, "appVersion": version, "written": LOGIN - 60,
            "status": {"spotify": spotify, "youtube": True, "transports": {"pixel": True}}, "music": m}


def setup(data=None, **kw) -> UIHarness:
    if data is not None:
        kw["companion_data"] = data
    return UIHarness(real=True, **kw)


def dialog(h):
    return h.g.NeedleDropperMusicDialog


def opened(h, data=None, **kw):
    h = setup(data, **kw) if h is None else h
    assert h.UI.OpenMusic()
    h.check_errors()
    return h, dialog(h)


def configs(h):
    return h.events_of(ET.CONFIG)


def test_the_dialog_basics():
    h, d = opened(None, app())
    assert h.shown(d) and d.__template == "ButtonFrameTemplate" and d.GetFrameStrata(d) == "DIALOG"
    assert "NeedleDropperMusicDialog" in h.special_frames()
    assert not h.same(d.CloseButton.GetScript(d.CloseButton, "OnClick"), h.g.UIPanelCloseButton_OnClick)
    allowed = {str(k): str(v) for k, v in h.ui_shim.ALLOWED_TEMPLATES.items()}
    for c in h.created():
        if c["template"] is not None:
            assert allowed[c["template"]] == c["frameType"], c
    for box in (d.DashboardBox, d.RedirectBox, d.ClientId):
        assert box.__autoFocus is False and h.count(box, "SetAutoFocus") >= 1
    assert h.focused() is None                               # nothing is focused for the player
    assert h.text(d.StatusLines[1]) == "NeedleDropper app 1.2.1 · report from %s (1 min before login)" % \
        time.strftime("%H:%M", time.localtime(LOGIN - 60))
    assert h.text(d.StatusLines[2]) == "Spotify: connected as Ethan (Premium)"
    assert h.text(d.StatusLines[3]) == "YouTube: ready · player box top right (Chrome)"
    assert h.text(d.DashboardBox) == "https://developer.spotify.com/dashboard"
    assert h.text(d.RedirectBox) == REDIRECT                  # the app's port, not the default
    assert h.text(d.ClientId) == CID                          # prefilled from the app's report
    assert h.text(d.ConnectButton) == "Sign in again"
    h.click(d.CloseButton)
    assert not h.shown(d)
    assert h.bad_escapes() == [] and h.forbidden() == []


def test_the_title_bar_drum_opens_it():
    h = setup(app())
    h.open()
    drum = h.window.MusicButton
    h.hover(drum)
    assert h.tooltip()[:2] == ["Music services", "Spotify, YouTube and how songs play."]
    h.click(drum)
    assert h.shown(dialog(h))


def test_read_only_boxes_put_their_text_back():
    h, d = opened(None, app())
    h.type(d.DashboardBox, "https://evil.example/")
    assert h.text(d.DashboardBox) == "https://developer.spotify.com/dashboard"
    h.paste(d.RedirectBox, "nope")
    assert h.text(d.RedirectBox) == REDIRECT
    h.hover(d.DashboardBox)
    assert h.tooltip()[-1] == "Click the box, then press Ctrl+C to copy."


def test_client_id_messages_and_saving():
    h, d = opened(None, app(spotify="not-connected", state="not-connected", clientId=None, account=None))
    assert h.text(d.ClientId) == "" and h.text(d.Validation) == ""
    assert not h.enabled(d.ConnectButton) and d.ConnectButton.ndDisabledReason == "Paste your Client ID first (step 3)."
    h.paste(d.ClientId, "0123456789abcdef0123456789abcde")
    assert h.text(d.Validation).startswith("A Client ID is 32 characters: digits and the letters a-f.")
    assert not h.enabled(d.ConnectButton)
    h.paste(d.ClientId, " " + CID.upper() + "\n")
    assert h.text(d.Validation).endswith("That looks like a Client ID.")
    assert h.enabled(d.ConnectButton) and h.text(d.ConnectButton) == "Connect Spotify"
    assert "clientId" not in h.account_db()["music"]
    h.enter(d.ClientId)                                       # Enter stores it
    assert h.account_db()["music"]["clientId"] == CID
    h.focus(d.ClientId)
    h.type(d.ClientId, "")
    h.focus(d.DashboardBox)                                   # focus loss: an empty box forgets it
    assert "clientId" not in h.account_db()["music"]


def test_another_client_id_while_connected_is_explained():
    h, d = opened(None, app())
    h.paste(d.ClientId, CID2)
    assert h.text(d.Validation) == ("The app is connected with another Client ID (…cdef). Connect switches to this one "
                                    "after you approve it.")
    h.click(d.ConnectButton)
    cfg = configs(h)[-1]
    assert cfg["action"]["name"] == "connect-spotify" and cfg["clientId"] == CID2
    assert h.account_db()["music"]["clientId"] == CID2
    fb = h.text(d.SpotifyFeedback)
    assert fb.startswith("Sent to the NeedleDropper app (") and fb.endswith("then press Send a test signal.")
    assert not h.enabled(d.ConnectButton) and d.ConnectButton.ndDisabledReason == "Just sent: give it a moment."
    h.advance(10.2)  # v1.2.1 review INGAME-8: the lock is the app's 10 s (it was 5 s)
    assert h.enabled(d.ConnectButton)


def test_buttons_without_a_current_app():
    h, d = opened(None, app(version="1.2.0"))
    for b in (d.ConnectButton, d.DisconnectButton, d.TestSpotifyButton, d.SignInButton, d.TestYouTubeButton):
        assert not h.enabled(b) and b.ndDisabledReason == "Not sent: update the NeedleDropper app to 1.2.1 first."
    h2, d2 = opened(None, None)
    assert d2.TestYouTubeButton.ndDisabledReason == "Not sent: install the NeedleDropper app first (from the page you got NeedleDropper from)."
    assert h2.text(d2.StatusLines[1]).startswith("NeedleDropper app not found.")


def test_disconnect_needs_a_connection_and_asks_first():
    h, d = opened(None, app(spotify="not-connected", state="not-connected"))
    assert not h.enabled(d.DisconnectButton) and d.DisconnectButton.ndDisabledReason == "Spotify isn't connected."
    h, d = opened(None, app(clientId=None))                   # connected, but the app's ID isn't a 32-hex one
    assert d.DisconnectButton.ndDisabledReason == "Disconnect it on the app's Connect page."
    h, d = opened(None, app())
    h.click(d.DisconnectButton)
    assert h.popup("NEEDLEDROPPER_SPOTIFY_DISCONNECT") is not None and configs(h) == []
    h.popup_accept("NEEDLEDROPPER_SPOTIFY_DISCONNECT")
    cfg = configs(h)[-1]
    assert cfg["action"]["name"] == "disconnect-spotify" and cfg["clientId"] == CID and "prefs" not in cfg
    assert h.text(d.SpotifyFeedback).startswith("Sent (")


def test_youtube_buttons():
    h, d = opened(None, app())
    h.click(d.SignInButton)
    assert configs(h) == []
    h.popup_cancel("NEEDLEDROPPER_YOUTUBE_SIGNIN")
    assert configs(h) == []
    h.click(d.SignInButton)
    h.popup_accept("NEEDLEDROPPER_YOUTUBE_SIGNIN")
    assert configs(h)[-1]["action"]["name"] == "youtube-sign-in"
    h.click(d.TestYouTubeButton)
    assert configs(h)[-1]["action"]["name"] == "test-youtube"
    assert "top right corner" in h.text(d.YouTubeFeedback)
    h.click(d.TestSpotifyButton)
    fr = p2.frame_from_event(configs(h)[-1])
    assert fr.action.name == "test-spotify" and h.text(d.SpotifyFeedback).startswith("Sent (")


def test_how_songs_play_controls_write_the_settings():
    h, d = opened(None, app())
    items = [i["text"] for i in h.dropdown_items(d.Order)]
    assert items == ["Spotify, then YouTube", "YouTube, then Spotify", "Spotify only", "YouTube only",
                     "Neither: title cards and in-game files only"]
    h.dropdown_pick(d.Order, "YouTube only")
    h.dropdown_pick(d.Corner, "Bottom left")
    h.click(d.Fade)
    assert (h.settings()["musicOrder"], h.settings()["youtubeCorner"], h.settings()["musicFade"]) == \
        ("youtube", "bottomleft", False)
    assert h.account_db()["music"]["prefsSet"] is True and not h.checked(d.Fade)
    assert h.text(d.Choices) == "The NeedleDropper app follows this within a second while the game runs."
    assert h.text(d.StatusLines[2]).endswith("not used: Play music with is YouTube only")
    h.advance(1.1)
    assert configs(h)[-1]["prefs"] == {"order": "youtube", "corner": "bottomleft", "fade": False, "resume": True}


def test_refresh_reloads_once_and_the_signal_button_pings():
    h, d = opened(None, app())
    h.click(d.RefreshButton)
    assert int(h.ui_shim.reloads) == 1
    mark = h.mark()
    h.click(d.SignalButton)
    assert [e["type"] for e in h.events_since(mark)] == [ET.PING]


def test_app_text_is_shown_escaped():
    h, d = opened(None, app(account="|cffff0000x|r"))
    assert h.text(d.StatusLines[2]) == "Spotify: connected as ||cffff0000x||r (Premium)"
    assert h.bad_escapes() == []


def test_it_hides_in_combat_returns_after_and_joins_the_gamepad_ui():
    h = setup(app(), before_load=lambda hh: hh.shim.EnableGamepadUI(True))
    h, d = opened(h)
    assert h.UI._W.IsGamepadRegistered(d)
    h.combat(True)
    assert not h.shown(d)
    h.combat(False)
    assert h.shown(d) and h.UI._W.IsGamepadRegistered(d)
    h.click(d.CloseButton)
    h.combat(True)
    h.slash("music")                                          # asked in combat: opens when it ends
    assert not h.shown(d)
    h.combat(False)
    assert h.shown(d) and h.focused() is None
    assert list(h.ui_shim.blocked.values()) == []


def test_escape_closes_it():
    h, d = opened(None, app())
    assert h.escape() == "closed" and not h.shown(d)


# --------------------------------------------------------------- report --

def test_needle_report_shows_a_selected_plain_text_report():
    h = setup(app(), settings={"chatlog": True})
    h.call("MusicServices.SetClientId", CID)
    h.set_location(1429, "Goldshire")
    out = h.slash("report")
    d = h.g.NeedleDropperReportDialog
    assert h.shown(d) and "NeedleDropperReportDialog" in h.special_frames()
    assert out == ["NeedleDropper: report ready: press Ctrl+C to copy it from the box, then paste it into your bug "
                   "report. It holds no account names, Client IDs or tokens."]
    eb = d.Code.GetEditBox(d.Code)
    text = h.text(eb)
    assert h.has_focus(eb)                                    # mouse and keyboard: focused and selected
    assert list(eb.__highlight.values()) == [0, len(text.encode("utf-8"))]
    lines = text.split("\n")
    assert lines[0].startswith("NeedleDropper report, ")
    assert lines[1].startswith("Addon: 1.2.1; client 1.60.1 (build 70790, interface 16001)")
    assert any(line.startswith("App: 1.2.1, report written ") for line in lines)
    assert any("Spotify: connected, mode app, plan premium, Client ID set; YouTube: on, player chrome" in line
               for line in lines)
    assert any(line.startswith("Beacon: on, 3 px cells") for line in lines)
    assert any(line.startswith("Chat log: on") for line in lines)
    assert any(line.startswith("Music: play with spotify-youtube") for line in lines)
    assert any(line.startswith("Settings: enabled on, historyMode respect") for line in lines)
    assert lines[-1] == "Last error: none this session"
    for secret in ("Ethan", CID, "0123456789"):
        assert secret not in text
    assert "|" not in text
    h.type(eb, "edited")                                      # read-only
    assert h.text(eb) == text


def test_report_lists_the_last_five_journal_entries_and_the_last_error():
    journal = [{"id": "zone:%d" % i, "t": LOGIN - 1000 + i, "label": "Place %d" % i, "artist": "A%d" % i,
                "title": "T%d" % i, "youtube": "x"} for i in range(8)]
    h = setup(app(), char_db={"journal": journal}, location=(1, ""))   # nowhere with a drop: no new entry
    h.run("local ns = ... ns.NoteError('Director.lua:1: boom |cff')", h.ns)
    text = h.call1("SelfTest.ReportText")
    lines = text.split("\n")
    i = lines.index("Journal (last 5 of 8):")
    assert [line.split()[2] for line in lines[i + 1:i + 6]] == ["zone:3", "zone:4", "zone:5", "zone:6", "zone:7"]
    assert lines[-1].startswith("Last error (") and lines[-1].endswith("Director.lua:1: boom /cff")


def test_report_without_the_window_goes_to_chat():
    from addon_harness import AddonHarness, toc_files
    files = [f for f in toc_files() if not f.startswith("UI/")]
    h = AddonHarness(files=files, companion_data=app())
    out = h.slash("report")
    assert out[0].startswith("NeedleDropper: NeedleDropper report, ") and len(out) > 8
    assert not any("Ethan" in line for line in out)
