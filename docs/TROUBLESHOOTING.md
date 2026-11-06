# Troubleshooting

Start with three quick checks:

1. **In the game, type `/needle status`.** It shows whether needle drops are on, your active
   soundtracks, where you are, what is pending and *why it is waiting*, what is playing, the
   cooldown, and the state of the link to the NeedleDropper app.
2. **Open the window (`/needle`) and read its footer.** The status line says what is playing or
   waiting, and what NeedleDropper knew about the app when you logged in, for example *App seen
   2 min before login · Spotify connected* or *NeedleDropper app not found: Spotify and YouTube songs
   need it*. Hovering over NeedleDropper in the add-on compartment (the small numbered button by the
   minimap) shows the same.
3. **Type `/needle test`.** If the app can see the game, it shows a notification within about two
   seconds and the **Check the link** card on its Connect page gets a tick. Nothing plays: the test
   only checks the link. No notification? See [Beacon not detected](#beacon-not-detected).

You only need the app for Spotify and YouTube songs. Title cards and in-game audio files work
without it.

**Contents:** [Beacon not detected](#beacon-not-detected) · [Drops don't start](#drops-dont-start) ·
[Zone music](#zone-music-doesnt-come-back) · [Editing drops](#editing-drops-in-game) ·
[Share codes](#share-codes) · [Spotify](#spotify) · [YouTube](#youtube) ·
[Chat-log link](#chat-log-link) · [The app](#the-needledropper-app) ·
[Installing the addon](#installing-the-addon) · [Logs](#where-the-logs-are) ·
[Command reference](#needle-command-reference) · [In-game options](#in-game-options) ·
[Key bindings](#key-bindings) · [Companion settings](#companion-settings)

## Beacon not detected

**Symptoms:** `/needle test` brings no notification, or the title card appears in the game but no
Spotify or YouTube song follows.

The app reads the **beacon**: a strip of coloured squares in the very top-left corner of the game
window. Most of the time it is a 96 × 3 pixel strip; when a song starts it grows to at most
336 × 9 pixels for a few seconds, because it carries the song itself. Anything that hides or
changes those pixels breaks the link.

1. **Is the app running?** Look for the NeedleDropper icon in the notification area of the taskbar
   (it may be under the **^** arrow). The app waits quietly until the World of Warcraft: Forever
   window exists and only then starts reading; give it a few seconds after the game opens.
2. **Run `/needle selftest beacon`.** It shows the largest song strip for 10 seconds, sends a test
   signal (the app shows its notification) and prints the beacon's cell size, rows, strip size and
   the measured gap above your player frame. Settings → NeedleDropper → **Send a test signal** does
   the same test without the big strip.
3. **Don't minimize the game.** While WoW is minimized the app can't see the beacon. (A song that
   should have started begins once you bring the game back.) Both display modes the game offers,
   **Windowed** and **Fullscreen (Windowed)**, work.
4. **Turn off HDR.** Windows HDR (*Settings → System → Display → Use HDR*) or the game's own HDR
   option changes the colours that screen capture sees. Turn it off, at least for WoW.
5. **Reset the picture settings.** Brightness, contrast and gamma in the game's graphics options
   change the beacon's colours: set them back to their defaults. Post-processing tools that draw into
   the game picture (ReShade and similar filters, driver sharpening) do the same.
6. **Keep the top-left corner clear.** FPS counters and overlays (RivaTuner / MSI Afterburner, the
   NVIDIA or AMD overlay, Steam, Xbox Game Bar widgets) and Discord's overlay notifications, which
   appear in the top-left corner by default, can cover the strip. Move them to another corner. Addon
   frames placed in that corner do the same.
7. **Check that the beacon is on:** `/needle beacon on`. `/needle status` shows the beacon as on.
8. **If the link comes and goes,** try bigger squares: `/needle set beaconCellPx 4` (the default is
   3; the app reads the size from the strip itself, so there is nothing to change on the PC). Bigger
   squares mean fewer rows, so less fits into one signal: go back to 3 if the link was fine.
9. **Still nothing?** Use the [chat-log link](#chat-log-link) instead, or watch the raw link: open a
   command prompt in the folder that holds `NeedleDropper.exe` (type `cmd` in File Explorer's address
   bar and press Enter), run `NeedleDropper.exe diag`, then type `/needle selftest beacon` in the
   game. `diag` prints every frame it decodes and should show `PING`.

Windows display scaling (125 %, 150 % and so on) is supported: the app measures the squares on
screen.

### The beacon overlaps your player frame

The beacon is never taller than 9 pixels, which normally leaves a gap above the player frame and
the rest of the default UI. On small screens with a small UI scale, or after you move frames into
the top-left corner, the gap can shrink. NeedleDropper measures it whenever you log in, change the UI
scale or the window size, or leave Edit Mode, and then uses fewer rows or smaller squares.

If even that doesn't fit, it still draws one row (the app needs it) and `/needle selftest beacon`
warns: *the beacon overlaps your player frame: move the frame in Edit Mode or set beaconCorner*.

- **Move the frame:** Game Menu → **Edit Mode**, click the **Player Frame** and drag it a little
  down or to the right, then save the layout. The **Modern** layout puts the player frame at the
  bottom of the screen; the **Classic** layout keeps it in the top-left corner.
- **`beaconCorner`** is an advanced setting (`/needle set beaconCorner …`, not in the Settings
  panel) that moves the strip to another corner of the screen. Leave it at `topleft`: the
  NeedleDropper 1.2 app looks for the beacon in the top-left corner.

## Drops don't start

`/needle status` usually says why: its `pending:` line lists what the drop is waiting for (arrival
time, combat, flight path, cooldown, another drop playing). The window's Drops tab shows each place's
state in words: *heard ✓*, *needs a song*, *off*, *overridden*.

| Reason | What to do |
|---|---|
| **This character already heard it.** Each place plays once per character | The Journal tab lists what you have heard; **Play again next visit** on a row lets that place play again, ▶ **Replay** plays it now |
| **The place counts as explored.** With *Exploration history: Respect* (the default), places from the **built-in** soundtracks that your character explored before NeedleDropper count as heard. Drops you add yourself always play on your next visit | `/needle set historyMode fresh`, and `/needle reset char` to forget this character's drops |
| **Once per account.** With *Play each drop once per: Account*, another character already heard it | `/needle set scope character` |
| **Cooldown.** After a drop, the next one waits 10 minutes by default | Wait, or `/needle set cooldownMin 5` |
| **Arrival delay.** You must stay 10 s in a new zone (3 s after a "Discovered" message, 4 s in a landmark) | Stay a moment; the delays are options |
| **Not calm yet.** In combat, on a flight path, dead, in a cinematic or loading screen, or in a boss encounter | It starts by itself when that ends (as long as you stay there) |
| **No song for this place.** The Drops tab says *No needle drop for <place> yet* | Press **Add drop here**, then paste a Spotify or YouTube link ([how](PACKS.md#add-a-drop-where-you-stand)) |
| **The drop is a draft** (*needs a song*) | Fill in **Artist** and **Title**: a drop without them never plays |
| **You turned it off** (*off*), or a soundtrack higher in your list turned it off | Right-click the drop → **Turn on**, or **Revert** it |
| **A soundtrack above has a song for this place** (*overridden*) | The higher one plays instead. Change the order on the Soundtracks tab ([priority](PACKS.md#which-song-plays-the-priority-order)) |
| **The soundtrack isn't on.** | Soundtracks tab: tick **On** next to it |
| **This character uses its own list.** *Use different soundtracks on this character* is ticked on the Soundtracks tab | Turn the soundtrack on in this character's list, or untick the box to use the account's list |
| **You added it where you stand.** A drop you add with **Add drop here** doesn't start during that visit, so it can't interrupt your editing | ▶ **Test** plays it now; it plays by itself on your next arrival |
| **Needle drops are switched off.** | `/needle set enabled on` (or the *Enable needle drops* option) |
| **Dungeons and raids.** | Instance drops need the *Instance drops* option on |

**The title card appears but no music plays:** the addon did its part.

- Is the app running, and does `/needle test` reach it? See [Beacon not detected](#beacon-not-detected).
- Does the drop have a source? The letters next to it in the Drops tab show what it has: **S**
  Spotify, **Y** YouTube, **F** in-game file. A drop with only Artist and Title plays only through
  Spotify (the app searches for it), so without Spotify it stays silent.
- Did the app show a notification such as *song not found on Spotify* or *YouTube video can't be
  embedded*? See [Spotify](#spotify) and [YouTube](#youtube).
- The footer says *Update the NeedleDropper app to 1.2 to play Spotify and YouTube songs*: the app
  on your PC is older than the addon. Update it ([how](INSTALL.md#updating)).

**Music plays but there is no title card:** title cards may be off (`/needle toast on`), or the
game UI is hidden (Alt+Z hides the card with the rest of the UI). `/needle toast` shows a preview.

## Zone music doesn't come back

- After a drop the zone music should return within a few seconds. If it doesn't, try
  `/needle set kickZoneMusic on`, or `/needle set duckMethod volume`. `/needle selftest music` fades
  the music out for 8 seconds and back, to test this.
- If a drop's length is unknown, the game keeps the zone music faded for *Length when unknown*
  (5 minutes by default), even when the song is shorter. Set **Play for** under **More…** in the
  drop's form. (The built-in soundtracks already know every song's length.)
- If the game closed during a drop, NeedleDropper puts your sound settings back at the next login
  (it says so in the chat).

## Editing drops in game

| You see | What it means |
|---|---|
| *needs a song* | The drop is a draft: it has no Artist or Title yet, so it never plays |
| *That's an album or playlist link: in Spotify right-click the song, then Share, then Copy Song Link.* | Only links to single songs work. Copy the song's own link |
| *That's a YouTube playlist link: open the video and copy its own link.* | Use the video's own link (`youtube.com/watch?v=…`, `youtu.be/…`, `/shorts/`, `/embed/`, `/live/`, `music.youtube.com`), not a playlist or channel |
| *not found: copy it there and restart WoW (a /reload is not enough)* | The in-game file isn't in `Interface\AddOns\NeedleDropper_Local\media`, or it was copied while the game was running. WoW only notices new files when it starts |
| *landmark saved by name: works on <language> clients only* | NeedleDropper couldn't learn the landmark's area number, so it matches the name your game shows. Players with another game language won't trigger it. The number comes from the game's exploration map: stand inside the landmark (once your character has explored it) and press **Use my location** |
| The fields of a drop are greyed out, with *From Azeroth Mixtape (built-in)* | Built-in soundtracks are read-only. Click **Change song here** to make your own version in My Drops ([how](PACKS.md#change-a-song-from-a-built-in-soundtrack)) |
| **Save now (reloads UI)** in the footer | You made 10 or more changes since you logged in. WoW writes addon data to disk only when you log out, quit or reload, so a game crash would lose them. Click it to save now (the interface reloads; you stay in the game) |
| The window vanished in a fight | It hides during combat and comes back afterwards. Text you were typing is kept |
| *Deleted Goldshire. [Undo]* | Deleting is instant; **Undo** brings the drop back for 15 seconds |

Limits: 50 soundtracks of your own, 300 drops in each, 3,000 in total.

Edits take effect at once, no `/reload`: the app learns the song from the beacon the next time
the drop plays (or when you press ▶ **Test**).

## Share codes

A share code starts with `!NeedleDropper:1!` and ends with `!`. The Import dialog finds the code
anywhere in what you paste, so text around it, Discord's ```` ``` ```` fences, line breaks and
invisible characters don't matter. If the code can't be read, the dialog says why:

| Reason (in bug reports) | The dialog says, in short | What it means | What to do |
|---|---|---|---|
| `notcode` | *This is not a NeedleDropper soundtrack code.* | The pasted text has no `!NeedleDropper:1!` in it | Copy the whole code, from the first `!` to the last |
| `foreign` | *This is a WeakAuras string, not a NeedleDropper soundtrack* (or ElvUI, Plater, MDT, BigWigs, Details, Cell, Blizzard) | You pasted another addon's export | Import it in that addon instead |
| `newer` | *This code was made with a newer NeedleDropper: update the addon.* | The code uses a format this version doesn't know | Update the addon |
| `cutoff` | *The code was cut off* | The end of the code is missing | Copy it again. From Discord, open the attached `message.txt` and copy all of it rather than the preview |
| `damaged` | *The code is damaged* | Some characters changed (autocorrect, a chat filter, a hand edit). Codes carry a checksum, so NeedleDropper refuses them instead of importing wrong songs | Ask for a fresh copy |
| `toobig` | *This code is too big to be a NeedleDropper soundtrack.* | More than 65,536 characters, or it unpacks to more than a soundtrack can hold | A soundtrack has at most 300 drops: split it |
| `invalid` | *This soundtrack has problems and cannot be imported.* | The code is readable, but its contents break the rules (no drops at all, more than 300 drops, the same place twice, values out of range) | Ask its author to export it again from NeedleDropper 1.2. Pack authors can check a file with `NeedleDropper.exe packs validate` ([how](PACKS.md#the-packs-commands)) |

The preview can also show **warnings**. They don't stop the import:

- **unknown zones**: the game doesn't know that map number (a different game version);
- **in-game files you may not have**: the drop names an audio file you'd have to copy into
  `NeedleDropper_Local\media` yourself;
- **landmarks saved by name**: they only work on the author's game language;
- **places this would override** among your active soundtracks, once you turn it on;
- **fields that were dropped**: values NeedleDropper doesn't accept (an unsafe file name, an
  unknown key) were left out.

An import never overwrites anything and arrives **turned off** (*Imported <name> (off). [Turn on]*).
When you already have the same soundtrack from the same author and the code is newer, the dialog
offers **Update <name>** instead, which keeps its on/off state and its place in your list. More
in [Share codes](PACKS.md#share-codes).

## Spotify

Setup steps and details: [Music services → Spotify](PROVIDERS.md#spotify-premium).

| Error or notification | Meaning | Fix |
|---|---|---|
| **403** (*Premium required* on the Spotify card) | Not a Premium account; or the account isn't on your developer app's **User Management** list; or the app's owner no longer has Premium | Use Premium; [add the account](PROVIDERS.md#signing-in-with-a-different-spotify-account-user-management) to your app |
| **404** / no active device | No Spotify device is online | Open the Spotify app on this PC, play and pause any song once, then pick it under **Play on** on the Spotify card |
| **429** / rate limited | Too many requests in a short time, or Spotify's `QUOTA_EXCEEDED` | Wait a few minutes; NeedleDropper already waits when Spotify asks it to |
| **INVALID_CLIENT: Invalid redirect URI** | The redirect URI in your developer app isn't exactly right | Set it to `http://127.0.0.1:8321/callback` (127.0.0.1, http, no `/` at the end) |
| **INVALID_CLIENT: Invalid client** | Wrong Client ID, or the developer app was deleted | Copy it again from the [dashboard](https://developer.spotify.com/dashboard) |
| Notification: Spotify sign-in expired | Spotify ended the login (password change, removed access, a long break) | Click the notification (or tray icon → **Open setup…**) and **Sign in** again on the Spotify card |
| Notification: song not found on Spotify | The drop has no Spotify link, and searching for its artist and title found nothing close enough | Paste the song's Spotify link into the drop (for a built-in drop: **Change song here** first). Meanwhile it plays on YouTube if it has a YouTube link |
| The wrong recording plays | The drop has no Spotify link and the search picked another version | Paste the exact song link. After the next login, the drop's form shows the match the app found (*Spotify match: … [Use this exact song]*) so you can keep it with one click |
| The song starts on the wrong device | Another device was active | Choose the device under **Play on** |

If Spotify can't play a drop, the app falls back to the drop's YouTube video, if it has one.

## YouTube

| Error | Meaning | Fix |
|---|---|---|
| **100** | The video was removed or made private | Paste another upload into the drop (for a built-in drop: **Change song here** first). With Spotify connected, the song plays there instead |
| **101** or **150** | The video's owner doesn't allow it to be played outside YouTube (embedding is off). The app also shows a notification, and after your next login the drop shows a warning in the window | Same: paste another official upload ([how to check one](PACKS.md#guidelines-for-shared-soundtracks)) |
| **153** | YouTube didn't learn which page is embedding the player (the "Referer" information was missing) | Use the player window NeedleDropper opens (its address starts with `http://127.0.0.1:8322/`); turn off privacy extensions or settings that strip referrers for `127.0.0.1`; update Edge or Chrome |
| **2** | The video id is malformed | Paste the video's link again |
| **5** | The browser couldn't play the video | Update Edge or Chrome |

- **No sound:** click once inside the player window (*Click to enable sound*), and check that
  Edge or Chrome isn't muted in the Windows volume mixer.
- **The player window doesn't open:** it opens only for YouTube songs. Without Edge or Chrome,
  NeedleDropper opens the player in your default browser instead.
- **Ads** are normal for embedded YouTube videos; NeedleDropper doesn't block them.

## Chat-log link

The chat-log link is a backup for setups where the beacon can't be read. It is slower than the beacon
(about a second) and **off by default**.

**Privacy first:** turning it on makes WoW write *all* chat (whispers, guild, party, trade) to
`World of Warcraft\_forever_\Logs\WoWChatLog.txt` in plain text. See [Privacy](PRIVACY.md#the-optional-chat-log-link).

To use it:

1. In the game: `/needle chatlog on`. This also switches on WoW's chat logging. NeedleDropper's own
   lines (they start with `NDROP2`) are hidden from your chat window. The app reads the log file
   by itself; there is nothing to switch on there.
2. Test it with **`/needle selftest chatlog`**. It reports whether chat logging is on (if it isn't,
   type `/chatlog` in the chat box), sends a test signal, then writes hidden filler in 8 KB steps up
   to 64 KB, once per second. WoW writes its log to disk in chunks; the filler pushes the signal out.
   The app's notification (or `NeedleDropper.exe diag`) shows when it arrives.
3. If the signal arrives only after more filler than the default, raise *Chat-log padding*
   (`/needle set chatlogPadBytes 60000`) to a little more than that size.

The app only accepts NeedleDropper lines that arrive as system messages, so other players can't fake
one by typing it in chat. While the game restricts chat (in some instances and encounters),
NeedleDropper holds its lines back, and the app ignores a song signal that arrives more than
10 seconds late.

To stop: `/needle chatlog off`, then `/chatlog` until the game says logging is off.

## The NeedleDropper app

- **Where is it?** In the notification area of the taskbar (maybe under the **^** arrow). It has no
  window of its own: left-click the icon (or choose **Open setup…**) to open the Connect page in your
  browser, at `http://127.0.0.1:8322/` while the app runs. Starting `NeedleDropper.exe` again while
  it runs just opens that page.
- **The menu:** a status line, **Open setup…**, **Pause needle drops**, **Start with Windows**,
  **Open log folder** and **Quit**. While needle drops are paused the app ignores the game, and the
  icon shows the tone arm parked.
- **Notifications** appear only when they matter: linked to World of Warcraft (once per session), a
  test signal arrived, Spotify sign-in expired, a song wasn't found on Spotify, a YouTube video can't
  be embedded, and (if the update check is on) a new version is available.
- **It starts with Windows** (if **Start with Windows** is ticked) and stays idle until the game
  runs. To stop that, untick it in the tray menu, or switch NeedleDropper off in Task Manager →
  *Startup apps*.
- **The footer in game says *NeedleDropper app not found*:** the game didn't load the small
  **NeedleDropper Companion Data** addon the app writes. Make sure it is ticked in the AddOns list,
  and if the app's first run happened while WoW was open, restart the game once (WoW only discovers
  new addon folders when it starts). Spotify and YouTube still play if `/needle test` reaches the
  app: the footer only reports what the app said at login.
- **The Connect page doesn't load:** the app isn't running, or another program uses port 8322.
  Quit other local servers and start `NeedleDropper.exe` again.
- **Updates:** when its update check is on, the app looks for a new release once a day and, if
  there is one, shows a notification. It never installs anything by itself. The check is off
  until an update address is set (`updates.releasesUrl` in `config.json`).

## Installing the addon

- **NeedleDropper isn't in the game's AddOns list**: the folder must be
  `...\_forever_\Interface\AddOns\NeedleDropper\NeedleDropper.toc` (not nested one level deeper).
- **"Out of date"** in the AddOns list after a game patch: tick *Load out of date AddOns*.
- **NeedleDropper Companion Data** in the AddOns list: that is the app's small data file (status,
  Spotify matches). Leave it ticked. It is optional: deleting it does no harm, and the app writes it
  again the next time it starts.
- **The Connect page has no Install addon button**: the addon is already installed, or an addon
  manager looks after it (then update it there).
- **"The game is running"**: close WoW completely, then click **Install addon** again. New addon
  files are only loaded when the game starts.
- **"Access denied" / the AddOns folder isn't writable**: install from the addon zip by hand
  ([how](INSTALL.md#from-the-addon-zip)).

## Where the logs are

| What | Where |
|---|---|
| App log | `%APPDATA%\NeedleDropper\logs\needledropper.log` (up to 5 files of 1 MB); tray icon → **Open log folder** |
| App settings | `%APPDATA%\NeedleDropper\config.json` |
| Addon messages | `/needle debug on` prints diagnostic messages in the chat window |
| Game Lua errors | Type `/console scriptErrors 1` to make WoW show addon errors |
| Taint (the game blocking an action) | `/console taintLog 1`, play, then read `World of Warcraft\_forever_\Logs\taint.log`; `/console taintLog 0` turns it off again |

**Reporting a problem?** Include the app version (`NeedleDropper.exe --version`), the addon version
(first line of `/needle help`), the output of `/needle status`, and the relevant part of
`needledropper.log`. The log never contains passwords or tokens, but file paths in it can show your
Windows user name. A share code of the soundtrack involved helps too.

## /needle command reference

Type the commands in the game's chat box. `/nd` is a short form of `/needle`. `/needle` on its own
opens or closes the NeedleDropper window. For `on|off` commands, leaving out the word switches the
setting.

| Command | What it does |
|---|---|
| `/needle` | Open or close the NeedleDropper window |
| `/needle help` | List every command in the chat window |
| `/needle add [Artist - Title \| link]` | **Add drop here**: adds the place where you stand to My Drops (or to the soundtrack the Drops tab is showing) and opens it in the window, cursor in Artist. With text it also fills in the song and saves it: `/needle add Toto - Africa` or `/needle add https://youtu.be/QAo_Ycocl1E`, then prints *Added Goldshire: Toto · Africa to My Drops.* If the place already has a drop, that drop opens instead |
| `/needle soundtracks` | Open the window on the Soundtracks tab |
| `/needle journal` | Open the window on the Journal tab |
| `/needle journal print [count]` | Print the songs this character has heard in the chat window instead (the last 15, or `count`) |
| `/needle import` | Open the Import dialog to paste a share code |
| `/needle share [name]` | Open the Share dialog for one of your soundtracks, by name: `/needle share My Drops` |
| `/needle status` | Settings summary, active soundtracks, beacon and chat-log link, where you are, the pending drop and what it waits for, what is playing, cooldown, how many drops you have heard |
| `/needle where` | Where you stand, for soundtrack authors: zone key and map number, subzone and its by-name id, instance id, the area numbers here, and the drops that match |
| `/needle replay [id]` | Replay the last drop you heard, or the one with that id. Doesn't change your history |
| `/needle play <id or place>` | Play any drop now, by id or place name: `/needle play zone:1429` or `/needle play Elwynn Forest`. Doesn't mark it as heard |
| `/needle test` | Show the test title card and send a test signal: the app shows a notification and ticks **Check the link** on its Connect page. Nothing plays |
| `/needle toast [on\|off]` | Preview the title card for where you stand; with `on` / `off`, turn title cards on or off |
| `/needle stop` | Stop the song that is playing. With nothing playing, let the pending drop go for now: it comes back on your next visit |
| `/needle skip` | Stop the song and keep it marked as heard. With nothing playing, mark the pending drop as heard so it never plays |
| `/needle reset char\|account\|all\|settings` | Forget this character's drops, journal and cooldown (`char`), the account-wide drops (`account`), both (`all`), or put every setting back to its default (`settings`). Your soundtracks are never touched |
| `/needle beacon on\|off` | The pixel beacon the app reads |
| `/needle chatlog on\|off` | The [chat-log link](#chat-log-link) (turns WoW's chat logging on) |
| `/needle selftest [beacon\|chatlog\|discovery\|music\|share]` | Check your setup: `beacon` (default) shows the largest beacon for 10 seconds, sends a test signal and prints the beacon's size and the gap above your player frame; `chatlog` tests the chat-log link; `discovery` prints every discovery message, exploration update and zone change for 120 seconds; `music` fades the zone music out for 8 seconds and back; `share` checks that share codes can be read and written on your client |
| `/needle debug on\|off` | Diagnostic messages in the chat window |
| `/needle set <setting> <value>` | Change a setting, for example `/needle set cooldownMin 15` (names below) |
| `/needle settings` | List every setting with its value and range |
| `/needle options` | Open NeedleDropper's page in the game's Settings panel |

## In-game options

Open them with the gear button in the NeedleDropper window's title bar, `/needle options`,
right-click on NeedleDropper in the add-on compartment, or *Game Menu → Options → AddOns →
NeedleDropper*. The Settings panel is searchable. Every option can also be changed with
`/needle set <setting> <value>` (on/off values: `on` or `off`).

The page starts with an **Open NeedleDropper** button and has a **Preview** button for the title
card and a **Send a test signal** button for the link. Your soundtracks are edited in the
NeedleDropper window, not here.

| Option | Setting | Default | Values | What it does |
|---|---|---|---|---|
| Enable needle drops | `enabled` | on | on / off | Play a song the first time you arrive somewhere |
| Zone arrival delay (s) | `dwellSec` | 10 | 0 – 300 | Seconds in a new zone before its song starts |
| Delay after discovery (s) | `minDwellSec` | 3 | 0 – 300 | Shorter delay after the game's "Discovered: …" message (also used for instances) |
| Landmark delay (s) | `landmarkDwellSec` | 4 | 0 – 300 | Seconds inside a landmark before its song starts (right away on discovery) |
| Cooldown between drops (min) | `cooldownMin` | 10 | 0 – 240 | Keeps drops rare |
| Exploration history | `historyMode` | respect | `respect` / `fresh` | *Respect*: places this character already explored count as heard, for the built-in soundtracks. Drops you add yourself always play on your next visit. *Fresh*: ignore the history |
| Play each drop once per | `scope` | character | `character` / `account` | Hear each song once per character, or once per account |
| Wait while on a flight path | `suppressOnTaxi` | on | on / off | Hold drops until you land |
| Instance drops | `instanceDrops` | on | on / off | Allow drops for dungeons and raids |
| Stop after combat lasting (s) | `combatFadeSec` | 5 | 0 – 120 | Fade out a playing drop when combat lasts longer than this (0 = never) |
| Length when unknown (s) | `maxDropSec` | 300 | 10 – 1800 | How long a drop counts as playing when its length is unknown |
| Zone music during a drop | `duckMethod` | silence | `silence` / `volume` / `none` | How the zone music is faded out: a silent track, music volume to 0, or not at all |
| Restart zone music afterwards | `kickZoneMusic` | on | on / off | Brings the zone music back right away after a drop |
| Play in-game files | `localPlayback` | on | on / off | Let WoW play drops that have an in-game audio file |
| Title card | `toast` | on | on / off | Show the place / artist / title card when a drop starts |
| Title card size | `toastScale` | 100% | 60% – 160% | Size of the card's text (`/needle set toastScale 120%`) |
| Title card position | `toastY` | 22% | 8% – 50% | How far down the screen the card sits, from the top |
| Title card time (s) | `toastSeconds` | 7 | 3 – 15 | How long the card stays fully visible |
| Pixel beacon | `beacon` | on | on / off | The colour strip in the top-left corner that the app reads |
| Beacon cell size (px) | `beaconCellPx` | 3 | 2 – 4 | Size of each beacon square in screen pixels. The strip is never taller than 9 pixels: up to 4 rows of 2 px, 3 rows of 3 px or 2 rows of 4 px. The app reads the size from the strip itself |
| (not in the panel) | `beaconCorner` | topleft | `topleft` / `topright` / `bottomleft` / `bottomright` | Advanced: the screen corner that holds the beacon. Leave it at `topleft`, where the app looks ([why](#the-beacon-overlaps-your-player-frame)) |
| Chat-log channel | `chatlog` | off | on / off | The [chat-log link](#chat-log-link) |
| Chat-log padding (bytes) | `chatlogPadBytes` | 50000 | 0 – 200000 | Hidden filler after important events, so WoW writes its chat log to disk (0 = off) |
| Debug messages | `debug` | off | on / off | Print diagnostic messages |

Updating from 1.1 keeps your settings; a beacon cell size of 4 (the old default) becomes 3.

## Key bindings

*Game Menu → Options → Keybindings*, section **NeedleDropper**, has three bindings: *Open or close
the NeedleDropper window*, *Add a needle drop where you stand* (the same as **Add drop here**) and
*Stop the needle drop*. None is bound until you choose a key.

## Companion settings

The app keeps its few settings on its Connect page (tray icon → **Open setup…**):

| Where | Setting | Default |
|---|---|---|
| **Spotify** card | Client ID, **Sign in** / **Disconnect**, **Play on** (the device that plays) | not set up |
| Footer | **Start with Windows** | on |
| Tray menu | **Pause needle drops**, **Start with Windows** | off, on |

Everything is saved in `%APPDATA%\NeedleDropper\config.json`. Which songs play where is never an app
setting: that is all in game.
