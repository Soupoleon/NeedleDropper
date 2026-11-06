# Addon site listing: CurseForge and Wago

The text for NeedleDropper's project pages on **CurseForge** and **Wago Addons**, ready to paste.
Phase 1 of [RELEASING.md](../RELEASING.md) (public beta) is when these pages go live.

Before pasting:

- Replace the placeholders `{{CURSEFORGE_URL}}` and `{{WAGO_URL}}` with the project pages once they
  exist (or delete those lines).
- The GitHub links below point to <https://github.com/Soupoleon/NeedleDropper>. That repository and
  its releases must be **public** before the listing goes live, or players can't download the app.
- Both sites accept Markdown for the description: paste the long description as it is and check the
  preview.
- Keep the text in step with [README.md](../../README.md), [PRIVACY.md](../PRIVACY.md) and
  [POLICY.md](../POLICY.md) when features change.
- Screenshots and their captions: [screenshots.md](screenshots.md).

## The basics

| Field | Value |
|---|---|
| Project name | NeedleDropper |
| Game | World of Warcraft: Forever (the TOC says `## Interface: 16001`; the packager uploads it as game version 1.60.x) |
| Summary | One hand-picked song and a cinematic title card, the first time you arrive somewhere new. |
| License | MIT |
| Source code | <https://github.com/Soupoleon/NeedleDropper> |
| Issues | <https://github.com/Soupoleon/NeedleDropper/issues> |
| The app (download) | <https://github.com/Soupoleon/NeedleDropper/releases> |
| Main category | Audio & Video |

Other summaries, if a site wants a shorter or a longer one:

- *Short:* Cinematic needle drops for Azeroth: a song and a title card when you arrive.
- *Long:* Kojima-style needle drops for WoW: Forever. The first time you reach a place, the zone
  music fades, one hand-picked song starts and a quiet title card drifts in.

## Categories and keywords

| Site | Suggestion |
|---|---|
| CurseForge | Main category **Audio & Video**. Extra categories, if the site offers them: **Roleplay** (it's about immersion), **Miscellaneous** |
| Wago Addons | **Audio & Video**, or the closest category the site lists |
| Search keywords (where a site has a tags or keywords field) | music, soundtrack, needle drop, title card, immersion, exploration, Spotify, YouTube, cinematic, Death Stranding |

Match the names against each site's current list when you create the project; they change now and
then.

## Long description

Copy everything inside the box.

````markdown
**The first time your character crests a hill into Elwynn Forest, the zone music fades away, a song begins, and a quiet title card drifts in at the top of the screen.**

One hand-picked song, for one place, once. Then the world goes quiet again until you find somewhere new.

In *Death Stranding*, Hideo Kojima saves his licensed songs for a few rare moments: you have walked for a long time, the landscape opens up in front of you, and a song starts as the title card fades in. Players call that a **needle drop**. NeedleDropper brings that moment to Azeroth.

## Features

- **First-arrival songs** for zones, capitals, landmarks and, if you like, dungeons and raids.
- **Silence is the default.** A song plays only when you arrive somewhere for the first time, at most every 10 minutes (you choose), and each place plays once per character (or once per account).
- **It waits for a calm moment:** never in combat, on a flight path, while dead, during a cinematic or a boss fight.
- **A cinematic title card:** the place in letter-spaced capitals, a thin line, the artist and the song, gone again after a few seconds. In your game's language, and hidden with the rest of the UI on Alt+Z.
- **One window in game** (`/needle`, the add-on compartment or a key binding) with three tabs: **Drops**, **Soundtracks** and **Journal**.
- **Add drop here:** stand anywhere, press one button, type a song or paste a Spotify or YouTube link, and ▶ **Test** it. It takes effect at once, with no `/reload`.
- **Two built-in soundtracks:** **Azeroth Mixtape** (38 places every player remembers, half the songs fitting and half funny) and **Azeroth Unbound** (a quiet, Kojima-style score for every zone).
- **Make them yours:** change or turn off any built-in drop. Your changes go into **My Drops**, a personal layer on top, and **Revert** brings the original back.
- **Your own soundtracks**, stacked in the order you choose, with a different list per character if you like.
- **Share codes:** a whole soundtrack as one piece of text to paste into Discord. Friends see a preview before they import it.
- **A journal** of every song you've heard, with replay and "play again next visit".
- **Three ways to play:** Spotify Premium or YouTube through the free NeedleDropper app, or your own audio files right inside WoW.
- **Music services in the game's settings:** see the app's status, set up Spotify, choose *Play music with* (Spotify first, YouTube first, only one, or neither) and the YouTube box's corner, and test both services without leaving the game.
- **`/needle report`** gives you a text report of your setup to paste into a bug report.

## How it works

1. **You curate in game.** The NeedleDropper window shows which song plays where. Change one, add a place or switch soundtracks.
2. **The addon notices** when you arrive somewhere new (the game's "Discovered: …" message, or a short stay in a zone you've never explored) and waits for a calm moment.
3. **The moment comes:** it fades out the zone music, shows the title card, and either plays your own audio file in WoW or hands the song to the app through a tiny strip of coloured squares in the top-left corner of the game window.
4. **The app plays it.** The optional NeedleDropper app for Windows reads that strip from the screen and plays the song in its own hidden Spotify player (no Spotify window, and the game keeps the focus), or in a small YouTube player window. It never touches the game itself.

## Requirements

- **World of Warcraft: Forever.** On its own, the addon shows the title cards and plays songs from your own audio files (.mp3 or .ogg, copied into `Interface\AddOns\NeedleDropper_Local\media`).
- **For Spotify and YouTube songs, the free NeedleDropper app** for Windows 10 and 11 (64-bit). It is a separate download from GitHub and is **not included in this addon download**: [download the app](https://github.com/Soupoleon/NeedleDropper/releases). It needs no installation; its one-time Connect page sets everything up, then it waits in the tray.
  - **Spotify:** Spotify Premium, Google Chrome (the app's Spotify player runs in it; the Spotify app doesn't need to be open), and a free Spotify developer app of your own. The Connect page, or the Music services section in the game's settings, walks you through it in about two minutes. NeedleDropper ships no shared Spotify key.
  - **YouTube:** nothing to set up. A small player window shows the video while the song plays; YouTube's rules say it must stay visible.
- Built-in songs are Spotify and YouTube songs, so to hear them you need the app.

## Privacy and fair play

- **No account, no server, no analytics.** The addon itself sends nothing anywhere: no addon messages, no chat.
- **The app only talks to Spotify and YouTube**, to play your music, plus an optional daily check for a new version. Nothing about your game, characters, realm, chat or location leaves your PC.
- **Strictly read-only toward the game.** The app never reads or changes the game's memory, injects code, or presses keys for you. It looks at a few pixels in the top-left corner of the game window, the way screen-recording software does.
- **No music is distributed.** Soundtracks contain only song titles, artists and links. The songs come from your own Spotify Premium account, from YouTube (with its ads), or from audio files you own.
- Free and open source under the MIT license. The whole story: [Privacy](https://github.com/Soupoleon/NeedleDropper/blob/main/docs/PRIVACY.md) and [Policies](https://github.com/Soupoleon/NeedleDropper/blob/main/docs/POLICY.md).

## FAQ

**Do I need the app?**
Only for Spotify and YouTube songs. Without it, NeedleDropper still shows its title cards and plays your own audio files in game.

**Why isn't the app in this download?**
Addon sites host addons, not Windows programs. The app lives on GitHub, next to its source code, and NeedleDropper's addon works fine without it.

**Is the app allowed? Is it safe?**
The addon follows Blizzard's UI Add-On Development Policy, and the app never interferes with the game: no memory reading, no injection, no automation, no gameplay advantage. It only reads a small strip of pixels from the screen. Blizzard always has the final word on its rules; our [Policies](https://github.com/Soupoleon/NeedleDropper/blob/main/docs/POLICY.md) page explains how NeedleDropper follows them.

**Windows says "Windows protected your PC" when I start the app.**
The app isn't code-signed yet, so Windows SmartScreen doesn't know it. Click **More info**, then **Run anyway**. Every release lists the files' SHA-256 checksums, and the source code is public.

**Why do I need my own Spotify developer app?**
Spotify only lets registered apps control playback, and a new app allows at most 5 users. One shared NeedleDropper app would stop working after the fifth player, so each player registers a free app of their own. The Connect page shows you how.

**Does it work with free Spotify?**
No: Spotify's player for apps works only for Premium accounts. YouTube works for everyone.

**Does a Spotify window pop up?**
No. The app plays Spotify songs in a hidden player of its own (it needs Google Chrome), and Spotify lists it among your devices as *NeedleDropper*. The only window NeedleDropper ever shows while you play is the small YouTube box, while a YouTube song plays.

**Will it play songs all the time?**
No. A song plays only the first time you arrive somewhere, at most every 10 minutes by default, and only at a calm moment. Most of the time, the world is as quiet as you're used to.

**Can I choose my own songs?**
Yes. Type `/needle`, stand where you want a song, press **Add drop here** and type an artist and title, or paste a Spotify or YouTube link. You can also change or turn off any built-in drop.

**Does it work with addon managers?**
Yes. Your own audio files (`NeedleDropper_Local`) and the app's small data addon (`NeedleDropper_Companion`) live in separate folders that addon managers leave alone.

**I stream or record my gameplay.**
The beacon strip in the top-left corner shows up in recordings; it only carries the song event, but you may want to crop that corner. Spotify is for personal listening only, so don't broadcast Spotify music.

**Mac? Apple Music?**
The app is for Windows 10 and 11. Without it, the addon's title cards and your own audio files still work. Apple Music isn't supported.

## Credits

- **Hideo Kojima and Kojima Productions' *Death Stranding***, whose needle drops are the whole idea.
- **The *Soundtrack* addon**, for the "silent track" trick that fades out zone music without touching your sound settings.
- **The BigWigs packager**, which builds and publishes every release.
- **The artists of the built-in soundtracks.** NeedleDropper ships no music; it only plays songs you can already stream or own. If a drop moves you, follow, stream or buy the artist.

NeedleDropper is a fan project. It is not affiliated with or endorsed by Blizzard Entertainment, Kojima Productions, Sony Interactive Entertainment, Spotify or Google/YouTube. World of Warcraft is a trademark of Blizzard Entertainment; Death Stranding is a trademark of Sony Interactive Entertainment; Spotify and YouTube are trademarks of their owners.

## Links

- **The NeedleDropper app (Windows):** https://github.com/Soupoleon/NeedleDropper/releases
- **Install guide:** https://github.com/Soupoleon/NeedleDropper/blob/main/docs/INSTALL.md
- **Troubleshooting:** https://github.com/Soupoleon/NeedleDropper/blob/main/docs/TROUBLESHOOTING.md
- **Report a bug or suggest an idea:** https://github.com/Soupoleon/NeedleDropper/issues
- **Source code:** https://github.com/Soupoleon/NeedleDropper
- **CurseForge:** {{CURSEFORGE_URL}}
- **Wago Addons:** {{WAGO_URL}}
````

## Short "about the app" note

For a site's sidebar, a pinned comment or a reply to "where are the songs?", wherever the app needs
mentioning in one line:

> NeedleDropper's title cards and in-game audio files work on their own. For Spotify and YouTube
> songs, also install the free NeedleDropper app for Windows from
> <https://github.com/Soupoleon/NeedleDropper/releases> (not included in this download).

## Checklist before publishing the page

- [ ] The GitHub repository and its releases are public, and the latest release has the app.
- [ ] The listing says, near the top of Requirements, that the app is optional, separate and on
      GitHub.
- [ ] No shared Spotify Client ID anywhere on the page.
- [ ] Screenshots follow [screenshots.md](screenshots.md): no character names, chat, Spotify account
      details or video frames.
- [ ] The project id is in the TOC (`## X-Curse-Project-ID`, `## X-Wago-ID`) and the API tokens are
      repository secrets, so the release workflow uploads new versions by itself.
