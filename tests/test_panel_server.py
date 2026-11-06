"""Deck server (SPEC-v1.1 C.1, C.3, C.4; DESIGN-v1.2 1.4): the Connect page and the player page
with the token meta and strict headers, /healthz, API authentication (token, Host, Origin, body
cap), SSE client kinds, the player-status hook, and the player window policy (only YouTube songs
may open it)."""
from __future__ import annotations

import re
import time
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import SSE, FakePlayer, http_call, wait_for  # noqa: E402

from needledropper import __version__  # noqa: E402
from needledropper.deck import server  # noqa: E402
from needledropper.deck.server import MAX_BODY_BYTES, Deck  # noqa: E402
from needledropper.models import Drop  # noqa: E402
from needledropper.providers.base import ProviderError  # noqa: E402
from needledropper.providers.local import LocalProvider  # noqa: E402
from needledropper.providers.youtube import YouTubeProvider  # noqa: E402


class ApiSpy:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, method, path, query, body):
        self.calls.append((method, path, query, body))
        return 200, {"ok": True, "echo": body}


@pytest.fixture
def deck():
    launched: list[list[str]] = []
    d = Deck(port=0, heartbeat_sec=0.2, launcher=launched.append, url_opener=lambda url: False)
    d.launched = launched  # type: ignore[attr-defined]
    d.api = ApiSpy()
    d.start()
    try:
        yield d
    finally:
        d.stop()


def drop(**kw) -> Drop:
    base = dict(n=1, id="zone:1429", kind="zone", title="T", artist="A", label="Elwynn Forest")
    base.update(kw)
    return Drop(**base)


# ----------------------------------------------------------- pages/health --

def test_healthz_identifies_the_app_without_a_token(deck):
    st, data, _h = http_call(deck.port, "GET", "/healthz")
    assert st == 200 and data == {"app": "needledropper", "version": __version__}
    st, _d, _h = http_call(deck.port, "GET", "/healthz", headers={"Host": "evil.example"})
    assert st == 403


def test_pages_carry_the_token_meta_and_strict_headers(deck):
    for path, needle in (("/", "connect.js"), ("/player", "player.js")):
        st, body, hdrs = http_call(deck.port, "GET", path)
        html = body.decode("utf-8")
        assert st == 200 and needle in html
        m = re.search(r'<meta name="nd-token" content="([^"]+)">', html)
        assert m and m.group(1) == deck.token and "{{ND_TOKEN}}" not in html
        assert hdrs["Cache-Control"] == "no-store"
        assert hdrs["X-Frame-Options"] == "DENY"
        csp = hdrs["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp and "object-src 'none'" in csp
    _st, _b, hdrs = http_call(deck.port, "GET", "/player")
    assert "https://www.youtube.com" in hdrs["Content-Security-Policy"]
    assert hdrs["Referrer-Policy"] == "strict-origin-when-cross-origin"  # YouTube error 153 otherwise
    _st, _b, hdrs = http_call(deck.port, "GET", "/")
    assert "youtube" not in hdrs["Content-Security-Policy"] and hdrs["Referrer-Policy"] == "no-referrer"
    assert http_call(deck.port, "GET", "/", headers={"Host": "evil.example:80"})[0] == 403


def test_tokens_are_random_per_process():
    a, b = Deck(port=0), Deck(port=0)
    assert a.token != b.token and len(a.token) >= 32


# -------------------------------------------------------------- API auth --

def test_api_requires_the_token(deck):
    spy: ApiSpy = deck.api
    assert http_call(deck.port, "GET", "/api/state")[0] == 401
    assert http_call(deck.port, "GET", "/api/state", token="")[0] == 401
    assert http_call(deck.port, "GET", "/api/state", token="not-the-token")[0] == 401
    assert http_call(deck.port, "GET", "/api/state", token=deck.token[:-1])[0] == 401
    assert http_call(deck.port, "POST", "/api/stop", {}, token="nope")[0] == 401
    assert spy.calls == []
    st, data, hdrs = http_call(deck.port, "GET", "/api/state?include=drops", token=deck.token)
    assert st == 200 and data["ok"] is True and hdrs["Cache-Control"] == "no-store"
    assert spy.calls == [("GET", "/api/state", {"include": "drops"}, None)]
    st, data, _h = http_call(deck.port, "POST", "/api/test", {"provider": "spotify"}, token=deck.token)
    assert st == 200 and data["echo"] == {"provider": "spotify"}


def test_token_is_compared_in_constant_time(deck, monkeypatch):
    seen: list[tuple] = []
    real = server.hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(server.hmac, "compare_digest", spy)
    assert http_call(deck.port, "GET", "/api/state", token=deck.token)[0] == 200
    assert seen and seen[-1][1] == deck.token.encode("ascii")


def test_api_rejects_bad_host_and_foreign_origin(deck):
    tok = deck.token
    spy: ApiSpy = deck.api
    for host in ("evil.example", f"evil.example:{deck.port}", f"127.0.0.1:{deck.port + 1}", f"[::1]:{deck.port}",
                 f"127.0.0.1.nip.io:{deck.port}"):
        assert http_call(deck.port, "GET", "/api/state", token=tok, headers={"Host": host})[0] == 403, host
        assert http_call(deck.port, "POST", "/api/stop", {}, token=tok, headers={"Host": host})[0] == 403, host
    for origin in ("https://evil.example", "null", f"http://127.0.0.1:{deck.port + 1}", f"https://127.0.0.1:{deck.port}"):
        assert http_call(deck.port, "POST", "/api/stop", {}, token=tok, headers={"Origin": origin})[0] == 403, origin
        assert http_call(deck.port, "GET", "/api/state", token=tok, headers={"Origin": origin})[0] == 403, origin
    assert spy.calls == []
    for origin in (f"http://127.0.0.1:{deck.port}", f"http://localhost:{deck.port}"):
        assert http_call(deck.port, "POST", "/api/stop", {}, token=tok, headers={"Origin": origin})[0] == 200
    assert http_call(deck.port, "GET", "/api/state", token=tok, headers={"Host": f"localhost:{deck.port}"})[0] == 200


def test_api_caps_request_bodies(deck):
    tok = deck.token
    spy: ApiSpy = deck.api
    big = b'{"x": "' + b"a" * MAX_BODY_BYTES + b'"}'
    st, data, _h = http_call(deck.port, "POST", "/api/config", raw=big, token=tok)
    assert st == 413 and "too large" in data["error"]
    exact = b"{}" + b" " * (MAX_BODY_BYTES - 2)
    assert http_call(deck.port, "POST", "/api/config", raw=exact, token=tok)[0] == 200
    assert http_call(deck.port, "POST", "/api/config", raw=b"{not json", token=tok)[0] == 400
    st, data, _h = http_call(deck.port, "POST", "/api/config", token=tok,
                             headers={"Transfer-Encoding": "chunked"})
    assert st == 411
    st, data, _h = http_call(deck.port, "POST", "/api/config", raw=b"{}", token=tok, headers={"Content-Length": "-5"})
    assert st == 400
    assert len(spy.calls) == 1  # only the exactly-64-KB body got through


def test_api_needs_a_companion(deck):
    deck.api = None
    st, data, _h = http_call(deck.port, "GET", "/api/state", token=deck.token)
    assert st == 503 and "not available" in data["error"]
    assert http_call(deck.port, "GET", "/api/state")[0] == 401  # auth is checked first


def test_api_errors_are_reported_not_fatal(deck):
    def broken(method, path, query, body):
        raise RuntimeError("boom")

    deck.api = broken
    st, data, _h = http_call(deck.port, "GET", "/api/state", token=deck.token)
    assert st == 500 and "boom" in data["error"]
    assert http_call(deck.port, "GET", "/healthz")[0] == 200  # still serving


# ------------------------------------------------------------ SSE kinds --

def test_playback_commands_go_to_player_clients_only(deck):
    panel = SSE(deck.port, "panel").pump()
    player = SSE(deck.port, "player", key=deck.player_key).pump()  # v1.2.1 ON-1: the launch key
    try:
        assert wait_for(lambda: deck.panel_count == 1 and deck.player_count == 1)
        assert deck.send({"cmd": "play", "playId": "p", "source": "youtube", "videoId": "x"}) == 1
        assert deck.send({"cmd": "show", "label": "Elwynn Forest"}) == 2
        assert deck.notify_panels("spotify") == 1
        assert deck.send({"cmd": "stop", "playId": "p"}) == 1
        assert wait_for(lambda: len(player.messages) >= 3 and len(panel.messages) >= 2)
        assert [m["cmd"] for m in player.messages] == ["play", "show", "stop"]
        assert [m["cmd"] for m in panel.messages if m["cmd"] != "state" or m.get("what") != "player"] == \
            ["show", "state"]
    finally:
        panel.close()
        player.close()


def test_open_panels_do_not_count_as_a_player(deck):
    panel = SSE(deck.port, "panel")
    try:
        assert wait_for(lambda: deck.panel_count == 1)
        assert not deck.connected and not deck.page_open and deck.player_count == 0
        # v1.2.1 review ON-1: a player page without this start's launch key (left by an earlier start, a v1.0
        # page, opened by hand) is told to retire and never counts as the player
        stale = SSE(deck.port, None)
        try:
            assert stale.status == 200 and stale.next_data() == {"cmd": "retire"}
            with pytest.raises(EOFError):
                stale.next_data(timeout=2)
            assert not deck.player_connected and not deck.page_open
        finally:
            stale.close()
        player = SSE(deck.port, None, key=deck.player_key)  # no ?client= -> a player
        try:
            assert wait_for(lambda: deck.player_connected)
            assert deck.connected and deck.page_open
        finally:
            player.close()
    finally:
        panel.close()
    assert SSE(deck.port, "bogus").status == 400
    st, _d, _h = http_call(deck.port, "GET", "/events?client=player", headers={"Origin": "https://evil.example"})
    assert st == 403


def test_panels_are_nudged_when_a_player_connects(deck):
    panel = SSE(deck.port, "panel").pump()
    try:
        assert wait_for(lambda: deck.panel_count == 1)
        player = SSE(deck.port, "player", key=deck.player_key)  # v1.2.1 ON-1: the launch key
        assert wait_for(lambda: any(m == {"cmd": "state", "what": "player"} for m in panel.messages))
        player.close()
    finally:
        panel.close()


def test_replay_differs_by_client_kind(deck):
    deck.send({"cmd": "show", "dropId": "zone:1", "label": "L"})
    deck.send({"cmd": "play", "playId": "p1", "source": "local", "url": "u", "startMs": 0})
    panel, player = SSE(deck.port, "panel"), SSE(deck.port, "player", key=deck.player_key)  # ON-1 key
    try:
        assert panel.next_data()["cmd"] == "show"
        assert [player.next_data()["cmd"], player.next_data()["cmd"]] == ["show", "play"]
        deck.notify_panels()
        assert panel.next_data()["cmd"] == "state"  # the panel never got the play command
    finally:
        panel.close()
        player.close()


# --------------------------------------------------------- player window --

def test_open_player_builds_the_app_window_command(deck, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": r"C:\Fake\Edge\msedge.exe")
    deck.configure_player(browser="edge", position=(1500, 40), profile_dir=tmp_path / "deck-browser")
    assert deck.open_player() == "edge"
    args = deck.launched[-1]
    assert args[0].endswith("msedge.exe")
    # D12: YouTube error 150 from 127.0.0.1; ON-1 / RCOMP-3: the launch key in the address fragment
    assert f"--app=http://localhost:{deck.port}/player#k={deck.player_key}" in args
    assert "--window-size=420,380" in args and "--window-position=1500,40" in args
    assert f"--user-data-dir={tmp_path / 'deck-browser'}" in args
    deck.configure_player(clear_position=True)
    deck.open_player()
    assert not any(a.startswith("--window-position") for a in deck.launched[-1])
    deck.configure_player(browser="none")
    assert deck.open_player() == "none" and len(deck.launched) == 2


def test_open_player_never_falls_back_to_the_default_browser(monkeypatch):
    """Real-client finding #8: a tab in the default browser took the owner out of the game. Without
    Edge or Chrome there is no player window (the drop plays on another provider or shows its
    card). This test used to assert the default-browser fallback."""
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": None)
    opened: list[str] = []
    d = Deck(port=0, launcher=lambda a: pytest.fail("no browser exe"), url_opener=lambda u: opened.append(u) or True)
    d.start()
    try:
        assert d.open_player() == "none" and opened == []
        d.configure_player(browser="default")             # an old config: Edge / Chrome, never a tab
        assert d.open_player() == "none" and opened == []
    finally:
        d.stop()


def test_ensure_player_honours_auto_open(monkeypatch):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": "msedge.exe")
    d = Deck(port=0, heartbeat_sec=0.2)
    player = FakePlayer(d)
    d.launcher = player
    d.start()
    try:
        d.configure_player(auto_open="never")
        assert d.ensure_player(0.5) is False and player.launches == []
        d.configure_player(auto_open="youtube")
        assert d.ensure_player(5) is True and len(player.launches) == 1
        assert d.ensure_player(5) is True and len(player.launches) == 1  # already open: no second window
        player.close()
        assert wait_for(lambda: not d.page_open)
        d.configure_player(auto_open="never")
        assert d.ensure_player(5, force=True) is True and len(player.launches) == 2  # an explicit Test click
    finally:
        player.close()
        d.stop()


def test_youtube_drop_opens_the_player_and_waits_for_it(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": "msedge.exe")
    d = Deck(port=0, heartbeat_sec=0.2)
    player = FakePlayer(d)
    d.launcher = player
    d.start()
    try:
        yt = YouTubeProvider({"enabled": True}, None, d, connect_timeout=5)
        song = drop(youtube="dQw4w9WgXcQ")
        assert yt.available() and not d.page_open
        h = yt.play(song, fade_in_ms=0)
        assert len(player.launches) == 1 and any("/player" in a for a in player.launches[0])
        assert h.provider == "youtube" and d.page_open
        assert any(c.get("cmd") == "play" and c.get("videoId") == "dQw4w9WgXcQ" for c in player.commands)
        yt.play(song)  # the window is open now: no second one
        assert len(player.launches) == 1
    finally:
        player.close()
        d.stop()


def test_youtube_without_a_player_falls_through(monkeypatch):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": "msedge.exe")
    launched: list = []
    d = Deck(port=0, launcher=launched.append).start()  # a window that never connects
    try:
        yt = YouTubeProvider({}, None, d, connect_timeout=0.3)
        with pytest.raises(ProviderError, match="did not connect within"):
            yt.play(drop(youtube="dQw4w9WgXcQ"))
        assert len(launched) == 1
        d2 = Deck(port=0, launcher=launched.append).start()
        try:
            d2.configure_player(auto_open="never")
            yt2 = YouTubeProvider({}, None, d2, connect_timeout=0.3)
            assert not yt2.available()
            with pytest.raises(ProviderError, match="opening it automatically is off"):
                yt2.play(drop(youtube="dQw4w9WgXcQ"))
            assert len(launched) == 1  # "never" opened nothing
        finally:
            d2.stop()
    finally:
        d.stop()


def test_no_window_for_spotify_local_or_in_game_drops(monkeypatch, tmp_path):
    """The in-game title card is the only popup: only a YouTube drop may open the player."""
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": "msedge.exe")
    launched: list = []
    d = Deck(port=0, launcher=launched.append, url_opener=launched.append).start()
    try:
        media = tmp_path / "media"
        media.mkdir()
        (media / "a.mp3").write_bytes(b"ID3")
        local = LocalProvider({"mediaDir": str(media)}, None, d)
        assert local.can_play(drop(file="a.mp3")) and not local.available()
        with pytest.raises(ProviderError):
            local.play(drop(file="a.mp3"))
        yt = YouTubeProvider({}, None, d, connect_timeout=0.2)
        assert not yt.can_play(drop(file="a.mp3"))  # no video id: never a window for it
        assert launched == []
    finally:
        d.stop()


# ---------------------------------------------------------- Connect page --

STATIC = Path(server.STATIC_DIR)


def test_connect_page_is_served_at_every_alias_and_the_old_panel_is_gone(deck):
    for path in ("/", "/index.html", "/connect", "/setup", "/panel"):
        st, body, hdrs = http_call(deck.port, "GET", path)
        html = body.decode("utf-8")
        assert st == 200 and "<title>Connect NeedleDropper</title>" in html, path
        assert f'<meta name="nd-token" content="{deck.token}">' in html
        assert hdrs["Cross-Origin-Opener-Policy"] == "same-origin"
        csp = hdrs["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "https:" not in csp
        assert "'unsafe-inline'" not in csp and "form-action 'none'" in csp
    for name, ctype in (("connect.js", "text/javascript"), ("connect.css", "text/css"), ("favicon.svg", "image/svg")):
        st, _b, hdrs = http_call(deck.port, "GET", f"/static/{name}")
        assert st == 200 and hdrs["Content-Type"].startswith(ctype), name
    for gone in ("panel.js", "panel.css", "panel.html", "connect.html", "../server.py"):
        assert http_call(deck.port, "GET", f"/static/{gone}")[0] == 404, gone
    assert not any(STATIC.glob("panel.*"))


def test_connect_page_has_no_inline_code_and_every_script_target_exists():
    html = (STATIC / "connect.html").read_text(encoding="utf-8")
    js = (STATIC / "connect.js").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> is blocked by the CSP"
    assert "<style" not in html and not re.search(r"\sstyle=\"", html) and not re.search(r"\son[a-z]+=", html)
    ids = set(re.findall(r'\sid="([^"]+)"', html))
    used = set(re.findall(r'\$\("([^"]+)"\)', js))
    assert used and used <= ids, sorted(used - ids)
    assert "X-ND-Token" in js and 'meta[name="nd-token"]' in js and "/events?client=panel" in js
    # the four cards and the footer of DESIGN-v1.2 1.4, in order
    titles = re.findall(r'<h2 id="[^"]+">([^<]+)', html)
    assert [t.strip() for t in titles] == ["World of Warcraft", "Spotify", "YouTube", "Check the link"]
    assert "/needle test" in html and "Start with Windows" in html and ">Done<" in html
    assert 'id="autostart" checked' in html  # ticked by default
    for endpoint in ("/api/state", "/api/config", "/api/spotify/connect", "/api/spotify/disconnect",
                     "/api/spotify/devices", "/api/wow/detect", "/api/addon/install", "/api/open"):
        assert endpoint in js, endpoint
    for retired in ("/api/test", "/api/stop", "/api/map", "/api/packs", "/api/spotify/resolve"):
        assert retired not in js, retired


def test_connect_page_is_accessible():
    html = (STATIC / "connect.html").read_text(encoding="utf-8")
    assert '<html lang="en">' in html and 'name="viewport"' in html and 'class="skip-link" href="#main"' in html
    assert '<main id="main"' in html and "<header" in html and "<footer" in html
    # every input/select has a label (for=...) or sits inside one
    for m in re.finditer(r"<(input|select)\b[^>]*\bid=\"([^\"]+)\"", html):
        control_id = m.group(2)
        labelled = f'for="{control_id}"' in html
        inside = html.rfind("<label", 0, m.start()) > html.rfind("</label>", 0, m.start())
        assert labelled or inside, control_id
    assert html.count('aria-live="polite"') >= 2 and 'role="alert"' in html
    # states are words + a glyph, never colour alone
    assert html.count('class="status-text"') == 4
    # links that open a new tab say so
    for m in re.finditer(r'<a\b[^>]*target="_blank"[^>]*>(.*?)</a>', html, re.S):
        assert "opens in a new tab" in m.group(1)
        assert 'rel="noopener noreferrer"' in m.group(0)
    css = (STATIC / "connect.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css and ":focus-visible" in css and "max-width: 560px" in css


def test_the_player_status_hook_hears_reports_with_the_song(deck):
    heard: list[tuple] = []
    deck.on_player_status = lambda st, play, card: heard.append((st, play, card))
    deck.send({"cmd": "show", "dropId": "zone:1429", "label": "Elwynn Forest", "title": "T", "artist": "A"})
    deck.send({"cmd": "play", "playId": "p1", "source": "youtube", "videoId": "abcdefghijk", "dropId": "zone:1429"})
    st, _d, _h = http_call(deck.port, "POST", "/status", {"state": "error", "playId": "p1", "code": 150,
                                                          "detail": "youtube 150"}, token=deck.token)
    assert st == 204
    assert wait_for(lambda: len(heard) == 1)
    status, play, card = heard[0]
    assert status["state"] == "error" and status["code"] == 150
    assert play["videoId"] == "abcdefghijk" and card["title"] == "T"
    # an unknown play id still reports (without a play command); a broken listener never breaks /status

    def broken(*a):
        raise RuntimeError("listener bug")

    deck.on_player_status = broken
    st, _d, _h = http_call(deck.port, "POST", "/status", {"state": "playing", "playId": "zz"}, token=deck.token)
    assert st == 204 and deck.last_status["playId"] == "zz"


def test_refused_posts_still_get_their_answer_when_the_body_arrives_late(deck):
    """A POST refused before its body was read (bad token here) must read the body first:
    closing with unread bytes makes Windows reset the connection instead of answering 401."""
    import socket as _socket

    body = b'{"what": "logs"}' + b" " * 3000
    for headers in ((f"Host: 127.0.0.1:{deck.port}", "X-ND-Token: wrong"),
                    ("Host: evil.example",),
                    (f"Host: 127.0.0.1:{deck.port}", f"X-ND-Token: {deck.token}", "Origin: https://evil.example")):
        s = _socket.create_connection(("127.0.0.1", deck.port), timeout=5)
        try:
            head = "\r\n".join(("POST /api/open HTTP/1.1", *headers, "Content-Type: application/json",
                                f"Content-Length: {len(body)}", "", "")).encode("ascii")
            s.sendall(head)
            time.sleep(0.05)  # the body follows in a later segment
            s.sendall(body)
            answer = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                answer += chunk
        finally:
            s.close()
        assert answer.split(b"\r\n", 1)[0] in (b"HTTP/1.0 401 Unauthorized", b"HTTP/1.0 403 Forbidden"), answer[:80]
