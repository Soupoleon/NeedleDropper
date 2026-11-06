## What and why

<!-- One or two sentences: what changes for players (or for releases), and why. Link the issue: "Fixes #123". -->

## How I tested it

<!-- Tests you ran, in-game steps (zone, commands), screenshots for UI changes. -->

## Checklist

- [ ] `python -m pytest tests -q` passes.
- [ ] `python tools/gen_sets.py --check` passes (when `packs/` or `addon/NeedleDropper/Sets/` changed).
- [ ] `CHANGELOG.md` has a line under the next version for anything players will notice.
- [ ] The docs say what changed (README, `docs/INSTALL.md`, `docs/TROUBLESHOOTING.md`, `docs/PRIVACY.md`, ...).
- [ ] TOC changed? `docs/CONTRACTS-v1.2.md` §2.1 and `EXPECTED_TOC` in `tests/test_addon_misc.py` match it.
- [ ] Nothing in `addon/` that addon sites refuse: no programs, no audio except `media/silence.mp3`.
- [ ] Nothing new leaves the player's PC, or `docs/PRIVACY.md` says what and why.
- [ ] Addon UI changes: checked in game as in `docs/FIRST-TEST.md` (window, Escape, combat, taint), with keyboard and with a controller.
- [ ] Release PR: `__version__` in `companion/needledropper/__init__.py` and `version` in `companion/pyproject.toml` are the version the tag will carry (the release workflow refuses a tag that doesn't match).
