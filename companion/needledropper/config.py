"""Companion configuration and derived filesystem paths (docs/SPEC.md 5.1, SPEC-v1.1, DESIGN-v1.2 1.4).

The config lives in ``%APPDATA%\\NeedleDropper\\config.json``. Set the
environment variable ``NEEDLEDROPPER_HOME`` to relocate the whole app dir
(config, caches, tokens, logs) -- tests and portable installs use this.

Only the keys a user changes need to be present in ``config.json``; it is
deep-merged over :data:`DEFAULT_CONFIG` on load and normalised by
:func:`normalize_config` (old values migrated, invalid enum values replaced by
their defaults). Keys this version does not know are kept in the file.

v1.2 keys (the Connect page changes only ``wowRoot``, ``setupComplete``,
``startWithWindows``, ``spotify.clientId`` / ``device`` / ``deviceId`` / ``deviceName`` / ``enabled`` and
``youtube.enabled``; everything else is an advanced knob edited in this file):

* ``startWithWindows`` -- the player's "Start with Windows" choice: ``null``
  until the Connect page's first **Done**, then ``true`` / ``false``. The
  registry value itself (:mod:`.autostart`) is re-checked at every start while
  this is ``true``.
* ``updates.releasesUrl`` -- where the daily update check looks (a GitHub
  repository / releases URL or a releases JSON endpoint). Empty (the default)
  switches the check off. ``updates.checkHours`` -- how often (default 24).
* ``links.addonPage`` -- the addon's download page shown on the Connect page
  when the addon is missing (default: a CurseForge search for NeedleDropper).
* ``packs`` / ``packsDisabled`` -- legacy (v1.1): the companion no longer keeps
  soundtracks; these only feed ``needledropper validate`` / ``test-drop`` and the
  one-time migration of v1.1 packs into the game.

v1.2.1 keys:

* ``spotify.device`` -- where Spotify songs play: ``"app"`` (default) in the app's own
  player, the Spotify Web Playback SDK in the hidden player window (the Connect device
  "NeedleDropper"; it needs Google Chrome and a sign-in from 1.2.1 or later), or
  ``"connect"`` to control another Spotify Connect device (the desktop app, a phone;
  ``spotify.deviceId`` pins the chosen device; ``deviceName`` is its display name).
  Older name-only selections require one exact matching name. An unavailable selection never
  falls back to another device. Empty ID and name enable automatic Connect selection.
* ``music`` -- the player's music choices, **set in game** (Options > AddOns > NeedleDropper >
  Music services; DESIGN-v1.2.1 3.2): the game sends them and the app writes them here, together
  with ``deck.playerCorner`` (the YouTube box position) and ``spotify.restorePrevious`` ("Resume
  my Spotify music afterwards"). ``music.order`` is "Play music with": ``"spotify-youtube"``
  (default), ``"youtube-spotify"``, ``"spotify"``, ``"youtube"`` or ``"none"``; ``music.fade``
  (default ``true``) fades songs in and out (``fadeInMs`` / ``fadeOutMs``; ``false`` starts and
  stops them at once); ``music.fromGame`` is ``true`` once the game has sent its choices (from
  then on the game's fade choice also rules Spotify's own ``spotify.fade``). Values edited here
  are replaced by the game's at its next choice.

v1.1 keys:

* ``deck.autoOpen`` -- the small YouTube player window: ``"youtube"`` (default) and
  ``"always"`` (kept for old configs; the same now) create it once, hidden, when the app
  starts while the game is not in front, and show it -- always on top at
  ``deck.playerCorner``, never taking the focus -- only while a YouTube song plays
  (real-client findings #8 / #13: a window created during play takes the focus, so none
  is created then); ``"never"`` never creates it. Old booleans migrate: ``true`` ->
  ``"youtube"``, ``false`` -> ``"never"``. The window is an Edge or Chrome app window;
  the default browser is never used for playback.
* ``deck.playerCorner`` -- the corner of the game's monitor the player window shows in:
  ``"topright"`` (default), ``"topleft"``, ``"bottomright"`` or ``"bottomleft"``.
* ``deck.playerPosition`` -- optional ``"x,y"`` screen position of the player window
  (overrides ``deck.playerCorner``).
* ``deck.panelOnStart`` -- open the Connect page when the app starts without a
  command: ``"first-run"`` (default: until the Connect page's **Done** has been
  clicked with this version), ``"always"`` or ``"never"``.
* ``setupComplete`` -- set by the Connect page's **Done**.
* ``transport.chatlog.match`` -- ``"strict"`` (default: only system lines, so
  other players cannot fake a record by typing it in chat) or ``"anywhere"``.
* ``spotify.enabled`` / ``local.enabled`` / ``youtube.enabled`` -- the panel's
  per-service switches; ``providers`` keeps the order of all services.
* ``packsDisabled`` -- packs the user switched off in the panel (kept so they
  can be switched on again). Packs bundled with the exe are stored as
  ``builtin:<id>`` (``packs/<id>.json``).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import atomicio as _atomicio, resources

#: Source checkout root (``D:\\NeedleDropper``); see :mod:`.resources` for bundled files.
REPO_ROOT = resources.repo_root()
#: Addon sources copied by ``install-addon`` (bundled into the exe when frozen).
REPO_ADDON_DIR = resources.addon_source_dir()
#: Starter pack (bundled into the exe when frozen).
DEFAULT_PACK = resources.default_pack_path()

APP_NAME = "NeedleDropper"
ADDON_NAME = "NeedleDropper"
LOCAL_ADDON_NAME = "NeedleDropper_Local"
HOME_ENV = "NEEDLEDROPPER_HOME"
CONFIG_FILENAME = "config.json"
DEFAULT_WOW_ROOT = "C:\\Program Files (x86)\\World of Warcraft"
DEFAULT_FLAVOR = "_forever_"
#: Reference to a pack that ships with the companion, ``builtin:<id>`` = ``packs/<id>.json``
#: (portable across exe runs; the same reference form as docs/SPEC-v1.2.md).
BUNDLED_PREFIX = "builtin:"

AUTO_OPEN_MODES = ("youtube", "always", "never")
#: ``spotify.device``: play in the app's own player (the Web Playback SDK) or control a Connect device.
SPOTIFY_DEVICE_MODES = ("app", "connect")
#: ``music.order``: "Play music with" (the same names and order as protocol2.PREFS_ORDERS).
MUSIC_ORDERS = ("spotify-youtube", "youtube-spotify", "spotify", "youtube", "none")
PLAYER_CORNERS = ("topright", "topleft", "bottomright", "bottomleft")
PANEL_ON_START_MODES = ("first-run", "always", "never")
CHATLOG_MATCH_MODES = ("strict", "anywhere")
PIXEL_METHODS = ("auto", "screen", "printwindow")
BROWSERS = ("edge", "chrome", "default", "none")

DEFAULT_CONFIG: dict[str, Any] = {
    "wowRoot": DEFAULT_WOW_ROOT,
    "flavorDir": DEFAULT_FLAVOR,
    "packs": [str(DEFAULT_PACK)],
    "packsDisabled": [],
    "providers": ["spotify", "local", "youtube"],
    "spotify": {"enabled": True, "clientId": "", "redirectPort": 8321, "device": "app", "deviceId": "", "deviceName": "", "fade": True,
                "restorePrevious": True, "volume": None},
    "local": {"enabled": True, "mediaDir": "%APPDATA%\\NeedleDropper\\media"},
    "youtube": {"enabled": True},
    "deck": {"port": 8322, "autoOpen": "youtube", "browser": "edge", "playerPosition": "",
             "playerCorner": "topright", "panelOnStart": "first-run"},
    "music": {"order": "spotify-youtube", "fade": True, "fromGame": False},
    "transport": {"pixel": {"enabled": True, "method": "auto", "hz": 4, "regionW": 512, "regionH": 48},
                  "chatlog": {"enabled": True, "pollMs": 250, "match": "strict"}},
    "fadeInMs": 2500,
    "fadeOutMs": 3000,
    "setupComplete": False,
    "startWithWindows": None,
    "updates": {"releasesUrl": "", "checkHours": 24},
    "links": {"addonPage": "https://www.curseforge.com/wow/search?search=NeedleDropper"},
}

KNOWN_PROVIDERS = ("spotify", "local", "youtube")

#: Rotating log: ``<app dir>\\logs\\needledropper.log`` plus 4 older files, 1 MB each (5 x 1 MB).
LOG_DIRNAME = "logs"
LOG_FILENAME = "needledropper.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 4
UPDATE_CHECK_HOURS_RANGE = (1, 24 * 14)


def is_retired_provider(name: Any) -> bool:
    """Apple Music (v1.0, any spelling) was removed in v1.1: Windows offers no way to control it.
    Such entries are dropped silently from ``providers``."""
    n = re.sub(r"[\s_\-]+", "", str(name)).lower()
    return n.startswith("apple")


class ConfigError(ValueError):
    """config.json exists but cannot be used."""


# ------------------------------------------------------------------ dirs ----

def app_dir() -> Path:
    """The companion's data dir: ``$NEEDLEDROPPER_HOME`` or ``%APPDATA%\\NeedleDropper``."""
    home = os.environ.get(HOME_ENV, "").strip()
    if home:
        return Path(os.path.expanduser(home))
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".needledropper"


def config_path() -> Path:
    return app_dir() / CONFIG_FILENAME


_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")
_APPDATA_APP_RE = re.compile(r"^%APPDATA%[\\/]+NeedleDropper(?=$|[\\/])", re.IGNORECASE)


def expand_path(value: str | os.PathLike[str]) -> Path:
    """Expand ``%VAR%``, ``$VAR`` and ``~`` in a configured path.

    ``%APPDATA%\\NeedleDropper`` is mapped onto :func:`app_dir` so that
    ``NEEDLEDROPPER_HOME`` relocates every default path consistently.
    Unknown ``%VAR%`` references are left untouched.
    """
    s = os.fspath(value).strip()
    m = _APPDATA_APP_RE.match(s)
    if m:
        rest = s[m.end():].lstrip("\\/")
        base = app_dir()
        return base / Path(rest.replace("\\", os.sep)) if rest else base
    s = _VAR_RE.sub(lambda mm: os.environ.get(mm.group(1), mm.group(0)), s)
    s = os.path.expandvars(s)
    s = os.path.expanduser(s)
    return Path(s)


# ---------------------------------------------------------------- config ----

def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict: ``override`` merged recursively over ``base``.

    Nested dicts merge key by key; any other value (lists included) replaces.
    """
    out = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def _section(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    sec = cfg.get(key)
    if not isinstance(sec, dict):
        sec = copy.deepcopy(DEFAULT_CONFIG.get(key) or {})
        cfg[key] = sec
    return sec


def _enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    return default


def normalize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Migrate old values and replace invalid enum values in a merged config (in place).

    * ``deck.autoOpen``: ``true`` -> ``"youtube"``, ``false`` -> ``"never"``;
    * unknown ``deck.autoOpen`` / ``deck.panelOnStart`` / ``transport.chatlog.match``
      values fall back to their defaults;
    * retired providers (Apple Music) are removed from ``providers``.

    Returns ``cfg``. The defaults themselves are a fixed point.
    """
    deck = _section(cfg, "deck")
    auto = deck.get("autoOpen")
    if isinstance(auto, bool):
        deck["autoOpen"] = "youtube" if auto else "never"
    else:
        deck["autoOpen"] = _enum(auto, AUTO_OPEN_MODES, "youtube")
    deck["panelOnStart"] = _enum(deck.get("panelOnStart"), PANEL_ON_START_MODES, "first-run")
    if deck.get("playerPosition") is None:
        deck["playerPosition"] = ""
    corner = deck.get("playerCorner")
    deck["playerCorner"] = _enum(corner.strip().lower().replace("-", "") if isinstance(corner, str) else corner,
                                 PLAYER_CORNERS, "topright")
    spotify = _section(cfg, "spotify")
    spotify["device"] = _enum(spotify.get("device"), SPOTIFY_DEVICE_MODES, "app")
    music = _section(cfg, "music")
    music["order"] = _enum(music.get("order"), MUSIC_ORDERS, "spotify-youtube")
    for key, default in (("fade", True), ("fromGame", False)):
        if not isinstance(music.get(key), bool):
            music[key] = default
    transport = _section(cfg, "transport")
    chat = transport.get("chatlog")
    if isinstance(chat, dict):
        chat["match"] = _enum(chat.get("match"), CHATLOG_MATCH_MODES, "strict")
    provs = cfg.get("providers")
    if isinstance(provs, list):
        cfg["providers"] = [p for p in provs if not is_retired_provider(p)]
    if not isinstance(cfg.get("packsDisabled"), list):
        cfg["packsDisabled"] = []
    cfg["setupComplete"] = bool(cfg.get("setupComplete", False))
    sww = cfg.get("startWithWindows")
    cfg["startWithWindows"] = sww if isinstance(sww, bool) else None
    updates = _section(cfg, "updates")
    url = updates.get("releasesUrl")
    updates["releasesUrl"] = url.strip() if isinstance(url, str) else ""
    hours = updates.get("checkHours")
    lo, hi = UPDATE_CHECK_HOURS_RANGE
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours != hours:
        hours = DEFAULT_CONFIG["updates"]["checkHours"]
    updates["checkHours"] = int(min(hi, max(lo, hours)))
    links = _section(cfg, "links")
    page = links.get("addonPage")
    if not (isinstance(page, str) and re.match(r"(?i)^https://[^\s/]+", page.strip())):
        page = DEFAULT_CONFIG["links"]["addonPage"]
    links["addonPage"] = page.strip()
    return cfg


def load_config(path: Optional[str | os.PathLike[str]] = None) -> dict[str, Any]:
    """Load ``config.json`` deep-merged over the defaults and normalised. Missing file -> defaults."""
    p = Path(path) if path else config_path()
    if not p.exists():
        return default_config()
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{p}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc
    except OSError as exc:
        raise ConfigError(f"{p}: cannot read config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{p}: top level must be a JSON object")
    return normalize_config(deep_merge(DEFAULT_CONFIG, raw))


def provider_enabled(cfg: dict[str, Any], name: str) -> bool:
    """The panel's per-service switch (``<name>.enabled``, default on)."""
    sec = cfg.get(name)
    return not (isinstance(sec, dict) and sec.get("enabled") is False)


def _int_ms(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def effective_fades(cfg: dict[str, Any]) -> tuple[int, int]:
    """``(fade_in_ms, fade_out_ms)`` for the engine: ``fadeInMs`` / ``fadeOutMs`` while
    ``music.fade`` (the game's "Fade songs in and out") is on, ``(0, 0)`` when it is off."""
    music = cfg.get("music") if isinstance(cfg.get("music"), dict) else {}
    if music.get("fade") is False:
        return 0, 0
    return (_int_ms(cfg.get("fadeInMs", 2500), 2500), _int_ms(cfg.get("fadeOutMs", 3000), 3000))


def music_choices(cfg: dict[str, Any]) -> dict[str, Any]:
    """The music choices in effect (DESIGN-v1.2.1 3.10 ``music.prefs``): ``order``, ``corner``,
    ``fade``, ``resume`` and ``fromGame`` (whether the game has sent them yet)."""
    music = cfg.get("music") if isinstance(cfg.get("music"), dict) else {}
    deck = cfg.get("deck") if isinstance(cfg.get("deck"), dict) else {}
    spotify = cfg.get("spotify") if isinstance(cfg.get("spotify"), dict) else {}
    order = music.get("order")
    corner = deck.get("playerCorner")
    return {"order": order if order in MUSIC_ORDERS else "spotify-youtube",
            "corner": corner if corner in PLAYER_CORNERS else "topright",
            "fade": music.get("fade") is not False,
            "resume": spotify.get("restorePrevious") is not False,
            "fromGame": music.get("fromGame") is True}


def parse_position(value: Any) -> Optional[tuple[int, int]]:
    """``"x,y"`` -> ``(x, y)``; ``""``/None/invalid -> None."""
    if not isinstance(value, str) or not value.strip():
        return None
    m = re.fullmatch(r"\s*(-?\d{1,5})\s*,\s*(-?\d{1,5})\s*", value)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def save_config(cfg: dict[str, Any], path: Optional[str | os.PathLike[str]] = None) -> Path:
    """Write the full config atomically (pretty-printed). Returns the path written."""
    p = Path(path) if path else config_path()
    atomic_write_text(p, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return p


def update_config_file(updates: dict[str, Any], path: Optional[str | os.PathLike[str]] = None) -> Path:
    """Merge ``updates`` into the user's config.json, keeping only keys they set.

    Unlike :func:`save_config` this does not freeze every default into the
    file, so future default changes (e.g. the repo's pack path) still apply.
    """
    p = Path(path) if path else config_path()
    raw: dict[str, Any] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{p}: cannot update config: {exc}") from exc
        if isinstance(loaded, dict):
            raw = loaded
    atomic_write_text(p, json.dumps(deep_merge(raw, updates), indent=2, ensure_ascii=False) + "\n")
    return p


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes, *, retries: int = 10,
                       delay: float = 0.2) -> Path:
    """Write ``data`` via a temp file in the same dir + ``os.replace`` (:mod:`.atomicio`).

    ``os.replace`` is retried on ``PermissionError`` (Windows: file briefly
    locked by an editor, antivirus or the game) up to ``retries`` times; a
    cross-device refusal (WinError 17, a virtualised ``%APPDATA%``) falls back to
    writing the file in place.
    """
    return _atomicio.atomic_write_bytes(path, data, retries=retries, delay=delay)


def atomic_write_text(path: str | os.PathLike[str], text: str, **kw: Any) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"), **kw)


# ----------------------------------------------------------------- paths ----

@dataclass(frozen=True)
class Paths:
    """Every filesystem location the companion touches, derived from config."""

    wow_root: Path
    flavor_root: Path  # <wow_root>\_forever_
    addons_dir: Path  # <flavor_root>\Interface\AddOns
    addon_dir: Path  # <addons_dir>\NeedleDropper
    local_addon_dir: Path  # <addons_dir>\NeedleDropper_Local (user media; never deleted)
    chat_log: Path  # <flavor_root>\Logs\WoWChatLog.txt
    game_exe: Path  # <flavor_root>\Wow.exe
    app_dir: Path  # %APPDATA%\NeedleDropper (or $NEEDLEDROPPER_HOME)
    cache_dir: Path  # <app_dir>\cache
    maps_dir: Path  # <app_dir>\maps

    @property
    def config_file(self) -> Path:
        return self.app_dir / CONFIG_FILENAME

    @property
    def resolved_cache(self) -> Path:
        """Track resolution cache keyed by drop id (written by the Spotify provider)."""
        return self.cache_dir / "resolved.json"

    @property
    def token_file(self) -> Path:
        return self.app_dir / "spotify_token.bin"

    @property
    def logs_dir(self) -> Path:
        """``<app_dir>\\logs`` (the tray's "Open log folder")."""
        return self.app_dir / LOG_DIRNAME

    @property
    def log_file(self) -> Path:
        """The rotating log, ``<app_dir>\\logs\\needledropper.log`` (5 x 1 MB)."""
        return self.logs_dir / LOG_FILENAME

    @property
    def install_receipt(self) -> Path:
        """What the "Install addon" fallback copied last (see :func:`write_install_receipt`)."""
        return self.app_dir / "addon-install.json"

    @property
    def update_state(self) -> Path:
        """When the update check last ran and what it found."""
        return self.app_dir / "update-check.json"

    @property
    def installed_map_json(self) -> Path:
        """v1.1 only (retired with Map.lua); kept for the legacy-install check."""
        return self.addon_dir / "Map.json"

    @property
    def local_media_dir(self) -> Path:
        """Where in-game local files live (``Interface\\AddOns\\NeedleDropper_Local\\media``)."""
        return self.local_addon_dir / "media"


def paths_from_config(cfg: dict[str, Any]) -> Paths:
    wow_root = expand_path(cfg.get("wowRoot") or DEFAULT_CONFIG["wowRoot"])
    flavor_root = wow_root / (cfg.get("flavorDir") or DEFAULT_CONFIG["flavorDir"])
    addons_dir = flavor_root / "Interface" / "AddOns"
    app = app_dir()
    return Paths(
        wow_root=wow_root,
        flavor_root=flavor_root,
        addons_dir=addons_dir,
        addon_dir=addons_dir / ADDON_NAME,
        local_addon_dir=addons_dir / LOCAL_ADDON_NAME,
        chat_log=flavor_root / "Logs" / "WoWChatLog.txt",
        game_exe=flavor_root / "Wow.exe",
        app_dir=app,
        cache_dir=app / "cache",
        maps_dir=app / "maps",
    )


def media_dir_from_config(cfg: dict[str, Any]) -> Path:
    """``local.mediaDir`` expanded (companion-side local audio files)."""
    local = cfg.get("local") or {}
    return expand_path(local.get("mediaDir") or DEFAULT_CONFIG["local"]["mediaDir"])


_MEI_DIR_RE = re.compile(r"^_MEI\w*$", re.IGNORECASE)


def _builtin_pack_path(ref_id: str) -> Path:
    name = ref_id.strip().lstrip("\\/")
    if not name.lower().endswith(".json"):
        name += ".json"
    return resources.packs_dir() / name


def expand_pack_entry(entry: Any) -> Path:
    """One ``packs`` entry -> a path.

    ``builtin:<id>`` names a pack that ships with the companion,
    ``packs/<id>.json`` in the source checkout or the exe (see
    :func:`portable_pack_entry`). An absolute path into an old, now deleted
    PyInstaller unpack folder (``...\\_MEIxxxx\\packs\\<file>``) is repaired to
    the current bundle when it has the same file name.
    """
    s = str(entry).strip()
    if s.lower().startswith(BUNDLED_PREFIX):
        return _builtin_pack_path(s[len(BUNDLED_PREFIX):])
    p = expand_path(s)
    try:
        missing = not p.exists()
    except OSError:
        missing = True
    if missing and p.parent.name.lower() == "packs" and _MEI_DIR_RE.match(p.parent.parent.name or ""):
        cand = resources.packs_dir() / p.name
        if cand.is_file():
            return cand
    return p


def portable_pack_entry(path: str | os.PathLike[str]) -> str:
    """How a pack path is written to config.json: ``builtin:<id>`` for packs that ship
    with the exe (it unpacks them to a different temp folder every run), the path
    as given otherwise."""
    s = os.fspath(path).strip()
    if s.lower().startswith(BUNDLED_PREFIX):
        return BUNDLED_PREFIX + _builtin_pack_path(s[len(BUNDLED_PREFIX):]).stem
    try:
        p = Path(s).resolve()
        base = resources.packs_dir().resolve()
    except OSError:
        return s
    if p.parent == base and p.suffix.lower() == ".json" and resources.is_frozen():
        return BUNDLED_PREFIX + p.stem
    return s


def same_pack(a: Any, b: Any) -> bool:
    """Whether two ``packs`` entries name the same file."""
    try:
        return os.path.normcase(str(expand_pack_entry(a).resolve())) == \
            os.path.normcase(str(expand_pack_entry(b).resolve()))
    except OSError:
        return str(a).strip() == str(b).strip()


def pack_paths_from_config(cfg: dict[str, Any]) -> list[Path]:
    """Configured (enabled) packs: files, or directories meaning every ``*.json`` inside."""
    out: list[Path] = []
    for entry in cfg.get("packs") or []:
        p = expand_pack_entry(entry)
        if p.is_dir():
            out.extend(sorted(p.glob("*.json")))
        else:
            out.append(p)
    return out


# ------------------------------------------------------- game detection ----

ProcessInfo = tuple[str, Optional[str]]  # (image name, full path or None if unknown)


def _norm(p: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(p)))


def _list_processes_toolhelp() -> Optional[list[ProcessInfo]]:
    """Enumerate processes with CreateToolhelp32Snapshot (Windows, no extra deps)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover
        return None

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            return None
        procs: list[ProcessInfo] = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                name = entry.szExeFile
                full: Optional[str] = None
                if name.lower() == "wow.exe":  # only resolve paths we care about
                    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, entry.th32ProcessID)
                    if h:
                        try:
                            buf = ctypes.create_unicode_buffer(32768)
                            size = wintypes.DWORD(len(buf))
                            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                                full = buf.value
                        finally:
                            k32.CloseHandle(h)
                procs.append((name, full))
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return procs
    except (OSError, AttributeError, ValueError):
        return None


def _list_processes_tasklist() -> Optional[list[ProcessInfo]]:
    """Fallback: parse ``tasklist /FO CSV /NH`` (names only)."""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=10, creationflags=flags).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    procs: list[ProcessInfo] = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('"'):
            procs.append((line.split('","', 1)[0].strip('"'), None))
    return procs


def list_processes() -> Optional[list[ProcessInfo]]:
    """Running processes as ``(image name, full path or None)``; None if unknown."""
    procs = _list_processes_toolhelp()
    if procs is None:
        procs = _list_processes_tasklist()
    return procs


def is_game_running(paths: Paths, *, processes: Optional[Iterable[ProcessInfo]] = None) -> bool:
    """True if this install's ``_forever_\\Wow.exe`` is running.

    A ``Wow.exe`` whose full path can be read only counts when it is this
    flavor's executable (so a running Retail client doesn't block installs);
    one whose path cannot be read counts conservatively.
    """
    procs = list(processes) if processes is not None else list_processes()
    if not procs:
        return False
    exe_name = paths.game_exe.name.lower()
    want = _norm(paths.game_exe)
    for name, full in procs:
        if (name or "").lower() != exe_name:
            continue
        if full is None or _norm(full) == want:
            return True
    return False


# --------------------------------------------------------- WoW detection ----

#: Registry keys whose ``InstallPath`` value names the WoW install (tried in order).
WOW_REGISTRY_KEYS: tuple[tuple[str, str], ...] = (
    ("HKLM", r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft"),
    ("HKLM", r"SOFTWARE\Blizzard Entertainment\World of Warcraft"),
    ("HKCU", r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft"),
    ("HKCU", r"SOFTWARE\Blizzard Entertainment\World of Warcraft"),
)
WOW_REGISTRY_VALUE = "InstallPath"

#: ``read_registry(hive, subkey, value_name) -> str | None`` (hive is "HKLM" or "HKCU").
RegistryReader = Callable[[str, str, str], Optional[str]]

#: WoW flavor folders: _retail_, _classic_, _classic_era_, _ptr_, _xptr_, _beta_, _forever_, ...
_FLAVOR_DIR_RE = re.compile(r"^_[A-Za-z0-9_]+_$")


def read_registry_value(hive: str, subkey: str, name: str) -> Optional[str]:
    """A string value from the Windows registry, or None (missing key, not Windows)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - always present on Windows
        return None
    roots = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    root = roots.get(hive.upper())
    if root is None:
        return None
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if not isinstance(value, str) or kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        return None
    return os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value


def normalize_wow_root(path: str | os.PathLike[str]) -> Path:
    """The WoW root for an ``InstallPath`` that may name the root, a flavor folder
    (``...\\World of Warcraft\\_retail_\\``) or an exe inside one."""
    s = os.fspath(path).strip().strip('"').strip()
    p = Path(s.rstrip("\\/") or s)
    if p.suffix.lower() == ".exe":
        p = p.parent
    if _FLAVOR_DIR_RE.match(p.name):
        p = p.parent
    return p


def wow_install_score(root: str | os.PathLike[str], flavor: str = DEFAULT_FLAVOR) -> int:
    """2 = ``<root>\\<flavor>\\Wow.exe`` exists, 1 = only the flavor folder exists, 0 = neither."""
    try:
        fl = Path(root) / (flavor or DEFAULT_FLAVOR)
        if (fl / "Wow.exe").is_file():
            return 2
        if fl.is_dir():
            return 1
    except OSError:
        pass
    return 0


@dataclass(frozen=True)
class WowDetection:
    """Result of :func:`detect_wow_root`."""

    root: Optional[Path]  # the WoW root that contains <flavor> (None: nothing found)
    source: str  # "registry" | "default" | "none"
    detail: str  # e.g. r"HKLM\SOFTWARE\...\World of Warcraft\InstallPath"
    tried: tuple[tuple[str, str, int], ...] = field(default_factory=tuple)  # (where, path, score)

    @property
    def found(self) -> bool:
        return self.root is not None


def detect_wow_root(flavor: str = DEFAULT_FLAVOR, *, read_registry: Optional[RegistryReader] = None,
                    default: str | os.PathLike[str] = DEFAULT_WOW_ROOT,
                    keys: Iterable[tuple[str, str]] = WOW_REGISTRY_KEYS) -> WowDetection:
    """Find the World of Warcraft folder that contains ``<flavor>`` (``_forever_``).

    Reads ``InstallPath`` from the Blizzard registry keys (64-bit view first,
    then the plain key, HKLM before HKCU), normalises flavor-folder paths to
    the root, and falls back to the default install path. A root whose
    ``<flavor>\\Wow.exe`` exists wins over one that only has the folder.
    ``read_registry`` is injectable for tests.
    """
    reader = read_registry or read_registry_value
    tried: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    candidates: list[tuple[str, str, Path]] = []
    for hive, subkey in keys:
        try:
            raw = reader(hive, subkey, WOW_REGISTRY_VALUE)
        except Exception:  # a broken reader must not break detection
            raw = None
        if raw and raw.strip():
            where = "\\".join((hive, subkey, WOW_REGISTRY_VALUE))
            candidates.append(("registry", where, normalize_wow_root(raw)))
    candidates.append(("default", "default install folder", Path(default)))
    best: Optional[tuple[str, str, Path, int]] = None
    for source, where, root in candidates:
        key = os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        score = wow_install_score(root, flavor)
        tried.append((where, str(root), score))
        if score and (best is None or score > best[3]):
            best = (source, where, root, score)
            if score == 2:
                break
    if best is None:
        return WowDetection(None, "none", "", tuple(tried))
    return WowDetection(best[2], best[0], best[1], tuple(tried))


# ------------------------------------------------------------ addon info ----

_TOC_VERSION_RE = re.compile(r"^\s*##\s*Version\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_TOC_FIELD_RE = re.compile(r"^[ \t]*##[ \t]*([A-Za-z][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
_VERSION_NUM_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?(?=$|[\s+-])")

#: TOC file suffixes the WoW: Forever client looks for, most specific first: the game type
#: (``_Forever`` / its alias ``_Camelot``), the family (``_Mainline``), then the plain
#: ``<Addon>.toc`` (warcraft.wiki.gg "TOC format"; the forever branch's own TOCs use the
#: ``[AllowLoadGameType camelot]`` / ``mainline`` names, e.g.
#: ``Interface/AddOns/Blizzard_Minimap/Blizzard_Minimap.toc``).
TOC_SUFFIXES = ("_Forever", "_Camelot", "_Mainline", "")

#: Project ids the addon sites put in a TOC for their addon managers to match on
#: (the BigWigs packager reads them to pick upload targets; WowUp matches on them).
SITE_TOC_FIELDS = (("X-Curse-Project-ID", "CurseForge"), ("X-Wago-ID", "Wago"), ("X-WoWI-ID", "WoWInterface"))
#: Folders an addon manager keeps in ``Interface\\AddOns`` for an installation it manages:
#: WowUp writes ``wowup_data_addon\\{data.lua,wowup_data_addon.toc}`` there for every
#: installation where its "WowUp" companion addon is installed (WowUp's
#: ``wowup-addon.service.ts``). CurseForge and the Wago app leave no marker on disk (they
#: match folders by file fingerprints and TOC ids), so an addon this app did not install
#: counts as someone else's.
MANAGER_MARKER_DIRS = (("wowup_data_addon", "WowUp"), ("WowUp", "WowUp"))
#: v1.1 files the v1.2 addon no longer has; an update by this app removes them.
RETIRED_ADDON_FILES = ("Map.lua", "Map.json", "Protocol.lua")


def read_toc_version(toc: str | os.PathLike[str]) -> Optional[str]:
    """The ``## Version:`` of an addon .toc file, or None if missing/unreadable."""
    try:
        text = Path(toc).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    m = _TOC_VERSION_RE.search(text)
    return m.group(1) if m else None


def read_toc_fields(toc: str | os.PathLike[str]) -> dict[str, str]:
    """The ``## Key: Value`` metadata lines of a .toc file (the first of each key wins; {} if unreadable)."""
    try:
        text = Path(toc).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for key, value in _TOC_FIELD_RE.findall(text):
        out.setdefault(key, value)
    return out


def addon_toc(addon_dir: str | os.PathLike[str], name: str = ADDON_NAME) -> Optional[Path]:
    """The TOC the WoW: Forever client would load for ``<addon_dir>`` (see :data:`TOC_SUFFIXES`), or None."""
    for suffix in TOC_SUFFIXES:
        p = Path(addon_dir) / f"{name}{suffix}.toc"
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def version_key(version: Any) -> Optional[tuple[int, int, int, int]]:
    """``"1.2.0"`` / ``"v1.2"`` -> ``(1, 2, 0, 0)``; None for anything that is not a version
    (``@project-version@`` in an unpackaged checkout, empty, ...)."""
    if not isinstance(version, str):
        return None
    m = _VERSION_NUM_RE.match(version)
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())  # type: ignore[return-value]


def _sha256_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _receipt_key(addon_dir: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(addon_dir)))


def read_install_receipts(paths: "Paths") -> dict[str, dict[str, Any]]:
    """Every install receipt, keyed by the normalised addon folder ({} when none / unreadable)."""
    try:
        raw = json.loads(paths.install_receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    receipts = raw.get("receipts") if isinstance(raw, dict) else None
    if not isinstance(receipts, dict):
        return {}
    return {k: v for k, v in receipts.items() if isinstance(k, str) and isinstance(v, dict)}


def read_install_receipt(paths: "Paths") -> Optional[dict[str, Any]]:
    """The receipt for ``paths.addon_dir`` (what this app copied there last), or None."""
    return read_install_receipts(paths).get(_receipt_key(paths.addon_dir))


def write_install_receipt(paths: "Paths", *, toc: Path, version: Optional[str], files: Iterable[str]) -> Path:
    """Record that this app installed the addon into ``paths.addon_dir``.

    Kept in the app dir (never inside the addon folder, which addon managers own): the
    TOC's name and SHA-256 (an addon manager's update changes it), the version and the
    relative paths of the files copied.
    """
    receipts = read_install_receipts(paths)
    receipts[_receipt_key(paths.addon_dir)] = {
        "addonDir": str(paths.addon_dir), "toc": toc.name, "tocSha256": _sha256_file(toc), "version": version,
        "files": sorted({str(f).replace("\\", "/") for f in files}),
        "installedAt": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
    return atomic_write_text(paths.install_receipt, json.dumps({"receipts": receipts}, indent=2) + "\n")


@dataclass(frozen=True)
class AddonStatus:
    """What is installed in ``Interface\\AddOns\\NeedleDropper`` and who looks after it.

    ``owner``: ``"missing"`` (no TOC the client would load), ``"companion"`` (this app
    installed it, per its receipt, or it is a v1.0/v1.1 addon from the Map.lua era, which
    was only ever installed by the companion or by hand), ``"manager"`` (an addon manager
    or addon-site download: ``manager`` names it) or ``"other"`` (installed by hand or by
    a manager that leaves no trace). ``managers`` lists addon managers seen in the AddOns
    folder at all (e.g. ``["WowUp"]``), installed or not.
    """

    installed: bool
    owner: str
    toc: Optional[Path] = None
    version: Optional[str] = None
    interface: Optional[str] = None
    manager: Optional[str] = None
    managers: tuple[str, ...] = ()
    legacy: bool = False


def addon_managers_in(addons_dir: str | os.PathLike[str]) -> tuple[str, ...]:
    """Addon managers that leave a folder in ``Interface\\AddOns`` (see :data:`MANAGER_MARKER_DIRS`)."""
    found: list[str] = []
    for folder, name in MANAGER_MARKER_DIRS:
        try:
            if (Path(addons_dir) / folder).is_dir() and name not in found:
                found.append(name)
        except OSError:
            continue
    return tuple(found)


def addon_status(paths: "Paths") -> AddonStatus:
    """Inspect the installed addon (see :class:`AddonStatus`); never writes anything."""
    managers = addon_managers_in(paths.addons_dir)
    toc = addon_toc(paths.addon_dir)
    if toc is None:
        return AddonStatus(False, "missing", managers=managers, manager=managers[0] if managers else None)
    fields = read_toc_fields(toc)
    version = fields.get("Version") or None
    interface = fields.get("Interface") or None
    site = next((label for key, label in SITE_TOC_FIELDS if fields.get(key)), None)
    vk = version_key(version)
    legacy = bool(vk is not None and vk < (1, 2, 0, 0) and (paths.addon_dir / "Map.lua").is_file())
    receipt = read_install_receipt(paths)
    ours = bool(receipt and receipt.get("toc") == toc.name and receipt.get("tocSha256")
                and receipt.get("tocSha256") == _sha256_file(toc))
    if ours:
        owner, manager = "companion", None
    elif legacy and not managers and not site:
        owner, manager = "companion", None
    elif managers:
        owner, manager = "manager", managers[0]
    else:
        # A site's project id in the TOC (X-Curse-Project-ID, ...) no longer names a manager: the
        # release TOC itself carries NeedleDropper's CurseForge id, so a hand-unpacked zip has it too.
        owner, manager = "other", None
    return AddonStatus(True, owner, toc=toc, version=version, interface=interface, manager=manager,
                       managers=managers, legacy=legacy)
