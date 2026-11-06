# Baseline review findings (pre-v1.2 code, independently verified)

Read-only review of the addon and companion as of 2026-11-05 ~17:00, three lenses, each finding challenged by a skeptic. **Confirmed** findings must be fixed by the integrators if the code still exists after the v1.2 changes; **uncertain** ones need a judgement call or an in-game check.

## Lens: addon-api

Refuted (dropped): 0

### Uncertain (1)

#### [medium] ND-AUDIO-TEMPCVAR: Volume duck sets a temp CVar but never calls RemoveTempCVar; the restore relies on SetCVar overriding an active temp value
- File: `D:/NeedleDropper/addon/NeedleDropper/Audio.lua:31`
- Detail: Verdict: PLAUSIBLE, not confirmed. Nothing documents how SetCVar behaves while a temp value is active.

Why every volume duck hits this: C_CVar.SetTempCVar always exists on Forever 1.60.1. So duckMethod="volume" always mutes music through the temp path (Audio.lua:28-34). `/needle selftest music` with that method takes the same path (SelfTest.lua:92).

What the undo paths do: all of them call C_CVar.SetCVar(saved), and nothing in the addon ever calls C_CVar.RemoveTempCVar. The undo paths are:
- A.Restore (Audio.lua:62-63)
- the PLAYER_LOGOUT handler (Audio.lua:97)
- ns.RestoreCVar (Core.lua:429-435)
- ns.RepairCVars (Core.lua:438-456)

What goes wrong if the temp value keeps overriding until RemoveTempCVar (the model Blizzard's own gamepad code assumes):
1. After the first volume-ducked drop ends, music stays at 0 for the rest of the session.
2. If GetCVar also reports the effective (temp) value, the next duck's ns.SaveCVar stores "0" as the "original" value.
3. That duck's restore then writes SetCVar("Sound_MusicVolume","0") as the persisted value, which goes to Config.wtf at logout. The player's music volume stays muted permanently.

Smaller side effect: after a crash on the temp path, RepairCVars prints "restored sound settings left over from an interrupted session" even though no persisted value changed.

The test suite cannot detect this. The shim's EnableTempCVar writes the same SHIM.cvars table that SetCVar/GetCVar use (tests/lua/wow_shim.lua:1073-1093). So test_duck_volume_prefers_temp_cvar (tests/test_addon_audio.py:70-77) passes under either semantic. The default shim also has no SetTempCVar, so most volume tests exercise the plain SetCVar path, which is not the path the real client takes. Impact is limited to the non-default duckMethod "volume"; if the semantic holds, the worst case is a permanently muted music setting.
- Verifier: Whether this is a bug depends on one undocumented client behaviour: does plain C_CVar.SetCVar cancel an active SetTempCVar override? I could not settle that, so the verdict is uncertain. Nothing I found refutes the finding.

Code (verified):
- With duckMethod="volume", A.Duck (Audio.lua:28-34) calls SaveCVar and then pcall(C_CVar.SetTempCVar,"Sound_MusicVolume","0").
- That call exists on 1.60.1 and its preconditions pass, because Sound_MusicVolume is a public Settings slider CVar (forever Blizzard_SettingsDefinitions_Shared/Audio.lua:526). A failed precondition would not raise anyway: its FailureMode is "ReturnNothing".
- Every undo goes through SetCVar(saved): A.Restore (Audio.lua:62-63), PLAYER_LOGOUT (Audio.lua:97), ns.RestoreCVar (Core.lua:429-435) and RepairCVars (Core.lua:438-456). No file in addon/ ever calls RemoveTempCVar.
- SelfTest.lua:92 takes the same path.
- docs/SPEC.md:215-216 prescribes exactly this, so the spec has the same gap.
- The default duckMethod is "silence" (Core.lua:135). But SelfTest.lua:97-98 and TROUBLESHOOTING.md:83 tell users to switch to "volume".

Primary source (Gethe/wow-ui-source forever; version.txt 1.60.1.70790 matches the installed build):
- CVarDocumentation.lua:113-125 (RemoveTempCVar) and 170-183 (SetTempCVar) give only signatures and preconditions.
- Blizzard has two consumers: Blizzard_Gamepad/Core.lua:1-8, and Blizzard_GamepadTargeting/TargetLogic.lua:104, which the reviewer missed. Both undo a temp value only with RemoveTempCVar, never SetCVar.
- Blizzard Settings code calls plain SetCVar on CVars that gamepad mode holds as temps (Controls.lua:79, Combat.lua:189, Accessibility.lua:61-62) and never shows which value wins.
- The installed Wow.exe contains only the usage strings.
- The warcraft.wiki.gg pages API:C_CVar.SetTempCVar and API:C_CVar.RemoveTempCVar have no behaviour text.
- Minor correction: the ptr branch (12.1.7.70740) has the same APIs and an identical Gamepad/Core.lua, so "Forever-exclusive" is true only against live 12.1.5.
- The installed WTF holds no NeedleDropper SavedVariables or Sound_MusicVolume line, so there is no in-game evidence either.

Experiment: C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_tempcvar\exp_models.py runs the unmodified addon in the project harness through:
- two real first-visit volume drops;
- selftest music;
- the user moving the music slider (SetCVar 0.8);
- PLAYER_LOGOUT.

It runs under three engine models:
1. shadow (the reviewer's model: the temp value overrides SetCVar until RemoveTempCVar). The finding reproduces:
   - After drop 1 the effective volume is 0.
   - Drop 2 saves "0" as the original, so the persisted (archived) value becomes 0.
   - The in-game slider cannot recover: Settings.SetupCVarSlider calls SetCVar, which leaves effective at 0 for the rest of the session.
2. overwrite_keep_backup (SetCVar replaces the live value, but the CVar stays temp-flagged and the pre-temp value is what gets archived). Music comes back, but the temp flag is never cleared, so the user's later 0.8 is dropped at logout (archived 0.4). This is a second persistent failure that the reviewer did not consider.
3. clear_on_set (SetCVar cancels the temp). The current code is correct.

The current code is therefore correct only under model 3, which nothing in Blizzard's own usage supports. With the suggested fix patched in at runtime, all three models end with effective 0.8 and archived 0.8, and no errors.

The test-suite claim is accurate:
- The shim's EnableTempCVar (tests/lua/wow_shim.lua:1087-1093) writes the same SHIM.cvars table that SetCVar uses.
- No RemoveTempCVar exists in the shim.
- So test_duck_volume_prefers_temp_cvar (tests/test_addon_audio.py:70-77) passes under any semantic.

The RepairCVars-message sub-claim is cosmetic only: it rewrites the same value.

Severity is medium: if model 1 or 2 holds, a persisted user audio setting is corrupted. The impact is limited to the opt-in volume method, though the addon itself recommends that method.
- Fix: Undo the temp path with RemoveTempCVar, and keep the crash record.

1. Audio.lua, A.Duck, volume branch:
```lua
ns.SaveCVar("Sound_MusicVolume")
A.tempCVar = false
if C_CVar and C_CVar.SetTempCVar and C_CVar.RemoveTempCVar then
  A.tempCVar = pcall(C_CVar.SetTempCVar, "Sound_MusicVolume", "0")
end
if not A.tempCVar then ns.SetCVar("Sound_MusicVolume", "0") end
A.ducked = "volume"
```

2. Add this helper and call it in place of ns.RestoreCVar("Sound_MusicVolume") in A.Restore (Audio.lua:63) and in the PLAYER_LOGOUT handler (Audio.lua:97):
```lua
local function undoVolume()
  if A.tempCVar then
    A.tempCVar = false
    pcall(C_CVar.RemoveTempCVar, "Sound_MusicVolume")
    ns.ForgetCVar("Sound_MusicVolume")
  else
    ns.RestoreCVar("Sound_MusicVolume")
  end
end
```
- Never call SetCVar on the temp path.
- After a crash, RepairCVars still writes the saved original, which is harmless because the temp layer does not outlive the process.
- Optional: tag the entry so RepairCVars skips its "restored sound settings" print.

3. Tests:
- Add a shim mode with a separate temp layer: GetCVar reads through it, SetCVar does not clear it, RemoveTempCVar drops it. The 'shadow' block in exp_models.py is a ready model.
- Add a test: two consecutive volume-ducked drops, then a user SetCVar, then logout. Assert that the effective and archived Sound_MusicVolume equal the user's value and that RemoveTempCVar was called.
- Make test_duck_volume_prefers_temp_cvar also assert that RemoveTempCVar was called.

4. Optional in-game check to settle the semantic (the fix is correct under every model):
- Run `/run local v=C_CVar.GetCVar("Sound_MusicVolume") C_CVar.SetTempCVar("Sound_MusicVolume","0") C_CVar.SetCVar("Sound_MusicVolume",v) print(C_CVar.GetCVar("Sound_MusicVolume"), v)`.
- If it prints 0, or zone music stays silent, model 1 holds. Clean up with `/run C_CVar.RemoveTempCVar("Sound_MusicVolume")`.
- Otherwise, without calling RemoveTempCVar, set the Music slider to 73% and exit the game.
- If WTF/Config.wtf then lacks `SET Sound_MusicVolume "0.73"`, model 2 holds.

## Lens: companion-runtime

Refuted (dropped): 0

### Confirmed (9)

#### [medium] ENG-STALE-HELLO: Late HELLO delivered by the chat-log transport stops a drop the beacon just started
- File: `companion/needledropper/engine.py:375`
- Detail: The engine skips events it has seen in the last 15 s (same seq, type, n, hash). It returns from duplicates (lines 333-337) before it reaches the DROP_ACTIVE check that clears _stale_since (347-348). _on_hello (371-377) starts the stale timer for any new HELLO without DROP_ACTIVE, without checking that the HELLO is older than the FIRE that started the current drop.

The addon writes HELLO and ARM to the chat log without padding. ChatLink.lua:14 pads only FIRE, CANCEL and PING (SPEC 3.6: 'ARM/HELLO are emitted unpadded'). So those lines sit in the client's ~48 KiB log buffer until the FIRE's NDPAD padding flushes them, in one batch together with the FIRE.

If the beacon delivers the FIRE first:
1. The chat-log batch then delivers the old HELLO. It is not treated as a duplicate: the beacon saw it more than 15 s earlier and the entry was pruned. This sets _stale_since.
2. The chat log's copy of the FIRE is a duplicate, so it returns before clearing the timer.
3. The addon emits no other event during an active drop. Director.lua 354-359 and 594-614 emit HELLO only at login, on a zone change with nothing armed or active, and when a drop ends.
4. tick() (285-289) stops the drop 5 s later: 'addon no longer reports ... active'.

The ordering is deterministic whenever chat-log delivery lags the beacon: chatlogPadBytes set to 0 or below the client's flush size (the setting allows 0..200000), or chat messaging lockdown (ChatLink retries after 1 s). Otherwise it is a race against the beacon's two-read confirmation. In those setups it hits the first drop of every session (HELLO at login, then ARM, then FIRE). The addon has already marked that drop done, so the first-visit moment is lost.
- Verifier: The code path is real. engine.py:333-337 returns on a duplicate before the DROP_ACTIVE clear at 347-348. _on_hello (371-377) starts _stale_since for any non-duplicate HELLO that lacks DROP_ACTIVE, with no seq or age check. tick() (285-289) then stops the drop 5 s later. ChatLink.lua:14 pads only FIRE, CANCEL and PING, and SPEC.md:128 says 'ARM/HELLO are emitted unpadded'. So the last HELLO (login, Director.lua:595-601; end of a drop, 355-359; zone change, 613) and the ARM stay in the client's log buffer until the FIRE's NDPAD lines flush HELLO, ARM and FIRE together. The transports call engine.submit directly (panel_api.py:512) and nothing in between filters by seq.

Reproduced with the real PixelTransport.feed (two-read rule), a real ChatLogTransport tailing a temp file, and the Engine (C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_cr\eng_stale_variants.py):
- (A) Default timing, where the chat batch lands first (250 ms poll against the beacon's 250 ms or longer two-read confirmation): no stop.
- (B) Beacon first, chat batch 0.6 s later: the drop is stopped at FIRE+5.8 s with 'addon no longer reports zone:1429 active'.
- (C) Chat batch 3 s later: stopped at FIRE+8.2 s.
- (D) Context only, outside this finding: with more than 15 s of lag the chat-log FIRE is no longer a duplicate, so 'new drop replaces' restarts the drop.
Moving the DROP_ACTIVE clear above the dedupe return, patched in-process (...\skeptic_cr\eng_fix_check.py), fixes B and C.

Re-rated from high to medium:
- It needs the opt-in chat-log channel (Core.lua:144 chatlog=false) running next to a working beacon.
- It needs chat delivery to lag the beacon, which is not the default race direction.
- But in lagging setups it hits nearly every drop, not only the first: every drop's preceding end-of-drop or zone HELLO is also unpadded and more than 15 s old.
- Lagging setups include pixel hz raised toward the panel maximum of 10 (panel_api.py:199), a higher pollMs, chatlogPadBytes below the flush size or 0, and the lockdown retry.
- Fix: Main fix: add fire_seq to _Active, set when a transport FIRE starts the drop. In _on_hello, start the stale timer only when (ev.seq - cur.fire_seq) % 256 is in 1..127, so the HELLO must be newer than the FIRE. Skip this check for origin 'cli'.

One-line patch that was verified: move `if ev.has(Flags.DROP_ACTIVE): self._stale_since = None` above the duplicate early-return in Engine._on_event (engine.py:333-337), so a duplicate DROP_ACTIVE event still clears the timer. On its own it has a small reverse risk: a late duplicate FIRE that arrives after a real end-of-drop HELLO would clear the timer again. That only delays the stop until stop_at; nothing hangs. The seq guard avoids this.

Optional, for the over-15 s restart: ignore a FIRE whose (seq, n, hash) equals the current drop's FIRE.

#### [medium] SPOT-VERIFY-UNMANAGED: play() that fails verification leaves Spotify playing unmanaged and the user's playback hijacked, then the engine plays the next provider too
- File: `companion/needledropper/providers/spotify.py:959`
- Detail: After PUT /me/player/play has been accepted (and re-sent once by _verify_started at 1062-1064), a verification miss raises ProviderError at 1069. A miss means GET /me/player still returns 204 or another item for about 3 s, which happens when Spotify's player state lags the command, for example on a just-woken or remote Connect device.

The except block at 959-961 only restores the volume:
- no pause is sent;
- the snapshot is not restored;
- the session is never added to _sessions (967-968) or returned, so nothing will ever stop it.

engine._play_chain (478-481) treats ProviderError as 'try the next provider', so local or YouTube starts the same drop while Spotify starts it late: double audio. At the natural end of the track Spotify's autoplay continues. The user's previous playback was transferred to our device and its context replaced by our uri, and it is never restored.
- Verifier: How it happens:
- _start_playback's PUT /me/player/play is accepted.
- _verify_started re-sends play once (spotify.py:1062-1064).
- If the player state was readable but never showed our track, it raises ProviderError (1069-1070).
- The except at 959-961 only calls _restore_volume. It sends no pause and never calls _restore_snapshot.
- The session is never appended to _sessions (968), so stop() and close() can never reach it.
- engine._play_chain (478-481) then moves on to the next provider.

Reproduced with the suite's FakeHTTP (C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_cr\sp_verify_late.py). The player state lags the roughly 3 s verify window and then starts our track late, in three variants:
- (1) 204 for all four verify reads;
- (2) our device active with no item;
- (3) the phone still showing the user's own song.
In all three: two PUT play to dev-pc were sent and the engine played the 60 s drop on local and stopped it. No pause and no snapshot restore was ever sent, 0 Spotify sessions were tracked, and at the end Spotify is still playing the drop track on GAMING-PC. The user's playlist on the phone was replaced and never restored. The reviewer's sp_verify.py, re-run, gives the same result.

Spotify's start-playback reference says 'The order of execution is not guaranteed when you use this API with other Player API endpoints'. 204 only means the command was accepted.

Re-rated from high to medium. It needs more than about 3-4 s of state lag after an accepted play, for example a just-woken app or a cross-device transfer. The ARM prewarm (prepare) usually wakes the device before FIRE.

Note: test_play_fails_when_track_never_starts encodes the case where the user's own item keeps playing elsewhere and raising is intended.
- Fix: Handle the verify outcome by state:
- If the last state is 204 or empty, or shows our own device (s.device_id) active with no item or another item, treat it like the unreadable case at 1065-1067. Return a managed handle, append the session, and let poll() and stop() reconcile; stop() then pauses and restores the snapshot.
- If you prefer to raise in that case, first send a tolerant PUT /me/player/pause?device_id=<our device>, then _restore_volume(s) and _restore_snapshot(s).
- Raise bare only when the state shows another item still playing on a different device, meaning the user's playback was left alone. Ideally send a tolerant pause aimed at our device_id first.
This keeps test_play_fails_when_track_never_starts valid.

#### [high] PIX-WINDOW-LATCH: Pixel transport locks onto any window titled 'World of Warcraft...' found before the game starts and never searches again
- File: `companion/needledropper/transport/pixel.py:1022`
- Detail: _ensure_window returns the cached hwnd for as long as backend.window_alive() is true (1016-1023). It calls find_window() only when it holds no window.

wincapture._score gives a positive score to title-only matches: 30 for an exact title, 10 for a prefix (lines 276-285). list_game_windows accepts any score above 0 (310-311), even when game_exe is known and the window belongs to explorer.exe or msedge.exe.

The documented flow starts the companion before the game (cli.py:725 'Waiting for the game.', cli.py:908 'Start NeedleDropper before you play'). So the first searches run before the game exists. If an Explorer window on the WoW install folder (titled exactly 'World of Warcraft') or a browser whose active tab starts with 'World of Warcraft' is open at that moment, the transport latches onto it. It keeps capturing that window as long as the window exists (a browser window's hwnd survives tab changes) and never picks up the game. The main transport is silently dead, and the status text blames the addon ('is the addon loaded, /needle beacon on ...').
- Verifier: The code path:
- wincapture._score (276-280) gives +30 for an exact title and +10 for a title prefix even when the process image does not match.
- list_game_windows accepts any score above 0 (310), even when game_exe is known.
- PixelTransport._ensure_window (1016-1023) returns the cached hwnd while IsWindow is true and calls find_window only when it holds no window. Nothing else resets _hwnd; the auto capture-method switch does not search again.

Reproduced on the real Win32 backend (C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_cr\latch_real.py). A python.exe window titled 'World of Warcraft', off-screen and without a taskbar button, was tested with the configured game_exe C:\Program Files (x86)\World of Warcraft\_forever_\Wow.exe:
- list_game_windows returned it with score 30 (exe C:\Python314\python.exe).
- PixelTransport latched onto it and made 1 window search in 20 s.
- The status detail said 'no beacon visible in the top-left corner - is the addon loaded...'.

The reviewer's latch.py (the suite's FakeScreen), re-run, shows no new search after the game starts: 1 search in 200 s and no events emitted.

Exposure:
- The companion is meant to run before the game (cli.py:725 'Waiting for the game.', autostart).
- Chrome window titles on this machine are '<tab title> - Google Chrome', so an active tab titled 'World of Warcraft...' gives +10.
- A Windows 10 Explorer window on the install folder is titled exactly 'World of Warcraft' (+30).
- Either one being open during any of the 5 s searches is enough.
- The result is a silent loss of the main transport for the session, with a diagnosis that blames the addon. High stands.
- No such window was open on this box at test time.
- Fix: 1) Window scoring (_score / list_game_windows): when game_exe is known, accept only candidates with an exe-based score (at least 60) or whose image name starts with 'wow' (case-insensitive). Keep title-only matching for when game_exe is None.

2) PixelTransport: while no beacon header has been seen on the current hwnd, search again every REFIND_INTERVAL. Call list_game_windows, which returns scores (find_game_window does not), and switch to a different, higher-scoring window when one appears. When switching, call reset_baseline() and clear _strip and _beacon_visible.

#### [low] SPOT-POLL-ERROR-FALLBACK: Spotify API trouble in the first 15 s makes the engine fall back while Spotify keeps playing (double play)
- File: `companion/needledropper/providers/spotify.py:1171`
- Detail: poll() returns 'error' after 3 consecutive request failures (network, 5xx, 429 with Retry-After over 5 s) or on ProviderUnavailable (1159-1171). The Spotify client is still playing the track at that point.

Engine.tick then calls _on_provider_error (engine.py 508-516). That calls stop(handle, 0) and, within fallback_window_sec (15 s), starts the next provider. Spotify's _stop needs the same failing API: the GET fails, so the verdict is 'playing', and the PUT pause also fails and is only logged (1237-1240). The Spotify track keeps playing while local or YouTube plays the same drop, and nothing manages Spotify any more.
- Verifier: The code path:
- poll() returns 'error' after MAX_POLL_FAILURES=3 consecutive ProviderErrors (spotify.py:1164-1171), and immediately on ProviderUnavailable (1159-1163).
- engine._on_provider_error (508-516) calls _safe_stop, then within 15 s of the play start runs _play_chain to the next provider.
- With the API down, _stop cannot confirm anything: the GET fails so the verdict becomes 'playing', and the PUT pause fails and is only logged (1237-1240).
- The session is marked stopped and removed, so nothing retries the pause.

Reproduced:
- The reviewer's sp_pollerr.py, re-run (429 with Retry-After: 30): 'spotify reported an error' and then 'playing zone:1429 on local'.
- C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_cr\sp_poll_net.py (network unreachable for 8 s starting 3 s into the drop): the error and 'playing ... on local' both at t+9.5 s. After the outage ended, Spotify was still playing the drop track, with 0 sessions, and nothing ever paused it.

Re-rated from medium to low. The trigger needs about 4-6 s of consecutive API failures starting within roughly the first 10 s of a drop: an outage, a 5xx burst, a 429 longer than 5 s, or a revocation mid-drop. Outside that window the outage alone leaves Spotify unmanaged and there is no double audio.
- Fix: Provider side (least invasive): on a transient ProviderError in poll(), keep returning 'playing'. Estimate the position from last_pos/last_pos_t and honour Retry-After. Return 'error' only for a definite playback failure seen in a successful state read.

For ProviderUnavailable mid-drop, what matters is that poll() does not return 'error', so no fallback starts while the stop cannot be confirmed. Either 'ended' or 'playing' works: 'ended' hides the card at once, 'playing' keeps it until stop_at.

Engine-side alternative: change the Provider.stop contract to report whether the stop was confirmed, and skip the fallback in _on_provider_error when it was not. This is a change to the base contract.

#### [medium] SPOT-TOKEN-SAVE-LOSS: A failed write of refreshed tokens throws away the rotated refresh token and leaks a raw OSError
- File: `companion/needledropper/providers/spotify_auth.py:561`
- Detail: _refresh_locked (556-563) gets new tokens from Spotify, then calls store.save(new). save_secret does mkstemp plus os.replace with no retry (secrets.py 157-174). config.atomic_write_bytes, by contrast, retries PermissionError 10 times for files briefly locked by antivirus or other processes.

If that write raises, the new access and refresh tokens are dropped, and the file and cache still hold the superseded refresh token. The next refresh sends the old token again. Refresh tokens rotate (the refresh() docstring at line 425 says so; rotation is the norm for public PKCE clients, RFC 9700 4.14.2). If Spotify has invalidated the old token, it answers invalid_grant, _auth_failure calls _mark_unavailable, and the user must reconnect Spotify.

The OSError is also not a SpotifyAuthError, so it escapes _token() and _call() (spotify.py 571-574) as a raw exception. play() and poll() do not classify it: the engine logs it as a crash or a failed poll and goes down the error and fallback paths.
- Verifier: The code path:
- _refresh_locked (spotify_auth.py:556-563) calls store.save(new) after Spotify has already issued the new tokens.
- TokenStore.save sets _cache only after nd_secrets.save_secret succeeds.
- save_secret (secrets.py:157-174) does mkstemp plus os.replace with no PermissionError retry, unlike config.atomic_write_bytes (272-300).
- On failure, AT-new and RT2 are lost, and the file and cache still hold RT1.
- The OSError is not a SpotifyAuthError, so it escapes _token() (spotify.py:571-574) and the 401 force_refresh path (615) out of _call. play() and poll() do not classify it, so the engine logs it as 'crashed playing' or as a failed poll.

Reproduced:
- The reviewer's token_save.py, re-run: 'call 1: RAW PermissionError escaped _call()', and the refresh tokens sent were ['RT1', 'RT1'].
- C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_cr\token_rotation.py, with a fake token endpoint that invalidates a refresh token once used: drop 1 'spotify crashed playing ... PermissionError', drop 2 'invalid_grant ... Spotify disabled for this session', provider unavailable, and the user must reconnect.

Certain: the raw exception escape, and RT2 never being cached or stored.

Plausible but not proven: that Spotify really invalidates the superseded refresh token. Spotify's refreshing-tokens guide only says to keep the new token when one is returned. The rotation statement comes from a search-engine excerpt of a Spotify staff reply in the community 'Refresh token expiration: discussion thread'; the thread itself could not be fetched, only its title came back. The project's own refresh() docstring (spotify_auth.py:425) assumes rotation.

The trigger is uncommon: the rename blocked by antivirus, a backup tool, or another reader at the hourly refresh. The project already guards against exactly that in atomic_write_bytes.
- Fix: Keep the new tokens live even if the write fails. In TokenStore.save, set self._cache = clean (with the stamp) before or regardless of the write, or have _refresh_locked keep `new` in memory.

Retry os.replace on PermissionError in save_secret. Copy the retry loop from config.atomic_write_bytes, or move it into a small shared helper module, rather than having secrets.py import config; that risks a circular import.

Convert storage errors: catch OSError around store.save in _refresh_locked. Either log it and return the in-memory access token, or raise SpotifyAuthError(error='storage'). Then _token() and _call() turn it into a ProviderError instead of a raw exception.

#### [medium] CHAT-REPLAY-ON-ERROR: Any read or open error makes the chat-log tailer re-read WoWChatLog.txt from byte 0 and replay the whole history
- File: `companion/needledropper/transport/chatlog.py:298`
- Detail: poll() handles an OSError from _poll_locked by closing the handle (282-287). On the next poll _fh is None, so it calls _open(from_start=True) (297-299), which sets the offset to 0 even though it is the same file with the same identity.

The same happens when the initial _open(from_start=False) in _begin (270-272) fails on an existing file (sharing violation, antivirus): the first successful open is then from the start.

Every NDROP1 record from earlier sessions is re-emitted. The engine only dedupes over 15 s, so each historical FIRE goes through _start_drop: Spotify play/stop churn and flashing cards, possibly for minutes. This breaks the module's documented contract: 'starts at the end of an existing file (no history replay)'.
- Verifier: The code path:
- poll() (chatlog.py:282-287) closes the handle on any OSError from _poll_locked, and _close() clears _ident.
- The next _poll_locked finds _fh None and calls _open(from_start=True) (297-299), which sets _offset=0 for the same file.
- A failed initial _open(from_start=False) in _begin (270-272) leads to the same from-the-start open.

Reproduced:
- The reviewer's chat_replay.py, re-run: one os.fstat OSError, and the next poll re-emitted [FIRE 10, HELLO 11, FIRE 12, HELLO 13].
- C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_cr\chat_replay_open.py: one PermissionError on the initial open of an existing log holding 50 old FIREs. The diag shows 'cannot open', then 'opened ... from the start', and the first poll emitted all 100 records, 50 of them FIREs.

The engine dedupes for only 15 s, so every historical FIRE that still resolves goes through _start_drop, causing Spotify play/stop churn and flashing cards. This breaks the module contract (chatlog.py:12 'no history replay') and SPEC.md:129.

Why medium: the risk comes from the impact, not from frequency. The channel is opt-in (Core.lua:144), and fstat or read errors on an already-open shared handle on local NTFS are rare.
- Fix: Keep the last (st_dev, st_ino) and _offset across a close triggered by an error; do not clear them in that path. On the next successful open:
- if the identity matches, resume at min(saved_offset, size);
- read from 0 only when the identity differs (the file was replaced) or the size shrank below the offset (truncation).
In _begin, when the file exists, record os.stat(path) identity and st_size first, and use them as the resume point if the initial open fails.

#### [low] CLI-CONSOLE-CLOSE-NO-CLEANUP: Closing the console window, a documented way to quit, skips all cleanup, so a playing Spotify drop is never paused or restored
- File: `companion/needledropper/cli.py:729`
- Detail: _run reaches app.stop() and deck.stop() only through KeyboardInterrupt (726-733). No console control handler is installed anywhere: neither cli.py nor launcher.py calls SetConsoleCtrlHandler or uses SIGBREAK.

Closing the console sends CTRL_CLOSE_EVENT. Python turns only Ctrl+C into KeyboardInterrupt, so the default handler ends the process with ExitProcess and no finally block runs (Microsoft docs, 'HandlerRoutine callback function', CTRL_CLOSE_EVENT). docs/INSTALL.md:96-97 says: 'Closing it, or pressing Ctrl+C in it, stops NeedleDropper.'

If a drop is playing on Spotify when the window closes, it keeps playing (then autoplay), the device volume can stay at the fade or per-drop level, and the user's previous playback is not restored.
- Verifier: _run reaches app.stop() and deck.stop() only through the finally that follows `except KeyboardInterrupt` (cli.py:727-733). No SetConsoleCtrlHandler or SIGBREAK handler exists anywhere in the companion: a grep finds only tray.py's atexit, which does not run on ExitProcess either. INSTALL.md:96-97 documents closing the window as a way to stop NeedleDropper.

Reproduced (C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_cr\ccx\parent.py with child.py). A child Python with the same try / sleep / except KeyboardInterrupt / finally structure ran in a new, hidden console, and WM_CLOSE was posted to its console window, which is what clicking X does:
- it exited immediately with code 3221225786 (0xC000013A), and the 'finally ran' marker was never written;
- a control child with a ctypes SetConsoleCtrlHandler received event 2 (CTRL_CLOSE_EVENT) and finished a 0.5 s cleanup before it was terminated.
So a Spotify drop playing at close keeps playing (then autoplay), and the volume and the user's previous playback are not restored.

Low: it only matters when a drop is playing, or the volume is faded, at the moment the window is closed.
- Fix: In cmd_run/_run, install a console control handler with ctypes SetConsoleCtrlHandler, keeping a module-level reference to the WINFUNCTYPE callback. Handle CTRL_CLOSE_EVENT (2), CTRL_LOGOFF_EVENT (5) and CTRL_SHUTDOWN_EVENT (6). The handler should, in order:
- run app.stop() synchronously, with the engine fade capped at about 1 s and Spotify pause plus volume and snapshot restore;
- run deck.stop();
- guard against running twice;
- return TRUE.
All of this must finish within about 5 s: Windows terminates the process once the handler returns, and the main thread's finally never runs.

#### [low] CHAT-UNICODE-RAISE: parse_chat_line raises UnicodeEncodeError on non-ASCII fields; in match=anywhere a player's chat line makes the tailer drop real records
- File: `companion/needledropper/protocol.py:132`
- Detail: CHAT_LINE_RE (line 23) uses \d and \S, which also match non-ASCII characters. body.encode('ascii') at line 132 sits outside the try block. In 'anywhere' mode chatlog._line passes every chat line to it (441).

UnicodeEncodeError is not an OSError, so poll() (282-287) does not catch it. The thread survives (_run catches Exception), but _read_new has already moved _offset past the chunk (398-400) and the local emitted list is discarded. A real FIRE flushed in the same client write as a player's 'NDROP1 ... é ...' line is lost. Strict mode is not affected, because other players' lines are rejected before parsing.
- Verifier: The code path:
- CHAT_LINE_RE (protocol.py:23) uses \d and \S without re.ASCII, so non-ASCII digits and ids match.
- body.encode("ascii") at protocol.py:132 is outside the try block and raises UnicodeEncodeError, which is a ValueError, not an OSError.
- In 'anywhere' mode, ChatLogTransport._line passes every NDROP1 line to parse_chat_line (441).
- poll() catches only OSError (284).
- _read_new has already advanced _offset (398) before _feed, so the rest of the chunk is never parsed, and records already added to the local emitted list are discarded.

Reproduced with the reviewer's chat_unicode.py, re-run with the venv: parse_chat_line('NDROP1 1 3 2 3 0 é 0000') raises, and so does an Arabic-Indic digit. In anywhere mode, a player line followed by a real FIRE in the same write gives: poll raised, events delivered [], offset 115, and the second poll returned 0. Strict mode delivered the FIRE.

Low: it only happens with match='anywhere' (the default is 'strict', config.py:88) and the opt-in chat-log channel. Anywhere mode already accepts records typed by other players by design.
- Fix: In protocol.py, compile CHAT_LINE_RE with re.ASCII, or use [0-9] and [!-~]. In parse_chat_line, return None when not body.isascii() before encoding, or catch UnicodeEncodeError. In ChatLogTransport._line, wrap the parse in try/except ValueError and count it as a bad line, so one line cannot abort the chunk.

#### [low] DECK-EVENTS-NO-ORIGIN: /events accepts token-less cross-site requests that carry no Origin header, so any web page can register a fake player
- File: `companion/needledropper/deck/server.py:221`
- Detail: _origin_ok treats a missing Origin header as allowed (219-221), and /events needs no token (317-325). Browsers send no Origin header on no-cors GET requests (img, audio, new Image().src). A page the user visits can therefore open http://127.0.0.1:8322/events?client=player; the Host header is valid.

_add_client counts that connection as a player (615-632), so page_open and connected become true. YouTubeProvider._ensure_player then skips opening the real player window, and LocalProvider sends its play command to the fake player. wait_status times out, and play() returns a handle anyway ('assuming it is starting'), so the engine never falls back and the drop is silent. Re-issuing the request every few seconds keeps the fake player present.

Chromium's Local Network Access prompt mitigates this in Chrome and Edge; Firefox has no such gate.
- Verifier: The code path:
- _origin_ok (deck/server.py:219-221) treats a missing Origin as allowed.
- /events requires no token (317-325).
- _add_client counts the connection as a player at once (615-632).
- connected keeps a 10 s grace after a disconnect (669-680).

Reproduced in-process (C:\Users\<user>\AppData\Local\Temp\claude\ad490dc250b63cb0\2f09d338-c3d1-4f2b-9a09-39854eb1d4ef\scratchpad\skeptic_cr\deck_events.py). A raw GET /events?client=player with a valid Host, no Origin, no token, Sec-Fetch-Site: cross-site and Sec-Fetch-Dest: image:
- returned 200;
- page_open and connected both became True;
- YouTubeProvider._player_open() returned True, so _ensure_player would not open the real window.
The same request with Origin https://evil.example got 403. Six seconds after the fake socket closed, page_open was False but connected was still True, so repeated requests bridge any early abort by the browser.

Per the Fetch spec, no-cors GETs (img, audio) carry no Origin header.

Low:
- It is a denial of service only: drops go silent while the real player window is not open, and the opaque response is unreadable.
- It needs a hostile page aimed at this app.
- Chromium 142+ Local Network Access prompts gate public-to-loopback subresource requests.
- Fix: For /events (and /api), reject requests whose Sec-Fetch-Site header is present and is not 'same-origin' or 'none'. Tools that send no Sec-Fetch-* headers keep working.

Stronger alternative: require the page token as a query parameter (?t=<token>), which player.js and panel.js read from the served page, since EventSource cannot send custom headers.

Do not require an Origin header: a same-origin EventSource GET does not send one, so that would break player.js:452 and panel.js:619.

## Lens: director-behaviour

Refuted (dropped): 1

### Confirmed (7)

#### [low] ND-DIR-01: /reload in the middle of a drop: the companion stops the song about 5 s later and the drop stays marked heard
- File: `D:/NeedleDropper/addon/NeedleDropper/Director.lua:31`
- Detail: D.active is kept only in memory (Director.lua:31). D.Fire has already persisted done, the journal and lastFireAt (335-341). After a /reload, the PLAYER_ENTERING_WORLD handler emits HELLO with flags=0 (599-602), and nothing records that a drop is playing. The engine treats a HELLO without DROP_ACTIVE as stale and stops the current drop after 5 s (companion/needledropper/engine.py:285-288, 371-377; SPEC §5.3 HELLO rule).

Both components follow the spec text. The gap is that §4.3 never persists the active drop. A routine /reload during the 2.5 to 6.5 minutes of a first-arrival song cuts it off. The companion itself tells users to "type /reload in game" after build-map. Because the drop is already "played" it never fires again; only /needle replay brings it back. PlayMusic also stops on UI reload (warcraft.wiki.gg API_PlayMusic), so zone music returns over the companion's song during those 5 s.
- Verifier: Code: D.active lives only in memory (D:/NeedleDropper/addon/NeedleDropper/Director.lua:31) and CHAR_DEFAULTS has no active record (Core.lua:283). D.Fire has already persisted done, journal and lastFireAt before StartActive (Director.lua:335-341). The PLAYER_ENTERING_WORLD handler (Director.lua:595-603) ignores isReloadingUi and emits HELLO. ComputeFlags (53-70) adds no DROP_ACTIVE because D.active is nil. The engine starts the stale timer on a HELLO without DROP_ACTIVE (D:/NeedleDropper/companion/needledropper/engine.py:371-377) and stops the drop more than 5 s later (285-288).

Repro: re-ran C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k6_reload.py.
- After the reload the addon emits one HELLO (flags=0), Director.active=nil and Audio.ducked=nil.
- The engine reports playing=True at +1 s and +4 s and False at +6 s.
- The last provider call is ('stop','zone:1429',3000).

Primary source: warcraft.wiki.gg API_PlayMusic says "The playback loops until it is stopped with StopMusic(), when the user interface is reloaded, or upon logout". So the silence duck is lost too and zone music returns.

Assessment: each component follows SPEC.md:98 (HELLO = addon loaded/reloaded) and SPEC.md:261 (stale rule). But SPEC.md:209-210 lists how an active drop ends or is cancelled, and a reload is not among those reasons, yet the drop stays marked played (how="played").

Severity is low: it needs a /reload during a 2.5-6.5 min song, and /needle replay (journal) brings the song back.
- Fix: Persist the active drop and restore it after a UI reload.
1. In D.StartActive, save ns.cdb.active = {id=drop.id, isTest=isTest, isLocal=isLocal, handle=handle, endsAtWall=time()+length}. Clear it in D.StopActive.
2. Give Director's PLAYER_ENTERING_WORLD handler its (isInitialLogin, isReloadingUi) arguments.
3. Restore the drop before the first HELLO when isReloadingUi is true, ns.cdb.active exists, time() < endsAtWall, the record is not isLocal, and the id resolves (ns.Map.byId[id], or ns.TEST_DROP for "test"). Then:
   - set D.active = {drop, startedAt=now(), endsAt=now()+(endsAtWall-time()), isTest}, without MarkDone, journal or lastFireAt;
   - call ns.Audio.Duck({}) again, because the reload stopped PlayMusic;
   - emit the HELLO, which now carries DROP_ACTIVE. EndActive will then run at the original end time.
4. Otherwise clear the record. For isLocal records, call pcall(StopSound, handle, 3000) as a best effort and treat the drop as ended. On isInitialLogin, always clear the record.
5. Add a harness test: FIRE, logout, then a new AddonHarness with the saved DBs and login(initial=False). The first HELLO must carry DROP_ACTIVE, and a HELLO without it must appear only at the original end time.

#### [low] ND-DIR-02: A drop that fires right after another ends starts inside the addon's own 0.1 s Sound_EnableMusic=0 kick window
- File: `D:/NeedleDropper/addon/NeedleDropper/Director.lua:410`
- Detail: D.Evaluate calls CheckActive, then EndActive (384), then Audio.Restore. Restore calls StopMusic and KickZoneMusic, which sets Sound_EnableMusic=0 and only sets it back to 1 after 0.1 s (Audio.lua:47-51). The same Evaluate then fires the pending drop (410). StartActive then calls Duck, i.e. PlayMusic(silence) (319), and PlayLocal, i.e. PlaySoundFile(file, "Music") (322), while music is disabled.

Triggers:
- (a) A manual play (/needle test, play or replay) ends naturally while a first-visit drop is pending and the cooldown is clear. ND-DIR-03 is the same-song case of this.
- (b) A user-lowered cooldown: shipped songs are 147 to 396 s against the 600 s default cooldown, and a drop is pending when the song ends.
- (c) cooldownMin = 0.

Documented consequence: warcraft.wiki.gg API_PlaySoundFile says willPlay is "nil otherwise (prevented by a muted sound channel)", and the same page lists Sound_EnableMusic as the Music channel's toggle. So the in-game file does not play. Audio.PlayLocal returns false, LOCAL_PLAYBACK is cleared and the drop falls to the companion (card only if the companion has no source), yet it stays marked heard.

Inferred, client-dependent: the silence duck is requested on a disabled channel. Whether the client keeps it when the kick re-enables music, or restarts zone music under the drop, is unverified.
- Verifier: Call order (verified):
1. D.Evaluate calls D.CheckActive, which calls EndActive (D:/NeedleDropper/addon/NeedleDropper/Director.lua:375-386).
2. EndActive calls StopActive(true) (345-352), which calls Audio.Restore (Audio.lua:56-66).
3. Restore calls StopMusic and then KickZoneMusic. KickZoneMusic sets Sound_EnableMusic="0" (Audio.lua:47) and sets it back to "1" only in a 0.1 s C_Timer (48-52).
4. The same Evaluate then reaches D.Fire (Director.lua:410). StartActive calls Duck, i.e. PlayMusic(silence) (319), and PlayLocal, i.e. PlaySoundFile(file,"Music") (322 / Audio.lua:73), while the CVar is "0".

Repro: re-ran C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k1_kick_window.py.
- K1a trace: SetCVar 0, then PlayMusic [EnableMusic=0], then PlaySoundFile [EnableMusic=0], all at t=37.000; SetCVar 1 at 37.100.
- With the muted-channel model, the FIRE has flags=32 (no LOCAL_PLAYBACK) and active.isLocal=false.
- K1b (cooldownMin 3, 240 s song) shows the same window for a drop the companion plays.

This breaks the project's own rule in SPEC.md:189 and Audio.lua:5-6: setting Sound_EnableMusic=0 during a drop mutes in-game drops.

Primary sources:
- PlaySoundFile and PlayMusic are not in the forever generated API docs, so warcraft.wiki.gg API_PlaySoundFile is the source: willPlay is "nil otherwise (prevented by a muted sound channel)", and the Music channel's toggle is Sound_EnableMusic.
- The forever file Blizzard_SharedXML/Mainline/Sound.lua:5-16 uses that same CVar as the music on/off toggle.

Why low:
- The documented failure needs an in-game-file drop; the shipped Map.json has 0 drops with `file`.
- It also needs a back-to-back hand-over: a /needle play, replay or test ending while a first-visit drop is pending with the cooldown clear, or a cooldown shorter than the song (shipped songs are 147-396 s against the 600 s default).
- What the client does with PlayMusic(silence) requested while music is disabled is unverified and is not counted here.
- Fix: Never start a drop inside the kick window.

Minimal fix, in Audio.lua:
- Add A.kickToken. KickZoneMusic increments it, and its C_Timer callback returns early if the token changed.
- Add A.FinishKick(): if A.kickPending, increment kickToken, set kickPending=false, call ns.SetCVar("Sound_EnableMusic","1") and ns.ForgetCVar("Sound_EnableMusic").
- Call ns.Audio.FinishKick() at the top of D.StartActive, before Duck and PlayLocal.

Better fix: when D.CheckActive ends a drop and the armed drop passes D.CanFire in the same Evaluate, end the old drop with StopActive(false) (no Restore and no kick), so StartActive keeps the duck across the hand-over.

Test: add a shim case where PlaySoundFile(...,"Music") returns nil while Sound_EnableMusic=="0", and a /needle test ends while an in-game-file drop is pending. The FIRE must carry LOCAL_PLAYBACK.

#### [medium] ND-DIR-03: `/needle play` on the pending drop plays the same song twice; `/needle skip` then prints a false message
- File: `D:/NeedleDropper/addon/NeedleDropper/Director.lua:452`
- Detail: PlayManual (452-459) leaves D.armed alone; the armed first-visit drop only waits for "no drop currently active". Four consequences:
1. When the manual play ends, EndActive emits HELLO and the same Evaluate fires the identical drop as its first-visit FIRE. The same song plays twice back-to-back, and the second time happens inside ND-DIR-02's kick window.
2. `/needle stop` during the manual play prints "stopped Westfall." and the same song restarts within a second.
3. `/needle skip` prints "skipped Westfall (it stays marked as heard)." (517), but nothing is marked because the play is a test. About 2.4 s later the drop fires again.
4. Leaving the zone during the manual play makes Disarm emit CANCEL for the armed n (304-310). That n equals the playing n, so the engine stops the manual play (engine.py:411-424). The addon still reports DROP_ACTIVE until endsAt, which contradicts "Leaving the zone does not stop it".
Pack authors who stand in a place and test its song (the SPEC-v1.2 /needle mark workflow) hit this directly.
- Verifier: Root cause: D.PlayManual (D:/NeedleDropper/addon/NeedleDropper/Director.lua:452-459) never touches D.armed. D.Evaluate (406-410) therefore keeps the same armed drop, waiting only for "no drop active".

Repro: re-ran C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k4_manual.py and C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k4b.py.
1. Same song twice: the TEST FIRE n=5 (flags=96) at 7.60 is followed at 38.00 by HELLO and a first-visit FIRE n=5 (flags=32). The second FIRE also lands in ND-DIR-02's kick window.
2. Stop restarts it: 'stopped Westfall.' is printed, then CANCEL at 16.60 and FIRE at 17.00.
3. Skip prints a false message: 'skipped Westfall (it stays marked as heard).' (517) while done['zone:1436'] is None; the drop then fires (flags=32) at 15.00.
4. Leaving the zone stops the song: Disarm (304-310) emits CANCEL n=5 (TEST|DROP_ACTIVE). engine.py:404 already removed the ARM entry on the TEST FIRE, so engine.py:411-422 matches cur.drop.n==ev.n and stops the song with ('stop','zone:1436',3000), while the addon still reports DROP_ACTIVE.

My C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/skeptic_dir/c_skip_msgs.py adds two cases:
- The drop is pending behind 4m48s of cooldown. /needle play then /needle skip prints the same message, D.armed is still zone:1436, done is None, and the drop fires as a first visit at 305 s.
- /needle skip during /needle test prints 'skipped NeedleDropper Test (it stays marked as heard).'

This contradicts:
- TROUBLESHOOTING.md:192: "mark the pending drop as heard so it never plays";
- SPEC.md:209: "Leaving the zone does not stop it".

The audition workflow is documented in SOUNDTRACK.md:453 and MIXTAPE.md:111. Severity is medium: two commands do the opposite of what they print and a playback is cut, all in a documented workflow; no data is lost.
- Fix: 1. In D.PlayManual: if D.armed and D.armed.drop.id == d.id, set D.armed = nil and D.suppress[d.id] = D.EntryTime(d). Do not emit CANCEL; engine _on_fire already dropped its ARM entry.
2. In D.Disarm: never emit the ARM-abandon CANCEL when D.active and D.active.drop.n == a.drop.n. This part is required. engine.py:404 removes the ARM entry on every FIRE, so the companion cannot tell an abandon-CANCEL from a stop-CANCEL for the same n.
3. In D.Skip, when D.active.isTest:
   - if not D.IsDone(d), call D.MarkDone(d, "skipped"), clear any armed or suppress state for d, and print "skipped X; it will not play on this character";
   - print "(it stays marked as heard)" only if D.IsDone(d) was already true.
4. D.Stop of a manual play of the armed drop then leaves it suppressed for this visit, through step 1.
5. Add harness tests for the play-then-end, play-then-skip, play-then-leave and cooldown-pending cases.

#### [low] ND-DIR-06: Title card overlaps Blizzard's subzone banner on discovered landmarks
- File: `D:/NeedleDropper/addon/NeedleDropper/Toast.lua:340`
- Detail: A discovered landmark fires 1.5 s after the subzone event (Director.lua:20 LANDMARK_CHECK_DELAY, and DwellState returns 0 on discovery at 248). Blizzard's banner for that event is still on screen (forever branch):
- ZoneText.lua:2-4 sets fade in 0.5 s, hold 1.0 s, fade out 2.0 s, so the banner is visible until +3.5 s.
- ZoneText.xml:6,8,14 puts ZoneTextFrame's top at y=-128 with size 128. ZoneTextString uses a 32 px font (SystemFont_OutlineThick_WTF) and is centred at y=-192, spanning about -176 to -208.
- ZoneText.xml:41 anchors SubZoneTextString (26 px, SystemFont_OutlineThick_Huge4) under ZoneTextString, spanning about -208 to -234.
At the default toastY=0.22 (Core.lua:140) and a UIParent height of 768 (UI scale 1.0, also the shim's value), the card spans -169 to -233.5: the same band. Both are clearly visible together between +1.5 and +3.5 s, e.g. card alpha 0.36 against banner alpha about 0.75 at +2.0 s.
The overlap depends on UI scale: it occurs while 0.22 x UIParent height is under about 234, i.e. UI scale above about 0.72. Zone and instance drops (3 s or more) only touch the banner's faded tail.
- Verifier: Primary source: Gethe/wow-ui-source, forever branch. I checked ZoneText.xml on GitHub and against the local 1.60.1.70790 copy at C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/skeptic_tempcvar/dl_forever/wow-ui-source-forever.
- ZoneTextFrame is anchored TOP at y=-128 with size 128 (ZoneText.xml:6,8). ZoneTextString is centred in it (line 14), so its centre is at -192. Its font, ZoneTextFont, is SystemFont_OutlineThick_WTF at 32 px (GameFontStyles.xml:123, GameFonts.xml:173-176).
- SubZoneTextString hangs from ZoneTextString's bottom (ZoneText.xml:41). Its font is Huge4 at 26 px (GameFontStyles.xml:126, GameFonts.xml:155-158), so it spans about -208 to -234.
- FadingFrame (FadingFrame.lua:28-49) uses the 0.5 / 1.0 / 2.0 s timings from ZoneText.lua:2-4.
- SubZoneTextFrame is shown on every subzone ZONE_CHANGED (ZoneText.lua:111-119).

The card layout (D:/NeedleDropper/addon/NeedleDropper/Toast.lua:322-345) at toastY 0.22 (Core.lua:140), with a UIParent height of 768 (default, no UI scale), spans -169 to -233.5. That is the same band.

My C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/skeptic_dir/b_card_timing.py:
- area:87 fires at +1.51 s; at +2.0 s the card is at alpha 0.36 and the banner at 0.75.
- The finding understates by-name landmarks and fresh mode: sub:1436:sentinel-hill fires at +0.50 s, and at +1.5 s the card is at alpha 0.93 while the banner is still at 1.00.

Cosmetic and lasting about 2 s, but it happens on every discovered landmark at default settings. It depends on UI scale: there is no overlap once 0.22 × UIParent height exceeds about 234.
- Fix: Delay the card while Blizzard's banner is still up.
- In Toast.Show, pcall-guarded: for each of SubZoneTextFrame and ZoneTextFrame that IsShown(), take FadingFrame_GetRemainingTime(frame) (a global in Blizzard_FrameXMLUtil/FadingFrame.lua:51), and set wait to the largest.
- Start the card timeline after `wait` (C_Timer.After, or c.start = GetTime() + wait).

This works at any UI scale. Raising the default toastY to 0.31 or more only clears the band for UIParent heights of about 755 or more.

#### [low] ND-DIR-07: Account scope plus "respect history": one character's history suppresses drops for every character
- File: `D:/NeedleDropper/addon/NeedleDropper/Director.lua:113`
- Detail: RunHistoryScan (135-139) calls MarkDone(d, "history"), and MarkDone always writes accountDone too (113). With scope="account", IsDone reads accountDone (103-105). So the veteran main's first login marks every zone it ever explored as heard for the whole account. An alt arriving in a zone for the first time gets no drop, and /needle where reports "(heard)" even though no character ever heard it.
The setting's own text says "Respect: skip places this character already explored" (Core.lua:162-164). A user who switches an existing install from character to account scope also inherits every history mark, because they were written to accountDone under character scope as well.
- Verifier: Code: MarkDone (D:/NeedleDropper/addon/NeedleDropper/Director.lua:110-114) writes accountDone for every kind of record. That includes the "history" records from RunHistoryScan (135-140) and RunLandmarkCheck (178-179). With scope=account, D.IsDone reads only accountDone (103-105).

Repro: re-ran C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k7_scope.py.
- The main's login scan writes accountDone {zone:1436: history, zone:1429: history}.
- A level-5 alt stays 30 s in Westfall and emits only HELLO. Its own done is {}, and /needle where reports '(heard)'.

The user-facing text says history is per character:
- Core.lua:162: "Respect: skip places this character already explored";
- TROUBLESHOOTING.md:215: "places this character already explored count as heard";
- TROUBLESHOOTING.md:63 explains account scope as "another character already heard it".

SPEC.md:207/211 ("not done (per scope)", "mark done how=history") does not distinguish the two cases, so either the behaviour or the text is wrong. With the default respect mode, an account-scope veteran never hears any zone that their first logged-in character explored, on any character. Severity is low because account scope is opt-in.
- Fix: Pick one of two fixes.

(a) Keep history per character:
- MarkDone(d, "history") writes only ns.cdb.done.
- In account scope, D.IsDone = (accountDone[id] and accountDone[id].how ~= "history") or (ns.cdb.done[id] and ns.cdb.done[id].how == "history").
- Ignore existing accountDone records whose how is "history".

(b) If account-wide history is intended, change the option label and tooltip (Core.lua:161-164) and TROUBLESHOOTING.md:63 and :215 to say that in account scope, places any of your characters explored count as heard.

#### [low] ND-DIR-08: Landmark history check marks the landmark scanned even when it got no data
- File: `D:/NeedleDropper/addon/NeedleDropper/Director.lua:173`
- Detail: RunLandmarkCheck sets cdb.scanned[d.id] before asking the API (173). ExploredAreasHere returns nil in two different cases: when the player position is unavailable (Discovery.lua:195; GetPlayerMapPosition's position is Nilable in the forever MapDocumentation) and when the API returns nothing (197; GetExploredAreaIDsAtPosition is Nilable). When the position is unavailable, the landmark is treated as new and never checked again, so a veteran who explored it long ago hears it, now or on a later visit.
RunHistoryScan, by contrast, only sets scanned when ExploredZone ~= nil (135-137).
- Verifier: Code:
- D:/NeedleDropper/addon/NeedleDropper/Director.lua:173 sets ns.cdb.scanned[d.id] before the lookup.
- Discovery.ExploredAreasHere returns nil in two cases: when the position is unavailable or secret (Discovery.lua:194-195), and when the API returns nothing (196-197).
- RunLandmarkCheck then returns false ("new"), and needsLandmarkCheck (Director.lua:166-169) never runs the check again.
- RunHistoryScan, in contrast, sets scanned only when ExploredZone ~= nil (135-137).

Primary source (forever branch):
- MapDocumentation.lua:437-451: GetPlayerMapPosition returns a position with Nilable=true.
- MapExplorationDocumentation.lua:11-24: GetExploredAreaIDsAtPosition returns areaID with Nilable=true.

SPEC.md:211 runs the landmark check "on entering the subzone". Its "No evidence ⇒ treat as new" covers the current visit, not skipping the check on later visits.

Repro: re-ran C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k2_misc.py (E3).
- With noPosition, scanned=True and done is None.
- area:87 fires at 9.00 although Goldshire is in exploredAreas.
- The second-visit variant also fires, with no re-check.

Severity is low: it needs a nil or secret player position about 1.5 s after a subzone change, while GetBestMapForUnit already works, and that is not shown on a real client.
- Fix: - Make Discovery.ExploredAreasHere return false when the position (or the call) is unknown, and an empty set when GetExploredAreaIDsAtPosition returns nil.
- In D.RunLandmarkCheck, check `if areas == false then return false end` before touching scanned.
- Set ns.cdb.scanned[d.id] = true only after a known answer, so later Evaluates and visits retry. During this visit the drop is still treated as new while the answer is unknown.
- Location.Describe's `if areas then` stays correct, because false is falsy.

#### [low] ND-DIR-09: Subzone-retry chain backdates the arrival time for a zone entered just after login
- File: `D:/NeedleDropper/addon/NeedleDropper/Location.lua:123`
- Detail: Within 10 s of login, an empty subzone text starts up to 3 retries that carry the original `since` (96-98, 120-124). If the player crosses into another zone during that window, the retry's Refresh reads the new zone and stamps zoneEnteredAt/subEnteredAt with the login time (103, 142-144). It also clears pendingSince for the debounce already in flight, so the real arrival time is lost.
Impact is bounded: dwell is shortened by at most about 3 s, and only within about 10 s of login.
- Verifier: Code:
- Up to 3 subzone retries carry the original `since` (D:/NeedleDropper/addon/NeedleDropper/Location.lua:96-98, 120-124).
- Refresh (101-104) uses that since and clears pendingSince.
- A zone or subzone change read by a retry is therefore stamped with the login time (142-144), and the debounce already in flight then sees no change.

Repro:
- Re-ran C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k3_travel.py (E4): a crossing at 1.0 s gets zoneEnteredAt 0.0, and FIRE comes at 10.00.
- My 0.1 s sweep, C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/skeptic_dir/a2_since_sweep.py, backdates crossings at 1.0-1.5, 2.0-2.5 and 3.0-3.5 s after PLAYER_ENTERING_WORLD (start inclusive, end exclusive). These are the 0.5 s before each retry, when the retry runs before the debounce. The largest backdate is 3.4 s; crossings at 1.6 s or 3.6 s are stamped correctly.

This contradicts the dwell rule in SPEC.md:207 (now - zoneEnteredAt). The impact is trivial: dwell is shortened by at most about 3.5 s, and only within about 4 s of login.

The landmark variant (C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/skeptic_dir/a_since_window.py) is moot, because the 3 s history-scan gate (Director.lua:152-164) holds the FIRE anyway.
- Fix: Carry `since` only while it still describes the same place.
- Have the subzone retry also pass the zoneMapID, inInstance and instanceID it was read with.
- In L.Refresh, use the carried since only if those three are unchanged in the new snapshot; otherwise use `pendingSince or now`.
- Keep the carried since for the nil-map retry.

Simpler alternative: call scheduleRetry("subzone-retry", nil), accepting that subEnteredAt at login can be up to 3.5 s late.

### Uncertain (1)

#### [low] ND-DIR-04: `/needle stop` or `/needle skip` on a pending drop fires a different drop within a second
- File: `D:/NeedleDropper/addon/NeedleDropper/Director.lua:505`
- Detail: Stop (503-507) suppresses only the armed drop for this entry; Skip (518-521) only marks it skipped. The next Evaluate, within the 1 Hz tick, picks the next candidate (landmark first, then zone). That drop's dwell has been accruing since zone entry, so it is ARMed and FIREd at once, with a title card.

The same happens if `/needle stop` is typed during the 1.5 s landmark history check. The zone drop is suppressed, the landmark ARMs 0.9 s later and fires. The user asked for quiet ("let X go for now") and gets a song immediately.

This follows §4.3's candidate priority literally, but contradicts §0 ("silence is the default") and the command's own message.
- Verifier: The reproduction holds, but nothing in the spec or docs says it is wrong.

Repro: re-ran C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k2_misc.py (E5/E5b) and C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/exp/k8_stop_checking.py.
- After /needle stop or skip on a pending area:87, the next tick ARMs and FIREs zone:1429 (t=22.00).
- /needle stop during the 1.5 s landmark check suppresses zone:1429, and area:87 fires at 13.00.

Why it is not clearly a defect:
- The code (D:/NeedleDropper/addon/NeedleDropper/Director.lua:503-507, 518-521; PickCandidate 276-296) does exactly what TROUBLESHOOTING.md:191-192 promises for the named drop: "let the pending drop go for now: it comes back on your next visit" and "mark the pending drop as heard so it never plays".
- The other drop is independently eligible under SPEC.md:205-207 candidate priority.
- For skip, moving on to the next drop is a natural reading.
- No spec or doc line promises silence after stop.

With default settings the window is narrow. Either a zone drop shadowed by a landmark must already have its 10 s dwell and a clear cooldown, and stop must be typed within about 2.5 s between the landmark check and the 4 s landmark dwell; or stop must be typed inside the 1.5 s check. Whether stop should quiet the whole visit is a product decision.
- Fix: Product decision; no defect is established.

If stop should mean "quiet here for now":
- In D.Stop's pending branch, and when PickCandidate reports "checking", record D.quiet = {zone = st.zoneEnteredAt, sub = st.subEnteredAt}.
- Make PickCandidate return nil while both entry times are unchanged.
- Print "no needle drops here until you move on".
- Keep skip as it is, or apply the same rule.
