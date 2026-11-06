# Primed, no-focus-steal YouTube playback (Track 3 prototype)

**Problem (owner, real-client notes §8).** The stand-in's YouTube fallback opened a browser tab,
alt-tabbed the owner out of WoW, and started late. The product needs a YouTube player that
(a) appears top-right, always-on-top, visible (YouTube's rule, >= 356x200 CSS px), (b) **never**
takes keyboard focus or foreground from the game, (c) starts audio within ~300 ms of FIRE because
the video was primed on ARM ~10 s earlier, and (d) hides when the drop ends.

**Verdict: achievable, with one structural change.** The focus steal the owner hit is caused by
creating a browser window *per drop*. Chromium activates its window once at creation and no flag
prevents it. The fix is to **create one long-lived player window at companion startup** and then,
for every drop, only **show/hide and reposition it with `SetWindowPos(..., SWP_NOACTIVATE)`** — an
operation that provably never activates. With the video *warm-primed* on ARM, FIRE-to-audio is
~260 ms.

All claims below were measured on this machine on 2026-11-05 unless marked **[inferred]**. Raw
trial logs (JSON) are under
`C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/focus-proto/results/`
and the prototype scripts (`w32.py`, `monitor.py`, `proto_server.py`, `trial.py`, `cleanup.py`) next to them.

**Test environment.** WoW: Forever 1.60.1.70790 running (borderless), single 3840x2160 monitor at
**300 % DPI (effective scale 3.0)**, Windows 11 Pro 26300. Edge 156.0.4314.58 at the given path,
Chrome 136.0.7103.48. Python 3.14 stdlib + ctypes only. Across all trials the foreground window was
the desktop app / terminal (never WoW); **1 of 6 on-screen trials were used**; every window opened
was muted, on screen <= ~3.6 s, and closed; `fg_back-to-start = True` after every run.

---

## 1. The recipe

### 1.1 Launch (once, at companion start — not per drop)

Launch the configured Chromium browser as an app window, pointed at the local player page, **hidden
off-screen**, with autoplay allowed and background throttling disabled:

```
<browser.exe>
  --app=http://127.0.0.1:<port>/player
  --user-data-dir=<dedicated profile dir>       (autoplay-with-sound is per-profile)
  --autoplay-policy=no-user-gesture-required
  --no-first-run --no-default-browser-check
  --window-size=<W>,<H>                          (DIP; see §4 geometry)
  --window-position=<offscreen x>,<y>            (computed from EnumDisplayMonitors, see §4)
  --disable-background-timer-throttling          (REQUIRED, see §3.5)
  --disable-backgrounding-occluded-windows       (REQUIRED)
  --disable-renderer-backgrounding               (REQUIRED)
  --mute-audio                                   (test only; omit in production)
```

Use `CreateProcess` with `CREATE_NEW_PROCESS_GROUP`, stdio to DEVNULL (the existing
`_default_launcher` is fine). **Do not** bother with `STARTUPINFO` / `STARTF_USESHOWWINDOW` /
`wShowWindow` — see §3.1, they do not prevent activation.

The player page (`player.html`) creates its `YT.Player` at page load, so the ~1 s `iframe_api`
fetch is paid during this startup, not at the first drop.

### 1.2 Find and tame the window

- **Find it by process tree, not title.** Enumerate top-level windows
  (`EnumWindows`), keep `class == "Chrome_WidgetWin_1"` whose owning pid
  (`GetWindowThreadProcessId`) is the launched process or a descendant of it. Do **not** match on
  the window title: the deck rewrites `document.title` per drop. (The prototype matched a nonce in
  the title only because it controls the page; production should use the pid.)
- **Restyle it the instant it is found**, with `SetWindowLongPtrW(GWL_EXSTYLE, ...)`:
  add `WS_EX_NOACTIVATE (0x08000000)` and `WS_EX_TOOLWINDOW (0x00000080)`, remove
  `WS_EX_APPWINDOW (0x00040000)`. `NOACTIVATE` stops click/`ShowWindow` activation; `TOOLWINDOW`
  minus `APPWINDOW` keeps it off the taskbar and Alt-Tab.
- **Hide it** with `SetWindowPos(hwnd, NULL, 0,0,0,0, SWP_HIDEWINDOW|SWP_NOACTIVATE|SWP_NOMOVE|SWP_NOSIZE|SWP_NOZORDER)`.
- The one activation Chromium does at creation (§3.1) happens here, at startup, and bounces back
  within ~0.2-0.7 s; after this the window never activates again.

### 1.3 On ARM (~10 s before FIRE): warm-prime

Send the page a prime command. The page, on the hidden window:
`mute()` -> `loadVideoById({videoId, startSeconds})` -> on the **first** `PLAYING` state change,
`pauseVideo()`. This plays ~5-9 ms of muted video while hidden and buffers it (loaded fraction
reaches 1.0). The video is then paused, muted, at `startSeconds`, fully buffered.

`cueVideoById` is the ToS-cleaner alternative (it never plays) but it does **not** pre-buffer, so it
misses the 300 ms target (§3.2). The ~5-9 ms of muted hidden playback is the price of hitting 300 ms;
flag it for the integrator as a YouTube-ToS grey area (see §3.3).

### 1.4 On FIRE: show, then play

1. **Show**: `SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE|SWP_SHOWWINDOW|SWP_NOOWNERZORDER)`
   with the top-right rect (§4). Measured ~5-40 ms, never activates.
2. **Play**: tell the page to `unMute()`, `setVolume(v)`, `playVideo()`.

Measured FIRE(page)->audio-advancing ~260-271 ms when warm-primed; the show call adds ~5-40 ms if
you show at FIRE rather than at ARM.

### 1.5 On drop end / CANCEL: hide

Fade out (existing page logic), then `SetWindowPos(hwnd, NULL, 0,0,0,0, SWP_HIDEWINDOW|SWP_NOACTIVATE|...)`.
The window is hidden (truly not visible), paused, ready to be re-primed for the next drop. Keep the
process and window alive for the whole session.

### 1.6 If the user closes the window (the "X")

Closing destroys the window; reopening it is a *creation* and costs one activation again, and worse,
Chromium's singleton may hand the relaunch to the still-dying old process. So: detect the player
page disconnecting, **wait for the browser process to exit** (measured ~100-200 ms after `WM_CLOSE`),
then relaunch lazily at the next ARM and accept the single startup-style activation. Alternatively,
drop the "you can close this" affordance for the player window.

---

## 2. Measured numbers

From `results/T1..T4` (off-screen) and `results/T3` (on-screen). "page fire->advanced" = wall time
from the page receiving the FIRE command to `getCurrentTime()` advancing >50 ms (the closest
in-page proxy for first audio; YouTube's audio pipeline is cross-origin and cannot be tapped
directly).

| Metric | Measured |
|---|---|
| Launch -> player page `onReady` (warm profile) | ~530-600 ms; cold profile first run ~850-1050 ms |
| `cueVideoById` -> cued (state 5) | ~120-190 ms |
| `loadVideoById` -> first PLAYING (warm, hidden) | ~185-510 ms (buffers to fraction 1.0) |
| Warm hidden playback before pause | ~5-9 ms (paused currentTime == startSeconds + ~0.006 s) |
| **FIRE(page) -> audio advancing, warm-primed** | **~260-271 ms** (meets 300 ms; T2 f1/f2, T3 f0/f1, T4 f0/f1) |
| FIRE(page) -> audio advancing, cold cue (never buffered) | ~470-700 ms (misses 300 ms) |
| FIRE(page) -> audio advancing, cue after prior buffering | ~178-221 ms |
| `SetWindowPos` show call | ~5-40 ms |
| Browser working set | ~420 MB idle, ~560-800 MB during playback (Chromium multiprocess) |
| Window close -> browser process exit | ~100-200 ms |

**Timing scope / honesty.** These are page-clock numbers. In the real system "FIRE" is the moment
the **companion decodes** the FIRE beacon, and the 4 Hz screen capture plus the two-identical-reads
acceptance rule (DESIGN §2.3) add **~250-500 ms [inferred]** *before* the page ever hears FIRE. So
the companion-decode-to-audio budget is tight: warm priming is needed, and the chat-log transport
(lower latency than the beacon) helps when available. All audio-timing was measured with
`--mute-audio`; opening a real WASAPI render stream on `unMute()` is **unmeasured** and may add tens
of ms. (The T1 `fire2` 10.5 ms reading and the `fire1` timeout were artifacts of priming `warm@40`
on a 19 s test video — start past the end, which YouTube clamps to 0 — not recipe behavior, and are
excluded from the table above.)

---

## 3. What failed, and why

### 3.1 STARTUPINFO show flags do **not** prevent activation — dropped from the recipe
Launching with `STARTF_USESHOWWINDOW` + `SW_SHOWMINNOACTIVE` (7) or `SW_SHOWNOACTIVATE` (4) does not
stop Chromium activating its window. In T0 the window came up `iconic=True` yet still produced an
`EVENT_SYSTEM_FOREGROUND` to itself. `wShowWindow` was not even honored consistently (iconic once,
not iconic three times). Chromium decides its own initial show state.

### 3.2 `WS_EX_NOACTIVATE` does **not** block Chromium's startup `SetForegroundWindow`
Applying `WS_EX_NOACTIVATE` at window-create time (T0b, applied at 316 ms, before first visible
show) still produced a foreground event at 331 ms. `WS_EX_NOACTIVATE` blocks activation from mouse
clicks and from `ShowWindow`, but **not** an explicit `SetForegroundWindow` call, which is what
Chromium does on browser startup. This is why the window must be created once (eating the one
activation at startup) and only shown/hidden thereafter.

### 3.3 `cueVideoById` cannot hit 300 ms
A cued video reaches the "cued" state fast (~125 ms) but deliberately does **not** download media
until `playVideo`/`seekTo`. FIRE on a never-buffered cued video measured ~470-700 ms. There is no
IFrame-API way to pre-buffer a cued video while keeping it paused: `seekTo(..., true)` on a cued
player *starts playback* **[per IFrame API docs; not run here]**. So "primed and paused via cue"
(DESIGN §8's wording) and "<300 ms" are not
simultaneously achievable. **Warm priming** (load -> pause on first PLAYING) is the only way to both
pre-buffer and hit 300 ms, at the cost of ~5-9 ms muted hidden playback.

### 3.4 `LockSetForegroundWindow` is not a useful instrument here
It was used in the prototype as a probe. Note for integrators: a foreground lock held by a process
does **not** block that process's own children, and the production companion will usually not hold
foreground rights anyway (§5.1). Do not build on it.

### 3.5 Removing the anti-throttle flags breaks hidden priming
T6 (`--no-throttle-flags`): with the window hidden and no
`--disable-background-timer-throttling` / `--disable-backgrounding-occluded-windows` /
`--disable-renderer-backgrounding`, `loadVideoById` on the backgrounded page **never reached PLAYING**
(warm prime produced nothing) and the subsequent FIRE never advanced (8 s timeout). These flags are
a hard requirement for the hidden-window design. (The alternative — keep the window visible during
ARM so it is not "occluded" — means showing the idle player card ~10 s before the drop; a product
choice.)

### 3.6 Edge is hijacked on this machine
`msedge.exe` has an IFEO `Debugger` hook
(`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\msedge.exe\0\Debugger`
= `C:\Program Files (x86)\Wedge\wedge.exe`, an open-source "Edge Deflector"), and the default browser
is Chrome. Launching `msedge.exe --app=...` produced **no msedge or chrome process and no player
window** (T0-edge, and a dedicated deflection probe). The companion currently defaults to
`browser = "edge"` and `find_browser` only checks that `msedge.exe` *exists* — it does, so the
companion would try Edge and silently get nothing. See §5.3.

### 3.7 The bundled Durotar video is not embeddable
`d_vPJjqWxZM` (Azeroth Mixtape's Durotar drop, "Dolly Parton - 9 to 5") returns IFrame error **150**
("owner does not allow embedding") on a `127.0.0.1` origin, even though its oEmbed endpoint returns
200. **oEmbed success is not an embeddability check.** Any bundled/added YouTube id should be probed
with an actual cued (non-playing) `YT.Player`; the Mixtape's Durotar drop currently cannot play via
embed and needs a different id or a Spotify fallback.

---

## 4. Geometry (DPI / multi-monitor)

- At 300 % DPI the `--app` window has essentially **zero non-client area** (no title bar, hairline
  border): measured client size == requested `--window-size` (1080x609 physical == 360x203 CSS px at
  scale 3.0). So in production: launch at `--window-size = ceil(356*scale) x ceil(200*scale)` DIP-ish
  (Chromium treats `--window-size` as DIP), then on show use `SetWindowPos` to **move only**
  (`SWP_NOSIZE`) to the target corner, and **verify once** after the first show that
  `GetClientRect` >= `ceil(356*scale) x ceil(200*scale)` physical. (The prototype's first-show
  348x197 was a non-client-size *guess* in the test harness, not Chromium; the second show, using the
  measured client size, was 360x203 and correct.)
- **Make the process per-monitor-DPI-aware** before reading/writing any pixel coordinate:
  `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` (or a manifest). A
  PyInstaller exe is DPI-*unaware* by default, so without this its coordinates are virtualized and
  the window lands in the wrong place / wrong size on a high-DPI display.
- **Compute the off-screen launch position and the top-right target from `EnumDisplayMonitors`**,
  never from a constant. A monitor arranged to the left has negative x, so a hardcoded `-3000` could
  be *on* screen. Target rect = `(monitor.right - margin - w, monitor.top + margin)` on the monitor
  that currently holds the game window (`MonitorFromWindow`). Use `GetDpiForMonitor` per monitor.
- **Top-right overlaps the minimap** in Forever's Modern Edit Mode preset (DESIGN §2.3: minimap is
  top-right) and generally sits over the top-right HUD. The owner chose top-right (real-client §8);
  honor the `playerCorner` setting and let players move it.

---

## 5. Risks and integrator notes

### 5.1 Foreground rights and the lock timeout — the core safety argument
Chromium's startup `SetForegroundWindow` succeeds only if the **launching** process currently holds
foreground rights, which it passes to the child. A background tray companion, launched at login and
sitting idle while WoW is focused, normally holds no such right, so the creation activation would be
**denied** by Windows (a taskbar flash at most, no steal) — **[inferred from Win32 foreground-activation rules]**.

**T4 measured the failure case, not the safe case.** T4 was spawned via WMI
(`Win32_Process.Create`, parent `WmiPrvSE.exe`, no foreground lineage) **254 s after the last
keyboard/mouse input**. With the default `ForegroundLockTimeout` of **200000 ms** lapsed, a launcher
with no foreground connection whatsoever still acquired rights, and Chromium's creation activation
**succeeded** (chrome foreground at 220 ms, returned to the prior window ~0.65 s later). That is
exactly the controller-only-player scenario (real-client §7): after 200 s with no mouse/keyboard
input, the lock lapses and any process can force foreground. `GetLastInputInfo` does **not** count
gamepad input **[inferred — documented Win32 behavior]**, so a pad-only player looks idle to Windows
and loses this protection. (A *true* no-rights launcher would have to be spawned within <200 s of
real HID input; the owner was away for every trial, so that case remains **[inferred]**. Note: the
live `SPI_GETFOREGROUNDLOCKTIMEOUT` here read `0x7FFFFFFF`, which is inconsistent with the observed
lapse behavior and unexplained — do not build on it.)

**This is exactly why the recipe must not depend on rights being absent.** Creating the player
window **once at companion startup** makes the single possible activation happen when the companion
starts (user not mid-combat), regardless of rights; per-drop show/hide via
`SetWindowPos(SWP_NOACTIVATE)` never calls `SetForegroundWindow` and so is safe even when the lock
has lapsed. The measured fact across every trial: creation activation is a single transient event
that returns to the prior foreground within ~0.2-0.7 s; **operation-phase activations = 0** in all 8
trials, including the on-screen trial where the window was visible and playing. The design is safe by
construction, not by assuming the launcher lacks rights.

### 5.2 create-at-startup vs create-at-first-drop — open product decision
Keeping one Chromium alive all session costs ~420 MB idle / ~560-800 MB during playback. The
alternative (`auto_open="youtube"`: open when a YouTube drop plays) is precisely the per-drop
creation path that caused the owner's alt-tab. Recommendation: for players who use YouTube drops,
create at startup (hidden) and pay the memory; otherwise never create. Expose it as the existing
`auto_open` setting but change its meaning to "create the hidden window at startup" rather than "open
on first play."

### 5.3 Browser selection / Edge deflection
`find_browser` should, in addition to locating the exe: (a) check for the `msedge.exe` IFEO
`Debugger` hook and skip Edge if present; and (b) regardless of browser, treat **"no player page
connected from my own process tree within N seconds"** as a launch failure and fall back to the other
Chromium, then to a clearly-labelled visible window as a last resort. `ensure_player` today waits for
a connection but does not retry a different browser. On this machine, Chrome is the working choice.

### 5.4 Hide with `SWP_HIDEWINDOW`, never park off-screen-visible
A window moved to e.g. x=-9000 but left *shown* reports `document.visibilityState === "visible"` to
the page (observed in T0b). That both defeats the throttling flags' purpose and, during warm priming,
means the video is "playing while not visible to the user" in ToS terms. Always hide with
`SWP_HIDEWINDOW` between drops; the page then correctly reports `hidden` and is paused.

### 5.5 Platform variance **[inferred where noted]**
- **Windows 10**: same Win32 APIs. `SetProcessDpiAwarenessContext` + `DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2` need **1703+**; `SetThreadDpiAwarenessContext` is 1607, `GetDpiForMonitor` is 8.1+. Behavior expected identical **[inferred]**; worth a smoke test.
- **Exclusive-fullscreen WoW**: a topmost window cannot paint over a true exclusive-fullscreen game, which makes YouTube's visible-player rule unsatisfiable. This box runs WoW **borderless** (window class `waApplication Window`, style `0x95000000`), where topmost works. Recommend the companion detect exclusive fullscreen and surface "set WoW to Windowed (Fullscreen)" for YouTube drops **[inferred]**.
- **Browser auto-update**: `--app`, the IFrame API, and the Win32 window handling are all stable surfaces, but pin nothing — the "no window connected -> fall back" guard (§5.3) also covers a browser update that changes window classing.
- **`gui.active/focus`** returned by `GetGUIThreadInfo` for the player's own thread is *per-thread* focus (the web content), **not** system foreground — do not read it as a steal.

### 5.6 Window-class note for PIX-WINDOW-LATCH
The running game's top-level window here is class **`waApplication Window`**, not
`transport/wincapture.py`'s `GAME_WINDOW_CLASS = "GxWindowClass"`. What actually matched the game
was the `_forever_\Wow.exe` path, not the class. This bears on the PIX-WINDOW-LATCH finding
(real-client §2): prefer the exe-path match and do not rely on the class string alone.

---

## 6. Reference implementation sketch

Mirrors the existing deck command/status shape in `companion/needledropper/deck/static/player.js`
and the provider flow in `companion/needledropper/providers/youtube.py`. **Sketch only — not wired
up here.**

### 6.1 `deck/static/player.js` — add a prime path

```js
// New SSE command: {cmd:"prime", playId, source:"youtube", videoId, startMs, volume}
// Sent on ARM, ~10 s before FIRE, while the window is hidden. Warm-buffers, then pauses.
function primeYoutube(cmd) {
  stopYoutubeNow();
  primed = { playId: cmd.playId, videoId: cmd.videoId, startMs: cmd.startMs || 0,
             volume: targetVolume(cmd), ready: false };
  withYouTube(function () {
    if (!primed || primed.playId !== cmd.playId) return;
    yt.player.mute();                                            // silent while hidden
    yt.player.loadVideoById({ videoId: cmd.videoId,
                              startSeconds: Math.floor((cmd.startMs || 0) / 1000) });
    // onStateChange: on the FIRST PLAYING after a prime, pause immediately and mark ready.
  });
}
// in onYtState(): if (primed && !primed.ready && e.data === YT.PlayerState.PLAYING) {
//   yt.player.pauseVideo(); primed.ready = true; post("primed","youtube",primed.playId); }

// play() becomes the fast path when already primed for the same video+start:
function playYoutube(cmd) {
  if (primed && primed.ready && primed.videoId === cmd.videoId &&
      (primed.startMs|0) === (cmd.startMs|0)) {
    current = { playId: cmd.playId, source: "youtube", volume: targetVolume(cmd), started: false, cmd: cmd };
    showYt();
    yt.player.unMute();
    yt.player.setVolume(cmd.fadeInMs > 0 ? 0 : current.volume);
    yt.player.playVideo();                                       // ~260 ms to audio
    primed = null;
    return;
  }
  // ... existing cold loadVideoById path as fallback (slower) ...
}
```
Page stays otherwise identical: `enablejsapi`, `origin`, `playsinline`, `mute:1` initial, keep the
autoplay-blocked watchdog. The window never calls `window.focus()`.

### 6.2 `providers/youtube.py` / `deck/server.py` — the window layer

```python
# Deck.open_player() launches ONCE, hidden, and keeps the handle for the session.
#   args = [exe, f"--app={self.player_url}", f"--user-data-dir={self.profile_dir}",
#           "--autoplay-policy=no-user-gesture-required", "--no-first-run",
#           "--no-default-browser-check", f"--window-size={w},{h}",
#           f"--window-position={offx},{offy}",
#           "--disable-background-timer-throttling",
#           "--disable-backgrounding-occluded-windows",
#           "--disable-renderer-backgrounding"]           # NO STARTUPINFO show flags
# After launch: find Chrome_WidgetWin_1 owned by the launched pid-tree (NOT by title),
#   SetWindowLongPtrW(EXSTYLE, +NOACTIVATE +TOOLWINDOW -APPWINDOW), then hide (SWP_HIDEWINDOW).

class PlayerWindow:
    def show_topright(self):   # on FIRE
        x, y, w, h = top_right_rect(game_hwnd())            # from EnumDisplayMonitors + GetDpiForMonitor
        SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h,
                     SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER)
    def hide(self):            # on end / CANCEL
        SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                     SWP_HIDEWINDOW | SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
    def closed_by_user(self):  # page disconnected + window gone
        wait_process_exit(self.pid); self.hwnd = None       # relaunch lazily at next ARM

# YouTubeProvider gains an arm()/prime step called on the P2 ARM event:
#   self.deck.send({"cmd":"prime","playId":pid,"source":"youtube",
#                   "videoId":drop.youtube,"startMs":drop.startMs or 0,"volume":...})
# play() on FIRE: deck.player.show_topright(); deck.send({"cmd":"play", ... same playId ...})
#   -> page takes the fast primed path. Fade/stop/poll unchanged.
```

`SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` at process start (companion `__main__`), so the
PyInstaller exe reads/writes physical pixels. All Win32 signatures used by the prototype are in
`scratchpad/focus-proto/w32.py` and can be lifted verbatim (stdlib `ctypes` only).

---

## 7. Checklist for the companion integrator

- [ ] Create the hidden player window **once at startup**, not per drop (§1.1, §5.2).
- [ ] Style it `WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW`, minus `WS_EX_APPWINDOW`, immediately; hide with `SWP_HIDEWINDOW` (§1.2, §5.4).
- [ ] Per drop: `SetWindowPos(HWND_TOPMOST, SWP_NOACTIVATE|SWP_SHOWWINDOW)` to show, `SWP_HIDEWINDOW|SWP_NOACTIVATE` to hide. Never relaunch while the process lives (§1.4-1.6).
- [ ] Warm-prime on ARM (`loadVideoById`->pause); fast `unMute`+`playVideo` on FIRE (§1.3, §6.1).
- [ ] Keep `--disable-background-timer-throttling`, `--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding` (§3.5).
- [ ] `SetProcessDpiAwarenessContext(PMv2)`; compute positions from `EnumDisplayMonitors`/`GetDpiForMonitor`; verify client >= 356x200 CSS px (§4).
- [ ] Detect the Edge IFEO deflection and/or fall back when no player page connects from the launched process tree (§5.3, §3.6).
- [ ] Probe each YouTube id's real embeddability with a cued player; the bundled Durotar id fails embed (§3.7).
- [ ] Match the player window by pid, not title (§1.2).
- [ ] Final in-game confirmation by the owner (real-client §8): `GetForegroundWindow` stays the WoW window across ARM/FIRE/end, and FIRE-to-audio measured with real sound.
```
