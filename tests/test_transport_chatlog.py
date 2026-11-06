"""Chat-log tailer: start-at-end, partial lines, padding, bad CRCs, truncation, rotation."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import protocol  # noqa: E402
from needledropper.models import Event, EventType, Flags  # noqa: E402
from needledropper.transport.chatlog import ChatLogTransport, open_shared  # noqa: E402

FIRE = Event(EventType.FIRE, 7, 12, 40213, Flags.LOCAL_PLAYBACK | Flags.DROP_ACTIVE, "zone:1429")
ARM = Event(EventType.ARM, 8, 3, 40213, Flags.NONE, "sub:1429:lion-s-pride-inn")
PING = Event(EventType.PING, 9, 0, 40213, Flags.TEST, "test")
HELLO = Event(EventType.HELLO, 10, 0, 40213, Flags.NONE, "")


def log_line(ev: Event, stamp: str = "11/5 18:01:02.345") -> str:
    """A chat-log line as WoW writes it (timestamp, two spaces, message)."""
    return f"{stamp}  {protocol.format_chat_line(ev)}\r\n"


def pad_lines(seq: int, count: int) -> str:
    body = f"NDPAD {seq} "
    return "".join(f"11/5 18:01:02.346  {body}{'.' * (900 - len(body))}\r\n" for _ in range(count))


class Rig:
    """A ChatLogTransport on a temp file, with captured events and diag lines."""

    def __init__(self, tmp_path: Path, **cfg) -> None:
        self.path = tmp_path / "Logs" / "WoWChatLog.txt"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[tuple[Event, str]] = []
        self.diag: list[str] = []
        self.lock = threading.Lock()
        config = {"enabled": True, "pollMs": 20, **cfg}

        def on_event(ev, name):
            with self.lock:
                self.events.append((ev, name))

        self.t = ChatLogTransport(config, SimpleNamespace(chat_log=self.path), on_event, self.diag.append)

    def append(self, text: str | bytes) -> None:
        data = text.encode("utf-8") if isinstance(text, str) else text
        with open(self.path, "ab") as f:
            f.write(data)

    def got(self) -> list[Event]:
        with self.lock:
            return [e for e, _n in self.events]


def test_start_at_end_skips_history(tmp_path):
    rig = Rig(tmp_path)
    rig.append("11/5 17:00:00.000  Some old chat\r\n" + log_line(FIRE) + log_line(ARM))
    rig.t.poll()  # first poll: positions at the current end
    assert rig.got() == []
    rig.append(log_line(PING))
    assert rig.t.poll() == 1
    (ev, name), = rig.events
    assert name == "chatlog"
    assert ev.key == PING.key and ev.flags == PING.flags and ev.id == "test"
    st = rig.t.status()
    assert st["open"] and st["events"] == 1 and st["exists"]


def test_event_fields_roundtrip(tmp_path):
    rig = Rig(tmp_path)
    rig.append("")
    rig.t.poll()
    rig.append(log_line(FIRE) + log_line(ARM) + log_line(HELLO))
    rig.t.poll()
    got = rig.got()
    assert [e.key for e in got] == [FIRE.key, ARM.key, HELLO.key]
    assert got[0].id == "zone:1429" and got[1].id == "sub:1429:lion-s-pride-inn" and got[2].id == ""
    assert got[0].has(Flags.LOCAL_PLAYBACK) and got[0].has(Flags.DROP_ACTIVE)


def test_partial_lines_and_crlf(tmp_path):
    rig = Rig(tmp_path)
    rig.append("")
    rig.t.poll()
    line = log_line(FIRE).encode()
    rig.append(line[:25])
    rig.t.poll()
    rig.append(line[25:-6])
    rig.t.poll()
    assert rig.got() == [], "must not parse an incomplete line"
    assert rig.t.status()["partialBytes"] > 0
    rig.append(line[-6:-1])  # everything but the final \n (crc complete, \r present)
    rig.t.poll()
    assert rig.got() == []
    rig.append(b"\n" + log_line(ARM).encode().replace(b"\r\n", b"\n"))  # LF-only line too
    rig.t.poll()
    assert [e.key for e in rig.got()] == [FIRE.key, ARM.key]


def test_padding_ignored_and_counted(tmp_path):
    rig = Rig(tmp_path)
    rig.append("")
    rig.t.poll()
    rig.append(log_line(FIRE) + pad_lines(7, protocol.pad_line_count(50000)))
    rig.t.poll()
    assert [e.key for e in rig.got()] == [FIRE.key]
    st = rig.t.status()
    assert st["padLines"] == 56
    assert any("56 NDPAD" in d for d in rig.diag)
    assert any("grew" in d or "bytes to read" in d for d in rig.diag)


def test_bad_crc_and_garbage_ignored(tmp_path):
    rig = Rig(tmp_path)
    rig.append("")
    rig.t.poll()
    good = protocol.format_chat_line(FIRE)
    bad_crc = good[:-4] + ("0000" if good[-4:] != "0000" else "0001")
    tampered = good.replace(" 12 ", " 13 ")
    rig.append(f"11/5 18:00:00.000  {bad_crc}\r\n"
               f"11/5 18:00:00.001  {tampered}\r\n"
               "11/5 18:00:00.002  NDROP1 not a record\r\n"
               "11/5 18:00:00.003  [Trade] WTS NDROP1 lol\r\n".encode("utf-8")
               + b"11/5 18:00:00.004  \xff\xfe invalid utf-8 NDROP1 \xc3\r\n"
               + log_line(PING).encode())
    rig.t.poll()
    assert [e.key for e in rig.got()] == [PING.key]
    st = rig.t.status()
    assert st["badLines"] == 5 and st["ndropLines"] == 6
    assert any("ignored malformed" in d for d in rig.diag)


def test_truncation_rereads_from_start(tmp_path):
    rig = Rig(tmp_path)
    rig.append(log_line(HELLO) * 20)
    rig.t.poll()  # at end
    with open(rig.path, "wb") as f:  # truncate (as a writer that recreates its log in place would)
        f.write(log_line(PING).encode())
    rig.t.poll()
    assert [e.key for e in rig.got()] == [PING.key]
    assert any("truncated" in d for d in rig.diag)
    rig.append(log_line(FIRE))
    rig.t.poll()
    assert [e.key for e in rig.got()] == [PING.key, FIRE.key]


def test_missing_file_then_created(tmp_path):
    rig = Rig(tmp_path)
    assert not rig.path.exists()
    rig.t.poll()
    st = rig.t.status()
    assert not st["exists"] and not st["open"] and "waiting for" in st["detail"]
    assert any("waiting for" in d for d in rig.diag)
    rig.t.poll()
    assert sum("waiting for" in d for d in rig.diag) == 1, "missing-file notice is not repeated"
    # The file appears after we started: everything in it is new.
    rig.append(log_line(ARM) + log_line(FIRE))
    rig.t.poll()
    assert [e.key for e in rig.got()] == [ARM.key, FIRE.key]
    assert rig.t.status()["open"]


def test_rotation_follows_new_file(tmp_path):
    """Log renamed away (we hold it open with FILE_SHARE_DELETE) and a new one created."""
    rig = Rig(tmp_path)
    rig.append(log_line(HELLO))
    rig.t.poll()
    rig.append(log_line(ARM))  # written to the old file just before rotation
    old = rig.path.with_name("WoWChatLog.old.txt")
    os.rename(rig.path, old)  # would raise PermissionError on Windows without FILE_SHARE_DELETE
    rig.append(log_line(FIRE))  # new file at the original path
    rig.t.poll()
    assert [e.key for e in rig.got()] == [ARM.key, FIRE.key]
    assert any("replaced" in d for d in rig.diag)


def test_replaced_while_closed_between_polls(tmp_path):
    """A different file at the same path (identity change) is followed from its start.

    (Win32 MoveFileEx cannot replace a file another process holds open even with
    FILE_SHARE_DELETE, so the replacement is staged as delete + create.)
    """
    rig = Rig(tmp_path)
    rig.append(log_line(HELLO) * 3)
    rig.t.poll()
    os.remove(rig.path)
    rig.append(log_line(PING))  # brand-new file, shorter than our old offset
    rig.t.poll()
    assert [e.key for e in rig.got()] == [PING.key]


def test_deleted_then_recreated(tmp_path):
    rig = Rig(tmp_path)
    rig.append(log_line(HELLO))
    rig.t.poll()
    os.remove(rig.path)
    rig.t.poll()
    assert any("removed" in d for d in rig.diag)
    assert not rig.t.status()["open"]
    rig.append(log_line(FIRE))
    rig.t.poll()
    assert [e.key for e in rig.got()] == [FIRE.key]


def test_never_writes_to_the_log(tmp_path):
    rig = Rig(tmp_path)
    rig.append(log_line(HELLO) + "x" * 1000 + "\r\n")
    before = rig.path.read_bytes()
    st0 = os.stat(rig.path)
    rig.t.poll()
    rig.t.poll()
    rig.t.stop()
    assert rig.path.read_bytes() == before
    assert os.stat(rig.path).st_mtime_ns == st0.st_mtime_ns


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share-mode semantics")
def test_open_shared_allows_writer_rename_and_delete(tmp_path):
    p = tmp_path / "log.txt"
    p.write_bytes(b"hello\n")
    fh = open_shared(p)
    try:
        with open(p, "ab") as w:  # concurrent writer
            w.write(b"world\n")
        assert os.fstat(fh.fileno()).st_size == 12
        fh.seek(0)
        assert fh.read() == b"hello\nworld\n"
        os.rename(p, tmp_path / "moved.txt")  # FILE_SHARE_DELETE
        os.remove(tmp_path / "moved.txt")
    finally:
        fh.close()
    with pytest.raises(FileNotFoundError):
        open_shared(tmp_path / "nope.txt")


def test_thread_end_to_end(tmp_path):
    rig = Rig(tmp_path, pollMs=20)
    rig.append(log_line(HELLO))
    rig.t.start()
    try:
        assert rig.t.status()["running"]
        time.sleep(0.05)
        rig.append(log_line(FIRE) + pad_lines(7, 3))
        deadline = time.monotonic() + 3
        while not rig.got() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [e.key for e in rig.got()] == [FIRE.key]
    finally:
        rig.t.stop()
    st = rig.t.status()
    assert not st["running"] and not st["open"]


def test_disabled_and_unconfigured(tmp_path):
    diag: list[str] = []
    t = ChatLogTransport({"enabled": False}, SimpleNamespace(chat_log=tmp_path / "x.txt"), lambda e, n: None,
                         diag.append)
    t.start()
    assert not t.status()["running"] and t.status()["detail"] == "disabled in config"
    t2 = ChatLogTransport({}, SimpleNamespace(), lambda e, n: None, diag.append)
    t2.start()
    assert t2.poll() == 0
    assert not t2.status()["running"] and "no chat log path" in t2.status()["detail"]


def test_callback_errors_do_not_stop_tailing(tmp_path):
    path = tmp_path / "WoWChatLog.txt"
    path.write_bytes(b"")
    calls = []

    def boom(ev, name):
        calls.append(ev)
        raise RuntimeError("engine exploded")

    t = ChatLogTransport({}, SimpleNamespace(chat_log=path), boom)
    t.poll()
    with open(path, "ab") as f:
        f.write((log_line(ARM) + log_line(FIRE)).encode())
    assert t.poll() == 2
    assert len(calls) == 2


def test_growth_deltas_reported_for_flush_measurement(tmp_path):
    """diag shows every size change, so `/needle selftest chatlog` can reveal the flush threshold."""
    rig = Rig(tmp_path)
    rig.append("")
    rig.t.poll()
    for _ in range(3):
        rig.append(b"x" * 8191 + b"\n")
        rig.t.poll()
    rig.t.poll()  # no change -> no new line
    growth = [d for d in rig.diag if "grew" in d]
    assert len(growth) == 3 and all("+8192 bytes" in d for d in growth)
    assert rig.t.status()["lastGrowthBytes"] == 8192


def test_restart_does_not_replay(tmp_path):
    rig = Rig(tmp_path)
    rig.append(log_line(HELLO))
    rig.t.start()
    rig.t.stop()
    rig.append(log_line(FIRE))  # written while we were stopped
    rig.t.start()
    try:
        rig.append(log_line(PING))
        deadline = time.monotonic() + 3
        while not rig.got() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
    finally:
        rig.t.stop()
    assert [e.key for e in rig.got()] == [PING.key]


# ------------------------------------------------------- strict matching --

from needledropper.transport.chatlog import parse_strict, strict_record  # noqa: E402


def test_strict_matching_accepts_system_lines_only(tmp_path):
    """Default "strict": a record counts only right after the timestamp (a system message) or at
    the start of the line; another player typing a valid record in chat cannot fire a drop."""
    rig = Rig(tmp_path)
    rig.append("")
    rig.t.poll()
    rec = protocol.format_chat_line(FIRE)
    rig.append(f"11/5 18:01:02.345  [Thrall] says: {rec}\r\n"
               f"11/5 18:01:02.346  [2. Trade - City] [Thrall-Silvermoon]: {rec}\r\n"
               f"11/5 18:01:02.347  [Jaina] whispers: {rec}\r\n"
               f"11/5 18:01:02.348  |Hchannel:GUILD|h[Guild]|h Thrall: {rec}\r\n"
               f"11/5 18:01:02.349  Thrall says: {rec}\r\n")
    rig.t.poll()
    assert rig.got() == []
    st = rig.t.status()
    assert st["match"] == "strict" and st["rejectedLines"] == 5 and st["badLines"] == 5 and st["ndropLines"] == 5
    hints = [d for d in rig.diag if "not a NeedleDropper system message" in d]
    assert len(hints) == 5 and all('"match": "anywhere"' in h for h in hints)
    rig.append(log_line(ARM) + log_line(PING, stamp="11/5/2026 9:01:02") + protocol.format_chat_line(HELLO) + "\n")
    rig.t.poll()
    assert [e.key for e in rig.got()] == [ARM.key, PING.key, HELLO.key]


def test_anywhere_matching_keeps_the_v1_behaviour(tmp_path):
    rig = Rig(tmp_path, match="anywhere")
    rig.append("")
    rig.t.poll()
    rig.append(f"11/5 18:01:02.345  [Thrall] says: {protocol.format_chat_line(FIRE)}\r\n")
    rig.t.poll()
    assert [e.key for e in rig.got()] == [FIRE.key]
    assert rig.t.status()["match"] == "anywhere" and rig.t.status()["rejectedLines"] == 0


def test_unknown_match_mode_falls_back_to_strict(tmp_path):
    rig = Rig(tmp_path, match="sometimes")
    assert rig.t.match == "strict"
    rig.t.start()
    rig.t.stop()
    assert any("unknown transport.chatlog.match" in d for d in rig.diag)


def test_strict_record_and_parse_strict():
    rec = protocol.format_chat_line(FIRE)
    for ok in (f"11/5 18:01:02.345  {rec}", f"1/1 0:00:00 {rec}", f"11/05/26 18:01:02.3\t{rec}", rec):
        assert strict_record(ok) == rec, ok
        ev = parse_strict(ok)
        assert ev is not None and ev.key == FIRE.key and ev.id == "zone:1429"
    for bad in (f"[Thrall] says: {rec}", f"11/5 18:01:02.345  [Thrall] says: {rec}", f"x {rec}",
                f"11/5 18:01  {rec}", f" {rec}", f"11/5 18:01:02.345{rec}"):
        assert strict_record(bad) is None and parse_strict(bad) is None, bad
    # a valid record later in an anchored line does not count either
    junk = f"11/5 18:01:02.345  NDROP1 junk {rec}"
    assert strict_record(junk) is not None and parse_strict(junk) is None
    assert protocol.parse_chat_line(junk) is not None  # ("anywhere" would have taken it)
    tampered = rec[:-4] + ("0000" if rec[-4:] != "0000" else "0001")
    assert parse_strict(f"11/5 18:01:02.345  {tampered}") is None
