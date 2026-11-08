"""Static and load-time hygiene of the v1.2 model files (Locale, Sets, Soundtracks, Share,
Companion): Lua 5.1 only, no locale-dependent pattern classes, no executing of data, pcall
only around optional client APIs, no stray globals; plus the Locale table itself."""
from __future__ import annotations

import re

import lupa.lua51 as lua51
import pytest

from addon_harness import ADDON_DIR, AddonHarness
from v12_model_harness import A1_FILES, ModelHarness

FORBIDDEN = ["goto", "//", "bit.", "bit32", "utf8.", "loadstring", "RunScript", "require", "setfenv", "getfenv",
             "string.upper", "strupper", ":upper(", "CopyToClipboard", "EnableKeyboard", "UIDropDownMenu",
             "EasyMenu", "ShowUIPanel", "HideUIPanel", "SetPropagateKeyboardInput", "InterfaceOptions_",
             "LoadAddOn", "dofile"]
LOCALE_CLASSES = re.compile(r"%[acdlpsuwxACDLPSUWX]")
PCALL_ALLOWED = {"C_Map.GetMapInfo", "C_Map.GetPlayerMapPosition",
                 "C_MapExplorationInfo.GetExploredAreaIDsAtPosition", "util.CompressString",
                 "util.DecompressString", "C_UIFileAsset.IsKnownFile"}


def source(fname: str) -> str:
    return (ADDON_DIR / fname).read_text(encoding="utf-8")


def strip_comments_and_strings(src: str) -> str:
    src = re.sub(r"--\[(=*)\[.*?\]\1\]", "", src, flags=re.S)
    src = re.sub(r"--[^\n]*", "", src)
    src = re.sub(r'"(\\.|[^"\\\n])*"', '""', src)
    src = re.sub(r"'(\\.|[^'\\\n])*'", "''", src)
    return src


def string_literal_at(src: str, i: int):
    """The quoted literal starting at src[i] (a quote), without the quotes, or None."""
    q = src[i]
    j = i + 1
    while j < len(src):
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == q:
            return src[i + 1:j]
        j += 1
    return None


def pattern_arguments(src: str):
    """Literal second arguments of find/match/gsub/gmatch calls (the Lua patterns)."""
    code = re.sub(r"--\[(=*)\[.*?\]\1\]", lambda m: " " * len(m.group(0)), src, flags=re.S)
    code = re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), code)
    for m in re.finditer(r"(?<![A-Za-z0-9_])(?:string\.)?(find|match|gsub|gmatch)\(", code):
        i, depth = m.end(), 0
        while i < len(code):
            c = code[i]
            if c in "\"'":
                lit = string_literal_at(code, i)
                i += len(lit or "") + 2
                continue
            if c in "({[":
                depth += 1
            elif c in ")}]":
                if depth == 0:
                    break
                depth -= 1
            elif c == "," and depth == 0:
                i += 1
                while code[i] in " \t\r\n(":
                    i += 1
                if code[i] in "\"'":
                    yield m.group(1), string_literal_at(code, i)
                break
            i += 1


@pytest.mark.parametrize("fname", A1_FILES)
def test_header_encoding_and_lua51_compile(fname):
    raw = (ADDON_DIR / fname).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8").splitlines()[0] == "local ADDON, ns = ..."
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    res = rt.execute("return function(src, name) return loadstring(src, name) end")(raw.decode("utf-8"), fname)
    fn, err = res if isinstance(res, tuple) else (res, None)
    assert fn is not None, err
    assert "\t" not in raw.decode("utf-8"), "use spaces"


@pytest.mark.parametrize("fname", A1_FILES)
def test_no_forbidden_constructs(fname):
    code = strip_comments_and_strings(source(fname))
    for word in FORBIDDEN:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", word):
            assert not re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % word, code), f"{fname}: {word}"
        else:
            assert word not in code, f"{fname}: {word}"
    assert not re.search(r"^\s*function\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", code, re.M), "global function"


@pytest.mark.parametrize("fname", A1_FILES)
def test_patterns_use_explicit_byte_ranges(fname):
    found = list(pattern_arguments(source(fname)))
    for fn, pat in found:
        assert pat is not None
        assert not LOCALE_CLASSES.search(pat.replace("%%", "")), f"{fname}: {fn} pattern {pat!r}"


def test_pattern_scan_finds_the_patterns():
    pats = [p for _, p in pattern_arguments(source("Share.lua"))]
    assert "!NeedleDropper:([0-9]+)!" in pats and any("A-Za-z0-9+/=_" in p for p in pats)


@pytest.mark.parametrize("fname", A1_FILES)
def test_pcall_only_around_optional_client_apis(fname):
    code = re.sub(r"--[^\n]*", "", re.sub(r"--\[(=*)\[.*?\]\1\]", "", source(fname), flags=re.S))
    for m in re.finditer(r"pcall\(\s*([A-Za-z_][A-Za-z0-9_.]*)", code):
        assert m.group(1) in PCALL_ALLOWED, f"{fname}: pcall({m.group(1)}"


def test_only_binding_globals_are_added():
    base = AddonHarness(files=["Locale.lua", "Json.lua", "Base64.lua", "Protocol2.lua", "Core.lua"])
    bindings = {"BINDING_HEADER_NEEDLEDROPPER", "BINDING_NAME_NEEDLEDROPPER_TOGGLE",
                "BINDING_NAME_NEEDLEDROPPER_ADD_HERE", "BINDING_NAME_NEEDLEDROPPER_STOP"}
    # Locale is a Core prerequisite; leave its intended globals in the comparison
    # so the model still has to add exactly these four bindings and nothing else.
    core_globals = base.new_globals() - bindings
    h = ModelHarness(profile="model")
    h.call("Soundtracks.Create", "Mine")
    h.call("Soundtracks.OverrideHere", "zone:1429", {"title": "x"})
    h.call("Share.Encode", h.call1("Share.FromSet", "user:my-drops"))
    h.call("Companion.Read")
    h.logout()
    extra = h.new_globals() - core_globals - {"SHIM_ENCODING_BRIDGE", "C_EncodingUtil"}
    assert extra == bindings


# -------------------------------------------------------------------- Locale --

def test_locale_table_falls_back_to_the_english_key():
    h = ModelHarness(profile="model", login=False)
    L = h.ns.L
    assert L["Add drop here"] == "Add drop here" and L["anything at all %d"] == "anything at all %d"
    assert h.ns.LOCALE == "enUS"
    assert h.g.BINDING_HEADER_NEEDLEDROPPER == "NeedleDropper"
    assert h.g.BINDING_NAME_NEEDLEDROPPER_TOGGLE == "Open or close the NeedleDropper window"
    assert h.g.BINDING_NAME_NEEDLEDROPPER_ADD_HERE == "Add a needle drop where you stand"
    assert h.g.BINDING_NAME_NEEDLEDROPPER_STOP == "Stop the needle drop"
    esc = h.ns.EscapeText
    assert esc("|cffff0000red|r |T1:0|t") == "||cffff0000red||r ||T1:0||t" and esc(None) == "" and esc("plain") == "plain"


def test_locale_overrides_win_over_the_fallback():
    def before(hh):
        hh.rt.execute("SHIM.SetLocale('deDE')")
    h = ModelHarness(profile="model", login=False, before_load=before)
    assert h.ns.LOCALE == "deDE"
    h.rt.execute("local ns = ... ns.L['Add drop here'] = 'Hier hinzufügen'", h.ns)
    assert h.ns.L["Add drop here"] == "Hier hinzufügen" and h.ns.L["Undo"] == "Undo"


def test_packager_markers_cover_every_client_language():
    src = source("Locale.lua")
    for loc in ("deDE", "esES", "esMX", "frFR", "itIT", "koKR", "ptBR", "ruRU", "zhCN", "zhTW"):
        assert f'--@localization(locale="{loc}", format="lua_additive_table", handle-unlocalized="ignore")@' in src


def test_player_facing_strings_go_through_L():
    """Messages returned to the player are ns.L lookups, not bare literals (spot check)."""
    for fname in ("Soundtracks.lua", "Share.lua", "Companion.lua"):
        code = re.sub(r"--[^\n]*", "", re.sub(r"--\[(=*)\[.*?\]\1\]", "", source(fname), flags=re.S))
        for m in re.finditer(r"return nil, \"([^\"]+)\"", code):
            assert m.group(1) in {"notcode", "toobig", "newer", "damaged", "invalid", "type", "bad", "notatrack",
                                  "playlist", "utf8", "long", "foreign:", "i\\204\\135"}, f"{fname}: bare message {m.group(1)!r}"
