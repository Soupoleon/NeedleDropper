# NeedleDropper 1.2 — in-game check (about 15 minutes)

The automated tests run the addon's Lua without a game client. This check covers what only
World of Warcraft: Forever itself can show: its compression, how the beacon sits on a real screen,
the window's behaviour, editing with the app playing, and taint. Do it once per client patch, and
before every release. It is [DESIGN-v1.2.md](DESIGN-v1.2.md) section 6.4 as a checklist.

## Before you start WoW

1. **Install the 1.2 addon cleanly.** Delete the installed
   `C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns\NeedleDropper` folder (keep
   `NeedleDropper_Local`), then copy the repo's addon in:

   ```powershell
   powershell -ExecutionPolicy Bypass -File D:\NeedleDropper\tools\deploy.ps1
   ```

   The deploy script never deletes files, so without the clean delete 1.1's `Map.lua`, `Map.json`
   and `Protocol.lua` would stay behind (harmless, as the 1.2 TOC doesn't list them, but a clean
   folder is what players get).
2. **Put a test file in place:** copy any short `.mp3` to
   `...\_forever_\Interface\AddOns\NeedleDropper_Local\media\test.mp3`. It must be there before the
   game starts.
3. **Start the app from the repo**, before the game, so it creates `NeedleDropper_Companion`:

   ```powershell
   cd D:\NeedleDropper\companion
   ..\.venv\Scripts\python.exe -m needledropper
   ```

   The Connect page opens. Sign in to Spotify on its Spotify card if you want to test Spotify, and
   leave **Start with Windows** as you like.
4. **Start the link diagnostics** in a second terminal. It only reads the screen and the chat log,
   and plays nothing:

   ```powershell
   cd D:\NeedleDropper\companion
   ..\.venv\Scripts\python.exe -m needledropper diag --seconds 1800
   ```

5. **Launch WoW: Forever.** At character select, open **Menu → AddOns** and make sure
   **NeedleDropper** and **NeedleDropper Companion Data** are enabled. Log in, then type
   `/console scriptErrors 1` so Lua errors show.

If this account played 1.1, the first login also tests the upgrade: you should see *NeedleDropper
1.2: your soundtrack is Azeroth Mixtape. Type /needle to change a song or pick another soundtrack.*,
your old settings should be kept, and `/needle settings` should show `beaconCellPx = 3`.

## In game

| # | Do this | What should happen | Send back |
|---|---|---|---|
| 1 | **Share codes:** `/needle selftest share` | It decodes a code written by the Python side (*OK*), round-trips an export, and prints the first byte of the game's compressed output: `0x78` | The printed lines |
| 2 | **Beacon:** `/needle selftest beacon` | The largest song strip shows at the top-left for 10 seconds; chat prints the measured gap, rows, cell size and strip size, with no "overlaps your player frame" warning. The diag terminal prints the decoded `PING`, and the app shows its notification. Repeat with Windows display scaling at **100 %** and **125 %** (*Settings → System → Display → Scale*), and in the **Classic** and **Modern** Edit Mode layouts (*Game Menu → Edit Mode → Layout*) | The chat lines and diag's output for each of the four combinations; your screen resolution and UI scale |
| 3 | **Window:** `/needle`, then `/needle` again; left-click NeedleDropper in the add-on compartment, then right-click it, then hover over it; bind a key to *Open or close the NeedleDropper window* in *Options → Keybindings → NeedleDropper* and press it | The window opens and closes each time; right-click opens NeedleDropper's Settings page; hovering shows the status tooltip; the key opens and closes the window | Anything that didn't work |
| 4 | In the window, click into the search box, press **Escape** twice | The first Escape leaves the field, the second closes the window | Did it close? Did the Game Menu open instead? |
| 5 | With the window open, start a fight (or get pulled into one), finish it | The window hides at the start of combat and comes back afterwards | Did it come back? |
| 6 | Drag the window by its title bar, resize it from its corner, switch between **Drops**, **Soundtracks** and **Journal**; then `/reload` and `/needle` | Smooth; after the reload the window opens where you left it, at the same size and on the same tab | Screenshots of anything misaligned |
| 7 | **Editing,** in Goldshire: **Add drop here**, paste a Spotify song link and a YouTube link, ▶ **Test** | The app plays exactly that song (on Spotify if you signed in, otherwise on YouTube), with no `/reload` | Did the right song play, on which service? |
| 8 | Select a built-in drop (for example Elwynn Forest) → **Change song here**, type a new artist and title | **My Drops** appears at the top of the Soundtracks tab's list; the row shows *yours* | Anything odd |
| 9 | Delete a drop, then click **Undo** in the bar that appears | *Deleted <place>. [Undo]*, and Undo brings it back | Did it come back? |
| 10 | **Instance and file:** inside a dungeon, **Add drop here**, ▶ **Test**. Then type `test.mp3` into **In-game file** | The drop's place is the instance (`/needle where` shows `inst:<number>`); the file field says *found ✓*; Test plays the file in game | `/needle where` output; did the file play? |
| 11 | **Sharing:** Soundtracks tab → My Drops → **Share…** → Ctrl+C. Paste it into a Discord message between two lines of ```` ``` ```` (or let it become `message.txt`), copy it back from Discord, then **Import…** and paste | The preview shows the right name, drop count and places; **Import** adds a copy (*My Drops (2)*) and says *Imported … (off). [Turn on]*: it arrives turned off | The preview text; any error |
| 12 | **Taint:** `/console taintLog 1`, then play for a while (open and close the window, a fight, the Escape menu), log out, `/console taintLog 0` | `World of Warcraft\_forever_\Logs\taint.log` doesn't mention NeedleDropper | The lines that mention NeedleDropper, if any |

Optional extras: after 10 edits the footer offers **Save now (reloads UI)** (click it: the UI reloads
and your edits are still there); a drop with only an artist and title plays on Spotify, and after the
next login its form shows *Spotify match: … [Use this exact song]*.

Notes:

- Arriving somewhere new for the first time fires a real needle drop after about 10 seconds.
  `/needle stop` ends it.
- Nothing here sends input to the game or reads its memory. The app only looks at the small coloured
  strip in the top-left corner of the screen.
- Close the second terminal (diag) when you are done; quit the app from its tray icon.
