"""Static checks of the release tooling: tools/build_release.ps1 and the BigWigs packager's .pkgmeta.

The PowerShell script is never executed here (it runs the whole suite and PyInstaller); these tests
pin what it must reference and how it must behave, by reading it.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "build_release.ps1"
PKGMETA = ROOT / ".pkgmeta"


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(script) -> str:
    """The script without comments (so documentation can't satisfy a check)."""
    no_block = re.sub(r"<#.*?#>", "", script, flags=re.S)
    return "\n".join(line for line in no_block.splitlines() if not line.lstrip().startswith("#"))


# ------------------------------------------------------------ build script ----

def test_script_is_ascii(script):
    # Windows PowerShell 5.1 reads a BOM-less script as ANSI: any non-ASCII character would be mangled.
    assert script.isascii()


def test_script_parameters(code):
    assert re.search(r"\[switch\]\$SkipTests", code)
    assert re.search(r'\[string\]\$OutDir\s*=\s*"dist"', code)


def test_script_fails_loudly(code):
    assert '$ErrorActionPreference = "Stop"' in code
    assert "Set-StrictMode -Version Latest" in code
    assert "$LASTEXITCODE" in code
    assert re.search(r"exit \$exitCode\s*$", code.strip())
    assert "$exitCode = 1" in code
    assert "BUILD FAILED" in code


#: repo path -> how build_release.ps1 must spell it ($RepoRoot is the parent of tools\).
REFERENCES = {
    "companion": '$CompanionDir = Join-Path $RepoRoot "companion"',
    "companion/NeedleDropper.spec": '$SpecFile = Join-Path $CompanionDir "NeedleDropper.spec"',
    "companion/needledropper/__init__.py": '$InitFile = Join-Path $CompanionDir "needledropper\\__init__.py"',
    "companion/build-requirements.txt": '$BuildRequirements = Join-Path $CompanionDir "build-requirements.txt"',
    "addon/NeedleDropper": '$AddonSource = Join-Path $RepoRoot "addon\\NeedleDropper"',
    "addon/NeedleDropper/NeedleDropper.toc": '$AddonToc = Join-Path $AddonSource "NeedleDropper.toc"',
    "packs": '$PacksDir = Join-Path $RepoRoot "packs"',
    "companion/needledropper/deck/static": '$StaticDir = Join-Path $CompanionDir "needledropper\\deck\\static"',
    "companion/needledropper/assets": '$AssetsDir = Join-Path $CompanionDir "needledropper\\assets"',
    "addon/NeedleDropper/Sets": '$SetsDir = Join-Path $AddonSource "Sets"',
    "tools/gen_sets.py": '$GenSets = Join-Path $RepoRoot "tools\\gen_sets.py"',
    "LICENSE": '$LicenseFile = Join-Path $RepoRoot "LICENSE"',
    "tests": '$TestsDir = Join-Path $RepoRoot "tests"',
}


def test_script_root_is_the_repository(code):
    assert "$RepoRoot = Split-Path -Parent $PSScriptRoot" in code
    assert SCRIPT.parent.parent == ROOT
    assert 'Join-Path $RepoRoot ".venv\\Scripts\\python.exe"' in code


@pytest.mark.parametrize("rel", sorted(REFERENCES))
def test_script_references_existing_repo_paths(code, rel):
    assert REFERENCES[rel] in code, f"build_release.ps1 should contain: {REFERENCES[rel]}"
    assert (ROOT / rel).exists(), rel


def test_script_runs_the_full_suite_unless_skipped(code):
    assert re.search(r'"-m", "pytest", \$TestsDir, "-q", "-p", "no:cacheprovider"', code)
    assert re.search(r"if \(\$SkipTests\)", code)


def test_script_reads_the_version_from_the_package(code):
    assert "needledropper.__version__" in code
    assert "import needledropper" in code


def test_script_builds_with_the_spec(code):
    assert re.search(r'"-m", "PyInstaller", "--noconfirm", "--clean"', code)
    assert "$SpecFile" in code and "--distpath" in code and "--workpath" in code
    # output folders must never land under companion/ (the tests compile everything there)
    assert "must not be inside companion" in code


def test_script_checks_the_soundtrack_sets_before_building(code):
    check = code.index('@($GenSets, "--check")')
    build = code.index('Invoke-PyInstaller -Mode "onefile"')
    tests = code.index('"-m", "pytest", $TestsDir')
    assert tests < check < build  # tests, then gen_sets --check, then PyInstaller


def test_script_builds_both_layouts_with_separate_work_folders(code):
    assert '$env:NEEDLEDROPPER_BUILD = $Mode' in code
    assert 'Invoke-PyInstaller -Mode "onefile" -DistPath $exeDir -WorkPath (Join-Path $WorkDir "onefile")' in code
    assert ('Invoke-PyInstaller -Mode "onedir" -DistPath $folderDistDir -WorkPath (Join-Path $WorkDir "onedir")'
            in code)
    assert '$internal = Join-Path $builtFolder "_internal"' in code  # the one-folder data is checked too
    spec = (ROOT / "companion" / "NeedleDropper.spec").read_text(encoding="utf-8")
    assert 'os.environ.get("NEEDLEDROPPER_BUILD")' in spec and 'BUILD_MODES = ("onefile", "onedir")' in spec


def test_script_waits_for_the_windowed_exe(code):
    # PowerShell returns at once from a GUI-subsystem program unless its output is piped
    assert '$lines = @(& $FilePath @Arguments | ForEach-Object { "$_" })' in code


def test_script_checks_the_exe(code):
    assert "PyInstaller.utils.cliutils.archive_viewer" in code
    for needed in ("packs/azeroth-mixtape.json", "packs/azeroth-unbound.json", "addon/NeedleDropper/NeedleDropper.toc",
                   "addon/NeedleDropper/Sets/AzerothMixtape.lua", "addon/NeedleDropper/Sets/AzerothUnbound.lua",
                   "addon/NeedleDropper/media/silence.mp3", "needledropper/deck/static/connect.html",
                   "needledropper/assets/NeedleDropper.ico", "needledropper/assets/NeedleDropper-paused.ico",
                   "needledropper.transport.pixel", "needledropper.transport.chatlog", "needledropper.tray",
                   "needledropper.panel_api"):
        assert needed in code, needed
    assert "Map.lua" not in code and "Map.json" not in code  # the v1.1 map is retired
    assert '"*panel.html"' in code  # and the v1.1 control panel must not come back
    assert '@("--version")' in code and '@("validate")' in code
    assert "NEEDLEDROPPER_HOME" in code  # smoke runs never touch the real %APPDATA%
    assert "missing module named" in code  # a missing needledropper module fails the build


def test_script_release_artifacts(code):
    assert '"$AppName-$Version-addon.zip"' in code
    assert '"$AppName-$Version-win64.zip"' in code
    assert '"$AppName-$Version-win64-folder.zip"' in code
    assert "foreach ($file in @($addonZip, $winZip, $folderZip, $outExe))" in code
    assert 'Add-TreeEntries -Dir $builtFolder -Prefix $AppName -Into $folderEntries' in code
    assert '"SHA256SUMS.txt"' in code
    assert '"README.txt"' in code and '"LICENSE.txt"' in code
    assert '"$AppName.exe"' in code
    assert "Get-FileHash" in code and "SHA256" in code and "ToLowerInvariant()" in code
    # sha256sum format: "<hash>  <name>" joined with LF
    assert '"$hash  $(Split-Path -Leaf $file)"' in code
    assert '-join "`n"' in code


def test_script_addon_zip_layout(code):
    assert 'Add-TreeEntries -Dir $AddonSource -Prefix "NeedleDropper"' in code
    assert '"NeedleDropper/LICENSE"' in code
    assert "the addon zip must hold only the NeedleDropper/ folder" in code
    # never package the player's own media folder
    assert not re.search(r"Add-TreeEntries[^\n]*NeedleDropper_Local", code)


def test_script_writes_zips_with_forward_slashes(code):
    # Compress-Archive on Windows PowerShell 5.1 writes backslash entry names; addon managers choke on them.
    assert "Compress-Archive" not in code
    assert "System.IO.Compression.ZipArchive" in code
    assert 'Contains("\\")' in code


def test_quick_start_text_describes_the_v12_app(script):
    m = re.search(r"function Get-QuickStartText.*?\n}\n", script, re.S)
    assert m
    text = m.group(0)
    for needed in ("Connect page", "tray", "/needle test", "Start with Windows", "win64-folder.zip"):
        assert needed in text, needed
    for retired in ("control panel", "Rebuild map", "/reload", "Keep the NeedleDropper window open"):
        assert retired not in text, retired


def test_script_skip_rules_match_the_spec(script):
    spec = (ROOT / "companion" / "NeedleDropper.spec").read_text(encoding="utf-8")

    def ps_list(name: str) -> set[str]:
        m = re.search(rf"\${name}\s*=\s*@\(([^)]*)\)", script)
        assert m, name
        return set(re.findall(r'"([^"]*)"', m.group(1)))

    def py_set(name: str) -> set[str]:
        m = re.search(rf"^{name}\s*=\s*[\[{{(]([^\]}})]*)[\]}})]", spec, re.M)
        assert m, name
        return set(re.findall(r'"([^"]*)"', m.group(1)))

    assert ps_list("SkipDirs") == py_set("SKIP_DIRS")
    assert ps_list("SkipFiles") == py_set("SKIP_FILES")
    assert ps_list("SkipSuffixes") == py_set("SKIP_SUFFIXES")


# ------------------------------------------------------------------ .pkgmeta ----

def parse_pkgmeta(text: str) -> dict:
    """The small YAML subset .pkgmeta uses: scalars, one level of mappings and lists."""
    data: dict = {}
    current = None
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            value = value.strip()
            if value:
                data[key.strip()] = value
                current = None
            else:
                current = key.strip()
                data[current] = None
            continue
        item = line.strip()
        if current is None:
            raise ValueError(f"indented line without a parent key: {raw!r}")
        if item.startswith("- "):
            if data[current] is None:
                data[current] = []
            data[current].append(item[2:].strip().strip('"'))
        else:
            k, _, v = item.partition(":")
            if data[current] is None:
                data[current] = {}
            data[current][k.strip()] = v.strip()
    return data


@pytest.fixture(scope="module")
def pkgmeta() -> dict:
    return parse_pkgmeta(PKGMETA.read_text(encoding="utf-8"))


def test_pkgmeta_packages_the_addon_folder(pkgmeta):
    assert pkgmeta["package-as"] == "NeedleDropper"
    assert pkgmeta["move-folders"] == {"NeedleDropper/addon/NeedleDropper": "NeedleDropper"}
    assert (ROOT / "addon" / "NeedleDropper" / "NeedleDropper.toc").is_file()


def test_pkgmeta_manual_changelog_exists(pkgmeta):
    mc = pkgmeta["manual-changelog"]
    assert mc == {"filename": "CHANGELOG.md", "markup-type": "markdown"}
    assert (ROOT / mc["filename"]).is_file()


def _gitignored(name: str) -> bool:
    gi = ROOT / ".gitignore"
    if not gi.is_file():
        return False
    for pat in gi.read_text(encoding="utf-8").splitlines():
        pat = pat.strip()
        if not pat or pat.startswith("#"):
            continue
        bare = pat.strip("/")
        if fnmatch.fnmatch(name, bare):
            return True
    return False


def test_pkgmeta_ignores_everything_but_the_addon(pkgmeta):
    ignore = pkgmeta["ignore"]
    assert isinstance(ignore, list) and ignore
    # kept on purpose: the addon itself, the MIT license and the changelog
    kept = {"addon", "LICENSE", "CHANGELOG.md"}
    for entry in sorted(ROOT.iterdir()):
        name = entry.name
        if name.startswith(".") or name in kept:  # dot files are always ignored by the packager
            continue
        covered = any(fnmatch.fnmatch(name, pat.rstrip("/")) for pat in ignore) or _gitignored(name)
        assert covered, f"top-level {name!r} would end up in the addon zip: add it to .pkgmeta ignore"
    assert "addon/NeedleDropper_Local" in ignore


def test_pkgmeta_never_ignores_addon_files(pkgmeta):
    addon = ROOT / "addon" / "NeedleDropper"
    files = [p.relative_to(ROOT).as_posix() for p in addon.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    assert files
    for pat in pkgmeta["ignore"]:
        assert pat not in ("addon", "addon/", "addon/NeedleDropper", "addon/NeedleDropper/"), pat
        for f in files:
            assert not fnmatch.fnmatch(f, pat), f"{pat!r} would drop {f}"
    for kept in ("LICENSE", "CHANGELOG.md"):
        assert not any(fnmatch.fnmatch(kept, pat) for pat in pkgmeta["ignore"]), kept


def test_pkgmeta_parser_handles_the_subset():
    data = parse_pkgmeta("# c\npackage-as: X\n\nmove-folders:\n  X/a: B  # note\nignore:\n  - one\n  - \"*.txt\"\n")
    assert data == {"package-as": "X", "move-folders": {"X/a": "B"}, "ignore": ["one", "*.txt"]}
