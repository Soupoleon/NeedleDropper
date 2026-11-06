"""The app shell's command line (DESIGN-v1.2 1.4): no command = the companion in the tray, the
Connect page opened on the first run (deck.panelOnStart) and never at sign-in (--background);
one instance only (a second start finds the first through /healthz and opens its Connect page);
a port taken by another program is reported in a dialog; `run` is the console variant; the
retired v1.1 commands explain themselves. No real tray icon, browser or registry is touched."""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panel_fakes import FakeTrayIcon, FakeWinreg, make_fake_wow  # noqa: E402

from needledropper import __version__, autostart, cli  # noqa: E402
from needledropper.deck.server import Deck  # noqa: E402


@pytest.fixture
def home(_needledropper_home):
    return Path(_needledropper_home)


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    """No browser, tray icon, dialog or registry from these tests."""
    seen = {"urls": [], "dialogs": [], "reg": FakeWinreg()}
    monkeypatch.setattr(cli, "open_url", seen["urls"].append)
    monkeypatch.setattr(cli, "tray_factory", FakeTrayIcon)
    monkeypatch.setattr(cli, "fatal_dialog", lambda title, text: seen["dialogs"].append((title, text)))
    monkeypatch.setattr(autostart, "_registry", lambda winreg=None: winreg or seen["reg"])
    FakeTrayIcon.instances.clear()
    return seen


@pytest.fixture
def opened(hermetic):
    return hermetic["urls"]


def write_config(home: Path, tmp_path: Path, **cfg) -> Path:
    base = {"wowRoot": str(make_fake_wow(tmp_path / "WoW")),
            "transport": {"pixel": {"enabled": False}, "chatlog": {"enabled": False}}}
    base.update(cfg)
    p = home / "config.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


class _Other(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"app": "something-else"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def foreign_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Other)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------- single instance --

def test_probe_instance(foreign_server):
    with Deck(port=0) as d:
        assert cli.probe_instance(d.port) == {"app": "needledropper", "version": __version__}
    assert cli.probe_instance(foreign_server) is None  # something else answers on that port
    assert cli.probe_instance(free_port(), timeout=0.5) is None  # nothing listens


def test_a_second_start_opens_the_running_connect_page_and_exits(home, tmp_path, opened, capsys):
    with Deck(port=0) as running:
        write_config(home, tmp_path, deck={"port": running.port})
        assert cli.main([]) == 0
        assert opened == [f"http://127.0.0.1:{running.port}/"]
        out = capsys.readouterr().out
        assert "already running" in out and "NeedleDropper companion" not in out  # nothing else started
        assert FakeTrayIcon.instances == []  # and no second tray icon
        opened.clear()
        assert cli.main(["--background"]) == 0  # Start with Windows while it runs: quietly nothing
        assert cli.main(["run"]) == 0  # an explicit run only reports it
        assert opened == []
        assert cli.main(["setup"]) == 0  # "open setup" from a shortcut
        assert opened == [f"http://127.0.0.1:{running.port}/"]


def test_a_port_taken_by_another_program_is_shown_in_a_dialog(home, tmp_path, opened, hermetic, foreign_server):
    write_config(home, tmp_path, deck={"port": foreign_server})
    assert cli.main([]) == 1
    assert opened == [] and len(hermetic["dialogs"]) == 1
    title, text = hermetic["dialogs"][0]
    assert "could not start" in title and str(foreign_server) in text and "config.json" in text


def test_a_broken_config_is_shown_in_a_dialog_too(home, hermetic):
    (home / "config.json").write_text("{not json", encoding="utf-8")
    assert cli.main([]) == 2
    assert hermetic["dialogs"] and "config.json" in hermetic["dialogs"][0][0]


# ------------------------------------------------------------ panel policy --

@pytest.mark.parametrize("cfg, argv, opens", [
    ({"deck": {"port": 0}}, [], True),  # first run
    ({"deck": {"port": 0}, "setupComplete": True}, [], True),  # v1.1 config: the 1.2 Connect page once
    ({"deck": {"port": 0}, "setupComplete": True, "startWithWindows": False}, [], False),
    ({"deck": {"port": 0, "panelOnStart": "always"}, "setupComplete": True, "startWithWindows": True}, [], True),
    ({"deck": {"port": 0, "panelOnStart": "never"}}, [], False),
    ({"deck": {"port": 0}}, ["--background"], False),  # at sign-in: quiet even on the first run
])
def test_default_start_runs_in_the_tray_and_opens_the_connect_page_per_policy(home, tmp_path, opened, capsys, cfg,
                                                                              argv, opens):
    write_config(home, tmp_path, **cfg)
    assert cli.main([*argv, "--seconds", "0.3"]) == 0
    out = capsys.readouterr().out
    assert "Connect page: http://127.0.0.1:" in out and "Bye." in out and "Running in the tray" in out
    assert len(opened) == (1 if opens else 0)
    if opens:
        assert opened[0].startswith("http://127.0.0.1:") and opened[0].endswith("/")
    icon = FakeTrayIcon.instances[-1]
    assert icon.started and icon.stopped and icon.title == "NeedleDropper"


def test_without_a_tray_icon_the_connect_page_opens_instead(home, tmp_path, opened, monkeypatch, capsys):
    class NoTray(FakeTrayIcon):
        def start(self):
            raise RuntimeError("TrayIcon requires Windows")

    monkeypatch.setattr(cli, "tray_factory", NoTray)
    write_config(home, tmp_path, deck={"port": 0}, setupComplete=True, startWithWindows=False)
    assert cli.main(["--seconds", "0.3"]) == 0
    assert len(opened) == 1 and "press Ctrl+C to quit" in capsys.readouterr().out


def test_run_is_the_console_variant(home, tmp_path, opened, monkeypatch, capsys):
    from needledropper.deck import server

    launched: list = []
    monkeypatch.setattr(server, "_default_launcher", launched.append)
    monkeypatch.setattr(server, "find_browser", lambda kind="edge": "msedge.exe")
    write_config(home, tmp_path, deck={"port": 0, "autoOpen": "always"})
    assert cli.main(["run", "--panel", "--seconds", "0.3"]) == 0
    assert len(opened) == 1 and FakeTrayIcon.instances == []  # no tray icon in the console variant
    assert len(launched) == 1 and any(a.endswith("/player") for a in launched[0])  # autoOpen "always"
    out = capsys.readouterr().out
    assert "opens at start and for YouTube songs" in out and "Ctrl+C" in out
    assert (home / "logs" / "needledropper.log").is_file()


def test_ctrl_c_shuts_down_cleanly(home, tmp_path, opened, monkeypatch, capsys):
    def interrupt(event, timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_wait_quit", interrupt)
    write_config(home, tmp_path, deck={"port": 0, "panelOnStart": "never"})
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Shutting down..." in out and "Bye." in out
    assert FakeTrayIcon.instances[-1].stopped


# ---------------------------------------------------------------- parser ----

def test_parser_commands_and_retired_ones(capsys):
    args = cli.parse_args([])
    assert args.func is cli.cmd_default and args.command is None and args.background is False
    assert cli.parse_args(["--background"]).background is True
    assert cli.parse_args(["run"]).func is cli.cmd_run
    assert cli.parse_args(["setup"]).func is cli.cmd_setup
    for kept in ("diag", "zones", "validate", "spotify-login", "install-addon"):
        assert cli.parse_args([kept] if kept not in ("test-drop",) else [kept, "x"]).command == kept
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    text = capsys.readouterr().out
    assert "build-map" not in text and "resolve" not in text and "tray" in text
    for retired in ("build-map", "resolve"):
        assert cli.main([retired, "--out", "x"]) == 2
        assert "removed in NeedleDropper 1.2" in capsys.readouterr().out


def test_packs_commands_are_registered_when_present_and_optional_otherwise(monkeypatch):
    import importlib

    real = importlib.import_module

    def missing(name, package=None):
        if name == ".commands_packs":
            raise ImportError("no module named commands_packs")
        return real(name, package)

    monkeypatch.setattr(cli.importlib, "import_module", missing)
    ap = cli.build_parser()  # still builds without the packs tools
    assert ap.parse_args(["zones"]).command == "zones"

    class Fake:
        @staticmethod
        def register(sub):
            p = sub.add_parser("packs", help="pack tools")
            p.set_defaults(func=lambda args: 7)

    monkeypatch.setattr(cli.importlib, "import_module",
                        lambda name, package=None: Fake if name == ".commands_packs" else real(name, package))
    assert cli.main(["packs"]) == 7

    class Broken:
        @staticmethod
        def register(sub):
            raise RuntimeError("bug in the packs tools")

    monkeypatch.setattr(cli.importlib, "import_module",
                        lambda name, package=None: Broken if name == ".commands_packs" else real(name, package))
    assert cli.build_parser().parse_args(["zones"]).command == "zones"


def test_describe_event_for_v1_and_v2():
    from needledropper import protocol2
    from needledropper.models import Event, EventType, Flags

    v2 = protocol2.build_frame(EventType.FIRE, seq=3, flags=int(Flags.TEST), drop_id="zone:1429", youtube="dQw4w9WgXcQ")
    assert "FIRE seq=3" in cli.describe_event(v2, "pixel") and cli.describe_event(v2, "pixel").endswith("[pixel]")
    v1 = Event(EventType.HELLO, 5, 0, 77, Flags.NONE)
    assert cli.describe_event(v1) == "v1 HELLO seq=5 n=0 hash=77 flags=0x00"


def test_test_drop_frames(home, tmp_path):
    from needledropper.models import EventType, Flags

    write_config(home, tmp_path)
    ctx = cli.Ctx(cli.parse_args(["zones"]))
    frame, what = cli.make_test_frame(ctx, "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc")
    assert frame.type == EventType.FIRE and frame.flags & Flags.TEST and frame.spotify_id == "4uLU6hMCjMI75M1A2tKUQC"
    frame, what = cli.make_test_frame(ctx, "https://youtu.be/dQw4w9WgXcQ")
    assert frame.youtube_id == "dQw4w9WgXcQ"
    frame, what = cli.make_test_frame(ctx, "Toto - Africa")
    assert (frame.artist, frame.title) == ("Toto", "Africa") and what == "search: Toto - Africa"
    frame, what = cli.make_test_frame(ctx, "Elwynn Forest")  # a drop of the bundled packs
    assert frame is not None and frame.drop_id == "zone:1429" and frame.label
    frame, what = cli.make_test_frame(ctx, "Narnia")
    assert frame is None and "No drop matches" in what
