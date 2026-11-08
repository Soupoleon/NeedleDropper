# Installing NeedleDropper

NeedleDropper has two parts:

- the **addon**, which runs inside World of Warcraft: Forever. Everything you do with NeedleDropper
  happens there: the NeedleDropper window, your soundtracks, the settings.
- the **NeedleDropper app**, `NeedleDropper.exe`, for Windows. It is **optional**: it plays the
  Spotify and YouTube songs, sits quietly in the notification area of the taskbar, and you set it up
  once on its **Connect** page.

Without the app you still get the title cards and the songs you play from your own
[in-game audio files](#in-game-audio-files). The built-in soundtracks are Spotify and YouTube songs,
so to hear them you need the app.

**Contents:** [What you need](#what-you-need) ·
[1. Install the addon](#1-install-the-addon) ·
[2. First login](#2-first-login) ·
[3. The app (optional)](#3-optional-the-needledropper-app) ·
[4. The Connect page](#4-the-connect-page) ·
[5. Check the link](#5-check-the-link) ·
[Music services in game](#music-services-in-game) ·
[Game settings](#game-settings-for-the-beacon) ·
[Upgrading from 1.2.0](#upgrading-from-120) ·
[Upgrading from 1.1](#upgrading-from-11) ·
[Updating](#updating) ·
[Uninstalling](#uninstalling)

## What you need

- **World of Warcraft: Forever.** Its files live in the `_forever_` folder inside your World of
  Warcraft folder, by default `C:\Program Files (x86)\World of Warcraft\_forever_`.
- For the app (optional):
  - **Windows 10 or 11** (64-bit), and
  - something to play the music, at least one of:
    - **Spotify Premium** and **your own Spotify developer app**, for either Spotify mode.
      **NeedleDropper (this app)** needs **Google Chrome** for its hidden player; Microsoft Edge
      cannot run that player. Alternatively, select the installed **Spotify desktop app** under
      **Play on**; this mode does not need Chrome.
    - **Microsoft Edge** (built into Windows) or **Google Chrome**, for YouTube.

You do **not** need Python, developer tools, a repository clone or any command line. Public GitHub
downloads need no GitHub account or invitation.

## 1. Install the addon

**Install the addon from [CurseForge](https://www.curseforge.com/wow/addons/needledropper), then download
the companion app from [GitHub's 1.2.1 beta 4 release](https://github.com/Soupoleon/NeedleDropper/releases/tag/v1.2.1-beta.4).**
If the CurseForge file is awaiting moderation or is absent from your addon manager, use the
GitHub-only route below. See also [Beta testing](BETA-TESTING.md).

**Updating from beta 3?** Keep its addon and update [the companion app](#3-optional-the-needledropper-app)
to beta 4. This release improves companion cleanup and Spotify diagnostics; it is not a confirmed
fix for Spotify playback failures. The release still includes an addon zip for new installations.
From an earlier beta, update both parts for the location fixes, volume setting and minimap icon.
The base versions still say 1.2.1; that alone does not identify the beta.

### With an addon manager

Close WoW. In CurseForge, select your **World of Warcraft: Forever** installation, search for
**NeedleDropper**, and allow **Beta** files for this addon. Install the newest **1.2.1 Beta** file
for WoW: Forever (or choose it from the project's **Files** tab). The manager keeps the addon up
to date; it does not install `NeedleDropper.exe`. Managers only replace the `NeedleDropper`
folder. Your own audio files live in a separate `NeedleDropper_Local` folder, and the app's small
data addon in `NeedleDropper_Companion`; managers never touch either.

### From the addon zip

This is the complete **GitHub-only alternative**: download both `NeedleDropper-1.2.1-addon.zip`
and `NeedleDropper.exe` from [1.2.1 beta 4](https://github.com/Soupoleon/NeedleDropper/releases/tag/v1.2.1-beta.4).
Install the addon below, then follow [step 3](#3-optional-the-needledropper-app) for the app.
You do not need CurseForge, a repository clone, developer tools or a GitHub account.

1. Close WoW and download `NeedleDropper-1.2.1-addon.zip` from
   the [beta 4 release](https://github.com/Soupoleon/NeedleDropper/releases/tag/v1.2.1-beta.4).
2. Open your AddOns folder: `World of Warcraft\_forever_\Interface\AddOns`. If `Interface` or
   `AddOns` doesn't exist yet, create it.
3. Double-click the zip to open it and drag the **NeedleDropper** folder into the AddOns folder.
   You should end up with `...\AddOns\NeedleDropper\NeedleDropper.toc`, not
   `...\AddOns\NeedleDropper\NeedleDropper\...` or `...\AddOns\NeedleDropper-<version>-addon\...`.
4. For Spotify or YouTube, [start the companion](#3-optional-the-needledropper-app) before starting
   WoW. Otherwise start the game now. WoW only loads new addon files when it starts.

### Check that it loads

On the character selection screen, open **Menu → AddOns** and make sure **NeedleDropper** is ticked.
If it is listed as *Out of date* after a game patch, tick *Load out of date AddOns*.

## 2. First login

The chat window greets you with *NeedleDropper 1.2: your soundtrack is Azeroth Mixtape. Type /needle
to change a song or pick another soundtrack.*

- **The window:** type `/needle` (or `/nd`), or left-click NeedleDropper's minimap icon. Drag the icon
  around the minimap; **Show minimap icon** in settings hides it. The add-on compartment also opens
  the window. Its tabs are **Drops**, **Soundtracks** and **Journal**; see
  [Soundtracks](PACKS.md) for everything it does.
- **The settings:** right-click the minimap icon or NeedleDropper in the add-on compartment, use the gear button in the
  window, or open *Game Menu → Options → AddOns → NeedleDropper*.
- **Key bindings** (optional): *Game Menu → Options → Keybindings*, section **NeedleDropper**: *Open
  or close the NeedleDropper window*, *Add a needle drop where you stand* and *Stop the needle
  drop*.

Then just play, and go somewhere your character has never been. Drops are deliberately rare: after
one plays, the next one waits at least 10 minutes (you can change that in the options).

Spotify and YouTube songs need the app ([next step](#3-optional-the-needledropper-app)). Until the
game has seen it, a new place shows only its title card (the zone music keeps playing, and the chat
says so once). Such places are not counted as heard: each one plays its song the first time you come
back with the app running.

## 3. Optional: the NeedleDropper app

The companion always comes from GitHub, including when CurseForge manages your addon.

1. Download **`NeedleDropper.exe`** from
   [1.2.1 beta 4](https://github.com/Soupoleon/NeedleDropper/releases/tag/v1.2.1-beta.4).
   `NeedleDropper-1.2.1-win64.zip` contains the same executable and a quick start.
2. Put the executable somewhere permanent, such as `Documents\NeedleDropper`. If you chose the
   zip, right-click it and choose **Extract All…** first. Don't run the app from inside the zip or
   from a temporary folder: the Connect page warns you when it does, and **Start with Windows**
   can't be turned on from there.
3. *Optional, for the careful:* compare the file's fingerprint with `SHA256SUMS.txt` from the
   releases page. In PowerShell: `Get-FileHash .\NeedleDropper.exe` and compare the long code with
   the `NeedleDropper.exe` line (case doesn't matter).
4. Double-click `NeedleDropper.exe`. Its **Connect** page opens in your browser
   ([next step](#4-the-connect-page)).

The release has five assets. Choose one app download; you do not need all three forms of the app.

| Asset | Use |
|---|---|
| `NeedleDropper-1.2.1-addon.zip` | Manual addon install; skip if beta 3 or later is already installed |
| `NeedleDropper.exe` | Companion app on its own |
| `NeedleDropper-1.2.1-win64.zip` | Same app with a quick start |
| `NeedleDropper-1.2.1-win64-folder.zip` | Same app in a folder; keep its files together |
| `SHA256SUMS.txt` | SHA-256 checksums for the four downloads above |

**Start the app before the game the first time, and give it a moment on the desktop.** It never
opens its player while WoW is in front. If the game is already open, return to the desktop to let
the player start. On its first run the app also creates a small data
addon, `NeedleDropper_Companion`, through which it tells the game whether it is connected. WoW only
discovers new addon folders when it starts, so if the game was already running, restart it once (the
Connect page's **World of Warcraft** card says *Restart World of Warcraft once* until you do).

### "Windows protected your PC"

The first time you start `NeedleDropper.exe`, Windows SmartScreen may show a blue *"Windows protected
your PC"* box with *"Unknown publisher"*. That happens to every new app that isn't signed with a paid
code-signing certificate, and NeedleDropper (a free fan project) doesn't have one.

1. Click **More info**.
2. Check that the app is `NeedleDropper.exe`.
3. Click **Run anyway**. Windows remembers this for the file.

Some antivirus programs wrongly flag apps packed with PyInstaller (the tool that bundles a Python app
into a single .exe). If yours quarantines `NeedleDropper.exe`, check its fingerprint as above, restore
it and add an exception, or build it yourself from the open source code (see the
[README](../README.md#building-from-source)).

## 4. The Connect page

The Connect page is at `http://127.0.0.1:8322/`. It is served by the app on your own PC: it only
works while the app runs, and nothing outside your PC can reach it. NeedleDropper doesn't need any
firewall access; if Windows ever asks, you can cancel. It has four cards:

| Card | What it does |
|---|---|
| **World of Warcraft** | Finds your game and shows which addon version is installed. If the addon is missing, it links to CurseForge and offers **Install addon** (close the game first). It doesn't offer to install when an addon manager looks after the addon |
| **Spotify** | Optional; both player modes need Premium and your own developer app. NeedleDropper's own player also needs Google Chrome; desktop mode uses the installed Spotify app instead. Create your developer app with **Web API** and **Web Playback SDK**, set redirect URI `http://127.0.0.1:8321/callback`, paste its Client ID, then **Sign in**. Choose **Play on: NeedleDropper (this app)** (recommended) or your Spotify desktop app and use **Test Spotify**. The card also shows the account details Spotify supplies. You can connect in game too ([Music services in game](#music-services-in-game)). Full steps: [Music services](PROVIDERS.md#spotify-premium) |
| **YouTube** | Nothing to set up. A small player window shows on top of the game, in the top-right corner (or the corner you choose in game), while a YouTube song plays, and never takes the focus from the game ([why it must stay visible](PROVIDERS.md#the-player-window-must-stay-visible)). **Test YouTube** plays a test song there; **Sign in to YouTube** is optional (YouTube Premium, no ads) |
| **Check the link** | *In WoW type /needle test*. A tick appears when the app sees the game, normally within 2 seconds ([next step](#5-check-the-link)) |

**Choose where Spotify plays:** select **NeedleDropper (this app)** for its own player on your gaming
PC. To use the separate Spotify desktop app, open Spotify on that PC, click **Refresh** beside
**Play on**, and select **Spotify desktop app — your PC's name**. You can switch back at any time.
**Automatic Spotify Connect device** can choose another PC or phone; select a specific device when
you want to control where it plays. For speakers/headphones, use
[Windows audio output settings](PROVIDERS.md#speakers-or-headphones-on-your-pc).

At the bottom: **Start with Windows** (ticked by default) and **Done**.

After that the app lives in the notification area of the taskbar (maybe under the **^** arrow). It
has no window of its own and no console. Left-click its icon to open the Connect page again;
right-click it for the menu: a status line, **Open setup…**, **Pause needle drops**, **Start with
Windows**, **Open log folder** and **Quit**.

With **Start with Windows** ticked, the app starts quietly when you sign in to Windows and waits until
World of Warcraft: Forever is running; only then does it start reading the game's beacon. It shows a
notification only when something matters (linked to the game, a test signal, the result of a
request you sent from the game, a Spotify sign-in that expired or needs renewing, Spotify not
starting songs, a song it couldn't find or play, a new version). When its update check is on, it
looks for a new stable version once a day; it never installs anything by itself. It does not report
pre-releases: check [Releases](https://github.com/Soupoleon/NeedleDropper/releases) or the owner's
message for the next beta.

The app also works from a command prompt for a few tools (`NeedleDropper.exe --help` lists them):
it prints into the window you started it from. Windows doesn't wait for it, so in `cmd` type
`start /wait NeedleDropper.exe --version` to keep the prompt from coming back before the output.

## 5. Check the link

1. Start the game and log in a character.
2. Type `/needle test`. The test title card appears in the game, the app shows a notification, and
   the **Check the link** card on the Connect page gets its tick. Nothing plays: this only checks
   that the app can see the game.
3. No notification after a few seconds? See [Beacon not detected](TROUBLESHOOTING.md#beacon-not-detected).

Then go somewhere new. Don't minimize the game while you play: while WoW is minimized the app can't
see the beacon, so a song that should start only begins once you bring the game back.

## Music services in game

Everything the Connect page does for Spotify and YouTube can also be done in the game, in
*Game Menu → Options → AddOns → NeedleDropper*, section **Music services** (search for *Spotify* or
*YouTube* in the Settings panel):

| Row | What it does |
|---|---|
| **NeedleDropper app** | What the app reported when you logged in, for example *Spotify + YouTube ready*. Click it for the **Music services** dialog |
| **Spotify account** | *Set up Spotify…*, *Connect Spotify*, *Sign in again* or *Manage…*, depending on the state. It opens the dialog or sends the request to the app |
| **YouTube account** | *Sign in to YouTube…*: optional, see [YouTube](PROVIDERS.md#youtube) |
| **Play music with** | *Spotify, then YouTube* (the default), *YouTube, then Spotify*, *Spotify only*, *YouTube only* or *Neither* ([more](PROVIDERS.md#which-service-plays-a-drop)) |
| **YouTube box position** | The corner of the YouTube player box |
| **Fade songs in and out**, **Resume my Spotify music afterwards** | Fades, and your own Spotify music coming back after a drop |
| **Test Spotify**, **Test YouTube** | 15 seconds of Toto's *Africa*, using only the named service. Test Spotify never tries YouTube on failure |
| **Refresh status** | Reloads the interface, so the rows show the app's latest report |

The **Music services** dialog (`/needle music`, or the drum button in the NeedleDropper window's title
bar) is where you paste your Spotify **Client ID**: the Settings panel has no text boxes. It shows the
two addresses you need for your Spotify developer app (click a box, then press Ctrl+C) and the
buttons **Connect Spotify**, **Disconnect…**, **Test Spotify**, **Sign in to YouTube…** and **Test
YouTube**.

The game sends your choices and requests to the app through the beacon, so the app must be running
and see the game. The game hears back only when it reads the app's report: at login and after a
`/reload`. So after **Connect Spotify**, approve NeedleDropper on Spotify's page in your browser (it
may open behind the game: press Alt+Tab), wait for the Windows notification, then press **Refresh
status**.

## Game settings for the beacon

The addon tells the app what to play through the **beacon**, a thin strip of coloured squares in the
very top-left corner of the game window. To keep it readable:

- **Display mode:** in *Options → Graphics*, either choice the game offers works: **Windowed** or
  **Fullscreen (Windowed)**.
- **Picture settings:** keep brightness, contrast and gamma at their defaults, leave Windows HDR off,
  and don't run filters (ReShade and the like) over the game. They change the beacon's colours.
- **Top-left corner:** keep FPS counters and overlay notifications (Discord shows them there by
  default) out of it.

More in [Beacon not detected](TROUBLESHOOTING.md#beacon-not-detected).

## In-game audio files

To play your own .mp3 or .ogg files inside WoW, copy them into
`World of Warcraft\_forever_\Interface\AddOns\NeedleDropper_Local\media`, then **restart the game**
completely: WoW only notices new files when it starts, `/reload` is not enough. Then type the file
name into a drop's **In-game file** field. Details: [In-game audio files](PROVIDERS.md#in-game-audio-files).

## Upgrading from 1.2.0

- **Update both parts together.** A 1.2.0 app can't read the signals of the 1.2.1 addon (and a 1.2.1
  app can't read a 1.2.0 addon's): with an old app the window's footer says *Update the NeedleDropper
  app to 1.2.1 to play Spotify and YouTube songs*; with an old addon the Connect page's World of
  Warcraft card says *Addon too old*. Update the addon, quit the app (tray icon → **Quit**), replace
  `NeedleDropper.exe` and start it again ([Updating](#updating)).
- **Quit 1.2.0 first, then close its old player window.** Its small Chrome or Edge window is titled
  *NeedleDropper*. Closing the window before quitting the old app makes it reopen. Start the new
  app from the desktop. Version 1.2.1 checks for an occupied player profile before launching, so
  a leftover browser cannot silently take over the new player.
- **Sign in to Spotify again, once.** The app now plays Spotify songs itself, and Spotify asks for two
  more permissions for that. Until you sign in again, Spotify songs play on YouTube, and the Connect
  page (**Sign in again** on the Spotify card), one notification and the game's Music services rows
  ask you to. Your Client ID stays.
- **Google Chrome** is needed for **NeedleDropper (this app)**, its own Spotify player. To keep using
  the installed Spotify desktop app without Chrome, select that app under **Play on** instead.
  Both modes still need Premium and your Spotify developer app.
- **Your Spotify developer app from 1.2.0:** open it in the dashboard → **Settings** → **Edit**, tick
  **Web Playback SDK** too and **Save** (the Redirect URI and the Client ID stay). A new app: tick both
  **Web API** and **Web Playback SDK** when you create it
  ([how](PROVIDERS.md#step-1-create-your-developer-app)).
- **Your settings are kept.** Until you change one in game, the four new music options show what the
  app already does (Spotify first, fades on, your music resumes, the box in the top-right corner).
- The YouTube box now loads its player from `http://localhost:8322/player`: many videos that failed
  with error 150 in 1.2.0 play now. Nothing to do.

## Upgrading from 1.1

- **Update both parts.** The 1.2.1 addon needs the 1.2.1 app for Spotify and YouTube; with an older
  app the window says *Update the NeedleDropper app to 1.2.1 to play Spotify and YouTube songs*. The
  1.2.1 app, in turn, expects the 1.2.1 addon. Everything in
  [Upgrading from 1.2.0](#upgrading-from-120) applies too.
- **Your data is kept.** At the first login the addon keeps your settings, the places you have heard
  and your journal. A beacon cell size of 4 (the 1.1 default) becomes 3.
- **The soundtrack is now Azeroth Mixtape.** The 1.1 addon played Azeroth Unbound; to keep it, turn
  it on in the Soundtracks tab. Places you already heard stay heard.
- **Your 1.1 packs:** open a command prompt in the app's folder and run
  `NeedleDropper.exe packs convert-legacy` once. At your next login the game says, for example,
  *2 soundtracks from your NeedleDropper app are ready to import: type /needle import to review
  them.* ([more](PACKS.md#coming-from-11)).
- **Your 1.1 audio files** from the app's media folder (`%APPDATA%\NeedleDropper\media`): copy the
  .mp3 and .ogg files into `NeedleDropper_Local\media` yourself, then restart the game. The 1.2 app
  no longer plays files.
- **No more Rebuild map and `/reload`:** edits in game apply at once.
- **A Startup-folder shortcut** you made for 1.1 can go (press **Win + R**, type `shell:startup`):
  the app now starts with Windows by itself.
- `Map.lua`, `Map.json` and `Protocol.lua` in the addon folder are no longer used. If you unzipped
  1.2 over the old folder they may still be there; they are harmless, and the app's **Install addon**
  removes them.

**Where did the control panel go?** The 1.1 app had a control panel in the browser. In 1.2 the game
holds everything you change, and the app has only the Connect page and its tray menu:

| 1.1 control panel | In 1.2 |
|---|---|
| **Status** | In game: the window's footer, the add-on compartment tooltip and `/needle status`. On the PC: the tray menu's status line and the **Check the link** card |
| **Music services** | The **Spotify** and **YouTube** cards on the Connect page, and the **Music services** section of the game's settings, where *Play music with* sets the order ([how a source is picked](PROVIDERS.md#which-service-plays-a-drop)) |
| **Soundtrack** | In game: the window's **Drops** and **Soundtracks** tabs ([Soundtracks](PACKS.md)) |
| **Game & addon** | The **World of Warcraft** card on the Connect page |
| **Advanced** | The beacon and chat-log options in the game's Settings panel; **Open log folder** in the tray menu |

## Updating

- **Addon:** update it with your addon manager, or close the game and unzip the new addon zip over the
  old `NeedleDropper` folder. Your soundtracks, settings and history are kept: they live in WoW's
  saved data, not in the addon folder.
- **App:** download the new `win64` zip from the releases page (with its update check on, the app
  tells you when one is out). Quit the app (tray icon → **Quit**), replace `NeedleDropper.exe` with
  the new one and start it again. Your settings and your Spotify connection are kept: they live in
  `%APPDATA%\NeedleDropper`, not next to the exe.

## Uninstalling

1. In the app's tray menu, untick **Start with Windows**, then choose **Quit**. (If the app is
   already gone: Task Manager → *Startup apps* → NeedleDropper → **Disable**.)
2. Delete the folder that holds `NeedleDropper.exe`.
3. Delete the app's data folder: paste `%APPDATA%\NeedleDropper` into the address bar of a File
   Explorer window and delete it. It holds your app settings, the encrypted Spotify login, logs,
   caches, backup copies of the addon's saved data (`backups`), the player window's browser profile
   (`deck-browser`: Spotify's player data, a copy of Chrome's Widevine component and, if you signed
   in to YouTube there, your Google sign-in) and, from 1.1, the `packs` and `media` folders (back up
   any audio files you put there).
4. Delete the addon: `World of Warcraft\_forever_\Interface\AddOns\NeedleDropper` (or remove it in
   your addon manager) and the app's data addon `...\AddOns\NeedleDropper_Companion`.
   `NeedleDropper_Local` next to them holds *your own* audio files; delete it only if you don't need
   them.
5. *Optional:* delete the addon's saved data: your soundtracks, settings and history, in
   `World of Warcraft\_forever_\WTF\Account\<account>\SavedVariables\NeedleDropper.lua` and, per
   character, `...\WTF\Account\<account>\<realm>\<character>\SavedVariables\NeedleDropper.lua`.
   Share your soundtracks first if you want to keep them ([share codes](PACKS.md#share-codes)).
6. *Spotify:* remove NeedleDropper's access on your Spotify account page
   (<https://www.spotify.com/account/apps/>) and, if you like, delete your developer app in the
   Spotify dashboard (<https://developer.spotify.com/dashboard>).
7. *Chat-log link:* if you turned it on, type `/chatlog` in the game until it says chat logging is off,
   and delete `World of Warcraft\_forever_\Logs\WoWChatLog.txt` if you don't want to keep your chat
   history.
