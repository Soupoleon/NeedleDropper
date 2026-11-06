"""Static checks on the window's source (DESIGN-v1.2 §4.9, §4.10, §6.3): Lua 5.1 only, the forbidden
APIs, the template allowlist, GameFont* objects, where pcall is allowed, and Bindings.xml."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from addon_harness import ADDON_DIR, toc_files
from test_integration_static import FORBIDDEN_CODE, LUA51_ESCAPES, _ESCAPE, tokenize_lua

lua51 = pytest.importorskip("lupa.lua51")

UI_DIR = ADDON_DIR / "UI"
UI_FILES = sorted(p.name for p in UI_DIR.glob("*.lua"))
EXPECTED = ["Drops.lua", "Journal.lua", "ShareDialogs.lua", "Soundtracks.lua", "Window.lua"]

ALLOWED_TEMPLATES = {
    "ButtonFrameTemplate": "Frame", "PanelTabButtonTemplate": "Button", "PanelResizeButtonTemplate": "Button",
    "WowScrollBoxList": "Frame", "MinimalScrollBar": "EventFrame", "SearchBoxTemplate": "EditBox",
    "InputBoxInstructionsTemplate": "EditBox", "ScrollingEditBoxTemplate": "Frame",
    "WowStyle1DropdownTemplate": "DropdownButton", "WowStyle1FilterDropdownTemplate": "DropdownButton",
    "UICheckButtonTemplate": "CheckButton", "UIRadioButtonTemplate": "CheckButton",
    "UIPanelButtonTemplate": "Button", "MinimalSliderWithSteppersTemplate": "Frame", "UIPanelCloseButton": "Button",
}

# DESIGN §4.9 / §6.3; checked in code AND string literals (script names are strings), not comments.
FORBIDDEN_API = [
    "UIDropDownMenu", "EasyMenu", "EnableKeyboard", "OnKeyDown", "OnKeyUp", "SetPropagateKeyboardInput",
    "CopyToClipboard", "ShowUIPanel", "HideUIPanel", "UIPanelWindows", "loadstring", "RunScript",
    "InterfaceOptions_", "string.upper", ":upper(",
]

# GameFont* objects from Blizzard_Fonts_Shared/Shared/FontStyles.xml; nothing smaller than
# GameFontHighlightSmall (DESIGN §4.10).
FONTS = {"GameFontNormal", "GameFontHighlight", "GameFontDisable", "GameFontNormalSmall", "GameFontHighlightSmall",
         "GameFontDisableSmall", "GameFontRedSmall", "GameFontGreenSmall", "GameFontNormalLarge",
         "GameFontHighlightLarge"}

# pcall only around optional / uncertain APIs (task rules; DESIGN §4.9 for the two click-only calls).
PCALL_TARGETS = {"Settings.OpenToCategory", "ReloadUI", "C_Map.GetPlayerMapPosition",
                 "C_MapExplorationInfo.GetExploredAreaIDsAtPosition", "C_Map.GetAreaInfo",
                 "C_UIFileAsset.IsKnownFile",
                 # Forever's gamepad UI opt-in (UI/Window.lua, real-client finding 7): optional APIs
                 # that may be missing or refuse in combat.
                 "InputUtil.IsGamepadUIEnabled", "InCombatLockdown", "C_RestrictedActions.IsAddOnRestrictionActive",
                 "GamepadMode.FrameControlsManager.FrameShown", "GamepadMode.FrameControlsManager.FrameHidden",
                 "SmartNavigation.RefreshButtonGroups"}


def _source(name):
    return (UI_DIR / name).read_text(encoding="utf-8")


def _code_and_strings(src):
    code, strings = tokenize_lua(src)
    return code, strings


def _without_comments(src):
    """Source with comments removed but string literals kept."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("--", i):
            m = re.compile(r"\[(=*)\[").match(src, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                i = src.find(close, m.end()) + len(close)
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j
            continue
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def test_ui_folder_holds_exactly_the_five_files():
    assert UI_FILES == EXPECTED


@pytest.mark.parametrize("name", EXPECTED)
def test_lua51_only(name):
    raw = (UI_DIR / name).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    src = raw.decode("utf-8")
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    res = rt.execute("return function(s, n) local f, e = loadstring(s, n); return {f ~= nil, e} end")(src, "@" + name)
    assert res[1], f"{name} does not compile under Lua 5.1: {res[2]}"
    code, strings = _code_and_strings(src)
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first == "local ADDON, ns = ..."
    problems = []
    for lineno, line in enumerate(code.splitlines(), 1):
        for rx, what in FORBIDDEN_CODE:
            if rx.search(line):
                problems.append(f"{name}:{lineno}: {what}: {line.strip()}")
    for s in strings:
        for m in _ESCAPE.finditer(s):
            if m.group(1) not in LUA51_ESCAPES:
                problems.append(f"{name}: string escape \\{m.group(1)}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("name", EXPECTED)
def test_no_forbidden_api(name):
    text = _without_comments(_source(name))
    hits = [api for api in FORBIDDEN_API if api in text]
    assert hits == [], f"{name}: {hits}"
    assert not re.search(r"Secure\w*Template", text), name
    assert not re.search(r"(?<![\w.:])LoadAddOn\s*\(", text), name
    assert not re.search(r"\bbit\.", text), name


def test_no_forbidden_api_in_the_whole_window_including_bindings():
    xml = (ADDON_DIR / "Bindings.xml").read_text(encoding="utf-8")
    body = re.sub(r"<!--.*?-->", "", xml, flags=re.S)
    for api in FORBIDDEN_API:
        assert api not in body


@pytest.mark.parametrize("name", EXPECTED)
def test_create_frame_templates_are_allowlisted(name):
    text = _without_comments(_source(name))
    calls = re.findall(r'CreateFrame\(\s*"(\w+)"\s*,[^,()]*,[^,()]*,\s*"([^"]+)"', text)
    for frame_type, template in calls:
        for t in [x.strip() for x in template.split(",")]:
            assert t in ALLOWED_TEMPLATES, f"{name}: {t}"
            assert ALLOWED_TEMPLATES[t] == frame_type, f"{name}: {t} is a {ALLOWED_TEMPLATES[t]}, not {frame_type}"
    # Every CreateFrame names a literal frame type; templates are literals or absent.
    for m in re.finditer(r"CreateFrame\(([^)]*)\)", text):
        args = m.group(1)
        assert args.strip().startswith('"'), f"{name}: {m.group(0)}"


@pytest.mark.parametrize("name", EXPECTED)
def test_fonts_are_game_fonts_no_smaller_than_highlight_small(name):
    text = _without_comments(_source(name))
    used = set(re.findall(r'"(GameFont\w+)"', text))
    assert used <= FONTS, f"{name}: {used - FONTS}"
    other = set(re.findall(r'"(\w*Font\w*)"', text)) - used
    assert not {o for o in other if o.endswith(("Font", "FontNormal", "Small", "Tiny"))}, other


@pytest.mark.parametrize("name", EXPECTED)
def test_pcall_only_around_optional_apis(name):
    text = _without_comments(_source(name))
    for m in re.finditer(r"pcall\(\s*([\w.]+)", text):
        assert m.group(1) in PCALL_TARGETS, f"{name}: pcall({m.group(1)}"
    assert "xpcall" not in text


def test_no_frames_are_created_at_file_load():
    """Top-level statements (outside any function) never call CreateFrame."""
    for name in EXPECTED:
        code, _ = _code_and_strings(_source(name))
        depth = 0
        for line in code.splitlines():
            stripped = line.strip()
            if depth == 0 and "CreateFrame(" in stripped and not stripped.startswith("function"):
                pytest.fail(f"{name}: CreateFrame at file level: {stripped}")
            depth += len(re.findall(r"\bfunction\b", stripped))
            depth += len(re.findall(r"\b(?:do|then)\b", stripped)) - len(re.findall(r"\bend\b", stripped))
            depth += len(re.findall(r"\brepeat\b", stripped)) - len(re.findall(r"\buntil\b", stripped))
            depth = max(depth, 0)


# Edit boxes hold the raw text the player edits; only FontStrings interpret "|" escapes.
EDIT_BOX_RECEIVERS = {"form.Note", "f.Code", "form.Landmark", "f.Name", "f.Author", "eb", "box", "self"}


def test_user_text_reaches_font_strings_escaped():
    """FontStrings get player / import text only through W.Esc or the escaping helpers."""
    for name in EXPECTED:
        text = _without_comments(_source(name))
        for m in re.finditer(r"([\w.]+):SetText\(([^()]*(?:\([^()]*\))?[^()]*)\)", text):
            receiver, arg = m.group(1), m.group(2)
            if receiver in EDIT_BOX_RECEIVERS:
                continue
            risky = re.search(r"\b(?:drop|d|info|set|pack|p|e|entry|match)\.(?:name|artist|title|note|label|author|"
                              r"description|subzone|homepage)\b", arg)
            if risky and "esc(" not in arg and "Format(" not in arg:
                pytest.fail(f"{name}: {receiver}:SetText({arg}) is not escaped")


def test_bindings_xml():
    path = ADDON_DIR / "Bindings.xml"
    root = ET.parse(path).getroot()
    assert root.tag == "Bindings"
    bindings = root.findall("Binding")
    assert [b.get("name") for b in bindings] == ["NEEDLEDROPPER_TOGGLE", "NEEDLEDROPPER_ADD_HERE",
                                                 "NEEDLEDROPPER_STOP"]
    window = _source("Window.lua")
    for b in bindings:
        assert b.get("category") == "NeedleDropper"
        assert b.get("default") is None                       # none bound by default
        body = (b.text or "").strip()
        fn = re.fullmatch(r"(NeedleDropper_\w+)\(\)", body)
        assert fn, body
        assert f"function {fn.group(1)}()" in window
    assert "Bindings.xml" not in toc_files()                  # loads by itself; never in the TOC
