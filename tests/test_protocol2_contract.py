"""Cross-language contract for protocol v2 (docs/PROTOCOL-v2.md).

The addon's ``Protocol2.lua`` (run here in ``lupa.lua51``) and the companion's
``needledropper/protocol2.py`` must agree byte for byte on frames, chat lines and pixel cell
grids, and on every helper that shapes them: UTF-8 cleaning / truncation, id sanitising, the
artist/title query, the Spotify / YouTube link parsers, the fit rule at every budget (the
264-byte chat frame and the beacon's 2 / 3 / 4 px row caps, DESIGN-v1.2 2.3, with the F3 / F4
fixes), the adaptive strip geometry, the 4-byte frame check (finding F1), the v1.2.1 CONFIG
event with its PREFS / ACTION / CLIENT_ID fields and HELLO's PREFS and placeKey
(DESIGN-v1.2.1 2), and the NDPAD / flag helpers. Also covers the decoders' validation rules
(both languages, strict UTF-8 text), forward compatibility, the column covers of finding F1,
the measured-size tables and the vectors in ``tests/vectors/protocol2/frames.json``.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
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
CLIENT_ID = "0123456789abcdef0123456789abcdef"
VECTORS_FILE = ROOT / "tests" / "vectors" / "protocol2" / "frames.json"


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
    """A Lua table for an event dict; nested dicts (prefs, action) become nested tables
    (``table_from`` alone is shallow)."""
    return lua.rt.table_from({k: lua_event(lua, v) if isinstance(v, dict) else v for k, v in ev.items()
                              if v is not None})


def lua_decoded(d) -> dict:
    """Lua DecodeFrame's result -> the dict of fields protocol2 frames give (see py_decoded)."""
    out = {"type": d.type, "seq": d.seq, "flags": d.flags}
    for k in ("placeKey", "dropId", "spotify", "youtube", "query", "startDs", "lengthDs", "volume", "label", "setId",
              "clientId"):
        if d[k] is not None:
            out[k] = d[k]
    if d.prefs is not None:
        out["prefs"] = {k: d.prefs[k] for k in ("order", "corner", "fade", "resume", "volume") if d.prefs[k] is not None}
    if d.action is not None:
        out["action"] = {k: d.action[k] for k in ("code", "name", "at") if d.action[k] is not None}
    return out


def py_decoded(f: p2.Frame) -> dict:
    out = {"type": int(f.type), "seq": f.seq, "flags": int(f.flags)}
    for k, v in (("placeKey", f.place_key), ("dropId", f.drop_id), ("spotify", f.spotify_id),
                 ("youtube", f.youtube_id), ("query", f.query), ("startDs", f.start_ds), ("lengthDs", f.length_ds),
                 ("volume", f.volume), ("label", f.label), ("setId", f.set_id), ("clientId", f.client_id)):
        if v is not None:
            out[k] = v
    if f.prefs is not None:
        out["prefs"] = {k: v for k, v in dataclasses.asdict(f.prefs).items() if v is not None}
    if f.action is not None:
        out["action"] = {"code": f.action.code, "at": f.action.issued_at, **({"name": f.action.name} if f.action.name
                                                                               else {})}
    return out


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
    # v1.2.1 (DESIGN-v1.2.1 2): HELLO with PREFS and the playing drop's placeKey, CONFIG
    pytest.param({"type": 1, "seq": 3, "flags": 0x20, "dropId": "zone:1411", "artist": "dropped",
                  "prefs": {"order": "Spotify-YouTube", "corner": "TOPRIGHT", "fade": True, "resume": False}},
                 id="hello-playing-prefs"),
    pytest.param({"type": 6, "seq": 12, "flags": 0x02, "dropId": "dropped", "volume": 50,
                  "prefs": {"order": "youtube", "corner": "bottomleft", "fade": 1, "resume": None}}, id="config-prefs"),
    pytest.param({"type": 6, "seq": 13, "action": {"name": "TEST-youtube", "at": 1762380000.9},
                  "prefs": {"order": "none"}}, id="config-action-prefs"),
    pytest.param({"type": 6, "seq": 14, "action": {"name": "connect-spotify", "at": 1762380123},
                  "clientId": " 0123456789ABCDEF\n0123456789abcdef "}, id="config-connect"),
    pytest.param({"type": 6, "seq": 15, "action": {"name": "nope", "at": 5}, "clientId": "0123",
                  "prefs": {"order": 3, "fade": "yes"}}, id="config-all-invalid"),
    pytest.param({"type": 3, "seq": 16, "dropId": "zone:9", "youtube": YT_ID, "prefs": {"order": "none"},
                  "action": {"name": "test-spotify", "at": 1}, "clientId": CLIENT_ID}, id="fire-drops-config-fields"),
    pytest.param({"type": 6, "seq": 17, "action": {"name": "disconnect-spotify", "at": -5}, "clientId": CLIENT_ID,
                  "prefs": {"order": "spotify", "corner": "topleft", "fade": False, "resume": True}},
                 id="config-bound-all-three"),
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
    assert lua_decoded(d) == py_decoded(frame)
    if frame.query is not None:
        assert (d.artist, d.title) == (frame.artist, frame.title)


def _random_prefs(rng: random.Random):
    names = list(p2.PREFS_ORDERS) + list(p2.PREFS_CORNERS) + ["SPOTIFY", "TopLeft", "", "x", 3, None, True]
    return {"order": rng.choice(names), "corner": rng.choice(names), "fade": rng.choice([True, False, None, 1, "on"]),
            "resume": rng.choice([True, False, None, 0])}


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
        "type": rng.randrange(7), "seq": rng.choice([rng.randrange(256), rng.randrange(-300, 600)]),
        "flags": rng.randrange(256), "dropId": maybe(0.9, rng.choice(ids)),
        "placeKey": maybe(0.1, rng.randrange(-5, 70000)),
        "spotify": maybe(0.5, rng.choice(links_s)), "youtube": maybe(0.5, rng.choice(links_y)),
        "artist": maybe(0.8, rng.choice(text)), "title": maybe(0.8, rng.choice(text)),
        "startMs": maybe(0.4, rng.choice(num)), "startDs": maybe(0.2, rng.choice(num)),
        "lengthMs": maybe(0.4, rng.choice(num)), "lengthDs": maybe(0.2, rng.choice(num)),
        "volume": maybe(0.5, rng.choice([0, 50, 80.5, 100, 101, -1, 33.4999])),
        "label": maybe(0.7, rng.choice(text)), "setId": maybe(0.5, rng.choice(["azeroth-mixtape", "s" * 40, "ü-set", ""])),
        "prefs": maybe(0.3, _random_prefs(rng)),
        "action": maybe(0.3, {"name": rng.choice(list(p2.ACTIONS) + ["Connect-Spotify", "x", None]),
                              "at": rng.choice([0, 1762380123, 4294967296, -1, 1.5e9 + 0.7, None, "1"])}),
        "clientId": maybe(0.3, rng.choice([CLIENT_ID, CLIENT_ID.upper(), " " + CLIENT_ID + "\t", CLIENT_ID[:-1],
                                           CLIENT_ID + "0", "g" * 32, ""])),
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
        d = lua.P.DecodeFrame(ltable(lua, pb))
        assert d is not None and lua_decoded(d) == py_decoded(frame)
        sizes.append(len(pb))
    assert min(sizes) == p2.FRAME_OVERHEAD and max(sizes) > 200  # empty HELLOs through near-max frames


def test_fit_rule_max_size_frame():
    """Every field at its cap is a 299-byte body (308-byte frame): within the 264-byte chat
    budget the fit rule drops setId, then the whole label (DESIGN 2.3 steps 1-2), and keeps
    everything else, so the chat line of the largest possible song event is 224 bytes."""
    ev = EVENTS[9].values[0]
    raw_body = 4 + 42 + 24 + 13 + 121 + 4 + 4 + 3 + 50 + 34   # all ten fields at their caps
    assert raw_body == 299 > p2.MAX_BODY
    f = p2.frame_from_event(ev)
    b = p2.encode_frame(f)
    assert len(b) == p2.FRAME_OVERHEAD + raw_body - 34 - 50 == 224  # was 222: the trailer grew 2 -> 4 bytes (F1)
    assert f.set_id is None and f.label is None
    assert len(f.query.encode()) <= p2.MAX_QUERY and p2.QUERY_SEP in f.query
    assert f.drop_id == ("sub:1429:" + "x" * 60)[:40]
    assert f.place_key == protocol.fletcher16(("sub:1429:" + "x" * 60).encode())  # of the full id
    assert (f.spotify_id, f.youtube_id, f.start_ds, f.length_ds, f.volume) == (SPOTIFY_ID, YT_ID, 65535, 65535, 100)
    # with room to spare nothing is dropped, and frames up to the 262-byte bound still occur
    small = p2.frame_from_event({**ev, "artist": "A", "title": "T"})
    assert small.set_id == "s" * 32 and small.label == "Ł" * 24
    big = p2.encode_frame(p2.frame_from_event({**ev, "setId": None, "label": "L" * 38}))
    assert len(big) == p2.MAX_FRAME == 264 and p2.decode_frame(big).label == "L" * 38  # was 262 (2-byte trailer)
    over = p2.frame_from_event({**ev, "setId": None, "label": "L" * 39})  # one byte too many
    assert over.label is None and len(p2.encode_frame(over)) == 224  # was 222 (2-byte trailer)
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
    # HELLO: only the placeKey of its drop id (D3: the playing drop's identity), no other song field
    hello = p2.encode_frame(p2.frame_from_event(EVENTS[11].values[0]))
    assert hello[4:9] == b"\x04\x01\x02" + p2.place_key_for("zone:1429").to_bytes(2, "big")
    assert p2.encode_frame(p2.frame_from_event({"type": 1, "artist": "x", "volume": 3}))[4] == 0  # no id: empty body
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
    with pytest.raises(ValueError):
        p2.build_frame(7)  # 7..15 are not event types; 6 is CONFIG
    assert p2.build_frame(6).type == EventType.CONFIG


# --------------------------------------------------------------- fit rule --

#: DESIGN 2.3's "FIRE user drop with every field (sub:1429:lion-s-pride-inn)": 143 bytes (141 with
#: the 2-byte trailer).
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
    """DESIGN 2.3: 1 setId, 2 label, 3 query (the preferred provider's id is present), 4 dropId
    (placeKey stays), 5 shorten query, 6 volume, 7 keep only the preferred provider's id -- each
    step only while the frame is still over budget; placeKey, start, length, flags and one id
    always stay. Every size is 2 bytes more than with the old Fletcher-16 trailer (F1)."""
    ev = FULL_USER_DROP
    assert len(py_frame(ev)) == 143
    stages = [(143, ALL_FIELDS),
              (133, ALL_FIELDS - {"setId"}),
              (115, ALL_FIELDS - {"setId", "label"}),
              (88, ALL_FIELDS - {"setId", "label", "query"}),
              (61, ALL_FIELDS - {"setId", "label", "query", "dropId"}),
              (58, {"placeKey", "spotify", "youtube", "startDs", "lengthDs"})]
    last = {"spotify": 45, "youtube": 34}[kept]
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
    step 5 -- artist and title separately (finding F3), each a code-point-safe prefix of its own
    part, the separator always kept -- and only when not even MIN_QUERY_PART bytes of each fit
    do the query and then the volume go."""
    full = len(py_frame(QUERY_ONLY))
    assert full == 140 > p2.capacity(3)  # was 138 (2-byte trailer)
    q_full = p2.build_query(QUERY_ONLY["artist"], QUERY_ONLY["title"])
    a_full, _, t_full = q_full.partition(b"\x1f")
    floor = p2.FRAME_OVERHEAD + 4 + 4 + 4  # 21: head + check + placeKey + start + length
    states = []
    for budget in range(full, p2.FRAME_OVERHEAD - 1, -1):
        f = p2.frame_from_event(QUERY_ONLY, max_bytes=budget)
        size = len(p2.encode_frame(f))
        assert size <= max(budget, floor), budget
        assert {"placeKey", "startDs", "lengthDs"} <= _present(f)
        if f.query is not None:
            q = f.query.encode()
            a, sep, t = q.partition(b"\x1f")
            assert sep and a_full.startswith(a) and t_full.startswith(t)  # two prefixes, never one fragment
            assert a.decode("utf-8") and t.decode("utf-8")  # each cut on a code-point boundary, neither empty
            assert len(a) >= p2.MIN_QUERY_PART - 2 and len(t) >= p2.MIN_QUERY_PART - 2  # 3-byte code points
            assert f.volume == 70  # the volume goes only after the whole query
            state = "full" if q == q_full else "shortened"
            if state == "shortened":
                assert f.drop_id is None and size >= budget - 4  # just enough: two cuts, <= 2 bytes slack each
        else:
            assert f.drop_id is None and f.label is None and f.set_id is None
            state = "no query" if f.volume is not None else "no volume"
        if not states or states[-1] != state:
            states.append(state)
    assert states == ["full", "shortened", "no query", "no volume"]
    # the boundary: 24 bytes without the query, so 43 keeps 8 bytes of room for each part, 42 does not
    keep = p2.frame_from_event(QUERY_ONLY, max_bytes=43)
    assert keep.query == "坂本\x1f戦場の" and len(p2.encode_frame(keep)) == 42
    gone = p2.frame_from_event(QUERY_ONLY, max_bytes=42)
    assert gone.query is None and gone.volume == 70 and len(p2.encode_frame(gone)) == 24
    tiny = p2.frame_from_event(QUERY_ONLY, max_bytes=p2.FRAME_OVERHEAD)
    assert _present(tiny) == {"placeKey", "startDs", "lengthDs"} and len(p2.encode_frame(tiny)) == floor


#: Finding F3's cases (fit_semantics.py): a long artist and a short title in tight budgets.
GODSPEED = {"type": 3, "seq": 5, "dropId": "area:87", "artist": "Godspeed You! Black Emperor", "title": "Moya",
            "lengthMs": 650000}
OLAFUR = {"type": 3, "seq": 5, "dropId": "zone:1429", "label": "Elwynn Forest", "setId": "my-drops",
          "artist": "The Ólafur Arnalds Ensemble & the Icelandic Symphony Orchestra feat. Arnór Dan", "title": "So Close"}
#: Finding F4's case: a bundled drop with a YouTube id and artist / title, no Spotify id.
YT_ONLY_DROP = {"type": 3, "seq": 6, "flags": 0x20, "dropId": "area:150", "youtube": "7C-VscEQugk",
                "artist": "Otis Redding", "title": "(Sittin' On) The Dock of the Bay", "label": "Menethil Harbor",
                "lengthMs": 163747, "setId": "azeroth-mixtape"}


def test_fit_rule_keeps_the_query_separator_f3():
    """F3: the fitted query is always "artist 0x1F title" with a fair share of each part, or
    absent -- never an artist prefix that the companion would read as the title."""
    f = p2.frame_from_event(GODSPEED, max_bytes=p2.capacity(1))
    assert (f.artist, f.title, f.search_text) == ("Godspeed You! B", "Moya", "Godspeed You! B Moya")
    assert len(p2.encode_frame(f)) == 39
    f = p2.frame_from_event({**GODSPEED, "startMs": 30000}, max_bytes=p2.capacity(1))
    assert (f.artist, f.title) == ("Godspeed Yo", "Moya")
    f = p2.frame_from_event(OLAFUR, max_bytes=p2.capacity(2))
    assert f.title == "So Close" and OLAFUR["artist"].startswith(f.artist) and len(f.artist.encode()) == 57
    # title-only and artist-only queries: the empty part needs no room, the other keeps >= 8 bytes
    title_only = {"type": 3, "seq": 1, "dropId": "zone:9", "title": "Hoppípolla Hoppípolla Hoppípolla", "lengthMs": 1}
    for budget in range(p2.FRAME_OVERHEAD, 60):
        f = p2.frame_from_event(title_only, max_bytes=budget)
        if f.query is not None:
            assert f.query.startswith("\x1f") and f.artist == "" and len(f.title.encode()) >= 7  # "Hoppípo" (í: 2 B)
            assert title_only["title"].startswith(f.title)
    artist_only = {"type": 3, "seq": 1, "dropId": "zone:9", "artist": "Explosions in the Sky", "lengthMs": 1000}
    sizes = {}
    for budget in range(p2.FRAME_OVERHEAD, 60):
        f = p2.frame_from_event(artist_only, max_bytes=budget)
        sizes[budget] = None if f.query is None else f.artist
        assert f.query is None or (f.query.endswith("\x1f") and f.title == "" and len(f.artist) >= 8)
    # 9 + 4 (placeKey) + 4 (length) = 17 bytes without the query; the query needs 2 + 8 + 1 more
    assert sizes[27] is None and sizes[28] == "Explosio" and sizes[29] == "Explosion"
    # every part shorter than the minimum is kept whole or the query goes
    short = {"type": 3, "seq": 1, "dropId": "zone:9", "artist": "Toto", "title": "Africa", "startMs": 900,
             "lengthMs": 1000}
    for budget in range(p2.FRAME_OVERHEAD, 40):
        f = p2.frame_from_event(short, max_bytes=budget)
        assert f.query in (None, "Toto\x1fAfrica")


@pytest.mark.parametrize("prefer,query_kept", [("spotify", True), ("youtube", False)])
def test_fit_rule_drops_the_query_only_for_the_preferred_id_f4(lua, prefer, query_kept):
    """F4: step 3 drops the query only when the preferred provider's id is present. A Spotify
    user (prefer spotify) of a drop with only a YouTube id keeps the query, so the companion can
    still search Spotify; a YouTube user does not need it."""
    assert len(py_frame(YT_ONLY_DROP)) == 121  # fits 3 rows (123 B) whole
    for budget in (p2.capacity(1), 60, p2.capacity(2)):
        f = p2.frame_from_event(YT_ONLY_DROP, max_bytes=budget, prefer=prefer)
        assert f.youtube_id == "7C-VscEQugk" and f.label is None and f.set_id is None
        if budget == p2.capacity(1) and prefer == "spotify":
            assert f.query is None  # 9 + 4 + 13 + 4 = 30 B leave no 8 + 8 bytes for the parts
        else:
            assert (f.query is not None) == query_kept, budget
            if f.query is not None:
                assert f.artist and f.title and "\x1f" in f.query
        assert lua_frame(lua, YT_ONLY_DROP, budget, prefer) == py_frame(YT_ONLY_DROP, budget, prefer)
    # with the preferred id present the query goes at step 3, as before
    both = {**YT_ONLY_DROP, "spotify": SPOTIFY_ID}
    assert p2.frame_from_event(both, max_bytes=p2.capacity(2), prefer=prefer).query is None
    sp_only = {**YT_ONLY_DROP, "youtube": None, "spotify": SPOTIFY_ID}
    f = p2.frame_from_event(sp_only, max_bytes=p2.capacity(2), prefer=prefer)
    assert (f.query is None) == (prefer == "spotify")  # the literal rule: only the preferred id counts


FIT_EVENTS = [FULL_USER_DROP, QUERY_ONLY, EVENTS[9].values[0], EVENTS[0].values[0], EVENTS[4].values[0],
              EVENTS[8].values[0], {**FULL_USER_DROP, "spotify": None}, {**FULL_USER_DROP, "youtube": None},
              GODSPEED, OLAFUR, YT_ONLY_DROP, {**YT_ONLY_DROP, "artist": None}, {**YT_ONLY_DROP, "title": "Ö"},
              {**QUERY_ONLY, "artist": "", "startMs": None}]


def _budget(b) -> int:
    if not isinstance(b, (int, float)) or b != b:
        return p2.MAX_FRAME
    return min(max(math.floor(b), p2.FRAME_OVERHEAD), p2.MAX_FRAME)


@pytest.mark.parametrize("prefer", ["spotify", "youtube", None, "Spotify", 7])
def test_fit_rule_byte_exact_at_every_budget(lua, prefer):
    for ev in FIT_EVENTS:
        for budget in list(range(0, 270)) + [None, 1e9, -1, 39.9, float("nan")]:
            want = py_frame(ev, budget, prefer)
            assert lua_frame(lua, ev, budget, prefer) == want, (ev, budget)
            assert len(want) <= max(_budget(budget), 45)  # 45: placeKey + start + length + Spotify id (was 43)
            f = p2.decode_frame(want)
            assert f.query is None or p2.QUERY_SEP in f.query  # F3: never a fragment without its separator
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
    """PROTOCOL-v2 4.3 "Measured" table: frame bytes, rows at 112 columns and the strip at 3 px,
    with the 4-byte trailer (every frame 2 bytes longer than with Fletcher-16; the 88-byte
    Durotar FIRE now needs 96 columns where the 86-byte one fitted 80)."""
    mixtape = {"type": 3, "seq": 1, "flags": 0x20, "dropId": "zone:1411", "youtube": "d_vPJjqWxZM",
               "artist": "Dolly Parton", "title": "9 to 5", "startMs": 0, "lengthMs": 165547}  # Azeroth Mixtape, Durotar
    table = [
        ("HELLO", {"type": 1, "seq": 1}, 9, 1, (96, 3), 9),
        ("CANCEL zone:1429", {"type": 4, "seq": 2, "dropId": "zone:1429"}, 24, 1, (240, 3), 24),
        ("FIRE, bundled Mixtape drop", mixtape, 62, 2, (288, 6), 62),
        ("... same plus label and setId", {**mixtape, "label": "Durotar", "setId": "azeroth-mixtape"}, 88, 3, (288, 9), 88),
        ("FIRE Spotify + YouTube + query + start + length + volume",
         {**FULL_USER_DROP, "dropId": "area:87", "label": None, "setId": None}, 97, 3, (288, 9), 97),
        ("FIRE user drop with every field", FULL_USER_DROP, 143, 4, (336, 9), 115),
    ]
    for name, ev, size, rows, strip, fitted in table:
        assert len(py_frame(ev)) == size, name
        assert p2.adaptive_layout(size)[0] == rows, name
        grid = p2.encode_event_cells(ev, p2.Geometry.beacon(3))
        assert (grid.frame_len, (grid.width_px, grid.height_px), grid.overflow) == (fitted, strip, False), name
        assert p2.decode_cells_grid(grid.as_rows()) == p2.frame_from_event(ev, max_bytes=123)
    # at 2 px the user drop keeps every field (4 rows of 112 cells), at 4 px it is fitted to 61 B
    two = p2.encode_event_cells(FULL_USER_DROP, p2.Geometry.beacon(2))
    four = p2.encode_event_cells(FULL_USER_DROP, p2.Geometry.beacon(4))
    assert (two.frame_len, two.cols, two.rows, two.width_px, two.height_px) == (143, 112, 4, 224, 8)
    assert (four.frame_len, four.cols, four.rows, four.width_px, four.height_px) == (61, 96, 2, 384, 8)
    # worst case: every field at its cap -- the chat frame is fitted to 224 bytes (6 rows at
    # 112 columns), the 3 px strip to <= 123 B
    worst = EVENTS[9].values[0]
    assert len(py_frame(worst)) == 224 and p2.adaptive_layout(224) == (6, 112)
    for px, cap in ((2, 165), (3, 123), (4, 81)):
        grid = p2.encode_event_cells(worst, p2.Geometry.beacon(px))
        assert grid.frame_len <= cap and grid.rows * px <= p2.MAX_STRIP_PX and not grid.overflow


def test_music_frames_size_table():
    """DESIGN-v1.2.1 2.6 with the 4-byte trailer: every HELLO and every CONFIG that obeys 2.2 (no
    PREFS beside a CLIENT_ID) is one strip row at 2 / 3 / 4 px; the never-emitted bound with all
    three fields is 41 B, two rows -- which is why that rule is mandatory."""
    prefs = {"order": "spotify-youtube", "corner": "topright", "fade": True, "resume": True}
    act = {"name": "test-youtube", "at": 1762380000}
    connect = {"name": "connect-spotify", "at": 4294967295}
    table = [  # (event, bytes, columns, rows)
        ({"type": 1}, 9, 32, 1),
        ({"type": 1, "prefs": prefs}, 15, 48, 1),
        ({"type": 1, "flags": 0x20, "placeKey": 65535}, 13, 48, 1),
        ({"type": 1, "flags": 0x20, "placeKey": 65535, "prefs": prefs}, 19, 64, 1),
        ({"type": 6, "prefs": prefs}, 15, 48, 1),
        ({"type": 6, "prefs": prefs, "action": act}, 23, 80, 1),
        ({"type": 6, "action": connect, "clientId": CLIENT_ID}, 35, 112, 1),
        ({"type": 6, "prefs": prefs, "action": connect, "clientId": CLIENT_ID}, 41, 64, 2),  # the bound
    ]
    for ev, size, cols, rows in table:
        assert len(py_frame(ev)) == size, ev
        assert p2.adaptive_layout(size) == (rows, cols), ev
        for px in (2, 3, 4):
            grid = p2.encode_event_cells(ev, p2.Geometry.beacon(px))
            assert (grid.frame_len, grid.cols, grid.rows) == (size, cols, rows)
            assert grid.width_px <= beacon_capture_width() and p2.decode_cells_grid(grid.as_rows()) == p2.frame_from_event(ev)
            if rows == 1:
                assert size <= p2.capacity(1) and grid.height_px == px


def beacon_capture_width() -> int:
    from needledropper.transport import beacon2
    return beacon2.CAPTURE_WIDTH


def test_every_music_services_frame_is_one_row(lua):
    """DESIGN-v1.2.1 5.2 item 3: every frame MusicServices can build -- the five actions, with or
    without a client id (connect / disconnect only, never beside PREFS), with or without PREFS,
    and every flag combination the addon sets -- encodes identically in both languages, is at
    most Capacity(1) = 39 bytes, and is one row at 2, 3 and 4 px (the widest is 448 px)."""
    prefs_set = [None, {"order": "youtube-spotify", "corner": "bottomleft", "fade": False, "resume": True}]
    events = []
    for name, cid, prefs, flags, at in itertools.product(p2.ACTIONS + (None,), (None, CLIENT_ID), prefs_set,
                                                         (0, 0x01, 0x22, 0x3F), (0, 1762380123, 4294967295)):
        if cid and (prefs or name not in ("connect-spotify", "disconnect-spotify")):
            continue  # DESIGN-v1.2.1 2.2: CLIENT_ID only with connect / disconnect, and never with PREFS
        events.append({"type": 6, "seq": 200, "flags": flags, "prefs": prefs,
                       "action": {"name": name, "at": at} if name else None, "clientId": cid})
    for prefs, flags, pk in itertools.product(prefs_set, (0, 0x20, 0x22, 0x3F), (None, 0, 65535)):
        events.append({"type": 1, "seq": 255, "flags": flags, "placeKey": pk, "prefs": prefs})  # steady HELLOs
    for ev in events:
        b = py_frame(ev)
        assert lua_frame(lua, ev) == b and len(b) <= p2.capacity(1), ev
        assert p2.adaptive_layout(len(b))[0] == 1
        for px in (2, 3, 4):
            grid = p2.encode_event_cells(ev, p2.Geometry.beacon(px))
            assert grid.rows == 1 and grid.width_px <= p2.MAX_COLS * 4 and not grid.overflow
    assert len(events) > 100


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
    """placeKey + start + length + a Spotify id are 45 bytes (43 with the old 2-byte trailer):
    more than one row holds (39)."""
    ev = {**FULL_USER_DROP, "prefer": "spotify"}
    grid = p2.encode_event_cells(ev, p2.Geometry.beacon(2, 1))
    assert (grid.frame_len, grid.rows, grid.overflow) == (45, 2, True) and p2.capacity(1) == 39
    f = p2.decode_cells_grid(grid.as_rows())
    assert (f.spotify_id, f.youtube_id, f.start_ds, f.length_ds) == (SPOTIFY_ID, None, 120, 950)
    lg = lua.P.EncodeCells(lua_event(lua, ev), lua.rt.table_from({"cellPx": 2, "maxRows": 1}))
    assert lg.overflow is True and lg.rows == 2 and lua_grid_cells(lg) == list(grid.cells)
    yt = p2.encode_event_cells({**ev, "prefer": "youtube"}, p2.Geometry.beacon(2, 1))
    assert (yt.frame_len, yt.rows, yt.overflow) == (34, 1, False)  # was 32 (2-byte trailer)


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


def _vectors() -> list[dict]:
    return json.loads(VECTORS_FILE.read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vec", _vectors(), ids=lambda v: v["name"])
def test_documented_vectors(lua, vec):
    """tests/vectors/protocol2/frames.json = docs/PROTOCOL-v2.md section 7 and DESIGN-v1.2.1 2.13:
    both encoders write exactly these bytes and chat lines, and both decoders read them back to
    the listed fields."""
    ev, budget = vec["event"], vec.get("maxBytes")
    want = bytes.fromhex(vec["hex"])
    assert len(want) == vec["bytes"] and p2.adaptive_layout(len(want)) == tuple(reversed(vec["cells"]))
    assert py_frame(ev, budget) == lua_frame(lua, ev, budget) == want
    if budget is None:
        assert p2.format_chat_line(want) == lua.P.FormatChatLine(lua_event(lua, ev)) == vec["chat"]
    assert p2.format_chat_line(want) == vec["chat"] and p2.parse_chat_line(vec["chat"]) == p2.decode_frame(want)
    assert py_decoded(p2.decode_frame(want)) == vec["decoded"]
    assert lua_decoded(lua.P.DecodeFrame(ltable(lua, want))) == vec["decoded"]


def test_documented_vectors_file_is_current():
    """The vectors cover the plain HELLO, a CANCEL, an ARM, HELLO with PREFS and with the playing
    drop's placeKey (D3), every CONFIG shape of DESIGN-v1.2.1 2.13 and the F3 one-row fit; the
    check parameters match the code; the HELLO strip still starts m g m g g k k w k r m."""
    doc = json.loads(VECTORS_FILE.read_text(encoding="utf-8"))
    # RP-1: the file records the two final rounds of the check too
    assert doc["check"] == {"seed": p2.CHECK_SEED, "mul": p2.CHECK_MUL, "mod": p2.CHECK_MOD, "finalRounds": 2,
                            "trailerBytes": 4}
    names = [v["name"] for v in doc["vectors"]]
    assert {"hello", "cancel", "arm", "hello-playing", "hello-prefs", "config-prefs", "config-prefs-action",
            "config-connect", "config-bound", "fire-fitted-39"} <= set(names) and len(names) == len(set(names))
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
            ("PLACE_KEY", "DROP_ID", "SPOTIFY", "YOUTUBE", "QUERY", "START_DS", "LENGTH_DS", "VOLUME", "LABEL", "SET_ID",
             "PREFS", "ACTION", "CLIENT_ID")}
    assert {name: P.TAG[name] for name in tags} == tags and (p2.TAG_PREFS, p2.TAG_ACTION, p2.TAG_CLIENT_ID) == (11, 12, 13)
    assert (P.MAX_DROP_ID, P.MAX_QUERY, P.MAX_LABEL, P.MAX_SET_ID, P.MAX_BODY, P.MAX_FRAME) == (
        p2.MAX_DROP_ID, p2.MAX_QUERY, p2.MAX_LABEL, p2.MAX_SET_ID, p2.MAX_BODY, p2.MAX_FRAME)
    assert (P.FRAME_OVERHEAD, P.TRAILER_LEN, P.MIN_QUERY_PART, P.CLIENT_ID_LEN) == (
        p2.FRAME_OVERHEAD, p2.TRAILER_LEN, p2.MIN_QUERY_PART, p2.CLIENT_ID_LEN) == (9, 4, 8, 16)
    assert p2.MAX_FRAME == 264
    assert (P.CHECK_SEED, P.CHECK_MUL, P.CHECK_MOD, P.CHECK_FINAL_ROUNDS) == (
        p2.CHECK_SEED, p2.CHECK_MUL, p2.CHECK_MOD, p2.CHECK_FINAL_ROUNDS)  # RP-1: the final rounds too
    assert (p2.CHECK_SEED, p2.CHECK_MUL, p2.CHECK_MOD) == (0x4E443243, 48271, 2 ** 31 - 1)
    assert (P.LEAD_CELLS, P.MIN_COLS, P.MAX_COLS, P.COL_STEP, P.MAX_ROWS) == (
        p2.LEAD_CELLS, p2.MIN_COLS, p2.MAX_COLS, p2.COL_STEP, p2.MAX_ROWS)
    assert (P.WHITEN_SEED, P.WHITEN_MUL, P.WHITEN_MOD) == (p2.WHITEN_SEED, p2.WHITEN_MUL, p2.WHITEN_MOD)
    assert P.QUERY_SEP == p2.QUERY_SEP
    for lua_names, py_names in ((P.PREFS_ORDERS, p2.PREFS_ORDERS), (P.PREFS_CORNERS, p2.PREFS_CORNERS),
                                (P.ACTIONS, p2.ACTIONS)):
        assert tuple(lua_names[i] for i in range(1, len(lua_names) + 1)) == py_names
    assert p2.PREFS_ORDERS == ("spotify-youtube", "youtube-spotify", "spotify", "youtube", "none")
    assert p2.PREFS_CORNERS == ("topright", "topleft", "bottomright", "bottomleft")
    assert p2.ACTIONS == ("connect-spotify", "disconnect-spotify", "test-spotify", "test-youtube", "youtube-sign-in")
    ks = P.Keystream(3000)
    assert bytes(int(ks[i]) for i in range(1, 3001)) == p2.keystream_bits(3000)
    assert sum(p2.keystream_bits(3000)) in range(1350, 1650)  # balanced
    for ev, ename in zip(range(7), ["NONE", "HELLO", "ARM", "FIRE", "CANCEL", "PING", "CONFIG"]):
        assert P.EV[ename] == ev == int(EventType[ename]) and P.EV_NAME[ev] == ename
    assert len(EventType) == 7
    for name, f in P.FLAG.items():
        assert int(Flags[name]) == f
    # TYPE_TAGS: the same field set per type in both languages
    for t in range(7):
        lua_tags = {int(k) for k, v in lua.P.TYPE_TAGS[t].items() if v}
        assert lua_tags == set(p2.TYPE_TAGS[EventType(t)]), t
    assert p2.TYPE_TAGS[EventType.HELLO] == {p2.TAG_PLACE_KEY, p2.TAG_PREFS, p2.TAG_MUSIC_VOLUME}
    assert p2.TYPE_TAGS[EventType.CONFIG] == {p2.TAG_PREFS, p2.TAG_ACTION, p2.TAG_CLIENT_ID, p2.TAG_MUSIC_VOLUME}
    assert p2.TAG_PREFS not in p2.TYPE_TAGS[EventType.FIRE] | p2.TYPE_TAGS[EventType.ARM]


def test_frame_check_matches_and_is_exact_in_lua_doubles(lua):
    """The 4-byte trailer (finding F1): the same value in both languages on random data up to
    the longest frame, with the products near 2^47 that Lua 5.1 must compute exactly."""
    rng = random.Random(41)
    for n in (0, 1, 2, 5, 9, 39, 123, 255, 263, 264, 1000):
        for _ in range(3):
            data = bytes(rng.randrange(256) for _ in range(n))
            assert lua.P.FrameCheck(ltable(lua, data)) == p2.frame_check(data) < p2.CHECK_MOD
            if n:
                assert lua.P.FrameCheck(ltable(lua, data), n - 1) == p2.frame_check(data[:-1])
    # RP-1: two final rounds h = h * 48271 + 1 after the bytes (as if two 0x00 bytes followed)
    fin = lambda h: ((h * 48271 + 1) % (2 ** 31 - 1) * 48271 + 1) % (2 ** 31 - 1)  # noqa: E731
    assert p2.frame_check(b"") == fin(p2.CHECK_SEED)
    assert p2.frame_check(b"\x00") == fin((p2.CHECK_SEED * 48271 + 1) % (2 ** 31 - 1))
    assert (2 ** 31 - 2) * 48271 + 256 < 2 ** 47  # every intermediate is an exact double
    # the cases that defeat Fletcher-16 do not defeat the check
    assert protocol.fletcher16(b"\x4e\x00") == protocol.fletcher16(b"\x4e\xff")
    assert p2.frame_check(b"\x4e\x00") != p2.frame_check(b"\x4e\xff")
    base = bytearray(rng.randrange(256) for _ in range(126))
    for k in (42, 36, 30, 24, 18, 12, 6):  # one column of a 112 / 96 / 80 / ... cell strip: bytes i, i+k, i+2k
        for i in range(0, 126 - 2 * k):
            for d in (1, 32, 64, 85):
                bad = bytearray(base)
                if bad[i] < d or bad[i + 2 * k] < d or bad[i + k] + 2 * d > 255:
                    continue
                bad[i] -= d
                bad[i + k] += 2 * d
                bad[i + 2 * k] -= d
                assert protocol.fletcher16(bytes(bad)) == protocol.fletcher16(bytes(base))  # Fletcher's blind spot
                assert p2.frame_check(bytes(bad)) != p2.frame_check(bytes(base))


def test_fletcher_and_utf8_truncate(lua):
    rng = random.Random(1)
    for n in (0, 1, 2, 7, 255, 262, 1000):
        data = bytes(rng.randrange(256) for _ in range(n))
        assert lua.P.Fletcher16Bytes(ltable(lua, data)) == protocol.fletcher16(data)
        assert lua.P.Fletcher16String(data) == protocol.fletcher16(data)
    # Fletcher-16 now only makes placeKey (an identity, not an integrity check); mod 255 it
    # cannot tell 0x00 from 0xFF, one reason the trailer is FrameCheck (and the beacon whitens)
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
    return head_and_body + p2.frame_check(head_and_body).to_bytes(4, "big")


GOOD = p2.encode_frame(p2.frame_from_event(EVENTS[0].values[0]))


def _with_body(body: bytes, *, t: int = 3) -> bytes:
    return _reframe(bytes((0x4E, 0x20 | t, 1, 0, len(body))) + body)


def _fletcher_framed(head_and_body: bytes) -> bytes:
    """A frame with the pre-1.2.1 Fletcher-16 trailer (2 bytes)."""
    return head_and_body + bytes(divmod(protocol.fletcher16(head_and_body), 256))


@pytest.mark.parametrize("name,data", [
    ("short", GOOD[:8]),
    ("bad-magic", _reframe(b"\x4f" + GOOD[1:-4])),
    ("v1-version", _reframe(bytes((0x4E, 0x13)) + GOOD[2:-4])),
    ("type-7", _with_body(b"", t=7)),
    ("type-15", _with_body(b"", t=15)),
    ("length-mismatch", GOOD + b"\x00"),
    ("checksum", GOOD[:-1] + bytes(((GOOD[-1] + 1) % 256,))),
    ("checksum-top-byte", GOOD[:-4] + bytes(((GOOD[-4] + 0x80) % 256,)) + GOOD[-3:]),
    ("old-fletcher-trailer", _fletcher_framed(GOOD[:-4])),
    ("old-fletcher-trailer-padded", _fletcher_framed(GOOD[:-4]) + b"\x00\x00"),
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
    # D5: query and label must be strict UTF-8
    ("query-lone-continuation", _with_body(b"\x05\x05ab\xb4cd")),
    ("query-overlong", _with_body(b"\x05\x04a\xc0\xafb")),
    ("query-surrogate", _with_body(b"\x05\x05\xed\xa0\x80\x1fx")),
    ("query-above-10ffff", _with_body(b"\x05\x05\xf4\x90\x80\x80x")),
    ("query-truncated-at-end", _with_body(b"\x05\x05a\x1fb\xe6\x88")),
    ("query-bad-lead", _with_body(b"\x05\x03\xffab")),
    ("label-lone-continuation", _with_body(b"\x09\x03a\xb4b")),
    ("label-overlong", _with_body(b"\x09\x02\xc0\xaf")),
    ("label-surrogate", _with_body(b"\x09\x03\xed\xa0\x80")),
    ("label-truncated-at-end", _with_body(b"\x09\x03ab\xc3")),
    ("label-truncated-4-byte", _with_body(b"\x09\x04x\xf0\x9f\x8e")),
    # DESIGN-v1.2.1 2.5: PREFS 4 bytes, ACTION 6 with its check byte, CLIENT_ID 16
    ("prefs-3", _with_body(b"\x0b\x03\x01\x01\x01", t=6)),
    ("prefs-5", _with_body(b"\x0b\x05\x01\x01\x01\x01\x01", t=1)),
    ("action-5", _with_body(b"\x0c\x05\x01\xfe\x00\x00\x00", t=6)),
    ("action-7", _with_body(b"\x0c\x07\x01\xfe\x00\x00\x00\x00\x00", t=6)),
    ("action-check-off-by-one", _with_body(b"\x0c\x06\x01\xfd\x69\x0b\xc9\x5b", t=6)),
    ("action-check-not-xor", _with_body(b"\x0c\x06\x05\x05\x69\x0b\xc9\x5b", t=6)),
    ("client-id-15", _with_body(b"\x0d\x0f" + bytes(15), t=6)),
    ("client-id-17", _with_body(b"\x0d\x11" + bytes(17), t=6)),
    ("client-id-empty", _with_body(b"\x0d\x00", t=6)),
    ("prefs-after-action", _with_body(b"\x0c\x06\x01\xfe\x00\x00\x00\x01\x0b\x04\x01\x01\x01\x01", t=6)),
])
def test_decoders_reject(lua, name, data):
    assert p2.decode_frame(data) is None
    assert lua.P.DecodeFrame(ltable(lua, data)) is None


def test_decoders_accept_unknown_codes_and_any_type_for_the_new_fields(lua):
    """DESIGN-v1.2.1 2.5: unknown PREFS / ACTION codes are not errors (the field decodes as
    absent, the action without a name), and decoders do not enforce TYPE_TAGS."""
    data = _with_body(b"\x0b\x04\x09\x00\x03\x02" + b"\x0c\x06\x4d\xb2\x00\x00\x00\x07", t=6)
    f = p2.decode_frame(data)
    assert f.prefs == p2.Prefs(None, None, None, False) and f.action == p2.Action(77, 7) and f.action.name is None
    d = lua.P.DecodeFrame(ltable(lua, data))
    assert lua_decoded(d) == py_decoded(f) == {"type": 6, "seq": 1, "flags": 0, "prefs": {"resume": False},
                                                "action": {"code": 77, "at": 7}}
    data = _with_body(b"\x0b\x04\x00\x00\x00\x00", t=6)  # never written (all four not set), still valid
    assert p2.decode_frame(data).prefs == p2.Prefs() and lua.P.DecodeFrame(ltable(lua, data)).prefs.order is None
    # a FIRE with PREFS, a HELLO with an ACTION, a PING with a client id: decoded, the app ignores them
    for t, body in ((3, b"\x0b\x04\x01\x01\x01\x01"), (1, b"\x0c\x06\x03\xfc\x00\x00\x00\x01"), (5, b"\x0d\x10" + bytes(16))):
        data = _with_body(body, t=t)
        assert p2.decode_frame(data) is not None and lua.P.DecodeFrame(ltable(lua, data)) is not None, t
    f = p2.decode_frame(_with_body(b"\x0d\x10" + bytes(range(0xf0, 0x100)), t=6))
    assert f.client_id == "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"
    # strict UTF-8 accepts every valid sequence, including 4-byte ones and U+10FFFF
    ok = _with_body(b"\x05\x0c\xf0\x9f\x8e\xb5\x1f\xf4\x8f\xbf\xbf\xc3\xa9x" + b"\x09\x03\xe6\x88\x91")
    f = p2.decode_frame(ok)
    assert f.query == "\U0001F3B5\x1f\U0010FFFFéx" and f.label == "我" and lua.P.DecodeFrame(ltable(lua, ok)) is not None


def test_utf8_valid_matches_python_strict_codec(lua):
    rng = random.Random(17)
    cases = [b"", b"plain", "Sigur Rós".encode(), b"\xb4", b"\xc0\xaf", b"\xc1\xbf", b"\xe0\x80\x80", b"\xe0\xa0\x80",
             b"\xed\x9f\xbf", b"\xed\xa0\x80", b"\xf0\x8f\xbf\xbf", b"\xf0\x90\x80\x80", b"\xf4\x8f\xbf\xbf",
             b"\xf4\x90\x80\x80", b"\xf5\x80\x80\x80", b"\xc3", b"x\xe2\x82", b"\x00\x1f\x7f", "🎵".encode() + b"\x80"]
    cases += [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 12))) for _ in range(2000)]
    cases += [bytes(rng.choice([0x41, 0x80, 0xbf, 0xc3, 0xe2, 0xed, 0xf0, 0xf4]) for _ in range(rng.randrange(1, 8)))
              for _ in range(2000)]
    for data in cases:
        try:
            data.decode("utf-8")
            want = True
        except UnicodeDecodeError:
            want = False
        assert lua.P.Utf8Valid(data) is want, data
    assert lua.P.Utf8Valid(None) is False and lua.P.Utf8Valid(5) is False


def test_unknown_tags_are_skipped_forward_compatible(lua):
    # was 0x0B (3 bytes): 0x0B-0x0E are known now, so the unknown tag is 0x0F
    body = b"\x02\x09zone:1429" + b"\x0f\x03abc" + b"\x7f\x00" + b"\xfe\x02\x01\x02"
    data = _with_body(body)
    f = p2.decode_frame(data)
    assert f is not None and f.drop_id == "zone:1429"
    assert f.extra == (p2.TLV(0x0F, b"abc"), p2.TLV(0x7F, b""), p2.TLV(0xFE, b"\x01\x02"))
    assert p2.encode_frame(f) == data  # extras round-trip in tag order
    d = lua.P.DecodeFrame(ltable(lua, data))
    assert d.dropId == "zone:1429" and len(d.unknown) == 3 and d.unknown[1].tag == 0x0F and d.unknown[1].value == "abc"
    # a decoder of this version still accepts every known tag in a frame from a newer encoder
    for tag in (0x05, 0x0B, 0x0C, 0x0D, 0x0E):
        with pytest.raises(ValueError):
            p2.encode_frame(dataclasses.replace(f, extra=(p2.TLV(tag, b"x"),)))  # known tag as "extra"


def test_master_volume_extension_all_percentages_and_legacy_frames(lua):
    for typ in (1, 6):
        for volume in range(101):
            for prefs in ({"volume": volume}, {"order": "youtube", "fade": False, "volume": volume}):
                ev = {"type": typ, "seq": 17, "prefs": prefs}
                raw = py_frame(ev)
                assert lua_frame(lua, ev) == raw
                assert p2.decode_frame(raw).prefs.volume == volume
                assert lua_decoded(lua.P.DecodeFrame(ltable(lua, raw))) == py_decoded(p2.decode_frame(raw))
    # Old four-byte PREFS remains byte-exact; missing volume never means reset to 100.
    raw = _with_body(b"\x0b\x04\x01\x01\x01\x01", t=6)
    assert p2.decode_frame(raw).prefs.volume is None
    assert p2.encode_frame(p2.decode_frame(raw)) == raw
    assert lua.P.DecodeFrame(ltable(lua, raw)).prefs.volume is None
    assert p2.decode_frame(py_frame({"type": 1})).prefs is None
    for typ in (0, 2, 3, 4, 5):
        ev = {"type": typ, "prefs": {"volume": 0}}
        assert lua_frame(lua, ev) == py_frame(ev)
        assert p2.decode_frame(py_frame(ev)).prefs is None


def test_master_volume_rejects_invalid_lengths_and_values(lua):
    for length in range(256):
        if length == 1:
            continue
        raw = _with_body(bytes((0x0E, length)) + (bytes(length) if length <= 253 else b""), t=6)
        assert p2.decode_frame(raw) is None
        assert lua.P.DecodeFrame(ltable(lua, raw)) is None
    for volume in range(101, 256):
        raw = _with_body(bytes((0x0E, 1, volume)), t=1)
        assert p2.decode_frame(raw) is None
        assert lua.P.DecodeFrame(ltable(lua, raw)) is None
    for volume in (-1, 101, True, 1.5, "50"):
        with pytest.raises(ValueError):
            p2.encode_frame(p2.Frame(type=EventType.CONFIG, prefs=p2.Prefs(volume=volume)))


def test_master_volume_normalization_and_beacon_capacity(lua):
    for volume, expected in ((-3, 0), (101, 100), (49.5, 50), (0, 0), (False, None), ("50", None),
                             (float("nan"), None), (float("inf"), None)):
        ev = {"type": 6, "prefs": {"volume": volume}}
        raw = py_frame(ev)
        assert lua_frame(lua, ev) == raw
        decoded = p2.decode_frame(raw)
        assert (decoded.prefs.volume if decoded.prefs else None) == expected
    prefs = {"order": "spotify-youtube", "corner": "topright", "fade": True, "resume": True, "volume": 50}
    events = [({"type": 1, "placeKey": 42, "prefs": prefs}, 22),
              ({"type": 6, "prefs": prefs}, 18),
              ({"type": 6, "prefs": prefs, "action": {"name": "test-spotify", "at": 7}}, 26),
              ({"type": 6, "prefs": {"volume": 50}, "clientId": CLIENT_ID,
                "action": {"name": "connect-spotify", "at": 7}}, 38)]
    for ev, length in events:
        for pixels in (2, 3, 4):
            grid = p2.encode_event_cells(ev, p2.Geometry.beacon(pixels))
            assert grid.frame_len == length and grid.rows == 1
            assert lua_frame(lua, ev, p2.capacity(1)) == py_frame(ev, p2.capacity(1))
            assert len(py_frame(ev, p2.capacity(1))) <= p2.capacity(1)


def test_encode_frame_is_strict():
    base = p2.Frame(EventType.FIRE, seq=1)
    for bad in [dict(seq=256), dict(place_key=-1), dict(drop_id="a b"), dict(drop_id="x" * 41), dict(spotify_id="x"),
                dict(youtube_id="abc"), dict(query=""), dict(query="q" * 121), dict(start_ds=70000), dict(volume=101),
                dict(label="l" * 49), dict(set_id="s" * 33), dict(extra=(p2.TLV(0x20, b"x" * 256),)),
                dict(query="\ud800x"), dict(label="\udfff"),
                dict(prefs=p2.Prefs(order="spotify-only")), dict(prefs=p2.Prefs(corner="TOPRIGHT")),
                dict(prefs=p2.Prefs(fade=1)), dict(prefs={"order": "none"}),
                dict(action=p2.Action(256, 0)), dict(action=p2.Action(-1, 0)), dict(action=p2.Action(1, -1)),
                dict(action=p2.Action(1, 2 ** 32)), dict(action=p2.Action(True, 0)), dict(action=("connect", 1)),
                dict(client_id="0123"), dict(client_id="g" * 32), dict(client_id=CLIENT_ID + "0"),
                dict(client_id=" " + CLIENT_ID[1:]), dict(extra=(p2.TLV(0x0D, b"x"),))]:
        with pytest.raises(ValueError):
            p2.encode_frame(dataclasses.replace(base, **bad))
    with pytest.raises(ValueError):  # body over 255 bytes
        p2.encode_frame(dataclasses.replace(base, extra=(p2.TLV(0x20, b"x" * 200), p2.TLV(0x21, b"y" * 60))))
    with pytest.raises(ValueError):
        p2.Action.named("sign-in", 1)
    # the strict encoder still writes what decoders accept: unknown action codes, empty PREFS
    for frame in (p2.Frame(EventType.CONFIG, action=p2.Action(77, 7)), p2.Frame(EventType.CONFIG, prefs=p2.Prefs()),
                  p2.Frame(EventType.CONFIG, client_id=CLIENT_ID.upper())):
        b = p2.encode_frame(frame)
        assert p2.decode_frame(b) is not None and p2.encode_frame(p2.decode_frame(b)) == b


def test_config_field_normalisation(lua):
    """DESIGN-v1.2.1 2.4: names match ASCII case-insensitively, fade / resume must be booleans,
    nothing set means no TLV; an action needs one of the five names and a number (floored,
    clamped to uint32); a client id loses its ASCII whitespace and must be 32 hex digits."""
    cases = [  # (event fields, expected Frame fields)
        ({"prefs": {"order": "YOUTUBE-spotify", "corner": "TopLeft", "fade": True, "resume": False}},
         {"prefs": p2.Prefs("youtube-spotify", "topleft", True, False)}),
        ({"prefs": {"order": "youtube spotify", "corner": "top-left", "fade": 1, "resume": "true"}}, {}),
        ({"prefs": {"fade": False}}, {"prefs": p2.Prefs(fade=False)}),
        ({"prefs": {}}, {}), ({"prefs": "spotify"}, {}), ({"prefs": p2.Prefs("none")}, {"prefs": p2.Prefs("none")}),
        ({"prefs": {"order": "spotıfy"}}, {}),  # dotless i: not ASCII case folding
        ({"action": {"name": "Connect-Spotify", "at": 1762380123.99}}, {"action": p2.Action(1, 1762380123)}),
        ({"action": {"name": "youtube-sign-in", "at": -3}}, {"action": p2.Action(5, 0)}),
        ({"action": {"name": "test-spotify", "at": 1e300}}, {"action": p2.Action(3, 4294967295)}),
        ({"action": {"name": "test-spotify", "at": float("nan")}}, {}),
        ({"action": {"name": "test-spotify", "at": True}}, {}), ({"action": {"name": "test-spotify"}}, {}),
        ({"action": {"name": "sign-in", "at": 1}}, {}), ({"action": {"at": 1}}, {}),
        ({"action": p2.Action(2, 99)}, {"action": p2.Action(2, 99)}), ({"action": p2.Action(77, 99)}, {}),
        ({"clientId": " 01234567 89ABCDEF\t0123456789abcdef\r\n"}, {"client_id": CLIENT_ID}),
        ({"clientId": CLIENT_ID + " "}, {}), ({"clientId": CLIENT_ID[:31]}, {}), ({"clientId": 12}, {}),
    ]
    for fields, want in cases:
        ev = {"type": 6, "seq": 3, **fields}
        f = p2.frame_from_event(ev)
        assert (f.prefs, f.action, f.client_id) == (want.get("prefs"), want.get("action"), want.get("client_id")), fields
        if not any(isinstance(v, (p2.Prefs, p2.Action)) for v in fields.values()):
            assert lua_frame(lua, ev) == p2.encode_frame(f), fields
    # build_frame takes the same values by keyword
    f = p2.build_frame(EventType.CONFIG, seq=4, prefs={"order": "spotify"}, action={"name": "test-spotify", "at": 9},
                       client_id=CLIENT_ID.upper())
    assert (f.prefs, f.action, f.client_id) == (p2.Prefs("spotify"), p2.Action(3, 9), CLIENT_ID)
    assert f.describe() == "CONFIG seq=4 flags=0x00 prefs=spotify/-/-/- action=test-spotify@9 client=...cdef"
    # per type: song frames never carry PREFS / ACTION / CLIENT_ID, HELLO carries PREFS and placeKey only
    fire = {"type": 3, "seq": 1, "dropId": "zone:9", "youtube": YT_ID, "prefs": {"order": "none"},
            "action": {"name": "test-spotify", "at": 1}, "clientId": CLIENT_ID}
    for ev in (fire, {**fire, "type": 2}, {**fire, "type": 4}, {**fire, "type": 5}, {**fire, "type": 0}):
        f = p2.frame_from_event(ev)
        assert (f.prefs, f.action, f.client_id) == (None, None, None), ev["type"]
        assert lua_frame(lua, ev) == p2.encode_frame(f)
    hello = p2.frame_from_event({**fire, "type": 1})
    assert (hello.prefs, hello.action, hello.client_id, hello.youtube_id, hello.drop_id) == (
        p2.Prefs("none"), None, None, None, None) and hello.place_key == p2.place_key_for("zone:9")
    config = p2.frame_from_event({**fire, "type": 6})
    assert (config.place_key, config.drop_id, config.youtube_id) == (None, None, None)
    assert config.prefs == p2.Prefs("none") and config.action == p2.Action(3, 1) and config.client_id == CLIENT_ID


def test_hello_carries_the_playing_drops_place_key(lua):
    """D3: the steady HELLO with DROP_ACTIVE carries the playing drop's placeKey (from placeKey
    or dropId) and stays one row with PREFS; its dedupe key includes that placeKey."""
    pk = p2.place_key_for("zone:1411")
    for ev in ({"type": 1, "seq": 9, "flags": 0x20, "placeKey": pk}, {"type": 1, "seq": 9, "flags": 0x20, "dropId": "zone:1411"}):
        f = p2.frame_from_event(ev)
        assert f.place_key == pk and f.drop_id is None and f.key == (9, 1, pk, 0x20)
        b = p2.encode_frame(f)
        assert b == lua_frame(lua, ev) and len(b) == 13 and p2.adaptive_layout(13) == (1, 48)
        d = lua.P.DecodeFrame(ltable(lua, b))
        assert (d.type, d.placeKey, d.flags) == (1, pk, 0x20)
    plain = p2.frame_from_event({"type": 1, "seq": 9, "flags": 0x20})
    assert plain.place_key is None and plain.key == (9, 1, 0, 0x20)


def test_decode_frame_never_raises_on_garbage():
    rng = random.Random(11)
    for _ in range(3000):
        n = rng.randrange(0, 40)
        data = bytes(rng.randrange(256) for _ in range(n))
        if rng.random() < 0.5 and n >= 5:  # plausible heads, so the TLV walker really runs
            data = bytes((0x4E, 0x20 | rng.randrange(7), 0, 0, max(0, n - 9))) + data[5:]
            data = _reframe(data[:-4]) if n >= 9 else data
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


#: Finding F1's decisive cases: Azeroth Mixtape drops whose 3 px FIRE strips (3 rows) decoded
#: to a different, Fletcher-valid frame under a one-cell-wide column cover.
MENETHIL = {"type": 3, "seq": 0, "flags": 0x20, "dropId": "area:150", "youtube": "7C-VscEQugk",
            "artist": "Otis Redding", "title": "(Sittin' On) The Dock of the Bay", "label": "Menethil Harbor",
            "lengthMs": 163747, "setId": "azeroth-mixtape"}
ROCKET_MAN = {"type": 3, "seq": 0, "flags": 0x20, "dropId": "area:439", "youtube": "DtVBCG6ThDk",  # before its
              "artist": "Elton John", "title": "Rocket Man (I Think It's Going to Be a Long, Long Time)",  # Spotify id
              "label": "The Shimmering Flats", "lengthMs": 281613, "setId": "azeroth-mixtape"}
DUROTAR_FULL = {"type": 3, "seq": 1, "flags": 0x20, "dropId": "zone:1411", "youtube": "d_vPJjqWxZM",
                "artist": "Dolly Parton", "title": "9 to 5", "lengthMs": 165547, "label": "Durotar",
                "setId": "azeroth-mixtape"}


def _column_cover_wrong_frames(ev: dict, px: int, cols_override=None) -> tuple[int, int]:
    """Every one-cell-wide column cover (all rows, every uniform colour) and every two-column
    one over the strip of ``ev`` at ``px``: (covers tried, covers that decode to a different
    frame)."""
    g = p2.Geometry.beacon(px)
    frame = p2.frame_from_event(ev, max_bytes=p2.capacity(g.max_rows))
    grid = p2.encode_cells(frame, g if cols_override is None else p2.Geometry(cols_override, px))
    raw = p2.encode_frame(frame)
    codes = grid.codes()
    tried = wrong = 0
    for width in (1, 2):
        for c0 in range(p2.LEAD_CELLS, grid.cols - width + 1):
            for colour in range(8):
                bad = list(codes)
                for r in range(grid.rows):
                    for c in range(c0, c0 + width):
                        bad[r * grid.cols + c] = colour
                if bad == codes:
                    continue
                tried += 1
                got = p2.decode_cell_codes(bad, grid.cols, grid.rows)
                if got is not None and got.raw != raw:
                    wrong += 1
    return tried, wrong


@pytest.mark.parametrize("ev,px,cols,shape", [
    (MENETHIL, 3, None, (3, 112)),       # repro_column_cover.py: column 87 black changed bytes 30 / 72 / 114
    (ROCKET_MAN, 3, None, (3, 112)),     # e2e_vertical.py: column 30 changed placeKey 39029 -> 39028
    (DUROTAR_FULL, 3, None, (3, 96)),    # 96-cell rows are 36 bytes: the same blind spot at k = 36
    (MENETHIL, 3, 96, (4, 96)),          # a fixed-width 96-cell strip, four rows
    (MENETHIL, 2, None, (3, 112)),
    (MENETHIL, 4, None, (2, 64)),
], ids=["area150-112x3", "area439-112x3", "durotar-96x3", "area150-96-fixed", "area150-2px", "area150-4px"])
def test_column_covers_never_decode_wrong_f1(ev, px, cols, shape):
    """F1: with Fletcher-16 a cover one column wide and as tall as the strip changed bytes i,
    i+k, i+2k by (-d, +2d, -d) -- invisible to it -- and decoded to another, playable frame. The
    4-byte check sees that pattern for every k and d, so every such cover is rejected."""
    g = p2.Geometry.beacon(px)
    frame = p2.frame_from_event(ev, max_bytes=p2.capacity(g.max_rows))
    grid = p2.encode_cells(frame, g if cols is None else p2.Geometry(cols, px))
    assert (grid.rows, grid.cols) == shape
    tried, wrong = _column_cover_wrong_frames(ev, px, cols)
    assert tried > 300 and wrong == 0
    # the original repro: column 87 painted black in every row of the area:150 strip
    if ev is MENETHIL and px == 3 and cols is None:
        codes = grid.codes()
        for r in range(grid.rows):
            codes[r * grid.cols + 87] = 0
        assert p2.decode_cell_codes(codes, grid.cols, grid.rows) is None


def test_no_one_column_change_can_pass_the_check_f1():
    """F1, for every frame at once: a change confined to one cell column of a strip of 2 to 4 rows
    (any of the column's 3 bits per row flipped either way, i.e. any cover colour over any
    content) never leaves the frame check of the covered bytes unchanged, at every strip width
    and bit offset. (Where the column also crosses the trailer, a pass needs the changed trailer
    to equal the new check by chance, about 2^-31; the cover sweeps above and the a3adv ones
    include those columns.)

    A strip row of ``cols`` cells is ``L = 3 * cols / 8`` bytes, so one column touches the same
    bit offsets in every row, L bytes apart, and a bit at stream position q adds
    ``s * 2^(7 - q % 8) * M^-(q // 8)`` (s = -1, 0 or +1) to the check's difference, M = 48271.
    Meet in the middle over the rows' sign patterns: no non-zero combination is 0 mod 2^31 - 1.
    (Fletcher-16 fails this for every 112-cell strip of 3 rows: the -d, +2d, -d pattern.)"""
    m, p = p2.CHECK_MUL, p2.CHECK_MOD
    inv_m = pow(m, p - 2, p)

    def sums(values, first, last, step):
        """value -> number of row-value tuples (rows first..last-1) with that weighted sum."""
        acc = {0: 1}
        for r in range(first, last):
            f, nxt = pow(step, r, p), {}
            for a, n in acc.items():
                for v in values:
                    k = (a + f * v) % p
                    nxt[k] = nxt.get(k, 0) + n
            acc = nxt
        return acc

    for cols in range(p2.MIN_COLS, p2.MAX_COLS + 1, p2.COL_STEP):
        step = pow(inv_m, 3 * cols // 8, p)  # one row further = L bytes later in the frame
        for offset in range(8):
            weights = [2 ** (7 - (offset + b) % 8) * pow(inv_m, (offset + b) // 8, p) % p for b in range(3)]
            by_value: dict[int, list] = {}
            for signs in itertools.product((-1, 0, 1), repeat=3):
                by_value.setdefault(sum(s * w for s, w in zip(signs, weights)) % p, []).append(signs)
            assert by_value[0] == [(0, 0, 0)]  # a row whose cell changes at all changes the check
            values = list(by_value)
            for rows in (2, 3, 4):
                top, bottom = sums(values, 0, rows // 2, step), sums(values, rows // 2, rows, step)
                cancelling = sum(n * bottom.get((-t) % p, 0) for t, n in top.items()) - 1  # minus "no change"
                assert cancelling == 0, (cols, offset, rows)
    # Fletcher-16 has the blind spot this test rules out for the 4-byte check
    data = bytes(range(126))
    bad = bytearray(data)
    bad[30] -= 1
    bad[72] += 2
    bad[114] -= 1
    assert protocol.fletcher16(bytes(bad)) == protocol.fletcher16(data)
    assert p2.frame_check(bytes(bad)) != p2.frame_check(data)


# Review RP-1 (2026-11-06): before the two final rounds the last body byte had weight 48271^0 = 1 in
# the check, like the trailer's low byte in the trailer value, so +d on both (two cells 10-11 apart,
# two thin strokes) decoded as another valid frame: in every frame kind the addon draws.
RP1_FRAMES = [
    {"type": 6, "seq": 14, "action": {"name": "connect-spotify", "at": 1762380123},
     "clientId": "0123456789abcdef0123456789abcdef"},
    {"type": 6, "seq": 40, "flags": 2, "action": {"name": "disconnect-spotify", "at": 1762390000},
     "clientId": "9f2c1e0a7b3d4c5e6f708192a3b4c5d6"},
    {"type": 6, "seq": 13, "prefs": {"order": "spotify-youtube", "corner": "topright", "fade": True, "resume": True},
     "action": {"name": "test-youtube", "at": 1762380000}},
    {"type": 1, "seq": 0, "prefs": {"order": "spotify-youtube", "corner": "topright", "fade": True, "resume": True}},
    {"type": 1, "seq": 9, "flags": 0x20, "placeKey": 18367},
    {"type": 3, "seq": 5, "flags": 0x20, "dropId": "sub:1429:lion-s-pride-inn", "youtube": "dQw4w9WgXcQ",
     "label": "The Old Lion's Pride Inn of Goldshire"},
]


def _rp1_cell_effects(start: int, final_rounds: int) -> set:
    """Every (check change mod p, trailer value change) one cell can make. ``start`` is the stream
    position of its first bit relative to the trailer's first bit: a body bit ``r`` bits before the
    trailer is worth ``2^(7 - q % 8)`` in byte ``(r - 1) // 8`` back from the trailer, whose weight in the
    check is ``48271^(that + final_rounds)``; a trailer bit ``t`` is worth ``2^(31 - t)`` in the trailer.
    Each of the cell's 3 bits flips either way or not at all (any colour over any content)."""
    m, p = p2.CHECK_MUL, p2.CHECK_MOD
    out = set()
    for signs in itertools.product((-1, 0, 1), repeat=3):
        if not any(signs):
            continue
        dh = dt = 0
        for i, sgn in enumerate(signs):
            q = start + i
            if not sgn:
                continue
            if q >= 32:
                break  # a pad bit: it must stay 0, so the frame fails anyway
            if q >= 0:
                dt += sgn * 2 ** (31 - q)
            else:
                dh += sgn * 2 ** (7 - q % 8) * pow(m, (-q - 1) // 8 + final_rounds, p)  # q % 8: bit in its byte
        else:
            out.add((dh % p, dt))
    return out


def _rp1_passing(final_rounds: int) -> dict:
    """How many changes of (1 body cell + 1 trailer cell), (1 body cell + 2 trailer cells) and (2 body cells
    within 64 cells of the trailer + 1 trailer cell) satisfy the check's congruence, per cell alignment."""
    p = p2.CHECK_MOD
    max_body_bits = 8 * (p2.MAX_FRAME - p2.TRAILER_LEN)
    found = {}
    for align in range(3):  # where the cell boundaries fall relative to the trailer (frame length mod 3)
        starts = [s for s in range(align - 3 * (max_body_bits // 3 + 1), 32, 3) if s + 2 >= -max_body_bits]
        trailer = {s: _rp1_cell_effects(s, final_rounds) for s in starts if s + 2 >= 0}
        body = {s: _rp1_cell_effects(s, final_rounds) for s in starts if s + 2 < 0}
        one = {(dt - dh) % p for eff in trailer.values() for dh, dt in eff}
        two = {(dt1 + dt2 - dh1 - dh2) % p for a, b in itertools.combinations(sorted(trailer), 2)
               for dh1, dt1 in trailer[a] for dh2, dt2 in trailer[b]}
        near = [s for s in body if s > -64 * 3]
        found[align] = (sum(dh in one for eff in body.values() for dh, _ in eff),
                        sum(dh in two for eff in body.values() for dh, _ in eff),
                        sum((x + y) % p in one for a, b in itertools.combinations(near, 2)
                            for x, _ in body[a] for y, _ in body[b]))
    return found


def test_no_change_of_a_few_cells_across_the_trailer_passes_rp1():
    """Exhaustive and algebraic, for every frame length and position: no change confined to one body cell
    plus one or two trailer cells, or to two body cells near the trailer plus one trailer cell, satisfies
    the check -- any colour over any content (a pass needs ``change of the check == change of the
    trailer (mod 2^31 - 1)``). Without the final rounds every alignment had such cell pairs: the last body
    byte and the trailer's low byte both had the weight 1. And no change of one body byte together with
    one trailer byte, of any amounts, passes."""
    m, p = p2.CHECK_MUL, p2.CHECK_MOD
    assert p2.CHECK_FINAL_ROUNDS == 2
    assert all(n == (0, 0, 0) for n in _rp1_passing(2).values())
    assert all(n[0] > 0 for n in _rp1_passing(0).values())  # the blind spot the final rounds close
    deltas = [d for d in range(-255, 256) if d]
    trailer_byte = {(d * 256 ** j) % p for j in range(4) for d in deltas}
    for dist in range(p2.MAX_FRAME - p2.TRAILER_LEN):
        w = pow(m, dist + p2.CHECK_FINAL_ROUNDS, p)
        assert not any((d * w) % p in trailer_byte for d in deltas), dist
    # the two final rounds are exactly "two more 0x00 bytes"
    data = bytes(range(40))
    h = p2.CHECK_SEED
    for b in data + bytes(2):
        h = (h * m + b + 1) % p
    assert p2.frame_check(data) == h


def test_two_cell_changes_near_the_trailer_fail_rp1(lua):
    """Exhaustive at the cell level for the frames of the review's RP-1 repro (one 3 px row each): every
    pair of cells whose second cell is a trailer cell and whose first is one of the 14 cells before it, each
    set to each of the 7 other colours, fails to decode -- it never yields another valid frame (before the
    fix 4 to 9 such pairs passed per frame). The Lua and Python checks agree on every frame."""
    for ev in RP1_FRAMES:
        fr = p2.frame_from_event(ev, max_bytes=p2.capacity(1))
        raw = p2.encode_frame(fr)
        assert lua.P.FrameCheck(ltable(lua, raw[:-4])) == int.from_bytes(raw[-4:], "big")
        grid = p2.encode_cells(fr, p2.Geometry.beacon(3))
        codes0, cols, rows = grid.codes(), grid.cols, grid.rows
        assert rows == 1
        first_trailer_bit = p2.LEAD_CELLS * 3 + 8 * (len(raw) - p2.TRAILER_LEN)
        trailer_cells = range(first_trailer_bit // 3, (first_trailer_bit + 8 * p2.TRAILER_LEN + 2) // 3)
        wrong = []
        for j in trailer_cells:
            for i in range(max(p2.LEAD_CELLS, j - 14), j):
                for ci in range(8):
                    if ci == codes0[i]:
                        continue
                    for cj in range(8):
                        if cj == codes0[j]:
                            continue
                        c = list(codes0)
                        c[i], c[j] = ci, cj
                        got = p2.decode_cell_codes(c, cols, rows)
                        if got is not None and got.raw != raw:
                            wrong.append((i, ci, j, cj, got.describe()))
        assert wrong == [], (fr.describe(), wrong[:3])


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
    for t, name in zip(range(7), ["NONE", "HELLO", "ARM", "FIRE", "CANCEL", "PING", "CONFIG"]):
        ev = {"type": t, "seq": t * 40, "flags": t, "dropId": f"zone:{t}", "artist": "A", "title": "T",
              "prefs": {"order": "youtube", "fade": False}, "action": {"name": "test-youtube", "at": 1762380000}}
        f = p2.frame_from_event(ev)
        if name == "CONFIG":  # DESIGN-v1.2.1 2.2: no drop fields, PREFS and the ACTION only
            assert (f.place_key, f.query, f.prefs, f.action) == (None, None, p2.Prefs("youtube", fade=False),
                                                                 p2.Action(4, 1762380000))
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
