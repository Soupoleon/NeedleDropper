# Testing the NeedleDropper beta

This guide takes you from a clean Windows PC to your first needle drop, without any programming
tools. Install the **WoW: Forever addon from [CurseForge](https://www.curseforge.com/wow/addons/needledropper)**
(Beta channel, version 1.2.1), then get **`NeedleDropper.exe` from
[GitHub's beta 2 release](https://github.com/Soupoleon/NeedleDropper/releases/tag/v1.2.1-beta.2)**.
Already have the 1.2.1 CurseForge addon? You only need the new companion. A complete GitHub-only
route is below if you prefer it or the CurseForge file is still awaiting moderation.

> **What is NeedleDropper?** The first time your character arrives somewhere new in World of Warcraft:
> Forever, the zone music fades, one hand-picked song starts, and a quiet title card drifts in at the
> top of the screen, like the "needle drops" in *Death Stranding*. Then the world goes quiet again
> until you find somewhere new.

NeedleDropper has two parts:

- **The addon** runs inside the game. It knows where you are, decides when a song starts and shows
  the title card. Everything you change happens in game: type `/needle`.
- **The app**, `NeedleDropper.exe`, runs on your Windows PC and plays the songs through **Spotify
  Premium** (in a hidden player of its own: no Spotify window) or **YouTube** (in a small box). It is
  optional: without it you still get the title cards, but no Spotify or YouTube music.

**Contents:** [What you need](#what-you-need) ·
[1. Download](#1-download-the-beta) ·
[2. Install the addon](#2-install-the-addon) ·
[3. Start the app](#3-start-the-app) ·
[4. Pick your music](#4-pick-your-music) ·
[5. Check the link](#5-start-the-game-and-check-the-link) ·
[6. The quick test](#6-the-quick-test-about-15-minutes) ·
[7. Report](#7-tell-us-what-you-found) ·
[Privacy](#privacy) ·
[Uninstalling](#uninstalling) ·
[For the owner](#for-the-owner-getting-a-tester-ready)

## What you need

- A **Windows 10 or 11** PC (64-bit) with **World of Warcraft: Forever**.
- No GitHub account or invitation is needed to download the beta. A GitHub account is needed only
  if you want to file an issue yourself.
- Something to play the music, one of:
  - **Spotify Premium** and **Google Chrome** (<https://www.google.com/chrome/>). The app plays
    Spotify songs itself, in a hidden Chrome window; you don't need the Spotify desktop app. A free
    Spotify account doesn't work: Spotify's player for apps is Premium only. Or
  - nothing at all, for **YouTube**: Microsoft Edge, which comes with Windows, shows a small player
    (Chrome works too).
- About 30 minutes for the setup and the quick test.

## 1. Download the beta

1. Install the addon through [CurseForge](https://www.curseforge.com/wow/addons/needledropper), selecting
   **WoW: Forever**, **Beta** files and version **1.2.1** ([step 2](#2-install-the-addon)).
   If it is still awaiting moderation or does not appear, use the GitHub-only alternative below.
2. Open [NeedleDropper 1.2.1 beta 2](https://github.com/Soupoleon/NeedleDropper/releases/tag/v1.2.1-beta.2),
   or the [Releases page](https://github.com/Soupoleon/NeedleDropper/releases) for a newer beta.
   Note the tag, `v1.2.1-beta.2`: the bug report form asks for it.
3. Under **Assets**, download **`NeedleDropper.exe`** for Spotify and YouTube. The companion is
   always downloaded from GitHub, even if CurseForge manages your addon. These are all five assets:

   | File | What it is |
   |---|---|
   | `NeedleDropper-1.2.1-addon.zip` | The addon for manual installation; skip if CurseForge already installed 1.2.1 |
   | `NeedleDropper.exe` | The app, for Spotify and YouTube (skip it if you only want title cards) |
   | `NeedleDropper-1.2.1-win64.zip` | The same app with a quick start |
   | `NeedleDropper-1.2.1-win64-folder.zip` | The same app as a folder; keep its files together ([step 3](#3-start-the-app)) |
   | `SHA256SUMS.txt` | SHA-256 checksums for the four downloads above |

   Choose one form of the app; you do not need all three.
4. *Optional, for the careful:* `SHA256SUMS.txt` lists each file's fingerprint. In PowerShell, run
   `Get-FileHash .\NeedleDropper.exe` in your Downloads folder and compare the long code with its
   line (upper or lower case doesn't matter). For a zip download, check that zip's name and line instead.

**GitHub-only alternative:** download `NeedleDropper-1.2.1-addon.zip` and `NeedleDropper.exe` from
that same release. Install the zip as described below and start the app in step 3. This is the whole
installation: no CurseForge, repository clone, developer tools, GitHub account or invitation needed.

## 2. Install the addon

**CurseForge:** close WoW, select its **WoW: Forever** installation in the CurseForge app, find
**NeedleDropper**, and enable **Beta** files for it. Install the newest **1.2.1 Beta** file, or choose
it from the project's **Files** tab. If you already have the 1.2.1 addon, keep it and continue to
step 3; beta.2 does not change the base addon version. A 1.2.1 app cannot read a 1.2.0 addon's signals.

**GitHub-only/manual install:** if you downloaded the addon zip, follow these steps:

1. **Close WoW completely.** The game only loads new addon files when it starts.
2. Open the AddOns folder. Normally it is
   `C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns`. Installed somewhere else?
   In the Battle.net app, select WoW: Forever, click the gear next to **Play** and choose **Show in
   Explorer**, then open `_forever_\Interface\AddOns`. If `Interface` or `AddOns` doesn't exist yet,
   create it.
3. Already have a `NeedleDropper` folder there from an earlier build? Delete it first. Your settings
   and history are safe: they live in the game's `WTF` folder, not here. Leave `NeedleDropper_Local`
   and `NeedleDropper_Companion` alone if you see them.
4. Double-click the addon zip to open it and drag the **NeedleDropper** folder into the AddOns
   folder. (If Windows asks for permission to copy into the folder, click **Continue**.)
5. Check: the file `...\AddOns\NeedleDropper\NeedleDropper.toc` must exist. Not
   `...\AddOns\NeedleDropper\NeedleDropper\...`, and not `...\AddOns\NeedleDropper-1.2.1-addon\...`.

## 3. Start the app

Skip this step if you only want to try the title cards.

Tried an earlier beta? Quit it first (right-click its tray icon → **Quit**), then close its player window
if one stays open (a small window titled *NeedleDropper*; 1.2.0 left it running). Keep that order:
closing the window while 1.2.0 still runs makes it reopen. Then put the new
`NeedleDropper.exe` in the same place. Your settings are kept, but **sign in to Spotify again** after
updating from 1.2.0 (step 4): the new Spotify player needs two more permissions. In your Spotify
developer app (dashboard → your app → **Settings** → **Edit**) tick **Web Playback SDK** too and
**Save**. Update the addon as well: the 1.2.1 app can't read a 1.2.0 addon's signals.

1. Put `NeedleDropper.exe` somewhere permanent, for example `Documents\NeedleDropper`. If you
   downloaded `NeedleDropper-1.2.1-win64.zip`, right-click it and choose **Extract All…** first.
   Don't run the app from inside the zip.
2. **Start the app before the game the first time, and give it a moment on the desktop.** It never
   opens its player while WoW is in front. It also creates a tiny data addon,
   `NeedleDropper_Companion`, which the game only notices when it starts. If WoW is already open,
   return to the desktop for the player to start, then restart WoW once for the new data addon.
3. Double-click `NeedleDropper.exe`. Windows will probably show a blue box, **"Windows protected your
   PC"**, because the beta isn't code-signed (that costs money every year). Click **More info**,
   check that it says `NeedleDropper.exe`, then click **Run anyway**. Windows asks once for each new
   beta.
4. Did your antivirus program quarantine it? That is a known false alarm with apps packed this way.
   Use `NeedleDropper-<version>-win64-folder.zip` from the same release instead: the same app as a
   folder, which antivirus programs flag less often (keep the folder together and start
   `NeedleDropper.exe` inside it). Please tell the owner which antivirus program it was.
5. The **Connect page** opens in your web browser, at `http://127.0.0.1:8322/`. The app serves it from
   your own PC, and nothing outside your PC can reach it. If Windows Firewall asks about network
   access, you can click **Cancel**.

The Connect page has four cards:

- **World of Warcraft** finds your game and shows which NeedleDropper addon version is installed.
  (If the addon is missing, **Install addon** copies the one that comes with the app; close the game
  first.)
- **Spotify** and **YouTube**: see [step 4](#4-pick-your-music).
- **Check the link** gets a tick in [step 5](#5-start-the-game-and-check-the-link).

At the bottom, leave **Start with Windows** as you like, and click **Done** when you have finished.
After that the app has no window: it waits as an icon in the notification area next to the clock
(maybe hidden under the **^** arrow). Left-click it to open the Connect page again. Right-click it for
**Open setup…**, **Pause needle drops**, **Start with Windows**, **Open log folder** and **Quit**.

## 4. Pick your music

Spotify needs **Google Chrome** on the PC (install it first if you don't have it). You can set
Spotify up on the Connect page, as below, or in the game: type `/needle music`, paste the Client ID
with Ctrl+V into the box of step 3 and click **Connect Spotify**.

### Option A: Spotify, through your own developer app

Use your own Spotify Premium account and developer app; you do not need the project owner's
Client ID or an invitation from them.

1. Open the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and log in with
   the Spotify Premium account you listen with. Accept the Developer Terms if asked.
2. Click **Create app**. Any name and description will do. Under **Redirect URIs**, enter exactly
   `http://127.0.0.1:8321/callback` and click **Add** (`127.0.0.1`, not `localhost`; `http`, not
   `https`; no `/` at the end). Tick **Web API** and **Web Playback SDK**, agree to the terms and
   save. If you already have a developer app, add these settings to it: Spotify limits new
   development access to one Client ID per developer.
3. Open the app's **Settings** and copy its **Client ID**. Do not copy the Client secret.
4. On NeedleDropper's Connect page, paste the Client ID into the **Spotify** card and click
   **Sign in**. Approve the request on Spotify's page, which shows the app name you chose.
   Your password only goes to Spotify. You can also connect in game through
   *Options → AddOns → NeedleDropper → Music services* (`/needle music`).
5. Keep **Play on** set to **NeedleDropper (this app)**, the recommended choice for your gaming PC.
   No Spotify desktop app needs to run. To use the separate desktop app instead, open Spotify on
   your gaming PC, click **Refresh** beside **Play on**, and select **Spotify desktop app — your
   PC's name**. Choose **NeedleDropper (this app)** again to switch back. **Automatic Spotify
   Connect device** can select another PC or phone signed into your Spotify account.
6. In game, open *Options → AddOns → NeedleDropper → Music services* and press **Test Spotify**,
   then **Test YouTube**. Each plays 15 seconds of Toto's *Africa* using only that service.
   **Test Spotify never falls back to YouTube.** Spotify shows no player window; YouTube shows
   the small box. The same buttons are on the Connect page.

Spotify requires Premium for development apps and limits them to five authorized users; each
tester uses their own app here. These are [Spotify's developer access rules](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security).
If **Create app** is unavailable, check that the developer account has Premium and whether it
already owns an app. YouTube can be tested independently while you resolve that.

**Account not allowed**: check that you signed in with the account that owns your developer app;
if needed, add that account under the app's **Settings → User Management**. **Premium needed**:
check the Spotify account's subscription. **Install Google Chrome…**: install Chrome, or start
it once and give it a minute to download its playback component, then restart NeedleDropper.
If the player says it **went offline**, quit NeedleDropper from its tray and restart it from
the desktop, then try Test Spotify again. Please report a repeat failure.

The full walk-through: [Music services → Spotify Premium](PROVIDERS.md#spotify-premium).

**Speakers or headphones:** open **Windows Settings → System → Sound → Volume mixer** and set
**Google Chrome** to the wanted **Output device** for NeedleDropper's in-app Spotify player.
If you selected the separate desktop Spotify app, set **Spotify** instead. Check its mute and
volume too. [Microsoft's instructions](https://support.microsoft.com/en-au/windows/hardware/audio/fix-app-audio-not-working-while-system-sounds-work-in-windows)
explain these Windows settings; **Play on** selects the Spotify player.

**Check device selection in beta.2:** selecting a specific device saves its ID; duplicate names have extra identifying
details. If it goes offline or Spotify restricts control, the app reports the problem without
switching to another Spotify device. Open Spotify there, click **Refresh** beside **Play on**, and
select it again. Do the same if Spotify changes its ID or an older name-only choice is ambiguous.
Check this with **Test Spotify**: it should fail when your selected device is unavailable, even if
another device is online.

### Option B: YouTube only

Nothing to set up. When a YouTube song plays, a small player window (Edge, or Chrome) shows the
video. YouTube's rules say the player must stay visible while it plays, so don't cover it with the
game. If you have no Spotify, set **Play music with** to *YouTube only* in game (*Options → AddOns →
NeedleDropper*, section **Music services**).

- **Focus:** the player window is made once, hidden, when the app starts, shows on top of the game
  in the top-right corner (or the corner you choose under **YouTube box position**) only while a
  YouTube song plays, and never takes the focus. Please report whenever the game loses focus,
  minimizes or a browser tab opens when a song starts.
- **Test YouTube** (on the Connect page or in the game's Music services) plays 15 seconds in the box.
- The first time, the player may ask you to click once to enable sound.
- YouTube may show ads (unless you have YouTube Premium). NeedleDropper never blocks or skips them.

With **Play music with: Spotify, then YouTube**, YouTube is the backup for needle drops that
have a YouTube link. The **Test Spotify** button checks Spotify alone and reports a failure
without trying YouTube.

## 5. Start the game and check the link

The addon tells the app what to play through a **beacon**: a thin strip of coloured squares in the
very top-left corner of the game window, which the app reads from the screen. To keep it readable:

- In WoW's **Options → Graphics**, set **Display Mode** to **Windowed** or **Fullscreen (Windowed)**.
- Keep brightness, contrast and gamma at their defaults, and don't run filters such as ReShade.
- Keep FPS counters and overlay notifications out of the top-left corner. (Discord puts its overlay
  notifications there by default.)
- Windows HDR is best off. If you normally play with HDR on, try it on too and tell us whether the
  link still works.

Then:

1. Start the game. At the character selection screen, open **Menu → AddOns** and make sure
   **NeedleDropper** and **NeedleDropper Companion Data** are ticked. If NeedleDropper is listed as
   *Out of date*, tick **Load out of date AddOns**.
2. Log in, and type `/console scriptErrors 1` once so that addon errors show up. Take a screenshot of
   any error you see.
3. On your first login with NeedleDropper, the chat greets you with a line naming your soundtrack,
   *Azeroth Mixtape*.
4. Type **`/needle test`**. The test title card appears, the app shows a notification, and the
   **Check the link** card on the Connect page gets its tick, normally within 2 seconds. Nothing
   plays: this only checks that the app can see the game. **Send a test signal** in the addon
   settings does the same link-only check. Use **Test Spotify** and **Test YouTube** to check music.

No tick? Make sure the game isn't minimized, check the display mode and the top-left corner, then see
[Beacon not detected](TROUBLESHOOTING.md#beacon-not-detected). Note your screen setup for your report:
resolution, Windows scaling, HDR and display mode.

## 6. The quick test (about 15 minutes)

Needle drops are rare on purpose: each place plays once per character, the next drop waits 10
minutes, and places your character has already explored count as heard. For testing, relax these
rules first:

```
/needle set cooldownMin 0
/needle set historyMode fresh
/needle reset char
```

The first line removes the 10-minute wait; the second stops NeedleDropper from counting the places
you've already explored as heard, and the third forgets what this character has heard so far
(including the explored places NeedleDropper marked when you logged in, and the journal). You'll put
the first two back [at the end](#when-youre-done). Then pick your faction.

Don't be surprised if the song of the zone you are standing in starts by itself a few seconds after
the third line: you have been there long enough, so that is a real needle drop. `/needle stop` ends
it, if you want to start the route below in silence.

### Horde: Durotar → Razor Hill → Orgrimmar

1. **A song on demand.** Anywhere, type `/needle play Durotar`. The title card for Durotar fades in
   (the place in spaced capitals, a thin line, then the artist and the title) and Dolly Parton's
   *9 to 5* starts on Spotify or YouTube. `/needle stop` ends it. (This doesn't count as hearing the
   place.)
2. **Your own drop at Razor Hill.** Ride to Razor Hill, the orc town on the road to Orgrimmar.
   Standing in the town, type `/needle` and press **Add drop here**. Type an artist and a title, for
   example *ZZ Top* and *Sharp Dressed Man*, then press **Test**. (With YouTube only, paste a
   YouTube link instead: YouTube can't be searched by name. Open the song's official video on
   youtube.com and copy the address from the address bar.) The quick way:
   `/needle add ZZ Top - Sharp Dressed Man` or `/needle add <YouTube link>`.
   Then walk out of Razor Hill and back in. A few seconds later the zone music fades, the Razor Hill
   title card drifts in and your song starts.
3. **A real arrival.** Ride north to Orgrimmar. About 10 seconds after you arrive (sooner after a
   *Discovered: …* message), *Now We Are Free* by Hans Zimmer and Lisa Gerrard starts with its title
   card.

Durotar's own song may also play along the way. That's normal: it's a drop too.

### Alliance: Elwynn Forest → Goldshire → Stormwind

1. **A song on demand.** Anywhere, type `/needle play Elwynn Forest`. The title card for Elwynn
   Forest fades in and The Beatles' *Here Comes the Sun* starts. `/needle stop` ends it. (This
   doesn't count as hearing the place.)
2. **A built-in landmark.** Ride into Goldshire. A few seconds later the Eagles' *Hotel California*
   starts with the Goldshire title card. When it's over, try your own drop: ride to a
   nearby place with a name of its own, such as Fargodeep Mine south of Goldshire, type `/needle`,
   press **Add drop here**, type an artist and a title and press **Test**. (With YouTube only,
   paste a YouTube link instead: YouTube can't be searched by name.) Walk away and come back: your
   song plays.
3. **A real arrival.** Ride on to Stormwind City. Walking in through the Valley of Heroes, David
   Bowie's *"Heroes"* starts about 10 seconds after you arrive.

Elwynn Forest's own song may also play along the way. That's normal: it's a drop too.

### While you test, look out for

| What | What should happen |
|---|---|
| **Timing** | The music starts within about a second of the title card. Roughly how long did it take? |
| **Focus** | The game never loses focus: no alt-tab, no browser tab opening, nothing jumping in front |
| **Zone music** | It fades out when the song starts and comes back when the song ends, or after `/needle stop` |
| **Title card** | Readable, centred, not on top of other UI. Alt+Z hides it along with the rest of the UI |
| **Combat** | A fight that lasts longer than 5 seconds fades the song out |
| **The window** | `/needle` opens and closes it. Drag it, resize it, switch between **Drops**, **Soundtracks** and **Journal**. Escape closes it. It hides during combat and comes back afterwards |
| **Controller** | If you play with a gamepad: can you reach every button and text box in the window and its dialogs? Does the gamepad cursor ever get stuck? |
| **Sharing** | On the **Soundtracks** tab, select **My Drops** and press **Share...**, copy the code and send it to the owner (a Discord message is fine). Then press **Import...** and paste a code the owner sends you: the preview shows its name and places, and it arrives turned off |
| **Music services** | *Options → AddOns → NeedleDropper*, section **Music services**: the first row says what the app reported (for example *Spotify + YouTube ready*). **Test Spotify** and **Test YouTube** each play 15 seconds of Toto's *Africa*; change **Play music with** and the **YouTube box position** and check that the next drops follow |
| **Errors** | Any Lua error popup, or a message that NeedleDropper was blocked from an action |

### When you're done

Put the rules back:

```
/needle set cooldownMin 10
/needle set historyMode respect
```

(Or `/needle reset settings`, which puts every setting back to its default.) Then just play: new
places will surprise you now and then. If you want this character to hear the built-in drops again
later, `/needle reset char` forgets the places it has heard (and its journal).

## 7. Tell us what you found

Open the repository's **Issues** tab, click **New issue** and choose **Bug report** (something went
wrong) or **Idea or feature request**. One problem per report, please.

- **Fill in the form.** It asks for your setup: the release tag, the versions, your screen (resolution,
  Windows scaling, HDR, display mode, UI scale), keyboard or controller, and your music service.
- **`/needle report`.** Type it in the game where the problem happened: a window opens with a text
  report, already selected. Press Ctrl+C and paste it into the issue. It has the versions, what the
  app reported, the beacon and your settings, your last 5 drops and the last error, and no account
  names, Client IDs or tokens.
- **`/needle status`.** Type it there too and attach a screenshot of the chat (WoW can't copy chat
  text).
- **The app's log.** Right-click the tray icon and choose **Open log folder** to find
  `needledropper.log`. It names your Spotify account and can contain personal file paths: send it
  privately to the owner, or remove those details before attaching an excerpt to a public issue.
- **Screenshots or a short video** help a lot. WoW saves screenshots (Print Screen key) in
  `World of Warcraft\_forever_\Screenshots`.
- **"NeedleDropper has been blocked from an action"?** Type `/console taintLog 1`, do the same thing
  again, log out, attach `World of Warcraft\_forever_\Logs\taint.log`, then type
  `/console taintLog 0`.
- **Everything worked?** Tell the owner that too, with your screen setup. "It works on my PC" is just
  as useful as a bug.

## Privacy

The short version: NeedleDropper has no account, no server, no analytics and no telemetry. The full
version is in [Privacy](PRIVACY.md).

- **What leaves your PC.** Only the app talks to the internet:
  - to **Spotify**, if you connect it: your sign-in (on Spotify's own page), Spotify's own player
    (it streams the songs, like any Spotify player), play, pause and volume commands, the list of your
    devices and what is playing, and searches by artist and title for songs without a Spotify link;
  - to **YouTube**, when a YouTube song plays: the standard embedded player, which, like any embedded
    video, lets YouTube see your IP address and which video plays.

  Nothing about your game, characters, realm, chat or location is sent anywhere. The app's daily
  update check is off by default.
- **Your Spotify developer app.** Its Client ID belongs to you. Your login token stays on your PC,
  encrypted with Windows (DPAPI) so that only your Windows account can read it.
  **Sign out of Spotify** on the Spotify card deletes it.
- **The beacon strip** in the top-left corner shows up in screenshots, recordings and streams. It
  carries the needle-drop event and your music choices. During a Spotify connection request it
  also carries your Client ID. If you stream, crop that corner, and don't broadcast Spotify
  music: Spotify is for personal listening only.
- **Your reports.** Anyone can read this public repository's issues and attachments.
  The app's log never contains passwords or tokens, but file paths in it can show your Windows user
  name, and it names your Spotify account; remove them before posting publicly. The `/needle report` text has
  no account names, Client IDs or tokens.
- **The chat-log link** (an alternative to the beacon, see [Privacy](PRIVACY.md#the-optional-chat-log-link))
  stays off unless you turn it on. If you do, WoW writes all the chat your game shows to a file on
  your disk.

## Uninstalling

1. Right-click the tray icon, untick **Start with Windows**, then choose **Quit**.
2. Delete the folder that holds `NeedleDropper.exe`.
3. Delete the app's data: paste `%APPDATA%\NeedleDropper` into the address bar of a File Explorer
   window and delete that folder. It holds the app's settings, your encrypted Spotify login and the
   logs.
4. In `World of Warcraft\_forever_\Interface\AddOns`, delete `NeedleDropper` and
   `NeedleDropper_Companion`. Delete `NeedleDropper_Local` only if you put audio files of your own
   there and don't need them.
5. *Optional:* delete the addon's saved data, `NeedleDropper.lua` in
   `World of Warcraft\_forever_\WTF\Account\<account>\SavedVariables\` and in each character's
   `SavedVariables` folder below it.
6. **Spotify:** remove your developer app's access on [your Spotify apps page](https://www.spotify.com/account/apps/).
   You can delete the developer app from your dashboard too if nothing else uses it.

All the details: [Uninstalling](INSTALL.md#uninstalling).

---

## For the owner: getting a tester ready

- [ ] **A release:** push a beta tag (`v1.2.1-beta.N`). The release workflow builds and publishes the
      pre-release with its five files; check that they are there and match `SHA256SUMS.txt`.
      Check CurseForge's Beta file for WoW: Forever, and give testers the GitHub addon zip if it
      awaits moderation. Both parts need base version 1.2.1; an existing 1.2.1 CurseForge addon
      works with the beta.2 app. A 1.2.0 app and a 1.2.1 addon cannot read each other.
- [ ] **Spotify:** point the tester to step 4 to create their own developer app. Never send them
      the owner's Client ID, tokens or Client secret.
- [ ] **Send** this guide and the link to the release.
- [ ] **New beta?** Tell your testers yourself: the app's update check never reports pre-releases.
