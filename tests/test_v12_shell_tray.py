"""Tray mode end to end (DESIGN-v1.2 1.4): ``NeedleDropper.exe`` without a command builds the
``tray.companion_menu`` (status line, Open setup..., Pause needle drops, Start with Windows,
Open log folder, Quit) around the running companion, swaps the icon while paused, keeps the
tooltip current and routes notification clicks. A recording fake replaces the Win32 tray icon;
the engine is a fake; the registry is a fake; no browser or Explorer window opens."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import FakeEngine, FakeTrayIcon, FakeWinreg, make_fake_wow, wait_for  # noqa: E402

from needledropper import autostart, cli, panel_api, protocol2, resources, tray  # noqa: E402
from needledropper.models import EventType, Flags  # noqa: E402


@pytest.fixture
def tray_app(_needledropper_home, tmp_path, monkeypatch):
    """``cli.main([])`` on a background thread, with every outside effect recorded."""
    home = Path(_needledropper_home)
    seen = {"urls": [], "opened": [], "engines": [], "reg": FakeWinreg(), "dialogs": []}
    monkeypatch.setattr(cli, "open_url", lambda url: seen["urls"].append(url) or True)
    monkeypatch.setattr(cli, "tray_factory", FakeTrayIcon)
    monkeypatch.setattr(cli, "fatal_dialog", lambda *a: seen["dialogs"].append(a))
    monkeypatch.setattr(cli, "TOOLTIP_REFRESH_S", 0.05)
    monkeypatch.setattr(autostart, "_registry", lambda winreg=None: winreg or seen["reg"])
    monkeypatch.setattr(panel_api, "default_opener", seen["opened"].append)

    def fake_engine(cls, providers, deck, kw):
        engine = FakeEngine(providers, deck, **kw)
        seen["engines"].append(engine)
        return engine

    monkeypatch.setattr(panel_api, "construct_engine", fake_engine)
    (home / "config.json").write_text(json.dumps({
        "wowRoot": str(make_fake_wow(tmp_path / "WoW")), "deck": {"port": 0, "panelOnStart": "never"},
        "transport": {"pixel": {"enabled": False}, "chatlog": {"enabled": False}}}), encoding="utf-8")
    FakeTrayIcon.instances.clear()
    result: list[int] = []
    t = threading.Thread(target=lambda: result.append(cli.main([])), daemon=True)
    t.start()
    assert wait_for(lambda: FakeTrayIcon.instances and FakeTrayIcon.instances[-1].started, timeout=10)
    icon = FakeTrayIcon.instances[-1]
    app = icon.on_click.__self__
    ns = type("TrayRig", (), {})()
    ns.icon, ns.app, ns.seen, ns.thread, ns.result, ns.home = icon, app, seen, t, result, home
    try:
        yield ns
    finally:
        if t.is_alive():
            icon.pick(tray.ITEM_QUIT)
            t.join(10)


def test_the_tray_menu_is_the_companion_menu(tray_app):
    icon, app = tray_app.icon, tray_app.app
    assert icon.title == "NeedleDropper" and icon.icon_path == resources.tray_icon_path(False)
    assert icon.lines() == [
        "Waiting for World of Warcraft (disabled)", "---",
        "Open setup… (default)", "---",
        "[ ] Pause needle drops", "[ ] Start with Windows", "---",
        "Open log folder", "---",
        "Quit",
    ]
    assert icon.tooltip.startswith("NeedleDropper - Waiting for World of Warcraft")
    assert icon.on_notification_click == app.notifier.clicked


def test_left_click_and_open_setup_open_the_connect_page(tray_app):
    urls = tray_app.seen["urls"]
    tray_app.icon.on_click()
    tray_app.icon.pick(tray.ITEM_SETUP)
    assert urls == [tray_app.app.deck.panel_url] * 2 and urls[0].startswith("http://127.0.0.1:")


def test_pause_keeps_frames_from_the_engine_and_swaps_the_icon(tray_app):
    icon, app = tray_app.icon, tray_app.app
    engine = tray_app.seen["engines"][-1]
    fire = protocol2.build_frame(EventType.FIRE, seq=1, drop_id="zone:1429", youtube="dQw4w9WgXcQ")
    app.submit(fire, "pixel")
    assert [e for e, _t in engine.submitted] == [fire]
    icon.pick(tray.ITEM_PAUSE)
    assert app.paused and engine.stop_playback_calls == 1
    assert icon.icon_path == resources.tray_icon_path(True)
    assert "[x] Pause needle drops" in icon.lines()
    assert wait_for(lambda: icon.tooltip == "NeedleDropper - Needle drops paused")
    app.submit(protocol2.build_frame(EventType.FIRE, seq=2, drop_id="zone:1411", youtube="dQw4w9WgXcQ"), "pixel")
    test = protocol2.build_frame(EventType.PING, seq=3, flags=int(Flags.TEST))
    app.submit(test, "chatlog")
    assert len(engine.submitted) == 1  # nothing reaches the engine while paused...
    assert app.link_check()["heard"]  # ...but the link check still ticks
    icon.pick(tray.ITEM_PAUSE)
    assert not app.paused and icon.icon_path == resources.tray_icon_path(False)
    app.submit(protocol2.build_frame(EventType.HELLO, seq=4), "pixel")
    assert len(engine.submitted) == 2


def test_start_with_windows_item_writes_the_run_value(tray_app):
    icon, reg = tray_app.icon, tray_app.seen["reg"]
    icon.pick(tray.ITEM_AUTOSTART)
    command = autostart.get_command(winreg=reg)
    assert command is not None and command.endswith(" --background")
    assert "[x] Start with Windows" in icon.lines()
    cfg = json.loads((tray_app.home / "config.json").read_text(encoding="utf-8"))
    assert cfg["startWithWindows"] is True
    icon.pick(tray.ITEM_AUTOSTART)
    assert autostart.get_command(winreg=reg) is None and "[ ] Start with Windows" in icon.lines()
    assert json.loads((tray_app.home / "config.json").read_text(encoding="utf-8"))["startWithWindows"] is False


def test_open_log_folder(tray_app):
    tray_app.icon.pick(tray.ITEM_LOG_FOLDER)
    logs = tray_app.home / "logs"
    assert tray_app.seen["opened"] == [str(logs)] and (logs / "needledropper.log").is_file()


def test_notifications_go_to_the_icon_and_clicks_open_setup(tray_app):
    icon, app = tray_app.icon, tray_app.app
    app.submit(protocol2.build_frame(EventType.HELLO, seq=1), "pixel")
    app.submit(protocol2.build_frame(EventType.HELLO, seq=2), "pixel")
    assert [t for (_title, t, _k) in icon.notes] == [
        "Linked to World of Warcraft. Your needle drops will play from here; NeedleDropper stays in the tray."]
    app.submit(protocol2.build_frame(EventType.PING, seq=3, flags=int(Flags.TEST)), "pixel")
    assert len(icon.notes) == 2 and "Test signal received" in icon.notes[-1][1]
    assert wait_for(lambda: icon.tooltip.startswith("NeedleDropper - Linked to World of Warcraft"))
    icon.on_notification_click()
    assert tray_app.seen["urls"] == [app.deck.panel_url]


def test_quit_ends_the_companion(tray_app):
    tray_app.icon.pick(tray.ITEM_QUIT)
    tray_app.thread.join(10)
    assert not tray_app.thread.is_alive() and tray_app.result == [0]
    assert tray_app.icon.stopped and tray_app.seen["engines"][-1].stopped
    assert not tray_app.app.started


def test_windows_closing_the_app_quits_too(tray_app):
    tray_app.icon.on_close()  # WM_CLOSE from an installer / the session ending
    tray_app.thread.join(10)
    assert tray_app.result == [0]
