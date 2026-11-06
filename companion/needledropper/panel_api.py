"""The running companion and the Connect page's JSON API (DESIGN-v1.2 1.2, 1.4; CONTRACTS-v1.2 3.6).

The companion holds no soundtrack state: songs arrive inside the addon's beacon frames
and :class:`Companion` only wires the parts that play them -- config, the deck (Connect
page + player server), the provider chain, the engine and the transports -- plus the
small amount of state the tray and the Connect page show:

* **the link**: when World of Warcraft was last heard and when a ``TEST`` frame last
  decoded (``/needle test``; the Check-the-link card's tick, :meth:`Companion.on_link_check`);
* **pause**: "Pause needle drops" stops playback and keeps every frame away from the
  engine (test frames still tick the link check);
* **faults**: the crash-safe thread wrapper (:class:`ThreadGuard` around
  ``threading.excepthook``) records transport / engine threads that die, shows them in
  ``/api/state`` and as one tray notification, and restarts the component once;
* **notifications** through :class:`needledropper.tray.Notifier`, only for the events
  DESIGN-v1.2 1.4 lists (linked to WoW once per session, test signal, Spotify sign-in
  expired, song not found on Spotify, YouTube video can't be embedded) plus thread
  failures and the update check;
* **Start with Windows** (:mod:`.autostart`), set by the Connect page's Done and the tray;
* the **update check**: daily, only when ``updates.releasesUrl`` is set; it notifies and
  never installs anything.

:class:`PanelAPI` maps the ``/api/*`` routes onto it. The deck server has already
enforced the Host / Origin / token / body-size rules before a request reaches it; it
returns ``(http_status, json_payload)`` and never includes secrets (Spotify tokens, the
deck token, the PKCE verifier). Routes: ``GET /api/state``, ``POST /api/config``,
``POST /api/spotify/connect``, ``POST /api/spotify/disconnect``,
``GET /api/spotify/devices``, ``GET /api/link``, ``POST /api/wow/detect``,
``POST /api/addon/install``, ``POST /api/open`` (``{"what": "logs"}``). Anything else
(the v1.1 soundtrack, map, pack and test endpoints included) is a 404.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import __version__, autostart, cli, resources, tray
from .config import (
    DEFAULT_FLAVOR, DEFAULT_WOW_ROOT, ConfigError, Paths, RegistryReader, addon_status, addon_toc, config_path,
    detect_wow_root, load_config, parse_position, paths_from_config, provider_enabled, read_toc_version,
    update_config_file, version_key, wow_install_score,
)
from .models import EventType, Flags

log = logging.getLogger("needledropper.panel")

#: What ``POST /api/open`` may open (the Connect page's "Open log folder").
OPEN_TARGETS = ("logs",)
#: Arguments of the "Start with Windows" command: start quietly in the tray.
AUTOSTART_ARGS = ("--background",)
#: YouTube IFrame player errors that mean "this video can't play in an embedded player".
YOUTUBE_EMBED_ERRORS = {100: "is not available any more", 101: "can't be played outside YouTube",
                        150: "can't be played outside YouTube", 153: "was refused by YouTube's embedded player"}
GAME_RUNNING_CACHE_S = 3.0
WOW_DETECT_CACHE_S = 60.0
#: A frame heard within this many seconds counts as "linked" in the tray's status line.
LINK_FRESH_S = 20.0
#: The same TEST frame from both transports (or the engine's callback) ticks once.
TEST_DEDUPE_S = 30.0
HOUSEKEEPING_S = 5.0
SPOTIFY_WATCH_S = 30.0
PLAYER_RETRY_S = 300.0  # no player window: try to make one again this often (housekeeping)
SPOTIFY_ACCOUNT_RECHECK_S = 3600.0
UPDATE_TIMEOUT_S = 10.0
UPDATE_MAX_BYTES = 512 * 1024

Out = Callable[[str], None]


def default_opener(target: str) -> None:
    """Open a folder or file with the OS (Explorer / the default app)."""
    if sys.platform == "win32" and hasattr(os, "startfile"):
        os.startfile(target)  # type: ignore[attr-defined]
        return
    if not webbrowser.open(Path(target).resolve().as_uri()):
        raise OSError("no application available to open it")


def _now_iso(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() if t is None else t))


def _song(artist: Any, title: Any) -> str:
    a, t = str(artist or "").strip(), str(title or "").strip()
    return f"{a} \u2013 {t}" if a and t else (t or a)


# ================================================================ helpers ====

class PatchError(ValueError):
    """A config patch from the Connect page was rejected. ``fields`` maps dotted keys to messages."""

    def __init__(self, message: str, fields: Optional[dict[str, str]] = None) -> None:
        super().__init__(message)
        self.fields = dict(fields or {})


_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _v_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    raise ValueError("must be true or false")


def _v_text(max_len: int, *, allow_empty: bool = True) -> Callable[[Any], str]:
    def check(v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("must be text")
        s = v.strip()
        if not s and not allow_empty:
            raise ValueError("must not be empty")
        if len(s) > max_len:
            raise ValueError(f"must be at most {max_len} characters")
        if _CTRL_RE.search(s):
            raise ValueError("must not contain control characters")
        return s
    return check


def _v_path(v: Any) -> str:
    s = _v_text(1024, allow_empty=False)(v)
    return s.strip('"').strip()


def _v_client_id(v: Any) -> str:
    s = _v_text(64)(v)
    if s and not _CLIENT_ID_RE.match(s):
        raise ValueError("is not a Spotify Client ID (32 letters and digits from your app's settings)")
    return s


#: The only settings the Connect page may change. Everything else (fades, transports,
#: player window, ports, update check) is an advanced knob edited in config.json.
SCHEMA: dict[str, Any] = {
    "wowRoot": _v_path,
    "setupComplete": _v_bool,
    "startWithWindows": _v_bool,
    "spotify": {"enabled": _v_bool, "clientId": _v_client_id, "deviceName": _v_text(200)},
    "youtube": {"enabled": _v_bool},
}


def _validate_tree(patch: Any, schema: dict[str, Any], prefix: str, errors: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(patch, dict):
        errors[prefix.rstrip(".") or "(body)"] = "must be an object"
        return out
    for key, val in patch.items():
        dotted = prefix + str(key)
        rule = schema.get(key) if isinstance(key, str) else None
        if rule is None:
            errors[dotted] = "is not a setting the Connect page can change"
            continue
        if isinstance(rule, dict):
            sub = _validate_tree(val, rule, dotted + ".", errors)
            if sub:
                out[key] = sub
            continue
        try:
            out[key] = rule(val)
        except ValueError as exc:
            errors[dotted] = str(exc)
    return out


def validate_config_patch(patch: Any, cfg: Optional[dict[str, Any]] = None, *,
                          deck_port: Optional[int] = None) -> dict[str, Any]:
    """Check a Connect page patch against :data:`SCHEMA` and return the cleaned patch.

    Raises :class:`PatchError` naming every bad field (``cfg`` and ``deck_port`` are
    accepted for compatibility; no rule needs them any more).
    """
    errors: dict[str, str] = {}
    clean = _validate_tree(patch, SCHEMA, "", errors)
    if errors:
        first = next(iter(errors.items()))
        raise PatchError(f"{first[0]} {first[1]}", errors)
    return clean


def should_open_panel(cfg: dict[str, Any], paths: Any = None) -> bool:
    """``deck.panelOnStart`` for the no-command start: ``always`` / ``never``, or
    ``first-run`` = until the Connect page's Done was clicked with this version (a v1.1
    config has ``setupComplete`` but no "Start with Windows" choice yet)."""
    mode = str((cfg.get("deck") or {}).get("panelOnStart") or "first-run")
    if mode == "always":
        return True
    if mode == "never":
        return False
    return not cfg.get("setupComplete") or cfg.get("startWithWindows") is None


def _event_flags(event: Any) -> int:
    try:
        return int(getattr(event, "flags", 0) or 0)
    except (TypeError, ValueError):
        return 0


def is_test_event(event: Any) -> bool:
    """A frame (v2 :class:`~needledropper.protocol2.Frame` or v1 Event) with the ``TEST`` flag."""
    return bool(_event_flags(event) & int(Flags.TEST))


def _event_type_name(event: Any) -> str:
    try:
        return EventType(int(getattr(event, "type", 0))).name
    except (TypeError, ValueError):
        return "?"


# ============================================================ faults ====

#: thread-name prefix -> (component key, label shown to the player)
THREAD_COMPONENTS: tuple[tuple[str, str, str], ...] = (
    ("needledropper-engine", "engine", "Playback engine"),
    ("needledropper-prewarm", "engine", "Playback engine"),  # the engine's ARM pre-warm worker
    ("needledropper-pixel", "pixel", "Pixel link"),
    ("needledropper-chatlog", "chatlog", "Chat-log link"),
    ("needledropper-deck", "deck", "Setup page server"),
    ("needledropper-housekeeping", "housekeeping", "Background checks"),
    ("needledropper-update", "update", "Update check"),
    ("needledropper-spotify", "spotify", "Spotify"),
    ("NeedleDropper-tray", "tray", "Tray icon"),
)
#: Components that are restarted (once per session) after their thread died.
RESTARTABLE = ("engine", "pixel", "chatlog")


def component_for_thread(name: str) -> tuple[str, str]:
    """``(key, label)`` for a thread name, e.g. ``needledropper-pixel`` -> ``("pixel", "Pixel link")``."""
    for prefix, key, label in THREAD_COMPONENTS:
        if name.startswith(prefix):
            return key, label
    return name or "thread", f"Background task {name}" if name else "A background task"


@dataclass
class Fault:
    """A component that stopped because of an unhandled exception."""

    key: str
    component: str
    message: str
    thread: str
    at: float  # wall clock
    count: int = 1
    restarted: bool = False

    def to_dict(self, now: float) -> dict[str, Any]:
        return {"key": self.key, "component": self.component, "message": self.message, "thread": self.thread,
                "at": _now_iso(self.at), "agoSec": round(max(0.0, now - self.at), 1), "count": self.count,
                "restarted": self.restarted}


class FaultLog:
    """Faults by component (thread-safe); the newest first in :meth:`to_list`."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._faults: dict[str, Fault] = {}

    def record(self, key: str, component: str, error: Any, *, thread: str = "") -> Fault:
        if isinstance(error, BaseException):
            message = f"{type(error).__name__}: {error}".rstrip(": ").strip()
        else:
            message = str(error)
        with self._lock:
            old = self._faults.get(key)
            fault = Fault(key, component, message[:500], thread, self._clock(),
                          count=(old.count + 1) if old else 1)
            self._faults[key] = fault
            return fault

    def mark_restarted(self, key: str) -> None:
        with self._lock:
            if key in self._faults:
                self._faults[key].restarted = True

    def clear(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._faults.clear()
            else:
                self._faults.pop(key, None)

    def active(self) -> list[Fault]:
        with self._lock:
            return sorted(self._faults.values(), key=lambda f: f.at, reverse=True)

    def to_list(self) -> list[dict[str, Any]]:
        now = self._clock()
        return [f.to_dict(now) for f in self.active()]


class ThreadGuard:
    """The crash-safe thread wrapper: hears every thread that dies of an exception.

    :meth:`install` puts a ``threading.excepthook`` in place (process wide) that calls
    ``on_crash(thread_name, exc, tb)`` and then the previous hook (except Python's
    default one, which would print the traceback a second time: ``on_crash`` logs it).
    :meth:`uninstall` restores the previous hook if this one is still installed.
    :meth:`wrap` guards a thread target this app starts itself.
    """

    def __init__(self, on_crash: Callable[[str, BaseException, Any], None]) -> None:
        self.on_crash = on_crash
        self._previous: Any = None
        self._installed = False

    def _hook(self, args: Any) -> None:
        exc = args.exc_value
        if exc is not None and not isinstance(exc, SystemExit):
            name = getattr(args.thread, "name", "") or ""
            try:
                self.on_crash(name, exc, args.exc_traceback)
            except Exception:  # noqa: BLE001 - never let the reporter itself raise here
                log.exception("thread guard: reporting a crash failed")
        prev = self._previous
        if prev is not None and prev is not getattr(threading, "__excepthook__", None):
            prev(args)

    def install(self) -> "ThreadGuard":
        if not self._installed:
            self._previous = threading.excepthook
            threading.excepthook = self._hook
            self._installed = True
        return self

    def uninstall(self) -> None:
        if self._installed:
            if threading.excepthook == self._hook:
                threading.excepthook = self._previous
            self._installed = False

    def wrap(self, target: Callable[..., Any], name: str) -> Callable[..., Any]:
        """``target`` for a thread named ``name``: an exception is reported instead of escaping."""
        def run(*args: Any, **kwargs: Any) -> None:
            try:
                target(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - reported, the thread ends quietly
                self.on_crash(name, exc, exc.__traceback__)
        return run


# ============================================================ updates ====

_GITHUB_REPO_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/releases(?:/latest)?)?/?$", re.I)


def releases_api_url(url: Any) -> Optional[str]:
    """The JSON endpoint for ``updates.releasesUrl``: a GitHub repository or releases page becomes
    ``https://api.github.com/repos/<owner>/<repo>/releases/latest``; any other ``https://`` URL is
    used as it is (it must return a GitHub-style release object or list). Empty / not https -> None."""
    s = str(url or "").strip()
    if not s:
        return None
    m = _GITHUB_REPO_RE.match(s)
    if m:
        return f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/releases/latest"
    if s.lower().startswith("https://") and not re.search(r"\s", s):
        return s
    return None


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    name: str = ""


def parse_release(data: Any) -> Optional[UpdateInfo]:
    """The newest published release in a GitHub release object (or list of them)."""
    items = data if isinstance(data, list) else [data]
    for rel in items:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name") or rel.get("name") or "").strip()
        if version_key(tag) is None:
            continue
        page = str(rel.get("html_url") or "").strip()
        if not page.lower().startswith("https://"):
            page = ""
        return UpdateInfo(tag.lstrip("vV"), page, str(rel.get("name") or "").strip())
    return None


def fetch_json(url: str, *, timeout: float = UPDATE_TIMEOUT_S, limit: int = UPDATE_MAX_BYTES) -> Any:
    """GET a small JSON document over https (the update check's default fetcher)."""
    if not url.lower().startswith("https://"):
        raise ValueError("the update check only uses https")
    req = urllib.request.Request(url, headers={"User-Agent": f"NeedleDropper/{__version__}",
                                               "Accept": "application/vnd.github+json, application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https enforced above
        raw = resp.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("the release information is too large")
    return json.loads(raw.decode("utf-8"))


class UpdateChecker:
    """The daily update check: off while ``updates.releasesUrl`` is empty; never installs.

    The time of the last check and its result are kept in ``update-check.json`` so a
    restart does not check again within ``interval_hours``.
    """

    def __init__(self, releases_url: Any, current_version: str, *, state_file: Path,
                 fetch: Optional[Callable[[str], Any]] = None, interval_hours: float = 24,
                 now: Callable[[], float] = time.time) -> None:
        self.api_url = releases_api_url(releases_url)
        self.current = current_version
        self.state_file = Path(state_file)
        self.fetch = fetch or fetch_json
        self.interval_s = max(3600.0, float(interval_hours) * 3600.0)
        self._now = now
        self.last_error: Optional[str] = None
        self.latest: Optional[UpdateInfo] = None

    @property
    def enabled(self) -> bool:
        return self.api_url is not None

    def _read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def last_checked(self) -> Optional[float]:
        st = self._read_state()
        t = st.get("lastCheck")
        return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else None

    def due(self) -> bool:
        if not self.enabled:
            return False
        last = self.last_checked()
        return last is None or self._now() - last >= self.interval_s or last > self._now() + 60

    def check(self) -> Optional[UpdateInfo]:
        """Ask the releases URL; the release when it is newer than this app, else None."""
        if not self.enabled:
            return None
        info: Optional[UpdateInfo] = None
        try:
            info = parse_release(self.fetch(self.api_url))  # type: ignore[arg-type]
            self.last_error = None if info is not None else "no published release found"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            self.last_error = str(exc)
            log.info("update check failed: %s", exc)
        state = {"lastCheck": self._now(), "url": self.api_url,
                 "latest": info.version if info else None, "page": info.url if info else None}
        try:
            from .config import atomic_write_text

            atomic_write_text(self.state_file, json.dumps(state) + "\n")
        except OSError as exc:
            log.debug("could not save the update check time: %s", exc)
        if info is None:
            return None
        cur, new = version_key(self.current), version_key(info.version)
        self.latest = info if (cur is not None and new is not None and new > cur) else None
        return self.latest

    def to_dict(self) -> dict[str, Any]:
        last = self.last_checked()
        latest = self.latest
        return {"enabled": self.enabled, "available": latest is not None,
                "version": latest.version if latest else None, "url": latest.url if latest else None,
                "checkedAt": _now_iso(last) if last else None, "error": self.last_error}


# ============================================================ engine ====

def construct_engine(cls: Any, providers: list, deck: Any, kwargs: dict[str, Any]) -> Any:
    """``cls(providers, deck, **kwargs)`` as CONTRACTS-v1.2 3.2 defines the engine.

    Keyword arguments the class does not declare are left out (and logged), so an engine
    without one of them still starts. A pre-cutover engine whose second positional
    parameter is ``registry`` (``Engine(providers, registry, deck, ...)``) is given no map
    registry (the companion has none any more).
    """
    try:
        params = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        params = None
    accepted = dict(kwargs)
    if params is not None and not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        accepted = {k: v for k, v in kwargs.items() if k in params}
        missing = sorted(set(kwargs) - set(accepted))
        if missing:
            log.warning("the playback engine does not take %s; continuing without", ", ".join(missing))
    if params is not None:
        positional = [p.name for p in params.values()
                      if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        if positional[1:2] == ["registry"]:
            return cls(providers, None, deck, **accepted)
    return cls(providers, deck, **accepted)


def _notice_kind(value: Any) -> Optional[str]:
    key = re.sub(r"[^a-z]", "", str(value or "").lower())
    if not key:
        return None
    if key in ("spotifynotfound", "notfound", "songnotfound", "nomatch", "spotifynomatch", "trackmissing"):
        return tray.NOTE_NOT_FOUND
    if key in ("youtubenotembeddable", "notembeddable", "embeddisabled", "youtubeembed", "notembedable"):
        return tray.NOTE_NOT_EMBEDDABLE
    if key in ("spotifyexpired", "spotifyauthexpired", "authexpired", "loginexpired", "spotifyloginexpired",
               "signinexpired", "spotifysigninexpired", "spotifyrevoked", "spotifyauth"):
        return tray.NOTE_SPOTIFY_EXPIRED
    if key in ("spotifystuck", "spotifynotstarting", "stuck"):
        return tray.NOTE_SPOTIFY_STUCK
    return None


def parse_engine_notice(args: Sequence[Any], kwargs: dict[str, Any]) -> tuple[Optional[str], dict[str, Any]]:
    """Normalise what the engine passes to ``on_status`` into ``(notice kind or None, info)``.

    The engine calls ``on_status(status)`` with :meth:`Engine.status` plus a ``change`` entry
    (``{"kind": "notFound" | "notEmbeddable" | "playing" | ..., ...}``); the change's fields are
    merged over the snapshot. Also accepted: ``on_status("spotify-not-found", {...})``,
    ``on_status({"notice": "...", ...})``, ``on_status({"notice": {"kind": "...", ...}})`` (also
    ``event`` / ``kind`` / ``type`` keys), ``on_status(kind="...", **info)``, or a plain status
    snapshot (no notice).
    """
    info: dict[str, Any] = dict(kwargs)
    kind_value: Any = None
    if args and isinstance(args[0], str):
        kind_value = args[0]
        if len(args) > 1 and isinstance(args[1], dict):
            info.update(args[1])
    elif args and isinstance(args[0], dict):
        info.update(args[0])
    for field in ("change", "notice", "event", "kind", "type"):
        if kind_value is not None:
            break
        v = info.get(field)
        if isinstance(v, dict):
            info = {**info, **v}
            v = v.get("kind") or v.get("type") or v.get("event")
        if isinstance(v, str) and _notice_kind(v):
            kind_value = v
    return _notice_kind(kind_value), info


# ============================================================ companion ====

class Companion:
    """The running companion (see the module doc). Everything external is injectable for tests:

    ``provider_factory(cfg, paths, deck) -> providers``; ``transport_factory(cfg, paths, on_event)
    -> transports`` (``None``: no transports; default: the configured pixel + chat-log
    transports); ``engine_factory(providers, deck, **kwargs) -> engine`` (default: the real
    :class:`~needledropper.engine.Engine`); ``opener`` (opens folders); ``game_running(paths)``;
    ``spotify_http`` (scripted Spotify HTTP); ``wow_registry`` / ``wow_default`` (WoW
    detection); ``addon_source`` (the bundled addon); ``autostart_winreg`` (a fake winreg);
    ``notifier``; ``update_fetch(url) -> json``; ``guard_threads`` (install the
    process-wide crash wrapper, default True).
    """

    def __init__(self, cfg: dict[str, Any], paths: Paths, *, deck: Any, config_file: Optional[Path] = None,
                 provider_factory: Optional[Callable[[dict, Paths, Any], list]] = None,
                 transport_factory: Any = "default",
                 engine_factory: Optional[Callable[..., Any]] = None,
                 opener: Optional[Callable[[str], Any]] = None,
                 game_running: Optional[Callable[[Paths], bool]] = None,
                 spotify_http: Any = None, wow_registry: Optional[RegistryReader] = None,
                 wow_default: Optional[str] = None, addon_source: Optional[Path] = None,
                 engine_kwargs: Optional[dict[str, Any]] = None, autostart_winreg: Any = None,
                 notifier: Optional[tray.Notifier] = None, update_fetch: Optional[Callable[[str], Any]] = None,
                 guard_threads: bool = True, housekeeping_s: float = HOUSEKEEPING_S,
                 clock: Callable[[], float] = time.monotonic, out: Optional[Out] = None) -> None:
        self.cfg = cfg
        self.paths = paths
        self.deck = deck
        self.config_file = Path(config_file) if config_file else None
        self._provider_factory = provider_factory
        self._transport_factory = transport_factory
        self._engine_factory = engine_factory
        self.opener = opener or default_opener
        self._game_running = game_running
        self.spotify_http = spotify_http
        self.wow_registry = wow_registry
        self.wow_default = wow_default or DEFAULT_WOW_ROOT
        self._addon_source = Path(addon_source) if addon_source else None
        self._engine_kwargs = dict(engine_kwargs or {})
        self.autostart_winreg = autostart_winreg
        self.notifier = notifier or tray.Notifier()
        self._update_fetch = update_fetch
        self._guard_threads = guard_threads
        self._hk_interval = float(housekeeping_s)
        self._clock = clock
        self.out: Out = out or (lambda msg: log.warning("%s", msg.strip()))
        self.on_pause_changed: Optional[Callable[[bool], Any]] = None

        self._lock = threading.RLock()
        self._rebuild_lock = threading.RLock()  # serialises provider / transport / engine rebuilds
        self.engine: Any = None
        self.providers: list = []
        self.transports: list = []
        self._side_spotify: Any = None
        self._login: Any = None
        self._account: Optional[dict[str, Any]] = None
        self._spotify_user_off = False
        self._spotify_was_connected = False
        self._next_spotify_watch = 0.0
        self._paused = False
        self._link: dict[str, Any] = {"firstAt": None, "lastAt": None, "transport": None, "frames": 0}
        self._check: dict[str, Any] = {"heardAt": None, "heardWall": None, "transport": None, "count": 0,
                                       "what": None, "label": None, "song": None}
        self._test_keys: dict[tuple, float] = {}
        self.faults = FaultLog()
        self.guard = ThreadGuard(self._on_thread_crash)
        self._restart_requests: set[str] = set()
        self._restarted: set[str] = set()
        self._game_cache: Optional[tuple[float, bool, str]] = None
        self._game_was_running = False
        self._player_thread: Optional[threading.Thread] = None
        self._next_player_try = 0.0
        self._data_needs_restart = False  # NeedleDropper_Companion made while the game ran (F6)
        self._wow_det: Optional[tuple[float, Any]] = None
        self._last_install: Optional[dict[str, Any]] = None
        self._game_status_sent: Optional[dict[str, Any]] = None
        self._sv_stamp: Optional[tuple] = None  # SavedVariables files last seen (see _watch_savedvars)
        self.updates: Optional[UpdateChecker] = None
        self._hk_thread: Optional[threading.Thread] = None
        self._hk_stop = threading.Event()
        self._hk_wake = threading.Event()
        self._started = False
        self.api = PanelAPI(self)

    # -------------------------------------------------------- lifecycle --
    @property
    def config_location(self) -> Path:
        return self.config_file or config_path()

    @property
    def addon_source(self) -> Path:
        return self._addon_source or Path(cli.REPO_ADDON_DIR)

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> "Companion":
        """Build everything, start the engine and the transports, attach the API."""
        with self._lock:
            if self._started:
                return self
            if self._guard_threads:
                self.guard.install()
            self._autodetect_wow()
            self._configure_deck()
            self.providers = self._build_providers()
            self.engine = self._make_engine(self.providers)
            self.engine.start()
            self.transports = self._start_transports()
            self.updates = self._make_update_checker()
            self.deck.on_player_status = self._on_player_status
            self.deck.api = self.api
            self._started = True
        self._ensure_companion_data()
        self.recheck_autostart()
        self._hk_stop.clear()
        self._hk_thread = threading.Thread(target=self.guard.wrap(self._housekeeping, "needledropper-housekeeping"),
                                           name="needledropper-housekeeping", daemon=True)
        self._hk_thread.start()
        self._prepare_player()
        return self

    def _watch_player(self, now: float) -> None:
        """No player window (it did not start, or was closed): try again, at most every few minutes,
        and never while the game is in front (deck.open_player refuses then)."""
        if getattr(self.deck, "page_open", True) or now < self._next_player_try:
            return
        self._next_player_try = now + PLAYER_RETRY_S
        self._prepare_player()

    def _youtube_enabled(self) -> bool:
        return any(getattr(p, "name", None) == "youtube" for p in self.providers or [])

    def _prepare_player(self) -> None:
        """Create the hidden YouTube player window now, while the game is (normally) not in front:
        Chromium activates a window when it creates it, so none may be created during play
        (real-client #8 / #13). The housekeeping loop tries again while there is none."""
        prepare = getattr(self.deck, "prepare_player", None)
        if not callable(prepare) or not self._youtube_enabled():
            return
        if getattr(self.deck, "auto_open", "youtube") == "never":
            return
        if getattr(self.deck, "page_open", False) or getattr(self.deck, "player_opening", False):
            return
        t = self._player_thread
        if t is not None and t.is_alive():
            return

        def run() -> None:
            try:
                prepare()
            except Exception as exc:  # noqa: BLE001 - the drop then shows its card only
                log.info("could not prepare the player window: %s", exc)

        self._player_thread = threading.Thread(target=run, name="needledropper-player-start", daemon=True)
        self._player_thread.start()

    def stop(self, *, fast: bool = False) -> None:
        """Stop the background thread, transports, engine and login; close providers. The deck is left to its owner.

        ``fast`` (Windows is logging off / shutting down, or the console window is closing: a few
        seconds at most, CLI-CONSOLE-CLOSE-NO-CLEANUP): the engine goes first -- its stop fades the
        playing drop for at most 1 s, pauses Spotify and restores its volume and the player's own
        music -- and every join is short.
        """
        self._hk_stop.set()
        self._hk_wake.set()
        if fast:
            # A song being started may sit in Spotify's start checks or the wait for the player window
            # for seconds: cut those waits short first, so the engine can stop playback (pause Spotify,
            # give its volume and the player's own music back) inside Windows' few seconds.
            for p in list(self.providers or []):
                interrupt = getattr(p, "interrupt", None)
                if callable(interrupt):
                    try:
                        interrupt()
                    except Exception:  # noqa: BLE001 - shutting down anyway
                        pass
            waits = getattr(self.deck, "interrupt_waits", None)
            if callable(waits):
                try:
                    waits()
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                engine, self.engine = self.engine, None
            if engine is not None:
                try:
                    engine.stop(timeout=3.0)
                except Exception:  # noqa: BLE001 - shutting down anyway
                    log.debug("engine stop failed", exc_info=True)
        hk = self._hk_thread
        if hk is not None and hk is not threading.current_thread():
            hk.join(1.0 if fast else 5.0)
        self._hk_thread = None
        login = self._login
        if login is not None and not login.done:
            login.cancel()
        # Collect under the lock, stop outside it: transport and engine threads call back
        # into this object (submit, on_link_check), and joining them while holding the lock
        # they wait for would stall the shutdown.
        with self._lock:
            if getattr(self.deck, "api", None) is self.api:
                self.deck.api = None
            if getattr(self.deck, "on_player_status", None) == self._on_player_status:
                self.deck.on_player_status = None
            transports, self.transports = self.transports, []
            engine, self.engine = self.engine, None
            providers = list(self.providers) + ([self._side_spotify] if self._side_spotify else [])
            self._side_spotify = None
            self._started = False
        cli._stop_all(transports)
        if engine is not None:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001 - shutting down anyway
                log.debug("engine stop failed", exc_info=True)
        for p in providers:
            try:
                p.close()
            except Exception:  # noqa: BLE001
                pass
        self._flush_game_data()
        self.guard.uninstall()

    # --------------------------------------------------------- building --
    def _build_providers(self) -> list:
        if self._provider_factory is not None:
            return list(self._provider_factory(self.cfg, self.paths, self.deck))
        return cli.build_providers(self.cfg, self.paths, self.deck, out=self.out, spotify_http=self.spotify_http)

    def _engine_kw(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"fade_in_ms": int(self.cfg.get("fadeInMs", 2500)),
                              "fade_out_ms": int(self.cfg.get("fadeOutMs", 3000)),
                              "log": logging.getLogger("needledropper.engine"),
                              "on_link_check": self.on_link_check, "on_status": self.on_engine_status}
        if self.paths.addons_dir.is_dir():  # where the engine records Spotify matches / failures for the game
            kw["addons_dir"] = self.paths.addons_dir
        kw.update(self._engine_kwargs)
        return kw

    def _make_engine(self, providers: list) -> Any:
        kw = self._engine_kw()
        if self._engine_factory is not None:
            return self._engine_factory(list(providers), self.deck, **kw)
        from .engine import Engine

        return construct_engine(Engine, list(providers), self.deck, kw)

    def _replace_engine(self) -> None:
        """A new engine with the current providers (after a crash, or a provider change the engine
        cannot take live). The transports keep feeding :meth:`submit`, so nothing else restarts."""
        with self._rebuild_lock:
            with self._lock:
                old = self.engine
                self.engine = None
            if old is not None:
                try:
                    old.stop()
                except Exception:  # noqa: BLE001 - it may be the one that crashed
                    log.debug("old engine stop failed", exc_info=True)
            new = self._make_engine(self.providers)
            new.start()
            with self._lock:
                self.engine = new

    def _start_transports(self) -> list:
        if self._transport_factory is None:
            return []
        if self._transport_factory == "default":
            built = cli.build_transports(self.cfg, self.paths, self.submit, out=self.out)
        else:
            built = self._transport_factory(self.cfg, self.paths, self.submit)
        return cli._start_transports(list(built), out=self.out)

    def _configure_deck(self) -> None:
        d = self.cfg.get("deck") or {}
        configure = getattr(self.deck, "configure_player", None)
        if configure is not None:
            configure(auto_open=str(d.get("autoOpen") or "youtube"), browser=str(d.get("browser") or "edge"),
                      position=parse_position(d.get("playerPosition")),
                      clear_position=parse_position(d.get("playerPosition")) is None,
                      profile_dir=self.paths.app_dir / "deck-browser",
                      corner=str(d.get("playerCorner") or "topright"),
                      browser_log=self.paths.logs_dir / "browser.log")

    def _make_update_checker(self) -> UpdateChecker:
        up = self.cfg.get("updates") or {}
        return UpdateChecker(up.get("releasesUrl"), __version__, state_file=self.paths.update_state,
                             fetch=self._update_fetch, interval_hours=up.get("checkHours") or 24)

    def rebuild_providers(self) -> None:
        """Rebuild the provider chain from the current config. An engine that can swap providers
        live keeps its drop; otherwise the engine is replaced."""
        replace = False
        with self._rebuild_lock:
            with self._lock:
                new = self._build_providers()
                eng = self.engine
                swap = getattr(eng, "set_providers", None) if eng is not None else None
                if callable(swap):
                    in_use = eng.current_provider() if callable(getattr(eng, "current_provider", None)) else None
                    old = swap(new) or list(self.providers)
                    stale = [p for p in old if p is not in_use and p not in new]
                else:
                    stale = list(self.providers)
                    replace = eng is not None
                self.providers = new
                if self._side_spotify is not None:
                    stale.append(self._side_spotify)
                    self._side_spotify = None
            if replace:
                self._replace_engine()
            for p in stale:
                try:
                    p.close()
                except Exception:  # noqa: BLE001
                    pass

    def _rebuild_engine(self) -> None:
        """New providers and a new engine (after the WoW folder changed)."""
        with self._rebuild_lock:
            with self._lock:
                old = list(self.providers) + ([self._side_spotify] if self._side_spotify else [])
                self._side_spotify = None
                self.providers = self._build_providers()
            self._replace_engine()
            for p in old:
                try:
                    p.close()
                except Exception:  # noqa: BLE001
                    pass

    def restart_transports(self) -> None:
        """Stop and rebuild the transports (stopping joins their threads: never under ``_lock``)."""
        with self._rebuild_lock:
            with self._lock:
                old, self.transports = self.transports, []
            cli._stop_all(old)
            if not self._started and self.engine is None:
                return
            new = self._start_transports()
            with self._lock:
                self.transports = new

    # ------------------------------------------------- frames and link --
    def submit(self, event: Any, transport_name: str) -> None:
        """The transports' ``on_event``: link bookkeeping, the pause gate, then the engine."""
        self._note_frame(transport_name)
        if is_test_event(event):
            self._link_tick(event, transport_name)
        if self._paused:
            return
        eng = self.engine
        if eng is not None:
            eng.submit(event, transport_name)

    def _note_frame(self, transport_name: str) -> None:
        now = self._clock()
        with self._lock:
            first = self._link["firstAt"] is None
            if first:
                self._link["firstAt"] = now
            self._link["lastAt"] = now
            self._link["transport"] = transport_name
            self._link["frames"] += 1
        if first:
            log.info("linked to World of Warcraft (%s)", transport_name)
            self.notifier.notify(tray.NOTE_LINKED, "Linked to World of Warcraft. Your needle drops will play from "
                                                   "here; NeedleDropper stays in the tray.",
                                 action=self.open_setup)
            self._nudge("link")

    def on_link_check(self, frame: Any, transport: Any = None) -> None:
        """The engine's ``on_link_check(frame, transport)``: a TEST / PING frame decoded."""
        self._link_tick(frame, str(transport or ""))

    def _link_tick(self, event: Any, transport_name: str) -> None:
        now = self._clock()
        key = (getattr(event, "seq", None), _event_type_name(event), _event_flags(event),
               getattr(event, "place_key", None) or getattr(event, "n", None))
        artist, title = getattr(event, "artist", "") or "", getattr(event, "title", "") or ""
        with self._lock:
            last = self._test_keys.get(key)
            if last is not None and now - last < TEST_DEDUPE_S:
                return
            self._test_keys = {k: t for k, t in self._test_keys.items() if now - t < TEST_DEDUPE_S}
            self._test_keys[key] = now
            self._check.update(heardAt=now, heardWall=time.time(), transport=transport_name or None,
                               count=self._check["count"] + 1, what=_event_type_name(event),
                               label=getattr(event, "label", None) or None,
                               song=_song(artist, title) or None)
        log.info("test signal from World of Warcraft (%s, %s)", _event_type_name(event), transport_name or "?")
        self.notifier.notify(tray.NOTE_TEST, "Test signal received: NeedleDropper can hear World of Warcraft.",
                             action=self.open_setup)
        self._nudge("link")

    def link_check(self) -> dict[str, Any]:
        """The Check-the-link card: the last test signal, and when the game was last heard at all."""
        now = self._clock()
        with self._lock:
            chk, lk = dict(self._check), dict(self._link)
            transports = list(self.transports)

        def ago(t: Optional[float]) -> Optional[float]:
            return None if t is None else round(max(0.0, now - t), 1)

        return {"heard": chk["heardAt"] is not None, "heardAgoSec": ago(chk["heardAt"]),
                "heardAt": _now_iso(chk["heardWall"]) if chk["heardWall"] else None,
                "transport": chk["transport"], "count": chk["count"], "what": chk["what"], "label": chk["label"],
                "song": chk["song"], "seen": lk["firstAt"] is not None, "lastSeenAgoSec": ago(lk["lastAt"]),
                "seenTransport": lk["transport"], "frames": lk["frames"], "listening": bool(transports),
                "transports": self._transport_states(transports)}

    def _transport_states(self, running: list) -> dict[str, Any]:
        tcfg = self.cfg.get("transport") or {}
        by_name = {getattr(t, "name", ""): t for t in running}
        out: dict[str, Any] = {}
        for name in ("pixel", "chatlog"):
            sec = tcfg.get(name) or {}
            enabled = bool(sec.get("enabled", True))
            entry: dict[str, Any] = {"enabled": enabled, "running": False, "ok": False,
                                     "detail": "switched off in config.json" if not enabled else "not running"}
            t = by_name.get(name)
            if t is not None:
                try:
                    st = t.status() or {}
                except Exception as exc:  # noqa: BLE001
                    st = {"detail": f"status error: {exc}"}
                entry.update({"running": bool(st.get("running", True)), "ok": bool(st.get("ok")),
                              "detail": str(st.get("detail") or "")})
            out[name] = entry
        return out

    # ------------------------------------------------------------ pause --
    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> bool:
        """Pause / resume needle drops (the tray item). Returns the new state."""
        paused = bool(paused)
        with self._lock:
            changed = paused != self._paused
            self._paused = paused
            eng = self.engine
        if changed:
            log.info("needle drops %s", "paused" if paused else "resumed")
            if paused and eng is not None:
                stop = getattr(eng, "stop_playback", None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:  # noqa: BLE001
                        log.warning("could not stop the current drop", exc_info=True)
            listener = self.on_pause_changed
            if listener is not None:
                try:
                    listener(paused)
                except Exception:  # noqa: BLE001
                    log.exception("pause listener failed")
            self._nudge("pause")
        return paused

    # ----------------------------------------------------------- config --
    def update_config(self, patch: Any) -> dict[str, Any]:
        """Validate a Connect page patch, write it to config.json and apply it live. Returns
        ``{"ok": True, "warnings": [...]}``; raises :class:`PatchError` / ``ConfigError``."""
        clean = validate_config_patch(patch, self.cfg)
        if not clean:
            return {"ok": True, "warnings": []}
        provs = list(self.cfg.get("providers") or [])
        for name in ("spotify", "youtube"):
            if (clean.get(name) or {}).get("enabled") is True and name not in provs:
                provs.append(name)
                clean["providers"] = list(provs)
        warnings = self._write_config(clean)
        if "startWithWindows" in clean:
            problem = self.set_autostart(clean["startWithWindows"], persist=False)
            if problem:
                warnings.append(problem)
        self._nudge()
        return {"ok": True, "warnings": warnings}

    def _write_config(self, updates: dict[str, Any]) -> list[str]:
        """Merge ``updates`` into config.json (no schema: internal writes) and apply them.

        The file write and the switch to the new config happen under the lock; rebuilding
        providers / transports (which joins threads) happens after it is released.
        """
        with self._lock:
            update_config_file(updates, self.config_file)
            new_cfg = load_config(self.config_file)
            warnings, actions = self._apply(new_cfg)
        for action in actions:
            action()
        return warnings

    def _apply(self, new: dict[str, Any]) -> tuple[list[str], list[Callable[[], Any]]]:
        """Switch to ``new``; returns warnings and the rebuilds to run once the lock is released."""
        old = self.cfg
        self.cfg = new
        new_paths = paths_from_config(new)
        paths_changed = new_paths != self.paths
        self.paths = new_paths
        warnings: list[str] = []
        actions: list[Callable[[], Any]] = []
        if paths_changed:
            self._wow_det = None
            self._game_cache = None
            if wow_install_score(new_paths.wow_root, new.get("flavorDir") or DEFAULT_FLAVOR) == 0:
                warnings.append(f"{new_paths.flavor_root} does not exist. Is this your World of Warcraft folder? "
                                "It is the folder that contains _forever_.")
            else:
                actions.append(self._ensure_companion_data)
        if (old.get("spotify") or {}).get("clientId") != (new.get("spotify") or {}).get("clientId"):
            login, self._login = self._login, None
            self._account = None
            if login is not None and not login.done:
                threading.Thread(target=login.cancel, daemon=True).start()
        if self._started and paths_changed:
            actions.append(self._rebuild_engine)  # it records for the game in the new AddOns folder
        elif self._started and any(old.get(k) != new.get(k) for k in ("providers", "spotify", "local", "youtube")):
            actions.append(self.rebuild_providers)
        if self._started and (paths_changed or old.get("transport") != new.get("transport")):
            actions.append(self.restart_transports)
        eng = self.engine
        if eng is not None:
            for attr, key, default in (("fade_in_ms", "fadeInMs", 2500), ("fade_out_ms", "fadeOutMs", 3000)):
                if hasattr(eng, attr):
                    try:
                        setattr(eng, attr, int(new.get(key, default)))
                    except (TypeError, ValueError):
                        pass
        self._configure_deck()
        self._hk_wake.set()
        return warnings, actions

    def _nudge(self, what: str = "state") -> None:
        notify = getattr(self.deck, "notify_panels", None)
        if notify is not None:
            try:
                notify(what)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------- autostart --
    def autostart_available(self) -> bool:
        return self.autostart_winreg is not None or sys.platform == "win32"

    def autostart_enabled(self) -> bool:
        """Start with Windows is on (the registry's word, Task Manager's switch included)."""
        try:
            return autostart.is_enabled(winreg=self.autostart_winreg)
        except Exception:  # noqa: BLE001 - a broken registry read must not break the menu
            log.debug("autostart state unreadable", exc_info=True)
            return False

    def set_autostart(self, enabled: bool, *, persist: bool = True) -> Optional[str]:
        """Turn Start with Windows on/off and remember the choice; returns a problem text or None."""
        enabled = bool(enabled)
        problem: Optional[str] = None
        try:
            autostart.set_enabled(enabled, args=AUTOSTART_ARGS, winreg=self.autostart_winreg)
        except autostart.AutostartError as exc:
            problem = f"Windows did not accept the Start with Windows change: {exc}"
            log.warning("%s", problem)
        if persist and self.cfg.get("startWithWindows") is not enabled:
            try:
                self._write_config({"startWithWindows": enabled})
            except (ConfigError, OSError) as exc:
                log.warning("could not save the Start with Windows choice: %s", exc)
        self._nudge("autostart")
        return problem

    def recheck_autostart(self) -> Optional[str]:
        """At every start: a player who chose Start with Windows keeps it (DESIGN-v1.2 1.4)."""
        if not (self.cfg.get("setupComplete") and self.cfg.get("startWithWindows") is True):
            return None
        try:
            result = autostart.ensure(args=AUTOSTART_ARGS, winreg=self.autostart_winreg)
        except autostart.AutostartError as exc:
            log.warning("Start with Windows could not be restored: %s", exc)
            return None
        if result in (autostart.ENSURE_CREATED, autostart.ENSURE_UPDATED):
            log.info("Start with Windows %s", "restored" if result == autostart.ENSURE_CREATED else "updated")
        return result

    # ---------------------------------------------------------- spotify --
    def spotify_provider(self) -> Any:
        """The chain's Spotify provider, or a side instance when Spotify is switched off
        (also used when the chain holds a stand-in without the Connect page helpers)."""
        for p in self.providers:
            if getattr(p, "name", None) == "spotify" and callable(getattr(p, "status_dict", None)):
                return p
        with self._lock:
            if self._side_spotify is None:
                from .providers.spotify import SpotifyProvider

                kw = {"http": self.spotify_http} if self.spotify_http is not None else {}
                self._side_spotify = SpotifyProvider(self.cfg.get("spotify") or {}, self.paths, **kw)
            return self._side_spotify

    def spotify_connect(self, client_id: Optional[str]) -> dict[str, Any]:
        """Start the browser sign-in (PKCE); returns Spotify's authorize URL for the page to open."""
        from .providers.spotify_auth import start_login

        sp_cfg = self.cfg.get("spotify") or {}
        cid = (client_id if client_id is not None else sp_cfg.get("clientId") or "").strip()
        try:
            cid = _v_client_id(cid)
        except ValueError as exc:
            raise PatchError(f"Client ID {exc}", {"spotify.clientId": str(exc)}) from exc
        if not cid:
            raise PatchError("Paste your Spotify app's Client ID first.", {"spotify.clientId": "must not be empty"})
        updates: dict[str, Any] = {}
        if cid != sp_cfg.get("clientId"):
            updates.setdefault("spotify", {})["clientId"] = cid
        if not provider_enabled(self.cfg, "spotify"):
            updates.setdefault("spotify", {})["enabled"] = True
        provs = list(self.cfg.get("providers") or [])
        if "spotify" not in provs:
            updates["providers"] = ["spotify"] + provs
        old, self._login = self._login, None
        if old is not None and not old.done:
            old.cancel()  # frees the callback port before the new listener binds it
        if updates:
            self._write_config(updates)
        session = start_login(self.cfg.get("spotify") or {}, self.paths, http=self.spotify_http,
                              on_change=self._on_login_change)
        with self._lock:
            self._login = session
            self._account = None
            self._spotify_user_off = False
        self.notifier.reset(tray.NOTE_SPOTIFY_EXPIRED)
        self._nudge("spotify")
        return {"authorizeUrl": session.authorize_url, "redirectUri": session.redirect_uri, "state": session.state}

    def _on_login_change(self, session: Any) -> None:
        # Called on the login thread; follow up on a fresh thread so a concurrent cancel()
        # (which joins the login thread) can never deadlock.
        threading.Thread(target=self.guard.wrap(lambda: self._after_login(session), "needledropper-spotify-done"),
                         name="needledropper-spotify-done", daemon=True).start()

    def _after_login(self, session: Any) -> None:
        if session is not self._login:
            return
        if session.state == "connected":
            prof = getattr(session, "profile", None) or {}
            status = prof.get("status")
            if status is None:
                status = 403 if (session.warning and not getattr(session, "save_error", None)) else 200
            with self._lock:
                self._account = {"name": session.account, "status": status, "at": self._clock(),
                                 "product": prof.get("product"), "country": prof.get("country"),
                                 "problem": prof.get("problem")}
                self._spotify_was_connected = True
            sp = next((p for p in self.providers if getattr(p, "name", None) == "spotify"), None)
            reset = getattr(sp, "reset_session", None)
            if callable(reset):
                reset()
            else:
                self.rebuild_providers()
            self.notifier.reset(tray.NOTE_SPOTIFY_EXPIRED)
        self._nudge("spotify")
        self._hk_wake.set()

    def spotify_disconnect(self) -> dict[str, Any]:
        """Sign out: forget the stored login (the Client ID stays)."""
        from .providers.spotify_auth import TokenStore

        login, self._login = self._login, None
        with self._lock:
            self._account = None
            self._spotify_user_off = True
            self._spotify_was_connected = False
        if login is not None and not login.done:
            login.cancel()
        TokenStore(self.paths).clear()
        self.rebuild_providers()
        self._nudge("spotify")
        return {"ok": True}

    def spotify_devices(self) -> tuple[int, dict[str, Any]]:
        from .providers.base import ProviderError, ProviderUnavailable

        sp = self.spotify_provider()
        if not sp.available():
            return 409, {"error": sp.unavailable_reason() or "Spotify is not connected."}
        try:
            devices = sp.list_devices()
        except ProviderUnavailable as exc:
            return 409, {"error": str(exc)}
        except ProviderError as exc:
            return 502, {"error": str(exc)}
        return 200, {"devices": devices, "selected": (self.cfg.get("spotify") or {}).get("deviceName") or ""}

    def _spotify_status(self) -> dict[str, Any]:
        try:
            st = dict(self.spotify_provider().status_dict() or {})
        except Exception as exc:  # noqa: BLE001 - e.g. Spotify support missing
            st = {"badge": "error", "message": str(exc), "loggedIn": False, "configured": False}
        return st

    def _spotify_state(self) -> dict[str, Any]:
        from .providers.spotify_auth import DASHBOARD_URL, redirect_uri_for

        sp_cfg = self.cfg.get("spotify") or {}
        port = int(sp_cfg.get("redirectPort") or 8321)
        st = self._spotify_status()
        login = self._login.to_dict() if self._login is not None else None
        badge, message = st.get("badge"), st.get("message")
        account = dict(self._account) if self._account else None
        warning = login.get("warning") if login else None
        if account and account.get("status") == "expired" and badge == "connected":
            badge, message = "error", "Your Spotify sign-in has expired. Sign in again."
        if login and login.get("state") in ("error", "timeout") and badge != "connected":
            badge, message = "error", login.get("message")
        problem = (account or {}).get("problem")
        if account and account.get("status") == 403 and problem != "scope" and not warning:
            warning = ("Spotify refused this account for your app. Development apps work only for their owner "
                       "(who needs Premium) and the accounts added under User Management in the dashboard.")
        save_error = st.get("saveError") or (login or {}).get("saveError")
        if save_error and not warning:
            warning = (f"Signed in, but Windows refused to save the sign-in ({save_error}). NeedleDropper keeps it "
                       "while it runs and keeps trying to save it; if you quit first, sign in again.")
        state = "waiting" if (login and login.get("state") == "waiting") else badge
        # /me's "product" needs user-read-private (real-client finding #3); without it a 200 from /me is the
        # best hint (development apps only run for a Premium owner).
        product = (account or {}).get("product") or (login or {}).get("product")
        country = (account or {}).get("country") or (login or {}).get("country")
        if badge == "premium":
            premium = "required"
        elif product == "premium":
            premium = "ok"
        elif product:
            premium = "required"  # "free" / "open": Spotify refuses playback control
        elif badge == "connected" and account and account.get("status") == 200:
            premium = "ok"
        else:
            premium = "unknown"
        missing = list(st.get("missingScopes") or [])
        reconnect = bool(missing) and badge == "connected" and state != "waiting"
        return {"enabled": "spotify" in (self.cfg.get("providers") or []) and provider_enabled(self.cfg, "spotify"),
                "clientId": sp_cfg.get("clientId") or "", "redirectUri": redirect_uri_for(port),
                "dashboardUrl": DASHBOARD_URL, "state": state, "message": message, "connected": badge == "connected",
                "account": (account or {}).get("name") or (login or {}).get("account"), "premium": premium,
                "product": product, "country": country, "missingScopes": missing, "reconnect": reconnect,
                "reconnectMessage": st.get("scopeMessage") if reconnect else None, "saveError": save_error,
                "warning": warning, "deviceName": sp_cfg.get("deviceName") or "", "login": login}

    def _check_spotify_account(self) -> None:
        """Ask Spotify who is signed in (``GET /v1/me``); a revoked sign-in is reported as expired."""
        from .providers.spotify_auth import SpotifyAuthError, TokenManager, TokenStore, fetch_profile

        cid = str((self.cfg.get("spotify") or {}).get("clientId") or "").strip()
        if not cid:
            return
        # Use the provider's own token manager when there is one: refresh tokens rotate, so two
        # managers refreshing at the same moment would invalidate each other's token.
        manager = getattr(self.spotify_provider(), "_tokens", None)
        if not callable(getattr(manager, "access_token", None)) or getattr(manager, "client_id", cid) != cid:
            manager = TokenManager(cid, TokenStore(self.paths), http=self.spotify_http)
        try:
            token = manager.access_token()
        except SpotifyAuthError as exc:
            if exc.revoked and exc.error != "not_logged_in":
                with self._lock:
                    self._account = {"name": None, "status": "expired", "at": self._clock()}
                self._spotify_expired()
            return
        except OSError as exc:
            log.debug("Spotify account check: %s", exc)
            return
        prof = fetch_profile(token, self.spotify_http)
        if prof["status"] is None:
            return
        with self._lock:
            self._account = {"name": prof["name"], "status": prof["status"], "at": self._clock(),
                             "product": prof["product"], "country": prof["country"], "problem": prof["problem"]}
        if prof["product"]:
            log.info("Spotify account: %s (%s%s)", prof["name"] or "?", prof["product"],
                     f", {prof['country']}" if prof["country"] else "")
        self._nudge("spotify")

    def _watch_spotify(self, now: float) -> None:
        if now < self._next_spotify_watch:
            return
        self._next_spotify_watch = now + SPOTIFY_WATCH_S
        if not (self.cfg.get("spotify") or {}).get("clientId"):
            return
        st = self._spotify_status()
        badge = st.get("badge")
        if badge == "connected":
            self._spotify_was_connected = True
            acc = self._account
            if acc is None or (acc.get("status") != "expired" and now - float(acc.get("at") or 0)
                               >= SPOTIFY_ACCOUNT_RECHECK_S):
                self._check_spotify_account()
            return
        if self._spotify_was_connected and not self._spotify_user_off and st.get("configured") \
                and badge in ("error", "not-set-up"):
            self._spotify_was_connected = False
            self._spotify_expired()

    def _spotify_expired(self) -> None:
        log.warning("the Spotify sign-in has expired")
        self.notifier.notify(tray.NOTE_SPOTIFY_EXPIRED, "Your Spotify sign-in has expired. Click here to sign in "
                                                        "again.", title="Spotify sign-in expired",
                             action=self.open_setup, level="warning")
        self._nudge("spotify")

    # ------------------------------------------------- engine / player --
    def on_engine_status(self, *args: Any, **kwargs: Any) -> None:
        """The engine's ``on_status``: playback changes, plus notices (see :func:`parse_engine_notice`)."""
        kind, info = parse_engine_notice(args, kwargs)
        if kind == tray.NOTE_NOT_FOUND:
            self._song_not_found(info)
        elif kind == tray.NOTE_NOT_EMBEDDABLE:
            self._not_embeddable(info)
        elif kind == tray.NOTE_SPOTIFY_EXPIRED:
            with self._lock:
                self._account = {"name": None, "status": "expired", "at": self._clock()}
            self._spotify_expired()
        elif kind == tray.NOTE_SPOTIFY_STUCK:
            self._spotify_stuck(info)
        elif str(info.get("kind") or "") == "searchFailed" and info.get("unavailable"):
            # Spotify itself became unusable during a search (sign-in revoked, Premium refused, ...):
            # check its state now instead of at the next 30 s tick; that check says what to tell.
            self._next_spotify_watch = 0.0
            self._hk_wake.set()
        self._nudge("playing")

    def _spotify_stuck(self, info: dict[str, Any]) -> None:
        """Spotify accepted a song but never started it (real-client finding #4): the engine already
        played the next provider; tell the player how to fix Spotify."""
        from .providers.spotify import STUCK_MESSAGE

        song = _song(info.get("artist"), info.get("title"))
        what = f" It accepted {song} but did not start it." if song else ""
        # Real-client #10: say once what happens now -- YouTube plays until Spotify works again.
        self.notifier.notify(tray.NOTE_SPOTIFY_STUCK, f"{STUCK_MESSAGE}.{what} YouTube plays NeedleDropper's songs "
                             "until Spotify works again.", title="Spotify isn't starting songs",
                             level="warning", action=self.open_logs)
        self._push_game_status()  # the game keeps YouTube ids in its frames (fit rule step 7)
        self._nudge("spotify")

    def _song_not_found(self, info: dict[str, Any]) -> None:
        key = str(info.get("key") or "")
        song = _song(info.get("artist"), info.get("title")) or str(info.get("query") or "").replace("\x1f", " \u2013 ")
        if not song and key.startswith("q:"):  # the engine's search key: "q:" + artist U+001F title
            song = _song(*(key[2:].split("\x1f", 1) + [""])[:2])
        if not song and key.startswith("sp:"):
            song = f"the Spotify track {key[3:]}"
        where = str(info.get("label") or "").strip()
        text = (f"No match for {song or 'this song'}{f' ({where})' if where else ''} on Spotify. In game, paste the "
                "song's Spotify link into the drop (/needle).")
        self.notifier.notify(tray.NOTE_NOT_FOUND, text, title="Song not found on Spotify",
                             key=(key or song or where or "?").lower(), level="warning")

    def _not_embeddable(self, info: dict[str, Any]) -> None:
        song = _song(info.get("artist"), info.get("title"))
        playing = info.get("playing")
        if not song and isinstance(playing, dict) and info.get("dropId") and playing.get("id") == info.get("dropId"):
            song = _song(playing.get("artist"), playing.get("title"))
        video = str(info.get("youtube") or info.get("videoId") or "").strip()
        what = YOUTUBE_EMBED_ERRORS.get(info.get("code"), "can't be played outside YouTube")  # type: ignore[arg-type]
        where = str(info.get("label") or "").strip()
        if song:
            subject = f"The YouTube video for {song}"
        elif where:
            subject = f"The YouTube video for {where}"
        else:
            subject = f"YouTube video {video}" if video else "A YouTube video"
        self.notifier.notify(tray.NOTE_NOT_EMBEDDABLE,
                             f"{subject} {what}. In game, paste another YouTube link into the drop (/needle).",
                             title="YouTube video can't be played here", key=(video or song or "?"), level="warning")

    def _on_player_status(self, status: dict[str, Any], play: Optional[dict[str, Any]],
                          card: Optional[dict[str, Any]]) -> None:
        """The deck's player reports: YouTube's "no embedding" errors become a notification."""
        if status.get("state") != "error":
            return
        code = status.get("code")
        if isinstance(code, bool) or not isinstance(code, int) or code not in YOUTUBE_EMBED_ERRORS:
            return
        if play is not None and play.get("source") not in (None, "youtube"):
            return
        info: dict[str, Any] = {"code": code, "youtube": (play or {}).get("videoId")}
        if card and (play is None or card.get("dropId") == play.get("dropId")):
            info.update(artist=card.get("artist"), title=card.get("title"), label=card.get("label"))
        self._not_embeddable(info)

    # ------------------------------------------------------------ faults --
    def _on_thread_crash(self, thread_name: str, exc: BaseException, tb: Any) -> None:
        key, label = component_for_thread(thread_name)
        log.error("%s stopped (thread %s): %s", label, thread_name,
                  "".join(traceback.format_exception(type(exc), exc, tb)).rstrip())
        self.faults.record(key, label, exc, thread=thread_name)
        restart = key in RESTARTABLE and key not in self._restarted and self._started
        if restart:
            with self._lock:
                self._restart_requests.add(key)
            self._hk_wake.set()
        tail = (" NeedleDropper is restarting it." if restart else
                " If songs stop playing, quit NeedleDropper from the tray and start it again.")
        self.notifier.notify(tray.NOTE_FAULT, f"{label} stopped because of an error.{tail} Details are in the log.",
                             title=f"NeedleDropper: {label} stopped", key=key, level="warning",
                             action=self.open_logs)
        self._nudge("fault")

    def _do_restarts(self) -> None:
        with self._lock:
            todo, self._restart_requests = self._restart_requests, set()
        for key in sorted(todo):
            if key in self._restarted or not self._started:
                continue
            self._restarted.add(key)
            log.info("restarting the %s after its thread stopped", key)
            if key == "engine":
                self._replace_engine()
            else:
                self.restart_transports()
            self.faults.mark_restarted(key)
            self._nudge("fault")

    # ------------------------------------------------------ housekeeping --
    def _housekeeping(self) -> None:
        while not self._hk_stop.is_set():
            self._hk_wake.wait(self._hk_interval)
            self._hk_wake.clear()
            if self._hk_stop.is_set():
                return
            now = self._clock()
            for step in (self._do_restarts, lambda: self._watch_spotify(now), self._watch_game,
                         self._push_game_status, self._watch_savedvars, self._maybe_check_updates,
                         lambda: self._watch_player(now)):
                if self._hk_stop.is_set():
                    return
                try:
                    step()
                except Exception:  # noqa: BLE001 - one failing check must not stop the others
                    log.exception("background check failed")

    def _savedvars_stamp(self) -> Optional[tuple]:
        """``(path, mtime_ns, size)`` of every NeedleDropper SavedVariables file (None: no game folder)."""
        from . import savedvars

        root = self.paths.flavor_root
        if not root.is_dir():
            return None
        out = []
        for f in savedvars.find_files(root):
            try:
                st = os.stat(f.path)
            except OSError:
                continue
            out.append((str(f.path), st.st_mtime_ns, st.st_size))
        return tuple(sorted(out))

    def _watch_savedvars(self) -> None:
        """When the game writes ``NeedleDropper.lua`` (logout, /reload): keep a rolling backup of it
        (10 copies in ``<app dir>\\backups``) and search Spotify ahead of time for the player's own
        search-only drops (DESIGN-v1.2 2.1). The game folder is only read; checked every few seconds
        by file time and size (no change: nothing is read)."""
        from . import savedvars

        stamp = self._savedvars_stamp()
        if stamp is None or stamp == self._sv_stamp:
            return
        first = self._sv_stamp is None
        self._sv_stamp = stamp
        if not stamp:
            return
        if self.game_running() and not first:
            # the client writes the files at logout / reload; give it a moment to finish
            time.sleep(0.5)
        try:
            made = savedvars.backup_savedvariables(self.paths.flavor_root, self.paths.app_dir / "backups")
            if made:
                log.info("backed up the game's NeedleDropper settings (%d file(s)) to %s", len(made),
                         self.paths.app_dir / "backups")
        except (OSError, ValueError) as exc:
            log.info("could not back up the game's NeedleDropper settings: %s", exc)
        eng = self.engine
        prewarm = getattr(eng, "prewarm_songs", None) if eng is not None else None
        if callable(prewarm):
            try:
                songs = savedvars.search_only_songs(self.paths.flavor_root)
                queued = prewarm(songs) if songs else 0
                if queued:
                    log.info("searching Spotify ahead of time for %d song(s) of your own soundtracks", queued)
            except Exception as exc:  # noqa: BLE001 - a convenience; never let it stop the checks
                log.info("could not read the game's soundtracks for the Spotify pre-search: %s", exc)

    def _maybe_check_updates(self) -> None:
        checker = self.updates
        if checker is None or not checker.due():
            return
        info = checker.check()
        if info is None:
            return
        page = info.url
        action = (lambda: webbrowser.open(page)) if page else None
        self.notifier.notify(tray.NOTE_UPDATE, f"NeedleDropper {info.version} is available (you have {__version__})."
                             + (" Click to open the download page." if page else ""),
                             title="NeedleDropper update", key=info.version, action=action)
        self._nudge("update")

    def _game_status(self) -> dict[str, Any]:
        st = self._spotify_status()
        badge = st.get("badge")
        spotify = {"connected": "connected", "premium": "premium-required", "allowlist": "error",
                   "error": "error"}.get(str(badge), "not-connected")
        if spotify == "connected" and st.get("stuck"):
            # Signed in, but it accepts songs and never starts them this session: "error" makes the
            # game keep the YouTube id when a frame can carry only one (DESIGN 2.3 fit rule step 7).
            spotify = "error"
        running = {getattr(t, "name", ""): t for t in self.transports}
        return {"spotify": spotify,
                "youtube": any(getattr(p, "name", None) == "youtube" for p in self.providers),
                "transports": {name: name in running for name in ("pixel", "chatlog")}}

    def _push_game_status(self) -> None:
        """Tell the game (``NeedleDropper_Companion``) what this app can play, when it changed."""
        from . import gamedata

        setter = getattr(gamedata, "set_status", None)
        if setter is None or not self.paths.addons_dir.is_dir():
            return
        status = self._game_status()
        if status == self._game_status_sent:
            return
        try:
            setter(self.paths.addons_dir, **status)
        except (TypeError, ValueError, OSError) as exc:
            log.info("could not update the game's companion data: %s", exc)
            return
        self._game_status_sent = status

    def _watch_game(self) -> None:
        """When the game starts, refresh the data addon's "written" time (the in-game "app seen" line);
        when it closes (quit or crash), stop the drop it was playing: no HELLO or CANCEL will ever end
        it otherwise (ENG-HELLO-SEQ-ROLLBACK)."""
        running = self.game_running()
        was, self._game_was_running = self._game_was_running, running
        if not running:
            self._data_needs_restart = False  # the next game start loads NeedleDropper_Companion
        if was and not running:
            self._game_closed()
        if not running or was or not self.paths.addons_dir.is_dir():
            return
        from . import gamedata

        touch = getattr(gamedata, "touch", None)
        if callable(touch):
            try:
                touch(self.paths.addons_dir)
            except (TypeError, ValueError, OSError) as exc:
                log.info("could not refresh the game's companion data: %s", exc)

    def _game_closed(self) -> None:
        eng = self.engine
        stop = getattr(eng, "stop_playback", None)
        if callable(stop) and getattr(eng, "is_playing", False):
            log.info("World of Warcraft closed: stopping the drop that was playing")
            try:
                stop()
            except Exception as exc:  # noqa: BLE001 - housekeeping must go on
                log.info("could not stop the drop: %s", exc)

    def _flush_game_data(self) -> None:
        from . import gamedata

        flush = getattr(gamedata, "flush", None)
        if callable(flush) and self.paths.addons_dir.is_dir():
            try:
                flush(self.paths.addons_dir)
            except Exception as exc:  # noqa: BLE001 - shutting down; atexit tries again
                log.info("could not write the game's companion data: %s", exc)

    def _ensure_companion_data(self) -> None:
        """Create ``NeedleDropper_Companion`` before the game starts (the client only sees folders that exist at launch).

        Created while the game already runs, the game cannot see it until it is restarted: the
        Connect page says so (journey review F6) until the game is seen closed."""
        from . import gamedata

        if not self.paths.addons_dir.is_dir():
            return
        try:
            existed = bool(gamedata.is_companion_addon_installed(self.paths.addons_dir))
        except Exception:  # noqa: BLE001
            existed = True
        try:
            gamedata.ensure_companion_addon(self.paths.addons_dir)
        except (gamedata.GameDataError, OSError) as exc:
            log.info("could not create the NeedleDropper_Companion data addon: %s", exc)
            return
        if not existed and self.game_running():
            self._data_needs_restart = True
            log.info("created NeedleDropper_Companion while World of Warcraft runs: the game sees it after a restart")

    # ------------------------------------------------------- operations --
    def open_setup(self) -> bool:
        """Open the Connect page in the default browser (tray, notifications)."""
        return bool(self.deck.open_panel())

    def open_logs(self) -> tuple[int, dict[str, Any]]:
        target = self.paths.logs_dir
        try:
            target.mkdir(parents=True, exist_ok=True)
            self.opener(str(target))
        except Exception as exc:  # noqa: BLE001 - no handler, access denied, ...
            return 500, {"error": f"Could not open {target}: {exc}"}
        return 200, {"ok": True, "path": str(target)}

    def game_running(self) -> bool:
        paths = self.paths
        key = str(paths.game_exe)
        now = time.monotonic()
        cached = self._game_cache
        if cached is not None and cached[2] == key and now - cached[0] < GAME_RUNNING_CACHE_S:
            return cached[1]
        fn = self._game_running or cli.is_game_running
        try:
            running = bool(fn(paths))
        except Exception:  # noqa: BLE001 - process listing is best effort
            running = False
        self._game_cache = (now, running, key)
        return running

    def _detect(self) -> Any:
        now = time.monotonic()
        cached = self._wow_det
        if cached is not None and now - cached[0] < WOW_DETECT_CACHE_S:
            return cached[1]
        det = detect_wow_root(self.cfg.get("flavorDir") or DEFAULT_FLAVOR, read_registry=self.wow_registry,
                              default=self.wow_default)
        self._wow_det = (now, det)
        return det

    def _autodetect_wow(self) -> None:
        """First run (or a configured folder without ``_forever_``): use the registry's InstallPath."""
        flavor = self.cfg.get("flavorDir") or DEFAULT_FLAVOR
        if wow_install_score(self.paths.wow_root, flavor) > 0:
            return
        det = self._detect()
        if not det.found:
            return
        if os.path.normcase(os.path.normpath(str(det.root))) == os.path.normcase(os.path.normpath(str(self.paths.wow_root))):
            return
        try:
            update_config_file({"wowRoot": str(det.root)}, self.config_file)
            self.cfg = load_config(self.config_file)
            self.paths = paths_from_config(self.cfg)
            log.info("found World of Warcraft (%s): %s", det.source, det.root)
        except (ConfigError, OSError) as exc:
            log.warning("could not save the detected World of Warcraft folder: %s", exc)

    def detect_wow(self) -> dict[str, Any]:
        self._wow_det = None
        det = self._detect()
        if not det.found:
            return {"found": False, "message": "World of Warcraft: Forever was not found. Type the folder that contains "
                                               "_forever_ (usually C:\\Program Files (x86)\\World of Warcraft).",
                    "tried": [{"where": w, "path": p} for w, p, _s in det.tried]}
        root = str(det.root)
        changed = os.path.normcase(os.path.normpath(root)) != os.path.normcase(os.path.normpath(str(self.paths.wow_root)))
        if changed:
            self._write_config({"wowRoot": root})
        where = "in the Windows registry" if det.source == "registry" else "in the default location"
        return {"found": True, "wowRoot": root, "source": det.source, "changed": changed,
                "message": f"Found World of Warcraft {where}: {root}"}

    def _wow_source(self) -> str:
        if wow_install_score(self.paths.wow_root, self.cfg.get("flavorDir") or DEFAULT_FLAVOR) == 0:
            return "none"
        det = self._detect()
        if det.found and os.path.normcase(os.path.normpath(str(det.root))) == \
                os.path.normcase(os.path.normpath(str(self.paths.wow_root))):
            return str(det.source)
        return "config"

    def install_addon(self, force: bool = False) -> dict[str, Any]:
        """The fallback "Install addon" / "Update addon": copies the bundled addon (incl. ``Sets/``).

        Refused when an addon manager (or anything else) installed the addon: this app never
        overwrites an addon it did not install (DESIGN-v1.2 1.4, the TSM AppHelper conflict).
        """
        st = addon_status(self.paths)
        if st.owner in ("manager", "other"):
            who = st.manager or "your addon manager"
            res = {"ok": False, "refused": True, "managed": True,
                   "message": (f"NeedleDropper {st.version or ''} was installed by {who} or by hand. Update it there: "
                               "this app never replaces an addon it did not install.").replace("  ", " ")}
        elif st.owner == "missing" and st.managers:
            res = {"ok": False, "refused": True, "managed": True,
                   "message": (f"Your addons are managed by {st.managers[0]}. Install NeedleDropper there "
                               "(search for NeedleDropper), so the two never fight over the folder.")}
        else:
            res = cli.install_addon(self.cfg, self.paths, source=self.addon_source, force=force,
                                    running=self.game_running())
            if res.get("ok"):
                self._ensure_companion_data()
        self._last_install = {"ok": bool(res.get("ok")), "message": res.get("message"), "at": _now_iso()}
        self._nudge("addon")
        return res

    def _addon_state(self) -> dict[str, Any]:
        st = addon_status(self.paths)
        src_toc = addon_toc(self.addon_source)
        bundled = read_toc_version(src_toc) if src_toc else None
        source_found = src_toc is not None
        have, new = version_key(st.version), version_key(bundled)
        update = st.owner == "companion" and source_found and have is not None and new is not None and new > have
        wow_found = wow_install_score(self.paths.wow_root, self.cfg.get("flavorDir") or DEFAULT_FLAVOR) > 0
        if st.owner == "missing":
            action = "install" if (source_found and wow_found and not st.managers) else None
        else:
            action = "update" if update else None
        try:
            from . import gamedata

            data_addon = gamedata.is_companion_addon_installed(self.paths.addons_dir)
        except Exception:  # noqa: BLE001
            data_addon = False
        in_game = self._engine_status().get("addon")
        outdated = bool(isinstance(in_game, dict) and in_game.get("outdated"))  # the game runs a v1.1 addon
        return {"installed": st.installed, "version": st.version, "toc": st.toc.name if st.toc else None,
                "outdatedInGame": outdated,
                "bundledVersion": bundled, "owner": st.owner, "managedBy": st.manager, "managers": list(st.managers),
                "legacy": st.legacy, "updateAvailable": bool(update), "action": action,
                "canInstall": action is not None, "pageUrl": (self.cfg.get("links") or {}).get("addonPage"),
                "addonDir": str(self.paths.addon_dir), "sourceFound": source_found, "companionData": data_addon,
                "dataNeedsRestart": bool(self._data_needs_restart and data_addon),
                "lastInstall": dict(self._last_install) if self._last_install else None}

    # ------------------------------------------------------------ state --
    def _engine_status(self) -> dict[str, Any]:
        eng = self.engine
        if eng is None:
            return {}
        try:
            st = eng.status()
        except Exception:  # noqa: BLE001 - a broken status must not break the page or the tray
            log.debug("engine status failed", exc_info=True)
            return {}
        return dict(st) if isinstance(st, dict) else {}

    def status_line(self) -> str:
        """One line for the tray menu and tooltip."""
        if self._paused:
            return "Needle drops paused"
        faults = self.faults.active()
        if faults and not faults[0].restarted:
            return f"Problem: {faults[0].component} stopped (see the log)"
        playing = self._engine_status().get("playing")
        if isinstance(playing, dict) and (playing.get("title") or playing.get("label")):
            song = _song(playing.get("artist"), playing.get("title"))
            place = str(playing.get("label") or "").strip()
            return "Playing: " + " \u00b7 ".join(x for x in (place, song) if x)
        now = self._clock()
        last = self._link.get("lastAt")
        if last is not None and now - last <= LINK_FRESH_S:
            line = "Linked to World of Warcraft"
        elif self.game_running():
            line = "World of Warcraft is running: waiting for the link"
        else:
            line = "Waiting for World of Warcraft"
        if self._spotify_status().get("badge") == "connected":
            line += " \u00b7 Spotify connected"
        return line

    def state(self) -> dict[str, Any]:
        """Everything the Connect page shows (no secrets)."""
        cfg, paths, deck = self.cfg, self.paths, self.deck
        flavor = cfg.get("flavorDir") or DEFAULT_FLAVOR
        score = wow_install_score(paths.wow_root, flavor)
        eng = self._engine_status()
        checker = self.updates
        return {
            "app": {"version": __version__, "frozen": resources.is_frozen(), "setupComplete": bool(cfg.get("setupComplete")),
                    "firstRun": should_open_panel({**cfg, "deck": {"panelOnStart": "first-run"}}),
                    "paused": self._paused, "panelUrl": deck.panel_url, "logDir": str(paths.logs_dir),
                    "logFile": str(paths.log_file), "configFile": str(self.config_location),
                    "tempLocation": autostart.temporary_location(), "status": self.status_line()},
            "wow": {"root": str(paths.wow_root), "flavorDir": flavor, "folder": str(paths.flavor_root),
                    "found": score > 0, "exe": score == 2, "source": self._wow_source(),
                    "addonsDir": str(paths.addons_dir), "running": self.game_running()},
            "addon": self._addon_state(),
            "spotify": self._spotify_state(),
            "youtube": {"enabled": "youtube" in (cfg.get("providers") or []) and provider_enabled(cfg, "youtube"),
                        "playerConnected": bool(getattr(deck, "page_open", False))},
            "link": self.link_check(),
            "autostart": {"available": self.autostart_available(), "enabled": self.autostart_enabled(),
                          "choice": cfg.get("startWithWindows")},
            "update": checker.to_dict() if checker is not None else {"enabled": False, "available": False},
            "faults": self.faults.to_list(),
            "playing": eng.get("playing"),
        }


# ================================================================== API ====

class PanelAPI:
    """``/api/*`` routes -> :class:`Companion` (callable as the deck's ``api``)."""

    def __init__(self, app: Companion) -> None:
        self.app = app
        self.routes: dict[tuple[str, str], Callable[[dict[str, str], Any], tuple[int, Any]]] = {
            ("GET", "/api/state"): self._state,
            ("POST", "/api/config"): self._config,
            ("POST", "/api/spotify/connect"): self._spotify_connect,
            ("POST", "/api/spotify/disconnect"): self._spotify_disconnect,
            ("GET", "/api/spotify/devices"): self._spotify_devices,
            ("GET", "/api/link"): self._link,
            ("POST", "/api/wow/detect"): self._wow_detect,
            ("POST", "/api/addon/install"): self._addon_install,
            ("POST", "/api/open"): self._open,
        }

    def __call__(self, method: str, path: str, query: dict[str, str], body: Any) -> tuple[int, Any]:
        handler = self.routes.get((method, path))
        if handler is None:
            if any(p == path for (_m, p) in self.routes):
                return 405, {"error": f"{method} is not allowed here"}
            return 404, {"error": "no such API"}
        if method == "POST" and not isinstance(body, dict):
            return 400, {"error": "expected a JSON object"}
        try:
            return handler(query, body)
        except PatchError as exc:
            return 400, {"error": str(exc), "fields": exc.fields}
        except ConfigError as exc:
            return 409, {"error": f"config.json cannot be used: {exc}"}

    # ---- handlers
    def _state(self, _q: dict[str, str], _body: Any) -> tuple[int, Any]:
        return 200, self.app.state()

    def _config(self, _q: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        result = self.app.update_config(body)
        return 200, {**result, "state": self.app.state()}

    def _spotify_connect(self, _q: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        from .providers.spotify_auth import SpotifyAuthError

        cid = body.get("clientId")
        if cid is not None and not isinstance(cid, str):
            raise PatchError("clientId must be text", {"spotify.clientId": "must be text"})
        try:
            return 200, self.app.spotify_connect(cid)
        except SpotifyAuthError as exc:
            return 409, {"error": str(exc), "code": exc.error}

    def _spotify_disconnect(self, _q: dict[str, str], _body: dict[str, Any]) -> tuple[int, Any]:
        return 200, self.app.spotify_disconnect()

    def _spotify_devices(self, _q: dict[str, str], _body: Any) -> tuple[int, Any]:
        return self.app.spotify_devices()

    def _link(self, _q: dict[str, str], _body: Any) -> tuple[int, Any]:
        return 200, self.app.link_check()

    def _wow_detect(self, _q: dict[str, str], _body: dict[str, Any]) -> tuple[int, Any]:
        return 200, self.app.detect_wow()

    def _addon_install(self, _q: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        force = body.get("force", False)
        if not isinstance(force, bool):
            return 400, {"error": "force must be true or false"}
        res = self.app.install_addon(force)
        if res.get("managed"):
            return 409, {"error": res["message"], "managed": True}
        if res.get("refused"):
            return 409, {"error": res["message"], "gameRunning": True}
        if not res.get("ok"):
            return 400, {"error": res["message"]}
        return 200, res

    def _open(self, _q: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        what = body.get("what")
        if not isinstance(what, str) or what not in OPEN_TARGETS:
            return 400, {"error": "what must be one of: " + ", ".join(OPEN_TARGETS)}
        return self.app.open_logs()


__all__ = ["Companion", "PanelAPI", "PatchError", "validate_config_patch", "should_open_panel", "SCHEMA",
           "OPEN_TARGETS", "AUTOSTART_ARGS", "default_opener", "FaultLog", "Fault", "ThreadGuard",
           "component_for_thread", "UpdateChecker", "UpdateInfo", "releases_api_url", "parse_release", "fetch_json",
           "construct_engine", "parse_engine_notice", "is_test_event", "YOUTUBE_EMBED_ERRORS"]
