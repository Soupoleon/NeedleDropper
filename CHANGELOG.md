# Changelog

All notable changes to NeedleDropper (the addon and the companion app). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-11-05

Everything you do now happens in game. One NeedleDropper window replaces the browser control panel:
add a needle drop where you stand, paste a Spotify or YouTube link, test it, share the soundtrack with
a code. The app on your PC shrinks to a one-time Connect page and a tray icon. Update the addon and the
app together.

### Added

- **The NeedleDropper window**: `/needle` (or `/nd`), the add-on compartment or a key binding opens
  and closes it. Three tabs: **Drops**, **Soundtracks** and **Journal**. It moves, resizes, closes with
  Escape, hides during combat and comes back afterwards.
- **Add drop here** (a button, `/needle add [Artist - Title | link]` and a key binding): adds the place
  where you stand (a zone, a landmark, a dungeon or raid) and puts the cursor in Artist. Type the
  artist and title, and paste a Spotify song link or a YouTube link for the exact recording (a drop
  without artist and title is an unfinished draft that never plays on arrival; the form says so).
  **Test** plays any drop at once, even an unfinished one, without marking it as heard. **Delete** is
  instant, with **Undo**.
- **Drops tab**: **Showing:** (what plays from all active soundtracks, or one soundtrack), search,
  **Filter** (*Here*, *Not heard yet (this character)*, *Needs a song*, *Changed by you*, *In-game
  files*, *Turned off*), source letters **S Y F**, *heard* (with a tick), a right-click menu, and a form with
  place, artist, title, Spotify, YouTube, in-game file, start, length, volume, tags and note.
- **Azeroth Mixtape**, the new default soundtrack: 38 needle drops at the places every player
  remembers, half fitting and half funny. **Azeroth Unbound** (62 drops) stays built in as the optional
  Kojima-style score for every zone.
- **My Drops**: **Change song here** and **Turn off here** on any built-in drop write to a personal
  layer at the top of your list; **Revert to Azeroth Mixtape** brings the original back. Later fixes
  to the built-in soundtracks still reach every place you didn't change.
- **Your own soundtracks**: **New**, **Duplicate**, **Rename**, **Delete**, an **On** box for each
  and **Move up** / **Move down** to order them; the one higher in the list wins. **Use different soundtracks on this
  character** gives one character its own list.
- **Share codes** (`!NeedleDropper:1!…!`): **Share...** gives you a soundtrack as one piece of text for
  Discord or a forum; **Import...** shows a preview with warnings, never overwrites anything, and
  imports turned off. **Update <name>** for newer versions from the same author. Commands
  `/needle share [name]` and `/needle import`.
- **Journal tab** with **Replay**, **Play again next visit** and **Show drop**.
- **Spotify match**: after a Spotify search, the drop's form shows the song the app found
  (*Spotify match: …* with **Use this exact song**) at the next login.
- **Save now (reloads UI)** appears after 10 edits, so a game crash can't cost you a long session.
- **Settings panel**: an **Open NeedleDropper** button, **Send a test signal**, and *Fade zone music
  without the app* (`duckWithoutApp`, off by default).
- **Gamepad**: the window and its dialogs register with Forever's gamepad cursor, and text boxes are
  never focused by themselves while the gamepad UI is on. **Copy or move to...** under the form's
  **More...** offers the right-click menu's **Copy to** / **Move to**, which a gamepad can't reach.
- **Add-on compartment**: left-click opens the window, right-click opens the settings, hovering
  shows what is playing or waiting, your soundtracks and the app's status.
- **Key bindings** (*Options → Keybindings → NeedleDropper*): *Open or close the NeedleDropper
  window*, *Add a needle drop where you stand* and *Stop the needle drop*. None is bound by default.
- `/needle soundtracks`, `/needle journal print`, and `/needle selftest share`, which checks that
  share codes can be read and written on your client.
- **The Connect page** (`http://127.0.0.1:8322/`, opened on the app's first run): four cards,
  **World of Warcraft**, **Spotify**, **YouTube** and **Check the link** (*In WoW type /needle
  test*), plus **Start with Windows** and **Done**.
- **Tray icon** with a status line, **Open setup…**, **Pause needle drops**, **Start with Windows**,
  **Open log folder** and **Quit**, and notifications only when something matters.
- **Start with Windows**, ticked by default. The app starts quietly and waits until the game runs.
- An optional daily check for a new version, shown as a notification (nothing is installed without
  your click). It is off until an update address is set (`updates.releasesUrl` in `config.json`).
- `NeedleDropper.exe packs validate`, `encode`, `decode` and `convert-legacy` for soundtrack authors.
- **Moving from 1.1**: `NeedleDropper.exe packs convert-legacy` offers your 1.1 packs in game as
  share codes (*… ready to import: type /needle import to review them.* at the next login). Copy your
  1.1 audio files into `NeedleDropper_Local\media` yourself.
- The app keeps 10 backup copies of the addon's saved data in `%APPDATA%\NeedleDropper\backups`.

### Changed

- **`/needle` on its own opens the window** (it used to print the help: that is `/needle help`), and
  `/needle journal` opens the Journal tab (`/needle journal print` prints it in chat as before).
- **The song travels in the beacon.** The app no longer needs a soundtrack map: changes in game,
  even a test of an unfinished drop, reach it with no `/reload`. The beacon is smaller
  (96 × 3 pixels most of the time, at most 336 × 9 for a few seconds when a song starts), measures the
  space above your player frame, and the app reads its cell size by itself. *Beacon cell size* is now
  2 to 4 px (default 3; an old default of 4 becomes 3).
- **The top of the soundtrack list wins** (in 1.1 the later pack won).
- *Exploration history: Respect* now applies to the built-in soundtracks only: drops you add play on
  your next visit, even in zones you explored long ago.
- **Spotify search happens as you approach a place**, for songs without a Spotify link.
- **In-game audio files are the only local files**: WoW plays them from
  `Interface\AddOns\NeedleDropper_Local\media`, and the Drops tab tells you whether each file is found.
- The app runs without a console window. Its log moved to `%APPDATA%\NeedleDropper\logs`. Started
  from a command prompt (`--version`, `diag`, `packs`), it prints into that window.
- The app writes a small data addon, `NeedleDropper_Companion`, which tells the game at login whether
  the app and Spotify are connected.
- `/needle stop` keeps the place quiet until you move on to another zone, landmark or instance.
- Spotify sign-in also asks for the account's plan and country: the Connect page shows a real
  Premium check and the country. A 1.1 sign-in shows **Sign in again**.

### Fixed

Found on a live client before the release:

- The first signal after logging in could say the character was dead; the addon now reads combat,
  instance, flight and death only once the loading screen is gone.
- A Spotify or YouTube drop no longer fades the zone music when no 1.2 app has reported in (nothing
  would play it, so the result was silence); in-game files always fade it. The *Fade zone music
  without the app* option brings the old behaviour back.
- The app no longer latches onto a passing "World of Warcraft" window at game start: it wants the
  game's own window and checks it again every few seconds. Its capture switches to the slower
  window method sooner when the screen method sees nothing.
- Spotify searches failed with "Insufficient client scope" and were taken for a missing Premium. The
  sign-in asks for the missing permission now, and refusals are told apart (Premium needed, account
  not on the developer app's list, sign in again).
- A Spotify desktop app that accepts a song but never starts it (Spotify's Windows app on the test
  PC, from the Microsoft Store and from spotify.com alike; its Web Player works): the app sees it within
  about 1.5 seconds, plays the drop on YouTube, says *Spotify isn't
  starting songs — restart the Spotify app* once, and plays YouTube first for the rest of the session
  without sending Spotify anything more. As you approach the next places it checks Spotify quietly
  and uses it again once it plays; a new game session also gives it another chance. The game is told
  too, so its beacon keeps the YouTube video when only one link fits.
- The YouTube player window took the game's focus and started late. It is now made once, hidden,
  when the app starts (never while the game is in front), shown only while a YouTube song plays,
  always on top in the top-right corner (`deck.playerCorner`), never activated, and hidden again
  afterwards. The video is loaded, paused and silent, as you approach the place. The default
  browser is never used for playback any more.
- The player window did not start on the test PC: Edge was redirected to another program there, and
  the app now notices that and uses Chrome. A browser that exits at once is logged (its own messages
  go to `logs\browser.log`) and the other browser is tried at once; programs the app starts no longer
  inherit its packaging's environment.
- The Spotify app is never started (it can come in front) while the game is the foreground window.
- A Spotify sign-in is no longer lost when Windows refuses to replace the token file (seen when the
  app was started by a packaged host); the app keeps it in memory and saves it again later.
- A song that plays during a `/reload` keeps playing, and the zone music is faded again afterwards.
- Closing the app's console, logging off or shutting Windows down stops a playing Spotify drop and
  gives you your own music back, also while a song is just being started.

Found in the review of the window and the app:

- Editing the drop of the place where you stand (**Change song here**, a field, **Turn off here**,
  **Revert**, **Delete**, **Move to**) no longer makes it play in the middle of the edit with a
  half-typed song and then count it as heard: like **Add drop here**, the place waits for your next
  visit (**Test** plays it now).
- **Add drop here** and `/needle add` no longer fail with *You already have a soundtrack called My
  Drops* after you imported a friend's shared *My Drops*: yours becomes *My Drops (2)*.
- The *Deleted X. [Undo]* bar brings back that drop even after other edits (it used to undo the newest
  edit instead).
- One drag of the volume slider is one change (it was 20, which filled the undo history).
- Opening the window (or Import / Share) in combat waits until combat ends, as hiding it in combat
  always did; with the gamepad UI a window opened in combat could not be used or closed.
- Without the app, a first visit only shows the title card: such places are no longer counted as
  heard, so they play their song once the app is there (the chat says so once).
- The footer's buttons no longer overlap at the narrowest window, and the Drops tab no longer shows two
  **Stop** buttons.
- The beacon also measures the target frame when you target something.
- A damaged saved drop without an id no longer keeps the window from opening.
- **Zephras Isle** is in the form's zone list.
- While a **Test** plays, signals about other places no longer carry the test mark.
- A link pasted into **Artist** or **Title** moves into the Spotify or YouTube field.
- After a game crash, the app still stops the drop that played (the crashed session's numbering no
  longer hides the new session's signals), and it stops a drop when the game closes.
- A Spotify start that fails after Spotify accepted the song (a resend or a check that fails) no
  longer leaves Spotify playing under the next provider.
- The Connect page says *Restart World of Warcraft once* when the app made its data addon while the
  game was running, and warns when the app runs from a temporary folder (inside the zip), where
  **Start with Windows** stays off.

### Removed

- **The browser control panel** (Status, Music services, Soundtrack, Game & addon, Advanced). Its jobs
  moved into the game and onto the Connect page.
- **Rebuild map**, **Resolve soundtrack**, the `build-map` and `resolve` commands, and the addon's
  `Map.lua` and `Map.json`: no more `/reload` after a change.
- The app's own playback of audio files from `%APPDATA%\NeedleDropper\media`.
- The 1.1 beacon format: the 1.2 addon sends only the new one, so update the addon and the app
  together.

### Security

- Share codes are data only: never run as code, checked against strict limits, displayed with their
  text escaped, never overwriting a soundtrack and never turned on by themselves.
- The app never writes the game's saved data, and never writes into the `NeedleDropper` addon
  folder except when you click **Install addon** on the Connect page.

### Not yet verified on a live client

- Share codes rely on the game's built-in compression, which the automated tests can only imitate;
  `/needle selftest share` checks it on your client.
- The beacon's measured clearance above the player frame on unusual layouts and screen sizes;
  `/needle selftest beacon` prints the measurement.
- The window with Forever's gamepad UI, and the song surviving a `/reload`.
- The Spotify recovery for a desktop app that doesn't start songs (tested against a simulated
  Spotify only).
- The YouTube player window's focus behaviour and placement (tested against a simulated desktop):
  the game must stay the foreground window across the approach, the song and its end.
- The in-game check in `docs/FIRST-TEST.md` covers all of these, and the window itself, before
  release.

### Known issues

- The player window is made when the app starts: while it is missing (closed, or no Edge or Chrome),
  YouTube songs show their title card only; it is made again a few minutes later, when the game is
  not in front. Keeping it costs a few hundred MB of memory while the app runs.
- A YouTube song starts about half a second after the drop: the video is cued (loaded, not played)
  as you approach, which YouTube's player allows; starting it faster would mean playing it hidden.

## [1.1.0] - 2026-11-05

The first public release: a one-click Windows app, a friendly control panel, a new title card and
complete player documentation.

### Added

- **`NeedleDropper.exe`**, a one-file Windows app: no Python and no command line needed.
  Double-click it to start NeedleDropper and open the control panel; double-clicking it again while
  it runs just opens the panel.
- **Control panel** in your browser (`http://127.0.0.1:8322/`):
  - **Status**: game found / running, installed addon version (with a "type /reload" reminder when
    the game uses an old soundtrack map), the link to the game (beacon ✓ / ✗, last event, chat log)
    and what is playing now, with a Stop button.
  - **Music services**: Spotify, YouTube and Local files in the order they are tried, each with a
    switch and a **Test** button. Spotify is set up in three numbered steps (with a Copy button for
    the redirect URI), then offers a device list (*Play on*), *Fade in and out*, *Resume my music
    after a drop*, **Resolve soundtrack** (with progress) and **Disconnect**.
  - **Soundtrack**: packs on/off, add a pack, **Rebuild map**, and a searchable table of every drop
    with its sources and a ▶ test button.
  - **Game & addon**: World of Warcraft folder **Auto-detect** and **Install / update addon**.
  - **Advanced**: pixel and chat-log links, fades, player window behaviour, logs and config folders.
- **YouTube player window**: a small Edge or Chrome window that opens when a YouTube drop starts,
  stays visible while the song plays (as YouTube requires) and then shows "Drop finished".
- **A new title card** in the spirit of *Death Stranding*: a centred, letter-spaced place name in your
  game language, a thin line that grows from the centre, then `Artist · Title`. It fades in and out,
  hides with the rest of the UI (Alt+Z), and fades away early if combat starts. New options *Title
  card size*, *Title card height* and *Title card time*, and `/needle toast` to preview it where you
  stand (`/needle toast on|off` switches it).
- **Starter soundtrack**: official YouTube uploads and song lengths for its songs, and area numbers
  for its landmarks, so they trigger on every game language.
- **Release packaging**: MIT license, an addon zip for CurseForge, Wago and WoWInterface (with a
  `.pkgmeta` for the BigWigs packager), `tools/build_release.ps1` and SHA-256 checksums.
- **Player documentation**: install, music services, soundtrack packs, privacy, policies and
  troubleshooting guides.
- Companion settings `deck.autoOpen` (`youtube`, `always` or `never`), `deck.panelOnStart`
  (`first-run`, `always` or `never`), `deck.playerPosition`, and an on/off switch per music service.

### Changed

- **The in-game title card is now the only popup.** The companion no longer opens a window for every
  drop; only YouTube drops open the player window YouTube requires.
- The default order of the music services is now Spotify → Local files → YouTube.
- Older `deck.autoOpen` values are migrated (`true` becomes `youtube`, `false` becomes `never`).
- The chat-log link only accepts NeedleDropper lines that arrive as system messages, so other players
  can't fake a drop by typing in chat.

### Removed

- The Apple Music service. Apple Music is not supported in v1 (Windows offers no way to control it).
  `appleMusic` links in packs are ignored, and the old `applemusic` entry is dropped from existing
  settings.

### Security

- The control panel listens on `127.0.0.1` only, checks the `Host` and `Origin` of every request,
  requires a secret token that changes every run, limits request sizes and never returns Spotify
  tokens.

## [1.0.0] - 2026-11-05

Internal release, not published.

### Added

- **Addon**: songs the first time a character arrives in a zone, landmark or instance; arrival delays
  with a shortcut on discovery; waits for calm (combat, flight paths, death, cinematics, encounters);
  a global cooldown; once per character or per account; respects exploration history; journal,
  replay, play and test commands; zone music fading with crash-safe restore; in-game playback of
  your own audio files; title card; pixel beacon and chat-log link to the companion; self-tests;
  options panel and `/needle` commands.
- **Companion** (Python, standard library only): Spotify Premium playback through the Web API
  (PKCE login, device wake-up, fades, resume), local files and YouTube in a now-playing page, beacon
  reader, chat-log reader, soundtrack map generator and pack checker, and the `setup`,
  `spotify-login`, `install-addon`, `build-map`, `resolve`, `run`, `diag`, `test-drop`, `zones` and
  `validate` commands.
- **Starter soundtrack** "Azeroth Unbound".
