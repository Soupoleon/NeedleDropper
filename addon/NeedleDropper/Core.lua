local ADDON, ns = ...
-- NeedleDropper Core: namespace, SavedVariables, map indexes, event dispatch, printing,
-- CVar helpers and slash commands. Loaded right after Protocol.lua and Map.lua.

ns.ADDON = ADDON or "NeedleDropper"
ns.SCHEMA = 1
ns.PREFIX = "|cff9fd3ffNeedleDropper|r:"
ns.SILENCE = "Interface\\AddOns\\NeedleDropper\\media\\silence.mp3"
ns.VERSION = "1.0.0"
do
  local getMeta = (C_AddOns and C_AddOns.GetAddOnMetadata) or GetAddOnMetadata
  if getMeta then
    local ok, v = pcall(getMeta, ns.ADDON, "Version")
    if ok and type(v) == "string" and v ~= "" then ns.VERSION = v end
  end
end

-- ------------------------------------------------------------------ utils ----

local floor = math.floor

-- True when `v` is a Midnight "secret value" (or when we cannot tell): never touch those.
function ns.IsSecret(v)
  if issecretvalue == nil then return false end
  local ok, s = pcall(issecretvalue, v)
  if not ok then return true end
  return s and true or false
end

-- `v` if it is a plain (non-secret) string, else nil.
function ns.SafeString(v)
  if ns.IsSecret(v) then return nil end
  if type(v) == "string" then return v end
  return nil
end

-- `v` if it is a plain (non-secret) number, else nil.
function ns.SafeNumber(v)
  if ns.IsSecret(v) then return nil end
  if type(v) == "number" then return v end
  return nil
end

-- Calls an API that returns a boolean; false if missing, erroring or secret.
function ns.SafeBool(fn, ...)
  if type(fn) ~= "function" then return false end
  local ok, v = pcall(fn, ...)
  if not ok or ns.IsSecret(v) then return false end
  return v and true or false
end

function ns.Trim(s)
  if type(s) ~= "string" then return "" end
  return (string.gsub(s, "^%s*(.-)%s*$", "%1"))
end

function ns.Lower(s)
  if type(s) ~= "string" then return "" end
  return string.lower(s)
end

function ns.Wipe(t)
  for k in pairs(t) do t[k] = nil end
  return t
end

function ns.Copy(t)
  if type(t) ~= "table" then return t end
  local out = {}
  for k, v in pairs(t) do out[k] = ns.Copy(v) end
  return out
end

local function isInt(v)
  return type(v) == "number" and v == v and v ~= math.huge and v ~= -math.huge and floor(v) == v
end
ns.IsInt = isInt

-- "1m 05s" style duration.
function ns.FormatSeconds(sec)
  sec = floor((sec or 0) + 0.5)
  if sec < 0 then sec = 0 end
  if sec >= 3600 then
    return string.format("%dh %02dm", floor(sec / 3600), floor(sec % 3600 / 60))
  elseif sec >= 60 then
    return string.format("%dm %02ds", floor(sec / 60), sec % 60)
  end
  return string.format("%ds", sec)
end

-- string.format that tolerates nil/boolean/table arguments for %s.
local function safeFormat(fmt, ...)
  local n = select("#", ...)
  if n == 0 then return tostring(fmt) end
  local args = { ... }
  for i = 1, n do
    local t = type(args[i])
    if t ~= "number" and t ~= "string" then args[i] = tostring(args[i]) end
  end
  local ok, s = pcall(string.format, fmt, unpack(args, 1, n))
  if ok then return s end
  return tostring(fmt)
end
ns.Format = safeFormat

function ns.Print(msg, ...)
  local line = ns.PREFIX .. " " .. safeFormat(msg, ...)
  if DEFAULT_CHAT_FRAME and DEFAULT_CHAT_FRAME.AddMessage then
    DEFAULT_CHAT_FRAME:AddMessage(line)
  else
    print(line)
  end
end

function ns.Debug(msg, ...)
  if ns.settings and ns.settings.debug then
    ns.Print("|cff8a8a8a[debug]|r " .. safeFormat(msg, ...))
  end
end

-- ------------------------------------------------------------- settings ----

ns.DEFAULTS = {
  enabled = true,
  dwellSec = 10,
  minDwellSec = 3,
  landmarkDwellSec = 4,
  cooldownMin = 10,
  historyMode = "respect",
  scope = "character",
  suppressOnTaxi = true,
  instanceDrops = true,
  combatFadeSec = 5,
  maxDropSec = 300,
  duckMethod = "silence",
  kickZoneMusic = true,
  localPlayback = true,
  toast = true,
  toastScale = 1.0,
  toastY = 0.22,
  toastSeconds = 7,
  beacon = true,
  beaconCellPx = 4,
  chatlog = false,
  chatlogPadBytes = 50000,
  debug = false,
}

-- Ordered setting descriptions: validation, /needle set, and the options panel.
ns.SETTINGS = {
  { key = "enabled", type = "boolean", label = "Enable needle drops",
    tip = "Play a curated song the first time you arrive in a zone or landmark." },
  { key = "dwellSec", type = "number", min = 0, max = 300, step = 1, label = "Zone arrival delay (s)",
    tip = "Seconds you must spend in a new zone before its song starts." },
  { key = "minDwellSec", type = "number", min = 0, max = 300, step = 1, label = "Delay after discovery (s)",
    tip = "Shorter delay used when the game announces \"Discovered: ...\" (also used for instances)." },
  { key = "landmarkDwellSec", type = "number", min = 0, max = 300, step = 1, label = "Landmark delay (s)",
    tip = "Seconds inside a landmark subzone before its song starts (immediate on discovery)." },
  { key = "cooldownMin", type = "number", min = 0, max = 240, step = 1, label = "Cooldown between drops (min)",
    tip = "Keep drops rare: minimum minutes between two needle drops." },
  { key = "historyMode", type = "string", label = "Exploration history",
    options = { { "respect", "Respect: skip places this character already explored" },
                { "fresh", "Fresh: ignore exploration history" } },
    tip = "Respect uses the world map's exploration data so veterans don't get every song at once." },
  { key = "scope", type = "string", label = "Play each drop once per",
    options = { { "character", "Character" }, { "account", "Account" } },
    tip = "Whether each song plays once per character or once per account." },
  { key = "suppressOnTaxi", type = "boolean", label = "Wait while on a flight path",
    tip = "Hold pending drops while you are on a taxi." },
  { key = "instanceDrops", type = "boolean", label = "Instance drops",
    tip = "Allow drops that belong to dungeons and raids." },
  { key = "combatFadeSec", type = "number", min = 0, max = 120, step = 1, label = "Stop after combat lasting (s)",
    tip = "Fade out a playing drop when combat lasts longer than this. 0 = never." },
  { key = "maxDropSec", type = "number", min = 10, max = 1800, step = 10, label = "Length when unknown (s)",
    tip = "How long a drop counts as playing when its duration is unknown." },
  { key = "duckMethod", type = "string", label = "Zone music during a drop",
    options = { { "silence", "Silence (play a silent track)" }, { "volume", "Volume (music volume to 0)" },
                { "none", "Leave zone music alone" } },
    tip = "How the in-game zone music is faded out while a drop plays." },
  { key = "kickZoneMusic", type = "boolean", label = "Restart zone music afterwards",
    tip = "Briefly toggle music off/on after a drop so zone music resumes right away." },
  { key = "localPlayback", type = "boolean", label = "Play in-game files",
    tip = "Play drops that have an in-game audio file inside WoW instead of via the companion." },
  { key = "toast", type = "boolean", label = "Title card",
    tip = "Show the place / artist / title card when a drop starts." },
  -- snap: values land on the slider's step grid; percent: shown as a percentage, and
  -- /needle set also accepts "120%".
  { key = "toastScale", type = "number", min = 0.6, max = 1.6, step = 0.05, snap = true, percent = true,
    label = "Title card size", tip = "Size of the title card text. 100% is a 30 px place name." },
  { key = "toastY", type = "number", min = 0.08, max = 0.50, step = 0.01, snap = true, percent = true,
    label = "Title card position", tip = "How far down the screen the title card sits, as a share of the screen height from the top." },
  { key = "toastSeconds", type = "number", min = 3, max = 15, step = 1, integer = true,
    label = "Title card time (s)", tip = "Seconds the title card stays fully visible, between its fade-in and fade-out." },
  { key = "beacon", type = "boolean", label = "Pixel beacon",
    tip = "Tiny colour strip in the top-left corner that the companion app reads." },
  { key = "beaconCellPx", type = "number", min = 1, max = 16, step = 1, integer = true, label = "Beacon cell size (px)",
    tip = "Physical pixels per beacon cell. Match the companion's setting." },
  { key = "chatlog", type = "boolean", label = "Chat-log channel",
    tip = "Also report events through WoWChatLog.txt (turns on chat logging; lines are hidden)." },
  { key = "chatlogPadBytes", type = "number", min = 0, max = 200000, step = 1000, integer = true,
    label = "Chat-log padding (bytes)",
    tip = "Filler written after important events so the client flushes its log to disk. 0 = off." },
  { key = "debug", type = "boolean", label = "Debug messages", tip = "Print diagnostic messages." },
}
ns.SETTINGS_BY_KEY = {}
for i = 1, #ns.SETTINGS do ns.SETTINGS_BY_KEY[ns.SETTINGS[i].key] = ns.SETTINGS[i] end

-- Coerces `value` for setting `key`; returns the valid value or nil, errorMessage.
function ns.CoerceSetting(key, value)
  local meta = ns.SETTINGS_BY_KEY[key]
  if not meta then return nil, "unknown setting '" .. tostring(key) .. "'" end
  if meta.type == "boolean" then
    if type(value) == "boolean" then return value end
    local s = ns.Lower(ns.Trim(tostring(value)))
    if s == "on" or s == "true" or s == "1" or s == "yes" or s == "enable" or s == "enabled" then return true end
    if s == "off" or s == "false" or s == "0" or s == "no" or s == "disable" or s == "disabled" then return false end
    return nil, key .. " must be on or off"
  elseif meta.type == "number" then
    local n = tonumber(value)
    if n == nil and meta.percent and type(value) == "string" then
      local pct = tonumber(string.match(value, "^%s*([%d%.%-%+]+)%s*%%%s*$") or "")
      if pct then n = pct / 100 end
    end
    if not n or n ~= n then return nil, key .. " must be a number" end
    if meta.integer then n = floor(n + 0.5) end
    if meta.snap and meta.step and meta.step > 0 and n > -math.huge and n < math.huge then
      n = meta.min + floor((n - meta.min) / meta.step + 0.5) * meta.step
      n = floor(n * 10000 + 0.5) / 10000   -- no float noise in SavedVariables
    end
    if n < meta.min then n = meta.min end
    if n > meta.max then n = meta.max end
    return n
  else
    local s = ns.Lower(ns.Trim(tostring(value)))
    for i = 1, #meta.options do
      if meta.options[i][1] == s then return s end
    end
    local names = {}
    for i = 1, #meta.options do names[#names + 1] = meta.options[i][1] end
    return nil, key .. " must be one of: " .. table.concat(names, ", ")
  end
end

local function validateSettings(s)
  for key, default in pairs(ns.DEFAULTS) do
    local v = s[key]
    if type(v) ~= type(default) then
      s[key] = default
    else
      local ok = ns.CoerceSetting(key, v)
      if ok == nil then s[key] = default else s[key] = ok end
    end
  end
end

-- Changes a setting (slash command / options), notifying modules. Returns value or nil, err.
function ns.SetSetting(key, value)
  if not ns.settings then return nil, "settings are not loaded yet" end
  local v, err = ns.CoerceSetting(key, value)
  if v == nil then return nil, err end
  ns.settings[key] = v
  ns.Fire("ND_SETTING", key, v)
  return v
end

-- Called after the Settings panel wrote straight into the settings table.
function ns.OnSettingEdited(key, value)
  if not ns.settings then return end
  local v = ns.CoerceSetting(key, value)
  if v == nil then v = ns.DEFAULTS[key] end
  ns.settings[key] = v
  ns.Fire("ND_SETTING", key, v)
end

function ns.FormatSettingValue(key, v)
  if type(v) == "boolean" then return v and "on" or "off" end
  return tostring(v)
end

-- ------------------------------------------------------ SavedVariables ----

local ACCOUNT_DEFAULTS = { schema = ns.SCHEMA, settings = ns.DEFAULTS, accountDone = {}, lastSeq = 0 }
local CHAR_DEFAULTS = { schema = ns.SCHEMA, done = {}, scanned = {}, journal = {}, lastFireAt = 0 }
local JOURNAL_MAX = 200

-- Recursively fills missing / wrongly typed fields of dst from defaults.
local function mergeDefaults(dst, defaults)
  for k, v in pairs(defaults) do
    if type(v) == "table" then
      if type(dst[k]) ~= "table" then dst[k] = {} end
      mergeDefaults(dst[k], v)
    elseif dst[k] == nil or type(dst[k]) ~= type(v) then
      dst[k] = v
    end
  end
end
ns.MergeDefaults = mergeDefaults

local function normalizeDone(t)
  for id, rec in pairs(t) do
    if type(id) ~= "string" then
      t[id] = nil
    elseif rec == true then
      t[id] = { t = 0, how = "played" }
    elseif type(rec) ~= "table" then
      t[id] = nil
    else
      if type(rec.t) ~= "number" then rec.t = 0 end
      if type(rec.how) ~= "string" then rec.how = "played" end
    end
  end
end

local function normalizeJournal(j)
  local clean = {}
  for i = 1, #j do
    local e = j[i]
    if type(e) == "table" and type(e.id) == "string" then
      if type(e.t) ~= "number" then e.t = 0 end
      clean[#clean + 1] = e
    end
  end
  while #clean > JOURNAL_MAX do table.remove(clean, 1) end
  ns.Wipe(j)
  for i = 1, #clean do j[i] = clean[i] end
end

local function initDB()
  if type(NeedleDropperDB) ~= "table" then NeedleDropperDB = {} end
  local db = NeedleDropperDB
  if type(db.schema) ~= "number" then db.schema = ns.SCHEMA end
  if db.schema > ns.SCHEMA then ns.newerSchema = true end
  mergeDefaults(db, ACCOUNT_DEFAULTS)
  validateSettings(db.settings)
  normalizeDone(db.accountDone)
  if db.savedCVars ~= nil and type(db.savedCVars) ~= "table" then db.savedCVars = nil end
  if not isInt(db.lastSeq) or db.lastSeq < 0 or db.lastSeq > 255 then db.lastSeq = 0 end

  if type(NeedleDropperCharDB) ~= "table" then
    NeedleDropperCharDB = {}
    ns.charDBCreated = true
  end
  local c = NeedleDropperCharDB
  if type(c.schema) ~= "number" then c.schema = ns.SCHEMA end
  mergeDefaults(c, CHAR_DEFAULTS)
  normalizeDone(c.done)
  for id, v in pairs(c.scanned) do
    if type(id) ~= "string" or v ~= true then c.scanned[id] = nil end
  end
  normalizeJournal(c.journal)
  if type(c.lastFireAt) ~= "number" or c.lastFireAt ~= c.lastFireAt then c.lastFireAt = 0 end

  ns.db = db
  ns.cdb = c
  ns.settings = db.settings
end

function ns.AddJournal(drop)
  if not ns.cdb then return end
  local j = ns.cdb.journal
  j[#j + 1] = { id = drop.id, t = time(), label = drop.label, title = drop.title, artist = drop.artist }
  while #j > JOURNAL_MAX do table.remove(j, 1) end
end

-- Resets history: "char", "account", "all" or "settings". Returns true if understood.
function ns.ResetData(what)
  if not ns.db then return false end
  if what == "char" or what == "all" then
    ns.Wipe(ns.cdb.done)
    ns.Wipe(ns.cdb.scanned)
    ns.Wipe(ns.cdb.journal)
    ns.cdb.lastFireAt = 0
  end
  if what == "account" or what == "all" then
    ns.Wipe(ns.db.accountDone)
  end
  if what == "settings" then
    ns.Wipe(ns.settings)
    mergeDefaults(ns.settings, ns.DEFAULTS)
  end
  if what ~= "char" and what ~= "account" and what ~= "all" and what ~= "settings" then return false end
  ns.Fire("ND_RESET", what)
  return true
end

-- ------------------------------------------------------------------- CVars ----

function ns.GetCVar(name)
  if C_CVar and C_CVar.GetCVar then
    local ok, v = pcall(C_CVar.GetCVar, name)
    if ok and v ~= nil and not ns.IsSecret(v) then return tostring(v) end
  end
  if GetCVar then
    local ok, v = pcall(GetCVar, name)
    if ok and v ~= nil and not ns.IsSecret(v) then return tostring(v) end
  end
  return nil
end

function ns.SetCVar(name, value)
  value = tostring(value)
  if C_CVar and C_CVar.SetCVar then
    local ok = pcall(C_CVar.SetCVar, name, value)
    if ok then return true end
  end
  if SetCVar then
    local ok = pcall(SetCVar, name, value)
    if ok then return true end
  end
  return false
end

-- Remembers the current value of a CVar in NeedleDropperDB.savedCVars (crash-safe restore).
function ns.SaveCVar(name, value)
  if not ns.db then return end
  if ns.db.savedCVars == nil then ns.db.savedCVars = {} end
  if ns.db.savedCVars[name] == nil then
    ns.db.savedCVars[name] = value or ns.GetCVar(name)
  end
end

function ns.ForgetCVar(name)
  if not ns.db or not ns.db.savedCVars then return end
  ns.db.savedCVars[name] = nil
  if next(ns.db.savedCVars) == nil then ns.db.savedCVars = nil end
end

-- Restores a saved CVar (and forgets it). Returns true if something was restored.
function ns.RestoreCVar(name)
  local saved = ns.db and ns.db.savedCVars and ns.db.savedCVars[name]
  if saved == nil then return false end
  ns.SetCVar(name, saved)
  ns.ForgetCVar(name)
  return true
end

-- PLAYER_LOGIN: undo CVar changes left over from a crash / forced quit during a drop.
function ns.RepairCVars()
  if not ns.db then return false end
  local saved = ns.db.savedCVars
  if saved == nil then return false end
  if type(saved) ~= "table" then ns.db.savedCVars = nil return false end
  local restored = {}
  for name, value in pairs(saved) do
    if type(name) == "string" and (type(value) == "string" or type(value) == "number") then
      ns.SetCVar(name, tostring(value))
      restored[#restored + 1] = name .. "=" .. tostring(value)
    end
  end
  ns.db.savedCVars = nil
  if #restored > 0 then
    table.sort(restored)
    ns.Print("restored sound settings left over from an interrupted session (%s).", table.concat(restored, ", "))
  end
  return #restored > 0
end

-- ---------------------------------------------------------------- the map ----

local KINDS = { zone = true, subzone = true, instance = true }
local ID_KIND = { zone = "zone", area = "subzone", sub = "subzone", inst = "instance" }

local function posInt(v)
  if isInt(v) and v > 0 then return v end
  return nil
end

local function str(v)
  if type(v) == "string" then return v end
  return nil
end

-- Returns a clean drop table, or nil and a reason.
local function validateDrop(d)
  if type(d) ~= "table" then return nil, "entry is not a table" end
  local n = d.n
  if not isInt(n) or n < 1 or n > 65535 then return nil, "bad n" end
  local id = str(d.id)
  if not id or id == "" or #id > 200 then return nil, "bad id" end
  local drop = {
    n = n, id = id,
    mapID = posInt(d.mapID), areaID = posInt(d.areaID), instanceID = posInt(d.instanceID),
    subzone = str(d.subzone),
    title = str(d.title) or "", artist = str(d.artist) or "",
    durationMs = (type(d.durationMs) == "number" and d.durationMs > 0 and d.durationMs < 86400000) and d.durationMs or 0,
    startMs = (type(d.startMs) == "number" and d.startMs > 0) and d.startMs or 0,
    file = str(d.file),
  }
  if drop.subzone == "" then drop.subzone = nil end
  if drop.file == "" then drop.file = nil end
  local kind = str(d.kind)
  if not KINDS[kind] then
    kind = ID_KIND[string.match(id, "^(%a+):") or ""]
    if not kind then
      if drop.instanceID then kind = "instance"
      elseif drop.areaID or drop.subzone then kind = "subzone"
      else kind = "zone" end
    end
  end
  drop.kind = kind
  if kind == "zone" and not drop.mapID then return nil, "zone drop without mapID" end
  if kind == "subzone" and not (drop.areaID or (drop.mapID and drop.subzone)) then
    return nil, "landmark drop without areaID or mapID+subzone"
  end
  if kind == "instance" and not drop.instanceID then return nil, "instance drop without instanceID" end
  if drop.subzone then drop.subzoneLower = string.lower(drop.subzone) end
  local label = str(d.label)
  if not label or label == "" then label = drop.subzone or id end
  drop.label = label
  return drop
end

-- Builds the validated map plus lookup indexes from a raw NeedleDropper_Map value.
function ns.BuildMap(raw)
  local M = {
    loaded = false, version = 0, hash = 0, generated = "", packs = {}, drops = {},
    byId = {}, byN = {}, zoneDrops = {}, subDrops = {}, looseSubDrops = {}, areaDrops = {},
    instanceDrops = {}, invalid = 0, problems = {},
  }
  local function problem(msg)
    if #M.problems < 20 then M.problems[#M.problems + 1] = msg end
  end
  if raw == nil then
    problem("no map loaded (Map.lua missing or empty)")
    return M
  end
  if type(raw) ~= "table" then
    problem("NeedleDropper_Map is not a table")
    return M
  end
  M.loaded = true
  if type(raw.version) == "number" then M.version = raw.version end
  if isInt(raw.hash) and raw.hash >= 0 and raw.hash <= 65535 then
    M.hash = raw.hash
  else
    problem("map hash missing or invalid")
  end
  if type(raw.generated) == "string" then M.generated = raw.generated end
  if type(raw.packs) == "table" then
    for i = 1, #raw.packs do
      if type(raw.packs[i]) == "string" then M.packs[#M.packs + 1] = raw.packs[i] end
    end
  end
  if type(raw.drops) ~= "table" then
    problem("map has no drops table")
    return M
  end
  -- collect entries in index order (tolerates holes and stray keys)
  local keys = {}
  for k in pairs(raw.drops) do
    if isInt(k) then keys[#keys + 1] = k else M.invalid = M.invalid + 1 end
  end
  table.sort(keys)
  for i = 1, #keys do
    local drop, why = validateDrop(raw.drops[keys[i]])
    if not drop then
      M.invalid = M.invalid + 1
      problem("drop #" .. keys[i] .. ": " .. why)
    elseif M.byId[drop.id] or M.byN[drop.n] then
      M.invalid = M.invalid + 1
      problem("drop #" .. keys[i] .. ": duplicate id or n")
    else
      M.drops[#M.drops + 1] = drop
      M.byId[drop.id] = drop
      M.byN[drop.n] = drop
      if drop.kind == "zone" then
        if not M.zoneDrops[drop.mapID] then M.zoneDrops[drop.mapID] = drop end
      elseif drop.kind == "subzone" then
        if drop.mapID then
          local list = M.subDrops[drop.mapID]
          if not list then list = {}; M.subDrops[drop.mapID] = list end
          list[#list + 1] = drop
        else
          M.looseSubDrops[#M.looseSubDrops + 1] = drop
        end
        if drop.areaID and not M.areaDrops[drop.areaID] then M.areaDrops[drop.areaID] = drop end
      elseif drop.kind == "instance" then
        if not M.instanceDrops[drop.instanceID] then M.instanceDrops[drop.instanceID] = drop end
      end
    end
  end
  return M
end

ns.Map = ns.BuildMap(NeedleDropper_Map)

ns.TEST_DROP = {
  n = 0, id = "test", kind = "test", label = "NeedleDropper Test",
  title = "NeedleDropper test", artist = "NeedleDropper", durationMs = 30000, startMs = 0,
}

local areaNames = {}
-- Localized area name (C_Map.GetAreaInfo), cached; nil if unknown.
function ns.AreaName(areaID)
  if not areaID then return nil end
  local cached = areaNames[areaID]
  if cached then return cached end
  if C_Map and C_Map.GetAreaInfo then
    local ok, name = pcall(C_Map.GetAreaInfo, areaID)
    name = ok and ns.SafeString(name) or nil
    if name and name ~= "" then
      areaNames[areaID] = name
      return name
    end
  end
  return nil
end

local function subzoneMatches(d, subLower)
  if d.subzoneLower and d.subzoneLower == subLower then return true end
  if d.areaID then
    local name = ns.AreaName(d.areaID)
    if name and string.lower(name) == subLower then return true end
  end
  return false
end

-- Landmark drop for subzone text `subLower` (lowercase) inside zone `mapID`.
function ns.FindSubzoneDrop(mapID, subLower)
  if type(subLower) ~= "string" or subLower == "" then return nil end
  local M = ns.Map
  local list = mapID and M.subDrops[mapID]
  if list then
    for i = 1, #list do
      if subzoneMatches(list[i], subLower) then return list[i] end
    end
  end
  for i = 1, #M.looseSubDrops do
    if subzoneMatches(M.looseSubDrops[i], subLower) then return M.looseSubDrops[i] end
  end
  return nil
end

-- --------------------------------------------------------- event dispatch ----

local eventFrame = CreateFrame("Frame", "NeedleDropperEventFrame")
local handlers = {}
ns.unsupportedEvents = {}

local function onError(err)
  local h = geterrorhandler and geterrorhandler()
  if h then
    h(err)
  else
    print(ns.PREFIX .. " error: " .. tostring(err))
  end
  return err
end

local function dispatch(event, ...)
  local list = handlers[event]
  if not list then return end
  local n = select("#", ...)
  local args = { ... }
  for i = 1, #list do
    local fn = list[i]
    xpcall(function() fn(event, unpack(args, 1, n)) end, onError)
  end
end

-- Registers fn(event, ...) for a game event or an internal "ND_*" message.
function ns.On(event, fn)
  local list = handlers[event]
  if not list then
    list = {}
    handlers[event] = list
    if string.sub(event, 1, 3) ~= "ND_" then
      local ok = pcall(eventFrame.RegisterEvent, eventFrame, event)
      if not ok then ns.unsupportedEvents[event] = true end
    end
  end
  list[#list + 1] = fn
end

-- Dispatches an internal message to ns.On handlers.
function ns.Fire(event, ...)
  dispatch(event, ...)
end

eventFrame:SetScript("OnEvent", function(self, event, ...)
  dispatch(event, ...)
end)

ns.On("ADDON_LOADED", function(_, name)
  if name ~= ns.ADDON or ns.initialized then return end
  initDB()
  ns.initialized = true
  ns.Fire("ND_INIT")
end)

ns.On("PLAYER_LOGIN", function()
  if not ns.initialized then
    -- ADDON_LOADED was missed (should not happen); initialize anyway.
    initDB()
    ns.initialized = true
    ns.Fire("ND_INIT")
  end
  ns.RepairCVars()
  local M = ns.Map
  if not M.loaded then
    ns.Print("no song map loaded. Run the companion's |cffffffffbuild-map|r, then /reload.")
  elseif M.invalid > 0 then
    ns.Print("ignored %d malformed entr%s in Map.lua (regenerate it with the companion).",
      M.invalid, M.invalid == 1 and "y" or "ies")
    for i = 1, #M.problems do ns.Debug("map: %s", M.problems[i]) end
  end
  if ns.newerSchema then
    ns.Print("saved data comes from a newer NeedleDropper version; some history may be ignored.")
  end
end)

-- ---------------------------------------------------------- slash commands ----

local function onOff(arg, current)
  arg = ns.Lower(ns.Trim(arg))
  if arg == "on" or arg == "1" or arg == "true" or arg == "enable" then return true end
  if arg == "off" or arg == "0" or arg == "false" or arg == "disable" then return false end
  if arg == "" or arg == "toggle" then return not current end
  return nil
end

local COMMANDS
local ORDER = { "help", "status", "where", "journal", "replay", "play", "test", "toast", "stop", "skip", "reset",
                "beacon", "chatlog", "selftest", "debug", "set", "settings", "options" }

local USAGE = {
  help = "this list",
  status = "settings, transports, map, pending/playing drop, cooldown",
  where = "current zone key, uiMapID, subzone, instance and matching drop ids",
  journal = "songs you have heard",
  replay = "[id] - replay the last (or given) drop",
  play = "<id|place name> - play a drop now (test, does not mark it heard)",
  test = "fire the test drop",
  toast = "[on|off] - preview the title card where you are (or turn title cards on/off)",
  stop ="stop the playing drop (or let the pending one go for now)",
  skip = "stop / skip the current drop and keep it marked as heard",
  reset = "char|account|all|settings - forget history (or reset settings)",
  beacon = "on|off - pixel beacon for the companion",
  chatlog = "on|off - chat-log channel for the companion",
  selftest = "[beacon|chatlog|discovery|music] - check your client setup",
  debug = "on|off - diagnostic messages",
  set = "<setting> <value> - change a setting",
  settings = "list all settings",
  options = "open the options panel",
}

local function needModule(name)
  local m = ns[name]
  if not m then ns.Print("%s is not available.", name) end
  return m
end

local function toggleSetting(key, arg, onMsg, offMsg)
  local v = onOff(arg, ns.settings[key])
  if v == nil then
    ns.Print("usage: /needle %s on|off", key)
    return
  end
  ns.SetSetting(key, v)
  ns.Print(v and onMsg or offMsg)
end

COMMANDS = {
  help = function()
    ns.Print("v%s commands (/needle or /nd):", ns.VERSION)
    for i = 1, #ORDER do
      ns.Print("  |cffffffff/needle %s|r - %s", ORDER[i], USAGE[ORDER[i]])
    end
  end,
  status = function()
    local D = needModule("Director")
    if D then
      local lines = D.StatusLines()
      for i = 1, #lines do ns.Print(lines[i]) end
    end
    if ns.Toast and ns.Toast.StatusLine then ns.Print("%s", ns.Toast.StatusLine()) end
  end,
  where = function()
    local L = needModule("Location")
    if L then
      local lines = L.Describe()
      for i = 1, #lines do ns.Print(lines[i]) end
    end
  end,
  journal = function(arg)
    local j = ns.cdb and ns.cdb.journal or {}
    if #j == 0 then
      ns.Print("your journal is empty - no needle drops heard on this character yet.")
      return
    end
    local count = tonumber(arg) or 15
    ns.Print("journal (%d drop%s, newest last):", #j, #j == 1 and "" or "s")
    for i = math.max(1, #j - count + 1), #j do
      local e = j[i]
      local when = (e.t and e.t > 0 and date) and date("%Y-%m-%d %H:%M", e.t) or "?"
      ns.Print("  %s  %s - %s \194\183 %s  |cff8a8a8a[%s]|r", when, tostring(e.label or e.id),
        tostring(e.artist or "?"), tostring(e.title or "?"), e.id)
    end
    ns.Print("replay one with /needle replay <id>.")
  end,
  replay = function(arg)
    local D = needModule("Director")
    if D then D.Replay(ns.Trim(arg)) end
  end,
  play = function(arg)
    local D = needModule("Director")
    if not D then return end
    arg = ns.Trim(arg)
    if arg == "" then
      ns.Print("usage: /needle play <drop id or place name>, e.g. /needle play zone:1429 or /needle play Elwynn Forest")
      return
    end
    D.PlayQuery(arg)
  end,
  test = function()
    local D = needModule("Director")
    if D then D.PlayTest() end
  end,
  toast = function(arg)
    arg = ns.Lower(ns.Trim(arg))
    if arg ~= "" and arg ~= "preview" then
      local v = onOff(arg, ns.settings.toast)
      if v == nil then
        ns.Print("usage: /needle toast [on|off] - on its own it previews the title card where you are")
        return
      end
      ns.SetSetting("toast", v)
      ns.Print(v and "title card on: it appears whenever a needle drop starts." or
        "title card off: needle drops start without the in-game card.")
      return
    end
    local T = needModule("Toast")
    if not T then return end
    local info = T.Preview()
    if not (info and info.shown) then
      ns.Print("the title card could not be shown on this client.")
      return
    end
    local place = string.gsub(info.place or "", "|", "||")
    if info.drop then
      ns.Print("title card preview: %s |cff8a8a8a[%s]|r", place, info.drop.id)
    else
      ns.Print("title card preview: %s - no needle drop is mapped here.", place)
    end
    if ns.settings.toast then
      ns.Print("adjust it with /needle set toastScale, toastY or toastSeconds, or in the options panel.")
    else
      ns.Print("title cards are off for needle drops; /needle toast on turns them back on.")
    end
  end,
  stop = function()
    local D = needModule("Director")
    if D then D.Stop() end
  end,
  skip = function()
    local D = needModule("Director")
    if D then D.Skip() end
  end,
  reset = function(arg)
    arg = ns.Lower(ns.Trim(arg))
    if arg == "character" then arg = "char" end
    if arg ~= "char" and arg ~= "account" and arg ~= "all" and arg ~= "settings" then
      ns.Print("usage: /needle reset char|account|all|settings")
      return
    end
    ns.ResetData(arg)
    if arg == "char" then
      ns.Print("forgot this character's needle drops, journal and cooldown.")
    elseif arg == "account" then
      ns.Print("forgot account-wide needle drops.")
    elseif arg == "all" then
      ns.Print("forgot all needle drop history (character and account).")
    else
      ns.Print("settings reset to defaults.")
    end
    if arg ~= "settings" and ns.settings.historyMode == "respect" then
      ns.Print("history mode is 'respect': places this character already explored stay skipped. " ..
        "Use /needle set historyMode fresh to hear them again.")
    end
  end,
  beacon = function(arg)
    toggleSetting("beacon", arg, "pixel beacon on (top-left corner).", "pixel beacon off.")
  end,
  chatlog = function(arg)
    toggleSetting("chatlog", arg,
      "chat-log channel on: chat logging is enabled and NeedleDropper lines are hidden from your chat.",
      "chat-log channel off.")
  end,
  selftest = function(arg)
    local S = needModule("SelfTest")
    if S then S.Run(ns.Lower(ns.Trim(arg))) end
  end,
  debug = function(arg)
    toggleSetting("debug", arg, "debug messages on.", "debug messages off.")
  end,
  set = function(arg)
    local key, value = string.match(ns.Trim(arg), "^(%S+)%s+(.+)$")
    if not key then
      ns.Print("usage: /needle set <setting> <value>  (see /needle settings)")
      return
    end
    -- case-insensitive key lookup
    local real
    for k in pairs(ns.DEFAULTS) do
      if string.lower(k) == string.lower(key) then real = k end
    end
    if not real then
      ns.Print("unknown setting '%s' (see /needle settings).", key)
      return
    end
    local v, err = ns.SetSetting(real, ns.Trim(value))
    if v == nil then
      ns.Print("%s", err)
    else
      ns.Print("%s = %s", real, ns.FormatSettingValue(real, v))
    end
  end,
  settings = function()
    ns.Print("settings (change with /needle set <setting> <value>):")
    for i = 1, #ns.SETTINGS do
      local m = ns.SETTINGS[i]
      local range = ""
      if m.type == "number" then
        range = string.format(" (%s-%s)", tostring(m.min), tostring(m.max))
      elseif m.options then
        local names = {}
        for j = 1, #m.options do names[#names + 1] = m.options[j][1] end
        range = " (" .. table.concat(names, "|") .. ")"
      end
      ns.Print("  %s = |cffffffff%s|r%s - %s", m.key, ns.FormatSettingValue(m.key, ns.settings[m.key]), range, m.label)
    end
  end,
  options = function()
    ns.OpenOptions()
  end,
}

ns.Commands = COMMANDS

function ns.HandleSlash(msg)
  if not ns.initialized then
    ns.Print("still loading - try again in a moment.")
    return
  end
  msg = ns.Trim(ns.SafeString(msg) or "")
  local cmd, rest = string.match(msg, "^(%S*)%s*(.-)$")
  cmd = ns.Lower(cmd or "")
  if cmd == "" or cmd == "?" then cmd = "help" end
  local fn = COMMANDS[cmd]
  if not fn then
    ns.Print("unknown command '%s'. Type /needle help.", cmd)
    return
  end
  xpcall(function() fn(rest or "") end, onError)
end

SLASH_NEEDLEDROPPER1 = "/needle"
SLASH_NEEDLEDROPPER2 = "/nd"
SlashCmdList["NEEDLEDROPPER"] = function(msg) ns.HandleSlash(msg) end

-- Replaced by Options.lua when the Settings panel is available.
function ns.OpenOptions()
  ns.Print("the options panel is not available; use /needle settings and /needle set <setting> <value>.")
end

function NeedleDropper_OnAddonCompartmentClick()
  ns.OpenOptions()
end
