# NeedleDropper 1.2 — in-game check

The automated tests run the addon's Lua without a game client. This check covers what only
World of Warcraft: Forever itself can show: the route through Durotar end to end, the game's
compression, how the beacon sits on a real screen, the window with mouse and gamepad, editing with
the app playing, and taint. Do it once per client patch, and before every release. It is
[DESIGN-v1.2.md](DESIGN-v1.2.md) section 6.4 as a checklist, plus the points the
[real-client run of 2026-11-05](review/real-client-2026-11-05.md) left open.

## Before you start WoW

1. **Install the 1.2 addon.** Close WoW first (the deploy script refuses while `Wow.exe` runs),
   then copy the repo's addon in:

   ```powershell
   powershell -ExecutionPolicy Bypass -File D:\NeedleDropper\tools\deploy.ps1
   ```

   It copies `addon\NeedleDropper` (its `Sets\` and `UI\` folders included) and deletes the three
   files 1.2 retired, `Map.lua`, `Map.json` and `Protocol.lua`, from the old copy. It never touches
   `NeedleDropper_Local` or `NeedleDropper_Companion`. (Players get the same folder from
   `dist\NeedleDropper-1.2.0-addon.zip`.)
2. **Put a test file in place:** copy any short `.mp3` to
   `...\_forever_\Interface\AddOns\NeedleDropper_Local\media\test.mp3`. It must be there before the
   game starts.
3. **Stop tonight's stand-in player** (`scratchpad\poc\poc_player.py`) if it still runs: with two
   readers on the beacon, every song would play twice.
4. **Start the 1.2 app before the game**, so the game reads its 1.2 status at login:

   ```powershell
   cd D:\NeedleDropper\companion
   ..\.venv\Scripts\python.exe -m needledropper
   ```

   (or `D:\NeedleDropper\dist\NeedleDropper.exe`). It goes to the tray; the Connect page opens on the
   first run (tray icon → **Open setup…** later). To test Spotify, paste your Client ID on the
   **Spotify** card and **Sign in**: the card should then show your account, *✓ Yes: this account has
   Spotify Premium.* and your country. A sign-in kept from 1.1 shows **Sign in again** instead: click
   it, because 1.2 asks Spotify for one more permission (the plan and country, real-client finding 3).
5. **Start the link diagnostics** in a second terminal. It only reads the screen and the chat log,
   and plays nothing:

   ```powershell
   cd D:\NeedleDropper\companion
   ..\.venv\Scripts\python.exe -m needledropper diag --seconds 1800
   ```

   (With the built exe, run `start /wait NeedleDropper.exe diag --seconds 1800` in a `cmd` window
   so its output stays in order.)
6. **Launch WoW: Forever.** At character select, open **Menu → AddOns** and make sure
   **NeedleDropper** and **NeedleDropper Companion Data** are enabled. Log in, then type
   `/console scriptErrors 1` so Lua errors show.

The first login with 1.2 also tests the upgrade: chat says *NeedleDropper 1.2: your soundtrack is
Azeroth Mixtape. Type /needle to change a song or pick another soundtrack.*, your old settings are
kept, and `/needle settings` shows `beaconCellPx = 3`.

## First check: the troll in Durotar (about 10 minutes)

The route of the 1.1 run, on the same level-6 troll: Durotar, Razor Hill, Orgrimmar. Azeroth
Mixtape, the 1.2 default, has a song for Durotar (Dolly Parton · *9 to 5*) and one for Orgrimmar
(Hans Zimmer & Lisa Gerrard · *Now We Are Free*) but none for Razor Hill, so Razor Hill also tests
**Add drop here**. `tests/test_addon_e2e_durotar.py` drives the same route without a client.

This character heard Durotar and Razor Hill on 1.1 (1.2 keeps that history), a level-6 character
has explored Durotar, and drops wait 10 minutes for each other, so step D2 lifts all three:

```
/needle set cooldownMin 0
/needle set historyMode fresh
/needle reset char
```

`reset char` forgets this character's heard places (including the explored ones NeedleDropper
marked at login), journal and cooldown; `historyMode fresh` keeps it from marking them again. (A new
level-1 troll in the Valley of Trials needs only the first line.)

| # | Do this | What should happen | Send back |
|---|---|---|---|
| D1 | Log in on the troll in Razor Hill, then `/needle status`, and look at diag | `soundtracks: Azeroth Mixtape - 38 drops`; `transports: beacon on (3 px cells, 96x3, …)`; `app: App seen … before login · …`. (*Update the NeedleDropper app to 1.2…* means the game read the old app's data: quit WoW, start the 1.2 app, start WoW again.) In diag, the first `HELLO` after the loading screen says `flags=0x00`, not `0x08` (DEAD, real-client finding 1) | The `/needle status` lines and the first `HELLO` line |
| D2 | Still in Razor Hill, type the three lines above | Within a few seconds of `reset char`, Durotar's drop starts (it is the zone's drop, and Razor Hill has none yet): the zone music fades, the card shows `D U R O T A R` and *Dolly Parton · 9 to 5*, and the app plays it. diag: `FIRE … id=zone:1411 youtube=d_vPJjqWxZM song='Dolly Parton'/'9 to 5' len=165.5s …`, then `HELLO … flags=0x20` (DROP_ACTIVE) while it plays. `/needle stop` ends it, and chat says *stopped Durotar.* (nothing else starts here until you move on) | Which service played it; seconds from the card to the music; did the game keep the focus? |
| D3 | `/needle where` | `subzone: Razor Hill  (id by name: sub:1411:razor-hill)`, `explored areas here:` with `362 Razor Hill`, `matching drops: zone:1411 "Durotar" (heard)` | The output |
| D4 | `/needle` (it opens on Durotar's drop: *From Azeroth Mixtape (built-in)*). Press **Add drop here**, type *ZZ Top* and *Sharp Dressed Man*, paste the song's Spotify link and a YouTube link, press **Test** | A new drop for Razor Hill in **My Drops** (*needs a song* until Artist and Title are in), the cursor starts in **Artist**; the Spotify field says *Spotify song* with a tick; **Test** shows the Razor Hill card and the app plays exactly that song, with no `/reload`. The Soundtracks tab lists **My Drops**, turned on | Did the right recording play, on which service? |
| D5 | Walk out of Razor Hill, then back in | About 4 seconds after you are back (landmark delay), the zone music fades and your song starts with `R A Z O R   H I L L`. (A drop added where you stand never starts during that visit.) diag: `ARM … id=area:362` with the song, then `FIRE … id=area:362` | diag's ARM and FIRE lines |
| D6 | Ride north to Orgrimmar | About 10 seconds after you pass the gate, *Now We Are Free* starts with the `O R G R I M M A R` card | Seconds from the card to the music |
| D7 | While the Orgrimmar song plays: `/reload` | The song keeps playing in the app, the zone music is faded again once the interface is back, `/needle status` says `playing: Orgrimmar - …`, and diag shows `HELLO … flags=0x20` after the reload, never a plain `HELLO … flags=0x00` with the song stopping 5 seconds later (finding ND-DIR-01) | Did the song survive the reload? Did the zone music come through? |
| D8 | `/needle journal` | The Journal tab lists Orgrimmar, Razor Hill and Durotar, newest first, each with its song. **Replay** on Razor Hill plays it again as a test (diag: `FIRE … flags=0x40`) and changes nothing | Anything missing |
| D9 | Put the rules back: `/needle set cooldownMin 10` and `/needle set historyMode respect` (or `/needle reset settings`) | `cooldownMin = 10`, `historyMode = respect` | |

If the 1.2 app had never reported in (no `NeedleDropper_Companion` data at login), the drops in D2,
D5 and D6 show their card but leave the zone music playing, and `/needle status` says `card only
(zone music left on)`: nothing would play the song (real-client finding 6; the option *Fade zone
music without the app* changes it).

## In game

| # | Do this | What should happen | Send back |
|---|---|---|---|
| 1 | **Share codes:** `/needle selftest share` | *share self-test: CompressString's first byte is 0x78 (OK, zlib).*, *… a code made by the NeedleDropper app's encoder decodes: OK.*, *… export and import round trip: OK (N characters).* and *share self-test passed: share codes work on this client.* | The printed lines |
| 2 | **Beacon:** `/needle selftest beacon` (with nothing playing: during a song or the 30-second `/needle test` card it answers *a drop is playing; /needle stop it first, then run the beacon test again.*) | The largest song strip shows at the top-left for 10 seconds, at most 336 × 9 pixels; chat prints the measured gap above your player frame, the cell size, the rows and the strip size, then *the strip clears your player frame.* (no "overlaps your player frame" warning). diag prints `PING seq=N flags=0x40`, then the test strip, `FIRE seq=N flags=0x50 … id=test …` (test, plays nothing), and the app shows its notification. Repeat with Windows display scaling at **100 %** and **125 %** (*Settings → System → Display → Scale*), and in the **Classic** and **Modern** Edit Mode layouts (*Game Menu → Edit Mode → Layout*) | The chat lines and diag's output for each of the four combinations; a screenshot of the top-left corner; your screen resolution and UI scale |
| 3 | **Window:** `/needle`, then `/needle` again; left-click NeedleDropper in the add-on compartment, then right-click it, then hover over it; bind a key to *Open or close the NeedleDropper window* in *Options → Keybindings → NeedleDropper* and press it | The window opens and closes each time; right-click opens NeedleDropper's Settings page; hovering shows the status tooltip (what plays or waits, the soundtracks, the app status, *Left-click: open · Right-click: settings*); the key opens and closes the window | Anything that didn't work |
| 4 | In the window, click into the search box, press **Escape** twice | The first Escape leaves the field, the second closes the window | Did it close? Did the Game Menu open instead? |
| 5 | With the window open, start a fight (or get pulled into one), finish it | The window hides at the start of combat and comes back afterwards | Did it come back? |
| 6 | Drag the window by its title bar, resize it from its corner, switch between **Drops**, **Soundtracks** and **Journal**; then `/reload` and `/needle` | Smooth; after the reload the window opens where you left it, at the same size and on the same tab | Screenshots of anything misaligned |
| 7 | **Gamepad** (Forever's gamepad UI on): open the window with `/needle`, the compartment and the key binding; move with the D-pad; press A on a text box, then B | The gamepad cursor lands on the window and reaches the tabs, the list rows and the form's boxes and buttons; A focuses a box and B (or Escape) leaves it without trapping the cursor; the Share and Import dialogs work the same way. The right-click menu can't be reached with a gamepad, so its **Copy to** / **Move to** are also under the form's **More...** as **Copy or move to...**: open it with A | Anything the cursor can't reach or gets stuck on |
| 8 | **Gamepad and combat:** with the window open and the gamepad UI on, get pulled into a fight; then, still in the fight, press the key binding (or type `/needle`) | The window hides; no *Interface action failed* message; in the fight the window stays closed and chat says *NeedleDropper opens when combat ends.*; after the fight it opens, and the gamepad cursor reaches it | Any error text |
| 9 | **Change a built-in drop,** in Goldshire: **Add drop here** selects Mixtape's Goldshire drop (*From Azeroth Mixtape (built-in)*). Click **Change song here**, type a new artist and title, paste a Spotify song link and a YouTube link, **Test** | The app plays exactly that song (on Spotify if you signed in, otherwise on YouTube), with no `/reload`; **My Drops** sits at the top of the Soundtracks tab's list and the row shows *yours*; **Revert to Azeroth Mixtape** brings *Hotel California* back | Did the right song play, on which service? |
| 10 | Delete one of your drops, then click **Undo** in the bar that appears | *Deleted <place>.* with **Undo**, which brings it back (for 15 seconds) | Did it come back? |
| 11 | **Instance and file:** inside a dungeon, **Add drop here**, **Test**. Then type `test.mp3` into **In-game file** | The drop's place is the instance (`/needle where` shows `inst:<number>`); the file field says *found* with a tick; **Test** plays the file in game | `/needle where` output; did the file play? |
| 12 | **Sharing:** Soundtracks tab → My Drops → **Share...** → Ctrl+C. Paste it into a Discord message between two lines of ```` ``` ```` (or let it become `message.txt`), copy it back from Discord, then **Import...** and paste | The preview shows the right name, drop count and places; **Import** adds a copy (*My Drops (2)*) and says *Imported … (off).* with a **Turn on** button: it arrives turned off | The preview text; any error |
| 13 | **Spotify that won't start** (only with Spotify connected; this PC's desktop app was stuck tonight): **Test** a drop that has a Spotify link and a YouTube link, then a second one | Either Spotify plays it, or within about 2 seconds the song plays on YouTube instead and the tray shows *Spotify isn't starting songs — restart the Spotify app* (once). The second **Test** plays on YouTube straight away: the log says *Spotify isn't starting songs this session: YouTube plays*. After restarting the Spotify app (play and pause any song once), or with Spotify's Web Player picked under **Play on**, the next place you approach plays on Spotify again | The notification and the matching lines of `needledropper.log` (tray → **Open log folder**) |
| 14 | **Focus:** start the app while the game is closed (or alt-tab out first); the YouTube player window appears briefly in the top-right corner and hides. Then in game, whenever a YouTube song starts and ends, watch the game window | The player shows on top in the top-right corner of the game's screen while the song plays and hides afterwards; the game keeps the keyboard and mouse the whole time (no alt-tab, no taskbar flash, no browser tab). The log names the browser (`opened the player window (chrome: …)` on this PC, where Edge is redirected) and *player window ready* | Did the game lose focus, and when? Seconds from the card to the sound |
| 15 | **Taint:** `/console taintLog 1`, then play for a while (open and close the window, a fight, the Escape menu, the gamepad UI), log out, `/console taintLog 0` | `World of Warcraft\_forever_\Logs\taint.log` doesn't mention NeedleDropper | The lines that mention NeedleDropper, if any |

Optional extras: after 10 edits the footer offers **Save now (reloads UI)** (click it: the UI reloads
and your edits are still there); a drop with only an artist and title plays on Spotify, and after the
next login its form shows *Spotify match: … · … (m:ss)* with **Use this exact song**; Settings →
NeedleDropper → **Send a test signal** makes the app show its notification within 2 seconds.

Notes:

- Arriving somewhere new for the first time fires a real needle drop after about 10 seconds
  (4 seconds in a landmark, 3 after a *Discovered* message). `/needle stop` ends it, and keeps that
  place quiet until you move on; `/needle skip` ends it and keeps it marked as heard.
- Nothing here sends input to the game or reads its memory. The app only looks at the small coloured
  strip in the top-left corner of the screen.
- Close the second terminal (diag) when you are done; quit the app from its tray icon.
