-- shim_ui.lua -- UI extension of wow_shim.lua for the NeedleDropper window (DESIGN-v1.2 §4.3-4.10).
--
-- Loaded right after wow_shim.lua by tests/v12_ui_harness.py. It turns the base shim's frames into
-- a recording mock of the Blizzard widget kit the window is built from, checked against the
-- forever branch (Gethe/wow-ui-source @eac30af, 1.60.1.70790):
--
--   * CreateFrame(type, name, parent, template) RAISES for any template outside the CONTRACTS 2.9
--     allowlist, and for a frame type that does not match the template's root element;
--   * every frame and region method call is recorded in obj.__calls / obj.__count;
--   * EditBox (focus, autofocus, OnTextChanged(userInput), max bytes), Button / CheckButton (enable
--     state, click registration, motion scripts while disabled), Slider, ScrollFrame and
--     DropdownButton behave like the client closely enough to drive the UI from tests;
--   * the allowlisted templates build the children, keys and mixin methods the real XML gives
--     (ButtonFrameTemplate.CloseButton with UIPanelCloseButton_OnClick -> HideUIPanel, .Inset,
--     TitleContainer.TitleText, PanelTabButtonTemplate parentArray "Tabs", UIRadioButtonTemplate's
--     lowercase .text, ScrollingEditBoxTemplate's callback registry, ...);
--   * ScrollBox lists (CreateScrollBoxListLinearView, CreateDataProvider, ScrollUtil), Blizzard_Menu
--     (SetupMenu / GenerateMenu / MenuUtil.CreateContextMenu and the rootDescription Create*),
--     StaticPopupDialogs / StaticPopup_Show with the 12.x (dialog, data) handlers, PanelTemplates_*,
--     UISpecialFrames / CloseSpecialWindows, GameTooltip and its GameTooltip_* helpers;
--   * forbidden APIs (DESIGN §4.9) record and refuse: OnKeyDown, EnableKeyboard,
--     SetPropagateKeyboardInput, CopyToClipboard, ShowUIPanel / HideUIPanel (blocked in combat);
--   * FontString text is checked for unescaped "|" (SHIM.ui.badEscapes).
--
-- SHIM.ui.* holds the test helpers (Click, Type, Paste, PressEscape, MenuPick, PopupAccept, ...).
-- SHIM.uiFakes.Install(ns) provides stand-ins for ns.Soundtracks / ns.Share / ns.Companion with
-- exactly the CONTRACTS 2.5-2.7 signatures, for running the window before A1's modules exist.
--
-- Lua 5.1 only.

local SHIM = assert(_G.SHIM, "shim_ui.lua needs wow_shim.lua first")
local U = {}
SHIM.ui = U

local tinsert, tremove, concat = table.insert, table.remove, table.concat
local floor, max, min = math.floor, math.max, math.min
local protected = SHIM.protected

local function shimError(msg, level)
  error("shim_ui: " .. msg, (level or 1) + 1)
end

local function copy(t, seen)
  if type(t) ~= "table" then return t end
  local out = {}
  for k, v in pairs(t) do out[k] = copy(v) end
  return out
end

-- ------------------------------------------------------------ allowlists --

-- CONTRACTS 2.9 template allowlist -> root element type, as declared on the forever branch.
U.ALLOWED_TEMPLATES = {
  ButtonFrameTemplate = "Frame",               -- Blizzard_SharedXML/Mainline/SharedUIPanelTemplates.xml L711
  PanelTabButtonTemplate = "Button",           -- same file L932
  PanelResizeButtonTemplate = "Button",        -- same file L1642
  UIPanelButtonTemplate = "Button",            -- same file L315
  UIPanelCloseButton = "Button",               -- same file L148
  WowScrollBoxList = "Frame",                  -- Blizzard_SharedXML/Shared/Scroll/ScrollTemplates.xml L4
  ScrollingEditBoxTemplate = "Frame",          -- same file L21
  MinimalScrollBar = "EventFrame",             -- Blizzard_SharedXML/Shared/Scroll/MinimalScrollBar.xml L15
  InputBoxInstructionsTemplate = "EditBox",    -- Blizzard_SharedXML/Shared/InputBox/InputBoxTemplates.xml L180
  SearchBoxTemplate = "EditBox",               -- same file L209
  WowStyle1DropdownTemplate = "DropdownButton",       -- Blizzard_Menu/Mainline/MenuTemplates.xml L3
  WowStyle1FilterDropdownTemplate = "DropdownButton", -- same file L66
  UICheckButtonTemplate = "CheckButton",       -- Blizzard_SharedXML/Shared/Button/CheckButtonTemplates.xml L50
  UIRadioButtonTemplate = "CheckButton",       -- same file L4
  MinimalSliderWithSteppersTemplate = "Frame", -- Blizzard_SharedXML/Shared/Slider/MinimalSlider.xml L34
}

-- GameFont* objects the window may use (Blizzard_Fonts_Shared/Shared/FontStyles.xml); nothing
-- smaller than GameFontHighlightSmall (DESIGN §4.10).
U.FONTS = {
  GameFontNormal = 12, GameFontHighlight = 12, GameFontDisable = 12,
  GameFontNormalSmall = 10, GameFontHighlightSmall = 10, GameFontDisableSmall = 10,
  GameFontRedSmall = 10, GameFontGreenSmall = 10,
  GameFontNormalLarge = 16, GameFontHighlightLarge = 16, GameFontNormalMed2 = 14, GameFontHighlightMedium = 14,
}

U.created = {}        -- every CreateFrame call made by addon code: { frame, frameType, name, template }
U.focus = nil         -- the EditBox with keyboard focus
U.forbidden = {}      -- forbidden / protected API use: { name = , detail = }
U.blocked = {}        -- protected calls refused in combat ("Interface action blocked")
U.badEscapes = {}     -- FontString / tooltip text with an unescaped "|"
U.sounds = {}         -- PlaySound kit ids
U.reloads = 0         -- ReloadUI() calls
U.popups = {}         -- StaticPopup dialogs ever shown
U.contextMenus = {}   -- MenuUtil.CreateContextMenu results: { owner =, root = }
U.shift = false

local function forbid(name, detail)
  U.forbidden[#U.forbidden + 1] = { name = name, detail = detail }
end

-- ------------------------------------------------------------- recording --

local WRAPPED = setmetatable({}, { __mode = "k" })
local MAX_CALLS = 3000

local function record(obj, name, ...)
  local calls = rawget(obj, "__calls")
  if not calls then
    calls = {}
    rawset(obj, "__calls", calls)
    rawset(obj, "__count", {})
  end
  if #calls < MAX_CALLS then calls[#calls + 1] = { name, ... } end
  local counts = rawget(obj, "__count")
  counts[name] = (counts[name] or 0) + 1
end
U.record = record

local function wrap(name, fn)
  if WRAPPED[fn] then return fn end
  local w = function(self, ...)
    if type(self) == "table" then record(self, name, ...) end
    return fn(self, ...)
  end
  WRAPPED[w] = true
  return w
end

local function wrapClass(class)
  for name, fn in pairs(class) do
    if type(fn) == "function" and string.sub(name, 1, 2) ~= "__" then class[name] = wrap(name, fn) end
  end
end

-- Copies methods onto an object (a template mixin), recording their calls too.
local function mix(obj, methods)
  for name, fn in pairs(methods) do
    if type(fn) == "function" then obj[name] = wrap(name, fn) else obj[name] = fn end
  end
  return obj
end
U.mix = mix

function U.Calls(obj, name)
  local out = {}
  local calls = rawget(obj, "__calls") or {}
  for i = 1, #calls do
    if name == nil or calls[i][1] == name then out[#out + 1] = calls[i] end
  end
  return out
end

function U.CallCount(obj, name)
  local counts = rawget(obj, "__count")
  return counts and counts[name] or 0
end

-- ---------------------------------------------------- the base classes --

local FrameClass = getmetatable(UIParent).__index        -- wow_shim's Frame methods
local RegionClass = getmetatable(FrameClass).__index     -- wow_shim's Region methods
U.FrameClass, U.RegionClass = FrameClass, RegionClass

local function fire(obj, script, ...)
  local fn = obj.__scripts and obj.__scripts[script]
  if fn then return protected(fn, obj, ...) end
  return nil
end
U.fire = fire

-- Script kinds by object type. OnKeyDown / OnKeyUp are refused outright (DESIGN §4.9).
local COMMON_SCRIPTS = { OnEvent = 1, OnUpdate = 1, OnShow = 1, OnHide = 1, OnSizeChanged = 1, OnEnter = 1,
  OnLeave = 1, OnMouseDown = 1, OnMouseUp = 1, OnMouseWheel = 1, OnDragStart = 1, OnDragStop = 1,
  OnLoad = 1, OnAttributeChanged = 1, OnReceiveDrag = 1 }
local TYPE_SCRIPTS = {
  Button = { OnClick = 1, PreClick = 1, PostClick = 1, OnDoubleClick = 1, OnEnable = 1, OnDisable = 1 },
  EditBox = { OnTextChanged = 1, OnEnterPressed = 1, OnEscapePressed = 1, OnTabPressed = 1, OnSpacePressed = 1,
    OnEditFocusGained = 1, OnEditFocusLost = 1, OnTextSet = 1, OnChar = 1, OnCursorChanged = 1,
    OnArrowPressed = 1, OnEnable = 1, OnDisable = 1, OnInputLanguageChanged = 1 },
  Slider = { OnValueChanged = 1, OnMinMaxChanged = 1, OnEnable = 1, OnDisable = 1 },
  ScrollFrame = { OnVerticalScroll = 1, OnHorizontalScroll = 1, OnScrollRangeChanged = 1 },
}
TYPE_SCRIPTS.CheckButton = TYPE_SCRIPTS.Button
TYPE_SCRIPTS.DropdownButton = TYPE_SCRIPTS.Button
local FORBIDDEN_SCRIPTS = { OnKeyDown = true, OnKeyUp = true }

local function scriptAllowed(obj, kind)
  if COMMON_SCRIPTS[kind] then return true end
  local extra = TYPE_SCRIPTS[obj.__kind]
  return extra ~= nil and extra[kind] ~= nil
end

function FrameClass:SetScript(kind, fn)
  if FORBIDDEN_SCRIPTS[kind] then
    forbid(kind, self:GetName())
    shimError(kind .. " scripts are not allowed (DESIGN 4.9: no keyboard capture)", 2)
  end
  if not scriptAllowed(self, kind) then
    shimError("SetScript: " .. tostring(self.__kind) .. " has no " .. tostring(kind) .. " script", 2)
  end
  if fn ~= nil and type(fn) ~= "function" then shimError("SetScript needs a function or nil", 2) end
  self.__scripts[kind] = fn
end

function FrameClass:HookScript(kind, fn)
  if FORBIDDEN_SCRIPTS[kind] then
    forbid(kind, self:GetName())
    shimError(kind .. " scripts are not allowed (DESIGN 4.9: no keyboard capture)", 2)
  end
  if not scriptAllowed(self, kind) then
    shimError("HookScript: " .. tostring(self.__kind) .. " has no " .. tostring(kind) .. " script", 2)
  end
  if type(fn) ~= "function" then shimError("HookScript needs a function", 2) end
  local prev = self.__scripts[kind]
  if prev then
    self.__scripts[kind] = function(...) prev(...); fn(...) end
  else
    self.__scripts[kind] = fn
  end
  self.__hooked = self.__hooked or {}
  self.__hooked[kind] = (self.__hooked[kind] or 0) + 1
end

function FrameClass:GetScript(kind) return self.__scripts[kind] end
function FrameClass:HasScript(kind) return scriptAllowed(self, kind) end

function FrameClass:EnableKeyboard()
  forbid("EnableKeyboard", self:GetName())
  shimError("EnableKeyboard is not allowed (DESIGN 4.9)", 2)
end
function FrameClass:SetPropagateKeyboardInput()
  forbid("SetPropagateKeyboardInput", self:GetName())
  shimError("SetPropagateKeyboardInput is not allowed (DESIGN 4.9)", 2)
end

-- Visibility: like the client, OnShow / OnHide run for every descendant whose visibility changes,
-- an EditBox loses keyboard focus when it stops being visible, and one with autoFocus grabs it
-- when it becomes visible.
local function onVisible(obj)
  fire(obj, "OnShow")
  if obj.__kind == "EditBox" and obj.__autoFocus and obj.__enabled ~= false and U.focus ~= obj then
    obj:SetFocus()
  end
  local kids = obj.__children
  if kids then
    for i = 1, #kids do
      local c = kids[i]
      if c.__shown then onVisible(c) end
    end
  end
end

local function onInvisible(obj)
  if U.focus == obj then obj:ClearFocus() end
  local kids = obj.__children
  if kids then
    for i = 1, #kids do
      local c = kids[i]
      if c.__shown then onInvisible(c) end
    end
  end
  fire(obj, "OnHide")
end

function FrameClass:Show()
  local was = self:IsVisible()
  self.__shown = true
  if not was and self:IsVisible() then onVisible(self) end
end
function FrameClass:Hide()
  local was = self:IsVisible()
  self.__shown = false
  if was then onInvisible(self) end
end
function FrameClass:SetShown(v) if v then self:Show() else self:Hide() end end

function FrameClass:SetID(id) self.__id = id end
function FrameClass:GetID() return self.__id or 0 end
function FrameClass:RegisterForDrag(...) self.__drag = { ... } end
function FrameClass:SetMovable(v) self.__movable = v and true or false end
function FrameClass:IsMovable() return self.__movable == true end
function FrameClass:SetResizable(v) self.__resizable = v and true or false end
function FrameClass:IsResizable() return self.__resizable == true end
function FrameClass:SetResizeBounds(minW, minH, maxW, maxH) self.__resizeBounds = { minW, minH, maxW, maxH } end
function FrameClass:GetResizeBounds()
  local b = self.__resizeBounds or {}
  return b[1], b[2], b[3], b[4]
end
function FrameClass:StartMoving()
  if not self.__movable then shimError("StartMoving on a frame that is not movable", 2) end
  self.__moving = true
end
function FrameClass:StartSizing(point)
  if not self.__resizable then shimError("StartSizing on a frame that is not resizable", 2) end
  self.__sizing = point or "BOTTOMRIGHT"
end
function FrameClass:StopMovingOrSizing() self.__moving = nil; self.__sizing = nil end
function FrameClass:SetUserPlaced(v) self.__userPlaced = v end
function FrameClass:IsUserPlaced() return self.__userPlaced == true end
function FrameClass:Raise() self.__raised = (self.__raised or 0) + 1 end
function FrameClass:Lower() end
function FrameClass:IsMouseOver() return self.__mouseOver == true end
function FrameClass:SetHitRectInsets(...) self.__hitRect = { ... } end
function FrameClass:SetClipsChildren(v) self.__clips = v end
function FrameClass:GetDebugName() return self:GetName() or ("<" .. tostring(self.__kind) .. ">") end
function FrameClass:SetIgnoreParentAlpha(v) self.__ignoreParentAlpha = v end
function FrameClass:SetFlattensRenderLayers(v) end
function FrameClass:EnableMouseMotion(v) self.__mouseMotion = v end
function FrameClass:GetLeft() return self.__left or 0 end
function FrameClass:GetTop() return self.__top or 0 end
function FrameClass:GetRight() return (self.__left or 0) + (self.__w or 0) end
function FrameClass:GetBottom() return (self.__top or 0) - (self.__h or 0) end
function FrameClass:SetSize(w, h)
  if type(w) ~= "number" or type(h) ~= "number" then shimError("SetSize needs numbers", 2) end
  local changed = w ~= self.__w or h ~= self.__h
  self.__w, self.__h = w, h
  if changed then fire(self, "OnSizeChanged", w, h) end
end
function FrameClass:SetWidth(w)
  local changed = w ~= self.__w
  self.__w = w
  if changed then fire(self, "OnSizeChanged", self.__w, self.__h) end
end
function FrameClass:SetHeight(h)
  local changed = h ~= self.__h
  self.__h = h
  if changed then fire(self, "OnSizeChanged", self.__w, self.__h) end
end
function RegionClass:GetPointByName(point)
  local p = self.__points[point]
  if not p then return nil end
  return p[1], p[2], p[3], p[4], p[5]
end

-- Regions: fonts, atlases, escape checking.
local FONT_OBJECTS = {}
local function fontObject(name)
  if type(name) == "table" then return name end
  local fo = FONT_OBJECTS[name]
  if not fo then shimError("unknown font object '" .. tostring(name) .. "' (allowed: GameFont* listed in U.FONTS)", 3) end
  return fo
end

for name, size in pairs(U.FONTS) do
  local fo = { __fontName = name, __size = size }
  function fo:GetFont() return "Fonts\\FRIZQT__.TTF", self.__size, "" end
  function fo:GetName() return self.__fontName end
  function fo:GetTextColor() return 1, 1, 1, 1 end
  FONT_OBJECTS[name] = fo
  _G[name] = fo
end

local function hasHex8(text, p)
  return string.find(text, "^%x%x%x%x%x%x%x%x", p) ~= nil
end

-- Records text with a "|" that does not start a valid UI escape sequence.
local function checkEscapes(where, text)
  if type(text) ~= "string" then return end
  local i, n = 1, #text
  while i <= n do
    local p = string.find(text, "|", i, true)
    if not p then return end
    local c = string.sub(text, p + 1, p + 1)
    local ok = false
    if c == "|" or c == "r" or c == "n" or c == "h" or c == "t" or c == "a" or c == "k" then
      ok = true
    elseif c == "c" then
      ok = hasHex8(text, p + 2)
    elseif c == "T" then
      ok = string.find(text, "|t", p + 2, true) ~= nil
    elseif c == "A" then
      ok = string.find(text, "|a", p + 2, true) ~= nil
    elseif c == "H" then
      ok = string.find(text, "|h", p + 2, true) ~= nil
    elseif c == "K" then
      ok = string.find(text, "|k", p + 2, true) ~= nil
    end
    if not ok then
      U.badEscapes[#U.badEscapes + 1] = { where = where, text = text }
      return
    end
    i = p + 2
  end
end
U.checkEscapes = checkEscapes

local baseRegionSetText = RegionClass.SetText
function RegionClass:SetText(t)
  if self.__kind == "FontString" and type(t) == "string" then checkEscapes(self:GetName() or "FontString", t) end
  return baseRegionSetText(self, t)
end
function RegionClass:SetFormattedText(fmt, ...)
  self:SetText(string.format(fmt, ...))
end
function RegionClass:SetFontObject(o)
  local fo = fontObject(o)
  self.__fontObject = fo.__fontName or fo
end
function RegionClass:GetFontObject()
  return FONT_OBJECTS[self.__fontObject] or self.__fontObject
end
function RegionClass:SetAtlas(atlas, useAtlasSize) self.__atlas = atlas; self.__texture = nil end
function RegionClass:GetAtlas() return self.__atlas end
function RegionClass:SetDesaturated(v) self.__desaturated = v end
function RegionClass:SetRotation(r) self.__rotation = r end
function RegionClass:IsTruncated() return false end
function RegionClass:GetUnboundedStringWidth() return self:GetStringWidth() end
function RegionClass:SetTextToFit(t) self:SetText(t) end
function RegionClass:SetIndentedWordWrap(v) end
function RegionClass:GetNumLines() return 1 end
function RegionClass:SetHorizTile(v) end
function RegionClass:SetVertTile(v) end
function RegionClass:CanWordWrap() return self.__wordWrap ~= false end

-- Regions are built like wow_shim's newRegion (a local there), with the same method table.
local RegionMT = { __index = RegionClass }
local function newRegion(kind, name, parent, layer)
  local r = setmetatable({}, RegionMT)
  r.__kind = kind
  r.__name = name
  r.__parent = parent
  r.__shown = true
  r.__alpha = 1
  r.__scale = 1
  r.__points = {}
  r.__pointOrder = {}
  r.__w, r.__h = 0, 0
  r.__layer = layer
  return r
end

-- CreateFontString(name, layer, inherits): `inherits` must be a known GameFont* object.
function FrameClass:CreateFontString(name, layer, inherits)
  local fs = newRegion("FontString", name, self, layer)
  if inherits ~= nil then
    local fo = fontObject(inherits)
    fs.__fontObject = fo.__fontName
    fs.__inherits = inherits
  end
  tinsert(self.__regions, fs)
  if name then _G[name] = fs end
  return fs
end

function FrameClass:CreateTexture(name, layer, template, sub)
  local t = newRegion("Texture", name, self, layer)
  t.__sublayer = sub
  tinsert(self.__regions, t)
  if name then _G[name] = t end
  return t
end

-- ------------------------------------------------------- typed classes --

local ButtonClass = setmetatable({}, { __index = FrameClass })
local CheckButtonClass = setmetatable({}, { __index = ButtonClass })
local EditBoxClass = setmetatable({}, { __index = FrameClass })
local SliderClass = setmetatable({}, { __index = FrameClass })
local ScrollFrameClass = setmetatable({}, { __index = FrameClass })
local DropdownButtonClass = setmetatable({}, { __index = ButtonClass })
local EventFrameClass = setmetatable({}, { __index = FrameClass })

local TYPE_CLASS = {
  Frame = FrameClass, Button = ButtonClass, CheckButton = CheckButtonClass, EditBox = EditBoxClass,
  Slider = SliderClass, ScrollFrame = ScrollFrameClass, DropdownButton = DropdownButtonClass,
  EventFrame = EventFrameClass,
}
local TYPE_MT = {}
for t, class in pairs(TYPE_CLASS) do TYPE_MT[t] = { __index = class } end
U.TYPE_CLASS = TYPE_CLASS

-- Button ------------------------------------------------------------------

local function buttonTexture(self, key, asset, layer)
  local t = self[key]
  if not t then
    t = self:CreateTexture(nil, layer or "ARTWORK")
    self[key] = t
  end
  if type(asset) == "table" then
    self[key] = asset
    return asset
  end
  t:SetTexture(asset)
  return t
end

function ButtonClass:GetFontString() return self.__fs end
function ButtonClass:SetFontString(fs) self.__fs = fs end
function ButtonClass:SetText(t)
  if not self.__fs then self.__fs = self:CreateFontString(nil, "ARTWORK") end
  self.__fs:SetText(t)
end
function ButtonClass:SetFormattedText(fmt, ...) self:SetText(string.format(fmt, ...)) end
function ButtonClass:GetText() return self.__fs and self.__fs:GetText() or nil end
function ButtonClass:GetTextWidth() return self.__fs and self.__fs:GetStringWidth() or 0 end
function ButtonClass:SetNormalFontObject(fo) self.__normalFont = fontObject(fo).__fontName end
function ButtonClass:SetHighlightFontObject(fo) self.__highlightFont = fontObject(fo).__fontName end
function ButtonClass:SetDisabledFontObject(fo) self.__disabledFont = fontObject(fo).__fontName end
function ButtonClass:IsEnabled() return self.__enabled ~= false end
function ButtonClass:Enable()
  if self.__enabled == false then
    self.__enabled = true
    fire(self, "OnEnable")
  end
end
function ButtonClass:Disable()
  if self.__enabled ~= false then
    self.__enabled = false
    fire(self, "OnDisable")
  end
end
function ButtonClass:SetEnabled(v) if v then self:Enable() else self:Disable() end end
function ButtonClass:RegisterForClicks(...) self.__clicks = { ... } end
function ButtonClass:SetMotionScriptsWhileDisabled(v) self.__motionWhileDisabled = v and true or false end
function ButtonClass:GetMotionScriptsWhileDisabled() return self.__motionWhileDisabled == true end
function ButtonClass:SetNormalTexture(asset) return buttonTexture(self, "__normalTex", asset, "ARTWORK") end
function ButtonClass:SetPushedTexture(asset) return buttonTexture(self, "__pushedTex", asset, "ARTWORK") end
function ButtonClass:SetDisabledTexture(asset) return buttonTexture(self, "__disabledTex", asset, "ARTWORK") end
function ButtonClass:SetHighlightTexture(asset, blend) local t = buttonTexture(self, "__highlightTex", asset, "HIGHLIGHT"); return t end
function ButtonClass:GetNormalTexture() return self.__normalTex end
function ButtonClass:GetPushedTexture() return self.__pushedTex end
function ButtonClass:GetDisabledTexture() return self.__disabledTex end
function ButtonClass:GetHighlightTexture() return self.__highlightTex end
function ButtonClass:SetNormalAtlas(a) buttonTexture(self, "__normalTex", nil, "ARTWORK"):SetAtlas(a) end
function ButtonClass:SetPushedAtlas(a) buttonTexture(self, "__pushedTex", nil, "ARTWORK"):SetAtlas(a) end
function ButtonClass:SetHighlightAtlas(a) buttonTexture(self, "__highlightTex", nil, "HIGHLIGHT"):SetAtlas(a) end
function ButtonClass:SetDisabledAtlas(a) buttonTexture(self, "__disabledTex", nil, "ARTWORK"):SetAtlas(a) end
function ButtonClass:LockHighlight() self.__locked = true end
function ButtonClass:UnlockHighlight() self.__locked = false end
function ButtonClass:SetButtonState(s) self.__state = s end
function ButtonClass:GetButtonState() return self.__state or "NORMAL" end
function ButtonClass:Click(mouseButton)
  if not self:IsEnabled() then return end
  fire(self, "PreClick", mouseButton or "LeftButton", false)
  fire(self, "OnClick", mouseButton or "LeftButton", false)
  fire(self, "PostClick", mouseButton or "LeftButton", false)
end
function ButtonClass:DesaturateHierarchy(v) end

function CheckButtonClass:SetChecked(v) self.__checked = v and true or false end
function CheckButtonClass:GetChecked() return self.__checked == true end
function CheckButtonClass:SetCheckedTexture(asset) return buttonTexture(self, "__checkedTex", asset, "ARTWORK") end
function CheckButtonClass:SetDisabledCheckedTexture(asset) return buttonTexture(self, "__disabledCheckedTex", asset, "ARTWORK") end
function CheckButtonClass:GetCheckedTexture() return self.__checkedTex end

-- EditBox -------------------------------------------------------------------

local function utf8Chars(s)
  local n = 0
  for _ in string.gmatch(s, "[%z\1-\127\194-\244][\128-\191]*") do n = n + 1 end
  return n
end

local function clipText(box, t)
  local maxBytes = box.__maxBytes or 0
  if maxBytes > 0 and #t > maxBytes then
    local n = maxBytes
    while n > 0 do
      local b = string.byte(t, n + 1)
      if b == nil or b < 0x80 or b >= 0xC0 then break end
      n = n - 1
    end
    t = string.sub(t, 1, n)
  end
  local maxLetters = box.__maxLetters or 0
  if maxLetters > 0 and utf8Chars(t) > maxLetters then
    local out, count = {}, 0
    for ch in string.gmatch(t, "[%z\1-\127\194-\244][\128-\191]*") do
      count = count + 1
      if count > maxLetters then break end
      out[#out + 1] = ch
    end
    t = concat(out)
  end
  return t
end

function EditBoxClass:SetText(t)
  if t == nil then t = "" end
  if type(t) == "number" then t = tostring(t) end
  if type(t) ~= "string" then shimError("EditBox:SetText needs a string", 2) end
  t = clipText(self, t)
  self.__text = t
  self.__cursor = #t
  fire(self, "OnTextSet")
  fire(self, "OnTextChanged", false)
end
function EditBoxClass:GetText() return self.__text or "" end
function EditBoxClass:GetNumLetters() return utf8Chars(self.__text or "") end
function EditBoxClass:Insert(t)
  local cur = self.__text or ""
  local pos = self.__cursor or #cur
  self.__text = clipText(self, string.sub(cur, 1, pos) .. tostring(t) .. string.sub(cur, pos + 1))
  self.__cursor = pos + #tostring(t)
  fire(self, "OnTextChanged", false)
end
function EditBoxClass:SetAutoFocus(v) self.__autoFocus = v and true or false end
function EditBoxClass:IsAutoFocus() return self.__autoFocus == true end
function EditBoxClass:SetFocus()
  if not self:IsVisible() then return end
  if U.focus == self then return end
  local prev = U.focus
  if prev then
    U.focus = nil
    fire(prev, "OnEditFocusLost")
  end
  U.focus = self
  fire(self, "OnEditFocusGained")
end
function EditBoxClass:ClearFocus()
  if U.focus ~= self then return end
  U.focus = nil
  fire(self, "OnEditFocusLost")
end
function EditBoxClass:HasFocus() return U.focus == self end
function EditBoxClass:HighlightText(a, b)
  local len = #(self.__text or "")
  a = a or 0
  b = b or -1
  if b < 0 then b = len end
  self.__highlight = { a, b }
end
function EditBoxClass:ClearHighlightText() self.__highlight = { 0, 0 } end
function EditBoxClass:SetMaxLetters(n) self.__maxLetters = n or 0 end
function EditBoxClass:GetMaxLetters() return self.__maxLetters or 0 end
function EditBoxClass:SetMaxBytes(n) self.__maxBytes = n or 0 end
function EditBoxClass:GetMaxBytes() return self.__maxBytes or 0 end
function EditBoxClass:SetCursorPosition(n) self.__cursor = n end
function EditBoxClass:GetCursorPosition() return self.__cursor or 0 end
function EditBoxClass:SetMultiLine(v) self.__multiLine = v and true or false end
function EditBoxClass:IsMultiLine() return self.__multiLine == true end
function EditBoxClass:SetNumeric(v) self.__numeric = v end
function EditBoxClass:IsNumeric() return self.__numeric == true end
function EditBoxClass:SetTextInsets(...) self.__insets = { ... } end
function EditBoxClass:GetTextInsets() local t = self.__insets or {}; return t[1], t[2], t[3], t[4] end
function EditBoxClass:SetCountInvisibleLetters(v) end
function EditBoxClass:SetSecureText(v) end
function EditBoxClass:SetDesiredWidth(w) self.__desiredWidth = w end
function EditBoxClass:IsEnabled() return self.__enabled ~= false end
function EditBoxClass:Enable()
  if self.__enabled == false then
    self.__enabled = true
    fire(self, "OnEnable")
  end
end
function EditBoxClass:Disable()
  if self.__enabled ~= false then
    self.__enabled = false
    if U.focus == self then self:ClearFocus() end
    fire(self, "OnDisable")
  end
end
function EditBoxClass:SetEnabled(v) if v then self:Enable() else self:Disable() end end
function EditBoxClass:GetFontHeight() return 12 end

-- Slider --------------------------------------------------------------------

function SliderClass:SetMinMaxValues(a, b) self.__min, self.__max = a, b end
function SliderClass:GetMinMaxValues() return self.__min or 0, self.__max or 0 end
function SliderClass:SetValueStep(s) self.__step = s end
function SliderClass:GetValueStep() return self.__step or 1 end
function SliderClass:SetObeyStepOnDrag(v) self.__obey = v end
function SliderClass:SetOrientation(o) self.__orientation = o end
function SliderClass:SetThumbTexture(t) self.__thumb = t end
local function sliderSet(self, v, userInput)
  local lo, hi = self:GetMinMaxValues()
  if v < lo then v = lo end
  if v > hi then v = hi end
  if v == self.__value then return end
  self.__value = v
  fire(self, "OnValueChanged", v, userInput)
end
function SliderClass:SetValue(v) sliderSet(self, v, false) end
function SliderClass:GetValue() return self.__value or 0 end
function SliderClass:IsEnabled() return self.__enabled ~= false end
function SliderClass:Enable() self.__enabled = true end
function SliderClass:Disable() self.__enabled = false end
function SliderClass:SetEnabled(v) self.__enabled = v and true or false end

-- ScrollFrame -----------------------------------------------------------------

function ScrollFrameClass:SetScrollChild(child)
  self.__scrollChild = child
  if child and child.SetParent then child:SetParent(self) end
end
function ScrollFrameClass:GetScrollChild() return self.__scrollChild end
function ScrollFrameClass:GetVerticalScrollRange()
  local c = self.__scrollChild
  if not c then return 0 end
  return max(0, (c.__h or 0) - (self.__h or 0))
end
function ScrollFrameClass:SetVerticalScroll(v)
  self.__vscroll = v
  fire(self, "OnVerticalScroll", v)
end
function ScrollFrameClass:GetVerticalScroll() return self.__vscroll or 0 end
function ScrollFrameClass:SetHorizontalScroll(v) self.__hscroll = v end
function ScrollFrameClass:GetHorizontalScroll() return self.__hscroll or 0 end
function ScrollFrameClass:UpdateScrollChildRect()
  fire(self, "OnScrollRangeChanged", 0, self:GetVerticalScrollRange())
end

-- ----------------------------------------------------------- CreateFrame --

local baseCreateFrame = CreateFrame

-- Internal object creation (templates' own children): no allowlist check.
local function newObject(frameType, name, parent, label)
  local mt = TYPE_MT[frameType]
  if not mt then shimError("frame type '" .. tostring(frameType) .. "' is not supported by the shim", 3) end
  local f = baseCreateFrame(frameType, name, parent)
  setmetatable(f, mt)
  f.__template = label
  if frameType == "EditBox" then
    f.__text = ""
    f.__autoFocus = true      -- UI.xsd: autoFocus defaults to true
    f.__maxLetters = 0
    f.__maxBytes = 0
    f.__mouse = true
  end
  return f
end
U.newObject = newObject

local TEMPLATE_BUILD = {}
U.TEMPLATE_BUILD = TEMPLATE_BUILD

function CreateFrame(frameType, name, parent, template, id)
  if type(frameType) ~= "string" then shimError("CreateFrame needs a frame type", 2) end
  if type(parent) == "string" then parent = _G[parent] end
  local list = {}
  if template ~= nil then
    if type(template) ~= "string" then shimError("CreateFrame template must be a string", 2) end
    for t in string.gmatch(template, "[^,%s]+") do
      local root = U.ALLOWED_TEMPLATES[t]
      if not root then
        shimError("template '" .. t .. "' is not in the CONTRACTS 2.9 allowlist", 2)
      end
      if root ~= frameType then
        shimError(string.format("template '%s' is a %s; CreateFrame was asked for a %s", t, root, frameType), 2)
      end
      list[#list + 1] = t
    end
  end
  if not TYPE_MT[frameType] then shimError("CreateFrame frame type '" .. frameType .. "' is not supported", 2) end
  local f = newObject(frameType, name, parent, template)
  for i = 1, #list do TEMPLATE_BUILD[list[i]](f) end
  if id ~= nil then f:SetID(id) end
  U.created[#U.created + 1] = { frame = f, frameType = frameType, name = name, template = template }
  return f
end

local function childName(f, suffix)
  local n = f:GetName()
  if n then return n .. suffix end
  return nil
end

-- ------------------------------------------------------ callback registry --

local ownerSeq = 0
local CallbackRegistry = {}
U.CallbackRegistry = CallbackRegistry

function CallbackRegistry:GenerateCallbackEvents(events)
  self.Event = self.Event or {}
  for i = 1, #events do self.Event[events[i]] = events[i] end
end
function CallbackRegistry:RegisterCallback(event, func, owner, ...)
  if type(event) ~= "string" then error("CallbackRegistryMixin::RegisterCallback 'event' requires string type.", 2) end
  if type(func) ~= "function" then error("CallbackRegistryMixin::RegisterCallback 'func' requires function type.", 2) end
  if owner == nil then
    ownerSeq = ownerSeq + 1
    owner = ownerSeq
  elseif type(owner) == "number" then
    error("CallbackRegistryMixin:RegisterCallback 'owner' as number is reserved internally.", 2)
  end
  self.__callbacks = self.__callbacks or {}
  local list = self.__callbacks[event]
  if not list then list = {}; self.__callbacks[event] = list end
  for i = #list, 1, -1 do if list[i].owner == owner then tremove(list, i) end end
  list[#list + 1] = { owner = owner, func = func, args = { n = select("#", ...), ... } }
  return owner
end
function CallbackRegistry:UnregisterCallback(event, owner)
  local list = self.__callbacks and self.__callbacks[event]
  if not list then return end
  for i = #list, 1, -1 do if list[i].owner == owner then tremove(list, i) end end
end
-- Like CallbackRegistryMixin:TriggerEvent (Blizzard_SharedXMLBase/CallbackRegistry.lua L184): every
-- callback is called as func(owner, ...), with bound arguments first when given.
function CallbackRegistry:TriggerEvent(event, ...)
  if type(event) ~= "string" then error("CallbackRegistryMixin:TriggerEvent 'event' requires string type.", 2) end
  if not self.isUndefinedEventAllowed and self.Event and not self.Event[event] then
    error(string.format("CallbackRegistryMixin:TriggerEvent event '%s' doesn't exist.", event), 2)
  end
  local list = self.__callbacks and self.__callbacks[event]
  if not list then return end
  local snapshot = {}
  for i = 1, #list do snapshot[i] = list[i] end
  for i = 1, #snapshot do
    local cb = snapshot[i]
    if cb.args.n > 0 then
      local args = {}
      for j = 1, cb.args.n do args[j] = cb.args[j] end
      local n = cb.args.n
      local extra = { ... }
      for j = 1, select("#", ...) do args[n + j] = extra[j] end
      cb.func(cb.owner, unpack(args, 1, n + select("#", ...)))
    else
      cb.func(cb.owner, ...)
    end
  end
end
function CallbackRegistry:OnLoad() self.__callbacks = self.__callbacks or {} end

-- ------------------------------------------------------------ colours --

local Color = {}
local ColorMT = { __index = Color }
function Color:GetRGB() return self.r, self.g, self.b end
function Color:GetRGBA() return self.r, self.g, self.b, self.a end
function Color:WrapTextInColorCode(text)
  return string.format("|cff%02x%02x%02x%s|r", floor(self.r * 255), floor(self.g * 255), floor(self.b * 255), text)
end
function Color:GenerateHexColor()
  return string.format("ff%02x%02x%02x", floor(self.r * 255), floor(self.g * 255), floor(self.b * 255))
end
function CreateColor(r, g, b, a) return setmetatable({ r = r, g = g, b = b, a = a or 1 }, ColorMT) end
NORMAL_FONT_COLOR = CreateColor(1, 0.82, 0)
HIGHLIGHT_FONT_COLOR = CreateColor(1, 1, 1)
RED_FONT_COLOR = CreateColor(1, 0.125, 0.125)
GREEN_FONT_COLOR = CreateColor(0.1, 1, 0.1)
DISABLED_FONT_COLOR = CreateColor(0.5, 0.5, 0.5)
GRAY_FONT_COLOR = CreateColor(0.5, 0.5, 0.5)
WHITE_FONT_COLOR = CreateColor(1, 1, 1)
LIGHTGRAY_FONT_COLOR = CreateColor(0.75, 0.75, 0.75)

-- ------------------------------------------------------------ strings --

DELETE = "Delete"; CANCEL = "Cancel"; OKAY = "Okay"; ACCEPT = "Accept"; SEARCH = "Search"
CLOSE = "Close"; SAVE = "Save"; FILTER = "Filter"; YES = "Yes"; NO = "No"; DONE = "Done"
RESET = "Reset"; NEW = "New"; SETTINGS = "Settings"; LIST_DELIMITER = ", "

SOUNDKIT = SOUNDKIT or {}
SOUNDKIT.IG_CHARACTER_INFO_OPEN = 839
SOUNDKIT.IG_CHARACTER_INFO_CLOSE = 840
SOUNDKIT.IG_CHARACTER_INFO_TAB = 841
SOUNDKIT.IG_MAINMENU_OPEN = 850
SOUNDKIT.IG_MAINMENU_CLOSE = 851
SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON = 856
SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_OFF = 857
SOUNDKIT.U_CHAT_SCROLL_BUTTON = 1115

local basePlaySound = PlaySound
function PlaySound(kit, channel)
  U.sounds[#U.sounds + 1] = kit
  return basePlaySound(kit, channel)
end

function IsShiftKeyDown() return U.shift == true end
function IsControlKeyDown() return false end
function IsAltKeyDown() return false end
function SetCursor(c) end
function GetCursorPosition() return 0, 0 end
function ReloadUI() U.reloads = U.reloads + 1 end
function nop() end
function GenerateClosure(fn, ...)
  local bound = { n = select("#", ...), ... }
  return function(...)
    local args = {}
    for i = 1, bound.n do args[i] = bound[i] end
    local extra = { ... }
    for i = 1, select("#", ...) do args[bound.n + i] = extra[i] end
    return fn(unpack(args, 1, bound.n + select("#", ...)))
  end
end

-- Protected panel functions: addon code must not use them (DESIGN §4.9). In combat the client
-- refuses ("Interface action blocked"), UIParentPanelManager.lua L853-L861.
function ShowUIPanel(frame)
  forbid("ShowUIPanel", frame and frame.GetName and frame:GetName())
  if InCombatLockdown() then
    U.blocked[#U.blocked + 1] = "ShowUIPanel"
    return
  end
  if frame then frame:Show() end
end
function HideUIPanel(frame)
  forbid("HideUIPanel", frame and frame.GetName and frame:GetName())
  if InCombatLockdown() then
    U.blocked[#U.blocked + 1] = "HideUIPanel"
    return
  end
  if frame then frame:Hide() end
end
function CopyToClipboard()
  forbid("CopyToClipboard")
  error("CopyToClipboard is protected: addons cannot write the clipboard", 2)
end

-- UISpecialFrames / CloseSpecialWindows (Blizzard_UIParentPanelManager/Shared/UIParentPanelManager.lua
-- L1104-L1116): Escape hides every shown frame whose global name is listed.
UISpecialFrames = {}
function CloseSpecialWindows()
  local found
  for _, value in pairs(UISpecialFrames) do
    local frame = _G[value]
    if frame and frame:IsShown() then
      frame:Hide()
      found = 1
    end
  end
  return found
end

-- C_UIFileAsset.IsKnownFile (Blizzard_APIDocumentationGenerated/UIFileAssetAPIDocumentation.lua)
SHIM.state.knownFiles = SHIM.state.knownFiles or {}
C_UIFileAsset = {}
function C_UIFileAsset.IsKnownFile(asset)
  if type(asset) ~= "string" and type(asset) ~= "number" then error("IsKnownFile needs an asset", 2) end
  return SHIM.state.knownFiles[asset] == true
end

-- C_Map.GetMapChildrenInfo(uiMapID, mapType, allDescendants) (MapDocumentation.lua) from SHIM.maps.
function C_Map.GetMapChildrenInfo(uiMapID, mapType, allDescendants)
  local out = {}
  local function walk(parent)
    for id, m in pairs(SHIM.maps) do
      if m.parentMapID == parent then
        if mapType == nil or m.mapType == mapType then
          out[#out + 1] = { mapID = id, name = m.name, mapType = m.mapType, parentMapID = m.parentMapID, flags = 0 }
        end
        if allDescendants then walk(id) end
      end
    end
  end
  walk(uiMapID)
  table.sort(out, function(a, b) return a.mapID < b.mapID end)
  return out
end

-- ------------------------------------------------------------ tooltip --

GameTooltip = newObject("Frame", "GameTooltip", UIParent)
GameTooltip:Hide()
GameTooltip.__lines = {}
local TT = {}
function TT:SetOwner(owner, anchor, x, y)
  self.__owner = owner
  self.__anchor = anchor
  self.__lines = {}
end
function TT:GetOwner() return self.__owner end
function TT:IsOwned(owner) return self.__owner == owner and self:IsShown() end
function TT:ClearLines() self.__lines = {} end
function TT:AddLine(text, r, g, b, wrap)
  if text == nil then text = "" end
  checkEscapes("GameTooltip", tostring(text))
  self.__lines[#self.__lines + 1] = { text = tostring(text), r = r, g = g, b = b, wrap = wrap }
end
function TT:AddDoubleLine(left, right, ...)
  checkEscapes("GameTooltip", tostring(left))
  checkEscapes("GameTooltip", tostring(right))
  self.__lines[#self.__lines + 1] = { text = tostring(left), right = tostring(right) }
end
function TT:SetText(text, r, g, b, a, wrap)
  self.__lines = {}
  self:AddLine(text, r, g, b, wrap)
end
function TT:NumLines() return #self.__lines end
function TT:SetMinimumWidth(w) end
function TT:SetPadding() end
mix(GameTooltip, TT)

function GetAppropriateTooltip() return GameTooltip end
function GameTooltip_AddColoredLine(tooltip, text, color, wrap, leftOffset)
  local r, g, b = color:GetRGB()
  if wrap == nil then wrap = true end
  tooltip:AddLine(text, r, g, b, wrap, leftOffset)
end
function GameTooltip_SetTitle(tooltip, text, overrideColor, wrap)
  tooltip:ClearLines()
  GameTooltip_AddColoredLine(tooltip, text, overrideColor or HIGHLIGHT_FONT_COLOR, wrap)
end
function GameTooltip_AddNormalLine(tooltip, text, wrap, o) GameTooltip_AddColoredLine(tooltip, text, NORMAL_FONT_COLOR, wrap, o) end
function GameTooltip_AddHighlightLine(tooltip, text, wrap, o) GameTooltip_AddColoredLine(tooltip, text, HIGHLIGHT_FONT_COLOR, wrap, o) end
function GameTooltip_AddInstructionLine(tooltip, text, wrap, o) GameTooltip_AddColoredLine(tooltip, text, GREEN_FONT_COLOR, wrap, o) end
function GameTooltip_AddErrorLine(tooltip, text, wrap, o) GameTooltip_AddColoredLine(tooltip, text, RED_FONT_COLOR, wrap, o) end
function GameTooltip_AddDisabledLine(tooltip, text, wrap, o) GameTooltip_AddColoredLine(tooltip, text, DISABLED_FONT_COLOR, wrap, o) end
function GameTooltip_AddBlankLineToTooltip(tooltip) tooltip:AddLine(" ") end
function GameTooltip_ShowDisabledTooltip(tooltip, owner, text, anchor)
  tooltip:SetOwner(owner, anchor)
  GameTooltip_SetTitle(tooltip, text, RED_FONT_COLOR, true)
  tooltip:Show()
end
function GameTooltip_Hide() GameTooltip:Hide() end

function U.TooltipLines()
  local out = {}
  if not GameTooltip:IsShown() then return out end
  for i = 1, #GameTooltip.__lines do out[i] = GameTooltip.__lines[i].text end
  return out
end

-- -------------------------------------------------------- template kit --

-- ButtonFrameTemplate (SharedUIPanelTemplates.xml L566-L721; PortraitFrame.lua L1-L96).
local PortraitFrameMixin = {}
function PortraitFrameMixin:GetTitleText() return self.TitleContainer.TitleText end
function PortraitFrameMixin:SetTitle(title) self:GetTitleText():SetText(title) end
function PortraitFrameMixin:SetTitleFormatted(fmt, ...) self:GetTitleText():SetFormattedText(fmt, ...) end
function PortraitFrameMixin:SetTitleOffsets(l, r)
  self.TitleContainer:SetPoint("TOPLEFT", self, "TOPLEFT", l or 58, -1)
  self.TitleContainer:SetPoint("TOPRIGHT", self, "TOPRIGHT", r or -24, -1)
end
function PortraitFrameMixin:GetPortrait() return self.PortraitContainer.portrait end
function PortraitFrameMixin:SetBorder(layoutName) self.__border = layoutName end
function PortraitFrameMixin:SetPortraitShown(shown) self.PortraitContainer:SetShown(shown) end
function PortraitFrameMixin:SetPortraitToAsset(t) self.PortraitContainer.portrait:SetTexture(t) end

-- UIPanelCloseButton_OnClick (Mainline/SharedUIPanelTemplates.lua L152): HideUIPanel(parent).
function UIPanelCloseButton_OnClick(self)
  local parent = self:GetParent()
  if parent then
    local continueHide = true
    if parent.onCloseCallback then continueHide = parent.onCloseCallback(self) end
    if continueHide then HideUIPanel(parent) end
  end
end

TEMPLATE_BUILD.UIPanelCloseButton = function(f)
  f:SetSize(24, 24)
  f:SetNormalAtlas("RedButton-Exit")
  f:SetPushedAtlas("RedButton-exit-pressed")
  f:SetHighlightAtlas("RedButton-Highlight")
  f:SetDisabledAtlas("RedButton-Exit-Disabled")
  f:SetFrameLevel(510)
  f:SetScript("OnClick", UIPanelCloseButton_OnClick)
end

TEMPLATE_BUILD.ButtonFrameTemplate = function(f)
  mix(f, PortraitFrameMixin)
  f.layoutType = "PortraitFrameTemplate"
  f:SetSize(338, 424)
  f.NineSlice = newObject("Frame", nil, f)
  f.PortraitContainer = newObject("Frame", nil, f)
  f.PortraitContainer:SetFrameLevel(400)
  f.PortraitContainer.portrait = f.PortraitContainer:CreateTexture(childName(f, "Portrait"), "OVERLAY")
  f.FrameGlow = newObject("Frame", nil, f)
  f.FrameGlow:Hide()
  f.TitleContainer = newObject("Frame", nil, f)
  f.TitleContainer:SetFrameLevel(510)
  f.TitleContainer:SetSize(0, 20)
  f.TitleContainer:SetPoint("TOPLEFT", f, "TOPLEFT", 58, -1)
  f.TitleContainer:SetPoint("TOPRIGHT", f, "TOPRIGHT", -24, -1)
  f.TitleContainer.TitleText = f.TitleContainer:CreateFontString(childName(f, "TitleText"), "OVERLAY", "GameFontNormal")
  f.Bg = f:CreateTexture(childName(f, "Bg"), "BACKGROUND")
  f.Bg:SetPoint("TOPLEFT", f, "TOPLEFT", 2, -21)
  f.Bg:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -2, 2)
  f.TopTileStreaks = f:CreateTexture(nil, "BORDER")
  f.CloseButton = newObject("Button", childName(f, "CloseButton"), f, "UIPanelCloseButtonDefaultAnchors")
  TEMPLATE_BUILD.UIPanelCloseButton(f.CloseButton)
  f.CloseButton:SetPoint("TOPRIGHT", f, "TOPRIGHT", 1, 0)
  f.Inset = newObject("Frame", childName(f, "Inset"), f, "InsetFrameTemplate")
  f.Inset:SetPoint("TOPLEFT", f, "TOPLEFT", 4, -60)
  f.Inset:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -6, 26)
  f.Inset.Bg = f.Inset:CreateTexture(nil, "BACKGROUND")
end

-- Mainline/SharedUIPanelTemplates.lua L111-L117 and L94-L109.
local function updateRegionAnchor(region, x)
  if region then
    local point, rel, relPoint, _, y = region:GetPointByName("TOPLEFT")
    if point then region:SetPoint(point, rel, relPoint, x, y) end
  end
end
function ButtonFrameTemplate_HidePortrait(self)
  self:SetBorder("ButtonFrameTemplateNoPortrait")
  self:SetPortraitShown(false)
  updateRegionAnchor(self.Bg, 7)
  updateRegionAnchor(self.Inset, 9)
  if self.TitleContainer then
    self.TitleContainer:SetPoint("TOPLEFT", self, "TOPLEFT", 0, -1)
    self.TitleContainer:SetPoint("TOPRIGHT", self, "TOPRIGHT", 0, -1)
  end
end
function ButtonFrameTemplate_ShowPortrait(self)
  self:SetBorder("PortraitFrameTemplate")
  self:SetPortraitShown(true)
  updateRegionAnchor(self.Bg, 2)
  updateRegionAnchor(self.Inset, 4)
end
function ButtonFrameTemplate_HideButtonBar(self)
  if self.Inset then self.Inset:SetPoint("BOTTOMRIGHT", self, "BOTTOMRIGHT", -6, 4) end
end
function ButtonFrameTemplate_ShowButtonBar(self)
  if self.Inset then self.Inset:SetPoint("BOTTOMRIGHT", self, "BOTTOMRIGHT", -6, 26) end
end

-- DisabledTooltipButtonMixin (SharedTooltipTemplates.lua L320-L348) + UIPanelButtonMixin (L223).
local DisabledTooltipButtonMixin = {}
function DisabledTooltipButtonMixin:SetDisabledTooltip(text, anchor)
  self.disabledTooltip = text
  self.disabledTooltipAnchor = anchor
end
function DisabledTooltipButtonMixin:GetDisabledTooltip() return self.disabledTooltip, self.disabledTooltipAnchor end
function DisabledTooltipButtonMixin:SetDisabledState(disabled, text, anchor)
  self:SetEnabled(not disabled)
  self:SetDisabledTooltip(text, anchor)
end
local UIPanelButtonMixin = {}
function UIPanelButtonMixin:OnEnter()
  if not self.tooltipDisabled then
    if self.tooltipText then
      GameTooltip:SetOwner(self, self.tooltipAnchor or "ANCHOR_RIGHT")
      GameTooltip_SetTitle(GameTooltip, self.tooltipText)
      GameTooltip:Show()
    elseif not self:IsEnabled() then
      local text, anchor = self:GetDisabledTooltip()
      if text ~= nil then GameTooltip_ShowDisabledTooltip(GameTooltip, self, text, anchor) end
    end
  end
end
function UIPanelButtonMixin:OnLeave() GameTooltip:Hide() end
function UIPanelButtonMixin:SetTextToFit(text)
  self:SetText(text)
  self:SetWidth(self:GetTextWidth() + 40)
end
function UIPanelButtonMixin:FitToText() self:SetWidth(self:GetTextWidth() + 40) end

TEMPLATE_BUILD.UIPanelButtonTemplate = function(f)
  mix(f, DisabledTooltipButtonMixin)
  mix(f, UIPanelButtonMixin)
  f:SetSize(40, 22)
  f.Left = f:CreateTexture(nil, "BACKGROUND")
  f.Right = f:CreateTexture(nil, "BACKGROUND")
  f.Middle = f:CreateTexture(nil, "BACKGROUND")
  f.Text = f:CreateFontString(childName(f, "Text"), "ARTWORK", "GameFontNormal")
  f:SetFontString(f.Text)
  f:SetScript("OnEnter", f.OnEnter)
  f:SetScript("OnLeave", f.OnLeave)
end

-- PanelTabButtonTemplate (SharedUIPanelTemplates.xml L932-L1004; PanelTabButtonMixin L251-L281).
local PanelTabButtonMixin = {}
function PanelTabButtonMixin:OnLoad()
  self:SetFrameLevel(self:GetFrameLevel() + 4)
  self:RegisterEvent("DISPLAY_SIZE_CHANGED")
end
function PanelTabButtonMixin:OnEvent()
  if self:IsVisible() then
    local p = self:GetParent()
    PanelTemplates_TabResize(self, p.tabPadding, nil, p.minTabWidth, p.maxTabWidth)
  end
end
function PanelTabButtonMixin:OnShow()
  local p = self:GetParent()
  PanelTemplates_TabResize(self, p.tabPadding, nil, p.minTabWidth, p.maxTabWidth)
end
function PanelTabButtonMixin:OnEnter() end
function PanelTabButtonMixin:OnLeave() GameTooltip:Hide() end

TEMPLATE_BUILD.PanelTabButtonTemplate = function(f)
  mix(f, PanelTabButtonMixin)
  f:SetSize(10, 32)
  for _, key in ipairs({ "LeftActive", "RightActive", "MiddleActive", "Left", "Right", "Middle",
                         "LeftHighlight", "MiddleHighlight", "RightHighlight" }) do
    f[key] = f:CreateTexture(nil, "BACKGROUND")
    f[key]:SetWidth(8)
  end
  f.TabTextures = { f.LeftActive, f.RightActive, f.MiddleActive, f.Left, f.Right, f.Middle }
  f.Text = f:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
  f.Text:SetPoint("CENTER", f, "CENTER", 0, 2)
  f:SetFontString(f.Text)
  f:SetScript("OnEvent", f.OnEvent)
  f:SetScript("OnShow", f.OnShow)
  f:SetScript("OnEnter", f.OnEnter)
  f:SetScript("OnLeave", f.OnLeave)
  f:OnLoad()
  -- parentArray="Tabs"
  local p = f:GetParent()
  if p then
    p.Tabs = p.Tabs or {}
    p.Tabs[#p.Tabs + 1] = f
  end
end

-- PanelTemplates_* (Mainline/SharedUIPanelTemplates.lua L437-L670).
local function tabByIndex(frame, index)
  return (frame.Tabs and frame.Tabs[index]) or _G[(frame:GetName() or "") .. "Tab" .. index]
end
function PanelTemplates_Tab_OnClick(self, frame) PanelTemplates_SetTab(frame, self:GetID()) end
function PanelTemplates_SetTab(frame, id)
  frame.selectedTab = id
  PanelTemplates_UpdateTabs(frame)
end
function PanelTemplates_GetSelectedTab(frame) return frame.selectedTab end
function PanelTemplates_UpdateTabs(frame)
  if frame.selectedTab then
    for i = 1, frame.numTabs do
      local tab = tabByIndex(frame, i)
      if tab.isDisabled then
        PanelTemplates_SetDisabledTabState(tab)
      elseif i == frame.selectedTab then
        PanelTemplates_SelectTab(tab)
      else
        PanelTemplates_DeselectTab(tab)
      end
    end
  end
end
function PanelTemplates_TabResize(tab, padding, absoluteSize, minWidth, maxWidth)
  local textWidth = tab.Text:GetStringWidth()
  local width = textWidth + 20 + (padding or 0)
  local sideWidths = tab.Left:GetWidth() + tab.Right:GetWidth()
  minWidth = minWidth or sideWidths
  if absoluteSize then
    width = max(absoluteSize, sideWidths)
  elseif maxWidth and width > maxWidth then
    width = maxWidth
  elseif minWidth and width < minWidth then
    width = minWidth
  end
  tab:SetWidth(width)
end
function PanelTemplates_SetNumTabs(frame, numTabs)
  frame.numTabs = numTabs
  PanelTemplates_AnchorTabs(frame)
end
function PanelTemplates_AnchorTabs(frame)
  for i = 2, frame.numTabs do
    tabByIndex(frame, i):SetPoint("TOPLEFT", tabByIndex(frame, i - 1), "TOPRIGHT", 3, 0)
  end
end
function PanelTemplates_DeselectTab(tab)
  tab.Left:Show(); tab.Middle:Show(); tab.Right:Show()
  tab:Enable()
  tab.LeftActive:Hide(); tab.MiddleActive:Hide(); tab.RightActive:Hide()
  tab.__selected = false
end
function PanelTemplates_SelectTab(tab)
  tab.Left:Hide(); tab.Middle:Hide(); tab.Right:Hide()
  tab:Disable()
  tab:SetDisabledFontObject(GameFontHighlightSmall)
  tab.LeftActive:Show(); tab.MiddleActive:Show(); tab.RightActive:Show()
  tab.__selected = true
  if GameTooltip:IsOwned(tab) then GameTooltip:Hide() end
end
function PanelTemplates_SetDisabledTabState(tab)
  tab:Disable()
  tab:SetDisabledFontObject(GameFontDisableSmall)
  tab.__selected = false
end
function PanelTemplates_DisableTab(frame, index)
  tabByIndex(frame, index).isDisabled = 1
  PanelTemplates_UpdateTabs(frame)
end
function PanelTemplates_EnableTab(frame, index)
  tabByIndex(frame, index).isDisabled = nil
  PanelTemplates_UpdateTabs(frame)
end
function PanelTemplates_SetTabEnabled(frame, index, enabled)
  if enabled then PanelTemplates_EnableTab(frame, index) else PanelTemplates_DisableTab(frame, index) end
end
function PanelTemplates_ResizeTabsToFit(frame, maxWidth) end

-- PanelResizeButtonTemplate (L1642) + PanelResizeButtonMixin (Mainline/SharedUIPanelTemplates.lua L1635-L1778).
local PanelResizeButtonMixin = {}
function PanelResizeButtonMixin:Init(target, minWidth, minHeight, maxWidth, maxHeight)
  self.target = target
  self.minWidth, self.minHeight, self.maxWidth, self.maxHeight = minWidth, minHeight, maxWidth, maxHeight
  local orig = target:GetScript("OnSizeChanged") or function() end
  local button = self
  target:SetScript("OnSizeChanged", function(t, width, height)
    orig(t, width, height)
    if not button.isActive then return end
    local w, h = width, height
    if w < button.minWidth then w = button.minWidth; t:SetWidth(w)
    elseif button.maxWidth and w > button.maxWidth then w = button.maxWidth; t:SetWidth(w) end
    if h < button.minHeight then h = button.minHeight; t:SetHeight(h)
    elseif button.maxHeight and h > button.maxHeight then h = button.maxHeight; t:SetHeight(h) end
    if button.resizeCallback then button.resizeCallback(button, w, h, button.isActive) end
  end)
end
function PanelResizeButtonMixin:OnMouseDown()
  if not self:IsEnabled() then return end
  self.isActive = true
  if self.target then self.target:StartSizing("BOTTOMRIGHT", true) end
end
function PanelResizeButtonMixin:OnMouseUp()
  self.isActive = false
  if not self.target then return end
  self.target:StopMovingOrSizing()
  if self.resizeStoppedCallback then self.resizeStoppedCallback(self.target) end
end
function PanelResizeButtonMixin:OnEnter() end
function PanelResizeButtonMixin:OnLeave() end
function PanelResizeButtonMixin:IsActive() return self.isActive == true end
function PanelResizeButtonMixin:SetOnResizeStoppedCallback(cb) self.resizeStoppedCallback = cb end
function PanelResizeButtonMixin:SetOnResizeCallback(cb) self.resizeCallback = cb end
function PanelResizeButtonMixin:SetMinWidth(v) self.minWidth = v end
function PanelResizeButtonMixin:SetMinHeight(v) self.minHeight = v end
function PanelResizeButtonMixin:SetMaxWidth(v) self.maxWidth = v end
function PanelResizeButtonMixin:SetMaxHeight(v) self.maxHeight = v end

TEMPLATE_BUILD.PanelResizeButtonTemplate = function(f)
  mix(f, PanelResizeButtonMixin)
  f:SetSize(16, 16)
  f:SetNormalTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
  f:SetHighlightTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight")
  f:SetPushedTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Down")
  f:SetScript("OnEnter", f.OnEnter)
  f:SetScript("OnLeave", f.OnLeave)
  f:SetScript("OnMouseDown", f.OnMouseDown)
  f:SetScript("OnMouseUp", f.OnMouseUp)
end

-- Check / radio buttons (CheckButtonTemplates.xml L4-L64).
TEMPLATE_BUILD.UICheckButtonTemplate = function(f)
  f:SetSize(32, 32)
  f:SetNormalTexture("Interface\\Buttons\\UI-CheckBox-Up")
  f:SetPushedTexture("Interface\\Buttons\\UI-CheckBox-Down")
  f:SetHighlightTexture("Interface\\Buttons\\UI-CheckBox-Highlight")
  f:SetCheckedTexture("Interface\\Buttons\\UI-CheckBox-Check")
  f:SetDisabledCheckedTexture("Interface\\Buttons\\UI-CheckBox-Check-Disabled")
  f.Text = f:CreateFontString(childName(f, "Text"), "ARTWORK", "GameFontNormalSmall")
  f.Text:SetPoint("LEFT", f, "RIGHT", -2, 0)
  f.text = f.Text      -- UICheckButtonFontString_SetParentKeyAlias (Mainline/SharedUIPanelTemplates.lua L1088)
end
TEMPLATE_BUILD.UIRadioButtonTemplate = function(f)
  f:SetSize(16, 16)
  f.text = f:CreateFontString(childName(f, "Text"), "BACKGROUND", "GameFontNormalSmall")   -- lowercase only
  f.text:SetPoint("LEFT", f, "RIGHT", 5, 0)
  f:SetNormalTexture("Interface\\Buttons\\UI-RadioButton")
  f:SetHighlightTexture("Interface\\Buttons\\UI-RadioButton")
  f:SetCheckedTexture("Interface\\Buttons\\UI-RadioButton")
end

-- Input boxes (InputBoxTemplates.xml L4-L70, L180-L256; InputBoxTemplates.lua).
function EditBox_OnTabPressed(self)
  if self.previousEditBox and IsShiftKeyDown() then
    self.previousEditBox:SetFocus()
  elseif self.nextEditBox then
    self.nextEditBox:SetFocus()
  end
end
function EditBox_ClearFocus(self) self:ClearFocus() end
function EditBox_HighlightText(self) self:HighlightText() end
function EditBox_ClearHighlight(self) self:HighlightText(0, 0) end
function InputBoxInstructions_OnTextChanged(self) self.Instructions:SetShown(self:GetText() == "") end
function SearchBoxTemplate_OnLoad(self)
  self.searchIcon:SetVertexColor(0.6, 0.6, 0.6)
  self.Instructions:SetText(self.instructionText)
end
function SearchBoxTemplate_OnEditFocusLost(self)
  if self:GetText() == "" then
    self.searchIcon:SetVertexColor(0.6, 0.6, 0.6)
    self.clearButton:Hide()
  end
end
function SearchBoxTemplate_OnEditFocusGained(self)
  self.searchIcon:SetVertexColor(1, 1, 1)
  self.clearButton:Show()
end
function SearchBoxTemplate_OnTextChanged(self)
  if not self:HasFocus() and self:GetText() == "" then
    self.searchIcon:SetVertexColor(0.6, 0.6, 0.6)
    self.clearButton:Hide()
  else
    self.searchIcon:SetVertexColor(1, 1, 1)
    self.clearButton:Show()
  end
  InputBoxInstructions_OnTextChanged(self)
end
function SearchBoxTemplate_ClearText(self)
  self:SetText("")
  self:ClearFocus()
end
function SearchBoxTemplateClearButton_OnClick(self)
  PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
  SearchBoxTemplate_ClearText(self:GetParent())
end

local function buildInputBoxScript(f)
  f:SetScript("OnTabPressed", EditBox_OnTabPressed)
  f:SetScript("OnEscapePressed", EditBox_ClearFocus)
  f:SetScript("OnEditFocusLost", EditBox_ClearHighlight)
  f:SetScript("OnEditFocusGained", EditBox_HighlightText)
end

TEMPLATE_BUILD.InputBoxInstructionsTemplate = function(f)
  buildInputBoxScript(f)
  f.Left = f:CreateTexture(nil, "BACKGROUND")
  f.Right = f:CreateTexture(nil, "BACKGROUND")
  f.Middle = f:CreateTexture(nil, "BACKGROUND")
  f.Instructions = f:CreateFontString(nil, "ARTWORK", "GameFontDisableSmall")
  f.Instructions:SetPoint("TOPLEFT", f, "TOPLEFT", 16, 0)
  f:SetFontObject("GameFontHighlightSmall")
  f:SetScript("OnTextChanged", InputBoxInstructions_OnTextChanged)
end

TEMPLATE_BUILD.SearchBoxTemplate = function(f)
  TEMPLATE_BUILD.InputBoxInstructionsTemplate(f)
  f.__autoFocus = false      -- autoFocus="false" in the XML
  f.instructionText = SEARCH
  f.searchIcon = f:CreateTexture(childName(f, "SearchIcon"), "OVERLAY")
  f.clearButton = newObject("Button", childName(f, "ClearButton"), f)
  f.clearButton:Hide()
  f.clearButton:SetScript("OnClick", SearchBoxTemplateClearButton_OnClick)
  f:SetScript("OnEscapePressed", EditBox_ClearFocus)
  f:SetScript("OnEnterPressed", EditBox_ClearFocus)
  f:SetScript("OnEditFocusLost", SearchBoxTemplate_OnEditFocusLost)
  f:SetScript("OnEditFocusGained", SearchBoxTemplate_OnEditFocusGained)
  f:SetScript("OnTextChanged", SearchBoxTemplate_OnTextChanged)
  SearchBoxTemplate_OnLoad(f)
end

-- -------------------------------------------------------------- ScrollBox --

ScrollBoxConstants = {
  UpdateQueued = false, UpdateImmediately = true, NoScrollInterpolation = true,
  RetainScrollPosition = true, DiscardScrollPosition = false,
  AlignBegin = 0, AlignCenter = 0.5, AlignEnd = 1, AlignNearest = -1,
}
BaseScrollBoxEvents = { OnScroll = "OnScroll", OnSizeChanged = "OnSizeChanged",
  OnAllowScrollChanged = "OnAllowScrollChanged", OnLayout = "OnLayout" }

-- DataProvider (Blizzard_SharedXML/DataProvider.lua L1-L290).
local DataProvider = {}
local function dpChanged(dp)
  local owners = dp.__owners
  for i = 1, #owners do owners[i]:__Layout() end
end
function DataProvider:Enumerate() return ipairs(self.collection) end
function DataProvider:EnumerateEntireRange() return ipairs(self.collection) end
function DataProvider:GetCollection() return self.collection end
function DataProvider:GetSize() return #self.collection end
function DataProvider:IsEmpty() return #self.collection == 0 end
function DataProvider:Insert(...)
  for i = 1, select("#", ...) do self.collection[#self.collection + 1] = (select(i, ...)) end
  if self.sortComparator then table.sort(self.collection, self.sortComparator) end
  dpChanged(self)
end
function DataProvider:InsertTable(tbl)
  for i = 1, #tbl do self.collection[#self.collection + 1] = tbl[i] end
  if self.sortComparator then table.sort(self.collection, self.sortComparator) end
  dpChanged(self)
end
function DataProvider:InsertAtIndex(elementData, index) tinsert(self.collection, index, elementData); dpChanged(self) end
function DataProvider:Remove(...)
  for i = 1, select("#", ...) do
    local e = select(i, ...)
    for j = #self.collection, 1, -1 do if self.collection[j] == e then tremove(self.collection, j) end end
  end
  dpChanged(self)
end
function DataProvider:RemoveIndex(index) tremove(self.collection, index); dpChanged(self) end
function DataProvider:RemoveByPredicate(pred)
  for j = #self.collection, 1, -1 do
    if pred(self.collection[j]) then tremove(self.collection, j); break end
  end
  dpChanged(self)
end
function DataProvider:Flush() self.collection = {}; dpChanged(self) end
function DataProvider:SetSortComparator(cmp, skipSort)
  self.sortComparator = cmp
  if not skipSort then self:Sort() end
end
function DataProvider:ClearSortComparator() self.sortComparator = nil end
function DataProvider:HasSortComparator() return self.sortComparator ~= nil end
function DataProvider:Sort()
  if self.sortComparator then table.sort(self.collection, self.sortComparator) end
  dpChanged(self)
end
function DataProvider:Find(index) return self.collection[index] end
function DataProvider:FindIndex(e)
  for i = 1, #self.collection do if self.collection[i] == e then return i, e end end
  return nil
end
function DataProvider:FindByPredicate(pred)
  for i = 1, #self.collection do if pred(self.collection[i]) then return i, self.collection[i] end end
  return nil
end
function DataProvider:FindElementDataByPredicate(pred)
  local _, e = self:FindByPredicate(pred)
  return e
end
function DataProvider:FindIndexByPredicate(pred) return (self:FindByPredicate(pred)) end
function DataProvider:ContainsByPredicate(pred) return self:FindByPredicate(pred) ~= nil end
function DataProvider:ForEach(fn) for i = 1, #self.collection do fn(self.collection[i]) end end
function DataProvider:RegisterCallback() end
function DataProvider:UnregisterCallback() end

function CreateDataProvider(tbl)
  local dp = mix({ collection = {}, __owners = {}, __kind = "DataProvider" }, DataProvider)
  if tbl then for i = 1, #tbl do dp.collection[i] = tbl[i] end end
  return dp
end

-- Linear list view (ScrollBoxLinearView.lua L246; ScrollBoxListView.lua).
local ListView = {}
function ListView:SetElementInitializer(frameTemplateOrFrameType, initializer)
  self.frameType = frameTemplateOrFrameType
  self.initializer = initializer
  self.elementFactory = function(factory, elementData) factory(frameTemplateOrFrameType, initializer) end
end
function ListView:SetElementFactory(fn) self.elementFactory = fn end
function ListView:SetElementExtent(extent) self.elementExtent = max(extent, 1) end
function ListView:HasElementExtent() return self.elementExtent ~= nil end
function ListView:GetElementExtent() return self.elementExtent end
function ListView:SetElementExtentCalculator(fn) self.extentCalculator = fn end
function ListView:SetPadding(top, bottom, left, right, spacing) self.padding = { top, bottom, left, right, spacing } end
function ListView:SetPanExtent(e) self.panExtent = e end
function ListView:SetElementResetter(fn) self.resetter = fn end
function ListView:SetScrollBox(sb) self.scrollBox = sb end
function ListView:GetScrollBox() return self.scrollBox end
function ListView:GetDataProvider() return self.dataProvider end
function ListView:FindFrame(elementData)
  for i = 1, #self.frames do
    local f = self.frames[i]
    if f:IsShown() and f.__elementData == elementData then return f end
  end
  return nil
end
function ListView:ForEachFrame(fn)
  for i = 1, #self.frames do
    local f = self.frames[i]
    if f:IsShown() and f.__elementData ~= nil then fn(f, f.__elementData) end
  end
end
function ListView:GetFrames()
  local out = {}
  self:ForEachFrame(function(f) out[#out + 1] = f end)
  return out
end
function ListView:Flush() end

function CreateScrollBoxListLinearView(top, bottom, left, right, spacing)
  local v = mix({ frames = {}, __kind = "ScrollBoxListLinearView" }, ListView)
  v:SetPadding(top or 0, bottom or 0, left or 0, right or 0, spacing or 0)
  return v
end

local ScrollBoxListMixin = {}
function ScrollBoxListMixin:Init(view)
  if type(view) ~= "table" or type(view.SetScrollBox) ~= "function" then
    error("ScrollBoxBaseMixin:SetView requires a valid view.", 2)
  end
  self.view = view
  view:SetScrollBox(self)
end
function ScrollBoxListMixin:GetView() return self.view end
function ScrollBoxListMixin:GetScrollTarget() return self.ScrollTarget end
function ScrollBoxListMixin:SetDataProvider(dp, retainScrollPosition)
  if not self.view then error("A view is required before assigning the data provider.", 2) end
  if dp == nil then error("SetDataProvider() dataProvider was nil. Call RemoveDataProvider() if this was your intent.", 2) end
  if self.view.elementFactory == nil then
    error("SetDataProvider() elementFactory was nil. Call SetElementFactory() before setting the data provider.", 2)
  end
  local old = self.view.dataProvider
  if old and old ~= dp then
    for i = #old.__owners, 1, -1 do if old.__owners[i] == self then tremove(old.__owners, i) end end
  end
  self.view.dataProvider = dp
  local owned = false
  for i = 1, #dp.__owners do if dp.__owners[i] == self then owned = true end end
  if not owned then dp.__owners[#dp.__owners + 1] = self end
  self.__retained = retainScrollPosition and true or false
  if not retainScrollPosition then self.__scrolledTo = nil end
  self:__Layout()
end
function ScrollBoxListMixin:GetDataProvider() return self.view and self.view.dataProvider end
function ScrollBoxListMixin:HasDataProvider() return self:GetDataProvider() ~= nil end
function ScrollBoxListMixin:RemoveDataProvider()
  if self.view then self.view.dataProvider = nil end
  self:__Layout()
end
function ScrollBoxListMixin:FlushDataProvider()
  local dp = self:GetDataProvider()
  if dp then dp:Flush() end
end
function ScrollBoxListMixin:Rebuild(retain) self:SetDataProvider(self:GetDataProvider(), retain) end
function ScrollBoxListMixin:FullUpdate() self:__Layout() end
function ScrollBoxListMixin:ScrollToBegin() self.__scrolledTo = "begin" end
function ScrollBoxListMixin:ScrollToEnd() self.__scrolledTo = "end" end
function ScrollBoxListMixin:ScrollToElementData(elementData, alignment) self.__scrolledTo = elementData end
function ScrollBoxListMixin:ScrollToElementDataIndex(index, alignment)
  local dp = self:GetDataProvider()
  self.__scrolledTo = dp and dp:Find(index)
end
function ScrollBoxListMixin:ScrollToElementDataByPredicate(pred, alignment)
  local dp = self:GetDataProvider()
  self.__scrolledTo = dp and dp:FindElementDataByPredicate(pred)
end
function ScrollBoxListMixin:FindFrame(elementData) return self.view:FindFrame(elementData) end
function ScrollBoxListMixin:ForEachFrame(fn) return self.view:ForEachFrame(fn) end
function ScrollBoxListMixin:EnumerateFrames() return ipairs(self.view:GetFrames()) end
function ScrollBoxListMixin:FindElementDataByPredicate(pred)
  local dp = self:GetDataProvider()
  return dp and dp:FindElementDataByPredicate(pred)
end
function ScrollBoxListMixin:SetInterpolateScroll(v) end
function ScrollBoxListMixin:SetPanExtent(e) end
function ScrollBoxListMixin:GetPadding() return { SetBottom = function() end } end
function ScrollBoxListMixin:SetAlignmentOverlapIgnored(v) end
function ScrollBoxListMixin:HasScrollableExtent() return false end
function ScrollBoxListMixin:GetFrames() return self.view and self.view:GetFrames() or {} end

-- Non-virtualized layout: one pooled frame per element, reused across rebuilds in index order
-- (like the client, a frame is re-initialized for a different element), frames past the end
-- are hidden.
function ScrollBoxListMixin:__Layout()
  local view = self.view
  if not view then return end
  local dp = view.dataProvider
  local n = dp and #dp.collection or 0
  if n > 0 and not view.elementExtent and not view.extentCalculator then
    -- ScrollBoxListView.lua L41-L52: a Lua-only frame type has no template extent.
    error("Failed to assign an explicit element extent or set an extent calculator.", 2)
  end
  local target = self.ScrollTarget
  local y = 0
  local pad = view.padding or {}
  for i = 1, n do
    local elementData = dp.collection[i]
    local frame = view.frames[i]
    local chosenType, chosenInit
    view.elementFactory(function(frameTypeOrTemplate, initializer)
      chosenType, chosenInit = frameTypeOrTemplate, initializer
    end, elementData)
    if not chosenType then error("ScrollBox element factory did not call the factory", 2) end
    if frame and frame.__poolType ~= chosenType then
      frame:Hide()
      frame = nil
    end
    if not frame then
      local template
      local frameType = chosenType
      if U.ALLOWED_TEMPLATES[chosenType] then
        template = chosenType
        frameType = U.ALLOWED_TEMPLATES[chosenType]
      end
      frame = CreateFrame(frameType, nil, target, template)
      frame.__poolType = chosenType
      view.frames[i] = frame
    end
    frame.__elementData = elementData
    frame.__elementIndex = i
    frame.GetElementData = function() return elementData end
    frame.GetData = frame.GetElementData
    frame.GetElementDataIndex = function() return i end
    local extent = view.elementExtent or (view.extentCalculator and view.extentCalculator(i, elementData)) or 0
    frame:ClearAllPoints()
    frame:SetPoint("TOPLEFT", target, "TOPLEFT", pad[3] or 0, -y)
    frame:SetPoint("TOPRIGHT", target, "TOPRIGHT", -(pad[4] or 0), -y)
    frame:SetHeight(extent)
    frame:Show()
    y = y + extent + (pad[5] or 0)
    if chosenInit then chosenInit(frame, elementData) end
  end
  for i = n + 1, #view.frames do
    local f = view.frames[i]
    if f.__elementData ~= nil and view.resetter then view.resetter(f, f.__elementData) end
    f.__elementData = nil
    f:Hide()
  end
  self.__layouts = (self.__layouts or 0) + 1
end

TEMPLATE_BUILD.WowScrollBoxList = function(f)
  mix(f, CallbackRegistry)
  f.Event = { OnScroll = "OnScroll", OnSizeChanged = "OnSizeChanged", OnLayout = "OnLayout",
    OnAcquiredFrame = "OnAcquiredFrame", OnInitializedFrame = "OnInitializedFrame",
    OnReleasedFrame = "OnReleasedFrame", OnDataRangeChanged = "OnDataRangeChanged",
    OnDataProviderReassigned = "OnDataProviderReassigned" }
  mix(f, ScrollBoxListMixin)
  f.ScrollTarget = newObject("Frame", nil, f)
end

-- MinimalScrollBar (MinimalScrollBar.xml L15; ScrollBar.lua).
local ScrollBarMixin = {}
function ScrollBarMixin:Init(visible, pan) self.__visible, self.__pan = visible, pan end
function ScrollBarMixin:SetScrollPercentage(p) self.__pct = p end
function ScrollBarMixin:GetScrollPercentage() return self.__pct or 0 end
function ScrollBarMixin:SetVisibleExtentPercentage(p) self.__visible = p end
function ScrollBarMixin:SetPanExtentPercentage(p) self.__pan = p end
function ScrollBarMixin:ScrollStepInDirection(d) end
function ScrollBarMixin:SetHideIfUnscrollable(v) self.__hideIfUnscrollable = v end
function ScrollBarMixin:SetHideTrackIfThumbExceedsTrack(v) end
function ScrollBarMixin:HasScrollableExtent() return false end

TEMPLATE_BUILD.MinimalScrollBar = function(f)
  mix(f, CallbackRegistry)
  f.Event = { OnScroll = "OnScroll", OnAllowScrollChanged = "OnAllowScrollChanged" }
  mix(f, ScrollBarMixin)
  f:SetSize(8, 560)
  f.Track = newObject("Frame", nil, f)
  f.Track.Thumb = newObject("Frame", nil, f.Track)
  f.Back = newObject("Button", nil, f)
  f.Forward = newObject("Button", nil, f)
end

ScrollUtil = {}
local function bindBar(scrollBox, scrollBar)
  if type(scrollBar) ~= "table" or scrollBar.__template ~= "MinimalScrollBar" then
    shimError("ScrollUtil: the scroll bar must be a MinimalScrollBar", 3)
  end
  scrollBox.__scrollBar = scrollBar
  scrollBar.__scrollBox = scrollBox
end
-- ScrollUtil.lua L137 / L144 / L149 / L215.
function ScrollUtil.InitScrollBoxListWithScrollBar(scrollBox, scrollBar, view)
  bindBar(scrollBox, scrollBar)
  scrollBox:Init(view)
end
function ScrollUtil.InitScrollBoxWithScrollBar(scrollBox, scrollBar, view)
  bindBar(scrollBox, scrollBar)
  scrollBox:Init(view)
end
function ScrollUtil.RegisterScrollBoxWithScrollBar(scrollBox, scrollBar)
  bindBar(scrollBox, scrollBar)
end
function ScrollUtil.InitScrollFrameWithScrollBar(scrollFrame, scrollBar)
  if scrollFrame.__kind ~= "ScrollFrame" then shimError("InitScrollFrameWithScrollBar needs a ScrollFrame", 2) end
  bindBar(scrollFrame, scrollBar)
  scrollFrame:SetScript("OnVerticalScroll", function(sf, offset)
    local range = sf:GetVerticalScrollRange()
    scrollBar:SetScrollPercentage(range > 0 and offset / range or 0)
  end)
  scrollFrame:SetScript("OnScrollRangeChanged", function() end)
  scrollFrame:SetScript("OnMouseWheel", function() end)
  scrollFrame.GetPanExtent = function(self) return self.panExtent end
  scrollFrame.SetPanExtent = function(self, e) self.panExtent = e end
  scrollFrame:SetPanExtent(30)
end

-- ScrollingEditBoxTemplate (ScrollTemplates.xml L21-L42; ScrollTemplates.lua L1-L260) and its
-- EventEditBox (Shared/Frame/EventEditBox.xml/.lua).
local EventEditBoxMixin = {}
function EventEditBoxMixin:IsDefaultTextEnabled() return self.defaultText ~= nil and self.defaultTextEnabled end
function EventEditBoxMixin:ShouldDefault(text)
  if text ~= "" then return false end
  if not self:IsDefaultTextEnabled() then return false end
  if self:HasFocus() then return false end
  return true
end
function EventEditBoxMixin:ApplyText(text)
  self.defaulted = self:ShouldDefault(text)
  if self.defaulted then
    self:SetText(self.defaultText)
  else
    self:SetText(text)
  end
  self:SetCursorPosition(0)
end
function EventEditBoxMixin:ApplyDefaultText(defaultText)
  self.defaultText = defaultText
  if self:ShouldDefault(self:GetText()) then self:ApplyText("") end
end
function EventEditBoxMixin:SetDefaultTextEnabled(v) self.defaultTextEnabled = v end
function EventEditBoxMixin:GetInputText()
  if not self.defaulted then return self:GetText() end
  return ""
end
function EventEditBoxMixin:IsDefaultTextDisplayed() return self.defaulted and self:GetText() == self.defaultText end
function EventEditBoxMixin:ApplyTextColor(c) self.textColor = c end
function EventEditBoxMixin:ApplyDefaultTextColor(c) self.defaultFontColor = c end

local function buildEventEditBox(box)
  mix(box, CallbackRegistry)
  box.Event = {}
  box:GenerateCallbackEvents({ "OnMouseDown", "OnMouseUp", "OnTabPressed", "OnTextChanged", "OnCursorChanged",
    "OnEscapePressed", "OnEnterPressed", "OnKeyDown", "OnEditFocusGained", "OnEditFocusLost" })
  mix(box, EventEditBoxMixin)
  box.defaultTextEnabled = true
  box.__autoFocus = false     -- autoFocus="false" on the template's EventEditBox
  box:SetMultiLine(true)
  box:SetScript("OnTabPressed", function(self) self:TriggerEvent("OnTabPressed", self) end)
  box:SetScript("OnTextChanged", function(self, userChanged)
    if userChanged then self.defaulted = self:IsDefaultTextEnabled() and self:GetText() == "" end
    self:TriggerEvent("OnTextChanged", self, userChanged)
  end)
  box:SetScript("OnEscapePressed", function(self) self:TriggerEvent("OnEscapePressed", self) end)
  box:SetScript("OnEnterPressed", function(self) self:TriggerEvent("OnEnterPressed", self) end)
  box:SetScript("OnEditFocusGained", function(self)
    if self:IsDefaultTextDisplayed() then
      self.defaulted = false
      self:SetText("")
    end
    self:TriggerEvent("OnEditFocusGained", self)
  end)
  box:SetScript("OnEditFocusLost", function(self)
    self:ClearHighlightText()
    if self:ShouldDefault(self:GetText()) then self:ApplyText("") end
    self:TriggerEvent("OnEditFocusLost", self)
  end)
end

local ScrollingEditBoxMixin = {}
function ScrollingEditBoxMixin:GetScrollBox() return self.ScrollBox end
function ScrollingEditBoxMixin:GetEditBox() return self:GetScrollBox().EditBox end
function ScrollingEditBoxMixin:SetFocus() self:GetEditBox():SetFocus() end
function ScrollingEditBoxMixin:HasFocus() return self:GetEditBox():HasFocus() end
function ScrollingEditBoxMixin:ClearFocus() self:GetEditBox():ClearFocus() end
function ScrollingEditBoxMixin:SetFontObject(name) self:GetEditBox():SetFontObject(name) end
function ScrollingEditBoxMixin:ClearText() self:SetText("") end
function ScrollingEditBoxMixin:GetText() return self:GetEditBox():GetText() end
function ScrollingEditBoxMixin:Insert(text) self:GetEditBox():Insert(text) end
function ScrollingEditBoxMixin:SetText(text) self:GetEditBox():ApplyText(text) end
function ScrollingEditBoxMixin:SetDefaultTextEnabled(v) self:GetEditBox():SetDefaultTextEnabled(v) end
function ScrollingEditBoxMixin:SetDefaultText(t) self:GetEditBox():ApplyDefaultText(t) end
function ScrollingEditBoxMixin:SetTextColor(c) self:GetEditBox():ApplyTextColor(c) end
function ScrollingEditBoxMixin:SetDefaultTextColor(c) self:GetEditBox():ApplyDefaultTextColor(c) end
function ScrollingEditBoxMixin:GetInputText() return self:GetEditBox():GetInputText() end
function ScrollingEditBoxMixin:GetFontHeight() return 12 end
function ScrollingEditBoxMixin:SetEnabled(v) self:GetEditBox():SetEnabled(v) end
function ScrollingEditBoxMixin:SetInterpolateScroll(v) end
function ScrollingEditBoxMixin:SetCursorPosition(p) self:GetEditBox():SetCursorPosition(p) end
function ScrollingEditBoxMixin:SetTextInsets(...) self:GetEditBox():SetTextInsets(...) end
function ScrollingEditBoxMixin:HasScrollableExtent() return false end
function ScrollingEditBoxMixin:UpdateScrollBox() end

TEMPLATE_BUILD.ScrollingEditBoxTemplate = function(f)
  mix(f, CallbackRegistry)
  f.Event = {}
  f:GenerateCallbackEvents({ "OnTabPressed", "OnTextChanged", "OnCursorChanged", "OnFocusGained",
    "OnFocusLost", "OnEnterPressed", "OnEscapePressed", "OnKeyDown" })
  mix(f, ScrollingEditBoxMixin)
  f.fontName = "GameFontHighlight"
  f.defaultFontName = "GameFontDisable"
  f.useDefaultEnterHandling = true
  f.useDefaultEscapeHandling = true
  f.ScrollBox = newObject("Frame", nil, f, "WowScrollBox")
  f.ScrollBox:SetAllPoints(f)
  f.ScrollBox.EditBox = newObject("EditBox", nil, f.ScrollBox, "EventEditBox")
  local box = f.ScrollBox.EditBox
  buildEventEditBox(box)
  box:SetFontObject(f.fontName)
  -- ScrollingEditBoxMixin:OnLoad (ScrollTemplates.lua L15-L76)
  box:RegisterCallback("OnTabPressed", function(_, eb) f:TriggerEvent("OnTabPressed", eb) end, f)
  box:RegisterCallback("OnTextChanged", function(_, eb, userChanged) f:TriggerEvent("OnTextChanged", eb, userChanged) end, f)
  box:RegisterCallback("OnEnterPressed", function(_, eb) f:TriggerEvent("OnEnterPressed", eb) end, f)
  box:RegisterCallback("OnEscapePressed", function(_, eb) f:TriggerEvent("OnEscapePressed", eb) end, f)
  box:RegisterCallback("OnEditFocusGained", function(_, eb) f:TriggerEvent("OnFocusGained", eb) end, f)
  box:RegisterCallback("OnEditFocusLost", function(_, eb) f:TriggerEvent("OnFocusLost", eb) end, f)
  f:RegisterCallback("OnEnterPressed", function(_, eb)
    if f.useDefaultEnterHandling and eb:IsMultiLine() then f:Insert("\n") end
  end, "__defaultEnter")
  f:RegisterCallback("OnEscapePressed", function(_, eb)
    if f.useDefaultEscapeHandling then f:ClearFocus() end
  end, "__defaultEscape")
  f:SetScript("OnMouseDown", function(self) self:GetEditBox():SetFocus() end)
end

-- --------------------------------------------------------------- Menus --

MenuResponse = { Open = 1, Refresh = 2, Close = 3, CloseAll = 4 }
MenuInputContext = { None = 1, MouseButton = 2, MouseWheel = 3 }

local Desc = {}
local DescMT = { __index = Desc }
local function newDesc(kind, text, data)
  return setmetatable({ kind = kind, text = text, data = data, elements = {}, initializers = {},
                        responseCallbacks = {} }, DescMT)
end
U.newDesc = newDesc

function Desc:Insert(d, index)
  if index then tinsert(self.elements, index, d) else self.elements[#self.elements + 1] = d end
  d.parentDesc = self
  return d
end
function Desc:CreateButton(text, callback, data)
  local d = newDesc("button", text, data)
  d.responder = callback
  if type(text) == "string" then checkEscapes("menu", text) end
  return self:Insert(d)
end
function Desc:CreateRadio(text, isSelected, setSelected, data)
  local d = newDesc("radio", text, data)
  d.radio = true
  d.isSelectedFn = isSelected
  d.responder = setSelected
  if type(text) == "string" then checkEscapes("menu", text) end
  return self:Insert(d)
end
function Desc:CreateCheckbox(text, isSelected, setSelected, data)
  local d = newDesc("checkbox", text, data)
  d.checkbox = true
  d.isSelectedFn = isSelected
  d.responder = setSelected
  d.defaultResponse = MenuResponse.Refresh
  if type(text) == "string" then checkEscapes("menu", text) end
  return self:Insert(d)
end
function Desc:CreateTitle(text, color)
  if type(text) == "string" then checkEscapes("menu", text) end
  return self:Insert(newDesc("title", text))
end
function Desc:CreateDivider() return self:Insert(newDesc("divider")) end
function Desc:CreateSpacer(extent) return self:Insert(newDesc("spacer")) end
function Desc:CreateTemplate() shimError("menu templates are not used by NeedleDropper", 2) end
function Desc:SetEnabled(v) self.isEnabled = v end
function Desc:IsEnabled()
  if self.isEnabled == nil then return true end
  if type(self.isEnabled) == "boolean" then return self.isEnabled end
  return self.isEnabled(self.data) and true or false
end
function Desc:SetTooltip(fn) self.tooltip = fn end
function Desc:SetTitleAndTextTooltip(title, text) self.tooltipTitle, self.tooltipText = title, text end
function Desc:SetResponse(r) self.defaultResponse = r end
function Desc:SetRadio(v) self.radio = v end
function Desc:IsRadio() return self.radio == true end
function Desc:SetIsSelected(fn) self.isSelectedFn = fn end
function Desc:IsSelected() return self.isSelectedFn ~= nil and self.isSelectedFn(self.data) and true or false end
function Desc:SetSelectionIgnored() self.selectionIgnored = true end
function Desc:IsSelectionIgnored() return self.selectionIgnored == true end
function Desc:SetCanSelect(v) self.canSelect = v end
function Desc:CanSelect()
  if not self:IsEnabled() then return false end
  if self.canSelect == nil then return true end
  if type(self.canSelect) == "boolean" then return self.canSelect end
  return self.canSelect(self.data)
end
function Desc:GetData() return self.data end
function Desc:SetData(d) self.data = d end
function Desc:HasElements() return #self.elements > 0 end
function Desc:EnumerateElementDescriptions() return ipairs(self.elements) end
function Desc:SetScrollMode(extent) self.scrollMode = extent end
function Desc:SetMinimumWidth(w) self.minimumWidth = w end
function Desc:GetMinimumWidth() return self.minimumWidth end
function Desc:SetMaximumWidth(w) end
function Desc:AddInitializer(fn) self.initializers[#self.initializers + 1] = fn end
function Desc:SetOnEnter(fn) self.onEnter = fn end
function Desc:SetOnLeave(fn) self.onLeave = fn end
function Desc:SetShouldPlaySoundOnClick(v) end
function Desc:SetSoundKit(v) end
function Desc:AddMenuResponseCallback(cb) self.responseCallbacks[#self.responseCallbacks + 1] = cb end
function Desc:AddMenuChangedCallback(cb) end
function Desc:AddMenuAcquiredCallback(cb) end
function Desc:AddMenuReleasedCallback(cb) end
function Desc:SetTag(tag) self.tag = tag end
-- MenuElementDescriptionProxyMixin:Pick (Blizzard_Menu/Menu.lua L903-L960).
function Desc:Pick(context, buttonName)
  if not self:CanSelect() then return false end
  if not self.responder then return false end
  local response = self.defaultResponse
  local override = self.responder(self.data, { context = context or MenuInputContext.MouseButton,
                                               buttonName = buttonName or "LeftButton" }, nil)
  if override then response = override end
  return true, response
end

MenuUtil = MenuUtil or {}
function MenuUtil.GetElementText(d) return d.text end
function MenuUtil.SetElementText(d, text) d.text = text end
function MenuUtil.ShowTooltipEx(owner, tooltip, func, ...)
  tooltip:SetOwner(owner, "ANCHOR_RIGHT")
  func(tooltip, ...)
  tooltip:Show()
end
function MenuUtil.HideTooltipEx(owner, tooltip) if tooltip:GetOwner() == owner then tooltip:Hide() end end
function MenuUtil.CreateRootMenuDescription() return newDesc("root") end
-- MenuUtil.CreateContextMenu (Blizzard_Menu/MenuUtil.lua L151): generator(owner, rootDescription, ...).
function MenuUtil.CreateContextMenu(ownerRegion, generator, ...)
  local root = newDesc("root")
  generator(ownerRegion, root, ...)
  local menu = { root = root, owner = ownerRegion, open = true }
  function menu:Close() self.open = false end
  U.contextMenus[#U.contextMenus + 1] = menu
  U.openMenu = menu
  PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
  return menu
end

-- Flattened menu entries, for assertions: { path, kind, text, enabled, selected }.
function U.MenuItems(root)
  local out = {}
  local function walk(d, prefix)
    for _, e in ipairs(d.elements) do
      local text = e.text
      local path = prefix and (prefix .. " > " .. tostring(text)) or tostring(text)
      out[#out + 1] = { path = path, kind = e.kind, text = text, enabled = e:IsEnabled(),
                        selected = (e.radio or e.checkbox) and e:IsSelected() or false,
                        submenu = #e.elements > 0 }
      if #e.elements > 0 then walk(e, path) end
    end
  end
  walk(root)
  return out
end

local function findDesc(root, ...)
  local d = root
  for i = 1, select("#", ...) do
    local want = select(i, ...)
    local found
    for _, e in ipairs(d.elements) do
      if e.text == want then found = e; break end
    end
    if not found then
      local have = {}
      for _, e in ipairs(d.elements) do have[#have + 1] = tostring(e.text) end
      shimError("menu entry '" .. tostring(want) .. "' not found; entries: " .. concat(have, " | "), 3)
    end
    d = found
  end
  return d
end
U.findDesc = findDesc

-- Picks an entry by its text path. Returns ok, response.
function U.MenuPick(root, ...)
  local d = findDesc(root, ...)
  local ok, response = d:Pick(MenuInputContext.MouseButton, "LeftButton")
  for _, cb in ipairs(root.responseCallbacks) do cb(nil, d) end
  return ok, response
end

function U.ContextMenu()
  return U.openMenu and U.openMenu.root
end
function U.ContextPick(...)
  local m = U.openMenu
  if not m then shimError("no context menu is open", 2) end
  local ok, response = U.MenuPick(m.root, ...)
  if response ~= MenuResponse.Open and response ~= MenuResponse.Refresh then m.open = false end
  return ok, response
end

-- DropdownButton intrinsic (Blizzard_Menu/DropdownButton.lua) + WowStyle1 mixins
-- (Blizzard_Menu/MenuTemplates.lua L719-L1056).
local DropdownButtonMixin = {}
function DropdownButtonMixin:SetupMenu(generator)
  assert(type(generator) == "function", "DropdownButtonMixin:SetupMenu(generator): argument is not a function.")
  self.menuGenerator = generator
  if self:IsShown() then self:GenerateMenu() end
end
function DropdownButtonMixin:GenerateMenu()
  local gen = self.menuGenerator
  if not gen then return end
  local root = newDesc("root")
  gen(self, root)
  self:RegisterMenu(root)
end
function DropdownButtonMixin:RegisterMenu(root)
  self.menuDescription = root
  local dd = self
  root:AddMenuResponseCallback(function(menu, description) dd:OnMenuResponse(menu, description) end)
  self:OnMenuAssigned()
end
function DropdownButtonMixin:GetMenuDescription() return self.menuDescription end
function DropdownButtonMixin:HasElements() return self.menuDescription and self.menuDescription:HasElements() or false end
function DropdownButtonMixin:OpenMenu()
  if not self:IsEnabled() then return end
  if self.menuOpen then return end
  self:GenerateMenu()
  if not self.menuDescription then return end
  self.menuOpen = true
end
function DropdownButtonMixin:CloseMenu()
  self.menuOpen = false
  self:SignalUpdate()
end
function DropdownButtonMixin:IsMenuOpen() return self.menuOpen == true end
function DropdownButtonMixin:SetMenuOpen(v) if v then self:OpenMenu() else self:CloseMenu() end end
function DropdownButtonMixin:SignalUpdate()
  local selections = {}
  local root = self.menuDescription
  if root then
    local function walk(d)
      for _, e in ipairs(d.elements) do
        if not e:IsSelectionIgnored() and (e.radio or e.checkbox) and e:IsSelected() then selections[#selections + 1] = e end
        walk(e)
      end
    end
    walk(root)
  end
  self:UpdateToMenuSelections(root, selections)
end
function DropdownButtonMixin:OnMenuResponse(menu, description)
  if self.shouldRegenerateOnResponse then self:GenerateMenu() else self:SignalUpdate() end
end
function DropdownButtonMixin:EnableRegenerateOnResponse() self.shouldRegenerateOnResponse = true end
function DropdownButtonMixin:OnMenuAssigned() self:SignalUpdate() end
function DropdownButtonMixin:UpdateToMenuSelections(menu, selections) end
function DropdownButtonMixin:SetMenuAnchor(a) self.menuAnchor = a end
function DropdownButtonMixin:SetTooltip(fn) self.tooltipFunc = fn end

local DropdownTextMixin = {}
function DropdownTextMixin:GetText() return self.text end
function DropdownTextMixin:SetText(text)
  self.text = text
  self:UpdateText()
end
function DropdownTextMixin:GetUpdateText() return self.text end
function DropdownTextMixin:UpdateText() self.Text:SetText(self:GetUpdateText()) end
function DropdownTextMixin:UpdateToMenuSelections() self:UpdateText() end

local DropdownSelectionTextMixin = {}
function DropdownSelectionTextMixin:GetUpdateText() return self.text or self.defaultText end
function DropdownSelectionTextMixin:SetDefaultText(text)
  self.defaultText = text
  self:UpdateText()
end
function DropdownSelectionTextMixin:GetDefaultText() return self.defaultText end
function DropdownSelectionTextMixin:SetSelectionTranslator(fn) self.selectionTranslator = fn end
function DropdownSelectionTextMixin:SetSelectionText(fn) self.selectionFunc = fn end
function DropdownSelectionTextMixin:OverrideText(text)
  if not text then return end
  self.disableSelectionText = true
  self:SetText(text)
end
function DropdownSelectionTextMixin:UpdateToMenuSelections(root, selections)
  if self.disableSelectionText then return end
  local text
  if self.selectionFunc then text = self.selectionFunc(selections) end
  if text == nil and selections then
    local texts = {}
    for _, s in ipairs(selections) do
      texts[#texts + 1] = (self.selectionTranslator or MenuUtil.GetElementText)(s)
    end
    if #texts > 0 then text = concat(texts, LIST_DELIMITER) end
  end
  self:SetText(text or self.defaultText)
end

local WowDropdownFilterBehaviorMixin = {}
function WowDropdownFilterBehaviorMixin:SetDefaultCallback(cb) self.defaultCallback = cb end
function WowDropdownFilterBehaviorMixin:SetIsDefaultCallback(cb) self.isDefaultCallback = cb end
function WowDropdownFilterBehaviorMixin:SetUpdateCallback(cb) self.notifyUpdateCallback = cb end
function WowDropdownFilterBehaviorMixin:ValidateResetState()
  if self.isDefaultCallback then self.ResetButton:SetShown(not self.isDefaultCallback()) end
end
function WowDropdownFilterBehaviorMixin:OnMenuResponse(menu, description)
  DropdownButtonMixin.OnMenuResponse(self, menu, description)
  self:ValidateResetState()
  if self.notifyUpdateCallback then self.notifyUpdateCallback(description) end
end
function WowDropdownFilterBehaviorMixin:OnMenuAssigned()
  DropdownButtonMixin.OnMenuAssigned(self)
  self:ValidateResetState()
end
function WowDropdownFilterBehaviorMixin:Reset() self.ResetButton:Hide() end

local function buildDropdownBase(f)
  mix(f, CallbackRegistry)
  f.Event = { OnUpdate = "OnUpdate", OnMenuOpen = "OnMenuOpen", OnMenuClose = "OnMenuClose" }
  mix(f, DropdownButtonMixin)
  f.intrinsic = "DropdownButton"
end

TEMPLATE_BUILD.WowStyle1DropdownTemplate = function(f)
  buildDropdownBase(f)
  mix(f, DropdownTextMixin)
  mix(f, DropdownSelectionTextMixin)
  f:SetSize(120, 25)
  f.Background = f:CreateTexture(nil, "BACKGROUND")
  f.Arrow = f:CreateTexture(nil, "OVERLAY")
  f.Text = f:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  f:SetScript("OnShow", function(self) self:GenerateMenu() end)
  f:SetScript("OnMouseDown", function(self) self:SetMenuOpen(not self:IsMenuOpen()) end)
end

TEMPLATE_BUILD.WowStyle1FilterDropdownTemplate = function(f)
  buildDropdownBase(f)
  mix(f, DropdownTextMixin)
  mix(f, WowDropdownFilterBehaviorMixin)
  f:SetSize(135, 18)
  f.text = FILTER
  f.resizeToText = true
  f.ResetButton = newObject("Button", nil, f, "UIResetButtonTemplate")
  f.ResetButton:Hide()
  f.ResetButton:SetScript("OnClick", function()
    if f.defaultCallback then f.defaultCallback() end
    f.ResetButton:Hide()
  end)
  f.Background = f:CreateTexture(nil, "BACKGROUND")
  f.Text = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f:UpdateText()
  f:SetScript("OnShow", function(self) self:ValidateResetState() end)
  f:SetScript("OnMouseDown", function(self) self:SetMenuOpen(not self:IsMenuOpen()) end)
end

-- Opens a dropdown (regenerating its menu) and picks the entry at the text path.
function U.DropdownPick(dd, ...)
  if not dd:IsVisible() then shimError("dropdown is not visible", 2) end
  if not dd:IsEnabled() then shimError("dropdown is disabled", 2) end
  dd:OpenMenu()
  local root = dd:GetMenuDescription()
  if not root then shimError("dropdown has no menu", 2) end
  local ok, response = U.MenuPick(root, ...)
  if response ~= MenuResponse.Open and response ~= MenuResponse.Refresh then dd:CloseMenu() end
  return ok, response
end

function U.DropdownItems(dd)
  dd:GenerateMenu()
  return U.MenuItems(dd:GetMenuDescription())
end

-- ------------------------------------------------------- MinimalSlider --

MinimalSliderWithSteppersMixin = MinimalSliderWithSteppersMixin or {}
MinimalSliderWithSteppersMixin.Label = MinimalSliderWithSteppersMixin.Label or
  { Left = 1, Right = 2, Top = 3, Min = 4, Max = 5 }
MinimalSliderWithSteppersMixin.Event = { OnValueChanged = "OnValueChanged", OnInteractStart = "OnInteractStart",
  OnInteractEnd = "OnInteractEnd" }

local MinimalSliderImpl = {}
function MinimalSliderImpl:Init(value, minValue, maxValue, steps, formatters)
  self.Slider:SetMinMaxValues(minValue, maxValue)
  self.Slider:SetValueStep((maxValue - minValue) / steps)
  self.Slider:SetValue(value)
  self.formatters = formatters
  self:FormatValue(value)
  local owner = self
  self.Slider:SetScript("OnValueChanged", function(slider, v, userInput)
    owner:FormatValue(v)
    owner:TriggerEvent(MinimalSliderWithSteppersMixin.Event.OnValueChanged, v)
  end)
end
function MinimalSliderImpl:FormatValue(value)
  if not self.formatters then return end
  for labelID, formatter in pairs(self.formatters) do
    local label = self.Labels[labelID]
    if label then
      label:SetText(formatter(value))
      label:Show()
    end
  end
end
function MinimalSliderImpl:SetValue(v) self.Slider:SetValue(v) end
function MinimalSliderImpl:SetEnabled(v)
  self.sliderEnabled = v
  self.Slider:SetEnabled(v)
  self.Back:SetEnabled(v)
  self.Forward:SetEnabled(v)
end
function MinimalSliderImpl:IsSliderEnabled() return self.sliderEnabled ~= false end
function MinimalSliderImpl:OnStepperClicked(forward)
  local v = self.Slider:GetValue()
  local step = self.Slider:GetValueStep()
  sliderSet(self.Slider, forward and (v + step) or (v - step), true)
end

TEMPLATE_BUILD.MinimalSliderWithSteppersTemplate = function(f)
  mix(f, CallbackRegistry)
  f.Event = MinimalSliderWithSteppersMixin.Event
  mix(f, MinimalSliderImpl)
  f.Label = MinimalSliderWithSteppersMixin.Label
  f:SetSize(250, 40)
  f.Slider = newObject("Slider", nil, f, "MinimalSliderTemplate")
  f.Slider.Thumb = f.Slider:CreateTexture(nil, "ARTWORK")
  f.Back = newObject("Button", nil, f)
  f.Forward = newObject("Button", nil, f)
  f.Back:SetScript("OnClick", function() f:OnStepperClicked(false) end)
  f.Forward:SetScript("OnClick", function() f:OnStepperClicked(true) end)
  f.LeftText = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f.RightText = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f.TopText = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f.MinText = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f.MaxText = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  f.Labels = { f.LeftText, f.RightText, f.TopText, f.MinText, f.MaxText }
  for i = 1, #f.Labels do f.Labels[i]:Hide() end
end

-- User drags the slider to `value`.
function U.SetSlider(sliderFrame, value)
  local s = sliderFrame.Slider or sliderFrame
  if not s:IsEnabled() then shimError("slider is disabled", 2) end
  sliderSet(s, value, true)
end

-- --------------------------------------------------------- StaticPopup --

StaticPopupDialogs = StaticPopupDialogs or {}
local shownDialogs = {}
local dialogSeq = 0

local GameDialog = {}
function GameDialog:GetEditBox() return self.EditBox end
function GameDialog:GetEditBoxText() local e = self:GetEditBox(); return e and e:GetText() or "" end
function GameDialog:GetButton(i) return self.ButtonContainer.Buttons[i] end
function GameDialog:GetButton1() return self:GetButton(1) end
function GameDialog:GetButton2() return self:GetButton(2) end
function GameDialog:GetButton3() return self:GetButton(3) end
function GameDialog:GetButton4() return self:GetButton(4) end
function GameDialog:GetButtons() return self.ButtonContainer.Buttons end
function GameDialog:GetTextFontString() return self.Text end

local function dialogInfoOf(dialog) return StaticPopupDialogs[dialog.which] end

local function makeDialog()
  dialogSeq = dialogSeq + 1
  local d = newObject("Frame", "StaticPopup" .. dialogSeq, UIParent, "StaticPopupTemplate")
  mix(d, GameDialog)
  d:Hide()
  d:SetFrameStrata("DIALOG")
  d.Text = d:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
  -- GameDialog.xml L210: no autoFocus attribute, so the edit box takes focus when shown.
  d.EditBox = newObject("EditBox", childName(d, "EditBox"), d, "StaticPopupEditBoxTemplate")
  d.EditBox.Instructions = d.EditBox:CreateFontString(nil, "ARTWORK", "GameFontDisableSmall")
  d.EditBox:SetScript("OnEnterPressed", function(self)
    local info = dialogInfoOf(d)
    if info and info.EditBoxOnEnterPressed then info.EditBoxOnEnterPressed(self, d.data) end
  end)
  d.EditBox:SetScript("OnEscapePressed", function(self)
    local info = dialogInfoOf(d)
    if info and info.EditBoxOnEscapePressed then info.EditBoxOnEscapePressed(self, d.data) end
  end)
  d.EditBox:SetScript("OnTextChanged", function(self, userInput)
    local info = dialogInfoOf(d)
    if info and info.EditBoxOnTextChanged then info.EditBoxOnTextChanged(self, d.data) end
    self.Instructions:SetShown(self:GetText() == "")
  end)
  d.ButtonContainer = newObject("Frame", nil, d)
  d.ButtonContainer.Buttons = {}
  for i = 1, 4 do
    local b = newObject("Button", nil, d.ButtonContainer, "StaticPopupButtonTemplate")
    b:SetScript("OnClick", function() StaticPopup_OnClick(d, i) end)
    d.ButtonContainer.Buttons[i] = b
  end
  d:SetScript("OnHide", function(self)
    for i = #shownDialogs, 1, -1 do if shownDialogs[i] == self then tremove(shownDialogs, i) end end
    local info = dialogInfoOf(self)
    if info and info.OnHide then info.OnHide(self, self.data) end
  end)
  return d
end

local dialogPool = {}

function StaticPopup_FindVisible(which, data)
  for _, d in ipairs(shownDialogs) do
    if d.which == which and (data == nil or d.data == data) then return d end
  end
  return nil
end
function StaticPopup_Visible(which)
  local d = StaticPopup_FindVisible(which)
  if d then return d:GetName(), d end
  return nil
end

-- Blizzard_StaticPopup/StaticPopup.lua L309-L432 (12.x: handlers get (dialog, data)).
function StaticPopup_Show(which, text_arg1, text_arg2, data, insertedFrame)
  local info = StaticPopupDialogs[which]
  if not info then error("Dialog " .. tostring(which) .. " does not exist.", 2) end
  if info.OnAccept and info.OnButton1 then error("Dialog " .. which .. " cannot have both OnAccept and OnButton1", 2) end
  if info.OnCancel and info.OnButton2 then error("Dialog " .. which .. " cannot have both OnCancel and OnButton2", 2) end
  local dialog = StaticPopup_FindVisible(which, data)
  if dialog then
    dialog:Hide()
  else
    for _, d in ipairs(dialogPool) do
      if not d:IsShown() then dialog = d; break end
    end
    if not dialog and #dialogPool < 4 then
      dialog = makeDialog()
      dialogPool[#dialogPool + 1] = dialog
    end
  end
  if not dialog then
    if info.OnCancel then info.OnCancel(nil, data) end
    return nil
  end
  dialog.which = which
  dialog.dialogInfo = info
  dialog.data = data
  dialog.hideOnEscape = info.hideOnEscape
  local text = info.text or ""
  if text_arg1 ~= nil or text_arg2 ~= nil then text = string.format(text, text_arg1, text_arg2) end
  dialog.Text:SetText(text)
  dialog.shownText = text
  dialog.showAlert = info.showAlert
  local buttonTexts = { info.button1, info.button2, info.button3, info.button4 }
  for i = 1, 4 do
    local b = dialog.ButtonContainer.Buttons[i]
    b:Enable()
    if buttonTexts[i] then
      b:SetText(buttonTexts[i])
      b:Show()
    else
      b:Hide()
    end
  end
  local eb = dialog.EditBox
  eb:SetMaxLetters(info.maxLetters or 0)
  eb:SetText("")
  eb.Instructions:SetText(info.editBoxInstructions or "")
  if info.hasEditBox then eb:Show() else eb:Hide() end
  shownDialogs[#shownDialogs + 1] = dialog
  U.popups[#U.popups + 1] = { which = which, dialog = dialog, text = text, data = data }
  dialog:Show()
  dialog:Raise()
  if info.OnShow then info.OnShow(dialog, data) end
  return dialog
end

-- StaticPopup.lua L695-L760 (the backward-compatible path: OnAccept(dialog, data, data2)).
function StaticPopup_OnClick(dialog, index)
  if not dialog:IsShown() then return end
  local which = dialog.which
  local info = StaticPopupDialogs[which]
  if not info then return nil end
  local hide = true
  if index == 1 then
    local OnAccept = info.OnAccept or info.OnButton1
    if OnAccept then hide = not OnAccept(dialog, dialog.data, dialog.data2) end
  elseif index == 3 then
    if info.OnAlt then info.OnAlt(dialog, dialog.data, "clicked") end
  else
    if info.OnCancel then hide = not info.OnCancel(dialog, dialog.data, "clicked") end
  end
  if hide and which == dialog.which then dialog:Hide() end
end

function StaticPopup_Hide(which, data)
  for i = #shownDialogs, 1, -1 do
    local d = shownDialogs[i]
    if d.which == which and (data == nil or d.data == data) then d:Hide() end
  end
end

function StaticPopup_EscapePressed()
  local closed = false
  for i = #shownDialogs, 1, -1 do
    local d = shownDialogs[i]
    if d.hideOnEscape then
      local info = dialogInfoOf(d)
      if info and info.OnCancel then info.OnCancel(d, d.data, "clicked") end
      d:Hide()
      closed = true
    end
  end
  return closed
end

function StaticPopup_StandardNonEmptyTextHandler(editBox)
  local dialog = editBox:GetParent()
  local text = editBox:GetText() or ""
  dialog:GetButton1():SetEnabled(string.find(text, "%S") ~= nil)
end
function StaticPopup_StandardEditBoxOnEscapePressed(editBox) editBox:GetParent():Hide() end

function U.Popup(which)
  return StaticPopup_FindVisible(which)
end
function U.PopupAccept(which)
  local d = StaticPopup_FindVisible(which)
  if not d then shimError("popup " .. tostring(which) .. " is not shown", 2) end
  U.Click(d:GetButton1())
end
function U.PopupCancel(which)
  local d = StaticPopup_FindVisible(which)
  if not d then shimError("popup " .. tostring(which) .. " is not shown", 2) end
  U.Click(d:GetButton2())
end

-- ------------------------------------------------------ input helpers --

local function assertInteractive(obj, what)
  if not obj then shimError(what .. ": no object", 3) end
  if not obj:IsVisible() then shimError(what .. ": " .. obj:GetDebugName() .. " is not visible", 3) end
end

-- Mouse click. Respects enable state and RegisterForClicks (default: LeftButtonUp only).
function U.Click(btn, mouseButton)
  mouseButton = mouseButton or "LeftButton"
  assertInteractive(btn, "Click")
  if btn.IsEnabled and not btn:IsEnabled() then return false end
  local regs = btn.__clicks or { "LeftButtonUp" }
  local ok = false
  for _, r in ipairs(regs) do
    if r == "AnyUp" or r == "AnyDown" or r == mouseButton .. "Up" or r == mouseButton .. "Down" then ok = true end
  end
  if not ok then return false end
  if btn.__kind == "CheckButton" then btn.__checked = not btn.__checked end
  fire(btn, "PreClick", mouseButton, false)
  fire(btn, "OnClick", mouseButton, false)
  fire(btn, "PostClick", mouseButton, false)
  return true
end

function U.Enter(frame)
  assertInteractive(frame, "Enter")
  frame.__mouseOver = true
  if frame.IsEnabled and not frame:IsEnabled() and not frame.__motionWhileDisabled
     and (frame.__kind == "Button" or frame.__kind == "CheckButton" or frame.__kind == "DropdownButton") then
    return false
  end
  fire(frame, "OnEnter", true)
  return true
end

function U.Leave(frame)
  frame.__mouseOver = false
  fire(frame, "OnLeave", true)
end

function U.MouseDown(frame, button) fire(frame, "OnMouseDown", button or "LeftButton") end
function U.MouseUp(frame, button) fire(frame, "OnMouseUp", button or "LeftButton") end

function U.Focus(box)
  assertInteractive(box, "Focus")
  if box:IsEnabled() then box:SetFocus() end
end

-- The player replaces the box's text (select all + type): OnTextChanged(self, true).
function U.Type(box, text)
  assertInteractive(box, "Type")
  if not box:IsEnabled() then shimError("Type: edit box is disabled", 2) end
  if U.focus ~= box then box:SetFocus() end
  box.__text = clipText(box, text)
  box.__cursor = #box.__text
  fire(box, "OnTextChanged", true)
end

-- A Ctrl+V paste: the client fires OnTextChanged twice (EditModeDialogs.lua L337-L345).
function U.Paste(box, text)
  assertInteractive(box, "Paste")
  if not box:IsEnabled() then shimError("Paste: edit box is disabled", 2) end
  if U.focus ~= box then box:SetFocus() end
  box.__text = clipText(box, text)
  box.__cursor = #box.__text
  fire(box, "OnTextChanged", true)
  fire(box, "OnTextChanged", true)
end

function U.PressEnter(box)
  box = box or U.focus
  if not box then shimError("PressEnter: no edit box has focus", 2) end
  fire(box, "OnEnterPressed")
end

function U.PressTab(box, shift)
  box = box or U.focus
  if not box then shimError("PressTab: no edit box has focus", 2) end
  U.shift = shift and true or false
  fire(box, "OnTabPressed")
  U.shift = false
end

-- Escape: a focused edit box gets the key; else StaticPopups with hideOnEscape, then
-- CloseSpecialWindows; else the Game Menu would open.
function U.PressEscape()
  local box = U.focus
  if box then
    fire(box, "OnEscapePressed")
    return "editbox"
  end
  if StaticPopup_EscapePressed() then return "popup" end
  if CloseSpecialWindows() then return "closed" end
  return "gamemenu"
end

-- A left-button drag on `frame` by (dx, dy): OnDragStart (only when registered for drag), the
-- frame that started moving is moved, OnDragStop.
function U.Drag(frame, dx, dy)
  assertInteractive(frame, "Drag")
  if not frame.__drag then return false end
  fire(frame, "OnDragStart", "LeftButton")
  local target = frame
  while target and not target.__moving do target = target.__parent end
  if target then
    local point, rel, relPoint, x, y = target:GetPoint(1)
    target:ClearAllPoints()
    target:SetPoint(point or "CENTER", rel or UIParent, relPoint or point or "CENTER", (x or 0) + dx, (y or 0) + dy)
  end
  fire(frame, "OnDragStop")
  return target ~= nil
end

-- The resize grip is dragged until the target is w x h.
function U.Resize(grip, w, h)
  assertInteractive(grip, "Resize")
  fire(grip, "OnMouseDown", "LeftButton")
  local t = grip.target
  if t then t:SetSize(w, h) end
  fire(grip, "OnMouseUp", "LeftButton")
end

-- All addon-created frames with a given template.
function U.CreatedWith(template)
  local out = {}
  for _, c in ipairs(U.created) do
    if c.template == template then out[#out + 1] = c.frame end
  end
  return out
end

function U.EditBoxes()
  local out = {}
  for _, f in ipairs(SHIM.frames) do
    if f.__kind == "EditBox" then out[#out + 1] = f end
  end
  return out
end

function U.Texts(frame, out)
  out = out or {}
  if not frame then return out end
  for _, r in ipairs(frame.__regions or {}) do
    if r.__kind == "FontString" and r.__text and r:IsVisible() then out[#out + 1] = r.__text end
  end
  for _, c in ipairs(frame.__children or {}) do
    if c:IsVisible() then U.Texts(c, out) end
  end
  return out
end

-- ======================================================================
-- Model fakes: CONTRACTS 2.5 (ns.Soundtracks), 2.6 (ns.Share), 2.7 (ns.Companion) and the
-- Director entry points CONTRACTS 2.9 lets the window call.
-- ======================================================================

local F = {}
SHIM.uiFakes = F
F.calls = {}
F.playTests = {}
F.suppressed = {}

local function note(name, ...) F.calls[#F.calls + 1] = { name, ... } end
function F.Called(name)
  local out = {}
  for _, c in ipairs(F.calls) do if c[1] == name then out[#out + 1] = c end end
  return out
end

F.DEFAULT_BUILTINS = {
  {
    id = "azeroth-mixtape", name = "Azeroth Mixtape", author = "NeedleDropper",
    description = "The default NeedleDropper soundtrack.", tags = { "default", "fitting", "funny" }, version = 1,
    source = "packs/azeroth-mixtape.json",
    drops = {
      { id = "area:87", kind = "subzone", mapID = 1429, areaID = 87, subzone = "Goldshire", label = "Goldshire",
        title = "Africa", artist = "Toto", durationMs = 295000, startMs = 0, youtube = "FTQbiNvZqaY",
        tags = { "funny" }, note = "Everybody's first inn." },
      { id = "inst:36", kind = "instance", instanceID = 36, label = "The Deadmines", title = "Pirates",
        artist = "Crew", durationMs = 0, startMs = 0, youtube = "bbbbbbbbbbb" },
      { id = "zone:1429", kind = "zone", mapID = 1429, label = "Elwynn Forest", title = "I'll Keep Coming",
        artist = "Low Roar", durationMs = 352000, startMs = 0, youtube = "mlM66FzjpgM", note = "Home." },
      { id = "zone:1436", kind = "zone", mapID = 1436, label = "Westfall", title = "Harvest Moon",
        artist = "Neil Young", durationMs = 0, startMs = 0, spotify = "4uLU6hMCjMI75M1A2tKUQC" },
    },
  },
  {
    id = "azeroth-unbound", name = "Azeroth Unbound", author = "NeedleDropper", description = "The v1.1 soundtrack.",
    tags = {}, version = 1, source = "packs/azeroth-unbound.json",
    drops = {
      { id = "zone:1411", kind = "zone", mapID = 1411, label = "Durotar", title = "Red Sands", artist = "Desert Band",
        durationMs = 0, startMs = 0, youtube = "ccccccccccc" },
      { id = "zone:1429", kind = "zone", mapID = 1429, label = "Elwynn Forest", title = "Unbound Elwynn",
        artist = "Someone", durationMs = 0, startMs = 0, youtube = "ddddddddddd" },
    },
  },
}

local function trim(s)
  if type(s) ~= "string" then return "" end
  return (string.gsub(s, "^[ \t\r\n]+", ""):gsub("[ \t\r\n]+$", ""))
end

function F.Install(ns, opts)
  opts = opts or {}
  if ns.L == nil then ns.L = setmetatable({}, { __index = function(t, k) return k end }) end

  -- ---------------------------------------------------------- Soundtracks
  local S = {}
  ns.Soundtracks = S
  S.Limits = { sets = 50, dropsPerSet = 300, dropsTotal = 3000, undo = 20 }
  local builtins, builtinById = {}, {}
  for _, set in ipairs(opts.builtins or F.DEFAULT_BUILTINS) do
    local c = copy(set)
    builtins[#builtins + 1] = c
    builtinById[c.id] = c
  end
  F.builtins = builtins
  local undo = {}
  local rebuildPending = false

  local function db() return ns.db end
  local function parse(ref)
    if type(ref) ~= "string" then return nil end
    local kind, id = string.match(ref, "^(%a+):(.+)$")
    return kind, id
  end

  function S.Slugify(name)
    local s = string.lower(trim(name))
    s = string.gsub(s, "[^a-z0-9]+", "-")
    s = string.gsub(s, "^%-+", "")
    s = string.gsub(s, "%-+$", "")
    return s
  end

  function S.DropIdFor(place)
    if type(place) ~= "table" then return nil end
    if place.instanceID then return "inst:" .. place.instanceID end
    if place.areaID then return "area:" .. place.areaID end
    if place.mapID and place.subzone and place.subzone ~= "" then
      local slug = S.Slugify(place.subzone)
      if slug ~= "" then return "sub:" .. place.mapID .. ":" .. slug end
    end
    if place.mapID then return "zone:" .. place.mapID end
    return nil
  end

  local KEEP = { "id", "kind", "mapID", "areaID", "subzone", "instanceID", "label", "title", "artist", "startMs",
    "durationMs", "spotify", "youtube", "tags", "note", "file", "volume", "disabled", "basedOn", "modified" }

  function S.Validate(drop)
    note("Validate", drop)
    if type(drop) ~= "table" then return nil, "not a table" end
    local d = {}
    for _, k in ipairs(KEEP) do d[k] = copy(drop[k]) end
    for k, v in pairs(d) do if v == "" or v == false then d[k] = nil end end
    if drop.disabled == true then d.disabled = true end
    if type(d.tags) == "table" and #d.tags == 0 then d.tags = nil end
    if d.title and #d.title > 200 then return nil, "title is too long" end
    if d.artist and #d.artist > 200 then return nil, "artist is too long" end
    if d.note and #d.note > 500 then return nil, "note is too long" end
    if d.spotify and not string.find(d.spotify, "^[0-9A-Za-z]+$") then return nil, "bad spotify id" end
    if d.volume and (d.volume < 0 or d.volume > 100) then return nil, "volume must be 0-100" end
    local id = S.DropIdFor(d)
    if not id then return nil, "a drop needs a place" end
    d.id = id
    if d.instanceID then d.kind = "instance" elseif d.areaID or d.subzone then d.kind = "subzone" else d.kind = "zone" end
    if not d.label or d.label == "" then d.label = d.subzone or id end
    return d
  end

  local function snapshot(what)
    if #undo >= S.Limits.undo then tremove(undo, 1) end
    undo[#undo + 1] = { what = what, soundtracks = copy(db().soundtracks), active = copy(db().active),
                        cactive = copy(ns.cdb.active) }
  end

  function S.BuildActiveMap()
    note("BuildActiveMap")
    rebuildPending = false
    local M = { loaded = true, version = 2, hash = 0, generated = "", packs = {}, drops = {}, byId = {}, byN = {},
      zoneDrops = {}, subDrops = {}, looseSubDrops = {}, areaDrops = {}, instanceDrops = {}, problems = {}, invalid = 0 }
    local claimed = {}
    for _, ref in ipairs(S.ActiveRefs()) do
      local set = S.Get(ref)
      if set then
        local kind, sid = parse(ref)
        for _, d in ipairs(set.drops) do
          local draft = (not d.disabled) and (not d.artist or d.artist == "" or not d.title or d.title == "")
          if not claimed[d.id] and not draft then
            claimed[d.id] = true
            if not d.disabled then
              local m = copy(d)
              m.setRef, m.setId, m.builtin = ref, sid, kind == "builtin"
              m.n = #M.drops + 1
              m.durationMs = m.durationMs or 0
              m.startMs = m.startMs or 0
              if m.subzone then m.subzoneLower = string.lower(m.subzone) end
              M.drops[#M.drops + 1] = m
              M.byId[m.id] = m
              M.byN[m.n] = m
              if m.kind == "zone" then
                M.zoneDrops[m.mapID] = M.zoneDrops[m.mapID] or m
              elseif m.kind == "subzone" then
                if m.mapID then
                  M.subDrops[m.mapID] = M.subDrops[m.mapID] or {}
                  tinsert(M.subDrops[m.mapID], m)
                else
                  M.looseSubDrops[#M.looseSubDrops + 1] = m
                end
                if m.areaID then M.areaDrops[m.areaID] = M.areaDrops[m.areaID] or m end
              elseif m.kind == "instance" then
                M.instanceDrops[m.instanceID] = M.instanceDrops[m.instanceID] or m
              end
            end
          end
        end
      end
    end
    ns.Map = M
    if ns.Fire then ns.Fire("ND_MAP_CHANGED", M) end
    return M
  end

  function S.ScheduleRebuild()
    if rebuildPending then return end
    rebuildPending = true
    C_Timer.After(0.25, function() if rebuildPending then S.BuildActiveMap() end end)
  end

  local function changed()
    if ns.Fire then ns.Fire("ND_SOUNDTRACKS_CHANGED") end
    S.ScheduleRebuild()
  end
  F.changed = changed

  function S.Init()
    note("Init")
    local d = db()
    if type(d.soundtracks) ~= "table" then d.soundtracks = {} end
    if type(d.active) ~= "table" then d.active = { "builtin:azeroth-mixtape" } end
    if type(d.ui) ~= "table" then d.ui = {} end
    if type(d.importsHandled) ~= "table" then d.importsHandled = {} end
    S.BuildActiveMap()
  end

  function S.Get(ref)
    local kind, id = parse(ref)
    if kind == "builtin" then return builtinById[id] end
    if kind == "user" then return db().soundtracks[id] end
    return nil
  end
  function S.Exists(ref) return S.Get(ref) ~= nil end

  function S.ActiveRefs()
    local list = ns.cdb and ns.cdb.active or db().active
    local out = {}
    for i = 1, #(list or {}) do out[i] = list[i] end
    return out
  end

  function S.SetActive(refs)
    note("SetActive", copy(refs))
    local clean, seen = {}, {}
    for _, ref in ipairs(refs) do
      if S.Exists(ref) and not seen[ref] then
        seen[ref] = true
        clean[#clean + 1] = ref
      end
    end
    snapshot("change the soundtrack order")
    if ns.cdb.active then ns.cdb.active = clean else db().active = clean end
    changed()
    return true
  end

  function S.UsesCharacterList() return ns.cdb.active ~= nil end
  function S.SetUseCharacterList(v)
    note("SetUseCharacterList", v)
    if v then ns.cdb.active = copy(db().active) else ns.cdb.active = nil end
    changed()
  end

  function S.List()
    local out = {}
    local active = S.ActiveRefs()
    local pri = {}
    for i, ref in ipairs(active) do pri[ref] = i end
    local function entry(ref, set, builtin)
      return { ref = ref, id = set.id, name = set.name, author = set.author or "", description = set.description or "",
               tags = copy(set.tags or {}), builtin = builtin, active = pri[ref] ~= nil, priority = pri[ref],
               dropCount = #set.drops }
    end
    for _, set in ipairs(builtins) do out[#out + 1] = entry("builtin:" .. set.id, set, true) end
    local users = {}
    for id, set in pairs(db().soundtracks) do users[#users + 1] = set end
    table.sort(users, function(a, b) return string.lower(a.name) < string.lower(b.name) end)
    for _, set in ipairs(users) do out[#out + 1] = entry("user:" .. set.id, set, false) end
    return out
  end

  local function uniqueId(base)
    if base == "" then base = "soundtrack" end
    local id, n = base, 1
    while db().soundtracks[id] or builtinById[id] do
      n = n + 1
      id = base .. "-" .. n
    end
    return id
  end

  local function nameTaken(name)
    for _, s in pairs(db().soundtracks) do if string.lower(s.name) == string.lower(name) then return true end end
    return false
  end

  function S.Create(name)
    note("Create", name)
    name = trim(name)
    if name == "" or #name > 64 then return nil, "a soundtrack name has 1-64 characters" end
    if nameTaken(name) then return nil, "you already have a soundtrack with that name" end
    local count = 0
    for _ in pairs(db().soundtracks) do count = count + 1 end
    if count >= S.Limits.sets then return nil, "you have 50 soundtracks already" end
    snapshot("create " .. name)
    local id = uniqueId(S.Slugify(name))
    db().soundtracks[id] = { id = id, name = name, author = "", description = "", tags = {}, revision = 0,
      created = time(), modified = time(), drops = {} }
    changed()
    return "user:" .. id
  end

  function S.Duplicate(ref, name)
    note("Duplicate", ref, name)
    local src = S.Get(ref)
    if not src then return nil, "no such soundtrack" end
    local kind = parse(ref)
    name = name or (src.name .. (kind == "builtin" and " (my copy)" or " (copy)"))
    local n = 1
    local base = name
    while nameTaken(name) do n = n + 1; name = base .. " (" .. n .. ")" end
    local newRef, err = S.Create(name)
    if not newRef then return nil, err end
    local set = S.Get(newRef)
    set.drops = copy(src.drops)
    set.description = src.description or ""
    set.author = src.author or ""
    changed()
    return newRef
  end

  function S.Rename(ref, name)
    note("Rename", ref, name)
    local kind = parse(ref)
    if kind ~= "user" then return nil, "built-in soundtracks cannot be renamed" end
    local set = S.Get(ref)
    if not set then return nil, "no such soundtrack" end
    name = trim(name)
    if name == "" or #name > 64 then return nil, "a soundtrack name has 1-64 characters" end
    snapshot("rename " .. set.name)
    set.name = name
    set.revision = (set.revision or 0) + 1
    changed()
    return true
  end

  function S.Delete(ref)
    note("Delete", ref)
    local kind, id = parse(ref)
    if kind ~= "user" then return nil, "built-in soundtracks cannot be deleted" end
    if not db().soundtracks[id] then return nil, "no such soundtrack" end
    snapshot("delete " .. db().soundtracks[id].name)
    db().soundtracks[id] = nil
    for _, list in ipairs({ db().active, ns.cdb.active }) do
      if list then for i = #list, 1, -1 do if list[i] == ref then tremove(list, i) end end end
    end
    if #db().active == 0 then db().active = { "builtin:azeroth-mixtape" } end
    changed()
    return true
  end

  local function findIndex(set, id)
    for i, d in ipairs(set.drops) do if d.id == id then return i, d end end
    return nil
  end

  function S.GetDrop(ref, id)
    local set = S.Get(ref)
    if not set then return nil end
    local _, d = findIndex(set, id)
    return d
  end

  function S.AddDrop(ref, drop)
    note("AddDrop", ref, copy(drop))
    local kind = parse(ref)
    if kind ~= "user" then return nil, "built-in soundtracks are read-only" end
    local set = S.Get(ref)
    if not set then return nil, "no such soundtrack" end
    local d, err = S.Validate(drop)
    if not d then return nil, err end
    if findIndex(set, d.id) then return nil, "this soundtrack already has a drop for that place" end
    if #set.drops >= S.Limits.dropsPerSet then return nil, "a soundtrack holds at most 300 drops" end
    snapshot("add " .. d.label)
    d.modified = time()
    set.drops[#set.drops + 1] = d
    set.revision = (set.revision or 0) + 1
    changed()
    return d.id
  end

  function S.UpdateDrop(ref, id, fields)
    note("UpdateDrop", ref, id, copy(fields))
    local kind = parse(ref)
    if kind ~= "user" then return nil, "built-in soundtracks are read-only" end
    local set = S.Get(ref)
    if not set then return nil, "no such soundtrack" end
    local i, old = findIndex(set, id)
    if not i then return nil, "no such drop" end
    local merged = copy(old)
    for k, v in pairs(fields) do
      if v == false or v == "" then merged[k] = nil else merged[k] = copy(v) end
    end
    if fields.disabled == false then merged.disabled = nil end
    local d, err = S.Validate(merged)
    if not d then return nil, err end
    if d.id ~= id and findIndex(set, d.id) then return nil, "this soundtrack already has a drop for that place" end
    snapshot("edit " .. (old.label or id))
    d.modified = time()
    set.drops[i] = d
    set.revision = (set.revision or 0) + 1
    changed()
    return true, d.id
  end

  function S.RemoveDrop(ref, id)
    note("RemoveDrop", ref, id)
    local set = S.Get(ref)
    if not set or parse(ref) ~= "user" then return nil, "read-only" end
    local i, d = findIndex(set, id)
    if not i then return nil, "no such drop" end
    snapshot("delete " .. (d.label or id))
    tremove(set.drops, i)
    set.revision = (set.revision or 0) + 1
    changed()
    return true
  end

  local function ensureMyDrops()
    local ref = "user:my-drops"
    if not db().soundtracks["my-drops"] then
      db().soundtracks["my-drops"] = { id = "my-drops", name = "My Drops", author = "", description = "", tags = {},
        revision = 0, created = time(), modified = time(), drops = {} }
    end
    local list = ns.cdb.active or db().active
    for i = #list, 1, -1 do if list[i] == ref then tremove(list, i) end end
    tinsert(list, 1, ref)
    return ref, db().soundtracks["my-drops"]
  end

  -- Soundtracks.EnsureMyDrops (beyond CONTRACTS 2.5): My Drops created when missing and first.
  function S.EnsureMyDrops()
    note("EnsureMyDrops")
    local ref = ensureMyDrops()
    changed()
    return ref
  end

  local function effective(id)
    for _, ref in ipairs(S.ActiveRefs()) do
      if ref ~= "user:my-drops" then
        local d = S.GetDrop(ref, id)
        if d then return d, ref end
      end
    end
    for _, set in ipairs(builtins) do
      for _, d in ipairs(set.drops) do if d.id == id then return d, "builtin:" .. set.id end end
    end
    return nil
  end

  function S.OverrideHere(builtinDropId, fields)
    note("OverrideHere", builtinDropId, copy(fields))
    local src, srcRef = effective(builtinDropId)
    if not src then return nil, "no drop here" end
    snapshot("change the song at " .. (src.label or builtinDropId))
    local ref, set = ensureMyDrops()
    local i = findIndex(set, builtinDropId)
    local d = copy(src)
    d.basedOn = string.match(srcRef, "^%a+:(.+)$")
    d.disabled = nil
    for k, v in pairs(fields or {}) do if v == false or v == "" then d[k] = nil else d[k] = copy(v) end end
    d.modified = time()
    if i then set.drops[i] = d else set.drops[#set.drops + 1] = d end
    set.revision = (set.revision or 0) + 1
    changed()
    return ref, d.id
  end

  function S.TurnOffHere(id)
    note("TurnOffHere", id)
    local src, srcRef = effective(id)
    snapshot("turn off " .. id)
    local ref, set = ensureMyDrops()
    local d = { id = id, kind = src and src.kind, mapID = src and src.mapID, areaID = src and src.areaID,
      subzone = src and src.subzone, instanceID = src and src.instanceID, label = src and src.label,
      disabled = true, basedOn = srcRef and string.match(srcRef, "^%a+:(.+)$"), modified = time() }
    local i = findIndex(set, id)
    if i then set.drops[i] = d else set.drops[#set.drops + 1] = d end
    changed()
    return ref, id
  end

  function S.Revert(id)
    note("Revert", id)
    local set = db().soundtracks["my-drops"]
    if not set then return nil, "nothing to revert" end
    local i = findIndex(set, id)
    if not i then return nil, "nothing to revert" end
    snapshot("revert " .. id)
    tremove(set.drops, i)
    changed()
    return true
  end

  function S.CanUndo()
    local top = undo[#undo]
    if not top then return false end
    return true, top.what
  end

  function S.Undo()
    note("Undo")
    local top = tremove(undo)
    if not top then return false end
    db().soundtracks = top.soundtracks
    db().active = top.active
    ns.cdb.active = top.cactive
    changed()
    return true, top.what
  end

  -- One entry per drop of every active soundtrack, in priority order: overriddenBy = the
  -- soundtracks above it that claim the same place (empty for what plays); drafts are listed
  -- with draft = true and claim nothing (as the real Soundtracks.lua does).
  function S.Merged()
    local refs = S.ActiveRefs()
    local claimers = {}
    for _, ref in ipairs(refs) do
      local set = S.Get(ref)
      for _, d in ipairs(set and set.drops or {}) do
        local draft = (not d.disabled) and (not d.artist or d.artist == "" or not d.title or d.title == "")
        if not draft then
          claimers[d.id] = claimers[d.id] or {}
          tinsert(claimers[d.id], ref)
        end
      end
    end
    local out = {}
    for r, ref in ipairs(refs) do
      local set = S.Get(ref)
      for _, d in ipairs(set and set.drops or {}) do
        local draft = (not d.disabled) and (not d.artist or d.artist == "" or not d.title or d.title == "")
        local above = {}
        for _, c in ipairs(claimers[d.id] or {}) do
          if c == ref then break end
          above[#above + 1] = c
        end
        out[#out + 1] = { drop = copy(d), setRef = ref, overriddenBy = above, disabled = d.disabled == true,
                          draft = draft, priority = r, plays = not draft and not d.disabled and #above == 0 }
      end
    end
    return out
  end

  if ns.On then ns.On("ND_INIT", function() S.Init() end) end

  -- --------------------------------------------------------------- Share
  local Sh = {}
  ns.Share = Sh
  F.codes = {}            -- code body -> { pack =, warnings = }
  local codeSeq = 0

  local function toPack(set, ref)
    local drops, omitted = {}, 0
    for _, d in ipairs(set.drops) do
      local draft = (not d.disabled) and (not d.artist or d.artist == "" or not d.title or d.title == "")
      if draft then
        omitted = omitted + 1
      else
        local p = { zone = d.mapID, subzone = d.subzone, areaID = d.areaID, instanceID = d.instanceID,
          label = d.label, artist = d.artist, title = d.title, youtube = d.youtube,
          spotify = d.spotify and ("spotify:track:" .. d.spotify) or nil, startMs = d.startMs, durationMs = d.durationMs,
          volume = d.volume, tags = copy(d.tags), note = d.note, disabled = d.disabled }
        if d.file then p.file = d.file; p.inGameFile = true end
        drops[#drops + 1] = p
      end
    end
    return { format = "needledropper-pack", version = 1, id = set.id, name = set.name, author = set.author or "",
             description = set.description or "", tags = copy(set.tags or {}), revision = set.revision or 0,
             drops = drops }, omitted
  end

  function Sh.FromSet(ref)
    note("Share.FromSet", ref)
    local set = S.Get(ref)
    if not set then return nil end
    return toPack(set, ref)
  end

  function Sh.Encode(pack)
    note("Share.Encode", copy(pack))
    if type(pack) ~= "table" or type(pack.drops) ~= "table" or #pack.drops == 0 then return nil, "the soundtrack has no finished drops" end
    codeSeq = codeSeq + 1
    local body = "RkFLRS" .. codeSeq .. string.gsub(pack.id or "x", "[^A-Za-z0-9]", "") .. "A=="
    F.codes[body] = { pack = copy(pack), warnings = {} }
    return "!NeedleDropper:1!" .. body .. "!"
  end

  -- Registers a code the fake decoder understands.
  function F.AddCode(body, pack, warnings)
    F.codes[body] = { pack = copy(pack), warnings = copy(warnings or {}) }
    return "!NeedleDropper:1!" .. body .. "!"
  end

  local FOREIGN = { { "!WA:", "WeakAuras" }, { "!E1!", "ElvUI" }, { "!PLATER:", "Plater" }, { "!~MDT2~", "MDT" },
                    { "!CELL:", "Cell" } }

  function Sh.Decode(text)
    note("Share.Decode", text)
    if type(text) ~= "string" then return nil, "notcode" end
    if #text > 65536 then return nil, "toobig" end
    local s, e, ver = string.find(text, "!NeedleDropper:(%d+)!")
    if not s then
      for _, f in ipairs(FOREIGN) do
        if string.find(text, f[1], 1, true) then return nil, "foreign:" .. f[2] end
      end
      return nil, "notcode"
    end
    if tonumber(ver) ~= 1 then return nil, (tonumber(ver) or 0) > 1 and "newer" or "damaged" end
    local body, term = string.match(text, "^([A-Za-z0-9+/=_ \t\r\n%-]*)(!?)", e + 1)
    body = string.gsub(body or "", "[ \t\r\n]", "")
    local hit = F.codes[body]
    if hit and term == "!" then return copy(hit.pack), copy(hit.warnings) end
    if term == "" then return nil, "cutoff" end
    return nil, "damaged"
  end

  function Sh.Validate(value) note("Share.Validate"); return copy(value), {} end

  function Sh.Import(pack, o)
    note("Share.Import", copy(pack), copy(o))
    local name = trim((o and o.name) or pack.name or "Imported")
    local base = name
    local n = 1
    while nameTaken(name) do n = n + 1; name = base .. " (" .. n .. ")" end
    snapshot("import " .. name)
    local id = uniqueId(pack.id or S.Slugify(name))
    local drops = {}
    for _, p in ipairs(pack.drops or {}) do
      local d = S.Validate({ mapID = p.zone or p.mapID, subzone = p.subzone, areaID = p.areaID, instanceID = p.instanceID,
        label = p.label, artist = p.artist, title = p.title, youtube = p.youtube,
        spotify = p.spotify and string.match(p.spotify, "([0-9A-Za-z]+)$") or nil,
        startMs = p.startMs, durationMs = p.durationMs, volume = p.volume, tags = p.tags, note = p.note,
        file = p.file, disabled = p.disabled })
      if d then drops[#drops + 1] = d end
    end
    db().soundtracks[id] = { id = id, name = name, author = pack.author or "", description = pack.description or "",
      tags = copy(pack.tags or {}), revision = pack.revision or 0, created = time(), modified = time(), drops = drops,
      importedFrom = pack.id }
    changed()
    return "user:" .. id
  end

  function Sh.CanUpdate(pack)
    note("Share.CanUpdate", pack and pack.id)
    if type(pack) ~= "table" or not pack.author or pack.author == "" then return nil end
    for id, set in pairs(db().soundtracks) do
      if (set.importedFrom == pack.id or id == pack.id) and set.author == pack.author
         and (pack.revision or 0) > (set.revision or 0) then
        return "user:" .. id
      end
    end
    return nil
  end

  function Sh.Update(ref, pack)
    note("Share.Update", ref, copy(pack))
    local set = S.Get(ref)
    if not set then return nil, "no such soundtrack" end
    snapshot("update " .. set.name)
    local drops = {}
    for _, p in ipairs(pack.drops or {}) do
      local d = S.Validate({ mapID = p.zone or p.mapID, subzone = p.subzone, areaID = p.areaID, instanceID = p.instanceID,
        label = p.label, artist = p.artist, title = p.title, youtube = p.youtube, startMs = p.startMs,
        durationMs = p.durationMs, note = p.note })
      if d then drops[#drops + 1] = d end
    end
    set.drops = drops
    set.revision = pack.revision or set.revision
    changed()
    return true
  end

  -- ----------------------------------------------------------- Companion
  local C = {}
  ns.Companion = C
  F.companion = { present = false, appVersion = nil, written = 0, spotify = false, youtube = false, transports = {} }
  F.tracks = {}
  F.imports = {}
  function C.Data() return F.companion.present and copy(F.companion) or nil end
  function C.Status() return copy(F.companion) end
  function C.Track(drop)
    if type(drop) ~= "table" then return nil end
    if drop.spotify and F.tracks["sp:" .. drop.spotify] then return copy(F.tracks["sp:" .. drop.spotify]) end
    local key = "q:" .. string.lower(drop.artist or "") .. "\31" .. string.lower(drop.title or "")
    return copy(F.tracks[key])
  end
  function C.Imports() return copy(F.imports) end
  -- 1.2.1 (DESIGN-v1.2.1 1.1): the music block and the version checks MusicServices.lua reads.
  function C.Music() return F.companion.present and copy(F.companion.music) or nil end
  function C.AtLeast(version, a, b, c)
    if type(version) ~= "string" then return false end
    local x, y, z = string.match(version, "^([0-9]+)%.([0-9]+)%.?([0-9]*)")
    x, y, z = tonumber(x), tonumber(y), tonumber(z) or 0
    if not x then return false end
    if x ~= a then return x > a end
    if y ~= b then return y > b end
    return z >= (c or 0)
  end
  function C.Supports(feature)
    return feature == "config" and F.companion.present and C.AtLeast(F.companion.appVersion, 1, 2, 1) or false
  end
  function C.StatusLine()
    local st = F.companion
    if not st.present then return "NeedleDropper app not found: Spotify and YouTube songs need it" end
    if not st.appVersion or st.appVersion < "1.2" then
      return "Update the NeedleDropper app to 1.2 to play Spotify and YouTube songs"
    end
    return "App seen " .. (st.seenText or "2 min") .. " before login" .. (st.spotify and " \194\183 Spotify connected" or "")
  end

  F.ns = ns
  return S, Sh, C
end

-- Director entry points CONTRACTS 2.9 lets the window call: every call is recorded
-- (F.playTests, F.suppressed) and then handed to the real Director.
function F.InstallDirector(ns)
  local D = ns.Director
  if type(D) ~= "table" or D.__uiFakesInstalled then return end
  D.__uiFakesInstalled = true
  local realPlayTest, realSuppress, start = D.PlayTest, D.SuppressHere, D.StartActive
  D.PlayTest = function(drop)
    F.playTests[#F.playTests + 1] = copy(drop)
    if realPlayTest then return realPlayTest(drop) end
    if type(drop) == "table" and start then return start(drop, { isTest = true }) end
  end
  D.SuppressHere = function(id)
    F.suppressed[#F.suppressed + 1] = id
    if realSuppress then return realSuppress(id) end
    if D.suppress and ns.Location and ns.Location.Candidates then
      for _, d in ipairs(ns.Location.Candidates()) do
        if d.id == id then D.suppress[id] = D.EntryTime(d) end
      end
    end
  end
end

-- ======================================================================
-- Forever's gamepad UI (Blizzard_GamepadSharedUtility / Blizzard_GamepadSmartNavigation), for
-- the window's opt-in (UI/Window.lua W.GamepadShown): InputUtil.IsGamepadUIEnabled, and
-- GamepadMode.FrameControlsManager:FrameShown / FrameHidden and SmartNavigation:
-- RefreshButtonGroups that record their calls. Like the real SecureHelper-wrapped functions, an
-- insecure call inside the combat restriction is refused ("Interface action failed"), recorded in
-- SHIM.gamepad.blocked.
-- ======================================================================

SHIM.gamepad = { enabled = false, shown = {}, hidden = {}, refreshed = {}, blocked = {}, frames = {} }

function SHIM.EnableGamepadUI(on)
  local G = SHIM.gamepad
  G.enabled = on ~= false
  InputUtil = InputUtil or {}
  function InputUtil.IsGamepadUIEnabled() return SHIM.gamepad.enabled end
  local function refused(what, frame)
    if SHIM.state.inCombat then
      G.blocked[#G.blocked + 1] = { what = what, name = frame and frame.GetName and frame:GetName() or nil }
      return true
    end
    return false
  end
  local FCM = {}
  function FCM:FrameShown(frame, isPopup, shouldFocus)
    if not SHIM.gamepad.enabled or refused("FrameShown", frame) then return end
    G.shown[#G.shown + 1] = frame
    G.frames[frame] = true
    return true
  end
  function FCM:FrameHidden(frame)
    if not SHIM.gamepad.enabled or refused("FrameHidden", frame) then return end
    G.hidden[#G.hidden + 1] = frame
    G.frames[frame] = nil
    return true
  end
  GamepadMode = { FrameControlsManager = FCM }
  SmartNavigation = {}
  function SmartNavigation:RefreshButtonGroups(frame)
    if refused("RefreshButtonGroups", frame) then return end
    G.refreshed[#G.refreshed + 1] = frame
  end
end

-- Wrap every class method (and those added above) so calls are recorded.
for _, class in ipairs({ FrameClass, RegionClass, ButtonClass, CheckButtonClass, EditBoxClass, SliderClass,
                         ScrollFrameClass, DropdownButtonClass, EventFrameClass }) do
  wrapClass(class)
end

return U
