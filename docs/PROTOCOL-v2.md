# NeedleDropper — Wire protocol v2 (song identity in-band)

Status: **implemented and contract-tested; the v1.2 addon emits only v2** (DESIGN-v1.2 §2). Supersedes
SPEC.md §3.4–§3.6 (beacon payload, chat line). The v1.2 companion still decodes v1 (`NDROP1`, the 10-byte
beacon) for old addons.

| Side | Module | Contents |
|---|---|---|
| Addon (Lua 5.1) | `addon/NeedleDropper/Protocol2.lua` → `ns.Protocol2` | frame encoder with the fit rule (+ decoder for self-tests), strip geometry, cell grid, chat and padding lines, link parsers, UTF-8 helpers, `HasFlag` |
| Addon | `Emit.lua` → `ns.BuildEvent`, `ns.Emit` | the event table for a map drop; seq, live flags, history; hands events to both transports |
| Addon | `Beacon.lua` → `ns.Beacon` | the pixel strip: geometry choice, clearance guard, hold / queue rules (§4.6) |
| Addon | `ChatLink.lua` → `ns.ChatLink` | `NDROP2` lines, padding, chat filter, deferral (§3) |
| Companion (Python ≥ 3.10, stdlib) | `companion/needledropper/protocol2.py` | `Frame` / `TLV`, `build_frame`, `frame_from_event`, `encode_frame`, `decode_frame`, geometry, cells, chat and padding lines, link parsers |
| Companion | `companion/needledropper/transport/beacon2.py` | `locate_and_decode` / `scan`: finds and decodes the strip in a captured BGRA buffer; capture-region constants |
| Tests | `tests/test_protocol2_contract.py`, `tests/test_beacon2_decode.py`, `tests/test_v12_beacon_*.py` | Lua ↔ Python byte-exact contract (fit rule at every budget, geometry, DESIGN §2.3 measured sizes); pixel decode matrix; addon emit → textures → BGRA → decode, hold timings, clearance guard, chat link |

## 1. Why v2

In the in-game-first design the soundtracks live in SavedVariables and bundled Lua data files. The
companion has no soundtrack files, so it can't resolve a `(map hash, n)` pair. Instead, the addon sends the
**song identity** itself: Spotify track id, YouTube id, artist/title for search, start, play length, volume,
and the place label. Editing a drop in game therefore takes effect immediately, with no `/reload` and no
companion-side files. Both transports carry the same event: the pixel beacon draws its frame, and the
chat-log line base64url-encodes it.

```
 map drop ─ns.BuildEvent─▶ event table ─EncodeFrame(ev, rowCapacity)─▶ fitted frame ─EncodeCellsFromBytes─▶ beacon strip
                                  └──────EncodeFrame(ev) (≤ 262 B)───▶ full frame ───FormatChatLineBytes───▶ "NDROP2 <base64url>"
 companion: beacon2.locate_and_decode(buf) ─┐
            protocol2.parse_chat_line(line) ─┴─▶ protocol2.Frame (validated) ─▶ engine (dedupe by Frame.key)
```

## 2. Frame

```
offset  size  field
0       1     0x4E 'N'                       magic
1       1     0x20 | type                    version 2 in the high nibble, event type in the low nibble
2       1     seq                            uint8, +1 (mod 256) per emitted event
3       1     flags                          v1 bits: 0x01 IN_COMBAT 0x02 IN_INSTANCE 0x04 ON_TAXI 0x08 DEAD
                                             0x10 LOCAL_PLAYBACK 0x20 DROP_ACTIVE 0x40 TEST, 0x80 reserved
4       1     bodyLen                        bytes of TLV body, 0..255
5       n     body                           TLVs: tag (uint8), len (uint8), value (len bytes)
5+n     2     Fletcher-16 of bytes 0..4+n    s2 first, then s1 (SPEC §3.4 algorithm)
```

* Frame size is `7 + bodyLen`: 7 (empty body) to **262** bytes.
* Event types are the v1 ones: `0 NONE`, `1 HELLO`, `2 ARM`, `3 FIRE`, `4 CANCEL`, `5 PING`. Types 6–15 are
  rejected by v2 decoders.
* **Dedupe key**, shared by both transports (CONTRACTS-v1.2 §3.1): `(seq, type, placeKey or 0, flags)`
  (`Frame.key`). The beacon's fitted frame and the chat line's fuller frame of one event share it, and so do
  the beacon's full ARM and the placeKey-only ARM it shows afterwards (§4.6). The pixel transport should still
  require two identical consecutive reads of the whole frame, and treat the first valid frame after start as a
  baseline, as in v1.

### 2.1 Body TLVs

Encoders emit tags in **strictly ascending** order and omit absent fields. Decoders reject a frame whose body
breaks the structure, and skip tags they don't know.

| Tag | Field | Value | Encoder normalisation (Lua `EncodeFrame` ≡ Python `build_frame`) |
|---|---|---|---|
| `0x01` | placeKey | uint16 BE | Fletcher-16 of the **full, unsanitised** drop-id string (UTF-8 bytes); `placeKey` in the event overrides it (`% 65536`) |
| `0x02` | dropId | ASCII `[A-Za-z0-9:_.-]`, 1–40 B | **byte-wise** sanitising: every UTF-8 byte outside the set becomes `_` (`é` → `__`), then cut to 40 bytes |
| `0x03` | spotifyTrackId | exactly 22 base62 chars | from `spotify:track:ID`, an `open.spotify.com` track link, or a bare id (§2.3) |
| `0x04` | youtubeId | exactly 11 chars `[A-Za-z0-9_-]` | from a bare id, a youtu.be / youtube.com link (§2.3) |
| `0x05` | query | UTF-8 `artist 0x1F title`, 1–120 B | text cleaning (§2.2). If artist + title exceed 119 B, the title keeps ≥ 60 B and the artist ≥ 59 B, each cut on a UTF-8 boundary, so the separator always survives. Omitted when both are empty |
| `0x06` | startDs | uint16 BE, 1/10 s | `startDs` (rounded) wins over `startMs` → `floor((ms + 50) / 100)`; clamped 0–65535; **0 is omitted** (absent = 0) |
| `0x07` | lengthDs | uint16 BE, 1/10 s | the same, from `lengthDs` / `lengthMs`. Absent or 0 means the whole song |
| `0x08` | volume | uint8 0–100 | `floor(v + 0.5)` clamped 0–100; absent means the companion default |
| `0x09` | label | UTF-8, 1–48 B | text cleaning, cut to 48 B on a UTF-8 boundary (place label for the player card) |
| `0x0A` | setId | ASCII `[A-Za-z0-9:_.-]`, 1–32 B | as dropId, cut to 32 B |
| `0x0B`–`0xFF` | — | any | reserved for future fields. v2 decoders skip them (kept in `Frame.extra`) |
| `0x00` | — | — | invalid (rejects zero-filled garbage) |

* **Numbers** must be numbers (Lua `type(v) == "number"`, Python int/float but not bool). Anything else,
  including NaN and ±inf, counts as absent. `type`, `seq` and `flags` are floored and wrapped mod 16/256/256.
* **Fields per type.**
  * ARM and FIRE carry every field.
  * CANCEL carries `placeKey` and `dropId` only.
  * HELLO, PING and NONE carry an empty body; extra fields are dropped by the encoder.
* **Fit rule** (DESIGN-v1.2 §2.3). `EncodeFrame(ev, maxBytes, prefer)` ≡ `build_frame(..., max_bytes, prefer)`
  keeps the whole frame within a byte budget: 262 by default (the chat line; a body of at most 255 bytes) and
  the row capacity for the beacon (§4.3). The budget is floored and clamped to 7..262; anything that is not a
  number means 262. While the frame is still over budget, the encoder removes, in this order:
  1. `setId`;
  2. `label`;
  3. `query`, if a Spotify or YouTube id is present (after link parsing);
  4. `dropId` (`placeKey` stays and still identifies the place);
  5. the tail of `query` (a search-only drop: just enough bytes, cut on a UTF-8 boundary with `Utf8Truncate`;
     the whole query when not even one byte fits);
  6. `volume`;
  7. the provider id that is not preferred: `prefer` is `"spotify"` or `"youtube"` (ASCII case-insensitive;
     anything else, and the default, is YouTube; the event's own `prefer` field is the default). The addon sets
     it to Spotify when the NeedleDropper app reports Spotify connected (`ns.PreferredProvider`).

  Never removed: `placeKey`, `startDs`, `lengthDs`, the flags, and the last provider id. So the result can still
  exceed a budget below that minimum (at most 43 bytes: placeKey + start + length + a Spotify id); the beacon
  then draws an extra row (§4.6). With every field at its cap the body would be 299 bytes; the 262-byte budget
  removes setId and the label and leaves **222 bytes**. Frames of up to 262 bytes still occur (e.g. a 38-byte
  label without a set id), so 262 stays the decoders' bound. This order replaced the v1.1 rule (drop setId,
  then *shrink* label, query and dropId to 255 body bytes); both languages changed together.

### 2.2 Text cleaning (`Utf8Clean` ≡ `utf8_clean_bytes`)

1. Invalid UTF-8 bytes become `?`, one per byte. This covers bad lead or continuation bytes, overlong forms,
   UTF-16 surrogates (`ED A0..BF`) and code points above U+10FFFF (RFC 3629 table).
2. C0 controls `0x00–0x1F` and DEL `0x7F` become a space, so `0x1F` is unambiguous as the query separator.
3. Leading and trailing spaces are trimmed.

Truncation (`Utf8Truncate` ≡ `utf8_truncate`) cuts at the largest length ≤ max that doesn't end inside a
sequence. It is code-point safe, but grapheme clusters such as ZWJ emoji may still be split. Python callers pass
`str`; lone surrogates are encoded with `surrogatepass` and then repaired like the Lua side would repair those
bytes.

### 2.3 Link parsers (`SpotifyIdFromLink` / `YouTubeIdFromLink` ≡ `spotify_id_from_link` / `youtube_id_from_link`)

Both operate on bytes, with ASCII-only case folding and Lua `%s` whitespace trimming, so the two languages
behave identically. Anything not listed returns `nil` / `None`.

* **Spotify**
  * `spotify:track:<22 base62>` (prefix case-insensitive).
  * `[http[s]://]open.spotify.com/[intl-xx[-yy]/][embed/]track/<id>[/][?…][#…]`, also `play.spotify.com`;
    the host is case-insensitive.
  * A bare 22-char base62 id.
  * Rejected: albums, playlists, `spotify.link` short links (they would need a network round trip), extra
    path segments, wrong lengths.
* **YouTube**
  * A bare 11-char id.
  * `youtu.be/<id>` (also `www.youtu.be`).
  * `youtube.com` (also `www.`, `m.`, `music.`), and `youtube-nocookie.com` (also `www.`), with any of these
    paths:
    * `/watch?…v=<id>…` (the `v` parameter anywhere in the query, `/watch/` accepted).
    * `/shorts/<id>`, `/embed/<id>`, `/live/<id>`, `/v/<id>`.
  * The id must be exactly 11 chars, followed by nothing or one of `/ ? & #`.

### 2.4 Decoder validation (`decode_frame` ≡ Lua `DecodeFrame`)

A decoder returns `None` / `nil` (it never raises) unless all of these hold:

* The magic byte is 0x4E and the version is 2.
* The type is ≤ 5.
* The frame length is exactly `7 + bodyLen`.
* The Fletcher-16 checksum matches.
* The TLV walk fits the body exactly, every tag is non-zero, and tags are strictly ascending (no duplicates).
* Known tags have valid lengths and character sets: placeKey/start/length 2 B, volume 1 B ≤ 100, ids per the
  table, query 1–120 B, label 1–48 B.

Text is decoded leniently: Python uses `errors="replace"`, and Lua keeps the raw bytes.

## 3. Chat-log line

```
NDROP2 <base64url(frame), RFC 4648 §5 alphabet, no padding>
NDPAD <seq> ......................................   (exactly 900 characters, after FIRE / CANCEL / PING)
```

* The addon (`ChatLink.lua`) sends the line with `SendSystemMessage`, only when `settings.chatlog` is on (or a
  self-test forces one line). The line carries the event's **full** frame (`FormatChatLine(ev)`, budget 262):
  the same bytes as the beacon whenever the beacon did not have to fit the frame tighter; otherwise the chat line
  has more fields, and the shared dedupe key (§2) makes the companion treat both as one event.
* After FIRE, CANCEL and PING come `PadLineCount(chatlogPadBytes)` padding lines `PadLine(seq)` (v1's SPEC §3.6
  rule, now in `ns.Protocol2`), which push the client's ~48 KiB log buffer to disk. 50,000 bytes = 56 lines.
* A `CHAT_MSG_SYSTEM` message filter (`ChatFrameUtil.AddMessageEventFilter`) hides every line starting
  `NDROP1 `, `NDROP2 ` or `NDPAD `, and never looks inside a secret value.
* **Deferral.** Lines wait in a queue while `C_ChatInfo.InChatMessagingLockdown()` or
  `C_RestrictedActions.IsAddOnRestrictionActive(Enum.AddOnRestrictionType.Chat)` (forever 1.60.1: Chat = 5) is
  true (a secret answer counts as true); the queue is retried every second. When it is sent, a **FIRE line that
  waited more than 10 s is dropped with its padding** (its moment has passed; the beacon delivered it if it
  could). Other lines are kept however long they wait.
* Chat logging (`LoggingChat(true)`) is turned on only when `settings.chatlog` is on (at login and when the
  setting changes) or by the chat-log self-test, and turned off again only if the addon turned it on.
* `parse_chat_line(line, strict=True)` accepts a record only when it is the whole system message: the client's
  timestamp, whitespace, then `NDROP2 <token>`, then optional trailing whitespace to the end of the line.
  The line may also simply start with `NDROP2 `.
  * Chat typed by other players always carries a `[Name]` or channel prefix, so it can never fire a drop.
  * `strict=False` finds a record anywhere in the line.
* Base64url decoding is strict: alphabet, length (`len % 4 != 1`) and canonical trailing bits. The frame
  checksum does the rest.

## 4. Pixel beacon v2

### 4.1 Layout

```
row 0:  M G M G | G0 G1 G2 | d0 d1 d2 d3 … (frame bits, whitened)
row 1:  d… d… d… d… …                                   ← cols cells per row, row-major
…
row R-1: … last frame bits | pad (zero bits, whitened) to the end of the row
```

* **Cells.** Each cell is `cellPx × cellPx` physical pixels. Its colour is 3 bits, read MSB-first in the order
  R, G, B; each channel is 0 or 255 (the addon passes 0/1 to `SetColorTexture`). A channel reads as on when it
  is ≥ the threshold (default 128).
* **Header**: `MAGENTA(1,0,1) GREEN(0,1,0) MAGENTA GREEN`.
* **Geometry, three cells, never whitened:**
  * `G0 = cols / 16`: 1..7, i.e. 16..112 cells per row.
  * `G1 = (rows − 1) div 8`.
  * `G2 = (rows − 1) mod 8`.

  This gives rows 1..64.
* **Data.** Frame bits run MSB-first from the first byte, 3 per cell, starting at cell 7. Zero bits pad the
  stream to the end of the last row, so the strip is always a full `cols × rows` rectangle. Every data and pad
  bit is XOR-ed with the whitening keystream (§4.2).
* `rows = ceil((7 + ceil(8·frameLen / 3)) / cols)` (`RowsFor` ≡ `rows_for`).
* **Rows always read left-to-right, top-to-bottom, whatever the corner.** The header is always at the left end
  of the top row. `corner` only says which corner of the client area the strip's own matching corner is
  anchored to. Cell (r, c), 0-based, is drawn at `x = c·cellPx, y = r·cellPx` from the strip's top-left.
* **Known prefix.** Cells 7..10 always show `WHITE BLACK RED MAGENTA` (`wkrm`): the whitened magic byte and
  version nibble. All four differ from their neighbours. Decoders use them to confirm a header before doing any
  expensive work, and to pin the lattice.

Fixed-width strips (`{cellsPerRow, cellPx, corner}`: cols 16..112 in steps of 16, cellPx 1..8; v1.1's
encoder default was 64 × 3 px) are still part of the protocol, and decoders need no configuration for any
width. The v1.2 addon draws **adaptive** strips (§4.3).

### 4.2 Whitening, pad, and why

The keystream is the Park–Miller "minimal standard" LCG:

```
x0 = 1313100338 (0x4E445232 "NDR2");  x[i+1] = 16807 · x[i] mod (2^31 − 1);  bit i = 1 if x[i+1] ≥ 2^30 else 0
```

Keystream bit *i* is XOR-ed with data bit *i* (bit 0 is the MSB of frame byte 0, in cell 7's R channel). Lua 5.1
computes this exactly, because the products stay below 2^53. The first bits are
`101011000111100101110111110001011100011001111011…`.

Whitening plus a strict pad check is the "terminator" scheme:

* **Fletcher-16 mod 255 cannot tell 0x00 from 0xFF.** A white cover over zero bytes, or a black one over 0xFF
  bytes, leaves the checksum unchanged. Unwhitened, a tooltip over `startDs = 0` would decode as
  `startDs = 6553.5 s`. Whitening turns any uniform cover into pseudo-random bit errors, which the checksum and
  the TLV structure catch (`test_uniform_covers_fail_the_checks`).
* **Every pad bit must de-whiten to 0, and `rows` must equal what the frame length needs.** A strip that is
  partly covered, cut off, or stale (an old longer frame's tail) fails even when the covered cells are only
  padding. A strip must be fully visible to decode, so it never decodes as garbage.
* **Dense transitions.** Whitened cells change colour at about 7/8 of boundaries in every row. That is what lets
  the decoder fit sub-pixel lattices (§4.4).

The checks a decoder applies are: header cells, geometry cells (cols matches the sampled grid, rows matches the
frame length), pad bits zero, then everything in §2.4.

### 4.3 The v1.2 beacon geometry (DESIGN-v1.2 §2.3)

* **Cell size**: 3 physical px by default; the `beaconCellPx` setting allows 2..4.
* **Height cap**: rows × cellPx ≤ 9 px, i.e. `RowsCap(px) = floor(9 / px)` ≡ `rows_cap`: 2 px → 4 rows,
  3 px → 3 rows, 4 px → 2 rows. The clearance guard (§4.6) may lower it at runtime.
* **Budget**: a strip of `maxRows` rows of 112 cells holds `Capacity(maxRows) = floor((maxRows·112 − 7)·3/8)`
  frame bytes ≡ `capacity` (at most 262): one row 39 B, 2 px (4 rows) **165 B**, 3 px (3 rows) **123 B**,
  4 px (2 rows) **81 B**. The event is fitted to it with the fit rule (§2.1). `rows ≤ maxRows` holds exactly
  when `frameLen ≤ Capacity(maxRows)`.
* **Width, per frame** (`AdaptiveLayout(frameLen)` ≡ `adaptive_layout`): with `cells = 7 + ceil(8·frameLen/3)`,
  `rows = ceil(cells / 112)` and `cols = 16 · ceil(ceil(cells / rows) / 16)` — the narrowest multiple of 16 that
  holds the frame in that many rows. `RowsFor(frameLen, cols)` is always exactly `rows`, so a decoder reads an
  adaptive strip like any fixed-width one.
* `EncodeCells(ev, {cellPx, maxRows, corner})` ≡ `encode_event_cells(ev, Geometry(cell_px=, max_rows=))`:
  fit to `Capacity(maxRows)`, then the adaptive layout. `maxRows` is floored into `1..RowsCap(cellPx)`. When the
  fitted frame still needs more rows than `maxRows` (§2.1's irreducible minimum), the grid has the rows it needs
  and `overflow` is set (`CellGrid.overflow`); it is complete and decodes normally.

Measured with both encoders (`tests/test_protocol2_contract.py::test_measured_size_table`):

| Frame | Bytes | Rows at 112 cols | Strip at 3 px |
|---|---|---|---|
| HELLO | 7 | 1 | 96 × 3 |
| CANCEL `zone:1429` | 22 | 1 | 240 × 3 |
| FIRE, bundled Mixtape drop (Durotar: YouTube + query + length) | 60 | 2 | 288 × 6 |
| … same plus label and setId | 86 | 3 | 240 × 9 |
| FIRE Spotify + YouTube + query + start + length + volume (`area:87`) | 95 | 3 | 288 × 9 |
| FIRE user drop with every field (`sub:1429:lion-s-pride-inn`) | 141 | 4, over the cap | fitted to 113 B → 336 × 9 |
| Worst case (all fields at their caps) | 222 (chat; 299-byte body before the fit) | 6 | fitted to 101 B ≤ 123 B |

At 2 px the 141-byte drop keeps every field (4 rows, 192 × 8 px); at 4 px it is fitted to 59 B (96 × 2 cells,
384 × 8 px).

### 4.4 Decoder (`beacon2.locate_and_decode(buf, width, height, stride, corner="TOPLEFT", *, threshold=128, max_offset=48)`)

**Input.** `buf` is a top-down BGRA buffer (GDI 32-bpp DIB order), `height` rows of `width` pixels, `stride`
bytes apart, captured at `corner` of the client area.

**Result.** `Beacon2Result(frame, cell_px, x, y, rows, cols)`, or `None`. `scan()` returns a `ScanReport` with
`reason`, which is one of `ok`, `no header`, `payload invalid`, `strip truncated` (plus `needed_width` /
`needed_height`), `wrong corner`, `bad buffer`, `bad corner`, `decoder error: …`. The report also carries
`header`, a `HeaderHit` for a confirmed header: header cells, geometry and known prefix all read. `x, y` is the
fitted origin; for a one-row strip at the buffer's top edge `y` can be off by one pixel (no row boundary above
it), which never affects the decode.

**The corner constraint.** The strip's own corner must be within `max_offset` px of the buffer's same corner.
For TOPRIGHT, for example, that is the strip's right edge and top edge.

**Algorithm.** This applies v1's techniques (`transport/pixel.py`) to many rows:

1. **Classify and find header candidates.**
   * Every pixel is classified to a 3-bit code at C speed (`bytes.translate` plus big-int addition).
   * A regex finds header runs (`m+ g+ m+ g+`, up to 2 blended pixels between runs) on the scanned rows. For
     top corners those are the rows down to `max_offset` + 24; for bottom corners, all rows.
   * Matches are clustered over consecutive pixel rows. A cluster's rows are the pixel rows of strip row 0.
   * Header boundaries are per-row medians. Run 1 is distrusted when the background next to it is magenta.
2. **Fit the column lattice** by progressive least squares, giving a float pitch. The fit starts from the header
   boundaries and the colour runs of the known prefix, then adds transitions of ever more cells.
   * The column and row lattices are separable: every pixel row inside the strip shows the same column
     boundaries. So transitions are pooled over the centre line of every strip row; rows are fitted the same way
     from all columns.
3. **Read and confirm.** Read the geometry cells and confirm with the known prefix.
4. **Sample and validate.** Sample each cell at the middle pixel of its span, then de-whiten and validate
   (§4.2). Steps 1–4 are the cheap path. They handle integer, scaled, blended and noisy cells in about 1 ms.
5. **Fallbacks.** These run only when nothing decoded cheaply, and each has a bounded budget per frame:
   * **Calibrated thresholds**: per-channel levels measured on the header (HDR, tinted or dim captures).
   * **Lattice perturbations.**
   * **The exact solver**, for crisp 1–3 px cells. For each transition at pixel *t* that starts cell *k*, the
     GPU rasterisation rule gives `t − 0.5 < x0 + k·pitch ≤ t + 0.5`. Intersecting these constraints clips a
     convex polygon in (x0, pitch) space. Transitions are assigned to boundaries best-first, preferring the
     fewest boundaries left without a transition, while the known colours of row 0 are enforced. For a one-row
     strip the pad colours of a guessed frame length are enforced too. The cheapest few lattices are each
     checksum-validated.

**Measured performance (pure Python, this machine).**

| Case | Time |
|---|---|
| 320 × 48 region with a maximal 64-column frame | ≈ 1 ms |
| Garbage frame | ≈ 0.3–0.6 ms |
| 1-px magenta/green checkerboard (worst header-candidate case) | ≈ 16 ms |
| Exact small-cell fallbacks (1–3 px crisp fractional pitch) | typically 2–15 ms, ≤ ~35 ms for the rare failing 1-row case |

The work is bounded deterministically by per-frame node and attempt budgets. A 300 ms wall-clock backstop only
guards against a pathologically slow machine; it never decides an outcome in normal use. Tests assert:

* < 30 ms for the default region, garbage frames at every corner, and fractional bottom-corner strips;
* < 60 ms for 1.25 px cells, which take the exact solver.

**Robustness (tests).** All of the following decode exactly:

* All 4 corners × 1–8 px cells × 32/48/64 cells per row × frames of 7..262 bytes.
* Adaptive v1.2 strips at 2/3/4 px in the 512 × 48 region at 100/125/150 % capture scaling, including the
  two-row overflow strip, and strips rendered from the addon's own textures (`tests/test_v12_beacon_*.py`).
* Offsets up to `max_offset`.
* Noise ±25.
* Tone shifts (HDR-like, washed out, dim, lifted blacks).
* Fractional pitch (cell × 1.125/1.25/1.333/1.5/1.75).
* Whole-frame nearest-neighbour resampling.
* Box-filtered (blended) captures.

Random sweeps over every corner, width, size, offset and noise level (more than 5,000 cases while
developing) produced **no wrong frame ever**. Partial occlusion, tearing between two frames, cut-off rows, a
v1 strip and garbage all return `None`. With pitches of 1.2–9 px, 0 of 600 random strips failed to decode.

**Known limits.**

* Cells must be ≥ 1 px.
* Crisp fractional pitches of **1.0–1.2 px** are occasionally ambiguous: a boundary without a transition can be
  read as a 2 px cell of a slightly different pitch. This is mostly an issue for one-row strips, e.g. a HELLO
  drawn with 1-px cells and captured at 101–104 % scale. About 3 % of such strips then fail cleanly (no frame)
  rather than decode wrongly; the next capture usually succeeds. Integer 1-px cells captured 1:1 always decode.
  The addon never draws cells below 2 px.
* Buffers are limited to 4096 × 2048: capture the corner region, not the whole window.

### 4.5 Capture assumptions (companion transport)

* The strip sits at the client area's **top-left pixel (0, 0)** (`beaconCorner` can move it to another corner;
  capture that corner then).
* The capture region is the top-left **512 × 48 physical pixels** of the client area
  (`beacon2.CAPTURE_WIDTH`, `CAPTURE_HEIGHT`): the widest strip is 112 × 4 px = 448 px (`MAX_STRIP_WIDTH`) and
  the tallest 9 px (`MAX_STRIP_HEIGHT`). A capture magnified by display scaling must grow the region by the same
  factor (`capture_size(scale)`: 640 × 60 at 125 %, 768 × 72 at 150 %).
* Nothing about the geometry is configured on the companion: header and geometry cells say everything, and the
  pitch is fitted (the companion's old "match the addon's cell size" setting is gone).
* Acceptance: two identical valid reads (the v1 rule). Song frames stay at least 3 s (12 captures at 4 Hz),
  CANCEL / PING at least 1 s (4 captures).

### 4.6 The addon's strip (`Beacon.lua`)

**Frame.** `CreateFrame("Frame", "NeedleDropperBeacon", nil)`: no parent, so Alt+Z can't hide it;
`SetPoint(corner, WorldFrame, corner, 0, 0)` with `corner` from the advanced `beaconCorner` setting (default
TOPLEFT; invalid values mean TOPLEFT); strata `TOOLTIP`, frame level 9000 (both fixed), `EnableMouse(false)`,
scale 1. Cells are a texture pool created lazily, at most 448 (112 × 4); each texture gets
`SetSnapToPixelGrid(true)` and `SetTexelSnappingBias(0)`. A cell is `px · PixelUtil.GetPixelToUIUnitFactor() /
frame:GetEffectiveScale()` UI units, so it is exactly `px` physical pixels at any UI scale or screen size.
Unused pool textures are hidden, so no longer frame's tail ever stays visible.

**What is on screen.**

* **Transient frames**, first in first out, each held at least: **FIRE 3 s**, **CANCEL 1 s**, **PING 1 s**.
  A newer event never shortens a hold and never skips a queued FIRE or CANCEL (the queue holds 64; when full,
  the oldest PING is dropped first).
* Otherwise the **steady state** of the newest event:
  * **HELLO** (and NONE): itself. The idle strip is 96 × 3 px at 3 px cells.
  * **ARM**: the full song for **3 s of drawn time** (time spent behind a transient doesn't count), then an ARM
    carrying only the placeKey, with the ARM's seq and flags, while armed.
  * after a **FIRE**: HELLO with DROP_ACTIVE.
  * after a **CANCEL**: HELLO.
  * PING doesn't change it.

  These steady frames are re-renderings, never emitted: they reuse their event's seq, never bump
  `ns.db.lastSeq`, never enter `ns.Events` and never reach the chat log. In the two synthesized HELLOs, TEST is
  cleared, and LOCAL_PLAYBACK unless DROP_ACTIVE is set (a CANCEL of a pending drop while another plays keeps
  DROP_ACTIVE, so the companion keeps playing). The placeKey-only ARM has the full ARM's dedupe key: the
  companion sees a repeat and keeps the song it cached.

**Geometry choice and clearance guard** (`Beacon.Choose(ev, px, gap, corner)`, pure):

* **Measuring.** On `PLAYER_ENTERING_WORLD`, `UI_SCALE_CHANGED`, `DISPLAY_SIZE_CHANGED` and EventRegistry
  `EditMode.Exit` (and once more 1 s later), for the TOPLEFT corner only:
  `gap = physicalHeight − region:GetTop() · region:GetEffectiveScale() / PixelUtil.GetPixelToUIUnitFactor()`,
  the smallest over `PlayerFrame.PlayerFrameContainer.FrameTexture` and
  `TargetFrame.TargetFrameContainer.FrameTexture` (each falling back to its frame when the texture is missing,
  hidden or unreadable), counting only regions that are visible and whose left edge is within 448 px. Every read
  is pcall-guarded; `GetTop` may return nothing or a secret value (then the region doesn't count). No region
  measured means no limit beyond the 9 px cap.
* **Rows cap**: `capRows(px) = min(RowsCap(px), floor((gap − 2) / px))` (2 px of margin above the art).
* **Choice**, per frame:
  1. the **nominal frame** — fitted to `Capacity(RowsCap(px))` at the configured cell size — if its rows ≤
     `capRows(px)`: drawn at `px`. With the default UI (gaps of 11.7–32.9 px, DESIGN §2.3) this is always the
     case;
  2. else the same nominal frame with **2 px cells**, if its rows ≤ `capRows(2)`;
  3. else the frame fitted to `Capacity(max(1, capRows(2)))` at 2 px — at least one row, because the beacon must
     exist;
  4. if even that needs more rows (the irreducible minimum, §2.1), it is drawn with the rows it needs.
* **Warnings** for `/needle selftest beacon` (`Beacon.Status().warnings`): when not even one 2 px row fits
  (`gap < 4`): "the beacon overlaps your player frame: move the frame in Edit Mode or set beaconCorner"; when a
  frame overflowed its cap (step 4), the same advice with the row count.

**Status** (`Beacon.Status()`): `enabled, shown, cellPx` (drawn), `settingCellPx, rows, cols, widthPx,
heightPx, size` ("96x3"), `maxRows` (cap at the configured size), `gap, corner, measuredFrom, lastFrameBytes,
lastFrameType, lastFrameSeq, variant, step, overflow, current` (the drawn event), `queued, dropped, warnings`.

Settings read by the beacon: `beacon` (show / hide; frames keep rendering while hidden), `beaconCellPx` (rounded
and clamped to 2..4; default 3), `beaconCorner` (advanced). `ForceShow(sec)` shows the strip for the self-test
even when `beacon` is off.

## 5. Events from the addon (`Emit.lua`)

`ns.BuildEvent(evType, drop, extraFlags, opts)` builds the event table from a map drop (DESIGN-v1.2 §2.2):

| Drop field | Event field → TLV |
|---|---|
| `id` | `dropId`, and `placeKey = PlaceKey(id)` |
| `spotify` (bare id) | `spotify` |
| `youtube` | `youtube` |
| `artist`, `title` | `artist`, `title` → `query` |
| `startMs` (> 0) | `startMs` → `startDs` |
| `durationMs` (> 0) | `lengthMs` → `lengthDs` (absent = the whole song) |
| `volume` | `volume` |
| localised place name | `label`: zone → `C_Map.GetMapInfo(mapID).name`; landmark → `ns.AreaName(areaID)`, else the live subzone text when it names this landmark; instance → the instance name while inside it; else the drop's `label`, else its `subzone` |
| soundtrack id (`setId`) | `setId` |
| — | `prefer` = `ns.PreferredProvider()` (not sent; §2.1 step 7) |

HELLO, PING and NONE carry no drop fields; CANCEL only `dropId` / `placeKey`; the test drop (id `test`) its label
but no song, so nothing plays. Character names, chat and realm never enter a frame.

`ns.Emit(evType, drop|nil, extraFlags, opts)` (v1's signature) bumps `seq` mod 256 and persists it in
`ns.db.lastSeq`, sets the live flags (`ns.ComputeFlags`: IN_COMBAT, IN_INSTANCE, ON_TAXI, DEAD from the unit
APIs; DROP_ACTIVE, LOCAL_PLAYBACK, TEST from `ns.Director.active`; plus the known bits of `extraFlags`; secret
answers count as no), stamps `t = GetTime()`, keeps `ns.LastEvent` and the last 200 in `ns.Events`, and calls
`ns.Beacon.Show(ev)` and `ns.ChatLink.Send(ev, {noPad, force = opts.forceChat})`; an error in one transport is
reported and doesn't stop the other. It returns the event.

## 6. API reference

### 6.1 Lua (`ns.Protocol2`, pure functions, never error in game)

| Function | Returns |
|---|---|
| `EncodeFrame(ev [, maxBytes [, prefer]])` | byte array (1-based numbers 0..255), fitted to `maxBytes` (default 262) by the fit rule. The `ev` fields are listed at the top of the file and in §2.1 |
| `DecodeFrame(bytes)` | event table (`type, seq, flags, placeKey, dropId, spotify, youtube, query, artist, title, startDs, lengthDs, volume, label, setId, unknown`) or `nil` |
| `EncodeCells(ev, geom)` / `EncodeCellsFromBytes(bytes, geom)` | `{rows, cols, cellPx, corner, widthPx, heightPx, frameLen, maxRows, overflow, cells = {{r,g,b}, …}}` with channels 0/1 and `rows*cols` cells row-major. With `geom.maxRows` adaptive (§4.3); `nil` only if more than 64 rows would be needed (impossible for valid frames) |
| `RowsCap(cellPx)`, `Capacity(rows)`, `AdaptiveLayout(frameLen)` | the v1.2 geometry (§4.3) |
| `NormalizeGeometry(geom)`, `RowsFor(frameLen, cols)`, `CellPosition(i, cols)` | geometry helpers |
| `FormatChatLine(ev)` / `FormatChatLineBytes(bytes)` | `"NDROP2 <base64url>"` |
| `PadLine(seq [, len])`, `PadLineCount(padBytes [, len])` | `"NDPAD <seq> ...."` (900 chars) and the line count for a padding size |
| `HasFlag(flags, f)` | whether bit `f` is set (non-numbers → false) |
| `Base64Url(bytes)` / `Base64UrlDecode(s)` | string / byte array or `nil` |
| `Fletcher16Bytes(bytes [, n])`, `Fletcher16String(s)`, `PlaceKey(dropId)` | checksums |
| `Utf8Clean(s)`, `Utf8Truncate(s, maxBytes)`, `SanitizeId(s, maxLen)`, `BuildQuery(artist, title)` | text helpers |
| `SpotifyIdFromLink(text)`, `YouTubeIdFromLink(text)` | id or `nil` |
| `Keystream(n)` | the first `n` whitening bits (self-tests) |
| constants | `MAGIC, VERSION, CHAT_PREFIX, EV, EV_NAME, FLAG, TAG, MAX_*, HEADER, LEAD_CELLS, MIN/MAX_COLS, MAX_ROWS, MAX_STRIP_PX, CORNERS, DEFAULT_GEOMETRY, PAD_PREFIX, PAD_LINE_LEN, WHITEN_*` |

`ns.Beacon`: `Show(ev)`, `Layout()`, `Status()`, `ForceShow(sec)`, `Choose(ev, px, gap, corner)`,
`MeasureClearance()`, `RowsCapFor(px, gap)`, `Warnings()`, `SettingCellPx()`, `Corner()`, `PixelFactor()`,
`CellSize(px)`. `ns.ChatLink`: `Send(ev, opts)`, `SendLines(lines)`, `Flush()`, `Filter(...)`,
`InstallFilter()`, `EnsureLogging()`, `StopLogging()`, `IsLogging()`, `Restricted()`, `PadCount(bytes)`,
`Status()`. Emit: `ns.BuildEvent`, `ns.Emit`, `ns.ComputeFlags`, `ns.PreferredProvider`, `ns.PlaceLabel`,
`ns.Events`, `ns.LastEvent`.

### 6.2 Python (`needledropper.protocol2`)

| Function | Notes |
|---|---|
| `build_frame(type, *, seq, flags, drop_id, place_key, spotify, youtube, artist, title, start_ms, start_ds, length_ms, length_ds, volume, label, set_id, max_bytes, prefer)` | normalises and fits exactly like `EncodeFrame` |
| `frame_from_event(ev, *, max_bytes=None, prefer=None)` | the same, from a camelCase dict (its `prefer` is the default) |
| `encode_frame(frame)` | strict: raises `ValueError` |
| `decode_frame(data)` | `Frame` or `None` |
| `Frame` | `.artist`, `.title`, `.search_text`, `.start_ms`, `.length_ms`, `.spotify` / `.spotify_id`, `.youtube` / `.youtube_id`, `.spotify_uri`, `.youtube_url`, `.key` = `(seq, type, place_key or 0, flags)`, `.describe()`, `.to_bytes()`, `.raw`, `.extra` |
| `Geometry(cells_per_row=64, cell_px=3, corner="TOPLEFT", max_rows=None)`, `Geometry.beacon(cell_px=3, max_rows=None)` | validated (`max_rows` ≤ `rows_cap(cell_px)`); `coerce` / `normalize` clamp like Lua |
| `encode_cells(frame_or_bytes, geom) -> CellGrid` | `rows`, `cols`, `cells` with RGB 0/255, `cell_px`, `width_px`, `height_px`, `overflow`, `as_rows()`, `codes()` |
| `encode_event_cells(ev, geom)` | `EncodeCells(ev, geom)`: fit to `capacity(geom.max_rows)`, then cells |
| `rows_cap`, `capacity`, `adaptive_layout`, `rows_for`, `strip_size`, `keystream_bits` | geometry and whitening helpers |
| `decode_cells_grid(rows_of_rgb, threshold=128)`, `decode_cell_codes(codes, cols, rows)` | `Frame` or `None` |
| `format_chat_line(frame_or_bytes)`, `parse_chat_line(line, *, strict=True)`, `find_chat_record` | chat line |
| `pad_line(seq, length=900)`, `pad_line_count(pad_bytes, length=900)` | `NDPAD` lines |
| `b64url_encode`, `b64url_decode` | base64url |
| `clean_text`, `utf8_clean_bytes`, `utf8_truncate`, `sanitize_id`, `build_query`, `place_key_for` | text helpers |
| `spotify_id_from_link`, `youtube_id_from_link` | link parsers |

`needledropper.transport.beacon2`: `locate_and_decode`, `scan`, `Beacon2Result`, `ScanReport`, `HeaderHit`,
`CORNERS`, `CAPTURE_WIDTH`, `CAPTURE_HEIGHT`, `MAX_STRIP_WIDTH`, `MAX_STRIP_HEIGHT`, `capture_size(scale)`.

## 7. Test vectors

| Event | Frame bytes | Chat line |
|---|---|---|
| `{type=1, seq=0}` (HELLO) | `4e 21 00 00 00 0c 6f` | `NDROP2 TiEAAAAMbw` |
| `{type=4, seq=42, flags=0x20, dropId="zone:1429"}` (CANCEL) | `4e 24 2a 20 0f 01 02 51 c8 02 09 7a 6f 6e 65 3a 31 34 32 39 4e bc` | `NDROP2 TiQqIA8BAlHIAgl6b25lOjE0MjlOvA` |
| `{type=2, seq=8, dropId="area:87", youtube="https://youtu.be/dQw4w9WgXcQ", artist="Sigur Rós", title="Hoppípolla", startMs=12000}` (ARM, 61 B) | `4e 22 08 00 36 01 02 2f 44 02 07 61 72 65 61 3a 38 37 04 0b 64 51 77 34 77 39 57 67 58 63 51 05 16 53 69 67 75 72 20 52 c3 b3 73 1f 48 6f 70 70 c3 ad 70 6f 6c 6c 61 06 02 00 78 65 a7` | `NDROP2 TiIIADYBAi9EAgdhcmVhOjg3BAtkUXc0dzlXZ1hjUQUWU2lndXIgUsOzcx9Ib3Bww61wb2xsYQYCAHhlpw` |

The HELLO strip at 64 cells is one row and starts `m g m g r k k w k r m …`:
* `r` is G0 = 4, i.e. 64 cells.
* `k k` are G1 = G2 = 0, i.e. one row.
* `w k r m` is the known prefix.

The v1.2 beacon draws the same HELLO adaptively: 32 cells (G0 = 2, green), one row, 96 × 3 px at 3 px.

Fit-rule vector (`FULL_USER_DROP` in the contract test, the 141-byte drop of §4.3): frame bytes at falling
budgets 141 → 131 (setId) → 113 (label) → 86 (query) → 59 (dropId) → 56 (volume) → 43 (Spotify preferred) or
32 (YouTube preferred); below that it stays 43 / 32.

## 8. Design decisions (refinements of the proposal)

1. **Three geometry cells, not two.** The proposal had W/16 and rows in 3 bits each (rows 1..8). At 64 cells per
   row, 8 rows hold only `(8·64 − 7)·3/8 = 189` bytes, and a 262-byte frame needs 12 rows (23 at 32 cells). With
   `rows − 1` in two cells (6 bits), every width 16..112 can carry a maximal frame.
2. **Whitening plus strict pad and row checks** as the "pad/terminator scheme" (§4.2). Plain zero padding plus
   Fletcher-16 would accept white-over-zero and black-over-0xFF covers.
3. **Strict structural decoding.** Tag 0 is invalid, tags are strictly ascending, and known-tag lengths and
   character sets are validated. This rejects garbage cheaply and costs nothing for conforming encoders; new
   fields use new tags.
4. **Byte-wise id sanitising, and a placeKey over the full id.** v1's Python regex sanitised per code point
   while Lua's `gsub` works per byte, so the two disagreed on non-ASCII ids. v2 does bytes in both languages. The
   placeKey is computed over the untruncated id, so long ids that share a 40-byte prefix still dedupe apart.
5. **Query fair split.** Plain truncation of `artist 0x1F title` to 120 bytes could lose the separator and the
   whole title when an artist name is long. Each part now keeps about half the budget.
6. **Zero start/length omitted; per-type field sets.** These give the smallest frames, and so the fewest strip
   rows, for the commonest events.
7. **The decoder needs no expected geometry.** It reads `cols` and `rows` from the strip, so the addon can
   choose the width per frame and change the cell size at runtime.
8. **v1.2: small strips, briefly** (DESIGN-v1.2 §2.3). Rows × cellPx ≤ 9 px keeps the strip above the player
   frame's art; the width adapts to the frame; the fit rule removes the least useful fields first (labels and
   set ids before anything the companion plays from, the search query only when an id makes it redundant) and
   never the place, start, length or the last id. Song frames are large only for about 6 s per drop.
9. **v1.2: synthesized steady frames reuse their event's seq** (§4.6): they are re-renderings of one event, the
   short ARM is a dedupe repeat, and nothing but real events advances `seq` or reaches the chat log.

## 9. Integration checklist (v1.2)

Status: done in the 1.2.0 integration (the TOC, Director, SelfTest, Core settings and the companion
transports follow it; `tests/test_integration_static.py` and `tests/test_addon_e2e_durotar.py` check
it). Kept as the record of what the cutover needed.

**Addon** (CONTRACTS-v1.2 §2.1 TOC order: `… Protocol2.lua, Core.lua, … Companion.lua, Emit.lua, Location.lua,
Discovery.lua, Audio.lua, Beacon.lua, ChatLink.lua, Toast.lua, Director.lua, …`; `Protocol.lua` deleted):

* `Beacon.lua` and `ChatLink.lua` index `ns.Protocol2` at load time, so `Protocol2.lua` must be listed before
  them; `Emit.lua` before `Director.lua`.
* `Director.lua`: delete `ns.Events` / `ns.LastEvent`, `addFlag`, `D.ComputeFlags` and the v1 `ns.Emit`
  (Emit.lua defines them; `ns.ComputeFlags` replaces `D.ComputeFlags`), and change `local P = ns.Protocol` to
  `local P = ns.Protocol2` (it uses `EV`, `FLAG`, `HasFlag`, `EV_NAME`). `SelfTest.lua`: the same `P` change
  (it uses `EV.PING` and `PadLine`).
* `Core.lua` settings: `beaconCellPx` default **3**, range **2..4** (tip: "Physical pixels per beacon cell";
  drop "Match the companion's setting"); new `beaconCorner` (options `topleft`, `topright`, `bottomleft`,
  `bottomright`, lowercase because `ns.CoerceSetting` lowercases input; default `topleft`; `Beacon.Corner()`
  upper-cases it; `advanced = true`, which Options.lua skips). Schema 1 → 2 moves a stored 4 to 3. There is no
  `beaconCellsPerRow` any more.
* `/needle selftest beacon` should print `Beacon.Status()` (gap, rows, cell px, size) and its `warnings`.

**Companion**

* **Transport**: feed the top-left `capture_size(scale)` region (512 × 48 at 100 %) to `beacon2.scan`; keep
  v1's acceptance rules (two identical consecutive frames, a baseline first frame, re-baseline when the window is
  lost).
* **Chat-log transport**: `protocol2.parse_chat_line` (strict by default); ignore `NDPAD` lines.
* **Engine**: dedupe by `Frame.key`; play the Spotify id, else a Spotify search on `artist` / `title` (when
  Spotify is connected), else the YouTube id, using `start_ms` / `length_ms` / `volume`; show `label`. There is
  no map lookup in v2.
