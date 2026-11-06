"""Release packaging (SPEC-v1.1 D, CONTRACTS-v1.2 3.6): the PyInstaller spec (a windowed exe, one
file by default, one folder with ``NEEDLEDROPPER_BUILD=onedir``), the exe launcher (console only for
commands, crash log + message box), the bundle layout expected by ``needledropper.resources``, the
pinned build tools, the icon and the license.

All static or in-process: PyInstaller never runs here. The spec is executed with stub
``Analysis`` / ``PYZ`` / ``EXE`` / ``COLLECT`` classes that only record their arguments.
"""
from __future__ import annotations

import importlib.util
import io
import os
import re
import shutil
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion"
PKG = COMPANION / "needledropper"
SPEC_FILE = COMPANION / "NeedleDropper.spec"
LAUNCHER = COMPANION / "launcher.py"
ADDON = ROOT / "addon" / "NeedleDropper"
STATIC = PKG / "deck" / "static"
ASSETS = PKG / "assets"
PACKS = ROOT / "packs"
ICON = ROOT / "assets" / "NeedleDropper.ico"

SKIP_DIRS = {"__pycache__", ".git", ".svn", ".hg", ".idea", ".vscode"}


def _shippable_files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith(".") or name in {"Thumbs.db", "desktop.ini"}:
                continue
            if name.endswith((".bak", ".tmp", ".orig", ".pyc", ".pyo", ".swp")):
                continue
            out.append(Path(dirpath) / name)
    return sorted(out)


# ------------------------------------------------------------------- spec ----

class _Recorder:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _Analysis(_Recorder):
    def __init__(self, scripts, **kwargs):
        super().__init__(scripts, **kwargs)
        self.scripts_in = list(scripts)
        # distinct objects, so the test can tell which TOC went where
        self.pure, self.scripts, self.binaries, self.datas = ["<pure>"], ["<scripts>"], ["<binaries>"], ["<datas>"]


class _PYZ(_Recorder):
    pass


class _EXE(_Recorder):
    pass


class _COLLECT(_Recorder):
    pass


def exec_spec(mode=None) -> dict:
    """The spec's namespace after executing it the way PyInstaller does (with stubs).
    ``mode`` sets NEEDLEDROPPER_BUILD for the run (None: unset, the default layout)."""
    ns = {"SPECPATH": str(COMPANION), "SPEC": str(SPEC_FILE), "specnm": "NeedleDropper",
          "Analysis": _Analysis, "PYZ": _PYZ, "EXE": _EXE, "os": os, "__name__": "__main__"}
    if mode == "onedir":
        ns["COLLECT"] = _COLLECT  # only the one-folder layout may use it
    saved = os.environ.pop("NEEDLEDROPPER_BUILD", None)
    try:
        if mode is not None:
            os.environ["NEEDLEDROPPER_BUILD"] = mode
        code = compile(SPEC_FILE.read_bytes(), str(SPEC_FILE), "exec")
        exec(code, ns)
    finally:
        os.environ.pop("NEEDLEDROPPER_BUILD", None)
        if saved is not None:
            os.environ["NEEDLEDROPPER_BUILD"] = saved
    return ns


@pytest.fixture(scope="module")
def spec_ns() -> dict:
    return exec_spec()


def test_spec_builds_a_windowed_one_file_exe_named_needledropper(spec_ns):
    exe = spec_ns["exe"]
    assert isinstance(exe, _EXE) and spec_ns["BUILD_MODE"] == "onefile"
    kw = exe.kwargs
    assert kw["name"] == "NeedleDropper"
    assert kw["console"] is False  # the tray app opens no console window
    assert kw["upx"] is False
    assert kw["debug"] is False
    a = spec_ns["a"]
    pyz = spec_ns["pyz"]
    assert isinstance(pyz, _PYZ) and pyz.args[0] is a.pure
    # one-file: scripts, binaries and datas all go into the EXE itself. (A COLLECT step would
    # have raised NameError above: the stub namespace doesn't provide it.)
    assert exe.args[0] is pyz
    for toc in (a.scripts, a.binaries, a.datas):
        assert any(arg is toc for arg in exe.args)
    assert kw.get("runtime_tmpdir") is None and "exclude_binaries" not in kw


def test_spec_builds_the_one_folder_variant_on_request():
    ns = exec_spec("onedir")
    exe, coll, a = ns["exe"], ns["coll"], ns["a"]
    assert ns["BUILD_MODE"] == "onedir"
    assert exe.kwargs["exclude_binaries"] is True and exe.kwargs["console"] is False
    assert not any(arg is a.binaries or arg is a.datas for arg in exe.args)
    assert isinstance(coll, _COLLECT) and coll.args[0] is exe
    assert any(arg is a.binaries for arg in coll.args) and any(arg is a.datas for arg in coll.args)
    assert coll.kwargs["name"] == "NeedleDropper" and coll.kwargs["upx"] is False
    assert exe.kwargs["icon"] == exec_spec()["exe"].kwargs["icon"]
    with pytest.raises(SystemExit, match="NEEDLEDROPPER_BUILD"):
        exec_spec("twofile")


def test_spec_entry_point_is_the_launcher(spec_ns):
    a = spec_ns["a"]
    assert [Path(s).resolve() for s in a.scripts_in] == [LAUNCHER.resolve()]
    assert str(COMPANION.resolve()) in [str(Path(p).resolve()) for p in a.kwargs["pathex"]]
    src = LAUNCHER.read_text(encoding="utf-8")
    assert "from needledropper.cli import main" in src


def test_spec_bundles_the_four_data_trees(spec_ns):
    datas = spec_ns["a"].kwargs["datas"]
    by_dest: dict[str, set[str]] = {}
    for src, dest in datas:
        assert Path(src).is_file(), src
        by_dest.setdefault(dest.replace("\\", "/"), set()).add(str(Path(src).resolve()))

    def expect_tree(root: Path, dest_root: str) -> None:
        files = _shippable_files(root)
        assert files, root
        for f in files:
            rel = f.parent.relative_to(root).as_posix()
            dest = dest_root if rel == "." else f"{dest_root}/{rel}"
            assert str(f.resolve()) in by_dest.get(dest, set()), f"{f} not bundled into {dest}"

    expect_tree(STATIC, "needledropper/deck/static")
    expect_tree(ADDON, "addon/NeedleDropper")
    packs = sorted(PACKS.glob("*.json"))
    assert packs
    assert {str(p.resolve()) for p in packs} <= by_dest["packs"]
    icons = sorted(ASSETS.glob("*.ico"))
    assert {p.name for p in icons} >= {"NeedleDropper.ico", "NeedleDropper-paused.ico"}
    assert {str(p.resolve()) for p in icons} == by_dest["needledropper/assets"]
    # the addon's essentials are among them: the soundtrack sets included
    addon_files = {Path(s).relative_to(ADDON.resolve()).as_posix()
                   for d, srcs in by_dest.items() if d.startswith("addon/NeedleDropper") for s in srcs}
    assert {"NeedleDropper.toc", "Sets/AzerothMixtape.lua", "Sets/AzerothUnbound.lua",
            "media/silence.mp3"} <= addon_files
    static_files = {Path(s).name for s in by_dest["needledropper/deck/static"]}
    assert {"connect.html", "connect.js", "connect.css", "player.html", "player.js"} <= static_files
    assert not any(name.startswith("panel.") for name in static_files)


def test_spec_bundles_nothing_else(spec_ns):
    datas = spec_ns["a"].kwargs["datas"]
    allowed = ("needledropper/deck/static", "needledropper/assets", "packs", "addon/NeedleDropper")
    for src, dest in datas:
        d = dest.replace("\\", "/")
        assert d in allowed or d.startswith(("needledropper/deck/static/", "addon/NeedleDropper/")), d
        parts = Path(src).parts
        assert "__pycache__" not in parts and not Path(src).name.startswith(".")
        assert "NeedleDropper_Local" not in parts
        assert not src.endswith((".pyc", ".bak", ".tmp", ".orig"))


def test_spec_lists_every_package_module_as_hidden_import(spec_ns):
    hidden = set(spec_ns["a"].kwargs["hiddenimports"])
    expected = set()
    for path in PKG.rglob("*.py"):
        parts = path.relative_to(PKG).with_suffix("").parts
        if "__pycache__" in parts or parts[-1] == "__main__":
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        expected.add(".".join(("needledropper",) + parts))
    assert expected <= hidden
    # loaded by name at runtime (cli.build_transports uses importlib), so analysis can't see them
    assert {"needledropper.transport.pixel", "needledropper.transport.chatlog", "needledropper.cli",
            "needledropper.resources"} <= hidden
    assert not any(h.endswith("__main__") for h in hidden)


def test_spec_version_resource_and_icon(spec_ns):
    from needledropper import __version__

    assert spec_ns["VERSION"] == __version__
    nums = spec_ns["version_tuple"](__version__)
    assert len(nums) == 4 and all(isinstance(n, int) for n in nums)
    assert spec_ns["version_tuple"]("1.2.3rc1") == (1, 2, 3, 1)
    kw = spec_ns["exe"].kwargs
    assert kw["icon"] is not None and Path(kw["icon"]).resolve() == ICON.resolve()
    if sys.platform == "win32" and importlib.util.find_spec("PyInstaller") is not None:
        vi = kw["version"]
        text = str(vi)
        assert "NeedleDropper" in text and "Copyright (c) 2026 NeedleDropper contributors" in text
        assert __version__ in text


def test_spec_fails_loudly_on_a_missing_data_tree(spec_ns, tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        spec_ns["data_tree"](tmp_path / "missing", "x")
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit, match="empty"):
        spec_ns["data_tree"](tmp_path / "empty", "x")
    with pytest.raises(SystemExit, match="no \\*.json"):
        spec_ns["data_glob"](tmp_path / "empty", "*.json", "packs")


def test_spec_data_tree_skips_litter(spec_ns, tmp_path):
    root = tmp_path / "tree"
    (root / "media").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / ".git").mkdir()
    for rel in ("a.lua", "media/b.mp3", "__pycache__/c.pyc", ".git/config", ".hidden", "d.lua.bak",
                "Thumbs.db", "e.tmp"):
        (root / rel).write_text("x", encoding="utf-8")
    pairs = spec_ns["data_tree"](root, "addon/NeedleDropper")
    got = sorted((Path(s).name, d) for s, d in pairs)
    assert got == [("a.lua", "addon/NeedleDropper"), ("b.mp3", "addon/NeedleDropper/media")]


# ----------------------------------------------- bundle layout = resources ----

def test_bundle_layout_matches_resources_contract(spec_ns, tmp_path, monkeypatch):
    """Lay the spec's datas out like PyInstaller unpacks them, then ask resources.py."""
    from needledropper import resources

    meipass = tmp_path / "_MEI12345"
    for src, dest in spec_ns["a"].kwargs["datas"]:
        target = meipass / dest / Path(src).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    assert resources.is_frozen()
    assert resources.static_dir() == meipass / "needledropper" / "deck" / "static"
    assert resources.assets_dir() == meipass / "needledropper" / "assets"
    assert resources.tray_icon_path().is_file() and resources.tray_icon_path(True).is_file()
    assert (resources.static_dir() / "connect.html").is_file()
    assert (resources.addon_source_dir() / "Sets" / "AzerothMixtape.lua").is_file()
    assert resources.packs_dir() == meipass / "packs"
    assert resources.addon_source_dir() == meipass / "addon" / "NeedleDropper"
    assert resources.default_pack_path() == meipass / "packs" / "azeroth-unbound.json"
    assert resources.default_pack_path().is_file()
    assert (resources.addon_source_dir() / "NeedleDropper.toc").is_file()
    assert (resources.addon_source_dir() / "media" / "silence.mp3").is_file()
    assert any(p.suffix == ".html" for p in resources.static_dir().iterdir())
    assert not (meipass / "addon" / "NeedleDropper_Local").exists()


# --------------------------------------------------------------- launcher ----

@pytest.fixture()
def launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("nd_release_launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    # never retitle the console running the tests, never show a real message box
    monkeypatch.setattr(mod, "set_console_title", lambda *a, **k: False)
    monkeypatch.setattr(mod, "show_error", lambda *a, **k: False)
    return mod


def test_launcher_exit_codes(launcher, capsys):
    assert launcher.exit_code(None) == 0
    assert launcher.exit_code(0) == 0
    assert launcher.exit_code(2) == 2
    assert launcher.exit_code(True) == 1
    assert launcher.exit_code("bad config") == 1
    assert "bad config" in capsys.readouterr().err


def test_launcher_runs_the_cli(launcher, capsys, monkeypatch):
    from needledropper import __version__

    monkeypatch.setattr(launcher, "owns_console", lambda: pytest.fail("no pause after success"))
    monkeypatch.setattr(launcher, "_stream_usable", lambda stream: True)  # pytest's capture counts as a console
    assert launcher.run(["--version"]) == 0
    assert __version__ in capsys.readouterr().out
    assert launcher.console_mode == "existing"


def test_launcher_passes_through_cli_return_code(launcher, monkeypatch):
    import needledropper.cli as cli

    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(argv) or 7)
    monkeypatch.setattr(launcher, "owns_console", lambda: False)
    monkeypatch.setattr(launcher, "attach_console", lambda **kw: "existing")
    assert launcher.run(["zones"]) == 7
    assert launcher.run() == 7
    assert seen == [["zones"], None]


def test_launcher_reports_a_crash_and_pauses_only_in_its_own_console(launcher, monkeypatch, capsys):
    import needledropper.cli as cli

    def boom(argv=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "main", boom)
    waits = []
    monkeypatch.setattr(launcher, "wait_for_enter", lambda *a, **k: waits.append(1))
    monkeypatch.setattr(launcher, "owns_console", lambda: False)
    assert launcher.run([]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError: kaboom" in err and "unexpected error" in err
    assert "RuntimeError: kaboom" in launcher.crash_log_path().read_text(encoding="utf-8")
    assert waits == []
    monkeypatch.setattr(launcher, "owns_console", lambda: True)
    assert launcher.run([]) == 1
    assert waits == [1]
    # Ctrl+C and success never pause
    monkeypatch.setattr(cli, "main", lambda argv=None: 130)
    assert launcher.run([]) == 130
    monkeypatch.setattr(cli, "main", lambda argv=None: 0)
    assert launcher.run([]) == 0
    assert waits == [1]


@pytest.mark.parametrize("argv, wants", [
    ([], False), (["--background"], False), (["-v"], False), (["--config", "C:\\x\\config.json"], False),
    (["--config=C:\\x.json", "--verbose"], False),
    (["--version"], True), (["--help"], True), (["diag"], True), (["validate"], True),
    (["--background", "zones"], True), (["--config", "c.json", "run"], True), (["--seconds", "5"], True),
    (["setup"], False), (["--config", "c.json", "setup"], False),  # a shortcut to the Connect page: tray, no console
    (["setup", "--help"], True),
])
def test_launcher_asks_for_a_console_only_for_commands(launcher, argv, wants):
    assert launcher.needs_console(argv) is wants


class _FakeFn:
    """A ctypes-function stand-in (attributes such as argtypes may be assigned)."""

    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class _FakeKernel32:
    def __init__(self, attach: bool, alloc: bool) -> None:
        self.AttachConsole = _FakeFn(1 if attach else 0)
        self.AllocConsole = _FakeFn(1 if alloc else 0)


def test_attach_console_prefers_the_terminal_then_its_own_window(launcher, monkeypatch, tmp_path):
    opened = []
    files = []

    def fake_open(name, mode="r", **kw):
        opened.append(name)
        fh = io.StringIO()
        files.append(fh)
        return fh

    monkeypatch.setattr(launcher, "open", fake_open, raising=False)
    monkeypatch.setattr(launcher, "print", lambda *a, **k: None, raising=False)
    for stream in ("stdout", "stderr", "stdin"):
        monkeypatch.setattr(sys, stream, getattr(sys, stream))  # restored by monkeypatch afterwards
    monkeypatch.setattr(sys, "stdout", None)
    k32 = _FakeKernel32(attach=True, alloc=True)
    monkeypatch.setattr(launcher, "_kernel32", lambda: k32)
    assert launcher.attach_console() == "attached"
    assert k32.AttachConsole.calls == [(launcher.ATTACH_PARENT_PROCESS,)] and k32.AllocConsole.calls == []
    assert opened == ["CONOUT$", "CONOUT$", "CONIN$"] and sys.stdout is files[0]
    monkeypatch.setattr(sys, "stdout", None)
    k32 = _FakeKernel32(attach=False, alloc=True)  # started from a shortcut: no terminal to attach to
    monkeypatch.setattr(launcher, "_kernel32", lambda: k32)
    assert launcher.attach_console() == "allocated" and launcher.owns_console() is True
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(launcher, "_kernel32", lambda: _FakeKernel32(attach=False, alloc=False))
    assert launcher.attach_console() == "none"
    monkeypatch.setattr(launcher, "_kernel32", lambda: None)
    assert launcher.attach_console() == "none"


def test_attach_console_keeps_redirected_output(launcher, monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_kernel32", lambda: pytest.fail("no console calls for a usable stdout"))
    with open(tmp_path / "redirected.txt", "w", encoding="utf-8") as fh:
        monkeypatch.setattr(sys, "stdout", fh)
        assert launcher.attach_console() == "existing"


def test_an_allocated_console_waits_before_it_closes(launcher, monkeypatch):
    import needledropper.cli as cli

    waits = []
    monkeypatch.setattr(launcher, "wait_for_enter", lambda *a, **k: waits.append(1))
    monkeypatch.setattr(launcher, "attach_console", lambda **kw: setattr(launcher, "console_mode", "allocated"))
    monkeypatch.setattr(cli, "main", lambda argv=None: 0)
    assert launcher.run(["zones"]) == 0 and waits == [1]  # even on success: the window would vanish


def test_launcher_owns_console_logic(launcher, monkeypatch):
    class FakeKernel32:
        def __init__(self, count):
            self.count = count

        def GetConsoleProcessList(self, buf, size):
            return self.count

    monkeypatch.setattr(launcher, "console_mode", "existing")
    monkeypatch.delattr(sys, "frozen", raising=False)
    for count, own in ((0, False), (1, True), (2, False), (3, False)):
        monkeypatch.setattr(launcher, "_kernel32", lambda c=count: FakeKernel32(c))
        assert launcher.owns_console() is own, count
    monkeypatch.setattr(sys, "frozen", True, raising=False)  # one-file: bootloader + interpreter
    for count, own in ((1, True), (2, True), (3, False)):
        monkeypatch.setattr(launcher, "_kernel32", lambda c=count: FakeKernel32(c))
        assert launcher.owns_console() is own, count
    monkeypatch.setattr(launcher, "_kernel32", lambda: None)
    assert launcher.owns_console() is False


def test_launcher_waits_only_on_a_terminal(launcher, monkeypatch):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or "")

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    launcher.wait_for_enter()
    assert prompts == []
    monkeypatch.setattr(sys, "stdin", Tty(""))
    launcher.wait_for_enter()
    assert prompts == ["Press Enter to close this window."]


def test_launcher_imports_nothing_before_it_can_log_a_crash():
    """A broken package must still produce a crash log: the launcher imports needledropper lazily."""
    src = LAUNCHER.read_text(encoding="utf-8")
    top = src[: src.index("def run(")]
    assert "import needledropper" not in top and "from needledropper" not in top
    assert "from needledropper.cli import main" in src


# ------------------------------------------------------------ build tools ----

def test_build_requirements_pin_pyinstaller():
    lines = [ln.strip() for ln in (COMPANION / "build-requirements.txt").read_text(encoding="utf-8").splitlines()]
    reqs = [ln for ln in lines if ln and not ln.startswith("#")]
    assert reqs, "no requirements"
    for ln in reqs:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][0-9A-Za-z.+-]*", ln), f"not an exact pin: {ln}"
    pins = dict(ln.split("==", 1) for ln in reqs)
    assert "pyinstaller" in {k.lower() for k in pins}


def test_installed_pyinstaller_matches_the_pin():
    try:
        from importlib.metadata import PackageNotFoundError, version
        installed = version("pyinstaller")
    except Exception:  # PackageNotFoundError or no metadata
        pytest.skip("PyInstaller is not installed in this environment")
    text = (COMPANION / "build-requirements.txt").read_text(encoding="utf-8")
    pinned = re.search(r"(?im)^pyinstaller==(\S+)", text).group(1)
    assert installed == pinned, (f"venv has PyInstaller {installed} but companion/build-requirements.txt pins "
                                 f"{pinned}; update the pin")


def test_version_is_a_release_version():
    from needledropper import __version__

    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert (PKG / "__init__.py").read_text(encoding="utf-8").count("__version__ =") == 1


# ------------------------------------------------------------------ icon ----

def test_icon_is_a_valid_multi_size_ico():
    data = ICON.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1) and count >= 4
    sizes = set()
    for i in range(count):
        w, h, _cc, _r, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        w, h = w or 256, h or 256
        assert w == h and planes == 1 and bpp == 32
        assert offset + size <= len(data)
        blob = data[offset:offset + size]
        if blob[:8] == b"\x89PNG\r\n\x1a\n":
            assert struct.unpack(">II", blob[16:24]) == (w, h)
        else:
            hdr_size, bw, bh = struct.unpack_from("<Iii", blob, 0)
            assert (hdr_size, bw, bh) == (40, w, 2 * h)
        sizes.add(w)
    assert {16, 32, 48, 256} <= sizes


def test_icon_is_reproducible_from_its_generator():
    spec = importlib.util.spec_from_file_location("nd_make_icon", ROOT / "assets" / "make_icon.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    data, _ = mod.build_icon()
    assert data == ICON.read_bytes(), "assets/NeedleDropper.ico is stale: run python assets/make_icon.py"


# --------------------------------------------------------- license, git ----

def test_license_is_mit_with_the_project_copyright():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n")
    assert "Copyright (c) 2026 NeedleDropper contributors" in text
    for phrase in ("Permission is hereby granted, free of charge, to any person obtaining a copy",
                   "The above copyright notice and this permission notice shall be included in all",
                   'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND'):
        assert phrase in text, phrase


def test_gitignore_keeps_the_spec_and_ignores_build_output():
    lines = [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    patterns = [ln for ln in lines if ln and not ln.startswith("#")]
    assert "*.spec" not in patterns and not any(p.endswith(".spec") for p in patterns)
    for needed in ("/build/", "/dist/", "__pycache__/", ".venv/", "/.release/"):
        assert needed in patterns, needed
