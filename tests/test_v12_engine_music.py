"""engine.py for the music services (DESIGN-v1.2.1 3.3, lead decisions D3 and D4): "Play music with"
orders and limits the routes and the ARM pre-warm; "none" plays nothing; ``noSource`` says why; a
lost FIRE starts late from the steady HELLO (F2); a duplicate frame fills in what the first copy
lacked. Frames go through the real codec (build -> encode -> decode), as a transport delivers them."""
from __future__ import annotations

import pytest

from needledropper import protocol2 as p2
from needledropper import engine as engine_mod
from needledropper.engine import LATE_START_SEC, MUSIC_ORDERS, Engine
from needledropper.models import Drop, EventType as ET, Flags
from needledropper.providers.base import ProviderError

from test_v12_engine_flows import AFRICA, SP_ID, SP_MATCH, YT_ID, FakeSpotify, FakeYouTube, Rig

DROP = {"drop_id": "zone:1411", "artist": "Dolly Parton", "title": "9 to 5", "label": "Durotar", "youtube": YT_ID}
SONG_SP = {"Africa": (f"spotify:track:{SP_MATCH}", "Africa", "TOTO", 295000),
           "9 to 5": (f"spotify:track:{SP_ID}", "9 to 5", "Dolly Parton", 165000)}


def rig(order: str = "spotify-youtube", **kw) -> Rig:
    kw.setdefault("spotify", FakeSpotify(matches=SONG_SP))
    r = Rig(**kw)
    r.engine.set_music_order(order)
    return r


def played(r: Rig) -> list[str]:
    calls = [(c, "spotify") for c in r.sp.calls if c[0] == "play"] + [(c, "youtube") for c in r.yt.calls if c[0] == "play"]
    return [name for _c, name in calls]


# ------------------------------------------------------------------ the order --

def test_orders_are_the_protocol_names():
    from needledropper import config

    assert MUSIC_ORDERS == p2.PREFS_ORDERS == config.MUSIC_ORDERS


@pytest.mark.parametrize("order, first", [("spotify-youtube", "spotify"), ("youtube-spotify", "youtube"),
                                          ("spotify", "spotify"), ("youtube", "youtube")])
def test_the_order_picks_who_plays_a_drop_both_can_play(order, first):
    r = rig(order)
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **DROP)
    assert played(r) == [first]
    assert r.engine.status()["playing"]["provider"] == first and r.engine.status()["musicOrder"] == order


def test_youtube_first_falls_back_to_spotify_and_spotify_only_never_tries_youtube():
    r = rig("youtube-spotify", youtube=FakeYouTube(fail=ProviderError("YouTube error 2")))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **DROP)
    assert [c[0] for c in r.yt.calls if c[0] == "play"] == ["play"] and r.engine.status()["playing"]["provider"] == "spotify"

    r = rig("spotify", spotify=FakeSpotify(fail=ProviderError("Spotify refused")))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **DROP)
    assert r.yt.of("play") == [] and r.engine.status()["playing"]["provider"] is None  # card only


def test_youtube_only_plays_no_search_only_drop_and_says_why():
    r = rig("youtube")
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, **AFRICA)           # artist + title, no YouTube id
    assert r.sp.calls == [] and r.yt.of("play") == []
    no = r.changes("noSource")
    assert no and no[0]["dropId"] == "area:87" and no[0]["reasons"] == []  # no route at all: nothing tried


def test_no_source_lists_the_reasons():
    r = rig(spotify=FakeSpotify(fail=ProviderError("Spotify refused the song")))
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **{**DROP, "youtube": None})
    assert r.changes("noSource")[0]["reasons"] == ["spotify: Spotify refused the song"]
    # a test the app runs names its provider: an unavailable one says so
    r = rig(spotify=FakeSpotify(avail=False))
    r.engine.play_now(Drop(n=0, id="test-spotify", kind="test", title="Africa", artist="Toto",
                           spotify=f"spotify:track:{SP_ID}"), test=True, only="spotify")
    r.engine.drain()
    assert r.changes("noSource")[-1]["reasons"] == ["spotify: not available"]


def test_none_plays_nothing_shows_no_card_and_logs_one_info_line():
    r = rig("none")
    r.send(ET.ARM, **DROP)
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE)                       # the addon strips the song in "none"
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **DROP)
    assert r.sp.calls == [] and r.yt.calls == [] and r.deck.sent == []
    assert not r.engine.is_playing and r.engine.status()["events"]["FIRE"] == 2
    assert sum("Play music with is Neither" in line for line in r.logs) == 2
    assert not any("carries no song" in line for line in r.logs)
    # in-game files still show their card
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE | Flags.LOCAL_PLAYBACK, **DROP)
    assert r.deck.cmds("show") and r.engine.status()["playing"]["local"] is True


def test_switching_to_none_ends_the_song_at_the_next_drop():
    r = rig()
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **DROP)
    assert r.engine.is_playing
    r.engine.set_music_order("none")
    assert r.engine.is_playing                                     # the drop playing now goes on
    r.send(ET.FIRE, flags=Flags.DROP_ACTIVE, **AFRICA)
    assert not r.engine.is_playing and r.sp.of("stop")


def test_unknown_orders_mean_the_default():
    r = rig()
    assert r.engine.set_music_order("loudest-first") == "spotify-youtube"
    assert r.engine.set_music_order(None) == "spotify-youtube"
    assert Engine([], None, music_order="youtube").music_order == "youtube"


def test_tests_from_the_app_ignore_the_order():
    r = rig("none")
    r.engine.play_now(Drop(n=0, id="test-youtube", kind="test", title="Africa", artist="Toto", youtube=YT_ID,
                           durationMs=15000), test=True, only="youtube")
    r.engine.drain()
    assert r.yt.of("play") == [("play", "test-youtube", YT_ID, 0)]


# --------------------------------------------------------------- the pre-warm --

@pytest.mark.parametrize("order, searched, prepared", [
    ("spotify-youtube", True, "spotify"), ("youtube-spotify", True, "youtube"), ("spotify", True, "spotify"),
    ("youtube", False, "youtube"), ("none", False, None)])
def test_the_arm_pre_warm_follows_the_order(order, searched, prepared):
    r = rig(order)
    r.send(ET.ARM, **AFRICA, youtube=YT_ID)
    assert bool(r.sp.of("resolve")) is searched
    who = [name for name, p in (("spotify", r.sp), ("youtube", r.yt)) if p.of("prepare")]
    assert who == ([prepared] if prepared else [])


def test_youtube_first_preloads_youtube_before_the_search():
    r = rig("youtube-spotify")
    order: list[str] = []
    r.sp.resolve_drop = (lambda orig: (lambda d: (order.append("search"), orig(d))[1]))(r.sp.resolve_drop)
    r.yt.prepare = lambda d: order.append("preload")
    r.send(ET.ARM, **AFRICA, youtube=YT_ID)
    assert order == ["preload", "search"]


def test_the_savedvariables_pre_search_respects_the_order():
    r = rig("youtube")
    assert r.engine.prewarm_songs([("Toto", "Africa")]) == 0 and r.sp.calls == []
    r.engine.set_music_order("spotify-youtube")
    assert r.engine.prewarm_songs([("Toto", "Africa")]) == 1


# ------------------------------------------------------------ D3: late start --

def arm_then_lost_fire(r: Rig, *, arm_seq: int = 2, hello_seq: int = 3, flags: Flags = Flags.DROP_ACTIVE):
    r.send(ET.ARM, seq=arm_seq, spotify=SP_ID, **DROP)
    r.send(ET.ARM, seq=arm_seq, place_key=p2.place_key_for(DROP["drop_id"]))  # the placeKey-only re-show
    # the FIRE (seq 3) was covered; the steady HELLO after it carries DROP_ACTIVE and the placeKey
    return r.send(ET.HELLO, seq=hello_seq, flags=flags, place_key=p2.place_key_for(DROP["drop_id"]))


def test_a_hello_with_drop_active_and_the_armed_place_starts_the_song_late():
    r = rig()
    arm_then_lost_fire(r)
    assert r.sp.of("play") == [("play", "zone:1411", f"spotify:track:{SP_ID}", 0, 0, None)]
    assert any("never arrived" in line for line in r.logs)
    st = r.engine.status()
    assert st["playing"]["id"] == "zone:1411" and st["armed"] == []
    # the real FIRE (the chat log's copy) arrives later: a duplicate, the song is not restarted
    r.send(ET.FIRE, seq=3, flags=Flags.DROP_ACTIVE, spotify=SP_ID, **DROP)
    r.send(ET.FIRE, seq=3, flags=Flags.DROP_ACTIVE | Flags.IN_COMBAT, spotify=SP_ID, **DROP)
    assert len(r.sp.of("play")) == 1 and r.sp.of("stop") == []
    assert r.engine.status()["duplicates"] >= 2


def test_no_late_start_without_drop_active_or_with_something_playing_or_an_old_hello():
    r = rig()
    arm_then_lost_fire(r, flags=Flags.NONE)
    assert r.sp.of("play") == []
    r = rig()
    arm_then_lost_fire(r, arm_seq=7, hello_seq=6)                # a HELLO from before the ARM
    assert r.sp.of("play") == []
    r = rig()
    r.send(ET.FIRE, seq=1, flags=Flags.DROP_ACTIVE, **AFRICA)    # something else plays
    arm_then_lost_fire(r)
    assert [c[1] for c in r.sp.of("play")] == ["area:87"]


def test_no_late_start_after_a_cancel_or_from_a_stale_arm():
    r = rig()
    r.send(ET.ARM, seq=2, spotify=SP_ID, **DROP)
    r.send(ET.CANCEL, seq=3, drop_id=DROP["drop_id"])
    r.send(ET.ARM, seq=2, spotify=SP_ID, transport="chatlog", **DROP)  # a late chat-log copy of the old ARM ...
    r.clock.advance(16)                                                  # ... after the dedupe window
    r.send(ET.ARM, seq=2, spotify=SP_ID, transport="chatlog", **DROP)
    r.send(ET.HELLO, seq=4, flags=Flags.DROP_ACTIVE, place_key=p2.place_key_for(DROP["drop_id"]))
    assert r.sp.of("play") == []

    # v1.2.1 review RP-2: the ARM is judged by sequence, not by its age (the Director fires minutes after it
    # arms, e.g. during the 10-minute cooldown): only an ARM the cache forgot (ARM_TTL_SEC) is too old
    r = rig()
    r.send(ET.ARM, seq=2, spotify=SP_ID, **DROP)
    r.clock.advance(engine_mod.ARM_TTL_SEC + 1)                  # forgotten: it can't stand in for a FIRE
    r.send(ET.HELLO, seq=3, flags=Flags.DROP_ACTIVE, place_key=p2.place_key_for(DROP["drop_id"]))
    assert r.sp.of("play") == []
    r = rig()
    r.send(ET.ARM, seq=2, spotify=SP_ID, **DROP)
    r.clock.advance(LATE_START_SEC + 1)                          # older than 30 s, still the armed place
    r.send(ET.HELLO, seq=3, flags=Flags.DROP_ACTIVE, place_key=p2.place_key_for(DROP["drop_id"]))
    assert len(r.sp.of("play")) == 1


def test_a_late_start_keeps_local_and_test_flags_of_the_arm():
    r = rig()
    r.send(ET.ARM, seq=2, flags=Flags.LOCAL_PLAYBACK, **DROP)
    r.send(ET.HELLO, seq=3, flags=Flags.DROP_ACTIVE, place_key=p2.place_key_for(DROP["drop_id"]))
    st = r.engine.status()["playing"]
    assert st["local"] is True and r.sp.calls == [] and r.yt.of("play") == []


def test_none_never_starts_late():
    r = rig("none")
    arm_then_lost_fire(r)
    assert r.sp.calls == [] and r.yt.calls == [] and not r.engine.is_playing


DUROTAR = {"id": "zone:1411", "kind": "zone", "mapID": 1411, "label": "Durotar", "title": "9 to 5",
           "artist": "Dolly Parton", "durationMs": 165547, "startMs": 0, "youtube": "d_vPJjqWxZM"}


@pytest.mark.parametrize("cover", [(-0.5, 5.0), (0.0, 3.5), (0.25, 6.0)])
def test_a_fire_covered_on_screen_starts_late_from_the_beacons_steady_hello(cover):
    """F2 with the real Beacon.lua (lupa), 4 Hz captures and the transport's two-identical-reads rule:
    a toast hides the strip for the FIRE's whole 3 s hold (start, length relative to the FIRE)."""
    pytest.importorskip("lupa.lua51")
    from needledropper.transport.pixel import PixelTransport
    from v12_beacon_harness import BeaconHarness

    class NoBackend:
        def available(self):
            return True, ""

        def prepare(self):
            return "test"

    h = BeaconHarness(settings={"beaconCellPx": 3})
    r = rig()
    emitted: list = []
    tr = PixelTransport({"enabled": True}, None, lambda e, n: emitted.append(e), backend=NoBackend())
    t0, covers = h.now, []

    def run(seconds: float) -> None:
        end = h.now + seconds
        while h.now < end - 1e-9:
            h.advance(0.25)
            r.clock.t = 1000.0 + (h.now - t0)
            r.engine.tick()
            hidden = any(a <= h.now < b for a, b in covers)
            _out, ev = tr.feed(None if hidden else h.beacon_decoded())
            if ev is not None:
                r.engine.handle(ev, "pixel")

    h.set_director_active(None)
    h.emit(int(ET.HELLO), None, 0)
    run(2.0)
    h.emit(int(ET.ARM), DUROTAR, 0)
    run(5.0)
    start, length = cover
    covers.append((h.now + start, h.now + start + length))
    h.set_director_active({"isLocal": False, "isTest": False})
    h.emit(int(ET.FIRE), DUROTAR, 0)
    run(10.0)
    types = [e.type for e in emitted]
    assert ET.FIRE not in types                                    # the covered FIRE never reached the app
    hellos = [e for e in emitted if e.type == ET.HELLO and e.has(Flags.DROP_ACTIVE)]
    assert hellos and hellos[-1].place_key == p2.place_key_for("zone:1411")
    plays = r.sp.of("play") + r.yt.of("play")
    assert [p[1] for p in plays] == ["zone:1411"]                  # started late, once (Spotify's search match)
    assert any("never arrived" in line for line in r.logs)


# ------------------------------------------------- D4: duplicates add fields --

def test_a_duplicate_arm_adds_the_query_the_fitted_beacon_arm_left_out():
    r = rig()
    r.send(ET.ARM, seq=5, drop_id="zone:1411", youtube=YT_ID, label="Durotar")       # the one-row beacon ARM
    r.send(ET.ARM, seq=5, transport="chatlog", **DROP)                                # the full chat line
    assert r.sp.of("resolve") == []                                                   # no pre-warm search
    r.send(ET.FIRE, seq=6, flags=Flags.DROP_ACTIVE, drop_id="zone:1411", youtube=YT_ID)
    # the FIRE took the query from the merged ARM: Spotify searched it at FIRE and played the match
    assert r.sp.of("play") and r.sp.of("play")[0][2] == f"spotify:track:{SP_ID}"


def test_a_duplicate_fire_fills_the_title_card_and_never_switches_the_song():
    r = rig("youtube-spotify")
    r.send(ET.FIRE, seq=8, flags=Flags.DROP_ACTIVE, drop_id="zone:1411", youtube=YT_ID)   # fitted: no query
    assert r.deck.cmds("show")[-1]["title"] == ""
    r.send(ET.FIRE, seq=8, flags=Flags.DROP_ACTIVE, transport="chatlog", **DROP)
    card = r.deck.cmds("show")[-1]
    assert (card["title"], card["artist"]) == ("9 to 5", "Dolly Parton") and card["provider"] == "youtube"
    assert len(r.yt.of("play")) == 1 and r.sp.calls == []


def test_a_duplicate_fire_plays_a_drop_nothing_could_play_yet():
    r = rig(spotify=FakeSpotify(avail=False))
    r.send(ET.FIRE, seq=9, flags=Flags.DROP_ACTIVE, drop_id="zone:1411", spotify=SP_ID)  # fitted: Spotify id only
    assert r.engine.status()["playing"]["provider"] is None                              # card only
    r.send(ET.FIRE, seq=9, flags=Flags.DROP_ACTIVE, transport="chatlog", spotify=SP_ID, **DROP)
    assert r.yt.of("play") == [("play", "zone:1411", YT_ID, 0)]


def test_a_duplicate_of_another_song_is_not_merged():
    r = rig()
    r.send(ET.ARM, seq=5, drop_id="zone:1411", youtube=YT_ID)
    r.send(ET.ARM, seq=5, transport="chatlog", **{**DROP, "youtube": "AAAAAAAAAAA"})
    r.send(ET.FIRE, seq=6, flags=Flags.DROP_ACTIVE, drop_id="zone:1411", youtube=YT_ID)
    assert r.sp.calls == []                                       # no query was taken from the other song
