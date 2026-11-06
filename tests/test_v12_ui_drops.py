"""Drops tab (DESIGN-v1.2 §4.4): list, selection, search / filter / Showing, Add drop here, the
detail form (place, links, file, More...), built-in banner (Change song here / Turn off here /
Revert), delete + undo, context menu, Test, escaping."""
from __future__ import annotations

import pytest

from v12_ui_harness import UIHarness

CHECK = "|TInterface\\RaidFrame\\ReadyCheck-Ready:0|t"
SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"


def at_razor_hill(**kw):
    """Durotar > Razor Hill (areaID 362, explored), where no soundtrack has a song."""
    return UIHarness(location=(1411, "Razor Hill"), state={"exploredAreas": {1411: [362]}}, **kw)


def add_draft(h):
    h.open()
    h.click(h.attic(1).AddButton)
    h.flush()
    return h.selected()


def drop(h, ref, drop_id):
    v = h.S.GetDrop(ref, drop_id)
    return h.from_lua(v) if v is not None else None


def row_texts(h):
    out = []
    for r in h.rows():
        out.append((h.text(r.Line1), h.text(r.Line2), h.text(r.Tags)))
    return out


# --------------------------------------------------------------- opening --

def test_open_selects_the_drop_for_the_current_place():
    h = UIHarness()                       # Elwynn Forest > Goldshire
    h.open()
    assert h.selected() == ("builtin:azeroth-mixtape", "area:87")
    form = h.form()
    assert h.text(form.BannerText) == "From Azeroth Mixtape (built-in)"
    assert h.shown(form.Change) and h.shown(form.TurnOff)
    assert h.text(form.PlaceText) == "Goldshire, Elwynn Forest"
    assert h.text(form.Artist) == "Toto" and h.text(form.Title) == "Africa"
    assert h.text(form.YouTube) == "https://youtu.be/FTQbiNvZqaY"
    for box in (form.Artist, form.Title, form.Spotify, form.YouTube, form.File):
        assert not h.enabled(box)          # built-ins are read-only
    assert not h.enabled(h.buttons(1).Delete)


def test_empty_pane_names_the_place_when_nothing_plays_here():
    h = at_razor_hill()
    h.open()
    assert h.selected() is None
    empty = h.drops().FormEmpty
    assert h.shown(empty)
    # A link alone makes a draft that never plays on arrival, so the hint asks for the names first
    # (UI review UI-LINK-ONLY-DRAFT / journey F5).
    assert h.text(empty) == ("No needle drop for Razor Hill yet. Press Add drop here, then type the artist and "
                             "title (and paste a Spotify or YouTube link if you have one).")
    assert not h.shown(h.form().scroll)


def test_rows_are_sorted_by_zone_then_landmark_with_letters_and_words():
    h = UIHarness()
    h.open()
    assert row_texts(h) == [
        ("Elwynn Forest", "Low Roar · I'll Keep Coming", "Y"),
        ("Elwynn Forest > Goldshire", "Toto · Africa", "Y  heard " + CHECK),   # it played at login
        ("The Deadmines", "Crew · Pirates", "Y"),
        ("Westfall", "Neil Young · Harvest Moon", "S"),
    ]
    for r in h.rows():
        assert r.GetHeight(r) == 36
    sel = h.row_for("builtin:azeroth-mixtape", "area:87")
    assert h.shown(sel.Selected) and h.shown(sel.Test)        # selected row shows [Test]
    other = h.row_for("builtin:azeroth-mixtape", "zone:1436")
    assert not h.shown(other.Test)
    h.hover(other)
    assert h.shown(other.Test)
    assert h.tooltip() == ["Westfall", "Neil Young · Harvest Moon", "Spotify", "from Azeroth Mixtape (built-in)",
                           "Right-click for options"]
    h.unhover(other)
    assert not h.shown(other.Test)


def test_search_matches_place_artist_title_and_note():
    h = UIHarness()
    h.open()
    search = h.attic(1).Search
    h.type(search, "TOTO")
    assert [t[0] for t in row_texts(h)] == ["Elwynn Forest > Goldshire"]
    h.type(search, "first inn")                      # the note
    assert [t[0] for t in row_texts(h)] == ["Elwynn Forest > Goldshire"]
    h.type(search, "elwynn")
    assert len(h.rows()) == 2
    h.type(search, "nothing like this")
    assert h.rows() == []
    assert h.text(h.drops().ListEmpty) == "No drops match the search and filter."
    h.type(search, "")
    assert len(h.rows()) == 4


def test_filter_dropdown():
    h = UIHarness()
    h.open()
    flt = h.attic(1).Filter
    items = [i["text"] for i in h.dropdown_items(flt)]
    assert items == ["Here", "Not heard yet (this character)", "Needs a song", "Changed by you", "In-game files",
                     "Turned off"]
    h.dropdown_pick(flt, "Here")
    assert [t[0] for t in row_texts(h)] == ["Elwynn Forest", "Elwynn Forest > Goldshire"]
    assert h.shown(flt.ResetButton)
    h.dropdown_pick(flt, "Not heard yet (this character)")
    assert [t[0] for t in row_texts(h)] == ["Elwynn Forest"]
    h.click(flt.ResetButton)
    assert len(h.rows()) == 4 and not h.shown(flt.ResetButton)
    h.dropdown_pick(flt, "Needs a song")
    assert h.rows() == []


def test_showing_dropdown_switches_the_view():
    h = UIHarness()
    h.open()
    dd = h.attic(1).Showing
    items = h.dropdown_items(dd)
    assert [(i["kind"], i.get("text")) for i in items] == [
        ("radio", "What plays (all active)"), ("divider", None), ("title", "Built-in"),
        ("radio", "Azeroth Mixtape"), ("radio", "Azeroth Unbound"), ("divider", None),
        ("button", "New soundtrack..."), ("button", "Import..."),
    ]
    assert h.text(dd.Text) == "What plays (all active)"
    h.dropdown_pick(dd, "Azeroth Unbound")
    assert h.text(dd.Text) == "Azeroth Unbound"
    assert h.account_db()["ui"]["showing"] == "builtin:azeroth-unbound"
    assert [t[0] for t in row_texts(h)] == ["Durotar", "Elwynn Forest"]
    h.dropdown_pick(dd, "What plays (all active)")
    assert len(h.rows()) == 4
    # New soundtrack... from the menu: the popup, then the view shows the new one.
    h.dropdown_pick(dd, "New soundtrack...")
    popup = h.popup("NEEDLEDROPPER_NEW")
    assert popup is not None
    h.type(popup.GetEditBox(popup), "Road Trip")
    h.popup_accept("NEEDLEDROPPER_NEW")
    h.flush()
    assert h.text(dd.Text) == "Road Trip"


# ---------------------------------------------------------- Add drop here --

def test_add_drop_here_creates_a_draft_in_my_drops_at_the_current_location():
    h = at_razor_hill()
    sel = add_draft(h)
    assert sel == ("user:my-drops", "area:362")
    assert h.active_refs()[0] == "user:my-drops"            # created and put first
    d = drop(h, "user:my-drops", "area:362")
    assert (d["kind"], d["mapID"], d["areaID"], d["subzone"], d["label"]) == ("subzone", 1411, 362, "Razor Hill", "Razor Hill")
    assert "artist" not in d and "title" not in d
    form = h.form()
    assert h.has_focus(form.Artist)                          # the cursor goes to Artist
    assert h.enabled(form.Artist)
    assert list(h.fakes.suppressed.values()) == ["area:362"] # doesn't fire mid-edit
    assert ("Durotar > Razor Hill", "needs a song", "yours") in row_texts(h)
    add = h.fake_calls("AddDrop")
    assert len(add) == 1 and add[0][1] == "user:my-drops"


def test_add_drop_here_in_a_zone_and_in_an_instance():
    h = UIHarness(location=(1411, ""))
    assert add_draft(h) == ("user:my-drops", "zone:1411")
    h2 = UIHarness(location=(1411, "", {"id": 48, "name": "Blackfathom Deeps"}))
    assert add_draft(h2) == ("user:my-drops", "inst:48")
    d = drop(h2, "user:my-drops", "inst:48")
    assert d["kind"] == "instance" and d["label"] == "Blackfathom Deeps"


def test_add_drop_here_selects_the_drop_that_already_covers_the_place():
    h = UIHarness()                                          # Goldshire is a built-in place
    h.open()
    h.click(h.window.drops.FormPane.Form.Change)             # now My Drops overrides it
    h.flush()
    h.dropdown_pick(h.attic(1).Showing, "What plays (all active)")
    h.click(h.attic(1).AddButton)
    h.flush()
    assert h.selected() == ("user:my-drops", "area:87")
    assert len(h.fake_calls("AddDrop")) == 0
    # A landmark the client cannot resolve to an areaID still matches by name.
    h2 = UIHarness()
    h2.open()
    h2.click(h2.attic(1).AddButton)
    assert h2.selected() == ("builtin:azeroth-mixtape", "area:87")
    assert len(h2.fake_calls("AddDrop")) == 0


def test_add_drop_here_into_the_soundtrack_being_shown():
    h = at_razor_hill()
    h.open()
    ref = h.S.Create("Road Trip")
    h.flush()
    h.dropdown_pick(h.attic(1).Showing, "Road Trip")
    h.click(h.attic(1).AddButton)
    h.flush()
    assert h.selected() == (ref, "area:362")
    assert not h.S.Exists("user:my-drops")


def test_slash_add_with_artist_and_title_commits_and_prints():
    h = at_razor_hill()
    before = len(h.prints())
    ok = h.UI.AddHere("Toto - Africa")
    h.check_errors()
    assert ok
    d = drop(h, "user:my-drops", "area:362")
    assert (d["artist"], d["title"]) == ("Toto", "Africa")
    assert h.prints()[before:] == ["NeedleDropper: Added Razor Hill: Toto · Africa to My Drops."]
    assert not h.UI.IsShown()                                # finished: no window needed
    # A built-in place: My Drops overrides it under the built-in's id.
    h2 = UIHarness()
    h2.UI.AddHere("Rick Astley - Never Gonna Give You Up")
    h2.check_errors()
    assert h2.fake_calls("OverrideHere")[0][1:] == ["area:87", {"artist": "Rick Astley", "title": "Never Gonna Give You Up"}]


def test_slash_add_with_a_link_keeps_a_draft_and_opens_the_window():
    h = at_razor_hill()
    h.UI.AddHere("https://open.spotify.com/track/%s?si=abc" % SPOTIFY_ID)
    h.check_errors()
    d = drop(h, "user:my-drops", "area:362")
    assert d["spotify"] == SPOTIFY_ID and "artist" not in d
    assert h.prints()[-1] == "NeedleDropper: Added Razor Hill to My Drops. Type the artist and title to finish it."
    assert h.UI.IsShown() and h.selected() == ("user:my-drops", "area:362")
    assert h.has_focus(h.form().Artist)
    h.UI.AddHere("just some words")
    h.check_errors()
    assert h.prints()[-1] == "NeedleDropper: Use /needle add Artist - Title, or paste a Spotify or YouTube song link."


# ------------------------------------------------------------------- form --

def test_fields_commit_on_tab_enter_and_focus_loss():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.type(form.Artist, "  Toto  ")
    h.tab_key()                                              # focus leaves -> commit
    assert h.has_focus(form.Title)
    assert drop(h, "user:my-drops", "area:362")["artist"] == "Toto"
    h.type(form.Title, "Africa")
    h.enter()
    assert drop(h, "user:my-drops", "area:362")["title"] == "Africa"
    assert h.focused() is None
    h.advance(0.3)                                           # the debounced rebuild
    assert ("Durotar > Razor Hill", "Toto · Africa", "yours") in row_texts(h)
    ups = h.fake_calls("UpdateDrop")
    assert [u[3] for u in ups] == [{"artist": "Toto"}, {"title": "Africa"}]
    # Same text again: nothing is written.
    h.type(form.Title, "Africa")
    h.enter()
    assert len(h.fake_calls("UpdateDrop")) == 2


def test_escape_restores_the_field():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.type(form.Artist, "Toto")
    h.enter()
    h.type(form.Artist, "Oops")
    assert h.escape() == "editbox"
    assert h.text(form.Artist) == "Toto"
    assert drop(h, "user:my-drops", "area:362")["artist"] == "Toto"
    assert len(h.fake_calls("UpdateDrop")) == 1


def test_spotify_links_are_parsed_and_explained():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.paste(form.Spotify, "https://open.spotify.com/intl-de/track/%s?si=0123abcd" % SPOTIFY_ID)
    assert h.text(form.SpotifyStatus) == "Spotify song " + CHECK      # live, before committing
    h.enter()
    assert drop(h, "user:my-drops", "area:362")["spotify"] == SPOTIFY_ID
    assert h.text(form.Spotify) == "https://open.spotify.com/track/" + SPOTIFY_ID
    h.paste(form.Spotify, "spotify:track:%s" % SPOTIFY_ID)            # same song: no write
    h.enter()
    assert len(h.fake_calls("UpdateDrop")) == 1
    h.paste(form.Spotify, "https://open.spotify.com/album/1A2GTWGtFfWp7KSQTwWOyo?si=x")
    want = "That's an album or playlist link: in Spotify right-click the song > Share > Copy Song Link"
    assert h.text(form.SpotifyStatus) == want
    h.enter()
    assert drop(h, "user:my-drops", "area:362")["spotify"] == SPOTIFY_ID   # not committed
    assert h.text(form.SpotifyStatus) == want
    h.paste(form.Spotify, "")
    h.enter()
    assert "spotify" not in drop(h, "user:my-drops", "area:362")


def test_youtube_links_and_the_start_offer():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.paste(form.YouTube, "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=83s")
    assert h.text(form.YouTubeStatus) == "YouTube video " + CHECK
    h.enter()
    d = drop(h, "user:my-drops", "area:362")
    assert d["youtube"] == "dQw4w9WgXcQ"
    assert h.text(form.YouTube) == "https://youtu.be/dQw4w9WgXcQ"
    # The offer comes from the link text while it is in the box.
    h.paste(form.YouTube, "https://youtu.be/dQw4w9WgXcQ?t=1m23s")
    assert h.shown(form.UseStart) and h.text(form.UseStart.Text) == "Start at 1:23?"
    h.click(form.UseStart)
    assert drop(h, "user:my-drops", "area:362")["startMs"] == 83000
    h.paste(form.YouTube, "https://www.youtube.com/playlist?list=PL1234567890")
    assert h.text(form.YouTubeStatus) == "That's a playlist link: open the video itself and copy its link"
    for link in ("https://m.youtube.com/shorts/abcdefghijk", "https://music.youtube.com/watch?v=abcdefghijk",
                 "youtube.com/embed/abcdefghijk", "https://www.youtube.com/live/abcdefghijk?si=1"):
        h.paste(form.YouTube, link)
        assert h.text(form.YouTubeStatus) == "YouTube video " + CHECK, link


def test_in_game_file_status():
    path = "Interface\\AddOns\\NeedleDropper_Local\\media\\razorhill.mp3"
    h = at_razor_hill(known_files=[path])
    add_draft(h)
    form = h.form()
    h.type(form.File, "razorhill.mp3")
    assert h.text(form.FileStatus) == "found " + CHECK
    h.enter()
    assert drop(h, "user:my-drops", "area:362")["file"] == "razorhill.mp3"
    h.type(form.File, "missing.ogg")
    assert h.text(form.FileStatus) == "not found: copy it there and restart WoW (a /reload is not enough)"
    h.type(form.File, "..\\secret.mp3")
    assert h.text(form.FileStatus) == "Use just the file name, without folders."
    h.type(form.File, "song.wav")
    assert h.text(form.FileStatus) == "In-game files must be .mp3 or .ogg."
    h.enter()
    assert drop(h, "user:my-drops", "area:362")["file"] == "razorhill.mp3"


def test_more_section_start_length_volume_tags_note_and_off():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    assert not h.shown(form.timingLine)                      # collapsed by default
    h.click(form.More)
    for key in ("timingLine", "volumeLine", "tagsLine", "noteLine", "offLine"):
        assert h.shown(form[key]), key
    h.type(form.Start, "1:23")
    h.enter()
    h.type(form.Length, "3:30")
    h.enter()
    d = drop(h, "user:my-drops", "area:362")
    assert (d["startMs"], d["durationMs"]) == (83000, 210000)
    h.type(form.Length, "soon")
    h.enter()
    assert h.text(form.ErrorText).startswith("Use minutes:seconds")
    h.escape() if h.focused() is not None else None
    h.set_slider(form.Volume, 60)
    assert h.text(form.Volume.RightText) == "60%"            # the label follows at once ...
    assert drop(h, "user:my-drops", "area:362").get("volume") is None
    h.advance(0.5)                                           # ... the drop is saved once the slider settles
    assert drop(h, "user:my-drops", "area:362")["volume"] == 60
    h.click(form.Fitting)
    assert drop(h, "user:my-drops", "area:362")["tags"] == ["fitting"]
    h.type(form.Tags, "night, chill , night")
    h.enter()
    assert drop(h, "user:my-drops", "area:362")["tags"] == ["fitting", "night", "chill"]
    note = form.Note
    nb = note.GetEditBox(note)
    h.focus(nb)
    h.type(nb, "Dust and drums.")
    h.focus(form.Artist)                                     # the note loses focus -> saved next frame
    h.flush()
    assert drop(h, "user:my-drops", "area:362")["note"] == "Dust and drums."
    # Escape in the note restores the saved text.
    h.focus(nb)
    h.type(nb, "Changed my mind")
    assert h.escape() == "editbox"
    h.flush()
    assert drop(h, "user:my-drops", "area:362")["note"] == "Dust and drums."
    assert h.text(nb) == "Dust and drums."
    # Moving to another drop right after typing still saves the note to the drop it belongs to.
    h.focus(nb)
    h.type(nb, "Saved on the way out")
    h.click(h.row_for("builtin:azeroth-mixtape", "zone:1429"))
    h.flush()
    assert drop(h, "user:my-drops", "area:362")["note"] == "Saved on the way out"
    h.click(h.row_for("user:my-drops", "area:362"))
    h.click(form.Off)
    assert drop(h, "user:my-drops", "area:362").get("disabled") is True
    assert h.checked(form.Off)
    assert not h.shown(form.artistLine)                      # a turned-off draft has no song to edit
    h.click(form.Off)
    assert drop(h, "user:my-drops", "area:362").get("disabled") is None


def test_tab_order_runs_through_the_visible_fields():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    order = []
    box = form.Artist
    for _ in range(7):
        order.append(box)
        h.tab_key(box)
        box = h.focused()
    names = []
    for b in order:
        for k in ("Landmark", "Artist", "Title", "Spotify", "YouTube", "File", "Start", "Length", "Tags"):
            if h.same(form[k], b):
                names.append(k)
    assert names == ["Artist", "Title", "Spotify", "YouTube", "File", "Landmark", "Artist"]
    h.tab_key(form.Title, shift=True)
    assert h.has_focus(form.Artist)


def test_place_scope_zone_and_landmark():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    assert h.checked(form.ThisLandmark) and not h.checked(form.WholeZone)
    h.click(form.WholeZone)
    assert h.selected() == ("user:my-drops", "zone:1411")    # a new place, a new id
    assert h.text(form.PlaceText) == "Durotar (whole zone)"
    h.click(form.ThisLandmark)
    assert h.shown(form.Landmark) and h.has_focus(form.Landmark)
    assert h.text(form.Landmark) == "Razor Hill"             # where the player stands
    h.enter()
    assert h.selected() == ("user:my-drops", "area:362")
    h.type(form.Landmark, "Tiragarde Keep")                  # not where the player is: saved by name
    h.enter()
    assert h.selected() == ("user:my-drops", "sub:1411:tiragarde-keep")
    assert h.text(form.PlaceNote) == "Landmark saved by name: works on English clients only."
    zones = [i for i in h.dropdown_items(form.Zone)]
    titles = [i["text"] for i in zones if i["kind"] == "title"]
    assert titles == ["Eastern Kingdoms", "Kalimdor", "Azeroth"]    # Azeroth: zones with no continent
    radios = [i["text"] for i in zones if i["kind"] == "radio"]
    assert "Elwynn Forest" in radios and "Westfall" in radios and "Durotar" in radios
    assert "Zephras Isle" in radios                          # parent 947 itself (UI review F9)
    assert [i["text"] for i in zones if i["kind"] == "radio" and i["selected"]] == ["Durotar"]
    h.dropdown_pick(form.Zone, "Westfall")
    assert h.selected() == ("user:my-drops", "sub:1436:tiragarde-keep")


def test_use_my_location_moves_the_drop():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.set_location(1436, "Sentinel Hill")
    h.click(form.UseLocation)
    assert h.selected() == ("user:my-drops", "sub:1436:sentinel-hill")


# ------------------------------------------------------- built-in drops --

def test_change_song_here_copies_into_my_drops_and_focuses_artist():
    h = UIHarness()
    h.open()
    form = h.form()
    h.click(form.Change)
    assert h.fake_calls("OverrideHere")[0][1] == "area:87"
    assert h.selected() == ("user:my-drops", "area:87")
    assert h.active_refs() == ["user:my-drops", "builtin:azeroth-mixtape"]
    assert h.enabled(form.Artist) and h.has_focus(form.Artist)
    assert list(form.Artist.__highlight.values()) == [0, 4]   # typing replaces "Toto"
    assert h.text(form.BannerText) == "Your song here replaces Azeroth Mixtape's."
    assert h.shown(form.Revert) and h.text(form.Revert.Text) == "Revert to Azeroth Mixtape"
    h.type(form.Artist, "Rick Astley")
    h.enter()
    assert drop(h, "user:my-drops", "area:87")["artist"] == "Rick Astley"
    assert h.enabled(h.buttons(1).Delete)


def test_turn_off_here_and_revert():
    h = UIHarness()
    h.open()
    form = h.form()
    h.click(form.TurnOff)
    assert h.fake_calls("TurnOffHere")[0][1] == "area:87"
    assert h.selected() == ("user:my-drops", "area:87")
    assert h.text(form.BannerText) == "Turned off here: Azeroth Mixtape's song does not play."
    h.advance(0.3)
    assert ("Elwynn Forest > Goldshire", "turned off here", "off") in row_texts(h)
    assert not h.shown(form.artistLine)                    # nothing to edit on a turned-off place
    h.click(form.Revert)
    assert h.fake_calls("Revert")[0][1] == "area:87"
    assert h.selected() == ("builtin:azeroth-mixtape", "area:87")
    assert h.S.GetDrop("user:my-drops", "area:87") is None


def test_builtin_overridden_by_my_drops_offers_show_what_plays():
    h = UIHarness()
    h.open()
    h.click(h.form().Change)
    h.dropdown_pick(h.attic(1).Showing, "Azeroth Mixtape")
    h.click(h.row_for("builtin:azeroth-mixtape", "area:87"))
    form = h.form()
    assert h.text(form.BannerText) == "From Azeroth Mixtape (built-in)\nMy Drops plays here instead."
    assert h.shown(form.ShowPlaying) and not h.shown(form.Change)
    assert "overridden" in h.text(h.row_for("builtin:azeroth-mixtape", "area:87").Tags)
    h.click(form.ShowPlaying)
    assert h.selected() == ("user:my-drops", "area:87")


# ------------------------------------------------------------ delete / undo --

def test_delete_at_once_with_an_undo_bar_for_15_seconds():
    h = at_razor_hill()
    add_draft(h)
    undo = h.drops().UndoBar
    h.click(h.buttons(1).Delete)
    assert h.S.GetDrop("user:my-drops", "area:362") is None
    assert h.selected() is None
    assert h.shown(undo) and h.text(undo.Text) == "Deleted Razor Hill."
    assert h.popup("NEEDLEDROPPER_DELETE") is None           # no confirmation
    h.click(undo.Button)
    assert h.S.GetDrop("user:my-drops", "area:362") is not None
    assert h.selected() == ("user:my-drops", "area:362")
    assert not h.shown(undo)
    h.click(h.buttons(1).Delete)
    h.advance(14)
    assert h.shown(undo)
    h.advance(1.5)
    assert not h.shown(undo)


def test_delete_is_disabled_for_builtins_with_the_reason():
    h = UIHarness()
    h.open()
    delete = h.buttons(1).Delete
    assert not h.enabled(delete)
    h.hover(delete)
    assert h.tooltip()[-1] == "Built-in drops can't be deleted: use Turn off here."


# ------------------------------------------------------------ context menu --

def test_context_menu_for_builtin_and_user_drops():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.type(form.Artist, "A")
    h.tab_key()
    h.type(form.Title, "B")
    h.enter()
    other = h.S.Create("Road Trip")
    h.advance(0.3)
    h.right_click(h.row_for("builtin:azeroth-mixtape", "zone:1429"))
    texts = [i["path"] for i in h.context_items()]
    assert texts == ["Elwynn Forest", "Test", "Change song here", "Turn off here", "Copy to",
                     "Copy to > My Drops", "Copy to > Road Trip"]
    h.right_click(h.row_for("user:my-drops", "area:362"))
    items = h.context_items()
    texts = [i["path"] for i in items if i["kind"] != "divider"]
    assert texts == ["Razor Hill", "Test", "Edit", "Turn off here", "Copy to", "Copy to > Road Trip", "Move to",
                     "Move to > Road Trip", "Delete"]
    assert [i["kind"] for i in items][-2:] == ["divider", "button"]
    h.context_pick("Move to", "Road Trip")
    assert h.S.GetDrop("user:my-drops", "area:362") is None
    assert h.S.GetDrop(other, "area:362") is not None
    assert h.selected() == (other, "area:362")


def test_context_menu_test_and_turn_off():
    h = UIHarness()
    h.open()
    h.right_click(h.row_for("builtin:azeroth-mixtape", "zone:1436"))
    h.context_pick("Test")
    played = h.play_tests()[-1]
    assert played["id"] == "zone:1436" and played["spotify"] == SPOTIFY_ID
    h.right_click(h.row_for("builtin:azeroth-mixtape", "zone:1436"))
    h.context_pick("Turn off here")
    assert h.fake_calls("TurnOffHere")[0][1] == "zone:1436"


# -------------------------------------------------------------------- Test --

def test_test_plays_the_form_even_for_an_unsaved_draft():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.paste(form.YouTube, "https://youtu.be/dQw4w9WgXcQ")   # not committed yet
    h.click(h.buttons(1).Test)
    played = h.play_tests()[-1]
    assert played["id"] == "area:362" and played["youtube"] == "dQw4w9WgXcQ"
    assert "artist" not in played
    assert "youtube" not in drop(h, "user:my-drops", "area:362")
    assert h.ns.Director.active is not None and h.ns.Director.active.isTest
    h.click(h.buttons(1).Stop)
    assert h.ns.Director.active is None


def test_test_plays_an_in_game_file_by_its_full_path():
    path = "Interface\\AddOns\\NeedleDropper_Local\\media\\razorhill.mp3"
    h = UIHarness(location=(1411, "Razor Hill"), known_files=[path],
                  state={"exploredAreas": {1411: [362]}, "playableFiles": {path: True}})
    add_draft(h)
    form = h.form()
    h.type(form.File, "razorhill.mp3")
    h.enter()
    assert drop(h, "user:my-drops", "area:362")["file"] == "razorhill.mp3"   # stored bare (DESIGN 4.2)
    h.click(h.buttons(1).Test)
    played = h.play_tests()[-1]
    assert (played["file"], played["fileName"]) == (path, "razorhill.mp3")
    assert h.ns.Director.active.isLocal                                      # the game plays it itself
    assert h.text(h.window.Status) == "Playing: Razor Hill (in-game file)"


def test_test_tooltip_explains_the_app_when_it_is_missing():
    h = UIHarness()
    h.open()
    h.hover(h.buttons(1).Test)
    assert h.tooltip()[-1] == ("Spotify and YouTube songs are played by the NeedleDropper app, which has not been "
                               "seen on this PC.")


def test_spotify_match_from_the_app_can_be_used():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.type(form.Artist, "Toto")
    h.tab_key()
    h.type(form.Title, "Africa")
    h.enter()
    h.fakes.tracks["q:toto\x1fafrica"] = h.table({"spotify": SPOTIFY_ID, "title": "Africa", "artist": "Toto",
                                                 "durationMs": 295000})
    h.UI.Refresh()
    h.check_errors()
    assert h.shown(form.matchLine)
    assert h.text(form.MatchText) == "Spotify match: Africa · Toto (4:55)"
    h.click(form.UseMatch)
    assert drop(h, "user:my-drops", "area:362")["spotify"] == SPOTIFY_ID
    assert not h.shown(form.matchLine)


# ---------------------------------------------------------------- escaping --

def test_text_from_players_is_escaped_everywhere():
    h = at_razor_hill()
    add_draft(h)
    form = h.form()
    h.type(form.Artist, "Lo|Fi |cZZ")
    h.tab_key()
    h.type(form.Title, "|TInterface\\Icons\\Spell_Fire:0|t Hot")
    h.enter()
    h.advance(0.3)
    row = h.row_for("user:my-drops", "area:362")
    assert h.text(row.Line2) == "Lo||Fi ||cZZ · ||TInterface\\Icons\\Spell_Fire:0||t Hot"
    h.hover(row)
    assert "Lo||Fi ||cZZ · ||TInterface\\Icons\\Spell_Fire:0||t Hot" in h.tooltip()
    assert h.text(form.Artist) == "Lo|Fi |cZZ"             # edit boxes keep the raw text
    assert h.bad_escapes() == []
