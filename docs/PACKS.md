# Soundtracks and share codes

A **soundtrack** is a set of needle drops: places and the songs that play there, such as *"in
Goldshire, play Hotel California by the Eagles"*. A soundtrack names songs (and can link to them on
Spotify or YouTube); it never contains audio. You browse, change, make and share soundtracks in the
game, in the NeedleDropper window: type `/needle`.

**Contents:** [Built-in soundtracks](#the-built-in-soundtracks) · [The window](#the-window) ·
[Add a drop](#add-a-drop-where-you-stand) · [Change a built-in song](#change-a-song-from-a-built-in-soundtrack) ·
[Your soundtracks](#your-own-soundtracks) · [Priority](#which-song-plays-the-priority-order) ·
[Per character](#a-different-list-on-one-character) · [Share codes](#share-codes) ·
[Pack format for authors](#for-soundtrack-authors-the-pack-format) · [Coming from 1.1](#coming-from-11)

## The built-in soundtracks

| Soundtrack | | What it is |
|---|---|---|
| **Azeroth Mixtape** | on by default | 38 needle drops at the places every player remembers: all six capitals, every starting zone, the signature zones, famous landmarks from Goldshire to Everlook and the four new Forever zones. Half the songs fit the place so well they give you chills; half are the punchline to a twenty-year-old in-joke. [Liner notes](MIXTAPE.md) |
| **Azeroth Unbound** | optional | 62 needle drops: the quiet, Kojima-style score for every zone and capital, plus a dozen landmarks. Post-rock, indie folk, modern classical and ambient electronica, with Low Roar as its anchor. [Liner notes](SOUNDTRACK.md) |

Built-in soundtracks come with the addon. They are read-only and get their fixes with addon updates.
To hear Azeroth Unbound too, open the **Soundtracks** tab and tick **On** next to it. Keep Azeroth
Mixtape above it: the Mixtape then plays at its 38 places and Unbound scores everywhere else.

The built-in drops come with their song lengths, an official YouTube upload and the exact Spotify
recording, so they play as soon as the
[NeedleDropper app](INSTALL.md#3-optional-the-needledropper-app) runs. With Spotify connected, the
app plays them on Spotify (or on YouTube first, if you choose that under
[Play music with](PROVIDERS.md#which-service-plays-a-drop)).

## The window

`/needle` (or `/nd`) opens and closes the window. So do a left-click on NeedleDropper in the add-on
compartment (the small numbered button by the minimap) and a key binding, if you set one
([Key bindings](TROUBLESHOOTING.md#key-bindings)). It has three tabs:

- **Drops** (the default): every place and its song, with a form to change it.
- **Soundtracks**: turn soundtracks on or off, set their order, and **New**, **Duplicate**,
  **Rename**, **Delete**, **Share...** and **Import...**.
- **Journal**: the songs this character has heard, newest first, each with **Replay**, **Play
  again next visit** (that place plays again the next time you arrive) and **Show drop**.

At the top of the Drops tab:

- **Showing:** picks what the list shows: *What plays (all active)*, which is the result of all the
  soundtracks you have on, or a single soundtrack (yours and the built-in ones are listed
  separately), plus *New soundtrack...* and *Import...*.
- The **search** box finds places, artists, titles and notes.
- **Filter** narrows the list: *Here*, *Not heard yet (this character)*, *Needs a song*, *Changed by
  you*, *In-game files*, *Turned off*.
- **Add drop here**, the button you'll use most.

Each row shows the place (*Elwynn Forest > Goldshire*) and the song (*Eagles · Hotel California*,
or *needs a song*). On the right, letters show where the song can play from: **S** Spotify,
**Y** YouTube, **F** an in-game file. *heard* (with a tick) means this character has heard it; *yours*, *off*
and *overridden* say whether you changed it, turned it off, or a soundtrack higher in your list has
another song there. Hover over a row for its sources, note and soundtrack, and for a **Test** button
that plays it now. Right-click a row for **Test**, **Change song here** (or **Edit**), **Turn off
here** (or **Turn on here**), **Copy to** and **Move to** (into one of your own soundtracks),
**Revert to Azeroth Mixtape** (named after the soundtrack your change covers) and **Delete**.

When you open the window, it selects the drop for the place you are standing in.

Good to know: the window moves with its title bar and resizes from its corner. Escape first leaves
the field you are typing in, a second Escape closes the window. During combat the window hides and
comes back afterwards, with what you typed still there. The gear button opens NeedleDropper's
settings.

## Add a drop where you stand

1. **Go there:** a zone, a town, an inn, a bridge, a dungeon.
2. **Press Add drop here** (or type `/needle add`, or use its key binding). NeedleDropper works out
   the place for you: inside a dungeon or raid, that instance; inside a landmark (when the game shows
   a subzone name that isn't the zone's), that landmark; otherwise the whole zone. If a drop for this
   place already exists, NeedleDropper selects it instead of making a second one (a built-in drop
   then shows **Change song here**, [below](#change-a-song-from-a-built-in-soundtrack)).
3. **Fill in the song:** type the **Artist** and **Title**, and paste a link if you have one: a
   Spotify song link into **Spotify**, a YouTube link into **YouTube**. A link plays exactly that
   recording; without one, the app searches Spotify for the artist and title.
4. **Press Test.** The title card appears and the song plays, through the app or, for an
   in-game file, in WoW itself. Testing never marks a place as heard, and works on a drop you haven't
   finished.

New drops go into **My Drops**, your personal soundtrack (NeedleDropper creates it the first time),
or into the soundtrack you picked under **Showing:**, if it is one of yours.

**In one line:** `/needle add Toto - Africa` adds the place and the song at once, and answers *Added
Goldshire: Toto · Africa to My Drops.* `/needle add https://youtu.be/QAo_Ycocl1E` (or a Spotify song
link) saves just the link; type the artist and title in the window, which opens by itself when the drop
has none yet. Until it has both, the drop is a draft: it never plays on arrival (the form says so), and
**Test** still plays it. A link pasted into **Artist** or **Title** by mistake moves into its own field.

A drop you add doesn't start during the visit you added it in, so it can't interrupt your typing.
It plays the next time you arrive, even in a zone your character explored long ago.

### The drop form

The fields, in the order Tab moves through them:

| Field | |
|---|---|
| **Place** | For example *Goldshire, Elwynn Forest*, *Elwynn Forest (whole zone)* or *The Deadmines (dungeon or raid)*. **Use my location** sets it to where you stand; **This landmark** or **Whole zone** picks the scope. You can also pick a **Zone** from the list (every zone, named in your game language) and type a **Landmark** name. Standing in a landmark gives the drop the game's area number, which works on every game language; a landmark typed by name shows *Landmark saved by name: works on <language> clients only.* Changing the place makes it a different place: the old one keeps its heard status |
| **Artist**, **Title** | Required: a drop without them is a draft (*needs a song*) and never plays. They are shown on the title card, and used to search Spotify when there is no link |
| **Spotify** | *Paste a Spotify song link*: `https://open.spotify.com/track/…` (with or without `intl-xx/` and `?si=…`), `spotify:track:…`, or the bare id. You'll see *Spotify song* (with a tick). In the Spotify app, right-click a song → **Share** → **Copy Song Link**. Album and playlist links don't work |
| **YouTube** | *Paste a YouTube link*: `youtube.com/watch?v=…`, `youtu.be/…`, `/shorts/…`, `/embed/…`, `/live/…`, `music.youtube.com` or `m.youtube.com`. If the link has a time in it (`t=` or `start=`), the form offers *Start at 1:23?* |
| **In-game file** | The name of an .mp3 or .ogg file in `NeedleDropper_Local\media`, for example `goldshire.mp3`. You'll see *found* (with a tick), or *not found: copy it there and restart WoW (a /reload is not enough)*. See [In-game audio files](PROVIDERS.md#in-game-audio-files) |
| **More...** | **Start at** (m:ss, to skip an intro), **Play for** (m:ss; empty = the whole song), **Volume** (Spotify and YouTube only: in-game files play at your Music volume; saved when you let go of the slider), tags **Fitting** and **Funny** plus your own, a **Note** (why the song fits; shown in the tooltip), **Turn off this place**, and **Copy or move to...** (the row's right-click **Copy to** / **Move to**, also within reach of a gamepad) |
| Buttons | **Test** / **Stop** and **Delete** |

There is no Save button. Pressing Enter or leaving a field saves it; Escape puts the field back as
it was. Changes apply at once, with no `/reload`: the app learns the song from the beacon the next
time the drop plays.

**Delete** is instant: a bar says *Deleted Goldshire.* with an **Undo** button for 15 seconds. The
bar's **Undo** brings back that drop, even if you changed something else in the meantime.

**Spotify match.** When the app plays a drop without a Spotify link, it remembers which song it
found. After your next login the form shows, for example, *Spotify match: Africa · Toto (4:55)*
with a **Use this exact song** button; one click pins that exact recording.

**Save now (reloads UI).** WoW writes addon data to disk only when you log out, quit or reload. After
10 or more changes since you logged in, the window's footer shows **Save now (reloads UI)**: click it
to save your edits now, so a game crash can't cost you them. The interface reloads for a moment; you
stay where you are.

## Change a song from a built-in soundtrack

Drops from a built-in soundtrack are read-only. Their form has a banner, *From Azeroth Mixtape
(built-in)*, with two buttons:

- **Change song here** copies the drop into **My Drops**, for the same place, and lets you edit it.
  NeedleDropper creates My Drops if you don't have it yet and puts it at the top of your list, so
  your version is the one that plays. The cursor lands in **Artist** with the old name selected:
  just type the new one, then fix the **Title** and paste a link.
- **Turn off here** means silence at this place: My Drops records the place as *off*.

Once My Drops has its own version of a place, the form (and the row's right-click menu) offers
**Revert to Azeroth Mixtape**, which brings the original back.

Why a layer instead of a copy? Changing one song takes one click, and fixes to the built-in
soundtrack in later addon updates still reach every place you didn't change. If you want a full copy
to rework from top to bottom, use **Duplicate** on the Soundtracks tab: it makes *Azeroth Mixtape
(my copy)*.

## Your own soundtracks

**My Drops** is your personal top layer: everything you add or change ends up there unless you pick
another soundtrack under **Showing:**. Otherwise it is a normal soundtrack: you can rename it, share
it, turn it off or move it down.

The **Soundtracks** tab lists every soundtrack with an **On** box, its name and a line such as *by
NeedleDropper · 38 drops · Built-in* (or *Yours*). Select one to see its author, description, tags
and homepage (shown as text: the game can't open links) and a summary such as *Plays at 38 places;
3 are covered by a soundtrack above*.

The buttons at the bottom:

| Button | |
|---|---|
| **New** | An empty soundtrack of your own, turned on near the top of your list (just below My Drops) |
| **Duplicate** | A full copy you can edit, turned on the same way; a built-in one becomes *<name> (my copy)*, one of yours *<name> (copy)* |
| **Rename** | Your soundtracks only |
| **Delete** | Your soundtracks only, after *Delete <name> and its N drops? This cannot be undone.* |
| **Share...** | A share code for the soundtrack ([below](#share-codes)) |
| **Import...** | Paste a share code |

Built-in soundtracks can't be renamed or deleted. To move drops between your own soundtracks,
right-click them in the Drops tab (**Copy to** or **Move to**), or use **Copy or move to...** under the
form's **More...**. You can have up to 50 soundtracks
of your own, with up to 300 drops each and 3,000 in total.

## Which song plays: the priority order

You can turn on as many soundtracks as you like. When two or more are on, arrow buttons (**Move up**,
**Move down**) appear to order them, and the tab explains the rule: *When two soundtracks have a song for the same place, the
one higher in this list plays.*

- The **top** of the list wins. That is why My Drops goes to the top: your changes beat the
  built-in songs.
- A place that a higher soundtrack **turned off** stays silent, whatever the soundtracks below have.
- A **draft** (no artist or title) doesn't count: the next soundtrack down plays there.
- **Showing: What plays (all active)** shows the result, one row per place, with *overridden* on the
  songs that lost.

This is the opposite of 1.1, where the pack further down the list won.

## A different list on one character

Tick **Use different soundtracks on this character** on the Soundtracks tab and this character gets
its own copy of the list: which soundtracks are on, and their order. Changes to the list then only
affect this character. Untick it to go back to the account's list. The soundtracks themselves, and
your edits to their drops, are shared by all your characters.

**Heard is per place.** A song plays the first time a character arrives at a place, whichever
soundtrack it comes from. Switching soundtracks therefore doesn't replay places you have heard. To
hear one again, use **Play again next visit** in the Journal, or start over with
`/needle reset char`. Whether "heard" counts per character or for the whole account is the *Play
each drop once per* option.

## Share codes

A share code is a whole soundtrack as one piece of text that you can paste anywhere: Discord, a
forum, a guild website. It looks like `!NeedleDropper:1!eNpdjzEL…Vdg==!`: it always starts with
`!NeedleDropper:1!` and ends with `!`.

### Sharing a soundtrack

1. On the Soundtracks tab, select the soundtrack and click **Share...** (or type
   `/needle share My Drops`).
2. Fill in **Author** if you like. It is empty by default, for your privacy, and NeedleDropper
   remembers what you type there.
3. The code is already selected: press **Ctrl+C**. (Addons can't copy to the clipboard themselves.)
   The dialog also shows how many characters it has.

Drafts are left out; the dialog tells you, for example, *2 unfinished drops were not included*.

### Posting it on Discord

The dialog's tip: *Press Ctrl+C to copy. In Discord, paste inside ``` ; long codes arrive as
message.txt, which works.*

- Type three backticks, paste the code, and type three backticks again. Discord then shows the code
  as it is instead of turning parts of it into formatting.
- A message holds 2,000 characters. Longer codes (the whole Azeroth Mixtape with its notes is about
  6,300) become a `message.txt` attachment when you paste them. That is fine: whoever imports it
  opens `message.txt`, selects everything and copies it.
- Text around the code doesn't matter. NeedleDropper finds the code in whatever you paste, even with
  line breaks, the fences or invisible characters in it.

### Importing a soundtrack

1. On the Soundtracks tab click **Import...** (also in the **Showing:** list, or type
   `/needle import`), and paste the code with **Ctrl+V**.
2. The preview shows the soundtrack's name, author, revision and number of drops, its first 12 places
   with their **S Y F** letters, and any warnings: unknown zones, in-game files you may not have,
   landmarks saved by name, places it would override among your active soundtracks, and fields that
   were dropped.
3. Change the **Name** if you like, then click **Import**.

The soundtrack arrives **turned off**: *Imported <name> (off).*, with a **Turn on** button. An import never replaces
anything: a soundtrack with the same id is imported as a new one (its id gets `-2`, `-3`, …), the same
name gets *(2)*, and the ids of the built-in soundtracks are reserved. Nothing in a code is ever run:
it is data only, and every field is checked before it is used.

**Updates.** If you already have that soundtrack, the code names the same author (an author name is
needed for this) and it has a higher revision, the button reads **Update <name>** instead. It keeps
the soundtrack's on/off state and its place in your list.

When a code can't be read, the dialog says why: see [Share code problems](TROUBLESHOOTING.md#share-codes).

### Try it

This code is a tiny My Drops: Toto's *Africa* in Goldshire. Copy all of it and paste it into
**Import...**:

```
!NeedleDropper:1!eNpdjzELwkAMhf+KZG6HiqB2Kyji4CC4iIik16iH16bkrkKV/ndz2skt+fKSvPcG7MKdBXKABCryRmwbLDcjEG495Kc3oBBuV5Av5onWwfqgigMHVpXDkpy2G3aVv1shZb4rX9zQHw02uMiKq1iDCnruQldGtC/4cjRsXLZW/tvNZtPlcE7gylJjfNgQVY6iq5YkbdE8VGsrndR9+jObQIN1PLjrJ6uRCD2t/4bK1APeYiQ9+yQZ6fABJcJVdg==!
```

### What is inside a code

`!NeedleDropper:1!`, then the soundtrack as JSON (the [pack format](#for-soundtrack-authors-the-pack-format)
below), compressed with zlib and written in standard Base64, then a closing `!`. The compression
carries a checksum, so a code damaged in transit is refused instead of importing the wrong songs.
Any program can read and write the format with standard tools; the NeedleDropper app's
[`packs` commands](#the-packs-commands) convert between codes and files.

## For soundtrack authors: the pack format

Most players never need this: the window does it all. The pack format is for authors who prefer to
write a soundtrack as a text file (and for the files in this repository that the built-in
soundtracks are made from).

A pack is one JSON object, the same one a share code carries:

```json
{
  "format": "needledropper-pack",
  "version": 1,
  "id": "my-soundtrack",
  "name": "My Soundtrack",
  "author": "Your name",
  "description": "Songs for my alt's long walk across Azeroth.",
  "revision": 1,
  "drops": [
    { "zone": 1429, "title": "I'll Keep Coming", "artist": "Low Roar",
      "note": "Where every journey starts.", "tags": ["fitting"] },
    { "zone": 1429, "subzone": "Goldshire", "areaID": 87,
      "title": "Africa", "artist": "Toto", "youtube": "QAo_Ycocl1E", "tags": ["funny"] },
    { "instanceID": 36, "label": "The Deadmines",
      "title": "Asylums for the Feeling", "artist": "Silent Poets" },
    { "zone": 1426, "disabled": true }
  ]
}
```

### Top level

| Key | | Meaning |
|---|---|---|
| `format` | required | Always `"needledropper-pack"` |
| `version` | required | Always `1` |
| `id` | recommended | The soundtrack's permanent id: 1 to 40 of `a-z`, `0-9` and `-`. Updates are matched on it, so keep it when you rename the soundtrack. Made from the name when missing |
| `name` | required | The name shown in game, up to 64 characters |
| `author` | optional | Who made it, up to 64 characters. Together with `id`, it decides whether an import is offered as an update |
| `description` | optional | What it is about, up to 1,000 characters; line breaks are fine |
| `tags` | optional | Up to 8 short words, for example `["fitting", "funny"]` |
| `homepage` | optional | An `http://` or `https://` link, shown as text |
| `revision` | optional | A whole number. Raise it each time you publish a new version: players who have the old one get **Update** |
| `drops` | required | The needle drops, 1 to 300 of them |

### Drops

| Key | | Meaning |
|---|---|---|
| `zone` | location | The zone's map number (uiMapID): `1429` is Elwynn Forest. In pack files the `packs` commands also accept zone names (`"Elwynn Forest"`, `"STV"`), but share codes and the game use numbers |
| `subzone` | location | A landmark inside the zone, by the name the game shows on an English client (needs `zone` or `areaID`) |
| `areaID` | location | A landmark by its area number: works on every game language (preferred) |
| `instanceID` | location | A dungeon or raid by its instance number |
| `label` | optional | The place name to show if the game can't name it (the title card uses the game's own, translated name when it can) |
| `title` | **required** | Song title, up to 200 characters |
| `artist` | **required** | Artist name, up to 200 characters |
| `spotify` | optional | The Spotify song: `"spotify:track:…"`, an `https://open.spotify.com/track/…` link, or the bare 22-character id |
| `youtube` | optional | A YouTube video: the 11-character id or a YouTube link (watch, youtu.be, shorts, embed or live) |
| `file` | optional | An in-game audio file: the bare file name of an .mp3 or .ogg in `NeedleDropper_Local\media` (letters, digits, spaces and `._-()` only) |
| `inGameFile` | with `file` | `true`. (A `file` without it was for the 1.1 app's own player, which is gone) |
| `startMs` | optional | Start this many milliseconds into the song (skip a long intro). Default 0 |
| `durationMs` | optional | How long the drop plays, in milliseconds from `startMs`: 4 minutes 5 seconds is `245000`. Leave it out to play the whole song; the game then keeps the zone music faded for *Length when unknown* (5 minutes by default) |
| `volume` | optional | Playback volume for this song, 0 to 100 (Spotify and YouTube) |
| `tags` | optional | Up to 8, for example `["funny"]` |
| `note` | optional | Why this song fits this place, up to 500 characters. The best part of a soundtrack |
| `disabled` | optional | `true` turns this place off: nothing plays there, even if a soundtrack lower in the list has a song. A bare place is enough, as in the last drop of the example |
| `id` | ignored | NeedleDropper works the id out from the place ([below](#places-zones-landmarks-and-instances)); a different value is reported |
| `appleMusic` | ignored | Apple Music links are ignored: Apple Music is not supported in v1 |

Unknown keys are reported as warnings and left out. Lengths count bytes of UTF-8 text, so a letter
with an accent counts as two.

### Places: zones, landmarks and instances

Every drop belongs to exactly one place, and gets an **id** from it:

| Place | Pack keys | Id |
|---|---|---|
| A zone or capital | `zone` | `zone:<map number>`, e.g. `zone:1429` (Elwynn Forest) |
| A landmark, by area number | `areaID` (plus `zone`, recommended) | `area:<area number>`, e.g. `area:87` (Goldshire) |
| A landmark, by name | `zone` + `subzone` | `sub:<map number>:<name>`, e.g. `sub:1429:lion-s-pride-inn` |
| A dungeon or raid | `instanceID` (plus `label`) | `inst:<instance number>`, e.g. `inst:36` |

When several match where you stand, the **instance** wins over the **landmark**, and the landmark over
the **zone**. Ids are what "heard" is recorded against, and what a soundtrack higher in the list
overrides, so two soundtracks that name the same place in the same way compete for it.

**Prefer `areaID` for landmarks.** A landmark given only by name matches the English name the game
shows, so it won't trigger on a German or French client; with `areaID` the game itself translates the
name. Keep `subzone` next to `areaID` anyway: it makes the pack readable.

### Finding the numbers: `/needle where`

Stand in the place and type **`/needle where`** in the game. It prints:

- the **zone key** and map number (`zone key: zone:1429 (Elwynn Forest)`),
- the **subzone** and its by-name id (`subzone: Goldshire (id by name: sub:1429:goldshire)`),
- the **instance** and its `instanceID` when you are inside one,
- the **area numbers** at your position (with names), which is where `areaID` values come from,
- the drops that already match this spot.

The easiest way to get a correct pack is to build the soundtrack in game, share it, and turn the code
into a file with `packs decode` (below).

### The packs commands

`NeedleDropper.exe` has commands for checking and converting packs. Open the folder that holds
`NeedleDropper.exe`, type `cmd` in File Explorer's address bar and press Enter, then type one of the
commands below. (Windows doesn't wait for the app, so the prompt can come back before its output:
put `start /wait` in front, for example `start /wait NeedleDropper.exe packs validate code.txt`.)

| Command | What it does |
|---|---|
| `NeedleDropper.exe packs validate "C:\path\my-soundtrack.json"` | Check a pack file (or a share code) and list every problem |
| `NeedleDropper.exe packs encode "C:\path\my-soundtrack.json" --out code.txt` | Turn a pack file into a share code |
| `NeedleDropper.exe packs decode code.txt --out my-soundtrack.json` | Turn a share code (pasted into a text file, or given directly) into a pack file |
| `NeedleDropper.exe packs convert-legacy` | Turn the 1.1 app's pack files into share codes that the game offers at your next login ([Coming from 1.1](#coming-from-11)) |
| `NeedleDropper.exe --help` | Every command |

Problems are reported with the soundtrack's name and the drop's position, for example
`pack "My Soundtrack" drops[1] (Goldshire): missing "artist"` (`drops[1]` is the second entry:
counting starts at 0). Common mistakes: a missing comma between two entries, a comma after the last
entry, curly "smart" quotes instead of straight `"` quotes, and a misspelled zone name (the message
suggests the closest names).

To add a built-in soundtrack to NeedleDropper itself, see [SETS-FORMAT.md](SETS-FORMAT.md).

### Guidelines for shared soundtracks

- **Share song names and links only, never audio files.** Players use their own Spotify account,
  YouTube, or their own copies of the music.
- **Use official YouTube uploads**: the artist's or label's channel, or YouTube's automatic
  "Artist - Topic" channels. Make sure the video can be embedded: open
  `https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=VIDEO_ID&format=json` in a
  browser. A short page of text means it works; *Unauthorized* means the owner disabled embedding
  (pick another upload); *Not Found* means the video is gone.
- **Add Spotify links when you can.** Without one the app searches by artist and title, which
  usually works but can pick another version of a song with a common title.
- **In-game files** only work for people who have files with the same names, so say where the songs
  come from (your own recordings, an album to buy) in the `description`.
- **Write a `note`** for each song: why it belongs to that place.
- **Give it a clear `name`**, your `author` name and a `revision`, and raise the revision whenever
  you publish changes. You choose the license for your soundtrack's text.
- Keep the soundtrack in the spirit of the game: Blizzard's add-on rules ask that content suits the
  game's Teen rating.

## Coming from 1.1

- **Your packs come with you, in one command.** Run `NeedleDropper.exe packs convert-legacy` once
  ([how](#the-packs-commands)): it turns each pack file of the 1.1 app (in
  `%APPDATA%\NeedleDropper\packs`, plus the packs its settings named) into a share code and hands the
  codes to the game. At your next login the chat says, for example, *2 soundtracks from your
  NeedleDropper app are ready to import: type /needle import to review them.* Each `/needle import`
  opens the next one in the normal import preview; they arrive turned off, and each is offered only
  once.
- **No more Rebuild map, no more `/reload`.** The 1.1 app built a "map" from your packs
  (**Rebuild map** in its control panel, `build-map` on the command line) that the game only read
  after a `/reload`. In 1.2 the game owns your soundtracks, and edits apply the moment you make them.
- **The order is reversed:** the top of the list wins now.
- **Resolve soundtrack is gone:** the app looks a song up on Spotify when you approach its place.
- **The default changed.** The 1.1 addon played Azeroth Unbound; 1.2 starts everyone on Azeroth
  Mixtape. Places you already heard stay heard (their ids didn't change). To keep Unbound, turn it on
  in the Soundtracks tab.
