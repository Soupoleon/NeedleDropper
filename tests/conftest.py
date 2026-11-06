"""Shared pytest setup for NeedleDropper.

* Puts ``companion/`` on ``sys.path`` so tests can ``import needledropper``.
* Relocates the companion app dir (``NEEDLEDROPPER_HOME``) to a fresh temp dir
  for every test, so nothing ever reads or writes the real ``%APPDATA%``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion"
if str(COMPANION) not in sys.path:
    sys.path.insert(0, str(COMPANION))


@pytest.fixture(autouse=True)
def _needledropper_home(tmp_path_factory, monkeypatch):
    """Every test gets its own empty app dir (never the user's real one)."""
    home = tmp_path_factory.mktemp("ndhome")
    monkeypatch.setenv("NEEDLEDROPPER_HOME", str(home))
    return home
