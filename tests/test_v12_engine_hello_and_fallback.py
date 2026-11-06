"""Engine fixes from the v1.2 integration:

* ENG-STALE-HELLO (docs/review/baseline-findings.md): a HELLO the chat log delivers late (it was written
  before the FIRE, unpadded, and flushed in one batch with it) must not stop the drop the beacon started.
* Real-client finding #4: a provider that fails after taking over playback (Spotify accepted a song but
  never started it) hands back a clean-up handle and a notice; the engine plays the next provider,
  tells the shell, and stops the clean-up handle when the drop ends.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v12_engine_flows import AFRICA, SP_ID, YT_ID, FakeSpotify, Rig  # noqa: E402

from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import Drop, EventType as ET, Flags  # noqa: E402
from needledropper.providers.base import PlayHandle  # noqa: E402
from needledropper.providers.spotify import NOTICE_STUCK, SpotifyStuckError  # noqa: E402


def deliver(r: Rig, fr: p2.Frame, transport: str) -> None:
    r.engine.handle(p2.decode_frame(p2.encode_frame(fr)), transport)


# ------------------------------------------------------------------ ENG-STALE-HELLO --

def test_a_late_chat_log_hello_from_before_the_fire_does_not_stop_the_drop():
    """The finding's variant (B): the beacon delivers the FIRE first; 0.6 s later the chat log flushes
    the login HELLO, the ARM and its own copy of the FIRE in one batch."""
    r = Rig()
    hello = p2.build_frame(ET.HELLO, seq=10)
    arm = p2.build_frame(ET.ARM, seq=11, spotify=SP_ID, **AFRICA)
    fire = p2.build_frame(ET.FIRE, seq=12, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    deliver(r, hello, "pixel")
    r.clock.advance(20)                              # the HELLO is far outside the 15 s dedupe window
    r.engine.tick()
    deliver(r, fire, "pixel")
    assert r.engine.is_playing
    r.clock.advance(0.6)
    for fr in (hello, arm, fire):                    # the chat-log batch
        deliver(r, fr, "chatlog")
    for _ in range(12):
        r.clock.advance(1.0)
        r.engine.tick()
    assert r.engine.is_playing and r.sp.of("stop") == []
    assert len(r.sp.of("play")) == 1


def test_a_newer_hello_still_ends_the_drop_and_an_older_duplicate_cannot_cancel_that():
    r = Rig()
    fire = p2.build_frame(ET.FIRE, seq=40, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    deliver(r, fire, "pixel")
    deliver(r, p2.build_frame(ET.HELLO, seq=41), "pixel")       # the drop ended in game
    r.clock.advance(1.0)
    deliver(r, fire, "chatlog")                                  # its late chat-log copy (DROP_ACTIVE, older)
    deliver(r, p2.build_frame(ET.HELLO, seq=40, flags=Flags.DROP_ACTIVE), "chatlog")  # an old steady frame
    r.clock.advance(4.5)
    r.engine.tick()
    assert not r.engine.is_playing and r.sp.of("stop") == [("stop", "area:87", 3000)]


def test_drop_active_again_after_a_newer_hello_cancels_the_timer():
    r = Rig()
    deliver(r, p2.build_frame(ET.FIRE, seq=5, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA), "pixel")
    deliver(r, p2.build_frame(ET.HELLO, seq=6), "pixel")
    r.clock.advance(3)
    deliver(r, p2.build_frame(ET.HELLO, seq=7, flags=Flags.DROP_ACTIVE), "pixel")  # e.g. a /reload re-armed it
    r.clock.advance(5)
    r.engine.tick()
    assert r.engine.is_playing


def test_the_seq_comparison_wraps_around():
    r = Rig()
    deliver(r, p2.build_frame(ET.FIRE, seq=250, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA), "pixel")
    deliver(r, p2.build_frame(ET.HELLO, seq=200), "chatlog")     # older: ignored
    r.clock.advance(6)
    r.engine.tick()
    assert r.engine.is_playing
    deliver(r, p2.build_frame(ET.HELLO, seq=3), "pixel")         # 250 -> 3 is newer (wrapped)
    r.clock.advance(5.5)
    r.engine.tick()
    assert not r.engine.is_playing


def test_v1_events_follow_the_same_rule():
    from needledropper.models import Event

    class Registry:
        installed_hash = None

        def resolve_event(self, ev):
            return Drop(n=ev.n, id="zone:1429", kind="zone", title="Africa", artist="Toto",
                        spotify=f"spotify:track:{SP_ID}")

        def describe_miss(self, ev):
            return "?"

    r = Rig(registry=Registry())
    r.engine.handle(Event(ET.FIRE, 30, 1, 40213, Flags.DROP_ACTIVE), "pixel")
    r.engine.handle(Event(ET.HELLO, 29, 0, 40213, Flags.NONE), "chatlog")   # older than the FIRE
    r.clock.advance(6)
    r.engine.tick()
    assert r.engine.is_playing
    r.engine.handle(Event(ET.HELLO, 31, 0, 40213, Flags.NONE), "pixel")
    r.clock.advance(5.5)
    r.engine.tick()
    assert not r.engine.is_playing


# --------------------------------------------------------------- stuck provider --

class StuckSpotify(FakeSpotify):
    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop.id, drop.spotify, drop.startMs, drop.durationMs, drop.volume))
        cleanup = PlayHandle(provider=self.name, drop=drop, data={"parked": True})
        raise SpotifyStuckError("Spotify isn't starting songs — restart the Spotify app", cleanup=cleanup)

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop.id, fade_out_ms, bool(handle.data.get("parked"))))


def test_stuck_spotify_falls_back_to_youtube_notes_it_and_cleans_up_at_the_end():
    r = Rig(spotify=StuckSpotify())
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, youtube=YT_ID, **AFRICA)
    assert r.yt.of("play") == [("play", "area:87", YT_ID, 0)]            # the next provider plays it
    notes = r.changes(NOTICE_STUCK)
    assert len(notes) == 1 and notes[0]["provider"] == "spotify" and notes[0]["title"] == "Africa"
    assert r.sp.of("stop") == []                                          # nothing to clean up yet
    r.send(ET.CANCEL, drop_id="area:87")                                  # the drop ends
    assert r.yt.of("stop") and r.sp.of("stop") == [("stop", "area:87", 0, True)]


def test_clean_up_also_runs_when_the_companion_shuts_down():
    r = Rig(spotify=StuckSpotify())
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, youtube=YT_ID, **AFRICA)
    r.engine.stop()
    assert r.sp.of("stop") == [("stop", "area:87", 0, True)]
