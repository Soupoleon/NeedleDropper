# Changelog

All notable changes to NeedleDropper (the addon and the companion app). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [1.2.1-beta.2] - 2026-11-06

The addon and app keep the compatible 1.2.1 version. Update the companion app to get these fixes.

### Fixed

- A specific **Play on** choice is saved by Spotify device ID. If that device is offline or
  restricted, playback fails with **Refresh** and reselect instructions instead of switching to
  another Spotify device. Duplicate names are distinguished in the selector; older name-only
  settings require one exact match. Refresh and reselect if Spotify changes a device's ID.

### Changed

- **Play on** makes the choice between **NeedleDropper (this app)** and the separate Spotify
  desktop app clearer. Open Spotify, click **Refresh**, and select **Spotify desktop app — your
  PC's name**; choose NeedleDropper again to switch back.
- Renamed the automatic option to **Automatic Spotify Connect device**: it can choose another PC
  or phone. **NeedleDropper (this app)** remains the recommended default for the gaming PC.
- Explained speaker/headphone selection through Windows **Sound → Volume mixer**: choose the
  output for **Google Chrome** during in-app Spotify playback, or **Spotify** for its desktop app.

## [1.2.1] - 2026-11-06

Spotify now plays **inside the NeedleDropper app**: no Spotify window, and no Spotify app has to be
open. Your music services are set up in game, in a new **Music services** section of the addon's
settings. **Update the addon and the app together**: a 1.2.0 app can't read the signals of a 1.2.1
addon, and a 1.2.1 app can't read a 1.2.0 addon's. After the update, **sign in to Spotify once
more**: in-app playback needs two more Spotify permissions.

### Added

- **Spotify in the app.** The app plays Spotify songs itself, through Spotify's official Web
  Playback SDK, in the same hidden player window as the YouTube box. While the app runs, Spotify lists
  it as a device called **NeedleDropper**. It needs **Spotify Premium** and **Google Chrome** (the SDK
  needs Chrome's Widevine component; the app copies it into its player's own profile). Nothing shows
  on screen while a Spotify song plays, and the game keeps the focus. Fades happen in the app's
  player, and with *Resume my Spotify music afterwards* the music you were playing on another device
  comes back afterwards.
- **Music services in the game's settings** (*Options → AddOns → NeedleDropper*, between *Needle
  drops* and *Playback*): the app's status, **Spotify account**, **YouTube account**, *Play music
  with*, *YouTube box position*, *Fade songs in and out*, *Resume my Spotify music afterwards*,
  **Test Spotify**, **Test YouTube** and **Refresh status**. The settings are searchable.
- **The Music services dialog** (`/needle music`, the drum button in the window's title bar, or the
  *Spotify account* row): set up Spotify from the game in three steps (the dashboard address and the
  redirect URI to copy, then your Client ID), **Connect Spotify**, **Disconnect…**, the two tests and
  **Sign in to YouTube…**. The game can't open web pages: the app opens Spotify's sign-in page in your
  browser, and a Windows notification tells you the result. **Refresh status (reloads UI)** then shows
  it in game.
- **Play music with:** *Spotify, then YouTube* (the default), *YouTube, then Spotify*, *Spotify only*,
  *YouTube only*, or *Neither* (title cards and in-game files only). The app follows a change within a
  second while the game runs.
- **YouTube box position:** top right (the default), top left, bottom right or bottom left.
- **Test Spotify** and **Test YouTube** play 15 seconds of Toto's *Africa*, in game or on the Connect
  page; a Windows notification says why when a test can't play.
- **Sign in to YouTube** (optional): opens YouTube's sign-in page in the player box's own browser
  profile, not your everyday browser. Signed in with YouTube Premium, YouTube songs are expected to
  play without ads (not verified yet).
- **`/needle report`:** a plain-text report of your setup for bug reports (versions, the app's
  report, the beacon, the settings that matter, your last 5 drops, the last error), selected in a
  window so that Ctrl+C copies it. It holds no account names, Client IDs or tokens.
- **Connect page:** **Play on** lists *NeedleDropper (this app)* first; the Spotify card has **Test
  Spotify**; the YouTube card has **Test YouTube**, **Sign in to YouTube** and the player box's corner;
  both cards show what you chose in game.
- Windows notifications for the requests you send from the game (connected, a test result, signed
  out, …), and one *Sign in to Spotify again* notification after the update.
- The in-game status lines (window footer, add-on compartment, `/needle status`) say more about
  Spotify: connected as which account, *sign in again*, *didn't start*, or why the app's Spotify player
  can't play: *needs Google Chrome*, *not ready*, *not started yet* (the app starts it when the game isn't
  in front), *switched off* or *didn't load* (no internet, or a firewall).

### Changed

- **Spotify's own apps are no longer remote-controlled by default.** On the test PC, Spotify's
  Windows app accepted every song and never started one, while Spotify's Web Player played them; the
  app now is that player. To control another Spotify device as 1.2.0 did (your phone, a speaker, the
  Spotify app), pick it under **Play on** on the Connect page (`spotify.device` `connect`).
- **Sign in to Spotify again** after the update. The sign-in now also asks for `streaming` (playing
  in the app's own player) and `user-read-email` (Spotify's player requires it; NeedleDropper doesn't
  read or keep the address). Until you do, Spotify songs play on YouTube, and the Connect page, one
  notification and the in-game status ask you to sign in again. Your Client ID stays.
- **When you create a Spotify developer app, tick both Web API and Web Playback SDK** (for an app
  you already have, the second box is under its **Settings** → **Edit**).
- **The YouTube box loads its player from `http://localhost:8322/player`** instead of
  `127.0.0.1`. From `127.0.0.1`, YouTube refused 79 of the 100 videos in the built-in soundtracks
  with error 150; from `localhost`, none. The Connect page and the Spotify redirect URI stay on
  `127.0.0.1`.
- **The player window runs in Google Chrome** whenever Spotify plays in the app. Microsoft Edge is
  still used for YouTube when Spotify doesn't play in the app and Chrome is missing.
- **The beacon carries a stronger check** (a 4-byte check instead of 2 bytes), so a strip that an
  overlay or a frame partly covers is rejected instead of read as a different song. This is why a
  1.2.0 app and a 1.2.1 addon can't read each other.
- The idle beacon strip is 144 × 3 pixels (it was 96 × 3) once you choose a music setting in game,
  and while a drop plays: it then also carries your four music choices, or the playing drop's place.
- The addon treats an app older than 1.2.1 like a missing app: it doesn't fade the zone music for
  Spotify and YouTube songs, and the window's footer says *Update the NeedleDropper app to 1.2.1 to
  play Spotify and YouTube songs*.
- `NeedleDropper_Companion` (the app's data addon) also tells the game the Spotify account name,
  plan and Client ID, the player browser, your music choices and the results of your last 8 requests.
- **The built-in soundtracks** name the exact Spotify recording of every drop, so the app no longer
  searches Spotify for them, and every drop has a YouTube video that plays in the box: 5 videos were
  replaced by uploads that match the recording, and Azeroth Unbound's Azshara got one.

### Fixed

- Switching Spotify away from **NeedleDropper (this app)** and back no longer builds a second
  Spotify player inside the same page. Reconnection uses a fresh page and checks that Spotify
  lists its device before playing; a **Device not found** response gets one recovery attempt.
  Repeated recovery is limited to once a minute, and a page reload waits for a song playing there.
- Signing in again, signing out or changing the Spotify Client ID refreshes the player session.
  Automatic selection for **The Spotify app on this PC** skips the in-app **NeedleDropper** device.
- The app checks for a browser holding its player profile before launch, preventing an old
  player browser from silently taking over a new app's session.
- Music service tests last 15 seconds from playback starting. A failed **Test Spotify** clearly
  says YouTube was not tried; **Send a test signal** and `/needle test` explain that they play
  nothing. Testing or replaying a drop no longer shows the link-test notification.
- The recommended **Play on** choice and recovery advice distinguish NeedleDropper's own player
  from a separate Spotify app. The beta installation guide now uses public GitHub downloads and
  each tester's own Spotify developer app.

Found in a review of the beacon protocol:

- A damaged beacon strip could, rarely, read as a valid frame with different contents, for example
  a vertical line over one column of squares. The new check rejects every change confined to one
  column; sweeps over 90 million covered strips found no wrong frame.
- A song whose start signal was hidden for a few seconds (a big toast or an overlay over the beacon
  at that moment) never played. The app now starts it from the signal that follows, as long as the
  game says the drop is playing and nothing cancelled it.
- When a song's artist and title had to be shortened to fit the beacon, the app could receive one
  without the other and search Spotify for half a name. Both parts are now shortened together, or
  the search is left out (and the drop plays from its links).
- A drop with only a YouTube link could lose its artist and title from the beacon to make room, so
  Spotify couldn't search for it even with Spotify first. They now stay unless the drop has a link
  for the service that comes first.
- When the chat-log link is on, its copy of a signal adds what the beacon had no room for (such as
  the search for a Spotify song) to the drop the app already knows.
- Both the addon and the app reject signals whose text isn't valid UTF-8.

Found in the release checks and a second review (1.2.1 beta builds before these fixes):

- **The app's player window took the focus and stayed visible.** The tray app never attached the
  window manager that hides the player window, keeps it out of the taskbar and never lets it take the
  focus; the window stayed a normal Chrome window where Chrome put it (1.2.0 had the same bug). It is
  now made once, kept off every screen (Spotify's player doesn't play in a hidden window), and shown
  only at its corner while a YouTube song plays. A player window that
  is already open when the app makes its own (such as the one 1.2.0 leaves behind) is never taken
  over, hidden or closed by it.
- **Quit left the player's browser running**, and its page then confused the next start of the app
  (Spotify said *install Google Chrome*, a YouTube song could play twice). The app now closes its
  player browser when it quits, and a player page left over from an earlier start closes itself.
- **The YouTube box stayed on top of the game** after a YouTube song that couldn't play (a video that
  can't be embedded, or no internet), for the whole song that played instead. It now hides at once.
- **The YouTube box in the top-left corner covered the beacon**, so the app read nothing (such as
  `/needle stop`) until the song ended. It now sits below the beacon, and changing the corner moves
  an open box at once. With the beacon moved to another corner (the advanced `beaconCorner`, and the
  app's `transport.pixel.corner`), the box keeps clear of it there too.
- **Back-to-back Spotify drops** no longer lose your own music: it comes back once, after the last
  drop, instead of restarting for a moment between them and then staying off.
- A short hiccup of Spotify's player connection no longer reads as *install Google Chrome*, and no
  longer cuts the song that plays; a Spotify player that can't connect at all stays reported as such
  instead of flipping between *connected* and *needs Chrome* every minute.
- With *YouTube, then Spotify*, a song no longer waits up to 3 seconds for a Spotify search it
  doesn't need.
- With only the chat-log link, the first drop after a *Play music with* change used the old choice.
- A drop whose start signal was hidden is now started late whenever its arrival signal came first,
  also minutes before (the 10-minute cooldown, combat, a taxi), and also when the next place is armed
  right away (a landmark, then the zone at the same spot). The chat log's late copy of a drop's
  arrival signal no longer arms the playing place again, so a song can't start twice.
- While an in-game file plays, cancelling another place no longer lets the app start the playing
  drop's song on top of the in-game file.
- A rare two-square change of the beacon (two thin strokes) could read as a valid frame with a
  different Client ID, place or YouTube id: the check now has two final rounds that close this gap.
- The Music services dialog no longer stores the app's Client ID as one typed in game (it made a
  later *Sign in again* in game switch back to an old Client ID).
- A first arrival whose song no music service could play yet (Spotify not signed in again after the
  update, YouTube switched off, no player browser) shows its title card and now plays its song once
  the service works, instead of counting as heard. The zone music is no longer faded for a song only
  YouTube could play when the app has no player browser.
- *Neither* also keeps the song out of the arrival signal of a drop with an in-game file (when the
  in-game file then couldn't play, the app could still play the song).
- A Spotify account that isn't on your developer app's User Management list now shows *Account not
  allowed* at once (it showed *Connected* until the first song), and the game says to add it under
  User Management instead of *sign in again* (and stops saying so once that no longer applies, also
  after a restart of the app).
- An addon older than 1.2.1 with this app: the Connect page now says *Addon too old* (it said
  *Ready*), and the tray tooltip says to update the addon.
- The player window's focus hand-back goes to the game when you clicked into it while the window was
  being made (it went to the window that was in front before).
- In game, a Spotify player that isn't ready no longer always says *install Google Chrome*: the Music
  services line, the footer and the *Spotify account* row name the cause the app reports (Chrome or its
  Widevine component missing, a player that didn't get ready, no player window yet, the player switched
  off, Spotify's player not loading), and only a missing Chrome says to install it. `/needle report`
  names the cause too, and a cause no longer lingers once it's gone. The Connect page no longer says
  *install Google Chrome* for a player that runs in Chrome but never got ready.
- A request sent again from the game within 10 seconds is now refused in game (*Just sent: give it a
  moment.*), as the app does; it was 5 seconds, so a second **Test YouTube** 5 to 10 seconds after the
  first said *Sent* and the app ignored it. The app now counts those 10 seconds from your click, as the
  game does, so a request sent as soon as its button comes back is never ignored.
- Switching **Play on** away from *NeedleDropper (this app)* on the Connect page while a Spotify song
  plays lets the song finish (also when you switch back and forth during it, or as it starts), and
  then disconnects the app's own Spotify player: it no longer stays a Spotify device until the app
  restarts. Switching back connects it again.
- The player window, waiting off screen between songs, could end up on a monitor after a display change
  (a monitor added above or to the left, a new resolution or scaling). The app checks every few
  seconds and moves it off screen again, without ever activating it.
- The browser tab after Spotify's sign-in page no longer sends you to the old control panel: it says to
  press **Refresh status** in the game's Music services (or look at the Connect page), and after an error
  how to try again from either place.
- A damaged NeedleDropper_Companion file with a date far in the future no longer breaks the Music
  services status, the dialog, `/needle music status` or `/needle report`.
- The Spotify setup texts in game, on the Connect page and in the app say to tick both **Web API** and
  **Web Playback SDK**; signing in again after the update shows how to add the second box to your
  1.2.0 developer app.

### Security

- The player page gets Spotify's access token from the app through one address
  (`/api/player/spotify-token`) that answers only the player page itself (the same origin, the
  app's per-run secret, a launch key that only the player window the app opened knows, the address
  check) and never caches the answer. It returns the access token
  only, never the refresh token.
- A request from the game is one of five fixed actions (connect, disconnect, the two tests, the
  YouTube sign-in). No signal carries a web address or free text; the app builds every address
  itself. **Disconnect** only works when the signal names the app's own Client ID. Requests are
  accepted only while fresh (up to 45 seconds), once each, and rate-limited.
- Every playback report from the player page (YouTube, local files and Spotify's player alike) needs
  the player window's launch key, not just the token any local program can read from the player's
  address; a page without it closes itself.
- Only the player window the app opened is its player: a page another website opens at the player's
  address gets nothing, and a copy without the launch key is told to close. The app also listens on
  the IPv6 loopback (`[::1]`), where `localhost` goes first, so no other program can stand in for the
  player there (and the player's requests no longer wait about 2 seconds each).

### Known issues

- A song that already plays is never switched to another service. Where the beacon has room for
  only one row of squares (a very tight layout), a drop with a YouTube link can start on YouTube
  without its Spotify search; the chat log's fuller copy of the signal then only updates the title
  card.
- Microsoft Edge can't run Spotify's player (it never became ready in testing); in-app Spotify needs
  Google Chrome.
- *Resume my Spotify music afterwards* depends on the previous device accepting the resume command.
  If it does not resume, press Play in Spotify and report which device was involved.
- If the in-app Spotify player still says it **went offline**, quit NeedleDropper from its tray and
  restart it from the desktop. The beta's reconnect fixes still need wider field testing.
- Windows may show SmartScreen for the unsigned app (**More info → Run anyway**).
  Compare downloads with `SHA256SUMS.txt`. The app's update check reports stable releases only;
  testers get new beta links directly from the owner.

### Not yet verified on a live client

- The reconnect and **Play on** switch regression on the game client after these fixes, and the
  Music services dialog with a gamepad: `docs/FIRST-TEST.md`, section *Music services (1.2.1)*.
  The pre-fix clean-start test on November 6 played four Spotify songs and the YouTube test
  without taking focus from the game; that does not verify recovery after a reconnect.
- The fade-in of in-app Spotify by ear (the live checks played at volume 0), the YouTube sign-in page
  and whether a signed-in YouTube Premium account removes the ads in the box.
- Measured on the test PC with the released app (`NeedleDropper.exe` 1.2.1): the player window
  waited off screen from 0.2 seconds after it was made; in-app Spotify was ready about 3.3 seconds
  after the app started, and a song started 0.9 seconds after its signal; a YouTube song from
  `localhost` showed in the top-right box 0.2 seconds after its signal and the box went off screen
  again when it ended; quitting closed the player's browser. No song took the focus from the window
  in front; only the player window's creation did, for about 0.08 seconds, before it handed the
  focus back.

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
