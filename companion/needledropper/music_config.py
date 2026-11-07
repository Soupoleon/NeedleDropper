"""Music services from the game (DESIGN-v1.2.1 3.1-3.8, 3.11): the player's music choices and
requests, sent by the addon over the beacon and the chat log.

* **PREFS** (a level: the latest value wins) ride in every HELLO and every CONFIG once the player
  has chosen in game. They are written to config.json (``music.order``, ``music.fade``,
  ``deck.playerCorner``, ``spotify.restorePrevious``, ``music.fromGame``) only when they differ,
  through the companion's own config write, which applies them live (:meth:`Companion._apply`).
  The baseline frame (the first one the pixel link reads after a start) counts too.
* **ACTION** (an edge: do it once) comes only in a CONFIG frame, never a baseline one:
  ``connect-spotify``, ``disconnect-spotify``, ``test-spotify``, ``test-youtube`` and
  ``youtube-sign-in``. A request is fresh from -5 s to +45 s after the game's ``time()`` stamped it
  (one PC clock), and runs once per ``(code, issuedAt)`` (the beacon shows it twice, the chat log
  once more). Rate limits: each action at most once per 10 s, the two that open a page at most 5
  times per 10 minutes. Every request that runs ends in exactly one result -- an entry in the game's
  ``music.actions`` (``NeedleDropper_Companion``) and one Windows notification; Connect Spotify
  shows one more, interim one ("opened") when Spotify's page opens.

Nothing a frame carries is a URL or text: the app opens only Spotify's authorize URL (built by
``spotify_auth.build_authorize_url``) and :data:`YOUTUBE_SIGNIN_URL`.

:class:`MusicConfig` runs one worker thread (``needledropper-music``); :meth:`MusicConfig.feed`
only queues, so no transport or engine thread ever waits on the disk or a browser. The Connect
page's Test / Sign-in buttons go through the same handlers (:meth:`MusicConfig.request`); their
results show on the page, not as notifications, and are not written for the game.

The companion (``app``) it serves provides: ``cfg``, ``paths``, ``deck``, ``engine``, ``providers``,
``paused``, ``notifier``, ``apply_music_choices(changes)``, ``connect_spotify_from_game(cid, on_done)``
(returns the login session), ``current_login()``, ``spotify_disconnect()``, ``push_game_data(flush)``
and ``nudge(what)``.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import tray
from .models import Drop, EventType
from .protocol2 import ACTIONS, Frame

log = logging.getLogger("needledropper.music")

__all__ = ["MusicConfig", "TEST_SONG", "TEST_SPOTIFY_URI", "TEST_YOUTUBE_ID", "YOUTUBE_SIGNIN_URL",
           "prefs_changes", "RESULTS"]

#: The test song (DESIGN-v1.2.1 3.7): 15 s of Toto - Africa from 0:30.
TEST_SONG = {"artist": "Toto", "title": "Africa", "label": "NeedleDropper test", "startMs": 30000,
             "durationMs": 15000}
TEST_SPOTIFY_URI = "spotify:track:2374M0fQpWi3dLnB54qaLX"  # "Africa" by TOTO (open.spotify.com, 2026-11-05)
TEST_YOUTUBE_ID = "QAo_Ycocl1E"  # the Azeroth Mixtape's zone:1413 (The Barrens) song; plays from localhost (D12)
#: Google's sign-in page, back to YouTube afterwards. Unverified against Google until FIRST-TEST step 7.
YOUTUBE_SIGNIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube"
                      "&continue=https%3A%2F%2Fwww.youtube.com%2F")

FRESH_BEFORE_S = 5.0      # a request may be stamped up to this far in the future (the same PC clock)
FRESH_AFTER_S = 45.0      # ... and is stale this long after the click
IDENTITY_TTL_S = 600.0    # (code, issuedAt) of the requests already handled are remembered this long
ACTION_INTERVAL_S = 10.0  # each action at most once per this many seconds
PAGE_LIMIT = 5            # the actions that open a page (connect, YouTube sign-in) at most this often ...
PAGE_WINDOW_S = 600.0     # ... per rolling 10 minutes
TEST_RESULT_S = 15.0      # a test that has not reported within this long failed
MAX_RESULTS = 8

#: Result codes per action (DESIGN-v1.2.1 3.4; as built, a YouTube sign-in without Edge or Chrome is
#: "unavailable": the 1.2.1 box never plays in the default browser).
RESULTS = {
    "connect-spotify": ("opened", "connected", "declined", "timeout", "error", "port-in-use", "no-client-id", "busy"),
    "disconnect-spotify": ("disconnected", "ignored", "busy"),
    "test-spotify": ("playing", "failed", "unavailable", "paused", "busy"),
    "test-youtube": ("playing", "failed", "unavailable", "paused", "busy"),
    "youtube-sign-in": ("opened", "unavailable", "busy"),
}
_PAGE_ACTIONS = ("connect-spotify", "youtube-sign-in")
_TITLES = {"connect-spotify": "Spotify sign-in", "disconnect-spotify": "Spotify", "test-spotify": "Spotify test",
           "test-youtube": "YouTube test", "youtube-sign-in": "YouTube sign-in"}
_CORNER_WORDS = {"topright": "top right", "topleft": "top left", "bottomright": "bottom right",
                 "bottomleft": "bottom left"}
_SONG = "Toto – Africa"
BUSY_TEXT = "NeedleDropper ignored a repeated request from the game. Wait a moment and try again."
NO_CLIENT_ID_TEXT = ("Paste your Spotify app's Client ID in the game first (Options > AddOns > NeedleDropper > "
                     "Spotify account).")
PORT_TEXT = ("Another program uses port {port}, which Spotify's sign-in needs. Close it and try again (or change "
             "spotify.redirectPort and the Redirect URI in your Spotify app).")
OPENED_TEXT = "Approve NeedleDropper on Spotify's page in your browser. Click here if it opened behind the game."
DECLINED_TEXT = "You declined on Spotify's page, so NeedleDropper can't play Spotify songs."
TIMEOUT_TEXT = ("Spotify sent no answer within 5 minutes. If its page said INVALID_CLIENT, copy the Client ID again "
                "(not the Client secret). If it said Invalid redirect URI, add exactly {uri} to your Spotify app.")
REFRESH_HINT = "In WoW, press Refresh status in Music services to see it there."
DISCONNECTED_TEXT = "Spotify disconnected. Your Client ID stays, so Connect signs you in again."
IGNORED_TEXT = "NeedleDropper ignored a Disconnect for a different Spotify app (Client ID …{tail})."
IGNORED_NO_ID_TEXT = "NeedleDropper ignored a Disconnect that named no Spotify Client ID."
PAUSED_TEXT = "Needle drops are paused in the NeedleDropper app (tray menu): resume them to test."
BUSY_DROP_TEXT = "A needle drop is playing. Try the test when it ends."
SPOTIFY_OFF_TEXT = "Spotify is switched off on NeedleDropper's Connect page."
SPOTIFY_NOT_CONNECTED_TEXT = "Spotify isn't connected: press Connect Spotify first."
YOUTUBE_OFF_TEXT = "YouTube is switched off on NeedleDropper's Connect page."
NO_BOX_TEXT = ("NeedleDropper's player isn't running yet: it starts by itself when World of Warcraft isn't the "
               "window in front.")
NO_BOX_NEVER_TEXT = ("NeedleDropper's player is switched off (deck.autoOpen is \"never\" in config.json), so the "
                     "YouTube box can't open.")
NO_BROWSER_TEXT = ("NeedleDropper has no Edge or Chrome for its YouTube box, so there is nothing to sign in to. "
                   "Install Google Chrome.")
SIGNIN_TEXT = ("Sign in to YouTube in the window that just opened. It belongs to NeedleDropper's player box, so your "
               "everyday browser isn't affected. Close it when you're done.")
NO_ANSWER_TEXT = "No answer from {what} within 15 seconds; see the log (tray → Open log folder)."
ENGINE_DOWN_TEXT = "NeedleDropper's playback engine isn't running; see the log (tray → Open log folder)."


@dataclass
class _Request:
    """One request: from the game (a CONFIG frame's ACTION) or from the Connect page."""

    action: str
    issued_at: int
    source: str = "game"  # "game" | "page"
    client_id: Optional[str] = None
    transport: str = ""


@dataclass
class _PendingTest:
    req: _Request
    drop_id: str
    service: str
    deadline: float  # monotonic


@dataclass
class _Stats:
    prefs_seen: int = 0
    prefs_written: int = 0
    actions: dict[str, int] = field(default_factory=dict)


def prefs_changes(prefs: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    """The config.json updates a PREFS value calls for (DESIGN-v1.2.1 3.2): only what differs from
    ``cfg``; "not set" fields (None) change nothing. ``deck.playerPosition`` is cleared when the
    corner changes; ``music.fromGame`` becomes true with the first PREFS that sets anything."""
    music = cfg.get("music") if isinstance(cfg.get("music"), dict) else {}
    deck = cfg.get("deck") if isinstance(cfg.get("deck"), dict) else {}
    spotify = cfg.get("spotify") if isinstance(cfg.get("spotify"), dict) else {}
    order = getattr(prefs, "order", None)
    corner = getattr(prefs, "corner", None)
    fade = getattr(prefs, "fade", None)
    resume = getattr(prefs, "resume", None)
    changes: dict[str, Any] = {}
    if order is not None and order != music.get("order"):
        changes.setdefault("music", {})["order"] = order
    if isinstance(fade, bool) and fade != music.get("fade"):
        changes.setdefault("music", {})["fade"] = fade
    if any(v is not None for v in (order, corner, fade, resume)) and music.get("fromGame") is not True:
        changes.setdefault("music", {})["fromGame"] = True
    if corner is not None and corner != deck.get("playerCorner"):
        changes["deck"] = {"playerCorner": corner, "playerPosition": ""}
    if isinstance(resume, bool) and resume != spotify.get("restorePrevious"):
        changes["spotify"] = {"restorePrevious": resume}
    return changes


def _strip_provider(reason: str) -> str:
    for name in ("spotify: ", "youtube: ", "local: "):
        if reason.lower().startswith(name):
            return reason[len(name):]
    return reason


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else text + "."


class MusicConfig:
    """See the module docs. ``clock`` is the wall clock (Unix seconds: the game stamps its requests
    with the same PC clock), ``monotonic`` measures the rate limits and the test timeouts."""

    def __init__(self, app: Any, *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 find_browser: Optional[Callable[[str], Optional[str]]] = None) -> None:
        self.app = app
        self._wall = clock
        self._mono = monotonic
        self._find_browser = find_browser
        self._q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._busy = 0
        self._seen: dict[tuple[int, int], float] = {}
        self._last_run: dict[str, float] = {}
        #: the click time (issuedAt) of the game's requests that ran, per action (see _too_soon)
        self._game_runs: dict[str, list[int]] = {}
        self._page_opens: deque[float] = deque()
        self._pending: dict[str, _PendingTest] = {}
        self._results: deque[dict[str, Any]] = deque(maxlen=MAX_RESULTS)
        self._login_waiters: list[tuple[Any, _Request]] = []
        self._own_sessions: set[int] = set()  # id() of the sign-ins this module started (they report on_done)
        self.signin_proc: Any = None
        self.sign_in_opened: Optional[int] = None
        self.stats = _Stats()

    # ------------------------------------------------------------ lifecycle --
    def start(self) -> "MusicConfig":
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            self._thread = threading.Thread(target=self._run, name="needledropper-music", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        t = self._thread
        if t is None:
            return
        self._q.put(None)
        if t is not threading.current_thread():
            t.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until everything queued so far was handled (tests, tools)."""
        return self._idle.wait(timeout)

    # -------------------------------------------------------------- inputs --
    @staticmethod
    def wants(frame: Any) -> bool:
        """A v2 frame this module acts on: a CONFIG, or a HELLO with PREFS."""
        if not isinstance(frame, Frame):
            return False
        return frame.type == EventType.CONFIG or (frame.type == EventType.HELLO and frame.prefs is not None)

    def feed(self, frame: Any, transport: str, *, baseline: bool = False) -> bool:
        """Queue a frame from a transport (any thread; never blocks). True when it was queued."""
        if not self.wants(frame):
            return False
        self._put(("frame", frame, str(transport or ""), bool(baseline)))
        return True

    def on_engine_note(self, change: Any) -> None:
        """An engine status change (``{"kind": ..., "dropId": ...}``): it may answer a running test."""
        if not isinstance(change, dict):
            return
        with self._lock:
            wanted = str(change.get("dropId") or "") in self._pending
        if wanted:
            self._put(("note", dict(change)))

    def request(self, action: str, *, source: str = "page") -> int:
        """Run a request from the Connect page through the same handler as the game's (its result
        shows in :meth:`recent`, not as a notification). Returns its id."""
        if action not in ACTIONS:
            raise ValueError(f"unknown request {action!r}")
        rid = int(self._wall())
        self._put(("request", _Request(action, rid, source=source)))
        return rid

    def _put(self, item: tuple) -> None:
        with self._lock:
            self._busy += 1
            self._idle.clear()
        self._q.put(item)

    # ---------------------------------------------------------- the worker --
    def _run(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=self._next_wait())
            except queue.Empty:
                item = ()
            if item is None:
                return
            try:
                if item:
                    self._process(item)
                self._timers()
            except Exception:  # noqa: BLE001 - one bad request must not stop the next
                log.exception("music services: handling %s failed", item[0] if item else "the timers")
            finally:
                if item:
                    with self._lock:
                        self._busy -= 1
                        if self._busy <= 0:
                            self._busy = 0
                            self._idle.set()

    def drain(self) -> int:
        """Handle everything queued now, on this thread (tests without the worker)."""
        n = 0
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                continue
            try:
                self._process(item)
            finally:
                with self._lock:
                    self._busy = max(0, self._busy - 1)
                    if not self._busy:
                        self._idle.set()
            n += 1
        self._timers()
        return n

    def _next_wait(self) -> float:
        with self._lock:
            deadlines = [p.deadline for p in self._pending.values()]
        if not deadlines:
            return 1.0
        return max(0.05, min(1.0, min(deadlines) - self._mono()))

    def _timers(self) -> None:
        self._expire_tests()
        self._foreign_logins()

    def _process(self, item: tuple) -> None:
        kind = item[0]
        if kind == "frame":
            self._on_frame(item[1], item[2], item[3])
        elif kind == "request":
            self._run_request(item[1])
        elif kind == "note":
            self._on_note(item[1])
        elif kind == "login":
            self._on_login_done(item[1], item[2])

    # ------------------------------------------------------------- frames --
    def _on_frame(self, fr: Frame, transport: str, baseline: bool) -> None:
        if fr.prefs is not None and fr.type in (EventType.HELLO, EventType.CONFIG):
            self._apply_prefs(fr, transport, baseline)
        if fr.type != EventType.CONFIG or fr.action is None:
            return
        act = fr.action
        if baseline:
            log.info("a request in the first frame read after the start is not run (%s via %s)",
                     act.describe(), transport)
            return
        name = act.name
        if name is None:
            log.info("unknown request code %d from the game ignored (a newer addon?)", act.code)
            return
        age = self._wall() - act.issued_at
        if not -FRESH_BEFORE_S <= age <= FRESH_AFTER_S:
            log.info("stale request ignored: %s from %.0f s ago via %s", name, age, transport)
            return
        now = self._mono()
        ident = (int(act.code), int(act.issued_at))
        self._seen = {k: t for k, t in self._seen.items() if now - t < IDENTITY_TTL_S}
        if ident in self._seen:
            log.debug("request %s@%d again (via %s): already handled", name, act.issued_at, transport)
            return
        self._seen[ident] = now
        self._run_request(_Request(name, int(act.issued_at), source="game", client_id=fr.client_id,
                                   transport=transport))

    def _apply_prefs(self, fr: Frame, transport: str, baseline: bool) -> None:
        self.stats.prefs_seen += 1
        changes = prefs_changes(fr.prefs, self.app.cfg)
        if not changes:
            return
        log.info("music choices from the game (%s via %s%s): %s", fr.prefs.describe(), transport,
                 ", first frame" if baseline else "", changes)
        try:
            self.app.apply_music_choices(changes)
        except Exception as exc:  # noqa: BLE001 - config.json locked or unreadable: the next frame retries
            log.warning("could not save the music choices from the game: %s", exc)
            return
        self.stats.prefs_written += 1
        self._push(flush=False)

    # ------------------------------------------------------------ requests --
    def _too_soon(self, req: _Request, now: float) -> bool:
        """Each action at most once per :data:`ACTION_INTERVAL_S`. A request from the game is measured by its
        click (``issuedAt``: the game's ``time()``, the same PC clock), against the clicks of the game's earlier
        requests of that action that ran. The game's own limit (``MusicServices.SEND_GAP``, the same 10 s)
        counts from the click too, so a request it sends is never refused here: by the app's clock two clicks
        10 s apart could arrive 2 s apart, the first read only at the beacon's re-show 8 s later or queued
        behind another frame (review RG-2). Within ``ACTION_INTERVAL_S`` either way counts, so a request that
        comes late over the chat log after a newer one ran is still checked. A request from the Connect page is
        measured by the app's own clock, against any source."""
        if req.source != "game":
            last = self._last_run.get(req.action)
            return last is not None and now - last < ACTION_INTERVAL_S
        wall = self._wall()
        # Only clicks a fresh request can still be near (none from before the PC clock was set back).
        runs = [t for t in self._game_runs.get(req.action, ())
                if wall - FRESH_AFTER_S - ACTION_INTERVAL_S <= t <= wall + FRESH_BEFORE_S]
        self._game_runs[req.action] = runs
        return any(abs(req.issued_at - t) < ACTION_INTERVAL_S for t in runs)

    def _run_request(self, req: _Request) -> None:
        now = self._mono()
        self.stats.actions[req.action] = self.stats.actions.get(req.action, 0) + 1
        reopen = req.action == "connect-spotify" and self._waiting_login(req) is not None
        while self._page_opens and now - self._page_opens[0] > PAGE_WINDOW_S:
            self._page_opens.popleft()
        too_soon = self._too_soon(req, now)
        too_many = req.action in _PAGE_ACTIONS and len(self._page_opens) >= PAGE_LIMIT
        if too_soon or too_many:
            log.info("request %s refused: %s", req.action,
                     "too soon after the last one" if too_soon else "too many pages opened in 10 minutes")
            self._result(req, "busy", BUSY_TEXT, notify=False)
            if req.source == "game":
                self._note(tray.NOTE_MUSIC_BUSY, BUSY_TEXT, title="NeedleDropper", key=None, level="info")
            return
        self._last_run[req.action] = now
        if req.source == "game":
            self._game_runs.setdefault(req.action, []).append(int(req.issued_at))
        if req.action in _PAGE_ACTIONS:
            self._page_opens.append(now)
        log.info("request from the %s: %s (issued %d%s)", "game" if req.source == "game" else "Connect page",
                 req.action, req.issued_at, f", via {req.transport}" if req.transport else "")
        if req.action == "connect-spotify":
            self._connect(req, reopen)
        elif req.action == "disconnect-spotify":
            self._disconnect(req)
        elif req.action in ("test-spotify", "test-youtube"):
            self._test(req)
        else:
            self._youtube_sign_in(req)

    # -- connect-spotify (3.5)
    def _waiting_login(self, req: _Request) -> Any:
        login = self.app.current_login()
        if login is None or getattr(login, "done", True) or getattr(login, "state", "") != "waiting":
            return None
        want = (req.client_id or self._configured_client_id()).lower()
        return login if str(getattr(login, "client_id", "")).lower() == want else None

    def _configured_client_id(self) -> str:
        return str((self.app.cfg.get("spotify") or {}).get("clientId") or "").strip()

    def _connect(self, req: _Request, reopen: bool) -> None:
        from .providers.spotify_auth import SpotifyAuthError

        cid = req.client_id or self._configured_client_id()
        if not cid:
            self._result(req, "no-client-id", NO_CLIENT_ID_TEXT, level="warning")
            return
        login = self._waiting_login(req) if reopen else None
        if login is not None:
            # Its sign-in is still waiting: open the same page again, no new session (3.4 rule 5).
            self._open_page(login.authorize_url)
            with self._lock:
                self._login_waiters.append((login, req))
            self._result(req, "opened", OPENED_TEXT, final=False, action=lambda u=login.authorize_url: self._open_page(u))
            return
        try:
            session = self.app.connect_spotify_from_game(cid, lambda s, r=req: self._put(("login", s, r)))
        except SpotifyAuthError as exc:
            if getattr(exc, "error", None) == "port_in_use":
                port = int((self.app.cfg.get("spotify") or {}).get("redirectPort") or 8321)
                self._result(req, "port-in-use", PORT_TEXT.format(port=port), level="warning")
            else:
                self._result(req, "error", _sentence(str(exc)), level="warning")
            return
        except ValueError as exc:  # PatchError: not a Client ID
            self._result(req, "error", _sentence(str(exc)), level="warning")
            return
        with self._lock:
            self._own_sessions.add(id(session))
        url = session.authorize_url
        self._open_page(url)
        self._result(req, "opened", OPENED_TEXT, final=False, action=lambda: self._open_page(url))

    def _open_page(self, url: str) -> None:
        opener = getattr(self.app.deck, "url_opener", None)
        if not callable(opener):
            log.warning("no way to open a browser: open %s yourself", url.split("?", 1)[0])
            return
        try:
            opener(url)
        except Exception as exc:  # noqa: BLE001 - the notification still offers the page
            log.warning("could not open Spotify's sign-in page: %s", exc)

    def _foreign_logins(self) -> None:
        """A request that re-opened a sign-in the Connect page started: no one tells this module when it
        ends, so it looks (the worker wakes every second)."""
        with self._lock:
            ended = [s for s, _r in self._login_waiters
                     if getattr(s, "done", False) and id(s) not in self._own_sessions]
        for session in dict.fromkeys(ended):
            self._on_login_done(session, None)

    def _on_login_done(self, session: Any, req: Optional[_Request]) -> None:
        with self._lock:
            others = [r for s, r in self._login_waiters if s is session]
            self._login_waiters = [(s, r) for s, r in self._login_waiters if s is not session]
            self._own_sessions.discard(id(session))
        reqs = ([req] if req is not None else []) + [r for r in others if r is not req]
        state = getattr(session, "state", "error")
        error = getattr(session, "error", None)
        if state == "connected":
            prof = getattr(session, "profile", None) or {}
            who = getattr(session, "account", None) or prof.get("name")
            product = prof.get("product")
            warning = getattr(session, "warning", None)
            if warning:
                result, text, level = "connected", warning, "warning"
            elif product and product != "premium":
                result, level = "connected", "warning"
                text = (f"Connected as {who or 'your account'}, but this account has Spotify "
                        f"{str(product).capitalize()}: controlling playback needs Premium.")
            else:
                result, level = "connected", "info"
                who_text = f" as {who}" if who else ""
                plan = " (Premium)" if product == "premium" else ""
                text = f"Spotify connected{who_text}{plan}. {REFRESH_HINT}"
        elif error == "access_denied":
            result, text, level = "declined", DECLINED_TEXT, "warning"
        elif state == "timeout" or error == "timeout":
            from .providers.spotify_auth import redirect_uri_for

            port = int((self.app.cfg.get("spotify") or {}).get("redirectPort") or 8321)
            result, text, level = "timeout", TIMEOUT_TEXT.format(uri=redirect_uri_for(port)), "warning"
        else:
            result, level = "error", "warning"
            text = _sentence(str(getattr(session, "message", "") or "The Spotify sign-in failed."))
        for r in reqs:
            self._result(r, result, text, level=level)

    # -- disconnect-spotify (3.6)
    def _disconnect(self, req: _Request) -> None:
        configured = self._configured_client_id()
        if not req.client_id and req.source == "page":
            req.client_id = configured.lower()
        if not req.client_id:
            self._result(req, "ignored", IGNORED_NO_ID_TEXT, level="warning")
            return
        if not configured or req.client_id.lower() != configured.lower():
            self._result(req, "ignored", IGNORED_TEXT.format(tail=req.client_id[-4:]), level="warning")
            return
        self.app.spotify_disconnect()
        self._result(req, "disconnected", DISCONNECTED_TEXT)

    # -- test-spotify / test-youtube (3.7)
    def _test(self, req: _Request) -> None:
        service = "spotify" if req.action == "test-spotify" else "youtube"
        app = self.app
        if getattr(app, "paused", False):
            self._result(req, "paused", PAUSED_TEXT, level="warning")
            return
        engine = getattr(app, "engine", None)
        if engine is None or not callable(getattr(engine, "play_now", None)):
            self._result(req, "failed", ENGINE_DOWN_TEXT, level="warning")
            return
        try:
            playing = (engine.status() or {}).get("playing")
        except Exception:  # noqa: BLE001
            playing = None
        if isinstance(playing, dict) and not playing.get("test"):
            # a real drop (an in-game file too): a test would play over it or cut it off
            self._result(req, "busy", BUSY_DROP_TEXT, level="warning")
            return
        provider = next((p for p in list(getattr(app, "providers", None) or []) if getattr(p, "name", None) == service),
                        None)
        if service == "spotify":
            if provider is None:
                self._result(req, "unavailable", SPOTIFY_OFF_TEXT, level="warning")
                return
            ok, reason = self._available(provider)
            if not ok:
                self._result(req, "unavailable", _sentence(reason or SPOTIFY_NOT_CONNECTED_TEXT), level="warning")
                return
            drop = Drop(n=0, id="test-spotify", kind="test", spotify=TEST_SPOTIFY_URI, **TEST_SONG)
        else:
            if provider is None:
                self._result(req, "unavailable", YOUTUBE_OFF_TEXT, level="warning")
                return
            deck = app.deck
            ensure = getattr(deck, "ensure_player", None)
            if callable(ensure) and not getattr(deck, "page_open", False):
                try:
                    ready = bool(ensure(force=True))
                except Exception as exc:  # noqa: BLE001
                    log.info("the YouTube box could not be opened: %s", exc)
                    ready = False
                if not ready:
                    never = getattr(deck, "auto_open", "") == "never"
                    self._result(req, "unavailable", NO_BOX_NEVER_TEXT if never else NO_BOX_TEXT, level="warning")
                    return
            drop = Drop(n=0, id="test-youtube", kind="test", youtube=TEST_YOUTUBE_ID, **TEST_SONG)
        with self._lock:
            self._pending[drop.id] = _PendingTest(req, drop.id, service, self._mono() + TEST_RESULT_S)
        engine.play_now(drop, test=True, only=service)

    @staticmethod
    def _available(provider: Any) -> tuple[bool, Optional[str]]:
        try:
            if provider.available():
                return True, None
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        reason = None
        fn = getattr(provider, "unavailable_reason", None)
        if callable(fn):
            try:
                reason = fn()
            except Exception:  # noqa: BLE001
                reason = None
        return False, reason

    def _on_note(self, change: dict[str, Any]) -> None:
        with self._lock:
            pending = self._pending.get(str(change.get("dropId") or ""))
        if pending is None:
            return
        kind = str(change.get("kind") or "")
        req, service = pending.req, pending.service
        if kind == "playing":
            if service == "spotify":
                text = f"Spotify test: playing {_SONG} for 15 seconds."
            else:
                corner = _CORNER_WORDS.get(str((self.app.cfg.get("deck") or {}).get("playerCorner") or ""), "top right")
                text = f"YouTube test: the box in the {corner} corner is playing {_SONG} for 15 seconds."
            self._done_test(pending)
            self._result(req, "playing", text)
            return
        if kind == "noSource":
            reasons = [str(r) for r in (change.get("reasons") or []) if r]
            reason = _strip_provider(reasons[0]) if reasons else "nothing could play it"
            text = f"{'Spotify' if service == 'spotify' else 'YouTube'} test failed: {_sentence(reason)}"
        elif kind == "notEmbeddable":
            text = f"YouTube test failed: YouTube refused to play the test video here (error {change.get('code')})."
        elif kind.startswith("spotify") and change.get("provider"):  # a provider notice, e.g. spotifyStuck
            notice = None
            if kind == "spotifyStuck":
                sp = next((p for p in self.app.providers or [] if getattr(p, "name", None) == "spotify"), None)
                fn = getattr(sp, "stuck_notice", None)
                if callable(fn):
                    try:
                        notice = fn()[1]
                    except Exception:  # noqa: BLE001
                        notice = None
            text = f"Spotify test failed: {_sentence(notice or str(change.get('detail') or kind))}"
        elif kind == "stopped" and change.get("reason") not in ("drop finished", "provider reports end"):
            text = f"The {'Spotify' if service == 'spotify' else 'YouTube'} test was stopped before it played."
        else:
            return
        self._done_test(pending)
        self._result(req, "failed", text, level="warning")

    def _done_test(self, pending: _PendingTest) -> None:
        with self._lock:
            if self._pending.get(pending.drop_id) is pending:
                del self._pending[pending.drop_id]

    def _expire_tests(self) -> None:
        now = self._mono()
        with self._lock:
            due = [p for p in self._pending.values() if now >= p.deadline]
            for p in due:
                del self._pending[p.drop_id]
        for p in due:
            what = "Spotify" if p.service == "spotify" else "the YouTube box"
            self._result(p.req, "failed", NO_ANSWER_TEXT.format(what=what), level="warning")

    # -- youtube-sign-in (3.8)
    def _youtube_sign_in(self, req: _Request) -> None:
        deck = self.app.deck
        try:
            kind = str(deck.player_browser()) if callable(getattr(deck, "player_browser", None)) else "none"
        except Exception:  # noqa: BLE001
            kind = "none"
        find = self._find_browser
        if find is None:
            from .deck.server import find_browser as find
        exe = find(kind) if kind in ("chrome", "edge") else None
        profile = getattr(deck, "profile_dir", None)
        if not exe or profile is None:
            self._result(req, "unavailable", NO_BROWSER_TEXT, level="warning")
            return
        # The player box's own browser and profile (deck-browser), a normal window with its address bar
        # (no --app): the cookies land where the box plays. With the box running, Chromium hands these
        # arguments to its process, which opens the window; otherwise a new process starts.
        args = [exe, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--new-window",
                YOUTUBE_SIGNIN_URL]
        page_open = bool(getattr(deck, "page_open", False))
        try:
            proc = deck.launcher(args)
        except OSError as exc:
            self._result(req, "unavailable", f"{kind.capitalize()} could not be started: {exc}.", level="warning")
            return
        if not page_open:
            # This process now owns the player's profile: the player is not made while it runs (it
            # would end up inside it, without its flags and out of the window manager's reach).
            self.signin_proc = proc
        when = int(self._wall())
        self.sign_in_opened = when
        try:
            self.app.note_youtube_sign_in(when)
        except Exception:  # noqa: BLE001 - the report is a convenience
            log.debug("could not note the YouTube sign-in time", exc_info=True)
        self._result(req, "opened", SIGNIN_TEXT)

    def signin_running(self) -> bool:
        """A YouTube sign-in window started its own browser process with the player's profile, and it
        is still open (no player window may be made meanwhile)."""
        proc = self.signin_proc
        poll = getattr(proc, "poll", None)
        if not callable(poll):
            return False
        try:
            running = poll() is None
        except Exception:  # noqa: BLE001
            running = False
        if not running:
            self.signin_proc = None
        return running

    # -------------------------------------------------------------- results --
    def _result(self, req: _Request, result: str, text: str, *, final: bool = True, notify: bool = True,
                level: str = "info", action: Optional[Callable[[], Any]] = None) -> None:
        if req.action == "test-spotify" and result in ("failed", "unavailable"):
            text = _sentence(text) + " YouTube wasn't tried; Test Spotify checks only Spotify."
        entry = {"id": int(req.issued_at), "action": req.action, "result": result, "text": text,
                 "at": int(self._wall())}
        log.info("request %s@%d: %s - %s", req.action, req.issued_at, result, text)
        with self._lock:
            kept = [e for e in self._results if (e["id"], e["action"]) != (entry["id"], entry["action"])]
            self._results = deque(kept + [dict(entry, source=req.source)], maxlen=MAX_RESULTS)
        if req.source == "game":
            self._record(entry)
            if final:
                self._push(flush=True)  # a Refresh status right after the notification shows it
            if notify:
                phase = "result" if final else result
                self._note(tray.NOTE_MUSIC, text, title=_TITLES.get(req.action, "NeedleDropper"),
                           key=f"{req.action}:{req.issued_at}:{phase}", level=level, action=action)
        nudge = getattr(self.app, "nudge", None)
        if callable(nudge):
            nudge("music")

    def _record(self, entry: dict[str, Any]) -> None:
        addons = getattr(getattr(self.app, "paths", None), "addons_dir", None)
        if addons is None or not addons.is_dir():
            return
        from . import gamedata

        try:
            gamedata.record_music_action(addons, entry)
        except (gamedata.GameDataError, OSError) as exc:
            log.info("could not record the request's result for the game: %s", exc)

    def _push(self, *, flush: bool) -> None:
        push = getattr(self.app, "push_game_data", None)
        if callable(push):
            try:
                push(flush=flush)
            except Exception as exc:  # noqa: BLE001
                log.info("could not update the game's companion data: %s", exc)

    def _note(self, kind: str, text: str, *, title: str, key: Optional[str], level: str,
              action: Optional[Callable[[], Any]] = None) -> None:
        notifier = getattr(self.app, "notifier", None)
        if notifier is None:
            return
        notifier.notify(kind, text, title=title, key=key, level=level, action=action)

    def recent(self) -> list[dict[str, Any]]:
        """The last 8 results, oldest first (``source``: "game" or "page"), for the Connect page."""
        with self._lock:
            return [dict(e) for e in self._results]

    def pending_tests(self) -> list[str]:
        """The tests still waiting for the engine (their drop ids)."""
        with self._lock:
            return sorted(self._pending)
