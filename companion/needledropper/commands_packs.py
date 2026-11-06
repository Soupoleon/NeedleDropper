"""``needledropper packs ...``: soundtrack packs and share codes for pack authors, CI and migration.

Commands (``cli.py`` adds them with :func:`register`; ``python -m needledropper.commands_packs``
runs them on their own):

* ``packs validate <file|code>`` -- check a pack file (``packs/*.json``) or a share code (pasted
  text, a file holding one, or ``-`` for stdin) and say whether it can be shared / imported.
* ``packs encode <file.json> [--out FILE]`` -- the share code of a pack file (DESIGN-v1.2 5.2 writer
  form: numeric zone ids, ``spotify:track:<id>``, unfinished drops left out).
* ``packs decode <code|file> [--out file.json] [--raw]`` -- a share code back to a pack file (the
  validated writer form; ``--raw`` = exactly the JSON inside the code).
* ``packs convert-legacy`` -- the one-time migration of a v1.1 companion: every user pack
  (``%APPDATA%\\NeedleDropper\\packs\\*.json`` and the user packs named in its config) becomes a
  share code, offered to the addon through ``NeedleDropper_Companion`` ``imports`` (``active`` =
  the pack was switched on in the v1.1 app). Packs every account already imported (read from the
  game's SavedVariables, never written) are skipped. Bundled packs are not converted: they ship
  inside the addon as built-in soundtracks.

Exit status: 0 success, 1 a problem (invalid pack or code, nothing written), 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import packs as packs_mod
from . import sharecode
from .sharecode import ShareCodeError

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
Out = Callable[[str], None]

_TOP_ORDER = ("format", "version", "id", "name", "author", "description", "tags", "homepage", "revision", "drops")
_DROP_ORDER = ("disabled", "zone", "subzone", "areaID", "instanceID", "label", "artist", "title", "spotify",
               "youtube", "file", "inGameFile", "startMs", "durationMs", "volume", "tags", "note")

__all__ = ["register", "main", "cmd_validate", "cmd_encode", "cmd_decode", "cmd_convert_legacy",
           "legacy_pack_files", "pack_json_text", "EXIT_OK", "EXIT_FAIL", "EXIT_USAGE"]


# ----------------------------------------------------------------- helpers ----

def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _read_source(source: str) -> tuple[str, Optional[Path]]:
    """``(text, path)`` for a command argument: ``-`` = stdin, an existing file, else the text itself."""
    if source == "-":
        return sys.stdin.read(), None
    p = Path(source)
    try:
        is_file = len(source) < 4096 and p.is_file()
    except (OSError, ValueError):
        is_file = False
    if is_file:
        return p.read_text(encoding="utf-8-sig", errors="replace"), p
    return source, None


def _looks_like_code(text: str) -> bool:
    return "!NeedleDropper:" in text or sharecode.foreign_prefix(text) is not None or not text.lstrip().startswith("{")


def _ordered(obj: dict[str, Any], order: Sequence[str]) -> dict[str, Any]:
    out = {k: obj[k] for k in order if k in obj}
    out.update({k: obj[k] for k in sorted(obj) if k not in out})
    return out


def pack_json_text(pack: dict[str, Any]) -> str:
    """A pack as a readable pack file: two-space indent, raw UTF-8, keys in the documented order."""
    top = _ordered(dict(pack), _TOP_ORDER)
    if isinstance(top.get("drops"), list):
        top["drops"] = [_ordered(d, _DROP_ORDER) if isinstance(d, dict) else d for d in top["drops"]]
    return json.dumps(top, ensure_ascii=False, indent=2) + "\n"


def _write_text(path: Path, text: str) -> None:
    from .config import atomic_write_text

    atomic_write_text(path, text)


def _summary(pack: dict[str, Any]) -> str:
    drops = pack.get("drops") or []
    off = sum(1 for d in drops if isinstance(d, dict) and d.get("disabled") is True)
    author = f" by {pack['author']}" if pack.get("author") else ""
    rev = f", revision {pack['revision']}" if pack.get("revision") is not None else ""
    turned = f" (+{off} turned off)" if off else ""
    return f'"{pack.get("name", "?")}"{author} [{pack.get("id", "?")}]: {len(drops) - off} drop(s){turned}{rev}'


def _load_pack_json(path: Path) -> tuple[Optional[Any], Optional[str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    except UnicodeDecodeError:
        return None, f"{path} is not UTF-8 text"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"


# ----------------------------------------------------------------- validate ----

def cmd_validate(args: argparse.Namespace, out: Out = print) -> int:
    text, path = _read_source(args.source)
    if path is not None and not _looks_like_code(text):
        return _validate_file(path, out)
    try:
        dec = sharecode.decode_detailed(text)
    except ShareCodeError as exc:
        out(f"FAILED  share code: {exc}")
        for issue in exc.issues:
            out(f"    ERROR {issue}")
        return EXIT_FAIL
    kind = "compressed" if dec.compressed else "uncompressed"
    out(f"OK      share code ({dec.length:,} characters, {kind}): {_summary(dec.normalized)}")
    for w in dec.warnings:
        out(f"    warn  {w}")
    return EXIT_OK


def _validate_file(path: Path, out: Out) -> int:
    ps = packs_mod.load_packs_detailed([path])
    lines, ok = packs_mod.validate_report(ps)
    for ln in lines:
        out(ln)
    if not ok:
        return EXIT_FAIL
    data, problem = _load_pack_json(path)
    if problem:  # pragma: no cover - load_packs_detailed already reported it
        out(problem)
        return EXIT_FAIL
    try:
        pack, warnings = sharecode.pack_for_sharing(data, name_hint=path.stem)
        code = sharecode.encode(pack)
    except ShareCodeError as exc:
        out(f"Not shareable as a code: {exc}")
        for issue in exc.issues:
            out(f"    ERROR {issue}")
        return EXIT_FAIL
    for w in warnings:
        out(f"    warn  {w}")
    out(f"Share code: ready ({len(code):,} characters; `needledropper packs encode {path}`).")
    return EXIT_OK


# ------------------------------------------------------------------- encode ----

def cmd_encode(args: argparse.Namespace, out: Out = print) -> int:
    path = Path(args.file)
    data, problem = _load_pack_json(path)
    if problem:
        _err(f"error: {problem}")
        return EXIT_FAIL
    try:
        pack, warnings = sharecode.pack_for_sharing(data, name_hint=path.stem)
        code = sharecode.encode(pack)
    except ShareCodeError as exc:
        _err(f"error: {path}: {exc}")
        for issue in exc.issues:
            _err(f"  - {issue}")
        return EXIT_FAIL
    for w in warnings:
        _err(f"warning: {w}")
    if args.out:
        target = Path(args.out)
        _write_text(target, code + "\n")
        out(f"Wrote {target} ({len(code):,} characters): {_summary(pack)}")
    else:
        out(code)
    return EXIT_OK


# ------------------------------------------------------------------- decode ----

def cmd_decode(args: argparse.Namespace, out: Out = print) -> int:
    text, _path = _read_source(args.source)
    try:
        dec = sharecode.decode_detailed(text)
    except ShareCodeError as exc:
        _err(f"error: {exc}")
        for issue in exc.issues:
            _err(f"  - {issue}")
        return EXIT_FAIL
    for w in dec.warnings:
        _err(f"warning: {w}")
    body = pack_json_text(dec.pack if args.raw else dec.normalized)
    if args.out:
        target = Path(args.out)
        _write_text(target, body)
        out(f"Wrote {target}: {_summary(dec.normalized)}")
    else:
        out(body.rstrip("\n"))
    return EXIT_OK


# ----------------------------------------------------------- convert-legacy ----

def legacy_pack_files(cfg: dict[str, Any], app_dir: Path) -> list[tuple[Path, bool]]:
    """The v1.1 user packs: ``(file, was switched on)``, in a stable order.

    ``<app_dir>\\packs\\*.json`` plus the files the config's ``packs`` / ``packsDisabled`` name;
    bundled packs (``builtin:<id>`` or files in the app's own ``packs`` folder) are left out --
    they ship inside the addon as built-in soundtracks.
    """
    from . import resources
    from .config import BUNDLED_PREFIX, expand_pack_entry

    def norm(p: Path) -> str:
        try:
            return os.path.normcase(str(p.resolve()))
        except OSError:
            return os.path.normcase(str(p))

    try:
        bundled_dir = norm(resources.packs_dir())
    except Exception:  # pragma: no cover - resources always resolves
        bundled_dir = ""

    def bundled(p: Path) -> bool:
        return bool(bundled_dir) and os.path.dirname(norm(p)) == bundled_dir

    enabled: set[str] = set()
    found: dict[str, Path] = {}

    def add(entry: Any, on: bool) -> None:
        s = str(entry).strip()
        if not s or s.lower().startswith(BUNDLED_PREFIX):
            return
        p = expand_pack_entry(s)
        candidates = sorted(p.glob("*.json")) if p.is_dir() else [p]
        for c in candidates:
            if bundled(c):
                continue
            key = norm(c)
            found.setdefault(key, c)
            if on:
                enabled.add(key)

    for entry in cfg.get("packs") or []:
        add(entry, True)
    for entry in cfg.get("packsDisabled") or []:
        add(entry, False)
    user_dir = Path(app_dir) / "packs"
    if user_dir.is_dir():
        for p in sorted(user_dir.glob("*.json")):
            key = norm(p)
            if not bundled(p):
                found.setdefault(key, p)
    return [(found[k], k in enabled) for k in sorted(found, key=lambda k: (found[k].name.lower(), k))]


def _accounts_state(flavor_root: Optional[Path]) -> list[tuple[set[str], dict[str, int]]]:
    """Per account with a NeedleDropperDB: (``importsHandled`` keys, user soundtrack id -> revision).
    Read-only (:func:`needledropper.savedvars.load_needledropper`); unreadable files are skipped."""
    if flavor_root is None:
        return []
    from . import savedvars

    try:
        state = savedvars.load_needledropper(flavor_root)
    except Exception:  # pragma: no cover - load_needledropper never raises for file problems
        return []
    out = []
    for acct in state.get("accounts", {}).values():
        db = acct.get("db")
        if not isinstance(db, dict):
            continue
        handled = {str(k) for k, v in savedvars.as_dict(db.get("importsHandled")).items() if v}
        tracks: dict[str, int] = {}
        for sid, st in savedvars.as_dict(db.get("soundtracks")).items():
            if isinstance(st, dict):
                rev = st.get("revision")
                tracks[str(sid)] = int(rev) if isinstance(rev, (int, float)) and not isinstance(rev, bool) else 0
        out.append((handled, tracks))
    return out


def _handled_everywhere(accounts: list[tuple[set[str], dict[str, int]]], pack: dict[str, Any]) -> bool:
    if not accounts:
        return False
    pid, rev = pack.get("id", ""), int(pack.get("revision") or 0)
    return all(f"{pid}@{rev}" in handled or tracks.get(pid, -1) >= rev for handled, tracks in accounts)


def cmd_convert_legacy(args: argparse.Namespace, out: Out = print) -> int:
    from . import gamedata
    from .config import load_config, paths_from_config

    try:
        cfg = load_config(Path(args.config) if getattr(args, "config", None) else None)
    except Exception as exc:  # ConfigError and friends
        out(f"Cannot read the app's config: {exc}")
        return EXIT_FAIL
    paths = paths_from_config(cfg)
    app_dir = Path(args.app_dir) if getattr(args, "app_dir", None) else paths.app_dir
    addons_dir = Path(args.addons_dir) if args.addons_dir else paths.addons_dir
    flavor_root = addons_dir.parent.parent if args.addons_dir else paths.flavor_root
    files = legacy_pack_files(cfg, app_dir)
    if not files:
        out("No soundtracks from the v1.1 NeedleDropper app were found; nothing to convert.")
        return EXIT_OK
    accounts = _accounts_state(flavor_root)
    imports: list[dict[str, Any]] = []
    failed = 0
    out_dir = Path(args.out_dir) if args.out_dir else None
    for path, active in files:
        data, problem = _load_pack_json(path)
        if problem:
            failed += 1
            out(f"SKIPPED {path.name}: {problem}")
            continue
        try:
            pack, warnings = sharecode.pack_for_sharing(data, name_hint=path.stem)
            code = sharecode.encode(pack)
        except ShareCodeError as exc:
            failed += 1
            out(f"SKIPPED {path.name}: {exc}")
            continue
        state = "on" if active else "off"
        if _handled_everywhere(accounts, pack):
            out(f"done    {path.name}: {_summary(pack)} -- already imported in game")
            continue
        out(f"convert {path.name}: {_summary(pack)} -- {len(code):,} characters, was {state}")
        for w in warnings:
            out(f"    warn  {w}")
        imports.append({"code": code, "active": bool(active)})
        if out_dir is not None:
            _write_text(out_dir / f"{pack['id']}.txt", code + "\n")
    if len(imports) > gamedata.MAX_IMPORTS:
        out(f"Only the first {gamedata.MAX_IMPORTS} soundtracks are offered (the addon keeps at most 50).")
        imports = imports[:gamedata.MAX_IMPORTS]
    if args.dry_run:
        out(f"Dry run: {len(imports)} soundtrack(s) would be offered to the game; nothing written.")
        return EXIT_FAIL if failed else EXIT_OK
    if imports or not failed:
        try:
            gamedata.ensure_companion_addon(addons_dir)
            gamedata.set_imports(addons_dir, imports)
            gamedata.flush(addons_dir)
        except (gamedata.GameDataError, OSError) as exc:
            out(f"Could not write the offers for the game: {exc}")
            return EXIT_FAIL
        w = gamedata.writer_for(addons_dir)
        if w.last_error:
            out(f"Could not write the offers for the game: {w.last_error}")
            return EXIT_FAIL
    if imports:
        out(f"Offered {len(imports)} soundtrack(s) to the game ({gamedata.companion_addon_dir(addons_dir)}). "
            "At the next login NeedleDropper asks whether to import them.")
    else:
        out("Nothing new to offer to the game.")
    return EXIT_FAIL if failed else EXIT_OK


# ------------------------------------------------------------------- parser ----

def _usage(parser: argparse.ArgumentParser) -> Callable[[argparse.Namespace], int]:
    def show(_args: argparse.Namespace) -> int:
        parser.print_help()
        return EXIT_USAGE
    return show


def register(subparsers: Any) -> argparse.ArgumentParser:
    """Add ``packs`` (validate / encode / decode / convert-legacy) to a CLI's subparsers.
    Each command sets ``func(args) -> exit status``."""
    p = subparsers.add_parser("packs", help="check, share and convert soundtrack packs (share codes)",
                              description="Check, share and convert soundtrack packs and share codes.")
    sub = p.add_subparsers(dest="packs_command", metavar="<action>")
    p.set_defaults(func=_usage(p))

    v = sub.add_parser("validate", help="check a pack file or a share code",
                       description="Check a pack file (packs/*.json) or a share code (text, a file holding "
                                   "one, or - for stdin).")
    v.add_argument("source", help="pack file, share code, file with a code, or - (stdin)")
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser("encode", help="make the share code of a pack file",
                       description="Print (or write) the share code of a pack file.")
    e.add_argument("file", help="pack file (.json)")
    e.add_argument("--out", metavar="FILE", help="write the code to FILE instead of printing it")
    e.set_defaults(func=cmd_encode)

    d = sub.add_parser("decode", help="turn a share code back into a pack file",
                       description="Decode a share code (text, a file holding one, or - for stdin) to pack JSON.")
    d.add_argument("source", help="share code, file with a code, or - (stdin)")
    d.add_argument("--out", metavar="FILE", help="write the pack to FILE instead of printing it")
    d.add_argument("--raw", action="store_true", help="exactly the JSON inside the code (not the validated form)")
    d.set_defaults(func=cmd_decode)

    c = sub.add_parser("convert-legacy", help="offer the v1.1 app's soundtracks to the game (one-time migration)",
                       description="Turn the v1.1 app's user packs into share codes and offer them to the addon "
                                   "through NeedleDropper_Companion (imported in game after confirmation).")
    c.add_argument("--addons-dir", metavar="DIR", help="WoW's Interface\\AddOns folder (default: from the config)")
    c.add_argument("--app-dir", metavar="DIR", help=argparse.SUPPRESS)
    c.add_argument("--out-dir", metavar="DIR", help="also write each share code to DIR\\<id>.txt")
    c.add_argument("--dry-run", action="store_true", help="show what would be offered; write nothing")
    c.set_defaults(func=cmd_convert_legacy)
    return p


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="needledropper", description="NeedleDropper soundtrack packs")
    ap.add_argument("--config", metavar="FILE", help="use this config.json instead of the one in the app folder")
    sub = ap.add_subparsers(dest="command", metavar="<command>")
    register(sub)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``needledropper packs ...`` on its own; ``argv`` without the leading ``packs`` is accepted too."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0] != "packs":
        args_list = ["packs", *args_list]
    try:
        args = build_parser().parse_args(args_list)
    except SystemExit as exc:  # argparse usage errors
        return int(exc.code or 0) if isinstance(exc.code, int) else EXIT_USAGE
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
