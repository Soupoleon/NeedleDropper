# NeedleDropper — Spec addendum v1.1 (public release)

Extends and **overrides** `docs/SPEC.md` where they conflict. Decisions made by the project owner on 2026-11-05.

## A. Decisions

| Topic | Decision |
|---|---|
| Music services | **Spotify Premium** (primary), **YouTube** (secondary), **local audio files** (fallback; in-game playback preferred). |
| Apple Music | **Out of v1.** Delete `providers/applemusic.py` and every reference (config defaults, provider list, CLI, UI, docs). Packs may still contain an `appleMusic` field; it is ignored. Docs say "Apple Music: not supported in v1 (Windows offers no way to control it)". |
| Popup | **The in-game title card is the only popup.** The companion must never pop a desktop window for a drop — the single exception is the YouTube player, which YouTube's policy requires to be visible while it plays (§C.3). |
| Audience | **Public release** → a one-click Windows companion (exe), an addon zip for addon sites (CurseForge / Wago / WoWInterface), a friendly settings page to connect music services, complete player docs. |
| License | **MIT**, `Copyright (c) 2026 NeedleDropper contributors`. |

Default provider order becomes `["spotify", "local", "youtube"]`.

## B. In-game title card (`Toast.lua`) — the popup

Kojima / Death Stranding style: quiet, cinematic, centered, no frame or background box.

- **Content**
  - Line 1 — the place, **client-localized** (works on every game locale): zone drop → `C_Map.GetMapInfo(mapID).name`; subzone drop → `C_Map.GetAreaInfo(areaID)` if `areaID`, else `GetSubZoneText()` at fire time, else `drop.label`; instance drop → `GetInstanceInfo()` name; fallback `drop.label`. Rendered upper-case with letter spacing: split into UTF-8 characters (pattern `[%z\1-\127\194-\244][\128-\191]*`), join with one space and words with three spaces — only when every character is below U+3000 (skip spacing for CJK). `string.upper` only touches ASCII; that is acceptable.
  - Line 2 — a thin horizontal rule (1 physical px, white, alpha ≈0.4) that grows outward from the center while the card fades in.
  - Line 3 — `Artist  ·  Title` from the drop (U+00B7 middle dot).
  - Test / replay cards use the same layout (test: line 1 `NEEDLEDROPPER`, line 3 `Test drop`).
- **Fonts**: `STANDARD_TEXT_FONT` (locale-correct; fallback `"Fonts\\FRIZQT__.TTF"`), outline `"OUTLINE"`, soft shadow. Line 1 ≈ 30 px × `toastScale`, line 3 ≈ 15 px × `toastScale`, warm off-white (`0.95, 0.93, 0.88`).
- **Placement**: frame `NeedleDropperTitleCard`, parent `UIParent` (so Alt+Z hides it with the rest of the UI), strata `FULLSCREEN_DIALOG`, mouse disabled, anchored `TOP` of `UIParent` at `-UIParent:GetHeight() * toastY`.
- **Animation** (OnUpdate-driven so it is testable in the shim): fade in 1.2 s with an 8 px upward drift, hold `toastSeconds`, fade out 2.0 s. A new card replaces a showing one (restart). If combat starts while the card is shown, fade out over 0.5 s.
- **Settings** (add to defaults, options panel, `/needle status`): `toast = true`, `toastScale = 1.0` (0.6–1.6, step 0.05), `toastY = 0.22` (0.08–0.50, step 0.01), `toastSeconds = 7` (3–15, step 1). Slash: `/needle toast` shows a preview card for the current location.
- The card shows on every FIRE (also when the companion plays the audio), on replay/play/test, and never during cinematics (already gated).

## C. Companion control panel — "connect your music"

### C.1 App shape
- Running the companion with **no subcommand** (double-clicking `NeedleDropper.exe`) = `run` + open the control panel in the default browser the first time (or when Spotify/YouTube are unconfigured); afterwards the console prints the panel URL. Single instance: if the deck port is already bound by another NeedleDropper, open the panel URL and exit.
- The deck server (127.0.0.1:`deck.port`, default 8322) serves:
  - `GET /` — **control panel** (settings + status)
  - `GET /player` — the YouTube/local **player** page (title card + player area)
  - `GET /events?client=panel|player` — SSE (player commands only go to player clients)
  - `/api/*` — JSON API (C.4)
  - `GET /media/<token>` — whitelisted local file (Range)
- `deck.autoOpen`: `"youtube"` (default — open the player window only when a YouTube drop starts and no player client is connected) | `"always"` | `"never"`.

### C.2 Panel UI (vanilla HTML/CSS/JS, no CDN dependencies, dark theme matching the title card)
1. **Status** strip: Game (found / running), Addon (installed version; "map out of date → type /reload in game" when the addon's map hash ≠ companion's), Link (beacon detected ✓/✗ with last event time; chat log), Now playing (+ Stop).
2. **Music services** — ordered list with ↑/↓ and an enable switch per service:
   - **Spotify** — badge: *Not set up* / *Connected* / *Premium required* / *Error: …*. Setup card with numbered steps: (1) link to `https://developer.spotify.com/dashboard` → *Create app*; (2) any name/description, Redirect URI exactly `http://127.0.0.1:<redirectPort>/callback` with a Copy button, tick *Web API*, save; (3) paste the **Client ID** → **Connect Spotify** (opens Spotify's consent page in a new tab; the panel polls until connected). Then: playback device dropdown (Refresh), switches *Fade in/out* and *Resume my music after a drop*, buttons **Test**, **Resolve soundtrack** (fills missing Spotify tracks + durations, with progress), **Disconnect**. Copy explaining: Premium required; each player creates their own free developer app because Spotify limits shared apps to 5 users.
   - **YouTube** — enable switch; explanation: plays in a small window that must stay visible while the song plays (YouTube's rules), ads unless YouTube Premium, click once in the player window the first time to allow sound; **Test** (opens the player and plays the first pack song that has a YouTube id).
   - **Local files** — companion media folder path (Open folder); explanation of in-game files (`Interface\AddOns\NeedleDropper_Local\media\`, restart WoW after adding files); **Test**.
3. **Soundtrack** — active packs (name, #drops, enable switch, add pack by path), **Rebuild map** (then "type /reload in game"), drops table (place, artist — title, available sources S/Y/L, duration, ▶ test button), search filter.
4. **Game & addon** — WoW folder (auto-detect button: registry `HKLM\SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft` `InstallPath`, then default path), flavor `_forever_`, **Install / update addon** (refuses while the game runs), Open AddOns folder.
5. **Advanced** — pixel link on/off + method + rate, chat-log link on/off, fade-in/out ms, player auto-open mode, open logs / config folder.

### C.3 YouTube player window
Opened (per `deck.autoOpen`) as a small Edge/Chrome app window: `--app=http://127.0.0.1:8322/player --window-size=420,380` (plus `--window-position` from `deck.playerPosition` if set). The player area is ≥ 320×180 px and the page never hides or covers the video while it plays. After the drop ends the page shows "Drop finished" and stays open (the user may close it; it reopens next time).

### C.4 API (all JSON; all `/api/*` require header `X-ND-Token: <token>`; token is random per process and injected into the served HTML as `<meta name="nd-token">`)
`GET /api/state` · `POST /api/config` (deep-merge patch of allowed keys, validated) · `POST /api/spotify/connect {clientId}` → `{authorizeUrl}` (starts the one-shot callback listener on `redirectPort`) · `POST /api/spotify/disconnect` · `GET /api/spotify/devices` · `POST /api/spotify/resolve` (background job; progress in state) · `POST /api/test {provider?, dropId?}` · `POST /api/stop` · `POST /api/map/build` · `POST /api/addon/install {force?}` · `POST /api/wow/detect` · `POST /api/open {what: "addons"|"media"|"logs"|"config"}`.

**Security (required):** bind 127.0.0.1 only; reject any request whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>` (DNS-rebinding guard); reject `/api/*` requests with an `Origin` header that isn't the panel origin; require the token on every `/api/*` call (constant-time compare); never return secrets (tokens) in any response; cap request bodies (64 KB); file-opening endpoints accept only the fixed `what` names.

## D. Public release packaging
- `LICENSE` (MIT), `CHANGELOG.md`, `README.md` (pitch, how it works, quick start), `docs/INSTALL.md`, `docs/PROVIDERS.md`, `docs/PACKS.md`, `docs/PRIVACY.md`, `docs/POLICY.md` (Blizzard add-on policy, read-only companion, Spotify Developer Policy caveats incl. III.6 sync risk and the own-app model, YouTube visible-player rule), `docs/TROUBLESHOOTING.md` (beacon not detected → borderless/windowed, HDR/gamma, `/needle selftest`; Spotify 403/404; YouTube errors 101/150/153).
- Addon zip layout: `NeedleDropper/` (+ `NeedleDropper_Local/media/README.txt` explaining local audio). TOC adds `## X-License: MIT`. `.pkgmeta` for the BigWigs packager (package-as NeedleDropper; ignore everything but the addon).
- Companion exe: PyInstaller (installed into `.venv` only), one-file `NeedleDropper.exe`, console app titled "NeedleDropper", bundles `deck/static/**` and `packs/*.json`. Frozen-path helper: resources resolve via `sys._MEIPASS` when frozen; default pack path points to the bundled pack; user data stays in `%APPDATA%\NeedleDropper`.
- `tools/build_release.ps1`: run tests → build exe → build both zips into `dist\` with the version from `needledropper.__version__` → SHA-256 checksums file.

## E. Starter pack enrichment (for YouTube users and locale-proof landmarks)
- `youtube`: official upload per song (artist channel, label, or YouTube "Topic" auto-upload), **validated** by `GET https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=<id>&format=json` → HTTP 200 (401 = embedding disabled → pick another upload; 404 = gone).
- `durationMs`: from the iTunes Search API (`https://itunes.apple.com/search?term=<artist+title>&entity=song&limit=5` → `trackTimeMillis` of the matching recording), cross-checked against Spotify/other listings when available.
- `areaID` for every landmark drop where it can be verified (needed for non-English clients); keep `subzone` names as the enUS fallback.
