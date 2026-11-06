"""Import and Share dialogs (DESIGN-v1.2 §5.6): decode on the player's own paste (once, even though
the client fires OnTextChanged twice), preview + warnings, Import (off) / Update / Turn on; the
share code shown highlighted and read-only, the author remembered."""
from __future__ import annotations

from v12_ui_harness import UIHarness

PACK = {
    "format": "needledropper-pack", "version": 1, "id": "road-songs", "name": "Road Songs", "author": "Ann",
    "description": "For the long ride", "tags": [], "revision": 3,
    "drops": [
        {"zone": 1429, "areaID": 87, "subzone": "Goldshire", "artist": "Toto", "title": "Africa",
         "youtube": "FTQbiNvZqaY"},
        {"zone": 1453, "subzone": "Trade District", "artist": "Band", "title": "Trade Song",
         "spotify": "spotify:track:4uLU6hMCjMI75M1A2tKUQC"},
        {"zone": 77777, "artist": "Who", "title": "Where"},
        {"zone": 1411, "artist": "Me", "title": "Home", "file": "home.mp3", "inGameFile": True},
    ],
}
WARNINGS = ["drops[2]: unknown key \"youtub\" ignored"]


def setup(**kw):
    h = UIHarness(location=(1411, ""), **kw)
    code = str(h.fakes.AddCode("Uk9BRFNPTkdT", h.table(PACK), h.table(WARNINGS)))
    return h, code


def dialog(h):
    return h.g.NeedleDropperImportDialog


def code_box(d):
    return d.Code.GetEditBox(d.Code)


def test_import_dialog_basics():
    h, _ = setup()
    assert h.UI.OpenImport()
    h.check_errors()
    d = dialog(h)
    assert h.shown(d) and "NeedleDropperImportDialog" in h.special_frames()
    assert d.__template == "ButtonFrameTemplate"
    eb = code_box(d)
    assert eb.__maxLetters == 65536 and h.count(eb, "SetMaxLetters") == 1
    assert eb.__autoFocus is False
    assert h.has_focus(eb)                                   # ready for Ctrl+V
    assert not h.enabled(d.ImportButton)
    h.hover(d.ImportButton)
    assert h.tooltip()[-1] == "Paste a valid share code first."
    assert h.text(d.Status) == "Paste a share code above. Nothing is imported until you press Import."
    assert not h.same(d.CloseButton.GetScript(d.CloseButton, "OnClick"), h.g.UIPanelCloseButton_OnClick)


def test_paste_decodes_once_and_shows_the_preview():
    h, code = setup()
    h.UI.OpenImport()
    d = dialog(h)
    h.paste(code_box(d), "```\n" + code + "\n```")           # fires OnTextChanged twice
    assert len(h.fake_calls("Share.Decode")) == 1
    assert h.text(d.Header) == "Road Songs by Ann, revision 3: 4 drops"
    assert h.text(d.Places).split("\n") == [
        "Elwynn Forest > Goldshire · Toto · Africa  Y",
        "Stormwind City > Trade District · Band · Trade Song  S",
        "Zone 77777 · Who · Where",
        "Durotar · Me · Home  F",
    ]
    assert h.text(d.Warnings).split("\n") == [
        "1 places are in zones this game client does not know; they will not play.",
        "1 drops play in-game files you may not have: copy them to NeedleDropper_Local\\media, then restart WoW.",
        "1 landmarks are saved by name: they only work on game clients in the language the author used.",
        "Turned on at the top, it would change the song at 1 places you hear now.",
        "drops[2]: unknown key \"youtub\" ignored",
    ]
    assert h.text(d.Name) == "Road Songs" and h.shown(d.Name)
    assert h.enabled(d.ImportButton)
    assert not h.shown(d.UpdateButton)
    # A code change made by the addon itself is not a paste: no decode.
    d.Code.SetText(d.Code, code)
    h.check_errors()
    assert len(h.fake_calls("Share.Decode")) == 1


def test_bad_codes_say_what_is_wrong():
    h, code = setup()
    h.UI.OpenImport()
    d = dialog(h)
    cases = [
        ("!WA:2!abcdef", "This is a WeakAuras string, not a NeedleDropper soundtrack."),
        (code[:-1], "The code was cut off: copy all of it, up to the ! at the end."),
        ("!NeedleDropper:2!QUJD!", "This code was made with a newer NeedleDropper: update the addon."),
        ("!NeedleDropper:1!QUJD!", "The code is damaged: copy it again."),
        ("hello there", "That is not a NeedleDropper share code. Share codes start with !NeedleDropper:1!"),
    ]
    for text, want in cases:
        h.paste(code_box(d), text)
        assert h.text(d.Status) == want, text
        assert not h.enabled(d.ImportButton)
        assert h.text(d.Header) == ""
    h.paste(code_box(d), "")
    assert h.text(d.Status) == "Paste a share code above. Nothing is imported until you press Import."


def test_import_arrives_off_and_can_be_turned_on():
    h, code = setup()
    h.UI.OpenImport()
    d = dialog(h)
    h.paste(code_box(d), code)
    h.type(d.Name, "My Road")
    h.click(d.ImportButton)
    call = h.fake_calls("Share.Import")[-1]
    assert call[1]["id"] == "road-songs" and call[2] == {"name": "My Road"}
    ref = "user:road-songs"
    assert h.S.Exists(ref) and ref not in h.active_refs()
    assert h.text(d.Status) == "Imported My Road (off)."
    assert h.shown(d.TurnOnButton) and not h.shown(d.ImportButton)
    assert h.text(d.CancelButton.Text) == "Close"
    h.click(d.TurnOnButton)
    assert h.active_refs()[0] == ref
    assert h.text(d.Status) == "Imported My Road. It is on, at the top of your soundtracks."
    assert not h.shown(d.TurnOnButton)


def test_update_is_offered_for_a_newer_revision_from_the_same_author():
    h, code = setup()
    old = dict(PACK, revision=1)
    h.ns.Share.Import(h.table(old), h.table({}))
    h.check_errors()
    h.UI.OpenImport(code)                                    # prefilled (the app's migration offer)
    h.check_errors()
    d = dialog(h)
    assert len(h.fake_calls("Share.Decode")) == 1
    assert h.shown(d.UpdateButton) and h.text(d.UpdateButton.Text) == "Update Road Songs"
    h.click(d.UpdateButton)
    assert h.fake_calls("Share.Update")[-1][1] == "user:road-songs"
    assert h.text(d.Status) == "Updated Road Songs. It keeps its on/off state and its place in the list."


def test_import_dialog_hides_in_combat_and_escape_closes_it():
    h, code = setup()
    h.UI.OpenImport()
    d = dialog(h)
    h.combat(True)
    assert not h.shown(d)
    h.combat(False)
    assert h.shown(d)
    h.focus(code_box(d))
    assert h.escape() == "editbox"
    assert h.escape() == "closed"
    assert not h.shown(d)


def share_dialog(h):
    return h.g.NeedleDropperShareDialog


def make_user_set(h):
    ref = h.S.Create("Road Trip")
    h.S.AddDrop(ref, h.table({"mapID": 1411, "artist": "Me", "title": "Home"}))
    h.S.AddDrop(ref, h.table({"mapID": 1436}))               # a draft: not shared
    h.check_errors()
    return ref


def test_share_dialog_shows_the_code_highlighted_and_focused():
    h, _ = setup()
    ref = make_user_set(h)
    assert h.UI.OpenShare(ref)
    h.check_errors()
    s = share_dialog(h)
    assert "NeedleDropperShareDialog" in h.special_frames()
    eb = s.Code.GetEditBox(s.Code)
    code = h.text(eb)
    assert code.startswith("!NeedleDropper:1!") and code.endswith("!")
    assert h.has_focus(eb)
    assert list(eb.__highlight.values()) == [0, len(code)]
    assert h.text(s.Count) == "%d characters" % len(code)
    assert h.text(s.Omitted) == "1 unfinished drop was not included."
    assert h.text(s.Hint) == ("Press Ctrl+C to copy. In Discord, paste inside ``` ; long codes arrive as message.txt, "
                              "which works.")
    assert h.text(s.TitleContainer.TitleText) == "Share Road Trip"
    assert h.fake_calls("Share.Encode")[-1][1]["author"] == ""     # empty by default (privacy)


def test_share_code_box_is_read_only():
    h, _ = setup()
    h.UI.OpenShare(make_user_set(h))
    s = share_dialog(h)
    eb = s.Code.GetEditBox(s.Code)
    code = h.text(eb)
    h.type(eb, "something else")
    assert h.text(eb) == code
    assert list(eb.__highlight.values()) == [0, len(code)]
    h.click(s.SelectButton)
    assert list(eb.__highlight.values()) == [0, len(code)]


def test_share_author_is_remembered():
    h, _ = setup()
    ref = make_user_set(h)
    h.UI.OpenShare(ref)
    s = share_dialog(h)
    h.type(s.Author, "Ann")
    h.enter()
    assert h.account_db()["ui"]["author"] == "Ann"
    assert h.fake_calls("Share.Encode")[-1][1]["author"] == "Ann"
    h.click(s.CloseButton)
    h.UI.OpenShare(ref)
    assert h.text(s.Author) == "Ann"
    # Built-ins keep their own author.
    h.UI.OpenShare("builtin:azeroth-mixtape")
    assert h.text(s.Author) == "NeedleDropper" and not h.enabled(s.Author)
    assert h.fake_calls("Share.Encode")[-1][1]["author"] == "NeedleDropper"
