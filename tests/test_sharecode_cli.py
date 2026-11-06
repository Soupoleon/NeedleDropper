"""``needledropper packs validate | encode | decode | convert-legacy`` (commands_packs.py).

Everything runs in pytest temp dirs: the app dir is ``NEEDLEDROPPER_HOME`` (conftest), the WoW
folder is a temp tree named by the config's ``wowRoot`` (or ``--addons-dir``).
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
from pathlib import Path

import pytest

from needledropper import commands_packs as cp
from needledropper import gamedata as gd
from needledropper import packs, sharecode as sc

ROOT = Path(__file__).resolve().parents[1]
MIXTAPE = ROOT / "packs" / "azeroth-mixtape.json"
UNBOUND = ROOT / "packs" / "azeroth-unbound.json"


def run(*argv: str, capsys=None) -> tuple[int, str, str]:
    code = cp.main(list(argv))
    out, err = capsys.readouterr() if capsys is not None else ("", "")
    return code, out, err


def user_pack(name: str, title: str = "Africa", **extra) -> dict:
    return {"format": "needledropper-pack", "version": 1, "name": name, "author": "Me",
            "drops": [{"zone": "Elwynn Forest", "subzone": "Goldshire", "artist": "Toto", "title": title,
                       "youtube": "QAo_Ycocl1E"}], **extra}


# ------------------------------------------------------------------- encode --

def test_encode_prints_a_code_that_decodes_to_the_pack(capsys):
    code, out, err = run("encode", str(MIXTAPE), capsys=capsys)
    assert code == 0 and err == ""
    text = out.strip()
    assert text.startswith(sc.PREFIX) and text.endswith("!") and "\n" not in text
    assert sc.decode(text) == sc.pack_for_sharing(json.loads(MIXTAPE.read_text(encoding="utf-8")))[0]


def test_encode_to_a_file(tmp_path, capsys):
    target = tmp_path / "out" / "mix.txt"
    code, out, _ = run("encode", str(MIXTAPE), "--out", str(target), capsys=capsys)
    assert code == 0 and "Wrote" in out and "Azeroth Mixtape" in out
    assert sc.decode(target.read_text(encoding="utf-8"))["id"] == "azeroth-mixtape"


def test_encode_reports_problems_and_warnings(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "needledropper-pack", "drops": [,]}', encoding="utf-8")
    code, _out, err = run("encode", str(bad), capsys=capsys)
    assert code == 1 and "invalid JSON" in err
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(user_pack("Mine") | {"drops": user_pack("Mine")["drops"] + [{"zone": 1429}]}),
                     encoding="utf-8")
    code, out, err = run("encode", str(draft), capsys=capsys)
    assert code == 0 and "1 unfinished drop was not included" in err and out.startswith(sc.PREFIX)
    code, _out, err = run("encode", str(tmp_path / "missing.json"), capsys=capsys)
    assert code == 1 and "cannot read" in err


# ------------------------------------------------------------------- decode --

def test_decode_writes_a_valid_pack_file(tmp_path, capsys):
    code_text = sc.encode(sc.pack_for_sharing(json.loads(UNBOUND.read_text(encoding="utf-8")))[0])
    (tmp_path / "code.txt").write_text(f"look:\n```\n{code_text}\n```\n", encoding="utf-8")
    target = tmp_path / "unbound.json"
    code, out, _ = run("decode", str(tmp_path / "code.txt"), "--out", str(target), capsys=capsys)
    assert code == 0 and "Wrote" in out
    text = target.read_text(encoding="utf-8")
    assert list(json.loads(text))[:4] == ["format", "version", "id", "name"]  # documented key order
    pack = packs.load_pack(target)
    assert pack.ok and pack.issues == [] and len(pack.drops) == 62 and pack.id == "azeroth-unbound"


def test_decode_prints_and_raw(capsys):
    data = json.loads(MIXTAPE.read_text(encoding="utf-8"))
    norm, _ = sc.pack_for_sharing(data)
    norm["future"] = "kept by --raw"
    code_text = sc.encode(norm)
    code, out, err = run("decode", code_text, capsys=capsys)
    assert code == 0 and "future" not in json.loads(out) and "'future'" in err
    code, out, _ = run("decode", code_text, "--raw", capsys=capsys)
    assert code == 0 and json.loads(out)["future"] == "kept by --raw"


def test_decode_failure(capsys):
    code, _out, err = run("decode", "!WA:2!abc", capsys=capsys)
    assert code == 1 and "WeakAuras" in err


def test_decode_from_stdin(monkeypatch, capsys):
    code_text = sc.encode(sc.pack_for_sharing(json.loads(MIXTAPE.read_text(encoding="utf-8")))[0])
    monkeypatch.setattr("sys.stdin", io.StringIO(code_text))
    code, out, _ = run("decode", "-", capsys=capsys)
    assert code == 0 and json.loads(out)["name"] == "Azeroth Mixtape"


# ----------------------------------------------------------------- validate --

def test_validate_a_pack_file(capsys):
    code, out, _ = run("validate", str(MIXTAPE), capsys=capsys)
    assert code == 0 and '"Azeroth Mixtape": 38 drop(s), 0 error(s), 0 warning(s)' in out
    assert "Share code: ready" in out


def test_validate_a_broken_pack_file(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "needledropper-pack", "name": "Bad",
                               "drops": [{"zone": "Elwyn Forrest", "artist": "a", "title": "t"}]}), encoding="utf-8")
    code, out, _ = run("validate", str(bad), capsys=capsys)
    assert code == 1 and "FAILED" in out and "did you mean" in out


def test_validate_a_pack_that_is_valid_but_cannot_be_shared(tmp_path, capsys):
    p = tmp_path / "long.json"
    p.write_text(json.dumps(user_pack("x" * 80)), encoding="utf-8")
    code, out, _ = run("validate", str(p), capsys=capsys)
    assert code == 1 and "Not shareable" in out and "at most 64" in out


def test_validate_codes(capsys):
    code_text = sc.encode(sc.pack_for_sharing(json.loads(MIXTAPE.read_text(encoding="utf-8")))[0])
    code, out, _ = run("validate", code_text, capsys=capsys)
    assert code == 0 and out.startswith("OK      share code") and "Azeroth Mixtape" in out and "38 drop(s)" in out
    code, out, _ = run("validate", code_text[: len(code_text) // 2], capsys=capsys)
    assert code == 1 and "cut off" in out
    code, out, _ = run("validate", "hello there", capsys=capsys)
    assert code == 1 and "not a NeedleDropper soundtrack code" in out


def test_packs_without_an_action_shows_usage(capsys):
    code, out, _ = run(capsys=capsys)
    assert code == 2 and "validate" in out and "convert-legacy" in out


def test_register_adds_packs_to_a_cli():
    ap = argparse.ArgumentParser(prog="needledropper")
    sub = ap.add_subparsers(dest="command")
    cp.register(sub)
    args = ap.parse_args(["packs", "encode", "x.json", "--out", "y.txt"])
    assert args.func is cp.cmd_encode and args.file == "x.json" and args.out == "y.txt"
    args = ap.parse_args(["packs", "convert-legacy", "--dry-run"])
    assert args.func is cp.cmd_convert_legacy and args.dry_run


# ----------------------------------------------------------- convert-legacy --

@pytest.fixture
def legacy(tmp_path, _needledropper_home):
    """A v1.1 app dir with user packs and a WoW: Forever tree in temp folders."""
    home = Path(_needledropper_home)
    wow = tmp_path / "WoW"
    addons = wow / "_forever_" / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    (home / "packs").mkdir()
    (home / "packs" / "alts.json").write_text(json.dumps(user_pack("Songs for my alts")), encoding="utf-8")
    (home / "packs" / "off.json").write_text(json.dumps(user_pack("Switched off", title="Rosanna")), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(user_pack("From elsewhere", title="Hold the Line", id="elsewhere")),
                         encoding="utf-8")
    cfg = {"wowRoot": str(wow), "packs": ["builtin:azeroth-unbound", str(home / "packs" / "alts.json"),
                                          str(elsewhere)],
           "packsDisabled": [str(home / "packs" / "off.json")]}
    (home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return {"home": home, "addons": addons, "flavor": wow / "_forever_", "elsewhere": elsewhere}


def _imports(addons: Path) -> list[dict]:
    data = gd.read_companion_data(addons) or {}
    imports = data.get("imports") or []
    return imports if isinstance(imports, list) else []


def test_convert_legacy_offers_user_packs_to_the_game(legacy, capsys):
    code, out, _ = run("convert-legacy", capsys=capsys)
    assert code == 0, out
    imports = _imports(legacy["addons"])
    by_name = {sc.decode(i["code"])["name"]: i["active"] for i in imports}
    assert by_name == {"Songs for my alts": True, "Switched off": False, "From elsewhere": True}
    assert "Azeroth Unbound" not in out  # bundled packs ship inside the addon
    assert "Offered 3 soundtrack(s)" in out
    assert gd.is_companion_addon_installed(legacy["addons"])
    # idempotent
    code, _out, _ = run("convert-legacy", capsys=capsys)
    assert code == 0 and len(_imports(legacy["addons"])) == 3


def test_convert_legacy_dry_run_writes_nothing(legacy, capsys):
    code, out, _ = run("convert-legacy", "--dry-run", capsys=capsys)
    assert code == 0 and "would be offered" in out
    assert list(legacy["addons"].iterdir()) == []


def test_convert_legacy_skips_what_every_account_already_imported(legacy, capsys):
    sv = legacy["flavor"] / "WTF" / "Account" / "ME" / "SavedVariables"
    sv.mkdir(parents=True)
    sv.joinpath("NeedleDropper.lua").write_bytes(
        b'NeedleDropperDB = {\r\n["schema"] = 2,\r\n["importsHandled"] = {\r\n["songs-for-my-alts@0"] = true,\r\n},\r\n'
        b'["soundtracks"] = {\r\n["elsewhere"] = {\r\n["revision"] = 3,\r\n},\r\n},\r\n}\r\n')
    before = {p: p.read_bytes() for p in sv.rglob("*") if p.is_file()}
    code, out, _ = run("convert-legacy", capsys=capsys)
    assert code == 0
    names = [sc.decode(i["code"])["name"] for i in _imports(legacy["addons"])]
    assert names == ["Switched off"]
    assert out.count("already imported in game") == 2
    assert {p: p.read_bytes() for p in sv.rglob("*") if p.is_file()} == before  # SavedVariables only read


def test_convert_legacy_reports_broken_packs(legacy, capsys):
    (legacy["home"] / "packs" / "broken.json").write_text("{nope", encoding="utf-8")
    code, out, _ = run("convert-legacy", "--out-dir", str(legacy["home"] / "codes"), capsys=capsys)
    assert code == 1 and "SKIPPED broken.json" in out
    assert len(_imports(legacy["addons"])) == 3  # the good ones are still offered
    codes = sorted(p.name for p in (legacy["home"] / "codes").iterdir())
    assert codes == ["elsewhere.txt", "songs-for-my-alts.txt", "switched-off.txt"]


def test_convert_legacy_with_an_explicit_addons_folder(legacy, tmp_path, capsys):
    other = tmp_path / "Other" / "_forever_" / "Interface" / "AddOns"
    other.mkdir(parents=True)
    code, _out, _ = run("convert-legacy", "--addons-dir", str(other), capsys=capsys)
    assert code == 0 and len(_imports(other)) == 3 and list(legacy["addons"].iterdir()) == []


def test_convert_legacy_without_wow(legacy, capsys):
    shutil.rmtree(legacy["addons"])
    code, out, _ = run("convert-legacy", capsys=capsys)
    assert code == 1 and "Could not write" in out


def test_convert_legacy_with_nothing_to_convert(tmp_path, _needledropper_home, capsys):
    Path(_needledropper_home, "config.json").write_text(json.dumps({"wowRoot": str(tmp_path / "WoW")}),
                                                        encoding="utf-8")
    code, out, _ = run("convert-legacy", capsys=capsys)
    assert code == 0 and "nothing to convert" in out


def test_legacy_pack_files_lists_user_packs_only(legacy):
    from needledropper.config import load_config

    cfg = load_config(legacy["home"] / "config.json")
    files = cp.legacy_pack_files(cfg, legacy["home"])
    assert [(p.name, on) for p, on in files] == [("alts.json", True), ("elsewhere.json", True), ("off.json", False)]
