#!/usr/bin/env python3
"""Apply audited media changes (Spotify pins, YouTube ids, lengths) to soundtrack packs.

Reads a decisions file and edits pack JSON in place without disturbing anything else:
UTF-8 without BOM, 2-space indent, ``ensure_ascii=False``, LF, trailing newline, original key
order. ``spotify`` is inserted just before ``youtube`` (SPEC 3.7 order), or where ``youtube``
would be when the drop has none. Every change is guarded: the drop at ``index`` must still have
the same ``artist`` and ``title`` as in the decisions file, otherwise the pack is left alone.

Decisions file::

    {"drops": [
      {"pack": "azeroth-mixtape.json", "index": 36, "artist": "Leon Bridges", "title": "River",
       "spotify": "3hhbDnFUb2bicI2df6VurK",      # bare id / URI / link; null or absent = untouched
       "youtube": "<11-char id>",                # new id; "" removes the field; absent = untouched
       "durationMs": 238560}                     # new length; absent/null = untouched
    ]}

Usage
  python tools/pack_checks/apply_media.py --decisions decisions.json packs/azeroth-mixtape.json packs/azeroth-unbound.json
  ... --dry-run    show the changes, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

TRACK_RE = re.compile(r"(?:spotify:track:|open\.spotify\.com/(?:intl-[a-z]{2}/)?track/)?([A-Za-z0-9]{22})(?:\?.*)?$")
YT_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def spotify_uri(value: str) -> str:
    m = TRACK_RE.search((value or "").strip())
    if not m:
        raise ValueError(f"not a Spotify track id: {value!r}")
    return f"spotify:track:{m.group(1)}"


def insert_before(d: dict, key: str, value: Any, anchors: list[str]) -> dict:
    """A copy of ``d`` with ``key`` set, placed before the first present anchor (else at the end)."""
    if key in d:
        out = dict(d)
        out[key] = value
        return out
    anchor = next((a for a in anchors if a in d), None)
    out: dict = {}
    for k, v in d.items():
        if k == anchor:
            out[key] = value
        out[k] = v
    if anchor is None:
        out[key] = value
    return out


def dump(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply(pack_path: Path, changes: list[dict], dry_run: bool) -> tuple[int, list[str]]:
    raw = pack_path.read_bytes()
    pack = json.loads(raw.decode("utf-8-sig"))
    drops = pack.get("drops") or []
    log: list[str] = []
    errors = 0
    for ch in changes:
        i = ch["index"]
        if not (0 <= i < len(drops)) or not isinstance(drops[i], dict):
            log.append(f"ERROR [{i}]: no such drop")
            errors += 1
            continue
        d = drops[i]
        if (d.get("artist"), d.get("title")) != (ch.get("artist"), ch.get("title")):
            log.append(f"ERROR [{i}]: expected {ch.get('artist')} - {ch.get('title')}, "
                       f"found {d.get('artist')} - {d.get('title')}")
            errors += 1
            continue
        new = dict(d)
        what = []
        if ch.get("spotify"):
            uri = spotify_uri(ch["spotify"])
            if new.get("spotify") != uri:
                what.append(f"spotify {new.get('spotify') or '-'} -> {uri}")
                new = insert_before(new, "spotify", uri, ["youtube", "startMs", "durationMs", "note", "tags"])
        if "youtube" in ch and ch["youtube"] is not None:
            yt = ch["youtube"].strip()
            if yt == "":
                if "youtube" in new:
                    what.append(f"youtube {new['youtube']} -> removed")
                    new.pop("youtube")
            else:
                if not YT_RE.match(yt):
                    log.append(f"ERROR [{i}]: bad YouTube id {yt!r}")
                    errors += 1
                    continue
                if new.get("youtube") != yt:
                    what.append(f"youtube {new.get('youtube') or '-'} -> {yt}")
                    if "youtube" in new:
                        new["youtube"] = yt
                    elif "spotify" in new:
                        keys = list(new.keys())
                        pos = keys.index("spotify") + 1
                        items = list(new.items())
                        items.insert(pos, ("youtube", yt))
                        new = dict(items)
                    else:
                        new = insert_before(new, "youtube", yt, ["startMs", "durationMs", "note", "tags"])
        if ch.get("durationMs") is not None:
            ms = int(ch["durationMs"])
            if new.get("durationMs") != ms:
                what.append(f"durationMs {new.get('durationMs')} -> {ms}")
                new["durationMs"] = ms
        if what:
            drops[i] = new
            log.append(f"[{i}] {d.get('artist')} - {d.get('title')}: " + "; ".join(what))
    if errors:
        log.append(f"{errors} error(s): {pack_path.name} NOT written")
        return errors, log
    out = dump(pack)
    if out != raw and not dry_run:
        write_atomic(pack_path, out)
        log.append(f"wrote {pack_path}")
    elif out == raw:
        log.append(f"{pack_path.name}: no changes")
    else:
        log.append(f"{pack_path.name}: dry run, nothing written")
    return 0, log


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("packs", nargs="+")
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    dec = json.loads(Path(a.decisions).read_text(encoding="utf-8-sig"))
    by_pack: dict[str, list[dict]] = {}
    for ch in dec.get("drops") or []:
        by_pack.setdefault(ch["pack"], []).append(ch)
    status = 0
    for p in a.packs:
        path = Path(p)
        changes = by_pack.get(path.name, [])
        errs, log = apply(path, changes, a.dry_run)
        print(f"== {path.name}: {len(changes)} decision(s)")
        for line in log:
            print("  " + line)
        status |= 1 if errs else 0
    return status


if __name__ == "__main__":
    raise SystemExit(main())
