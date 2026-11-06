"""Beacon v2 hold and queue rules (DESIGN-v1.2 2.3 "Timing and acceptance").

* idle: HELLO (96 x 3 px at 3 px cells);
* ARM: the full song for 3 s of drawn time, then an ARM carrying only the placeKey;
* FIRE: the full song for at least 3 s, then HELLO + DROP_ACTIVE;
* CANCEL / PING: at least 1 s each;
* FIRE / CANCEL / PING queue first in first out and are never lost; HELLO / ARM only set what
  shows once the queue is empty. Synthesized frames reuse their event's seq and never reach
  ns.Events or the chat log.

Also: at 4 Hz every event is captured at least twice in a row (the companion's acceptance rule).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v12_beacon_harness import BeaconHarness  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

DROP_A = {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "title": "I'll Keep Coming",
          "artist": "Low Roar", "durationMs": 60000, "youtube": "mlM66FzjpgM", "setId": "azeroth-mixtape"}
DROP_B = {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "title": "Old Pine",
          "artist": "Ben Howard", "durationMs": 30000, "spotify": "4uLU6hMCjMI75M1A2tKUQC"}


@pytest.fixture
def h() -> BeaconHarness:
    return BeaconHarness(settings={"beaconCellPx": 3, "chatlog": True})


def shown(h: BeaconHarness) -> p2.Frame:
    f = h.beacon_decoded()
    assert f is not None, h.status()
    return f


def full(ev: dict) -> p2.Frame:
    return p2.frame_from_event(ev, max_bytes=p2.capacity(3))


def test_arm_shows_the_full_song_for_3_seconds_then_only_the_place_key(h):
    ev = h.emit(ET.ARM, DROP_A)
    assert shown(h) == full(ev) and shown(h).query is not None
    h.advance(2.9)
    assert shown(h) == full(ev)
    h.advance(0.2)
    short = shown(h)
    assert (short.type, short.seq, int(short.flags), short.place_key) == (ET.ARM, ev["seq"], ev["flags"],
                                                                          p2.place_key_for(DROP_A["id"]))
    assert short.drop_id is None and short.query is None and short.youtube_id is None
    assert short.key == full(ev).key  # the companion sees a repeat, not a new ARM
    assert h.status()["size"] == "144x3"
    h.advance(60)
    assert shown(h) == short  # while armed
    assert [e["type"] for e in h.events()] == [ET.ARM]  # synthesized frames are never emitted
    assert len([x for x in h.chat_lines() if x.startswith("NDROP2")]) == 1


def test_fire_holds_at_least_3_seconds_then_hello_with_drop_active(h):
    ev = h.emit(ET.FIRE, DROP_A, int(Flags.TEST | Flags.LOCAL_PLAYBACK))
    assert shown(h) == full(ev)
    h.advance(2.95)
    assert shown(h) == full(ev)
    h.advance(0.1)
    after = shown(h)
    assert (after.type, after.seq) == (ET.HELLO, ev["seq"])
    assert after.has(Flags.DROP_ACTIVE) and after.has(Flags.LOCAL_PLAYBACK) and not after.has(Flags.TEST)
    assert h.status()["size"] == "96x3"


def test_cancel_and_ping_hold_one_second(h):
    ev = h.emit(ET.CANCEL, DROP_A, int(Flags.TEST))
    assert shown(h) == full(ev)
    h.advance(0.95)
    assert shown(h) == full(ev)
    h.advance(0.1)
    idle = shown(h)
    assert (idle.type, idle.seq, int(idle.flags)) == (ET.HELLO, ev["seq"], 0)  # TEST cleared, nothing playing
    ping = h.emit(ET.PING, None, int(Flags.TEST))
    assert shown(h) == full(ping) and shown(h).has(Flags.TEST)
    h.advance(0.95)
    assert shown(h).type == ET.PING
    h.advance(0.1)
    assert shown(h) == idle  # back to the steady state


def test_cancel_while_another_drop_plays_keeps_drop_active(h):
    """Disarming candidate B while drop A plays: the HELLO after the CANCEL keeps DROP_ACTIVE,
    or the companion would stop A five seconds later."""
    h.set_director_active({"isLocal": False, "isTest": False})
    ev = h.emit(ET.CANCEL, DROP_B)
    assert ev["flags"] & Flags.DROP_ACTIVE
    h.advance(1.1)
    after = shown(h)
    assert after.type == ET.HELLO and after.has(Flags.DROP_ACTIVE)


def test_newer_events_queue_behind_a_fire_and_none_is_lost(h):
    fire = h.emit(ET.FIRE, DROP_A)
    h.advance(0.5)
    cancel = h.emit(ET.CANCEL, DROP_A)    # /needle stop half a second in
    arm = h.emit(ET.ARM, DROP_B)          # ...and the next candidate
    assert shown(h) == full(fire)
    h.advance(2.4)
    assert shown(h) == full(fire)         # FIRE keeps its 3 s
    h.advance(0.2)
    assert shown(h) == full(cancel)       # then CANCEL for 1 s
    h.advance(0.9)
    assert shown(h) == full(cancel)
    h.advance(0.2)
    assert shown(h) == full(arm)          # then the newest steady state: the ARM's full song
    h.advance(3.0)
    assert shown(h).type == ET.ARM and shown(h).query is None


def test_fire_after_fire_both_get_three_seconds(h):
    a = h.emit(ET.FIRE, DROP_A)
    h.advance(1.0)
    b = h.emit(ET.FIRE, DROP_B)
    h.advance(1.9)
    assert shown(h) == full(a)
    h.advance(0.2)
    assert shown(h) == full(b)
    h.advance(2.9)
    assert shown(h) == full(b)
    h.advance(0.2)
    assert (shown(h).type, shown(h).seq) == (ET.HELLO, b["seq"]) and shown(h).has(Flags.DROP_ACTIVE)


def test_hello_after_a_short_drop_waits_for_the_fire_hold(h):
    fire = h.emit(ET.FIRE, DROP_A)
    h.advance(1.0)
    hello = h.emit(ET.HELLO)  # the drop ended already
    assert shown(h) == full(fire)
    h.advance(2.1)
    assert shown(h) == full(hello) and not shown(h).has(Flags.DROP_ACTIVE)


def test_fire_preempts_the_arm_song_at_once(h):
    h.emit(ET.ARM, DROP_A)
    h.advance(1.0)
    fire = h.emit(ET.FIRE, DROP_A)
    assert shown(h) == full(fire)


def test_cancel_then_arm_in_one_evaluation_shows_both(h):
    h.emit(ET.ARM, DROP_A)
    h.advance(5)
    cancel = h.emit(ET.CANCEL, DROP_A)
    arm = h.emit(ET.ARM, DROP_B)
    assert shown(h) == full(cancel)
    h.advance(1.1)
    assert shown(h) == full(arm)


def test_arm_song_interrupted_by_a_ping_resumes_for_the_rest_of_its_3_seconds(h):
    arm = h.emit(ET.ARM, DROP_A)
    h.advance(1.0)
    h.emit(ET.PING)
    h.advance(1.05)              # PING held 1 s
    assert shown(h) == full(arm)  # 2 s of the song still to show
    h.advance(1.9)
    assert shown(h) == full(arm)
    h.advance(0.2)
    assert shown(h).query is None


def run_script(h: BeaconHarness, script, until: float, step: float):
    """Emits ``script`` [(t, type, drop)] on a ``step`` grid and decodes the strip at every step
    (a capture). Returns (emitted events, captures)."""
    t0 = h.now
    emitted, captures = [], []
    i, t = 0, 0.0
    while t < until:
        while i < len(script) and script[i][0] <= t + 1e-9:
            _, etype, drop = script[i]
            emitted.append(h.emit(etype, drop))
            i += 1
        captures.append(h.beacon_decoded())
        h.advance(step)
        t = h.now - t0
    return emitted, captures


def test_superseded_timers_never_overwrite_newer_frames(h):
    """Six ARM + CANCEL pairs half a second apart: every CANCEL shows, in order, for its full
    second, whatever timers were scheduled in between; the screen never goes back to an older
    frame and ends on the newest ARM."""
    script = []
    for i in range(6):
        script.append((0.5 * i, ET.ARM, DROP_A if i % 2 else DROP_B))
        script.append((0.5 * i + 0.3, ET.CANCEL, DROP_A if i % 2 else DROP_B))
    script.append((3.0, ET.ARM, DROP_A))
    emitted, captures = run_script(h, script, until=14, step=0.1)
    seen = []
    for f in captures:
        assert f is not None
        if not seen or seen[-1][0] != f:
            seen.append([f, 0])
        seen[-1][1] += 1
    cancels = [full(e) for e in emitted if e["type"] == ET.CANCEL]
    assert [f for f, _n in seen if f.type == ET.CANCEL] == cancels  # all six, in order, once each
    assert all(n >= 10 for f, n in seen if f.type == ET.CANCEL)    # >= 1 s at 10 samples per second
    seqs = [f.seq for f, _n in seen]
    assert seqs == sorted(seqs)  # never back to an older frame
    last_arm = emitted[-1]
    assert seen[-2][0] == full(last_arm)
    assert seen[-1][0].type == ET.ARM and seen[-1][0].query is None and seen[-1][0].seq == last_arm["seq"]


def test_every_event_is_captured_twice_in_a_row_at_4_hz(h):
    """The companion accepts a frame after two identical consecutive reads at 4 Hz."""
    script = [(0.0, ET.HELLO, None), (0.3, ET.ARM, DROP_A), (3.6, ET.FIRE, DROP_A), (3.9, ET.CANCEL, DROP_A),
              (4.0, ET.PING, None), (4.1, ET.ARM, DROP_B), (4.2, ET.CANCEL, DROP_B), (4.25, ET.FIRE, DROP_B)]
    emitted, captures = run_script(h, script, until=16, step=0.25)
    runs: dict[tuple, int] = {}
    prev, run = None, 0
    for f in captures + [None]:
        if f is not None and prev is not None and f.raw == prev.raw:
            run += 1
        else:
            if prev is not None:
                runs[prev.key] = max(runs.get(prev.key, 0), run)
            run = 1
        prev = f
    for ev in emitted:
        if ev["type"] in (ET.FIRE, ET.CANCEL, ET.PING):
            key = full(ev).key
            assert runs.get(key, 0) >= (12 if ev["type"] == ET.FIRE else 4), (ev["type"], runs.get(key))
    fires = [e for e in emitted if e["type"] == ET.FIRE]
    assert all(full(e) in captures for e in fires)


def test_queue_is_bounded_and_drops_pings_first(h):
    first = h.emit(ET.FIRE, DROP_A)       # on screen (not queued)
    pings = [h.emit(ET.PING) for _ in range(40)]
    cancels = [h.emit(ET.CANCEL, DROP_A) for _ in range(40)]
    st = h.status()
    assert (st["queued"], st["dropped"]) == (64, 16)
    fifo = h.ns.Beacon.fifo
    q = [h.from_lua(fifo[i]) for i in range(1, len(fifo) + 1)]
    assert [e["seq"] for e in q] == [e["seq"] for e in pings[16:] + cancels]  # the oldest PINGs went
    assert shown(h) == full(first)


def test_status_counts_the_queue(h):
    h.emit(ET.FIRE, DROP_A)
    h.emit(ET.CANCEL, DROP_A)
    h.emit(ET.PING)
    st = h.status()
    assert st["queued"] == 2 and st["variant"] == "transient" and st["lastFrameType"] == int(ET.FIRE)
    h.advance(5.1)
    assert h.status()["queued"] == 0
