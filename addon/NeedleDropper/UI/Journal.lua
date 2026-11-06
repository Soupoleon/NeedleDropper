local ADDON, ns = ...
-- UI/Journal.lua: the Journal tab (DESIGN-v1.2 §4.6). Newest first, from NeedleDropperCharDB.journal
-- (at most 200 entries): date, place, "Artist · Title", soundtrack, and per row
--   [Replay]                 plays the song again as a test (FIRE + TEST; nothing is recorded), from the
--                            song stored in the entry, so it still works after the drop changed or went away
--   [Play again next visit]  forgets that the place was heard, for this character and the account
--   [Show drop]              opens the drop on the Drops tab
-- Rows are Lua-built buttons in a WowScrollBoxList with an explicit element extent
-- (Blizzard_SharedXML/Shared/Scroll/ScrollBoxListView.lua L41-L52).

local UI = ns.UI
local W = UI._W
local L = W.L
local esc, nonEmpty = W.Esc, W.NonEmpty

local Jt = {}
W.Journal = Jt

local ROW_EXTENT = 46
local ui = {}

-- The journal entry as a playable drop (place fields from the id: zone / area / sub / inst).
function Jt.DropFor(e)
  local d = { id = e.id, label = e.label, title = e.title, artist = e.artist, spotify = e.spotify,
              youtube = e.youtube, file = e.file, startMs = e.startMs or 0, durationMs = e.durationMs or 0,
              volume = e.volume, setId = e.setId }
  local live = ns.Map and ns.Map.byId and ns.Map.byId[e.id]
  local kind, a, b = string.match(e.id or "", "^(%a+):([^:]+):?(.*)$")
  if kind == "zone" then
    d.kind, d.mapID = "zone", tonumber(a)
  elseif kind == "area" then
    d.kind, d.areaID = "subzone", tonumber(a)
  elseif kind == "sub" then
    d.kind, d.mapID = "subzone", tonumber(a)
    d.subzone = live and live.subzone or e.label
  elseif kind == "inst" then
    d.kind, d.instanceID = "instance", tonumber(a)
  else
    d.kind = "zone"
  end
  if live then
    d.mapID = d.mapID or live.mapID
    d.areaID = d.areaID or live.areaID
    d.subzone = d.subzone or live.subzone
  end
  return d
end

local function isDone(id)
  return (ns.cdb and ns.cdb.done and ns.cdb.done[id] ~= nil)
      or (ns.db and ns.db.accountDone and ns.db.accountDone[id] ~= nil)
end

local function playsSomewhere(id)
  local e = W.MergedIndex()[id]
  return e ~= nil and not (e.disabled or (e.drop and e.drop.disabled))
end

function Jt.Replay(e)
  local D = ns.Director
  if D and D.PlayTest then
    D.PlayTest(W.Playable(Jt.DropFor(e)))
    W.UpdateFooter()
  end
end

-- Clears "heard" for this character and the account; the place plays again on the next visit
-- (not right now, if the player is standing there).
function Jt.PlayAgain(e)
  local id = e.id
  if ns.cdb and ns.cdb.done then ns.cdb.done[id] = nil end
  if ns.db and ns.db.accountDone then ns.db.accountDone[id] = nil end
  -- Exploration history must not mark it heard again.
  if ns.cdb and type(ns.cdb.scanned) == "table" then ns.cdb.scanned[id] = true end
  local Loc = ns.Location
  if Loc and Loc.Candidates and ns.Director and ns.Director.SuppressHere then
    for _, d in ipairs(Loc.Candidates()) do
      if d.id == id then ns.Director.SuppressHere(id) end
    end
  end
  W.RefreshAll()
end

function Jt.ShowDrop(e)
  UI.Open("drops")
  if W.Drops and W.Drops.ShowDrop then W.Drops.ShowDrop(e.id) end
end

local function setName(e)
  local ref = e.setId and W.RefForSetId(e.setId)
  if ref then return W.SetName(ref) end
  return e.setId
end

local function initRow(row, data)
  if not row.ndBuilt then
    row.ndBuilt = true
    row.Hover = row:CreateTexture(nil, "HIGHLIGHT")
    row.Hover:SetAllPoints(row)
    row.Hover:SetColorTexture(1, 1, 1, 0.06)
    row.When = W.Label(row, nil, "GameFontHighlightSmall")
    row.When:SetPoint("TOPLEFT", row, "TOPLEFT", 8, -7)
    row.Place = W.Label(row, nil, "GameFontNormal")
    row.Place:SetPoint("LEFT", row.When, "RIGHT", 10, 0)
    row.Place:SetPoint("RIGHT", row, "RIGHT", -320, 0)
    row.Place:SetWordWrap(false)
    row.Song = W.Label(row, nil, "GameFontHighlightSmall")
    row.Song:SetPoint("TOPLEFT", row.When, "BOTTOMLEFT", 0, -6)
    row.Song:SetPoint("RIGHT", row, "RIGHT", -320, 0)
    row.Song:SetWordWrap(false)
    row.ShowDrop = W.Button(row, L["Show drop"], 86, function(self) Jt.ShowDrop(self:GetParent().ndEntry) end,
      L["Show drop"], L["Opens this place on the Drops tab."])
    row.ShowDrop:SetPoint("RIGHT", row, "RIGHT", -6, 0)
    row.Again = W.Button(row, L["Play again next visit"], 150, function(self) Jt.PlayAgain(self:GetParent().ndEntry) end,
      L["Play again next visit"], L["Forgets that you heard this place, on this character and the account, so its song plays the next time you arrive."])
    row.Again:SetPoint("RIGHT", row.ShowDrop, "LEFT", -4, 0)
    row.Replay = W.Button(row, L["Replay"], 70, function(self) Jt.Replay(self:GetParent().ndEntry) end,
      L["Replay"], L["Plays this song again now, as a test. It changes nothing."])
    row.Replay:SetPoint("RIGHT", row.Again, "LEFT", -4, 0)
  end
  local e = data.entry
  row.ndEntry = e
  row.When:SetText(W.FormatDate(e.t))
  local live = ns.Map and ns.Map.byId and ns.Map.byId[e.id]
  row.Place:SetText(live and W.PlaceLine(live) or esc(e.label or e.id))
  local song = W.SongText(e) or L["(song not recorded)"]
  local from = setName(e)
  if from then song = song .. W.DOT .. esc(from) end
  row.Song:SetText(song)
  local playable = nonEmpty(e.title) or nonEmpty(e.artist) or nonEmpty(e.spotify) or nonEmpty(e.youtube) or nonEmpty(e.file)
  W.SetEnabled(row.Replay, playable ~= nil, L["This entry has no song to play."])
  W.SetEnabled(row.Again, isDone(e.id), L["Not heard any more: it plays the next time you arrive."])
  W.SetEnabled(row.ShowDrop, playsSomewhere(e.id), L["This place has no needle drop in your soundtracks any more."])
end

function Jt.Entries()
  local j = ns.cdb and ns.cdb.journal or {}
  local out = {}
  for i = #j, 1, -1 do
    local e = j[i]
    if type(e) == "table" and type(e.id) == "string" then out[#out + 1] = { entry = e, index = i } end
  end
  return out
end

function Jt.Refresh()
  if not ui.list then return end
  local entries = Jt.Entries()
  ui.list:SetDataProvider(CreateDataProvider(entries), ScrollBoxConstants.RetainScrollPosition)
  ui.empty:SetShown(#entries == 0)
  ui.count:SetText(#entries == 1 and L["1 song heard on this character"]
    or W.Format(L["%s songs heard on this character, newest first"], tostring(#entries)))
end

local function build(panel, attic, buttons)
  ui.count = W.Label(attic, nil, "GameFontHighlightSmall")
  ui.count:SetPoint("LEFT", attic, "LEFT", 2, 0)
  attic.Count = ui.count

  local list = CreateFrame("Frame", nil, panel, "WowScrollBoxList")
  list:SetPoint("TOPLEFT", panel, "TOPLEFT", 4, -4)
  list:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -20, 4)
  local bar = CreateFrame("EventFrame", nil, panel, "MinimalScrollBar")
  bar:SetPoint("TOPLEFT", list, "TOPRIGHT", 4, 0)
  bar:SetPoint("BOTTOMLEFT", list, "BOTTOMRIGHT", 4, 0)
  bar:SetHideIfUnscrollable(true)
  local view = CreateScrollBoxListLinearView(2, 2, 2, 2, 1)
  view:SetElementExtent(ROW_EXTENT)
  view:SetElementInitializer("Button", initRow)
  ScrollUtil.InitScrollBoxListWithScrollBar(list, bar, view)
  ui.list = list
  panel.List, panel.ListView, panel.ListBar = list, view, bar

  ui.empty = W.Label(panel, L["Your journal is empty: no needle drops heard on this character yet."], "GameFontDisableSmall")
  ui.empty:SetPoint("TOP", panel, "TOP", 0, -40)
  ui.empty:Hide()
  panel.Empty = ui.empty
  buttons:SetWidth(1)
end

W.RegisterTab({
  key = "journal",
  title = L["Journal"],
  Build = build,
  Refresh = function() Jt.Refresh() end,
  OnShow = function() Jt.Refresh() end,
})
