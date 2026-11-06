"""engine.py v2 flows (DESIGN-v1.2 2.2, CONTRACTS-v1.2 3.2) with fake providers, deck and clock.

Frames are built with ``protocol2.build_frame`` (the same normalisation as the addon's encoder), so
these tests exercise exactly what the transports deliver: no map, no registry.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from needledropper import gamedata as gd
from needledropper import protocol2 as p2
from needledropper.engine import Engine, describe_event, query_key
from needledropper.models import Drop, Event, EventType as ET, Flags
from needledropper.providers.base import NowPlaying, PlayHandle, Provider, ProviderError, ProviderUnavailable

SP_ID = "4uLU6hMCjMI75M1A2tKUQC"
SP_MATCH = "6EPRKhUOdiFSQwGBRBbvsZ"
YT_ID = "QAo_Ycocl1E"


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
        self.statuses: dict[str, dict] = {}

    def send(self, cmd: dict) -> int:
        self.sent.append(dict(cmd))
        return 1

    def status_for(self, play_id: str) -> Optional[dict]:
        return self.statuses.get(play_id)

    def cmds(self, kind: str) -> list[dict]:
        return [c for c in self.sent if c.get("cmd") == kind]


class FakeSpotify(Provider):
    """Plays drops with a URI; ``resolve_drop`` answers from ``matches`` {title: (uri, title, artist, ms)}."""

    name = "spotify"

    def __init__(self, *, avail: bool = True, matches: Optional[dict] = None, fail: Optional[Exception] = None,
                 gate: Optional[threading.Event] = None) -> None:
        self.avail = avail
        self.matches = dict(matches or {})
        self.fail = fail
        self.gate = gate  # resolve_drop blocks until set (a slow search)
        self.lookup_error: Optional[Exception] = None
        self.calls: list[tuple] = []
        self.poll_state = "playing"

    def available(self) -> bool:
        return self.avail

    def can_play(self, drop: Drop) -> bool:
        return self.avail and bool(drop.spotify or (drop.title and drop.artist))

    def resolve_drop(self, drop: Drop) -> Drop:
        self.calls.append(("resolve", drop.artist, drop.title, drop.spotify))
        if self.gate is not None:
            self.gate.wait(5)
        if drop.spotify:
            if self.lookup_error is not None:
                raise self.lookup_error
            return dataclasses.replace(drop, extra={**drop.extra, "matchedTitle": "Linked Title",
                                                    "matchedArtist": "Linked Artist", "trackDurationMs": 201000})
        m = self.matches.get(drop.title)
        if isinstance(m, Exception):
            raise m
        if m is None:
            return drop
        uri, title, artist, ms = m
        return dataclasses.replace(drop, spotify=uri, extra={**drop.extra, "matchedTitle": title,
                                                             "matchedArtist": artist, "trackDurationMs": ms})

    def prepare(self, drop: Drop) -> None:
        self.calls.append(("prepare", drop.id, drop.spotify))

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop.id, drop.spotify, drop.startMs, drop.durationMs, drop.volume))
        if self.fail is not None:
            raise self.fail
        if not drop.spotify:
            raise ProviderError(f"No Spotify track found for {drop.title!r} by {drop.artist!r}")
        return PlayHandle(provider=self.name, drop=drop,
                          now_playing=NowPlaying(title=drop.title, artist=drop.artist, attribution="Spotify",
                                                 provider=self.name))

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop.id, fade_out_ms))

    def poll(self, handle: PlayHandle) -> str:
        return self.poll_state

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


class FakeYouTube(Provider):
    name = "youtube"

    def __init__(self, *, fail: Optional[Exception] = None) -> None:
        self.fail = fail
        self.calls: list[tuple] = []
        self.poll_state = "playing"
        self.n = 0

    def available(self) -> bool:
        return True

    def can_play(self, drop: Drop) -> bool:
        return bool(drop.youtube)

    def prepare(self, drop: Drop) -> None:
        self.calls.append(("prepare", drop.id, drop.youtube))

    def play(self, drop: Drop, *, fade_in_ms: int = 0) -> PlayHandle:
        self.calls.append(("play", drop.id, drop.youtube, drop.startMs))
        if self.fail is not None:
            raise self.fail
        self.n += 1
        return PlayHandle(provider=self.name, drop=drop, data={"playId": f"p{self.n}"},
                          now_playing=NowPlaying(title=drop.title, artist=drop.artist, attribution="YouTube",
                                                 provider=self.name))

    def stop(self, handle: PlayHandle, *, fade_out_ms: int = 0) -> None:
        self.calls.append(("stop", handle.drop.id, fade_out_ms))

    def poll(self, handle: PlayHandle) -> str:
        return self.poll_state

    def of(self, kind: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == kind]


class Rig:
    """An engine with fake Spotify + YouTube, recording what it tells the game and the shell."""

    def __init__(self, *, spotify: Optional[FakeSpotify] = None, youtube: Optional[FakeYouTube] = None,
                 extra: tuple = (), **kw: Any) -> None:
        self.clock = FakeClock()
        self.deck = FakeDeck()
        self.sp = spotify if spotify is not None else FakeSpotify(matches={"Africa": (f"spotify:track:{SP_MATCH}",
                                                                                     "Africa", "TOTO", 295000)})
        self.yt = youtube if youtube is not None else FakeYouTube()
        self.records: list[tuple[str, dict]] = []
        self.statuses: list[dict] = []
        self.links: list[tuple[Any, str]] = []
        self.logs: list[str] = []
        kw.setdefault("prewarm_async", False)
        self.engine = Engine([self.yt, self.sp, *extra], self.deck, clock=self.clock, fade_in_ms=2500,
                             fade_out_ms=3000, log=self.logs.append,
                             on_link_check=lambda fr, t: self.links.append((fr, t)),
                             on_status=self.statuses.append,
                             track_recorder=lambda k, i: self.records.append((k, dict(i))), **kw)
        self.seq = 0

    def send(self, etype: ET, transport: str = "pixel", **fields: Any) -> p2.Frame:
        if "seq" not in fields:
            self.seq += 1
            fields["seq"] = self.seq
        fr = p2.build_frame(etype, **fields)
        self.engine.handle(p2.decode_frame(p2.encode_frame(fr)), transport)  # exactly what a transport delivers
        return fr

    def changes(self, kind: Optional[str] = None) -> list[dict]:
        return [s["change"] for s in self.statuses if kind is None or s["change"]["kind"] == kind]


AFRICA = {"drop_id": "area:87", "artist": "Toto", "title": "Africa", "label": "Goldshire"}


# ------------------------------------------------------------------- routing --

def test_fire_with_spotify_id_plays_spotify_without_searching():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, youtube=YT_ID, start_ms=12000, length_ms=60000,
           volume=80, **AFRICA)
    assert r.sp.of("resolve") == []
    assert r.sp.of("play") == [("play", "area:87", f"spotify:track:{SP_ID}", 12000, 60000, 80)]
    assert r.yt.of("play") == []
    st = r.engine.status()
    assert st["playing"]["provider"] == "spotify" and st["playing"]["label"] == "Goldshire"
    assert st["playing"]["remainingSec"] == 60.0 and st["addon"]["protocol"] == 2
    show = r.deck.cmds("show")[-1]
    assert (show["label"], show["title"], show["artist"], show["test"]) == ("Goldshire", "Africa", "Toto", False)


def test_fire_with_query_searches_spotify_then_plays_the_match_and_records_it():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
    assert [c[2] for c in r.sp.of("resolve")] == ["Africa"]
    assert r.sp.of("play")[0][2] == f"spotify:track:{SP_MATCH}"
    assert r.yt.of("play") == []
    key = query_key("Toto", "Africa")
    assert key == "q:toto\x1fafrica"
    assert (key, {"spotify": SP_MATCH, "durationMs": 295000, "title": "Africa", "artist": "TOTO",
                  "notFound": False}) in r.records
    assert [s["change"]["kind"] for s in r.statuses[-2:]] == ["match", "playing"]
    assert r.statuses[-1]["playing"]["provider"] == "spotify"  # on_status gets the whole status
    # the second FIRE of the same song reuses the search
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
    assert len(r.sp.of("resolve")) == 1 and len(r.sp.of("play")) == 2


def test_no_spotify_match_falls_back_to_youtube_and_records_not_found():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, drop_id="zone:1429", artist="Nobody", title="Nothing", youtube=YT_ID)
    assert r.sp.of("play") == []  # no match: Spotify is not even tried
    assert r.yt.of("play") == [("play", "zone:1429", YT_ID, 0)]
    assert (query_key("Nobody", "Nothing"), {"notFound": True}) in r.records
    assert r.changes("notFound")[0]["key"] == "q:nobody\x1fnothing"
    assert r.engine.status()["spotifySearches"]["notFound"] == 1


def test_failed_search_is_reported_not_recorded_and_youtube_still_plays():
    r = Rig(spotify=FakeSpotify(matches={"Africa": ProviderUnavailable("Spotify sign-in expired")}))
    r.send(ET.ARM, youtube=YT_ID, **AFRICA)
    failed = r.changes("searchFailed")
    assert failed and failed[0]["unavailable"] is True and "sign-in expired" in failed[0]["detail"]
    assert r.records == []
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
    assert r.engine.status()["playing"]["provider"] == "youtube"
    assert len(r.sp.of("resolve")) == 2  # a failed search is tried again, never cached as "not found"
    assert r.records == []


def test_spotify_unavailable_goes_straight_to_youtube():
    r = Rig(spotify=FakeSpotify(avail=False))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, youtube=YT_ID, **AFRICA)
    assert r.sp.calls == [] and r.yt.of("play")[0][2] == YT_ID
    assert r.records == []


def test_spotify_failure_falls_through_to_youtube():
    r = Rig(spotify=FakeSpotify(fail=ProviderError("No Spotify device is available")))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, youtube=YT_ID, **AFRICA)
    assert len(r.sp.of("play")) == 1 and len(r.yt.of("play")) == 1
    assert r.engine.status()["playing"]["provider"] == "youtube"
    assert any("No Spotify device" in m for m in r.logs)


def test_other_providers_come_after_spotify_and_youtube():
    class Other(FakeYouTube):
        name = "other"

        def can_play(self, drop: Drop) -> bool:
            return True

    other = Other()
    r = Rig(spotify=FakeSpotify(avail=False), youtube=FakeYouTube(fail=ProviderError("player closed")),
            extra=(other,))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
    assert [c[0] for c in r.yt.calls] == ["play"] and other.of("play")


def test_fire_without_a_playable_source_shows_the_card_only():
    r = Rig(spotify=FakeSpotify(avail=False))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, drop_id="zone:1429", artist="Toto", title="Africa")
    assert r.yt.calls == []
    assert r.deck.cmds("show")[-1]["hint"] == "No playable source for this drop"
    assert r.changes("noSource")
    r.clock.advance(12.5)
    r.engine.tick()
    assert not r.engine.is_playing and r.deck.cmds("hide")


def test_fire_routes_ignore_the_configured_provider_order():
    """Spotify comes first even though the shell listed YouTube first (DESIGN 2.2 order)."""
    r = Rig()
    assert [p.name for p in r.engine.providers] == ["youtube", "spotify"]
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, youtube=YT_ID, **AFRICA)
    assert r.sp.of("play") and not r.yt.of("play")


# ------------------------------------------------------------ flags: local, test --

def test_local_playback_shows_and_logs_only():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE | Flags.LOCAL_PLAYBACK, spotify=SP_ID, length_ms=30000, **AFRICA)
    assert r.sp.of("play") == [] and r.yt.of("play") == []
    show = r.deck.cmds("show")[-1]
    assert show["hint"] == "Playing in game" and show["title"] == "Africa"
    st = r.engine.status()["playing"]
    assert st["local"] is True and st["provider"] == "game" and st["remainingSec"] == 30.0
    assert r.records == [] and r.changes("local")
    # the addon ends it: HELLO without DROP_ACTIVE for more than 5 s
    r.send(ET.HELLO)
    r.clock.advance(5.5)
    r.engine.tick()
    assert not r.engine.is_playing and r.deck.cmds("hide")


def test_test_fire_plays_records_nothing_and_checks_the_link():
    r = Rig()
    fr = r.send(ET.FIRE, "chatlog", flags=Flags.DROP_ACTIVE | Flags.TEST, youtube=YT_ID, artist="Toto",
                title="Africa", drop_id="area:87")
    assert r.sp.of("play")[0][2] == f"spotify:track:{SP_MATCH}"  # searched and played ...
    assert r.records == []  # ... but nothing is recorded for the game
    assert len(r.links) == 1 and r.links[0][1] == "chatlog" and r.links[0][0] == fr
    assert r.deck.cmds("show")[-1]["test"] is True
    assert r.engine.status()["playing"]["test"] is True
    assert r.engine.status()["linkChecks"] == 1


def test_needle_test_carries_no_song_and_never_touches_playback():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE | Flags.TEST, drop_id="test")
    assert len(r.links) == 1
    assert r.engine.status()["playing"]["id"] == "area:87"  # the song keeps playing
    assert len(r.sp.of("play")) == 1 and r.sp.of("stop") == []
    assert any("test signal from World of Warcraft" in m for m in r.logs)


def test_ping_checks_the_link_and_is_never_played():
    r = Rig()
    r.send(ET.PING, "chatlog", flags=Flags.TEST)
    assert r.sp.calls == [] and r.yt.calls == [] and r.deck.sent == []
    assert len(r.links) == 1 and any("PING" in m and "via chatlog" in m for m in r.logs)


def test_every_test_flagged_frame_calls_on_link_check():
    r = Rig()
    r.send(ET.HELLO, flags=Flags.TEST)
    r.send(ET.CANCEL, flags=Flags.TEST, drop_id="area:87")
    r.send(ET.HELLO)
    assert [fr.type for fr, _t in r.links] == [ET.HELLO, ET.CANCEL]


# ----------------------------------------------------------------- ARM cache --

def test_arm_caches_the_song_and_prewarms_spotify_with_the_search_result():
    r = Rig()
    r.send(ET.ARM, youtube=YT_ID, **AFRICA)
    assert r.sp.of("resolve") == [("resolve", "Toto", "Africa", None)]
    assert r.sp.of("prepare") == [("prepare", "area:87", f"spotify:track:{SP_MATCH}")]  # device wake
    assert r.yt.of("prepare") == []  # Spotify will play it: no YouTube preload
    assert r.engine.status()["armed"] == ["area:87"]
    assert r.records and r.records[0][0] == "q:toto\x1fafrica"
    assert r.changes("armed")[0]["label"] == "Goldshire"


def test_arm_without_spotify_preloads_youtube():
    r = Rig(spotify=FakeSpotify(avail=False))
    r.send(ET.ARM, youtube=YT_ID, **AFRICA)
    assert r.yt.of("prepare") == [("prepare", "area:87", YT_ID)]


def test_arm_with_no_spotify_match_preloads_youtube():
    r = Rig()
    r.send(ET.ARM, youtube=YT_ID, drop_id="zone:1429", artist="Nobody", title="Nothing")
    assert r.sp.of("prepare") == [] and r.yt.of("prepare") == [("prepare", "zone:1429", YT_ID)]


def test_arm_with_a_spotify_link_records_the_linked_track():
    r = Rig()
    r.send(ET.ARM, spotify=SP_ID, **AFRICA)
    assert (f"sp:{SP_ID}", {"durationMs": 201000, "title": "Linked Title", "artist": "Linked Artist",
                            "notFound": False}) in r.records
    assert r.sp.of("prepare")[0][2] == f"spotify:track:{SP_ID}"


def test_dead_spotify_link_is_recorded_as_not_found():
    sp = FakeSpotify()
    err = ProviderError("Spotify GET /tracks: 404 Not Found")
    err.status = 404  # like SpotifyApiError
    sp.lookup_error = err
    r = Rig(spotify=sp)
    r.send(ET.ARM, spotify=SP_ID, **AFRICA)
    assert (f"sp:{SP_ID}", {"notFound": True}) in r.records


def test_key_only_arm_refreshes_and_fire_fills_fitted_fields_from_the_arm():
    """The full ARM shows for 3 s, then only the place key; a FIRE whose query/label/drop id were
    fitted away still plays the ARM's song (searching on the ARM's query)."""
    r = Rig()
    r.send(ET.ARM, youtube=YT_ID, set_id="azeroth-mixtape", **AFRICA)
    pk = p2.place_key_for("area:87")
    r.clock.advance(3.0)
    r.send(ET.ARM, place_key=pk)  # placeKey only: must not replace the cached song
    assert r.engine.status()["armed"] == ["area:87"]
    r.clock.advance(0.5)
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, place_key=pk, youtube=YT_ID)  # query, label, id fitted away
    played = r.sp.of("play")
    assert played and played[0][1] == "area:87" and played[0][2] == f"spotify:track:{SP_MATCH}"
    assert r.engine.status()["playing"]["label"] == "Goldshire"
    assert r.engine.status()["armed"] == []  # the FIRE consumed the ARM


def test_fire_with_only_a_place_key_plays_the_armed_song():
    r = Rig(spotify=FakeSpotify(avail=False))
    r.send(ET.ARM, youtube=YT_ID, length_ms=90000, **AFRICA)
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, place_key=p2.place_key_for("area:87"))
    assert r.yt.of("play") == [("play", "area:87", YT_ID, 0)]
    assert r.engine.status()["playing"]["remainingSec"] == 90.0


def test_arm_of_a_different_song_at_the_same_place_is_not_merged():
    r = Rig(spotify=FakeSpotify(avail=False))
    r.send(ET.ARM, youtube=YT_ID, **AFRICA)
    other_yt = "dQw4w9WgXcQ"
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, drop_id="area:87", youtube=other_yt)
    assert r.yt.of("play")[0][2] == other_yt
    assert r.engine.status()["playing"]["title"] == ""  # the ARM's query belonged to another song


def test_cancel_of_an_armed_drop_forgets_it():
    r = Rig()
    r.send(ET.ARM, youtube=YT_ID, **AFRICA)
    r.send(ET.CANCEL, drop_id="area:87")
    assert r.engine.status()["armed"] == [] and r.sp.of("play") == []
    assert any("abandoned" in m for m in r.logs)


def test_armed_songs_expire():
    r = Rig()
    r.send(ET.ARM, youtube=YT_ID, **AFRICA)
    r.clock.advance(31 * 60)
    r.engine.tick()
    assert r.engine.status()["armed"] == []


# ------------------------------------------------- slow pre-warm: FIRE waits --

def test_fire_waits_for_a_search_still_running_on_the_worker():
    gate = threading.Event()
    sp = FakeSpotify(matches={"Africa": (f"spotify:track:{SP_MATCH}", "Africa", "Toto", 295000)}, gate=gate)
    r = Rig(spotify=sp, prewarm_async=True)
    try:
        r.send(ET.ARM, youtube=YT_ID, **AFRICA)
        deadline = time.monotonic() + 2
        while not sp.of("resolve") and time.monotonic() < deadline:
            time.sleep(0.005)
        assert sp.of("resolve"), "the search runs on the pre-warm worker"
        threading.Timer(0.3, gate.set).start()  # the search answers 0.3 s after the FIRE
        t0 = time.monotonic()
        r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
        waited = time.monotonic() - t0
        assert 0.2 <= waited < 3.0
        assert sp.of("play") and sp.of("play")[0][2] == f"spotify:track:{SP_MATCH}"
        assert r.yt.of("play") == []
        assert len(sp.of("resolve")) == 1  # no second search
        assert ("prepare", "area:87", f"spotify:track:{SP_MATCH}") in sp.calls
        r.engine.drain()  # the worker's result is recorded once
        assert [k for k, _i in r.records].count("q:toto\x1fafrica") == 1
    finally:
        gate.set()
        r.engine.stop()


def test_fire_waits_at_most_fire_wait_sec_then_plays_youtube_first():
    gate = threading.Event()
    sp = FakeSpotify(matches={"Africa": (f"spotify:track:{SP_MATCH}", "Africa", "Toto", 295000)}, gate=gate)
    r = Rig(spotify=sp, prewarm_async=True, fire_wait_sec=1.0)
    try:
        r.send(ET.ARM, youtube=YT_ID, **AFRICA)
        deadline = time.monotonic() + 2
        while not sp.of("resolve") and time.monotonic() < deadline:
            time.sleep(0.005)
        t0 = time.monotonic()
        r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
        waited = time.monotonic() - t0
        assert 0.95 <= waited < 1.8  # one shared budget (a wait per step would take 2 s)
        assert r.yt.of("play") and r.engine.status()["playing"]["provider"] == "youtube"
        assert len(sp.of("resolve")) == 1 and sp.of("play") == []  # Spotify only after YouTube, not tried yet
        assert any("pre-warm still running" in m for m in r.logs)
    finally:
        gate.set()
        r.engine.stop()


def test_fire_elsewhere_cancels_queued_prewarms_of_other_places():
    gate = threading.Event()
    sp = FakeSpotify(matches={}, gate=gate)
    r = Rig(spotify=sp, prewarm_async=True, fire_wait_sec=0.2)
    try:
        r.send(ET.ARM, drop_id="zone:1", artist="A", title="One")  # blocks the worker
        r.send(ET.ARM, drop_id="zone:2", artist="B", title="Two")  # queued behind it
        r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, drop_id="zone:3", youtube=YT_ID)
        gate.set()
        assert r.engine.wait_prewarm(3)
        titles = [c[2] for c in sp.of("resolve")]
        assert "Two" not in titles  # cancelled: the player is somewhere else now
        r.engine.drain()
        # the cancelled search is finished (nobody waits for it) and searched again when needed
        r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, drop_id="zone:2", artist="B", title="Two")
        assert "Two" in [c[2] for c in sp.of("resolve")]
    finally:
        gate.set()
        r.engine.stop()


# ---------------------------------------------------------- CANCEL and HELLO --

def test_cancel_fades_out_the_drop_playing_there():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    r.send(ET.CANCEL, drop_id="zone:1429")  # another place: ignored
    assert r.engine.is_playing
    r.send(ET.CANCEL, drop_id="area:87")
    assert r.sp.of("stop") == [("stop", "area:87", 3000)]
    assert not r.engine.is_playing and r.deck.cmds("hide")[-1]["dropId"] == "area:87"
    assert r.changes("stopped")[-1]["reason"] == "cancelled by addon"


def test_cancel_by_place_key_alone_stops_a_drop_whose_id_was_fitted_away():
    r = Rig()
    pk = p2.place_key_for("sub:1429:lion-s-pride-inn")
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, place_key=pk, spotify=SP_ID, artist="A", title="B")
    assert r.engine.status()["playing"]["id"] == f"place:{pk}"
    r.send(ET.CANCEL, drop_id="sub:1429:lion-s-pride-inn")
    assert not r.engine.is_playing


def test_cancel_with_a_colliding_place_key_but_another_drop_id_is_ignored():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    pk = p2.place_key_for("area:87")
    r.send(ET.CANCEL, place_key=pk, drop_id="zone:9999")  # same 16-bit key, different drop
    assert r.engine.is_playing


def test_hello_without_drop_active_stops_after_5_seconds():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    r.send(ET.HELLO, flags=Flags.DROP_ACTIVE)
    r.clock.advance(30)
    r.engine.tick()
    assert r.engine.is_playing  # still active in game
    r.send(ET.HELLO)
    r.clock.advance(4.5)
    r.engine.tick()
    assert r.engine.is_playing  # within the grace
    r.clock.advance(1.0)
    r.engine.tick()
    assert not r.engine.is_playing and r.sp.of("stop") == [("stop", "area:87", 3000)]


def test_hello_flag_changes_with_the_same_seq_are_not_duplicates():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    r.send(ET.HELLO, seq=50, flags=Flags.DROP_ACTIVE)
    r.send(ET.HELLO, seq=50)  # the same seq, DROP_ACTIVE gone: must start the stale timer
    r.clock.advance(5.5)
    r.engine.tick()
    assert not r.engine.is_playing


def test_new_fire_replaces_the_current_drop():
    r = Rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, drop_id="zone:1429", youtube=YT_ID)
    assert r.sp.of("stop") == [("stop", "area:87", 1000)]
    assert r.engine.status()["playing"]["id"] == "zone:1429"


# ------------------------------------------------------------------- dedupe --

def test_the_same_frame_over_both_transports_plays_once():
    r = Rig()
    fr = p2.build_frame(ET.FIRE, seq=7, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **AFRICA)
    r.engine.handle(fr, "pixel")
    r.clock.advance(1)
    r.engine.handle(p2.parse_chat_line(p2.format_chat_line(fr)), "chatlog")
    assert len(r.sp.of("play")) == 1
    st = r.engine.status()
    assert st["duplicates"] == 1 and st["transports"] == {"pixel": 1} and st["events"]["FIRE"] == 1
    r.clock.advance(15.5)
    r.engine.tick()
    r.engine.handle(fr, "chatlog")  # the dedupe window is over (seq wrapped around)
    assert len(r.sp.of("play")) == 2


# ---------------------------------------------------- YouTube problems --

def test_unembeddable_youtube_video_is_reported_and_recorded():
    r = Rig(spotify=FakeSpotify(avail=False),
            youtube=FakeYouTube(fail=ProviderError("YouTube error 150: the owner does not allow embedding")))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
    assert (f"yt:{YT_ID}", {"notEmbeddable": True}) in r.records
    assert ("q:toto\x1fafrica", {"notEmbeddable": True}) in r.records
    note = r.changes("notEmbeddable")[0]
    assert note["youtube"] == YT_ID and note["code"] == 150
    # the video works again later: the flag is cleared
    r.yt.fail = None
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
    assert (f"yt:{YT_ID}", {"notEmbeddable": False}) in r.records


def test_youtube_error_reported_after_start_uses_the_player_code():
    yt = FakeYouTube()
    r = Rig(spotify=FakeSpotify(avail=False), youtube=yt, poll_interval=0.5)
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
    r.deck.statuses["p1"] = {"state": "error", "code": 101}
    yt.poll_state = "error"
    r.clock.advance(0.6)
    r.engine.tick()
    assert (f"yt:{YT_ID}", {"notEmbeddable": True}) in r.records
    assert r.deck.cmds("show")[-1]["hint"] == "No playable source for this drop"


def test_other_youtube_errors_are_not_recorded():
    r = Rig(spotify=FakeSpotify(avail=False), youtube=FakeYouTube(fail=ProviderError("player window closed")))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, youtube=YT_ID, **AFRICA)
    assert r.records == []


def test_test_flag_never_records_youtube_problems():
    r = Rig(spotify=FakeSpotify(avail=False),
            youtube=FakeYouTube(fail=ProviderError("YouTube error 150: the owner does not allow embedding")))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE | Flags.TEST, youtube=YT_ID, **AFRICA)
    assert r.records == [] and r.changes("notEmbeddable")  # the shell still hears about it


# ------------------------------------------------------- legacy v1 events --

def test_v1_events_without_a_registry_are_ignored_with_one_clear_message():
    r = Rig()
    r.engine.handle(Event(ET.FIRE, 1, 12, 40213, Flags.DROP_ACTIVE), "pixel")
    r.engine.handle(Event(ET.FIRE, 2, 13, 40213, Flags.DROP_ACTIVE), "pixel")
    assert r.sp.calls == [] and r.yt.calls == []
    warnings = [m for m in r.logs if "older" in m or "old NeedleDropper addon" in m]
    assert len(warnings) == 1 and "Update the NeedleDropper addon" in warnings[0]
    assert r.changes("outdatedAddon")
    st = r.engine.status()
    assert st["addon"]["protocol"] == 1 and st["addon"]["outdated"] is True and st["addon"]["seen"]
    r.engine.handle(Event(ET.PING, 3, 0, 40213, Flags.TEST), "chatlog")
    assert len(r.links) == 1  # an old addon still proves the link


def test_v1_registry_still_plays_old_addons():
    class Registry:
        installed_hash = None

        def resolve_event(self, ev: Event) -> Optional[Drop]:
            return Drop(n=ev.n, id="zone:1429", kind="zone", title="Old", artist="Song", youtube=YT_ID)

        def describe_miss(self, ev: Event) -> str:
            return "no such drop"

    deck = FakeDeck()
    yt = FakeYouTube()
    for eng in (Engine([yt], Registry(), deck, log=lambda m: None),  # the v1.1 call
                Engine([yt], deck, registry=Registry(), log=lambda m: None)):
        eng.handle(Event(ET.FIRE, 1, 2, 40213, Flags.DROP_ACTIVE), "pixel")
        assert eng.status()["playing"]["title"] == "Old"
        eng.handle(Event(ET.CANCEL, 2, 2, 40213), "pixel")
        assert not eng.is_playing
    assert len(yt.of("play")) == 2


def test_constructor_forms():
    deck = FakeDeck()
    e1 = Engine([], deck)
    assert e1.deck is deck and e1.registry is None

    class Reg:
        def resolve_event(self, ev):  # noqa: ANN001
            return None

    reg = Reg()
    e2 = Engine([], reg)  # Engine(providers, registry): no deck
    assert e2.registry is reg and e2.deck is None
    with pytest.raises(TypeError):
        Engine([], reg, deck, deck)


# ------------------------------------------------------------ gamedata hook --

def test_engine_records_through_gamedata_when_given_the_addons_folder(tmp_path):
    addons = tmp_path / "_forever_" / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    sp = FakeSpotify(matches={"Africa": (f"spotify:track:{SP_MATCH}", "Africa", "Toto", 295000)})
    eng = Engine([sp], None, addons_dir=addons, prewarm_async=False, log=lambda m: None)
    eng.handle(p2.build_frame(ET.ARM, seq=1, **AFRICA), "pixel")
    eng.handle(p2.build_frame(ET.ARM, seq=2, drop_id="zone:1", artist="Nobody", title="Nothing"), "pixel")
    gd.flush(addons)
    data = gd.read_companion_data(addons)
    assert data["tracks"]["q:toto\x1fafrica"] == {"spotify": SP_MATCH, "durationMs": 295000, "title": "Africa",
                                                   "artist": "Toto"}
    assert data["tracks"]["q:nobody\x1fnothing"] == {"notFound": True}
    written = {p.relative_to(addons).as_posix() for p in addons.rglob("*") if p.is_file()}
    assert written == {"NeedleDropper_Companion/Data.lua", "NeedleDropper_Companion/NeedleDropper_Companion.toc"}


def test_recorder_errors_never_stop_playback():
    def boom(key: str, info: dict) -> None:
        raise OSError("disk full")

    sp = FakeSpotify(matches={"Africa": (f"spotify:track:{SP_MATCH}", "Africa", "Toto", 295000)})
    logs: list[str] = []
    eng = Engine([sp], None, track_recorder=boom, prewarm_async=False, log=logs.append)
    eng.handle(p2.build_frame(ET.FIRE, seq=1, flags=Flags.DROP_ACTIVE, **AFRICA), "pixel")
    assert eng.is_playing and any("could not record" in m for m in logs)


def test_callback_errors_are_contained():
    def bad(*a: Any) -> None:
        raise RuntimeError("shell bug")

    sp = FakeSpotify()
    logs: list[str] = []
    eng = Engine([sp], None, on_status=bad, on_link_check=bad, prewarm_async=False, log=logs.append)
    eng.handle(p2.build_frame(ET.FIRE, seq=1, flags=Flags.DROP_ACTIVE | Flags.TEST, spotify=SP_ID, **AFRICA), "x")
    assert eng.is_playing and any("callback failed" in m for m in logs)


def test_prewarm_songs_searches_ahead_and_records():
    r = Rig()
    n = r.engine.prewarm_songs([("Toto", "Africa"), ("Nobody", "Nothing"), ("", "draft"), ("Toto", "Africa")])
    assert n == 2
    keys = [k for k, _i in r.records]
    assert keys == ["q:toto\x1fafrica", "q:nobody\x1fnothing"]
    assert r.engine.prewarm_songs([("Toto", "Africa")]) == 0  # already searched this session
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, **AFRICA)
    assert len(r.sp.of("resolve")) == 2 and r.sp.of("play")[0][2] == f"spotify:track:{SP_MATCH}"


def test_prewarm_songs_needs_spotify():
    r = Rig(spotify=FakeSpotify(avail=False))
    assert r.engine.prewarm_songs([("Toto", "Africa")]) == 0


# ------------------------------------------------------------------- misc --

def test_describe_event_handles_both_protocols():
    fr = p2.build_frame(ET.FIRE, seq=3, flags=Flags.DROP_ACTIVE, **AFRICA)
    assert describe_event(fr, "pixel").startswith("FIRE seq=3") and describe_event(fr, "pixel").endswith("via pixel")
    assert describe_event(Event(ET.HELLO, 1, 0, 5), "chatlog").startswith("HELLO seq=1 n=0 hash=5")


def test_status_snapshot_is_complete():
    r = Rig()
    r.send(ET.ARM, youtube=YT_ID, **AFRICA)
    st = r.engine.status()
    assert st["armed"] == ["area:87"] and st["spotifySearches"] == {"matched": 1, "notFound": 0, "pending": 0}
    assert st["providers"] == ["youtube", "spotify"] and st["addon"]["hash"] is None
    assert r.changes("addon")[0]["protocol"] == 2
    import json
    json.dumps(st)


def test_threaded_engine_with_async_prewarm_starts_and_stops_cleanly():
    sp = FakeSpotify(matches={"Africa": (f"spotify:track:{SP_MATCH}", "Africa", "Toto", 295000)})
    eng = Engine([sp], None, log=lambda m: None)
    eng.start()
    try:
        eng.submit(p2.build_frame(ET.ARM, seq=1, **AFRICA), "pixel")
        eng.submit(p2.build_frame(ET.FIRE, seq=2, flags=Flags.DROP_ACTIVE, **AFRICA), "pixel")
        deadline = time.monotonic() + 3
        while not sp.of("play") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sp.of("play")[0][2] == f"spotify:track:{SP_MATCH}"
        assert len(sp.of("resolve")) == 1
    finally:
        eng.stop()
    assert not eng.status()["running"] and sp.of("stop")


def test_a_restarted_engine_prewarms_and_plays_without_stalling():
    """stop() before the first ARM, then start() again: the pre-warm worker must still run (a stale
    stop marker would orphan the job and make every FIRE wait the full fire_wait_sec)."""
    sp = FakeSpotify(matches={"Africa": (f"spotify:track:{SP_MATCH}", "Africa", "Toto", 295000)})
    eng = Engine([sp], None, log=lambda m: None)
    eng.start()
    eng.stop()
    eng.start()
    try:
        eng.submit(p2.build_frame(ET.ARM, seq=1, **AFRICA), "pixel")
        deadline = time.monotonic() + 3
        while not sp.of("prepare") and time.monotonic() < deadline:  # the worker searched, then woke Spotify
            time.sleep(0.01)
        assert sp.of("resolve") and sp.of("prepare") and eng.wait_prewarm(3)
        t0 = time.monotonic()
        eng.submit(p2.build_frame(ET.FIRE, seq=2, flags=Flags.DROP_ACTIVE, **AFRICA), "pixel")
        deadline = time.monotonic() + 3
        while not sp.of("play") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sp.of("play") and time.monotonic() - t0 < 1.5
        assert len(sp.of("resolve")) == 1
    finally:
        eng.stop()


def test_logger_object_is_accepted(caplog):
    sp = FakeSpotify()
    eng = Engine([sp], None, log=logging.getLogger("t.engine2"), prewarm_async=False)
    with caplog.at_level(logging.INFO, logger="t.engine2"):
        eng.handle(p2.build_frame(ET.PING, seq=5, flags=Flags.TEST), "pixel")
    assert "PING seq=5 via pixel" in caplog.text
