"""Start with Windows (DESIGN-v1.2 1.4): ticked by default, written by the Connect page's first
Done (never before), re-checked at every start while the player wants it (a lost Run value is
restored, a moved program followed, Task Manager's switch never overridden). Fake registry only."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import FakeWinreg, make_shell, raw_config  # noqa: E402

from needledropper import autostart  # noqa: E402

RUN, APPROVED = autostart.RUN_KEY, autostart.STARTUP_APPROVED_KEY


@pytest.fixture
def shell(tmp_path, _needledropper_home):
    ns = make_shell(tmp_path, Path(_needledropper_home))
    try:
        yield ns
    finally:
        ns.close()


def test_nothing_is_registered_before_the_first_done(shell):
    assert autostart.get_command(winreg=shell.reg) is None
    assert not any(c[0] == "SetValueEx" for c in shell.reg.calls)
    st, state = shell.call("GET", "/api/state")
    assert state["autostart"] == {"available": True, "enabled": False, "choice": None}


def test_the_first_done_with_the_default_tick_turns_it_on(shell):
    # the page sends the footer's checkbox, ticked by default
    st, data = shell.call("POST", "/api/config", {"setupComplete": True, "startWithWindows": True})
    assert st == 200 and data["warnings"] == []
    command = autostart.get_command(winreg=shell.reg)
    assert command is not None and command.startswith('"') and command.endswith(" --background")
    assert autostart.is_enabled(winreg=shell.reg)
    raw = raw_config(shell.cfg_file)
    assert raw["setupComplete"] is True and raw["startWithWindows"] is True
    state = data["state"]
    assert state["autostart"] == {"available": True, "enabled": True, "choice": True}
    assert state["app"]["firstRun"] is False  # the Connect page no longer opens by itself


def test_done_with_the_box_unticked_leaves_it_off(shell):
    st, data = shell.call("POST", "/api/config", {"setupComplete": True, "startWithWindows": False})
    assert st == 200 and autostart.get_command(winreg=shell.reg) is None
    assert data["state"]["autostart"]["choice"] is False and data["state"]["app"]["firstRun"] is False


def test_a_registry_refusal_is_reported_not_fatal(shell):
    shell.reg.deny.add("SetValueEx")
    st, data = shell.call("POST", "/api/config", {"setupComplete": True, "startWithWindows": True})
    assert st == 200 and data["warnings"] and "Start with Windows" in data["warnings"][0]
    assert raw_config(shell.cfg_file)["setupComplete"] is True


def start_again(tmp_path, home, reg, **cfg) -> object:
    ns = make_shell(tmp_path, home, cfg_extra=cfg, start=False, autostart_winreg=reg)
    ns.app.start()
    return ns


def test_every_start_restores_a_lost_entry_for_a_player_who_wants_it(tmp_path, _needledropper_home):
    reg = FakeWinreg()
    ns = start_again(tmp_path, Path(_needledropper_home), reg, setupComplete=True, startWithWindows=True)
    try:
        command = autostart.get_command(winreg=reg)
        assert command is not None and command.endswith("--background")  # restored
        assert ns.app.recheck_autostart() == autostart.ENSURE_OK  # already right: untouched
    finally:
        ns.close()


def test_every_start_follows_a_moved_program_but_respects_task_manager(tmp_path, _needledropper_home):
    reg = FakeWinreg()
    reg.put(RUN, autostart.VALUE_NAME, r'"C:\Old\NeedleDropper.exe" --background', reg.REG_SZ)
    reg.put(APPROVED, autostart.VALUE_NAME, b"\x03" + b"\x00" * 11, reg.REG_BINARY)  # switched off in Task Manager
    ns = start_again(tmp_path, Path(_needledropper_home), reg, setupComplete=True, startWithWindows=True)
    try:
        command = autostart.get_command(winreg=reg)
        assert command != r'"C:\Old\NeedleDropper.exe" --background' and command.endswith("--background")
        assert reg.get(APPROVED, autostart.VALUE_NAME) is not None  # the player's Task Manager choice stays
        assert not autostart.is_enabled(winreg=reg)
        assert ns.call("GET", "/api/state")[1]["autostart"]["enabled"] is False  # the page shows the truth
    finally:
        ns.close()


@pytest.mark.parametrize("cfg", [{"setupComplete": True, "startWithWindows": False}, {"startWithWindows": True},
                                 {"setupComplete": True}])
def test_no_start_touches_the_registry_unless_the_player_chose_it(tmp_path, _needledropper_home, cfg):
    reg = FakeWinreg()
    ns = start_again(tmp_path, Path(_needledropper_home), reg, **cfg)
    try:
        assert autostart.get_command(winreg=reg) is None
        assert not any(c[0] in ("SetValueEx", "DeleteValue", "CreateKeyEx") for c in reg.calls)
    finally:
        ns.close()
