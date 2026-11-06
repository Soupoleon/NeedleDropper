# NeedleDropper v1.2: in-game-first design

**Owner's request:** an external control panel is too much for a player, so move curation into the game.
**Result:** the game owns every soundtrack decision. The companion keeps only the jobs an addon cannot do: Spotify sign-in and token storage, the YouTube player window, and playback. It no longer stores soundtracks.

**Baseline.** This design was checked against the repo as it stands on 2026-11-05 (D:/NeedleDropper, read-only) and the Blizzard `forever` branch (1.60.1.70790). In several places the repo is already further along than the audits assumed. Where it is, the repo is the baseline and the audit advice is adjusted to fit it:

- `addon/NeedleDropper/Protocol2.lua`, `companion/needledropper/protocol2.py` and `transport/beacon2.py` already implement a multi-row frame that carries the song itself, as TLV fields.
- `tools/gen_sets.py` and `docs/SETS-FORMAT.md` already generate `Sets/*.lua`.
- `gamedata.py` already writes a `NeedleDropper_Companion` data addon.
- `tray.py` already says "the companion has no control panel of its own any more".
- `savedvars.py`, `Json.lua` and `Base64.lua` already exist.

Audit keys cited below: [in-game-editors] [share-strings] [companions] [music-and-beacons] [forever-ui-apis].

---

## 0. Decisions (one answer per open conflict)

| Question | Decision | Deciding reason |
|---|---|---|
| Where curation lives | In game: one NeedleDropper window plus SavedVariables. The companion has no soundtrack UI. | The owner's request. Every studied addon edits content in game [in-game-editors]. `tray.py` already assumes this. |
| How the companion learns the song | The song itself (provider IDs, artist and title, start, length) travels in the **beacon v2 frame** (`Protocol2.lua`). The same frame goes as an opt-in chat-log line. Reading SavedVariables is only for backup and pre-warming. | SavedVariables reach disk only on /reload or logout ([wiki](https://warcraft.wiki.gg/wiki/Saving_variables_between_game_sessions)), so any sync built on them needs a /reload after every edit. |
| Beacon format | Keep Protocol2: TLV frame, whitening, geometry cells. Do **not** adopt the single-row paging format from [music-and-beacons]. | Already built and mirrored in Python. It fits the size limits below. |
| Beacon geometry | Top-left (0,0); 3 px cells; rows × cell size ≤ 9 px (so 3 rows); at most 112 columns (≤ 336 × 9 px); width adapts to the frame. Song frames show only briefly; the idle strip is 96 × 3 px. | §2.3: measured frame sizes and the space above the player frame. |
| Bundled soundtracks | Use the existing `Sets/*.lua` from `gen_sets.py` (SETS-FORMAT.md). `Map.lua` and `Map.json` are retired. | Already built and checked by CI. |
| Editing a built-in soundtrack | A personal top layer, **My Drops**: "Change song here" and "Turn off here" write to it. "Duplicate" stays available for a full copy. | Changing one song is one click. Copy-to-edit (the Edit Mode precedent [in-game-editors]) would copy 38 drops and freeze the copy out of future bundled fixes. |
| Window | One `ButtonFrameTemplate` window with 3 tabs: Drops, Soundtracks, Journal. Settings stay in the Blizzard Settings panel. | The lightweight pattern players expect, as in Baganator and BigWigs: at most 3 tabs, one list plus one form [in-game-editors]. |
| Libraries / load-on-demand | None. No Ace3, LibDeflate or LibDBIcon. The window is built inside the main addon the first time it opens. | One window doesn't justify a library or a second addon folder [in-game-editors][forever-ui-apis]. Revisit load-on-demand only if the UI code passes about 200 KB. |
| Active soundtracks | An account-wide ordered list, plus an optional per-character list. Top of the list wins. | Same pattern as Baganator's profile pointer and Edit Mode's Account/Character layouts [in-game-editors]. |
| Share code | `!NeedleDropper:1!` + standard Base64 (padded) of zlib-compressed JSON (a needledropper-pack v1) + `!`. Replaces SPEC-v1.2 §B `NDPACK1:` (never shipped) and the stale `needledropper-set` sketch in `tests/test_lua_codecs.py`. | zlib's Adler-32 turns damaged pastes into errors instead of silently wrong songs (0% vs 19.8% silent corruption [share-strings]). The standard alphabet is safe to paste into Discord. |
| Bare `/needle`, compartment | `/needle` opens and closes the window; `/needle help` lists commands. Compartment: left-click opens the window, right-click opens Settings, hovering shows a status tooltip. | Every studied addon's bare slash command opens its UI [in-game-editors]. The compartment passes `(addonName, buttonName)` ([AddonCompartment.lua](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_Minimap/Mainline/AddonCompartment.lua) L102-L150). |
| Companion → game data | Keep the existing `NeedleDropper_Companion` data addon (`gamedata.py`), not the audit's `NeedleDropper_Data`. It is never required. | Already built. Status, Spotify matches and the one-time migration can wait for the next login. |
| Local audio files | In game only, from `Interface\AddOns\NeedleDropper_Local\media\` (`Audio.lua`). The companion's local-file playback is retired. | Frames carry no file field. The task defines local files as played in game. |

---

## 1. What moves in game, what stays in the companion

### 1.1 In game (the addon is the source of truth)

| Capability | Where |
|---|---|
| Browse soundtracks, turn them on or off, set their order | Soundtracks tab |
| New / Duplicate / Rename / Delete a soundtrack | Soundtracks tab footer, and the "Showing" dropdown on the Drops tab |
| Add a drop where you stand | **Add drop here** button, `/needle add [Artist - Title \| link]`, key binding |
| Edit a song by typing, or by pasting Spotify or YouTube links | Drops tab detail form |
| Change or turn off a built-in drop | Drops tab: "Change song here" / "Turn off here" (writes to My Drops) |
| Test-play any drop, including an unsaved draft | ▶ on a row, Test in the form |
| Journal and replay; "play again next visit" | Journal tab |
| Preferences | Blizzard Settings panel (`Options.lua`, searchable vertical layout), plus a gear button in the window |
| Import and export share codes | Share and Import dialogs |
| In-game audio files: check that they exist, test them | Drops tab ("In-game file" field) |
| Companion status | Compartment tooltip and window footer (live state plus `NeedleDropper_Companion` data) |

### 1.2 Stays in the companion (only what it must do)

1. **Spotify.**
   - Each user registers their own Spotify app. Development-mode apps allow 5 users, the app owner must have Premium, and search returns at most 10 results ([quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes), [Feb-2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)) [companions][music-and-beacons].
   - Sign-in uses PKCE in the browser with the redirect `http://127.0.0.1:8321/callback` (`spotify_auth.py`).
   - Tokens are stored with DPAPI (`secrets.py`).
   - The companion calls the Web API to play, fade and search.
2. **YouTube.** A visible player window is required while a YouTube song plays (`providers/youtube.py`, `deck/`).
3. **Playback control.** Fades, stop, resuming the player's own music, and choosing which Spotify device plays.
4. **Reading the beacon and the chat log** (`transport/beacon2.py`, `transport/chatlog.py`).
5. **Optional.** Write `NeedleDropper_Companion\Data.lua` (status, matches, one-time migration), read SavedVariables for backups, and pre-warm Spotify searches.

Addons have no network access, so Spotify search cannot run in game. The in-game flow is therefore: paste a link (exact), or type Artist and Title and let the companion search when the drop is armed (§2.5).

### 1.3 Retired

- **Companion soundtrack features:** the control panel's Soundtrack / Rebuild map / Game & addon sections, and SPEC-v1.2 §C.1-C.3 (`packstore`, `/api/packs`, `/api/merged`, `/api/marks`, `/api/media`).
- **Map files:** `mapgen.py`, `Map.lua`, `Map.json` and `maps\<hash>.json`.
- **v1 beacon matching:** the `(hash, n)` lookup and the v1 `Protocol.lua` frame. The addon stops emitting v1 frames; the companion keeps a v1 decoder for old addons.
- **Planned v1.2 features that are no longer needed:** the `/needle mark` → `/reload` → "places you marked" flow (SPEC-v1.2 §D; `/needle add` replaces it) and companion-side local-file playback.
- **Kept for pack authors and CI:** the `packs` CLI (validate, convert to and from share codes).

### 1.4 The smallest companion experience

Every accepted companion has the same shape: install once, sign in if a service needs it, then sit in the tray, start with Windows and stay quiet. The game is where players act [companions] (WA Companion config.ts defaults `autostart=true`; Raider.IO puts its consent toggles and `/rio beacon test` in game). On the PC that means:

1. **First run** (`NeedleDropper.exe`, single instance; `deck.panelOnStart = first-run`). One **Connect** page at `http://127.0.0.1:8322/` replaces today's control panel (`deck/static/panel.html` still contains Game / Addon / Link / Now playing / Music services / Soundtrack / Advanced). It has four cards:
   1. **World of Warcraft.** Detected from the registry InstallPath (`_forever_`), showing the installed addon version from its TOC. If the addon is missing, show a link to CurseForge and a fallback **Install addon** button, never shown when an addon manager owns the folder (TSM AppHelper conflict [companions]).
   2. **Spotify** (optional, Premium).
      - Three steps: open the developer dashboard, copy the redirect URI, paste the Client ID.
      - **Sign in** in the browser.
      - The card then shows the account, the Premium check and a **Play on** device picker.
   3. **YouTube.** Nothing to set up. Explains that a small player window appears while a YouTube song plays.
   4. **Check the link.** "In WoW type `/needle test`". A tick appears when a TEST frame decodes, normally within 2 s. This is Raider.IO's test-signal pattern [companions].
   - Footer: **[x] Start with Windows** (ticked by default; `autostart.py` writes the HKCU Run value and checks it again at every start) and **Done**.
2. **Afterwards: tray only.** The menu is `tray.companion_menu`: status line · Open setup… · Pause needle drops · Start with Windows · Open log folder · Quit. Left-click opens setup. The companion starts minimised at sign-in and stays idle until a `GxWindowClass` window of the `_forever_` client exists. Only then does it capture at 4 Hz.
3. **Notifications only when they matter:**
   - linked to WoW (once per session);
   - test signal received;
   - Spotify sign-in expired (opens setup);
   - song not found on Spotify;
   - YouTube video can't be embedded.
4. **No other windows,** except the YouTube player while a YouTube song plays (`deck.autoOpen = youtube`).
5. **Updates:** a daily check of GitHub releases, shown as a tray notification. Never installed without the player's click.

---

## 2. Data flow

### 2.1 Primary path: the song travels in the beacon

```
 IN GAME (source of truth)                                   COMPANION (holds no soundtracks)
 NeedleDropperDB / CharDB ─► Soundtracks.Rebuild() ─► ns.Map ─► Director ─► ns.Emit(P2 event)
   (+ Sets/*.lua built-ins)       (live, no /reload)                │
                                                                    ├─► Beacon v2 strip ──screen capture 4 Hz──► beacon2.locate_and_decode ─┐
                                                                    └─► ChatLink "NDROP2 <b64url>" ─WoWChatLog.txt (opt-in)─► chatlog ───────┤
                                                                                                                         Engine ─► Spotify / YouTube
 NeedleDropper_Companion\Data.lua ◄── gamedata.py (read by the game at next login / /reload): status, Spotify matches, imports ◄────────┘
```

**Why the beacon is the primary path:**

- **It works with no /reload.** In-game edits, and even Test on an unsaved draft, reach the companion with the next frame.
- **The alternatives can't:**
  - Syncing through SavedVariables needs a /reload or logout after every edit, and the companion's view still lags. WA Companion shows ghost auras for exactly this reason ([#2152](https://github.com/WeakAuras/WeakAuras-Companion/issues/2152)).
  - WA-Companion-style data files only carry data from the companion into the game, so they can't carry the player's edits out.
- **Midnight's restrictions don't touch it.** Pixel textures built from the addon's own data are unaffected by chat lockdown and secret values; chat and addon messages are [companions][music-and-beacons].
- **The companion keeps no soundtrack state.** No map hash, no `n` numbers, no `Map.json` sidecars, and nothing to keep in sync.

Secondary channels, none of them needed for playback:

- **Chat-log line.** Same bytes as `NDROP2 <base64url frame>` (`P2.FormatChatLine`). Opt-in. Deferred while `C_RestrictedActions` reports the Chat restriction; a FIRE line older than 10 s is dropped.
- **`NeedleDropper_Companion`.** Data from the companion into the game (§2.6).
- **SavedVariables.** `savedvars.py` reads `WTF\Account\*\SavedVariables\NeedleDropper.lua` after the game writes it (a file watcher, not polling). It is used for rolling backups (10 copies) and for pre-warming Spotify searches for search-only drops. The companion never writes SavedVariables.

### 2.2 What each event carries (Protocol2 TLVs)

| Event | When | TLVs |
|---|---|---|
| HELLO | idle; after a drop ends | none. 7 bytes. While a drop plays: flag `DROP_ACTIVE` |
| ARM | the Director arms a candidate | first 3 s: the full song; afterwards: `PLACE_KEY` only |
| FIRE | first-visit drop, Test, Replay, `/needle play`, `/needle test` (drop id `test`) | the full song, fitted (§2.3); `TEST` flag for Test, Replay and `/needle test`; `LOCAL_PLAYBACK` when the game plays the file itself |
| CANCEL | disarm, stop, skip, long combat, death | `PLACE_KEY`, `DROP_ID` |
| PING | `/needle selftest beacon`, Settings → "Send a test signal" | none, `TEST` flag |

The companion answers any frame with the `TEST` flag by showing a "heard World of Warcraft" notification and ticking the Connect page's check card. Drop id `test` carries no song, so nothing plays.

How a drop maps onto the frame (built from the drop table, never from `Map.lua`):

| Drop field | TLV |
|---|---|
| `id` | `dropId` (`placeKey` = Fletcher-16 of the id) |
| `spotify` | `spotify` (bare 22-character id) |
| `youtube` | `youtube` |
| `artist`, `title` | `query` (`artist` 0x1F `title`) |
| `startMs` | `startDs` |
| `durationMs` | `lengthDs` (absent = the whole song) |
| `volume` | `volume` (absent = 100) |
| localised place name | `label` (≤ 48 bytes) |
| soundtrack id | `setId` |

**What the companion does with each event:**

- **ARM:** cache the song under its `placeKey`. Pre-warm: Spotify search when there is no id; wake the device; open and buffer the YouTube player.
- **FIRE:**
  - `LOCAL_PLAYBACK`: show and log the song, don't play it.
  - Otherwise, in this order: the Spotify id; else a Spotify search on the query (when Spotify is connected); else the YouTube id.
  - `TEST`: play, but record nothing.
- **CANCEL:** fade out.
- **HELLO** without `DROP_ACTIVE` for more than 5 s while something plays: stop (the existing engine rule).

**Required code changes:**

- `Director.Emit` must build the Protocol2 event table (the field list at the top of `Protocol2.lua`) instead of v1's `{type, seq, n, hash, flags, id}`.
- `ChatLink` and `Beacon` must switch to `ns.Protocol2`.

### 2.3 Beacon v2 layout

```
anchor     client-area top-left (0,0). Frame with no parent (Alt+Z can't hide it),
           SetPoint("TOPLEFT", WorldFrame, "TOPLEFT", 0, 0), strata TOOLTIP, level 9000,
           EnableMouse(false); every cell texture SetSnapToPixelGrid(true), SetTexelSnappingBias(0)
           (SetRoundLayoutToNearestPixel is protected [music-and-beacons]).
cell       3 physical px. Setting beaconCellPx 2..4. Schema-2 migration: v1's stored 4 (its old default)
           becomes 3; values outside 2..4 are clamped. beacon2 reads the pitch from the header, so the
           companion no longer needs a matching setting (remove "Match the companion's setting").
height     rows × cellPx ≤ 9 px:  2 px → 4 rows, 3 px → 3 rows, 4 px → 2 rows;
           lowered further at runtime by the measured clearance (below).
width      columns are a multiple of 16, 16..112, chosen per frame:
           rows = ceil(cells / 112) (capped as above); cols = 16 · ceil(ceil(cells / rows) / 16),
           where cells = 7 lead cells + ceil(frameBytes · 8 / 3).
capacity   at 112 columns under the 9 px cap: 2 px = 165 B, 3 px = 123 B, 4 px = 81 B (frame bytes, 7-byte overhead included).
anchors    P2.CORNERS (TOPLEFT, TOPRIGHT, BOTTOMLEFT, BOTTOMRIGHT) stay in the protocol. Only TOPLEFT appears
           in the UI. beaconCorner is an ns.SETTINGS entry flagged advanced = true: Options.lua skips such
           entries, and /needle set beaconCorner still accepts it. Bottom-left likely overlaps Forever's
           default bars: Camelot anchors the main action bar about 2 UI units above the screen bottom, left
           of the micro menu (Blizzard_EditMode/Camelot/EditModePresetLayoutConstants.lua; the bar width is
           an estimate).
```

**Measured** by running `Protocol2.lua` under lupa (`P2.EncodeFrame` / `P2.RowsFor`):

| Frame | Bytes | Rows at 112 cols | Strip at 3 px |
|---|---|---|---|
| HELLO | 7 | 1 | 96 × 3 |
| CANCEL `zone:1429` | 22 | 1 | 240 × 3 |
| FIRE, bundled Mixtape drop (YouTube + query + length) | 60 | 2 | 288 × 6 |
| ... same plus label and setId | 86 | 3 | 240 × 9 |
| FIRE Spotify + YouTube + query + start + length + volume | 95 | 3 | 288 × 9 |
| FIRE user drop with every field (`sub:1429:lion-s-pride-inn`) | 141 | 4, over the cap | fitted to 113 B → 336 × 9 |
| Worst case (all fields at their caps) | 262 | 7 | fitted to ≤ 123 B |

**Fit rule.** When the frame is over capacity, remove fields in this order until it fits:

1. Drop `setId`.
2. Drop `label`.
3. Drop `query`, if a Spotify or YouTube id is present.
4. Drop `dropId` (`placeKey` stays).
5. Shorten `query` on a UTF-8 boundary (`P2.Utf8Truncate`).
6. Drop `volume`.
7. Keep only the preferred provider's id: Spotify if `NeedleDropper_Companion` says Spotify is connected, else YouTube.

Never dropped: `placeKey`, `startDs`, `lengthDs`, flags, and at least one provider id.

This order **replaces** the 255-byte fit order now in `Protocol2.lua` and `protocol2.py` (drop setId → shrink label → shrink query → drop dropId). Both sides change together. `P2.EncodeCells(ev, geom)` gains `geom.maxRows`, and the byte budget becomes the row capacity. `test_protocol2_contract.py` enforces identical output.

**Why ≤ 9 px avoids the default UI:**

- In the Classic Edit Mode preset, PlayerFrame is anchored TOPLEFT at (+4,−4) [music-and-beacons] (`Blizzard_EditMode/Mainline/EditModePresetLayouts.lua`). Its first art sits further down:
  - `Blizzard_UnitFrame/Camelot/PlayerFrame.lua` anchors the StatusTexture overlay at (17,−14);
  - the portrait sits at (24,−19) (`Mainline/PlayerFrame.xml`);
  - the frame border `PlayerFrameContainer.FrameTexture` (atlas `UI-HUD-UnitFrame-Player-PortraitOn`, `useAtlasSize`, centred) has a height that isn't in the Lua source.
- So the art starts about 18 UI units below the screen top:

  | Screen and UI scale | Clearance |
  |---|---|
  | 1366 × 768, scale 0.65 (worst case) | 11.7 px |
  | 1080p, scale 0.65 | 16.5 px |
  | 1080p, scale 1.0 | 25 px |
  | 1440p default | 21.6 px |
  | 4K, scale 0.65 | 32.9 px |

- In the Modern preset, PlayerFrame sits at the bottom centre. Top-centre widgets start 15 units down and the minimap is top-right, so nothing else shares the top-left edge.

**Runtime guard.** The exact atlas height isn't known, so the addon measures instead of assuming:

- **When:** on `PLAYER_ENTERING_WORLD`, `UI_SCALE_CHANGED`, `DISPLAY_SIZE_CHANGED` and EventRegistry `EditMode.Exit` (EditModeManager.lua L124).
- **What:** `gap = physicalHeight − region:GetTop() · region:GetEffectiveScale() / PixelUtil.GetPixelToUIUnitFactor()`.
  - Regions: `PlayerFrame.PlayerFrameContainer.FrameTexture` and TargetFrame's equivalent (falling back to the frame itself).
  - Only regions that are shown and whose left edge is within 448 px.
  - All reads pcall-guarded.
- **Result:** rows cap = `floor((gap − 2) / cellPx)`, within the 9 px cap. If the frame doesn't fit, the addon first tries 2 px cells; only then does the fit rule remove more fields.
- **If even 2 px cells can't fit the fitted frame:** draw 1 row anyway (the beacon must exist). `/needle selftest beacon` then warns "the beacon overlaps your player frame: move the frame in Edit Mode or set beaconCorner".

**Timing and acceptance.** This replaces v1's `Beacon.lua` queue. `IMPORTANT` / `MIN_HOLD` and the 31-texture strip are v1-only and are rewritten, not extended.

- **Idle:** HELLO stays visible, as in v1, but at 96 × 3 px instead of 124 × 4.
- **ARM:** the full song for 3 s (about 12 captures at 4 Hz), then the `placeKey`-only ARM while armed.
- **FIRE:** the full song for at least 3 s (survives a brief overlay notification), then HELLO + `DROP_ACTIVE`.
- **CANCEL / PING:** at least 1 s each.
- **Large strip on screen:** about 6 s per drop, with drops at least 10 minutes apart (default cooldown).
- **Texture pool:** created lazily, up to 448 cells (112 × 4).
- **Companion acceptance:** two identical valid reads (the v1 rule).
  - Valid means: header, geometry, whitening pad bits, magic, version, TLV structure and Fletcher-16.
  - Capture region: the top-left 512 × 48 px of the client area.
  - The whitened magic and checksum reject foreign strips such as CraftPresence or Dwow (top-left; neither ships a 16001 TOC) [companions].
- **Privacy:** no character names, chat or realm in frames. Labels are place names.

### 2.4 Compatibility

- The v1.2 addon emits only v2. The v1.2 companion decodes v1 and v2.
- If `NeedleDropper_Companion` is missing or `appVersion < 1.2`, the window footer and compartment tooltip say "Update the NeedleDropper app to 1.2 to play Spotify and YouTube songs".
- Title cards and in-game files work without the companion.

### 2.5 Pre-warming and write-back for songs without a link

At ARM, the companion searches Spotify with `track:"<title>" artist:"<artist>"` (limit 10). It picks by name similarity and by `durationMs` when known, then caches the result by query. It then:

- shows a notification with the match;
- records the match in `NeedleDropper_Companion` `tracks` under `q:<lowercased artist␟title>`, including `notFound` or `notEmbeddable` flags.

After the next login, the Drops form shows "Spotify match: *Africa · Toto (4:55)* [Use this exact song]", which copies the id into the drop. YouTube needs a link, because searching it needs a Data API key.

### 2.6 `NeedleDropper_Companion` (data addon)

- Unchanged mechanism (`gamedata.py`): a separate folder that addon managers don't own, `## Interface` copied from NeedleDropper.toc, atomic writes, created on first run so it exists before the client starts.
- Schema 1 gains `tracks[*].notFound`, `tracks[*].notEmbeddable` and a top-level `imports` list: `{ code = "!NeedleDropper:1!…!", active = bool }`, used once for migration (§4.2).
- The addon reads `NeedleDropper_CompanionData` at `PLAYER_LOGIN`. A missing or malformed table just means "no app data". Anything it contains goes through the same validator as an import.

---

## 3. Bundled soundtracks (Lua data inside the addon)

- **Already done:**
  - `packs/*.json` → `tools/gen_sets.py` → `addon/NeedleDropper/Sets/AzerothMixtape.lua` and `AzerothUnbound.lua`.
  - Each file calls `ns.RegisterBuiltinSet{...}` (contract: `docs/SETS-FORMAT.md`).
  - Output is byte-deterministic.
  - `--check` and `tests/test_setgen.py` keep the files in sync with the packs.
- **To do:**
  1. Add `Sets.lua`, which defines `ns.RegisterBuiltinSet` (stores frozen tables; rejects `version > 1`; refuses duplicate ids). List it in the TOC **before** the `Sets\*.lua` lines.
  2. Remove `Map.lua` and `Map.json` from the TOC and the addon.
  3. Run `gen_sets.py --check` in `tools/build_release.ps1`.
  4. `.pkgmeta` needs no change: `packs/` stays excluded and `Sets/` ships inside the addon folder.
- **Default active list:** `{ "builtin:azeroth-mixtape" }`. Built-in ids are permanent keys in saved data.
- **History continuity (checked).** The v1.1 `Map.lua`/`Map.json` in the repo was generated from **Azeroth Unbound** (62 drops, hash 39761), not Mixtape. Its 62 ids equal `Sets/AzerothUnbound.lua`'s ids exactly; both come from `models.drop_id_for`. So `done` and `journal` carry over unchanged:
  - places Azeroth Mixtape shares with Unbound keep their "heard" status;
  - Mixtape-only places (for example `area:87` Goldshire) are new and follow the normal history rules on the next visit.
  - Heard-status is keyed by **place**, by design ("the first time you arrive"). Switching soundtracks doesn't replay places already heard; the Journal's "Play again next visit" and `/needle reset` cover that.
  - A test pins all of this (§6.3).
- **Spotify ids:** bundled packs ship YouTube ids plus artist and title, so Spotify users rely on the companion's search at ARM time (§2.5). Verified Spotify ids can be added to the packs later; `gen_sets` already accepts them.

---

## 4. The in-game window

### 4.1 Modules and TOC

```
## Interface: 16001                (Title, Notes, Author, Category, X-License lines unchanged)
## Version: 1.2.0
## SavedVariables: NeedleDropperDB
## SavedVariablesPerCharacter: NeedleDropperCharDB
## AddonCompartmentFunc: NeedleDropper_OnAddonCompartmentClick
## AddonCompartmentFuncOnEnter: NeedleDropper_OnAddonCompartmentEnter
## AddonCompartmentFuncOnLeave: NeedleDropper_OnAddonCompartmentLeave
## IconTexture: Interface\Icons\INV_Misc_Drum_01
Locale.lua            L table, enUS fallback (@localization@ via the BigWigs packager)
Json.lua  Base64.lua  Protocol2.lua
Core.lua              settings, DB init and migration, events, slash commands
Sets.lua  Sets\AzerothMixtape.lua  Sets\AzerothUnbound.lua
Soundtracks.lua       model: user sets, active stack, My Drops overlay, merge → ns.Map, undo, validation
Share.lua             share-code encode / decode / validate
Location.lua  Discovery.lua  Audio.lua  Beacon.lua  ChatLink.lua  Toast.lua  Director.lua
Companion.lua         reads NeedleDropper_CompanionData
Options.lua
UI\Window.lua  UI\Drops.lua  UI\Soundtracks.lua  UI\Journal.lua  UI\ShareDialogs.lua
SelfTest.lua
```

- `Protocol.lua`, `Map.lua` and `Map.json` are deleted.
- `Bindings.xml` loads automatically and must **not** be listed in the TOC ([wiki](https://warcraft.wiki.gg/wiki/Bindings.xml)).
- Nothing in `UI\` creates frames until the window first opens.

### 4.2 Data model and live apply (no Ace3)

```lua
NeedleDropperDB = {                       -- account
  schema = 2, settings = {...}, accountDone = {...}, lastSeq = 0,
  soundtracks = {                         -- user soundtracks by id ([a-z0-9-]{1,40}); built-ins never stored
    ["my-drops"] = { id = "my-drops", name = "My Drops", author = "", description = "", tags = {},
                     revision = 4, created = t, modified = t,
                     drops = { <drop>, ... } },               -- array; order = creation order
  },
  active = { "user:my-drops", "builtin:azeroth-mixtape" },    -- priority order: index 1 wins
  ui = { point = {...}, w = 700, h = 500, tab = 1, showing = "mix", author = "" },
  importsHandled = { ["<pack id>@<revision>"] = true },   -- one-time migration offers already answered
}
NeedleDropperCharDB = {                   -- character
  schema = 2, done = {...}, scanned = {...}, journal = {...}, lastFireAt = 0,
  active = nil,                           -- nil = use the account list; a table = this character's own list
}
-- <drop> uses the SETS-FORMAT drop keys (id, kind, mapID, areaID, subzone, instanceID, label, title, artist,
-- startMs, durationMs, spotify = bare id, youtube, tags, note) plus user-only keys:
-- file (bare name in NeedleDropper_Local\media), volume (0..100), disabled (true = "turned off here"),
-- basedOn (set id it overrides, for "Revert"), modified.
```

**Merge rules** (`Soundtracks.BuildActiveMap`):

1. Walk `ns.ActiveRefs()` (= `cdb.active or db.active`) in priority order.
2. Take the **first** entry per drop `id`. Later entries with that id are hidden and shown as "overridden".
3. `disabled = true` claims the id and plays nothing.
4. Drafts (no artist or title) claim nothing and never play.
5. Refs to missing soundtracks are skipped and pruned on save. If nothing valid is left, fall back to the default list.

This keeps SPEC-v1.2 §A's override and disable rules but reverses the order: the top of the list wins, matching what the list shows.

**Live apply:**

1. After every commit, after a 0.25 s debounce: `BuildActiveMap()`.
2. The result has the same indexes as today's `ns.BuildMap`: `byId`, `zoneDrops`, `subDrops`, `areaDrops`, `instanceDrops`. `validateDrop`'s mandatory `n` and `hash` go away (`n` is a per-rebuild index, only for the debug list).
3. Then `ns.Fire("ND_MAP_CHANGED")`.
4. The Director then:
   - disarms (CANCEL) if the armed drop's id disappeared or its song changed;
   - re-evaluates the current place.
5. No /reload.

**Exploration history.** `historyMode = respect` marks explored zones as heard. It now applies to **built-in drops only**. A zone drop the player adds plays on their next arrival, even in a zone they have already explored. Otherwise "I added a song to Westfall and it never plays" would be the first bug report.

**Add drop here.** Sets `D.suppress[id]` for the current visit (the existing `/needle stop` mechanism), so a drop added where the player stands doesn't fire in the middle of editing. Test plays it now.

**Per-character soundtracks.** "Use different soundtracks on this character" copies `db.active` into `cdb.active`. Unticking sets it back to nil. This is the Baganator `BAGANATOR_CURRENT_PROFILE` pattern [in-game-editors]. "Profile" never appears in the UI. The play-once scope setting (Character / Account) is unchanged.

**Limits:**

| Item | Limit |
|---|---|
| User soundtracks | 50 |
| Drops per soundtrack | 300 |
| User drops in total | 3,000 |
| Undo stack | 20 actions per session |

**Migration (schema 1 → 2, at `ADDON_LOADED`):**

- Settings are kept; `beaconCellPx` moves from 4 to 3.
- `done` and `journal` are kept, because the ids are stable.
- **Active list:** the v1.1 addon kept no active list (the companion generated `Map.lua`, by default from Azeroth Unbound). Every account therefore starts on the new default, Azeroth Mixtape, following SPEC-v1.2 §A's "old default → new default" rule. The companion's migration may propose a different list (built from its v1.1 `packs` config), which the player confirms in the prompt below.
- **v1.1 companion packs:** the companion converts each `%APPDATA%\NeedleDropper\packs\*.json` to a share code and lists them under `imports`. At the next login the addon offers "2 soundtracks from your NeedleDropper app: [Review & import]". They go through the normal preview, and each one is offered only once.
- **Local media:** the Connect page offers to copy `local.mediaDir` audio into `NeedleDropper_Local\media` (.mp3/.ogg only; "restart WoW afterwards").

**Unsaved edits.** SavedVariables are written only at /reload, logout or quit, so a client crash loses the session's edits. After 10 or more edits since login, the footer shows **Save now (reloads UI)**. It calls `ReloadUI()` from the click, which is allowed from a hardware event [forever-ui-apis].

### 4.3 Window shell (template names checked on forever 1.60.1.70790)

- **Frame:** `CreateFrame("Frame", "NeedleDropperFrame", UIParent, "ButtonFrameTemplate")` ([SharedUIPanelTemplates.xml](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_SharedXML/Mainline/SharedUIPanelTemplates.xml) L711), then `ButtonFrameTemplate_HidePortrait(f)` and `f:SetTitle("NeedleDropper")`. Forever re-skins it with classic metal art automatically (Camelot NineSlice overrides).
- **Size and position:**
  - Default 700 × 500.
  - `PanelResizeButtonTemplate` (same file, L1642) with `PanelResizeButtonMixin:Init(f, 620, 420, 1100, 800)` (SharedUIPanelTemplates.lua L1637).
  - Drag by the title bar; `SetClampedToScreen(true)`.
  - Strata `DIALOG`, which is above the Settings panel's `HIGH`.
  - Position, size and last tab are saved in `db.ui`.
- **Closing:** `f.CloseButton:SetScript("OnClick", function() f:Hide() end)`. The template's handler calls `HideUIPanel`, which addon code may not use in combat ([UIParentPanelManager.lua](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_UIParentPanelManager/Shared/UIParentPanelManager.lua) L853-L861).
  - `tinsert(UISpecialFrames, "NeedleDropperFrame")`, and the same for each dialog, so Escape closes them through `CloseSpecialWindows` (L1104-L1116).
  - Never `ShowUIPanel`, never `UIPanelWindows`.
- **Combat:** hide on `PLAYER_REGEN_DISABLED`; reopen on `PLAYER_REGEN_ENABLED` if the player hadn't closed the window (WeakAuras pattern [in-game-editors]). Text being edited is kept, because no frame is destroyed.
- **Tabs:**
  - Three `PanelTabButtonTemplate` buttons: **Drops | Soundtracks | Journal**.
  - Tab 1 is anchored `TOPLEFT` to the frame's `BOTTOMLEFT` (5, 2).
  - Each tab gets `SetID(i)` and a manual `OnClick` (the template has no OnClick) calling `PanelTemplates_SetTab(f, id)` and `PlaySound(SOUNDKIT.IG_CHARACTER_INFO_TAB)`.
  - Then `PanelTemplates_SetNumTabs(f, 3)`.
- **Title bar:** a gear button that opens Settings via `pcall(Settings.OpenToCategory, O.CategoryID())`, from the click only.
- **Footer:** the 26 px button bar holds the tab's buttons on the right and a status line on the left. The status line shows one of:
  - "Playing: Goldshire · Toto – Africa (YouTube) [Stop]"
  - "Waiting for a calm moment: Goldshire (in combat)"
  - "App seen 2 min before login · Spotify connected"
  - "NeedleDropper app not found: Spotify and YouTube songs need it"

### 4.4 Drops tab (the default)

**Top bar** (the template's 35 px attic):

| Control | Template | Notes |
|---|---|---|
| `Showing:` | `WowStyle1DropdownTemplate` + `:SetupMenu` (Blizzard_Menu), 200 px | Radios, Edit Mode layout-dropdown style: **What plays (all active)** · divider · "Yours" / "Built-in" titles with one radio per soundtrack · divider · New soundtrack… / Import…. Call `:GenerateMenu()` after outside changes. |
| Search | `SearchBoxTemplate`, 170 px | `HookScript("OnTextChanged")`. Plain, case-insensitive match on place, artist, title and note. |
| `Filter` | `WowStyle1FilterDropdownTemplate` | Here · Not heard yet (this character) · Needs a song · Changed by you · In-game files · Turned off |
| **Add drop here** | `UIPanelButtonTemplate`, 130 px | Primary button. Tooltip: "Adds this spot to My Drops (or the soundtrack you're showing)". |

**List** (left, 300 px):

- **Widgets:** `WowScrollBoxList` + `MinimalScrollBar` (an EventFrame), `CreateScrollBoxListLinearView(2,2,2,2,1)`, `ScrollUtil.InitScrollBoxListWithScrollBar`, `CreateDataProvider` sorted by localised zone name then landmark, `SetDataProvider(dp, ScrollBoxConstants.RetainScrollPosition)`.
- **Rows:** built in Lua, so call `view:SetElementExtent(36)`. Without it, `ScrollBoxListView.lua` L44 raises an error [forever-ui-apis]. Row parts are built once inside the initializer, behind a guard flag.
- **Line 1:** "Elwynn Forest › Goldshire" in `GameFontNormal`.
- **Line 2:** "Toto · Africa" in `GameFontHighlightSmall`, or "needs a song" in `GameFontDisableSmall`.
- **Right side:** source letters **S Y F** (Spotify, YouTube, in-game File); "heard ✓"; "yours" / "off" / "overridden". Text, not colour alone. A ▶ Test button appears on hover.
- **Right-click** opens `MenuUtil.CreateContextMenu`: Test · Change song here (built-in) / Edit · Turn off here / Turn on · Copy to ▸ · Move to ▸ (user soundtracks) · Revert to built-in · Delete.
- **Tooltip:** `GameTooltip_SetTitle(place)` · sources · note · "from <soundtrack>" · `GameTooltip_AddInstructionLine("Right-click for options")`.
- **On open:** the drop for the player's current place is selected, from `Location.Candidates()[1]`. If there is none, the empty pane says: "No needle drop for <place> yet. Press **Add drop here**, then paste a Spotify or YouTube link."

**Detail form** (right, about 360 px, fields in Tab order):

1. **Place.** "Goldshire — Elwynn Forest", **Use my location**, and scope radios ("This landmark" / "Whole zone", `UIRadioButtonTemplate`, whose label is lowercase `.text`).
   - A **Zone** dropdown is built at runtime from `C_Map.GetMapChildrenInfo` of each continent under Azeroth (uiMapID 947), with names localised by the client.
   - **Landmark** is an `InputBoxInstructionsTemplate`. The areaID is filled in automatically when the player stands there; otherwise the drop is saved by name, with the note "landmark saved by name: works on <client language> clients only".
   - Changing the place gives the drop a new id. Heard-history stays with the old place.
2. **Artist** and **Title** (`InputBoxInstructionsTemplate`, `SetMaxBytes(200)`). Both are required for a finished drop; without them it stays a draft.
3. **Spotify** ("Paste a Spotify song link"). Parsed by `P2.SpotifyIdFromLink`: `open.spotify.com/[intl-xx/]track/<22>?si=…`, `spotify:track:<22>` or a bare id. Status line: "Spotify song ✓", or "That's an album or playlist link: in Spotify right-click the song → Share → Copy Song Link".
4. **YouTube** ("Paste a YouTube link"). Parsed by `P2.YouTubeIdFromLink`: youtu.be, watch?v=, /shorts/, /embed/, /live/, music. and m. hosts. A `t=` or `start=` parameter offers "Start at 1:23?".
5. **In-game file** ("goldshire.mp3 in NeedleDropper_Local\media"). Bare name, .mp3 or .ogg. Status comes from `C_UIFileAsset.IsKnownFile("Interface\\AddOns\\NeedleDropper_Local\\media\\"..name)` ([UIFileAssetAPIDocumentation.lua](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_APIDocumentationGenerated/UIFileAssetAPIDocumentation.lua)): "found ✓", or "not found: copy it there and restart WoW (a /reload is not enough)" [music-and-beacons]. Test plays it with `PlaySoundFile` and checks its willPlay return (`Audio.lua`).
6. **More…** (collapsed by default):
   - Start at (m:ss), Play for (m:ss; empty = the whole song);
   - Volume (`MinimalSliderWithSteppersTemplate`, [MinimalSlider.xml](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_SharedXML/Shared/Slider/MinimalSlider.xml) L34; Spotify and YouTube only, since in-game files play on the Music channel);
   - Tags: Fitting / Funny (`UICheckButtonTemplate`, label `.Text`) plus a custom-tags field;
   - Note (`ScrollingEditBoxTemplate`, 3 lines, 500 bytes);
   - "Turn off this place".
7. **Footer:** ▶ **Test** / ■ **Stop** · **Delete**.

**Drops from a built-in soundtrack** are shown read-only. A banner reads "From Azeroth Mixtape (built-in)" with **[Change song here]** and **[Turn off here]**:

- **Change song here** copies the drop into My Drops under the same id. If My Drops doesn't exist it is created; if it isn't active it is put at the top of the list. The fields become editable, and the cursor goes to Artist with its text selected, so typing replaces it.
- When My Drops already overrides a place, the form is editable and offers **Revert to Azeroth Mixtape**.

**Editing:**

- **Commit:** Enter or leaving a field commits. Escape restores the field's last value and clears focus (`OnEscapePressed`), so the first Escape leaves the field and the second closes the window.
- **Tab order:** Tab and Shift-Tab move through the fields via `.nextEditBox` / `.previousEditBox` (`InputBoxScriptTemplate`).
- **Every EditBox** gets `SetAutoFocus(false)`; the default is autofocus on (`UI.xsd` L1065).
- **Each commit** calls `Soundtracks.UpdateDrop(setId, id, fields)`, which validates, increments `revision`, rebuilds (§4.2) and refreshes the row.

**Add drop here** (button, `/needle add [text]`, key binding):

1. Take `ns.Location.Snapshot()` and `CurrentAreaID()`. The latter is best effort: `C_MapExplorationInfo.GetExploredAreaIDsAtPosition(mapID, C_Map.GetPlayerMapPosition(mapID, "player"))`, matched against `GetSubZoneText()` via `C_Map.GetAreaInfo`, all pcall-guarded (SPEC-v1.2 §D).
2. Work out the id:
   - in an instance: `inst:<instanceID>`;
   - in a landmark (subzone text that isn't the zone name): `area:<areaID>`, else `sub:<mapID>:<slug>`;
   - otherwise: `zone:<mapID>`.
3. If the target soundtrack or the active mix already covers the place, select that drop: HandyNotes' Add/Edit switch ([HandyNotes_EditFrame.lua](https://github.com/Nevcairiel/HandyNotes/blob/master/HandyNotes_EditFrame.lua)) and TomTom's `/wayb` [in-game-editors].
4. Otherwise insert a draft, select it and put the cursor in Artist.
5. `/needle add Toto - Africa` or `/needle add <link>` fills the drop and commits it, then prints "Added Goldshire: Toto · Africa to My Drops."

**Delete** happens at once, with an "Deleted Goldshire. **[Undo]**" bar for 15 s; no confirmation dialog (confirmation fatigue [in-game-editors]).

**Test** calls `Director.PlayTest(drop)`:

- shows the title card;
- plays the in-game file locally, or sends FIRE with the `TEST` flag and the song, even for an unsaved draft;
- never marks the drop heard.

With no link and no companion, the tooltip explains: "Spotify and YouTube songs are played by the NeedleDropper app (last seen …)".

### 4.5 Soundtracks tab

- **List** (left): rows 44 px tall, each with an **On** `UICheckButtonTemplate`, the name, and "by <author> · 38 drops · Built-in / Yours". When two or more soundtracks are on, ↑/↓ buttons appear. Caption: "When two soundtracks have a song for the same place, the one higher in this list plays."
- **Details** (right):
  - name, author, description, tags and homepage (shown as text only; the game can't open links);
  - "Plays at 38 places; 3 are covered by a soundtrack above";
  - built-ins are read-only.
- **Footer buttons:**
  - New, Rename, Delete: `StaticPopupDialogs` with keys `NEEDLEDROPPER_NEW` / `_RENAME` / `_DELETE`. Handlers take `(dialog, data)` and use `dialog:GetEditBox()`, `dialog:GetEditBoxText()` and `dialog:GetButton1()`; there are no `.editBox` or `.button1` fields on 12.x ([GameDialog.lua](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_StaticPopup_Game/GameDialog.lua) L574-L617). Delete uses `button1 = DELETE`, `showAlert = 1`, and the text "Delete <name> and its N drops? This cannot be undone."
  - Duplicate: a built-in becomes "<name> (my copy)".
  - Share…, Import….
  - Built-ins cannot be renamed or deleted.
- **Checkbox:** "Use different soundtracks on this character" (§4.2).

### 4.6 Journal tab

- Newest first, from `cdb.journal` (up to 200 entries). Each row: date, place, Artist · Title, soundtrack.
- Row buttons: ▶ **Replay** (FIRE + TEST), **Play again next visit** (clears `done` for that id for this character and account), **Show drop** (jumps to the Drops tab).
- `ns.AddJournal` also stores the song (`spotify`, `youtube`, `file`, `startMs`, `durationMs`, `setId`), so a replay still works after the drop was edited or deleted.

### 4.7 Settings (Blizzard panel, `Options.lua`)

- Keep the searchable vertical layout and `O.CategoryID()` (`category:GetID()`, which is what 12.x requires).
- Add an **Open NeedleDropper** row at the top (`CreateSettingsButtonInitializer(name, text, onClick, tooltip, true)`).
- "Companion link": pixel beacon, beacon cell size (2-4), chat-log channel, padding, and **Send a test signal** (PING + TEST; "your NeedleDropper app shows a notification within 2 seconds if it can see the game").
- No content editing goes in Settings: canvas pages aren't searchable, and closing Settings returns to the Game Menu [in-game-editors].

### 4.8 Entry points (changes to `Core.lua`)

- **Slash:**
  - bare `/needle` and `/nd` open and close the window (today they print help);
  - `help` lists the commands;
  - new: `add [Artist - Title | link]`, `soundtracks`, `journal` (opens the tab; `journal print` gives the old chat list), `import`, `share [name]`;
  - `selftest` gains `share`;
  - every existing subcommand keeps working.
- **Compartment** (TOC lines in §4.1):
  - `NeedleDropper_OnAddonCompartmentClick(addonName, buttonName)`: left-click opens or closes the window, right-click opens Settings.
  - `…Enter(addonName, button)`: `GameTooltip:SetOwner(button, "ANCHOR_LEFT")`. Lines: what's playing or waiting, or the cooldown; the active soundtracks; app status; "Left-click: open · Right-click: settings".
  - `…Leave` hides the tooltip.
  - No minimap button.
- **`Bindings.xml`:** `NEEDLEDROPPER_TOGGLE`, `NEEDLEDROPPER_ADD_HERE` and `NEEDLEDROPPER_STOP`, all with `category="NeedleDropper"`. `BINDING_NAME_*` strings live in `Locale.lua`. None are bound by default.
- **First login on 1.2:** one chat line. "NeedleDropper 1.2: your soundtrack is Azeroth Mixtape. Type /needle to change a song or pick another soundtrack."

### 4.9 Interaction rules (each is checked by a test, §6.3)

- No `UIDropDownMenu*` and no `EasyMenu`; use `Blizzard_Menu` only.
- No `EnableKeyboard`, `OnKeyDown` or `SetPropagateKeyboardInput`. Cogworks tainted the Escape path this way on 12.0 [in-game-editors].
- No `CopyToClipboard`, which is protected ([wiki](https://warcraft.wiki.gg/wiki/API_CopyToClipboard)); copying out uses a highlighted box and Ctrl+C.
- No `ShowUIPanel` / `HideUIPanel`.
- No `Secure*Template`, and no anchoring to Blizzard's secure unit, action or minimap frames. The window stays insecure, so it works everywhere.
- `ReloadUI()` and `Settings.OpenToCategory` run only from clicks, inside `pcall`.
- Template key-values are read only at OnLoad, so set them afterwards through methods.
- Forever-only helpers (`MenuUtil.CreateSearchEntry`, `CheckboxWithLabelTemplate`, `WowClassicScrollBar`) are feature-detected.

### 4.10 Localisation and accessibility

- **Fonts:** `GameFont*` font objects only; nothing smaller than `GameFontHighlightSmall`.
- **Strings:** Blizzard's own globals for buttons (`DELETE`, `CANCEL`, `OKAY`, `ACCEPT`, `SEARCH`, `CLOSE`, `SAVE`).
- **Place names:** stored as ids and localised when shown (`C_Map.GetMapInfo`, `ns.AreaName`).
- **Keyboard:** full Tab order through the form.
- **Tooltips:** every icon button has one, including the reason it is disabled.
- **States:** shown with letters or words, never colour alone.
- **The window:** follows UI scale (parented to UIParent) and can be resized.
- **Text from players or imports:** every string goes through `ns.EscapeText` (`|` → `||`) before `SetText`.

---

## 5. Share code

### 5.1 Grammar (exact)

```
code        = prefix body "!"
prefix      = "!NeedleDropper:" container "!"           ; container version, currently "1"
body        = Base64 (RFC 4648 §4: A-Z a-z 0-9 + /, padded with "="), no line breaks
payload     = Base64-decoded bytes = zlib stream (RFC 1950) of UTF-8 JSON (RFC 8259)
JSON        = one needledropper-pack v1 object (§5.2)
```

**Readers must accept:**

- the prefix anywhere in the text (Discord ``` fences, chatter around it);
- whitespace and line breaks anywhere in the body;
- U+200B, U+FEFF and U+00A0 (removed or turned into spaces);
- `-` / `_` instead of `+` / `/`;
- missing or partial padding;
- a missing final `!`, which on failure turns "damaged" into "the code was cut off";
- a payload whose first byte is `{`: uncompressed JSON, never written by NeedleDropper; this keeps the whole reader testable without zlib.

`ns.Base64.Decode` already accepts both alphabets, whitespace and missing padding. Known prefixes from other addons get "This is a <WeakAuras/ElvUI/Plater/MDT/BigWigs/Details/Cell/Blizzard> string, not a NeedleDropper soundtrack": `!WA:`, `!E1!`, `!E2!`, `!PLATER:`, `!~MDT2~`, `D!ProfileV2-` and `!CELL:` anywhere in the text; `BW2:`, `BWB1:` and Blizzard's `1|` only at the start of the trimmed text (they would match ordinary text).

### 5.2 JSON body: the pack file format (`packs.py` validates it unchanged)

```json
{"author":"","description":"Songs for my alts","drops":[
  {"areaID":87,"artist":"Toto","label":"Goldshire","subzone":"Goldshire","tags":["funny"],"title":"Africa","youtube":"QAo_Ycocl1E","zone":1429},
  {"artist":"Low Roar","durationMs":352000,"spotify":"spotify:track:4uLU6hMCjMI75M1A2tKUQC","title":"I'll Keep Coming","zone":1429},
  {"disabled":true,"zone":1426},
  {"artist":"Me","file":"stormwind.mp3","inGameFile":true,"title":"Home","zone":1453}],
 "format":"needledropper-pack","id":"my-drops","name":"My Drops","revision":4,"tags":[],"version":1}
```

- **Top level:** `format`, `version` (=1), `id`, `name`, `author`, `description`, `tags`, `homepage`, `revision` (an additive integer), `drops`.
- **Each drop:**
  - place: `zone` (**numeric uiMapID**; zone names remain a legacy companion-only input), `subzone`, `areaID`, `instanceID`, `label`;
  - song: `artist`, `title`, `spotify` (written as `spotify:track:<id>`; read as URI, URL or bare id), `youtube`, `file` + `inGameFile: true`;
  - timing and volume: `startMs`, `durationMs`, `volume`;
  - extras: `tags`, `note`, `disabled`.
- Ids are recomputed on import with SPEC 3.1 precedence (`inst:` > `area:` > `sub:<map>:<slug>` > `zone:`). The Lua slug function must match `models.slugify`.
- **Writers:** keys sorted, no `null`, no empty arrays except `tags`, integers written as integers. Drafts are omitted ("2 unfinished drops were not included").

### 5.3 Lua pipeline (addon)

```lua
-- export
local json, jerr = ns.Json.Encode(pack, { sortKeys = true })                  -- strict, deterministic, raw UTF-8
if not json then return nil, jerr end
local ok, z = pcall(C_EncodingUtil.CompressString, json,
                    Enum.CompressionMethod.Zlib, Enum.CompressionLevel.OptimizeForSize)  -- Zlib = 1 (forever docs)
local ok2, back = pcall(C_EncodingUtil.DecompressString, z, Enum.CompressionMethod.Zlib)
if not (ok and ok2 and back == json) then return nil, L.SHARE_ENCODER_BROKEN end     -- check our own output
return "!NeedleDropper:1!" .. ns.Base64.Encode(z, false, true) .. "!"              -- standard alphabet, padded

-- import (each failure returns nil plus a reason: notcode / foreign / newer / cutoff / damaged / toobig / invalid)
if #text > 65536 then return nil, "toobig" end
text = text:gsub("\226\128\139", ""):gsub("\239\187\191", ""):gsub("\194\160", " ")
local s, e, ver = text:find("!NeedleDropper:(%d+)!")
if not s then return nil, foreign(text) or "notcode" end
if tonumber(ver) ~= 1 then return nil, (tonumber(ver) or 0) > 1 and "newer" or "damaged" end
local body, term = text:match("^([A-Za-z0-9+/=_ \t\r\n%-]*)(!?)", e + 1)   -- explicit ranges: %w/%s follow the C locale
local bytes = #body <= 65536 and ns.Base64.Decode((body:gsub("[ \t\r\n=]", "")))
if not bytes then return nil, term == "" and "cutoff" or "damaged" end
if #bytes > 49152 then return nil, "toobig" end
local json = bytes
if bytes:sub(1, 1) ~= "{" then
  local c1, c2 = bytes:byte(1, 2)
  if not c2 or c1 % 16 ~= 8 or (c1 * 256 + c2) % 31 ~= 0 then return nil, "damaged" end   -- zlib header check
  local ok, out = pcall(C_EncodingUtil.DecompressString, bytes, Enum.CompressionMethod.Zlib)
  if not ok or type(out) ~= "string" then return nil, term == "" and "cutoff" or "damaged" end
  json = out
end
if #json > 262144 then return nil, "toobig" end
local value = ns.Json.Decode(json, { maxBytes = 262144, maxDepth = 6 })
if type(value) ~= "table" then return nil, "damaged" end
return Share.Validate(value)          -- §5.5: whitelist, caps, normalise; returns pack, warnings
```

JSON and Base64 run in pure Lua: deterministic, tested byte-for-byte against Python, and never executed (`Json.lua`, `Base64.lua`). Only zlib is native. That keeps the undocumented parts of `C_EncodingUtil` out of the path: Base64 padding tolerance, JSON escaping and key order [share-strings].

**Prototype results.** Measured with exactly this logic, using the repo's `Json.lua` and `Base64.lua` under lupa and zlib faked with Python's:

- The 38-drop *Azeroth Mixtape*, every note included, encodes to **6,290 characters** (15,974 uncompressed).
- Codes written by Lua and by Python are byte-identical when both use the same zlib. The real client's compressor may produce different bytes, so tests compare decoded packs, never code strings.
- 587 random one-character changes gave 587 errors and **0** silently wrong imports.
- Pastes inside a Discord fence, wrapped at 76 characters, with zero-width characters, or URL-safe and unpadded all decode.
- A code cut in half reports "cut off".
- The §5.2 example passes `packs.parse_pack` with no errors or warnings.

Only the real client's zlib remains unchecked; §6.4 step 1 covers it.

### 5.4 Python pipeline (companion, packs CLI, a future web gallery)

```python
import base64, binascii, json, re, zlib

PREFIX = "!NeedleDropper:1!"
CODE_RE = re.compile(r"!NeedleDropper:(\d+)!([A-Za-z0-9+/=_\-\s]*)(!?)")

def encode(pack: dict) -> str:
    raw = json.dumps(pack, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return PREFIX + base64.b64encode(zlib.compress(raw, 9)).decode("ascii") + "!"

def decode(text: str) -> dict:
    if len(text) > 65536: raise ShareCodeError("toobig")
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\u00a0", " ")
    m = CODE_RE.search(text)
    if not m: raise ShareCodeError(foreign_prefix(text) or "notcode")
    if int(m.group(1)) != 1: raise ShareCodeError("newer" if int(m.group(1)) > 1 else "damaged")
    cut = m.group(3) == ""
    body = re.sub(r"[\s=]", "", m.group(2)).replace("-", "+").replace("_", "/")
    try:
        blob = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
        if len(blob) > 49152: raise ShareCodeError("toobig")
        if blob[:1] == b"{":
            raw = blob
        else:
            d = zlib.decompressobj()
            raw = d.decompress(blob, 262145)
            if len(raw) > 262144: raise ShareCodeError("toobig")
            if not d.eof: raise ShareCodeError("cutoff" if cut else "damaged")
        pack = json.loads(raw.decode("utf-8"), parse_constant=_reject_nan)    # NaN / Infinity rejected
    except ShareCodeError:
        raise
    except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError) as exc:
        raise ShareCodeError("cutoff" if cut else "damaged") from exc
    return ints_from_integral_floats(pack)    # then packs.parse_pack(pack): the same rules as a .json file
```

This lives in a new module, `companion/needledropper/sharecode.py`. It is used by `packs import <code>`, `packs export --code` and migration (§4.2). Browsers can read the same code with `DecompressionStream("deflate")` (zlib format).

### 5.5 Limits and validation (the same in Lua and Python)

| Item | Rule |
|---|---|
| Pasted text | ≤ 65,536 characters (`SetMaxLetters(65536)`). A 300-drop worst case is about 45 K characters (about 150 characters per full drop [share-strings]). |
| Decoded zlib bytes / JSON | ≤ 49,152 B / ≤ 262,144 B; depth ≤ 6 |
| Drops | 1-300; duplicate ids → error |
| Text | name 1-64 B, author ≤ 64, description ≤ 1,000, artist and title ≤ 200 each, subzone and label ≤ 100, note ≤ 500, homepage ≤ 200 (http(s) only); invalid UTF-8 → error; control characters stripped (except `\n` in description and note) |
| Tags | ≤ 8 per item, 1-24 B each, single line |
| Numbers | `zone`, `areaID`, `instanceID`: integers 1..2^31−1; `startMs`, `durationMs`: integers 0..2^31−1 (the pack rule; play length is still capped by `maxDropSec`); `volume`: 0-100; `revision`: integer ≥ 0 |
| Ids | `id` `^[a-z0-9-]{1,40}$`; Spotify `^[0-9A-Za-z]{22}$`; YouTube `^[0-9A-Za-z_-]{11}$` |
| `file` | bare name: letters, digits, space, `._-()`; ends in `.mp3` or `.ogg`; ≤ 100 B; no `/ \ : ..`. Anything else is dropped with a warning. |
| Unknown keys | warned about and dropped (the packs.py behaviour) |

**What import never does:**

- `loadstring` / `RunScript` of anything taken from a code;
- overwrite: an id collision becomes `-2`, `-3`, …; a name collision becomes " (2)"; ids of built-ins are reserved;
- turn the soundtrack on automatically;
- put unescaped text in a FontString.

**Updates.** **Update** is offered only when the id and the (non-empty) author match and the incoming `revision` is higher. It keeps the on/off state and the order.

### 5.6 Import and export dialogs

- **Import** (`NeedleDropperImportDialog`, in `UISpecialFrames`):
  - a `ScrollingEditBoxTemplate` with a `MinimalScrollBar` (`ScrollUtil.RegisterScrollBoxWithScrollBar`). The 65,536-letter cap is set after creation with `box:GetEditBox():SetMaxLetters(65536)`, because the template reads its `maxLetters` key-value only at OnLoad;
  - decodes in its `OnTextChanged` callback only when `userChanged` is true, the prefix is present and the text differs from the cached last text. Blizzard notes the event fires twice on paste (Edit Mode import dialog, [EditModeDialogs.lua](https://github.com/Gethe/wow-ui-source/blob/forever/Interface/AddOns/Blizzard_EditMode/Shared/EditModeDialogs.lua)).
  - **Preview:** name, author, revision and drop count; the first 12 places with S/Y/F letters; then warnings:
    - unknown zones (`C_Map.GetMapInfo` is nil);
    - local files the player may not have;
    - landmarks saved by name;
    - places this would override among the player's active soundtracks;
    - fields that were dropped.
  - An editable **Name** field, prefilled.
  - **Buttons:** **Import** (disabled until the code is valid) / **Update <name>** (when offered) / **Cancel**.
  - After importing: "Imported <name> (off). [Turn on]".
  - Decoding is plain data work, but the dialog hides with the window in combat.
- **Share** (`NeedleDropperShareDialog`):
  - an Author field (remembered in `db.ui.author`, empty by default for privacy);
  - a read-only `ScrollingEditBoxTemplate` with `SetText(code)`, `HighlightText()` and `SetFocus()`; any edit is undone (the Cell pattern [share-strings]);
  - the character count;
  - "Press Ctrl+C to copy. In Discord, paste inside ``` ; long codes arrive as message.txt, which works."

### 5.7 Versioning

- The prefix number changes only when the container changes: alphabet, compression or payload type.
- The JSON `version` changes only for breaking schema changes; added fields stay at version 1.
- Every released reader is kept.
- Newer codes get "made with a newer NeedleDropper: update the addon".

---

## 6. Risks and test strategy

### 6.1 Risks

| Risk | Mitigation | How we find out |
|---|---|---|
| The strip covers UI on unusual setups (768 p screens, moved frames, other UI addons) | ≤ 9 px cap; large strip only briefly; runtime clearance measurement; `beaconCorner` | `/needle selftest beacon` prints the gap, rows, cell size and strip size |
| Bigger frames fail to decode (overlays such as Discord's default top-left notifications, HDR, driver sharpening) | ARM delivers the song before FIRE; FIRE held ≥ 3 s; 1-bit channels with thresholds calibrated on the header; whitening and checksums reject covered strips; never ask players to change gamma | companion `diag` (pitch, rows, unreadable cells); TROUBLESHOOTING entry |
| `C_EncodingUtil` zlib behaves unexpectedly (wrong wrapper, errors vs nil) | pcall every call; export checks its own output; zlib header check; uncompressed `{` path; golden vector in `/needle selftest share` | selftest prints the first byte of `CompressString`'s output (expect 0x78) and the result of the golden-vector check |
| A hostile code inflates huge in the client: `DecompressString` has no output cap except the native `EncodingDecompressSizeLimit` (100 MB) [share-strings] | the 49,152-byte input cap bounds the worst case to about 50 MB, once, before the 256 KiB rejection; decoding only happens on the player's own paste | robustness test with a zlib bomb; in game, a pasted bomb shows "too big" |
| Taint locks players out of the Escape menu | §4.9 rules, static checks, insecure frames only, `UISpecialFrames` | `/console taintLog 1` session; `Logs\taint.log` must not mention NeedleDropper |
| A client crash loses unsaved edits | **Save now (reloads UI)** after 10+ edits; companion SavedVariables backups; share codes | manual crash test (end the process) |
| A search-only drop plays the wrong Spotify song | encourage pasting links; notification names the match; write-back plus "Use this exact song" | companion log of `tracks` |
| YouTube upload not embeddable; video gone | companion reports; `notEmbeddable` flag; editor warns after the next login | provider error codes 100/101/150/153 |
| Spotify development-mode limits (5 users, Premium, 10 search results) | each user registers their own app; YouTube fallback; links before search | Connect page shows the Premium check |
| Old companion with the new addon | the companion decodes v1 and v2; in-game "update the app" message when `appVersion < 1.2` | `NeedleDropper_Companion` appVersion |
| Landmarks saved by name fail on other client languages | prefer areaID; warning in editor and preview | import preview lists them |
| History changes (user drops never play / built-ins replay) | history scan limited to built-ins; ids stable from v1.1 | migration and history tests |
| Addon managers vs companion files | the companion writes only `NeedleDropper_Companion` (and `NeedleDropper_Local\media` when asked), never `NeedleDropper\` | test: the companion's file writes are limited to those folders |
| Data size and speed | limits (§4.2); window built on first open; 0.25 s rebuild debounce | lupa timing test: rebuild with 3,000 drops < 20 ms |

### 6.2 Gaps in the repo today (must be closed in v1.2)

- **Missing protocol pieces:**
  - `docs/PROTOCOL-v2.md` is referenced by `Protocol2.lua`, `protocol2.py` and `beacon2.py` but doesn't exist.
  - `tests/test_protocol2_contract.py` is referenced by `protocol2.py` but doesn't exist.
  - `transport/beacon2.py` has no tests.
- **Not loaded by the game:** `Protocol2.lua`, `Json.lua`, `Base64.lua` and `Sets\*.lua` aren't in the TOC. `ns.RegisterBuiltinSet` (`Sets.lua`) doesn't exist.
- **Still on v1:** `Beacon.lua`, `Director` (`ns.Emit`) and `ChatLink` still speak v1 (31 cells, `(hash, n)`). `ns.BuildMap` / `validateDrop` require `n` and `hash`, and `Map.lua` is still in the TOC.
- **Deleting `Protocol.lua` breaks callers.** `Director.lua` uses `P.HasFlag`; `ChatLink.lua` uses `P.PadLine`, `P.PadLineCount` and `P.FormatChatLine`; `SelfTest.lua` uses `P.PadLine`; `Beacon.lua` uses `P.TOTAL_CELLS` and `P.EncodeCells`. `Protocol2.lua` has no `HasFlag`, `PadLine` or `PadLineCount`, so these move to `Protocol2.lua` (or Core) before `Protocol.lua` is removed. A static test checks that nothing references `ns.Protocol`.
- **Outdated documents:**
  - `docs/SPEC-v1.2.md` §B (`NDPACK1:`), §C (companion editor) and §D (`/needle mark` + /reload);
  - `docs/MIXTAPE.md` steps 3-5;
  - `docs/PACKS.md` "Sharing packs";
  - `deck/static/panel.html` (full control panel).
  - All of them describe the retired flow and must be rewritten.
- **Stale or incomplete:**
  - `tests/test_lua_codecs.py::test_share_code_round_trip_with_python` uses a stale format (`needledropper-set`, base64url, uncompressed);
  - `gamedata.py`'s schema has no `imports`, `notFound` or `notEmbeddable`.

### 6.3 Automated tests (no game client; Lua 5.1 through lupa)

- **Harness.**
  - Binary codec paths run in a **bytes-mode** runtime (`LuaRuntime(encoding=None)`, as `test_lua_codecs.py` does). `tests/addon_harness.py` uses UTF-8 strings, and binary Lua strings sent to Python callbacks raise UnicodeDecodeError there [share-strings].
  - The shim's `C_EncodingUtil.CompressString` / `DecompressString` (Zlib only) call Python's `zlib` in that mode, or through hex strings when running in the UTF-8 harness. They error on bad input, so both of the client's possible behaviours are covered.
- **Share code, golden vectors** in `tests/vectors/share/`:
  - codes written by Python decode in Lua to the same pack;
  - codes written by Lua decode in Python, and `packs.py` validates them;
  - round trips are idempotent.
- **Share code, robustness:**
  - ``` fence with chatter around it; wrapped every 76 characters; U+200B and U+FEFF; URL-safe unpadded;
  - truncated (→ cutoff); one character changed (→ damaged, 0 silent corruptions over 1,000 random mutations);
  - over the size cap; `!NeedleDropper:2!` (→ newer); each foreign prefix;
  - hostile fields: a `|T…|t` title, a `..\\` file path, NaN, 1e400, nesting depth 200, a 300-drop maximum, a zlib bomb (≤ 49 KB in, rejected after inflating past 256 KiB).
- **Protocol2 contract** (`test_protocol2_contract.py`):
  - byte-identical frames, chat lines and cell grids from Lua and Python for an event set: every event type, both IDs, query only, UTF-8 cut points, and the fit rule at row caps for 2/3/4 px;
  - **geometry:** rows × cell size ≤ 9 px and the adaptive column formula; the measured sizes in §2.3 become assertions;
  - **decoding:** Lua cell grids rendered into BGRA buffers at 2/3/4 px and at 100/125/150 % scaling, with ±40 level shifts, noise and covered cells, decode in `beacon2.locate_and_decode` (covered strips → None, never garbage).
- **Model:**
  - merge order, overrides, disabled entries and drafts;
  - My Drops created and activated on the first "Change song here"; Revert;
  - per-character list; pruning of missing refs;
  - limits; undo; the schema 1 → 2 migration (settings, `beaconCellPx`);
  - the v1.1 `Map.json` ids (Azeroth Unbound, 62) equal `Sets/AzerothUnbound.lua`'s ids;
  - the history scan skips user drops;
  - `ND_MAP_CHANGED` disarms when the armed drop is edited or removed;
  - Test of a draft emits FIRE + TEST with the song; local files set `LOCAL_PLAYBACK`.
- **Link parsers:** a shared corpus for `P2.SpotifyIdFromLink` / `YouTubeIdFromLink` and Python (intl- paths, `si=`, album and playlist rejection, `t=`), plus a slug parity corpus against `models.slugify` (accents, apostrophes, CJK).
- **UI smoke test:**
  - a recording frame mock (each `CreateFrame` returns an object that logs its method calls); open the window, switch every tab, add, edit, delete, undo, import and export;
  - assert every template name is in an allowlist checked against the forever branch (`ButtonFrameTemplate`, `PanelTabButtonTemplate`, `WowScrollBoxList`, `MinimalScrollBar`, `SearchBoxTemplate`, `InputBoxInstructionsTemplate`, `ScrollingEditBoxTemplate`, `WowStyle1DropdownTemplate`, `WowStyle1FilterDropdownTemplate`, `UICheckButtonTemplate`, `UIRadioButtonTemplate`, `UIPanelButtonTemplate`, `MinimalSliderWithSteppersTemplate`, `PanelResizeButtonTemplate`);
  - assert every EditBox got `SetAutoFocus(false)`, every top-level frame has a global name in `UISpecialFrames`, the CloseButton's OnClick was replaced, and the list view has an element extent.
- **Static checks** on the addon source:
  - none of `UIDropDownMenu`, `EasyMenu`, `SetPropagateKeyboardInput`, `EnableKeyboard`, `OnKeyDown`, `CopyToClipboard`, `ShowUIPanel`, `HideUIPanel`, `loadstring`, `RunScript`, `InterfaceOptions_`, a global `LoadAddOn`, `bit.`;
  - the TOC lists `Sets.lua` before `Sets\*.lua`; `Bindings.xml` is not listed; no `Map.lua`;
  - `gen_sets.py --check` passes.
- **Companion:**
  - `sharecode.py` vectors;
  - `gamedata.py` with `imports` and flags;
  - the engine plays FIRE frames without any map (Spotify id → query → YouTube), caches ARM by `placeKey`, and respects `LOCAL_PLAYBACK` and `TEST`;
  - the Connect page API exposes no soundtrack endpoints;
  - nothing is written under `Interface\AddOns\NeedleDropper\`.

### 6.4 In-game check for the owner (about 15 minutes, once per client patch)

1. **`/needle selftest share`.** Decodes an embedded code written by Python (expect "OK"), round-trips an export, and prints `CompressString`'s first byte (expect 0x78).
2. **`/needle selftest beacon`.** Shows the largest fitted song strip for 10 s and prints the measured gap, rows and size. In the companion, `diag` must decode it at 100 % and 125 % Windows scaling, in the Classic and the Modern Edit Mode presets.
3. **Window.**
   - Open it with `/needle`, the compartment and the key binding.
   - Escape twice (field, then window).
   - Get pulled into combat: the window hides, then returns.
   - Resize it; switch tabs.
4. **Editing.**
   - In Goldshire: **Add drop here**, paste a Spotify link and a YouTube link, Test. The companion plays the exact song with no /reload.
   - Change a built-in drop → My Drops appears at the top of the list.
   - Delete + Undo.
5. **Instances and files.** In a dungeon: Add drop here (`inst:` id) and Test. With an in-game `.mp3` copied before launch: found ✓, and it plays.
6. **Sharing.** Export, paste into Discord inside ```, copy it back, import: the preview is correct and the soundtrack arrives turned off.
7. **Taint.** `/console taintLog 1`, play one session, check `Logs\taint.log`.