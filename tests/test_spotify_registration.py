"""Regression coverage for the SDK's one-registration-per-page limitation (beta 1)."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_spotify_fakes import FakeHTTP, SCOPES, V11_SCOPE, make_drop
from test_spotify_inapp import PageDeck, SDK_DEVICE, STATIC, fast, make_app_rig
from needledropper.providers import spotify as sp
from needledropper.providers.base import ProviderError


def test_a_404_reloads_then_retries_once_on_a_listed_device(tmp_path, fast, caplog):
    caplog.set_level(logging.INFO)
    rig = make_app_rig(tmp_path, cfg={"restorePrevious": False})
    first = True

    def play(call):
        nonlocal first
        if first:
            first = False
            rig.page.registered.clear()
        if call.query["device_id"] not in rig.page.registered:
            return 404, {"error": {"status": 404, "message": "Device not found"}}
        rig.page.tick(500)
        return 204, None

    rig.http.add("PUT", "/v1/me/player/play", play)
    handle = rig.provider.play(make_drop(), fade_in_ms=0)
    assert handle.data["deviceId"] == "sdk-device-2"
    assert [c.query["device_id"] for c in rig.http.puts()] == [SDK_DEVICE, "sdk-device-2"]
    assert rig.page.ops()[-4:] == ["start", "reload", "connect", "start"]
    assert "Device not found" in caplog.text and "sdk-devi" in caplog.text
    assert "AT1" not in caplog.text and "RT1" not in caplog.text


def test_an_unlisted_ready_at_arm_is_reloaded_and_never_targeted(tmp_path, fast):
    rig = make_app_rig(tmp_path, cfg={"restorePrevious": False})
    rig.page.spotify.command("reconnect")  # model the broken old implementation: same-page Player
    assert rig.page.spotify.device_id.endswith("-unlisted")
    rig.provider.prepare(make_drop())
    assert "reload" in rig.page.ops()
    rig.http.add("PUT", "/v1/me/player/play", lambda c: (rig.page.tick(500), (204, None))[1])
    handle = rig.provider.play(make_drop(), fade_in_ms=0)
    assert handle.data["deviceId"] in rig.page.registered
    assert all("unlisted" not in c.query["device_id"] for c in rig.http.puts())


def test_an_unlisted_ready_does_not_clear_stuck(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    rig.provider._mark_stuck("no progress")
    rig.page.report("ready", deviceId="unlisted-ready")
    assert rig.page.spotify.wait_ready(0) is None
    assert rig.provider._wait_registered(0) is None
    assert rig.provider.stuck()


def test_verification_of_an_old_ready_cannot_verify_a_new_report(tmp_path):
    rig = make_app_rig(tmp_path)
    sdk = rig.page.spotify
    old_seq = sdk.ready_seq
    rig.page.report("ready", deviceId=SDK_DEVICE)
    assert not sdk.verify_device(SDK_DEVICE, old_seq, listed=True)
    assert sdk.wait_ready(0) is None


def test_wait_returns_the_id_that_was_verified_after_a_snapshot_race(tmp_path, monkeypatch):
    rig = make_app_rig(tmp_path)
    sdk = rig.page.spotify
    original = sdk.wait_ready

    def racing_wait(timeout):
        rig.page.report("ready", deviceId="replacement-id")
        sdk.verify_device("replacement-id", sdk.ready_seq, listed=True)
        return original(timeout)

    monkeypatch.setattr(sdk, "wait_ready", racing_wait)
    assert rig.provider._wait_registered(0) == "replacement-id"


def test_failed_check_cannot_reload_a_newer_verified_registration(tmp_path):
    rig = make_app_rig(tmp_path)
    sdk = rig.page.spotify
    old_seq = sdk.ready_seq
    rig.page.report("ready", deviceId="replacement-id")
    sdk.verify_device("replacement-id", sdk.ready_seq, listed=True)
    before = list(rig.page.ops())
    assert not sdk.reconnect(expected_device=SDK_DEVICE, expected_ready_seq=old_seq)
    assert rig.page.ops() == before and sdk.wait_ready(0) == "replacement-id"


def test_registration_lag_beyond_the_old_ready_budget_does_not_trigger_reload(tmp_path):
    listed = threading.Event()
    fake = FakeHTTP().add("GET", "/v1/me/player/devices", lambda call: (200, {
        "devices": [{"id": SDK_DEVICE}] if listed.is_set() else []}))
    rig = make_app_rig(tmp_path, http=fake)
    timer = threading.Timer(1.8, listed.set)
    timer.start()
    try:
        assert rig.provider._wait_registered(sp.APP_REGISTRATION_WAIT_S) == SDK_DEVICE
        assert "reload" not in rig.page.ops()
    finally:
        timer.cancel()
        rig.provider.close()


def test_a_ready_inside_the_background_throttle_is_checked_later(tmp_path):
    rig = make_app_rig(tmp_path)
    p = rig.provider
    p._registration_thread.join(2)
    rig.page.report("ready", deviceId=SDK_DEVICE)
    timer = p._registration_timer
    assert timer is not None  # coalesced, not forgotten until an ARM
    timer.cancel()
    p._last_registration_check = float("-inf")
    timer.function()
    p._registration_thread.join(2)
    assert rig.page.spotify.wait_ready(0) == SDK_DEVICE
    p.close()


def test_disconnect_connect_and_auth_change_each_use_a_new_page(tmp_path, fast):
    rig = make_app_rig(tmp_path)
    sdk = rig.page.spotify
    sdk.disable(owner=rig.provider)
    assert not rig.page.registered
    rig.provider.sync_sdk()
    assert rig.provider._wait_registered(0) == "sdk-device-2"
    rig.provider._store.save({**rig.provider._store.load(), "access_token": "NEW-ACCESS"})
    before = rig.page.ops().count("reload")
    rig.provider.reset_session()
    assert rig.page.ops().count("reload") == before + 1
    assert rig.provider._wait_registered(0) == "sdk-device-3"
    assert sdk.token() == "NEW-ACCESS"


def test_connect_device_selection_excludes_our_player_unless_explicit(caplog):
    caplog.set_level(logging.INFO)
    ours = {"id": "ours", "name": "NeedleDropper", "type": "Computer", "is_active": True}
    pc = {"id": "desktop", "name": "BIG-RED", "type": "Computer"}
    assert sp.pick_device([ours, pc]) == pc
    assert sp.pick_device([ours]) is None
    assert sp.pick_device([ours, pc], "NeedleDropper") == ours
    assert "BIG-RED" in caplog.text


def test_second_404_reaches_the_engines_youtube_fallback(tmp_path, fast):
    from test_v12_engine_flows import AFRICA, SP_ID, YT_ID, Rig
    from needledropper.models import EventType, Flags

    spotify = make_app_rig(tmp_path, cfg={"restorePrevious": False})
    spotify.http.add("PUT", "/v1/me/player/play", (404, {"error": {"message": "Device not found"}}))
    engine = Rig(spotify=spotify.provider)
    engine.send(EventType.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, youtube=YT_ID, **AFRICA)
    assert len(spotify.http.find("PUT", "/v1/me/player/play")) == 2
    assert len(engine.yt.of("play")) == 1 and engine.engine.is_playing


def test_refresh_can_add_sdk_scopes_without_exposing_tokens(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    fake = FakeHTTP().add("POST", "/api/token", (200, {
        "access_token": "NEW-ACCESS", "refresh_token": "NEW-REFRESH", "expires_in": 3600,
        "scope": " ".join(SCOPES)}))
    rig = make_app_rig(tmp_path, http=fake, scope=V11_SCOPE)
    assert rig.provider.needs_relogin()
    rig.provider._tokens.force_refresh()
    assert not rig.provider.needs_relogin()
    rig.provider.sync_sdk()
    assert rig.page.spotify.wanted
    assert "granted scopes changed" in caplog.text and "streaming" in caplog.text
    assert all(secret not in caplog.text for secret in ("NEW-ACCESS", "NEW-REFRESH", "AT1", "RT1"))


def test_player_js_reloads_once_per_minute_and_defers_playing_songs():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to execute the browser lifecycle regression")
    source = (STATIC / "player.js").read_text(encoding="utf-8")
    chunks = [source[source.index("  function " + name):source.index("  function " + following)]
              for name, following in (("spCreate()", "spDrop()"), ("spReload()", "spTrack(s)"),
                                      ("spotifyCmd(cmd)", "stopCmd(cmd)"))]
    harness = r'''
const assert = require("assert");
let now = 100000, reloads = 0, constructions = 0, timer, storage = {}, reports = [];
Date.now = () => now;
const location = {hash: "#k=launch-key", reload: () => {reloads++;}};
const sessionStorage = {getItem: k => storage[k], setItem: (k,v) => {storage[k]=v;}};
const setTimeout = fn => {timer=fn; return 1;};
const clearTimeout = () => {};
const spPost = (ev, rec) => reports.push([ev,rec]);
const spToken = () => {}, spState = () => {};
let current = null; const fades = {audio:null, yt:null};
let sp = {want:true, created:false, player:null, ready:false, reloadTimer:null};
const window = {Spotify: {Player: function () {
  constructions++; this.addListener=()=>{}; this.connect=()=>Promise.resolve(true);
}}};
const spStartSdk = () => spCreate();
FUNCTIONS
spCreate(); assert.equal(constructions, 1);
sp.player = null; spCreate(); assert.equal(constructions, 1); assert.equal(reloads, 1);
spotifyCmd({op:"reconnect"}); assert.equal(reloads, 1);
now += 60000; current = {source:"youtube"}; timer(); assert.equal(reloads, 1);
current = null; sp.current = {playId:"spotify-song"}; timer(); assert.equal(reloads, 1);
sp.current = null; fades.yt = 1; timer(); assert.equal(reloads, 1);
fades.yt = null; timer(); assert.equal(reloads, 2);
now += 60000; sp.pending = {playId:"failed-404"}; spotifyCmd({op:"reload"});
assert.equal(reloads, 3); assert.equal(location.hash, "#k=launch-key");
now += 60000; spotifyCmd({op:"disconnect"}); assert.equal(sp.want, false); assert.equal(reloads, 4);
assert.equal(constructions, 1);
'''.replace("FUNCTIONS", "\n".join(chunks))
    completed = subprocess.run([node, "-"], input=harness, text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
