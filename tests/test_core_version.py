"""One version everywhere (lead decision D15): the companion's ``__version__``, its pyproject.toml,
the addon TOC's literal ``## Version:`` line and the newest CHANGELOG entry name the same release.
A 1.2.0 app and a 1.2.1 addon cannot read each other's frames (DESIGN-v1.2.1 2.11), so the two
are released together under one number."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "companion" / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "companion/pyproject.toml has no version"
    return m.group(1)


def _toc_version() -> str:
    text = (ROOT / "addon" / "NeedleDropper" / "NeedleDropper.toc").read_text(encoding="utf-8-sig")
    m = re.search(r"^## Version:[ \t]*(\S+)[ \t]*$", text, re.M)
    assert m, "the addon TOC has no ## Version: line"
    return m.group(1)


def _changelog_newest() -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", text, re.M)
    assert m, "CHANGELOG.md has no released entry"
    return m.group(1)


def test_the_companion_is_1_2_1():
    from needledropper import __version__

    assert __version__ == "1.2.1"
    assert _pyproject_version() == __version__


def _addon_fallback_version() -> str:
    """Core.lua's ``ns.VERSION`` (what the addon reports when the client gives no metadata)."""
    text = (ROOT / "addon" / "NeedleDropper" / "Core.lua").read_text(encoding="utf-8")
    m = re.search(r'^ns\.VERSION = "([^"]+)"', text, re.M)
    assert m, "Core.lua sets no ns.VERSION"
    return m.group(1)


def test_app_addon_and_changelog_agree():
    from needledropper import __version__

    # a literal (release.yml also accepts @project-version@, which this release does not use)
    assert re.fullmatch(r"\d+\.\d+\.\d+", _toc_version()), _toc_version()
    found = {"app": __version__, "pyproject": _pyproject_version(), "toc": _toc_version(),
             "addon": _addon_fallback_version(), "changelog": _changelog_newest()}
    assert found == dict.fromkeys(found, __version__)
