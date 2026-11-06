# Real-client test results (2026-11-05)

WoW: Forever 1.60.1.70790 on Windows 11. 3840×2160 LG TV, game in borderless mode, Forever's **gamepad UI** in use (controller prompts). Level 6 troll priest in Durotar. Installed build was the v1.1 addon with a test map: Azeroth Mixtape plus a Razor Hill landmark.

Readers: `needledropper diag` and a scratch stand-in player (`scratchpad/poc/poc_player.py`) that used a frozen snapshot of the companion and the Spotify provider.

## Verified on the real client

| Area | Result |
|---|---|
| Addon load | `/needle status` correct: 39 drops, settings applied, title card settings, transports |
| Location | `zone:1411 Durotar / Razor Hill`; the preview label resolves `area:362`. Confirms the Classic-style uiMapIDs and subzone detection on Forever. |
| Director | Razor Hill (landmark) FIRE, then Durotar (zone) ARM→FIRE. The landmark won over the zone, and cooldown 0 allowed the next drop. `/needle test` FIRE n=0 TEST, then HELLO 30 s later. |
| Title card | Renders as designed (owner screenshot): spaced caps `R A Z O R   H I L L`, a thin rule, then `ZZ Top · Sharp Dressed Man`, centred about 22 % from the top. Legible at 4K; no overlap with Forever's UI. |
| Beacon (v1, 4 px cells) | Decoded at client (0,0) by both readers. In world, screen BitBlt works. Earlier, at character select or login, auto mode needed PrintWindow (it switched after 20 frames with no header). |
| Zone-music duck (silence method) | Zone music faded out as intended. Restore after the drop not yet confirmed. |

## Defects and risks found (fix in v1.2 integration)

1. **DEAD flag at login.** The first HELLO after login carried flag `0x08` (DEAD) while the character was alive. Flags are computed during loading. Compute live flags only when in the world (after `PLAYER_ENTERING_WORLD` / `LOADING_SCREEN_DISABLED`), or ignore the dead state while loading.
2. **Pixel transport window latch** (baseline finding PIX-WINDOW-LATCH). The reader latched onto a transient "World of Warcraft" window at game start, then lost it ("game closed?"). Prefer the match on the `_forever_\Wow.exe` path, and re-validate the cached window.
3. **Spotify `market=from_token`.** `/search` and `/tracks` with `market=from_token` return 403 "Insufficient client scope", because they need `user-read-private`. The provider misread this as "Premium required" and disabled Spotify for the session.
   - Fix: add the `user-read-private` scope. It also makes `/me` return `product` and `country`, so the Connect page can show a real Premium check.
   - Classify 403 scope errors separately from Premium or allowlist errors.
4. **Spotify desktop client does not start playback.**
   - With no active session in the desktop app, `PUT /me/player/play {uris}` returned 204 but nothing loaded (`item: null`), and every action was disallowed.
   - After the owner started a song by hand, play-by-uri loaded the track, but `progress_ms` stayed at 0 for more than 8 s (not actually playing).
   - The engine must check that playback **progresses** within about 3-4 s.
   - Recovery steps:
     1. Transfer playback with `PUT /me/player {device_ids:[id], play:true}`, then play again.
     2. If it is still stuck, fall back to YouTube and send the tray notification "Spotify isn't starting songs — restart the Spotify app".
   - Log `actions.disallows`.
   - Root cause on this PC is unresolved (a stuck desktop client is suspected). A restart of the Spotify app is pending.
5. **Token save lost on WinError 17.**
   - `os.replace` inside `%APPDATA%\NeedleDropper` raised `[WinError 17] ERROR_NOT_SAME_DEVICE` when the process ran under the Claude desktop host (AppData virtualization). The freshly obtained login was thrown away.
   - Atomic writes need a fallback: on `OSError` errno 17 / winerror 17, write in place (or copy then replace) and keep the tokens in memory. Apply this in secrets, gamedata, config and atomic_write helpers.
   - Never lose a just-obtained token.
6. **Silent drops when nothing can play.** The addon ducks zone music for the whole song even when no app or provider plays it, which leaves silence.
   - With v1.2's `Companion.lua` status: if no companion data exists (app never seen), external drops show the card but don't duck.
   - If the data says the app exists, duck as now. Add a setting `duckWithoutApp` (default false).
7. **Gamepad.** The owner plays with a controller. The v1.2 window and dialogs must be usable with Forever's gamepad UI and cursor (`Blizzard_GamepadSmartNavigation` exists on the forever branch). Verify buttons and edit boxes are reachable, and that nothing traps the gamepad cursor.
8. **Focus stealing and late starts (owner feedback, 19:35).**
   - The stand-in's YouTube fallback opened a browser tab. It alt-tabbed the owner out of the game and started late, because it waited about 5 s for a stuck Spotify and then loaded a full page.
   - Requirements for the product:
     - **Nothing ever takes focus from the game.** Spotify plays with no window. YouTube plays in a small always-on-top, non-activating player window: launched without activation, then positioned with `SWP_NOACTIVATE`. It is visible, as YouTube's rules require, and closes or hides when the drop ends. Never `webbrowser.open` for playback.
     - **Primed playback.**
       - On ARM, about 10 s before FIRE, resolve the song (Spotify search), check Spotify device health and wake the device, and pre-load the YouTube video *cued and paused* at its start, muted, in the player window.
       - On FIRE, only unmute and play. Target: music within 300 ms of FIRE.
     - **Session health memory.** After one stuck or failed Spotify attempt, use YouTube directly for the rest of the session, and re-check Spotify on the next ARM in the background.
   - Verify empirically on this PC: `GetForegroundWindow` stays the WoW window across ARM, FIRE and end, and measure FIRE-to-audio latency.
   - **Owner decision:** the YouTube player box defaults to the **top-right** corner. Add a setting for the corner.
9. **Spotify desktop app greyed out and stuck (owner report, 19:40).** Even manual play in the Spotify app is greyed out and stuck, so the playback failure above is local to the Spotify client on this PC, not the Web API calls. The stuck-client detection and YouTube fallback in #4 is still required, because players will hit this too.

## v1.2.0 exe test on the owner's PC (21:43-21:52)

| Area | Result |
|---|---|
| Spotify login | Works when the owner starts the exe himself: "Spotify login complete (the tester)", tokens saved. |
| Linking | Pixel v2 linked. The beacon was found at (0,0), 32×1 cells, 3 px, via screen capture. Capture flips to PrintWindow when the strip is briefly hidden, then flips back. `/needle test` produced the "linked" and "test" notifications. |
| Stormwind drop | The FIRE frame carried the song (YouTube id, query, length, label, set). The Spotify search matched. |

10. **Spotify desktop (Store 1.306.1921.0) never starts remote songs.** Every Web API play since 18:31 ended `item=None`, with every action disallowed, even right after a fresh Spotify restart (21:43). The owner reports Spotify **froze** after the 21:52 retry and transfer sequence.
    - Fix: after one stuck or failed attempt, stop all Spotify calls for the session: no retries, no transfer, no pause on a client that never started. Go straight to YouTube.
    - Surface "Spotify isn't responding: restart it, or switch NeedleDropper to YouTube" once.
    - Consider a cheap pre-flight on ARM: if `GET /me/player` shows `item=None` with every action disallowed, skip Spotify for this drop without sending play at all.
11. **The YouTube player window never started from the frozen exe.** The log says "opened the player window (edge)", but `%APPDATA%\NeedleDropper\deck-browser` stayed **empty** (not even Crashpad), and the player never connected within 8 s.
    - Suspected cause: PyInstaller's bootloader `SetDllDirectory(_MEIPASS)` is inherited by child processes, so msedge.exe loads the wrong VCRUNTIME/SSL DLLs and dies.
    - Fix: before launching any external program from the frozen app, call `ctypes.windll.kernel32.SetDllDirectoryW(None)` (or launch through the Explorer shell), and clear the PyInstaller `_PYI_*`/`_MEIPASS*` environment variables for the child.
    - Verify by launching Edge from the built exe.
    - Implement the long-lived hidden player per docs/research/no-focus-playback.md.
12. **"Spotify Lite" works (22:05).** `os.startfile("spotify:track:<id>")` from a background process made the Store desktop client (1.306) **play the exact track within about 1.1 s**, with progress advancing. No Web API play command and no developer app were needed; the Web API was used only to read state.
    - Downside: the Spotify window took the foreground for about 5 s.
    - Proposed product direction, pending the owner's go-ahead:
      - make Lite the **default Spotify mode**, with zero setup and possibly Free accounts too (verify);
      - keep the developer-app Web API mode as an optional "precise" mode (fades, start offset, resume).
    - Lite needs:
      - focus handback: after the URI launch, find the Spotify window and `ShowWindow(SW_MINIMIZE)` it within 200-500 ms, so Windows reactivates the previous foreground window (WoW). Never inject input.
      - pausing at the end of the drop through Windows media session controls (GlobalSystemMediaTransportControlsSessionManager via a PowerShell/WinRT helper or ctypes WinRT), never synthetic media keys.
      - no start offset, no fades; the previous queue is not restored (document this).
13. **Owner decision (22:10): a window coming to the front is a deal-breaker.** Spotify Lite (URI handler) is rejected, because it activates the Spotify window. Spotify stays Web API (Connect) only, which never shows a window. The account is confirmed **Premium** (US) via `/me` with `user-read-private`, so the refusals come from this client: Microsoft Store Spotify 1.306 accepts plays and never starts them.
    - Recommend to players: the spotify.com desktop build rather than the Store build (troubleshooting entry).
    - The engine's fast stuck detection plus session-long YouTube fallback in the focus-safe box is the safety net.
14. **Root cause isolated (22:30). Spotify desktop clients don't start Web API plays; the Web Player does.**
    - The owner reinstalled Spotify from spotify.com (`%APPDATA%\Spotify\Spotify.exe`). It still accepted plays and dropped the loaded item without starting anything.
    - The same API call to **"Web Player (Chrome)"** played `"Heroes"` from `position_ms=30000` immediately and paused on command.
    - This matches a recent public report: https://github.com/encodi/sonora-mcp/issues/34 (Android does the same; Chrome works).
    - **New Spotify design:** the app plays Spotify **itself** through the Spotify **Web Playback SDK** in its long-lived hidden player window, registered as a Connect device ("NeedleDropper").
      - Scopes: add `streaming`, `user-read-email`, `user-read-private`.
      - Target the SDK device id with play `{uris, position_ms}`.
      - Fades via the SDK's `setVolume`.
      - Prime on ARM: load the track, pause at volume 0 (Spotify has no visibility rule).
    - Desktop-app control becomes an optional fallback, off by default. Window rules stay strict: no window ever for Spotify; the YouTube box only while a YouTube song plays, and never activated.
15. **PROVEN (22:48): in-app Spotify through the Web Playback SDK in a hidden Chrome player.** Prototype at `scratchpad/poc/sdk_proto.py`; owner's PC, Chrome stable.
    - Token scopes: `streaming user-modify-playback-state user-read-playback-state user-read-email user-read-private`.
    - Page: loads `https://sdk.scdn.co/spotify-player.js` and creates `new Spotify.Player({name:'NeedleDropper', getOAuthToken})`. It calls `activateElement()` after `connect()`. `ready` with a device id arrives in about 0.7-1.3 s.
    - Chrome flags required:
      - `--app=<local page>`
      - a dedicated `--user-data-dir`
      - `--autoplay-policy=no-user-gesture-required`
      - `--disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling --disable-features=CalculateNativeWinOcclusion`
      - `--window-position` off-screen and a tiny `--window-size`
    - **Without the four background flags, the SDK stays in `loading: true` forever and plays nothing.** With them, `PUT /me/player/play?device_id=<sdk> {uris, position_ms:30000}` played immediately: server progress 30877→37671 ms on device "NeedleDropper", page `loading:false`.
    - Widevine: `navigator.requestMediaKeySystemAccess('com.widevine.alpha')` succeeds in the dedicated profile after copying `%LOCALAPPDATA%\Google\Chrome\User Data\WidevineCdm` into it. Otherwise wait for Chrome's component updater on first run.
    - Edge attempt: the SDK was not ready within 40 s (first-run or profile issue, not diagnosed). Prefer Chrome, with Edge as a fallback to diagnose.
    - Focus: window creation can take the foreground for a few seconds. So create the player once at app start (never in game), apply the no-activate styles and hiding from docs/research/no-focus-playback.md, and hand focus back explicitly.
