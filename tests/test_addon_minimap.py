"""Execute the real minimap launcher under Lua 5.1 with recorded Blizzard frames."""
from __future__ import annotations

import math

import pytest

from v12_ui_harness import UIHarness


MINIMAP = """
Minimap = CreateFrame("Frame", "Minimap", UIParent)
Minimap:SetSize(140, 140)
function Minimap:GetCenter() return 200, 200 end
function GetCursorPosition() return SHIM.state.cursorX or 0, SHIM.state.cursorY or 0 end
function GetMinimapShape() return SHIM.state.minimapShape or "ROUND" end
"""


def harness(**kwargs):
    return UIHarness(real=True, before_load=lambda h: h.lua(MINIMAP), **kwargs)


def icon(h):
    return h.g.NeedleDropperMinimapButton


def script(h, name):
    b = icon(h)
    fn = b.GetScript(b, name)
    assert fn is not None
    fn(b)
    h.check_errors()


def position(h):
    b = icon(h)
    anchor, parent, relative, x, y = b.GetPoint(b, 1)
    assert anchor == relative == "CENTER"
    assert h.same(parent, h.g.Minimap)
    return x, y


def cursor(h, x, y):
    h.state.cursorX = x
    h.state.cursorY = y


def test_initialization_is_idempotent_and_does_not_build_the_main_window():
    h = harness(login=False)
    assert icon(h) is None
    h.login()
    b = icon(h)
    assert h.shown(b) and h.window is None
    assert h.same(b.GetParent(b), h.g.Minimap)
    assert position(h) == pytest.approx((-80 / math.sqrt(2), -80 / math.sqrt(2)))
    assert h.account_db()["ui"]["minimapAngle"] == 225
    h.ns.Minimap.Init()
    h.ns.Fire("ND_INIT")
    assert h.same(icon(h), b)
    assert len([c for c in h.created() if c["name"] == "NeedleDropperMinimapButton"]) == 1


def test_missing_or_late_minimap_does_not_break_addon_initialization():
    h = UIHarness(real=True)
    assert icon(h) is None
    h.lua(MINIMAP)
    h.ns.Fire("PLAYER_LOGIN")
    h.check_errors()
    assert h.shown(icon(h))


def test_left_click_toggles_window_and_respects_existing_combat_deferral():
    h = harness()
    h.click(icon(h))
    assert h.UI.IsShown()
    h.click(icon(h))
    assert not h.UI.IsShown()
    h.combat(True)
    h.click(icon(h))
    assert not h.UI.IsShown()
    h.combat(False)
    assert h.UI.IsShown()
    assert h.forbidden() == []


def test_right_click_opens_settings_and_tooltip_explains_both_actions_and_drag():
    h = harness()
    h.right_click(icon(h))
    assert h.from_lua(h.log.openedCategory)
    assert not h.UI.IsShown()
    h.hover(icon(h))
    tip = "\n".join(h.tooltip())
    assert "NeedleDropper" in tip
    assert "Left-click: open" in tip and "Right-click: settings" in tip
    assert "Drag to move around the minimap." in tip
    assert "Hide this icon in Options > AddOns > NeedleDropper." in tip
    h.unhover(icon(h))
    assert not h.shown(h.g.GameTooltip)


def test_saved_hidden_setting_checkbox_and_settings_reset_control_visibility():
    h = harness(settings={"minimapIcon": False})
    assert not h.shown(icon(h))
    setting = h.shim.settings.settings["NeedleDropper_minimapIcon"]
    assert setting is not None
    setting.SetValue(setting, True)
    h.check_errors()
    assert h.shown(icon(h)) and h.settings()["minimapIcon"] is True
    h.ns.SetSetting("minimapIcon", False)
    assert not h.shown(icon(h))
    h.ns.ResetData("settings")
    h.check_errors()
    assert h.shown(icon(h))


def test_scaled_drag_persists_position_across_reload_without_opening_window():
    h = harness(db={"schema": 2, "ui": {"w": 840, "h": 600}})
    h.g.Minimap.SetScale(h.g.Minimap, 0.5)
    # Center (200, 200) in minimap units is (100, 100) in physical pixels.
    cursor(h, 100, 150)
    script(h, "OnDragStart")
    assert position(h) == pytest.approx((0, 80))
    cursor(h, 50, 100)
    h.advance(0.1)
    assert position(h) == pytest.approx((-80, 0))
    script(h, "OnDragStop")
    assert icon(h).GetScript(icon(h), "OnUpdate") is None
    h.click(icon(h))  # release of this same drag must not launch the window
    assert not h.UI.IsShown()
    saved = h.account_db()
    assert saved["ui"] == {"w": 840, "h": 600, "minimapAngle": 180}
    again = harness(db=saved)
    assert position(again) == pytest.approx((-80, 0))
    h.advance(0.2)
    h.click(icon(h))
    assert h.UI.IsShown()


@pytest.mark.parametrize("saved_angle", ["broken", False, {}, float("inf"), float("nan")])
def test_malformed_saved_angle_is_repaired_without_losing_other_ui_preferences(saved_angle):
    # The harness deliberately writes special float values through Lua itself.
    h = harness(login=False, db={"schema": 2, "ui": {"w": 800}})
    h.g.NeedleDropperDB.ui.minimapAngle = h.table(saved_angle) if isinstance(saved_angle, dict) else saved_angle
    h.login()
    assert h.account_db()["ui"] == {"w": 800, "minimapAngle": 225}
    assert all(math.isfinite(v) for v in position(h))


def test_hidden_during_drag_stops_updates_and_keeps_last_position():
    h = harness()
    h.hover(icon(h))
    cursor(h, 200, 120)
    script(h, "OnDragStart")
    assert not h.shown(h.g.GameTooltip)
    h.ns.SetSetting("minimapIcon", False)
    assert not h.shown(icon(h))
    assert icon(h).GetScript(icon(h), "OnUpdate") is None
    assert h.account_db()["ui"]["minimapAngle"] == 270
    cursor(h, 300, 200)
    h.advance(0.2)
    script(h, "OnDragStop")  # a late release after hiding must not resume the drag
    assert h.account_db()["ui"]["minimapAngle"] == 270
    h.ns.SetSetting("minimapIcon", True)
    assert position(h) == pytest.approx((0, -80))


def test_resize_and_square_minimap_keep_launcher_on_the_edge():
    h = harness(db={"schema": 2, "ui": {"minimapAngle": 405}})
    assert h.account_db()["ui"]["minimapAngle"] == 45
    h.state.minimapShape = "SQUARE"
    h.g.Minimap.SetSize(h.g.Minimap, 200, 160)
    assert position(h) == pytest.approx((110, 90))
    h.state.minimapShape = "ROUND"
    h.ns.Minimap.Refresh()
    assert position(h) == pytest.approx((110 / math.sqrt(2), 90 / math.sqrt(2)))


def test_cursor_at_center_keeps_prior_angle_and_malformed_ui_table_is_repaired():
    h = harness(db={"schema": 2, "ui": "broken"})
    cursor(h, 200, 200)
    script(h, "OnDragStart")
    script(h, "OnDragStop")
    assert h.account_db()["ui"] == {"minimapAngle": 225}
    assert position(h) == pytest.approx((-80 / math.sqrt(2), -80 / math.sqrt(2)))
