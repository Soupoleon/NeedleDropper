"""Shared pytest setup for NeedleDropper.

* Puts ``companion/`` on ``sys.path`` so tests can ``import needledropper``.
* Relocates the companion app dir (``NEEDLEDROPPER_HOME``) to a fresh temp dir
  for every test, so nothing ever reads or writes the real ``%APPDATA%``.
* Keeps the companion from finding the real World of Warcraft install.
* Refuses -- and fails the test for -- any write below the real World of Warcraft folder or the
  real ``%APPDATA%\\NeedleDropper``, wherever it comes from (a config without ``wowRoot`` falls
  back to the real default folder, so the redirections above alone are not enough).
"""
from __future__ import annotations

import builtins
import errno
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion"
if str(COMPANION) not in sys.path:
    sys.path.insert(0, str(COMPANION))

#: Folders no test may write into: the real game install and the real app data folder. Taken from
#: the environment when pytest starts, before any test redirects it.
_REAL_APPDATA = os.environ.get("APPDATA", "").strip()
_PROTECTED = tuple(
    os.path.normcase(os.path.abspath(p)) for p in (
        r"C:\Program Files (x86)\World of Warcraft",
        *((os.path.join(_REAL_APPDATA, "NeedleDropper"),) if _REAL_APPDATA else ()),
    )
)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _protected(path) -> bool:
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False  # a file descriptor
    try:
        full = os.path.normcase(os.path.abspath(os.fsdecode(path)))
    except (TypeError, ValueError):
        return False
    return any(full == root or full.startswith(root + os.sep) for root in _PROTECTED)


@pytest.fixture(autouse=True)
def _no_writes_to_real_user_folders(monkeypatch):
    """Every test: opening for writing, creating, renaming or deleting anything below the real WoW
    folder or the real ``%APPDATA%\\NeedleDropper`` raises PermissionError (so the product's own
    error handling runs instead of the write) and fails the test afterwards, even when the attempt
    happened on a background thread that swallowed the error."""
    attempts: list[str] = []

    def refuse(what: str, path) -> None:
        attempts.append(f"{what} {os.fsdecode(path)!r}")
        raise PermissionError(errno.EACCES, f"tests must not write here ({what})", os.fsdecode(path))

    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+") and _protected(file):
            refuse(f"open(mode={mode!r})", file)
        return real_open(file, mode, *args, **kwargs)

    real_os_open = os.open

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & _WRITE_FLAGS and _protected(path):
            refuse("os.open", path)
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    for name in ("mkdir", "makedirs", "replace", "rename", "remove", "unlink", "rmdir", "utime", "truncate"):
        real = getattr(os, name)

        def guard(*args, _real=real, _name=name, **kwargs):
            for arg in args[:2]:
                if _protected(arg):
                    refuse(f"os.{_name}", arg)
            return _real(*args, **kwargs)

        monkeypatch.setattr(os, name, guard)
    yield
    assert not attempts, "a test tried to write into a real user folder: " + "; ".join(attempts[:5])


@pytest.fixture(autouse=True)
def _needledropper_home(tmp_path_factory, monkeypatch):
    """Every test gets its own empty app dir (never the user's real one)."""
    home = tmp_path_factory.mktemp("ndhome")
    monkeypatch.setenv("NEEDLEDROPPER_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _no_real_wow_detection(tmp_path_factory, monkeypatch):
    """The companion never finds the real World of Warcraft install from a test.

    When a test's configured folder has no ``_forever_``, the running companion looks for the
    game itself -- the Blizzard registry keys, then the default install folder -- and then
    creates its ``NeedleDropper_Companion`` data addon there. On a PC with WoW installed that
    wrote into the real ``Interface\\AddOns`` (seen during the v1.2 integration). Tests that
    exercise the detection inject their own reader and default (``wow_registry=`` /
    ``wow_default=``), which this leaves alone.
    """
    try:
        from needledropper import config, panel_api
    except ImportError:  # pragma: no cover - a broken package fails its own tests
        return
    nowhere = tmp_path_factory.mktemp("no-wow") / "World of Warcraft"
    monkeypatch.setattr(config, "read_registry_value", lambda *a, **k: None)
    monkeypatch.setattr(panel_api, "DEFAULT_WOW_ROOT", str(nowhere))


@pytest.fixture(autouse=True)
def _no_real_desktop_windows(monkeypatch):
    """No test touches the desktop: the player window manager (deck/window.py) is off, nothing
    is considered "the game in front", and the Spotify app is never really launched. Tests of
    those pieces inject fakes."""
    monkeypatch.setenv("NEEDLEDROPPER_NO_WINDOW_MANAGER", "1")
    try:
        from needledropper.deck import server
        from needledropper.providers import spotify
    except ImportError:  # pragma: no cover
        return
    monkeypatch.setattr(server, "_game_in_front", lambda: False)
    monkeypatch.setattr(spotify, "_default_game_in_front", lambda: False)
    # No real browser either: the app now makes its hidden player window when it starts.
    monkeypatch.setattr(server, "_default_launcher", lambda args, log_file=None: None)
