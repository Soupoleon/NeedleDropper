"""Soundtracks tab (DESIGN-v1.2 §4.5): rows with On / order, details, New / Rename / Delete through
StaticPopupDialogs with the 12.x (dialog, data) handlers, Duplicate, Share / Import, and the
per-character list."""
from __future__ import annotations

from v12_ui_harness import UIHarness


def open_tab(**kw):
    h = UIHarness(location=(1411, ""), **kw)      # Durotar: nothing plays at login
    h.open("soundtracks")
    return h


def rows(h):
    lst = h.window.soundtracks.List
    return list(lst.GetFrames(lst).values())


def row_texts(h):
    return [(h.text(r.Name), h.text(r.Info), h.checked(r.On)) for r in rows(h)]


def details(h):
    d = h.window.soundtracks.Details
    return {k: h.text(d[k]) for k in ("Name", "Author", "Description", "Tags", "Homepage", "Stats", "ReadOnly")}


def footer(h):
    return h.buttons(2)


def test_rows_show_on_state_order_and_counts():
    h = open_tab()
    assert row_texts(h) == [
        ("Azeroth Mixtape", "by NeedleDropper · 4 drops · Built-in · on, plays #1", True),
        ("Azeroth Unbound", "by NeedleDropper · 2 drops · Built-in · off", False),
    ]
    first = rows(h)[0]
    assert not h.enabled(first.On)                  # the last soundtrack that is on stays on
    h.hover(first.On)
    assert h.tooltip()[-1] == "At least one soundtrack stays on. Turn needle drops off in Settings instead."
    assert not h.shown(first.Up) and not h.shown(first.Down)
    for r in rows(h):
        assert r.GetHeight(r) == 44
    caption = h.window.soundtracks.Caption
    assert h.text(caption) == "When two soundtracks have a song for the same place, the one higher in this list plays."


def test_turning_soundtracks_on_and_off_and_reordering():
    h = open_tab()
    h.click(rows(h)[1].On)                          # Azeroth Unbound on: goes to the top
    assert h.active_refs() == ["builtin:azeroth-unbound", "builtin:azeroth-mixtape"]
    assert [t[0] for t in row_texts(h)] == ["Azeroth Unbound", "Azeroth Mixtape"]
    top, second = rows(h)
    assert h.shown(top.Up) and not h.enabled(top.Up) and h.enabled(top.Down)
    assert h.shown(second.Up) and h.enabled(second.Up) and not h.enabled(second.Down)
    h.click(top.Down)
    assert h.active_refs() == ["builtin:azeroth-mixtape", "builtin:azeroth-unbound"]
    h.click(rows(h)[1].On)                          # Unbound off again
    assert h.active_refs() == ["builtin:azeroth-mixtape"]
    # Turning one on below My Drops: My Drops stays first.
    h.S.Create("My Drops")
    h.set_active(["user:my-drops", "builtin:azeroth-mixtape"])
    h.flush()
    unbound = [r for r in rows(h) if h.text(r.Name) == "Azeroth Unbound"][0]
    h.click(unbound.On)
    assert h.active_refs() == ["user:my-drops", "builtin:azeroth-unbound", "builtin:azeroth-mixtape"]


def test_details_pane():
    h = open_tab()
    assert details(h) == {
        "Name": "Azeroth Mixtape",
        "Author": "by NeedleDropper",
        "Description": "The default NeedleDropper soundtrack.",
        "Tags": "Tags: default, fitting, funny",
        "Homepage": "",
        "Stats": "Plays at 4 places; 0 are covered by a soundtrack above.",
        "ReadOnly": ("Built-in soundtracks are read-only. Duplicate makes your own copy; Change song here on the "
                     "Drops tab changes one place."),
    }
    # My Drops above it covers one place.
    h.S.OverrideHere("area:87", h.table({"artist": "Rick Astley", "title": "Never Gonna Give You Up"}))
    h.advance(0.3)
    assert details(h)["Stats"] == "Plays at 4 places; 1 is covered by a soundtrack above."
    h.click(rows(h)[2])
    assert details(h)["Name"] == "Azeroth Unbound"
    assert details(h)["Stats"] == "Off: turn it on to hear its 2 places."


def test_new_rename_and_delete_use_static_popups():
    h = open_tab()
    b = footer(h)
    h.click(b.New)
    popup = h.popup("NEEDLEDROPPER_NEW")
    assert popup is not None
    eb = popup.GetEditBox(popup)
    assert h.has_focus(eb)
    assert not h.enabled(popup.GetButton1(popup))   # empty name
    h.type(eb, "Road Trip")
    assert h.enabled(popup.GetButton1(popup))
    h.enter(eb)                                     # Enter clicks the first button
    assert h.popup("NEEDLEDROPPER_NEW") is None
    assert h.S.Exists("user:road-trip")
    assert details(h)["Name"] == "Road Trip"
    assert details(h)["ReadOnly"] == ""

    h.click(b.Rename)
    popup = h.popup("NEEDLEDROPPER_RENAME")
    eb = popup.GetEditBox(popup)
    assert h.text(eb) == "Road Trip" and list(eb.__highlight.values()) == [0, 9]
    assert h.text(popup.Text) == "Rename Road Trip:"
    h.type(eb, "Long Drive")
    h.popup_accept("NEEDLEDROPPER_RENAME")
    assert h.user_set("road-trip")["name"] == "Long Drive"

    h.click(b.Delete)
    popup = h.popup("NEEDLEDROPPER_DELETE")
    assert h.text(popup.Text) == "Delete Long Drive and its 0 drops? This cannot be undone."
    assert h.text(popup.GetButton1(popup).__fs) == "Delete"
    assert popup.showAlert == 1
    h.popup_cancel("NEEDLEDROPPER_DELETE")
    assert h.S.Exists("user:road-trip")
    h.click(b.Delete)
    h.popup_accept("NEEDLEDROPPER_DELETE")
    assert not h.S.Exists("user:road-trip")
    # 12.x dialogs have no .editBox / .button1 fields: the handlers only use the accessors.
    assert popup.editBox is None and popup.button1 is None


def test_popup_escape_closes_the_popup_only():
    h = open_tab()
    h.click(footer(h).New)
    assert h.escape() == "editbox"                  # EditBoxOnEscapePressed hides the dialog
    assert h.popup("NEEDLEDROPPER_NEW") is None
    assert h.UI.IsShown()


def test_builtins_cannot_be_renamed_or_deleted():
    h = open_tab()
    b = footer(h)
    assert not h.enabled(b.Rename) and not h.enabled(b.Delete)
    assert h.enabled(b.Duplicate) and h.enabled(b.Share)
    h.hover(b.Delete)
    assert h.tooltip()[-1] == "Built-in soundtracks can't be deleted. Turn it off instead."


def test_duplicate_a_builtin():
    h = open_tab()
    h.click(footer(h).Duplicate)
    copies = [r for r in row_texts(h) if r[0] == "Azeroth Mixtape (my copy)"]
    assert copies and copies[0][2] is False          # arrives turned off
    assert details(h)["Name"] == "Azeroth Mixtape (my copy)"
    assert h.enabled(footer(h).Rename)


def test_share_and_import_open_their_dialogs():
    h = open_tab()
    h.click(footer(h).Share)
    share = h.g.NeedleDropperShareDialog
    assert h.shown(share)
    assert h.text(share.TitleContainer.TitleText) == "Share Azeroth Mixtape"
    h.click(share.CloseButton)
    h.click(footer(h).Import)
    assert h.shown(h.g.NeedleDropperImportDialog)
    # A soundtrack with no finished drops cannot be shared.
    h.S.Create("Empty One")
    h.flush()
    h.click([r for r in rows(h) if h.text(r.Name) == "Empty One"][0])
    assert not h.enabled(footer(h).Share)


def test_per_character_soundtracks_checkbox():
    h = open_tab()
    box = h.attic(2).PerCharacter
    assert not h.checked(box)
    h.click(box)
    assert h.fake_calls("SetUseCharacterList")[-1][1] is True
    assert h.char_db()["active"] == ["builtin:azeroth-mixtape"]
    h.click(rows(h)[1].On)
    assert h.char_db()["active"] == ["builtin:azeroth-unbound", "builtin:azeroth-mixtape"]
    assert h.account_db()["active"] == ["builtin:azeroth-mixtape"]
    h.click(box)
    assert "active" not in h.char_db()
    assert h.text(box.Text) == "Use different soundtracks on this character"
