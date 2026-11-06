#!/usr/bin/env python3
"""Find official YouTube uploads of a recording (standard library only).

Reads YouTube's public search results page and lists each video with its channel, the
channel's verification badge, its length and the description snippet. Auto-generated
"<Artist> - Topic" uploads (the snippet starts "Provided to YouTube by <label>") carry exactly
the released recording, so they are usually the best match for a pack's ``durationMs``.
``--oembed`` asks YouTube's oEmbed endpoint who uploaded a given id (title + channel); oEmbed
answering is NOT proof that the video plays in an embedded player (use yt_embed_check.py).

Usage
  python tools/pack_checks/yt_search.py "Leon Bridges River" [--limit 12] [--json]
  python tools/pack_checks/yt_search.py --oembed ID [ID ...]

Requests are paced (one per second per process). Nothing is opened in a browser.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator, Optional

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
_last = 0.0


def _get(url: str) -> str:
    global _last
    wait = _last + 1.0 - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    finally:
        _last = time.monotonic()


def _text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    return "".join(str(r.get("text", "")) for r in node.get("runs") or [] if isinstance(r, dict))


def _walk(node: Any, key: str) -> Iterator[dict]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, dict):
                yield v
            else:
                yield from _walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key)


def _initial_data(html: str) -> Optional[dict]:
    m = re.search(r"(?:var ytInitialData|window\[\"ytInitialData\"\])\s*=\s*(\{.+?\});\s*</script>", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def search(query: str, limit: int = 12) -> list[dict]:
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode(
        {"search_query": query, "hl": "en", "gl": "US"})
    data = _initial_data(_get(url))
    if data is None:
        raise RuntimeError("could not read YouTube's search results (consent page or layout change?)")
    out: list[dict] = []
    seen: set[str] = set()
    for vr in _walk(data, "videoRenderer"):
        vid = vr.get("videoId")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        owner = (vr.get("ownerText") or vr.get("longBylineText") or {})
        runs = owner.get("runs") or [{}]
        nav = ((runs[0].get("navigationEndpoint") or {}).get("browseEndpoint") or {})
        badges = [((b.get("metadataBadgeRenderer") or {}).get("style") or "").replace("BADGE_STYLE_TYPE_", "")
                  for b in vr.get("ownerBadges") or []]
        snippet = " ".join(_text(s.get("snippetText")) for s in vr.get("detailedMetadataSnippets") or [])
        out.append({
            "id": vid,
            "title": _text(vr.get("title")),
            "channel": _text(owner),
            "channelUrl": nav.get("canonicalBaseUrl"),
            "badges": [b for b in badges if b],
            "length": _text(vr.get("lengthText")) or None,
            "views": _text(vr.get("viewCountText")) or None,
            "published": _text(vr.get("publishedTimeText")) or None,
            "snippet": snippet or None,
            "topic": _text(owner).endswith(" - Topic"),
        })
        if len(out) >= limit:
            break
    return out


def oembed(vid: str) -> dict:
    url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
        {"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"})
    try:
        d = json.loads(_get(url))
        return {"id": vid, "ok": True, "title": d.get("title"), "author": d.get("author_name"),
                "authorUrl": d.get("author_url")}
    except urllib.error.HTTPError as exc:
        return {"id": vid, "ok": False, "http": exc.code}


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("query", nargs="*")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--oembed", action="store_true", help="treat the arguments as video ids for oEmbed")
    a = ap.parse_args(argv)
    if not a.query:
        ap.error("give a search query (or ids with --oembed)")
    if a.oembed:
        res = [oembed(v) for v in a.query]
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0
    res = search(" ".join(a.query), a.limit)
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
    else:
        for r in res:
            badge = f" [{','.join(r['badges'])}]" if r["badges"] else ""
            print(f"{r['id']}  {r['length'] or '-':>8}  {r['channel']}{badge} | {r['title']}"
                  + (f"\n             {r['snippet'][:140]}" if r.get("snippet") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
