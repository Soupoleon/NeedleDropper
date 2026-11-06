# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the windowed companion app ``NeedleDropper.exe``.

Build it with ``tools\\build_release.ps1``, or directly from the repository root:

    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean companion\\NeedleDropper.spec

**Windowed** (``console=False``): double-clicking the exe (and Start with Windows) opens no
console window; the companion lives in the tray and logs to
``%APPDATA%\\NeedleDropper\\logs\\needledropper.log``. ``companion/launcher.py`` is the entry
point: it attaches a console only when a command is given (``NeedleDropper.exe diag``).

**Two layouts from this one spec**, picked by the environment variable
``NEEDLEDROPPER_BUILD``:

* ``onefile`` (default): one ``NeedleDropper.exe`` that unpacks itself to a temporary
  folder at every start. Easiest to download and run.
* ``onedir``: a ``NeedleDropper\\`` folder (``NeedleDropper.exe`` + ``_internal\\``),
  shipped as a zip. Nothing is unpacked at runtime, so it starts faster and antivirus
  heuristics flag it less often (self-extracting one-file executables are a classic false
  positive). The build script makes both; each needs its own PyInstaller run (and work
  folder), because two EXE targets in one run would share the same .pkg work file.

Bundled data, laid out as ``needledropper/resources.py`` expects under ``sys._MEIPASS``:

    companion/needledropper/deck/static/**  ->  needledropper/deck/static/   (Connect page, player)
    companion/needledropper/assets/*.ico    ->  needledropper/assets/        (tray icons)
    packs/*.json                            ->  packs/
    addon/NeedleDropper/** (incl. Sets/)    ->  addon/NeedleDropper/         ("Install addon" fallback)

``__pycache__`` folders, dot files and editor/OS litter are never bundled. Every module of the
``needledropper`` package is listed as a hidden import, because some are loaded by name at
runtime (``importlib.import_module`` in cli.py: transports, the packs commands) and
PyInstaller's import analysis cannot see those. No UPX (it triggers antivirus false positives
and saves little).
"""
import os
import re
import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 - SPECPATH is provided by PyInstaller
REPO = SPEC_DIR.parent
PKG_NAME = "needledropper"
PKG_DIR = SPEC_DIR / PKG_NAME

APP_NAME = "NeedleDropper"
LAUNCHER = SPEC_DIR / "launcher.py"
ICON_FILE = REPO / "assets" / "NeedleDropper.ico"
COPYRIGHT = "Copyright (c) 2026 NeedleDropper contributors"

#: "onefile" (default) or "onedir"; see the module docstring.
BUILD_MODES = ("onefile", "onedir")
BUILD_MODE = (os.environ.get("NEEDLEDROPPER_BUILD") or "onefile").strip().lower()

# Same rules as `needledropper install-addon` (cli.addon_files), plus editor/OS litter.
SKIP_DIRS = {"__pycache__", ".git", ".svn", ".hg", ".idea", ".vscode"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_SUFFIXES = (".bak", ".tmp", ".orig", ".pyc", ".pyo", ".swp")


def _fail(message):
    raise SystemExit(f"NeedleDropper.spec: {message}")


def _shippable(name):
    return not (name.startswith(".") or name in SKIP_FILES or name.endswith(SKIP_SUFFIXES))


def data_tree(src, dest):
    """``(file, bundle dir)`` pairs for every shippable file below ``src``, kept in place under ``dest``."""
    src = Path(src)
    if not src.is_dir():
        _fail(f"data folder not found: {src}")
    pairs = []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        rel = Path(root).relative_to(src).as_posix()
        target = dest if rel == "." else f"{dest}/{rel}"
        for name in sorted(files):
            if _shippable(name):
                pairs.append((str(Path(root) / name), target))
    if not pairs:
        _fail(f"data folder is empty: {src}")
    return pairs


def data_glob(src_dir, pattern, dest):
    files = sorted(p for p in Path(src_dir).glob(pattern) if p.is_file() and _shippable(p.name))
    if not files:
        _fail(f"no {pattern} files in {src_dir}")
    return [(str(p), dest) for p in files]


def package_modules(pkg_dir, pkg_name):
    """Dotted names of every module and subpackage in the package (no imports needed)."""
    names = set()
    for path in Path(pkg_dir).rglob("*.py"):
        parts = path.relative_to(pkg_dir).with_suffix("").parts
        if any(p in SKIP_DIRS or p.startswith(".") for p in parts) or parts[-1] == "__main__":
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join((pkg_name,) + tuple(parts)))
    return sorted(names)


def read_version(init_file):
    text = Path(init_file).read_text(encoding="utf-8")
    m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", text, re.MULTILINE)
    if not m:
        _fail(f"__version__ not found in {init_file}")
    return m.group(1)


def version_tuple(version):
    nums = [int(n) for n in re.findall(r"\d+", version.split("+")[0])[:4]]
    return tuple((nums + [0, 0, 0, 0])[:4])


def version_resource(version):
    """Windows file properties (Details tab, Task Manager name); None if unavailable."""
    if sys.platform != "win32":
        return None
    try:
        from PyInstaller.utils.win32 import versioninfo as vi
    except ImportError:
        return None
    nums = version_tuple(version)
    strings = [
        vi.StringStruct("CompanyName", "NeedleDropper contributors"),
        vi.StringStruct("FileDescription", APP_NAME),
        vi.StringStruct("FileVersion", version),
        vi.StringStruct("InternalName", APP_NAME),
        vi.StringStruct("LegalCopyright", COPYRIGHT),
        vi.StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
        vi.StringStruct("ProductName", APP_NAME),
        vi.StringStruct("ProductVersion", version),
        vi.StringStruct("Comments", "Companion app for the NeedleDropper addon for World of Warcraft: Forever. "
                                    "MIT licensed."),
    ]
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004,
                             fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            vi.StringFileInfo([vi.StringTable("040904B0", strings)]),
            vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


# ------------------------------------------------------------------ inputs ----

if BUILD_MODE not in BUILD_MODES:
    _fail(f"NEEDLEDROPPER_BUILD must be one of {', '.join(BUILD_MODES)}, not {BUILD_MODE!r}")

for required in (LAUNCHER, PKG_DIR / "__init__.py", PKG_DIR / "cli.py", PKG_DIR / "tray.py",
                 PKG_DIR / "deck" / "static" / "connect.html", PKG_DIR / "deck" / "static" / "player.html",
                 PKG_DIR / "assets" / "NeedleDropper.ico", PKG_DIR / "assets" / "NeedleDropper-paused.ico",
                 REPO / "packs" / "azeroth-mixtape.json",
                 REPO / "addon" / "NeedleDropper" / "NeedleDropper.toc"):
    if not required.is_file():
        _fail(f"required file not found: {required}")

STATIC_DATAS = data_tree(PKG_DIR / "deck" / "static", f"{PKG_NAME}/deck/static")
if not any(src.lower().endswith(".html") for src, _ in STATIC_DATAS):
    _fail(f"no .html page in {PKG_DIR / 'deck' / 'static'}")
ASSET_DATAS = data_glob(PKG_DIR / "assets", "*.ico", f"{PKG_NAME}/assets")
PACK_DATAS = data_glob(REPO / "packs", "*.json", "packs")
ADDON_DATAS = data_tree(REPO / "addon" / "NeedleDropper", "addon/NeedleDropper")
if not any(dest == "addon/NeedleDropper/Sets" for _src, dest in ADDON_DATAS):
    _fail("the addon has no Sets/*.lua (run tools/gen_sets.py)")
DATAS = STATIC_DATAS + ASSET_DATAS + PACK_DATAS + ADDON_DATAS

HIDDEN_IMPORTS = package_modules(PKG_DIR, PKG_NAME)

# Never needed at runtime; keeps test/build tooling from the venv out of the exe.
EXCLUDES = ["tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3",
            "pytest", "_pytest", "lupa", "setuptools", "pkg_resources", "_distutils_hack", "pip"]

VERSION = read_version(PKG_DIR / "__init__.py")
ICON = str(ICON_FILE) if ICON_FILE.is_file() else None

# ------------------------------------------------------------------- build ----

a = Analysis(  # noqa: F821 - provided by PyInstaller
    [str(LAUNCHER)],
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

EXE_OPTIONS = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,  # windowed: the tray app opens no console window
    disable_windowed_traceback=False,  # last resort: the bootloader shows a dialog if even the launcher fails
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=version_resource(VERSION),
)

if BUILD_MODE == "onefile":
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        runtime_tmpdir=None,
        **EXE_OPTIONS,
    )
else:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **EXE_OPTIONS,
    )
    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
