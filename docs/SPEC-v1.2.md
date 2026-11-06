> **Superseded** by [DESIGN-v1.2.md](DESIGN-v1.2.md) (in-game-first). The companion-side soundtrack editor (§C), `NDPACK1:` share codes (§B) and `/needle mark` (§D) described below were never built. The concepts in §A and the default soundtrack brief in §E still apply.

# NeedleDropper — Spec addendum v1.2 (soundtrack sets)

Extends and **overrides** `docs/SPEC.md` and `docs/SPEC-v1.1.md` where they conflict.
Owner request (2026-11-05): *users can create their own curated sets of needle drops; ship a default set with iconic locales and fitting or funny songs.*

## A. Concepts and naming

- A **soundtrack** (a "set"; a pack file on disk) is a named list of needle drops (place → song). UI copy says **Soundtracks** and explains "a soundtrack is a set of needle drops: places and the songs that play there".
- **Bundled** soundtracks (read-only, shipped in the repo `packs/` and inside the exe):
  - `azeroth-mixtape` — **"Azeroth Mixtape" — the default.** Iconic places, fitting or funny songs.
  - `azeroth-unbound` — "Azeroth Unbound". The quiet Kojima-style score for every zone.
- **User** soundtracks: `%APPDATA%\NeedleDropper\packs\<id>.json` (or `NEEDLEDROPPER_HOME\packs`), fully editable.
- **Active** soundtracks: config `packs` = ordered list of references. Later soundtracks win on the same drop id, and a drop entry with `"disabled": true` removes that place's drop coming from earlier soundtracks. References: `builtin:<id>`, `user:<id>`, or a file path (back-compat).
  - Default: `["builtin:azeroth-mixtape"]`.
  - Migration: if a config's `packs` is exactly the old default (the repo/bundled `azeroth-unbound.json` path), replace it with the new default. Keep every other entry.
- The shipped `addon/NeedleDropper/Map.lua` + `Map.json` are generated from the default (`azeroth-mixtape`).

## B. Pack format additions (still `"version": 1`, backward compatible)

- Top level:
  - `id`: slug `[a-z0-9-]{1,40}`, derived from `name` if missing.
  - `tags`: list of strings.
  - `homepage`: optional URL.
- Drop:
  - `tags`: e.g. `["funny"]` or `["fitting"]`.
  - `disabled`: bool.
  - `spotify`: accepts `spotify:track:<id>` or `https://open.spotify.com/track/<id>[?…]`, normalized to the URI.
  - `youtube`: accepts an 11-char id or any `youtube.com/watch?v=`, `youtu.be/`, `/shorts/` or `/embed/` URL, normalized to the id.
- **Share code:** `NDPACK1:` + base64url (no padding) of zlib-compressed UTF-8 JSON of the pack (`ensure_ascii=False`, drop order preserved).
  - Decoded size limit: 512 KB.
  - Import validates exactly like a file.
  - Import never overwrites. An id collision gets suffix `-2`, `-3`, …; a name collision gets ` (2)`.

## C. Companion

### C.1 `packstore.py`

`PackStore(app_dir, builtin_dir)` provides:

| Method | Behavior |
|---|---|
| `list()` | Returns `[{ref, id, name, author, description, tags, builtin, active, order, path, dropCount, errors, warnings}]`. |
| `get(ref)` | Returns the pack. |
| `create(name, author="", description="")` | Creates a user pack. |
| `duplicate(ref, name=None)` | Creates an editable user copy. A bundled source becomes "<name> (my copy)". |
| `save(ref, pack)` | Validates, then writes atomically. Refuses bundled packs. |
| `delete(ref)` | User packs only. Also removes the pack from the active list. |
| `import_code(code)` / `import_json(text_or_bytes)` | Import a pack. |
| `export_code(ref)` / `export_json(ref)` | Export a pack. |
| `set_active(refs)` | Validates refs, keeps the order, saves config. |
| `resolve_active(config)` | Returns ordered paths for `packs.load_packs`. |

All writes are atomic and stay under the app dir.

### C.2 Panel "Soundtracks" section

Replaces the v1.1 "Soundtrack" section.

**Soundtrack cards.** Each card shows the name, author, drop count, tags and badges (*Built-in* / *Yours* / *Active*). Controls:
- an **Active** switch, plus ↑/↓ ordering when more than one is active;
- **Edit** (yours only) or **Duplicate to edit** (built-in);
- **Export** (Download .json / Copy share code);
- **Delete** (yours, with confirm).

The section also has **New soundtrack** and **Import** buttons. Import takes a pasted share code or a chosen .json file.

**Editor.**
- Header fields: name, author, description, tags.
- Drops table with a filter box. Columns: Place, Song, Sources (S/Y/L), Length, Tags, ▶ Test, Edit, Remove.

**Add / Edit needle drop dialog.**

| Field | Behavior |
|---|---|
| **Place** | Searchable zone list grouped by continent (from `/api/zones`), plus an optional *Landmark* (subzone name and optional areaID). *Places you marked in game* lists entries from `/api/marks`; one click fills zone, subzone and areaID. Help text: "Stand there in game and type /needle mark". |
| **Song** | Artist and title. **Find on Spotify** (when Spotify is connected) lists up to 10 results with art, title, artist, album and length. Choosing one fills the Spotify URI and length. When Spotify is not connected, a hint explains how to connect it. |
| **YouTube** | URL or id, plus **Check**. Check calls oEmbed and shows the title and channel, or warns "embedding is turned off for this video — pick another upload". |
| **Local file** | Pick from the media-folder file list, plus a *Play inside the game* checkbox (`inGameFile`). |
| **Start at, Play for, Volume** | Start at (m:ss). Play for (m:ss, optional: whole song). Volume. |
| **Tags and Note** | Tags as chips: *Fitting*, *Funny*, plus custom. A note. |

The dialog shows validation inline: unknown zone, duplicate place in this soundtrack, missing title or artist.

**Saving.** Saving an active soundtrack offers **Rebuild map** and then shows "type /reload in game".

**Merged view.** A table shows what will actually play: one row per place, "from <soundtrack>", and *overridden* or *disabled* notes.

### C.3 API

All endpoints are token-protected JSON, as in v1.1 §C.4.

| Endpoint | Purpose |
|---|---|
| `GET /api/packs` | List soundtracks. |
| `GET /api/packs/<ref>` | Get one soundtrack. |
| `POST /api/packs` `{name, author?, description?}` | Create a soundtrack. |
| `POST /api/packs/<ref>/duplicate` `{name?}` | Duplicate a soundtrack. |
| `PUT /api/packs/<ref>` `{pack}` | Save a soundtrack. |
| `DELETE /api/packs/<ref>` | Delete a user soundtrack. |
| `POST /api/packs/import` `{code}` or `{json}` | Import. Body cap 1 MB for this endpoint only. |
| `GET /api/packs/<ref>/export?format=json\|code` | Export. |
| `POST /api/packs/active` `{refs: [...]}` | Set the active soundtracks. |
| `GET /api/merged` | The merged active drops, with their source and overrides. |
| `GET /api/zones` | The zone list. |
| `POST /api/spotify/search` `{title, artist}` or `{q}` | Up to 10 `{uri, title, artist, album, durationMs, artUrl}`. Needs a connected Spotify. |
| `POST /api/youtube/check` `{urlOrId}` | `{id, ok, title, author, embeddable, error?}`. Server-side GET to `https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=<id>&format=json`, 5 s timeout: 200 → ok, 401/403 → not embeddable, 404 → not found. |
| `GET /api/marks` | Places marked in game. |
| `GET /api/media` | Files in `local.mediaDir`: audio extensions only, no traversal. |

Refs in URLs are `builtin:<id>` / `user:<id>`, URL-encoded. Unknown ref → 404. Writing a bundled pack → 409.

### C.4 CLI

`packs list` · `packs new <name>` · `packs duplicate <ref> [name]` · `packs import <file|code>` · `packs export <ref> [--code] [--out FILE]` · `packs activate <ref> [<ref> ...]` · `packs delete <ref>`.

### C.5 SavedVariables reader (`savedvars.py`)

A safe, minimal parser for WoW SavedVariables files.
- **Grammar:** global assignments of Lua table constructors. Values are strings (incl. `\n \" \\ \ddd` escapes and long brackets), numbers, booleans and `nil`. It supports nested tables, `[key] =` / `key =` / positional entries, and `--` comments.
- **Output:** Python dicts and lists. It never executes code and caps input at 5 MB.
- `read_marks(paths)` scans `<flavor_root>\WTF\Account\*\SavedVariables\NeedleDropper.lua` and returns `[{account, mapID, zone, subzone, areaID, instanceID, t, note}]`, newest first. Unparseable files are reported and do not crash.

## D. Addon

- **`/needle mark [note]`.** Stores `{mapID, zone, subzone, areaID, instanceID, t = time(), note}` in `NeedleDropperDB.marks`.
  - `zone` is the localized `C_Map.GetMapInfo(mapID).name` and `subzone` is `GetSubZoneText()`.
  - Marks are newest first, capped at 50; the same place within 60 s replaces the earlier mark.
  - Prints: "Marked <place>. Type /reload, then add a needle drop here in the NeedleDropper app (Soundtracks → Add needle drop)."
  - `/needle marks` lists the marks and `/needle marks clear` empties them.
- **`ns.Location.CurrentAreaID()`.** Best effort:
  - Iterate `C_MapExplorationInfo.GetExploredAreaIDsAtPosition(mapID, C_Map.GetPlayerMapPosition(mapID, "player"))`.
  - Return the id whose `C_Map.GetAreaInfo(id)` equals `GetSubZoneText()`.
  - Everything is pcall-guarded; return nil when unknown.
  - Used by `/needle where` (prints `areaID`) and `/needle mark`.

## E. Default soundtrack — "Azeroth Mixtape" (`packs/azeroth-mixtape.json`)

**Places.** 32–40 drops at **iconic** places only:
- every capital city;
- signature zones;
- famous landmarks, for example Goldshire, Booty Bay, Gadgetzan, the Crossroads, Ratchet, Southshore, Tarren Mill, Light's Hope Chapel, Everlook, Aerie Peak, the Shimmering Flats, the Dark Portal, Karazhan, Menethil Harbor, Thelsamar;
- the new Forever zones: Mount Hyjal, Riverglades, Shen'dralas, Zephras Isle.

**Songs.**
- Roughly half **fitting** (the place's mood, epic or wistful) and half **funny** (puns, memes and community in-jokes). Tag every drop `fitting` or `funny`.
- Family-friendly: no explicit tracks. No song is used twice, and no artist appears more than twice.

**Verification.** Every song needs:
- a verified real recording, with the exact canonical title and artist;
- `durationMs` from the iTunes Search API;
- an **official, embeddable** `youtube` id, validated through oEmbed;
- for landmarks, an `areaID` verified on Wowhead classic zone pages (`/classic/zone=<id>`);
- a one-line `note` explaining the fit or the joke. Never quote lyrics.
