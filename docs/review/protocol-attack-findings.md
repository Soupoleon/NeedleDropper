# Protocol v2 adversarial test findings

An adversarial end-to-end tester attacked the A3 cutover: Lua Emit → beacon textures → simulated captures (DPI scaling, HDR shifts, blur, noise, overlays, other strips) → beacon2 → transport logic, plus hostile chat lines. Verify each finding before fixing.

**Summary:** Adversarial test of A3's v1.2 protocol cutover. No project file was modified; every script is in C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv (call it DIR below). A3's own tests pass (736 passed).

**Main result: wrong frames do get through.** On multi-row strips, a static cover one cell column wide and as tall as the strip decodes to a different frame that still passes the checksum and every structural check (F1). The cause: each 112-column row is exactly 42 bytes, so one column changes the same bits in bytes i, i+42 and i+84, and Fletcher-16 cannot detect the change pattern (-d, +2d, -d). The full chain was reproduced from the real Lua textures:
- the addon emits the frame (Emit.lua and Beacon.lua under lupa) and it is rendered to a capture at 100/125/150/175 %;
- beacon2.scan decodes the wrong frame, PixelTransport accepts it after two identical reads, and the Engine plays the wrong YouTube id;
- when the cover leaves, the correct FIRE has the same Frame.key, so the engine drops it as a duplicate.

How common:
- Exhaustive search (cell-aligned covers up to 10 cells wide, 8 colours): 156 of 300 bundled FIRE frames (and 156/300 ARM) at 2 px and at 3 px can be broken this way; 51/300 by a black or white cover; 8/300 at 4 px; 0 of the one-row HELLO/CANCEL frames.
- Random 300k realistic captures: 7 wrong frames, all multi-row FIRE/ARM strips under covers. Wrong fields seen: Spotify ids, YouTube ids, placeKey, dropId, query, label, setId.

**Other findings:**
- F2 (medium): a FIRE is lost entirely when a cover of 2.5 s or longer overlaps its 3 s hold (5 s cover: 14 of 40 offsets). Nothing recovers it, and the Director has already marked the place heard.
- F3 (medium): fit-rule step 5 can cut off the 0x1F separator, so the companion treats a prefix of the artist as the title.
- F4 (medium): step 3 drops the search query for Spotify users when only a YouTube id exists. This hits 5/100 bundled drops at 4 px (still Core.lua's shipped default) and 99/100 in a one-row strip. The chat line cannot restore it, because the engine dedupes by key first.
- F5 (low): during chat lockdown ChatLink keeps CANCEL lines indefinitely; a 37 s old CANCEL stopped a later Replay of the same place.
- F6 (low): the 1 s CANCEL/PING hold is lost by a 0.5 s blip; the song then stops about 5 s late through the stale-HELLO rule.
- F7 (low): decoding a partly covered strip takes 35–78 ms on an idle machine, over the 30 ms target. Clean, degraded and header-flood captures stay under 28.9 ms.
- F8 (low): strict chat-log parsing rejects every single-line hostile format tested. Only a raw newline in a logged message could inject a record.
- F9 (low): the clearance guard does not measure a TargetFrame that was hidden at measure time.
- F10 (low): 2 px cells fail to decode (safely, never a wrong frame) under blur, chroma subsampling and HDR at 150 %.

**Checked and clean:**
- Lua/Python parity: 0 mismatches across about

## [high] F1: A static cover one cell column wide on a multi-row strip makes a different frame decode, pass both reads and play (Fletcher-16 blind spot)
- Reproduction: Interpreter: D:/NeedleDropper/.venv/Scripts/python.exe; DIR = C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv.

1. Cell level: `python -I DIR/repro_column_cover.py`. Takes the bundled Azeroth Mixtape area:150 FIRE at 3 px (3x112 cells) and paints column 87 black in every row. decode_cell_codes returns a frame with valid checksum and structure.

2. End to end from the real addon: `python DIR/e2e_vertical.py 1.0` (also 1.25, 1.5, 1.75; adds D:/NeedleDropper/tests to sys.path itself). BeaconHarness emits the FIRE through Emit.lua and Beacon.lua and renders the textures into a 512x48 BGRA capture. A 1 px line of RGB (47,49,54) is painted at x=262, y=0..8 (x=327 at 125 %, 393 at 150 %, 459 at 175 %). beacon2.scan decodes the wrong frame. PixelTransport.feed returns baseline, pending, emitted. The Engine (fake providers from tests/test_v12_engine_flows.py) plays YouTube 7C-VscEQuGk instead of 7C-VscEQugk. A second case, area:439 'Rocket Man' with a near-black line at column 30, changes placeKey 39029 to 39028 and the query, so the addon's later CANCEL (placeKey 39029) cannot stop it.

3. `python DIR/e2e_wrongframe.py`: a gold (255,209,0) patch at x 165-173, y 6-8 over a 3 px user-drop FIRE changes startDs 120 to 65400; the Engine calls Spotify play with startMs 6540000.

4. `python DIR/wrong_then_right.py`: when the cover leaves, the correct FIRE has the same Frame.key; the engine counts it as a duplicate and keeps playing the wrong video.

5. Sweeps:
   - `python -I DIR/wf_batch.py 10 24` (exhaustive; `DIR/wf_exhaustive.py lions` for one frame)
   - `python -I DIR/sweep_pixels.py 300000 24 2000000`; one seed: `python -I -c "import sys; sys.path.insert(0, r'DIR'); import sweep_pixels as sp; print(sp.case(2237080))"`
- Evidence: Cell-level repro: the changed bytes are 30, 72 and 114 (42 apart), values 103->71, 116->180, 97->65, i.e. deltas (-32, +64, -32). The YouTube id becomes 7C-VscEQuGk and the setId azeroth-mixtApe; Fletcher-16 and the TLV checks still pass.

Cause: with 3-bit cells, a row of 16k cells is exactly 6k bytes (112 cols = 42 B). A column cover therefore changes the same bit weights in bytes i, i+6k and i+12k. Fletcher-16's two sums are both zero for the second-difference pattern (-d, +2d, -d), so the error is invisible.

Exhaustive sweep (every cell-aligned rectangle up to 10 cells wide, every height, 8 uniform colours; cells from protocol2.encode_cells, byte-identical to Lua by the contract test):
- 640 of 3380 real frames have at least one cover that decodes wrong.
- Bundled FIRE: 156/300 at 2 px and 156/300 at 3 px; ARM the same. 51/300 have a black or white cover that works; 8/300 at 4 px.
- 0/1280 HELLO and 0/300 CANCEL.
- 716 of the 838 bad covers are one column wide and span every row.

Random realistic sweep:
- 7 wrong frames out of 300,000 captures; 119,829 of those captures had a cover.
- All 7 are multi-row FIRE/ARM strips; one was 96 columns wide, so the effect is not specific to 112.
- Causes: 5 thin vertical glyph strokes, 1 Discord-style toast, 1 opaque panel over 4.8 % of the strip.
- Wrong values seen: Spotify ids (p1KIb55qR2X5... -> p1KIb55QR2X5..., N8ZCAu1E0... -> J8ZCAu1E0...), YouTube ids (D56XxiS_pJ0 -> D56XxiS_hJ0, ozqw3UZRsIU -> ozpw3UZRsIU), placeKey, dropId, query, label, setId.

The chain is decoded by beacon2, accepted by PixelTransport (a static cover gives the same read twice) and acted on by the Engine.

Gaps in A3's tests: test_uniform_covers_fail_the_checks uses 32 columns and horizontal runs only; test_partial_occlusion_never_decodes_garbage tries 120 random rectangles on one frame. The PROTOCOL-v2 claims in §4.2 (whitening plus checksum catch uniform covers) and §4.4 (no wrong frame ever) do not hold.

The kind of cover that does this: a 1-2 px stroke or panel edge of near-uniform colour crossing one cell column over all rows (overlay text glyphs, another addon's frame edge), or a small uniform patch over specific cells.

Owner: A3 (Protocol2.lua, protocol2.py, beacon2.py, docs, tests); changing the frame format is a design decision.
- Suggested fix: Replace the Fletcher-16 trailer with a check that has no blind spot for this pattern:
- a CRC (CRC-16-CCITT, or CRC-32 for multi-row strips), or
- a polynomial hash sum(b_i * r^i) mod 65521 with r of large multiplicative order. This is exact in Lua 5.1 floats and needs no bit library.

Version the change on both sides: a new version nibble, or a trailer TLV the companion requires on multi-row strips.

Also:
- Validate query and label as strict UTF-8 in decode_frame and DecodeFrame. That would have caught the 0xB4 byte in the area:150 case, but not the ASCII-only corruptions.
- Add column covers at 112x3 and 96x3 to test_uniform_covers_fail_the_checks.
- Correct PROTOCOL-v2 §4.2 and §4.4.

## [medium] F2: A FIRE is lost entirely when a cover of 2.5 s or longer overlaps its 3 s hold, and nothing recovers it
- Reproduction: `D:/NeedleDropper/.venv/Scripts/python.exe C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/timeline_sim.py` (adds the tests/ path itself).

- The addon under lupa emits HELLO, then ARM(Durotar), then FIRE 5 s later.
- The companion captures at 4 Hz with a random phase and ±30 ms jitter.
- A cover of D seconds starts at 40 different offsets around the FIRE.
- Captures go through PixelTransport.feed into the Engine, whose clock follows the game clock.
- Example of a fully covered FIRE: fire_with_toast(7, 5.0, -0.5).
- Evidence: FIRE lost, by cover length D (out of 40 offsets each):
- 1 s: 0; 2 s: 0
- 2.5 s: 1; 3 s: 5; 3.5 s: 7; 4 s: 9
- 5 s: 14; 6 s: 17

When it is lost, the transport delivers ARM, ARM (placeKey only) and HELLO+DROP_ACTIVE, and the engine never plays: Engine._on_hello ignores DROP_ACTIVE when nothing is playing.

With the shipped defaults nothing else delivers it: Core.lua L144 sets `chatlog = false`. Director D.Fire calls D.MarkDone(d, 'played') before ns.Emit, so a missed FIRE leaves the place permanently 'heard' in game.

Owner: A3/design (hold rules), C1 (engine).
- Suggested fix: - Make the steady frame after a FIRE carry the place identity: HELLO+DROP_ACTIVE with the placeKey TLV, or an ARM-like identity frame. Then the engine can start the cached ARM when it sees DROP_ACTIVE for a place it armed but never saw fire.
- And/or lengthen the FIRE hold; DESIGN already budgets about 6 s per drop.
- Do not simply re-show the FIRE bytes later: the transport emits on change, so after the 15 s dedupe window the engine would restart the song.

## [medium] F3: Fit-rule step 5 can cut off the 0x1F separator, so the companion treats a prefix of the artist as the title
- Reproduction: `D:/NeedleDropper/.venv/Scripts/python.exe -I C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/fit_semantics.py`. It loads Protocol2.lua in a bytes-mode lupa runtime with a fresh ns and asserts the frame bytes are identical to protocol2.py's.

Case: P2.EncodeFrame({type=3, dropId='area:87', artist='Godspeed You! Black Emperor', title='Moya', lengthMs=650000}, 39), i.e. the one-row 2 px clearance fallback. Also run with the 81 B budget (the 4 px nominal) and a long artist.
- Evidence: At 39 B the frame's query is 'Godspeed You! Black Em'. Frame.artist is '' and Frame.title is 'Godspeed You! Black Em', so the engine searches that as a track title.

At 81 B, 'The Ólafur Arnalds Ensemble & the Icelandic Symphony Orchestra feat. Arnór Dan' / 'So Close' loses the title the same way.

`python -I DIR/utf8_check.py`: under random tight budgets, 7,042 of 23,475 fitted queries had lost the separator.

Lua and Python agree byte for byte, so this is the rule itself, not a parity bug. Owner: A3 (fitBody / _fit_body), DESIGN §2.3 step 5.
- Suggested fix: Shorten artist and title separately and keep the separator: reapply BuildQuery's fair split with the reduced budget, keeping a minimum of each part. Otherwise drop the whole query rather than send a fragment without a separator. Change both languages and the contract vectors together.

## [medium] F4: Fit-rule step 3 drops the query for Spotify users when only a YouTube id exists, and the chat line cannot restore it
- Reproduction: `D:/NeedleDropper/.venv/Scripts/python.exe -I C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/fit_semantics.py`.

- The real Sets/*.lua drops are loaded by sets_events.py with prefer='spotify' and fitted at 39/81/123/165 B.
- The same event's beacon frame (81 B) and chat frame (262 B) go into an Engine whose fake Spotify has a search match.
- The run does beacon first, then the chat frame alone.
- Evidence: Bundled drops that lose the query:
- 5/100 at 81 B (2 rows, the 4 px nominal): area:150, area:380, area:439, zone:1426, zone:1454.
- 99/100 at 39 B (one row).
- 0 at 123 and 165 B.

Engine with the beacon frame first: duplicates 1, no Spotify search, YouTube plays. With the chat frame alone: Spotify plays the search match. Engine._accept dedupes by Frame.key before any merge, so whichever transport lands first wins.

Integration risk: Core.lua L143 still ships `beaconCellPx = 4` (min 1, max 16). Until the integrator changes it, the 81 B budget is the default.

Owner: A3/design (fit rule), C1 (dedupe), integrator (default).
- Suggested fix: - Step 3: drop the query only when the preferred provider's id is present (prefer='spotify' requires a Spotify id).
- Engine: when a duplicate key arrives, merge the fields it adds, or keep the fuller frame, instead of discarding it.
- Integrator: ship beaconCellPx = 3.

## [low] F5: During chat lockdown ChatLink keeps CANCEL lines indefinitely; a stale CANCEL later stops a replay of the same place
- Reproduction: `D:/NeedleDropper/.venv/Scripts/python.exe C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/stale_cancel.py` (adds the tests/ path itself).

- chatlog on; SHIM.state.chatLockdown = true for 40 s.
- Emit FIRE A (seq 1), CANCEL A (seq 2), then a Replay FIRE A+TEST (seq 3).
- The beacon delivers all three to the Engine.
- Lockdown ends, and the flushed chat lines go through chatlog.parse_line into the same Engine.
- Evidence: The flushed queue writes only `CANCEL seq=2 ... id=zone:1411`. Both FIRE lines were aged out (ChatLink.Status stale=2).

The engine receives the CANCEL 37 s late. That is past the 15 s dedupe window, so it is accepted as new, and it stops the replayed drop: the YouTube fake records two stop calls, and is_playing is false afterwards.

Owner: A3 (ChatLink.Flush ages only FIRE lines) and C1 (the engine accepts a seq older than the one playing).
- Suggested fix: - ChatLink: age out CANCEL and PING lines too, or drop a queued CANCEL when a later FIRE/ARM of the same dropId is queued after it.
- Engine: ignore a CANCEL whose seq is older (mod 256 window) than the FIRE currently playing that placeKey.

## [low] F6: The 1 s CANCEL/PING hold is lost by a 0.5 s blip; the song then stops about 5 s late
- Reproduction: `D:/NeedleDropper/.venv/Scripts/python.exe C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/timeline_sim.py cancel-only`. Same rig as F2, with the cover placed around a CANCEL.
- Evidence: CANCEL lost, by cover length (out of 30 offsets each): 0.5 s: 6; 1 s: 10; 1.5 s: 13; 2 s: 17; 5 s: 24.

A 1 s hold gives about 4 captures. Two blanked reads in the middle leave no pair of consecutive identical reads.

In every case the song stopped later through the HELLO-without-DROP_ACTIVE rule (5 s), so the cost is a late stop with no fade-on-cue, not a stuck song. A lost PING means the self-test tick does not appear.

Owner: A3/design (hold rules).
- Suggested fix: Hold CANCEL and PING for at least 2 s (about 8 captures). Optionally carry the CANCEL's placeKey in the HELLO that follows it.

## [low] F7: Decoding a partly covered strip exceeds the 30 ms budget in PROTOCOL-v2 §4.4
- Reproduction: Both run on an idle machine:
- `D:/NeedleDropper/.venv/Scripts/python.exe -I C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/perf.py 8`: single-run scan times at 100/125/150/175 % for 2/3/4 px under clean, noise, blur, sharpen, JPEG-like, HDR, overlay and header-flood conditions.
- `.../a3adv/retime.py 40`: re-times the slowest seeds of sweep_pixels.py serially, 3 runs each.
- Evidence: Within budget: clean, degraded-but-decodable and header-flood captures all stay under 30 ms (max 28.9 ms, header flood at 175 %, 2 px).

Over budget: partly covered strips, where the header is confirmed but the payload is invalid, take 35-78 ms serially. Examples:
- seed 2215248: 69-78 ms
- seed 2232229: 66 ms
- seed 2232245: 65-83 ms
- also 2216652, 2245332, 2228509, ...

In the 24-process sweep, 2 % of cases took over 30 ms (p99 64 ms, max 408 ms), inflated by the parallel load.

The 300 ms backstop only stops new fallback attempts from starting. Owner: A3 (beacon2.py).
- Suggested fix: Time-box the whole scan, e.g. stop starting fallbacks after about 20 ms elapsed instead of relying only on node budgets and the 300 ms backstop. Skip the heavy and exact passes when the cheap lattice already read the header and the known prefix cleanly: the failure is then in the payload, not the lattice.

## [low] F8: Strict chat-log parsing rejects hostile single-line chat; only a raw newline in a logged message could inject a record
- Reproduction: `D:/NeedleDropper/.venv/Scripts/python.exe -I C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/chat_corpus.py`. Each corpus line goes through ChatLogTransport._feed (splits on \n) and protocol2.parse_chat_line(strict=True).
- Evidence: Rejected:
- say, yell, /e emote, whisper, Battle.net whisper
- trade channel, raid warning, AFK reply, guild MOTD
- a custom channel named NDROP2
- a record with trailing text
- a fake timestamp inside a say
- a smuggled NDPAD

Accepted besides the real line:
- a bare `NDROP2 <token>` line (LINE_START_RE)
- `<timestamp>  NDROP2 <token>` on the line after a raw newline

So any logged message able to carry a raw \n would inject a frame. Multi-line Battle.net whispers are one possible source; not verified in client.

\d and \s are Unicode-aware: Arabic-Indic digits and U+3000 are accepted, but that only matters together with newline injection.

Owner: A3 (protocol2.py regexes) and C1 (chatlog.py).
- Suggested fix: - Remove LINE_START_RE (WoW always timestamps WoWChatLog.txt).
- Compile STRICT_LINE_RE with re.ASCII and a literal two-space separator.
- Check in game whether logged messages can contain raw newlines.

## [low] F9: The clearance guard never measures a TargetFrame that was hidden at measure time
- Reproduction: `D:/NeedleDropper/.venv/Scripts/python.exe C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/target_frame.py`.

- PlayerFrame art at 14 px.
- TargetFrame art at 7 px, hidden at login and shown later (the player targets something).
- Then UI_SCALE_CHANGED is fired.
- Evidence: After the TargetFrame appears, Status still reports gap 14 and the strip stays 240x9. It does not avoid the 7 px TargetFrame art until UI_SCALE_CHANGED; then the gap is 7 and the strip becomes 2 px cells, 192x4. There is no PLAYER_TARGET_CHANGED re-measure.

On the forever branch, TargetFrameContainer (~L59) and FrameTexture (~L78) exist, and the boss border is inset. So the impact is small unless the target frame sits higher than the player frame.

Owner: A3 (Beacon.lua).
- Suggested fix: Re-measure on PLAYER_TARGET_CHANGED and when PlayerFrame or TargetFrame show. Alternatively measure the anchored position of hidden regions too.

## [low] F10: 2 px cells (the clearance fallback) fail to decode under blur, chroma subsampling and HDR at 150 %, though never wrongly
- Reproduction: `D:/NeedleDropper/.venv/Scripts/python.exe -I C:/Users/<user>/AppData/Local/Temp/claude/ad490dc250b63cb0/2f09d338-c3d1-4f2b-9a09-39854eb1d4ef/scratchpad/a3adv/sweep_pixels.py 300000 24 2000000`, plus perf.py. capture_sim.py holds the filters: bilinear and nearest magnification, tone, 3x3 blur, unsharp mask, JPEG-like 8x8 blocks with 4:2:0 chroma.
- Evidence: With background only, decoding succeeds about 99.99 % of the time at every scale and cell size (4 failures in about 45k).

At 2 px:
- 125 % with JPEG-like chroma: 672/672 fail to decode.
- 100 % with a full 3x3 box blur: 0/8 decode.
- 150 % bilinear with HDR tone: 0/8 decode.
- 100 % blur plus tone: 33.6 % fail.

Filters alone never produced a wrong frame. This matters because the clearance guard falls back to 2 px cells. Owner: A3/design.
- Suggested fix: Informational. Prefer 3 px whenever the clearance allows it, and document the 2 px limits in TROUBLESHOOTING.
