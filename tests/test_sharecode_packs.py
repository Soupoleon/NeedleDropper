"""``packs.py`` understands the share-code pack fields (DESIGN-v1.2 5.2) and keeps its validate semantics."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from needledropper import packs

ROOT = Path(__file__).resolve().parents[1]
SP_ID = "4uLU6hMCjMI75M1A2tKUQC"


def write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def base(**top) -> dict:
    return {"format": "needledropper-pack", "version": 1, "name": "Mine",
            "drops": [{"zone": 1429, "artist": "A", "title": "T"}], **top}


def test_bundled_packs_parse_without_any_issue():
    for name in ("azeroth-mixtape.json", "azeroth-unbound.json"):
        pack = packs.load_pack(ROOT / "packs" / name)
        assert pack.issues == [], [str(i) for i in pack.issues]
    mix = packs.load_pack(ROOT / "packs" / "azeroth-mixtape.json")
    assert mix.id == "azeroth-mixtape" and "default" in mix.tags
    assert any(d.extra.get("tags") == ["fitting"] for d in mix.drops)
    assert packs.load_pack(ROOT / "packs" / "azeroth-unbound.json").id == "azeroth-unbound"


def test_top_level_share_fields():
    p = packs.parse_pack(base(id="my-drops", tags=["a", "b", "a"], homepage="https://example.com/x", revision=4))
    assert (p.id, p.tags, p.homepage, p.revision) == ("my-drops", ["a", "b"], "https://example.com/x", 4)
    assert [i.message for i in p.issues] == ['duplicate tag "a" listed once']
    assert packs.parse_pack(base(name="Ólafur's Mix")).id == "olafur-s-mix"
    assert packs.parse_pack(base(revision=2.0)).revision == 2


@pytest.mark.parametrize("top,needle", [
    ({"id": "Bad Id"}, '"id"'), ({"id": "-" * 3}, '"id"'), ({"id": "x" * 41}, '"id"'), ({"tags": "fitting"}, '"tags"'),
    ({"tags": [""]}, '"tags"[0]'), ({"tags": ["a\nb"]}, "single line"), ({"homepage": "ftp://x"}, '"homepage"'),
    ({"homepage": 5}, '"homepage"'), ({"revision": -1}, '"revision"'), ({"revision": "3"}, '"revision"'),
    ({"revision": True}, '"revision"'), ({"revision": 1.5}, '"revision"'),
])
def test_invalid_top_level_share_fields_are_errors(top, needle):
    p = packs.parse_pack(base(**top))
    assert not p.ok and any(needle in i.message for i in p.issues if i.level == "error")


def test_disabled_entries_are_skipped_without_checks():
    p = packs.parse_pack(base(drops=[
        {"zone": 1429, "artist": "A", "title": "T"},
        {"disabled": True, "zone": 1426},
        {"disabled": True, "zone": "Westfall", "subzone": "Moonbrook"},
        {"disabled": True, "title": "a draft with no place"},
        {"disabled": False, "zone": 1411, "artist": "B", "title": "U"},
    ]))
    assert p.ok and p.issues == []
    assert [d.id for d in p.drops] == ["zone:1429", "zone:1411"]
    assert p.disabled_count == 3 and p.disabled_ids == ["zone:1426", "sub:1436:moonbrook"]


def test_disabled_must_be_a_boolean():
    p = packs.parse_pack(base(drops=[{"disabled": "yes", "zone": 1429}]))
    assert not p.ok and '"disabled" must be true or false' in str(p.issues[0])


def test_drop_tags_are_checked_and_kept():
    p = packs.parse_pack(base(drops=[{"zone": 1429, "artist": "A", "title": "T", "tags": ["fitting", "funny"]}]))
    assert p.issues == [] and p.drops[0].extra == {"tags": ["fitting", "funny"]}
    bad = packs.parse_pack(base(drops=[{"zone": 1429, "artist": "A", "title": "T", "tags": [1]}]))
    assert not bad.ok


@pytest.mark.parametrize("value", [SP_ID, f" {SP_ID} ", f"spotify:track:{SP_ID}",
                                   f"https://open.spotify.com/intl-de/track/{SP_ID}?si=x",
                                   f"open.spotify.com/track/{SP_ID}"])
def test_spotify_accepts_uri_url_and_bare_id(value):
    p = packs.parse_pack(base(drops=[{"zone": 1429, "artist": "A", "title": "T", "spotify": value}]))
    assert p.ok and p.drops[0].spotify == f"spotify:track:{SP_ID}"


@pytest.mark.parametrize("value", ["spotify:album:" + SP_ID, f"https://open.spotify.com/playlist/{SP_ID}", "short"])
def test_spotify_rejects_non_tracks(value):
    assert not packs.parse_pack(base(drops=[{"zone": 1429, "artist": "A", "title": "T", "spotify": value}])).ok


@pytest.mark.parametrize("value", ["QAo_Ycocl1E", "https://youtu.be/QAo_Ycocl1E?t=83",
                                   "https://music.youtube.com/watch?v=QAo_Ycocl1E", "https://m.youtube.com/shorts/QAo_Ycocl1E",
                                   "https://www.youtube.com/live/QAo_Ycocl1E"])
def test_youtube_links(value):
    p = packs.parse_pack(base(drops=[{"zone": 1429, "artist": "A", "title": "T", "youtube": value}]))
    assert p.ok and p.drops[0].youtube == "QAo_Ycocl1E"


def test_numeric_zone_ids_and_in_game_files():
    p = packs.parse_pack(base(drops=[
        {"zone": 1453, "artist": "Me", "title": "Home", "file": "stormwind.mp3", "inGameFile": True, "volume": 70},
        {"zone": "1429", "artist": "A", "title": "T"},
    ]))
    assert p.ok
    d = p.drops[0]
    assert d.id == "zone:1453" and d.inGameFile == packs.IN_GAME_MEDIA_PREFIX + "stormwind.mp3" and d.volume == 70
    assert p.drops[1].id == "zone:1429"


def test_unknown_drop_keys_still_warn_and_duplicates_still_win_last():
    p = packs.parse_pack(base(drops=[{"zone": 1429, "artist": "A", "title": "one", "mood": 1},
                                     {"zone": 1429, "artist": "A", "title": "two"}]))
    assert p.ok
    msgs = [i.message for i in p.issues]
    assert any("unknown key(s) 'mood'" in m for m in msgs) and any("duplicate drop id" in m for m in msgs)
    assert [d.title for d in p.drops] == ["two"]


def test_a_later_pack_can_switch_an_earlier_drop_off(tmp_path):
    a = write(tmp_path, "a.json", base(drops=[{"zone": 1429, "artist": "A", "title": "T"},
                                              {"zone": 1411, "artist": "B", "title": "U"}]))
    b = write(tmp_path, "b.json", base(name="Off", drops=[{"disabled": True, "zone": 1429},
                                                          {"zone": 1436, "artist": "C", "title": "V"}]))
    ps = packs.load_packs_detailed([a, b])
    assert ps.ok and sorted(d.id for d in ps.drops) == ["zone:1411", "zone:1436"]
    lines, ok = packs.validate_report(ps)
    assert ok and any('"Off": 1 drop(s) (+1 turned off)' in ln for ln in lines)
    # the other order: the earlier "off" entry does not hide a later pack's drop
    assert "zone:1429" in packs.load_packs_detailed([b, a]).by_id()


def test_parse_drop_helper():
    d, issues = packs.parse_drop({"zone": "Elwynn Forest", "artist": "A", "title": "T"}, 3)
    assert d is not None and d.id == "zone:1429" and issues == []
    d, issues = packs.parse_drop({"zone": "Elwyn Forrest", "artist": "A", "title": "T"}, 3, pack_name="P")
    assert d is None and issues and str(issues[0]).startswith('pack "P" drops[3]')
    assert packs.parse_drop({"disabled": True}) == (None, [])
    assert packs.parse_drop("x")[0] is None


def test_design_example_passes_parse_pack_with_no_issues():
    example = json.loads("""{"author":"","description":"Songs for my alts","drops":[
      {"areaID":87,"artist":"Toto","label":"Goldshire","subzone":"Goldshire","tags":["funny"],"title":"Africa","youtube":"QAo_Ycocl1E","zone":1429},
      {"artist":"Low Roar","durationMs":352000,"spotify":"spotify:track:4uLU6hMCjMI75M1A2tKUQC","title":"I'll Keep Coming","zone":1429},
      {"disabled":true,"zone":1426},
      {"artist":"Me","file":"stormwind.mp3","inGameFile":true,"title":"Home","zone":1453}],
     "format":"needledropper-pack","id":"my-drops","name":"My Drops","revision":4,"tags":[],"version":1}""")
    p = packs.parse_pack(copy.deepcopy(example))
    assert p.issues == [] and [d.id for d in p.drops] == ["area:87", "zone:1429", "zone:1453"]
