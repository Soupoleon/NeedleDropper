"""Cross-language contract for protocol v2 (docs/PROTOCOL-v2.md).

The addon's ``Protocol2.lua`` (run here in ``lupa.lua51``) and the companion's
``needledropper/protocol2.py`` must agree byte for byte on frames, chat lines and pixel cell
grids, and on every helper that shapes them: UTF-8 cleaning / truncation, id sanitising, the
artist/title query, the Spotify / YouTube link parsers, the fit rule at every budget (the
262-byte chat frame and the beacon's 2 / 3 / 4 px row caps, DESIGN-v1.2 2.3), the adaptive
strip geometry and the NDPAD / flag helpers. Also covers the decoders' validation rules
(both languages), forward compatibility and the DESIGN 2.3 measured-size table.
"""
from __future__ import annotations

import dataclasses
import itertools
import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper import protocol, protocol2 as p2  # noqa: E402
from needledropper.models import EventType, Flags  # noqa: E402

lua51 = pytest.importorskip("lupa.lua51")

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
YT_ID = "dQw4w9WgXcQ"


# --------------------------------------------------------------- fixtures --

@pytest.fixture(scope="module")
def lua():
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    src = (ROOT / "addon" / "NeedleDropper" / "Protocol2.lua").read_text(encoding="utf-8")
    chunk = rt.execute("return function(src) return loadstring(src, '=Protocol2.lua') end")(src)
    assert chunk is not None, "Protocol2.lua does not compile under Lua 5.1"
    ns = rt.table()
    chunk("NeedleDropper", ns)
    helpers = rt.execute("""
      local function tobytes(s) local t = {} for i = 1, #s do t[i] = s:byte(i) end return t end
      return {
        tobytes = tobytes,
        strbytes = function(f, ...) local r = f(...) if type(r) ~= "string" then return nil end return tobytes(r) end,
        fromlist = function(n, ...) local t = {...} return t end,
      }""")
    return SimpleNamespace(rt=rt, P=ns.Protocol2, h=helpers)


def lbytes(t) -> bytes:
    """Lua byte array -> bytes."""
    return bytes(int(t[i]) for i in range(1, len(t) + 1))


def ltable(lua, data: bytes):
    return lua.rt.table_from(list(data))


def lua_event(lua, ev: dict):
    return lua.rt.table_from({k: v for k, v in ev.items() if v is not None})


def lua_str_bytes(lua, fn, *args) -> bytes | None:
    """Call a Lua function returning a string, get its raw bytes (None if it returned nil)."""
    t = lua.h.strbytes(fn, *args)
    return None if t is None else lbytes(t)


def lua_frame(lua, ev: dict, max_bytes=None, prefer=None) -> bytes:
    return lbytes(lua.P.EncodeFrame(lua_event(lua, ev), max_bytes, prefer))


def py_frame(ev: dict, max_bytes=None, prefer=None) -> bytes:
    return p2.encode_frame(p2.frame_from_event(ev, max_bytes=max_bytes, prefer=prefer))


def lua_grid_cells(lg) -> list[tuple[int, int, int]]:
    return [tuple(255 * int(lg.cells[i][c]) for c in (1, 2, 3)) for i in range(1, len(lg.cells) + 1)]


# ------------------------------------------------------------- the events --

UTF8_NAMES = ["Sigur Rós", "Ólafur Arnalds", "Hoppípolla", "Björk", "Mötley Crüe", "坂本龍一", "久石譲",
              "Joe Hisaishi — 人生のメリーゴーランド", "Nils Frahm", "🎵🎶 Lo-fi 🎧", "👨‍👩‍👧 family",
              "Ελληνικά", "Кино", "Низкий рёв", "Low Roar", "I'll Keep Coming", "Café Tacvba", "á combining"]

EVENTS = [
    pytest.param({"type": 3, "seq": 7, "flags": 0x30, "dropId": "zone:1429",
                  "spotify": f"https://open.spotify.com/track/{SPOTIFY_ID}?si=abc", "youtube": f"https://youtu.be/{YT_ID}?t=3",
                  "artist": "Low Roar", "title": "I'll Keep Coming", "startMs": 12340, "lengthMs": 95000, "volume": 80,
                  "label": "Elwynn Forest", "setId": "azeroth-mixtape"}, id="fire-full"),
    pytest.param({"type": 2, "seq": 1, "dropId": "area:87", "artist": "Sigur Rós", "title": "Hoppípolla",
                  "spotify": f"spotify:track:{SPOTIFY_ID}", "label": "Goldshire"}, id="arm-icelandic"),
    pytest.param({"type": 3, "seq": 2, "dropId": "inst:36", "artist": "Ólafur Arnalds", "title": "Saman",
                  "youtube": YT_ID, "lengthDs": 1234, "label": "The Deadmines"}, id="fire-olafur"),
    pytest.param({"type": 3, "seq": 3, "dropId": "zone:1453", "artist": "坂本龍一", "title": "戦場のメリークリスマス",
                  "label": "ストームウィンド", "setId": "jp"}, id="fire-cjk"),
    pytest.param({"type": 3, "seq": 4, "dropId": "zone:1", "artist": "🎵" * 20, "title": "🎶" * 20,
                  "label": "a" + "🎵" * 12}, id="emoji-truncation"),
    pytest.param({"type": 3, "seq": 5, "dropId": "zone:2", "artist": "A" * 100, "title": "T" * 100}, id="query-split"),
    pytest.param({"type": 3, "seq": 6, "dropId": "zone:3", "artist": "Björk", "title": "x" * 200}, id="query-long-title"),
    pytest.param({"type": 3, "seq": 7, "dropId": "zone:4", "artist": "久" * 50, "title": "T"}, id="query-long-artist"),
    pytest.param({"type": 3, "seq": 8, "dropId": "zone:5", "artist": "久" * 30, "title": "譲" * 30}, id="query-cjk-boundaries"),
    pytest.param({"type": 3, "seq": 9, "dropId": "sub:1429:" + "x" * 60, "spotify": SPOTIFY_ID, "youtube": YT_ID,
                  "artist": "Ö" * 70, "title": "é" * 70, "startDs": 65535, "lengthDs": 65535, "volume": 100,
                  "label": "Ł" * 30, "setId": "s" * 40}, id="max-size"),
    pytest.param({"type": 4, "seq": 255, "dropId": "sub:1429:lion-s-pride-inn", "artist": "dropped", "volume": 3,
                  "label": "dropped"}, id="cancel-identity-only"),
    pytest.param({"type": 1, "seq": 0, "flags": 0, "dropId": "zone:1429", "artist": "x"}, id="hello-empty"),
    pytest.param({"type": 5, "seq": 128, "flags": Flags.TEST, "dropId": "test"}, id="ping-empty"),
    pytest.param({"type": 0, "seq": 42}, id="none"),
    pytest.param({"type": 3, "seq": 10, "dropId": "zone:9", "artist": "A\x1fB\x00C", "title": "Hello\nWorld\t!\x7f",
                  "label": "  padded  "}, id="control-chars"),
    pytest.param({"type": 3, "seq": 11, "dropId": "zone:9", "spotify": "spotify:album:" + SPOTIFY_ID,
                  "youtube": "https://vimeo.com/1"}, id="invalid-links-dropped"),
    pytest.param({"type": 3, "seq": 12, "dropId": "zone:9", "startMs": 12349, "lengthMs": 12350.0}, id="ms-rounding"),
    pytest.param({"type": 3, "seq": 13, "dropId": "zone:9", "startMs": 999999999, "lengthMs": -5, "volume": 80.6},
                 id="clamping"),
    pytest.param({"type": 3, "seq": 14, "dropId": "zone:9", "startDs": 7, "startMs": 99000, "lengthDs": 0,
                  "volume": -3}, id="ds-precedence-and-zero"),
    pytest.param({"type": 3, "seq": 256, "flags": 0x1FF, "dropId": "zone:9", "placeKey": 70000}, id="wraparound"),
    pytest.param({"type": 3, "seq": -1, "dropId": "sub:1:café | pipe"}, id="sanitize-bytewise"),
    pytest.param({"type": 2.9, "seq": 3.7, "flags": 33.2, "dropId": "zone:9", "volume": 150}, id="float-numbers"),
    pytest.param({"type": 3, "seq": 1, "dropId": "zone:9", "artist": "", "title": "Only Title"}, id="title-only"),
    pytest.param({"type": 3, "seq": 1, "dropId": "zone:9", "artist": "Only Artist"}, id="artist-only"),
    pytest.param({"type": 3, "seq": 1, "dropId": "zone:9", "seq_unused": "x", "label": 42, "artist": 7}, id="non-string-text"),
]


@pytest.mark.parametrize("ev", EVENTS)
def test_frames_byte_exact(lua, ev):
    lb = lua_frame(lua, ev)
    pb = py_frame(ev)
    assert lb == pb
    assert len(pb) <= p2.MAX_FRAME and pb[4] <= p2.MAX_BODY
    frame = p2.decode_frame(pb)
    assert frame is not None and frame == p2.frame_from_event(ev)
    assert frame.raw == pb and frame.to_bytes() == pb
    # Lua's own decoder agrees on every field
    d = lua.P.DecodeFrame(ltable(lua, pb))
    assert d is not None
    assert (d.type, d.seq, d.flags) == (int(frame.type), frame.seq, int(frame.flags))
    assert d.placeKey == frame.place_key and d.dropId == frame.drop_id and d.setId == frame.set_id
    assert d.spotify == frame.spotify_id and d.youtube == frame.youtube_id
    assert d.startDs == frame.start_ds and d.lengthDs == frame.length_ds and d.volume == frame.volume
    assert (d.label or None) == frame.label
    if frame.query is not None:
        assert (d.artist, d.title) == (frame.artist, frame.title)


def _random_event(rng: random.Random) -> dict:
    def maybe(p, v):
        return v if rng.random() < p else None
    links_s = [SPOTIFY_ID, f"spotify:track:{SPOTIFY_ID}", f"https://open.spotify.com/track/{SPOTIFY_ID}?si=q",
               "spotify:album:x", "junk", ""]
    links_y = [YT_ID, f"https://youtu.be/{YT_ID}", f"https://www.youtube.com/watch?x=1&v={YT_ID}", "nope", ""]
    ids = ["zone:1429", "area:87", "sub:1429:lion-s-pride-inn", "inst:36", "test", "x" * rng.randint(30, 70),
           "sub:1:café", "", "a b|c"]
    text = UTF8_NAMES + ["", " ", "x" * rng.randint(40, 160), "".join(rng.choice(UTF8_NAMES) for _ in range(6)),
                         "ctl\x01\x1f\x7f end"]
    num = [0, 1, 12345, 99.5, -7, 6553500, 6553600, 1e9, 0.4]
    return {
        "type": rng.randrange(6), "seq": rng.choice([rng.randrange(256), rng.randrange(-300, 600)]),
        "flags": rng.randrange(256), "dropId": maybe(0.9, rng.choice(ids)),
        "placeKey": maybe(0.1, rng.randrange(-5, 70000)),
        "spotify": maybe(0.5, rng.choice(links_s)), "youtube": maybe(0.5, rng.choice(links_y)),
        "artist": maybe(0.8, rng.choice(text)), "title": maybe(0.8, rng.choice(text)),
        "startMs": maybe(0.4, rng.choice(num)), "startDs": maybe(0.2, rng.choice(num)),
        "lengthMs": maybe(0.4, rng.choice(num)), "lengthDs": maybe(0.2, rng.choice(num)),
        "volume": maybe(0.5, rng.choice([0, 50, 80.5, 100, 101, -1, 33.4999])),
        "label": maybe(0.7, rng.choice(text)), "setId": maybe(0.5, rng.choice(["azeroth-mixtape", "s" * 40, "ü-set", ""])),
    }


def test_random_frames_byte_exact(lua):
    rng = random.Random(20261105)
    sizes = []
    for _ in range(400):
        ev = _random_event(rng)
        pb = py_frame(ev)
        assert lua_frame(lua, ev) == pb, ev
        frame = p2.decode_frame(pb)
        assert frame == p2.frame_from_event(ev)
        assert lua.P.FormatChatLine(lua_event(lua, ev)) == p2.format_chat_line(pb)
        sizes.append(len(pb))
    assert min(sizes) == p2.FRAME_OVERHEAD and max(sizes) > 200  # empty HELLOs through near-max frames


def test_fit_rule_max_size_frame():
    """Every field at its cap is a 299-byte body (306-byte frame): within the 262-byte chat
    budget the fit rule drops setId, then the whole label (DESIGN 2.3 steps 1-2), and keeps
    everything else, so the chat line of the largest possible event is 222 bytes."""
    ev = EVENTS[9].values[0]
    raw_body = 4 + 42 + 24 + 13 + 121 + 4 + 4 + 3 + 50 + 34   # all ten fields at their caps
    assert raw_body == 299 > p2.MAX_BODY
    f = p2.frame_from_event(ev)
    b = p2.encode_frame(f)
    assert len(b) == p2.FRAME_OVERHEAD + raw_body - 34 - 50 == 222
    assert f.set_id is None and f.label is None
    assert len(f.query.encode()) <= p2.MAX_QUERY and p2.QUERY_SEP in f.query
    assert f.drop_id == ("sub:1429:" + "x" * 60)[:40]
    assert f.place_key == protocol.fletcher16(("sub:1429:" + "x" * 60).encode())  # of the full id
    assert (f.spotify_id, f.youtube_id, f.start_ds, f.length_ds, f.volume) == (SPOTIFY_ID, YT_ID, 65535, 65535, 100)
    # with room to spare nothing is dropped, and frames up to the 262-byte bound still occur
    small = p2.frame_from_event({**ev, "artist": "A", "title": "T"})
    assert small.set_id == "s" * 32 and small.label == "Ł" * 24
    big = p2.encode_frame(p2.frame_from_event({**ev, "setId": None, "label": "L" * 38}))
    assert len(big) == 262 and p2.decode_frame(big).label == "L" * 38
    over = p2.frame_from_event({**ev, "setId": None, "label": "L" * 39})  # one byte too many
    assert over.label is None and len(p2.encode_frame(over)) == 222
    assert max(len(py_frame(e.values[0])) for e in EVENTS) <= p2.MAX_FRAME


def test_field_normalisation_details():
    f = p2.frame_from_event(EVENTS[16].values[0])  # ms rounding: floor((ms + 50) / 100)
    assert (f.start_ds, f.length_ds) == (123, 124)
    f = p2.frame_from_event(EVENTS[17].values[0])  # clamping
    assert (f.start_ds, f.length_ds, f.volume) == (65535, None, 81)
    f = p2.frame_from_event(EVENTS[18].values[0])  # ds wins over ms; zero omitted; volume clamped
    assert (f.start_ds, f.length_ds, f.volume) == (7, None, 0)
    f = p2.frame_from_event(EVENTS[19].values[0])  # wraparound
    assert (f.seq, int(f.flags), f.place_key) == (0, 0xFF, 70000 % 65536)
    f = p2.frame_from_event(EVENTS[20].values[0])  # byte-wise id sanitizing (é = 2 bytes)
    assert f.seq == 255 and f.drop_id == "sub:1:caf_____pipe"
    f = p2.frame_from_event(EVENTS[14].values[0])  # control characters
    assert f.artist == "A B C" and f.title == "Hello World !" and f.label == "padded"
    f = p2.frame_from_event(EVENTS[10].values[0])  # CANCEL keeps only the identity
    assert f.type == EventType.CANCEL and f.query is None and f.volume is None and f.label is None
    assert f.drop_id == "sub:1429:lion-s-pride-inn" and f.place_key == p2.place_key_for(f.drop_id)
    assert p2.encode_frame(p2.frame_from_event(EVENTS[11].values[0]))[4] == 0  # HELLO: empty body
    f = p2.frame_from_event(EVENTS[22].values[0])
    assert (f.artist, f.title) == ("", "Only Title")
    f = p2.frame_from_event(EVENTS[23].values[0])
    assert (f.artist, f.title, f.search_text) == ("Only Artist", "", "Only Artist")
    f = p2.frame_from_event(EVENTS[0].values[0])
    assert f.spotify_uri == f"spotify:track:{SPOTIFY_ID}" and f.youtube_id == YT_ID
    assert (f.spotify, f.youtube) == (SPOTIFY_ID, YT_ID)  # CONTRACTS-v1.2 3.1 property names
    assert (f.start_ms, f.length_ms) == (12300, 95000) and f.key == (7, 3, f.place_key, 0x30)
    assert "FIRE seq=7" in f.describe() and "Elwynn Forest" in f.describe()
    with pytest.raises(ValueError):
        p2.build_frame(9)


# --------------------------------------------------------------- fit rule --

#: DESIGN 2.3's "FIRE user drop with every field (sub:1429:lion-s-pride-inn)": 141 bytes.
FULL_USER_DROP = {"type": 3, "seq": 21, "flags": 0x20, "dropId": "sub:1429:lion-s-pride-inn", "spotify": SPOTIFY_ID,
                  "youtube": YT_ID, "artist": "Low Roar", "title": "I'll Keep Coming", "startMs": 12000,
                  "lengthMs": 95000, "volume": 80, "label": "Lion's Pride Inn", "setId": "my-drops"}
#: A search-only drop (no provider id): the query is all the companion has, so it is shortened, not dropped.
QUERY_ONLY = {"type": 2, "seq": 4, "dropId": "zone:1453", "artist": "坂本龍一", "title": "戦場のメリークリスマス" * 2,
              "startMs": 5000, "lengthMs": 60000, "volume": 70, "label": "Stormwind City", "setId": "jp-set"}


def _present(f: p2.Frame) -> set[str]:
    named = (("placeKey", f.place_key), ("dropId", f.drop_id), ("spotify", f.spotify_id), ("youtube", f.youtube_id),
             ("query", f.query), ("startDs", f.start_ds), ("lengthDs", f.length_ds), ("volume", f.volume),
             ("label", f.label), ("setId", f.set_id))
    return {name for name, v in named if v is not None}


ALL_FIELDS = {"placeKey", "dropId", "spotify", "youtube", "query", "startDs", "lengthDs", "volume", "label", "setId"}


@pytest.mark.parametrize("prefer,kept", [("spotify", "spotify"), ("youtube", "youtube"), (None, "youtube"),
                                         ("SPOTIFY", "spotify"), ("apple", "youtube")])
def test_fit_rule_removes_fields_in_design_order(prefer, kept):
    """DESIGN 2.3: 1 setId, 2 label, 3 query (an id is present), 4 dropId (placeKey stays),
    5 shorten query, 6 volume, 7 keep only the preferred provider's id -- each step only while
    the frame is still over budget; placeKey, start, length, flags and one id always stay."""
    ev = FULL_USER_DROP
    assert len(py_frame(ev)) == 141
    stages = [(141, ALL_FIELDS),
              (131, ALL_FIELDS - {"setId"}),
              (113, ALL_FIELDS - {"setId", "label"}),
              (86, ALL_FIELDS - {"setId", "label", "query"}),
              (59, ALL_FIELDS - {"setId", "label", "query", "dropId"}),
              (56, {"placeKey", "spotify", "youtube", "startDs", "lengthDs"})]
    last = {"spotify": 43, "youtube": 32}[kept]
    stages.append((last, {"placeKey", kept, "startDs", "lengthDs"}))
    prev = p2.MAX_FRAME + 1
    for size, fields in stages:  # a budget in [size, previous size - 1] yields exactly this stage
        for budget in (size, prev - 1):
            f = p2.frame_from_event(ev, max_bytes=budget, prefer=prefer)
            assert len(p2.encode_frame(f)) == size and _present(f) == fields, (budget, size)
            assert (f.seq, int(f.flags), f.place_key) == (21, 0x20, p2.place_key_for(ev["dropId"]))
        prev = size
    # the irreducible frame is returned as is, even over the budget (the beacon draws an extra row)
    assert len(py_frame(ev, max_bytes=p2.FRAME_OVERHEAD, prefer=prefer)) == last


def test_fit_rule_shortens_a_search_only_query_on_utf8_boundaries():
    """Without a provider id the query is the song: it stays through step 3, is shortened in
    step 5 (code-point safe, just enough), and only then go the query entirely and the volume."""
    full = len(py_frame(QUERY_ONLY))
    assert full == 138 > p2.capacity(3)
    q_full = p2.build_query(QUERY_ONLY["artist"], QUERY_ONLY["title"])
    states = []
    for budget in range(full, p2.FRAME_OVERHEAD - 1, -1):
        f = p2.frame_from_event(QUERY_ONLY, max_bytes=budget)
        size = len(p2.encode_frame(f))
        assert size <= max(budget, 19), budget  # 19 = head + placeKey + start + length
        assert {"placeKey", "startDs", "lengthDs"} <= _present(f)
        if f.query is not None:
            q = f.query.encode()
            assert q_full.startswith(q) and q.decode("utf-8")  # a prefix cut on a code-point boundary
            assert f.volume == 70  # the volume goes only after the whole query
            state = "full" if q == q_full else "shortened"
            if state == "shortened":
                assert f.drop_id is None and size >= budget - 2  # just enough (code points here are <= 3 bytes)
        else:
            assert f.drop_id is None and f.label is None and f.set_id is None
            state = "no query" if f.volume is not None else "no volume"
        if not states or states[-1] != state:
            states.append(state)
    assert states == ["full", "shortened", "no query", "no volume"]
    tiny = p2.frame_from_event(QUERY_ONLY, max_bytes=p2.FRAME_OVERHEAD)
    assert _present(tiny) == {"placeKey", "startDs", "lengthDs"} and len(p2.encode_frame(tiny)) == 19


FIT_EVENTS = [FULL_USER_DROP, QUERY_ONLY, EVENTS[9].values[0], EVENTS[0].values[0], EVENTS[4].values[0],
              EVENTS[8].values[0], {**FULL_USER_DROP, "spotify": None}, {**FULL_USER_DROP, "youtube": None}]


def _budget(b) -> int:
    if not isinstance(b, (int, float)) or b != b:
        return p2.MAX_FRAME
    return min(max(math.floor(b), p2.FRAME_OVERHEAD), p2.MAX_FRAME)


@pytest.mark.parametrize("prefer", ["spotify", "youtube", None, "Spotify", 7])
def test_fit_rule_byte_exact_at_every_budget(lua, prefer):
    for ev in FIT_EVENTS:
        for budget in list(range(0, 268)) + [None, 1e9, -1, 39.9, float("nan")]:
            want = py_frame(ev, budget, prefer)
            assert lua_frame(lua, ev, budget, prefer) == want, (ev, budget)
            assert len(want) <= max(_budget(budget), 43)  # 43: placeKey + start + length + Spotify id
    # the event's own `prefer` is the default, as in Lua
    ev = {**FULL_USER_DROP, "prefer": "spotify"}
    assert py_frame(ev, 50) == lua_frame(lua, ev, 50) == py_frame(FULL_USER_DROP, 50, "spotify")


def test_random_events_fit_byte_exact_at_beacon_budgets(lua):
    rng = random.Random(23)
    for _ in range(300):
        ev = _random_event(rng)
        ev["prefer"] = rng.choice(["spotify", "youtube", None])
        for budget in (p2.capacity(1), p2.capacity(2), p2.capacity(3), p2.capacity(4), rng.randrange(7, 263)):
            assert lua_frame(lua, ev, budget) == py_frame(ev, budget), (ev, budget)


# ------------------------------------------------------------- geometry --

def test_geometry_helpers_match(lua):
    P = lua.P
    assert [p2.rows_cap(px) for px in (2, 3, 4)] == [4, 3, 2]  # rows x cellPx <= 9 px
    for px in [0, 1, 2, 3, 4, 5, 6, 8, 9, 100, 2.4, 2.6, -3, None, "3", float("nan")]:
        assert P.RowsCap(px) == p2.rows_cap(px), px
    for px in range(p2.MIN_CELL_PX, p2.MAX_CELL_PX + 1):
        assert p2.rows_cap(px) * px <= p2.MAX_STRIP_PX < (p2.rows_cap(px) + 1) * px or p2.rows_cap(px) == 1
    assert [p2.capacity(r) for r in (1, 2, 3, 4)] == [39, 81, 123, 165]
    assert [p2.capacity(p2.rows_cap(px)) for px in (2, 3, 4)] == [165, 123, 81]  # DESIGN 2.3 capacities
    for rows in [-1, 0, 1, 2, 3, 4, 5, 7, 8, 100, 2.9, None, float("nan")]:
        assert P.Capacity(rows) == p2.capacity(rows), rows
    assert p2.capacity(7) == p2.MAX_FRAME and p2.capacity(6) == 249
    for n in range(0, p2.MAX_FRAME + 1):
        rows, cols = p2.adaptive_layout(n)
        assert tuple(P.AdaptiveLayout(n)) == (rows, cols), n
        cells = p2.LEAD_CELLS + -(-n * 8 // 3)
        assert rows == -(-cells // p2.MAX_COLS) and cols % p2.COL_STEP == 0 and p2.MIN_COLS <= cols <= p2.MAX_COLS
        assert p2.rows_for(n, cols) == rows  # decoders read an adaptive strip like any other
        assert cols >= -(-cells // rows) and cols - -(-cells // rows) < p2.COL_STEP  # the narrowest width
        assert (n <= p2.capacity(rows)) and (rows == 1 or n > p2.capacity(rows - 1))


def test_measured_size_table():
    """DESIGN 2.3 "Measured" table: frame bytes, rows at 112 columns and the strip at 3 px."""
    mixtape = {"type": 3, "seq": 1, "flags": 0x20, "dropId": "zone:1411", "youtube": "d_vPJjqWxZM",
               "artist": "Dolly Parton", "title": "9 to 5", "startMs": 0, "lengthMs": 165547}  # Azeroth Mixtape, Durotar
    table = [
        ("HELLO", {"type": 1, "seq": 1}, 7, 1, (96, 3), 7),
        ("CANCEL zone:1429", {"type": 4, "seq": 2, "dropId": "zone:1429"}, 22, 1, (240, 3), 22),
        ("FIRE, bundled Mixtape drop", mixtape, 60, 2, (288, 6), 60),
        ("... same plus label and setId", {**mixtape, "label": "Durotar", "setId": "azeroth-mixtape"}, 86, 3, (240, 9), 86),
        ("FIRE Spotify + YouTube + query + start + length + volume",
         {**FULL_USER_DROP, "dropId": "area:87", "label": None, "setId": None}, 95, 3, (288, 9), 95),
        ("FIRE user drop with every field", FULL_USER_DROP, 141, 4, (336, 9), 113),
    ]
    for name, ev, size, rows, strip, fitted in table:
        assert len(py_frame(ev)) == size, name
        assert p2.adaptive_layout(size)[0] == rows, name
        grid = p2.encode_event_cells(ev, p2.Geometry.beacon(3))
        assert (grid.frame_len, (grid.width_px, grid.height_px), grid.overflow) == (fitted, strip, False), name
        assert p2.decode_cells_grid(grid.as_rows()) == p2.frame_from_event(ev, max_bytes=123)
    # worst case: every field at its cap -- the chat frame is fitted to 222 bytes (6 rows at
    # 112 columns; DESIGN's 262 / 7 rows predates the drop-the-label rule), the 3 px strip to <= 123 B
    worst = EVENTS[9].values[0]
    assert len(py_frame(worst)) == 222 and p2.adaptive_layout(222) == (6, 112)
    for px, cap in ((2, 165), (3, 123), (4, 81)):
        grid = p2.encode_event_cells(worst, p2.Geometry.beacon(px))
        assert grid.frame_len <= cap and grid.rows * px <= p2.MAX_STRIP_PX and not grid.overflow


@pytest.mark.parametrize("px", [2, 3, 4])
def test_adaptive_cells_byte_exact_at_row_caps(lua, px):
    """The fit rule at the 2 / 3 / 4 px row caps (and every lower cap): identical frames,
    geometry and cells from Lua and Python, and the grid decodes to the fitted frame."""
    rng = random.Random(px * 101)
    events = [FULL_USER_DROP, QUERY_ONLY, EVENTS[9].values[0], EVENTS[11].values[0], EVENTS[10].values[0]]
    events += [_random_event(rng) for _ in range(40)]
    for i, ev in enumerate(events):
        ev = {**ev, "prefer": ("spotify", "youtube", None)[i % 3]}
        for max_rows in range(1, p2.rows_cap(px) + 1):
            g = p2.Geometry.beacon(px, max_rows)
            grid = p2.encode_event_cells(ev, g)
            lg = lua.P.EncodeCells(lua_event(lua, ev), lua.rt.table_from({"cellPx": px, "maxRows": max_rows}))
            assert lua_grid_cells(lg) == list(grid.cells), (ev, max_rows)
            assert (lg.rows, lg.cols, lg.cellPx, lg.widthPx, lg.heightPx, lg.frameLen, lg.maxRows, bool(lg.overflow)) == (
                grid.rows, grid.cols, px, grid.width_px, grid.height_px, grid.frame_len, max_rows, grid.overflow)
            assert grid.overflow == (grid.rows > max_rows)
            assert grid.frame_len <= p2.capacity(max_rows) or grid.overflow
            assert grid.rows == p2.adaptive_layout(grid.frame_len)[0] == p2.rows_for(grid.frame_len, grid.cols)
            assert p2.decode_cells_grid(grid.as_rows()) == p2.frame_from_event(ev, max_bytes=p2.capacity(max_rows))
            if not grid.overflow:
                assert grid.rows * px <= p2.MAX_STRIP_PX


def test_irreducible_frame_overflows_one_row_but_still_decodes(lua):
    """placeKey + start + length + a Spotify id are 43 bytes: more than one row holds (39)."""
    ev = {**FULL_USER_DROP, "prefer": "spotify"}
    grid = p2.encode_event_cells(ev, p2.Geometry.beacon(2, 1))
    assert (grid.frame_len, grid.rows, grid.overflow) == (43, 2, True) and p2.capacity(1) == 39
    f = p2.decode_cells_grid(grid.as_rows())
    assert (f.spotify_id, f.youtube_id, f.start_ds, f.length_ds) == (SPOTIFY_ID, None, 120, 950)
    lg = lua.P.EncodeCells(lua_event(lua, ev), lua.rt.table_from({"cellPx": 2, "maxRows": 1}))
    assert lg.overflow is True and lg.rows == 2 and lua_grid_cells(lg) == list(grid.cells)
    yt = p2.encode_event_cells({**ev, "prefer": "youtube"}, p2.Geometry.beacon(2, 1))
    assert (yt.frame_len, yt.rows, yt.overflow) == (32, 1, False)


def test_pad_line_and_flag_helpers_match(lua):
    P = lua.P
    for seq, shown in ((0, 0), (1, 1), (42, 42), (255, 255), (256, 0), (300, 44), (-1, 255), (7.9, 7), (None, 0)):
        line = P.PadLine(seq)
        head = f"NDPAD {shown} "
        assert line == p2.pad_line(seq) and len(line) == 900
        assert line.startswith(head) and set(line[len(head):]) == {"."}
    assert P.PadLine(9, 120) == p2.pad_line(9, 120) and len(p2.pad_line(9, 120)) == 120
    assert P.PadLine(9, 3) == p2.pad_line(9, 3) == "NDPAD 9 "
    for b in (0, -5, 1, 899, 900, 901, 50000, 65536, None, "x"):
        assert P.PadLineCount(b) == p2.pad_line_count(b), b
        if isinstance(b, int):
            assert p2.pad_line_count(b) == protocol.pad_line_count(b)  # the v1 rule is kept
    assert p2.pad_line_count(50000) == 56 and P.PadLineCount(1000, 300) == p2.pad_line_count(1000, 300) == 4
    for name, bit in P.FLAG.items():
        assert bit == int(Flags[name])
        for flags in range(256):
            assert P.HasFlag(flags, bit) == bool(flags & bit)
    assert P.HasFlag(None, 1) is False and P.HasFlag("x", 1) is False and P.HasFlag(5, 0) is False
    assert (P.PAD_PREFIX, P.PAD_LINE_LEN, P.MAX_STRIP_PX) == (p2.PAD_PREFIX, p2.PAD_LINE_LEN, p2.MAX_STRIP_PX)


def test_documented_vectors(lua):
    """docs/PROTOCOL-v2.md section 7."""
    vectors = [
        ({"type": 1, "seq": 0}, "4e 21 00 00 00 0c 6f", "NDROP2 TiEAAAAMbw"),
        ({"type": 4, "seq": 42, "flags": 0x20, "dropId": "zone:1429"},
         "4e 24 2a 20 0f 01 02 51 c8 02 09 7a 6f 6e 65 3a 31 34 32 39 4e bc", "NDROP2 TiQqIA8BAlHIAgl6b25lOjE0MjlOvA"),
        ({"type": 2, "seq": 8, "dropId": "area:87", "youtube": "https://youtu.be/dQw4w9WgXcQ", "artist": "Sigur Rós",
          "title": "Hoppípolla", "startMs": 12000},
         "4e 22 08 00 36 01 02 2f 44 02 07 61 72 65 61 3a 38 37 04 0b 64 51 77 34 77 39 57 67 58 63 51 05 16 53 69 67 "
         "75 72 20 52 c3 b3 73 1f 48 6f 70 70 c3 ad 70 6f 6c 6c 61 06 02 00 78 65 a7",
         "NDROP2 TiIIADYBAi9EAgdhcmVhOjg3BAtkUXc0dzlXZ1hjUQUWU2lndXIgUsOzcx9Ib3Bww61wb2xsYQYCAHhlpw"),
    ]
    for ev, hexbytes, line in vectors:
        assert py_frame(ev) == lua_frame(lua, ev) == bytes.fromhex(hexbytes)
        assert p2.format_chat_line(py_frame(ev)) == lua.P.FormatChatLine(lua_event(lua, ev)) == line
    hello = p2.encode_event_cells({"type": 1}, p2.Geometry.beacon(3))
    assert (hello.rows, hello.cols, hello.width_px, hello.height_px) == (1, 32, 96, 3)
    assert "".join("kbgcrmyw"[c] for c in hello.codes()[:11]) == "mgmggkkwkrm"


def test_query_split_keeps_both_parts():
    for a, t in [("A" * 100, "T" * 100), ("久" * 50, "T"), ("Björk", "x" * 200), ("久" * 30, "譲" * 30),
                 ("🎵" * 20, "🎶" * 20)]:
        q = p2.build_query(a, t)
        assert q is not None and len(q) <= p2.MAX_QUERY
        artist, sep, title = q.decode("utf-8").partition(p2.QUERY_SEP)  # always valid UTF-8
        assert sep and a.startswith(artist) and t.startswith(title)
        assert len(title.encode()) >= min(60, len(t.encode())) - 3  # title keeps >= 60 bytes (minus a split char)
        assert len(artist.encode()) >= min(59, len(a.encode())) - 3


# ------------------------------------------------------------- helpers --

def test_constants_match(lua):
    P = lua.P
    assert (P.MAGIC, P.VERSION, P.CHAT_PREFIX) == (p2.MAGIC, p2.VERSION, p2.CHAT_PREFIX)
    tags = {name: getattr(p2, "TAG_" + name) for name in
            ("PLACE_KEY", "DROP_ID", "SPOTIFY", "YOUTUBE", "QUERY", "START_DS", "LENGTH_DS", "VOLUME", "LABEL", "SET_ID")}
    assert {name: P.TAG[name] for name in tags} == tags
    assert (P.MAX_DROP_ID, P.MAX_QUERY, P.MAX_LABEL, P.MAX_SET_ID, P.MAX_BODY, P.MAX_FRAME) == (
        p2.MAX_DROP_ID, p2.MAX_QUERY, p2.MAX_LABEL, p2.MAX_SET_ID, p2.MAX_BODY, p2.MAX_FRAME)
    assert (P.LEAD_CELLS, P.MIN_COLS, P.MAX_COLS, P.COL_STEP, P.MAX_ROWS) == (
        p2.LEAD_CELLS, p2.MIN_COLS, p2.MAX_COLS, p2.COL_STEP, p2.MAX_ROWS)
    assert (P.WHITEN_SEED, P.WHITEN_MUL, P.WHITEN_MOD) == (p2.WHITEN_SEED, p2.WHITEN_MUL, p2.WHITEN_MOD)
    assert P.QUERY_SEP == p2.QUERY_SEP
    ks = P.Keystream(3000)
    assert bytes(int(ks[i]) for i in range(1, 3001)) == p2.keystream_bits(3000)
    assert sum(p2.keystream_bits(3000)) in range(1350, 1650)  # balanced
    for ev, ename in zip(range(6), ["NONE", "HELLO", "ARM", "FIRE", "CANCEL", "PING"]):
        assert P.EV[ename] == ev == int(EventType[ename])
    for name, f in P.FLAG.items():
        assert int(Flags[name]) == f


def test_fletcher_and_utf8_truncate(lua):
    rng = random.Random(1)
    for n in (0, 1, 2, 7, 255, 262, 1000):
        data = bytes(rng.randrange(256) for _ in range(n))
        assert lua.P.Fletcher16Bytes(ltable(lua, data)) == protocol.fletcher16(data)
        assert lua.P.Fletcher16String(data) == protocol.fletcher16(data)
    # 0x00 and 0xFF are the same byte to Fletcher-16 mod 255 -- why the beacon whitens its bits
    assert protocol.fletcher16(b"\x4e\x00") == protocol.fletcher16(b"\x4e\xff")
    samples = ["", "abc", "Sigur Rós", "坂本龍一", "🎵🎶🎧", "a🎵b", "👨‍👩‍👧", "Ólafur Arnalds", "x" * 60]
    for s in samples:
        b = s.encode()
        for cut in range(len(b) + 2):
            want = p2.utf8_truncate(b, cut)
            assert lua_str_bytes(lua, lua.P.Utf8Truncate, b, cut) == want
            want.decode("utf-8")  # never ends inside a sequence
            assert len(want) <= cut and b.startswith(want)
    assert p2.utf8_truncate("🎵🎵", 7) == "🎵".encode()  # 4-byte emoji, cut at 7 -> one emoji
    assert p2.utf8_truncate("é", 1) == b"" and p2.utf8_truncate("aé", 2) == b"a"


def test_utf8_clean_matches_on_arbitrary_bytes(lua):
    rng = random.Random(7)
    cases = [b"", b"plain", " Sigur Rós ".encode(), b"\xff\xfe", b"a\x80b", b"\xc0\xaf", b"\xe0\x80\x80",
             b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xf0\x9f\x8e", b"\xc3", b"x\xe2\x82", b"\x01\x1f\x7f",
             "🎵".encode() + b"\x80", b"\xf5\x80\x80\x80", b"   ", b"\t tab \n"]
    cases += [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40))) for _ in range(300)]
    for data in cases:
        want = p2.utf8_clean_bytes(data)
        assert lua_str_bytes(lua, lua.P.Utf8Clean, data) == want, data
        want.decode("utf-8")  # always valid UTF-8
    assert p2.utf8_clean_bytes(b"\xed\xa0\x80ok") == b"???ok"  # each invalid byte -> "?"
    assert p2.clean_text("a\x1fb\nc") == b"a b c" and p2.clean_text(None) == b"" and p2.clean_text(5) == b""
    assert p2.clean_text("\ud800x") == b"???x"  # lone surrogate: same repair as the Lua side


def test_sanitize_and_query_helpers_match(lua):
    for s in ["zone:1429", "sub:1:café", "a b|c", "x" * 60, "", "ü", "ok-._:", "日本"]:
        for n in (5, 32, 40):
            assert lua_str_bytes(lua, lua.P.SanitizeId, s, n) == p2.sanitize_id(s, n).encode()
    rng = random.Random(3)
    for _ in range(200):
        a = rng.choice(UTF8_NAMES + ["", "x" * rng.randint(0, 150)]) * rng.randint(1, 4)
        t = rng.choice(UTF8_NAMES + ["", "y" * rng.randint(0, 150)]) * rng.randint(1, 4)
        assert lua_str_bytes(lua, lua.P.BuildQuery, a, t) == p2.build_query(a, t)
    assert lua.P.PlaceKey("zone:1429") == p2.place_key_for("zone:1429") == protocol.fletcher16(b"zone:1429")
    assert lua.P.PlaceKey("") is None and p2.place_key_for("") is None


# ---------------------------------------------------------- link parsers --

LINKS = [
    # (kind, input, expected id)
    ("spotify", f"spotify:track:{SPOTIFY_ID}", SPOTIFY_ID),
    ("spotify", f"SPOTIFY:TRACK:{SPOTIFY_ID}", SPOTIFY_ID),
    ("spotify", f"https://open.spotify.com/track/{SPOTIFY_ID}?si=a1b2c3", SPOTIFY_ID),
    ("spotify", f"http://open.spotify.com/track/{SPOTIFY_ID}", SPOTIFY_ID),
    ("spotify", f"open.spotify.com/track/{SPOTIFY_ID}", SPOTIFY_ID),
    ("spotify", f"https://open.spotify.com/intl-de/track/{SPOTIFY_ID}?si=x", SPOTIFY_ID),
    ("spotify", f"https://open.spotify.com/intl-pt-br/track/{SPOTIFY_ID}", SPOTIFY_ID),
    ("spotify", f"https://open.spotify.com/embed/track/{SPOTIFY_ID}", SPOTIFY_ID),
    ("spotify", f"  https://open.spotify.com/track/{SPOTIFY_ID}#frag \n", SPOTIFY_ID),
    ("spotify", f"https://open.spotify.com/track/{SPOTIFY_ID}/", SPOTIFY_ID),
    ("spotify", f"https://open.spotify.com/track/{SPOTIFY_ID}?si=1&context=spotify%3Aplaylist", SPOTIFY_ID),
    ("spotify", f"HTTPS://OPEN.SPOTIFY.COM/TRACK/{SPOTIFY_ID}", SPOTIFY_ID),
    ("spotify", f"https://play.spotify.com/track/{SPOTIFY_ID}", SPOTIFY_ID),
    ("spotify", SPOTIFY_ID, SPOTIFY_ID),
    ("spotify", f"https://open.spotify.com/album/{SPOTIFY_ID}", None),
    ("spotify", f"spotify:album:{SPOTIFY_ID}", None),
    ("spotify", f"spotify:track:{SPOTIFY_ID[:-1]}", None),
    ("spotify", f"spotify:track:{SPOTIFY_ID}1", None),
    ("spotify", f"spotify:track:{SPOTIFY_ID}?si=x", None),
    ("spotify", f"https://open.spotify.com/track/{SPOTIFY_ID}/extra", None),
    ("spotify", f"https://open.spotify.com.evil.example/track/{SPOTIFY_ID}", None),
    ("spotify", f"https://evil.example/track/{SPOTIFY_ID}", None),
    ("spotify", "https://spotify.link/abc123XYZ", None),
    ("spotify", "spotify:track:", None),
    ("spotify", SPOTIFY_ID[:-1] + "-", None),
    ("spotify", "Ólafur Arnalds", None),
    ("spotify", "", None),
    ("spotify", 42, None),
    ("spotify", None, None),
    ("youtube", YT_ID, YT_ID),
    ("youtube", f"  {YT_ID}  ", YT_ID),
    ("youtube", "_-_-_-_-_-_", "_-_-_-_-_-_"),
    ("youtube", f"https://youtu.be/{YT_ID}", YT_ID),
    ("youtube", f"https://youtu.be/{YT_ID}?si=abc&t=42", YT_ID),
    ("youtube", f"HTTPS://YOUTU.BE/{YT_ID}", YT_ID),
    ("youtube", f"https://www.youtube.com/watch?v={YT_ID}", YT_ID),
    ("youtube", f"https://www.youtube.com/watch?feature=share&v={YT_ID}&t=1m", YT_ID),
    ("youtube", f"https://m.youtube.com/watch?v={YT_ID}#t=30", YT_ID),
    ("youtube", f"https://music.youtube.com/watch?v={YT_ID}&list=RDAMVM", YT_ID),
    ("youtube", f"youtube.com/watch?v={YT_ID}", YT_ID),
    ("youtube", f"https://youtube.com/watch/?v={YT_ID}", YT_ID),
    ("youtube", f"https://www.youtube.com/shorts/{YT_ID}?feature=share", YT_ID),
    ("youtube", f"https://www.youtube.com/shorts/{YT_ID}/", YT_ID),
    ("youtube", f"https://www.youtube.com/embed/{YT_ID}?start=30", YT_ID),
    ("youtube", f"https://www.youtube-nocookie.com/embed/{YT_ID}", YT_ID),
    ("youtube", f"https://www.youtube.com/live/{YT_ID}", YT_ID),
    ("youtube", f"https://www.youtube.com/v/{YT_ID}", YT_ID),
    ("youtube", f"https://www.youtube.com/watch?v={YT_ID[:-1]}", None),
    ("youtube", f"https://www.youtube.com/watch?v={YT_ID}Q", None),
    ("youtube", f"https://www.youtube.com/watch?vv={YT_ID}", None),
    ("youtube", f"https://www.youtube.com/watch#v={YT_ID}", None),
    ("youtube", "https://www.youtube.com/playlist?list=PL1234567890", None),
    ("youtube", "https://www.youtube.com/@channel", None),
    ("youtube", f"https://www.youtube.com/embed/{YT_ID}.jpg", None),
    ("youtube", f"https://evilyoutube.com/watch?v={YT_ID}", None),
    ("youtube", f"https://www.youtube.com.evil.example/watch?v={YT_ID}", None),
    ("youtube", "https://vimeo.com/123456789", None),
    ("youtube", "https://youtu.be/", None),
    ("youtube", "dQw4w9WgXc!", None),
    ("youtube", "坂本龍一坂本龍一坂本", None),
    ("youtube", "", None),
    ("youtube", 7, None),
    ("youtube", None, None),
]


@pytest.mark.parametrize("kind,text,want", LINKS, ids=lambda v: str(v)[:48] if v is not None else "None")
def test_link_parsers_agree(lua, kind, text, want):
    py_fn = p2.spotify_id_from_link if kind == "spotify" else p2.youtube_id_from_link
    lua_fn = lua.P.SpotifyIdFromLink if kind == "spotify" else lua.P.YouTubeIdFromLink
    assert py_fn(text) == want
    assert lua_fn(text) == want


def test_link_table_is_large_and_mixed():
    assert len(LINKS) >= 30
    assert sum(1 for _k, _t, w in LINKS if w is None) >= 15


# ------------------------------------------------------------- chat line --

def test_chat_lines_match_and_parse(lua):
    for param in EVENTS:
        ev = param.values[0]
        line = lua.P.FormatChatLine(lua_event(lua, ev))
        frame_bytes = py_frame(ev)
        assert line == p2.format_chat_line(frame_bytes) == p2.format_chat_line(p2.frame_from_event(ev))
        assert line.startswith("NDROP2 ") and line.isascii() and " " not in line[7:]
        want = p2.decode_frame(frame_bytes)
        assert p2.parse_chat_line(line) == want
        assert p2.parse_chat_line("11/5 18:01:02.345  " + line) == want
        assert p2.parse_chat_line("11/5/2026 18:01:02  " + line + "\r\n") == want
        # strict mode: a record typed in chat by someone else never counts
        for chat in (f"11/5 18:01:02.345  [Bob] says: {line}", f"[2. Trade] [Bob]: {line}", f"x{line}",
                     f"11/5 18:01:02.345  {line} trailing"):
            assert p2.parse_chat_line(chat) is None, chat
        assert p2.parse_chat_line(f"[Bob] says: {line}", strict=False) == want


def test_chat_line_corruption_rejected():
    line = p2.format_chat_line(p2.frame_from_event(EVENTS[0].values[0]))
    token = line[7:]
    for i in range(len(token)):
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        bad = token[:i] + alphabet[(alphabet.index(token[i]) + 1) % 64] + token[i + 1:]
        assert p2.parse_chat_line("NDROP2 " + bad) is None, i
    assert p2.parse_chat_line("NDROP2 " + token[:-1]) is None
    assert p2.parse_chat_line("NDROP2 " + token + "A") is None
    assert p2.parse_chat_line("NDROP2 ") is None and p2.parse_chat_line("NDROP2 !!!!") is None
    assert p2.parse_chat_line("NDROP1 1 2 3 4 5 zone:1 abcd") is None
    assert p2.parse_chat_line(None) is None  # type: ignore[arg-type]


def test_base64url_matches(lua):
    rng = random.Random(9)
    for n in range(0, 70):
        data = bytes(rng.randrange(256) for _ in range(n))
        enc = p2.b64url_encode(data)
        assert lua.P.Base64Url(ltable(lua, data)) == enc
        assert "=" not in enc and "+" not in enc and "/" not in enc
        assert p2.b64url_decode(enc) == data
        assert lbytes(lua.P.Base64UrlDecode(enc)) == data
    for bad in ["A", "AAAAA", "A=A=", "AB+/", "AB*C", "AB", "ABC"]:  # "AB"/"ABC": non-canonical tails
        py = p2.b64url_decode(bad)
        lu = lua.P.Base64UrlDecode(bad)
        assert (py is None) == (lu is None), bad
    assert p2.b64url_decode("AB") is None and p2.b64url_decode("AA") == b"\x00"


# ------------------------------------------------------- decode validation --

def _reframe(head_and_body: bytes) -> bytes:
    """Recompute the trailer so only the structural rule under test is broken."""
    return head_and_body + bytes(divmod(protocol.fletcher16(head_and_body), 256))


GOOD = p2.encode_frame(p2.frame_from_event(EVENTS[0].values[0]))


def _with_body(body: bytes, *, t: int = 3) -> bytes:
    return _reframe(bytes((0x4E, 0x20 | t, 1, 0, len(body))) + body)


@pytest.mark.parametrize("name,data", [
    ("short", GOOD[:6]),
    ("bad-magic", _reframe(b"\x4f" + GOOD[1:-2])),
    ("v1-version", _reframe(bytes((0x4E, 0x13)) + GOOD[2:-2])),
    ("type-6", _with_body(b"", t=6)),
    ("length-mismatch", GOOD + b"\x00"),
    ("checksum", GOOD[:-1] + bytes(((GOOD[-1] + 1) % 256,))),
    ("tag-zero", _with_body(b"\x00\x00")),
    ("descending-tags", _with_body(b"\x02\x01a\x01\x02\x00\x01")),
    ("duplicate-tags", _with_body(b"\x02\x01a\x02\x01b")),
    ("tlv-overrun", _with_body(b"\x02\x05ab")),
    ("half-tlv", _with_body(b"\x0b")),
    ("placekey-len", _with_body(b"\x01\x01\x00")),
    ("dropid-charset", _with_body(b"\x02\x03a b")),
    ("dropid-empty", _with_body(b"\x02\x00")),
    ("dropid-too-long", _with_body(b"\x02\x29" + b"a" * 41)),
    ("spotify-len", _with_body(b"\x03\x15" + b"a" * 21)),
    ("spotify-charset", _with_body(b"\x03\x16" + b"a" * 21 + b"-")),
    ("youtube-len", _with_body(b"\x04\x0a" + b"a" * 10)),
    ("query-empty", _with_body(b"\x05\x00")),
    ("query-too-long", _with_body(b"\x05\x79" + b"q" * 121)),
    ("start-len", _with_body(b"\x06\x01\x05")),
    ("volume-101", _with_body(b"\x08\x01\x65")),
    ("label-too-long", _with_body(b"\x09\x31" + b"l" * 49)),
    ("setid-charset", _with_body(b"\x0a\x02\xc3\xbc")),
])
def test_decoders_reject(lua, name, data):
    assert p2.decode_frame(data) is None
    assert lua.P.DecodeFrame(ltable(lua, data)) is None


def test_unknown_tags_are_skipped_forward_compatible(lua):
    body = b"\x02\x09zone:1429" + b"\x0b\x03abc" + b"\x7f\x00" + b"\xfe\x02\x01\x02"
    data = _with_body(body)
    f = p2.decode_frame(data)
    assert f is not None and f.drop_id == "zone:1429"
    assert f.extra == (p2.TLV(0x0B, b"abc"), p2.TLV(0x7F, b""), p2.TLV(0xFE, b"\x01\x02"))
    assert p2.encode_frame(f) == data  # extras round-trip in tag order
    d = lua.P.DecodeFrame(ltable(lua, data))
    assert d.dropId == "zone:1429" and len(d.unknown) == 3 and d.unknown[1].tag == 0x0B and d.unknown[1].value == "abc"
    # a decoder of this version still accepts every known tag in a frame from a newer encoder
    with pytest.raises(ValueError):
        p2.encode_frame(dataclasses.replace(f, extra=(p2.TLV(0x05, b"x"),)))  # known tag as "extra"


def test_encode_frame_is_strict():
    base = p2.Frame(EventType.FIRE, seq=1)
    for bad in [dict(seq=256), dict(place_key=-1), dict(drop_id="a b"), dict(drop_id="x" * 41), dict(spotify_id="x"),
                dict(youtube_id="abc"), dict(query=""), dict(query="q" * 121), dict(start_ds=70000), dict(volume=101),
                dict(label="l" * 49), dict(set_id="s" * 33), dict(extra=(p2.TLV(0x20, b"x" * 256),))]:
        with pytest.raises(ValueError):
            p2.encode_frame(dataclasses.replace(base, **bad))
    with pytest.raises(ValueError):  # body over 255 bytes
        p2.encode_frame(dataclasses.replace(base, extra=(p2.TLV(0x20, b"x" * 200), p2.TLV(0x21, b"y" * 60))))


def test_decode_frame_never_raises_on_garbage():
    rng = random.Random(11)
    for _ in range(3000):
        n = rng.randrange(0, 40)
        data = bytes(rng.randrange(256) for _ in range(n))
        if rng.random() < 0.5 and n >= 5:  # plausible heads, so the TLV walker really runs
            data = bytes((0x4E, 0x20 | rng.randrange(6), 0, 0, max(0, n - 7))) + data[5:]
            data = _reframe(data[:-2]) if n >= 7 else data
        f = p2.decode_frame(data)
        assert f is None or f.raw == data
    assert p2.decode_frame(None) is None and p2.decode_frame("text") is None  # type: ignore[arg-type]


# ------------------------------------------------------------- pixel cells --

GEOMETRIES = [
    {"cellsPerRow": 64, "cellPx": 3, "corner": "TOPLEFT"},
    {"cellsPerRow": 16, "cellPx": 1, "corner": "TOPRIGHT"},
    {"cellsPerRow": 32, "cellPx": 8, "corner": "bottomleft"},
    {"cellsPerRow": 48, "cellPx": 2, "corner": "BOTTOMRIGHT"},
    {"cellsPerRow": 112, "cellPx": 4},
    {},
    # adaptive (the beacon): fitted to the row capacity, columns per frame
    {"cellPx": 2, "maxRows": 4},
    {"cellPx": 3, "maxRows": 3},
    {"cellPx": 4, "maxRows": 2, "corner": "TOPRIGHT"},
    {"cellPx": 3, "maxRows": 1, "cellsPerRow": 16},
]


@pytest.mark.parametrize("geom", GEOMETRIES, ids=lambda g: "-".join(f"{v}" for v in g.values()) or "default")
@pytest.mark.parametrize("ev", [EVENTS[0], EVENTS[9], EVENTS[11], EVENTS[3]])
def test_cells_match(lua, ev, geom):
    lg = lua.P.EncodeCells(lua_event(lua, ev), lua.rt.table_from(geom))
    g = p2.Geometry.normalize(geom)
    grid = p2.encode_event_cells(ev, g)
    budget = p2.MAX_FRAME if g.max_rows is None else p2.capacity(g.max_rows)
    if g.max_rows is None:
        assert grid == p2.encode_cells(p2.frame_from_event(ev), g)
    assert (lg.rows, lg.cols, lg.cellPx, lg.corner) == (grid.rows, grid.cols, g.cell_px, g.corner)
    assert (lg.widthPx, lg.heightPx) == p2.strip_size(grid.frame_len, g)[:2] == (grid.width_px, grid.height_px)
    cells = lua_grid_cells(lg)
    assert len(cells) == grid.rows * grid.cols
    assert cells == list(grid.cells)
    assert cells[:4] == list(p2.HEADER)
    assert p2.decode_cells_grid(grid.as_rows()) == p2.frame_from_event(ev, max_bytes=budget)
    assert grid.rows == p2.rows_for(grid.frame_len, grid.cols) <= p2.MAX_ROWS
    assert tuple(grid.codes()[7:11]) == p2.KNOWN_PREFIX_CODES
    r, c = lua.P.CellPosition(grid.cols + 2, grid.cols)
    assert (r, c) == (1, 1)


def test_geometry_normalisation_matches(lua):
    odd = [{"cellsPerRow": 70}, {"cellsPerRow": 7}, {"cellsPerRow": 500}, {"cellsPerRow": 24}, {"cellsPerRow": "64"},
           {"cellPx": 0}, {"cellPx": 3.6}, {"cellPx": 99}, {"corner": "middle"}, {"corner": "TopRight"},
           {"cellsPerRow": float("nan")}, {},
           {"maxRows": 3}, {"maxRows": 9, "cellPx": 2}, {"maxRows": 0}, {"maxRows": 2.7}, {"maxRows": -4},
           {"maxRows": float("nan")}, {"maxRows": "2"}, {"cellPx": 8, "maxRows": 5}, {"cellPx": 1, "maxRows": 99},
           {"cellPx": 3.6, "maxRows": 3}]
    for geom in odd:
        lg = lua.P.NormalizeGeometry(lua.rt.table_from(geom))
        g = p2.Geometry.normalize(geom)
        assert (lg.cellsPerRow, lg.cellPx, lg.corner, lg.maxRows) == (g.cells_per_row, g.cell_px, g.corner,
                                                                      g.max_rows), geom
    assert p2.Geometry.normalize({"maxRows": 9, "cellPx": 2}).max_rows == 4
    assert p2.Geometry.normalize({"cellPx": 3.6, "maxRows": 3}).max_rows == 2  # 4 px cells: two rows
    assert p2.Geometry.beacon(3) == p2.Geometry(64, 3, "TOPLEFT", 3)
    with pytest.raises(ValueError):
        p2.Geometry(cells_per_row=70)
    with pytest.raises(ValueError):
        p2.Geometry(cell_px=9)
    with pytest.raises(ValueError):
        p2.Geometry(corner="MIDDLE")
    with pytest.raises(ValueError):
        p2.Geometry(cell_px=3, max_rows=4)  # 12 px > the 9 px cap
    with pytest.raises(ValueError):
        p2.Geometry(max_rows=0)


def test_max_frames_fit_every_width():
    big = p2.encode_frame(p2.frame_from_event(EVENTS[9].values[0]))
    for cols in range(p2.MIN_COLS, p2.MAX_COLS + 1, p2.COL_STEP):
        grid = p2.encode_cells(big, p2.Geometry(cols))
        assert grid.rows <= p2.MAX_ROWS
        assert p2.decode_cells_grid(grid.as_rows()) is not None
    # the original 2-geometry-cell idea (rows 1..8) could not carry this frame at 64 cells per row
    assert p2.rows_for(len(big), 64) > 8


def _grid_codes(grid: p2.CellGrid) -> list[int]:
    return grid.codes()


def test_decode_cells_grid_rejects_damage():
    frame = p2.frame_from_event(EVENTS[0].values[0])
    grid = p2.encode_cells(frame, p2.Geometry(32))
    codes = _grid_codes(grid)
    ok = p2.decode_cell_codes(codes, grid.cols, grid.rows)
    assert ok == frame
    # every single-bit error in any data or pad cell is detected
    for i in range(p2.LEAD_CELLS, len(codes)):
        for bit in (4, 2, 1):
            bad = list(codes)
            bad[i] ^= bit
            assert p2.decode_cell_codes(bad, grid.cols, grid.rows) is None, (i, bit)
    # header / geometry damage, wrong row count, missing last row
    for i in range(p2.LEAD_CELLS):
        for delta in range(1, 8):
            bad = list(codes)
            bad[i] = (bad[i] + delta) % 8
            assert p2.decode_cell_codes(bad, grid.cols, grid.rows) is None, (i, delta)
    assert p2.decode_cell_codes(codes[:-grid.cols], grid.cols, grid.rows - 1) is None
    assert p2.decode_cell_codes(codes + [0] * grid.cols, grid.cols, grid.rows + 1) is None
    assert p2.decode_cell_codes(codes, 48, grid.rows) is None
    assert p2.decode_cell_codes([9] * len(codes), grid.cols, grid.rows) is None
    assert p2.decode_cells_grid([]) is None and p2.decode_cells_grid([[(0, 0, 0)], []]) is None


@pytest.mark.parametrize("cover", [(0, 0, 0), (255, 255, 255), (30, 30, 34), (255, 0, 255)])
def test_uniform_covers_fail_the_checks(cover):
    """Whitening turns a uniform cover (tooltip, window edge, black bar) into pseudo-random
    bit errors, so the frame is rejected even over runs of 0x00 / 0xFF bytes that Fletcher-16
    alone could not tell apart."""
    ev = {"type": 3, "seq": 0, "dropId": "zone:1", "startDs": 256, "lengthDs": 65535, "volume": 0,
          "spotify": SPOTIFY_ID}
    grid = p2.encode_cells(p2.frame_from_event(ev), p2.Geometry(32))
    code = ((cover[0] >= 128) << 2) | ((cover[1] >= 128) << 1) | (cover[2] >= 128)
    codes = grid.codes()
    rejected = 0
    trials = 0
    for start in range(p2.LEAD_CELLS, len(codes) - 1):
        for length in (1, 2, 3, 5, 8, 13):
            if start + length > len(codes):
                continue
            bad = list(codes)
            bad[start:start + length] = [code] * length
            if bad == codes:
                continue
            trials += 1
            got = p2.decode_cell_codes(bad, grid.cols, grid.rows)
            assert got is None or got == p2.frame_from_event(ev)
            rejected += got is None
    assert trials > 100 and rejected == trials


def test_lua_cells_decode_with_python(lua):
    """End-to-end contract: what the addon would draw decodes in the companion."""
    rng = random.Random(5)
    for _ in range(40):
        ev = _random_event(rng)
        cols = rng.choice([16, 32, 48, 64, 80, 96, 112])
        lg = lua.P.EncodeCells(lua_event(lua, ev), lua.rt.table_from({"cellsPerRow": cols}))
        rows = [[tuple(255 * int(lg.cells[r * cols + c + 1][k]) for k in (1, 2, 3)) for c in range(cols)]
                for r in range(int(lg.rows))]
        assert p2.decode_cells_grid(rows) == p2.frame_from_event(ev)


def test_threshold_in_decode_cells_grid():
    grid = p2.encode_cells(p2.frame_from_event(EVENTS[1].values[0]), p2.Geometry(64))
    dim = [[tuple(v * 100 // 255 for v in c) for c in row] for row in grid.as_rows()]
    assert p2.decode_cells_grid(dim) is None
    assert p2.decode_cells_grid(dim, threshold=50) == p2.frame_from_event(EVENTS[1].values[0])


def test_all_event_types_round_trip_through_every_layer(lua):
    for t, name in zip(range(6), ["NONE", "HELLO", "ARM", "FIRE", "CANCEL", "PING"]):
        ev = {"type": t, "seq": t * 40, "flags": t, "dropId": f"zone:{t}", "artist": "A", "title": "T"}
        f = p2.frame_from_event(ev)
        b = p2.encode_frame(f)
        assert f.type == EventType[name]
        assert p2.parse_chat_line(p2.format_chat_line(b)) == f
        for cols in (16, 64, 112):
            assert p2.decode_cells_grid(p2.encode_cells(b, p2.Geometry(cols)).as_rows()) == f
        assert lua_frame(lua, ev) == b


def test_known_prefix_is_fixed_and_distinct():
    codes = set()
    for cols, ev in itertools.product((16, 64), (EVENTS[0], EVENTS[11])):
        grid = p2.encode_cells(p2.frame_from_event(ev.values[0]), p2.Geometry(cols))
        codes.add(tuple(grid.codes()[7:11]))
    assert codes == {p2.KNOWN_PREFIX_CODES}
    pc = p2.KNOWN_PREFIX_CODES
    assert all(a != b for a, b in zip(pc, pc[1:]))  # adjacent prefix cells always show a transition
