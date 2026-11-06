"""CHAT-REPLAY-ON-ERROR and CHAT-UNICODE-RAISE (docs/review/baseline-findings.md): a read/open error never
replays WoWChatLog.txt from byte 0, and a player's non-ASCII chat line never costs a real record."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_transport_chatlog import ARM, FIRE, HELLO, PING, Rig, log_line  # noqa: E402

from needledropper import protocol, protocol2  # noqa: E402
from needledropper.models import Event, EventType, Flags  # noqa: E402
from needledropper.transport import chatlog as chatlog_mod  # noqa: E402


def old_history(n: int) -> str:
    return "".join(log_line(Event(EventType.FIRE, i % 256, 3, 40213, Flags.DROP_ACTIVE, "zone:1429"))
                   for i in range(n))


def test_a_read_error_resumes_where_reading_stopped(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.append(old_history(20))
    rig.t.poll()                                          # positioned at the end: history skipped
    rig.append(log_line(PING))
    assert rig.t.poll() == 1
    real_fstat = os.fstat
    calls = {"n": 0}

    def flaky_fstat(fd):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(5, "Access is denied (antivirus scan)")
        return real_fstat(fd)

    monkeypatch.setattr(chatlog_mod.os, "fstat", flaky_fstat)
    rig.append(log_line(ARM))
    assert rig.t.poll() == 0                              # the error: the handle is closed
    monkeypatch.setattr(chatlog_mod.os, "fstat", real_fstat)
    assert rig.t.poll() == 1                              # reopened at the old offset: only the new ARM
    assert [e.key for e in rig.got()] == [PING.key, ARM.key]
    assert any("where reading stopped" in d for d in rig.diag)
    assert not any("from the start" in d for d in rig.diag)


def test_a_failed_first_open_does_not_replay_the_history(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.append(old_history(50))
    real_open = chatlog_mod.open_shared
    calls = {"n": 0}

    def locked_once(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "sharing violation")
        return real_open(path)

    monkeypatch.setattr(chatlog_mod, "open_shared", locked_once)
    assert rig.t.poll() == 0                              # the first open fails
    rig.append(log_line(HELLO))
    assert rig.t.poll() == 1                              # opened at the end it had at start
    assert [e.key for e in rig.got()] == [HELLO.key]      # not the 50 old FIREs


def test_a_truncated_or_replaced_log_is_still_read_from_its_start(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.append(old_history(5))
    rig.t.poll()
    rig.t._close(keep_position=True)                      # as after an error ...
    rig.path.write_bytes(log_line(PING).encode())         # ... and the game started a new, shorter log
    assert rig.t.poll() == 1
    assert [e.key for e in rig.got()] == [PING.key]


def test_non_ascii_digits_and_ids_never_raise():
    for line in ("NDROP1 1 3 2 3 0 é 0000", "NDROP1 ١ 3 2 3 0 zone:1 0000",
                 "11/5 18:01:02.345  NDROP1 ١٢ 3 2 3 0 x 0000"):
        assert protocol.parse_chat_line(line) is None
        assert chatlog_mod.parse_line(line, match="anywhere") is None
        assert chatlog_mod.parse_line(line, match="strict") is None


@pytest.mark.parametrize("match", ["anywhere", "strict"])
def test_a_players_unicode_line_does_not_cost_the_real_record_after_it(tmp_path, match):
    rig = Rig(tmp_path, match=match)
    rig.append("")
    rig.t.poll()
    player = "11/5 18:01:02.300  [Thráll] says: NDROP1 1 3 2 3 0 é 0000\r\n"
    real = Event(EventType.FIRE, 11, 4, 40213, Flags.DROP_ACTIVE, "zone:1429")
    v2 = protocol2.format_chat_line(protocol2.build_frame(EventType.FIRE, seq=12, drop_id="zone:1436",
                                                          youtube="dQw4w9WgXcQ"))
    rig.append(player + log_line(real) + f"11/5 18:01:02.400  {v2}\r\n")
    assert rig.t.poll() == 2
    got = rig.got()
    assert got[0].key == real.key and isinstance(got[1], protocol2.Frame) and got[1].seq == 12
