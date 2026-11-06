"""Entry point of the windowed ``NeedleDropper.exe`` (built from ``NeedleDropper.spec``).

The exe behaves exactly like ``python -m needledropper``; this launcher only adapts it to
a GUI-subsystem program, which Windows starts without a console window:

* **No arguments** (double-click, Start with Windows' ``--background``): the companion
  runs in the tray and logs to ``%APPDATA%\\NeedleDropper\\logs\\needledropper.log``;
  nothing is printed anywhere.
* **A command** (``NeedleDropper.exe diag``, ``validate``, ``--version``, ...): the output
  needs a console. Started from a terminal, the launcher attaches to that terminal's
  console (Windows does not wait for GUI programs, so the prompt may come back before
  the output; ``start /wait NeedleDropper.exe diag`` in cmd, or piping the output, e.g.
  ``NeedleDropper.exe validate | Out-Host`` in PowerShell, keeps it in order). Started
  without one (a shortcut with arguments), it opens a console window of its own and
  waits for Enter before that window closes. Output that is redirected (a pipe or a
  file, as in ``tools\\build_release.ps1``) is used as it is.
* **A crash** is appended to the log file (even when the ``needledropper`` package
  itself cannot be imported) and, without a console, shown in a message box.

It also runs straight from a source checkout: ``python companion\\launcher.py ...``.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Optional

TITLE = "NeedleDropper"
#: Exit codes that need no pause: success, and Ctrl+C (``cli.main`` returns 130).
QUIET_EXIT_CODES = (0, 130)
ATTACH_PARENT_PROCESS = 0xFFFFFFFF  # (DWORD)-1
#: Top-level options that still mean "run the companion in the tray" (no console needed).
TRAY_FLAGS = ("--background", "-v", "--verbose")
#: Commands that run the companion in the tray too (``setup`` opens the Connect page first).
TRAY_COMMANDS = ("setup",)

#: How this process got its console: "existing" (a console or redirected output was already
#: there), "attached" (the parent terminal's), "allocated" (a window of its own) or "none".
console_mode = "existing"


def _kernel32() -> Optional[Any]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return ctypes.WinDLL("kernel32", use_last_error=True)
    except (ImportError, OSError, AttributeError):
        return None


def set_console_title(title: str = TITLE) -> bool:
    """Name the console window (only one this process opened itself); False if there is none."""
    k32 = _kernel32()
    if k32 is None:
        return False
    try:
        return bool(k32.SetConsoleTitleW(str(title)))
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def owns_console() -> bool:
    """True when no other program shares this console window: the launcher opened it, or (a
    console build / ``python launcher.py``) it was started by double-click, not from a terminal."""
    if console_mode == "allocated":
        return True
    k32 = _kernel32()
    if k32 is None:
        return False
    try:
        import ctypes

        pids = (ctypes.c_uint32 * 16)()
        count = int(k32.GetConsoleProcessList(pids, len(pids)))
    except (OSError, AttributeError, TypeError, ValueError):
        return False
    if count <= 0:
        return False
    own = 2 if getattr(sys, "frozen", False) else 1  # one-file: bootloader + interpreter
    return count <= own


def needs_console(argv: list[str]) -> bool:
    """Whether the command line asks for output (a command, ``--help``, ``--version``)
    rather than the tray (no arguments, ``--background``, ``-v``, ``--config FILE``, ``setup``)."""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--config":
            i += 2
            continue
        if arg.startswith("--config=") or arg in TRAY_FLAGS:
            i += 1
            continue
        return arg not in TRAY_COMMANDS or "--help" in argv[i:] or "-h" in argv[i:]
    return False


def _stream_usable(stream: Any) -> bool:
    if stream is None:
        return False
    try:
        stream.fileno()
    except (AttributeError, OSError, ValueError):
        return False
    return True


def attach_console(*, allow_alloc: bool = True) -> str:
    """Give a windowed process a console for its output; returns the new :data:`console_mode`."""
    global console_mode
    if _stream_usable(sys.stdout):
        console_mode = "existing"
        return console_mode
    k32 = _kernel32()
    if k32 is None:
        console_mode = "none"
        return console_mode
    how = "none"
    try:
        from ctypes import wintypes

        k32.AttachConsole.argtypes = [wintypes.DWORD]
        k32.AttachConsole.restype = wintypes.BOOL
        k32.AllocConsole.argtypes = []
        k32.AllocConsole.restype = wintypes.BOOL
        if k32.AttachConsole(ATTACH_PARENT_PROCESS):
            how = "attached"
        elif allow_alloc and k32.AllocConsole():
            how = "allocated"
    except Exception:  # noqa: BLE001 - OSError, ctypes.ArgumentError, ...: stay without a console
        how = "none"
    if how != "none":
        try:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")  # noqa: SIM115
        except OSError:
            how = "none"
    if how == "attached":
        print()  # the terminal's prompt is already on this line
    elif how == "allocated":
        set_console_title()
    console_mode = how
    return how


def app_dir() -> Path:
    """Mirror of ``needledropper.config.app_dir`` (usable when that package cannot be imported)."""
    home = os.environ.get("NEEDLEDROPPER_HOME", "").strip()
    if home:
        return Path(os.path.expanduser(home))
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "NeedleDropper"
    return Path.home() / ".needledropper"


def crash_log_path() -> Path:
    return app_dir() / "logs" / "needledropper.log"


def record_crash(details: str) -> Optional[Path]:
    """Append a crash report to the log file; the path, or None if it could not be written."""
    path = crash_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CRITICAL launcher: NeedleDropper stopped because of an "
                     f"unexpected error\n{details.rstrip()}\n")
        return path
    except OSError:
        return None


def show_error(title: str, text: str) -> bool:
    """A message box (the windowed exe's only way to say something before the tray exists)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        return bool(user32.MessageBoxW(None, str(text), str(title), 0x10 | 0x10000 | 0x40000))  # error, foreground, topmost
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def exit_code(value: Any) -> int:
    """Normalize a ``main()`` return value or ``SystemExit.code`` to an int."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    # SystemExit("message"): Python prints the message and exits with 1.
    try:
        print(value, file=sys.stderr)
    except (OSError, ValueError, AttributeError):
        pass
    return 1


def wait_for_enter(prompt: str = "Press Enter to close this window.") -> None:
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return
        print()
        input(prompt)
    except (EOFError, KeyboardInterrupt, OSError, RuntimeError, ValueError):
        pass


def run(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if needs_console(args):
        attach_console(allow_alloc=True)
    try:
        from needledropper.cli import main as cli_main

        code = exit_code(cli_main() if argv is None else cli_main(argv))
    except SystemExit as exc:  # --help / --version / usage errors from argparse
        code = exit_code(exc.code)
    except KeyboardInterrupt:
        code = 130
    except Exception:  # noqa: BLE001 - last-resort report for the windowed exe
        details = traceback.format_exc()
        where = record_crash(details)
        if sys.stderr is not None:
            try:
                sys.stderr.write(details)
                print("\nNeedleDropper stopped because of an unexpected error (details above).\n"
                      "Please include that text if you report the problem.", file=sys.stderr)
            except (OSError, ValueError):
                pass
        else:
            show_error("NeedleDropper stopped", "NeedleDropper stopped because of an unexpected error."
                       + (f"\n\nThe details are in {where}." if where else ""))
        code = 1
    if console_mode == "allocated" or (code not in QUIET_EXIT_CODES and owns_console()):
        wait_for_enter()
    return code


if __name__ == "__main__":
    raise SystemExit(run())
