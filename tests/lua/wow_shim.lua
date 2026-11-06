-- wow_shim.lua -- a fake World of Warcraft (Forever / Mainline 12.x API) environment for
-- running the NeedleDropper addon under lupa.lua51 in pytest.
--
-- Everything observable lives in the global SHIM table:
--   SHIM.state         mutable game state (location, gates, cvars, exploration, chat, screen ...)
--   SHIM.log           what the addon did (prints, chat lines, music/sound calls, cvar sets, errors ...)
--   SHIM.FireEvent(name, ...)   dispatch a game event to every frame registered for it
--   SHIM.Advance(seconds)       move the fake clock, running C_Timer callbacks and OnUpdate scripts
--   SHIM.RenderFrame()          one OnUpdate pass at the current time (elapsed 0)
--   SHIM.MakeSecret(desc)       a "secret value" that errors (and records) on any touch
--   SHIM.SetLocale(locale)      switch the client language (map / area names, STANDARD_TEXT_FONT)
--
-- Lua 5.1 only. Loaded once per LuaRuntime by tests/addon_harness.py.

local SHIM = {}
_G.SHIM = SHIM

local floor = math.floor
local tinsert = table.insert

SHIM.START = 1000.0          -- GetTime() at shim load
SHIM.EPOCH = 1762365600      -- time() at shim load (2025-11-05T18:00:00Z)
SHIM.now = SHIM.START
SHIM.FRAME_DT = 1 / 30       -- OnUpdate granularity while any visible frame has an OnUpdate script

SHIM.log = {
  prints = {},        -- every line given to print / DEFAULT_CHAT_FRAME:AddMessage
  chat = {},          -- every SendSystemMessage: { text = , hidden = bool }
  chatlog = {},       -- lines that would have reached WoWChatLog.txt (SendSystemMessage while logging)
  errors = {},        -- errors reported to the error handler / raised by scripts and timers
  music = {},         -- { "play", path } / { "stop" }
  sounds = {},        -- PlaySoundFile calls: { path = , channel = , handle = , willPlay = }
  stoppedSounds = {}, -- StopSound calls: { handle = , fade = }
  cvarSets = {},      -- { name = , value = , temp = bool }
  secretTouches = {}, -- any metamethod hit on a secret value
  logging = {},       -- LoggingChat(newState) calls
  openedCategory = {},-- Settings.OpenToCategory ids
  unknownEvents = {}, -- RegisterEvent attempts for events the shim doesn't know
}

SHIM.state = {
  loggedIn = false,
  -- location
  mapID = 1411,               -- C_Map.GetBestMapForUnit("player")
  mapNilCalls = 0,            -- return nil for this many GetBestMapForUnit calls (login race)
  subzone = "",
  zoneTextOverride = nil,
  inInstance = false,
  instanceType = "none",
  instanceID = nil,
  instanceName = nil,
  noPosition = false,
  continentInstanceID = 1,
  -- gates
  inCombat = false,
  dead = false,
  taxi = false,
  cinematic = false,
  encounterRestricted = false,
  level = 10,
  -- exploration
  exploredTextures = {},      -- [uiMapID] = count of explored overlay textures
  exploredAreas = {},         -- [uiMapID] = { areaID, ... } at the player's position
  -- chat
  loggingChat = false,
  loggingChatBlocked = false, -- LoggingChat(true) does not stick
  chatLockdown = false,
  -- audio
  playableFiles = {},         -- [path] = true -> PlaySoundFile will play it
  soundLengths = {},          -- [path] = seconds (default: plays "forever")
  musicPath = nil,
  hasTempCVar = false,        -- provide C_CVar.SetTempCVar
  -- screen
  physW = 2560,
  physH = 1440,
  uiScale = 1.0,
  -- fonts
  badFonts = {},              -- [path] = true -> FontString:SetFont(path, ...) fails (returns false)
}

SHIM.cvars = {
  Sound_EnableMusic = "1",
  Sound_MusicVolume = "0.4",
  Sound_EnableAllSound = "1",
  uiScale = "1",
}

-- --------------------------------------------------------------- helpers ----

local function shimError(msg)
  error("wow_shim: " .. msg, 3)
end

local function recordError(err)
  tinsert(SHIM.log.errors, tostring(err))
end

local function traceback(err)
  return tostring(err) .. "\n" .. debug.traceback("", 2)
end

-- Runs fn(...) protected; failures go to SHIM.log.errors with a traceback.
local function protected(fn, ...)
  local n = select("#", ...)
  local args = { ... }
  local ok, err = xpcall(function() return fn(unpack(args, 1, n)) end, traceback)
  if not ok then recordError(err) end
  return ok
end
SHIM.protected = protected

local function copy(t)
  if type(t) ~= "table" then return t end
  local out = {}
  for k, v in pairs(t) do out[k] = copy(v) end
  return out
end

-- ------------------------------------------------------------ secrets ----

local secretSet = setmetatable({}, { __mode = "k" })
local secretMeta = {}
local function touched(what)
  return function(s)
    tinsert(SHIM.log.secretTouches, what)
    error("touched a secret value (" .. what .. ")", 2)
  end
end
for _, mm in ipairs({ "__index", "__newindex", "__concat", "__eq", "__lt", "__le", "__len",
                      "__call", "__tostring", "__add", "__sub", "__mul", "__div", "__mod",
                      "__pow", "__unm" }) do
  secretMeta[mm] = touched(mm)
end
secretMeta.__metatable = false

function SHIM.MakeSecret(desc)
  local s = setmetatable({}, secretMeta)
  secretSet[s] = desc or true
  return s
end

function issecretvalue(v)
  return secretSet[v] ~= nil
end

-- ------------------------------------------------------------- events ----

SHIM.KNOWN_EVENTS = {}
for _, e in ipairs({
  "ADDON_LOADED", "VARIABLES_LOADED", "PLAYER_LOGIN", "PLAYER_LOGOUT", "PLAYER_ENTERING_WORLD",
  "PLAYER_LEAVING_WORLD", "ZONE_CHANGED", "ZONE_CHANGED_INDOORS", "ZONE_CHANGED_NEW_AREA",
  "PLAYER_MAP_CHANGED", "MAP_EXPLORATION_UPDATED", "UI_INFO_MESSAGE", "UI_ERROR_MESSAGE",
  "CHAT_MSG_SYSTEM", "PLAYER_REGEN_DISABLED", "PLAYER_REGEN_ENABLED", "PLAYER_DEAD",
  "PLAYER_ALIVE", "PLAYER_UNGHOST", "PLAYER_CONTROL_LOST", "PLAYER_CONTROL_GAINED",
  "CINEMATIC_START", "CINEMATIC_STOP", "PLAY_MOVIE", "STOP_MOVIE", "LOADING_SCREEN_ENABLED",
  "LOADING_SCREEN_DISABLED", "SHARD_TRANSFER", "UI_SCALE_CHANGED", "DISPLAY_SIZE_CHANGED",
  "PLAYER_LEVEL_UP", "PLAYER_FLAGS_CHANGED", "UPDATE_CHAT_WINDOWS", "CVAR_UPDATE",
  "PLAYER_TARGET_CHANGED",
}) do SHIM.KNOWN_EVENTS[e] = true end

SHIM.frames = {}            -- every frame ever created, in creation order
SHIM.eventOrder = {}        -- [event] = { frame, ... } in registration order

-- ------------------------------------------------------------- regions ----

local Region = {}
local RegionMT = { __index = Region }

local function newRegion(kind, name, parent)
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
  return r
end

function Region:GetObjectType() return self.__kind end
function Region:IsObjectType(t) return self.__kind == t end
function Region:GetName() return self.__name end
function Region:GetParent() return self.__parent end
function Region:SetParent(p)
  if type(p) == "string" then p = _G[p] end
  self.__parent = p
end
function Region:Show() self.__shown = true end
function Region:Hide() self.__shown = false end
function Region:SetShown(v) self.__shown = not not v end
function Region:IsShown() return self.__shown end
function Region:IsVisible()
  if not self.__shown then return false end
  if self.__parent then return self.__parent:IsVisible() end
  return true
end
function Region:SetAlpha(a)
  if type(a) ~= "number" then shimError("SetAlpha needs a number") end
  self.__alpha = math.max(0, math.min(1, a))
end
function Region:GetAlpha() return self.__alpha end
function Region:SetScale(s)
  if type(s) ~= "number" or s <= 0 then shimError("SetScale needs a positive number") end
  self.__scale = s
end
function Region:GetScale() return self.__scale end
function Region:GetEffectiveScale()
  local s = self.__scale
  if self.__parent and self.__parent.GetEffectiveScale then
    s = s * self.__parent:GetEffectiveScale()
  end
  return s
end
function Region:SetIgnoreParentScale(v) self.__ignoreParentScale = v end
function Region:SetSize(w, h)
  if type(w) ~= "number" or type(h) ~= "number" then shimError("SetSize needs numbers") end
  self.__w, self.__h = w, h
end
function Region:SetWidth(w) self.__w = w end
function Region:SetHeight(h) self.__h = h end
function Region:GetWidth() return self.__w end
function Region:GetHeight() return self.__h end
function Region:GetSize() return self.__w, self.__h end

local VALID_POINTS = { TOPLEFT = 1, TOP = 1, TOPRIGHT = 1, LEFT = 1, CENTER = 1, RIGHT = 1,
                       BOTTOMLEFT = 1, BOTTOM = 1, BOTTOMRIGHT = 1 }

function Region:SetPoint(point, a, b, c, d)
  if not VALID_POINTS[point] then shimError("SetPoint: bad point " .. tostring(point)) end
  local rel, relPoint, x, y
  if type(a) == "number" then
    x, y = a, b
  else
    rel = a
    if type(b) == "string" then
      relPoint, x, y = b, c, d
    else
      x, y = b, c
    end
  end
  if type(rel) == "string" then rel = _G[rel] end
  if relPoint ~= nil and not VALID_POINTS[relPoint] then shimError("SetPoint: bad relativePoint") end
  if self.__points[point] == nil then tinsert(self.__pointOrder, point) end
  self.__points[point] = { point, rel or self.__parent, relPoint or point, x or 0, y or 0 }
end
function Region:ClearAllPoints()
  self.__points = {}
  self.__pointOrder = {}
end
function Region:GetNumPoints() return #self.__pointOrder end
function Region:GetPoint(i)
  local p = self.__points[self.__pointOrder[i or 1]]
  if not p then return nil end
  return p[1], p[2], p[3], p[4], p[5]
end
function Region:SetAllPoints(rel)
  self:ClearAllPoints()
  self:SetPoint("TOPLEFT", rel or self.__parent, "TOPLEFT", 0, 0)
  self:SetPoint("BOTTOMRIGHT", rel or self.__parent, "BOTTOMRIGHT", 0, 0)
end
-- texture / fontstring common
function Region:SetDrawLayer(layer, sub) self.__layer = layer; self.__sublayer = sub end
function Region:GetDrawLayer() return self.__layer, self.__sublayer end
function Region:SetVertexColor(r, g, b, a) self.__vertex = { r, g, b, a } end
function Region:SetSnapToPixelGrid(v) self.__snap = v end
function Region:SetTexelSnappingBias(v) self.__texelBias = v end
-- textures
function Region:SetColorTexture(r, g, b, a)
  if type(r) ~= "number" or type(g) ~= "number" or type(b) ~= "number" then
    shimError("SetColorTexture needs numbers")
  end
  self.__color = { r, g, b, a or 1 }
  self.__texture = nil
end
function Region:SetTexture(path) self.__texture = path; self.__color = nil end
function Region:GetTexture() return self.__texture end
function Region:SetTexCoord(...) self.__texCoord = { ... } end
function Region:SetBlendMode(m) self.__blend = m end
function Region:SetGradient(...) self.__gradient = { ... } end
-- font strings
function Region:SetText(t)
  if t ~= nil and type(t) ~= "string" and type(t) ~= "number" then shimError("SetText needs a string") end
  self.__text = t ~= nil and tostring(t) or nil
end
function Region:GetText() return self.__text end
function Region:SetFormattedText(fmt, ...) self.__text = string.format(fmt, ...) end
-- Like the client: returns false (keeping the old font) when the file cannot be used.
function Region:SetFont(path, size, flags)
  if type(path) ~= "string" or path == "" then shimError("SetFont needs a font file path") end
  if type(size) ~= "number" or size <= 0 then shimError("SetFont needs a positive size") end
  if flags ~= nil and type(flags) ~= "string" then shimError("SetFont flags must be a string") end
  if SHIM.state.badFonts[path] then return false end
  self.__font = { path, size, flags or "" }
  return true
end
function Region:GetFont()
  local f = self.__font or { "Fonts\\FRIZQT__.TTF", 12, "" }
  return f[1], f[2], f[3]
end
function Region:SetFontObject(o) self.__fontObject = o end
function Region:SetTextColor(r, g, b, a) self.__textColor = { r, g, b, a } end
function Region:SetShadowOffset(x, y) self.__shadowOffset = { x, y } end
function Region:SetShadowColor(r, g, b, a) self.__shadowColor = { r, g, b, a } end
function Region:SetJustifyH(j) self.__justifyH = j end
function Region:SetJustifyV(j) self.__justifyV = j end
function Region:SetWordWrap(v) self.__wordWrap = v end
function Region:SetNonSpaceWrap(v) self.__nonSpaceWrap = v end
function Region:SetMaxLines(n) self.__maxLines = n end
function Region:SetSpacing(n) self.__spacing = n end
function Region:SetTextScale(n) self.__textScale = n end
-- Rough glyph metrics: 0.6 x font size per character, 0.3 for a space, 1.0 for wide
-- (CJK / Hangul, lead byte >= 0xE3) characters; "||" draws one "|", colour codes nothing.
function Region:GetStringWidth()
  local text = self.__text or ""
  text = text:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("||", "|")
  local size = (self.__font and self.__font[2]) or 12
  local w = 0
  for ch in string.gmatch(text, "[%z\1-\127\194-\244][\128-\191]*") do
    local b = string.byte(ch)
    if b >= 227 then
      w = w + size
    elseif ch == " " then
      w = w + size * 0.3
    else
      w = w + size * 0.6
    end
  end
  return w
end
function Region:GetStringHeight() return 14 end

-- --------------------------------------------------------------- frames ----

local Frame = setmetatable({}, { __index = Region })
local FrameMT = { __index = Frame }

local SCRIPT_TYPES = { OnEvent = 1, OnUpdate = 1, OnShow = 1, OnHide = 1, OnSizeChanged = 1,
                       OnEnter = 1, OnLeave = 1, OnMouseDown = 1, OnMouseUp = 1, OnClick = 1 }

function Frame:RegisterEvent(event)
  if type(event) ~= "string" then shimError("RegisterEvent needs a string") end
  if not SHIM.KNOWN_EVENTS[event] then
    tinsert(SHIM.log.unknownEvents, event)
    error('Attempt to register unknown event "' .. event .. '"', 2)
  end
  if self.__events[event] then return true end
  self.__events[event] = true
  local order = SHIM.eventOrder[event]
  if not order then order = {}; SHIM.eventOrder[event] = order end
  tinsert(order, self)
  return true
end
function Frame:RegisterUnitEvent(event) return self:RegisterEvent(event) end
function Frame:UnregisterEvent(event)
  if not self.__events[event] then return end
  self.__events[event] = nil
  local order = SHIM.eventOrder[event]
  if order then
    for i = #order, 1, -1 do
      if order[i] == self then table.remove(order, i) end
    end
  end
end
function Frame:UnregisterAllEvents()
  for event in pairs(self.__events) do self:UnregisterEvent(event) end
end
function Frame:IsEventRegistered(event) return self.__events[event] == true end
function Frame:SetScript(kind, fn)
  if not SCRIPT_TYPES[kind] then shimError("SetScript: unsupported script " .. tostring(kind)) end
  if fn ~= nil and type(fn) ~= "function" then shimError("SetScript needs a function or nil") end
  self.__scripts[kind] = fn
end
function Frame:GetScript(kind) return self.__scripts[kind] end
function Frame:HookScript(kind, fn)
  local prev = self.__scripts[kind]
  if prev then
    self.__scripts[kind] = function(...) prev(...); fn(...) end
  else
    self.__scripts[kind] = fn
  end
end
function Frame:HasScript(kind) return SCRIPT_TYPES[kind] ~= nil end
function Frame:Show()
  local was = self:IsVisible()
  self.__shown = true
  if not was and self:IsVisible() and self.__scripts.OnShow then protected(self.__scripts.OnShow, self) end
end
function Frame:Hide()
  local was = self:IsVisible()
  self.__shown = false
  if was and self.__scripts.OnHide then protected(self.__scripts.OnHide, self) end
end
function Frame:SetShown(v) if v then self:Show() else self:Hide() end end
function Frame:SetFrameStrata(s)
  local valid = { BACKGROUND = 1, LOW = 1, MEDIUM = 1, HIGH = 1, DIALOG = 1, FULLSCREEN = 1,
                  FULLSCREEN_DIALOG = 1, TOOLTIP = 1 }
  if not valid[s] then shimError("SetFrameStrata: bad strata " .. tostring(s)) end
  self.__strata = s
end
function Frame:GetFrameStrata() return self.__strata or "MEDIUM" end
function Frame:SetFrameLevel(l)
  if type(l) ~= "number" then shimError("SetFrameLevel needs a number") end
  self.__level = math.min(10000, math.max(0, floor(l)))
end
function Frame:GetFrameLevel() return self.__level or 0 end
function Frame:SetToplevel(v) self.__toplevel = v end
function Frame:EnableMouse(v) self.__mouse = not not v end
function Frame:IsMouseEnabled() return self.__mouse == true end
function Frame:SetMouseClickEnabled(v) self.__mouseClick = v end
function Frame:SetMouseMotionEnabled(v) self.__mouseMotion = v end
function Frame:EnableMouseWheel(v) self.__wheel = v end
function Frame:EnableKeyboard(v) self.__keyboard = v end
function Frame:SetClampedToScreen(v) self.__clamped = v end
function Frame:SetMovable(v) self.__movable = v end
function Frame:SetDontSavePosition(v) self.__dontSave = v end
function Frame:SetFixedFrameStrata(v) self.__fixedStrata = v end
function Frame:SetFixedFrameLevel(v) self.__fixedLevel = v end
function Frame:GetChildren() return unpack(self.__children) end
function Frame:GetRegions() return unpack(self.__regions) end
function Frame:GetNumRegions() return #self.__regions end
function Frame:CreateTexture(name, layer, template, sub)
  local t = newRegion("Texture", name, self)
  t.__layer, t.__sublayer = layer, sub
  tinsert(self.__regions, t)
  if name then _G[name] = t end
  return t
end
function Frame:CreateFontString(name, layer, template)
  local f = newRegion("FontString", name, self)
  f.__layer = layer
  f.__template = template
  tinsert(self.__regions, f)
  if name then _G[name] = f end
  return f
end
function Frame:SetParent(p)
  if type(p) == "string" then p = _G[p] end
  if self.__parent and self.__parent.__children then
    local kids = self.__parent.__children
    for i = #kids, 1, -1 do if kids[i] == self then table.remove(kids, i) end end
  end
  self.__parent = p
  if p and p.__children then tinsert(p.__children, self) end
end

function CreateFrame(frameType, name, parent, template, id)
  if type(frameType) ~= "string" then shimError("CreateFrame needs a frame type") end
  if type(parent) == "string" then parent = _G[parent] end
  local f = newRegion(frameType, name, parent)
  setmetatable(f, FrameMT)
  f.__events = {}
  f.__scripts = {}
  f.__children = {}
  f.__regions = {}
  f.__template = template
  if parent and parent.__children then tinsert(parent.__children, f) end
  tinsert(SHIM.frames, f)
  if name then _G[name] = f end
  return f
end

UIParent = CreateFrame("Frame", "UIParent", nil)
function UIParent:GetScale() return SHIM.state.uiScale end
function UIParent:GetEffectiveScale() return SHIM.state.uiScale end
function UIParent:GetWidth() return 768 * SHIM.state.physW / SHIM.state.physH / SHIM.state.uiScale end
function UIParent:GetHeight() return 768 / SHIM.state.uiScale end
function UIParent:GetSize() return self:GetWidth(), self:GetHeight() end
WorldFrame = CreateFrame("Frame", "WorldFrame", nil)
function WorldFrame:GetWidth() return 768 * SHIM.state.physW / SHIM.state.physH end
function WorldFrame:GetHeight() return 768 end
MovieFrame = CreateFrame("Frame", "MovieFrame", UIParent)
MovieFrame:Hide()

function SHIM.FireEvent(event, ...)
  if not SHIM.KNOWN_EVENTS[event] then shimError("FireEvent: unknown event " .. tostring(event)) end
  local order = SHIM.eventOrder[event]
  if not order then return end
  local list = {}
  for i = 1, #order do list[i] = order[i] end
  for i = 1, #list do
    local f = list[i]
    if f.__events[event] and f.__scripts.OnEvent then
      protected(f.__scripts.OnEvent, f, event, ...)
    end
  end
end

-- --------------------------------------------------------------- timers ----

SHIM.timers = {}
local timerSeq = 0
local TimerHandle = {}
local TimerHandleMT = { __index = TimerHandle }
function TimerHandle:Cancel() self.__t.cancelled = true end
function TimerHandle:IsCancelled() return self.__t.cancelled end

local function addTimer(delay, fn, interval, iterations)
  if type(fn) ~= "function" then shimError("C_Timer callback must be a function") end
  if type(delay) ~= "number" then shimError("C_Timer delay must be a number") end
  timerSeq = timerSeq + 1
  local t = { due = SHIM.now + math.max(0, delay), fn = fn, interval = interval,
              remaining = iterations, id = timerSeq, cancelled = false }
  t.handle = setmetatable({ __t = t }, TimerHandleMT)
  tinsert(SHIM.timers, t)
  return t
end

C_Timer = {}
function C_Timer.After(delay, fn) addTimer(delay, fn) end
function C_Timer.NewTimer(delay, fn) return addTimer(delay, fn).handle end
function C_Timer.NewTicker(interval, fn, iterations)
  if type(interval) ~= "number" or interval <= 0 then shimError("NewTicker needs a positive interval") end
  return addTimer(interval, fn, interval, iterations).handle
end

local function nextTimer(limit)
  local best
  for i = 1, #SHIM.timers do
    local t = SHIM.timers[i]
    if not t.cancelled and t.due <= limit and (best == nil or t.due < best.due or (t.due == best.due and t.id < best.id)) then
      best = t
    end
  end
  return best
end

local function pruneTimers()
  local keep = {}
  for i = 1, #SHIM.timers do
    if not SHIM.timers[i].cancelled then keep[#keep + 1] = SHIM.timers[i] end
  end
  SHIM.timers = keep
end

local function runTimer(t)
  if t.interval then
    if t.remaining then
      t.remaining = t.remaining - 1
      if t.remaining <= 0 then t.cancelled = true end
    end
    t.due = t.due + t.interval
    protected(t.fn, t.handle)
  else
    t.cancelled = true
    protected(t.fn, t.handle)
  end
end

local function onUpdateFrames()
  local list = {}
  for i = 1, #SHIM.frames do
    local f = SHIM.frames[i]
    if f.__scripts.OnUpdate and f:IsVisible() then list[#list + 1] = f end
  end
  return list
end

function SHIM.Advance(seconds)
  local target = SHIM.now + (seconds or 0)
  local guard = 0
  while true do
    guard = guard + 1
    if guard > 2000000 then shimError("Advance: runaway timers") end
    local frames = onUpdateFrames()
    local frameAt = (#frames > 0) and math.min(SHIM.now + SHIM.FRAME_DT, target) or nil
    local t = nextTimer(frameAt or target)
    if t then
      if t.due > SHIM.now then SHIM.now = t.due end
      runTimer(t)
    elseif frameAt and frameAt > SHIM.now then
      local elapsed = frameAt - SHIM.now
      SHIM.now = frameAt
      for i = 1, #frames do
        local f = frames[i]
        if f.__scripts.OnUpdate and f:IsVisible() then protected(f.__scripts.OnUpdate, f, elapsed) end
      end
    else
      break
    end
    if guard % 64 == 0 then pruneTimers() end
  end
  SHIM.now = target
  pruneTimers()
end

-- Runs callbacks that are due now (e.g. C_Timer.After(0, ...)).
function SHIM.Flush() SHIM.Advance(0) end

-- Draws one frame right now: every visible frame's OnUpdate runs with elapsed = 0. (Advance
-- runs OnUpdate on a 1/30 s grid; a timer due exactly at the target time can end it before
-- the last frame, so tests that sample animation state at an exact time call this first.)
function SHIM.RenderFrame()
  local frames = onUpdateFrames()
  for i = 1, #frames do
    local f = frames[i]
    if f.__scripts.OnUpdate and f:IsVisible() then protected(f.__scripts.OnUpdate, f, 0) end
  end
end

function GetTime() return SHIM.now end
function GetTimePreciseSec() return SHIM.now end
function time(t)
  if t ~= nil then return os.time(t) end
  return floor(SHIM.EPOCH + (SHIM.now - SHIM.START))
end
function date(fmt, t) return os.date(fmt, t or time()) end
function debugprofilestop() return SHIM.now * 1000 end

-- ---------------------------------------------------------- error handler ----

local errorHandler = function(err)
  recordError(tostring(err) .. "\n" .. debug.traceback("", 2))
end
function geterrorhandler() return errorHandler end
function seterrorhandler(fn) errorHandler = fn end
function debugstack() return debug.traceback("", 2) end
function securecall(fn, ...)
  if type(fn) == "string" then fn = _G[fn] end
  return fn(...)
end
function hooksecurefunc(tbl, name, fn)
  if type(tbl) == "string" then tbl, name, fn = _G, tbl, name end
  local orig = tbl[name]
  tbl[name] = function(...)
    local r = { orig(...) }
    fn(...)
    return unpack(r)
  end
end

-- ------------------------------------------------------------- strings ----

_G.strlower = string.lower
_G.strupper = string.upper
_G.strlen = string.len
_G.strsub = string.sub
_G.strfind = string.find
_G.strmatch = string.match
_G.strrep = string.rep
_G.strbyte = string.byte
_G.strchar = string.char
_G.strrev = string.reverse
_G.format = string.format
_G.gsub = string.gsub
_G.gmatch = string.gmatch
_G.tinsert = table.insert
_G.tremove = table.remove
_G.tconcat = table.concat
_G.sort = table.sort
_G.floor = math.floor
_G.ceil = math.ceil
_G.abs = math.abs
_G.max = math.max
_G.min = math.min
_G.sqrt = math.sqrt
_G.mod = math.fmod

function strtrim(s, chars)
  if chars then
    local esc = chars:gsub("[%^%$%(%)%%%.%[%]%*%+%-%?]", "%%%0")
    return (s:gsub("^[" .. esc .. "]+", ""):gsub("[" .. esc .. "]+$", ""))
  end
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

function strsplit(delim, str, pieces)
  local out = {}
  local start = 1
  local plain = true
  while true do
    if pieces and #out == pieces - 1 then break end
    local i, j = string.find(str, delim, start, plain)
    if not i then break end
    out[#out + 1] = string.sub(str, start, i - 1)
    start = j + 1
  end
  out[#out + 1] = string.sub(str, start)
  return unpack(out)
end

function strjoin(delim, ...) return table.concat({ ... }, delim) end
function tostringall(...)
  local t = { ... }
  for i = 1, select("#", ...) do t[i] = tostring(t[i]) end
  return unpack(t, 1, select("#", ...))
end

function wipe(t)
  for k in pairs(t) do t[k] = nil end
  return t
end

function CopyTable(t, shallow)
  local out = {}
  for k, v in pairs(t) do
    if type(v) == "table" and not shallow then out[k] = CopyTable(v) else out[k] = v end
  end
  return out
end

function Mixin(obj, ...)
  for i = 1, select("#", ...) do
    local m = select(i, ...)
    for k, v in pairs(m) do obj[k] = v end
  end
  return obj
end
function CreateFromMixins(...) return Mixin({}, ...) end

function tContains(t, v)
  for i = 1, #t do if t[i] == v then return true end end
  return false
end

function tIndexOf(t, v)
  for i = 1, #t do if t[i] == v then return i end end
  return nil
end

-- --------------------------------------------------------------- output ----

local function addPrint(msg) tinsert(SHIM.log.prints, msg) end
function print(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
  addPrint(table.concat(parts, " "))
end
DEFAULT_CHAT_FRAME = CreateFrame("Frame", "ChatFrame1", UIParent)
function DEFAULT_CHAT_FRAME:AddMessage(msg, r, g, b)
  if type(msg) ~= "string" then shimError("AddMessage needs a string") end
  addPrint(msg)
end
SELECTED_CHAT_FRAME = DEFAULT_CHAT_FRAME

-- ------------------------------------------------------------ game info ----

SHIM.locale = "enUS"
function GetLocale() return SHIM.locale end
STANDARD_TEXT_FONT = "Fonts\\FRIZQT__.TTF"
function GetBuildInfo() return "1.60.1", "70790", "Nov 5 2026", 16001 end
function IsLoggedIn() return SHIM.state.loggedIn end
function GetPhysicalScreenSize() return SHIM.state.physW, SHIM.state.physH end
function GetScreenWidth() return UIParent:GetWidth() end
function GetScreenHeight() return UIParent:GetHeight() end
function InCombatLockdown() return SHIM.state.inCombat end

-- TOC metadata of the addon under test: tests/addon_harness.py copies the "## Key: value" lines
-- of NeedleDropper.toc into SHIM.addonMetadata before the addon loads.
SHIM.addonMetadata = { Version = "1.2.0", Title = "NeedleDropper" }
C_AddOns = {}
function C_AddOns.GetAddOnMetadata(addon, field)
  if addon ~= "NeedleDropper" and addon ~= 1 then return nil end
  return SHIM.addonMetadata[field]
end
GetAddOnMetadata = C_AddOns.GetAddOnMetadata
function C_AddOns.IsAddOnLoaded(name) return name == "NeedleDropper" end

Enum = {
  UIMapType = { Cosmic = 0, World = 1, Continent = 2, Zone = 3, Dungeon = 4, Micro = 5, Orphan = 6 },
  AddOnRestrictionType = { Combat = 0, Encounter = 1, ChallengeMode = 2 },
}

PixelUtil = {}
function PixelUtil.GetPixelToUIUnitFactor() return 768.0 / SHIM.state.physH end
function PixelUtil.SetSize(region, w, h) region:SetSize(w, h) end

-- ------------------------------------------------------------------ maps ----

SHIM.maps = {
  [946] = { name = "Cosmic", mapType = 0, parentMapID = 0 },
  [947] = { name = "Azeroth", mapType = 1, parentMapID = 946 },
  [1414] = { name = "Kalimdor", mapType = 2, parentMapID = 947 },
  [1415] = { name = "Eastern Kingdoms", mapType = 2, parentMapID = 947 },
  [1411] = { name = "Durotar", mapType = 3, parentMapID = 1414 },
  [1454] = { name = "Orgrimmar", mapType = 3, parentMapID = 1414 },
  [2482] = { name = "Mount Hyjal", mapType = 3, parentMapID = 1414 },
  [2521] = { name = "Zephras Isle", mapType = 3, parentMapID = 947 },  -- a zone directly under Azeroth (UiMap 1.60.1)
  [1429] = { name = "Elwynn Forest", mapType = 3, parentMapID = 1415 },
  [1436] = { name = "Westfall", mapType = 3, parentMapID = 1415 },
  [1453] = { name = "Stormwind City", mapType = 3, parentMapID = 1415 },
  [291] = { name = "The Deadmines", mapType = 4, parentMapID = 1436 },
  [9001] = { name = "Fargodeep Mine", mapType = 5, parentMapID = 1429 },
  [9002] = { name = "Fargodeep Mine Lower", mapType = 5, parentMapID = 9001 },
  [9100] = { name = "Some Orphan", mapType = 6, parentMapID = 0 },
}
for id, m in pairs(SHIM.maps) do m.mapID = id end

SHIM.areas = {
  [9] = "Northshire Valley",
  [12] = "Elwynn Forest",
  [57] = "Fargodeep Mine",
  [87] = "Goldshire",
  [88] = "Lion's Pride Inn",
  [108] = "Sentinel Hill",
  [362] = "Razor Hill",
}

-- Client languages: what C_Map map / area names and STANDARD_TEXT_FONT are on that client.
-- (Instance names come from SHIM.state.instanceName; subzone text from SHIM.state.subzone.)
SHIM.LOCALES = {
  enUS = { font = "Fonts\\FRIZQT__.TTF", maps = {}, areas = {} },
  frFR = { font = "Fonts\\FRIZQT__.TTF",
    maps = { [1411] = "Durotar", [1429] = "Forêt d'Elwynn", [1436] = "Marche de l'Ouest",
             [1453] = "Hurlevent", [291] = "Les Mortemines" },
    areas = { [9] = "Vallée de Comté-du-Nord", [12] = "Forêt d'Elwynn", [87] = "Comté-de-l'Or",
              [108] = "Colline des Sentinelles" } },
  deDE = { font = "Fonts\\FRIZQT__.TTF",
    maps = { [1429] = "Wald von Elwynn", [1436] = "Westfall", [1453] = "Sturmwind", [291] = "Die Todesminen" },
    areas = { [9] = "Nordhaintal", [87] = "Goldhain", [108] = "Späherkuppe" } },
  ruRU = { font = "Fonts\\FRIZQT___CYR.TTF",
    maps = { [1411] = "Дуротар", [1429] = "Элвиннский лес", [1436] = "Западный Край",
             [1453] = "Штормград", [291] = "Мертвые копи" },
    areas = { [9] = "Долина Североземья", [87] = "Златоземье", [108] = "Сторожевой холм" } },
  zhCN = { font = "Fonts\\ARKai_T.ttf",
    maps = { [1411] = "杜隆塔尔", [1429] = "艾尔文森林", [1436] = "西部荒野", [1453] = "暴风城",
             [291] = "死亡矿井" },
    areas = { [9] = "北郡山谷", [87] = "闪金镇", [88] = "狮王之傲旅店", [108] = "哨兵岭" } },
  koKR = { font = "Fonts\\2002.TTF",
    maps = { [1429] = "엘윈 숲", [1436] = "서부 몰락지대", [1453] = "스톰윈드" },
    areas = { [87] = "골드샤이어" } },
}

local baseMapNames, baseAreaNames = {}, {}
for id, m in pairs(SHIM.maps) do baseMapNames[id] = m.name end
for id, name in pairs(SHIM.areas) do baseAreaNames[id] = name end

-- Switches the client language. Call before the addon loads (names are cached like in game).
function SHIM.SetLocale(locale)
  local L = SHIM.LOCALES[locale]
  if not L then shimError("SetLocale: unknown locale " .. tostring(locale)) end
  SHIM.locale = locale
  for id, m in pairs(SHIM.maps) do m.name = L.maps[id] or baseMapNames[id] or m.name end
  for id, name in pairs(baseAreaNames) do SHIM.areas[id] = L.areas[id] or name end
  STANDARD_TEXT_FONT = L.font
end

local function walkZone(id)
  local guard = 0
  local info = id and SHIM.maps[id]
  while info and info.mapType > 3 and info.parentMapID and info.parentMapID > 0 and guard < 20 do
    guard = guard + 1
    id = info.parentMapID
    info = SHIM.maps[id]
  end
  return info
end

C_Map = {}
function C_Map.GetBestMapForUnit(unit)
  if unit ~= "player" then return nil end
  if SHIM.state.mapNilCalls > 0 then
    SHIM.state.mapNilCalls = SHIM.state.mapNilCalls - 1
    return nil
  end
  return SHIM.state.mapID
end
function C_Map.GetMapInfo(id)
  local m = SHIM.maps[id]
  if not m then return nil end
  return { mapID = m.mapID, name = m.name, mapType = m.mapType, parentMapID = m.parentMapID, flags = 0 }
end
function C_Map.GetAreaInfo(areaID) return SHIM.areas[areaID] end
function C_Map.GetPlayerMapPosition(mapID, unit)
  if unit ~= "player" or SHIM.state.inInstance or SHIM.state.noPosition then return nil end
  local v = { x = 0.5, y = 0.5 }
  function v:GetXY() return self.x, self.y end
  return v
end

function GetZoneText()
  if SHIM.state.zoneTextOverride then return SHIM.state.zoneTextOverride end
  if SHIM.state.inInstance and SHIM.state.instanceName then return SHIM.state.instanceName end
  local info = walkZone(SHIM.state.mapID)
  return info and info.name or ""
end
function GetRealZoneText() return GetZoneText() end
function GetSubZoneText() return SHIM.state.subzone or "" end
function GetMinimapZoneText()
  local s = GetSubZoneText()
  if s ~= "" then return s end
  return GetZoneText()
end

function IsInInstance()
  if SHIM.state.inInstance then return true, SHIM.state.instanceType or "party" end
  return false, "none"
end
function GetInstanceInfo()
  local st = SHIM.state
  if st.inInstance then
    return st.instanceName or "Instance", st.instanceType or "party", 1, "Normal", 5, 0, false,
           st.instanceID, 5, nil
  end
  local cont = (st.continentInstanceID == 0) and "Eastern Kingdoms" or "Kalimdor"
  return cont, "none", 0, "", 0, 0, false, st.continentInstanceID, 0, nil
end

C_MapExplorationInfo = {}
function C_MapExplorationInfo.GetExploredMapTextures(mapID)
  local n = SHIM.state.exploredTextures[mapID] or 0
  local out = {}
  for i = 1, n do out[i] = { textureWidth = 256, textureHeight = 256, offsetX = i, offsetY = 0 } end
  return out
end
function C_MapExplorationInfo.GetExploredAreaIDsAtPosition(mapID, pos)
  if pos == nil then return nil end
  local list = SHIM.state.exploredAreas[mapID]
  if not list then return nil end
  return copy(list)
end

-- ---------------------------------------------------------------- units ----

function UnitAffectingCombat(unit) return unit == "player" and SHIM.state.inCombat or false end
function UnitIsDeadOrGhost(unit) return unit == "player" and SHIM.state.dead or false end
function UnitOnTaxi(unit) return unit == "player" and SHIM.state.taxi or false end
function UnitLevel(unit) return SHIM.state.level end
function UnitName(unit) return "Tester", nil end
function UnitGUID(unit) return "Player-0000-00000001" end
function GetRealmName() return "Forever" end
function InCinematic() return SHIM.state.cinematic end

C_RestrictedActions = {}
function C_RestrictedActions.IsAddOnRestrictionActive(kind)
  if kind == Enum.AddOnRestrictionType.Encounter then return SHIM.state.encounterRestricted end
  return false
end

-- ------------------------------------------------------------ messages ----

ERR_ZONE_EXPLORED = "Discovered: %s"
ERR_ZONE_EXPLORED_XP = "Discovered %s: %d experience gained"

SHIM.gameMessages = {
  [1] = "ERR_OUT_OF_RANGE",
  [50] = "ERR_QUEST_ACCEPTED_S",
  [372] = "ERR_ZONE_EXPLORED",
  [373] = "ERR_ZONE_EXPLORED_XP",
}
SHIM.MSG_ZONE_EXPLORED = 372
SHIM.MSG_ZONE_EXPLORED_XP = 373

function GetGameMessageInfo(errorType)
  local name = SHIM.gameMessages[errorType]
  if not name then return nil end
  return name, 0, 0
end

-- Fires what the client fires when the player discovers `name`.
function SHIM.Discover(name, xp, opts)
  opts = opts or {}
  local msg
  if xp then
    msg = string.format(ERR_ZONE_EXPLORED_XP, name, xp)
  else
    msg = string.format(ERR_ZONE_EXPLORED, name)
  end
  if not opts.noInfo then
    SHIM.FireEvent("UI_INFO_MESSAGE", xp and SHIM.MSG_ZONE_EXPLORED_XP or SHIM.MSG_ZONE_EXPLORED, msg)
  end
  if not opts.noChat then
    SHIM.FireEvent("CHAT_MSG_SYSTEM", msg, "", "", "", "", "", 0, 0, "", 0, 0, nil, 0)
  end
  if not opts.noExploration then
    SHIM.FireEvent("MAP_EXPLORATION_UPDATED")
  end
end

-- ----------------------------------------------------------------- chat ----

SHIM.chatFilters = {}
local function addFilter(event, fn)
  if type(fn) ~= "function" then shimError("message filter must be a function") end
  local list = SHIM.chatFilters[event]
  if not list then list = {}; SHIM.chatFilters[event] = list end
  for i = 1, #list do if list[i] == fn then return end end
  tinsert(list, fn)
end
local function removeFilter(event, fn)
  local list = SHIM.chatFilters[event]
  if not list then return end
  for i = #list, 1, -1 do if list[i] == fn then table.remove(list, i) end end
end
ChatFrameUtil = { AddMessageEventFilter = addFilter, RemoveMessageEventFilter = removeFilter }
ChatFrame_AddMessageEventFilter = addFilter
ChatFrame_RemoveMessageEventFilter = removeFilter

-- Runs the chat filters like ChatFrame_MessageEventHandler; returns true if hidden.
function SHIM.RunChatFilters(event, msg, ...)
  local list = SHIM.chatFilters[event]
  if not list then return false end
  local hidden = false
  for i = 1, #list do
    local fn = list[i]
    local n = select("#", ...)
    local args = { ... }
    local ok, res = xpcall(function() return fn(DEFAULT_CHAT_FRAME, event, msg, unpack(args, 1, n)) end, traceback)
    if not ok then
      recordError(res)
    elseif res then
      hidden = true
      break
    end
  end
  return hidden
end

function SendSystemMessage(text)
  if type(text) ~= "string" then shimError("SendSystemMessage needs a string") end
  local hidden = SHIM.RunChatFilters("CHAT_MSG_SYSTEM", text, "", "", "", "", "", 0, 0, "", 0, 0, nil, 0)
  tinsert(SHIM.log.chat, { text = text, hidden = hidden, t = SHIM.now })
  if SHIM.state.loggingChat then tinsert(SHIM.log.chatlog, text) end
  SHIM.FireEvent("CHAT_MSG_SYSTEM", text, "", "", "", "", "", 0, 0, "", 0, 0, nil, 0)
end

function LoggingChat(newState)
  if newState ~= nil then
    tinsert(SHIM.log.logging, newState and true or false)
    if newState and SHIM.state.loggingChatBlocked then
      -- the client refused; state unchanged
    else
      SHIM.state.loggingChat = newState and true or false
    end
  end
  return SHIM.state.loggingChat
end

C_ChatInfo = {}
function C_ChatInfo.IsLoggingChat() return SHIM.state.loggingChat end
function C_ChatInfo.InChatMessagingLockdown() return SHIM.state.chatLockdown end

-- ---------------------------------------------------------------- audio ----

SHIM.playing = {}
local soundHandle = 100
function PlayMusic(path)
  if type(path) ~= "string" then shimError("PlayMusic needs a path") end
  tinsert(SHIM.log.music, { "play", path, t = SHIM.now })
  SHIM.state.musicPath = path
  return true
end
function StopMusic()
  tinsert(SHIM.log.music, { "stop", t = SHIM.now })
  SHIM.state.musicPath = nil
end
-- warcraft.wiki.gg API_PlaySoundFile: willPlay is nil when a muted channel prevents the sound;
-- the Music channel's toggle is Sound_EnableMusic (Blizzard_SharedXML/Mainline/Sound.lua).
function PlaySoundFile(path, channel)
  if type(path) ~= "string" and type(path) ~= "number" then shimError("PlaySoundFile needs a path") end
  local entry = { path = path, channel = channel, t = SHIM.now }
  tinsert(SHIM.log.sounds, entry)
  local muted = channel == "Music" and GetCVar("Sound_EnableMusic") == "0"
  if muted then entry.muted = true end
  if muted or not SHIM.state.playableFiles[path] then
    entry.willPlay = false
    return nil, nil
  end
  soundHandle = soundHandle + 1
  local len = SHIM.state.soundLengths[path]
  SHIM.playing[soundHandle] = { path = path, endsAt = len and (SHIM.now + len) or math.huge }
  entry.willPlay = true
  entry.handle = soundHandle
  return true, soundHandle
end
function PlaySound(kit, channel) return true, 1 end
function StopSound(handle, fadeMs)
  tinsert(SHIM.log.stoppedSounds, { handle = handle, fade = fadeMs, t = SHIM.now })
  SHIM.playing[handle] = nil
end
C_Sound = {}
function C_Sound.IsPlaying(handle)
  local p = SHIM.playing[handle]
  return p ~= nil and SHIM.now < p.endsAt
end

-- ----------------------------------------------------------------- cvars ----

-- SHIM.cvars holds the stored values (what Config.wtf gets at logout). SHIM.tempCVars holds
-- session-only values set with C_CVar.SetTempCVar: GetCVar reads through them, SetCVar changes
-- only the stored value underneath, and only RemoveTempCVar drops a temp value. This is the
-- model Blizzard's own code assumes (Blizzard_Gamepad/Core.lua pairs SetTempCVar with
-- RemoveTempCVar, never with SetCVar; baseline finding ND-AUDIO-TEMPCVAR).
SHIM.tempCVars = {}
function GetCVar(name)
  local t = SHIM.tempCVars[name]
  if t ~= nil then return t end
  return SHIM.cvars[name]
end
function SetCVar(name, value)
  if type(name) ~= "string" then shimError("SetCVar needs a name") end
  if type(value) == "boolean" then value = value and "1" or "0" end
  SHIM.cvars[name] = tostring(value)
  tinsert(SHIM.log.cvarSets, { name = name, value = tostring(value), t = SHIM.now })
  return true
end
function GetCVarDefault(name) return "1" end
C_CVar = {}
function C_CVar.GetCVar(name) return GetCVar(name) end
function C_CVar.SetCVar(name, value) return SetCVar(name, value) end
function C_CVar.GetCVarDefault(name) return "1" end
function C_CVar.GetCVarBool(name) return GetCVar(name) == "1" end
-- C_CVar.SetTempCVar / RemoveTempCVar (CVarDocumentation.lua on forever: no return values).
function SHIM.EnableTempCVar()
  function C_CVar.SetTempCVar(name, value)
    SHIM.tempCVars[name] = tostring(value)
    tinsert(SHIM.log.cvarSets, { name = name, value = tostring(value), temp = true, t = SHIM.now })
  end
  function C_CVar.RemoveTempCVar(name)
    SHIM.tempCVars[name] = nil
    tinsert(SHIM.log.cvarSets, { name = name, removeTemp = true, t = SHIM.now })
  end
end

-- ------------------------------------------------------------- fade API ----

-- Optional in the real client; the addon must work without them. Call to provide.
function SHIM.EnableFadeHelpers()
  SHIM.fades = {}
  function UIFrameFadeIn(frame, t, from, to)
    tinsert(SHIM.fades, { "in", frame, t, from, to })
    frame:SetAlpha(to or 1)
    frame:Show()
  end
  function UIFrameFadeOut(frame, t, from, to)
    tinsert(SHIM.fades, { "out", frame, t, from, to })
    frame:SetAlpha(to or 0)
  end
end

-- ------------------------------------------------------------- settings ----

-- controls: setting controls only; elements: everything on the panel in order (controls,
-- section headers, buttons) as { kind = "checkbox"|"slider"|"dropdown"|"header"|"button", ... }.
SHIM.settings = { categories = {}, settings = {}, controls = {}, elements = {} }
Settings = {}
Settings.VarType = { Boolean = "boolean", Number = "number", String = "string" }
local Category = {}
local CategoryMT = { __index = Category }
function Category:GetID() return self.ID end
function Category:GetName() return self.name end
local Layout = {}
local LayoutMT = { __index = Layout }
function Layout:AddInitializer(initializer)
  if type(initializer) ~= "table" then shimError("AddInitializer needs an initializer") end
  tinsert(SHIM.settings.elements, initializer)
  return initializer
end
local function addElement(kind, setting, extra)
  local e = { kind = kind, setting = setting, variable = setting and setting.variable }
  if extra then for k, v in pairs(extra) do e[k] = v end end
  tinsert(SHIM.settings.elements, e)
end
local Setting = {}
local SettingMT = { __index = Setting }
function Setting:GetValue() return self.tbl[self.key] end
function Setting:SetValue(v)
  self.tbl[self.key] = v
  if self.cb then self.cb(self, v) end
end
function Setting:GetDefaultValue() return self.default end
function Setting:GetVariable() return self.variable end
function Setting:SetValueChangedCallback(cb) self.cb = cb end
local catSeq = 0
function Settings.RegisterVerticalLayoutCategory(name)
  catSeq = catSeq + 1
  local c = setmetatable({ name = name, ID = catSeq }, CategoryMT)
  tinsert(SHIM.settings.categories, c)
  local layout = setmetatable({ category = c }, LayoutMT)
  c.layout = layout
  return c, layout
end
function CreateSettingsListSectionHeaderInitializer(name, tooltip)
  if type(name) ~= "string" then shimError("section header needs a name") end
  return { kind = "header", name = name, tooltip = tooltip }
end
function CreateSettingsButtonInitializer(name, buttonText, buttonClick, tooltip, addSearchTags)
  if type(buttonClick) ~= "function" then shimError("button initializer needs a click function") end
  return { kind = "button", name = name, buttonText = buttonText, click = buttonClick, tooltip = tooltip,
           searchTags = addSearchTags }
end
function Settings.RegisterAddOnSetting(category, variable, variableKey, variableTbl, varType, name, default)
  if type(variableTbl) ~= "table" then shimError("RegisterAddOnSetting needs a table") end
  if varType ~= type(default) then shimError("RegisterAddOnSetting: varType mismatch for " .. tostring(variable)) end
  local s = setmetatable({ category = category, variable = variable, key = variableKey, tbl = variableTbl,
                           varType = varType, name = name, default = default }, SettingMT)
  SHIM.settings.settings[variable] = s
  return s
end
function Settings.SetOnValueChangedCallback(variable, cb)
  local s = SHIM.settings.settings[variable]
  if s then s.cb = function(setting, value) cb(nil, setting, value) end end
end
function Settings.CreateCheckbox(category, setting, tooltip)
  tinsert(SHIM.settings.controls, { "checkbox", setting, tooltip })
  addElement("checkbox", setting, { tooltip = tooltip })
  return {}
end
function Settings.CreateSliderOptions(minV, maxV, step)
  local o = { min = minV, max = maxV, step = step }
  function o:SetLabelFormatter(where, fn) self.formatter = fn end
  return o
end
function Settings.CreateSlider(category, setting, options, tooltip)
  tinsert(SHIM.settings.controls, { "slider", setting, options, tooltip })
  addElement("slider", setting, { options = options, tooltip = tooltip })
  return {}
end
function Settings.CreateControlTextContainer()
  local c = { data = {} }
  function c:Add(value, label, tip) tinsert(self.data, { value = value, label = label, text = label, tooltip = tip }) end
  function c:GetData() return self.data end
  return c
end
function Settings.CreateDropdown(category, setting, getOptions, tooltip)
  tinsert(SHIM.settings.controls, { "dropdown", setting, getOptions, tooltip })
  addElement("dropdown", setting, { getOptions = getOptions, tooltip = tooltip })
  return {}
end
function Settings.RegisterAddOnCategory(category) SHIM.settings.registered = category end
function Settings.OpenToCategory(id) tinsert(SHIM.log.openedCategory, id); return true end
MinimalSliderWithSteppersMixin = { Label = { Left = 1, Right = 2, Top = 3, Min = 4, Max = 5 } }

function SHIM.RemoveSettingsAPI() Settings = nil end
-- Older Settings API: no section headers / buttons, and no layout from the category call.
function SHIM.RemoveSettingsExtras()
  CreateSettingsListSectionHeaderInitializer = nil
  CreateSettingsButtonInitializer = nil
  local register = Settings.RegisterVerticalLayoutCategory
  Settings.RegisterVerticalLayoutCategory = function(name) return (register(name)) end
end

-- ---------------------------------------------------------------- slash ----

SlashCmdList = {}
hash_SlashCmdList = {}

-- -------------------------------------------------------------- utility ----

function SHIM.StripColors(s)
  return (s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""))
end

function SHIM.FrameInfo(name)
  local f = _G[name]
  if not f then return nil end
  return f
end

return SHIM
