local ADDON, ns = ...
-- UI/ShareDialogs.lua: the Import and Share dialogs (DESIGN-v1.2 §5.6), each its own named
-- ButtonFrameTemplate window in UISpecialFrames, built the first time it opens and hidden with
-- the main window in combat.
--
-- Import (NeedleDropperImportDialog): a ScrollingEditBoxTemplate with a MinimalScrollBar
-- (ScrollUtil.RegisterScrollBoxWithScrollBar). Its 65,536-letter cap is set after creation,
-- because the template reads its maxLetters key-value only at OnLoad
-- (Blizzard_SharedXML/Shared/Scroll/ScrollTemplates.lua L15-L30). The code is decoded in the
-- OnTextChanged callback only for the player's own edits (userChanged) and only when the text
-- differs from the last one seen: the client fires the event twice on a paste
-- (Blizzard_EditMode/Shared/EditModeDialogs.lua L337-L345). Preview: name, author, revision, drop
-- count, the first 12 places with their S/Y/F letters, then warnings. Buttons: Import (disabled
-- until the code is valid) / Update <name> (when Share.CanUpdate offers it) / Cancel; after
-- importing, "Imported <name> (off)." and [Turn on].
--
-- Share (NeedleDropperShareDialog): an Author field (remembered in NeedleDropperDB.ui.author, empty
-- by default for privacy), a read-only ScrollingEditBoxTemplate holding the code, highlighted and
-- focused so Ctrl+C copies it (CopyToClipboard is protected); any edit is undone (the Cell
-- pattern). The callback signature is func(owner, ...) (Blizzard_SharedXMLBase/CallbackRegistry.lua
-- L112 / L184), so every callback below takes the owner first.

local UI = ns.UI
local W = UI._W
local L = W.L
local esc, trim, nonEmpty = W.Esc, W.Trim, W.NonEmpty
local tinsert = table.insert

local PREVIEW_PLACES = 12
local MAX_WARNING_LINES = 6

local REASONS = {
  notcode = "That is not a NeedleDropper share code. Share codes start with !NeedleDropper:1!",
  newer = "This code was made with a newer NeedleDropper: update the addon.",
  cutoff = "The code was cut off: copy all of it, up to the ! at the end.",
  damaged = "The code is damaged: copy it again.",
  toobig = "That code is too big for NeedleDropper.",
  invalid = "The code opened, but the soundtrack inside is not valid.",
}

local function reasonText(reason)
  reason = tostring(reason or "invalid")
  local foreign = string.match(reason, "^foreign:(.+)$")
  if foreign then return W.Format(L["This is a %s string, not a NeedleDropper soundtrack."], esc(foreign)) end
  return L[REASONS[reason] or REASONS.invalid]
end

local function dialogFrame(name, title, w, h)
  local f = CreateFrame("Frame", name, UIParent, "ButtonFrameTemplate")
  f:Hide()
  ButtonFrameTemplate_HidePortrait(f)
  f:SetTitle(title)
  f:SetSize(w, h)
  f:SetPoint("CENTER", UIParent, "CENTER", 0, 30)
  f:SetFrameStrata("DIALOG")
  f:SetToplevel(true)
  f:SetClampedToScreen(true)
  f:SetMovable(true)
  f:SetDontSavePosition(true)
  f:EnableMouse(true)
  f.CloseButton:SetScript("OnClick", function() f:Hide() end)
  W.SetTooltip(f.CloseButton, W.SimpleTooltip(CLOSE or L["Close"]))
  tinsert(UISpecialFrames, name)
  local drag = CreateFrame("Frame", nil, f)
  drag:SetPoint("TOPLEFT", f, "TOPLEFT", 4, 0)
  drag:SetPoint("TOPRIGHT", f.CloseButton, "TOPLEFT", -4, 0)
  drag:SetHeight(22)
  drag:EnableMouse(true)
  drag:RegisterForDrag("LeftButton")
  drag:SetScript("OnDragStart", function() f:StartMoving() end)
  drag:SetScript("OnDragStop", function() f:StopMovingOrSizing() end)
  f.TitleDrag = drag
  W.ManageInCombat(f)
  return f
end

local function codeBox(f, height)
  local box = CreateFrame("Frame", nil, f.Inset, "ScrollingEditBoxTemplate")
  box:SetPoint("TOPLEFT", f.Inset, "TOPLEFT", 8, -8)
  box:SetPoint("TOPRIGHT", f.Inset, "TOPRIGHT", -24, -8)
  box:SetHeight(height)
  local bar = CreateFrame("EventFrame", nil, f.Inset, "MinimalScrollBar")
  bar:SetPoint("TOPLEFT", box, "TOPRIGHT", 6, 0)
  bar:SetPoint("BOTTOMLEFT", box, "BOTTOMRIGHT", 6, 0)
  bar:SetHideIfUnscrollable(true)
  ScrollUtil.RegisterScrollBoxWithScrollBar(box:GetScrollBox(), bar)
  local eb = box:GetEditBox()
  eb:SetAutoFocus(false)
  box.Bar = bar
  return box
end

-- ================================================================= import --

local I = { frame = nil, pack = nil, warnings = nil, lastText = nil, imported = nil, updateRef = nil }
W.Import = I

local function packDropAsDrop(p)
  local d = { mapID = p.zone or p.mapID, areaID = p.areaID, subzone = p.subzone, instanceID = p.instanceID,
              label = p.label, artist = p.artist, title = p.title, youtube = p.youtube, file = p.file,
              spotify = p.spotify, disabled = p.disabled }
  if type(d.mapID) ~= "number" then d.mapID = nil end
  if d.instanceID then d.kind = "instance" elseif d.areaID or d.subzone then d.kind = "subzone" else d.kind = "zone" end
  d.id = p.id or (ns.Soundtracks and ns.Soundtracks.DropIdFor(d)) or ""
  return d
end

local function setButtons()
  local f = I.frame
  local valid = I.pack ~= nil and not I.imported
  W.SetEnabled(f.ImportButton, valid, I.pack and L["Already imported."] or L["Paste a valid share code first."])
  f.ImportButton:SetShown(not I.imported)
  f.UpdateButton:SetShown(valid and I.updateRef ~= nil)
  if I.updateRef then f.UpdateButton:SetText(W.Format(L["Update %s"], esc(W.SetName(I.updateRef)))) end
  f.TurnOnButton:SetShown(I.imported ~= nil and not I.turnedOn)
  f.CancelButton:SetText(I.imported and (CLOSE or L["Close"]) or (CANCEL or L["Cancel"]))
  f.NameLabel:SetShown(valid)
  f.Name:SetShown(valid)
end

function I.ShowEmpty()
  local f = I.frame
  f.Status:SetFontObject("GameFontHighlightSmall")
  f.Status:SetText(L["Paste a share code above. Nothing is imported until you press Import."])
  f.Header:SetText("")
  f.Places:SetText("")
  f.Warnings:SetText("")
  setButtons()
end

function I.ShowError(reason)
  local f = I.frame
  f.Status:SetFontObject("GameFontRedSmall")
  f.Status:SetText(reasonText(reason))
  f.Header:SetText("")
  f.Places:SetText("")
  f.Warnings:SetText("")
  setButtons()
end

-- The preview: name, author, revision and drop count; the first places; then the warnings.
function I.ShowPreview()
  local f = I.frame
  local pack = I.pack
  local drops = type(pack.drops) == "table" and pack.drops or {}
  f.Status:SetFontObject("GameFontHighlightSmall")
  f.Status:SetText(L["This code holds a soundtrack. Check it, then press Import: it arrives turned off."])
  local author = nonEmpty(pack.author) and esc(pack.author) or L["no author given"]
  local count = #drops == 1 and L["1 drop"] or W.Format(L["%s drops"], tostring(#drops))
  f.Header:SetText(W.Format(L["%s by %s, revision %s: %s"], esc(pack.name or "?"), author,
    tostring(pack.revision or 0), count))

  local lines = {}
  local unknownZones, files, byName, overrides = 0, 0, 0, 0
  local merged = ns.Soundtracks and W.MergedIndex() or {}
  for i, p in ipairs(drops) do
    local d = packDropAsDrop(p)
    if i <= PREVIEW_PLACES then
      local src = W.Sources(d)
      local song = W.SongText(d) or (d.disabled and L["turned off here"]) or ""
      local line = W.PlaceLine(d)
      if song ~= "" then line = line .. W.DOT .. song end
      if src ~= "" then line = line .. "  " .. src end
      lines[#lines + 1] = line
    end
    if d.mapID and not W.ZoneName(d.mapID) then unknownZones = unknownZones + 1 end
    if nonEmpty(d.file) then files = files + 1 end
    if d.subzone and not d.areaID then byName = byName + 1 end
    if merged[d.id] then overrides = overrides + 1 end
  end
  if #drops > PREVIEW_PLACES then
    lines[#lines + 1] = W.Format(L["... and %s more"], tostring(#drops - PREVIEW_PLACES))
  end
  f.Places:SetText(table.concat(lines, "\n"))

  local warn = {}
  if unknownZones > 0 then
    warn[#warn + 1] = W.Format(L["%s places are in zones this game client does not know; they will not play."], tostring(unknownZones))
  end
  if files > 0 then
    warn[#warn + 1] = W.Format(L["%s drops play in-game files you may not have: copy them to NeedleDropper_Local\\media, then restart WoW."], tostring(files))
  end
  if byName > 0 then
    warn[#warn + 1] = W.Format(L["%s landmarks are saved by name: they only work on game clients in the language the author used."], tostring(byName))
  end
  if overrides > 0 then
    warn[#warn + 1] = W.Format(L["Turned on at the top, it would change the song at %s places you hear now."], tostring(overrides))
  end
  local dropped = I.warnings or {}
  for i = 1, math.min(#dropped, MAX_WARNING_LINES) do
    warn[#warn + 1] = esc(tostring(dropped[i]))
  end
  if #dropped > MAX_WARNING_LINES then
    warn[#warn + 1] = W.Format(L["... and %s more notes"], tostring(#dropped - MAX_WARNING_LINES))
  end
  f.Warnings:SetText(table.concat(warn, "\n"))

  if not f.Name:HasFocus() then f.Name:SetText(pack.name or "") end
  I.updateRef = ns.Share.CanUpdate and ns.Share.CanUpdate(pack) or nil
  setButtons()
end

function I.Decode(text)
  I.pack, I.warnings, I.imported, I.updateRef, I.turnedOn = nil, nil, nil, nil, nil
  if trim(text) == "" then
    I.ShowEmpty()
    return
  end
  local pack, extra = ns.Share.Decode(text)
  if type(pack) ~= "table" then
    I.ShowError(extra)
    return
  end
  I.pack, I.warnings = pack, type(extra) == "table" and extra or {}
  I.ShowPreview()
end

function I.OnTextChanged(userChanged)
  if not userChanged then return end
  local text = I.frame.Code:GetInputText()
  if text == I.lastText then return end
  I.lastText = text
  I.Decode(text)
end

function I.Import()
  if not I.pack or I.imported then return end
  local name = trim(I.frame.Name:GetText())
  local ref, err = ns.Share.Import(I.pack, { name = name ~= "" and name or nil })
  local f = I.frame
  if not ref then
    f.Status:SetFontObject("GameFontRedSmall")
    f.Status:SetText(esc(err or L["The soundtrack could not be imported."]))
    return
  end
  I.imported = ref
  f.Status:SetFontObject("GameFontNormal")
  f.Status:SetText(W.Format(L["Imported %s (off)."], esc(W.SetName(ref))))
  setButtons()
  W.RefreshAll()
end

function I.Update()
  if not (I.pack and I.updateRef) then return end
  local ref = I.updateRef
  local ok, err = ns.Share.Update(ref, I.pack)
  local f = I.frame
  if not ok then
    f.Status:SetFontObject("GameFontRedSmall")
    f.Status:SetText(esc(err or L["The soundtrack could not be updated."]))
    return
  end
  I.imported, I.turnedOn = ref, true
  f.Status:SetFontObject("GameFontNormal")
  f.Status:SetText(W.Format(L["Updated %s. It keeps its on/off state and its place in the list."], esc(W.SetName(ref))))
  setButtons()
  W.RefreshAll()
end

function I.TurnOn()
  if not I.imported then return end
  W.TurnOn(I.imported)
  I.turnedOn = true
  local f = I.frame
  f.Status:SetFontObject("GameFontNormal")
  f.Status:SetText(W.Format(L["Imported %s. It is on, at the top of your soundtracks."], esc(W.SetName(I.imported))))
  setButtons()
  W.RefreshAll()
end

local function buildImport()
  if I.frame then return I.frame end
  local f = dialogFrame("NeedleDropperImportDialog", L["Import a soundtrack"], 560, 500)
  I.frame = f
  f.Hint = W.Label(f, L["Paste a NeedleDropper share code here (Ctrl+V)."], "GameFontHighlightSmall")
  f.Hint:SetPoint("TOPLEFT", f, "TOPLEFT", 14, -34)
  f.Code = codeBox(f, 96)
  f.Code:GetEditBox():SetMaxLetters(65536)
  f.Code:RegisterCallback("OnTextChanged", function(owner, editBox, userChanged) I.OnTextChanged(userChanged) end, I)
  f.Status = W.Label(f.Inset, nil, "GameFontHighlightSmall")
  f.Status:SetPoint("TOPLEFT", f.Code, "BOTTOMLEFT", 0, -10)
  f.Status:SetPoint("RIGHT", f.Inset, "RIGHT", -10, 0)
  f.Header = W.Label(f.Inset, nil, "GameFontNormal")
  f.Header:SetPoint("TOPLEFT", f.Status, "BOTTOMLEFT", 0, -10)
  f.Header:SetPoint("RIGHT", f.Inset, "RIGHT", -10, 0)
  f.Places = W.Label(f.Inset, nil, "GameFontHighlightSmall")
  f.Places:SetPoint("TOPLEFT", f.Header, "BOTTOMLEFT", 0, -6)
  f.Places:SetPoint("RIGHT", f.Inset, "RIGHT", -10, 0)
  f.Warnings = W.Label(f.Inset, nil, "GameFontNormalSmall")
  f.Warnings:SetPoint("TOPLEFT", f.Places, "BOTTOMLEFT", 0, -8)
  f.Warnings:SetPoint("RIGHT", f.Inset, "RIGHT", -10, 0)
  f.NameLabel = W.Label(f.Inset, L["Name"], "GameFontNormalSmall")
  f.NameLabel:SetPoint("BOTTOMLEFT", f.Inset, "BOTTOMLEFT", 10, 12)
  f.Name = W.Input(f.Inset, L["Soundtrack name"], 64)
  f.Name:SetPoint("LEFT", f.NameLabel, "RIGHT", 10, 0)
  f.Name:SetWidth(240)
  f.Name:SetScript("OnEnterPressed", function(self) self:ClearFocus() end)

  f.CancelButton = W.Button(f, CANCEL or L["Cancel"], 84, function() f:Hide() end, CANCEL or L["Cancel"])
  f.CancelButton:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -8, 3)
  f.ImportButton = W.Button(f, L["Import"], 90, function() I.Import() end, L["Import"],
    L["Adds the soundtrack to yours, turned off. Nothing of yours is overwritten."])
  f.ImportButton:SetPoint("RIGHT", f.CancelButton, "LEFT", -4, 0)
  f.UpdateButton = W.Button(f, L["Update"], 170, function() I.Update() end, L["Update"],
    L["Replaces your copy with this newer revision from the same author. It stays on or off as it was."])
  f.UpdateButton:SetPoint("RIGHT", f.ImportButton, "LEFT", -4, 0)
  f.TurnOnButton = W.Button(f, L["Turn on"], 90, function() I.TurnOn() end, L["Turn on"],
    L["Turns the imported soundtrack on, at the top of your list."])
  f.TurnOnButton:SetPoint("RIGHT", f.CancelButton, "LEFT", -4, 0)
  return f
end

function W.OpenImport(prefillText)
  if not (ns.db and ns.Share) then
    if ns.Print then ns.Print(L["NeedleDropper is still loading - try again in a moment."]) end
    return false
  end
  local f = buildImport()
  I.pack, I.warnings, I.imported, I.updateRef, I.turnedOn, I.lastText = nil, nil, nil, nil, nil, nil
  f.Name:SetText("")
  if f:IsShown() or not W.InCombat() then
    f:Show()
    f:Raise()
  else
    W.OpenAfterCombat(f)          -- dialogs stay hidden in combat; this one opens when it ends
  end
  if type(prefillText) == "string" and prefillText ~= "" then
    f.Code:SetText(prefillText)
    I.lastText = prefillText
    I.Decode(prefillText)
  else
    f.Code:SetText("")
    I.ShowEmpty()
    W.FocusBox(f.Code)      -- ready for Ctrl+V (mouse and keyboard; see W.FocusBox)
  end
  return true
end

-- ================================================================== share --

local Sd = { frame = nil, ref = nil, code = nil }
W.Share = Sd

function Sd.Generate()
  local f = Sd.frame
  local model = ns.Soundtracks
  local pack, omitted = ns.Share.FromSet(Sd.ref)
  Sd.code = nil
  if type(pack) ~= "table" then
    f.Code:SetText("")
    f.Count:SetText("")
    f.Omitted:SetText("")
    f.Error:SetText(L["That soundtrack could not be shared."])
    return
  end
  if not W.IsBuiltinRef(Sd.ref) then pack.author = trim(f.Author:GetText()) end
  local code, err = ns.Share.Encode(pack)
  if not code then
    f.Code:SetText("")
    f.Count:SetText("")
    f.Error:SetText(esc(err or L["That soundtrack could not be shared."]))
    return
  end
  f.Error:SetText("")
  Sd.code = code
  f.Code:SetText(code)
  f.Count:SetText(W.Format(L["%s characters"], tostring(#code)))
  omitted = tonumber(omitted) or 0
  if omitted == 1 then
    f.Omitted:SetText(L["1 unfinished drop was not included."])
  elseif omitted > 1 then
    f.Omitted:SetText(W.Format(L["%s unfinished drops were not included."], tostring(omitted)))
  else
    f.Omitted:SetText("")
  end
  W.FocusBox(f.Code)
  f.Code:GetEditBox():HighlightText()
end

local function commitAuthor()
  local f = Sd.frame
  local text = trim(f.Author:GetText())
  if text == (f.Author.ndValue or "") then return end
  f.Author.ndValue = text
  local u = W.UIDB()
  if u then u.author = text end
  Sd.Generate()
end

local function buildShare()
  if Sd.frame then return Sd.frame end
  local f = dialogFrame("NeedleDropperShareDialog", L["Share a soundtrack"], 520, 420)
  Sd.frame = f
  f.AuthorLabel = W.Label(f, L["Author"], "GameFontNormalSmall")
  f.AuthorLabel:SetPoint("TOPLEFT", f, "TOPLEFT", 14, -36)
  f.Author = W.Input(f, L["Your name (optional)"], 64)
  f.Author:SetPoint("LEFT", f.AuthorLabel, "RIGHT", 10, 0)
  f.Author:SetWidth(200)
  f.Author:SetScript("OnEnterPressed", function(self)
    commitAuthor()
    self:ClearFocus()
  end)
  f.Author:SetScript("OnEscapePressed", function(self)
    self:SetText(self.ndValue or "")
    self:ClearFocus()
  end)
  f.Author:SetScript("OnEditFocusLost", function(self)
    self:HighlightText(0, 0)
    commitAuthor()
  end)
  f.Code = codeBox(f, 170)
  -- Read-only: any edit puts the code back, selected again.
  f.Code:RegisterCallback("OnTextChanged", function(owner, editBox, userChanged)
    if userChanged and Sd.code then
      f.Code:SetText(Sd.code)
      editBox:HighlightText()
    end
  end, Sd)
  f.Code:RegisterCallback("OnFocusGained", function(owner, editBox) editBox:HighlightText() end, Sd)
  f.Count = W.Label(f.Inset, nil, "GameFontHighlightSmall")
  f.Count:SetPoint("TOPLEFT", f.Code, "BOTTOMLEFT", 0, -8)
  f.Omitted = W.Label(f.Inset, nil, "GameFontHighlightSmall")
  f.Omitted:SetPoint("LEFT", f.Count, "RIGHT", 16, 0)
  f.Error = W.Label(f.Inset, nil, "GameFontRedSmall")
  f.Error:SetPoint("TOPLEFT", f.Count, "BOTTOMLEFT", 0, -6)
  f.Error:SetPoint("RIGHT", f.Inset, "RIGHT", -10, 0)
  f.Hint = W.Label(f.Inset, L["Press Ctrl+C to copy. In Discord, paste inside ``` ; long codes arrive as message.txt, which works."],
    "GameFontNormalSmall")
  f.Hint:SetPoint("TOPLEFT", f.Error, "BOTTOMLEFT", 0, -10)
  f.Hint:SetPoint("RIGHT", f.Inset, "RIGHT", -10, 0)
  f.CloseButton2 = W.Button(f, CLOSE or L["Close"], 84, function() f:Hide() end, CLOSE or L["Close"])
  f.CloseButton2:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -8, 3)
  f.SelectButton = W.Button(f, L["Select all"], 96, function()
    f.Code:SetFocus()
    f.Code:GetEditBox():HighlightText()
  end, L["Select all"], L["Selects the whole code again, ready for Ctrl+C."])
  f.SelectButton:SetPoint("RIGHT", f.CloseButton2, "LEFT", -4, 0)
  return f
end

-- Opens the Share dialog for `ref` (default: the soundtrack selected on the Soundtracks tab, else
-- My Drops).
function W.OpenShare(ref)
  local model = ns.Soundtracks
  if not (ns.db and model and ns.Share) then
    if ns.Print then ns.Print(L["NeedleDropper is still loading - try again in a moment."]) end
    return false
  end
  ref = ref or (W.Soundtracks and W.Soundtracks.state.selected) or "user:my-drops"
  local set = model.Get(ref)
  if not set then
    if ns.Print then ns.Print(L["There is no such soundtrack to share."]) end
    return false
  end
  local f = buildShare()
  Sd.ref = ref
  f:SetTitle(W.Format(L["Share %s"], esc(set.name)))
  local u = W.UIDB() or {}
  local builtin = W.IsBuiltinRef(ref)
  local author = builtin and (set.author or "") or (nonEmpty(set.author) or u.author or "")
  f.Author:SetText(author)
  f.Author.ndValue = author
  W.SetEnabled(f.Author, not builtin, L["A built-in soundtrack keeps its own author."])
  if f:IsShown() or not W.InCombat() then
    f:Show()
    f:Raise()
  else
    W.OpenAfterCombat(f)          -- dialogs stay hidden in combat; this one opens when it ends
  end
  Sd.Generate()
  return true
end
