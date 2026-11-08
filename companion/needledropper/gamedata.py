"""Companion -> game data: the ``NeedleDropper_Companion`` data addon.

An addon cannot read files or talk to other programs; it only sees what the
client loads at login or on ``/reload``. So the companion delivers facts to the
game the way the WeakAuras Companion does: it writes a tiny data addon next to
NeedleDropper::

    Interface\\AddOns\\NeedleDropper_Companion\\NeedleDropper_Companion.toc
    Interface\\AddOns\\NeedleDropper_Companion\\Data.lua

``Data.lua`` sets one global::

    NeedleDropper_CompanionData = {
      schema = 1,
      appVersion = "1.1.0",
      written = 1762380000,            -- unix time; 0 = the app has not written real data yet
      status = {
        spotify = "connected",         -- "connected" | "not-connected" | "premium-required" | "error"
        youtube = true,
        transports = { pixel = true, chatlog = false },
      },
      tracks = {                       -- keyed by drop id or by a Spotify / search key
        ["zone:1429"] = { spotify = "<22-char id>", durationMs = 245000, title = "...", artist = "..." },
        ["q:toto\\031africa"] = { spotify = "<22-char id>", durationMs = 295000, title = "Africa", artist = "Toto" },
        ["q:nobody\\031nothing"] = { notFound = true },        -- Spotify search found no match
        ["yt:QAo_Ycocl1E"] = { notEmbeddable = true },         -- the video may not be embedded
      },
      imports = {                      -- one-time migration offers (DESIGN-v1.2 4.2); absent when none
        { code = "!NeedleDropper:1!...!", active = true },
      },
      music = {                        -- v1.2.1, DESIGN-v1.2.1 3.10 (lead decision D17); absent when empty
        spotify = { clientId = "<32 lowercase hex>", redirectUri = "http://127.0.0.1:8321/callback",
                    account = "Ethan", product = "premium", state = "connected", mode = "app" },
        youtube = { enabled = true, player = "chrome", signInOpened = 1762380000 },
        prefs   = { order = "spotify-youtube", corner = "topright", fade = true, resume = true, fromGame = true },
        actions = { { id = 1762380123, action = "connect-spotify", result = "connected",
                      text = "Spotify connected as Ethan (Premium).", at = 1762380150 } },
      },
    }

``status.spotify`` keeps its four v1.2 values; the detail is ``music.spotify.state`` (connected,
not-connected, needs-relogin, premium-required, no-player, stuck, error -- the first three of
v1.2's values map to themselves, the new states read "error" in ``status.spotify``).
``music.spotify.mode`` is "app" or "connect", ``music.youtube.player`` "chrome", "edge" or "none".

Track keys written by the engine: ``q:<artist>\\031<title>`` (ASCII-lowercased
text as sent in the beacon query) for Spotify search results, ``sp:<id>`` for
the track behind a pasted Spotify link, ``yt:<id>`` for YouTube problems.
``notFound`` / ``notEmbeddable`` are written only when true.

The client only discovers addon folders that exist when it starts, so
:func:`ensure_companion_addon` must run before the game's first launch with
NeedleDropper (when the addon is installed / on the app's first run). After
that, rewriting ``Data.lua`` with :func:`write_companion_data` is enough: the
game picks it up at the next login or ``/reload``.

Incremental updates -- :func:`record_track`, :func:`set_status`,
:func:`add_import`, :func:`set_imports`, :func:`set_music`, :func:`record_music_action`,
:func:`touch` -- are validated at
once, then **coalesced**: at most one write per :data:`MIN_WRITE_INTERVAL`
seconds per AddOns folder, done on a background timer that re-reads the file
and applies every pending change to it (so changes written by something else
in between are kept). :func:`flush` / :func:`flush_all` write pending changes
now (at shutdown; registered with ``atexit``).

Guarantees:

* Writes only ``<addons_dir>\\NeedleDropper_Companion\\NeedleDropper_Companion.toc``,
  ``...\\Data.lua`` and its own short-lived temp files in that folder. It
  refuses an ``addons_dir`` that does not exist or is not named ``AddOns``,
  and a ``NeedleDropper_Companion`` that is a file or a link (symlink or
  junction) leading outside ``addons_dir``. It never creates the AddOns folder.
* Every write is atomic (temp file in the same folder + ``os.replace``, retried
  while the game or a virus scanner holds the file), and is skipped when the
  file already has exactly the new bytes.
* ``Data.lua`` is valid Lua 5.1 built by a small serializer (no string
  templating of values): every string is escaped (quotes, backslashes, line
  breaks and all control characters as 3-digit ``\\ddd``; UTF-8 kept), keys
  and fields come out in a fixed order (tracks sorted by key), and the text is
  parsed back and compared before it may replace the old file.
* Input is validated: wrong types or values raise :class:`GameDataError`
  before anything is written. Oversized content is trimmed (and logged)
  instead of failing: at most :data:`MAX_TRACKS` tracks and at most
  :data:`MAX_DATA_BYTES` of file (the first tracks in key order are kept),
  :data:`MAX_TEXT_CHARS` characters per title / artist, and tracks whose key
  is longer than :data:`MAX_KEY_CHARS` are skipped.

The TOC mirrors the ``## Interface:`` of the installed NeedleDropper addon
(falling back to ``16001``), so a game patch that the main addon has been
updated for never leaves the data addon "out of date".
"""
from __future__ import annotations

import atexit
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from . import __version__ as _APP_VERSION
from . import atomicio as _atomicio
from . import savedvars

__all__ = [
    "ADDON_FOLDER",
    "TOC_FILENAME",
    "DATA_FILENAME",
    "GLOBAL_NAME",
    "SCHEMA",
    "DEFAULT_INTERFACE",
    "MAX_TRACKS",
    "MAX_TEXT_CHARS",
    "MAX_KEY_CHARS",
    "MAX_DURATION_MS",
    "MAX_DATA_BYTES",
    "MAX_IMPORTS",
    "MAX_CODE_CHARS",
    "MIN_WRITE_INTERVAL",
    "SPOTIFY_STATES",
    "TRANSPORTS",
    "TRACK_FIELDS",
    "TRACK_FLAGS",
    "GameDataError",
    "companion_addon_dir",
    "is_companion_addon_installed",
    "ensure_companion_addon",
    "write_companion_data",
    "read_companion_data",
    "normalize_companion_data",
    "render_data_lua",
    "render_toc",
    "lua_quote",
    "query_track_key",
    "CompanionDataWriter",
    "writer_for",
    "record_track",
    "set_status",
    "add_import",
    "set_imports",
    "set_music",
    "record_music_action",
    "touch",
    "flush",
    "flush_all",
    "MUSIC_SPOTIFY_STATES",
    "MUSIC_SPOTIFY_PROBLEMS",
    "MUSIC_SPOTIFY_MODES",
    "YOUTUBE_PLAYERS",
    "MUSIC_ORDERS",
    "MUSIC_CORNERS",
    "MUSIC_ACTIONS",
    "MAX_MUSIC_ACTIONS",
    "MAX_TIME",
]

log = logging.getLogger(__name__)

ADDON_FOLDER = "NeedleDropper_Companion"
TOC_FILENAME = "NeedleDropper_Companion.toc"
DATA_FILENAME = "Data.lua"
GLOBAL_NAME = "NeedleDropper_CompanionData"
SCHEMA = 1
#: TOC interface used when the installed NeedleDropper addon's TOC cannot be read.
DEFAULT_INTERFACE = "16001"
MAIN_ADDON = "NeedleDropper"

#: At most this many tracks are written (the first ones in key order).
MAX_TRACKS = 2000
#: Titles and artists longer than this (in characters) are cut.
MAX_TEXT_CHARS = 300
#: Track keys longer than this (in characters) are skipped.
MAX_KEY_CHARS = 512
#: Longest accepted ``durationMs`` (24 hours).
MAX_DURATION_MS = 24 * 60 * 60 * 1000
#: Hard cap for the rendered ``Data.lua`` (tracks beyond it are trimmed, see :func:`normalize_companion_data`).
MAX_DATA_BYTES = 4 * 1024 * 1024
# Bytes the tracks may take; the rest of the file (header, status) is far smaller than the margin.
_TRACKS_BUDGET = MAX_DATA_BYTES - 64 * 1024

#: At most this many migration offers (``imports``); the addon keeps at most 50 soundtracks.
MAX_IMPORTS = 50
#: Longest share code accepted in ``imports`` (the in-game import box takes 65,536 letters).
MAX_CODE_CHARS = 65536
#: Coalesced updates write ``Data.lua`` at most once per this many seconds (per AddOns folder).
MIN_WRITE_INTERVAL = 2.0
_RETRY_DELAY = 5.0  # after a failed coalesced write (file locked)
_MAX_RETRIES = 5

SPOTIFY_STATES = ("connected", "not-connected", "premium-required", "error")
TRANSPORTS = ("pixel", "chatlog")
#: Boolean track fields; written only when true.
TRACK_FLAGS = ("notFound", "notEmbeddable")
TRACK_FIELDS = ("spotify", "durationMs", "title", "artist") + TRACK_FLAGS
_TOP_FIELDS = ("schema", "appVersion", "written", "status", "tracks", "imports", "music")

# The music block (v1.2.1, DESIGN-v1.2.1 3.10, lead decision D17). The addon's Companion.lua accepts
# exactly these values; they equal providers.spotify.MUSIC_STATES, config.SPOTIFY_DEVICE_MODES,
# config.MUSIC_ORDERS / PLAYER_CORNERS and protocol2.ACTIONS (tested).
MUSIC_SPOTIFY_STATES = ("connected", "not-connected", "needs-relogin", "premium-required", "no-player", "stuck",
                        "error")
MUSIC_SPOTIFY_MODES = ("app", "connect")
#: ``music.spotify.problem``: what the game can say beyond the state (providers.spotify.MUSIC_PROBLEMS). With
#: "error" (v1.2.1 review ON-3): "allowlist", Spotify refused the account for the player's developer app (User
#: Management). With "no-player" (review ON-9): "no-chrome", "not-ready", "no-window", "window-never" or
#: "sdk-load" (spotify.NO_PLAYER_PROBLEMS), so the game names the right cause instead of "install Chrome".
MUSIC_SPOTIFY_PROBLEMS = ("allowlist", "no-chrome", "not-ready", "no-window", "window-never", "sdk-load")
YOUTUBE_PLAYERS = ("chrome", "edge", "none")
MUSIC_ORDERS = ("spotify-youtube", "youtube-spotify", "spotify", "youtube", "none")
MUSIC_CORNERS = ("topright", "topleft", "bottomright", "bottomleft")
MUSIC_ACTIONS = ("connect-spotify", "disconnect-spotify", "test-spotify", "test-youtube", "youtube-sign-in")
#: ``music.actions`` keeps this many request results (the newest last).
MAX_MUSIC_ACTIONS = 8
MAX_ACCOUNT_CHARS = 64
MAX_ACTION_TEXT_CHARS = 300
_MAX_UINT32 = 0xFFFFFFFF
#: The latest time stamp the game is given (2100-01-01 00:00 UTC): ``written``, ``music.youtube.signInOpened`` and
#: an action's ``at`` reach the game's date(), which gives nothing past the client's calendar (year 3000 on
#: Windows). Companion.lua's checker has the same cap (C.MAX_TIME; review INGAME-7).
MAX_TIME = 4102444800
_MUSIC_FIELDS = ("spotify", "youtube", "prefs", "actions")
_MUSIC_SPOTIFY_FIELDS = ("clientId", "redirectUri", "account", "product", "state", "mode", "problem")
_MUSIC_YOUTUBE_FIELDS = ("enabled", "player", "signInOpened")
_MUSIC_PREFS_FIELDS = ("order", "corner", "fade", "resume", "volume", "fromGame")
_MUSIC_ACTION_FIELDS = ("id", "action", "result", "text", "at")
_CLIENT_ID_RE = re.compile(r"[0-9a-f]{32}")
_REDIRECT_RE = re.compile(r"http://127\.0\.0\.1:[0-9]{1,5}/callback")
_PRODUCT_RE = re.compile(r"[a-z]{1,16}")
_RESULT_RE = re.compile(r"[a-z][a-z-]{0,23}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_STATUS_FIELDS = ("spotify", "youtube", "transports")
_IMPORT_FIELDS = ("code", "active")
_CODE_RE = re.compile(r"!NeedleDropper:[0-9]{1,9}![A-Za-z0-9+/=]+!")

_HEADER_LINE = "local ADDON, ns = ..."
_TOC_TEMPLATE = (
    "## Interface: {interface}\n"
    "## Title: NeedleDropper Companion Data\n"
    "## Notes: Written by the NeedleDropper app. Do not edit.\n"
    "## Author: NeedleDropper\n"
    "## Version: {version}\n"
    "Data.lua\n"
)

# os.replace retries while another process (the game loading the file, a virus
# scanner) holds the target open.
_REPLACE_ATTEMPTS = 10
_REPLACE_DELAY = 0.2
_TMP_SUFFIX = ".tmp"
_STALE_TMP_SECONDS = 10 * 60
_MAX_EXACT_INT = 2 ** 53
_MAX_RENDER_DEPTH = 16

_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")
_INTERFACE_RE = re.compile(r"[0-9]{5,6}(?:[ \t]*,[ \t]*[0-9]{5,6}){0,7}")
_TOC_INTERFACE_LINE = re.compile(rb"^[ \t]*##[ \t]*Interface[ \t]*:[ \t]*([^\r\n]*?)[ \t]*\r?$",
                                 re.IGNORECASE | re.MULTILINE)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LUA_KEYWORDS = frozenset(
    "and break do else elseif end false for function if in local nil not or "
    "repeat return then true until while".split()
)
_SURROGATE = re.compile("[\ud800-\udfff]")
_SPOTIFY_ID = re.compile(r"[0-9A-Za-z]{22}")
_SPOTIFY_URI = re.compile(r"spotify:track:([0-9A-Za-z]{22})")
_SPOTIFY_URL = re.compile(r"https?://open\.spotify\.com/(?:intl-[A-Za-z-]+/)?track/([0-9A-Za-z]{22})(?:[/?#].*)?",
                          re.IGNORECASE | re.DOTALL)
_HEADER_RE = re.compile(rb"\A(?:\xef\xbb\xbf)?[ \t]*local[ \t]+ADDON[ \t]*,[ \t]*ns[ \t]*=[ \t]*\.\.\.[ \t]*;?")

# Lua string escapes: every C0 control character and DEL as \ddd (always 3
# digits, so a following digit can never extend the escape), the usual short
# forms for the common ones.
_LUA_ESCAPES: dict[int, str] = {code: f"\\{code:03d}" for code in [*range(0x20), 0x7F]}
_LUA_ESCAPES.update({ord("\\"): "\\\\", ord('"'): '\\"', ord("\n"): "\\n", ord("\r"): "\\r", ord("\t"): "\\t"})


class GameDataError(ValueError):
    """The companion data is invalid, or the target folder is not safe to write."""


# ------------------------------------------------------------ serializer ----

def _clean_text(s: str) -> str:
    """``s`` with lone surrogates (not encodable as UTF-8) replaced by U+FFFD."""
    return _SURROGATE.sub("�", s) if _SURROGATE.search(s) else s


def lua_quote(s: str) -> str:
    """``s`` as a double-quoted Lua 5.1 string literal (UTF-8 kept, everything else escaped)."""
    if not isinstance(s, str):
        raise GameDataError(f"expected a string, not {type(s).__name__}")
    return '"' + _clean_text(s).translate(_LUA_ESCAPES) + '"'


def _lua_key(key: Any) -> str:
    if isinstance(key, str):
        if _IDENTIFIER.fullmatch(key) and key not in _LUA_KEYWORDS:
            return key
        return f"[{lua_quote(key)}]"
    if isinstance(key, int) and not isinstance(key, bool):
        if abs(key) > _MAX_EXACT_INT:
            raise GameDataError(f"table key {key} is too large for Lua")
        return f"[{key}]"
    raise GameDataError(f"cannot write a {type(key).__name__} table key")


def _is_table(value: Any) -> bool:
    return isinstance(value, (Mapping, list, tuple))


def _lua(value: Any, indent: int = 0, depth: int = 0) -> str:
    """``value`` as a Lua 5.1 constant. Dicts keep their insertion order."""
    if depth > _MAX_RENDER_DEPTH:
        raise GameDataError("data is nested too deeply")
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > _MAX_EXACT_INT:
            raise GameDataError(f"number {value} is too large for Lua")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GameDataError(f"cannot write the number {value!r}")
        return repr(value)  # shortest form that reads back exactly, e.g. 0.1, 1e+20
    if isinstance(value, str):
        return lua_quote(value)
    if isinstance(value, Mapping):
        entries = [(_lua_key(k), v) for k, v in value.items() if v is not None]
    elif isinstance(value, (list, tuple)):
        if any(v is None for v in value):
            raise GameDataError("a list cannot hold None (Lua arrays cannot have holes)")
        entries = [(None, v) for v in value]
    else:
        raise GameDataError(f"cannot write a {type(value).__name__} value")
    if not entries:
        return "{}"
    if not any(_is_table(v) for _, v in entries):
        items = [(f"{k} = " if k else "") + _lua(v, indent + 1, depth + 1) for k, v in entries]
        return "{ " + ", ".join(items) + " }"
    pad = "  " * (indent + 1)
    lines = [pad + (f"{k} = " if k else "") + _lua(v, indent + 1, depth + 1) + "," for k, v in entries]
    return "{\n" + "\n".join(lines) + "\n" + "  " * indent + "}"


# ------------------------------------------------------------ validation ----

def _bool(value: Any, what: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise GameDataError(f"{what} must be true or false, not {value!r}")
    return value


def _check_fields(raw: Mapping, allowed: tuple[str, ...], what: str) -> None:
    unknown = [k for k in raw if k not in allowed]
    if unknown:
        raise GameDataError(f"{what}: unknown field(s) {', '.join(map(repr, sorted(map(str, unknown))))} "
                            f"(allowed: {', '.join(allowed)})")


def _status(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise GameDataError(f"status must be a dict, not {type(raw).__name__}")
    _check_fields(raw, _STATUS_FIELDS, "status")
    spotify = raw.get("spotify")
    if spotify is None:
        spotify = "not-connected"
    if spotify not in SPOTIFY_STATES:
        raise GameDataError(f"status.spotify must be one of {', '.join(SPOTIFY_STATES)}, not {spotify!r}")
    transports = raw.get("transports")
    if transports is None:
        transports = {}
    if not isinstance(transports, Mapping):
        raise GameDataError(f"status.transports must be a dict, not {type(transports).__name__}")
    _check_fields(transports, TRANSPORTS, "status.transports")
    return {
        "spotify": spotify,
        "youtube": _bool(raw.get("youtube"), "status.youtube", False),
        "transports": {name: _bool(transports.get(name), f"status.transports.{name}", False)
                       for name in TRANSPORTS},
    }


def _spotify_id(value: Any, what: str) -> str:
    if isinstance(value, str):
        s = value.strip()
        if _SPOTIFY_ID.fullmatch(s):
            return s
        m = _SPOTIFY_URI.fullmatch(s) or _SPOTIFY_URL.fullmatch(s)
        if m:
            return m.group(1)
    raise GameDataError(f"{what}.spotify must be a 22-character Spotify track id (or a spotify:track: URI / "
                        f"open.spotify.com track link), not {value!r}")


def _duration(value: Any, what: str) -> int:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_DURATION_MS:
        raise GameDataError(f"{what}.durationMs must be a whole number of milliseconds from 0 to "
                            f"{MAX_DURATION_MS}, not {value!r}")
    return value


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise GameDataError(f"{what} must be a string, not {type(value).__name__}")
    value = _clean_text(value)
    if len(value) > MAX_TEXT_CHARS:
        log.warning("%s is %d characters long; keeping the first %d", what, len(value), MAX_TEXT_CHARS)
        value = value[:MAX_TEXT_CHARS]
    return value


def _track(key: str, raw: Any) -> dict[str, Any]:
    what = f"tracks[{key!r}]"
    if not isinstance(raw, Mapping):
        raise GameDataError(f"{what} must be a dict, not {type(raw).__name__}")
    _check_fields(raw, TRACK_FIELDS, what)
    out: dict[str, Any] = {}
    if raw.get("spotify") is not None:
        out["spotify"] = _spotify_id(raw["spotify"], what)
    if raw.get("durationMs") is not None:
        out["durationMs"] = _duration(raw["durationMs"], what)
    if raw.get("title") is not None:
        out["title"] = _text(raw["title"], f"{what}.title")
    if raw.get("artist") is not None:
        out["artist"] = _text(raw["artist"], f"{what}.artist")
    for flag in TRACK_FLAGS:
        if _bool(raw.get(flag), f"{what}.{flag}", False):
            out[flag] = True
    return out


def query_track_key(artist: Any, title: Any) -> str:
    """The ``tracks`` key of a search: ``q:`` + artist + U+001F + title, ASCII letters lowercased.

    Only ``A``-``Z`` are lowercased (what Lua's ``string.lower`` does in the client's C locale),
    so the addon can build the same key from the same text without Unicode case tables.
    """
    a = artist if isinstance(artist, str) else ""
    t = title if isinstance(title, str) else ""
    return "q:" + (a + "\x1f" + t).translate(_ASCII_LOWER)


_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def _import(raw: Any, what: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise GameDataError(f"{what} must be a dict with a share code, not {type(raw).__name__}")
    _check_fields(raw, _IMPORT_FIELDS, what)
    code = raw.get("code")
    if not isinstance(code, str) or len(code) > MAX_CODE_CHARS or not _CODE_RE.fullmatch(code):
        shown = code[:40] + "..." if isinstance(code, str) and len(code) > 40 else code
        raise GameDataError(f"{what}.code must be a NeedleDropper share code (\"!NeedleDropper:1!...!\", at most "
                            f"{MAX_CODE_CHARS} characters), not {shown!r}")
    return {"code": code, "active": _bool(raw.get("active"), f"{what}.active", False)}


def _imports(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, Mapping) and not raw:
        return []  # an empty Lua table reads back as {}
    if not isinstance(raw, (list, tuple)):
        raise GameDataError(f"imports must be a list of {{code, active}}, not {type(raw).__name__}")
    out: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for i, item in enumerate(raw):
        entry = _import(item, f"imports[{i}]")
        if entry["code"] in index:  # the same code twice: one offer (the later "active" wins)
            out[index[entry["code"]]]["active"] = entry["active"]
            continue
        index[entry["code"]] = len(out)
        out.append(entry)
    if len(out) > MAX_IMPORTS:
        raise GameDataError(f"at most {MAX_IMPORTS} imports, not {len(out)}")
    return out


def _whole(value: Any, what: str, upper: int = _MAX_EXACT_INT) -> int:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= upper:
        raise GameDataError(f"{what} must be a whole number from 0 to {upper}, not {value!r}")
    return value


def _one_of(value: Any, allowed: tuple[str, ...], what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise GameDataError(f"{what} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def _matching(value: Any, pattern: "re.Pattern[str]", what: str, shape: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise GameDataError(f"{what} must be {shape}, not {value!r}")
    return value


def _plain_text(value: Any, what: str, limit: int) -> Optional[str]:
    """Text for the game: control characters removed, at most ``limit`` characters; None if empty."""
    if not isinstance(value, str):
        raise GameDataError(f"{what} must be a string, not {type(value).__name__}")
    s = _CONTROL_RE.sub("", _clean_text(value)).strip()
    return s[:limit] or None


def _music_spotify(name: str, value: Any) -> Any:
    what = f"music.spotify.{name}"
    if name == "clientId":
        return _matching(value, _CLIENT_ID_RE, what, "32 lowercase hex digits")
    if name == "redirectUri":
        return _matching(value, _REDIRECT_RE, what, "http://127.0.0.1:<port>/callback")
    if name == "account":
        return _plain_text(value, what, MAX_ACCOUNT_CHARS)
    if name == "product":
        return _matching(value, _PRODUCT_RE, what, "1-16 lowercase letters (premium, free, open)")
    if name == "state":
        return _one_of(value, MUSIC_SPOTIFY_STATES, what)
    if name == "problem":
        return _one_of(value, MUSIC_SPOTIFY_PROBLEMS, what)
    return _one_of(value, MUSIC_SPOTIFY_MODES, what)  # mode


def _music_youtube(name: str, value: Any) -> Any:
    what = f"music.youtube.{name}"
    if name == "enabled":
        return _bool(value, what, False)
    if name == "player":
        return _one_of(value, YOUTUBE_PLAYERS, what)
    return _whole(value, what, MAX_TIME)  # signInOpened


def _music_prefs(name: str, value: Any) -> Any:
    what = f"music.prefs.{name}"
    if name == "volume":
        return _whole(value, what, 100)
    if name == "order":
        return _one_of(value, MUSIC_ORDERS, what)
    if name == "corner":
        return _one_of(value, MUSIC_CORNERS, what)
    return _bool(value, what, False)  # fade, resume, fromGame


_MUSIC_PARTS: dict[str, tuple[tuple[str, ...], Callable[[str, Any], Any]]] = {
    "spotify": (_MUSIC_SPOTIFY_FIELDS, _music_spotify),
    "youtube": (_MUSIC_YOUTUBE_FIELDS, _music_youtube),
    "prefs": (_MUSIC_PREFS_FIELDS, _music_prefs),
}


def _music_part(part: str, raw: Any) -> dict[str, Any]:
    """One of ``music.spotify`` / ``youtube`` / ``prefs``: unknown fields and wrong values raise; a
    field that is None (or text that is empty once cleaned) is left out."""
    if raw is None or (isinstance(raw, Mapping) and not raw):
        return {}
    if not isinstance(raw, Mapping):
        raise GameDataError(f"music.{part} must be a dict, not {type(raw).__name__}")
    fields, check = _MUSIC_PARTS[part]
    _check_fields(raw, fields, f"music.{part}")
    out: dict[str, Any] = {}
    for name in fields:  # a fixed order in the file
        if raw.get(name) is None:
            continue
        value = check(name, raw[name])
        if value is not None:
            out[name] = value
    return out


def _music_action(raw: Any, what: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise GameDataError(f"{what} must be a dict, not {type(raw).__name__}")
    _check_fields(raw, _MUSIC_ACTION_FIELDS, what)
    for name in ("id", "action", "result", "at"):
        if raw.get(name) is None:
            raise GameDataError(f"{what}.{name} is missing")
    out: dict[str, Any] = {
        "id": _whole(raw["id"], f"{what}.id", _MAX_UINT32),
        "action": _one_of(raw["action"], MUSIC_ACTIONS, f"{what}.action"),
        "result": _matching(raw["result"], _RESULT_RE, f"{what}.result", "a result code like \"connected\""),
    }
    text = _plain_text(raw["text"], f"{what}.text", MAX_ACTION_TEXT_CHARS) if raw.get("text") is not None else None
    if text is not None:
        out["text"] = text
    out["at"] = _whole(raw["at"], f"{what}.at", MAX_TIME)
    return out


def _music_actions(raw: Any) -> list[dict[str, Any]]:
    """The request results, oldest first: one per (id, action) -- a later entry replaces an earlier
    one, so an interim "opened" becomes the final result -- and the newest :data:`MAX_MUSIC_ACTIONS`."""
    if raw is None or (isinstance(raw, Mapping) and not raw):
        return []
    if not isinstance(raw, (list, tuple)):
        raise GameDataError(f"music.actions must be a list, not {type(raw).__name__}")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        entry = _music_action(item, f"music.actions[{i}]")
        out = [e for e in out if (e["id"], e["action"]) != (entry["id"], entry["action"])]
        out.append(entry)
    return out[-MAX_MUSIC_ACTIONS:]


def _music(raw: Any) -> dict[str, Any]:
    """The ``music`` block (DESIGN-v1.2.1 3.10); ``{}`` when there is nothing in it."""
    if raw is None or (isinstance(raw, Mapping) and not raw):
        return {}
    if not isinstance(raw, Mapping):
        raise GameDataError(f"music must be a dict, not {type(raw).__name__}")
    _check_fields(raw, _MUSIC_FIELDS, "music")
    out: dict[str, Any] = {}
    for part in ("spotify", "youtube", "prefs"):
        value = _music_part(part, raw.get(part))
        if value:
            out[part] = value
    actions = _music_actions(raw.get("actions"))
    if actions:
        out["actions"] = actions
    return out


def _tracks(raw: Any, max_tracks: int, *, budget: int = _TRACKS_BUDGET) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise GameDataError(f"tracks must be a dict of key -> track, not {type(raw).__name__}")
    items: dict[str, dict[str, Any]] = {}
    for key, entry in raw.items():
        if not isinstance(key, str) or not key:
            raise GameDataError(f"track keys must be non-empty strings, not {key!r}")
        clean = _clean_text(key)
        if len(clean) > MAX_KEY_CHARS:
            log.warning("skipping a track whose key is %d characters long (limit %d)", len(clean), MAX_KEY_CHARS)
            continue
        if clean in items:
            raise GameDataError(f"two track keys are the same after replacing invalid characters: {clean!r}")
        items[clean] = _track(clean, entry)
    keys = sorted(items)
    if len(keys) > max_tracks:
        log.warning("%d tracks given; writing the first %d (in key order)", len(keys), max_tracks)
        keys = keys[:max_tracks]
    out: dict[str, dict[str, Any]] = {}
    used = 0
    for key in keys:
        used += len(_lua_key(key).encode("utf-8")) + len(_lua(items[key], 2, 2).encode("utf-8")) + 10
        if used > budget:
            log.warning("the tracks do not fit in %d bytes; writing the first %d of %d (in key order)",
                        budget, len(out), len(keys))
            break
        out[key] = items[key]
    return out


def normalize_companion_data(data: Optional[Mapping[str, Any]] = None, *, now: Optional[float] = None,
                             max_tracks: int = MAX_TRACKS) -> dict[str, Any]:
    """Validate ``data`` and return the exact payload :func:`render_data_lua` writes.

    Missing fields get defaults: ``appVersion`` = this app's version,
    ``written`` = ``now`` (default: the current time), ``status.spotify`` =
    ``"not-connected"``, every other status flag ``False``, ``tracks`` = ``{}``,
    no ``imports`` (an empty list is left out of the file).
    ``None`` counts as missing. ``spotify`` accepts a track id, a
    ``spotify:track:`` URI or an ``open.spotify.com`` track link; the track
    flags ``notFound`` / ``notEmbeddable`` are kept only when true. Tracks come
    out sorted by key; beyond ``max_tracks`` tracks or the file size budget
    the rest is dropped, and over-long titles / artists are cut (see the
    module docs). ``imports`` is a list of ``{code, active}`` share-code
    offers (a code listed twice counts once). ``music`` (v1.2.1) is checked
    field by field (see the module docs; an empty block is left out, the
    account name and an action's text lose control characters and are cut to
    64 / 300 characters, and only the newest 8 actions are kept). Raises
    :class:`GameDataError` for unknown fields, wrong types and invalid values.
    """
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise GameDataError(f"companion data must be a dict, not {type(data).__name__}")
    if not isinstance(max_tracks, int) or isinstance(max_tracks, bool) or max_tracks < 0:
        raise GameDataError(f"max_tracks must be a non-negative int, not {max_tracks!r}")
    _check_fields(data, _TOP_FIELDS, "companion data")
    schema = data.get("schema")
    if schema is not None and (schema != SCHEMA or isinstance(schema, bool)):
        raise GameDataError(f"schema must be {SCHEMA}, not {schema!r}")
    version = data.get("appVersion")
    if version is None:
        version = _APP_VERSION
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise GameDataError(f"appVersion must look like a version (e.g. 1.2.0), not {version!r}")
    written = data.get("written")
    if written is None:
        written = time.time() if now is None else now
        if isinstance(written, (int, float)) and math.isfinite(written):
            written = min(int(written), MAX_TIME)  # a PC clock past 2100 must not stop every write
    if isinstance(written, float) and math.isfinite(written):
        written = int(written)
    if not isinstance(written, int) or isinstance(written, bool) or not 0 <= written <= MAX_TIME:
        raise GameDataError(f"written must be a unix time (whole seconds, from 0 to {MAX_TIME}), not {written!r}")
    imports = _imports(data.get("imports"))
    imports_bytes = sum(len(i["code"]) + 48 for i in imports)
    music = _music(data.get("music"))
    out = {
        "schema": SCHEMA,
        "appVersion": version,
        "written": written,
        "status": _status(data.get("status")),
        "tracks": _tracks(data.get("tracks"), max_tracks, budget=_TRACKS_BUDGET - imports_bytes),
    }
    if imports:
        out["imports"] = imports
    if music:
        out["music"] = music
    return out


def _render(payload: dict[str, Any]) -> str:
    text = "\n".join([
        _HEADER_LINE,
        f"-- NeedleDropper companion data, written by the NeedleDropper app {payload['appVersion']}.",
        "-- Do not edit: the app replaces this file whenever it has news for the game,",
        "-- and the game reads it at login and on /reload.",
        f"{GLOBAL_NAME} = {_lua(payload)}",
        "",
    ])
    size = len(text.encode("utf-8"))
    if size > MAX_DATA_BYTES:
        raise GameDataError(f"companion data is too large ({size:,} bytes; the limit is {MAX_DATA_BYTES:,})")
    # Never let a file the game cannot load replace a good one: read it back.
    try:
        back = savedvars.parse_savedvariables(_strip_header(text.encode("utf-8")), string_errors="strict")
    except savedvars.SavedVariablesError as exc:  # pragma: no cover - a serializer bug
        raise GameDataError(f"internal error: the rendered Data.lua does not parse ({exc})") from exc
    if back != {GLOBAL_NAME: payload}:  # pragma: no cover - a serializer bug
        raise GameDataError("internal error: the rendered Data.lua does not read back as the data")
    return text


def render_data_lua(data: Optional[Mapping[str, Any]] = None, *, now: Optional[float] = None,
                    max_tracks: int = MAX_TRACKS) -> str:
    """The complete text of ``Data.lua`` for ``data`` (see :func:`normalize_companion_data`)."""
    return _render(normalize_companion_data(data, now=now, max_tracks=max_tracks))


def render_toc(interface: Optional[str] = None, version: Optional[str] = None) -> str:
    """The text of ``NeedleDropper_Companion.toc`` (no SavedVariables; loads ``Data.lua``)."""
    interface = DEFAULT_INTERFACE if interface is None else interface
    version = _APP_VERSION if version is None else version
    if not isinstance(interface, str) or not _INTERFACE_RE.fullmatch(interface):
        raise GameDataError(f"not a TOC interface number: {interface!r}")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise GameDataError(f"not a version: {version!r}")
    return _TOC_TEMPLATE.format(interface=interface, version=version)


# ------------------------------------------------------------ filesystem ----

def companion_addon_dir(addons_dir: Union[str, os.PathLike]) -> Path:
    """``<addons_dir>\\NeedleDropper_Companion`` (no checks)."""
    return Path(addons_dir) / ADDON_FOLDER


def is_companion_addon_installed(addons_dir: Union[str, os.PathLike]) -> bool:
    """Whether the data addon's TOC and ``Data.lua`` exist (the game sees the addon only if
    they did when it started)."""
    folder = companion_addon_dir(addons_dir)
    return (folder / TOC_FILENAME).is_file() and (folder / DATA_FILENAME).is_file()


def _addons_root(addons_dir: Union[str, os.PathLike]) -> Path:
    if not isinstance(addons_dir, (str, os.PathLike)):
        raise GameDataError(f"addons_dir must be a path, not {type(addons_dir).__name__}")
    root = Path(addons_dir)
    if root.name.casefold() != "addons":
        raise GameDataError(f"refusing to write: {root} is not a WoW AddOns folder (expected ...\\Interface\\AddOns)")
    if not root.is_dir():
        raise GameDataError(f"refusing to write: the AddOns folder {root} does not exist")
    return root


def _check_inside(root: Path, folder: Path) -> None:
    """``folder`` must be a real directory directly inside ``root`` (not a link leading elsewhere)."""
    if folder.is_symlink():
        raise GameDataError(f"refusing to write: {folder} is a symbolic link")
    if not folder.is_dir():
        raise GameDataError(f"refusing to write: {folder} exists but is not a folder")
    real_root = os.path.normcase(os.path.realpath(root))
    real = os.path.normcase(os.path.realpath(folder))
    if os.path.dirname(real) != real_root or os.path.basename(real) != os.path.normcase(ADDON_FOLDER):
        raise GameDataError(f"refusing to write: {folder} leads outside {root}")


def _prepare_folder(addons_dir: Union[str, os.PathLike]) -> tuple[Path, Path]:
    """Validate ``addons_dir``, create ``NeedleDropper_Companion`` if needed; ``(root, folder)``."""
    root = _addons_root(addons_dir)
    folder = root / ADDON_FOLDER
    if not os.path.lexists(folder):
        try:
            folder.mkdir()
        except FileExistsError:
            pass
    _check_inside(root, folder)
    _remove_stale_temps(folder)
    return root, folder


def _remove_stale_temps(folder: Path) -> None:
    """Delete temp files an interrupted write of ours left behind (older than 10 minutes)."""
    prefixes = (f".{DATA_FILENAME}.", f".{TOC_FILENAME}.")
    cutoff = time.time() - _STALE_TMP_SECONDS
    try:
        with os.scandir(folder) as it:
            stale = [e.path for e in it
                     if e.name.startswith(prefixes) and e.name.endswith(_TMP_SUFFIX) and e.is_file(follow_symlinks=False)
                     and e.stat(follow_symlinks=False).st_mtime < cutoff]
    except OSError:
        return
    for path in stale:
        try:
            os.remove(path)
        except OSError:
            pass


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file in the same folder and ``os.replace``
    (:func:`needledropper.atomicio.atomic_write_bytes`: retried while the file is locked, and
    written in place when Windows refuses the rename across devices -- WinError 17)."""
    if path.is_dir():
        raise GameDataError(f"refusing to write: {path} is a folder")
    _atomicio.atomic_write_bytes(path, data, retries=_REPLACE_ATTEMPTS, delay=_REPLACE_DELAY,
                                 prefix=f".{path.name}.")


def _write_if_changed(path: Path, data: bytes) -> bool:
    """Atomically write ``data`` unless ``path`` already holds exactly it. True if written."""
    try:
        if path.is_file() and not path.is_symlink() and path.stat().st_size == len(data):
            with open(path, "rb") as fh:
                if fh.read(len(data) + 1) == data:
                    return False
    except OSError:
        pass
    _atomic_write(path, data)
    return True


def _interface_for(root: Path) -> str:
    """The ``## Interface:`` of the installed NeedleDropper addon, else :data:`DEFAULT_INTERFACE`."""
    toc = root / MAIN_ADDON / f"{MAIN_ADDON}.toc"
    try:
        with open(toc, "rb") as fh:
            head = fh.read(64 * 1024)
    except OSError:
        return DEFAULT_INTERFACE
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    m = _TOC_INTERFACE_LINE.search(head)
    if m:
        value = m.group(1).decode("ascii", "replace").strip()
        if _INTERFACE_RE.fullmatch(value):
            return value
    return DEFAULT_INTERFACE


def _strip_header(raw: bytes) -> bytes:
    """``Data.lua`` without its ``local ADDON, ns = ...`` line (kept as an empty line, so
    error line numbers still match the file)."""
    return _HEADER_RE.sub(b"", raw, count=1)


def _read_data(path: Path, *, string_errors: str) -> Optional[dict[str, Any]]:
    size = path.stat().st_size
    if size > MAX_DATA_BYTES:
        raise GameDataError(f"{path} is too large ({size:,} bytes)")
    with open(path, "rb") as fh:
        raw = fh.read(MAX_DATA_BYTES + 1)
    try:
        values = savedvars.parse_savedvariables(_strip_header(raw), string_errors=string_errors)
    except savedvars.SavedVariablesError as exc:
        raise GameDataError(f"{path}: {exc.detail}") from None
    value = values.get(GLOBAL_NAME)
    if value is not None and not isinstance(value, dict):
        raise GameDataError(f"{path}: {GLOBAL_NAME} is not a table")
    return value


def read_companion_data(addons_dir: Union[str, os.PathLike]) -> Optional[dict[str, Any]]:
    """The ``NeedleDropper_CompanionData`` table currently in ``Data.lua``, read without executing it.

    ``None`` when the data addon or ``Data.lua`` is missing, or the file sets no
    data. Raises :class:`GameDataError` when the file cannot be parsed.
    """
    path = companion_addon_dir(addons_dir) / DATA_FILENAME
    try:
        return _read_data(path, string_errors="replace")
    except FileNotFoundError:
        return None


def ensure_companion_addon(addons_dir: Union[str, os.PathLike]) -> Path:
    """Create the data addon (folder, TOC and a default ``Data.lua``) if it is missing.

    Run this before the game starts for the first time with NeedleDropper: the
    client only discovers addon folders that exist at launch. Idempotent:
    an up-to-date TOC is left alone (it is rewritten when the app version or
    NeedleDropper's interface changed), and an existing readable ``Data.lua``
    is never replaced; a missing or unreadable one gets the default payload
    (``written = 0``, nothing connected, no tracks), so the addon can always
    index ``NeedleDropper_CompanionData.status``. Returns the folder.

    Raises :class:`GameDataError` for an unsafe ``addons_dir`` (see the module
    docs) and ``OSError`` when the files cannot be written.
    """
    root, folder = _prepare_folder(addons_dir)
    _write_if_changed(folder / TOC_FILENAME, render_toc(_interface_for(root)).encode("utf-8"))
    data_path = folder / DATA_FILENAME
    try:
        current = _read_data(data_path, string_errors="replace")
    except FileNotFoundError:
        current = None
    except GameDataError as exc:
        log.warning("replacing an unreadable companion data file: %s", exc)
        current = None
    # Any other OSError (e.g. the game holding the file) propagates: never
    # replace real data just because it could not be read right now.
    if current is None:
        _atomic_write(data_path, render_data_lua({}, now=0).encode("utf-8"))
    return folder


def write_companion_data(addons_dir: Union[str, os.PathLike], data: Optional[Mapping[str, Any]], *,
                         now: Optional[float] = None, max_tracks: int = MAX_TRACKS) -> Path:
    """Write ``Data.lua`` for ``data`` (see :func:`normalize_companion_data`); returns its path.

    The data is validated and rendered before anything on disk is touched. The
    data addon is created if needed (see :func:`ensure_companion_addon`; the
    game only sees it after its next start if it did not exist at launch).
    A write that would produce the bytes already on disk is skipped (``written``
    defaults to the current second, so pass ``written`` or ``now`` to make
    repeated writes of unchanged data identical). Raises
    :class:`GameDataError` for invalid data or an unsafe ``addons_dir`` and
    ``OSError`` when the file cannot be written.
    """
    payload = render_data_lua(data, now=now, max_tracks=max_tracks).encode("utf-8")
    root, folder = _prepare_folder(addons_dir)
    _write_if_changed(folder / TOC_FILENAME, render_toc(_interface_for(root)).encode("utf-8"))
    path = folder / DATA_FILENAME
    _write_if_changed(path, payload)
    return path


# ------------------------------------------------------ coalesced updates ----

Schedule = Callable[[float, Callable[[], None]], Any]  # (delay s, fn) -> handle with .cancel()


def _timer_schedule(delay: float, fn: Callable[[], None]) -> threading.Timer:
    t = threading.Timer(max(0.0, delay), fn)
    t.daemon = True
    t.name = "needledropper-gamedata"
    t.start()
    return t


def _lenient_current(addons_dir: Union[str, os.PathLike]) -> dict[str, Any]:
    """What ``Data.lua`` holds now, keeping every part that is still valid (status fields, tracks
    and imports one by one); ``{}`` when it is missing or unreadable. ``OSError`` (file locked)
    propagates so the caller can retry instead of dropping data."""
    try:
        raw = read_companion_data(addons_dir)
    except GameDataError as exc:
        log.warning("starting the companion data afresh: %s", exc)
        return {}
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    status = raw.get("status")
    if isinstance(status, Mapping):
        clean: dict[str, Any] = {}
        for key in _STATUS_FIELDS:
            if status.get(key) is None:
                continue
            try:
                clean[key] = _status({key: status.get(key)})[key]
            except GameDataError:
                continue
        out["status"] = clean
    tracks = raw.get("tracks")
    if isinstance(tracks, Mapping):
        good: dict[str, dict[str, Any]] = {}
        for key, entry in tracks.items():
            if not isinstance(key, str) or not key:
                continue
            try:
                good[key] = _track(key, entry)
            except GameDataError:
                continue
        out["tracks"] = good
    imports = raw.get("imports")
    if isinstance(imports, (list, tuple)):
        kept = []
        for i, item in enumerate(imports):
            try:
                kept.append(_import(item, f"imports[{i}]"))
            except GameDataError:
                continue
        out["imports"] = kept
    music = _lenient_music(raw.get("music"))
    if music:
        out["music"] = music
    return out


def _lenient_music(raw: Any) -> dict[str, Any]:
    """The valid parts of a ``music`` block read back from ``Data.lua``, field by field."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for part, (fields, check) in _MUSIC_PARTS.items():
        sub = raw.get(part)
        if not isinstance(sub, Mapping):
            continue
        good: dict[str, Any] = {}
        for name in fields:
            if sub.get(name) is None:
                continue
            try:
                value = check(name, sub[name])
            except GameDataError:
                continue
            if value is not None:
                good[name] = value
        if good:
            out[part] = good
    actions = raw.get("actions")
    if isinstance(actions, (list, tuple)):
        kept = []
        for i, item in enumerate(actions):
            try:
                kept.append(_music_action(item, f"music.actions[{i}]"))
            except GameDataError:
                continue
        if kept:
            out["actions"] = _music_actions(kept)
    return out


def _merge_music(data: dict[str, Any], parts: Mapping[str, Optional[Mapping[str, Any]]]) -> None:
    """Merge ``{part: {field: value or None}}`` into ``data["music"]`` (None removes a field)."""
    music = data.setdefault("music", {})
    for part, fields in parts.items():
        if fields is None:
            continue
        sub = dict(music.get(part) or {})
        for name, value in fields.items():
            if value is None:
                sub.pop(name, None)
            else:
                sub[name] = value
        if sub:
            music[part] = sub
        else:
            music.pop(part, None)
    if not music:
        data.pop("music", None)


def _merge_track(tracks: dict[str, dict[str, Any]], key: str, info: Optional[Mapping[str, Any]],
                 replace: bool) -> None:
    if info is None:
        tracks.pop(key, None)
        return
    entry = {} if replace else dict(tracks.get(key) or {})
    for name, value in info.items():
        if value is None or value is False:
            entry.pop(name, None)
        else:
            entry[name] = value
    if entry:
        tracks[key] = entry
    else:
        tracks.pop(key, None)


class CompanionDataWriter:
    """Coalescing, thread-safe updater of one AddOns folder's ``Data.lua``.

    Every change is validated when it is made (bad input raises :class:`GameDataError` right
    away) and queued; a flush re-reads ``Data.lua``, applies the queued changes in order and
    writes the result atomically -- at most once per ``min_interval`` seconds, on a background
    timer (``schedule``), so callers (the engine thread) never wait for the disk. ``clock``
    measures the interval; ``now`` gives the ``written`` time; both are injectable for tests.
    """

    def __init__(self, addons_dir: Union[str, os.PathLike], *, min_interval: float = MIN_WRITE_INTERVAL,
                 clock: Callable[[], float] = time.monotonic, now: Optional[Callable[[], float]] = None,
                 schedule: Optional[Schedule] = None) -> None:
        self.addons_dir = Path(addons_dir)
        self.min_interval = float(min_interval)
        self._clock = clock
        self._now = now
        self._schedule = schedule or _timer_schedule
        self._lock = threading.RLock()  # the queue and counters
        self._io_lock = threading.Lock()  # serialises flushes
        self._ops: list[Callable[[dict[str, Any]], None]] = []
        self._timer: Any = None
        self._last_write = -math.inf
        self._failures = 0
        self.writes = 0
        self.last_error: Optional[str] = None

    # -- changes
    def record_track(self, key: str, info: Optional[Mapping[str, Any]], *, replace: bool = False) -> None:
        """Merge ``info`` into ``tracks[key]`` (fields set to None / False are removed; an entry
        left empty disappears); ``replace`` = replace the entry; ``info=None`` deletes it."""
        if not isinstance(key, str) or not key:
            raise GameDataError(f"track keys must be non-empty strings, not {key!r}")
        clean_key = _clean_text(key)
        if len(clean_key) > MAX_KEY_CHARS:
            raise GameDataError(f"track key is {len(clean_key)} characters long (limit {MAX_KEY_CHARS})")
        clean: Optional[dict[str, Any]] = None
        if info is not None:
            if not isinstance(info, Mapping):
                raise GameDataError(f"track info must be a dict, not {type(info).__name__}")
            _check_fields(info, TRACK_FIELDS, f"tracks[{clean_key!r}]")
            setting = {k: v for k, v in info.items() if v is not None and v is not False}
            clean = dict(_track(clean_key, setting))
            for k, v in info.items():
                if v is None or v is False:
                    clean[k] = None  # remove this field when merging
        self._queue(lambda d: _merge_track(d.setdefault("tracks", {}), clean_key, clean, replace))

    def set_status(self, **status: Any) -> None:
        """Update status fields (``spotify``, ``youtube``, ``transports`` -- merged per transport)."""
        _check_fields(status, _STATUS_FIELDS, "status")
        for key, value in status.items():
            if value is None:
                continue
            if key == "transports":
                if not isinstance(value, Mapping):
                    raise GameDataError(f"status.transports must be a dict, not {type(value).__name__}")
                _check_fields(value, TRANSPORTS, "status.transports")
                for name, flag in value.items():
                    _bool(flag, f"status.transports.{name}", False)
            else:
                _status({key: value})
        fields = {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in status.items() if v is not None}

        def apply(d: dict[str, Any]) -> None:
            st = d.setdefault("status", {})
            for k, v in fields.items():
                if k == "transports":
                    merged = dict(st.get("transports") or {})
                    merged.update({n: f for n, f in v.items() if f is not None})
                    st["transports"] = merged
                else:
                    st[k] = v
        self._queue(apply)

    def add_import(self, code: str, active: bool = False) -> None:
        """Offer a share code to the addon's one-time import (an existing offer gets ``active``)."""
        entry = _import({"code": code, "active": active}, "import")

        def apply(d: dict[str, Any]) -> None:
            imports = d.setdefault("imports", [])
            for item in imports:
                if item.get("code") == entry["code"]:
                    item["active"] = entry["active"]
                    return
            if len(imports) >= MAX_IMPORTS:
                log.warning("not offering another import: there are already %d", MAX_IMPORTS)
                return
            imports.append(dict(entry))
        self._queue(apply)

    def set_imports(self, imports: Sequence[Mapping[str, Any]]) -> None:
        """Replace the whole ``imports`` list (``[]`` removes it from the file)."""
        clean = _imports(list(imports))
        self._queue(lambda d: d.__setitem__("imports", [dict(i) for i in clean]))

    def set_music(self, spotify: Optional[Mapping[str, Any]] = None, youtube: Optional[Mapping[str, Any]] = None,
                  prefs: Optional[Mapping[str, Any]] = None) -> None:
        """Merge fields into ``music.spotify`` / ``youtube`` / ``prefs`` (DESIGN-v1.2.1 3.10): a part
        left out (None) is unchanged, a field set to None is removed. Validated now."""
        parts: dict[str, Optional[dict[str, Any]]] = {}
        for part, given in (("spotify", spotify), ("youtube", youtube), ("prefs", prefs)):
            if given is None:
                parts[part] = None
                continue
            if not isinstance(given, Mapping):
                raise GameDataError(f"music.{part} must be a dict, not {type(given).__name__}")
            fields, check = _MUSIC_PARTS[part]
            _check_fields(given, fields, f"music.{part}")
            clean: dict[str, Any] = {}
            for name, value in given.items():
                clean[name] = None if value is None else check(name, value)
            parts[part] = clean
        if all(v is None for v in parts.values()):
            return
        self._queue(lambda d: _merge_music(d, parts))

    def record_music_action(self, entry: Mapping[str, Any]) -> None:
        """Add a request result to ``music.actions`` (``{id, action, result, text, at}``): an entry
        with the same (id, action) replaces the old one; the newest 8 are kept."""
        clean = _music_action(entry, "music action")

        def apply(d: dict[str, Any]) -> None:
            music = d.setdefault("music", {})
            music["actions"] = _music_actions(list(music.get("actions") or []) + [dict(clean)])
        self._queue(apply)

    def touch(self) -> None:
        """Rewrite the file with a fresh ``written`` time (the addon shows how long ago the app was seen)."""
        self._queue(lambda d: None)

    # -- writing
    @property
    def pending(self) -> bool:
        with self._lock:
            return bool(self._ops)

    def _queue(self, op: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._ops.append(op)
            self._schedule_locked(self.min_interval)

    def _schedule_locked(self, interval: float) -> None:
        if self._timer is not None:
            return
        delay = max(0.0, self._last_write + interval - self._clock())
        self._timer = self._schedule(delay, self._fire)

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self.flush(_from_timer=True)
        except Exception:  # pragma: no cover - flush() already contains its errors
            log.exception("writing the companion data failed")

    def flush(self, *, _from_timer: bool = False) -> Optional[Path]:
        """Write every queued change now. Returns the path written, or None when nothing was
        pending or the write failed (logged; a locked file is retried a few times). The disk work
        happens outside the queue lock, so callers queueing changes never wait for it."""
        with self._io_lock:  # one flush at a time: each re-reads what the previous one wrote
            with self._lock:
                if not _from_timer and self._timer is not None:
                    try:
                        self._timer.cancel()
                    except Exception:  # pragma: no cover - defensive
                        pass
                    self._timer = None
                if not self._ops:
                    return None
                ops, self._ops = self._ops, []
                self._last_write = self._clock()
            try:
                data = _lenient_current(self.addons_dir)
                for op in ops:
                    op(data)
                data.pop("written", None)
                data.pop("appVersion", None)
                if not data.get("imports"):
                    data.pop("imports", None)
                now = self._now() if self._now is not None else None
                path = write_companion_data(self.addons_dir, data, now=now)
            except GameDataError as exc:  # an unsafe folder: retrying cannot help
                with self._lock:
                    self.last_error = str(exc)
                    self._failures = 0
                log.error("cannot write the companion data: %s", exc)
                return None
            except OSError as exc:  # the game or a scanner holds the file: try again soon
                with self._lock:
                    self.last_error = str(exc)
                    self._failures += 1
                    retry = self._failures <= _MAX_RETRIES
                    if retry:
                        self._ops = ops + self._ops
                        self._schedule_locked(_RETRY_DELAY)
                    else:
                        self._failures = 0
                if retry:
                    log.warning("companion data not written yet (%s); retrying", exc)
                else:
                    log.error("giving up writing the companion data: %s", exc)
                return None
            with self._lock:
                self._failures = 0
                self.last_error = None
                self.writes += 1
            return path


_writers: dict[str, CompanionDataWriter] = {}
_writers_lock = threading.Lock()
_atexit_registered = False


def writer_for(addons_dir: Union[str, os.PathLike]) -> CompanionDataWriter:
    """The shared :class:`CompanionDataWriter` of ``addons_dir`` (one per folder, made on first use)."""
    global _atexit_registered
    if not isinstance(addons_dir, (str, os.PathLike)):
        raise GameDataError(f"addons_dir must be a path, not {type(addons_dir).__name__}")
    key = os.path.normcase(os.path.abspath(os.fspath(addons_dir)))
    with _writers_lock:
        w = _writers.get(key)
        if w is None:
            w = _writers[key] = CompanionDataWriter(addons_dir)
            if not _atexit_registered:
                atexit.register(flush_all)
                _atexit_registered = True
        return w


def record_track(addons_dir: Union[str, os.PathLike], key: str, info: Optional[Mapping[str, Any]], *,
                 replace: bool = False) -> None:
    """Record what the app learned about a song (coalesced; see :meth:`CompanionDataWriter.record_track`).
    ``info`` fields: ``spotify``, ``durationMs``, ``title``, ``artist``, ``notFound``, ``notEmbeddable``."""
    writer_for(addons_dir).record_track(key, info, replace=replace)


def set_status(addons_dir: Union[str, os.PathLike], **status: Any) -> None:
    """Update ``status`` (``spotify=``, ``youtube=``, ``transports={"pixel": bool, "chatlog": bool}``),
    coalesced."""
    writer_for(addons_dir).set_status(**status)


def add_import(addons_dir: Union[str, os.PathLike], code: str, active: bool = False) -> None:
    """Offer a share code for the addon's one-time import (coalesced)."""
    writer_for(addons_dir).add_import(code, active)


def set_imports(addons_dir: Union[str, os.PathLike], imports: Sequence[Mapping[str, Any]]) -> None:
    """Replace the ``imports`` list (coalesced)."""
    writer_for(addons_dir).set_imports(imports)


def set_music(addons_dir: Union[str, os.PathLike], **parts: Any) -> None:
    """Merge ``spotify=`` / ``youtube=`` / ``prefs=`` fields into the ``music`` block (coalesced;
    see :meth:`CompanionDataWriter.set_music`)."""
    unknown = sorted(set(parts) - {"spotify", "youtube", "prefs"})
    if unknown:
        raise GameDataError(f"set_music: unknown part(s) {', '.join(unknown)} (allowed: spotify, youtube, prefs)")
    writer_for(addons_dir).set_music(**parts)


def record_music_action(addons_dir: Union[str, os.PathLike], entry: Mapping[str, Any]) -> None:
    """Record the result of a request the player sent from the game (coalesced; see
    :meth:`CompanionDataWriter.record_music_action`)."""
    writer_for(addons_dir).record_music_action(entry)


def touch(addons_dir: Union[str, os.PathLike]) -> None:
    """Refresh ``written`` (coalesced), e.g. when the game window appears."""
    writer_for(addons_dir).touch()


def flush(addons_dir: Union[str, os.PathLike]) -> Optional[Path]:
    """Write ``addons_dir``'s pending changes now (see :meth:`CompanionDataWriter.flush`)."""
    return writer_for(addons_dir).flush()


def flush_all() -> None:
    """Write every folder's pending changes now (registered with ``atexit``)."""
    with _writers_lock:
        writers = list(_writers.values())
    for w in writers:
        try:
            w.flush()
        except Exception:  # pragma: no cover - flush() contains its errors
            log.exception("flushing the companion data failed")
