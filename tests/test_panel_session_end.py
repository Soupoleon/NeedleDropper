"""CLI-CONSOLE-CLOSE-NO-CLEANUP (docs/review/baseline-findings.md), for the v1.2 windowed + tray app:
Quit, log off, shut down and closing a ``run`` console all stop playback cleanly (Spotify paused, its
volume and the player's own music restored) *before* Windows ends the process.

No real tray icon, console handler, browser or registry: fakes injected through cli's seams.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import FakeTrayIcon, FakeWinreg, make_fake_wow  # noqa: E402

from needledropper import autostart, cli, panel_api, tray  # noqa: E402


@pytest.fixture
def home(_needledropper_home):
    return Path(_needledropper_home)


class FakeConsoleApi:
    """Records SetConsoleCtrlHandler calls (the real one would hook this test process's console)."""

    def __init__(self) -> None:
        self.handlers: list = []
        self.removed: list = []

    def SetConsoleCtrlHandler(self, handler, add):  # noqa: N802 - Win32 name
        (self.handlers if add else self.removed).append(handler)
        return 1


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    seen = {"urls": [], "dialogs": [], "reg": FakeWinreg(), "console": FakeConsoleApi(), "stops": []}
    monkeypatch.setattr(cli, "open_url", seen["urls"].append)
    monkeypatch.setattr(cli, "tray_factory", FakeTrayIcon)
    monkeypatch.setattr(cli, "fatal_dialog", lambda title, text: seen["dialogs"].append((title, text)))
    monkeypatch.setattr(cli, "console_ctrl_api", seen["console"])
    monkeypatch.setattr(autostart, "_registry", lambda winreg=None: winreg or seen["reg"])
    real_stop = panel_api.Companion.stop

    def recording_stop(self, *, fast=False):
        seen["stops"].append(("start", fast, time.monotonic()))
        real_stop(self, fast=fast)
        seen["stops"].append(("end", fast, time.monotonic()))

    monkeypatch.setattr(panel_api.Companion, "stop", recording_stop)
    FakeTrayIcon.instances.clear()
    return seen


def write_config(home: Path, tmp_path: Path, **cfg) -> None:
    base = {"wowRoot": str(make_fake_wow(tmp_path / "WoW")), "deck": {"port": 0, "panelOnStart": "never"},
            "transport": {"pixel": {"enabled": False}, "chatlog": {"enabled": False}}}
    base.update(cfg)
    (home / "config.json").write_text(json.dumps(base), encoding="utf-8")


def run_in_thread(argv):
    result = {}

    def target():
        result["code"] = cli.main(argv)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, result


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_log_off_runs_the_fast_clean_up_before_the_tray_handler_returns(home, tmp_path, hermetic):
    write_config(home, tmp_path)
    t, result = run_in_thread([])
    assert wait_for(lambda: FakeTrayIcon.instances and FakeTrayIcon.instances[-1].started)
    icon = FakeTrayIcon.instances[-1]
    session_end = icon.kw.get("on_session_end") if hasattr(icon, "kw") else None
    assert callable(session_end), "the tray must get a synchronous session-end callback"
    started = time.monotonic()
    session_end()                                        # what WM_ENDSESSION does on the tray thread
    returned = time.monotonic()
    stops = hermetic["stops"]
    assert [s[:2] for s in stops] == [("start", True), ("end", True)]  # fast stop, finished ...
    assert stops[-1][2] <= returned                      # ... before the handler returned
    assert returned - started < cli.SESSION_END_WAIT_S
    t.join(10)
    assert result["code"] == 0 and icon.stopped


def test_closing_the_console_window_cleans_up_too(home, tmp_path, hermetic):
    write_config(home, tmp_path)
    t, result = run_in_thread(["run"])
    console = hermetic["console"]
    assert wait_for(lambda: console.handlers)
    handler = console.handlers[-1]
    assert handler(0) == 0                               # Ctrl+C: Python's own KeyboardInterrupt handling
    assert handler(2) == 1                               # CTRL_CLOSE_EVENT: handled, after the clean-up
    assert [s[:2] for s in hermetic["stops"]] == [("start", True), ("end", True)]
    t.join(10)
    assert result["code"] == 0
    assert console.removed == [handler]                  # uninstalled on the way out


def test_quit_from_the_tray_is_the_normal_full_stop(home, tmp_path, hermetic):
    write_config(home, tmp_path)
    t, result = run_in_thread([])
    assert wait_for(lambda: FakeTrayIcon.instances and FakeTrayIcon.instances[-1].started)
    FakeTrayIcon.instances[-1].pick(tray.ITEM_QUIT)
    t.join(10)
    assert result["code"] == 0
    assert [s[:2] for s in hermetic["stops"]] == [("start", False), ("end", False)]


def test_the_tray_window_calls_the_session_end_hook_synchronously():
    calls = []
    icon = tray.TrayIcon("NeedleDropper", on_close=lambda: calls.append("close"),
                         on_session_end=lambda: calls.append("session-end"))
    assert icon._window_proc(0, tray.WM_QUERYENDSESSION, 0, 0) == 1   # never blocks a shutdown
    assert icon._window_proc(0, tray.WM_ENDSESSION, 0, 0) == 0        # session continues: nothing
    assert calls == []
    assert icon._window_proc(0, tray.WM_ENDSESSION, 1, 0) == 0
    assert calls == ["session-end"]                                   # ran before the proc returned


def test_the_fast_stop_stops_the_engine_first(tmp_path):
    order = []

    class Engine:
        def stop(self, timeout=10.0):
            order.append(("engine", timeout))

    class Transport:
        name = "pixel"

        def stop(self):
            order.append(("transport", None))

    from types import SimpleNamespace

    app = panel_api.Companion.__new__(panel_api.Companion)
    app._hk_stop, app._hk_wake, app._hk_thread = threading.Event(), threading.Event(), None
    app._lock = threading.RLock()
    app._login = None
    app.deck = SimpleNamespace(api=None, on_player_status=None)
    app.api = object()
    app.engine, app.transports, app.providers, app._side_spotify = Engine(), [Transport()], [], None
    app._started = True
    app.paths = SimpleNamespace(addons_dir=tmp_path / "missing")
    app.guard = panel_api.ThreadGuard(lambda *a: None)
    app.stop(fast=True)
    assert order == [("engine", 3.0), ("transport", None)]
