"""Tests for ``needledropper.gamedata`` (the NeedleDropper_Companion data addon writer).

``Data.lua`` is checked the way the game uses it: compiled and run by Lua 5.1
(lupa.lua51) with the addon varargs, in an empty environment, after which the
global must equal the input exactly. Everything happens in pytest temp dirs.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from needledropper import __version__
from needledropper import gamedata as gd
from needledropper.gamedata import GameDataError

import lupa.lua51 as lua51

_LUA = lua51.LuaRuntime(encoding=None, unpack_returned_tuples=True, register_eval=False,
                        register_builtins=False)
_RUN = _LUA.eval("""
function(src)
  local f, err = loadstring(src, "=Data.lua")
  if not f then return false, err end
  local env = {}
  setfenv(f, env)                         -- the file can reach nothing but its own globals
  local ok, e2 = pcall(f, "NeedleDropper_Companion", {})   -- the client passes (ADDON, ns)
  if not ok then return false, e2 end
  return true, env
end
""")

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"


def _from_lua(value):
    if lua51.lua_type(value) == "table":
        return {_from_lua(k): _from_lua(v) for k, v in value.items()}
    if isinstance(value, bytes):
        return value.decode("utf-8")  # strict: the file must hold valid UTF-8
    return value


def run_data_lua(path_or_bytes) -> dict:
    """Run ``Data.lua`` like the client does; returns ``{global name: value}``."""
    src = path_or_bytes.read_bytes() if isinstance(path_or_bytes, Path) else path_or_bytes
    ok, env = _RUN(src)
    assert ok, env
    return {k.decode(): _from_lua(v) for k, v in env.items()}


def full_data(**overrides) -> dict:
    """A complete, already-canonical payload (normalizing it changes nothing)."""
    data = {
        "schema": 1,
        "appVersion": "1.2.0",
        "written": 1762380000,
        "status": {"spotify": "connected", "youtube": True, "transports": {"pixel": True, "chatlog": False}},
        "tracks": {
            "sub:1429:lion-s-pride-inn": {"title": "Tavern Song", "artist": "Björk"},
            "zone:1429": {"spotify": SPOTIFY_ID, "durationMs": 245000, "title": "I'll Keep Coming",
                          "artist": "Low Roar"},
            "zone:1453": {"durationMs": 0},
        },
    }
    data.update(overrides)
    return data


@pytest.fixture
def addons(tmp_path) -> Path:
    d = tmp_path / "_forever_" / "Interface" / "AddOns"
    d.mkdir(parents=True)
    return d


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    """Every file below ``root`` with its size and mtime."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


# ------------------------------------------------------------ the addon --

def test_ensure_creates_the_data_addon(addons):
    folder = gd.ensure_companion_addon(addons)
    assert folder == addons / "NeedleDropper_Companion"
    assert sorted(p.name for p in addons.iterdir()) == ["NeedleDropper_Companion"]
    assert sorted(p.name for p in folder.iterdir()) == ["Data.lua", "NeedleDropper_Companion.toc"]
    toc = (folder / "NeedleDropper_Companion.toc").read_text(encoding="utf-8")
    assert toc.splitlines() == [
        "## Interface: 16001",
        "## Title: NeedleDropper Companion Data",
        "## Notes: Written by the NeedleDropper app. Do not edit.",
        "## Author: NeedleDropper",
        f"## Version: {__version__}",
        "Data.lua",
    ]
    assert "SavedVariables" not in toc
    data = (folder / "Data.lua").read_bytes()
    assert data.split(b"\n", 1)[0] == b"local ADDON, ns = ..."
    assert run_data_lua(data) == {"NeedleDropper_CompanionData": {
        "schema": 1, "appVersion": __version__, "written": 0,
        "status": {"spotify": "not-connected", "youtube": False, "transports": {"pixel": False, "chatlog": False}},
        "tracks": {},
    }}
    assert gd.is_companion_addon_installed(addons)


def test_ensure_is_idempotent(addons, monkeypatch):
    gd.ensure_companion_addon(addons)
    before = _tree(addons)
    calls = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda *a: (calls.append(a), real_replace(*a)))
    gd.ensure_companion_addon(addons)
    gd.ensure_companion_addon(addons)
    assert calls == [] and _tree(addons) == before


def test_ensure_keeps_existing_data(addons):
    path = gd.write_companion_data(addons, full_data())
    before = path.read_bytes()
    gd.ensure_companion_addon(addons)
    assert path.read_bytes() == before


def test_ensure_replaces_an_unreadable_data_file(addons, caplog):
    folder = gd.ensure_companion_addon(addons)
    (folder / "Data.lua").write_bytes(b"local ADDON, ns = ...\nNeedleDropper_CompanionData = { oops")
    with caplog.at_level(logging.WARNING, logger="needledropper.gamedata"):
        gd.ensure_companion_addon(addons)
    assert "unreadable" in caplog.text
    assert gd.read_companion_data(addons)["written"] == 0


def test_ensure_never_replaces_data_it_cannot_read_right_now(addons, monkeypatch):
    path = gd.write_companion_data(addons, full_data())
    before = path.read_bytes()

    def locked(*a, **k):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(gd, "_read_data", locked)
    with pytest.raises(PermissionError):
        gd.ensure_companion_addon(addons)
    assert path.read_bytes() == before


def test_toc_follows_the_installed_addons_interface(addons):
    main = addons / "NeedleDropper"
    main.mkdir()
    (main / "NeedleDropper.toc").write_text("## Interface: 16002\n## Title: NeedleDropper\nCore.lua\n",
                                            encoding="utf-8")
    toc = gd.ensure_companion_addon(addons) / gd.TOC_FILENAME
    assert toc.read_text(encoding="utf-8").startswith("## Interface: 16002\n")
    (main / "NeedleDropper.toc").write_bytes(b"\xef\xbb\xbf## Interface: 11507, 16003\r\n")
    gd.write_companion_data(addons, {})
    assert toc.read_text(encoding="utf-8").startswith("## Interface: 11507, 16003\n")
    (main / "NeedleDropper.toc").write_text("## Interface: 16004\nX.lua\n## Interface: evil\n", encoding="utf-8")
    gd.ensure_companion_addon(addons)
    assert toc.read_text(encoding="utf-8").startswith("## Interface: 16004\n")
    # Anything that is not a plain interface list falls back to the default.
    for bad in ("## Interface: 16002 ## LoadOnDemand: 1\n", "## Interface: 1600 1\n", "## Title: no interface\n"):
        (main / "NeedleDropper.toc").write_text(bad, encoding="utf-8")
        gd.ensure_companion_addon(addons)
        lines = toc.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "## Interface: 16001" and not any("LoadOnDemand" in line for line in lines)


def test_toc_is_rewritten_when_the_app_version_changes(addons, monkeypatch):
    toc = gd.ensure_companion_addon(addons) / gd.TOC_FILENAME
    monkeypatch.setattr(gd, "_APP_VERSION", "9.9.9")
    gd.ensure_companion_addon(addons)
    assert "## Version: 9.9.9\n" in toc.read_text(encoding="utf-8")


@pytest.mark.parametrize("interface, version", [
    ("16001\n## LoadOnDemand: 1", None), ("abc", None), ("", None), (None, "1.0\n## LoadOnDemand: 1"),
    (None, ""), (None, "1.0 beta"),
])
def test_render_toc_rejects_injection(interface, version):
    with pytest.raises(GameDataError):
        gd.render_toc(interface, version)


# ------------------------------------------------------------- Data.lua --

def test_written_data_runs_in_lua_and_equals_the_input(addons):
    data = full_data()
    path = gd.write_companion_data(addons, data)
    assert path == addons / "NeedleDropper_Companion" / "Data.lua"
    assert run_data_lua(path) == {"NeedleDropper_CompanionData": data}
    assert gd.read_companion_data(addons) == data
    assert gd.normalize_companion_data(data) == data


def test_defaults_are_filled_in(addons):
    t0 = time.time()
    gd.write_companion_data(addons, {"status": {"spotify": "premium-required"},
                                     "tracks": {"zone:1": {"title": "x", "spotify": None}}})
    got = gd.read_companion_data(addons)
    assert t0 - 1 <= got["written"] <= time.time() + 1
    assert got["appVersion"] == __version__ and got["schema"] == 1
    assert got["status"] == {"spotify": "premium-required", "youtube": False,
                             "transports": {"pixel": False, "chatlog": False}}
    assert got["tracks"] == {"zone:1": {"title": "x"}}
    assert gd.normalize_companion_data(None, now=5)["written"] == 5
    assert gd.normalize_companion_data({"written": 7}, now=5)["written"] == 7
    assert gd.normalize_companion_data({"written": 7.9})["written"] == 7


HOSTILE_TEXT = [
    'double "quotes"', "single 'quotes'", "back\\slash", "\\", "\\\\n not a newline", "line\nbreak",
    "crlf\r\nline", "cr\ronly", "nul\x00byte", "".join(chr(c) for c in range(32)) + "\x7f",
    "\x01" + "23", "\x00" + "1", "tab\there", "]]", "[[", "]=]", "[==[", "-- not a comment",
    "--[[ not a comment ]]", "\" ; os.exit() --", "end", "éè café", "日本語",
    "\U0001F3B5\U0001F3B6", "é combining", "‮RTL‬", "  ", "﻿BOM", " nbsp",
    "|cffff0000red|r ||", "%s %d %%", "",
]


@pytest.mark.parametrize("text", HOSTILE_TEXT)
def test_hostile_titles_survive_exactly(addons, text):
    data = full_data(tracks={"zone:1429": {"title": text, "artist": text[::-1]}})
    path = gd.write_companion_data(addons, data)
    assert run_data_lua(path) == {"NeedleDropper_CompanionData": data}
    assert gd.read_companion_data(addons) == data


def test_hostile_keys_survive_exactly(addons):
    keys = ['"]] os.exit() --', 'a"b', "x\ny", "end", "nil", "true", "_G", "1abc", "zone:1429",
            "sub:1429:lion-s-pride-inn", "\x00", "é日\U0001F3B5", "[[x]]", "\\", "key with spaces",
            "spotify/track:" + SPOTIFY_ID, "low roar + i'll keep coming", "Valid_Identifier"]
    data = full_data(tracks={k: {"title": k} for k in keys})
    path = gd.write_companion_data(addons, data)
    got = run_data_lua(path)["NeedleDropper_CompanionData"]["tracks"]
    assert got == {k: {"title": k} for k in keys}
    text = path.read_text(encoding="utf-8")
    assert "  Valid_Identifier = " in text and '  ["end"] = ' in text and '["\\"]] os.exit() --"]' in text


def test_control_characters_use_three_digit_escapes():
    assert gd.lua_quote("\x01" + "23") == '"\\00123"'
    assert gd.lua_quote("\x00") == '"\\000"' and gd.lua_quote("\x7f") == '"\\127"'
    assert gd.lua_quote('a"b\\c\nd\re\tf') == '"a\\"b\\\\c\\nd\\re\\tf"'
    assert gd.lua_quote("café \U0001F3B5") == '"café \U0001F3B5"'
    with pytest.raises(GameDataError):
        gd.lua_quote(5)  # type: ignore[arg-type]


def test_lone_surrogates_become_replacement_characters(addons):
    data = full_data(tracks={"k\ud800": {"title": "a\udfffb", "artist": "\ud83c"}})
    path = gd.write_companion_data(addons, data)
    assert run_data_lua(path)["NeedleDropper_CompanionData"]["tracks"] == {
        "k�": {"title": "a�b", "artist": "�"}}


def test_output_is_deterministic():
    a = full_data()
    b = {
        "tracks": {k: dict(reversed(list(v.items()))) for k, v in reversed(list(a["tracks"].items()))},
        "status": {"transports": {"chatlog": False, "pixel": True}, "youtube": True, "spotify": "connected"},
        "written": a["written"], "appVersion": a["appVersion"], "schema": 1,
    }
    assert gd.render_data_lua(a) == gd.render_data_lua(b) == gd.render_data_lua(a)
    text = gd.render_data_lua(a)
    assert text.index('["sub:1429:lion-s-pride-inn"]') < text.index('["zone:1429"]') < text.index('["zone:1453"]')
    assert text.index("schema =") < text.index("appVersion =") < text.index("written =") < \
        text.index("status =") < text.index("tracks =")


def test_rendered_file_layout():
    text = gd.render_data_lua(full_data(tracks={"zone:1429": {"spotify": SPOTIFY_ID, "durationMs": 245000}}))
    lines = text.splitlines()
    assert lines[0] == "local ADDON, ns = ..."
    assert all(line.startswith("--") for line in lines[1:4])
    assert lines[4] == "NeedleDropper_CompanionData = {"
    assert '    ["zone:1429"] = { spotify = "4uLU6hMCjMI75M1A2tKUQC", durationMs = 245000 },' in lines
    assert "    transports = { pixel = true, chatlog = false }," in lines
    assert text.endswith("}\n") and "\r" not in text


@pytest.mark.parametrize("value", [
    SPOTIFY_ID, f" spotify:track:{SPOTIFY_ID} ", f"https://open.spotify.com/track/{SPOTIFY_ID}?si=a1b2c3",
    f"https://open.spotify.com/intl-de/track/{SPOTIFY_ID}", f"HTTP://OPEN.SPOTIFY.COM/track/{SPOTIFY_ID}/",
])
def test_spotify_ids_are_normalized(value):
    out = gd.normalize_companion_data({"tracks": {"k": {"spotify": value}}}, now=0)
    assert out["tracks"]["k"]["spotify"] == SPOTIFY_ID


@pytest.mark.parametrize("data", [
    [], "x", {"bogus": 1}, {"schema": 2}, {"schema": True}, {"appVersion": ""}, {"appVersion": 5},
    {"appVersion": "1.0\n## LoadOnDemand: 1"}, {"appVersion": "1.0 --]] x"}, {"written": -1},
    {"written": "now"}, {"written": True}, {"written": float("nan")}, {"written": 2.0 ** 60},
    {"status": []}, {"status": {"spotify": "maybe"}}, {"status": {"spotify": 1}}, {"status": {"youtube": "yes"}},
    {"status": {"youtube": 1}}, {"status": {"transports": {"pixel": 1}}}, {"status": {"transports": {"udp": True}}},
    {"status": {"transports": []}}, {"status": {"other": 1}}, {"tracks": []}, {"tracks": {"k": "x"}},
    {"tracks": {"k": {"bogus": 1}}}, {"tracks": {"k": {"spotify": "short"}}}, {"tracks": {"k": {"spotify": 123}}},
    {"tracks": {"k": {"spotify": f"spotify:album:{SPOTIFY_ID}"}}},
    {"tracks": {"k": {"spotify": f"https://example.com/track/{SPOTIFY_ID}"}}},
    {"tracks": {"k": {"spotify": SPOTIFY_ID + "!"}}}, {"tracks": {"k": {"durationMs": -1}}},
    {"tracks": {"k": {"durationMs": True}}}, {"tracks": {"k": {"durationMs": 1.5}}},
    {"tracks": {"k": {"durationMs": "245000"}}}, {"tracks": {"k": {"durationMs": gd.MAX_DURATION_MS + 1}}},
    {"tracks": {"k": {"title": 5}}}, {"tracks": {"k": {"artist": b"x"}}}, {"tracks": {1: {}}}, {"tracks": {"": {}}},
    {"tracks": {"a\ud800": {}, "a\udc00": {}}},
])
def test_invalid_data_is_rejected_before_touching_disk(addons, data):
    with pytest.raises(GameDataError):
        gd.write_companion_data(addons, data)
    assert list(addons.iterdir()) == []


def test_integral_float_duration_is_accepted():
    assert gd.normalize_companion_data({"tracks": {"k": {"durationMs": 245000.0}}}, now=0)["tracks"]["k"] == {
        "durationMs": 245000}


def test_track_count_cap(addons, caplog):
    tracks = {f"t{i:04d}": {"durationMs": i} for i in range(gd.MAX_TRACKS + 1)}
    with caplog.at_level(logging.WARNING, logger="needledropper.gamedata"):
        path = gd.write_companion_data(addons, {"tracks": tracks}, now=1)
    got = run_data_lua(path)["NeedleDropper_CompanionData"]["tracks"]
    assert len(got) == gd.MAX_TRACKS == 2000
    assert "t2000" not in got and got["t1999"] == {"durationMs": 1999}
    assert "writing the first 2000" in caplog.text
    small = gd.normalize_companion_data({"tracks": {"c": {}, "a": {}, "b": {}}}, now=0, max_tracks=2)
    assert list(small["tracks"]) == ["a", "b"]


def test_text_and_key_caps(caplog):
    with caplog.at_level(logging.WARNING, logger="needledropper.gamedata"):
        out = gd.normalize_companion_data({"tracks": {
            "long": {"title": "x" * 1000, "artist": "é" * 301},
            "k" * (gd.MAX_KEY_CHARS + 1): {"title": "skipped"},
        }}, now=0)
    assert out["tracks"] == {"long": {"title": "x" * gd.MAX_TEXT_CHARS, "artist": "é" * gd.MAX_TEXT_CHARS}}
    assert "skipping a track" in caplog.text


def test_largest_payload_stays_loadable_and_under_the_size_cap(addons, caplog):
    tracks = {f"zone:{i:05d}" + "\U0001F3B5" * 100: {"spotify": SPOTIFY_ID, "durationMs": 300000,
                                                    "title": "\U0001F3B5" * 300, "artist": "日" * 300}
              for i in range(gd.MAX_TRACKS)}
    with caplog.at_level(logging.WARNING, logger="needledropper.gamedata"):
        path = gd.write_companion_data(addons, {"tracks": tracks}, now=1)
    size = path.stat().st_size
    assert size <= gd.MAX_DATA_BYTES
    got = run_data_lua(path)["NeedleDropper_CompanionData"]["tracks"]
    assert 0 < len(got) < gd.MAX_TRACKS  # trimmed to the byte budget, not failed
    assert set(got) == set(sorted(tracks)[:len(got)])  # the first ones in key order
    assert "do not fit" in caplog.text


# ---------------------------------------------------------- disk safety --

def test_identical_write_is_skipped(addons, monkeypatch):
    path = gd.write_companion_data(addons, full_data())
    before = path.stat().st_mtime_ns
    calls = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda *a: (calls.append(a), real_replace(*a)))
    assert gd.write_companion_data(addons, full_data()) == path
    assert calls == [] and path.stat().st_mtime_ns == before
    gd.write_companion_data(addons, full_data(written=1762380001))
    assert len(calls) == 1


def test_replace_is_retried_while_the_file_is_locked(addons, monkeypatch):
    gd.write_companion_data(addons, full_data())
    monkeypatch.setattr(gd, "_REPLACE_DELAY", 0)
    real_replace = os.replace
    attempts = []

    def flaky(src, dst):
        attempts.append(dst)
        if len(attempts) < 3:
            raise PermissionError(13, "locked by the game")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    path = gd.write_companion_data(addons, full_data(written=2))
    assert len(attempts) == 3
    assert gd.read_companion_data(addons)["written"] == 2
    assert sorted(p.name for p in path.parent.iterdir()) == ["Data.lua", "NeedleDropper_Companion.toc"]


def test_a_failed_replace_keeps_the_old_file_and_no_temp_files(addons, monkeypatch):
    path = gd.write_companion_data(addons, full_data())
    before = path.read_bytes()
    monkeypatch.setattr(gd, "_REPLACE_DELAY", 0)

    def always_locked(src, dst):
        raise PermissionError(13, "locked by the game")

    monkeypatch.setattr(os, "replace", always_locked)
    with pytest.raises(PermissionError):
        gd.write_companion_data(addons, full_data(written=2))
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["Data.lua", "NeedleDropper_Companion.toc"]


def test_a_crash_while_writing_keeps_the_old_file(addons, monkeypatch):
    path = gd.write_companion_data(addons, full_data())
    before = path.read_bytes()

    def disk_full(fd, *a, **k):
        os.close(fd)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fdopen", disk_full)
    with pytest.raises(OSError, match="No space left"):
        gd.write_companion_data(addons, full_data(written=2))
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["Data.lua", "NeedleDropper_Companion.toc"]


def test_stale_temp_files_of_ours_are_cleaned_up(addons):
    folder = gd.ensure_companion_addon(addons)
    old = folder / ".Data.lua.abc123.tmp"
    fresh = folder / ".Data.lua.def456.tmp"
    other = folder / "notes.txt"
    for p in (old, fresh, other):
        p.write_text("x", encoding="utf-8")
    hour_ago = time.time() - 3600
    os.utime(old, (hour_ago, hour_ago))
    gd.write_companion_data(addons, full_data())
    assert not old.exists() and fresh.exists() and other.exists()


def test_writes_nothing_else_in_the_addons_folder(addons):
    (addons / "NeedleDropper").mkdir()
    (addons / "NeedleDropper" / "NeedleDropper.toc").write_text("## Interface: 16001\n", encoding="utf-8")
    (addons / "OtherAddon").mkdir()
    (addons / "OtherAddon" / "Other.lua").write_text("-- other", encoding="utf-8")
    (addons / "loose.txt").write_text("keep", encoding="utf-8")
    others_before = {k: v for k, v in _tree(addons).items() if not k.startswith(gd.ADDON_FOLDER)}
    gd.ensure_companion_addon(addons)
    gd.write_companion_data(addons, full_data())
    gd.write_companion_data(addons, full_data(written=5))
    assert sorted(p.name for p in addons.iterdir()) == ["NeedleDropper", "NeedleDropper_Companion", "OtherAddon",
                                                        "loose.txt"]
    assert {k: v for k, v in _tree(addons).items() if not k.startswith(gd.ADDON_FOLDER)} == others_before
    assert sorted(p.name for p in (addons / gd.ADDON_FOLDER).iterdir()) == ["Data.lua",
                                                                           "NeedleDropper_Companion.toc"]


def test_refuses_a_missing_addons_folder(tmp_path):
    missing = tmp_path / "_forever_" / "Interface" / "AddOns"
    for call in (gd.ensure_companion_addon, lambda d: gd.write_companion_data(d, {})):
        with pytest.raises(GameDataError, match="does not exist"):
            call(missing)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("sub", ["", "Interface", "Interface/AddOns/NeedleDropper", "AddOnsBackup"])
def test_refuses_folders_that_are_not_addons(tmp_path, sub):
    target = tmp_path / sub if sub else tmp_path
    target.mkdir(parents=True, exist_ok=True)
    before = _tree(tmp_path)
    with pytest.raises(GameDataError, match="not a WoW AddOns folder"):
        gd.write_companion_data(target, full_data())
    with pytest.raises(GameDataError, match="not a WoW AddOns folder"):
        gd.ensure_companion_addon(target)
    assert _tree(tmp_path) == before
    assert not (target / gd.ADDON_FOLDER).exists()


def test_refuses_bad_addons_dir_argument():
    with pytest.raises(GameDataError):
        gd.ensure_companion_addon(None)  # type: ignore[arg-type]


def test_refuses_a_file_where_the_folder_should_be(addons):
    blocker = addons / gd.ADDON_FOLDER
    blocker.write_text("not a folder", encoding="utf-8")
    with pytest.raises(GameDataError, match="not a folder"):
        gd.ensure_companion_addon(addons)
    assert blocker.read_text(encoding="utf-8") == "not a folder"


def test_refuses_a_data_path_that_is_a_folder(addons):
    folder = gd.ensure_companion_addon(addons)
    (folder / "Data.lua").unlink()
    (folder / "Data.lua").mkdir()
    with pytest.raises(GameDataError, match="is a folder"):
        gd.write_companion_data(addons, full_data())


def _make_link(link: Path, target: Path, kind: str) -> None:
    if kind == "junction":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, AttributeError, OSError) as exc:
            pytest.skip(f"cannot create a junction here: {exc}")
    else:
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot create a symlink here: {exc}")


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_refuses_a_link_that_leads_outside(addons, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = addons / gd.ADDON_FOLDER
    _make_link(link, outside, kind)
    try:
        with pytest.raises(GameDataError, match="refusing to write"):
            gd.ensure_companion_addon(addons)
        with pytest.raises(GameDataError, match="refusing to write"):
            gd.write_companion_data(addons, full_data())
        assert list(outside.iterdir()) == []
    finally:
        try:
            os.rmdir(link)  # removes a junction / Windows directory symlink, never the target
        except OSError:
            os.unlink(link)
        assert outside.is_dir()


# --------------------------------------------------------------- reading --

def test_read_companion_data_edge_cases(addons):
    assert gd.read_companion_data(addons) is None
    folder = gd.ensure_companion_addon(addons)
    data = folder / "Data.lua"
    data.write_text("local ADDON, ns = ...\n-- nothing here\n", encoding="utf-8")
    assert gd.read_companion_data(addons) is None
    data.write_text("local ADDON, ns = ...\nNeedleDropper_CompanionData = 5\n", encoding="utf-8")
    with pytest.raises(GameDataError, match="not a table"):
        gd.read_companion_data(addons)
    data.write_text("local ADDON, ns = ...\nNeedleDropper_CompanionData = {\n  x = ,\n}\n", encoding="utf-8")
    with pytest.raises(GameDataError, match="line 3"):
        gd.read_companion_data(addons)
    data.write_text("NeedleDropper_CompanionData = { tracks = {} }\n", encoding="utf-8")
    assert gd.read_companion_data(addons) == {"tracks": {}}
    data.unlink()
    assert gd.read_companion_data(addons) is None


# ------------------------------------------------- v1.2: flags and imports --

CODE = "!NeedleDropper:1!eNqrVkrLz1eyUlBKys9RqgUAKSoFIw==!"
CODE2 = "!NeedleDropper:1!eNqrVkrLz1eyUlBKys9RqgUAKSoFIx==!"


def test_track_flags_are_written_only_when_true(addons):
    data = full_data(tracks={"q:toto\x1fafrica": {"notFound": True}, "yt:QAo_Ycocl1E": {"notEmbeddable": True},
                             "q:a\x1fb": {"spotify": SPOTIFY_ID, "notFound": False}})
    path = gd.write_companion_data(addons, data)
    got = run_data_lua(path)["NeedleDropper_CompanionData"]["tracks"]
    assert got == {"q:toto\x1fafrica": {"notFound": True}, "yt:QAo_Ycocl1E": {"notEmbeddable": True},
                   "q:a\x1fb": {"spotify": SPOTIFY_ID}}
    assert b'["q:toto\\031africa"]' in path.read_bytes()  # the unit separator as a 3-digit escape
    with pytest.raises(GameDataError):
        gd.normalize_companion_data({"tracks": {"k": {"notFound": "yes"}}})


def test_imports_are_validated_and_run_in_lua(addons):
    data = full_data(imports=[{"code": CODE, "active": True}, {"code": CODE2}])
    path = gd.write_companion_data(addons, data)
    got = run_data_lua(path)["NeedleDropper_CompanionData"]["imports"]
    assert got == {1: {"code": CODE, "active": True}, 2: {"code": CODE2, "active": False}}
    assert gd.read_companion_data(addons)["imports"] == [{"code": CODE, "active": True},
                                                         {"code": CODE2, "active": False}]


def test_empty_imports_are_left_out(addons):
    for empty in ([], {}, None):
        out = gd.normalize_companion_data({"imports": empty}, now=0)
        assert "imports" not in out
    gd.write_companion_data(addons, {"imports": []}, now=0)
    assert "imports" not in gd.read_companion_data(addons)


def test_the_same_code_twice_is_one_offer():
    out = gd.normalize_companion_data({"imports": [{"code": CODE}, {"code": CODE, "active": True}]}, now=0)
    assert out["imports"] == [{"code": CODE, "active": True}]


@pytest.mark.parametrize("imports", [
    "x", [5], [{"code": "!WA:2!abc"}], [{"code": "!NeedleDropper:1!@@!"}], [{"code": "!NeedleDropper:1!!"}],
    [{"code": CODE, "active": "yes"}], [{"code": CODE, "extra": 1}], [{"active": True}],
    [{"code": "!NeedleDropper:1!" + "A" * 70000 + "!"}],
    [{"code": "!NeedleDropper:1!" + "A" * (i + 4) + "!"} for i in range(51)],
    [{"code": CODE + "\n## LoadOnDemand: 1"}],
])
def test_bad_imports_are_rejected(addons, imports):
    with pytest.raises(GameDataError):
        gd.write_companion_data(addons, {"imports": imports})
    assert list(addons.iterdir()) == []


def test_query_track_key_lowercases_ascii_only():
    assert gd.query_track_key("TOTO", "Africa") == "q:toto\x1fafrica"
    assert gd.query_track_key("\u00d3lafur Arnalds", "\u00de\u00fa") == "q:\u00d3lafur arnalds\x1f\u00de\u00fa"
    assert gd.query_track_key(None, "x") == "q:\x1fx"


def test_big_imports_shrink_the_tracks_budget(addons, caplog):
    codes = [{"code": "!NeedleDropper:1!" + "A" * (60000 + i) + "!"} for i in range(50)]
    tracks = {f"q:{i:05d}": {"title": "t" * 300, "artist": "a" * 300} for i in range(2000)}
    with caplog.at_level(logging.WARNING, logger="needledropper.gamedata"):
        path = gd.write_companion_data(addons, {"imports": codes, "tracks": tracks}, now=1)
    assert path.stat().st_size <= gd.MAX_DATA_BYTES
    got = gd.read_companion_data(addons)
    assert len(got["imports"]) == 50 and 0 < len(got["tracks"]) < 2000
    assert "do not fit" in caplog.text


# ---------------------------------------------------- coalesced updates --

class ManualTimers:
    """A scheduler for CompanionDataWriter: timers run only when the test says so."""

    def __init__(self) -> None:
        self.pending: list[list] = []

    def __call__(self, delay: float, fn):
        entry = [delay, fn, False]

        class Handle:
            def cancel(self_inner) -> None:
                entry[2] = True

        self.pending.append(entry)
        return Handle()

    def run(self) -> int:
        n = 0
        while self.pending:
            _delay, fn, cancelled = self.pending.pop(0)
            if not cancelled:
                fn()
                n += 1
        return n

    def delays(self) -> list[float]:
        return [d for d, _fn, c in self.pending if not c]


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def writer(addons):
    clock, timers = Clock(), ManualTimers()
    w = gd.CompanionDataWriter(addons, clock=clock, now=lambda: 1762380000, schedule=timers)
    return w, clock, timers


def data_of(addons) -> dict:
    return gd.read_companion_data(addons) or {}


def test_writer_coalesces_to_one_write_per_two_seconds(addons, writer):
    w, clock, timers = writer
    w.record_track("q:a\x1fb", {"spotify": SPOTIFY_ID, "durationMs": 1000, "title": "B", "artist": "A"})
    assert timers.delays() == [0.0]  # the first change is written right away (on the timer thread)
    timers.run()
    assert w.writes == 1 and data_of(addons)["tracks"]["q:a\x1fb"]["spotify"] == SPOTIFY_ID
    clock.t += 0.5
    w.set_status(spotify="connected")
    w.record_track("q:c\x1fd", {"notFound": True})
    w.add_import(CODE, active=True)
    assert timers.delays() == [pytest.approx(1.5)]  # one timer for all three, at the 2 s mark
    clock.t += 1.5
    timers.run()
    assert w.writes == 2
    got = data_of(addons)
    assert got["status"]["spotify"] == "connected" and got["tracks"]["q:c\x1fd"] == {"notFound": True}
    assert got["imports"] == [{"code": CODE, "active": True}] and got["written"] == 1762380000
    assert not w.pending


def test_writer_merges_into_what_is_on_disk(addons, writer):
    w, _clock, timers = writer
    gd.write_companion_data(addons, full_data())  # written by someone else
    w.record_track("zone:1429", {"title": "Changed", "durationMs": None})
    timers.run()
    got = data_of(addons)
    assert got["tracks"]["zone:1429"] == {"spotify": SPOTIFY_ID, "title": "Changed", "artist": "Low Roar"}
    assert got["tracks"]["sub:1429:lion-s-pride-inn"] == {"title": "Tavern Song", "artist": "Björk"}
    assert got["status"]["spotify"] == "connected" and got["appVersion"] == __version__


def test_record_track_merge_replace_and_delete(addons, writer):
    w, _clock, _timers = writer
    key = "q:toto\x1fafrica"
    w.record_track(key, {"notFound": True})
    w.record_track(key, {"spotify": SPOTIFY_ID, "title": "Africa", "notFound": False})
    w.record_track(key, {"notEmbeddable": True})
    w.flush()
    assert data_of(addons)["tracks"][key] == {"spotify": SPOTIFY_ID, "title": "Africa", "notEmbeddable": True}
    w.record_track(key, {"artist": "Toto"}, replace=True)
    w.flush()
    assert data_of(addons)["tracks"][key] == {"artist": "Toto"}
    w.record_track(key, {"artist": None})  # nothing left: the entry goes
    w.record_track("q:x\x1fy", {"notFound": True})
    w.record_track("q:x\x1fy", None)
    w.flush()
    assert data_of(addons)["tracks"] == {}


def test_set_status_merges_transports(addons, writer):
    w, _clock, _timers = writer
    w.set_status(transports={"pixel": True})
    w.set_status(transports={"chatlog": True}, youtube=True)
    w.flush()
    st = data_of(addons)["status"]
    assert st == {"spotify": "not-connected", "youtube": True, "transports": {"pixel": True, "chatlog": True}}


def test_imports_add_dedupe_and_replace(addons, writer):
    w, _clock, _timers = writer
    w.add_import(CODE)
    w.add_import(CODE, active=True)
    w.add_import(CODE2)
    w.flush()
    assert data_of(addons)["imports"] == [{"code": CODE, "active": True}, {"code": CODE2, "active": False}]
    w.set_imports([{"code": CODE2}])
    w.flush()
    assert data_of(addons)["imports"] == [{"code": CODE2, "active": False}]
    w.set_imports([])
    w.flush()
    assert "imports" not in data_of(addons)


def test_touch_refreshes_written(addons):
    times = iter([1000, 2000])
    w = gd.CompanionDataWriter(addons, now=lambda: next(times), schedule=ManualTimers())
    w.touch()
    w.flush()
    assert data_of(addons)["written"] == 1000
    w.touch()
    w.flush()
    assert data_of(addons)["written"] == 2000


@pytest.mark.parametrize("call", [
    lambda w: w.record_track("", {}), lambda w: w.record_track("k", {"bogus": 1}),
    lambda w: w.record_track("k", {"spotify": "short"}), lambda w: w.record_track("k", {"notFound": 1}),
    lambda w: w.record_track("k" * 600, {}), lambda w: w.record_track("k", ["x"]),
    lambda w: w.set_status(spotify="maybe"), lambda w: w.set_status(other=1),
    lambda w: w.set_status(transports={"udp": True}), lambda w: w.set_status(transports={"pixel": 1}),
    lambda w: w.add_import("not a code"), lambda w: w.add_import(CODE, active="yes"),
    lambda w: w.set_imports([{"code": "x"}]),
])
def test_bad_changes_raise_at_once_and_queue_nothing(addons, writer, call):
    w, _clock, timers = writer
    with pytest.raises(GameDataError):
        call(w)
    assert not w.pending and timers.delays() == []


def test_a_locked_file_is_retried_and_nothing_is_lost(addons, writer, monkeypatch):
    w, _clock, timers = writer
    real = gd.write_companion_data
    fails = {"n": 2}

    def flaky(*a, **kw):
        if fails["n"]:
            fails["n"] -= 1
            raise PermissionError("the game holds Data.lua")
        return real(*a, **kw)

    monkeypatch.setattr(gd, "write_companion_data", flaky)
    w.record_track("q:a\x1fb", {"notFound": True})
    timers.run()  # fails twice (the retry timer runs too), then the third attempt writes
    assert not w.pending and w.last_error is None
    w.record_track("q:c\x1fd", {"notFound": True})
    w.flush()
    assert set(data_of(addons)["tracks"]) == {"q:a\x1fb", "q:c\x1fd"}


def test_an_unsafe_folder_is_reported_not_retried(tmp_path, caplog):
    w = gd.CompanionDataWriter(tmp_path / "NotAddOns", schedule=ManualTimers())
    w.record_track("q:a\x1fb", {"notFound": True})
    with caplog.at_level(logging.ERROR, logger="needledropper.gamedata"):
        assert w.flush() is None
    assert "not a WoW AddOns folder" in (w.last_error or "") and not w.pending
    assert not (tmp_path / "NotAddOns").exists()


def test_module_level_updates_share_one_writer_per_folder(addons):
    assert gd.writer_for(addons) is gd.writer_for(str(addons))
    gd.record_track(addons, gd.query_track_key("Toto", "Africa"), {"spotify": SPOTIFY_ID})
    gd.set_status(addons, spotify="connected", youtube=True, transports={"pixel": True, "chatlog": False})
    gd.add_import(addons, CODE, active=True)
    gd.touch(addons)
    gd.flush(addons)
    got = data_of(addons)
    assert got["tracks"] == {"q:toto\x1fafrica": {"spotify": SPOTIFY_ID}}
    assert got["status"]["youtube"] is True and got["imports"] == [{"code": CODE, "active": True}]
    assert set(_tree(addons)) == {os.path.join("NeedleDropper_Companion", "Data.lua"),
                                  os.path.join("NeedleDropper_Companion", "NeedleDropper_Companion.toc")}
    gd.flush_all()  # nothing pending: harmless


def test_background_timer_writes_without_an_explicit_flush(addons):
    w = gd.CompanionDataWriter(addons, min_interval=0.05)
    w.record_track("q:a\x1fb", {"notFound": True})
    deadline = time.time() + 3
    while w.writes == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert w.writes == 1 and data_of(addons)["tracks"] == {"q:a\x1fb": {"notFound": True}}
