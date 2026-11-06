# v1.2 builder notes for the integrators

Collected from the five v1.2 builders and the A3 protocol cutover (deviations, open issues, integration notes). Read with DESIGN-v1.2.md and CONTRACTS-v1.2.md.

## A1-model (tests passed: True)

**Test summary:** All 428 of my tests pass (about 5 s). They cover:
- Merge: order, overrides, turned-off entries, drafts, landmark priority, pruning at PLAYER_LOGOUT, fallback to the default list, an empty list on purpose, the per-character list, the 0.25 s debounce and ND_MAP_CHANGED.
- Live apply through the real Director: a new user drop FIREs with no /reload.
- Rebuild with 3,000 drops takes under 20 ms.
- Editing: CRUD, validation messages, My Drops (Change song here, Turn off/on, Revert), Add drop here with suppression (no FIRE this visit, FIRE on the next one), limits, undo (20 steps, no restore over a reused id).
- Migration: schema 1→2, beaconCellPx 4→3 and clamped, settings/done/journal kept, newer schema left alone, malformed data repaired. The 62 v1.1 Map.json ids are pinned and equal Sets/AzerothUnbound.lua; heard-status carries over by place. The history helpers are tested, including a simulated Director scan.
- Slug and drop-id parity with models.slugify/drop_id_for: a corpus with accents, apostrophes, CJK, İ and the Kelvin sign, plus 2,000 random strings.
- Sets.lua: registration rules and frozen copies.
- Companion: data checks, status lines, track keys (Python-style lowercasing tested per code point), imports offered once.
- Static checks on my 5 files and the Locale table.
- Share golden vectors: 7 in tests/vectors/share written by the DESIGN 5.4 Python reference encoder. Python codes decode in Lua to the same pack and Lua codes decode in Python. With the same zlib the two 

**Public interface:** All of this follows CONTRACTS-v1.2 2.3-2.7. Anything beyond the contract is marked +.

ns.L (Locale.lua)
- A table whose __index returns the key, so English text is the fallback.
- BigWigs packager @localization markers for all 10 client locales.
- Globals: BINDING_HEADER_NEEDLEDROPPER = "NeedleDropper", plus BINDING_NAME_NEEDLEDROPPER_TOGGLE, _ADD_HERE and _STOP.
- +ns.LOCALE.
- +ns.EscapeText(s): turns | into || (A2's Window.lua already uses it).

ns.RegisterBuiltinSet(set) -> true | false, msg (Sets.lua)
- Accepts version 1 only, refuses duplicate ids, stores a frozen copy. Problems are printed once at PLAYER_LOGIN.
- ns.Sets.Builtins() and GetBuiltin(id) return copies.
- +Has, Ids, Count, DefaultId (the "default" tag), Problems, IsValidId.
- +Internal(id): the stored table itself, read-only, for Soundtracks/Share.

ns.Soundtracks (also aliased as ns.ActiveRefs)
- Init() runs on ND_INIT and is idempotent.
- List(): {ref, id, name, author, description, tags, builtin, active, priority, dropCount} plus +homepage, myDrops, places, drafts, covered, revision. Returns a new array each call.
- Get(ref): user sets give the live table, built-ins a copy. Exists(ref).
- ActiveRefs(), SetActive(refs), UsesCharacterList(), SetUseCharacterList(bool).
- Create(name) and Duplicate(ref, name?) return a ref. Rename(ref, name) and Delete(ref) return ok, err.
- AddDrop(ref, drop) -> id | nil, err, field.
- UpdateDrop(ref, id, fields) -> true, newId | nil, err, field. A field set to false or "" is cleared.
- RemoveDrop(ref, id); GetDrop(ref, id).
- OverrideHere(id, fields?) -> "user:my-drops", id. TurnOffHere(id). Revert(id).
- Undo() -> ok, desc. CanUndo() -> bool, desc.
- Validate(drop, opts) -> drop | nil, msg, field. DropIdFor(place). Slugify(name).
- BuildActiveMap() (also aliased as Rebuild), ScheduleRebuild().
- Merged(): see deviations.
- Limits = {sets=50, dropsPerSet=300, dropsTotal=3000, undo=20}.
- + extras:
  - TurnOnHere(id, ref?), RevertTarget(id), CopyDrop, MoveDrop.
  - SetOn(ref, bool), MoveActive(ref, delta), SetDetails(ref, {author, description, tags, homepage}).
  - PlaceHere(), CurrentAreaID(), AddHere(ref?, fields?) -> ref, id, existed, Suppress(id, kind), ParseSongText(text), YouTubeStartMs(link).
  - Winner(id), IsDraft, EditCount(), RebuildPending(), Prune().
  - MigrateAccount(db), MigrateCharacter(cdb), MigratedFrom(), WelcomeLine().
  - History helpers: HistoryApplies(drop), DoneCounts(drop, rec), HistoryZoneDrops().
  - Text helpers used by Share: CleanText, CleanTags, CleanHomepage, FileName, SpotifyId, YouTubeId, IntValue.
- Map drops have the v1 shape plus setRef, setId, setName, builtin, n, songKey, and fileName. For user files, `file` is the full Interface\AddOns\NeedleDropper_Local\media\ path.

ns.Share
- Encode(pack) -> code | nil, msg. Validates first and refuses codes the reader would reject.
- FromSet(ref) -> pack, omittedCount.
- Decode(text) -> pack, warnings | nil, reason, detail. Reasons: notcode, foreign:<Name>, newer, 

### deviationsFromContract
- Merged() returns one entry per place the active soundtracks claim: the drop that plays, or a 'turned off here' entry. Drafts are not listed. Each entry: {drop (copy), id, setRef, setId, setName, builtin, priority, disabled, plays, overrides = refs of lower soundtracks whose drop it hides here, overriddenBy = {} (always empty, kept for the contract's shape)}. Reason: A2's Drops.lua indexes Merged() by drop id, expects 'what plays' and adds drafts itself. An earlier version that listed every occurrence would have shown duplicate rows and picked the wrong winner.
- Validate, AddDrop and UpdateDrop return a third value on error: the field name, so the form can point at the right field.
- SetActive({}) is allowed and means nothing plays (useful for a character's own list). Only a stored list whose refs all point at missing soundtracks falls back to the default. Delete falls back to the default when it empties a list.
- Create and Duplicate turn the new soundtrack on (just below My Drops when My Drops is first). Import never turns anything on.
- OverrideHere and TurnOffHere move My Drops to priority 1 even when it was already on lower in the list. The contract says priority 1; DESIGN only says 'put it at the top if it isn't active'. Without this the override never wins.
- OverrideHere stores basedOn as the set id (DESIGN 4.2), e.g. 'azeroth-mixtape', not a ref. RevertTarget(id) gives the live ref and name of what Revert would bring back.
- Share's normalized pack follows C1's sharecode.validate so Lua and Python agree: - a turned-off entry is shared as just its place; - zero startMs and durationMs are left out; - revision is left out when the code has none; - a missing name is an error; - zone, areaID, instanceID and revision must be numbers; - inGameFile must be true or false, and true needs a file; - appleMusic must be an https:// link (it is then dropped with a warning); - only C0 control characters and DEL are stripped, not C1; - a pack with no id gets one from its name with accents folded, like packs.derive_pack_id.
- Known Lua/Python differences, tested and documented: - a pack version above 1 is 'newer' in Lua and 'invalid' in Python; - Lua refuses a landmark saved by a name with no a-z/0-9 and no areaID (its id 'sub:<map>:' would be empty), Python accepts it; - Python fills in the zone of a known area (zones.KNOWN_AREAS), Lua cannot.
- Share.Decode returns 'cutoff' instead of DESIGN 5.3's literal 'damaged' when the closing ! is missing and the JSON fails to parse, the payload is shorter than 2 bytes, or the zlib header is bad. The body may also contain \f and \v. Both match the Python reference in DESIGN 5.4 and C1's sharecode. JSON that isn't an object is 'damaged', as in C1's code.
- The DESIGN 4.8 first-login line is not printed by my modules. Soundtracks.WelcomeLine() and MigratedFrom() are there for Core to print it once.

### openIssues
- Outside my area: about 168 existing addon test failures and 28 errors (and 4 in test_integration_e2e) come from A3's new Beacon.lua:30, which needs ns.Protocol2 at load while the current TOC doesn't list Protocol2.lua. The TOC must follow CONTRACTS 2.1.
- Outside my area: C2 work in progress fails test_core_cli (7), test_integration_e2e (1, CLI build-map), test_core_config_zones (1) and test_core_deck_providers (1). D's docs fail test_release_docs (3). test_transport_wincapture::test_find_window_without_match_returns_none depends on the PC (a real window matched).
- Not checked in the real client: C_EncodingUtil's actual zlib output and how it fails (DESIGN 6.4 step 1). I handle both styles, raise or return nothing, and Encode checks its own output.
- Lowercasing for the companion's track keys covers ASCII, Latin-1, Latin Extended-A, Greek (without the final-sigma rule) and basic Cyrillic. Other alphabets fall back to ASCII-only lowercasing, so a Spotify match recorded under a Python .lower() key can be missed for them.
- Folding accents to derive a pack id covers U+00C0-U+024F only. Other NFKD compatibility forms (fullwidth letters, ligatures) aren't folded. This only matters for hand-made codes that have no id.

### notesForIntegrator
- Core.lua: - Remove the line `ns.Map = ns.BuildMap(NeedleDropper_Map)`, the functions ns.BuildMap and validateDrop (with their n/hash rules), and in PLAYER_LOGIN both the 'no song map loaded … build-map' print and the 'malformed entries in Map.lua' print. Soundtracks.BuildActiveMap now sets ns.Map at ND_INIT; if you want a message, use ns.Map.problems and ns.Map.invalid. - Keep ns.FindSubzoneDrop; it reads ns.Map when called. - Set ns.SCHEMA = 2 and the account/character defaults to schema = 2. Otherwise the second login after migrating prints 'saved data comes from a newer version'. - Set the beaconCellPx default to 3 with range 2..4, and drop the tip 'Match the companion's setting'. - Call ns.Soundtracks.MigrateAccount(db) in initDB before validateSettings. Then a v1 value like 8 clamps to 4 instead of being clamped to 4 first and turned into 3. Init() skips data that is already migrated. - Print Soundtracks.WelcomeLine() once at PLAYER_LOGIN on the first login with 1.2 (MigratedFrom() is 1, or the data is fresh). - Wire `/needle add [text]` as `ParseSongText(text)` then `AddHere(nil, fields)`.
- Director.lua: - In RunHistoryScan, loop over Soundtracks.HistoryZoneDrops(). - In needsLandmarkCheck, also require Soundtracks.HistoryApplies(d). - In IsDone, use Soundtracks.DoneCounts(drop, record), so an old history mark never hides a drop the player made. - Add D.SuppressHere(id). AddHere already calls it when it exists and otherwise falls back to D.suppress[id] = D.EntryTime(...). - On ND_MAP_CHANGED, disarm when the armed drop's id is gone from ns.Map.byId or its songKey changed, then re-evaluate. - Map-drop `file` is already the full path, so Audio.PlayLocal works unchanged. StatusLines still prints the v1 'map: hash'; ns.Map.hash is 0.
- Test harness merge: - tests/v12_model_harness.py has FINAL_TOC (CONTRACTS 2.1). Its 'full' profile leaves out Beacon, ChatLink, UI, Options and SelfTest on purpose. It adds Protocol.lua while Director uses ns.Protocol, and Emit.lua once Director stops defining ns.Emit. When merging, load FINAL_TOC plus tests/lua/shim_encoding.lua. Python must set SHIM_ENCODING_BRIDGE (hex or bytes mode) before the shim is loaded. - test_addon_misc::test_only_allowed_globals_are_created must allow BINDING_HEADER_NEEDLEDROPPER and the three BINDING_NAME_NEEDLEDROPPER_* globals. - test_every_addon_file_compiles_under_lua51 now also picks up my 5 files, and they pass.
- A2 Bindings.xml: Keybindings.lua resolves the category as _G[category]. Use category="BINDING_HEADER_NEEDLEDROPPER" so the Locale.lua header string is shown; category="NeedleDropper" also shows 'NeedleDropper', but it can never be translated.
- C1: - gamedata.record_track: key a search match as 'q:' + Python .lower() of the frame's query (artist, 0x1F, title, as P2.BuildQuery cleans it), and a Spotify id as 'sp:<id>'. Companion.Track also tries 'yt:<id>' for notEmbeddable. - sharecode.validate: consider returning 'newer' for a pack version above 1 (DESIGN 5.7), and refusing a landmark whose name gives an empty slug, as gen_sets does.
- Shared vocabulary: the history helpers mean 'built-in only' by map-drop builtin == true. Map drops of user soundtracks have builtin = false and setRef 'user:<id>'.

## A2-window (tests passed: True)

**Test summary:** My tests: 112 passed, 0 failed. 108 run against fakes that follow the CONTRACTS 2.5-2.7 signatures. 4 (test_v12_ui_real.py) run against A1's real Locale/Sets/Soundtracks/Share/Companion, including a Share code -> Import round trip through A1's zlib bridge.

What they cover:
- Window shell: lazy build (no frames at file load); Toggle, Open and Close; the ButtonFrameTemplate window (portrait hidden, title, DIALOG strata, 700x500, resize bounds 620/420/1100/800 through PanelResizeButtonMixin:Init); the CloseButton OnClick replaced, and it still works in combat with no HideUIPanel and nothing blocked.
- Escape: UISpecialFrames registration; the first Escape leaves the field and restores it, the second closes the window.
- Tabs: three PanelTabButtonTemplate tabs, manual OnClick, PanelTemplates_SetTab / SetNumTabs, sound kit 841, last tab saved.
- Combat: the window hides and comes back with typed text and selection kept; it stays closed if the player closed it.
- Gear button opens Settings.OpenToCategory(CategoryID). Position and size are saved, clamped and restored.
- Footer: Playing / Waiting / Next drop / companion status lines, and the Stop button. "Save now" appears after 10 edits and calls ReloadUI.
- Key-binding globals; every EditBox has SetAutoFocus(false) recorded; every template is on the allowlist with the right frame type (the shim raises otherwise, which is self-tested); every list view has an element extent.
- Drops tab: sorting, rows with S/Y/F letters and words, h

**Public interface:** Public API, ns.UI (CONTRACTS 2.9):
- ns.UI.Toggle()
- ns.UI.Open(tab) where tab is "drops", "soundtracks" or "journal"; nil means the last tab used. Returns false before the DBs are ready.
- ns.UI.Close() / ns.UI.IsShown()
- ns.UI.OpenImport(prefillText?)
- ns.UI.OpenShare(ref?): the default is the soundtrack selected on the Soundtracks tab, else user:my-drops.
- ns.UI.AddHere(text?): with no text it is the Add drop here button. With "Artist - Title" or a Spotify/YouTube link it is /needle add: it fills and commits the drop, prints "Added <place>: <song> to <soundtrack>.", and opens the window when the result is still a draft.
- ns.UI.Refresh()

Globals used by Bindings.xml (defined in UI/Window.lua):
- NeedleDropper_ToggleWindow()
- NeedleDropper_AddDropHere()
- NeedleDropper_StopDrop()

Named frames (all in UISpecialFrames, CloseButton OnClick replaced, hidden in combat and restored afterwards):
- NeedleDropperFrame, with tabs NeedleDropperFrameTab1..3
- NeedleDropperImportDialog
- NeedleDropperShareDialog

StaticPopupDialogs keys, defined lazily on first use: NEEDLEDROPPER_NEW, NEEDLEDROPPER_RENAME, NEEDLEDROPPER_DELETE.

Bindings.xml: NEEDLEDROPPER_TOGGLE, NEEDLEDROPPER_ADD_HERE and NEEDLEDROPPER_STOP, each with category="NeedleDropper" and no default key.

Saved state: the window writes only NeedleDropperDB.ui (point, w, h, tab, showing, author). Every model change goes through ns.Soundtracks / ns.Share.

ns.UI._W is internal (shared helpers for the UI files: W.Esc, W.MergedIndex, W.Playable, W.TurnOn, ...). It is not a contract.

Events listened to: ND_MAP_CHANGED, ND_SOUNDTRACKS_CHANGED, ND_COMPANION_DATA, ND_PLAYBACK, ND_LOCATION, PLAYER_REGEN_DISABLED/ENABLED, and PLAYER_LOGIN (to start counting edits for Save now). Model events only mark the window dirty; the refresh runs on the next frame, or right away after the window's own edits.

Other modules called: only the CONTRACTS 2.5-2.7 functions, plus ns.Director.PlayTest / Stop / SuppressHere, the Director's active/armed/GateReasons/CooldownLeft/IsDone for the footer, ns.Location.Snapshot / Candidates, ns.AreaName, ns.Protocol2.SpotifyIdFromLink / YouTubeIdFromLink, ns.EscapeText when present, and Settings.OpenToCategory(ns.Options.CategoryID()).

Test helpers:
- tests/lua/shim_ui.lua defines SHIM.ui: Click, Type, Paste, PressEnter, PressTab, PressEscape, Enter, Leave, Drag, Resize, SetSlider, DropdownPick, DropdownItems, MenuItems, ContextPick, PopupAccept, PopupCancel, TooltipLines, Calls, CallCount, created, badEscapes, forbidden.
- It also defines SHIM.uiFakes: Install(ns, opts) and InstallDirector(ns).
- tests/v12_ui_harness.py has UIHarness(real=False|True, location=, builtins=, db=, char_db=, known_files=, ...). It is used as UIHarness(real=True) when A1's files exist.

### deviationsFromContract
- ns.UI.AddHere takes an optional text argument ("Artist - Title" or a link) so /needle add can go through the same coverage, override and selection logic. With no argument it is the CONTRACTS AddHere().
- The Import dialog also calls Share.Decode on non-empty text that lacks the !NeedleDropper: prefix, so the player gets Share's foreign:<Name> and notcode messages ("This is a WeakAuras string..."). It still decodes only on userChanged edits whose text differs from the cached last text (DESIGN 5.6). Share returns early for prefix-less text, so no base64 or zlib work runs.
- The Drops tab's Test / Stop / Delete live in the window's 26 px button bar (the tab's buttons, DESIGN 4.3), not at the bottom of the scrolling form. The form scrolls (a plain ScrollFrame + MinimalScrollBar through ScrollUtil.InitScrollFrameWithScrollBar) because the expanded More... section does not fit at the 420 px minimum height.
- Glyphs are replaced with words or textures that game fonts are sure to have: buttons say Test, Stop and Replay instead of ▶/■; the check mark is the |TInterface\RaidFrame\ReadyCheck-Ready:0|t texture; zone > landmark replaces ›; "Goldshire, Elwynn Forest" replaces the em dash; "Share > Copy Song Link" replaces →; "..." replaces the ellipsis. The · middle dot is kept (Toast already uses it).
- Bindings.xml uses category="NeedleDropper", exactly as DESIGN 4.8 says. A1's Locale.lua sets BINDING_HEADER_NEEDLEDROPPER = "NeedleDropper", so category="BINDING_HEADER_NEEDLEDROPPER" (the Blizzard_PingUI/Bindings.xml pattern) would show the same text: Keybindings.lua L220-L226 shows _G[cat] or else cat itself.
- Switching the Showing view clears the Drops selection. In the What plays view the player's unfinished drafts are listed too (marked "needs a song"), so a fresh Add drop here draft is visible there.
- Soundtracks tab layout: the caption sits under the list and the "Use different soundtracks on this character" checkbox is in the tab's top bar. Up/Down are icon buttons (UI-ScrollBar-Scroll*Button textures) with tooltips.
- The window refuses to turn off the last active soundtrack. The On checkbox is disabled, and the tooltip says "At least one soundtrack stays on. Turn needle drops off in Settings instead." A1's model allows an emptied list on purpose, and DESIGN 4.2 rule 5 falls back to the default list, so this is a UI policy and not a model rule.
- "Move to" in the drop context menu is AddDrop + RemoveDrop, which is two undo steps, because CONTRACTS 2.5 has no move. A1 also ships S.MoveDrop and S.CopyDrop (one step each), outside the contract.

### openIssues
- Creating My Drops: W.EnsureMyDrops (used by Add drop here when My Drops doesn't exist) calls Soundtracks.Create("My Drops") and expects the id my-drops. That holds with A1's module today. The name is then changed to L["My Drops"] when a translation exists. If a player already has a soundtrack named "My Drops" under another id, A1's Create refuses and Add drop here prints that error. An ensure-My-Drops model API in CONTRACTS 2.5 would close this gap.
- The CONTRACTS 2.5 wording for Merged() ("what plays, by place") can be read two ways. A1 returns one entry per drop of every active soundtrack, with overriddenBy = the soundtracks above it, plus draft and plays fields. W.MergedIndex() works with either reading, and my fake now behaves like A1's module. Suggest pinning the wording in CONTRACTS.
- The UI code is 150 KB. DESIGN 4.1 says to revisit load-on-demand at about 200 KB.
- ns.Director.PlayTest(drop) and ns.Director.SuppressHere(id) don't exist in today's v1 Director.lua. v1 PlayTest() ignores its argument. The tests install recording stand-ins (SHIM.uiFakes.InstallDirector), so the real versions are the integrator's to add.
- ND_PLAYBACK is not fired by anything yet. The footer also polls the Director once a second while the window is shown, so it stays correct either way.

### notesForIntegrator
- TOC: the five UI files go after Options.lua in the CONTRACTS 2.1 order: UI\Window.lua first, then UI\Drops.lua, UI\Soundtracks.lua, UI\Journal.lua, UI\ShareDialogs.lua. Bindings.xml is never listed.
- Director.PlayTest(drop) receives the shape W.Playable() makes, which matches A1's map drops. file is the full Interface\AddOns\NeedleDropper_Local\media\<name> path, fileName is the bare name, and the drop includes unsaved form text (drafts too). It should play like a map drop with the TEST flag and never mark the drop heard. Director.SuppressHere(id) can call A1's S.Suppress(id, kind).
- Core slash commands: bare /needle and /nd should call ns.UI.Toggle(). /needle add <text> should call ns.UI.AddHere(text): calling A1's S.AddHere directly would skip the window's selection, the Artist focus and the window opening for link-only drafts. Also: /needle soundtracks -> ns.UI.Open("soundtracks"), /needle journal -> ns.UI.Open("journal"), /needle import -> ns.UI.OpenImport(), /needle share [name] -> ns.UI.OpenShare(ref). Compartment left-click -> ns.UI.Toggle(). The companion's migration offer can call ns.UI.OpenImport(code): a prefilled code is decoded once.
- Please fire ND_PLAYBACK on Director state changes (armed / active / ended). The footer refreshes on it and also polls at 1 Hz while the window is shown.
- A1 provides extra helpers outside CONTRACTS 2.5: S.MoveDrop and S.CopyDrop (one undo step), S.TurnOnHere, S.AddHere, S.SetOn and S.MoveActive. The window uses only contract calls. Switching "Move to" to S.MoveDrop would make it a single undo step.
- tests/v12_ui_harness.py leaves out A3's Beacon.lua, ChatLink.lua and Emit.lua, as A1's harness does; the Director reaches them only through if ns.Beacon / if ns.ChatLink. With real=True it loads A1's files and A1's zlib bridge (v12_model_harness.make_bridge plus tests/lua/shim_encoding.lua). tests/test_v12_ui_static.py imports tokenize_lua, FORBIDDEN_CODE, LUA51_ESCAPES and _ESCAPE from your tests/test_integration_static.py. That file only scans top-level *.lua, so UI/*.lua are covered by my static test.
- Templates and APIs checked on Gethe/wow-ui-source @eac30af597e5aaa43a60f6675ef5fdcea100375f (forever, 1.60.1 70790), paths under Interface/AddOns/.
- Blizzard_SharedXML/Mainline/SharedUIPanelTemplates.xml: L148 UIPanelCloseButton (OnClick = UIPanelCloseButton_OnClick); L315 UIPanelButtonTemplate; L566-L721 ButtonFrameTemplate (L711) with CloseButton, Inset and TitleContainer.TitleText; L932 PanelTabButtonTemplate (no OnClick, parentArray Tabs); L1642 PanelResizeButtonTemplate.
- Blizzard_SharedXML/Mainline/SharedUIPanelTemplates.lua: L111 ButtonFrameTemplate_HidePortrait; L152 UIPanelCloseButton_OnClick -> HideUIPanel; L441 PanelTemplates_SetTab; L542 PanelTemplates_SetNumTabs; L1637 PanelResizeButtonMixin:Init. Blizzard_SharedXML/PortraitFrame.lua L11 / L32: SetTitle.
- Blizzard_SharedXML/Shared/InputBox/InputBoxTemplates.xml: L4 InputBoxScriptTemplate (OnTabPressed = EditBox_OnTabPressed, using .nextEditBox / .previousEditBox, InputBoxTemplates.lua L1); L180 InputBoxInstructionsTemplate (.Instructions, autoFocus defaults to true); L209 SearchBoxTemplate (autoFocus false).
- Blizzard_SharedXML/Shared/Scroll: ScrollTemplates.xml L4 WowScrollBoxList and L21 ScrollingEditBoxTemplate (GetEditBox, GetInputText, callback events; ScrollTemplates.lua L1-L260); MinimalScrollBar.xml L15 (an EventFrame); ScrollBar.lua L209 SetHideIfUnscrollable; ScrollBoxListView.lua L41-L52 (extent error), L496, L677 SetElementExtent; ScrollBoxLinearView.lua L246; ScrollUtil.lua L137 / L149 / L215; ScrollBox.lua L12-L25 ScrollBoxConstants and L855 ScrollToElementData; Blizzard_SharedXML/DataProvider.lua L277.
- Blizzard_SharedXML/Shared/Slider/MinimalSlider.xml L34 MinimalSliderWithSteppersTemplate, and MinimalSlider.lua L126 Init and its Event. Blizzard_SharedXML/Shared/Button/CheckButtonTemplates.xml: L4 UIRadioButtonTemplate (lowercase .text) and L50 UICheckButtonTemplate (.Text).
- Blizzard_Menu: Mainline/MenuTemplates.xml L3 WowStyle1DropdownTemplate and L66 WowStyle1FilterDropdownTemplate (DropdownButton type); MenuTemplates.lua L719-L1056; DropdownButton.lua L237 SetupMenu and L255 GenerateMenu; MenuUtil.lua L151 CreateContextMenu and L198-L285 Create*; Menu.lua L227 SetScrollMode, L830 SetEnabled, L903 Pick (responder(data, menuInputData, menu)).
- Static popups: Blizzard_StaticPopup_Game/GameDialog.lua L574-L617 (GetEditBox / GetEditBoxText / GetButton1); Blizzard_StaticPopup/StaticPopup.lua L309 Show, L591 OnShow(dialog, data), L695 OnClick / OnAccept(dialog, data, data2); GameDialog.xml L210 (its edit box autofocuses).
- Blizzard_UIParentPanelManager/Shared/UIParentPanelManager.lua: L853-L861 (HideUIPanel blocked in combat) and L1104-L1116 (CloseSpecialWindows).
- Blizzard_SharedXML/SharedTooltipTemplates.lua: L128-L166 GameTooltip_* helpers and L320 DisabledTooltipButtonMixin. Blizzard_SharedXMLBase/CallbackRegistry.lua L112 / L184: callbacks receive the owner first.
- Blizzard_SharedXML/Mainline/SoundKitConstants.lua L38: IG_CHARACTER_INFO_TAB = 841. Blizzard_SettingsDefinitions_Frame/Keybindings.lua L220-L226: category name lookup. Blizzard_EditMode/Shared/EditModeDialogs.lua L337-L345: OnTextChanged fires twice on paste.
- Blizzard_APIDocumentationGenerated/UIFileAssetAPIDocumentation.lua L27: C_UIFileAsset.IsKnownFile. Blizzard_APIDocumentationGenerated/MapDocumentation.lua L198: C_Map.GetMapChildrenInfo. Blizzard_Fonts_Shared/Shared/FontStyles.xml L50-L112: GameFont* objects.
- Textures: the gear Interface\WorldMap\GEAR_64GREY (Blizzard_Menu/MenuVariants.lua L8); Interface\Buttons\UI-ScrollBar-Scroll{Up,Down}Button-* (Blizzard_SharedXML/SecureUIPanelTemplates.xml L10-L20); Interface\RaidFrame\ReadyCheck-Ready (Blizzard_Calendar/Mainline/Blizzard_CalendarTemplates.xml L325).

## C1-engine (tests passed: True)

**Test summary:** Owned tests all pass: test_v12_engine_flows 51, test_v12_engine_transports 21, test_sharecode_codec 83 (+1 skipped: no share-code text files under tests/vectors/share), test_sharecode_cli 21, test_sharecode_packs 35, test_gamedata 158 (+1 pre-existing skip), test_savedvars 232. The command above also runs the existing suites that use C1 modules (legacy v1 engine, v1 pixel and chat transports, packs/mapgen, setgen, wiring, static compile, and C2's/A1's v12 shell-engine, link-check and share parity/vector tests): 1439 passed, 2 skipped. The engine thread tests were repeated 5x with no flakes. The share-code mutation test shows 0 silent corruptions over 1000 random one-character changes. Lua interop checked: the addon's own Base64.lua and Json.lua (lupa, bytes mode) read Python-made codes byte-identically, and codes built with them decode in Python.

The full suite is NOT green. Single process, 130 s: 188 failed, 28 errors, 3183 passed, 4 skipped. No failure traceback references a C1 module. Per file: test_addon_toast 71, test_addon_director 61, test_addon_misc 27, test_addon_transport 18, test_addon_audio 13, test_core_cli 7, test_v12_ui_drops 5, test_integration_e2e 5, test_release_docs 3, test_addon_integration 2, test_v12_beacon_pipeline 1, test_v12_beacon_clearance 1, test_core_deck_providers 1, test_core_config_zones 1. Causes are under openIssues. That full run came just before my last three engine fixes (worker restart, one shared wait budget, place-key guard); the 18-fi

**Public interface:** ENGINE (companion/needledropper/engine.py)
- Engine(providers, deck=None, *, clock, fade_in_ms, fade_out_ms, log, on_link_check=None, on_status=None, addons_dir=None, track_recorder=None, registry=None, prewarm_async=True, fire_wait_sec=3.0, dedupe_sec, hello_stale_sec, poll_interval, switch_fade_ms, fallback_window_sec, card_only_sec). The v1.1 call Engine(providers, registry, deck) still works.
- Methods: submit(event, transport) takes a protocol2.Frame or a v1 models.Event; start(); stop(); status(); handle(); drain(); play_now(drop, test, only); stop_playback(); set_providers(); set_registry(); set_addons_dir(); current_provider(); is_playing; wait_idle(); wait_prewarm(timeout); prewarm_songs(songs, limit=25).
- Module functions: describe_event(ev, transport) handles both protocols; query_key(artist, title).
- v2 FIRE routing: Spotify id, then Spotify search on the query (when Spotify is available), then YouTube id, then any other provider. The configured provider order is ignored for v2.
- ARM caches the song by place key. A later placeKey-only ARM only refreshes it. A FIRE fills fields the fit rule removed from the ARM of the same place (provider ids are never mixed).
- Pre-warm runs on one background worker: Spotify search, or a lookup of the pasted Spotify track; then Spotify prepare() (device wake), or the YouTube prepare() when Spotify will not play the song.
- A FIRE waits for its place's pre-warm with one shared budget of at most fire_wait_sec (3 s), and drops queued pre-warms of other places.
- LOCAL_PLAYBACK shows the card only. TEST plays but records nothing. Drop id "test" with no song only calls on_link_check. CANCEL fades out by place key (a different drop id means a key collision and is ignored). HELLO without DROP_ACTIVE for more than 5 s stops playback.
- Dedupe key: ("v2", seq, type, placeKey or 0, flags); v1 uses (seq, type, n, hash).
- on_link_check(frame, transport) runs for every TEST-flagged frame and every PING (v1 included).
- on_status(status) gets status() plus change = {kind: addon | armed | playing | local | stopped | match | notFound | notEmbeddable | searchFailed (unavailable=bool) | outdatedAddon | noSource, ...}. Both callbacks run on the engine thread, outside the engine lock.
- Recordings via gamedata.record_track (or track_recorder):
  - q:<ascii-lower artist>\x1f<title>: {spotify, durationMs, title, artist} on a match, {notFound: true} on no match;
  - sp:<id>: {title, artist, durationMs} for a pasted link, {notFound: true} on HTTP 400/404;
  - yt:<id> and q:...: {notEmbeddable: true} for YouTube codes 100/101/150/153 (from the ProviderError text or deck.status_for(playId)); cleared after the video plays again.
- v1 events without a registry: one "update the addon" warning plus outdatedAddon. HELLO and PING still count.

SHARE CODES (sharecode.py)
- PREFIX = "!NeedleDropper:1!", CODE_RE, REASONS, FOREIGN_ANYWHERE, FOREIGN_AT_START.
- encode(pack) -> str: sorted keys, compact, raw UTF-8, zlib 9, padded Base

### deviationsFromContract
- Engine signature is a superset of CONTRACTS 3.2. Beyond Engine(providers, deck, *, clock, fade_in_ms, fade_out_ms, log, on_link_check, on_status) it takes addons_dir / track_recorder (the contract has no way to reach gamedata), registry (legacy v1 map), prewarm_async, fire_wait_sec and the old timing knobs. The v1.1 positional form Engine(providers, registry, deck) is still accepted, because cli.py/panel_api.py and integrator tests use it. C2's construct_engine already passes addons_dir.
- providers/base.py says provider methods are called from the engine thread only. The ARM pre-warm (resolve_drop, prepare) runs on one background worker instead, so a slow Spotify search never blocks the beacon and a FIRE can wait at most 3 s for it. To limit the overlap, a FIRE waits for the worker to go idle first, within that budget. SpotifyProvider documents itself as thread-safe (TokenManager and the resolve cache are locked).
- pixel.py calls beacon2.scan() rather than beacon2.locate_and_decode(). locate_and_decode is scan(...).result, so the decode is the same; the scan report is needed for diagnostics and region sizing.
- Rule for the v1 fallback: beacon2 reports 'no header' for a v1 strip, because its header check needs the v2 geometry and prefix cells. So v1 decoding runs whenever beacon2 found no confirmed v2 header, not only on 'payload invalid'.
- sharecode.decode follows DESIGN 5.4 but is stricter, to match the addon's strict readers: - a Base64 ending with non-zero unused bits is damaged (cutoff if the final ! is missing); - a JSON byte-order mark is skipped; - nesting deeper than 6, unpaired surrogate escapes, NaN/Infinity and numbers too large for a double are damaged; - a huge version numeral counts as newer, not an int-parse crash; - regex classes are ASCII-only. These strictness choices are mine: A1's Lua reader may answer differently on these edge inputs.
- sharecode.decode() returns the decoded JSON object (integral floats as ints, JSON nulls left out), validated. The writer form, with unknown keys dropped, is in decode_detailed(text).normalized.
- The 49,152-byte payload cap cannot be reached inside a 65,536-character paste (49,152 bytes is already 65,536 Base64 characters). It is kept for parity and tested by raising the text cap with monkeypatch.
- File names: a path-like `file` inside a share code is dropped with a warning (DESIGN 5.5). Only pack_for_sharing (converting v1.1 pack files) shortens a path to its bare .mp3/.ogg name. A file without inGameFile becomes an in-game file, with a warning.
- notEmbeddable is recorded under both q:<artist>\x1f<title> (DESIGN 2.5) and yt:<videoId>. sp:<id> entries (title, artist, durationMs, or notFound for a dead link) are recorded for pasted Spotify links. Both are additions to the DESIGN schema.
- gamedata adds set_imports, touch, flush, flush_all, writer_for and CompanionDataWriter beyond the contract's record_track / set_status / add_import. C2 already calls touch and flush.
- pixel.py's own default region is 512x48, but config.py's DEFAULT_CONFIG (C2) still passes regionW 256 / regionH 32 explicitly. I did not force a 512x48 minimum, because test_transport_pixel asserts the 256x32 capture. Wide or tall v2 strips widen the region automatically.
- packs.KNOWN_DROP_KEYS gained tags and disabled. test_release_docs::test_packs_guide_documents_every_pack_key still passes (PACKS.md already lists them).

### openIssues
- Full suite: 188 failed, 28 errors, none in C1 files and no traceback references a C1 module. - test_addon_* (192) and 4 of 5 test_integration_e2e failures: `Beacon.lua:30: attempt to index local 'P2' (a nil value)`. A3's new Beacon.lua needs ns.Protocol2, which the integrator-owned TOC/addon harness does not load yet. - test_core_cli (7) and the 5th e2e failure: C2 removed `build-map --out` and changed install-addon messages. - test_release_docs (3): README mentions /needle add (Core.lua lacks it), INSTALL wording, newest CHANGELOG version 1.2.0 vs 1.1.0. - test_core_config_zones (1): C2 added DEFAULT_CONFIG keys (links, startWithWindows, updates). - test_core_deck_providers (1): panel.js was replaced by connect.js. - test_v12_ui_drops (5): A2. - test_v12_beacon_pipeline (1, at 4 px it expects y=0 and gets 1) and test_v12_beacon_clearance (1): A3 beacon2/harness.
- My first single-process full-suite run stalled (about 20 s CPU over about 20 minutes) and was stopped. A per-file run (every file under 15 s) and a second single-process run (130 s) both completed. Not reproduced; likely a temporary state while other builders were editing.
- companion/needledropper/__init__.py still says __version__ = 1.1.0, so Data.lua's appVersion is 1.1.0 and the addon's `appVersion < 1.2` check will show 'Update the NeedleDropper app'. That file is not C1's.
- Nothing yet calls engine.prewarm_songs(savedvars.search_only_songs(flavor_root)) or savedvars.backup_savedvariables. The helpers are ready; C2's SavedVariables file watcher has to call them.
- Engine searchFailed notices (unavailable=True when Spotify itself is unusable, e.g. sign-in expired) are not yet mapped by C2's parse_engine_notice to the 'Spotify sign-in expired' notification.

### notesForIntegrator
- WoW API citations: none needed. No C1 file uses any WoW API or template name, so there is nothing to verify against Gethe/wow-ui-source (forever). The one client-side assumption is about Lua 5.1's string.lower: in the client's C locale it lowercases only ASCII letters (next note).
- A1 (Companion.lua Track(drop)): engine keys are 'q:' .. artist .. '\031' .. title with only A-Z lowercased. The text is the frame's cleaned query parts (control characters become spaces, trimmed, possibly cut to fit the 120-byte query). Build the key from the same P2.BuildQuery parts. Prefer an explicit gsub('[A-Z]', ...) over string.lower so the result never depends on the locale. Other keys: 'sp:<22-char id>' (title, artist, durationMs or notFound) and 'yt:<11-char id>' (notEmbeddable). Flags are only ever written as true. imports = { {code=..., active=bool}, ... } is left out when empty.
- A3 (protocol2.py): the engine depends on the current Frame attribute names: type, seq, flags, place_key, drop_id, spotify_id, youtube_id, query, artist/title, start_ms, length_ms, volume, label, set_id, spotify_uri, raw, has(), describe(). CONTRACTS 3.1 calls them spotify/youtube. Renaming them in protocol2.py would break engine.py, transport/pixel.py and transport/chatlog.py; tests/test_v12_engine_flows.py and tests/test_v12_engine_transports.py would catch it.
- C2 (config.py / panel_api.py / cli.py): - Set transport.pixel regionW/regionH defaults to 512/48 (DESIGN 2.3); widening copes in the meantime. - Optional new config key transport.pixel.corner (TOPLEFT default, matching the addon's advanced beaconCorner). - Map on_status change kind 'searchFailed' with unavailable=True to the sign-in-expired notice if wanted. - Wire a SavedVariables watcher to engine.prewarm_songs(savedvars.search_only_songs(paths.flavor_root)) and savedvars.backup_savedvariables(paths.flavor_root, app_dir/'backups'). - cli.py already calls commands_packs.register; set_status, touch and flush are already wired.
- Integrator: - Bump needledropper.__version__ to 1.2.0 (gamedata writes it as appVersion). - engine.py no longer imports mapgen; v1 support is duck-typed through registry=. Deleting mapgen.py is safe for the engine, but tests/test_core_engine.py, test_integration_e2e.py and test_integration_wiring.py still import mapgen and would need updating or retiring with it. - The addon harness/TOC must load Protocol2.lua before Beacon.lua for the test_addon_* suites to run again.
- Companion side of 'Check the link': the engine calls on_link_check(frame, transport) for every TEST-flagged frame and every PING, once per deduplicated frame, including v1 frames from old addons, so the shell can tell an outdated addon apart via status()['addon']['protocol'] == 1 / ['outdated'].

## C2-shell (tests passed: True)

**Test summary:** My area: 344 passed, 1 skipped (the existing live tray-icon test). The command above covers every test file I own except tests/test_release_docs.py. That file is mine, but it checks the docs and the version, and three of its tests fail for reasons outside my changes (see openIssues): test_docs_only_mention_real_needle_subcommands, test_install_guide_covers_the_public_release and test_changelog_has_an_entry_for_this_version.

The new tests cover the requested list:
- the Connect page is served with its token meta, the CSP blocks inline code, and the page has the four cards in order plus basic accessibility checks;
- the retired endpoints return 404 (/api/test, /api/stop, /api/map/build, /api/spotify/resolve, /api/packs, /api/merged, /api/marks, /api/media and others);
- the link-check tick flow, with the engine calling on_link_check, an SSE nudge and one notification; both transports delivering the same frame tick only once; the tick still works while paused;
- tray menu wiring through cli.main with a recording fake TrayIcon: menu lines, pause gate and icon swap, Start with Windows, Open log folder, Quit, notification click;
- Start with Windows is turned on by the first Done with the default tick (fake winreg) and re-checked at every start without overriding Task Manager;
- single instance through /healthz, and a port taken by another program reported in a dialog;
- the crash wrapper puts dead transport and engine threads in /api/state faults, notifies once and restarts the c

**Public interface:** Command line (cli.py)
- No command: the companion runs in the tray. One instance only: if /healthz on the configured deck port answers, the new process opens that instance's Connect page and exits 0. A port held by another program gets a dialog and exit 1.
- The Connect page opens on first run, defined as `not setupComplete or startWithWindows is None` (deck.panelOnStart = "first-run"). It never opens with `--background`, which is the flag the Start with Windows command uses.
- Hidden top-level `--seconds N` quits after N seconds (tests and smoke runs).
- Commands: `run` (same companion, console logging, no tray, `--panel`), `setup` (opens the Connect page, starting the tray app if needed), `spotify-login`, `install-addon [--force]`, `diag`, `test-drop <drop id | zone | "Artist - Title" | Spotify or YouTube link>` (sends a v2 FIRE+TEST frame to the engine), `zones`, `validate`, and `packs` (registered from C1's commands_packs by lazy import; a missing or broken module is ignored).
- `build-map` and `resolve` are hidden; they print why they were removed and exit 2.
- Test seams: `cli.open_url`, `cli.tray_factory`, `cli.fatal_dialog`, `cli._wait_quit`.
- Helpers: `install_addon(cfg, paths, *, source, force, running) -> dict` (copies the bundled addon including Sets/, writes the install receipt, removes stale files from an earlier install by this app and the retired Map.lua/Map.json/Protocol.lua), `copy_addon(src, dest) -> list[str]`, `addon_files(src)`, `make_test_frame(ctx, query)`, `describe_event(ev, transport)`, `make_file_handler(paths, verbose)`.

Companion (panel_api.Companion)
- Constructor: `Companion(cfg, paths, *, deck, config_file, provider_factory, transport_factory, engine_factory, opener, game_running, spotify_http, wow_registry, wow_default, addon_source, engine_kwargs, autostart_winreg, notifier, update_fetch, guard_threads, housekeeping_s, clock, out)`.
- Methods: start/stop, `submit(event, transport)` (the transports' on_event: link bookkeeping, pause gate, then the engine), `on_link_check(frame, transport)`, `on_engine_status(*args, **kw)`, `link_check()`, `state()`, `status_line()`, `set_paused()` / `paused`, `set_autostart()` / `autostart_enabled()` / `recheck_autostart()`, `open_setup()`, `open_logs()`, `install_addon(force)`, `detect_wow()`, `spotify_connect` / `spotify_disconnect` / `spotify_devices`, `update_config(patch)`.
- Attributes: `notifier`, `faults` (FaultLog), `guard` (ThreadGuard), `on_pause_changed`.
- Also exported: `construct_engine(cls, providers, deck, kwargs)`, `parse_engine_notice`, `UpdateChecker`, `releases_api_url`, `parse_release`, `fetch_json`, `ThreadGuard`, `FaultLog`, `component_for_thread`, `is_test_event`, `SCHEMA`.

Connect page API
- Routes: GET /api/state, POST /api/config, POST /api/spotify/connect, POST /api/spotify/disconnect, GET /api/spotify/devices, GET /api/link, POST /api/wow/detect, POST /api/addon/install {force}, POST /api/open {what: "logs"}. Everything else returns 404.
- Patchab

### deviationsFromContract
- Config key `startWithWindows` is three-state: null until the first Done, then true/false. A null value counts as first run, so v1.1 upgraders (who already have setupComplete) see the Connect page once and nothing writes the Run key before the player chooses. Only the first Done sets setupComplete; Spotify sign-in no longer does.
- `/api/open` keeps its v1.1 shape but only accepts {"what": "logs"}; other targets return 400. The link-check status is `GET /api/link` and is also part of /api/state.
- Premium check. Spotify's Feb-2026 migration guide removed `product` from /v1/me for development-mode apps (https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide), and such apps only run for a Premium owner (https://developer.spotify.com/documentation/web-api/concepts/quota-modes). The card therefore shows Premium OK when /v1/me returns 200, "required" when the provider saw a Premium refusal, and "needed for playback" otherwise. It cannot see the plan of a non-owner account.
- No on-disk marker shows that CurseForge or the Wago app manages a folder: they match by file fingerprints and TOC ids, and the BigWigs packager's release.sh only rewrites `## Interface` and keywords. So the companion treats an addon folder as its own only when its install receipt in the app dir matches, or when the folder holds a v1.1 Map.lua-era addon. Anything else counts as someone else's: WowUp markers (wowup_data_addon\ or WowUp\ in AddOns, per WowUp's wowup-addon.service.ts), X-Curse/X-Wago/X-WoWI TOC ids, or an unknown origin. Install/Update is never offered for those, and API `force` overrides only "game running", never ownership.
- The one-folder variant is a second PyInstaller run selected by NEEDLEDROPPER_BUILD=onedir, not a second EXE in the same spec. Two EXE targets in one run would share the work file `NeedleDropper.pkg`. The choice is documented in the spec, the build script and the README.txt quick start.
- Two notifications exist beyond the DESIGN 1.4 list: component thread failures (required by CONTRACTS 3.6) and update available (DESIGN 1.4 step 5). "tray.notify" is implemented as a `tray.Notifier` policy layer in front of `TrayIcon.notify`.
- Pause needle drops is a gate in the shell (`Companion.submit`), plus `engine.stop_playback()` when the engine has it. Test frames still tick the link check while paused.
- `setup` is no longer the v1.1 terminal wizard: it opens the Connect page, starting tray mode if needed. `build-map` and `resolve` are hidden commands that explain they were removed and exit 2. A hidden top-level `--seconds` exists for tests and smoke runs.
- Outside the brief: the deck now reads and drops a refused POST's body (bad Host/Origin/token, unknown path) before answering. Without that, Windows resets the connection, which caused a flaky ConnectionAbortedError and would let a stale Connect page mistake a token 401 for "not running".

### openIssues
- tests/test_release_docs.py has 3 failures; the file is mine but the causes are the docs and the version. (1) test_install_guide_covers_the_public_release: D's INSTALL.md rewrite no longer says "Windowed (Fullscreen)". Worth restoring, because the pixel beacon needs windowed modes. (2) test_changelog_has_an_entry_for_this_version: CHANGELOG's newest entry is 1.2.0 while needledropper.__version__ is still 1.1.0 (integrator bumps it). (3) test_docs_only_mention_real_needle_subcommands: the docs name v1.2 /needle subcommands that Core.lua does not have yet (integrator).
- C1's engine sends `notFound` notices with only the lowercased query key (`q:artist\x1ftitle`), so the "Song not found on Spotify" notification shows lowercased text. Adding artist/title/label to that note would fix it; the shell already uses those fields when present.
- packs.validate_report (C1) still tells users to run the retired `needledropper resolve`.
- Not built because it was not in my card list: DESIGN 4.2's Connect page offer to copy local.mediaDir audio into NeedleDropper_Local\media.
- The windowed exe run from an interactive terminal (e.g. `NeedleDropper.exe diag`) attaches to that console, but the prompt returns before the output. launcher.py documents `start /wait` (cmd) or piping (PowerShell) as the workaround.
- Placeholders until links exist: links.addonPage defaults to a CurseForge search URL because there is no project page yet, and updates.releasesUrl is empty, so the update check stays off until a repository URL is configured.
- Not tested live, only with fakes: Spotify sign-in against real Spotify, and Done / Start with Windows against the real registry. I deliberately did not click Done in the live preview because it would write the user's HKCU Run key.
- With YouTube switched off and Spotify not connected nothing can play, and the page shows no explicit warning for that combination.

### notesForIntegrator
- Failures in files owned by others, with causes: - test_addon_* and test_addon_director (~190): Beacon.lua indexes ns.Protocol2, which the old harness/TOC does not load yet. - test_core_cli x7: build-map retired, `--no-map` gone, Map.json no longer regenerated, banner now says "Connect page", `--help` no longer lists build-map. - test_integration_e2e x5: build-map/mapgen. - test_core_config_zones::test_default_config_matches_spec: add `startWithWindows: None`, `updates: {releasesUrl: '', checkHours: 24}` and `links: {addonPage: ...}` to the expected dict. - test_core_deck_providers::test_serves_panel_and_player_pages: expect /static/connect.js. - test_transport_wincapture::test_find_window_without_match_returns_none: environment-dependent; a real window on this machine matched. - test_v12_ui_static and test_v12_beacon_* failed once during concurrent edits and pass now.
- Any test that calls cli.main([]) must patch cli.tray_factory, cli.open_url, cli.fatal_dialog and needledropper.autostart._registry. Otherwise it creates a real tray icon, opens a browser, shows a dialog, or reads and writes the real registry. test_panel_cli.py's autouse fixture is the pattern.
- Engine wiring for C1's engine: Companion builds it through construct_engine with fade_in_ms, fade_out_ms, log, on_link_check, on_status and addons_dir (when the AddOns folder exists). It reads on_status(status) using change.kind notFound and notEmbeddable, uses set_providers/current_provider/stop_playback when present, and replaces the engine on a WoW folder change and after an engine-thread crash. The shell also calls gamedata.ensure_companion_addon at start and after an install, gamedata.set_status when Spotify/YouTube/transport state changes, gamedata.touch when the game process appears, and gamedata.flush at stop.
- Build notes: in tools/build_release.ps1 the pipe through ForEach-Object in Invoke-NativeOutput is required, because PowerShell only waits for (and reads the exit code of) a GUI-subsystem exe when its output is piped. gen_sets.py --check passes but warns that Sets\*.lua are not in the TOC yet (integrator's TOC update).
- Verification sources: - warcraft.wiki.gg/wiki/TOC_format: game-type suffix _Forever/_Camelot beats family _Mainline, which beats the plain TOC. - Gethe/wow-ui-source forever branch: version.txt = 1.60.1.70790; Interface/AddOns/Blizzard_Minimap/Blizzard_Minimap.toc uses [AllowLoadGameType camelot] / mainline. - WowUp: wowup-electron/src/app/services/wowup/wowup-addon.service.ts writes wowup_data_addon/{data.lua, wowup_data_addon.toc}; WowUp.Addon AddonManagers.md. - BigWigsMods/packager release.sh injects no project ids. - Spotify: February-2026 migration guide and the quota-modes page. - The Connect page tells players to type /needle test, which exists in Core.lua's ORDER and COMMANDS.
- Release build: ran tools/build_release.ps1 -SkipTests with output in the scratchpad (not the repo) using PyInstaller 6.22.3. Results: NeedleDropper.exe one-file 9.3 MB, NeedleDropper-<v>-win64-folder.zip 9.2 MB, addon zip with Sets/, SHA256SUMS over four files. The frozen windowed exe passed --version and validate and ran cleanly in tray mode. Nothing was written under Program Files or the real %APPDATA%.

## D-docs (tests passed: False)

**Test summary:** tests/test_release_docs.py: 30 passed, 3 failed. None of the 3 is a doc bug. (1) Waiting on integrator code: test_docs_only_mention_real_needle_subcommands fails because `/needle add`, `/needle soundtracks`, `/needle import` and `/needle share` (DESIGN §4.8) are not in Core.lua's ORDER/COMMANDS/USAGE yet; test_changelog_has_an_entry_for_this_version fails because companion/needledropper/__init__.py still has __version__ = "1.1.0" while the newest entry is [1.2.0]. (2) A stale assertion C2 must fix: test_install_guide_covers_the_public_release requires "Windowed (Fullscreen)". The real client label is VIDEO_OPTIONS_WINDOWED_FULLSCREEN = "Fullscreen (Windowed)", and the Display Mode dropdown has only that option and "Windowed", no exclusive fullscreen (Blizzard_SettingsDefinitions_Shared/Graphics.lua L788-803). The docs use the real label on purpose. The other v1.1 strings that test checks (**Status**/**Music services**/**Soundtrack**/**Game & addon**/**Advanced**, Rebuild map, build-map, Resolve soundtrack, %APPDATA%\NeedleDropper\media) still pass. They appear only in true "Upgrading/Coming from 1.1" notes. C2 should still change those assertions to v1.2 facts: the Connect page cards, Add drop here, share codes. Extra checks I ran: every relative link and #anchor in all 11 files resolves (MIXTAPE, SOUNDTRACK and FIRST-TEST included, which the test skips). No banned markers. Fences are balanced. Every Apple Music mention is negated. The PACKS.md JSON example passes packs.parse

**Public interface:** Player docs for v1.2.0. Anchors other docs and UI copy may link to:
- TROUBLESHOOTING.md: #beacon-not-detected, #the-beacon-overlaps-your-player-frame, #drops-dont-start, #zone-music-doesnt-come-back, #editing-drops-in-game, #share-codes (reason table: notcode/foreign/newer/cutoff/damaged/toobig/invalid), #spotify, #youtube, #chat-log-link, #the-needledropper-app, #installing-the-addon, #where-the-logs-are, #needle-command-reference, #in-game-options, #key-bindings, #companion-settings.
- PACKS.md (title "Soundtracks and share codes"): #the-built-in-soundtracks, #the-window, #add-a-drop-where-you-stand, #the-drop-form, #change-a-song-from-a-built-in-soundtrack, #your-own-soundtracks, #which-song-plays-the-priority-order, #a-different-list-on-one-character, #share-codes, #try-it (a real one-drop code), #for-soundtrack-authors-the-pack-format, #drops, #places-zones-landmarks-and-instances, #the-packs-commands, #guidelines-for-shared-soundtracks, #coming-from-11.
- INSTALL.md: #1-install-the-addon, #from-the-addon-zip, #2-first-login, #3-optional-the-needledropper-app, #windows-protected-your-pc, #4-the-connect-page, #5-check-the-link, #game-settings-for-the-beacon, #in-game-audio-files, #upgrading-from-11, #updating, #uninstalling.
- PROVIDERS.md: #which-service-plays-a-drop, #spotify-premium, #why-every-player-creates-their-own-developer-app, #signing-in-with-a-different-spotify-account-user-management, #how-the-app-finds-a-song, #the-player-window-must-stay-visible, #in-game-audio-files, #apple-music.
- PRIVACY.md: #the-beacon-strip, #the-optional-chat-log-link, #share-codes. FIRST-TEST.md is the owner's 12-row DESIGN §6.4 checklist.
- UI labels used: Drops | Soundtracks | Journal; Add drop here; Change song here; Turn off here; Revert to Azeroth Mixtape / Revert to built-in; Showing: / What plays (all active); Filter; Share… / Import… (U+2026); Save now (reloads UI); Use different soundtracks on this character; My Drops; Connect page cards World of Warcraft / Spotify / YouTube / Check the link, plus Start with Windows / Done.
- Key binding names are quoted from the new Locale.lua: "Open or close the NeedleDropper window", "Add a needle drop where you stand", "Stop the needle drop".

### deviationsFromContract
- Update check is documented as optional and off until `updates.releasesUrl` is set in config.json. That is how C2's config.py and panel_api.py behave (empty URL = off). DESIGN §1.4.5 describes an unconditional daily GitHub check. If the release build will set a default URL, PRIVACY/INSTALL/TROUBLESHOOTING/CHANGELOG/README should be reworded to 'daily'.
- `beaconCorner` is documented as advanced (`/needle set` only) with 'leave it at topleft: the 1.2 app looks for the beacon in the top-left corner'. DESIGN §2.3 gives the capture region as the top-left 512 × 48 px, and pixel.py only polls top-left, which contradicts the §2.3 warning text '…or set beaconCorner'. The docs quote that warning verbatim but steer players to Edit Mode instead.
- The packs CLI is documented with the CONTRACTS §3.4 names (`NeedleDropper.exe packs validate|encode|decode|convert-legacy`), not the `packs import <code>` / `packs export --code` in DESIGN §5.4. commands_packs.py does not exist yet.
- 'Share…', 'Import…', 'Open setup…', 'More…' and 'New soundtrack…' use the U+2026 ellipsis as in DESIGN §4.4/§4.5 and tray.py. The task text wrote ASCII 'Share...'.
- Display mode uses the client's real label 'Fullscreen (Windowed)' (GlobalStrings VIDEO_OPTIONS_WINDOWED_FULLSCREEN), not the v1.1 docs' wrong 'Windowed (Fullscreen)'. This is what makes one C2 doc test fail.
- Share-code reason messages in TROUBLESHOOTING quote companion/needledropper/sharecode.py `_MESSAGES` and `describe_reason`, because the in-game Lua strings don't exist yet. The Spotify/YouTube link errors quote A1's Soundtracks.lua strings ('…right-click the song, then Share, then Copy Song Link.', 'That's a YouTube playlist link: open the video and copy its own link.') rather than DESIGN's arrow wording.

### openIssues
- The docs tell players to run `NeedleDropper.exe diag`, `NeedleDropper.exe --version`, `NeedleDropper.exe packs …` and `--help` from a command prompt. CONTRACTS §3.6 makes the exe windowed (PyInstaller console=False), so its output would vanish unless C2 attaches to the parent console (AttachConsole(ATTACH_PARENT_PROCESS)). Otherwise these doc instructions need another route.
- The location of the SavedVariables backups (DESIGN §2.1: 10 rolling copies) is not specified anywhere. PRIVACY/INSTALL mention 'backup copies' without a path. Add the folder name once C1 implements it.
- PRIVACY says `cache\` holds the Spotify search matches and `update-check.json` holds the update-check state (from panel_api `paths.update_state`). Confirm both file names when C1/C2 finish.
- A 1.2 app only plays a 1.1 addon's v1 frames if the shell passes a map registry (engine.py L40-42). Otherwise it logs 'update the addon' and ignores them. The docs therefore make no compatibility claim and say 'update both parts'.
- The character-select path 'Menu → AddOns' is verified in code (GlueMenuFrame.lua L89-107 adds ADDONS; CharacterSelectNavBar.lua L264-270 adds a MenuButton with CHARACTER_SELECT_NAV_BAR_MENU). The button's English text 'Menu' is a glue string I could not find in the enUS GlobalStrings dump.
- `taintLog` (used in FIRST-TEST and TROUBLESHOOTING) is an engine CVar and does not appear in the UI Lua source. Its existence on Forever is confirmed only by Ketho/BlizzardInterfaceResources forever Resources/CVars.lua L1569 ('taint.log').
- Not verifiable without a live client, and marked so in CHANGELOG ('Not yet verified on a live client') and covered by FIRST-TEST: C_EncodingUtil zlib behaviour (`/needle selftest share`), beacon clearance on unusual layouts, the window's Escape/combat behaviour, and taint.

### notesForIntegrator
- Core.lua: add `add`, `soundtracks`, `import` and `share` to ORDER, COMMANDS and USAGE. That clears test_docs_only_mention_real_needle_subcommands; TROUBLESHOOTING already documents all four, plus bare /needle toggling the window. Keep `toast` and every other v1.1 subcommand, because the command-reference test needs each one documented and the docs keep them.
- Docs assume `/needle journal` opens the Journal tab and `/needle journal print [count]` keeps the old count argument (last 15 by default). Docs also assume `/needle share [name]` takes a soundtrack name, e.g. `/needle share My Drops`, and `/needle import` takes no argument. They also document `/needle add Toto - Africa` printing 'Added Goldshire: Toto · Africa to My Drops.' (DESIGN §4.4).
- Core.lua ns.SETTINGS: change beaconCellPx to min 2, max 4, default 3, and drop 'Match the companion's setting' from its tip (schema-2 migration 4 → 3 is documented). Add `beaconCorner` as an advanced string setting with lowercase options topleft/topright/bottomleft/bottomright, default topleft: CoerceSetting lowercases input, so uppercase options could never match. The TROUBLESHOOTING options table documents both settings.
- Bump companion/needledropper/__init__.py __version__ to 1.2.0, and the TOC `## Version: 1.2.0`. test_changelog_has_an_entry_for_this_version expects the newest entry [1.2.0] - 2026-11-05.
- SelfTest.lua: the docs describe `/needle selftest beacon` per DESIGN §6.4/§6.1: it shows the largest fitted strip for 10 s (today 60 s), sends a PING with the TEST flag, and prints gap, rows, cell size and strip size. It prints the exact overlap warning 'the beacon overlaps your player frame: move the frame in Edit Mode or set beaconCorner'. `/needle selftest share` prints OK, the round trip and CompressString's first byte (0x78). Docs also rely on: the first-login chat line from DESIGN §4.8; Options.lua's 'Open NeedleDropper' button at the top, a 'Preview' title-card button and a 'Send a test signal' button.
- Lua share-code messages (A1 Share.lua / A2 dialogs) should mirror companion sharecode._MESSAGES, because TROUBLESHOOTING quotes them: 'This is not a NeedleDropper soundtrack code.', 'This code was made with a newer NeedleDropper: update the addon.', 'The code was cut off…', 'The code is damaged…', 'This code is too big to be a NeedleDropper soundtrack.', 'This soundtrack has problems and cannot be imported.', 'This is a <Name> string, not a NeedleDropper soundtrack.'
- C2 should update tests/test_release_docs.py for v1.2. Replace 'Windowed (Fullscreen)' with 'Fullscreen (Windowed)'. Move the v1.1 control-panel assertions (**Status**…**Advanced**, Rebuild map, build-map, Resolve soundtrack, %APPDATA%\NeedleDropper\media) to v1.2 facts: **World of Warcraft**/**Spotify**/**YouTube**/**Check the link**, Add drop here, `!NeedleDropper:1!`, NeedleDropper_Local\media, logs\needledropper.log. The docs mention the old terms only in honest upgrade notes.
- Before publishing, remove or rewrite the CHANGELOG 1.2.0 section 'Not yet verified on a live client' once the owner has run docs/FIRST-TEST.md. CHANGELOG.md ships in the addon zip via .pkgmeta manual-changelog.
- WoW UI facts were checked against Gethe/wow-ui-source forever @ eac30af597 (1.60.1.70790, sparse clone), Interface/AddOns/...: - Blizzard_Minimap/Mainline/AddonCompartment.lua L102-150: the TOC Func/OnEnter/OnLeave fields; buttonName is passed. - Blizzard_Minimap/Camelot/AddonCompartment.xml: a numbered DropdownButton under MinimapCluster, anchored to GameTimeFrame. - Blizzard_Settings_Shared/Blizzard_SettingsPanel.xml L26-43: the Game and AddOns tabs. - Blizzard_GameMenu/Shared/GameMenuFrame.lua L189 and L227: the Options and AddOns buttons. - Blizzard_GlueMenuFrame/Mainline/GlueMenuFrame.lua L89-107 and Blizzard_CharacterSelectNavBar/CharacterSelectNavBar.lua L264-270: Menu → AddOns at character select. - Blizzard_AddOnList/AddonList.xml L131-137: the ForceLoad checkbox (ADDON_FORCE_LOAD). - Blizzard_SettingsDefinitions_Frame/Keybindings.lua L220-226 (GetBindingCategoryName) and L278-325: the Keybindings category in the Gameplay group. - Blizzard_SettingsDefinitions_Shared/Graphics.lua L788-803: only two display modes. - Blizzard_EditMode/Shared/EditModePresetLayoutsManager.lua L8 and L18: the Modern and Classic presets. - Blizzard_APIDocumentationGenerated/UIFileAssetAPIDocumentation.lua L27-39: IsKnownFile. - Blizzard_APIDocumentationGenerated/EncodingUtilDocumentation.lua L11, L62, L215: CompressString, DecompressString, Zlib = 1. - Blizzard_ChatFrameBase/Shared/SlashCommands.lua L845-853: /chatlog toggles LoggingChat. - Blizzard_ScriptErrorsFrame/Blizzard_ScriptErrorsFrame.lua L25-31 and L130: the scriptErrors CVar. English label text came from Ketho/BlizzardInterfaceResources forever Resources/GlobalStrings/enUS.lua (1.60.1.70532): ADDONS 'AddOns', ADDON_FORCE_LOAD 'Load out of date AddOns', ADDON_INTERFACE_VERSION 'Out of date', GAMEMENU_OPTIONS 'Options', SETTINGS_TAB_ADDONS 'AddOns', SETTINGS_KEYBINDINGS_LABEL 'Keybindings', VIDEO_OPTIONS_WINDOWED 'Windowed', VIDEO_OPTIONS_WINDOWED_FULLSCREEN 'Fullscreen (Windowed)', HUD_EDIT_MODE_MENU 'Edit Mode', HUD_EDIT_MODE_PLAYER_FRAME_LABEL 'Player Frame', LAYOUT_STYLE_MODERN 'Modern', LAYOUT_STYLE_CLASSIC 'Classic'. The scriptErrors and taintLog CVars are confirmed by that repo's Resources/CVars.lua L1345 and L1569.

## A3-protocol (tests passed: True)

**Test summary:** A3 area: 844 passed in 27 s (the command above). That covers the Protocol2 contract (byte-identical frames, chat lines and cells from Lua and Python; the fit rule at every budget from 0 to 267 and at 2/3/4 px row caps; the geometry helpers; and the DESIGN 2.3 measured-size table: 7→96x3, 22→240x3, 60→288x6, 86→240x9, 95→288x9, 141→fitted 113→336x9). It also covers the beacon2 decoder (adaptive strips in the 512x48 region at 100/125/150%), and emit → textures → BGRA → locate_and_decode at 2/3/4 px × 100/125/150%. Hold timings, the clearance guard, ChatLink, Emit, the static checks and an integrated run with the documented Director/SelfTest patch applied in memory are covered too.

FULL SUITE (whole repo, during concurrent work): 182 failed, 3314 passed, 4 skipped, 28 errors in 141 s.
(a) 180 of the 182 failures and all 28 errors have one cause: TOC-driven AddonHarness tests (test_addon_toast 71, test_addon_director 33 + the 28 fixture errors, test_addon_misc 27, test_addon_transport 18, test_addon_audio 13, test_addon_integration 2, 4 of test_integration_e2e). They fail at load with `Beacon.lua:30: attempt to index local 'P2' (a nil value)` because NeedleDropper.toc does not list Protocol2.lua yet (integrator fix, see notes).
(b) The rest are outside A3 and unrelated to my changes:
- test_core_cli: 7 (build-map / install-addon changed by C2).
- test_core_config_zones::test_default_config_matches_spec: new config keys.
- test_core_deck_providers::test_serves_panel_and_player_pa

**Public interface:** LUA ns.Protocol2 (additions/changes):
- EncodeFrame(ev [, maxBytes [, prefer]]): fits the frame to maxBytes (default and maximum 262, floored, clamped 7..262) by the DESIGN 2.3 rule. The order is setId → label → query (when a Spotify/YouTube id is present) → dropId → shorten the query on a UTF-8 boundary → volume → keep only the preferred id (prefer "spotify"/"youtube", case-insensitive; default ev.prefer, else youtube). placeKey, start, length, flags and the last id are never removed.
- EncodeCells(ev, geom): with geom.maxRows (floored into 1..RowsCap(cellPx)) it fits to Capacity(maxRows) and uses the adaptive layout. The result gains maxRows and overflow (true when the irreducible frame needs more rows; the grid stays complete and valid).
- EncodeCellsFromBytes(bytes, geom): adaptive when geom.maxRows is set.
- RowsCap(px) = max(1, floor(9/px)): 2→4, 3→3, 4→2.
- Capacity(rows) = min(262, floor((rows*112-7)*3/8)): 39/81/123/165.
- AdaptiveLayout(frameLen) → rows, cols.
- NormalizeGeometry(geom) returns maxRows.
- HasFlag(flags, f), PadLine(seq [, len]), PadLineCount(bytes [, len]).
- Constants MAX_STRIP_PX=9, PAD_PREFIX, PAD_LINE_LEN. FLAG / EV / EV_NAME as before.

EMIT.LUA (new):
- ns.BuildEvent(evType, drop, extraFlags, opts{seq, prefer, label}) → {type, seq, flags=extraFlags, dropId, placeKey, spotify, youtube, artist, title, startMs, lengthMs (from durationMs > 0), volume, label (localised), setId, prefer}. The label comes from C_Map.GetMapInfo, ns.AreaName or the live subzone, or the instance name while inside it, falling back to drop.label / subzone. CANCEL carries the identity only; HELLO/PING/NONE carry no fields; drop id "test" carries its label but no song.
- ns.Emit(evType, drop|nil, extraFlags, opts{noPad, forceChat}) → event with seq. It bumps and persists ns.db.lastSeq, sets flags = ns.ComputeFlags(extra), stamps t, keeps ns.LastEvent and ns.Events (200), then calls ns.Beacon.Show(ev) and ns.ChatLink.Send(ev, {noPad, force}), each pcall-guarded with errors sent to the error handler.
- ns.ComputeFlags(extra): v1 semantics, reading ns.Director.active lazily.
- ns.PreferredProvider(): ns.Companion.Status().spotify == "connected"/true (or a table that says connected), else the raw NeedleDropper_CompanionData.status, else "youtube".
- ns.PlaceLabel(drop), ns.EmitState.

BEACON.LUA ns.Beacon: Show(ev), Layout(), Status() → {enabled, shown, cellPx (drawn), settingCellPx, cellUnits, rows, cols, widthPx, heightPx, size "96x3", maxRows, gap, corner, measuredFrom, lastFrameBytes, lastFrameType, lastFrameSeq, variant, step, overflow, current, queued, dropped, warnings}. Also ForceShow(sec), Choose(ev, px, gap, corner) (a pure function), MeasureClearance(), RowsCapFor(px, gap), Warnings(), SettingCellPx() (2..4, default 3), Corner() (beaconCorner validated, default TOPLEFT), PixelFactor(), CellSize(px), Pump().

CHATLINK.LUA ns.ChatLink: Send(ev, opts{force, noPad}), SendLines, Flush, Filter, InstallFilter, IsLogging, EnsureLogging, StopLoggin

### deviations
- The 2 px fallback is part of the clearance guard only. Without a clearance limit, the beacon fits the frame to the configured cell size's 9 px cap, as in DESIGN 2.3's measured table (141 B → 113 B → 336x9 at 3 px). With a clearance limit, Choose tries in order: the nominal frame at the configured px; then the same frame at 2 px; then the frame fitted to max(1, rows that fit at 2 px); and if it still needs more rows, draws them.
- Minimal overflow: when the frame cannot shrink below one row's capacity (placeKey + start + length + a Spotify id = 43 B > 39 B), the beacon draws the rows it needs (two 2 px rows) and records an overflow warning, instead of an undecodable single row.
- ARM's 3 s full song counts drawn time and is preemptible: a FIRE, CANCEL or PING replaces it at once. After a PING the rest of the song time resumes; a FIRE or CANCEL replaces the ARM state entirely.
- After a CANCEL or PING hold, the strip returns to the steady state (idle HELLO / short ARM / HELLO+DROP_ACTIVE) instead of keeping the CANCEL/PING on screen; DESIGN left this open. After a CANCEL the HELLO keeps DROP_ACTIVE when another drop is still playing.
- The steady frames the beacon makes up itself (placeKey-only ARM, HELLO+DROP_ACTIVE after FIRE, HELLO after CANCEL) reuse their event's seq. They are never emitted (no lastSeq bump, no ns.Events entry, no chat line). TEST is cleared in the two HELLOs, and LOCAL_PLAYBACK too unless DROP_ACTIVE is set; the short ARM keeps the ARM's flags so its dedupe key is the same.
- The chat line carries the event's full frame (262-byte budget), not the beacon's tighter fitted bytes; both share Frame.key. PROTOCOL-v2 says the bytes are the same only when the beacon did not have to fit tighter.
- Frame.key now includes flags, per CONTRACTS 3.1 (it was (seq, type, placeKey)).
- Under the new rule an event with every field at its cap is 222 B / 6 rows at 112 columns (setId and the whole label go), not DESIGN 2.3's 262 B / 7 rows. That row predates the drop-the-label rule; fitted ≤ 123 B at 3 px still holds (101 B), and frames up to 262 B still occur.
- Beacon.lua and ChatLink.lua index ns.Protocol2 at load and are deliberately not guarded, so they raise a load error under the current TOC.
- Beacon.lua clamps beaconCellPx to 2..4 (default 3) and validates beaconCorner at read time. The ns.DEFAULTS/ns.SETTINGS entries are left to the integrator (Core.lua).
- Clearance is measured only for the TOPLEFT corner. Other corners use the nominal 9 px caps.
- The decoder's reported y for a one-row strip at the capture's top edge can be off by 1 px (no row boundary above it). This is cosmetic; the decode is unaffected, and the tests allow a difference of up to 1.

### openIssues
- Every TOC-driven AddonHarness test (test_addon_*, 4 of test_integration_e2e) fails at load with `Beacon.lua:30: attempt to index local 'P2' (a nil value)` until the TOC lists Protocol2.lua and Emit.lua (CONTRACTS 2.1 order) and Director/SelfTest get the patch below.
- These existing integrator-owned tests still assert v1 even after the TOC fix and need rewriting for v2: test_addon_transport.py (31 cells, NDROP1, as_event with n/hash), parts of test_addon_misc.py and test_integration_e2e.py, and addon_harness.beacon_cells/beacon_event/as_event. The replacement readers are v12_beacon_harness.texture_rects/beacon_grid/beacon_decoded/beacon_bgra.
- /needle selftest beacon (SelfTest.lua, integrator) should print Beacon.Status() gap/rows/cellPx/size/warnings. DESIGN 6.4's 'show the largest fitted song strip for 10 s' needs a choice about which frame type to draw without making the companion play or pre-warm; it can use ns.Beacon.ForceShow plus Beacon.Choose(ev, px, gap) to compute sizes.
- The clearance formula (GetTop * effectiveScale / pixelFactor) and the PlayerFrame/TargetFrame art heights can only be checked in the client. The owner should compare Status().gap with a screenshot in DESIGN 6.4 step 2.
- Not affected by my changes, but failing in the full run: test_core_cli (7), test_core_config_zones (1), test_core_deck_providers (1), test_integration_e2e::test_map_built_by_cli... (1), test_release_docs (3), test_transport_wincapture (1).

### notesForIntegrator
- Director.lua: delete lines 27-28 (`ns.Events = {}` / `ns.LastEvent = nil`), 45-48 (`local function addFlag`) and 50-97 (the whole '-- emit --' section: `D.ComputeFlags` and the v1 `function ns.Emit`). Emit.lua now defines ns.Events, ns.LastEvent, ns.ComputeFlags and ns.Emit. Change line 17 to `local P = ns.Protocol2` (Director uses P.EV, P.FLAG, P.HasFlag, P.EV_NAME, all now in Protocol2). Line 19 `MAX_EVENTS` becomes unused and can go. Optionally alias `D.ComputeFlags = ns.ComputeFlags`.
- If that patch is skipped, Director.lua (which loads after Emit.lua in CONTRACTS 2.1 order) overrides ns.Emit with v1 events; and once Protocol.lua is deleted, `local P = ns.Protocol` is nil and Director fails to load. tests/v12_beacon_harness.py `integrated_source()` applies exactly this patch in memory, and tests/test_v12_beacon_integration.py proves it works with the real Director, Location, Toast, Options and SelfTest.
- SelfTest.lua line 8: `local P = ns.Protocol` → `local P = ns.Protocol2` (it uses EV.PING and PadLine).
- TOC: Protocol2.lua before Core.lua; Emit.lua after Companion.lua and before Location/Beacon/ChatLink/Director (CONTRACTS 2.1); delete Protocol.lua. Beacon.lua and ChatLink.lua index ns.Protocol2 at file load on purpose (no guard).
- Core.lua settings: beaconCellPx default 3, min 2, max 4, tip 'Physical pixels per beacon cell' (drop 'Match the companion's setting'). Add beaconCorner as type string, advanced = true, default 'topleft', options topleft/topright/bottomleft/bottomright. Gotcha: ns.CoerceSetting lower-cases string input before matching options[i][1], so the option values must be lowercase; Beacon.Corner() upper-cases whatever is stored. Options.lua must skip advanced entries. The schema 1→2 migration (4 → 3) is A1/integrator work.
- Director.StatusLines prints `b.cellPx`, which is now the drawn cell size (it can be 2 under the clearance fallback); Status().settingCellPx is the configured one. Status() keeps `enabled`, `cellPx` and `current` for v1 callers.
- For C1 (engine/chatlog/pixel): Frame.key is now (seq, type, place_key or 0, flags). Frame.spotify/.youtube are aliases of spotify_id/youtube_id. protocol2.pad_line/pad_line_count exist; ignore NDPAD lines. Chat lines carry the full 262-budget frame while the beacon may draw a tighter fitted frame of the same event (same key). The pixel transport should capture beacon2.capture_size(scale) at the top-left (512x48 physical px at 100%). engine.py:332's `.key` is the v1 models.Event key, which is unchanged; no other test or companion code uses Frame.key or builds CellGrid/Geometry positionally.
- For D (docs): DESIGN 2.3's 'worst case 262 B / 7 rows' row predates the drop-the-label rule; it is now 222 B / 6 rows (≤ 123 B at 3 px still holds). docs/PROTOCOL-v2.md has the final fit rule, geometry, measured table, holds, clearance guard and a v1.2 integration checklist.
- WoW APIs checked on the Gethe/wow-ui-source forever branch: PixelUtil.GetPixelToUIUnitFactor() = 768/physicalHeight; TextureBase SetSnapToPixelGrid(bool) and SetTexelSnappingBias(normalizedValue); ScriptRegion GetTop/GetLeft (MayReturnNothing, SecretWhenAnchoringSecret, hence pcall + SafeNumber) and GetEffectiveScale; SetFrameStrata/SetFrameLevel; EventRegistry:TriggerEvent('EditMode.Exit') in EditModeManager.lua around L124, with CallbackRegistryMixin:RegisterCallback(event, func, owner), where the owner must not be a number; PlayerFrame.xml L23/L48 PlayerFrameContainer.FrameTexture and TargetFrame.xml L58/L78 TargetFrameContainer.FrameTexture; Enum.AddOnRestrictionType.Chat = 5; C_ChatInfo.InChatMessagingLockdown; ChatFrameUtil.AddMessageEventFilter; SendSystemMessage and LoggingChat (used by SlashCommands.lua).
