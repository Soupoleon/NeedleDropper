# Bundled soundtrack sets: `Sets/*.lua`

A **soundtrack set** is a named list of needle drops (place → song). Players create and edit sets
in game (stored in SavedVariables); the sets that **ship with the addon** are plain Lua data files
in `addon/NeedleDropper/Sets/`, generated from the soundtrack pack JSON in `packs/` by
`tools/gen_sets.py`. This document is the contract between the pack files, the generator and the
in-game Sets module that consumes the generated files.

```
packs/azeroth-unbound.json ──tools/gen_sets.py──▶ addon/NeedleDropper/Sets/AzerothUnbound.lua
   (edit this)                                      (generated; listed in NeedleDropper.toc)
                                                         │ loaded by the client at login / /reload
                                                         ▼
                                              ns.RegisterBuiltinSet({ id = "azeroth-unbound", ... })
```

Because every set carries the full song identity (Spotify track id, YouTube id, artist + title,
start and length), the addon can hand it to the companion in the beacon and neither the companion
nor a `/reload` ever needs the pack files.

**Contents:** [Generating](#generating) · [Pack input](#pack-input) · [Generated file](#generated-file) ·
[Consumer contract](#consumer-contract-nsregisterbuiltinset) · [Adding a bundled set](#adding-a-bundled-set)

## Generating

```
python tools/gen_sets.py                         # every packs/*.json -> addon/NeedleDropper/Sets/
python tools/gen_sets.py packs/azeroth-unbound.json [more.json] [--out addon/NeedleDropper/Sets]
python tools/gen_sets.py --check                 # CI: writes nothing; fails if anything is stale
```

Any Python 3.10+ works (standard library only; the repo venv is fine). Default paths are anchored at
the repository root, so the tool can be run from any directory.

| Behaviour | Detail |
|---|---|
| File name | CamelCase of the set id: `azeroth-unbound` → `AzerothUnbound.lua`, `lo-fi-2` → `LoFi2.lua` |
| One pack at a time | Each pack is validated and written on its own. A broken pack is reported and makes the exit status 1, but valid packs are still written. Packs that clash (same set id, or file names that differ only by case) all fail. |
| Unchanged = untouched | A file is rewritten only when its content changes, atomically (temp file + rename). Files are UTF-8 without BOM, with LF line endings. |
| CRLF tolerance | A CRLF-only difference (a `git core.autocrlf` checkout) counts as up to date. |
| Foreign files | A file the generator did not write (no generated header) is never overwritten; that pack fails instead. Nor is a file generated from *another* pack that still exists (a copied pack that kept the same set id). |
| Stale files | With the default pack list, a generated file whose pack no longer exists (or now produces a different file name) is reported: as a warning when writing, as a failure under `--check`. Nothing is ever deleted. |
| TOC reminder | When the output folder is inside the addon, a set file that `NeedleDropper.toc` does not list gets a warning, because the client would never load it. This check is skipped if the TOC lists an XML include. |
| Exit status | `0` all good · `1` an invalid pack, a refused write, or (`--check`) a missing, out-of-date or stale file · `2` usage error |

`tests/test_setgen.py` also fails when a shipped set file is out of date with its pack, so the normal
test run catches a forgotten regeneration.

## Pack input

Pack files are the format documented for players in [PACKS.md](PACKS.md) (SPEC.md 3.7) plus the
SPEC-v1.2 additions. The generator reuses the companion's own zone table and id derivation
(`needledropper.zones`, `needledropper.models.drop_id_for` / `slugify`), so ids are identical
everywhere.

### Top level

| Key | | Rules |
|---|---|---|
| `format` | expected | `"needledropper-pack"` (missing: warning; anything else: error) |
| `version` | | `1` (default 1; anything else: error) |
| `name` | expected | Text, shown in game. Missing: warning, and the file stem is used. |
| `id` | optional | The set id: 1–40 of `a-z 0-9 -`, with at least one letter or digit. Default: the name with accents folded (`Ó` → `o`), then the SPEC 3.1 slug (`Azeroth Unbound` → `azeroth-unbound`), at most 40 characters. **Bundled packs should set it explicitly**: renaming a set must not change its id, because players' saved data refers to sets by id. |
| `author`, `description` | optional | Text |
| `tags` | optional | List of distinct, non-empty, single-line strings, e.g. `["kojima"]` (order kept) |
| `homepage` | optional | An `http://` or `https://` link |
| `drops` | required | List of drop objects (at least one enabled) |

### Drops

| Key | | Rules |
|---|---|---|
| `title`, `artist` | **required** | Non-empty text |
| `zone` | location | Zone name, alias (`STV`, `the barrens`, `Un'Goro`), `"zone:1429"` or uiMapID (number or digit string), resolved by `zones.lookup`. An unknown name is an error, with "did you mean". An unknown number is a warning, and the label becomes `Zone <id>`. |
| `subzone` | location | Landmark name (needs `zone` or `areaID`). Without `areaID` the name must contain a letter or digit `a-z 0-9`, because the id is built from its slug; otherwise give the `areaID`. |
| `areaID`, `instanceID` | location | Positive whole numbers |
| `label` | optional | Title-card fallback text. Default: the subzone name, the known area name, the canonical zone name, `Area <id>`, or `Instance <id>` (a warning for instances). |
| `spotify` | optional | `spotify:track:<id>`, an `https://open.spotify.com/[intl-xx/]track/<id>[?si=…]` link, or the bare id. Normalised to the **bare 22-character id**. Album, playlist and other non-track links are errors. |
| `youtube` | optional | The 11-character id, or a `watch?v=`, `youtu.be/`, `/embed/`, `/shorts/`, `/live/` or `/v/` link (www, m., music., youtube-nocookie). Normalised to the **11-character id**. Playlist links are errors. |
| `startMs`, `durationMs` | optional | Whole milliseconds, ≥ 0 and ≤ 2147483647 (numbers, integral floats, or digit strings) |
| `note` | optional | Text: why the song fits |
| `tags` | optional | As at the top level, e.g. `["fitting"]` / `["funny"]` |
| `disabled` | optional | `true`: the drop is **skipped without being checked**, so it may be an unfinished draft or a bare place. Anything other than `true`, `false` or `null` is an error. |
| `id` | optional | Must equal the derived id, or it is an error. It is only a cross-check. |
| `appleMusic` | ignored | Silently ignored (Apple Music is out of v1) |
| `file`, `inGameFile`, `volume` | ignored | Warning: bundled sets ship no audio and carry no volume |
| `_…`, `$…`, `//…` | comments | Keys starting with `_`, `$` or `//` are ignored silently (`"_comment"`, `"$schema"`) |
| anything else | warning | `unknown key "youtub" ignored (did you mean "youtube"?)` |

**Drop id and kind** (SPEC 3.1, same precedence as the companion):

| Pack keys | `id` | `kind` |
|---|---|---|
| `instanceID` | `inst:<instanceID>` | `instance` |
| `areaID` | `area:<areaID>` | `subzone` |
| `zone` + `subzone` | `sub:<uiMapID>:<slug>`, e.g. `sub:1429:lion-s-pride-inn` | `subzone` |
| `zone` | `zone:<uiMapID>` | `zone` |

For an `areaID` the companion knows (`zones.KNOWN_AREAS`), a missing `mapID` or `subzone` is filled
in from the area. A known area placed in a different zone than `zone` gives a warning.

**Errors and warnings.** Every problem in a pack is reported in one run, in the companion's format,
prefixed with the pack file:

```
error: packs/azeroth-mixtape.json: pack "Azeroth Mixtape" drops[3] (Goldshire): missing "artist" (text)
error: packs/azeroth-mixtape.json: pack "Azeroth Mixtape" drops[7] (Elwyn Forest): unknown zone 'Elwyn Forest' -- did you mean 'Elwynn Forest'?
warning: packs/azeroth-mixtape.json: pack "Azeroth Mixtape" drops[9] (Booty Bay): unknown key "youtub" ignored (did you mean "youtube"?)
FAILED packs/azeroth-mixtape.json: 2 error(s); no set file written for it
```

`drops[3]` counts from 0, like the JSON list. Other errors: invalid JSON (with line and column), a
file that is not UTF-8 (a UTF-8 BOM is fine), wrong types, unparseable links, negative or
fractional numbers, a duplicate drop id among the enabled drops, text with unpaired surrogate
escapes, and a set with no enabled drops. Warnings never fail a pack. A key given twice in one JSON
object is also a warning (the last one wins).

All text is trimmed of surrounding whitespace and otherwise kept exactly: UTF-8 as written, no
Unicode normalisation.

## Generated file

```lua
local ADDON, ns = ...
-- Generated by tools/gen_sets.py from packs/azeroth-unbound.json. Edit the pack, not this file.
ns.RegisterBuiltinSet({
  id = "azeroth-unbound", name = "Azeroth Unbound", author = "NeedleDropper", description = "...", tags = {}, version = 1, source = "packs/azeroth-unbound.json",
  drops = {
    { id = "area:150", kind = "subzone", mapID = 1437, areaID = 150, subzone = "Menethil Harbor", label = "Menethil Harbor", title = "Old Pine", artist = "Ben Howard", durationMs = 327000, startMs = 0, youtube = "Coj1Nb2DY_w", note = "..." },
    { id = "zone:1429", kind = "zone", mapID = 1429, label = "Elwynn Forest", title = "I'll Keep Coming", artist = "Low Roar", durationMs = 352000, startMs = 0, youtube = "mlM66FzjpgM", note = "..." },
  },
})
```

- Line 1 is the standard addon varargs line. Line 2 is the generated header; the generator uses it
  to recognise its own files. One header line, then one line per drop.
- Drops are sorted by `id` with a plain string sort (`area:1000` < `area:87`), like Map.lua numbering.
  Keys are always written in the order of the tables below.
- Strings are double-quoted Lua 5.1 literals. `\\`, `\"`, `\n`, `\r` and `\t` are escaped, other
  control characters become zero-padded `\ddd`, and all other characters stay raw UTF-8. Only
  Lua 5.1 escapes are used, and no long brackets.
- There are no timestamps and no machine-specific paths. `source` is repo-relative with `/`
  separators, or just the file name for a pack outside the repository. The same pack always gives
  byte-identical output.
- The file reads no globals and assigns none. Its only effect is the one call.

### Set table

| Key | Present | Type | Meaning |
|---|---|---|---|
| `id` | always | string | Set id (stable key for saved data), `[a-z0-9-]{1,40}` |
| `name` | always | string | Display name |
| `author` | always | string | May be `""` |
| `description` | always | string | May be `""`; may contain `\n` |
| `tags` | always | array of strings | May be empty `{}` |
| `version` | always | number | Format version of this table: `1` |
| `source` | always | string | Pack file it came from, e.g. `packs/azeroth-unbound.json` (informational) |
| `homepage` | if given | string | http(s) link |
| `drops` | always | array of drops | At least one, sorted by `id` |

### Drop table

| Key | Present | Type | Meaning |
|---|---|---|---|
| `id` | always | string | `zone:<uiMapID>` · `area:<areaID>` · `sub:<uiMapID>:<slug>` · `inst:<instanceID>` |
| `kind` | always | string | `"zone"` · `"subzone"` · `"instance"` |
| `mapID` | if known | number | uiMapID of the zone (absent for an instance without `zone`, or an unknown area without `zone`) |
| `areaID` | `area:` drops | number | Area id: localise the place with `C_Map.GetAreaInfo(areaID)` |
| `subzone` | if known | string | enUS landmark name (what `GetSubZoneText()` shows on an English client) |
| `instanceID` | `inst:` drops | number | 8th return of `GetInstanceInfo()` |
| `label` | always | string | enUS place name: the title-card **fallback** (the card prefers client-localised names) |
| `title`, `artist` | always | string | The song: also the "artist + title" search key when no `spotify` id is given |
| `durationMs` | always | number | Play length from `startMs`; `0` = unknown (use `settings.maxDropSec`) |
| `startMs` | always | number | Start offset into the song; `0` = from the beginning |
| `youtube` | if given | string | 11-character YouTube video id |
| `spotify` | if given | string | Bare 22-character Spotify track id (URI = `"spotify:track:" .. spotify`) |
| `tags` | if given | array of strings | e.g. `{ "fitting" }` |
| `note` | if given | string | Why it fits (journal / tooltip text) |

Optional keys are **absent** (nil), never `""` or `{}`. Nothing else is ever emitted: no `n`, no
`file`, no `volume`.

## Consumer contract: `ns.RegisterBuiltinSet`

The in-game Sets module must provide `ns.RegisterBuiltinSet(set)`:

- **Lives beside `Sets/`, not in it.** `addon/NeedleDropper/Sets/` holds generated set files only
  (`tests/test_setgen.py` enforces it), so put the module next to the other modules, for example
  `addon/NeedleDropper/Sets.lua`.
- **Defined before the set files load.** The generated files call it at load time, with no guard,
  so a missing definition is a loud load error rather than a silently missing set. In the TOC, list
  the file that defines it **before** the `Sets\*.lua` lines:

  ```
  Sets.lua                 # (example name) defines ns.RegisterBuiltinSet
  Sets\AzerothUnbound.lua
  Sets\AzerothMixtape.lua
  ```

- **Called once per set file, during file loading.** That is before `ADDON_LOADED`, so before
  SavedVariables exist. Store the table and do the per-character and per-account work (active set,
  user overrides keyed by set id + drop id) later, in `ADDON_LOADED` / `PLAYER_LOGIN`.
- **Check `set.version`.** Accept `1`; ignore (or report) anything newer instead of guessing.
- **Treat the table as read-only shared data.** Never mutate it, and never write it into
  SavedVariables (it reloads with the addon). User edits belong in SavedVariables as overrides or
  copies, keyed by `set.id` and drop `id`.
- **Handle the optional keys being nil** (see the tables above). For example, a drop with neither
  `spotify` nor `youtube` is played by searching for `artist` + `title`.
- **Bundled sets are identified by id.** A duplicate `id` (from a user set or a second registration)
  is the module's call; the generator guarantees bundled ids are unique among themselves.
- **Which set is the default** is not a header field. The bundled Azeroth Mixtape (SPEC-v1.2: the
  default soundtrack) marks itself with the set tag `"default"`. Read that tag, or agree on a
  header field (bump `version` if it changes the meaning of the table).

## Adding a bundled set

1. Write `packs/<id>.json` (give it an explicit `"id"`).
2. Run `python tools/gen_sets.py`. Fix every error it reports, then run it again until it prints
   `wrote addon/NeedleDropper/Sets/<CamelCaseId>.lua`.
3. Add `Sets\<CamelCaseId>.lua` to `addon/NeedleDropper/NeedleDropper.toc`, after the Sets module.
   The generator warns until you do.
4. Commit the pack and the generated file together. `python tools/gen_sets.py --check` (and
   `tests/test_setgen.py`) fail if they drift apart.

To change a bundled set, edit its pack and regenerate. Never edit `Sets/*.lua` by hand: the next
run overwrites it, and `--check` flags it immediately.
