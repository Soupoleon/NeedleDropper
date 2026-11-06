"""Connecting Spotify from the Connect page without blocking (spotify_auth.start_login), the
page's Spotify endpoints (connect / devices / disconnect), the account / Premium check, the
"sign-in expired" notification and the provider's helpers. All Spotify HTTP is scripted
(FakeHTTP); the callback listener runs on an ephemeral port; nothing reaches the network."""
from __future__ import annotations

import http.client
import json
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import make_shell, raw_config, wait_for  # noqa: E402
from test_spotify_fakes import FakeHTTP, save_tokens, track  # noqa: E402

from needledropper import tray  # noqa: E402
from needledropper.providers import spotify_auth as sa  # noqa: E402
from needledropper.providers.spotify import PREMIUM_MESSAGE, SpotifyProvider  # noqa: E402

CID = "0123456789abcdef0123456789abcdef"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def hit(url: str) -> tuple[int, str]:
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    try:
        conn.request("GET", parts.path + ("?" + parts.query if parts.query else ""))
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8")
    finally:
        conn.close()


def approve(authorize_url: str, *, code: str = "THE-CODE", error: str = "") -> tuple[int, str]:
    """What Spotify does after the user clicks Agree: redirect the browser to our callback."""
    q = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
    tail = f"error={error}" if error else f"code={code}"
    return hit(f"{q['redirect_uri']}?{tail}&state={q['state']}")


def token_routes(fake: FakeHTTP, who: str = "Thrall", me_status: int = 200) -> FakeHTTP:
    # Spotify grants the scopes 1.2.1 asks for (a narrower grant is a sign-in to repeat, D9)
    fake.add("POST", "/api/token", (200, {"access_token": "AT-NEW", "refresh_token": "RT-NEW", "expires_in": 3600,
                                          "scope": " ".join(sa.SCOPES)}))
    fake.add("GET", "/v1/me", (me_status, {"display_name": who, "id": who.lower()} if me_status == 200 else
                               {"error": {"status": me_status, "message": "User not registered"}}))
    return fake


# ------------------------------------------------------------ start_login --

def test_start_login_connects_without_blocking_and_the_running_provider_picks_it_up(tmp_path):
    paths = SimpleNamespace(app_dir=tmp_path)
    fake = token_routes(FakeHTTP())
    # already running, not logged in yet; connect mode: app mode would also need the in-app player
    provider = SpotifyProvider({"clientId": CID, "device": "connect"}, paths, http=fake)
    assert not provider.available() and provider.status_dict()["badge"] == "not-set-up"
    changes: list[str] = []
    t0 = time.monotonic()
    session = sa.start_login({"clientId": CID, "redirectPort": 8321}, paths, http=fake, port=0,
                             on_change=lambda s: changes.append(s.state))
    assert time.monotonic() - t0 < 2  # returns at once
    assert session.state == "waiting" and not session.done
    q = {k: v[0] for k, v in parse_qs(urlsplit(session.authorize_url).query).items()}
    assert q["client_id"] == CID and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == session.redirect_uri and urlsplit(q["redirect_uri"]).port != 8321
    snap = session.to_dict()
    assert snap["state"] == "waiting" and snap["authorizeUrl"] == session.authorize_url
    assert "verifier" not in json.dumps(snap).lower()
    status, page = approve(session.authorize_url)
    assert status == 200 and "control panel" in page and "terminal" not in page
    assert session.wait(5) == "connected"
    assert session.account == "Thrall" and "Thrall" in session.message and session.warning is None
    form = fake.find("POST", "/api/token")[0].form
    assert form["code"] == "THE-CODE" and sa.code_challenge_s256(form["code_verifier"]) == q["code_challenge"]
    assert sa.TokenStore(tmp_path).load()["refresh_token"] == "RT-NEW"
    assert wait_for(lambda: changes == ["connected"])
    # the provider that was running all along re-reads the token file: no rebuild needed
    assert provider.available() and provider.status_dict()["badge"] == "connected"
    assert session.to_dict()["authorizeUrl"] is None  # no longer offered once finished


def test_start_login_reports_allowlist_problems(tmp_path):
    fake = token_routes(FakeHTTP(), me_status=403)
    session = sa.start_login({"clientId": CID}, tmp_path, http=fake, port=0)
    approve(session.authorize_url)
    assert session.wait(5) == "connected"
    assert "User Management" in session.warning


def test_start_login_failures(tmp_path):
    with pytest.raises(sa.SpotifyAuthError) as ei:
        sa.start_login({"clientId": ""}, tmp_path, port=0)
    assert ei.value.error == "no_client_id"

    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        with pytest.raises(sa.SpotifyAuthError) as ei:
            sa.start_login({"clientId": CID, "redirectPort": blocker.getsockname()[1]}, tmp_path)
        assert ei.value.error == "port_in_use"
    finally:
        blocker.close()

    denied = sa.start_login({"clientId": CID}, tmp_path, http=FakeHTTP(), port=0)
    approve(denied.authorize_url, error="access_denied")
    assert denied.wait(5) == "error" and "declined" in denied.message
    assert not (tmp_path / "spotify_token.bin").exists()

    slow = sa.start_login({"clientId": CID, "redirectPort": 8321}, tmp_path, http=FakeHTTP(), port=0, timeout=0.3)
    assert slow.wait(5) == "timeout"
    assert "http://127.0.0.1:8321/callback" in slow.message  # the URI to register, not the test port

    bad = FakeHTTP().add("POST", "/api/token", (400, {"error": "invalid_grant", "error_description": "bad code"}))
    broken = sa.start_login({"clientId": CID}, tmp_path, http=bad, port=0)
    approve(broken.authorize_url)
    assert broken.wait(5) == "error" and "invalid_grant" in broken.message


def test_cancel_frees_the_callback_port(tmp_path):
    port = free_port()
    session = sa.start_login({"clientId": CID, "redirectPort": port}, tmp_path, http=FakeHTTP())
    t0 = time.monotonic()
    session.cancel()
    assert time.monotonic() - t0 < 3
    assert session.state == "error" and "cancelled" in session.message.lower()
    again = sa.start_login({"clientId": CID, "redirectPort": port}, tmp_path, http=FakeHTTP())  # port is free again
    again.cancel()


def test_login_interactive_still_works(tmp_path):
    fake = token_routes(FakeHTTP())
    out: list[str] = []

    def browser(url: str) -> None:
        import threading
        threading.Thread(target=approve, args=(url,), daemon=True).start()

    sa.login_interactive({"clientId": CID, "redirectPort": 0}, tmp_path, http=fake, print_fn=out.append,
                         browser=browser, timeout=10)
    assert "successful" in "\n".join(out) and sa.is_logged_in({"clientId": CID}, tmp_path)


# -------------------------------------------------------- provider helpers --

def test_provider_status_dict_and_reset(tmp_path):
    paths = SimpleNamespace(app_dir=tmp_path)
    assert SpotifyProvider({"clientId": ""}, paths, http=FakeHTTP()).status_dict()["badge"] == "not-set-up"
    save_tokens(tmp_path, client_id=CID)
    # connect mode: in app mode available() also needs the in-app player (test_spotify_inapp.py)
    sp = SpotifyProvider({"clientId": CID, "deviceName": "PC", "device": "connect"}, paths, http=FakeHTTP())
    st = sp.status_dict()
    assert st == {"badge": "connected", "message": None, "configured": True, "loggedIn": True, "deviceName": "PC",
                  "missingScopes": [], "scopeMessage": None, "saveError": None,
                  "stuck": False,                    # the session health verdict (SPOT-NO-SESSION-HEALTH)
                  # 1.2.1 (D17): the mode, the detailed state, the in-app player (none in connect mode)
                  "mode": "connect", "state": "connected", "warning": None, "relogin": False, "player": None}
    assert "AT1" not in json.dumps(st) and "RT1" not in json.dumps(st)
    sp._mark_unavailable(PREMIUM_MESSAGE)
    assert sp.status_dict()["badge"] == "premium" and not sp.available()
    sp.reset_session()
    assert sp.status_dict()["badge"] == "connected" and sp.available()
    other = SpotifyProvider({"clientId": "ffffffffffffffffffffffffffffffff"}, paths, http=FakeHTTP())
    st = other.status_dict()
    assert st["badge"] == "error" and "another Client ID" in st["message"]


def test_list_devices(tmp_path):
    save_tokens(tmp_path, client_id=CID)
    fake = FakeHTTP().add("GET", "/v1/me/player/devices", (200, {"devices": [
        {"id": "pc", "name": "GAMING-PC", "type": "Computer", "is_active": True, "volume_percent": 70},
        {"id": "tv", "name": "Living room", "type": "TV", "is_restricted": True, "supports_volume": False},
        {"id": "", "name": ""},
    ]}))
    sp = SpotifyProvider({"clientId": CID}, SimpleNamespace(app_dir=tmp_path), http=fake)
    assert sp.list_devices() == [
        {"id": "pc", "name": "GAMING-PC", "type": "Computer", "isActive": True, "isRestricted": False, "volume": 70,
         "supportsVolume": True},
        {"id": "tv", "name": "Living room", "type": "TV", "isActive": False, "isRestricted": True, "volume": None,
         "supportsVolume": False},
    ]


# --------------------------------------------------------- Connect page --

@pytest.fixture
def rig(tmp_path, _needledropper_home):
    port = free_port()
    fake = token_routes(FakeHTTP())
    ns = make_shell(tmp_path, Path(_needledropper_home), cfg_extra={"spotify": {"redirectPort": port}},
                    spotify_http=fake)
    ns.fake = fake
    ns.port = port
    try:
        yield ns
    finally:
        ns.close()


def spotify(rig) -> dict:
    return rig.call("GET", "/api/state")[1]["spotify"]


def test_connect_flow(rig):
    s = spotify(rig)
    assert s["state"] == "not-set-up" and s["redirectUri"].endswith(f":{rig.port}/callback")
    assert s["dashboardUrl"] == sa.DASHBOARD_URL and s["premium"] == "unknown" and s["account"] is None
    st, data = rig.call("POST", "/api/spotify/connect", {"clientId": "not valid!"})
    assert st == 400 and "spotify.clientId" in data["fields"]
    st, data = rig.call("POST", "/api/spotify/connect", {"clientId": CID})
    assert st == 200 and data["state"] == "waiting"
    assert data["authorizeUrl"].startswith(sa.AUTHORIZE_URL) and f"127.0.0.1%3A{rig.port}" in data["authorizeUrl"]
    assert raw_config(rig.cfg_file)["spotify"]["clientId"] == CID
    s = spotify(rig)
    assert s["state"] == "waiting" and s["login"]["authorizeUrl"] == data["authorizeUrl"]
    approve(data["authorizeUrl"])
    assert wait_for(lambda: spotify(rig)["state"] == "connected", timeout=8)
    s = spotify(rig)
    assert s["connected"] and s["account"] == "Thrall" and s["premium"] == "ok" and s["warning"] is None
    assert raw_config(rig.cfg_file).get("setupComplete") is None  # only Done completes the setup
    assert rig.app.spotify_provider().available()
    text = json.dumps(rig.call("GET", "/api/state")[1])
    assert "AT-NEW" not in text and "RT-NEW" not in text and rig.deck.token not in text

    rig.fake.add("GET", "/v1/me/player/devices", (200, {"devices": [
        {"id": "pc", "name": "GAMING-PC", "type": "Computer", "is_active": True, "volume_percent": 55}]}))
    st, data = rig.call("GET", "/api/spotify/devices")
    assert st == 200 and data["devices"][0]["name"] == "GAMING-PC" and data["selected"] == ""
    st, _ = rig.call("POST", "/api/config", {"spotify": {"deviceName": "GAMING-PC"}})
    assert st == 200 and rig.call("GET", "/api/spotify/devices")[1]["selected"] == "GAMING-PC"
    assert spotify(rig)["deviceName"] == "GAMING-PC"

    st, data = rig.call("POST", "/api/spotify/disconnect", {})
    assert st == 200 and not (rig.home / "spotify_token.bin").exists()
    s = spotify(rig)
    assert s["state"] == "not-set-up" and s["clientId"] == CID  # one click to sign in again
    assert rig.call("GET", "/api/spotify/devices")[0] == 409


def test_an_account_spotify_refuses_gets_a_warning(tmp_path, _needledropper_home):
    port = free_port()
    fake = token_routes(FakeHTTP(), me_status=403)
    ns = make_shell(tmp_path, Path(_needledropper_home), cfg_extra={"spotify": {"redirectPort": port}},
                    spotify_http=fake)
    try:
        st, data = ns.call("POST", "/api/spotify/connect", {"clientId": CID})
        approve(data["authorizeUrl"])
        # v1.2.1 review ON-3: the card says "Account not allowed" at once, not "Connected" until the first song
        assert wait_for(lambda: ns.call("GET", "/api/state")[1]["spotify"]["state"] == "allowlist", timeout=8)
        s = ns.call("GET", "/api/state")[1]["spotify"]
        assert s["premium"] == "unknown" and "User Management" in s["warning"]
    finally:
        ns.close()


def test_premium_refusal_shows_on_the_card(rig):
    save_tokens(rig.home, client_id=CID)
    rig.call("POST", "/api/config", {"spotify": {"clientId": CID}})
    assert spotify(rig)["state"] == "connected"
    rig.app.spotify_provider()._mark_unavailable(PREMIUM_MESSAGE)
    s = spotify(rig)
    assert s["state"] == "premium" and s["premium"] == "required"


def test_connect_reports_a_busy_callback_port(rig):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", rig.port))
    blocker.listen(1)
    try:
        st, data = rig.call("POST", "/api/spotify/connect", {"clientId": CID})
        assert st == 409 and data["code"] == "port_in_use" and "redirectPort" in data["error"]
    finally:
        blocker.close()


def test_reconnect_with_another_client_id_reuses_the_port(rig):
    st, first = rig.call("POST", "/api/spotify/connect", {"clientId": CID})
    assert st == 200
    other = "ffffffffffffffffffffffffffffffff"
    st, second = rig.call("POST", "/api/spotify/connect", {"clientId": other})  # old listener must be gone
    assert st == 200 and f"client_id={other}" in second["authorizeUrl"]
    status, page = approve(first["authorizeUrl"])  # the first login's link is stale now
    assert status == 400 and "Stale" in page
    approve(second["authorizeUrl"])
    assert wait_for(lambda: spotify(rig)["state"] == "connected", timeout=8)
    assert sa.TokenStore(rig.home).load()["client_id"] == other


def test_an_expired_sign_in_is_reported_once_and_opens_setup(rig):
    save_tokens(rig.home, client_id=CID, expires_at=time.time() - 60)  # must refresh
    rig.fake.set("POST", "/api/token", (400, {"error": "invalid_grant", "error_description": "Refresh token revoked"}))
    rig.call("POST", "/api/config", {"spotify": {"clientId": CID}})
    rig.app._next_spotify_watch = 0.0
    rig.app._hk_wake.set()
    notes = rig.app.notifier

    assert wait_for(lambda: tray.NOTE_SPOTIFY_EXPIRED in notes.kinds(), timeout=5)
    s = spotify(rig)
    assert s["state"] == "error" and "expired" in s["message"]
    assert notes.kinds().count(tray.NOTE_SPOTIFY_EXPIRED) == 1
    rig.app.on_engine_status({"notice": "spotify-auth-expired"})  # the engine saying it again: no repeat
    assert notes.kinds().count(tray.NOTE_SPOTIFY_EXPIRED) == 1
    opened: list = []
    rig.deck.url_opener = lambda url: opened.append(url) or True
    notes.clicked()
    assert opened == [rig.deck.panel_url]


def test_the_account_check_shares_the_providers_token_manager(rig):
    """Refresh tokens rotate: the shell's account check and playback must refresh through one
    token manager (one lock), or each would invalidate the other's refresh."""
    save_tokens(rig.home, client_id=CID)
    rig.call("POST", "/api/config", {"spotify": {"clientId": CID}})
    sp = rig.app.spotify_provider()
    calls: list = []
    real = sp._tokens.access_token
    sp._tokens.access_token = lambda: calls.append(1) or real()
    rig.app._check_spotify_account()
    assert calls == [1]
    s = spotify(rig)
    assert s["account"] == "Thrall" and s["premium"] == "ok"


# ------------------------------------------------- in-app Spotify (1.2.1) --

def test_a_sign_in_from_before_1_2_1_asks_once_for_a_new_one(tmp_path, _needledropper_home):
    """D9: a stored sign-in without streaming / user-read-email: one notification, the Connect page asks
    to sign in again (the Client ID stays), and the game's v1.2 status reads "error" (D17)."""
    from needledropper.providers.spotify import RELOGIN_MESSAGE

    home = Path(_needledropper_home)
    save_tokens(home, client_id=CID, scope="user-modify-playback-state user-read-playback-state user-read-private")
    ns = make_shell(tmp_path, home, cfg_extra={"spotify": {"clientId": CID}})
    try:
        app = ns.app
        for _ in range(2):
            app._next_spotify_watch = 0.0
            app._watch_spotify(app._clock())
        notes = [n for n in app.notifier.history if n.kind == tray.NOTE_SPOTIFY_RELOGIN]
        assert len(notes) == 1 and notes[0].title == "Sign in to Spotify again" and RELOGIN_MESSAGE in notes[0].text
        assert not [n for n in app.notifier.history if n.kind == tray.NOTE_SPOTIFY_EXPIRED]
        assert app._game_status()["spotify"] == "error"
        s = spotify(ns)
        assert s["state"] == "error" and s["message"] == RELOGIN_MESSAGE and s["clientId"] == CID
    finally:
        ns.close()


def test_the_connect_page_shows_the_in_app_players_warning(rig, monkeypatch):
    monkeypatch.setattr(rig.app, "_spotify_status", lambda: {"badge": "connected", "message": None, "loggedIn": True,
                                                             "configured": True, "missingScopes": [],
                                                             "warning": "install Google Chrome", "state": "no-player"})
    s = spotify(rig)
    assert s["warning"] == "install Google Chrome" and s["state"] == "connected"
    assert rig.app._game_status()["spotify"] == "error"      # D17: no-player is "error" for v1.2 readers


def test_the_housekeeping_reconnects_an_in_app_player_that_never_got_ready(tmp_path, _needledropper_home,
                                                                            monkeypatch):
    """A no-player Spotify is never prepared by the engine (unavailable), so the app's 30 s Spotify check
    calls sync_sdk, which connects such a player again at most once a minute."""
    from test_spotify_inapp import PageDeck

    from needledropper.providers import spotify as spmod

    home = Path(_needledropper_home)
    save_tokens(home, client_id=CID)
    page = PageDeck(ready=False)
    built: list = []

    def factory(cfg, paths, deck):
        built.append(SpotifyProvider(cfg["spotify"], paths, deck=page, http=FakeHTTP()))
        return list(built[-1:])

    monkeypatch.setattr(spmod, "SDK_READY_GRACE_S", -1.0)
    ns = make_shell(tmp_path, home, cfg_extra={"spotify": {"clientId": CID}}, provider_factory=factory)
    try:
        assert not built[-1].available() and page.ops()[0] == "connect"
        ns.app._next_spotify_watch = 0.0
        ns.app._watch_spotify(ns.app._clock())
        assert page.ops().count("reconnect") == 1
    finally:
        ns.close()
