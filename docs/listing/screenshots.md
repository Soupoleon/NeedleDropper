# Listing screenshots: the shot list

The screenshots for the CurseForge and Wago pages ([LISTING.md](LISTING.md)), in the order they
should appear. The first one is the page's lead image, so it has to sell the idea on its own.

Save them as `assets/screenshots/<file>` (the file names are in the table). `assets/` is on the
`.pkgmeta` ignore list, so screenshots never end up in the addon zip.

## Before you take them

- **Resolution:** take them at 1920 × 1080 or larger, 16:9, and save as PNG. Scale 4K shots down to
  1920 × 1080 (or 2560 × 1440) before uploading.
- **Default UI scale** and the default Edit Mode layout, so players see what they will get. Hide
  other addons' frames where you can.
- **Nothing personal:** no character, realm or guild names (use a fresh character, or crop or blur the
  names), no chat (clear it, or hide the chat frame), no whispers, no Battle.net friend toasts.
- **The beacon strip** sits in the top-left corner. For pure in-game shots, type `/needle beacon off`
  first and `/needle beacon on` afterwards, or crop the corner. (With the beacon off the app can't
  hear the game, so the music won't start; that doesn't matter for a still image.)
- **No cover art or video frames.** Song titles and artist names on the title card are fine, but
  don't show Spotify cover art or a YouTube video picture in the listing: they belong to the artists
  and labels.
- **The Connect page:** blur the Spotify account name and email, and the Client ID. Use a clean
  browser window (no bookmarks bar, no other tabs).
- Captions should read well without the picture too: some sites show them as the alt text.

## The shots

| # | File | What to show | Caption |
|---|---|---|---|
| 1 | `01-title-card-razor-hill.png` | **The title card at Razor Hill.** The owner already has this one (4K, borderless window, 2026-11-05): the letter-spaced *R A Z O R   H I L L*, the thin line, *ZZ Top · Sharp Dressed Man*, about 22 % from the top of the screen, Durotar behind it. Scale it to 1920 × 1080, crop the beacon corner and check that no name or chat shows | The first time you arrive somewhere, the zone music fades, a song starts and the title card drifts in. |
| 2 | `02-drops-tab.png` | **The window, Drops tab**, open in a recognisable place (Goldshire or Orgrimmar), with the list of drops, a selected row and the **Add drop here** button | Type /needle: see which song plays where, and press Add drop here to give any place a song. |
| 3 | `03-drop-form.png` | **The Drops tab's form** with a Spotify song link pasted into a drop and the ▶ **Test** button in view (Artist, Title, Spotify and YouTube fields filled in) | Paste a Spotify or YouTube link, or just type the artist and title. ▶ Test plays it at once. |
| 4 | `04-soundtracks-tab.png` | **The Soundtracks tab** with Azeroth Mixtape, Azeroth Unbound and My Drops (and one soundtrack of your own) in the list, their **On** boxes and the ↑ / ↓ order buttons | Turn soundtracks on and off and stack them. Your own changes sit on top, in My Drops. |
| 5 | `05-journal-tab.png` | **The Journal tab** after a few drops, with ▶ **Replay** | The Journal remembers every song you've heard, with Replay. |
| 6 | `06-share-dialog.png` | **The Share dialog** for a soundtrack, with its `!NeedleDropper:1!…!` code selected | Share a whole soundtrack as one code for Discord. |
| 7 | `07-import-preview.png` | **The Import dialog's preview** after pasting a code: the soundtrack's name, its drop count and places | Friends see a preview before they import, and imports arrive turned off. |
| 8 | `08-connect-page.png` | **The app's Connect page** in the browser, all four cards visible (World of Warcraft, Spotify, YouTube, Check the link), with the Check the link tick | The optional app for Spotify and YouTube: one Connect page to set up, then just a tray icon. |

Optional extras, if a site allows more images:

| # | File | What to show | Caption |
|---|---|---|---|
| 9 | `09-settings.png` | NeedleDropper's page in the game's Settings panel (delays, cooldown, title card options) | Arrival delays, cooldown and the title card's size and position, in the game's Settings panel. |
| 10 | `10-compartment-tooltip.png` | The add-on compartment tooltip by the minimap, showing what is playing and the app's status | Hover over NeedleDropper by the minimap to see what's playing or waiting. |
| 11 | `11-youtube-player.png` | The game in a window with the small YouTube player beside it (blur the video picture) | No Spotify? YouTube songs play in a small player window next to the game. |

## Alt text

For sites that ask for alt text separately:

1. A World of Warcraft landscape in Durotar with a centred title card: "RAZOR HILL" in spaced
   capitals over a thin line, then "ZZ Top · Sharp Dressed Man".
2. The NeedleDropper window's Drops tab: a list of places with their songs, and an Add drop here
   button.
3. The Drops tab's form for one place, with Artist, Title, Spotify and YouTube fields and a Test
   button.
4. The Soundtracks tab: three soundtracks with On boxes and buttons to change their order.
5. The Journal tab: songs heard so far, each with a Replay button.
6. The Share dialog showing a long share code that starts with "!NeedleDropper:1!".
7. The Import dialog previewing a soundtrack's name, drop count and places.
8. The NeedleDropper app's Connect page in a web browser, with four cards: World of Warcraft,
   Spotify, YouTube and Check the link.
