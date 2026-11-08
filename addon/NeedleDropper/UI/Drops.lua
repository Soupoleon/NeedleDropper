local ADDON, ns = ...
-- UI/Drops.lua: the Drops tab (DESIGN-v1.2 §4.4), the window's default tab.
--
--   top bar  Showing: [What plays (all active) v]  [Search]  [Filter v]  [Add drop here]
--   left     every drop of the view, sorted by client-localised zone then landmark; two lines per
--            row, source letters S Y F and state words on the right, [Test] on hover, right-click
--            for the context menu
--   right    the detail form (place, artist, title, Spotify, YouTube, in-game file, More...), or
--            for a built-in drop a read-only form with [Change song here] / [Turn off here]
--   footer   [Test] [Stop] [Delete]; a deletion shows "Deleted <place>. [Undo]" for 15 s
--
-- Every edit goes through ns.Soundtracks (CONTRACTS 2.5); the window never writes SavedVariables
-- itself. Fields commit on Enter or when they lose focus; Escape restores the field's last value
-- and leaves the field, so a second Escape closes the window.
--
-- Templates (all on the CONTRACTS 2.9 allowlist, checked on Gethe/wow-ui-source@forever):
--   WowStyle1DropdownTemplate / WowStyle1FilterDropdownTemplate + :SetupMenu / :GenerateMenu
--       Blizzard_Menu/Mainline/MenuTemplates.xml L3 / L66, Blizzard_Menu/DropdownButton.lua L237 / L255
--   rootDescription:CreateRadio / CreateCheckbox / CreateButton / CreateTitle / CreateDivider,
--   :SetScrollMode, MenuUtil.CreateContextMenu   Blizzard_Menu/MenuUtil.lua L151, L198-L285; Menu.lua L227
--   SearchBoxTemplate / InputBoxInstructionsTemplate (.Instructions, .nextEditBox / .previousEditBox
--       via EditBox_OnTabPressed)              Blizzard_SharedXML/Shared/InputBox/InputBoxTemplates.xml L209 / L180, .lua L1
--   WowScrollBoxList + MinimalScrollBar, CreateScrollBoxListLinearView, SetElementExtent (required for
--       Lua-built rows: ScrollBoxListView.lua L41-L52), ScrollUtil.InitScrollBoxListWithScrollBar,
--       CreateDataProvider, ScrollBoxConstants.RetainScrollPosition
--                                              Blizzard_SharedXML/Shared/Scroll/ScrollTemplates.xml L4, MinimalScrollBar.xml L15,
--                                              ScrollBoxLinearView.lua L246, ScrollUtil.lua L137 / L215, DataProvider.lua L277, ScrollBox.lua L17
--   UIRadioButtonTemplate (.text) / UICheckButtonTemplate (.Text)   Shared/Button/CheckButtonTemplates.xml L4 / L50
--   MinimalSliderWithSteppersTemplate :Init / .Event.OnValueChanged  Shared/Slider/MinimalSlider.xml L34, MinimalSlider.lua L126
--   ScrollingEditBoxTemplate :GetEditBox / :GetInputText / RegisterCallback  Shared/Scroll/ScrollTemplates.xml L21, ScrollTemplates.lua L123 / L192
--   C_UIFileAsset.IsKnownFile                    Blizzard_APIDocumentationGenerated/UIFileAssetAPIDocumentation.lua L27
--   C_Map.GetMapChildrenInfo(uiMapID, mapType)   Blizzard_APIDocumentationGenerated/MapDocumentation.lua L198

local UI = ns.UI
local W = UI._W
local L = W.L
local esc, trim, nonEmpty = W.Esc, W.Trim, W.NonEmpty
local tinsert, tremove = table.insert, table.remove

local Dt = {}
W.Drops = Dt

local MEDIA = "Interface\\AddOns\\NeedleDropper_Local\\media\\"
local ROW_EXTENT = 36
local LIST_W = 300
local LABEL_W = 86
local UNDO_SECONDS = 15
local MAX_TAGS = 8
local AZEROTH = 947

local state = {
  showing = nil,        -- "mix" or a set ref
  search = "",
  filters = {},
  selected = nil,       -- { ref =, id = }
  merged = {},          -- drop id -> Merged() entry (what plays)
  rows = {},
  more = false,
  error = nil,
  pendingLandmark = false,
  emptyPlace = nil,
}
Dt.state = state

local ui = {}            -- widgets (also reachable as NeedleDropperFrame.drops.<Key> for tests)
local form = {}
local undoTimer

local function S() return ns.Soundtracks end

local function showing()
  if not state.showing then
    local u = W.UIDB()
    state.showing = (u and type(u.showing) == "string" and u.showing) or "mix"
  end
  if state.showing ~= "mix" and not S().Exists(state.showing) then state.showing = "mix" end
  return state.showing
end

local function setShowing(ref)
  state.showing = ref
  local u = W.UIDB()
  if u then u.showing = ref end
end

local function isActive(ref)
  for _, r in ipairs(S().ActiveRefs()) do
    if r == ref then return true end
  end
  return false
end

function Dt.SelectedDrop()
  local sel = state.selected
  if not sel then return nil end
  return S().GetDrop(sel.ref, sel.id)
end

local function editable()
  local sel = state.selected
  return sel ~= nil and not W.IsBuiltinRef(sel.ref) and Dt.SelectedDrop() ~= nil
end
Dt.Editable = editable

-- -------------------------------------------------------------- places --

-- Use the same guarded area lookup as Add drop here, including the raw map and
-- known landmark fallback when the client has no exploration answer.
function Dt.CurrentAreaID(mapID, subzone, rawMapID)
  local Loc = ns.Location
  if Loc and Loc.CurrentAreaID then return Loc.CurrentAreaID(mapID, subzone, rawMapID) end
  return nil
end

-- Where the player stands, as drop place fields: an instance, a landmark (subzone text that is
-- not the zone name) or the zone. nil, reason when the client cannot tell.
function Dt.CurrentPlace()
  local Loc = ns.Location
  if not (Loc and Loc.Snapshot) then return nil, L["NeedleDropper cannot tell where you are right now."] end
  local snap = Loc.Snapshot()
  if snap.inInstance then
    if not snap.instanceID then return nil, L["NeedleDropper cannot tell which instance this is."] end
    return { kind = "instance", instanceID = snap.instanceID, label = nonEmpty(snap.instanceName) or nonEmpty(snap.zoneName) }
  end
  if not snap.zoneMapID then return nil, L["NeedleDropper cannot tell where you are right now."] end
  local zoneName = nonEmpty(snap.zoneInfoName) or nonEmpty(snap.zoneName)
  local sub = nonEmpty(snap.subzone)
  if sub and string.lower(sub) ~= string.lower(zoneName or "") then
    return { kind = "subzone", mapID = snap.zoneMapID, subzone = sub, label = sub,
             areaID = Dt.CurrentAreaID(snap.zoneMapID, sub, snap.rawMapID) }
  end
  return { kind = "zone", mapID = snap.zoneMapID, label = zoneName }
end

-- UpdateDrop fields that move a drop to `place` (false clears a key).
local function placeFields(place)
  return { kind = place.kind, mapID = place.mapID or false, areaID = place.areaID or false,
           subzone = place.subzone or false, instanceID = place.instanceID or false,
           label = place.label or false }
end

-- ---------------------------------------------------------- link checks --

local function spotifyProblem(text)
  local l = string.lower(text)
  if string.find(l, "spotify", 1, true) and (string.find(l, "album", 1, true) or string.find(l, "playlist", 1, true)
     or string.find(l, "artist", 1, true) or string.find(l, "show", 1, true) or string.find(l, "episode", 1, true)) then
    return L["That's an album or playlist link: in Spotify right-click the song > Share > Copy Song Link"]
  end
  return L["That is not a Spotify song link. In Spotify right-click the song > Share > Copy Song Link"]
end

local function youtubeProblem(text)
  if string.find(string.lower(text), "list=", 1, true) then
    return L["That's a playlist link: open the video itself and copy its link"]
  end
  return L["That is not a YouTube video link"]
end

-- Seconds from a t= / start= parameter ("83", "83s", "1m23s", "1h2m3s").
function Dt.YouTubeStart(text)
  if type(text) ~= "string" then return nil end
  local v = string.match(text, "[?&#]t=([0-9hms]+)") or string.match(text, "[?&#]start=([0-9]+)")
  if not v then return nil end
  if string.match(v, "^[0-9]+$") then return tonumber(v) end
  local h = string.match(v, "([0-9]+)h")
  local m = string.match(v, "([0-9]+)m")
  local s = string.match(v, "([0-9]+)s")
  if not (h or m or s) then return nil end
  return (tonumber(h or "0") * 3600) + (tonumber(m or "0") * 60) + tonumber(s or "0")
end

-- DESIGN §5.5 `file`: bare name, letters / digits / space / ._-(), .mp3 or .ogg, <= 100 bytes.
function Dt.CheckFileName(name)
  if #name > 100 then return nil, L["That file name is too long (100 bytes at most)."] end
  if string.find(name, "[/\\:]") or string.find(name, "..", 1, true) then
    return nil, L["Use just the file name, without folders."]
  end
  if not string.find(name, "^[A-Za-z0-9 ._%(%)%-\128-\255]+$") then
    return nil, L["Use letters, digits, spaces and . _ - ( ) in the file name."]
  end
  local l = string.lower(name)
  if not (string.find(l, "%.mp3$") or string.find(l, "%.ogg$")) then
    return nil, L["In-game files must be .mp3 or .ogg."]
  end
  return true
end

function Dt.FileKnown(name)
  if not (C_UIFileAsset and C_UIFileAsset.IsKnownFile) then return nil end
  local ok, known = pcall(C_UIFileAsset.IsKnownFile, MEDIA .. name)
  if not ok or (ns.IsSecret and ns.IsSecret(known)) then return nil end
  return known and true or false
end

local function spotifyLink(id) return "https://open.spotify.com/track/" .. id end
local function youtubeLink(id) return "https://youtu.be/" .. id end

-- ------------------------------------------------------------------ rows --

local FILTERS = {
  { key = "here", text = "Here" },
  { key = "unheard", text = "Not heard yet (this character)" },
  { key = "needsSong", text = "Needs a song" },
  { key = "mine", text = "Changed by you" },
  { key = "files", text = "In-game files" },
  { key = "off", text = "Turned off" },
}
Dt.FILTERS = FILTERS

local function hereIds()
  local ids = {}
  local Loc = ns.Location
  if Loc and Loc.Candidates then
    for _, d in ipairs(Loc.Candidates()) do ids[d.id] = true end
  end
  local place = Dt.CurrentPlace()
  if place then
    local id = S().DropIdFor(place)
    if id then ids[id] = true end
    if place.mapID then ids["zone:" .. place.mapID] = true end
  end
  return ids
end

local function heard(drop)
  local D = ns.Director
  return D and D.IsDone and D.IsDone(drop) or false
end

local function lowerOr(s) return string.lower(s or "") end

local function makeRow(ref, drop, winning)
  local zone, lm = W.PlaceNames(drop)
  return {
    key = ref .. "\n" .. drop.id, ref = ref, id = drop.id, drop = drop,
    builtin = W.IsBuiltinRef(ref), draft = W.IsDraft(drop), disabled = drop.disabled == true,
    overriddenBy = (winning and winning ~= ref) and winning or nil,
    sortZone = lowerOr(zone), sortLandmark = lowerOr(lm),
  }
end

local function matches(row, needle, filters, here)
  local d = row.drop
  if needle ~= "" then
    local zone, lm = W.PlaceNames(d)
    local hay = lowerOr(zone) .. "\n" .. lowerOr(lm) .. "\n" .. lowerOr(d.label) .. "\n" .. lowerOr(d.artist)
      .. "\n" .. lowerOr(d.title) .. "\n" .. lowerOr(d.note)
    if not string.find(hay, needle, 1, true) then return false end
  end
  if filters.here and not here[d.id] then return false end
  if filters.unheard and ns.cdb and ns.cdb.done and ns.cdb.done[d.id] then return false end
  if filters.needsSong and not row.draft then return false end
  if filters.mine and row.builtin then return false end
  if filters.files and not nonEmpty(d.file) then return false end
  if filters.off and not row.disabled then return false end
  return true
end

local function rowLess(a, b)
  if a.sortZone ~= b.sortZone then return a.sortZone < b.sortZone end
  if a.sortLandmark ~= b.sortLandmark then return a.sortLandmark < b.sortLandmark end
  if a.id ~= b.id then return a.id < b.id end
  return a.ref < b.ref
end

function Dt.BuildRows()
  local model = S()
  local merged, list = W.MergedIndex()
  state.merged = merged
  local rows = {}
  local view = showing()
  if view == "mix" then
    -- What plays (or is turned off) at each place, in priority order ...
    local seen = {}
    for _, e in ipairs(list) do
      local d = e.drop
      if type(d) == "table" and d.id and merged[d.id] == e then
        seen[e.setRef .. "\n" .. d.id] = true
        rows[#rows + 1] = makeRow(e.setRef, d, nil)
      end
    end
    -- ... plus the player's unfinished drafts: they claim nothing, but belong in the list.
    for _, ref in ipairs(model.ActiveRefs()) do
      if not W.IsBuiltinRef(ref) then
        local set = model.Get(ref)
        for _, d in ipairs(set and set.drops or {}) do
          if type(d) == "table" and type(d.id) == "string" and W.IsDraft(d) and not seen[ref .. "\n" .. d.id] then
            seen[ref .. "\n" .. d.id] = true
            rows[#rows + 1] = makeRow(ref, d, nil)
          end
        end
      end
    end
  else
    local set = model.Get(view)
    local active = isActive(view)
    for _, d in ipairs(set and set.drops or {}) do
      if type(d) == "table" and type(d.id) == "string" then   -- damaged saved data: never a row
        local e = merged[d.id]
        local winning = (active and e and not W.IsDraft(d)) and e.setRef or nil
        rows[#rows + 1] = makeRow(view, d, winning)
      end
    end
  end
  local needle = string.lower(trim(state.search))
  local here = state.filters.here and hereIds() or {}
  local out = {}
  for _, r in ipairs(rows) do
    if matches(r, needle, state.filters, here) then out[#out + 1] = r end
  end
  table.sort(out, rowLess)
  state.rows = out
  return out
end

local function isSelectedRow(data)
  local sel = state.selected
  return sel ~= nil and sel.ref == data.ref and sel.id == data.id
end

function Dt.RowTags(data)
  local parts = {}
  local src = W.Sources(data.drop)
  if src ~= "" then parts[#parts + 1] = src end
  if not data.draft and not data.disabled and heard(data.drop) then parts[#parts + 1] = L["heard"] .. " " .. W.CHECK end
  if data.disabled then
    parts[#parts + 1] = L["off"]
  elseif data.overriddenBy then
    parts[#parts + 1] = L["overridden"]
  elseif not data.builtin then
    parts[#parts + 1] = L["yours"]
  end
  return table.concat(parts, "  ")
end

local function rowTooltip(row)
  local data = row.ndData
  if not data then return end
  local d = data.drop
  GameTooltip:SetOwner(row, "ANCHOR_RIGHT")
  GameTooltip_SetTitle(GameTooltip, W.PlaceLine(d))
  local song = W.SongText(d)
  if data.disabled then
    GameTooltip_AddNormalLine(GameTooltip, L["Turned off here: nothing plays."], true)
  elseif song then
    GameTooltip_AddHighlightLine(GameTooltip, song, true)
  else
    GameTooltip_AddNormalLine(GameTooltip, L["Needs a song: type the artist and title."], true)
  end
  local sources = W.SourceNames(d)
  if sources ~= "" then
    GameTooltip_AddNormalLine(GameTooltip, sources, true)
  elseif song then
    GameTooltip_AddNormalLine(GameTooltip, L["No link: the NeedleDropper app searches Spotify for the artist and title."], true)
  end
  if nonEmpty(d.note) then GameTooltip_AddNormalLine(GameTooltip, esc(d.note), true) end
  local from = W.Format(L["from %s"], esc(W.SetName(data.ref)))
  if data.builtin then from = from .. " " .. L["(built-in)"] end
  GameTooltip_AddDisabledLine(GameTooltip, from, true)
  if data.overriddenBy then
    GameTooltip_AddNormalLine(GameTooltip, W.Format(L["%s plays here instead."], esc(W.SetName(data.overriddenBy))), true)
  end
  GameTooltip_AddInstructionLine(GameTooltip, L["Right-click for options"], true)
  GameTooltip:Show()
end

local function updateRowHover(row)
  if not row.ndData then return end
  row.Test:SetShown(isSelectedRow(row.ndData) or row:IsMouseOver())
end

local function initRow(row, data)
  if not row.ndBuilt then
    row.ndBuilt = true
    row:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    row.Selected = row:CreateTexture(nil, "BACKGROUND")
    row.Selected:SetAllPoints(row)
    row.Selected:SetColorTexture(1, 0.82, 0, 0.18)
    row.Hover = row:CreateTexture(nil, "HIGHLIGHT")
    row.Hover:SetAllPoints(row)
    row.Hover:SetColorTexture(1, 1, 1, 0.08)
    row.Line1 = W.Label(row, nil, "GameFontNormal")
    row.Line1:SetPoint("TOPLEFT", row, "TOPLEFT", 6, -4)
    row.Line1:SetPoint("RIGHT", row, "RIGHT", -96, 0)
    row.Line1:SetWordWrap(false)
    row.Line2 = W.Label(row, nil, "GameFontHighlightSmall")
    row.Line2:SetPoint("TOPLEFT", row.Line1, "BOTTOMLEFT", 0, -3)
    row.Line2:SetPoint("RIGHT", row, "RIGHT", -60, 0)
    row.Line2:SetWordWrap(false)
    row.Tags = W.Label(row, nil, "GameFontHighlightSmall")
    row.Tags:SetPoint("TOPRIGHT", row, "TOPRIGHT", -6, -5)
    row.Tags:SetJustifyH("RIGHT")
    row.Test = W.Button(row, L["Test"], 50, function(self) Dt.Test(self:GetParent().ndData) end,
      L["Test"], L["Plays this song now. It is not marked as heard."])
    row.Test:SetHeight(18)
    row.Test:SetPoint("BOTTOMRIGHT", row, "BOTTOMRIGHT", -4, 2)
    row.Test:HookScript("OnLeave", function(self) updateRowHover(self:GetParent()) end)
    row:SetScript("OnClick", function(self, button)
      if button == "RightButton" then
        Dt.OpenContextMenu(self, self.ndData)
      else
        Dt.Select(self.ndData.ref, self.ndData.id)
      end
    end)
    row:SetScript("OnEnter", function(self)
      updateRowHover(self)
      rowTooltip(self)
    end)
    row:SetScript("OnLeave", function(self)
      GameTooltip:Hide()
      updateRowHover(self)
    end)
  end
  -- Rows are recycled for other drops: reset everything every time.
  row.ndData = data
  local d = data.drop
  row.Line1:SetText(W.PlaceLine(d))
  if data.disabled then
    row.Line2:SetFontObject("GameFontDisableSmall")
    row.Line2:SetText(L["turned off here"])
  elseif data.draft then
    row.Line2:SetFontObject("GameFontDisableSmall")
    row.Line2:SetText(L["needs a song"])
  else
    row.Line2:SetFontObject("GameFontHighlightSmall")
    row.Line2:SetText(W.SongText(d))
  end
  row.Tags:SetText(Dt.RowTags(data))
  row.Selected:SetShown(isSelectedRow(data))
  updateRowHover(row)
end

function Dt.RefreshList()
  if not ui.list then return end
  local rows = Dt.BuildRows()
  ui.list:SetDataProvider(CreateDataProvider(rows), ScrollBoxConstants.RetainScrollPosition)
  if #rows == 0 then
    local view = showing()
    if trim(state.search) ~= "" or next(state.filters) then
      ui.listEmpty:SetText(L["No drops match the search and filter."])
    elseif view == "mix" then
      ui.listEmpty:SetText(L["Nothing plays yet: turn a soundtrack on in the Soundtracks tab."])
    else
      ui.listEmpty:SetText(L["This soundtrack has no drops yet. Go somewhere and press Add drop here."])
    end
    ui.listEmpty:Show()
  else
    ui.listEmpty:Hide()
  end
  if state.selected then
    for _, r in ipairs(rows) do
      if isSelectedRow(r) then ui.list:ScrollToElementData(r, ScrollBoxConstants.AlignNearest) end
    end
  end
end

-- ------------------------------------------------------------- selection --

function Dt.ClearFormFocus()
  local f = form.focused
  if f and f.HasFocus and f:HasFocus() then f:ClearFocus() end
  if Dt.FlushVolume then Dt.FlushVolume() end       -- a volume drag still waiting to be saved
end

function Dt.Select(ref, id, opts)
  Dt.ClearFormFocus()
  if ref and id then
    state.selected = { ref = ref, id = id }
  else
    state.selected = nil
  end
  state.error = nil
  state.pendingLandmark = false
  if opts and opts.inView and ref and showing() ~= "mix" and showing() ~= ref then setShowing("mix") end
  Dt.Refresh()
end

-- "On open the drop for the player's current place is selected" (Location.Candidates()[1]),
-- or a draft for this place in My Drops; else the empty pane names the place.
function Dt.SelectCurrentPlace()
  local model = S()
  local cand
  local Loc = ns.Location
  if Loc and Loc.Candidates then cand = Loc.Candidates()[1] end
  local place = Dt.CurrentPlace()
  state.emptyPlace = place and (place.label and esc(place.label) or W.PlaceShort(place)) or nil
  if cand and cand.id then
    local ref = cand.setRef or (cand.setId and W.RefForSetId(cand.setId))
    if ref and model.GetDrop(ref, cand.id) then
      state.selected = { ref = ref, id = cand.id }
      return true
    end
  end
  if place then
    local id = model.DropIdFor(place)
    local mine = id and Dt.FindPlaceIn("user:my-drops", place, id)
    if mine then
      state.selected = { ref = "user:my-drops", id = mine }
      return true
    end
  end
  state.selected = nil
  return false
end

-- Selects drop `id` wherever it plays (Journal "Show drop").
function Dt.ShowDrop(id)
  setShowing("mix")
  local e = W.MergedIndex()[id]
  if not e then return false end
  Dt.Select(e.setRef, id)
  return true
end

-- ------------------------------------------------------------- actions --

local function notice(text)
  state.error = text
  Dt.RefreshForm()
end

-- An edit to the drop of the place where the player stands must not make that drop fire in the
-- middle of the edit (with a half-typed song, then marked heard), nor a drop the edit reveals
-- (turning off or deleting a landmark's song hands the place to the zone's). Like Add drop here
-- (DESIGN-v1.2 4.2), such an edit lets the place go for this visit: it plays on the next arrival,
-- and Test plays it now. `ids`: the drop ids the edit touches; `all`: also hold every other drop
-- of this place (edits that can reveal another drop).
local function holdHere(ids, all)
  local D = ns.Director
  if not (D and D.SuppressHere) then return end
  local here = hereIds()
  local touched = false
  for _, id in pairs(ids) do
    if here[id] then
      touched = true
      D.SuppressHere(id)
    end
  end
  if touched and all then
    for id in pairs(here) do D.SuppressHere(id) end
  end
end
Dt.HoldHere = holdHere

function Dt.Update(fields)
  local sel = state.selected
  if not sel then return false end
  local reveals = type(fields) == "table" and fields.disabled == true
  holdHere({ sel.id }, reveals)
  local ok, res = S().UpdateDrop(sel.ref, sel.id, fields)
  if not ok then
    notice(res or L["That change could not be saved."])
    return false
  end
  if type(res) == "string" and res ~= "" then
    if res ~= sel.id then holdHere({ res }, false) end    -- moved here ("Use my location")
    sel.id = res
  end
  state.error = nil
  W.RefreshAll()
  return true
end

-- Volume. MinimalSliderTemplate obeys its 5 % step while dragging (obeyStepOnDrag,
-- MinimalSlider.xml L6 on forever), so one drag from 100 % to 0 % reports 20 values. The % label
-- follows each one; the drop is saved once, when the player lets go: at OnInteractEnd, or
-- VOLUME_SETTLE seconds after the last change while the thumb is not held (stepper clicks, the
-- gamepad, a release over the slider). One drag = one edit and one undo step.
local VOLUME_SETTLE = 0.4
local volumeTimer

local function flushVolume()
  if volumeTimer then
    volumeTimer:Cancel()
    volumeTimer = nil
  end
  local p = state.pendingVolume
  state.pendingVolume = nil
  if not p or W.IsBuiltinRef(p.ref) then return end
  local drop = S().GetDrop(p.ref, p.id)
  if not drop or p.value == (type(drop.volume) == "number" and drop.volume or 100) then return end
  holdHere({ p.id }, false)
  local ok, res = S().UpdateDrop(p.ref, p.id, { volume = p.value })
  if not ok then
    notice(res or L["That change could not be saved."])
    return
  end
  W.RefreshAll()
end
Dt.FlushVolume = flushVolume

local function thumbHeld()
  local s = form.Volume and form.Volume.Slider
  if not (s and type(s.IsDraggingThumb) == "function") then return false end
  return s:IsDraggingThumb() == true      -- Slider API (SimpleSliderAPIDocumentation.lua L118)
end

local function scheduleVolume()
  if volumeTimer then volumeTimer:Cancel() end
  volumeTimer = C_Timer.NewTimer(VOLUME_SETTLE, function()
    volumeTimer = nil
    if thumbHeld() then
      scheduleVolume()
    else
      flushVolume()
    end
  end)
end

function Dt.VolumeChanged(value)
  local sel = state.selected
  if not (sel and Dt.SelectedDrop() and editable()) then return end
  local p = state.pendingVolume
  if p and (p.ref ~= sel.ref or p.id ~= sel.id) then flushVolume() end
  state.pendingVolume = { ref = sel.ref, id = sel.id, value = math.floor(value + 0.5) }
  scheduleVolume()
end

-- The drop as the form shows it right now, typed but uncommitted text included (Test plays
-- unsaved drafts too).
function Dt.FormDrop()
  local d = Dt.SelectedDrop()
  if not d then return nil end
  local out = {}
  for k, v in pairs(d) do out[k] = v end
  if editable() and form.Artist then
    local a, t = trim(form.Artist:GetText()), trim(form.Title:GetText())
    out.artist = nonEmpty(a)
    out.title = nonEmpty(t)
    local P2 = ns.Protocol2
    local spText, ytText = trim(form.Spotify:GetText()), trim(form.YouTube:GetText())
    if spText == "" then
      out.spotify = nil
    else
      local sp = P2 and P2.SpotifyIdFromLink(spText)
      if sp then out.spotify = sp end
    end
    if ytText == "" then
      out.youtube = nil
    else
      local yt = P2 and P2.YouTubeIdFromLink(ytText)
      if yt then out.youtube = yt end
    end
    local file = trim(form.File:GetText())
    if file == "" then
      out.file = nil
    elseif Dt.CheckFileName(file) then
      out.file = file
    end
  end
  return out
end

function Dt.Test(data)
  local D = ns.Director
  if not (D and D.PlayTest) then return end
  local drop
  if data and not isSelectedRow(data) then
    drop = data.drop
  else
    drop = Dt.FormDrop() or (data and data.drop)
  end
  if drop then
    D.PlayTest(W.Playable(drop))
    W.UpdateFooter()
  end
end

function Dt.Stop()
  local D = ns.Director
  if D and D.Stop then D.Stop() end
  W.UpdateFooter()
end

function Dt.ChangeSongHere(data)
  data = data or (state.selected and { ref = state.selected.ref, id = state.selected.id })
  if not data then return end
  holdHere({ data.id }, false)
  local ref, id = S().OverrideHere(data.id)
  if not ref then
    notice(id or L["That change could not be saved."])
    return
  end
  if showing() ~= "mix" and showing() ~= ref then setShowing("mix") end
  state.selected = { ref = ref, id = id }
  state.error = nil
  W.RefreshAll()
  -- The fields are editable now; typing replaces the artist straight away.
  if form.Artist and form.Artist:IsVisible() and W.FocusBox(form.Artist) then
    form.Artist:HighlightText()
  end
end

function Dt.TurnOffHere(data)
  data = data or state.selected
  if not data then return end
  local model = S()
  local drop = model.GetDrop(data.ref, data.id)
  if not drop then return end
  if W.IsBuiltinRef(data.ref) or drop.basedOn then
    holdHere({ data.id }, true)
    local ref, id = model.TurnOffHere(data.id)
    if not ref then
      notice(id or L["That change could not be saved."])
      return
    end
    state.selected = { ref = ref, id = id or data.id }
    if showing() ~= "mix" and showing() ~= ref then setShowing("mix") end
    W.RefreshAll()
  else
    state.selected = { ref = data.ref, id = data.id }
    Dt.Update({ disabled = true })
  end
end

function Dt.TurnOnHere(data)
  data = data or state.selected
  if not data then return end
  local drop = S().GetDrop(data.ref, data.id)
  if not drop then return end
  state.selected = { ref = data.ref, id = data.id }
  if drop.basedOn and not W.SongText(drop) then
    Dt.Revert(data)
  else
    Dt.Update({ disabled = false })
  end
end

function Dt.Revert(data)
  data = data or state.selected
  if not data then return end
  local model = S()
  local drop = model.GetDrop(data.ref, data.id)
  local base = drop and drop.basedOn
  holdHere({ data.id }, true)
  local ok, err = model.Revert(data.id)
  if not ok then
    notice(err or L["That change could not be saved."])
    return
  end
  local baseRef = base and W.RefForSetId(base)
  if baseRef and model.GetDrop(baseRef, data.id) then
    state.selected = { ref = baseRef, id = data.id }
  else
    state.selected = nil
  end
  W.RefreshAll()
end

local function hideUndo()
  if undoTimer then
    undoTimer:Cancel()
    undoTimer = nil
  end
  if ui.undo then ui.undo:Hide() end
end

function Dt.ShowUndo(text)
  hideUndo()
  ui.undo.Text:SetText(text)
  ui.undo:Show()
  undoTimer = C_Timer.NewTimer(UNDO_SECONDS, function()
    undoTimer = nil
    if ui.undo then ui.undo:Hide() end
  end)
end

-- The bar's Undo brings back exactly the drop it names, even after other edits (the model's
-- UndoRemove); the shared session stack (Undo()) is only the fallback for a model without it.
function Dt.Undo()
  local model = S()
  local d = state.lastDeleted
  local ok, err
  if d and d.token ~= nil and model.UndoRemove then
    if d.id then holdHere({ d.id }, false) end
    local why
    ok, err, why = model.UndoRemove(d.token)
    if not ok and why == "gone" and d.drop then
      ok, err = model.AddDrop(d.ref, d.drop)        -- the step fell out of the undo stack
    end
  elseif d then
    ok = model.Undo()
  end
  hideUndo()
  state.lastDeleted = nil
  if ok and d and model.GetDrop(d.ref, d.id) then
    state.selected = { ref = d.ref, id = d.id }
  elseif not ok and err then
    state.error = err
  end
  W.RefreshAll()
end

-- Deletes at once; the bar offers Undo for 15 s (no confirmation dialog).
function Dt.Delete(data)
  data = data or state.selected
  if not data or W.IsBuiltinRef(data.ref) then return end
  local drop = S().GetDrop(data.ref, data.id)
  if not drop then return end
  Dt.ClearFormFocus()
  local place = W.PlaceShort(drop)
  local kept = {}
  for k, v in pairs(drop) do kept[k] = v end
  holdHere({ data.id }, true)
  local ok, err = S().RemoveDrop(data.ref, data.id)
  if not ok then
    notice(err or L["That drop could not be deleted."])
    return
  end
  local token = S().LastUndoToken and S().LastUndoToken() or nil
  state.lastDeleted = { ref = data.ref, id = data.id, token = token, drop = kept }
  if isSelectedRow(data) then state.selected = nil end
  Dt.ShowUndo(W.Format(L["Deleted %s."], place))
  W.RefreshAll()
end

local function copyDrop(drop)
  local out = {}
  for k, v in pairs(drop) do
    if k ~= "basedOn" and k ~= "modified" and k ~= "setRef" and k ~= "setId" and k ~= "builtin" and k ~= "n" then
      out[k] = v
    end
  end
  return out
end

function Dt.CopyTo(data, targetRef, move)
  local model = S()
  local drop = model.GetDrop(data.ref, data.id)
  if not drop then return end
  holdHere({ data.id }, move)
  local id, err = model.AddDrop(targetRef, copyDrop(drop))
  if not id then
    state.selected = { ref = data.ref, id = data.id }
    notice(err or L["That drop could not be copied."])
    return
  end
  if move then model.RemoveDrop(data.ref, data.id) end
  if showing() ~= "mix" and showing() ~= targetRef then setShowing(targetRef) end
  state.selected = { ref = targetRef, id = id }
  W.RefreshAll()
end

-- Soundtracks of your own a drop can be copied or moved to (every one but its own).
local function copyTargets(data)
  local out = {}
  for _, info in ipairs(S().List()) do
    if not info.builtin and info.ref ~= data.ref then out[#out + 1] = info end
  end
  return out
end

-- "Copy to" / "Move to" entries for `data` (a row, or the selected drop): the context menu has
-- them as submenus; the form's More... section has the same menu as a dropdown, because a
-- right-click is out of reach with Forever's gamepad UI (SmartNavigation binds only Click and
-- Close, Blizzard_GamepadSmartNavigation/SmartNavigation.lua L233-L234 on forever).
function Dt.AddCopyMoveItems(root, data, flat)
  local targets = copyTargets(data)
  if #targets == 0 then return false end
  if flat then
    -- One level, every entry saying what it does: "Copy to Road Trip", "Move to Road Trip".
    for _, info in ipairs(targets) do
      root:CreateButton(W.Format(L["Copy to %s"], esc(info.name)), function() Dt.CopyTo(data, info.ref, false) end)
    end
    if not data.builtin then
      root:CreateDivider()
      for _, info in ipairs(targets) do
        root:CreateButton(W.Format(L["Move to %s"], esc(info.name)), function() Dt.CopyTo(data, info.ref, true) end)
      end
    end
    return true
  end
  local copyTo = root:CreateButton(L["Copy to"])
  for _, info in ipairs(targets) do
    copyTo:CreateButton(esc(info.name), function() Dt.CopyTo(data, info.ref, false) end)
  end
  if not data.builtin then
    local moveTo = root:CreateButton(L["Move to"])
    for _, info in ipairs(targets) do
      moveTo:CreateButton(esc(info.name), function() Dt.CopyTo(data, info.ref, true) end)
    end
  end
  return true
end

-- The selected drop as a row (for the form's Copy / Move dropdown).
function Dt.SelectedRow()
  local sel = state.selected
  local d = Dt.SelectedDrop()
  if not (sel and d) then return nil end
  return { ref = sel.ref, id = sel.id, drop = d, builtin = W.IsBuiltinRef(sel.ref), disabled = d.disabled == true }
end

function Dt.OpenContextMenu(row, data)
  if not data then return end
  local model = S()
  MenuUtil.CreateContextMenu(row, function(owner, root)
    root:CreateTitle(W.PlaceShort(data.drop))
    root:CreateButton(L["Test"], function() Dt.Test(data) end)
    if data.builtin then
      root:CreateButton(L["Change song here"], function() Dt.ChangeSongHere(data) end)
    else
      root:CreateButton(L["Edit"], function()
        Dt.Select(data.ref, data.id)
        if form.Artist and form.Artist:IsVisible() then form.Artist:SetFocus() end
      end)
    end
    if data.disabled then
      root:CreateButton(L["Turn on here"], function() Dt.TurnOnHere(data) end)
    else
      root:CreateButton(L["Turn off here"], function() Dt.TurnOffHere(data) end)
    end
    Dt.AddCopyMoveItems(root, data, false)
    if not data.builtin and data.drop.basedOn then
      local baseRef = W.RefForSetId(data.drop.basedOn)
      root:CreateButton(W.Format(L["Revert to %s"], esc(baseRef and W.SetName(baseRef) or data.drop.basedOn)),
        function() Dt.Revert(data) end)
    end
    if not data.builtin then
      root:CreateDivider()
      root:CreateButton(DELETE or L["Delete"], function() Dt.Delete(data) end)
    end
  end)
end

-- "Add drop here" (button, key binding, /needle add [Artist - Title | link]).
local function parseAddText(text)
  text = trim(text)
  if text == "" then return nil end
  -- The model's parser knows every separator (" - ", en and em dash) and says what is wrong
  -- with a link ("That's an album or playlist link: ...").
  local model = S()
  if model and model.ParseSongText then
    local fields, err = model.ParseSongText(text)
    if fields and next(fields) ~= nil then return fields end
    if not fields then
      return nil, err or L["Use /needle add Artist - Title, or paste a Spotify or YouTube song link."]
    end
  end
  local P2 = ns.Protocol2
  local sp = P2 and P2.SpotifyIdFromLink(text)
  if sp then return { spotify = sp } end
  local yt = P2 and P2.YouTubeIdFromLink(text)
  if yt then
    local fields = { youtube = yt }
    local start = Dt.YouTubeStart(text)
    if start and start > 0 then fields.startMs = start * 1000 end
    return fields
  end
  local artist, title = string.match(text, "^(.-)[ \t]+%-[ \t]+(.+)$")
  artist, title = trim(artist), trim(title)
  if artist ~= "" and title ~= "" then return { artist = artist, title = title } end
  return nil, L["Use /needle add Artist - Title, or paste a Spotify or YouTube song link."]
end

-- True when drop `d` is the landmark `place` names (by areaID, or by name in the same zone).
local function sameLandmark(d, place)
  if type(d) ~= "table" or W.KindOf(d) ~= "subzone" or place.kind ~= "subzone" then return false end
  if d.areaID and place.areaID then return d.areaID == place.areaID end
  local name = string.lower(place.subzone or "")
  if name == "" then return false end
  if d.subzone and string.lower(d.subzone) == name and (not d.mapID or not place.mapID or d.mapID == place.mapID) then
    return true
  end
  local an = d.areaID and W.AreaName(d.areaID)
  return an ~= nil and string.lower(an) == name
end

-- The id of the drop set `ref` has for `place` (its own id, or the same landmark by name).
function Dt.FindPlaceIn(ref, place, id)
  local model = S()
  if not model.Exists(ref) then return nil end
  if model.GetDrop(ref, id) then return id end
  local set = model.Get(ref)
  for _, d in ipairs(set and set.drops or {}) do
    if sameLandmark(d, place) then return d.id end
  end
  return nil
end

-- The Merged() entry that claims `place`, if any (its own id, else the same landmark by name).
local function coveringEntry(place, id)
  local byId, list = W.MergedIndex()
  if byId[id] then return byId[id] end
  for _, e in ipairs(list) do
    if e.drop and byId[e.drop.id] == e and sameLandmark(e.drop, place) then return e end
  end
  return nil
end

function W.AddHere(text)
  local model = S()
  if not (ns.db and model) then
    if ns.Print then ns.Print(L["NeedleDropper is still loading - try again in a moment."]) end
    return false
  end
  local fields, parseErr
  if text ~= nil and trim(text) ~= "" then
    fields, parseErr = parseAddText(text)
    if not fields then
      if ns.Print then ns.Print(parseErr) end
      return false
    end
  end
  local place, why = Dt.CurrentPlace()
  if not place then
    if ns.Print then ns.Print(why) end
    return false
  end
  local id = model.DropIdFor(place)
  if not id then
    if ns.Print then ns.Print(L["NeedleDropper cannot tell where you are right now."]) end
    return false
  end
  if not fields then UI.Open("drops") end

  local view = showing()
  local targetRef = (view ~= "mix" and not W.IsBuiltinRef(view)) and view or nil
  local existingRef, existingId
  if targetRef then
    existingId = Dt.FindPlaceIn(targetRef, place, id)
    if existingId then existingRef = targetRef end
  else
    existingId = Dt.FindPlaceIn("user:my-drops", place, id)
    if existingId then existingRef = "user:my-drops" end
  end
  local cover = coveringEntry(place, id)

  local resultRef, resultId
  if not fields then
    -- The place is covered already: select that drop (HandyNotes' Add/Edit switch).
    if existingRef then
      resultRef, resultId = existingRef, existingId
    elseif cover then
      resultRef, resultId = cover.setRef, cover.drop.id
    end
  end

  if not resultRef then
    if existingRef then
      resultRef, resultId = existingRef, existingId
      local ok, newId = model.UpdateDrop(existingRef, existingId, fields)
      if not ok then
        if ns.Print then ns.Print(newId) end
        return false
      end
      if type(newId) == "string" then resultId = newId end
    elseif fields and not targetRef and cover and W.IsBuiltinRef(cover.setRef) then
      -- A song for a place a built-in covers: My Drops overrides it, under the same id.
      resultRef, resultId = model.OverrideHere(cover.drop.id, fields)
      if not resultRef then
        if ns.Print then ns.Print(resultId) end
        return false
      end
    else
      local ref = targetRef
      if not ref then
        local err
        ref, err = W.EnsureMyDrops()
        if not ref then
          if ns.Print then ns.Print(err) end
          return false
        end
      end
      local drop = { kind = place.kind, mapID = place.mapID, areaID = place.areaID, subzone = place.subzone,
                     instanceID = place.instanceID, label = place.label }
      for k, v in pairs(fields or {}) do drop[k] = v end
      local newId, err = model.AddDrop(ref, drop)
      if not newId then
        if ns.Print then ns.Print(err) end
        return false
      end
      resultRef, resultId = ref, newId
    end
  end

  -- Don't let a drop added where the player stands fire in the middle of editing.
  if ns.Director and ns.Director.SuppressHere then ns.Director.SuppressHere(resultId) end

  local drop = model.GetDrop(resultRef, resultId)
  if fields then
    local song = drop and W.SongText(drop)
    if ns.Print then
      if song then
        ns.Print(W.Format(L["Added %s: %s to %s."], W.PlaceShort(drop), song, esc(W.SetName(resultRef))))
      else
        ns.Print(W.Format(L["Added %s to %s. Type the artist and title to finish it."],
          W.PlaceShort(drop or place), esc(W.SetName(resultRef))))
      end
    end
    if drop and W.IsDraft(drop) then UI.Open("drops") end
  end
  if UI.IsShown() then
    if showing() ~= "mix" and showing() ~= resultRef then setShowing(resultRef) end
    state.selected = { ref = resultRef, id = resultId }
    state.error = nil
    W.RefreshAll()
    if drop and W.IsDraft(drop) and not W.IsBuiltinRef(resultRef) and form.Artist and form.Artist:IsVisible() then
      W.FocusBox(form.Artist)
    end
  end
  return true, resultRef, resultId
end

-- ------------------------------------------------------------------- form --

local FIELD = {}

-- A Spotify or YouTube link pasted into Artist or Title (Add drop here puts the cursor in
-- Artist) goes into its own field instead of being saved as the name. Only text that names the
-- service counts: a bare 11-character word like "Evanescence" is an artist, not a video id.
local function linkFields(text)
  local P2 = ns.Protocol2
  local l = string.lower(text)
  if not P2 or not (string.find(l, "spotify", 1, true) or string.find(l, "youtu", 1, true)) then return nil end
  local sp = P2.SpotifyIdFromLink(text)
  if sp then return { spotify = sp } end
  local yt = P2.YouTubeIdFromLink(text)
  if yt then
    local fields = { youtube = yt }
    local start = Dt.YouTubeStart(text)
    if start and start > 0 then fields.startMs = start * 1000 end
    return fields
  end
  return nil
end

FIELD.artist = function(text)
  local link = linkFields(trim(text))
  if link then return link, nil, true end
  return { artist = trim(text) }
end
FIELD.title = function(text)
  local link = linkFields(trim(text))
  if link then return link, nil, true end
  return { title = trim(text) }
end

FIELD.spotify = function(text)
  text = trim(text)
  if text == "" then return { spotify = "" } end
  local id = ns.Protocol2 and ns.Protocol2.SpotifyIdFromLink(text)
  if id then return { spotify = id } end
  return nil, spotifyProblem(text)
end

FIELD.youtube = function(text)
  text = trim(text)
  if text == "" then return { youtube = "" } end
  local id = ns.Protocol2 and ns.Protocol2.YouTubeIdFromLink(text)
  if id then return { youtube = id } end
  return nil, youtubeProblem(text)
end

FIELD.file = function(text)
  text = trim(text)
  if text == "" then return { file = "" } end
  local ok, why = Dt.CheckFileName(text)
  if not ok then return nil, why end
  return { file = text }
end

FIELD.start = function(text)
  if trim(text) == "" then return { startMs = 0 } end
  local ms = W.ParseTime(text)
  if not ms then return nil, L["Use minutes:seconds, like 1:23."] end
  return { startMs = ms }
end

FIELD.length = function(text)
  if trim(text) == "" then return { durationMs = 0 } end
  local ms = W.ParseTime(text)
  if not ms or ms <= 0 then return nil, L["Use minutes:seconds, like 3:30, or leave it empty for the whole song."] end
  return { durationMs = ms }
end

local function currentTags()
  local tags, seen = {}, {}
  local function add(t)
    t = trim(t)
    local l = string.lower(t)
    if t ~= "" and #t <= 24 and not seen[l] and #tags < MAX_TAGS then
      seen[l] = true
      tags[#tags + 1] = t
    end
  end
  if form.Fitting:GetChecked() then add("fitting") end
  if form.Funny:GetChecked() then add("funny") end
  for part in string.gmatch(form.Tags:GetText() or "", "[^,]+") do add(part) end
  return tags
end

FIELD.tags = function() return { tags = currentTags() } end

FIELD.landmark = function(text, drop)
  text = trim(text)
  if text == "" then
    return { kind = "zone", areaID = false, subzone = false, label = W.ZoneName(drop.mapID) or false }
  end
  local areaID = false
  if drop.subzone and string.lower(drop.subzone) == string.lower(text) and drop.areaID then
    areaID = drop.areaID
  else
    local here = Dt.CurrentPlace()
    if here and here.mapID == drop.mapID and here.subzone and string.lower(here.subzone) == string.lower(text) then
      areaID = here.areaID or false
    end
  end
  return { kind = "subzone", subzone = text, areaID = areaID, label = text }
end

local function sameValue(a, b)
  if a == "" or a == false then a = nil end
  if b == "" or b == false then b = nil end
  if type(a) == "table" and type(b) == "table" then
    if #a ~= #b then return false end
    for i = 1, #a do if a[i] ~= b[i] then return false end end
    return true
  end
  if type(a) == "table" and #a == 0 then a = nil end
  if type(b) == "table" and #b == 0 then b = nil end
  if a == 0 and b == nil then return true end
  if b == 0 and a == nil then return true end
  return a == b
end

local function sameFields(drop, fields)
  for k, v in pairs(fields) do
    if not sameValue(drop[k], v) then return false end
  end
  return true
end

function Dt.CommitField(box)
  if form.refreshing or not box.ndKey then return end
  local text = box:GetText() or ""
  if text == (box.ndValue or "") then return end
  local drop = Dt.SelectedDrop()
  if not (drop and editable()) then
    box:SetText(box.ndValue or "")
    return
  end
  local fields, problem, moved = FIELD[box.ndKey](text, drop, box)
  if not fields then
    box.ndProblem = problem
    if box.ndKey == "spotify" or box.ndKey == "youtube" or box.ndKey == "file" then
      Dt.RefreshStatus()          -- shown under the field
    else
      notice(problem)
    end
    return
  end
  box.ndProblem = nil
  if moved then
    -- A link typed or pasted into Artist / Title: it goes to its own field, and the box gets its
    -- last saved text back, ready for the name.
    box:SetText(box.ndValue or "")
    if sameFields(drop, fields) then Dt.RefreshForm() else Dt.Update(fields) end
    return
  end
  if sameFields(drop, fields) then
    box.ndValue = text
    Dt.RefreshForm()
    return
  end
  Dt.Update(fields)
end

local function setupField(box, key)
  box.ndKey = key
  box:SetScript("OnEnterPressed", function(self)
    Dt.CommitField(self)
    self:ClearFocus()
  end)
  box:SetScript("OnEscapePressed", function(self)
    self.ndProblem = nil
    self:SetText(self.ndValue or "")
    self:ClearFocus()
    Dt.RefreshStatus()
  end)
  box:SetScript("OnEditFocusGained", function(self)
    form.focused = self
    self:HighlightText()
  end)
  box:SetScript("OnEditFocusLost", function(self)
    if form.focused == self then form.focused = nil end
    self:HighlightText(0, 0)
    Dt.CommitField(self)
  end)
  box:HookScript("OnTextChanged", function(self, userInput)
    if userInput then
      self.ndProblem = nil
      Dt.RefreshStatus()
    end
  end)
end

-- Status lines under the link and file fields, from the text in the box right now.
function Dt.RefreshStatus()
  if not form.Spotify then return end
  local P2 = ns.Protocol2
  local drop = Dt.SelectedDrop()

  local sp = trim(form.Spotify:GetText())
  if form.Spotify.ndProblem then
    form.SpotifyStatus:SetFontObject("GameFontRedSmall")
    form.SpotifyStatus:SetText(form.Spotify.ndProblem)
  elseif sp == "" then
    form.SpotifyStatus:SetText("")
  elseif P2 and P2.SpotifyIdFromLink(sp) then
    form.SpotifyStatus:SetFontObject("GameFontHighlightSmall")
    form.SpotifyStatus:SetText(L["Spotify song"] .. " " .. W.CHECK)
  else
    form.SpotifyStatus:SetFontObject("GameFontRedSmall")
    form.SpotifyStatus:SetText(spotifyProblem(sp))
  end

  local yt = trim(form.YouTube:GetText())
  local offer = nil
  if form.YouTube.ndProblem then
    form.YouTubeStatus:SetFontObject("GameFontRedSmall")
    form.YouTubeStatus:SetText(form.YouTube.ndProblem)
  elseif yt == "" then
    form.YouTubeStatus:SetText("")
  elseif P2 and P2.YouTubeIdFromLink(yt) then
    form.YouTubeStatus:SetFontObject("GameFontHighlightSmall")
    form.YouTubeStatus:SetText(L["YouTube video"] .. " " .. W.CHECK)
    local start = Dt.YouTubeStart(yt)
    if start and start > 0 and drop and (drop.startMs or 0) ~= start * 1000 and editable() then offer = start end
  else
    form.YouTubeStatus:SetFontObject("GameFontRedSmall")
    form.YouTubeStatus:SetText(youtubeProblem(yt))
  end
  form.youtubeStart = offer
  if offer then
    form.UseStart:SetText(W.Format(L["Start at %s?"], W.FormatTime(offer * 1000)))
    form.UseStart:Show()
  else
    form.UseStart:Hide()
  end

  local file = trim(form.File:GetText())
  if form.File.ndProblem then
    form.FileStatus:SetFontObject("GameFontRedSmall")
    form.FileStatus:SetText(form.File.ndProblem)
  elseif file == "" then
    form.FileStatus:SetText("")
  else
    local ok, why = Dt.CheckFileName(file)
    if not ok then
      form.FileStatus:SetFontObject("GameFontRedSmall")
      form.FileStatus:SetText(why)
    else
      local known = Dt.FileKnown(file)
      if known then
        form.FileStatus:SetFontObject("GameFontHighlightSmall")
        form.FileStatus:SetText(L["found"] .. " " .. W.CHECK)
      elseif known == false then
        form.FileStatus:SetFontObject("GameFontRedSmall")
        form.FileStatus:SetText(L["not found: copy it there and restart WoW (a /reload is not enough)"])
      else
        form.FileStatus:SetFontObject("GameFontHighlightSmall")
        form.FileStatus:SetText(L["This client cannot check for the file; Test plays it if it is there."])
      end
    end
  end

  -- "Spotify match: Africa · Toto (4:55) [Use this exact song]" (DESIGN §2.5).
  local match = drop and not nonEmpty(drop.spotify) and ns.Companion and ns.Companion.Track and ns.Companion.Track(drop)
  form.match = nil
  if type(match) == "table" and match.notFound then
    form.MatchText:SetText(L["The NeedleDropper app found no Spotify match for this artist and title."])
    form.UseMatch:Hide()
    form.matchLine.ndWanted = true
  elseif type(match) == "table" and nonEmpty(match.spotify) then
    local song = W.Format("%s%s%s", esc(match.title or drop.title or ""), W.DOT, esc(match.artist or drop.artist or ""))
    if type(match.durationMs) == "number" and match.durationMs > 0 then
      song = song .. " (" .. W.FormatTime(match.durationMs) .. ")"
    end
    form.MatchText:SetText(W.Format(L["Spotify match: %s"], song))
    form.match = match
    form.UseMatch:SetShown(editable())
    form.matchLine.ndWanted = true
  else
    form.matchLine.ndWanted = false
  end
  if type(match) == "table" and match.notEmbeddable then
    form.YouTubeStatus:SetFontObject("GameFontRedSmall")
    form.YouTubeStatus:SetText(L["This YouTube video can't be played outside YouTube: pick another upload."])
  end
end

local function placeText(drop)
  local kind = W.KindOf(drop)
  local zone, lm = W.PlaceNames(drop)
  if kind == "instance" then return W.Format(L["%s (dungeon or raid)"], esc(zone)) end
  if kind == "subzone" then
    if zone then return esc(lm) .. ", " .. esc(zone) end
    return esc(lm)
  end
  return W.Format(L["%s (whole zone)"], esc(zone))
end

-- Zones for the Zone dropdown: every zone of every continent under Azeroth, client-localised.
local zoneCache
local function zonesByContinent()
  if zoneCache then return zoneCache end
  zoneCache = {}
  if not (C_Map and C_Map.GetMapChildrenInfo) then return zoneCache end
  local types = Enum and Enum.UIMapType or {}
  local continents = C_Map.GetMapChildrenInfo(AZEROTH, types.Continent or 2) or {}
  table.sort(continents, function(a, b) return (a.name or "") < (b.name or "") end)
  for _, c in ipairs(continents) do
    local zones = C_Map.GetMapChildrenInfo(c.mapID, types.Zone or 3) or {}
    table.sort(zones, function(a, b) return (a.name or "") < (b.name or "") end)
    if #zones > 0 then zoneCache[#zoneCache + 1] = { name = c.name, zones = zones } end
  end
  -- Zones whose parent is Azeroth itself, not a continent: Zephras Isle (2521, the Skyborne
  -- starting zone in UiMap 1.60.1.70790) would otherwise be missing from the list.
  local loose = C_Map.GetMapChildrenInfo(AZEROTH, types.Zone or 3) or {}
  if #loose > 0 then
    table.sort(loose, function(a, b) return (a.name or "") < (b.name or "") end)
    zoneCache[#zoneCache + 1] = { name = W.ZoneName(AZEROTH) or L["Azeroth"], zones = loose }
  end
  return zoneCache
end

local function linkFocusOrder()
  local order = {}
  for _, box in ipairs(form.tabOrder) do
    if box:IsVisible() and box:IsEnabled() then order[#order + 1] = box end
  end
  for i, box in ipairs(order) do
    box.nextEditBox = order[i % #order + 1]
    box.previousEditBox = order[(i - 2) % #order + 1]
  end
  form.focusOrder = order
end

local function layoutForm()
  local y = 4
  for _, line in ipairs(form.lines) do
    if line.ndVisible then
      line:ClearAllPoints()
      line:SetPoint("TOPLEFT", form.content, "TOPLEFT", 0, -y)
      line:SetPoint("RIGHT", form.content, "RIGHT", 0, 0)
      line:Show()
      y = y + line.ndHeight + 4
    else
      line:Hide()
    end
  end
  form.content:SetHeight(y + 6)
  form.scroll:UpdateScrollChildRect()
  linkFocusOrder()
end

function Dt.RefreshForm()
  if not form.content then return end
  local model = S()
  local sel = state.selected
  local drop = sel and model.GetDrop(sel.ref, sel.id)
  if not drop then
    if sel then state.selected = nil end
    form.scroll:Hide()
    form.bar:Hide()
    local place = state.emptyPlace
    if place then
      ui.formEmpty:SetText(W.Format(L["No needle drop for %s yet. Press Add drop here, then type the artist and title (and paste a Spotify or YouTube link if you have one)."], place))
    else
      ui.formEmpty:SetText(L["Pick a drop on the left, or press Add drop here."])
    end
    ui.formEmpty:Show()
    Dt.RefreshButtons()
    return
  end
  ui.formEmpty:Hide()
  form.scroll:Show()
  form.bar:Show()
  form.refreshing = true
  local canEdit = not W.IsBuiltinRef(sel.ref)
  local kind = W.KindOf(drop)
  local active = isActive(sel.ref)
  local playing = state.merged[drop.id]

  -- Banner.
  local banner, buttons = nil, {}
  if not canEdit then
    banner = W.Format(L["From %s (built-in)"], esc(W.SetName(sel.ref)))
    if playing and playing.setRef ~= sel.ref then
      banner = banner .. "\n" .. W.Format(L["%s plays here instead."], esc(W.SetName(playing.setRef)))
      buttons.show = playing.setRef
    else
      buttons.change, buttons.off = true, true
    end
  elseif drop.basedOn then
    local baseRef = W.RefForSetId(drop.basedOn)
    local baseName = esc(baseRef and W.SetName(baseRef) or drop.basedOn)
    if drop.disabled then
      banner = W.Format(L["Turned off here: %s's song does not play."], baseName)
    else
      banner = W.Format(L["Your song here replaces %s's."], baseName)
    end
    buttons.revert = baseName
  elseif not active then
    banner = W.Format(L["%s is off, so this drop does not play."], esc(W.SetName(sel.ref)))
    buttons.turnOn = true
  elseif playing and playing.setRef ~= sel.ref and not W.IsDraft(drop) then
    banner = W.Format(L["%s is higher in the list and plays here instead."], esc(W.SetName(playing.setRef)))
    buttons.show = playing.setRef
  elseif W.IsDraft(drop) then
    -- A link alone is not a finished drop: drafts never play on arrival (DESIGN 4.2 rule 4).
    banner = L["Type the artist and title to finish this drop: until then it doesn't play when you arrive (Test still plays it)."]
  end
  form.bannerLine.ndVisible = banner ~= nil
  form.BannerText:SetText(banner or "")
  form.Change:SetShown(buttons.change == true)
  form.TurnOff:SetShown(buttons.off == true)
  form.Revert:SetShown(buttons.revert ~= nil)
  if buttons.revert then form.Revert:SetText(W.Format(L["Revert to %s"], buttons.revert)) end
  form.ShowPlaying:SetShown(buttons.show ~= nil)
  form.ShowPlaying.ndRef = buttons.show
  form.TurnSetOn:SetShown(buttons.turnOn == true)

  form.errorLine.ndVisible = state.error ~= nil
  form.ErrorText:SetText(state.error and esc(state.error) or "")

  -- Place.
  form.PlaceText:SetText(placeText(drop))
  W.SetEnabled(form.UseLocation, canEdit, L["Built-in drops are read-only."])
  local isInstance = kind == "instance"
  form.scopeLine.ndVisible = not isInstance
  form.zoneLine.ndVisible = not isInstance
  if kind == "subzone" then state.pendingLandmark = false end
  local landmarkMode = kind == "subzone" or state.pendingLandmark
  form.ThisLandmark:SetChecked(landmarkMode)
  form.WholeZone:SetChecked(not landmarkMode)
  W.SetEnabled(form.ThisLandmark, canEdit, L["Built-in drops are read-only."])
  W.SetEnabled(form.WholeZone, canEdit, L["Built-in drops are read-only."])
  W.SetEnabled(form.Zone, canEdit, L["Built-in drops are read-only."])
  form.Zone:GenerateMenu()
  form.LandmarkLabel:SetShown(landmarkMode)
  form.Landmark:SetShown(landmarkMode)
  W.SetBoxText(form.Landmark, kind == "subzone" and (drop.subzone or W.AreaName(drop.areaID) or "") or "")
  W.SetEnabled(form.Landmark, canEdit, L["Built-in drops are read-only."])
  local note
  if isInstance then
    note = L["Plays inside this dungeon or raid."]
  elseif kind == "subzone" and not drop.areaID then
    note = W.Format(L["Landmark saved by name: works on %s clients only."], W.ClientLanguage())
  end
  form.placeNoteLine.ndVisible = note ~= nil
  form.PlaceNote:SetText(note or "")

  -- Song.
  local songless = drop.disabled and not (nonEmpty(drop.artist) or nonEmpty(drop.title) or nonEmpty(drop.spotify)
    or nonEmpty(drop.youtube) or nonEmpty(drop.file))
  for _, key in ipairs({ "artistLine", "titleLine", "spotifyLine", "spotifyStatusLine", "youtubeLine",
                         "youtubeStatusLine", "fileLine", "fileStatusLine", "moreLine" }) do
    form[key].ndVisible = not songless
  end
  W.SetBoxText(form.Artist, drop.artist)
  W.SetBoxText(form.Title, drop.title)
  W.SetBoxText(form.Spotify, nonEmpty(drop.spotify) and spotifyLink(drop.spotify) or "")
  W.SetBoxText(form.YouTube, nonEmpty(drop.youtube) and youtubeLink(drop.youtube) or "")
  W.SetBoxText(form.File, drop.file)
  for _, box in ipairs({ form.Artist, form.Title, form.Spotify, form.YouTube, form.File, form.Start, form.Length, form.Tags }) do
    W.SetEnabled(box, canEdit, L["Built-in drops are read-only."])
    if not box:HasFocus() then box.ndProblem = nil end
  end
  Dt.RefreshStatus()
  form.matchLine.ndVisible = form.matchLine.ndWanted and not songless

  -- More...
  local more = state.more and not songless
  form.More:SetText(state.more and L["Less"] or L["More..."])
  for _, key in ipairs({ "timingLine", "volumeLine", "tagsLine", "noteLine", "offLine" }) do
    form[key].ndVisible = more
  end
  if songless and state.more then form.offLine.ndVisible = true end
  form.copyLine.ndVisible = state.more and #copyTargets({ ref = sel.ref }) > 0
  form.CopyMove:GenerateMenu()
  W.SetBoxText(form.Start, (drop.startMs or 0) > 0 and W.FormatTime(drop.startMs) or "")
  W.SetBoxText(form.Length, (drop.durationMs or 0) > 0 and W.FormatTime(drop.durationMs) or "")
  local pv = state.pendingVolume
  if pv and pv.ref == sel.ref and pv.id == sel.id then
    form.Volume:SetValue(pv.value)        -- a drag not saved yet: keep the thumb where it is
  else
    form.Volume:SetValue(type(drop.volume) == "number" and drop.volume or 100)
  end
  form.Volume:SetEnabled(canEdit)
  local tags, custom = drop.tags or {}, {}
  local fitting, funny = false, false
  for _, t in ipairs(tags) do
    local l = string.lower(t)
    if l == "fitting" then fitting = true elseif l == "funny" then funny = true else custom[#custom + 1] = t end
  end
  form.Fitting:SetChecked(fitting)
  form.Funny:SetChecked(funny)
  W.SetEnabled(form.Fitting, canEdit, L["Built-in drops are read-only."])
  W.SetEnabled(form.Funny, canEdit, L["Built-in drops are read-only."])
  W.SetBoxText(form.Tags, table.concat(custom, ", "))
  local noteBox = form.Note:GetEditBox()
  form.Note.ndValue = drop.note or ""
  if not noteBox:HasFocus() and form.Note:GetInputText() ~= (drop.note or "") then form.Note:SetText(drop.note or "") end
  form.Note:SetEnabled(canEdit)
  form.Off:SetChecked(drop.disabled == true)
  W.SetEnabled(form.Off, canEdit, L["Built-in drops are read-only: use Turn off here."])

  form.refreshing = false
  layoutForm()
  Dt.RefreshButtons()
end

function Dt.RefreshButtons()
  if not ui.testButton then return end
  local drop = Dt.SelectedDrop()
  local sel = state.selected
  W.SetEnabled(ui.testButton, drop ~= nil, L["Pick a drop first."])
  local D = ns.Director
  W.SetEnabled(ui.stopButton, D ~= nil and (D.active ~= nil or D.armed ~= nil), L["Nothing is playing."])
  if not drop then
    W.SetEnabled(ui.deleteButton, false, L["Pick a drop first."])
  elseif W.IsBuiltinRef(sel.ref) then
    W.SetEnabled(ui.deleteButton, false, L["Built-in drops can't be deleted: use Turn off here."])
  else
    W.SetEnabled(ui.deleteButton, true)
  end
end

local function newLine(key, height)
  local line = CreateFrame("Frame", nil, form.content)
  line:SetHeight(height)
  line.ndHeight = height
  line.ndVisible = true
  form.lines[#form.lines + 1] = line
  form[key] = line
  return line
end

local function label(line, text)
  local fs = W.Label(line, text, "GameFontNormalSmall")
  fs:SetPoint("LEFT", line, "LEFT", 4, 0)
  fs:SetWidth(LABEL_W - 8)
  fs:SetWordWrap(false)
  return fs
end

local function input(line, key, instructions, maxBytes)
  local e = W.Input(line, instructions, maxBytes)
  e:SetPoint("LEFT", line, "LEFT", LABEL_W, 0)
  e:SetPoint("RIGHT", line, "RIGHT", -8, 0)
  setupField(e, key)
  form.tabOrder[#form.tabOrder + 1] = e
  return e
end

local function statusLine(key)
  local line = newLine(key, 14)
  local fs = W.Label(line, nil, "GameFontHighlightSmall")
  fs:SetPoint("LEFT", line, "LEFT", LABEL_W, 0)
  fs:SetPoint("RIGHT", line, "RIGHT", -8, 0)
  fs:SetWordWrap(false)
  return line, fs
end

local function selectZone(mapID)
  local drop = Dt.SelectedDrop()
  if not (drop and editable()) or drop.mapID == mapID then return end
  local fields = { mapID = mapID }
  if W.KindOf(drop) == "subzone" then
    fields.areaID = false          -- an area belongs to one zone; the landmark stays, by name
  else
    fields.label = W.ZoneName(mapID) or false
  end
  Dt.Update(fields)
end

local function buildForm(pane)
  form.lines, form.tabOrder = {}, {}
  form.scroll = CreateFrame("ScrollFrame", nil, pane)
  form.scroll:SetPoint("TOPLEFT", pane, "TOPLEFT", 0, -2)
  form.scroll:SetPoint("BOTTOMRIGHT", pane, "BOTTOMRIGHT", -14, 2)
  form.content = CreateFrame("Frame", nil, form.scroll)
  form.content:SetSize(360, 600)
  form.scroll:SetScrollChild(form.content)
  form.bar = CreateFrame("EventFrame", nil, pane, "MinimalScrollBar")
  form.bar:SetPoint("TOPLEFT", form.scroll, "TOPRIGHT", 4, 0)
  form.bar:SetPoint("BOTTOMLEFT", form.scroll, "BOTTOMRIGHT", 4, 0)
  form.bar:SetHideIfUnscrollable(true)
  ScrollUtil.InitScrollFrameWithScrollBar(form.scroll, form.bar)
  form.scroll:EnableMouseWheel(true)
  form.scroll:HookScript("OnSizeChanged", function(self, w)
    if type(w) == "number" and w > 0 then form.content:SetWidth(w) end
  end)
  pane.Form = form

  -- Banner (built-in / override / inactive soundtrack).
  local line = newLine("bannerLine", 48)
  form.BannerText = W.Label(line, nil, "GameFontHighlightSmall")
  form.BannerText:SetPoint("TOPLEFT", line, "TOPLEFT", 4, -2)
  form.BannerText:SetPoint("RIGHT", line, "RIGHT", -8, 0)
  form.Change = W.Button(line, L["Change song here"], 130, function() Dt.ChangeSongHere() end,
    L["Change song here"], L["Copies this drop into My Drops so you can pick another song for this place. The built-in soundtrack stays as it is."])
  form.Change:SetPoint("BOTTOMLEFT", line, "BOTTOMLEFT", 4, 0)
  form.TurnOff = W.Button(line, L["Turn off here"], 110, function() Dt.TurnOffHere() end,
    L["Turn off here"], L["Plays nothing at this place. My Drops remembers it; Revert brings the song back."])
  form.TurnOff:SetPoint("LEFT", form.Change, "RIGHT", 6, 0)
  form.Revert = W.Button(line, L["Revert"], 190, function() Dt.Revert() end,
    L["Revert"], L["Removes your change, so the original song plays here again."])
  form.Revert:SetPoint("BOTTOMLEFT", line, "BOTTOMLEFT", 4, 0)
  form.ShowPlaying = W.Button(line, L["Show what plays"], 130, function(self)
    local drop = Dt.SelectedDrop()
    if drop and self.ndRef then Dt.Select(self.ndRef, drop.id, { inView = true }) end
  end, L["Show what plays"], L["Selects the drop that plays at this place."])
  form.ShowPlaying:SetPoint("BOTTOMLEFT", line, "BOTTOMLEFT", 4, 0)
  form.TurnSetOn = W.Button(line, L["Turn it on"], 100, function()
    local sel = state.selected
    if sel then W.TurnOn(sel.ref) end
    W.RefreshAll()
  end, L["Turn it on"], L["Turns this soundtrack on, at the top of your list."])
  form.TurnSetOn:SetPoint("BOTTOMLEFT", line, "BOTTOMLEFT", 4, 0)

  line = newLine("errorLine", 28)
  form.ErrorText = W.Label(line, nil, "GameFontRedSmall")
  form.ErrorText:SetPoint("TOPLEFT", line, "TOPLEFT", 4, -2)
  form.ErrorText:SetPoint("RIGHT", line, "RIGHT", -8, 0)

  -- Place.
  line = newLine("placeLine", 24)
  form.PlaceText = W.Label(line, nil, "GameFontNormal")
  form.PlaceText:SetPoint("LEFT", line, "LEFT", 4, 0)
  form.PlaceText:SetPoint("RIGHT", line, "RIGHT", -124, 0)
  form.PlaceText:SetWordWrap(false)
  form.UseLocation = W.Button(line, L["Use my location"], 116, function()
    local place, why = Dt.CurrentPlace()
    if not place then
      notice(why)
      return
    end
    state.pendingLandmark = false
    Dt.Update(placeFields(place))
  end, L["Use my location"], L["Moves this drop to where you are standing. A new place starts a new history: songs heard here before still count as heard."])
  form.UseLocation:SetPoint("RIGHT", line, "RIGHT", -8, 0)

  line = newLine("scopeLine", 22)
  form.ThisLandmark = W.Radio(line, L["This landmark"], function()
    local drop = Dt.SelectedDrop()
    if not (drop and editable()) then return end
    if W.KindOf(drop) ~= "subzone" then
      state.pendingLandmark = true
      Dt.RefreshForm()
      local here = Dt.CurrentPlace()
      if here and here.mapID == drop.mapID and here.subzone then
        form.Landmark:SetText(here.subzone)
      end
      if form.Landmark:IsVisible() then W.FocusBox(form.Landmark) end
    else
      Dt.RefreshForm()
    end
  end)
  form.ThisLandmark:SetPoint("LEFT", line, "LEFT", 8, 0)
  W.SetTooltip(form.ThisLandmark, W.SimpleTooltip(L["This landmark"], L["Plays at one landmark of the zone, like a town or an inn."]))
  form.WholeZone = W.Radio(line, L["Whole zone"], function()
    local drop = Dt.SelectedDrop()
    state.pendingLandmark = false
    if drop and editable() and W.KindOf(drop) == "subzone" then
      Dt.Update({ kind = "zone", areaID = false, subzone = false, label = W.ZoneName(drop.mapID) or false })
    else
      Dt.RefreshForm()
    end
  end)
  form.WholeZone:SetPoint("LEFT", form.ThisLandmark.text, "RIGHT", 24, 0)
  W.SetTooltip(form.WholeZone, W.SimpleTooltip(L["Whole zone"], L["Plays anywhere in the zone that has no landmark song of its own."]))

  line = newLine("zoneLine", 28)
  local zl = label(line, L["Zone"])
  form.Zone = CreateFrame("DropdownButton", nil, line, "WowStyle1DropdownTemplate")
  form.Zone:SetPoint("LEFT", line, "LEFT", LABEL_W, 0)
  form.Zone:SetWidth(150)
  form.Zone:SetDefaultText(L["Pick a zone"])
  form.Zone:SetupMenu(function(owner, root)
    root:SetScrollMode(400)
    local drop = Dt.SelectedDrop()
    for _, group in ipairs(zonesByContinent()) do
      root:CreateTitle(esc(group.name))
      for _, z in ipairs(group.zones) do
        root:CreateRadio(esc(z.name), function(mapID)
          local d = Dt.SelectedDrop()
          return d ~= nil and d.mapID == mapID
        end, function(mapID) selectZone(mapID) end, z.mapID)
      end
    end
    if drop and drop.mapID and not W.ZoneName(drop.mapID) then
      root:CreateRadio(W.Format(L["Zone %s"], tostring(drop.mapID)), function() return true end, function() end, drop.mapID)
    end
  end)
  form.LandmarkLabel = W.Label(line, L["Landmark"], "GameFontNormalSmall")
  form.LandmarkLabel:SetPoint("LEFT", form.Zone, "RIGHT", 10, 0)
  form.Landmark = W.Input(line, L["Landmark name"], 100)
  form.Landmark:SetPoint("LEFT", form.LandmarkLabel, "RIGHT", 8, 0)
  form.Landmark:SetPoint("RIGHT", line, "RIGHT", -8, 0)
  setupField(form.Landmark, "landmark")
  form.tabOrder[#form.tabOrder + 1] = form.Landmark

  line = newLine("placeNoteLine", 14)
  form.PlaceNote = W.Label(line, nil, "GameFontDisableSmall")
  form.PlaceNote:SetPoint("LEFT", line, "LEFT", LABEL_W, 0)
  form.PlaceNote:SetPoint("RIGHT", line, "RIGHT", -8, 0)

  -- Song.
  line = newLine("artistLine", 24)
  label(line, L["Artist"])
  form.Artist = input(line, "artist", L["Artist"], 200)
  line = newLine("titleLine", 24)
  label(line, L["Title"])
  form.Title = input(line, "title", L["Title"], 200)

  line = newLine("spotifyLine", 24)
  label(line, L["Spotify"])
  form.Spotify = input(line, "spotify", L["Paste a Spotify song link"], 300)
  line, form.SpotifyStatus = statusLine("spotifyStatusLine")

  line = newLine("matchLine", 24)
  form.MatchText = W.Label(line, nil, "GameFontHighlightSmall")
  form.MatchText:SetPoint("LEFT", line, "LEFT", LABEL_W, 0)
  form.MatchText:SetPoint("RIGHT", line, "RIGHT", -150, 0)
  form.MatchText:SetWordWrap(false)
  form.UseMatch = W.Button(line, L["Use this exact song"], 140, function()
    local m = form.match
    if m and nonEmpty(m.spotify) then Dt.Update({ spotify = m.spotify }) end
  end, L["Use this exact song"], L["Saves the song the NeedleDropper app found, so it plays exactly this one from now on."])
  form.UseMatch:SetPoint("RIGHT", line, "RIGHT", -8, 0)
  form.matchLine.ndVisible = false

  line = newLine("youtubeLine", 24)
  label(line, L["YouTube"])
  form.YouTube = input(line, "youtube", L["Paste a YouTube link"], 300)
  line, form.YouTubeStatus = statusLine("youtubeStatusLine")
  form.YouTubeStatus:SetPoint("RIGHT", line, "RIGHT", -110, 0)
  form.UseStart = W.Button(line, L["Start at"], 100, function()
    if form.youtubeStart then Dt.Update({ startMs = form.youtubeStart * 1000 }) end
  end, L["Start at"], L["The link starts part-way into the video: start the drop there too."])
  form.UseStart:SetHeight(16)
  form.UseStart:SetPoint("RIGHT", line, "RIGHT", -8, 0)
  form.UseStart:Hide()

  line = newLine("fileLine", 24)
  label(line, L["In-game file"])
  form.File = input(line, "file", L["goldshire.mp3 in NeedleDropper_Local\\media"], 100)
  line, form.FileStatus = statusLine("fileStatusLine")

  line = newLine("moreLine", 24)
  form.More = W.Button(line, L["More..."], 90, function()
    state.more = not state.more
    Dt.RefreshForm()
  end, L["More..."], L["Start and length, volume, tags, a note, turning this place off, and copying or moving the drop."])
  form.More:SetPoint("LEFT", line, "LEFT", 4, 0)

  -- More... (collapsed by default).
  line = newLine("timingLine", 24)
  label(line, L["Start at"])
  form.Start = W.Input(line, "0:00", 10)
  form.Start:SetPoint("LEFT", line, "LEFT", LABEL_W, 0)
  form.Start:SetWidth(56)
  setupField(form.Start, "start")
  form.tabOrder[#form.tabOrder + 1] = form.Start
  local pl = W.Label(line, L["Play for"], "GameFontNormalSmall")
  pl:SetPoint("LEFT", form.Start, "RIGHT", 14, 0)
  form.Length = W.Input(line, L["whole song"], 10)
  form.Length:SetPoint("LEFT", pl, "RIGHT", 8, 0)
  form.Length:SetWidth(76)
  setupField(form.Length, "length")
  form.tabOrder[#form.tabOrder + 1] = form.Length

  line = newLine("volumeLine", 40)
  label(line, L["Volume"])
  form.Volume = CreateFrame("Frame", nil, line, "MinimalSliderWithSteppersTemplate")
  form.Volume:SetPoint("LEFT", line, "LEFT", LABEL_W - 4, 0)
  form.Volume:SetWidth(200)
  form.Volume:Init(100, 0, 100, 20, {
    [MinimalSliderWithSteppersMixin.Label.Right] = function(v) return W.Format("%d%%", math.floor(v + 0.5)) end,
  })
  form.Volume:RegisterCallback(MinimalSliderWithSteppersMixin.Event.OnValueChanged, function(owner, value)
    if form.refreshing then return end
    Dt.VolumeChanged(value)
  end, form)
  form.Volume:RegisterCallback(MinimalSliderWithSteppersMixin.Event.OnInteractEnd, function()
    flushVolume()
  end, form)
  form.VolumeNote = W.Label(line, L["Spotify and YouTube only; in-game files play at your Music volume."], "GameFontDisableSmall")
  form.VolumeNote:SetPoint("TOPLEFT", form.Volume, "BOTTOMLEFT", 4, 2)

  line = newLine("tagsLine", 26)
  label(line, L["Tags"])
  form.Fitting = W.Check(line, L["Fitting"], function() if not form.refreshing then Dt.Update(FIELD.tags()) end end)
  form.Fitting:SetPoint("LEFT", line, "LEFT", LABEL_W - 4, 0)
  form.Funny = W.Check(line, L["Funny"], function() if not form.refreshing then Dt.Update(FIELD.tags()) end end)
  form.Funny:SetPoint("LEFT", form.Fitting.Text, "RIGHT", 10, 0)
  form.Tags = W.Input(line, L["other tags, separated by commas"], 200)
  form.Tags:SetPoint("LEFT", form.Funny.Text, "RIGHT", 14, 0)
  form.Tags:SetPoint("RIGHT", line, "RIGHT", -8, 0)
  setupField(form.Tags, "tags")
  form.tabOrder[#form.tabOrder + 1] = form.Tags

  line = newLine("noteLine", 60)
  local nl = label(line, L["Note"])
  nl:ClearAllPoints()
  nl:SetPoint("TOPLEFT", line, "TOPLEFT", 4, -4)
  form.Note = CreateFrame("Frame", nil, line, "ScrollingEditBoxTemplate")
  form.Note:SetPoint("TOPLEFT", line, "TOPLEFT", LABEL_W, 0)
  form.Note:SetPoint("BOTTOMRIGHT", line, "BOTTOMRIGHT", -20, 0)
  form.NoteBar = CreateFrame("EventFrame", nil, line, "MinimalScrollBar")
  form.NoteBar:SetPoint("TOPLEFT", form.Note, "TOPRIGHT", 4, 0)
  form.NoteBar:SetPoint("BOTTOMLEFT", form.Note, "BOTTOMRIGHT", 4, 0)
  form.NoteBar:SetHideIfUnscrollable(true)
  ScrollUtil.RegisterScrollBoxWithScrollBar(form.Note:GetScrollBox(), form.NoteBar)
  local noteBox = form.Note:GetEditBox()
  noteBox:SetAutoFocus(false)
  noteBox:SetMaxBytes(500)
  form.Note:SetDefaultText(L["Why the song fits (optional)"])
  form.tabOrder[#form.tabOrder + 1] = noteBox
  -- The note commits when it loses focus; Escape puts the last saved text back instead. The
  -- template's own Escape handler clears focus as well, and callback order is undefined, so the
  -- commit (to the drop that was being edited) runs on the next frame unless Escape was pressed.
  form.Note:RegisterCallback("OnEscapePressed", function()
    form.noteEscaped = true
    form.Note:SetText(form.Note.ndValue or "")
    form.Note:ClearFocus()
  end, form)
  form.Note:RegisterCallback("OnFocusGained", function()
    form.focused = form.Note
    form.noteEscaped = false
  end, form)
  form.Note:RegisterCallback("OnFocusLost", function()
    if form.focused == form.Note then form.focused = nil end
    local sel = state.selected
    if not (sel and editable()) then return end
    local ref, id = sel.ref, sel.id
    local text = trim(form.Note:GetInputText())
    C_Timer.After(0, function()
      if form.noteEscaped then
        form.noteEscaped = false
        return
      end
      local drop = S().GetDrop(ref, id)
      if drop and text ~= (drop.note or "") then
        holdHere({ id }, false)
        local ok, res = S().UpdateDrop(ref, id, { note = text })
        if ok then
          W.RefreshAll()
        else
          notice(res or L["That change could not be saved."])
        end
      end
    end)
  end, form)
  form.Note:RegisterCallback("OnTabPressed", function()
    local target = IsShiftKeyDown() and noteBox.previousEditBox or noteBox.nextEditBox
    if target and target ~= noteBox then target:SetFocus() end
  end, form)

  line = newLine("offLine", 26)
  form.Off = W.Check(line, L["Turn off this place"], function(self)
    if form.refreshing then return end
    Dt.Update({ disabled = self:GetChecked() and true or false })
  end)
  form.Off:SetPoint("LEFT", line, "LEFT", LABEL_W - 4, 0)
  W.SetTooltip(form.Off, W.SimpleTooltip(L["Turn off this place"], L["Nothing plays here, not even a soundtrack lower in the list."]))

  -- Copy to / Move to, also in the row's right-click menu (out of a controller's reach).
  line = newLine("copyLine", 28)
  label(line, L["Copy"])
  form.CopyMove = CreateFrame("DropdownButton", nil, line, "WowStyle1DropdownTemplate")
  form.CopyMove:SetPoint("LEFT", line, "LEFT", LABEL_W, 0)
  form.CopyMove:SetWidth(200)
  form.CopyMove:SetDefaultText(L["Copy or move to..."])
  form.CopyMove:SetupMenu(function(owner, root)
    local data = Dt.SelectedRow()
    if not (data and Dt.AddCopyMoveItems(root, data, true)) then
      root:CreateTitle(L["Make a soundtrack of your own first (Soundtracks tab, New)."])
    end
  end)

  for _, key in ipairs({ "timingLine", "volumeLine", "tagsLine", "noteLine", "offLine", "copyLine" }) do
    form[key].ndVisible = false
  end
end

-- -------------------------------------------------------------------- tab --

function Dt.Refresh()
  if not ui.list then return end
  if ui.showing then ui.showing:GenerateMenu() end
  if ui.filter then ui.filter:ValidateResetState() end
  Dt.RefreshList()
  Dt.RefreshForm()
end

local function buildAttic(attic)
  local sl = W.Label(attic, L["Showing:"], "GameFontNormalSmall")
  sl:SetPoint("LEFT", attic, "LEFT", 2, 0)
  local dd = CreateFrame("DropdownButton", nil, attic, "WowStyle1DropdownTemplate")
  dd:SetPoint("LEFT", sl, "RIGHT", 6, 0)
  dd:SetWidth(200)
  dd:SetDefaultText(L["What plays (all active)"])
  dd:SetupMenu(function(owner, root)
    local model = S()
    local function isSel(ref) return showing() == ref end
    local function setSel(ref)
      setShowing(ref)
      state.selected = nil
      W.RefreshAll()
    end
    root:CreateRadio(L["What plays (all active)"], isSel, setSel, "mix")
    local users, builtins = {}, {}
    for _, info in ipairs(model.List()) do
      if info.builtin then builtins[#builtins + 1] = info else users[#users + 1] = info end
    end
    if #users > 0 then
      root:CreateDivider()
      root:CreateTitle(L["Yours"])
      for _, info in ipairs(users) do root:CreateRadio(esc(info.name), isSel, setSel, info.ref) end
    end
    if #builtins > 0 then
      root:CreateDivider()
      root:CreateTitle(L["Built-in"])
      for _, info in ipairs(builtins) do root:CreateRadio(esc(info.name), isSel, setSel, info.ref) end
    end
    root:CreateDivider()
    root:CreateButton(L["New soundtrack..."], function()
      if W.ShowNewSoundtrack then
        W.ShowNewSoundtrack(function(ref)
          setShowing(ref)
          state.selected = nil
          W.RefreshAll()
        end)
      end
    end)
    root:CreateButton(L["Import..."], function() UI.OpenImport() end)
  end)
  ui.showing = dd

  local add = W.Button(attic, L["Add drop here"], 130, function() W.AddHere() end,
    L["Add drop here"], L["Adds this spot to My Drops (or the soundtrack you're showing)."])
  add:SetPoint("RIGHT", attic, "RIGHT", -2, 0)
  ui.addButton = add

  local filter = CreateFrame("DropdownButton", nil, attic, "WowStyle1FilterDropdownTemplate")
  filter:SetPoint("RIGHT", add, "LEFT", -10, 0)
  filter:SetupMenu(function(owner, root)
    for _, f in ipairs(FILTERS) do
      root:CreateCheckbox(L[f.text], function(key) return state.filters[key] == true end, function(key)
        state.filters[key] = (not state.filters[key]) or nil
        Dt.RefreshList()
      end, f.key)
    end
  end)
  filter:SetIsDefaultCallback(function() return next(state.filters) == nil end)
  filter:SetDefaultCallback(function()
    state.filters = {}
    Dt.RefreshList()
  end)
  ui.filter = filter

  local search = CreateFrame("EditBox", nil, attic, "SearchBoxTemplate")
  search:SetAutoFocus(false)
  search:SetHeight(20)
  search:SetPoint("LEFT", dd, "RIGHT", 14, 0)
  search:SetPoint("RIGHT", filter, "LEFT", -14, 0)
  search:SetMaxBytes(100)
  search:HookScript("OnTextChanged", function(self)
    local text = self:GetText() or ""
    if text ~= state.search then
      state.search = text
      Dt.RefreshList()
    end
  end)
  ui.search = search

  attic.Showing, attic.AddButton, attic.Filter, attic.Search = dd, add, filter, search
end

local function buildButtons(buttons)
  local del = W.Button(buttons, DELETE or L["Delete"], 80, function() Dt.Delete() end,
    DELETE or L["Delete"], L["Deletes the drop at once. Undo stays available for 15 seconds."])
  del:SetPoint("RIGHT", buttons, "RIGHT", 0, 0)
  local stop = W.Button(buttons, L["Stop"], 64, function() Dt.Stop() end, L["Stop"], L["Stops the song that is playing."])
  stop:SetPoint("RIGHT", del, "LEFT", -4, 0)
  local test = W.Button(buttons, L["Test"], 64, function() Dt.Test() end, L["Test"],
    L["Plays this drop now, even an unsaved draft. It is not marked as heard."])
  test:SetPoint("RIGHT", stop, "LEFT", -4, 0)
  test.ndTooltip = function(tt, self)
    GameTooltip_SetTitle(tt, L["Test"])
    GameTooltip_AddNormalLine(tt, L["Plays this drop now, even an unsaved draft. It is not marked as heard."], true)
    local drop = Dt.SelectedDrop()
    if drop and W.IsDraft(drop) then
      GameTooltip_AddNormalLine(tt, L["This drop isn't finished: Test plays it, but it won't play when you arrive until it has an artist and title."], true)
    end
    local C = ns.Companion
    local st = C and C.Status and C.Status()
    if drop and not nonEmpty(drop.file) and type(st) == "table" and not st.present then
      GameTooltip_AddErrorLine(tt, L["Spotify and YouTube songs are played by the NeedleDropper app, which has not been seen on this PC."], true)
    elseif drop and not nonEmpty(drop.file) and type(st) == "table" and type(st.written) == "number" and st.written > 0 then
      GameTooltip_AddNormalLine(tt, W.Format(L["Spotify and YouTube songs are played by the NeedleDropper app (last seen %s)."], W.FormatDate(st.written)), true)
    end
  end
  buttons:SetWidth(64 + 4 + 64 + 4 + 80)
  ui.testButton, ui.stopButton, ui.deleteButton = test, stop, del
  buttons.Test, buttons.Stop, buttons.Delete = test, stop, del
end

local function build(panel, attic, buttons)
  buildAttic(attic)
  buildButtons(buttons)

  local list = CreateFrame("Frame", nil, panel, "WowScrollBoxList")
  list:SetPoint("TOPLEFT", panel, "TOPLEFT", 4, -4)
  list:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", 4, 4)
  list:SetWidth(LIST_W - 16)
  local bar = CreateFrame("EventFrame", nil, panel, "MinimalScrollBar")
  bar:SetPoint("TOPLEFT", list, "TOPRIGHT", 4, 0)
  bar:SetPoint("BOTTOMLEFT", list, "BOTTOMRIGHT", 4, 0)
  bar:SetHideIfUnscrollable(true)
  local view = CreateScrollBoxListLinearView(2, 2, 2, 2, 1)
  view:SetElementExtent(ROW_EXTENT)       -- Lua-built rows have no template extent
  view:SetElementInitializer("Button", initRow)
  ScrollUtil.InitScrollBoxListWithScrollBar(list, bar, view)
  ui.list, ui.view, ui.bar = list, view, bar
  panel.List, panel.ListView, panel.ListBar = list, view, bar

  ui.listEmpty = W.Label(panel, nil, "GameFontDisableSmall")
  ui.listEmpty:SetPoint("TOPLEFT", list, "TOPLEFT", 10, -12)
  ui.listEmpty:SetPoint("RIGHT", list, "RIGHT", -10, 0)
  ui.listEmpty:SetJustifyH("CENTER")
  ui.listEmpty:Hide()
  panel.ListEmpty = ui.listEmpty

  local pane = CreateFrame("Frame", nil, panel)
  pane:SetPoint("TOPLEFT", panel, "TOPLEFT", LIST_W + 6, -4)
  pane:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -4, 4)
  ui.pane = pane
  panel.FormPane = pane
  buildForm(pane)
  ui.formEmpty = W.Label(pane, nil, "GameFontHighlight")
  ui.formEmpty:SetPoint("TOPLEFT", pane, "TOPLEFT", 16, -40)
  ui.formEmpty:SetPoint("RIGHT", pane, "RIGHT", -16, 0)
  ui.formEmpty:SetJustifyH("CENTER")
  ui.formEmpty:Hide()
  panel.FormEmpty = ui.formEmpty

  -- "Deleted Goldshire. [Undo]"
  local undo = CreateFrame("Frame", nil, panel)
  undo:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", 4, 4)
  undo:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -4, 4)
  undo:SetHeight(28)
  undo:SetFrameLevel(panel:GetFrameLevel() + 20)
  undo.Bg = undo:CreateTexture(nil, "BACKGROUND")
  undo.Bg:SetAllPoints(undo)
  undo.Bg:SetColorTexture(0, 0, 0, 0.85)
  undo.Text = W.Label(undo, nil, "GameFontHighlight")
  undo.Text:SetPoint("LEFT", undo, "LEFT", 10, 0)
  undo.Button = W.Button(undo, L["Undo"], 80, function() Dt.Undo() end, L["Undo"], L["Brings the deleted drop back."])
  undo.Button:SetPoint("LEFT", undo.Text, "RIGHT", 10, 0)
  undo:Hide()
  ui.undo = undo
  panel.UndoBar = undo
end

W.RegisterTab({
  key = "drops",
  title = L["Drops"],
  hasStop = true,         -- its button bar has Stop: the window's own Stop stays hidden here
  Build = build,
  Refresh = function() Dt.Refresh() end,
  UpdateButtons = function() Dt.RefreshButtons() end,
  OnShow = function(opened)
    if opened or not state.selected then Dt.SelectCurrentPlace() end
    Dt.Refresh()
  end,
  OnHide = function() Dt.ClearFormFocus() end,
})
