"""Atomic file writes that survive Windows' quirks (no imports from the rest of the package).

Every file the companion writes (config.json, the Spotify sign-in, the
``NeedleDropper_Companion`` data addon, caches, SavedVariables backups) goes through
:func:`atomic_write_bytes`: the bytes go to a temp file in the target's folder, which then
replaces the target with ``os.replace`` -- so a reader (the game, another NeedleDropper)
sees the old file or the new one, never half of one.

Two failure modes are handled instead of losing the write:

* **The target is briefly locked** (``PermissionError``: an editor, a virus scanner, the
  game reading it): ``os.replace`` is retried ``retries`` times, ``delay`` seconds apart.
* **The rename crosses a "device"** (``OSError`` with ``winerror`` 17,
  ``ERROR_NOT_SAME_DEVICE``, i.e. ``errno.EXDEV``): seen on a real PC when NeedleDropper ran
  under a packaged (MSIX) host whose ``%APPDATA%`` is virtualised -- the temp file and the
  existing target live in different stores, and Windows refuses to rename between them.
  The data is then written **in place** (open the target for writing, truncate, write,
  flush), which is not atomic but is the only write that works there; a warning is logged
  once per path.

Nothing here retries forever or swallows other errors: they propagate, so callers that
must never lose data (the Spotify tokens) can keep it in memory and report it.
"""
from __future__ import annotations

import errno
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

log = logging.getLogger("needledropper.files")

PathLike = Union[str, "os.PathLike[str]"]

#: Windows' ERROR_NOT_SAME_DEVICE ("The system cannot move the file to a different disk drive").
ERROR_NOT_SAME_DEVICE = 17
DEFAULT_RETRIES = 10
DEFAULT_DELAY = 0.2

_warned: set[str] = set()
_warned_lock = threading.Lock()


def is_cross_device(exc: BaseException) -> bool:
    """True for the "cannot rename across devices" error (WinError 17 / EXDEV)."""
    if not isinstance(exc, OSError):
        return False
    return getattr(exc, "winerror", None) == ERROR_NOT_SAME_DEVICE or exc.errno == errno.EXDEV


def _warn_once(path: Path, exc: BaseException) -> None:
    key = os.path.normcase(str(path))
    with _warned_lock:
        if key in _warned:
            return
        _warned.add(key)
    log.warning("Windows refused to replace %s atomically (%s); writing it in place instead. This happens when "
                "the app data folder is virtualised (e.g. NeedleDropper was started by a packaged app).", path, exc)


def write_in_place(path: PathLike, data: bytes, *, fsync: bool = True) -> Path:
    """Overwrite ``path`` with ``data`` directly (truncate + write + flush). Not atomic."""
    target = Path(path)
    with open(target, "wb") as fh:
        fh.write(data)
        fh.flush()
        if fsync:
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
    return target


def atomic_write_bytes(path: PathLike, data: bytes, *, retries: int = DEFAULT_RETRIES, delay: float = DEFAULT_DELAY,
                       fsync: bool = True, prefix: Optional[str] = None,
                       sleep: Optional[Callable[[float], None]] = None,
                       replace: Optional[Callable[[str, Path], None]] = None) -> Path:
    """Write ``data`` to ``path`` through a temp file in the same folder (see the module doc).

    Creates the parent folder. ``replace`` (default ``os.replace``) and ``sleep`` (default
    ``time.sleep``, both looked up at call time) are test seams. Returns the
    target path. Raises the last ``PermissionError`` when the target stays locked, and any
    other ``OSError`` as it is.
    """
    target = Path(path)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("atomic_write_bytes() expects bytes")
    data = bytes(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    do_replace = replace or os.replace
    do_sleep = sleep or time.sleep
    fd, tmp_name = tempfile.mkstemp(prefix=prefix if prefix is not None else f".{target.name}.", suffix=".tmp",
                                    dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            if fsync:
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
        attempts = max(1, int(retries))
        last: Optional[BaseException] = None
        for attempt in range(attempts):
            try:
                do_replace(tmp_name, target)
                return target
            except PermissionError as exc:
                last = exc
                if attempt + 1 < attempts:
                    do_sleep(delay)
            except OSError as exc:
                if not is_cross_device(exc):
                    raise
                _warn_once(target, exc)
                return write_in_place(target, data, fsync=fsync)
        assert last is not None
        raise last
    finally:
        try:
            os.remove(tmp_name)  # still there only when the replace did not happen
        except FileNotFoundError:
            pass
        except OSError:
            log.debug("could not remove the temp file %s", tmp_name)


def atomic_write_text(path: PathLike, text: str, *, encoding: str = "utf-8", **kw) -> Path:
    return atomic_write_bytes(path, text.encode(encoding), **kw)


__all__ = ["ERROR_NOT_SAME_DEVICE", "atomic_write_bytes", "atomic_write_text", "is_cross_device", "write_in_place"]
