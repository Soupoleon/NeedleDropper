# Policies and fair play

NeedleDropper touches three sets of rules: Blizzard's rules for add-ons and third-party software,
Spotify's developer rules, and YouTube's rules for embedded players. This page explains how
NeedleDropper is built to respect each of them, and where the grey areas are. It is a plain-language
summary, not legal advice; the linked originals are what count.

**Contents:** [Blizzard](#blizzard-ui-add-on-development-policy) ·
[The read-only companion](#the-companion-is-strictly-read-only) · [Spotify](#spotify) ·
[YouTube](#youtube) · [No audio is distributed](#no-audio-is-distributed)

## Blizzard: UI Add-On Development Policy

Blizzard's [UI Add-On Development Policy](https://us.forums.blizzard.com/en/wow/t/ui-add-on-development-policy/24534)
sets eight rules for add-ons. NeedleDropper follows each one:

| Blizzard's rule (summarized) | NeedleDropper |
|---|---|
| Add-ons must be free of charge | Free, MIT licensed, no paid or locked features |
| Add-on code must be fully visible, not obfuscated | Plain, commented Lua; the companion app's Python source is public too. Share codes are plain data: the addon checks them field by field and never runs anything from them |
| Add-ons must not hurt realm performance or other players | The addon sends nothing to the game servers or to other players: no addon messages, no chat. Sharing a soundtrack is copy and paste, done by you. Its optional chat-log lines are local system messages that only your own client displays (and hides). Its work is a once-per-second check and a small texture |
| No advertising | None |
| No donation requests inside the game | None |
| Content must suit the game's Teen rating | The addon shows place names and song titles; soundtracks contain only song names, links and notes, and the default soundtrack has no explicit songs |
| Add-ons must follow the Terms of Use and EULA | See [the read-only companion](#the-companion-is-strictly-read-only) |
| Blizzard may disable add-on functionality at any time | Every optional game function is checked before use, so a game change disables a feature instead of breaking the addon. The window is built from Blizzard's standard building blocks, uses no protected functions and hides during combat |

Blizzard's follow-up on **music in add-ons** allows add-ons that replace in-game music but forbids
distributing copyrighted music without permission. NeedleDropper distributes **no music at all**: the
only audio file in the addon is a few seconds of digital silence that NeedleDropper generates itself
(`media\silence.mp3`, used to fade out the zone music). See [No audio is distributed](#no-audio-is-distributed).

NeedleDropper is not endorsed by Blizzard, and Blizzard has the final word on what its rules allow.

## The companion is strictly read-only

The NeedleDropper app, `NeedleDropper.exe`, never interferes with the game. It does **not**:

- read or write the game's memory,
- inject code or DLLs, or hook into the game process,
- intercept or modify network traffic,
- press keys, click, move the mouse or automate anything in the game,
- give any gameplay advantage or read combat data.

What it does:

1. **Looks at a few pixels.** While World of Warcraft: Forever runs, it captures a small area
   (512 × 48 pixels) in the top-left corner of the WoW window, 4 times per second, exactly as
   screen-recording or streaming software does, to decode the beacon the addon draws there.
2. **Optionally reads the chat log** (`WoWChatLog.txt`), a file the game itself writes when chat
   logging is on, the same way combat-log uploaders read the combat log. It never changes the file.
3. **Reads the addon's saved data** (`WTF\...\SavedVariables\NeedleDropper.lua`) after the game has
   written it, to keep backup copies and to look songs up on Spotify ahead of time. It never writes
   that file.
4. **Writes one small data addon.** `Interface\AddOns\NeedleDropper_Companion` holds the app's
   version, whether Spotify is connected and which Spotify songs it found. The game reads it at
   login, like any addon's data file; the WeakAuras Companion delivers its data the same way.
5. **Writes other addon files only when you click:** **Install addon** on the Connect page (refused
   while the game runs, and not offered when an addon manager looks after the addon), and copying your
   own audio files into `NeedleDropper_Local\media`. It never writes into the `NeedleDropper` addon
   folder otherwise.

The game tells the app what to play through the beacon: the song, the place, and a few flags that
tell the app to wait or fade (for example "in combat"). The only way back into the game is that data
addon, read when you log in: the app's status and the songs it found, nothing that acts in the game.

The addon itself uses only the documented add-on API, runs inside the game's add-on sandbox, and
works without the app.

## Spotify

NeedleDropper uses the official Spotify Web API. Every app that uses it, including the developer app
you create for NeedleDropper, falls under Spotify's
[Developer Policy](https://developer.spotify.com/policy) and
[Developer Terms](https://developer.spotify.com/terms).

### The own-app model

- **You register your own app.** NeedleDropper ships no Spotify key and runs no server. Your app is in
  Spotify's Development Mode, for **your personal, non-commercial use** (up to 5 accounts you add
  yourself). See [why](PROVIDERS.md#why-every-player-creates-their-own-developer-app).
- **You sign in on Spotify's own page.** NeedleDropper never sees your password (Authorization Code
  with PKCE) and asks for only two permissions: to see what is playing on your devices and to
  control playback on them. No library, playlist or profile access.
- **Premium only.** Spotify allows playback control only for Premium subscribers; NeedleDropper
  doesn't try to work around that.

### How NeedleDropper uses Spotify

- **The music plays in Spotify's own app**, on your device. NeedleDropper only sends play, pause and
  volume commands; it never receives, records or saves audio.
- **Searches are for your soundtrack only.** For a drop without a Spotify link, the app searches for
  that one song by artist and title, shortly before it plays, and remembers the result.
- **Spotify stays visible as the source.** The Spotify app shows the cover art and song details while
  the song plays, and the NeedleDropper window names the service that is playing.
- **No mixing.** NeedleDropper fades the game's music out before the song starts instead of playing
  the two together, and never alters the Spotify audio.
- **It adds something Spotify doesn't do.** NeedleDropper picks the moment; Spotify plays the song.
  It doesn't imitate or replace any part of the Spotify app.

### Caveats: please read

- **Synchronization with visual media (Developer Policy III.6).** The policy forbids synchronizing
  recordings with visual media such as films, videos or slideshows. A song started when you reach a
  place in a game, with a short text card, could be read as that kind of synchronization. NeedleDropper
  is designed as personal listening, close to pressing play yourself at a good moment, and it never
  makes or publishes a video. Still, this is a **grey area** and Spotify could see it differently.
- **Games (Developer Policy III.2).** The policy also says not to build a game with the Spotify
  platform. NeedleDropper is not a game and adds no gameplay; it is a separate music app that runs next
  to one. Again, Spotify has the final word.
- **Streams and videos.** Spotify's service is for personal, non-commercial listening, not for public
  performance or broadcast. If you stream or record your gameplay, don't broadcast Spotify music
  (copyright claims are likely too): pause NeedleDropper's music or use music you have the rights to.
- **Spotify can change its rules or API access at any time** (it tightened Development Mode in
  February 2026). If Spotify playback stops working, YouTube and in-game files keep working.

## YouTube

YouTube songs use YouTube's standard embedded player (the IFrame Player API). The
[YouTube API Services policies](https://developers.google.com/youtube/terms/developer-policies)
shape how the player works:

- **The player is always visible while it plays.** The policies forbid separating the audio from the
  video and playing content from a background player, which is why YouTube songs play in a small
  player window instead of silently in the background, and why you should keep that window in view.
  YouTube also requires an embedded player to be at least 200 × 200 pixels.
- **YouTube's player is left intact.** NeedleDropper doesn't hide, cover or modify the player, its
  controls or YouTube's links and branding.
- **Ads are never blocked, skipped or changed.** The policies forbid interfering with YouTube's ads, so
  you may see some (unless you have YouTube Premium).
- **Nothing is downloaded.** Every play streams from YouTube; NeedleDropper stores no videos and no
  audio.
- **No YouTube search.** NeedleDropper plays only videos that a soundtrack links to. The built-in
  soundtracks link to official uploads (artist, label or "Topic" channels) whose owners allow
  embedding.

Your use of YouTube itself is governed by YouTube's Terms of Service.

## No audio is distributed

- **The addon contains no music**, only a short silent track that NeedleDropper generates itself.
- **The companion app contains no music.**
- **Soundtracks and share codes contain only metadata**: song titles, artist names, optional Spotify
  and YouTube links, timings and short notes, never audio files or lyrics.
- **Songs come from you**: from your own Spotify Premium account, from YouTube (streamed by YouTube,
  with its ads), or from audio files you own and copy into the game's folder yourself.

NeedleDropper is a fan project. It is not affiliated with or endorsed by Blizzard Entertainment,
Spotify or Google/YouTube.
