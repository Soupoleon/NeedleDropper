"""Playback engine (docs/DESIGN-v1.2.md 2.1, 2.2, 2.5; docs/CONTRACTS-v1.2.md 3.2).

Transports call :meth:`Engine.submit` from their own threads with ``(event, transport name)``,
where ``event`` is a v2 :class:`~needledropper.protocol2.Frame` (addon 1.2+) or a legacy v1
:class:`~needledropper.models.Event` (addon 1.1). Everything else happens on the single engine
thread (:meth:`Engine.run`), so providers see one caller -- except the ARM pre-warm, which runs on
one background worker so that a slow Spotify search never delays the beacon. A FIRE that arrives
while that pre-warm still runs waits for it (``fire_wait_sec``, at most 3 s) instead of failing:
landmark drops can FIRE 3-4 s after their ARM.

v2 frames carry the song itself, so the engine needs no map and keeps no soundtrack state:

* ``ARM``    -- cache the song by ``placeKey`` (a later ``placeKey``-only ARM only refreshes it) and
  pre-warm: a Spotify search for a drop without a Spotify id (cached by query, and recorded for the
  game through ``gamedata.record_track``), the Spotify device wake, and the YouTube player preload
  (whatever the provider's ``prepare`` does).
* ``FIRE``   -- the song from the frame, completed from the ARM cache of its place (the beacon's fit
  rule may have left fields out). ``LOCAL_PLAYBACK``: show and log the song only (the game plays
  its own file). Otherwise, in this order: Spotify with the frame's id; Spotify with the search
  result for the query (when Spotify is available); YouTube with the frame's id; then any other
  provider that can play it. A provider that fails, or reports an error shortly after starting,
  falls through to the next. ``TEST`` (Test, Replay, ``/needle test``): play, but record nothing;
  the drop id ``test`` carries no song and plays nothing.
* ``CANCEL`` -- fade out the drop playing at that place (or forget an armed one).
* ``HELLO``  -- the addon is alive; while a drop plays, a HELLO without ``DROP_ACTIVE`` for more
  than 5 s stops it.
* ``PING``   -- logged prominently, never played.

Every frame with the ``TEST`` flag, and every PING, calls ``on_link_check(frame, transport)``;
notable changes call ``on_status(status)`` with :meth:`Engine.status` plus a ``change`` entry
(``{"kind": "addon" | "armed" | "playing" | "local" | "stopped" | "match" | "notFound" |
"notEmbeddable" | "searchFailed" | "outdatedAddon" | "noSource", ...}``; ``searchFailed`` has
``unavailable=True`` when Spotify itself is unusable, e.g. the sign-in expired). Both callbacks run
on the engine thread after the event was handled, outside the engine lock.

Spotify search results, tracks behind pasted Spotify links, and failures (``notFound``,
``notEmbeddable``) are recorded for the game (``NeedleDropper_Companion``) when the engine knows
the AddOns folder (``addons_dir=``) or gets a ``track_recorder``; frames with ``TEST`` record
nothing.

Legacy v1 events (``(hash, n)`` into a ``Map.json``) still play when a map registry is given
(``registry=``, any object with ``resolve_event(ev)`` and ``describe_miss(ev)``); without one they
are logged once with an "update the addon" message and ignored (HELLO and PING still count).

Events arriving over both transports are de-duplicated for 15 s by ``(seq, type, placeKey, flags)``
(v2) or ``(seq, type, n, hash)`` (v1).
"""
from __future__ import annotations

import dataclasses
import logging
import math
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Union

from .gamedata import query_track_key
from .models import Drop, Event, EventType, Flags
from .protocol2 import Frame
from .providers.base import NowPlaying, PlayHandle, Provider, ProviderError, ProviderUnavailable

LogLike = Union[logging.Logger, Callable[[str], None], None]
AnyEvent = Union[Frame, Event]
TrackRecorder = Callable[[str, dict], None]

DEDUPE_SEC = 15.0
HELLO_STALE_SEC = 5.0
POLL_INTERVAL_SEC = 1.0
SWITCH_FADE_MS = 1000
FALLBACK_WINDOW_SEC = 15.0
CARD_ONLY_SEC = 12.0  # how long the card stays up when nothing could play
LOCAL_CARD_CAP_SEC = 600.0  # in-game drop of unknown length: drop the card after this at the latest
FIRE_WAIT_SEC = 3.0  # a FIRE waits at most this long for its ARM's pre-warm to finish
ARM_TTL_SEC = 30 * 60.0  # an armed song nobody refreshed or fired is forgotten after this
MAX_ARMED = 64
NOT_FOUND_TTL_SEC = 30 * 60.0  # a failed Spotify search is retried after this
MAX_RESOLUTIONS = 512
ADDON_GONE_SEC = 120.0  # no frame for this long: the next one is reported as "addon seen" again

#: YouTube player error codes that mean "this video cannot be played embedded" (or is gone).
YT_UNEMBEDDABLE = frozenset((100, 101, 150, 153))
_YT_CODE_RE = re.compile(r"YouTube error (\d+)")


class _Log:
    """Routes engine messages to a Logger or a plain ``callable(str)``."""

    def __init__(self, target: LogLike) -> None:
        if target is None:
            target = logging.getLogger("needledropper.engine")
        self._logger = target if isinstance(target, logging.Logger) else None
        self._fn = None if self._logger else target

    def _emit(self, level: int, msg: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.log(level, msg, *args)
            return
        text = msg % args if args else msg
        if level >= logging.WARNING:
            text = f"{logging.getLevelName(level)}: {text}"
        if level >= logging.INFO and self._fn is not None:
            self._fn(text)

    def debug(self, msg: str, *a: Any) -> None:
        self._emit(logging.DEBUG, msg, *a)

    def info(self, msg: str, *a: Any) -> None:
        self._emit(logging.INFO, msg, *a)

    def warning(self, msg: str, *a: Any) -> None:
        self._emit(logging.WARNING, msg, *a)

    def error(self, msg: str, *a: Any) -> None:
        self._emit(logging.ERROR, msg, *a)


@dataclass
class _Route:
    """One way to play a drop: a provider and the drop as that provider should get it."""

    provider: Provider
    drop: Drop
    search: bool = False  # Spotify by artist + title (no id): resolve first, record the result


@dataclass
class _Active:
    """The drop currently on screen (and usually playing)."""

    drop: Drop
    started_at: float
    origin: str  # transport name, or "cli"
    map_hash: Optional[int] = None
    local: bool = False  # addon plays it in game; we only show the card
    test: bool = False
    version: int = 1  # protocol of the event that started it (1 also for play_now)
    place_key: Optional[int] = None
    query_key: Optional[str] = None
    frame_drop_id: Optional[str] = None  # the drop id as the FIRE frame carried it (None if fitted away)
    routes: list[_Route] = field(default_factory=list)
    next_route: int = 0
    handle: Optional[PlayHandle] = None
    provider: Optional[Provider] = None
    route: Optional[_Route] = None
    stop_at: Optional[float] = None  # when to begin the end-of-drop fade
    end_at: Optional[float] = None  # nominal end of the drop
    next_poll_at: Optional[float] = None
    tried: list[str] = field(default_factory=list)  # provider names attempted for this drop
    play_started_at: Optional[float] = None


@dataclass
class _Resolution:
    """A Spotify search for one query (``q:`` key), done on the pre-warm worker or at FIRE."""

    key: str
    created: float
    done: threading.Event = field(default_factory=threading.Event)
    uri: Optional[str] = None
    title: str = ""
    artist: str = ""
    duration_ms: int = 0
    not_found: bool = False
    error: Optional[str] = None
    finished: Optional[float] = None
    recorded: bool = False


@dataclass
class _Armed:
    """The song of an ARM, kept by place key until its FIRE / CANCEL (or expiry)."""

    frame: Frame
    at: float
    transport: str
    job: Optional["_Job"] = None


class _Job:
    """A pre-warm task for the worker; ``cancelled`` jobs are skipped when their turn comes (then
    ``on_cancel`` runs instead, e.g. to finish a search nobody will run)."""

    def __init__(self, fn: Callable[[], None], place_key: Optional[int],
                 on_cancel: Optional[Callable[[], None]] = None) -> None:
        self.fn = fn
        self.place_key = place_key
        self.on_cancel = on_cancel
        self.cancelled = False
        self.done = threading.Event()


class _Worker:
    """One daemon thread running pre-warm jobs in order (started on first use)."""

    def __init__(self, log: _Log) -> None:
        self._log = log
        self._q: "queue.Queue[Optional[_Job]]" = queue.Queue()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._pending: deque[_Job] = deque()
        self._idle = threading.Event()
        self._idle.set()

    def submit(self, job: _Job) -> None:
        with self._lock:
            self._pending.append(job)
            self._idle.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="needledropper-prewarm", daemon=True)
                self._thread.start()
        self._q.put(job)

    def cancel_except(self, place_key: Optional[int]) -> int:
        """Cancel every queued (not yet started) job for another place; returns how many."""
        n = 0
        with self._lock:
            for job in self._pending:
                if not job.cancelled and job.place_key != place_key:
                    job.cancelled = True
                    n += 1
        return n

    def wait_idle(self, timeout: float) -> bool:
        return self._idle.wait(max(0.0, timeout))

    @property
    def busy(self) -> bool:
        return not self._idle.is_set()

    def stop(self, timeout: float = 1.0) -> None:
        """Cancel queued jobs and end the thread. A stopped worker is not reused (the engine makes a
        new one), so no stop marker is ever left behind for a later thread."""
        with self._lock:
            for job in self._pending:
                job.cancelled = True
            t = self._thread
        if t is None or not t.is_alive():
            return
        self._q.put(None)
        if t is not threading.current_thread():
            t.join(timeout)

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                if not job.cancelled:
                    job.fn()
                elif job.on_cancel is not None:
                    job.on_cancel()
            except Exception as exc:  # a provider bug must never kill the worker
                self._log.warning("pre-warm failed: %r", exc)
                logging.getLogger("needledropper.engine").debug("traceback", exc_info=True)
            finally:
                job.done.set()
                with self._lock:
                    try:
                        self._pending.remove(job)
                    except ValueError:
                        pass
                    if not self._pending:
                        self._idle.set()


def _flag_names(flags: Flags) -> str:
    names = [f.name for f in Flags if f.value and f.name and flags & f]
    return "|".join(names) if names else "-"


def describe_event(ev: AnyEvent, transport: str = "") -> str:
    """One-line description of a v1 event or a v2 frame (logs, ``needledropper diag``)."""
    via = f" via {transport}" if transport else ""
    if isinstance(ev, Frame):
        return ev.describe() + via
    did = f" id={ev.id}" if ev.id else ""
    return (f"{ev.type.name} seq={ev.seq} n={ev.n} hash={ev.hash} flags={_flag_names(ev.flags)}"
            f"{did}{via}")


def query_key(artist: str, title: str) -> str:
    """``tracks`` key of a search (``q:`` + artist + U+001F + title, ASCII letters lowercased; the
    same rule as :func:`needledropper.gamedata.query_track_key`)."""
    return query_track_key(artist, title)


def _has_song(fr: Frame) -> bool:
    return bool(fr.spotify_id or fr.youtube_id or fr.query)


def _abandon(res: Optional[_Resolution]) -> None:
    """Finish a search that will not run (its pre-warm was cancelled), so nobody waits for it."""
    if res is not None and not res.done.is_set():
        res.error = "cancelled"
        res.done.set()


def _kind_for(drop_id: str) -> str:
    if drop_id == "test":
        return "test"
    if drop_id.startswith("inst:"):
        return "instance"
    if drop_id.startswith(("area:", "sub:")):
        return "subzone"
    return "zone"


def _zone_name(drop_id: str) -> str:
    """Zone name for ``zone:<id>`` / ``sub:<id>:...`` ids from the companion's zone table ("" if unknown)."""
    m = re.match(r"(?:zone|sub):(\d+)", drop_id or "")
    if not m:
        return ""
    try:
        from . import zones

        z = zones.lookup(int(m.group(1)))
    except Exception:  # pragma: no cover - the zone table is optional decoration
        return ""
    return z.name if z else ""


def _merge_frames(fire: Frame, armed: Optional[Frame]) -> Frame:
    """``fire`` with the fields it lacks taken from the ARM of the same place (the beacon's fit rule
    may leave out setId, label, query and dropId). Provider ids are never mixed between frames."""
    if armed is None or armed.place_key != fire.place_key:
        return fire
    if fire.drop_id and armed.drop_id and fire.drop_id != armed.drop_id:
        return fire  # a different drop that happens to share the place key
    if (fire.spotify_id and armed.spotify_id and fire.spotify_id != armed.spotify_id) or \
            (fire.youtube_id and armed.youtube_id and fire.youtube_id != armed.youtube_id):
        return fire  # the song changed between ARM and FIRE
    changes: dict[str, Any] = {}
    for name in ("drop_id", "query", "label", "set_id"):
        if getattr(fire, name) is None and getattr(armed, name) is not None:
            changes[name] = getattr(armed, name)
    if not fire.spotify_id and not fire.youtube_id and not fire.query:
        for name in ("spotify_id", "youtube_id", "start_ds", "length_ds", "volume"):
            if getattr(fire, name) is None and getattr(armed, name) is not None:
                changes[name] = getattr(armed, name)
    return dataclasses.replace(fire, **changes) if changes else fire


class Engine:
    """Single-threaded drop player fed by any number of transports.

    ``Engine(providers, deck, *, clock, fade_in_ms, fade_out_ms, log, on_link_check=None,
    on_status=None)`` (CONTRACTS-v1.2 3.2). Also accepted: ``addons_dir`` / ``track_recorder``
    (where Spotify matches and failures are recorded for the game), ``registry`` (legacy v1 map),
    ``prewarm_async`` (False runs the ARM pre-warm inline -- tests and tools), and the timing knobs.
    The v1.1 call ``Engine(providers, registry, deck, ...)`` still works.
    """

    def __init__(self, providers: Sequence[Provider], deck: Any = None, *legacy: Any, registry: Any = None,
                 clock: Callable[[], float] = time.monotonic, fade_in_ms: int = 2500, fade_out_ms: int = 3000,
                 log: LogLike = None, on_link_check: Optional[Callable[[AnyEvent, str], None]] = None,
                 on_status: Optional[Callable[[dict], None]] = None, addons_dir: Any = None,
                 track_recorder: Optional[TrackRecorder] = None, prewarm_async: bool = True,
                 dedupe_sec: float = DEDUPE_SEC, hello_stale_sec: float = HELLO_STALE_SEC,
                 poll_interval: float = POLL_INTERVAL_SEC, switch_fade_ms: int = SWITCH_FADE_MS,
                 fallback_window_sec: float = FALLBACK_WINDOW_SEC, card_only_sec: float = CARD_ONLY_SEC,
                 fire_wait_sec: float = FIRE_WAIT_SEC) -> None:
        if len(legacy) > 1:
            raise TypeError("Engine(providers, deck) takes at most one more positional argument "
                            "(the v1.1 form Engine(providers, registry, deck))")
        if legacy:  # v1.1 call: Engine(providers, registry, deck)
            registry, deck = deck, legacy[0]
        elif deck is not None and hasattr(deck, "resolve_event") and not hasattr(deck, "send"):
            registry, deck = deck, None  # Engine(providers, registry): no deck
        self.providers = list(providers)
        self.registry = registry
        self.deck = deck
        self.clock = clock
        self.fade_in_ms = int(fade_in_ms)
        self.fade_out_ms = int(fade_out_ms)
        self.dedupe_sec = dedupe_sec
        self.hello_stale_sec = hello_stale_sec
        self.poll_interval = poll_interval
        self.switch_fade_ms = int(switch_fade_ms)
        self.fallback_window_sec = fallback_window_sec
        self.card_only_sec = card_only_sec
        self.fire_wait_sec = max(0.0, min(float(fire_wait_sec), FIRE_WAIT_SEC))
        self.on_link_check = on_link_check
        self.on_status = on_status
        self.addons_dir = addons_dir
        self.track_recorder = track_recorder
        self.prewarm_async = bool(prewarm_async)
        self.log = _Log(log)

        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._idle_evt = threading.Event()
        self._idle_evt.set()
        self._worker = _Worker(self.log)

        self._seen: dict[tuple, float] = {}
        self._armed: dict[tuple[int, int], Drop] = {}  # v1: (hash, n) -> drop
        self._armed2: dict[int, _Armed] = {}  # v2: place key -> song
        self._resolutions: dict[str, _Resolution] = {}
        self._recorded: dict[str, dict] = {}  # what this session already recorded per track key
        self._flagged_yt: set[str] = set()
        self._current: Optional[_Active] = None
        self._stale_since: Optional[float] = None
        self._addon_seen_at: Optional[float] = None
        self._last_hello_at: Optional[float] = None
        self._addon_hash: Optional[int] = None
        self._addon_flags: Flags = Flags.NONE
        self._addon_protocol: Optional[int] = None
        self._warned_hashes: set[int] = set()
        self._warned_v1 = False
        self._last_event: Optional[str] = None
        self._notes: list[tuple[str, tuple]] = []  # callbacks to run after the lock is released
        self.counts: dict[str, int] = {t.name: 0 for t in EventType}
        self.transport_counts: dict[str, int] = {}
        self.duplicates = 0
        self.played = 0
        self.link_checks = 0
        self.recent: deque[str] = deque(maxlen=20)

    # ------------------------------------------------------------ public ----
    def submit(self, event: AnyEvent, transport_name: str) -> None:
        """Queue an event from any thread (this is the transports' ``on_event``)."""
        self._queue.put(("event", event, transport_name))

    def play_now(self, drop: Drop, *, test: bool = True, only: Optional[str] = None) -> None:
        """Queue a drop to play immediately, as if fired (``test-drop``, setup's Test buttons).

        ``only`` restricts playback to the provider with that name (the other providers are not
        tried); the title card is shown either way.
        """
        self._idle_evt.clear()
        self._queue.put(("play", drop, test, only))

    def stop_playback(self) -> None:
        """Queue a fade-out of whatever is playing."""
        self._queue.put(("stop",))

    def set_providers(self, providers: Sequence[Provider]) -> list[Provider]:
        """Replace the provider chain (after a settings change). Thread-safe.

        A drop that is playing keeps its provider instance until it ends. Returns the previous
        list so the caller can close instances that are no longer used.
        """
        with self._lock:
            old = self.providers
            self.providers = list(providers)
            self._resolutions.clear()  # e.g. Spotify was just connected: search again
            return old

    def set_registry(self, registry: Any) -> None:
        """Use another legacy v1 map registry (old addons only). Thread-safe."""
        with self._lock:
            self.registry = registry
            self._warned_hashes.clear()

    def set_addons_dir(self, addons_dir: Any) -> None:
        """Where search results and failures are recorded for the game (None: nowhere). Thread-safe."""
        with self._lock:
            self.addons_dir = addons_dir

    def current_provider(self) -> Optional[Provider]:
        """The provider instance playing the current drop, if any."""
        cur = self._current
        return cur.provider if cur is not None else None

    def start(self) -> "Engine":
        if self._thread and self._thread.is_alive():
            return self
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self.run, name="needledropper-engine", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the engine thread, the pre-warm worker and any playback (short fade)."""
        self._stop_evt.set()
        self._queue.put(("quit",))
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)
        old, self._worker = self._worker, _Worker(self.log)  # a restarted engine gets a fresh worker
        old.stop()
        if not (t and t.is_alive()):
            self._shutdown_playback()

    def run(self) -> None:
        """Engine loop: process queued items and timers until :meth:`stop`."""
        try:
            while not self._stop_evt.is_set():
                try:
                    item = self._queue.get(timeout=self._next_wait())
                except queue.Empty:
                    item = None
                if item is not None:
                    if item[0] == "quit":
                        break
                    self._dispatch(item)
                self.tick()
        finally:
            self._shutdown_playback()

    def drain(self) -> int:
        """Synchronously process everything queued, then timers (tests / no thread)."""
        n = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item[0] != "quit":
                self._dispatch(item)
                n += 1
        self.tick()
        return n

    def handle(self, event: AnyEvent, transport_name: str = "test") -> None:
        """Process one event synchronously on the calling thread."""
        self._dispatch(("event", event, transport_name))

    def wait_prewarm(self, timeout: float = 5.0) -> bool:
        """Block until the background pre-warm is idle (tests, tools); True when it is."""
        return self._worker.wait_idle(timeout)

    @property
    def is_playing(self) -> bool:
        return self._current is not None

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        """Block until nothing is playing (after a :meth:`play_now`)."""
        return self._idle_evt.wait(timeout)

    # ------------------------------------------------------------ timers ----
    def _next_wait(self) -> float:
        now = self.clock()
        due = [t for t in self._deadlines() if t is not None]
        if not due:
            return 0.5
        return max(0.02, min(0.5, min(due) - now))

    def _deadlines(self) -> list[Optional[float]]:
        cur = self._current
        out: list[Optional[float]] = []
        if cur is not None:
            out += [cur.stop_at, cur.next_poll_at]
            if self._stale_since is not None:
                out.append(self._stale_since + self.hello_stale_sec)
        return out

    def tick(self) -> None:
        """Run due timers: auto-stop, provider polling, stale-drop check, cache pruning."""
        with self._lock:
            self._tick_locked()
        self._run_notes()

    def _tick_locked(self) -> None:
        now = self.clock()
        if self._seen:
            cutoff = now - self.dedupe_sec
            for k in [k for k, t in self._seen.items() if t < cutoff]:
                del self._seen[k]
        self._prune(now)
        cur = self._current
        if cur is None:
            return
        if (self._stale_since is not None and cur.origin != "cli"
                and now - self._stale_since > self.hello_stale_sec):
            self.log.info("addon no longer reports %s active -- stopping", cur.drop.id)
            self._stop_current(self.fade_out_ms, "addon reports no active drop")
            return
        if cur.stop_at is not None and now >= cur.stop_at:
            self._stop_current(self.fade_out_ms if cur.handle else 0, "drop finished")
            return
        if cur.handle is not None and cur.provider is not None and cur.next_poll_at is not None \
                and now >= cur.next_poll_at:
            cur.next_poll_at = now + self.poll_interval
            try:
                state = cur.provider.poll(cur.handle)
            except Exception as exc:  # provider bug: treat as error
                self.log.warning("%s poll failed: %s", cur.provider.name, exc)
                state = "error"
            if state == "ended":
                self.log.info("%s finished playing %s", cur.provider.name, cur.drop.id)
                self._stop_current(0, "provider reports end")
            elif state == "error":
                self._on_provider_error(cur, now)

    def _prune(self, now: float) -> None:
        if self._armed2:
            for pk in [pk for pk, a in self._armed2.items() if now - a.at > ARM_TTL_SEC]:
                self._disarm(pk)
        if self._resolutions:
            for k in [k for k, r in self._resolutions.items()
                      if r.done.is_set() and (r.error or (r.not_found and now - (r.finished or now) > NOT_FOUND_TTL_SEC))]:
                del self._resolutions[k]
            if len(self._resolutions) > MAX_RESOLUTIONS:
                for k in sorted(self._resolutions, key=lambda k: self._resolutions[k].created)[:-MAX_RESOLUTIONS]:
                    if self._resolutions[k].done.is_set():
                        del self._resolutions[k]

    # ------------------------------------------------------------ events ----
    def _dispatch(self, item: tuple) -> None:
        kind = item[0]
        with self._lock:
            try:
                if kind == "event":
                    self._on_event(item[1], item[2])
                elif kind == "play":
                    drop, test = item[1], item[2]
                    only = item[3] if len(item) > 3 else None
                    self._start_drop(drop, origin="cli", local=False, test=test, map_hash=None, only=only)
                elif kind == "stop":
                    if self._current is not None:
                        self._stop_current(self.fade_out_ms, "stop requested")
                elif kind == "resolved":
                    self._on_resolved(item[1], item[2])
            except Exception as exc:  # never let one bad event kill the engine thread
                self.log.error("engine error while handling %s: %r", kind, exc)
                logging.getLogger("needledropper.engine").debug("traceback", exc_info=True)
            finally:
                if self._current is None:
                    self._idle_evt.set()
        self._run_notes()

    def _on_event(self, ev: Any, transport: str) -> None:
        if isinstance(ev, Frame):
            self._on_frame(ev, transport)
        elif isinstance(ev, Event):
            self._on_v1_event(ev, transport)
        else:
            self.log.debug("ignoring an event of type %s from %s", type(ev).__name__, transport)

    def _accept(self, ev: AnyEvent, key: tuple, transport: str, protocol: int) -> Optional[float]:
        """Common bookkeeping; the event's time, or None for a duplicate."""
        now = self.clock()
        first = self._seen.get(key)
        if first is not None and now - first < self.dedupe_sec:
            self.duplicates += 1
            self.log.debug("duplicate %s", describe_event(ev, transport))
            return None
        self._seen[key] = now
        self.counts[ev.type.name] = self.counts.get(ev.type.name, 0) + 1
        self.transport_counts[transport] = self.transport_counts.get(transport, 0) + 1
        back = self._addon_seen_at is None or now - self._addon_seen_at > ADDON_GONE_SEC
        changed = self._addon_protocol != protocol
        self._addon_seen_at = now
        self._addon_flags = ev.flags
        self._addon_protocol = protocol
        desc = describe_event(ev, transport)
        self._last_event = desc
        self.recent.append(desc)
        if ev.has(Flags.DROP_ACTIVE):
            self._stale_since = None
        if back or changed:
            self._note("addon", protocol=protocol, transport=transport)
        return now

    # -- v2 frames
    def _on_frame(self, fr: Frame, transport: str) -> None:
        if fr.type == EventType.NONE:
            return
        now = self._accept(fr, ("v2", fr.seq, int(fr.type), fr.place_key or 0, int(fr.flags)), transport, 2)
        if now is None:
            return
        self._addon_hash = None
        if fr.has(Flags.TEST) or fr.type == EventType.PING:
            self.link_checks += 1
            self._notes.append(("link", (fr, transport)))
        if fr.type == EventType.HELLO:
            self._on_hello(fr.flags, now, fr)
        elif fr.type == EventType.ARM:
            self._on_arm2(fr, transport, now)
        elif fr.type == EventType.FIRE:
            self._on_fire2(fr, transport, now)
        elif fr.type == EventType.CANCEL:
            self._on_cancel2(fr)
        elif fr.type == EventType.PING:
            self.log.warning("=== PING seq=%d via %s (flags=%s) -- the link to World of Warcraft works ===",
                             fr.seq, transport, _flag_names(fr.flags))

    def _label(self, fr: Frame) -> str:
        return fr.label or _zone_name(fr.drop_id or "") or ""

    def _drop_from_frame(self, fr: Frame) -> Drop:
        """A provider-ready Drop for a v2 frame (title/artist from the query, ids, timing, volume)."""
        did = fr.drop_id or (f"place:{fr.place_key}" if fr.place_key is not None else "")
        extra: dict[str, Any] = {"protocol": 2}
        if fr.place_key is not None:
            extra["placeKey"] = fr.place_key
        if fr.set_id:
            extra["setId"] = fr.set_id
        return Drop(n=0, id=did, kind=_kind_for(did), title=fr.title, artist=fr.artist, label=self._label(fr),
                    durationMs=fr.length_ms, startMs=fr.start_ms, volume=fr.volume, spotify=fr.spotify_uri,
                    youtube=fr.youtube_id, extra=extra)

    def _provider(self, name: str) -> Optional[Provider]:
        return next((p for p in self.providers if p.name == name), None)

    @staticmethod
    def _usable(p: Optional[Provider], drop: Drop) -> bool:
        if p is None:
            return False
        try:
            return bool(p.available() and p.can_play(drop))
        except Exception:
            return False

    def _on_arm2(self, fr: Frame, transport: str, now: float) -> None:
        pk = fr.place_key
        if pk is None:
            self.log.debug("ARM without a place key ignored")
            return
        entry = self._armed2.get(pk)
        if not _has_song(fr):  # the placeKey-only ARM shown after the first 3 s
            if entry is not None:
                entry.at = now
            else:
                self.log.debug("ARM place=%d (its song was not seen; the FIRE will carry it)", pk)
            return
        if entry is not None and (entry.job is None or not entry.job.cancelled) and \
                entry.frame == dataclasses.replace(fr, seq=entry.frame.seq, flags=entry.frame.flags):
            entry.at = now  # the same song again (e.g. re-armed after a /reload): already warm
            return
        if entry is not None and entry.job is not None:
            entry.job.cancelled = True
        armed = _Armed(frame=fr, at=now, transport=transport)
        self._armed2[pk] = armed
        if len(self._armed2) > MAX_ARMED:
            oldest = min(self._armed2, key=lambda k: self._armed2[k].at)
            self._disarm(oldest)
        drop = self._drop_from_frame(fr)
        self.log.info("armed %s (%s - %s) via %s", drop.display_label, drop.artist or "?", drop.title or "?",
                      transport)
        self._note("armed", dropId=drop.id, label=drop.display_label, title=drop.title, artist=drop.artist)
        if fr.has(Flags.LOCAL_PLAYBACK):
            return  # the game plays its own file: nothing to warm up here
        self._prewarm(armed, drop, fr.has(Flags.TEST))

    def _prewarm(self, armed: _Armed, drop: Drop, test: bool) -> None:
        """Start the ARM pre-warm for a v2 song: a Spotify search (no id) or a lookup of the pasted
        Spotify track, then the playing provider's ``prepare`` (Spotify device wake, or the
        YouTube player preload when Spotify will not play it)."""
        sp = self._provider("spotify")
        sp_ok = self._usable(sp, drop)
        res: Optional[_Resolution] = None
        search = False
        if sp_ok and not drop.spotify and drop.title and drop.artist and hasattr(sp, "resolve_drop"):
            key = query_key(drop.artist, drop.title)
            res = self._resolutions.get(key)
            if res is None or (res.done.is_set() and res.error):
                res = self._resolutions[key] = _Resolution(key=key, created=self.clock())
                search = True
        sp_route = sp_ok and bool(drop.spotify or (drop.title and drop.artist))
        lookup_link = bool(sp_route and drop.spotify and hasattr(sp, "resolve_drop") and not test
                           and f"sp:{drop.spotify.rsplit(':', 1)[-1]}" not in self._recorded)
        yt = self._provider("youtube")
        others = [p for p in self.providers if p.name not in ("spotify", "youtube")]
        if self.prewarm_async:  # results are handled on the engine thread
            def deliver(r: _Resolution) -> None:
                self._queue.put(("resolved", r, test))
        else:  # inline: we are on the engine thread already
            def deliver(r: _Resolution) -> None:
                self._on_resolved(r, test)

        def work() -> None:
            resolved = drop
            if search and res is not None:
                resolved = self._search(sp, drop, res)
                deliver(res)
            elif res is not None:
                res.done.wait(FIRE_WAIT_SEC)  # another ARM is already searching this song
                if res.uri:
                    resolved = dataclasses.replace(drop, spotify=res.uri)
            elif lookup_link:
                resolved = self._lookup_link(sp, drop, deliver)
            if sp_route and (resolved.spotify or res is None or not res.not_found):
                self._safe_prepare(sp, resolved)
                return
            target = yt if self._usable(yt, drop) else next((p for p in others if self._usable(p, drop)), None)
            if target is not None:
                self._safe_prepare(target, drop)

        job = _Job(work, armed.frame.place_key, on_cancel=(lambda: _abandon(res)) if search else None)
        armed.job = job
        if self.prewarm_async:
            self._worker.submit(job)
            return
        try:
            work()
        finally:
            job.done.set()

    def prewarm_songs(self, songs: Sequence[tuple[str, str]], *, limit: int = 25) -> int:
        """Queue Spotify searches for ``(artist, title)`` pairs ahead of time -- the search-only
        drops of the player's own soundtracks, read from the game's SavedVariables
        (``savedvars.search_only_songs``; DESIGN-v1.2 2.1). Songs already searched this session
        are skipped; at most ``limit`` are queued. Results are recorded for the game like ARM
        searches. Returns how many were queued. Thread-safe."""
        queued = 0
        with self._lock:
            sp = self._provider("spotify")
            if sp is None or not hasattr(sp, "resolve_drop"):
                return 0
            for artist, title in songs:
                if queued >= limit:
                    break
                if not (isinstance(artist, str) and artist.strip() and isinstance(title, str) and title.strip()):
                    continue
                drop = Drop(n=0, id="", kind="zone", title=title.strip(), artist=artist.strip())
                if not self._usable(sp, drop):
                    break  # Spotify is not connected: nothing to search with
                key = query_key(drop.artist, drop.title)
                if key in self._resolutions or key in self._recorded:
                    continue
                res = self._resolutions[key] = _Resolution(key=key, created=self.clock())
                queued += 1
                if self.prewarm_async:
                    def work(drop: Drop = drop, res: _Resolution = res) -> None:
                        self._search(sp, drop, res)
                        self._queue.put(("resolved", res, False))
                    self._worker.submit(_Job(work, None, on_cancel=lambda res=res: _abandon(res)))
                else:
                    self._search(sp, drop, res)
                    self._on_resolved(res, False)
        self._run_notes()
        return queued

    def _search(self, sp: Provider, drop: Drop, res: _Resolution) -> Drop:
        """Spotify search for a drop without an id (worker or engine thread); fills ``res``."""
        try:
            resolved = sp.resolve_drop(drop)  # type: ignore[attr-defined]
            uri = resolved.spotify if isinstance(resolved, Drop) else None
            if uri:
                res.uri = uri
                extra = resolved.extra if isinstance(resolved.extra, dict) else {}
                res.title = str(extra.get("matchedTitle") or "")
                res.artist = str(extra.get("matchedArtist") or "")
                try:
                    res.duration_ms = int(extra.get("trackDurationMs") or 0)
                except (TypeError, ValueError):
                    res.duration_ms = 0
                return resolved
            res.not_found = True
            return drop
        except ProviderUnavailable as exc:
            res.error = f"unavailable: {exc}"
        except ProviderError as exc:
            res.error = str(exc)
        except Exception as exc:  # provider bug
            res.error = repr(exc)
        finally:
            res.finished = self.clock()
            res.done.set()
        return drop

    def _lookup_link(self, sp: Provider, drop: Drop, deliver: Callable[[_Resolution], None]) -> Drop:
        """The track behind a pasted Spotify link (title, artist, length) for the game; best effort.
        A link Spotify does not know (HTTP 400/404) is recorded as ``notFound``."""
        sid = (drop.spotify or "").rsplit(":", 1)[-1]
        try:
            resolved = sp.resolve_drop(drop)  # type: ignore[attr-defined]
        except ProviderError as exc:
            if getattr(exc, "status", None) in (400, 404):
                gone = _Resolution(key=f"sp:{sid}", created=self.clock(), not_found=True, finished=self.clock())
                gone.done.set()
                deliver(gone)
            self.log.debug("Spotify lookup of %s failed: %s", sid, exc)
            return drop
        except Exception as exc:  # provider bug
            self.log.debug("Spotify lookup of %s failed: %r", sid, exc)
            return drop
        if not isinstance(resolved, Drop):
            return drop
        extra = resolved.extra if isinstance(resolved.extra, dict) else {}
        info = _Resolution(key=f"sp:{sid}", created=self.clock(), uri=resolved.spotify,
                           title=str(extra.get("matchedTitle") or ""), artist=str(extra.get("matchedArtist") or ""),
                           finished=self.clock())
        try:
            info.duration_ms = int(extra.get("trackDurationMs") or 0)
        except (TypeError, ValueError):
            info.duration_ms = 0
        info.done.set()
        if info.title or info.duration_ms:
            deliver(info)
        return resolved

    def _safe_prepare(self, p: Optional[Provider], drop: Drop) -> None:
        if p is None:
            return
        try:
            p.prepare(drop)
            self.log.debug("%s prepared %s", p.name, drop.id)
        except Exception as exc:  # prepare must never raise; be defensive anyway
            self.log.debug("%s prepare failed: %s", p.name, exc)

    def _on_resolved(self, res: _Resolution, test: bool) -> None:
        """A search / link lookup finished: record it for the game and tell the shell (engine thread)."""
        if res.recorded or test:
            return
        if res.key.startswith("q:"):
            if res.uri:
                res.recorded = True
                sid = res.uri.rsplit(":", 1)[-1]
                self._record(res.key, {"spotify": sid, "durationMs": res.duration_ms or None,
                                       "title": res.title or None, "artist": res.artist or None, "notFound": False})
                self.log.info("Spotify match for %s: %s - %s", res.key[2:].replace("\x1f", " / "),
                              res.artist or "?", res.title or "?")
                self._note("match", key=res.key, spotify=sid, title=res.title, artist=res.artist,
                           durationMs=res.duration_ms)
            elif res.not_found:
                res.recorded = True
                self._record(res.key, {"notFound": True})
                self.log.warning("no confident Spotify match for %s", res.key[2:].replace("\x1f", " / "))
                self._note("notFound", key=res.key)
            elif res.error and res.error != "cancelled":
                res.recorded = True  # nothing to record: the search is tried again next time
                self.log.warning("Spotify search for %s failed: %s", res.key[2:].replace("\x1f", " / "), res.error)
                self._note("searchFailed", key=res.key, detail=res.error,
                           unavailable=res.error.startswith("unavailable:"))
        elif res.key.startswith("sp:"):
            res.recorded = True
            if res.not_found:
                self._record(res.key, {"notFound": True})
                self._note("notFound", key=res.key)
            else:
                self._record(res.key, {"durationMs": res.duration_ms or None, "title": res.title or None,
                                       "artist": res.artist or None, "notFound": False})

    def _on_fire2(self, fr: Frame, transport: str, now: float) -> None:
        test = fr.has(Flags.TEST)
        pk = fr.place_key
        armed = self._armed2.pop(pk, None) if pk is not None else None
        song = _merge_frames(fr, armed.frame if armed is not None else None)
        local = fr.has(Flags.LOCAL_PLAYBACK)
        if not local and not _has_song(song):
            if song.drop_id == "test" or test:
                self.log.info("test signal from World of Warcraft via %s (seq %d) -- nothing to play", transport,
                              fr.seq)
            else:
                self.log.warning("FIRE %s via %s carries no song (no Spotify or YouTube id, no artist/title)",
                                 song.drop_id or f"place={pk}", transport)
            return
        drop = self._drop_from_frame(song)
        if local:
            self._start_drop(drop, origin=transport, local=True, test=test, map_hash=None, version=2, place_key=pk,
                             frame_drop_id=song.drop_id)
            return
        job = armed.job if armed is not None else None
        routes, qkey = self._routes_v2(drop, pk, job)
        self._start_drop(drop, origin=transport, local=False, test=test, map_hash=None, version=2, place_key=pk,
                         routes=routes, qkey=qkey, frame_drop_id=song.drop_id)

    def _routes_v2(self, drop: Drop, place_key: Optional[int],
                   job: Optional[_Job]) -> tuple[list[_Route], Optional[str]]:
        """Spotify id -> Spotify search on the query -> YouTube id -> any other provider.

        If the pre-warm of this place (or any pre-warm, when Spotify may play) is still running,
        wait for it first -- at most ``fire_wait_sec`` -- so the search result is used and
        ``play()`` never overlaps a ``prepare()``; queued pre-warms of other places are dropped.
        """
        sp = self._provider("spotify")
        yt = self._provider("youtube")
        qkey = query_key(drop.artist, drop.title) if (drop.title and drop.artist) else None
        routes: list[_Route] = []
        late: list[_Route] = []
        sp_ok = self._usable(sp, drop)
        own_pending = job is not None and not job.done.is_set() and not job.cancelled
        started = time.monotonic()
        deadline = started + self.fire_wait_sec  # one budget for every wait below

        def left() -> float:
            return max(0.0, deadline - time.monotonic())

        if self.prewarm_async and self._worker.busy:
            if place_key is not None:  # the player is here now, not there
                dropped = self._worker.cancel_except(place_key)
                if dropped:
                    self.log.debug("dropped %d queued pre-warm(s) of other places", dropped)
            if (own_pending or sp_ok) and not self._worker.wait_idle(left()):
                self.log.info("pre-warm still running after %.1f s; playing without waiting longer",
                              time.monotonic() - started)
        if sp is not None and sp_ok:
            if drop.spotify:
                routes.append(_Route(sp, drop))
            elif qkey is not None:
                res = self._resolutions.get(qkey)
                if res is not None and not res.done.is_set():
                    res.done.wait(left())
                if res is not None and res.done.is_set():
                    if not res.recorded and (res.uri or res.not_found):
                        self._on_resolved(res, False)
                    if res.uri:
                        routes.append(_Route(sp, dataclasses.replace(drop, spotify=res.uri)))
                    elif not res.not_found:
                        routes.append(_Route(sp, drop, search=True))  # the earlier search failed: try again
                elif res is not None:  # still searching after the wait: YouTube first, Spotify after it
                    late.append(_Route(sp, drop, search=False))
                else:
                    routes.append(_Route(sp, drop, search=True))
        if yt is not None and drop.youtube:
            routes.append(_Route(yt, drop))
        routes += late
        for p in self.providers:
            if p.name not in ("spotify", "youtube"):
                routes.append(_Route(p, drop))
        return routes, qkey

    def _on_cancel2(self, fr: Frame) -> None:
        pk = fr.place_key
        removed = self._disarm(pk) if pk is not None else None
        cur = self._current
        if cur is not None and cur.version == 2 and pk is not None and cur.place_key == pk and not (
                fr.drop_id and cur.frame_drop_id and fr.drop_id != cur.frame_drop_id):
            self._stop_current(self.fade_out_ms, "cancelled by addon")
        elif removed is not None:
            self.log.info("ARM %s abandoned", self._label(removed.frame) or f"place={pk}")
        else:
            self.log.debug("CANCEL place=%s: nothing armed or playing there", pk)

    def _disarm(self, place_key: int) -> Optional[_Armed]:
        a = self._armed2.pop(place_key, None)
        if a is not None and a.job is not None:
            a.job.cancelled = True
        return a

    # -- v1 events (legacy addons)
    def _on_v1_event(self, ev: Event, transport: str) -> None:
        if ev.type == EventType.NONE:
            return
        now = self._accept(ev, ("v1",) + ev.key, transport, 1)
        if now is None:
            return
        self._addon_hash = ev.hash
        if ev.has(Flags.TEST) or ev.type == EventType.PING:
            self.link_checks += 1
            self._notes.append(("link", (ev, transport)))
        if ev.type == EventType.HELLO:
            self._on_hello(ev.flags, now, ev)
            return
        if ev.type == EventType.PING:
            self.log.warning("=== PING seq=%d via %s (hash=%d, flags=%s) -- transport works ===",
                             ev.seq, transport, ev.hash, _flag_names(ev.flags))
            return
        if self.registry is None:
            self._v1_unsupported(ev, transport)
            return
        self._check_hash(ev)
        if ev.type == EventType.ARM:
            self._on_arm(ev, transport)
        elif ev.type == EventType.FIRE:
            self._on_fire(ev, transport)
        elif ev.type == EventType.CANCEL:
            self._on_cancel(ev)

    def _v1_unsupported(self, ev: Event, transport: str) -> None:
        if not self._warned_v1:
            self._warned_v1 = True
            self.log.warning("World of Warcraft runs an old NeedleDropper addon (it sent a v1 %s via %s): this app "
                             "plays drops sent by NeedleDropper 1.2 or newer. Update the NeedleDropper addon.",
                             ev.type.name, transport)
            self._note("outdatedAddon", protocol=1)
        else:
            self.log.debug("v1 %s n=%d ignored (old addon)", ev.type.name, ev.n)

    def _check_hash(self, ev: Event) -> None:
        inst = getattr(self.registry, "installed_hash", None)
        if inst is None or ev.hash == inst or ev.hash in self._warned_hashes:
            return
        self._warned_hashes.add(ev.hash)
        self.log.warning("addon is running map hash %d but the installed Map.json has hash %d -- "
                         "type /reload in game to load the new map", ev.hash, inst)

    def _on_hello(self, flags: Flags, now: float, ev: AnyEvent) -> None:
        self._last_hello_at = now
        self.log.debug("addon alive (%s)", describe_event(ev))
        cur = self._current
        if cur is not None and cur.origin != "cli" and not (flags & Flags.DROP_ACTIVE):
            if self._stale_since is None:
                self._stale_since = now

    def _on_arm(self, ev: Event, transport: str) -> None:
        drop = self.registry.resolve_event(ev)
        if drop is None:
            self.log.warning("ARM n=%d: %s", ev.n, self.registry.describe_miss(ev))
            return
        self._armed[(ev.hash, ev.n)] = drop
        self.log.info("armed %s (%s - %s) via %s", drop.id, drop.artist, drop.title, transport)
        if ev.has(Flags.LOCAL_PLAYBACK):
            return
        for p in self.providers:
            try:
                if not p.available() or not p.can_play(drop):
                    continue
            except Exception as exc:
                self.log.debug("%s availability check failed: %s", p.name, exc)
                continue
            self._safe_prepare(p, drop)
            break

    def _on_fire(self, ev: Event, transport: str) -> None:
        drop = self.registry.resolve_event(ev)
        self._armed.pop((ev.hash, ev.n), None)
        if drop is None:
            self.log.warning("FIRE n=%d: %s", ev.n, self.registry.describe_miss(ev))
            return
        self._start_drop(drop, origin=transport, local=ev.has(Flags.LOCAL_PLAYBACK),
                         test=ev.has(Flags.TEST), map_hash=ev.hash)

    def _on_cancel(self, ev: Event) -> None:
        if self._armed.pop((ev.hash, ev.n), None) is not None and (
                self._current is None or self._current.drop.n != ev.n):
            self.log.info("ARM n=%d abandoned", ev.n)
            return
        cur = self._current
        if cur is None:
            self.log.debug("CANCEL n=%d: nothing playing", ev.n)
            return
        same_hash = cur.map_hash is None or cur.map_hash == ev.hash
        if cur.version == 1 and cur.drop.n == ev.n and (same_hash or (ev.id and ev.id == cur.drop.id)):
            self._stop_current(self.fade_out_ms, "cancelled by addon")
        else:
            self.log.debug("CANCEL n=%d ignored (playing %s)", ev.n, cur.drop.id)

    # ---------------------------------------------------------- playback ----
    def _start_drop(self, drop: Drop, *, origin: str, local: bool, test: bool, map_hash: Optional[int],
                    only: Optional[str] = None, version: int = 1, place_key: Optional[int] = None,
                    routes: Optional[list[_Route]] = None, qkey: Optional[str] = None,
                    frame_drop_id: Optional[str] = None) -> None:
        now = self.clock()
        if self._current is not None:
            self.log.info("new drop %s replaces %s", drop.id, self._current.drop.id)
            self._stop_current(self.switch_fade_ms, "replaced", hide=False)
        self._stale_since = None
        self._idle_evt.clear()
        cur = _Active(drop=drop, started_at=now, origin=origin, map_hash=map_hash, local=local, test=test,
                      version=version, place_key=place_key, query_key=qkey, frame_drop_id=frame_drop_id)
        if routes is None:
            routes = [_Route(p, drop) for p in self.providers]
        if only:
            # Restrict to one provider: the others count as already tried.
            cur.tried = [p.name for p in self.providers if p.name != only]
            routes = [r for r in routes if r.provider.name == only]
        cur.routes = routes
        self._current = cur
        self.played += 1
        tag = " [test]" if test else ""
        if local:
            self.log.info("FIRE %s%s: %s - %s (playing in game; card only)", drop.id, tag, drop.artist, drop.title)
            self._send_card(cur, None, hint="Playing in game")
            # The addon ends it (HELLO without DROP_ACTIVE / CANCEL); cap in case we miss that.
            secs = drop.durationMs / 1000.0 if drop.durationMs else LOCAL_CARD_CAP_SEC
            cur.stop_at = cur.end_at = now + secs
            self._note("local", dropId=drop.id, label=drop.display_label, title=drop.title, artist=drop.artist,
                       test=test)
            return
        self.log.info("FIRE %s%s: %s - %s", drop.id, tag, drop.artist, drop.title)
        self._send_card(cur, None)
        if not self._play_chain(cur, now):
            self._card_only(cur, now)

    def _play_chain(self, cur: _Active, now: float) -> bool:
        """Try the remaining routes in order (providers already tried for this drop are skipped)."""
        reasons: list[str] = []
        while cur.next_route < len(cur.routes):
            route = cur.routes[cur.next_route]
            cur.next_route += 1
            p, drop = route.provider, route.drop
            if p.name in cur.tried:
                continue
            try:
                if not p.available():
                    reasons.append(f"{p.name}: not available")
                    continue
                if not p.can_play(drop):
                    reasons.append(f"{p.name}: nothing to play")
                    continue
            except Exception as exc:
                reasons.append(f"{p.name}: {exc}")
                continue
            if route.search:
                drop = self._resolve_now(cur, route)
                if drop is None:
                    reasons.append(f"{p.name}: no match for {cur.drop.artist} - {cur.drop.title}")
                    continue
            cur.tried.append(p.name)
            try:
                handle = p.play(drop, fade_in_ms=self.fade_in_ms)
            except ProviderUnavailable as exc:
                self.log.warning("%s unavailable: %s", p.name, exc)
                reasons.append(f"{p.name}: {exc}")
                continue
            except ProviderError as exc:
                self.log.warning("%s could not play %s: %s", p.name, drop.id, exc)
                reasons.append(f"{p.name}: {exc}")
                self._note_failure(cur, p, str(exc))
                continue
            except Exception as exc:
                self.log.error("%s crashed playing %s: %r", p.name, drop.id, exc)
                reasons.append(f"{p.name}: {exc!r}")
                continue
            cur.handle, cur.provider, cur.route = handle, p, route
            cur.play_started_at = now
            cur.next_poll_at = now + self.poll_interval
            if drop.durationMs:
                cur.end_at = now + drop.durationMs / 1000.0
                fade_s = self.fade_out_ms / 1000.0
                cur.stop_at = max(now + drop.durationMs / 2000.0, cur.end_at - fade_s)
            else:
                cur.end_at = cur.stop_at = None  # rely on provider.poll() / the addon's HELLO
            self.log.info("playing %s on %s", drop.id, p.name)
            self._send_card(cur, handle.now_playing, provider=p.name)
            self._note_success(cur, p, handle)
            return True
        detail = "; ".join(reasons) if reasons else "no providers configured"
        self.log.warning("no provider could play %s (%s)", cur.drop.id, detail)
        return False

    def _resolve_now(self, cur: _Active, route: _Route) -> Optional[Drop]:
        """Spotify search at FIRE time (no ARM saw this song): the drop with the match, or None."""
        p, drop = route.provider, route.drop
        if not hasattr(p, "resolve_drop") or not (drop.title and drop.artist):
            return drop  # the provider searches inside play()
        key = cur.query_key or query_key(drop.artist, drop.title)
        res = self._resolutions.get(key)
        if res is not None and not res.done.is_set():
            return drop  # searching elsewhere right now: the provider resolves it inside play()
        if res is None or res.error:
            res = self._resolutions[key] = _Resolution(key=key, created=self.clock())
            resolved = self._search(p, drop, res)
        else:
            resolved = dataclasses.replace(drop, spotify=res.uri) if res.uri else drop
        if not res.recorded:
            self._on_resolved(res, cur.test)
        if res.not_found:
            return None
        return resolved

    def _note_success(self, cur: _Active, p: Provider, handle: PlayHandle) -> None:
        self._note("playing", dropId=cur.drop.id, label=cur.drop.display_label, title=cur.drop.title,
                   artist=cur.drop.artist, provider=p.name, test=cur.test)
        if cur.test or cur.version != 2:
            return
        if p.name == "youtube" and cur.drop.youtube and cur.drop.youtube in self._flagged_yt:
            self._flagged_yt.discard(cur.drop.youtube)
            self._record(f"yt:{cur.drop.youtube}", {"notEmbeddable": False})
            if cur.query_key:
                self._record(cur.query_key, {"notEmbeddable": False})

    def _note_failure(self, cur: _Active, p: Provider, detail: str, code: Optional[int] = None) -> None:
        if p.name != "youtube" or not cur.drop.youtube:
            return
        if code is None:
            m = _YT_CODE_RE.search(detail or "")
            code = int(m.group(1)) if m else None
        if code not in YT_UNEMBEDDABLE:
            return
        self.log.warning("YouTube video %s cannot be played here (error %d)", cur.drop.youtube, code)
        self._note("notEmbeddable", youtube=cur.drop.youtube, code=code, dropId=cur.drop.id,
                   label=cur.drop.display_label, test=cur.test)
        if cur.test or cur.version != 2:
            return
        self._flagged_yt.add(cur.drop.youtube)
        self._record(f"yt:{cur.drop.youtube}", {"notEmbeddable": True})
        if cur.query_key:
            self._record(cur.query_key, {"notEmbeddable": True})

    def _card_only(self, cur: _Active, now: float) -> None:
        cur.handle = cur.provider = None
        cur.next_poll_at = None
        cur.stop_at = cur.end_at = now + self.card_only_sec
        self._send_card(cur, None, hint="No playable source for this drop")
        self._note("noSource", dropId=cur.drop.id, label=cur.drop.display_label, title=cur.drop.title,
                   artist=cur.drop.artist, test=cur.test)

    def _on_provider_error(self, cur: _Active, now: float) -> None:
        p = cur.provider
        name = p.name if p else "?"
        self.log.warning("%s reported an error playing %s", name, cur.drop.id)
        if p is not None and p.name == "youtube" and cur.handle is not None:
            st = None
            status_for = getattr(self.deck, "status_for", None)
            if callable(status_for):
                try:
                    st = status_for(cur.handle.data.get("playId", ""))
                except Exception:
                    st = None
            code = st.get("code") if isinstance(st, dict) else None
            if isinstance(code, int):
                self._note_failure(cur, p, "", code)
        self._safe_stop(cur, 0)
        cur.handle = cur.provider = None
        started = cur.play_started_at if cur.play_started_at is not None else cur.started_at
        if now - started <= self.fallback_window_sec and self._play_chain(cur, now):
            return
        self._card_only(cur, now)

    def _safe_stop(self, cur: _Active, fade_ms: int) -> None:
        if cur.handle is None or cur.provider is None:
            return
        try:
            cur.provider.stop(cur.handle, fade_out_ms=int(fade_ms))
        except Exception as exc:  # stop must not raise; be defensive anyway
            self.log.warning("%s stop failed: %s", cur.provider.name, exc)

    def _stop_current(self, fade_ms: int, reason: str, *, hide: bool = True) -> None:
        cur = self._current
        if cur is None:
            return
        self._current = None
        self._stale_since = None
        self.log.info("stop %s (%s)", cur.drop.id, reason)
        self._safe_stop(cur, fade_ms)
        if hide:
            self._deck_send({"cmd": "hide", "dropId": cur.drop.id})
        self._note("stopped", dropId=cur.drop.id, reason=reason)
        self._idle_evt.set()

    def _shutdown_playback(self) -> None:
        with self._lock:
            if self._current is not None:
                self._stop_current(min(self.fade_out_ms, SWITCH_FADE_MS), "companion shutting down")
            self._idle_evt.set()
        self._run_notes()

    # ----------------------------------------------------------- records ----
    def _record(self, key: str, info: dict) -> None:
        """Record a fact for the game (``NeedleDropper_Companion`` tracks); never raises."""
        clean = {k: v for k, v in info.items()}
        if self._recorded.get(key) == clean:
            return
        recorder = self.track_recorder
        if recorder is None and self.addons_dir is not None:
            from . import gamedata

            addons_dir = self.addons_dir

            def recorder(k: str, i: dict) -> None:
                gamedata.record_track(addons_dir, k, i)
        if recorder is None:
            self.log.debug("not recording %s (no AddOns folder configured)", key)
            return
        try:
            recorder(key, clean)
            self._recorded[key] = clean
        except Exception as exc:  # recording is a convenience; playback must go on
            self.log.warning("could not record %s for the game: %s", key, exc)

    # ------------------------------------------------------------- notes ----
    def _note(self, kind: str, **info: Any) -> None:
        if self.on_status is not None:
            self._notes.append(("status", ({"kind": kind, **info},)))

    def _run_notes(self) -> None:
        """Run queued callbacks (outside the engine lock, in order)."""
        while True:
            with self._lock:
                if not self._notes:
                    return
                notes, self._notes = self._notes, []
            for what, args in notes:
                try:
                    if what == "link" and self.on_link_check is not None:
                        self.on_link_check(*args)
                    elif what == "status" and self.on_status is not None:
                        st = self.status()
                        st["change"] = args[0]
                        self.on_status(st)
                except Exception as exc:  # a shell bug must not stop playback
                    self.log.warning("%s callback failed: %r", what, exc)

    # -------------------------------------------------------------- deck ----
    def _deck_send(self, cmd: dict) -> None:
        if self.deck is None:
            return
        try:
            self.deck.send(cmd)
        except Exception as exc:
            self.log.debug("deck send failed: %s", exc)

    def _send_card(self, cur: _Active, np: Optional[NowPlaying], *, hint: Optional[str] = None,
                   provider: Optional[str] = None) -> None:
        drop = cur.drop
        card = {
            "cmd": "show",
            "dropId": drop.id,
            "label": (np.label if np and np.label else "") or drop.display_label,
            "title": (np.title if np and np.title else "") or drop.title,
            "artist": (np.artist if np and np.artist else "") or drop.artist,
            "artUrl": (np.art_url if np else None) or drop.extra.get("artUrl"),
            "attribution": np.attribution if np else None,
            "link": np.link if np else None,
            "hint": hint or (np.hint if np else None),
            "provider": provider or (np.provider if np else None),
            "test": (cur.test or drop.kind == "test") if cur.version == 2 else drop.kind == "test",
        }
        self._deck_send(card)

    # ------------------------------------------------------------ status ----
    def status(self) -> dict[str, Any]:
        """Snapshot for the tray / setup page / diagnostics (safe to call from any thread)."""
        now = self.clock()
        cur = self._current

        def ago(t: Optional[float]) -> Optional[float]:
            return None if t is None else round(now - t, 1)

        playing = None
        if cur is not None:
            playing = {
                "id": cur.drop.id, "label": cur.drop.display_label, "title": cur.drop.title,
                "artist": cur.drop.artist,
                "provider": cur.provider.name if cur.provider else ("game" if cur.local else None),
                "elapsedSec": round(now - cur.started_at, 1),
                "remainingSec": None if cur.end_at is None else max(0.0, round(cur.end_at - now, 1)),
                "local": cur.local, "test": cur.test, "origin": cur.origin,
            }
        armed = [d.id for d in list(self._armed.values())]
        for pk, a in list(self._armed2.items()):
            armed.append(a.frame.drop_id or f"place:{pk}")
        res = list(self._resolutions.values())
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "addon": {
                "seen": self._addon_seen_at is not None,
                "lastEventAgoSec": ago(self._addon_seen_at),
                "lastHelloAgoSec": ago(self._last_hello_at),
                "hash": self._addon_hash,
                "flags": _flag_names(self._addon_flags),
                "protocol": self._addon_protocol,
                "outdated": self._addon_protocol == 1 and self.registry is None,
            },
            "playing": playing,
            "armed": sorted(armed),
            "events": dict(self.counts),
            "transports": dict(self.transport_counts),
            "duplicates": self.duplicates,
            "played": self.played,
            "linkChecks": self.link_checks,
            "spotifySearches": {
                "matched": sum(1 for r in res if r.uri),
                "notFound": sum(1 for r in res if r.not_found),
                "pending": sum(1 for r in res if not r.done.is_set()),
            },
            "lastEvent": self._last_event,
            "providers": [p.name for p in self.providers],
        }
