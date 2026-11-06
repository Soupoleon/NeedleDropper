# CurseForge rules and process (with Wago and WoWInterface)

Research notes for putting the NeedleDropper addon on CurseForge, starting with a **Beta** file, with
Wago Addons next to it. Researched on **2026-11-05**, the day after WoW: Forever launched. Every claim
below points to a source in the [Sources](#sources) list. Nothing was created, uploaded or submitted on
any site for this research.

The related documents:

- [LISTING.md](LISTING.md): the project page text.
- [screenshots.md](screenshots.md): the images.
- [../RELEASING.md](../RELEASING.md): the release phases.
- [`.github/workflows/release.yml`](../../.github/workflows/release.yml) and [`.pkgmeta`](../../.pkgmeta): the upload automation.

Rules change. Read the [Moderation Policies](https://support.curseforge.com/en/support/solutions/articles/9000197279-moderation-policies)
page again on the day you submit.

**How sure each point is**

| Tag | Meaning |
|---|---|
| **Official** | CurseForge, Wago or Blizzard says so on a page fetched on 2026-11-05. CurseForge support articles are cited by ID and their "Modified on" date. |
| **Code** | Read in the BigWigs packager source, `release.sh` at tag v2.7.0. |
| **Observed** | Seen in live data: curseforge.com pages, Wago's public API, or CFWidget. CFWidget is a third-party mirror of CurseForge's project and file data, and the official CurseForge pages confirmed what it showed. |
| **Inferred** | Our reading of the sources above. Nobody states it. |
| **Not found** | Searched for and not found. Don't rely on it. |

---

## The short version

1. **Only the owner can create the project.** It is a manual form at
   `authors.curseforge.com`. CurseForge reviews the project only once it has a file, and the
   moderators work 08:00 to 15:00 CET every day, first come first served. CurseForge publishes no
   turnaround time for WoW. (Official)
2. **The optional Windows app is fine in practice, but no rule says so.** CurseForge has no rule
   about addons that need outside software. The rule that comes closest is this one: "External
   download links for files are not allowed." It means the project's own files must download from
   CurseForge, and ours will. Raider.IO, WeakAuras and SimulationCraft all point to desktop apps from
   approved pages. To stay safe:
   - link to the app's *page*, not to a file;
   - put links to other sites at the bottom of the page;
   - make the GitHub repository public before the first upload.
3. **Forever on CurseForge** is the flavor "Forever" (`gameVersionTypeId=88568`). Its only game
   version so far is **1.60.1**. A file can target Forever only: Leatrix Plus and Forever Enhanced
   Cursor ship such files. (Observed)
4. **The packager:** Forever support arrived in v2.6.0. v2.7.0, the version release.yml pins, turns
   `## Interface: 16001` into "Forever 1.60.1" with no extra flag. `-g` is optional.
   WoWInterface lists no Forever versions, so keep `-w 0`. (Code, Observed)
5. **Wago** lists Forever `1.60.1` and takes beta uploads from the packager. Its developer
   agreement has three rules that matter here:
   - no `.exe` in the zip;
   - no advertising on the page or in the addon;
   - an account linked to Battle.net or GitHub.

   (Official, Observed)
6. **Starting with a Beta file works, with one open question.** CurseForge's newest article says an
   approved Beta file is enough for a new project to reach the CurseForge app. An older article says
   a Release file is needed. Either way, app users only get Beta files after they opt in. Check in
   the app once the project is approved (see [section 7](#7-the-beta-first-plan)).

---

## 1. Creating the CurseForge project

### 1.1 The order of events

1. Sign in to CurseForge with the owner's account.
2. Open <https://authors.curseforge.com/#/projects/create/choose-game> and pick **World of
   Warcraft**. (Official, 9000197241)
3. Fill in the form ([1.2](#12-the-creation-form)), then the Description tab, then the License tab.
   (Official, 9000197241)
4. Upload a file, by hand or from the release workflow.

   A project with no file stays "New" and nobody reviews it. The first file is what puts the
   project in the moderation queue. (Official, 9000197905, 9000197241)
5. Moderators approve the project page first, then the file. (Official, 9000197905)
6. Once both are approved, the file goes live, unless it was held back for a manual release
   ([1.4](#14-what-a-file-upload-sets)).

**There is no flavor to pick on the form.** Each file's game versions decide where it shows up. Leatrix
Plus, for example, keeps Forever, Classic, Mists, Titan and Retail files in one project. (Observed)
[RELEASING.md](../RELEASING.md) says "flavor WoW Forever" at creation. In practice, the packager sets
the flavor through each file's game version.

The project ID is shown in the "About Project" box on the project page (packager README). It exists
as soon as the project is created, before approval (Inferred: files can't be uploaded to a project
without one). It goes into the TOC as `## X-Curse-Project-ID: <number>`.
The packager skips CurseForge unless that value is all digits. (Code, `release.sh` line 1502)

### 1.2 The creation form

Sources: 9000197241 (Creating and Submitting a Project, 2024-09-24), 9000199552 (Project
Submission Guide and Tips, 2024-12-23) and 9000197279 (Moderation Policies, 2026-10-22). The blog
posts are listed under [Sources](#sources).

| Field | What CurseForge requires | NeedleDropper |
|---|---|---|
| Game | Pick it from the list | World of Warcraft |
| Name | Must be unique: a taken name is rejected. No game name, version numbers, category word ("addon") or overly long name. English only. | **NeedleDropper**. On 2026-11-05, nothing similar existed on CurseForge (a search for "needle" found no such project) or on Wago (the `needledropper` slugs return 404). (Observed) |
| Summary | One line on what the project does, not who made it or who it is for. Shown in search results, not on the project page. The policy asks for one sentence at most, ideally. | LISTING.md's summary: one sentence, 89 characters. **Not found:** any published character limit. Watch for a counter in the form. |
| Description | Pick the editor in the form: WYSIWYG (the default) or **Markdown**. The text must say what the addon actually does, in English first, and be grammatically correct. The blog calls the description the most common reason for "Changes Required". | Pick **Markdown** so LISTING.md's text pastes as it is, then check the preview. Allowed HTML and CSS ([9000284625](https://support.curseforge.com/en/support/solutions/articles/9000284625), 2026-09-15): headings, lists, tables, `pre`/`code`, images, links, fonts up to 40 px. Iframes work only from listed domains, among them youtube.com and github.com. |
| Project License | A drop-down of common licenses, or **Custom** | **MIT License**. The option exists: OmniCC's CurseForge page shows "MIT License". It matches `LICENSE` and `## X-License: MIT`. (Observed) |
| Class | The root category, which decides the supported file types | Addons |
| Main category | The one category that best describes the project | **Audio & Video** (URL slug `audio-video`). It is in CurseForge's WoW category list, along with Roleplay, Miscellaneous, Data Export, Twitch Integration and others. (Observed) |
| Additional categories | Up to 4. Unrelated categories get the project sent back. | Roleplay and Miscellaneous (LISTING.md). Both exist. |
| Logo (avatar) | At least 400 × 400 px, square, PNG. Larger images are scaled down. It must be original: not a single colour or simple gradient, no game logo or other game art, nothing copyrighted or NSFW. Avoid WebP, which has a known bug. | [`assets/logo/needledropper-logo-400.png`](../../assets/logo/needledropper-logo-400.png): 400 × 400 PNG, an original vinyl-record drawing made by `assets/logo/make_logo.py`. Don't use the TOC's `IconTexture`, which is a Blizzard icon, as the avatar. |
| Allow Comments | A toggle. CurseForge wants players to have a way to report bugs. If comments are off, the description should say where to go instead. | Either works. Issues go to GitHub ([1.3](#13-after-creation)). |
| Experimental | Hides the project from the CurseForge app and from site search ([1.7](#17-options-for-a-quieter-start)) | Off, for a public beta |
| Distribution toggle (License tab) | Whether apps that use CurseForge's third-party API may offer the project. Their downloads don't count towards rewards. It can be changed later. ([9000207877](https://support.curseforge.com/en/support/solutions/articles/9000207877)) | Recommended **on**, so players can install it from more addon managers. The owner decides. |

The TOC line `## Category: Audio & Video` only sorts the addon in the game's own AddOns list.
CurseForge doesn't read it: its categories are set only in this form. (Inferred)

### 1.3 After creation

The project dashboard then offers these settings (Official, 9000199552):

- Source: a link to the code;
- Issues Tracker: CurseForge's own tracker, or an external one such as GitHub Issues;
- Wiki;
- Donation method;
- Members;
- Images;
- Files;
- social links ([blog](https://blog.curseforge.com/how-to-create-the-best-project-page/)).

For NeedleDropper:

- **Source:** <https://github.com/Soupoleon/NeedleDropper>
- **Issues:** <https://github.com/Soupoleon/NeedleDropper/issues>
- **Wiki:** optional. INSTALL.md or TROUBLESHOOTING.md would do.
- **Donation:** none needed.

**Images.** CurseForge recommends in-game screenshots and the carousel at the top of the page. Images
are only *required* for texture packs and Sims projects, not addons (Official, 9000197241; blog). An
AI-edited showcase image that could mislead needs a visible disclaimer (Official, 9000197279). The
shot list in [screenshots.md](screenshots.md) already meets these rules.

### 1.4 What a file upload sets

These are the same fields by hand or through the API (Official, 9000197241, 9000197242, 9000197321):

- **File.** A real `.zip` (not a renamed `.rar`), under 2 GB.
- **Display name.** Optional, but CurseForge advises against giving every file the same name. The
  packager names each file after its tag ([3.4](#34-what-needledroppers-uploads-will-look-like)).
- **Release type.** Release, Beta or Alpha ([section 7](#7-the-beta-first-plan)). It can be changed
  later from the dashboard (Files, then Edit on the file).
- **Changelog.** Required in practice: the Fair Play policy wants every file to describe its
  changes. The packager sends `CHANGELOG.md` as Markdown, because `.pkgmeta` has `manual-changelog`
  with `markup-type: markdown`.
- **Supported versions.** The game versions: Forever 1.60.1 ([section 3](#3-wow-forever-on-curseforge)).
- **Related projects.** Dependencies. NeedleDropper has none.
- **Release options.** Publish the file as soon as it is approved, or hold it and publish it by
  hand. In the API this is `isMarkedForManualRelease`. The packager never sets it, so its uploads go
  live as soon as they are approved. (Code)

### 1.5 CurseForge's automatic checks on WoW zips

The automatic checks are described in
[9000210425](https://support.curseforge.com/en/support/solutions/articles/9000210425) (2024-02-28) and
[9000205250](https://support.curseforge.com/en/support/solutions/articles/9000205250) (2024-03-05).
(Official)

- The upload must be a `.zip`.
- Everything at the top level of the zip must be a folder. A loose file holds the upload "Under
  Review".
- At least one top-level folder `X` must contain `X/X.toc`. The name is case-sensitive.
- Blacklisted files, extensions and folders get the upload rejected. The list is not published, but
  macOS's `__MACOSX` and `.DS_Store` are on it. Paths with `../` are rejected too.
- Every upload is scanned for malware.
- A file identical to one already approved is rejected as a duplicate.

The packaged zip already passes these checks:

- one top-level folder, `NeedleDropper/`, holding `NeedleDropper/NeedleDropper.toc`, `LICENSE` and
  `CHANGELOG.md`;
- no programs: release.yml refuses `.exe`, `.dll`, `.bat`, `.cmd`, `.ps1`, `.py` and `.pyd`.
  (Observed)

Keep the unsuffixed `NeedleDropper.toc`. **Not found:** whether a zip with only
`NeedleDropper_Camelot.toc` would pass the `X/X.toc` check.

### 1.6 Moderation: statuses, hours and timing

**Statuses** (Official, 9000197905):

- **Projects:**
  - **New:** waits for a file before anyone reviews it.
  - **Changes Required:** comes with notes from the moderators.
  - **Changes Made:** after you fix and resubmit.
  - **Approved** or **Rejected.** A rejected project can't be reviewed again or approved later.
  - **Inactive** and **Abandoned:** for stale projects.
- **Files:**
  - **Under Review:** reviewed once the project page is approved.
  - **Approved.**
  - **Under manual review:** wait, and don't resubmit.
  - **Rejected:** open a support ticket if it was a mistake.
  - **Archived** and **Deleted.**

Don't delete a project or file and upload it again to get around a rejection: CurseForge says this
makes things worse (Official, 9000197279). The notes for "Changes Required" arrive in
<https://www.curseforge.com/my-notifications> (Official, 9000199552).

**Hours.** Moderators review projects and files from 08:00 to 15:00 CET (23:00 to 06:00 PST), seven
days a week except holidays, first come first served. A file uploaded late in the day may wait until
the next review day. (Official, [9000198422](https://support.curseforge.com/en/support/solutions/articles/9000198422), 2026-07-07)

**Volume.** About 2,000 new projects a week, not counting updates
([blog, 2025](https://blog.curseforge.com/how-to-pass-moderation-review-on-curseforge-2/)).

**Turnaround.** **Not found for WoW.**

- CurseForge's documentation for game studios promises a review within 24 hours at most. That
  promise covers games that use CurseForge's moderation service, and WoW isn't documented as one, so
  don't count on it.
  ([docs.curseforge.com](https://docs.curseforge.com/docs/capabilities/moderation/moderation-as-a-service))
- A third-party blog claims 24 to 72 hours. Not verified.

Our plan (Inferred): upload in the CET morning, allow a few days, and more if a "Changes Required"
round happens.

### 1.7 Options for a quieter start

- **Experimental project.** It doesn't sync to the CurseForge app and doesn't appear in site search.
  Players can still install its files with the app's import feature. To make it a normal project
  later, use the "Submit Project for non-experimental" button. The article says that button is only
  on the legacy site, `legacy.curseforge.com`. (Official, 9000197242)

  This is useful for setting up the page while the repository is still private. (Inferred: the
  switch probably goes through a review of its own.)
- **Manual release.** Hold an approved file and publish it when you choose
  ([1.4](#14-what-a-file-upload-sets)).
- **Don't** also set up CurseForge's own webhook packager
  ([9000197281](https://support.curseforge.com/en/support/solutions/articles/9000197281)). Each tag
  would then upload twice: once from it and once from the BigWigs packager. (Inferred)

---

## 2. Moderation rules that apply to NeedleDropper

### 2.1 The optional Windows app (NeedleDropper.exe)

- **No rule covers addons that need or link to outside software.** None of these pages has such a
  rule or prescribes a disclosure format (Not found):
  - the Moderation Policies (2026-10-22);
  - the submission guide;
  - the file-types article;
  - the website rules.
- **The rule that comes closest is "Third Party Downloads"**, quoted in the short version above
  (Official, 9000197279). Read plainly, it means the project's own files must download from
  CurseForge, which they will. It doesn't mention separate applications.
- **Approved projects already point to desktop apps.** All of these link to the app's *web page*,
  not to a file (Observed in their live CurseForge descriptions):
  - **Raider.IO** recommends its desktop app and links to raider.io/addon.
  - **WeakAuras** describes the WeakAuras Companion app and links to weakauras.wtf.
  - **SimulationCraft** sends readers to simulationcraft.org for the application and its downloads.

  These are precedents, not a guarantee.
- **Two general rules work in our favour** (Official, 9000197279). Projects should add value for
  players, and the description must say clearly what the project does. NeedleDropper works without
  the app: title cards, plus your own audio files played inside WoW. Say so near the top, as
  LISTING.md does.

What we recommend (Inferred from the rule and the precedents):

1. Link to a *page* about the app, never to an `.exe` or `.zip` download URL. For example, link to
   [INSTALL.md section 3](https://github.com/Soupoleon/NeedleDropper/blob/main/docs/INSTALL.md#3-optional-the-needledropper-app),
   or to the releases page.
2. Say that the app is "not included in this download", as LISTING.md does.
3. **Make the GitHub repository and its releases public before the first file goes up.** Moderators
   may follow the links, and the Source and Issues fields point to GitHub. While the repository is
   private, every one of those links returns 404.
4. State what the app does not do: no memory reading, no injection, no automated input. CurseForge
   applies the game developer's rules to what an addon does as well as to its content
   ([2.6](#26-the-game-developers-rules)).

### 2.2 Links to other sites, and where they go on the page

- Some kinds of link must come at the bottom of the page, after the description, at a reasonable
  size: personal websites, donation links, hosting banners, and pages on other sites or portfolios.
  Small functional link badges (dependencies, social links) are allowed at the top if they don't
  advertise anything. (Official, 9000197279; [blog](https://blog.curseforge.com/how-to-create-the-best-project-page/))
- github.com is on CurseForge's list of trusted domains: its links skip CurseForge's link-out
  warning, and its pages can be embedded. (Official, 9000284625)
- **LISTING.md's draft today:** the Links block sits at the bottom, which is right. But the
  Requirements section, mid-page, links to "download the app" on GitHub Releases, and the releases
  page also carries the addon zip. A moderator could read that as a mirror of the addon's files
  placed mid-page. Whether they would is a judgment call.

  The cheap fix: in Requirements, call the app "a separate download (see Links below)", or link to
  the install guide's app section. Keep the releases link in the bottom Links block. (Inferred)

### 2.3 Executables in the zip

- **CurseForge** rejects blacklisted files and extensions and scans every upload for malware
  (Official, 9000210425). No published rule names `.exe` (Not found).
- **Wago** forbids `.exe` files in the uploaded zip, in its developer agreement (Official).
- **NeedleDropper:** release.yml already refuses programs in both zips (Observed). Keep
  NeedleDropper.exe on GitHub Releases only.

### 2.4 License

- Pick a license from the list, or Custom. If the code appears elsewhere under a different license,
  CurseForge goes by the one on the CurseForge page. (Official, 9000199552)
- MIT is on the list (Observed, OmniCC).
- Using other people's work needs their license or permission, plus credit. Abuse can lead to a
  permanent ban. (Official, 9000197279)
- LISTING.md credits the Soundtrack addon for an *idea* (the silent-track trick). An idea isn't a
  copy, but if any of Soundtrack's files or code were reused, check its license and credit it.
  (Inferred: we did not compare the code.)

### 2.5 Music and other copyrighted material

- **Licensed Music.** A project may include music only if the author owns it or may redistribute
  it, with credit. Unlicensed third-party music is not allowed. (Official, 9000197279, the October
  2026 version)
- **Blizzard's 2009 note** on music in add-ons says that distributing copyrighted music or audio
  without permission is illegal. (Official, Blizzard forum post)
- **NeedleDropper ships no music.** The only audio file in the addon is `media/silence.mp3`, the
  silent track used to fade out zone music. Soundtracks contain only titles, artists and Spotify and
  YouTube IDs. (Observed in the repository)

  Say this plainly in the description. LISTING.md's "No music is distributed" does: a reviewer who
  skims "songs" and "soundtracks" might otherwise ask.
- **Screenshots:** no album covers and no video frames. [screenshots.md](screenshots.md) already says
  so. (Inferred from the copyright rule)

### 2.6 The game developer's rules

CurseForge applies the game developer's EULA and terms of use, to what an addon does as well as to
its content (Official, 9000197279). For WoW, that means Blizzard's **UI Add-On Development Policy**
(Official, Blizzard). In short, an add-on:

- must be free;
- must keep its code fully visible, with no obfuscation;
- must not harm realm performance or other players;
- must not advertise goods or services;
- must not ask for donations in game;
- must stay within the game's T rating;
- must follow the Terms of Use and the EULA.

Blizzard may also disable any add-on feature. [POLICY.md](../POLICY.md) explains how NeedleDropper
follows these rules.

One point to watch (Inferred): keep in-game mentions of Spotify and YouTube functional, never
promotional. The Wago agreement says the same ([5.3](#53-the-wago-developer-agreement-points-that-matter)).

### 2.7 Fair play

Uploads meant only to look active are not allowed: re-uploads without changes, or tiny,
non-functional updates. Every file needs a changelog. (Official, 9000197279)

**A risk for the release workflow (Inferred from release.sh):** don't re-run the `addon-sites` job
for a tag that has already uploaded.

- The packager rebuilds the zip, so its checksum changes.
- CurseForge's duplicate-file check then probably won't catch it, and a second, identical-looking
  file goes live on both sites.
- If it happens, archive the extra file in the dashboard.

### 2.8 Comments and conduct

The CurseForge website rules apply to comments, issue trackers and private messages: be civil, no
spam, no advertising in comments (Official,
[9000197998](https://support.curseforge.com/en/support/solutions/articles/9000197998)).

---

## 3. WoW: Forever on CurseForge

### 3.1 The flavor and its game version

- **The Forever flavor.** CurseForge's WoW browse page has a **Forever** flavor filter. Its link
  uses `gameVersionTypeId=88568`. (Observed on curseforge.com)
- **One game version.** The game-version filter's only Forever entry is `1.60.1`. (Observed)
- **How a Forever file is shown.** A file tagged `1.60.1` shows "Flavors: Forever" and "Supported
  game versions: 1.60.1", and is labelled "WoW Forever" in the Recent Files list. (Observed on
  Leatrix Plus file 9467366)
- **No other Forever version yet.** Every Forever file sampled, from the beta (2026-09-17) to today,
  is tagged `1.60.1`, and no other `1.6x` version appears. (Observed through CFWidget)
- **From TOC number to game version.** Interface `16001` stands for version 1.60.1: the client is
  1.60.1, and the wiki's TOC page lists Forever as `16001`. (Official, warcraft.wiki.gg; Code)
- **The numeric ID behind `1.60.1`,** which the upload API needs, can only be read from CurseForge's
  API with a token (`GET /api/game/versions`). Without a token it returns 401. **Not found** here,
  and not needed: the packager looks it up at upload time ([4.2](#42-from--interface-16001-to-forever-1601)).

### 3.2 How other addons label their Forever files

The latest files on 2026-11-05, from CurseForge's file lists (Observed):

| Addon | How it ships | Latest file: label (file name) | Game versions on that file |
|---|---|---|---|
| TomTom | One file for every flavor | `v4.4.0-release` (`TomTom-v4.4.0-release.zip`) | 12.1.5, 12.1.0, 12.0.7, 5.5.4, 4.4.2, 3.80.2, 2.5.6, 1.15.9, **1.60.1** |
| Baganator | One file for every flavor; the summary mentions Forever support | `842` (`Baganator-842.zip`) | 12.1.5, 12.1.0, 5.5.4, 3.80.1, 2.5.6, 1.15.9, **1.60.1** |
| Details! | One file for every flavor | `Details.20261104.15400.172` | 12.1.5, 5.5.4, 4.4.2, 3.80.2, 3.4.5, 2.5.6, 1.15.9, **1.60.1** |
| Questie | One file for every flavor | `v12.6.2` | 5.5.4, 3.80.2, 2.5.6, 1.15.9, **1.60.1** |
| Plater | One file for every flavor | `Plater-v663` | 12.1.7, 12.1.5, 5.5.4, 3.80.1, 2.5.6, 1.15.9, **1.60.1** |
| Leatrix Plus | **A separate file per flavor** | `1.60.28-forever` (`Leatrix_Plus-1.60.28-forever.zip`) | **1.60.1 only** |
| Forever Enhanced Cursor | **A Forever-only project** | `1.0.2` (`ForeverEnhancedCursor-1.0.2.zip`) | **1.60.1 only** |

Most big addons add `1.60.1` to one file for all flavors. Leatrix Plus gives each flavor its own file,
with a `-forever` suffix in the label.

### 3.3 Can one file target Forever only? Yes.

Leatrix Plus and Forever Enhanced Cursor upload files tagged only `1.60.1`, and CurseForge lists them
under the Forever flavor alone (Observed). NeedleDropper's files will be the same. Its TOC has a single
interface number, `16001`, so the packager tags each file with Forever 1.60.1 and nothing else (Code).
The CurseForge app then offers it only to Forever installs (Inferred from how flavors work).

### 3.4 What NeedleDropper's uploads will look like

For the tag `v1.2.0-beta.1`, with today's release.yml (packager v2.7.0, default name templates):

- **File name** `NeedleDropper-v1.2.0-beta.1-forever.zip`; **display name** `v1.2.0-beta.1-forever`.
  - The packager adds `-forever` for any game type other than retail, unless the tag already
    contains it (Code, `filename_filter`, line 153).
  - To drop the suffix, add `-n "{package-name}-{project-version}:{project-version}"`. Only the
    look changes. (Code, README)
- **Release type:** Beta, because the tag contains "beta" (Code, line 848).
- **Game version:** Forever 1.60.1 (Code, Observed).
- **Changelog:** the whole `CHANGELOG.md`, as Markdown (Code).

---

## 4. The BigWigs packager and Forever

### 4.1 Which version

| Version (date) | What changed for Forever |
|---|---|
| v2.5.1 and older | No Forever case. `toc_to_type` treats `16001` as **retail**, so the file would go up under the wrong flavor. (Code, v2.5.1) |
| **v2.6.0** (2026-09-18) | "Add WoW Forever support" ([PR #202](https://github.com/BigWigsMods/packager/pull/202)). v2.6.1 fixed a WoWInterface check. (Official, release notes) |
| **v2.7.0** (2026-11-05, commit `01e8631`) | Forever interface numbers become allowed in `_Mainline.toc` ([issue #204](https://github.com/BigWigsMods/packager/issues/204); v2.6.x stops with an error) and refused in `_Classic.toc`. WoWInterface and Wago uploads now retry. (Official, Code) |

release.yml pins v2.7.0 by its commit, which is right. (Observed)

### 4.2 From `## Interface: 16001` to "Forever 1.60.1"

Step by step, in `release.sh` v2.7.0 (Code):

1. **Finding the TOC.**
   - `.pkgmeta`'s `package-as: NeedleDropper` and `move-folders` entry register
     `addon/NeedleDropper/` as the addon's folder (lines 1118 to 1124).
   - The packager then reads `addon/NeedleDropper/NeedleDropper.toc` (lines 1424 to 1455).
2. **Interface number to game type.** `toc_to_type` (line 207) maps every `16xxx` interface number
   to the game type `forever`. For comparison:

   | Interface number | Game type |
   |---|---|
   | `11xxx` | classic |
   | `16xxx` | forever |
   | `20xxx` | bcc |
   | `30xxx` | wrath |
   | `380xx` | titan |
   | `40xxx` | cata |
   | `50xxx` | mists |
   | anything else | retail |
3. **Game type to version.**
   - `set_build_version` (line 1361) turns `16001` into `1.60.1`: the leading digit, then two digits,
     then two digits.
   - Since that is the only version, it makes `forever` the game type of the whole build
     (line 1402).
4. **Version to CurseForge ID.** `upload_curseforge` (line 2890) does three things:
   - it fetches CurseForge's WoW game-version list with the token;
   - it looks for the name `1.60.1` under game version type **88568** (line 2909);
   - it sends that version's ID with the file.

   88568 is the ID CurseForge's own Forever filter uses (Observed), so the two agree.
5. **No exact match.** This happens, for example, after a new patch that CurseForge hasn't listed
   yet. The packager warns, then picks the nearest lower version of the same type, or failing that
   the highest one. If the type has no versions at all, the upload fails.
6. **Release type from the tag** (line 848):
   - a tag with "alpha" gives Alpha;
   - otherwise a tag with "beta" gives Beta;
   - any other tag gives Release;
   - an untagged build gives Alpha.

The `forever` game type also switches on `@version-forever@` blocks and comments out
`@retail@`/`@version-retail@` blocks. NeedleDropper uses none of these. Its only keywords are
`@localization(...)@` in `Locale.lua`, covered in [4.5](#45-upload-behaviour-worth-knowing).
(Observed)

### 4.3 Flags

| Flag | Effect | Use it? |
|---|---|---|
| none | Forever is detected from the TOC | **Yes, this is enough** |
| `-g forever` or `-g camelot` | Forces the Forever game type; the version still comes from the TOC (lines 305 to 311) | Optional. It only states the intent. |
| `-g 1.60.1` | Skips detection. Any `1.6x.y` counts as Forever (lines 312 to 347; README, "Single game version"). | No. When the TOC moves to `16002`, a pinned `-g 1.60.1` would keep tagging the old version. (Inferred) |
| `-w 0` | Drops the WoWInterface ID | **Yes, keep it** ([section 6](#6-wowinterface)) |
| `-d` | Builds the zip without uploading | release.yml's first packaging step |
| `-n "file:label"` | Sets the file-name and label templates | Optional ([3.4](#34-what-needledroppers-uploads-will-look-like)) |
| `-l` | Skips `@localization@` replacement | The fallback if the localization export misbehaves ([4.5](#45-upload-behaviour-worth-knowing)) |
| `-S` | Creates one TOC per game type from `## Interface-<Type>:` lines; Forever's would be `_Camelot.toc` | Not needed |

### 4.4 TOC file names

- **What the client loads** (Official, [warcraft.wiki.gg](https://warcraft.wiki.gg/wiki/TOC_format)):
  1. The Forever client looks first for its game-type suffixes, `_Forever.toc` or `_Camelot.toc`.
  2. Then for the family suffix `_Mainline.toc`.
  3. Then for the plain `NeedleDropper.toc`.

  The TOC `[Game]` variable is "Camelot" for now; the wiki says it may become "Forever".
- **The packager** recognises `-Camelot`/`_Camelot` but **not** `_Forever` (Code, line 1424).
- **Wago's API** lists all four suffixes for Forever: `-Camelot`, `_Camelot`, `-Forever` and
  `_Forever` (Observed).
- **NeedleDropper:** keep the one unsuffixed `NeedleDropper.toc` with `## Interface: 16001`. It
  works for the client, the packager, CurseForge's `X/X.toc` check and Wago. (Inferred)
- **When Blizzard ships a new Forever patch,** change the interface number. The packager works out
  the new version itself, and the fallback in [4.2](#42-from--interface-16001-to-forever-1601) step 5
  covers a site that hasn't listed it yet.
- **The range that counts as Forever.** In PR #202 the maintainers discussed whether to match `1.60`
  or all of `1.6x`. One of them asked to hold off on Forever TOC detection until Blizzard confirmed
  the naming. The released code treats all `16xxx` and `-g 1.6x.y` as Forever. A Blizzard
  renumbering outside that range would need a packager update. (Official PR discussion, Code,
  Inferred)

### 4.5 Upload behaviour worth knowing

- **CurseForge goes first.** If its upload still fails after retries, the packager stops, and Wago
  is never tried (Code, lines 3425 to 3427). Every upload is retried up to 10 times, 3 seconds
  apart (Code, `retry`).
- **Localization.** With a CurseForge project ID and token, the packager replaces each
  `--@localization(...)@` line in `Locale.lua` with that language's phrases. It exports them from
  the project's localization tool on CurseForge. (Code, lines 1564 to 1705; Official,
  [9000197354](https://support.curseforge.com/en/support/solutions/articles/9000197354))

  A brand-new project has no phrases, and what the export returns then is **Not found**. If it
  returns an error page, the build stops. The first `-d` step in release.yml will show what happens.
  If it fails, `-l` turns localization off.
- **Tokens.** The packager calls `https://wow.curseforge.com/api/...` with an `x-api-token` header
  (Code). Tokens are made at <https://authors.curseforge.com/#/settings/api-tokens> (Official,
  9000197321; packager README). It reads `CF_API_TOKEN` and still accepts the older `CF_API_KEY`
  name (Code, lines 506 and 507). release.yml passes the `CF_API_KEY` secret in as `CF_API_TOKEN`
  (Observed).
- **Label and file name** get `-forever` ([3.4](#34-what-needledroppers-uploads-will-look-like)).

### 4.6 Setting game versions yourself: the CurseForge Upload API

From [9000197321](https://support.curseforge.com/en/support/solutions/articles/9000197321)
(2026-09-27). (Official)

- **Authentication:** an `X-Api-Token` header, or a `token` query parameter.
- **Listing versions:** `GET /api/game/versions` returns entries with `id`, `gameVersionTypeID`,
  `name` and `slug`. For Forever, take the one with `gameVersionTypeID` 88568 and name `1.60.1`. The
  packager queries `/api/game/wow/versions` instead (Code).
- **Uploading:** `POST /api/projects/{projectId}/upload-file`, as multipart with two parts, a
  `metadata` JSON and the `file`. The metadata fields:

  | Field | What it holds |
  |---|---|
  | `changelog` | The text of the changes |
  | `changelogType` | `text`, `html` or `markdown` |
  | `displayName` | The label shown on the site |
  | `gameVersions` | A list of version IDs |
  | `gameVersionNames` | A list of version names. This field is newer and untested for WoW. |
  | `releaseType` | `alpha`, `beta` or `release` |
  | `isMarkedForManualRelease` | Hold the file after approval |
  | `relations` | Dependencies on other projects |
- **Fixing a file afterwards:** `POST /api/projects/{projectId}/update-file` with the `fileID` and,
  for example, new `gameVersions` or `releaseType`. The file's Edit page on the dashboard does the
  same (Official, 9000197242).
- **With the packager:** `-g 1.60.1` ([4.3](#43-flags)).

---

## 5. Wago Addons

### 5.1 Forever support

From <https://addons.wago.io/api/data/game> on 2026-11-05 (Observed):

| Key | Value |
|---|---|
| `patches.forever` | `["1.60.1"]` |
| `live_patches.supported_forever_patches` | `"1.60.1"` |
| `toc_suffixes.forever` | `-Camelot`, `_Camelot`, `-Forever`, `_Forever` |
| `stability_values` | `stable`, `beta`, `alpha` |

### 5.2 How the packager uploads to Wago

- **Setup:** put `## X-Wago-ID: <id>` in the TOC and provide `WAGO_API_TOKEN` (Official,
  [docs.wago.io](https://docs.wago.io/)).
  - The ID is the 8-character code under the addon's name on the developer dashboard,
    <https://addons.wago.io/developers>.
  - The key comes from <https://addons.wago.io/account/apikeys>.
- **Upload:** `POST https://addons.wago.io/api/projects/<id>/version` with a Bearer token, sending
  `label`, `stability`, `changelog` and the supported patches (Official, docs.wago.io).
- **What the packager sends** (Code, `upload_wago`, line 3128):
  - the game type `forever` becomes `"supported_forever_patches": ["1.60.1"]`;
  - beta tags go up with stability `beta`, and release tags with `stable`.
- **One difference to know about:** Wago's own example uses singular keys such as
  `supported_retail_patch`, while the packager sends lists under plural keys. Wago's API uses the
  plural form itself (`live_patches`), and this is the packager's long-standing integration.
  (Observed, Inferred)
- **Creating the project:** <https://addons.wago.io/developers/projects/create> (Observed, the site's
  route list).

### 5.3 The Wago developer agreement: points that matter

From <https://addons.wago.io/agreements/developer-agreement>, paraphrased (Official):

- Your Wago account must be linked to a verified Blizzard (Battle.net) or GitHub account.
- The uploaded zip must contain **no `.exe` files** and no malware.
- No advertising or promotion of a brand or service, in the addon or on its Wago page. That includes
  its name, content and features. Keep the Spotify and YouTube mentions about what the addon does.
- Before submitting, tell Wago what the addon materially does, and keep the page up to date.
- The addon must not collect users' personal data.
- You must follow Blizzard's rules. Wago decides what it lists, and may stop hosting an addon with
  7 days' notice.
- The agreement's scope names only WoW's retail and classic versions. That wording predates Forever,
  and Wago's own data lists Forever, so it isn't a blocker. (Inferred)
- An optional reward program shares advertising and subscription revenue.
- **Not found:** how long Wago takes to approve a new project, and which description format its
  editor uses. LISTING.md assumes Markdown.

---

## 6. WoWInterface

- WoWInterface's `compatible.json` lists only Retail, Classic, TBC-Classic, WOTLK-Classic and
  Cata-Classic, with no Forever version (Observed).
- The packager skips the `forever` game type for WoWInterface. With no version left to upload, that
  upload fails (Code, line 3026).
- release.yml's `-w 0` is right. Keep it until WoWInterface lists Forever.

---

## 7. The beta-first plan

- **Who gets Beta files.** Beta files reach CurseForge app users only if they opt in to Beta, and
  Alpha files only if they opt in to Alpha. (Official, 9000197242)
- **Where Beta files show.** For WoW (unlike Minecraft), Beta and Alpha files are not shown in the
  project page's sidebar. Testers find them in the **Files** tab. (Official, 9000197242, 9000197241)
- **Whether a Beta-only project reaches the app at all: CurseForge's articles disagree.**
  - The newer article (9000197242, 2026-07-09) says a Release *or* Beta file must be approved before
    a new project syncs to the app. A project with only Alpha files stays on the website.
  - The same article's Release line says a project needs at least one Release file to be available.
    It contradicts itself.
  - The older article (9000197241, 2024-09-24) says a Release file is needed.

  The newest wording favours the plan, but treat the question as open.
- **Where a WoW player switches an addon to Beta** in the CurseForge app: **Not found** in
  CurseForge's support articles.
- **What to do** (Inferred):
  1. Once the project is approved, check with a WoW: Forever install in the CurseForge app. Search
     for NeedleDropper, install it, then see whether the next beta arrives as an update.
  2. Until that works, give testers the project's Files-tab link and the manual install steps in
     [../INSTALL.md](../INSTALL.md) and [../BETA-TESTING.md](../BETA-TESTING.md).
  3. If the app ignores a Beta-only project, say so to testers and let the first Release fix it.
     Don't upload the beta as a Release: Release files go to every app user by default.
- **Wago:** beta uploads carry stability `beta`. **Not found:** how Wago app users opt in to betas.

---

## 8. Checklist for the owner

Only the owner can do the account and site steps. Where a step changes a file, the people working on
that part of the repository make the change.

**Before creating anything**

1. Make the GitHub repository and its releases public.
2. Decide where the app link goes in LISTING.md ([2.2](#22-links-to-other-sites-and-where-they-go-on-the-page)).

**CurseForge**

3. Create the project at <https://authors.curseforge.com/#/projects/create/choose-game>:
   - World of Warcraft, name NeedleDropper, LISTING.md's summary, **Markdown** editor;
   - MIT License, class Addons, main category Audio & Video, plus Roleplay and Miscellaneous;
   - the 400 × 400 logo, your choice of comments and distribution toggle.
4. On the project dashboard, set Source and Issues to GitHub, add the screenshots and copy the
   numeric **project ID**.
5. Make an API token at <https://authors.curseforge.com/#/settings/api-tokens> and save it as the
   repository secret `CF_API_KEY`.

**Wago**

6. Link the account to Battle.net or GitHub and create the project. Copy its 8-character ID, make an
   API key and save it as the repository secret `WAGO_API_TOKEN`.

**Release**

7. In the TOC, add `## X-Curse-Project-ID: <number>` and `## X-Wago-ID: <id>`. The addon's
   maintainers make this change.
8. Push a `vX.Y.Z-beta.N` tag. The workflow builds and uploads, and the upload puts the project in
   the moderation queue. Moderators work 08:00 to 15:00 CET.
9. Watch <https://www.curseforge.com/my-notifications>. Answer "Changes Required" by editing the
   project, never by deleting and starting over.
10. After approval:
    - check the file's flavor ("Forever") and game version (`1.60.1`);
    - check the CurseForge app ([section 7](#7-the-beta-first-plan));
    - fill in LISTING.md's `{{CURSEFORGE_URL}}` and `{{WAGO_URL}}` placeholders.

**Don't**

- re-run the `addon-sites` job for a tag that already uploaded ([2.7](#27-fair-play));
- set up CurseForge's webhook packager as well;
- link to the `.exe` file itself;
- offer the addon zip from anywhere other than CurseForge in the CurseForge description.

**Corrections for other documents**

- [../RELEASING.md](../RELEASING.md):
  - "flavor WoW Forever": the form has no flavor field. The packager's game version sets the flavor.
  - "Account → API tokens" is right. The direct URL is
    <https://authors.curseforge.com/#/settings/api-tokens>. The older Automatic Packaging article
    gives <https://www.curseforge.com/account/api-tokens> instead.
- [LISTING.md](LISTING.md):
  - "game version 1.60.x": it is the Forever flavor, version **1.60.1**.
  - "Both sites accept Markdown": on CurseForge, only after choosing the Markdown editor in the
    creation form.

---

## 9. Still open (not verified)

- The summary's character limit, if any.
- CurseForge's review time for WoW projects.
- Whether a project with only a Beta file appears in the CurseForge app, and where players opt in to
  Beta.
- The numeric CurseForge ID of version `1.60.1`. It needs a token, and the packager finds it by
  itself.
- What CurseForge's localization export returns for a new project with no phrases.
- Whether moderators treat a mid-page GitHub Releases link as mirroring the addon's files.
- Wago's review process and its description format.
- Whether CurseForge accepts a zip whose only TOC has a suffix (such as `_Camelot.toc`).

---

## Sources

**CurseForge support articles** (Official; IDs as cited above, with the "Modified on" date shown on
2026-11-05)

- [9000197241 Creating and Submitting a Project](https://support.curseforge.com/en/support/solutions/articles/9000197241-creating-and-submitting-a-project) (2024-09-24)
- [9000199552 Project Submission Guide and Tips](https://support.curseforge.com/en/support/solutions/articles/9000199552-project-submission-guide-and-tips) (2024-12-23)
- [9000197279 Moderation Policies](https://support.curseforge.com/en/support/solutions/articles/9000197279-moderation-policies) (2026-10-22)
- [9000197242 File & Project Types and Additional Fields](https://support.curseforge.com/en/support/solutions/articles/9000197242-file-project-types-and-additional-fields) (2026-07-09)
- [9000197905 Project Statuses 101](https://support.curseforge.com/en/support/solutions/articles/9000197905-project-statuses-101) (2024-09-24)
- [9000197321 CurseForge Upload API](https://support.curseforge.com/en/support/solutions/articles/9000197321-curseforge-upload-api) (2026-09-27)
- [9000210425 CurseForge File Processor Errors - Per Game](https://support.curseforge.com/en/support/solutions/articles/9000210425-curseforge-file-processor-errors-per-game) (2024-02-28)
- [9000205250 System Rejection Messages](https://support.curseforge.com/en/support/solutions/articles/9000205250-system-rejection-messages) (2024-03-05)
- [9000284625 Supported HTML Tags & CSS for Project Descriptions](https://support.curseforge.com/en/support/solutions/articles/9000284625-supported-html-tags-css-for-project-descriptions) (2026-09-15)
- [9000207877 Project Distribution Toggle](https://support.curseforge.com/en/support/solutions/articles/9000207877-project-distribution-toggle) (2024-10-31)
- [9000198422 World of Warcraft Addons - FAQ and Troubleshooting](https://support.curseforge.com/en/support/solutions/articles/9000198422-world-of-warcraft-addons-faq-and-troubleshooting) (2026-07-07)
- [9000197281 Automatic Packaging](https://support.curseforge.com/en/support/solutions/articles/9000197281-automatic-packaging) (2024-02-21)
- [9000197354 Localization Substitutions](https://support.curseforge.com/en/support/solutions/articles/9000197354-localization-substitutions)
- [9000197998 CurseForge Website Rules](https://support.curseforge.com/en/support/solutions/articles/9000197998) (2024-12-09)

**CurseForge blog and other documentation**

- [How to Pass Moderation Review](https://blog.curseforge.com/how-to-pass-moderation-review-on-curseforge-2/) (2025-02-09, updated 2025-05-06)
- [How to Create the Best Project Page](https://blog.curseforge.com/how-to-create-the-best-project-page/) (2024-10-27, updated 2025-05-06)
- [Moderation as a service](https://docs.curseforge.com/docs/capabilities/moderation/moderation-as-a-service) (documentation for game studios: the 24-hour figure)

**CurseForge pages** (Observed, 2026-11-05)

- [WoW addons, Audio & Video category](https://www.curseforge.com/wow/addons/audio-video): the category list, the Forever flavor filter (`gameVersionTypeId=88568`) and version `1.60.1`
- [Leatrix Plus file 9467366](https://www.curseforge.com/wow/addons/leatrix-plus/files/9467366): a Forever-only file
- [OmniCC](https://www.curseforge.com/wow/addons/omni-cc): the "MIT License" option, and Source and Issues links to GitHub
- Project pages: [Raider.IO](https://www.curseforge.com/wow/addons/raiderio), [WeakAuras](https://www.curseforge.com/wow/addons/weakauras-2), [SimulationCraft](https://www.curseforge.com/wow/addons/simulationcraft), [TomTom](https://www.curseforge.com/wow/addons/tomtom), [Baganator](https://www.curseforge.com/wow/addons/baganator), [Details!](https://www.curseforge.com/wow/addons/details), [Questie](https://www.curseforge.com/wow/addons/questie), [Plater](https://www.curseforge.com/wow/addons/plater-nameplates), [Forever Enhanced Cursor](https://www.curseforge.com/wow/addons/forever-enhanced-cursor)
- File lists and descriptions came through [CFWidget](https://api.cfwidget.com/wow/addons/tomtom), a third-party mirror of CurseForge data at `https://api.cfwidget.com/wow/addons/<slug>`. The CurseForge pages above confirmed its figures.

**BigWigs packager** (Code and Official)

- [Repository](https://github.com/BigWigsMods/packager): `release.sh` and `README.md` at tag [v2.7.0](https://github.com/BigWigsMods/packager/tree/v2.7.0) (commit `01e8631a69c69d7654c788d152630d5fed9e3812`), compared with v2.6.1 and v2.5.1
- [Release notes](https://github.com/BigWigsMods/packager/releases) for v2.6.0, v2.6.1 and v2.7.0
- [PR #202 Add WoW Forever support](https://github.com/BigWigsMods/packager/pull/202) and its review comments
- [Issue #204 Error when having 16001 in -Mainline.toc](https://github.com/BigWigsMods/packager/issues/204)

**Wago** (Official and Observed)

- [Game data API](https://addons.wago.io/api/data/game)
- [Developer documentation](https://docs.wago.io/)
- [Developer agreement](https://addons.wago.io/agreements/developer-agreement)

**WoWInterface** (Observed)

- [Compatible versions API](https://api.wowinterface.com/addons/compatible.json)

**Blizzard and the game client** (Official)

- [UI Add-On Development Policy](https://us.forums.blizzard.com/en/wow/t/ui-add-on-development-policy/24534), with the 2009 note on music in add-ons
- [warcraft.wiki.gg: TOC format](https://warcraft.wiki.gg/wiki/TOC_format): Forever interface `16001`, the `_Forever`/`_Camelot`/`_Mainline` suffixes and the `[Game]` value "Camelot"
