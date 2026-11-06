"""Addon robustness: Lua 5.1 compile check, TOC, globals, malformed maps, secret values,
SavedVariables repair, slash commands, options panel and self-tests. (The title card has its
own module: test_addon_toast.py.)"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addon_harness import ADDON_DIR, AddonHarness, make_map, toc_files  # noqa: E402
from needledropper.models import EventType as ET  # noqa: E402

DROPS = [
    {"id": "zone:1429", "kind": "zone", "mapID": 1429, "label": "Elwynn Forest",
     "title": "I'll Keep Coming", "artist": "Low Roar", "durationMs": 60000},
    {"id": "area:87", "kind": "subzone", "mapID": 1429, "areaID": 87, "subzone": "Goldshire",
     "label": "Goldshire", "title": "Inn", "artist": "Bard", "durationMs": 30000},
]
MAP = make_map(DROPS)
MY_FILES = ["Core.lua", "Location.lua", "Discovery.lua", "Audio.lua", "Beacon.lua", "ChatLink.lua",
            "Toast.lua", "Director.lua", "Options.lua", "SelfTest.lua", "Map.lua"]


# --------------------------------------------------------------- sources --

def test_toc_matches_spec():
    expected = """## Interface: 16001
## Title: NeedleDropper
## Notes: Kojima-style needle drops: curated songs the first time you arrive somewhere.
## Author: NeedleDropper
## Version: 1.0.0
## X-License: MIT
## Category: Audio & Video
## IconTexture: Interface\\Icons\\INV_Misc_Drum_01
## SavedVariables: NeedleDropperDB
## SavedVariablesPerCharacter: NeedleDropperCharDB
## AddonCompartmentFunc: NeedleDropper_OnAddonCompartmentClick
Protocol.lua
Map.lua
Core.lua
Location.lua
Discovery.lua
Audio.lua
Beacon.lua
ChatLink.lua
Toast.lua
Director.lua
Options.lua
SelfTest.lua
"""
    assert (ADDON_DIR / "NeedleDropper.toc").read_text(encoding="utf-8") == expected
    for f in toc_files():
        assert (ADDON_DIR / f).is_file(), f


@pytest.mark.parametrize("fname", sorted(p.name for p in ADDON_DIR.glob("*.lua")))
def test_every_addon_file_compiles_under_lua51(fname):
    h = AddonHarness(map=MAP, login=False, files=[])
    fn, err = h.compile_file(fname)
    assert fn is not None, err
    raw = (ADDON_DIR / fname).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM breaks loadstring and the client"
    lines = [x.strip() for x in raw.decode("utf-8").splitlines()]
    if fname == "Map.lua":
        return  # generated data file (companion build-map); compiling is all that matters
    if fname in MY_FILES:
        assert lines[0] == "local ADDON, ns = ...", fname
    else:
        assert "local ADDON, ns = ..." in lines, fname


@pytest.mark.parametrize("fname", [f for f in MY_FILES if f != "Map.lua"])
def test_no_lua52_constructs_or_bit_library(fname):
    src = (ADDON_DIR / fname).read_text(encoding="utf-8")
    code = re.sub(r"--[^\n]*", "", src)
    code = re.sub(r'"(\\.|[^"\\])*"', '""', code)
    assert "goto" not in code and "//" not in code
    assert "bit." not in code and "bit32" not in code and "require" not in code
    assert "utf8." not in code


def test_only_allowed_globals_are_created():
    h = AddonHarness(map=MAP, settings={"chatlog": True})
    h.set_location(1429, "Goldshire")
    h.advance(12)
    h.slash("status")
    h.slash("options")
    h.slash("selftest chatlog")
    h.advance(10)
    allowed_fixed = {"NeedleDropperDB", "NeedleDropperCharDB", "NeedleDropper_Map",
                     "NeedleDropper_OnAddonCompartmentClick", "SLASH_NEEDLEDROPPER1", "SLASH_NEEDLEDROPPER2"}
    extra = {g for g in h.new_globals() if g not in allowed_fixed and not g.startswith("NeedleDropper")}
    assert extra == set()
    assert h.g.SLASH_NEEDLEDROPPER1 == "/needle" and h.g.SLASH_NEEDLEDROPPER2 == "/nd"


# ------------------------------------------------------------------- map --

def test_shipped_map_file_loads():
    """The shipped Map.lua is generated from the starter pack (SPEC 3.2): it loads with no
    malformed entries and agrees with its Map.json sidecar. (A map with no drops only ever
    emits HELLO; see test_missing_or_malformed_map_does_not_crash.)"""
    sidecar = json.loads((ADDON_DIR / "Map.json").read_text(encoding="utf-8"))
    h = AddonHarness(use_map_file=True)
    M = h.ns.Map
    assert M.loaded is True and M.invalid == 0, h.from_lua(M.problems)
    assert M.hash == sidecar["hash"] and M.hash != 1
    assert len(M.drops) == len(sidecar["drops"]) > 0
    first = h.events()[0]
    assert first["type"] == ET.HELLO and first["hash"] == sidecar["hash"]


@pytest.mark.parametrize("map_lua", [
    "NeedleDropper_Map = nil",
    "NeedleDropper_Map = 42",
    "NeedleDropper_Map = 'garbage'",
    "NeedleDropper_Map = { hash = 'x', drops = 'nope' }",
    "NeedleDropper_Map = { drops = { } }",
])
def test_missing_or_malformed_map_does_not_crash(map_lua):
    h = AddonHarness(map_lua=map_lua)
    h.set_location(1429, "Northshire Valley")
    h.advance(15)
    assert [e["type"] for e in h.events()] == [ET.HELLO]
    h.slash("status")
    h.slash("where")
    h.slash("play zone:1429")
    h.slash("replay")


def test_malformed_entries_are_skipped_and_valid_ones_work():
    lua = r"""
    NeedleDropper_Map = {
      version = 1, hash = 4242, packs = { "P", 7 },
      drops = {
        "junk",
        { n = "x", id = "zone:1" },
        { n = 2, id = 5, kind = "zone", mapID = 1436 },
        { n = 3, id = "zone:1429", kind = "zone", mapID = 1429, title = "Song", artist = "Band", durationMs = 30000 },
        { n = 3, id = "zone:1436", kind = "zone", mapID = 1436 },
        { n = 4, id = "zone:1429", kind = "zone", mapID = 1429 },
        { n = 5, id = "zone:9", kind = "zone" },
        { n = 6, id = "inst:1", kind = "instance" },
        { n = 7, id = "area:1", kind = "subzone" },
        { n = 70000, id = "zone:2", kind = "zone", mapID = 2 },
        { n = 8, id = "zone:1453", mapID = 1453, title = {}, artist = 3, durationMs = -5, label = 9 },
        [99] = { n = 9, id = "inst:36", instanceID = 36 },
        foo = "bar",
      },
    }
    """
    h = AddonHarness(map_lua=lua)
    M = h.ns.Map
    assert M.hash == 4242
    ids = sorted(d.id for d in M.drops.values())
    assert ids == ["inst:36", "zone:1429", "zone:1453"]
    assert M.invalid == 10
    assert h.ns.Map.byId["zone:1453"].title == "" and h.ns.Map.byId["zone:1453"].durationMs == 0
    assert h.ns.Map.byId["zone:1453"].label == "zone:1453"
    assert h.ns.Map.byId["inst:36"].kind == "instance"
    assert any("malformed" in line for line in h.prints())
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    fires = h.events_of(ET.FIRE)
    assert len(fires) == 1 and fires[0]["n"] == 3 and fires[0]["hash"] == 4242


def test_debug_lists_map_problems():
    h = AddonHarness(map_lua="NeedleDropper_Map = { hash = 5, drops = { 'junk' } }", settings={"debug": True})
    assert any("map: drop #1" in line for line in h.prints())


# --------------------------------------------------------------- secrets --

def test_secret_chat_and_info_payloads_are_never_touched():
    h = AddonHarness(map=MAP, settings={"chatlog": True})
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


def test_secret_location_values_are_ignored():
    h = AddonHarness(map=MAP)
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
          C_CVar = nil; C_AddOns = nil; Settings = nil; UIFrameFadeIn = nil
        """)
    h = AddonHarness(map=MAP, before_load=strip, settings={"chatlog": True})
    h.set_location(1429, "Goldshire")
    h.discover("Goldshire")
    h.advance(12)
    assert h.events_of(ET.FIRE)
    for cmd in ("status", "where", "options", "selftest chatlog", "selftest beacon", "selftest discovery"):
        h.slash(cmd)
    h.advance(10)


# ------------------------------------------------------- saved variables --

def test_saved_variables_are_repaired_and_merged():
    db = {"schema": "x", "settings": {"dwellSec": "ten", "cooldownMin": 999, "historyMode": "weird",
                                      "beaconCellPx": 4.6, "toast": False, "unknownKey": 5},
          "accountDone": {"zone:1": True, "zone:2": 7, "zone:3": {"how": 3}}, "savedCVars": "bad",
          "lastSeq": 900}
    char = {"done": "nope", "scanned": {"zone:1": True, "zone:2": "yes"},
            "journal": [{"id": "zone:1", "t": 1}, "junk", {"t": 3}], "lastFireAt": "soon"}
    h = AddonHarness(map=MAP, db=db, char_db=char)
    a = h.account_db()
    s = a["settings"]
    assert a["schema"] == 1
    assert s["dwellSec"] == 10 and s["cooldownMin"] == 240 and s["historyMode"] == "respect"
    assert s["beaconCellPx"] == 5 and s["toast"] is False and s["unknownKey"] == 5
    assert s["chatlog"] is False and s["maxDropSec"] == 300
    assert a["accountDone"] == {"zone:1": {"t": 0, "how": "played"}, "zone:3": {"t": 0, "how": "played"}}
    assert "savedCVars" not in a
    assert a["lastSeq"] == 1                         # reset to 0, then the login HELLO
    c = h.char_db()
    assert c["schema"] == 1 and c["done"] == {}
    assert c["scanned"]["zone:1"] is True and "zone:2" not in c["scanned"]
    assert c["journal"] == [{"id": "zone:1", "t": 1}]
    assert c["lastFireAt"] == 0


def test_fresh_install_creates_saved_variables():
    h = AddonHarness(map=MAP)
    a, c = h.account_db(), h.char_db()
    assert a["schema"] == 1 and a["settings"]["dwellSec"] == 10 and a["accountDone"] == {}
    assert set(c) >= {"schema", "done", "scanned", "journal", "lastFireAt"}


# ---------------------------------------------------------------- slash --

ALL_COMMANDS = ["", "help", "status", "where", "journal", "replay", "play zone:1429", "test", "toast",
                "toast preview", "toast off", "toast on", "toast sideways", "stop", "skip",
                "reset char", "reset account", "reset all", "beacon off", "beacon on", "chatlog on",
                "chatlog off", "selftest", "selftest beacon", "selftest chatlog", "selftest discovery",
                "selftest music", "debug on", "debug off", "options", "settings", "set dwellSec 12",
                "set toastY 30%", "set nonsense 1", "set dwellSec", "play", "unknowncmd"]


def test_every_slash_command_runs_and_answers():
    h = AddonHarness(map=MAP)
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    for cmd in ALL_COMMANDS:
        out = h.slash(cmd)
        if cmd == "options":
            assert h.from_lua(h.log.openedCategory), "options panel did not open"
            continue
        assert out, f"/needle {cmd} printed nothing"
        assert all(line.startswith("NeedleDropper:") for line in out), out
        h.advance(1)
    assert h.settings()["dwellSec"] == 12
    assert h.settings()["toastY"] == pytest.approx(0.30) and h.settings()["toast"] is True


def test_help_lists_every_subcommand():
    h = AddonHarness(map=MAP)
    text = "\n".join(h.slash("help"))
    for cmd in ["status", "where", "journal", "replay", "play", "test", "toast", "stop", "skip", "reset", "beacon",
                "chatlog", "selftest", "debug", "options"]:
        assert f"/needle {cmd}" in text


def test_slash_aliases_and_prefix_color():
    h = AddonHarness(map=MAP)
    h.g.SlashCmdList["NEEDLEDROPPER"]("status")
    raw = h.prints(raw=True)[-1]
    assert raw.startswith("|cff9fd3ffNeedleDropper|r:")


def test_where_reports_keys_for_pack_authoring():
    h = AddonHarness(map=MAP, state={"exploredAreas": {1429: [87, 12]}})
    h.set_location(9001, "Goldshire")
    text = "\n".join(h.slash("where"))
    assert "best map: 9001" in text and "zone:1429" in text
    assert "sub:1429:goldshire" in text and "area:87" in text
    assert "87 Goldshire" in text


def test_where_in_instance():
    h = AddonHarness(map=MAP)
    h.set_location(291, "", instance={"id": 36, "name": "The Deadmines"})
    text = "\n".join(h.slash("where"))
    assert "inst:36" in text and "The Deadmines" in text


def test_journal_lists_heard_drops():
    h = AddonHarness(map=MAP)
    assert any("empty" in line for line in h.slash("journal"))
    h.set_location(1429, "Northshire Valley")
    h.advance(11)
    text = "\n".join(h.slash("journal"))
    assert "Elwynn Forest" in text and "Low Roar" in text and "zone:1429" in text


def test_set_validates_values():
    h = AddonHarness(map=MAP)
    assert any("must be on or off" in line for line in h.slash("set toast maybe"))
    assert any("must be one of" in line for line in h.slash("set scope galaxy"))
    h.slash("set SCOPE account")
    assert h.settings()["scope"] == "account"
    h.slash("set cooldownMin -5")
    assert h.settings()["cooldownMin"] == 0


def test_debug_messages_only_when_enabled():
    h = AddonHarness(map=MAP)
    h.set_location(1429, "Northshire Valley")
    assert not any("[debug]" in line for line in h.prints())
    h.slash("debug on")
    h.set_location(1436, "")
    assert any("[debug]" in line for line in h.prints())


# -------------------------------------------------------------- options --

def test_options_panel_registers_every_setting_and_opens():
    h = AddonHarness(map=MAP)
    settings_api = h.shim.settings
    controls = h.from_lua(settings_api.controls)
    assert len(controls) == len(h.from_lua(h.ns.SETTINGS))
    kinds = {c[0] for c in controls}
    assert kinds == {"checkbox", "slider", "dropdown"}
    assert settings_api.registered is not None
    h.slash("options")
    cat_id = settings_api.registered.ID
    assert h.from_lua(h.log.openedCategory) == [cat_id]
    h.g.NeedleDropper_OnAddonCompartmentClick("NeedleDropper", "LeftButton")
    assert h.from_lua(h.log.openedCategory) == [cat_id, cat_id]


def test_options_panel_changes_notify_modules():
    h = AddonHarness(map=MAP)
    beacon = h.shim.settings.settings["NeedleDropper_beacon"]
    assert h.beacon_shown()
    beacon.SetValue(beacon, False)
    assert not h.beacon_shown() and h.settings()["beacon"] is False
    hist = h.shim.settings.settings["NeedleDropper_historyMode"]
    assert hist.GetValue(hist) == "respect"
    dd = [c for c in h.from_lua(h.shim.settings.controls) if c[0] == "dropdown"]
    assert dd


def test_options_fallback_without_settings_api():
    h = AddonHarness(map=MAP, before_load=lambda hh: hh.shim.RemoveSettingsAPI())
    out = h.slash("options")
    assert any("/needle set" in line for line in out)


# ----------------------------------------------------------- discovery --

def test_discovery_selftest_prints_for_120_seconds():
    h = AddonHarness(map=MAP)
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


def test_format_to_pattern_handles_magic_and_positional_specs():
    h = AddonHarness(map=MAP, login=False)
    f2p = h.ns.Discovery.FormatToPattern
    assert f2p("Discovered: %s") == "^Discovered: (.+)$"
    pat = f2p("Entdeckt (%1$s): %2$d Erfahrung. 100%%")
    assert h.lua("return string.match")("Entdeckt (Goldhain): 50 Erfahrung. 100%", pat) == "Goldhain"
