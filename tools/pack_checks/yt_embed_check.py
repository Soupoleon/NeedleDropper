#!/usr/bin/env python3
"""Check that YouTube ids really play in an embedded IFrame player (standard library only).

oEmbed answering HTTP 200 is NOT an embeddability check: Azeroth Mixtape's Durotar id
``d_vPJjqWxZM`` passes oEmbed, yet the embedded player refuses it with error 150. This tool
asks the real YouTube IFrame Player API the way the NeedleDropper player page does
(``companion/needledropper/deck/static/player.js``): a page served from
``http://127.0.0.1:<port>`` creates ``YT.Player`` with ``origin: location.origin`` and the
same playerVars, then loads each video.

How a video is judged
  * Each id is loaded muted with ``loadVideoById``. It passes ("ok") only when the player
    reports PLAYING (state 1) for that same video id.
  * ``onError`` 2 / 5 / 100 / 101 / 150 / 153 fails with that code.
  * No PLAYING within ``--timeout`` seconds fails as "timeout": age-gated and "sign in to
    confirm" videos often never raise an error code. Timeouts (and other non-definitive
    outcomes) are retried once in a fresh browser with a longer timeout before they are final.
  * The player's own duration, title and channel (``getDuration`` / ``getVideoData``) are
    recorded for every id, so a pass on a 13-minute music video is visible next to the pack's
    ``durationMs``.

Controls run with every check: ``M7lc1UVf-VE`` (the IFrame API demo video) must play and
``zzzzzzzzzz0`` (no such video) must fail. If they do not, the results are not trustworthy and
the exit status is 3. ``d_vPJjqWxZM`` (Dolly Parton, "9 to 5") also runs as an *origin probe*:
it is reported, not judged.

The page origin matters (measured 2026-11-05): served from ``http://127.0.0.1:<port>``, YouTube
refuses most label-owned music with error 150 (the origin probe included), while the very same
ids play from ``http://localhost:<port>`` or ``http://<name>.localhost:<port>``. ``--page-host``
picks the host name. The default, ``127.0.0.1``, is what the NeedleDropper player page used when
this tool was written; always check under the origin the player really uses.

Nothing is shown on screen: the browser runs ``--headless=new --mute-audio`` with a throwaway
``--user-data-dir`` and its whole process tree is killed when the check ends. ``--browser
auto`` uses Edge unless msedge.exe carries an Image File Execution Options "Debugger" hook
(an Edge redirector such as Wedge, which could hand the URL to a visible default browser);
then it uses Chrome. A hooked Edge is never launched.

Usage
  python tools/pack_checks/yt_embed_check.py --pack packs/azeroth-mixtape.json \\
      --pack packs/azeroth-unbound.json --out yt-embed.json
  python tools/pack_checks/yt_embed_check.py d_vPJjqWxZM M7lc1UVf-VE
  python tools/pack_checks/yt_embed_check.py --ids-file candidates.txt --json

Exit status: 0 every checked id plays · 1 some ids fail · 2 usage/launch error ·
3 the controls did not behave (results untrustworthy).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

CONTROL_OK = "M7lc1UVf-VE"      # YouTube IFrame API demo video: embeddable from any origin
CONTROL_BAD = "zzzzzzzzzz0"     # no such video: an error (150) from every origin
ORIGIN_PROBE = "d_vPJjqWxZM"    # Dolly Parton - 9 to 5: 150 from 127.0.0.1, plays from localhost

ERROR_TEXT = {
    2: "invalid parameter / video id",
    5: "HTML5 player error",
    100: "video not found, removed or private",
    101: "owner does not allow embedding",
    150: "owner does not allow embedding (or blocked/age-restricted in embeds)",
    153: "missing referrer / origin (embed rejected)",
}
DEFINITIVE_CODES = {2, 100, 101, 150, 153}

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_ID_RES = [
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})"),
]


def normalize_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if ID_RE.match(v):
        return v
    for rx in URL_ID_RES:
        m = rx.search(v)
        if m:
            return m.group(1)
    return None


# ----------------------------------------------------------------- browser --

def _edge_deflected(edge_path: str) -> Optional[str]:
    """The IFEO Debugger that would run instead of ``edge_path``, or None."""
    try:
        import winreg
    except ImportError:  # not Windows
        return None
    base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\msedge.exe"
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return None
    with key:
        try:
            dbg = winreg.QueryValueEx(key, "Debugger")[0]
            if dbg:
                return str(dbg)
        except OSError:
            pass
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(key, sub) as sk:
                    dbg = winreg.QueryValueEx(sk, "Debugger")[0]
                    try:
                        filt = winreg.QueryValueEx(sk, "FilterFullPath")[0]
                    except OSError:
                        filt = ""
            except OSError:
                continue
            if dbg and (not filt or os.path.normcase(str(filt)) == os.path.normcase(edge_path)):
                return str(dbg)
    return None


def pick_browser(choice: str) -> str:
    def first(paths: list[str]) -> Optional[str]:
        return next((p for p in paths if p and os.path.isfile(p)), None)

    if choice in ("auto", "edge"):
        edge = first(EDGE_PATHS)
        if edge:
            hook = _edge_deflected(edge)
            if not hook:
                return edge
            msg = (f"msedge.exe is redirected by an Image File Execution Options Debugger ({hook}); "
                   "launching it could open a visible window, so Edge is not used")
            if choice == "edge":
                raise SystemExit(f"error: {msg}. Use --browser chrome.")
            print(f"note: {msg}; using Chrome instead.", file=sys.stderr)
        elif choice == "edge":
            raise SystemExit("error: Microsoft Edge not found")
    if choice in ("auto", "chrome"):
        chrome = first(CHROME_PATHS)
        if chrome:
            return chrome
        raise SystemExit("error: Google Chrome not found (pass --browser <path to a Chromium exe>)")
    if os.path.isfile(choice):
        if os.path.basename(choice).lower() == "msedge.exe" and _edge_deflected(choice):
            raise SystemExit("error: that msedge.exe is redirected by an IFEO Debugger; refusing to launch it")
        return choice
    raise SystemExit(f"error: browser {choice!r} not found")


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def remove_profile(path: str) -> None:
    for _ in range(20):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.5)


# -------------------------------------------------------------------- page --

PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>NeedleDropper embed check</title>
<style>
  body { margin: 0; background: #111; }
  #slots { display: grid; grid-template-columns: repeat(4, 400px); gap: 0; }
  #slots > div, #slots > iframe { width: 400px; height: 225px; }
</style></head>
<body><div id="slots"></div>
<script>
"use strict";
var CFG = __CONFIG__;
function post(kind, obj) {
  return fetch("/" + kind + "/" + CFG.token, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj || {})
  }).catch(function () {});
}
post("hello", { ua: navigator.userAgent, origin: location.origin });
var queue = CFG.ids.slice(), active = 0, finished = 0, seq = 0;
function pump() {
  while (active < CFG.concurrency && queue.length) {
    active++;
    check(queue.shift()).then(function (r) {
      post("result", r).then(function () {
        active--; finished++;
        if (finished === CFG.ids.length) post("done", {}); else pump();
      });
    });
  }
}
function now() { return Math.round(performance.now()); }
function check(id) {
  return new Promise(function (resolve) {
    var t0 = now(), slot = document.createElement("div"), player = null, done = false, timer = null;
    slot.id = "slot" + (seq++);
    document.getElementById("slots").appendChild(slot);
    var r = { id: id, status: "timeout", code: null, states: [], ready: false };
    function grab() {
      try { var vd = player && player.getVideoData ? player.getVideoData() : null;
            if (vd) { r.videoIdSeen = vd.video_id || null; r.title = vd.title || null; r.author = vd.author || null; } } catch (e) {}
      try { var d = player && player.getDuration ? player.getDuration() : 0; if (d) r.durationS = d; } catch (e) {}
      try { if (player && player.getCurrentTime) r.currentTimeS = player.getCurrentTime(); } catch (e) {}
      try { if (player && player.getPlayerState) r.finalState = player.getPlayerState(); } catch (e) {}
    }
    function finish(status, extra) {
      if (done) return;
      done = true; clearTimeout(timer);
      r.status = status;
      if (extra) for (var k in extra) r[k] = extra[k];
      r.elapsedMs = now() - t0;
      grab();
      try { player.stopVideo(); } catch (e) {}
      setTimeout(function () {
        try { player.destroy(); } catch (e) {}
        var f = document.getElementById(slot.id); if (f) f.remove();
        resolve(r);
      }, 100);
    }
    timer = setTimeout(function () {
      finish("timeout", { lastState: r.states.length ? r.states[r.states.length - 1].s : null });
    }, CFG.timeoutMs);
    try {
      player = new YT.Player(slot.id, {
        width: 400, height: 225,
        playerVars: { autoplay: 0, controls: 1, rel: 0, playsinline: 1, fs: 0, iv_load_policy: 3,
                      enablejsapi: 1, origin: location.origin, mute: 1 },
        events: {
          onReady: function () {
            r.ready = true; r.readyMs = now() - t0;
            try { player.mute(); player.loadVideoById({ videoId: id, startSeconds: 0 }); }
            catch (e) { finish("exception", { message: String(e) }); }
          },
          onStateChange: function (e) {
            r.states.push({ s: e.data, t: now() - t0 });
            if (e.data === 1 && !r.playingMs) {
              r.playingMs = now() - t0;
              setTimeout(function () {
                if (done) return;
                grab();
                if (r.videoIdSeen && r.videoIdSeen !== id) finish("mismatch");
                else finish("ok");
              }, 1500);
            }
          },
          onError: function (e) { finish("error", { code: e.data }); }
        }
      });
    } catch (e) { finish("exception", { message: String(e) }); }
  });
}
window.onYouTubeIframeAPIReady = function () { post("log", { msg: "iframe_api ready" }); pump(); };
var s = document.createElement("script");
s.src = "https://www.youtube.com/iframe_api";
s.onerror = function () { post("fatal", { msg: "could not load https://www.youtube.com/iframe_api" }); };
document.head.appendChild(s);
</script></body></html>
"""


class _State:
    def __init__(self, token: str, ids: list[str], concurrency: int, timeout_ms: int) -> None:
        self.token = token
        self.ids = ids
        self.concurrency = concurrency
        self.timeout_ms = timeout_ms
        self.results: dict[str, dict] = {}
        self.hello: Optional[dict] = None
        self.fatal: Optional[str] = None
        self.logs: list[str] = []
        self.done = threading.Event()
        self.started = threading.Event()
        self.lock = threading.Lock()


def _make_handler(state: _State, verbose: bool):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "yt-embed-check"

        def log_message(self, fmt: str, *args: Any) -> None:
            if verbose:
                sys.stderr.write("[http] " + (fmt % args) + "\n")

        def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == f"/check/{state.token}":
                cfg = {"ids": state.ids, "concurrency": state.concurrency,
                       "timeoutMs": state.timeout_ms, "token": state.token}
                page = PAGE.replace("__CONFIG__", json.dumps(cfg).replace("</", "<\\/"))
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found")

        def do_POST(self) -> None:  # noqa: N802
            parts = self.path.strip("/").split("/")
            if len(parts) != 2 or parts[1] != state.token:
                self._send(404, b"not found")
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            except (ValueError, UnicodeDecodeError):
                self._send(400, b"bad json")
                return
            kind = parts[0]
            with state.lock:
                if kind == "hello":
                    state.hello = data
                    state.started.set()
                elif kind == "result" and isinstance(data, dict) and data.get("id") in state.ids:
                    state.results[data["id"]] = data
                    if verbose:
                        sys.stderr.write(f"[result] {data.get('id')} {data.get('status')} {data.get('code')}\n")
                elif kind == "done":
                    state.done.set()
                elif kind == "fatal":
                    state.fatal = str(data.get("msg") or "fatal")
                    state.done.set()
                elif kind == "log":
                    state.logs.append(str(data.get("msg")))
            self._send(204)

    return Handler


class _QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # The browser is killed with keep-alive sockets open; a reset then is expected noise.
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def run_browser_pass(ids: list[str], *, browser: str, concurrency: int, timeout_s: float,
                     verbose: bool = False, keep_profile: bool = False,
                     user_agent: Optional[str] = None, page_host: str = "127.0.0.1") -> dict:
    """Load ``ids`` in one fresh headless browser; returns {"results": {id: result}, ...}."""
    token = secrets.token_urlsafe(16)
    state = _State(token, ids, concurrency, int(timeout_s * 1000))
    server = _QuietServer(("127.0.0.1", 0), _make_handler(state, verbose))
    server.daemon_threads = True
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, name="embed-check-http", daemon=True)
    thread.start()
    profile = tempfile.mkdtemp(prefix="nd-yt-embed-")
    url = f"http://{page_host}:{port}/check/{token}"
    args = [browser, "--headless=new", f"--user-data-dir={profile}",
            "--autoplay-policy=no-user-gesture-required", "--no-first-run",
            "--no-default-browser-check", "--mute-audio", "--disable-extensions",
            "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding", "--window-size=1600,1000"]
    if user_agent:
        args.append(f"--user-agent={user_agent}")
    args.append(url)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    started_at = time.monotonic()
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags)
    out: dict[str, Any] = {"origin": f"http://{page_host}:{port}", "browser": browser}
    try:
        if not state.started.wait(45):
            out["fatal"] = "the headless browser never loaded the check page"
        else:
            budget = 60 + (len(ids) / max(1, concurrency) + 1) * (timeout_s + 8)
            state.done.wait(budget)
            if not state.done.is_set():
                out["fatal"] = f"the check did not finish within {budget:.0f} s"
    finally:
        kill_tree(proc)
        server.shutdown()
        server.server_close()
        if not keep_profile:
            remove_profile(profile)
    with state.lock:
        out["results"] = dict(state.results)
        out["userAgent"] = (state.hello or {}).get("ua")
        out["logs"] = list(state.logs)
        if state.fatal:
            out["fatal"] = state.fatal
    out["seconds"] = round(time.monotonic() - started_at, 1)
    return out


# ------------------------------------------------------------------- input --

def load_pack_refs(pack_paths: list[str]) -> tuple[list[str], dict[str, list[dict]], list[dict]]:
    """YouTube ids in the packs (first-seen order), their references, and drops without one."""
    order: list[str] = []
    refs: dict[str, list[dict]] = {}
    missing: list[dict] = []
    for p in pack_paths:
        data = json.loads(Path(p).read_text(encoding="utf-8-sig"))
        for i, d in enumerate(data.get("drops") or []):
            if not isinstance(d, dict) or d.get("disabled") is True:
                continue
            place = d.get("subzone") or d.get("label") or d.get("zone") or d.get("instanceID")
            ref = {"pack": Path(p).name, "index": i, "place": str(place), "zone": d.get("zone"),
                   "artist": d.get("artist"), "title": d.get("title"),
                   "durationMs": d.get("durationMs"), "startMs": d.get("startMs")}
            vid = normalize_id(d.get("youtube"))
            if vid is None:
                if d.get("youtube"):
                    ref["badYoutube"] = d.get("youtube")
                missing.append(ref)
                continue
            if vid not in refs:
                order.append(vid)
                refs[vid] = []
            refs[vid].append(ref)
    return order, refs, missing


def read_ids_file(path: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
        items = data if isinstance(data, list) else data.get("ids", [])
    except ValueError:
        items = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    out = []
    for it in items:
        vid = normalize_id(it if isinstance(it, str) else (it or {}).get("id"))
        if vid:
            out.append(vid)
    return out


# -------------------------------------------------------------------- main --

def _fmt_dur(seconds: Optional[float]) -> str:
    if not seconds:
        return "-"
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="YouTube ids or links to check")
    ap.add_argument("--pack", action="append", default=[], help="pack JSON whose drops' youtube ids are checked (repeatable)")
    ap.add_argument("--ids-file", action="append", default=[], help="file with ids: one per line (# comments) or a JSON list")
    ap.add_argument("--out", help="write the full results as JSON to this file")
    ap.add_argument("--json", action="store_true", help="print the full results as JSON instead of a table")
    ap.add_argument("--browser", default="auto", help="auto (default) | edge | chrome | path to a Chromium exe")
    ap.add_argument("--concurrency", type=int, default=3, help="players loading at once (1-4, default 3)")
    ap.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for PLAYING (default 15)")
    ap.add_argument("--retry-timeout", type=float, default=40.0, help="seconds to wait on the retry pass (default 40)")
    ap.add_argument("--no-retry", action="store_true", help="do not retry non-definitive failures")
    ap.add_argument("--no-controls", action="store_true", help="skip the control videos")
    ap.add_argument("--user-agent", help="override the browser user agent")
    ap.add_argument("--page-host", default="127.0.0.1",
                    help="host name in the page URL (default 127.0.0.1, like the NeedleDropper player; "
                         "'localhost' or '<name>.localhost' also reach the loopback server)")
    ap.add_argument("--keep-profile", action="store_true", help="keep the temporary browser profile (debugging)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    if a.page_host not in ("127.0.0.1", "localhost") and not a.page_host.endswith(".localhost"):
        ap.error("--page-host must be 127.0.0.1, localhost or <name>.localhost (the server only listens on loopback)")
    concurrency = max(1, min(4, a.concurrency))
    ids: list[str] = []
    refs: dict[str, list[dict]] = {}
    missing: list[dict] = []
    if a.pack:
        ids, refs, missing = load_pack_refs(a.pack)
    for f in a.ids_file:
        ids += read_ids_file(f)
    for raw in a.ids:
        vid = normalize_id(raw)
        if not vid:
            print(f"error: not a YouTube id or link: {raw!r}", file=sys.stderr)
            return 2
        ids.append(vid)
    seen: set[str] = set()
    ids = [v for v in ids if not (v in seen or seen.add(v))]
    controls = [] if a.no_controls else [CONTROL_OK, CONTROL_BAD, ORIGIN_PROBE]
    if not ids and not controls:
        ap.error("nothing to check")

    browser = pick_browser(a.browser)
    first_ids = controls + [v for v in ids if v not in controls]
    print(f"checking {len(ids)} id(s) + {len(controls)} control(s) in {os.path.basename(browser)} "
          f"(headless, page http://{a.page_host}:<port>, concurrency {concurrency}, "
          f"timeout {a.timeout:.0f} s)", file=sys.stderr)
    p1 = run_browser_pass(first_ids, browser=browser, concurrency=concurrency, timeout_s=a.timeout,
                          verbose=a.verbose, keep_profile=a.keep_profile, user_agent=a.user_agent,
                          page_host=a.page_host)
    if p1.get("fatal") and not p1["results"]:
        print(f"error: {p1['fatal']}", file=sys.stderr)
        return 2
    attempts: dict[str, list[dict]] = {v: [] for v in first_ids}
    for v in first_ids:
        attempts[v].append(p1["results"].get(v) or {"id": v, "status": "no-result"})

    def needs_retry(res: dict) -> bool:
        return res.get("status") != "ok" and not (res.get("status") == "error" and res.get("code") in DEFINITIVE_CODES)

    passes = [{k: p1.get(k) for k in ("origin", "browser", "userAgent", "seconds", "fatal")}]
    retry_ids = [v for v in first_ids if needs_retry(attempts[v][-1])]
    if retry_ids and not a.no_retry:
        print(f"retrying {len(retry_ids)} non-definitive result(s) in a fresh browser "
              f"(timeout {a.retry_timeout:.0f} s)", file=sys.stderr)
        p2 = run_browser_pass(retry_ids, browser=browser, concurrency=min(concurrency, 2),
                              timeout_s=a.retry_timeout, verbose=a.verbose,
                              keep_profile=a.keep_profile, user_agent=a.user_agent,
                              page_host=a.page_host)
        passes.append({k: p2.get(k) for k in ("origin", "browser", "userAgent", "seconds", "fatal")})
        for v in retry_ids:
            attempts[v].append(p2["results"].get(v) or {"id": v, "status": "no-result"})

    results = []
    for v in first_ids:
        final = next((r for r in attempts[v] if r.get("status") == "ok"), attempts[v][-1])
        entry = {"id": v, "status": final.get("status"), "code": final.get("code"),
                 "codeText": ERROR_TEXT.get(final.get("code")) if final.get("code") is not None else None,
                 "title": final.get("title"), "author": final.get("author"),
                 "durationS": final.get("durationS"), "videoIdSeen": final.get("videoIdSeen"),
                 "attempts": attempts[v], "control": v in controls and v not in ids,
                 "refs": refs.get(v, [])}
        for ref in entry["refs"]:
            if entry["durationS"] and ref.get("durationMs"):
                ref["deltaS"] = round(entry["durationS"] - ref["durationMs"] / 1000.0, 1)
        results.append(entry)

    by_id = {r["id"]: r for r in results}
    controls_ok = True
    control_notes = []
    if controls:
        good, bad = by_id.get(CONTROL_OK, {}), by_id.get(CONTROL_BAD, {})
        if good.get("status") != "ok":
            controls_ok = False
            control_notes.append(f"positive control {CONTROL_OK} did not play ({good.get('status')} {good.get('code')})")
        if bad.get("status") == "ok":
            controls_ok = False
            control_notes.append(f"negative control {CONTROL_BAD} played")
        probe = by_id.get(ORIGIN_PROBE, {})
        control_notes.append(f"origin probe {ORIGIN_PROBE}: {probe.get('status')}"
                             + (f" {probe.get('code')}" if probe.get("code") is not None else ""))

    report = {
        "tool": "tools/pack_checks/yt_embed_check.py",
        "checkedAt": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "browser": browser, "pageHost": a.page_host, "passes": passes, "controlsOk": controls_ok,
        "controlNotes": control_notes,
        "counts": {
            "checked": len(ids),
            "ok": sum(1 for r in results if r["id"] in ids and r["status"] == "ok"),
            "failed": sum(1 for r in results if r["id"] in ids and r["status"] != "ok"),
        },
        "results": results, "dropsWithoutYoutube": missing,
    }
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for r in results:
            tag = "CONTROL " if r["control"] else ""
            ref = r["refs"][0] if r["refs"] else {}
            where = f"{ref.get('pack', '')}[{ref.get('index', '')}] {ref.get('place', '')}: " if ref else ""
            delta = f" d={ref.get('deltaS'):+.0f}s" if ref.get("deltaS") is not None else ""
            code = f" {r['code']}" if r["code"] is not None else ""
            print(f"{tag}{r['id']}  {str(r['status']).upper()}{code}  {_fmt_dur(r['durationS'])}{delta}  "
                  f"{where}{r.get('author') or '?'} | {r.get('title') or '?'}")
        print(f"\n{report['counts']['ok']} ok, {report['counts']['failed']} failed of {len(ids)} "
              f"from http://{a.page_host}:<port>; controls {'OK' if controls_ok else 'FAILED'}"
              + (f" ({'; '.join(control_notes)})" if control_notes else ""))
    if not controls_ok:
        return 3
    return 0 if report["counts"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
