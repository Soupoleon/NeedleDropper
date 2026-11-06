"""The crash-safe thread wrapper (CONTRACTS-v1.2 3.6): a transport or engine thread that dies of
an exception shows up in /api/state ``faults`` and as one tray notification, and the component
is restarted once. The wrapper is ``threading.excepthook`` (it hears threads the shell does not
own) and is removed again when the companion stops."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import make_shell, wait_for  # noqa: E402

from needledropper import tray  # noqa: E402
from needledropper.panel_api import FaultLog, ThreadGuard, component_for_thread  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")


@pytest.fixture
def shell(tmp_path, _needledropper_home):
    ns = make_shell(tmp_path, Path(_needledropper_home))
    try:
        yield ns
    finally:
        ns.close()


def crash(name: str, message: str = "capture failed") -> None:
    def boom() -> None:
        raise RuntimeError(message)

    t = threading.Thread(target=boom, name=name, daemon=True)
    t.start()
    t.join(5)


def faults(shell) -> list:
    return shell.call("GET", "/api/state")[1]["faults"]


def test_a_dead_transport_thread_is_reported_and_restarted_once(shell):
    old_pixel = shell.transports.live("pixel")
    crash("needledropper-pixel")
    assert wait_for(lambda: faults(shell) and faults(shell)[0]["restarted"], timeout=5)
    f = faults(shell)[0]
    assert f["component"] == "Pixel link" and f["key"] == "pixel" and f["thread"] == "needledropper-pixel"
    assert f["message"] == "RuntimeError: capture failed" and f["count"] == 1 and f["agoSec"] is not None
    assert old_pixel.stopped and shell.transports.live("pixel") is not old_pixel  # restarted
    notes = [n for n in shell.app.notifier.history if n.kind == tray.NOTE_FAULT]
    assert len(notes) == 1 and "Pixel link stopped" in notes[0].title and "restarting" in notes[0].text
    assert not shell.app.status_line().startswith("Problem")  # restarted: the tray looks normal again
    # the same component dying again: counted, but neither restarted nor notified a second time
    restarted = shell.transports.live("pixel")
    crash("needledropper-pixel", "again")
    assert wait_for(lambda: faults(shell)[0]["count"] == 2)
    assert shell.transports.live("pixel") is restarted and faults(shell)[0]["restarted"] is False
    assert len([n for n in shell.app.notifier.history if n.kind == tray.NOTE_FAULT]) == 1
    assert shell.app.status_line() == "Problem: Pixel link stopped (see the log)"


def test_a_dead_engine_thread_gets_a_new_engine(shell):
    first = shell.engines.current
    crash("needledropper-engine", "engine bug")
    assert wait_for(lambda: shell.engines.current is not first and shell.engines.current.started, timeout=5)
    assert first.stopped
    f = faults(shell)[0]
    assert f["component"] == "Playback engine" and f["restarted"]
    shell.transports.live("pixel").emit(object())  # frames reach the new engine
    assert shell.engines.current.submitted


def test_other_threads_are_named_and_not_restarted(shell):
    crash("needledropper-update", "offline")
    assert wait_for(lambda: bool(faults(shell)))
    f = faults(shell)[0]
    assert f["component"] == "Update check" and f["restarted"] is False
    assert "Update check stopped" in shell.app.status_line()


def test_the_fault_banner_text_reaches_the_page_state(shell):
    crash("needledropper-chatlog")
    assert wait_for(lambda: faults(shell) and faults(shell)[0]["restarted"], timeout=5)
    state = shell.call("GET", "/api/state")[1]
    assert state["faults"][0]["component"] == "Chat-log link"


def test_the_guard_is_removed_when_the_companion_stops(tmp_path, _needledropper_home):
    before = threading.excepthook
    ns = make_shell(tmp_path, Path(_needledropper_home))
    assert threading.excepthook != before
    ns.close()
    assert threading.excepthook == before


def test_thread_guard_unit():
    seen: list = []
    guard = ThreadGuard(lambda name, exc, tb: seen.append((name, str(exc))))
    previous = threading.excepthook
    chained: list = []
    threading.excepthook = lambda args: chained.append(args.thread.name)
    try:
        guard.install()
        crash("worker-1", "x")
        assert seen == [("worker-1", "x")] and chained == ["worker-1"]
        wrapped = guard.wrap(lambda: 1 / 0, "needledropper-housekeeping")
        wrapped()  # never raises
        assert seen[-1][0] == "needledropper-housekeeping" and "division" in seen[-1][1]
        guard.uninstall()
        crash("worker-2", "y")
        assert len(seen) == 2 and chained[-1] == "worker-2"
    finally:
        threading.excepthook = previous


def test_fault_log_and_component_names():
    log = FaultLog(clock=lambda: 1000.0)
    log.record("pixel", "Pixel link", ValueError("bad"))
    log.record("pixel", "Pixel link", ValueError("worse"))
    log.record("engine", "Playback engine", "text message")
    items = {f["key"]: f for f in log.to_list()}
    assert items["pixel"]["count"] == 2 and items["pixel"]["message"] == "ValueError: worse"
    assert items["engine"]["message"] == "text message"
    log.mark_restarted("pixel")
    assert items and log.active()[0].key in ("pixel", "engine")
    log.clear("pixel")
    assert [f.key for f in log.active()] == ["engine"]
    assert component_for_thread("needledropper-pixel") == ("pixel", "Pixel link")
    assert component_for_thread("NeedleDropper-tray-actions") == ("tray", "Tray icon")
    assert component_for_thread("Thread-7")[1] == "Background task Thread-7"
