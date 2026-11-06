"""Start with Windows (``needledropper.autostart``): command building and the Run registry value.

Every registry test uses :class:`FakeWinreg`; nothing here writes to the real registry.
The only real-registry test is read-only (it looks up a value name that cannot exist).
"""
from __future__ import annotations

import logging
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import autostart  # noqa: E402

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="real winreg")

RUN = autostart.RUN_KEY
APPROVED = autostart.STARTUP_APPROVED_KEY


# ------------------------------------------------------------- fake winreg --
class FakeKey:
    def __init__(self, reg: "FakeWinreg", path: str, access: int) -> None:
        self.reg, self.path, self.access, self.closed = reg, path, access, False

    def __enter__(self) -> "FakeKey":
        return self

    def __exit__(self, *exc) -> None:
        self.Close()

    def Close(self) -> None:  # noqa: N802 - winreg naming
        self.closed = True


class FakeWinreg:
    """The small winreg surface autostart uses, over an in-memory store.

    Key paths and value names are case-insensitive, like the real registry. A missing
    key or value raises FileNotFoundError; writing through a key opened without
    KEY_SET_VALUE raises PermissionError. ``deny`` makes chosen operations raise
    PermissionError, to exercise error handling.
    """

    HKEY_CURRENT_USER = 0x80000001
    KEY_QUERY_VALUE = 0x0001
    KEY_SET_VALUE = 0x0002
    KEY_READ = 0x20019
    KEY_WRITE = 0x20006
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_BINARY = 3
    REG_DWORD = 4

    def __init__(self) -> None:
        self.keys: dict[tuple[int, str], dict[str, tuple[str, object, int]]] = {}
        self.calls: list[tuple] = []
        self.deny: set[str] = set()

    # helpers for tests
    def put(self, path: str, name: str, value: object, kind: int, root: int = HKEY_CURRENT_USER) -> None:
        self.keys.setdefault((root, path.lower()), {})[name.lower()] = (name, value, kind)

    def get(self, path: str, name: str, root: int = HKEY_CURRENT_USER):
        entry = self.keys.get((root, path.lower()), {}).get(name.lower())
        return None if entry is None else (entry[1], entry[2])

    def _check(self, op: str) -> None:
        if op in self.deny:
            raise PermissionError(13, f"Access is denied ({op})")

    # winreg API
    def OpenKey(self, key, sub_key, reserved=0, access=KEY_READ):  # noqa: N802
        self.calls.append(("OpenKey", sub_key, access))
        self._check("OpenKey")
        if (key, sub_key.lower()) not in self.keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return FakeKey(self, sub_key.lower(), access)

    def CreateKeyEx(self, key, sub_key, reserved=0, access=KEY_WRITE):  # noqa: N802
        self.calls.append(("CreateKeyEx", sub_key, access))
        self._check("CreateKeyEx")
        self.keys.setdefault((key, sub_key.lower()), {})
        return FakeKey(self, sub_key.lower(), access)

    def QueryValueEx(self, key, value_name):  # noqa: N802
        self.calls.append(("QueryValueEx", key.path, value_name))
        self._check("QueryValueEx")
        entry = self.keys[(self.HKEY_CURRENT_USER, key.path)].get(value_name.lower())
        if entry is None:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return entry[1], entry[2]

    def SetValueEx(self, key, value_name, reserved, type, value):  # noqa: N802,A002
        self.calls.append(("SetValueEx", key.path, value_name, type, value))
        self._check("SetValueEx")
        if not key.access & self.KEY_SET_VALUE:
            raise PermissionError(5, "Access is denied")
        self.keys[(self.HKEY_CURRENT_USER, key.path)][value_name.lower()] = (value_name, value, type)

    def DeleteValue(self, key, value):  # noqa: N802
        self.calls.append(("DeleteValue", key.path, value))
        self._check("DeleteValue")
        if not key.access & self.KEY_SET_VALUE:
            raise PermissionError(5, "Access is denied")
        values = self.keys[(self.HKEY_CURRENT_USER, key.path)]
        if value.lower() not in values:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        del values[value.lower()]

    def CloseKey(self, key) -> None:  # noqa: N802
        key.Close()


@pytest.fixture
def reg() -> FakeWinreg:
    fake = FakeWinreg()
    fake.put(RUN, "OneDrive", r'"C:\Users\x\AppData\Local\Microsoft\OneDrive\OneDrive.exe" /background', 1)
    return fake


EXE_SPACES = r"C:\Program Files\NeedleDropper\NeedleDropper.exe"


# ---------------------------------------------------------- command lines --
def test_command_line_always_quotes_the_program_and_quotes_args_only_when_needed():
    assert autostart.command_line([EXE_SPACES]) == f'"{EXE_SPACES}"'
    assert autostart.command_line([r"C:\ND\nd.exe"]) == r'"C:\ND\nd.exe"'
    cmd = autostart.command_line([EXE_SPACES, "--tray", r"C:\My Music\a b.mp3", "", 'say "hi"', "x\\"])
    assert cmd == (f'"{EXE_SPACES}" --tray "C:\\My Music\\a b.mp3" "" "say \\"hi\\"" x\\')
    assert autostart.command_line([Path(r"C:\Program Files\X\x.exe"), Path("a b")]) == \
        r'"C:\Program Files\X\x.exe" "a b"'


@pytest.mark.skipif(sys.platform != "win32", reason="CommandLineToArgvW is Windows-only")
def test_command_line_round_trips_through_commandlinetoargvw():
    import ctypes
    from ctypes import wintypes as wt

    shell32 = ctypes.WinDLL("shell32")
    kernel32 = ctypes.WinDLL("kernel32")
    shell32.CommandLineToArgvW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wt.LPWSTR)
    kernel32.LocalFree.argtypes = [wt.HLOCAL]
    kernel32.LocalFree.restype = wt.HLOCAL
    argv = [EXE_SPACES, "--tray", r"C:\My Music\a b.mp3", "", 'say "hi"', "back\\slash\\", "-m"]
    count = ctypes.c_int()
    parsed = shell32.CommandLineToArgvW(autostart.command_line(argv), ctypes.byref(count))
    try:
        assert [parsed[i] for i in range(count.value)] == argv
    finally:
        kernel32.LocalFree(ctypes.cast(parsed, ctypes.c_void_p))


def test_command_line_rejects_bad_programs():
    for bad in ([], [""], ['C:\\a"b.exe']):
        with pytest.raises(ValueError):
            autostart.command_line(bad)


def test_default_command_frozen_uses_the_exe(tmp_path):
    exe = tmp_path / "Needle Dropper" / "NeedleDropper.exe"
    argv = autostart.default_command(["--tray"], frozen=True, executable=exe)
    assert argv == [str(exe), "--tray"]


def _fake_python(tmp_path: Path, *, with_pythonw: bool = True) -> Path:
    scripts = tmp_path / "venv dir" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"MZ")
    if with_pythonw:
        (scripts / "pythonw.exe").write_bytes(b"MZ")
    return scripts / "python.exe"


def test_default_command_installed_package_uses_pythonw_dash_m(tmp_path):
    python = _fake_python(tmp_path)
    argv = autostart.default_command(["--tray"], frozen=False, executable=python, installed=True)
    assert argv == [str(python.with_name("pythonw.exe")), "-m", "needledropper", "--tray"]
    cmd = autostart.command_line(argv)
    assert cmd == f'"{python.with_name("pythonw.exe")}" -m needledropper --tray'


def test_default_command_source_checkout_runs_the_launcher(tmp_path):
    python = _fake_python(tmp_path)
    launcher = tmp_path / "repo" / "companion" / "launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# launcher\n", encoding="utf-8")
    argv = autostart.default_command((), frozen=False, executable=python, installed=False, launcher=launcher)
    assert argv == [str(python.with_name("pythonw.exe")), str(launcher)]
    # no launcher next to the package either: fall back to -m
    argv = autostart.default_command((), frozen=False, executable=python, installed=False,
                                     launcher=tmp_path / "missing.py")
    assert argv[1:] == ["-m", "needledropper"]


def test_this_checkout_has_the_launcher_the_fallback_relies_on():
    assert autostart.launcher_script() == ROOT / "companion" / "launcher.py"


def test_pythonw_swap_only_when_the_sibling_exists(tmp_path):
    python = _fake_python(tmp_path, with_pythonw=False)
    assert autostart.pythonw_for(python) == str(python)  # keeps python.exe (a console may flash)
    (python.parent / "pythonw.exe").write_bytes(b"MZ")
    assert autostart.pythonw_for(python) == str(python.with_name("pythonw.exe"))
    assert autostart.pythonw_for(python.with_name("pythonw.exe")) == str(python.with_name("pythonw.exe"))
    other = tmp_path / "NeedleDropper.exe"
    assert autostart.pythonw_for(other) == str(other)


def test_package_installed_detection():
    assert autostart.package_installed("pytest") is True
    assert autostart.package_installed("needledropper-surely-not-installed-" + uuid.uuid4().hex) is False


# --------------------------------------------------------------- registry --
def test_enable_is_enabled_disable_round_trip(reg):
    assert autostart.is_enabled(winreg=reg) is False
    assert autostart.get_command(winreg=reg) is None
    cmd = autostart.enable(EXE_SPACES, ["--tray"], winreg=reg)
    assert cmd == f'"{EXE_SPACES}" --tray'
    assert reg.get(RUN, "NeedleDropper") == (cmd, reg.REG_SZ)
    assert autostart.is_enabled(winreg=reg) is True
    assert autostart.get_command(winreg=reg) == cmd
    assert autostart.disable(winreg=reg) is True
    assert reg.get(RUN, "NeedleDropper") is None
    assert autostart.is_enabled(winreg=reg) is False
    assert autostart.disable(winreg=reg) is False  # idempotent
    # other programs' values are never touched
    assert reg.get(RUN, "OneDrive") is not None


def test_enable_writes_only_through_set_value_access(reg):
    autostart.enable(EXE_SPACES, winreg=reg)
    writes = [c for c in reg.calls if c[0] in ("CreateKeyEx", "SetValueEx")]
    assert writes[0] == ("CreateKeyEx", RUN, reg.KEY_SET_VALUE)
    assert writes[1][0] == "SetValueEx" and writes[1][2] == "NeedleDropper" and writes[1][3] == reg.REG_SZ


def test_enable_makes_relative_paths_absolute(reg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cmd = autostart.enable("NeedleDropper.exe", winreg=reg)
    assert cmd == f'"{tmp_path / "NeedleDropper.exe"}"'


def test_enable_default_command_frozen(reg, tmp_path, monkeypatch):
    exe = tmp_path / "Program Files" / "NeedleDropper.exe"
    # pytest's tmp_path lives in %TEMP%, which Start with Windows now refuses (journey review F10):
    # point TEMP elsewhere so this fake "Program Files" counts as the permanent folder it stands for.
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "temp"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert autostart.enable(winreg=reg) == f'"{exe}"'
    assert autostart.enable(args=["--tray"], winreg=reg) == f'"{exe}" --tray'


def test_enable_default_command_from_source(reg, tmp_path, monkeypatch):
    python = _fake_python(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(autostart, "package_installed", lambda name=autostart.PACKAGE: True)
    cmd = autostart.enable(winreg=reg)
    assert cmd == f'"{python.with_name("pythonw.exe")}" -m needledropper'


def test_task_manager_switch_is_honoured_and_reset(reg):
    autostart.enable(EXE_SPACES, winreg=reg)
    # Task Manager -> Startup apps -> Disable: first byte odd, then a FILETIME
    reg.put(APPROVED, "NeedleDropper", bytes([3, 0, 0, 0]) + b"\x10" * 8, reg.REG_BINARY)
    assert autostart.get_command(winreg=reg) is not None
    assert autostart.is_approved(winreg=reg) is False
    assert autostart.is_enabled(winreg=reg) is False
    autostart.enable(EXE_SPACES, winreg=reg)  # ticking "Start with Windows" again really turns it on
    assert reg.get(APPROVED, "NeedleDropper") is None
    assert autostart.is_enabled(winreg=reg) is True
    # an "enabled" switch (even first byte) counts as on; disable() cleans the switch up too
    reg.put(APPROVED, "NeedleDropper", bytes([2] + [0] * 11), reg.REG_BINARY)
    assert autostart.is_enabled(winreg=reg) is True
    assert autostart.disable(winreg=reg) is True
    assert reg.get(APPROVED, "NeedleDropper") is None


def test_unexpected_switch_data_counts_as_on(reg):
    autostart.enable(EXE_SPACES, winreg=reg)
    for data, kind in ((b"", reg.REG_BINARY), (7, reg.REG_DWORD), ("off", reg.REG_SZ)):
        reg.put(APPROVED, "NeedleDropper", data, kind)
        assert autostart.is_enabled(winreg=reg) is True


def test_non_string_or_blank_run_values_are_not_registrations(reg):
    reg.put(RUN, "NeedleDropper", b"\x00\x01", reg.REG_BINARY)
    assert autostart.get_command(winreg=reg) is None and autostart.is_enabled(winreg=reg) is False
    reg.put(RUN, "NeedleDropper", "   ", reg.REG_SZ)
    assert autostart.get_command(winreg=reg) is None
    reg.put(RUN, "NeedleDropper", r"%LOCALAPPDATA%\ND\nd.exe", reg.REG_EXPAND_SZ)
    assert autostart.get_command(winreg=reg) == r"%LOCALAPPDATA%\ND\nd.exe"


def test_missing_run_key_is_handled(reg):
    empty = FakeWinreg()
    assert autostart.is_enabled(winreg=empty) is False
    assert autostart.disable(winreg=empty) is False
    autostart.enable(EXE_SPACES, winreg=empty)  # CreateKeyEx creates the key
    assert autostart.is_enabled(winreg=empty) is True


def test_registry_errors(reg, caplog):
    reg.deny = {"SetValueEx"}
    with pytest.raises(autostart.AutostartError, match="could not write"):
        autostart.enable(EXE_SPACES, winreg=reg)
    reg.deny = set()
    autostart.enable(EXE_SPACES, winreg=reg)
    reg.deny = {"DeleteValue"}
    with pytest.raises(autostart.AutostartError, match="could not remove"):
        autostart.disable(winreg=reg)
    reg.deny = {"OpenKey"}
    with caplog.at_level(logging.WARNING, logger="needledropper.autostart"):
        assert autostart.get_command(winreg=reg) is None
        assert autostart.is_enabled(winreg=reg) is False
    assert "could not read" in caplog.text


def test_set_enabled_returns_the_resulting_state(reg):
    assert autostart.set_enabled(True, EXE_SPACES, winreg=reg) is True
    assert autostart.set_enabled(False, winreg=reg) is False
    assert reg.get(RUN, "NeedleDropper") is None


def test_refresh_follows_a_moved_program_but_never_registers(reg, caplog):
    assert autostart.refresh(EXE_SPACES, winreg=reg) is False  # not registered: nothing written
    assert reg.get(RUN, "NeedleDropper") is None
    autostart.enable(r"C:\Old Place\NeedleDropper.exe", winreg=reg)
    reg.put(APPROVED, "NeedleDropper", bytes([3] + [0] * 11), reg.REG_BINARY)  # user switched it off
    assert autostart.refresh(EXE_SPACES, winreg=reg) is True
    assert reg.get(RUN, "NeedleDropper")[0] == f'"{EXE_SPACES}"'
    assert autostart.refresh(EXE_SPACES, winreg=reg) is False  # already current
    assert reg.get(APPROVED, "NeedleDropper") is not None  # the Task Manager switch is left alone
    assert autostart.is_enabled(winreg=reg) is False


def test_custom_value_name(reg):
    autostart.enable(EXE_SPACES, winreg=reg, value_name="NeedleDropper (beta)")
    assert reg.get(RUN, "NeedleDropper (beta)") is not None
    assert autostart.is_enabled(winreg=reg) is False
    assert autostart.is_enabled(winreg=reg, value_name="NeedleDropper (beta)") is True


def test_long_commands_are_written_with_a_warning(reg, caplog):
    long_exe = "C:\\" + "\\".join(["very long folder name"] * 14) + "\\NeedleDropper.exe"
    with caplog.at_level(logging.WARNING, logger="needledropper.autostart"):
        cmd = autostart.enable(long_exe, winreg=reg)
    assert len(cmd) > autostart.MAX_COMMAND_LENGTH
    assert reg.get(RUN, "NeedleDropper")[0] == cmd
    assert "260" in caplog.text


def test_other_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert autostart.is_enabled() is False
    assert autostart.get_command() is None
    assert autostart.disable() is False
    assert autostart.refresh(EXE_SPACES) is False
    with pytest.raises(autostart.AutostartError, match="requires Windows"):
        autostart.enable(EXE_SPACES)
    # an injected registry still works anywhere
    fake = FakeWinreg()
    autostart.enable(EXE_SPACES, winreg=fake)
    assert autostart.is_enabled(winreg=fake) is True


def test_imports_without_winreg():
    """The module imports (and degrades) where winreg does not exist."""
    code = (
        "import sys; sys.platform = 'linux'; sys.modules['winreg'] = None; sys.path.insert(0, sys.argv[1])\n"
        "from needledropper import autostart as a\n"
        "assert a.is_enabled() is False and a.disable() is False and a.get_command() is None\n"
        "try:\n"
        "    a.enable('/usr/bin/needledropper')\n"
        "except a.AutostartError as e:\n"
        "    assert 'requires Windows' in str(e)\n"
        "else:\n"
        "    raise SystemExit('enable did not raise')\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code, str(ROOT / "companion")], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


@windows_only
def test_real_registry_lookup_is_read_only_and_safe():
    """Exercises the real winreg calls with a value name that cannot exist (no writes)."""
    name = f"NeedleDropper-pytest-{uuid.uuid4().hex}"
    assert autostart.get_command(value_name=name) is None
    assert autostart.is_approved(value_name=name) is True
    assert autostart.is_enabled(value_name=name) is False


# ------------------------------------------------------------------ ensure --
def test_ensure_restores_a_lost_entry_without_touching_task_manager(reg):
    reg.put(APPROVED, autostart.VALUE_NAME, b"\x03" + b"\x00" * 11, reg.REG_BINARY)  # off in Task Manager
    assert autostart.ensure(EXE_SPACES, ["--background"], winreg=reg) == autostart.ENSURE_CREATED
    assert autostart.get_command(winreg=reg) == f'"{EXE_SPACES}" --background'
    assert reg.get(APPROVED, autostart.VALUE_NAME) is not None and not autostart.is_enabled(winreg=reg)
    assert autostart.ensure(EXE_SPACES, ["--background"], winreg=reg) == autostart.ENSURE_OK
    calls = len(reg.calls)
    assert autostart.ensure(EXE_SPACES, ["--background"], winreg=reg) == autostart.ENSURE_OK
    assert not any(c[0] == "SetValueEx" for c in reg.calls[calls:])  # nothing rewritten when it is right


def test_ensure_follows_a_moved_program(reg):
    reg.put(RUN, autostart.VALUE_NAME, r'"C:\Old\NeedleDropper.exe"', reg.REG_SZ)
    assert autostart.ensure(EXE_SPACES, winreg=reg) == autostart.ENSURE_UPDATED
    assert autostart.get_command(winreg=reg) == f'"{EXE_SPACES}"'
    assert reg.get(RUN, "OneDrive") is not None  # other programs' values are never touched


def test_ensure_reports_registry_errors(reg):
    reg.deny.add("SetValueEx")
    with pytest.raises(autostart.AutostartError):
        autostart.ensure(EXE_SPACES, winreg=reg)


def test_ensure_elsewhere(monkeypatch):
    monkeypatch.setattr(autostart, "_registry", lambda winreg=None: None)
    assert autostart.ensure(EXE_SPACES) == autostart.ENSURE_UNAVAILABLE
