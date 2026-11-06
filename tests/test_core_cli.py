"""cli.py commands that run without the game: zones, validate, build-map, install-addon, test-drop, run."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from needledropper import cli, config, packs


def write_pack(path: Path, drops, name="CLI Pack") -> Path:
    path.write_text(json.dumps({"format": "needledropper-pack", "version": 1, "name": name, "drops": drops}),
                    encoding="utf-8")
    return path


GOOD = [
    {"zone": "Elwynn Forest", "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 245000},
    # packs may still carry an "appleMusic" link: it is accepted and ignored (Apple Music is not supported)
    {"zone": "Durotar", "title": "D", "artist": "A", "appleMusic": "https://music.apple.com/us/album/x/1?i=2"},
    {"zone": 1429, "subzone": "Goldshire", "areaID": 87, "title": "G", "artist": "A", "file": "g.mp3",
     "inGameFile": True},
]


@pytest.fixture
def home(_needledropper_home):
    return Path(_needledropper_home)


def write_config(home: Path, **cfg) -> Path:
    p = home / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_zones(capsys):
    assert cli.main(["zones"]) == 0
    out = capsys.readouterr().out
    assert "Kalimdor (continent 1414)" in out and "Eastern Kingdoms (continent 1415)" in out
    assert "1429  Elwynn Forest" in out and "2652  Shen'dralas" in out
    assert "Powderfuse Port" in out and "STV" in out
    assert cli.main(["zones", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {"id": 2548, "name": "Riverglades", "continent": "Eastern Kingdoms", "kind": "zone"} in data


def test_validate_ok_and_list(home, tmp_path, capsys):
    pack = write_pack(tmp_path / "p.json", GOOD)
    write_config(home, packs=[str(pack)])
    assert cli.main(["validate", "--list"]) == 0
    out = capsys.readouterr().out
    assert '"CLI Pack": 3 drop(s), 0 error(s)' in out and "Packs are valid." in out
    assert "zone:1429" in out and "area:87" in out


def test_validate_errors_exit_1(home, tmp_path, capsys):
    bad = write_pack(tmp_path / "bad.json", [{"zone": "Elwyn", "title": "t"}], name="Bad")
    assert cli.main(["validate", str(bad)]) == 1
    out = capsys.readouterr().out
    assert 'pack "Bad" drops[0]' in out and 'missing "artist"' in out and "FAILED" in out


def test_build_map_out_dir_no_registry(home, tmp_path, capsys):
    pack = write_pack(tmp_path / "p.json", GOOD)
    write_config(home, packs=[str(pack)])
    out_dir = tmp_path / "out"
    assert cli.main(["build-map", "--out", str(out_dir), "--no-registry"]) == 0
    js = json.loads((out_dir / "Map.json").read_text(encoding="utf-8"))
    lua = (out_dir / "Map.lua").read_text(encoding="utf-8")
    assert js["packs"] == ["CLI Pack"] and len(js["drops"]) == 3
    assert [d["id"] for d in js["drops"]] == ["area:87", "zone:1411", "zone:1429"]
    assert "NeedleDropper_Map = {" in lua and f"hash = {js['hash']}," in lua
    assert not (home / "maps").exists()
    out = capsys.readouterr().out
    assert f"map hash {js['hash']}" in out


def test_build_map_registry_copy(home, tmp_path):
    pack = write_pack(tmp_path / "p.json", GOOD)
    write_config(home, packs=[str(pack)])
    out_dir = tmp_path / "out"
    assert cli.main(["build-map", "--out", str(out_dir)]) == 0
    h = json.loads((out_dir / "Map.json").read_text(encoding="utf-8"))["hash"]
    assert (home / "maps" / f"{h}.json").is_file()


def test_build_map_refuses_bad_packs_and_missing_addon(home, tmp_path, capsys):
    bad = write_pack(tmp_path / "bad.json", [{"zone": "Nowhere", "title": "t", "artist": "a"}])
    write_config(home, packs=[str(bad)], wowRoot=str(tmp_path / "WoW"))
    assert cli.main(["build-map", "--out", str(tmp_path / "o")]) == 1
    assert "the map was not written" in capsys.readouterr().out
    assert not (tmp_path / "o" / "Map.lua").exists()
    assert cli.main(["build-map"]) == 1
    assert "install-addon" in capsys.readouterr().out


def make_fake_addon(root: Path) -> Path:
    src = root / "addon_src" / "NeedleDropper"
    (src / "media").mkdir(parents=True)
    (src / "NeedleDropper.toc").write_text("## Interface: 16001\n", encoding="utf-8")
    (src / "Core.lua").write_text("local ADDON, ns = ...\n", encoding="utf-8")
    (src / "Map.lua").write_text("NeedleDropper_Map = { placeholder = true }\n", encoding="utf-8")
    (src / "Map.json").write_text('{"hash": 1, "drops": []}', encoding="utf-8")
    (src / "media" / "silence.mp3").write_bytes(b"\xff\xfb")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_bytes(b"x")
    return src


def test_install_addon(home, tmp_path, monkeypatch, capsys):
    src = make_fake_addon(tmp_path)
    monkeypatch.setattr(cli, "REPO_ADDON_DIR", src)
    monkeypatch.setattr(cli, "is_game_running", lambda paths: False)
    wow = tmp_path / "WoW"
    (wow / "_forever_").mkdir(parents=True)
    pack = write_pack(tmp_path / "p.json", GOOD)
    write_config(home, packs=[str(pack)], wowRoot=str(wow))
    addons = wow / "_forever_" / "Interface" / "AddOns"
    local_media = addons / "NeedleDropper_Local" / "media"
    local_media.mkdir(parents=True)
    (local_media / "mine.mp3").write_bytes(b"user file")

    assert cli.main(["install-addon"]) == 0
    dest = addons / "NeedleDropper"
    assert (dest / "NeedleDropper.toc").is_file() and (dest / "media" / "silence.mp3").is_file()
    assert not (dest / "__pycache__").exists()
    js = json.loads((dest / "Map.json").read_text(encoding="utf-8"))
    assert len(js["drops"]) == 3  # regenerated from packs, not the placeholder
    assert (local_media / "mine.mp3").read_bytes() == b"user file"  # never touched

    # reinstall keeps the generated map even with --no-map
    (src / "Core.lua").write_text("local ADDON, ns = ...\n-- v2\n", encoding="utf-8")
    assert cli.main(["install-addon", "--no-map"]) == 0
    assert "v2" in (dest / "Core.lua").read_text(encoding="utf-8")
    assert json.loads((dest / "Map.json").read_text(encoding="utf-8")) == js


def test_install_addon_refuses_while_game_runs(home, tmp_path, monkeypatch, capsys):
    src = make_fake_addon(tmp_path)
    monkeypatch.setattr(cli, "REPO_ADDON_DIR", src)
    monkeypatch.setattr(cli, "is_game_running", lambda paths: True)
    wow = tmp_path / "WoW"
    (wow / "_forever_").mkdir(parents=True)
    write_config(home, packs=[str(write_pack(tmp_path / "p.json", GOOD))], wowRoot=str(wow))
    assert cli.main(["install-addon"]) == 2
    assert "WoW is running" in capsys.readouterr().out
    assert not (wow / "_forever_" / "Interface" / "AddOns" / "NeedleDropper").exists()
    assert cli.main(["install-addon", "--force"]) == 0
    assert (wow / "_forever_" / "Interface" / "AddOns" / "NeedleDropper" / "NeedleDropper.toc").is_file()


def test_install_addon_without_wow(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "REPO_ADDON_DIR", make_fake_addon(tmp_path))
    write_config(home, wowRoot=str(tmp_path / "NoWoW"))
    assert cli.main(["install-addon"]) == 1
    assert "was not found" in capsys.readouterr().out


def test_test_drop_card_only_without_a_window(home, tmp_path, monkeypatch, capsys):
    opened: list[str] = []
    monkeypatch.setattr(cli, "open_url", opened.append)
    pack = write_pack(tmp_path / "p.json", GOOD)
    write_config(home, packs=[str(pack)], providers=["local"], deck={"port": 0, "autoOpen": "never"},
                 fadeOutMs=0)
    assert cli.main(["test-drop", "Durotar", "--no-window", "--seconds", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "Test drop zone:1411" in out
    assert opened == []  # no window for a drop nothing can play
    assert cli.main(["test-drop", "Narnia", "--no-window"]) == 1
    assert "No drop matches" in capsys.readouterr().out


def test_run_starts_and_stops_cleanly(home, tmp_path, monkeypatch, capsys):
    opened: list[str] = []
    monkeypatch.setattr(cli, "open_url", opened.append)
    pack = write_pack(tmp_path / "p.json", GOOD)
    write_config(home, packs=[str(pack)], providers=["local", "bogus"], deck={"port": 0, "autoOpen": False},
                 transport={"pixel": {"enabled": False}, "chatlog": {"enabled": False}},
                 wowRoot=str(tmp_path / "WoW"))
    assert cli.main(["run", "--seconds", "0.3"]) == 0
    out = capsys.readouterr().out
    assert "NeedleDropper companion" in out and "Unknown provider 'bogus'" in out
    assert "local (plays in an open player window)" in out and "Control panel: http://127.0.0.1:" in out
    assert "Player:     never opens by itself" in out  # deck.autoOpen false migrated to "never"
    assert "Bye." in out
    assert opened == []  # an explicit `run` does not open the panel unless --panel
    assert (home / "needledropper.log").is_file()
    import logging
    assert logging.getLogger("needledropper").handlers == []  # nothing leaks out of the command


def test_build_providers_and_transports_report_missing(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.endswith("providers.spotify"):
            raise ImportError(f"No module named {name!r} (simulated)")
        return real_import(name, globals, locals, fromlist, level)

    import importlib

    real_im = importlib.import_module

    def fake_im(name, package=None):
        if name in (".transport.pixel", ".transport.chatlog"):
            raise ImportError(f"No module named {name!r} (simulated)")
        return real_im(name, package)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(cli.importlib, "import_module", fake_im)
    msgs: list[str] = []
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {})
    paths = config.paths_from_config(cfg)
    provs = cli.build_providers(cfg, paths, None, out=msgs.append)
    assert [p.name for p in provs] == ["local", "youtube"]
    assert any("Spotify support is not available" in m for m in msgs)
    trans = cli.build_transports(cfg, paths, lambda e, t: None, out=msgs.append)
    assert trans == []
    assert any("pixel transport is not available" in m for m in msgs)
    assert any("chatlog transport is not available" in m for m in msgs)


def test_no_command_means_the_default_start(capsys):
    """Double-clicking the exe = no command = run + control panel (cmd_default)."""
    args = cli.parse_args([])
    assert args.func is cli.cmd_default and args.command is None
    assert (args.panel, args.no_panel, args.open, args.seconds) == (False, False, False, 0.0)
    assert cli.parse_args(["run"]).func is cli.cmd_run
    with pytest.raises(SystemExit) as ei:
        cli.main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "build-map" in out and "control panel" in out


def test_build_providers_skips_switched_off_services(tmp_path):
    cfg = config.deep_merge(config.DEFAULT_CONFIG, {"spotify": {"enabled": False}, "local": {"enabled": False}})
    provs = cli.build_providers(cfg, config.paths_from_config(cfg), None, out=lambda m: None)
    assert [p.name for p in provs] == ["youtube"]
    old = config.deep_merge(config.DEFAULT_CONFIG, {"providers": ["applemusic", "spotify"]})
    assert config.normalize_config(old)["providers"] == ["spotify"]  # retired Apple Music is dropped


def test_update_config_file_keeps_only_user_keys(home):
    p = config.update_config_file({"spotify": {"clientId": "abc"}})
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw == {"spotify": {"clientId": "abc"}}
    config.update_config_file({"spotify": {"deviceName": "PC"}})
    assert json.loads(p.read_text(encoding="utf-8")) == {"spotify": {"clientId": "abc", "deviceName": "PC"}}
    assert config.load_config()["spotify"]["redirectPort"] == 8321


def test_resolved_cache_staleness(tmp_path):
    p = write_pack(tmp_path / "p.json", [{"zone": "Durotar", "title": "New Title", "artist": "A", "startMs": 10000}])
    cache = {"zone:1411": {"spotify": "spotify:track:4uLU6hMCjMI75M1A2tKUQC", "durationMs": 1,
                           "trackDurationMs": 200000, "queryTitle": "new title", "queryArtist": "A"}}
    d = packs.load_packs([p], resolved_cache=cache)[0]
    assert d.spotify and d.durationMs == 190000  # recomputed from the track length and startMs
    cache["zone:1411"]["queryTitle"] = "Old Title"
    d2 = packs.load_packs([p], resolved_cache=cache)[0]
    assert d2.spotify is None and d2.durationMs == 0
