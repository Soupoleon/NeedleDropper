"""End-to-end (addon side, v1.2): a fresh troll in Durotar, the REAL addon (every file in TOC order,
the real Sets\\*.lua), a soundtrack of the player's own made through ns.Soundtracks plus the
default Azeroth Mixtape, and the companion's real decoders -- no map anywhere:

    Soundtracks.Create / AddDrop -> ns.Map (merge) -> Location -> Director -> Emit -> Beacon v2
    textures -> synthetic BGRA capture of the client's top-left corner ->
    needledropper.transport.beacon2.locate_and_decode -> protocol2.Frame (the song itself)

and the same events as NDROP2 chat lines -> protocol2.parse_chat_line. The route is the one the
owner tested on the real client on 2026-11-05 (docs/review/real-client-2026-11-05.md): Durotar
(Mixtape's zone drop), the Razor Hill landmark (the player's own drop, area:362) and Orgrimmar
(Mixtape). The companion engine has its own end-to-end test (tests/test_integration_e2e.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import AddonHarness  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402
from needledropper.transport import beacon2  # noqa: E402

SPOTIFY = "2tSQF1nD4ZmexGL9Ac7l1y"
YOUTUBE = "GNtOjE5rbK0"
RAZOR_HILL = {"mapID": 1411, "areaID": 362, "subzone": "Razor Hill", "artist": "ZZ Top", "title": "Sharp Dressed Man",
              "spotify": "https://open.spotify.com/intl-de/track/" + SPOTIFY + "?si=abc123",
              "youtube": "https://youtu.be/" + YOUTUBE + "?t=12", "startMs": 12000, "durationMs": 120000,
              "volume": 80}
# The owner's route ran with the NeedleDropper app (it wrote NeedleDropper_Companion): without it a
# first-visit drop only shows its title card and is marked "card", not "played" (journey F4).
# 1.2.1: an app older than 1.2.1 counts as outdated (it reads no 1.2.1 frame, DESIGN-v1.2.1 2.11)
APP = {"schema": 1, "appVersion": "1.2.1", "written": 1762365000, "status": {"youtube": True}}


def fresh_troll(**kw) -> AddonHarness:
    """First login of a level-1 troll in the Valley of Trials (no SavedVariables at all)."""
    state = {"level": 1}
    state.update(kw.pop("state", {}))
    return AddonHarness(location=(1411, "Valley of Trials"), state=state, **kw)


def advance_until(h: AddonHarness, etype: ET, drop_id: str, limit: float = 30.0, dt: float = 0.25) -> dict:
    """Advance the fake clock until the addon emits event `etype` for `drop_id`; stop right there
    (so a FIRE is still on the strip, inside its 3 s hold)."""
    seen = h.mark()
    t = 0.0
    while t < limit:
        h.advance(dt)
        t += dt
        for ev in h.events_since(seen):
            if ev["type"] == int(etype) and ev.get("dropId") == drop_id:
                return ev
    raise AssertionError(f"no {etype.name} for {drop_id} within {limit} s: {h.events_since(seen)}")


def captured(h: AddonHarness, *, scale: float = 1.0, seed: int = 0) -> p2.Frame:
    """What the companion's pixel transport reads: a capture of the top-left 512 x 48 px decoded by
    transport.beacon2.locate_and_decode."""
    buf, w, hh, stride = h.beacon_bgra(scale=scale, width=int(512 * scale), height=int(48 * scale), seed=seed)
    res = beacon2.locate_and_decode(buf, w, hh, stride)
    assert res is not None, "the companion did not find the beacon"
    return res.frame


@pytest.fixture
def trip():
    h = fresh_troll(settings=None, companion_data=APP)
    h.slash("set cooldownMin 0")                 # back-to-back drops for the route
    ref = h.call1("Soundtracks.Create", "Durotar Road Trip")
    assert ref == "user:durotar-road-trip"
    assert h.call1("Soundtracks.AddDrop", ref, RAZOR_HILL) == "area:362"
    h.rebuild()
    return h, ref


def test_the_route_reaches_the_companion_with_the_song_in_every_frame(trip):
    h, ref = trip
    # The soundtrack of your own is on, above the default (DESIGN-v1.2 4.2: index 1 wins).
    assert h.active() == [ref, "builtin:azeroth-mixtape"]
    assert h.map_drop("area:362")["setRef"] == ref and h.map_drop("zone:1411")["builtin"] is True

    # 1. Durotar: Mixtape's zone drop (a new character: no exploration history at all).
    fire = advance_until(h, ET.FIRE, "zone:1411")
    got = captured(h)
    assert (got.type, got.drop_id, got.seq) == (ET.FIRE, "zone:1411", fire["seq"])
    assert (got.artist, got.title) == ("Dolly Parton", "9 to 5")
    assert got.youtube_id == "d_vPJjqWxZM" and got.length_ms == 165500  # durationMs 165547, in deciseconds
    assert got.label == "Durotar" and got.has(Flags.DROP_ACTIVE) and not got.has(Flags.TEST)
    assert not got.has(Flags.DEAD)
    h.advance(166)                                # the song ends: HELLO

    # 2. Razor Hill: the player's own drop, every song field, as the owner's real-client run did.
    h.set_location(1411, "Razor Hill", settle=False)
    arm = advance_until(h, ET.ARM, "area:362", limit=2)
    got = captured(h, scale=1.25, seed=3)        # the ARM's full song, at 125 % Windows scaling
    assert (got.type, got.drop_id, got.seq) == (ET.ARM, "area:362", arm["seq"])
    assert (got.spotify_id, got.youtube_id) == (SPOTIFY, YOUTUBE)
    assert (got.artist, got.title, got.start_ms, got.length_ms, got.volume) == (
        "ZZ Top", "Sharp Dressed Man", 12000, 120000, 80)
    fire = advance_until(h, ET.FIRE, "area:362", limit=6)     # landmark dwell 4 s
    got = captured(h, seed=5)
    assert (got.type, got.drop_id, got.place_key) == (ET.FIRE, "area:362", p2.place_key_for("area:362"))
    assert (got.spotify_id, got.youtube_id, got.artist, got.title) == (SPOTIFY, YOUTUBE, "ZZ Top", "Sharp Dressed Man")
    assert (got.start_ms, got.length_ms, got.volume, got.label) == (12000, 120000, 80, "Razor Hill")
    # The beacon fits the frame to 3 rows of 3 px (DESIGN-v1.2 2.3): the soundtrack id goes first.
    assert got.set_id is None and got == p2.frame_from_event(fire, max_bytes=p2.capacity(3))
    st = h.beacon_status()
    assert st["rows"] <= 3 and st["heightPx"] <= 9 and st["cellPx"] == 3
    h.advance(121)

    # 3. Orgrimmar: Mixtape's capital.
    h.set_location(1454, "", settle=False)
    fire = advance_until(h, ET.FIRE, "zone:1454")
    got = captured(h, scale=1.5, seed=9)
    assert (got.drop_id, got.label) == ("zone:1454", "Orgrimmar") and got.youtube_id and got.artist and got.title
    mix = h.map_drop("zone:1454")
    assert (got.artist, got.title) == (mix["artist"], mix["title"])

    # Heard once each, journaled with the song itself (DESIGN-v1.2 4.6).
    cdb = h.char_db()
    assert {k: v["how"] for k, v in cdb["done"].items()} == {"zone:1411": "played", "area:362": "played",
                                                             "zone:1454": "played"}
    razor = next(e for e in cdb["journal"] if e["id"] == "area:362")
    assert (razor["spotify"], razor["youtube"], razor["setId"]) == (SPOTIFY, YOUTUBE, "durotar-road-trip")


def test_the_chat_log_carries_the_same_events_with_every_field(trip):
    h, ref = trip
    h.slash("chatlog on")
    h.set_location(1411, "Razor Hill", settle=False)
    fire = advance_until(h, ET.FIRE, "area:362", limit=6)
    frames = h.chat_frames()
    chat_fire = next(f for f in frames if f.type == ET.FIRE and f.seq == fire["seq"])
    assert chat_fire == p2.frame_from_event(fire)            # the whole frame: the setId is there too
    assert chat_fire.set_id == "durotar-road-trip" and chat_fire.label == "Razor Hill"
    assert (chat_fire.spotify_id, chat_fire.youtube_id) == (SPOTIFY, YOUTUBE)
    assert all(e["hidden"] for e in h.chat_entries())          # never shown in the chat window


def test_an_edit_in_game_reaches_the_companion_without_a_reload(trip):
    h, ref = trip
    h.set_location(1411, "Razor Hill", settle=False)
    advance_until(h, ET.ARM, "area:362", limit=2)
    h.advance(1.0)                                 # the CANCEL of the Durotar zone drop is off the strip
    # Change the song while the drop is pending: the old ARM is cancelled, the new song armed.
    assert h.call("Soundtracks.UpdateDrop", ref, "area:362", {"artist": "Toto", "title": "Africa",
                                                              "spotify": False, "youtube": "QAo_Ycocl1E"})[0] is True
    mark = h.mark()
    h.rebuild()
    evs = h.events_since(mark)
    assert [e["type"] for e in evs][:2] == [ET.CANCEL, ET.ARM]
    cancel = captured(h)                           # the CANCEL holds the strip for 1 s first
    assert (cancel.type, cancel.drop_id) == (ET.CANCEL, "area:362")
    h.advance(1.1)
    got = captured(h)                              # then the new ARM's full song
    assert (got.type, got.artist, got.title, got.spotify_id, got.youtube_id) == (
        ET.ARM, "Toto", "Africa", None, "QAo_Ycocl1E")
    fire = advance_until(h, ET.FIRE, "area:362", limit=6)
    assert captured(h).seq == fire["seq"] and fire["title"] == "Africa"
