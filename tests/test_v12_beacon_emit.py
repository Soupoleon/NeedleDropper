"""Emit.lua (CONTRACTS-v1.2 2.8): ns.BuildEvent builds the Protocol2 event from a map drop, and
ns.Emit keeps v1's signature and return value -- seq bumped and persisted in ns.db.lastSeq, live
flags computed like v1's Director.ComputeFlags, ns.Events / ns.LastEvent kept -- and hands the event
to ns.Beacon.Show and ns.ChatLink.Send.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import LuaErrors  # noqa: E402
from v12_beacon_harness import BeaconHarness  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
YT_ID = "dQw4w9WgXcQ"
ZONE = {"n": 3, "id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn (pack label)",
        "title": "I'll Keep Coming", "artist": "Low Roar", "spotify": SPOTIFY_ID, "youtube": YT_ID,
        "startMs": 12340, "durationMs": 95000, "volume": 80, "setId": "azeroth-mixtape",
        "setRef": "builtin:azeroth-mixtape", "builtin": True, "note": "not sent", "tags": ["fitting"],
        "file": "elwynn.mp3"}
AREA = {"id": "area:87", "kind": "subzone", "mapID": 1429, "areaID": 87, "subzone": "Goldshire",
        "subzoneLower": "goldshire", "label": "Goldshire (pack)", "title": "Africa", "artist": "Toto"}
SUB = {"id": "sub:1429:lion-s-pride-inn", "kind": "subzone", "mapID": 1429, "subzone": "Lion's Pride Inn",
       "subzoneLower": "lion's pride inn", "label": "Lion's Pride Inn", "title": "T", "artist": "A"}
INST = {"id": "inst:36", "kind": "instance", "instanceID": 36, "label": "The Deadmines", "title": "T", "artist": "A"}


def make(**kw) -> BeaconHarness:
    settings = {"beaconCellPx": 3, "chatlog": True}
    settings.update(kw.pop("settings", {}))
    return BeaconHarness(settings=settings, **kw)


# ------------------------------------------------------------ BuildEvent --

def test_build_event_maps_every_drop_field():
    h = make()
    ev = h.build_event(ET.FIRE, ZONE, int(Flags.TEST))
    assert ev == {"type": 3, "seq": 0, "flags": 0x40, "dropId": "zone:1429", "placeKey": p2.place_key_for("zone:1429"),
                  "spotify": SPOTIFY_ID, "youtube": YT_ID, "artist": "Low Roar", "title": "I'll Keep Coming",
                  "startMs": 12340, "lengthMs": 95000, "volume": 80, "label": "Elwynn Forest",
                  "setId": "azeroth-mixtape", "prefer": "youtube"}
    f = p2.frame_from_event(ev)
    assert (f.query, f.start_ds, f.length_ds) == ("Low Roar\x1fI'll Keep Coming", 123, 950)
    assert ET.ARM == h.build_event(ET.ARM, ZONE)["type"] and h.build_event(ET.ARM, ZONE)["label"] == "Elwynn Forest"


def test_build_event_per_type_fields():
    h = make()
    assert h.build_event(ET.CANCEL, ZONE) == {"type": 4, "seq": 0, "flags": 0, "dropId": "zone:1429",
                                             "placeKey": p2.place_key_for("zone:1429"), "prefer": "youtube"}
    for etype in (ET.HELLO, ET.PING, ET.NONE):
        assert h.build_event(etype, ZONE) == {"type": int(etype), "seq": 0, "flags": 0, "prefer": "youtube"}
    # absent / zero values stay absent: whole song, start at 0, the companion's volume
    ev = h.build_event(ET.FIRE, {"id": "zone:1436", "kind": "zone", "mapID": 1436, "title": "T", "artist": "A",
                                 "durationMs": 0, "startMs": 0})
    assert not {"lengthMs", "startMs", "volume", "spotify", "youtube", "setId"} & set(ev)
    assert h.build_event(ET.FIRE, {"id": "zone:1", "volume": 0})["volume"] == 0
    assert h.build_event(ET.FIRE, None) == {"type": 3, "seq": 0, "flags": 0, "prefer": "youtube"}
    assert h.build_event(ET.FIRE, ZONE, 0, {"seq": 300, "prefer": "spotify", "label": "Here"})["seq"] == 44


def test_test_drop_carries_its_label_but_no_song():
    h = make()
    test_drop = {"n": 0, "id": "test", "kind": "test", "label": "NeedleDropper Test", "title": "NeedleDropper test",
                 "artist": "NeedleDropper", "durationMs": 30000, "startMs": 0}
    ev = h.build_event(ET.FIRE, test_drop, int(Flags.TEST))
    assert ev == {"type": 3, "seq": 0, "flags": 0x40, "dropId": "test", "placeKey": p2.place_key_for("test"),
                  "label": "NeedleDropper Test", "prefer": "youtube"}
    f = p2.frame_from_event(ev)
    assert f.query is None and f.spotify_id is None and f.youtube_id is None and f.length_ds is None


def test_labels_are_localised_by_the_client():
    h = make(before_load=lambda hh: hh.shim.SetLocale("frFR"))
    assert h.build_event(ET.FIRE, ZONE)["label"] == "Forêt d'Elwynn"          # C_Map.GetMapInfo
    assert h.build_event(ET.FIRE, AREA)["label"] == "Comté-de-l'Or"            # ns.AreaName
    h.state.subzone = "Lion's Pride Inn"
    assert h.build_event(ET.FIRE, SUB)["label"] == "Lion's Pride Inn"         # the live subzone text
    h.state.subzone = "Somewhere else"
    assert h.build_event(ET.FIRE, SUB)["label"] == "Lion's Pride Inn"         # the drop's label
    assert h.build_event(ET.FIRE, INST)["label"] == "The Deadmines"           # not inside: the label
    h.state.inInstance, h.state.instanceID, h.state.instanceName = True, 36, "Les Mortemines"
    assert h.build_event(ET.FIRE, INST)["label"] == "Les Mortemines"          # inside: the instance name
    h.state.instanceID = 48
    assert h.build_event(ET.FIRE, INST)["label"] == "The Deadmines"           # another instance
    unknown = {"id": "zone:424242", "kind": "zone", "mapID": 424242, "label": "Old Label", "title": "T", "artist": "A"}
    assert h.build_event(ET.FIRE, unknown)["label"] == "Old Label"
    no_kind = {"id": "zone:1436", "mapID": 1436, "title": "T", "artist": "A"}
    assert h.build_event(ET.FIRE, no_kind)["label"] == "Marche de l'Ouest"   # kind from the id prefix
    assert "label" not in h.build_event(ET.FIRE, {"id": "sub:1:x", "title": "T", "artist": "A"})


def test_secret_place_names_are_never_touched():
    h = make()
    h.lua("local real = C_Map.GetMapInfo; C_Map.GetMapInfo = function(id) "
          "local i = real(id); if i then i.name = SHIM.MakeSecret('map name') end; return i end")
    assert h.build_event(ET.FIRE, ZONE)["label"] == "Elwynn (pack label)"
    assert h.from_lua(h.log.secretTouches) == {}


# ------------------------------------------------------------------ Emit --

def test_emit_bumps_and_persists_seq_and_wraps():
    h = make(db={"lastSeq": 253})
    seqs = [h.emit(t, ZONE)["seq"] for t in (ET.HELLO, ET.ARM, ET.FIRE, ET.CANCEL)]
    assert seqs == [254, 255, 0, 1]
    assert h.account_db()["lastSeq"] == 1
    assert [e["seq"] for e in h.events()] == seqs and h.from_lua(h.ns.LastEvent)["seq"] == 1


def test_emit_returns_the_event_and_records_history_capped_at_200():
    h = make(settings={"chatlog": False})
    for i in range(205):
        ev = h.emit(ET.HELLO)
    assert ev["type"] == 1 and ev["t"] == pytest.approx(h.now)
    evs = h.events()
    assert len(evs) == 200 and evs[-1]["seq"] == ev["seq"] and evs[0]["seq"] == 6


def test_emit_without_saved_variables_keeps_counting():
    h = make(login=False)
    assert h.ns.db is None
    a = h.emit(ET.HELLO)
    b = h.emit(ET.HELLO)
    assert (a["seq"], b["seq"]) == (1, 2)


def test_live_flags_like_v1():
    h = make()
    assert h.emit(ET.HELLO)["flags"] == 0
    h.state.inCombat, h.state.taxi, h.state.dead = True, True, True
    h.state.inInstance = True
    assert h.emit(ET.HELLO)["flags"] == int(Flags.IN_COMBAT | Flags.IN_INSTANCE | Flags.ON_TAXI | Flags.DEAD)
    h.state.inCombat = h.state.taxi = h.state.dead = h.state.inInstance = False
    h.set_director_active({"isLocal": True, "isTest": True})
    assert h.emit(ET.HELLO)["flags"] == int(Flags.DROP_ACTIVE | Flags.LOCAL_PLAYBACK | Flags.TEST)
    h.set_director_active(None)
    assert h.emit(ET.CANCEL, ZONE, int(Flags.TEST | Flags.LOCAL_PLAYBACK))["flags"] == 0x50
    assert h.emit(ET.HELLO, None, 0x80 | 0x01)["flags"] == 0x01  # unknown bits are not sent
    h.state.inCombat = h.shim.MakeSecret("combat")
    assert h.emit(ET.HELLO)["flags"] == 0  # a secret answer counts as no
    assert h.from_lua(h.log.secretTouches) == {}


def test_emit_reaches_both_transports_with_the_same_event():
    h = make()
    calls = h.rt.execute("""
        local ns = ...
        local log = {}
        local show, send = ns.Beacon.Show, ns.ChatLink.Send
        ns.Beacon.Show = function(ev) log[#log + 1] = { "beacon", ev } return show(ev) end
        ns.ChatLink.Send = function(ev, opts) log[#log + 1] = { "chat", ev, opts } return send(ev, opts) end
        return log
    """, h.ns)
    ev = h.emit(ET.FIRE, ZONE, 0, {"noPad": True, "forceChat": True})
    assert [calls[i][1] for i in range(1, len(calls) + 1)] == ["beacon", "chat"]
    same = h.rt.execute("local log, ns = ... return log[1][2] == log[2][2] and log[2][2] == ns.LastEvent", calls, h.ns)
    assert same is True  # one event table for both transports
    assert calls[2][3].noPad is True and calls[2][3].force is True
    assert p2.parse_chat_line(h.chat_lines()[-1]) == p2.frame_from_event(ev)
    assert h.beacon_decoded() == p2.frame_from_event(ev, max_bytes=p2.capacity(3))


def test_a_failing_transport_does_not_stop_the_other():
    h = make()
    h.rt.execute("local ns = ... ns.Beacon.Show = function() error('beacon broke') end", h.ns)
    with pytest.raises(LuaErrors, match="beacon broke"):
        h.emit(ET.ARM, ZONE)
    assert p2.parse_chat_line(h.chat_lines()[-1]).type == ET.ARM


# ---------------------------------------------------- preferred provider --

@pytest.mark.parametrize("spotify,want", [("connected", "spotify"), (True, "spotify"), ("not-connected", "youtube"),
                                          ("premium-required", "youtube"), ("error", "youtube"), (None, "youtube"),
                                          ({"connected": True}, "spotify"), ({"state": "connected"}, "spotify")])
def test_preferred_provider_from_the_companion_status(spotify, want):
    h = make()
    h.set_companion(spotify)
    assert h.ns.PreferredProvider() == want
    assert h.emit(ET.FIRE, ZONE)["prefer"] == want


def test_preferred_provider_without_companion_module():
    h = make()
    assert h.ns.Companion is None and h.ns.PreferredProvider() == "youtube"
    h.lua('NeedleDropper_CompanionData = { schema = 1, status = { spotify = "connected" } }')
    assert h.ns.PreferredProvider() == "spotify"
    h.lua("NeedleDropper_CompanionData = { status = 'broken' }")
    assert h.ns.PreferredProvider() == "youtube"
    h.rt.execute("local ns = ... ns.Companion = { Status = function() error('no data') end }", h.ns)
    h.lua("NeedleDropper_CompanionData = nil")
    assert h.ns.PreferredProvider() == "youtube"  # an erroring Status() is ignored
