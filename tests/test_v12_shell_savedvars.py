"""The companion reads the game's SavedVariables after the game writes them (DESIGN-v1.2 2.1): a rolling
backup in ``<app dir>\\backups`` and a Spotify pre-search for the player's search-only drops. The game
folder is only read. (C1 built the helpers; the v1.2 integration wires them into the shell.)"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import make_shell  # noqa: E402

SV = '''
NeedleDropperDB = {
  ["schema"] = 2,
  ["soundtracks"] = {
    ["my-drops"] = {
      ["id"] = "my-drops", ["name"] = "My Drops",
      ["drops"] = {
        { ["id"] = "area:87", ["areaID"] = 87, ["artist"] = "Toto", ["title"] = "Africa" },
        { ["id"] = "zone:1429", ["mapID"] = 1429, ["artist"] = "Low Roar", ["title"] = "Gosi",
          ["spotify"] = "4uLU6hMCjMI75M1A2tKUQC" },
        { ["id"] = "zone:1436", ["mapID"] = 1436, ["artist"] = "Off", ["title"] = "Song", ["disabled"] = true },
      },
    },
  },
}
'''


@pytest.fixture
def shell(tmp_path, _needledropper_home):
    s = make_shell(tmp_path, Path(_needledropper_home))
    try:
        yield s
    finally:
        s.close()


def sv_file(shell) -> Path:
    p = shell.app.paths.flavor_root / "WTF" / "Account" / "ACC1" / "SavedVariables" / "NeedleDropper.lua"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def test_a_saved_variables_write_is_backed_up_and_its_search_only_songs_pre_searched(shell):
    searched: list = []
    shell.engines.current.prewarm_songs = lambda songs, limit=25: searched.append(list(songs)) or len(songs)
    f = sv_file(shell)
    f.write_text(SV, encoding="utf-8")
    before = f.read_bytes()
    shell.app._watch_savedvars()
    backups = list((shell.app.paths.app_dir / "backups").rglob("NeedleDropper-*.lua"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert searched == [[("Toto", "Africa")]]          # no Spotify id, not turned off
    assert f.read_bytes() == before                    # the game's file is only read
    shell.app._watch_savedvars()                       # nothing changed: nothing read again
    assert searched == [[("Toto", "Africa")]] and len(list((shell.app.paths.app_dir / "backups").rglob("*.lua"))) == 1
    f.write_text(SV.replace("Africa", "Rosanna"), encoding="utf-8")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    shell.app._watch_savedvars()                       # the game saved again (logout / reload)
    assert searched[-1] == [("Toto", "Rosanna")]
    assert len(list((shell.app.paths.app_dir / "backups").rglob("*.lua"))) == 2


def test_no_game_folder_or_no_files_is_quiet(shell):
    shell.engines.current.prewarm_songs = lambda songs, limit=25: pytest.fail("nothing to search")
    shell.app._watch_savedvars()                       # a WoW folder without SavedVariables yet
    assert not (shell.app.paths.app_dir / "backups").exists()
