"""The window against A1's real model (Locale, Sets, Soundtracks, Share, Companion) instead of the
CONTRACTS 2.5-2.7 stand-ins. Skipped until those files exist."""
from __future__ import annotations

import pytest

from v12_ui_harness import SHIM_ENCODING, UIHarness, a1_available

pytestmark = pytest.mark.skipif(not a1_available(), reason="A1's model modules are not in the repo yet")

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"


def real(**kw):
    return UIHarness(real=True, **kw)


def drop(h, ref, drop_id):
    v = h.S.GetDrop(ref, drop_id)
    return h.from_lua(v) if v is not None else None


def test_window_tabs_and_lists_on_the_real_model():
    h = real()                                          # Elwynn Forest > Goldshire
    h.open()
    assert h.selected() == ("builtin:azeroth-mixtape", "area:87")
    assert len(h.rows()) >= 30                          # Azeroth Mixtape's places
    assert h.text(h.form().BannerText) == "From Azeroth Mixtape (built-in)"
    h.open("soundtracks")
    lst = h.window.soundtracks.List
    names = [h.text(r.Name) for r in lst.GetFrames(lst).values()]
    assert names[:2] == ["Azeroth Mixtape", "Azeroth Unbound"]
    h.open("journal")
    h.open("drops")
    assert h.bad_escapes() == []


def test_add_edit_change_revert_delete_undo_on_the_real_model():
    h = real(location=(1411, "Razor Hill"), state={"exploredAreas": {1411: [362]}})
    h.open()
    h.click(h.attic(1).AddButton)
    h.flush()
    assert h.selected() == ("user:my-drops", "area:362")
    assert h.active_refs()[0] == "user:my-drops"
    form = h.form()
    assert h.has_focus(form.Artist)
    h.type(form.Artist, "Toto")
    h.tab_key()
    h.type(form.Title, "Africa")
    h.enter()
    h.paste(form.Spotify, "https://open.spotify.com/track/%s?si=1" % SPOTIFY_ID)
    h.enter()
    d = drop(h, "user:my-drops", "area:362")
    assert (d["artist"], d["title"], d["spotify"]) == ("Toto", "Africa", SPOTIFY_ID)
    h.advance(0.3)
    # Change song here on a built-in drop, then Revert.
    h.click(h.row_for("builtin:azeroth-mixtape", "zone:1411"))
    h.click(form.Change)
    assert h.selected() == ("user:my-drops", "zone:1411")
    h.type(form.Artist, "Someone Else")
    h.enter()
    assert drop(h, "user:my-drops", "zone:1411")["artist"] == "Someone Else"
    h.click(form.Revert)
    assert h.selected() == ("builtin:azeroth-mixtape", "zone:1411")
    assert h.S.GetDrop("user:my-drops", "zone:1411") is None
    # Delete + Undo.
    h.click(h.row_for("user:my-drops", "area:362"))
    h.click(h.buttons(1).Delete)
    assert h.S.GetDrop("user:my-drops", "area:362") is None
    h.click(h.drops().UndoBar.Button)
    assert h.S.GetDrop("user:my-drops", "area:362") is not None
    assert h.bad_escapes() == []


def test_soundtrack_popups_on_the_real_model():
    h = real(location=(1411, ""))
    h.open("soundtracks")
    b = h.buttons(2)
    h.click(b.New)
    popup = h.popup("NEEDLEDROPPER_NEW")
    h.type(popup.GetEditBox(popup), "Road Trip")
    h.popup_accept("NEEDLEDROPPER_NEW")
    assert h.S.Exists("user:road-trip")
    h.click(b.Rename)
    popup = h.popup("NEEDLEDROPPER_RENAME")
    h.type(popup.GetEditBox(popup), "Long Drive")
    h.popup_accept("NEEDLEDROPPER_RENAME")
    assert h.from_lua(h.S.Get("user:road-trip"))["name"] == "Long Drive"
    h.click(b.Delete)
    h.popup_accept("NEEDLEDROPPER_DELETE")
    assert not h.S.Exists("user:road-trip")


@pytest.mark.skipif(not SHIM_ENCODING.exists(), reason="no C_EncodingUtil shim")
def test_share_then_import_round_trip_on_the_real_model():
    h = real(location=(1411, ""))
    ref = h.S.Create("Road Trip")
    h.S.AddDrop(ref, h.table({"mapID": 1411, "artist": "Me", "title": "Home", "youtube": "dQw4w9WgXcQ"}))
    h.check_errors()
    assert h.UI.OpenShare(ref)
    h.check_errors()
    s = h.g.NeedleDropperShareDialog
    eb = s.Code.GetEditBox(s.Code)
    code = h.text(eb)
    assert code.startswith("!NeedleDropper:1!") and code.endswith("!"), h.text(s.Error)
    h.UI.OpenImport()
    d = h.g.NeedleDropperImportDialog
    h.paste(d.Code.GetEditBox(d.Code), code)
    assert h.text(d.Header).startswith("Road Trip by ")
    assert h.enabled(d.ImportButton)
    h.type(d.Name, "Road Trip Copy")
    h.click(d.ImportButton)
    assert h.text(d.Status) == "Imported Road Trip Copy (off)."
    assert h.bad_escapes() == []
