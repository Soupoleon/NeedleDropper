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
| Connect with a new Client ID | Validated, then the browser sign-in starts at once. The ID is saved immediately, unless a working sign-in for another ID exists. Then it is saved only when the new sign-in succeeds. | `TokenManager` reads a Client ID mismatch as a revoked sign-in, and changing `spotify.clientId` cancels the login in progress. Saving first would cost a working connection. |
| Disconnect | Only when the frame's CLIENT_ID equals the app's active Client ID. | A damaged or forged frame can't sign anyone out. |
| Feedback | On the PC, immediately: Spotify's page, the YouTube box, Windows notifications. In game: "Sent…" copy at once, and the result after **Refresh status (reloads UI)**. | The addon only hears from the app when it reads `NeedleDropper_Companion` again. |
| YouTube sign-in | The app opens Google's sign-in page in a **normal** window of the player box's own browser profile (`deck-browser`). | The cookies land where the box plays, and the address bar stays visible, so the player can see accounts.google.com. |
| Schemas | No `ns.SCHEMA` bump. `NeedleDropper_Companion` stays schema 1 and gains a `music` block. config.json gains `music` and `deck.playerCorner`. | Every change is additive; both older versions keep or ignore what they don't know (§5.6). |

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
| | not-connected, a Client ID is known | Spotify: not connected (Client ID …%s). |
| | not-connected | Spotify: not set up. |
| | suffix, when the order excludes Spotify | · not used: Play music with is YouTube only |
| YouTube | `status.youtube` false or `music.youtube.enabled` false | YouTube: switched off in the NeedleDropper app (its Connect page). |
| | `music.youtube.player` none | YouTube: the NeedleDropper app has no player browser (deck.browser in its config.json). |
| | player default | YouTube: ready · plays in your default browser |
| | otherwise | YouTube: ready · player box %s (top right, …) |
| | suffix, when `signInOpened` is set | · sign-in page opened %d %b |
| | suffix, when the order excludes YouTube | · not used: Play music with is Spotify only |
| Last request | | §4.3 |

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

Every send from a Settings row prints its §4.2 copy as one chat line, because Settings has no feedback line of its own.

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
8. "The NeedleDropper app plays 15 seconds of Toto – Africa on Spotify, or a Windows notification says why it can't."
9. "The NeedleDropper app opens the YouTube box in your corner and plays 15 seconds of Toto – Africa."
10. "The game reads the NeedleDropper app's report only when the interface loads. Reload now to see the latest status here. Your soundtrack edits are saved first."

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
| any of them | not within 5 s of the last send | "Just sent: give it a moment." |

The connect button reads "Sign in again" when the status is connected or error, else "Connect Spotify".

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
| `TYPE_TAGS` | HELLO {} | HELLO {0x0B}; CONFIG {0x0B, 0x0C, 0x0D}; ARM, FIRE, CANCEL, PING, NONE unchanged (song frames never carry PREFS) |
| `MAX_TAG` (Lua) / `_MAX_KNOWN_TAG` (Python) | 0x0A | 0x0D, so `frameSize` / `_frame_size` count the new tags |
| Fit rule | | unchanged. It never touches 0x0B–0x0D; HELLO and CONFIG frames are always below every budget (§2.6) |
| Frame layout, checksum, whitening, geometry | | unchanged |

### 2.2 The CONFIG frame

```text
offset  size  field
0       1     0x4E
1       1     0x26                       version 2, type 6
2       1     seq                        the next ns.Emit seq, as for every event
3       1     flags                      live flags (ns.ComputeFlags); never TEST
4       1     bodyLen
5       n     [0x0B PREFS] [0x0C ACTION] [0x0D CLIENT_ID]    each optional, in ascending order
5+n     2     Fletcher-16
```

- A CONFIG carries PREFS (when set), an ACTION, or both.
- CLIENT_ID only goes with ACTION connect-spotify or disconnect-spotify, and a frame that carries CLIENT_ID carries no PREFS (headroom, §2.6).
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

Measured with the repo's `adaptive_layout` and `capacity` (vectors in §2.13). `Capacity(1) = 39` bytes.

| Frame | Bytes | Columns | 3 px | 2 px | 4 px |
|---|---|---|---|---|---|
| HELLO, music choices not set in game (as in 1.2.0) | 7 | 32 | 96 × 3 | 64 × 2 | 128 × 4 |
| HELLO + PREFS | 13 | 48 | 144 × 3 | 96 × 2 | 192 × 4 |
| CONFIG, PREFS only | 13 | 48 | 144 × 3 | 96 × 2 | 192 × 4 |
| CONFIG, PREFS + ACTION (tests, YouTube sign-in, connect without an ID) | 21 | 64 | 192 × 3 | 128 × 2 | 256 × 4 |
| CONFIG, ACTION + CLIENT_ID (connect, disconnect) | 33 | 96 | 288 × 3 | 192 × 2 | 384 × 4 |
| Bound: all three (never emitted) | 39 | 112 | 336 × 3 | 224 × 2 | 448 × 4 |

- **One row at every cell size.** Every HELLO and CONFIG is a single row. The widest, 448 px at 4 px cells, equals `MAX_STRIP_WIDTH` and fits inside the 512 × 48 capture.
- **Why one row matters (F1).** The column cover F1 describes needs the same column in at least two rows (bytes i, i+42, i+84). The attack sweep found none of 1,280 one-row HELLO and 300 CANCEL frames breakable.
- **Headroom.** Leaving PREFS out of frames that carry a CLIENT_ID keeps 6 bytes below `Capacity(1)`.
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
  - It refuses (returns nil plus a reason) when the app can't take requests (`not CanSendActions()`), when the same action was sent less than 5 s ago, and when the action needs a client id it doesn't have.
  - Connect carries CLIENT_ID when the game has an ID (typed or stored). Otherwise the app uses its own.
  - Disconnect carries the app's active ID (`music.spotify.clientId`), else the stored one.
- **After a music setting changes,** `ns.MusicServices` calls `B.SetPrefs` at once. It also emits a PREFS-only CONFIG 1.0 s after the last change (debounced), which reaches the chat log and gives the change a fresh seq. That CONFIG is skipped when the app's report says it is older than 1.2.1, because it would only show an unreadable strip to it.

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

**Verified twice:**

- The vectors of §2.13, run through the repo's current (1.2.0) `protocol2.decode_frame`: HELLO + PREFS decodes as a HELLO with the PREFS bytes in `Frame.extra`; every CONFIG decodes as None.
- Strips rendered from those frames at 2, 3 and 4 px into a 512 × 48 BGRA buffer, read by today's `beacon2.scan`: HELLO + PREFS (48 × 1 cells) reports `ok` with PREFS in `extra`; CONFIG (96 × 1 cells) reports `payload invalid` with a confirmed header.

**On a 1.2.0 app's screen reader** (transport/pixel.py), a CONFIG strip therefore has a valid header, so it counts as an `invalid` read, not a headerless one. Capture-method switching is unaffected. After 8 such reads (`LOST_AFTER`), it logs "beacon no longer readable (payload invalid)", once per CONFIG. That is a log line only: no notification, and the next HELLO restores the beacon state.

| | App 1.2.0 | App 1.2.1 |
|---|---|---|
| **Addon 1.2.0** | as today | never sees PREFS or CONFIG; keeps its own config. It writes the `music` block, which the 1.2.0 addon's checker ignores. |
| **Addon 1.2.1** | CONFIG ignored; PREFS in HELLO skipped; the idle strip is 144 × 3 once choices are set. With no `music` block in the report, the game says "Update the NeedleDropper app to 1.2.1…" and disables the request buttons. The "only" modes and "Neither" still work through stripping (§1.5). | everything |

### 2.12 Security

**Who can reach the app:** only the WoW client area (the beacon) and WoW's own chat log (strict system-line parsing). Other players can't inject into either (F8). The one remaining actor is another addon in the same client. It can already draw any strip and send any system message, so v1.2 FIRE frames have the same exposure.

| Concern | Rule |
|---|---|
| Secrets in transit | None. The Spotify Client ID is not a secret: PKCE uses no client secret, and every authorize URL carries the ID in the clear. Tokens never leave `spotify_token.bin`, and the game never sees them. |
| A request opening arbitrary pages | No TLV carries text or URLs. The app opens only Spotify's authorize URL (built by `build_authorize_url` from its `AUTHORIZE_URL` constant) and the constant `YOUTUBE_SIGNIN_URL`. Fuzz-tested (§5.3). |
| What an action can do | Connect starts a sign-in the player must approve on accounts.spotify.com. YouTube sign-in opens Google's page. A test plays 15 s of a fixed song. Disconnect deletes the local tokens, only for the matching Client ID, and Connect restores it. |
| Damaged frames | One-row frames (safe from F1), Fletcher-16 plus whitening, the ACTION check byte, exact TLV lengths, and two identical pixel reads. |
| Replays | The freshness window against the shared PC clock and the identity memory. Baseline frames never trigger actions. The chat-log reader starts at the end of the file, so a re-read after truncation only finds stale requests. |
| A Client ID planted by someone else | It can only start a consent page. The working sign-in stays until a new one succeeds (§3.5). |
| Floods | In the addon, a button stays disabled for 5 s after a click, and queue B holds 4. In the app, the §3.4 limits. |
| A player pasting the Client secret | The format is the same, so it can't be caught. The copy says "Client ID, not Client secret" in three places, and the timeout notification names INVALID_CLIENT. The secret would only reach local files: SavedVariables, config.json, and the chat log when that channel is on. |

### 2.13 Test vectors

All bytes were computed with the repo's Fletcher-16 and base64url helpers. "1.2.0 decoder" is the result of today's `protocol2.decode_frame`. The plain HELLO (`4e 21 00 00 00 0c 6f`) is unchanged from PROTOCOL-v2 §7.

**1. HELLO seq 0, PREFS {spotify-youtube, topright, fade on, resume on}**
- Frame: `4e 21 00 00 06 0b 04 01 01 01 01 33 88`
- Chat line: `NDROP2 TiEAAAYLBAEBAQEziA`
- 1.2.0 decoder: HELLO, PREFS in `extra`

**2. HELLO seq 7, flags 0x20, PREFS {youtube-spotify, bottomleft, fade off, resume on}**
- Frame: `4e 21 07 20 06 0b 04 02 04 02 01 82 b4`
- Chat line: `NDROP2 TiEHIAYLBAIEAgGCtA`
- 1.2.0 decoder: HELLO, PREFS in `extra`

**3. CONFIG seq 12, PREFS {youtube, topleft, fade on, resume off}**
- Frame: `4e 26 0c 00 06 0b 04 04 02 01 02 e1 9e`
- Chat line: `NDROP2 TiYMAAYLBAQCAQLhng`
- 1.2.0 decoder: None

**4. CONFIG seq 13, PREFS {spotify-youtube, topright, on, on} + test-youtube issued at 1762380000**
- Frame: `4e 26 0d 00 0e 0b 04 01 01 01 01 0c 06 04 fb 69 0b c8 e0 ef d2`
- Chat line: `NDROP2 TiYNAA4LBAEBAQEMBgT7aQvI4O_S`
- 1.2.0 decoder: None

**5. CONFIG seq 14, connect-spotify issued at 1762380123 + CLIENT_ID `0123456789abcdef0123456789abcdef`**
- Frame: `4e 26 0e 00 1a 0c 06 01 fe 69 0b c9 5b 0d 10 01 23 45 67 89 ab cd ef 01 23 45 67 89 ab cd ef 68 ec`
- Chat line: `NDROP2 TiYOABoMBgH-aQvJWw0QASNFZ4mrze8BI0VniavN72js`
- 1.2.0 decoder: None

**6. Bound: CONFIG seq 15, flags 0x03, PREFS {none, bottomright, off, off} + disconnect-spotify issued at 4294967295 + CLIENT_ID `ff` × 16**
- Frame: `4e 26 0f 03 20 0b 04 05 03 02 02 0c 06 02 fd ff ff ff ff 0d 10`, then 16 × `ff`, then `50 f0`
- Chat line: `NDROP2 TiYPAyALBAUDAgIMBgL9_____w0Q_____________________1Dw`
- 1.2.0 decoder: None

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

**How the Spotify provider gets the game's fade.** `SpotifyProvider.__init__` reads its switch from the `spotify` section only (`self._fade = bool(cfg.get("fade", True))`, providers/spotify.py L540), and `cli.build_providers` passes it `cfg.get("spotify")` (cli.py L264). A rebuild alone therefore changes nothing. When `music.fromGame` is true, `cli.build_providers` passes `{**cfg["spotify"], "fade": cfg["music"]["fade"]}` instead.

- "Off" is already enforced by the engine's zeros: the provider ramps only when `fade_in_ms > 0` (L1088) and fades out over `fade_out_ms` (L1439), so with 0 nothing fades.
- The override matters for "on", when an advanced user had set `spotify.fade = false` in config.json.

**Applying a change:**
- "Not set" fields (0) and unknown values change nothing.
- MusicConfig computes which keys differ from the current config, and only then calls `Companion._write_config(changes)`. That method writes under `Companion._lock` and runs any rebuilds afterwards, as for a Connect page edit.
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

### 3.4 Actions: common rules

1. Actions are accepted only from CONFIG frames, never from baseline frames.
2. Freshness: the action needs `-5 ≤ time.time() − issuedAt ≤ 45`. Otherwise it is logged as "stale request ignored", with no notification and no result.
3. Identity `(code, issuedAt)`: a second delivery (the other transport, the re-show, a re-queue after preemption) is dropped silently.
4. An unknown code is logged at INFO and ignored.
5. Rate limits:
   - each action at most once per 10 s. The exception is connect-spotify while its sign-in is still waiting: the same authorize page is opened again (at most once per 10 s), and no new session starts;
   - the page-opening actions (connect-spotify and youtube-sign-in) at most 5 times in total per rolling 10 minutes;
   - each test at most once per 10 s.

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

### 3.6 disconnect-spotify

- **The ID must match.** The frame must carry a CLIENT_ID equal (ignoring case) to the configured `spotify.clientId`. Otherwise the result is `ignored`, with the notification "NeedleDropper ignored a Disconnect for a different Spotify app (Client ID …%s)."
- **Otherwise it runs:**
  - `Companion.spotify_disconnect()` deletes the tokens and keeps the Client ID; this is harmless when not connected;
  - `music.spotify.account` and `product` are removed;
  - result `disconnected`; `Data.lua` is flushed;
  - notification: "Spotify disconnected. Your Client ID stays, so Connect signs you in again."

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

**The result** comes from the engine's notes for that drop id within 15 s:

| Note | Result | Text |
|---|---|---|
| `playing` | playing | "Spotify test: playing Toto – Africa for 15 seconds." / "YouTube test: the box in the %s corner is playing Toto – Africa for 15 seconds." |
| `noSource`, `notEmbeddable`, or a provider notice (e.g. Spotify stuck, RC#4) | failed | the first reason |
| nothing within 15 s | failed | "No answer from %s within 15 seconds; see the log (tray → Open log folder)." |

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

**Without them** (the default browser, or no Edge or Chrome): no window opens. The result is `default-browser`: "Your YouTube box plays in your default browser, so sign in to YouTube there (youtube.com)."

**What sign-in does for the box.** The player box loads the IFrame API from www.youtube.com (`player.js` L284, no youtube-nocookie host). So the profile's YouTube session is the one the box uses.

That a signed-in Premium session removes ads in the embedded player is expected, not promised. FIRST-TEST checks it (§5.5), and the copy says "optional".

### 3.9 YouTube box placement

This section only fixes what the corner means; the window itself is shared with the RC#8 non-activating player work.

- **The area:** the WoW window's client area (the pixel transport's window, `wincapture.client_rect`), else the primary monitor's work area.
- **The box** (`deck.PLAYER_WINDOW_SIZE`, unchanged) sits in `deck.playerCorner`, 16 px (scaled to the monitor's DPI) from both edges.
- **The beacon stays clear.** The box never overlaps the beacon capture rectangle (`capture_size(scale)` at the beacon's corner: the top-left 512 × 48 at 100 %). When it would, it moves down to 16 px below that rectangle. That always happens for topleft, and for topright when the window is narrower than the box plus 512 px.
- **A manual position wins:** a non-empty `deck.playerPosition` (an advanced key) applies until the corner changes, which clears it (§3.2).
- **Moving an open box:** a corner change while the box is open moves it at once with `SetWindowPos(SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER)`. It is never activated.

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
| youtube.enabled | boolean: the Connect page switch |
| youtube.player | edge, chrome, default or none: what `open_player` would use |
| youtube.signInOpened | Unix seconds, ≥ 0 |
| prefs | the effective values: order and corner names, fade and resume booleans, fromGame boolean |
| actions | at most 8, newest last. `id` uint32; `action` one of the five names; `result` per §3.4; `text` at most 300 characters; `at` ≥ 0. A new entry with the same (id, action) replaces the old one, so the interim `opened` becomes the final result. |

**Writer API:**
- `CompanionDataWriter.set_music(spotify=None, youtube=None, prefs=None)` merges per sub-table; a field set to None is removed.
- `CompanionDataWriter.record_music_action(entry)`.
- Module functions `gamedata.set_music(addons_dir, **parts)` and `gamedata.record_music_action(addons_dir, entry)`.
- `_lenient_current` keeps a valid `music` block, field by field.

**When it is written:** the app writes the block at start and whenever any part of it changes. `_push_game_status` gets a `_push_game_music` twin with the same compare-then-write rule.

**Downgrade.** `read_companion_data` parses without validating fields. A 1.2.0 app's `_lenient_current` keeps `status`, `tracks` and `imports` and silently drops `music` at its next write.

### 3.11 Notifications

- New kind `tray.NOTE_MUSIC = "music"`, keyed by `"<action>:<issuedAt>:<phase>"`, so each request shows each phase once.
- DESIGN-v1.2 §1.4 ("notifications only when they matter") gains one entry: the results of requests the player sent from the game.
- The texts are in §3.5–§3.8.

### 3.12 Connect page

- **Spotify card:** the setup is unchanged; it stays the path that needs no keyboard in game. It shows "Play music with: %s (set in game)" read-only, and an ID set from the game appears in its box.
- **YouTube card:** shows "Player box: %s (set in game)". It also gains a **Sign in to YouTube** button (`POST /api/youtube/signin`) that runs the same handler as the game's request; C2 may defer this.
- **`/api/state`** gains `music`: the effective choices and the last 8 request results.

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

**When the game refuses to send:**

| Reason | Text |
|---|---|
| app older than 1.2.1 | Not sent: update the NeedleDropper app to 1.2.1 first. |
| no app data | Not sent: install the NeedleDropper app first (see the addon page). |
| the same request less than 5 s ago | Just sent: give it a moment. |
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
2. **Compatibility:** `tests/compat/protocol2_v1_2_0.py` is a verbatim copy of the 1.2.0 decoder, taken at the 1.2.0 release. Every new HELLO decodes with PREFS in `extra`, every CONFIG is None, and nothing raises.
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

### 5.5 In game (add to FIRST-TEST.md)

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
| The F1 checksum fix lands (a new trailer or version) | CONFIG and HELLO follow the frame format, and the one-row rule stays. |
| `TEST_SPOTIFY_URI` disappears from Spotify's catalogue | The test fails with the reason; the constant is replaced in the next release. |
