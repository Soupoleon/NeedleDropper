local ADDON, ns = ...
-- UI/Window.lua: the NeedleDropper window shell and the public ns.UI API (DESIGN-v1.2 §4.3,
-- CONTRACTS 2.9). One ButtonFrameTemplate window with three PanelTabButtonTemplate tabs
-- (Drops | Soundtracks | Journal: UI\Drops.lua, UI\Soundtracks.lua, UI\Journal.lua), a gear in the
-- title bar for the Settings panel, a resize grip, and a footer: a status line on the left and
-- the current tab's buttons on the right. Nothing here creates a frame at file load; the window
-- is built the first time it opens.
--
-- Checked on Gethe/wow-ui-source, branch forever (1.60.1.70790, @eac30af):
--   ButtonFrameTemplate, .CloseButton, .Inset, TitleContainer   Blizzard_SharedXML/Mainline/SharedUIPanelTemplates.xml L566-L721
--   ButtonFrameTemplate_HidePortrait                           Blizzard_SharedXML/Mainline/SharedUIPanelTemplates.lua L111
--   SetTitle (TitledPanelMixin, via PortraitFrameMixin)         Blizzard_SharedXML/PortraitFrame.lua L11, L32
--   CloseButton -> UIPanelCloseButton_OnClick -> HideUIPanel    SharedUIPanelTemplates.xml L148, .lua L152; blocked in
--                                                              combat: Blizzard_UIParentPanelManager/Shared/UIParentPanelManager.lua L853-L861
--   UISpecialFrames / CloseSpecialWindows                       UIParentPanelManager.lua L1104-L1116
--   PanelTabButtonTemplate (no OnClick, parentArray "Tabs")     SharedUIPanelTemplates.xml L932
--   PanelTemplates_SetTab / PanelTemplates_SetNumTabs           SharedUIPanelTemplates.lua L441 / L542
--   PanelResizeButtonTemplate, PanelResizeButtonMixin:Init      SharedUIPanelTemplates.xml L1642, .lua L1637
--   UIPanelButtonTemplate (+ DisabledTooltipButtonMixin)        SharedUIPanelTemplates.xml L315, Blizzard_SharedXML/SharedTooltipTemplates.lua L320
--   GameTooltip_SetTitle / AddNormalLine / AddInstructionLine   Blizzard_SharedXML/SharedTooltipTemplates.lua L128-L166
--   SOUNDKIT.IG_CHARACTER_INFO_TAB (841)                        Blizzard_SharedXML/Mainline/SoundKitConstants.lua L38
--   gear texture Interface\WorldMap\GEAR_64GREY                 used by Blizzard_Menu/MenuVariants.lua L8 on forever

local UI = ns.UI or {}
ns.UI = UI
local W = UI._W or {}
UI._W = W          -- helpers shared by the UI\*.lua files; not part of the public API

local tinsert, tremove = table.insert, table.remove
local floor = math.floor

-- Localised text: ns.L (Locale.lua) when it has the key, else the English key itself.
local L = setmetatable({}, { __index = function(_, key)
  local t = ns.L
  local v = t and t[key]
  if type(v) ~= "string" or v == "" then v = key end
  return v
end })
W.L = L

W.DEFAULT_W, W.DEFAULT_H = 700, 500
W.MIN_W, W.MIN_H, W.MAX_W, W.MAX_H = 620, 420, 1100, 800
W.SAVE_HINT_EDITS = 10
W.DOT = " \194\183 "                                     -- " · " (U+00B7, in every game font)
W.CHECK = "|TInterface\\RaidFrame\\ReadyCheck-Ready:0|t"  -- check mark texture (Blizzard_Calendar uses it)

-- ------------------------------------------------------------- text helpers --

-- "|" starts a UI escape sequence in a FontString; text from players or imports is escaped
-- (ns.EscapeText from Locale.lua, or the same rule here when it is missing).
local function esc(s)
  if s == nil then return "" end
  if type(s) ~= "string" then s = tostring(s) end
  if ns.EscapeText then return ns.EscapeText(s) end
  return (string.gsub(s, "|", "||"))
end
W.Esc = esc

-- Trims ASCII white space only (%s follows the C locale and would cut UTF-8 bytes).
local function trim(s)
  if type(s) ~= "string" then return "" end
  return (string.gsub(string.gsub(s, "^[ \t\r\n]+", ""), "[ \t\r\n]+$", ""))
end
W.Trim = trim

local function nonEmpty(s)
  if type(s) == "string" and s ~= "" then return s end
  return nil
end
W.NonEmpty = nonEmpty

-- string.format that never raises on a bad translation.
function W.Format(fmt, ...)
  if ns.Format then return ns.Format(fmt, ...) end
  return string.format(fmt, ...)
end

function W.IsDraft(drop)
  if type(drop) ~= "table" or drop.disabled then return false end
  return not (nonEmpty(drop.artist) and nonEmpty(drop.title))
end

-- "Artist · Title" (escaped), or nil for a draft / turned-off place.
function W.SongText(drop)
  if type(drop) ~= "table" then return nil end
  local a, t = nonEmpty(drop.artist), nonEmpty(drop.title)
  if a and t then return esc(a) .. W.DOT .. esc(t) end
  if t then return esc(t) end
  if a then return esc(a) end
  return nil
end

local ID_KIND = { zone = "zone", area = "subzone", sub = "subzone", inst = "instance" }

function W.KindOf(drop)
  if type(drop) ~= "table" then return "zone" end
  if drop.kind == "zone" or drop.kind == "subzone" or drop.kind == "instance" then return drop.kind end
  local prefix = type(drop.id) == "string" and string.match(drop.id, "^(%a+):") or nil
  if prefix and ID_KIND[prefix] then return ID_KIND[prefix] end
  if drop.instanceID then return "instance" end
  if drop.areaID or drop.subzone then return "subzone" end
  return "zone"
end

-- Client-localised zone name for a uiMapID (nil if the client does not know it).
function W.ZoneName(mapID)
  if type(mapID) ~= "number" or not (C_Map and C_Map.GetMapInfo) then return nil end
  local info = C_Map.GetMapInfo(mapID)
  if type(info) ~= "table" then return nil end
  local name = ns.SafeString and ns.SafeString(info.name) or info.name
  return nonEmpty(name)
end

function W.AreaName(areaID)
  if type(areaID) ~= "number" or not ns.AreaName then return nil end
  return nonEmpty(ns.AreaName(areaID))
end

-- zone, landmark (unescaped): client names first, the drop's own label as the fallback.
function W.PlaceNames(drop)
  local kind = W.KindOf(drop)
  if kind == "instance" then
    return nonEmpty(drop.label) or W.Format(L["Instance %s"], tostring(drop.instanceID or "?")), nil
  end
  local zone = W.ZoneName(drop.mapID)
  if kind == "subzone" then
    local lm = W.AreaName(drop.areaID) or nonEmpty(drop.subzone) or nonEmpty(drop.label)
    return zone, lm
  end
  return zone or nonEmpty(drop.label) or (drop.mapID and W.Format(L["Zone %s"], tostring(drop.mapID))) or tostring(drop.id), nil
end

-- "Elwynn Forest > Goldshire" (escaped): zone first, as the list is sorted.
function W.PlaceLine(drop)
  if type(drop) ~= "table" then return "" end
  local zone, lm = W.PlaceNames(drop)
  if lm and zone then return esc(zone) .. " > " .. esc(lm) end
  return esc(lm or zone or drop.id)
end

-- "Goldshire" (escaped): the most specific name.
function W.PlaceShort(drop)
  if type(drop) ~= "table" then return "" end
  local zone, lm = W.PlaceNames(drop)
  return esc(lm or zone or drop.id)
end

-- "S Y F": Spotify, YouTube, in-game File (letters, not colour).
function W.Sources(drop)
  if type(drop) ~= "table" then return "" end
  local parts = {}
  if nonEmpty(drop.spotify) then parts[#parts + 1] = L["S"] end
  if nonEmpty(drop.youtube) then parts[#parts + 1] = L["Y"] end
  if nonEmpty(drop.file) then parts[#parts + 1] = L["F"] end
  return table.concat(parts, " ")
end

function W.SourceNames(drop)
  local parts = {}
  if nonEmpty(drop.spotify) then parts[#parts + 1] = L["Spotify"] end
  if nonEmpty(drop.youtube) then parts[#parts + 1] = L["YouTube"] end
  if nonEmpty(drop.file) then parts[#parts + 1] = L["in-game file"] end
  return table.concat(parts, W.DOT)
end

-- 83000 -> "1:23"; 3723000 -> "1:02:03".
function W.FormatTime(ms)
  if type(ms) ~= "number" or ms < 0 then return "" end
  local sec = floor(ms / 1000 + 0.5)
  local h = floor(sec / 3600)
  local m = floor((sec % 3600) / 60)
  local s = sec % 60
  if h > 0 then return string.format("%d:%02d:%02d", h, m, s) end
  return string.format("%d:%02d", m, s)
end

-- "1:23" / "83" / "1:02:03" -> milliseconds; nil when it is not a time.
function W.ParseTime(text)
  text = trim(text)
  if text == "" then return nil end
  local h, m, s = string.match(text, "^(%d+):(%d%d?):(%d%d?)$")
  if h then return ((tonumber(h) * 60 + tonumber(m)) * 60 + tonumber(s)) * 1000 end
  m, s = string.match(text, "^(%d+):(%d%d?)$")
  if m then return (tonumber(m) * 60 + tonumber(s)) * 1000 end
  s = string.match(text, "^(%d+)$")
  if s then return tonumber(s) * 1000 end
  return nil
end

function W.FormatDate(t)
  if type(t) ~= "number" or t <= 0 or not date then return "?" end
  return date("%Y-%m-%d %H:%M", t)
end

local LANGUAGES = {
  enUS = "English", enGB = "English", deDE = "German", frFR = "French", esES = "Spanish", esMX = "Spanish",
  itIT = "Italian", ptBR = "Portuguese", ruRU = "Russian", koKR = "Korean", zhCN = "Simplified Chinese",
  zhTW = "Traditional Chinese",
}
function W.ClientLanguage()
  local loc = GetLocale and GetLocale() or "enUS"
  return L[LANGUAGES[loc] or loc]
end

W.MEDIA = "Interface\\AddOns\\NeedleDropper_Local\\media\\"

-- A copy of a stored drop shaped like a map drop for Director.PlayTest: `file` becomes the full
-- in-game path (the Director plays it as is) and `fileName` keeps the bare name.
function W.Playable(drop)
  local out = {}
  for k, v in pairs(drop) do out[k] = v end
  local f = nonEmpty(out.file)
  if f and not string.find(f, "\\", 1, true) then
    out.fileName = f
    out.file = W.MEDIA .. f
  end
  return out
end

-- ------------------------------------------------------------- model access --

function W.Model() return ns.Soundtracks end

function W.RefKind(ref)
  if type(ref) ~= "string" then return nil end
  return (string.match(ref, "^(%a+):"))
end

function W.IsBuiltinRef(ref) return W.RefKind(ref) == "builtin" end

-- Display name of a soundtrack (from List(): Get() copies a built-in's every drop).
function W.SetName(ref)
  local S = ns.Soundtracks
  if S and ref then
    for _, info in ipairs(S.List()) do
      if info.ref == ref then return info.name end
    end
  end
  return tostring(ref)
end

-- What claims each place in the active mix, from Soundtracks.Merged(): the first entry per
-- drop id that is not a draft and that no soundtrack above overrides (it may be turned off).
-- Returns byId (id -> entry) and the raw list. Works whether Merged() lists only the winners
-- or every entry with overriddenBy = the soundtracks above it.
function W.MergedIndex()
  local list = ns.Soundtracks.Merged()
  local byId = {}
  for _, e in ipairs(list) do
    local d = e.drop
    if type(d) == "table" and d.id and not byId[d.id] and not (e.draft or W.IsDraft(d))
       and not (type(e.overriddenBy) == "table" and #e.overriddenBy > 0) then
      byId[d.id] = e
    end
  end
  return byId, list
end

-- The set ref for a set id (journal entries and map drops carry ids).
function W.RefForSetId(setId)
  local S = ns.Soundtracks
  if not (S and setId) then return nil end
  if S.Exists("user:" .. setId) then return "user:" .. setId end
  if S.Exists("builtin:" .. setId) then return "builtin:" .. setId end
  return nil
end

-- Turns a soundtrack on: it goes to the top of the list, below My Drops when My Drops is first.
function W.TurnOn(ref)
  local S = ns.Soundtracks
  local refs = S.ActiveRefs()
  for i = #refs, 1, -1 do if refs[i] == ref then tremove(refs, i) end end
  local pos = 1
  if refs[1] == "user:my-drops" and ref ~= "user:my-drops" then pos = 2 end
  tinsert(refs, pos, ref)
  return S.SetActive(refs)
end

function W.TurnOff(ref)
  local S = ns.Soundtracks
  local refs = S.ActiveRefs()
  for i = #refs, 1, -1 do if refs[i] == ref then tremove(refs, i) end end
  if #refs == 0 then return nil, L["At least one soundtrack stays on. Turn needle drops off in Settings instead."] end
  return S.SetActive(refs)
end

-- The personal top layer every "Add drop here" goes to: created and put first when needed.
-- The model does it (Soundtracks.EnsureMyDrops): it picks a free name, so an imported soundtrack
-- that is also called "My Drops" (a friend's shared code) never blocks Add drop here.
function W.EnsureMyDrops()
  local S = ns.Soundtracks
  if type(S.EnsureMyDrops) == "function" then return S.EnsureMyDrops() end
  local ref = "user:my-drops"
  if not S.Exists(ref) then
    -- Created under its canonical name so its id is "my-drops" (the one Change song here and
    -- Turn off here use too), then shown in the client's language.
    local newRef, err = S.Create("My Drops")
    if not newRef then return nil, err end
    ref = newRef
    if L["My Drops"] ~= "My Drops" then S.Rename(ref, L["My Drops"]) end
  end
  local refs = S.ActiveRefs()
  if refs[1] ~= ref then
    for i = #refs, 1, -1 do if refs[i] == ref then tremove(refs, i) end end
    tinsert(refs, 1, ref)
    S.SetActive(refs)
  end
  return ref
end

-- ------------------------------------------------------------ widget kit --

function W.Label(parent, text, font, layer)
  local fs = parent:CreateFontString(nil, layer or "ARTWORK", font or "GameFontNormal")
  fs:SetJustifyH("LEFT")
  if text then fs:SetText(text) end
  return fs
end

-- Shows `fill(tooltip, frame)` on hover; a disabled frame also shows why (DESIGN §4.10).
function W.SetTooltip(frame, fill)
  frame.ndTooltip = fill
  frame:SetScript("OnEnter", function(self)
    GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
    self.ndTooltip(GameTooltip, self)
    if self.IsEnabled and not self:IsEnabled() and self.ndDisabledReason then
      GameTooltip_AddErrorLine(GameTooltip, self.ndDisabledReason, true)
    end
    GameTooltip:Show()
  end)
  frame:SetScript("OnLeave", function() GameTooltip:Hide() end)
  if frame.SetMotionScriptsWhileDisabled then frame:SetMotionScriptsWhileDisabled(true) end
end

function W.SimpleTooltip(title, text)
  return function(tt)
    GameTooltip_SetTitle(tt, title)
    if text then GameTooltip_AddNormalLine(tt, text, true) end
  end
end

-- Enables / disables with the reason the tooltip gives.
function W.SetEnabled(widget, enabled, reason)
  widget:SetEnabled(enabled and true or false)
  widget.ndDisabledReason = (not enabled) and reason or nil
end

function W.Button(parent, text, width, onClick, tipTitle, tipText)
  local b = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
  b:SetSize(width, 22)
  b:SetText(text)
  b:SetScript("OnClick", onClick)
  W.SetTooltip(b, W.SimpleTooltip(tipTitle or text, tipText))
  return b
end

-- A small textured button (gear, arrows); every one has a tooltip.
function W.IconButton(parent, size, texture, onClick, tipTitle, tipText)
  local b = CreateFrame("Button", nil, parent)
  b:SetSize(size, size)
  b:SetNormalTexture(texture)
  b:SetPushedTexture(texture)
  b:SetHighlightTexture(texture, "ADD")
  b:SetScript("OnClick", onClick)
  W.SetTooltip(b, W.SimpleTooltip(tipTitle, tipText))
  return b
end

-- InputBoxInstructionsTemplate with autofocus off (UI.xsd default is on).
function W.Input(parent, instructions, maxBytes)
  local e = CreateFrame("EditBox", nil, parent, "InputBoxInstructionsTemplate")
  e:SetAutoFocus(false)
  e:SetHeight(20)
  if instructions then e.Instructions:SetText(instructions) end
  if maxBytes then e:SetMaxBytes(maxBytes) end
  return e
end

function W.Check(parent, text, onClick)
  local c = CreateFrame("CheckButton", nil, parent, "UICheckButtonTemplate")
  c:SetSize(24, 24)
  c.Text:SetFontObject("GameFontHighlightSmall")
  c.Text:SetText(text)
  c:SetScript("OnClick", onClick)
  return c
end

function W.Radio(parent, text, onClick)
  local r = CreateFrame("CheckButton", nil, parent, "UIRadioButtonTemplate")
  r.text:SetFontObject("GameFontHighlightSmall")    -- lowercase .text on this template
  r.text:SetText(text)
  r:SetScript("OnClick", onClick)
  return r
end

-- Sets an edit box's text from the model without clobbering what the player is typing.
function W.SetBoxText(box, text)
  text = text or ""
  box.ndValue = text
  if box:HasFocus() then return end
  if box:GetText() ~= text then box:SetText(text) end
end

-- --------------------------------------------------------------- the window --

local frame
local tabs = {}
local current
local ticker
local editsSinceLogin, countEdits = 0, false
local managed = {}       -- top-level frames that hide in combat and come back afterwards
local dirty, flushPending = {}, false

-- Registers a tab: { key, title, Build(panel, attic, buttons), Refresh(), OnShow(opened), OnHide() }.
function W.RegisterTab(def)
  tabs[#tabs + 1] = def
  def.index = #tabs
end

function W.Frame() return frame end
function W.Tabs() return tabs end
function W.CurrentTab() return current and tabs[current] or nil end

local function uiDB()
  local db = ns.db
  if not db then return nil end
  if type(db.ui) ~= "table" then db.ui = {} end
  return db.ui
end
W.UIDB = uiDB

local function tabIndex(key)
  if type(key) == "number" and tabs[key] then return key end
  for i = 1, #tabs do
    if tabs[i].key == key then return i end
  end
  return nil
end

local function savePosition()
  local u = uiDB()
  if not (u and frame) then return end
  local point, _, relPoint, x, y = frame:GetPoint(1)
  if point then u.point = { point, relPoint or point, floor((x or 0) + 0.5), floor((y or 0) + 0.5) } end
end

local function saveSize()
  local u = uiDB()
  if not (u and frame) then return end
  u.w = floor(frame:GetWidth() + 0.5)
  u.h = floor(frame:GetHeight() + 0.5)
end

local function clamp(v, lo, hi, default)
  if type(v) ~= "number" or v ~= v then return default end
  if v < lo then return lo end
  if v > hi then return hi end
  return v
end

local VALID_POINT = { TOPLEFT = true, TOP = true, TOPRIGHT = true, LEFT = true, CENTER = true, RIGHT = true,
  BOTTOMLEFT = true, BOTTOM = true, BOTTOMRIGHT = true }

local function restoreLayout()
  local u = uiDB() or {}
  frame:SetSize(clamp(u.w, W.MIN_W, W.MAX_W, W.DEFAULT_W), clamp(u.h, W.MIN_H, W.MAX_H, W.DEFAULT_H))
  frame:ClearAllPoints()
  local p = u.point
  if type(p) == "table" and VALID_POINT[p[1]] and VALID_POINT[p[2]] and type(p[3]) == "number" and type(p[4]) == "number" then
    frame:SetPoint(p[1], UIParent, p[2], p[3], p[4])
  else
    frame:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
  end
end

-- ---------------------------------------------------------------- gamepad --
-- Forever's gamepad UI (real-client finding 7): Blizzard_GamepadSmartNavigation moves its cursor
-- over the Buttons and EditBoxes of the frames GamepadMode.FrameControlsManager knows. A frame
-- joins by calling FrameControlsManager:FrameShown(frame) in OnShow and :FrameHidden(frame) in
-- OnHide while InputUtil.IsGamepadUIEnabled() (the comment at the top of
-- Blizzard_GamepadSharedUtility/FrameControlsManager.lua; e.g. Blizzard_Channels
-- CreateChannelPopup.lua and ColorPickerFrame.lua). Both are SecureHelper:WrapFunction'ed (FrameControlsManager.lua
-- L464 / L561) so insecure callers may use them, but only outside the combat restriction
-- (SecureHelper.IsSecureOrOutOfCombatRestriction): an insecure call in combat would print
-- "Interface action failed". So the calls are skipped in combat and made up for when it ends.
-- SmartNavigation's B button clicks the active frame's CloseButton (SmartNavigation.lua
-- AttemptClose): all three frames have one. StaticPopups and Blizzard_Menu menus are tracked by
-- Blizzard itself (StaticPopupGamepad.lua, the MenuProxy.OnShow callback). Edit boxes are never
-- focused programmatically while the gamepad UI is on (W.FocusBox), so nothing grabs the
-- controller's input until the player picks a box.

local gamepadRegistered = setmetatable({}, { __mode = "k" })

function W.GamepadUI()
  if type(InputUtil) ~= "table" or type(InputUtil.IsGamepadUIEnabled) ~= "function" then return false end
  local ok, on = pcall(InputUtil.IsGamepadUIEnabled)
  return ok and on == true
end

local function combatLocked()
  if type(InCombatLockdown) == "function" then
    local ok, v = pcall(InCombatLockdown)
    if ok and v then return true end
  end
  local kinds = Enum and Enum.AddOnRestrictionType
  if C_RestrictedActions and C_RestrictedActions.IsAddOnRestrictionActive and kinds and kinds.Combat ~= nil then
    local ok, v = pcall(C_RestrictedActions.IsAddOnRestrictionActive, kinds.Combat)
    if ok and v == true then return true end
  end
  return false
end
W.CombatLocked = combatLocked

local function frameControls()
  local FCM = type(GamepadMode) == "table" and GamepadMode.FrameControlsManager or nil
  if type(FCM) == "table" and type(FCM.FrameShown) == "function" and type(FCM.FrameHidden) == "function" then
    return FCM
  end
  return nil
end

local gamepadStale = setmetatable({}, { __mode = "k" })   -- hidden in combat: FrameHidden still owed

local function tellHidden(f)
  gamepadRegistered[f], gamepadStale[f] = nil, nil
  return (pcall(GamepadMode.FrameControlsManager.FrameHidden, GamepadMode.FrameControlsManager, f))
end

-- Tells the gamepad UI that `f` is shown (true when it was told). A frame hidden during combat is
-- first reported hidden, so the gamepad UI picks it up afresh.
function W.GamepadShown(f)
  if not W.GamepadUI() or combatLocked() or not frameControls() then return false end
  if gamepadStale[f] then tellHidden(f) end
  if gamepadRegistered[f] then return false end
  local ok = pcall(GamepadMode.FrameControlsManager.FrameShown, GamepadMode.FrameControlsManager, f)
  if ok then gamepadRegistered[f] = true end
  return ok
end

function W.GamepadHidden(f)
  if not gamepadRegistered[f] or not frameControls() then return false end
  if combatLocked() then
    gamepadStale[f] = true       -- told once combat ends (W.GamepadCatchUp)
    return false
  end
  return tellHidden(f)
end

-- After combat: report the frames that were hidden meanwhile and stayed hidden.
function W.GamepadCatchUp(f)
  if gamepadStale[f] and not f:IsShown() and not combatLocked() and frameControls() then return tellHidden(f) end
  return false
end

-- After a tab switch or a list refresh: SmartNavigation re-reads the frame's buttons (it also
-- does so by itself for frames created later, through its CreateFrame hook).
function W.GamepadRefresh(f)
  if not (f and gamepadRegistered[f]) or combatLocked() then return false end
  if type(SmartNavigation) ~= "table" or type(SmartNavigation.RefreshButtonGroups) ~= "function" then return false end
  return (pcall(SmartNavigation.RefreshButtonGroups, SmartNavigation, f))
end

function W.IsGamepadRegistered(f) return gamepadRegistered[f] == true end

-- Puts the keyboard cursor in `box` (mouse and keyboard only; see above). Returns true if it did.
-- A box that is not on screen (a dialog that opens after combat) is left alone.
function W.FocusBox(box)
  if not box or W.GamepadUI() then return false end
  if box.IsVisible and not box:IsVisible() then return false end
  box:SetFocus()
  return true
end

-- In combat the window and its dialogs stay hidden (DESIGN-v1.2 4.3, the WeakAuras pattern), so
-- an open asked for meanwhile (/needle, the key binding, the compartment, Settings) happens when
-- combat ends. A frame shown in combat could not join Forever's gamepad UI (W.GamepadShown is
-- skipped under the combat restriction), so a controller could neither use nor close it.
function W.InCombat()
  if combatLocked() then return true end
  return ns.SafeBool ~= nil and ns.SafeBool(UnitAffectingCombat, "player") == true
end

local toldAfterCombat = false
function W.OpenAfterCombat(f)
  f.ndReopen = "open"
  if not toldAfterCombat then
    toldAfterCombat = true
    if ns.Print then ns.Print(L["NeedleDropper opens when combat ends."]) end
  end
end

-- Frames listed here hide when combat starts and return when it ends, unless the player
-- closed them meanwhile (the WeakAuras pattern); nothing is destroyed, so typed text stays.
-- They also join the gamepad UI while they are shown.
function W.ManageInCombat(f)
  managed[#managed + 1] = f
  f:HookScript("OnShow", function(self)
    self.ndReopen = nil
    W.GamepadShown(self)
  end)
  f:HookScript("OnHide", function(self)
    if not self.ndCombatHiding then self.ndReopen = nil end
    W.GamepadHidden(self)
  end)
end

function W.OnCombatStart()
  for _, f in ipairs(managed) do
    if f:IsShown() then
      f.ndCombatHiding = true
      f:Hide()
      f.ndCombatHiding = nil
      f.ndReopen = true
    end
  end
end

function W.OnCombatEnd()
  toldAfterCombat = false
  for _, f in ipairs(managed) do
    if f.ndReopen then
      local fresh = f.ndReopen == "open"   -- opened during combat (W.OpenAfterCombat)
      f.ndReopen = nil
      if not f:IsShown() then
        f.ndReturning = not fresh   -- returning: keep the selection and the typed text
        f:Show()
        f.ndReturning = nil
      end
    end
    -- Hidden or opened during combat: tell the gamepad UI now.
    if f:IsShown() then W.GamepadShown(f) else W.GamepadCatchUp(f) end
  end
end

-- ------------------------------------------------------------------ footer --

local GATE_TEXT = {
  combat = "in combat", dead = "while dead", taxi = "on a flight path", cinematic = "during a cinematic",
  loading = "loading", ["no control"] = "while you are not in control", encounter = "during a boss fight",
  instance = "in an instance",
}

local function sourceLabel(active)
  local d = active.drop
  if active.isLocal then return L["in-game file"] end
  if nonEmpty(d.spotify) then return L["Spotify"] end
  if nonEmpty(d.youtube) then return L["YouTube"] end
  return L["Spotify search"]
end

-- The footer status line (DESIGN §4.3) and whether [Stop] applies to it.
function W.StatusText()
  local D = ns.Director
  if D and D.active and D.active.drop then
    local a = D.active
    local song = W.SongText(a.drop)
    local text = W.PlaceShort(a.drop)
    if song then text = text .. W.DOT .. song end
    return W.Format(L["Playing: %s (%s)"], text, sourceLabel(a)), true
  end
  if D and D.armed and D.armed.drop then
    local d = D.armed.drop
    local reasons = D.GateReasons and D.GateReasons(d) or {}
    if #reasons > 0 then
      return W.Format(L["Waiting for a calm moment: %s (%s)"], W.PlaceShort(d), L[GATE_TEXT[reasons[1]] or reasons[1]]), false
    end
    local cd = D.CooldownLeft and D.CooldownLeft() or 0
    if cd > 0 then
      return W.Format(L["Next needle drop in %s: %s"], ns.FormatSeconds and ns.FormatSeconds(cd) or tostring(cd), W.PlaceShort(d)), false
    end
    return W.Format(L["Arriving: %s"], W.PlaceShort(d)), false
  end
  local C = ns.Companion
  if C and C.StatusLine then
    local line = C.StatusLine()
    if nonEmpty(line) then return esc(line), false end
  end
  return L["NeedleDropper app not found: Spotify and YouTube songs need it"], false
end

function W.UpdateFooter()
  if not frame then return end
  local cur = W.CurrentTab()
  if cur and cur.UpdateButtons then cur.UpdateButtons() end
  local text, canStop = W.StatusText()
  frame.Status:SetText(text)
  local showSave = countEdits and editsSinceLogin >= W.SAVE_HINT_EDITS
  local def = W.CurrentTab()
  local right = def and def.buttons or frame.Grip
  -- The window's own Stop only where the tab has none (the Drops tab's bar has Stop), and the
  -- footer never overlaps the tab's buttons: at the narrowest window the Soundtracks tab's bar
  -- leaves ~150 px, so Save now takes its short label there and Save now beats Stop for room.
  canStop = canStop and not (def and def.hasStop)
  local fw = frame:GetWidth()
  local barW = (def and def.buttons and def.buttons:GetWidth()) or 0
  local room = (type(fw) == "number" and fw or W.DEFAULT_W) - 22 - barW - 12
  local saveW = 160
  if showSave and room < saveW + (canStop and 70 or 0) then saveW = 84 end
  if showSave and canStop and room < saveW + 70 then canStop = false end
  frame.SaveButton:SetText(saveW == 160 and L["Save now (reloads UI)"] or L["Save now"])
  frame.SaveButton:SetWidth(saveW)
  frame.SaveButton:SetShown(showSave)
  frame.StopButton:SetShown(canStop)
  local status = frame.Status
  status:ClearAllPoints()
  if showSave then
    status:SetPoint("LEFT", frame.SaveButton, "RIGHT", 8, 0)
  else
    status:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 14, 8)
  end
  frame.StopButton:ClearAllPoints()
  frame.StopButton:SetPoint("RIGHT", right, "LEFT", -6, 0)
  if canStop then
    status:SetPoint("RIGHT", frame.StopButton, "LEFT", -6, 0)
  else
    status:SetPoint("RIGHT", right, "LEFT", -6, 0)
  end
end

-- ---------------------------------------------------------------- refresh --

-- Model events only mark the window dirty; the refresh runs on the next frame, or right away
-- when the window's own code calls W.Flush() after an edit (so the selection is already right).
function W.MarkDirty(what)
  dirty[what or "all"] = true
  if not flushPending then
    flushPending = true
    C_Timer.After(0, function() W.Flush() end)
  end
end

function W.Flush()
  flushPending = false
  local what = dirty
  dirty = {}
  if not (frame and frame:IsShown()) or next(what) == nil then return end
  local def = W.CurrentTab()
  if def and def.Refresh and (what.all or what.model or what.location) then
    def.Refresh(what)
    W.GamepadRefresh(frame)
  end
  W.UpdateFooter()
end

function W.RefreshAll()
  dirty.all = true
  W.Flush()
end

-- ------------------------------------------------------------------- tabs --

function W.SelectTab(index, userClick)
  local def = tabs[index]
  if not (frame and def) then return end
  local previous = W.CurrentTab()
  if previous and previous ~= def and previous.OnHide then previous.OnHide() end
  current = index
  PanelTemplates_SetTab(frame, index)
  for i = 1, #tabs do
    local t = tabs[i]
    t.panel:SetShown(i == index)
    t.attic:SetShown(i == index)
    t.buttons:SetShown(i == index)
  end
  local u = uiDB()
  if u then u.tab = index end
  if userClick then PlaySound(SOUNDKIT.IG_CHARACTER_INFO_TAB) end
  if def.OnShow then def.OnShow(false) end
  W.UpdateFooter()
  W.GamepadRefresh(frame)
end

-- ------------------------------------------------------------------ build --

function W.OpenSettings()
  local O = ns.Options
  local id = O and O.CategoryID and O.CategoryID()
  if id ~= nil and Settings and Settings.OpenToCategory then
    local ok = pcall(Settings.OpenToCategory, id)
    if ok then return true end
  end
  if ns.Print then ns.Print(L["The Settings panel is not available here; use /needle settings and /needle set."]) end
  return false
end

local function build()
  if frame then return frame end
  local f = CreateFrame("Frame", "NeedleDropperFrame", UIParent, "ButtonFrameTemplate")
  frame = f
  f:Hide()
  ButtonFrameTemplate_HidePortrait(f)
  f:SetTitle(L["NeedleDropper"])
  f:SetFrameStrata("DIALOG")          -- above the Settings panel (HIGH)
  f:SetToplevel(true)
  f:SetClampedToScreen(true)
  f:SetMovable(true)
  f:SetResizable(true)
  f:SetResizeBounds(W.MIN_W, W.MIN_H, W.MAX_W, W.MAX_H)
  f:SetDontSavePosition(true)         -- db.ui holds the position, not layout-local.txt
  f:EnableMouse(true)
  restoreLayout()

  -- The template's close button calls HideUIPanel, which addons may not use in combat.
  f.CloseButton:SetScript("OnClick", function() UI.Close() end)
  W.SetTooltip(f.CloseButton, W.SimpleTooltip(CLOSE or L["Close"]))
  tinsert(UISpecialFrames, "NeedleDropperFrame")

  -- Title bar: gear (Settings) next to the close button, and a drag handle.
  f.SettingsButton = W.IconButton(f, 16, "Interface\\WorldMap\\GEAR_64GREY", function() W.OpenSettings() end,
    L["Settings"], L["Opens NeedleDropper's page in the game's Settings panel."])
  f.SettingsButton:SetPoint("RIGHT", f.CloseButton, "LEFT", -2, 0)
  f.SettingsButton:SetFrameLevel(f.CloseButton:GetFrameLevel())

  local drag = CreateFrame("Frame", nil, f)
  drag:SetPoint("TOPLEFT", f, "TOPLEFT", 4, 0)
  drag:SetPoint("TOPRIGHT", f.SettingsButton, "TOPLEFT", -4, 0)
  drag:SetHeight(22)
  drag:EnableMouse(true)
  drag:RegisterForDrag("LeftButton")
  drag:SetScript("OnDragStart", function() f:StartMoving() end)
  drag:SetScript("OnDragStop", function()
    f:StopMovingOrSizing()
    savePosition()
  end)
  f.TitleDrag = drag

  local grip = CreateFrame("Button", nil, f, "PanelResizeButtonTemplate")
  grip:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -3, 3)
  grip:Init(f, W.MIN_W, W.MIN_H, W.MAX_W, W.MAX_H)
  grip:SetOnResizeStoppedCallback(function() saveSize() end)
  grip:HookScript("OnEnter", function(self)
    GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
    GameTooltip_SetTitle(GameTooltip, L["Drag to resize the window"])
    GameTooltip:Show()
  end)
  grip:HookScript("OnLeave", function() GameTooltip:Hide() end)
  f.Grip = grip

  -- Footer (the 26 px button bar).
  f.Status = W.Label(f, nil, "GameFontHighlightSmall")
  f.Status:SetWordWrap(false)
  f.SaveButton = W.Button(f, L["Save now (reloads UI)"], 160, function() pcall(ReloadUI) end,
    L["Save now (reloads UI)"],
    L["The game writes your soundtracks to disk only at /reload, logout or exit. Reload now so a crash cannot lose today's edits."])
  f.SaveButton:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 6, 3)
  f.SaveButton:Hide()
  f.StopButton = W.Button(f, L["Stop"], 64, function()
    if ns.Director and ns.Director.Stop then ns.Director.Stop() end
    W.UpdateFooter()
  end, L["Stop"], L["Stops the song that is playing."])
  f.StopButton:Hide()

  -- Tabs and their panels.
  f.Tabs = f.Tabs or {}
  for i, def in ipairs(tabs) do
    local tab = CreateFrame("Button", "NeedleDropperFrameTab" .. i, f, "PanelTabButtonTemplate")
    f.Tabs[i] = tab
    tab:SetID(i)
    tab:SetText(def.title)
    tab:SetScript("OnClick", function(self) W.SelectTab(self:GetID(), true) end)
    if i == 1 then tab:SetPoint("TOPLEFT", f, "BOTTOMLEFT", 5, 2) end
    def.tab = tab

    local panel = CreateFrame("Frame", nil, f)
    panel:SetPoint("TOPLEFT", f.Inset, "TOPLEFT", 0, 0)
    panel:SetPoint("BOTTOMRIGHT", f.Inset, "BOTTOMRIGHT", 0, 0)
    local attic = CreateFrame("Frame", nil, f)
    attic:SetPoint("TOPLEFT", f, "TOPLEFT", 10, -26)
    attic:SetPoint("TOPRIGHT", f, "TOPRIGHT", -10, -26)
    attic:SetHeight(30)
    local buttons = CreateFrame("Frame", nil, f)
    buttons:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -22, 3)
    buttons:SetSize(1, 22)
    def.panel, def.attic, def.buttons = panel, attic, buttons
    f[def.key] = panel
    def.Build(panel, attic, buttons)
    panel:Hide()
    attic:Hide()
    buttons:Hide()
  end
  PanelTemplates_SetNumTabs(f, #tabs)

  f:SetScript("OnShow", function()
    PlaySound(SOUNDKIT.IG_CHARACTER_INFO_OPEN)
    if ticker then ticker:Cancel() end
    ticker = C_Timer.NewTicker(1, function() W.UpdateFooter() end)
    local def = W.CurrentTab()
    if def and def.OnShow then def.OnShow(not f.ndReturning) end
    W.RefreshAll()
  end)
  f:HookScript("OnSizeChanged", function() W.UpdateFooter() end)   -- the footer's room depends on the width
  f:SetScript("OnHide", function()
    PlaySound(SOUNDKIT.IG_CHARACTER_INFO_CLOSE)
    if ticker then
      ticker:Cancel()
      ticker = nil
    end
    local def = W.CurrentTab()
    if def and def.OnHide then def.OnHide() end
    GameTooltip:Hide()
  end)
  W.ManageInCombat(f)
  return f
end

-- ------------------------------------------------------------- public API --

function UI.IsShown()
  return frame ~= nil and frame:IsShown()
end

-- Opens the window, on `tab` ("drops" | "soundtracks" | "journal") or the last one used.
function UI.Open(tab)
  if not (ns.db and ns.Soundtracks) then
    if ns.Print then ns.Print(L["NeedleDropper is still loading - try again in a moment."]) end
    return false
  end
  build()
  local u = uiDB() or {}
  local index = tabIndex(tab) or tabIndex(u.tab) or 1
  W.SelectTab(index, false)
  if frame:IsShown() then
    W.RefreshAll()
  elseif W.InCombat() then
    W.OpenAfterCombat(frame)
    return true
  else
    frame:Show()
  end
  frame:Raise()
  return true
end

function UI.Close()
  if frame then
    frame.ndReopen = nil            -- also cancels an open waiting for the end of combat
    frame:Hide()
  end
end

function UI.Toggle()
  if UI.IsShown() or (frame and frame.ndReopen) then UI.Close() else UI.Open() end
end

function UI.Refresh()
  if frame and frame:IsShown() then W.RefreshAll() end
end

function UI.AddHere(text)
  if W.AddHere then return W.AddHere(text) end
  return false
end

function UI.OpenImport(prefillText)
  if W.OpenImport then return W.OpenImport(prefillText) end
  return false
end

function UI.OpenShare(ref)
  if W.OpenShare then return W.OpenShare(ref) end
  return false
end

-- Key bindings (Bindings.xml runs these in the global environment).
function NeedleDropper_ToggleWindow()
  UI.Toggle()
end

function NeedleDropper_AddDropHere()
  UI.AddHere()
end

function NeedleDropper_StopDrop()
  if ns.Director and ns.Director.Stop then ns.Director.Stop() end
end

-- ------------------------------------------------------------------ events --

ns.On("PLAYER_LOGIN", function() countEdits = true end)
ns.On("ND_SOUNDTRACKS_CHANGED", function()
  if countEdits then editsSinceLogin = editsSinceLogin + 1 end
  W.MarkDirty("model")
end)
ns.On("ND_MAP_CHANGED", function() W.MarkDirty("model") end)
ns.On("ND_COMPANION_DATA", function() W.MarkDirty("model") end)
ns.On("ND_PLAYBACK", function() W.MarkDirty("footer") end)
ns.On("ND_LOCATION", function() W.MarkDirty("location") end)
ns.On("PLAYER_REGEN_DISABLED", function() W.OnCombatStart() end)
ns.On("PLAYER_REGEN_ENABLED", function() W.OnCombatEnd() end)

function W.EditsSinceLogin() return editsSinceLogin end
