"""The Connect page API end to end (real HTTP, real Companion; fake engine, transports, registry
and WoW folder): the state it shows, the allowed config keys, the retired soundtrack endpoints
(404), WoW detection, the "Install addon" fallback and who owns the addon folder, opening the
log folder, and that no response ever contains a secret."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import install_v11_addon, make_shell, raw_config  # noqa: E402

from needledropper import autostart, config  # noqa: E402
from needledropper.panel_api import SCHEMA, PatchError, should_open_panel, validate_config_patch  # noqa: E402
from needledropper.providers.spotify_auth import TokenStore  # noqa: E402


@pytest.fixture
def shell(tmp_path, _needledropper_home):
    ns = make_shell(tmp_path, Path(_needledropper_home))
    try:
        yield ns
    finally:
        ns.close()


# ------------------------------------------------------------------ state --

def test_state_shows_the_four_cards_and_no_secrets(shell):
    TokenStore(shell.home).save({"access_token": "SECRET-ACCESS-TOKEN", "refresh_token": "SECRET-REFRESH-TOKEN",
                                 "expires_at": time.time() + 3600, "scope": "", "client_id": "abcdef0123456789"})
    st, data = shell.call("GET", "/api/state")
    assert st == 200
    # 1.2.1 adds "music": the choices set in game and the last request results (DESIGN-v1.2.1 3.12)
    assert set(data) == {"app", "wow", "addon", "spotify", "youtube", "link", "autostart", "update", "faults",
                         "playing", "music"}
    assert data["music"]["order"] == "spotify-youtube" and data["music"]["fromGame"] is False
    assert data["wow"]["found"] and data["wow"]["exe"] and not data["wow"]["running"]
    assert data["wow"]["source"] == "config" and data["wow"]["folder"].endswith("_forever_")
    assert data["addon"]["installed"] is False and data["addon"]["action"] == "install"
    assert data["addon"]["bundledVersion"] == "1.2.0" and data["addon"]["pageUrl"].startswith("https://")
    assert data["spotify"]["redirectUri"] == "http://127.0.0.1:8321/callback"
    assert data["spotify"]["state"] == "not-set-up" and data["spotify"]["premium"] == "unknown"
    assert data["youtube"]["enabled"] is True
    assert data["link"]["heard"] is False and data["link"]["listening"] is True
    assert set(data["link"]["transports"]) == {"pixel", "chatlog"}
    assert data["autostart"] == {"available": True, "enabled": False, "choice": None}
    assert data["update"]["enabled"] is False and data["faults"] == []
    assert data["app"]["firstRun"] is True and data["app"]["setupComplete"] is False
    assert data["app"]["logFile"].endswith(str(Path("logs") / "needledropper.log"))
    text = json.dumps(data)
    for secret in ("SECRET-ACCESS-TOKEN", "SECRET-REFRESH-TOKEN", shell.deck.token):
        assert secret not in text
    # v1.1 soundtrack sections are gone
    for retired in ("soundtrack", "advanced", "services", "local", "results"):
        assert retired not in data


def test_the_companion_creates_the_data_addon_before_the_game_starts(shell):
    data_addon = shell.wow / "_forever_" / "Interface" / "AddOns" / "NeedleDropper_Companion"
    assert (data_addon / "NeedleDropper_Companion.toc").is_file() and (data_addon / "Data.lua").is_file()
    assert shell.call("GET", "/api/state")[1]["addon"]["companionData"] is True


# ----------------------------------------------------------------- config --

@pytest.mark.parametrize("patch, field", [
    ({"nope": 1}, "nope"),
    ({"packs": ["x.json"]}, "packs"),  # soundtrack state is not the companion's any more
    ({"fadeInMs": 100}, "fadeInMs"),  # advanced knobs stay in config.json
    ({"deck": {"autoOpen": "never"}}, "deck"),
    ({"transport": {"pixel": {"enabled": False}}}, "transport"),
    ({"providers": ["youtube"]}, "providers"),
    ({"updates": {"releasesUrl": "https://example.invalid"}}, "updates"),
    ({"spotify": {"redirectPort": 9000}}, "spotify.redirectPort"),
    ({"spotify": {"clientId": "has spaces!"}}, "spotify.clientId"),
    ({"setupComplete": "yes"}, "setupComplete"),
    ({"startWithWindows": 1}, "startWithWindows"),
    ({"youtube": {"enabled": "on"}}, "youtube.enabled"),
    ({"wowRoot": ""}, "wowRoot"),
    ({"wowRoot": "C:\\bad\x07path"}, "wowRoot"),
])
def test_config_patch_allows_only_the_connect_page_keys(shell, patch, field):
    before = raw_config(shell.cfg_file)
    st, data = shell.call("POST", "/api/config", patch)
    assert st == 400 and field in data["fields"], data
    assert raw_config(shell.cfg_file) == before


def test_schema_is_exactly_the_connect_page(shell):
    assert set(SCHEMA) == {"wowRoot", "setupComplete", "startWithWindows", "spotify", "youtube"}
    # 1.2.1: the Connect page also picks where Spotify plays (spotify.device, D6)
    assert set(SCHEMA["spotify"]) == {"enabled", "clientId", "deviceName", "device"}
    assert validate_config_patch({"spotify": {"deviceName": "  PC  "}}) == {"spotify": {"deviceName": "PC"}}
    assert validate_config_patch({"spotify": {"device": "Connect"}}) == {"spotify": {"device": "connect"}}
    with pytest.raises(PatchError):
        validate_config_patch({"spotify": {"device": "desktop"}})
    with pytest.raises(PatchError) as exc:
        validate_config_patch({"a": 1, "b": 2})
    assert set(exc.value.fields) == {"a", "b"}


def test_config_patch_applies_live_and_keeps_unknown_keys(shell):
    first = shell.engines.current
    st, data = shell.call("POST", "/api/config", {"youtube": {"enabled": False}})
    assert st == 200 and data["ok"] and data["state"]["youtube"]["enabled"] is False
    raw = raw_config(shell.cfg_file)
    assert raw["youtube"] == {"enabled": False} and raw["myOwnKey"] == {"keep": True}
    # the fake engine cannot swap providers live, so the companion built a new one without YouTube
    assert first.stopped and shell.engines.current is not first
    assert "youtube" not in [p.name for p in shell.engines.current.providers]
    st, data = shell.call("POST", "/api/config", {"youtube": {"enabled": True}, "spotify": {"deviceName": "PC"}})
    assert st == 200 and "youtube" in [p.name for p in shell.engines.current.providers]
    assert raw_config(shell.cfg_file)["spotify"] == {"deviceName": "PC"}


def test_switching_a_service_on_puts_it_back_in_the_provider_order(shell):
    config.update_config_file({"providers": ["spotify"]}, shell.cfg_file)
    shell.app._write_config({})
    st, _ = shell.call("POST", "/api/config", {"youtube": {"enabled": True}})
    assert st == 200 and raw_config(shell.cfg_file)["providers"] == ["spotify", "youtube"]


def test_a_new_wow_folder_is_checked(shell, tmp_path):
    st, data = shell.call("POST", "/api/config", {"wowRoot": str(tmp_path / "Elsewhere")})
    assert st == 200 and data["warnings"] and "_forever_" in data["warnings"][0]
    assert data["state"]["wow"]["found"] is False and data["state"]["addon"]["action"] is None


def test_first_run_until_done_and_should_open_panel():
    base = config.default_config()
    assert should_open_panel(base)
    assert should_open_panel({**base, "setupComplete": True})  # a v1.1 config: no Start with Windows choice yet
    assert not should_open_panel({**base, "setupComplete": True, "startWithWindows": False})
    assert should_open_panel({**base, "deck": {"panelOnStart": "always"}, "setupComplete": True,
                              "startWithWindows": True})
    assert not should_open_panel({**base, "deck": {"panelOnStart": "never"}})


# ---------------------------------------------------------------- retired --

@pytest.mark.parametrize("method, path", [
    ("POST", "/api/test"), ("POST", "/api/stop"), ("POST", "/api/map/build"), ("POST", "/api/spotify/resolve"),
    ("GET", "/api/packs"), ("POST", "/api/packs"), ("GET", "/api/merged"), ("GET", "/api/marks"),
    ("GET", "/api/media"), ("GET", "/api/soundtrack"), ("GET", "/api/drops"),
])
def test_retired_soundtrack_endpoints_are_gone(shell, method, path):
    st, data = shell.call(method, path, {} if method == "POST" else None)
    assert st == 404 and data == {"error": "no such API"}


def test_unknown_methods_and_bodies(shell):
    assert shell.call("GET", "/api/config")[0] == 405
    assert shell.call("POST", "/api/state", {})[0] == 405
    st, data = shell.call("POST", "/api/config", raw=b"[1, 2]")
    assert st == 400 and "JSON object" in data["error"]


# -------------------------------------------------------------------- wow --

def test_wow_detect_with_a_fake_registry(shell, tmp_path):
    other = tmp_path / "Games" / "World of Warcraft"
    (other / "_forever_").mkdir(parents=True)
    (other / "_forever_" / "Wow.exe").write_bytes(b"MZ")
    shell.app.wow_registry = lambda hive, key, name: str(other / "_forever_") + "\\" if hive == "HKLM" else None
    st, data = shell.call("POST", "/api/wow/detect", {})
    assert st == 200 and data["found"] and data["changed"] and data["source"] == "registry"
    assert raw_config(shell.cfg_file)["wowRoot"] == str(other)
    assert shell.call("GET", "/api/state")[1]["wow"]["source"] == "registry"
    shell.app.wow_registry = lambda *a: None
    shell.app.wow_default = str(tmp_path / "nothing")
    config.update_config_file({"wowRoot": str(tmp_path / "gone")}, shell.cfg_file)
    shell.app._write_config({})
    st, data = shell.call("POST", "/api/wow/detect", {})
    assert st == 200 and not data["found"] and data["tried"]


def test_first_start_finds_wow_in_the_registry(tmp_path, _needledropper_home):
    reg_root = tmp_path / "Registry WoW"
    (reg_root / "_forever_" / "Interface" / "AddOns").mkdir(parents=True)
    (reg_root / "_forever_" / "Wow.exe").write_bytes(b"MZ")
    ns = make_shell(tmp_path, Path(_needledropper_home), wow=False,
                    wow_registry=lambda hive, key, name: str(reg_root) if hive == "HKLM" else None)
    try:
        assert raw_config(ns.cfg_file)["wowRoot"] == str(reg_root)
        st, data = ns.call("GET", "/api/state")
        assert data["wow"]["found"] and data["wow"]["source"] == "registry"
    finally:
        ns.close()


# ------------------------------------------------------------ addon install --

def addon_dir(shell) -> Path:
    return shell.wow / "_forever_" / "Interface" / "AddOns" / "NeedleDropper"


def test_install_addon_copies_the_bundled_addon_with_its_sets(shell):
    st, data = shell.call("POST", "/api/addon/install", {})
    assert st == 200 and data["ok"] and data["version"] == "1.2.0", data
    dest = addon_dir(shell)
    assert (dest / "NeedleDropper.toc").is_file() and (dest / "Sets" / "AzerothMixtape.lua").is_file()
    assert not (dest / "__pycache__").exists() and not (dest / "notes.bak").exists()
    assert (shell.wow / "_forever_" / "Interface" / "AddOns" / "NeedleDropper_Local" / "media").is_dir()
    receipt = config.read_install_receipt(shell.app.paths)
    assert receipt["version"] == "1.2.0" and "Sets/AzerothMixtape.lua" in receipt["files"]
    st, state = shell.call("GET", "/api/state")
    a = state["addon"]
    assert a["installed"] and a["version"] == "1.2.0" and a["owner"] == "companion"
    assert a["action"] is None and not a["updateAvailable"]  # nothing newer bundled
    assert not (shell.home / "maps").exists()  # no map registry any more


def test_install_refuses_while_the_game_runs_unless_forced(shell):
    shell.game["v"] = True
    shell.app._game_cache = None
    st, data = shell.call("POST", "/api/addon/install", {})
    assert st == 409 and data["gameRunning"] and "restart" in data["error"]
    assert not addon_dir(shell).exists()
    st, data = shell.call("POST", "/api/addon/install", {"force": True})
    assert st == 200 and data["gameRunning"] and "restart the game" in data["message"]
    assert shell.call("POST", "/api/addon/install", {"force": "yes"})[0] == 400


def test_a_newer_bundled_addon_updates_our_own_install_and_removes_stale_files(shell, tmp_path):
    shell.call("POST", "/api/addon/install", {})
    dest = addon_dir(shell)
    newer = make_fake_addon_version(tmp_path, "1.2.5")
    (newer / "Sets" / "AzerothMixtape.lua").unlink()
    (newer / "Sets" / "Other.lua").write_text("local ADDON, ns = ...\n", encoding="utf-8")
    shell.app._addon_source = newer
    st, state = shell.call("GET", "/api/state")
    assert state["addon"]["action"] == "update" and state["addon"]["updateAvailable"]
    st, data = shell.call("POST", "/api/addon/install", {})
    assert st == 200 and data["version"] == "1.2.5" and "Sets/AzerothMixtape.lua" in data["removed"]
    assert (dest / "Sets" / "Other.lua").is_file() and not (dest / "Sets" / "AzerothMixtape.lua").exists()


def make_fake_addon_version(tmp_path: Path, version: str) -> Path:
    from test_panel_fakes import make_fake_addon

    return make_fake_addon(tmp_path, version, name=f"addon_{version}")


def test_a_v11_addon_from_the_old_companion_is_updated_and_its_map_removed(shell):
    install_v11_addon(addon_dir(shell))
    st, state = shell.call("GET", "/api/state")
    a = state["addon"]
    assert a["owner"] == "companion" and a["legacy"] and a["action"] == "update"
    st, data = shell.call("POST", "/api/addon/install", {})
    assert st == 200 and {"Map.lua", "Map.json", "Protocol.lua"} <= set(data["removed"])
    assert not (addon_dir(shell) / "Map.lua").exists() and (addon_dir(shell) / "Core.lua").is_file()


@pytest.mark.parametrize("how", ["wowup", "curseforge-ids", "by-hand"])
def test_never_overwrites_an_addon_someone_else_installed(shell, how, tmp_path):
    dest = addon_dir(shell)
    dest.mkdir(parents=True)
    toc = "## Interface: 16001\n## Version: 1.2.0\nCore.lua\n"
    if how == "curseforge-ids":
        toc = "## Interface: 16001\n## Version: 1.2.0\n## X-Curse-Project-ID: 123456\nCore.lua\n"
    (dest / "NeedleDropper.toc").write_text(toc, encoding="utf-8")
    (dest / "Core.lua").write_text("-- installed elsewhere\n", encoding="utf-8")
    if how == "wowup":
        (dest.parent / "wowup_data_addon").mkdir()
    shell.app._addon_source = make_fake_addon_version(tmp_path, "9.9.9")
    st, state = shell.call("GET", "/api/state")
    a = state["addon"]
    assert a["installed"] and a["action"] is None and not a["canInstall"]
    # A TOC project id no longer counts as a manager's mark: NeedleDropper's own release TOC carries
    # its CurseForge id now (journey review F8), so a hand-unpacked zip has it as well. The folder is
    # still someone else's ("other": never overwritten, no install offered). This used to expect
    # owner "manager" / managedBy "CurseForge" for the curseforge-ids case.
    assert a["owner"] == ("manager" if how == "wowup" else "other")
    assert a["managedBy"] == {"wowup": "WowUp", "curseforge-ids": None, "by-hand": None}[how]
    st, data = shell.call("POST", "/api/addon/install", {"force": True})
    assert st == 409 and data["managed"]
    assert (dest / "Core.lua").read_text(encoding="utf-8") == "-- installed elsewhere\n"


def test_no_install_button_when_an_addon_manager_runs_this_wow(shell):
    (shell.wow / "_forever_" / "Interface" / "AddOns" / "wowup_data_addon").mkdir()
    st, state = shell.call("GET", "/api/state")
    a = state["addon"]
    assert not a["installed"] and a["managers"] == ["WowUp"] and a["action"] is None
    st, data = shell.call("POST", "/api/addon/install", {})
    assert st == 409 and "WowUp" in data["error"] and not addon_dir(shell).exists()


def test_forever_toc_names_win(shell):
    dest = addon_dir(shell)
    dest.mkdir(parents=True)
    (dest / "NeedleDropper.toc").write_text("## Version: 1.0.0\n", encoding="utf-8")
    (dest / "NeedleDropper_Mainline.toc").write_text("## Version: 1.1.0\n", encoding="utf-8")
    (dest / "NeedleDropper_Camelot.toc").write_text("## Version: 1.2.0\n", encoding="utf-8")
    assert shell.call("GET", "/api/state")[1]["addon"]["toc"] == "NeedleDropper_Camelot.toc"
    (dest / "NeedleDropper_Forever.toc").write_text("## Version: 1.2.1\n", encoding="utf-8")
    st, state = shell.call("GET", "/api/state")
    assert state["addon"]["toc"] == "NeedleDropper_Forever.toc" and state["addon"]["version"] == "1.2.1"


def test_install_without_wow(tmp_path, _needledropper_home):
    ns = make_shell(tmp_path, Path(_needledropper_home), wow=False)
    try:
        st, data = ns.call("POST", "/api/addon/install", {})
        assert st == 400 and "was not found" in data["error"]
    finally:
        ns.close()


# ------------------------------------------------------------- open, misc --

def test_open_accepts_only_the_log_folder(shell):
    for what in ("addons", "media", "config", "C:\\Windows", None):
        st, data = shell.call("POST", "/api/open", {"what": what})
        assert st == 400 and "logs" in data["error"]
    st, data = shell.call("POST", "/api/open", {"what": "logs"})
    assert st == 200 and shell.opened == [str(shell.home / "logs")] and Path(data["path"]).is_dir()


def test_start_with_windows_from_the_page_uses_the_registry(shell):
    st, data = shell.call("POST", "/api/config", {"startWithWindows": True})
    assert st == 200 and data["state"]["autostart"]["enabled"] is True
    command = autostart.get_command(winreg=shell.reg)
    assert command is not None and command.endswith("--background")
    st, data = shell.call("POST", "/api/config", {"startWithWindows": False})
    assert data["state"]["autostart"]["enabled"] is False and autostart.get_command(winreg=shell.reg) is None
    assert raw_config(shell.cfg_file)["startWithWindows"] is False
