"""Journal tab (DESIGN-v1.2 §4.6): newest first; Replay plays the stored song as a test; Play again
next visit forgets the place for this character and the account; Show drop opens it."""
from __future__ import annotations

from v12_ui_harness import UIHarness

T0 = 1762365600

JOURNAL = [
    {"id": "zone:1436", "t": T0 - 3000, "label": "Westfall", "title": "Harvest Moon", "artist": "Neil Young",
     "spotify": "4uLU6hMCjMI75M1A2tKUQC", "setId": "azeroth-mixtape"},
    {"id": "area:87", "t": T0 - 2000, "label": "Goldshire", "title": "Old Song", "artist": "Old Band",
     "youtube": "aaaaaaaaaaa", "startMs": 15000, "durationMs": 60000, "setId": "azeroth-mixtape"},
    {"id": "zone:9999", "t": T0 - 1000, "label": "Gone Zone", "title": "Lost", "artist": "Nobody"},
]


def open_journal(done=None, location=(1411, "")):
    char = {"journal": JOURNAL, "done": done or {}}
    db = {"accountDone": done or {}}
    h = UIHarness(location=location, char_db=char, db=db)
    h.open("journal")
    return h


def rows(h):
    lst = h.window.journal.List
    return list(lst.GetFrames(lst).values())


def test_newest_first_with_date_place_song_and_soundtrack():
    h = open_journal()
    got = [(h.text(r.When), h.text(r.Place), h.text(r.Song)) for r in rows(h)]
    date = h.g.date
    assert got == [
        (date("%Y-%m-%d %H:%M", T0 - 1000), "Gone Zone", "Nobody · Lost"),
        (date("%Y-%m-%d %H:%M", T0 - 2000), "Elwynn Forest > Goldshire", "Old Band · Old Song · Azeroth Mixtape"),
        (date("%Y-%m-%d %H:%M", T0 - 3000), "Westfall", "Neil Young · Harvest Moon · Azeroth Mixtape"),
    ]
    assert h.text(h.attic(3).Count) == "3 songs heard on this character, newest first"
    for r in rows(h):
        assert r.GetHeight(r) == 46


def test_replay_plays_the_song_the_journal_stored():
    h = open_journal()
    goldshire = rows(h)[1]
    h.click(goldshire.Replay)
    played = h.play_tests()[-1]
    # The stored song, not today's drop (the built-in now has Toto there).
    assert (played["artist"], played["title"], played["youtube"]) == ("Old Band", "Old Song", "aaaaaaaaaaa")
    assert (played["startMs"], played["durationMs"]) == (15000, 60000)
    assert (played["kind"], played["areaID"], played["mapID"]) == ("subzone", 87, 1429)
    assert h.ns.Director.active.isTest
    assert h.char_db()["journal"][-1]["id"] == "zone:9999"     # nothing recorded


def test_play_again_next_visit_forgets_the_place():
    done = {"area:87": {"t": T0, "how": "played"}, "zone:1436": {"t": T0, "how": "history"}}
    h = open_journal(done=done)
    westfall = rows(h)[2]
    assert h.enabled(westfall.Again)
    h.click(westfall.Again)
    assert "zone:1436" not in h.char_db()["done"]
    assert "zone:1436" not in h.account_db()["accountDone"]
    assert h.char_db()["scanned"]["zone:1436"] is True          # history scan will not re-mark it
    assert "area:87" in h.char_db()["done"]
    westfall = rows(h)[2]
    assert not h.enabled(westfall.Again)
    h.hover(westfall.Again)
    assert h.tooltip()[-1] == "Not heard any more: it plays the next time you arrive."


def test_play_again_where_the_player_stands_waits_for_the_next_visit():
    done = {"area:87": {"t": T0, "how": "played"}}
    h = open_journal(done=done, location=(1429, "Goldshire"))
    goldshire = [r for r in rows(h) if h.text(r.Place) == "Elwynn Forest > Goldshire"][0]
    h.click(goldshire.Again)
    assert list(h.fakes.suppressed.values()) == ["area:87"]


def test_show_drop_opens_it_on_the_drops_tab():
    h = open_journal()
    h.click(rows(h)[2].ShowDrop)
    assert h.window.selectedTab == 1
    assert h.selected() == ("builtin:azeroth-mixtape", "zone:1436")
    h.open("journal")
    gone = rows(h)[0]
    assert not h.enabled(gone.ShowDrop)
    h.hover(gone.ShowDrop)
    assert h.tooltip()[-1] == "This place has no needle drop in your soundtracks any more."


def test_empty_journal():
    h = UIHarness(location=(1411, ""))
    h.open("journal")
    assert rows(h) == []
    assert h.shown(h.window.journal.Empty)
    assert h.text(h.window.journal.Empty) == "Your journal is empty: no needle drops heard on this character yet."
