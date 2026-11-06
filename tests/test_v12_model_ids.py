"""Drop ids and slugs equal the companion's (models.drop_id_for / models.slugify), and the
built-in soundtrack registry (Sets.lua; docs/SETS-FORMAT.md consumer contract)."""
from __future__ import annotations

import random

import pytest

from needledropper import models
from v12_model_harness import ModelHarness

NAMES = [
    "Goldshire", "Lion's Pride Inn", "Lion’s Pride Inn", "Ahn'Qiraj: The Fallen Kingdom", "Un'Goro Crater",
    "Thousand Needles", "  spaced  out  ", "---dashes---", "CamelCaseName", "Zul'Gurub 2.0", "100% Proof",
    "Café Ünder", "Forêt d'Elwynn", "Comté-de-l'Or", "Ölmühle", "Æther Ward", "Zoë's Haus", "Ñandú",
    "İstanbul", "Iİıi", "KELVIN K", "Kkelvin", "ǅungla", "ﬁne ligature", "Ｆｕｌｌｗｉｄｔｈ",
    "東京", "狮王之傲旅店", "골드샤이어", "Златоземье", "Ἀθῆναι", "مدينة", "emoji 🎵 song", "🎵", "",
    "a", "-", "ÀÉÎÕÜ", "x́y", "tab\there", "new\nline", "MiXeD 123 CaSe",
]


@pytest.fixture(scope="module")
def h():
    return ModelHarness(profile="model")


@pytest.mark.parametrize("name", NAMES)
def test_slugify_equals_models_slugify(h, name):
    assert h.call1("Soundtracks.Slugify", name) == models.slugify(name)


def test_slugify_random_strings(h):
    rng = random.Random(1202)
    pool = ("abcxyzABCXYZ0189 -_'.:/" + "éÉüÜßøÆİıKKǅ" + "東京골드" + "🎵́ 　")
    slug = h.fn("Soundtracks.Slugify")
    for _ in range(2000):
        s = "".join(rng.choice(pool) for _ in range(rng.randint(0, 24)))
        assert slug(s) == models.slugify(s), repr(s)


PLACES = [
    {"mapID": 1429},
    {"mapID": 1429, "subzone": "Lion's Pride Inn"},
    {"mapID": 1429, "subzone": "Goldshire", "areaID": 87},
    {"areaID": 87},
    {"instanceID": 36, "mapID": 1436, "areaID": 1, "subzone": "x"},
    {"mapID": 1429, "subzone": "東京"},
    {"mapID": 1429, "subzone": " "},
    {"mapID": 2147483647},
    {"mapID": 1429, "subzone": ""},
]


@pytest.mark.parametrize("place", PLACES)
def test_drop_id_for_equals_models_drop_id_for(h, place):
    assert h.call1("Soundtracks.DropIdFor", place) == models.drop_id_for(**place)


@pytest.mark.parametrize("place", [{}, {"subzone": "Goldshire"}])
def test_drop_id_for_incomplete_places(h, place):
    with pytest.raises(ValueError):
        models.drop_id_for(**place)
    res = h.call("Soundtracks.DropIdFor", place)
    assert res[0] is None and res[1]


def test_kind_of_id(h):
    kind = h.fn("Soundtracks.KindOfId")
    assert [kind(i) for i in ("zone:1", "area:2", "sub:1:x", "inst:3", "test", None)] == \
           ["zone", "subzone", "subzone", "instance", None, None]


def test_every_shipped_drop_id_is_derived_from_its_place(h):
    for set_id in h.call1("Sets.Ids"):
        for d in h.call1("Sets.GetBuiltin", set_id)["drops"]:
            place = {k: d[k] for k in ("mapID", "areaID", "subzone", "instanceID") if k in d}
            if "areaID" in place or "instanceID" in place:
                place.pop("subzone", None)
            assert models.drop_id_for(**place) == d["id"], (set_id, d["id"])
            assert h.call1("Soundtracks.DropIdFor", place) == d["id"]
            assert h.call1("Soundtracks.Validate", d)["id"] == d["id"]


# ------------------------------------------------------------------- Sets.lua --

def test_shipped_sets_are_registered_in_toc_order(h):
    assert h.call1("Sets.Ids") == ["azeroth-mixtape", "azeroth-unbound"]
    assert h.call1("Sets.Count") == 2 and h.call1("Sets.DefaultId") == "azeroth-mixtape"
    assert h.call1("Sets.Problems") in ({}, [])
    builtins = h.call1("Sets.Builtins")
    assert [s["id"] for s in builtins] == ["azeroth-mixtape", "azeroth-unbound"]
    mix = builtins[0]
    assert mix["name"] == "Azeroth Mixtape" and mix["version"] == 1 and len(mix["drops"]) == 38
    assert mix["source"] == "packs/azeroth-mixtape.json" and "default" in mix["tags"]
    for d in mix["drops"]:
        assert set(d) <= {"id", "kind", "mapID", "areaID", "subzone", "instanceID", "label", "title", "artist",
                          "durationMs", "startMs", "youtube", "spotify", "tags", "note"}


def register(h, src):
    return h.rt.execute("local ns = ...\n" + src, h.ns)


def test_register_builtin_set_rules():
    h = ModelHarness(profile="model", login=False)
    ok = register(h, """return ns.RegisterBuiltinSet({ id = "extra", name = "Extra", version = 1, tags = { "x", "x", "" },
        drops = { { id = "zone:1", kind = "zone", mapID = 1, label = "One", title = "T", artist = "A", startMs = 0,
                    durationMs = 0, extra = "dropped" },
                  { id = "zone:2", kind = "zone", label = "no mapID", title = "T", artist = "A" },
                  { id = "zone:1", kind = "zone", mapID = 1, label = "twice", title = "T", artist = "A" } } })""")
    assert ok is True
    s = h.call1("Sets.GetBuiltin", "extra")
    assert [d["id"] for d in s["drops"]] == ["zone:1"] and "extra" not in s["drops"][0]
    assert s["tags"] == ["x"] and s["author"] == "" and s["description"] == ""
    res = register(h, 'return ns.RegisterBuiltinSet({ id = "extra", name = "Again", version = 1, drops = {} })')
    assert res[0] is False and "twice" in res[1]
    res = register(h, 'return ns.RegisterBuiltinSet({ id = "future", name = "F", version = 2, drops = {} })')
    assert res[0] is False and "newer NeedleDropper" in res[1]
    res = register(h, 'return ns.RegisterBuiltinSet({ id = "Bad Id", name = "B", version = 1, drops = {} })')
    assert res[0] is False and "id is not valid" in res[1]
    res = register(h, 'return ns.RegisterBuiltinSet({ id = "empty", name = "E", version = 1, drops = {} })')
    assert res[0] is False and "no drops" in res[1]
    res = register(h, "return ns.RegisterBuiltinSet('nope')")
    assert res[0] is False
    problems = h.call1("Sets.Problems")
    assert len(problems) == 7  # two skipped drops + five refused sets
    h.login()
    printed = "\n".join(h.prints())
    assert "drop 2 was skipped" in printed and "needs a newer NeedleDropper" in printed
    assert h.call1("Sets.Ids") == ["azeroth-mixtape", "azeroth-unbound", "extra"]


def test_built_in_sets_are_frozen_copies():
    h = ModelHarness(profile="model", login=False)
    register(h, """SRC = { id = "frozen", name = "Frozen", version = 1,
        drops = { { id = "zone:1", kind = "zone", mapID = 1, label = "One", title = "T", artist = "A" } } }
        ns.RegisterBuiltinSet(SRC)
        SRC.name = "changed"; SRC.drops[1].title = "changed"; SRC.drops[2] = SRC.drops[1]""")
    s = h.call1("Sets.GetBuiltin", "frozen")
    assert s["name"] == "Frozen" and s["drops"][0]["title"] == "T" and len(s["drops"]) == 1
    register(h, 'local s = ns.Sets.GetBuiltin("frozen"); s.name = "mutated"; s.drops[1].title = "mutated"')
    register(h, 'local all = ns.Sets.Builtins(); all[1].name = "mutated"')
    assert h.call1("Sets.GetBuiltin", "frozen")["name"] == "Frozen"
    assert h.call1("Sets.GetBuiltin", "azeroth-mixtape")["name"] == "Azeroth Mixtape"
    assert h.call1("Sets.GetBuiltin", "missing") is None and h.call1("Sets.Has", "frozen") is True


def test_default_id_comes_from_the_default_tag():
    h = ModelHarness(profile="model", login=False)
    # without the "default" tag the fallback id (azeroth-mixtape) is used
    assert h.call1("Sets.DefaultId") == "azeroth-mixtape"
    register(h, """local ok = ns.Sets.Internal("azeroth-mixtape"); ok.tags = {}
        ns.RegisterBuiltinSet({ id = "tagged", name = "Tagged", version = 1, tags = { "default" },
          drops = { { id = "zone:1", kind = "zone", mapID = 1, label = "One", title = "T", artist = "A" } } })""")
    assert h.call1("Sets.DefaultId") == "tagged"
    h.login()
    assert h.active() == ["builtin:tagged"]
