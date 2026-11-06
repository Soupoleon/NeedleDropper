"""Spotify login for NeedleDropper: Authorization Code with PKCE (no client secret).

Every user registers their *own* Spotify app (Development Mode) at
https://developer.spotify.com/dashboard and pastes its Client ID into the
companion config (``spotify.clientId``). The redirect URI must be the loopback
IP literal ``http://127.0.0.1:<redirectPort>/callback`` (Spotify does not accept
``localhost``); the default port is 8321.

Two ways to log in:

* :func:`login_interactive` -- blocking, for ``needledropper spotify-login``;
* :func:`start_login` -- non-blocking, for the control panel: returns a
  :class:`LoginSession` at once (``authorize_url`` to open in a browser tab,
  ``state`` to poll); a daemon thread runs the one-shot callback listener,
  exchanges the code and stores the tokens. A running
  :class:`~needledropper.providers.spotify.SpotifyProvider` picks the new
  tokens up by itself (:class:`TokenStore` re-reads the file when it changes).

All HTTP goes through an injectable ``http`` callable::

    http(method: str, url: str, headers: dict[str, str], body: bytes | None)
        -> (status: int, headers: dict[str, str] (lower-case keys), body: bytes)

which defaults to :func:`urllib_http`. Transport failures raise
:class:`NetworkError`; HTTP error statuses are *returned*, not raised.
"""
from __future__ import annotations

import base64
import hashlib
import html
import http.client
import json
import logging
import os
import random
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

from .. import secrets as nd_secrets

log = logging.getLogger("needledropper.spotify")

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
DASHBOARD_URL = "https://developer.spotify.com/dashboard"
DEFAULT_PORT = 8321
CALLBACK_PATH = "/callback"
#: ``user-read-private`` is needed by ``market=from_token`` on ``/search`` and ``/tracks`` (without it
#: Spotify answers 403 "Insufficient client scope", seen on a real PC on 2026-11-05) and makes ``/me``
#: return ``product`` and ``country`` (the Connect page's Premium check).
SCOPE_PRIVATE = "user-read-private"
#: In-app Spotify (D9): the Web Playback SDK needs ``streaming``, ``user-read-email`` and
#: ``user-read-private``.
SCOPE_STREAMING = "streaming"
SCOPE_EMAIL = "user-read-email"
SCOPES: tuple[str, ...] = (SCOPE_STREAMING, "user-modify-playback-state", "user-read-playback-state", SCOPE_EMAIL,
                           SCOPE_PRIVATE)
#: A stored sign-in without these must sign in again before Spotify plays in the app (D9).
RELOGIN_SCOPES: tuple[str, ...] = (SCOPE_STREAMING, SCOPE_EMAIL)
TOKEN_FILE = "spotify_token.bin"
#: A sign-in that could not be saved is retried at most this often (see :class:`TokenStore`).
SAVE_RETRY_S = 30.0
LOGIN_TIMEOUT_S = 180.0
PANEL_LOGIN_TIMEOUT_S = 300.0  # the panel flow may include logging in to Spotify in the browser first
HTTP_TIMEOUT_S = 10.0
EXPIRY_MARGIN_S = 60.0  # refresh this long before the access token expires

HttpFunc = Callable[[str, str, dict, Optional[bytes]], "tuple[int, dict, bytes]"]
PrintFunc = Callable[..., None]

_PKCE_ALPHABET = string.ascii_letters + string.digits + "-._~"  # RFC 7636 "unreserved"
_rng = random.SystemRandom()


class NetworkError(OSError):
    """The HTTP request could not be completed (DNS, connection, TLS, timeout)."""


class SpotifyAuthError(Exception):
    """Login or token refresh failed. ``str(exc)`` is a user-facing message."""

    def __init__(self, message: str, *, status: Optional[int] = None, error: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.error = error

    @property
    def revoked(self) -> bool:
        """The stored login is no longer valid; the user must log in again."""
        return self.error in ("invalid_grant", "invalid_client", "not_logged_in", "client_mismatch")


# ------------------------------------------------------------ HTTP layer ----

def _lower(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {str(k).lower(): str(v) for k, v in items}


def urllib_http(method: str, url: str, headers: Optional[dict] = None, body: Optional[bytes] = None,
                timeout: float = HTTP_TIMEOUT_S) -> tuple[int, dict[str, str], bytes]:
    """Default ``http`` implementation (urllib). Returns (status, headers, body)."""
    data = body
    if data is None and method.upper() in ("POST", "PUT"):
        data = b""  # Spotify answers 411 Length Required to a PUT/POST without Content-Length
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _lower(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read() or b""
        except Exception:  # noqa: BLE001 - body is best effort
            payload = b""
        return exc.code, _lower(exc.headers), payload
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise NetworkError(f"{method.upper()} {urllib.parse.urlsplit(url).netloc}: {reason}") from exc


def _json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


# ------------------------------------------------------------------ PKCE ----

def generate_code_verifier(length: int = 64) -> str:
    """Random PKCE code verifier (RFC 7636: 43..128 chars from the unreserved set)."""
    if not 43 <= length <= 128:
        raise ValueError("PKCE code verifier length must be 43..128")
    return "".join(_rng.choice(_PKCE_ALPHABET) for _ in range(length))


def code_challenge_s256(verifier: str) -> str:
    """``BASE64URL(SHA256(ascii(verifier)))`` without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """Opaque anti-CSRF value for the authorize request."""
    return base64.urlsafe_b64encode(os.urandom(18)).rstrip(b"=").decode("ascii")


def redirect_uri_for(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}{CALLBACK_PATH}"


def _scope_str(scopes: Union[str, Iterable[str]]) -> str:
    if isinstance(scopes, str):
        return " ".join(scopes.split())
    return " ".join(s for s in scopes if s)


def build_authorize_url(client_id: str, redirect_uri: str, scopes: Union[str, Iterable[str]],
                        state: str, challenge: str) -> str:
    """The accounts.spotify.com/authorize URL for the PKCE flow."""
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": _scope_str(scopes),
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


# ------------------------------------------------------- callback server ----

@dataclass
class CallbackResult:
    """What Spotify sent to the redirect URI."""

    code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None
    error_description: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.code) and not self.error


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>NeedleDropper - Spotify</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0c10;color:#e8e6e3;font-family:Segoe UI,system-ui,sans-serif}}
main{{max-width:34rem;padding:2.5rem;text-align:center}}
h1{{font-weight:300;letter-spacing:.35em;text-transform:uppercase;font-size:1.3rem;color:{accent}}}
p{{line-height:1.6;color:#b9b6b0}} code{{color:#e8e6e3}}
</style></head><body><main><h1>{heading}</h1>{body}</main></body></html>
"""


def _page(heading: str, body_html: str, ok: bool) -> bytes:
    return _PAGE.format(heading=html.escape(heading), body=body_html,
                        accent="#1ed760" if ok else "#ff6b6b").encode("utf-8")


#: Where the user goes back to after the browser step: (back-to text, retry instruction).
_CONTEXT_TEXT = {
    "terminal": ("You can close this tab and return to the terminal.",
                 "Return to the terminal and run <code>needledropper spotify-login</code> to try again."),
    "panel": ("You can close this tab and go back to the NeedleDropper control panel.",
              "Go back to the NeedleDropper control panel and click <b>Connect Spotify</b> to try again."),
}


class _CallbackHTTPServer(HTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind the same port silently.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], expected_state: Optional[str], path: str,
                 context: str = "terminal") -> None:
        self.expected_state = expected_state
        self.callback_path = path
        self.context = context if context in _CONTEXT_TEXT else "terminal"
        self.result: Optional[CallbackResult] = None
        self.got_result = threading.Event()
        super().__init__(addr, _CallbackHandler)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackHTTPServer
    server_version = "NeedleDropper"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.debug("callback server: " + format, *args)

    def _send(self, status: int, page: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(page)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != self.server.callback_path:
            self._send(404, _page("Not found", "<p>This address is only used for the Spotify login.</p>", False))
            return
        q = urllib.parse.parse_qs(parts.query)

        def first(name: str) -> Optional[str]:
            vals = q.get(name)
            return vals[0] if vals else None

        res = CallbackResult(code=first("code"), state=first("state"), error=first("error"),
                             error_description=first("error_description"))
        back, retry = _CONTEXT_TEXT[self.server.context]
        expected = self.server.expected_state
        if expected is not None and res.state != expected:
            self._send(400, _page("Stale login link",
                                  "<p>This login response does not belong to the login that is currently "
                                  f"running.</p><p>{retry}</p>", False))
            return
        if not res.code and not res.error:
            self._send(400, _page("Nothing to do", "<p>No authorization code was received.</p>", False))
            return
        if res.ok:
            self._send(200, _page("Connected",
                                  "<p>NeedleDropper is now connected to your Spotify account.</p>"
                                  f"<p>{back}</p>", True))
        else:
            detail = html.escape(res.error_description or res.error or "unknown error")
            hint = ("You declined access, so NeedleDropper cannot control playback."
                    if res.error == "access_denied" else "Spotify reported an error.")
            self._send(200, _page("Login not completed",
                                  f"<p>{html.escape(hint)}</p><p><code>{detail}</code></p>"
                                  f"<p>{retry}</p>", False))
        if self.server.result is None:
            self.server.result = res
            self.server.got_result.set()


class LoopbackCallbackServer:
    """One-shot HTTP server on ``127.0.0.1:<port>`` that captures the OAuth redirect.

    Use ``port=0`` to bind an ephemeral port (tests); read the bound port from
    :attr:`port` / :attr:`redirect_uri`.
    """

    def __init__(self, port: int = DEFAULT_PORT, *, expected_state: Optional[str] = None,
                 host: str = "127.0.0.1", path: str = CALLBACK_PATH, context: str = "terminal") -> None:
        self._requested = (host, int(port))
        self._expected_state = expected_state
        self._path = path
        self._context = context
        self._server: Optional[_CallbackHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        if self._server is None:
            return self._requested[1]
        return int(self._server.server_address[1])

    @property
    def redirect_uri(self) -> str:
        return f"http://{self._requested[0]}:{self.port}{self._path}"

    def start(self) -> "LoopbackCallbackServer":
        if self._server is not None:
            return self
        try:
            self._server = _CallbackHTTPServer(self._requested, self._expected_state, self._path, self._context)
        except OSError as exc:
            port = self._requested[1]
            raise SpotifyAuthError(
                f"Could not listen on 127.0.0.1:{port} for the Spotify login ({exc.strerror or exc}). "
                f"Another program (or another NeedleDropper login) is probably using port {port}. Close it, "
                f"or change spotify.redirectPort in the config AND the redirect URI in your Spotify app to "
                f"match.", error="port_in_use") from exc
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
                                        name="needledropper-spotify-callback", daemon=True)
        self._thread.start()
        return self

    def wait(self, timeout: float = LOGIN_TIMEOUT_S, *,
             cancelled: Optional[threading.Event] = None) -> Optional[CallbackResult]:
        """Block until a matching redirect arrives, ``timeout`` seconds pass or
        ``cancelled`` is set (Ctrl+C friendly)."""
        srv = self._server
        if srv is None:
            raise RuntimeError("LoopbackCallbackServer.wait() called before start()")
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (cancelled is not None and cancelled.is_set()):
                return srv.result
            if srv.got_result.wait(min(0.2 if cancelled is not None else 0.5, remaining)):
                return srv.result

    def close(self) -> None:
        srv, self._server = self._server, None
        if srv is None:
            return
        try:
            srv.shutdown()
        finally:
            srv.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def __enter__(self) -> "LoopbackCallbackServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------- token requests ----

def _token_request(form: dict[str, str], http: Optional[HttpFunc], what: str,
                   now: Callable[[], float] = time.time) -> dict[str, Any]:
    call = http or urllib_http
    body = urllib.parse.urlencode(form).encode("ascii")
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    try:
        status, _headers, raw = call("POST", TOKEN_URL, headers, body)
    except Exception as exc:  # noqa: BLE001 - any transport failure
        raise SpotifyAuthError(f"Could not reach Spotify to {what}: {exc}", error="network") from exc
    data = _json(raw)
    if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
        err = data.get("error") if isinstance(data, dict) else None
        desc = data.get("error_description") if isinstance(data, dict) else None
        if not isinstance(err, str):
            err = None
        msg = f"Spotify refused to {what} (HTTP {status}"
        msg += f", {err}: {desc})" if err else ")"
        if err == "invalid_grant":
            msg += ". Your Spotify login has expired or was revoked; run 'needledropper spotify-login'."
        elif err == "invalid_client":
            msg += ". Check spotify.clientId in the config (it must be the Client ID of YOUR Spotify app)."
        raise SpotifyAuthError(msg, status=status, error=err)
    expires_in = data.get("expires_in")
    try:
        expires_in = float(expires_in)
    except (TypeError, ValueError):
        expires_in = 3600.0
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": now() + expires_in,
        "scope": data.get("scope", ""),
        "token_type": data.get("token_type", "Bearer"),
    }


def exchange_code(client_id: str, code: str, redirect_uri: str, code_verifier: str, *,
                  http: Optional[HttpFunc] = None, now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the token dict (incl. ``client_id``)."""
    tokens = _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }, http, "complete the login", now)
    if not tokens.get("refresh_token"):
        raise SpotifyAuthError("Spotify did not return a refresh token; please run the login again.",
                               error="no_refresh_token")
    tokens["client_id"] = client_id
    return tokens


def refresh(client_id: str, refresh_token: str, *, http: Optional[HttpFunc] = None,
            now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Refresh the access token. PKCE refresh tokens rotate: keeps the old one if none is returned."""
    tokens = _token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }, http, "refresh the access token", now)
    if not tokens.get("refresh_token"):
        tokens["refresh_token"] = refresh_token
    tokens["client_id"] = client_id
    return tokens


# ------------------------------------------------------------ token store ----

def _app_dir(paths: Any) -> Path:
    """``paths.app_dir`` (config.Paths), or a str/Path, or the NEEDLEDROPPER_HOME / %APPDATA% default."""
    if paths is not None:
        app = getattr(paths, "app_dir", None)
        if app is None and isinstance(paths, (str, os.PathLike)):
            app = paths
        if app is not None:
            return Path(app)
    home = os.environ.get("NEEDLEDROPPER_HOME")
    if home:
        return Path(home)
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "NeedleDropper"


#: Sign-ins this process obtained but could not write to disk, by normalised token-file path:
#: ``{path: (tokens, error text, monotonic time of the last write attempt)}``. Every
#: :class:`TokenStore` of that path (the provider's, the Connect page's login, the account
#: check) sees them, so a fresh sign-in or a rotated refresh token is never thrown away.
_UNSAVED: dict[str, tuple[dict[str, Any], str, float]] = {}
_UNSAVED_LOCK = threading.RLock()


def _store_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


class TokenStore:
    """Spotify tokens in ``<app_dir>\\spotify_token.bin`` (DPAPI-encrypted JSON).

    Stored keys: access_token, refresh_token, expires_at (unix time), scope, client_id.
    Thread-safe; re-reads the file when it changes on disk (e.g. a login in
    another process while ``needledropper run`` is going).

    **Never loses fresh tokens.** When the file cannot be written (a locked or virtualised
    ``%APPDATA%``, see :mod:`needledropper.atomicio`), :meth:`save` keeps the tokens in memory
    for this process (shared by every store of the same path), returns False, logs the
    problem and exposes it as :attr:`save_error`; later :meth:`load` calls retry the write at
    most every :data:`SAVE_RETRY_S` seconds. Spotify rotates refresh tokens, so dropping a
    just-refreshed one would sign the player out.
    """

    KEYS = ("access_token", "refresh_token", "expires_at", "scope", "client_id")

    def __init__(self, app_dir: Any, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = _app_dir(app_dir) / TOKEN_FILE
        self._key = _store_key(self.path)
        self._clock = clock
        self._lock = threading.RLock()
        self._cache: Optional[dict[str, Any]] = None
        self._stamp: Optional[tuple[int, int]] = None

    def _file_stamp(self) -> Optional[tuple[int, int]]:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    @property
    def save_error(self) -> Optional[str]:
        """Why the newest tokens are only in memory (None when they are saved on disk)."""
        with _UNSAVED_LOCK:
            pending = _UNSAVED.get(self._key)
        return pending[1] if pending else None

    @property
    def unsaved(self) -> bool:
        return self.save_error is not None

    def _write(self, clean: dict[str, Any]) -> Optional[str]:
        """Write ``clean`` to disk; None on success, else the error text."""
        try:
            nd_secrets.save_secret(self.path, clean)
        except (OSError, nd_secrets.SecretError) as exc:
            return f"{type(exc).__name__}: {exc}"
        return None

    def _retry_unsaved(self) -> Optional[dict[str, Any]]:
        """The in-memory tokens, if any (trying to write them again when that is due)."""
        with _UNSAVED_LOCK:
            pending = _UNSAVED.get(self._key)
            if pending is None:
                return None
            tokens, _err, tried = pending
            if self._clock() - tried < SAVE_RETRY_S:
                return dict(tokens)
            err = self._write(tokens)
            if err is None:
                del _UNSAVED[self._key]
                log.info("Spotify sign-in saved to %s (it was kept in memory until now)", self.path)
                self._cache, self._stamp = dict(tokens), self._file_stamp()
            else:
                _UNSAVED[self._key] = (tokens, err, self._clock())
            return dict(tokens)

    def load(self) -> Optional[dict[str, Any]]:
        with self._lock:
            pending = self._retry_unsaved()
            if pending is not None:
                return pending
            stamp = self._file_stamp()
            if stamp is None:
                self._cache, self._stamp = None, None
                return None
            if stamp != self._stamp:
                self._cache = nd_secrets.load_secret(self.path)
                self._stamp = stamp
            return dict(self._cache) if self._cache else None

    def save(self, tokens: dict[str, Any]) -> bool:
        """Store ``tokens``; True when they are on disk, False when only in memory (see the class doc)."""
        clean = {k: tokens.get(k) for k in self.KEYS}
        with self._lock:
            self._cache = dict(clean)  # live at once, whatever happens to the write
            with _UNSAVED_LOCK:
                err = self._write(clean)
                if err is None:
                    _UNSAVED.pop(self._key, None)
                else:
                    _UNSAVED[self._key] = (dict(clean), err, self._clock())
            if err is None:
                self._stamp = self._file_stamp()
                return True
        log.warning("Could not save the Spotify sign-in to %s (%s). NeedleDropper keeps it in memory and tries "
                    "again; it is lost only if the app quits before a save works.", self.path, err)
        return False

    def clear(self) -> None:
        with self._lock:
            with _UNSAVED_LOCK:
                _UNSAVED.pop(self._key, None)
            nd_secrets.delete_secret(self.path)
            self._cache, self._stamp = None, None

    def has_refresh_token(self) -> bool:
        tokens = self.load()
        return bool(tokens and tokens.get("refresh_token"))


class TokenManager:
    """Hands out a valid access token, refreshing (thread-safely) when needed."""

    def __init__(self, client_id: str, store: TokenStore, *, http: Optional[HttpFunc] = None,
                 now: Callable[[], float] = time.time) -> None:
        self.client_id = client_id
        self.store = store
        self._http = http
        self._now = now
        self._lock = threading.Lock()

    def _tokens(self) -> dict[str, Any]:
        tokens = self.store.load()
        if not tokens or not tokens.get("refresh_token"):
            raise SpotifyAuthError("Not logged in to Spotify. Run 'needledropper spotify-login'.",
                                   error="not_logged_in")
        stored_cid = tokens.get("client_id")
        if stored_cid and self.client_id and stored_cid != self.client_id:
            raise SpotifyAuthError("The saved Spotify login belongs to a different Client ID than "
                                   "spotify.clientId in the config. Run 'needledropper spotify-login' again.",
                                   error="client_mismatch")
        return tokens

    def usable(self) -> bool:
        try:
            self._tokens()
            return True
        except SpotifyAuthError:
            return False

    def access_token(self) -> str:
        """A token valid for at least ~60 s (refreshes if needed)."""
        with self._lock:
            tokens = self._tokens()
            access = tokens.get("access_token")
            try:
                expires_at = float(tokens.get("expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0.0
            if access and expires_at - EXPIRY_MARGIN_S > self._now():
                return access
            return self._refresh_locked(tokens)

    def force_refresh(self, stale_token: Optional[str] = None) -> str:
        """Refresh after a 401. If another thread already replaced ``stale_token``, reuse its result."""
        with self._lock:
            tokens = self._tokens()
            if stale_token is not None and tokens.get("access_token") and tokens["access_token"] != stale_token:
                return tokens["access_token"]
            return self._refresh_locked(tokens)

    def _refresh_locked(self, tokens: dict[str, Any]) -> str:
        client_id = self.client_id or tokens.get("client_id") or ""
        new = refresh(client_id, tokens["refresh_token"], http=self._http, now=self._now)
        if not new.get("scope"):
            new["scope"] = tokens.get("scope", "")
        try:
            self.store.save(new)  # keeps the rotated tokens in memory when the disk refuses them
        except Exception as exc:  # noqa: BLE001 - a broken store must never cost the fresh token
            log.warning("Could not store the refreshed Spotify token (%s); using it anyway", exc)
        log.debug("Spotify access token refreshed (valid until %s)", time.ctime(new["expires_at"]))
        return new["access_token"]


# ------------------------------------------------------------ interactive ----

def dashboard_instructions(port: int = DEFAULT_PORT) -> str:
    """Step-by-step text for registering the user's own Spotify app."""
    uri = redirect_uri_for(port)
    return "\n".join([
        "Spotify needs a (free) developer app of your own. One-time setup, about two minutes:",
        "",
        f"  1. Open {DASHBOARD_URL} and log in with the Spotify account you listen with.",
        "     (Controlling playback requires Spotify Premium.)",
        "  2. Click 'Create app'. Any name and description will do, e.g. 'NeedleDropper'.",
        "  3. Under 'Which API/SDKs are you planning to use?' select 'Web API' and 'Web Playback SDK'.",
        "  4. Under 'Redirect URIs' add EXACTLY this address and click 'Add':",
        f"         {uri}",
        "     It must be 127.0.0.1 (Spotify rejects 'localhost') and the port must match",
        "     spotify.redirectPort in the NeedleDropper config.",
        "  5. Accept the terms and click 'Save'. Open the app's 'Settings' and copy the 'Client ID'.",
        "     (You do NOT need the client secret.)",
        "  6. Run 'needledropper setup' and paste the Client ID when asked",
        "     (it is stored in config.json as spotify.clientId).",
        "  7. Run 'needledropper spotify-login' to connect your account.",
        "",
        "  Development-mode apps work for up to 5 Spotify accounts. To use another account than",
        "  the app owner's, add its e-mail under the app's 'User Management' page.",
    ])


def is_logged_in(spotify_cfg: dict[str, Any], paths: Any) -> bool:
    """A refresh token for the configured Client ID is stored (no network)."""
    client_id = str((spotify_cfg or {}).get("clientId") or "").strip()
    if not client_id:
        return False
    return TokenManager(client_id, TokenStore(paths)).usable()


def granted_scopes(tokens: Optional[dict[str, Any]]) -> Optional[set[str]]:
    """The scopes a stored sign-in carries, or None when the store does not say."""
    raw = (tokens or {}).get("scope") if isinstance(tokens, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    return set(raw.split())


def missing_scopes(tokens: Optional[dict[str, Any]]) -> list[str]:
    """Scopes this version asks for that the stored sign-in lacks (signed in with an older version)."""
    granted = granted_scopes(tokens)
    if granted is None:
        return []
    return [s for s in SCOPES if s not in granted]


def fetch_profile(access_token: str, http: Optional[HttpFunc] = None) -> dict[str, Any]:
    """``GET /v1/me``: ``{"status", "name", "product", "country", "problem", "message"}``.

    ``status`` is None when Spotify could not be reached. With ``user-read-private``
    Spotify includes ``product`` ("premium", "free", "open") and ``country``.
    ``problem`` classifies a refusal: ``"allowlist"`` (403: the account is not on the
    development app's User Management list), ``"scope"`` (403 "Insufficient client
    scope"), ``"expired"`` (401) or None.
    """
    out: dict[str, Any] = {"status": None, "name": None, "product": None, "country": None, "problem": None,
                           "message": None}
    call = http or urllib_http
    try:
        status, _h, raw = call("GET", API_BASE + "/me",
                               {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}, None)
    except Exception as exc:  # noqa: BLE001 - purely informational
        log.debug("GET /v1/me failed: %s", exc)
        return out
    data = _json(raw)
    out["status"] = status
    if status == 200 and isinstance(data, dict):
        out["name"] = str(data.get("display_name") or data.get("id") or "") or None
        product = data.get("product")
        out["product"] = product.lower() if isinstance(product, str) and product else None
        country = data.get("country")
        out["country"] = country.upper() if isinstance(country, str) and country else None
        return out
    err = data.get("error") if isinstance(data, dict) else None
    message = str(err.get("message") or "") if isinstance(err, dict) else str(err or "")
    out["message"] = message or None
    if status == 401:
        out["problem"] = "expired"
    elif status == 403:
        out["problem"] = "scope" if "insufficient client scope" in message.lower() else "allowlist"
    return out


def fetch_account(access_token: str, http: Optional[HttpFunc] = None) -> tuple[Optional[int], Optional[str]]:
    """``GET /v1/me``: ``(HTTP status, display name)``; ``(None, None)`` if unreachable.

    403 means the account is not on the app's User Management allowlist. See
    :func:`fetch_profile` for the plan (``product``) and country.
    """
    prof = fetch_profile(access_token, http)
    return prof["status"], prof["name"]


def _check_account(access_token: str, http: Optional[HttpFunc], out: PrintFunc) -> None:
    """Best effort: greet the user (with the plan, when Spotify says), or explain the dev-mode allowlist 403."""
    prof = fetch_profile(access_token, http)
    status, who = prof["status"], prof["name"]
    if status == 200:
        plan = prof["product"]
        extra = ""
        if plan == "premium":
            extra = " (Spotify Premium)"
        elif plan:
            extra = f" (Spotify {plan.title()}: NeedleDropper needs Premium to control playback)"
        out(f"Logged in to Spotify as {who or 'your account'}{extra}.")
    elif status == 403:
        out("Warning: Spotify says this account is not allowed to use your app (HTTP 403).\n"
            f"  Open {DASHBOARD_URL}, select your app, go to 'User Management' and add the e-mail\n"
            "  address of the Spotify account you just logged in with. (The app owner needs Premium.)")
    elif status is not None:
        log.debug("GET /v1/me -> HTTP %s", status)


def login_interactive(spotify_cfg: dict[str, Any], paths: Any, *, open_browser: bool = True,
                      http: Optional[HttpFunc] = None, timeout: float = LOGIN_TIMEOUT_S,
                      print_fn: PrintFunc = print,
                      browser: Optional[Callable[[str], Any]] = None) -> None:
    """Run the PKCE login in the user's browser and store the tokens.

    Returns None on success. Raises :class:`SpotifyAuthError` (message is
    user-facing) if the Client ID is missing (after printing the dashboard
    steps), the port is busy, the user declines, the login times out, or the
    token exchange fails.
    """
    cfg = spotify_cfg or {}
    client_id = str(cfg.get("clientId") or "").strip()
    try:
        port = int(cfg.get("redirectPort") if cfg.get("redirectPort") is not None else DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not client_id:
        print_fn(dashboard_instructions(port or DEFAULT_PORT))
        raise SpotifyAuthError("No Spotify Client ID configured (spotify.clientId). "
                               "Follow the steps above, then run 'needledropper setup'.", error="no_client_id")

    verifier = generate_code_verifier()
    challenge = code_challenge_s256(verifier)
    state = generate_state()
    server = LoopbackCallbackServer(port, expected_state=state)
    server.start()
    try:
        redirect_uri = server.redirect_uri
        url = build_authorize_url(client_id, redirect_uri, SCOPES, state, challenge)
        print_fn("Opening your browser to connect NeedleDropper to Spotify...")
        print_fn(f"If it does not open, copy this address into your browser:\n  {url}")
        if open_browser:
            opener = browser or webbrowser.open
            try:
                opener(url)
            except Exception as exc:  # noqa: BLE001 - user can open the URL by hand
                print_fn(f"(Could not open a browser automatically: {exc})")
        print_fn(f"Waiting up to {int(timeout)} s for you to approve access in the browser... (Ctrl+C to cancel)")
        result = server.wait(timeout)
    finally:
        server.close()

    if result is None:
        raise SpotifyAuthError(
            f"Timed out after {int(timeout)} s waiting for the Spotify login.\n"
            f"If Spotify showed 'INVALID_CLIENT: Invalid redirect URI', add exactly {redirect_uri_for(port)}\n"
            "to your app's Redirect URIs in the dashboard (127.0.0.1, not localhost) and try again.",
            error="timeout")
    if result.error:
        if result.error == "access_denied":
            raise SpotifyAuthError("Spotify login cancelled: access was not granted.", error="access_denied")
        raise SpotifyAuthError(f"Spotify login failed: {result.error_description or result.error}",
                               error=result.error)

    tokens = exchange_code(client_id, result.code or "", redirect_uri, verifier, http=http)
    granted = set((tokens.get("scope") or "").split())
    missing = [s for s in SCOPES if granted and s not in granted]
    store = TokenStore(paths)
    if not store.save(tokens):
        # This command ends in a moment, so a sign-in kept in memory would be lost with it.
        raise SpotifyAuthError(f"Spotify login worked, but Windows refused to save it to {store.path} "
                               f"({store.save_error}). Sign in from the Connect page instead "
                               "(NeedleDropper keeps the sign-in while it runs and retries saving it).",
                               error="store_failed")
    protection = "encrypted with Windows DPAPI" if nd_secrets.dpapi_available() else "base64-encoded"
    print_fn(f"Spotify login successful. Tokens saved ({protection}) to {store.path}")
    if missing:
        print_fn(f"Warning: Spotify did not grant the scopes {', '.join(missing)}; playback control may fail.")
    _check_account(tokens["access_token"], http, print_fn)
    print_fn("Note: playback control needs Spotify Premium. NeedleDropper will tell you if Spotify refuses.")


# --------------------------------------------------------- panel (async) ----

LOGIN_WAITING = "waiting"
LOGIN_CONNECTED = "connected"
LOGIN_ERROR = "error"
LOGIN_TIMEOUT = "timeout"


class LoginSession:
    """One non-blocking Spotify login started by :func:`start_login`.

    Attributes (read them from any thread):

    * ``authorize_url`` -- open this in a browser tab (Spotify's consent page);
    * ``redirect_uri`` -- the callback address the listener answers on;
    * ``state`` -- ``"waiting"`` | ``"connected"`` | ``"error"`` | ``"timeout"``;
    * ``message`` -- short user-facing text for the current state;
    * ``account`` -- the Spotify display name once connected (if Spotify told us);
    * ``warning`` -- e.g. the account is not on the app's User Management list.

    :meth:`cancel` stops waiting and frees the callback port. Never holds secrets
    that a caller could leak: the PKCE verifier and the tokens stay private.
    """

    def __init__(self, *, client_id: str, redirect_uri: str, authorize_url: str, timeout: float) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.authorize_url = authorize_url
        self.timeout = float(timeout)
        self.started_at = time.time()
        self.state = LOGIN_WAITING
        self.message = "Waiting for you to approve access on Spotify's page."
        self.account: Optional[str] = None
        self.warning: Optional[str] = None
        self.error: Optional[str] = None  # machine-readable SpotifyAuthError.error
        self.save_error: Optional[str] = None  # the sign-in works but is only in memory (see TokenStore)
        self.profile: Optional[dict[str, Any]] = None  # fetch_profile() once connected (plan, country)
        self._cancelled = threading.Event()
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: Optional[float] = None) -> str:
        """Block until the session finished (or ``timeout``); returns :attr:`state`."""
        self._done.wait(timeout)
        return self.state

    def cancel(self) -> None:
        """Stop waiting for the browser; a no-op once the session finished."""
        self._cancelled.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)

    def _finish(self, state: str, message: str, *, error: Optional[str] = None) -> None:
        with self._lock:
            if self._done.is_set():
                return
            self.state, self.message, self.error = state, message, error
            self._done.set()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe snapshot for the control panel (no secrets)."""
        prof = self.profile or {}
        return {
            "state": self.state, "message": self.message, "account": self.account, "warning": self.warning,
            "saveError": self.save_error, "product": prof.get("product"), "country": prof.get("country"),
            "authorizeUrl": self.authorize_url if self.state == LOGIN_WAITING else None,
            "redirectUri": self.redirect_uri, "startedAt": self.started_at,
            "expiresIn": max(0, int(self.started_at + self.timeout - time.time()))
            if self.state == LOGIN_WAITING else None,
        }


def start_login(spotify_cfg: dict[str, Any], paths: Any, *, http: Optional[HttpFunc] = None,
                timeout: float = PANEL_LOGIN_TIMEOUT_S, port: Optional[int] = None,
                on_change: Optional[Callable[["LoginSession"], Any]] = None,
                now: Callable[[], float] = time.time) -> LoginSession:
    """Start a PKCE login without blocking; returns a :class:`LoginSession` immediately.

    Binds the one-shot callback listener on ``127.0.0.1:<redirectPort>`` (or
    ``port``; 0 = ephemeral, for tests) and serves it from a daemon thread,
    which then exchanges the code, stores the tokens (DPAPI) and checks the
    account against the app's allowlist. ``on_change(session)`` is called from
    that thread when the session ends (connected, error, timeout, cancelled).

    Raises :class:`SpotifyAuthError` at once if no Client ID is configured or
    the callback port is busy.
    """
    cfg = spotify_cfg or {}
    client_id = str(cfg.get("clientId") or "").strip()
    if not client_id:
        raise SpotifyAuthError("Paste your Spotify app's Client ID first.", error="no_client_id")
    try:
        cfg_port = int(cfg.get("redirectPort") if cfg.get("redirectPort") is not None else DEFAULT_PORT)
    except (TypeError, ValueError):
        cfg_port = DEFAULT_PORT
    if port is None:
        port = cfg_port
    verifier = generate_code_verifier()
    challenge = code_challenge_s256(verifier)
    state = generate_state()
    server = LoopbackCallbackServer(port, expected_state=state, context="panel")
    server.start()  # raises SpotifyAuthError(error="port_in_use")
    redirect_uri = server.redirect_uri
    url = build_authorize_url(client_id, redirect_uri, SCOPES, state, challenge)
    session = LoginSession(client_id=client_id, redirect_uri=redirect_uri, authorize_url=url, timeout=timeout)
    configured_uri = redirect_uri_for(cfg_port or port)  # the address registered in the user's Spotify app

    def notify() -> None:
        if on_change is not None:
            try:
                on_change(session)
            except Exception:  # noqa: BLE001 - a broken listener must not kill the login
                log.exception("Spotify login listener failed")

    def run() -> None:
        try:
            result = server.wait(timeout, cancelled=session._cancelled)
        finally:
            server.close()
        try:
            if session._cancelled.is_set() and (result is None or not result.ok):
                session._finish(LOGIN_ERROR, "Login cancelled.", error="cancelled")
                return
            if result is None:
                session._finish(LOGIN_TIMEOUT,
                                f"Timed out after {int(timeout // 60) or 1} min waiting for Spotify. If Spotify "
                                f"said 'Invalid redirect URI', add exactly {configured_uri} to your app's "
                                "Redirect URIs, then try again.", error="timeout")
                return
            if result.error:
                if result.error == "access_denied":
                    session._finish(LOGIN_ERROR, "You declined access on Spotify's page, so NeedleDropper "
                                                 "cannot control playback.", error="access_denied")
                else:
                    session._finish(LOGIN_ERROR, f"Spotify reported an error: "
                                                 f"{result.error_description or result.error}", error=result.error)
                return
            try:
                tokens = exchange_code(client_id, result.code or "", redirect_uri, verifier, http=http, now=now)
            except SpotifyAuthError as exc:
                session._finish(LOGIN_ERROR, str(exc), error=exc.error or "exchange_failed")
                return
            store = TokenStore(paths)
            try:
                saved = store.save(tokens)  # never raises for a refused write: kept in memory instead
            except Exception as exc:  # noqa: BLE001 - an unexpected store bug: still keep the sign-in
                log.exception("Spotify login: could not store the tokens")
                saved = False
                session.save_error = f"{type(exc).__name__}: {exc}"
            if not saved:
                session.save_error = session.save_error or store.save_error
                session.warning = ("Signed in, but Windows refused to save the sign-in "
                                   f"({session.save_error}). NeedleDropper keeps it while it runs and tries "
                                   "to save it again; if you quit first, you will have to sign in again.")
            prof = fetch_profile(tokens["access_token"], http)
            who = prof["name"]
            session.account = who
            session.profile = prof
            if prof["status"] == 403 and prof["problem"] == "allowlist":
                session.warning = ("Spotify says this account is not allowed to use your app. Open the "
                                   "Spotify dashboard, select your app, open User Management and add the "
                                   "e-mail address of the account you just logged in with.")
            text = f"Connected as {who}." if who else "Connected."
            session._finish(LOGIN_CONNECTED, text)
            log.info("Spotify login complete%s", f" ({who})" if who else "")
        except Exception as exc:  # noqa: BLE001 - never leave the session hanging in "waiting"
            log.exception("Spotify login failed")
            session._finish(LOGIN_ERROR, f"Spotify login failed: {exc}", error="internal")
        finally:
            session._finish(LOGIN_ERROR, "Spotify login ended unexpectedly.", error="internal")
            notify()

    t = threading.Thread(target=run, name="needledropper-spotify-login", daemon=True)
    session._thread = t
    t.start()
    return session
