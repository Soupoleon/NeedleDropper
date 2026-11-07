# NeedleDropper v1.2.1: music services in game

**Owner's request:** "ensure in the addon options we have requisite spotify/youtube account config".

**Result:** the game's Settings panel (Options > AddOns > NeedleDropper) gets a **Music services** section. It holds everything a player needs to choose and check their services:

- the app's status;
- the Spotify account (set up, connect, manage) and an optional YouTube sign-in;
- "Play music with", the YouTube box position, fade and resume;
- test buttons.

The Spotify Client ID is typed in a small **Music services** dialog of the NeedleDropper window, because the Settings panel has no text box (verified, §1.2).

The game sends the choices and requests to the NeedleDropper app over the existing beacon and chat-log channels (Protocol v2 plus a new CONFIG event). The app answers through `NeedleDropper_Companion`, which the game reads at the next login or reload.

**Baseline.** Checked on 2026-11-05 against:

- the repo at `D:/NeedleDropper`, read only;
- Gethe/wow-ui-source `forever` @ `eac30af` ("1.60.1 (70790)", committed 2026-11-04).

DESIGN-v1.2 stays authoritative for everything this document does not change. PROTOCOL-v2, CONTRACTS-v1.2 and the other documents get the edits listed in §6.

Citations: **RC#n** = docs/review/real-client-2026-11-05.md; **F#n** = docs/review/protocol-attack-findings.md.

**Scope.** This is a specification only; no code changes with it.

---

## 0. Decisions

| Question | Decision | Deciding reason |
|---|---|---|
| Where the Client ID is typed | The **Music services** dialog (`NeedleDropperMusicDialog`, new `UI\MusicDialog.lua`). It opens from the Settings section, from the window's title bar and with `/needle music`. | Settings on forever has no text-input control (§1.2). DESIGN-v1.2 §4.7 keeps text entry out of Settings anyway. |
| What the Settings panel holds | A searchable **Music services** section with ten rows: app status, Spotify account, YouTube account, Play music with, YouTube box position, Fade, Resume, Test Spotify, Test YouTube, Refresh status. Stock elements only. | The owner asked for it "in the addon options". Status texts use function-valued button text, which forever supports. |
| A fourth window tab? | No. A dialog, like Share and Import. | DESIGN-v1.2 §0: at most three tabs. |
| How the choices reach the app | **PREFS** TLV (0x0B), level-triggered state. Once the player has set a music choice in game, PREFS rides in every HELLO and every CONFIG. | HELLO is on screen whenever nothing else is, so the app converges within one capture, even after it restarts (baseline hook, §3.1). |
| How requests reach the app | New event **CONFIG = 6** with **ACTION** (0x0C) and **CLIENT_ID** (0x0D). | 1.2.0 decoders reject type 6 outright (verified, §2.11), so an old app never mistakes a request for a song or a test signal. |
| Request identity | `(action code, issuedAt)`. issuedAt is the game's `time()`, read from the same PC clock as the app's. A request is fresh only from −5 s to +45 s. | Survives beacon + chat-log duplicates, re-shows, seq wrap and chat-log re-reads after truncation. |
| What a request can do | One of five enumerated actions. No TLV carries text or a URL; the app builds every URL from its own constants. | The worst a forged or damaged frame can do: open Spotify's or Google's own sign-in page, play a 15-second test, or sign out of Spotify. The sign-out is reversible and needs the matching Client ID. |
| Frame size | HELLO+PREFS and every CONFIG are **one strip row** (≤ 39 bytes). | One-row strips are immune to F1's column-cover pattern, and the idle strip stays 3 px tall. |
| Beacon priority | CONFIG has its own queue. It shows only when no FIRE/CANCEL/PING is due and no ARM is in its 3 s song window. Hold: 3 s for a request, 2 s for choices only. A request is shown once more 8 s later. | Never delays song data (the RC#8 300 ms target). The second showing survives a short overlay (F2). |
| "Play music with" | Five values. A 1.2.1 app orders and limits its providers from `music.order`. For an older app, the addon enforces the "only" modes by leaving the excluded fields out of song frames. "Neither" is always enforced in the addon. | Works with every app version, and "Neither" must never play. |
| Who owns which app setting | The game owns `music.order`, `music.fade`, `spotify.restorePrevious` and `deck.playerCorner`. The Connect page keeps the service switches (`spotify.enabled`, `youtube.enabled`), the Client ID and the Spotify device. | The engine's v2 routes ignore the `providers` order (`_routes_v2` hard-codes Spotify → YouTube → others). `spotify_connect` rewrites `providers` and `spotify.enabled`, so a game-owned copy of those would fight it. |
| Where Spotify plays (added with the build, lead decisions D6–D14) | **Inside the app:** the Spotify Web Playback SDK in the same hidden player window as YouTube (one Chrome), as the Connect device "NeedleDropper". `spotify.device = "connect"` (with `deviceName`) keeps the 1.2.0 control of another Connect device, off by default. See §3.13. | The Spotify desktop clients accept Web API plays and never start them; the Web Player does (RC#14, RC#15). "The Spotify device" in the row above means `spotify.device` and `deviceName`. |
| Connect with a new Client ID | Validated, then the browser sign-in starts at once. The ID is saved immediately, unless a working sign-in for another ID exists. Then it is saved only when the new sign-in succeeds. | `TokenManager` reads a Client ID mismatch as a revoked sign-in, and changing `spotify.clientId` cancels the login in progress. Saving first would cost a working connection. |
| Disconnect | Only when the frame's CLIENT_ID equals the app's active Client ID. | A damaged or forged frame can't sign anyone out. |
| Feedback | On the PC, immediately: Spotify's page, the YouTube box, Windows notifications. In game: "Sent…" copy at once, and the result after **Refresh status (reloads UI)**. | The addon only hears from the app when it reads `NeedleDropper_Companion` again. |
| YouTube sign-in | The app opens Google's sign-in page in a **normal** window of the player box's own browser profile (`deck-browser`). | The cookies land where the box plays, and the address bar stays visible, so the player can see accounts.google.com. |
| Schemas | No `ns.SCHEMA` bump. `NeedleDropper_Companion` stays schema 1 and gains a `music` block. config.json gains `music` and `deck.playerCorner`. | Every change is additive; both older versions keep or ignore what they don't know (§5.6). |
| The oldest app the addon talks to (added with the build) | **1.2.1.** `Companion.IsOutdated` is true below 1.2.1, so a 1.2.0 app counts as no app: no zone-music fade for its songs, the "update the app" lines, no requests (§1.1). | A 1.2.0 app reads no 1.2.1 frame (decisions D1, D2; §2.11): nothing it could do would play. |
| A bug report from the game (added with the build, decision D16) | `/needle report`: a read-only, selected plain-text report in a Share-style dialog, plus one chat line (§1.6a). | The player copies it with Ctrl+C; it holds no account name, Client ID or token. |

---

## 1. In game

### 1.1 What the game knows

The addon has two sources and no others:

1. **Its own SavedVariables:** the four music settings, the Client ID the player typed, and the requests it sent (§1.4).
2. **`NeedleDropper_CompanionData`**, read once at `PLAYER_LOGIN`, which also fires after `/reload`. It uses:
   - from v1.2: `appVersion`, `written`, `status.spotify`, `status.youtube`;
   - new: the `music` block (§3.10).

Within a session, nothing new arrives from the app. Everything the game shows about the app is "as of its report".

**`Companion.lua` additions (A1):**

- `C.Music()` returns the checked `music` block, or nil.
- `C.AtLeast(version, major, minor, patch)` compares versions. An unreadable version counts as false.
- `C.Supports("config")` is true when `appVersion` is 1.2.1 or newer.
- `C.StatusLine()` (window footer, compartment tooltip, `/needle status`) adds the account, the plan and YouTube, e.g. "App seen 2 min before login · Spotify: Ethan (Premium) · YouTube ready".
- **As built:** `C.SpotifyState()` (music.spotify.state when a 1.2.1 app reports it, else status.spotify), `C.ReportAge()`. **`C.IsOutdated` now means "older than 1.2.1"** (it was 1.2): a 1.2.0 app reads no 1.2.1 frame (§2.11), so it counts as no app for `Director.AppKnown` (no zone-music fade, a drop shows its card and plays again once a current app is there) and for the footer ("Update the NeedleDropper app to 1.2.1 to play Spotify and YouTube songs"). The StatusLine's Spotify words follow the D17 state (connected: "Spotify: <account> (Premium)" or "Spotify connected" without an account; needs-relogin "Spotify: sign in again"; no-player by its cause, `music.spotify.problem` (polish, review ON-9): "Spotify needs Google Chrome" (no-chrome), "Spotify player not ready" (not-ready, and without a cause or with an unknown one), "Spotify player not started yet" (no-window), "Spotify player switched off" (window-never), "Spotify player didn't load" (sdk-load); stuck "Spotify didn't start"); its YouTube part is "YouTube ready", "YouTube off" or "YouTube: no player browser".

**Status lines** (`ns.MusicServices.StatusLines()`): the dialog and the Settings tooltip show four lines. For each line, the first matching row applies.

| Line | Condition | Text |
|---|---|---|
| App | no data | NeedleDropper app not found. Spotify and YouTube songs need it: get it from the addon page. |
| | appVersion < 1.2 | Update the NeedleDropper app to 1.2.1 to play Spotify and YouTube songs. |
| | appVersion < 1.2.1 | NeedleDropper app %s. Update it to 1.2.1 to set up music from the game, or use its Connect page (tray icon → Open setup…). |
| | written = 0 | NeedleDropper app installed: it hasn't reported in yet. |
| | otherwise | NeedleDropper app %s · report from %H:%M (%s before login) |
| Spotify | `status.spotify` connected, plan premium or unknown | Spotify: connected as %s (Premium) / Spotify: connected as %s / Spotify: connected (no account name) |
| | connected, plan known and not premium | Spotify: connected as %s, but Spotify %s can't be controlled: playback needs Premium. |
| | premium-required | Spotify: this account needs Premium to play songs. |
| | error | Spotify: sign in again (the sign-in stopped working). |
| | detail from a 1.2.1 app: `music.spotify.state` needs-relogin, no-player or stuck (`status.spotify` reads "error" then, §3.13) | the state's own line from the §3.13 status table (e.g. "Spotify: sign in again: NeedleDropper now plays Spotify in its own player") |
| | not-connected, a Client ID is known | Spotify: not connected (Client ID …%s). |
| | not-connected | Spotify: not set up. |
| | suffix, when the order excludes Spotify | · not used: Play music with is YouTube only |
| YouTube | `status.youtube` false or `music.youtube.enabled` false | YouTube: switched off in the NeedleDropper app (its Connect page). |
| | `music.youtube.player` none | YouTube: the NeedleDropper app has no player browser (deck.browser in its config.json). |
| | player default (obsolete: 1.2.1 reports only chrome, edge or none, §3.10; the default browser never plays) | YouTube: ready · plays in your default browser |
| | otherwise | YouTube: ready · player box %s (top right, …) |
| | suffix, when `signInOpened` is set | · sign-in page opened %d %b |
| | suffix, when the order excludes YouTube | · not used: Play music with is Spotify only |
| Last request | | §4.3 |

**As built** (`MusicServices.StatusLines()`; every line comes escaped, ready for a FontString, a tooltip or chat). Where this differs from the table above, this wins:

| Line | Condition | Text |
|---|---|---|
| App | no data | NeedleDropper app not found. Spotify and YouTube songs need it: get it from the addon page. |
| | appVersion < 1.2 | Update the NeedleDropper app to 1.2.1 to play Spotify and YouTube songs. |
| | appVersion < 1.2.1 (D2: it reads no 1.2.1 frame, so nothing plays) | NeedleDropper app %s can't read this addon's signals: update it to 1.2.1 to play Spotify and YouTube songs. |
| | written = 0 | NeedleDropper app installed: it hasn't reported in yet. |
| | otherwise | NeedleDropper app %s · report from %H:%M (2 min before login) |
| Spotify | state connected, Premium / no plan / no account | Spotify: connected as %s (Premium) · Spotify: connected as %s · Spotify: connected (no account name) |
| | connected, a plan other than Premium | Spotify: connected as %s, but Spotify %s can't be controlled: playback needs Premium. |
| | suffix, connect mode (`spotify.device = "connect"`) | · plays on another Spotify device |
| | premium-required | Spotify: this account needs Premium to play songs. |
| | needs-relogin (D9) | Spotify: sign in again to allow in-app playback (NeedleDropper's Music services). |
| | no-player (D10, D11), by `music.spotify.problem` (polish, review ON-9; only no-chrome names Chrome) | no-chrome: Spotify: install Google Chrome to play Spotify in NeedleDropper (if it is installed, wait a minute for its Widevine component). · not-ready: Spotify: NeedleDropper's player didn't get ready. The app tries again by itself (its Connect page says more). · no-window: Spotify: NeedleDropper's player isn't running yet. The app starts it when World of Warcraft isn't the window in front (start the app before the game). · window-never: Spotify: NeedleDropper's player is switched off (deck.autoOpen is "never" in the app's config.json). · sdk-load: Spotify: Spotify's player didn't load in NeedleDropper (no internet, or a firewall blocks it). · no cause (an app built before the codes) or an unknown one: Spotify: NeedleDropper's player isn't ready (the app's Connect page says why). (`MusicServices.NO_PLAYER_LINES`, `NO_PLAYER_LINE`) |
| | stuck (D8) | Spotify: a song didn't start in NeedleDropper's player, so drops play YouTube until it works again (Test Spotify checks it). |
| | error | Spotify: sign in again (the sign-in stopped working). |
| | not-connected (or no state), a Client ID known | Spotify: not connected (Client ID …%s). |
| | otherwise | Spotify: not set up. |
| | suffix, the order excludes Spotify | · not used: Play music with is YouTube only (· … is Neither for "none") |
| YouTube | no app data | YouTube: plays through the NeedleDropper app. |
| | switched off (`status.youtube` false or `music.youtube.enabled` false) | YouTube: switched off in the NeedleDropper app (its Connect page). |
| | player none | YouTube: the NeedleDropper app found no browser for its player box: install Google Chrome. |
| | otherwise | YouTube: ready · player box top right (Chrome) — the corner of `youtubeCorner`, the browser of `music.youtube.player` |
| | suffixes | · sign-in page opened %d %b · not used: Play music with is Spotify only / Neither |
| Last request | none sent | (empty) |
| | | §4.3 |

### 1.2 The Settings panel

**Verified: Settings has no text input on forever.** At `eac30af`, `Interface/AddOns/Blizzard_Settings_Shared/Blizzard_SettingControls.xml` defines these control templates and no others:

- `SettingsCheckboxControlTemplate` (L107), `SettingsSliderControlTemplate` (L114), `SettingsDropdownControlTemplate` (L121);
- `SettingButtonControlTemplate` (L129), `SettingsColorSwatchControlTemplate` (L136);
- the combinations `SettingsCheckboxWithButtonControlTemplate` (L143), `SettingsCheckboxSliderControlTemplate` (L150), `SettingsCheckboxDropdownControlTemplate` (L157), `SettingsCheckboxWithColorSwatchControlTemplate` (L164);
- plus the section header, the expandable section and the add-on-disabled label.

"EditBox" appears nowhere in `Blizzard_SettingControls.lua`, `Blizzard_SettingControls.xml`, `Blizzard_Settings.lua`, `Blizzard_SettingsList.xml` or `Mainline/Blizzard_SettingsPanelTemplates.xml`.

The only way to get a text box in would be `Settings.CreateElementInitializer(frameTemplate, data)` (Blizzard_Settings.lua L342) with an XML template of our own. Rejected, for three reasons:

- NeedleDropper ships no XML templates.
- The panel's ScrollBox recycles rows, so a half-typed ID would be lost when scrolling.
- DESIGN-v1.2 §4.7 keeps editing out of Settings.

**What forever does support, and this section relies on:**

- **Dynamic button text.** `CreateSettingsButtonInitializer(name, buttonText, buttonClick, tooltip, addSearchTags, …)` is at Blizzard_SettingControls.lua L1040.
  - `buttonText` may be a function: `SettingsButtonControlMixin:EvaluateName` (L960) calls it whenever the row is initialised (L968: `self.Button:SetText(self:EvaluateName())`).
  - The button is a 200 px `UIPanelButtonTemplate` (L946–L950), so dynamic texts stay at 24 characters or fewer.
- **Dynamic tooltips.** A tooltip may be a function (`Settings.InitTooltip`, Blizzard_Settings.lua L289–L297).
- **Search tags.** `initializer:AddSearchTags(...)` indexes strings only and skips anything else (Blizzard_Settings.lua L53–L64). A function-valued `buttonText` is therefore never indexed, so each row adds its own string tags.
- **Disabling.** `initializer:AddModifyPredicate(fn)` (Blizzard_SettingControls.lua L156) disables the row's button while `fn()` returns false (`EvaluateState`).

Rows are re-initialised when the category opens or scrolls into view, not live. That is enough: the app's report only changes at a reload. The one change within a session (a request was just sent) shows the next time the panel opens.

**Placement.** The section sits between "Needle drops" and "Playback": the four settings go into `ns.SETTINGS` right after `instanceDrops`, with `SECTIONS.musicOrder = "Music services"`.

**Rows.** Options.lua builds them in this order:
- the header;
- rows 1–3 (a new "before key" hook next to the existing `PREVIEW_AFTER`/`SIGNAL_AFTER` "after key" hooks);
- the four settings;
- rows 8–10 (an "after `resumeMyMusic`" hook).

| # | Row label | Element | Control text | Click / setting | Extra search tags |
|---|---|---|---|---|---|
| — | Music services | section header | | | |
| 1 | NeedleDropper app | button | `MS.StatusWord()` | `ns.UI.OpenMusic()` | app, companion, status, music |
| 2 | Spotify account | button | `MS.SpotifyButtonText()` | `MS.SpotifyButtonClicked()` | Spotify, account, Client ID, connect, sign in, Premium |
| 3 | YouTube account | button | Sign in to YouTube… | popup `NEEDLEDROPPER_YOUTUBE_SIGNIN` | YouTube, sign in, Premium, ads |
| 4 | Play music with | dropdown | | `musicOrder` | Spotify, YouTube |
| 5 | YouTube box position | dropdown | | `youtubeCorner` | YouTube, corner, player |
| 6 | Fade songs in and out | checkbox | | `musicFade` | fade |
| 7 | Resume my Spotify music afterwards | checkbox | | `resumeMyMusic` | Spotify, resume |
| 8 | Test Spotify | button | Test Spotify | `MS.Send("test-spotify")` | test, Spotify |
| 9 | Test YouTube | button | Test YouTube | `MS.Send("test-youtube")` | test, YouTube |
| 10 | Refresh status | button | Reload UI | `pcall(ReloadUI)` | reload, refresh, status |

- Rows 3, 8 and 9 get `AddModifyPredicate(MS.CanSendActions)`. Their tooltip functions end with the reason when it is false.
- Rows 1, 2 and 10 are always enabled: 1 and 2 open the dialog, which explains what to do.
- Every Settings call stays pcall-guarded, as all of Options.lua is. If the client has no button initializers (`SHIM.RemoveSettingsExtras`), only rows 4–7 appear, and `/needle music` still opens the dialog.

**`MS.StatusWord()`** (row 1), first match:

| Condition | Text |
|---|---|
| no app data | App not found |
| appVersion < 1.2.1 | Update the app |
| written = 0 | Waiting for the app |
| Spotify ready and YouTube ready | Spotify + YouTube ready |
| Spotify ready | Spotify ready |
| YouTube ready | YouTube ready |
| otherwise | Nothing ready yet |

- **Spotify ready** = `status.spotify == "connected"`, and the plan is premium or unknown.
- **YouTube ready** = `status.youtube == true`, `music.youtube.enabled ~= false` and `music.youtube.player ~= "none"`.

**`MS.SpotifyButtonText()`** (row 2) and its click, first match:

| Condition | Text | Click |
|---|---|---|
| a connect was sent < 5 min ago and the report is older than it | Waiting for browser… | open the dialog |
| connected, plan premium or unknown | Manage… | open the dialog |
| premium-required, or the plan is known and not premium | Needs Premium… | open the dialog |
| `not MS.CanSendActions()` | Set up Spotify… | open the dialog |
| status error | Sign in again | `MS.Send("connect-spotify")` |
| a Client ID is known (typed in game, or reported by the app) | Connect Spotify | `MS.Send("connect-spotify")` |
| otherwise | Set up Spotify… | open the dialog |

**As built (D17):** the table is checked on `music.spotify.state` (status.spotify for an older report), and three rows sit between "not `CanSendActions()`" and "status error": **needs-relogin** → "Sign in again" (connect), **no-player** → "Needs Chrome…" when its cause is no-chrome, else "Check Spotify…" (open the dialog; polish, review ON-9), **stuck** → "Check Spotify…" (open the dialog). Signing in again cannot fix a missing Chrome or a start that did not progress, so status "error" alone no longer means "Sign in again".

Every send from a Settings row prints its §4.2 copy as one chat line, because Settings has no feedback line of its own. **As built:** every send prints that one line wherever it comes from (Settings, the dialog, `/needle music`): the copy, then the "Nothing after 10 seconds?" sentence; a refusal prints its refusal text.

**Tooltips** (all through `ns.L`):

1. The §1.1 status lines, then "Click to open Music services."
2. Depends on the state:
   - connected: "Connected as %s (%s) · Client ID …%s. Click for Disconnect and Test.";
   - connect: "The NeedleDropper app opens Spotify's sign-in page in your web browser. Approve it there.";
   - set up: "Create your own free Spotify developer app and paste its Client ID: about 5 minutes, once."
3. "Optional. The NeedleDropper app opens YouTube's sign-in page in its player box's browser. Signed in with YouTube Premium, YouTube songs play without ads."
4. "Which service the NeedleDropper app uses for a song, and which one it tries next if that can't play it. In-game audio files always play in the game. YouTube only: drops without a YouTube link show just their title card."
5. "Where the small YouTube player box appears while a YouTube song plays. YouTube requires the player to stay visible."
6. "Off: songs start and stop at once, like a real needle drop."
7. "When a Spotify needle drop ends, Spotify goes back to what you were listening to."
8. "The NeedleDropper app plays 15 seconds of Toto – Africa on Spotify, or a Windows notification says why it can't." **As built (D6, D7):** in app mode (the default) "The NeedleDropper app plays 15 seconds of Toto – Africa in its own Spotify player (no Spotify app needed), or a Windows notification says why it can't."; the text above stays for connect mode.
9. "The NeedleDropper app opens the YouTube box in your corner and plays 15 seconds of Toto – Africa."
10. "The game reads the NeedleDropper app's report only when the interface loads. Reload now to see the latest status here. Your soundtrack edits are saved first."

**As built (Options.lua):** the rows are added in the order above (`MUSIC_BEFORE = "musicOrder"` hook for rows 1–3 after the header, `MUSIC_AFTER = "resumeMyMusic"` for 8–10). Each button row is `CreateSettingsButtonInitializer(name, text, click, tooltip, true)` plus `AddSearchTags(...)` with its tags (the name is indexed by `addSearchTags`; a function-valued text never is) and, for rows 3, 8 and 9, `AddModifyPredicate(O.CanSendActions)`, a plain boolean. The four settings' own initializers get their extra tags too (`SETTING_TAGS`). Setting labels, tooltips and option texts now go through `ns.L`, like every other text. Without button initializers only rows 4–7 appear (`test_old_settings_api_gets_the_four_settings_only`).

**Option texts:**

- **`musicOrder`:**
  - `spotify-youtube` "Spotify, then YouTube" (default)
  - `youtube-spotify` "YouTube, then Spotify"
  - `spotify` "Spotify only"
  - `youtube` "YouTube only"
  - `none` "Neither: title cards and in-game files only"
- **`youtubeCorner`:**
  - `topright` "Top right" (default; the owner's choice, RC#8)
  - `topleft` "Top left (below the beacon)"
  - `bottomright` "Bottom right"
  - `bottomleft` "Bottom left"

### 1.3 The Music services dialog

**The frame.** `NeedleDropperMusicDialog` is built on first open by `UI\MusicDialog.lua` (A2). It reuses the construction of UI\ShareDialogs.lua's `dialogFrame`, which is a local function today: A2 exports it as `W.DialogFrame(name, title, w, h)` for both files. The dialog is:

- `ButtonFrameTemplate`, 560 × 600, title "Music services";
- strata DIALOG, which is above the Settings panel's HIGH;
- centred, draggable by its title bar, listed in `UISpecialFrames`;
- `W.ManageInCombat`: it hides in combat, comes back afterwards, and joins the gamepad UI.

**How it opens.** `ns.UI.OpenMusic()` opens it, called from:

- Settings rows 1 and 2;
- a new 16 px title-bar button in the main window, left of the gear (`W.IconButton`). Its texture is `Interface\Icons\INV_Misc_Drum_01`, the addon's own TOC icon. Tooltip: "Music services" / "Spotify, YouTube and how songs play.";
- `/needle music`.

The compartment keeps its v1.2 clicks: left = window, right = Settings.

**Contents.** Top to bottom; templates from the existing allowlist only (no new template).

**A. Status**

- The four §1.1 lines (`GameFontHighlightSmall`, wrapping).
- [Send a test signal] → `ns.SelfTest.Signal()`.
- [Refresh status (reloads UI)] → `pcall(ReloadUI)`.

**B. How songs play**

- **Play music with:** `WowStyle1DropdownTemplate` + `SetupMenu` radios with the five values; writes through `ns.SetSetting("musicOrder", v)`.
- **YouTube box position:** the same pattern, `youtubeCorner`.
- **[x] Fade songs in and out** and **[x] Resume my Spotify music afterwards:** `UICheckButtonTemplate`.
- One line under them with the §4.2 "choices" copy.

**C. Spotify**

- Intro: "Spotify needs a free developer app of your own (about 5 minutes, once). The game can't open web pages, so copy these addresses into your browser."
- Step 1: "Open the Spotify developer dashboard and log in with the Spotify account you listen with:" followed by the read-only box `https://developer.spotify.com/dashboard`.
- Step 2: "Click Create app (any name, e.g. NeedleDropper), tick Web API, add this exact Redirect URI and press Add, then Save:" followed by a read-only box. Its value is `music.spotify.redirectUri` when the app reports one (the port is configurable), else `http://127.0.0.1:8321/callback`.
- Step 3: "Open your app's Settings, copy its Client ID (not the Client secret) and paste it here with Ctrl+V:" followed by the Client ID box and its validation line.
- Buttons: [Connect Spotify] [Disconnect…] [Test Spotify].
- Notes:
  - "Controlling playback needs Spotify Premium. A development app works for its owner and up to 5 accounts added under User Management."
  - "No keyboard? Do these steps on the app's Connect page instead: tray icon → Open setup…"
- A feedback line.

**D. YouTube**

- "Nothing to set up. While a YouTube song plays, a small player box appears in the corner you chose (YouTube requires it to be visible)."
- Buttons: [Sign in to YouTube…] [Test YouTube].
- Note: "Optional: sign in with YouTube Premium to play without ads. The page opens in the player box's own browser, not your everyday one."
- A feedback line.

**Read-only boxes** (`W.Input`, i.e. `InputBoxInstructionsTemplate` with `SetAutoFocus(false)`):

- `SetText(value)`; `OnEditFocusGained` → `HighlightText()`.
- `OnTextChanged(self, userInput)`: when `userInput`, restore the value and highlight it again. This is the Cell pattern of DESIGN-v1.2 §5.6.
- Enter or Escape → `ClearFocus()`.
- Tooltip: "Click the box, then press Ctrl+C to copy."
- No `CopyToClipboard`: it is protected (DESIGN-v1.2 §4.9).

**The Client ID box** is `W.Input(parent, L["Client ID: 32 characters"])` with `SetMaxLetters(80)`, so pastes with spaces still fit.

- **Normalising, on every change:** `MS.NormalizeClientId(text)` removes ASCII spaces, tabs and line breaks and lowercases the rest. The result is valid when it matches `^[0-9a-f]{32}$`.
- **Validation line:**

| Box | Line |
|---|---|
| empty | (nothing; Connect is disabled with "Paste your Client ID first (step 3).") |
| valid | the check-mark texture (`W.CHECK`) + "That looks like a Client ID." |
| valid, but differs from the app's active ID while Spotify is connected | "The app is connected with another Client ID (…%s). Connect switches to this one after you approve it." |
| invalid | "A Client ID is 32 characters: digits and the letters a-f. Copy the Client ID from your app's Settings page, not the Client secret." |

- **Saving:** a valid ID is stored in `NeedleDropperDB.music.clientId` on Enter, on focus loss and on Connect. Emptying the box clears it.
- **Prefill:** the stored ID, else `music.spotify.clientId` from the app's report.

**Buttons:**

| Button | Enabled when | Disabled reason (tooltip) |
|---|---|---|
| Connect Spotify / Sign in again | `MS.CanSendActions()`, and a Client ID is available (valid box, stored, or the app's) | §4.2 refusal texts / "Paste your Client ID first (step 3)." |
| Disconnect… | `CanSendActions`, an active ID is known, and the status is connected, error or premium-required (or a connect was sent this session) | "Spotify isn't connected." / "Disconnect it on the app's Connect page." (the app's ID is not a 32-hex ID, §3.10) |
| Test Spotify, Sign in to YouTube…, Test YouTube | `CanSendActions` | §4.2 refusal texts |
| any of them | not within 5 s of the last send (as built: 10 s, `MusicServices.SEND_GAP`, the app's own `ACTION_INTERVAL_S`; polish, review INGAME-8) | "Just sent: give it a moment." |

The connect button reads "Sign in again" when the status is connected or error, else "Connect Spotify".

**As built (UI\MusicDialog.lua):**

- **Size and layout:** 840 × 590 in **two columns** (it was 560 × 600, one column). Sections A, B and D are in the left column, C (Spotify) in the right one. One column needed about 700 px of height for the four status lines, two dropdowns, two checkboxes, three boxes, five buttons and the notes, more than a 768-unit UIParent leaves; a scrolling body would hide buttons from the gamepad cursor. So nothing scrolls.
- **Nothing is focused by the addon** (the RC#7 rule, also with mouse and keyboard): the player clicks the box they want. The Client ID box is `InputBoxInstructionsTemplate` with `SetMaxLetters(80)`; its `OnTextChanged` is hooked, so the template's instructions keep working.
- **Connect** is also disabled while the box holds text that is not a Client ID ("The Client ID box doesn't hold a Client ID (step 3)."), rather than silently using a stored or reported ID. With an empty box, the stored ID or the app's own is used.
- **Disconnect…** is enabled for the states connected, error, premium-required, needs-relogin, no-player and stuck (or after a connect this session), and needs the app's active ID (or, after a connect this session, the stored one).
- The "just sent" lock is **per request** (`MusicServices.Cooling(action)`), the same rule as the `Send` refusal: a Test Spotify right after a Connect is not refused.
- The Spotify note reads "Playing Spotify songs needs Spotify Premium and Google Chrome (NeedleDropper plays them in its own hidden player). A development app works for its owner and up to 5 accounts added under User Management." (D6, D11).
- The feedback lines show the §4.2 copy and the "Nothing after 10 seconds?" line; they follow the internal message `ND_MUSIC` ("sent", action, at, copy), so a send from Settings or `/needle music` shows there too.
- The two popups are defined in MusicServices.lua (`MS.Confirm`), because Settings row 3 needs the YouTube one without the window.

**Popups** (`StaticPopupDialogs`, with the 12.x `(dialog, data)` signatures of DESIGN-v1.2 §4.5):

- **`NEEDLEDROPPER_SPOTIFY_DISCONNECT`:** "Disconnect Spotify? The NeedleDropper app forgets your Spotify sign-in. Your Client ID stays, so Connect signs you in again." button1 "Disconnect", button2 `CANCEL`, `hideOnEscape`.
- **`NEEDLEDROPPER_YOUTUBE_SIGNIN`:** "Sign in to YouTube? The NeedleDropper app opens YouTube's sign-in page in its player box's own browser window. This is optional: with YouTube Premium, YouTube songs then play without ads." button1 "Open sign-in page", button2 `CANCEL`, `hideOnEscape`.

### 1.4 Settings and saved data

**`ns.DEFAULTS` / `ns.SETTINGS`** (Core.lua, integrator), inserted after `instanceDrops`:

```lua
musicOrder = "spotify-youtube",   -- spotify-youtube | youtube-spotify | spotify | youtube | none
youtubeCorner = "topright",       -- topright | topleft | bottomright | bottomleft
musicFade = true,
resumeMyMusic = true,
```

The `ns.SETTINGS` entries carry the labels, options and tooltips of §1.2. `/needle set musicOrder youtube` and friends work through `ns.CoerceSetting`, like every other setting.

**`NeedleDropperDB.music`** (account-wide, created by `mergeDefaults`):

```lua
music = {
  prefsSet = false,   -- true once the player chose any of the four music settings in game
  clientId = nil,     -- "0123456789abcdef0123456789abcdef" (lowercase hex), or nil
  requests = {},      -- the last 8 requests sent: { action = "connect-spotify", at = <time()> }, oldest first
}
```

At `ADDON_LOADED` it is validated:

- `prefsSet` must be a boolean;
- `clientId` must match `^[0-9a-f]{32}$`, else it becomes nil;
- `requests` keeps only entries whose action is one of the five names and whose `at` is a whole number ≥ 0, newest 8.

**`prefsSet`:**

- Becomes true on `ND_SETTING` for any of the four keys (Settings panel, dialog or `/needle set`), and on `ND_RESET("settings")`, because a reset sends the defaults.
- While it is false, the game sends no PREFS and the app keeps its own config (§5.6).
- While it is false, at `ND_COMPANION_DATA` the four settings take the app's values from `music.prefs` (through `ns.CoerceSetting`, assigned directly, without firing `ND_SETTING`). So the controls show what the app actually does.
- Once it is true, PREFS rides in every HELLO and CONFIG (§2), and the app follows the game.

### 1.5 What "Play music with" means in the addon

`MS.Plan(drop)` is pure. It feeds three existing decisions.

**1. The fit rule's preferred id.** `ns.PreferredProvider()` (Emit.lua) delegates to `MS.PreferredProvider()`:

| Mode | Preferred id |
|---|---|
| spotify-youtube | "spotify" when the app reports Spotify connected, else "youtube" (today's rule) |
| youtube-spotify, youtube | "youtube" |
| spotify | "spotify" |
| none | "youtube" (nothing plays anyway) |

**2. Compatibility stripping** in `ns.BuildEvent`, for ARM and FIRE. It never applies to frames with LOCAL_PLAYBACK, or to the test drop.

| Mode | App ≥ 1.2.1 (`Supports("config")`) | Older app, or no app data |
|---|---|---|
| none | leave out `spotify`, `youtube`, `artist`, `title` | the same |
| spotify | nothing | leave out `youtube` |
| youtube | nothing | leave out `spotify`, `artist`, `title` |
| spotify-youtube, youtube-spotify | nothing | nothing |

With a 1.2.1 app the full song travels, so its title card has the artist and title, and the app applies the order itself (§3.3). "Neither" is enforced here, whatever the app version.

**As built (`MusicServices.Strip`, called by `ns.BuildEvent`):** `ns.Emit` now computes the live flags before it builds the event, so a FIRE whose in-game file plays (LOCAL_PLAYBACK) is never stripped. An ARM never carries that flag, so an ARM is exempt when the drop has an in-game file and `localPlayback` is on. The test drop (`id = "test"`) is exempt. A TEST-flagged FIRE of a real drop (the window's Test, a Journal replay) **is** stripped in "Neither": "Neither" never plays.

**3. Fading the zone music** (`Director.ShouldDuck`, RC#6). A non-local drop fades the zone music only if `MS.Plan(drop).canPlay` is true, and then only under the existing rule (`duckWithoutApp`, or `D.AppKnown()`). `canPlay` is computed as:

```text
wantSp = order is not youtube or none     hasSp = a Spotify id, or both artist and title
wantYt = order is not spotify or none     hasYt = a YouTube id
spReady = no app data, or status.spotify == "connected", or a connect was sent this session
ytReady = no app data, or status.youtube == true
canPlay = (wantSp and hasSp and spReady) or (wantYt and hasYt and ytReady)
```

The title card is unchanged in every mode. In-game files play in every mode.

### 1.6 Slash command

`/needle music [status | connect [clientId] | disconnect | test spotify | test youtube | youtube]`

- On its own: opens the dialog.
- `status`: prints the §1.1 lines.
- `connect [clientId]`: stores a valid ID first (otherwise prints the validation text), then sends the request.
- `disconnect`, `test spotify`, `test youtube`, `youtube` (the sign-in): send the request and print its §4.2 copy.

The USAGE entry: "music [status|connect|disconnect|test spotify|test youtube|youtube] - Spotify and YouTube (opens Music services)".

**As built:** without the window (no UI files), `/needle music` prints the four status lines instead; an unknown subcommand prints the usage.

### 1.6a `/needle report` (as built, decision D16)

`/needle report` opens `NeedleDropperReportDialog` (UI\ShareDialogs.lua, the Share dialog's construction: a read-only `ScrollingEditBoxTemplate` whose edits are undone, focused and selected with `W.FocusBox`, so the player presses Ctrl+C) and prints one chat line: "report ready: press Ctrl+C to copy it from the box, then paste it into your bug report. It holds no account names, Client IDs or tokens." Without the window the report goes to chat, line by line.

The text (`SelfTest.ReportText()`, plain English for the developer like `/needle status`, no `|` anywhere):

- the date; the addon version, the client's `GetBuildInfo()` (version, build, interface) and locale;
- the app: not found / too old (older than 1.2.1) / its version, when its report was written and how long before login; the Spotify state, mode and plan, the cause the app named (`music.spotify.problem`: ", cause no-window"; one of the checker's fixed codes; polish-fix, review RG-3) and whether a Client ID is set (never the ID itself, never the account name); YouTube on or off and the player browser; the transports; the last request's action, time and **result code** (the app's result text can name the Spotify account, so it is left out);
- the beacon: on or off, drawn and configured cell size, rows, size, the rows a song strip may use, the measured clearance and where from, the corner, the frame on screen and the queue counters, and the clearance warnings; the chat log: off, or on with logging, lines sent, queued and stale;
- the four music settings and whether they were chosen in game; the settings that matter (enabled, historyMode, scope, cooldownMin, the three dwell times, instanceDrops, suppressOnTaxi, combatFadeSec, maxDropSec, duckMethod, kickZoneMusic, duckWithoutApp, localPlayback, toast, chatlogPadBytes, debug);
- the active soundtracks and the number of drops, what plays now;
- the last 5 journal entries (time, drop id, artist – title, how it played);
- the last error NeedleDropper's own handlers caught this session (`ns.NoteError`, recorded by Core's event dispatch and Emit's transport guard; at most 300 bytes), or "none this session".

### 1.7 Rules

- DESIGN-v1.2 §4.9 is unchanged, and the static tests keep checking it:
  - no `CopyToClipboard`, `EnableKeyboard`/`OnKeyDown` or `ShowUIPanel`;
  - `ReloadUI()` only from a click, inside `pcall`.
- Every string from the app (`account`, an action's `text`) goes through `ns.EscapeText` before `SetText` or `AddLine`.
- Gamepad (RC#7): the dialog joins `FrameControlsManager` through `W.ManageInCombat`. No box is focused programmatically while the gamepad UI is on (`W.FocusBox`).
- Combat: nothing here is protected (textures, system messages), so the Settings rows stay usable. The dialog hides with the other frames. A chat line waits in ChatLink's lockdown queue as usual.
- The window keeps its three tabs.

---

## 2. Protocol extension (v1.2.1)

### 2.1 Changes at a glance

| Item | v1.2.0 | v1.2.1 |
|---|---|---|
| Event types | 0..5; 6..15 rejected | + **6 CONFIG**; 7..15 rejected |
| Tags | 0x01..0x0A known; 0x0B+ skipped into `extra` | + **0x0B PREFS**, **0x0C ACTION**, **0x0D CLIENT_ID**; 0x0E+ skipped |
| `TYPE_TAGS` | HELLO {} | HELLO {0x01, 0x0B} (the playing drop's placeKey, decision D3, and PREFS); CONFIG {0x0B, 0x0C, 0x0D}; ARM, FIRE, CANCEL, PING, NONE unchanged (song frames never carry PREFS) |
| `MAX_TAG` (Lua) / `_MAX_KNOWN_TAG` (Python) | 0x0A | 0x0D, so `frameSize` / `_frame_size` count the new tags |
| Fit rule | | It never touches 0x0B–0x0D; HELLO and CONFIG frames are always below every budget (§2.6). Steps 3 and 5 changed for findings F3 and F4 (as built: PROTOCOL-v2 §2.1) |
| Trailer | Fletcher-16, 2 bytes | **as built: a 4-byte frame check** (finding F1, decision D2; PROTOCOL-v2 §2, §4.2): every frame is 2 bytes longer, 9..264 bytes. The check ends with two final rounds (review RP-1): without them a change of the last body byte and the same change of the trailer's low byte passed |
| Decoder text | lenient | **as built:** strict UTF-8 query and label (decision D5) |
| Whitening, geometry | | unchanged |

### 2.2 The CONFIG frame

```text
offset  size  field
0       1     0x4E
1       1     0x26                       version 2, type 6
2       1     seq                        the next ns.Emit seq, as for every event
3       1     flags                      live flags (ns.ComputeFlags); never TEST
4       1     bodyLen
5       n     [0x0B PREFS] [0x0C ACTION] [0x0D CLIENT_ID]    each optional, in ascending order
5+n     4     frame check (as built; was Fletcher-16, 2 bytes, in this spec)
```

- A CONFIG carries PREFS (when set), an ACTION, or both.
- CLIENT_ID only goes with ACTION connect-spotify or disconnect-spotify, and a frame that carries CLIENT_ID carries no PREFS (§2.6). **As built this rule is load-bearing:** with the 4-byte trailer all three together are 41 bytes, two strip rows, and the codec does not enforce it (`EncodeFrame` encodes whatever the event holds), so `MS.Send` must never put PREFS beside a CLIENT_ID.
- A CONFIG with none of the three is never emitted.

### 2.3 TLV layouts

**0x0B PREFS**: exactly 4 bytes; allowed in HELLO and CONFIG.

| Byte | Field | Values |
|---|---|---|
| 0 | order | 0 not set · 1 spotify-youtube · 2 youtube-spotify · 3 spotify · 4 youtube · 5 none |
| 1 | corner | 0 not set · 1 topright · 2 topleft · 3 bottomright · 4 bottomleft |
| 2 | fade | 0 not set · 1 on · 2 off |
| 3 | resume | 0 not set · 1 on · 2 off |

"Not set" means "the app keeps its own value". The 1.2.1 addon always sends all four (§1.4); 0 exists for future writers. Later preferences get new tags, not longer values (PROTOCOL-v2 §8.3: new fields use new tags).

**0x0C ACTION**: exactly 6 bytes; allowed in CONFIG.

| Byte | Field | Values |
|---|---|---|
| 0 | code | 1 connect-spotify · 2 disconnect-spotify · 3 test-spotify · 4 test-youtube · 5 youtube-sign-in |
| 1 | check | code XOR 0xFF |
| 2..5 | issuedAt | uint32 BE: the game's `time()` when the player clicked (Unix seconds, from the PC clock) |

**0x0D CLIENT_ID**: exactly 16 bytes; allowed in CONFIG.

- The bytes are the 16 that the 32 hex characters spell; decoders turn them back into 32 lowercase hex characters.
- The Connect page keeps accepting 8–64 letters and digits (`panel_api._v_client_id`); only 32-hex IDs travel from the game.

### 2.4 Encoders (Lua `EncodeFrame` ≡ Python `build_frame` / `frame_from_event`)

New event-table fields. In Lua:

```lua
prefs    = { order = "spotify-youtube", corner = "topright", fade = true, resume = true },
action   = { name = "connect-spotify", at = 1762380123 },
clientId = "0123456789abcdef0123456789abcdef",
```

`frame_from_event` takes the same camelCase keys; `build_frame` takes `prefs=`, `action=` and `client_id=`.

- **`prefs`:** the order and corner names match ASCII case-insensitively; fade and resume must be booleans. Anything else is "not set" (0). When all four are not set, there is no TLV.
- **`action`:** `name` must be one of the five names (ASCII case-insensitive) and `at` must be a number, which is floored and clamped to 0..4294967295. Otherwise there is no TLV.
- **`clientId`:** ASCII whitespace is removed; the rest must be 32 hex digits, in either case. Otherwise there is no TLV.
- **Per type:** fields outside `TYPE_TAGS[type]` are dropped, as today; a PREFS on a FIRE, for example, is dropped.
- **Strict encoding:** `encode_frame(Frame)` serialises the new fields strictly. It raises `ValueError` for an enum code out of range, an issued time outside uint32, or a client id that isn't 32 hex. `Frame.extra` may only use tags 0x0E..0xFF.

### 2.5 Decoders (Python `decode_frame` ≡ Lua `DecodeFrame`)

Additional rules, in the same never-raise style:

- **Types:** 6 is accepted; 7..15 are rejected.
- **Rejected as damaged:**
  - a PREFS length other than 4;
  - an ACTION length other than 6, or a check byte other than `code XOR 0xFF`;
  - a CLIENT_ID length other than 16.
- **Unknown enum values are not rejected:** that field is simply absent (nil / None) and the frame decodes. An unknown action code with a valid check byte decodes as an action without a name, which the app ignores. That lets a future addon add values.
- **No per-type checks in decoders:** they don't enforce `TYPE_TAGS`. The app acts on PREFS only from HELLO and CONFIG frames, and on ACTION and CLIENT_ID only from CONFIG frames.

**Results:**

- **Python** `Frame` gains:
  - `prefs: Optional[Prefs]`, frozen, with `order` and `corner` (str | None) and `fade` and `resume` (bool | None);
  - `action: Optional[Action]`, with `code`, `name` (str | None) and `issued_at`;
  - `client_id: Optional[str]`.

  `describe()` adds e.g. `prefs=youtube-spotify/topleft/fade/resume action=connect-spotify@1762380123 client=…cdef`.
- **Lua** `DecodeFrame` returns `ev.prefs = { order, corner, fade, resume }`, `ev.action = { code, name, at }` and `ev.clientId`.
- **The v1 decoders need a guard.** `protocol.decode_beacon_bytes` and the NDROP1 chat parser both validate the type with `EventType(t)`. Once `EventType.CONFIG = 6` is in `models.py`, they would accept 6, so both get an explicit `t > 5 → None`.

### 2.6 Sizes and the one-row rule

Measured with the repo's `adaptive_layout` and `capacity` (vectors in §2.13), **as built with the 4-byte frame check** (every frame 2 bytes longer than this spec first had). `Capacity(1) = 39` bytes.

| Frame | Bytes | Columns | 3 px | 2 px | 4 px |
|---|---|---|---|---|---|
| HELLO, music choices not set in game (as in 1.2.0) | 9 | 32 | 96 × 3 | 64 × 2 | 128 × 4 |
| HELLO + placeKey (the steady HELLO after a FIRE, D3) | 13 | 48 | 144 × 3 | 96 × 2 | 192 × 4 |
| HELLO + PREFS | 15 | 48 | 144 × 3 | 96 × 2 | 192 × 4 |
| HELLO + placeKey + PREFS | 19 | 64 | 192 × 3 | 128 × 2 | 256 × 4 |
| CONFIG, PREFS only | 15 | 48 | 144 × 3 | 96 × 2 | 192 × 4 |
| CONFIG, PREFS + ACTION (tests, YouTube sign-in, connect without an ID) | 23 | 80 | 240 × 3 | 160 × 2 | 320 × 4 |
| CONFIG, ACTION + CLIENT_ID (connect, disconnect) | 35 | 112 | 336 × 3 | 224 × 2 | 448 × 4 |
| All three (never emitted: two rows) | 41 | 64 × 2 rows | 192 × 6 | 128 × 4 | 256 × 8 |

- **One row at every cell size.** Every HELLO and every CONFIG that obeys §2.2 is a single row. The widest, 448 px at 4 px cells, equals `MAX_STRIP_WIDTH` and fits inside the 512 × 48 capture (`test_every_music_services_frame_is_one_row`, `test_music_frames_size_table`).
- **Why one row mattered (F1).** The column cover F1 describes needs the same column in at least two rows (bytes i, i+42, i+84). The attack sweep found none of 1,280 one-row HELLO and 300 CANCEL frames breakable. As built, the 4-byte frame check also closes F1 for multi-row strips (PROTOCOL-v2 §4.2), so one row is now about the strip's size and the idle strip's height.
- **Headroom.** Leaving PREFS out of frames that carry a CLIENT_ID keeps 4 bytes below `Capacity(1)`. With all three a CONFIG would be 41 bytes and two rows, so the §2.2 rule is required, not just headroom.
- **Tested.** `MS.Send` asserts `#EncodeFrame(ev) <= 39` (§5.2).

### 2.7 Beacon (Beacon.lua)

Today `B.Show` makes every event that is neither a transient nor a PING the steady state. CONFIG is excluded from that and gets its own queue.

- **Queue B** (`B.cfgQueue`) holds at most 4. When it is full, the oldest CONFIG without an ACTION is dropped first, else the oldest.
- **Holds:** 3.0 s for a CONFIG with an ACTION; 2.0 s for PREFS only.
- **When it shows** (`B.Pump`): only when FIFO A (FIRE, CANCEL, PING; its rules unchanged) has nothing on screen or queued, and the steady state is not an ARM with full-song time left (`fullLeft > 0`).
- **Preemption:**
  - A FIFO-A event that arrives while a CONFIG is on screen takes the screen in the same `Pump`, so FIRE latency is unchanged.
  - The CONFIG goes back to the head of queue B with its full hold.
  - A CONFIG never shortens or delays a FIRE, a CANCEL, a PING or an ARM's song window.
- **Re-show:** 8.0 s after an ACTION CONFIG first completes its hold, the same event (same table, same seq, same bytes) is queued once more with a 2.0 s hold. The app treats it as a duplicate (§2.10); it exists for a capture missed behind an overlay (F2).
- **Visibility:** with the beacon setting off, drawing an ACTION CONFIG calls `B.ForceShow(hold + 0.5)`, the pattern of "Send a test signal". PREFS-only CONFIGs are not forced.
- **Steady HELLOs carry the current choices.**
  - `B.SetPrefs(prefs | nil)` (called by MusicServices) stores them.
  - Every HELLO the beacon draws, emitted or synthesized (`playingHello`, `idleHello`), is drawn with `prefs = B.prefs`.
  - `SetPrefs` bumps the layout version, so the HELLO on screen changes at once.
- **Status:** `B.Status()` gains `configQueued` and `configDropped`.

**As built (Beacon.lua):**

- `B.Pump` decides in this order: a transient within its hold; the next FIFO-A event (it takes the screen from a CONFIG); an ARM's full-song window; queue B; the steady state. **A new ARM that arrives while a CONFIG is on screen takes the screen too** (the rule "a CONFIG never delays an ARM's song window" needs it; the FIFO-A list alone would miss it). The preempted CONFIG goes back to the head of queue B with its full hold.
- The re-show belongs to the queue entry (shown at the first completed hold + 8 s, once); a re-show is never emitted again (no `ns.Events` entry, no chat line). One scheduled wake-up covers holds, the ARM window and pending re-shows.
- **HELLOs as drawn** are beacon-owned copies of the event: `prefs = B.prefs` and, **whenever DROP_ACTIVE is set, the playing drop's placeKey** (D3): after a FIRE the FIRE's own key; after a CANCEL that keeps DROP_ACTIVE, and for an emitted HELLO with DROP_ACTIVE (the first HELLO after a `/reload` during a drop), the key of `Director.active.drop`, else of the last FIRE shown. Without DROP_ACTIVE a drawn HELLO has no placeKey. The emitted event and its chat line carry no placeKey (PROTOCOL-v2 §2.5). `B.Status().current` is the drawn copy, so it decodes to the strip. Sizes at 3 px: 96 × 3 idle, 144 × 3 with PREFS or with a placeKey, 192 × 3 with both.
- **For the engine:** that HELLO keeps LOCAL_PLAYBACK when the playing drop's in-game file plays (DROP_ACTIVE is set), so the D3 late start must respect LOCAL_PLAYBACK as a FIRE would (show and log, never play). As built after review INGAME-3, the beacon takes LOCAL_PLAYBACK from `Director.active.isLocal` for the HELLO it draws after a CANCEL (the CANCEL of another place does not carry it), and after review RP-3 an ARM that becomes the steady frame within `PLAYING_HOLD` (3 s) of the playing HELLO first gives that HELLO its 3 s on screen (FIFO A, right after the FIRE's hold): at a spot with a landmark and the zone drop the Director arms the zone at once, and the playing HELLO was never drawn.
- `B.Status()` also has `configReshows`, `configShown` and `prefs`.

### 2.8 Chat-log line (ChatLink.lua)

- A CONFIG goes out as `NDROP2 <base64url>` like every event, with the full frame.
- A CONFIG with an ACTION is padded like FIRE, CANCEL and PING, so it reaches the disk at once. A PREFS-only CONFIG is not padded.
- In the lockdown queue, a CONFIG with an ACTION that has waited more than 20 s is dropped with its padding (`CONFIG_MAX_AGE = 20`); the app would reject it after 45 s anyway. Other CONFIG lines are kept, like other lines.
- HELLO lines carry PREFS once set. HELLOs are emitted at login, after a drop and on zone changes, which is how an app that only reads the chat log converges.

### 2.9 Emit.lua and MusicServices.lua

- **HELLO:** `ns.BuildEvent(EV.HELLO)` sets `prefs = ns.MusicServices.WirePrefs()`, which is nil until `prefsSet`.
- **CONFIG:** `ns.BuildEvent(EV.CONFIG, nil, 0, { prefs =, action =, clientId = })` copies those three fields. CONFIG carries no drop fields.
- **ARM and FIRE:** the §1.5 stripping and `prefer`.
- **`ns.Emit`** is otherwise unchanged: seq, flags, `ns.Events` and both transports.
- **`ns.MusicServices.Send(name)`** builds the CONFIG event, records the request in `db.music.requests`, and calls `ns.Emit(EV.CONFIG, nil, 0, opts)`.
  - It refuses (returns nil plus a reason) when the app can't take requests (`not CanSendActions()`), when the same action was sent less than 5 s ago (as built: 10 s, the app's limit, see below), and when the action needs a client id it doesn't have.
  - Connect carries CLIENT_ID when the game has an ID (typed or stored). Otherwise the app uses its own.
  - Disconnect carries the app's active ID (`music.spotify.clientId`), else the stored one.
- **After a music setting changes,** `ns.MusicServices` calls `B.SetPrefs` at once. It also emits a PREFS-only CONFIG 1.0 s after the last change (debounced), which reaches the chat log and gives the change a fresh seq. That CONFIG is skipped when the app's report says it is older than 1.2.1, because it would only show an unreadable strip to it.

**As built:**

- `ns.Emit` computes the live flags first and hands them to `ns.BuildEvent`, so the stripping can see LOCAL_PLAYBACK (§1.5). For a CONFIG it then clears TEST: during a test drop the live flags would lend a CONFIG the drop's TEST bit, and the engine treats TEST frames as link checks.
- `MusicServices.Send(action, opts)` returns `ev, copy` or `nil, reason` and prints one chat line either way (`opts.quiet` prints nothing). Refusals, in order: an unknown action; no app data / an app older than 1.2.1 (§4.2); the same request less than `SEND_GAP` = 10 s ago ("Just sent: give it a moment."; it was 5 s, so a request 5 to 10 s after the same one was announced as sent and refused by the app, whose limit is `music_config.ACTION_INTERVAL_S` = 10 s: polish, review INGAME-8; `test_v121_polish_addon.py` reads both constants; the app measures the game's requests by their click, so one sent as the buttons come back is never refused: §3.4, review RG-2); connect with no Client ID at all (typed, stored or the app's): "Paste your Client ID first (step 3)."; disconnect with neither the app's ID nor a stored one: "Disconnect it on the app's Connect page."; and the **one-row guard**: the CONFIG is encoded first and refused ("Not sent: the request did not fit the beacon. Please report this with /needle report.") if it is longer than `Capacity(1)` (39 bytes). The guard never raises in game.
- Connect and disconnect carry CLIENT_ID and never PREFS (35 bytes); the other three carry PREFS (when set) and the ACTION (23 bytes).
- After a send, `ns.Fire("ND_MUSIC", "sent", action, at, copy)`, and `SEND_GAP` (10 s) later `("ready", action)`, which the dialog uses for its feedback lines and buttons.
- NeedleDropperDB.music is validated by Core.lua's `initDB` (with the other SavedVariables), `ns.MUSIC_ACTIONS` lists the five names.

### 2.10 Delivery and dedupe

| | PREFS | ACTION |
|---|---|---|
| Semantics | level: the latest value wins | edge: do it once |
| Delivered by | every HELLO (the beacon's steady state, HELLO chat lines) and every CONFIG | its CONFIG: the beacon twice, the chat log once |
| Baseline frame (the first frame after the app starts or finds the window) | applied | ignored |
| Engine dedupe (`Frame.key`, 15 s) | bypassed: MusicConfig sees every decoded frame first (§3.1) | bypassed, the same way |
| App-side identity | none: compare with config.json, write only on a change | `(code, issuedAt)`, remembered for 600 s |
| Freshness | none | −5 s ≤ now − issuedAt ≤ 45 s |

An unchanged HELLO captured at 4 Hz produces no write: the pixel transport only emits frames that changed, and MusicConfig compares before writing.

### 2.11 Backward compatibility

**As built, there is none between 1.2.0 and 1.2.1, in either direction** (decisions D1 and D2). Protocol v2 had never been released (only the owner's PC ran it), so the F1 fix changed its trailer without a version bump: every 1.2.1 frame ends in the 4-byte frame check, which a 1.2.0 decoder reads as a length mismatch, and a 1.2.0 frame (Fletcher-16) fails a 1.2.1 decoder the same way. So:

- a 1.2.0 app's beacon reader reports every 1.2.1 strip as `payload invalid` (a confirmed header; after `LOST_AFTER` such reads it logs "beacon no longer readable"), and its chat-log reader ignores every 1.2.1 line; no drop plays;
- a 1.2.1 app reads no 1.2.0 addon's frame;
- the addon and the app must be updated together. The 1.2.1 addon's own report check ("Update the NeedleDropper app to 1.2.1…", §1.1) is the in-game hint.

*Originally specified (superseded):* with the 1.2.0 trailer, HELLO + PREFS decoded on a 1.2.0 app as a HELLO with PREFS in `Frame.extra`, every CONFIG as None (and as an `invalid` read on its screen reader), and the mixed-version table below held. It is kept as the record of the design intent: the type-6 guard (CONFIG rejected by 1.2.0) and the new-tags rule still make 1.2.1 frames safe for any later decoder that keeps the trailer.

| | App 1.2.0 | App 1.2.1 |
|---|---|---|
| **Addon 1.2.0** | as today | *(as specified)* never sees PREFS or CONFIG; keeps its own config. *As built: reads no frame.* |
| **Addon 1.2.1** | *(as specified)* CONFIG ignored; PREFS in HELLO skipped. *As built: reads no frame.* | everything |

### 2.12 Security

**Who can reach the app:** only the WoW client area (the beacon) and WoW's own chat log (strict system-line parsing). Other players can't inject into either (F8). The one remaining actor is another addon in the same client. It can already draw any strip and send any system message, so v1.2 FIRE frames have the same exposure.

| Concern | Rule |
|---|---|
| Secrets in transit | None. The Spotify Client ID is not a secret: PKCE uses no client secret, and every authorize URL carries the ID in the clear. The refresh token never leaves `spotify_token.bin`, and the game never sees a token. **As built (D13, review RCOMP-3):** the in-app player's SDK gets the access token (only) from `GET /api/player/spotify-token`, which needs the same-origin fetch headers, the `X-ND-Token` and the `X-ND-Key` launch key. The launch key is in the fragment of the address the app opens its player window with (`--app=http://localhost:<port>/player#k=<key>`): a fragment never reaches a server, so a process that fetches `/player` (which serves the `X-ND-Token`) does not get it -- not another Windows account's process, not another website's navigation (`/player` refuses `Sec-Fetch-Site` cross-site and same-site). The same key admits the player page's event stream: a page without it (left running by an earlier start, opened by hand) is answered `{"cmd": "retire"}`, never counts as the player, and closes itself (review ON-1). Every `POST /status` report needs it too (the SDK's since RCOMP-3; the YouTube, local and CSP ones since the polish pass: they end songs and show the box): a report without it is refused with 401, and `player.js` (whose one `post()` sends the key with every report) retires on a 401. The deck also listens on `[::1]` with `SO_EXCLUSIVEADDRUSE`, so no other program can answer `localhost` first (review RCOMP-4). |
| A request opening arbitrary pages | No TLV carries text or URLs. The app opens only Spotify's authorize URL (built by `build_authorize_url` from its `AUTHORIZE_URL` constant) and the constant `YOUTUBE_SIGNIN_URL`. Fuzz-tested (§5.3). |
| What an action can do | Connect starts a sign-in the player must approve on accounts.spotify.com. YouTube sign-in opens Google's page. A test plays 15 s of a fixed song. Disconnect deletes the local tokens, only for the matching Client ID, and Connect restores it. |
| Damaged frames | One-row frames, the 4-byte frame check (as built; F1 closed for every strip, PROTOCOL-v2 §4.2) plus whitening, the ACTION check byte, exact TLV lengths, strict UTF-8 text, and two identical pixel reads. |
| Replays | The freshness window against the shared PC clock and the identity memory. Baseline frames never trigger actions. The chat-log reader starts at the end of the file, so a re-read after truncation only finds stale requests. |
| A Client ID planted by someone else | It can only start a consent page. The working sign-in stays until a new one succeeds (§3.5). |
| Floods | In the addon, a button stays disabled for 10 s after a click (as built; the app's own once-per-10-s limit, review INGAME-8), and queue B holds 4. In the app, the §3.4 limits. |
| A player pasting the Client secret | The format is the same, so it can't be caught. The copy says "Client ID, not Client secret" in three places, and the timeout notification names INVALID_CLIENT. The secret would only reach local files: SavedVariables, config.json, and the chat log when that channel is on. |

### 2.13 Test vectors

**As built** (the 4-byte frame check, D2, with its two final rounds, RP-1): the bytes before the trailer are those this spec first listed; every trailer changed. The authoritative copy, with the decoded fields, is `tests/vectors/protocol2/frames.json` (PROTOCOL-v2 §7); both encoders write these bytes and both decoders read them (`test_documented_vectors`). A 1.2.0 decoder reads none of them (§2.11). The plain HELLO is `4e 21 00 00 00 29 50 ec 87` (`NDROP2 TiEAAAApUOyH`).

**1. HELLO seq 0, PREFS {spotify-youtube, topright, fade on, resume on}**
- Frame (15 B): `4e 21 00 00 06 0b 04 01 01 01 01 65 4e 5a ef`
- Chat line: `NDROP2 TiEAAAYLBAEBAQFlTlrv`

**2. HELLO seq 7, flags 0x20, PREFS {youtube-spotify, bottomleft, fade off, resume on}**
- Frame (15 B): `4e 21 07 20 06 0b 04 02 04 02 01 53 9b 69 88`
- Chat line: `NDROP2 TiEHIAYLBAIEAgFTm2mI`

**3. CONFIG seq 12, PREFS {youtube, topleft, fade on, resume off}**
- Frame (15 B): `4e 26 0c 00 06 0b 04 04 02 01 02 05 5b 6e d2`
- Chat line: `NDROP2 TiYMAAYLBAQCAQIFW27S`

**4. CONFIG seq 13, PREFS {spotify-youtube, topright, on, on} + test-youtube issued at 1762380000**
- Frame (23 B): `4e 26 0d 00 0e 0b 04 01 01 01 01 0c 06 04 fb 69 0b c8 e0 10 62 81 25`
- Chat line: `NDROP2 TiYNAA4LBAEBAQEMBgT7aQvI4BBigSU`

**5. CONFIG seq 14, connect-spotify issued at 1762380123 + CLIENT_ID `0123456789abcdef0123456789abcdef`**
- Frame (35 B): `4e 26 0e 00 1a 0c 06 01 fe 69 0b c9 5b 0d 10 01 23 45 67 89 ab cd ef 01 23 45 67 89 ab cd ef 52 ea a8 c4`
- Chat line: `NDROP2 TiYOABoMBgH-aQvJWw0QASNFZ4mrze8BI0VniavN71LqqMQ`

**6. Bound, never emitted: CONFIG seq 15, flags 0x03, PREFS {none, bottomright, off, off} + disconnect-spotify issued at 4294967295 + CLIENT_ID `ff` × 16** (41 B: two strip rows, which is why §2.2 forbids PREFS beside a CLIENT_ID)
- Frame (41 B): `4e 26 0f 03 20 0b 04 05 03 02 02 0c 06 02 fd ff ff ff ff 0d 10 ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff 5f 0d de 31`
- Chat line: `NDROP2 TiYPAyALBAUDAgIMBgL9_____w0Q_____________________18N3jE`

**7. (D3) HELLO seq 9, flags 0x20 (DROP_ACTIVE), the playing drop's placeKey (zone:1411 = 18367)**
- Frame (13 B): `4e 21 09 20 04 01 02 47 bf 34 02 85 e7`
- Chat line: `NDROP2 TiEJIAQBAke_NAKF5w`

**8. (D3) the same with PREFS {spotify-youtube, topright, on, on}**
- Frame (19 B): `4e 21 09 20 0a 01 02 47 bf 0b 04 01 01 01 01 48 94 80 fc`
- Chat line: `NDROP2 TiEJIAoBAke_CwQBAQEBSJSA_A`

---

## 3. Companion

### 3.1 Where frames enter

- **New module** `companion/needledropper/music_config.py` (C2): `MusicConfig(app, *, clock=time.time, notifier, …)` with one worker thread, `needledropper-music`.
  - `feed(frame, transport, *, baseline=False)` only queues the frame.
  - So no transport or engine thread ever waits on the disk or on a browser.
- **`Companion.submit`** (the transports' `on_event`) calls `self.music.feed(event, transport)` for every v2 frame that carries PREFS or is a CONFIG.
  - It does so after `_note_frame` and **before** the pause gate.
  - While the app is paused, PREFS still apply and connect, disconnect and the YouTube sign-in still run; tests answer `paused`.
- **`PixelTransport.on_baseline: Optional[Callable[[Frame, str], None]]`** (C1) is new.
  - It is called, outside the transport's lock, with the v2 frame that became the baseline.
  - `Companion` sets it to `lambda f, t: self.music.feed(f, t, baseline=True)` after building the transports, and again after `restart_transports`.
- **The engine** treats CONFIG like a PING that never plays: `_accept` does the bookkeeping (counts, "addon seen") and nothing else. Adding `EventType.CONFIG = 6` gives it `counts["CONFIG"]`.

**As built:**
- `Companion.submit` hands every frame to `MusicConfig.feed`, which keeps only CONFIG frames and HELLOs with PREFS (`MusicConfig.wants`); PREFS in a song frame are ignored.
- `Companion.hook_baseline(transport)` sets `on_baseline` on every transport that has the attribute, when the transports are built and again when they are rebuilt. Today that is the pixel link (transport/pixel.py): it calls it once per baseline, outside its lock, never for a `same` read.
- What MusicConfig uses of the companion: `cfg`, `paths`, `deck`, `engine`, `providers`, `paused`, `notifier`, `apply_music_choices(changes)`, `connect_spotify_from_game(cid, on_done)`, `current_login()`, `spotify_disconnect()`, `push_game_data(flush)`, `note_youtube_sign_in(when)` and `nudge(what)`. It hears the engine's notes through `Companion.on_engine_status`.

### 3.2 PREFS → config.json

| PREFS field | config.json key (new keys in bold) | Live effect (`Companion._apply`) |
|---|---|---|
| order | **`music.order`** | `engine.set_music_order(order)`: routes and pre-warm (§3.3) |
| corner | **`deck.playerCorner`**; when it changes, also `deck.playerPosition = ""` | `deck.configure_player(corner=…)`: the next box opens there, and an open box moves (§3.9) |
| fade | **`music.fade`** | engine `fade_in_ms`/`fade_out_ms` = `fadeInMs`/`fadeOutMs` when on, 0 when off (every provider); the Spotify provider's own `fade` switch follows the same value (see below) |
| resume | `spotify.restorePrevious` | the Spotify provider (`rebuild_providers`) |
| — | **`music.fromGame`** = true, at the first PREFS ever | |

**Defaults.** `DEFAULT_CONFIG` gains `"music": {"order": "spotify-youtube", "fade": true, "fromGame": false}` and `"deck": {…, "playerCorner": "topright"}`. `normalize_config` replaces invalid values with these defaults.

While `fromGame` is false, the app behaves exactly as 1.2.0 does:
- fades come from `fadeInMs`/`fadeOutMs` and `spotify.fade`;
- the order is spotify-youtube.

*As built:* `music.order` and `music.fade` always apply (`config.music_choices`, `config.effective_fades`). Their defaults are the 1.2.0 behaviour, so until the game sends PREFS the app plays exactly as 1.2.0 did, and a value edited in config.json by hand also works. `music.fromGame` only decides whether the game's fade also overrides `spotify.fade` (below).

**How the Spotify provider gets the game's fade.** `SpotifyProvider.__init__` reads its switch from the `spotify` section only (`self._fade = bool(cfg.get("fade", True))`, providers/spotify.py L540), and `cli.build_providers` passes it `cfg.get("spotify")` (cli.py L264). A rebuild alone therefore changes nothing. When `music.fromGame` is true, `cli.build_providers` passes `{**cfg["spotify"], "fade": cfg["music"]["fade"]}` instead.

- "Off" is already enforced by the engine's zeros: the provider ramps only when `fade_in_ms > 0` (L1088) and fades out over `fade_out_ms` (L1439), so with 0 nothing fades.
- The override matters for "on", when an advanced user had set `spotify.fade = false` in config.json.

**Applying a change:**
- "Not set" fields (0) and unknown values change nothing.
- MusicConfig computes which keys differ from the current config, and only then calls `Companion._write_config(changes)`. That method writes under `Companion._lock` and runs any rebuilds afterwards, as for a Connect page edit.
  - *As built:* through `Companion.apply_music_choices(changes)` (`music_config.prefs_changes` computes them). A change of `music.fade` or `music.fromGame` while `fromGame` is set rebuilds the providers, because the Spotify provider's own fade switch follows it.
- Then MusicConfig pushes the `music.prefs` echo to `Data.lua` (§3.10).

**Precedence.**
- A service plays only when the game's order includes it **and** its Connect page switch is on.
- The Connect page shows the game-owned values read-only: "set in game: Options > AddOns > NeedleDropper".
- `spotify_connect` still turns Spotify's switch on, because connecting is an explicit request to use it. It never touches `music.order`.

**Two WoW accounts on one PC** with different choices: the account that is logged in wins, and each switch between them writes config.json once.

### 3.3 Engine (engine.py, C1)

`Engine.set_music_order(order)` is new: thread-safe, default spotify-youtube. `_routes_v2` builds:

| Order | Routes, in order |
|---|---|
| spotify-youtube | Spotify id → Spotify search → YouTube id → late Spotify search → other providers (today's chain) |
| youtube-spotify | YouTube id → Spotify id → Spotify search → late Spotify search → other providers |
| spotify | Spotify id → Spotify search → late Spotify search |
| youtube | YouTube id |
| none | no routes |

In "none", the FIRE is counted and logged at INFO ("Play music with is Neither in the game"). It returns before `_start_drop`: no card, no window, no notification. LOCAL_PLAYBACK frames behave as today in every mode.

The "none" check must come first in `_on_fire2`, before the existing `if not local and not _has_song(song)` branch (engine.py L964). The addon strips every song field in "none" (§1.5), so that branch would otherwise log the warning "FIRE … carries no song" for every drop. Done in this order, it is one INFO line per drop.

**Pre-warm at ARM** follows the order:
- no Spotify search and no device wake in youtube or none;
- no YouTube preload in spotify or none.

**Reasons for failures.** The `noSource` note gains `reasons`, the list `_play_chain` already builds, so a failed test can say why.

**As built:**
- `Engine(…, music_order=)` and `set_music_order()` are thread-safe, and an unknown value means the default. `status()["musicOrder"]` reports the order.
- In "none", a FIRE also ends an external drop that still plays from before the switch: the game has moved on to another drop.
- Pre-warm:
  - In youtube-spotify, the YouTube preload runs before the Spotify search. The search still runs, for the Spotify fallback and for the game's records.
  - The SavedVariables pre-search (`prewarm_songs`) is skipped when Spotify is not in the order.
- `noSource.reasons` is a list of "provider: why" strings. A drop with no route at all has none; an example is "youtube" for a search-only drop.
- **Late start (lead decision D3, finding F2).** The HELLO must carry DROP_ACTIVE and a placeKey, and arrive while nothing plays. Its place must have a cached ARM (as built after review RP-2: the ARM is judged by sequence, not by its age -- the Director fires a place when the player may, minutes after arming it during the 10-minute cooldown, in combat or on a taxi; Razor Hill armed at 185 s fired at 605 s, which the first rule of "within 30 s" never late-started -- so any ARM still cached, up to `ARM_TTL_SEC`). The HELLO's seq must come after the ARM's ((hello − arm) mod 256 in 1..127), and no CANCEL or FIRE of that place may have come at or after the ARM (review RP-4: a FIRE seen there means the drop already played; and an ARM copy older than a FIRE seen for its place -- the chat log's line, delivered in one batch with the FIRE -- never re-arms the place). Then the engine starts the ARM's song as the lost FIRE would have:
  - it runs `_on_fire2` with the HELLO's seq and keeps the ARM's LOCAL_PLAYBACK and TEST flags;
  - the real FIRE with that seq (the chat log's copy), arriving within 30 s, counts as a duplicate.
  - Measured with the real Beacon.lua in the adapted timeline_sim sweep (320 FIREs covered by 1–6 s toasts): all 53 lost FIREs started late, and every drop played exactly once.
- **Duplicates that add fields (lead decision D4).** The other transport's copy of an event may carry more:
  - A duplicate ARM fills the cached ARM's missing song fields, so the FIRE takes the query from it. It starts no extra pre-warm search.
  - A duplicate FIRE fills in the title card of the drop it started. When nothing could play the fitted frame yet (card only, within the fallback window), the chain runs again with the fuller song.
  - A song that already plays is never switched.

### 3.4 Actions: common rules

1. Actions are accepted only from CONFIG frames, never from baseline frames.
2. Freshness: the action needs `-5 ≤ time.time() − issuedAt ≤ 45`. Otherwise it is logged as "stale request ignored", with no notification and no result.
3. Identity `(code, issuedAt)`: a second delivery (the other transport, the re-show, a re-queue after preemption) is dropped silently.
4. An unknown code is logged at INFO and ignored.
5. Rate limits:
   - each action at most once per 10 s. The exception is connect-spotify while its sign-in is still waiting: the same authorize page is opened again (at most once per 10 s), and no new session starts;
   - the page-opening actions (connect-spotify and youtube-sign-in) at most 5 times in total per rolling 10 minutes;
   - each test at most once per 10 s.

   **As built (polish-fix, review RG-2):** a request from the game is measured by its own click, `issuedAt` (the game's `time()`, the same PC clock), against the clicks of the game's earlier requests of that action that ran (`MusicConfig._too_soon`): refused when one is less than 10 s from it, either way. The game's own limit (`MusicServices.SEND_GAP`, the same 10 s, §2.9) counts from the click too, so a request the game sends is never refused for it -- by the app's clock two clicks 10 s apart can arrive 2 s apart (the first read only at the beacon's re-show 8 s later, or queued behind another frame), which made a click as the button came back "Sent" in game and `busy` in the app. Clicks are kept only while a fresh request can be near them (none from before a PC clock set back). A Connect-page request is measured by the app's monotonic clock, against the last run of that action from either source.

   A refused request records the result `busy`. At most once a minute, it also shows "NeedleDropper ignored a repeated request from the game. Wait a moment and try again."
6. Every executed action ends in exactly one result: an entry in `music.actions` (§3.10) and one Windows notification. Connect shows one more, interim notification when the page opens.
7. Before a success notification, the app flushes `Data.lua` (`gamedata.flush`), so a **Refresh status** right after the notification already shows the new state.

Result codes (`result`, matching `^[a-z][a-z-]{0,23}$`):

| Action | Results |
|---|---|
| connect-spotify | opened (interim), connected, declined, timeout, error, port-in-use, no-client-id, busy |
| disconnect-spotify | disconnected, ignored, busy |
| test-spotify, test-youtube | playing, failed, unavailable, paused, busy |
| youtube-sign-in | opened, default-browser, busy |

**As built:**
- youtube-sign-in results are `opened`, `unavailable` and `busy`. The 1.2.1 box never plays in the default browser (D17), so there is no `default-browser` result (§3.8).
- The once-a-minute "ignored a repeated request" notification has its own kind, `music-busy` (`tray.NOTE_MUSIC_BUSY`, 60 s cooldown).
- A Connect that finds a waiting sign-in for the same ID opens its page again. It also gets that session's final result, even when the Connect page started the sign-in: the worker checks it every second.
- The Connect page's Test and Sign-in buttons (§3.12) run the same handlers and rate limits. Their results show only on the page: no notification, and nothing in `music.actions`.
- Every final result flushes `Data.lua` before its notification, failures included.

### 3.5 connect-spotify

1. **Pick the ID.** `cid` is the frame's CLIENT_ID, else the configured `spotify.clientId`. With neither, the result is `no-client-id`: "Paste your Spotify app's Client ID in the game first (Options > AddOns > NeedleDropper > Spotify account)."
2. **Check for a working sign-in:** `TokenManager(configured id, TokenStore(paths)).usable()`.
   - **`cid` is the configured ID, or there is no working sign-in:** call `Companion.spotify_connect(cid)`, exactly as the Connect page does. It saves the ID, turns Spotify on and starts `start_login`.
   - **`cid` differs and a working sign-in exists: deferred save.**
     - Cancel any waiting login.
     - Start `start_login({**cfg["spotify"], "clientId": cid}, paths, on_change=…)` without writing config.json, and make it `Companion._login`.
     - Suppress the expiry watcher (`_watch_spotify`) until this login is done: the new tokens carry `client_id = cid`, which the old configuration would read as a mismatch.
     - On success, `_after_login` first writes `spotify.clientId = cid` (which clears `_login` and `_account` by design), then sets `_account` from the session's profile, then rebuilds the providers.
     - On failure nothing was written, and the old connection keeps working.
   - **The callback port is busy** (`SpotifyAuthError`, error `port_in_use`): result `port-in-use`, "Another program uses port 8321, which Spotify's sign-in needs. Close it and try again (or change spotify.redirectPort and the Redirect URI in your Spotify app)."
3. **Open the page.** `session.authorize_url` opens in the default browser through `deck.url_opener` (`webbrowser.open`). The player asked for this, so taking focus is acceptable. Windows' foreground lock may still keep the browser behind a borderless game; the notification below covers that.
4. **Interim notification** (result `opened`): title "Spotify sign-in", text "Approve NeedleDropper on Spotify's page in your browser. Click here if it opened behind the game." A click opens the same URL again; the click gives the app the right to bring the browser forward.
5. **When the session ends** (`on_change`):

   | Session outcome | Result | Notification |
   |---|---|---|
   | connected, Premium | connected | "Spotify connected as %s (Premium). In WoW, press Refresh status in Music services to see it there." |
   | connected, plan known and not Premium | connected | "Connected as %s, but this account has Spotify %s: controlling playback needs Premium." |
   | connected, 403 allowlist | connected | `session.warning` (the User Management text) |
   | connected, the sign-in could not be saved | connected | `session.warning` (kept in memory) |
   | access_denied | declined | "You declined on Spotify's page, so NeedleDropper can't play Spotify songs." |
   | timeout | timeout | "Spotify sent no answer within 5 minutes. If its page said INVALID_CLIENT, copy the Client ID again (not the Client secret). If it said Invalid redirect URI, add exactly %s to your Spotify app." |
   | any other error | error | `session.message` |

6. **Report to the game.** `status.spotify` goes through `_push_game_status`; `music.spotify` gets `clientId` (the active ID), `redirectUri`, `account` and `product`; the result is recorded; `Data.lua` is flushed; then the notification shows.

**As built:**
- The entry point is `Companion.connect_spotify_from_game(cid, on_done)`, the same as `spotify_connect(cid, on_done=…, keep_working=True)`.
- The deferred session is `Companion._deferred_login`. `_watch_spotify` waits for it, and `spotify_disconnect` forgets it.
- A connected sign-in whose plan is unknown says "Spotify connected as %s. In WoW, press Refresh status in Music services to see it there.", without "(Premium)".

### 3.6 disconnect-spotify

- **The ID must match.** The frame must carry a CLIENT_ID equal (ignoring case) to the configured `spotify.clientId`. Otherwise the result is `ignored`, with the notification "NeedleDropper ignored a Disconnect for a different Spotify app (Client ID …%s)."
- **Otherwise it runs:**
  - `Companion.spotify_disconnect()` deletes the tokens and keeps the Client ID; this is harmless when not connected;
  - `music.spotify.account` and `product` are removed;
  - result `disconnected`; `Data.lua` is flushed;
  - notification: "Spotify disconnected. Your Client ID stays, so Connect signs you in again."
- *As built:* a Disconnect that names no Client ID is `ignored` too: "NeedleDropper ignored a Disconnect that named no Spotify Client ID."

### 3.7 test-spotify and test-youtube

**Refused:**
- while paused: result `paused`, "Needle drops are paused in the NeedleDropper app (tray menu): resume them to test.";
- while a real drop plays: result `busy`, "A needle drop is playing. Try the test when it ends.".

A running test of the other service is simply replaced.

**Constants** (music_config.py):

```python
TEST_SONG = {"artist": "Toto", "title": "Africa", "label": "NeedleDropper test", "startMs": 30000, "durationMs": 15000}
TEST_SPOTIFY_URI = "spotify:track:2374M0fQpWi3dLnB54qaLX"  # "Africa" by TOTO: open.spotify.com page title checked 2026-11-05
TEST_YOUTUBE_ID = "QAo_Ycocl1E"  # the Azeroth Mixtape's zone:1413 (The Barrens) song
```

**test-spotify:**
- It is `unavailable` when there is no Spotify provider or its `available()` is false. The text is `unavailable_reason()`, else "Spotify isn't connected: press Connect Spotify first."
- Otherwise: `engine.play_now(Drop(n=0, id="test-spotify", kind="test", spotify=TEST_SPOTIFY_URI, **TEST_SONG), test=True, only="spotify")`.
- It works whatever "Play music with" says, because a test is explicit. Like every TEST, it records nothing.

**test-youtube:**
- It is `unavailable` when YouTube is switched off on the Connect page.
- Otherwise:
  - `deck.ensure_player(force=True)` opens the box in the chosen corner, even with `deck.autoOpen = "never"`;
  - then `play_now(Drop(n=0, id="test-youtube", kind="test", youtube=TEST_YOUTUBE_ID, **TEST_SONG), test=True, only="youtube")`.

**As built (1.2.1, §3.13):**
- Service-test duration is 15 seconds from playback starting, not from the earlier FIRE. Each
  test uses only its named provider; failed Spotify results explicitly say YouTube was not tried.
- The tests play at the volume of a drop without its own volume (the test drops carry none, like every frame without VOLUME): 100 in the YouTube box, `spotify.volume` (default 100) in the app's Spotify player, the device's own volume in connect mode unless `spotify.volume` is set (pinned by `test_v121_polish.py`).
- test-spotify plays in the app's own player (the SDK device "NeedleDropper") in app mode, at the configured volume with the usual fade, so the player hears it from the PC with no Spotify app open. `SpotifyProvider.available()` / `unavailable_reason()` give the `unavailable` text: a sign-in from before 1.2.1 (`RELOGIN_MESSAGE`), no Chrome or no Widevine (`NO_PLAYER_MESSAGE`), a player page that never got ready (`NOT_READY_MESSAGE`, polish), no player window yet (`NO_WINDOW_MESSAGE`), Premium. A start that does not progress within 3 s raises the `spotifyStuck` notice (the "failed" row below).
- test-youtube: `deck.ensure_player(force=True)` can only reuse the hidden player made at the app's start while the game is in front: creating a window then would take the focus, so it returns False and the result is `unavailable` ("NeedleDropper's player isn't running yet: it starts by itself when World of Warcraft isn't the window in front"). The box shows at the corner only while the test plays and is never activated.

**The result** comes from the engine's notes for that drop id within 15 s:

| Note | Result | Text |
|---|---|---|
| `playing` | playing | "Spotify test: playing Toto – Africa for 15 seconds." / "YouTube test: the box in the %s corner is playing Toto – Africa for 15 seconds." |
| `noSource`, `notEmbeddable`, or a provider notice (e.g. Spotify stuck, RC#4) | failed | the first reason |
| nothing within 15 s | failed | "No answer from %s within 15 seconds; see the log (tray → Open log folder)." |

**As built:**
- A test is also `busy` while an in-game file plays, because that is a real drop.
- The failure texts are:
  - "Spotify test failed: …" or "YouTube test failed: …", followed by the first reason without its "provider:" prefix;
  - for a stuck Spotify, the text of `stuck_notice()`;
  - for a video the box refuses: "YouTube test failed: YouTube refused to play the test video here (error N).";
  - for a test stopped before it played: "The Spotify test was stopped before it played." (or YouTube).
- When no player page is connected, test-youtube waits up to 8 s for one in `ensure_player`. Only the music worker waits; no transport or engine thread does.

### 3.8 youtube-sign-in

**With Edge or Chrome.** When `deck.browser` is edge or chrome and `find_browser` finds it, the app launches through `deck.launcher`:

```text
[exe, "--user-data-dir=<app dir>\deck-browser", "--no-first-run", "--no-default-browser-check", "--new-window", YOUTUBE_SIGNIN_URL]
```

- **The URL** is a deck/server.py constant: `YOUTUBE_SIGNIN_URL = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"`. It has not been checked against Google yet: FIRST-TEST step 7 (§5.5) confirms that it lands on accounts.google.com and returns to YouTube signed in. Until then it counts as unverified.
- **No `--app` flag:** the window is a normal one with an address bar, so the player can see accounts.google.com.
- **The profile:**
  - When the player box is open, Chromium hands these arguments to the running process of that profile, which opens a normal window.
  - Otherwise a new process starts with the profile.
- **The result** is `opened`, and `music.youtube.signInOpened = now`. Notification: "Sign in to YouTube in the window that just opened. It belongs to NeedleDropper's player box, so your everyday browser isn't affected. Close it when you're done."

**As built (1.2.1, §3.13):** the player window runs in Chrome whenever Spotify plays in the app, even with `deck.browser = "edge"`, and a Chromium profile belongs to one browser. So the sign-in window must start the player's own browser (`Deck.player_browser()`: "chrome" or "edge", the one `deck-browser` was made with), not `deck.browser`.

**Without them** (the default browser, or no Edge or Chrome): no window opens. The result is `default-browser`: "Your YouTube box plays in your default browser, so sign in to YouTube there (youtube.com)."

**As built:**
- `YOUTUBE_SIGNIN_URL` is a constant in music_config.py, not deck/server.py, and it is still unverified.
- The executable comes from `deck_server.find_browser(Deck.player_browser())`. The arguments are exactly the ones above, with `Deck.profile_dir` as the profile, and they go through `deck.launcher`, the same clean environment and DLL search as the player.
- With no Edge or Chrome (`player_browser()` "none") the result is `unavailable`: "NeedleDropper has no Edge or Chrome for its YouTube box, so there is nothing to sign in to. Install Google Chrome."
- **The profile while no player page is connected.** The launched browser process owns `deck-browser` until the player closes the window. A player window made meanwhile would open inside that process: without its flags, and out of the window manager's reach. So `Companion._watch_player` makes no player while `MusicConfig.signin_running()` is true.
- `music.youtube.signInOpened` is written through `Companion.note_youtube_sign_in`.

**What sign-in does for the box.** The player box loads the IFrame API from www.youtube.com (`player.js` L284, no youtube-nocookie host). So the profile's YouTube session is the one the box uses.

That a signed-in Premium session removes ads in the embedded player is expected, not promised. FIRST-TEST checks it (§5.5), and the copy says "optional".

### 3.9 YouTube box placement

This section only fixes what the corner means; the window itself is shared with the RC#8 non-activating player work.

- **The area:** the WoW window's client area (the pixel transport's window, `wincapture.client_rect`), else the primary monitor's work area.
- **The box** (`deck.PLAYER_WINDOW_SIZE`, unchanged) sits in `deck.playerCorner`, 16 px (scaled to the monitor's DPI) from both edges.
- **The beacon stays clear.** The box never overlaps the beacon capture rectangle (`capture_size(scale)` at the beacon's corner: the top-left 512 × 48 at 100 %). When it would, it moves down to 16 px below that rectangle. That always happens for topleft, and for topright when the window is narrower than the box plus 512 px.
- **A manual position wins:** a non-empty `deck.playerPosition` (an advanced key) applies until the corner changes, which clears it (§3.2).
- **Moving an open box:** a corner change while the box is open moves it at once with `SetWindowPos(SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER)`. It is never activated.

**As built (1.2.1, after the review):** `PlayerWindow.target` places the box in the game window's client area (`Win32.client_rect`, else the monitor) with `corner_position(..., beacon=BEACON_RECT_PX)`: a box that would overlap the 512 × 48 physical-pixel capture rectangle at the client area's top-left moves 16 px (DPI-scaled) below it (the strip is drawn in physical pixels at any display scale, so the rectangle is not scaled; at 300 % the 48 px margin already clears it). A corner from the game reaches `deck.configure_player(corner=…)`, which also calls `PlayerWindow.reposition()`: a box on screen moves at once (`SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER`), a hidden one takes the corner the next time it shows. The beacon's corner is the app's `transport.pixel.corner` (which must match the addon's advanced `beaconCorner`): `Companion._configure_deck` hands it to `deck.configure_player(beacon_corner=…)`, at start and after every config change, and `corner_position(..., beacon_corner=)` puts the rectangle in that corner of the area. A box that would overlap it moves 16 px past it, away from the area's edge: below a beacon at the top, above one at the bottom (polish pass; it kept only the top-left corner clear). Tests: `test_v121_review_fixes.py::test_the_youtube_box_keeps_clear_of_the_beacon_and_moves_at_once_when_the_corner_changes`, `test_v121_polish.py::test_the_youtube_box_keeps_clear_of_the_beacon_at_its_configured_corner` (every corner).

### 3.10 `NeedleDropper_Companion`: the `music` block

The block is additive to schema 1: `schema = 1` stays, and `_TOP_FIELDS` gains `"music"`.

```lua
music = {
  spotify = { clientId = "0123456789abcdef0123456789abcdef", redirectUri = "http://127.0.0.1:8321/callback",
              account = "Ethan", product = "premium" },
  youtube = { enabled = true, player = "edge", signInOpened = 1762380000 },
  prefs   = { order = "spotify-youtube", corner = "topright", fade = true, resume = true, fromGame = true },
  actions = { { id = 1762380123, action = "connect-spotify", result = "connected",
                text = "Spotify connected as Ethan (Premium).", at = 1762380150 } },
},
```

The Python `_music` validator and the Lua checker in `Companion.lua` apply the same rules:

| Field | Rule |
|---|---|
| spotify.clientId | 32 lowercase hex. An ID of another format (the Connect page allows 8–64 letters and digits) is left out; the game then disables Disconnect with "Disconnect it on the app's Connect page." |
| spotify.redirectUri | `^http://127\.0\.0\.1:[0-9]{1,5}/callback$` |
| spotify.account | text, at most 64 characters, control characters removed; absent when unknown |
| spotify.product | `^[a-z]{1,16}$` (premium, free, open) |
| spotify.state (1.2.1, D17) | one of connected, not-connected, needs-relogin, premium-required, no-player, stuck, error: `SpotifyProvider.status_dict()["state"]` (§3.13). `status.spotify` keeps its four v1.2 values; needs-relogin, no-player and stuck read "error" there (`providers.spotify.legacy_status`) |
| spotify.mode (1.2.1, D17) | app or connect: `spotify.device` |
| spotify.problem (1.2.1, review ON-3; polish, review ON-9) | optional. With state `error`: `allowlist`, Spotify refused the account for the player's developer app (403 "User not registered in the Developer Dashboard"), from the sign-in's profile read or a refused play. The game then says to add the account under User Management (and offers no "Sign in again"); the Connect page shows "Account not allowed". With state `no-player`, why in-app Spotify has no player (`SpotifyProvider.sdk_problem_detail`, `status_dict()["problem"]`): `no-chrome` (no Google Chrome, the page in Edge, no Widevine yet, `initialization_error`), `not-ready` (the page in Chrome never got ready, or the SDK's `connect()` said no: `NOT_READY_MESSAGE`), `no-window` (no player page: none is made while the game is in front, `NO_WINDOW_MESSAGE`), `window-never` (`deck.autoOpen` "never", `NO_WINDOW_NEVER_MESSAGE`), `sdk-load` (the SDK's script did not load, `SDK_LOAD_MESSAGE`). The game words each (§1.1); without a cause, or with an unknown one (which the checker leaves out, never fails on), it says neutrally that the player isn't ready. The app writes the field with every music block, `None` (removed from the file) without a cause: `set_music` leaves a field the call leaves out unchanged, so a cause once written outlived its state, also across restarts ("add the account under User Management" after the account was added; polish-fix, review RG-1). `gamedata.MUSIC_SPOTIFY_PROBLEMS` = `providers.spotify.MUSIC_PROBLEMS` = `Companion.SPOTIFY_PROBLEMS` (tested) |
| youtube.enabled | boolean: the Connect page switch |
| youtube.player | chrome, edge or none (D17): `Deck.player_browser()`, the connected player page's browser, else the one `open_player` would start. The default browser never plays (RC#8), so "default" is not reported |
| youtube.signInOpened | Unix seconds, ≥ 0 and ≤ 4102444800 (2100-01-01; polish, review INGAME-7: `date()` gives nothing past the client's calendar, year 3000 on Windows, and the status lines raised). The same cap holds for the top-level `written` (a later one reads as 0, "hasn't reported in yet") and an action's `at`: `gamedata.MAX_TIME`, `Companion.MAX_TIME`. Every `date()` caller in MusicServices.lua and SelfTest.lua shows "?" for a time it can't show |
| prefs | the effective values: order and corner names, fade and resume booleans, fromGame boolean |
| actions | at most 8, newest last. `id` uint32; `action` one of the five names; `result` per §3.4; `text` at most 300 characters; `at` ≥ 0. A new entry with the same (id, action) replaces the old one, so the interim `opened` becomes the final result. |

**Writer API:**
- `CompanionDataWriter.set_music(spotify=None, youtube=None, prefs=None)` merges per sub-table; a field set to None is removed.
- `CompanionDataWriter.record_music_action(entry)`.
- Module functions `gamedata.set_music(addons_dir, **parts)` and `gamedata.record_music_action(addons_dir, entry)`.
- `_lenient_current` keeps a valid `music` block, field by field.

**When it is written:** the app writes the block at start and whenever any part of it changes. `_push_game_status` gets a `_push_game_music` twin with the same compare-then-write rule.

**Downgrade.** `read_companion_data` parses without validating fields. A 1.2.0 app's `_lenient_current` keeps `status`, `tracks` and `imports` and silently drops `music` at its next write.

**As built:**
- **When it is written.** `Companion._push_game_music()` runs:
  - in the housekeeping loop, with `_push_game_status` through `push_game_data()`, which reads Spotify's status once for both;
  - after every PREFS write;
  - with every final result, as `push_game_data(flush=True)`.
- **Values.**
  - `music.spotify.state` falls back to the 1.2.0 badge mapping when a provider reports no D17 state.
  - `youtube.player` is looked up at most once a minute while no player page is connected.
- **`music.actions`** holds only the game's own requests, not the Connect page's.
- **Validation.**
  - `account` loses control characters and is cut to 64 characters; an empty one is left out.
  - An action's `text` is cut to 300 characters.
  - `gamedata.MUSIC_SPOTIFY_STATES`, `MUSIC_SPOTIFY_MODES`, `YOUTUBE_PLAYERS`, `MUSIC_ORDERS`, `MUSIC_CORNERS` and `MUSIC_ACTIONS` equal the app modules' tuples (tested).

### 3.11 Notifications

- New kind `tray.NOTE_MUSIC = "music"`, keyed by `"<action>:<issuedAt>:<phase>"`, so each request shows each phase once.
- New kind `tray.NOTE_SPOTIFY_RELOGIN = "spotify-relogin"` (once per session, built with §3.13): a sign-in from before 1.2.1 lacks `streaming` / `user-read-email`. Title "Sign in to Spotify again", text `RELOGIN_MESSAGE` + "Click here to sign in again." (opens the Connect page). The in-app stuck notice (`spotify-stuck`) takes its title and text from `SpotifyProvider.stuck_notice()`: "Spotify didn't start" / "Spotify didn't start in NeedleDropper's player." in app mode.
- DESIGN-v1.2 §1.4 ("notifications only when they matter") gains one entry: the results of requests the player sent from the game.
- The texts are in §3.5–§3.8.
- *As built:* `NOTE_MUSIC` is once per session per key, so every phase of every request shows once. The refusal notice is `NOTE_MUSIC_BUSY` ("music-busy", at most once a minute, §3.4). Results of the Connect page's own requests are not notified.

### 3.12 Connect page

- **Spotify card:** the setup is unchanged; it stays the path that needs no keyboard in game. It shows "Play music with: %s (set in game)" read-only, and an ID set from the game appears in its box.
- **YouTube card:** shows "Player box: %s (set in game)". It also gains a **Sign in to YouTube** button (`POST /api/youtube/signin`) that runs the same handler as the game's request; C2 may defer this.
- **`/api/state`** gains `music`: the effective choices and the last 8 request results.

**As built:**
- **Spotify card.**
  - The choice line reads "Play music with: <order> (set in game: Options > AddOns > NeedleDropper)". Before the game has chosen, it ends "(the default: choose it in game, Options > AddOns > NeedleDropper)".
  - **Play on** lists "NeedleDropper (this app)" first and marks it recommended. That is `spotify.device = "app"`, the default.
  - Next come "The Spotify app on this PC (automatic)" and the account's other Connect devices: `spotify.device = "connect"`, with `deviceName` for a named device. The SDK's own device, "NeedleDropper", is not listed.
  - `spotify.device` is now a key the Connect page may change (`panel_api.SCHEMA`). Switching it away from "app" while an in-app song plays never cuts the song: while the engine plays a drop, `rebuild_providers` closes none of the old providers; it retires them all and closes them once the engine is idle, so the drop's own stop (the fade-out, a failed provider's clean-up) is never cut (`Companion._close_retired`, run by the housekeeping loop, which the engine's notes wake). Its player then disconnects from Spotify (the close of whichever old provider owns it), the token endpoint refuses and the player window no longer needs Chrome; switching back connects the new provider's player again, and a provider of the chain that owns the player again is not disconnected by the old ones' close (polish, review RCOMP-10). **polish-fix (reviews RAPP-1, RAPP-2):** the rebuild asks whether the engine is idle after the swap, not before (`Engine.set_providers` waits for a drop that is starting; the engine names its provider only once `play()` returned), and every old provider waits, not only the one playing: after away, back and away again during one song, the one built on the way back owns the player that song plays in. A retired `SpotifyProvider` (`retire()`) never connects, disconnects or reconnects the in-app player again, while the song it plays still plays and stops there; `close()` is terminal (`_sdk()` is None after it, `play()` refuses): an ARM pre-warm queued before the switch ran `sync_sdk()` on the old provider and connected the player under it again (or took it from the chain's new in-app provider, whose player the old one's close then disconnected).
  - The connected view has **Test Spotify**.
  - For a sign-in from before 1.2.1 (`spotify.relogin`, D9), the sign-in button reads "Sign in again".
- **YouTube card.** "Player box: <corner> (set in game …)", **Test YouTube** and **Sign in to YouTube**, with one result line.
- **Endpoints.** `POST /api/music/test {"service": "spotify" | "youtube"}` and `POST /api/youtube/signin` answer 202 with the request's `id`. The result appears in `state.music.actions`, marked `source: "page"`.
- **`/api/state`.**
  - `music` holds `order`, `corner`, `fade`, `resume`, `fromGame`, `actions` (the last 8 results from the game and the page, oldest first) and `pendingTests`.
  - `spotify` gains `device`, `relogin` and `musicState`; `youtube` gains `player` and `signInOpened`.
- **`GET /api/spotify/devices`** needs only a sign-in in app mode: the SDK need not be ready to list the other devices. It also returns `device`.

### 3.13 In-app Spotify and the player window (as built, lead decisions D6–D14, D17)

This section was written with the build; where §1–§3.12 assumed a Spotify Connect device (the desktop app), this wins.

**Modes** (`spotify.device`, config.py; normalised to these two):

| Mode | What plays Spotify songs |
|---|---|
| `app` (default) | The Spotify Web Playback SDK in the player page, the Connect device "NeedleDropper". Needs Google Chrome (Widevine) and a sign-in with the 1.2.1 scopes. |
| `connect` | Another Connect device (`deviceName`, else a Computer, the active one, the first): the 1.2.0 flow, including the desktop app's stuck signature (`looks_stuck`), transfer and resend. Automatic selection excludes devices named NeedleDropper; an explicitly configured name can select one. |

**Scopes (D9):** `streaming user-modify-playback-state user-read-playback-state user-read-email user-read-private`. A stored sign-in that records fewer (no `streaming` or no `user-read-email`) needs a new one: Spotify is unavailable (drops play YouTube), the Connect page shows "Needs attention" with `RELOGIN_MESSAGE` and the Connect button (Client ID kept), one `spotify-relogin` notification, and `music.spotify.state = needs-relogin`. A sign-in whose scopes the token file does not record is trusted until the SDK reports `authentication_error` "Invalid token scopes".

**The player page** (`/player`, opened as `http://localhost:<port>/player`, D12) hosts the YouTube IFrame player (cue at ARM unchanged) and the SDK:
- The SDK loads only when the app says `{"cmd":"spotify","op":"connect"}`: a provider in app mode with a usable sign-in (`SpotifySdk.enable`). Without one the provider puts it on standby (`SpotifySdk.standby`): no SDK, but the player still opens in Chrome, so a later sign-in needs no other browser. Outside Chrome the page never loads it (error kind `browser`).
- Widevine is checked first (`requestMediaKeySystemAccess('com.widevine.alpha')`), retried every 30 s up to 10 times. The deck copies `%LOCALAPPDATA%\Google\Chrome\User Data\WidevineCdm` into `deck-browser\` when it is missing there (D11).
- `getOAuthToken` reads `GET /api/player/spotify-token` (D13): the access token only, for a `fetch` marked `Sec-Fetch-Site: same-origin` with the right `X-ND-Token`, Host and Origin; `Cache-Control: no-store`. 404 while in-app Spotify is off, 503 when there is no usable sign-in.
- The page reports `POST /status {"state":"spotify","event":...}`: `ready` / `not_ready` (device id; it can change), `error` (`initialization_error`, `authentication_error`, `account_error`, `playback_error`, `load`, `autoplay`, `browser`), `eme`, `connect`, `token`, `state` / `tick` (position, paused, loading, track, volume), `stopped`. `SpotifySdk` keeps the device id and the last 128 reports. A new page clears readiness and the previous device id. A `ready` report alone does not make the id usable: the provider verifies it against `GET /me/player/devices` before `wait_ready` can return it.
- **One Spotify.Player per page (beta.1):** a reconnect or a later connect reloads the page instead of constructing another Player in it. Disconnect reloads into a page without the SDK. The launch-key fragment survives, and the deck replays its current connection state. Reloads are limited to one a minute and wait while a YouTube or local song plays in the page. Authentication changes also request a reload, deferred while one of our songs plays.
- Commands: `connect`, `disconnect`, `reconnect`, `start` (playId, uri, volume 0–100, fadeInMs), `fade`, `stop` (playId, fadeOutMs, delayMs), `pause`.
- CSP additions: `script-src https://sdk.scdn.co`, `frame-src https://sdk.scdn.co`, `connect-src https://*.spotify.com wss://*.spotify.com https://*.scdn.co https://*.spotifycdn.com`, `media-src 'self' blob: https://*.scdn.co https://*.spotifycdn.com`. Violations are reported (`{"state":"csp"}`) and logged once each; none were seen live.

**One drop in app mode (D7, D8):**
1. ARM: resolve the track (pinned id or search), warm the token (refreshed within 60 s of expiry), verify the SDK device against `GET /me/player/devices`, and read `GET /me/player` once for `restorePrevious`. **Never a play**: one sent to the SDK device would take playback over before FIRE. Missing devices request a page reload. Background verification after SDK readiness is limited to at most once per 30 s, and reloads to once per minute; an unavailable provider can recover through the background check even when the engine skips its ARM.
2. FIRE: `start` to the page (SDK volume 0), then `PUT /me/player/play?device_id=<sdk>` `{uris:[uri], position_ms}`. Started = the position advances within 3 s (`APP_START_TIMEOUT_S`): a page report at least 150 ms past the start, or 100 ms further than the report before, while not paused or loading; `GET /me/player` on device NeedleDropper is read at 1.5 s and 3 s when the page shows nothing. The page then fades in with `setVolume` to `spotify.volume` (default 100) × the drop's volume. On 404, log Spotify's error with the target id's first eight characters, request a page reload, wait for a fresh id verified in Spotify's device list, and retry the PUT once. Do not pause a player with nothing loaded. If recovery fails, raise for the engine's normal fallback.
3. No progress, or an SDK `playback_error` / `account_error` / `authentication_error` / `initialization_error` / `not_ready` meanwhile: the page pauses at volume 0, Spotify is marked stuck (`SpotifyStuckError`, notice `spotifyStuck`), the engine plays YouTube through the existing chain, and the parked handle pauses a late start at the end of the drop. A later `ready` alone cannot clear the verdict; recovery requires a device verified in Spotify's list. Service tests remain restricted to their named provider, so a failed Test Spotify never falls back to YouTube.
4. While it plays, `poll` reads the page's ticks (every 2 s; 250 ms for the first 6 s), not the Web API. Buffering (`loading`) is not a pause; `paused` is someone else's control. A `playback_error`, or the device offline for 3 s with no `ready` of the same device since (`APP_OFFLINE_GRACE_S`), is an error: the engine stops the song and plays the next provider within its fallback window.
5. Stop: `stop` to the page: fade out with `setVolume`, then `pause`, then `stopped`; without that confirmation, `PUT /me/player/pause`. With `restorePrevious`, the player's previous device and context resume there (never inside the SDK device, and not when the music before was already in it).
- The desktop "every action disallowed" signature never applies to the SDK device (D8); `check_health` in app mode only reports ready and re-checks a stuck player.

**Status values (D17):** `SpotifyProvider.status_dict()` gains `mode`, `state`, `warning`, `relogin` and `player` (ready, browser, Widevine, last error kind, last start latency; no token, no device id).

| `state` | When | `status.spotify` (v1.2 readers) |
|---|---|---|
| connected | signed in; in app mode the SDK is ready or still connecting | connected |
| not-connected | no Client ID or no sign-in | not-connected |
| needs-relogin | a sign-in without the 1.2.1 scopes, or a revoked one (`invalid_grant`, another Client ID) | error |
| premium-required | Spotify refused playback (403 Premium, or the SDK's `account_error`) | premium-required |
| no-player | no Chrome, no Widevine, `initialization_error`, the SDK script did not load, the page runs in Edge, no `ready` within 30 s of the page connecting, or no player page at all 30 s after start (`NO_WINDOW_MESSAGE`: none is made while the game is in front). The cause travels as `problem` (§3.10; polish, review ON-9): a page in Chrome that never got ready now says `NOT_READY_MESSAGE`, not "install Google Chrome" | error |
| stuck | a start did not progress this session | error |
| error | anything else, e.g. `authentication_error` again after a fresh token | error |

**The player window (D14):** made once at the app's start when YouTube or app-mode Spotify needs it (`Companion._player_needed`), never while World of Warcraft is the foreground window; the housekeeping loop tries again every few minutes while there is none. Chrome first whenever a Spotify provider is in app mode; Edge stays the fallback for YouTube only (it is skipped where an IFEO debugger redirects msedge.exe). As soon as it appears it is restyled `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW` minus `WS_EX_APPWINDOW` and **parked** (D14's fallback, as built after the review): shown, but beyond the virtual screen's top-left corner (`GetSystemMetrics` 76-79, `PlayerWindow.park`), `HWND_NOTOPMOST`, never activated, so no monitor shows it and it has no taskbar or Alt-Tab entry. **Measured on the owner's PC with the frozen 1.2.1 app (2026-11-06):** hidden with `SWP_HIDEWINDOW` from 218 ms after the launch, the SDK got ready and Spotify accepted the song on device NeedleDropper, but its position never moved (26 s at the start position); with the window shown, it plays. The first "hidden works" measurement (from source) had adopted another app's visible player window (the owner's 1.2.0 one) 14 ms after the launch, so its own window had stayed visible; windows that exist before the launch are no longer candidates (`PlayerWindow.windows_now`, `adopt(ignore=...)`). The box is shown topmost at the corner (`SWP_NOACTIVATE | SWP_SHOWWINDOW`) only while a YouTube song plays, and parked again afterwards. Nothing YouTube may play while it is parked (research §5.4: a shown page reports itself visible): the ARM only cues the video, paused and silent, and no warm-up play may be added. A display change while it is parked (a monitor added above or to the left, a resolution or scaling change) moves the virtual screen, and Windows may bring the window onto a monitor: the housekeeping loop calls `Deck.keep_player_parked()` → `PlayerWindow.keep_parked()` every few seconds, which parks it again (never activated) when the parking spot moved or the window overlaps the virtual screen, and leaves a box a YouTube song shows alone (polish pass). Placing the window is serialised (`PlayerWindow._place_lock`), so a park decided a moment before never lands on a box a song just showed. Chromium's one creation activation is handed back to the window that was in front (`SetForegroundWindow`, best effort), and `PlayerWindow.took_foreground` / `handed_back` record it (`Deck.player_info()`).

**Browser profile ownership (beta.1):** before launching, inspect the dedicated profile's lock
and browser process arguments. Retiring a previous NeedleDropper player requires a matching
executable, exact profile argument, process creation time and a loopback `/player` app URL.
Close its windows, wait up to three seconds and then end only the verified tree. A foreign or
unverifiable holder uses a fresh per-start profile with Widevine copied instead. A launcher that
exits successfully after handing off logs that fact; validated adoption retains ownership of
the actual browser PID for shutdown. All launches still respect the foreground-game guard.

**Recovery diagnostics (beta.1):** log the first eight characters of current and previous device
ids on ready, not-ready and reload, and the target prefix on play commands. Failed commands log
Spotify's error text; sign-in logs granted scopes and sign-out logs one event. Tokens are never
logged. Device-list verification and real-client reconnect tests are separate from the older
clean-start measurements below.

**Measured live before the beta.1 reconnect fixes** (2026-11-06, owner's PC, Windows 11, Chrome stable, 3840 × 2160 at 300 %, from source in a sandbox, Spotify at SDK volume 0, YouTube at player volume 0):

| Measure | Result |
|---|---|
| Launch → player page connected → Widevine usable → SDK `ready` | 1.2–1.7 s → 1.8–2.3 s → 2.2–3.4 s (11 s in the one run whose sign-in had to be refreshed first) |
| Window while Spotify plays | hidden (`IsWindowVisible` false); extended style 0x08000180 |
| Spotify FIRE (`play()`) → progress detected | 901 ms with the final start rule (917–963 ms in two earlier runs with a looser one); the play request itself takes about 200 ms |
| Server progress after the start | 31 344 → 34 960 ms over 3.6 s on device "NeedleDropper"; page volume 0 throughout |
| Stop with a 1.5 s fade | 1.82–1.83 s to the page's `stopped`; then `is_playing` false |
| YouTube FIRE → playing (cued at ARM), from localhost | 643–817 ms over three runs for `d_vPJjqWxZM`, `r2rAYwhM0Kc`, `QAo_Ycocl1E` (all three failed with error 150 from 127.0.0.1) |
| YouTube box | hidden at ARM, shown at (2532, 48)–(3792, 1188) (420 × 380 DIP, 16 DIP from the top-right edges), topmost, no-activate; hidden again after stop |
| Foreground (sampled every 25 ms) | at creation, a 51 ms and a 25 ms activation of the new window in two of three runs, each handed back (`took_foreground` true, `handed_back` true); none sampled in the third; no change during any Spotify or YouTube play, fade or stop |
| CSP violations | none |

**APIs for the music services (test-spotify, test-youtube, status):**
- `SpotifyProvider.available()`, `unavailable_reason()`, `status_dict()` (`state`, `mode`, `relogin`, `warning`), `music_state()`, `stuck_notice()`, `mode`; `providers.spotify.legacy_status(state)`, `MUSIC_STATES`, `RELOGIN_MESSAGE`, `NO_PLAYER_MESSAGE`, `NO_WINDOW_MESSAGE`; polish pass: `NOT_READY_MESSAGE`, `NO_PLAYER_PROBLEMS`, `MUSIC_PROBLEMS`, `SpotifyProvider.sdk_problem_detail()` (`(state, message, problem)`) and `status_dict()["problem"]`.
- `Deck.player_browser()` ("chrome" / "edge" / "none"), `Deck.player_info()`, `Deck.ensure_player(force=True)`, `Deck.page_open`, `Deck.spotify` (`SpotifySdk.snapshot()`).

---

## 4. Live feedback and copy

### 4.1 Limits

- During a session the game learns nothing from the app, so "Sent" never means "done".
- The game can't tell whether the app is running right now. "Report from %s" is the best it has.
- Feedback happens where the work happens: Spotify's page, the YouTube box, and Windows notifications (DESIGN-v1.2 §1.4).
- The game reads `Data.lua` at login and on `/reload`. The app flushes it before every final notification, so a reload right after the notification shows the result.

### 4.2 Copy

`ns.L` keys are the English text. Send confirmations appear in the dialog section's feedback line and as one chat line (`ns.Print`).

**After a send:**

| When | Text |
|---|---|
| connect sent | Sent to the NeedleDropper app (%H:%M). Approve NeedleDropper on Spotify's page in your browser (Alt+Tab if it opened behind the game). A Windows notification confirms it; then press Refresh status to see it here. |
| disconnect sent | Sent (%H:%M). The NeedleDropper app forgets your Spotify sign-in and confirms with a Windows notification. |
| test-spotify sent | Sent (%H:%M). You should hear 15 seconds of Toto – Africa from Spotify in a few seconds. If not, a Windows notification says why. |
| test-youtube sent | Sent (%H:%M). The YouTube box should open in the %s corner and play 15 seconds of Toto – Africa. If not, a Windows notification says why. |
| YouTube sign-in sent | Sent (%H:%M). The NeedleDropper app opens YouTube's sign-in page in its player browser (Alt+Tab if it stays behind the game). |
| any send (second line) | Nothing after 10 seconds? Check that the NeedleDropper app is running (its tray icon), then press Send a test signal. |

**After a change of choices:**

| App | Text |
|---|---|
| 1.2.1 or newer | The NeedleDropper app follows this within a second while the game runs. |
| older | Update the NeedleDropper app to 1.2.1 to follow this choice. Until then it plays Spotify first, then YouTube; "only" and "Neither" already work. |
| no app data | Saved. The NeedleDropper app picks it up when it sees the game. |

**As built:** the "older" row reads "Saved. Update the NeedleDropper app to 1.2.1: the version you have can't read this addon's signals." (a 1.2.0 app reads no 1.2.1 frame, §2.11). "test-spotify sent" reads, in app mode (D7), "Sent (%H:%M). In a few seconds you should hear 15 seconds of Toto – Africa from the NeedleDropper app's own Spotify player. If not, a Windows notification says why."; the text below stays for connect mode.

**When the game refuses to send:**

| Reason | Text |
|---|---|
| app older than 1.2.1 | Not sent: update the NeedleDropper app to 1.2.1 first. |
| no app data | Not sent: install the NeedleDropper app first (see the addon page). |
| the same request less than 5 s ago (as built: 10 s, the app's own limit; review INGAME-8) | Just sent: give it a moment. |
| connect without a Client ID | Paste your Client ID first (step 3). |

### 4.3 After the reload: the "Last request" line

The line describes the newest entry of `db.music.requests`:

| Condition | Text |
|---|---|
| the app's `music.actions` holds this (at, action) | %action (%H:%M): %text (the app's own text, escaped) |
| it doesn't, and the report was written at least 30 s after the request | %action (%H:%M): the NeedleDropper app didn't receive this request. Check the link with Send a test signal, then try again. |
| it doesn't, and the report is older than the request | %action (%H:%M): no report from the NeedleDropper app since. Press Refresh status after it confirms in Windows. |

The action labels are: Connect Spotify, Disconnect Spotify, Test Spotify, Test YouTube, Sign in to YouTube.

---

## 5. Tests and migration

### 5.1 Existing tests that change

| Test | Change |
|---|---|
| `test_protocol2_contract.py::test_decoders_reject`, case "type-6" | becomes "type-7" |
| `::test_unknown_tags_are_skipped_forward_compatible` | Its unknown tag 0x0B (3 bytes) would now be a PREFS of the wrong length, which is rejected. Move it to 0x0E. The "known tag as extra" check then covers 0x0B–0x0D. |
| `::test_measured_size_table` | add HELLO + PREFS (13 B, 144 × 3) and the CONFIG rows of §2.6 |
| `::test_all_event_types_round_trip_through_every_layer` | includes CONFIG |
| `::test_constants_match` | `EV.CONFIG`, the new tags, and the order, corner and action codes |
| `test_v12_beacon_timing.py`, `test_v12_beacon_pipeline.py` | FIFO A behaviour is unchanged; add the queue-B cases of §5.2 |
| `test_v12_ui_static.py`, `test_integration_static.py` | the two new files in the TOC; the template allowlist is unchanged |
| `tests/lua/wow_shim.lua` | `CreateSettingsButtonInitializer` returns an object with `AddSearchTags` and `AddModifyPredicate`; a new `SHIM.settings.Render()` evaluates function-valued button texts and tooltips |

### 5.2 Lua tests (lupa)

1. **Protocol contract** (`test_protocol2_contract.py`):
   - byte-exact Lua ≡ Python for the §2.13 vectors, and for a random corpus of prefs × actions × client ids × seq and flags;
   - decode parity between the languages;
   - rejected: PREFS of 3 or 5 bytes, ACTION of 5 or 7 bytes, a check byte off by one, CLIENT_ID of 15 or 17 bytes, type 7;
   - accepted: unknown order 9 (order nil), unknown action 77 (name nil);
   - both encoders drop PREFS from a FIRE;
   - `encode_frame` strictness and the `frame_from_event` keys.
2. **Compatibility:** *void as built* (§2.11): with the 4-byte trailer a 1.2.0 decoder reads no 1.2.1 frame, so there is no `tests/compat/protocol2_v1_2_0.py`. The v1 decoders' type guard is tested instead (`test_protocol_contract.py::test_v1_decoders_reject_types_above_5`).
3. **One row:** for every frame MusicServices can build (5 actions × with or without client id × prefs set or not × flags):
   - `#EncodeFrame` ≤ 39;
   - `AdaptiveLayout` gives one row;
   - at 2, 3 and 4 px the strip fits 512 × 48.
4. **Beacon queue B** (beacon harness, timeline style):
   - holds of 2 and 3 s;
   - a FIRE during a CONFIG is drawn in the same `Pump`, and the CONFIG returns after the FIRE's 3 s with identical bytes;
   - no CONFIG during an ARM's song window;
   - the re-show at +8 s and the overflow order;
   - the steady HELLO carries PREFS once set and changes at once after `SetPrefs`;
   - ForceShow with the beacon off;
   - the strips decode through `beacon2.locate_and_decode` at 100, 125 and 150 %.
5. **ChatLink:** an action CONFIG is padded and a PREFS-only one is not; the 20 s drop in lockdown; HELLO lines carry PREFS.
6. **MusicServices model:**
   - `prefsSet` transitions: setting edits and a settings reset set it; the echo copy doesn't;
   - `WirePrefs`;
   - the stripping matrix of §1.5 against app versions none, 1.1.0, 1.2.0, 1.2.1, 1.10.0 and "dev";
   - `PreferredProvider` and the `canPlay`/duck matrix;
   - a Client ID corpus: spaces, line breaks, upper case, 31 and 33 characters, non-hex; a 32-hex secret-like value is accepted;
   - the `Send` refusals;
   - request bookkeeping capped at 8 and matched to fake `music.actions` after a simulated reload, covering all three §4.3 cases.
7. **Options** (the wow_shim Settings API):
   - the rows' order and labels of §1.2, and the dynamic texts for each status case;
   - search tags and modify predicates;
   - clicks emit the right CONFIG, decoded in the test;
   - the old-API path registers just the four settings, without errors.
8. **Dialog** (shim_ui):
   - the template allowlist; every EditBox gets `SetAutoFocus(false)`; `UISpecialFrames`; the CloseButton's OnClick replaced;
   - read-only boxes restore their text after an edit;
   - the Client ID messages, button states and disabled reasons;
   - the popups use `dialog:GetButton1()`;
   - Refresh calls `ReloadUI` once;
   - hiding and returning around combat, and gamepad registration;
   - app text is escaped: an account named `|cffff0000x|r` shows literally.
9. **Static checks:** no `CopyToClipboard`; no URL string is handed to any API; all new strings go through `ns.L`.

**As built**, the addon-side items are in:

- `tests/test_v12_model_music.py` (items 3 and 6, plus the status lines for every D17 state, the Companion `music` checker, `/needle music` and the D15 version check);
- `tests/test_v12_beacon_music.py` (items 4 and 5, plus D3: the HELLO drawn while a drop plays carries its placeKey);
- `tests/test_addon_music_options.py` (item 7);
- `tests/test_v12_ui_music.py` (item 8 and `/needle report`);
- the existing static tests cover item 9 for the two new files (`test_integration_static.py`, `test_v12_ui_static.py`).

The shims gained: `CreateSettingsButtonInitializer` returns an initializer with `AddSearchTags`, `AddModifyPredicate` and `EvaluateModifyPredicates`, setting controls take search tags, `SHIM.settings.Render()` evaluates function texts and tooltips and the predicates (`tests/lua/wow_shim.lua`); the UI fakes' Companion has `Music`, `AtLeast` and `Supports` (`tests/lua/shim_ui.lua`).

### 5.3 Python tests

1. **`protocol2`:** the new `Frame` fields, `build_frame`, `frame_from_event`, `encode_frame`, `decode_frame` and `describe()`. The v1 decoders reject type 6, both as beacon bytes and as an NDROP1 line with a valid checksum.
2. **`music_config`** (fake app, fake clock, fake notifier, launcher and opener):
   - **PREFS:**
     - the mapping table;
     - 1,000 identical deliveries make one write;
     - fields that are not set, or unknown, are ignored;
     - baseline PREFS apply;
     - the Connect page switches are untouched;
     - `playerPosition` is cleared only by a corner change.
   - **Actions:**
     - the freshness edges (−6, −5, 45 and 46 s);
     - identity across pixel, chat and re-show;
     - the rate limits;
     - baseline actions and unknown codes are ignored;
     - behaviour while paused.
   - **connect:**
     - the same ID;
     - a new ID without a working sign-in (saved at once);
     - a new ID with a working sign-in: not saved until success; after a failure the old tokens still work; the expiry watcher stays quiet;
     - the port in use, and no ID at all;
     - every session outcome gives the right result and notification text;
     - the flush happens before the final notification.
   - **disconnect:** a matching ID, a different ID, and no ID.
   - **Tests:**
     - the refusals;
     - the `play_now` arguments (`only=`);
     - results from fake engine notes;
     - the 15 s timeout.
   - **Sign-in:**
     - the launcher arguments exactly as in §3.8: no `--app`, the profile path, the constant URL;
     - the default-browser path.
   - **Fuzz:** 10,000 random CONFIG frames, after which the opener has only ever received a Spotify authorize URL or `YOUTUBE_SIGNIN_URL`.
3. **Engine:** the route order for each mode with fake providers; "none" never reaches `_start_drop` and shows no card; the pre-warm skips; `noSource.reasons`.
4. **`gamedata`:** `music` validation, rendering, round trip and `_lenient_current`. Cross-language: the rendered file is read by the real `Companion.lua` under lupa.
5. **Pixel transport:** `on_baseline` runs once per baseline, not for `same` reads, and again after the window is found again.
6. **Config:** the defaults and `normalize_config`. While `fromGame` is false, the 1.2.0 fades are kept.

**As built** (the files):
- `tests/test_v12_shell_music.py`: item 2 against a fake companion.
  - The "default-browser" path is the `unavailable` one (§3.8).
  - It also covers a Connect that re-opens the Connect page's sign-in, and a test refused during an in-game file.
- `tests/test_panel_music.py`: the real Companion on a real deck. It covers:
  - the wiring, the baseline hook and the pause gate;
  - the order and fades into the engine, and the Spotify provider's fade;
  - the endpoints;
  - item 2's deferred connect against a real PKCE login with scripted Spotify HTTP;
  - item 5 and item 6.
- `tests/test_v12_engine_music.py`: item 3, plus:
  - D3: frames, and FIREs covered on screen through the real Beacon.lua;
  - D4.
- `tests/test_core_music_block.py`: item 4.
- `tests/test_core_version.py`: D15.

### 5.4 End to end (in the style of `test_integration_e2e.py`)

1. The addon harness loads the integrated TOC and the real `Companion.lua`, with a `Data.lua` written beforehand.
2. A click on Settings row 2, "Connect Spotify".
3. The Beacon textures are captured as BGRA and read by `beacon2.scan`.
4. `PixelTransport.feed` → `Companion.submit` → MusicConfig.
5. A fake `start_login`. The fake opener receives the authorize URL, whose `client_id=` is the typed ID.
6. A fake approval. `music.actions` and the status are written.
7. The harness reloads with the new `Data.lua`.
8. Settings row 1 reads "Spotify + YouTube ready", and the dialog's Spotify line reads "connected as Tester (Premium)".

Then the same path again through the chat-log transport, with the beacon off.

**As built** (`tests/test_integration_music_e2e.py`): the real addon (TOC order) loads a `Data.lua` that the real Companion wrote. The game sends a music choice and `MusicServices.Send("connect-spotify", {clientId})`, which is what row 2 calls; the Settings row itself is not clicked. The path:
1. The beacon textures are captured as BGRA and read by `PixelTransport` (the real one).
2. The real Companion (MusicConfig, the real engine, a real deck) takes them.
3. config.json gets the choice and the Client ID.
4. The opener receives the authorize URL (`client_id=` is the typed ID), and the approval goes through the real PKCE login against scripted Spotify HTTP.
5. A Test YouTube plays on the engine's (fake) YouTube provider.
6. `Data.lua` gets `music.actions` (connected, then playing), the account and the choices.
7. After a /reload, `MusicServices.StatusWord()` reads "Spotify + YouTube ready" and `Companion.Music()` has the results.

The chat-log variant sends Test YouTube with the beacon off.

### 5.5 In game (add to FIRST-TEST.md)

**As built:** FIRST-TEST.md has a "Music services (1.2.1)" section with these steps, adapted to in-app Spotify (step 4: Spotify plays from the app's own player, nothing shows; a 1.2.0 sign-in needs **Sign in again**) and `/needle report` (step 10). Step 10 below (the taint log) stays in the main list.

1. Options > AddOns > NeedleDropper shows the Music services section; searching "Spotify" and "YouTube" finds its rows.
2. Spotify account → the dialog:
   - click each read-only box, press Ctrl+C and paste into the browser: both addresses work;
   - paste the Client ID with Ctrl+V: "That looks like a Client ID".
3. Connect:
   - the browser opens Spotify's page (note whether it came forward over the borderless game);
   - approve; the notification arrives;
   - Refresh status shows "connected as … (Premium)".
4. Test Spotify and Test YouTube each play 15 s. During the YouTube test, `GetForegroundWindow` stays on WoW (RC#8).
5. Change the YouTube box position while the box is open: it moves without taking focus. Top left sits below the beacon.
6. Play music with:
   - "YouTube only": a drop with both links plays YouTube;
   - "Neither": only the card shows, and the zone music keeps playing.
7. Sign in to YouTube:
   - a normal window opens on accounts.google.com in the player profile;
   - after signing in with a Premium account, a YouTube drop that normally shows an ad plays without one. Record the result; the copy stays "optional" either way.
8. Disconnect, then Refresh status: "not connected". Connect again works without retyping the ID.
9. Gamepad: the dialog is reachable and closes with B; nothing traps the cursor.
10. A `/console taintLog 1` session shows no NeedleDropper lines.

### 5.6 Migration from v1.2

**Addon 1.2.0 → 1.2.1**

- No `ns.SCHEMA` bump. The four settings get their defaults through `mergeDefaults`, and `NeedleDropperDB.music` is created with `prefsSet = false`.
- Nothing is sent until the player chooses a music setting, so the idle strip stays 96 × 3 until then.
- At the first login with a 1.2.1 app, the four settings show the app's values (the echo). A player who customised config.json sees the truth and keeps it.
- A Client ID entered on the Connect page appears in the dialog (from `music.spotify.clientId`).
- `duckWithoutApp`, `localPlayback`, `beaconCorner` and the other settings are unchanged.

**App 1.2.0 → 1.2.1**

- config.json gains the `music` and `deck.playerCorner` defaults in memory. Nothing is written until PREFS arrive or the Connect page saves.
- While `music.fromGame` is false, the app behaves exactly as 1.2.0 does.
- The first PREFS write only the keys that differ (§3.2).
- `deck.playerPosition` stays in force until the player changes the corner in game.

**Downgrades**

- **Addon back to 1.2.0:** the four settings and `db.music` stay in SavedVariables untouched, because `validateSettings` only walks `ns.DEFAULTS`. The 1.2.0 Options panel doesn't show them.
- **App back to 1.2.0:**
  - `music` and `deck.playerCorner` stay in config.json ("keys this version does not know are kept");
  - `Data.lua` loses its `music` block at that app's next write (§3.10);
  - the 1.2.1 addon then says "Update the NeedleDropper app to 1.2.1".

---

## 6. Who builds what, and documents to update

| Owner (CONTRACTS-v1.2 §1) | Work |
|---|---|
| **A1 model** | new `MusicServices.lua` (TOC: right after `Companion.lua`); `Companion.lua`: `music` checker, `Music()`, `Supports()`, `AtLeast()`, `StatusLine` |
| **A2 window** | new `UI\MusicDialog.lua` (TOC: after `UI\ShareDialogs.lua`); the title-bar button in Window.lua; `ns.UI.OpenMusic()`; shim_ui support |
| **A3 protocol** | Protocol2.lua, protocol2.py, `models.EventType.CONFIG`, the v1 type guard in protocol.py; Beacon.lua (queue B, prefs); ChatLink.lua; Emit.lua; the PROTOCOL-v2 edits; the contract tests |
| **C1 engine** | engine.py (order, none, pre-warm, `reasons`); gamedata.py (`music`); transport/pixel.py (`on_baseline`) |
| **C2 shell** | new `music_config.py`; panel_api.py (the submit hook, deferred connect, `_push_game_music`, `/api/state`); config.py (`music`, `deck.playerCorner`); deck/server.py (sign-in launch, corner placement); tray.py (`NOTE_MUSIC`); the Connect page |
| **Integrators** | Core.lua (settings, `db.music`, `/needle music`); Options.lua (the section's rows); Director.lua (`ShouldDuck`); the TOC (`## Version: 1.2.1`); wow_shim.lua |
| **D docs** | the documents below |

**Documents to update:**

- **PROTOCOL-v2.md:**
  - §1 module table (MusicServices);
  - §2 event types (CONFIG);
  - §2.1 tags 0x0B–0x0D and `TYPE_TAGS`;
  - §2.4 decoder rules;
  - §3 padding and the 20 s rule;
  - §4.6 queue B and HELLO prefs;
  - §5 Emit and §6 API;
  - §7 vectors (this document's §2.13).
- **DESIGN-v1.2.md:**
  - §0: a row for the Music services dialog;
  - §1.4: the notification list, and "set in game" on the Connect page;
  - §2.2: the event table (CONFIG);
  - §2.3: the measured table (HELLO + PREFS, 144 × 3);
  - §4.3: the title-bar button;
  - §4.7: the Settings section;
  - §4.8: the slash command.
- **CONTRACTS-v1.2.md:**
  - §2.1 TOC order; §2.7 the Companion API; §2.9 `OpenMusic`;
  - a new §2.10 for `ns.MusicServices`;
  - §3.1 the `Frame` fields and `EventType`; §3.5 the gamedata functions; §3.6 MusicConfig.
- **PRIVACY.md:**
  - The beacon is 96 × 3, or 144 × 3 once music choices are set, and then carries those four choices.
  - After Connect or Disconnect, it carries your Spotify Client ID for 3 s, and again for 2 s about 8 s later. That ID is not a secret, but it shows in screenshots and streams; CONFIG lines in the chat log carry it too.
  - `NeedleDropper_Companion\Data.lua` now also holds:
    - your Spotify display name, plan and Client ID;
    - the YouTube player browser, and when you last opened YouTube's sign-in;
    - your four music choices and the results of your last 8 requests.
  - config.json gains a `music` section.
  - Signing in to YouTube from the game puts your Google sign-in cookies in `deck-browser\`. Sign out in that window, or delete the folder, to remove them.
- **FIRST-TEST.md:** §5.5.
- **TROUBLESHOOTING.md:**
  - the browser stayed behind the game;
  - INVALID_CLIENT and Invalid redirect URI;
  - "the app didn't receive this request".
- **INSTALL.md and README.md:** setting up Spotify from the game.
- **PROVIDERS.md:** "Play music with" and the test songs.

---

## 7. Risks

| Risk | Mitigation, and how we find out |
|---|---|
| The browser opens behind a borderless game | The copy says Alt+Tab, and a click on the notification brings the page forward. FIRST-TEST step 3 records what actually happens. |
| The embedded player ignores the profile's YouTube sign-in (third-party cookie policy) | The copy says "optional"; FIRST-TEST step 7 checks it. If it fails, the button stays and its note adds "may still show ads". 1.2.1 never changes the profile's browser settings. |
| Players paste the Client secret | The copy warns in three places, and the timeout notification names INVALID_CLIENT. |
| The wider idle strip (144 × 3) covers something | It is still one 3 px row, inside DESIGN-v1.2 §2.3's clearance budget; `/needle selftest beacon` reports it. |
| Another addon forges requests | The effects are bounded (§2.12), the same exposure as today's FIRE frames. |
| The F1 checksum fix lands (a new trailer or version) | Landed in 1.2.1 as a new trailer without a version bump (D1, D2): CONFIG and HELLO follow the frame format, and the one-row rule stays. A 1.2.0 app and a 1.2.1 addon cannot read each other (§2.11): both must be updated together. |
| `TEST_SPOTIFY_URI` disappears from Spotify's catalogue | The test fails with the reason; the constant is replaced in the next release. |
| In-app Spotify needs Chrome and Widevine (§3.13) | No Chrome, or no Widevine yet: `no-player` with "install Google Chrome, or wait a minute for Chrome's Widevine component", and YouTube plays. The Widevine copy from Chrome's own profile covers a fresh player profile. |
| Spotify changes the Web Playback SDK, or a Chrome update throttles hidden pages again | The start check (progress within 3 s) and the stuck fallback keep drops playing on YouTube; the re-checks reconnect the player at most once a minute. If hidden pages stop the SDK, D14's fallback applies: keep the window shown but off every monitor between songs (no-activate, tool window). |
| A Chromium profile belongs to one browser | The player is Chrome whenever Spotify plays in the app, so the YouTube sign-in window must start `Deck.player_browser()`, not `deck.browser` (§3.8). |
