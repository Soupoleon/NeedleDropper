"""Atomic writes survive Windows' refusals (real-client finding #5, docs/review/real-client-2026-11-05.md).

On the owner's PC ``os.replace`` inside ``%APPDATA%\\NeedleDropper`` raised ``[WinError 17]
ERROR_NOT_SAME_DEVICE`` (a virtualised AppData under a packaged host). Every writer now goes
through :mod:`needledropper.atomicio`, which writes the file in place in that case and keeps
retrying a briefly locked target. Hermetic: everything lives in tmp_path.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import atomicio, config, gamedata, savedvars  # noqa: E402
from needledropper import secrets as nd_secrets  # noqa: E402
from needledropper.providers.spotify import _ResolveCache  # noqa: E402


def cross_device(*_a, **_k):
    exc = OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
    exc.winerror = 17
    raise exc


@pytest.fixture(autouse=True)
def _fresh_warnings():
    atomicio._warned.clear()
    yield
    atomicio._warned.clear()


def test_cross_device_rename_falls_back_to_writing_in_place(tmp_path, monkeypatch, caplog):
    target = tmp_path / "out.bin"
    target.write_bytes(b"old contents that are longer")
    monkeypatch.setattr(os, "replace", cross_device)
    caplog.set_level(logging.WARNING, logger="needledropper.files")
    assert atomicio.atomic_write_bytes(target, b"new") == target
    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]       # the temp file is gone
    atomicio.atomic_write_bytes(target, b"newer")
    assert target.read_bytes() == b"newer"
    assert caplog.text.count("writing it in place") == 1             # warned once per file


def test_is_cross_device_recognises_both_spellings():
    e17 = OSError(errno.EIO, "x")
    e17.winerror = 17
    assert atomicio.is_cross_device(e17)
    assert atomicio.is_cross_device(OSError(errno.EXDEV, "x"))
    assert not atomicio.is_cross_device(PermissionError(errno.EACCES, "x"))
    assert not atomicio.is_cross_device(ValueError("x"))


def test_locked_target_is_retried_then_reported(tmp_path, monkeypatch):
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(errno.EACCES, "locked")
        return real(src, dst)

    sleeps: list[float] = []
    monkeypatch.setattr(os, "replace", flaky)
    atomicio.atomic_write_bytes(tmp_path / "a.txt", b"x", sleep=sleeps.append, delay=0.01)
    assert (tmp_path / "a.txt").read_bytes() == b"x" and calls["n"] == 3 and sleeps == [0.01, 0.01]
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError(errno.EACCES, "locked")))
    with pytest.raises(PermissionError):
        atomicio.atomic_write_bytes(tmp_path / "b.txt", b"y", retries=3, sleep=lambda s: None)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_other_errors_are_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError(errno.ENOSPC, "disk full")))
    with pytest.raises(OSError) as ei:
        atomicio.atomic_write_bytes(tmp_path / "c.txt", b"z")
    assert ei.value.errno == errno.ENOSPC and list(tmp_path.iterdir()) == []


def test_every_writer_survives_winerror_17(tmp_path, monkeypatch):
    """config.json, the Spotify sign-in, NeedleDropper_Companion\\Data.lua, the resolve cache and the
    SavedVariables backups all go through the same helper."""
    monkeypatch.setattr(os, "replace", cross_device)
    # config.json
    cfg = tmp_path / "app" / "config.json"
    config.update_config_file({"setupComplete": True}, cfg)
    assert json.loads(cfg.read_text(encoding="utf-8"))["setupComplete"] is True
    # the DPAPI-protected secret
    secret = tmp_path / "app" / "spotify_token.bin"
    nd_secrets.save_secret(secret, {"refresh_token": "RT"})
    assert nd_secrets.load_secret(secret) == {"refresh_token": "RT"}
    # the Spotify resolve cache
    cache = _ResolveCache(tmp_path / "app" / "cache" / "resolved.json")
    cache.put("zone:1429", {"spotify": "spotify:track:" + "a" * 22})
    assert _ResolveCache(cache.path).get("zone:1429")["spotify"].endswith("a" * 22)
    # the data addon the game reads
    addons = tmp_path / "wow" / "_forever_" / "Interface" / "AddOns"
    (addons / "NeedleDropper").mkdir(parents=True)
    data_file = gamedata.write_companion_data(addons, {"schema": 1, "appVersion": "1.2.0", "written": 5,
                                                       "status": {"spotify": "connected", "youtube": True,
                                                                  "transports": {"pixel": True, "chatlog": False}},
                                                       "tracks": {}})
    assert "NeedleDropper_CompanionData" in data_file.read_text(encoding="utf-8")
    # SavedVariables backups
    sv = tmp_path / "wow" / "_forever_" / "WTF" / "Account" / "ACC" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "NeedleDropper.lua").write_text("NeedleDropperDB = {}\n", encoding="utf-8")
    made = savedvars.backup_savedvariables(tmp_path / "wow" / "_forever_", tmp_path / "app" / "backups", now=0)
    assert made and made[0].read_text(encoding="utf-8") == "NeedleDropperDB = {}\n"
    leftovers = [p for p in tmp_path.rglob("*.tmp")]
    assert leftovers == []
