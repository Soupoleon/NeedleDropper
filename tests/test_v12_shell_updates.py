"""The daily update check (DESIGN-v1.2 1.4 step 5): off while ``updates.releasesUrl`` is empty
(no repository URL exists yet), GitHub repository / releases URLs map to the releases API,
https only, at most once per ``updates.checkHours`` across restarts, a newer release becomes one
tray notification whose click opens its page; nothing is ever installed. No network: the fetcher
is injected."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import make_shell, wait_for  # noqa: E402

from needledropper import __version__, panel_api, tray  # noqa: E402
from needledropper.panel_api import UpdateChecker, parse_release, releases_api_url  # noqa: E402

API = "https://api.github.com/repos/owner/needledropper/releases/latest"


@pytest.mark.parametrize("url, api", [
    ("", None), ("   ", None), (None, None),
    ("https://github.com/owner/needledropper", API),
    ("https://github.com/owner/needledropper/", API),
    ("https://github.com/owner/needledropper.git", API),
    ("https://github.com/owner/needledropper/releases", API),
    ("https://github.com/owner/needledropper/releases/latest", API),
    ("https://example.org/needledropper/latest.json", "https://example.org/needledropper/latest.json"),
    ("http://github.com/owner/needledropper", None),  # https only
    ("ftp://example.org/x", None), ("https://exa mple.org/x", None),
])
def test_releases_url_mapping(url, api):
    assert releases_api_url(url) == api


def test_parse_release_skips_drafts_and_prereleases():
    assert parse_release({"tag_name": "v1.3.0", "html_url": "https://github.com/o/r/releases/tag/v1.3.0"}) == \
        panel_api.UpdateInfo("1.3.0", "https://github.com/o/r/releases/tag/v1.3.0", "")
    items = [{"tag_name": "v2.0.0", "draft": True}, {"tag_name": "v1.9.0-beta", "prerelease": True},
             {"tag_name": "1.4.1", "html_url": "http://insecure.example/x", "name": "Spring"}]
    info = parse_release(items)
    assert info.version == "1.4.1" and info.url == "" and info.name == "Spring"
    assert parse_release({"tag_name": "nightly"}) is None and parse_release("junk") is None


def test_disabled_when_empty(tmp_path):
    calls: list = []
    checker = UpdateChecker("", "1.2.0", state_file=tmp_path / "u.json", fetch=calls.append)
    assert not checker.enabled and not checker.due() and checker.check() is None and calls == []
    assert checker.to_dict()["enabled"] is False


def test_checks_at_most_once_per_interval_across_restarts(tmp_path):
    now = [1_000_000.0]
    calls: list = []

    def fetch(url):
        calls.append(url)
        return {"tag_name": "v9.9.9", "html_url": "https://github.com/owner/needledropper/releases/tag/v9.9.9"}

    state = tmp_path / "update-check.json"
    first = UpdateChecker("https://github.com/owner/needledropper", "1.2.0", state_file=state, fetch=fetch,
                          interval_hours=24, now=lambda: now[0])
    assert first.due()
    info = first.check()
    assert info.version == "9.9.9" and calls == [API]
    assert json.loads(state.read_text(encoding="utf-8"))["latest"] == "9.9.9"
    again = UpdateChecker("https://github.com/owner/needledropper", "1.2.0", state_file=state, fetch=fetch,
                          now=lambda: now[0])
    now[0] += 3600
    assert not again.due()  # a restart an hour later does not ask again
    now[0] += 24 * 3600
    assert again.due()


def test_same_or_older_releases_are_not_updates(tmp_path):
    for tag in (f"v{__version__}", "v0.0.1"):
        checker = UpdateChecker("https://github.com/o/r", __version__, state_file=tmp_path / "s.json",
                                fetch=lambda url, tag=tag: {"tag_name": tag})
        assert checker.check() is None and checker.to_dict()["available"] is False


def test_network_errors_are_remembered_not_raised(tmp_path):
    def fetch(url):
        raise OSError("offline")

    checker = UpdateChecker("https://github.com/o/r", "1.2.0", state_file=tmp_path / "s.json", fetch=fetch)
    assert checker.check() is None and checker.last_error == "offline" and not checker.due()


def test_fetch_json_refuses_plain_http():
    with pytest.raises(ValueError):
        panel_api.fetch_json("http://example.org/latest.json")


def test_a_newer_release_is_one_notification_that_opens_its_page(tmp_path, _needledropper_home, monkeypatch):
    fetched: list = []

    def fetch(url):
        fetched.append(url)
        return {"tag_name": "v99.0.0", "html_url": "https://github.com/owner/needledropper/releases/tag/v99.0.0"}

    opened: list = []
    monkeypatch.setattr(panel_api.webbrowser, "open", lambda url: opened.append(url) or True)
    ns = make_shell(tmp_path, Path(_needledropper_home), update_fetch=fetch,
                    cfg_extra={"updates": {"releasesUrl": "https://github.com/owner/needledropper"}})
    try:
        assert wait_for(lambda: tray.NOTE_UPDATE in ns.app.notifier.kinds(), timeout=5)
        note = [n for n in ns.app.notifier.history if n.kind == tray.NOTE_UPDATE][0]
        assert "99.0.0" in note.text and __version__ in note.text
        ns.app.notifier.clicked()
        assert opened == ["https://github.com/owner/needledropper/releases/tag/v99.0.0"]
        st, state = ns.call("GET", "/api/state")
        assert state["update"]["available"] and state["update"]["version"] == "99.0.0"
        assert fetched == [API]
        ns.app._hk_wake.set()  # more housekeeping rounds: no second request, no second notification
        assert not wait_for(lambda: len(fetched) > 1, timeout=0.5)
        assert ns.app.notifier.kinds().count(tray.NOTE_UPDATE) == 1
    finally:
        ns.close()


def test_no_check_and_no_network_without_a_url(tmp_path, _needledropper_home):
    calls: list = []
    ns = make_shell(tmp_path, Path(_needledropper_home), update_fetch=calls.append)
    try:
        ns.app._hk_wake.set()
        assert not wait_for(lambda: bool(calls), timeout=0.4)
        assert ns.call("GET", "/api/state")[1]["update"]["enabled"] is False
    finally:
        ns.close()
