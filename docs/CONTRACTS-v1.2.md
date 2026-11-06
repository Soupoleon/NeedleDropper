# NeedleDropper v1.2 — module contracts for parallel builders

Binding interface definitions for the v1.2 build. Behaviour, rationale and UI details are in [DESIGN-v1.2.md](DESIGN-v1.2.md) (authoritative), wire formats in [PROTOCOL-v2.md](PROTOCOL-v2.md) and [SETS-FORMAT.md](SETS-FORMAT.md). If a contract below is impossible, implement the closest compatible thing and report it — do not silently diverge.

## 1. Ownership (one owner per file during the build phase)

| Builder | Owns (create/edit) |
|---|---|
| **A1 model** | `addon/NeedleDropper/Locale.lua`, `Sets.lua`, `Soundtracks.lua`, `Share.lua`, `Companion.lua`; `tests/lua/shim_encoding.lua`; `tests/test_v12_model_*.py`, `tests/test_v12_share_*.py`; `tests/vectors/share/**` |
| **A2 window** | `addon/NeedleDropper/UI/*.lua`, `addon/NeedleDropper/Bindings.xml`; `tests/lua/shim_ui.lua`; `tests/test_v12_ui_*.py` |
| **A3 protocol** | `addon/NeedleDropper/Protocol2.lua`, `Beacon.lua`, `ChatLink.lua`, new `Emit.lua`; `companion/needledropper/protocol2.py`, `transport/beacon2.py`; `docs/PROTOCOL-v2.md`; `tests/test_protocol2_*.py`, `tests/test_beacon2_*.py`, `tests/test_v12_beacon_*.py` |
| **C1 engine** | `companion/needledropper/engine.py`, `sharecode.py` (new), `gamedata.py`, `savedvars.py`, `packs.py`, `commands_packs.py` (new), `transport/chatlog.py`, `transport/pixel.py`; `tests/test_v12_engine_*.py`, `tests/test_sharecode_*.py`, `tests/test_gamedata.py`, `tests/test_savedvars.py` |
| **C2 shell** | `companion/needledropper/cli.py`, `config.py`, `panel_api.py`, `deck/**`, `tray.py`, `autostart.py`, `resources.py`, `companion/launcher.py`, `companion/NeedleDropper.spec`, `tools/build_release.ps1`; `tests/test_v12_shell_*.py`, `tests/test_panel_*.py`, `tests/test_tray.py`, `tests/test_autostart.py`, `tests/test_release_*.py` |
| **D docs** | `README.md`, `CHANGELOG.md`, `docs/INSTALL.md`, `PROVIDERS.md`, `PACKS.md`, `PRIVACY.md`, `POLICY.md`, `TROUBLESHOOTING.md`, `MIXTAPE.md`, `SOUNDTRACK.md`, `FIRST-TEST.md` |
| **Integrators (later phase)** | everything else, notably `Core.lua`, `Director.lua`, `Location.lua`, `Discovery.lua`, `Audio.lua`, `Toast.lua`, `Options.lua`, `SelfTest.lua`, the TOC, `tests/lua/wow_shim.lua`, `tests/addon_harness.py`, existing `tests/test_addon_*.py`, `tests/test_integration_*.py`, `tests/test_core_*.py`, `tools/gen_sets.py`, `addon/NeedleDropper/Sets/*.lua`, deletion of `Protocol.lua` / `Map.lua` / `Map.json` / `mapgen.py` |

Builders that need shim or harness support put it in their **own** extension file (`tests/lua/shim_<name>.lua`) and a small loader in their own test helper (e.g. `tests/v12_<name>_harness.py`) that subclasses or wraps `tests/addon_harness.AddonHarness`. The addon integrator merges them later.

## 2. Addon (Lua 5.1; every file starts `local ADDON, ns = ...`)

### 2.1 Load order (final TOC, written by the integrator)
`Locale.lua, Json.lua, Base64.lua, Protocol2.lua, Core.lua, Sets.lua, Sets\AzerothMixtape.lua, Sets\AzerothUnbound.lua, Soundtracks.lua, Share.lua, Companion.lua, Emit.lua, Location.lua, Discovery.lua, Audio.lua, Beacon.lua, ChatLink.lua, Toast.lua, Director.lua, Options.lua, UI\Window.lua, UI\Drops.lua, UI\Soundtracks.lua, UI\Journal.lua, UI\ShareDialogs.lua, SelfTest.lua`. Modules must not call into modules loaded after them at file-load time; use `ns.On("ND_INIT", ...)` or lazy calls.

### 2.2 Existing core services modules may rely on (from Core.lua)
`ns.On(event, fn)`, `ns.Fire(msg, ...)`, `ns.Print(fmt, ...)`, `ns.Debug(fmt, ...)`, `ns.db` (NeedleDropperDB), `ns.cdb` (NeedleDropperCharDB), `ns.settings`, `ns.SetSetting(key, value)`, `ns.AreaName(areaID)`, `ns.Location.Snapshot()`, `ns.Location.Candidates()`, `ns.Director.*`, `ns.Audio.*`, `ns.Toast.Show(drop, opts)`. Internal messages: `ND_INIT` (DBs ready), `ND_LOCATION`, `ND_DISCOVERY`, `ND_SETTING`, `ND_RESET`. **New messages:** `ND_SOUNDTRACKS_CHANGED` (any set or active-list edit), `ND_MAP_CHANGED` (after a rebuild; arg = new map), `ND_COMPANION_DATA` (after Companion.lua read data), `ND_PLAYBACK` (Director state change: armed/active/ended).

### 2.3 `ns.L` (Locale.lua)
`ns.L` is a table with a metatable `__index = function(t, k) return k end` (English keys = English text). Locale files may override for `GetLocale()`. All new user-facing strings go through `ns.L`. `BINDING_HEADER_NEEDLEDROPPER`, `BINDING_NAME_NEEDLEDROPPER_TOGGLE`, `BINDING_NAME_NEEDLEDROPPER_ADD_HERE`, `BINDING_NAME_NEEDLEDROPPER_STOP` are set here as globals.

### 2.4 `ns.Sets` (Sets.lua) — built-ins
- `ns.RegisterBuiltinSet(set)` — called by `Sets\*.lua` (format: SETS-FORMAT.md). Validates `version == 1`, unique `id`, stores a read-only (frozen via proxy or defensive copy) table.
- `ns.Sets.Builtins()` → array of built-in sets in registration order. `ns.Sets.GetBuiltin(id)` → set or nil.

### 2.5 `ns.Soundtracks` (Soundtracks.lua) — model, merge, live apply
Refs are strings `"builtin:<id>"` or `"user:<id>"`. A *drop* is a table with SETS-FORMAT keys (`id, kind, mapID, areaID, subzone, instanceID, label, title, artist, startMs, durationMs, spotify (bare 22-char id), youtube, tags, note`) plus user keys (`file, volume, disabled, basedOn, modified`).
- `Init()` — called on `ND_INIT`; runs schema 1→2 migration (DESIGN §4.2) on `ns.db`/`ns.cdb`, then `BuildActiveMap()`.
- `List()` → array of `{ref, id, name, author, description, tags, builtin, active, priority (1 = top, nil if off), dropCount}` (built-ins first in registration order, then user sets by name).
- `Get(ref)` → set table (user sets: the live SavedVariables table — callers must not mutate it directly; use the API). `Exists(ref)`.
- `ActiveRefs()` → array (copy) — `ns.cdb.active or ns.db.active`. `SetActive(refs)` (validates; writes to the character list if one is in use, else the account list). `UsesCharacterList()` / `SetUseCharacterList(bool)`.
- `Create(name)` → ref | nil, err. `Duplicate(ref, name?)` → ref. `Rename(ref, name)` → ok, err. `Delete(ref)` → ok, err (user only; removes from active lists).
- `AddDrop(ref, drop)` → id | nil, err (normalizes via `Validate`, recomputes id from place). `UpdateDrop(ref, id, fields)` → ok | nil, err (may change id if the place changes; returns `ok, newId`). `RemoveDrop(ref, id)` → ok. `GetDrop(ref, id)`.
- `OverrideHere(builtinDropId, fields?)` → `ref, id` — ensures user set `my-drops` ("My Drops") exists and is active at priority 1, copies the effective drop with `basedOn`, applies fields. `TurnOffHere(id)` → writes `{id=…, disabled=true}` to My Drops. `Revert(id)` → removes the My Drops override.
- `Undo()` → ok, description; `CanUndo()` → bool, description (20-step session stack covering add/update/remove/override/turn-off/delete-set).
- `Validate(drop)` → normalized drop | nil, err (limits from DESIGN §5.5). `DropIdFor(place)` where place = `{mapID, areaID, subzone, instanceID}` → id string (precedence inst > area > sub > zone; `Slugify` must equal `models.slugify`). `Slugify(name)`.
- `BuildActiveMap()` → map and assigns `ns.Map`; fires `ND_MAP_CHANGED`. Map shape (unchanged from v1 so Director/Location keep working): `{loaded, drops = {...}, byId, zoneDrops[mapID], subDrops[mapID] (array), looseSubDrops, areaDrops[areaID], instanceDrops[instanceID], problems = {...}, invalid = n}`; each map drop also carries `setRef`, `setId`, `builtin` (bool), `n` (rebuild-local index, debug only). Built with a 0.25 s debounce when triggered by edits (`ScheduleRebuild()`); immediate on `Init`.
- `Merged()` → array of `{drop, setRef, overriddenBy = {refs}, disabled}` for the UI (what plays, by place).
- `Limits = {sets = 50, dropsPerSet = 300, dropsTotal = 3000, undo = 20}`.

### 2.6 `ns.Share` (Share.lua) — share codes (DESIGN §5)
- `Encode(pack)` → code | nil, err. `FromSet(ref)` → pack (pack JSON object per DESIGN §5.2; drafts omitted; returns `pack, omittedCount`).
- `Decode(text)` → `pack, warnings` | `nil, reason` where reason ∈ `notcode, foreign:<Name>, newer, cutoff, damaged, toobig, invalid`.
- `Validate(value)` → `pack, warnings` | nil, reason (whitelist + caps + normalisation).
- `Import(pack, opts)` → ref (never overwrites; never activates; `opts.name` override). `CanUpdate(pack)` → ref | nil; `Update(ref, pack)`.
- Uses `ns.Json`, `ns.Base64` and `C_EncodingUtil.CompressString/DecompressString` with `Enum.CompressionMethod.Zlib` (pcall-guarded; uncompressed `{` path accepted on read).

### 2.7 `ns.Companion` (Companion.lua)
- Reads global `NeedleDropper_CompanionData` (written by `gamedata.py`, schema 1 + `tracks[*].notFound/notEmbeddable` + `imports`) at `PLAYER_LOGIN`; validates defensively; fires `ND_COMPANION_DATA`.
- `Data()` → table | nil. `Status()` → `{present, appVersion, written, spotify, youtube, transports}`. `Track(drop)` → match table | nil (keys: `q:<lower artist>\31<lower title>` or `sp:<id>`). `Imports()` → array of `{code, active}` not yet handled (`ns.db.importsHandled`). `StatusLine()` → localized one-line status ("App seen 2 min before login · Spotify connected" / "NeedleDropper app not found: Spotify and YouTube songs need it" / "Update the NeedleDropper app to 1.2…").

### 2.8 `ns.Emit` (Emit.lua) + protocol cutover (A3)
- `ns.BuildEvent(evType, drop, extraFlags, opts)` → Protocol2 event table (fields per PROTOCOL-v2.md / top of Protocol2.lua) built from the drop: `dropId, placeKey, spotify, youtube, query, startDs, lengthDs, volume, label (localized place), setId`.
- `ns.Emit(evType, drop|nil, extraFlags, opts)` keeps its v1 signature and return (the event table, with `seq`), bumps/persists `seq` (`ns.db.lastSeq`), computes live flags, and sends to `ns.Beacon.Show(ev)` and `ns.ChatLink.Send(ev)`. Director keeps calling `ns.Emit` unchanged. (Emit.lua replaces the v1 `ns.Emit` definition; the integrator removes the old one.)
- `ns.Protocol2` additionally exposes `HasFlag(flags, f)`, `PadLine(seq, len)`, `PadLineCount(bytes, len)`, `FLAG`, `EV`, `EV_NAME` so nothing needs v1 `ns.Protocol`.
- `ns.Beacon`: `Show(ev)`, `Layout()`, `Status()` (cell px, rows, cols, gap/clearance, last frame bytes), `ForceShow(sec)`; geometry + fit + hold rules per DESIGN §2.3.
- `ns.ChatLink`: `Send(ev, opts)`, `InstallFilter()`, `EnsureLogging()`, `StopLogging()`, `Status()`; v2 lines `NDROP2 <base64url frame>`, filter hides `NDROP1`/`NDROP2`/`NDPAD`.

### 2.9 `ns.UI` (UI/*.lua) — window (A2)
- `ns.UI.Toggle()`, `Open(tab)` (`"drops" | "soundtracks" | "journal"`), `Close()`, `IsShown()`, `OpenImport(prefillText?)`, `OpenShare(ref)`, `AddHere()` (the "Add drop here" action), `Refresh()`.
- Built lazily on first open; listens to `ND_MAP_CHANGED`, `ND_SOUNDTRACKS_CHANGED`, `ND_COMPANION_DATA`, `ND_PLAYBACK`, `ND_LOCATION`, `PLAYER_REGEN_DISABLED/ENABLED`.
- Calls only the APIs above plus `ns.Director.PlayTest(drop)` / `ns.Director.Stop()` / `ns.Director.SuppressHere(id)` (integrator ensures these exist), `ns.Toast`, `Settings.OpenToCategory(ns.Options.CategoryID())`.
- Template allowlist (verify on the forever branch): `ButtonFrameTemplate, PanelTabButtonTemplate, PanelResizeButtonTemplate, WowScrollBoxList, MinimalScrollBar, SearchBoxTemplate, InputBoxInstructionsTemplate, ScrollingEditBoxTemplate, WowStyle1DropdownTemplate, WowStyle1FilterDropdownTemplate, UICheckButtonTemplate, UIRadioButtonTemplate, UIPanelButtonTemplate, MinimalSliderWithSteppersTemplate, UIPanelCloseButton`. Interaction rules: DESIGN §4.9.

## 3. Companion (Python ≥ 3.10, stdlib at runtime)

### 3.1 Frames and events
`protocol2.py`: `Frame` (type, seq, flags, tlvs → properties `drop_id, place_key, spotify, youtube, query (artist, title), start_ms, length_ms, volume, label, set_id`), `decode_frame(bytes)`, `parse_chat_line(line)`, `encode_*` for tests. Transports deliver `(event, transport_name)` where `event` is either a v1 `models.Event` (legacy) or a `protocol2.Frame`. Dedupe key: `(seq, type, place_key or n, flags)`.

### 3.2 `engine.Engine` (C1)
Consumes both. v2 FIRE plays without any map: Spotify id → Spotify search on query (if Spotify is available) → YouTube id; `LOCAL_PLAYBACK` → show/log only; `TEST` → play, record nothing. ARM caches the frame by `place_key` and pre-warms (search, device wake, YouTube player preload). CANCEL fades out. HELLO without `DROP_ACTIVE` > 5 s while playing → stop. Every TEST/PING frame calls `on_link_check(frame, transport)` (C2 shows the tick + notification). Search results and failures are recorded through `gamedata.record_track(...)`. Public: `Engine(providers, deck, *, clock, fade_in_ms, fade_out_ms, log, on_link_check=None, on_status=None)`, `submit(event, transport)`, `start()`, `stop()`, `status()`.

### 3.3 `sharecode.py` (C1)
`encode(pack: dict) -> str`, `decode(text: str) -> dict` (raises `ShareCodeError(reason)`, reasons identical to Lua), `PREFIX = "!NeedleDropper:1!"`. Uses `packs.parse_pack` for validation semantics.

### 3.4 `commands_packs.py` (C1)
`register(subparsers)` adds `packs validate <file|code>`, `packs encode <file.json> [--out]`, `packs decode <code|file> [--out file.json]`, `packs convert-legacy` (v1.1 `%APPDATA%\NeedleDropper\packs\*.json` → share codes → `gamedata` imports). `cli.py` (C2) calls `commands_packs.register(subparsers)`.

### 3.5 `gamedata.py` (C1)
Existing API plus `record_track(addons_dir, key, info)`, `set_status(addons_dir, **status)`, `add_import(addons_dir, code, active=False)`; writes are coalesced (≤ 1 write / 2 s) and atomic. Never writes outside `AddOns\NeedleDropper_Companion\`.

### 3.6 App shell (C2)
Connect page (DESIGN §1.4) served at `/`: four cards (World of Warcraft, Spotify, YouTube, Check the link) + footer (Start with Windows, Done). `panel_api.py` keeps only: state, config patch (allowed keys), Spotify connect/disconnect/devices, test signal status, wow detect, addon install fallback, open logs. No soundtrack endpoints. Tray (`tray.companion_menu`), autostart (default on at first Done), notifications, single instance, windowed exe (PyInstaller `console=False`) with rotating file log (`%APPDATA%\NeedleDropper\logs\needledropper.log`, 5 × 1 MB) and a crash-safe thread wrapper that surfaces transport/engine thread failures in `/api/state` and as a tray notification.
