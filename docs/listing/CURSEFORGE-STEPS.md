# Putting NeedleDropper on CurseForge

NeedleDropper's project is created. The **Release** workflow publishes each GitHub pre-release
and uploads its **Beta** addon to CurseForge after green CI. CurseForge accepted the first upload,
`v1.2.1-beta.1`, in [run 42333788920](https://github.com/Soupoleon/NeedleDropper/actions/runs/42333788920).
Get the addon from CurseForge and the companion from GitHub; [GitHub Releases](https://github.com/Soupoleon/NeedleDropper/releases)
also provide both parts while a CurseForge project or file is awaiting review.

## Recorded status, 2026-11-06

- **Project 1786392:** created; the first Beta upload was accepted. Public moderation approval has not been verified.
- **Description:** LISTING.md's long description, using Markdown.
- **Categories:** Audio & Video, Miscellaneous and Roleplay.
- **License:** MIT. **Third-party distribution:** allowed.
- **Issues:** External, `https://github.com/Soupoleon/NeedleDropper/issues`.
- **Image:** the featured Razor Hill screenshot is uploaded.
- **Summary and logo:** the owner's existing choices; keep them.
- **Automation:** `CF_API_KEY` is configured in GitHub Actions; the addon TOC contains
  `## X-Curse-Project-ID: 1786392` and `## Version: 1.2.1`.
- **GitHub:** the repository is public for beta.1.
- **Still pending:** verify public CurseForge availability and set the **Source** link.
  Now that GitHub is public, open [CurseForge Authors](https://authors.curseforge.com) →
  NeedleDropper → **Source** → **GitHub**, set `https://github.com/Soupoleon/NeedleDropper`,
  then **Save**. CurseForge refused this link while the repository was private.

The setup instructions below are reference material. Do not recreate the project, replace the
owner's summary/logo, or manually upload a file already accepted through the workflow.

Values marked *(verify)* weren't confirmed on the live site yet. Check them on screen as you go.

## What you need

| What | File |
|---|---|
| Logo (CurseForge wants exactly 400 × 400) | [`assets/logo/needledropper-logo-400.png`](../../assets/logo/needledropper-logo-400.png) |
| Lead screenshot | [`assets/screenshots/title-card-razor-hill.png`](../../assets/screenshots/title-card-razor-hill.png) |
| Summary, description, categories | [LISTING.md](LISTING.md) |
| Screenshot caption | [screenshots.md](screenshots.md), shot 1 (the same picture; the shot list calls the file `01-title-card-razor-hill.png`) |
| The addon zip | `dist\NeedleDropper-<version>-addon.zip`, built fresh in step 0 |

## 0. Before you start

1. **Make the GitHub repository public** (*Settings → General → Danger Zone → Change repository
   visibility*). The listing links to the source, the issues and the app download, and moderators
   click those links.
2. **Build a fresh addon zip.** Build from the tested release commit. In
   PowerShell, in the repository folder:

   ```powershell
   .\tools\build_release.ps1 -OutDir dist
   ```

   Open `dist\NeedleDropper-1.2.1-addon.zip` and check that it holds one `NeedleDropper` folder, and
   that its `NeedleDropper.toc` says `## Version: 1.2.1` (a real number, not `@project-version@`).
3. **A CurseForge account.** Sign in at <https://www.curseforge.com>, or create an account there.

## 1. Create the project

1. Open <https://authors.curseforge.com/#/projects/create/choose-game> (or
   <https://authors.curseforge.com> → **Create project**).
2. Choose **World of Warcraft**, then **Addons** if it asks for a class.
3. Fill in the form:

| Field | Value |
|---|---|
| Name | `NeedleDropper` |
| Summary | Keep the owner's existing summary |
| Main category | **Audio & Video** |
| Additional categories | **Miscellaneous** and **Roleplay** (already set) |
| License | **MIT License** |
| Logo | Keep the owner's existing logo |
| Description | The **Long description** box in [LISTING.md](LISTING.md) (pick Markdown if the editor asks). Replace `{{CURSEFORGE_URL}}` with the project's address, probably `https://www.curseforge.com/wow/addons/needledropper` *(verify)*, and delete the Wago line unless you set up Wago. |
| Source | `https://github.com/Soupoleon/NeedleDropper` |
| Issues | `https://github.com/Soupoleon/NeedleDropper/issues` |
| Wiki, donation | Leave them empty |

4. Save. The project now exists but isn't public yet. It goes to the moderators once it has a file
   (step 3).

## 2. Add the screenshot

In the project, open **Images** (or **Gallery**) *(verify)* and upload:

- **File:** `assets/screenshots/title-card-razor-hill.png`
- **Title:** `Razor Hill`
- **Description:** `The first time you arrive somewhere, the zone music fades, a song starts and the title card drifts in.`
- Tick **Featured** if the form offers it, so this picture leads the page.

## 3. First Beta upload

The normal path is the **Release** workflow in step 5, including beta.1. Check its **addon-sites**
job for the CurseForge file ID and confirm the file is **Beta** on the project. The GitHub release
must contain the addon zip, Windows zip, Windows folder zip, `NeedleDropper.exe` and `SHA256SUMS.txt`.

Use this manual form only if the automated upload failed and you have confirmed it created no file:

In the project, open **Files** → **Upload file**:

| Field | Value |
|---|---|
| File | `dist\NeedleDropper-1.2.1-addon.zip` (from step 0) |
| Display name | `v1.2.1-beta.1` |
| Release type | **Beta** |
| Game version | **WoW: Forever 1.60.1** (client 1.60.1.70790, TOC `## Interface: 16001`). The exact name in the list *(verify)* |
| Changelog | The text below. Pick Markdown if the form asks. |
| Release options | Publish as soon as it's approved |
| Related projects | None |

```markdown
First public beta of NeedleDropper 1.2.1.

- One hand-picked song and a cinematic title card, the first time you arrive somewhere new.
- Everything happens in game: type /needle for the Drops, Soundtracks and Journal tabs. Add drop here, paste a Spotify or YouTube link, and Test it.
- Two built-in soundtracks, Azeroth Mixtape and Azeroth Unbound, plus your own. Share them with a code.
- Music services in Options > AddOns > NeedleDropper: set up Spotify, choose which service plays, test both.
- Spotify and YouTube songs need the free NeedleDropper app for Windows, a separate download: https://github.com/Soupoleon/NeedleDropper/releases (Spotify: Premium and Google Chrome).
- This is a beta. Please report bugs at https://github.com/Soupoleon/NeedleDropper/issues

Full notes: https://github.com/Soupoleon/NeedleDropper/blob/main/CHANGELOG.md
```

Click **Upload**. The file shows **Under review** and the project is now in the moderators' queue.
That can take a few days *(verify)*. If they ask for changes, they'll message you.

> **Beta-only projects don't reach the CurseForge App yet.** CurseForge's help pages say a project
> needs at least one **Release** file before it syncs to the CurseForge App *(verify)*. Until then,
> testers download the zip from the project's **Files** tab (or from GitHub). The first Release
> file (`v1.2.1`, or whichever version is first released) brings the project into the app.

## 4. Release workflow setup (already completed)

The project's ID and `CF_API_KEY` are configured. These steps are retained for maintenance; the
first automated upload does not need to wait for project approval.

1. **Project ID.** The **About Project** box on the project page shows **Project ID**, a number. It
   may show up before approval too *(verify)*. The TOC already has NeedleDropper's
   (`## X-Curse-Project-ID: 1786392`, see [curseforge-project.md](curseforge-project.md)); for
   another project, put its number in that line of `addon/NeedleDropper/NeedleDropper.toc`, under
   `## X-License: MIT`:

   ```
   ## X-Curse-Project-ID: 123456
   ```

   Then commit and push to `main`.
2. **API token.** Open <https://authors.curseforge.com/#/settings/api-tokens>, name the token
   `NeedleDropper GitHub`, generate it and copy it. It may be shown only once *(verify)*.
3. **GitHub secret.** In the repository, open *Settings → Secrets and variables → Actions → New
   repository secret*. Name it `CF_API_KEY`, exactly that: the workflow hands it to the packager as
   `CF_API_TOKEN`. Paste the token as the secret and click **Add secret**.

The token belongs only in that secret. Never put it in the TOC, a file, an issue or a chat.

## 5. From then on, one tag uploads everything

The Release workflow ([release.yml](../../.github/workflows/release.yml), the source of truth)
runs on every version tag:

Pushes to `main` and pull requests run CI without publishing. A tag runs the same Python
3.10/3.12/3.14 checks inside Release; every check must pass before the single EXE/ZIP build and
automatic uploads. See [the release guide](../RELEASING.md#automation-one-tag--every-site).

| Tag | GitHub | CurseForge |
|---|---|---|
| `v1.2.1-alpha.N` | pre-release | Alpha |
| `v1.2.1-beta.N` | pre-release | Beta |
| `v1.2.1` | latest release | Release |

It refuses any other tag (`v1.2.1-rc.1`, `v1.2`). For the next beta:

```powershell
git tag -a v1.2.1-beta.5 -m "NeedleDropper 1.2.1 beta 5"
git push origin v1.2.1-beta.5
```

- **Every beta uses this workflow.** Betas 1 through 4 are already published. Choose a new tag;
  do not recreate an existing tag or upload the same beta manually.
- The workflow checks the tag against `__version__` in `companion/needledropper/__init__.py` and the
  TOC's `## Version:`, and says what to fix when they don't match.
- It builds the app and the GitHub release first. Then the BigWigs packager zips the addon and
  uploads it. The packager reads the game version from the TOC's `## Interface: 16001`; check that
  the first automatic file lands on WoW: Forever 1.60.x *(verify)*. The changelog it sends is the
  whole of CHANGELOG.md.
- The run's **Summary** page has a table. *CurseForge: upload to project 1786392 (beta)* means the
  upload is on. *skipped: no CF_API_KEY secret* or *skipped: no ## X-Curse-Project-ID in the TOC*
  tells you what's missing. A skipped site doesn't fail the run.
- To keep tags GitHub-only for a while (a friends-only build, say), add the repository **variable**
  `ADDON_SITE_UPLOADS` with the value `off` (*Settings → Secrets and variables → Actions →
  Variables*). Delete it to turn the uploads back on.
- WoWInterface stays off, because it has no WoW: Forever versions yet.

To test the packaging without another upload, use **Actions → Release → Run workflow** with an
existing version tag, or `gh workflow run release.yml --ref main -f tag=v1.2.1-beta.4`. Manual runs
are always dry runs: tests and release-file artifacts, with GitHub publishing and addon-site jobs
skipped. Tag pushes remain the only publishing trigger.

## Optional: Wago Addons

1. Sign in at <https://addons.wago.io> and create the addon from the developer dashboard *(verify
   the menu names)*. Use the same name, summary, description, logo and screenshot, with category
   **Audio & Video**.
2. Check that Wago offers **WoW: Forever** as a game version *(verify)*. If it doesn't, skip Wago for
   now.
3. If Wago wants a first file before review, upload the same zip as a Beta *(verify)*.
4. Copy the Wago project ID (a short code) into the TOC as `## X-Wago-ID: <id>`.
5. Create an API key in your Wago account settings *(verify where)* and add it to GitHub as the
   secret `WAGO_API_TOKEN`.

From then on, the same tags upload to Wago too.

## About the files

- **Logo.** `assets/logo/make_logo.py` draws it in plain Python, with the same palette and shape as
  the app icon (`assets/make_icon.py`). To redraw it, run `python assets\logo\make_logo.py`. It
  writes `needledropper-logo-400.png` (the one to upload) and a 1024 px master
  (`needledropper-logo-1024.png`) next to the script in a few seconds.
- **Screenshot.** It's cropped from the 2000 × 1126 copy of your Razor Hill screenshot: the box from
  (416, 160) to (1584, 817), 1168 × 657 (16:9), saved as PNG with only its sRGB colour profile. The
  crop leaves out the chat (other players' names), another player's name and guild tag, your player
  frame, the minimap and coordinates, the quest tracker, the gamepad hints, the action buttons and
  the beacon corner. Three NPC names ("Razor Hill Grunt") stay next to the title card; NPC names are
  fine. The crop was done with Pillow from the system Python, because the project's `.venv` has no
  image library.
- **Sharper version.** The 4K original (3840 × 2160) gives the same framing at about (799, 307) to
  (3041, 1567); scale that crop to 1920 × 1080. For a cleaner retake, turn off NPC names in the
  game's settings first *(verify where)*, and follow [screenshots.md](screenshots.md).
