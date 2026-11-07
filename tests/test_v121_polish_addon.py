"""v1.2.1 follow-up pass ("polish"), the addon's side: far-future time stamps from the app (INGAME-7), one limit
for repeated requests (INGAME-8, measured on the same clock: RG-2) and the cause of a missing in-app Spotify player
(ON-9, in /needle report too: RG-3)."""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v12_shell_music import NOW, Rig  # noqa: E402
from v12_ui_harness import UIHarness  # noqa: E402

from needledropper import gamedata, music_config, tray  # noqa: E402
from needledropper.providers import spotify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOGIN = 1762365600
CID = "0123456789abcdef0123456789abcdef"

MUSIC = {"spotify": {"clientId": CID, "redirectUri": "http://127.0.0.1:8321/callback", "account": "Ethan",
                     "product": "premium", "state": "connected", "mode": "app"},
         "youtube": {"enabled": True, "player": "chrome", "signInOpened": LOGIN - 3600},
         "prefs": {"order": "spotify-youtube", "corner": "topright", "fade": True, "resume": True,
                   "fromGame": False},
         "actions": [{"id": LOGIN - 300, "action": "test-youtube", "result": "playing", "text": "Playing.",
                      "at": LOGIN - 290}]}


def app(spotify="connected", written=LOGIN - 60, **music_spotify):
    music = copy.deepcopy(MUSIC)
    music["spotify"].update(music_spotify)
    return {"schema": 1, "appVersion": "1.2.1", "written": written,
            "status": {"spotify": spotify, "youtube": True, "transports": {"pixel": True, "chatlog": True}},
            "music": music}


# Everything in game that shows the app's times: the Music services status lines (dialog, Settings row 1's
# tooltip, /needle music status), the dialog and its refresh, the footer line and /needle report.
PROBE = r"""
local ns = ...
local out = {}
local function try(name, fn, ...)
  local ok, err = pcall(fn, ...)
  if not ok then out[#out + 1] = name .. ": " .. tostring(err) end
end
local MS = ns.MusicServices
try("StatusLines", MS.StatusLines)
try("StatusWord", MS.StatusWord)
try("SpotifyButton", MS.SpotifyButton)
try("StatusLine", ns.Companion.StatusLine)
try("ReportText", ns.SelfTest.ReportText)
try("MusicStatusTip", ns.Options.MusicStatusTip)
try("OpenMusic", ns.UI.OpenMusic)
try("Refresh", function() ns.UI._W.Music.Refresh() end)
return table.concat(out, "\n")
"""


def _probe(h) -> str:
    return h.run(PROBE, h.ns) or ""


# ------------------------------------------------------------------ INGAME-7 --

@pytest.mark.parametrize("bad", [1e300, 4.1e10, 99999999999])
def test_far_future_time_stamps_from_the_app_never_break_the_game(bad):
    """Review INGAME-7: written, music.youtube.signInOpened and an action's at were accepted as any whole number,
    and date() gives nothing past the client's calendar (year 3000 on Windows): the status lines, the dialog's
    refresh, /needle music status and /needle report raised. Companion.lua leaves such times out (the cap is
    2100-01-01, gamedata's too) and every date() caller tolerates a time it can't show."""
    data = app()
    data["written"] = bad
    data["music"]["youtube"]["signInOpened"] = bad
    data["music"]["actions"][0]["at"] = bad
    h = UIHarness(real=True, companion_data=data, location=(1637, ""), strict=False)
    st = h.from_lua(h.ns.Companion.Status())
    music = h.from_lua(h.ns.Companion.Music())
    assert st["written"] == 0                                       # "hasn't reported in yet"
    assert "signInOpened" not in music["youtube"] and "at" not in music["actions"][0]
    assert _probe(h) == ""
    h.call("MusicServices.Send", "test-spotify")                   # a request, so the Last request line shows
    assert _probe(h) == ""
    out = h.slash("music status") + h.slash("report")
    assert out and not any("error" in line.lower() and "lua" in line.lower() for line in out)
    assert h.errors() == []


def test_the_newest_time_the_game_takes_is_2100():
    assert gamedata.MAX_TIME == 4102444800
    h = UIHarness(real=True, companion_data=app(), location=(1637, ""))
    assert h.ns.Companion.MAX_TIME == gamedata.MAX_TIME
    data = app(written=gamedata.MAX_TIME)
    data["music"]["youtube"]["signInOpened"] = gamedata.MAX_TIME + 1
    h = UIHarness(real=True, companion_data=data, location=(1637, ""))
    assert h.from_lua(h.ns.Companion.Status())["written"] == gamedata.MAX_TIME
    assert "signInOpened" not in h.from_lua(h.ns.Companion.Music())["youtube"]
    # the app's own validator: refused when written, dropped when read back
    with pytest.raises(gamedata.GameDataError):
        gamedata.normalize_companion_data({"written": gamedata.MAX_TIME + 1})
    with pytest.raises(gamedata.GameDataError, match="signInOpened"):
        gamedata.normalize_companion_data({"music": {"youtube": {"signInOpened": 99999999999}}})
    with pytest.raises(gamedata.GameDataError, match="at"):
        gamedata.normalize_companion_data({"music": {"actions": [{"id": 1, "action": "test-spotify",
                                                                  "result": "playing", "at": 4.1e10}]}})
    assert gamedata._lenient_music({"youtube": {"player": "chrome", "signInOpened": 1e300}}) == \
        {"youtube": {"player": "chrome"}}
    # a PC clock past 2100 still writes (capped), it never stops every write
    assert gamedata.normalize_companion_data({}, now=99999999999)["written"] == gamedata.MAX_TIME


@pytest.mark.parametrize("how", ["nil", "raise"])
def test_every_date_caller_tolerates_a_time_it_cannot_show(how):
    """The cap makes it unreachable from the app's data, but the game's own saved requests and journal reach
    date() too: a client whose date() gives nil (or raises) for a time shows "?" instead of failing."""
    h = UIHarness(real=True, companion_data=app(), location=(1637, ""), strict=False)
    h.call("MusicServices.Send", "test-youtube")
    body = "return nil" if how == "nil" else "error('time out of range')"
    h.run("local ns = ... _G.date = function(fmt, t) if t ~= nil then %s end return '2026-11-06 12:00' end" % body,
          h.ns)
    assert _probe(h) == ""
    lines = h.call1("MusicServices.StatusLines")
    assert "report from ?" in lines[0] and "sign-in page opened ?" in lines[2] and "(?)" in lines[3]
    assert "report written ?" in h.call1("SelfTest.ReportText")
    assert h.errors() == []


# ------------------------------------------------------------------ INGAME-8 --

def test_the_game_and_the_app_have_one_limit_for_repeated_requests():
    """Review INGAME-8: the game re-enabled a request after 5 s and said "Sent", the app refused it for 10 s and
    showed "ignored a repeated request". One limit, read from both sources (as test_core_version does)."""
    lua = (ROOT / "addon" / "NeedleDropper" / "MusicServices.lua").read_text(encoding="utf-8")
    m = re.search(r"^MS\.SEND_GAP = ([0-9.]+)\s*$", lua, re.M)
    assert m, "MusicServices.lua sets no MS.SEND_GAP"
    assert float(m.group(1)) == music_config.ACTION_INTERVAL_S == 10.0
    h = UIHarness(real=True, companion_data=app(), location=(1637, ""))
    assert h.ns.MusicServices.SEND_GAP == music_config.ACTION_INTERVAL_S
    assert h.call1("MusicServices.Send", "test-youtube") is not None
    h.advance(6)                                                     # the app would answer "busy" now
    assert h.call("MusicServices.Send", "test-youtube")[1] == "Just sent: give it a moment."
    h.advance(4.1)
    assert h.call1("MusicServices.Send", "test-youtube") is not None


# How long after its click the app read each request: pixel reads at 4 Hz (two identical samples), a frame queued
# behind another one (a PREFS-only CONFIG holds 2 s), only the beacon's re-show 8 s later.
DELAYS = [(0.3, 0.3), (0.5, 0.25), (0.45, 0.3), (1.2, 0.3), (8.3, 0.3), (0.3, 8.3)]


@pytest.mark.parametrize("d1, d2", DELAYS)
def test_a_request_the_game_sends_again_as_its_button_comes_back_is_never_refused(tmp_path, d1, d2):
    """Review RG-2: the same 10 s on two clocks. The game counts from the click (GetTime) and gives its buttons
    back 10.05 s later; the app counted from when each request reached it, so a second click right then was
    "Sent" in game and "busy" (ignored a repeated request) in the app whenever the first one travelled slower.
    The app now measures the game's requests by their click (issuedAt: the same PC clock)."""
    h = UIHarness(real=True, companion_data=app(), location=(1637, ""))
    clicks = []
    for _ in range(2):
        wall = float(h.shim.EPOCH + (h.shim.now - h.shim.START))     # the PC clock as the player clicks
        assert h.call1("MusicServices.Send", "test-youtube") is not None   # "Sent" in game
        clicks.append((wall, int(h.from_lua(h.ns.MusicServices.LastRequest())["at"])))
        h.advance(music_config.ACTION_INTERVAL_S + 0.05)             # the buttons come back (SEND_GAP + 0.05)
    rig = Rig(tmp_path)
    for (wall, at), delay in zip(clicks, (d1, d2)):
        rig.wall.t = rig.mono.t = wall + delay                       # (both clocks run at the same pace)
        rig.act("test-youtube", at=at)
    assert len(rig.app.engine.played) == 2
    assert rig.results("test-youtube") == [] and rig.notes(tray.NOTE_MUSIC_BUSY) == []


def test_the_game_requests_limit_counts_from_the_click(tmp_path):
    """Measured by the click, the limit still holds: a request clicked within 10 s of one that ran is refused,
    however late it arrives -- also one the chat log brings after a newer one ran -- and two clicks 10 s apart
    both run in whichever order they arrive. A clock set back forgets the clicks before it, and the Connect page's
    requests keep the app's own clock."""
    rig = Rig(tmp_path)
    rig.act("test-youtube", at=NOW)                                  # ran
    rig.later(11)
    rig.act("test-youtube", at=NOW + 6)                              # 11 s later by the app's clock: still too soon
    assert len(rig.app.engine.played) == 1 and [r["result"] for r in rig.results("test-youtube")] == ["busy"]
    rig.act("test-spotify", at=NOW + 15)                             # the newer click arrives first ...
    rig.later(1)
    rig.act("test-spotify", at=NOW + 5)                              # ... the one 10 s before it 1 s after (chat log)
    assert len(rig.app.engine.played) == 3
    rig.act("test-spotify", at=NOW + 9)                              # 4 and 6 s from those two
    assert len(rig.app.engine.played) == 3 and [r["result"] for r in rig.results("test-spotify")] == ["busy"]
    rig.wall.t -= 3600                                               # the PC clock was set back an hour
    rig.later(1)
    rig.act("test-youtube", at=int(rig.wall.t))
    assert len(rig.app.engine.played) == 4
    rig.later(3)
    rig.music.request("test-youtube")                                # the Connect page, 3 s after the game's
    rig.music.drain()
    assert len(rig.app.engine.played) == 4 and rig.results("test-youtube")[-1]["result"] == "busy"


# --------------------------------------------------------------------- RG-3 --

@pytest.mark.parametrize("state, problem", [("no-player", p) for p in spotify.NO_PLAYER_PROBLEMS]
                         + [("error", "allowlist")])
def test_the_bug_report_names_the_cause_the_app_reported(state, problem):
    """Review RG-3: /needle report said "Spotify: no-player" without the cause the game already had (Chrome, not
    ready, no window, switched off, the SDK not loading; the allowlist), the one field that tells which fix
    applies. It is one of the checker's fixed codes: never an account name, a Client ID or a "|"."""
    h = UIHarness(real=True, companion_data=app(spotify="error", state=state, problem=problem), location=(1637, ""))
    report = h.call1("SelfTest.ReportText")
    line = next(x for x in report.split("\n") if x.strip().startswith("Spotify:"))
    assert line.strip().startswith(f"Spotify: {state}, mode app, plan premium, cause {problem}, Client ID set;")
    assert "|" not in report and "Ethan" not in report and CID not in report
    assert h.errors() == []


@pytest.mark.parametrize("problem", [None, "a-future-cause", "<b>"])
def test_the_bug_report_names_no_cause_without_a_known_one(problem):
    extra = {"state": "no-player"}
    if problem is not None:
        extra["problem"] = problem
    h = UIHarness(real=True, companion_data=app(spotify="error", **extra), location=(1637, ""))
    report = h.call1("SelfTest.ReportText")
    assert "cause" not in report and "Spotify: no-player, mode app, plan premium, Client ID set;" in report
    assert h.errors() == []


# --------------------------------------------------------------------- ON-9 --

NO_PLAYER = {
    "no-chrome": ("Spotify: install Google Chrome to play Spotify in NeedleDropper (if it is installed, wait a minute "
                  "for its Widevine component).", "Spotify needs Google Chrome", "Needs Chrome…"),
    "not-ready": ("Spotify: NeedleDropper's player didn't get ready. The app tries again by itself (its Connect page "
                  "says more).", "Spotify player not ready", "Check Spotify…"),
    "no-window": ("Spotify: NeedleDropper's player isn't running yet. The app starts it when World of Warcraft isn't "
                  "the window in front (start the app before the game).", "Spotify player not started yet",
                  "Check Spotify…"),
    "window-never": ("Spotify: NeedleDropper's player is switched off (deck.autoOpen is \"never\" in the app's "
                     "config.json).", "Spotify player switched off", "Check Spotify…"),
    "sdk-load": ("Spotify: Spotify's player didn't load in NeedleDropper (no internet, or a firewall blocks it).",
                 "Spotify player didn't load", "Check Spotify…"),
    None: ("Spotify: NeedleDropper's player isn't ready (the app's Connect page says why).", "Spotify player not ready",
           "Check Spotify…"),
    "a-future-cause": ("Spotify: NeedleDropper's player isn't ready (the app's Connect page says why).",
                       "Spotify player not ready", "Check Spotify…"),
}


@pytest.mark.parametrize("problem", list(NO_PLAYER))
def test_the_game_names_why_in_app_spotify_has_no_player(problem):
    """Review ON-9: one "needs Google Chrome" wording covered four causes, so a player with Chrome was told to
    install it. The app names the cause (music.spotify.problem); the status line, the footer and Settings row 2
    follow it, only "no-chrome" mentions Chrome, and an older app (no cause) or an unknown one gets a neutral
    text -- an unknown code never fails the checker."""
    line, footer, button = NO_PLAYER[problem]
    extra = {"state": "no-player"}
    if problem is not None:
        extra["problem"] = problem
    h = UIHarness(real=True, companion_data=app(spotify="error", **extra), location=(1637, ""))
    known = problem in gamedata.MUSIC_SPOTIFY_PROBLEMS
    assert h.from_lua(h.ns.Companion.Music())["spotify"].get("problem") == (problem if known else None)
    assert h.call1("MusicServices.SpotifyLine") == line
    assert footer in h.call1("Companion.StatusLine")
    assert h.call("MusicServices.SpotifyButton") == (button, "open")
    if problem != "no-chrome":
        assert "Chrome" not in line + footer + button
    h.UI.OpenMusic()
    assert h.text(h.g.NeedleDropperMusicDialog.StatusLines[2]) == line
    assert h.errors() == []


def test_the_problem_codes_are_the_same_in_the_game_and_the_app():
    h = UIHarness(real=True, companion_data=app(), location=(1637, ""))
    lua = h.from_lua(h.ns.Companion.SPOTIFY_PROBLEMS)
    assert tuple(lua) == gamedata.MUSIC_SPOTIFY_PROBLEMS == spotify.MUSIC_PROBLEMS
    assert spotify.NO_PLAYER_PROBLEMS == tuple(p for p in gamedata.MUSIC_SPOTIFY_PROBLEMS if p != "allowlist")
    for problem in spotify.NO_PLAYER_PROBLEMS:
        assert problem in h.ns.MusicServices.NO_PLAYER_LINES
