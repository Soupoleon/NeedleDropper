"""Beacon v2 clearance guard (DESIGN-v1.2 2.3 "Runtime guard").

The addon measures the gap between the screen top and the player / target frame art
(``gap = physicalHeight - GetTop() * GetEffectiveScale() / PixelUtil.GetPixelToUIUnitFactor()``)
on PLAYER_ENTERING_WORLD, UI_SCALE_CHANGED, DISPLAY_SIZE_CHANGED and Edit Mode exit, caps the
strip at ``floor((gap - 2) / cellPx)`` rows within the 9 px cap, falls back to 2 px cells, then
fits the frame further, draws at least one row, and records a warning for the self-test when even
one 2 px row overlaps the art. Fake art is placed in physical pixels (tests/lua/shim_beacon2.lua).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v12_beacon_harness import BeaconHarness  # noqa: E402
from needledropper import protocol2 as p2  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402
from needledropper.transport import beacon2  # noqa: E402

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
YT_ID = "dQw4w9WgXcQ"
LIONS_PRIDE = {"id": "sub:1429:lion-s-pride-inn", "kind": "subzone", "mapID": 1429, "subzone": "Lion's Pride Inn",
               "label": "Lion's Pride Inn", "title": "I'll Keep Coming", "artist": "Low Roar", "spotify": SPOTIFY_ID,
               "youtube": YT_ID, "startMs": 12000, "durationMs": 95000, "volume": 80, "setId": "my-drops"}
OVERLAP = "the beacon overlaps your player frame: move the frame in Edit Mode or set beaconCorner"


def harness(px: int = 3, *, art: dict | None = None, target: dict | None = None, state: dict | None = None,
            **kw) -> BeaconHarness:
    def place(h):
        if art is not None:
            h.shim.SetUnitFrameArt("PlayerFrame", h.table(art))
        if target is not None:
            h.shim.SetUnitFrameArt("TargetFrame", h.table(target))
    return BeaconHarness(settings={"beaconCellPx": px}, state=state, before_load=place, **kw)


def warnings(h: BeaconHarness) -> list[str]:
    w = h.status().get("warnings")  # a Lua array: [] arrives as {}
    return list(w.values()) if isinstance(w, dict) else list(w or [])


def regions(h: BeaconHarness) -> set[str]:
    r = h.status().get("measuredFrom")
    return set(r.values()) if isinstance(r, dict) else set(r or [])


def drawn(h: BeaconHarness) -> tuple[int, int, int]:
    """(cell px, rows, frame bytes) of the strip on screen."""
    st = h.status()
    return st["cellPx"], st["rows"], st["lastFrameBytes"]


# ------------------------------------------------------------ measuring --

@pytest.mark.parametrize("phys_h,ui_scale", [(768, 0.65), (1080, 0.65), (1080, 1.0), (1440, 0.64), (2160, 0.65),
                                             (1200, 1.15)])
def test_gap_formula_round_trips_at_every_scale(phys_h, ui_scale):
    h = harness(art={"topPx": 11.7, "leftPx": 30}, state={"physH": phys_h, "physW": phys_h * 16 // 9,
                                                         "uiScale": ui_scale})
    assert h.status()["gap"] == pytest.approx(11.7)
    assert regions(h) == {"PlayerFrame.PlayerFrameContainer.FrameTexture"}


@pytest.mark.parametrize("ui_scale,phys_h,gap", [(0.65, 768, 11.7), (0.65, 1080, 16.5), (1.0, 1080, 25.3),
                                                 (0.64, 1440, 21.6), (0.65, 2160, 32.9)])
def test_design_clearance_table_keeps_three_rows_at_3_px(ui_scale, phys_h, gap):
    """DESIGN 2.3: the art ~18 UI units below the top leaves 11.7..32.9 px -- always the full
    3 rows of 3 px, so nothing changes on default setups."""
    top_units = 18.0
    h = harness(art={"topPx": top_units * ui_scale * phys_h / 768, "scale": ui_scale},
                state={"physH": phys_h, "physW": phys_h * 16 // 9, "uiScale": ui_scale})
    assert h.status()["gap"] == pytest.approx(gap, abs=0.06)
    assert h.status()["maxRows"] == 3
    h.emit(ET.FIRE, LIONS_PRIDE)
    assert drawn(h) == (3, 3, 113) and warnings(h) == []


def test_no_art_measured_means_the_nominal_caps():
    h = harness()
    st = h.status()
    assert "gap" not in st and st["maxRows"] == 3
    h.emit(ET.FIRE, LIONS_PRIDE)
    assert drawn(h) == (3, 3, 113)


def test_smallest_gap_of_player_and_target_frames_counts():
    h = harness(art={"topPx": 20}, target={"topPx": 9, "leftPx": 300})
    assert h.status()["gap"] == pytest.approx(9)
    assert regions(h) == {"PlayerFrame.PlayerFrameContainer.FrameTexture",
                          "TargetFrame.TargetFrameContainer.FrameTexture"}


def test_hidden_or_far_right_regions_are_ignored():
    h = harness(art={"topPx": 20}, target={"topPx": 5, "leftPx": 300, "shown": False})
    assert h.status()["gap"] == pytest.approx(20)
    h.art("TargetFrame", topPx=5, leftPx=449)  # left edge beyond the widest strip (448 px)
    h.fire("UI_SCALE_CHANGED")
    assert h.status()["gap"] == pytest.approx(20)
    h.art("TargetFrame", topPx=5, leftPx=447)
    h.fire("UI_SCALE_CHANGED")
    assert h.status()["gap"] == pytest.approx(5)


def test_falls_back_to_the_frame_when_the_art_is_missing_or_unreadable():
    h = harness(art={"topPx": 14, "noArt": True, "frameTopPx": 10})
    assert h.status()["gap"] == pytest.approx(10)
    assert regions(h) == {"PlayerFrame"}
    h.art("PlayerFrame", topPx=14, frameTopPx=10, artShown=False)  # vehicle UI hides FrameTexture
    h.fire("DISPLAY_SIZE_CHANGED")
    assert h.status()["gap"] == pytest.approx(10)
    h.art("PlayerFrame", topPx=14, noTop=True)  # GetTop returns nothing (MayReturnNothing)
    h.fire("DISPLAY_SIZE_CHANGED")
    assert "gap" not in h.status()
    h.art("PlayerFrame", topPx=14, secretTop=True)  # SecretWhenAnchoringSecret
    h.fire("DISPLAY_SIZE_CHANGED")
    assert "gap" not in h.status()
    h.art("PlayerFrame", topPx=14, shown=False)
    h.fire("DISPLAY_SIZE_CHANGED")
    assert "gap" not in h.status()


@pytest.mark.parametrize("trigger", ["PLAYER_ENTERING_WORLD", "UI_SCALE_CHANGED", "DISPLAY_SIZE_CHANGED",
                                     "EditMode.Exit"])
def test_remeasured_on_each_trigger_and_again_after_a_second(trigger):
    h = harness(art={"topPx": 20})
    h.emit(ET.FIRE, LIONS_PRIDE)
    assert drawn(h) == (3, 3, 113)
    h.art("PlayerFrame", topPx=6)  # the player moved the frame up in Edit Mode
    if trigger == "EditMode.Exit":
        h.trigger_registry("EditMode.Exit")
    else:
        h.fire(trigger)
    assert h.status()["gap"] == pytest.approx(6)
    assert drawn(h) == (2, 2, 59)  # redrawn at once for the new clearance
    h.art("PlayerFrame", topPx=30)
    h.advance(1.1)  # the second measurement, once the UI has settled
    assert h.status()["gap"] == pytest.approx(30)


def test_edit_mode_callback_is_registered_with_an_owner_table():
    h = harness()
    assert h.shim.RegistryHas("EditMode.Exit") is True


# ------------------------------------------------------------ the policy --

@pytest.mark.parametrize("gap,px,event,want", [
    # ample clearance: the nominal frame at the configured size
    (30, 3, "fire", (3, 3, 113, 1)),
    (30, 4, "fire", (4, 2, 59, 1)),
    (30, 2, "fire", (2, 4, 141, 1)),
    # 8 px: 3 px x 2 rows only -- small frames stay at 3 px, the song moves to 2 px cells (3 rows)
    (8, 3, "hello", (3, 1, 7, 1)),
    (8, 3, "fire", (2, 3, 113, 2)),
    # 6 px: one 3 px row / two 2 px rows -- the song is fitted to two rows of 2 px cells (<= 81 B)
    (6, 3, "fire", (2, 2, 59, 3)),
    (6, 3, "cancel", (3, 1, 38, 1)),
    (6, 4, "fire", (2, 2, 59, 2)),
    (6, 2, "fire", (2, 2, 59, 3)),
    # 5 px: one row at any size -- the song is fitted to one row of 2 px cells (39 B: YouTube kept)
    (5, 3, "fire", (2, 1, 32, 3)),
    (5, 3, "hello", (3, 1, 7, 1)),
])
def test_rows_and_cell_size_follow_the_clearance(gap, px, event, want):
    h = harness(px, art={"topPx": gap})
    drop = {"fire": LIONS_PRIDE, "cancel": LIONS_PRIDE, "hello": None}[event]
    etype = {"fire": ET.FIRE, "cancel": ET.CANCEL, "hello": ET.HELLO}[event]
    ev = h.emit(etype, drop)
    st = h.status()
    assert (st["cellPx"], st["rows"], st["lastFrameBytes"], st["step"]) == want
    assert st["rows"] * st["cellPx"] <= gap - 2  # clear of the art, with the 2 px margin
    assert not st["overflow"] and warnings(h) == []
    if want[3] == 3:  # fitted to the rows that fit at 2 px
        budget = p2.capacity(max(1, min(p2.rows_cap(2), math.floor((gap - 2) / 2))))
    else:             # the nominal frame of the configured cell size
        budget = p2.capacity(p2.rows_cap(px))
    assert h.beacon_decoded() == p2.frame_from_event(ev, max_bytes=budget)


def test_preferred_provider_decides_which_id_survives_one_row():
    h = harness(3, art={"topPx": 5})
    h.set_companion("connected")
    ev = h.emit(ET.FIRE, LIONS_PRIDE)
    assert ev["prefer"] == "spotify"
    st = h.status()
    assert (st["cellPx"], st["lastFrameBytes"], st["rows"], st["overflow"]) == (2, 43, 2, True)  # 43 B > 39 B
    f = h.beacon_decoded()
    assert f.spotify_id == SPOTIFY_ID and f.youtube_id is None and (f.start_ds, f.length_ds) == (120, 950)
    assert any("does not fit above your player frame" in w for w in warnings(h))
    assert h.status()["rows"] * 2 == 4  # one row more than the clearance: the beacon must exist
    h.advance(3.5)
    h.set_companion("premium-required")
    h.emit(ET.FIRE, LIONS_PRIDE)
    f = h.beacon_decoded()
    assert h.status()["lastFrameBytes"] == 32 and f.youtube_id == YT_ID and f.spotify_id is None


@pytest.mark.parametrize("gap", [3.9, 3, 1, -4])
def test_warning_when_even_one_2px_row_overlaps(gap):
    h = harness(3, art={"topPx": gap})
    assert warnings(h) == [OVERLAP]
    h.emit(ET.FIRE, LIONS_PRIDE)
    st = h.status()
    assert (st["cellPx"], st["rows"]) == (2, 1) and st["lastFrameBytes"] == 32  # one row anyway
    assert h.beacon_decoded() is not None
    h.art("PlayerFrame", topPx=30)  # moved down in Edit Mode: the warning clears
    h.trigger_registry("EditMode.Exit")
    assert OVERLAP not in warnings(h)


def test_no_warning_at_exactly_one_2px_row():
    h = harness(3, art={"topPx": 4})
    assert warnings(h) == []
    h.emit(ET.HELLO)
    assert h.status()["rows"] == 1


def test_other_corners_are_not_measured():
    h = harness(3, art={"topPx": 5})
    assert h.status()["gap"] == pytest.approx(5)
    h.set_setting("beaconCorner", "BOTTOMRIGHT")
    assert "gap" not in h.status() and h.status()["maxRows"] == 3
    h.emit(ET.FIRE, LIONS_PRIDE)
    assert drawn(h) == (3, 3, 113)


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
@pytest.mark.parametrize("gap", [8, 6, 5, 3])
def test_squeezed_strips_still_decode_from_a_capture(gap, scale):
    h = harness(3, art={"topPx": gap})
    h.set_companion("connected")
    for drop, etype in ((LIONS_PRIDE, ET.FIRE), (None, ET.HELLO), (LIONS_PRIDE, ET.CANCEL)):
        h.emit(etype, drop)
        w, hh = beacon2.capture_size(scale)
        res = beacon2.locate_and_decode(*h.beacon_bgra(scale=scale, width=w, height=hh, seed=gap))
        assert res is not None and res.frame == h.beacon_decoded(), (gap, scale, etype)
        assert res.cell_px == pytest.approx(h.status()["cellPx"] * scale, abs=0.06)
        h.advance(3.5)


def test_choose_is_a_pure_function_of_event_px_and_gap():
    h = harness()
    ev = h.table({"type": 3, "seq": 1, "dropId": LIONS_PRIDE["id"], "spotify": SPOTIFY_ID, "youtube": YT_ID,
                  "artist": "Low Roar", "title": "I'll Keep Coming", "startMs": 12000, "lengthMs": 95000,
                  "volume": 80, "label": "Lion's Pride Inn", "setId": "my-drops"})
    choose = h.ns.Beacon.Choose
    for px, gap, want in [(3, None, (3, 113, 1)), (3, 8, (2, 113, 2)), (3, 6, (2, 59, 3)), (4, None, (4, 59, 1)),
                          (2, 6, (2, 59, 3)), (9, None, (4, 59, 1)), (1, None, (2, 141, 1)), (3, 5, (2, 32, 3))]:
        c = choose(ev, px, gap, "TOPLEFT")
        assert (c.px, len(c.bytes), c.step) == want, (px, gap)
