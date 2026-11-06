"""deck/server.py (ephemeral port, real HTTP) and the local/youtube providers."""
from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import pytest

from needledropper.config import Paths
from needledropper.deck.server import Deck, parse_range
from needledropper.models import Drop
from needledropper.providers.base import PlayHandle, ProviderError
from needledropper.providers.local import LocalProvider
from needledropper.providers.youtube import YouTubeProvider


@pytest.fixture
def deck():
    d = Deck(port=0, heartbeat_sec=0.2).start()
    try:
        yield d
    finally:
        d.stop()


def get(deck: Deck, path: str, headers: Optional[dict] = None):
    conn = http.client.HTTPConnection("127.0.0.1", deck.port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def post_status(deck: Deck, data: dict, origin: Optional[str] = None, token: Optional[str] = "deck") -> int:
    """POST /status as the player page does (with the page's X-ND-Token)."""
    conn = http.client.HTTPConnection("127.0.0.1", deck.port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-ND-Token"] = deck.token if token == "deck" else token
    if origin:
        headers["Origin"] = origin
    conn.request("POST", "/status", body=json.dumps(data), headers=headers)
    code = conn.getresponse().status
    conn.close()
    return code


class SSEClient:
    """Minimal EventSource for tests (a player page unless ``client`` says otherwise)."""

    def __init__(self, deck: Deck, client: Optional[str] = None) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", deck.port, timeout=5)
        self.conn.request("GET", "/events" + (f"?client={client}" if client else ""))
        self.resp = self.conn.getresponse()
        assert self.resp.status == 200
        assert self.resp.getheader("Content-Type").startswith("text/event-stream")
        self.comments = 0

    def next_data(self, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.resp.fp.readline().decode("utf-8")
            if not line:
                raise EOFError("stream closed")
            if line.startswith(":"):
                self.comments += 1
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise TimeoutError

    def close(self) -> None:
        try:
            self.resp.close()
        finally:
            self.conn.close()


def wait_for(pred, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


# ------------------------------------------------------------------ deck --

def test_serves_panel_and_player_pages(deck):
    assert deck.port != 0 and deck.url == f"http://127.0.0.1:{deck.port}/"
    assert deck.player_url == f"http://127.0.0.1:{deck.port}/player"
    resp, body = get(deck, "/")
    assert resp.status == 200 and resp.getheader("Content-Type").startswith("text/html")
    html = body.decode("utf-8")
    assert "/static/panel.js" in html and "Music services" in html
    resp, body = get(deck, "/player")
    html = body.decode("utf-8")
    assert resp.status == 200 and "Click to enable sound" in html and "/static/player.js" in html
    for path, ctype in (("/static/panel.js", "text/javascript"), ("/static/panel.css", "text/css"),
                        ("/static/player.js", "text/javascript"), ("/static/player.css", "text/css"),
                        ("/static/favicon.svg", "image/svg+xml")):
        r, b = get(deck, path)
        assert r.status == 200 and r.getheader("Content-Type").startswith(ctype) and b
    js = get(deck, "/static/player.js")[1].decode("utf-8")
    assert "https://www.youtube.com/iframe_api" in js and "origin: location.origin" in js
    assert "EventSource" in js and "/events?client=player" in js and "/status" in js and "X-ND-Token" in js
    for bad in ("/static/../server.py", "/static/..%2Fserver.py", "/static/panel.html", "/nope", "/media/unknown",
                "/deck.js", "/index.htm"):
        assert get(deck, bad)[0].status == 404, bad


def test_rejects_foreign_host_and_origin(deck):
    assert get(deck, "/", {"Host": "evil.example:8322"})[0].status == 403
    assert get(deck, "/", {"Host": f"127.0.0.1:{deck.port + 1}"})[0].status == 403  # right name, wrong port
    assert get(deck, "/", {"Host": f"localhost:{deck.port}"})[0].status == 200
    assert post_status(deck, {"state": "ready"}, origin="https://evil.example") == 403
    assert post_status(deck, {"state": "ready"}, origin=f"http://127.0.0.1:{deck.port}") == 204
    assert post_status(deck, {"state": "ready"}, token=None) == 401  # the player page sends its token
    assert post_status(deck, {"state": "ready"}, token="wrong") == 401


def test_sse_receives_commands_and_heartbeats(deck):
    assert not deck.connected
    c = SSEClient(deck)
    try:
        assert wait_for(lambda: deck.client_count == 1)
        assert deck.connected
        assert deck.send({"cmd": "show", "label": "Elwynn Forest", "title": "T", "artist": "A"}) == 1
        msg = c.next_data()
        assert msg == {"cmd": "show", "label": "Elwynn Forest", "title": "T", "artist": "A"}
        time.sleep(0.5)  # heartbeat every 0.2 s
        deck.send({"cmd": "hide"})
        assert c.next_data() == {"cmd": "hide"}
        assert c.comments >= 2
    finally:
        c.close()
    assert wait_for(lambda: deck.client_count == 0)
    assert deck.connected and not deck.page_open  # grace window bridges a page reload
    deck.connected_window_sec = 0.1
    assert wait_for(lambda: not deck.connected)


def test_reconnecting_page_gets_current_card_and_playback(deck):
    deck.send({"cmd": "show", "dropId": "zone:1", "label": "L", "title": "T", "artist": "A"})
    deck.send({"cmd": "play", "playId": "p1", "source": "local", "url": "u", "startMs": 1000, "fadeInMs": 2000})
    c = SSEClient(deck)
    try:
        first = c.next_data()
        second = c.next_data()
        assert first["cmd"] == "show" and first["replay"] is True
        assert second["cmd"] == "play" and second["startMs"] >= 1000 and second["fadeInMs"] == 500
    finally:
        c.close()
    deck.send({"cmd": "stop", "playId": "p1"})
    deck.send({"cmd": "hide"})
    c2 = SSEClient(deck)
    try:
        deck.send({"cmd": "fade", "volume": 10})
        assert c2.next_data()["cmd"] == "fade"  # nothing replayed
    finally:
        c2.close()


def test_post_status_and_wait_status(deck):
    assert deck.last_status is None
    assert post_status(deck, {"state": "ready", "detail": "sound enabled"}) == 204
    assert deck.last_status["state"] == "ready"

    def later():
        time.sleep(0.1)
        post_status(deck, {"state": "playing", "playId": "abc", "detail": "local"})

    t = threading.Thread(target=later)
    t.start()
    st = deck.wait_status(play_id="abc", states={"playing", "error"}, timeout=3)
    t.join()
    assert st is not None and st["state"] == "playing" and st["detail"] == "local"
    assert deck.status_for("abc")["state"] == "playing"
    assert deck.wait_status(play_id="zzz", timeout=0.05) is None
    conn = http.client.HTTPConnection("127.0.0.1", deck.port, timeout=5)
    conn.request("POST", "/status", body=b"not json", headers={"Content-Type": "application/json",
                                                                "X-ND-Token": deck.token})
    assert conn.getresponse().status == 400
    conn.close()


def test_media_range_requests(deck, tmp_path):
    f = tmp_path / "song.mp3"
    data = bytes(range(256)) * 40  # 10240 bytes
    f.write_bytes(data)
    url = deck.register_media(f)
    assert url.startswith(deck.base_url + "/media/")
    assert deck.register_media(f) == url  # stable token
    path = url[len(deck.base_url):]
    r, b = get(deck, path)
    assert r.status == 200 and b == data and r.getheader("Accept-Ranges") == "bytes"
    assert r.getheader("Content-Type") == "audio/mpeg"
    r, b = get(deck, path, {"Range": "bytes=2-5"})
    assert r.status == 206 and b == data[2:6] and r.getheader("Content-Range") == f"bytes 2-5/{len(data)}"
    r, b = get(deck, path, {"Range": "bytes=10000-"})
    assert r.status == 206 and b == data[10000:]
    r, b = get(deck, path, {"Range": "bytes=-16"})
    assert r.status == 206 and b == data[-16:]
    r, b = get(deck, path, {"Range": "bytes=999999-"})
    assert r.status == 416 and r.getheader("Content-Range") == f"bytes */{len(data)}"
    with pytest.raises(FileNotFoundError):
        deck.register_media(tmp_path / "missing.mp3")
    # urllib (a different client) also works
    with urllib.request.urlopen(url, timeout=5) as resp:
        assert resp.read() == data


def test_parse_range():
    assert parse_range(None, 100) is None
    assert parse_range("bytes=0-9", 100) == (0, 9)
    assert parse_range("bytes=90-200", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=100-", 100) == "unsatisfiable"
    assert parse_range("bytes=5-1", 100) is None
    assert parse_range("items=0-1", 100) is None
    assert parse_range("bytes=0-1,5-6", 100) == (0, 1)


def test_stop_unblocks_sse_clients():
    d = Deck(port=0, heartbeat_sec=10).start()
    c = SSEClient(d)
    assert wait_for(lambda: d.client_count == 1)
    t0 = time.monotonic()
    d.stop()
    assert time.monotonic() - t0 < 3
    c.close()


def test_port_in_use_raises():
    a = Deck(port=0).start()
    try:
        with pytest.raises(OSError):
            Deck(port=a.port).start()
    finally:
        a.stop()


# ------------------------------------------------------------- providers --

class FakeDeck:
    """Deck stand-in that answers play commands with a scripted status."""

    def __init__(self, reply: Optional[dict] = None, connected: bool = True) -> None:
        self.connected = connected
        self.reply = reply
        self.sent: list[dict] = []
        self.statuses: dict[str, dict] = {}
        self.media: list[Path] = []
        self.url = "http://127.0.0.1:8322/"

    def send(self, cmd: dict) -> int:
        self.sent.append(cmd)
        if cmd.get("cmd") == "play" and self.reply is not None:
            self.statuses[cmd["playId"]] = {**self.reply, "playId": cmd["playId"]}
        return 1

    def wait_status(self, *, play_id=None, states=None, timeout=5.0, after_seq=None):
        st = self.statuses.get(play_id)
        if st and (states is None or st["state"] in states):
            return st
        return None

    def status_for(self, play_id):
        return self.statuses.get(play_id)

    def register_media(self, path) -> str:
        self.media.append(Path(path))
        return "http://127.0.0.1:8322/media/tok"


def make_paths(tmp_path: Path) -> Paths:
    fl = tmp_path / "WoW" / "_forever_"
    ad = fl / "Interface" / "AddOns"
    app = tmp_path / "app"
    return Paths(wow_root=tmp_path / "WoW", flavor_root=fl, addons_dir=ad, addon_dir=ad / "NeedleDropper",
                 local_addon_dir=ad / "NeedleDropper_Local", chat_log=fl / "Logs" / "WoWChatLog.txt",
                 game_exe=fl / "Wow.exe", app_dir=app, cache_dir=app / "cache", maps_dir=app / "maps")


def drop(**kw) -> Drop:
    base = dict(n=1, id="zone:1429", kind="zone", title="T", artist="A", label="Elwynn Forest")
    base.update(kw)
    return Drop(**base)


def test_local_provider_plays_through_deck(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "elwynn.mp3").write_bytes(b"ID3")
    deck = FakeDeck(reply={"state": "playing"})
    p = LocalProvider({"mediaDir": str(media)}, make_paths(tmp_path), deck)
    d = drop(file="elwynn.mp3", startMs=1500, volume=70)
    assert p.available() and p.can_play(d)
    assert not p.can_play(drop(file="missing.mp3")) and not p.can_play(drop())
    h = p.play(d, fade_in_ms=2000)
    play = deck.sent[-1]
    assert play["cmd"] == "play" and play["source"] == "local" and play["url"].endswith("/media/tok")
    assert (play["startMs"], play["fadeInMs"], play["volume"]) == (1500, 2000, 70)
    assert deck.media == [media / "elwynn.mp3"]
    assert h.provider == "local" and h.now_playing.hint is None
    assert p.poll(h) == "playing"
    deck.statuses[h.data["playId"]]["state"] = "ended"
    assert p.poll(h) == "ended"
    p.stop(h, fade_out_ms=3000)
    assert deck.sent[-1] == {"cmd": "stop", "playId": h.data["playId"], "fadeOutMs": 3000}


def test_local_provider_default_media_dir_and_local_addon_fallback(tmp_path):
    paths = make_paths(tmp_path)
    paths.local_media_dir.mkdir(parents=True)
    (paths.local_media_dir / "x.ogg").write_bytes(b"OggS")
    p = LocalProvider({}, paths, FakeDeck(reply={"state": "playing"}))
    # default mediaDir follows NEEDLEDROPPER_HOME (never the real %APPDATA%)
    from needledropper.config import app_dir
    assert p.media_dir == app_dir() / "media"
    assert p.resolve_file(drop(file="x.ogg")) == paths.local_media_dir / "x.ogg"
    absf = tmp_path / "abs.mp3"
    absf.write_bytes(b"x")
    assert p.resolve_file(drop(file=str(absf))) == absf


def test_local_provider_errors(tmp_path):
    media = tmp_path / "m"
    media.mkdir()
    (media / "a.mp3").write_bytes(b"x")
    d = drop(file="a.mp3")
    off = LocalProvider({"mediaDir": str(media)}, make_paths(tmp_path), FakeDeck(connected=False))
    assert not off.available()
    with pytest.raises(ProviderError, match="player window is not open"):
        off.play(d)
    bad = LocalProvider({"mediaDir": str(media)}, make_paths(tmp_path),
                        FakeDeck(reply={"state": "error", "detail": "local: decode error"}))
    with pytest.raises(ProviderError, match="decode error"):
        bad.play(d)
    blocked = LocalProvider({"mediaDir": str(media)}, make_paths(tmp_path),
                            FakeDeck(reply={"state": "autoplay-blocked"}))
    h = blocked.play(d)
    assert "enable sound" in h.now_playing.hint


def test_youtube_provider(tmp_path):
    deck = FakeDeck(reply={"state": "playing"})
    p = YouTubeProvider({"enabled": True}, make_paths(tmp_path), deck)
    d = drop(youtube="dQw4w9WgXcQ", startMs=61000)
    assert p.available() and p.can_play(d) and not p.can_play(drop())
    h = p.play(d, fade_in_ms=1000)
    cmd = deck.sent[-1]
    assert cmd["source"] == "youtube" and cmd["videoId"] == "dQw4w9WgXcQ" and cmd["startMs"] == 61000
    assert h.now_playing.attribution == "YouTube"
    assert h.now_playing.link == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=61s"
    deck.statuses[h.data["playId"]] = {"state": "error", "code": 150}
    assert p.poll(h) == "error"
    p.stop(h, fade_out_ms=500)
    assert deck.sent[-1]["cmd"] == "stop"
    with pytest.raises(ProviderError, match="150"):
        YouTubeProvider({}, None, FakeDeck(reply={"state": "error", "code": 150})).play(d)
    assert not YouTubeProvider({"enabled": False}, None, deck).can_play(d)
    assert not YouTubeProvider({}, None, FakeDeck(connected=False)).available()


def test_local_provider_end_to_end_with_real_deck(tmp_path):
    """LocalProvider + real Deck + a fake page answering over HTTP."""
    media = tmp_path / "m"
    media.mkdir()
    (media / "a.mp3").write_bytes(b"\xff\xfb" + b"\x00" * 1000)
    d = Deck(port=0, heartbeat_sec=0.2).start()
    page = SSEClient(d)
    try:
        assert wait_for(lambda: d.connected)
        prov = LocalProvider({"mediaDir": str(media)}, make_paths(tmp_path), d, start_timeout=5)
        result: dict = {}

        def fake_page():
            cmd = page.next_data()
            r, body = get(d, cmd["url"][len(d.base_url):], {"Range": "bytes=0-1"})
            result["range"] = (r.status, body)
            post_status(d, {"state": "playing", "playId": cmd["playId"], "detail": "local"})

        t = threading.Thread(target=fake_page)
        t.start()
        h = prov.play(drop(file="a.mp3"), fade_in_ms=0)
        t.join(5)
        assert result["range"] == (206, b"\xff\xfb")
        assert prov.poll(h) == "playing"
    finally:
        page.close()
        d.stop()
