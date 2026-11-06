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
| **Spotify** (`accounts.spotify.com`, `api.spotify.com`) | Only if you connect Spotify | Your sign-in (on Spotify's own consent page); commands to play, pause and fade songs on your devices; the list of your devices and what is playing; and searches by artist and title for the songs in your soundtracks that have no Spotify link, made as you approach their place |
| **YouTube** (Google) | Only when a YouTube song plays | The player window loads YouTube's standard embedded player, which fetches the video from YouTube. As with any embedded video, YouTube sees your IP address and which video plays, and may set cookies, under Google's privacy policy |
| **GitHub** | Only if the update check is on (it is off until an update address is set, `updates.releasesUrl` in `config.json`): once a day while the app runs | A request for the latest NeedleDropper release, to tell you when an update is out. It carries nothing about you, your game or your soundtracks |

Images shown on the Connect page or in the player window come from Spotify's and YouTube's servers in
the same way. In-game audio files never leave your PC.

NeedleDropper never sends anything about your game, characters, realm, chat or location anywhere.

## What stays on your PC

### The app's folder

The app keeps its data in `%APPDATA%\NeedleDropper` (paste that into the address bar of a File
Explorer window to open it):

| File | Contents |
|---|---|
| `config.json` | Your app settings, including your Spotify **Client ID**. A Client ID is not a secret: it only names your developer app |
| `spotify_token.bin` | Your Spotify login token, **encrypted with Windows DPAPI**: only your Windows user account on this PC can decrypt it. **Disconnect** on the Connect page deletes it |
| `logs\needledropper.log` | A log of what the app did (songs played, errors), up to 5 files of 1 MB. It never contains tokens or passwords; file paths in it can show your Windows user name |
| `cache\` | Which Spotify song was found for which artist and title, so each is looked up once |
| Backups | Copies of the addon's saved data (the last 10 versions), made after the game writes it, so a broken or lost file can be restored |
| `deck-browser\` | The browser profile used by the YouTube player window (YouTube's cookies live here) |
| `update-check.json` | When the update check last ran and what it found (only if it is on) |
| `packs\`, `media\` | Only if you used NeedleDropper 1.1: your old packs and audio files. The 1.2 app no longer uses them |

The Connect page never shows or sends your Spotify token. It is only reachable from your own PC
(`127.0.0.1`), protected by a secret that changes every time the app starts, and it refuses
requests from other websites.

With **Start with Windows** ticked, the app adds one entry, named `NeedleDropper`, to your Windows
account's list of programs to start at sign-in. Unticking it removes the entry.

### The game's folder

| File | Contents |
|---|---|
| `WTF\Account\<account>\SavedVariables\NeedleDropper.lua` and the per-character file of the same name | The addon's own data, like every addon's: settings, the soundtracks you made, which ones are on, the places you have heard and your journal. The game writes it when you log out or reload. The app reads it for its backups and to look songs up ahead of time; it never changes it |
| `Interface\AddOns\NeedleDropper_Companion\` | A tiny data addon the app writes: its version, whether Spotify is connected, the Spotify songs it found for your drops, and (once, after an update from 1.1) your old packs as share codes. The game reads it at login |
| `Interface\AddOns\NeedleDropper_Local\media\` | Your own audio files, if you put any there |

## The beacon strip

To tell the app when a song should start, the addon draws a **beacon**: a strip of coloured
squares in the very top-left corner of the game window. Most of the time it is a 96 × 3 pixel
strip. When a song starts it grows, for a few seconds, to at most 336 × 9 pixels. It stays visible
while the beacon is on, and pressing Alt+Z doesn't hide it.

- It contains only the needle-drop event: the song (its Spotify or YouTube id, artist and title,
  start, length and volume), the name of the place and of the soundtrack, a counter, and a few flags
  (such as "in combat" or "on a flight path", used to pause drops). No character name, realm, chat or
  anything about your account.
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
