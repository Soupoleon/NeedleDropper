"""In-app Spotify (v1.2.1, D6-D14 and D17): the Spotify Web Playback SDK in the player page.

* the deck: the SDK's token endpoint and its guards, the player page's CSP, the localhost player
  address, :class:`SpotifySdk` (reports, waits, replay, page connect / disconnect), Chrome first,
  the Widevine copy;
* the provider in app mode against a scripted Web API (FakeHTTP) and a simulated player page: ARM
  never plays, FIRE sends one play to the SDK device, the start check, the stuck fallback, stop and
  fade in the page, the restore of the player's own music, sign-ins without the new scopes, and the
  D17 status values;
* the window manager's foreground hand-back after Chromium's creation activation.

Nothing here opens a browser or reaches the network.
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_spotify_fakes import (DROP_TRACK, DROP_URI, PHONE, FakeClock, FakeHTTP, V11_SCOPE, make_drop,  # noqa: E402
                                player_state, save_tokens, tid, track)

from needledropper.deck import server  # noqa: E402
from needledropper.deck import window as W  # noqa: E402
from needledropper.deck.server import Deck, SpotifySdk  # noqa: E402
from needledropper.providers import spotify as sp  # noqa: E402
from needledropper.providers.base import ProviderError  # noqa: E402
from needledropper.providers.spotify import SpotifyProvider, SpotifyStuckError, legacy_status  # noqa: E402
from needledropper.providers.spotify_auth import SCOPES  # noqa: E402

STATIC = Path(server.STATIC_DIR)
SDK_DEVICE = "sdk-device-1"
PREV = track("prev", "Previous Song", ["Someone"], duration=300_000)


# ================================================================ helpers ====

def request(deck: Deck, method: str, path: str, headers: dict | None = None, body: bytes | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", deck.port, timeout=5)
    try:
        hdrs = {"Host": f"127.0.0.1:{deck.port}"}
        hdrs.update(headers or {})
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, dict((k.lower(), v) for k, v in resp.getheaders()), resp.read()
    finally:
        conn.close()


def post_status(deck: Deck, payload: dict) -> int:
    body = json.dumps(payload).encode("utf-8")
    # v1.2.1 review RCOMP-3: the SDK's reports also carry the player window's launch key
    return request(deck, "POST", "/status", {"X-ND-Token": deck.token, "X-ND-Key": deck.player_key,
                                             "Content-Type": "application/json",
                                             "Content-Length": str(len(body))}, body)[0]


@pytest.fixture
def deck():
    d = Deck(port=0, heartbeat_sec=0.2, launcher=lambda a: None, url_opener=lambda u: False).start()
    try:
        yield d
    finally:
        d.stop()


class PageDeck:
    """The parts of the Deck that :class:`SpotifySdk` and the provider use, with a simulated player
    page: ``sent`` collects the commands; ``on_command`` lets a test answer them like the page."""

    def __init__(self, *, ready: bool = True, browser: str = "chrome") -> None:
        self.sent: list[dict] = []
        self._interrupt = threading.Event()
        self._stopping = threading.Event()
        self.page_open = True
        self.player_opening = False
        self.page_browser = browser
        self.auto_open = "youtube"
        self.chrome = True
        self.on_command = None
        self.spotify = SpotifySdk(self)
        self.auto_stop = True  # answer "stop" with "stopped", as player.js does after its fade
        self._ready = ready
        self.generation = 1
        self.created = False
        self.registered: set[str] = set()

    def reload(self):
        self.spotify.page_gone()
        self.generation += 1
        self.created = False
        self.registered.clear()
        self.spotify.page_connected()
        for cmd in self.spotify.replay():
            self.send(cmd)

    def send(self, cmd: dict) -> int:
        self.sent.append(dict(cmd))
        if cmd.get("cmd") == "spotify":
            op = cmd.get("op")
            if op in ("reload", "disconnect"):
                self.reload()
            elif op in ("connect", "reconnect") and self._ready:
                device = "sdk-device-" + str(self.generation)
                if self.created and not cmd.get("replay"):
                    device += "-unlisted"  # Spotify only registers the first Player in this page
                else:
                    self.registered.add(device)
                self.created = True
                self.report("ready", deviceId=device)
            if op == "stop" and self.auto_stop:
                self.report("stopped", playId=cmd.get("playId"))
            if self.on_command is not None:
                self.on_command(cmd)
        return 1 if self.page_open else 0

    def chrome_available(self) -> bool:
        return self.chrome

    def report(self, event: str, **data) -> None:
        self.spotify.report({"state": "spotify", "event": event, **data})

    def ops(self) -> list[str]:
        return [c["op"] for c in self.sent if c.get("cmd") == "spotify"]

    def tick(self, position: int, *, uri: str = DROP_URI, paused: bool = False, loading: bool = False,
             event: str = "tick") -> None:
        self.report(event, active=True, paused=paused, loading=loading, position=position, duration=245_000,
                    uri=uri, id=uri.rsplit(":", 1)[-1], linked="")


@pytest.fixture
def fast(monkeypatch):
    """Short in-app waits (the logic is the same; the tests stay quick)."""
    monkeypatch.setattr(sp, "APP_READY_WAIT_S", 0.3)
    monkeypatch.setattr(sp, "APP_REGISTRATION_WAIT_S", 0.3)
    monkeypatch.setattr(sp, "APP_START_TIMEOUT_S", 0.5)
    monkeypatch.setattr(sp, "APP_API_CHECK_S", 0.2)
    monkeypatch.setattr(sp, "APP_STOP_SLACK_S", 0.3)


def make_app_rig(tmp_path: Path, *, page: PageDeck | None = None, cfg: dict | None = None, tokens: bool = True,
                 scope: str | None = None, http: FakeHTTP | None = None):
    if tokens:
        if scope is None:
            save_tokens(tmp_path)
        else:
            save_tokens(tmp_path, scope=scope)
    page = page or PageDeck()
    fake = http or FakeHTTP()
    if ("GET", "/v1/me/player/devices") not in fake.routes:
        fake.add("GET", "/v1/me/player/devices", lambda call: (200, {"devices": [
            {"id": dev, "name": "NeedleDropper", "type": "Computer"} for dev in page.registered]}))
    clock = FakeClock()
    conf = {"clientId": "cid", "device": "app", "fade": True, "restorePrevious": True}
    conf.update(cfg or {})
    provider = SpotifyProvider(conf, SimpleNamespace(app_dir=tmp_path), http=fake, sleep=clock.sleep, clock=clock,
                               deck=page)
    if tokens:
        provider._wait_registered(0)
    return SimpleNamespace(provider=provider, page=page, http=fake, clock=clock)


def plays_and_ticks(rig, *, start_offset: int = 400):
    """The Web API play makes the page's SDK play: a tick a little past the start."""
    def on_play(call):
        rig.page.tick(int(call.json["position_ms"]) + start_offset)
        return (204, None)
    rig.http.add("PUT", "/v1/me/player/play", on_play)


# ================================================================= deck =====

def test_the_player_opens_on_localhost_and_everything_else_stays_on_127(deck):
    assert deck.player_url == f"http://localhost:{deck.port}/player"
    assert deck.base_url == f"http://127.0.0.1:{deck.port}" and deck.url.startswith("http://127.0.0.1:")
    assert deck.origin_allowed(f"http://localhost:{deck.port}") and deck.origin_allowed(f"http://127.0.0.1:{deck.port}")
    assert not deck.origin_allowed("http://localhost:1") and not deck.origin_allowed("https://evil.example")
    st, hdrs, body = request(deck, "GET", "/player", {"Host": f"localhost:{deck.port}"})
    assert st == 200 and deck.token.encode() in body
    # the page's reports from that origin (D12: the X-ND-Token flow works there too)
    payload = json.dumps({"state": "ready", "browser": "chrome"}).encode()
    local = {"Host": f"localhost:{deck.port}", "Origin": f"http://localhost:{deck.port}",
             "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json", "Content-Length": str(len(payload))}
    assert request(deck, "POST", "/status", {**local, "X-ND-Token": deck.token, "X-ND-Key": deck.player_key},
                   payload)[0] == 204  # v1.2.1 polish: every player report carries the launch key
    assert request(deck, "POST", "/status", {**local, "X-ND-Token": "nope"}, payload)[0] == 401
    assert request(deck, "POST", "/status", {**local, "Origin": "http://localhost:1", "X-ND-Token": deck.token},
                   payload)[0] == 403


def test_the_player_csp_allows_the_sdk_and_nothing_wider(deck):
    st, hdrs, _ = request(deck, "GET", "/player")
    csp = {d.split(" ", 1)[0]: d.split(" ", 1)[1] if " " in d else "" for d in
           (x.strip() for x in hdrs["content-security-policy"].split(";")) if d}
    assert "https://sdk.scdn.co" in csp["script-src"] and "https://www.youtube.com" in csp["script-src"]
    assert "https://sdk.scdn.co" in csp["frame-src"] and "https://www.youtube.com" in csp["frame-src"]
    assert "wss://*.spotify.com" in csp["connect-src"] and "https://*.spotify.com" in csp["connect-src"]
    assert "blob:" in csp["media-src"] and "'self'" in csp["media-src"]
    assert "'unsafe-inline'" not in hdrs["content-security-policy"] and "*" not in csp["script-src"]
    assert csp["frame-ancestors"] == "'none'" and csp["object-src"] == "'none'"
    # the Connect page loads nothing from Spotify
    _, chdrs, _ = request(deck, "GET", "/")
    assert "scdn" not in chdrs["content-security-policy"] and "spotify" not in chdrs["content-security-policy"]


def test_the_token_endpoint_serves_the_player_page_only(deck):
    path = server.SPOTIFY_TOKEN_PATH
    # v1.2.1 review RCOMP-3: and the launch key, which only the window the app opened has (its address)
    same = {"Sec-Fetch-Site": "same-origin", "X-ND-Token": deck.token, "X-ND-Key": deck.player_key}
    # in-app Spotify off: nothing to hand out
    assert request(deck, "GET", path, same)[0] == 404
    deck.spotify.enable(lambda: "ACCESS-123")
    st, hdrs, body = request(deck, "GET", path, same)
    assert st == 200 and json.loads(body) == {"accessToken": "ACCESS-123"}
    assert hdrs["cache-control"] == "no-store" and hdrs["content-type"].startswith("application/json")
    # from the localhost page too (D12), with its Origin
    st, _, _ = request(deck, "GET", path, {**same, "Host": f"localhost:{deck.port}",
                                           "Origin": f"http://localhost:{deck.port}"})
    assert st == 200
    for bad in ({"X-ND-Token": deck.token},                                        # no Sec-Fetch-Site: not a page
                {"Sec-Fetch-Site": "cross-site", "X-ND-Token": deck.token},        # another site
                {"Sec-Fetch-Site": "none", "X-ND-Token": deck.token},              # typed into the address bar
                {**same, "Origin": "https://evil.example"}):                        # a foreign page
        assert request(deck, "GET", path, bad)[0] == 403, bad
    assert request(deck, "GET", path, {"Sec-Fetch-Site": "same-origin"})[0] == 401
    assert request(deck, "GET", path, {"Sec-Fetch-Site": "same-origin", "X-ND-Token": "wrong"})[0] == 401
    # the X-ND-Token alone (anyone can read it from GET /player) is not enough (review RCOMP-3)
    assert request(deck, "GET", path, {"Sec-Fetch-Site": "same-origin", "X-ND-Token": deck.token})[0] == 401
    assert request(deck, "GET", path, {**same, "X-ND-Key": "wrong"})[0] == 401
    assert request(deck, "GET", path, {**same, "Host": "evil.example"})[0] == 403  # DNS rebinding
    assert request(deck, "HEAD", path, same)[0] == 405


def test_the_token_endpoint_never_leaks_a_refresh_token_or_a_sign_in_error(tmp_path, deck):
    save_tokens(tmp_path)  # access AT1, refresh RT1
    prov = SpotifyProvider({"clientId": "cid", "device": "app"}, SimpleNamespace(app_dir=tmp_path), http=FakeHTTP(),
                           deck=deck)
    try:
        page = {"Sec-Fetch-Site": "same-origin", "X-ND-Token": deck.token, "X-ND-Key": deck.player_key}  # RCOMP-3
        st, _, body = request(deck, "GET", server.SPOTIFY_TOKEN_PATH, page)
        assert st == 200 and json.loads(body) == {"accessToken": "AT1"} and b"RT1" not in body
        deck.spotify.enable(lambda: (_ for _ in ()).throw(RuntimeError("refresh failed: RT1 rejected")))
        st, _, body = request(deck, "GET", server.SPOTIFY_TOKEN_PATH, page)
        assert st == 503 and b"RT1" not in body
    finally:
        prov.close()


def test_sdk_reports_reach_the_sdk_state_and_never_the_youtube_status(deck):
    heard: list = []
    deck.on_player_status = lambda *a: heard.append(a)
    events: list = []
    deck.spotify.enable(lambda: "T", listener=lambda ev, rec: events.append(ev))
    assert post_status(deck, {"state": "spotify", "event": "ready", "deviceId": "dev-A"}) == 204
    assert deck.spotify.wait_ready(0) is None  # an SDK ready alone is not routable
    deck.spotify.verify_device("dev-A", deck.spotify.ready_seq, listed=True)
    assert deck.spotify.wait_ready(0) == "dev-A"
    post_status(deck, {"state": "spotify", "event": "tick", "active": True, "paused": False, "position": 5000,
                       "uri": DROP_URI, "playId": "p1"})
    snap = deck.spotify.snapshot()
    assert snap["tick"]["position"] == 5000 and snap["ready"] and "T" not in json.dumps(snap)
    post_status(deck, {"state": "spotify", "event": "not_ready", "deviceId": "dev-A"})
    assert deck.spotify.wait_ready(0) is None
    post_status(deck, {"state": "spotify", "event": "ready", "deviceId": "dev-B"})  # the device id can change
    deck.spotify.verify_device("dev-B", deck.spotify.ready_seq, listed=True)
    assert deck.spotify.wait_ready(0) == "dev-B"
    post_status(deck, {"state": "spotify", "event": "error", "kind": "account_error", "message": "Premium"})
    assert deck.spotify.snapshot()["error"]["kind"] == "account_error"
    post_status(deck, {"state": "csp", "blocked": "https://x.example/a.js", "directive": "script-src"})
    assert deck.csp_violations == [("script-src", "https://x.example/a.js")]
    assert heard == [] and deck.last_status is None  # YouTube / local waits never see SDK reports
    assert events == ["ready", "tick", "not_ready", "ready", "error"]


def test_wait_for_resumes_after_the_reports_it_has_seen():
    page = PageDeck()
    sdk = page.spotify
    sdk.enable(lambda: "T")
    page.tick(100)
    cursor = {"seq": 0}
    assert sdk.wait_for(lambda r: r.get("event") == "nothing", timeout=0, cursor=cursor) is None
    assert cursor["seq"] == sdk.seq                       # everything up to now was looked at
    page.tick(900)
    rec = sdk.wait_for(lambda r: r.get("event") == "tick", after_seq=cursor["seq"], timeout=0)
    assert rec["position"] == 900
    t0 = time.monotonic()
    threading.Timer(0.1, lambda: page.report("stopped", playId="p9")).start()
    assert sdk.wait_event("stopped", play_id="p9", after_seq=sdk.seq, timeout=2)["playId"] == "p9"
    assert time.monotonic() - t0 < 1.5


def test_a_connecting_player_page_is_told_to_connect_and_starts_unready(deck):
    from test_core_deck_providers import SSEClient

    deck.spotify.enable(lambda: "T")
    post_status(deck, {"state": "spotify", "event": "ready", "deviceId": "old-page"})
    page = SSEClient(deck)
    try:
        first = page.next_data()
        assert first["cmd"] == "spotify" and first["op"] == "connect" and first["name"] == "NeedleDropper"
        assert first.get("replay") is True
        assert deck.spotify.wait_ready(0) is None   # the new page's SDK is not ready yet (new device id)
        post_status(deck, {"state": "spotify", "event": "ready", "deviceId": "new-page"})
        deck.spotify.verify_device("new-page", deck.spotify.ready_seq, listed=True)
        assert deck.spotify.wait_ready(0) == "new-page"
    finally:
        page.close()
    deadline = time.monotonic() + 3
    while deck.page_open and time.monotonic() < deadline:
        time.sleep(0.02)
    assert deck.spotify.wait_ready(0) is None       # no page: nothing can play


def test_the_player_opens_in_chrome_first_while_spotify_plays_in_the_app(deck, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": f"C:\\Fake\\{kind}.exe")
    copied: list = []
    deck.widevine_copier = copied.append
    launched: list = []
    deck.launcher = lambda a: launched.append(a)
    deck.configure_player(browser="edge", profile_dir=tmp_path / "deck-browser")
    assert deck._browser_order() == ["edge", "chrome"]
    deck.spotify.standby(owner="provider")          # app mode, not signed in yet: Chrome already
    assert deck._browser_order() == ["chrome", "edge"]
    assert deck.open_player() == "chrome" and launched[-1][0].endswith("chrome.exe")
    assert copied == [tmp_path / "deck-browser"]     # Chrome's Widevine copied into the player profile
    # with the launch key in the address fragment (v1.2.1 review ON-1 / RCOMP-3)
    assert any(a == f"--app=http://localhost:{deck.port}/player#k={deck.player_key}" for a in launched[-1])
    for flag in server.NO_THROTTLE_FLAGS:
        assert flag in launched[-1]
    deck.spotify.disable(owner="someone else")       # only the owner switches app mode off
    assert deck.spotify.app_mode
    deck.spotify.disable(owner="provider")
    assert deck._browser_order() == ["edge", "chrome"]
    deck.open_player()
    assert launched[-1][0].endswith("edge.exe") and len(copied) == 1  # no Widevine copy for Edge


def test_widevine_is_copied_once_into_the_player_profile(tmp_path):
    src = tmp_path / "chrome" / "WidevineCdm"
    (src / "4.10.2830.0" / "_platform_specific").mkdir(parents=True)
    (src / "4.10.2830.0" / "manifest.json").write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile"
    assert server.ensure_widevine(profile, src)
    assert (profile / "WidevineCdm" / "4.10.2830.0" / "manifest.json").is_file()
    (src / "4.10.2830.0" / "manifest.json").write_text('{"changed": true}', encoding="utf-8")
    assert server.ensure_widevine(profile, src)       # already there: left alone
    assert (profile / "WidevineCdm" / "4.10.2830.0" / "manifest.json").read_text(encoding="utf-8") == "{}"
    assert not server.ensure_widevine(tmp_path / "p2", tmp_path / "missing")
    assert not (tmp_path / "p2" / "WidevineCdm").exists()


def test_the_player_page_hosts_the_sdk_without_ever_focusing():
    js = (STATIC / "player.js").read_text(encoding="utf-8")
    assert "https://sdk.scdn.co/spotify-player.js" in js and "new window.Spotify.Player(" in js
    assert "getOAuthToken: spToken" in js and '"/api/player/spotify-token"' in js and '"X-ND-Token": TOKEN' in js
    assert 'case "spotify": spotifyCmd(cmd)' in js and "com.widevine.alpha" in js
    for event in ('"ready"', '"not_ready"', '"initialization_error"', '"authentication_error"', '"account_error"',
                  '"playback_error"', '"player_state_changed"'):
        assert event in js, event
    assert 'spPost("ready", { deviceId: sp.deviceId, again: true })' in js  # a replayed connect re-reports
    assert "getCurrentState" in js and "setVolume" in js and ".pause()" in js
    assert "window.focus" not in js and ".focus()" not in js
    assert 'browserKind() !== "chrome"' in js           # D11: no SDK outside Chrome
    assert "spotify" in server.PLAYER_ONLY_CMDS


# ============================================================ provider ======

def test_arm_never_plays_and_reads_the_players_music_once(tmp_path):
    rig = make_app_rig(tmp_path)
    rig.http.add("GET", "/v1/me/player", (200, player_state(PREV, 30_000, device=PHONE)))
    assert rig.page.ops() == ["connect"]                 # a sign-in that allows it: the SDK connects
    rig.provider.prepare(make_drop())
    assert rig.http.find("PUT", "/v1/me/player/play") == [] and rig.http.puts() == []
    assert len(rig.http.find("GET", "/v1/me/player")) == 1
    assert rig.provider.check_health() is True           # the SDK is ready (never the desktop signature)


def test_fire_sends_one_play_to_the_sdk_device_silent_then_fades_in_the_page(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    rig.http.add("GET", "/v1/me/player", (204, None))
    plays_and_ticks(rig)
    h = rig.provider.play(make_drop(startMs=30_000, volume=80), fade_in_ms=2500)
    plays = rig.http.find("PUT", "/v1/me/player/play")
    assert len(plays) == 1 and plays[0].query == {"device_id": SDK_DEVICE}
    assert plays[0].json == {"uris": [DROP_URI], "position_ms": 30_000}
    start = [c for c in rig.page.sent if c.get("op") == "start"]
    assert len(start) == 1 and start[0]["uri"] == DROP_URI and start[0]["volume"] == 80
    assert start[0]["fadeInMs"] == 2500 and start[0]["playId"] == h.data["playId"]
    assert rig.http.find("PUT", "/v1/me/player/volume") == []   # fades never go through the Web API (D7)
    assert h.data["app"] and h.data["deviceId"] == SDK_DEVICE and not rig.provider.stuck()
    assert h.now_playing.attribution == "Spotify" and rig.provider.last_start_ms is not None
    assert rig.provider.poll(h) == "playing"


def test_the_sdks_position_jitter_before_the_audio_is_not_a_start(tmp_path, fast):
    rig = make_app_rig(tmp_path, cfg={"restorePrevious": False})
    rig.http.add("GET", "/v1/me/player", (204, None))

    def jitter(call):                         # seen live: 30000 loading, then 30001 / 30002 / 30076 / 30002
        for pos, loading in ((30_083, True), (30_001, False), (30_002, False), (30_076, False), (30_002, False)):
            rig.page.tick(pos, loading=loading)
        return (204, None)
    rig.http.add("PUT", "/v1/me/player/play", jitter)
    with pytest.raises(SpotifyStuckError):
        rig.provider.play(make_drop(startMs=30_000), fade_in_ms=0)
    rig.provider.reset_session()
    rig.http.set("PUT", "/v1/me/player/play", lambda call: (jitter(call), rig.page.tick(30_255), (204, None))[2])
    h = rig.provider.play(make_drop(startMs=30_000), fade_in_ms=0)   # the audio really moves on: started
    assert h.data["app"] and not rig.provider.stuck()


def test_no_fade_and_fixed_volume_reach_the_page(tmp_path, fast):
    rig = make_app_rig(tmp_path, cfg={"fade": False, "volume": 50})
    plays_and_ticks(rig)
    rig.provider.play(make_drop(volume=50), fade_in_ms=2500)
    start = [c for c in rig.page.sent if c.get("op") == "start"][0]
    assert start["fadeInMs"] == 0 and start["volume"] == 25


def test_the_start_check_also_reads_the_web_api(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    ours = dict(player_state(DROP_TRACK, 1_500), device={"id": SDK_DEVICE, "name": "NeedleDropper"})
    rig.http.add("GET", "/v1/me/player", (204, None), (200, ours))   # the page sends no ticks at all
    rig.http.add("PUT", "/v1/me/player/play", (204, None))
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    assert h.data["app"] and not rig.provider.stuck()


def test_a_start_that_never_progresses_falls_back_and_parks_the_song(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    rig.http.add("GET", "/v1/me/player", (200, player_state(PREV, 10_000, device=PHONE)),
                 (200, dict(player_state(DROP_TRACK, 0, playing=False), device={"id": SDK_DEVICE})))
    rig.http.add("PUT", "/v1/me/player/play", (204, None))
    with pytest.raises(SpotifyStuckError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    exc = ei.value
    assert exc.notice == sp.NOTICE_STUCK and exc.cleanup is not None and exc.cleanup.data["parked"]
    assert rig.page.ops()[-1] == "pause"                  # silenced in the page at once
    assert rig.provider.stuck() and rig.provider.music_state() == "stuck"
    assert legacy_status(rig.provider.music_state()) == "error"
    assert rig.provider.stuck_notice()[0] == "Spotify didn't start"
    # the end of the drop: the player's own music (on the phone) resumes there, never in the SDK
    rig.provider.stop(exc.cleanup)
    restore = rig.http.find("PUT", "/v1/me/player/play")[-1]
    assert restore.query == {"device_id": PHONE["id"]} and restore.json["uris"] == [PREV["uri"]]
    # A later ready clears the verdict only after the Web API confirms its registration.
    rig.page.report("ready", deviceId=SDK_DEVICE)
    rig.provider._wait_registered(0)
    assert not rig.provider.stuck()


def test_an_sdk_error_during_the_start_fails_at_once(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    rig.http.add("GET", "/v1/me/player", (204, None))
    rig.http.add("PUT", "/v1/me/player/play",
                 lambda call: (rig.page.report("error", kind="playback_error", message="Cannot play"), (204, None))[1])
    t0 = time.monotonic()
    with pytest.raises(SpotifyStuckError, match="playback_error"):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert time.monotonic() - t0 < 0.45


def test_a_second_404_fails_after_one_reload_without_pausing_an_empty_player(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    rig.http.add("GET", "/v1/me/player", (204, None))
    rig.http.add("PUT", "/v1/me/player/play", (404, {"error": {"status": 404, "message": "Device not found"}}))
    with pytest.raises(ProviderError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert not isinstance(ei.value, SpotifyStuckError)
    assert rig.page.ops()[-4:] == ["start", "reload", "connect", "start"]
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 2


def test_a_player_that_is_not_ready_sends_no_play(tmp_path, fast):
    rig = make_app_rig(tmp_path, page=PageDeck(ready=False))
    rig.http.add("GET", "/v1/me/player", (204, None))
    assert rig.provider.available()            # still connecting: the engine may try it
    with pytest.raises(ProviderError, match="isn't ready"):
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert rig.http.find("PUT", "/v1/me/player/play") == []


def test_stop_fades_out_and_pauses_in_the_page_then_restores_the_previous_device(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    ctx = {"type": "playlist", "uri": "spotify:playlist:" + tid("pl")}
    rig.http.add("GET", "/v1/me/player", (200, player_state(PREV, 42_000, device=PHONE, context=ctx)))
    plays_and_ticks(rig)
    rig.provider.prepare(make_drop())                       # ARM: reads the phone's music
    h = rig.provider.play(make_drop(startMs=10_000, durationMs=60_000), fade_in_ms=0)
    rig.provider.stop(h, fade_out_ms=3000)
    stop = [c for c in rig.page.sent if c.get("op") == "stop"]
    assert len(stop) == 1 and stop[0]["playId"] == h.data["playId"] and stop[0]["fadeOutMs"] == 3000
    assert rig.http.find("PUT", "/v1/me/player/pause") == []   # the page confirmed: no Web API pause
    restore = rig.http.find("PUT", "/v1/me/player/play")[-1]
    assert restore.query == {"device_id": PHONE["id"]}
    assert restore.json["context_uri"] == ctx["uri"] and restore.json["offset"] == {"uri": PREV["uri"]}
    assert restore.json["position_ms"] >= 42_000


def test_stop_without_the_pages_confirmation_pauses_through_the_web_api(tmp_path, fast):
    rig = make_app_rig(tmp_path, cfg={"restorePrevious": False})
    rig.page.auto_stop = False
    plays_and_ticks(rig)
    rig.http.add("PUT", "/v1/me/player/pause", (204, None))
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    rig.provider.stop(h, fade_out_ms=0)
    pauses = rig.http.find("PUT", "/v1/me/player/pause")
    assert len(pauses) == 1 and pauses[0].query == {"device_id": SDK_DEVICE}
    assert rig.http.find("GET", "/v1/me/player") == []    # no restore read with restorePrevious off


def test_music_already_in_the_app_player_is_never_resumed_there(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    in_app = dict(player_state(PREV, 5_000), device={"id": SDK_DEVICE, "name": "NeedleDropper"})
    rig.http.add("GET", "/v1/me/player", (200, in_app))
    plays_and_ticks(rig)
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    rig.provider.stop(h)
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 1   # the drop's play only


def test_poll_follows_the_pages_ticks_and_a_playback_error(tmp_path, fast):
    rig = make_app_rig(tmp_path, cfg={"restorePrevious": False})
    plays_and_ticks(rig)
    h = rig.provider.play(make_drop(startMs=0, durationMs=30_000), fade_in_ms=0)
    rig.clock.advance(3)
    rig.page.tick(3_500)
    assert rig.provider.poll(h) == "playing" and rig.http.find("GET", "/v1/me/player") == []  # no API read
    rig.clock.advance(3)
    rig.page.tick(30_100)
    assert rig.provider.poll(h) == "ended"
    h2 = rig.provider.play(make_drop(), fade_in_ms=0)
    rig.page.report("error", kind="playback_error", message="Cannot decode")
    assert rig.provider.poll(h2) == "error"


def test_buffering_is_not_a_pause(tmp_path, fast):
    rig = make_app_rig(tmp_path, cfg={"restorePrevious": False})
    plays_and_ticks(rig)
    h = rig.provider.play(make_drop(startMs=0, durationMs=60_000), fade_in_ms=0)
    rig.clock.advance(3)
    rig.page.tick(2_000, loading=True)          # the SDK rebuffers for a moment (seen live)
    assert rig.provider.poll(h) == "playing"
    rig.clock.advance(3)
    rig.page.tick(2_100, paused=True)           # someone paused NeedleDropper's device in Spotify
    assert rig.provider.poll(h) == "ended"
    rig.provider.stop(h)
    assert [c for c in rig.page.sent if c.get("op") == "stop"] == []   # theirs now: left alone


def test_a_sign_in_from_before_1_2_1_needs_a_new_one(tmp_path):
    rig = make_app_rig(tmp_path, scope=V11_SCOPE + " user-read-private")
    p = rig.provider
    assert p.needs_relogin() and not p.available() and p.unavailable_reason() == sp.RELOGIN_MESSAGE
    assert p.music_state() == "needs-relogin" and legacy_status("needs-relogin") == "error"
    st = p.status_dict()
    assert st["relogin"] and st["badge"] == "error" and st["message"] == sp.RELOGIN_MESSAGE
    assert rig.page.ops() == [] and rig.page.spotify.app_mode and not rig.page.spotify.wanted  # standby
    save_tokens(tmp_path)                                   # signed in again with 1.2.1
    p.reset_session()
    assert rig.page.ops() == ["connect"] and p.available() and p.music_state() == "connected"
    # the clientId stays; a sign-in whose scopes the store does not record is trusted until the SDK says
    save_tokens(tmp_path, scope="")
    rig.page.report("error", kind="authentication_error", message="Invalid token scopes.")
    assert p.music_state() == "needs-relogin" and rig.page.ops()[-1] == "disconnect"


def test_sdk_errors_map_to_the_d17_states(tmp_path):
    rig = make_app_rig(tmp_path)
    p, page = rig.provider, rig.page
    assert p.music_state() == "connected" and p.status_dict()["mode"] == "app"
    page.report("error", kind="initialization_error", message="Failed to initialize player")
    assert p.music_state() == "no-player" and not p.available()
    page.report("ready", deviceId=SDK_DEVICE)                # a later ready wins over an older error
    assert p.music_state() == "connected"
    page.report("error", kind="browser", message="in-app Spotify needs Google Chrome (this is edge)")
    assert p.sdk_problem() == ("no-player", sp.NO_PLAYER_MESSAGE)   # D11: the page never loads the SDK there
    page.report("ready", deviceId=SDK_DEVICE)
    page.report("error", kind="account_error", message="Premium required")
    assert p.music_state() == "premium-required" and legacy_status("premium-required") == "premium-required"
    p.reset_session()
    page.report("ready", deviceId=SDK_DEVICE)
    assert p.music_state() == "connected"


def test_authentication_errors_get_one_fresh_token_then_are_an_error(tmp_path):
    fake = FakeHTTP().add("POST", "/api/token", (200, {"access_token": "AT2", "refresh_token": "RT2",
                                                       "expires_in": 3600, "scope": " ".join(SCOPES)}))
    rig = make_app_rig(tmp_path, http=fake)
    p, page = rig.provider, rig.page
    page._ready = False                                       # from now on the SDK refuses every token
    page.report("error", kind="authentication_error", message="Authentication failed")
    deadline = time.monotonic() + 3
    while "reload" not in page.ops() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "reload" in page.ops() and len(fake.find("POST", "/api/token")) == 1
    assert p.music_state() == "connected"                    # connecting again with the fresh token
    page.report("error", kind="authentication_error", message="Authentication failed")
    assert p.music_state() == "error" and p.status_dict()["badge"] == "error"
    assert p.status_dict()["message"] == sp.SDK_AUTH_MESSAGE
    page.report("ready", deviceId=SDK_DEVICE)                 # accepted after all (a new sign-in, say)
    assert p.music_state() == "connected"


def test_no_player_states_say_why(tmp_path, monkeypatch):
    page = PageDeck(ready=False)
    rig = make_app_rig(tmp_path, page=page)
    p = rig.provider
    assert p.music_state() == "connected"                     # connecting: give it time
    monkeypatch.setattr(sp, "SDK_READY_GRACE_S", -1.0)        # ... time is up
    # v1.2.1 review ON-9: a page in Chrome that never got ready is not "install Google Chrome"
    assert p.music_state() == "no-player" and p.sdk_problem()[1] == sp.NOT_READY_MESSAGE
    assert p.sdk_problem_detail()[2] == "not-ready" and p.status_dict()["problem"] == "not-ready"
    page.page_open = False                                     # no player window at all
    assert p.sdk_problem()[1] == sp.NO_WINDOW_MESSAGE and p.sdk_problem_detail()[2] == "no-window"
    page.chrome = False
    assert p.sdk_problem()[1] == sp.NO_PLAYER_MESSAGE         # nothing to open it in: install Chrome
    assert p.sdk_problem_detail()[2] == "no-chrome"
    page.auto_open = "never"
    assert p.sdk_problem()[1] == sp.NO_WINDOW_NEVER_MESSAGE and p.sdk_problem_detail()[2] == "window-never"
    page.page_open, page.auto_open, page.player_opening = True, "youtube", False
    page.page_browser = "edge"                                 # D11: the SDK never works in Edge
    monkeypatch.setattr(sp, "SDK_READY_GRACE_S", 30.0)
    assert p.sdk_problem() == ("no-player", sp.NO_PLAYER_MESSAGE)
    page.page_browser = "chrome"
    page.report("eme", ok=False, detail="Unsupported keySystem")
    assert p.sdk_problem() == ("no-player", sp.NO_PLAYER_MESSAGE) and "Chrome" in p.status_dict()["warning"]


def test_a_player_that_never_got_ready_is_connected_again_by_sync(tmp_path, monkeypatch):
    page = PageDeck(ready=False)
    rig = make_app_rig(tmp_path, page=page)
    monkeypatch.setattr(sp, "SDK_READY_GRACE_S", -1.0)
    assert not rig.provider.available()       # no-player: the engine never prepares it (the app's
    rig.provider.sync_sdk()                   # housekeeping calls sync_sdk: test_panel_spotify.py)
    assert page.ops() == ["connect", "reload", "connect"]
    rig.provider.sync_sdk()                   # at most once a minute
    assert page.ops() == ["connect", "reload", "connect"]
    page.page_browser = "edge"                # never for a page in Edge (it would never work)
    rig.provider._last_recheck = -1e9
    rig.provider.sync_sdk()
    assert page.ops() == ["connect", "reload", "connect"]


def test_a_short_offline_blip_is_not_a_failure(tmp_path, fast, monkeypatch):
    rig = make_app_rig(tmp_path, cfg={"restorePrevious": False})
    plays_and_ticks(rig)
    h = rig.provider.play(make_drop(durationMs=60_000), fade_in_ms=0)
    rig.page.report("not_ready", deviceId=SDK_DEVICE)
    assert rig.provider.poll(h) == "playing"             # within the grace: the SDK reconnects by itself
    rig.page.report("ready", deviceId=SDK_DEVICE)
    monkeypatch.setattr(sp, "APP_OFFLINE_GRACE_S", 0.0)
    rig.clock.advance(3)
    rig.page.tick(4_000)
    assert rig.provider.poll(h) == "playing"             # it came back with the same device: still ours
    rig.page.report("not_ready", deviceId=SDK_DEVICE)
    assert rig.provider.poll(h) == "error"               # offline past the grace: the engine falls back


def test_the_desktop_stuck_signature_never_applies_to_the_app_device(tmp_path):
    rig = make_app_rig(tmp_path)
    stuck_state = {"device": {"id": SDK_DEVICE, "name": "NeedleDropper"}, "item": None, "is_playing": False,
                   "actions": {"disallows": {"pausing": True, "resuming": True, "skipping_next": True}}}
    rig.http.add("GET", "/v1/me/player", (200, stuck_state))
    assert rig.provider.check_health() is True and not rig.provider.stuck()


def test_a_passive_instance_never_drives_the_player_and_close_only_releases_its_own(tmp_path):
    page = PageDeck()
    save_tokens(tmp_path)
    passive = SpotifyProvider({"clientId": "cid", "device": "app"}, SimpleNamespace(app_dir=tmp_path),
                              http=FakeHTTP(), deck=page, passive=True)
    assert page.ops() == [] and not page.spotify.app_mode
    active = SpotifyProvider({"clientId": "cid", "device": "app"}, SimpleNamespace(app_dir=tmp_path),
                             http=FakeHTTP(), deck=page)
    newer = SpotifyProvider({"clientId": "cid", "device": "app"}, SimpleNamespace(app_dir=tmp_path),
                            http=FakeHTTP(), deck=page)    # a rebuild: the new one takes over
    passive.close()
    active.close()                                          # not the owner any more: nothing changes
    assert page.ops() == ["connect"] and page.spotify.wanted
    newer.close()
    assert page.ops() == ["connect", "disconnect"] and not page.spotify.app_mode


def test_connect_mode_leaves_the_app_player_alone(tmp_path):
    page = PageDeck()
    save_tokens(tmp_path)
    p = SpotifyProvider({"clientId": "cid", "device": "connect"}, SimpleNamespace(app_dir=tmp_path),
                        http=FakeHTTP(), deck=page)
    assert page.ops() == [] and p.mode == "connect" and p.status_dict()["player"] is None
    assert p.stuck_notice()[0] == "Spotify isn't starting songs"


@pytest.mark.parametrize("state,legacy", [("connected", "connected"), ("not-connected", "not-connected"),
                                          ("premium-required", "premium-required"), ("needs-relogin", "error"),
                                          ("no-player", "error"), ("stuck", "error"), ("error", "error")])
def test_d17_states_keep_the_four_v1_2_values(state, legacy):
    assert state in sp.MUSIC_STATES and legacy_status(state) == legacy


# ============================================================ window ========

class ForegroundWin32:
    """A fake Win32 with a foreground window: Chromium's creation activation and the hand-back."""

    def __init__(self, game: int = 777) -> None:
        self.windows: list = []
        self.tree: dict = {}
        self.styles: dict = {}
        self.calls: list = []
        self.fg = game
        self.pids = {game: 4242}
        self.alive = {game}
        self.allow_set = True

    def top_windows(self):
        return list(self.windows)

    def parents(self):
        return dict(self.tree)

    def is_window(self, hwnd):
        return hwnd in self.alive

    def window_size(self, hwnd):
        return (420, 380)

    def get_exstyle(self, hwnd):
        return self.styles.get(hwnd, W.WS_EX_APPWINDOW)

    def set_exstyle(self, hwnd, style):
        self.styles[hwnd] = style

    def set_pos(self, hwnd, after, x, y, cx, cy, flags):
        self.calls.append(("pos", hwnd, flags))
        return True

    def close(self, hwnd):
        pass

    def monitor_rect(self, hwnd):
        return (0, 0, 1920, 1080)

    def dpi_scale(self, hwnd):
        return 1.0

    def foreground(self):
        return self.fg

    def set_foreground(self, hwnd):
        self.calls.append(("setfg", hwnd))
        if self.allow_set:
            self.fg = hwnd
        return self.allow_set

    def window_pid(self, hwnd):
        return self.pids.get(hwnd, 0)


def test_creation_activation_is_handed_back_and_recorded():
    win32 = ForegroundWin32()
    win32.windows = [(900, 51, W.CHROME_CLASS, "NeedleDropper", True, False)]
    win32.alive.add(900)
    win32.pids[900] = 51
    win32.tree = {51: 50}
    win32.fg = 900                                    # Chromium activated its new window
    pw = W.PlayerWindow(win32, sleep=lambda s: None, clock=_ticking_clock())
    assert pw.adopt(50, prev_foreground=777)
    assert ("setfg", 777) in win32.calls and win32.fg == 777
    assert pw.took_foreground is True and pw.handed_back is True
    # parked off screen, shown (D14 fallback: the SDK plays nothing in a hidden window), never activated
    parks = [c for c in win32.calls if c[0] == "pos" and c[2] & W.SWP_SHOWWINDOW and not c[2] & W.SWP_HIDEWINDOW]
    assert parks and all(c[2] & W.SWP_NOACTIVATE for c in win32.calls if c[0] == "pos")


def test_a_quiet_creation_is_recorded_too_and_nothing_is_forced():
    win32 = ForegroundWin32()
    win32.windows = [(901, 61, W.CHROME_CLASS, "NeedleDropper", True, False)]
    win32.alive.add(901)
    win32.pids[901] = 61
    win32.tree = {61: 60}
    pw = W.PlayerWindow(win32, sleep=lambda s: None)
    pw.clock = _ticking_clock()
    assert pw.adopt(60, prev_foreground=777)
    assert pw.took_foreground is False and pw.handed_back is None
    assert not any(c[0] == "setfg" for c in win32.calls)


def test_a_refused_hand_back_is_recorded():
    win32 = ForegroundWin32()
    win32.windows = [(902, 71, W.CHROME_CLASS, "NeedleDropper", True, False)]
    win32.alive.add(902)
    win32.pids[902] = 71
    win32.tree = {71: 70}
    win32.fg = 902
    win32.allow_set = False                           # Windows' foreground lock refuses it
    pw = W.PlayerWindow(win32, sleep=lambda s: None)
    pw.clock = _ticking_clock()
    assert pw.adopt(70, prev_foreground=777)
    assert pw.took_foreground is True and pw.handed_back is False


def test_the_deck_reports_the_player_window(deck):
    info = deck.player_info()
    assert info["pageOpen"] is False and info["opened"] == 0 and info["tookForeground"] is None
    deck.window = SimpleNamespace(took_foreground=True, handed_back=True, shown=False, close=lambda: None)
    info = deck.player_info()
    assert info["tookForeground"] is True and info["handedBack"] is True and info["shown"] is False


def _ticking_clock():
    t = [0.0]

    def clock():
        t[0] += 0.05
        return t[0]
    return clock
