local ADDON, ns = ...
-- UI/Soundtracks.lua: the Soundtracks tab (DESIGN-v1.2 §4.5).
--
--   top bar  [x] Use different soundtracks on this character
--   left     one 44 px row per soundtrack: [On] name / "by <author> · 38 drops · Built-in", with
--            Up / Down while two or more are on (the top one wins a place), and the caption
--            "When two soundtracks have a song for the same place, the one higher in this list plays."
--   right    name, author, description, tags, homepage (text only), "Plays at 38 places; 3 are
--            covered by a soundtrack above"; built-ins are read-only
--   footer   [New] [Rename] [Duplicate] [Delete] [Share...] [Import...]
--
-- New / Rename / Delete are StaticPopupDialogs NEEDLEDROPPER_NEW / _RENAME / _DELETE. On 12.x the
-- handlers get (dialog, data) and use dialog:GetEditBox(), :GetEditBoxText() and :GetButton1();
-- there are no .editBox / .button1 fields (Blizzard_StaticPopup_Game/GameDialog.lua L574-L617,
-- Blizzard_StaticPopup/StaticPopup.lua L309 / L695). Delete uses button1 = DELETE, showAlert = 1.

local UI = ns.UI
local W = UI._W
local L = W.L
local esc, trim, nonEmpty = W.Esc, W.Trim, W.NonEmpty
local tremove, tinsert = table.remove, table.insert

local St = {}
W.Soundtracks = St

local ROW_EXTENT = 44
local LIST_W = 330

local state = { selected = nil }
St.state = state
local ui = {}

local function S() return ns.Soundtracks end

-- ---------------------------------------------------------------- popups --

local popupsReady = false

local function nameHandler(editBox)
  local dialog = editBox:GetParent()
  dialog:GetButton1():SetEnabled(trim(editBox:GetText()) ~= "")
end

local function enterClicks(editBox)
  local dialog = editBox:GetParent()
  local b = dialog:GetButton1()
  if b:IsEnabled() then b:Click() end
end

local function hideOnEscape(editBox)
  editBox:GetParent():Hide()
end

function St.EnsurePopups()
  if popupsReady then return end
  popupsReady = true
  StaticPopupDialogs["NEEDLEDROPPER_NEW"] = {
    text = L["Name the new soundtrack:"],
    button1 = OKAY or L["Okay"],
    button2 = CANCEL or L["Cancel"],
    hasEditBox = true,
    maxLetters = 64,
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    OnShow = function(dialog, data)
      local eb = dialog:GetEditBox()
      eb:SetText("")
      dialog:GetButton1():SetEnabled(false)
      eb:SetFocus()
    end,
    OnAccept = function(dialog, data)
      local name = trim(dialog:GetEditBoxText())
      local ref, err = S().Create(name)
      if not ref then
        if ns.Print then ns.Print(err or L["That soundtrack could not be created."]) end
        return true                 -- keep the dialog open to fix the name
      end
      if data and data.onCreated then data.onCreated(ref) else St.Select(ref) end
      W.RefreshAll()
    end,
    EditBoxOnTextChanged = nameHandler,
    EditBoxOnEnterPressed = enterClicks,
    EditBoxOnEscapePressed = hideOnEscape,
  }
  StaticPopupDialogs["NEEDLEDROPPER_RENAME"] = {
    text = L["Rename %s:"],
    button1 = OKAY or L["Okay"],
    button2 = CANCEL or L["Cancel"],
    hasEditBox = true,
    maxLetters = 64,
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    OnShow = function(dialog, data)
      local eb = dialog:GetEditBox()
      eb:SetText(data and data.name or "")
      eb:SetFocus()
      eb:HighlightText()
    end,
    OnAccept = function(dialog, data)
      local name = trim(dialog:GetEditBoxText())
      local ok, err = S().Rename(data.ref, name)
      if not ok then
        if ns.Print then ns.Print(err or L["That soundtrack could not be renamed."]) end
        return true
      end
      W.RefreshAll()
    end,
    EditBoxOnTextChanged = nameHandler,
    EditBoxOnEnterPressed = enterClicks,
    EditBoxOnEscapePressed = hideOnEscape,
  }
  StaticPopupDialogs["NEEDLEDROPPER_DELETE"] = {
    text = "%s",                  -- the whole question, worded for the drop count

    button1 = DELETE or L["Delete"],
    button2 = CANCEL or L["Cancel"],
    showAlert = 1,
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    OnAccept = function(dialog, data)
      local ok, err = S().Delete(data.ref)
      if not ok then
        if ns.Print then ns.Print(err or L["That soundtrack could not be deleted."]) end
        return
      end
      if state.selected == data.ref then state.selected = nil end
      if W.Drops and W.Drops.state.showing == data.ref then W.Drops.state.showing = "mix" end
      W.RefreshAll()
    end,
  }
end

local function showPopup(which, a1, a2, data)
  St.EnsurePopups()
  local dialog = StaticPopup_Show(which, a1, a2, data)
  if not dialog and ns.Print then ns.Print(L["Close the other dialog first, then try again."]) end
  return dialog
end

-- "New soundtrack..." (also from the Drops tab's Showing menu); onCreated(ref) after creation.
function W.ShowNewSoundtrack(onCreated)
  return showPopup("NEEDLEDROPPER_NEW", nil, nil, { onCreated = onCreated })
end

-- ------------------------------------------------------------------ rows --

local function activeCount()
  return #S().ActiveRefs()
end

-- Active soundtracks in play order first, then the ones that are off (List() order).
function St.Entries()
  local list = S().List()
  local on, off = {}, {}
  for _, info in ipairs(list) do
    if info.active then on[#on + 1] = info else off[#off + 1] = info end
  end
  table.sort(on, function(a, b) return (a.priority or 0) < (b.priority or 0) end)
  for _, info in ipairs(off) do on[#on + 1] = info end
  return on
end

local function infoLine(info)
  local parts = {}
  if nonEmpty(info.author) then parts[#parts + 1] = W.Format(L["by %s"], esc(info.author)) end
  if info.dropCount == 1 then
    parts[#parts + 1] = L["1 drop"]
  else
    parts[#parts + 1] = W.Format(L["%s drops"], tostring(info.dropCount or 0))
  end
  parts[#parts + 1] = info.builtin and L["Built-in"] or L["Yours"]
  if info.active then
    parts[#parts + 1] = W.Format(L["on, plays #%s"], tostring(info.priority or "?"))
  else
    parts[#parts + 1] = L["off"]
  end
  return table.concat(parts, W.DOT)
end

function St.ToggleOn(info, on)
  if not info then return end
  if on then
    W.TurnOn(info.ref)
  else
    local ok, err = W.TurnOff(info.ref)
    if not ok and err and ns.Print then ns.Print(err) end
  end
  W.RefreshAll()
end

function St.Move(info, delta)
  local refs = S().ActiveRefs()
  local i
  for k, ref in ipairs(refs) do if ref == info.ref then i = k end end
  if not i then return end
  local j = i + delta
  if j < 1 or j > #refs then return end
  refs[i], refs[j] = refs[j], refs[i]
  S().SetActive(refs)
  W.RefreshAll()
end

local function initRow(row, info)
  if not row.ndBuilt then
    row.ndBuilt = true
    row:RegisterForClicks("LeftButtonUp")
    row.Selected = row:CreateTexture(nil, "BACKGROUND")
    row.Selected:SetAllPoints(row)
    row.Selected:SetColorTexture(1, 0.82, 0, 0.18)
    row.Hover = row:CreateTexture(nil, "HIGHLIGHT")
    row.Hover:SetAllPoints(row)
    row.Hover:SetColorTexture(1, 1, 1, 0.08)
    row.On = W.Check(row, nil, function(self) St.ToggleOn(self:GetParent().ndInfo, self:GetChecked()) end)
    row.On:SetPoint("LEFT", row, "LEFT", 4, 0)
    W.SetTooltip(row.On, function(tt, self)
      local i = self:GetParent().ndInfo
      GameTooltip_SetTitle(tt, i and i.active and L["On"] or L["Off"])
      GameTooltip_AddNormalLine(tt, L["Turn this soundtrack on or off. Soundtracks that are on play their songs; the one higher in the list wins a place."], true)
    end)
    row.Name = W.Label(row, nil, "GameFontNormal")
    row.Name:SetPoint("TOPLEFT", row, "TOPLEFT", 34, -7)
    row.Name:SetPoint("RIGHT", row, "RIGHT", -48, 0)
    row.Name:SetWordWrap(false)
    row.Info = W.Label(row, nil, "GameFontHighlightSmall")
    row.Info:SetPoint("TOPLEFT", row.Name, "BOTTOMLEFT", 0, -5)
    row.Info:SetPoint("RIGHT", row, "RIGHT", -48, 0)
    row.Info:SetWordWrap(false)
    row.Up = W.IconButton(row, 18, "Interface\\Buttons\\UI-ScrollBar-ScrollUpButton-Up",
      function(self) St.Move(self:GetParent().ndInfo, -1) end, L["Move up"],
      L["Plays before the soundtracks below it where both have a song."])
    row.Up:SetDisabledTexture("Interface\\Buttons\\UI-ScrollBar-ScrollUpButton-Disabled")
    row.Up:SetPoint("TOPRIGHT", row, "TOPRIGHT", -8, -3)
    row.Down = W.IconButton(row, 18, "Interface\\Buttons\\UI-ScrollBar-ScrollDownButton-Up",
      function(self) St.Move(self:GetParent().ndInfo, 1) end, L["Move down"],
      L["Lets the soundtracks above it win where both have a song."])
    row.Down:SetDisabledTexture("Interface\\Buttons\\UI-ScrollBar-ScrollDownButton-Disabled")
    row.Down:SetPoint("BOTTOMRIGHT", row, "BOTTOMRIGHT", -8, 3)
    row:SetScript("OnClick", function(self) St.Select(self.ndInfo.ref) end)
  end
  row.ndInfo = info
  local count = activeCount()
  row.On:SetChecked(info.active)
  W.SetEnabled(row.On, not (info.active and count <= 1),
    L["At least one soundtrack stays on. Turn needle drops off in Settings instead."])
  row.Name:SetText(esc(info.name))
  row.Info:SetText(infoLine(info))
  local movable = info.active and count >= 2
  row.Up:SetShown(movable)
  row.Down:SetShown(movable)
  W.SetEnabled(row.Up, (info.priority or 1) > 1, L["Already at the top."])
  W.SetEnabled(row.Down, (info.priority or count) < count, L["Already the last soundtrack that is on."])
  row.Selected:SetShown(state.selected == info.ref)
end

-- --------------------------------------------------------------- details --

local function statsFor(info)
  local model = S()
  local set = model.Get(info.ref)
  if not set then return "" end
  local total, covered, drafts = 0, 0, 0
  local merged = W.MergedIndex()
  for _, d in ipairs(set.drops) do
    if W.IsDraft(d) then
      drafts = drafts + 1
    elseif not d.disabled then
      total = total + 1
      local e = merged[d.id]
      if e and e.setRef ~= info.ref then covered = covered + 1 end
    end
  end
  local lines = {}
  if info.active then
    if covered == 1 then
      lines[#lines + 1] = W.Format(L["Plays at %s places; 1 is covered by a soundtrack above."], tostring(total))
    else
      lines[#lines + 1] = W.Format(L["Plays at %s places; %s are covered by a soundtrack above."], tostring(total), tostring(covered))
    end
  else
    lines[#lines + 1] = W.Format(L["Off: turn it on to hear its %s places."], tostring(total))
  end
  if drafts > 0 then
    lines[#lines + 1] = W.Format(L["%s unfinished drops (no artist or title yet) do not play."], tostring(drafts))
  end
  return table.concat(lines, "\n")
end

function St.RefreshDetails()
  if not ui.details then return end
  local model = S()
  local info
  for _, i in ipairs(model.List()) do if i.ref == state.selected then info = i end end
  local d = ui.details
  if not info then
    d.Name:SetText(L["Pick a soundtrack on the left."])
    d.Author:SetText("")
    d.Description:SetText("")
    d.Tags:SetText("")
    d.Homepage:SetText("")
    d.Stats:SetText("")
    d.ReadOnly:SetText("")
    St.RefreshButtons(nil)
    return
  end
  local set = model.Get(info.ref) or {}
  d.Name:SetText(esc(info.name))
  d.Author:SetText(nonEmpty(info.author) and W.Format(L["by %s"], esc(info.author)) or L["no author given"])
  d.Description:SetText(esc(info.description or ""))
  local tags = {}
  for _, t in ipairs(info.tags or {}) do tags[#tags + 1] = esc(t) end
  d.Tags:SetText(#tags > 0 and W.Format(L["Tags: %s"], table.concat(tags, ", ")) or "")
  if nonEmpty(set.homepage) then
    d.Homepage:SetText(W.Format(L["Homepage: %s (copy it into your browser; the game cannot open links)"], esc(set.homepage)))
  else
    d.Homepage:SetText("")
  end
  d.Stats:SetText(statsFor(info))
  d.ReadOnly:SetText(info.builtin and L["Built-in soundtracks are read-only. Duplicate makes your own copy; Change song here on the Drops tab changes one place."] or "")
  St.RefreshButtons(info)
end

function St.RefreshButtons(info)
  local b = ui.buttons
  if not b then return end
  local none = L["Pick a soundtrack first."]
  if not info then
    for _, key in ipairs({ "Rename", "Delete", "Duplicate", "Share" }) do W.SetEnabled(b[key], false, none) end
    return
  end
  W.SetEnabled(b.Rename, not info.builtin, L["Built-in soundtracks can't be renamed: duplicate it first."])
  W.SetEnabled(b.Delete, not info.builtin, L["Built-in soundtracks can't be deleted. Turn it off instead."])
  W.SetEnabled(b.Duplicate, true)
  local set = S().Get(info.ref)
  local finished = 0
  for _, d in ipairs(set and set.drops or {}) do
    if not W.IsDraft(d) then finished = finished + 1 end
  end
  W.SetEnabled(b.Share, finished > 0, L["This soundtrack has no finished drops to share yet."])
end

-- ------------------------------------------------------------------ tab --

function St.Select(ref)
  state.selected = ref
  St.Refresh()
end

function St.Refresh()
  if not ui.list then return end
  local model = S()
  if state.selected and not model.Exists(state.selected) then state.selected = nil end
  local entries = St.Entries()
  if not state.selected and entries[1] then state.selected = entries[1].ref end
  ui.list:SetDataProvider(CreateDataProvider(entries), ScrollBoxConstants.RetainScrollPosition)
  ui.perChar:SetChecked(model.UsesCharacterList())
  St.RefreshDetails()
end

local function selectedInfo()
  for _, i in ipairs(S().List()) do
    if i.ref == state.selected then return i end
  end
  return nil
end

local function build(panel, attic, buttons)
  ui.perChar = W.Check(attic, L["Use different soundtracks on this character"], function(self)
    S().SetUseCharacterList(self:GetChecked() and true or false)
    W.RefreshAll()
  end)
  ui.perChar:SetPoint("LEFT", attic, "LEFT", 0, 0)
  W.SetTooltip(ui.perChar, W.SimpleTooltip(L["Use different soundtracks on this character"],
    L["This character gets its own on/off list and order, starting as a copy of the account's. Untick it to go back to the account's list."]))
  attic.PerCharacter = ui.perChar

  local list = CreateFrame("Frame", nil, panel, "WowScrollBoxList")
  list:SetPoint("TOPLEFT", panel, "TOPLEFT", 4, -4)
  list:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", 4, 40)
  list:SetWidth(LIST_W - 16)
  local bar = CreateFrame("EventFrame", nil, panel, "MinimalScrollBar")
  bar:SetPoint("TOPLEFT", list, "TOPRIGHT", 4, 0)
  bar:SetPoint("BOTTOMLEFT", list, "BOTTOMRIGHT", 4, 0)
  bar:SetHideIfUnscrollable(true)
  local view = CreateScrollBoxListLinearView(2, 2, 2, 2, 2)
  view:SetElementExtent(ROW_EXTENT)
  view:SetElementInitializer("Button", initRow)
  ScrollUtil.InitScrollBoxListWithScrollBar(list, bar, view)
  ui.list = list
  panel.List, panel.ListView, panel.ListBar = list, view, bar

  local caption = W.Label(panel, L["When two soundtracks have a song for the same place, the one higher in this list plays."],
    "GameFontDisableSmall")
  caption:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", 8, 8)
  caption:SetWidth(LIST_W - 12)
  panel.Caption = caption

  local d = CreateFrame("Frame", nil, panel)
  d:SetPoint("TOPLEFT", panel, "TOPLEFT", LIST_W + 12, -10)
  d:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -10, 10)
  d.Name = W.Label(d, nil, "GameFontNormalLarge")
  d.Name:SetPoint("TOPLEFT", d, "TOPLEFT", 0, 0)
  d.Name:SetPoint("RIGHT", d, "RIGHT", 0, 0)
  d.Author = W.Label(d, nil, "GameFontHighlightSmall")
  d.Author:SetPoint("TOPLEFT", d.Name, "BOTTOMLEFT", 0, -6)
  d.Description = W.Label(d, nil, "GameFontHighlightSmall")
  d.Description:SetPoint("TOPLEFT", d.Author, "BOTTOMLEFT", 0, -10)
  d.Description:SetPoint("RIGHT", d, "RIGHT", 0, 0)
  d.Tags = W.Label(d, nil, "GameFontHighlightSmall")
  d.Tags:SetPoint("TOPLEFT", d.Description, "BOTTOMLEFT", 0, -10)
  d.Tags:SetPoint("RIGHT", d, "RIGHT", 0, 0)
  d.Homepage = W.Label(d, nil, "GameFontHighlightSmall")
  d.Homepage:SetPoint("TOPLEFT", d.Tags, "BOTTOMLEFT", 0, -6)
  d.Homepage:SetPoint("RIGHT", d, "RIGHT", 0, 0)
  d.Stats = W.Label(d, nil, "GameFontNormal")
  d.Stats:SetPoint("TOPLEFT", d.Homepage, "BOTTOMLEFT", 0, -14)
  d.Stats:SetPoint("RIGHT", d, "RIGHT", 0, 0)
  d.ReadOnly = W.Label(d, nil, "GameFontDisableSmall")
  d.ReadOnly:SetPoint("TOPLEFT", d.Stats, "BOTTOMLEFT", 0, -14)
  d.ReadOnly:SetPoint("RIGHT", d, "RIGHT", 0, 0)
  ui.details = d
  panel.Details = d

  -- Footer, right to left.
  local b = {}
  b.Import = W.Button(buttons, L["Import..."], 74, function() UI.OpenImport() end,
    L["Import..."], L["Paste a share code to add someone's soundtrack. It arrives turned off."])
  b.Import:SetPoint("RIGHT", buttons, "RIGHT", 0, 0)
  b.Share = W.Button(buttons, L["Share..."], 70, function() UI.OpenShare(state.selected) end,
    L["Share..."], L["Makes a share code for this soundtrack to paste in Discord or a forum."])
  b.Share:SetPoint("RIGHT", b.Import, "LEFT", -4, 0)
  b.Delete = W.Button(buttons, DELETE or L["Delete"], 66, function()
    local info = selectedInfo()
    if info and not info.builtin then
      local n = info.dropCount or 0
      local question
      if n == 1 then
        question = W.Format(L["Delete %s and its 1 drop? This cannot be undone."], esc(info.name))
      else
        question = W.Format(L["Delete %s and its %s drops? This cannot be undone."], esc(info.name), tostring(n))
      end
      showPopup("NEEDLEDROPPER_DELETE", question, nil, { ref = info.ref, name = info.name })
    end
  end, DELETE or L["Delete"], L["Deletes this soundtrack and its drops. You are asked first."])
  b.Delete:SetPoint("RIGHT", b.Share, "LEFT", -4, 0)
  b.Duplicate = W.Button(buttons, L["Duplicate"], 84, function()
    local info = selectedInfo()
    if not info then return end
    local ref, err = S().Duplicate(info.ref)
    if not ref then
      if ns.Print then ns.Print(err or L["That soundtrack could not be duplicated."]) end
      return
    end
    state.selected = ref
    W.RefreshAll()
  end, L["Duplicate"], L["Makes your own copy you can change. A built-in becomes \"<name> (my copy)\"."])
  b.Duplicate:SetPoint("RIGHT", b.Delete, "LEFT", -4, 0)
  b.Rename = W.Button(buttons, L["Rename"], 70, function()
    local info = selectedInfo()
    if info and not info.builtin then
      showPopup("NEEDLEDROPPER_RENAME", esc(info.name), nil, { ref = info.ref, name = info.name })
    end
  end, L["Rename"], L["Gives this soundtrack a new name."])
  b.Rename:SetPoint("RIGHT", b.Duplicate, "LEFT", -4, 0)
  b.New = W.Button(buttons, L["New"], 58, function()
    W.ShowNewSoundtrack(function(ref)
      state.selected = ref
      W.RefreshAll()
    end)
  end, L["New"], L["Starts an empty soundtrack of your own."])
  b.New:SetPoint("RIGHT", b.Rename, "LEFT", -4, 0)
  buttons:SetWidth(74 + 70 + 66 + 84 + 70 + 58 + 5 * 4)
  ui.buttons = b
  for k, v in pairs(b) do buttons[k] = v end
end

W.RegisterTab({
  key = "soundtracks",
  title = L["Soundtracks"],
  Build = build,
  Refresh = function() St.Refresh() end,
  OnShow = function() St.Refresh() end,
})
