"""Addon robustness: Lua 5.1 compile check, TOC, globals, the bundled soundtracks, malformed saved
soundtracks, secret values, SavedVariables repair and migration, slash commands, the add-on
compartment, the options panel and self-tests. (The title card has its own module:
test_addon_toast.py; the window: test_v12_ui_*.)

v1.2 migration: the v1 map (Map.lua / Map.json, ns.BuildMap with its n / hash rules) is gone.
What plays comes from the soundtrack model (Sets\\*.lua built-ins and the player's own
soundtracks); the map checks became checks of the bundled sets and of malformed saved
soundtracks; SavedVariables are schema 2; the slash commands follow DESIGN-v1.2 4.8.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import ADDON_DIR, AddonHarness, make_set, toc_files, toc_lines, user_db  # noqa: E402
from needledropper.models import EventType as ET, Flags  # noqa: E402

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
     "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 60000},
    {"id": "area:87", "kind": "subzone", "mapID": 1429, "areaID": 87, "subzone": "Goldshire",
     "label": "Goldshire", "title": "Inn", "artist": "Bard", "durationMs": 30000},
]
SET = make_set(DROPS)
ADDON_LUA = sorted(str(p.relative_to(ADDON_DIR)).replace("\\", "/") for p in ADDON_DIR.rglob("*.lua"))


# --------------------------------------------------------------- sources --

# 1.2.1 (D15, DESIGN-v1.2.1 6): version 1.2.1, MusicServices.lua after Companion.lua and
# UI\MusicDialog.lua after UI\ShareDialogs.lua.
EXPECTED_TOC = """## Interface: 16001
## Title: NeedleDropper
## Notes: Kojima-style needle drops: curated songs the first time you arrive somewhere.
## Author: NeedleDropper
## Version: 1.2.1
## X-License: MIT
## X-Curse-Project-ID: 1786392
## Category: Audio & Video
## IconTexture: Interface\\Icons\\INV_Misc_Drum_01
## SavedVariables: NeedleDropperDB
## SavedVariablesPerCharacter: NeedleDropperCharDB
## AddonCompartmentFunc: NeedleDropper_OnAddonCompartmentClick
## AddonCompartmentFuncOnEnter: NeedleDropper_OnAddonCompartmentEnter
## AddonCompartmentFuncOnLeave: NeedleDropper_OnAddonCompartmentLeave
Locale.lua
Json.lua
Base64.lua
Protocol2.lua
Core.lua
Sets.lua
Sets\\AzerothMixtape.lua
Sets\\AzerothUnbound.lua
Soundtracks.lua
Share.lua
Companion.lua
MusicServices.lua
Emit.lua
Location.lua
Discovery.lua
Audio.lua
Beacon.lua
ChatLink.lua
Toast.lua
Director.lua
Options.lua
UI\\Window.lua
UI\\Drops.lua
UI\\Soundtracks.lua
UI\\Journal.lua
UI\\ShareDialogs.lua
UI\\MusicDialog.lua
SelfTest.lua
"""


def test_toc_matches_contracts_2_1():
    assert (ADDON_DIR / "NeedleDropper.toc").read_text(encoding="utf-8") == EXPECTED_TOC
    for f in toc_files():
        assert (ADDON_DIR / f).is_file(), f
    # every Lua file of the addon is listed: nothing ships that the client would never load
    assert sorted(toc_files()) == ADDON_LUA


def test_toc_lists_sets_after_the_registry_and_never_bindings_or_v1_files():
    lines = toc_lines()
    assert lines.index("Sets.lua") < lines.index("Sets\\AzerothMixtape.lua") < lines.index("Soundtracks.lua")
    assert "Bindings.xml" not in lines and (ADDON_DIR / "Bindings.xml").is_file()
    for retired in ("Protocol.lua", "Map.lua", "Map.json"):
        assert retired not in lines and not (ADDON_DIR / retired).exists(), retired


@pytest.mark.parametrize("fname", ADDON_LUA)
def test_every_addon_file_compiles_under_lua51_and_starts_with_the_varargs(fname):
    h = AddonHarness(login=False, files=[])
    fn, err = h.compile_file(fname)
    assert fn is not None, err
    raw = (ADDON_DIR / fname).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM breaks loadstring and the client"
    first = raw.decode("utf-8").splitlines()[0].strip()
    assert first == "local ADDON, ns = ...", fname


@pytest.mark.parametrize("fname", [f for f in ADDON_LUA if not f.startswith("Sets/")])
def test_no_lua52_constructs_or_bit_library(fname):
    src = (ADDON_DIR / fname).read_text(encoding="utf-8")
    code = re.sub(r"--\[(=*)\[.*?\]\1\]", "", src, flags=re.S)
    code = re.sub(r"--[^\n]*", "", code)
    code = re.sub(r'"(\\.|[^"\\])*"', '""', code)
    assert not re.search(r"\bgoto\b", code) and "//" not in code
    assert not re.search(r"\bbit\s*\.|\bbit32\b|\brequire\s*[(\"']", code)
    assert not re.search(r"\butf8\s*\.", code)


def test_only_allowed_globals_are_created():
    h = AddonHarness(sets=[SET], settings={"chatlog": True})
    h.set_location(1429, "Goldshire")
    h.advance(12)
    for cmd in ("status", "options", "selftest chatlog", "", "soundtracks", "journal", "import", "share"):
        h.slash(cmd)
    h.advance(10)
    allowed_fixed = {"NeedleDropperDB", "NeedleDropperCharDB",
                     "NeedleDropper_OnAddonCompartmentClick", "NeedleDropper_OnAddonCompartmentEnter",
                     "NeedleDropper_OnAddonCompartmentLeave", "SLASH_NEEDLEDROPPER1", "SLASH_NEEDLEDROPPER2",
                     "BINDING_HEADER_NEEDLEDROPPER", "BINDING_NAME_NEEDLEDROPPER_TOGGLE",
                     "BINDING_NAME_NEEDLEDROPPER_ADD_HERE", "BINDING_NAME_NEEDLEDROPPER_STOP"}
    # named frames (UISpecialFrames needs names) and the key-binding functions start with NeedleDropper
    extra = {g for g in h.new_globals() if g not in allowed_fixed and not g.startswith("NeedleDropper")}
    assert extra == set()
    assert h.g.SLASH_NEEDLEDROPPER1 == "/needle" and h.g.SLASH_NEEDLEDROPPER2 == "/nd"


# ------------------------------------------------------------- soundtracks --

def test_bundled_soundtracks_load_and_mixtape_is_the_default():
    h = AddonHarness()
    M = h.ns.Map
    assert M.loaded is True and M.invalid == 0, h.from_lua(M.problems)
    assert h.active() == ["builtin:azeroth-mixtape"]
    assert len(M.drops) == 38 and h.map_drop("zone:1429")["builtin"] is True
    assert h.call1("Sets.Ids") == ["azeroth-mixtape", "azeroth-unbound"]
    assert not h.call1("Sets.Problems")
    first = h.events()[0]
    assert first["type"] == ET.HELLO and first.get("dropId") is None


@pytest.mark.parametrize("soundtracks", [
    "garbage",
    {"Bad Id!": {"drops": []}},
    {"x": 42},
    {"x": {"name": "X", "drops": "nope"}},
    {"x": {"name": "X", "drops": ["junk", {"id": 5}, {"id": "zone:1", "kind": "zone"},
                                  {"id": "inst:1", "kind": "instance"}, {"id": "area:1", "kind": "subzone"}]}},
])
def test_malformed_saved_soundtracks_do_not_crash(soundtracks):
    db = {"schema": 2, "soundtracks": soundtracks, "active": ["user:x", "builtin:test-set"]}
    h = AddonHarness(sets=[SET], db=db, settings={"historyMode": "fresh"})
    h.set_location(1429, "Northshire Valley")
    h.advance(15)
    assert [e["dropId"] for e in h.events_of(ET.FIRE)] == ["zone:1429"]   # the valid soundtrack still plays
    for cmd in ("status", "where", "play zone:1429", "replay", "stop"):
        h.slash(cmd)


def test_unusable_drops_of_your_own_are_skipped_and_counted():
    db = user_db([{"id": "zone:1436", "kind": "zone", "mapID": 1436, "title": "T", "artist": "A"}],
                 active=["user:test", "builtin:test-set"])
    db["soundtracks"]["test"]["drops"] += [{"id": "zone:9", "kind": "zone"}, {"id": "inst:1", "kind": "instance"}]
    h = AddonHarness(sets=[SET], db=db, settings={"debug": True})
    M = h.ns.Map
    assert M.invalid == 2 and sorted(h.map_ids()) == ["area:87", "zone:1429", "zone:1436"]
    assert any("could not be used" in line for line in h.prints())
    assert any("2 unusable" in line for line in h.slash("status"))


# --------------------------------------------------------------- secrets --

def test_secret_chat_and_info_payloads_are_never_touched():
    h = AddonHarness(sets=[SET], settings={"chatlog": True})
    h.slash("selftest discovery")
    secret_msg = h.shim.MakeSecret("chat")
    secret_type = h.shim.MakeSecret("errorType")
    secret_text = h.shim.MakeSecret("text")
    h.fire("CHAT_MSG_SYSTEM", secret_msg, "", "", "")
    h.fire("UI_INFO_MESSAGE", secret_type, secret_text)
    h.fire("UI_INFO_MESSAGE", 372, secret_text)            # a real discovery with a secret message
    assert h.shim.RunChatFilters("CHAT_MSG_SYSTEM", secret_msg) is False
    assert h.from_lua(h.log.secretTouches) == {}
    assert h.errors() == []
    # the discovery still counts, named after the current subzone instead of the message
    assert h.ns.Discovery.last is not None


def test_our_own_chat_lines_are_never_a_discovery():
    h = AddonHarness(sets=[SET], settings={"chatlog": True})
    h.fire("CHAT_MSG_SYSTEM", "NDROP2 TiEAAAAMbw")
    h.fire("CHAT_MSG_SYSTEM", "NDPAD 3 ....")
    assert h.ns.Discovery.last is None


def test_secret_location_values_are_ignored():
    h = AddonHarness(sets=[SET])
    h.lua("""
      local real = GetSubZoneText
      GetSubZoneText = function() return SHIM.MakeSecret("sub") end
      C_Map.GetBestMapForUnit = function() return SHIM.MakeSecret("map") end
    """)
    h.set_location(1429, "Goldshire")
    h.advance(12)
    h.slash("where")
    assert h.from_lua(h.log.secretTouches) == {}


def test_missing_optional_apis_do_not_break_the_addon():
    def strip(hh):
        hh.lua("""
          issecretvalue = nil; C_MapExplorationInfo = nil; C_RestrictedActions = nil; C_ChatInfo = nil
          PixelUtil = nil; C_Sound = nil; GetGameMessageInfo = nil; ChatFrameUtil = nil
          C_CVar = nil; C_AddOns = nil; Settings = nil; UIFrameFadeIn = nil; C_EncodingUtil = nil
        """)
    h = AddonHarness(sets=[SET], before_load=strip, settings={"chatlog": True})
    h.set_location(1429, "Goldshire")
    h.discover("Goldshire")
    h.advance(12)
    assert h.events_of(ET.FIRE)
    for cmd in ("status", "where", "options", "selftest chatlog", "selftest beacon", "selftest discovery",
                "selftest share", "add", "help"):
        h.slash(cmd)
    h.advance(10)


# ------------------------------------------------------- saved variables --

def test_saved_variables_are_repaired_and_merged():
    db = {"schema": "x", "settings": {"dwellSec": "ten", "cooldownMin": 999, "historyMode": "weird",
                                      "beaconCellPx": 4.6, "toast": False, "unknownKey": 5},
          "accountDone": {"zone:1": True, "zone:2": 7, "zone:3": {"how": 3}}, "savedCVars": "bad",
          "lastSeq": 900}
    char = {"done": "nope", "scanned": {"zone:1": True, "zone:2": "yes"},
            "journal": [{"id": "zone:1", "t": 1}, "junk", {"t": 3}], "lastFireAt": "soon", "playing": "junk"}
    h = AddonHarness(sets=[SET], db=db, char_db=char)
    a = h.account_db()
    s = a["settings"]
    assert a["schema"] == 2                           # a table without a schema number is v1: migrated
    assert s["dwellSec"] == 10 and s["cooldownMin"] == 240 and s["historyMode"] == "respect"
    assert s["beaconCellPx"] == 4 and s["toast"] is False and s["unknownKey"] == 5   # 4.6 -> 5 -> 2..4
    assert s["chatlog"] is False and s["maxDropSec"] == 300 and s["duckWithoutApp"] is False
    assert s["beaconCorner"] == "topleft"
    assert a["accountDone"] == {"zone:1": {"t": 0, "how": "played"}, "zone:3": {"t": 0, "how": "played"}}
    assert "savedCVars" not in a
    assert a["lastSeq"] == 1                         # reset to 0, then the login HELLO
    assert a["active"] == ["builtin:test-set"]       # v1.1 kept no list: the default soundtrack
    c = h.char_db()
    assert c["schema"] == 2 and c["done"] == {}
    assert c["scanned"]["zone:1"] is True and "zone:2" not in c["scanned"]
    assert c["journal"] == [{"id": "zone:1", "t": 1}]
    assert c["lastFireAt"] == 0 and "playing" not in c


def test_v11_beacon_cell_size_four_becomes_three():
    h = AddonHarness(sets=[SET], db={"schema": 1, "settings": {"beaconCellPx": 4}})
    assert h.settings()["beaconCellPx"] == 3 and h.account_db()["schema"] == 2
    h = AddonHarness(sets=[SET], db={"schema": 1, "settings": {"beaconCellPx": 8}})
    assert h.settings()["beaconCellPx"] == 4


def test_fresh_install_creates_saved_variables():
    h = AddonHarness(sets=[SET])
    a, c = h.account_db(), h.char_db()
    assert a["schema"] == 2 and a["settings"]["dwellSec"] == 10 and a["accountDone"] == {}
    assert a["active"] == ["builtin:test-set"] and a["soundtracks"] in ({}, [])
    assert set(c) >= {"schema", "done", "scanned", "journal", "lastFireAt"}


def test_first_login_with_1_2_prints_one_welcome_line():
    h = AddonHarness()
    welcome = [line for line in h.prints() if line.startswith("NeedleDropper 1.2:")]
    assert welcome == ["NeedleDropper 1.2: your soundtrack is Azeroth Mixtape. "
                       "Type /needle to change a song or pick another soundtrack."]
    # an account upgraded from 1.1 gets it too; the next login (schema 2 already) does not
    up = AddonHarness(db={"schema": 1, "settings": {}, "accountDone": {}})
    assert any(line.startswith("NeedleDropper 1.2:") for line in up.prints())
    again = AddonHarness(db=up.account_db(), char_db=up.char_db())
    assert not any(line.startswith("NeedleDropper 1.2:") for line in again.prints())


# ---------------------------------------------------------------- slash --

ALL_COMMANDS = ["help", "status", "where", "journal print", "journal print 3", "replay", "play zone:1429", "test",
                "toast", "toast preview", "toast off", "toast on", "toast sideways", "stop", "skip",
                "reset char", "reset account", "reset all", "beacon off", "beacon on", "chatlog on",
                "chatlog off", "selftest", "selftest beacon", "selftest chatlog", "selftest discovery",
                "selftest music", "selftest share", "debug on", "debug off", "settings", "set dwellSec 12",
                "set toastY 30%", "set nonsense 1", "set dwellSec", "play", "unknowncmd", "add Toto - Africa",
                "add not a song", "share nobody", "set beaconCorner bottomleft"]


def test_every_slash_command_runs_and_answers():
    h = AddonHarness(sets=[SET])
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    for cmd in ALL_COMMANDS:
        out = h.slash(cmd)
        assert out, f"/needle {cmd} printed nothing"
        assert all(line.startswith("NeedleDropper:") for line in out), out
        h.advance(1)
    assert h.settings()["dwellSec"] == 12 and h.settings()["beaconCorner"] == "bottomleft"
    assert h.settings()["toastY"] == pytest.approx(0.30) and h.settings()["toast"] is True


def test_window_commands_open_the_window_on_their_tab():
    h = AddonHarness(sets=[SET])
    frame = lambda: h.g.NeedleDropperFrame  # noqa: E731 - built on first open
    assert h.slash("") == [] and frame().IsShown(frame())
    h.slash("/nd")                                     # bare /nd closes it again
    assert not frame().IsShown(frame())
    for cmd, key in (("soundtracks", "soundtracks"), ("journal", "journal")):
        h.slash(cmd)
        assert h.ns.UI._W.CurrentTab().key == key
    h.slash("import")
    assert h.g.NeedleDropperImportDialog.IsShown(h.g.NeedleDropperImportDialog)
    h.slash("options")
    assert h.from_lua(h.log.openedCategory), "options panel did not open"


def test_add_with_text_makes_a_drop_where_you_stand():
    h = AddonHarness(sets=[SET], location=(1436, ""))
    out = h.slash("add Toto - Africa")
    assert any("Added Westfall: Toto" in line and "My Drops" in line for line in out), out
    d = next(x for x in h.user_set("my-drops")["drops"] if x["id"] == "zone:1436")
    assert (d["artist"], d["title"]) == ("Toto", "Africa")
    assert h.active()[0] == "user:my-drops"
    out = h.slash("add https://open.spotify.com/album/1A2B3C4D5E6F7G8H9I0J1K")
    assert any("album or playlist" in line for line in out)


def test_share_by_name_opens_the_share_dialog():
    h = AddonHarness(sets=[SET], location=(1436, ""))
    h.slash("add Toto - Africa")
    h.slash("share my drops")
    f = h.g.NeedleDropperShareDialog
    assert f.IsShown(f)
    assert h.ns.UI._W.Share.ref == "user:my-drops"
    assert h.ns.UI._W.Share.code.startswith("!NeedleDropper:1!")


def test_import_offers_the_apps_converted_soundtracks_once():
    from v12_model_harness import ref_encode
    pack = {"format": "needledropper-pack", "version": 1, "id": "old-pack", "name": "Old Pack", "revision": 2,
            "drops": [{"zone": 1436, "artist": "Toto", "title": "Africa"}]}
    data = {"schema": 1, "appVersion": "1.2.0", "written": 1762365000,
            "imports": [{"code": ref_encode(pack), "active": True}]}
    h = AddonHarness(sets=[SET], companion_data=data)
    assert any("1 soundtrack from your NeedleDropper app is ready to import" in line for line in h.prints())
    h.slash("import")
    f = h.g.NeedleDropperImportDialog
    assert f.IsShown(f)
    assert h.ns.UI._W.Import.pack.name == "Old Pack"
    assert h.account_db()["importsHandled"] == {"old-pack@2": True}
    f.Hide(f)
    h.slash("import")                                  # offered once: now a blank import dialog
    assert h.ns.UI._W.Import.lastText is None


def test_help_lists_every_subcommand():
    h = AddonHarness(sets=[SET])
    text = "\n".join(h.slash("help"))
    for cmd in ["add", "soundtracks", "journal", "import", "share", "status", "where", "replay", "play", "test",
                "toast", "stop", "skip", "reset", "beacon", "chatlog", "selftest", "debug", "set", "settings",
                "options"]:
        assert f"/needle {cmd}" in text


def test_slash_aliases_and_prefix_color():
    h = AddonHarness(sets=[SET])
    h.g.SlashCmdList["NEEDLEDROPPER"]("status")
    raw = h.prints(raw=True)[-1]
    assert raw.startswith("|cff9fd3ffNeedleDropper|r:")


def test_where_reports_keys_for_pack_authoring():
    h = AddonHarness(sets=[SET], state={"exploredAreas": {1429: [87, 12]}})
    h.set_location(9001, "Goldshire")
    text = "\n".join(h.slash("where"))
    assert "best map: 9001" in text and "zone:1429" in text
    assert "sub:1429:goldshire" in text and "area:87" in text
    assert "87 Goldshire" in text


def test_where_in_instance():
    h = AddonHarness(sets=[SET])
    h.set_location(291, "", instance={"id": 36, "name": "The Deadmines"})
    text = "\n".join(h.slash("where"))
    assert "inst:36" in text and "The Deadmines" in text


def test_journal_print_lists_heard_drops():
    h = AddonHarness(sets=[SET])
    assert any("empty" in line for line in h.slash("journal print"))
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    text = "\n".join(h.slash("journal print"))
    assert "Elwynn Forest" in text and "Low Roar" in text and "zone:1429" in text


def test_set_validates_values():
    h = AddonHarness(sets=[SET])
    assert any("must be on or off" in line for line in h.slash("set toast maybe"))
    assert any("must be one of" in line for line in h.slash("set scope galaxy"))
    h.slash("set SCOPE account")
    assert h.settings()["scope"] == "account"
    h.slash("set cooldownMin -5")
    assert h.settings()["cooldownMin"] == 0
    h.slash("set beaconCellPx 9")
    assert h.settings()["beaconCellPx"] == 4
    assert any("must be one of" in line for line in h.slash("set beaconCorner middle"))


def test_debug_messages_only_when_enabled():
    h = AddonHarness(sets=[SET])
    h.set_location(1429, "Northshire Valley")
    assert not any("[debug]" in line for line in h.prints())
    h.slash("debug on")
    h.set_location(1436, "")
    assert any("[debug]" in line for line in h.prints())


# ------------------------------------------------------------ compartment --

def test_compartment_left_click_toggles_the_window_and_right_click_opens_settings():
    h = AddonHarness(sets=[SET])
    click = h.g.NeedleDropper_OnAddonCompartmentClick
    click("NeedleDropper", "LeftButton")
    assert h.g.NeedleDropperFrame.IsShown(h.g.NeedleDropperFrame)
    click("NeedleDropper", "LeftButton")
    assert not h.g.NeedleDropperFrame.IsShown(h.g.NeedleDropperFrame)
    click("NeedleDropper", "RightButton")
    assert h.from_lua(h.log.openedCategory) == [h.shim.settings.registered.ID]


def tooltip_lines(h):
    return [str(x) for x in h.lua("return SHIM.ui.TooltipLines()").values()]


def test_compartment_tooltip_shows_playback_soundtracks_and_app_status():
    h = AddonHarness(sets=[SET], settings={"historyMode": "fresh"})
    button = h.lua('return CreateFrame("Button", "SomeCompartmentEntry", UIParent)')
    h.g.NeedleDropper_OnAddonCompartmentEnter("NeedleDropper", button)
    lines = tooltip_lines(h)
    assert lines[0] == "NeedleDropper"
    assert "Soundtracks: Test Set" in lines
    assert "NeedleDropper app not found: Spotify and YouTube songs need it" in lines
    assert lines[-1] == "Left-click: open · Right-click: settings"
    assert h.same(h.lua("return GameTooltip:GetOwner()"), button) and h.lua("return GameTooltip:IsShown()")
    h.g.NeedleDropper_OnAddonCompartmentLeave("NeedleDropper", button)
    assert not h.lua("return GameTooltip:IsShown()")
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    h.g.NeedleDropper_OnAddonCompartmentEnter("NeedleDropper", button)
    lines = tooltip_lines(h)
    assert any(line.startswith("Playing: Elwynn Forest") and "Low Roar" in line for line in lines), lines


# -------------------------------------------------------------- options --

def panel(h):
    return [{"kind": e.kind, "variable": e.variable, "name": e.name, "buttonText": e.buttonText, "click": e.click}
            for e in h.shim.settings.elements.values()]


def test_options_panel_registers_every_shown_setting_and_opens():
    h = AddonHarness(sets=[SET])
    settings_api = h.shim.settings
    controls = h.from_lua(settings_api.controls)
    shown = [m for m in h.from_lua(h.ns.SETTINGS) if not m.get("advanced")]
    assert len(controls) == len(shown) and len(shown) == len(h.from_lua(h.ns.SETTINGS)) - 1
    variables = {c[2].variable for c in settings_api.controls.values()}
    assert variables == {"NeedleDropper_" + m["key"] for m in shown}
    assert "NeedleDropper_beaconCorner" not in variables
    kinds = {c[0] for c in controls}
    assert kinds == {"checkbox", "slider", "dropdown"}
    assert settings_api.registered is not None
    h.slash("options")
    cat_id = settings_api.registered.ID
    assert h.from_lua(h.log.openedCategory) == [cat_id]


def test_options_panel_starts_with_open_and_has_a_test_signal_button():
    h = AddonHarness(sets=[SET])
    els = panel(h)
    assert els[0]["kind"] == "button" and els[0]["buttonText"] == "Open NeedleDropper"
    els[0]["click"]()
    assert h.g.NeedleDropperFrame.IsShown(h.g.NeedleDropperFrame)
    order = [(e["kind"], e["variable"] or e["buttonText"] or e["name"]) for e in els]
    i = order.index(("header", "Companion link"))
    assert order[i + 1:i + 6] == [("checkbox", "NeedleDropper_beacon"), ("slider", "NeedleDropper_beaconCellPx"),
                                  ("checkbox", "NeedleDropper_chatlog"), ("slider", "NeedleDropper_chatlogPadBytes"),
                                  ("button", "Send a test signal")]
    cell = next(c for c in h.shim.settings.controls.values() if c[2].variable == "NeedleDropper_beaconCellPx")
    assert (cell[3].min, cell[3].max, cell[3].step) == (2, 4, 1) and cell[2].default == 3
    playback = order.index(("checkbox", "NeedleDropper_duckWithoutApp"))
    assert order[playback - 1] == ("checkbox", "NeedleDropper_kickZoneMusic")
    h.set_setting("beacon", False)
    mark = h.mark()
    els[order.index(("button", "Send a test signal"))]["click"]()
    ping = h.events_since(mark)
    assert [e["type"] for e in ping] == [ET.PING] and ping[0]["flags"] & int(Flags.TEST)
    assert h.beacon_shown()                           # shown for the signal although the beacon is off
    assert any("notification within 2 seconds" in line for line in h.prints())
    h.advance(6)
    assert not h.beacon_shown()


def test_options_panel_changes_notify_modules():
    h = AddonHarness(sets=[SET])
    beacon = h.shim.settings.settings["NeedleDropper_beacon"]
    assert h.beacon_shown()
    beacon.SetValue(beacon, False)
    assert not h.beacon_shown() and h.settings()["beacon"] is False
    hist = h.shim.settings.settings["NeedleDropper_historyMode"]
    assert hist.GetValue(hist) == "respect"
    dd = [c for c in h.from_lua(h.shim.settings.controls) if c[0] == "dropdown"]
    assert dd


def test_options_fallback_without_settings_api():
    h = AddonHarness(sets=[SET], before_load=lambda hh: hh.shim.RemoveSettingsAPI())
    out = h.slash("options")
    assert any("/needle set" in line for line in out)


# ----------------------------------------------------------- self-tests --

def test_discovery_selftest_prints_for_120_seconds():
    h = AddonHarness(sets=[SET])
    h.slash("selftest discovery")
    h.set_location(1429, "Goldshire", settle=False)
    h.discover("Goldshire", xp=40)
    text = "\n".join(h.prints())
    assert "UI_INFO_MESSAGE ERR_ZONE_EXPLORED_XP: Discovered Goldshire: 40 experience gained" in text
    assert "MAP_EXPLORATION_UPDATED" in text and "ZONE_CHANGED_NEW_AREA" in text
    assert "best map 1429 -> zone 1429, subzone 'Goldshire'" in text
    h.advance(121)
    n = len(h.prints())
    h.discover("Goldshire")
    assert len(h.prints()) == n


def test_share_selftest_checks_the_clients_zlib():
    h = AddonHarness(sets=[SET])
    out = h.slash("selftest share")
    text = "\n".join(out)
    assert "first byte is 0x78 (OK, zlib)" in text
    assert "decodes: OK" in text and "round trip: OK" in text
    assert out[-1] == "NeedleDropper: share self-test passed: share codes work on this client."


def test_share_selftest_reports_a_broken_zlib():
    h = AddonHarness(sets=[SET], fail_mode="nil", before_load=lambda hh: hh.lua(
        "SHIM_ENCODING_BRIDGE.decompress = function() return nil end"))
    out = h.slash("selftest share")
    assert out[-1].endswith("share self-test FAILED: please report the lines above.")
    assert any("reference code did not decode" in line for line in out)


def test_beacon_selftest_reports_the_v2_geometry_and_shows_the_largest_strip():
    """DESIGN-v1.2 6.4 step 2: a PING with the TEST flag, then the largest song strip for 10 s
    (so the app's diag can decode it on the real screen), and the measured geometry in chat."""
    from needledropper import protocol2 as p2
    from needledropper.transport import beacon2
    h = AddonHarness(sets=[SET], settings={"beacon": False})
    h.art("PlayerFrame", topPx=14)
    out = h.slash("selftest beacon")
    text = "\n".join(out)
    ping = h.last_event()
    assert ping["type"] == ET.PING and ping["flags"] & int(Flags.TEST)
    assert h.beacon_decoded().type == ET.PING                       # the test signal first
    assert "gap above your player frame: 14.0 px (measured from PlayerFrame.PlayerFrameContainer.FrameTexture)" in text
    assert "cells: 3 px drawn (setting 3 px); a song strip may use 3 rows; this strip: 96x3 px (1 row)" in text
    # 122 bytes: with the 4-byte frame check (F1) the title trim lands on a trailing space, which the
    # text cleaning drops, so the 123-byte cap is not reached exactly; the strip is still 336 x 9
    assert "largest song strip (shown for 10 s after the PING): 336x9 px (3 rows x 112 columns of 3 px, 122 bytes)" in text
    assert "the strip clears your player frame." in text
    mark = h.mark()
    h.advance(1.1)
    sample = h.beacon_decoded()
    st = h.beacon_status()
    assert (st["size"], st["rows"], st["cols"]) == ("336x9", 3, 112)
    # a FIRE of the test drop that nothing would play: TEST + LOCAL_PLAYBACK, text only, no ids
    assert sample.type == ET.FIRE and sample.drop_id == "test" and sample.seq == ping["seq"]
    assert sample.has(Flags.TEST) and sample.has(Flags.LOCAL_PLAYBACK)
    assert sample.spotify_id is None and sample.youtube_id is None and sample.artist == "NeedleDropper"
    buf, w, hh, stride = h.beacon_bgra(scale=1.25, width=640, height=60)
    assert beacon2.locate_and_decode(buf, w, hh, stride).frame == sample           # 125 % Windows scaling
    assert p2.frame_from_event(st["current"]) == sample
    assert h.events_since(mark) == [] and not h.chat_lines()       # never emitted, never in the chat log
    assert h.beacon_shown()                                        # forced on although the beacon is off
    h.advance(10)
    assert h.beacon_decoded().type == ET.HELLO                     # back to the steady strip
    h.advance(1.5)
    assert not h.beacon_shown()


def test_beacon_selftest_strip_gives_way_to_real_events():
    h = AddonHarness(sets=[SET], settings={"historyMode": "fresh"})
    h.slash("selftest beacon")
    h.advance(1.1)
    assert h.beacon_decoded().drop_id == "test"
    h.set_location(1429, "Northshire Valley")     # a real ARM replaces the sample at once
    got = h.beacon_decoded()
    assert (got.type, got.drop_id) == (ET.ARM, "zone:1429")


def test_beacon_selftest_waits_while_a_drop_plays():
    h = AddonHarness(sets=[SET])
    h.slash("play zone:1429")
    mark = h.mark()
    out = h.slash("selftest beacon")
    assert any("stop it first" in line for line in out) and h.events_since(mark) == []


def test_beacon_selftest_warns_when_the_strip_overlaps_the_player_frame():
    h = AddonHarness(sets=[SET])
    h.art("PlayerFrame", topPx=3)
    out = h.slash("selftest beacon")
    assert any("warning: the beacon overlaps your player frame: move the frame in Edit Mode or set beaconCorner"
               in line for line in out), out


def test_format_to_pattern_handles_magic_and_positional_specs():
    h = AddonHarness(sets=[SET], login=False)
    f2p = h.ns.Discovery.FormatToPattern
    assert f2p("Discovered: %s") == "^Discovered: (.+)$"
    pat = f2p("Entdeckt (%1$s): %2$d Erfahrung. 100%%")
    assert h.lua("return string.match")("Entdeckt (Goldhain): 50 Erfahrung. 100%", pat) == "Goldhain"
