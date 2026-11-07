"""The Check-the-link card (DESIGN-v1.2 1.4 step 4, 2.2): "In WoW type /needle test". Every
frame with the TEST flag ticks the card and shows one "test signal received" notification; the
engine reports TEST / PING frames through ``on_link_check(frame, transport)``. Also: "linked to
WoW" once per session, both transports delivering the same frame tick once, the pause gate never
blocks the tick, and the Connect page is nudged over SSE the moment it happens."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import SSE, make_shell, wait_for  # noqa: E402

from needledropper import protocol2, tray  # noqa: E402
from needledropper.models import Event, EventType, Flags  # noqa: E402
from needledropper.panel_api import is_test_event  # noqa: E402


@pytest.fixture
def shell(tmp_path, _needledropper_home):
    ns = make_shell(tmp_path, Path(_needledropper_home))
    try:
        yield ns
    finally:
        ns.close()


def ping(seq: int) -> protocol2.Frame:
    return protocol2.build_frame(EventType.PING, seq=seq, flags=int(Flags.TEST))


def link(shell) -> dict:
    st, data = shell.call("GET", "/api/link")
    assert st == 200
    return data


def test_the_engine_reports_a_test_frame_and_the_card_ticks(shell):
    panel = SSE(shell.deck.port, "panel").pump()
    try:
        assert wait_for(lambda: shell.deck.panel_count == 1)
        before = link(shell)
        assert before["heard"] is False and before["count"] == 0 and before["listening"] is True
        engine = shell.engines.current
        assert engine.on_link_check == shell.app.on_link_check  # wired per CONTRACTS-v1.2 3.2
        engine.on_link_check(ping(9), "pixel")  # what the engine does for /needle test
        after = link(shell)
        assert after["heard"] and after["count"] == 1 and after["transport"] == "pixel" and after["what"] == "PING"
        assert after["heardAgoSec"] is not None and after["heardAgoSec"] < 5 and after["heardAt"]
        assert shell.call("GET", "/api/state")[1]["link"]["heard"] is True
        assert shell.app.notifier.kinds() == [tray.NOTE_TEST]
        assert "Test signal received" in shell.app.notifier.history[-1].text
        assert wait_for(lambda: {"cmd": "state", "what": "link"} in panel.messages)
    finally:
        panel.close()


def test_the_same_frame_from_both_transports_and_the_engine_ticks_once(shell):
    frame = ping(4)
    shell.transports.live("pixel").emit(frame)
    shell.transports.live("chatlog").emit(frame)
    shell.engines.current.on_link_check(frame, "pixel")
    data = link(shell)
    assert data["count"] == 1 and data["transport"] == "pixel"
    assert shell.app.notifier.kinds() == [tray.NOTE_LINKED, tray.NOTE_TEST]
    # both copies still went to the engine (it dedupes frames itself)
    assert [t for _e, t in shell.engines.current.submitted] == ["pixel", "chatlog"]
    # the next /needle test is a new frame: it ticks again, but the notification has a cooldown
    shell.transports.live("chatlog").emit(ping(5))
    data = link(shell)
    assert data["count"] == 2 and data["transport"] == "chatlog"
    assert shell.app.notifier.kinds().count(tray.NOTE_TEST) == 1


def test_a_test_from_the_drop_editor_plays_without_a_link_notification(shell):
    fire = protocol2.build_frame(EventType.FIRE, seq=12, flags=int(Flags.TEST), drop_id="area:87", artist="Toto",
                                 title="Africa", youtube="dQw4w9WgXcQ", label="Goldshire")
    shell.transports.live("pixel").emit(fire)
    data = link(shell)
    assert not data["heard"] and data["count"] == 0
    assert tray.NOTE_TEST not in shell.app.notifier.kinds()
    assert shell.engines.current.submitted[-1][0] is fire  # and it still plays


def test_linked_to_wow_is_notified_once_per_session(shell):
    for seq in range(5):
        shell.transports.live("pixel").emit(protocol2.build_frame(EventType.HELLO, seq=seq))
    data = link(shell)
    assert data["seen"] and data["frames"] == 5 and not data["heard"] and data["seenTransport"] == "pixel"
    assert shell.app.notifier.kinds() == [tray.NOTE_LINKED]
    assert "Linked to World of Warcraft" in shell.app.status_line()


def test_paused_frames_still_tick_the_link_check(shell):
    shell.app.set_paused(True)
    shell.transports.live("pixel").emit(ping(1))
    assert link(shell)["heard"] and shell.engines.current.submitted == []
    assert shell.engines.current.stop_playback_calls == 1


def test_frames_without_the_test_flag_do_not_tick(shell):
    shell.transports.live("pixel").emit(protocol2.build_frame(EventType.PING, seq=1))
    shell.transports.live("pixel").emit(protocol2.build_frame(EventType.FIRE, seq=2, drop_id="zone:1429",
                                                              youtube="dQw4w9WgXcQ"))
    assert not link(shell)["heard"]


def test_an_old_v1_addon_test_still_ticks(shell):
    shell.transports.live("chatlog").emit(Event(EventType.PING, 3, 0, 0, Flags.TEST, "test"))
    data = link(shell)
    assert data["heard"] and data["transport"] == "chatlog" and data["what"] == "PING"


def test_is_test_event():
    assert is_test_event(ping(1)) and is_test_event(Event(EventType.FIRE, 1, 1, 1, Flags.TEST | Flags.DROP_ACTIVE))
    assert not is_test_event(protocol2.build_frame(EventType.HELLO)) and not is_test_event(object())
