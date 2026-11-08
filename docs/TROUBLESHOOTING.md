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
without it. Reporting a problem? `/needle report` gives you a text to paste
([more](#where-the-logs-are)).

**Contents:** [Beacon not detected](#beacon-not-detected) · [Drops don't start](#drops-dont-start) ·
[Zone music](#zone-music-doesnt-come-back) · [Editing drops](#editing-drops-in-game) ·
[Share codes](#share-codes) · [Music services in game](#music-services-in-game) ·
[Spotify](#spotify) · [YouTube](#youtube) ·
[Chat-log link](#chat-log-link) · [The app](#the-needledropper-app) ·
[Installing the addon](#installing-the-addon) · [Logs](#where-the-logs-are) ·
[Command reference](#needle-command-reference) · [In-game options](#in-game-options) ·
[Key bindings](#key-bindings) · [Companion settings](#companion-settings)

## Beacon not detected

**Symptoms:** `/needle test` brings no notification, or the title card appears in the game but no
Spotify or YouTube song follows.

The app reads the **beacon**: a strip of coloured squares in the very top-left corner of the game
window. Most of the time it is a 96 × 3 pixel strip (144 × 3 once you choose a music setting in
game, and while a drop plays); when a song starts it grows to at most 336 × 9 pixels for a few
seconds, because it carries the song itself. A request from the game's Music services (Connect, a
test) shows a one-row strip of up to 336 × 3 pixels for a few seconds. (All sizes are for the
default 3 px squares.) Anything that hides or changes those pixels breaks the link.

1. **Is the app running?** Look for the NeedleDropper icon in the notification area of the taskbar
   (it may be under the **^** arrow). The app waits quietly until the World of Warcraft: Forever
   window exists and only then starts reading; give it a few seconds after the game opens.
   **Just updated?** The addon and the app must be the same version family: from 1.2.1 on, an older
   addon and a newer app (or the other way round) can't read each other's signals. The Connect page's
   World of Warcraft card then says *Addon too old*, the tray tooltip says *Update the NeedleDropper
   addon*, and in game the footer says *Update the NeedleDropper app*. Update the one that is older,
   then restart World of Warcraft.
2. **Run `/needle selftest beacon`.** It sends a test signal (the app shows its notification), shows
   the largest song strip for 10 seconds and prints the beacon's cell size, rows, strip size and the
   measured gap above your player frame. It waits while a song (or the `/needle test` card) is
   playing: type `/needle stop` first. Settings → NeedleDropper → **Send a test signal** does the
   same test without the big strip.
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
   bar and press Enter), run `start /wait NeedleDropper.exe diag` (the `start /wait` keeps its output
   in order), then type `/needle selftest beacon` in the game. `diag` prints every frame it decodes
   and plays nothing: it should show the `PING`, then the self-test's big strip as a `FIRE` of the
   drop `test` (marked as a test and as an in-game song, so the app doesn't play it either). It can
   run next to the app. Ctrl+C stops `diag`.

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
  NeedleDropper app looks for the beacon in the top-left corner.

## Drops don't start

`/needle status` usually says why: its `pending:` line lists what the drop is waiting for (arrival
time, combat, flight path, cooldown, another drop playing). The window's Drops tab shows each place's
state in words: *heard* (with a tick), *needs a song*, *off*, *overridden*.

**A landmark such as Zoram Strand does not fire after re-entering:** update both parts to beta 3
or newer. Older builds could save the whole zone when the client reported the landmark in its zone
text but left its sub-zone text empty. Stand inside the landmark, press **Use my location** again,
and check that the saved place is the landmark. Walk far enough out that `/needle where` reports
another location, then return and wait for the landmark delay. If both game location fields only
report Ashenvale, the addon cannot identify Zoram Strand by name at that position; use
`/needle where` and `/needle report` to report what the client exposes.

**Exploration history: Fresh does not mean every visit.** It ignores explored-map history; the
once-per-character/account rule and cooldown still apply. Use **Play again next visit** for a heard
drop, or **Replay** to hear it now. A drop saved while standing in its place waits until your next
arrival so unfinished edits cannot interrupt play.

| Reason | What to do |
|---|---|
| **This character already heard it.** Each place plays once per character | The Journal tab lists what you have heard; **Play again next visit** on a row lets that place play again, **Replay** plays it now |
| **You stopped a drop here.** After `/needle stop`, no needle drop starts at this place until you move on (`/needle status` says *quiet here until you move on*) | Leave the zone, landmark or instance and come back |
| **The place counts as explored.** With *Exploration history: Respect* (the default), places from the **built-in** soundtracks that your character explored before NeedleDropper count as heard. Drops you add yourself always play on your next visit | `/needle set historyMode fresh`, and `/needle reset char` to forget this character's drops |
| **Once per account.** With *Play each drop once per: Account*, another character already heard it | `/needle set scope character` |
| **Cooldown.** After a drop, the next one waits 10 minutes by default | Wait, or `/needle set cooldownMin 5` |
| **Arrival delay.** You must stay 10 s in a new zone (3 s after a "Discovered" message, 4 s in a landmark) | Stay a moment; the delays are options |
| **Not calm yet.** In combat, on a flight path, dead, in a cinematic or loading screen, or in a boss encounter | It starts by itself when that ends (as long as you stay there) |
| **No song for this place.** The Drops tab says *No needle drop for <place> yet* | Press **Add drop here**, then type the artist and title, and paste a Spotify or YouTube link if you have one ([how](PACKS.md#add-a-drop-where-you-stand)) |
| **The drop is a draft** (*needs a song*; the form says *Type the artist and title to finish this drop*) | Fill in **Artist** and **Title**: a drop without them never plays on arrival, even with a link (**Test** still plays it) |
| **You turned it off** (*off*), or a soundtrack higher in your list turned it off | Right-click the drop → **Turn on here**, or **Revert** it |
| **A soundtrack above has a song for this place** (*overridden*) | The higher one plays instead. Change the order on the Soundtracks tab ([priority](PACKS.md#which-song-plays-the-priority-order)) |
| **The soundtrack isn't on.** | Soundtracks tab: tick **On** next to it |
| **This character uses its own list.** *Use different soundtracks on this character* is ticked on the Soundtracks tab | Turn the soundtrack on in this character's list, or untick the box to use the account's list |
| **You added or edited it where you stand.** A drop you add with **Add drop here**, or change, turn off, delete or move while you stand at its place, doesn't start during that visit, so it can't interrupt your editing (with a half-typed song) | **Test** plays it now; it plays by itself on your next arrival |
| **Needle drops are switched off.** | `/needle set enabled on` (or the *Enable needle drops* option) |
| **Dungeons and raids.** | Instance drops need the *Instance drops* option on |

**The title card appears but no music plays:** the addon did its part.

- Is the app running, and does `/needle test` reach it? See [Beacon not detected](#beacon-not-detected).
- Places you visited before the app was installed (or seen by the game) showed their title card only;
  they play their song the first time you come back with the app running.
- Does the drop have a source? The letters next to it in the Drops tab show what it has: **S**
  Spotify, **Y** YouTube, **F** in-game file. A drop with only Artist and Title plays only through
  Spotify (the app searches for it), so without Spotify it stays silent.
- Did the app show a notification such as *song not found on Spotify*, *Spotify didn't start* or
  *YouTube video can't be embedded*? See [Spotify](#spotify) and [YouTube](#youtube).
- *Play music with* (Options → AddOns → NeedleDropper → Music services) is *Neither*, or *Spotify
  only* / *YouTube only* and the drop has nothing for that service: then only the title card shows.
- The footer says *Update the NeedleDropper app to 1.2.1 to play Spotify and YouTube songs*: the app
  on your PC is older than the addon, and a 1.2.0 app can't read a 1.2.1 addon's signals. Update it
  ([how](INSTALL.md#updating)).
- The zone music keeps playing under the title card, and `/needle status` says *card only (zone
  music left on)*: when the game started, NeedleDropper had no word from a 1.2.1 app (the footer says
  *NeedleDropper app not found*, or asks you to update it), so it doesn't fade the zone music for a
  song that nothing may play. Start the app before the game, or turn on *Fade zone music without the
  app* (`/needle set duckWithoutApp on`) if the app runs anyway.

**Music plays but there is no title card:** title cards may be off (`/needle toast on`), or the
game UI is hidden (Alt+Z hides the card with the rest of the UI). `/needle toast` shows a preview.

## Zone music doesn't come back

- After a drop the zone music should return within a few seconds. If it doesn't, try
  `/needle set kickZoneMusic on`, or `/needle set duckMethod volume`. `/needle selftest music` fades
  the music out for 8 seconds and back, to test this.
- If a drop's length is unknown, the game keeps the zone music faded for *Length when unknown*
  (5 minutes by default), even when the song is shorter. Set **Play for** under **More...** in the
  drop's form. (The built-in soundtracks already know every song's length.)
- If the game closed during a drop, NeedleDropper puts your sound settings back at the next login
  (it says so in the chat).

## Editing drops in game

| You see | What it means |
|---|---|
| *needs a song* | The drop is a draft: it has no Artist or Title yet, so it never plays |
| *That's an album or playlist link: in Spotify right-click the song > Share > Copy Song Link* (in chat after `/needle add`: *…right-click the song, then Share, then Copy Song Link.*) | Only links to single songs work. Copy the song's own link |
| *That's a playlist link: open the video itself and copy its link* (in chat: *That's a YouTube playlist link: open the video and copy its own link.*) | Use the video's own link (`youtube.com/watch?v=…`, `youtu.be/…`, `/shorts/`, `/embed/`, `/live/`, `music.youtube.com`), not a playlist or channel |
| *not found: copy it there and restart WoW (a /reload is not enough)* | The in-game file isn't in `Interface\AddOns\NeedleDropper_Local\media`, or it was copied while the game was running. WoW only notices new files when it starts |
| *Landmark saved by name: works on <language> clients only.* | NeedleDropper couldn't learn the landmark's area number, so it matches the name your game shows. Players with another game language won't trigger it. The number comes from the game's exploration map: stand inside the landmark (once your character has explored it) and press **Use my location** |
| The fields of a drop are greyed out, with *From Azeroth Mixtape (built-in)* | Built-in soundtracks are read-only. Click **Change song here** to make your own version in My Drops ([how](PACKS.md#change-a-song-from-a-built-in-soundtrack)) |
| **Save now (reloads UI)** in the footer | You made 10 or more changes since you logged in. WoW writes addon data to disk only when you log out, quit or reload, so a game crash would lose them. Click it to save now (the interface reloads; you stay in the game) |
| The window vanished in a fight | It hides during combat and comes back afterwards. Text you were typing is kept |
| *Deleted Goldshire.* with an **Undo** button | Deleting is instant; **Undo** brings the drop back for 15 seconds |

Limits: 50 soundtracks of your own, 300 drops in each, 3,000 in total.

Edits take effect at once, no `/reload`: the app learns the song from the beacon the next time
the drop plays (or when you press **Test**).

**With a gamepad:** the window and its dialogs register with Forever's gamepad cursor, so it can
reach the tabs, list rows, text boxes and buttons; text boxes are never focused by themselves while
the gamepad UI is on. The right-click menu of a row can't be reached with a gamepad, and its **Copy
to** and **Move to** exist only there. If the cursor can't reach something or gets stuck, please
report it.

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

An import never overwrites anything and arrives **turned off** (*Imported <name> (off).*, with a
**Turn on** button).
When you already have the same soundtrack from the same author and the code is newer, the dialog
offers **Update <name>** instead, which keeps its on/off state and its place in your list. More
in [Share codes](PACKS.md#share-codes).

## Music services in game

The **Music services** section of *Options → AddOns → NeedleDropper* and its dialog (`/needle music`)
send requests to the app through the beacon (or the chat-log link). The game learns the result only
when it reads the app's report again, at login or after **Refresh status** (which reloads the
interface); the Windows notifications tell you at once.

| You see | What it means | What to do |
|---|---|---|
| *Sent to the NeedleDropper app (…)*, then nothing for 10 seconds | The app didn't get the request: it isn't running, or it can't see the beacon | Check its tray icon, then **Send a test signal** (the app should show a notification). See [Beacon not detected](#beacon-not-detected) |
| Spotify's sign-in page doesn't show after **Connect Spotify** | Windows opened it behind the game (borderless games keep the front) | Press Alt+Tab, or click the app's *Spotify sign-in* notification: it brings the page forward |
| *Not sent: update the NeedleDropper app to 1.2.1 first.* | The app's last report came from an older version | [Update the app](INSTALL.md#updating), then **Refresh status** |
| *Not sent: install the NeedleDropper app first (see the addon page).* | The game has never seen a report from the app | [Install the app](INSTALL.md#3-optional-the-needledropper-app), start it before the game (or restart the game once) |
| *Just sent: give it a moment.* | The same button was pressed less than 10 seconds ago (the app takes each request at most once per 10 seconds) | Wait a moment |
| *A Client ID is 32 characters: digits and the letters a-f…* | The Client ID box holds something else, often the Client secret or a part of the ID | Copy the **Client ID** from your app's page in the [Spotify dashboard](https://developer.spotify.com/dashboard) again |
| Last request: *…the NeedleDropper app didn't receive this request…* | The app's report is newer than the request but doesn't mention it | Check the link with **Send a test signal**, then try again |
| Last request: *…no report from the NeedleDropper app since…* | The game hasn't read a report written after the request | Wait for the Windows notification, then press **Refresh status** |
| Notification: *NeedleDropper ignored a repeated request from the game* | The same request came too often (each at most once per 10 seconds; Connect and the YouTube sign-in at most 5 times in 10 minutes) | Wait a minute and try again |
| Notification: *NeedleDropper ignored a Disconnect for a different Spotify app* | The Disconnect named another Client ID than the app's | Use **Sign out of Spotify** on the Connect page |
| **Disconnect…** stays greyed out: *Disconnect it on the app's Connect page.* | The app's Client ID isn't a standard 32-character one, so the game can't name it | Use **Sign out of Spotify** on the Connect page |
| A test doesn't play (the notification and the *Last request* line say why) | For example *Needle drops are paused in the NeedleDropper app (tray menu): resume them to test.*, *A needle drop is playing. Try the test when it ends.*, or the app's player isn't running yet | Do what it says, then test again |
| The rows show an old state | They show the app's report from your login | Press **Refresh status** |

## Spotify

Setup steps and details: [Music services → Spotify](PROVIDERS.md#spotify-premium). NeedleDropper
plays Spotify songs in its own hidden player (Google Chrome needed), shown in Spotify's device lists
as **NeedleDropper**, unless you picked another device under **Play on**.

Keep **Play on: NeedleDropper (this app)** for the recommended setup. **Test Spotify** checks
Spotify alone; a failure never tries YouTube. **Test YouTube** checks YouTube alone.
**Send a test signal** and `/needle test` only check the link and play nothing.

To use the separate Spotify desktop app, open Spotify on your gaming PC, click **Refresh** beside
**Play on**, and select **Spotify desktop app — your PC's name** (just the device name in beta.1).
Choose **NeedleDropper (this app)** to switch back to NeedleDropper's own player.

**Device-selection safeguards in beta.2 and newer:** selecting a specific
Spotify device saves its ID. An offline or restricted device fails with recovery instructions;
NeedleDropper does not substitute another Spotify device. Click **Refresh** beside **Play on** and
select it again after opening Spotify there. IDs can change, and older name-only choices must
match exactly one device. Entries with duplicate names have extra identifying details.
**Automatic Spotify Connect device** can choose another PC or phone; beta.1 misleadingly labels
this **The Spotify app on this PC (automatic)**. For your gaming PC, keep **NeedleDropper (this app)**.

| Error or notification | Meaning | Fix |
|---|---|---|
| **403** (*Premium needed* on the Spotify card) | Spotify refused playback because of the subscription, or the developer-app owner no longer has Premium. New developer apps may not receive a plan in the profile response | Check that both the signed-in account and developer-app owner have Premium |
| *Account not allowed* on the Spotify card | The account isn't on your developer app's **User Management** list | [Add the account](PROVIDERS.md#signing-in-with-a-different-spotify-account-user-management) to your app |
| **Sign in again** on the Spotify card (*Needs attention*); *Spotify: sign in again* in game; the notification *Sign in to Spotify again* | Your sign-in comes from NeedleDropper 1.2.0 or older. Playing in the app needs two more permissions (`streaming`, `user-read-email`); a 1.1 sign-in also lacks the plan and country (Spotify then answers searches with "Insufficient client scope"). Meanwhile Spotify songs play on YouTube | Click **Sign in again** and approve. Your Client ID stays |
| *Spotify can't play in NeedleDropper's player: install Google Chrome, or wait a minute for Chrome's Widevine component* (in game: *Spotify needs Google Chrome*) | The app's Spotify player runs in Google Chrome and needs its Widevine component. Microsoft Edge can't run it | Install Google Chrome. If it is installed, start it once and wait a minute (it downloads Widevine), then quit and restart the NeedleDropper app |
| *NeedleDropper's Spotify player didn't get ready (it tries again by itself)…* (in game: *Spotify player not ready*) | The player runs in Chrome with Widevine, but Spotify's player in it never connected: the internet connection, Spotify's servers, or Widevine still downloading on a fresh Chrome | Check the connection and wait a minute: the app connects its player again by itself (at most once a minute). If it stays, quit the app and start it again |
| *NeedleDropper's player isn't running yet: it starts by itself when World of Warcraft isn't the window in front* (in game: *Spotify player not started yet*) | The player window is made only while the game is not in front (a new window takes the focus for a moment) | Start the app before the game, or switch away from the game for a moment: the app makes the player within a few minutes |
| *NeedleDropper's player is switched off (deck.autoOpen is "never" in config.json)* (in game: *Spotify player switched off*) | You switched the player window off | Set `deck.autoOpen` back to `youtube` in `config.json` and restart the app |
| *Spotify's player could not load (offline, or blocked by a firewall)* (in game: *Spotify player didn't load*) | Chrome couldn't load Spotify's player script from `sdk.scdn.co` | Check the connection; let Chrome reach `sdk.scdn.co` and `*.spotify.com` through your firewall or filter |
| In game only: *Spotify: NeedleDropper's player isn't ready (the app's Connect page says why)* | The app reports no player, without saying why (a build of the app from before the game learned the causes) | The Spotify card on the Connect page names the cause: see the rows above |
| Notification: *Spotify didn't start*; in game *Spotify: a song didn't start in NeedleDropper's player* | Spotify accepted a song, but the player did not start it. Your needle drops use YouTube until recovery if they have a video and your settings allow it; service tests do not fall back | Quit NeedleDropper from its tray, restart it from the desktop and try **Test Spotify** again. Send the relevant `Spotify` lines from `needledropper.log` privately if the failure repeats |
| *Spotify refused NeedleDropper's player even with a fresh sign-in* | Spotify's player rejected the sign-in twice | Sign in to Spotify again on the Connect page |
| Notification asking you to restart the Spotify app | **Play on** is another device: Spotify accepted the song but did not start it | Pick **NeedleDropper (this app)** under **Play on**. If you want the separate Spotify app, restart that app and play and pause a song once |
| **404**, **Device not found**, or the in-app player **went offline** | The device reported by the player is not reachable. NeedleDropper reloads the player page, checks the replacement against Spotify's device list and retries once | If it still fails, tray → **Quit**, start NeedleDropper again with WoW in the background, and **Test Spotify**. With another **Play on** device, open Spotify there, play and pause a song, click **Refresh**, then select it again |
| **429** / rate limited | Too many requests in a short time, or Spotify's `QUOTA_EXCEEDED` | Wait a few minutes; NeedleDropper already waits when Spotify asks it to |
| **INVALID_CLIENT: Invalid redirect URI** | The redirect URI in your developer app isn't exactly right | Set it to `http://127.0.0.1:8321/callback` (127.0.0.1, http, no `/` at the end) |
| **INVALID_CLIENT: Invalid client** | Wrong Client ID, or the developer app was deleted | Copy it again from the [dashboard](https://developer.spotify.com/dashboard) |
| Notification: Spotify sign-in expired | Spotify ended the login (password change, removed access, a long break) | Click the notification (or tray icon → **Open setup…**) and **Sign in** again on the Spotify card |
| Notification: song not found on Spotify | The drop has no Spotify link, and searching for its artist and title found nothing close enough | Paste the song's Spotify link into the drop (for a built-in drop: **Change song here** first). Meanwhile it plays on YouTube if it has a YouTube link |
| The wrong recording plays | The drop has no Spotify link and the search picked another version | Paste the exact song link. After the next login, the drop's form shows the match the app found (*Spotify match: …* with **Use this exact song**) so you can keep it with one click |
| The song starts on the wrong device | Automatic Spotify Connect selection can choose another PC or phone; beta.1 lacks the safeguards for a specific selection | Update to beta.2 or newer, then choose **NeedleDropper (this app)** for your gaming PC, or **Refresh** and select the intended Spotify device |
| Spotify is playing, but the wrong speakers or headphones are used | Windows has a separate output setting for the app playing the audio | In **Windows Settings → System → Sound → Volume mixer**, set **Google Chrome** to the wanted **Output device** for in-app playback, or **Spotify** for the desktop Spotify app; also check mute and volume |
| Your own Spotify music stops when a drop starts | One account plays on one device at a time: Spotify moves your playback to NeedleDropper's player | Normal. With *Resume my Spotify music afterwards* on (in game), it comes back afterwards. The Spotify Windows app may not restart by itself: press play there |
| A **NeedleDropper** device in the Spotify app's device list | The app's own player, while the app runs | Nothing to do. Picking it plays your own music through the app |

### Spotify desktop plays manually, but a test starts nothing

Check that Spotify desktop and the Connect page use the same account. Play a song manually in
Spotify, select **This computer** in Spotify's device menu, then pause it. On NeedleDropper's Connect
page, **Refresh** and reselect **Spotify desktop app — your PC's name**, then use **Test Spotify**.
If you have Spotify on another device, check whether it sees the desktop song while it plays
manually; that helps distinguish a Spotify Connect synchronization problem from a NeedleDropper test.
[Spotify's Connect guide](https://support.spotify.com/us/article/spotify-connect/) also covers restarting
and updating the desktop app.

A log saying Spotify accepted a play command while `item=None`, `is_playing=False` and
`progress_ms=0` means playback was not confirmed. It does not establish missing Premium, a bad
password, or which side failed to load the song. Share those lines privately with the target/observed
device details and the manual-play result. **Test Spotify** tests only Spotify. **Spotify only**
does not enable YouTube fallback. An Edge/Chrome error belongs to **NeedleDropper (this app)**;
desktop Spotify does not require Chrome.

If Spotify can't play a drop, the app falls back to the drop's YouTube video, if it has one (and
*Play music with* allows YouTube).

**Play on** selects a Spotify player. Speakers and headphones on that PC are selected through
Windows; see [Microsoft's app-audio instructions](https://support.microsoft.com/en-au/windows/hardware/audio/fix-app-audio-not-working-while-system-sounds-work-in-windows).

## YouTube

| Error | Meaning | Fix |
|---|---|---|
| **100** | The video was removed or made private | Paste another upload into the drop (for a built-in drop: **Change song here** first). With Spotify connected, the song plays there instead |
| **101** or **150** | The video's owner doesn't allow it to be played outside YouTube (embedding is off). YouTube also refuses many videos to players on `127.0.0.1`, so since 1.2.1 the box loads from `localhost` (in 1.2.0 most built-in videos failed with 150). The app also shows a notification, and after your next login the drop shows a warning in the window | Update to 1.2.1. A video that still fails: paste another official upload ([how to check one](PACKS.md#guidelines-for-shared-soundtracks)) |
| **153** | YouTube didn't learn which page is embedding the player (the "Referer" information was missing) | Use the player window NeedleDropper opens (its address is `http://localhost:8322/player`); turn off privacy extensions or settings that strip referrers for `localhost`; update Edge or Chrome |
| **2** | The video id is malformed | Paste the video's link again |
| **5** | The browser couldn't play the video | Update Edge or Chrome |

- **No sound:** click once inside the player window (*Click to enable sound*), and check that
  Edge or Chrome isn't muted in the Windows volume mixer.
- **The player window doesn't show:** it shows only while a YouTube song plays, on top of the game
  in the corner chosen in game (*YouTube box position*, top right by default). The app makes it when
  it starts, never while the game is in front (a new browser window takes the focus), and needs Edge
  or Chrome: it never uses a tab in your default browser. It runs in Chrome whenever Spotify plays in
  the app. If a tool on your PC redirects Edge to another browser ("Edge Deflector" and similar),
  NeedleDropper uses Chrome. The browser's own messages are in `logs\browser.log` (tray → **Open log
  folder**).
- **Ads** are normal for embedded YouTube videos; NeedleDropper doesn't block them. Signed in to
  YouTube Premium through **Sign in to YouTube** (Music services, or the Connect page), the box
  should play without them.
- **Sign in to YouTube opens nothing:** the app needs Edge or Chrome for its player box (*NeedleDropper
  has no Edge or Chrome for its YouTube box*), or the button was pressed too often.

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
  test signal arrived, the result of a request you sent from the game's Music services, Spotify
  sign-in expired or needs renewing (*Sign in to Spotify again*), Spotify didn't start or isn't
  starting songs, a song wasn't found on Spotify, a YouTube video can't be embedded, a part of the app
  stopped because of an error (details in the log), and (if the update check is on) a new version is
  available.
- **The player window** is made once, hidden, when the app starts. Spotify songs play in it while it
  stays hidden. It shows only while a YouTube song plays, on top of the game in the corner you chose
  (top right by default), and never takes the focus from the game; afterwards it hides again.
- **Commands in a terminal** (`NeedleDropper.exe --version`, `diag`, `packs …`) print into the
  window you started them from. Windows doesn't wait for the app, so the prompt can come back before
  the output: in `cmd` use `start /wait NeedleDropper.exe diag`, in PowerShell add `| Out-Host`
  (for example `NeedleDropper.exe --version | Out-Host`).
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

**Reporting a problem?** Type `/needle report` in the game: a window opens with a text report,
already selected. Press Ctrl+C and paste it into your report. It has the addon, game and app
versions, what the app reported (Spotify and YouTube), the beacon, the settings that matter, your
last 5 drops and the last error, and no account names, Client IDs or tokens. Add the output of
`/needle status` and the relevant part of `needledropper.log`. The log never contains passwords or
tokens, but file paths in it can show your Windows user name, and it names your Spotify account
(remove those details before posting publicly, or send the log privately). A share code of the
soundtrack involved helps too. Never post an unedited log in a public GitHub issue.

## /needle command reference

Type the commands in the game's chat box. `/nd` is a short form of `/needle`. `/needle` on its own
opens or closes the NeedleDropper window. For `on|off` commands, leaving out the word switches the
setting.

| Command | What it does |
|---|---|
| `/needle` | Open or close the NeedleDropper window |
| `/needle help` | List every command in the chat window |
| `/needle add [Artist - Title \| link]` | **Add drop here**: adds the place where you stand to My Drops (or to the soundtrack the Drops tab is showing) and opens it in the window, cursor in Artist. If the place already has a drop, that drop opens instead. With text it also saves the song: `/needle add Toto - Africa` prints *Added Goldshire: Toto · Africa to My Drops.* (a built-in drop there is overridden in My Drops); with only a link (`/needle add https://youtu.be/QAo_Ycocl1E`) it saves the link, and you type or check the artist and title in the window |
| `/needle soundtracks` | Open the window on the Soundtracks tab |
| `/needle journal` | Open the window on the Journal tab |
| `/needle journal print [count]` | Print the songs this character has heard in the chat window instead (the last 15, or `count`) |
| `/needle import` | Open the Import dialog to paste a share code |
| `/needle share [name]` | Open the Share dialog for one of your soundtracks, by name: `/needle share My Drops` |
| `/needle music [status\|connect [Client ID]\|disconnect\|test spotify\|test youtube\|youtube]` | On its own, open the **Music services** dialog (Spotify, YouTube and how songs play). `status` prints the app, Spotify, YouTube and last-request lines; `connect` asks the NeedleDropper app to sign in to Spotify (with a Client ID, it is stored first: `/needle music connect 0123…cdef`); `disconnect` signs the app out of Spotify; `test spotify` / `test youtube` play 15 seconds of a test song; `youtube` opens YouTube's sign-in page in the player's browser |
| `/needle status` | Settings summary, active soundtracks, beacon and chat-log link, where you are, the pending drop and what it waits for, what is playing, cooldown, how many drops you have heard |
| `/needle where` | Where you stand, for soundtrack authors: zone key and map number, subzone and its by-name id, instance id, the area numbers here, and the drops that match |
| `/needle replay [id]` | Replay the last drop you heard, or the one with that id. Doesn't change your history |
| `/needle play <id or place>` | Play any drop now, by id or place name: `/needle play zone:1429` or `/needle play Elwynn Forest`. Doesn't mark it as heard |
| `/needle test` | Show the test title card (for 30 seconds) and send a test signal: the app shows a notification and ticks **Check the link** on its Connect page. Nothing plays and the zone music isn't faded; `/needle stop` ends the card |
| `/needle toast [on\|off]` | Preview the title card for where you stand; with `on` / `off`, turn title cards on or off |
| `/needle stop` | Stop the song that is playing (it stays marked as heard). With nothing playing, let the pending drop go for now: it comes back on your next visit. Either way no needle drop starts at this place until you move on to another zone, landmark or instance |
| `/needle skip` | Stop the song and keep it marked as heard. With nothing playing, mark the pending drop as heard so it never plays |
| `/needle reset char\|account\|all\|settings` | Forget this character's drops, journal and cooldown (`char`), the account-wide drops (`account`), both (`all`), or put every setting back to its default (`settings`). Your soundtracks are never touched |
| `/needle beacon on\|off` | The pixel beacon the app reads |
| `/needle chatlog on\|off` | The [chat-log link](#chat-log-link) (turns WoW's chat logging on) |
| `/needle selftest [beacon\|chatlog\|discovery\|music\|share]` | Check your setup: `beacon` (default) sends a test signal, shows the largest beacon for 10 seconds and prints the beacon's size and the gap above your player frame (not while a song or the test card plays); `chatlog` tests the chat-log link; `discovery` prints every discovery message, exploration update and zone change for 120 seconds; `music` fades the zone music out for 8 seconds and back; `share` checks that share codes can be read and written on your client |
| `/needle report` | A text report of your setup for a bug report (versions, the app's report, the beacon, the settings that matter, the last 5 drops, the last error), selected in a window: press Ctrl+C. It holds no account names, Client IDs or tokens |
| `/needle debug on\|off` | Diagnostic messages in the chat window |
| `/needle set <setting> <value>` | Change a setting, for example `/needle set cooldownMin 15` (names below) |
| `/needle settings` | List every setting with its value and range |
| `/needle options` | Open NeedleDropper's page in the game's Settings panel |

## In-game options

Open them with the gear button in the NeedleDropper window's title bar, `/needle options`,
right-click on NeedleDropper in the add-on compartment, or *Game Menu → Options → AddOns →
NeedleDropper*. The Settings panel is searchable. Every option can also be changed with
`/needle set <setting> <value>` (on/off values: `on` or `off`).

The page starts with an **Open NeedleDropper** button, then groups the options under *Needle drops*,
*Music services* (the app's status, the Spotify and YouTube accounts, the music options below,
**Test Spotify**, **Test YouTube** and **Refresh status**), *Playback*, *Title card* (with a **Preview**
button), *Companion link* (with a **Send a test signal** button) and *Troubleshooting*. Your
soundtracks are edited in the NeedleDropper window, not here.

| Option | Setting | Default | Values | What it does |
|---|---|---|---|---|
| Enable needle drops | `enabled` | on | on / off | Play a song the first time you arrive somewhere |
| Show minimap icon | `minimapIcon` | on | on / off | Left-click to open the addon, right-click for settings; drag to reposition |
| Zone arrival delay (s) | `dwellSec` | 10 | 0 – 300 | Seconds in a new zone before its song starts |
| Delay after discovery (s) | `minDwellSec` | 3 | 0 – 300 | Shorter delay after the game's "Discovered: …" message (also used for instances) |
| Landmark delay (s) | `landmarkDwellSec` | 4 | 0 – 300 | Seconds inside a landmark before its song starts (right away on discovery) |
| Cooldown between drops (min) | `cooldownMin` | 10 | 0 – 240 | Keeps drops rare |
| Exploration history | `historyMode` | respect | `respect` / `fresh` | *Respect*: places this character already explored count as heard, for the built-in soundtracks. Drops you add yourself always play on your next visit. *Fresh*: ignore the history |
| Play each drop once per | `scope` | character | `character` / `account` | Hear each song once per character, or once per account |
| Wait while on a flight path | `suppressOnTaxi` | on | on / off | Hold drops until you land |
| Instance drops | `instanceDrops` | on | on / off | Allow drops for dungeons and raids |
| Play music with | `musicOrder` | spotify-youtube | `spotify-youtube` / `youtube-spotify` / `spotify` / `youtube` / `none` | Which service the NeedleDropper app uses for a song, and which it tries next. `none` (*Neither*): title cards and in-game files only |
| Music volume | `musicVolume` | 100 | 0 – 100 | Percentage for the next companion song or service test, multiplied by the drop's volume. In-game files use WoW's Music volume |
| YouTube box position | `youtubeCorner` | topright | `topright` / `topleft` / `bottomright` / `bottomleft` | Where the small YouTube player box shows while a YouTube song plays. A top-left box sits below the beacon; a change moves an open box at once |
| Fade songs in and out | `musicFade` | on | on / off | Off: songs start and stop at once |
| Resume my Spotify music afterwards | `resumeMyMusic` | on | on / off | When a Spotify needle drop ends, Spotify goes back to what you were listening to |
| Stop after combat lasting (s) | `combatFadeSec` | 5 | 0 – 120 | Fade out a playing drop when combat lasts longer than this (0 = never) |
| Length when unknown (s) | `maxDropSec` | 300 | 10 – 1800 | How long a drop counts as playing when its length is unknown |
| Zone music during a drop | `duckMethod` | silence | `silence` / `volume` / `none` | How the zone music is faded out: a silent track, music volume to 0, or not at all |
| Restart zone music afterwards | `kickZoneMusic` | on | on / off | Brings the zone music back right away after a drop |
| Fade zone music without the app | `duckWithoutApp` | off | on / off | Also fade the zone music for Spotify and YouTube songs when the NeedleDropper app has never reported in. Off: such drops show their title card and the zone music keeps playing, because nothing would play the song |
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
| **Spotify** card | Client ID, **Sign in** / **Sign out of Spotify**, **Play on** (*NeedleDropper (this app)*, or another Spotify device), **Test Spotify** | not set up; *NeedleDropper (this app)* |
| **YouTube** card | **Play songs from YouTube**, **Test YouTube**, **Sign in to YouTube** | on |
| Footer | **Start with Windows** | on |
| Tray menu | **Pause needle drops**, **Start with Windows** | off, on |

Everything is saved in `%APPDATA%\NeedleDropper\config.json`. Which songs play where is never an app
setting: that is all in game. *Play music with*, the YouTube box position, the fades and *Resume my
Spotify music afterwards* are set in game too (Music services); the Connect page shows them.
