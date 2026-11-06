#!/usr/bin/env python3
"""Spotify lookups for auditing soundtrack packs (standard library only).

Requests go through the Spotify provider of the owner's scratch test player
(``poc_player.py``: ``Player().sp`` is a ``SpotifyProvider`` whose signed-in token lives in that
folder's ``appdata``). Only ``GET /search`` and ``GET /tracks/<id>`` are used, never with
``market=from_token`` (the token has no ``user-read-private`` scope). As of 2026-11-05 Spotify
answers this app without ``popularity`` or ``available_markets`` (shown as null / 0, so market
availability cannot be compared) and rejects search ``limit`` above 10 (clamped here; page
with ``--offset``).

The token file is shared, and Spotify rotates refresh tokens, so every request from every
process on this machine first takes one lock file and keeps the pace (0.5 s between requests by
default). A refresh can therefore never run in two processes at once. The ``serve`` command
keeps one provider in one process and answers lookups over ``http://127.0.0.1``; with
``--server`` (or a running server announced in the port file) the other commands become thin
clients of it.

Commands
  search QUERY [--limit 10] [--offset 0]           one track search, compact JSON
  track ID_OR_URI [...]                              GET /tracks/<id> for each, compact JSON
  candidates --pack FILE [--pack FILE] --out FILE    several searches per drop, ranked candidates
  serve [--port 0]                                   lookup server (Ctrl+C or kill to stop)

Environment: ``ND_POC_DIR`` overrides the scratch player folder.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import http.server
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_POC_DIR = (r"C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0"
                   r"\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\poc")
LOCK_FILE = Path(tempfile.gettempdir()) / "nd-spotify-lookup.lock"
PORT_FILE = Path(tempfile.gettempdir()) / "nd-spotify-lookup.port"
MIN_INTERVAL_S = 0.5
MAX_SEARCH_LIMIT = 10
TRACK_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


# ------------------------------------------------------------------- pacing --

class _GlobalPace:
    """Cross-process lock + pacing around every Spotify request on this machine."""

    def __init__(self, path: Path = LOCK_FILE, interval: float = MIN_INTERVAL_S) -> None:
        self.path = path
        self.interval = interval
        self._fh = None

    def __enter__(self) -> "_GlobalPace":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        deadline = time.monotonic() + 600
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"could not lock {self.path} for 10 minutes")
                time.sleep(0.1)
        stamp = self.path.with_suffix(".last")
        try:
            last = float(stamp.read_text(encoding="ascii").strip() or 0)
        except (OSError, ValueError):
            last = 0.0
        wait = last + self.interval - time.time()
        if wait > 0:
            time.sleep(min(wait, self.interval))
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.path.with_suffix(".last").write_text(f"{time.time():.3f}", encoding="ascii")
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None


# ------------------------------------------------------------------ backend --

class Spotify:
    """Direct access through poc_player's SpotifyProvider (one per process)."""

    def __init__(self, poc_dir: Optional[str] = None) -> None:
        poc = Path(poc_dir or os.environ.get("ND_POC_DIR") or DEFAULT_POC_DIR)
        if not (poc / "poc_player.py").is_file():
            raise SystemExit(f"error: poc_player.py not found in {poc}")
        sys.path.insert(0, str(poc))
        import poc_player  # noqa: E402  (inserts its own snapshot of the companion package)
        self.sp = poc_player.Player().sp
        if not self.sp.available():
            raise SystemExit(f"error: Spotify not available: {self.sp.unavailable_reason()}")

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        attempt = 0
        while True:
            attempt += 1
            with _GlobalPace():
                try:
                    return self.sp._call("GET", path, params=params).data
                except Exception as exc:  # noqa: BLE001
                    status = getattr(exc, "status", None)
                    retry = getattr(exc, "retry_after", None)
                    if status == 429 and attempt <= 4:
                        wait = min(60.0, float(retry or 5.0))
                        print(f"rate limited; waiting {wait:.0f} s", file=sys.stderr)
                    elif status in (500, 502, 503, 504) and attempt <= 3:
                        wait = 2.0 * attempt
                    else:
                        raise
            time.sleep(wait)

    def search(self, q: str, limit: int = 10, offset: int = 0) -> list[dict]:
        # Spotify rejects limit > 10 for this app (measured 2026-11-05); page with offset instead.
        limit = max(1, min(MAX_SEARCH_LIMIT, int(limit)))
        data = self._get("/search", {"q": q, "type": "track", "limit": limit, "offset": int(offset)})
        items = ((data or {}).get("tracks") or {}).get("items") or []
        return [compact(t) for t in items if isinstance(t, dict)]

    def track(self, tid: str) -> dict:
        return compact(self._get(f"/tracks/{track_id(tid)}"))


class Client:
    """Thin client of a running ``serve`` process."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def _get(self, path: str, params: dict) -> Any:
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error")
            except (ValueError, AttributeError):
                detail = None
            raise RuntimeError(detail or f"lookup server answered HTTP {exc.code}") from None
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(data["error"])
        return data

    def search(self, q: str, limit: int = 10, offset: int = 0) -> list[dict]:
        return self._get("/search", {"q": q, "limit": limit, "offset": offset})

    def track(self, tid: str) -> dict:
        return self._get("/track", {"id": track_id(tid)})


def backend(server: Optional[str], poc_dir: Optional[str]):
    if server == "none":
        return Spotify(poc_dir)
    if server:
        return Client(server)
    try:
        port = int(PORT_FILE.read_text(encoding="ascii").strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=3) as r:
            if r.status == 200:
                return Client(f"http://127.0.0.1:{port}")
    except (OSError, ValueError):
        pass
    return Spotify(poc_dir)


# ------------------------------------------------------------------ shaping --

def track_id(value: str) -> str:
    v = (value or "").strip()
    m = re.search(r"(?:spotify:track:|open\.spotify\.com/(?:intl-[a-z]{2}/)?track/)([A-Za-z0-9]{22})", v)
    if m:
        return m.group(1)
    if TRACK_ID_RE.match(v):
        return v
    raise ValueError(f"not a Spotify track id/uri/link: {value!r}")


def compact(t: dict) -> dict:
    album = t.get("album") or {}
    return {
        "id": t.get("id"),
        "uri": t.get("uri"),
        "name": t.get("name"),
        "artists": [a.get("name") for a in t.get("artists") or []],
        "artistIds": [a.get("id") for a in t.get("artists") or []],
        "album": album.get("name"),
        "albumType": album.get("album_type"),
        "albumArtists": [a.get("name") for a in album.get("artists") or []],
        "releaseDate": album.get("release_date"),
        "albumTracks": album.get("total_tracks"),
        "trackNumber": t.get("track_number"),
        "durationMs": t.get("duration_ms"),
        "explicit": t.get("explicit"),
        "isrc": (t.get("external_ids") or {}).get("isrc"),
        "popularity": t.get("popularity"),
        "markets": len(t.get("available_markets") or []),
        "US": "US" in (t.get("available_markets") or []),
        "isLocal": t.get("is_local"),
    }


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ð", "d").replace("Ð", "d").replace("þ", "th").replace("æ", "ae").replace("ø", "o")
    return s.casefold()


def norm(s: str) -> str:
    s = _fold(s).replace("&", " and ")
    s = re.sub(r"[\"'’`´]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


VERSION_WORDS = ["live", "remix", "mix", "edit", "version", "acoustic", "demo", "instrumental",
                 "karaoke", "cover", "tribute", "made famous", "originally performed", "re recorded",
                 "rerecorded", "extended", "radio", "single", "mono", "stereo", "remaster", "remastered",
                 "orchestral", "piano", "lullaby", "8 bit", "slowed", "sped up", "reverb", "nightcore",
                 "medley", "session", "unplugged", "reprise", "interlude", "from", "feat", "with"]


def title_core(s: str) -> str:
    s = re.sub(r"\s*[\(\[].*?[\)\]]", "", s or "")
    s = re.split(r"\s+-\s+", s)[0]
    return norm(s)


def version_tags(name: str) -> list[str]:
    extra = " ".join(re.findall(r"[\(\[](.*?)[\)\]]", name or "")) + " " + " ".join(re.split(r"\s+-\s+", name or "")[1:])
    n = f" {norm(extra)} "
    return [w for w in VERSION_WORDS if f" {w} " in n]


def split_artists(artist: str) -> list[str]:
    parts = re.split(r"\s*(?:&|,|\bfeat\.?|\bft\.?|\bx\b|\bwith\b)\s*", artist or "")
    return [p for p in (norm(x) for x in parts) if p]


def artist_match(pack_artist: str, cand: list[str]) -> str:
    cn = [norm(a) for a in cand]
    whole = norm(pack_artist)
    if whole in cn:
        return "exact" if cn and cn[0] == whole else "listed"
    parts = split_artists(pack_artist)
    if parts and all(p in cn for p in parts):
        return "all-parts"
    if parts and any(p in cn for p in parts):
        return "some-parts"
    if any(whole and (whole in a or a in whole) for a in cn if a):
        return "partial"
    return "none"


def rank(drop: dict, c: dict) -> dict:
    pack_ms = int(drop.get("durationMs") or 0)
    excerpt = "excerpt" in (drop.get("note") or "").lower()
    out = dict(c)
    out["artistMatch"] = artist_match(drop.get("artist", ""), c.get("artists") or [])
    out["titleMatch"] = ("exact" if norm(c.get("name") or "") == norm(drop.get("title", "")) else
                         "core" if title_core(c.get("name") or "") == title_core(drop.get("title", "")) else
                         "partial" if title_core(drop.get("title", "")) in norm(c.get("name") or "") else "none")
    out["versionTags"] = version_tags(c.get("name") or "")
    out["deltaMs"] = (int(c.get("durationMs") or 0) - pack_ms) if pack_ms else None
    a_score = {"exact": 0, "listed": 1, "all-parts": 1, "some-parts": 3, "partial": 4, "none": 9}[out["artistMatch"]]
    t_score = {"exact": 0, "core": 1, "partial": 3, "none": 9}[out["titleMatch"]]
    d = abs(out["deltaMs"]) if out["deltaMs"] is not None and not excerpt else 0
    d_score = 0 if d <= 3000 else 1 if d <= 10000 else 3
    v_score = sum(2 for w in out["versionTags"] if w in ("live", "remix", "karaoke", "cover", "tribute",
                                                          "made famous", "originally performed", "instrumental",
                                                          "acoustic", "demo", "lullaby", "8 bit", "slowed",
                                                          "sped up", "nightcore", "orchestral", "piano"))
    comp = 1 if (c.get("albumType") == "compilation") else 0
    out["score"] = a_score * 10 + t_score * 10 + d_score * 4 + v_score * 5 + comp * 2 + (1 if c.get("explicit") else 0)
    return out


def queries_for(drop: dict) -> list[str]:
    title, artist = drop.get("title", ""), drop.get("artist", "")
    core_title = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", title).strip().strip('"').strip() or title
    first_artist = re.split(r"\s*(?:&|,)\s*", artist)[0].strip() or artist
    qs = [f'track:"{title.strip(chr(34))}" artist:"{artist}"', f"{title} {artist}"]
    if core_title != title:
        qs.append(f'track:"{core_title}" artist:"{first_artist}"')
    if first_artist != artist:
        qs.append(f'track:"{core_title}" artist:"{first_artist}"')
    seen: set[str] = set()
    return [q for q in qs if not (q in seen or seen.add(q))]


# ------------------------------------------------------------------ commands --

def cmd_candidates(sp, packs: list[str], out_path: str, limit: int, only: Optional[str]) -> int:
    drops_out = []
    for p in packs:
        data = json.loads(Path(p).read_text(encoding="utf-8-sig"))
        for i, d in enumerate(data.get("drops") or []):
            if not isinstance(d, dict) or d.get("disabled") is True:
                continue
            key = f"{Path(p).name}[{i}]"
            if only and only not in key:
                continue
            entry = {"pack": Path(p).name, "index": i,
                     "place": d.get("subzone") or d.get("label") or d.get("zone"),
                     "zone": d.get("zone"), "artist": d.get("artist"), "title": d.get("title"),
                     "durationMs": d.get("durationMs"), "startMs": d.get("startMs"),
                     "excerpt": "excerpt" in (d.get("note") or "").lower(), "note": d.get("note"),
                     "existingSpotify": d.get("spotify"), "queries": [], "candidates": []}
            seen: dict[str, dict] = {}
            for q in queries_for(d):
                try:
                    items = sp.search(q, limit=limit)
                    entry["queries"].append({"q": q, "n": len(items)})
                except Exception as exc:  # noqa: BLE001
                    entry["queries"].append({"q": q, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                for it in items:
                    if it.get("id") and it["id"] not in seen:
                        seen[it["id"]] = rank(d, it)
                        seen[it["id"]]["foundBy"] = q
            entry["candidates"] = sorted(seen.values(), key=lambda c: (c["score"], -(c.get("popularity") or 0)))
            best = entry["candidates"][0] if entry["candidates"] else None
            print(f"{key:28} {d.get('artist')} - {d.get('title')}: {len(seen)} candidates; best "
                  + (f"{best['id']} {', '.join(best['artists'])} - {best['name']} [{best['album']}] "
                     f"d={best['deltaMs']} score={best['score']}" if best else "NONE"), file=sys.stderr)
            drops_out.append(entry)
    report = {"tool": "tools/pack_checks/spotify_lookup.py candidates",
              "generatedAt": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "packs": packs, "drops": drops_out}
    Path(out_path).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({len(drops_out)} drops)", file=sys.stderr)
    return 0


def cmd_serve(port: int, poc_dir: Optional[str]) -> int:
    sp = Spotify(poc_dir)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(time.strftime("%H:%M:%S ") + (fmt % args) + "\n")

        def _json(self, obj: Any, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            u = urllib.parse.urlparse(self.path)
            qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            try:
                if u.path == "/ping":
                    self._json({"ok": True})
                elif u.path == "/search":
                    self._json(sp.search(qs["q"], int(qs.get("limit", 10)), int(qs.get("offset", 0))))
                elif u.path == "/track":
                    self._json(sp.track(qs["id"]))
                else:
                    self._json({"error": "unknown path"}, 404)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    class Server(http.server.HTTPServer):
        request_queue_size = 64  # many clients may wait; requests are still answered one at a time

    srv = Server(("127.0.0.1", port), H)  # single-threaded: one Spotify request at a time
    PORT_FILE.write_text(str(srv.server_address[1]), encoding="ascii")
    print(f"serving Spotify lookups on http://127.0.0.1:{srv.server_address[1]}", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        try:
            if PORT_FILE.read_text(encoding="ascii").strip() == str(srv.server_address[1]):
                PORT_FILE.unlink()
        except OSError:
            pass
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", help="lookup server URL; 'none' forces direct access (default: auto)")
    ap.add_argument("--poc-dir", help="scratch player folder (default: ND_POC_DIR or the known path)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--offset", type=int, default=0)
    t = sub.add_parser("track")
    t.add_argument("ids", nargs="+")
    c = sub.add_parser("candidates")
    c.add_argument("--pack", action="append", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--limit", type=int, default=10)
    c.add_argument("--only", help="only drops whose 'pack.json[i]' key contains this text")
    v = sub.add_parser("serve")
    v.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        return cmd_serve(a.port, a.poc_dir)
    sp = backend(a.server, a.poc_dir)
    if a.cmd == "search":
        print(json.dumps(sp.search(a.query, a.limit, a.offset), indent=1, ensure_ascii=False))
    elif a.cmd == "track":
        print(json.dumps([sp.track(x) for x in a.ids], indent=1, ensure_ascii=False))
    elif a.cmd == "candidates":
        return cmd_candidates(sp, a.pack, a.out, a.limit, a.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
