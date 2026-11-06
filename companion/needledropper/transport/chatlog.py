"""Chat-log transport (docs/SPEC.md 3.6, docs/DESIGN-v1.2.md 2.1): tail WoWChatLog.txt for NeedleDropper records.

The addon (when ``settings.chatlog`` is on) enables chat logging and emits
system messages hidden from the chat frame by a message filter: the 1.2 addon
writes ``NDROP2 <base64url frame>`` -- exactly the bytes its pixel beacon shows,
the song included (:func:`needledropper.protocol2.parse_chat_line`) -- and
addons older than 1.2 wrote ``NDROP1 <seq> <type> <n> <hash> <flags> <id> <crc>``.
Both are accepted: an NDROP2 line becomes a :class:`~needledropper.protocol2.Frame`,
an NDROP1 line a v1 :class:`~needledropper.models.Event`. The client buffers the
log (~48 KiB), so after FIRE / CANCEL / PING the addon also writes ``NDPAD``
padding lines to force a flush. This transport:

* opens the log **read-only with full sharing** (``FILE_SHARE_READ |
  FILE_SHARE_WRITE | FILE_SHARE_DELETE`` via ``CreateFileW`` on Windows) so the
  game can keep writing, truncating, renaming or deleting it - we never write;
* starts at the **end** of an existing file (no history replay). A file that
  appears *after* start (missing at start, or replaced/rotated later) is read
  from its beginning, since everything in it is new;
* polls the size every ``pollMs`` (default 250) on its own handle, reads only the
  new bytes, keeps a partial trailing line until its newline arrives, splits on
  ``\\n`` (stripping ``\\r``), decodes UTF-8 with ``errors="replace"`` and
  parses each record line (:func:`parse_line`);
* handles truncation (size < offset -> reopen from 0) and replacement
  (path now names a different file -> drain the old handle, follow the new
  file from 0) and a missing file (wait, report in :meth:`status`).

``diag`` (if given) receives one line per file growth (``+N bytes``; useful to
measure the client's flush threshold during ``/needle selftest chatlog``),
every NDROP1 line with its decode result, and NDPAD counts.

Matching (config ``match``):

* ``"strict"`` (default) -- a record counts only when the line is the client's
  timestamp immediately followed by the record (how WoW logs a system message,
  e.g. ``11/5 18:01:02.345  NDROP2 ...``), or the line itself starts with
  ``NDROP2 `` / ``NDROP1 ``. Chat from other players always carries a
  ``[Name] says:`` / ``[2. Trade] [Name]:`` prefix, so nobody can fire a drop by
  typing a fake record in chat. In diag mode a line that contains ``NDROP1`` /
  ``NDROP2`` but fails this check is reported with a hint.
* ``"anywhere"`` -- the v1.0 behaviour: a valid record anywhere in a line counts.
"""
from __future__ import annotations

import errno
import io
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .. import protocol, protocol2
from ..models import Event
from ..protocol2 import Frame
from .base import EventCallback, Transport

log = logging.getLogger(__name__)

DiagCallback = Callable[[str], None]
AnyEvent = Union[Event, Frame]

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes as _wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _k32.CreateFileW
    _CreateFileW.restype = _wt.HANDLE
    _CreateFileW.argtypes = [_wt.LPCWSTR, _wt.DWORD, _wt.DWORD, ctypes.c_void_p, _wt.DWORD, _wt.DWORD,
                             _wt.HANDLE]
    _CloseHandle = _k32.CloseHandle
    _CloseHandle.restype = _wt.BOOL
    _CloseHandle.argtypes = [_wt.HANDLE]
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_ERROR_SHARING_VIOLATION = 32

PAD_MARKER = protocol.PAD_PREFIX.encode("ascii") + b" "  # b"NDPAD "
DROP_MARKER = protocol.CHAT_PREFIX.encode("ascii")  # b"NDROP1" (addons before 1.2)
DROP2_MARKER = protocol2.CHAT_PREFIX.encode("ascii")  # b"NDROP2"

MATCH_STRICT = "strict"
MATCH_ANYWHERE = "anywhere"
#: A system message in the chat log: the client's timestamp, whitespace, then the record.
STRICT_LINE_RE = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})? \d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\s+NDROP1 ", re.ASCII)
_RECORD_START = protocol.CHAT_PREFIX + " "  # "NDROP1 "


def strict_record(line: str) -> Optional[str]:
    """The line from its anchored ``NDROP1`` on, if the line passes strict matching, else None.

    Accepted: ``<timestamp><whitespace>NDROP1 ...`` (a system line) or a line
    that starts with ``NDROP1 ``.
    """
    if line.startswith(_RECORD_START):
        return line
    m = STRICT_LINE_RE.match(line)
    if not m:
        return None
    return line[m.end() - len(_RECORD_START):]


def parse_strict(line: str) -> Optional[Event]:
    """:func:`needledropper.protocol.parse_chat_line` with strict matching: the record
    must start at the anchor (a valid record later in the same line does not count)."""
    rec = strict_record(line)
    if rec is None:
        return None
    m = protocol.CHAT_LINE_RE.match(rec)
    if not m:
        return None
    return protocol.parse_chat_line(m.group(0))


def is_strict_record(line: str) -> bool:
    """Whether ``line`` passes strict matching for an NDROP2 or an NDROP1 record."""
    return protocol2.find_chat_record(line, strict=True) is not None or strict_record(line) is not None


def parse_line(line: str, *, match: str = "strict") -> Optional[AnyEvent]:
    """The record in one chat-log line: a v2 ``Frame`` for ``NDROP2 <base64url frame>``, a v1
    ``Event`` for ``NDROP1 ...``; None for anything else or an invalid record. ``match`` is
    ``"strict"`` (the record must be the whole system message) or ``"anywhere"``."""
    strict = match != MATCH_ANYWHERE
    if protocol2.CHAT_PREFIX in line:
        fr = protocol2.parse_chat_line(line, strict=strict)
        if fr is not None:
            return fr
    if protocol.CHAT_PREFIX in line:
        return parse_strict(line) if strict else protocol.parse_chat_line(line)
    return None


def open_shared(path: os.PathLike[str] | str) -> io.RawIOBase:
    """Open ``path`` read-only without blocking the writer (unbuffered binary file).

    On Windows the handle allows other processes to read, write, truncate,
    rename and delete the file while we hold it (plain ``open()`` would not
    allow rename/delete). Raises FileNotFoundError / PermissionError / OSError.
    """
    if not IS_WINDOWS:
        return open(path, "rb", buffering=0)  # POSIX never blocks other writers
    p = os.fspath(path)
    h = _CreateFileW(p, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, None,
                     OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if h is None or h == _INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        msg = ctypes.FormatError(err).strip()
        if err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
            raise FileNotFoundError(errno.ENOENT, msg, p)
        if err in (_ERROR_ACCESS_DENIED, _ERROR_SHARING_VIOLATION):
            raise PermissionError(errno.EACCES, msg, p)
        raise OSError(errno.EIO, f"CreateFileW failed (Win32 error {err}: {msg})", p)
    try:
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
    except Exception:
        _CloseHandle(h)
        raise
    return os.fdopen(fd, "rb", buffering=0)  # closing the file object closes the handle


def _identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _describe(ev: AnyEvent) -> str:
    if isinstance(ev, Frame):
        return ev.describe()
    return (f"{ev.type.name} seq={ev.seq} n={ev.n} hash={ev.hash} flags=0x{int(ev.flags):02x}"
            f"{' id=' + ev.id if ev.id else ''}")


class ChatLogTransport(Transport):
    """Tails the WoW chat log and emits parsed records as ``on_event(ev, "chatlog")``
    (``ev``: a v2 :class:`~needledropper.protocol2.Frame` for NDROP2 lines, a v1 ``Event`` for NDROP1).

    Config (``transport.chatlog``): ``enabled`` (True), ``pollMs`` (250),
    ``match`` ("strict" | "anywhere", default "strict"; see the module docs).
    ``paths.chat_log`` names the file. :meth:`poll` runs one iteration
    synchronously (used by tests and tools); :meth:`start` runs it on a daemon
    thread every ``pollMs``.
    """

    name = "chatlog"
    MAX_PARTIAL = 1 << 20  # a "line" longer than this without a newline is discarded
    READ_CHUNK = 1 << 20

    def __init__(self, config_section: Optional[dict[str, Any]], paths: Any, on_event: EventCallback,
                 diag: Optional[DiagCallback] = None, *, wall: Callable[[], float] = time.time) -> None:
        super().__init__(dict(config_section or {}), paths, on_event)
        cfg = self.config
        self.diag = diag
        self.wall = wall
        self.enabled = bool(cfg.get("enabled", True))
        try:
            poll_ms = float(cfg.get("pollMs", 250))
        except (TypeError, ValueError):
            poll_ms = 250.0
        self.poll_s = min(5.0, max(0.02, poll_ms / 1000.0))
        match = str(cfg.get("match") or MATCH_STRICT).strip().lower()
        self._config_warning: Optional[str] = None
        if match not in (MATCH_STRICT, MATCH_ANYWHERE):
            self._config_warning = (f"unknown transport.chatlog.match {cfg.get('match')!r}; using 'strict' "
                                    "(valid: strict, anywhere)")
            match = MATCH_STRICT
        self.match = match
        raw_path = getattr(paths, "chat_log", None)
        self.path: Optional[Path] = Path(raw_path) if raw_path else None

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._begun = False
        self._existed_at_start = False
        self._fh: Optional[io.RawIOBase] = None
        self._ident: Optional[tuple[int, int]] = None
        self._offset = 0
        #: Where to continue after an error closed the file (CHAT-REPLAY-ON-ERROR): (identity, offset,
        #: partial line). A later open of the *same* file resumes there instead of replaying it from 0.
        self._resume: Optional[tuple[tuple[int, int], int, bytes]] = None
        self._partial = b""
        self._last_size: Optional[int] = None
        self._missing_reported = False
        # stats
        self._events = 0
        self._drop_lines = 0
        self._bad_lines = 0
        self._rejected_lines = 0  # contained NDROP1 but failed strict matching (subset of bad lines)
        self._pad_lines = 0
        self._bytes_read = 0
        self._last_growth_wall: Optional[float] = None
        self._last_growth_bytes = 0
        self._last_event: Optional[AnyEvent] = None
        self._last_event_wall: Optional[float] = None
        self._last_error: Optional[str] = None
        self._opened_at_wall: Optional[float] = None

    # ------------------------------------------------------------ lifecycle --
    def start(self) -> None:
        """Remember where the log ends now and start the polling thread."""
        if self._config_warning:
            self._say(f"chatlog: {self._config_warning}")
            self._config_warning = None
        if not self.enabled:
            self._say("chatlog: disabled in config (transport.chatlog.enabled = false)")
            return
        if self.path is None:
            self._last_error = "no chat log path configured (paths.chat_log)"
            self._say(f"chatlog: not started - {self._last_error}")
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._begin()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="needledropper-chatlog", daemon=True)
            self._thread.start()
        self._say(f"chatlog: tailing {self.path} every {self.poll_s * 1000:.0f} ms")

    def stop(self) -> None:
        """Stop the thread and close the file (the log itself is never modified)."""
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=max(2.0, 3 * self.poll_s))
        with self._lock:
            self._thread = None
            self._close()
            self._begun = False  # a later start() skips to the then-current end again

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as e:  # pragma: no cover - poll() guards itself
                log.exception("chatlog poll failed")
                self._last_error = f"unexpected error: {e}"
            self._stop.wait(self.poll_s)

    # ----------------------------------------------------------------- core --
    def _begin(self) -> None:
        """Fix the starting point: the current end of the log (if it exists)."""
        if self._begun:
            return
        self._begun = True
        assert self.path is not None
        self._existed_at_start = self.path.exists()
        if self._existed_at_start:
            try:  # remember where the log ends now, even if the first open fails (sharing violation, AV)
                st = os.stat(self.path)
                self._resume = (_identity(st), st.st_size, b"")
            except OSError:
                pass
            self._open(from_start=False)

    def poll(self) -> int:
        """One iteration: follow the file, read new bytes, emit events. Returns #events."""
        if self.path is None:
            return 0
        with self._lock:
            if not self._begun:
                self._begin()
            emitted: list[AnyEvent] = []
            try:
                self._poll_locked(emitted)
            except OSError as e:
                self._last_error = f"{e.__class__.__name__}: {e}"
                self._say(f"chatlog: read error ({self._last_error}); reopening")
                self._close(keep_position=True)
        for ev in emitted:
            try:
                self.on_event(ev, self.name)
            except Exception:
                log.exception("chatlog on_event callback failed")
        return len(emitted)

    def _poll_locked(self, emitted: list[AnyEvent]) -> None:
        assert self.path is not None
        if self._fh is None:
            if not self._open(from_start=True):
                return
        fh = self._fh
        assert fh is not None
        # Did the path start naming a different file (rotated/replaced) or vanish?
        try:
            path_ident: Optional[tuple[int, int]] = _identity(os.stat(self.path))
        except FileNotFoundError:
            path_ident = None
        except OSError:
            path_ident = self._ident  # transient (e.g. sharing) error: keep going
        if path_ident != self._ident:
            self._read_new(emitted)  # drain whatever the old file still has
            self._flush_partial(emitted)
            self._say("chatlog: log file was removed" if path_ident is None
                      else "chatlog: log file was replaced; following the new file from its start")
            self._close()
            if path_ident is None or not self._open(from_start=True):
                return
            fh = self._fh
            assert fh is not None
        size = os.fstat(fh.fileno()).st_size
        if size < self._offset:
            self._say(f"chatlog: log truncated ({self._offset} -> {size} bytes); re-reading from the start")
            self._close()
            if not self._open(from_start=True):
                return
            size = os.fstat(self._fh.fileno()).st_size  # type: ignore[union-attr]
        self._note_size(size)
        if size > self._offset:
            self._read_new(emitted, size)

    def _open(self, *, from_start: bool) -> bool:
        assert self.path is not None
        try:
            fh = open_shared(self.path)
        except FileNotFoundError:
            if not self._missing_reported:
                self._missing_reported = True
                self._say(f"chatlog: waiting for {self.path} (chat logging off? '/needle chatlog on' "
                          "in game, or type /chatlog)")
            return False
        except OSError as e:
            msg = f"cannot open {self.path}: {e}"
            if msg != self._last_error:
                self._say(f"chatlog: {msg}")
            self._last_error = msg
            return False
        try:
            st = os.fstat(fh.fileno())
        except OSError:
            fh.close()
            raise
        self._fh = fh
        self._ident = _identity(st)
        resume, self._resume = self._resume, None
        if resume is not None and resume[0] == self._ident and st.st_size >= resume[1]:
            # the same file we were reading before an error: carry on where we were (never replay it)
            self._offset, self._partial, self._last_size = resume[1], resume[2], resume[1]
            where = f"at byte {resume[1]} (where reading stopped)"
        else:
            self._offset = 0 if from_start else st.st_size
            self._partial = b""
            self._last_size = None if from_start else st.st_size
            if resume is not None and resume[0] == self._ident:
                where = "from the start (it was truncated in the meantime)"
            else:
                where = "from the start" if from_start else f"at the end ({st.st_size} bytes; history skipped)"
        self._missing_reported = False
        self._last_error = None
        self._opened_at_wall = self.wall()
        self._say(f"chatlog: opened {self.path} {where}")
        return True

    def _close(self, *, keep_position: bool = False) -> None:
        """Close the log. ``keep_position`` (after a read error) remembers the file and offset so the
        next open of the same file resumes there; otherwise (rotation, truncation, stop) it is forgotten."""
        if keep_position and self._ident is not None:
            self._resume = (self._ident, self._offset, self._partial)
        elif not keep_position:
            self._resume = None
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
        self._ident = None
        self._partial = b""

    def _note_size(self, size: int) -> None:
        prev = self._last_size
        if prev is not None and size != prev:
            delta = size - prev
            self._last_growth_bytes = delta
            self._last_growth_wall = self.wall()
            self._say(f"chatlog: file {'grew' if delta > 0 else 'shrank'} {delta:+d} bytes (size {size})")
        elif prev is None and size > self._offset:
            self._last_growth_bytes = size - self._offset
            self._last_growth_wall = self.wall()
            self._say(f"chatlog: +{size - self._offset} bytes to read (size {size})")
        self._last_size = size

    def _read_new(self, emitted: list[AnyEvent], size: Optional[int] = None) -> None:
        fh = self._fh
        if fh is None:
            return
        if size is None:
            size = os.fstat(fh.fileno()).st_size
        pads_before = self._pad_lines
        while self._offset < size:
            fh.seek(self._offset)
            chunk = fh.read(min(self.READ_CHUNK, size - self._offset))
            if not chunk:
                break
            self._offset += len(chunk)
            self._bytes_read += len(chunk)
            self._feed(chunk, emitted)
        pads = self._pad_lines - pads_before
        if pads:
            self._say(f"chatlog: skipped {pads} NDPAD padding line{'s' if pads != 1 else ''} "
                      f"(total {self._pad_lines})")

    def _feed(self, chunk: bytes, emitted: list[AnyEvent]) -> None:
        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()  # incomplete trailing line (b"" if chunk ended with \n)
        if len(self._partial) > self.MAX_PARTIAL:
            self._say(f"chatlog: discarding a {len(self._partial)}-byte line with no newline")
            self._partial = b""
        for raw in lines:
            self._line(raw.rstrip(b"\r"), emitted)

    def _flush_partial(self, emitted: list[AnyEvent]) -> None:
        """At end of a file that is going away, treat a dangling partial as a full line."""
        if self._partial:
            raw, self._partial = self._partial, b""
            self._line(raw.rstrip(b"\r"), emitted)

    def _line(self, raw: bytes, emitted: list[AnyEvent]) -> None:
        if PAD_MARKER in raw:
            self._pad_lines += 1
            return
        v2 = DROP2_MARKER in raw
        if not v2 and DROP_MARKER not in raw:
            return  # ordinary chat
        text = raw.decode("utf-8", errors="replace")
        self._drop_lines += 1
        marker = "NDROP2" if v2 else "NDROP1"
        shown = text.strip()
        if len(shown) > 200:
            shown = shown[:200] + "..."
        if self.match == MATCH_STRICT and not is_strict_record(text):
            self._bad_lines += 1
            self._rejected_lines += 1
            self._hint(f"chatlog: ignored a line that contains {marker} but is not a NeedleDropper system "
                       f"message (strict matching: probably chat typed by a player): {shown}\n"
                       "  If your own records are ignored like this (an unusual chat-log timestamp format), "
                       "set \"transport\": {\"chatlog\": {\"match\": \"anywhere\"}} in config.json.")
            return
        try:
            ev = parse_line(text, match=self.match)
        except (ValueError, UnicodeError) as exc:  # CHAT-UNICODE-RAISE: one bad line never drops the chunk
            log.debug("chatlog: unparsable line (%s)", exc)
            ev = None
        if ev is None:
            self._bad_lines += 1
            what = "bad format or checksum" if not v2 else "bad Base64, length or checksum"
            self._say(f"chatlog: ignored malformed {marker} line ({what}): {shown}")
            return
        self._events += 1
        self._last_event = ev
        self._last_event_wall = self.wall()
        self._say(f"chatlog: {shown} -> {_describe(ev)}")
        emitted.append(ev)

    def _say(self, line: str) -> None:
        log.info(line)
        if self.diag is not None:
            try:
                self.diag(line)
            except Exception:
                log.exception("chatlog diag callback failed")

    def _hint(self, line: str) -> None:
        """Diagnostic for ``needledropper diag`` only (debug level in the normal log)."""
        log.debug(line)
        if self.diag is not None:
            try:
                self.diag(line)
            except Exception:
                log.exception("chatlog diag callback failed")

    # --------------------------------------------------------------- status --
    def status(self) -> dict[str, Any]:
        with self._lock:
            exists = bool(self.path and self.path.exists())
            open_ = self._fh is not None
            running = bool(self._thread is not None and self._thread.is_alive())
            if not self.enabled:
                detail = "disabled in config"
            elif self.path is None:
                detail = "no chat log path configured"
            elif open_:
                detail = f"tailing {self.path} at byte {self._offset}"
                if self._last_event is not None:
                    detail += f"; last event {_describe(self._last_event)}"
            elif self._last_error:
                detail = self._last_error
            elif not exists:
                detail = (f"waiting for {self.path} - enable logging in game ('/needle chatlog on' or "
                          "/chatlog)")
            else:
                detail = "not started"
            return {
                "name": self.name,
                "enabled": self.enabled,
                "running": running,
                "ok": bool(self.enabled and open_ and self._last_error is None),
                "file": str(self.path) if self.path else None,
                "exists": exists,
                "open": open_,
                "offset": self._offset if open_ else None,
                "size": self._last_size,
                "partialBytes": len(self._partial),
                "lastGrowth": self._last_growth_wall,
                "lastGrowthBytes": self._last_growth_bytes,
                "bytesRead": self._bytes_read,
                "events": self._events,
                "ndropLines": self._drop_lines,
                "badLines": self._bad_lines,
                "rejectedLines": self._rejected_lines,
                "match": self.match,
                "padLines": self._pad_lines,
                "lastEvent": _describe(self._last_event) if self._last_event else None,
                "lastEventAt": self._last_event_wall,
                "lastError": self._last_error,
                "detail": detail,
            }


__all__ = ["ChatLogTransport", "open_shared", "strict_record", "parse_strict", "parse_line", "is_strict_record",
           "STRICT_LINE_RE", "MATCH_STRICT", "MATCH_ANYWHERE"]
