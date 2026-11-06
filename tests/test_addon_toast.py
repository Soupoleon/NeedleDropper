"""In-game title card (SPEC-v1.1 B): NeedleDropperTitleCard, the product's only popup.

Driven through the WoW shim: what each drop kind shows on localized clients, letter spacing
(UTF-8 aware, none for CJK), the OnUpdate timeline on the fake clock, restart, combat fade,
the title card settings, previews, the options panel, and the idle cost (no OnUpdate script
and nothing per frame while no card is on screen).

v1.2 migration: the v1 test map became a built-in test soundtrack (``make_set``); drops are
identified by the Protocol2 event's ``dropId``; a song with only a title or only an artist is a
draft in v1.2 (it never plays from a soundtrack), so its card is shown through the window's Test
(Director.PlayTest). New here: the card waits for Blizzard's zone / subzone banner (baseline
ND-DIR-06)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import AddonHarness, make_set  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
     "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 60000},
    {"id": "area:87", "kind": "subzone", "mapID": 1429, "areaID": 87, "subzone": "Goldshire",
     "label": "Goldshire", "title": "Inn Song", "artist": "Bard", "durationMs": 30000},
    {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall",
     "title": "Dust", "artist": "Farmer", "durationMs": 30000},
    # a landmark by name only (no areaID); its label differs from the subzone on purpose
    {"id": "sub:1436:sentinel-hill", "kind": "subzone", "mapID": 1436, "subzone": "Sentinel Hill",
     "label": "Sentinel Hill Watch", "title": "Watch", "artist": "Guard", "durationMs": 30000},
    {"id": "inst:36", "kind": "instance", "instanceID": 36, "label": "The Deadmines",
     "title": "Below", "artist": "Miners", "durationMs": 30000},
    # a zone this client's C_Map does not know: the pack label is all there is
    {"id": "zone:2548", "kind": "zone", "mapID": 2548, "label": "Riverglades",
     "title": "Reeds", "artist": "Delta", "durationMs": 30000},
]
SET = make_set(DROPS)
FAST = {"historyMode": "fresh", "cooldownMin": 0}
DOT = "·"
UI_W, UI_H = 768 * 2560 / 1440, 768.0      # UIParent in the shim (2560x1440, UI scale 1)
REST_Y = -UI_H * 0.22                       # default toastY
FRIZ = "Fonts\\FRIZQT__.TTF"


def credit(artist: str, title: str) -> str:
    return f"{artist}  {DOT}  {title}"


def smooth(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def ease_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


def localized(locale: str):
    return lambda hh: hh.shim.SetLocale(locale)


def at(h: AddonHarness, t: float) -> None:
    """Advance the fake clock to absolute time `t` and draw a frame exactly there."""
    h.advance(max(0.0, t - h.now))
    h.render()


def on_update_frames(h: AddonHarness) -> int:
    return h.lua("local n = 0 for _, f in ipairs(SHIM.frames) do "
                 "if f.__scripts.OnUpdate then n = n + 1 end end return n")


# ---------------------------------------------------------------- the frame --

def test_card_is_a_quiet_uiparent_child_without_a_background():
    h = AddonHarness(sets=[SET])
    assert h.toast_frame() is None                       # created on first use
    h.slash("play zone:1429")
    f = h.toast_frame()
    assert f.GetName(f) == "NeedleDropperTitleCard"
    assert h.lua("return NeedleDropperTitleCard:GetParent() == UIParent") is True
    assert f.GetFrameStrata(f) == "FULLSCREEN_DIALOG" and not f.IsMouseEnabled(f)
    assert sorted(r["__kind"] for r in f["__regions"].values()) == ["FontString", "FontString", "Texture"]
    assert h.lua("return #NeedleDropperTitleCard.__children") == 0   # no box, no art, no buttons
    rule = h.toast_rule()                                # line 2: the rule is the only texture
    assert h.from_lua(rule["__color"]) == pytest.approx([1, 1, 1, 0.4])
    assert float(rule.GetHeight(rule)) == pytest.approx(768 / 1440)   # one physical pixel
    for fs, size in zip(h.toast_font_strings(), (30, 15)):
        path, fsize, flags = fs.GetFont(fs)
        assert (path, flags) == (FRIZ, "OUTLINE") and fsize == pytest.approx(size)
        assert h.from_lua(fs["__textColor"]) == pytest.approx([0.95, 0.93, 0.88, 1])
        shadow = h.from_lua(fs["__shadowColor"])
        assert shadow[:3] == [0, 0, 0] and 0 < shadow[3] < 1          # soft shadow
        assert h.from_lua(fs["__shadowOffset"]) == [1, -1]
        assert fs["__justifyH"] == "CENTER"
    h.advance(1.3)
    point, rel, rel_point, x, y = h.toast_anchor()
    assert (point, rel_point, x) == ("TOP", "TOP", 0) and y == pytest.approx(REST_Y)
    assert h.lua("local _, rel = NeedleDropperTitleCard:GetPoint(1) return rel == UIParent") is True
    ui = h.g.UIParent
    ui.Hide(ui)                                          # Alt+Z hides it with the rest of the UI
    assert not h.toast_visible()
    ui.Show(ui)
    assert h.toast_visible()


def test_card_animates_itself_even_when_fade_helpers_exist():
    h = AddonHarness(sets=[SET], before_load=lambda hh: hh.shim.EnableFadeHelpers())
    h.slash("play zone:1429")
    assert h.from_lua(h.shim.fades) == {}
    f = h.toast_frame()
    assert f.GetScript(f, "OnUpdate") is not None
    h.advance(10.3)
    assert not h.toast_visible() and h.from_lua(h.shim.fades) == {}


# -------------------------------------------------------------- the content --

def test_zone_drop_card_reads_place_rule_and_credit():
    h = AddonHarness(sets=[SET])
    h.set_location(1429, "Northshire Valley")
    h.advance(10.5)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == ["zone:1429"]
    assert h.toast_texts() == ["E L W Y N N   F O R E S T", credit("Low Roar", "I'll Keep Coming")]
    assert h.ns.Toast.Subtitle(h.ns.Map.byId["zone:1429"]) == f"Low Roar {DOT} I'll Keep Coming"  # chat form


@pytest.mark.parametrize("locale,line1,font", [
    ("enUS", "E L W Y N N   F O R E S T", FRIZ),
    ("frFR", "F O R Ê T   D ' E L W Y N N", FRIZ),
    ("deDE", "W A L D   V O N   E L W Y N N", FRIZ),
    ("ruRU", "Э Л В И Н Н С К И Й   Л Е С", "Fonts\\FRIZQT___CYR.TTF"),
    ("zhCN", "艾尔文森林", "Fonts\\ARKai_T.ttf"),
    ("koKR", "엘윈 숲", "Fonts\\2002.TTF"),
])
def test_zone_card_uses_the_client_localized_map_name(locale, line1, font):
    h = AddonHarness(sets=[SET], before_load=localized(locale))
    h.set_location(1429, "")
    h.advance(10.5)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == ["zone:1429"]
    assert h.toast_texts() == [line1, credit("Low Roar", "I'll Keep Coming")]
    assert [f[0] for f in h.toast_fonts()] == [font, font]           # STANDARD_TEXT_FONT of that client


@pytest.mark.parametrize("locale,subzone,line1", [
    ("enUS", "Goldshire", "G O L D S H I R E"),
    ("frFR", "Comté-de-l'Or", "C O M T É - D E - L ' O R"),
    ("deDE", "Goldhain", "G O L D H A I N"),
    ("ruRU", "Златоземье", "З Л А Т О З Е М Ь Е"),
    ("zhCN", "闪金镇", "闪金镇"),
    ("koKR", "골드샤이어", "골드샤이어"),
])
def test_landmark_card_uses_the_localized_area_name(locale, subzone, line1):
    h = AddonHarness(sets=[SET], settings=FAST, before_load=localized(locale))
    h.set_location(1429, subzone)
    h.advance(6)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == ["area:87"]
    assert h.toast_texts() == [line1, credit("Bard", "Inn Song")]
    h.set_location(1436, "")
    h.slash("replay area:87")                            # C_Map.GetAreaInfo works from anywhere
    assert h.toast_texts()[0] == line1


def test_named_landmark_uses_the_live_subzone_text_only_while_there():
    h = AddonHarness(sets=[SET], settings=FAST)
    h.set_location(1436, "Sentinel Hill")
    h.advance(6)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == ["sub:1436:sentinel-hill"]
    assert h.toast_texts() == ["S E N T I N E L   H I L L", credit("Guard", "Watch")]   # the client's text
    h.set_location(1429, "Northshire Valley")
    h.slash("replay sub:1436:sentinel-hill")
    assert h.toast_texts()[0] == "S E N T I N E L   H I L L   W A T C H"            # elsewhere: the label


def test_instance_card_uses_the_instance_name_only_inside_it():
    h = AddonHarness(sets=[SET], settings=FAST, before_load=localized("frFR"))
    h.set_location(291, "", instance={"id": 36, "name": "Les Mortemines"})
    h.advance(5)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == ["inst:36"]
    assert h.toast_texts() == ["L E S   M O R T E M I N E S", credit("Miners", "Below")]
    h.set_location(1429, "")
    # outside, GetInstanceInfo() describes the continent ("Kalimdor"): use the pack label
    h.slash("replay inst:36")
    assert h.toast_texts()[0] == "T H E   D E A D M I N E S"
    # inside another instance it names that one: still the label
    h.set_location(1453, "", instance={"id": 389, "name": "Gouffre de Ragefeu"})
    h.slash("replay inst:36")
    assert h.toast_texts()[0] == "T H E   D E A D M I N E S"


def test_place_falls_back_to_the_pack_label():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:2548")                            # unknown to this client's C_Map
    assert h.toast_texts() == ["R I V E R G L A D E S", credit("Delta", "Reeds")]
    # a French client without C_Map: the (English) pack label, not a blank card
    h2 = AddonHarness(sets=[SET], before_load=lambda hh: (hh.shim.SetLocale("frFR"), hh.lua("C_Map = nil")))
    h2.slash("play zone:1429")
    h2.slash("play area:87")
    assert h2.toast_texts()[0] == "G O L D S H I R E"
    h2.slash("play zone:1429")
    assert h2.toast_texts()[0] == "E L W Y N N   F O R E S T"


def test_secret_map_names_are_never_touched():
    def secret_names(hh):
        hh.lua("""
          local real = C_Map.GetMapInfo
          C_Map.GetMapInfo = function(id)
            local info = real(id)
            if info then info.name = SHIM.MakeSecret("map name") end
            return info
          end
          C_Map.GetAreaInfo = function() return SHIM.MakeSecret("area name") end
        """)
    h = AddonHarness(sets=[SET], before_load=secret_names)
    h.slash("play zone:1429")
    assert h.toast_texts()[0] == "E L W Y N N   F O R E S T"
    h.slash("play area:87")
    assert h.toast_texts()[0] == "G O L D S H I R E"
    assert h.from_lua(h.log.secretTouches) == {}


def test_credit_line_with_one_part_missing():
    h = AddonHarness(sets=[SET])
    h.call("Director.PlayTest", {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
                                 "title": "Only Title", "artist": ""})
    assert h.toast_texts()[1] == "Only Title"
    h.call("Director.PlayTest", {"id": "zone:1436", "kind": "zone", "mapID": 1436, "label": "Westfall",
                                 "title": "", "artist": "Only Artist"})
    assert h.toast_texts()[1] == "Only Artist"


def test_pipes_in_pack_text_cannot_inject_ui_escapes():
    m = make_set([{"id": "zone:2548", "kind": "zone", "mapID": 2548, "label": "Ice|cffff0000Red",
                   "title": "|TInterface\\Icons\\X:0|t Song", "artist": "A"}])
    h = AddonHarness(sets=[m])
    h.slash("play zone:2548")
    place, line3 = h.toast_texts()
    assert place == "I C E || C F F F F 0 0 0 0 R E D"
    assert line3 == f"A  {DOT}  ||TInterface\\Icons\\X:0||t Song"


def test_test_and_replay_cards_use_the_same_layout():
    h = AddonHarness(sets=[SET])
    h.slash("test")
    assert h.toast_texts() == ["N E E D L E D R O P P E R", "Test drop"]
    assert h.toast_rule() is not None and h.toast_card()["ruleW"] > 0
    h.slash("stop")
    h.set_location(1429, "Northshire Valley")
    h.advance(10.5)
    assert [e["dropId"] for e in h.events_of(ET.FIRE) if e["dropId"] != "test"] == ["zone:1429"]
    fired = h.toast_texts()
    h.advance(12)
    assert not h.toast_visible()
    t0 = h.now
    h.slash("replay")
    assert h.toast_texts() == fired
    h.render()
    assert h.toast_card()["start"] == pytest.approx(t0) and h.toast_alpha() == 0   # a full card again
    at(h, t0 + 1.2)
    assert h.toast_alpha() == pytest.approx(1) and h.toast_y() == pytest.approx(REST_Y)


# -------------------------------------------------------------- the spacing --

@pytest.mark.parametrize("label,expected", [
    ("Elwynn Forest", "E L W Y N N   F O R E S T"),
    ("Lion's Pride Inn", "L I O N ' S   P R I D E   I N N"),
    ("  Stormwind \t City  ", "S T O R M W I N D   C I T Y"),
    ("A Very Long Landmark Name Indeed", "A   V E R Y   L O N G   L A N D M A R K   N A M E   I N D E E D"),
    ("Forêt d'Elwynn", "F O R Ê T   D ' E L W Y N N"),
    ("Voilà", "V O I L À"),                              # à is C3 A0: byte 0xA0 is not a space
    ("Straße", "S T R A ß E"),                           # ß has no single capital
    ("ÿ œ ą ł ž", "Ÿ   Œ   Ą   Ł   Ž"),
    ("Элвиннский лес", "Э Л В И Н Н С К И Й   Л Е С"),
    ("ёлка", "Ё Л К А"),
    ("A’B", "A ’ B"),                          # U+2019 is below U+3000: spaced
    ("艾尔文森林", "艾尔文森林"),                         # CJK: no spacing
    ("엘윈 숲", "엘윈 숲"),                               # Hangul: no spacing, word space kept
    ("Goldshire 闪金镇", "GOLDSHIRE 闪金镇"),             # any code point >= U+3000: none at all
    ("　Elwynn ", "　ELWYNN"),                   # U+3000 itself counts
    ("", ""),
    ("   ", ""),
])
def test_spaced_caps(label, expected):
    h = AddonHarness(sets=[SET], login=False)
    assert h.ns.Toast.SpacedCaps(label) == expected


def test_upper_case_is_utf8_safe_and_leaves_other_scripts_alone():
    h = AddonHarness(sets=[SET], login=False)
    up = h.ns.Toast.Upper
    assert up("abc xyz 09") == "ABC XYZ 09"
    assert up("àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþ") == "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ"
    assert up("÷ × ı ĸ ŉ ſ ß") == "÷ × ı ĸ ŉ ſ ß"               # no simple capital: unchanged
    assert up("абвгдежзийклмнопрстуфхцчшщъыьэюя ёђѓєѕіїјљњћќѝўџ") == \
        "АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ ЁЂЃЄЅІЇЈЉЊЋЌЍЎЏ"
    assert up("Ĳĳ Ĺĺ Ŋŋ Ŷŷ Źź") == "ĲĲ ĹĹ ŊŊ ŶŶ ŹŹ"
    assert up("艾尔文森林 林 엘윈 Ωω") == "艾尔文森林 林 엘윈 Ωω"      # 林 is E6 9E 97 (0x9E = ž in cp1252)
    assert h.ns.Toast.SpacedCaps(None) == "" and h.ns.Toast.SpacedCaps(5) == ""
    assert h.ns.Toast.Upper(None) == ""


def test_malformed_utf8_is_dropped_not_corrupted():
    h = AddonHarness(sets=[SET], login=False)
    call_with_bad_bytes = h.lua(r'return function(f) return f("Ab\255\128c \192\175d") end')
    assert call_with_bad_bytes(h.ns.Toast.SpacedCaps) == "A B C   D"   # stray bytes vanish, nothing breaks


# ------------------------------------------------------------- the timeline --

def test_timeline_fade_in_drift_rule_growth_hold_and_fade_out():
    h = AddonHarness(sets=[SET])
    t0 = h.now
    h.slash("play zone:1429")
    card = h.toast_card()
    assert card["start"] == pytest.approx(t0) and card["hold"] == 7
    rule_w = card["ruleW"]
    assert 160 <= rule_w <= UI_W * 0.9
    h.render()
    assert h.toast_alpha() == 0 and h.toast_y() == pytest.approx(REST_Y - 8) and h.toast_rule_width() == 0
    for t in (0.3, 0.6, 0.9):                            # fade in 1.2 s, drifting up 8 px, rule growing
        at(h, t0 + t)
        p = t / 1.2
        assert h.toast_alpha() == pytest.approx(smooth(p))
        assert h.toast_y() == pytest.approx(REST_Y - 8 * (1 - ease_out(p)))
        assert h.toast_rule_width() == pytest.approx(rule_w * ease_out(p))
    at(h, t0 + 1.2)
    assert h.toast_alpha() == pytest.approx(1)
    assert h.toast_y() == pytest.approx(REST_Y) and h.toast_rule_width() == pytest.approx(rule_w)
    at(h, t0 + 1.2 + 7 - 0.05)                           # hold toastSeconds = 7
    assert h.toast_alpha() == pytest.approx(1)
    for t, alpha in ((8.7, 1 - smooth(0.25)), (9.2, 0.5), (9.9, 1 - smooth(0.85))):   # fade out 2.0 s
        at(h, t0 + t)
        assert h.toast_alpha() == pytest.approx(alpha)
        assert h.toast_y() == pytest.approx(REST_Y) and h.toast_rule_width() == pytest.approx(rule_w)
    h.advance(t0 + 10.2 + 1 / 30 - h.now)
    f = h.toast_frame()
    assert not h.toast_visible() and h.toast_card() is None and not f.IsShown(f)
    assert f.GetScript(f, "OnUpdate") is None and on_update_frames(h) == 0


def test_timeline_runs_on_ordinary_frames_too():
    """Without render(): the shim's 1/30 s frames drive the same curve."""
    h = AddonHarness(sets=[SET])
    t0 = h.now
    h.slash("play zone:1429")
    h.advance(0.6)
    assert h.toast_alpha() == pytest.approx(smooth((h.now - t0) / 1.2), abs=0.05)
    h.advance(2)
    assert h.toast_alpha() == 1.0
    h.advance(7.0)                                       # t = 9.6: fading out
    assert 0 < h.toast_alpha() < 1
    h.advance(0.7)                                       # t = 10.3
    assert not h.toast_visible()


def test_new_card_restarts_the_timeline():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    h.advance(5)
    assert h.toast_alpha() == pytest.approx(1)
    t1 = h.now
    h.slash("play zone:1436")
    assert h.toast_texts() == ["W E S T F A L L", credit("Farmer", "Dust")]
    h.render()
    assert h.toast_card()["start"] == pytest.approx(t1)
    assert h.toast_alpha() == 0 and h.toast_y() == pytest.approx(REST_Y - 8) and h.toast_rule_width() == 0
    at(h, t1 + 0.6)
    assert h.toast_alpha() == pytest.approx(smooth(0.5))
    at(h, t1 + 10.1)                                     # the first card would be long gone
    assert h.toast_visible()
    h.advance(0.2)
    assert not h.toast_visible()


def test_a_new_card_also_restarts_a_card_that_is_fading_out():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    h.advance(2)
    h.combat(True)
    h.advance(0.25)
    t1 = h.now
    h.slash("test")                                      # manual plays still show in combat
    at(h, t1 + 1.2)
    assert h.toast_texts()[0] == "N E E D L E D R O P P E R" and h.toast_alpha() == pytest.approx(1)


def test_combat_start_fades_the_card_out_in_half_a_second():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    h.advance(3)
    h.combat(True)
    h.render()
    assert h.toast_alpha() == pytest.approx(1)
    h.advance(0.25)
    h.render()
    assert h.toast_alpha() == pytest.approx(0.5)
    h.advance(0.25 + 1 / 30)
    f = h.toast_frame()
    assert not h.toast_visible() and f.GetScript(f, "OnUpdate") is None
    h.combat(False)
    h.combat(True)                                       # nothing on screen: nothing to do
    assert not h.toast_visible()


def test_combat_during_the_fade_in_fades_from_where_the_card_is():
    h = AddonHarness(sets=[SET])
    t0 = h.now
    h.slash("play zone:1429")
    at(h, t0 + 0.6)
    a0, y0, w0 = h.toast_alpha(), h.toast_y(), h.toast_rule_width()
    assert a0 == pytest.approx(0.5)
    h.combat(True)
    at(h, t0 + 0.85)
    assert h.toast_alpha() == pytest.approx(a0 * 0.5)
    assert h.toast_y() == pytest.approx(y0) and h.toast_rule_width() == pytest.approx(w0)   # frozen
    h.advance(0.3)
    assert not h.toast_visible()


def test_stop_fades_the_card_quickly_and_hide_without_quick_takes_two_seconds():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    h.advance(2)
    h.slash("stop")                                      # Director: CancelActive -> Hide(true)
    h.advance(0.25)
    h.render()
    assert h.toast_alpha() == pytest.approx(0.5)
    h.advance(0.3)
    assert not h.toast_visible()
    h.slash("play zone:1429")
    h.advance(2)
    h.ns.Toast.Hide(False)
    h.advance(1.0)
    h.render()
    assert h.toast_alpha() == pytest.approx(0.5)
    h.advance(1.05)
    assert not h.toast_visible()
    h.slash("play zone:1429")
    h.ns.Toast.Hide(True)                                # still invisible at t = 0: gone at once
    assert h.toast_card() is None and not h.toast_visible()


def test_card_hidden_with_the_ui_does_not_resume_later():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    h.advance(1)
    ui = h.g.UIParent
    ui.Hide(ui)
    h.advance(30)                                        # no frames are drawn for a hidden UI
    assert h.toast_card() is not None
    ui.Show(ui)
    h.advance(0.05)
    f = h.toast_frame()
    assert h.toast_card() is None and not f.IsShown(f) and f.GetScript(f, "OnUpdate") is None


def test_no_per_frame_cost_while_idle():
    h = AddonHarness(sets=[SET])
    assert on_update_frames(h) == 0
    h.slash("play zone:1429")
    assert on_update_frames(h) == 1
    h.advance(10.3)
    assert on_update_frames(h) == 0
    h.slash("test")
    h.slash("stop")
    h.advance(0.6)
    assert on_update_frames(h) == 0


# -------------------------------------------------------------- the settings --

def test_title_card_settings_defaults_clamping_and_snapping():
    h = AddonHarness(sets=[SET])
    s = h.settings()
    assert (s["toast"], s["toastScale"], s["toastY"], s["toastSeconds"]) == (True, 1.0, 0.22, 7)
    for cmd, want in [("toastScale 2", 1.6), ("toastScale 0.1", 0.6), ("toastScale 1.23", 1.25),
                      ("toastScale 1.3", 1.3), ("toastScale 120%", 1.2), ("toastY 0.9", 0.5), ("toastY 0", 0.08),
                      ("toastY 0.123", 0.12), ("toastY 35 %", 0.35), ("toastSeconds 2.4", 3),
                      ("toastSeconds 99", 15), ("toastSeconds 9.6", 10), ("TOASTSECONDS 7", 7)]:
        out = h.slash("set " + cmd)
        key = {"toastscale": "toastScale", "toasty": "toastY", "toastseconds": "toastSeconds"}[cmd.split()[0].lower()]
        assert h.settings()[key] == want, cmd             # exact: no float noise from the step grid
        assert out == ["NeedleDropper: %s = %s" % (key, "%.14g" % want)], cmd
    for bad in ("toastScale big", "toastY %", "toastSeconds"):
        out = h.slash("set " + bad)
        assert len(out) == 1 and ("must be a number" in out[0] or "usage" in out[0]), bad


def test_saved_title_card_settings_are_repaired_and_added_on_upgrade():
    h = AddonHarness(sets=[SET], db={"settings": {"toastScale": "huge", "toastY": -3, "toastSeconds": 99.5,
                                               "toast": "yes"}})
    s = h.settings()
    assert (s["toastScale"], s["toastY"], s["toastSeconds"], s["toast"]) == (1.0, 0.08, 15, True)
    old = AddonHarness(sets=[SET], db={"schema": 1, "settings": {"toast": False, "dwellSec": 12}})   # a v1.0 install
    s = old.settings()
    assert (s["toast"], s["toastScale"], s["toastY"], s["toastSeconds"], s["dwellSec"]) == (False, 1.0, 0.22, 7, 12)


def test_scale_position_and_hold_settings_shape_the_card():
    h = AddonHarness(sets=[SET], settings={"toastScale": 1.5, "toastY": 0.3, "toastSeconds": 3})
    t0 = h.now
    h.slash("play zone:1429")
    assert [f[1] for f in h.toast_fonts()] == pytest.approx([45, 22.5])
    card = h.toast_card()
    assert card["drift"] == pytest.approx(12) and card["hold"] == 3
    at(h, t0)
    assert h.toast_y() == pytest.approx(-UI_H * 0.3 - 12)
    at(h, t0 + 1.2)
    assert h.toast_y() == pytest.approx(-UI_H * 0.3)
    at(h, t0 + 1.2 + 3 + 1.0)
    assert h.toast_alpha() == pytest.approx(0.5)
    h.advance(1.05)
    assert not h.toast_visible()


def test_setting_changes_relayout_a_showing_card_live():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    h.advance(2)
    h.slash("set toastY 0.4")
    assert h.toast_y() == pytest.approx(-UI_H * 0.4)
    h.slash("set toastScale 0.8")
    assert [f[1] for f in h.toast_fonts()] == pytest.approx([24, 12])
    assert h.toast_alpha() == pytest.approx(1)           # the timeline is untouched
    h.slash("reset settings")
    assert h.toast_y() == pytest.approx(REST_Y) and [f[1] for f in h.toast_fonts()] == pytest.approx([30, 15])


def test_ui_scale_and_display_changes_relayout_a_showing_card():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    h.advance(2)
    h.state.uiScale = 0.8
    h.fire("UI_SCALE_CHANGED")
    assert h.toast_y() == pytest.approx(-(768 / 0.8) * 0.22)
    rule = h.toast_rule()
    assert float(rule.GetHeight(rule)) == pytest.approx((768 / 1440) / 0.8)   # still one physical pixel
    h.state.physH = 1080
    h.state.physW = 1920
    h.fire("DISPLAY_SIZE_CHANGED")
    assert float(rule.GetHeight(rule)) == pytest.approx((768 / 1080) / 0.8)


def test_rule_is_one_physical_pixel_without_pixelutil():
    h = AddonHarness(sets=[SET], before_load=lambda hh: hh.lua("PixelUtil = nil"))
    h.slash("play zone:1429")
    rule = h.toast_rule()
    assert float(rule.GetHeight(rule)) == pytest.approx(768 / 1440)          # GetPhysicalScreenSize
    h2 = AddonHarness(sets=[SET], before_load=lambda hh: hh.lua("PixelUtil = nil; GetPhysicalScreenSize = nil"))
    h2.slash("play zone:1429")
    rule = h2.toast_rule()
    assert float(rule.GetHeight(rule)) == pytest.approx(768 / 1080)


def test_font_falls_back_when_standard_text_font_is_missing_or_unusable():
    h = AddonHarness(sets=[SET], before_load=lambda hh: hh.lua("STANDARD_TEXT_FONT = nil"))
    h.slash("play zone:1429")
    assert h.toast_fonts() == [(FRIZ, 30, "OUTLINE"), (FRIZ, 15, "OUTLINE")]

    def broken_cyrillic(hh):
        hh.shim.SetLocale("ruRU")
        hh.set_state("badFonts", {"Fonts\\FRIZQT___CYR.TTF": True})
    h2 = AddonHarness(sets=[SET], before_load=broken_cyrillic)
    h2.slash("play zone:1429")
    assert [f[0] for f in h2.toast_fonts()] == [FRIZ, FRIZ]
    assert h2.toast_texts()[0] == "Э Л В И Н Н С К И Й   Л Е С"


def test_long_place_names_shrink_to_fit_the_screen_instead_of_losing_their_spacing():
    long_label = "The Exceedingly Long And Winding Road Of Nowhere In Particular"
    m = make_set([{"id": "zone:2548", "kind": "zone", "mapID": 2548, "label": long_label,
                   "title": "T", "artist": "A"}])
    h = AddonHarness(sets=[m])
    h.slash("play zone:2548")
    place = h.toast_font_strings()[0]
    assert h.toast_texts()[0] == h.ns.Toast.SpacedCaps(long_label)
    assert place.GetFont(place)[1] < 30
    assert float(place.GetStringWidth(place)) <= UI_W * 0.9
    assert h.toast_card()["ruleW"] <= UI_W * 0.9 + 1e-9


def test_toast_off_shows_no_card_for_drops():
    h = AddonHarness(sets=[SET], settings={"toast": False})
    h.set_location(1429, "Northshire Valley")
    h.advance(10.5)
    assert h.events_of(ET.FIRE)                          # the drop itself still plays
    for cmd in ("play zone:1436", "replay", "test"):
        h.slash(cmd)
        h.advance(1.5)
        assert not h.toast_visible(), cmd
    assert h.toast_frame() is None
    assert h.ns.Toast.Show(h.ns.Map.byId["zone:1429"]) is False


def test_turning_cards_off_hides_a_showing_card():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    h.advance(2)
    out = h.slash("toast off")
    assert out == ["NeedleDropper: title card off: needle drops start without the in-game card."]
    h.advance(0.6)
    assert not h.toast_visible() and h.settings()["toast"] is False
    h.slash("toast on")
    assert h.settings()["toast"] is True
    out = h.slash("toast upside-down")
    assert out and "usage: /needle toast" in out[0]


# --------------------------------------------------------------- the preview --

def test_preview_shows_the_drop_that_belongs_here_and_plays_nothing():
    h = AddonHarness(sets=[SET])
    h.set_location(1429, "Goldshire")                    # landmark beats zone, like the Director
    mark, music, sounds = h.mark(), len(h.music()), len(h.sounds())
    out = h.slash("toast")
    assert h.toast_texts() == ["G O L D S H I R E", credit("Bard", "Inn Song")]
    assert out[0] == "NeedleDropper: title card preview: Goldshire [area:87]"
    assert h.events_since(mark) == [] and len(h.music()) == music and len(h.sounds()) == sounds
    assert h.toast_card()["preview"] is True
    h.set_location(1429, "Northshire Valley")
    h.slash("toast preview")
    assert h.toast_texts()[0] == "E L W Y N N   F O R E S T"


@pytest.mark.parametrize("locale,line1", [("enUS", "D U R O T A R"), ("ruRU", "Д У Р О Т А Р"),
                                          ("zhCN", "杜隆塔尔")])
def test_preview_without_a_mapped_drop_names_the_zone(locale, line1):
    h = AddonHarness(sets=[SET], before_load=localized(locale))      # logged in in Durotar: nothing mapped
    out = h.slash("toast")
    assert h.toast_texts() == [line1, "Preview"]
    assert "no needle drop is mapped here" in out[0]
    assert any("toastScale" in line for line in out)


def test_preview_in_an_unmapped_instance_names_the_instance():
    h = AddonHarness(sets=[SET])
    h.set_location(1453, "", instance={"id": 389, "name": "Ragefire Chasm"})
    h.slash("toast")
    assert h.toast_texts() == ["R A G E F I R E   C H A S M", "Preview"]


def test_preview_works_while_cards_are_off_and_says_so():
    h = AddonHarness(sets=[SET], settings={"toast": False})
    out = h.slash("toast")
    h.advance(1.3)
    assert h.toast_visible() and h.toast_texts() == ["D U R O T A R", "Preview"]
    assert any("/needle toast on" in line for line in out)
    h.advance(10)
    assert not h.toast_visible()


# ------------------------------------------------------ status, help, options --

def test_status_reports_the_title_card():
    h = AddonHarness(sets=[SET])
    assert "NeedleDropper: title card: on - size 100%, 22% from the top, 7 s (/needle toast previews it here)" \
        in h.slash("status")
    h.slash("set toastScale 1.2")
    h.slash("set toastY 0.3")
    h.slash("set toastSeconds 10")
    assert any("size 120%, 30% from the top, 10 s" in line for line in h.slash("status"))
    h.slash("toast off")
    assert any(line.startswith("NeedleDropper: title card: off") for line in h.slash("status"))


def test_help_and_settings_list_the_title_card():
    h = AddonHarness(sets=[SET])
    assert any("/needle toast - [on|off]" in line for line in h.slash("help"))
    text = "\n".join(h.slash("settings"))
    assert "toastScale = 1 (0.6-1.6)" in text and "toastY = 0.22 (0.08-0.5)" in text
    assert "toastSeconds = 7 (3-15)" in text


def panel(h: AddonHarness) -> list[dict]:
    """The options panel's elements in order: kind plus variable (controls) or name (extras)."""
    out = []
    for e in h.shim.settings.elements.values():
        out.append({"kind": e.kind, "variable": e.variable, "name": e.name, "options": e.options,
                    "click": e.click, "buttonText": e.buttonText})
    return out


def test_options_panel_has_title_card_sliders_headers_and_a_preview_button():
    h = AddonHarness(sets=[SET])
    els = panel(h)
    sliders = {e["variable"]: e["options"] for e in els if e["kind"] == "slider"}
    for key, lo, hi, step, labels in (("toastScale", 0.6, 1.6, 0.05, {1.0: "100%", 0.6: "60%", 1.25: "125%"}),
                                      ("toastY", 0.08, 0.5, 0.01, {0.22: "22%", 0.5: "50%", 0.08: "8%"}),
                                      ("toastSeconds", 3, 15, 1, {7: "7", 15: "15"}),
                                      ("cooldownMin", 0, 240, 1, {10: "10"})):
        o = sliders["NeedleDropper_" + key]
        assert (o.min, o.max, o.step) == pytest.approx((lo, hi, step)), key
        for v, text in labels.items():
            assert o.formatter(v) == text, (key, v)
    headers = [e["name"] for e in els if e["kind"] == "header"]
    # 1.2.1: the Music services section sits between Needle drops and Playback (DESIGN-v1.2.1 1.2)
    assert headers == ["Needle drops", "Music services", "Playback", "Title card", "Companion link", "Troubleshooting"]
    i = next(i for i, e in enumerate(els) if e["kind"] == "header" and e["name"] == "Title card")
    section = [(e["kind"], e["variable"] or e["buttonText"]) for e in els[i + 1:i + 6]]
    assert section == [("checkbox", "NeedleDropper_toast"), ("slider", "NeedleDropper_toastScale"),
                       ("slider", "NeedleDropper_toastY"), ("slider", "NeedleDropper_toastSeconds"),
                       ("button", "Preview")]
    shown = [m for m in h.from_lua(h.ns.SETTINGS) if not m.get("advanced")]   # beaconCorner: /needle set only
    assert h.ns.Options.controls == len(shown) and h.ns.Options.controlErrors == 0
    # 6 section headers + Open NeedleDropper + the title card Preview + Send a test signal + the six
    # Music services buttons (1.2.1: rows 1-3 and 8-10 of DESIGN-v1.2.1 1.2)
    assert h.ns.Options.extras == 15
    button = els[i + 5]
    button["click"]()                                    # Preview: a card for where you are
    assert h.toast_texts() == ["D U R O T A R", "Preview"]


def test_options_slider_edits_are_coerced_and_relayout_the_card():
    h = AddonHarness(sets=[SET])
    h.slash("toast")
    h.advance(2)
    y = h.shim.settings.settings["NeedleDropper_toastY"]
    y.SetValue(y, 0.30000000000000004)                   # slider float noise
    assert h.settings()["toastY"] == 0.3 and h.toast_y() == pytest.approx(-UI_H * 0.3)
    scale = h.shim.settings.settings["NeedleDropper_toastScale"]
    scale.SetValue(scale, 7)
    assert h.settings()["toastScale"] == 1.6
    assert [f[1] for f in h.toast_fonts()] == pytest.approx([48, 24])


def test_options_panel_without_headers_or_buttons_still_has_every_setting():
    h = AddonHarness(sets=[SET], before_load=lambda hh: hh.shim.RemoveSettingsExtras())
    els = panel(h)
    assert {e["kind"] for e in els} == {"checkbox", "slider", "dropdown"}
    shown = [m for m in h.from_lua(h.ns.SETTINGS) if not m.get("advanced")]
    assert len(els) == len(shown) == h.ns.Options.controls
    assert h.ns.Options.extras == 0
    h.slash("options")
    assert h.from_lua(h.log.openedCategory)


# --------------------------------------------------- Blizzard's banner (ND-DIR-06) --

def zone_banner(h: AddonHarness, shown_for: float = 0.0) -> None:
    """Blizzard's SubZoneTextFrame (FadingFrame: 0.5 s in, 1 s hold, 2 s out) shown `shown_for`
    seconds ago, and FadingFrame_GetRemainingTime as in Blizzard_FrameXMLUtil/FadingFrame.lua."""
    h.lua(f"""
      SubZoneTextFrame = CreateFrame("Frame", "SubZoneTextFrame", UIParent)
      SubZoneTextFrame.fadeInTime, SubZoneTextFrame.holdTime, SubZoneTextFrame.fadeOutTime = 0.5, 1.0, 2.0
      SubZoneTextFrame.startTime = GetTime() - {shown_for}
      SubZoneTextFrame:Show()
      function FadingFrame_GetRemainingTime(f)
        return f.holdTime + f.fadeInTime + f.fadeOutTime - (GetTime() - f.startTime)
      end
    """)


def test_the_card_waits_until_blizzards_subzone_banner_is_gone():
    h = AddonHarness(sets=[SET])
    zone_banner(h, shown_for=1.5)                 # a discovered landmark fires 1.5 s into the banner
    start = h.now
    h.slash("play area:87")
    card = h.toast_card()
    assert card["wait"] == pytest.approx(2.0) and card["start"] == pytest.approx(start + 2.0)
    at(h, start + 1.9)
    assert h.toast_alpha() == 0.0                 # nothing while the banner is still up
    at(h, start + 2.0 + 0.6)
    assert h.toast_alpha() == pytest.approx(smooth(0.6 / 1.2), abs=1e-6)


def test_no_wait_without_a_banner_and_never_for_a_preview():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    assert h.toast_card()["wait"] == 0
    zone_banner(h, shown_for=0.0)
    h.slash("toast")                               # a preview is shown at once
    assert h.toast_card()["wait"] == 0 and h.toast_card()["preview"] is True


def test_combat_during_the_wait_drops_the_card():
    h = AddonHarness(sets=[SET])
    zone_banner(h, shown_for=0.0)
    h.slash("play zone:1429")
    h.combat(True)
    h.advance(0.1)
    assert h.toast_card() is None and not h.toast_visible()
