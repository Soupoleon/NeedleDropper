"""music_config.MusicConfig (DESIGN-v1.2.1 3.1-3.8, 5.3 item 2): the music choices and requests the game
sends, against a fake companion (clock, notifier, opener and launcher are fakes; nothing opens a window).

Frames are built and decoded with the real codec, as a transport delivers them."""
from __future__ import annotations

import random
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from needledropper import config, gamedata as gd, music_config as mc, protocol2 as p2, tray  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

NOW = 1_762_380_000.0
CID = "0123456789abcdef0123456789abcdef"
OTHER = "ffffffffffffffffffffffffffffffff"


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Icon:
    """Records notifications in order with the app's pushes (the flush comes first)."""

    def __init__(self, events: list) -> None:
        self.events = events

    def notify(self, title: str, text: str, *, kind: str = "info") -> bool:
        self.events.append(("note", title, text, kind))
        return True


class Session:
    def __init__(self, cid: str) -> None:
        self.client_id = cid
        self.authorize_url = f"https://accounts.spotify.com/authorize?client_id={cid}&state=s"
        self.state = "waiting"
        self.error: Optional[str] = None
        self.message = "Waiting."
        self.account: Optional[str] = None
        self.profile: Optional[dict] = None
        self.warning: Optional[str] = None

    @property
    def done(self) -> bool:
        return self.state != "waiting"

    def finish(self, state: str, *, error: Optional[str] = None, message: str = "", account: Optional[str] = None,
               product: Optional[str] = None, warning: Optional[str] = None) -> None:
        self.state, self.error, self.message, self.account, self.warning = state, error, message, account, warning
        self.profile = {"name": account, "product": product}


class Provider:
    def __init__(self, name: str, *, ok: bool = True, reason: Optional[str] = None) -> None:
        self.name, self.ok, self.reason = name, ok, reason

    def available(self) -> bool:
        return self.ok

    def unavailable_reason(self) -> Optional[str]:
        return None if self.ok else self.reason

    def stuck_notice(self) -> tuple[str, str]:
        return ("Spotify didn't start", "Spotify didn't start in NeedleDropper's player.")


class Engine:
    def __init__(self) -> None:
        self.played: list[tuple] = []
        self.playing: Optional[dict] = None

    def play_now(self, drop: Any, *, test: bool = True, only: Optional[str] = None) -> None:
        self.played.append((drop, test, only))

    def status(self) -> dict:
        return {"playing": self.playing}


class Deck:
    def __init__(self, tmp_path: Path) -> None:
        self.opened: list[str] = []
        self.launched: list[list[str]] = []
        self.page_open = True
        self.auto_open = "youtube"
        self.browser_kind = "chrome"
        self.profile_dir = tmp_path / "deck-browser"
        self.ensure_calls: list[bool] = []
        self.ensure_result = True
        self.proc: Any = None

    def url_opener(self, url: str) -> bool:
        self.opened.append(url)
        return True

    def launcher(self, args: list[str]) -> Any:
        self.launched.append(list(args))
        return self.proc

    def player_browser(self) -> str:
        return self.browser_kind

    def ensure_player(self, timeout: float = 8.0, *, force: bool = False) -> bool:
        self.ensure_calls.append(force)
        return self.ensure_result


class App:
    """The companion as MusicConfig sees it (see music_config's module docs)."""

    def __init__(self, tmp_path: Path, cfg: Optional[dict] = None) -> None:
        self.cfg = config.normalize_config(config.deep_merge(config.DEFAULT_CONFIG, cfg or {}))
        addons = tmp_path / "Interface" / "AddOns"
        addons.mkdir(parents=True, exist_ok=True)
        self.paths = SimpleNamespace(addons_dir=addons, app_dir=tmp_path)
        self.deck = Deck(tmp_path)
        self.engine = Engine()
        self.providers = [Provider("spotify"), Provider("youtube")]
        self.paused = False
        self.events: list = []
        self.notifier = tray.Notifier(Icon(self.events))
        self.writes: list[dict] = []
        self.sessions: list[Session] = []
        self.done: list[Any] = []
        self.disconnects = 0
        self.signin_at: Optional[int] = None
        self.connect_error: Optional[Exception] = None

    def apply_music_choices(self, changes: dict) -> list[str]:
        self.writes.append(changes)
        self.cfg = config.normalize_config(config.deep_merge(self.cfg, changes))
        return []

    def connect_spotify_from_game(self, cid: str, on_done: Any) -> Session:
        if self.connect_error is not None:
            raise self.connect_error
        s = Session(cid)
        self.sessions.append(s)
        self.done.append(on_done)
        return s

    def current_login(self) -> Optional[Session]:
        return self.sessions[-1] if self.sessions else None

    def spotify_disconnect(self) -> dict:
        self.disconnects += 1
        return {"ok": True}

    def push_game_data(self, *, flush: bool = False) -> None:
        self.events.append(("push", flush))

    def note_youtube_sign_in(self, when: int) -> None:
        self.signin_at = when

    def nudge(self, what: str = "state") -> None:
        pass


class Rig:
    def __init__(self, tmp_path: Path, cfg: Optional[dict] = None) -> None:
        self.app = App(tmp_path, cfg)
        self.wall = Clock(NOW)
        self.mono = Clock(500.0)
        self.found: list[str] = []
        self.music = mc.MusicConfig(self.app, clock=self.wall, monotonic=self.mono,
                                    find_browser=lambda kind: (self.found.append(kind), f"C:/{kind}.exe")[1])
        self.seq = 0

    def frame(self, etype: ET = ET.CONFIG, **fields: Any) -> p2.Frame:
        self.seq += 1
        fields.setdefault("seq", self.seq % 256)
        return p2.decode_frame(p2.encode_frame(p2.build_frame(etype, **fields)))

    def send(self, etype: ET = ET.CONFIG, *, transport: str = "pixel", baseline: bool = False, **fields: Any):
        fr = self.frame(etype, **fields)
        assert fr is not None
        self.music.feed(fr, transport, baseline=baseline)
        self.music.drain()
        return fr

    def act(self, name: str, *, at: Optional[float] = None, transport: str = "pixel", **fields: Any) -> p2.Frame:
        return self.send(ET.CONFIG, transport=transport, action={"name": name, "at": NOW if at is None else at},
                         **fields)

    def results(self, action: Optional[str] = None) -> list[dict]:
        return [r for r in self.music.recent() if action is None or r["action"] == action]

    def notes(self, kind: str = tray.NOTE_MUSIC) -> list:
        return [n for n in self.app.notifier.history if n.kind == kind]

    def game_actions(self) -> list[dict]:
        gd.flush(self.app.paths.addons_dir)
        data = gd.read_companion_data(self.app.paths.addons_dir) or {}
        return list((data.get("music") or {}).get("actions") or [])

    def later(self, s: float) -> None:
        self.wall.t += s
        self.mono.t += s


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


PREFS = {"order": "youtube-spotify", "corner": "topleft", "fade": False, "resume": False}


# ------------------------------------------------------------------ PREFS --

def test_prefs_map_onto_config_json(rig):
    rig.app.cfg["deck"]["playerPosition"] = "10,10"
    rig.send(ET.HELLO, prefs=PREFS)
    assert rig.app.writes == [{"music": {"order": "youtube-spotify", "fade": False, "fromGame": True},
                               "deck": {"playerCorner": "topleft", "playerPosition": ""},
                               "spotify": {"restorePrevious": False}}]
    assert config.music_choices(rig.app.cfg) == {"order": "youtube-spotify", "corner": "topleft", "fade": False,
                                                 "resume": False, "fromGame": True}
    assert ("push", False) in rig.app.events                            # the music.prefs echo for the game


def test_a_thousand_identical_deliveries_make_one_write(rig):
    for i in range(1000):
        rig.send(ET.HELLO if i % 2 else ET.CONFIG, prefs=PREFS, transport="pixel" if i % 3 else "chatlog")
    assert len(rig.app.writes) == 1 and rig.music.stats.prefs_seen == 1000


def test_fields_not_set_or_unknown_change_nothing(rig):
    rig.send(ET.HELLO, prefs={"order": "youtube"})                  # corner, fade, resume not set
    assert rig.app.writes == [{"music": {"order": "youtube", "fromGame": True}}]
    unknown = p2.Frame(type=ET.HELLO, prefs=p2.Prefs.from_codes(bytes((9, 9, 9, 9))))
    assert unknown.prefs == p2.Prefs()                              # a newer addon's values: none known
    rig.music.feed(unknown, "pixel")
    rig.music.drain()
    assert len(rig.app.writes) == 1


def test_baseline_prefs_apply_and_the_connect_page_switches_are_untouched(rig):
    rig.app.cfg["spotify"]["enabled"] = False
    rig.app.cfg["youtube"]["enabled"] = False
    rig.send(ET.HELLO, baseline=True, prefs={"order": "spotify", "corner": "bottomright", "fade": True,
                                             "resume": True})
    assert rig.app.cfg["music"]["order"] == "spotify" and rig.app.cfg["deck"]["playerCorner"] == "bottomright"
    assert rig.app.cfg["spotify"]["enabled"] is False and rig.app.cfg["youtube"]["enabled"] is False
    assert "providers" not in rig.app.writes[0]


def test_player_position_is_cleared_only_by_a_corner_change(rig):
    rig.app.cfg["deck"]["playerPosition"] = "100,200"
    rig.send(ET.HELLO, prefs={"order": "youtube", "corner": "topright", "fade": True, "resume": True})
    assert rig.app.cfg["deck"]["playerPosition"] == "100,200"       # the corner did not change
    rig.send(ET.HELLO, prefs={"order": "youtube", "corner": "bottomleft", "fade": True, "resume": True})
    assert rig.app.cfg["deck"]["playerPosition"] == ""


def test_prefs_in_song_frames_are_ignored(rig):
    fr = p2.Frame(type=ET.FIRE, place_key=1, youtube_id="QAo_Ycocl1E", prefs=p2.Prefs(order="none"))
    assert rig.music.feed(fr, "pixel") is False
    rig.music.drain()
    assert rig.app.writes == []


# ----------------------------------------------------------------- actions --

@pytest.mark.parametrize("age, runs", [(-6, False), (-5, True), (45, True), (46, False)])
def test_freshness(rig, age, runs):
    rig.act("test-youtube", at=NOW - age)
    assert bool(rig.app.engine.played) is runs and bool(rig.music.pending_tests()) is runs
    assert rig.results() == []                                      # a stale request has no result at all


def test_one_request_runs_once_across_pixel_chat_and_the_re_show(rig):
    for transport in ("pixel", "chatlog", "pixel"):
        rig.act("test-youtube", transport=transport)
    rig.later(8)
    rig.act("test-youtube")                                         # the beacon's second showing, 8 s later
    assert len(rig.app.engine.played) == 1
    assert rig.results() == [] and rig.music.pending_tests() == ["test-youtube"]  # duplicates: silent, no "busy"


def test_each_action_at_most_once_per_ten_seconds(rig):
    rig.act("test-youtube", at=NOW)
    rig.later(6)
    rig.act("test-youtube", at=NOW + 6)
    assert len(rig.app.engine.played) == 1
    assert [r["result"] for r in rig.results("test-youtube")][-1] == "busy"
    busy = rig.notes(tray.NOTE_MUSIC_BUSY)
    assert len(busy) == 1 and "repeated request" in busy[0].text
    rig.later(5)
    rig.act("test-youtube", at=NOW + 11)
    assert len(rig.app.engine.played) == 2
    rig.later(2)
    rig.act("test-spotify", at=NOW + 13)                            # another action: its own limit
    assert len(rig.app.engine.played) == 3


def test_page_opening_actions_at_most_five_per_ten_minutes(rig):
    rig.app.deck.page_open = True
    for i in range(6):
        rig.act("youtube-sign-in", at=NOW + 11 * i)
        rig.later(11)
    assert len(rig.app.deck.launched) == 5
    assert rig.results("youtube-sign-in")[-1]["result"] == "busy"
    rig.later(600)
    rig.act("youtube-sign-in", at=rig.wall.t)
    assert len(rig.app.deck.launched) == 6


def test_baseline_actions_and_unknown_codes_are_ignored(rig):
    rig.send(ET.CONFIG, baseline=True, action={"name": "test-youtube", "at": NOW})
    unknown = p2.Frame(type=ET.CONFIG, seq=1, action=p2.Action(77, int(NOW)))
    rig.music.feed(unknown, "pixel")
    rig.music.drain()
    assert rig.app.engine.played == [] and rig.results() == []


def test_while_paused_tests_answer_paused_and_the_rest_runs(rig):
    rig.app.paused = True
    rig.act("test-spotify")
    assert rig.app.engine.played == [] and rig.results("test-spotify")[0]["result"] == "paused"
    rig.act("youtube-sign-in")
    assert rig.app.deck.launched and rig.results("youtube-sign-in")[0]["result"] == "opened"
    rig.act("disconnect-spotify", client_id=CID)
    assert rig.results("disconnect-spotify")[0]["result"] == "ignored"   # no Client ID configured here


# ---------------------------------------------------------------- connect --

def test_connect_opens_spotifys_page_and_reports_the_outcome(rig):
    rig.act("connect-spotify", client_id=CID)
    s = rig.app.sessions[0]
    assert s.client_id == CID and rig.app.deck.opened == [s.authorize_url]
    first = rig.results("connect-spotify")
    assert [r["result"] for r in first] == ["opened"] and "Approve NeedleDropper" in first[0]["text"]
    assert rig.notes()[0].key == f"connect-spotify:{int(NOW)}:opened"
    rig.app.notifier.clicked()                                  # "it opened behind the game": the same page again
    assert rig.app.deck.opened == [s.authorize_url] * 2
    s.finish("connected", account="Ethan", product="premium")
    rig.app.done[0](s)
    rig.music.drain()
    final = rig.results("connect-spotify")
    assert len(final) == 1 and final[0]["result"] == "connected"
    assert final[0]["text"].startswith("Spotify connected as Ethan (Premium).")
    assert [a["result"] for a in rig.game_actions()] == ["connected"]       # the interim one was replaced
    # the flush comes before the final notification (a Refresh status right after it shows the news)
    events = [e[0] if e[0] == "push" and e[1] else e[0] for e in rig.app.events if e[0] in ("push", "note")]
    assert events[-2:] == ["push", "note"] and ("push", True) in rig.app.events


@pytest.mark.parametrize("finish, result, needle", [
    ({"state": "connected", "account": "Thrall", "product": "free"}, "connected", "Spotify Free"),
    ({"state": "connected", "account": "Thrall", "warning": "Add the account under User Management."},
     "connected", "User Management"),
    ({"state": "error", "error": "access_denied", "message": "declined"}, "declined", "You declined"),
    ({"state": "timeout", "error": "timeout", "message": "timed out"}, "timeout", "http://127.0.0.1:8321/callback"),
    ({"state": "error", "error": "invalid_client", "message": "Spotify reported an error: INVALID_CLIENT"},
     "error", "INVALID_CLIENT"),
])
def test_every_session_outcome_has_its_result(rig, finish, result, needle):
    rig.act("connect-spotify", client_id=CID)
    s = rig.app.sessions[0]
    s.finish(**finish)
    rig.app.done[0](s)
    rig.music.drain()
    r = rig.results("connect-spotify")[-1]
    assert r["result"] == result and needle in r["text"]
    assert rig.notes()[-1].key == f"connect-spotify:{int(NOW)}:result"


def test_connect_without_any_client_id_or_with_a_busy_port(rig):
    rig.act("connect-spotify")
    assert rig.results()[-1]["result"] == "no-client-id" and rig.app.sessions == []
    from needledropper.providers.spotify_auth import SpotifyAuthError

    rig.app.connect_error = SpotifyAuthError("busy", error="port_in_use")
    rig.later(11)
    rig.act("connect-spotify", at=rig.wall.t, client_id=CID)
    r = rig.results()[-1]
    assert r["result"] == "port-in-use" and "port 8321" in r["text"]


def test_connect_uses_the_configured_id_when_the_game_sends_none(tmp_path):
    r = Rig(tmp_path, {"spotify": {"clientId": CID}})
    r.act("connect-spotify")
    assert r.app.sessions[0].client_id == CID


def test_a_second_connect_while_the_sign_in_waits_reopens_the_same_page(rig):
    rig.act("connect-spotify", client_id=CID)
    rig.later(11)
    rig.act("connect-spotify", at=rig.wall.t, client_id=CID)
    assert len(rig.app.sessions) == 1                                  # no new session
    assert rig.app.deck.opened == [rig.app.sessions[0].authorize_url] * 2
    s = rig.app.sessions[0]
    s.finish("connected", account="Ethan", product="premium")
    rig.app.done[0](s)
    rig.music.drain()
    assert [r["result"] for r in rig.results("connect-spotify")] == ["connected", "connected"]  # both requests


def test_a_connect_that_reopens_the_connect_pages_sign_in_still_gets_its_result(rig):
    page = Session(CID)
    rig.app.sessions.append(page)                                      # started on the Connect page, waiting
    rig.act("connect-spotify", client_id=CID)
    assert rig.app.deck.opened == [page.authorize_url] and rig.app.sessions == [page]
    page.finish("connected", account="Ethan", product="premium")
    rig.music.drain()                                                  # nobody tells it: the worker looks
    res = rig.results("connect-spotify")
    assert len(res) == 1 and res[0]["result"] == "connected"


# ------------------------------------------------------------- disconnect --

def test_disconnect_needs_the_matching_client_id(tmp_path):
    r = Rig(tmp_path, {"spotify": {"clientId": CID}})
    r.act("disconnect-spotify", client_id=OTHER)
    assert r.app.disconnects == 0 and r.results()[-1]["result"] == "ignored" and "\u2026ffff" in r.results()[-1]["text"]
    r.later(11)
    r.act("disconnect-spotify", at=r.wall.t)                              # no ID at all
    assert r.app.disconnects == 0 and r.results()[-1]["result"] == "ignored"
    r.later(11)
    r.act("disconnect-spotify", at=r.wall.t, client_id=CID.upper())       # case does not matter
    assert r.app.disconnects == 1 and r.results()[-1]["result"] == "disconnected"
    assert "Your Client ID stays" in r.results()[-1]["text"]


# ------------------------------------------------------------------ tests --

def test_test_spotify_plays_the_test_song_in_the_app_whatever_the_order(rig):
    rig.app.cfg["music"]["order"] = "none"
    rig.act("test-spotify")
    drop, test, only = rig.app.engine.played[0]
    assert (drop.id, drop.kind, drop.spotify, drop.artist, drop.title) == \
        ("test-spotify", "test", mc.TEST_SPOTIFY_URI, "Toto", "Africa")
    assert (drop.startMs, drop.durationMs, test, only) == (30000, 15000, True, "spotify")
    assert rig.results() == []                                        # waiting for the engine
    rig.music.on_engine_note({"kind": "playing", "dropId": "test-spotify", "provider": "spotify"})
    rig.music.drain()
    assert rig.results()[-1] == {**rig.results()[-1], "result": "playing",
                                 "text": "Spotify test: playing Toto \u2013 Africa for 15 seconds."}


def test_test_youtube_opens_the_box_and_names_its_corner(rig):
    rig.app.cfg["deck"]["playerCorner"] = "bottomleft"
    rig.app.deck.page_open = False
    rig.act("test-youtube")
    assert rig.app.deck.ensure_calls == [True]                         # force: even with autoOpen "never"
    drop, _test, only = rig.app.engine.played[0]
    assert (drop.id, drop.youtube, only) == ("test-youtube", mc.TEST_YOUTUBE_ID, "youtube")
    rig.music.on_engine_note({"kind": "playing", "dropId": "test-youtube"})
    rig.music.drain()
    assert "the box in the bottom left corner" in rig.results()[-1]["text"]


@pytest.mark.parametrize("setup, result, needle", [
    (lambda a: a.providers.remove(a.providers[0]), "unavailable", "switched off"),
    (lambda a: setattr(a.providers[0], "ok", False), "unavailable", "isn't connected"),
    (lambda a: (setattr(a.providers[0], "ok", False), setattr(a.providers[0], "reason", "Sign in to Spotify again."))
     , "unavailable", "Sign in to Spotify again"),
    (lambda a: setattr(a.engine, "playing", {"id": "zone:1", "test": False}), "busy", "A needle drop is playing"),
    (lambda a: setattr(a.engine, "playing", {"id": "zone:1", "test": False, "local": True}), "busy",
     "A needle drop is playing"),                                      # an in-game file is a real drop too
])
def test_test_spotify_refusals(rig, setup, result, needle):
    setup(rig.app)
    rig.act("test-spotify")
    assert rig.app.engine.played == []
    assert rig.results()[-1]["result"] == result and needle in rig.results()[-1]["text"]


def test_test_youtube_refusals(rig):
    rig.app.deck.page_open = False
    rig.app.deck.ensure_result = False                                # no window while the game is in front
    rig.act("test-youtube")
    assert rig.app.engine.played == [] and "isn't running yet" in rig.results()[-1]["text"]
    rig.app.providers = [p for p in rig.app.providers if p.name != "youtube"]
    rig.later(11)
    rig.act("test-youtube", at=rig.wall.t)
    assert rig.results()[-1]["result"] == "unavailable" and "switched off" in rig.results()[-1]["text"]


def test_a_running_test_is_replaced_by_the_other_one(rig):
    rig.app.engine.playing = {"id": "test-youtube", "test": True}
    rig.act("test-spotify")
    assert rig.app.engine.played


@pytest.mark.parametrize("note, needle", [
    ({"kind": "noSource", "reasons": ["spotify: Spotify refused the song (403)"]}, "Spotify refused the song (403)."),
    ({"kind": "spotifyStuck", "provider": "spotify", "detail": "no progress"}, "didn't start in NeedleDropper"),
])
def test_test_failures_say_why(rig, note, needle):
    rig.act("test-spotify")
    rig.music.on_engine_note({**note, "dropId": "test-spotify"})
    rig.music.drain()
    r = rig.results()[-1]
    assert r["result"] == "failed" and needle in r["text"]
    assert "YouTube wasn't tried" in r["text"]


def test_a_youtube_test_the_video_refuses(rig):
    rig.act("test-youtube")
    rig.music.on_engine_note({"kind": "notEmbeddable", "dropId": "test-youtube", "code": 150})
    rig.music.drain()
    assert rig.results()[-1]["result"] == "failed" and "error 150" in rig.results()[-1]["text"]


def test_no_answer_within_fifteen_seconds(rig):
    rig.act("test-spotify")
    rig.later(14)
    rig.music.drain()
    assert rig.results() == []
    rig.later(1.5)
    rig.music.drain()
    r = rig.results()[-1]
    assert r["result"] == "failed" and "No answer from Spotify within 15 seconds" in r["text"]
    rig.music.on_engine_note({"kind": "playing", "dropId": "test-spotify"})    # too late: no second result
    rig.music.drain()
    assert len(rig.results()) == 1


# ---------------------------------------------------------------- sign-in --

def test_youtube_sign_in_opens_a_normal_window_of_the_players_profile(rig):
    rig.act("youtube-sign-in")
    args = rig.app.deck.launched[0]
    assert args == ["C:/chrome.exe", f"--user-data-dir={rig.app.deck.profile_dir}", "--no-first-run",
                    "--no-default-browser-check", "--new-window", mc.YOUTUBE_SIGNIN_URL]
    assert not any(a.startswith("--app") for a in args) and rig.found == ["chrome"]
    assert rig.app.signin_at == int(NOW) and rig.music.sign_in_opened == int(NOW)
    assert rig.results()[-1]["result"] == "opened" and "window that just opened" in rig.results()[-1]["text"]
    assert mc.YOUTUBE_SIGNIN_URL.startswith("https://accounts.google.com/")


def test_youtube_sign_in_uses_the_players_browser_not_deck_browser(rig):
    rig.app.cfg["deck"]["browser"] = "edge"
    rig.app.deck.browser_kind = "chrome"                               # Chrome: in-app Spotify needs it
    rig.act("youtube-sign-in")
    assert rig.app.deck.launched[0][0] == "C:/chrome.exe"


def test_youtube_sign_in_without_a_player_browser(rig):
    rig.app.deck.browser_kind = "none"
    rig.act("youtube-sign-in")
    assert rig.app.deck.launched == [] and rig.results()[-1]["result"] == "unavailable"


def test_a_sign_in_window_without_the_player_holds_back_the_player(rig):
    class Proc:
        code: Optional[int] = None

        def poll(self):
            return self.code

    proc = rig.app.deck.proc = Proc()
    rig.app.deck.page_open = False
    rig.act("youtube-sign-in")
    assert rig.music.signin_running()
    proc.code = 0                                                      # the player closed the window
    assert not rig.music.signin_running()
    rig.app.deck.page_open = True                                      # with the box running: not tracked
    rig.later(11)
    rig.act("youtube-sign-in", at=rig.wall.t)
    assert not rig.music.signin_running()


# ------------------------------------------------------------ Connect page --

def test_requests_from_the_connect_page_show_there_and_not_in_game(rig):
    rid = rig.music.request("test-youtube")
    rig.music.drain()
    rig.music.on_engine_note({"kind": "playing", "dropId": "test-youtube"})
    rig.music.drain()
    res = rig.results("test-youtube")
    assert res[-1]["result"] == "playing" and res[-1]["source"] == "page" and res[-1]["id"] == rid
    assert rig.notes() == [] and rig.game_actions() == []
    with pytest.raises(ValueError):
        rig.music.request("format-c")


# ------------------------------------------------------------------- fuzz --

def test_fuzz_random_config_frames_open_only_spotifys_and_googles_pages(rig):
    from needledropper.providers.spotify_auth import AUTHORIZE_URL

    rng = random.Random(1234)
    rig.app.deck.page_open = True
    n_frames = 0
    for i in range(10_000):
        fields: dict[str, Any] = {"flags": Flags(rng.randrange(256) & 0x7F)}
        if rng.random() < 0.6:
            fields["action"] = {"name": rng.choice(p2.ACTIONS), "at": NOW + rng.uniform(-60, 60)}
        if rng.random() < 0.4:
            fields["client_id"] = "".join(rng.choice("0123456789abcdef") for _ in range(32))
        if rng.random() < 0.5:
            fields["prefs"] = {"order": rng.choice(p2.PREFS_ORDERS + ("x",)), "corner": rng.choice(p2.PREFS_CORNERS),
                               "fade": rng.choice((True, False, None)), "resume": rng.choice((True, False, None))}
        raw = bytearray(p2.encode_frame(p2.build_frame(ET.CONFIG, seq=i % 256, **fields)))
        if rng.random() < 0.3:                                        # damaged on the way: mostly rejected
            raw[rng.randrange(len(raw))] ^= 1 << rng.randrange(8)
        fr = p2.decode_frame(bytes(raw))
        if fr is None:
            continue
        n_frames += 1
        rig.music.feed(fr, rng.choice(("pixel", "chatlog")), baseline=rng.random() < 0.05)
        if i % 50 == 0:
            rig.later(rng.uniform(0, 30))
            for s in rig.app.sessions:
                if not s.done:
                    s.finish("connected", account="A")
        rig.music.drain()
    assert n_frames > 5000
    opened = rig.app.deck.opened + [a[-1] for a in rig.app.deck.launched]
    assert opened and all(u.startswith(AUTHORIZE_URL + "?") or u == mc.YOUTUBE_SIGNIN_URL for u in opened)
    assert all(a[0] in ("C:/chrome.exe", "C:/edge.exe") for a in rig.app.deck.launched)
