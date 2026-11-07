# Music services

NeedleDropper can play a needle drop in three ways:

| Service | Played by | You need | Best for |
|---|---|---|---|
| [In-game audio files](#in-game-audio-files) | World of Warcraft itself | Your own .mp3 or .ogg files | No app and no streaming at all |
| [Spotify Premium](#spotify-premium) | The NeedleDropper app, in its own hidden player (Spotify lists it as the device **NeedleDropper**) | Spotify Premium, Google Chrome, a free Spotify developer app of your own | The real recordings, with fades, and your own music resumes afterwards |
| [YouTube](#youtube) | The NeedleDropper app, in a small player box | Microsoft Edge (built into Windows) or Google Chrome | No account needed; every drop of the built-in soundtracks has a YouTube video |

[Apple Music](#apple-music) is not supported in this version.

The NeedleDropper app (`NeedleDropper.exe`, Windows) is optional: you need it only for Spotify and
YouTube ([installing it](INSTALL.md#3-optional-the-needledropper-app)). You set it up once, either on
its **Connect** page, at `http://127.0.0.1:8322/` while the app runs (tray icon → **Open setup…**),
or in the game: *Game Menu → Options → AddOns → NeedleDropper*, section **Music services**, and the
**Music services** dialog (`/needle music`) ([how](INSTALL.md#music-services-in-game)).

## Which service plays a drop

1. **An in-game file** comes first, if the drop has one and the *Play in-game files* option is on.
   WoW plays it, and the app only notes that a song is playing. (If WoW can't play the file, the app
   takes over with the next source.)
2. Otherwise the app tries the services in the order you choose in game, under **Play music with**
   (*Options → AddOns → NeedleDropper → Music services*, or `/needle set musicOrder …`):

| Play music with | What the app plays |
|---|---|
| *Spotify, then YouTube* (the default) | Spotify: the drop's Spotify link, or else a search for its artist and title ([how](#how-the-app-finds-a-song)). If Spotify can't play it, the drop's YouTube video |
| *YouTube, then Spotify* | The drop's YouTube video; Spotify if it has none or YouTube can't play it |
| *Spotify only* | Spotify only. A drop Spotify can't play shows its title card only |
| *YouTube only* | YouTube only. Drops without a YouTube link show just their title card |
| *Neither* | Nothing through the app: title cards and in-game files only, and the zone music keeps playing |

A service also has to be on in the app: Spotify signed in on the Connect page, and YouTube's *Play
songs from YouTube* switch on. Until you choose in game, the app plays *Spotify, then YouTube*.

The letters next to each drop in the window's Drops tab show what it has: **S** Spotify, **Y**
YouTube, **F** in-game file. **Test** plays any drop right away, without marking it as heard.
**Test Spotify** and **Test YouTube** (in the game's Music services section, in the dialog and on the
Connect page) play 15 seconds of Toto's *Africa*, whatever *Play music with* says, or a Windows
notification tells you why they can't. Each test uses only the named service: **Test Spotify does
not try YouTube on failure**. **Send a test signal** and `/needle test` check only the link and
play nothing; the drop editor's **Test** and Journal's **Replay** use your normal music choices.

---

## Spotify Premium

NeedleDropper plays the song **itself**, in a hidden player inside the app that uses Spotify's
official **Web Playback SDK**. While the app runs, Spotify lists that player among your devices as
**NeedleDropper**. No Spotify window opens, the Spotify app doesn't have to be running, and the game
keeps the focus. The song fades in and out, and afterwards the music you were listening to (on your
phone, in the Spotify app, anywhere) can pick up where it was.

### What you need

- **Spotify Premium.** Spotify's player for apps works only for Premium accounts.
- **Google Chrome.** The player runs in a Chrome window that the app keeps hidden, because Spotify's
  player needs Chrome's **Widevine** component (it decrypts Spotify's audio). The app copies Widevine
  from your Chrome into the player's own profile; Microsoft Edge can't run the player. Installed
  Chrome just now? Start it once and give it a minute to download Widevine.
- **A free Spotify developer app of your own** (two minutes, see below). NeedleDropper needs its
  **Client ID** to ask Spotify for permission. No client secret, no password.
- **The NeedleDropper app**, which does the talking to Spotify.

### Why every player creates their own developer app

Spotify only lets an app play music or control playback after a developer registers it. A new app
runs in Spotify's *Development Mode*, which allows **at most 5 users per app**, requires the app's
owner to have Premium, and returns at most 10 results per search. New development access is limited
to one Client ID per developer. Use an app you already own if that limit prevents you from creating
another. A shared app cannot authorize an unlimited number of testers. See Spotify's
[developer access update](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)
and [development-mode limits](https://developer.spotify.com/documentation/web-api/concepts/quota-modes).

So NeedleDropper ships without any Spotify key. You register a private app for yourself, you are its
only user, and the Spotify connection belongs to you alone. NeedleDropper runs no server: it talks
to Spotify directly from your PC.

### Step 1: Create your developer app

1. Open **<https://developer.spotify.com/dashboard>** and log in with the Spotify account you listen
   with; it must have Premium. The first time, accept the Spotify Developer Terms (and confirm your email address if
   Spotify asks).
2. Click **Create app** and fill in the form:
   - **App name:** anything, for example `NeedleDropper for me`.
   - **App description:** anything, for example `Plays NeedleDropper songs`.
   - **Website:** leave it empty.
   - **Redirect URIs:** type exactly **`http://127.0.0.1:8321/callback`** and click **Add**.
     (The Spotify card on the Connect page and the game's Music services dialog show this address,
     ready to copy.) It must be `127.0.0.1`, not `localhost`; `http`, not `https`; and no `/` at the
     end. Spotify requires an explicit loopback address for this local callback
     ([redirect URI rules](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri)).
   - **Which API/SDKs are you planning to use?** Tick **Web API** and **Web Playback SDK**.
3. Tick the box to agree to Spotify's terms and click **Save**.
4. On your app's page (open **Settings** if you don't see it), copy the **Client ID**: a 32-character
   code of digits and the letters a to f. You don't need the *Client secret*; never share it.

If **Create app** is unavailable, check your Premium subscription and whether you already have a
developer app. You can use YouTube independently while resolving Spotify access.

Made your app for an older NeedleDropper, with only **Web API** ticked? Open it → **Settings** →
**Edit**, tick **Web Playback SDK** as well and **Save**.

### Step 2: Sign in

Either on the Connect page or in the game:

- **Connect page:** open it (it opens by itself the first time you start the app; later, left-click
  the tray icon). On the **Spotify** card, paste the **Client ID** and click **Sign in**.
- **In game:** type `/needle music` (or click **Spotify account** in *Options → AddOns →
  NeedleDropper*). Click the step 3 box, paste the **Client ID** with Ctrl+V (*That looks like a
  Client ID.*) and click **Connect Spotify**. The app opens Spotify's page in your browser; it may
  open behind the game, so press Alt+Tab (or click the app's notification).

Spotify's consent page shows the app name you chose and asks for these permissions:

| Permission | Why NeedleDropper asks |
|---|---|
| Play music in NeedleDropper's player (`streaming`) | The app's own Spotify player plays the songs |
| See and control what plays on your devices (`user-read-playback-state`, `user-modify-playback-state`) | To start a song in that player, fade it, stop it, and resume your own music afterwards |
| Your email address (`user-read-email`) | Spotify's player requires it. NeedleDropper never reads, keeps or shows the address |
| Your account's subscription and country (`user-read-private`) | The Premium check, and searching the songs available where you live |

Click **Agree**. The browser tab says you can close it. A sign-in you started on the Connect page shows
there: the Spotify card now shows your account and **Play on**, the device that plays. The
**Premium** and **Country** fields show what Spotify returns; newer development apps may not receive
these profile fields ([Spotify's API changes](https://developer.spotify.com/documentation/web-api/references/changes/february-2026)).
Use **Test Spotify** to check playback. A sign-in you started in
game is confirmed by a Windows notification (*Spotify connected as …*), and the Connect page's card shows
it too. In game, press **Refresh status** (it reloads the interface): the Music services rows then say
*Spotify: connected as … (Premium)*, and the window's footer, for example, *App seen 2 min before login ·
Spotify: Alex (Premium)*.

**Signed in with NeedleDropper 1.2.0 or 1.1?** Sign in once more: in-app playback needs the
`streaming` and `user-read-email` permissions, which older versions didn't ask for. Until then the
Connect page shows **Sign in again** (with *Needs attention*), the game says *Spotify: sign in again*,
one notification says *Sign in to Spotify again*, and Spotify songs play on YouTube. Your Client ID
stays: click **Sign in again** on the Spotify card (in game, the **Spotify account** row's **Sign in
again**) and approve.

NeedleDropper keeps the login in `%APPDATA%\NeedleDropper\spotify_token.bin`, encrypted with Windows
DPAPI so that only your Windows account on this PC can read it ([privacy](PRIVACY.md)).
**Sign out of Spotify** on the Spotify card, or **Disconnect…** in the game's Music services dialog,
deletes the login. Your Client ID stays, so connecting again needs no retyping.

### Signing in with a different Spotify account (User Management)

The account that **created** the developer app normally works with it right away. If even that
account gets error 403, add it to the list below as well. To use a **different** Spotify account with
your app (a family member on the same PC, a friend testing NeedleDropper, or because you created the
app with a separate developer account), add it first:

1. In the dashboard, open your app → **Settings** → **User Management** (under *Users and Access*).
2. Enter the person's name and the **email address of their Spotify account**, and save.

Up to 5 accounts per app. Spotify lets an account that isn't on this list sign in, but then refuses
every request with error 403, which the Spotify card shows as **Account not allowed**. The app's
owner must have Premium as well, or the app stops working (a Spotify rule for Development Mode apps
since February 2026).

### Devices: Play on

**Play on**, on the Connect page's Spotify card, chooses the Spotify player that plays the songs.
For playback inside NeedleDropper on your gaming PC, choose **NeedleDropper (this app)**.
To use the separate Spotify desktop app, open Spotify on that PC, click **Refresh** beside
**Play on**, then select **Spotify desktop app — your PC's name**. Switch back by choosing
**NeedleDropper (this app)** again.

**Use [1.2.1 beta 2](https://github.com/Soupoleon/NeedleDropper/releases/tag/v1.2.1-beta.2) or newer
for these device-selection safeguards.** Beta.1 lists desktop devices without the new label.

- **NeedleDropper (this app)**, the recommended default: the app's own hidden player on your gaming PC. While the app runs, the
  Spotify app on your phone or PC lists it among your devices as **NeedleDropper**. You don't have to
  do anything with it; if you pick it there yourself, your own music plays through the app.
- **Automatic Spotify Connect device** lets NeedleDropper choose an available Spotify device.
  This can be another PC or a phone signed into your account. Published beta.1 calls this
  **The Spotify app on this PC (automatic)**, but it is not limited to this PC. Automatic selection
  skips the in-app **NeedleDropper** player.
- **A specific Spotify Connect device** keeps playback on that selected device. Devices with the
  same name have extra identifying details in the list. If the selected device is offline or Spotify
  will not let apps control it, Spotify playback fails with instructions; NeedleDropper does not
  switch to another Spotify device. Open Spotify on the intended device, click **Refresh** beside
  **Play on**, and select it again. You can also choose **NeedleDropper (this app)** to play locally.

Spotify [device IDs can change](https://developer.spotify.com/documentation/web-api/reference/get-a-users-available-devices).
If a saved choice is no longer found, use **Refresh** and select its current entry again.
Older settings that saved only a name work only when exactly one available
device has that exact name; otherwise, select it again. Phones, speakers and TVs that do not allow
remote volume control start and stop without fading. Your normal service order still decides
whether a failed Spotify drop may try YouTube; **Test Spotify** uses Spotify alone.

In `config.json` the choice is `spotify.device` (`app` or `connect`). A specific device is saved as
`spotify.deviceId`, with `spotify.deviceName` for its label; use the selector to change it.

### Speakers or headphones on your PC

Choose **NeedleDropper (this app)** to keep Spotify on your gaming PC. To choose which speakers or
headphones it uses, open **Windows Settings → System → Sound → Volume mixer**. Under **Apps**, set
the **Output device** for **Google Chrome** (NeedleDropper's in-app Spotify player), or **Spotify**
if you chose the separate desktop Spotify app. Check that the same app is unmuted and its volume
is turned up. **Play on** chooses a Spotify player; Windows controls that player's audio output.
See [Microsoft's app-audio instructions](https://support.microsoft.com/en-au/windows/hardware/audio/fix-app-audio-not-working-while-system-sounds-work-in-windows).

### One account, one device at a time

A Spotify account plays on one device at a time. When a drop starts, Spotify moves your account's
playback to NeedleDropper's player, so whatever you were listening to stops there (on your phone, in
the Spotify app) for the length of the drop. Approaching a place only looks the song up; nothing is
taken over until the drop really starts. With **Resume my Spotify music afterwards** (on by default,
in game), your music comes back afterwards on the device it played on, at the same track and position.
Turn it off and NeedleDropper simply pauses at the end of the drop. A remote device can accept a
resume command without starting; if your own music does not come back, resume it in Spotify and
include the device and the app's log in your report.

### How the app finds a song

- **With a Spotify link** in the drop, the app plays exactly that song.
- **Without one** (a drop you added with just its artist and title; every built-in drop has its
  exact Spotify song), the app searches Spotify a few seconds before the drop plays: the game tells
  it which drop is coming up as you arrive. It picks the result whose title, artist and length match best, and remembers it, so each
  song is looked up only once. This replaces 1.1's **Resolve soundtrack** button.
- **If nothing matches,** a notification says *song not found on Spotify*, and the drop plays on
  YouTube if it has a video (and *Play music with* allows YouTube).
- **After your next login,** the drop's form in the window shows what the app found, for example
  *Spotify match: Africa · Toto (4:55)* with a **Use this exact song** button. One click saves that
  exact recording in the drop (for a built-in drop, in My Drops).
- **If the wrong version plays,** paste the right song's link into the drop's **Spotify** field: in
  the Spotify app, right-click the song → **Share** → **Copy Song Link**.

### During a drop

- The song fades in, and fades out when the drop's time is up, when you get into a long fight
  (the *Stop after combat lasting* option), or when you type `/needle stop`. With **Fade songs in and
  out** off (in game), songs start and stop at once.
- If you pause, skip or change the song yourself during a drop, NeedleDropper lets go: it won't pause
  or change your music when the drop's time is up.
- If the app's player doesn't start a song within about 3 seconds, the drop plays on YouTube instead
  (if it has a video), a notification says *Spotify didn't start*, and YouTube plays first for the rest
  of the session until a verified player recovers. The app reloads its player page at most once
  a minute and checks that Spotify lists the new device before using it. A page reload waits
  while a song is playing there.

### Spotify problems

| You see | What it means | What to do |
|---|---|---|
| Spotify's page says **INVALID_CLIENT: Invalid redirect URI** | The redirect URI in your app doesn't match exactly | In the dashboard, open your app → **Settings** → **Edit**, and make the redirect URI exactly `http://127.0.0.1:8321/callback` |
| Spotify's page says **INVALID_CLIENT: Invalid client** | The Client ID is wrong (often the Client secret was copied instead) or the app was deleted | Copy the Client ID again from the dashboard and paste it on the Spotify card or in the Music services dialog |
| **Sign in again** on the Spotify card; *Spotify: sign in again* in game; the notification *Sign in to Spotify again* | The sign-in comes from NeedleDropper 1.2.0 or older and lacks the permissions in-app playback needs. Meanwhile Spotify songs play on YouTube | Click **Sign in again** and approve |
| *Spotify can't play in NeedleDropper's player: install Google Chrome, or wait a minute for Chrome's Widevine component* (in game: *Spotify needs Google Chrome*) | The app's player needs Google Chrome. On a fresh Chrome, its Widevine component may still be downloading | Install Google Chrome. If it is installed, start it once, wait a minute, then quit and restart the NeedleDropper app |
| *NeedleDropper's Spotify player didn't get ready (it tries again by itself)…* (in game: *Spotify player not ready*) | Chrome and its Widevine component are there, but Spotify's player never connected (often the internet connection, or Widevine still downloading on a fresh Chrome) | Check the internet connection and wait a minute: the app connects its player again by itself. If it stays, quit the app (tray → **Quit**) and start it again |
| *NeedleDropper's player isn't running yet: it starts by itself when World of Warcraft isn't the window in front* (in game: *Spotify player not started yet*) | The app makes its player window only while the game is not in front, because a new window takes the focus for a moment | Start the app before the game, or switch away from the game for a moment |
| *Spotify's player could not load (offline, or blocked by a firewall)* (in game: *Spotify player didn't load*) | Chrome couldn't load Spotify's player from `sdk.scdn.co` | Check the internet connection; allow Chrome through your firewall or filter |
| Notification *Spotify didn't start*; in game *Spotify: a song didn't start in NeedleDropper's player* | Spotify accepted the song, but the app's player did not start it. Needle drops can use YouTube until recovery, if they have a video and your settings allow it. Test Spotify never tries YouTube | Quit NeedleDropper (tray → **Quit**), restart it from the desktop and try **Test Spotify** again |
| **Premium needed**, or a Premium-related error **403** | Spotify refused playback because of the account subscription; profile fields alone may not report the plan | Check that the signed-in account and developer-app owner have Premium |
| **Account not allowed** | The account isn't on your app's User Management list | Add it under [User Management](#signing-in-with-a-different-spotify-account-user-management) |
| Notification asking you to restart the Spotify app | Only when **Play on** is another device: Spotify accepted the song but did not start it | Pick **NeedleDropper (this app)** under **Play on**. To keep using the separate Spotify app, restart that app and play and pause a song once |
| Error **404**, **Device not found**, or the player **went offline** | In app mode, the device reported by the player is not reachable. NeedleDropper reloads the page, checks the replacement against Spotify's device list and retries once. In Connect mode, the chosen device may be offline or its ID may have changed | In app mode, if recovery fails, quit NeedleDropper from its tray and restart it from the desktop, then Test Spotify. In Connect mode, open Spotify on the chosen device, play and pause a song, click **Refresh** beside **Play on**, then select it again |
| Error **429** / "rate limited" | Too many requests in a short time; Spotify may also report `QUOTA_EXCEEDED` | Wait a few minutes and try again; NeedleDropper already waits when Spotify asks it to |
| **Sign in** never finishes | The consent page wasn't completed in time (the sign-in link expires after a few minutes), or another program uses port 8321 | Click **Sign in** again and finish the consent page; close other apps that might use port 8321 |
| Notification: Spotify sign-in expired | Spotify ended the login | Click the notification and **Sign in** again |
| The song starts on the wrong device | Automatic Spotify Connect selection can choose another PC or phone; beta.1 lacks the safeguards for a specific selection | Update to beta.2 or newer, then choose **NeedleDropper (this app)** for your gaming PC, or **Refresh** and select the intended Spotify device |
| The song plays through the wrong speakers or headphones | Windows routes the playing app's audio to that output | Set Chrome's or Spotify's output in [Windows Volume mixer](#speakers-or-headphones-on-your-pc) |

More in [Troubleshooting](TROUBLESHOOTING.md#spotify).

---

## YouTube

No account needed. YouTube songs play in a **small player box**: a browser window of about
420 × 380 pixels with the video, the place name and the song. It is the only window NeedleDropper
shows while you play (the in-game title card is the only other "popup"). The **YouTube** card on the
Connect page needs no setup.

- The app makes the window **once, hidden, when it starts** (normally when Windows starts), because a
  browser window takes the keyboard focus at the moment it is created. The same window holds the
  Spotify player, so it runs in **Google Chrome** whenever Spotify plays in the app; otherwise in
  Microsoft Edge or Chrome.
- While a YouTube song plays, the box shows **on top of the game**, in the corner you choose in game
  (**YouTube box position**: top right by default, also top left, bottom right or bottom left), on the
  screen the game is on. It **never takes the focus**: your keys and mouse stay in the game. When the
  song ends it hides again. (Advanced: `deck.playerPosition` in `config.json` as `"x,y"` places it
  exactly, until you change the corner in game.) At the top left the box sits below the beacon, so
  the app keeps reading the game's signals while it shows; changing the corner while it shows moves it
  at once.
- The app serves the box's page itself, at `http://localhost:8322/player`. YouTube refuses many
  videos to a page on `127.0.0.1` (error 150), but not on `localhost`.
- When a drop is about to fire (as you approach the place) the app already loads the video, paused
  and silent, so the song starts quickly.
- NeedleDropper never opens a tab in your default browser for a song: that would take you out of the
  game. Without Edge or Chrome, YouTube songs show their title card only.
- If the window is closed, the app makes it again a few minutes later, but **never while the game is
  in front**; until then YouTube songs show their title card only.

### The player window must stay visible

YouTube's rules don't allow hidden or background players or audio-only playback, and an embedded
player must be at least 200 × 200 pixels. While a song plays the window therefore stays visible on
top of the game, in its corner. Run the game in *Fullscreen (Windowed)* or *Windowed* mode (the two
display modes Forever offers), don't cover the window with other always-on-top windows, and don't
minimize it. (Spotify songs play in the same window while it stays hidden: the window shows only
for YouTube songs, whose rules require it.)

### Good to know

- **First click:** the player window's own browser profile allows sound without a click. If the
  player ever shows *Click to enable sound*, click once anywhere in it (the game keeps the focus).
- **Ads:** YouTube may show ads before or during a song, as with any embedded video. NeedleDropper
  never blocks or skips ads; that would break YouTube's rules.
- **Sign in to YouTube** (optional; in the game's Music services, or on the Connect page's YouTube
  card) opens YouTube's sign-in page in a normal window of the player box's own browser, not your
  everyday browser, so the box uses that account. With **YouTube Premium**, YouTube songs should then
  play without ads; this isn't verified yet. Close the window when you're done.
- **Which songs:** only drops with a YouTube link. YouTube can't be searched without a developer key,
  so a drop without a link never plays there. The built-in soundtracks list official uploads (artist,
  label or YouTube "Topic" channels) for every drop, each checked to play in the box.
- **When a video stops working** (removed, or its owner turns off embedding), the app shows a
  notification, and after your next login the drop shows a warning in the window. Paste another
  official upload into the drop (for a built-in drop, **Change song here** first). With Spotify
  connected, the song plays there instead.
- **Test YouTube** plays 15 seconds of Toto's *Africa* in the box, in your corner.

YouTube error codes (100, 101, 150, 153) are explained in
[Troubleshooting](TROUBLESHOOTING.md#youtube).

---

## In-game audio files

Play your own audio files, songs you own, ripped or made, inside World of Warcraft: no app, no
window and no streaming.

1. Copy the file (**.mp3** or **.ogg**) into
   **`World of Warcraft\_forever_\Interface\AddOns\NeedleDropper_Local\media\`**.
   Create the `NeedleDropper_Local` and `media` folders if they don't exist yet. Addon managers never
   touch this folder (it isn't part of the addon), and NeedleDropper never changes or deletes
   anything in it.
2. **Restart the game completely.** WoW only notices new files when it starts; `/reload` is not
   enough.
3. In the NeedleDropper window, select the drop (or stand there and press **Add drop here**) and type
   the file name into **In-game file**, for example `goldshire.mp3`. The form says *found* (with a
   tick); **Test** plays it.

- Use plain file names (letters, digits, spaces and `._-()`), directly in the `media` folder, without
  subfolders.
- In-game files play on the game's Music channel, at your Music volume, whatever *Play music with*
  says.
- The *Play in-game files* option (`/needle set localPlayback off`) turns them off; drops then play
  through the app, if they also have a Spotify or YouTube song.
- In a pack file the same drop is written `"file": "goldshire.mp3"` with `"inGameFile": true`
  ([pack format](PACKS.md#drops)).

**Coming from 1.1:** the app no longer plays files itself, so its media folder,
`%APPDATA%\NeedleDropper\media`, isn't used any more. Copy the .mp3 and .ogg files from it into
`NeedleDropper_Local\media` yourself, then restart the game. Convert other formats (.flac, .wav,
.m4a) to .mp3 or .ogg first.

---

## Apple Music

**Apple Music is not supported in v1** (Windows offers no way to control it): the Apple Music app for
Windows can't be told to play a particular song, so NeedleDropper can't start one at the right moment.
Soundtrack files may still contain an `appleMusic` link; it is ignored.
