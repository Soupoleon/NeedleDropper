"""Static checks over A3's protocol-cutover files (CONTRACTS-v1.2 1, 2.8; DESIGN-v1.2 6.2/6.3).

* nothing in Protocol2.lua / Emit.lua / Beacon.lua / ChatLink.lua references the v1 ``ns.Protocol``
  (or its v1-only names), and Protocol2 exposes what v1 callers used (HasFlag, PadLine,
  PadLineCount, FLAG, EV, EV_NAME);
* each file starts ``local ADDON, ns = ...``, compiles under Lua 5.1 and uses no Lua 5.2+ constructs;
* the files create no globals but the beacon frame's name, and register only events the client has;
* the companion side imports nothing from the v1 beacon / map machinery.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from addon_harness import ADDON_DIR, ROOT  # noqa: E402
from test_integration_static import scan_problems, tokenize_lua  # noqa: E402
from v12_beacon_harness import BeaconHarness  # noqa: E402

lua51 = pytest.importorskip("lupa.lua51")

A3_FILES = ["Protocol2.lua", "Emit.lua", "Beacon.lua", "ChatLink.lua"]
V1_PROTOCOL = re.compile(r"\bns\s*\.\s*Protocol\b(?!2)|\bns\s*\[\s*[\"']Protocol[\"']\s*\]")
V1_NAMES = re.compile(r"\b(?:TOTAL_CELLS|PAYLOAD_CELLS|EncodeBytes|Map\s*\.\s*hash)\b")


@pytest.mark.parametrize("fname", A3_FILES)
def test_no_v1_protocol_references(fname):
    code, _strings = tokenize_lua((ADDON_DIR / fname).read_text(encoding="utf-8"))
    assert not V1_PROTOCOL.search(code), f"{fname} still uses v1 ns.Protocol"
    assert not V1_NAMES.search(code), f"{fname} uses a v1-only protocol name"


@pytest.mark.parametrize("fname", A3_FILES)
def test_file_header_and_lua51(fname):
    raw = (ADDON_DIR / fname).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    src = raw.decode("utf-8")
    code, _ = tokenize_lua(src)
    assert next(line.strip() for line in code.splitlines() if line.strip()) == "local ADDON, ns = ..."
    assert src.splitlines()[0] == "local ADDON, ns = ..."
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    ok = rt.execute("return function(s, n) return loadstring(s, n) ~= nil end")(src, "@" + fname)
    assert ok, f"{fname} does not compile under Lua 5.1"
    assert scan_problems(src) == []


def test_protocol2_exposes_what_v1_callers_need():
    h = BeaconHarness()
    P = h.ns.Protocol2
    for name in ("HasFlag", "PadLine", "PadLineCount", "FormatChatLine", "EncodeCells", "EncodeFrame",
                 "Capacity", "RowsCap", "AdaptiveLayout"):
        assert callable(P[name]), name
    assert dict(P.EV.items()) == {"NONE": 0, "HELLO": 1, "ARM": 2, "FIRE": 3, "CANCEL": 4, "PING": 5}
    assert [P.EV_NAME[i] for i in range(6)] == ["NONE", "HELLO", "ARM", "FIRE", "CANCEL", "PING"]
    assert dict(P.FLAG.items()) == {"IN_COMBAT": 1, "IN_INSTANCE": 2, "ON_TAXI": 4, "DEAD": 8, "LOCAL_PLAYBACK": 16,
                                    "DROP_ACTIVE": 32, "TEST": 64}
    assert h.ns.Protocol is None  # nothing loaded here needs v1


def test_a3_files_create_no_globals_and_register_known_events():
    h = BeaconHarness(settings={"chatlog": True})
    h.emit(1)
    base = BeaconHarness(settings={"chatlog": True}, files=["Protocol2.lua", "Core.lua"])
    assert h.new_globals() - base.new_globals() == {"NeedleDropperBeacon"}
    assert h.from_lua(h.log.unknownEvents) == {}
    assert h.from_lua(h.ns.unsupportedEvents) == {}


def test_forbidden_ui_calls_are_absent():
    """DESIGN 4.9 / 6.3 static rules that apply to these files too."""
    bad = re.compile(r"\b(?:UIDropDownMenu\w*|EasyMenu|SetPropagateKeyboardInput|EnableKeyboard|CopyToClipboard|"
                     r"ShowUIPanel|HideUIPanel|loadstring|RunScript|InterfaceOptions_\w*|LoadAddOn|"
                     r"SetRoundLayoutToNearestPixel)\b")
    for fname in A3_FILES:
        code, _ = tokenize_lua((ADDON_DIR / fname).read_text(encoding="utf-8"))
        assert not bad.search(code), (fname, bad.search(code).group(0))


def test_companion_protocol_modules_do_not_depend_on_the_v1_map():
    for rel in ("companion/needledropper/protocol2.py", "companion/needledropper/transport/beacon2.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "map_hash" not in src and "mapgen" not in src, rel
        imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M)
        assert all(not m.endswith(("mapgen", "zones", "engine")) for m in imports), (rel, imports)
