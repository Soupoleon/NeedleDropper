"""NeedleDropper window shell (DESIGN-v1.2 §4.3, §4.8-§4.10; CONTRACTS 2.9), against the recording
frame mock in tests/lua/shim_ui.lua and the CONTRACTS 2.5-2.7 model stand-ins."""
from __future__ import annotations

import pytest

from v12_ui_harness import UIHarness

import lupa.lua51 as lua51


@pytest.fixture
def h():
    return UIHarness()


def _tabs(h):
    return list(h.window.Tabs.values())


# --------------------------------------------------------------- lazy build --

def test_nothing_is_built_until_the_window_first_opens():
    h = UIHarness()
    assert h.frames_before_ui == h.frames_after_ui, "UI files created frames at load"
    assert h.window is None
    assert not h.UI.IsShown()
    assert h.created() == [] or all(c["name"] != "NeedleDropperFrame" for c in h.created())
    h.UI.Toggle()
    h.check_errors()
    assert h.window is not None and h.UI.IsShown()
    h.UI.Toggle()
    h.check_errors()
    assert not h.UI.IsShown()
    h.UI.Toggle()
    assert h.UI.IsShown()


def test_window_is_a_named_button_frame_template_without_portrait(h):
    h.open()
    w = h.window
    made = [c for c in h.created() if c["name"] == "NeedleDropperFrame"]
    assert len(made) == 1 and made[0]["template"] == "ButtonFrameTemplate" and made[0]["frameType"] == "Frame"
    assert h.same(w.GetParent(w), h.g.UIParent)
    assert w.__border == "ButtonFrameTemplateNoPortrait"            # ButtonFrameTemplate_HidePortrait
    assert not h.shown(w.PortraitContainer)
    assert h.text(w.TitleContainer.TitleText) == "NeedleDropper"
    assert w.GetFrameStrata(w) == "DIALOG"
    assert w.__clamped is True and w.__movable is True and w.__resizable is True
    assert (w.GetWidth(w), w.GetHeight(w)) == (700, 500)
    assert list(w.__resizeBounds.values()) == [620, 420, 1100, 800]
    grip = w.Grip
    init = h.calls(grip, "Init")
    assert len(init) == 1 and init[0][2:] == [620, 420, 1100, 800]
    assert h.same(init[0][1], w)


def test_close_button_onclick_is_replaced_and_works_in_combat(h):
    h.open()
    w = h.window
    close = w.CloseButton
    assert not h.same(close.GetScript(close, "OnClick"), h.g.UIPanelCloseButton_OnClick)
    h.click(close)
    assert not h.UI.IsShown()
    # In combat the window is hidden, but the player may open it again; X still closes it.
    h.combat(True)
    h.open()
    assert h.UI.IsShown()
    h.click(close)
    assert not h.UI.IsShown()
    assert h.forbidden() == []
    assert list(h.ui_shim.blocked.values()) == []


def test_escape_leaves_a_field_first_then_closes_the_window(h):
    h.open()
    assert "NeedleDropperFrame" in h.special_frames()
    form = h.form()
    h.click(form.Change)                      # built-in -> My Drops: editable, Artist focused
    assert h.has_focus(form.Artist)
    before = h.text(form.Artist)
    h.type(form.Artist, "Half typed")
    assert h.escape() == "editbox"            # restores the field's last value, leaves the field
    assert h.text(form.Artist) == before
    assert h.focused() is None
    assert h.UI.IsShown()
    assert h.escape() == "closed"             # CloseSpecialWindows
    assert not h.UI.IsShown()


def test_three_panel_tabs_with_manual_onclick(h):
    h.open()
    w = h.window
    tabs = _tabs(h)
    assert [h.text(t.Text) for t in tabs] == ["Drops", "Soundtracks", "Journal"]
    for i, t in enumerate(tabs, 1):
        assert t.GetID(t) == i
        assert t.GetName(t) == f"NeedleDropperFrameTab{i}"
        assert t.__template == "PanelTabButtonTemplate"
        assert t.GetScript(t, "OnClick") is not None
    point = tabs[0].GetPoint(tabs[0], 1)
    assert point[0] == "TOPLEFT" and h.same(point[1], w) and point[2:] == ("BOTTOMLEFT", 5, 2)
    assert w.numTabs == 3
    assert w.selectedTab == 1
    h.click(tabs[1])
    assert w.selectedTab == 2
    assert 841 in list(h.ui_shim.sounds.values())      # SOUNDKIT.IG_CHARACTER_INFO_TAB
    assert h.shown(w.soundtracks) and not h.shown(w.drops) and not h.shown(w.journal)
    assert h.account_db()["ui"]["tab"] == 2
    h.click(tabs[2])
    assert h.shown(w.journal) and w.selectedTab == 3
    # The selected tab is disabled (PanelTemplates_SelectTab), others enabled.
    assert not h.enabled(tabs[2]) and h.enabled(tabs[0])
    h.UI.Close()
    h.open()
    assert w.selectedTab == 3                         # last tab remembered
    h.open("drops")
    assert w.selectedTab == 1 and h.shown(w.drops)


def test_combat_hides_the_window_and_brings_it_back(h):
    h.open()
    form = h.form()
    h.click(form.Change)
    h.type(form.Artist, "Typed in a hurry")
    h.combat(True)
    assert not h.UI.IsShown()
    assert h.text(form.Artist) == "Typed in a hurry"   # nothing destroyed
    h.combat(False)
    h.flush()
    assert h.UI.IsShown()
    assert h.text(form.Artist) == "Typed in a hurry"


def test_combat_does_not_reopen_a_window_the_player_closed(h):
    h.open()
    h.UI.Close()
    h.combat(True)
    h.combat(False)
    assert not h.UI.IsShown()
    # Closed during combat (Esc on a hidden window does nothing), still back after combat.
    h.open()
    h.combat(True)
    assert h.escape() in ("gamemenu", "closed")
    h.combat(False)
    assert h.UI.IsShown()


def test_gear_button_opens_the_settings_category(h):
    h.open()
    gear = h.window.SettingsButton
    h.hover(gear)
    assert h.tooltip()[0] == "Settings"
    h.click(gear)
    opened = list(h.shim.log.openedCategory.values())
    assert opened == [h.ns.Options.CategoryID()]


def test_position_and_size_are_saved_and_restored():
    h = UIHarness()
    h.open()
    w = h.window
    h.drag(w.TitleDrag, 120, -40)
    assert h.account_db()["ui"]["point"] == ["CENTER", "CENTER", 120, -40]
    h.resize(w.Grip, 2000, 300)                     # clamped to the resize bounds
    assert (w.GetWidth(w), w.GetHeight(w)) == (1100, 420)
    ui = h.account_db()["ui"]
    assert (ui["w"], ui["h"]) == (1100, 420)
    h2 = UIHarness(db={"ui": {"point": ["TOPLEFT", "TOPLEFT", 30, -60], "w": 800, "h": 600, "tab": 2}})
    h2.open()
    w2 = h2.window
    p = w2.GetPoint(w2, 1)
    assert p[0] == "TOPLEFT" and p[2:] == ("TOPLEFT", 30, -60)
    assert (w2.GetWidth(w2), w2.GetHeight(w2)) == (800, 600)
    assert w2.selectedTab == 2
    # Junk in db.ui falls back to the defaults.
    h3 = UIHarness(db={"ui": {"point": ["NOWHERE", 1, "x", None], "w": "big", "h": 99999}})
    h3.open()
    w3 = h3.window
    assert (w3.GetWidth(w3), w3.GetHeight(w3)) == (700, 800)
    assert w3.GetPoint(w3, 1)[0] == "CENTER"


def test_footer_status_line(h):
    h.open()
    w = h.window
    # The harness login fired the Goldshire drop: "Playing: <place> · <song> (<source>)" + [Stop].
    assert h.text(w.Status) == "Playing: Goldshire · Toto · Africa (YouTube)"
    assert h.shown(w.StopButton)
    h.click(w.StopButton)
    assert h.ns.Director.active is None
    # Elwynn Forest's zone drop is armed now and waits for the cooldown.
    assert h.text(w.Status) == "Next needle drop in 9m 59s: Elwynn Forest"
    assert not h.shown(w.StopButton)
    h.set_location(1411, "")                         # Durotar: nothing mapped here
    h.advance(1.1)
    assert h.text(w.Status) == "NeedleDropper app not found: Spotify and YouTube songs need it"
    h.fakes.companion.present = True
    h.fakes.companion.appVersion = "1.2.0"
    h.fakes.companion.spotify = True
    h.advance(1.1)                                   # the footer ticker
    assert h.text(w.Status) == "App seen 2 min before login · Spotify connected"


def test_footer_waiting_line_names_the_gate():
    h = UIHarness(location=(1436, ""), settle=0.5)     # Westfall, arrival delay not over yet
    h.combat(True)
    h.advance(12)
    h.open()
    assert h.text(h.window.Status) == "Waiting for a calm moment: Westfall (in combat)"


def test_save_now_appears_after_ten_edits_and_reloads(h):
    h.open()
    w = h.window
    assert not h.shown(w.SaveButton)
    for i in range(10):
        h.set_active(["builtin:azeroth-mixtape"])
    h.flush()
    assert h.shown(w.SaveButton)
    assert h.text(w.SaveButton.Text) == "Save now (reloads UI)"
    h.click(w.SaveButton)
    assert int(h.ui_shim.reloads) == 1


def test_key_binding_functions(h):
    g = h.g
    for name in ("NeedleDropper_ToggleWindow", "NeedleDropper_AddDropHere", "NeedleDropper_StopDrop"):
        assert g[name] is not None, name
    g.NeedleDropper_ToggleWindow()
    assert h.UI.IsShown()
    g.NeedleDropper_ToggleWindow()
    assert not h.UI.IsShown()
    g.NeedleDropper_AddDropHere()
    h.check_errors()
    assert h.UI.IsShown() and h.window.selectedTab == 1
    assert h.ns.Director.active is not None
    g.NeedleDropper_StopDrop()
    h.check_errors()
    assert h.ns.Director.active is None


def _open_everything(h):
    h.open()
    for t in _tabs(h):
        h.click(t)
        h.flush()
    h.click(_tabs(h)[0])
    form = h.form()
    h.click(form.More)
    h.UI.OpenImport()
    h.UI.OpenShare("builtin:azeroth-mixtape")
    h.check_errors()


def test_every_edit_box_has_autofocus_off(h):
    _open_everything(h)
    boxes = [c["frame"] for c in h.created() if c["frameType"] == "EditBox"]
    assert len(boxes) >= 12
    for b in boxes:
        assert h.count(b, "SetAutoFocus") >= 1, b.__template
        assert b.__autoFocus is False
    # Edit boxes inside templates (ScrollingEditBoxTemplate's EventEditBox) too.
    for b in h.ui_shim.EditBoxes().values():
        if b.__template in ("StaticPopupEditBoxTemplate",):
            continue
        assert b.__autoFocus is False, b.__template


def test_every_template_is_allowlisted_and_typed(h):
    _open_everything(h)
    allowed = {str(k): str(v) for k, v in h.ui_shim.ALLOWED_TEMPLATES.items()}
    used = set()
    for c in h.created():
        t = c["template"]
        if t is None:
            continue
        assert t in allowed, t
        assert allowed[t] == c["frameType"], (t, c["frameType"])
        used.add(t)
    assert used >= {"ButtonFrameTemplate", "PanelTabButtonTemplate", "PanelResizeButtonTemplate", "WowScrollBoxList",
                    "MinimalScrollBar", "SearchBoxTemplate", "InputBoxInstructionsTemplate",
                    "ScrollingEditBoxTemplate", "WowStyle1DropdownTemplate", "WowStyle1FilterDropdownTemplate",
                    "UICheckButtonTemplate", "UIRadioButtonTemplate", "UIPanelButtonTemplate",
                    "MinimalSliderWithSteppersTemplate"}


def test_shim_refuses_templates_outside_the_allowlist_and_keyboard_capture(h):
    run = h.rt.execute
    with pytest.raises(lua51.LuaError, match="allowlist"):
        run('CreateFrame("Frame", nil, UIParent, "BackdropTemplate")')
    with pytest.raises(lua51.LuaError, match="allowlist"):
        run('CreateFrame("Button", nil, UIParent, "SecureActionButtonTemplate")')
    with pytest.raises(lua51.LuaError, match="is a DropdownButton"):
        run('CreateFrame("Button", nil, UIParent, "WowStyle1DropdownTemplate")')
    with pytest.raises(lua51.LuaError, match="OnKeyDown"):
        run('CreateFrame("Frame", nil, UIParent):SetScript("OnKeyDown", function() end)')
    with pytest.raises(lua51.LuaError, match="EnableKeyboard"):
        run('CreateFrame("Frame", nil, UIParent):EnableKeyboard(true)')


def test_every_list_view_has_an_element_extent(h):
    _open_everything(h)
    lists = [c["frame"] for c in h.created() if c["template"] == "WowScrollBoxList"]
    assert len(lists) == 3
    for lst in lists:
        view = lst.GetView(lst)
        assert view.elementExtent is not None and view.elementExtent > 0
        assert h.count(view, "SetElementExtent") >= 1
        assert lst.__scrollBar.__template == "MinimalScrollBar"


def test_ui_special_frames_lists_every_top_level_frame(h):
    _open_everything(h)
    special = h.special_frames()
    for name in ("NeedleDropperFrame", "NeedleDropperImportDialog", "NeedleDropperShareDialog"):
        assert name in special
        f = h.g[name]
        assert f is not None and f.GetName(f) == name
        assert not h.same(f.CloseButton.GetScript(f.CloseButton, "OnClick"), h.g.UIPanelCloseButton_OnClick)
    # One Esc closes them all (CloseSpecialWindows hides every shown special frame).
    assert h.escape() in ("editbox",)          # the share dialog's code box has focus first
    assert h.escape() == "closed"
    for name in ("NeedleDropperFrame", "NeedleDropperImportDialog", "NeedleDropperShareDialog"):
        assert not h.shown(h.g[name])


def test_icon_buttons_have_tooltips(h):
    h.open()
    h.click(_tabs(h)[1])
    h.flush()
    w = h.window
    h.hover(w.SettingsButton)
    assert h.tooltip() and h.tooltip()[0] == "Settings"
    h.set_active(["builtin:azeroth-mixtape", "builtin:azeroth-unbound"])
    h.flush()
    rows = list(w.soundtracks.List.GetFrames(w.soundtracks.List).values())
    up = rows[0].Up
    assert h.shown(up) and not h.enabled(up)
    h.hover(up)
    assert h.tooltip() == ["Move up", "Plays before the soundtracks below it where both have a song.",
                           "Already at the top."]


def test_no_globals_beyond_the_named_frames_and_entry_points(h):
    before = set(h.new_globals())
    _open_everything(h)
    h.click(h.form().Change)
    h.flush()
    created = set(h.new_globals()) - before
    for name in created:
        assert name.startswith("NeedleDropper") or name.startswith("StaticPopup"), name


def test_open_before_login_is_refused_politely():
    h = UIHarness(login=False)
    assert h.UI.Open() is False
    h.check_errors()
    assert h.window is None
