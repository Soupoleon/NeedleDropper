"""engine.py with fake providers/deck and an injected clock (no real sleeps)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import pytest

from needledropper import mapgen
from needledropper.config import Paths
from needledropper.engine import Engine
from needledropper.models import Drop, Event, EventType, Flags
from needledropper.providers.base import NowPlaying, PlayHandle, Provider, ProviderError, ProviderUnavailable


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class FakeDeck:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.connected = True

    def send(self, cmd: dict) -> int:
        self.sent.append(dict(cmd))
        return 1

    def cmds(self, kind: str) -> list[dict]:
        return [c for c in self.sent if c.get("cmd") == kind]


class FakeProvider(Provider):
    def __init__(self, name: str, *, avail: bool = True, can: bool = True, fail: Optional[Exception] = None,
                 attribution: Optional[str] = None) -> None:
        self.name = name
        self.avail = avail
        self.can = can
        self.fail = fail
        self.attribution = attribution
        self.poll_state = "playing"
        self.calls: list[tuple] = []

    def available(self) -> bool:
        return self.avail

    def can_play(self, drop: Drop) -> bool:
        return self.can

    def prepare(self, drop: Drop) -> None:
        self.calls.append(("prepare", drop.id))

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop.id, fade_in_ms))
        if self.fail is not None:
            raise self.fail
        np = NowPlaying(title=drop.title, artist=drop.artist, attribution=self.attribution, provider=self.name,
                        art_url="https://art/x.jpg", link="https://link/x")
        return PlayHandle(provider=self.name, drop=drop, now_playing=np)

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop.id, fade_out_ms))

    def poll(self, handle: PlayHandle) -> str:
        self.calls.append(("poll", handle.drop.id))
        return self.poll_state

    def plays(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "play"]

    def stops(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "stop"]


def make_paths(tmp_path: Path) -> Paths:
    fl = tmp_path / "WoW" / "_forever_"
    ad = fl / "Interface" / "AddOns"
    app = tmp_path / "app"
    return Paths(wow_root=tmp_path / "WoW", flavor_root=fl, addons_dir=ad, addon_dir=ad / "NeedleDropper",
                 local_addon_dir=ad / "NeedleDropper_Local", chat_log=fl / "Logs" / "WoWChatLog.txt",
                 game_exe=fl / "Wow.exe", app_dir=app, cache_dir=app / "cache", maps_dir=app / "maps")


DROPS = [
    Drop(n=1, id="zone:1411", kind="zone", title="Durotar Song", artist="A", label="Durotar", mapID=1411,
         durationMs=10000),
    Drop(n=2, id="zone:1429", kind="zone", title="Elwynn Song", artist="B", label="Elwynn Forest", mapID=1429),
    Drop(n=3, id="zone:1436", kind="zone", title="Westfall Song", artist="C", label="Westfall", mapID=1436),
]


@pytest.fixture
def env(tmp_path):
    clock = FakeClock()
    deck = FakeDeck()
    reg = mapgen.MapRegistry(make_paths(tmp_path), DROPS)
    logs: list[str] = []

    def build(*providers: FakeProvider, **kw) -> Engine:
        return Engine(list(providers), reg, deck, clock=clock, fade_in_ms=2500, fade_out_ms=3000,
                      log=logs.append, **kw)

    return {"clock": clock, "deck": deck, "reg": reg, "logs": logs, "build": build, "hash": reg.pack_hash}


def ev(env, etype: EventType, n: int, seq: int, flags: Flags = Flags.NONE, id_: str = "") -> Event:
    return Event(etype, seq, n, env["hash"], flags, id_)


def test_fire_falls_through_provider_chain(env):
    p1 = FakeProvider("spotify", avail=False)
    p2 = FakeProvider("local", fail=ProviderError("file missing"))
    p3 = FakeProvider("youtube", fail=ProviderUnavailable("disabled"))
    p4 = FakeProvider("cant", can=False)
    p5 = FakeProvider("fallback", attribution="Fallback Radio")
    eng = env["build"](p1, p2, p3, p4, p5)
    eng.handle(ev(env, EventType.FIRE, 2, 10, Flags.DROP_ACTIVE), "pixel")
    assert p1.plays() == [] and p4.plays() == []
    assert p2.plays() == ["zone:1429"] and p3.plays() == ["zone:1429"] and p5.plays() == ["zone:1429"]
    assert ("play", "zone:1429", 2500) in p5.calls
    shows = env["deck"].cmds("show")
    assert shows[0]["label"] == "Elwynn Forest" and shows[0]["attribution"] is None
    assert shows[-1]["attribution"] == "Fallback Radio" and shows[-1]["artUrl"] == "https://art/x.jpg"
    assert shows[-1]["title"] == "Elwynn Song" and shows[-1]["artist"] == "B"
    st = eng.status()
    assert st["playing"]["id"] == "zone:1429" and st["playing"]["provider"] == "fallback"
    assert any("file missing" in m for m in env["logs"])


def test_no_provider_can_play_shows_card_only(env):
    p = FakeProvider("spotify", can=False)
    eng = env["build"](p, card_only_sec=12)
    eng.handle(ev(env, EventType.FIRE, 3, 1), "pixel")
    assert p.plays() == []
    assert env["deck"].cmds("show")[-1]["hint"] == "No playable source for this drop"
    env["clock"].advance(12.5)
    eng.tick()
    assert not eng.is_playing and env["deck"].cmds("hide")
    assert any("no provider could play" in m for m in env["logs"])


def test_local_playback_flag_plays_no_audio(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.FIRE, 2, 3, Flags.LOCAL_PLAYBACK | Flags.DROP_ACTIVE), "pixel")
    assert p.plays() == []
    show = env["deck"].cmds("show")[-1]
    assert show["label"] == "Elwynn Forest" and show["hint"] == "Playing in game"
    assert eng.status()["playing"]["local"] is True
    # addon ends it: HELLO without DROP_ACTIVE -> card hidden after the grace period
    eng.handle(ev(env, EventType.HELLO, 0, 4), "pixel")
    env["clock"].advance(5.5)
    eng.tick()
    assert not eng.is_playing and env["deck"].cmds("hide")
    assert p.stops() == []


def test_cancel_stops_with_fade_and_hides(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.FIRE, 2, 1, Flags.DROP_ACTIVE), "pixel")
    eng.handle(ev(env, EventType.CANCEL, 2, 2), "pixel")
    assert p.stops() == [("stop", "zone:1429", 3000)]
    assert env["deck"].cmds("hide")[-1]["dropId"] == "zone:1429"
    assert not eng.is_playing


def test_cancel_of_armed_only_does_nothing(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.ARM, 3, 1), "pixel")
    assert ("prepare", "zone:1436") in p.calls
    assert eng.status()["armed"] == ["zone:1436"]
    eng.handle(ev(env, EventType.CANCEL, 3, 2), "pixel")
    assert p.plays() == [] and p.stops() == []
    assert eng.status()["armed"] == []
    # a CANCEL for some other n while something plays is ignored
    eng.handle(ev(env, EventType.FIRE, 2, 3), "pixel")
    eng.handle(ev(env, EventType.CANCEL, 3, 4), "pixel")
    assert p.stops() == [] and eng.is_playing


def test_arm_prepares_first_capable_provider_only(env):
    a = FakeProvider("spotify", can=False)
    b = FakeProvider("local")
    c = FakeProvider("youtube")
    eng = env["build"](a, b, c)
    eng.handle(ev(env, EventType.ARM, 1, 1), "pixel")
    assert ("prepare", "zone:1411") in b.calls
    assert not any(x[0] == "prepare" for x in a.calls + c.calls)
    eng.handle(ev(env, EventType.ARM, 2, 2, Flags.LOCAL_PLAYBACK), "pixel")
    assert ("prepare", "zone:1429") not in b.calls


def test_dedupe_across_two_transports(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    fire = ev(env, EventType.FIRE, 2, 7, Flags.DROP_ACTIVE)
    eng.handle(fire, "pixel")
    env["clock"].advance(1.0)
    eng.handle(Event(fire.type, fire.seq, fire.n, fire.hash, fire.flags, "zone:1429"), "chatlog")
    assert p.plays() == ["zone:1429"]
    st = eng.status()
    assert st["duplicates"] == 1 and st["transports"] == {"pixel": 1} and st["events"]["FIRE"] == 1
    # after the 15 s window the same key counts as new (seq wrapped around)
    eng.handle(ev(env, EventType.CANCEL, 2, 8), "pixel")
    env["clock"].advance(15.5)
    eng.tick()
    eng.handle(fire, "chatlog")
    assert p.plays() == ["zone:1429", "zone:1429"]


def test_auto_stop_after_duration(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.FIRE, 1, 1, Flags.DROP_ACTIVE), "pixel")  # durationMs = 10000
    assert eng.status()["playing"]["remainingSec"] == 10.0
    env["clock"].advance(6.9)
    eng.tick()
    assert eng.is_playing and p.stops() == []
    env["clock"].advance(0.2)  # fade starts fadeOutMs before the end
    eng.tick()
    assert p.stops() == [("stop", "zone:1411", 3000)]
    assert not eng.is_playing


def test_poll_ended_stops_and_unknown_duration_relies_on_poll(env):
    p = FakeProvider("spotify")
    eng = env["build"](p, poll_interval=1.0)
    eng.handle(ev(env, EventType.FIRE, 2, 1), "pixel")  # durationMs = 0
    env["clock"].advance(100)
    eng.tick()
    assert eng.is_playing
    p.poll_state = "ended"
    env["clock"].advance(1.1)
    eng.tick()
    assert not eng.is_playing and p.stops() == [("stop", "zone:1429", 0)]
    assert env["deck"].cmds("hide")


def test_delayed_provider_error_falls_through(env):
    yt = FakeProvider("youtube")
    am = FakeProvider("fallback", attribution="Fallback Radio")
    eng = env["build"](yt, am, poll_interval=0.5)
    eng.handle(ev(env, EventType.FIRE, 2, 1), "pixel")
    assert yt.plays() == ["zone:1429"] and am.plays() == []
    yt.poll_state = "error"  # e.g. YouTube onError 150 arrives after play() returned
    env["clock"].advance(0.6)
    eng.tick()
    assert ("stop", "zone:1429", 0) in yt.calls
    assert am.plays() == ["zone:1429"]
    assert eng.status()["playing"]["provider"] == "fallback"
    assert env["deck"].cmds("show")[-1]["attribution"] == "Fallback Radio"
    # the failed provider is not retried for this drop
    am.poll_state = "error"
    env["clock"].advance(0.6)
    eng.tick()
    assert yt.plays() == ["zone:1429"]
    assert eng.status()["playing"]["provider"] is None  # card only now


def test_late_provider_error_does_not_restart(env):
    yt = FakeProvider("youtube")
    am = FakeProvider("fallback")
    eng = env["build"](yt, am, poll_interval=0.5, fallback_window_sec=15)
    eng.handle(ev(env, EventType.FIRE, 2, 1), "pixel")
    env["clock"].advance(60)
    yt.poll_state = "error"
    eng.tick()
    assert am.plays() == []


def test_ping_is_logged_never_played(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.PING, 1, 42, Flags.TEST), "chatlog")
    assert p.plays() == [] and env["deck"].sent == []
    assert any("PING seq=42 via chatlog" in m for m in env["logs"])


def test_new_fire_stops_old_first(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.FIRE, 2, 1, Flags.DROP_ACTIVE), "pixel")
    eng.handle(ev(env, EventType.FIRE, 3, 2, Flags.DROP_ACTIVE), "pixel")
    play_idx = [i for i, c in enumerate(p.calls) if c[0] == "play"]
    stop_idx = [i for i, c in enumerate(p.calls) if c[0] == "stop"]
    assert p.plays() == ["zone:1429", "zone:1436"]
    assert p.stops() == [("stop", "zone:1429", 1000)]
    assert play_idx[0] < stop_idx[0] < play_idx[1]
    assert eng.status()["playing"]["id"] == "zone:1436"


def test_hello_without_drop_active_stops_stale_drop(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.FIRE, 2, 1, Flags.DROP_ACTIVE), "pixel")
    eng.handle(ev(env, EventType.HELLO, 0, 2, Flags.DROP_ACTIVE), "pixel")
    env["clock"].advance(10)
    eng.tick()
    assert eng.is_playing  # addon still reports it active
    eng.handle(ev(env, EventType.HELLO, 0, 3), "pixel")
    env["clock"].advance(4.0)
    eng.tick()
    assert eng.is_playing  # within the 5 s grace
    env["clock"].advance(1.5)
    eng.tick()
    assert not eng.is_playing and p.stops() == [("stop", "zone:1429", 3000)]
    assert eng.status()["addon"]["seen"] is True


def test_drop_active_again_cancels_stale_timer(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.FIRE, 2, 1, Flags.DROP_ACTIVE), "pixel")
    eng.handle(ev(env, EventType.HELLO, 0, 2), "pixel")
    env["clock"].advance(2)
    eng.handle(ev(env, EventType.ARM, 3, 3, Flags.DROP_ACTIVE), "pixel")
    env["clock"].advance(10)
    eng.tick()
    assert eng.is_playing


def test_test_drop_and_unknown_drop(env):
    p = FakeProvider("spotify")
    eng = env["build"](p)
    eng.handle(ev(env, EventType.FIRE, 0, 1, Flags.TEST | Flags.DROP_ACTIVE), "pixel")
    assert p.plays() == ["test"]
    assert env["deck"].cmds("show")[0]["test"] is True
    eng.handle(ev(env, EventType.CANCEL, 0, 2, Flags.TEST), "pixel")
    assert p.stops()[-1][1] == "test"
    eng.handle(Event(EventType.FIRE, 3, 1, (env["hash"] + 1) % 65536 or 1), "pixel")
    assert p.plays() == ["test"]
    assert any("unknown map hash" in m for m in env["logs"])


def test_provider_exception_is_contained(env):
    boom = FakeProvider("boom", fail=RuntimeError("bug"))
    ok = FakeProvider("ok")
    eng = env["build"](boom, ok)
    eng.handle(ev(env, EventType.FIRE, 2, 1), "pixel")
    assert ok.plays() == ["zone:1429"]


def test_threaded_submit_and_clean_stop(env):
    p = FakeProvider("spotify")
    eng = Engine([p], env["reg"], env["deck"], fade_in_ms=0, fade_out_ms=3000, log=env["logs"].append)
    eng.start()
    try:
        results = []

        def from_transport(name: str) -> None:
            eng.submit(Event(EventType.FIRE, 9, 2, env["hash"], Flags.DROP_ACTIVE), name)
            results.append(name)

        threads = [threading.Thread(target=from_transport, args=(n,)) for n in ("pixel", "chatlog")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        deadline = time.monotonic() + 2
        while not p.plays() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        assert p.plays() == ["zone:1429"]
        assert eng.status()["running"] is True
    finally:
        eng.stop()
    assert not eng.status()["running"]
    assert p.stops() == [("stop", "zone:1429", 1000)]  # shutdown uses a short fade


def test_play_now_and_wait_idle(env):
    p = FakeProvider("local")
    eng = env["build"](p)
    eng.play_now(DROPS[0])
    eng.drain()
    assert p.plays() == ["zone:1411"] and not eng.wait_idle(0)
    eng.stop_playback()
    eng.drain()
    assert eng.wait_idle(0) and p.stops() == [("stop", "zone:1411", 3000)]


def test_logger_object_is_accepted(env, caplog):
    import logging

    p = FakeProvider("spotify")
    eng = Engine([p], env["reg"], env["deck"], clock=env["clock"], log=logging.getLogger("t.engine"))
    with caplog.at_level(logging.INFO, logger="t.engine"):
        eng.handle(ev(env, EventType.PING, 0, 5), "pixel")
    assert "PING seq=5 via pixel" in caplog.text


def test_play_now_only_restricts_the_provider_chain(env):
    """The panel's per-service Test buttons: only that provider is tried; the card still shows."""
    sp = FakeProvider("spotify")
    yt = FakeProvider("youtube", attribution="YouTube")
    eng = env["build"](sp, yt)
    eng.play_now(DROPS[1], only="youtube")
    eng.drain()
    assert sp.plays() == [] and yt.plays() == ["zone:1429"]
    assert eng.status()["playing"]["provider"] == "youtube"
    eng.stop_playback()
    eng.drain()
    # the restricted provider cannot play -> card only, the others are still not tried
    yt.can = False
    eng.play_now(DROPS[1], only="youtube")
    eng.drain()
    assert sp.plays() == [] and eng.status()["playing"]["provider"] is None
    assert env["deck"].cmds("show")[-1]["hint"] == "No playable source for this drop"
    eng.stop_playback()
    eng.drain()
    eng.play_now(DROPS[1])  # no filter: the whole chain again
    eng.drain()
    assert sp.plays() == ["zone:1429"]


def test_set_providers_and_registry_swap_live(env, tmp_path):
    old = FakeProvider("spotify")
    eng = env["build"](old)
    eng.handle(ev(env, EventType.FIRE, 2, 1, Flags.DROP_ACTIVE), "pixel")
    assert eng.current_provider() is old
    new = FakeProvider("spotify")
    previous = eng.set_providers([new])
    assert previous == [old] and eng.providers == [new]
    # the drop in progress keeps (and is stopped by) the instance that started it
    eng.handle(ev(env, EventType.CANCEL, 2, 2), "pixel")
    assert old.stops() and new.stops() == []
    eng.handle(ev(env, EventType.FIRE, 3, 3, Flags.DROP_ACTIVE), "pixel")
    assert new.plays() == ["zone:1436"]
    reg2 = mapgen.MapRegistry(make_paths(tmp_path / "other"), [
        Drop(n=1, id="zone:1411", kind="zone", title="Other Song", artist="Z", label="Durotar", mapID=1411)])
    eng.set_registry(reg2)
    assert eng.registry is reg2
    eng.handle(Event(EventType.FIRE, 4, 1, reg2.pack_hash, Flags.DROP_ACTIVE, ""), "pixel")
    assert eng.status()["playing"]["title"] == "Other Song"
