"""config.py and zones.py."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from needledropper import config, zones
from needledropper.config import Paths


def test_default_config_matches_spec():
    expected = {
        "wowRoot": "C:\\Program Files (x86)\\World of Warcraft", "flavorDir": "_forever_",
        "packs": [str(config.REPO_ROOT / "packs" / "azeroth-unbound.json")],
        "packsDisabled": [],
        "providers": ["spotify", "local", "youtube"],
        "spotify": {"enabled": True, "clientId": "", "redirectPort": 8321, "deviceName": "", "fade": True,
                    "restorePrevious": True, "volume": None},
        "local": {"enabled": True, "mediaDir": "%APPDATA%\\NeedleDropper\\media"},
        "youtube": {"enabled": True},
        # playerCorner: where the YouTube player window shows (real-client #8: top-right by default)
        "deck": {"port": 8322, "autoOpen": "youtube", "browser": "edge", "playerPosition": "",
                 "playerCorner": "topright", "panelOnStart": "first-run"},
        # v1.2: the capture region is the client area's top-left 512 x 48 px (DESIGN-v1.2 2.3)
        "transport": {"pixel": {"enabled": True, "method": "auto", "hz": 4, "regionW": 512, "regionH": 48},
                      "chatlog": {"enabled": True, "pollMs": 250, "match": "strict"}},
        "fadeInMs": 2500, "fadeOutMs": 3000,
        "setupComplete": False,
        # v1.2 shell (C2): three-state Start with Windows, the optional update check, the addon page link
        "startWithWindows": None,
        "updates": {"releasesUrl": "", "checkHours": 24},
        "links": {"addonPage": "https://www.curseforge.com/wow/search?search=NeedleDropper"},
    }
    assert config.DEFAULT_CONFIG == expected
    assert Path(config.DEFAULT_CONFIG["packs"][0]).parent == Path(__file__).resolve().parents[1] / "packs"


def test_app_dir_honours_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEEDLEDROPPER_HOME", str(tmp_path / "home"))
    assert config.app_dir() == tmp_path / "home"
    assert config.config_path() == tmp_path / "home" / "config.json"


def test_expand_path_maps_appdata_app_dir_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NEEDLEDROPPER_HOME", str(tmp_path))
    assert config.expand_path("%APPDATA%\\NeedleDropper\\media") == tmp_path / "media"
    assert config.expand_path("%appdata%/NeedleDropper") == tmp_path
    monkeypatch.setenv("ND_TEST_VAR", str(tmp_path / "x"))
    assert config.expand_path("%ND_TEST_VAR%\\y") == Path(str(tmp_path / "x") + "\\y")
    # unknown vars are left alone
    assert "%ND_NOPE_VAR%" in str(config.expand_path("%ND_NOPE_VAR%\\z"))


def test_load_missing_config_returns_defaults():
    assert not config.config_path().exists()
    assert config.load_config() == config.DEFAULT_CONFIG


def test_deep_merge_and_save_roundtrip(tmp_path):
    p = config.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"spotify": {"clientId": "abc"}, "providers": ["local"], "deck": {"port": 9000}}),
                 encoding="utf-8")
    cfg = config.load_config()
    assert cfg["spotify"]["clientId"] == "abc"
    assert cfg["spotify"]["redirectPort"] == 8321  # default kept
    assert cfg["providers"] == ["local"]  # lists replace
    assert cfg["deck"] == {"port": 9000, "autoOpen": "youtube", "browser": "edge", "playerPosition": "",
                           "playerCorner": "topright", "panelOnStart": "first-run"}
    cfg["fadeInMs"] = 100
    config.save_config(cfg)
    again = config.load_config()
    assert again["fadeInMs"] == 100 and again["spotify"]["clientId"] == "abc"
    # defaults are never mutated
    assert config.DEFAULT_CONFIG["spotify"]["clientId"] == ""


def test_bad_config_reports_location():
    p = config.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"a": 1,}', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="line 1"):
        config.load_config()


def test_paths_from_config(tmp_path):
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {"wowRoot": str(tmp_path / "WoW")})
    p = config.paths_from_config(cfg)
    fl = tmp_path / "WoW" / "_forever_"
    assert p.wow_root == tmp_path / "WoW"
    assert p.flavor_root == fl
    assert p.addons_dir == fl / "Interface" / "AddOns"
    assert p.addon_dir == fl / "Interface" / "AddOns" / "NeedleDropper"
    assert p.local_addon_dir == fl / "Interface" / "AddOns" / "NeedleDropper_Local"
    assert p.chat_log == fl / "Logs" / "WoWChatLog.txt"
    assert p.game_exe == fl / "Wow.exe"
    assert p.app_dir == config.app_dir()
    assert p.cache_dir == p.app_dir / "cache" and p.maps_dir == p.app_dir / "maps"
    assert p.resolved_cache == p.cache_dir / "resolved.json"
    assert p.token_file.name == "spotify_token.bin"


def test_default_paths_point_at_forever_install():
    p = config.paths_from_config(config.DEFAULT_CONFIG)
    assert str(p.game_exe).lower().endswith(os.path.join("_forever_", "wow.exe").lower())


def test_is_game_running_matches_flavor_exe(tmp_path):
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {"wowRoot": str(tmp_path / "WoW")})
    p = config.paths_from_config(cfg)
    exe = str(p.game_exe)
    assert config.is_game_running(p, processes=[("explorer.exe", None), ("Wow.exe", exe)])
    assert config.is_game_running(p, processes=[("WOW.EXE", exe.upper())])
    retail = str(tmp_path / "WoW" / "_retail_" / "Wow.exe")
    assert not config.is_game_running(p, processes=[("Wow.exe", retail)])
    assert config.is_game_running(p, processes=[("Wow.exe", None)])  # path unknown: conservative
    assert not config.is_game_running(p, processes=[])
    assert not config.is_game_running(p, processes=[("notepad.exe", None)])


def test_atomic_write_retries_permission_error(tmp_path, monkeypatch):
    calls = {"n": 0}
    real = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        return real(src, dst)

    monkeypatch.setattr(config.os, "replace", flaky)
    monkeypatch.setattr(config.time, "sleep", lambda s: None)
    target = tmp_path / "out.txt"
    config.atomic_write_text(target, "hello")
    assert target.read_text() == "hello" and calls["n"] == 3
    assert [x.name for x in tmp_path.iterdir()] == ["out.txt"]  # temp file cleaned


def test_atomic_write_gives_up_after_retries(tmp_path, monkeypatch):
    def locked(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(config.os, "replace", locked)
    monkeypatch.setattr(config.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        config.atomic_write_text(tmp_path / "x.txt", "data")
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ zones --

def test_zone_table_ids():
    assert len({z.id for z in zones.ZONES}) == len(zones.ZONES)
    for zid, name in [(1411, "Durotar"), (1456, "Thunder Bluff"), (2482, "Mount Hyjal"), (2652, "Shen'dralas"),
                      (1429, "Elwynn Forest"), (1453, "Stormwind City"), (2548, "Riverglades"),
                      (2521, "Zephras Isle"), (2524, "Darkspear Islands"), (1459, "Alterac Valley")]:
        assert zones.ZONES_BY_ID[zid].name == name
    assert zones.ZONES_BY_ID[2521].kind == "start"
    assert zones.ZONES_BY_ID[1454].kind == "city"
    assert zones.ZONES_BY_ID[2524].kind == "battleground"
    assert all(z.kind in zones.ZONE_KINDS for z in zones.ZONES)
    assert zones.ZONES_BY_ID[1413].continent == zones.KALIMDOR
    assert zones.ZONES_BY_ID[1434].continent == zones.EASTERN_KINGDOMS
    assert zones.AREAS_BY_ID[279].name == "Dalaran" and zones.AREAS_BY_ID[279].zone == 1416


@pytest.mark.parametrize("query,zid", [
    ("Elwynn Forest", 1429), ("elwynn forest", 1429), ("  ELWYNN   FOREST ", 1429),
    ("The Barrens", 1413), ("barrens", 1413), ("Barrens", 1413),
    ("The Hinterlands", 1425), ("Hinterlands", 1425),
    ("STV", 1434), ("stranglethorn", 1434), ("Stranglethorn Vale", 1434),
    ("Stormwind", 1453), ("Stormwind City", 1453),
    ("Undercity", 1458), ("Thunder Bluff", 1456), ("thunderbluff", 1456),
    ("Shendralas", 2652), ("Shen'dralas", 2652), ("Shen\u2019dralas", 2652),
    ("Un'Goro Crater", 1449), ("Ungoro Crater", 1449), ("un-goro crater", 1449),
    ("Mount Hyjal", 2482), ("Hyjal", 2482), ("Riverglades", 2548), ("Zephras Isle", 2521),
    (1429, 1429), ("1429", 1429), ("zone:1453", 1453),
])
def test_lookup(query, zid):
    z = zones.lookup(query)
    assert z is not None and z.id == zid


@pytest.mark.parametrize("query", ["Narnia", "", None, 999999, True, "Kalimdor"])
def test_lookup_misses(query):
    assert zones.lookup(query) is None


def test_suggest_and_area_lookup():
    assert "Elwynn Forest" in zones.suggest("Elwyn Forrest")
    assert zones.lookup_area("Powderfuse Port").id == 16727
    assert zones.lookup_area(17045).name == "Skywatcher Plateau"
    assert zones.lookup_area("Dalaran", zone_id=1416).id == 279
    assert zones.lookup_area("Dalaran", zone_id=1429) is None


def test_zones_by_continent_grouping():
    g = zones.zones_by_continent()
    assert set(g) == {zones.KALIMDOR, zones.EASTERN_KINGDOMS, zones.ZEPHRAS, zones.BATTLEGROUNDS}
    assert sum(len(v) for v in g.values()) == len(zones.ZONES)
