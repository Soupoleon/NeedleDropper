"""Bundled resources (source checkout vs. PyInstaller exe), config migration (v1.1 and the v1.2
keys: startWithWindows, updates, links, the logs folder), and the Apple Music removal (SPEC-v1.1 A)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import __version__, config, resources  # noqa: E402
from needledropper.deck.server import Deck  # noqa: E402

COMPANION = ROOT / "companion"


# --------------------------------------------------------------- resources --

def test_source_checkout_layout():
    assert resources.is_frozen() is False
    assert resources.repo_root() == ROOT
    assert resources.static_dir() == COMPANION / "needledropper" / "deck" / "static"
    for name in ("connect.html", "connect.js", "connect.css", "player.html", "player.js", "player.css",
                 "favicon.svg"):
        assert (resources.static_dir() / name).is_file(), name
    assert not list(resources.static_dir().glob("panel.*"))  # the v1.1 control panel is gone
    assert resources.assets_dir() == COMPANION / "needledropper" / "assets"
    assert resources.tray_icon_path().is_file() and resources.tray_icon_path(True).is_file()
    from needledropper import tray

    assert tray.ICON_PATH == resources.tray_icon_path() and tray.PAUSED_ICON_PATH == resources.tray_icon_path(True)
    assert resources.packs_dir() == ROOT / "packs"
    assert resources.default_pack_path() == ROOT / "packs" / "azeroth-unbound.json"
    assert resources.default_pack_path().is_file()
    assert resources.addon_source_dir() == ROOT / "addon" / "NeedleDropper"
    assert (resources.addon_source_dir() / "NeedleDropper.toc").is_file()
    assert resources.app_version() == __version__
    # config and the deck use the same locations
    assert config.DEFAULT_PACK == resources.default_pack_path()
    assert config.REPO_ADDON_DIR == resources.addon_source_dir()
    assert Deck().static_dir == resources.static_dir()


def test_frozen_layout(monkeypatch, tmp_path):
    bundle = tmp_path / "_MEI12345"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert resources.is_frozen() is True
    assert resources.static_dir() == bundle / "needledropper" / "deck" / "static"
    assert resources.packs_dir() == bundle / "packs"
    assert resources.default_pack_path() == bundle / "packs" / "azeroth-unbound.json"
    assert resources.addon_source_dir() == bundle / "addon" / "NeedleDropper"
    assert resources.assets_dir() == bundle / "needledropper" / "assets"
    assert resources.tray_icon_path(True) == bundle / "needledropper" / "assets" / "NeedleDropper-paused.ico"
    assert resources.app_version() == __version__


def test_bundled_packs_are_stored_portably(monkeypatch, tmp_path):
    """The exe unpacks to a new temp folder every run: config.json must not pin that folder."""
    bundle = tmp_path / "_MEI777"
    (bundle / "packs").mkdir(parents=True)
    (bundle / "packs" / "azeroth-unbound.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert config.portable_pack_entry(bundle / "packs" / "azeroth-unbound.json") == "builtin:azeroth-unbound"
    assert config.portable_pack_entry("builtin:azeroth-unbound.json") == "builtin:azeroth-unbound"
    assert config.portable_pack_entry(tmp_path / "mine.json") == str(tmp_path / "mine.json")
    assert config.expand_pack_entry("builtin:azeroth-unbound") == bundle / "packs" / "azeroth-unbound.json"
    assert config.expand_pack_entry("BUILTIN:azeroth-unbound.json") == bundle / "packs" / "azeroth-unbound.json"
    stale = tmp_path / "_MEI111" / "packs" / "azeroth-unbound.json"  # an older run's unpack folder
    assert config.expand_pack_entry(str(stale)) == bundle / "packs" / "azeroth-unbound.json"
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {"packs": ["builtin:azeroth-unbound", str(stale)]})
    assert config.pack_paths_from_config(cfg) == [bundle / "packs" / "azeroth-unbound.json"] * 2
    assert config.same_pack("builtin:azeroth-unbound", str(bundle / "packs" / "azeroth-unbound.json"))


def test_frozen_deck_serves_the_bundled_pages(monkeypatch, tmp_path):
    bundle = tmp_path / "_MEI999"
    static = bundle / "needledropper" / "deck" / "static"
    static.mkdir(parents=True)
    (static / "connect.html").write_text('<meta name="nd-token" content="{{ND_TOKEN}}">bundled', encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    import http.client

    with Deck(port=0, static_dir=resources.static_dir()) as d:
        conn = http.client.HTTPConnection("127.0.0.1", d.port, timeout=5)
        conn.request("GET", "/")
        body = conn.getresponse().read().decode("utf-8")
        conn.close()
    assert body == f'<meta name="nd-token" content="{d.token}">bundled'


# --------------------------------------------------------------- migration --

@pytest.mark.parametrize("old, new", [(True, "youtube"), (False, "never"), ("ALWAYS", "always"),
                                      ("never", "never"), ("sometimes", "youtube"), (None, "youtube")])
def test_auto_open_migration(old, new):
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {"deck": {"autoOpen": old}})
    assert config.normalize_config(cfg)["deck"]["autoOpen"] == new


def test_load_config_normalises_and_keeps_unknown_keys(tmp_path):
    p = config.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"deck": {"autoOpen": True, "panelOnStart": "weekly"},
                             "providers": ["spotify", "applemusic", "youtube"],
                             "applemusic": {"enabled": True}, "transport": {"chatlog": {"match": "loose"}},
                             "futureFeature": {"x": 1}}), encoding="utf-8")
    cfg = config.load_config()
    assert cfg["deck"]["autoOpen"] == "youtube" and cfg["deck"]["panelOnStart"] == "first-run"
    assert cfg["providers"] == ["spotify", "youtube"]
    assert cfg["transport"]["chatlog"]["match"] == "strict"
    assert cfg["futureFeature"] == {"x": 1} and cfg["setupComplete"] is False
    config.update_config_file({"setupComplete": True})
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["futureFeature"] == {"x": 1} and raw["applemusic"] == {"enabled": True}  # preserved as written
    assert raw["setupComplete"] is True
    assert config.load_config(p)["setupComplete"] is True


def test_v12_keys_are_normalised(tmp_path):
    defaults = config.default_config()
    assert defaults["startWithWindows"] is None
    assert defaults["updates"] == {"releasesUrl": "", "checkHours": 24}
    assert defaults["links"]["addonPage"].startswith("https://www.curseforge.com/")
    assert config.normalize_config(config.default_config()) == defaults  # the defaults are a fixed point
    odd = config.deep_merge(config.DEFAULT_CONFIG, {"startWithWindows": "yes", "updates": {"releasesUrl": 5,
                                                    "checkHours": 0.1}, "links": {"addonPage": "javascript:x"}})
    cfg = config.normalize_config(odd)
    assert cfg["startWithWindows"] is None and cfg["updates"] == {"releasesUrl": "", "checkHours": 1}
    assert cfg["links"]["addonPage"] == defaults["links"]["addonPage"]
    cfg = config.normalize_config(config.deep_merge(config.DEFAULT_CONFIG, {
        "startWithWindows": True, "updates": {"releasesUrl": "  https://github.com/o/r  ", "checkHours": 10 ** 6}}))
    assert cfg["startWithWindows"] is True and cfg["updates"]["releasesUrl"] == "https://github.com/o/r"
    assert cfg["updates"]["checkHours"] == config.UPDATE_CHECK_HOURS_RANGE[1]


def test_paths_put_the_log_in_its_own_folder(tmp_path):
    paths = config.paths_from_config(config.default_config())
    assert paths.logs_dir == config.app_dir() / "logs"
    assert paths.log_file == config.app_dir() / "logs" / "needledropper.log"
    assert paths.install_receipt == config.app_dir() / "addon-install.json"
    assert paths.update_state == config.app_dir() / "update-check.json"


def test_addon_toc_and_version_helpers(tmp_path):
    d = tmp_path / "NeedleDropper"
    d.mkdir()
    assert config.addon_toc(d) is None
    toc_text = "﻿## Interface: 16001\n## Version: v1.2\n## X-Wago-ID: abc\n"
    (d / "NeedleDropper.toc").write_text(toc_text, encoding="utf-8")
    assert config.addon_toc(d) == d / "NeedleDropper.toc"
    assert config.read_toc_fields(d / "NeedleDropper.toc") == {"Interface": "16001", "Version": "v1.2",
                                                               "X-Wago-ID": "abc"}
    for name in ("NeedleDropper_Mainline.toc", "NeedleDropper_Camelot.toc", "NeedleDropper_Forever.toc"):
        (d / name).write_text("## Version: 1\n", encoding="utf-8")
        assert config.addon_toc(d) == d / name  # more specific game types win
    assert config.version_key("v1.2") == (1, 2, 0, 0) and config.version_key("1.10.3") > config.version_key("1.9.9")
    for bad in ("@project-version@", "", None, "beta"):
        assert config.version_key(bad) is None
    assert config.version_key("1.2.0-beta1") == (1, 2, 0, 0)


def test_parse_position():
    assert config.parse_position("1500,40") == (1500, 40)
    assert config.parse_position(" -10 , 5 ") == (-10, 5)
    for bad in ("", None, "left", "1,2,3", "1.5,2", 7):
        assert config.parse_position(bad) is None


# ------------------------------------------------------------ Apple Music --

def test_apple_music_is_gone_from_the_companion():
    """Apple Music is out of v1: no provider, config, CLI or UI mentions it. (Packs may still carry
    an ignored camelCase "appleMusic" field, which this case-sensitive check allows.)"""
    assert not (COMPANION / "needledropper" / "providers" / "applemusic.py").exists()
    offenders = []
    pattern = re.compile(r"applemusic|AppleMusic")
    for path in COMPANION.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert offenders == []
    assert "applemusic" not in config.KNOWN_PROVIDERS and "applemusic" not in config.DEFAULT_CONFIG
    assert config.DEFAULT_CONFIG["providers"] == ["spotify", "local", "youtube"]
    from needledropper import cli

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["test-drop", "x", "--provider", "applemusic"])
