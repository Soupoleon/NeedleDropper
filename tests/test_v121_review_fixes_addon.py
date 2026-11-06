"""Regression tests for the v1.2.1 review findings fixed in the addon after the build (2026-11-06): the Music
services dialog's Client ID (INGAME-1), places whose song nothing could play (INGAME-2), the steady HELLO's
LOCAL_PLAYBACK after a CANCEL (INGAME-3), "Neither" and in-game files (INGAME-5), the YouTube player state
(INGAME-6), the playing HELLO before the next ARM (RP-3) and the allowlist state (ON-3)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import AddonHarness  # noqa: E402
from v12_ui_harness import UIHarness  # noqa: E402

from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

LOGIN = 1762365600
CID = "0123456789abcdef0123456789abcdef"
CID2 = "fedcba9876543210fedcba9876543210"

MUSIC = {"spotify": {"clientId": CID, "redirectUri": "http://127.0.0.1:8321/callback", "account": "Ethan",
                     "product": "premium", "state": "connected", "mode": "app"},
         "youtube": {"enabled": True, "player": "chrome"},
         "prefs": {"order": "spotify-youtube", "corner": "topright", "fade": True, "resume": True,
                   "fromGame": False}}


def app(version="1.2.1", spotify="connected", music=MUSIC, written=LOGIN - 60, youtube=True):
    d = {"schema": 1, "appVersion": version, "written": written,
         "status": {"spotify": spotify, "youtube": youtube, "transports": {"pixel": True, "chatlog": True}}}
    if music is not None:
        d["music"] = copy.deepcopy(music)
    return d


# ------------------------------------------------------------------ INGAME-1 --

def _settings_button(h, name):
    rows = {e.name: e for e in list(h.shim.settings.elements.values()) if getattr(e, "kind", None) == "button"}
    return rows[name]


def test_opening_the_music_dialog_never_makes_the_apps_client_id_the_games_own():
    """Review INGAME-1: the dialog prefilled the box with the app's Client ID and stored it on close, so after
    the player changed the ID on the Connect page, Settings row 2 ("Sign in again") sent the old ID and the
    app switched back to it. Only a typed ID is stored, the app's own is never sent back, and the status
    line shows the app's ID."""
    data = app()
    h = UIHarness(real=True, companion_data=data, location=(1637, ""))
    h.UI.OpenMusic()
    d = h.g.NeedleDropperMusicDialog
    assert h.text(d.ClientId) == CID  # the prefill
    h.click(d.CloseButton)  # the player only looked
    assert h.account_db()["music"].get("clientId") is None
    # later the Connect page signs in with another app (B): needs-relogin after the update
    data2 = app(spotify="error")
    data2["music"]["spotify"].update({"clientId": CID2, "state": "needs-relogin"})
    data2["written"] = int(h.lua("return time()")) + 60
    h.logout()
    h2 = UIHarness(real=True, db=h.account_db(), char_db=h.char_db(), companion_data=data2,
                   epoch=int(h.lua("return time()")) + 120, location=(1637, ""), login=False)
    h2.login(initial=False)
    mark = h2.mark()
    _settings_button(h2, "Spotify account").click()  # "Sign in again"
    sent = [e for e in h2.events_since(mark) if e["type"] == ET.CONFIG]
    assert [(e["action"]["name"], e.get("clientId")) for e in sent] == [("connect-spotify", None)]
    assert h2.errors() == []


def test_a_client_id_typed_in_game_is_sent_until_the_app_reports_it():
    """The first-time setup from the game still works: an ID typed in the box (the app has none yet) is stored
    and sent with Connect; once the app reports that ID, the stored copy is forgotten (the Connect page owns
    it from then on), and a different ID typed in this session is sent (the player means to switch)."""
    data = app(spotify="not-connected")
    data["music"]["spotify"].update({"clientId": None, "state": "not-connected", "account": None})
    h = UIHarness(real=True, companion_data=data, location=(1637, ""))
    h.UI.OpenMusic()
    d = h.g.NeedleDropperMusicDialog
    h.paste(d.ClientId, CID2)
    h.click(d.CloseButton)
    assert h.account_db()["music"].get("clientId") == CID2
    assert h.call1("MusicServices.StoredClientId") == CID2
    ev, _copy = h.call("MusicServices.Send", "connect-spotify")
    assert ev["clientId"] == CID2
    assert h.errors() == []


# ------------------------------------------------------------- INGAME-3, RP-3 --

def _drawn(h, seconds, step=0.25):
    """The distinct frames the beacon draws over ``seconds`` (sampled like the pixel transport)."""
    seen = []
    for _ in range(int(seconds / step)):
        fr = h.beacon_decoded()
        if fr is not None and (not seen or p2.encode_frame(seen[-1]) != p2.encode_frame(fr)):
            seen.append(fr)
        h.advance(step)
    return seen


def test_the_hello_after_a_cancel_of_another_place_keeps_the_playing_in_game_files_local_flag():
    """Review INGAME-3: a CANCEL of the place the player just left carries DROP_ACTIVE but not LOCAL_PLAYBACK
    (that flag describes the playing drop), so the steady HELLO drawn after it lost LOCAL_PLAYBACK; an app
    that missed the FIRE (F2) then started the playing drop's song late, over the in-game file."""
    from addon_harness import MEDIA_PATH, user_db
    from test_v12_engine_flows import FakeSpotify, FakeYouTube, Rig

    y = {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "durationMs": 200000,
         "file": "westfall.mp3", "youtube": "d_vPJjqWxZM", "spotify": "4w3tQBXhn5345eUXDGBWZG",
         "artist": "Dolly Parton", "title": "9 to 5"}
    x = {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "durationMs": 60000,
         "youtube": "mlM66FzjpgM", "artist": "Low Roar", "title": "I'll Keep Coming"}
    h = AddonHarness(sets=[], db=user_db([y, x]), companion_data=app(), location=(1637, ""),
                     settings={"chatlog": True, "localPlayback": True})
    h.state.playableFiles[MEDIA_PATH + "westfall.mp3"] = True
    h.set_location(1436, "")
    seen = _drawn(h, 15)                # ARM Y, FIRE Y (the in-game file plays), the playing HELLO
    h.set_location(1429, "")            # on into Elwynn: ARM X while Y plays
    seen += _drawn(h, 3)
    h.set_location(1436, "")            # back before X's dwell: CANCEL X
    seen += _drawn(h, 3)
    hello = seen[-1]
    assert hello.type == ET.HELLO and hello.has(Flags.DROP_ACTIVE) and hello.has(Flags.LOCAL_PLAYBACK)
    assert hello.place_key == p2.place_key_for("zone:1436")
    arm_y = next(f for f in seen if f.type == ET.ARM and f.spotify_uri)
    rig = Rig(spotify=FakeSpotify(), youtube=FakeYouTube())
    for fr in (arm_y, hello):           # the FIRE and its own playing HELLO were covered (F2)
        rig.engine.handle(p2.decode_frame(p2.encode_frame(fr)), "pixel")
    assert [c for c in rig.sp.calls if c[0] == "play"] == [] and [c for c in rig.yt.calls if c[0] == "play"] == []
    assert h.errors() == []


def test_the_playing_hello_is_drawn_before_the_next_arm_takes_the_screen():
    """Review RP-3: at a spot with a landmark drop and the zone drop (Razor Hill in Durotar) the Director arms
    the zone right after it fires the landmark, so the beacon went FIRE -> the zone ARM's song window -> the
    short ARM, and the HELLO with DROP_ACTIVE and the landmark's placeKey (the D3 late start of a covered
    FIRE) was never on screen. It now gets PLAYING_HOLD (3 s) right after the FIRE's hold."""
    from test_addon_e2e_durotar import APP, RAZOR_HILL

    h = AddonHarness(location=(1411, "Razor Hill"), state={"level": 1}, settings=None, companion_data=APP)
    ref = h.call1("Soundtracks.Create", "Durotar Road Trip")
    h.call1("Soundtracks.AddDrop", ref, RAZOR_HILL)
    h.rebuild()
    seen = _drawn(h, 25)
    kinds = [(ET(f.type).name, f.place_key, bool(f.has(Flags.DROP_ACTIVE))) for f in seen]
    fires = [i for i, f in enumerate(seen) if f.type == ET.FIRE]
    assert fires, kinds
    landmark = seen[fires[0]].place_key
    after = seen[fires[0] + 1]
    assert (after.type, after.place_key) == (ET.HELLO, landmark) and after.has(Flags.DROP_ACTIVE), kinds
    assert any(f.type == ET.ARM and f.place_key != landmark for f in seen[fires[0] + 2:]), kinds  # then the zone
    assert h.errors() == []


# ------------------------------------------------------ INGAME-2, INGAME-5, INGAME-6 --

def _done_how(h, drop_id="zone:1411"):
    c = h.char_db().get("done", {}).get(drop_id)
    a = h.account_db().get("accountDone", {}).get(drop_id)
    return (c or {}).get("how"), (a or {}).get("how")


def test_a_first_arrival_no_service_can_play_waits_for_the_service_and_plays_later():
    """Review INGAME-2: with the app there but no music service able to play the song (Spotify only while
    needs-relogin -- every 1.2.0 sign-in after the update -- no-player or not connected; YouTube only while it
    is off), only the title card showed and the place was marked "played", so it never played its song once
    the service worked. It is marked "card" (with why = "service") and plays after the sign-in."""
    from addon_harness import AddonHarness as H

    relogin = app(spotify="error")
    relogin["music"]["spotify"]["state"] = "needs-relogin"
    h = H(location=(1637, ""), settings={"musicOrder": "spotify"}, companion_data=relogin,
          db={"music": {"prefsSet": True, "requests": []}})
    h.set_location(1411, "")
    h.advance(40)
    assert len(h.events_of(ET.FIRE)) == 1 and _done_how(h) == ("card", "card")
    assert any("none of the music services" in line for line in h.prints()), h.prints()
    # signed in again on the Connect page, /reload, back in Durotar after the cooldown
    data = app()
    data["written"] = int(h.lua("return time()")) + 5
    h.set_location(1637, "")
    h.advance(11 * 60)
    h2 = h.reload(companion_data=data)
    n0 = len(h2.events_of(ET.FIRE))
    h2.set_location(1411, "")
    h2.advance(40)
    assert len(h2.events_of(ET.FIRE)) - n0 == 1 and _done_how(h2) == ("played", "played")
    # "Neither" is the player's own choice: the place counts as heard
    h3 = H(location=(1637, ""), settings={"musicOrder": "none"}, companion_data=app(),
           db={"music": {"prefsSet": True, "requests": []}})
    h3.set_location(1411, "")
    h3.advance(40)
    assert _done_how(h3) == ("played", "played")
    assert h.errors() == [] and h2.errors() == [] and h3.errors() == []


def test_neither_strips_the_arm_of_a_drop_with_an_in_game_file_too():
    """Review INGAME-5: the ARM of a drop with an in-game file was never stripped, so when that file then did
    not play (WoW's music off), the stripped FIRE merged with the cached ARM in the app and played the song
    under "Neither"."""
    from addon_harness import user_db

    y = {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "durationMs": 30000,
         "file": "westfall.mp3", "youtube": "d_vPJjqWxZM", "spotify": "4w3tQBXhn5345eUXDGBWZG",
         "artist": "Dolly Parton", "title": "9 to 5"}
    for order, stripped in (("none", True), ("spotify-youtube", False)):
        db = user_db([y])
        db["music"] = {"prefsSet": True, "requests": []}  # the music choices were set in game
        h = AddonHarness(sets=[], db=db, companion_data=app(), location=(1637, ""),
                         settings={"localPlayback": True, "musicOrder": order})
        h.set_location(1436, "")
        h.advance(30)
        arm = h.events_of(ET.ARM)[0]
        assert ("spotify" not in arm and "youtube" not in arm and "artist" not in arm) is stripped, (order, arm)
        assert h.errors() == []


def test_the_zone_music_is_not_faded_for_a_song_only_youtube_could_play_without_a_player_browser():
    """Review INGAME-6: music.youtube.player "none" (no Edge or Chrome) meant YouTube can't play, as the status
    said, but the plan still counted YouTube as ready, so the zone music faded into silence."""
    data = app(spotify="not-connected")
    data["music"]["spotify"].update({"state": "not-connected", "account": None})
    data["music"]["youtube"]["player"] = "none"
    h = AddonHarness(location=(1637, ""), companion_data=data)
    m0 = len(h.music())
    h.set_location(1411, "")
    h.advance(15)
    assert len(h.events_of(ET.FIRE)) == 1 and h.music()[m0:] == []
    plan = h.from_lua(h.ns.MusicServices.Plan(h.ns.Director.active.drop))
    assert plan["canPlay"] is False and plan["youtubeReady"] is False
    assert h.errors() == []


# ---------------------------------------------------------------------- ON-3 --

def test_the_game_says_add_the_account_under_user_management_not_sign_in_again():
    """Review ON-3: for an account Spotify refuses for the player's developer app the game said "sign in
    again" and Settings row 2 offered "Sign in again", which can't fix it. With music.spotify.problem
    "allowlist" the status line names User Management, the footer says so, and row 2 opens the dialog."""
    data = app(spotify="error")
    data["music"]["spotify"].update({"state": "error", "problem": "allowlist", "account": None, "product": None})
    h = UIHarness(real=True, companion_data=data, location=(1637, ""))
    assert h.from_lua(h.ns.Companion.Music())["spotify"]["problem"] == "allowlist"
    line = h.call1("MusicServices.SpotifyLine")
    assert "User Management" in line and "sign in again" not in line
    text, what = h.call("MusicServices.SpotifyButton")
    assert what == "open" and "Sign in again" not in text
    assert "not allowed" in h.call1("Companion.StatusLine")
    bad = app(spotify="error")
    bad["music"]["spotify"].update({"state": "error", "problem": "<script>"})
    h2 = AddonHarness(location=(1637, ""), companion_data=bad)
    assert "problem" not in h2.from_lua(h2.ns.Companion.Music())["spotify"]
    assert h.errors() == [] and h2.errors() == []
