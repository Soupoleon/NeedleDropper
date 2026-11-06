""""Start with Windows" for the companion: the per-user ``Run`` registry value.

When the user signs in, Windows starts every command stored under
``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``. NeedleDropper owns the value
named ``NeedleDropper`` there (:data:`VALUE_NAME`) and touches nothing else in that key.

* :func:`is_enabled` - the value exists and Task Manager has not switched it off;
* :func:`enable` - write the value (``REG_SZ``); by default the command starts this
  companion the way it is running now (see :func:`default_command`);
* :func:`disable` - remove the value (idempotent);
* :func:`set_enabled` - either of the two, for a "Start with Windows" check item;
* :func:`get_command` / :func:`refresh` - read the registered command, or rewrite it
  when the program has moved (e.g. ``NeedleDropper.exe`` unpacked to another folder);
* :func:`ensure` - the start-up re-check for a player who chose "Start with Windows":
  restores a lost entry and follows a moved program, never overriding Task Manager;
* :func:`default_command` / :func:`command_line` - pure helpers, no registry access.

**The command line.** The program path is always quoted, so a path with spaces such as
``C:\\Program Files\\NeedleDropper\\NeedleDropper.exe`` can never be read as
``C:\\Program.exe``; arguments follow the MS C runtime quoting rules
(:func:`subprocess.list2cmdline`), so arguments with spaces are quoted too.

* frozen (``NeedleDropper.exe``): ``"<exe>" <args>``;
* source or pip install: ``"<pythonw.exe>" -m needledropper <args>``, using the
  ``pythonw.exe`` next to the running interpreter so no console window opens at sign-in.
  Windows starts ``Run`` commands in ``C:\\Windows\\System32``, where ``-m needledropper``
  only works if the package is installed. For a plain source checkout (no
  ``needledropper`` distribution) the command runs ``companion\\launcher.py`` instead,
  which behaves exactly like ``python -m needledropper``.

**Task Manager.** Switching an app off on Task Manager's "Startup apps" page keeps its
``Run`` value and records the switch as a binary value with the same name under
``HKCU\\...\\Explorer\\StartupApproved\\Run`` (first byte even = on, odd = off).
:func:`is_enabled` honours that switch, and :func:`enable` / :func:`disable` delete
NeedleDropper's entry there, so ticking "Start with Windows" really turns it back on.

**Testing.** Every function takes an optional ``winreg`` module; tests pass a fake with
the same small surface (``HKEY_CURRENT_USER``, ``KEY_READ``, ``KEY_SET_VALUE``, ``REG_SZ``,
``REG_EXPAND_SZ``, ``OpenKey``, ``CreateKeyEx``, ``QueryValueEx``, ``SetValueEx``,
``DeleteValue``; keys usable as context managers; a missing key or value raises
:class:`FileNotFoundError`). Without one, the real :mod:`winreg` is used on Windows.
Elsewhere :func:`is_enabled` is False, :func:`disable` does nothing and :func:`enable`
raises :class:`AutostartError`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

log = logging.getLogger("needledropper.autostart")

#: The per-user key Windows reads at sign-in (relative to ``HKEY_CURRENT_USER``).
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: Where Task Manager's "Startup apps" page records its on/off switch.
STARTUP_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
#: The value NeedleDropper owns in both keys.
VALUE_NAME = "NeedleDropper"
#: The package started by ``python -m``.
PACKAGE = "needledropper"
#: Microsoft documents ``Run`` commands as at most 260 characters. Longer ones are still
#: written (current Windows starts them), but a warning is logged.
MAX_COMMAND_LENGTH = 260

PathArg = Union[str, "os.PathLike[str]"]


class AutostartError(RuntimeError):
    """Start with Windows could not be changed (not on Windows, or the registry refused)."""


# ------------------------------------------------------------ pure helpers --
def is_frozen() -> bool:
    """True when running as the PyInstaller-built ``NeedleDropper.exe``."""
    return bool(getattr(sys, "frozen", False))


def _text(value: Any) -> str:
    return os.fspath(value) if isinstance(value, os.PathLike) else str(value)


def pythonw_for(executable: PathArg) -> str:
    """The windowless interpreter next to ``executable`` (``pythonw.exe``), if there is one.

    ``python.exe`` (or ``python3.12.exe``) becomes the sibling ``pythonw.exe`` when that
    file exists; anything else is returned unchanged.
    """
    path = Path(_text(executable))
    name = path.name.lower()
    if name == "pythonw.exe":
        return str(path)
    if name.startswith("python") and name.endswith(".exe"):
        candidate = path.with_name("pythonw.exe")
        if candidate.is_file():
            return str(candidate)
    return str(path)


def package_installed(name: str = PACKAGE) -> bool:
    """True when a ``needledropper`` distribution is installed (pip, including editable).

    Only then can ``python -m needledropper`` import the package from any working folder.
    """
    try:
        from importlib import metadata

        metadata.distribution(name)
    except Exception:  # PackageNotFoundError, or broken metadata on sys.path
        return False
    return True


TEMP_PLACE_MESSAGE = ("NeedleDropper.exe runs from a temporary folder (inside the zip?). Extract the zip to a "
                      "permanent folder such as Documents\\NeedleDropper and start it from there; Windows cleans "
                      "temporary folders, and Start with Windows would stop working.")


def temporary_location(path: Optional[PathArg] = None) -> bool:
    """True when ``path`` (default: the running frozen exe) lives in a temporary folder: under
    ``%TEMP%`` / ``%TMP%``, or in the folder Explorer unpacks a zip into when a program is
    double-clicked inside it (``...\\Temp1_<name>.zip\\``). Journey review F10: Start with Windows
    pointed at such a folder stops working once Windows cleans it."""
    if path is None:
        if not is_frozen():
            return False
        path = sys.executable or ""
    if not path:
        return False
    full = os.path.normcase(os.path.abspath(_text(path)))
    parts = [p.lower() for p in Path(full).parts]
    if any(p.startswith("temp") and p.endswith(".zip") for p in parts):
        return True
    for env in ("TEMP", "TMP"):
        base = os.environ.get(env)
        if base:
            root = os.path.normcase(os.path.abspath(base))
            if full == root or full.startswith(root.rstrip("\\/") + os.sep):
                return True
    return False


def launcher_script() -> Optional[Path]:
    """``companion/launcher.py`` of a source checkout, or None (installed / frozen layouts)."""
    path = Path(__file__).resolve().parent.parent / "launcher.py"
    return path if path.is_file() else None


def default_command(args: Sequence[Any] = (), *, frozen: Optional[bool] = None,
                    executable: Optional[PathArg] = None, installed: Optional[bool] = None,
                    launcher: Optional[PathArg] = None) -> List[str]:
    """The argv that starts this companion at sign-in.

    * frozen: ``[<exe>, *args]``;
    * package installed: ``[<pythonw.exe>, "-m", "needledropper", *args]``;
    * source checkout: ``[<pythonw.exe>, <companion/launcher.py>, *args]``, because
      ``-m needledropper`` cannot find the package from ``C:\\Windows\\System32``;
      ``-m needledropper`` again if there is no launcher script either.

    The keyword arguments override what is detected (``sys.frozen``, ``sys.executable``,
    :func:`package_installed`, :func:`launcher_script`); tests use them.
    """
    frozen = is_frozen() if frozen is None else bool(frozen)
    program = _text(executable) if executable is not None else (sys.executable or "")
    if not program:
        raise AutostartError("cannot tell which program to start (sys.executable is empty)")
    program = os.path.abspath(program)
    extra = [_text(a) for a in args]
    if frozen:
        return [program, *extra]
    interpreter = pythonw_for(program)
    if installed is None:
        installed = package_installed()
    if not installed:
        script = Path(_text(launcher)) if launcher is not None else launcher_script()
        if script is not None and script.is_file():
            return [interpreter, str(script), *extra]
    return [interpreter, "-m", PACKAGE, *extra]


def command_line(argv: Sequence[Any]) -> str:
    """One ``Run``-value command line from ``argv``.

    The program (``argv[0]``) is always wrapped in double quotes: Windows reads the
    program name up to the closing quote, so spaces in the path are safe. The other
    arguments are quoted only when needed (spaces, tabs, quotes, empty), with the
    escaping rules ``CommandLineToArgvW`` and the MS C runtime use.
    """
    if not argv:
        raise ValueError("command_line() needs at least the program path")
    program = _text(argv[0])
    if not program:
        raise ValueError("the program path is empty")
    if '"' in program:
        raise ValueError(f"a program path cannot contain a double quote: {program!r}")
    rest = subprocess.list2cmdline([_text(a) for a in argv[1:]])
    return f'"{program}" {rest}' if rest else f'"{program}"'


def _command_for(exe_path: Optional[PathArg], args: Sequence[Any]) -> str:
    if exe_path is None:
        argv = default_command(args)
    else:
        program = _text(exe_path)
        if not program:
            raise AutostartError("the program path is empty")
        argv = [os.path.abspath(program), *[_text(a) for a in args]]
    try:
        command = command_line(argv)
    except ValueError as exc:
        raise AutostartError(str(exc)) from exc
    if len(command) > MAX_COMMAND_LENGTH:
        log.warning("Start with Windows: the command is %d characters long (Windows documents at most %d): %s",
                    len(command), MAX_COMMAND_LENGTH, command)
    return command


# ---------------------------------------------------------------- registry --
def _registry(winreg: Any = None) -> Any:
    """The winreg module to use: the injected one, the real one on Windows, else None."""
    if winreg is not None:
        return winreg
    if sys.platform != "win32":
        return None
    try:
        import winreg as real_winreg
    except ImportError:  # pragma: no cover - always present on Windows
        return None
    return real_winreg


def get_command(*, winreg: Any = None, value_name: str = VALUE_NAME) -> Optional[str]:
    """The command registered to start at sign-in, or None (not registered / not Windows)."""
    reg = _registry(winreg)
    if reg is None:
        return None
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
            value, kind = reg.QueryValueEx(key, value_name)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("Start with Windows: could not read HKCU\\%s\\%s: %s", RUN_KEY, value_name, exc)
        return None
    if kind not in (reg.REG_SZ, reg.REG_EXPAND_SZ) or not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def is_approved(*, winreg: Any = None, value_name: str = VALUE_NAME) -> bool:
    """False only when Task Manager's "Startup apps" switch for the value is off."""
    reg = _registry(winreg)
    if reg is None:
        return True
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY, 0, reg.KEY_READ) as key:
            data, _kind = reg.QueryValueEx(key, value_name)
    except OSError:  # no switch recorded (the normal case) or unreadable: Windows treats it as on
        return True
    if isinstance(data, (bytes, bytearray)) and len(data) >= 1:
        return data[0] % 2 == 0
    return True


def is_enabled(*, winreg: Any = None, value_name: str = VALUE_NAME) -> bool:
    """True when the companion will start at the next sign-in."""
    reg = _registry(winreg)
    if reg is None:
        return False
    return (get_command(winreg=reg, value_name=value_name) is not None
            and is_approved(winreg=reg, value_name=value_name))


def _write(reg: Any, value_name: str, command: str) -> None:
    try:
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, value_name, 0, reg.REG_SZ, command)
    except OSError as exc:
        raise AutostartError(f"could not write HKCU\\{RUN_KEY}\\{value_name}: {exc}") from exc


def _reset_task_manager_switch(reg: Any, value_name: str) -> None:
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, value_name)
    except FileNotFoundError:
        return
    except OSError as exc:
        log.debug("Start with Windows: could not reset the Task Manager switch for %s: %s", value_name, exc)
        return
    log.debug("Start with Windows: reset the Task Manager switch for %s", value_name)


def enable(exe_path: Optional[PathArg] = None, args: Sequence[Any] = (), *, winreg: Any = None,
           value_name: str = VALUE_NAME) -> str:
    """Start the companion at every sign-in; returns the command line written.

    ``exe_path`` is the program to start (made absolute) with ``args``; when it is None
    the command comes from :func:`default_command` (``args`` are appended). Also resets
    Task Manager's switch for the value, in case the user had turned it off there.

    Raises :class:`AutostartError` when not on Windows or when the registry refuses.
    """
    reg = _registry(winreg)
    if reg is None:
        raise AutostartError(f"Start with Windows requires Windows (this is {sys.platform})")
    if exe_path is None and temporary_location():
        raise AutostartError(TEMP_PLACE_MESSAGE)
    command = _command_for(exe_path, args)
    _write(reg, value_name, command)
    _reset_task_manager_switch(reg, value_name)
    log.info("Start with Windows enabled: %s", command)
    return command


def disable(*, winreg: Any = None, value_name: str = VALUE_NAME) -> bool:
    """Stop starting the companion at sign-in; True when a registered command was removed.

    Idempotent (False when nothing was registered, and on other platforms). Raises
    :class:`AutostartError` when the value exists but cannot be deleted.
    """
    reg = _registry(winreg)
    if reg is None:
        return False
    removed = False
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, value_name)
        removed = True
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise AutostartError(f"could not remove HKCU\\{RUN_KEY}\\{value_name}: {exc}") from exc
    _reset_task_manager_switch(reg, value_name)
    if removed:
        log.info("Start with Windows disabled")
    return removed


def set_enabled(enabled: bool, exe_path: Optional[PathArg] = None, args: Sequence[Any] = (), *,
                winreg: Any = None, value_name: str = VALUE_NAME) -> bool:
    """:func:`enable` or :func:`disable`; returns the resulting :func:`is_enabled`.

    Fits a checkable "Start with Windows" menu item: ``action=autostart.set_enabled``.
    """
    if enabled:
        enable(exe_path, args, winreg=winreg, value_name=value_name)
    else:
        disable(winreg=winreg, value_name=value_name)
    return is_enabled(winreg=winreg, value_name=value_name)


#: :func:`ensure` results.
ENSURE_CREATED, ENSURE_UPDATED, ENSURE_OK, ENSURE_UNAVAILABLE = "created", "updated", "ok", "unavailable"


def ensure(exe_path: Optional[PathArg] = None, args: Sequence[Any] = (), *, winreg: Any = None,
           value_name: str = VALUE_NAME) -> str:
    """Make sure the companion still starts at sign-in, for a player who chose "Start with Windows".

    Call it at every start (autostart helpers are known to lose their entry after updates):
    a missing ``Run`` value is written again, one pointing at an old program path is
    rewritten, a correct one is left alone. Unlike :func:`enable` it never touches Task
    Manager's switch, so a player who turned NeedleDropper off there stays in charge.

    Returns :data:`ENSURE_CREATED`, :data:`ENSURE_UPDATED`, :data:`ENSURE_OK` or
    :data:`ENSURE_UNAVAILABLE` (not Windows). Raises :class:`AutostartError` when the
    registry refuses the write.
    """
    reg = _registry(winreg)
    if reg is None:
        return ENSURE_UNAVAILABLE
    if exe_path is None and temporary_location():
        raise AutostartError(TEMP_PLACE_MESSAGE)  # never repoint the Run value at a temporary copy
    current = get_command(winreg=reg, value_name=value_name)
    desired = _command_for(exe_path, args)
    if current == desired:
        return ENSURE_OK
    _write(reg, value_name, desired)
    if current is None:
        log.info("Start with Windows: the Run entry was missing; restored it: %s", desired)
        return ENSURE_CREATED
    log.info("Start with Windows updated: %s (was %s)", desired, current)
    return ENSURE_UPDATED


def refresh(exe_path: Optional[PathArg] = None, args: Sequence[Any] = (), *, winreg: Any = None,
            value_name: str = VALUE_NAME) -> bool:
    """Point an existing registration at the current program; True when it was rewritten.

    Does nothing when Start with Windows is not registered, and never changes the Task
    Manager switch. Call it at startup so the entry follows the exe after a move or update.
    """
    reg = _registry(winreg)
    if reg is None:
        return False
    current = get_command(winreg=reg, value_name=value_name)
    if current is None:
        return False
    desired = _command_for(exe_path, args)
    if current == desired:
        return False
    _write(reg, value_name, desired)
    log.info("Start with Windows updated: %s (was %s)", desired, current)
    return True


__all__ = [
    "RUN_KEY", "STARTUP_APPROVED_KEY", "VALUE_NAME", "PACKAGE", "MAX_COMMAND_LENGTH", "AutostartError",
    "ENSURE_CREATED", "ENSURE_UPDATED", "ENSURE_OK", "ENSURE_UNAVAILABLE",
    "is_frozen", "temporary_location", "TEMP_PLACE_MESSAGE", "pythonw_for", "package_installed", "launcher_script", "default_command", "command_line",
    "get_command", "is_approved", "is_enabled", "enable", "disable", "set_enabled", "refresh", "ensure",
]
