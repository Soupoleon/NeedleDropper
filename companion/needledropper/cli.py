"""Command line: ``NeedleDropper.exe [<command>]`` / ``python -m needledropper [<command>]``.

Without a command (double-clicking ``NeedleDropper.exe``, and at sign-in) the companion
runs in the **tray** (DESIGN-v1.2 1.4): one instance only (a second start finds the first
through ``GET /healthz`` and just opens its Connect page), the Connect page opens on the
first run (``deck.panelOnStart = "first-run"``) and from the tray, notifications appear
only when they matter, and everything is logged to the rotating
``%APPDATA%\\NeedleDropper\\logs\\needledropper.log`` (5 x 1 MB).

Commands: ``run`` (the same companion in the foreground, logging to the console),
``setup`` (open the Connect page, starting the companion if needed), ``spotify-login``,
``install-addon`` (fallback copy of the bundled addon), ``diag``, ``test-drop``, ``zones``,
``validate`` and, when available, the ``packs`` tools (``commands_packs``). Spotify, the
transports and the packs tools are imported lazily, so the rest keeps working if one of
them is missing or broken. The v1.1 ``build-map`` and ``resolve`` commands are retired:
soundtracks live in game now.
"""
from __future__ import annotations

import argparse
import http.client
import importlib
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import __version__, zones
from .config import (
    KNOWN_PROVIDERS, LOG_BACKUP_COUNT, LOG_MAX_BYTES, REPO_ADDON_DIR, RETIRED_ADDON_FILES, ConfigError, Paths,
    addon_status, addon_toc, atomic_write_bytes, config_path, expand_path, is_game_running, load_config,
    pack_paths_from_config, parse_position, paths_from_config, provider_enabled, read_install_receipt,
    read_toc_version, update_config_file, write_install_receipt,
)

log = logging.getLogger("needledropper")

EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2
Out = Callable[[str], None]

#: Opens a URL in the default browser (the Connect page). Tests replace it.
open_url: Callable[[str], Any] = webbrowser.open
#: Builds the tray icon: ``tray_factory(title, icon_path, menu_items, on_click, **kw)``.
#: None = :class:`needledropper.tray.TrayIcon`. Tests replace it with a recording fake.
tray_factory: Optional[Callable[..., Any]] = None
#: Shows a fatal start-up problem to someone who has no console: ``fatal_dialog(title, text)``.
#: None = :func:`needledropper.tray.message_box`. Tests replace it.
fatal_dialog: Optional[Callable[[str, str], Any]] = None

HEALTH_PATH = "/healthz"
DEFAULT_DECK_PORT = 8322
TOOLTIP_REFRESH_S = 2.0
APP_TITLE = "NeedleDropper"


def say(text: str = "", **kw: Any) -> None:
    """``print`` that is silent when there is no console (the windowed exe)."""
    if sys.stdout is None:
        return
    try:
        print(text, **kw)
    except (OSError, ValueError):
        pass


# ------------------------------------------------------------------ setup ----

class Ctx:
    """Loaded config + derived paths for one command invocation."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.config_file: Optional[Path] = Path(args.config) if getattr(args, "config", None) else None
        self.cfg = load_config(self.config_file)
        self.paths: Paths = paths_from_config(self.cfg)

    def save(self, updates: dict[str, Any]) -> Path:
        p = update_config_file(updates, self.config_file)
        self.cfg = load_config(self.config_file)
        self.paths = paths_from_config(self.cfg)
        return p


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever ``sys.stdout`` currently is (survives stream swaps)."""

    _nd_console = True

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property  # type: ignore[override]
    def stream(self):  # noqa: D401
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        pass

    def emit(self, record: logging.LogRecord) -> None:
        if sys.stdout is not None:
            super().emit(record)


def make_file_handler(paths: Paths, *, verbose: bool = False) -> logging.Handler:
    """The rotating log: ``<app dir>\\logs\\needledropper.log`` + 4 older files of 1 MB (5 x 1 MB)."""
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(paths.log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
                                              encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    return fh


class _LogSession:
    """Console (when there is one) + optional rotating file logging for long-running
    commands; undone on exit so nothing leaks into other code in the same process (tests)."""

    def __init__(self, verbose: bool, paths: Optional[Paths] = None, *, console: bool = True) -> None:
        self.verbose = verbose
        self.paths = paths
        self.console = console
        self.log_file: Optional[Path] = None
        self._added: list[logging.Handler] = []
        self._old_level = logging.NOTSET

    def __enter__(self) -> "_LogSession":
        root = logging.getLogger("needledropper")
        self._old_level = root.level
        root.setLevel(logging.DEBUG)
        if self.console and sys.stdout is not None:
            console = _StdoutHandler()
            console.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
            console.setLevel(logging.DEBUG if self.verbose else logging.INFO)
            root.addHandler(console)
            self._added.append(console)
        if self.paths is not None:
            try:
                fh = make_file_handler(self.paths, verbose=self.verbose)
            except OSError as exc:
                say(f"(could not open the log file {self.paths.log_file}: {exc})")
            else:
                root.addHandler(fh)
                self._added.append(fh)
                self.log_file = self.paths.log_file
        return self

    def __exit__(self, *exc: Any) -> None:
        root = logging.getLogger("needledropper")
        for h in self._added:
            root.removeHandler(h)
            if not isinstance(h, _StdoutHandler):
                h.close()
        self._added.clear()
        root.setLevel(self._old_level)


def _ask(prompt: str, default: str = "") -> str:
    """input() with a default; EOF (non-interactive) returns the default."""
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, RuntimeError):
        say()
        return default
    return ans or default


def _fmt_ms(ms: int) -> str:
    if not ms:
        return "?:??"
    s = int(ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def _fatal(title: str, text: str, *, dialog: bool) -> None:
    """Report a problem that stops the start: log it, print it, and (no console) show a dialog."""
    log.error("%s: %s", title, text)
    if sys.stdout is not None:
        say(f"{title}: {text}")
    if dialog or sys.stdout is None:
        show = fatal_dialog
        if show is None:
            from .tray import message_box as show
        try:
            show(title, text)
        except Exception:  # noqa: BLE001 - nothing else is left to tell the user
            log.debug("could not show the error dialog", exc_info=True)


# ----------------------------------------------------------- single instance --

def probe_instance(port: int, timeout: float = 1.0) -> Optional[dict[str, Any]]:
    """``GET http://127.0.0.1:<port>/healthz``: the running NeedleDropper's answer, or None."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
        try:
            conn.request("GET", HEALTH_PATH, headers={"Accept": "application/json"})
            resp = conn.getresponse()
            raw = resp.read(4096)
            status = resp.status
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, ValueError):
        return None
    if status != 200:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("app") == "needledropper":
        return data
    return None


def panel_url_for(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}/"


def deck_port(cfg: dict[str, Any]) -> int:
    port = (cfg.get("deck") or {}).get("port")
    return DEFAULT_DECK_PORT if port is None else int(port)  # 0 = any free port


# --------------------------------------------------------------- builders ----

def load_packset(ctx: Ctx, pack_args: Optional[Sequence[str]] = None):
    from .packs import load_packs_detailed

    files = [expand_path(p) for p in pack_args] if pack_args else pack_paths_from_config(ctx.cfg)
    return load_packs_detailed(files, resolved_cache=ctx.paths.resolved_cache)


def build_providers(cfg: dict, paths: Paths, deck: Any, *, out: Out = say, spotify_http: Any = None) -> list:
    """Providers in configured order; switched-off ones (``<name>.enabled = false``) are
    skipped, a missing/broken one is reported and skipped."""
    from .providers.local import LocalProvider
    from .providers.youtube import YouTubeProvider

    result = []
    seen: set[str] = set()
    for raw in cfg.get("providers") or []:
        name = str(raw).strip().lower()
        if name in seen:
            continue
        seen.add(name)
        if name in KNOWN_PROVIDERS and not provider_enabled(cfg, name):
            continue
        try:
            if name == "spotify":
                try:
                    from .providers.spotify import SpotifyProvider
                except ImportError as exc:
                    out(f"  Spotify support is not available ({exc}); continuing without it.")
                    continue
                kw = {"http": spotify_http} if spotify_http is not None else {}
                result.append(SpotifyProvider(cfg.get("spotify") or {}, paths, **kw))
            elif name == "local":
                result.append(LocalProvider(cfg.get("local") or {}, paths, deck))
            elif name == "youtube":
                result.append(YouTubeProvider(cfg.get("youtube") or {}, paths, deck))
            else:
                out(f"  Unknown provider {name!r} in config \"providers\" (known: {', '.join(KNOWN_PROVIDERS)}).")
        except Exception as exc:  # a provider that cannot even be built must not stop the rest
            out(f"  Provider {name} failed to start: {exc}")
    return result


TRANSPORTS = (("pixel", ".transport.pixel", "PixelTransport"),
              ("chatlog", ".transport.chatlog", "ChatLogTransport"))


def build_transports(cfg: dict, paths: Paths, on_event: Callable, *, diag: Optional[Callable[[str], None]] = None,
                     out: Out = say, diag_for: Optional[Callable[[str], Callable[[str], None]]] = None) -> list:
    """Configured transports (pixel, chatlog); missing/broken ones are reported and skipped."""
    tcfg = cfg.get("transport") or {}
    result = []
    for name, modname, clsname in TRANSPORTS:
        sec = tcfg.get(name) or {}
        if not sec.get("enabled", True):
            out(f"  {name} transport is disabled in config.")
            continue
        try:
            mod = importlib.import_module(modname, __package__)
            cls = getattr(mod, clsname)
        except (ImportError, AttributeError) as exc:
            out(f"  {name} transport is not available ({exc}); continuing without it.")
            continue
        d = diag_for(name) if diag_for else diag
        try:
            result.append(cls(sec, paths, on_event, diag=d))
        except Exception as exc:
            out(f"  {name} transport failed to start: {exc}")
    return result


def _start_transports(transports: list, out: Out = say) -> list:
    started = []
    for t in transports:
        try:
            t.start()
            started.append(t)
        except Exception as exc:
            out(f"  {getattr(t, 'name', type(t).__name__)} transport could not start: {exc}")
    return started


def _stop_all(transports: list) -> None:
    for t in transports:
        try:
            t.stop()
        except Exception as exc:
            log.debug("transport stop failed: %s", exc)


def _transport_line(t: Any) -> str:
    name = getattr(t, "name", type(t).__name__)
    try:
        st = t.status() or {}
    except Exception as exc:
        return f"{name} (status error: {exc})"
    ok = st.get("ok")
    detail = st.get("detail") or ""
    mark = "ok" if ok else ("waiting" if ok is None else "not ok")
    return f"{name} ({mark}{': ' + str(detail) if detail else ''})"


def _provider_line(p: Any) -> str:
    try:
        avail = p.available()
    except Exception:
        avail = False
    if avail:
        return f"{p.name} (ready)"
    reason = None
    why = getattr(p, "unavailable_reason", None)  # e.g. Spotify: not set up / not logged in / Premium
    if callable(why):
        try:
            reason = why()
        except Exception:
            reason = None
    if not isinstance(reason, str) or not reason:
        hints = {"spotify": "not connected - open the Connect page",
                 "local": "plays in an open player window", "youtube": "needs the player window"}
        reason = hints.get(p.name, "not available")
    return f"{p.name} ({reason})"


def _start_deck(ctx: Ctx, *, out: Out = say, allow_fallback: bool = False):
    from .deck.server import Deck

    port = deck_port(ctx.cfg)
    try:
        deck = Deck(port=port, url_opener=open_url).start()
    except OSError as exc:
        if allow_fallback:
            out(f"  Port {port} is busy (is NeedleDropper already running?); using a free port instead.")
            deck = Deck(port=0, url_opener=open_url).start()
        else:
            out(f"Cannot start the companion's web server on 127.0.0.1:{port}: {exc}\n"
                f"Another program uses this port. Close it, or change \"deck\": {{\"port\": ...}} in {config_path()}.")
            return None
    dcfg = ctx.cfg.get("deck") or {}
    attach_player_window(deck)
    deck.configure_player(auto_open=str(dcfg.get("autoOpen") or "youtube"), browser=str(dcfg.get("browser") or "edge"),
                          position=parse_position(dcfg.get("playerPosition")),
                          profile_dir=ctx.paths.app_dir / "deck-browser",
                          corner=str(dcfg.get("playerCorner") or "topright"),
                          browser_log=ctx.paths.logs_dir / "browser.log")
    return deck


def attach_player_window(deck: Any) -> None:
    """Give the deck its never-activating player window manager (deck/window.py; Windows only)."""
    if getattr(deck, "window", None) is not None:
        return
    try:
        from .deck.window import make_player_window
        deck.window = make_player_window()
    except Exception as exc:  # noqa: BLE001 - the plain window still works
        log.info("no player window manager: %s", exc)


_PLAYER_POLICY = {"youtube": "opens for YouTube songs", "always": "opens at start and for YouTube songs",
                  "never": "never opens by itself"}


def describe_event(ev: Any, transport: str = "") -> str:
    """One line for ``diag`` and the log: a v2 frame's own description, or a v1 event."""
    desc = getattr(ev, "describe", None)
    if callable(desc):
        text = str(desc())
    else:
        try:
            from .models import EventType

            kind = EventType(int(getattr(ev, "type", 0))).name
        except (TypeError, ValueError):
            kind = str(getattr(ev, "type", "?"))
        text = (f"v1 {kind} seq={getattr(ev, 'seq', '?')} n={getattr(ev, 'n', '?')} "
                f"hash={getattr(ev, 'hash', '?')} flags=0x{int(getattr(ev, 'flags', 0) or 0):02x}")
        if getattr(ev, "id", ""):
            text += f" id={ev.id}"
    return f"{text} [{transport}]" if transport else text


# --------------------------------------------------------- addon install ----

_SKIP_NAMES = {"__pycache__", ".git", ".DS_Store", "Thumbs.db", "desktop.ini"}
_SKIP_SUFFIXES = (".bak", ".tmp", ".orig", ".pyc", ".pyo", ".swp")


def addon_files(src: Path) -> list[str]:
    """Relative (``/``) paths of every file the installer copies from ``src`` (``Sets/`` included)."""
    out: list[str] = []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_NAMES and not d.startswith("."))
        rel_root = Path(root).relative_to(src)
        for name in sorted(files):
            if name in _SKIP_NAMES or name.startswith(".") or name.endswith(_SKIP_SUFFIXES):
                continue
            out.append((rel_root / name).as_posix())
    return out


def copy_addon(src: Path, dest: Path) -> list[str]:
    """Copy the addon tree into ``dest`` (atomic per file); returns the relative paths copied."""
    copied = []
    for rel in addon_files(src):
        atomic_write_bytes(dest / rel, (src / rel).read_bytes())
        copied.append(rel)
    return copied


def _remove_stale(dest: Path, previous: Sequence[str], current: Sequence[str]) -> list[str]:
    """Delete files an earlier install of this app put in ``dest`` that the new addon no longer has,
    plus the retired v1.1 files (Map.lua, Map.json, Protocol.lua). Never leaves ``dest``."""
    keep = set(current)
    removed = []
    base = dest.resolve()
    for rel in list(dict.fromkeys([*previous, *RETIRED_ADDON_FILES])):
        if not isinstance(rel, str) or rel in keep or rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/"):
            continue
        target = (dest / rel)
        try:
            if target.resolve().parent != base and base not in target.resolve().parents:
                continue
            if target.is_file():
                target.unlink()
                removed.append(rel)
        except OSError as exc:
            log.info("could not remove the old addon file %s: %s", target, exc)
    return removed


def install_addon(cfg: dict, paths: Paths, *, source: Optional[Path] = None, force: bool = False,
                  running: Optional[bool] = None) -> dict[str, Any]:
    """The fallback installer: copy the bundled addon (``Sets/`` included) into
    ``_forever_\\Interface\\AddOns\\NeedleDropper`` and record a receipt in the app dir.

    Refuses while this install's game runs (new addon folders load only after a client
    restart) unless ``force``. An earlier install by this app loses the files the new
    addon no longer has. Never touches ``NeedleDropper_Local``'s files (the folder is only
    created). Ownership (addon managers) is checked by the callers. Returns
    ``{"ok", "refused", "message", "files", "addonDir", "gameRunning", "version"}``.
    """
    src = Path(source or REPO_ADDON_DIR)
    toc_src = addon_toc(src)
    if toc_src is None:
        return {"ok": False, "refused": False, "message": f"The bundled addon was not found at {src} "
                                                          "(no NeedleDropper.toc there)."}
    if not paths.flavor_root.is_dir():
        return {"ok": False, "refused": False,
                "message": f"WoW: Forever was not found at {paths.flavor_root}. Choose your World of Warcraft "
                           "folder first."}
    if running is None:
        running = is_game_running(paths)
    if running and not force:
        return {"ok": False, "refused": True, "gameRunning": True,
                "message": "World of Warcraft is running. The game only finds new addon files when it starts: "
                           "close it first, or install anyway and restart the game afterwards."}
    before = read_install_receipt(paths)
    previous = [f for f in (before or {}).get("files") or [] if isinstance(f, str)]
    try:
        copied = copy_addon(src, paths.addon_dir)
        removed = _remove_stale(paths.addon_dir, previous, copied)
        paths.local_media_dir.mkdir(parents=True, exist_ok=True)  # create only; never touch its contents
    except OSError as exc:
        return {"ok": False, "refused": False,
                "message": f"Install failed: {exc}. Is the AddOns folder writable? If WoW is installed under "
                           "Program Files, start NeedleDropper once as administrator."}
    version = read_toc_version(toc_src)
    installed_toc = paths.addon_dir / toc_src.name
    try:
        write_install_receipt(paths, toc=installed_toc, version=version, files=copied)
    except OSError as exc:
        log.warning("could not record the addon install: %s", exc)
    if removed:
        log.info("removed old addon files: %s", ", ".join(removed))
    tail = ("World of Warcraft is running: restart the game to load the addon." if running else
            "Start the game: NeedleDropper is in the AddOns list.")
    return {"ok": True, "refused": False, "files": len(copied), "removed": removed, "addonDir": str(paths.addon_dir),
            "gameRunning": bool(running), "version": version, "localMediaDir": str(paths.local_media_dir),
            "message": f"Installed NeedleDropper {version or ''} ({len(copied)} files) to {paths.addon_dir}. "
                       f"{tail}".replace("  ", " ")}


# --------------------------------------------------------------- commands ----

def cmd_zones(args: argparse.Namespace) -> int:
    if args.json:
        data = [{"id": z.id, "name": z.name, "continent": z.continent, "kind": z.kind} for z in zones.ZONES]
        say(json.dumps(data, indent=2, ensure_ascii=False))
        return EXIT_OK
    cont_ids = {v: k for k, v in zones.CONTINENTS.items()}
    for continent, lst in zones.zones_by_continent().items():
        cid = cont_ids.get(continent)
        say(f"{continent}" + (f" (continent {cid})" if cid else ""))
        for z in lst:
            kind = "" if z.kind == "zone" else z.kind
            say(f"  {z.id:>5}  {z.name:<24} {kind}".rstrip())
        say()
    say("Known landmarks (use as \"areaID\" in packs)")
    for a in zones.KNOWN_AREAS:
        say(f"  {a.id:>5}  {a.name:<24} in {zones.ZONES_BY_ID[a.zone].name}")
    say()
    say("Notable subzones (use as \"subzone\" with the zone)")
    for zid, names in zones.KNOWN_SUBZONES.items():
        say(f"  {zones.ZONES_BY_ID[zid].name}: {', '.join(names)}")
    say()
    say("Names are matched ignoring case, punctuation and a leading \"The\"; short forms like STV, "
        "Hinterlands, Barrens, Stormwind also work.")
    say("In game, /needle where prints the ids for where you stand.")
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    from .packs import validate_report

    ctx = Ctx(args)
    ps = load_packset(ctx, args.packs)
    if not ps.packs:
        say("No packs given (and \"packs\" in config.json is empty).")
        return EXIT_FAIL
    lines, ok = validate_report(ps)
    for ln in lines:
        say(ln)
    if args.list and ps.drops:
        say()
        for d in ps.drops:
            srcs = ",".join(k for k in ("spotify", "youtube", "file") if getattr(d, k)) or "-"
            say(f"  {d.n:>4}  {d.id:<32} {d.display_label} -- {d.artist} - {d.title} [{srcs}]")
    return EXIT_OK if ok else EXIT_FAIL


def cmd_install_addon(args: argparse.Namespace, ctx: Optional[Ctx] = None) -> int:
    ctx = ctx or Ctx(args)
    paths = ctx.paths
    src = Path(getattr(args, "source", None) or REPO_ADDON_DIR)
    if addon_toc(src) is None:
        say(f"The bundled addon was not found at {src} (expected NeedleDropper.toc there).")
        return EXIT_FAIL
    if not paths.flavor_root.is_dir():
        say(f"WoW: Forever was not found at {paths.flavor_root}.\n"
            f"Set \"wowRoot\" in {ctx.config_file or config_path()}, or use the Connect page (needledropper setup).")
        return EXIT_FAIL
    st = addon_status(paths)
    if st.owner in ("manager", "other") and not args.force:
        who = st.manager or "an addon manager or by hand"
        say(f"NeedleDropper {st.version or ''} in {paths.addon_dir} was installed by {who}.".replace("  ", " "))
        say("Update it there: replacing it here would fight your addon manager. (--force replaces it anyway.)")
        return EXIT_REFUSED
    if st.owner == "missing" and st.managers and not args.force:
        say(f"Your addons are managed by {st.managers[0]}: install NeedleDropper there (search for NeedleDropper).")
        say("(--force installs the bundled copy anyway.)")
        return EXIT_REFUSED
    running = is_game_running(paths)
    if running and not args.force:
        say(f"World of Warcraft is running ({paths.game_exe}).\n"
            "The game only finds new addon files when it starts. Close it first, or re-run with --force and "
            "restart the game afterwards.")
        return EXIT_REFUSED
    res = install_addon(ctx.cfg, paths, source=src, force=True, running=running)
    say(res["message"])
    if not res["ok"]:
        return EXIT_FAIL
    say(f"Put in-game audio files in {paths.local_media_dir} (NeedleDropper never deletes them).")
    try:
        from . import gamedata

        if paths.addons_dir.is_dir():
            gamedata.ensure_companion_addon(paths.addons_dir)
    except Exception as exc:  # noqa: BLE001 - optional data addon
        log.debug("NeedleDropper_Companion not created: %s", exc)
    return EXIT_OK


def cmd_spotify_login(args: argparse.Namespace, ctx: Optional[Ctx] = None) -> int:
    ctx = ctx or Ctx(args)
    sp = dict(ctx.cfg.get("spotify") or {})
    port = int(sp.get("redirectPort") or 8321)
    if not sp.get("clientId"):
        say("Spotify needs a (free) developer app of your own:\n"
            "  1. Open https://developer.spotify.com/dashboard and create an app (Web API).\n"
            f"  2. Add the redirect URI exactly: http://127.0.0.1:{port}/callback\n"
            "  3. Copy the app's Client ID here. (Playback control requires Spotify Premium.)\n"
            "  (Easier: open the Connect page with `needledropper setup`.)")
        cid = _ask("Spotify Client ID", "")
        if not cid:
            say("No Client ID given; Spotify sign-in skipped.")
            return EXIT_FAIL
        ctx.save({"spotify": {"clientId": cid}})
        sp = dict(ctx.cfg.get("spotify") or {})
    try:
        from .providers.spotify_auth import login_interactive
    except ImportError as exc:
        say(f"Spotify support is not available ({exc}).")
        return EXIT_FAIL
    try:
        login_interactive(sp, ctx.paths, open_browser=not getattr(args, "no_browser", False), print_fn=say)
    except KeyboardInterrupt:
        say("Spotify sign-in cancelled.")
        return EXIT_FAIL
    except Exception as exc:
        say(f"Spotify sign-in failed: {exc}")
        return EXIT_FAIL
    say("Spotify sign-in complete.")
    return EXIT_OK


def _sleep_until(deadline: Optional[float], step: float = 0.25, also: Optional[Callable[[], bool]] = None) -> None:
    while deadline is None or time.monotonic() < deadline:
        if also is not None and also():
            return
        time.sleep(step)


# ------------------------------------------------------- the companion ----

def _already_running(port: int, info: dict[str, Any], *, open_panel: bool) -> int:
    url = panel_url_for(port)
    say(f"NeedleDropper {info.get('version') or ''} is already running. Connect page: {url}".replace("  ", " "))
    if open_panel:
        try:
            open_url(url)
            say("Opened the Connect page in your browser.")
        except Exception as exc:  # noqa: BLE001
            say(f"Could not open a browser ({exc}); open {url} yourself.")
    return EXIT_OK


def _start_tray(app: Any, quit_event: threading.Event,
                session_end: Optional[Callable[[], Any]] = None) -> Optional[Any]:
    """The notification-area icon with ``tray.companion_menu`` wired to ``app``; None if there is none.
    ``session_end`` runs synchronously when Windows logs off / shuts down (see :class:`_SessionEnd`)."""
    from . import tray

    factory = tray_factory or tray.TrayIcon
    menu = tray.companion_menu(
        on_open_setup=app.open_setup,
        on_pause=app.set_paused, paused=lambda: app.paused,
        on_autostart=app.set_autostart, autostart=app.autostart_enabled,
        on_open_log_folder=app.open_logs,
        on_quit=quit_event.set,
        status=app.status_line,
    )
    kw: dict[str, Any] = {"on_session_end": session_end} if session_end is not None else {}
    try:
        icon = factory(APP_TITLE, tray.default_icon_path(app.paused), menu, app.open_setup,
                       tooltip=f"{APP_TITLE} - {app.status_line()}", on_notification_click=app.notifier.clicked,
                       on_close=quit_event.set, **kw)
        icon.start()
    except (RuntimeError, OSError, ValueError) as exc:
        log.warning("no tray icon (%s); NeedleDropper keeps running without one", exc)
        return None

    def pause_changed(paused: bool) -> None:
        icon.set_icon(tray.default_icon_path(paused))

    app.on_pause_changed = pause_changed
    app.notifier.attach(icon)
    return icon


def _wait_quit(quit_event: threading.Event, timeout: float) -> bool:
    """One wait of the companion's main loop (Ctrl+C arrives here when run from a console)."""
    return quit_event.wait(timeout)


# ------------------------------------------------- session end / console close ----
#
# CLI-CONSOLE-CLOSE-NO-CLEANUP (baseline-findings.md): closing the console, logging off or shutting
# Windows down ends the process without running ``finally`` blocks, so a playing Spotify drop kept
# playing at its fade volume and the player's own music was never restored. Windows allows a few
# seconds of clean-up in two places, and both now run the fast stop synchronously:
#
# * the tray's hidden window receives WM_ENDSESSION (the windowed exe: log off / shut down);
# * a console control handler receives CTRL_CLOSE_EVENT (``run`` in a console window: its X button;
#   also CTRL_LOGOFF / CTRL_SHUTDOWN for a console process without a window of its own).

#: Console control events that end the process (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT).
CTRL_CLOSE_EVENTS = (2, 5, 6)
#: How long a session-end / console-close handler waits for the clean-up (Windows allows about 5 s).
SESSION_END_WAIT_S = 4.5
#: ``kernel32``-like object with ``SetConsoleCtrlHandler`` (tests inject a fake); None = the real one.
console_ctrl_api: Any = None
_console_handlers: list[Any] = []  # keeps the ctypes callbacks alive while installed


class _SessionEnd:
    """The handshake between a session-end callback (tray or console thread) and the main thread."""

    def __init__(self, quit_event: threading.Event) -> None:
        self.quit_event = quit_event
        self.requested = threading.Event()
        self.done = threading.Event()

    def __call__(self, why: str = "session end") -> None:
        """Ask the main loop to stop *now* and block until it cleaned up (at most SESSION_END_WAIT_S)."""
        if not self.requested.is_set():
            log.info("Windows is ending NeedleDropper (%s): stopping playback now", why)
        self.requested.set()
        self.quit_event.set()
        if not self.done.wait(SESSION_END_WAIT_S):
            log.warning("clean-up did not finish within %.1f s", SESSION_END_WAIT_S)


def install_console_handler(on_close: Callable[[str], Any]) -> Optional[Callable[[], None]]:
    """Run ``on_close(reason)`` (synchronously) on CTRL_CLOSE / CTRL_LOGOFF / CTRL_SHUTDOWN; Ctrl+C
    and Ctrl+Break keep Python's own handling (KeyboardInterrupt). Returns an undo, or None when
    there is no console API (not Windows)."""
    api = console_ctrl_api
    if api is None:
        if sys.platform != "win32":
            return None
        try:
            import ctypes

            api = ctypes.WinDLL("kernel32", use_last_error=True)
        except (OSError, AttributeError):
            return None
    try:
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    except (ImportError, AttributeError, ValueError):  # pragma: no cover - no ctypes support
        return None
    names = {2: "console closed", 5: "log off", 6: "shut down"}

    def routine(event: int) -> int:
        if int(event) not in CTRL_CLOSE_EVENTS:
            return 0  # not ours: the next handler (Python's Ctrl+C) runs
        try:
            on_close(names.get(int(event), f"console event {event}"))
        except Exception:  # noqa: BLE001 - never raise into Windows
            log.exception("console close clean-up failed")
        return 1

    callback = handler_type(routine)
    try:
        if not api.SetConsoleCtrlHandler(callback, True):
            return None
    except (OSError, AttributeError, TypeError, ValueError):
        return None
    _console_handlers.append(callback)

    def undo() -> None:
        try:
            api.SetConsoleCtrlHandler(callback, False)
        except (OSError, AttributeError, TypeError, ValueError):
            pass
        if callback in _console_handlers:
            _console_handlers.remove(callback)

    return undo


def _install_excepthook() -> Callable[[], None]:
    """Log uncaught main-thread exceptions too (the windowed exe has no console); returns an undo."""
    previous = sys.excepthook

    def hook(etype: Any, value: Any, tb: Any) -> None:
        if not issubclass(etype, KeyboardInterrupt):
            log.critical("unexpected error", exc_info=(etype, value, tb))
        previous(etype, value, tb)

    sys.excepthook = hook

    def undo() -> None:
        if sys.excepthook is hook:
            sys.excepthook = previous

    return undo


def run_companion(args: argparse.Namespace, *, tray_mode: bool) -> int:
    """The companion itself: ``tray_mode`` for the default command, else ``run`` (console, no tray)."""
    from .deck.server import Deck
    from .panel_api import Companion, should_open_panel

    ctx = Ctx(args)
    port = deck_port(ctx.cfg)
    background = bool(getattr(args, "background", False))
    if tray_mode:
        want_panel = bool(getattr(args, "panel", False)) or (not background and should_open_panel(ctx.cfg, ctx.paths))
    else:
        want_panel = bool(getattr(args, "panel", False))
    if getattr(args, "no_panel", False):
        want_panel = False
    if port:
        other = probe_instance(port)
        if other is not None:
            return _already_running(port, other, open_panel=(tray_mode and not background) or want_panel)
    with _LogSession(bool(getattr(args, "verbose", False)), ctx.paths, console=True) as session:
        undo_hook = _install_excepthook()
        try:
            return _serve(ctx, args, session, Deck, Companion, port=port, tray_mode=tray_mode, want_panel=want_panel,
                          background=background)
        finally:
            undo_hook()


def _serve(ctx: Ctx, args: argparse.Namespace, session: _LogSession, Deck: Any, Companion: Any, *, port: int,
           tray_mode: bool, want_panel: bool, background: bool) -> int:
    log.info("NeedleDropper %s starting (%s)", __version__, "tray" if tray_mode else "console")
    say(f"NeedleDropper companion {__version__}")
    try:
        deck = Deck(port=port, url_opener=open_url).start()
    except OSError as exc:
        other = probe_instance(port) if port else None
        if other is not None:
            return _already_running(port, other, open_panel=tray_mode and not background)
        _fatal("NeedleDropper could not start",
               f"Port {port} on this PC is already used by another program ({exc}). Close that program, or give "
               f"NeedleDropper another port in {ctx.config_file or config_path()} (\"deck\": {{\"port\": 8323}}).",
               dialog=tray_mode)
        return EXIT_FAIL
    app = Companion(ctx.cfg, ctx.paths, deck=deck, config_file=ctx.config_file,
                    out=(say if sys.stdout is not None else None))
    quit_event = threading.Event()
    ending = _SessionEnd(quit_event)
    icon = None
    undo_console = install_console_handler(ending)  # closing the console window / log off / shut down
    try:
        app.start()
        if tray_mode:
            icon = _start_tray(app, quit_event, session_end=lambda: ending("log off or shut down"))
            if icon is None and not background:
                want_panel = True  # no tray icon to find NeedleDropper by: show the page instead
        opened = ""
        if want_panel:
            opened = "  (opened in your browser)" if app.open_setup() else "  (open it in your browser)"
        say(f"  Connect page: {deck.panel_url}{opened}")
        say(f"  Music:        {', '.join(_provider_line(p) for p in app.providers) or 'none'}")
        say(f"  Transports:   {', '.join(_transport_line(t) for t in app.transports) or 'none -- nothing will be received'}")
        say(f"  Player:       {_PLAYER_POLICY.get(deck.auto_open, deck.auto_open)}")
        if session.log_file:
            say(f"  Log:          {session.log_file}")
        say("Running in the tray: right-click the icon to quit." if icon is not None else
            "Waiting for the game. Keep this running while you play; press Ctrl+C to quit.")
        seconds = float(getattr(args, "seconds", 0) or 0)
        deadline = time.monotonic() + seconds if seconds else None
        last_tip = ""
        next_tip = 0.0
        try:
            while not quit_event.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    break
                _wait_quit(quit_event, 0.25)
                if icon is not None and time.monotonic() >= next_tip:
                    next_tip = time.monotonic() + TOOLTIP_REFRESH_S
                    tip = f"{APP_TITLE} - {app.status_line()}"
                    if tip != last_tip:
                        last_tip = tip
                        icon.set_tooltip(tip)
        except KeyboardInterrupt:
            say("\nShutting down...")
    finally:
        if ending.requested.is_set():
            # Windows is ending us: clean up first (seconds count), then let the waiting handler return.
            try:
                app.stop(fast=True)
            except Exception:  # noqa: BLE001
                log.exception("fast stop failed")
            try:
                deck.stop()
            except Exception:  # noqa: BLE001
                log.debug("deck stop failed", exc_info=True)
            ending.done.set()
            log.info("NeedleDropper stopped (session end)")
            if icon is not None:
                try:
                    icon.stop(timeout=1.0)
                except Exception:  # noqa: BLE001
                    log.debug("tray stop failed", exc_info=True)
        else:
            if icon is not None:
                try:
                    icon.stop()
                except Exception:  # noqa: BLE001
                    log.debug("tray stop failed", exc_info=True)
            app.stop()
            deck.stop()
            ending.done.set()
            log.info("NeedleDropper stopped")
        if undo_console is not None:
            undo_console()
    say("Bye.")
    return EXIT_OK


def cmd_default(args: argparse.Namespace) -> int:
    """No command: the companion in the tray (``deck.panelOnStart`` decides about the Connect page)."""
    return run_companion(args, tray_mode=True)


def cmd_run(args: argparse.Namespace) -> int:
    """``run``: the companion in the foreground, logging to the console; Ctrl+C quits."""
    return run_companion(args, tray_mode=False)


def cmd_setup(args: argparse.Namespace) -> int:
    """``setup``: open the Connect page (the running companion's, or start one in the tray)."""
    args.panel = True
    args.background = False
    return run_companion(args, tray_mode=True)


def cmd_retired(args: argparse.Namespace) -> int:
    say(f"`needledropper {args.command}` was removed in NeedleDropper 1.2: soundtracks are edited in game now "
        "(type /needle), and songs travel to the companion with every needle drop. Pack authors: see "
        "`needledropper packs --help`.")
    return EXIT_REFUSED


# ------------------------------------------------------------------- diag ----

def cmd_diag(args: argparse.Namespace) -> int:
    ctx = Ctx(args)
    say(f"NeedleDropper diag {__version__} -- no playback. Shows every decoded frame / chat-log line.")
    say(f"  Chat log: {ctx.paths.chat_log}")

    def on_event(ev: Any, transport: str) -> None:
        say(f"{time.strftime('%H:%M:%S')}  EVENT {describe_event(ev, transport)}", flush=True)

    def diag_for(name: str) -> Callable[[str], None]:
        return lambda msg: say(f"{time.strftime('%H:%M:%S')}  [{name}] {msg}", flush=True)

    transports = _start_transports(build_transports(ctx.cfg, ctx.paths, on_event, diag_for=diag_for))
    if not transports:
        say("No transports could start.")
        return EXIT_FAIL
    say("In game: /needle test  (or /needle selftest beacon). Ctrl+C to stop.")
    deadline = time.monotonic() + args.seconds if args.seconds else None
    next_status = time.monotonic() + 10
    try:
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.25)
            if time.monotonic() >= next_status:
                next_status = time.monotonic() + 10
                say(f"{time.strftime('%H:%M:%S')}  status: " + "; ".join(_transport_line(t) for t in transports),
                    flush=True)
    except KeyboardInterrupt:
        say()
    finally:
        _stop_all(transports)
    return EXIT_OK


# -------------------------------------------------------------- test-drop ----

def _bundled_and_configured_drops(ctx: Ctx) -> list:
    """Drops of the configured packs plus every bundled pack (``packs/*.json``), first one wins."""
    from . import resources
    from .packs import load_packs_detailed

    files = list(pack_paths_from_config(ctx.cfg))
    try:
        files += sorted(resources.packs_dir().glob("*.json"))
    except OSError:
        pass
    drops: list = []
    seen: set[str] = set()
    for f in dict.fromkeys(str(p) for p in files):
        try:
            ps = load_packs_detailed([f])
        except Exception as exc:  # noqa: BLE001 - a broken pack must not stop the lookup
            log.debug("test-drop: %s unreadable: %s", f, exc)
            continue
        for d in ps.drops:
            if d.id not in seen:
                seen.add(d.id)
                drops.append(d)
    return drops


def make_test_frame(ctx: Ctx, query: str) -> tuple[Optional[Any], str]:
    """A FIRE + TEST frame for ``test-drop``: a Spotify / YouTube link or id, ``Artist - Title``,
    or a drop of the configured / bundled packs (id, zone or landmark name). ``(None, why)`` if not found."""
    from . import protocol2
    from .models import EventType, Flags

    q = query.strip()
    sp = protocol2.spotify_id_from_link(q)
    yt = None if sp else protocol2.youtube_id_from_link(q)
    if sp or yt:
        frame = protocol2.build_frame(EventType.FIRE, flags=int(Flags.TEST), drop_id="test", spotify=sp, youtube=yt,
                                      label="NeedleDropper test")
        return frame, f"{'Spotify track ' + sp if sp else 'YouTube video ' + str(yt)}"
    drop = None
    try:
        from .packs import find_drop

        drop = find_drop(_bundled_and_configured_drops(ctx), q)
    except Exception as exc:  # noqa: BLE001
        log.debug("test-drop lookup failed: %s", exc)
    if drop is not None:
        frame = protocol2.build_frame(
            EventType.FIRE, flags=int(Flags.TEST), drop_id=drop.id, spotify=drop.spotify, youtube=drop.youtube,
            artist=drop.artist, title=drop.title, start_ms=int(drop.startMs or 0) or None,
            length_ms=int(drop.durationMs or 0) or None, volume=drop.volume, label=drop.display_label)
        return frame, f"{drop.id}: {drop.display_label} -- {drop.artist} - {drop.title}"
    for sep in (" – ", " - ", " · "):
        if sep in q:
            artist, title = (x.strip() for x in q.split(sep, 1))
            if artist and title:
                frame = protocol2.build_frame(EventType.FIRE, flags=int(Flags.TEST), drop_id="test", artist=artist,
                                              title=title, label="NeedleDropper test")
                return frame, f"search: {artist} - {title}"
    return None, (f"No drop matches {query!r}. Give a drop id (zone:1429), a zone or landmark name, "
                  "\"Artist - Title\", or a Spotify / YouTube link.")


def cmd_test_drop(args: argparse.Namespace) -> int:
    ctx = Ctx(args)
    with _LogSession(args.verbose):
        return _test_drop(ctx, args)


def _test_drop(ctx: Ctx, args: argparse.Namespace) -> int:
    from .panel_api import construct_engine

    frame, what = make_test_frame(ctx, " ".join(args.query))
    if frame is None:
        say(what)
        return EXIT_FAIL
    say(f"Test drop {what}")
    deck = _start_deck(ctx, allow_fallback=True)
    if deck is None:
        return EXIT_FAIL
    providers = build_providers(ctx.cfg, ctx.paths, deck)
    if args.provider:
        providers = [p for p in providers if p.name == args.provider]
        if not providers:
            say(f"Provider {args.provider!r} is not configured/available.")
            deck.stop()
            return EXIT_FAIL
    engine = None
    try:
        if getattr(frame, "youtube_id", None) and any(p.name == "youtube" for p in providers) and not args.no_window:
            how = deck.open_player()
            say(f"  Player: {deck.player_url} ({'opened' if how != 'none' else 'open it yourself'})")
            say("  Waiting up to 15 s for the player window to connect...")
            deck.wait_player(15)
        from .engine import Engine

        engine = construct_engine(Engine, providers, deck, {
            "fade_in_ms": int(ctx.cfg.get("fadeInMs", 2500)), "fade_out_ms": int(ctx.cfg.get("fadeOutMs", 3000)),
            "log": logging.getLogger("needledropper.engine")})
        engine.start()
        engine.submit(frame, "test-drop")
        secs = args.seconds or ((frame.length_ms / 1000.0 + 2) if frame.length_ms else 30.0)
        say(f"  Playing for up to {int(secs)} s (Ctrl+C to stop).")
        deadline = time.monotonic() + secs

        def playing() -> bool:
            try:
                return bool((engine.status() or {}).get("playing"))
            except Exception:  # noqa: BLE001
                return False

        try:
            _sleep_until(min(deadline, time.monotonic() + 8.0), also=playing)
            if not playing():
                say("  Nothing is playing (no service could play it; see the messages above).")
            else:
                _sleep_until(deadline, also=lambda: not playing())
        except KeyboardInterrupt:
            say()
    finally:
        if engine is not None:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
        for p in providers:
            try:
                p.close()
            except Exception:
                pass
        deck.stop()
    return EXIT_OK


# ----------------------------------------------------------------- parser ----

def _register_packs_commands(sub: Any) -> bool:
    """``packs`` tools for pack authors (C1's commands_packs), when that module is present."""
    try:
        commands_packs = importlib.import_module(".commands_packs", __package__)
    except ImportError as exc:
        log.debug("packs commands not available: %s", exc)
        return False
    except Exception:  # noqa: BLE001 - a broken optional module must not take the CLI down
        log.warning("the packs commands could not be loaded", exc_info=True)
        return False
    register = getattr(commands_packs, "register", None)
    if not callable(register):
        return False
    try:
        register(sub)
    except Exception:  # noqa: BLE001
        log.warning("the packs commands could not be registered", exc_info=True)
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="needledropper",
                                 description="NeedleDropper companion: plays the needle drops of the NeedleDropper "
                                             "addon for WoW: Forever. Without a command it runs in the tray.")
    ap.add_argument("--version", action="version", version=f"needledropper {__version__}")
    ap.add_argument("--config", metavar="FILE", help="use this config.json instead of the one in the app folder")
    ap.add_argument("-v", "--verbose", action="store_true", help="more logging")
    ap.add_argument("--background", action="store_true", help=argparse.SUPPRESS)  # Start with Windows: no page
    ap.add_argument("--seconds", type=float, default=0, help=argparse.SUPPRESS)  # quit after N s (tests, smoke runs)
    sub = ap.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, func: Callable, help_: Optional[str]) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_, description=help_) if help_ else sub.add_parser(name)
        p.set_defaults(func=func)
        return p

    p = add("run", cmd_run, "run the companion in this terminal (no tray icon); Ctrl+C to quit")
    p.add_argument("--panel", action="store_true", help="open the Connect page in your browser")
    p.add_argument("--no-panel", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--open", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--seconds", type=float, default=0, help=argparse.SUPPRESS)
    p = add("setup", cmd_setup, "open the Connect page (starts NeedleDropper in the tray if needed)")
    p.add_argument("--seconds", type=float, default=0, help=argparse.SUPPRESS)
    p = add("spotify-login", cmd_spotify_login, "sign in to Spotify from the terminal (Authorization Code + PKCE)")
    p.add_argument("--no-browser", action="store_true", help="print the sign-in URL instead of opening it")
    p = add("install-addon", cmd_install_addon, "copy the bundled addon into WoW: Forever's AddOns folder (fallback)")
    p.add_argument("--force", action="store_true",
                   help="install even while the game runs, or over an addon this app did not install")
    p = add("diag", cmd_diag, "print every decoded beacon frame / chat-log line; no playback")
    p.add_argument("--seconds", type=float, default=0, metavar="N", help="stop after N seconds")
    p = add("test-drop", cmd_test_drop, "play a song through the music services without the game")
    p.add_argument("query", nargs="+", help="drop id (zone:1429) or zone/landmark name of a pack, "
                                            "\"Artist - Title\", or a Spotify / YouTube link")
    p.add_argument("--provider", choices=KNOWN_PROVIDERS, help="only try this service")
    p.add_argument("--seconds", type=float, default=0, metavar="N", help="stop after N seconds")
    p.add_argument("--no-window", action="store_true", help="do not open the player window")
    p = add("zones", cmd_zones, "list known zones, landmarks and aliases")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p = add("validate", cmd_validate, "check soundtrack pack files for errors")
    p.add_argument("packs", nargs="*", help="pack files (default: the configured packs)")
    p.add_argument("--list", action="store_true", help="also list every drop")
    for retired in ("build-map", "resolve"):  # hidden; any options they once had are accepted
        add(retired, cmd_retired, None)
    _register_packs_commands(sub)
    return ap


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse the command line; no command means the default start (the tray)."""
    parser = build_parser()
    args, extra = parser.parse_known_args(list(argv) if argv is not None else None)
    if extra and getattr(args, "func", None) is not cmd_retired:
        parser.error("unrecognized arguments: " + " ".join(extra))
    if not getattr(args, "func", None):
        args.command = None
        args.func = cmd_default
    for name, default in (("panel", False), ("no_panel", False), ("open", False), ("seconds", 0.0),
                          ("background", False)):
        if not hasattr(args, name):
            setattr(args, name, default)
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        _fatal("NeedleDropper: config.json cannot be used", str(exc), dialog=args.func is cmd_default)
        return EXIT_REFUSED
    except KeyboardInterrupt:
        say("\nInterrupted.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
