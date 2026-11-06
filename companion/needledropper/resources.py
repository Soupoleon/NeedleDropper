"""Where the companion finds its bundled resources (source checkout or frozen exe).

Two layouts are supported:

* **frozen** (``NeedleDropper.exe`` built with PyInstaller, ``sys.frozen`` is
  true): everything is unpacked below ``sys._MEIPASS`` (the one-file exe's temp
  folder, or the one-folder build's ``_internal`` folder)::

      <root>/needledropper/deck/static/   Connect page + player pages
      <root>/needledropper/assets/        tray icons (*.ico)
      <root>/packs/*.json                 bundled soundtrack packs (for `validate` / `test-drop`)
      <root>/addon/NeedleDropper/         addon sources (incl. Sets/) for the "Install addon" fallback

* **source checkout** (``python -m needledropper``): the repository layout,
  ``<repo>/companion/needledropper/deck/static``, ``<repo>/companion/needledropper/assets``,
  ``<repo>/packs`` and ``<repo>/addon/NeedleDropper``.

User data never lives here: config, caches, tokens and logs always go to
``%APPDATA%\\NeedleDropper`` (or ``$NEEDLEDROPPER_HOME``), see :mod:`.config`.
The packaging spec (PyInstaller ``datas``) must match this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_PACK_NAME = "azeroth-unbound.json"
TRAY_ICON_NAME = "NeedleDropper.ico"
TRAY_PAUSED_ICON_NAME = "NeedleDropper-paused.ico"

_HERE = Path(__file__).resolve()


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle (``NeedleDropper.exe``)."""
    return bool(getattr(sys, "frozen", False))


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS"))


def repo_root() -> Path:
    """The source checkout root (``<repo>``); only meaningful when not frozen."""
    return _HERE.parents[2]


def static_dir() -> Path:
    """Folder with the Connect page / player pages (``deck/static``)."""
    if is_frozen():
        return _bundle_root() / "needledropper" / "deck" / "static"
    return repo_root() / "companion" / "needledropper" / "deck" / "static"


def assets_dir() -> Path:
    """Folder with the tray icons (``needledropper/assets``)."""
    if is_frozen():
        return _bundle_root() / "needledropper" / "assets"
    return _HERE.parent / "assets"


def tray_icon_path(paused: bool = False) -> Path:
    """The tray icon, or its "needle drops paused" variant."""
    return assets_dir() / (TRAY_PAUSED_ICON_NAME if paused else TRAY_ICON_NAME)


def packs_dir() -> Path:
    """Folder with the bundled soundtrack packs."""
    if is_frozen():
        return _bundle_root() / "packs"
    return repo_root() / "packs"


def default_pack_path() -> Path:
    """The legacy (v1.1) starter pack ``packs/azeroth-unbound.json`` (``validate``'s default input)."""
    return packs_dir() / DEFAULT_PACK_NAME


def addon_source_dir() -> Path:
    """The addon copied by the "Install addon" fallback (``addon/NeedleDropper``, incl. ``Sets/``)."""
    if is_frozen():
        return _bundle_root() / "addon" / "NeedleDropper"
    return repo_root() / "addon" / "NeedleDropper"


def app_version() -> str:
    """The companion version (``needledropper.__version__``)."""
    from . import __version__

    return __version__


__all__ = ["DEFAULT_PACK_NAME", "TRAY_ICON_NAME", "TRAY_PAUSED_ICON_NAME", "is_frozen", "repo_root", "static_dir",
           "assets_dir", "tray_icon_path", "packs_dir", "default_pack_path", "addon_source_dir", "app_version"]
