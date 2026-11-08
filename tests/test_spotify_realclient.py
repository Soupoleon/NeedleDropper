"""Spotify fixes from the owner's real-client test (docs/review/real-client-2026-11-05.md #3-#5) and the
baseline findings SPOT-VERIFY-UNMANAGED / SPOT-TOKEN-SAVE-LOSS (docs/review/baseline-findings.md).

All hermetic: scripted HTTP (test_spotify_fakes.FakeHTTP), a fake clock, tmp_path app dirs.
"""
from __future__ import annotations

import errno
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_spotify_fakes import (DROP_TRACK, DROP_URI, PC, PHONE, V11_SCOPE, FakeClock, FakeHTTP, make_drop,  # noqa: E402
                                make_rig, ok_routes, player_state, save_tokens, tid, track)

from needledropper import secrets as nd_secrets  # noqa: E402
from needledropper.providers import spotify as sp  # noqa: E402
from needledropper.providers import spotify_auth as sa  # noqa: E402
from needledropper.providers.base import PlayHandle, ProviderError, ProviderUnavailable  # noqa: E402

PREV = track("prev", "My Own Song", ["Someone"])
ALBUM_CTX = {"type": "album", "uri": "spotify:album:" + tid("album")}


def err(status, reason=None, message="nope"):
    e = {"status": status, "message": message}
    if reason:
        e["reason"] = reason
    return (status, {"error": e})


SEARCH_HIT = {"tracks": {"items": [track("good", "I'll Keep Coming", ["Low Roar"])]}}


# ===================================================================== #3 scopes ===

def test_sign_in_asks_for_user_read_private():
    assert "user-read-private" in sa.SCOPES
    url = sa.build_authorize_url("cid", "http://127.0.0.1:8321/callback", sa.SCOPES, "s", "c")
    assert "user-read-private" in url


def test_insufficient_client_scope_403_is_not_premium_and_search_still_works(tmp_path):
    """Real client: /search with market=from_token -> 403 "Insufficient client scope" was read as
    "Premium required" and Spotify was switched off for the session."""
    rig = make_rig(tmp_path)
    rig.http.add("GET", "/v1/search", err(403, message="Insufficient client scope"), (200, SEARCH_HIT))
    out = rig.provider.resolve_drop(make_drop(spotify=None))
    assert out.spotify == f"spotify:track:{tid('good')}"
    searches = rig.http.find("GET", "/v1/search")
    assert searches[0].query.get("market") == "from_token" and "market" not in searches[1].query
    assert rig.provider.available() and rig.provider.unavailable_reason() is None   # not "Premium required"
    st = rig.provider.status_dict()
    assert st["badge"] == "connected" and st["missingScopes"] == ["user-read-private"]
    assert "sign in" in st["scopeMessage"].lower()
    # the next search skips the market straight away (no second 403 round trip)
    rig.http.set("GET", "/v1/search", (200, SEARCH_HIT))
    rig.provider.resolve_drop(make_drop(id="zone:1", spotify=None, title="I'll Keep Coming"), force=True)
    assert "market" not in rig.http.find("GET", "/v1/search")[-1].query


def test_scope_error_on_track_lookup_retries_without_market(tmp_path):
    rig = make_rig(tmp_path)
    rig.http.add("GET", f"/v1/tracks/{tid('drop')}", err(403, message="Insufficient client scope"),
                 (200, DROP_TRACK))
    out = rig.provider.resolve_drop(make_drop())
    assert out.durationMs == 245_000 and rig.provider.available()


def test_older_sign_in_without_the_scope_is_detected_up_front(tmp_path):
    save_tokens(tmp_path, scope=V11_SCOPE)
    rig = make_rig(tmp_path, tokens=False)
    rig.http.add("GET", "/v1/search", (200, SEARCH_HIT))
    rig.provider.resolve_drop(make_drop(spotify=None))
    assert "market" not in rig.http.find("GET", "/v1/search")[0].query  # no 403 needed to find out
    # 1.2.1 also asks for streaming and user-read-email (in-app Spotify, D9)
    missing = ["streaming", "user-read-email", "user-read-private"]
    assert rig.provider.status_dict()["missingScopes"] == missing
    rig.provider.reset_session()
    assert rig.provider.status_dict()["missingScopes"] == missing  # still the old sign-in


def test_premium_and_allowlist_403s_are_told_apart(tmp_path):
    rig = make_rig(tmp_path / "a")
    ok_routes(rig.http)
    rig.http.set("PUT", "/v1/me/player/play", err(403, "PREMIUM_REQUIRED", "Player command failed: Premium required"))
    with pytest.raises(ProviderUnavailable) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert str(ei.value) == sp.PREMIUM_MESSAGE and rig.provider.status_dict()["badge"] == "premium"

    rig = make_rig(tmp_path / "b")
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", err(403, message="User not registered in the Developer Dashboard"))
    with pytest.raises(ProviderUnavailable) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert str(ei.value) == sp.ALLOWLIST_MESSAGE and rig.provider.status_dict()["badge"] == "allowlist"


def test_fetch_profile_reports_plan_country_and_refusals():
    fake = FakeHTTP().add("GET", "/v1/me", (200, {"display_name": "Thrall", "product": "premium", "country": "us"}))
    prof = sa.fetch_profile("AT", fake)
    assert (prof["status"], prof["name"], prof["product"], prof["country"], prof["problem"]) == \
        (200, "Thrall", "premium", "US", None)
    assert sa.fetch_account("AT", fake) == (200, "Thrall")
    fake = FakeHTTP().add("GET", "/v1/me", (403, {"error": {"status": 403, "message": "Insufficient client scope"}}))
    assert sa.fetch_profile("AT", fake)["problem"] == "scope"
    fake = FakeHTTP().add("GET", "/v1/me", (403, {"error": {"status": 403,
                                                             "message": "User not registered in the Developer Dashboard"}}))
    assert sa.fetch_profile("AT", fake)["problem"] == "allowlist"
    fake = FakeHTTP().add("GET", "/v1/me", ConnectionError("offline"))
    assert sa.fetch_profile("AT", fake)["status"] is None


# ============================================================= #4 stuck desktop app ===

def _stuck_routes(rig, *, verify_state, snapshot=(204, None)):
    ok_routes(rig.http, snapshot=snapshot)
    rig.http.set("GET", "/v1/me/player", snapshot, *([(200, verify_state)] if verify_state is not None
                                                     else [(204, None)]))
    rig.http.add("PUT", "/v1/me/player", (204, None))  # transfer playback


def test_loaded_but_not_progressing_track_is_recovered_by_transfer(tmp_path):
    """Real client: play-by-uri loaded the track but progress_ms stayed at 0. The fix that worked by
    hand: transfer playback to the device with play=true, then play again."""
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    stuck = (200, player_state(DROP_TRACK, 0))
    moving = (200, player_state(DROP_TRACK, 800))
    rig.http.set("GET", "/v1/me/player", (204, None), *([stuck] * 5), moving)
    rig.http.add("PUT", "/v1/me/player", (204, None))
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    transfers = rig.http.find("PUT", "/v1/me/player")
    assert len(transfers) == 1 and transfers[0].json == {"device_ids": ["dev-pc"], "play": True}
    assert len(rig.http.find("PUT", "/v1/me/player/play")) == 3     # first, again after ~1.5 s, after the transfer
    assert h.provider == "spotify" and not h.data.get("parked")
    assert 3.0 <= sum(rig.clock.sleeps) <= 7.0                       # ~4 s of checks before the recovery


def test_track_that_never_starts_raises_stuck_and_pauses_first(tmp_path, caplog):
    """SPOT-VERIFY-UNMANAGED: a verification miss used to leave Spotify playing unmanaged while the
    engine played the next provider. Now it raises a stuck error that carries a clean-up handle for
    the end of the drop. Real-client finding #10 (the exe test, 21:52) changed what happens on the
    stuck desktop app's signature -- nothing loaded, neither pause nor resume allowed: the play is not
    sent again, playback is not transferred and no pause is sent (that sequence froze the owner's
    Spotify app), and the provider remembers the verdict for the session. This test used to assert
    the transfer and the pause."""
    rig = make_rig(tmp_path)
    no_item = {"device": dict(PC), "item": None, "progress_ms": 0, "is_playing": False,
               "actions": {"disallows": {"pausing": True, "resuming": True, "seeking": True, "skipping_next": True}}}
    _stuck_routes(rig, verify_state=no_item)
    caplog.set_level(logging.INFO, logger="needledropper.spotify")
    with pytest.raises(sp.SpotifyStuckError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    exc = ei.value
    assert isinstance(exc, ProviderError) and not isinstance(exc, ProviderUnavailable)
    assert exc.notice == "spotifyStuck" and sp.STUCK_MESSAGE in str(exc)
    assert isinstance(exc.cleanup, PlayHandle) and exc.cleanup.data["parked"]
    puts = [c.path for c in rig.http.puts()]
    assert puts.count("/v1/me/player/play") == 1                     # sent once, never again
    assert "/v1/me/player" not in puts and "/v1/me/player/pause" not in puts
    assert sum(rig.clock.sleeps) == 6.5                              # bounded observation, no more commands
    assert rig.provider.stuck()
    assert "disallowed actions: pausing, resuming, seeking, skipping_next" in caplog.text


def test_wrong_item_on_our_device_is_stuck_too(tmp_path):
    rig = make_rig(tmp_path)
    _stuck_routes(rig, verify_state=player_state(PREV, 10))
    with pytest.raises(sp.SpotifyStuckError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    assert "instead of" in str(ei.value)


def test_unreadable_player_state_is_still_assumed_to_play(tmp_path):
    rig = make_rig(tmp_path)
    ok_routes(rig.http)
    rig.http.set("GET", "/v1/me/player", (204, None), ConnectionError("flaky"))
    h = rig.provider.play(make_drop(), fade_in_ms=0)
    assert h.provider == "spotify" and rig.http.find("PUT", "/v1/me/player") == []  # nothing to recover from


def test_parked_clean_up_resumes_the_players_music_at_the_end_of_the_drop(tmp_path):
    rig = make_rig(tmp_path)
    snap = (200, player_state(PREV, 42_000, device=PHONE, context=ALBUM_CTX))
    _stuck_routes(rig, verify_state=player_state(DROP_TRACK, 0), snapshot=snap)
    with pytest.raises(sp.SpotifyStuckError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    cleanup = ei.value.cleanup
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 0, playing=False)))
    before = len(rig.http.calls)
    rig.provider.stop(cleanup, fade_out_ms=0)                        # the engine does this when the drop ends
    puts = [c for c in rig.http.calls[before:] if c.method == "PUT"]
    assert [c.path for c in puts] == ["/v1/me/player/play"]
    assert puts[0].query["device_id"] == "dev-phone" and puts[0].json["context_uri"] == ALBUM_CTX["uri"]
    rig.provider.stop(cleanup, fade_out_ms=0)                        # idempotent
    assert len([c for c in rig.http.calls if c.method == "PUT"]) == len(rig.http.puts())


def test_parked_clean_up_pauses_a_late_start_and_respects_new_music(tmp_path):
    rig = make_rig(tmp_path / "late")
    _stuck_routes(rig, verify_state=player_state(DROP_TRACK, 0))
    with pytest.raises(sp.SpotifyStuckError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    rig.http.set("GET", "/v1/me/player", (200, player_state(DROP_TRACK, 9_000)))  # it started after all
    before = len(rig.http.calls)
    rig.provider.stop(ei.value.cleanup, fade_out_ms=0)
    assert [c.path for c in rig.http.calls[before:] if c.method == "PUT"] == ["/v1/me/player/pause"]

    rig = make_rig(tmp_path / "user")
    _stuck_routes(rig, verify_state=player_state(DROP_TRACK, 0))
    with pytest.raises(sp.SpotifyStuckError) as ei:
        rig.provider.play(make_drop(), fade_in_ms=0)
    rig.http.set("GET", "/v1/me/player", (200, player_state(PREV, 1_000, device=PHONE)))  # the player moved on
    before = len(rig.http.calls)
    rig.provider.stop(ei.value.cleanup, fade_out_ms=0)
    assert [c for c in rig.http.calls[before:] if c.method == "PUT"] == []


# ============================================================ #5 / token save loss ===

def _cross_device(*_a, **_k):
    e = OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
    e.winerror = 17
    raise e


def test_token_save_survives_winerror_17(tmp_path, monkeypatch):
    """Real client: os.replace inside %APPDATA%\\NeedleDropper raised [WinError 17] under a packaged host
    and the fresh sign-in was thrown away. Now the file is written in place."""
    monkeypatch.setattr(os, "replace", _cross_device)
    store = sa.TokenStore(tmp_path)
    assert store.save({"access_token": "AT", "refresh_token": "RT", "expires_at": 1.0, "client_id": "cid"})
    assert store.save_error is None
    assert nd_secrets.load_secret(store.path)["refresh_token"] == "RT"
    assert [p.name for p in tmp_path.iterdir()] == [sa.TOKEN_FILE]   # no temp file left behind


def test_refreshed_tokens_stay_live_when_the_disk_refuses_them(tmp_path, monkeypatch):
    """SPOT-TOKEN-SAVE-LOSS: a failed write threw away the rotated refresh token and leaked a raw OSError
    out of the provider's API calls."""
    clock = FakeClock()
    save_tokens(tmp_path, expires_at=time.time() - 10)              # expired: the first call refreshes
    refreshes: list[str] = []

    def token_endpoint(call):
        refreshes.append(call.form["refresh_token"])
        n = len(refreshes)
        return (200, {"access_token": f"AT{n + 1}", "refresh_token": f"RT{n + 1}", "expires_in": 3600,
                      "token_type": "Bearer", "scope": " ".join(sa.SCOPES)})

    fake = FakeHTTP()
    fake.add("POST", "/api/token", token_endpoint)
    fake.add("GET", "/v1/me/player/devices", (200, {"devices": [PC]}))
    real_save = nd_secrets.save_secret
    monkeypatch.setattr(nd_secrets, "save_secret", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "locked")))
    store = sa.TokenStore(tmp_path, clock=clock)
    provider = sp.SpotifyProvider({"clientId": "cid"}, SimpleNamespace(app_dir=tmp_path), http=fake,
                                  sleep=clock.sleep, clock=clock, token_store=store)
    assert provider.list_devices()[0]["id"] == "dev-pc"              # no raw PermissionError escapes
    assert refreshes == ["RT1"] and fake.find("GET", "/v1/me/player/devices")[0].bearer == "Bearer AT2"
    assert store.save_error and "locked" in store.save_error
    assert provider.status_dict()["saveError"] == store.save_error
    # another store of the same file (the Connect page's account check) sees the live tokens too
    assert sa.TokenStore(tmp_path, clock=clock).load()["refresh_token"] == "RT2"
    # the disk works again: the next load after the retry interval writes the kept tokens
    monkeypatch.setattr(nd_secrets, "save_secret", real_save)
    clock.advance(sa.SAVE_RETRY_S + 1)
    assert store.load()["refresh_token"] == "RT2" and store.save_error is None
    assert nd_secrets.load_secret(store.path)["refresh_token"] == "RT2"


def test_panel_sign_in_succeeds_with_a_warning_when_the_store_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(nd_secrets, "save_secret", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "nope")))
    fake = FakeHTTP()
    fake.add("POST", "/api/token", (200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
                                          "token_type": "Bearer", "scope": " ".join(sa.SCOPES)}))
    fake.add("GET", "/v1/me", (200, {"display_name": "Thrall", "product": "premium", "country": "DE"}))
    session = sa.start_login({"clientId": "cid", "redirectPort": 0}, SimpleNamespace(app_dir=tmp_path), http=fake,
                             port=0, timeout=10)
    import http.client
    from urllib.parse import parse_qs, urlsplit

    q = {k: v[0] for k, v in parse_qs(urlsplit(session.authorize_url).query).items()}
    port = urlsplit(session.redirect_uri).port
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", f"/callback?code=C&state={q['state']}")
    conn.getresponse().read()
    conn.close()
    assert session.wait(10) == "connected"
    assert session.save_error and "refused to save" in session.warning
    d = session.to_dict()
    assert d["product"] == "premium" and d["country"] == "DE" and d["saveError"]
    assert sa.TokenStore(tmp_path).load()["refresh_token"] == "RT"  # kept in memory for the provider
    assert sa.TokenStore(tmp_path).has_refresh_token()
