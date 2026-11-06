# Privacy

**In short:** NeedleDropper runs entirely on your PC and in your game. There is no NeedleDropper
account, server, analytics, telemetry or crash reporting. The only things that ever leave your PC
are the requests needed to play music on **Spotify** and **YouTube**, sent directly to them, and,
if you switch it on, a daily check for a new version of the app. The addon itself sends nothing anywhere: no addon
messages, no chat.

## What leaves your PC

Only the NeedleDropper app (`NeedleDropper.exe`) talks to the internet. Without the app, nothing
leaves your PC at all.

| To | When | What |
|---|---|---|
| **Spotify** (`accounts.spotify.com`, `api.spotify.com`, and Spotify's player hosts such as `sdk.scdn.co`) | Only if you connect Spotify | Your sign-in (on Spotify's own consent page); a request for your account's name, plan and country (for the Premium check, and to search the songs available where you live); commands to play, pause and fade songs; the list of your devices and what is playing; and searches by artist and title for drops that have no Spotify link, made as you approach their place. The app's hidden player window also runs **Spotify's own player** (the Web Playback SDK, loaded from Spotify): like any Spotify player it signs in to Spotify, registers as a device called *NeedleDropper* and streams the songs it plays |
| **YouTube** (Google) | Only when a YouTube song is about to play (as you approach its place, the player window loads the video, paused) and while it plays; and when you use **Sign in to YouTube** | The player window loads YouTube's standard embedded player, which fetches the video from YouTube. As with any embedded video, YouTube sees your IP address and which video plays, and may set cookies, under Google's privacy policy. **Sign in to YouTube** (optional) opens Google's own sign-in page in the player window's browser profile; your password goes only to Google |
| **GitHub** | Only if the update check is on (it is off until an update address is set, `updates.releasesUrl` in `config.json`): once a day while the app runs | A request for the latest NeedleDropper release, to tell you when an update is out. It carries nothing about you, your game or your soundtracks |

Images shown on the Connect page or in the player window come from Spotify's and YouTube's servers in
the same way. In-game audio files never leave your PC.

**Spotify's permissions.** The sign-in asks for `streaming` (playing songs in the app's own player),
`user-read-playback-state` and `user-modify-playback-state` (seeing and controlling what plays, to
start, fade and stop a drop and resume your music afterwards), `user-read-private` (your plan and
country) and `user-read-email`. Spotify's player requires the last one; NeedleDropper never reads,
keeps or shows your email address. It asks for nothing about your library, playlists or followers.

NeedleDropper never sends anything about your game, characters, realm, chat or location anywhere.

## What stays on your PC

### The app's folder

The app keeps its data in `%APPDATA%\NeedleDropper` (paste that into the address bar of a File
Explorer window to open it):

| File | Contents |
|---|---|
| `config.json` | Your app settings, including your Spotify **Client ID** and the four music choices you make in game (`music`). A Client ID is not a secret: it only names your developer app |
| `spotify_token.bin` | Your Spotify login token, **encrypted with Windows DPAPI**: only your Windows user account on this PC can decrypt it. **Sign out of Spotify** on the Connect page deletes it |
| `logs\needledropper.log` | A log of what the app did (songs played, errors), up to 5 files of 1 MB. It never contains tokens or passwords; file paths in it can show your Windows user name, and once Spotify is connected it names your Spotify account (its display name, plan and country) |
| `logs\browser.log` | What the player window's browser (Chrome or Edge) itself printed, for troubleshooting |
| `cache\` | Which Spotify song was found for which artist and title, so each is looked up once |
| `backups\` | Copies of the addon's saved data (`NeedleDropper.lua`, the last 10 versions of each file), made after the game writes it, so a broken or lost file can be restored |
| `addon-install.json` | Only if you used **Install addon**: which files it copied, so an update can tidy up after itself |
| `deck-browser\` | The browser profile of the player window: YouTube's cookies, what Spotify's player stores, a copy of Chrome's Widevine component (`WidevineCdm`, which Spotify's player needs) and, if you used **Sign in to YouTube**, your Google sign-in. Sign out in that window, or delete the folder while the app isn't running, to remove them |
| `update-check.json` | When the update check last ran and what it found (only if it is on) |
| `packs\`, `media\` | Only if you used NeedleDropper 1.1: your old packs and audio files. The 1.2 app no longer uses them |

The Connect page never shows or sends your Spotify token. It is only reachable from your own PC
(`127.0.0.1`), protected by a secret that changes every time the app starts, and it refuses
requests from other websites.

The app's player page (`http://localhost:8322/player`, also served only on your own PC) needs
Spotify's **access token** to play. It gets it from one address of the app,
`/api/player/spotify-token`, which answers only a request from that page itself (same origin, the
per-run secret, the address check) and tells the browser not to store the answer. It hands out the
access token, which expires after an hour, never the long-lived refresh token.

With **Start with Windows** ticked, the app adds one entry, named `NeedleDropper`, to your Windows
account's list of programs to start at sign-in. Unticking it removes the entry.

### The game's folder

| File | Contents |
|---|---|
| `WTF\Account\<account>\SavedVariables\NeedleDropper.lua` and the per-character file of the same name | The addon's own data, like every addon's: settings, the soundtracks you made, which ones are on, the places you have heard and your journal. The game writes it when you log out or reload. The app reads it for its backups and to look songs up ahead of time; it never changes it |
| `Interface\AddOns\NeedleDropper_Companion\` | A tiny data addon the app writes: its version, whether Spotify is connected, the Spotify songs it found for your drops, and (only if you run `packs convert-legacy` after an update from 1.1) your old packs as share codes. Since 1.2.1 also your Spotify display name, plan and Client ID, the player window's browser, when you last opened YouTube's sign-in, your four music choices and the results of your last 8 requests from the game's Music services. The game reads it at login |
| `Interface\AddOns\NeedleDropper_Local\media\` | Your own audio files, if you put any there |

## The beacon strip

To tell the app when a song should start, the addon draws a **beacon**: a strip of coloured
squares in the very top-left corner of the game window. Most of the time it is a 96 × 3 pixel
strip, or 144 × 3 once you choose a music setting in game and while a drop plays. When a song starts
it grows, for a few seconds, to at most 336 × 9 pixels. It stays visible while the beacon is on, and
pressing Alt+Z doesn't hide it.

- It contains only the needle-drop event: the song (its Spotify or YouTube id, artist and title,
  start, length and volume), the name of the place and of the soundtrack, a counter, and a few flags
  (such as "in combat" or "on a flight path", used to pause drops). No character name, realm, chat or
  anything about your account.
- Once you choose a music setting in game, it also carries your four music choices (*Play music
  with*, the YouTube box's corner, fades, resume). For a few seconds after a request from Music
  services it shows which request it is. **Connect** and **Disconnect** also carry your Spotify
  **Client ID**, for 3 seconds and again for 2 seconds about 8 seconds later. A Client ID is not a
  secret, but it does show up in screenshots and streams then; with the chat-log link on, those lines
  carry it too.
- It **shows up in screenshots, recordings and streams**. Someone who decoded it could learn which
  song started at which place, nothing more. If you stream you may still prefer to crop the
  top-left corner.
- The app looks only at a small area (512 × 48 pixels) in the top-left corner of the WoW window, 4
  times per second, and only while World of Warcraft: Forever is running. The image is decoded in
  memory and immediately discarded: it is never saved or sent anywhere.

Turn the beacon off with `/needle beacon off` (then the app can't play Spotify or YouTube songs,
unless you use the chat-log link below).

## The optional chat-log link

The chat-log link is an alternative to the beacon, **off by default**. When you turn it on
(`/needle chatlog on` in the game), the addon switches on WoW's **chat logging**, and the app reads
NeedleDropper's own lines from the log file.

**Be aware:** WoW's chat logging writes **all chat your game shows** (whispers, party, guild, trade,
everything) to `World of Warcraft\_forever_\Logs\WoWChatLog.txt` on your disk, in plain text, and
the file keeps growing. NeedleDropper only reads its own (hidden) lines and never changes or uploads
the file, but the file is there for anyone with access to your PC.

To stop: `/needle chatlog off`, then type `/chatlog` in the game until it says chat logging is off,
and delete `WoWChatLog.txt` if you don't want to keep it.

## Bug reports: /needle report

`/needle report` shows a plain-text report in a window inside your game, to copy into a bug report.
It holds the date, the addon, game and app versions and your game language; what the app last
reported (Spotify's state and plan, whether a Client ID is set, YouTube and its player browser, the
last request's result code); the beacon's size, rows and measured clearance and the chat-log link's
state; your music choices and the settings that matter; your active soundtracks and what plays; your
last 5 drops (time, place id, artist and title, how it played); and the last error NeedleDropper
caught. It holds **no account names, Client IDs or tokens**. Nothing is sent anywhere: you decide
whether and where to paste it, and you can read it all before you do.

## Share codes

A share code holds a soundtrack: its name, the author name you typed (the **Author** field is empty
unless you fill it in), and for each place the song, its links, timings, tags and notes. Nothing
else: no character, realm or account. NeedleDropper never uploads a code; you copy it and decide
where to paste it. Codes you import are treated as plain data, checked field by field, and never run.

## Removing your data

See [Uninstalling](INSTALL.md#uninstalling): delete `%APPDATA%\NeedleDropper` and the addon's folders,
and remove NeedleDropper's access on your Spotify account page
(<https://www.spotify.com/account/apps/>).

NeedleDropper is open source: everything described here can be checked in its code.
