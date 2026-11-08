# Releasing NeedleDropper (phased public release)

NeedleDropper ships as two pieces, and they go to different places:

| Piece | Where it goes | How players update |
|---|---|---|
| **Addon** (`NeedleDropper/` folder) | GitHub Releases and CurseForge; other sites only after configuration | During CurseForge review, download the addon zip from GitHub. After listing approval, supported addon managers can update it |
| **App** (`NeedleDropper.exe`, optional, needed only for Spotify and YouTube) | GitHub Releases | Once `updates.releasesUrl` is configured (empty in 1.2.1), the app checks stable releases daily. It never reports pre-releases or installs an update itself; send testers each new beta link |

Addon sites don't host Windows executables, so the app lives on GitHub and the addon page links to it. This is the same arrangement as the Raider.IO client and the WeakAuras Companion. The addon also works on its own (title cards, in-game audio files), which keeps the listing legitimate.

## Phases

| Phase | Audience | Channel | Exit criteria |
|---|---|---|---|
| **0: Initial friend testing** | Owner and invited testers | Public GitHub pre-release `v1.2.1-beta.N`; both assets downloaded there while CurseForge reviews | The FIRST-TEST.md checklist passes on 2+ PCs (different screens, keyboard + gamepad); Spotify and YouTube drops play; no taint |
| **1: Public beta** | Anyone who opts in | Public GitHub pre-release and CurseForge **Beta** channel after approval; other sites only when configured | 2-3 weeks without critical bugs; share codes tested between players |
| **2: Stable** | Everyone | **Release** channel | Crash-free; docs complete; exe code-signed (optional but removes the SmartScreen warning) |

Version tags pick the channel automatically:
- `v1.2.1-alpha.1` → Alpha
- `v1.2.1-beta.1` → Beta
- `v1.2.1` → Release

The addon and the app of one release always carry the same version (`tests/test_core_version.py`
checks `__version__`, `pyproject.toml`, the TOC's `## Version:`, `Core.lua` and the newest
CHANGELOG entry). Ship them together: a 1.2.0 app and a 1.2.1 addon can't read each other's beacon
signals.

## Automation (one tag → every site)

Publishing is automatic when a version tag is pushed. Ordinary pushes to `main` and pull requests
run **CI**; they do not publish. Both CI and Release call the same [test workflow](../.github/workflows/tests.yml).

The [Release workflow](../.github/workflows/release.yml) follows this sequence:

1. Validate the tag and versions, then record the tag's exact commit.
2. Run the tests, generated soundtrack check and byte-compilation on Python **3.10, 3.12 and 3.14**.
   Release tests also include the frozen-launcher checks on 3.14. Every version must pass.
3. Build the EXE and ZIPs **once**, from that tested commit. Check their contents and SHA256 sums.
4. Publish the GitHub release with all five files.
5. Package the addon with **BigWigs packager**, check the addon-only ZIP, and upload to CurseForge
   with the tag's Alpha, Beta or Release channel. Other sites remain optional.

Python package downloads are cached. Tag pushes no longer start a second CI build or a redundant
fourth test run. The publishing jobs depend on the entire test matrix, so a failed version blocks
both GitHub publication and CurseForge upload.

For the next beta, after committing the changes and updating the changelog:

```powershell
git push origin main
git tag -a v1.2.1-beta.5 -m "NeedleDropper 1.2.1 beta 5"
git push origin v1.2.1-beta.5
```

Choose a new tag each time; never move an already published tag. Base versions must match the tag
(the current base is 1.2.1). The `CF_API_KEY` secret and CurseForge project ID are already configured.
CurseForge may review a file after accepting the upload; the workflow cannot approve moderation.
CurseForge updates the addon; the Windows companion remains a separate GitHub download.

### Test release packaging without publishing

In **Actions → Release → Run workflow**, choose the current branch and an existing version tag.
Or run:

```powershell
gh workflow run release.yml --ref main -f tag=v1.2.1-beta.4
```

Every manual run is a **dry run**: it validates the selected tag, runs all three test versions and
builds/checks the release files. Download the five files from the run's artifact. It never creates
or changes a GitHub release, and it skips addon-site publishing. Actual publishing requires a tag push.

The betas use the addon's English fallback on every client language. Both packager calls pass `-l`
to skip optional CurseForge localization exports, which currently reject project access. File
uploads remain enabled. When translations and localization access are ready, remove `-l` from
both calls in `release.yml`.

What it needs, once:

1. **A public GitHub repo**, so testers and addon-site reviewers can download the app and source
   without invitations. NeedleDropper is public for beta.1.
2. **Site accounts and projects**, which only the owner can create:
   - CurseForge author account (curseforge.com): create the project "NeedleDropper", game *World of Warcraft*, flavor WoW Forever. Copy the **Project ID** into the TOC as `## X-Curse-Project-ID:` and create an **API token** (Account → API tokens).
   - Wago Addons (addons.wago.io): create the project, add `## X-Wago-ID:` and a token.
   - WoWInterface (optional): `## X-WoWI-ID:` and a token.
3. **GitHub repository secrets:** `CF_API_KEY`, `WAGO_API_TOKEN`, `WOWI_API_TOKEN` (and later the code-signing secrets).
4. **Listing assets:** a short description, the title-card screenshot, a "Requires the free NeedleDropper app for Spotify/YouTube" note with the GitHub link, and the privacy and policy notes (docs/PRIVACY.md, docs/POLICY.md).

## Policy checklist before going public

- **Blizzard UI Add-On Development Policy:** free, no ads, no obfuscation, no harmful performance. The companion is strictly read-only toward the game: screen pixels and an opt-in chat log, never memory or input.
- **Addon sites:** no executables inside the addon zip (the build already guarantees this). Disclose the optional external app clearly.
- **No audio is distributed.** Soundtracks contain only metadata (titles, artists, YouTube and Spotify ids).
- **Spotify** (Developer Policy, Feb 2026 dev-mode rules):
  - A developer app allows at most 5 users. For the public, every player creates their own free app (the in-game and Connect-page setup guides walk through it).
  - Never publish a shared Client ID for the public.
  - Beta testers create their own developer app too; do not distribute the owner's Client ID.
  - The "synchronising recordings with visual media" clause is a grey area, flagged in POLICY.md.
- **YouTube:** the visible player window satisfies the API rules. Never a hidden player.

## Friends beta: what a tester needs

Beta 4 updates companion cleanup and Spotify diagnostics. An existing beta 3 addon remains
compatible; testers on earlier betas should update both parts for the volume setting, minimap icon
and location fixes. Keep providing all five release assets, including the addon zip for new installs.
Do not describe this release as a confirmed fix for Spotify playback failures; playback needs a retest.

1. WoW: Forever on Windows.
2. The WoW: Forever Beta addon from [CurseForge](https://www.curseforge.com/wow/addons/needledropper), or `NeedleDropper-1.2.1-addon.zip` from the [beta release](https://github.com/Soupoleon/NeedleDropper/releases/tag/v1.2.1-beta.4), extracted into `World of Warcraft\_forever_\Interface\AddOns\`.
3. `NeedleDropper.exe` (optional) from the same release. Windows SmartScreen may warn because the build is unsigned: *More info → Run anyway*. Check `SHA256SUMS.txt`. Start the app before WoW and give it a moment on the desktop; it never opens its player while the game is in front.
4. Music (one of):
   - **Spotify:** Premium and their own developer app; create an app in the Spotify dashboard, set redirect URI `http://127.0.0.1:8321/callback`, tick **Web API** and **Web Playback SDK**, paste its Client ID on the Connect page and sign in. For **Play on: NeedleDropper (this app)**, install Google Chrome. To use the separate Spotify desktop app, open Spotify on the gaming PC, click **Refresh** beside **Play on**, and select that PC; desktop mode does not need Chrome. Full steps: [BETA-TESTING.md](BETA-TESTING.md).
   - **YouTube only:** nothing to set up.
5. In-game **Music services → Test Spotify**, then **Test YouTube**; each tests only its own service. **Send a test signal** and `/needle test` check only the link. Full checklist: [FIRST-TEST.md](FIRST-TEST.md).
6. Where to report: GitHub Issues (bug template). Paste `/needle report` (Ctrl+C). The app's log (tray → **Open log folder**) names the Spotify account and may contain personal paths: send it privately or redact it before posting.
