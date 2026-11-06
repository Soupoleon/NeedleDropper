# Pack media audit: Azeroth Mixtape and Azeroth Unbound

*2026-11-05. All 100 drops of the two bundled soundtracks (`packs/azeroth-mixtape.json`, 38 drops;
`packs/azeroth-unbound.json`, 62 drops) were checked in a real embedded YouTube player, and each
was pinned to its exact Spotify recording.*

## Result

| | Before | After |
|---|---|---|
| Drops with a YouTube id | 99 of 100 (Azshara had none) | **100 of 100** |
| YouTube ids that play when the page is `http://localhost:<port>` | 99 of 99 | **100 of 100** |
| YouTube ids that play when the page is `http://127.0.0.1:<port>` (the player page today) | 21 of 99 | 21 of 100 |
| Non-excerpt drops whose YouTube upload matches `durationMs` within 3 s | 89 of 94 | **95 of 95** |
| Drops pinned to a Spotify track (`"spotify": "spotify:track:<id>"`) | 0 | **100 of 100** |
| `durationMs` values changed | | 0 (largest Spotify gap 2.76 s) |

**Changes:** 5 YouTube ids replaced, 0 removed, 1 added (Azshara); 100 Spotify ids pinned; no lengths
changed; 0 drops unresolved. Both packs validate with the companion loader (`ok`, no errors, warnings
or issues) and with `tools/gen_sets.py` (run into a scratch folder, so no addon files were written).
**`Sets/*.lua` need regenerating.**

## The YouTube failures come from the player page's origin, not from the videos

The embed check loads each id muted in the YouTube IFrame Player API, from a local page with
`origin: location.origin`, the way `companion/needledropper/deck/static/player.js` does. An id passes
only when the player reports PLAYING for that same video. Two controls run every time: the API demo
video `M7lc1UVf-VE` must play and the nonexistent id `zzzzzzzzzz0` must fail. Both behaved correctly
in every run.

- **From `http://127.0.0.1:<port>`** (the origin the deck builds in `deck/server.py:628`), 78 of the
  99 ids failed with **error 150**. That includes all 38 Mixtape ids. The only ids that played were 21
  "Topic" uploads by 17 artists, mostly on independent labels: AURORA, Beirut, Boards of Canada, Bon Iver, Fleet
  Foxes, Gregory Alan Isakov, Hania Rani, Iron & Wine, Jon Hopkins, Kiasmos, Lisa Hannigan, Mogwai,
  Nils Frahm, Silent Poets, Tycho, Wardruna and Ólafur Arnalds.
- **From `http://localhost:<port>`**, in the same browser a minute later, **99 of 99 played**. That
  includes Durotar's `d_vPJjqWxZM`, which `docs/research/no-focus-playback.md` §3.7 reported as not
  embeddable. `http://needledropper.localhost:<port>` behaves the same as `localhost`, and replacing
  the headless user agent with a normal Chrome one changes nothing.
- **Neither oEmbed nor YouTube's own `playableInEmbed` flag is a check.** Both say yes for ids that
  fail from 127.0.0.1.
- **Swapping ids does not get around it.** For Toto "Africa", Adele "Skyfall", Low Roar "I'll Keep
  Coming" and Radiohead "Daydreaming", every other official upload tried also failed from 127.0.0.1
  with 150: VEVO videos, label and artist channels, and other Topic listings. The one exception was the
  Low Roar music video on the band's self-run LOWROARMUSIC channel, which is a different 6:08 cut.

**Fix (companion, outside this audit's files):** serve the YouTube player page as
`http://localhost:<port>/player`. `Deck.player_url` (`deck/server.py:640`) builds on `base_url`
(`deck/server.py:628`, `http://127.0.0.1:{port}`). The DNS-rebinding guard already accepts
`Host: localhost:<port>` and `Origin: http://localhost:<port>` (`deck/server.py:655-666`). The Spotify
OAuth redirect must stay on 127.0.0.1, because Spotify rejects `localhost` there; only the player page
moves. Until it moves, 79 of the 100 bundled YouTube drops cannot play. Once it has moved, check
with `--page-host localhost`, as in [Reproduce](#reproduce).

## What changed in the packs

### YouTube: 5 replaced, 1 added

Each replaced id was an official music video cut differently from the recording. Playing it from 0:00
for `durationMs` therefore played a video intro and stopped before the song ended. In the worst case,
"River" is a 3:59 song inside a 6:55 film.

| Drop | Before | After |
|---|---|---|
| Tanaris (Mixtape): Darude, "Sandstorm" | `y6120QOlsfU`, Darude channel video, 3:52 (+6.6 s) | `XjvkxXblpz8`, Darude - Topic, "Sandstorm (Radio Edit)", 3:47 (+0.9 s) |
| Karazhan (Mixtape): Queen, "Bohemian Rhapsody" | `fJ9rUzIMcZQ`, Queen Official "Official Video Remastered", 5:59 (+5.1 s) | `BSTsnWoslP4`, Queen - Topic, *A Night At The Opera*, 5:54 (+0.0 s) |
| Ironforge (Mixtape): Wind Rose, "Diggy Diggy Hole" | `34CZjsEI1yU`, Napalm Records official video, 5:41 (+25.3 s) | `0FvHyHaT8ys`, Wind Rose - Topic, 5:16 (+0.0 s) |
| Riverglades (Mixtape): Leon Bridges, "River" | `0Hegd4xNfRo`, official video, 6:55 (+176.7 s) | `7ug41DBUJOw`, Leon Bridges - Topic, *Coming Home*, 3:59 (+0.0 s) |
| Zephras Isle (Mixtape): Electric Light Orchestra, "Mr. Blue Sky" | `aQUlA8Hcv4s`, ELOVEVO official video, 4:55 (−9.2 s) | `bJ8Sz8CJY5g`, ELO - Topic, *Out of the Blue*, 5:03 (−0.4 s) |
| Azshara (Unbound): M83, "Wait" | none | `iQnRCdtECl8`, M83 - Topic, *Hurry Up, We're Dreaming*, 5:43 (+0.3 s) |

For all six new ids, oEmbed confirms the uploader is the `<Artist> - Topic` channel. Each played from
localhost in four separate runs, and in one of them it was still playing at 20 s. Like the ids they
replace, they fail from 127.0.0.1. The old note that M83's Topic uploads "ask viewers to sign in"
traces back to YouTube's bot interstitial ("Please sign in. This helps protect our community"), which
a signed-out plain-HTTP fetch of the watch page gets. The embedded player played the video in every
run. A second M83 - Topic upload, `60-UeAOV5tI`, is the same length and can serve as an alternate.

### Spotify: 100 pinned

- **How the pins were chosen.** Three or four Spotify searches per drop were prefetched in one paced
  process. Four resolver agents chose from them (25 drops each). Four adversarial reviewers re-fetched
  every pin and looked for a better release: they confirmed 100 of 100 and changed none. A final
  scripted re-fetch of all 100 checked artist, title, length and the explicit flag against the pack.
- **Lengths.** Of the 95 non-excerpt drops, 29 are within 10 ms of `durationMs`, 86 within 1 s, and 9
  between 1 and 2.76 s. None is over 3 s, so no `durationMs` changed. The 5 excerpt drops are pinned
  to the full track and keep their 6:30 `durationMs`.
- **Releases.** The original album or single was preferred over compilations, deluxe reissues and
  third-party soundtrack licences. Where the YouTube upload names a remaster, that same remaster was
  pinned. 89 pins are album tracks and 6 are singles. Spotify labels 5 releases as compilations: three
  official film soundtracks (*How to Train Your Dragon*, *Pirates of the Caribbean*, *Call Me by Your
  Name*) and two recordings that exist nowhere else on Spotify, the "Brothers In Arms" single edit and
  the "Cities in Dust" single version.
- **Explicit.** No explicit track was pinned when a clean one exists. Two recordings are explicit-only
  on Spotify. Guns N' Roses, "Welcome to the Jungle" (Stranglethorn Vale, Mixtape): the clean listings
  are the 1986 Sound City Session and live takes, which are different recordings. Bon Iver, "Holocene"
  (Winterspring, Unbound): both releases are flagged explicit, with the same ISRC. *Not verified:* an
  account with Spotify's explicit filter on would refuse these two tracks, and the app should then fall
  back to YouTube.
- **Not checked: market availability.** For this app Spotify no longer returns `available_markets`
  or `popularity`, and the token has no scope for `market=from_token`.

### Judgment calls worth a glance (20)

The resolvers flagged 19 of these and a reviewer flagged Tycho. All 20 are within 3 s of
`durationMs`.

- **Mixtape, Teldrassil.** Enya, "May It Be": the 2002 single (3:31). The *Fellowship* soundtrack cut
  is a longer version.
- **Mixtape, Darnassus.** Céline Dion, "All by Myself": the original *Falling into You* track
  (+1.45 s). A 2024 soundtrack re-release (`49tbDrswKv7ivUgGlEddQQ`, new ISRC) matches the pack and the
  YouTube Topic upload to the millisecond.
- **Mixtape, Tirisfal Glades.** Evanescence, "Bring Me to Life": the 2023 remaster that the YouTube
  upload names (+0.5 s). The 2003 master `0COqiPhxzoWICwFCS4eZcp` matches `durationMs` exactly.
- **Mixtape, Tarren Mill.** Dire Straits, "Brothers in Arms": "Brothers In Arms (Edit) - 2022
  Remaster" on *Money For Nothing*. It matches the note's "4:58 single edit" and `durationMs` exactly,
  and it is the only 4:58 edit on Spotify.
- **Mixtape, Stranglethorn Vale.** Guns N' Roses, "Welcome to the Jungle": explicit only (see above).
- **Mixtape, Menethil Harbor.** Otis Redding, "(Sittin' On) The Dock of the Bay": the mono mix, which
  the YouTube upload also plays. The stereo track is +3.1 s.
- **Mixtape, Undercity.** Siouxsie and the Banshees, "Cities in Dust": the single version on the band's
  *The Best Of...*, an exact match. The *Tinderbox* album cut is a different, 12 s shorter recording.
- **Unbound, Skywatcher Plateau.** Tycho, "Awake": Spotify lists the album twice. The alternate id is
  `3BowGuMV7r1PYtSXeB6zlR`.
- **Unbound, Moonglade.** Brian Eno, "An Ending (Ascent)": the 2019 remaster on the *Apollo* Extended
  Edition, the one the YouTube upload names.
- **Unbound, Winterspring.** Bon Iver, "Holocene": explicit only (see above).
- **Unbound, Orgrimmar.** Bring Me The Horizon, "Ludens": the clean 2019 single (+2.2 s), chosen over
  the explicit album track that matches the YouTube length. Both carry the same ISRC.
- **Unbound, Badlands.** Ry Cooder, "Paris, Texas": the 1984 soundtrack album is not on Spotify, so
  this is the original recording on the 1995 collection *Music by Ry Cooder* (+2.8 s).
- **Unbound, Eastern Plaguelands.** Max Richter, "On the Nature of Daylight": the 2004 recording,
  on *The Blue Notebooks (15 Years)*.
- **Unbound, Light's Hope Chapel.** Sigur Rós, "Hoppípolla": the *Takk...* album track.
- **Unbound, Dun Morogh.** Ásgeir, "King and Cross": *In The Silence (Bonus Edition)*, the only
  edition of the album on Spotify.
- **Unbound, Searing Gorge.** Jon Hopkins, "Open Eye Signal": the *Immunity* "Remaster 2023", which
  the YouTube upload names. This is an excerpt drop, so its length is kept.
- **Unbound, Wetlands.** Novo Amor, "Anchor": the vocal recording on the *Bathing Beach* EP.
- **Unbound, Stormwind City.** Of Monsters and Men, "King and Lionheart": Spotify lists the album
  twice. The alternate id is `5HF5PRNJ8KGtbzNPPc93tG`.
- **Unbound, Ironforge and Undercity.** Woodkid, "Iron", and Apparat, "Goodbye": the album versions
  that the pack lengths match. The EP and single edits are different.

## Still open

1. **Companion: move the player page to localhost** (see the fix above). This blocks YouTube playback
   for 79 of the 100 drops.
2. **Regenerate the Sets:** `python tools/gen_sets.py`.
3. **`tests/test_setgen.py:238` will fail after regeneration.** It asserts that Azshara (`zone:1447`)
   has no YouTube id; it should now expect `iQnRCdtECl8`.
4. **`docs/research/no-focus-playback.md` §3.7 depends on the origin:** from localhost the Durotar
   video plays.
5. **Check M83 "Wait" once in the real player** (see the YouTube section). If it fails, the alternate
   is `60-UeAOV5tI`.
6. **Region.** Topic uploads can be region-limited, and everything here was tested from this PC's
   location only.
7. **`docs/MIXTAPE.md`** still says every drop has an official YouTube upload "that allows embedding,
   so ... it plays without connecting any account". That is true from localhost, and false from
   today's 127.0.0.1 player page.
8. **Explicit filter.** Spotify accounts with the explicit filter on (two drops, above) were not tested.
9. **Beacon frames are fuller.** Every drop now carries a 22-character Spotify id. The frames were
   simulated with `companion/needledropper/protocol2.build_frame` at the 3-row capacity (123 bytes).
   33 drops now shed their set id. Four Mixtape landmarks also shed their label: Menethil Harbor, The
   Crossroads, The Shimmering Flats and Dun Morogh. The Shimmering Flats ("Rocket Man") also sheds its
   artist/title text. No provider id is ever shed, so playback is unaffected. Durotar keeps its label,
   so `tests/test_addon_e2e_durotar.py:89` should still pass.

## Method and deviations

- **Headless Chrome instead of headless Edge.** On this PC `msedge.exe` has an Image File Execution
  Options `Debugger` hook (`C:\Program Files (x86)\Wedge\wedge.exe`, filtered to the Edge exe; see
  `no-focus-playback.md` §3.6). The hook could hand the check URL to a visible default browser.
  `yt_embed_check.py` detects the hook and refuses to launch Edge, so every check ran in Chrome 136:
  `--headless=new --mute-audio`, a throwaway `--user-data-dir`, and the whole process tree killed after
  each pass. It is the same Chromium engine, and error 150 is decided by YouTube's servers. No window
  ever opened.
- **Spotify.** Calls used the scratch test player's existing sign-in (`poc_player.Player().sp`), and
  only `GET /search` and `GET /tracks/<id>`, with no `market`. They ran one at a time behind a
  machine-wide lock file, at least 0.5 s apart, mostly through one lookup server process. That came
  to about 325 searches and 305 track lookups, with no rate limiting. The shared token was refreshed
  once, by the provider's own code. For this app Spotify rejects search `limit` above 10; the tool
  clamps it.
- **Where the edits are.** The packs, the two docs and the new files were edited in the session
  worktree, because writes to `D:\NeedleDropper` were blocked from this session. A patch against the
  main checkout carries them across.

## Tools (`tools/pack_checks/`, standard library only)

| File | What it does |
|---|---|
| `yt_embed_check.py` | Real-player embeddability check. Takes `--pack` or ids, uses `--page-host` (`127.0.0.1` by default, or `localhost`), runs the controls, retries timeouts once, and reports the player's own length and channel. Exit 0 = all ok, 1 = some fail, 3 = the controls failed. |
| `yt_search.py` | YouTube search results (channel, badge, length, description snippet) and `--oembed` uploader lookups. |
| `spotify_lookup.py` | Paced Spotify `search` / `track` lookups through the test player's sign-in, `candidates` prefetch for whole packs, and a single-process `serve` mode. |
| `apply_media.py` | Applies a decisions file (`spotify` / `youtube` / `durationMs` per drop) to packs. It checks the artist and title before each change and keeps the pack format and key order. |

## Reproduce

```
python tools/pack_checks/yt_embed_check.py --pack packs/azeroth-mixtape.json --pack packs/azeroth-unbound.json --page-host localhost
python tools/pack_checks/yt_embed_check.py --pack packs/azeroth-mixtape.json --pack packs/azeroth-unbound.json
python tools/pack_checks/spotify_lookup.py track 4w3tQBXhn5345eUXDGBWZG
python tools/gen_sets.py --check
```

## Per drop

YouTube statuses read "127.0.0.1 result / localhost result", followed by the length the player
reported; `150` is IFrame error 150. "Spotify length − durationMs" compares the pinned track with the
pack's `durationMs`, which no drop changed.

| # | Pack | Place | Artist — Title | YouTube before (127.0.0.1 / localhost, length) | YouTube after | Spotify track id | Spotify release | Spotify length − durationMs | Flags |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Mixtape | Durotar | Dolly Parton — 9 to 5 | `d_vPJjqWxZM` 150 / ok, 2:46 | unchanged | `4w3tQBXhn5345eUXDGBWZG` | 9 To 5 And Odd Jobs (1980) | +1.2 s |  |
| 2 | Mixtape | Mulgore | John Barry — The John Dunbar Theme | `r2rAYwhM0Kc` 150 / ok, 2:17 | unchanged | `4VyuAqNcplPZkNGLyNza4H` | Dances With Wolves: Original Motion Picture Soundtrack (1990) | -0.0 s |  |
| 3 | Mixtape | The Barrens | Toto — Africa | `QAo_Ycocl1E` 150 / ok, 4:56 | unchanged | `2374M0fQpWi3dLnB54qaLX` | Toto IV (1982) | -0.0 s |  |
| 4 | Mixtape | The Crossroads (The Barrens) | U2 — I Still Haven't Found What I'm Looking For | `e3-5YC_oHjE` 150 / ok, 4:35 | unchanged | `3MRQ3CSjoiV1HFil8ykM9M` | The Joshua Tree (1987) | +0.0 s |  |
| 5 | Mixtape | Teldrassil | Enya — May It Be | `acM4c1BYVlU` 150 / ok, 3:31 | unchanged | `1wIQ2Nm0raqOapHK4bCD3J` | May It Be (2002) | -0.0 s | judgment call |
| 6 | Mixtape | The Shimmering Flats (Thousand Needles) | Elton John — Rocket Man (I Think It's Going to Be a Long, Long Time) | `DtVBCG6ThDk` 150 / ok, 4:42 | unchanged | `3gdewACMIVMEWVbyb8O9sY` | Honky Chateau (1972) | +0.0 s |  |
| 7 | Mixtape | Tanaris | Darude — Sandstorm | `y6120QOlsfU` 150 / ok, 3:52 | **`XjvkxXblpz8`** 150 / ok, 3:47 (Darude - Topic) | `3dxDj8pDPlIHCIrUPXuCeG` | Before the Storm (2000) | -0.0 s |  |
| 8 | Mixtape | Gadgetzan (Tanaris) | ABBA — Money, Money, Money | `1LPNuFkteI8` 150 / ok, 3:06 | unchanged | `3lKN03PDeBnXZS7sDukRBt` | Arrival (1976) | +0.4 s |  |
| 9 | Mixtape | Felwood | Britney Spears — Toxic | `7l1A2gShWZo` 150 / ok, 3:19 | unchanged | `6I9VzXrHxO9rA9A5euc8Ak` | In The Zone (2003) | +0.0 s |  |
| 10 | Mixtape | Un'Goro Crater | John Williams — Theme from Jurassic Park | `lDlU08RU7Tk` 150 / ok, 3:27 | unchanged | `2TZbQZXOuR8osP2AK8yYMN` | Jurassic Park (1993) | -0.0 s |  |
| 11 | Mixtape | Silithus | Kansas — Dust in the Wind | `9fNrJMkTWEE` 150 / ok, 3:26 | unchanged | `6zeE5tKyr8Nu882DQhhSQI` | Point Of Know Return (Expanded Edition) (1977) | -0.0 s |  |
| 12 | Mixtape | Everlook (Winterspring) | Vanilla Ice — Ice Ice Baby | `jewmMjnbfzE` 150 / ok, 4:31 | unchanged | `11d9oUiwHuYt216EFA2tiz` | To The Extreme (1990) | -0.0 s |  |
| 13 | Mixtape | Orgrimmar | Hans Zimmer & Lisa Gerrard — Now We Are Free | `ghxzLw2wRis` 150 / ok, 4:14 | unchanged | `1elGwF4VwkwglV4nCBPJtv` | Gladiator - Music From The Motion Picture (2000) | +0.0 s |  |
| 14 | Mixtape | Thunder Bluff | Tom Petty — Free Fallin' | `1lWJXDG2i0A` 150 / ok, 4:15 | unchanged | `5tVA6TkbaAH9QMITTQRrNv` | Full Moon Fever (1989) | +0.3 s |  |
| 15 | Mixtape | Darnassus | Céline Dion — All by Myself | `3p-rsl10t9I` 150 / ok, 5:11 | unchanged | `0gsl92EMIScPGV1AU35nuD` | Falling into You (1996) | +1.4 s | judgment call |
| 16 | Mixtape | Mount Hyjal | Queen — Who Wants to Live Forever | `cHh05dFIDmk` 150 / ok, 5:16 | unchanged | `4IgwcozjYTR0MFrHurw1un` | A Kind Of Magic (1986) | +0.0 s |  |
| 17 | Mixtape | Shen'dralas | Coldplay — Viva la Vida | `dvgZkm1xWPE` 150 / ok, 4:02 | unchanged | `1mea3bSkSGXuIRvnydlB5b` | Viva La Vida or Death and All His Friends (2008) | +0.9 s |  |
| 18 | Mixtape | The Dark Portal (Blasted Lands) | AC/DC — Highway to Hell | `l482T0yNkeo` 150 / ok, 3:27 | unchanged | `2zYzyRzz6pRmhPzyfMEC8s` | Highway to Hell (1979) | -0.0 s |  |
| 19 | Mixtape | Tirisfal Glades | Evanescence — Bring Me to Life | `Yhwal9i1uyw` 150 / ok, 3:56 | unchanged | `2UYK6Xdo2519MY0o2NtA3n` | Fallen (Deluxe Edition / Remastered 2023) (2023) | +0.5 s | judgment call |
| 20 | Mixtape | Light's Hope Chapel (Eastern Plaguelands) | Jeff Buckley — Hallelujah | `hjn13QP05xM` 150 / ok, 6:53 | unchanged | `3pRaLNL3b8x5uBOcsgvdqM` | Grace (1994) | +0.5 s |  |
| 21 | Mixtape | Southshore (Hillsbrad Foothills) | Edwin Starr — War | `gKAM1NfMXY8` 150 / ok, 3:20 | unchanged | `4ZEEcEMGjkWOfZNBcIB9yE` | War And Peace (1970) | -0.0 s |  |
| 22 | Mixtape | Tarren Mill (Hillsbrad Foothills) | Dire Straits — Brothers in Arms | `jhdFe3evXpk` 150 / ok, 4:59 | unchanged | `3eIRJjo9p51WRInZbEe7RW` | Money For Nothing (2022 Remaster) (1988) | +0.0 s | judgment call |
| 23 | Mixtape | Aerie Peak (The Hinterlands) | John Powell — Test Drive | `IpPIK4T068s` 150 / ok, 2:36 | unchanged | `2QTDuJIGKUjR7E2Q6KupIh` | How To Train Your Dragon (Music From The Motion Picture) (2010) | -0.0 s |  |
| 24 | Mixtape | Dun Morogh | Richard Armitage & The Dwarf Cast — Misty Mountains | `s4LX9NX7hS4` 150 / ok, 1:43 | unchanged | `3AAwJ043uT1wXSKDtGyuxF` | The Hobbit: An Unexpected Journey (Original Motion Picture Soundtrack) (2012) | -0.0 s |  |
| 25 | Mixtape | Burning Steppes | Billy Joel — We Didn't Start the Fire | `WqkjYKUXERQ` 150 / ok, 4:52 | unchanged | `3Cx4yrFaX8CeHwBMReOWXI` | Storm Front (1989) | +0.0 s |  |
| 26 | Mixtape | Elwynn Forest | The Beatles — Here Comes the Sun | `xUNqsfFUwhY` 150 / ok, 3:06 | unchanged | `6dGnYIeXmHdcikdzNNDMm2` | Abbey Road (Remastered 2009) (1969) | +0.0 s |  |
| 27 | Mixtape | Goldshire (Elwynn Forest) | Eagles — Hotel California | `dLl4PZtxia8` 150 / ok, 6:31 | unchanged | `40riOy7x9W7GXjyGp4pjAv` | Hotel California (2013 Remaster) (1976) | +0.0 s |  |
| 28 | Mixtape | Karazhan (Deadwind Pass) | Queen — Bohemian Rhapsody | `fJ9rUzIMcZQ` 150 / ok, 5:59 | **`BSTsnWoslP4`** 150 / ok, 5:54 (Queen - Topic) | `2JiDi0qAXsPwhPqA2qaKGt` | A Night At The Opera (1975) | +0.0 s |  |
| 29 | Mixtape | Duskwood | Michael Jackson — Thriller | `sO4vI8P88NM` 150 / ok, 5:57 | unchanged | `2LlQb7Uoj1kKyGhlkBf9aC` | Thriller (1982) | +1.1 s |  |
| 30 | Mixtape | Stranglethorn Vale | Guns N' Roses — Welcome to the Jungle | `0CNPR2qNzxk` 150 / ok, 4:34 | unchanged | `0G21yYKMZoHa30cYVi1iA8` | Appetite For Destruction (1987) | -0.0 s | explicit only; judgment call |
| 31 | Mixtape | Booty Bay (Stranglethorn Vale) | Klaus Badelt — He's a Pirate | `BuYf0taXoNw` 150 / ok, 1:30 | unchanged | `08QaHlMPWuO5PUxjl61bXn` | Pirates of the Caribbean: The Curse of the Black Pearl (Original Motion Picture Soundtrack) (2003) | -0.0 s |  |
| 32 | Mixtape | Westfall | Adele — Skyfall | `LJzp_mDxaT0` 150 / ok, 4:47 | unchanged | `6VObnIkLVruX4UVyxWhlqm` | Skyfall (2012) | -0.0 s |  |
| 33 | Mixtape | Menethil Harbor (Wetlands) | Otis Redding — (Sittin' On) The Dock of the Bay | `7C-VscEQugk` 150 / ok, 2:44 | unchanged | `3zBhihYUHBmGd2bcQIobrF` | The Dock of the Bay (Mono) (1968) | +0.0 s | judgment call |
| 34 | Mixtape | Stormwind City | David Bowie — "Heroes" | `YLp2cW7ICCU` 150 / ok, 6:11 | unchanged | `7Jh1bpe76CNTCgdgAdBw4Z` | "Heroes" (2017 Remaster) (1977) | -0.0 s |  |
| 35 | Mixtape | Ironforge | Wind Rose — Diggy Diggy Hole | `34CZjsEI1yU` 150 / ok, 5:41 | **`0FvHyHaT8ys`** 150 / ok, 5:16 (Wind Rose - Topic) | `0FDY2EHOv32kUoqplU1hdY` | Wintersaga (2019) | +0.0 s |  |
| 36 | Mixtape | Undercity | Siouxsie and the Banshees — Cities in Dust | `wsOHvP1XnRg` 150 / ok, 4:06 | unchanged | `0LvH53IYMdkQ2VHanpCiBi` | The Best Of... (2002) | +0.0 s | judgment call |
| 37 | Mixtape | Riverglades | Leon Bridges — River | `0Hegd4xNfRo` 150 / ok, 6:55 | **`7ug41DBUJOw`** 150 / ok, 3:59 (Leon Bridges - Topic) | `3hhbDnFUb2bicI2df6VurK` | Coming Home (Deluxe) (2015) | +0.0 s |  |
| 38 | Mixtape | Zephras Isle | Electric Light Orchestra — Mr. Blue Sky | `aQUlA8Hcv4s` 150 / ok, 4:55 | **`bJ8Sz8CJY5g`** 150 / ok, 5:03 (Electric Light Orchestra - Topic) | `2RlgNHKcydI9sayD2Df2xp` | Out of the Blue (1977) | +0.0 s |  |
| 39 | Unbound | Durotar | Bon Iver — The Wolves (Act I and II) | `A938Em4iAeM` ok / ok, 5:22 | unchanged | `3TO6wG55HdfwC8zfOaXAlJ` | For Emma, Forever Ago (2008) | +0.0 s |  |
| 40 | Unbound | Mulgore | Novo Amor — Birthplace | `G7ZcSnF7lhY` 150 / ok, 3:11 | unchanged | `2tSjhsJNIMBjNpnapXnd7y` | Birthplace (2018) | +0.6 s |  |
| 41 | Unbound | Skywatcher Plateau (Mulgore) | Tycho — Awake | `dm4tkSNKfFI` ok / ok, 4:44 | unchanged | `5MhMXTuVODDF234VDvSxQx` | Awake (2014) | +0.6 s | judgment call |
| 42 | Unbound | The Barrens | Explosions in the Sky — Your Hand in Mine | `1aMQ8db4Y4c` 150 / ok, 8:17 | unchanged | `4sYn64V9gqP6bpkOGw5B9k` | The Earth Is Not a Cold Dead Place (Anniversary Edition) (2003) | +107.0 s (excerpt kept) |  |
| 43 | Unbound | Teldrassil | Ólafur Arnalds — Near Light | `-M9PnC6uJVs` ok / ok, 3:29 | unchanged | `6Xajr512QEGAZ20bqAWkyT` | Living Room Songs (2011) | +0.6 s |  |
| 44 | Unbound | Darkshore | Sigur Rós — Sæglópur | `IP4kUjIXOMk` 150 / ok, 7:39 | unchanged | `3OAf2iksV6MRLzqtUglTDq` | Takk... (2005) | +69.0 s (excerpt kept) |  |
| 45 | Unbound | Ashenvale | The Album Leaf — The Light | `28O4bMduFcc` 150 / ok, 4:29 | unchanged | `0PaOYkEinHPDxK4yq7sR5l` | Into The Blue Again (2006) | +0.1 s |  |
| 46 | Unbound | Thousand Needles | Silent Poets — Asylums for the Feeling | `GKAywP6yd60` ok / ok, 7:09 | unchanged | `0zsDAdng7q4y8vAXXEDuED` | dawn (2018) | +39.3 s (excerpt kept) |  |
| 47 | Unbound | Stonetalon Mountains | Hania Rani — Esja | `VgxKGtjVx8w` ok / ok, 4:21 | unchanged | `0qLJdyq6rqXQmdlzjtcCs1` | Esja (2019) | +0.3 s |  |
| 48 | Unbound | Desolace | Low Roar — Don't Be So Serious | `D56XxiS_pJ0` 150 / ok, 6:13 | unchanged | `27VxXIWKxPbcyinlY3SYc3` | Once in a Long, Long While... (2017) | +1.3 s |  |
| 49 | Unbound | Feralas | Sufjan Stevens — Mystery of Love | `N0CP9zpbmAQ` 150 / ok, 4:09 | unchanged | `0MNNKSUU9OOQ8DSGWduw79` | Call Me by Your Name (Original Motion Picture Soundtrack) (2017) | +1.0 s |  |
| 50 | Unbound | Dustwallow Marsh | Radiohead — Daydreaming | `TkiyWhETiJw` 150 / ok, 6:24 | unchanged | `1Rd5OVjqDAr5xC96MHt448` | A Moon Shaped Pool (2016) | +0.4 s |  |
| 51 | Unbound | Tanaris | Nils Frahm — A Shimmer | `BAqri1XfCTg` ok / ok, 6:37 | unchanged | `09F0zRNSUa0qAWYUksXtXt` | Empty (2020) | +0.7 s |  |
| 52 | Unbound | Gadgetzan (Tanaris) | Khruangbin — Maria También | `H_kvg5fV6mw` 150 / ok, 3:11 | unchanged | `3CT4U4jJEagjIHg5PPph8X` | Con Todo El Mundo (2018) | +1.0 s |  |
| 53 | Unbound | Azshara | M83 — Wait | none | **`iQnRCdtECl8`** 150 / ok, 5:43 (M83 - Topic) | `01Q3OyB05mLgH01fpdAMPP` | Hurry up, We're Dreaming (2011) | +0.3 s |  |
| 54 | Unbound | Felwood | Agnes Obel — The Curse | `ZKEK5mVTOEo` 150 / ok, 5:53 | unchanged | `7hiY7fVxa0LEUeUP4F4ReR` | Aventine (2013) | +0.3 s |  |
| 55 | Unbound | Un'Goro Crater | Jon Hopkins — Emerald Rush | `zzwvuznsEJg` ok / ok, 5:37 | unchanged | `5S3F5GL8LASIjPg2PMiU1Q` | Singularity (2018) | +0.7 s |  |
| 56 | Unbound | Marshal's Refuge (Un'Goro Crater) | The Cinematic Orchestra — To Build a Home | `ozqw3UZRsIU` 150 / ok, 6:11 | unchanged | `3AqPL1n1wKc5DVFFnYuJhp` | Ma Fleur (2007) | -0.3 s |  |
| 57 | Unbound | Moonglade | Brian Eno — An Ending (Ascent) | `XbL9DT4QFc8` 150 / ok, 4:25 | unchanged | `4ZknuVR2jOpMOcfwWpsuEr` | Apollo: Atmospheres And Soundtracks (Extended Edition) (1983) | +0.9 s | judgment call |
| 58 | Unbound | Nighthaven (Moonglade) | Nick Drake — Northern Sky | `BBBkFWXiL9Q` 150 / ok, 3:44 | unchanged | `7pdmBld4yW3WIUkrxHyb5r` | Bryter Layter (1971) | +0.7 s |  |
| 59 | Unbound | Silithus | Jóhann Jóhannsson — Heptapod B | `KzaqrQuwr1k` 150 / ok, 3:42 | unchanged | `0wvldPbWwuellRqCfzL4eu` | Arrival (Original Motion Picture Soundtrack) (2016) | +0.1 s |  |
| 60 | Unbound | Cenarion Hold (Silithus) | Low Roar — Easy Way Out | `270KVNiFJ64` 150 / ok, 4:49 | unchanged | `00G1BtS8r791C63yZLVc0R` | 0 (2014) | +0.7 s |  |
| 61 | Unbound | Winterspring | Bon Iver — Holocene | `pvC5YD-IjL0` ok / ok, 5:37 | unchanged | `35KiiILklye1JRRctaLUb4` | Bon Iver, Bon Iver (2011) | +0.6 s | explicit only; judgment call |
| 62 | Unbound | Everlook (Winterspring) | Fleet Foxes — White Winter Hymnal | `bepPFNnaviA` ok / ok, 2:27 | unchanged | `0GegHVxeozw3rdjte45Bfx` | Fleet Foxes (2008) | +0.0 s |  |
| 63 | Unbound | Orgrimmar | Bring Me The Horizon — Ludens | `caYD1-uck_c` 150 / ok, 4:39 | unchanged | `2eZjO58Qe9og2xmyfw4qwJ` | Ludens (2019) | +2.2 s | judgment call |
| 64 | Unbound | Thunder Bluff | Jónsi — Go Do | `_aUh-7CPdQI` 150 / ok, 4:41 | unchanged | `06ivS38drtNuRCDIg2ww9k` | Go (2010) | -0.4 s |  |
| 65 | Unbound | Darnassus | AURORA — Runaway | `1f5dO89GtuE` ok / ok, 4:09 | unchanged | `2pB49wD5LpB8gYmqOIybTC` | All My Demons Greeting Me as a Friend (2016) | +0.8 s |  |
| 66 | Unbound | Mount Hyjal | M83 — Outro | `1cEy4UyYHI0` 150 / ok, 4:07 | unchanged | `1s9i7W8zx7Nxx78MUIsvjV` | Hurry up, We're Dreaming (2011) | +0.0 s |  |
| 67 | Unbound | Shen'dralas | Woodkid — To the Wilder | `JKDPPIlk_HM` 150 / ok, 4:38 | unchanged | `5xjkxF6sKG9ANZuicTXWVm` | WOODKID FOR DEATH STRANDING 2: ON THE BEACH (2025) | +0.1 s |  |
| 68 | Unbound | Alterac Mountains | Wardruna — Helvegen | `g1RJv-27i6Q` ok / ok, 7:12 | unchanged | `0OXEH9T6QSQCrmnjdtpf4P` | Runaljod – Yggdrasil (2013) | +41.9 s (excerpt kept) |  |
| 69 | Unbound | Dalaran (Alterac Mountains) | Hania Rani — Glass | `mneDuKb3BT8` ok / ok, 4:31 | unchanged | `4sQBgAPX981djCcsjDJvQx` | Esja (2019) | -0.3 s |  |
| 70 | Unbound | Arathi Highlands | Explosions in the Sky — Welcome, Ghosts | `WVdyjIrTq4U` 150 / ok, 5:44 | unchanged | `7k25nOYpb29GPLYfRbVbg6` | All of a Sudden I Miss Everyone (2007) | +0.8 s |  |
| 71 | Unbound | Badlands | Ry Cooder — Paris, Texas | `ParIMYd-QV0` 150 / ok, 2:53 | unchanged | `1idiifjTcCoGfBeZiYOdim` | Music by Ry Cooder (1995) | +2.8 s | judgment call |
| 72 | Unbound | Blasted Lands | Hildur Guðnadóttir — Bridge of Death | `aM_HhU_CV44` 150 / ok, 4:44 | unchanged | `5VwxuwHdpY19gZvyA7TlyU` | Chernobyl (Music from the Original TV Series) (2019) | +0.1 s |  |
| 73 | Unbound | Tirisfal Glades | Low Roar — Bones | `o9wSw7KHHkU` 150 / ok, 2:50 | unchanged | `6lt8Z886tMHpNqzOFWX8Yn` | Once in a Long, Long While... (2017) | -0.2 s |  |
| 74 | Unbound | Silverpine Forest | Hozier — In a Week | `yDZnKWdvTUo` 150 / ok, 5:19 | unchanged | `5F55SiWwEEytk6vsQXHxQW` | Hozier (2014) | +0.5 s |  |
| 75 | Unbound | Western Plaguelands | Gustavo Santaolalla — The Last of Us | `Y2UOwvUQ8L0` 150 / ok, 3:04 | unchanged | `285ieonEuLkll3zknYK2TY` | The Last of Us (2013) | -0.5 s |  |
| 76 | Unbound | Eastern Plaguelands | Max Richter — On the Nature of Daylight | `tUMc_-Bcunk` 150 / ok, 6:12 | unchanged | `56oReVXIfUO9xkX7pHmEU0` | The Blue Notebooks (15 Years) (2018) | +0.7 s | judgment call |
| 77 | Unbound | Light's Hope Chapel (Eastern Plaguelands) | Sigur Rós — Hoppípolla | `559un6zgVq4` 150 / ok, 4:29 | unchanged | `0yQPpUq5BJyqah5m2Q5Stt` | Takk... (2005) | +0.7 s | judgment call |
| 78 | Unbound | Hillsbrad Foothills | Iron & Wine — Passing Afternoon | `7JzLO2g9cZM` ok / ok, 4:01 | unchanged | `2G2PpiNxdcNnPRKt76iShb` | Our Endless Numbered Days (2004) | +0.2 s |  |
| 79 | Unbound | Southshore (Hillsbrad Foothills) | Lisa Hannigan — An Ocean and a Rock | `D56S2UfcNrA` ok / ok, 4:21 | unchanged | `6PCtiuzzXEEjes1RRFYSo3` | Sea Sew (2008) | +0.1 s |  |
| 80 | Unbound | The Hinterlands | Fleet Foxes — Blue Ridge Mountains | `d370CKlg-wk` ok / ok, 4:26 | unchanged | `7bzzC7QIfflM9eEj2aqaYX` | Fleet Foxes (2008) | +0.8 s |  |
| 81 | Unbound | Dun Morogh | Ásgeir — King and Cross | `eX5oljTS2_k` 150 / ok, 3:33 | unchanged | `5VlsKHU8L65FRqgr3zXgIW` | In The Silence (Bonus Edition) (2014) | +0.7 s | judgment call |
| 82 | Unbound | Searing Gorge | Jon Hopkins — Open Eye Signal | `2rqH8gT3R4A` ok / ok, 7:49 | unchanged | `0c9MTfnssGoNXUgmsaE5XU` | Immunity (2013) | +78.6 s (excerpt kept) | judgment call |
| 83 | Unbound | Burning Steppes | This Will Destroy You — Quiet | `mWRa3-3UM0o` 150 / ok, 4:53 | unchanged | `4llDF3RuBQYj2nkjTCJY4w` | Young Mountain (2006) | +0.2 s |  |
| 84 | Unbound | Elwynn Forest | Low Roar — I'll Keep Coming | `mlM66FzjpgM` 150 / ok, 5:52 | unchanged | `5Pf5HwjqLAbYDVBQU7H7Oz` | 0 (2014) | +0.3 s |  |
| 85 | Unbound | Deadwind Pass | Jóhann Jóhannsson — Flight from the City | `aXlx-YnvgKU` 150 / ok, 6:31 | unchanged | `3Sy6wgTNEp6vSgLs4EyXAf` | Orphée (2016) | +0.2 s |  |
| 86 | Unbound | Duskwood | Mogwai — Hunted by a Freak | `__IQOzqUUZA` ok / ok, 4:16 | unchanged | `3LD4GOl1YpyrTDriiRLqhs` | Happy Songs for Happy People (2003) | -1.9 s |  |
| 87 | Unbound | Loch Modan | José González — Stay Alive | `iuXtcFS7Xsc` 150 / ok, 4:27 | unchanged | `0ZNYGrmcehorhh9JOeg5Iv` | Stay Alive (From The Secret Life Of Walter Mitty) (2013) | +0.9 s |  |
| 88 | Unbound | Redridge Mountains | Lord Huron — Ends of the Earth | `N0D8JtOc5u4` 150 / ok, 4:44 | unchanged | `0iMt8ZMe2BsNvsF9LFDOAn` | Lonesome Dreams (Bonus Track Version) (2012) | +0.1 s |  |
| 89 | Unbound | Stranglethorn Vale | Bonobo — Kiara | `mBgz9gNUotQ` 150 / ok, 3:50 | unchanged | `7sqii6BhIDpJChYpU3WjwS` | Black Sands (2010) | +0.3 s |  |
| 90 | Unbound | Booty Bay (Stranglethorn Vale) | Beirut — Elephant Gun | `wmzVu5IDFR4` ok / ok, 5:49 | unchanged | `4J7EhrRbacK3AQ5ypCZn4I` | Lon Gisland (2006) | +0.9 s |  |
| 91 | Unbound | Swamp of Sorrows | Agnes Obel — Riverside | `d0n4rBcWhFI` 150 / ok, 3:49 | unchanged | `4vJ2nNBA6sxAUN5kLGPq3Q` | Philharmonics (2010) | +0.5 s |  |
| 92 | Unbound | Westfall | Gregory Alan Isakov — The Stable Song | `jGDjO9kuKyY` ok / ok, 6:01 | unchanged | `3G9ETaH55bMQx8hwNhAgbU` | That Sea, The Gambler (2007) | +0.9 s |  |
| 93 | Unbound | Wetlands | Novo Amor — Anchor | `E-MrJOz1VEY` 150 / ok, 4:18 | unchanged | `7qH9Z4dJEN0l9bidizW7fq` | Bathing Beach (2017) | +0.5 s | judgment call |
| 94 | Unbound | Menethil Harbor (Wetlands) | Ben Howard — Old Pine | `Coj1Nb2DY_w` 150 / ok, 5:29 | unchanged | `3CAX47TnPqTujLIQTw8nwI` | Every Kingdom (2011) | +1.5 s |  |
| 95 | Unbound | Stormwind City | Of Monsters and Men — King and Lionheart | `6etreW3oyK0` 150 / ok, 4:34 | unchanged | `310d8Vawp5kYDYrGrrTAzl` | My Head Is An Animal (2012) | -1.1 s | judgment call |
| 96 | Unbound | Ironforge | Woodkid — Iron | `HGoRCk7joUY` 150 / ok, 3:22 | unchanged | `59JXnQ3QCfMX572aDTYd46` | The Golden Age (2013) | +0.7 s | judgment call |
| 97 | Unbound | Undercity | Apparat — Goodbye | `rA7KC225dxA` 150 / ok, 4:30 | unchanged | `6BJXuY0lY0EUtSDCgkYnqE` | The Devil's Walk (2011) | +0.5 s | judgment call |
| 98 | Unbound | Riverglades | Ólafur Arnalds — Árbakkinn | `u1ZiOvVnxzk` 150 / ok, 4:56 | unchanged | `1uH1UPuN0JWa15Gs2R2jZZ` | Island Songs (2016) | +0.1 s |  |
| 99 | Unbound | Powderfuse Port (Riverglades) | Kiasmos — Blurred | `8V3lAgxp4_E` ok / ok, 5:06 | unchanged | `0jJLwPDpyuZRpd800yScN0` | Blurred (2017) | +0.5 s |  |
| 100 | Unbound | Zephras Isle | Boards of Canada — Dayvan Cowboy | `A_XIF-ISBaM` ok / ok, 5:00 | unchanged | `2J4lJMCuFCA0zlwFOjePD5` | The Campfire Headphase (2005) | +0.2 s |  |
