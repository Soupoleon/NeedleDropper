# Music services

NeedleDropper can play a needle drop in three ways:

| Service | Played by | You need | Best for |
|---|---|---|---|
| [In-game audio files](#in-game-audio-files) | World of Warcraft itself | Your own .mp3 or .ogg files | No app and no streaming at all |
| [Spotify Premium](#spotify-premium) | The NeedleDropper app, in your own Spotify app | Spotify Premium, the Spotify app, a free Spotify developer app of your own | The smoothest experience: fades, and your own music resumes afterwards |
| [YouTube](#youtube) | The NeedleDropper app, in a small player window | Microsoft Edge (built into Windows) or Google Chrome | No account needed; the built-in soundtracks have a YouTube video for nearly every drop |

[Apple Music](#apple-music) is not supported in this version.

The NeedleDropper app (`NeedleDropper.exe`, Windows) is optional: you need it only for Spotify and
YouTube ([installing it](INSTALL.md#3-optional-the-needledropper-app)). You set it up once on its
**Connect** page, at `http://127.0.0.1:8322/` while the app runs (tray icon → **Open setup…**).

## Which service plays a drop

You don't set an order; for every drop NeedleDropper uses the best source it has:

1. **An in-game file**, if the drop has one and the *Play in-game files* option is on. WoW plays
   it, and the app only notes that a song is playing. (If WoW can't play the file, the app takes
   over with the next source.)
2. **Spotify**, if you connected it: the drop's Spotify link, or else a search for its artist and
   title ([how](#how-the-app-finds-a-song)).
3. **YouTube**: the drop's YouTube video.

The letters next to each drop in the window's Drops tab show what it has: **S** Spotify, **Y**
YouTube, **F** in-game file. **Test** plays any drop right away, without marking it as heard.

---

## Spotify Premium

NeedleDropper starts the song in **your own Spotify app** (on this PC, or any Spotify Connect device
of yours), fades it in and out, and afterwards picks up whatever you were listening to before.

### What you need

- **Spotify Premium.** Spotify only lets apps control playback for Premium accounts.
- **The Spotify app** on this PC (minimized is fine), or another device signed in to your account.
- **A free Spotify developer app of your own** (two minutes, see below). NeedleDropper needs its
  **Client ID** to ask Spotify for permission. No client secret, no password.
- **The NeedleDropper app**, which does the talking to Spotify.

### Why every player creates their own developer app

Spotify only lets an app control playback after a developer registers it. A new app runs in
Spotify's *Development Mode*, which allows **at most 5 users per app**, requires the app's owner to
have Premium, and returns at most 10 results per search. Wider access is granted only to large,
established companies, not to free fan projects. A single shared NeedleDropper app would therefore
stop working after the fifth player.

So NeedleDropper ships without any Spotify key. You register a private app for yourself, you are its
only user, and the Spotify connection belongs to you alone. NeedleDropper runs no server: it talks
to Spotify directly from your PC.

### Step 1: Create your developer app

1. Open **<https://developer.spotify.com/dashboard>** and log in with the Spotify account you listen
   with. The first time, accept the Spotify Developer Terms (and confirm your email address if
   Spotify asks).
2. Click **Create app** and fill in the form:
   - **App name:** anything, for example `NeedleDropper for me`.
   - **App description:** anything, for example `Plays NeedleDropper songs in my Spotify app`.
   - **Website:** leave it empty.
   - **Redirect URIs:** type exactly **`http://127.0.0.1:8321/callback`** and click **Add**.
     (The Spotify card on the Connect page shows this address with a **Copy** button.) It must be
     `127.0.0.1`, not `localhost`; `http`, not `https`; and no `/` at the end.
   - **Which API/SDKs are you planning to use?** Tick **Web API**.
3. Tick the box to agree to Spotify's terms and click **Save**.
4. On your app's page (open **Settings** if you don't see it), copy the **Client ID**: a 32-character
   code of letters and digits. You don't need the *Client secret*; never share it.

### Step 2: Sign in on the Connect page

1. Open the Connect page (it opens by itself the first time you start the app; later, left-click the
   tray icon). On the **Spotify** card, paste the **Client ID**.
2. Click **Sign in**. Spotify's consent page opens in your browser. It shows the app name you chose
   and asks to let it *see what is playing on your devices*, *control playback on your devices* and
   see your account's subscription and country (so the app can check Premium and search the songs
   available where you live). Click **Agree**.
3. The browser tab says you can close it. The Spotify card now shows your account, its **Premium**
   check (for example *✓ Yes: this account has Spotify Premium.*), its **Country** (when Spotify
   reports the plan and the country), and **Play on**, the device that plays.

Signed in with NeedleDropper 1.1? The card then shows **Sign in again**: 1.1 didn't ask for the
plan and country, so the app can't check Premium or search your country's catalogue (it searches
without it meanwhile). Click it and approve once more.

The game learns that Spotify is connected at your next login: the NeedleDropper window's footer then
says, for example, *App seen 2 min before login · Spotify connected*.

NeedleDropper keeps the login in `%APPDATA%\NeedleDropper\spotify_token.bin`, encrypted with Windows
DPAPI so that only your Windows account on this PC can read it ([privacy](PRIVACY.md)).
Disconnect with **Sign out of Spotify** on the Spotify card: it deletes the login.

### Signing in with a different Spotify account (User Management)

The account that **created** the developer app normally works with it right away. If even that
account gets error 403, add it to the list below as well. To use a **different** Spotify account with
your app (a family member on the same PC, or because you created the app with a separate developer
account), add it first:

1. In the dashboard, open your app → **Settings** → **User Management** (under *Users and Access*).
2. Enter the person's name and the **email address of their Spotify account**, and save.

Up to 5 accounts per app. Spotify lets an account that isn't on this list sign in, but then refuses
every request with error 403, which the Spotify card shows as **Account not allowed**. The app's
owner must have Premium as well, or the app stops working (a Spotify rule for Development Mode apps
since February 2026).

### Devices: Play on

NeedleDropper plays the song on one of your **Spotify Connect** devices:

1. the device chosen under **Play on** on the Spotify card (*Automatic* prefers this PC),
2. otherwise the Spotify app on this PC,
3. otherwise the device that is currently active, or the first available one.

If no device is online, NeedleDropper starts the Spotify app on this PC and waits up to 10 seconds
for it. Keeping the Spotify app open (minimized) makes drops start instantly. Phones, speakers and
TVs work when they are online, but some don't allow remote volume control; on those the song simply
starts and stops without fading.

### How the app finds a song

- **With a Spotify link** in the drop, the app plays exactly that song.
- **Without one** (the built-in soundtracks name their songs by artist and title), the app searches
  Spotify a few seconds before the drop plays: the game tells it which drop is coming up as you
  arrive. It picks the result whose title, artist and length match best, and remembers it, so each
  song is looked up only once. This replaces 1.1's **Resolve soundtrack** button.
- **If nothing matches,** a notification says *song not found on Spotify*, and the drop plays on
  YouTube if it has a video.
- **After your next login,** the drop's form in the window shows what the app found, for example
  *Spotify match: Africa · Toto (4:55)* with a **Use this exact song** button. One click saves that
  exact recording in the drop (for a built-in drop, in My Drops).
- **If the wrong version plays,** paste the right song's link into the drop's **Spotify** field: in
  the Spotify app, right-click the song → **Share** → **Copy Song Link**.

### During a drop

- The song fades in, and fades out when the drop's time is up, when you get into a long fight
  (the *Stop after combat lasting* option), or when you type `/needle stop`.
- If you were listening to something when the drop started, NeedleDropper resumes it afterwards
  (same playlist or album, track and position). Otherwise it just pauses.
- If you skip or change the song yourself during a drop, NeedleDropper lets go: it won't pause or
  change your music when the drop's time is up.

### Spotify problems

| You see | What it means | What to do |
|---|---|---|
| Spotify's page says **INVALID_CLIENT: Invalid redirect URI** | The redirect URI in your app doesn't match exactly | In the dashboard, open your app → **Settings** → **Edit**, and make the redirect URI exactly `http://127.0.0.1:8321/callback` |
| Spotify's page says **INVALID_CLIENT: Invalid client** | The Client ID is wrong or the app was deleted | Copy the Client ID again from the dashboard and paste it on the Spotify card |
| **Premium needed**, or error **403** | The account isn't Premium (the card's **Premium** line names its plan), or the app's owner lost Premium | Use a Premium account |
| **Account not allowed** | The account isn't on your app's User Management list | Add it under [User Management](#signing-in-with-a-different-spotify-account-user-management) |
| **Sign in again** | The sign-in comes from NeedleDropper 1.1 and lacks the plan-and-country permission | Click **Sign in again** and approve |
| Notification: *Spotify isn't starting songs — restart the Spotify app* | Spotify accepted the song but didn't start it (the app's buttons often look greyed out). On the test PC, Spotify's Windows app did this with every song, in both its Microsoft Store and its spotify.com version, while Spotify's **Web Player** played them. That drop played on YouTube, if it has a video, and for the rest of the session YouTube plays first without asking Spotify again. As you approach the next places the app checks Spotify quietly and uses it again once it plays | Quit the Spotify app completely, start it again, and play and pause any song once. If it keeps happening, open Spotify's Web Player (`open.spotify.com`) in a browser, play and pause a song there, and pick it under **Play on** |
| Error **404** / "no active device" | Spotify has no device that is online right now | Open the Spotify app on this PC (play and pause any song once), then pick it under **Play on** |
| Error **429** / "rate limited" | Too many requests in a short time; Spotify may also report `QUOTA_EXCEEDED` | Wait a few minutes and try again; NeedleDropper already waits when Spotify asks it to |
| **Sign in** never finishes | The consent page wasn't completed in time (the sign-in link expires after a few minutes), or another program uses port 8321 | Click **Sign in** again and finish the consent page; close other apps that might use port 8321 |
| Notification: Spotify sign-in expired | Spotify ended the login | Click the notification and **Sign in** again |
| The song starts on the wrong device | Another device was active | Choose the device under **Play on** |

More in [Troubleshooting](TROUBLESHOOTING.md#spotify).

---

## YouTube

No account needed. YouTube songs play in a **small player window**: a Microsoft Edge (or Google
Chrome) app window of about 420 × 380 pixels with the video, the place name and the song. It is the
only window NeedleDropper shows while you play (the in-game title card is the only other "popup"). The
**YouTube** card on the Connect page needs no setup.

- The app makes the window **once, hidden, when it starts** (normally when Windows starts), because a
  browser window takes the keyboard focus at the moment it is created.
- While a YouTube song plays, the window shows **on top of the game in the top-right corner** of the
  screen the game is on, and it **never takes the focus**: your keys and mouse stay in the game.
  When the song ends it hides again. Another corner: set `deck.playerCorner` in `config.json` to
  `topleft`, `bottomright` or `bottomleft` (or `deck.playerPosition` to `"x,y"`).
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
minimize it.

### Good to know

- **First click:** the player window's own browser profile allows sound without a click. If the
  player ever shows *Click to enable sound*, click once anywhere in it (the game keeps the focus).
- **Ads:** YouTube may show ads before or during a song, as with any embedded video, unless you have
  YouTube Premium (and are signed in to YouTube in the browser that shows the player). NeedleDropper
  never blocks or skips ads; that would break YouTube's rules.
- **Which songs:** only drops with a YouTube link. YouTube can't be searched without a developer key,
  so a drop without a link never plays there. The built-in soundtracks list official uploads (artist,
  label or YouTube "Topic" channels) for every drop except Azeroth Unbound's Azshara.
- **When a video stops working** (removed, or its owner turns off embedding), the app shows a
  notification, and after your next login the drop shows a warning in the window. Paste another
  official upload into the drop (for a built-in drop, **Change song here** first). With Spotify
  connected, the song plays there instead.

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
- In-game files play on the game's Music channel, at your Music volume.
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
