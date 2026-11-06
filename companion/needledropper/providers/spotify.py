"""Spotify provider: plays needle drops through the Spotify Web API (docs/SPEC.md 5.4).

Requirements (verified Nov 2026): Spotify Premium for player endpoints, a
Development-Mode app registered by the user (see spotify_auth.py), scopes
``user-modify-playback-state user-read-playback-state``.

Flow of one drop::

    snapshot GET /v1/me/player            (204 = nothing playing)
    pick a device GET /v1/me/player/devices  (deviceName > Computer > active > first)
      (none? os.startfile("spotify:") and poll <= 10 s)
    [volume low]  PUT /v1/me/player/volume
    PUT /v1/me/player/play?device_id=..   {"uris": [uri], "position_ms": startMs}
    verify GET /v1/me/player item == uri  (retry once)
    [fade-in thread ramps volume, <= 8 steps]
    ... poll() every >= 2 s: ended at startMs+durationMs / user changed something ...
    stop(): fade out, pause, restore volume, restore the snapshot (context+offset or uris)

All HTTP goes through the injectable ``http`` callable of spotify_auth.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ..models import TEST_DROP, Drop
from .base import NowPlaying, PlayHandle, Provider, ProviderError, ProviderUnavailable
from .spotify_auth import (API_BASE, HttpFunc, SpotifyAuthError, TokenManager, TokenStore, _app_dir, _json,
                           urllib_http)

log = logging.getLogger("needledropper.spotify")

__all__ = [
    "SpotifyProvider", "SpotifyApiError", "PREMIUM_MESSAGE", "pick_device", "normalize_track_uri",
    "choose_best_track", "score_track", "largest_image",
]

PREMIUM_MESSAGE = ("Spotify Premium is required for playback control "
                   "(or your account is not allowlisted in your Spotify app)")
NOT_CONFIGURED_MESSAGE = ("Spotify is not set up: no spotify.clientId in the config. "
                          "Run 'needledropper setup'.")
NOT_LOGGED_IN_MESSAGE = "Not logged in to Spotify. Run 'needledropper spotify-login'."
NO_DEVICE_MESSAGE = ("No Spotify device is available. Open the Spotify app on this PC (signed in to the same "
                     "account) and try again, or set spotify.deviceName to choose a device.")

POLL_INTERVAL_S = 2.0        # poll() hits GET /v1/me/player at most this often
MAX_POLL_FAILURES = 3        # consecutive failed polls -> "error"
WAKE_TIMEOUT_S = 10.0        # after os.startfile("spotify:"), poll the device list this long
WAKE_POLL_S = 1.0
DEVICE_CACHE_TTL_S = 60.0    # device found by prepare() is reused this long if the list omits it
MAX_RETRY_AFTER_S = 5.0      # honour a 429 Retry-After up to this
SERVER_RETRY_S = 0.5
MAX_FADE_STEPS = 8
FADE_STEP_MIN_MS = 250
FADE_FLOOR = 0.1             # fade-in starts at 10% of the target volume
END_LEAD_MS = 2500           # at a natural track end, report "ended" this early (beats autoplay)
NEAR_END_MS = 4000           # item changed this close to a natural end = autoplay, not the user
NATURAL_END_SLACK_MS = 1000
VERIFY_DELAYS_S = (0.5, 1.0)  # waits before each "is our track playing?" check
SEARCH_LIMIT = 10            # Development-mode maximum

_PLAYER_REASONS_RESTRICTION_EXEMPT = {"PREMIUM_REQUIRED"}


# ================================================================ helpers ====

class SpotifyApiError(ProviderError):
    """A Spotify Web API call returned an error status."""

    def __init__(self, message: str, *, status: int, reason: Optional[str] = None,
                 api_message: str = "", retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.api_message = api_message
        self.retry_after = retry_after

    @property
    def is_restriction(self) -> bool:
        """A command-specific refusal (403 'Restriction violated', ALREADY_PAUSED, ...), not an account problem."""
        if self.reason and self.reason not in _PLAYER_REASONS_RESTRICTION_EXEMPT:
            return True
        return "restriction violated" in (self.api_message or "").lower()

    @classmethod
    def from_response(cls, method: str, path: str, status: int, payload: Any,
                      retry_after: Optional[float] = None) -> "SpotifyApiError":
        err = payload.get("error") if isinstance(payload, dict) else None
        reason = None
        api_message = ""
        if isinstance(err, dict):
            reason = err.get("reason") if isinstance(err.get("reason"), str) else None
            api_message = str(err.get("message") or "")
        elif isinstance(err, str):
            api_message = str(payload.get("error_description") or err)
        detail = api_message or {404: "not found", 429: "rate limited"}.get(status, "error")
        if reason:
            detail += f" [{reason}]"
        return cls(f"Spotify {method} {path} failed: HTTP {status} {detail}", status=status, reason=reason,
                   api_message=api_message, retry_after=retry_after)


@dataclass
class _Resp:
    status: int
    data: Any
    headers: dict[str, str]


_TRACK_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


def normalize_track_uri(value: Any) -> Optional[str]:
    """``spotify:track:<id>`` from a track uri, an open.spotify.com track link or a bare id."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    tid: Optional[str] = None
    if v.lower().startswith("spotify:"):
        parts = v.split(":")
        if len(parts) >= 3 and parts[1].lower() == "track":
            tid = parts[2]
    elif "open.spotify.com" in v.lower():
        segs = [s for s in urllib.parse.urlsplit(v if "://" in v else "https://" + v).path.split("/") if s]
        for i, seg in enumerate(segs[:-1]):
            if seg.lower() == "track":
                tid = segs[i + 1]
                break
    else:
        tid = v
    if tid and _TRACK_ID_RE.match(tid):
        return f"spotify:track:{tid}"
    return None


def _track_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1]


def largest_image(images: Any) -> Optional[str]:
    """URL of the largest image in a Spotify ``images`` array."""
    best_url, best_area = None, -1
    for img in images or []:
        if not isinstance(img, dict) or not img.get("url"):
            continue
        area = int(img.get("width") or 0) * int(img.get("height") or 0)
        if area > best_area:
            best_url, best_area = img["url"], area
    return best_url


def pick_device(devices: Iterable[dict], preferred_name: str = "") -> Optional[dict]:
    """Choose a device: configured name > type Computer > active > first non-restricted."""
    usable = [d for d in devices or [] if isinstance(d, dict) and d.get("id") and not d.get("is_restricted")]
    if not usable:
        return None
    want = (preferred_name or "").strip().casefold()
    if want:
        for d in usable:
            if str(d.get("name") or "").casefold() == want:
                return d
        for d in usable:
            if want in str(d.get("name") or "").casefold():
                return d
        log.info("Spotify device %r not found (have: %s); choosing another one", preferred_name,
                 ", ".join(str(d.get("name")) for d in usable))
    computers = [d for d in usable if str(d.get("type") or "").casefold() == "computer"]
    if computers:
        return next((d for d in computers if d.get("is_active")), computers[0])
    return next((d for d in usable if d.get("is_active")), usable[0])


def _item_matches(state: Optional[dict], uri: str) -> bool:
    item = (state or {}).get("item") if isinstance(state, dict) else None
    if not isinstance(item, dict):
        return False
    if item.get("uri") == uri:
        return True
    linked = item.get("linked_from")
    if isinstance(linked, dict) and linked.get("uri") == uri:
        return True
    return bool(item.get("id")) and item.get("id") == _track_id(uri)


# ------------------------------------------------------- track matching ----

_FEAT_PAREN_RE = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]", re.I)
_FEAT_TAIL_RE = re.compile(r"\s(?:feat\.?|ft\.?|featuring)\s.*$", re.I)
_VERSION_PAREN_RE = re.compile(
    r"[\(\[][^\)\]]*\b(?:remaster(?:ed)?|version|edit|mono|stereo|deluxe|anniversary|bonus|single|explicit|"
    r"original|mix|remix|live|demo|acoustic|instrumental)\b[^\)\]]*[\)\]]", re.I)
_DASH_SUFFIX_RE = re.compile(r"\s[-–—]\s.*$")
_APOS_RE = re.compile(r"['’‘`\"“”]")
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")

_STRONG_VARIANTS = re.compile(r"\b(?:karaoke|tribute|in the style of|made famous|originally performed|"
                              r"cover|8[- ]?bit|lullaby|music box|piano version)\b", re.I)
_MEDIUM_VARIANTS = re.compile(r"\b(?:live|remix|rmx|sped up|slowed|reverb|nightcore|acoustic|demo|"
                              r"instrumental|unplugged|mashup|bootleg)\b", re.I)
_ALBUM_LIVE = re.compile(r"\blive (?:at|from|in|on)\b|\(live\)|\blive$|\bunplugged\b", re.I)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def normalize_title(s: str) -> str:
    """Comparable track title: no feat./remaster/version tags, punctuation or case."""
    s = _fold(s)
    s = _FEAT_PAREN_RE.sub(" ", s)
    s = _VERSION_PAREN_RE.sub(" ", s)
    s = _DASH_SUFFIX_RE.sub("", s)
    s = _FEAT_TAIL_RE.sub("", s)
    s = s.replace("&", " and ")
    s = _APOS_RE.sub("", s)
    return " ".join(_NON_ALNUM_RE.sub(" ", s).split())


def normalize_artist(s: str) -> str:
    s = _fold(s).replace("&", " and ")
    s = _FEAT_TAIL_RE.sub("", s)
    s = _APOS_RE.sub("", s)
    s = " ".join(_NON_ALNUM_RE.sub(" ", s).split())
    return s[4:] if s.startswith("the ") else s


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _variant_penalty(name: str, album: str, query_title: str) -> float:
    q = query_title.lower()
    penalty = 0.0
    for m in _STRONG_VARIANTS.finditer(name):
        if m.group(0).lower() not in q:
            penalty += 60
            break
    for m in _MEDIUM_VARIANTS.finditer(name):
        if m.group(0).lower() not in q:
            penalty += 40
            break
    if _STRONG_VARIANTS.search(album) and not _STRONG_VARIANTS.search(q):
        penalty += 50
    elif _ALBUM_LIVE.search(album) and "live" not in q:
        penalty += 20
    return penalty


def score_track(track: dict, title: str, artist: str) -> tuple[float, float, float]:
    """(total, title_score, artist_score) of a search result against the wanted title/artist."""
    q_title, q_artist = normalize_title(title), normalize_artist(artist)
    name = str(track.get("name") or "")
    t_title = normalize_title(name)
    artists = [normalize_artist(str(a.get("name") or "")) for a in track.get("artists") or [] if isinstance(a, dict)]
    artists = [a for a in artists if a]

    if q_title and t_title == q_title:
        ts = 100.0
    elif q_title and t_title and (q_title in t_title or t_title in q_title):
        ts = 70.0
    else:
        ts = 60.0 * _jaccard(q_title, t_title)

    joined = " and ".join(artists)
    if artists and artists[0] == q_artist:
        ars = 60.0
    elif q_artist and (q_artist in artists or joined == q_artist):
        ars = 50.0
    elif q_artist and any(len(a) >= 3 and len(q_artist) >= 3 and (q_artist in a or a in q_artist) for a in artists):
        ars = 30.0
    else:
        ars = 30.0 * max([_jaccard(q_artist, a) for a in artists] + [_jaccard(q_artist, joined), 0.0])

    album = str((track.get("album") or {}).get("name") or "") if isinstance(track.get("album"), dict) else ""
    total = ts + ars - _variant_penalty(name, album, title)
    if track.get("is_playable") is False:
        total -= 80
    return total, ts, ars


def choose_best_track(items: Iterable[Any], title: str, artist: str) -> Optional[dict]:
    """Best confident match from search results, or None (a miss beats the wrong song)."""
    best: Optional[dict] = None
    best_score = -math.inf
    for track in items or []:
        if not isinstance(track, dict) or not track.get("uri"):
            continue
        total, ts, ars = score_track(track, title, artist)
        if ts < 60 or ars < 25 or total < 90:
            continue
        if total > best_score:  # strict: earlier (Spotify-ranked) results win ties
            best, best_score = track, total
    return best


def _artists_str(track: dict) -> str:
    return ", ".join(str(a.get("name")) for a in track.get("artists") or [] if isinstance(a, dict) and a.get("name"))


def _clamp_volume(v: Any) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


def is_placeholder_test_drop(drop: Drop) -> bool:
    """True for the built-in ``test`` drop (``models.TEST_DROP``) when the user has not
    configured a real test track (config ``testDrop``): its title/artist name no song,
    so searching Spotify for it would only cost API calls."""
    return drop.kind == "test" and drop.title == TEST_DROP.title and drop.artist == TEST_DROP.artist


# ============================================================ resolve cache ==

class _ResolveCache:
    """``<app_dir>\\cache\\resolved.json``: {dropId: {spotify, durationMs, artUrl, matchedTitle, matchedArtist, ...}}."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}
        self._stamp: Optional[tuple[int, int]] = None

    def _file_stamp(self) -> Optional[tuple[int, int]]:
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _reload(self) -> None:
        stamp = self._file_stamp()
        if stamp == self._stamp:
            return
        self._stamp = stamp
        if stamp is None:
            self._data = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable Spotify resolve cache %s: %s", self.path, exc)
            self._data = {}

    def get(self, drop_id: str) -> Optional[dict]:
        if not drop_id:
            return None
        with self._lock:
            self._reload()
            entry = self._data.get(drop_id)
            return dict(entry) if entry else None

    def put(self, drop_id: str, entry: dict) -> None:
        if not drop_id:
            return
        with self._lock:
            self._reload()
            self._data[drop_id] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="resolved.", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, indent=2, sort_keys=True, ensure_ascii=False)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._stamp = self._file_stamp()


def _entry_matches(entry: dict, drop: Drop, uri: Optional[str]) -> bool:
    if not entry.get("spotify"):
        return False
    if uri:
        return entry.get("spotify") == uri
    qt, qa = entry.get("queryTitle"), entry.get("queryArtist")
    if qt is not None and normalize_title(str(qt)) != normalize_title(drop.title):
        return False
    if qa is not None and normalize_artist(str(qa)) != normalize_artist(drop.artist):
        return False
    return True


def _apply_entry(drop: Drop, entry: dict) -> Drop:
    changes: dict[str, Any] = {}
    uri = normalize_track_uri(drop.spotify)
    if not uri:
        uri = normalize_track_uri(entry.get("spotify"))
    if uri and uri != drop.spotify:
        changes["spotify"] = uri
    try:
        track_ms = int(entry.get("trackDurationMs") or 0)
        play_ms = int(entry.get("durationMs") or 0)
    except (TypeError, ValueError):
        track_ms = play_ms = 0
    if not drop.durationMs:
        if track_ms:  # recompute: the pack's startMs may have changed since the entry was written
            play_ms = max(0, track_ms - int(drop.startMs or 0))
        if play_ms:
            changes["durationMs"] = play_ms
    extra = dict(drop.extra)
    for key in ("artUrl", "matchedTitle", "matchedArtist"):
        if entry.get(key):
            extra[key] = entry[key]
    if track_ms:
        extra["trackDurationMs"] = track_ms
    return dataclasses.replace(drop, extra=extra, **changes)


# =============================================================== sessions ===

@dataclass(eq=False)
class _Session:
    """Mutable per-drop state shared by the engine thread and the fade thread."""

    uri: str
    device_id: str
    device_name: str
    start_ms: int
    snapshot: Optional[dict]
    orig_volume: Optional[int]
    volume_device: str
    volume_ok: bool
    target_volume: Optional[int] = None
    last_set_volume: Optional[int] = None
    end_ms: Optional[int] = None
    natural_end: bool = False
    last_pos: int = 0
    last_pos_t: float = 0.0
    last_playing: bool = True
    last_poll: float = -math.inf
    failures: int = 0
    outcome: Optional[str] = None  # "end" | "user" | "error"
    stopped: bool = False
    fade_cancel: threading.Event = field(default_factory=threading.Event)
    fade_thread: Optional[threading.Thread] = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def estimate_pos(self, now: float) -> int:
        with self.lock:
            if not self.last_playing:
                return self.last_pos
            return int(self.last_pos + max(0.0, now - self.last_pos_t) * 1000)

    def near_natural_end(self, now: float) -> bool:
        return (self.natural_end and self.end_ms is not None
                and self.estimate_pos(now) >= self.end_ms - NEAR_END_MS)


# =============================================================== provider ===

class SpotifyProvider(Provider):
    """Spotify Web API provider (Premium). Thread-safe for engine + fade threads.

    ``config_section`` is the ``spotify`` config dict (clientId, redirectPort,
    deviceName, fade, restorePrevious, volume). ``paths`` needs ``app_dir``.
    Injectable for tests: ``http``, ``startfile``, ``sleep``, ``clock``
    (monotonic seconds), ``now`` (wall-clock seconds for token expiry),
    ``token_store``.
    """

    name = "spotify"

    def __init__(self, config_section: Optional[dict[str, Any]], paths: Any, *,
                 http: Optional[HttpFunc] = None,
                 startfile: Optional[Callable[[str], Any]] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], float] = time.time,
                 token_store: Optional[TokenStore] = None) -> None:
        cfg = dict(config_section or {})
        self.config = cfg
        self.paths = paths
        self._client_id = str(cfg.get("clientId") or "").strip()
        self._device_name = str(cfg.get("deviceName") or "").strip()
        self._fade = bool(cfg.get("fade", True))
        self._restore_previous = bool(cfg.get("restorePrevious", True))
        self._fixed_volume = _clamp_volume(cfg.get("volume"))
        self._http: HttpFunc = http or urllib_http
        self._startfile = startfile if startfile is not None else getattr(os, "startfile", None)
        self._sleep = sleep
        self._clock = clock
        app_dir = _app_dir(paths)
        self._store = token_store or TokenStore(app_dir)
        self._tokens = TokenManager(self._client_id, self._store, http=self._http, now=now)
        cache_path = getattr(paths, "resolved_cache", None)
        self._cache = _ResolveCache(Path(cache_path) if cache_path else app_dir / "cache" / "resolved.json")
        self._lock = threading.RLock()
        self._unavailable: Optional[str] = None
        self._device_cache: Optional[tuple[dict, float]] = None
        self._no_match: set[tuple[str, str]] = set()
        self._sessions: list[_Session] = []
        self._wake_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ status --

    def unavailable_reason(self) -> Optional[str]:
        """Why :meth:`available` is False (user-facing), or None when available."""
        if not self._client_id:
            return NOT_CONFIGURED_MESSAGE
        with self._lock:
            if self._unavailable:
                return self._unavailable
        try:
            self._tokens._tokens()
        except SpotifyAuthError as exc:
            return str(exc)
        return None

    def available(self) -> bool:
        if not self._client_id:
            return False
        with self._lock:
            if self._unavailable:
                return False
        return self._tokens.usable()

    def can_play(self, drop: Drop) -> bool:
        if not self.available():
            return False
        if normalize_track_uri(drop.spotify):
            return True
        if is_placeholder_test_drop(drop):
            return False  # the built-in test card names no real song: never search Spotify for it
        return bool(drop.title and drop.artist)

    def status(self) -> dict[str, Any]:
        reason = self.unavailable_reason()
        return {"ok": reason is None, "detail": reason or "ready", "clientId": bool(self._client_id),
                "loggedIn": self._store.has_refresh_token(), "tokenFile": str(self._store.path)}

    def _mark_unavailable(self, message: str) -> None:
        with self._lock:
            if not self._unavailable:
                log.warning("Spotify disabled for this session: %s", message)
            self._unavailable = message

    # -------------------------------------------------------------- HTTP --

    def _token(self) -> str:
        with self._lock:
            reason = self._unavailable
        if reason:
            raise ProviderUnavailable(reason)
        if not self._client_id:
            raise ProviderUnavailable(NOT_CONFIGURED_MESSAGE)
        try:
            return self._tokens.access_token()
        except SpotifyAuthError as exc:
            raise self._auth_failure(exc) from exc

    def _auth_failure(self, exc: SpotifyAuthError) -> ProviderError:
        if exc.error in ("invalid_grant", "invalid_client"):
            self._mark_unavailable(str(exc))
        if exc.revoked:
            return ProviderUnavailable(str(exc))
        return ProviderError(str(exc))

    def _call(self, method: str, path: str, *, params: Optional[dict[str, Any]] = None,
              body: Any = None, tolerant: bool = False) -> _Resp:
        """One API request with the SPEC 5.4 error policy.

        401 -> refresh once and retry; 429 -> honour Retry-After (<= 5 s) once;
        5xx -> retry once; 403 -> ProviderUnavailable + session-disable unless
        ``tolerant`` or the refusal is command-specific (``is_restriction``);
        other errors -> SpotifyApiError; transport failures -> ProviderError.
        """
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        refreshed = limited = server_retry = False
        while True:
            token = self._token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            try:
                status, rheaders, raw = self._http(method, url, headers, data)
            except Exception as exc:  # noqa: BLE001 - any transport failure is a ProviderError
                raise ProviderError(f"Could not reach Spotify ({exc})") from exc
            rh = {str(k).lower(): str(v) for k, v in (rheaders or {}).items()}
            payload = _json(raw or b"")
            log.debug("Spotify %s %s -> %s", method, path, status)
            if 200 <= status < 300:
                return _Resp(status, payload, rh)
            if status == 401 and not refreshed:
                refreshed = True
                try:
                    self._tokens.force_refresh(token)
                except SpotifyAuthError as exc:
                    raise self._auth_failure(exc) from exc
                continue
            if status == 429:
                wait = _retry_after(rh)
                if not limited and wait <= MAX_RETRY_AFTER_S:
                    limited = True
                    log.info("Spotify rate limit: waiting %.1f s", wait)
                    self._sleep(wait)
                    continue
                raise SpotifyApiError(f"Spotify is rate-limiting requests (retry after {wait:.0f} s)",
                                      status=429, retry_after=wait)
            if status in (500, 502, 503, 504) and not server_retry:
                server_retry = True
                self._sleep(SERVER_RETRY_S)
                continue
            err = SpotifyApiError.from_response(method, path, status, payload)
            if status == 401:
                raise SpotifyApiError("Spotify rejected the access token even after refreshing it. "
                                      "Run 'needledropper spotify-login'.", status=401)
            if status == 403 and not tolerant and not err.is_restriction:
                log.warning("%s", err)
                self._mark_unavailable(PREMIUM_MESSAGE)
                raise ProviderUnavailable(PREMIUM_MESSAGE) from err
            raise err

    def _get_player(self) -> Optional[dict]:
        r = self._call("GET", "/me/player", params={"additional_types": "track,episode"})
        if r.status == 204 or not isinstance(r.data, dict):
            return None
        return r.data

    def _get_devices(self) -> list[dict]:
        r = self._call("GET", "/me/player/devices")
        devices = (r.data or {}).get("devices") if isinstance(r.data, dict) else None
        return [d for d in devices or [] if isinstance(d, dict)]

    def _get_track(self, uri: str) -> dict:
        r = self._call("GET", f"/tracks/{_track_id(uri)}", params={"market": "from_token"})
        if not isinstance(r.data, dict):
            raise ProviderError(f"Spotify returned no data for {uri}")
        return r.data

    # ----------------------------------------------------------- devices --

    def _remember_device(self, device: dict) -> None:
        with self._lock:
            self._device_cache = (dict(device), self._clock())

    def _cached_device(self) -> Optional[dict]:
        with self._lock:
            if self._device_cache and self._clock() - self._device_cache[1] <= DEVICE_CACHE_TTL_S:
                return dict(self._device_cache[0])
            return None

    def _forget_device(self) -> None:
        with self._lock:
            self._device_cache = None

    def _wake_device(self) -> Optional[dict]:
        """Launch/focus the Spotify app (``spotify:``) and poll the device list for up to 10 s."""
        if self._startfile is not None:
            try:
                log.info("No Spotify device found; starting the Spotify app")
                self._startfile("spotify:")
            except Exception as exc:  # noqa: BLE001 - not installed, no handler, ...
                log.info("Could not launch the Spotify app (%s)", exc)
        else:
            log.info("Cannot launch the Spotify app on this OS; waiting for a device to appear")
        deadline = self._clock() + WAKE_TIMEOUT_S
        while self._clock() < deadline:
            self._sleep(WAKE_POLL_S)
            try:
                device = pick_device(self._get_devices(), self._device_name)
            except ProviderUnavailable:
                raise
            except ProviderError as exc:
                log.debug("device poll failed: %s", exc)
                continue
            if device:
                self._remember_device(device)
                return device
        return None

    def _acquire_device(self) -> dict:
        """Fresh device list -> pick; else a recently seen device; else wake Spotify."""
        device = pick_device(self._get_devices(), self._device_name)
        if device is None:
            self._join_background_wake()  # a wake started on ARM may be about to find one
            device = self._cached_device()
            if device is not None:
                log.info("Spotify device list is empty; trying recently seen device %r", device.get("name"))
                return device
            device = self._wake_device()
        if device is None:
            raise ProviderError(NO_DEVICE_MESSAGE)
        self._remember_device(device)
        return device

    # ------------------------------------------------------------ volume --

    def _put_volume(self, device_id: str, volume: int) -> bool:
        try:
            self._call("PUT", "/me/player/volume",
                       params={"volume_percent": int(volume), "device_id": device_id}, tolerant=True)
            return True
        except SpotifyApiError as exc:
            if exc.status in (403, 404):
                log.info("Spotify volume control not available on this device (%s); no fades", exc.reason or exc.status)
            else:
                log.info("Spotify volume change failed: %s", exc)
            return False
        except ProviderError as exc:
            log.info("Spotify volume change failed: %s", exc)
            return False

    def _set_volume(self, s: _Session, volume: int) -> bool:
        with s.lock:
            if not s.volume_ok:
                return False
            device = s.volume_device
        ok = self._put_volume(device, volume)
        with s.lock:
            if ok:
                s.last_set_volume = int(volume)
            else:
                s.volume_ok = False
        return ok

    def _ramp(self, s: _Session, start: int, end: int, duration_ms: int,
              wait: Callable[[float], bool]) -> bool:
        """Move the volume start -> end in <= 8 steps. ``wait`` returns True to cancel."""
        steps = min(MAX_FADE_STEPS, max(1, int(duration_ms) // FADE_STEP_MIN_MS))
        interval = max(0.0, duration_ms / 1000.0 / steps)
        for i in range(1, steps + 1):
            if wait(interval):
                return False
            vol = int(round(start + (end - start) * i / steps))
            if not self._set_volume(s, vol):
                return False
        return True

    def _fade_in_worker(self, s: _Session, start: int, target: int, duration_ms: int) -> None:
        try:
            done = self._ramp(s, start, target, duration_ms, s.fade_cancel.wait)
            if not done and not s.fade_cancel.is_set():
                # Volume control broke mid-ramp: don't leave the drop stuck quiet.
                if self._put_volume(s.volume_device, target):
                    with s.lock:
                        s.last_set_volume = target
        except Exception:  # noqa: BLE001 - background thread must never die loudly
            log.exception("Spotify fade-in failed")

    def _cancel_fade(self, s: _Session) -> None:
        s.fade_cancel.set()
        t = s.fade_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)

    def _restore_volume(self, s: _Session, state: Optional[dict] = None, *, only_if_untouched: bool = False) -> None:
        """Put our device's volume back to what it was before the drop."""
        with s.lock:
            last, orig, device = s.last_set_volume, s.orig_volume, s.volume_device
        if last is None or orig is None:
            return
        if only_if_untouched:
            dev = (state or {}).get("device") if isinstance(state, dict) else None
            current = dev.get("volume_percent") if isinstance(dev, dict) and dev.get("id") == device else None
            if current is None or abs(int(current) - last) > 1:
                log.info("Spotify volume was changed during the drop; leaving it as it is")
                return
        if last != orig and self._put_volume(device, orig):
            with s.lock:
                s.last_set_volume = orig

    # -------------------------------------------------------- resolution --

    def _resolved(self, drop: Drop) -> Drop:
        """Drop with a usable spotify uri when possible (cache first; search only when needed)."""
        uri = normalize_track_uri(drop.spotify)
        entry = self._cache.get(drop.id) if drop.id else None
        if entry and _entry_matches(entry, drop, uri):
            return _apply_entry(drop, entry)
        if uri:
            return drop if uri == drop.spotify else dataclasses.replace(drop, spotify=uri)
        return self.resolve_drop(drop)

    def resolve_drop(self, drop: Drop, *, force: bool = False) -> Drop:
        """Fill ``spotify`` uri, ``durationMs`` (if 0) and ``extra['artUrl']`` for a drop.

        Searches ``track:"<title>" artist:"<artist>"`` (limit 10), falling back to a
        plain query, and keeps only a confident normalized match. Results are cached
        in ``<app_dir>\\cache\\resolved.json``. A drop that already has a uri gets
        its duration/art from ``GET /v1/tracks/{id}``. Returns the drop unchanged
        when nothing matches. Raises ProviderError/ProviderUnavailable on API failure.
        ``durationMs`` (in the drop and the cache) is ``track length - startMs``, i.e. the
        length to play; the cache also records ``trackDurationMs`` (full track length).
        """
        uri = normalize_track_uri(drop.spotify)
        if not force and drop.id:
            entry = self._cache.get(drop.id)
            if entry and _entry_matches(entry, drop, uri):
                return _apply_entry(drop, entry)
        if uri:
            track = self._get_track(uri)
        else:
            if not (drop.title and drop.artist):
                return drop
            key = (normalize_title(drop.title), normalize_artist(drop.artist))
            if not force and key in self._no_match:
                return drop
            track = self._search_best(drop.title, drop.artist)
            if track is None:
                self._no_match.add(key)
                log.warning("No confident Spotify match for %r by %r; set \"spotify\" in the pack to pick one",
                            drop.title, drop.artist)
                return drop
            uri = normalize_track_uri(track.get("uri")) or str(track.get("uri"))
        track_ms = int(track.get("duration_ms") or 0)
        entry = {
            "spotify": uri,
            # length to play from the drop's startMs (what Drop.durationMs means); the full
            # track length is kept separately so a changed startMs can be recomputed
            "durationMs": max(0, track_ms - int(drop.startMs or 0)) if track_ms else 0,
            "trackDurationMs": track_ms,
            "artUrl": largest_image((track.get("album") or {}).get("images")),
            "matchedTitle": track.get("name") or "",
            "matchedArtist": _artists_str(track),
            "queryTitle": drop.title,
            "queryArtist": drop.artist,
            "resolvedAt": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if drop.id:
            try:
                self._cache.put(drop.id, entry)
            except OSError as exc:
                log.warning("Could not write the Spotify resolve cache: %s", exc)
        log.info("Spotify: %s -> %s (%s - %s)", drop.id or drop.title, uri, entry["matchedArtist"],
                 entry["matchedTitle"])
        return _apply_entry(drop, entry)

    def _search_best(self, title: str, artist: str) -> Optional[dict]:
        def q(s: str) -> str:
            return s.replace('"', " ").strip()

        queries = [f'track:"{q(title)}" artist:"{q(artist)}"', f"{q(title)} {q(artist)}"]
        for query in queries:
            r = self._call("GET", "/search", params={"q": query, "type": "track", "limit": SEARCH_LIMIT,
                                                     "market": "from_token"})
            tracks = (r.data or {}).get("tracks") if isinstance(r.data, dict) else None
            items = (tracks or {}).get("items") if isinstance(tracks, dict) else None
            best = choose_best_track(items or [], title, artist)
            if best is not None:
                return best
        return None

    # ----------------------------------------------------------- prepare --

    def prepare(self, drop: Drop) -> None:
        """ARM prewarm: token, track resolution, device. Never raises.

        If no device is listed, Spotify is woken on a background thread so the
        engine thread is not blocked for the up-to-10 s device poll; play() waits
        for that wake instead of starting a second one.
        """
        try:
            if not self.can_play(drop):
                return
            self._token()
            self._resolved(drop)
            device = pick_device(self._get_devices(), self._device_name)
            if device is not None:
                self._remember_device(device)
            elif self._cached_device() is None:
                self._start_background_wake()
        except Exception as exc:  # noqa: BLE001 - best effort by contract
            log.info("Spotify prewarm for %s failed: %s", drop.id, exc)

    def _start_background_wake(self) -> None:
        with self._lock:
            if self._wake_thread is not None and self._wake_thread.is_alive():
                return

            def run() -> None:
                try:
                    self._wake_device()
                except Exception as exc:  # noqa: BLE001 - background best effort
                    log.info("Spotify wake failed: %s", exc)

            self._wake_thread = threading.Thread(target=run, name="needledropper-spotify-wake", daemon=True)
            self._wake_thread.start()

    def _join_background_wake(self) -> None:
        with self._lock:
            t = self._wake_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=WAKE_TIMEOUT_S + 5.0)

    # -------------------------------------------------------------- play --

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        if not self.available():
            raise ProviderUnavailable(self.unavailable_reason() or NOT_LOGGED_IN_MESSAGE)
        drop = self._resolved(drop)
        uri = normalize_track_uri(drop.spotify)
        if not uri:
            raise ProviderError(f"No Spotify track found for {drop.title!r} by {drop.artist!r}")
        start_ms = max(0, int(drop.startMs or 0))

        try:
            snapshot = self._get_player()
        except ProviderUnavailable:
            raise
        except ProviderError as exc:
            log.warning("Could not read current Spotify playback (%s); it will not be restored", exc)
            snapshot = None

        device = self._acquire_device()
        s = self._new_session(uri, device, start_ms, snapshot)
        target, fade = self._plan_volume(s, device, drop, fade_in_ms)
        low = int(round(target * FADE_FLOOR)) if fade and target is not None else None
        preset = False
        try:
            if fade and low is not None:
                preset = self._set_volume(s, low)
                if not preset:
                    with s.lock:
                        s.volume_ok = True  # the device may simply not be active yet; retry after play
            elif target is not None and target != s.orig_volume:
                preset = self._set_volume(s, target)

            used = self._start_playback(device, uri, start_ms)
            if used.get("id") != device.get("id"):
                self._rebind_device(s, used)
                target, fade = self._plan_volume(s, used, drop, fade_in_ms)
                low = int(round(target * FADE_FLOOR)) if fade and target is not None else None
                preset = False
            if not preset:
                if fade and low is not None:
                    fade = self._set_volume(s, low)
                elif target is not None and target != s.orig_volume and s.volume_ok:
                    self._set_volume(s, target)

            state = self._verify_started(s, uri, start_ms)
        except BaseException:
            self._restore_volume(s)
            raise

        self._finish_session(s, drop, state)
        np = self._now_playing(drop, uri, state)
        handle = PlayHandle(provider=self.name, drop=drop, now_playing=np,
                            data={"session": s, "uri": uri, "deviceId": s.device_id, "endMs": s.end_ms})
        with self._lock:
            self._sessions.append(s)
        if fade and low is not None and target is not None and fade_in_ms > 0:
            with s.lock:
                s.target_volume = target
            t = threading.Thread(target=self._fade_in_worker, args=(s, low, target, int(fade_in_ms)),
                                 name="needledropper-spotify-fade", daemon=True)
            s.fade_thread = t
            t.start()
        log.info("Spotify: playing %s on %r from %d ms", uri, s.device_name, start_ms)
        return handle

    def now_playing(self, handle: PlayHandle) -> Optional[NowPlaying]:
        return handle.now_playing

    def _new_session(self, uri: str, device: dict, start_ms: int, snapshot: Optional[dict]) -> _Session:
        orig = _clamp_volume(device.get("volume_percent"))
        snap_dev = (snapshot or {}).get("device") if isinstance(snapshot, dict) else None
        if orig is None and isinstance(snap_dev, dict) and snap_dev.get("id") == device.get("id"):
            orig = _clamp_volume(snap_dev.get("volume_percent"))
        return _Session(uri=uri, device_id=str(device["id"]), device_name=str(device.get("name") or ""),
                        start_ms=start_ms, snapshot=snapshot, orig_volume=orig,
                        volume_device=str(device["id"]),
                        volume_ok=bool(device.get("supports_volume", True)) and orig is not None,
                        last_pos=start_ms, last_pos_t=self._clock())

    def _plan_volume(self, s: _Session, device: dict, drop: Drop, fade_in_ms: int) -> tuple[Optional[int], bool]:
        """(target volume or None to leave it alone, whether to fade in)."""
        base = self._fixed_volume if self._fixed_volume is not None else s.orig_volume
        if base is None or not s.volume_ok:
            return None, False
        target = base
        dv = _clamp_volume(drop.volume)
        if dv is not None:
            target = int(round(base * dv / 100.0))
        fade = self._fade and fade_in_ms > 0 and target > 0
        return target, fade

    def _rebind_device(self, s: _Session, device: dict) -> None:
        """Playback ended up on another device than planned: move volume bookkeeping there."""
        self._restore_volume(s)
        orig = _clamp_volume(device.get("volume_percent"))
        with s.lock:
            s.device_id = s.volume_device = str(device["id"])
            s.device_name = str(device.get("name") or "")
            s.orig_volume = orig
            s.last_set_volume = None
            s.volume_ok = bool(device.get("supports_volume", True)) and orig is not None

    def _play_command(self, device_id: str, uri: str, start_ms: int) -> None:
        self._call("PUT", "/me/player/play", params={"device_id": device_id},
                   body={"uris": [uri], "position_ms": int(start_ms)})

    def _start_playback(self, device: dict, uri: str, start_ms: int) -> dict:
        """PUT play; on 404/NO_ACTIVE_DEVICE refetch devices (or wake Spotify) and retry once."""
        try:
            self._play_command(str(device["id"]), uri, start_ms)
            return device
        except SpotifyApiError as exc:
            if exc.status != 404:
                raise
            log.info("Spotify device %r not reachable (%s); looking again", device.get("name"), exc.reason or 404)
        self._forget_device()
        retry = pick_device(self._get_devices(), self._device_name)
        if retry is None or retry.get("id") == device.get("id"):
            retry = self._wake_device() or retry
        if retry is None:
            raise ProviderError(NO_DEVICE_MESSAGE)
        try:
            self._play_command(str(retry["id"]), uri, start_ms)
        except SpotifyApiError as exc:
            if exc.status == 404:
                raise ProviderError(NO_DEVICE_MESSAGE) from exc
            raise
        self._remember_device(retry)
        return retry

    def _verify_started(self, s: _Session, uri: str, start_ms: int) -> Optional[dict]:
        """Confirm GET /v1/me/player shows our track; resend play once. Returns the state seen."""
        saw_state = False
        last_state: Optional[dict] = None
        for attempt in range(2):
            for delay in VERIFY_DELAYS_S:
                self._sleep(delay)
                try:
                    state = self._get_player()
                except ProviderUnavailable:
                    raise
                except ProviderError as exc:
                    log.debug("verify read failed: %s", exc)
                    continue
                saw_state = True
                last_state = state
                if _item_matches(state, uri):
                    return state
            if attempt == 0:
                log.info("Spotify has not switched to %s yet; sending play again", uri)
                self._play_command(s.device_id, uri, start_ms)
        if not saw_state:
            log.info("Could not confirm Spotify playback (player state unreadable); assuming it started")
            return None
        other = ((last_state or {}).get("item") or {}).get("uri") if isinstance(last_state, dict) else None
        raise ProviderError(f"Spotify accepted the play command but is playing {other or 'nothing'} "
                            f"instead of {uri}")

    def _finish_session(self, s: _Session, drop: Drop, state: Optional[dict]) -> None:
        item = (state or {}).get("item") if isinstance(state, dict) else None
        track_ms = int((item or {}).get("duration_ms") or 0) if isinstance(item, dict) else 0
        if not track_ms:
            track_ms = int(drop.extra.get("trackDurationMs") or 0)
        if not track_ms and not drop.durationMs:
            try:
                track_ms = int(self._get_track(s.uri).get("duration_ms") or 0)
            except ProviderError as exc:
                log.debug("track length lookup failed: %s", exc)
        start = s.start_ms
        if drop.durationMs and drop.durationMs > 0:
            end: Optional[int] = start + int(drop.durationMs)
            if track_ms:
                end = min(end, track_ms)
            natural = bool(track_ms) and end >= track_ms - NATURAL_END_SLACK_MS
        elif track_ms:
            end, natural = track_ms, True
        else:
            end, natural = None, False
        now = self._clock()
        with s.lock:
            s.end_ms, s.natural_end = end, natural
            if isinstance(state, dict):
                s.last_pos = int(state.get("progress_ms") or start)
                s.last_playing = bool(state.get("is_playing", True))
                dev = state.get("device")
                if isinstance(dev, dict) and dev.get("id"):
                    s.device_id = str(dev["id"])
            s.last_pos_t = now
            s.last_poll = now

    def _now_playing(self, drop: Drop, uri: str, state: Optional[dict]) -> NowPlaying:
        item = (state or {}).get("item") if isinstance(state, dict) else None
        item = item if isinstance(item, dict) else {}
        art = largest_image((item.get("album") or {}).get("images")) or drop.extra.get("artUrl")
        link = (item.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/track/{_track_id(uri)}"
        return NowPlaying(title=drop.title or str(item.get("name") or ""),
                          artist=drop.artist or _artists_str(item),
                          label=drop.display_label, art_url=art, link=link, attribution="Spotify",
                          provider=self.name)

    # -------------------------------------------------------------- poll --

    @staticmethod
    def _session(handle: PlayHandle) -> Optional[_Session]:
        s = handle.data.get("session") if isinstance(handle.data, dict) else None
        return s if isinstance(s, _Session) else None

    def _assess(self, s: _Session, state: Optional[dict], now: float) -> str:
        """'playing' (ours, still going), 'end' (our end point / natural end / autoplay), 'user'."""
        near = s.near_natural_end(now)
        if not isinstance(state, dict) or not isinstance(state.get("item"), dict):
            return "end" if near else "user"
        if not _item_matches(state, s.uri):
            return "end" if near else "user"
        progress = int(state.get("progress_ms") or 0)
        playing = bool(state.get("is_playing"))
        with s.lock:
            dev = state.get("device")
            if isinstance(dev, dict) and dev.get("id") and dev["id"] != s.device_id:
                log.info("Spotify playback moved to %r; following it", dev.get("name"))
                s.device_id = str(dev["id"])
            s.last_pos, s.last_pos_t, s.last_playing = progress, now, playing
        if s.end_ms is not None:
            lead = END_LEAD_MS if s.natural_end else 0
            if progress >= s.end_ms - lead:
                return "end"
        if not playing:
            return "end" if near else "user"
        return "playing"

    def poll(self, handle: PlayHandle) -> str:
        s = self._session(handle)
        if s is None:
            return "error"
        now = self._clock()
        with s.lock:
            if s.stopped:
                return "ended"
            if s.outcome:
                return "error" if s.outcome == "error" else "ended"
            if now - s.last_poll < POLL_INTERVAL_S:
                return "playing"
            s.last_poll = now
        try:
            state = self._get_player()
        except ProviderUnavailable as exc:
            log.warning("Spotify poll: %s", exc)
            with s.lock:
                s.outcome = "error"
            return "error"
        except ProviderError as exc:
            with s.lock:
                s.failures += 1
                failed = s.failures >= MAX_POLL_FAILURES
                if failed:
                    s.outcome = "error"
            log.info("Spotify poll failed (%s)%s", exc, "; giving up" if failed else "")
            return "error" if failed else "playing"
        verdict = self._assess(s, state, now)
        with s.lock:
            s.failures = 0
            if verdict == "playing":
                return "playing"
            s.outcome = verdict
        if verdict == "user":
            log.info("Spotify: playback changed by you during the drop; NeedleDropper stops managing it")
        return "ended"

    # -------------------------------------------------------------- stop --

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        try:
            self._stop(handle, int(fade_out_ms or 0))
        except Exception:  # noqa: BLE001 - must not raise by contract
            log.exception("Spotify stop failed")

    def _stop(self, handle: PlayHandle, fade_out_ms: int) -> None:
        s = self._session(handle)
        if s is None:
            return
        with s.lock:
            if s.stopped:
                return
            s.stopped = True
        with self._lock:
            if s in self._sessions:
                self._sessions.remove(s)
        self._cancel_fade(s)

        now = self._clock()
        state: Optional[dict] = None
        state_ok = False
        try:
            state = self._get_player()
            state_ok = True
        except ProviderError as exc:
            log.info("Spotify stop: could not read playback state (%s); stopping anyway", exc)

        verdict = s.outcome
        if state_ok:
            fresh = self._assess(s, state, now)
            if verdict != "end":
                verdict = "user" if (verdict == "user" or fresh == "user") else fresh
        elif verdict in (None, "error"):
            verdict = "playing"

        if verdict == "user":
            log.info("Spotify: leaving your playback as it is")
            self._restore_volume(s, state, only_if_untouched=True)
            return

        if state_ok:
            ours_playing = _item_matches(state, s.uri) and bool((state or {}).get("is_playing"))
            anything_playing = bool((state or {}).get("is_playing"))
        else:
            ours_playing = anything_playing = True

        if ours_playing:
            self._fade_out(s, verdict, fade_out_ms, now)

        if anything_playing:
            dev = (state or {}).get("device") if isinstance(state, dict) else None
            pause_dev = str(dev["id"]) if isinstance(dev, dict) and dev.get("id") else s.device_id
            try:
                self._call("PUT", "/me/player/pause", params={"device_id": pause_dev}, tolerant=True)
            except ProviderError as exc:
                log.info("Spotify pause failed: %s", exc)

        self._restore_volume(s)
        self._restore_snapshot(s)

    def _fade_out(self, s: _Session, verdict: Optional[str], fade_out_ms: int, now: float) -> None:
        """Lower the volume before pausing (blocking, on the caller's thread).

        At a natural track end, poll() reports "ended" up to END_LEAD_MS early so
        that we can pause before Spotify rolls into autoplay; the engine then
        stops with no fade, so the remaining moments are played out under a fade
        (or simply waited out when fades are off) and paused just before the end.
        """
        fade_ms = int(fade_out_ms) if self._fade else 0
        tail_ms = 0
        if s.natural_end and s.end_ms is not None:
            remaining = max(0, min(END_LEAD_MS + 2000, s.end_ms - s.estimate_pos(now) - 300))
            fade_ms = min(fade_ms, remaining)
            if verdict == "end":
                tail_ms = remaining
        with s.lock:
            current = s.last_set_volume if s.last_set_volume is not None else s.orig_volume
            can_fade = self._fade and s.volume_ok and bool(current)
        ramp_ms = max(fade_ms, tail_ms)
        if can_fade and ramp_ms >= FADE_STEP_MIN_MS:
            self._ramp(s, int(current or 0), 0, ramp_ms, lambda t: bool(self._sleep(t)))
        elif tail_ms > 0:
            self._sleep(tail_ms / 1000.0)

    def _restore_snapshot(self, s: _Session) -> None:
        snap = s.snapshot
        if not self._restore_previous or not isinstance(snap, dict) or not snap.get("is_playing"):
            return
        item = snap.get("item")
        if not isinstance(item, dict) or not item.get("uri"):
            return
        item_uri = str(item["uri"])
        pos = max(0, int(snap.get("progress_ms") or 0))
        ctx = snap.get("context") if isinstance(snap.get("context"), dict) else {}
        if ctx.get("type") in ("album", "playlist") and ctx.get("uri"):
            linked = item.get("linked_from") if isinstance(item.get("linked_from"), dict) else {}
            body: dict[str, Any] = {"context_uri": ctx["uri"], "offset": {"uri": linked.get("uri") or item_uri},
                                    "position_ms": pos}
        else:
            body = {"uris": [item_uri], "position_ms": pos}
        snap_dev = snap.get("device") if isinstance(snap.get("device"), dict) else {}
        targets: list[str] = []
        if snap_dev.get("id") and not snap_dev.get("is_restricted"):
            targets.append(str(snap_dev["id"]))
        if s.device_id not in targets:
            targets.append(s.device_id)
        for dev in targets:
            try:
                self._call("PUT", "/me/player/play", params={"device_id": dev}, body=body, tolerant=True)
                log.info("Spotify: resumed your previous playback")
                return
            except SpotifyApiError as exc:
                if exc.status == 404:
                    continue
                log.info("Could not resume your previous Spotify playback: %s", exc)
                return
            except ProviderError as exc:
                log.info("Could not resume your previous Spotify playback: %s", exc)
                return

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions)
        for s in sessions:
            self._cancel_fade(s)

    # ------------------------------------------------- control panel helpers --

    def list_devices(self) -> list[dict[str, Any]]:
        """Spotify Connect devices for the panel's device dropdown.

        Each entry: ``id``, ``name``, ``type``, ``isActive``, ``isRestricted``,
        ``volume`` (percent or None), ``supportsVolume``. Raises
        ProviderUnavailable / ProviderError like the player calls.
        """
        out: list[dict[str, Any]] = []
        for d in self._get_devices():
            if not d.get("id") and not d.get("name"):
                continue
            out.append({
                "id": str(d.get("id") or ""),
                "name": str(d.get("name") or "Unnamed device"),
                "type": str(d.get("type") or ""),
                "isActive": bool(d.get("is_active")),
                "isRestricted": bool(d.get("is_restricted")),
                "volume": _clamp_volume(d.get("volume_percent")),
                "supportsVolume": bool(d.get("supports_volume", True)),
            })
        return out

    def status_dict(self) -> dict[str, Any]:
        """Connection state for the control panel (no tokens or secrets).

        ``badge`` is ``"not-set-up"`` (no Client ID or no login), ``"connected"``,
        ``"premium"`` (Spotify refused playback control: Premium required or the
        account is not allowlisted) or ``"error"`` (with ``message``).
        """
        with self._lock:
            unavailable = self._unavailable
        configured = bool(self._client_id)
        logged_in = False
        message = None
        if not configured:
            badge, message = "not-set-up", "Paste your Spotify app's Client ID and connect."
        elif unavailable == PREMIUM_MESSAGE:
            badge, message = "premium", unavailable
            logged_in = self._store.has_refresh_token()
        elif unavailable:
            badge, message = "error", unavailable
            logged_in = self._store.has_refresh_token()
        else:
            try:
                self._tokens._tokens()
                badge, logged_in = "connected", True
            except SpotifyAuthError as exc:
                if exc.error == "not_logged_in":
                    badge, message = "not-set-up", "Not connected yet."
                elif exc.error == "client_mismatch":
                    badge, message = "error", ("The saved login belongs to another Client ID. "
                                               "Click Connect Spotify again.")
                else:
                    badge, message = "error", str(exc)
        return {"badge": badge, "message": message, "configured": configured, "loggedIn": logged_in,
                "deviceName": self._device_name}

    def reset_session(self) -> None:
        """Forget session-wide verdicts (Premium refusal, revoked login, device and
        no-match caches), e.g. after the user connected again in the panel."""
        with self._lock:
            self._unavailable = None
            self._device_cache = None
            self._no_match.clear()


def _retry_after(headers: dict[str, str]) -> float:
    raw = headers.get("retry-after")
    if raw is None:
        return 1.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0
