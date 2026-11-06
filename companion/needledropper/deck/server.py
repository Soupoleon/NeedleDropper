"""The deck: the companion's local web server (docs/SPEC-v1.1.md C.1, C.3, C.4; DESIGN-v1.2 1.4).

``Deck`` runs a ``ThreadingHTTPServer`` on ``127.0.0.1:<deck.port>`` serving:

* ``GET /`` -- the one-time **Connect page** (World of Warcraft, Spotify,
  YouTube, Check the link; ``/connect``, ``/setup`` and the v1.1 ``/panel`` are
  aliases). It connects to ``/events`` as a ``panel`` client;
* ``GET /player`` -- the small **player** page (title card, YouTube IFrame
  Player, ``<audio>`` for companion-side local files);
* ``GET /events?client=panel|player`` -- Server-Sent Events. Playback commands
  (``play`` / ``stop`` / ``fade``) go to player clients only; ``show`` / ``hide``
  (the title card) go to both; ``state`` nudges ask the Connect page to refresh;
* ``POST /status`` -- the player reports ``{"state": "ready"|"playing"|"ended"|
  "error"|"autoplay-blocked", "detail": ..., "playId": ..., "code": ...}``;
  :attr:`Deck.on_player_status` hears every report (e.g. YouTube's "embedding
  not allowed" codes);
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

import hmac
import html
import json
import logging
import mimetypes
import os
import queue
import secrets
import shutil
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
BROWSER_LOG_MAX = 1_000_000  # logs\browser.log (the browser's stderr): one older copy above this
#: Chromium flags for a page that must keep working while its window is hidden (warm YouTube player,
#: cue at ARM): without them a hidden page's timers and media are throttled (research 3.5; the
#: occlusion feature too, real-client #15).
NO_THROTTLE_FLAGS = ("--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                     "--disable-renderer-backgrounding", "--disable-features=CalculateNativeWinOcclusion")

CLIENT_PANEL = "panel"
CLIENT_PLAYER = "player"
CLIENT_KINDS = (CLIENT_PANEL, CLIENT_PLAYER)
PLAYER_ONLY_CMDS = frozenset({"play", "stop", "fade", "cue"})
PANEL_ONLY_CMDS = frozenset({"state"})

AUTO_OPEN_MODES = ("youtube", "always", "never")
#: Frequent requests that are not written to the debug log.
_QUIET_PATHS = frozenset({"/api/state", "/healthz", "/events", "/status", "/favicon.ico"})

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

_CSP_COMMON = ("default-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' https: data:; "
               "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_CSP = {
    # The Connect page loads nothing from anywhere else (its links only navigate).
    "connect": ("default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    # The YouTube IFrame API loads its widget script from www.youtube.com and embeds an iframe.
    "player": _CSP_COMMON + "; script-src 'self' https://www.youtube.com https://s.ytimg.com; "
                            "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
                            "media-src 'self' blob:",
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
            self._page(*PAGES[path])
        elif path == "/events" and not head:
            if not self._origin_ok():
                self._error(HTTPStatus.FORBIDDEN, "forbidden origin")
                return
            kind = (parse_qs(parts.query).get("client") or [CLIENT_PLAYER])[0]
            if kind not in CLIENT_KINDS:
                self._error(HTTPStatus.BAD_REQUEST, "client must be 'panel' or 'player'")
                return
            self._events(kind)
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
        self.api: Optional[ApiHandler] = api
        self.launcher: Launcher = launcher or self._launch
        self.url_opener: Callable[[str], Any] = url_opener or webbrowser.open
        # player-window policy
        self.auto_open = "youtube"
        self.browser = "edge"
        self.player_position: Optional[tuple[int, int]] = None
        self.player_corner = "topright"
        self.profile_dir: Optional[Path] = None
        self.browser_log: Optional[Path] = None   # the browser's stderr (logsrowser.log)
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
        srv = _Server((self.host, self.requested_port), _Handler)
        srv.deck = self
        self._server = srv
        self._thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.25},
                                        name="needledropper-deck", daemon=True)
        self._thread.start()
        log.info("deck listening on %s", self.url)
        return self

    def stop(self) -> None:
        srv = self._server
        if srv is None:
            return
        self.close_player()
        self._stopping.set()
        with self._lock:
            for c in self._clients:
                c.q.put(None)
        srv.shutdown()
        srv.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

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
    def player_url(self) -> str:
        return self.base_url + "/player"

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
            self._cond.notify_all()
        log.debug("deck status: %s", st)
        hook = self.on_player_status
        if hook is not None:
            try:
                hook(dict(st), play, card)
            except Exception:  # noqa: BLE001 - a listener bug must not break the player's report
                log.exception("deck: the player status listener failed")

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
        """Whitelist a local file and return the URL the page can load it from."""
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
        return f"{self.base_url}/media/{token}"

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
            win.corner = self.player_corner
            win.position = self.player_position

    @property
    def player_opening(self) -> bool:
        """A player window was launched within the last few seconds and has not connected yet."""
        with self._lock:
            t = self._player_opened_at
        return (t is not None and time.monotonic() - t <= PLAYER_CONNECT_TIMEOUT + 2.0
                and not self.page_open)

    def _browser_order(self) -> list[str]:
        b = (self.browser or "edge").strip().lower()
        first = "chrome" if b == "chrome" else "edge"
        order = [first, "chrome" if first == "edge" else "edge"]
        return [k for k in order if k not in self._bad_browsers]

    def open_player(self, *, size: tuple[int, int] = PLAYER_WINDOW_SIZE) -> str:
        """Open the player page in a small Edge/Chrome app window.

        ``--app=<player url> --window-size=420,380``, a dedicated profile under :attr:`profile_dir`
        (where autoplay with sound is allowed) and the no-throttling flags (a hidden player keeps
        working). With the window manager (:attr:`window`, Windows) the window is found, kept out
        of the taskbar, made never-activating and hidden until a YouTube song plays; it starts at
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
            args = [exe, f"--app={self.player_url}", f"--window-size={size[0]},{size[1]}",
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
            self._adopt_later(proc)
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

    def _adopt_later(self, proc: Any) -> None:
        win = self.window
        if win is None:
            return
        pid = getattr(proc, "pid", None)
        threading.Thread(target=lambda: win.adopt(pid), name="needledropper-player-window", daemon=True).start()

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

    def close_player(self) -> None:
        """Close the player window (the app is quitting)."""
        win = self.window
        if win is not None:
            win.close()

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
