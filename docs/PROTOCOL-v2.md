# NeedleDropper — Wire protocol v2 (song identity in-band)

Status: **implemented and contract-tested; the v1.2 addon emits only v2** (DESIGN-v1.2 §2). Supersedes
SPEC.md §3.4–§3.6 (beacon payload, chat line). The v1.2 companion still decodes v1 (`NDROP1`, the 10-byte
beacon) for old addons.

**v1.2.1 changes** (protocol v2 was never released, so the wire format changed without a version bump,
decision D1; the Lua and Python codecs and the vectors changed together):

* the 2-byte Fletcher-16 trailer is now a **4-byte frame check** (§2, §4.2; finding F1 of
  docs/review/protocol-attack-findings.md). Every frame is 2 bytes longer: 9..264 bytes;
* event type **6 CONFIG** and the TLVs **0x0B PREFS**, **0x0C ACTION**, **0x0D CLIENT_ID**; HELLO may carry
  PREFS and the playing drop's **placeKey** (§2.5; DESIGN-v1.2.1-music-services §2);
* fit-rule steps 3 and 5 (§2.1; findings F3 and F4);
* decoders require **strict UTF-8** in the query and the label (§2.4).

A 1.2.0 app reads no 1.2.1 frame (each fails its length and checksum test) and a 1.2.1 app no 1.2.0 frame:
the addon and the app must be updated together.

| Side | Module | Contents |
|---|---|---|
| Addon (Lua 5.1) | `addon/NeedleDropper/Protocol2.lua` → `ns.Protocol2` | frame encoder with the fit rule (+ decoder for self-tests), frame check, strip geometry, cell grid, chat and padding lines, link parsers, UTF-8 helpers, `HasFlag` |
| Addon | `MusicServices.lua` → `ns.MusicServices` | the music choices (PREFS) in HELLOs and the CONFIG requests (DESIGN-v1.2.1 §1.4, §2.9) |
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
                                  └──────EncodeFrame(ev) (≤ 264 B)───▶ full frame ───FormatChatLineBytes───▶ "NDROP2 <base64url>"
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
5+n     4     frame check of bytes 0..4+n    uint32 big-endian (FrameCheck ≡ frame_check, below)
```

* Frame size is `9 + bodyLen`: 9 (empty body) to **264** bytes. A body is empty or at least one 2-byte TLV
  head, so no frame is 10 bytes long.
* **The frame check** (v1.2.1, finding F1): `h = 0x4E443243` ("ND2C", 1313092163), then for every byte `b`
  before the trailer `h = (h · 48271 + b + 1) mod (2^31 − 1)`, then two final rounds
  `h = (h · 48271 + 1) mod (2^31 − 1)` (as if two 0x00 bytes followed; `CHECK_FINAL_ROUNDS = 2`, review RP-1);
  the trailer is `h` as uint32 big-endian (its top bit is always 0). 2^31 − 1 is prime and 48271 a primitive
  root mod it; every intermediate stays below 2^47, so Lua 5.1 doubles compute it exactly, without a bit
  library (`P2.FrameCheck(bytes, n)` ≡ `protocol2.frame_check(data)`). Why it replaced Fletcher-16, and why the
  final rounds: §4.2.
* Event types: the v1 ones, `0 NONE`, `1 HELLO`, `2 ARM`, `3 FIRE`, `4 CANCEL`, `5 PING`, and (v1.2.1)
  `6 CONFIG`: music choices and requests from the game, never a song (§2.5). Types 7–15 are rejected by v2
  decoders. The v1 decoders (`protocol.decode_beacon_bytes`, the `NDROP1` parser) still accept only 0–5.
* **Dedupe key**, shared by both transports (CONTRACTS-v1.2 §3.1): `(seq, type, placeKey or 0, flags)`
  (`Frame.key`). The beacon's fitted frame and the chat line's fuller frame of one event share it, and so do
  the beacon's full ARM and the placeKey-only ARM it shows afterwards (§4.6). A HELLO that carries the playing
  drop's placeKey (§2.5) has the key `(seq, 1, placeKey, flags)`, distinct from the plain HELLO's
  `(seq, 1, 0, flags)`. The pixel transport should still require two identical consecutive reads of the whole
  frame, and treat the first valid frame after start as a baseline, as in v1.

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
| `0x0B` | prefs | 4 code bytes: order, corner, fade, resume | v1.2.1, HELLO and CONFIG (§2.5) |
| `0x0C` | action | 6 B: code, `code XOR 0xFF`, issuedAt uint32 BE | v1.2.1, CONFIG (§2.5) |
| `0x0D` | clientId | 16 B: the Spotify client id's 32 hex digits | v1.2.1, CONFIG (§2.5) |
| `0x0E` | musicVolume | 1 B, 0–100 | beta 3 master music percentage, HELLO and CONFIG (§2.5) |
| `0x0F`–`0xFF` | — | any | reserved for future fields. v2 decoders skip them (kept in `Frame.extra`) |
| `0x00` | — | — | invalid (rejects zero-filled garbage) |

* **Numbers** must be numbers (Lua `type(v) == "number"`, Python int/float but not bool). Anything else,
  including NaN and ±inf, counts as absent. `type`, `seq` and `flags` are floored and wrapped mod 16/256/256.
* **Fields per type** (`TYPE_TAGS` in both languages; the encoder drops every other field):
  * ARM and FIRE carry the song fields `0x01`–`0x0A` (never PREFS, ACTION or CLIENT_ID).
  * CANCEL carries `placeKey` and `dropId` only.
  * HELLO carries `placeKey` (the playing drop's, from `placeKey` or `dropId`; §2.5), `prefs` and `musicVolume`.
  * CONFIG carries `prefs`, `action`, `clientId` and `musicVolume`.
  * PING and NONE carry an empty body.
* **Fit rule** (DESIGN-v1.2 §2.3, with v1.2.1's F3 / F4 fixes). `EncodeFrame(ev, maxBytes, prefer)` ≡
  `build_frame(..., max_bytes, prefer)` keeps the whole frame within a byte budget: 264 by default (the chat
  line; a body of at most 255 bytes) and the row capacity for the beacon (§4.3). The budget is floored and
  clamped to 9..264; anything that is not a number means 264. While the frame is still over budget, the encoder
  removes, in this order:
  1. `setId`;
  2. `label`;
  3. `query`, if the **preferred** provider's id is present (after link parsing; finding F4: a Spotify user
     with only a YouTube id keeps the query, so the app can still search Spotify);
  4. `dropId` (`placeKey` stays and still identifies the place);
  5. **shortens the query, artist and title separately** (finding F3): the excess comes off the parts by
     BuildQuery's fair split (a part within half the room stays whole, otherwise the artist gets half), each cut
     on a UTF-8 boundary, and the `0x1F` separator always stays. When the room left cannot keep
     `MIN_QUERY_PART` = 8 bytes of each part (all of a part shorter than that), the whole query goes. A query
     fragment without its separator is never sent, so the app never reads an artist prefix as a title;
  6. `volume`;
  7. the provider id that is not preferred: `prefer` is `"spotify"` or `"youtube"` (ASCII case-insensitive;
     anything else, and the default, is YouTube; the event's own `prefer` field is the default). The addon sets
     it to Spotify when the NeedleDropper app reports Spotify connected (`ns.PreferredProvider`).

  Never removed: `placeKey`, `startDs`, `lengthDs`, the flags, the last provider id, and PREFS, ACTION and
  CLIENT_ID (every HELLO and CONFIG the addon builds is within every budget the beacon uses, §2.5). So the result can
  still exceed a budget below that minimum (at most **45 bytes**: placeKey + start + length + a Spotify id;
  34 with a YouTube id); the beacon then draws an extra row (§4.6). With every field at its cap the body would
  be 299 bytes; the 264-byte budget removes setId and the label and leaves **224 bytes**. Frames of up to 264
  bytes still occur (e.g. a 38-byte label without a set id), so 264 is the decoders' bound. This order replaced
  the v1.1 rule (drop setId, then *shrink* label, query and dropId to 255 body bytes); both languages changed
  together, and again for v1.2.1's steps 3 and 5.

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
* The type is ≤ 6.
* The frame length is exactly `9 + bodyLen`.
* The 4-byte frame check matches.
* The TLV walk fits the body exactly, every tag is non-zero, and tags are strictly ascending (no duplicates).
* Known tags have valid lengths and character sets: placeKey/start/length 2 B, volume 1 B ≤ 100, ids per the
  table, query 1–120 B, label 1–48 B, PREFS exactly 4 B, ACTION exactly 6 B with the check byte
  `code XOR 0xFF`, CLIENT_ID exactly 16 B, MUSIC_VOLUME exactly 1 B and at most 100.
* **The query and the label are strict UTF-8** (v1.2.1, decision D5): an invalid sequence (bad lead or
  continuation byte, overlong form, UTF-16 surrogate, above U+10FFFF, cut off at the end) rejects the frame.
  Encoders only ever write valid UTF-8 (§2.2), so a decoded text is always exactly what was sent. Lua
  `P2.Utf8Valid(s)` ≡ Python's strict codec.

Unknown PREFS and ACTION codes are not errors: that field decodes as absent (an action without a name), so a
newer addon can add values. Decoders do not enforce `TYPE_TAGS`: the app acts on PREFS only from HELLO and
CONFIG frames and on ACTION and CLIENT_ID only from CONFIG frames.

### 2.5 v1.2.1: CONFIG, PREFS, ACTION, CLIENT_ID and HELLO's placeKey

DESIGN-v1.2.1-music-services §2 has the semantics (who sends what, when, how the app applies it); this is the
encoding.

* **CONFIG (type 6)**: `4E 26 seq flags bodyLen [0x0B PREFS] [0x0C ACTION] [0x0D CLIENT_ID] check`. A CONFIG
  carries PREFS, an ACTION, or both; CLIENT_ID goes only with the ACTIONs connect-spotify and
  disconnect-spotify, and **a frame that carries CLIENT_ID carries no PREFS**. That last rule is the emitter's
  (`ns.MusicServices`), not the codec's: all three together are 41 bytes, two strip rows, and every frame the
  addon builds must be one row (≤ `Capacity(1)` = 39 B).
* **0x0B PREFS** (HELLO, CONFIG), exactly 4 bytes, each 0 = "not set" (the app keeps its own value):

  | Byte | Field | Codes |
  |---|---|---|
  | 0 | order | 1 spotify-youtube · 2 youtube-spotify · 3 spotify · 4 youtube · 5 none |
  | 1 | corner | 1 topright · 2 topleft · 3 bottomright · 4 bottomleft |
  | 2 | fade | 1 on · 2 off |
  | 3 | resume | 1 on · 2 off |

  Encoder input `prefs = { order, corner, fade, resume }`: the names match ASCII case-insensitively, `fade` and
  `resume` must be booleans, anything else is not set; nothing set means no TLV. Later preferences get new
  tags, not longer values.

* **0x0E MUSIC_VOLUME** (HELLO, CONFIG; beta 3), one byte, 0–100 inclusive. This separate optional
  preference preserves the four-byte PREFS layout. Absence keeps the app's saved `music.volume`;
  0 means muted. Older apps skip this unknown tag. The master percentage multiplies the drop's
  volume when the app starts a song or service test; an active song keeps its original volume.
  In-game WoW audio files continue to use the game's Music volume.
* **0x0C ACTION** (CONFIG), exactly 6 bytes: `code`, `code XOR 0xFF`, `issuedAt` (uint32 BE, the game's
  `time()` at the click). Codes: 1 connect-spotify · 2 disconnect-spotify · 3 test-spotify · 4 test-youtube ·
  5 youtube-sign-in. Encoder input `action = { name, at }`: the name is one of the five (ASCII
  case-insensitive) and `at` a number, floored and clamped to 0..4294967295; otherwise no TLV.
* **0x0D CLIENT_ID** (CONFIG), exactly 16 bytes: the bytes the 32 hex digits spell. Encoder input `clientId`:
  ASCII whitespace is removed and the rest must be 32 hex digits in either case; otherwise no TLV. Decoders
  return 32 lowercase hex digits.
* **HELLO's placeKey** (decision D3, finding F2): the steady HELLO the beacon shows after a FIRE (DROP_ACTIVE)
  carries the playing drop's placeKey (`placeKey`, or `dropId` from which it is derived; HELLO never carries
  the dropId itself). If the FIRE's 3 s were covered, the app sees the HELLO + DROP_ACTIVE with the placeKey of
  the ARM it holds and can start that song late, as the FIRE would have. Emitted HELLOs (`ns.BuildEvent`) carry
  no drop, so no placeKey (nor their chat lines); the beacon's drawn copy of any HELLO with DROP_ACTIVE carries
  the playing drop's (§4.6). The app acts on the placeKey only when DROP_ACTIVE is set, and must respect
  LOCAL_PLAYBACK on that HELLO as on a FIRE (the game plays the file).

Sizes (frame bytes; one row at 2, 3 and 4 px except the last; Lua ≡ Python, `test_music_frames_size_table`):

| Frame | Bytes | Columns | 3 px strip |
|---|---|---|---|
| HELLO (no music choices set in game) | 9 | 32 | 96 × 3 |
| HELLO + placeKey (after a FIRE) | 13 | 48 | 144 × 3 |
| HELLO + PREFS / CONFIG, PREFS only | 15 | 48 | 144 × 3 |
| HELLO + placeKey + PREFS | 19 | 64 | 192 × 3 |
| CONFIG, PREFS + ACTION | 23 | 80 | 240 × 3 |
| CONFIG, ACTION + CLIENT_ID | 35 | 112 | 336 × 3 (448 × 4 at 4 px) |
| CONFIG with all three (never built: two rows) | 41 | 64 × 2 rows | 192 × 6 |

## 3. Chat-log line

```
NDROP2 <base64url(frame), RFC 4648 §5 alphabet, no padding>
NDPAD <seq> ......................................   (exactly 900 characters, after FIRE / CANCEL / PING)
```

* The addon (`ChatLink.lua`) sends the line with `SendSystemMessage`, only when `settings.chatlog` is on (or a
  self-test forces one line). The line carries the event's **full** frame (`FormatChatLine(ev)`, budget 264):
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
* **v1.2.1 CONFIG** (DESIGN-v1.2.1 §2.8): a CONFIG that carries an ACTION (a request to the app) is padded like
  FIRE / CANCEL / PING, so it reaches the disk at once, and a request line that waited more than **20 s**
  (`ChatLink.CONFIG_MAX_AGE`) in the lockdown queue is dropped with its padding (the app ignores requests older
  than 45 s anyway). A PREFS-only CONFIG is not padded and is kept like other lines. `ChatLink.Status().stale`
  counts both kinds of dropped lines. HELLO lines carry PREFS once the player chose a music setting in game.
* Chat logging (`LoggingChat(true)`) is turned on only when `settings.chatlog` is on (at login and when the
  setting changes) or by the chat-log self-test, and turned off again only if the addon turned it on.
* `parse_chat_line(line, strict=True)` accepts a record only when it is the whole system message: the client's
  timestamp, whitespace, then `NDROP2 <token>`, then optional trailing whitespace to the end of the line.
  The line may also simply start with `NDROP2 `.
  * Chat typed by other players always carries a `[Name]` or channel prefix, so it can never fire a drop.
  * `strict=False` finds a record anywhere in the line.
* Base64url decoding is strict: alphabet, length (`len % 4 != 1`) and canonical trailing bits. The frame
  check does the rest.

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

Whitening plus a strict pad check is the "terminator" scheme, and the 4-byte frame check (§2) closes what it
leaves open:

* **Uniform covers become pseudo-random errors.** A white cover over zero bytes, or a black one over 0xFF
  bytes, would otherwise leave a mod-255 checksum unchanged (Fletcher-16 cannot tell 0x00 from 0xFF):
  unwhitened, a tooltip over `startDs = 0` would decode as `startDs = 6553.5 s`. Whitening turns any uniform
  cover into pseudo-random bit errors, which the frame check and the TLV structure catch
  (`test_uniform_covers_fail_the_checks`).
* **Every pad bit must de-whiten to 0, and `rows` must equal what the frame length needs.** A strip that is
  partly covered, cut off, or stale (an old longer frame's tail) fails even when the covered cells are only
  padding. A strip must be fully visible to decode, so it never decodes as garbage.
* **Dense transitions.** Whitened cells change colour at about 7/8 of boundaries in every row. That is what lets
  the decoder fit sub-pixel lattices (§4.4).
* **Column covers and the frame check** (v1.2.1, finding F1). A row of 16k cells is exactly 6k bytes (112 cells:
  42 bytes), so a cover one cell column wide and as tall as the strip changes the same bits of bytes `i`,
  `i + 6k`, `i + 12k`. With whitening a uniform cover gives the change pattern (−d, +2d, −d), whose plain and
  position-weighted sums are both 0: invisible to Fletcher-16. Under 1.2.0's trailer 156 of 300 bundled FIRE
  frames at 2 and at 3 px had a static cover that decoded to another valid frame (a different YouTube id,
  placeKey, start…; 716 of the 838 such covers were one column wide), and a random 300,000-capture sweep found
  7 wrong frames, all multi-row strips. The frame check is a
  polynomial hash over the prime field mod 2^31 − 1: a change pattern `d` passes only if
  `Σ dᵢ · 48271^(n−1−i) ≡ 0`. For F1's pattern that is `−d · (48271^k − 1)² ≡ 0`, which needs `48271^k ≡ 1`,
  impossible for a primitive root and k < 2^31 − 2.
* **The final rounds** (review RP-1, 2026-11-06). Without them the last body byte had the weight 48271^0 = 1 in
  `h`, just as the trailer's low byte has the weight 1 in the trailer, so `+d` on both (two cells 10–11 apart,
  two thin strokes) left the frame valid. Every frame kind the addon draws had such cell pairs (10,611 of
  12.35 M pairs tested over 1,575 frames), a 200,000-capture sweep of the v1.2.1 frame kinds found 2 wrong
  frames, and two 1-pixel strokes over the real strip turned a connect request's Client ID, the D3 HELLO's
  placeKey or a one-row FIRE's YouTube id into another valid value. The two final rounds give the last body
  byte the weight 48271² mod (2^31 − 1) = 182,605,794 and every other byte a weight just as far from the small
  powers of 2 a few trailer bits can make. Checked exhaustively, algebraically, at every frame length and
  position, any colour over any content (`test_no_change_of_a_few_cells_across_the_trailer_passes_rp1`):
  no change confined to one body cell plus one or two trailer cells, or to two body cells near the trailer plus
  one trailer cell, passes (without the final rounds 16–38 such cell patterns per alignment did), and no change
  of one body byte together with one trailer byte, of any amounts, passes. At the decoder: every pair of cells
  near the trailer, in every colour, fails on the review's one-row HELLO, CONFIG and FIRE frames
  (`test_two_cell_changes_near_the_trailer_fail_rp1`).
* Checked exhaustively for changes to the bytes before the trailer:
  * every change confined to at most two bytes, at any distance up to 263, is detected;
  * every change confined to one cell column of a strip of 2 to 4 rows (any bit of the column flipped either
    way: any cover colour over any content), at every width and bit offset, is detected
    (`test_no_one_column_change_can_pass_the_check_f1`);
  * a cover two cells wide over all four rows of a 2 px strip is the narrowest column change that can, in
    principle, cancel: 2 to 6 change patterns per geometry among some 10^10, so about 10^-9 or less per cover;
    no sweep has found one.

  Any other corruption passes with probability about 2^-31 (≈ 4.7 · 10^-10) per read, and the transport still
  needs two identical reads.

The checks a decoder applies are: header cells, geometry cells (cols matches the sampled grid, rows matches the
frame length), pad bits zero, then everything in §2.4.

### 4.3 The v1.2 beacon geometry (DESIGN-v1.2 §2.3)

* **Cell size**: 3 physical px by default; the `beaconCellPx` setting allows 2..4.
* **Height cap**: rows × cellPx ≤ 9 px, i.e. `RowsCap(px) = floor(9 / px)` ≡ `rows_cap`: 2 px → 4 rows,
  3 px → 3 rows, 4 px → 2 rows. The clearance guard (§4.6) may lower it at runtime.
* **Budget**: a strip of `maxRows` rows of 112 cells holds `Capacity(maxRows) = floor((maxRows·112 − 7)·3/8)`
  frame bytes (the 9-byte overhead included) ≡ `capacity` (at most 264): one row 39 B, 2 px (4 rows)
  **165 B**, 3 px (3 rows) **123 B**, 4 px (2 rows) **81 B** (unchanged by v1.2.1: the trailer's 2 extra bytes
  come out of the fields). The event is fitted to it with the fit rule (§2.1). `rows ≤ maxRows` holds exactly
  when `frameLen ≤ Capacity(maxRows)`.
* **Width, per frame** (`AdaptiveLayout(frameLen)` ≡ `adaptive_layout`): with `cells = 7 + ceil(8·frameLen/3)`,
  `rows = ceil(cells / 112)` and `cols = 16 · ceil(ceil(cells / rows) / 16)` — the narrowest multiple of 16 that
  holds the frame in that many rows. `RowsFor(frameLen, cols)` is always exactly `rows`, so a decoder reads an
  adaptive strip like any fixed-width one.
* `EncodeCells(ev, {cellPx, maxRows, corner})` ≡ `encode_event_cells(ev, Geometry(cell_px=, max_rows=))`:
  fit to `Capacity(maxRows)`, then the adaptive layout. `maxRows` is floored into `1..RowsCap(cellPx)`. When the
  fitted frame still needs more rows than `maxRows` (§2.1's irreducible minimum), the grid has the rows it needs
  and `overflow` is set (`CellGrid.overflow`); it is complete and decodes normally.

Measured with both encoders (`tests/test_protocol2_contract.py::test_measured_size_table`; every frame is
2 bytes longer than with 1.2.0's 2-byte trailer):

| Frame | Bytes | Rows at 112 cols | Strip at 3 px |
|---|---|---|---|
| HELLO | 9 | 1 | 96 × 3 |
| CANCEL `zone:1429` | 24 | 1 | 240 × 3 |
| FIRE, bundled Mixtape drop (Durotar: YouTube + query + length) | 62 | 2 | 288 × 6 |
| … same plus label and setId | 88 | 3 | 288 × 9 |
| FIRE Spotify + YouTube + query + start + length + volume (`area:87`) | 97 | 3 | 288 × 9 |
| FIRE user drop with every field (`sub:1429:lion-s-pride-inn`) | 143 | 4, over the cap | fitted to 115 B → 336 × 9 |
| Worst case (all fields at their caps) | 224 (chat; 299-byte body before the fit) | 6 | fitted to 103 B ≤ 123 B |

At 2 px the 143-byte drop keeps every field (4 rows of 112 cells, 224 × 8 px); at 4 px it is fitted to 61 B
(96 × 2 cells, 384 × 8 px). The HELLO and CONFIG sizes are in §2.5.

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
     strip the pad colours of a guessed frame length (9 bytes, then the lengths the candidate lattices read
     from the frame head) are enforced too. The cheapest few lattices are each validated by the frame check.

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

* All 4 corners × 1–8 px cells × 32/48/64 cells per row × frames of 9..264 bytes.
* Adaptive v1.2 strips at 2/3/4 px in the 512 × 48 region at 100/125/150 % capture scaling, including the
  two-row overflow strip, and strips rendered from the addon's own textures (`tests/test_v12_beacon_*.py`).
* Offsets up to `max_offset`.
* Noise ±25.
* Tone shifts (HDR-like, washed out, dim, lifted blacks).
* Fractional pitch (cell × 1.125/1.25/1.333/1.5/1.75).
* Whole-frame nearest-neighbour resampling.
* Box-filtered (blended) captures.

Partial occlusion, tearing between two frames, cut-off rows, a v1 strip and garbage all return `None`. With
pitches of 1.2–9 px, 0 of 600 random strips failed to decode.

**Wrong frames.** 1.2.0 claimed "no wrong frame ever" from about 5,000 random cases; the adversarial review
(finding F1) disproved it for multi-row strips under a static cover one cell column wide, the Fletcher-16 blind
spot of §4.2. With v1.2.1's frame check, measured on the attack harness (docs/review/protocol-attack-findings.md,
re-run 2026-11-06; the decisive cases are in `tests/test_protocol2_contract.py`):

| Sweep | 1.2.0 (Fletcher-16) | v1.2.1 (frame check) |
|---|---|---|
| Exhaustive covers (every cell-aligned rectangle up to 10 cells wide, all heights, 8 colours) over the bundled drops as FIRE / ARM / CANCEL at 2 / 3 / 4 px, every HELLO, and (v1.2.1) the HELLO + placeKey / PREFS and CONFIG frames | 640 of 3,380 frames breakable, 838 wrong covers | 0 of 4,481 frames, 0 wrong of 89.5 million covers |
| 300,000 random realistic captures (100–175 % scaling, HDR tone, blur, sharpen, JPEG-like noise, overlays, glyph strokes, toasts; about 120,000 covered), the same seeds and generator | 7 wrong frames | 0 wrong (and 0 in a second 300,000 that adds HELLO + placeKey / PREFS and CONFIG frames) |
| The real addon's strip (lupa) with a 1–2 px line over every column, 5 colours, at 100 / 125 / 150 / 175 % (Menethil Harbor, Shimmering Flats, Durotar) | the repro lines played the wrong YouTube id | 0 wrong of 12,800 covers |
| The real addon's Lion's Pride Inn FIRE strip under every cell-aligned patch 1–4 cells wide and 1–3 rows tall, 4 colours, at 2 and 3 px, 100 / 125 / 150 % | a gold patch turned startDs 120 into 65400 | 0 wrong of 79,560 covers |

Under a cover the decoder now returns `payload invalid` (or the right frame, when the fallbacks read around a
thin line); the seven 1.2.0 wrong-frame seeds all end `payload invalid`. The decode rate is essentially
unchanged: 234,376 of the 300,000 captures decoded correctly, against 234,574 with Fletcher-16 (the frames are
2 bytes longer).

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
  * after a **FIRE**: HELLO with DROP_ACTIVE (v1.2.1: carrying the playing drop's placeKey, §2.5, so a FIRE
    whose hold was covered can still be started from the cached ARM; finding F2, decision D3). When an ARM
    replaces it before it was on screen for 3 s (`PLAYING_HOLD`; the Director arms the next place right after
    a FIRE), that HELLO is drawn first, for the rest of the 3 s, right after the FIRE's hold (review RP-3).
  * after a **CANCEL**: HELLO.
  * PING doesn't change it.

  These steady frames are re-renderings, never emitted: they reuse their event's seq, never bump
  `ns.db.lastSeq`, never enter `ns.Events` and never reach the chat log. In the two synthesized HELLOs, TEST is
  cleared, and LOCAL_PLAYBACK unless DROP_ACTIVE is set (a CANCEL of a pending drop while another plays keeps
  DROP_ACTIVE, so the companion keeps playing). The placeKey-only ARM has the full ARM's dedupe key: the
  companion sees a repeat and keeps the song it cached.
* **v1.2.1, every HELLO as drawn** (emitted or synthesized; a beacon-owned copy, `Status().current`) carries the
  music choices `Beacon.SetPrefs(prefs)` stored (PREFS; `ns.MusicServices` calls it at load and whenever a music
  setting changes, so the HELLO on screen changes at once) and, **while DROP_ACTIVE is set, the playing drop's
  placeKey** (D3): after a FIRE the FIRE's own; after a CANCEL that keeps DROP_ACTIVE, and for an emitted HELLO
  with DROP_ACTIVE (the first HELLO after a `/reload` mid-drop), `Director.active.drop`'s, else the last FIRE's.
  The emitted HELLO and its chat line carry no placeKey. Sizes at 3 px: 96 × 3 idle, 144 × 3 with PREFS or a
  placeKey, 192 × 3 with both.
* **v1.2.1 CONFIG, queue B** (DESIGN-v1.2.1 §2.7): CONFIGs never change the steady state. Queue B holds 4 (when
  full, the oldest without an ACTION goes, else the oldest); a CONFIG is shown only while FIFO A is empty and no
  ARM is in its full-song window, for **3 s** with an ACTION or **2 s** PREFS only. A FIFO-A event or a new ARM
  takes the screen in the same `Pump`; the CONFIG goes back to the head of queue B with its full hold. **8 s**
  after an ACTION CONFIG first completes its hold, the same event (same bytes) is queued once more for 2 s. With
  `beacon` off, an ACTION CONFIG is forced visible for its hold + 0.5 s (`ForceShow`); a PREFS-only one is not.
  `B.Pump` decides in this order: a transient within its hold, the next FIFO-A event, an ARM's full window,
  queue B, the steady state.

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
lastFrameType, lastFrameSeq, variant, step, overflow, current` (the drawn event), `queued, dropped, warnings`;
v1.2.1 adds `configQueued, configReshows, configDropped, configShown, prefs` (variant "config" while a CONFIG
shows).

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

**v1.2.1** (DESIGN-v1.2.1 §1.5, §2.9):

* `ns.Emit` computes the live flags **before** it builds the event and passes them to `ns.BuildEvent` as
  `extraFlags`; a CONFIG never carries TEST (it is cleared after the live flags are computed).
* HELLO: `prefs = ns.MusicServices.WirePrefs()` (nil until the player chose a music setting in game).
* CONFIG: `ns.BuildEvent(EV.CONFIG, nil, 0, { prefs =, action = { name, at }, clientId = })` copies those three
  fields and nothing else. Requests are built by `ns.MusicServices.Send` (connect / disconnect: ACTION +
  CLIENT_ID, never PREFS; the others: PREFS + ACTION), one strip row each; the PREFS-only CONFIG follows the last
  music setting change by 1 s.
* ARM / FIRE: "Play music with" leaves out song fields (`MusicServices.Strip`): "Neither" removes `spotify`,
  `youtube`, `artist` and `title`; for an app older than 1.2.1 (or no app data), "Spotify only" removes `youtube`
  and "YouTube only" removes `spotify`, `artist` and `title`. Never for a FIRE with LOCAL_PLAYBACK, the ARM of a
  drop the game will play itself (an in-game file with `localPlayback` on), or the test drop. `prefer` =
  `ns.PreferredProvider()`, which follows "Play music with".

## 6. API reference

### 6.1 Lua (`ns.Protocol2`, pure functions, never error in game)

| Function | Returns |
|---|---|
| `EncodeFrame(ev [, maxBytes [, prefer]])` | byte array (1-based numbers 0..255), fitted to `maxBytes` (default 264) by the fit rule. The `ev` fields are listed at the top of the file and in §2.1 / §2.5: `type, seq, flags, dropId, placeKey, spotify, youtube, artist, title, startMs \| startDs, lengthMs \| lengthDs, volume, label, setId, prefer` and (v1.2.1) `prefs = {order, corner, fade, resume}`, `action = {name, at}`, `clientId` |
| `DecodeFrame(bytes)` | event table (`type, seq, flags, placeKey, dropId, spotify, youtube, query, artist, title, startDs, lengthDs, volume, label, setId, prefs = {order, corner, fade, resume}, action = {code, name, at}, clientId, unknown`) or `nil`; `unknown` lists `{tag, value}` of tags ≥ 0x0E |
| `FrameCheck(bytes [, n])` | the 4-byte trailer's value over the first `n` bytes (§2) |
| `EncodeCells(ev, geom)` / `EncodeCellsFromBytes(bytes, geom)` | `{rows, cols, cellPx, corner, widthPx, heightPx, frameLen, maxRows, overflow, cells = {{r,g,b}, …}}` with channels 0/1 and `rows*cols` cells row-major. With `geom.maxRows` adaptive (§4.3); `nil` only if more than 64 rows would be needed (impossible for valid frames) |
| `RowsCap(cellPx)`, `Capacity(rows)`, `AdaptiveLayout(frameLen)` | the v1.2 geometry (§4.3) |
| `NormalizeGeometry(geom)`, `RowsFor(frameLen, cols)`, `CellPosition(i, cols)` | geometry helpers |
| `FormatChatLine(ev)` / `FormatChatLineBytes(bytes)` | `"NDROP2 <base64url>"` |
| `PadLine(seq [, len])`, `PadLineCount(padBytes [, len])` | `"NDPAD <seq> ...."` (900 chars) and the line count for a padding size |
| `HasFlag(flags, f)` | whether bit `f` is set (non-numbers → false) |
| `Base64Url(bytes)` / `Base64UrlDecode(s)` | string / byte array or `nil` |
| `Fletcher16Bytes(bytes [, n])`, `Fletcher16String(s)`, `PlaceKey(dropId)` | Fletcher-16 (now only the placeKey's hash) |
| `Utf8Clean(s)`, `Utf8Truncate(s, maxBytes)`, `Utf8Valid(s)`, `SanitizeId(s, maxLen)`, `BuildQuery(artist, title)` | text helpers |
| `SpotifyIdFromLink(text)`, `YouTubeIdFromLink(text)` | id or `nil` |
| `Keystream(n)` | the first `n` whitening bits (self-tests) |
| constants | `MAGIC, VERSION, CHAT_PREFIX, EV` (with `CONFIG = 6`), `EV_NAME, FLAG, TAG` (with `PREFS, ACTION, CLIENT_ID`), `TYPE_TAGS, MAX_*, TRAILER_LEN` (4), `FRAME_OVERHEAD` (9), `MAX_FRAME` (264), `CHECK_SEED, CHECK_MUL, CHECK_MOD, MIN_QUERY_PART` (8), `PREFS_ORDERS, PREFS_CORNERS, ACTIONS, CLIENT_ID_LEN, HEADER, LEAD_CELLS, MIN/MAX_COLS, MAX_ROWS, MAX_STRIP_PX, CORNERS, DEFAULT_GEOMETRY, PAD_PREFIX, PAD_LINE_LEN, WHITEN_*` |

`ns.Beacon`: `Show(ev)`, `Layout()`, `Status()`, `ForceShow(sec)`, `Choose(ev, px, gap, corner)`,
`MeasureClearance()`, `RowsCapFor(px, gap)`, `Warnings()`, `SettingCellPx()`, `Corner()`, `PixelFactor()`,
`CellSize(px)`. `ns.ChatLink`: `Send(ev, opts)`, `SendLines(lines)`, `Flush()`, `Filter(...)`,
`InstallFilter()`, `EnsureLogging()`, `StopLogging()`, `IsLogging()`, `Restricted()`, `PadCount(bytes)`,
`Status()`. Emit: `ns.BuildEvent`, `ns.Emit`, `ns.ComputeFlags`, `ns.PreferredProvider`, `ns.PlaceLabel`,
`ns.Events`, `ns.LastEvent`.

### 6.2 Python (`needledropper.protocol2`)

| Function | Notes |
|---|---|
| `build_frame(type, *, seq, flags, drop_id, place_key, spotify, youtube, artist, title, start_ms, start_ds, length_ms, length_ds, volume, label, set_id, prefs, action, client_id, max_bytes, prefer)` | normalises and fits exactly like `EncodeFrame`; `prefs` is a mapping `{order, corner, fade, resume}` or a `Prefs`, `action` a mapping `{name, at}` or an `Action`; raises `ValueError` only for a type 7..15 |
| `frame_from_event(ev, *, max_bytes=None, prefer=None)` | the same, from a camelCase dict (`dropId, placeKey, startMs, startDs, lengthMs, lengthDs, setId, prefs, action, clientId, prefer`, …; its `prefer` is the default) |
| `encode_frame(frame)` | strict: raises `ValueError` (an id out of format, an enum name not in the lists, `issued_at` outside uint32, a client id that is not 32 hex digits, an `extra` tag ≤ 0x0D) |
| `decode_frame(data)` | `Frame` or `None` |
| `frame_check(data)` | the 4-byte trailer's value (§2) |
| `Frame` | `.type` (`EventType`, incl. `CONFIG`), `.seq`, `.flags`, `.place_key`, `.drop_id`, `.spotify_id`, `.youtube_id`, `.query`, `.start_ds`, `.length_ds`, `.volume`, `.label`, `.set_id`, `.prefs` (`Prefs` or None), `.action` (`Action` or None), `.client_id` (32 lowercase hex or None); views `.artist`, `.title`, `.search_text`, `.start_ms`, `.length_ms`, `.spotify`, `.youtube`, `.spotify_uri`, `.youtube_url`, `.key` = `(seq, type, place_key or 0, flags)`, `.has(flag)`, `.describe()`, `.to_bytes()`, `.raw`, `.extra` |
| `Prefs(order, corner, fade, resume)` | frozen; `order` in `PREFS_ORDERS`, `corner` in `PREFS_CORNERS`, `fade` / `resume` bools; None = not set (also an unknown code). `Prefs.coerce(value)`, `.from_codes(b)`, `.codes()`, `.describe()` |
| `Action(code, issued_at)` | frozen; `.name` (`ACTIONS[code - 1]`, None for an unknown code), `Action.named(name, at)`, `Action.coerce(value)`, `.value()`, `.describe()` |
| `Geometry(cells_per_row=64, cell_px=3, corner="TOPLEFT", max_rows=None)`, `Geometry.beacon(cell_px=3, max_rows=None)` | validated (`max_rows` ≤ `rows_cap(cell_px)`); `coerce` / `normalize` clamp like Lua |
| `encode_cells(frame_or_bytes, geom) -> CellGrid` | `rows`, `cols`, `cells` with RGB 0/255, `cell_px`, `width_px`, `height_px`, `overflow`, `as_rows()`, `codes()` |
| `encode_event_cells(ev, geom)` | `EncodeCells(ev, geom)`: fit to `capacity(geom.max_rows)`, then cells |
| `rows_cap`, `capacity`, `adaptive_layout`, `rows_for`, `strip_size`, `keystream_bits` | geometry and whitening helpers |
| `decode_cells_grid(rows_of_rgb, threshold=128)`, `decode_cell_codes(codes, cols, rows)` | `Frame` or `None` |
| `format_chat_line(frame_or_bytes)`, `parse_chat_line(line, *, strict=True)`, `find_chat_record` | chat line |
| `pad_line(seq, length=900)`, `pad_line_count(pad_bytes, length=900)` | `NDPAD` lines |
| `b64url_encode`, `b64url_decode` | base64url |
| `clean_text`, `utf8_clean_bytes`, `utf8_truncate`, `sanitize_id`, `build_query`, `place_key_for` | text helpers |
| constants | `MAGIC, VERSION, CHAT_PREFIX, TAG_*` (incl. `TAG_PREFS, TAG_ACTION, TAG_CLIENT_ID`), `TYPE_TAGS, MAX_*, TRAILER_LEN, FRAME_OVERHEAD, MAX_FRAME, CHECK_SEED, CHECK_MUL, CHECK_MOD, MIN_QUERY_PART, PREFS_ORDERS, PREFS_CORNERS, ACTIONS, CLIENT_ID_LEN`, … (`__all__`) |
| `spotify_id_from_link`, `youtube_id_from_link` | link parsers |

`needledropper.transport.beacon2`: `locate_and_decode`, `scan`, `Beacon2Result`, `ScanReport`, `HeaderHit`,
`CORNERS`, `CAPTURE_WIDTH`, `CAPTURE_HEIGHT`, `MAX_STRIP_WIDTH`, `MAX_STRIP_HEIGHT`, `capture_size(scale)`.

## 7. Test vectors

`tests/vectors/protocol2/frames.json` holds these, with the decoded fields; both encoders write exactly these
bytes and chat lines, and both decoders read them back (`test_documented_vectors`). The check parameters are in
the file's header (seed 1313092163, multiplier 48271, modulus 2^31 − 1, 2 final rounds, 4 bytes).

| Vector | Event | Size | Frame bytes | Chat line |
|---|---|---|---|---|
| `hello`: HELLO seq 0 (no music choices set) | `{type=1, seq=0}` | 9 B, 32 × 1 cells | `4e 21 00 00 00 29 50 ec 87` | `NDROP2 TiEAAAApUOyH` |
| `cancel`: CANCEL seq 42, flags DROP_ACTIVE, zone:1429 | `{type=4, seq=42, flags=0x20, dropId="zone:1429"}` | 24 B, 80 × 1 cells | `4e 24 2a 20 0f 01 02 51 c8 02 09 7a 6f 6e 65 3a 31 34 32 39 71 b6 90 06` | `NDROP2 TiQqIA8BAlHIAgl6b25lOjE0MjlxtpAG` |
| `arm`: ARM seq 8: area:87, YouTube link, Sigur Rós / Hoppípolla, start 12 s | `{type=2, seq=8, dropId="area:87", youtube="https://youtu.be/dQw4w9WgXcQ", artist="Sigur Rós", title="Hoppípolla", startMs=12000}` | 63 B, 96 × 2 cells | `4e 22 08 00 36 01 02 2f 44 02 07 61 72 65 61 3a 38 37 04 0b 64 51 77 34 77 39 57 67 58 63 51 05 16 53 69 67 75 72 20 52 c3 b3 73 1f 48 6f 70 70 c3 ad 70 6f 6c 6c 61 06 02 00 78 41 3d 15 68` | `NDROP2 TiIIADYBAi9EAgdhcmVhOjg3BAtkUXc0dzlXZ1hjUQUWU2lndXIgUsOzcx9Ib3Bww61wb2xsYQYCAHhBPRVo` |
| `hello-playing`: HELLO seq 9 with DROP_ACTIVE and the playing drop's placeKey (zone:1411; D3) | `{type=1, seq=9, flags=0x20, placeKey=18367}` | 13 B, 48 × 1 cells | `4e 21 09 20 04 01 02 47 bf 34 02 85 e7` | `NDROP2 TiEJIAQBAke_NAKF5w` |
| `hello-prefs`: HELLO seq 0, PREFS {spotify-youtube, topright, fade on, resume on} | `{type=1, seq=0, prefs={order="spotify-youtube", corner="topright", fade=true, resume=true}}` | 15 B, 48 × 1 cells | `4e 21 00 00 06 0b 04 01 01 01 01 65 4e 5a ef` | `NDROP2 TiEAAAYLBAEBAQFlTlrv` |
| `hello-prefs-flags`: HELLO seq 7, flags 0x20, PREFS {youtube-spotify, bottomleft, fade off, resume on} | `{type=1, seq=7, flags=0x20, prefs={order="youtube-spotify", corner="bottomleft", fade=false, resume=true}}` | 15 B, 48 × 1 cells | `4e 21 07 20 06 0b 04 02 04 02 01 53 9b 69 88` | `NDROP2 TiEHIAYLBAIEAgFTm2mI` |
| `hello-playing-prefs`: HELLO seq 9, DROP_ACTIVE, placeKey of zone:1411, PREFS {spotify-youtube, topright, on, on} | `{type=1, seq=9, flags=0x20, dropId="zone:1411", prefs={order="spotify-youtube", corner="topright", fade=true, resume=true}}` | 19 B, 64 × 1 cells | `4e 21 09 20 0a 01 02 47 bf 0b 04 01 01 01 01 48 94 80 fc` | `NDROP2 TiEJIAoBAke_CwQBAQEBSJSA_A` |
| `config-prefs`: CONFIG seq 12, PREFS {youtube, topleft, fade on, resume off} | `{type=6, seq=12, prefs={order="youtube", corner="topleft", fade=true, resume=false}}` | 15 B, 48 × 1 cells | `4e 26 0c 00 06 0b 04 04 02 01 02 05 5b 6e d2` | `NDROP2 TiYMAAYLBAQCAQIFW27S` |
| `config-prefs-action`: CONFIG seq 13, PREFS {spotify-youtube, topright, on, on} + test-youtube issued at 1762380000 | `{type=6, seq=13, prefs={order="spotify-youtube", corner="topright", fade=true, resume=true}, action={name="test-youtube", at=1762380000}}` | 23 B, 80 × 1 cells | `4e 26 0d 00 0e 0b 04 01 01 01 01 0c 06 04 fb 69 0b c8 e0 10 62 81 25` | `NDROP2 TiYNAA4LBAEBAQEMBgT7aQvI4BBigSU` |
| `config-connect`: CONFIG seq 14, connect-spotify issued at 1762380123 + CLIENT_ID 0123456789abcdef0123456789abcdef | `{type=6, seq=14, action={name="connect-spotify", at=1762380123}, clientId="0123456789abcdef0123456789abcdef"}` | 35 B, 112 × 1 cells | `4e 26 0e 00 1a 0c 06 01 fe 69 0b c9 5b 0d 10 01 23 45 67 89 ab cd ef 01 23 45 67 89 ab cd ef 52 ea a8 c4` | `NDROP2 TiYOABoMBgH-aQvJWw0QASNFZ4mrze8BI0VniavN71LqqMQ` |
| `config-bound`: Bound, never emitted (two rows): CONFIG seq 15, flags 0x03, PREFS {none, bottomright, off, off} + disconnect-spotify issued at 4294967295 + CLIENT_ID ff x 16 | `{type=6, seq=15, flags=0x03, prefs={order="none", corner="bottomright", fade=false, resume=false}, action={name="disconnect-spotify", at=4294967295}, clientId="ffffffffffffffffffffffffffffffff"}` | 41 B, 64 × 2 cells | `4e 26 0f 03 20 0b 04 05 03 02 02 0c 06 02 fd ff ff ff ff 0d 10 ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff 5f 0d de 31` | `NDROP2 TiYPAyALBAUDAgIMBgL9_____w0Q_____________________18N3jE` |
| `fire-fitted-39`: The F3 case: a search-only FIRE fitted to one strip row (39 B): artist and title both survive, maxBytes 39 | `{type=3, seq=5, dropId="area:87", artist="Godspeed You! Black Emperor", title="Moya", lengthMs=650000}` | 39 B, 112 × 1 cells | `4e 23 05 00 1e 01 02 2f 44 05 14 47 6f 64 73 70 65 65 64 20 59 6f 75 21 20 42 1f 4d 6f 79 61 07 02 19 64 3b c8 9a 22` | `NDROP2 TiMFAB4BAi9EBRRHb2RzcGVlZCBZb3UhIEIfTW95YQcCGWQ7yJoi` |

The HELLO strip at 64 cells is one row and starts `m g m g r k k w k r m …`:
* `r` is G0 = 4, i.e. 64 cells.
* `k k` are G1 = G2 = 0, i.e. one row.
* `w k r m` is the known prefix.

The v1.2 beacon draws the same HELLO adaptively: 32 cells (G0 = 2, green), one row, 96 × 3 px at 3 px.

Fit-rule vector (`FULL_USER_DROP` in the contract test, the 143-byte drop of §4.3): frame bytes at falling
budgets 143 → 133 (setId) → 115 (label) → 88 (query) → 61 (dropId) → 58 (volume) → 45 (Spotify preferred) or
34 (YouTube preferred); below that it stays 45 / 34. A search-only drop keeps its query through step 3 and is
shortened in step 5: `QUERY_ONLY` (坂本龍一 / 戦場のメリークリスマス twice, 140 B) keeps `坂本 0x1F 戦場の` at a
43-byte budget (42 B) and loses the whole query at 42.

## 8. Design decisions (refinements of the proposal)

1. **Three geometry cells, not two.** The proposal had W/16 and rows in 3 bits each (rows 1..8). At 64 cells per
   row, 8 rows hold only `(8·64 − 7)·3/8 = 189` bytes, and a 264-byte frame needs 12 rows (23 at 32 cells). With
   `rows − 1` in two cells (6 bits), every width 16..112 can carry a maximal frame.
2. **Whitening plus strict pad and row checks** as the "pad/terminator scheme" (§4.2). Plain zero padding plus
   Fletcher-16 would accept white-over-zero and black-over-0xFF covers. (v1.2.1 replaced Fletcher-16 as well: #10.)
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
10. **v1.2.1: a 4-byte polynomial frame check instead of Fletcher-16** (§2, §4.2; finding F1). A CRC would have
    needed bit operations, which addon Lua avoids (no `bit.*`); the multiplicative hash mod 2^31 − 1 is exact in
    Lua 5.1 doubles, costs one multiply-add per byte, and has no structural blind spot for the strip's
    row-periodic errors. Protocol v2 had not been released, so the version nibble stayed 2 (decision D1); 1.2.0
    and 1.2.1 cannot read each other's frames.
11. **v1.2.1: the fit rule never sends half a query** (§2.1 steps 3 and 5; findings F3, F4). A query without
    its separator made the app search an artist prefix as a title, and dropping the query whenever any id was
    present left a Spotify user with only a YouTube id nothing to search. The engine dedupes by key, so the
    beacon's frame, often the first to arrive, must carry what the song needs.
12. **v1.2.1: strict UTF-8 text in decoders** (§2.4; decision D5): encoders never write invalid UTF-8, so a
    frame whose query or label is invalid was damaged, and is rejected rather than repaired.
13. **v1.2.1: CONFIG is its own event type** (§2.5), so song frames never carry the music settings and 1.2.0's
    decoders reject it outright. PREFS are level-triggered (every HELLO repeats them); ACTIONs are edges
    identified by `(code, issuedAt)` with a check byte; no TLV carries text or a URL.

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
