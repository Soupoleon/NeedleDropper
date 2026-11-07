"""Profile singleton regressions; no real processes, windows or app data are touched."""
from __future__ import annotations

import dataclasses
import logging
import types

import pytest

from needledropper.deck import server
from needledropper.deck.profile import BrowserProcess, ProfileGuard, WindowsProcesses
from test_v12_review_fixes_player import Proc, make_window

IMAGE = r"C:\Browser\chrome.exe"


def browser(profile, pid=40, parent=1, created=100, app="http://localhost:8322/player#k=example", image=IMAGE):
    return BrowserProcess(pid, parent, image, created,
                          (image, "--user-data-dir=" + str(profile), "--app=" + app))


class ProcessTable:
    def __init__(self, *rows):
        self.rows = {p.pid: p for p in rows}
        self.calls = []
        self.held_lock = False
        self.now = 0.0
        self.on_sleep = None

    def snapshot(self):
        return list(self.rows.values())

    def same(self, process):
        return self.rows.get(process.pid) == process

    def close_windows(self, process):
        if self.same(process):
            self.calls.append(("close", process.pid))

    def terminate(self, process):
        if self.same(process):
            self.calls.append(("end", process.pid))
            self.rows.pop(process.pid)
            return True
        return False

    def locked(self, profile):
        return self.held_lock

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self)
            self.on_sleep = None

    def guard(self):
        return ProfileGuard(self, clock=lambda: self.now, sleep=self.sleep)


def test_stale_player_closes_then_ends_only_its_checked_tree_before_launch(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    old = browser(profile)
    child = dataclasses.replace(old, pid=41, parent=40, created=101, args=(IMAGE, "--type=renderer"))
    unrelated = browser(tmp_path / "other", pid=99)
    table = ProcessTable(old, child, unrelated)
    monkeypatch.setattr(server, "find_browser", lambda kind: IMAGE)
    d = server.Deck(launcher=lambda args: table.calls.append(("launch", args)) or Proc(200))
    d.profile_guard = table.guard()
    d.configure_player(browser="chrome", profile_dir=profile)
    assert d.open_player() == "chrome"
    assert table.calls[:4] == [("close", 40), ("close", 41), ("end", 41), ("end", 40)]
    assert table.calls[4][0] == "launch" and table.rows == {99: unrelated}
    assert table.now >= 3


@pytest.mark.parametrize("change", [
    {"app": "https://localhost/player"}, {"app": "http://localhost.evil.test/player"},
    {"app": "http://localhost:8322/other"}, {"app": "http://localhost@evil.test/player"},
    {"image": r"C:\Other\chrome.exe"}, {"created": 0},
])
def test_foreign_or_unverified_profile_holder_is_never_signalled(tmp_path, change):
    profile = tmp_path / "profile"
    table = ProcessTable(browser(profile, **change))
    fresh = table.guard().select(IMAGE, profile)
    assert fresh != profile and fresh.parent == profile.parent
    assert table.calls == []


def test_matching_requires_exact_profile_not_a_prefix(tmp_path):
    profile = tmp_path / "profile"
    table = ProcessTable(browser(tmp_path / "profile-other"))
    assert table.guard().select(IMAGE, profile) == profile
    assert table.calls == []


def test_unknown_held_lock_uses_fresh_profile_and_copies_widevine(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    table, copies, launches = ProcessTable(), [], []
    table.held_lock = True
    monkeypatch.setattr(server, "find_browser", lambda kind: IMAGE)
    d = server.Deck(launcher=lambda args: launches.append(args))
    d.profile_guard = table.guard()
    d.widevine_copier = copies.append
    d.configure_player(browser="chrome", profile_dir=profile)
    assert d.open_player() == "chrome" and d.profile_dir != profile
    assert copies == [d.profile_dir]
    assert "--user-data-dir=" + str(d.profile_dir) in launches[0]
    assert table.calls == []


def test_reused_pid_during_grace_period_is_not_terminated(tmp_path):
    profile = tmp_path / "profile"
    old = browser(profile)
    replacement = dataclasses.replace(old, created=200, args=(IMAGE, "https://example.test"))
    table = ProcessTable(old)
    table.on_sleep = lambda t: t.rows.update({old.pid: replacement})
    table.guard().retire(old)
    assert table.calls == [("close", 40)]
    assert table.rows[40] == replacement


def test_process_inspection_failure_isolates_the_profile(tmp_path):
    table = ProcessTable()
    table.snapshot = lambda: (_ for _ in ()).throw(OSError("unavailable"))
    assert table.guard().select(IMAGE, tmp_path / "profile") != tmp_path / "profile"
    assert table.calls == []


def test_game_becomes_foreground_during_profile_inspection_prevents_launch(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(server, "find_browser", lambda kind: IMAGE)
    d = server.Deck(launcher=launched.append)
    d.configure_player(profile_dir=tmp_path / "profile")
    foreground = iter((False, True))
    d.game_in_front = lambda: next(foreground)
    d.profile_guard = ProcessTable().guard()
    assert d.open_player() == "none" and launched == []


def test_handoff_identity_is_retained_and_closed_even_when_launcher_exited(tmp_path, monkeypatch, caplog):
    profile = tmp_path / "profile"
    holder = browser(profile, pid=4321)
    table = ProcessTable(holder)
    win32, window = make_window()
    win32.windows = [(11, 4321, "Chrome_WidgetWin_1", "NeedleDropper", True, False)]
    win32.alive = {11}
    d = server.Deck()
    d.window, d.profile_dir, d._player_image = window, profile, IMAGE
    d.profile_guard = table.guard()
    d._player_proc = Proc(100, 0)
    monkeypatch.setattr(server.threading, "Thread", lambda target, **kw: types.SimpleNamespace(start=target))
    with caplog.at_level(logging.WARNING):
        d._adopt_later(d._player_proc)
    assert d._adopted_process == holder and window.hwnd == 11
    assert "handed off to browser pid 4321" in caplog.text
    d._end_player_process(0)
    assert table.calls == [("close", 4321), ("end", 4321)]


def test_title_match_cannot_adopt_a_foreign_profile(tmp_path):
    win32, window = make_window()
    win32.windows = [(11, 4321, "Chrome_WidgetWin_1", "NeedleDropper", True, False)]
    win32.alive = {11}
    table = ProcessTable(browser(tmp_path / "foreign", pid=4321))
    guard = table.guard()
    assert not window.adopt(100, timeout=0, accept_handoff=lambda p: bool(guard.identify(p, IMAGE, tmp_path / "ours")))
    assert win32.calls == []


def test_stale_page_repeated_connections_show_only_one_notice():
    d, notices = server.Deck(), []
    d.on_stale_player = notices.append
    for _ in range(4):
        d._note_stale_player()
    assert notices == ["An old NeedleDropper window is open: close it."]


@pytest.mark.parametrize("metadata", [(IMAGE, 101), (r"C:\Other\chrome.exe", 100), ("", 0)])
def test_native_termination_checks_creation_and_image_on_the_handle(tmp_path, metadata):
    calls = []
    adapter = object.__new__(WindowsProcesses)
    adapter.wt = None
    adapter.kernel = types.SimpleNamespace(OpenProcess=lambda *args: 7,
                                            CloseHandle=lambda h: calls.append(("close", h)),
                                            TerminateProcess=lambda *args: calls.append(("end", args)))
    adapter._metadata = lambda handle: metadata
    assert not adapter.terminate(browser(tmp_path / "profile"))
    assert calls == [("close", 7)]
