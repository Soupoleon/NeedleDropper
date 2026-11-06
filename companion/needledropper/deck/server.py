"""The deck: the companion's local web server (docs/SPEC-v1.1.md C.1, C.3, C.4; DESIGN-v1.2 1.4).

``Deck`` runs a ``ThreadingHTTPServer`` on ``127.0.0.1:<deck.port>`` serving:

* ``GET /`` -- the one-time **Connect page** (World of Warcraft, Spotify,
  YouTube, Check the link; ``/connect``, ``/setup`` and the v1.1 ``/panel`` are
  aliases). It connects to ``/events`` as a ``panel`` client;
* ``GET /player`` -- the small **player** page (title card, YouTube IFrame
  Player, ``<audio>`` for companion-side local files, and the Spotify Web Playback
  SDK: in-app Spotify, the Connect device "NeedleDropper"). The browser opens it
  as ``http://localhost:<port>/player`` (:attr:`Deck.player_url`): YouTube refuses
  most embeds on a ``127.0.0.1`` origin (error 150);
* ``GET /events?client=panel|player`` -- Server-Sent Events. Playback commands
  (``play`` / ``stop`` / ``fade`` / ``cue`` / ``spotify``) go to player clients only;
  ``show`` / ``hide`` (the title card) go to both; ``state`` nudges ask the Connect
  page to refresh;
* ``POST /status`` -- the player reports ``{"state": "ready"|"playing"|"ended"|
  "error"|"autoplay-blocked", "detail": ..., "playId": ..., "code": ...}``;
  :attr:`Deck.on_player_status` hears every report (e.g. YouTube's "embedding
  not allowed" codes). ``{"state": "spotify", "event": ...}`` reports from the
  Web Playback SDK go to :class:`SpotifySdk` (``deck.spotify``) instead, and
  ``{"state": "csp", ...}`` (a Content-Security-Policy violation) to the log;
* ``GET /api/player/spotify-token`` -- the SDK's ``getOAuthToken``: the Spotify
  access token only (never the refresh token), for the player page only (same
  origin, ``X-ND-Token``, ``Cache-Control: no-store``); answered by the deck
  itself, so it works without the Connect page's API;
* ``/api/*`` -- the Connect page's JSON API, answered by :attr:`Deck.api`
  (see :mod:`needledropper.panel_api`);
* ``GET /media/<token>`` -- a whitelisted local file, with HTTP Range support;
* ``GET /healthz`` -- unauthenticated ``{"app": "needledropper", "version": ...}``
  used to detect a running instance;
* ``GET /static/<file>`` -- page assets.

Security (SPEC-v1.1 C.4): binds 127.0.0.1 only; every request must carry
``Host: 127.0.0.1:<port>`` or ``localhost:<port>`` (DNS-rebinding guard);
``/api/*``, ``/status`` and ``/events`` reject a foreign ``Origin``; ``/api/*``
and ``/status`` require the per-process token in ``X-ND-Token`` (compared in
constant time; it is injected into the served pages as
``<meta name="nd-token">``); request bodies are capped at 64 KB; pages are
served with a strict Content-Security-Policy and may not be framed. Only files
passed to :meth:`Deck.register_media` are ever served from disk.
"""
from __future__ import annotations

import collections
import errno
import hmac
import html
import json
import logging
import mimetypes
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from .. import __version__, resources

STATIC_DIR = resources.static_dir()
DEFAULT_PORT = 8322
HEARTBEAT_SEC = 5.0
CONNECTED_WINDOW_SEC = 10.0
MAX_BODY_BYTES = 64 * 1024
MAX_STATUS_BYTES = MAX_BODY_BYTES
DRAIN_LIMIT = 1024 * 1024  # an oversized body up to this size is read and dropped before the 413
CHUNK = 64 * 1024
TOKEN_HEADER = "X-ND-Token"
TOKEN_PLACEHOLDER = "{{ND_TOKEN}}"
APP_ID = "needledropper"

PLAYER_WINDOW_SIZE = (420, 380)
PLAYER_CONNECT_TIMEOUT = 8.0  # wait this long for an opened player window to connect
PLAYER_RECONNECT_GRACE = 3.0  # a player that vanished this recently is probably reloading
PLAYER_STARTUP_TIMEOUT = 20.0  # the hidden player window made at the app's start may take this long
HIDE_AFTER_HIDE_S = 2.6  # the page fades its card out in 2.1 s; the window hides after that
PLAYER_QUIT_WAIT_S = 3.0  # at quit: how long the player browser gets to exit after its window closed
BROWSER_LOG_MAX = 1_000_000  # logs\browser.log (the browser's stderr): one older copy above this
#: Chromium flags for a page that must keep working while its window is hidden (warm YouTube player,
#: cue at ARM): without them a hidden page's timers and media are throttled (research 3.5; the
#: occlusion feature too, real-client #15).
NO_THROTTLE_FLAGS = ("--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                     "--disable-renderer-backgrounding", "--disable-features=CalculateNativeWinOcclusion")

CLIENT_PANEL = "panel"
CLIENT_PLAYER = "player"
CLIENT_KINDS = (CLIENT_PANEL, CLIENT_PLAYER)
PLAYER_ONLY_CMDS = frozenset({"play", "stop", "fade", "cue", "spotify"})
PANEL_ONLY_CMDS = frozenset({"state"})

#: The SDK's getOAuthToken reads this (the player page only; see ``_Handler._spotify_token``).
SPOTIFY_TOKEN_PATH = "/api/player/spotify-token"
#: The Connect device the player page registers as (in-app Spotify, D6).
SPOTIFY_DEVICE_NAME = "NeedleDropper"
#: How many SDK reports :class:`SpotifySdk` keeps for waits.
_SDK_EVENT_MEMORY = 128

AUTO_OPEN_MODES = ("youtube", "always", "never")
#: Frequent requests that are not written to the debug log.
_QUIET_PATHS = frozenset({"/api/state", "/healthz", "/events", "/status", "/favicon.ico", SPOTIFY_TOKEN_PATH})

#: The Connect page's template (the v1.1 control panel, panel.html, is gone).
CONNECT_PAGE = "connect.html"
PLAYER_PAGE = "player.html"

#: path -> (template file, page kind: picks the CSP and referrer policy)
PAGES: dict[str, tuple[str, str]] = {
    "/": (CONNECT_PAGE, "connect"),
    "/index.html": (CONNECT_PAGE, "connect"),
    "/connect": (CONNECT_PAGE, "connect"),
    "/setup": (CONNECT_PAGE, "connect"),
    "/panel": (CONNECT_PAGE, "connect"),  # v1.1 bookmarks
    "/player": (PLAYER_PAGE, "player"),
    "/player.html": (PLAYER_PAGE, "player"),
}

#: Spotify's Web Playback SDK (in-app Spotify, D13): its script and the hidden iframe it embeds come
#: from sdk.scdn.co; the player talks to Spotify's https and wss hosts and plays through MSE (blob:).
_SPOTIFY_SDK_ORIGIN = "https://sdk.scdn.co"
_SPOTIFY_CONNECT = "https://*.spotify.com wss://*.spotify.com https://*.scdn.co https://*.spotifycdn.com"
_SPOTIFY_MEDIA = "https://*.scdn.co https://*.spotifycdn.com"
_CSP = {
    # The Connect page loads nothing from anywhere else (its links only navigate).
    "connect": ("default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    # The YouTube IFrame API loads its widget script from www.youtube.com and embeds an iframe; the
    # Spotify SDK loads from sdk.scdn.co and embeds its own iframe there.
    "player": ("default-src 'self'; style-src 'self'; img-src 'self' https: data:; object-src 'none'; "
               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
               f"connect-src 'self' {_SPOTIFY_CONNECT}; "
               f"script-src 'self' https://www.youtube.com https://s.ytimg.com {_SPOTIFY_SDK_ORIGIN}; "
               f"frame-src https://www.youtube.com https://www.youtube-nocookie.com {_SPOTIFY_SDK_ORIGIN}; "
               f"media-src 'self' blob: {_SPOTIFY_MEDIA}"),
}
# YouTube rejects embeds without the page origin as referrer (error 153); the Connect page needs none.
_REFERRER = {"connect": "no-referrer", "player": "strict-origin-when-cross-origin"}
#: How many recent ``play`` commands the deck remembers (to tell :attr:`Deck.on_player_status` what failed).
_PLAY_MEMORY = 64

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
_MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
    ".flac": "audio/flac", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".weba": "audio/webm", ".webm": "audio/webm", ".mp4": "video/mp4",
}

log = logging.getLogger("needledropper.deck")

#: ``api(method, path, query, body) -> (http_status, json_payload)``
ApiHandler = Callable[[str, str, dict[str, str], Any], "tuple[int, Any]"]


def parse_range(header: Optional[str], size: int) -> Optional[tuple[int, int]] | str:
    """Parse a single ``Range: bytes=...`` header.

    Returns ``(start, end)`` inclusive, ``None`` to serve the whole file
    (no/ignored header), or ``"unsatisfiable"``.
    """
    if not header:
        return None
    h = header.strip()
    if not h.lower().startswith("bytes="):
        return None
    spec = h[6:].split(",", 1)[0].strip()  # first range only
    if "-" not in spec:
        return None
    a, b = spec.split("-", 1)
    a, b = a.strip(), b.strip()
    try:
        if a == "":
            if not b:
                return None
            n = int(b)
            if n <= 0 or size == 0:
                return "unsatisfiable"
            return (max(0, size - n), size - 1)
        start = int(a)
        end = int(b) if b else size - 1
    except ValueError:
        return None
    if start < 0:
        return None
    if start >= size:
        return "unsatisfiable"
    if end < start:
        return None
    return (start, min(end, size - 1))


class _Client:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.q: "queue.Queue[Optional[str]]" = queue.Queue()
        self.connected_at = time.monotonic()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False  # Windows: SO_REUSEADDR would allow port hijacking
    deck: "Deck"

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:  # Windows: no other socket may bind this address and port at all
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            except OSError:
                pass
        super().server_bind()


class _Server6(_Server):
    """The same server on the IPv6 loopback ``[::1]``: ``localhost`` (the player's origin, D12) resolves to
    ``::1`` first on Windows, and a browser tries it before 127.0.0.1 -- with nothing listening there every
    request of the player page waited about 2 s for the refused connect, and another program listening there
    would have been "the player page" (review RCOMP-4)."""
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except (AttributeError, OSError):
            pass
        super().server_bind()


#: The [::1] bind failed this way: another program has the port there (anything else: no IPv6 on this PC).
_ADDR_IN_USE = {errno.EADDRINUSE, 10048}  # 10048: WSAEADDRINUSE


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "NeedleDropper/" + __version__
    sys_version = ""
    protocol_version = "HTTP/1.0"

    # ---- plumbing
    def log_message(self, fmt: str, *args: Any) -> None:  # route to logging, not stderr
        path = urlsplit(getattr(self, "path", "") or "").path
        if path in _QUIET_PATHS or path.startswith("/static/"):
            return  # the panel polls these every few seconds: keep the log readable
        log.debug("%s %s", self.address_string(), fmt % args)

    @property
    def deck(self) -> "Deck":
        return self.server.deck

    def _send_simple(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8",
                     headers: Optional[dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            try:
                self.wfile.write(body)
            except OSError:
                pass

    def _send_json(self, code: int, payload: Any) -> None:
        try:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            log.error("deck: cannot encode API response: %s", exc)
            code, body = HTTPStatus.INTERNAL_SERVER_ERROR, b'{"error":"internal error"}'
        self._send_simple(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._send_json(code, {"error": message})

    def _host_ok(self) -> bool:
        return self.deck.host_allowed(self.headers.get("Host"))

    def _origin_ok(self) -> bool:
        """A foreign ``Origin`` is refused; so is a request the browser marks as coming from another
        site (``Sec-Fetch-Site`` other than ``same-origin`` / ``none``). DECK-EVENTS-NO-ORIGIN: a
        no-cors ``<img src=".../events">`` from any web page carries no Origin header at all, but
        modern browsers always say ``Sec-Fetch-Site: cross-site`` for it. A missing Origin alone is
        fine (a same-origin EventSource sends none), and so are tools that send no Sec-Fetch-*."""
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or self.deck.origin_allowed(origin)

    def _token_ok(self) -> bool:
        return self.deck.token_valid(self.headers.get(TOKEN_HEADER))

    def _discard(self, length: int) -> None:
        """Read and drop ``length`` body bytes (never kept in memory at once)."""
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    def _read_json_body(self) -> tuple[bool, Any]:
        """Read and parse a capped JSON body. On failure the error response is sent
        and ``(False, None)`` returned."""
        if self.headers.get("Transfer-Encoding"):
            self._error(HTTPStatus.LENGTH_REQUIRED, "chunked request bodies are not accepted")
            return False, None
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            length = 0
        else:
            try:
                length = int(raw_len)
            except ValueError:
                length = -1
        if length < 0:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return False, None
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            if length <= DRAIN_LIMIT:
                self._discard(length)  # so the client reads the 413 instead of a connection reset
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"request body too large (limit {MAX_BODY_BYTES} bytes)")
            return False, None
        raw = self.rfile.read(length) if length else b""
        if len(raw) != length:
            self._error(HTTPStatus.BAD_REQUEST, "incomplete request body")
            return False, None
        if not raw.strip():
            return True, {}
        try:
            return True, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid JSON")
            return False, None

    # ---- verbs
    def do_GET(self) -> None:  # noqa: N802
        self._route_get(head=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._route_get(head=True)

    def _drain_unused_body(self) -> None:
        """Before refusing a POST: read and drop its (capped) body and close the connection.

        Closing a socket with unread request bytes makes Windows reset the connection, so
        the client would see "connection aborted" instead of the 401/403/404 answer (a stale
        Connect page could not tell "restarted" from "not running")."""
        self.close_connection = True
        if self.headers.get("Transfer-Encoding"):
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < length <= DRAIN_LIMIT:
            try:
                self.connection.settimeout(2.0)  # a client that never sends its body must not hold the thread
            except OSError:
                return
            self._discard(length)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._drain_unused_body()
            self._send_simple(HTTPStatus.FORBIDDEN, b"forbidden host\n")
            return
        if not self._origin_ok():
            self._drain_unused_body()
            self._error(HTTPStatus.FORBIDDEN, "forbidden origin")
            return
        parts = urlsplit(self.path)
        path = parts.path
        if path != "/status" and not path.startswith("/api/"):
            self._drain_unused_body()
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._token_ok():
            self._drain_unused_body()
            self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong X-ND-Token")
            return
        ok, data = self._read_json_body()
        if not ok:
            return
        if path == "/status":
            if not isinstance(data, dict) or not isinstance(data.get("state"), str):
                self._error(HTTPStatus.BAD_REQUEST, 'expected {"state": ...}')
                return
            if data.get("state") == "spotify" and not self.deck.player_key_valid(self.headers.get("X-ND-Key")):
                # The SDK's reports set the device the app plays to: only from the window the app opened (its
                # launch key), never from a process that read the X-ND-Token from /player (review RCOMP-3).
                self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong X-ND-Key")
                return
            self.deck._on_status(data)
            self._send_simple(HTTPStatus.NO_CONTENT)
            return
        self._api("POST", path, parts.query, data)

    def _route_get(self, *, head: bool) -> None:
        if not self._host_ok():
            self._send_simple(HTTPStatus.FORBIDDEN, b"forbidden host\n")
            return
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        if path == "/healthz":
            self._send_json(HTTPStatus.OK, {"app": APP_ID, "version": __version__})
        elif path in PAGES:
            name, kind = PAGES[path]
            site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            if kind == "player" and site in ("cross-site", "same-site"):
                # The player opens from the app's own launch (Sec-Fetch-Site: none) or reloads itself
                # (same-origin); another site's window.open or link never gets it (a second player page).
                self._send_simple(HTTPStatus.FORBIDDEN, b"NeedleDropper's player page opens only from the app\n")
                return
            self._page(name, kind)
        elif path == "/events" and not head:
            if not self._origin_ok():
                self._error(HTTPStatus.FORBIDDEN, "forbidden origin")
                return
            query = parse_qs(parts.query)
            kind = (query.get("client") or [CLIENT_PLAYER])[0]
            if kind not in CLIENT_KINDS:
                self._error(HTTPStatus.BAD_REQUEST, "client must be 'panel' or 'player'")
                return
            if kind == CLIENT_PLAYER and not self.deck.player_key_valid((query.get("k") or [""])[0]):
                self._retire()
                return
            self._events(kind)
        elif path == SPOTIFY_TOKEN_PATH:
            self._spotify_token(head)
        elif path.startswith("/api/"):
            if not self._origin_ok():
                self._error(HTTPStatus.FORBIDDEN, "forbidden origin")
                return
            if not self._token_ok():
                self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong X-ND-Token")
                return
            self._api("GET", path, parts.query, None)
        elif path.startswith("/media/"):
            self._media(path[len("/media/"):])
        elif path.startswith("/static/"):
            self._static(path[len("/static/"):])
        elif path == "/favicon.ico":
            self._static("favicon.svg")
        else:
            self._send_simple(HTTPStatus.NOT_FOUND, b"not found\n")

    # ---- handlers
    def _spotify_token(self, head: bool) -> None:
        """The Web Playback SDK's ``getOAuthToken`` (D13): ``{"accessToken": ...}`` -- the access token
        only, never the refresh token -- and only for a ``fetch`` from the player page itself: the
        browser must mark it ``Sec-Fetch-Site: same-origin`` (a page on any other site, a typed
        address or a tool without it is refused), an ``Origin`` must be the deck's own, and
        ``X-ND-Token`` must match. ``Cache-Control: no-store`` (``_send_simple``)."""
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        origin = self.headers.get("Origin")
        if site != "same-origin" or (origin is not None and not self.deck.origin_allowed(origin)):
            self._error(HTTPStatus.FORBIDDEN, "the Spotify token is only for NeedleDropper's player page")
            return
        if not self._token_ok():
            self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong X-ND-Token")
            return
        if not self.deck.player_key_valid(self.headers.get("X-ND-Key")):
            # The launch key is in the player window's address fragment only (never served): a page
            # anyone fetched from /player has the X-ND-Token, not this (review RCOMP-3).
            self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong X-ND-Key")
            return
        if head:
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "use GET")
            return
        try:
            token = self.deck.spotify.token()
        except LookupError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a sign-in problem: the page reports it, the app logs it
            log.info("the player asked for a Spotify token, but there is none: %s", exc)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Spotify sign-in unavailable")
            return
        self._send_simple(HTTPStatus.OK, json.dumps({"accessToken": token}).encode("utf-8"),
                          "application/json; charset=utf-8", {"Pragma": "no-cache"})

    def _api(self, method: str, path: str, query: str, body: Any) -> None:
        api = self.deck.api
        if api is None:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "the setup API is not available in this mode")
            return
        q = {k: v[0] for k, v in parse_qs(query).items()}
        try:
            code, payload = api(method, path, q, body)
        except Exception as exc:  # never let an API bug kill the handler thread silently
            log.exception("deck: API %s %s failed", method, path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"internal error: {exc}")
            return
        self._send_json(code, payload)

    def _page(self, name: str, kind: str) -> None:
        p = self.deck.static_dir / name
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            self._send_simple(HTTPStatus.INTERNAL_SERVER_ERROR, b"page missing\n")
            return
        body = text.replace(TOKEN_PLACEHOLDER, html.escape(self.deck.token, quote=True)).encode("utf-8")
        self._send_simple(HTTPStatus.OK, body, "text/html; charset=utf-8", {
            "Content-Security-Policy": _CSP[kind],
            "X-Frame-Options": "DENY",
            "Referrer-Policy": _REFERRER[kind],
            "Cross-Origin-Opener-Policy": "same-origin" if kind == "connect" else "unsafe-none",
        })

    def _static(self, name: str) -> None:
        name = name.strip("/")
        if not name or "/" in name or "\\" in name or name.startswith(".") or name.endswith(".html"):
            self._send_simple(HTTPStatus.NOT_FOUND, b"not found\n")
            return
        base = self.deck.static_dir.resolve()
        p = (base / name).resolve()
        if p.parent != base or not p.is_file():
            self._send_simple(HTTPStatus.NOT_FOUND, b"not found\n")
            return
        ctype = _STATIC_TYPES.get(p.suffix.lower(), "application/octet-stream")
        try:
            body = p.read_bytes()
        except OSError:
            self._send_simple(HTTPStatus.INTERNAL_SERVER_ERROR, b"read error\n")
            return
        self._send_simple(HTTPStatus.OK, body, ctype, {"Cache-Control": "no-cache"})

    def _retire(self) -> None:
        """A player page without this app start's launch key: one left running by an earlier start (the
        old app crashed, or quit without closing it), or a page opened by hand. It is told to retire --
        stop, disconnect and close itself -- and never counts as the player (review ON-1)."""
        self.deck._note_stale_player()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(b"retry: 60000\n\n" + self.deck._encode({"cmd": "retire"}).encode("utf-8"))
            self.wfile.flush()
        except OSError:
            pass
        self.close_connection = True

    def _events(self, kind: str) -> None:
        deck = self.deck
        # Register before the headers go out: once the page sees the response it
        # is guaranteed to receive every later command.
        client, replay = deck._add_client(kind)
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n: connected\n\n")
            for msg in replay:
                self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()
            deck._touch(kind)
            while not deck._stopping.is_set():
                try:
                    msg = client.q.get(timeout=deck.heartbeat_sec)
                except queue.Empty:
                    msg = ": hb\n\n"
                if msg is None:
                    break
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
                deck._touch(kind)
        except OSError:
            pass  # page closed / reloaded
        finally:
            deck._remove_client(client)
            self.close_connection = True

    def _media(self, token: str) -> None:
        token = token.split("/", 1)[0]
        p = self.deck._media_path(token)
        if p is None:
            self._send_simple(HTTPStatus.NOT_FOUND, b"unknown media\n")
            return
        try:
            size = p.stat().st_size
        except OSError:
            self._send_simple(HTTPStatus.NOT_FOUND, b"file missing\n")
            return
        ctype = _MEDIA_TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        rng = parse_range(self.headers.get("Range"), size)
        if rng == "unsatisfiable":
            self._send_simple(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"",
                              headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
            return
        if rng is None:
            start, end, code = 0, size - 1, HTTPStatus.OK
        else:
            start, end = rng  # type: ignore[misc]
            code = HTTPStatus.PARTIAL_CONTENT
        length = max(0, end - start + 1)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if code == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or length == 0:
            return
        try:
            with open(p, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except OSError:
            pass  # browsers abort media requests when seeking; that's normal


Launcher = Callable[[list[str]], Any]


def child_environment() -> dict[str, str]:
    """The environment for a program the app starts (the browser): without PyInstaller's own
    variables (``_PYI_*``, ``_MEIPASS*``), which a frozen app's children would otherwise inherit."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(("_PYI_", "_MEIPASS"))}


class _NormalDllSearch:
    """PyInstaller's bootloader calls ``SetDllDirectoryW(<its unpack folder>)``, which child processes
    inherit: a browser started from the frozen app could load the wrong DLLs from there (suspected for
    real-client finding #11). Inside this block the normal search order applies
    (``SetDllDirectoryW(NULL)``) -- just for creating the child; the app's own setting is put back
    afterwards. A no-op outside a frozen Windows app."""

    def __enter__(self) -> "_NormalDllSearch":
        self.saved: Optional[str] = None
        self.active = False
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            return self
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            buf = ctypes.create_unicode_buffer(32768)
            n = k32.GetDllDirectoryW(len(buf), buf)
            self.saved = buf.value if n else None
            self.active = bool(k32.SetDllDirectoryW(None))
        except Exception as exc:  # noqa: BLE001 - best effort
            log.debug("SetDllDirectoryW(NULL) failed: %s", exc)
        return self

    def __exit__(self, *exc: Any) -> None:
        if not self.active:
            return
        try:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(self.saved)
        except Exception as e:  # noqa: BLE001
            log.debug("restoring the DLL directory failed: %s", e)


def _default_launcher(args: list[str], log_file: Optional[Path] = None) -> Any:
    """Start ``args`` detached, with a clean environment; stderr goes to ``log_file`` when given (the
    browser's own complaints, for the log folder). Returns the process (polled for an early exit)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    err: Any = subprocess.DEVNULL
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            if log_file.is_file() and log_file.stat().st_size > BROWSER_LOG_MAX:
                os.replace(log_file, log_file.with_name(log_file.name + ".1"))  # keep one older file
            err = open(log_file, "ab")  # noqa: SIM115 - handed to the child, closed below
        except OSError:
            err = subprocess.DEVNULL
    try:
        with _NormalDllSearch():
            return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                    close_fds=True, env=child_environment(),
                                    creationflags=flags if sys.platform == "win32" else 0)
    finally:
        if err is not subprocess.DEVNULL:
            err.close()


_REAL_LAUNCHER = _default_launcher


def end_process_tree(proc: Any) -> bool:
    """End a process this app started, and its children (Windows: ``taskkill /PID <pid> /T /F``; else
    ``kill``). Only for a ``Popen`` this process holds: its handle keeps the pid from being reused."""
    if not isinstance(proc, subprocess.Popen) or proc.poll() is not None:
        return False
    try:
        if sys.platform == "win32":
            exe = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")
            subprocess.run([exe, "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
        proc.wait(timeout=5)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("could not end the player browser (pid %s): %s", getattr(proc, "pid", "?"), exc)
        return False


def chrome_widevine_dir() -> Optional[Path]:
    """Chrome's own Widevine component, ``%LOCALAPPDATA%\\Google\\Chrome\\User Data\\WidevineCdm``, or None."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    p = Path(base, "Google", "Chrome", "User Data", "WidevineCdm")
    return p if p.is_dir() else None


def ensure_widevine(profile_dir: Path, source: Optional[Path] = None) -> bool:
    """Copy Chrome's Widevine component into the player's own profile when it is missing there (D11).

    The Spotify Web Playback SDK plays DRM-protected audio (EME, ``com.widevine.alpha``); a fresh
    profile has no Widevine until Chrome's component updater fetches it (about a minute), so the SDK
    would fail at first. Returns True when the profile has it afterwards. Never raises."""
    dest = Path(profile_dir) / "WidevineCdm"
    try:
        if dest.is_dir() and any(dest.iterdir()):
            return True
    except OSError:
        return False
    src = source if source is not None else chrome_widevine_dir()
    if src is None:
        log.info("no Widevine component in Chrome's own profile to copy; the player's Chrome fetches it itself "
                 "(in-app Spotify waits for that)")
        return False
    tmp = dest.with_name(dest.name + ".nd-copy")
    try:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, tmp)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
    except OSError as exc:
        log.info("could not copy Chrome's Widevine component into the player profile (%s); Chrome fetches it "
                 "itself in a minute or so", exc)
        shutil.rmtree(tmp, ignore_errors=True)
        return False
    log.info("copied Chrome's Widevine component into the player profile (%s)", dest)
    return True


#: Report fields :class:`SpotifySdk` keeps (everything else the page sends is dropped).
_SDK_FIELDS = ("event", "deviceId", "kind", "message", "ok", "detail", "playId", "active", "paused", "position",
               "duration", "loading", "uri", "id", "linked", "started", "volume")


class SpotifySdk:
    """The Spotify Web Playback SDK in the player page: in-app Spotify, the Connect device
    "NeedleDropper" (D6-D10). ``deck.spotify`` holds what the page reported and sends it commands.

    * :meth:`enable` (a Spotify sign-in that allows streaming, app mode on) makes the page create a
      ``Spotify.Player`` and connect; its ``getOAuthToken`` reads :data:`SPOTIFY_TOKEN_PATH`, served
      from ``token_source``. :meth:`standby` (app mode, but no such sign-in yet) and :meth:`disable`
      (not app mode) disconnect it. While a provider in app mode owns it (:attr:`app_mode`) the deck
      opens the player in Chrome first (D11). A (re)connecting page is told again
      (:meth:`replay`); a page that connects or goes away makes the SDK "not ready" until the page
      reports ``ready`` (:meth:`page_connected` / :meth:`page_gone`).
    * The page reports ``{"state": "spotify", "event": ...}``: ``ready`` / ``not_ready`` (with the
      device id, which can change), ``error`` (``kind`` = the SDK's ``initialization_error`` /
      ``authentication_error`` / ``account_error`` / ``playback_error``, or ``load`` / ``autoplay`` /
      ``browser``: not Chrome, so the page never loads the SDK),
      ``eme`` (Widevine usable, ``ok``), ``connect``, ``token`` (the token read failed), ``state`` /
      ``tick`` (``player_state_changed`` / ``getCurrentState``: ``active``, ``paused``, ``position``,
      ``duration``, ``loading``, ``uri`` / ``id`` / ``linked``, ``playId``, ``started``) and
      ``stopped`` (a ``stop`` command finished: faded out and paused).
    * Commands (``{"cmd": "spotify", "op": ...}``, players only): ``connect``, ``disconnect``,
      ``reconnect``; ``start`` (``playId``, ``uri``, ``volume`` 0-100, ``fadeInMs``: volume 0 now, then
      the fade-in once that track plays -- the app sends the Web API play itself); ``fade``
      (``volume``, ``ms``); ``stop`` (``playId``, ``fadeOutMs``, ``delayMs``: fade to 0, then pause);
      ``pause`` (volume 0 and pause at once).

    The provider (``SpotifyProvider`` in app mode) waits on these reports (:meth:`wait_ready`,
    :meth:`wait_for`) instead of polling the Web API. Thread-safe; ``listener(event, report)`` runs on
    the reporting request's thread, outside the lock.
    """

    def __init__(self, deck: "Deck") -> None:
        self._deck = deck
        self._cond = threading.Condition()
        self._owner: Any = None
        self._token_source: Optional[Callable[[], str]] = None
        self._listener: Optional[Callable[[str, dict[str, Any]], Any]] = None
        self.wanted = False
        self.wanted_at: Optional[float] = None
        #: a Spotify provider in app mode owns the in-app player (with or without a usable sign-in)
        self.app_mode = False
        #: when the current player page connected (the wait for "ready" starts again then)
        self.page_since: Optional[float] = None
        self.device_id: Optional[str] = None
        self.ready = False
        self.ready_at: Optional[float] = None
        self.ready_seq = 0
        #: when the ready SDK went offline (not_ready), until it is ready again (a blip is not "no player")
        self.offline_at: Optional[float] = None
        self.error: Optional[dict[str, Any]] = None
        self.eme: Optional[bool] = None
        self.eme_detail = ""
        self.connect_ok: Optional[bool] = None
        self.tick: Optional[dict[str, Any]] = None
        self.reconnects = 0
        self._seq = 0
        self._events: "collections.deque[dict[str, Any]]" = collections.deque(maxlen=_SDK_EVENT_MEMORY)

    # -- the app side
    def enable(self, token_source: Callable[[], str], *, owner: Any = None,
               listener: Optional[Callable[[str, dict[str, Any]], Any]] = None) -> bool:
        """Want the SDK connected; idempotent (the newest owner's token source and listener win).
        True when this call switched it on (the page is then told to connect)."""
        with self._cond:
            self._token_source, self._owner, self._listener = token_source, owner, listener
            self.app_mode = True
            first = not self.wanted
            if first:
                self.wanted = True
                self.wanted_at = time.monotonic()
                self.ready, self.device_id, self.error, self.tick = False, None, None, None
                self._cond.notify_all()
        if first:
            log.info("in-app Spotify: connecting NeedleDropper's player to Spotify (device %r)", SPOTIFY_DEVICE_NAME)
            self.command("connect", name=SPOTIFY_DEVICE_NAME)
        return first

    def standby(self, owner: Any = None) -> bool:
        """App mode without a sign-in that allows it (not signed in, or signed in before 1.2.1): the
        SDK is disconnected, but the player still opens in Chrome first, so a sign-in later needs no
        other browser. True when this call disconnected a connected SDK."""
        with self._cond:
            was = self.wanted
            self.wanted = False
            self.app_mode = True
            self._owner = owner
            self._token_source = self._listener = None
            self.ready, self.device_id, self.tick = False, None, None
            self._cond.notify_all()
        if was:
            log.info("in-app Spotify: disconnecting NeedleDropper's player (no Spotify sign-in that allows it)")
            self.command("disconnect")
        return was

    def disable(self, owner: Any = None) -> bool:
        """Not app mode any more (only for ``owner``, when given): the page disconnects the SDK.
        True when it was connected."""
        with self._cond:
            if owner is not None and self._owner is not owner:
                return False
            was = self.wanted
            self.wanted = False
            self.app_mode = False
            self._token_source = self._owner = self._listener = None
            self.ready, self.device_id, self.tick = False, None, None
            self._cond.notify_all()
        if was:
            log.info("in-app Spotify: disconnecting NeedleDropper's player from Spotify")
            self.command("disconnect")
        return was

    def page_connected(self) -> None:
        """A player page connected (called under the deck's lock): until it reports ``ready``, the
        SDK there is not ready -- a page that was reloaded or reopened has a new device id -- and the
        wait for it starts again. A page whose event stream merely reconnected reports its live SDK
        again when it hears the replayed ``connect``."""
        with self._cond:
            self.page_since = time.monotonic()
            self.offline_at = None
            if self.wanted:
                self.ready, self.device_id, self.tick = False, None, None
                self.error, self.connect_ok = None, None
                self.wanted_at = self.page_since
            self._cond.notify_all()

    def page_gone(self) -> None:
        """No player page is connected any more (called under the deck's lock): nothing can play."""
        with self._cond:
            self.page_since = None
            self.ready, self.tick = False, None
            self._cond.notify_all()

    def reconnect(self, *, restart_grace: bool = True) -> bool:
        """Disconnect and connect again (after a token refresh, or to re-check a player that failed).
        ``restart_grace=False`` (the periodic re-check of a player that never got ready): the wait for
        "ready" is not restarted, so such a player stays "no player" instead of counting as connecting
        again every minute (review RCOMP-6)."""
        with self._cond:
            if not self.wanted:
                return False
            self.reconnects += 1
            self.ready, self.error = False, None
            if restart_grace:
                self.wanted_at = time.monotonic()
            self._cond.notify_all()
        log.info("in-app Spotify: reconnecting NeedleDropper's player")
        self.command("reconnect", name=SPOTIFY_DEVICE_NAME)
        return True

    def owned_by(self, owner: Any) -> bool:
        with self._cond:
            return self.wanted and self._owner is owner

    def token(self) -> str:
        """The access token for the page (``LookupError`` while in-app Spotify is off)."""
        with self._cond:
            source = self._token_source if self.wanted else None
        if source is None:
            raise LookupError("in-app Spotify is off (no Spotify sign-in that allows it)")
        return source()

    def command(self, op: str, **fields: Any) -> int:
        """Send ``{"cmd": "spotify", "op": op, ...}`` to the player page(s); returns how many got it."""
        return self._deck.send({"cmd": "spotify", "op": op, **fields})

    def replay(self) -> list[dict[str, Any]]:
        """What a (re)connecting player page needs to hear (called under the deck's lock)."""
        with self._cond:
            wanted = self.wanted
        return [{"cmd": "spotify", "op": "connect", "name": SPOTIFY_DEVICE_NAME, "replay": True}] if wanted else []

    # -- the page side
    def report(self, data: dict[str, Any]) -> None:
        """A ``{"state": "spotify", ...}`` report from the player page (POST /status)."""
        event = str(data.get("event") or "")
        now = time.monotonic()
        rec: dict[str, Any] = {k: data[k] for k in _SDK_FIELDS if k in data}
        for k in ("message", "detail", "uri", "id", "linked", "deviceId", "playId", "kind"):
            if k in rec:
                rec[k] = str(rec[k])[:300]
        rec["event"] = event
        msg: Optional[str] = None
        with self._cond:
            self._seq += 1
            rec["seq"], rec["t"] = self._seq, now
            if event == "ready":
                dev = str(data.get("deviceId") or "")
                changed = bool(self.device_id) and dev != self.device_id
                if dev:
                    self.device_id, self.ready, self.ready_at, self.ready_seq = dev, True, now, self._seq
                    self.offline_at = None
                msg = f"in-app Spotify ready{' (a new device id)' if changed else ''}"
            elif event == "not_ready":
                dev = str(data.get("deviceId") or "")
                if not dev or dev == self.device_id:
                    if self.ready:
                        self.offline_at = now
                    self.ready = False
                msg = "in-app Spotify went offline (not_ready); the SDK reconnects by itself"
            elif event == "error":
                self.error = {"kind": rec.get("kind") or "error", "message": rec.get("message") or "", "at": now,
                              "seq": self._seq}
                msg = f"in-app Spotify error {self.error['kind']}: {self.error['message']}"
            elif event == "eme":
                self.eme = bool(data.get("ok"))
                self.eme_detail = str(data.get("detail") or "")[:300]
                msg = "Widevine is usable in the player" if self.eme else \
                    f"Widevine (DRM) is not usable in the player yet: {self.eme_detail}"
            elif event == "connect":
                self.connect_ok = bool(data.get("ok"))
                if not self.connect_ok:
                    msg = "in-app Spotify: the SDK's connect() failed"
            elif event == "token":
                msg = f"in-app Spotify: the player could not read a token ({rec.get('detail', '')})"
            elif event in ("tick", "state"):
                self.tick = rec
            self._events.append(rec)
            self._cond.notify_all()
            listener = self._listener if self.wanted else None
        if msg:
            if event in ("error", "connect") or (event == "eme" and not rec.get("ok")):
                log.warning("%s", msg)
            else:
                log.info("%s", msg)
        if listener is not None:
            try:
                listener(event, dict(rec))
            except Exception:  # noqa: BLE001 - a listener bug must not break the page's report
                log.exception("in-app Spotify: the listener failed")

    # -- waits and state
    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    def _give_up(self) -> bool:
        return self._deck._interrupt.is_set() or self._deck._stopping.is_set()

    def wait_ready(self, timeout: float) -> Optional[str]:
        """The device id once the SDK is ready (now, or within ``timeout``); None otherwise."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                if self.ready and self.device_id:
                    return self.device_id
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.wanted or self._give_up():
                    return None
                self._cond.wait(min(remaining, 0.25))

    def wait_for(self, predicate: Callable[[dict[str, Any]], bool], *, after_seq: int = 0,
                 timeout: float = 0.0, cursor: Optional[dict[str, int]] = None) -> Optional[dict[str, Any]]:
        """The first report newer than ``after_seq`` for which ``predicate`` holds (within ``timeout``).
        ``cursor["seq"]`` is set to the newest report looked at, so a later call goes on from there."""
        deadline = time.monotonic() + max(0.0, timeout)
        checked = after_seq
        try:
            with self._cond:
                while True:
                    for rec in self._events:
                        if rec["seq"] > checked:
                            checked = rec["seq"]
                            try:
                                if predicate(rec):
                                    return dict(rec)
                            except Exception:  # noqa: BLE001 - a bad predicate means "no"
                                pass
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or self._give_up():
                        return None
                    self._cond.wait(min(remaining, 0.25))
        finally:
            if cursor is not None:
                cursor["seq"] = checked

    def wait_event(self, event: str, *, play_id: Optional[str] = None, after_seq: int = 0,
                   timeout: float = 0.0) -> Optional[dict[str, Any]]:
        return self.wait_for(lambda r: r.get("event") == event and (play_id is None or r.get("playId") == play_id),
                             after_seq=after_seq, timeout=timeout)

    def events_since(self, after_seq: int) -> list[dict[str, Any]]:
        with self._cond:
            return [dict(r) for r in self._events if r["seq"] > after_seq]

    def snapshot(self) -> dict[str, Any]:
        """Everything the app knows about the SDK (no token): for the provider, status and diagnostics."""
        with self._cond:
            snap: dict[str, Any] = {
                "wanted": self.wanted, "appMode": self.app_mode, "ready": self.ready, "deviceId": self.device_id,
                "readyAt": self.ready_at, "readySeq": self.ready_seq, "wantedAt": self.wanted_at,
                "offlineAt": self.offline_at,
                "pageSince": self.page_since,
                "error": dict(self.error) if self.error else None, "eme": self.eme, "emeDetail": self.eme_detail,
                "connected": self.connect_ok, "tick": dict(self.tick) if self.tick else None, "seq": self._seq,
                "reconnects": self.reconnects}
        deck = self._deck
        snap.update(pageOpen=deck.page_open, opening=deck.player_opening, browser=deck.page_browser,
                    autoOpen=deck.auto_open, now=time.monotonic())
        return snap


class Deck:
    """The Connect page + player server and the command channel to the player page.

    Player-window policy (set from config by the companion): :attr:`auto_open`
    (``"youtube"`` | ``"always"`` | ``"never"``), :attr:`browser`,
    :attr:`player_position` and :attr:`profile_dir`. ``launcher`` (starts the
    browser process) and ``url_opener`` (default-browser fallback) are
    injectable so tests never open a window.

    :attr:`on_player_status` (``fn(status, play_command, title_card)``) is called on
    the request thread after every player status report; ``play_command`` is the
    ``play`` command the report refers to (by ``playId``) and ``title_card`` the
    ``show`` command on screen, both None when unknown.
    """

    def __init__(self, port: int = DEFAULT_PORT, *, host: str = "127.0.0.1", static_dir: Optional[Path] = None,
                 heartbeat_sec: float = HEARTBEAT_SEC, connected_window_sec: float = CONNECTED_WINDOW_SEC,
                 token: Optional[str] = None, api: Optional[ApiHandler] = None,
                 launcher: Optional[Launcher] = None, url_opener: Optional[Callable[[str], Any]] = None) -> None:
        self.host = host
        self.requested_port = int(port)
        self.static_dir = Path(static_dir) if static_dir else STATIC_DIR
        self.heartbeat_sec = heartbeat_sec
        self.connected_window_sec = connected_window_sec
        self._token = token or secrets.token_urlsafe(32)
        #: The player window's launch key: only in the address the app opens it with (a fragment, never sent
        #: to any server), so only the window this app start launched is its player (ON-1, RCOMP-3).
        self._player_key = secrets.token_urlsafe(18)
        self._stale_noted_at: Optional[float] = None
        self.api: Optional[ApiHandler] = api
        self.launcher: Launcher = launcher or self._launch
        self.url_opener: Callable[[str], Any] = url_opener or webbrowser.open
        # player-window policy
        self.auto_open = "youtube"
        self.browser = "edge"
        self.player_position: Optional[tuple[int, int]] = None
        self.player_corner = "topright"
        self.profile_dir: Optional[Path] = None
        self.browser_log: Optional[Path] = None   # the browser's stderr (logs\browser.log)
        #: the never-activating window manager (deck/window.py; None: none, the plain window)
        self.window: Any = None
        #: True while the game is the foreground window: no player window is created then, because
        #: Chromium activates a window when it creates it (real-client #8 / #13)
        self.game_in_front: Callable[[], bool] = _game_in_front
        self._player_proc: Any = None
        self._player_kind: Optional[str] = None
        self._bad_browsers: set[str] = set()
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None
        self._server6: Optional[_Server] = None
        self._thread6: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._clients: list[_Client] = []
        self._stopping = threading.Event()
        self._interrupt = threading.Event()  # the app is quitting: waits for the player end at once
        self._last_seen: dict[str, Optional[float]] = {CLIENT_PANEL: None, CLIENT_PLAYER: None}
        self._player_gone_at: Optional[float] = None
        self._player_opened_at: Optional[float] = None
        self.player_opened = 0  # how many times a player window was launched
        self._last_status: Optional[dict[str, Any]] = None
        self._status_by_play: dict[str, dict[str, Any]] = {}
        self._status_seq = 0
        self._media: dict[str, Path] = {}
        self._media_by_path: dict[str, str] = {}
        self._last_show: Optional[dict[str, Any]] = None
        self._active_play: Optional[tuple[dict[str, Any], float]] = None
        self._plays: dict[str, dict[str, Any]] = {}
        self.on_player_status: Optional[Callable[[dict[str, Any], Optional[dict[str, Any]],
                                                  Optional[dict[str, Any]]], Any]] = None
        self.sent = 0
        #: in-app Spotify: the Web Playback SDK in the player page (D6)
        self.spotify = SpotifySdk(self)
        #: the browser the connected player page says it runs in ("chrome" / "edge" / "other")
        self._page_browser: Optional[str] = None
        #: copies Chrome's Widevine component into the player profile (None: :func:`ensure_widevine`)
        self.widevine_copier: Optional[Callable[[Path], Any]] = None
        self._csp_seen: set[tuple[str, str]] = set()
        self._chrome_cache: Optional[tuple[float, bool]] = None

    def _launch(self, args: list[str]) -> Any:
        """The default launcher: :func:`_default_launcher`, with the browser's stderr in the log folder."""
        fn = _default_launcher
        if fn is _REAL_LAUNCHER and self.browser_log is not None:
            return fn(args, self.browser_log)
        return fn(args)

    # ------------------------------------------------------------ server ----
    def start(self) -> "Deck":
        """Bind and serve in a daemon thread. Raises OSError if the port is taken."""
        if self._server is not None:
            return self
        self._stopping.clear()
        tries = 5 if self.requested_port == 0 else 1
        for attempt in range(tries):
            srv = _Server((self.host, self.requested_port), _Handler)
            srv6 = None
            if self.host == "127.0.0.1":
                try:
                    srv6 = _Server6(("::1", srv.server_address[1]), _Handler)
                except OSError as exc:
                    if getattr(exc, "errno", None) in _ADDR_IN_USE or getattr(exc, "winerror", None) == 10048:
                        srv.server_close()
                        if attempt + 1 < tries:
                            continue  # a free port taken on [::1] only: pick another
                        raise OSError(exc.errno, f"port {srv.server_address[1]} is in use on [::1] by another "
                                                 f"program ({exc})") from exc
                    log.debug("no IPv6 loopback listener (%s); the player page reaches 127.0.0.1 only", exc)
            break
        srv.deck = self
        self._server = srv
        self._thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.25},
                                        name="needledropper-deck", daemon=True)
        self._thread.start()
        self._server6 = srv6
        self._thread6 = None
        if srv6 is not None:
            srv6.deck = self
            self._thread6 = threading.Thread(target=srv6.serve_forever, kwargs={"poll_interval": 0.25},
                                             name="needledropper-deck-ipv6", daemon=True)
            self._thread6.start()
        log.info("deck listening on %s", self.url)
        return self

    def stop(self) -> None:
        srv = self._server
        if srv is None:
            return
        closed = self.close_player()
        self._stopping.set()
        with self._lock:
            for c in self._clients:
                c.q.put(None)
        srv6 = getattr(self, "_server6", None)
        for server_ in (srv, srv6):
            if server_ is not None:
                server_.shutdown()
                server_.server_close()
        for thread in (self._thread, getattr(self, "_thread6", None)):
            if thread:
                thread.join(timeout=5)
        self._server = None
        self._thread = None
        self._server6 = None
        self._thread6 = None
        self._end_player_process(PLAYER_QUIT_WAIT_S if closed else 0.0)

    def _end_player_process(self, wait_s: float) -> None:
        """The browser this app start launched for the player: once its window got WM_CLOSE it exits by
        itself; when it still runs after ``wait_s`` (no window manager, the window never found, or it hangs)
        its process tree is ended. A browser left running holds the player profile, and its page would
        connect to the next start (review ON-1). Only a process this deck launched (a real ``Popen``)."""
        with self._lock:
            proc = self._player_proc
        if not isinstance(proc, subprocess.Popen):
            return
        deadline = time.monotonic() + max(0.0, wait_s)
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if proc.poll() is not None:
            return
        log.info("the player browser (pid %s) did not close with its window; ending it", proc.pid)
        end_process_tree(proc)

    def __enter__(self) -> "Deck":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else self.requested_port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        """The control panel's URL."""
        return self.base_url + "/"

    @property
    def panel_url(self) -> str:
        return self.url

    @property
    def player_origin(self) -> str:
        """The player page's origin. ``localhost``, not ``127.0.0.1``: from a ``127.0.0.1`` origin YouTube
        refused 79 of the 100 bundled videos with error 150, from ``localhost`` none (D12). Only the
        player moves there; :attr:`base_url`, the Connect page and Spotify's redirect stay on 127.0.0.1."""
        return f"http://localhost:{self.port}"

    @property
    def player_url(self) -> str:
        """The player page's address (see :attr:`player_origin`)."""
        return self.player_origin + "/player"

    @property
    def player_key(self) -> str:
        """This app start's launch key (see :attr:`player_launch_url`)."""
        return self._player_key

    @property
    def player_launch_url(self) -> str:
        """The address the player window opens: :attr:`player_url` with the launch key in its fragment. The
        page sends the key with its event stream and the Spotify token request; a page without it (left by
        an earlier start, opened by hand or by another site) is told to retire (``{"cmd": "retire"}``)."""
        return f"{self.player_url}#k={self._player_key}"

    def player_key_valid(self, given: Optional[str]) -> bool:
        if not given:
            return False
        return hmac.compare_digest(given.strip().encode("utf-8", "replace"), self._player_key.encode("ascii"))

    def _note_stale_player(self) -> None:
        now = time.monotonic()
        with self._lock:
            quiet = self._stale_noted_at is not None and now - self._stale_noted_at < 300
            if not quiet:
                self._stale_noted_at = now
        if not quiet:
            log.info("a player page from an earlier start of NeedleDropper (or opened by hand) connected; it was "
                     "told to close itself")

    # ---------------------------------------------------------- security ----
    @property
    def token(self) -> str:
        """The per-process API token (only ever sent inside the served pages)."""
        return self._token

    def token_valid(self, given: Optional[str]) -> bool:
        if not given:
            return False
        return hmac.compare_digest(given.strip().encode("utf-8", "replace"), self._token.encode("ascii"))

    def host_allowed(self, host: Optional[str]) -> bool:
        """DNS-rebinding guard: only ``127.0.0.1:<port>`` / ``localhost:<port>``."""
        h = (host or "").strip().lower()
        if not h:
            return False
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.port == 80:
            allowed |= {"127.0.0.1", "localhost"}
        return h in allowed

    def origin_allowed(self, origin: str) -> bool:
        o = origin.strip().lower().rstrip("/")
        return o in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}")

    # ----------------------------------------------------------- clients ----
    def _add_client(self, kind: str = CLIENT_PLAYER) -> tuple[_Client, list[str]]:
        """Register an SSE client; returns it with the replay snapshot taken atomically
        (so a command sent concurrently is delivered exactly once)."""
        c = _Client(kind)
        with self._cond:
            self._clients.append(c)
            self._last_seen[kind] = time.monotonic()
            if kind == CLIENT_PLAYER:
                self._player_opened_at = None  # a launched window (if any) has arrived
                self._player_gone_at = None
                self.spotify.page_connected()
            replay = self._replay_messages_locked(kind)
            n = sum(1 for x in self._clients if x.kind == kind)
            panels = [x for x in self._clients if x.kind == CLIENT_PANEL and x is not c]
            self._cond.notify_all()
        log.info("%s page connected (%d open)", kind, n)
        if kind == CLIENT_PLAYER:
            self._nudge(panels)
        return c, replay

    def _remove_client(self, c: _Client) -> None:
        with self._cond:
            if c in self._clients:
                self._clients.remove(c)
                self._last_seen[c.kind] = time.monotonic()  # grace window starts at disconnect
                if c.kind == CLIENT_PLAYER and not any(x.kind == CLIENT_PLAYER for x in self._clients):
                    self._player_gone_at = time.monotonic()
                    self._page_browser = None
                    self.spotify.page_gone()
            n = sum(1 for x in self._clients if x.kind == c.kind)
            panels = [x for x in self._clients if x.kind == CLIENT_PANEL]
            self._cond.notify_all()
        log.info("%s page disconnected (%d open)", c.kind, n)
        if c.kind == CLIENT_PLAYER:
            self._nudge(panels)

    def _touch(self, kind: str = CLIENT_PLAYER) -> None:
        with self._lock:
            self._last_seen[kind] = time.monotonic()

    def _count(self, kind: Optional[str]) -> int:
        with self._lock:
            return sum(1 for c in self._clients if kind is None or c.kind == kind)

    @property
    def client_count(self) -> int:
        return self._count(None)

    @property
    def player_count(self) -> int:
        return self._count(CLIENT_PLAYER)

    @property
    def panel_count(self) -> int:
        return self._count(CLIENT_PANEL)

    @property
    def connected(self) -> bool:
        """A **player** page is connected: one was alive within the last ~10 s.

        The grace window bridges page reloads; a play command sent meanwhile is
        replayed (at the right offset) when the page reconnects. Open control
        panels do not count -- they cannot play anything.
        """
        with self._lock:
            if any(c.kind == CLIENT_PLAYER for c in self._clients):
                return True
            seen = self._last_seen[CLIENT_PLAYER]
            return seen is not None and time.monotonic() - seen <= self.connected_window_sec

    @property
    def page_open(self) -> bool:
        """A player page is connected right now (no grace window)."""
        return self._count(CLIENT_PLAYER) > 0

    player_connected = page_open

    def wait_player(self, timeout: float = PLAYER_CONNECT_TIMEOUT) -> bool:
        """Block until a player page is connected (or ``timeout``)."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while not any(c.kind == CLIENT_PLAYER for c in self._clients):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._stopping.is_set() or self._interrupt.is_set():
                    return False
                self._cond.wait(min(remaining, 0.25))
            return True

    def wait_connected(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.connected:
                return True
            time.sleep(0.05)
        return self.connected

    # ---------------------------------------------------------- commands ----
    @staticmethod
    def _encode(cmd: dict[str, Any]) -> str:
        return "data: " + json.dumps(cmd, separators=(",", ":"), default=str) + "\n\n"

    def send(self, cmd: dict[str, Any]) -> int:
        """Push a command to the pages that handle it: playback commands to players,
        the title card to players and panels. Returns the number of pages. Never raises."""
        try:
            msg = self._encode(cmd)
        except (TypeError, ValueError) as exc:
            log.warning("deck: cannot encode command %r: %s", cmd, exc)
            return 0
        kind = cmd.get("cmd")
        with self._lock:
            if kind == "show":
                self._last_show = dict(cmd)
            elif kind == "hide":
                self._last_show = None
            elif kind == "play":
                self._active_play = (dict(cmd), time.monotonic())
                pid = cmd.get("playId")
                if isinstance(pid, str) and pid:
                    self._plays[pid] = dict(cmd)
                    while len(self._plays) > _PLAY_MEMORY:
                        self._plays.pop(next(iter(self._plays)))
            elif kind == "stop":
                ap = self._active_play
                if ap is None or not cmd.get("playId") or ap[0].get("playId") == cmd.get("playId"):
                    self._active_play = None
            if kind in PLAYER_ONLY_CMDS:
                clients = [c for c in self._clients if c.kind == CLIENT_PLAYER]
            elif kind in PANEL_ONLY_CMDS:
                clients = [c for c in self._clients if c.kind == CLIENT_PANEL]
            else:
                clients = list(self._clients)
            self.sent += 1
        self._place_window(kind, cmd)
        for c in clients:
            c.q.put(msg)
        return len(clients)

    def _place_window(self, kind: Any, cmd: dict[str, Any]) -> None:
        """The player window is on screen only while a YouTube song plays (deck/window.py)."""
        win = self.window
        if win is None:
            return
        try:
            if kind == "play" and cmd.get("source") == "youtube":
                win.show()  # always on top at its corner, never activated
            elif kind == "stop":
                win.hide_later(int(cmd.get("fadeOutMs") or 0) / 1000.0 + 0.6)
            elif kind == "hide":
                win.hide_later(HIDE_AFTER_HIDE_S)
        except Exception as exc:  # noqa: BLE001 - a window problem must not stop playback
            log.debug("player window: %s", exc)

    def notify_panels(self, what: str = "state") -> int:
        """Ask open Connect pages to refresh their state now."""
        return self.send({"cmd": "state", "what": what})

    def _nudge(self, panels: Iterable[_Client]) -> None:
        msg = self._encode({"cmd": "state", "what": "player"})
        for c in panels:
            c.q.put(msg)

    def _replay_messages_locked(self, kind: str) -> list[str]:
        """What a (re)connecting page needs: the visible card, plus any active
        playback for players. Caller holds ``self._lock``."""
        out: list[str] = []
        show = self._last_show
        play = self._active_play if kind == CLIENT_PLAYER else None
        if kind == CLIENT_PLAYER:
            out.extend(self._encode(cmd) for cmd in self.spotify.replay())  # in-app Spotify: connect again
        if show:
            out.append(self._encode({**show, "replay": True}))
        if play:
            cmd, sent_at = play
            elapsed = int((time.monotonic() - sent_at) * 1000)
            resumed = {**cmd, "startMs": int(cmd.get("startMs") or 0) + elapsed, "fadeInMs": 500, "replay": True}
            out.append(self._encode(resumed))
        return out

    @property
    def now_showing(self) -> Optional[dict[str, Any]]:
        """The title card currently shown (last ``show`` not yet hidden)."""
        with self._lock:
            return dict(self._last_show) if self._last_show else None

    # ------------------------------------------------------------ status ----
    def _on_status(self, data: dict[str, Any]) -> None:
        state = data.get("state")
        if state in ("spotify", "csp"):
            # The SDK's reports (ticks among them) never touch the YouTube / local status a provider
            # waits on, and never reach on_player_status.
            self._touch(CLIENT_PLAYER)
            if state == "spotify":
                self.spotify.report(data)
            else:
                self._note_csp(data)
            return
        browser = data.get("browser")
        if state == "ready" and browser in ("chrome", "edge", "other"):
            with self._lock:
                self._page_browser = browser
        with self._cond:
            self._status_seq += 1
            st = {k: data[k] for k in ("state", "detail", "playId", "code") if k in data}
            st["seq"] = self._status_seq
            st["t"] = time.monotonic()
            self._last_status = st
            pid = st.get("playId")
            if isinstance(pid, str) and pid:
                self._status_by_play[pid] = st
                if len(self._status_by_play) > 200:
                    for k in list(self._status_by_play)[:100]:
                        del self._status_by_play[k]
            self._last_seen[CLIENT_PLAYER] = time.monotonic()
            play = dict(self._plays[pid]) if isinstance(pid, str) and pid in self._plays else None
            card = dict(self._last_show) if self._last_show else None
            ap = self._active_play
            failed_box = (state == "error" and play is not None and play.get("source") == "youtube"
                          and ap is not None and ap[0].get("playId") == pid)
            self._cond.notify_all()
        log.debug("deck status: %s", st)
        if failed_box and self.window is not None:
            # The YouTube song on screen failed: the box goes at once, whatever the provider does next
            # (review RCOMP-1; a later "play" shows it again and cancels this).
            try:
                self.window.hide_later(0.0)
            except Exception as exc:  # noqa: BLE001 - a window problem must not stop the report
                log.debug("player window: %s", exc)
        hook = self.on_player_status
        if hook is not None:
            try:
                hook(dict(st), play, card)
            except Exception:  # noqa: BLE001 - a listener bug must not break the player's report
                log.exception("deck: the player status listener failed")

    def _note_csp(self, data: dict[str, Any]) -> None:
        """The player page hit its Content-Security-Policy: logged once per directive and address."""
        directive = str(data.get("directive") or "?")[:80]
        blocked = str(data.get("blocked") or "?")[:200]
        key = (directive, blocked.split("?", 1)[0])
        with self._lock:
            if key in self._csp_seen or len(self._csp_seen) >= 50:
                return
            self._csp_seen.add(key)
        log.warning("the player page's Content-Security-Policy blocked %s (%s)", blocked, directive)

    @property
    def csp_violations(self) -> list[tuple[str, str]]:
        """(directive, address) of every CSP violation the player page reported (diagnostics, tests)."""
        with self._lock:
            return sorted(self._csp_seen)

    @property
    def page_browser(self) -> Optional[str]:
        """The browser of the connected player page ("chrome" / "edge" / "other"), or the one the deck
        launched it in; None when no player page is connected."""
        if not self.page_open:
            return None
        with self._lock:
            return self._page_browser or self._player_kind

    def player_info(self) -> dict[str, Any]:
        """The player window for status pages and reports: its browser, how often it was opened, whether
        its creation took the foreground (and got it back) -- no token, no device id."""
        win = self.window
        return {"browser": self.page_browser, "pageOpen": self.page_open, "opened": self.player_opened,
                "tookForeground": getattr(win, "took_foreground", None) if win is not None else None,
                "handedBack": getattr(win, "handed_back", None) if win is not None else None,
                "shown": bool(getattr(win, "shown", False)) if win is not None else None}

    def chrome_available(self) -> bool:
        """Google Chrome is installed (in-app Spotify needs it, D11); looked up at most once a minute."""
        now = time.monotonic()
        cached = self._chrome_cache
        if cached is not None and now - cached[0] < 60.0:
            return cached[1]
        ok = find_browser("chrome") is not None
        self._chrome_cache = (now, ok)
        return ok

    def player_browser(self) -> str:
        """What plays YouTube songs (and in-app Spotify): "chrome", "edge" or "none" -- the connected
        player page's browser, else the one :meth:`open_player` would start (D17 ``music.youtube.player``)."""
        kind = self.page_browser
        if kind in ("chrome", "edge"):
            return kind
        if (self.browser or "").strip().lower() in ("none", "off", "false", "no"):
            return "none"
        for k in self._browser_order():
            if find_browser(k):
                return k
        return "none"

    @property
    def last_status(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return dict(self._last_status) if self._last_status else None

    @property
    def status_seq(self) -> int:
        with self._lock:
            return self._status_seq

    def status_for(self, play_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            st = self._status_by_play.get(play_id)
            return dict(st) if st else None

    def wait_status(self, *, play_id: Optional[str] = None, states: Optional[Iterable[str]] = None,
                    timeout: float = 5.0, after_seq: Optional[int] = None) -> Optional[dict[str, Any]]:
        """Wait for a status report (for ``play_id`` if given) whose state is in ``states``."""
        want = set(states) if states is not None else None
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                st = self._status_by_play.get(play_id) if play_id else self._last_status
                if st and (want is None or st.get("state") in want) and (
                        after_seq is None or st.get("seq", 0) > after_seq):
                    return dict(st)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._interrupt.is_set():
                    return None
                self._cond.wait(min(remaining, 0.25))

    # ------------------------------------------------------------- media ----
    def register_media(self, path: str | os.PathLike[str]) -> str:
        """Whitelist a local file and return the URL the page can load it from: a same-origin path
        (``/media/<token>``), so the player page loads it from whichever origin it runs on (D12: it
        runs on ``localhost``, and its CSP allows media from its own origin only)."""
        p = Path(path).resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        key = os.path.normcase(str(p))
        with self._lock:
            token = self._media_by_path.get(key)
            if token is None:
                token = secrets.token_urlsafe(16)
                self._media_by_path[key] = token
                self._media[token] = p
        return f"/media/{token}"

    def _media_path(self, token: str) -> Optional[Path]:
        with self._lock:
            return self._media.get(token)

    # ------------------------------------------------------------ windows ----
    def configure_player(self, *, auto_open: Optional[str] = None, browser: Optional[str] = None,
                         position: Optional[tuple[int, int]] = None, profile_dir: Any = None,
                         clear_position: bool = False, corner: Optional[str] = None,
                         browser_log: Any = None) -> None:
        """Update the player-window policy (from ``deck.*`` config)."""
        if auto_open is not None:
            self.auto_open = auto_open if auto_open in AUTO_OPEN_MODES else "youtube"
        if browser is not None:
            if browser != self.browser:
                self._bad_browsers.clear()
            self.browser = browser
        if position is not None or clear_position:
            self.player_position = position
        if profile_dir is not None:
            self.profile_dir = Path(profile_dir)
        if corner is not None:
            from .window import normalize_corner
            self.player_corner = normalize_corner(corner)
        if browser_log is not None:
            self.browser_log = Path(browser_log)
        win = self.window
        if win is not None:
            moved = (win.corner, win.position) != (self.player_corner, self.player_position)
            win.corner = self.player_corner
            win.position = self.player_position
            reposition = getattr(win, "reposition", None)
            if moved and callable(reposition):
                try:
                    reposition()  # an open box moves to its new corner at once (DESIGN-v1.2.1 3.9)
                except Exception as exc:  # noqa: BLE001 - placement only
                    log.debug("player window: %s", exc)

    @property
    def player_opening(self) -> bool:
        """A player window was launched within the last few seconds and has not connected yet."""
        with self._lock:
            t = self._player_opened_at
        return (t is not None and time.monotonic() - t <= PLAYER_CONNECT_TIMEOUT + 2.0
                and not self.page_open)

    def _browser_order(self) -> list[str]:
        """Chrome first when ``deck.browser`` says so, and always while Spotify plays in the app (a
        provider in app mode, signed in or not yet): the Web Playback SDK needs Chrome's Widevine (it
        never became ready in Edge; D11), and the one player window serves both. Edge stays the
        fallback, for YouTube songs."""
        b = (self.browser or "edge").strip().lower()
        first = "chrome" if (b == "chrome" or self.spotify.app_mode or self.spotify.wanted) else "edge"
        order = [first, "chrome" if first == "edge" else "edge"]
        return [k for k in order if k not in self._bad_browsers]

    def _copy_widevine(self) -> None:
        """Chrome's Widevine component into the player profile, when it is missing there (D11)."""
        if self.profile_dir is None:
            return
        copier = self.widevine_copier or ensure_widevine
        try:
            copier(self.profile_dir)
        except Exception as exc:  # noqa: BLE001 - in-app Spotify then waits for Chrome's own download
            log.info("Widevine copy failed: %s", exc)

    def open_player(self, *, size: tuple[int, int] = PLAYER_WINDOW_SIZE) -> str:
        """Open the player page in a small Edge/Chrome app window.

        ``--app=<player url> --window-size=420,380``, a dedicated profile under :attr:`profile_dir`
        (where autoplay with sound is allowed) and the no-throttling flags (a hidden player keeps
        working). With the window manager (:attr:`window`, Windows) the window is found, kept out
        of the taskbar, made never-activating and parked off every monitor until a YouTube song plays
        (shown, not hidden: the Web Playback SDK plays nothing in a hidden window, D14); it shows at
        its corner (or :attr:`player_position`).

        Never while the game is the foreground window: Chromium activates a window when it creates
        it, and nothing may take the focus from the game (real-client #8 / #13). And never the
        default browser (a tab took the owner out of the game): no Edge/Chrome, no player.
        Returns ``"edge"``, ``"chrome"`` or ``"none"``.
        """
        b = (self.browser or "edge").strip().lower()
        if b in ("none", "off", "false", "no") or self._stopping.is_set():
            return "none"
        if b == "default":  # a tab in the default browser took the owner out of the game: Edge / Chrome
            log.info('deck.browser "default": the player opens in an Edge or Chrome app window instead '
                     '(a browser tab would take the focus from the game)')
        try:
            in_front = bool(self.game_in_front())
        except Exception:  # noqa: BLE001
            in_front = False
        if in_front:
            log.info("not opening the player window while the game is in front (creating it would take the "
                     "focus); it opens once you are out of the game")
            return "none"
        for kind in self._browser_order():
            exe = find_browser(kind)
            if not exe:
                continue
            args = [exe, f"--app={self.player_launch_url}", f"--window-size={size[0]},{size[1]}",
                    "--autoplay-policy=no-user-gesture-required", *NO_THROTTLE_FLAGS, "--hide-crash-restore-bubble"]
            pos = self._launch_position(size)
            if pos is not None:
                args.append(f"--window-position={pos[0]},{pos[1]}")
            if self.profile_dir is not None:
                try:
                    self.profile_dir.mkdir(parents=True, exist_ok=True)
                    args += [f"--user-data-dir={self.profile_dir}", "--no-first-run", "--no-default-browser-check"]
                except OSError as exc:
                    log.debug("player profile dir unavailable: %s", exc)
                if kind == "chrome":
                    self._copy_widevine()
            before = self._foreground()  # Chromium activates the window it creates: hand it back after
            existing = self._windows_now()  # none of these is the new player window (never adopt one)
            try:
                proc = self.launcher(args)
            except OSError as exc:
                log.warning("could not start %s: %s", exe, exc)
                self._bad_browsers.add(kind)
                continue
            with self._lock:
                self._player_opened_at = time.monotonic()
                self.player_opened += 1
                self._player_proc = proc
                self._player_kind = kind
            log.info("opened the player window (%s: %s)", kind, exe)
            self._adopt_later(proc, before, existing)
            return kind
        log.warning("no Edge or Chrome to play YouTube songs in (deck.browser %r); YouTube drops show their card "
                    "only", self.browser)
        return "none"

    def _launch_position(self, size: tuple[int, int]) -> Optional[tuple[int, int]]:
        """Only an explicit ``deck.playerPosition``: Chromium reads ``--window-position`` in DIPs, while
        the corner is placed in physical pixels by the window manager once the window exists (a corner
        computed here would land off-screen on a 300 % display, and stay there if the window were
        never found)."""
        return self.player_position

    def _foreground(self) -> Optional[int]:
        """The foreground window before a launch (for the window manager's hand-back), or None."""
        win = self.window
        fn = getattr(win, "foreground", None) if win is not None else None
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001 - best effort
            return None

    def _windows_now(self) -> Optional[set[int]]:
        win = self.window
        fn = getattr(win, "windows_now", None) if win is not None else None
        if not callable(fn):
            return None
        try:
            return set(fn())
        except Exception:  # noqa: BLE001
            return None

    def _adopt_later(self, proc: Any, before: Optional[int] = None, existing: Optional[set[int]] = None) -> None:
        win = self.window
        if win is None:
            return
        pid = getattr(proc, "pid", None)
        kw: dict[str, Any] = {}
        if before is not None:  # else nothing to hand back (or a window manager without the hand-back)
            kw["prev_foreground"] = before
        if existing:
            kw["ignore"] = existing
        target: Callable[[], Any] = lambda: win.adopt(pid, **kw)  # noqa: E731
        threading.Thread(target=target, name="needledropper-player-window", daemon=True).start()

    def _player_exit_code(self) -> Optional[int]:
        proc = self._player_proc
        poll = getattr(proc, "poll", None)
        if not callable(poll):
            return None
        try:
            return poll()
        except Exception:  # noqa: BLE001
            return None

    def ensure_player(self, timeout: float = PLAYER_CONNECT_TIMEOUT, *, force: bool = False,
                      failover: bool = False) -> bool:
        """Make sure a player page is connected, opening the window if the policy allows.

        Returns True once a player is connected. Waits briefly for a player that
        is just reloading, reuses a window that is still starting, and otherwise
        opens one when :attr:`auto_open` is not ``"never"`` (or ``force``). A browser that exits
        at once with an error is logged (its exit code; its stderr is in logs\\browser.log) and
        the other one is tried right away (journey review F1); ``failover`` also tries the other
        one when the page does not connect in time (the Edge "deflector" on the owner's PC starts
        nothing at all: research 3.6).
        """
        if self.page_open:
            return True
        with self._lock:
            gone = self._player_gone_at
        if gone is not None and time.monotonic() - gone <= PLAYER_RECONNECT_GRACE:
            if self.wait_player(min(PLAYER_RECONNECT_GRACE, timeout)):
                return True
        if self.player_opening:
            return self._wait_launched(timeout, failover)
        if not force and self.auto_open == "never":
            return False
        if self.open_player() == "none":
            return False
        return self._wait_launched(timeout, failover)

    def _wait_launched(self, timeout: float, failover: bool) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self.wait_player(min(0.25, max(0.0, deadline - time.monotonic()))):
                return True
            if self._stopping.is_set() or self._interrupt.is_set():
                return False
            code = self._player_exit_code()
            timed_out = time.monotonic() >= deadline
            failed = code is not None and code != 0
            if not (failed or (timed_out and failover)):
                if timed_out:
                    return False
                continue
            kind = self._player_kind
            if failed:
                log.warning("the player browser (%s) exited at once with code %s; see logs\\browser.log", kind, code)
            else:
                log.warning("the player window (%s) did not connect within %.0f s", kind, timeout)
            if kind:
                self._bad_browsers.add(kind)
            with self._lock:
                self._player_opened_at = None
                self._player_proc = None
            if self.open_player() == "none":
                return False
            deadline = time.monotonic() + max(0.0, timeout)
            failover = False  # one more browser at most

    def prepare_player(self) -> bool:
        """Create the hidden player window now (the app's start), so that no window is ever created
        while the player is in the game. Blocks until it connects or gives up; call from a thread."""
        if self.auto_open == "never" or self.page_open or self.player_opening:
            return self.page_open
        return self.ensure_player(PLAYER_STARTUP_TIMEOUT, failover=True)

    def interrupt_waits(self) -> None:
        """The app is quitting: :meth:`wait_player` / :meth:`wait_status` return at once, so a song that
        is being started does not hold up the shutdown (SHELL-FASTSTOP-BUSY-ENGINE)."""
        self._interrupt.set()
        with self._cond:
            self._cond.notify_all()

    def close_player(self) -> bool:
        """Close the player window (the app is quitting). True when the window manager had one to close."""
        win = self.window
        if win is None:
            return False
        had = bool(getattr(win, "hwnd", None))
        try:
            win.close()
        except Exception as exc:  # noqa: BLE001 - the process is ended below anyway
            log.debug("closing the player window failed: %s", exc)
        return had

    def open_panel(self) -> bool:
        """Open the Connect page in the default browser."""
        try:
            return self.url_opener(self.panel_url) is not False
        except Exception as exc:
            log.warning("could not open a browser: %s -- open %s yourself", exc, self.panel_url)
            return False


_IFEO = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"


def edge_redirected() -> bool:
    """True when Windows runs another program instead of msedge.exe: an Image File Execution Options
    ``Debugger`` for it (on the key itself, or on one of its filter subkeys), as "Edge deflector" tools
    set up. On the owner's PC this made ``msedge --app=...`` start nothing at all (research 3.6)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _IFEO + r"\msedge.exe")
    except OSError:
        return False
    try:
        try:
            winreg.QueryValueEx(key, "Debugger")
            return True
        except OSError:
            pass
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                return False
            i += 1
            try:
                with winreg.OpenKey(key, name) as sub:
                    winreg.QueryValueEx(sub, "Debugger")
                    return True
            except OSError:
                continue
    finally:
        winreg.CloseKey(key)


def _game_in_front() -> bool:
    try:
        from ..transport import wincapture
        return bool(wincapture.game_in_foreground())
    except Exception:  # noqa: BLE001 - not Windows: never block on this
        return False


def find_browser(kind: str = "edge") -> Optional[str]:
    """Locate msedge.exe / chrome.exe in the usual install locations (Edge: not when Windows
    redirects it to another program, see :func:`edge_redirected`)."""
    if kind != "chrome" and edge_redirected():
        log.info("msedge.exe is redirected to another program on this PC (Image File Execution Options); "
                 "using Chrome for the player window")
        return None
    if kind == "chrome":
        rel = ("Google", "Chrome", "Application", "chrome.exe")
        names = ("chrome", "google-chrome", "chromium")
    else:
        rel = ("Microsoft", "Edge", "Application", "msedge.exe")
        names = ("msedge", "microsoft-edge")
    for env in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cand = Path(base, *rel)
            if cand.is_file():
                return str(cand)
    for n in names:
        found = shutil.which(n)
        if found:
            return found
    return None
