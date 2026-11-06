# Releasing NeedleDropper (phased public release)

NeedleDropper ships as two pieces, and they go to different places:

| Piece | Where it goes | How players update |
|---|---|---|
| **Addon** (`NeedleDropper/` folder) | CurseForge, Wago Addons and (optionally) WoWInterface | Their addon manager (CurseForge app, Wago app, WowUp) updates it automatically |
| **App** (`NeedleDropper.exe`, optional, needed only for Spotify and YouTube) | GitHub Releases | Once `updates.releasesUrl` points at the releases (it is empty in 1.2.1, so the check is off), the app checks them daily and shows a tray notice. It never updates itself without a click. |

Addon sites don't host Windows executables, so the app lives on GitHub and the addon page links to it. This is the same arrangement as the Raider.IO client and the WeakAuras Companion. The addon also works on its own (title cards, in-game audio files), which keeps the listing legitimate.

## Phases

| Phase | Audience | Channel | Exit criteria |
|---|---|---|---|
| **0: Friends beta** | You + Andrew (+ a few) | Private GitHub repo and release `v1.2.1-beta.N` | The FIRST-TEST.md checklist passes on 2+ PCs (different screens, keyboard + gamepad); Spotify and YouTube drops play; no taint |
| **1: Public beta** | Anyone who opts in | CurseForge / Wago **Beta** channel (or Alpha) plus a public GitHub release marked *pre-release* | 2-3 weeks without critical bugs; share codes tested between players |
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

The **BigWigs packager** GitHub Action (`BigWigsMods/packager@v2`) does the following:
- reads `.pkgmeta` (already in the repo);
- substitutes `@project-version@` where it appears (the TOC's `## Version:` is written by hand today,
  `1.2.1`; `tools/build_release.ps1` warns when it differs from the app's version);
- zips `NeedleDropper/`;
- uploads it to every site whose project id is in the TOC;
- chooses alpha, beta or release from the tag.

The same workflow builds `NeedleDropper.exe` with `tools/build_release.ps1` and attaches it to the GitHub release.

What it needs, once:

1. **A GitHub repo**, private for Phase 0. It can go public in Phase 1, since the code is MIT.
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
  - Friends can be added to the owner's app under User Management (up to 5).
  - The "synchronising recordings with visual media" clause is a grey area, flagged in POLICY.md.
- **YouTube:** the visible player window satisfies the API rules. Never a hidden player.

## Friends beta: what a tester like Andrew needs

1. WoW: Forever on Windows.
2. The addon zip, extracted into `World of Warcraft\_forever_\Interface\AddOns\`, or later via the CurseForge app.
3. `NeedleDropper.exe` (optional). Windows SmartScreen will warn because the build is unsigned: *More info → Run anyway*.
4. Music (one of):
   - **Spotify via the owner's app:** the owner adds the tester's Spotify email under *User Management* in the developer dashboard. The tester enters the owner's Client ID (on the Connect page, or in game with `/needle music`) and signs in. The tester needs their own **Premium** and **Google Chrome** (the app plays Spotify in its own Chrome player).
   - **Spotify via their own app:** about 5 minutes on developer.spotify.com (tick **Web API** and **Web Playback SDK**).
   - **YouTube only:** nothing to set up.
5. The test checklist: docs/FIRST-TEST.md.
6. Where to report: GitHub Issues (bug template). Paste the `/needle report` text (no account names, Client IDs or tokens) and attach the app's log (tray → Open log folder).
