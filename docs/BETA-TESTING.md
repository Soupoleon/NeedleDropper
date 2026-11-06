# Testing the NeedleDropper beta

Hi Andrew, and anyone else trying NeedleDropper before it goes public: thank you! This guide takes you
from nothing to your first needle drop in about half an hour, without any programming tools. Then it
shows what to look out for and how to tell us what you found.

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
- A free **GitHub account** (<https://github.com/signup>). The beta lives in a private GitHub
  repository: send the owner your GitHub user name, then accept the invitation. It arrives by email
  and on <https://github.com/notifications>.
- Something to play the music, one of:
  - **Spotify Premium** and **Google Chrome** (<https://www.google.com/chrome/>). The app plays
    Spotify songs itself, in a hidden Chrome window; you don't need the Spotify desktop app. A free
    Spotify account doesn't work: Spotify's player for apps is Premium only. Or
  - nothing at all, for **YouTube**: Microsoft Edge, which comes with Windows, shows a small player
    (Chrome works too).
- About 30 minutes for the setup and the quick test.

## 1. Download the beta

1. Sign in to GitHub and open the **Releases** page:
   <https://github.com/Soupoleon/NeedleDropper/releases>. (Until you have accepted the invitation it
   shows "404 Not Found".)
2. Pick the newest release marked **Pre-release**, for example *NeedleDropper 1.2.1 beta 1*. Note its
   tag, `v1.2.1-beta.1`: the bug report form asks for it.
3. Under **Assets**, download:

   | File | What it is |
   |---|---|
   | `NeedleDropper-<version>-addon.zip` | The addon |
   | `NeedleDropper-<version>-win64.zip` | The app, for Spotify and YouTube (skip it if you only want title cards) |

   The release has three more files: `NeedleDropper.exe` (the same app without the zip),
   `NeedleDropper-<version>-win64-folder.zip` (the same app as a folder, see
   [step 3](#3-start-the-app)) and `SHA256SUMS.txt`.
4. *Optional, for the careful:* `SHA256SUMS.txt` lists each file's fingerprint. In PowerShell, run
   `Get-FileHash .\NeedleDropper-1.2.1-win64.zip` in your Downloads folder and compare the long code
   with the matching line (upper or lower case doesn't matter).

## 2. Install the addon

From the release's addon zip, as below. (Once the owner tells you NeedleDropper is live on
CurseForge, you can install it from there instead, with the CurseForge app or from the project's
**Files** tab; pick the newest **Beta** file for WoW: Forever. It is the same addon, but always use
an addon and an app of the same version: a 1.2.1 app can't read a 1.2.0 addon's signals.)

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

Tried an earlier beta? Quit it first (right-click its tray icon → **Quit**), close its player window
if one stays open (a small window titled *NeedleDropper*; 1.2.0 left it running), then put the new
`NeedleDropper.exe` in the same place. Your settings are kept, but **sign in to Spotify again** after
updating from 1.2.0 (step 4): the new Spotify player needs two more permissions. In your Spotify
developer app (dashboard → your app → **Settings** → **Edit**) tick **Web Playback SDK** too and
**Save**. Update the addon as well: the 1.2.1 app can't read a 1.2.0 addon's signals.

1. Right-click `NeedleDropper-<version>-win64.zip`, choose **Extract All…** and pick a permanent
   place, for example `Documents\NeedleDropper`. Don't run the app from inside the zip.
2. **Start the app before the game the first time.** It creates a tiny data addon,
   `NeedleDropper_Companion`, and the game only notices new addon folders when it starts.
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

### Option A: Spotify, through the owner's developer app

The usual choice if you have Spotify Premium. Spotify only lets an app play music after a developer
registers it, and each registered app allows at most 5 people, so the owner registered one for the
beta and adds you to it.

1. Send the owner the **email address of your Spotify account** (it is on your Spotify account page,
   <https://www.spotify.com/account/>). The owner adds it to their app's *User Management* list.
2. The owner sends you their app's **Client ID**: a code of 32 digits and letters. (Nobody needs the
   app's *client secret*, and you should never be sent one.)
3. Once the owner says you're added: on the Connect page's **Spotify** card, paste the Client ID and
   click **Sign in**.
4. Spotify's own page opens. It shows the owner's app name and asks to let it play music in
   NeedleDropper's player, see and control what plays on your devices, see your email address
   (Spotify's player requires that; NeedleDropper never reads or keeps it) and your account's
   subscription and country (to check Premium and pick songs available where you live). Click
   **Agree**. Your password only ever goes to Spotify.
5. The card now shows your account, the **Premium** check, your **Country** and **Play on**:
   *NeedleDropper (this app)*. That is the app's own player: you don't need to open the Spotify
   desktop app. While NeedleDropper runs, your Spotify device list (on your phone, say) shows a
   device called **NeedleDropper**. When a drop starts, Spotify moves your playback there for the
   song; whatever you were listening to comes back afterwards.
6. Press **Test Spotify** on the card: within a few seconds you hear 15 seconds of Toto's *Africa*,
   and nothing shows on screen.

If the card says **Account not allowed**, the owner hasn't added your email yet, or added a
different one. **Premium needed** means Spotify sees no Premium on this account. *Spotify can't play
in NeedleDropper's player: install Google Chrome…* means what it says (if Chrome is installed, start
it once, wait a minute, then restart the NeedleDropper app).

### Option B: Spotify, through your own developer app

About 5 minutes, and nobody has to add you.

1. Open <https://developer.spotify.com/dashboard> and log in with the Spotify account you listen
   with. The first time, accept the Spotify Developer Terms.
2. Click **Create app**. Any name and description will do. Under **Redirect URIs**, type exactly
   `http://127.0.0.1:8321/callback` and click **Add** (`127.0.0.1`, not `localhost`; `http`, not
   `https`; no `/` at the end). Under *Which API/SDKs are you planning to use?* tick **Web API**
   and **Web Playback SDK**, agree to the terms and click **Save**.
3. Copy your app's **Client ID** (on its **Settings** page), then continue with step 3 of Option A.

The full walk-through, with the error messages you might see:
[Music services → Spotify Premium](PROVIDERS.md#spotify-premium).

### Option C: YouTube only

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

With Spotify connected, YouTube is also the backup: if Spotify doesn't start a song, NeedleDropper
should play it on YouTube instead and tell you with a notification. Please report it whenever that
happens (or when it doesn't, and you just get silence).

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
   plays yet: this only checks that the app can see the game.

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
  Windows scaling, HDR, display mode, UI scale), keyboard or controller, and your music service. On a
  private repository GitHub doesn't enforce the required fields, so please fill them in anyway.
- **`/needle report`.** Type it in the game where the problem happened: a window opens with a text
  report, already selected. Press Ctrl+C and paste it into the issue. It has the versions, what the
  app reported, the beacon and your settings, your last 5 drops and the last error, and no account
  names, Client IDs or tokens.
- **`/needle status`.** Type it there too and attach a screenshot of the chat (WoW can't copy chat
  text).
- **The app's log.** Right-click the tray icon, choose **Open log folder** and attach
  `needledropper.log`. If the folder has several log files, zip them.
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
- **The owner's Spotify app (Option A).** The owner sees the email address you gave them in their
  developer dashboard, and Spotify's usage numbers for their app. They never see your password or
  your login: the login token stays on your PC, encrypted with Windows (DPAPI) so that only your
  Windows account can read it. **Sign out of Spotify** on the Spotify card deletes it.
- **The beacon strip** in the top-left corner shows up in screenshots, recordings and streams. It
  carries only the needle-drop event (the song, the place, the soundtrack's name and a few flags such
  as "in combat"), nothing about you. If you stream, crop that corner, and don't broadcast Spotify
  music: Spotify is for personal listening only.
- **Your reports.** Everyone with access to the repository can read your issues and attachments.
  The app's log never contains passwords or tokens, but file paths in it can show your Windows user
  name, and it names your Spotify account; edit them out if you like. The `/needle report` text has
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
6. **Spotify:** remove NeedleDropper's access on <https://www.spotify.com/account/apps/>, and ask the
   owner to take you off their User Management list (it frees a place for someone else).
7. **GitHub:** leave the repository under *Settings → Repositories* on github.com, or ask the owner
   to remove you.

All the details: [Uninstalling](INSTALL.md#uninstalling).

---

## For the owner: getting a tester ready

- [ ] **GitHub:** add the tester to the repository (*Settings → Collaborators*). On a personal
      account every collaborator can also push; for read-only testers, keep the repository in a free
      organization and give them the *Read* role.
- [ ] **While the repository is private:** set the repository variable `ADDON_SITE_UPLOADS` to `off`
      (*Settings → Secrets and variables → Actions → Variables*). Otherwise a beta tag also uploads the
      addon to CurseForge, whose page links the app's GitHub releases, which only collaborators can open.
- [ ] **A release:** push a beta tag (`v1.2.1-beta.N`). The release workflow builds and publishes the
      pre-release with its five files; check that they are there. The addon and the app must come
      from the same release: a 1.2.0 app and a 1.2.1 addon can't read each other.
- [ ] **Spotify (Option A):** in the developer dashboard, open your app → **Settings** → **User
      Management**, add the tester's name and the email address of their Spotify account, then send
      them the Client ID (never the client secret). Spotify allows at most 5 accounts per developer
      app (you count as one), and the app keeps working only while you, its owner, have Premium.
      While you're there, check that **Web API** and **Web Playback SDK** are both ticked under
      *APIs used*.
- [ ] **Send** this guide and the link to the release.
- [ ] **New beta?** Tell your testers yourself: the app's update check never reports pre-releases.
