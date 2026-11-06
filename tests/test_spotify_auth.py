"""Spotify PKCE login, callback server, token handling and DPAPI secret storage."""
from __future__ import annotations

import http.client
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_spotify_fakes import FakeHTTP  # noqa: E402

from needledropper import secrets as nd_secrets  # noqa: E402
from needledropper.providers import spotify_auth as sa  # noqa: E402


# ------------------------------------------------------------------ PKCE ----

def test_pkce_rfc7636_appendix_b_vector():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert sa.code_challenge_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_code_verifier_shape():
    seen = set()
    for _ in range(20):
        v = sa.generate_code_verifier()
        assert len(v) == 64
        assert set(v) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        seen.add(v)
    assert len(seen) == 20
    with pytest.raises(ValueError):
        sa.generate_code_verifier(42)
    assert "=" not in sa.code_challenge_s256(sa.generate_code_verifier())


def test_authorize_url_params():
    url = sa.build_authorize_url("my-client", "http://127.0.0.1:8321/callback", sa.SCOPES, "st4te", "chall")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://accounts.spotify.com/authorize"
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q == {
        "client_id": "my-client",
        "response_type": "code",
        "redirect_uri": "http://127.0.0.1:8321/callback",
        "code_challenge_method": "S256",
        "code_challenge": "chall",
        "state": "st4te",
        "scope": "user-modify-playback-state user-read-playback-state",
    }
    assert "%20" in url and "+" not in url  # scopes space-separated, percent-encoded
    assert sa.redirect_uri_for(8321) == "http://127.0.0.1:8321/callback"


# ------------------------------------------------------- callback server ----

def _get(port: int, path: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8")
    finally:
        conn.close()


def test_callback_server_captures_code_on_ephemeral_port():
    with sa.LoopbackCallbackServer(0, expected_state="abc") as srv:
        port = srv.port
        assert port != 0 and srv.redirect_uri == f"http://127.0.0.1:{port}/callback"
        assert _get(port, "/favicon.ico")[0] == 404
        status, body = _get(port, "/callback?code=WRONG&state=nope")
        assert status == 400 and "Stale" in body
        assert srv.wait(0.05) is None  # a stale state does not end the wait
        status, body = _get(port, "/callback?code=AUTHCODE&state=abc")
        assert status == 200 and "connected" in body.lower() and "<script" not in body
        res = srv.wait(2)
        assert res is not None and res.ok and res.code == "AUTHCODE" and res.state == "abc"


def test_callback_server_reports_denied_access_and_escapes_text():
    with sa.LoopbackCallbackServer(0, expected_state="s") as srv:
        status, body = _get(srv.port, "/callback?error=access_denied&state=s&error_description=%3Cb%3Eno%3C%2Fb%3E")
        assert status == 200 and "declined" in body and "<b>no</b>" not in body and "&lt;b&gt;" in body
        res = srv.wait(2)
        assert res is not None and not res.ok and res.error == "access_denied"


def test_callback_server_port_in_use_is_friendly():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        port = blocker.getsockname()[1]
        with pytest.raises(sa.SpotifyAuthError) as ei:
            sa.LoopbackCallbackServer(port).start()
        assert ei.value.error == "port_in_use" and "redirectPort" in str(ei.value)
    finally:
        blocker.close()


# --------------------------------------------------------- token requests ----

def test_exchange_code_posts_pkce_form():
    fake = FakeHTTP().add("POST", "/api/token", (200, {
        "access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": "a b", "token_type": "Bearer"}))
    tokens = sa.exchange_code("cid", "CODE", "http://127.0.0.1:8321/callback", "VERIFIER", http=fake,
                              now=lambda: 1000.0)
    call = fake.calls[0]
    assert call.url == sa.TOKEN_URL
    assert call.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert call.form == {"grant_type": "authorization_code", "code": "CODE",
                         "redirect_uri": "http://127.0.0.1:8321/callback", "client_id": "cid",
                         "code_verifier": "VERIFIER"}
    assert "client_secret" not in call.form and "Authorization" not in call.headers
    assert tokens["access_token"] == "AT" and tokens["refresh_token"] == "RT"
    assert tokens["expires_at"] == 4600.0 and tokens["client_id"] == "cid" and tokens["scope"] == "a b"


def test_refresh_rotates_or_keeps_refresh_token_and_reports_invalid_grant():
    fake = FakeHTTP().add("POST", "/api/token",
                          (200, {"access_token": "AT2", "expires_in": 3600}),
                          (200, {"access_token": "AT3", "refresh_token": "RT3", "expires_in": 3600}),
                          (400, {"error": "invalid_grant", "error_description": "Refresh token revoked"}))
    assert sa.refresh("cid", "RT1", http=fake)["refresh_token"] == "RT1"
    assert fake.calls[0].form == {"grant_type": "refresh_token", "refresh_token": "RT1", "client_id": "cid"}
    assert sa.refresh("cid", "RT1", http=fake)["refresh_token"] == "RT3"
    with pytest.raises(sa.SpotifyAuthError) as ei:
        sa.refresh("cid", "RT3", http=fake)
    assert ei.value.revoked and ei.value.status == 400 and "spotify-login" in str(ei.value)


def test_network_failure_is_auth_error():
    fake = FakeHTTP().add("POST", "/api/token", ConnectionError("no route to host"))
    with pytest.raises(sa.SpotifyAuthError) as ei:
        sa.refresh("cid", "RT", http=fake)
    assert ei.value.error == "network" and not ei.value.revoked


# ------------------------------------------------------------ token store ----

def test_token_store_roundtrip_and_encryption(tmp_path):
    store = sa.TokenStore(SimpleNamespace(app_dir=tmp_path))
    assert store.load() is None and not store.has_refresh_token()
    store.save({"access_token": "ACCESS-SECRET", "refresh_token": "REFRESH-SECRET", "expires_at": 123.0,
                "scope": "s", "client_id": "cid", "junk": "dropped"})
    assert store.path == tmp_path / "spotify_token.bin"
    raw = store.path.read_bytes()
    if nd_secrets.dpapi_available():
        assert b"REFRESH-SECRET" not in raw
    again = sa.TokenStore(tmp_path).load()
    assert again == {"access_token": "ACCESS-SECRET", "refresh_token": "REFRESH-SECRET", "expires_at": 123.0,
                     "scope": "s", "client_id": "cid"}
    store.clear()
    assert not store.path.exists() and store.load() is None


def test_token_manager_refreshes_when_expiring(tmp_path):
    store = sa.TokenStore(tmp_path)
    store.save({"access_token": "OLD", "refresh_token": "RT1", "expires_at": 1030.0, "scope": "s",
                "client_id": "cid"})
    fake = FakeHTTP().add("POST", "/api/token", (200, {"access_token": "NEW", "refresh_token": "RT2",
                                                       "expires_in": 3600}))
    tm = sa.TokenManager("cid", store, http=fake, now=lambda: 1000.0)
    assert tm.access_token() == "NEW"  # 30 s left < 60 s margin -> refreshed
    assert tm.access_token() == "NEW" and len(fake.calls) == 1
    assert store.load()["refresh_token"] == "RT2"
    assert tm.force_refresh("SOMETHING-ELSE") == "NEW" and len(fake.calls) == 1  # already replaced
    assert not sa.TokenManager("other-client", store).usable()


# ------------------------------------------------------------ interactive ----

def test_login_without_client_id_prints_dashboard_steps(tmp_path):
    out: list[str] = []
    with pytest.raises(sa.SpotifyAuthError) as ei:
        sa.login_interactive({"clientId": "", "redirectPort": 8321}, SimpleNamespace(app_dir=tmp_path),
                             open_browser=False, print_fn=out.append)
    text = "\n".join(out)
    assert ei.value.error == "no_client_id"
    for needle in ("developer.spotify.com/dashboard", "Create app", "Web API",
                   "http://127.0.0.1:8321/callback", "Client ID", "needledropper setup", "localhost"):
        assert needle in text
    assert not (tmp_path / "spotify_token.bin").exists()


def test_login_interactive_end_to_end(tmp_path):
    fake = FakeHTTP()
    fake.add("POST", "/api/token", (200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                                          "scope": "user-modify-playback-state user-read-playback-state"}))
    fake.add("GET", "/v1/me", (200, {"display_name": "Thrall", "id": "thrall"}))
    seen: dict[str, str] = {}

    def browser(url: str) -> None:
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        seen.update(q)
        port = urlsplit(q["redirect_uri"]).port

        def hit() -> None:
            time.sleep(0.05)
            _get(port, f"/callback?code=THE-CODE&state={q['state']}")

        threading.Thread(target=hit, daemon=True).start()

    out: list[str] = []
    sa.login_interactive({"clientId": "cid", "redirectPort": 0}, SimpleNamespace(app_dir=tmp_path), http=fake,
                         print_fn=out.append, browser=browser, timeout=10)
    token_call = fake.find("POST", "/api/token")[0]
    assert token_call.form["code"] == "THE-CODE"
    assert token_call.form["redirect_uri"] == seen["redirect_uri"]
    assert sa.code_challenge_s256(token_call.form["code_verifier"]) == seen["code_challenge"]
    assert seen["scope"] == "user-modify-playback-state user-read-playback-state"
    stored = sa.TokenStore(tmp_path).load()
    assert stored["refresh_token"] == "RT" and stored["client_id"] == "cid"
    text = "\n".join(out)
    assert "successful" in text and "Thrall" in text
    assert sa.is_logged_in({"clientId": "cid"}, SimpleNamespace(app_dir=tmp_path))


def test_login_warns_when_account_not_allowlisted(tmp_path):
    fake = FakeHTTP()
    fake.add("POST", "/api/token", (200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}))
    fake.add("GET", "/v1/me", (403, {"error": {"status": 403, "message": "User not registered in the Developer Dashboard"}}))

    def browser(url: str) -> None:
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        threading.Thread(target=_get, args=(urlsplit(q["redirect_uri"]).port,
                                            f"/callback?code=C&state={q['state']}"), daemon=True).start()

    out: list[str] = []
    sa.login_interactive({"clientId": "cid", "redirectPort": 0}, tmp_path, http=fake, print_fn=out.append,
                         browser=browser, timeout=10)
    assert "User Management" in "\n".join(out)


def test_login_times_out_with_redirect_hint(tmp_path):
    out: list[str] = []
    with pytest.raises(sa.SpotifyAuthError) as ei:
        sa.login_interactive({"clientId": "cid", "redirectPort": 0}, tmp_path, http=FakeHTTP(),
                             print_fn=out.append, browser=lambda url: None, timeout=0.3)
    assert ei.value.error == "timeout" and "Redirect URI" in str(ei.value)


def test_login_access_denied(tmp_path):
    def browser(url: str) -> None:
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        threading.Thread(target=_get, args=(urlsplit(q["redirect_uri"]).port,
                                            f"/callback?error=access_denied&state={q['state']}"), daemon=True).start()

    with pytest.raises(sa.SpotifyAuthError) as ei:
        sa.login_interactive({"clientId": "cid", "redirectPort": 0}, tmp_path, http=FakeHTTP(),
                             print_fn=lambda *a: None, browser=browser, timeout=10)
    assert ei.value.error == "access_denied"
    assert not (tmp_path / "spotify_token.bin").exists()


# ---------------------------------------------------------------- secrets ----

@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_roundtrip():
    data = b"refresh-token \x00\xff binary"
    blob = nd_secrets.protect(data)
    assert blob != data and data not in blob and not blob.startswith(b"NDB64:")
    assert nd_secrets.unprotect(blob) == data
    assert nd_secrets.unprotect(nd_secrets.protect(b"")) == b""
    with pytest.raises(nd_secrets.SecretError):
        nd_secrets.unprotect(blob, entropy=b"wrong entropy")
    with pytest.raises(nd_secrets.SecretError):
        nd_secrets.unprotect(b"definitely not a DPAPI blob")


def test_base64_fallback_when_dpapi_missing(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(nd_secrets, "dpapi_available", lambda: False)
    monkeypatch.setattr(nd_secrets, "_warned_fallback", False)
    with caplog.at_level("WARNING", logger="needledropper.secrets"):
        blob = nd_secrets.protect(b"hello")
    assert blob.startswith(b"NDB64:") and nd_secrets.unprotect(blob) == b"hello"
    assert any("NOT encrypted" in r.getMessage() for r in caplog.records)
    path = tmp_path / "sub" / "s.bin"
    nd_secrets.save_secret(path, {"a": 1, "b": "two"})
    assert nd_secrets.load_secret(path) == {"a": 1, "b": "two"}


def test_save_and_load_secret_edge_cases(tmp_path):
    path = tmp_path / "secret.bin"
    assert nd_secrets.load_secret(path) is None
    nd_secrets.save_secret(path, {"k": "v"})
    assert nd_secrets.load_secret(path) == {"k": "v"}
    assert [p.name for p in tmp_path.iterdir()] == ["secret.bin"]  # atomic write leaves no temp files
    path.write_bytes(b"corrupt")
    assert nd_secrets.load_secret(path) is None
    assert nd_secrets.delete_secret(path) and not nd_secrets.delete_secret(path)
    with pytest.raises(TypeError):
        nd_secrets.protect("not bytes")  # type: ignore[arg-type]
