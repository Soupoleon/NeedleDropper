"""Logging of the windowed companion (CONTRACTS-v1.2 3.6): a rotating file log at
``%APPDATA%\\NeedleDropper\\logs\\needledropper.log`` (5 x 1 MB), a console handler only when
there is a console, uncaught exceptions logged, and the launcher's crash log at the same path
(even when the package itself cannot be imported)."""
from __future__ import annotations

import importlib.util
import logging
import logging.handlers
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import cli, config  # noqa: E402


def handlers_of(session) -> list:
    return list(session._added)


def test_log_rotation_is_five_files_of_one_megabyte(_needledropper_home):
    paths = config.paths_from_config(config.default_config())
    with cli._LogSession(False, paths, console=False) as session:
        files = [h for h in handlers_of(session) if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert len(files) == 1 and not any(isinstance(h, cli._StdoutHandler) for h in handlers_of(session))
        fh = files[0]
        assert Path(fh.baseFilename) == Path(_needledropper_home) / "logs" / "needledropper.log"
        assert fh.maxBytes == 1_000_000 and fh.backupCount == 4  # the live file + 4 older = 5 x 1 MB
        assert config.LOG_MAX_BYTES == 1_000_000 and config.LOG_BACKUP_COUNT == 4
        assert fh.level == logging.INFO and fh.encoding == "utf-8"
        logging.getLogger("needledropper.test").info("hello from the test")
        fh.flush()
        assert "hello from the test" in session.log_file.read_text(encoding="utf-8")
    assert fh not in logging.getLogger("needledropper").handlers  # undone on exit
    with cli._LogSession(True, paths, console=False) as session:
        assert [h for h in handlers_of(session)][0].level == logging.DEBUG


def test_the_log_rotates(_needledropper_home, monkeypatch):
    monkeypatch.setattr(cli, "LOG_MAX_BYTES", 2000)
    paths = config.paths_from_config(config.default_config())
    with cli._LogSession(False, paths, console=False):
        log = logging.getLogger("needledropper.rotation")
        for i in range(400):
            log.info("line %04d %s", i, "x" * 40)
    logs = sorted(p.name for p in paths.logs_dir.iterdir())
    assert logs == ["needledropper.log", "needledropper.log.1", "needledropper.log.2", "needledropper.log.3",
                    "needledropper.log.4"]


def test_no_console_handler_without_a_console(_needledropper_home, monkeypatch):
    paths = config.paths_from_config(config.default_config())
    monkeypatch.setattr(sys, "stdout", None)
    with cli._LogSession(False, paths, console=True) as session:
        assert not any(isinstance(h, cli._StdoutHandler) for h in handlers_of(session))
        logging.getLogger("needledropper.test").warning("still logged to the file")
    cli.say("printing without a console is a no-op")
    assert "still logged" in paths.log_file.read_text(encoding="utf-8")


def test_uncaught_main_thread_errors_are_logged(_needledropper_home, monkeypatch):
    paths = config.paths_from_config(config.default_config())
    seen: list = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append(a))
    with cli._LogSession(False, paths, console=False):
        undo = cli._install_excepthook()
        try:
            try:
                raise ValueError("unexpected!")
            except ValueError:
                sys.excepthook(*sys.exc_info())
        finally:
            undo()
    assert len(seen) == 1  # the previous hook still ran
    text = paths.log_file.read_text(encoding="utf-8")
    assert "CRITICAL" in text and "ValueError: unexpected!" in text


# --------------------------------------------------------------- launcher ----

@pytest.fixture()
def launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("nd_shell_launcher", ROOT / "companion" / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "set_console_title", lambda *a, **k: False)
    monkeypatch.setattr(mod, "show_error", lambda *a, **k: False)
    return mod


def test_the_launcher_crash_log_is_the_companion_log(launcher, _needledropper_home):
    assert launcher.crash_log_path() == config.paths_from_config(config.default_config()).log_file
    assert launcher.app_dir() == config.app_dir()


def test_a_crash_is_written_to_the_log_and_shown_without_a_console(launcher, monkeypatch, _needledropper_home):
    import needledropper.cli as real_cli

    def boom(argv=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(real_cli, "main", boom)
    shown: list = []
    monkeypatch.setattr(launcher, "show_error", lambda title, text: shown.append((title, text)) or True)
    monkeypatch.setattr(launcher, "owns_console", lambda: False)
    monkeypatch.setattr(sys, "stderr", None)
    assert launcher.run([]) == 1
    log = launcher.crash_log_path().read_text(encoding="utf-8")
    assert "RuntimeError: kaboom" in log and "CRITICAL launcher" in log
    assert shown and str(launcher.crash_log_path()) in shown[0][1]
