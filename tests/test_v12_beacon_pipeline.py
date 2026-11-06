"""Beacon v2 end to end (DESIGN-v1.2 2.3, 6.3): ns.Emit -> Beacon.lua textures -> a synthetic
BGRA capture of the client's top-left corner -> beacon2.locate_and_decode -> the same frame.

Runs the addon's Protocol2 / Core / Emit / Beacon / ChatLink under lupa (tests/v12_beacon_harness.py)
at 2 / 3 / 4 px cells and 100 / 125 / 150 % capture scaling, and checks the frame itself: parentless,
TOOLTIP strata, level 9000, no mouse, anchored to WorldFrame, exact physical-pixel cells at every
UI scale and screen size, adaptive width, a lazily grown texture pool and relayout on changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v12_beacon_harness import BeaconHarness  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402
from needledropper.transport import beacon2  # noqa: E402

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
YT_ID = "dQw4w9WgXcQ"

#: Azeroth Mixtape's Durotar drop, as a map drop.
DUROTAR = {"id": "zone:1411", "kind": "zone", "mapID": 1411, "label": "Durotar", "title": "9 to 5",
           "artist": "Dolly Parton", "durationMs": 165547, "startMs": 0, "youtube": "d_vPJjqWxZM",
           "setId": "azeroth-mixtape", "setRef": "builtin:azeroth-mixtape", "builtin": True}
#: A user drop with every field (DESIGN 2.3: 141 bytes, fitted to 113 at 3 px).
LIONS_PRIDE = {"id": "sub:1429:lion-s-pride-inn", "kind": "subzone", "mapID": 1429, "subzone": "Lion's Pride Inn",
               "label": "Lion's Pride Inn", "title": "I'll Keep Coming", "artist": "Low Roar", "spotify": SPOTIFY_ID,
               "youtube": YT_ID, "startMs": 12000, "durationMs": 95000, "volume": 80, "setId": "my-drops"}


def make(**kw) -> BeaconHarness:
    settings = {"beaconCellPx": 3}
    settings.update(kw.pop("settings", {}))
    return BeaconHarness(settings=settings, **kw)


def expected(ev: dict, px: int, max_rows=None) -> p2.Frame:
    """What the beacon should show for emitted event ``ev`` with ``px`` px cells (no clearance limit)."""
    rows = p2.rows_cap(px) if max_rows is None else max_rows
    return p2.frame_from_event(ev, max_bytes=p2.capacity(rows))


def decode_capture(h: BeaconHarness, scale: float, seed: int = 0):
    w, hh = beacon2.capture_size(scale)
    buf, w, hh, stride = h.beacon_bgra(scale=scale, width=w, height=hh, seed=seed)
    rep = beacon2.scan(buf, w, hh, stride)
    assert not rep.reason.startswith("decoder error"), rep.reason
    return rep


# ----------------------------------------------------------- the pipeline --

@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5], ids=["100%", "125%", "150%"])
@pytest.mark.parametrize("px", [2, 3, 4])
def test_emitted_frames_decode_from_a_capture(px, scale):
    h = make(settings={"beaconCellPx": px, "chatlog": True})
    steps = [
        (ET.HELLO, None, 0),
        (ET.ARM, DUROTAR, 0),
        (ET.FIRE, LIONS_PRIDE, Flags.TEST),
        (ET.CANCEL, LIONS_PRIDE, Flags.TEST),
        (ET.PING, None, Flags.TEST),
        (ET.FIRE, DUROTAR, 0),
    ]
    for i, (etype, drop, extra) in enumerate(steps):
        ev = h.emit(etype, drop, int(extra))
        want = expected(ev, px)
        assert h.beacon_decoded() == want, (etype, h.status())
        rep = decode_capture(h, scale, seed=i + px * 10)
        assert rep.result is not None, (etype, px, scale, rep)
        assert rep.result.frame == want and rep.result.frame.raw == p2.encode_frame(want)
        # the strip sits at (0, 0); the reported origin is a fit, within a pixel (a one-row strip at
        # the capture's top edge has no row boundary above it to pin y)
        assert abs(rep.result.x) <= (0 if scale == 1.0 else 1) and abs(rep.result.y) <= 1
        assert rep.result.cell_px == pytest.approx(px * scale, abs=0.06)
        st = h.status()
        assert (rep.result.rows, rep.result.cols) == (st["rows"], st["cols"])
        assert st["rows"] * px <= p2.MAX_STRIP_PX and st["cellPx"] == px
        # the chat line carries the same event with every field (same dedupe key)
        chat = p2.parse_chat_line([x for x in h.chat_lines() if x.startswith("NDROP2 ")][-1])
        assert chat == p2.frame_from_event(ev) and chat.key == want.key
        h.advance(3.5)  # past every hold


@pytest.mark.parametrize("px", [2, 3, 4])
def test_the_full_song_is_fitted_to_the_cell_size_cap(px):
    h = make(settings={"beaconCellPx": px})
    ev = h.emit(ET.FIRE, LIONS_PRIDE)
    f = h.beacon_decoded()
    size = len(p2.encode_frame(f))
    assert size == {2: 141, 3: 113, 4: 59}[px] <= p2.capacity(p2.rows_cap(px))
    assert f == expected(ev, px)
    assert (f.place_key, f.start_ds, f.length_ds) == (p2.place_key_for(LIONS_PRIDE["id"]), 120, 950)
    assert f.spotify_id == SPOTIFY_ID and f.youtube_id == YT_ID
    assert (f.label is not None) == (px == 2) and (f.drop_id is not None) == (px != 4)
    assert h.status()["lastFrameBytes"] == size


def test_strip_size_adapts_to_the_frame():
    """DESIGN 2.3 measured sizes, drawn: idle 96 x 3, CANCEL 240 x 3, Mixtape FIRE (with label and
    set id) 240 x 9, the fitted user drop 336 x 9; unused pool textures are hidden."""
    h = make()
    cases = [(ET.HELLO, None, "96x3", 7), (ET.CANCEL, {"id": "zone:1429", "kind": "zone", "mapID": 1429}, "240x3", 22),
             (ET.FIRE, DUROTAR, "240x9", 86), (ET.FIRE, LIONS_PRIDE, "336x9", 113)]
    for etype, drop, size, nbytes in cases:
        h.emit(etype, drop)
        st = h.status()
        assert (st["size"], st["lastFrameBytes"]) == (size, nbytes), etype
        assert len(h.shown_textures()) == st["rows"] * st["cols"]
        h.advance(3.5)
    assert len(h.beacon_textures()) <= 448
    assert h.status()["size"] == "96x3"  # after the last FIRE: HELLO + DROP_ACTIVE


# ------------------------------------------------------------- the frame --

def test_frame_is_parentless_tooltip_9000_mouse_off_at_worldframe_topleft():
    h = make()
    f = h.beacon_frame()
    assert f is not None and f.GetParent(f) is None  # Alt+Z cannot hide it
    assert f.GetName(f) == "NeedleDropperBeacon"
    assert f.GetFrameStrata(f) == "TOOLTIP" and f.GetFrameLevel(f) == 9000
    assert not f.IsMouseEnabled(f)
    assert len(h.beacon_textures()) == 0 and not h.beacon_shown()  # lazily: nothing drawn before an event
    h.emit(ET.HELLO)
    point, _rel, rel_point, x, y = f.GetPoint(f, 1)
    assert (point, rel_point, x, y) == ("TOPLEFT", "TOPLEFT", 0, 0)
    assert h.lua("return select(2, NeedleDropperBeacon:GetPoint(1)) == WorldFrame") is True
    assert h.lua("for _, t in ipairs(NeedleDropperBeacon.__regions) do "
                 "if t.__points.TOPLEFT[2] ~= NeedleDropperBeacon then return false end end return true") is True
    texs = h.beacon_textures()
    assert len(texs) == 32  # a 32-cell HELLO: the pool grows with the frames
    for t in texs:
        assert t["__snap"] is True and t["__texelBias"] == 0 and t["__layer"] == "OVERLAY"
    h.emit(ET.FIRE, LIONS_PRIDE)
    assert len(h.beacon_textures()) == 336
    assert h.beacon_shown()


@pytest.mark.parametrize("phys_h,ui_scale", [(768, 0.65), (1080, 0.65), (1080, 1.0), (1440, 0.64), (2160, 0.65),
                                             (1440, 1.15)])
@pytest.mark.parametrize("px", [2, 3, 4])
def test_cells_are_exact_physical_pixels(px, phys_h, ui_scale):
    h = make(settings={"beaconCellPx": px}, state={"physH": phys_h, "physW": phys_h * 16 // 9, "uiScale": ui_scale})
    h.emit(ET.FIRE, DUROTAR)
    assert h.beacon_cell_px() == pytest.approx(px, abs=1e-9)
    for x, y, w, hh, _rgb in h.texture_rects():
        assert x / px == pytest.approx(round(x / px), abs=1e-9) and y / px == pytest.approx(round(y / px), abs=1e-9)
    st = h.status()
    assert st["cellUnits"] == pytest.approx(px * 768 / phys_h)


def test_relayout_on_display_scale_and_setting_changes():
    h = make(state={"physH": 1440, "physW": 2560})
    h.emit(ET.ARM, DUROTAR)
    assert h.beacon_cell_px() == pytest.approx(3)
    h.state.physH, h.state.physW = 1080, 1920
    h.fire("DISPLAY_SIZE_CHANGED")
    t = h.shown_textures()[5]
    assert t.GetWidth(t) == pytest.approx(3 * 768 / 1080) and h.beacon_cell_px() == pytest.approx(3)
    h.state.uiScale = 0.71
    h.fire("UI_SCALE_CHANGED")  # a parentless frame's pixels do not move with the UI scale
    assert h.beacon_cell_px() == pytest.approx(3)
    h.set_setting("beaconCellPx", 4)
    assert h.beacon_cell_px() == pytest.approx(4) and h.status()["cellPx"] == 4
    assert h.beacon_decoded() == expected(h.last_event(), 4)  # re-encoded for the new cap
    h.set_setting("beaconCellPx", 9)  # outside 2..4: clamped
    assert h.status()["settingCellPx"] == 4
    h.set_setting("beaconCellPx", 1)
    assert h.status()["settingCellPx"] == 2 and h.beacon_cell_px() == pytest.approx(2)
    h.lua("NeedleDropperDB.settings.beaconCellPx = nil")
    assert h.ns.Beacon.SettingCellPx() == 3  # the v1.2 default


@pytest.mark.parametrize("corner", ["TOPRIGHT", "BOTTOMLEFT", "BOTTOMRIGHT", "topright"])
def test_corner_setting_anchors_the_strip_and_still_decodes(corner):
    h = make()
    h.set_setting("beaconCorner", corner)
    h.emit(ET.FIRE, DUROTAR)
    f = h.beacon_frame()
    point, _rel, rel_point, x, y = f.GetPoint(f, 1)
    assert (point, rel_point, x, y) == (corner.upper(), corner.upper(), 0, 0)
    assert h.status()["corner"] == corner.upper()
    assert h.beacon_decoded() == expected(h.last_event(), 3)
    h.set_setting("beaconCorner", "MIDDLE")
    assert h.status()["corner"] == "TOPLEFT"
    point, *_ = f.GetPoint(f, 1)
    assert point == "TOPLEFT"


def test_disabled_beacon_keeps_rendering_hidden_and_force_show():
    h = make(settings={"beacon": False})
    h.emit(ET.ARM, DUROTAR)
    assert not h.beacon_shown() and h.beacon_decoded() is not None  # state kept, frame hidden
    h.ns.Beacon.ForceShow(10)
    assert h.beacon_shown()
    h.advance(10.1)
    assert not h.beacon_shown()
    h.set_setting("beacon", True)
    assert h.beacon_shown()
    assert decode_capture(h, 1.0).result is not None
    h.set_setting("beacon", False)
    assert decode_capture(h, 1.0).result is None  # nothing on screen


def test_status_reports_the_drawn_strip():
    h = make()
    h.emit(ET.FIRE, LIONS_PRIDE)
    st = h.status()
    for key in ("cellPx", "rows", "cols", "size", "lastFrameBytes", "warnings", "maxRows", "corner", "shown",
                "enabled", "queued"):
        assert key in st, key
    assert (st["cellPx"], st["rows"], st["cols"], st["size"], st["lastFrameBytes"]) == (3, 3, 112, "336x9", 113)
    assert st["lastFrameType"] == int(ET.FIRE) and st["warnings"] == {} and st["overflow"] is False
    assert st["current"]["dropId"] == LIONS_PRIDE["id"]
