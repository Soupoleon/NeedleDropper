"""The player documentation (SPEC-v1.1 D) stays accurate and complete.

Facts are taken from the code at test time, not copied: the /needle subcommands and settings
from addon/NeedleDropper/Core.lua, the Spotify redirect URI and the ports from the companion, the
pack keys from packs.py. Every relative link and #anchor between the docs must resolve, and no
doc may present Apple Music as supported.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CORE_LUA = ROOT / "addon" / "NeedleDropper" / "Core.lua"

GUIDES = {
    "README": ROOT / "README.md",
    "CHANGELOG": ROOT / "CHANGELOG.md",
    "INSTALL": DOCS / "INSTALL.md",
    "PROVIDERS": DOCS / "PROVIDERS.md",
    "PACKS": DOCS / "PACKS.md",
    "PRIVACY": DOCS / "PRIVACY.md",
    "POLICY": DOCS / "POLICY.md",
    "TROUBLESHOOTING": DOCS / "TROUBLESHOOTING.md",
}

_FENCE = re.compile(r"^(```|~~~)")


def read(name: str) -> str:
    return GUIDES[name].read_text(encoding="utf-8")


def flat(name: str) -> str:
    """The guide with all whitespace runs (line wraps included) collapsed to one space."""
    return " ".join(read(name).split())


def prose(text: str) -> str:
    """The text without fenced code blocks."""
    out, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


def gh_slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation (except - and _) removed, spaces -> '-'."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = text.replace("`", "").replace("*", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    for line in prose(path.read_text(encoding="utf-8")).splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not m:
            continue
        slug = gh_slug(m.group(2))
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


# ------------------------------------------------------------- from code ----

def lua_commands() -> set[str]:
    src = CORE_LUA.read_text(encoding="utf-8")
    names: set[str] = set()
    m = re.search(r"local\s+ORDER\s*=\s*\{(.*?)\}", src, re.S)
    assert m, "ORDER not found in Core.lua"
    names |= set(re.findall(r'"(\w+)"', m.group(1)))
    m = re.search(r"\nCOMMANDS\s*=\s*\{\n(.*?)\n\}\n", src, re.S)
    assert m, "COMMANDS table not found in Core.lua"
    names |= set(re.findall(r"^\s{2}(\w+)\s*=\s*function\b", m.group(1), re.M))
    m = re.search(r"local\s+USAGE\s*=\s*\{(.*?)\n\}", src, re.S)
    if m:
        names |= set(re.findall(r"^\s*(\w+)\s*=", m.group(1), re.M))
    return names


def lua_settings() -> list[str]:
    src = CORE_LUA.read_text(encoding="utf-8")
    m = re.search(r"ns\.SETTINGS\s*=\s*\{(.*?)\n\}", src, re.S)
    assert m, "ns.SETTINGS not found in Core.lua"
    return re.findall(r'\{\s*key\s*=\s*"(\w+)"', m.group(1))


def redirect_uri() -> str:
    from needledropper.config import DEFAULT_CONFIG
    from needledropper.providers.spotify_auth import CALLBACK_PATH, DEFAULT_PORT

    assert DEFAULT_CONFIG["spotify"]["redirectPort"] == DEFAULT_PORT
    return f"http://127.0.0.1:{DEFAULT_PORT}{CALLBACK_PATH}"


def panel_url() -> str:
    from needledropper.config import DEFAULT_CONFIG

    return f"http://127.0.0.1:{DEFAULT_CONFIG['deck']['port']}/"


# ------------------------------------------------------------------ tests ----

@pytest.mark.parametrize("name", sorted(GUIDES))
def test_guide_exists_and_is_finished(name):
    text = read(name)
    assert len(text) > 1500, f"{name} looks like a stub"
    assert text.startswith("# "), f"{name} should start with a title"
    for marker in ("TODO", "TBD", "FIXME", "lorem ipsum", "example.com", "XXX"):
        assert marker.lower() not in text.lower(), f"{name} contains {marker!r}"


def test_lua_command_parser_sees_the_commands():
    cmds = lua_commands()
    assert {"help", "status", "where", "journal", "replay", "play", "test", "stop", "skip", "reset",
            "beacon", "chatlog", "selftest", "debug", "set", "settings", "options"} <= cmds


def test_command_reference_covers_every_needle_subcommand():
    ref = read("TROUBLESHOOTING")
    missing = [c for c in sorted(lua_commands()) if not re.search(rf"/needle {c}(?![\w-])", ref)]
    assert not missing, f"TROUBLESHOOTING.md command reference lacks: {', '.join(missing)}"


def test_docs_only_mention_real_needle_subcommands():
    cmds = lua_commands()
    for name in GUIDES:
        # commands are written as code: `/needle status`, `/needle set cooldownMin 15`, ...
        for cmd in re.findall(r"`/needle\s+([A-Za-z]\w*)", read(name)):
            assert cmd.lower() in cmds, f"{name} mentions /needle {cmd}, which Core.lua doesn't have"


def test_slash_aliases_documented():
    ref = read("TROUBLESHOOTING")
    src = CORE_LUA.read_text(encoding="utf-8")
    for alias in re.findall(r'SLASH_NEEDLEDROPPER\d\s*=\s*"(/\w+)"', src):
        assert f"`{alias}`" in ref, alias


def test_every_in_game_setting_is_documented():
    ref = read("TROUBLESHOOTING")
    settings = lua_settings()
    assert len(settings) >= 20
    missing = [k for k in settings if f"`{k}`" not in ref]
    assert not missing, f"TROUBLESHOOTING.md in-game options table lacks: {', '.join(missing)}"


def test_spotify_redirect_uri_is_exact():
    uri = redirect_uri()
    assert uri == "http://127.0.0.1:8321/callback"
    for name in ("PROVIDERS", "TROUBLESHOOTING"):
        assert f"`{uri}`" in read(name), name
    for name in GUIDES:
        text = read(name)
        assert "localhost:8321" not in text and "https://127.0.0.1:8321" not in text, name
        assert "8321/callback/" not in text, name


def test_panel_address_matches_the_deck_port():
    url = panel_url()
    for name in ("INSTALL", "PROVIDERS", "TROUBLESHOOTING"):
        assert url in read(name), name


_NEGATION = re.compile(r"not supported|isn't supported|unsupported|ignored|removed|dropped", re.I)
_APPLE = re.compile(r"apple\s*music", re.I)


def _blocks(text: str):
    """Paragraphs (blank-line separated), with every table row as its own block; headings skipped."""
    for block in re.split(r"\n\s*\n", prose(text)):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if all(ln.lstrip().startswith("|") for ln in lines):
            yield from lines
            continue
        body = [ln for ln in lines if not ln.lstrip().startswith("#")]
        if body:
            yield "\n".join(body)


@pytest.mark.parametrize("name", sorted(GUIDES))
def test_no_doc_presents_apple_music_as_supported(name):
    for block in _blocks(read(name)):
        if _APPLE.search(block):
            assert _NEGATION.search(block), f"{name}: mentions Apple Music without saying it is unsupported:\n{block}"
    assert not re.search(r'"providers"\s*:\s*\[[^\]]*applemusic', read(name), re.I), name


def test_apple_music_statement_matches_the_spec():
    text = read("PROVIDERS")
    assert "not supported in v1" in text and "Windows offers no way to control it" in text


def test_relative_links_and_anchors_resolve():
    problems = []
    link_re = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
    for name, path in GUIDES.items():
        for target in link_re.findall(prose(path.read_text(encoding="utf-8"))):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):  # http:, https:, mailto:
                continue
            file_part, _, anchor = target.partition("#")
            dest = (path.parent / file_part).resolve() if file_part else path.resolve()
            if not dest.exists():
                problems.append(f"{name}: {target} -> missing file")
                continue
            if anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                problems.append(f"{name}: {target} -> no heading #{anchor} in {dest.name}")
    assert not problems, "\n".join(problems)


def test_readme_pitch_diagram_quick_start_and_links():
    text = read("README")
    assert "```mermaid" in text and "flowchart" in text
    assert "Kojima" in text and "needle drop" in text.lower()
    section = text.split("## Quick start", 1)[1].split("\n## ", 1)[0]
    steps = re.findall(r"^(\d+)\. ", section, re.M)
    assert steps == ["1", "2", "3", "4", "5"], steps
    for target in ("docs/INSTALL.md", "docs/PROVIDERS.md", "docs/PACKS.md", "docs/PRIVACY.md",
                   "docs/POLICY.md", "docs/TROUBLESHOOTING.md", "LICENSE", "CHANGELOG.md"):
        assert f"]({target}" in text, target
    assert "MIT" in text and "Credits" in text


def test_install_guide_covers_the_public_release():
    text = flat("INSTALL")
    # "Fullscreen (Windowed)" is the client's own label (GlobalStrings VIDEO_OPTIONS_WINDOWED_FULLSCREEN); the
    # v1.1 docs had it backwards ("Windowed (Fullscreen)"), which this test used to require.
    for needed in ("NeedleDropper.exe", "-addon.zip", "-win64.zip", "Windows protected your PC", "More info",
                   "Run anyway", "Fullscreen (Windowed)", "/reload", "restart the game", "Uninstalling",
                   "NeedleDropper_Local", "%APPDATA%\\NeedleDropper", "addon manager",
                   "_forever_\\Interface\\AddOns", "NeedleDropper_Local\\media", "Start with Windows",
                   "/needle test"):
        assert needed in text, needed
    # v1.2: the Connect page's four cards (DESIGN-v1.2 1.4) replaced the v1.1 control panel's sections
    for card in ("**World of Warcraft**", "**Spotify**", "**YouTube**", "**Check the link**"):
        assert card in text, card


def test_providers_guide_covers_the_three_services():
    from needledropper.packs import IN_GAME_MEDIA_PREFIX

    text = flat("PROVIDERS")
    for needed in ("https://developer.spotify.com/dashboard", "Create app", "Client ID", "Web API",
                   "User Management", "5 users", "Premium", "403", "404", "Play on",
                   "Resolve soundtrack", "Disconnect", "visible", "Click to enable sound", "ads",
                   "%APPDATA%\\NeedleDropper\\media", "restart the game", ".mp3", ".ogg", "inGameFile"):
        assert needed in text, needed
    assert IN_GAME_MEDIA_PREFIX.rstrip("\\") in text
    assert re.search(r"200\s*[×x]\s*200", text)


def test_packs_guide_documents_every_pack_key():
    from needledropper import packs

    text = flat("PACKS")
    assert f'"{packs.PACK_FORMAT}"' in text
    for key in sorted(packs.KNOWN_DROP_KEYS) + ["format", "version", "name", "author", "description", "drops"]:
        assert f"`{key}`" in text, key
    for needed in ("/needle where", "Rebuild map", "/reload", "zone:", "area:", "sub:", "inst:",
                   "validate", "build-map", "oembed"):
        assert needed in text, needed


def test_privacy_guide():
    from needledropper.config import default_config, paths_from_config

    paths = paths_from_config(default_config())
    text = flat("PRIVACY")
    for needed in ("DPAPI", "WoWChatLog.txt", "beacon", "Spotify", "YouTube", paths.token_file.name,
                   paths.log_file.name, "config.json", "127.0.0.1", "all chat"):
        assert needed.lower() in text.lower(), needed


def test_policy_guide():
    text = flat("POLICY")
    for needed in ("UI Add-On Development Policy", "memory", "inject", "III.6", "III.2", "own app",
                   "Development Mode", "background player", "ads", "No audio is distributed",
                   "silence.mp3"):
        assert needed.lower() in text.lower(), needed
    assert re.search(r"200\s*[×x]\s*200", text)


def test_troubleshooting_guide():
    from needledropper.config import default_config, paths_from_config

    text = flat("TROUBLESHOOTING")
    for needed in ("/needle selftest beacon", "/needle status", "Windowed", "HDR", "gamma", "overlay",
                   "Link", "cooldown", "historyMode", "403", "404", "429", "101", "150", "153",
                   "chat-log link", paths_from_config(default_config()).log_file.name, "diag"):
        assert needed.lower() in text.lower(), needed


def test_changelog_has_an_entry_for_this_version():
    from needledropper import __version__

    text = read("CHANGELOG")
    assert re.search(rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", text, re.M), __version__
    newest = re.search(r"^## \[([^\]]+)\]", text, re.M).group(1)
    assert newest == __version__, "the newest CHANGELOG entry should be the current version"
