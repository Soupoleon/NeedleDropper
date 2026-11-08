"""Execute the real release preflight against isolated version/site metadata.

No publishing token or network is used. Git's commit lookup is stubbed; the real
Bash/Python tag, version, channel and dry-run decisions run unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40


def preflight(tmp_path, tag, *, dry_run=False, site_uploads="", cf_token=True):
    bash = (Path(r"C:\Program Files\Git\bin\bash.exe") if os.name == "nt"
            else shutil.which("bash"))
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash is required for the release workflow preflight")
    source = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    section = source.split("      - name: Check the tag, the versions and the upload targets\n", 1)[1]
    block = section.split("        run: |\n", 1)[1]
    block = re.split(r"\n  [\w-]+:\n", block, maxsplit=1)[0]
    script = textwrap.dedent(block)
    interpreter = shlex.quote(sys.executable.replace("\\", "/"))
    script = (f'python3() {{ {interpreter} "$@"; }}\n'
              f'git() {{ [[ "$*" == "rev-parse HEAD" ]] && printf "%s\\n" "{COMMIT}"; }}\n'
              + script)

    init = tmp_path / "companion/needledropper/__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "companion/pyproject.toml").write_text('version = "1.2.3"\n', encoding="utf-8")
    toc = tmp_path / "addon/NeedleDropper/NeedleDropper.toc"
    toc.parent.mkdir(parents=True)
    toc.write_text("## Version: 1.2.3\n## X-Curse-Project-ID: 123456\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")
    outputs, summary = tmp_path / "outputs.txt", tmp_path / "summary.md"
    env = dict(os.environ, TAG=tag, DRY_RUN=str(dry_run).lower(), SITE_UPLOADS=site_uploads,
               HAS_CF_TOKEN=str(cf_token).lower(), HAS_WAGO_TOKEN="false", HAS_WOWI_TOKEN="false",
               GITHUB_OUTPUT=outputs.as_posix(), GITHUB_STEP_SUMMARY=summary.as_posix())
    result = subprocess.run([str(bash), "-s"], input=script, text=True, capture_output=True,
                            cwd=tmp_path, env=env, timeout=20)
    values = dict(line.split("=", 1) for line in outputs.read_text(encoding="utf-8").splitlines()) \
        if outputs.exists() else {}
    return result, values, summary.read_text(encoding="utf-8") if summary.exists() else ""


@pytest.mark.parametrize("tag,channel,prerelease", [
    ("v1.2.3-beta.5", "beta", "true"),
    ("v1.2.3-alpha.2", "alpha", "true"),
    ("v1.2.3", "release", "false"),
])
def test_tag_selects_the_site_channel_and_freezes_the_tested_commit(tmp_path, tag, channel, prerelease):
    result, values, summary = preflight(tmp_path, tag)
    assert result.returncode == 0, result.stderr + result.stdout
    assert values["tag"] == tag and values["base"] == "1.2.3"
    assert values["channel"] == channel and values["prerelease"] == prerelease
    assert values["sha"] == COMMIT and values["upload"] == "true"
    assert "upload to project 123456" in summary and "| Mode | publish |" in summary


@pytest.mark.parametrize("tag", ["v1.2.3-rc.1", "v1.2", "main", "v1.2.4-beta.1"])
def test_invalid_or_mismatched_version_never_enables_upload(tmp_path, tag):
    result, values, _ = preflight(tmp_path, tag)
    assert result.returncode != 0
    assert not values


def test_manual_dry_run_cannot_enable_site_uploads_even_with_a_configured_token(tmp_path):
    result, values, summary = preflight(tmp_path, "v1.2.3-beta.5", dry_run=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert values["upload"] == "false" and values["sha"] == COMMIT
    assert "dry run: no publishing" in summary and "dry run: no upload" in summary


def test_explicit_github_only_switch_is_preserved(tmp_path):
    result, values, summary = preflight(tmp_path, "v1.2.3-beta.5", site_uploads="off")
    assert result.returncode == 0, result.stderr + result.stdout
    assert values["upload"] == "false" and "ADDON_SITE_UPLOADS" in summary
