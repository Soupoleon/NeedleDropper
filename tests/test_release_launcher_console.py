"""The windowed exe prints into the terminal that started it (CONTRACTS-v1.2 3.6, DESIGN-v1.2 1.4).

The one-file ``NeedleDropper.exe`` runs as two processes (PyInstaller's bootloader, then the same exe
as the Python process), so the Python process's parent is the console-less bootloader and
``AttachConsole(ATTACH_PARENT_PROCESS)`` fails even from a terminal. Found by running the built exe in
a real console during the v1.2 integration: ``NeedleDropper.exe --version`` opened a console window of
its own and waited for Enter. The launcher now attaches to the bootloader's parent (the terminal).
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "companion" / "launcher.py"


@pytest.fixture()
def launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("nd_console_launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "set_console_title", lambda *a, **k: False)
    monkeypatch.setattr(mod, "show_error", lambda *a, **k: False)
    return mod


class FakeKernel32:
    """AttachConsole succeeds only for ``consoles`` (pids that own a console); AllocConsole always."""

    def __init__(self, consoles):
        self.consoles = set(consoles)
        self.attach_calls, self.alloc_calls = [], 0
        k = self

        class Fn:
            def __init__(self, fn):
                self.fn = fn

            def __call__(self, *a):
                return self.fn(*a)

        self.AttachConsole = Fn(lambda pid: (k.attach_calls.append(pid), 1 if pid in k.consoles else 0)[1])
        self.AllocConsole = Fn(lambda: (setattr(k, "alloc_calls", k.alloc_calls + 1), 1)[1])


ME, BOOT, TERMINAL, EXPLORER = os.getpid(), 4100, 4000, 1234


def one_file_table(started_by: int, started_by_name: str):
    return {ME: (BOOT, "NeedleDropper.exe"), BOOT: (started_by, "NeedleDropper.exe"),
            started_by: (1, started_by_name)}


@pytest.fixture()
def frozen(monkeypatch, launcher):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Tools\NeedleDropper.exe")
    opened = []

    def fake_open(name, mode="r", **kw):
        opened.append(name)
        return io.StringIO()

    monkeypatch.setattr(launcher, "open", fake_open, raising=False)
    monkeypatch.setattr(launcher, "print", lambda *a, **k: None, raising=False)
    return opened


def no_stdout(monkeypatch):
    """A windowed exe has no stdout (set in the test body: pytest swaps the streams per phase)."""
    for stream in ("stdout", "stderr", "stdin"):
        monkeypatch.setattr(sys, stream, getattr(sys, stream))  # restored by monkeypatch afterwards
    monkeypatch.setattr(sys, "stdout", None)


def test_terminal_pid_is_the_bootloaders_parent(launcher, monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Tools\NeedleDropper.exe")
    monkeypatch.setattr(launcher, "process_parents", lambda k32, pid: one_file_table(TERMINAL, "cmd.exe"))
    assert launcher.terminal_pid(object()) == TERMINAL
    # one-folder exe / python launcher.py: the parent is the terminal itself, not our own bootloader
    monkeypatch.setattr(launcher, "process_parents",
                        lambda k32, pid: {ME: (TERMINAL, "NeedleDropper.exe"), TERMINAL: (1, "cmd.exe")})
    assert launcher.terminal_pid(object()) is None
    monkeypatch.setattr(launcher, "process_parents", lambda k32, pid: {})  # no Toolhelp: no guess
    assert launcher.terminal_pid(object()) is None


def test_a_one_file_exe_started_from_a_terminal_attaches_to_that_terminal(launcher, monkeypatch, frozen):
    k32 = FakeKernel32(consoles=[TERMINAL])
    monkeypatch.setattr(launcher, "_kernel32", lambda: k32)
    monkeypatch.setattr(launcher, "process_parents", lambda k, pid: one_file_table(TERMINAL, "powershell.exe"))
    no_stdout(monkeypatch)
    assert launcher.attach_console() == "attached"
    assert k32.attach_calls == [launcher.ATTACH_PARENT_PROCESS, TERMINAL] and k32.alloc_calls == 0
    assert frozen == ["CONOUT$", "CONOUT$", "CONIN$"]


def test_a_one_file_exe_started_from_explorer_gets_its_own_console(launcher, monkeypatch, frozen):
    k32 = FakeKernel32(consoles=[])
    monkeypatch.setattr(launcher, "_kernel32", lambda: k32)
    monkeypatch.setattr(launcher, "process_parents", lambda k, pid: one_file_table(EXPLORER, "explorer.exe"))
    no_stdout(monkeypatch)
    assert launcher.attach_console() == "allocated"
    assert k32.attach_calls == [launcher.ATTACH_PARENT_PROCESS, EXPLORER] and k32.alloc_calls == 1


def test_a_failing_command_in_the_players_terminal_never_waits_for_enter(launcher, monkeypatch, frozen):
    """An exit code other than 0 / 130 pauses only in a console window the exe opened itself; in the
    player's terminal "Press Enter" would block on the terminal's own input."""
    import needledropper.cli as cli

    k32 = FakeKernel32(consoles=[TERMINAL])
    monkeypatch.setattr(launcher, "_kernel32", lambda: k32)
    monkeypatch.setattr(launcher, "process_parents", lambda k, pid: one_file_table(TERMINAL, "cmd.exe"))
    waits = []
    monkeypatch.setattr(launcher, "wait_for_enter", lambda *a, **k: waits.append(1))
    monkeypatch.setattr(cli, "main", lambda argv=None: 2)  # e.g. a refused install-addon, a usage error
    no_stdout(monkeypatch)
    assert launcher.run(["install-addon"]) == 2
    assert launcher.console_mode == "attached" and launcher.owns_console() is False
    assert waits == []
    # started without a terminal (a shortcut): its own window, which does wait so the text can be read
    k32 = FakeKernel32(consoles=[])
    monkeypatch.setattr(launcher, "_kernel32", lambda: k32)
    monkeypatch.setattr(launcher, "process_parents", lambda k, pid: one_file_table(EXPLORER, "explorer.exe"))
    no_stdout(monkeypatch)
    assert launcher.run(["install-addon"]) == 2 and launcher.console_mode == "allocated" and waits == [1]


def test_the_real_process_table_is_readable():
    if sys.platform != "win32":
        pytest.skip("Windows only")
    spec = importlib.util.spec_from_file_location("nd_console_launcher_real", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    table = mod.process_parents(mod._kernel32(), os.getpid())
    assert os.getpid() in table and table[os.getpid()][1].lower().endswith(".exe")
