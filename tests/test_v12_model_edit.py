"""v1.2 soundtrack model: editing through the API (CONTRACTS-v1.2 section 2.5, DESIGN-v1.2 sections
4.2 and 4.4): soundtracks, drops, validation, My Drops (Change song here / Turn off here /
Revert), Add drop here with its suppression hook, limits and undo."""
from __future__ import annotations

import pytest

from v12_model_harness import ModelHarness

MIX = "builtin:azeroth-mixtape"
UNB = "builtin:azeroth-unbound"
MY = "user:my-drops"
FIRE, ARM = 3, 2
SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"


@pytest.fixture
def h():
    return ModelHarness(profile="model")


def err_of(res):
    assert res[0] is None, f"expected an error, got {res!r}"
    return res[1]


# --------------------------------------------------------------- soundtracks --

def test_create_turns_it_on_and_checks_names(h):
    ref = h.call1("Soundtracks.Create", "  Westfall Mix  ")
    assert ref == "user:westfall-mix" and h.active() == [ref, MIX]
    s = h.user_set("westfall-mix")
    assert s["name"] == "Westfall Mix" and s["revision"] == 0 and s["drops"] in ({}, [])
    assert s["created"] == s["modified"] > 0
    assert "already" in err_of(h.call("Soundtracks.Create", "westfall MIX"))
    assert "already" in err_of(h.call("Soundtracks.Create", "Azeroth Mixtape"))  # built-in names count
    assert "name" in err_of(h.call("Soundtracks.Create", "   "))
    assert "64" in err_of(h.call("Soundtracks.Create", "x" * 65))
    res = h.run("local ns = ... return ns.Soundtracks.Create('bad \\255 byte')", h.ns)
    assert res[0] is None and "UTF-8" in res[1]


def test_create_picks_free_ids(h):
    assert h.call1("Soundtracks.Create", "Azeroth Mixtape!") == "user:azeroth-mixtape-2"  # built-in id reserved
    assert h.call1("Soundtracks.Create", "東京") == "user:soundtrack"
    assert h.call1("Soundtracks.Create", "大阪") == "user:soundtrack-2"
    long = h.call1("Soundtracks.Create", "A very long soundtrack name that keeps going on")
    assert long == "user:a-very-long-soundtrack-name-that-keeps-g"
    assert len(long) - len("user:") <= 40


def test_rename_and_delete(h):
    ref = h.call1("Soundtracks.Create", "Mine")
    assert h.call("Soundtracks.Rename", ref, "Ours") == (True,)
    assert h.user_set("mine")["name"] == "Ours" and h.user_set("mine")["revision"] == 1
    assert "already" in err_of(h.call("Soundtracks.Rename", ref, "Azeroth Unbound"))
    assert h.call("Soundtracks.Rename", ref, "ours") == (True,)  # changing only the case is fine
    h.call("Soundtracks.SetUseCharacterList", True)
    assert h.call("Soundtracks.Delete", ref) == (True,)
    assert h.user_set("mine") is None
    assert ref not in h.account_db()["active"] and ref not in h.char_db()["active"]
    assert "Built-in" in err_of(h.call("Soundtracks.Delete", MIX))
    assert "Built-in" in err_of(h.call("Soundtracks.Rename", MIX, "Nope"))
    assert err_of(h.call("Soundtracks.Delete", ref))


def test_deleting_the_only_active_soundtrack_falls_back_to_the_default(h):
    ref = h.call1("Soundtracks.Create", "Solo")
    h.call("Soundtracks.SetActive", [ref])
    h.call("Soundtracks.Delete", ref)
    assert h.account_db()["active"] == [MIX]


def test_duplicate_makes_a_full_copy(h):
    ref = h.call1("Soundtracks.Duplicate", MIX)
    assert ref == "user:azeroth-mixtape-my-copy"
    s = h.user_set("azeroth-mixtape-my-copy")
    mix = h.call1("Sets.GetBuiltin", "azeroth-mixtape")
    assert s["name"] == "Azeroth Mixtape (my copy)" and len(s["drops"]) == 38
    assert [d["id"] for d in s["drops"]] == [d["id"] for d in mix["drops"]]
    assert s["author"] == "NeedleDropper" and s["tags"] == ["default", "fitting", "funny"]
    assert h.active() == [ref, MIX]
    again = h.call1("Soundtracks.Duplicate", MIX)
    assert h.user_set(again.split(":")[1])["name"] == "Azeroth Mixtape (my copy) (2)"
    named = h.call1("Soundtracks.Duplicate", ref, "Tweaked")
    assert h.user_set(named.split(":")[1])["name"] == "Tweaked"
    copy_of_user = h.call1("Soundtracks.Duplicate", named)
    assert h.user_set(copy_of_user.split(":")[1])["name"] == "Tweaked (copy)"


def test_set_details(h):
    ref = h.call1("Soundtracks.Create", "Mine")
    assert h.call("Soundtracks.SetDetails", ref, {"author": "Ethan", "description": "Line 1\nLine 2\x07",
                                                  "tags": ["calm", "calm", "night"],
                                                  "homepage": "https://example.com/x"}) == (True,)
    s = h.user_set("mine")
    assert s["author"] == "Ethan" and s["description"] == "Line 1\nLine 2"
    assert s["tags"] == ["calm", "night"] and s["homepage"] == "https://example.com/x"
    assert "http" in err_of(h.call("Soundtracks.SetDetails", ref, {"homepage": "ftp://x"}))
    assert "8" in err_of(h.call("Soundtracks.SetDetails", ref, {"tags": ["t%d" % i for i in range(9)]}))
    h.call("Soundtracks.SetDetails", ref, {"homepage": ""})
    assert "homepage" not in h.user_set("mine")


# --------------------------------------------------------------------- drops --

def test_add_update_remove(h):
    ref = h.call1("Soundtracks.Create", "Mine")
    rid = h.call1("Soundtracks.AddDrop", ref, {
        "mapID": "1436", "artist": " Toto ", "title": "Africa",
        "spotify": "https://open.spotify.com/intl-de/track/%s?si=abc" % SPOTIFY_ID,
        "youtube": "https://www.youtube.com/watch?v=QAo_Ycocl1E&t=83", "tags": ["funny"]})
    assert rid == "zone:1436"
    d = h.call1("Soundtracks.GetDrop", ref, rid)
    assert d["spotify"] == SPOTIFY_ID and d["youtube"] == "QAo_Ycocl1E" and d["artist"] == "Toto"
    assert d["kind"] == "zone" and d["label"] == "Westfall" and d["mapID"] == 1436 and d["modified"] > 0
    assert h.user_set("mine")["revision"] == 1
    assert "already has" in err_of(h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "x", "title": "y"}))
    # update fields; "" / false clear a field
    assert h.call("Soundtracks.UpdateDrop", ref, rid, {"title": "Rosanna", "spotify": "", "volume": 60}) == (True, rid)
    d = h.call1("Soundtracks.GetDrop", ref, rid)
    assert d["title"] == "Rosanna" and "spotify" not in d and d["volume"] == 60
    h.call("Soundtracks.UpdateDrop", ref, rid, {"volume": False})
    assert "volume" not in h.call1("Soundtracks.GetDrop", ref, rid)
    # a new place gives a new id and a fresh label
    assert h.call("Soundtracks.UpdateDrop", ref, rid, {"mapID": 1429, "subzone": "Goldshire"}) == (True, "sub:1429:goldshire")
    d = h.call1("Soundtracks.GetDrop", ref, "sub:1429:goldshire")
    assert d["kind"] == "subzone" and d["label"] == "Goldshire" and h.call1("Soundtracks.GetDrop", ref, rid) is None
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "x", "title": "y"})
    assert "already has" in err_of(h.call("Soundtracks.UpdateDrop", ref, "sub:1429:goldshire",
                                          {"mapID": 1436, "subzone": ""}))
    assert h.call("Soundtracks.RemoveDrop", ref, "zone:1436") == (True,)
    assert err_of(h.call("Soundtracks.RemoveDrop", ref, "zone:1436"))
    assert [x["id"] for x in h.user_set("mine")["drops"]] == ["sub:1429:goldshire"]


@pytest.mark.parametrize("fields, field, words", [
    ({"title": "t" * 201, "artist": "a"}, "title", "200"),
    ({"artist": "a" * 201, "title": "t"}, "artist", "200"),
    ({"note": "n" * 501}, "note", "500"),
    ({"label": "l" * 101}, "label", "100"),
    ({"spotify": "https://open.spotify.com/album/1A2GTWGtFfWp7KSQTwWOyo"}, "spotify", "album"),
    ({"spotify": "not a link"}, "spotify", "Spotify"),
    ({"youtube": "https://www.youtube.com/playlist?list=PL123"}, "youtube", "playlist"),
    ({"youtube": "https://vimeo.com/123"}, "youtube", "YouTube"),
    ({"file": "..\\..\\Wow.exe"}, "file", ".mp3"),
    ({"file": "song.wav"}, "file", ".mp3"),
    ({"file": "dir/song.mp3"}, "file", ".mp3"),
    ({"volume": 101}, "volume", "100"),
    ({"startMs": -1}, "startMs", "milliseconds"),
    ({"durationMs": 2147483648}, "durationMs", "milliseconds"),
    ({"tags": ["x" * 25]}, "tags", "24"),
    ({"tags": "fitting"}, "tags", "list"),
    ({"disabled": "yes"}, "disabled", "true or false"),
])
def test_validation_errors_name_the_field(h, fields, field, words):
    drop = {"mapID": 1436}
    drop.update(fields)
    res = h.call("Soundtracks.Validate", drop)
    assert res[0] is None and words in res[1] and res[2] == field


def test_validation_of_places(h):
    assert "place" in h.call("Soundtracks.Validate", {"artist": "a"})[1]
    assert "zone" in h.call("Soundtracks.Validate", {"subzone": "Goldshire"})[1]
    res = h.call("Soundtracks.Validate", {"mapID": 1429, "subzone": "東京"})
    assert res[0] is None and "area id" in res[1] and res[2] == "subzone"
    assert h.call1("Soundtracks.Validate", {"mapID": 1429, "subzone": "東京", "areaID": 88})["id"] == "area:88"
    assert "1 to" in h.call("Soundtracks.Validate", {"mapID": 0})[1]
    assert "1 to" in h.call("Soundtracks.Validate", {"mapID": 2147483648})[1]
    d = h.call1("Soundtracks.Validate", {"instanceID": 36, "mapID": 1436, "subzone": "Ignored by the id"})
    assert d["id"] == "inst:36" and d["kind"] == "instance" and d["label"] == "Instance 36"
    d = h.call1("Soundtracks.Validate", {"areaID": 87})
    assert d["id"] == "area:87" and d["kind"] == "subzone" and d["label"] == "Goldshire"  # C_Map.GetAreaInfo
    d = h.call1("Soundtracks.Validate", {"mapID": 99999})
    assert d["label"] == "Zone 99999"


def test_text_is_cleaned_but_never_interpreted(h):
    d = h.call1("Soundtracks.Validate", {
        "mapID": 1429, "artist": "  |cffff0000Red|r \x00\x1f", "title": "|TInterface\\Icons\\Spell:0|t Burn",
        "note": "line 1\r\nline 2\t\x7f", "label": "\u0085Elwynn Forest "})
    assert d["artist"] == "|cffff0000Red|r" and d["title"] == "|TInterface\\Icons\\Spell:0|t Burn"
    assert d["note"] == "line 1\nline 2" and d["label"] == "Elwynn Forest"
    assert h.call1("Soundtracks.Validate", {"mapID": 1429, "artist": "　x "})["artist"] == "x"
    assert h.rt.execute("local ns, s = ... return ns.EscapeText(s)", h.ns, "a|Tb|t") == "a||Tb||t"


def test_builtin_soundtracks_are_read_only(h):
    for res in (h.call("Soundtracks.AddDrop", MIX, {"mapID": 1, "artist": "a", "title": "t"}),
                h.call("Soundtracks.UpdateDrop", MIX, "zone:1429", {"title": "x"}),
                h.call("Soundtracks.RemoveDrop", MIX, "zone:1429")):
        assert "Built-in" in err_of(res)
    # Get / GetDrop of a built-in hand out copies
    h.call1("Soundtracks.Get", MIX)["drops"][0]["title"] = "changed"
    got = h.fn("Soundtracks.Get")(MIX)
    got.drops[1].title = "changed in Lua"
    assert h.call1("Sets.GetBuiltin", "azeroth-mixtape")["drops"][0]["title"] != "changed in Lua"
    assert h.call1("Soundtracks.GetDrop", MIX, "zone:1429")["title"] == h.map_drop("zone:1429")["title"]


def test_copy_and_move(h):
    a = h.call1("Soundtracks.Create", "A")
    b = h.call1("Soundtracks.Create", "B")
    assert h.call1("Soundtracks.CopyDrop", MIX, "zone:1429", a) == "zone:1429"
    copied = h.call1("Soundtracks.GetDrop", a, "zone:1429")
    assert copied["title"] == h.call1("Soundtracks.GetDrop", MIX, "zone:1429")["title"]
    assert h.call("Soundtracks.MoveDrop", a, "zone:1429", b) == (True, "zone:1429")
    assert h.call1("Soundtracks.GetDrop", a, "zone:1429") is None
    assert h.call1("Soundtracks.GetDrop", b, "zone:1429")["title"] == copied["title"]
    assert h.call("Soundtracks.Undo")[0] is True  # one step undoes the move
    assert h.call1("Soundtracks.GetDrop", a, "zone:1429") and h.call1("Soundtracks.GetDrop", b, "zone:1429") is None
    assert "Built-in" in err_of(h.call("Soundtracks.MoveDrop", MIX, "zone:1429", a))


# ------------------------------------------------------------------ My Drops --

def test_change_song_here_creates_my_drops_on_top_and_revert_undoes_it(h):
    original = h.map_drop("zone:1429")
    assert h.call("Soundtracks.OverrideHere", "zone:1429", {"artist": "Toto", "title": "Africa"}) == (MY, "zone:1429")
    assert h.active() == [MY, MIX]
    my = h.user_set("my-drops")
    assert my["name"] == "My Drops" and len(my["drops"]) == 1
    e = my["drops"][0]
    assert e["basedOn"] == "azeroth-mixtape" and e["artist"] == "Toto" and e["title"] == "Africa"
    assert e["label"] == original["label"] and e["youtube"] == original["youtube"]  # the rest is copied
    assert e["durationMs"] == original["durationMs"]
    h.rebuild()
    assert h.map_drop("zone:1429")["setRef"] == MY and h.map_drop("zone:1429")["title"] == "Africa"
    assert h.call("Soundtracks.RevertTarget", "zone:1429") == (MIX, "Azeroth Mixtape")
    # changing it again edits the same entry
    h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "Rosanna"})
    assert len(h.user_set("my-drops")["drops"]) == 1 and h.user_set("my-drops")["drops"][0]["title"] == "Rosanna"
    assert h.call("Soundtracks.Revert", "zone:1429") == (True,)
    h.rebuild()
    assert h.map_drop("zone:1429")["title"] == original["title"] and h.map_drop("zone:1429")["setRef"] == MIX
    assert err_of(h.call("Soundtracks.Revert", "zone:1429"))
    assert "no needle drop" in err_of(h.call("Soundtracks.OverrideHere", "zone:424242"))


def test_change_song_here_moves_an_existing_my_drops_to_the_top(h):
    h.call("Soundtracks.OverrideHere", "zone:1411", {"title": "First"})
    h.call("Soundtracks.SetActive", [UNB, MIX, MY])
    h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "Second"})
    assert h.active() == [MY, UNB, MIX]
    e = next(d for d in h.user_set("my-drops")["drops"] if d["id"] == "zone:1429")
    assert e["basedOn"] == "azeroth-unbound"  # the soundtrack that was playing there


def test_change_song_here_on_a_character_list(h):
    h.call("Soundtracks.SetUseCharacterList", True)
    h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "Mine"})
    assert h.char_db()["active"] == [MY, MIX] and h.account_db()["active"] == [MIX]


def test_turn_off_here_and_on_again(h):
    assert h.call("Soundtracks.TurnOffHere", "zone:1429") == (MY, "zone:1429")
    e = h.user_set("my-drops")["drops"][0]
    assert e["disabled"] is True and "title" not in e and e["mapID"] == 1429 and e["basedOn"] == "azeroth-mixtape"
    h.rebuild()
    assert h.map_drop("zone:1429") is None
    assert h.call("Soundtracks.TurnOnHere", "zone:1429") == (True,)
    assert h.user_set("my-drops")["drops"] in ({}, [])  # a bare "off" entry is removed
    h.rebuild()
    assert h.map_drop("zone:1429")["setRef"] == MIX
    # an override keeps its song while turned off
    h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "Kept"})
    h.call("Soundtracks.TurnOffHere", "zone:1429")
    assert h.user_set("my-drops")["drops"][0]["title"] == "Kept"
    h.call("Soundtracks.TurnOnHere", "zone:1429")
    h.rebuild()
    assert h.map_drop("zone:1429")["title"] == "Kept"


def test_turn_off_a_drop_of_your_own(h):
    ref = h.call1("Soundtracks.Create", "Mine")
    h.call("Soundtracks.AddDrop", ref, {"mapID": 77010, "artist": "a", "title": "t"})
    h.call("Soundtracks.UpdateDrop", ref, "zone:77010", {"disabled": True})
    h.rebuild()
    assert h.map_drop("zone:77010") is None
    h.call("Soundtracks.TurnOnHere", "zone:77010", ref)
    h.rebuild()
    assert h.map_drop("zone:77010")["title"] == "t"


# -------------------------------------------------------------- Add drop here --

def full(**kw):
    kw.setdefault("settings", {"historyMode": "fresh"})
    return ModelHarness(**kw)


def test_add_here_in_a_new_landmark_makes_a_suppressed_draft():
    h = full()
    h.call("Soundtracks.SetActive", [])
    h.set_state("exploredAreas", {1429: [88]})
    h.set_location(1429, "Lion's Pride Inn")
    assert h.call("Soundtracks.PlaceHere")[0]["id"] == "area:88"
    assert h.call("Soundtracks.AddHere") == (MY, "area:88", False)
    assert h.active() == [MY]
    d = h.call1("Soundtracks.GetDrop", MY, "area:88")
    assert d["kind"] == "subzone" and d["subzone"] == "Lion's Pride Inn" and d["mapID"] == 1429
    assert h.call1("Soundtracks.IsDraft", d) is True
    assert h.run("local ns = ... return ns.Director.suppress['area:88']", h.ns) is not None
    # finishing the drop while standing there does not fire it mid-edit...
    h.call("Soundtracks.UpdateDrop", MY, "area:88", {"artist": "Toto", "title": "Africa"})
    mark = h.mark()
    h.advance(20)
    assert not [e for e in h.events_since(mark) if e["type"] in (ARM, FIRE) and e.get("dropId") == "area:88"]
    # ...but it plays on the next visit
    h.set_location(1429, "")
    h.set_location(1429, "Lion's Pride Inn")
    h.advance(10)
    assert [e for e in h.events_since(mark) if e["type"] == FIRE and e.get("dropId") == "area:88"]


def test_add_here_uses_director_suppress_here_when_it_exists():
    h = full(profile="model")
    h.run("""local ns = ...
      ns.Director = { SuppressHere = function(id) SUPPRESSED = id end }
      ns.Location = { Snapshot = function()
        return { inInstance = false, zoneMapID = 1436, rawMapID = 1436, zoneName = 'Westfall', subzone = '' } end }
    """, h.ns)
    h.call("Soundtracks.SetActive", [])
    assert h.call("Soundtracks.AddHere") == (MY, "zone:1436", False)
    assert h.g.SUPPRESSED == "zone:1436"


def test_add_here_selects_what_already_covers_the_place():
    h = full()
    h.set_state("exploredAreas", {1429: [87]})
    h.set_location(1429, "Goldshire")
    assert h.call("Soundtracks.AddHere") == (MIX, "area:87", True)
    assert h.user_set("my-drops") is None
    # with a song (/needle add Artist - Title) a built-in place becomes a My Drops change
    assert h.call("Soundtracks.AddHere", None, {"artist": "Toto", "title": "Africa"}) == (MY, "area:87", True)
    h.rebuild()
    assert h.map_drop("area:87")["title"] == "Africa" and h.map_drop("area:87")["setRef"] == MY
    # and a place in the target soundtrack is updated in place
    assert h.call("Soundtracks.AddHere", MY, {"title": "Rosanna"}) == (MY, "area:87", True)
    assert h.call1("Soundtracks.GetDrop", MY, "area:87")["title"] == "Rosanna"


def test_add_here_landmark_by_name_and_without_letters():
    h = full()
    h.set_location(1429, "Lion's Pride Inn")  # no explored area at the position: saved by name
    place = h.call1("Soundtracks.PlaceHere")
    assert place["id"] == "sub:1429:lion-s-pride-inn" and place["byName"] is True
    h.set_location(1429, "東京")
    place = h.call1("Soundtracks.PlaceHere")
    assert place["id"] == "zone:1429" and place["landmarkSkipped"] == "東京"
    h.set_location(1429, "Elwynn Forest")  # subzone text equal to the zone name is the zone
    assert h.call1("Soundtracks.PlaceHere")["id"] == "zone:1429"


def test_add_here_in_an_instance_and_outside_any_zone():
    h = full()
    h.set_location(291, "", instance={"id": 36, "name": "The Deadmines"})
    place = h.call1("Soundtracks.PlaceHere")
    assert place["id"] == "inst:36" and place["label"] == "The Deadmines" and place["kind"] == "instance"
    h.call("Soundtracks.SetActive", [])
    assert h.call("Soundtracks.AddHere") == (MY, "inst:36", False)
    h.set_location(1415, "")  # a continent map: no zone key
    assert "zone" in err_of(h.call("Soundtracks.PlaceHere"))
    assert "Built-in" in err_of(h.call("Soundtracks.AddHere", MIX))


@pytest.mark.parametrize("text, expected", [
    ("Toto - Africa", {"artist": "Toto", "title": "Africa"}),
    ("  Low Roar  –  I'll Keep Coming ", {"artist": "Low Roar", "title": "I'll Keep Coming"}),
    ("Sigur Rós — Hoppípolla - Live", {"artist": "Sigur Rós", "title": "Hoppípolla - Live"}),
    ("https://open.spotify.com/track/%s?si=x" % SPOTIFY_ID, {"spotify": SPOTIFY_ID}),
    ("spotify:track:%s" % SPOTIFY_ID, {"spotify": SPOTIFY_ID}),
    ("https://youtu.be/QAo_Ycocl1E?t=1m23s", {"youtube": "QAo_Ycocl1E", "startMs": 83000}),
    ("https://music.youtube.com/watch?v=QAo_Ycocl1E", {"youtube": "QAo_Ycocl1E"}),
    ("", {}),
])
def test_parse_song_text(h, text, expected):
    assert h.call1("Soundtracks.ParseSongText", text) == expected


@pytest.mark.parametrize("text, words", [
    ("Toto-Africa", "Artist - Title"),
    ("Toto - ", "Artist - Title"),
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "playlist"),
    ("https://www.youtube.com/playlist?list=PL1", "playlist"),
])
def test_parse_song_text_errors(h, text, words):
    res = h.call("Soundtracks.ParseSongText", text)
    assert res[0] is None and words in res[1]


def test_youtube_start_offsets(h):
    f = h.fn("Soundtracks.YouTubeStartMs")
    assert f("https://youtu.be/QAo_Ycocl1E?t=83") == 83000
    assert f("https://www.youtube.com/watch?v=QAo_Ycocl1E&t=1h2m3s") == 3723000
    assert f("https://www.youtube.com/embed/QAo_Ycocl1E?start=15") == 15000
    assert f("https://youtu.be/QAo_Ycocl1E") is None and f("https://youtu.be/x?t=0") is None


# --------------------------------------------------------------------- limits --

def test_soundtrack_limit():
    sets = {"s%d" % i: {"id": "s%d" % i, "name": "S %d" % i, "drops": []} for i in range(50)}
    h = ModelHarness(profile="model", db={"schema": 2, "soundtracks": sets, "active": [MIX]})
    assert "50" in err_of(h.call("Soundtracks.Create", "One too many"))
    assert "50" in err_of(h.call("Soundtracks.Duplicate", MIX))
    assert "50" in err_of(h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "x"}))
    h.call("Soundtracks.Delete", "user:s0")
    assert h.call1("Soundtracks.Create", "Now it fits") == "user:now-it-fits"


def test_drops_per_soundtrack_limit():
    drops = [{"id": "zone:%d" % (1000 + i), "kind": "zone", "mapID": 1000 + i, "label": "z", "title": "t",
              "artist": "a"} for i in range(300)]
    h = ModelHarness(profile="model", db={"schema": 2, "active": [MIX],
                                          "soundtracks": {"full": {"id": "full", "name": "Full", "drops": drops}}})
    assert "300" in err_of(h.call("Soundtracks.AddDrop", "user:full", {"mapID": 5000, "artist": "a", "title": "t"}))
    other = h.call1("Soundtracks.Create", "Other")
    h.call("Soundtracks.AddDrop", other, {"mapID": 5000, "artist": "a", "title": "t"})
    assert "300" in err_of(h.call("Soundtracks.MoveDrop", other, "zone:5000", "user:full"))


def test_total_drops_limit():
    sets = {}
    for s in range(10):
        drops = [{"id": "zone:%d" % (100000 + s * 300 + i), "kind": "zone", "mapID": 100000 + s * 300 + i,
                  "label": "z", "title": "t", "artist": "a"} for i in range(300)]
        sets["s%d" % s] = {"id": "s%d" % s, "name": "S %d" % s, "drops": drops}
    h = ModelHarness(profile="model", db={"schema": 2, "soundtracks": sets, "active": [MIX]})
    assert h.call1("Soundtracks.CountUserDrops") == 3000
    ref = h.call1("Soundtracks.Create", "Empty")
    assert "3000" in err_of(h.call("Soundtracks.AddDrop", ref, {"mapID": 5, "artist": "a", "title": "t"}))
    assert "3000" in err_of(h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "x"}))
    assert "3000" in err_of(h.call("Soundtracks.Duplicate", MIX))
    assert h.get("Soundtracks.Limits") == {"sets": 50, "dropsPerSet": 300, "dropsTotal": 3000, "undo": 20}


# ----------------------------------------------------------------------- undo --

def test_undo_reverses_each_kind_of_edit(h):
    assert h.call1("Soundtracks.CanUndo") is False
    ref = h.call1("Soundtracks.Create", "Mine")
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "Toto", "title": "Africa"})
    assert h.call("Soundtracks.CanUndo") == (True, "Add Westfall")
    h.call("Soundtracks.UpdateDrop", ref, "zone:1436", {"title": "Rosanna"})
    h.call("Soundtracks.RemoveDrop", ref, "zone:1436")
    assert h.call("Soundtracks.Undo") == (True, "Delete Westfall")
    assert h.call1("Soundtracks.GetDrop", ref, "zone:1436")["title"] == "Rosanna"
    assert h.call("Soundtracks.Undo") == (True, "Change Westfall")
    assert h.call1("Soundtracks.GetDrop", ref, "zone:1436")["title"] == "Africa"
    assert h.call("Soundtracks.Undo") == (True, "Add Westfall")
    assert h.call1("Soundtracks.GetDrop", ref, "zone:1436") is None
    # override / turn off / revert, including My Drops' creation and the list change
    before = h.active()
    h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "Mine"})
    h.call("Soundtracks.TurnOffHere", "zone:1411")
    h.call("Soundtracks.Revert", "zone:1429")
    assert h.call("Soundtracks.Undo") == (True, "Revert Elwynn Forest")
    assert h.call("Soundtracks.Undo") == (True, "Turn off Durotar")
    assert [d["id"] for d in h.user_set("my-drops")["drops"]] == ["zone:1429"]
    assert h.call("Soundtracks.Undo") == (True, "Change song here: Elwynn Forest")
    assert h.user_set("my-drops") is None and h.active() == before
    # deleting a soundtrack
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "Toto", "title": "Africa"})
    h.call("Soundtracks.Delete", ref)
    assert h.call("Soundtracks.Undo") == (True, "Delete soundtrack Mine")
    assert h.user_set("mine")["drops"][0]["title"] == "Africa" and ref in h.active()
    h.rebuild()
    assert h.map_drop("zone:1436")["setRef"] == ref


def test_undo_keeps_20_steps(h):
    ref = h.call1("Soundtracks.Create", "Mine")
    for i in range(21):
        h.call("Soundtracks.AddDrop", ref, {"mapID": 3000 + i, "artist": "a", "title": "t"})
    for _ in range(20):
        assert h.call("Soundtracks.Undo")[0] is True
    assert h.call("Soundtracks.Undo") == (False, "Nothing to undo.")
    assert [d["id"] for d in h.user_set("mine")["drops"]] == ["zone:3000"]


def test_undo_never_restores_over_a_new_soundtrack_with_the_same_id(h):
    ref = h.call1("Soundtracks.Create", "Mine")
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "a", "title": "old"})
    h.call("Soundtracks.Delete", ref)
    again = h.call1("Soundtracks.Create", "Mine")
    assert again == ref
    h.call("Soundtracks.AddDrop", again, {"mapID": 1437, "artist": "a", "title": "new"})
    assert h.call("Soundtracks.Undo") == (True, "Add Zone 1437")
    assert h.call("Soundtracks.Undo") == (False, "Nothing to undo.")
    assert h.user_set("mine") is not None


def test_undo_after_a_rename_keeps_the_new_name(h):
    ref = h.call1("Soundtracks.Create", "Mine")
    h.call("Soundtracks.AddDrop", ref, {"mapID": 1436, "artist": "a", "title": "t"})
    h.call("Soundtracks.Rename", ref, "Renamed")
    h.call("Soundtracks.Undo")
    s = h.user_set("mine")
    assert s["name"] == "Renamed" and s["drops"] in ({}, [])
