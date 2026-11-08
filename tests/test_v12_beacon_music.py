"""v1.2.1 Music services on the wire, addon side (DESIGN-v1.2.1 2.7, 2.8, 5.2 items 4 and 5; decision D3):

* Beacon queue B: CONFIG holds (3 s with an ACTION, 2 s PREFS only), a FIRE or an ARM takes the screen from a
  CONFIG at once and the CONFIG comes back with identical bytes and its full hold, no CONFIG during an ARM's
  song window, the re-show 8 s after an ACTION CONFIG's hold, the overflow order, ForceShow with the beacon
  off, PREFS on every drawn HELLO (B.SetPrefs) and the strips through the companion's screen decoder;
* D3: the HELLO drawn while a drop plays carries that drop's placeKey;
* ChatLink: a request CONFIG is padded and a PREFS-only one is not; requests older than 20 s are dropped
  from the lockdown queue; HELLO lines carry PREFS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import AddonHarness, make_set  # noqa: E402
from v12_beacon_harness import BeaconHarness  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

DROP_A = {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest", "title": "I'll Keep Coming",
          "artist": "Low Roar", "durationMs": 60000, "youtube": "mlM66FzjpgM"}
DROP_B = {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall", "title": "Old Pine",
          "artist": "Ben Howard", "durationMs": 30000, "spotify": "4uLU6hMCjMI75M1A2tKUQC"}
PREFS = {"order": "youtube-spotify", "corner": "topleft", "fade": False, "resume": True}
CID = "0123456789abcdef0123456789abcdef"
T0 = 1762365600


def make(**settings) -> BeaconHarness:
    s = {"beaconCellPx": 3, "chatlog": True}
    s.update(settings)
    return BeaconHarness(settings=s)


def shown(h) -> p2.Frame:
    f = h.beacon_decoded()
    assert f is not None, h.status()
    return f


def full(ev: dict) -> p2.Frame:
    return p2.frame_from_event(ev, max_bytes=p2.capacity(3))


def request(h, name="test-spotify", at=T0, client=None, prefs=PREFS) -> dict:
    opts = {"action": {"name": name, "at": at}}
    if client:
        opts["clientId"] = client
    elif prefs:
        opts["prefs"] = prefs
    return h.emit(ET.CONFIG, None, 0, opts)


def prefs_only(h, prefs=PREFS) -> dict:
    return h.emit(ET.CONFIG, None, 0, {"prefs": prefs})


# ------------------------------------------------------------------ holds --

def test_a_request_holds_3_seconds_and_prefs_only_2():
    h = make()
    h.emit(ET.HELLO)
    idle = shown(h)
    cfg = request(h)
    assert shown(h) == full(cfg) and shown(h).action.name == "test-spotify"
    st = h.status()
    assert st["variant"] == "config" and st["configShown"] is True
    h.advance(2.95)
    assert shown(h).type == ET.CONFIG
    h.advance(0.1)
    assert shown(h) == idle                                   # back to the steady HELLO
    p = prefs_only(h)
    assert shown(h) == full(p) and shown(h).action is None
    h.advance(1.95)
    assert shown(h).type == ET.CONFIG
    h.advance(0.1)
    assert shown(h) == idle
    assert [e["type"] for e in h.events()] == [ET.HELLO, ET.CONFIG, ET.CONFIG]   # CONFIG never becomes steady


def test_a_fire_takes_the_screen_and_the_config_returns_unchanged():
    h = make()
    h.emit(ET.HELLO)
    cfg = request(h)
    h.advance(1.0)
    fire = h.emit(ET.FIRE, DROP_A)
    assert shown(h) == full(fire)                             # in the same Pump: FIRE latency unchanged
    assert h.status()["configQueued"] == 1
    h.advance(2.95)
    assert shown(h) == full(fire)                             # the FIRE keeps its 3 s
    h.advance(0.1)
    back = shown(h)
    assert back == full(cfg) and p2.encode_frame(back) == p2.encode_frame(full(cfg))   # identical bytes
    h.advance(2.9)
    assert shown(h).type == ET.CONFIG                          # its full hold again, not the 2 s it had left
    h.advance(0.2)
    after = shown(h)
    assert after.type == ET.HELLO and after.has(Flags.DROP_ACTIVE)


def test_cancel_and_ping_also_preempt_a_config():
    h = make()
    h.emit(ET.HELLO)
    request(h)
    h.advance(0.5)
    ping = h.emit(ET.PING, None, int(Flags.TEST))
    assert shown(h) == full(ping)
    h.advance(1.05)
    assert shown(h).type == ET.CONFIG


def test_no_config_during_an_arms_song_window():
    h = make()
    arm = h.emit(ET.ARM, DROP_A)
    cfg = request(h)
    assert shown(h) == full(arm)                              # the ARM's full song keeps the screen
    h.advance(2.95)
    assert shown(h) == full(arm)
    h.advance(0.1)
    assert shown(h) == full(cfg)                              # then the CONFIG
    h.advance(3.0)
    short = shown(h)
    assert short.type == ET.ARM and short.query is None and short.key == full(arm).key


def test_an_arm_arriving_while_a_config_shows_takes_the_screen():
    h = make()
    h.emit(ET.HELLO)
    cfg = request(h)
    h.advance(1.0)
    arm = h.emit(ET.ARM, DROP_B)
    assert shown(h) == full(arm)
    h.advance(3.05)
    assert shown(h) == full(cfg)                              # back, with its full hold
    h.advance(2.9)
    assert shown(h).type == ET.CONFIG
    h.advance(0.2)
    assert shown(h).type == ET.ARM and shown(h).query is None


def test_a_request_is_shown_once_more_8_seconds_after_its_hold():
    h = make()
    h.emit(ET.HELLO)
    cfg = request(h)
    h.advance(3.05)                                           # hold over at +3.0
    assert shown(h).type == ET.HELLO and h.status()["configReshows"] == 1
    h.advance(7.9)                                            # +10.95
    assert shown(h).type == ET.HELLO
    h.advance(0.1)                                            # +11.05: shown again
    again = shown(h)
    assert again == full(cfg) and p2.encode_frame(again) == p2.encode_frame(full(cfg))
    h.advance(1.9)
    assert shown(h).type == ET.CONFIG                         # for 2 s
    h.advance(0.2)
    assert shown(h).type == ET.HELLO
    h.advance(20)
    assert shown(h).type == ET.HELLO and h.status()["configReshows"] == 0   # once only
    assert [e["type"] for e in h.events()].count(ET.CONFIG) == 1           # the re-show is not emitted


def test_prefs_only_configs_are_not_shown_again():
    h = make()
    h.emit(ET.HELLO)
    prefs_only(h)
    h.advance(2.05)
    assert h.status()["configReshows"] == 0
    h.advance(10)
    assert shown(h).type == ET.HELLO


def test_queue_b_holds_four_and_drops_prefs_only_ones_first():
    h = make()
    h.emit(ET.HELLO)
    first = request(h, "test-spotify", T0)                     # on screen
    p1 = prefs_only(h)
    r2 = request(h, "test-youtube", T0 + 1)
    p2_ = prefs_only(h, dict(PREFS, fade=True))
    r3 = request(h, "youtube-sign-in", T0 + 2)
    r4 = request(h, "connect-spotify", T0 + 3, client=CID)     # five waiting: the oldest PREFS-only goes
    st = h.status()
    assert st["configQueued"] == 4 and st["configDropped"] == 1
    seen = []
    for _ in range(32):                                       # 3 + 3 + 2 + 3 + 3 s of holds
        f = shown(h)
        if f.type == ET.CONFIG and (not seen or seen[-1] != f):
            seen.append(f)
        h.advance(0.5)
    assert seen[0] == full(first)
    assert seen[1:5] == [full(r2), full(p2_), full(r3), full(r4)]
    assert full(p1) not in seen


def test_with_the_beacon_off_a_request_is_shown_anyway():
    h = make(beacon=False)
    h.emit(ET.HELLO)
    assert not h.beacon_shown()
    prefs_only(h)
    assert not h.beacon_shown()                               # PREFS-only: not forced
    h.advance(2.1)
    request(h)
    assert h.beacon_shown()                                   # forced for the hold + 0.5 s
    h.advance(3.4)
    assert h.beacon_shown()
    h.advance(0.2)
    assert not h.beacon_shown()


# ------------------------------------------------------------ PREFS, D3 --

def test_every_drawn_hello_carries_the_prefs_and_changes_at_once():
    h = make()
    h.emit(ET.HELLO)
    assert shown(h).prefs is None and h.status()["size"] == "96x3"
    h.ns.Beacon.SetPrefs(h.table(PREFS))
    assert shown(h).prefs == p2.Prefs("youtube-spotify", "topleft", False, True)
    assert h.status()["size"] == "144x3" and h.status()["current"]["prefs"] == PREFS
    fire = h.emit(ET.FIRE, DROP_A)
    h.advance(3.05)
    playing = shown(h)                                        # the synthesized HELLO too
    assert playing.type == ET.HELLO and playing.prefs.order == "youtube-spotify"
    assert playing.place_key == p2.place_key_for(DROP_A["id"]) and playing.seq == fire["seq"]
    assert h.status()["size"] == "192x3"                       # HELLO + placeKey + PREFS: 19 bytes, one row
    h.ns.Beacon.SetPrefs(None)
    assert shown(h).prefs is None and shown(h).place_key == p2.place_key_for(DROP_A["id"])


def test_d3_the_hello_after_a_fire_carries_the_drops_place_key_and_the_arms_key_matches():
    h = make()
    arm = h.emit(ET.ARM, DROP_A)
    fire = h.emit(ET.FIRE, DROP_A)
    h.advance(3.05)
    hello = shown(h)
    assert hello.has(Flags.DROP_ACTIVE) and hello.place_key == full(arm).place_key == full(fire).place_key
    assert hello.drop_id is None and hello.query is None      # HELLO never carries the song
    assert hello.key == (fire["seq"], int(ET.HELLO), p2.place_key_for(DROP_A["id"]), int(hello.flags))


def test_d3_a_cancel_while_another_drop_plays_keeps_the_playing_drops_key():
    h = make()
    h.set_director_active({"isLocal": False, "isTest": False, "drop": DROP_A})
    ev = h.emit(ET.CANCEL, DROP_B)                            # the pending drop B goes; A plays on
    assert ev["flags"] & int(Flags.DROP_ACTIVE)
    h.advance(1.05)
    hello = shown(h)
    assert hello.type == ET.HELLO and hello.place_key == p2.place_key_for(DROP_A["id"])
    h.set_director_active(None)
    h.emit(ET.CANCEL, DROP_A)                                 # nothing plays any more
    h.advance(1.05)
    assert shown(h).place_key is None and not shown(h).has(Flags.DROP_ACTIVE)


def test_d3_an_emitted_hello_carries_the_playing_drops_key_only_with_drop_active():
    h = make()
    h.emit(ET.HELLO)
    assert shown(h).place_key is None
    h.set_director_active({"isLocal": False, "isTest": False, "drop": DROP_B})
    ev = h.emit(ET.HELLO)                                     # e.g. the first HELLO after a /reload mid-drop
    assert ev["flags"] & int(Flags.DROP_ACTIVE) and "placeKey" not in ev   # the event itself carries none
    assert shown(h).place_key == p2.place_key_for(DROP_B["id"])
    chat = p2.parse_chat_line([x for x in h.chat_lines() if x.startswith("NDROP2")][-1])
    assert chat.type == ET.HELLO and chat.place_key is None   # the chat line is the event as emitted


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_music_strips_decode_on_the_companions_screen_reader(scale):
    h = make()
    h.ns.Beacon.SetPrefs(h.table(PREFS))
    h.emit(ET.HELLO)
    for ev in (request(h, client=CID), request(h, "test-youtube", T0 + 9)):
        while h.status()["current"]["seq"] != ev["seq"]:
            h.advance(0.5)
        got = h.beacon_capture_decoded(scale=scale)
        assert got == full(ev), scale
    while h.status()["current"]["type"] != int(ET.HELLO):
        h.advance(0.5)
    got = h.beacon_capture_decoded(scale=scale)
    assert got.type == ET.HELLO and got.prefs == p2.Prefs("youtube-spotify", "topleft", False, True)


# --------------------------------------------------------------- ChatLink --

def test_a_request_is_padded_and_prefs_only_is_not():
    h = make()
    request(h)
    lines = h.chat_lines()
    assert lines[0].startswith("NDROP2 ") and p2.parse_chat_line(lines[0]).action.name == "test-spotify"
    pads = [x for x in lines[1:] if x.startswith("NDPAD ")]
    assert len(pads) == p2.pad_line_count(50000) == len(lines) - 1
    n = len(lines)
    prefs_only(h)
    assert len(h.chat_lines()) == n + 1 and p2.parse_chat_line(h.chat_lines()[-1]).prefs.order == "youtube-spotify"


def test_requests_waiting_in_the_lockdown_queue_go_after_20_seconds():
    h = make()
    h.state.chatLockdown = True
    request(h, "test-spotify")
    prefs_only(h)
    h.advance(19)
    h.state.chatLockdown = False
    h.advance(1.1)
    lines = [x for x in h.chat_lines() if x.startswith("NDROP2 ")]
    assert [p2.parse_chat_line(x).action is not None for x in lines] == [True, False]
    h.state.chatLockdown = True
    request(h, "test-youtube", T0 + 50)
    prefs_only(h)
    h.advance(21)
    h.state.chatLockdown = False
    h.advance(1.1)
    lines = [p2.parse_chat_line(x) for x in h.chat_lines() if x.startswith("NDROP2 ")]
    assert [f.action is not None for f in lines] == [True, False, False]   # the late request is gone, with its pads
    assert h.chat_status()["stale"] == 1
    assert sum(1 for x in h.chat_lines() if x.startswith("NDPAD ")) == p2.pad_line_count(50000)


def test_hello_lines_carry_prefs_once_set_in_game():
    h = AddonHarness(sets=[make_set([DROP_A])], settings={"chatlog": True})
    hellos = [f for f in h.chat_frames() if f.type == ET.HELLO]
    assert hellos and hellos[-1].prefs is None
    h.set_setting("musicOrder", "spotify")
    h.emit(ET.HELLO)
    hellos = [f for f in h.chat_frames() if f.type == ET.HELLO]
    assert hellos[-1].prefs == p2.Prefs("spotify", "topright", True, True, 100)
    assert len(p2.encode_frame(hellos[-1])) == 18
