local ADDON, ns = ...
-- NeedleDropper Core: namespace, settings, SavedVariables (schema 2) and their migration, event
-- dispatch, printing, CVar helpers, slash commands and the add-on compartment entry points.
--
-- Loaded after Locale.lua, Json.lua, Base64.lua and Protocol2.lua and before everything else
-- (CONTRACTS-v1.2 2.1). Later modules (Soundtracks, Director, UI ...) are only reached at call
-- time, never while this file loads.
--
-- What plays comes from the soundtrack model: Soundtracks.Init() runs on ADDON_LOADED, right
-- before ND_INIT is fired, and builds ns.Map from the active soundtracks (built-in Sets\*.lua
-- plus the player's own). Until then ns.Map is an empty map of the same shape.

ns.ADDON = ADDON or "NeedleDropper"
ns.SCHEMA = 2
ns.PREFIX = "|cff9fd3ffNeedleDropper|r:"
ns.SILENCE = "Interface\\AddOns\\NeedleDropper\\media\\silence.mp3"
ns.VERSION = "1.2.1"
do
  local getMeta = (C_AddOns and C_AddOns.GetAddOnMetadata) or GetAddOnMetadata
  if getMeta then
    local ok, v = pcall(getMeta, ns.ADDON, "Version")
    if ok and type(v) == "string" and v ~= "" then ns.VERSION = v end
  end
end

local L = ns.L or setmetatable({}, { __index = function(_, k) return k end })

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

-- Text from players, imports or the app before it reaches chat or a FontString (Locale.lua
-- defines it; this fallback keeps Core usable on its own).
local function esc(s)
  if ns.EscapeText then return ns.EscapeText(s) end
  if type(s) ~= "string" then return "" end
  return (string.gsub(s, "|", "||"))
end

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

local function addMessage(line)
  if DEFAULT_CHAT_FRAME and DEFAULT_CHAT_FRAME.AddMessage then
    DEFAULT_CHAT_FRAME:AddMessage(line)
  else
    print(line)
  end
end

function ns.Print(msg, ...)
  addMessage(ns.PREFIX .. " " .. safeFormat(msg, ...))
end

-- A chat line that names the addon itself ("NeedleDropper 1.2: ..."): no second prefix.
function ns.PrintLine(text)
  text = tostring(text or "")
  local colored = string.gsub(text, "^NeedleDropper", "|cff9fd3ffNeedleDropper|r", 1)
  addMessage(colored)
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
  -- Music services (DESIGN-v1.2.1 1.4): what the NeedleDropper app plays songs with. Sent to the
  -- app (PREFS) once the player has chosen one of them in game (NeedleDropperDB.music.prefsSet).
  musicOrder = "spotify-youtube",
  youtubeCorner = "topright",
  musicFade = true,
  resumeMyMusic = true,
  combatFadeSec = 5,
  maxDropSec = 300,
  duckMethod = "silence",
  kickZoneMusic = true,
  duckWithoutApp = false,
  localPlayback = true,
  toast = true,
  toastScale = 1.0,
  toastY = 0.22,
  toastSeconds = 7,
  beacon = true,
  beaconCellPx = 3,
  beaconCorner = "topleft",
  chatlog = false,
  chatlogPadBytes = 50000,
  debug = false,
}

-- Ordered setting descriptions: validation, /needle set, and the options panel. Entries with
-- advanced = true are left out of the Settings panel (/needle set still takes them).
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
    tip = "Respect uses the world map's exploration data so veterans don't get every song of the built-in soundtracks at once. Drops you add yourself always play on your next visit." },
  { key = "scope", type = "string", label = "Play each drop once per",
    options = { { "character", "Character" }, { "account", "Account" } },
    tip = "Whether each song plays once per character or once per account." },
  { key = "suppressOnTaxi", type = "boolean", label = "Wait while on a flight path",
    tip = "Hold pending drops while you are on a taxi." },
  { key = "instanceDrops", type = "boolean", label = "Instance drops",
    tip = "Allow drops that belong to dungeons and raids." },
  { key = "musicOrder", type = "string", label = "Play music with",
    options = { { "spotify-youtube", "Spotify, then YouTube" }, { "youtube-spotify", "YouTube, then Spotify" },
                { "spotify", "Spotify only" }, { "youtube", "YouTube only" },
                { "none", "Neither: title cards and in-game files only" } },
    tip = "Which service the NeedleDropper app uses for a song, and which one it tries next if that can't play it. In-game audio files always play in the game. YouTube only: drops without a YouTube link show just their title card." },
  { key = "youtubeCorner", type = "string", label = "YouTube box position",
    options = { { "topright", "Top right" }, { "topleft", "Top left (below the beacon)" },
                { "bottomright", "Bottom right" }, { "bottomleft", "Bottom left" } },
    tip = "Where the small YouTube player box appears while a YouTube song plays. YouTube requires the player to stay visible." },
  { key = "musicFade", type = "boolean", label = "Fade songs in and out",
    tip = "Off: songs start and stop at once, like a real needle drop." },
  { key = "resumeMyMusic", type = "boolean", label = "Resume my Spotify music afterwards",
    tip = "When a Spotify needle drop ends, Spotify goes back to what you were listening to." },
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
  { key = "duckWithoutApp", type = "boolean", label = "Fade zone music without the app",
    tip = "Also fade the zone music for Spotify and YouTube songs when the NeedleDropper app has never reported in. Off: such drops show their title card and the zone music keeps playing, because nothing would play the song." },
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
    tip = "Tiny colour strip in the top-left corner that the NeedleDropper app reads." },
  { key = "beaconCellPx", type = "number", min = 2, max = 4, step = 1, integer = true, label = "Beacon cell size (px)",
    tip = "Physical pixels per beacon cell. The strip is never taller than 9 pixels; the app reads the size from the strip itself." },
  { key = "beaconCorner", type = "string", advanced = true, label = "Beacon corner",
    options = { { "topleft", "Top left" }, { "topright", "Top right" }, { "bottomleft", "Bottom left" },
                { "bottomright", "Bottom right" } },
    tip = "Advanced: the screen corner that holds the beacon. The app looks in the top-left corner." },
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

-- music (DESIGN-v1.2.1 1.4): prefsSet = the player chose a music setting in game (then PREFS
-- travel to the app), clientId = the Spotify Client ID typed in game (32 lowercase hex, or nil),
-- requests = the last 8 requests sent to the app ({ action =, at = time() }, oldest first).
local ACCOUNT_DEFAULTS = { schema = ns.SCHEMA, settings = ns.DEFAULTS, accountDone = {}, lastSeq = 0,
                           music = { prefsSet = false, requests = {} } }
local CHAR_DEFAULTS = { schema = ns.SCHEMA, done = {}, scanned = {}, journal = {}, lastFireAt = 0 }
local JOURNAL_MAX = 200
ns.JOURNAL_MAX = JOURNAL_MAX

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

-- The five requests the game can send the app (DESIGN-v1.2.1 2.3 ACTION codes 1..5).
ns.MUSIC_ACTIONS = { "connect-spotify", "disconnect-spotify", "test-spotify", "test-youtube", "youtube-sign-in" }
ns.MUSIC_REQUESTS_MAX = 8
local MUSIC_ACTION = {}
for i = 1, #ns.MUSIC_ACTIONS do MUSIC_ACTION[ns.MUSIC_ACTIONS[i]] = true end

-- NeedleDropperDB.music after mergeDefaults (DESIGN-v1.2.1 1.4): a Client ID that is not 32
-- lowercase hex digits is forgotten; requests keep the valid entries, newest 8.
local function normalizeMusic(m)
  if type(m.prefsSet) ~= "boolean" then m.prefsSet = false end
  local id = m.clientId
  if not (type(id) == "string" and #id == 32 and string.find(id, "^[0-9a-f]+$")) then m.clientId = nil end
  local clean = {}
  local list = type(m.requests) == "table" and m.requests or {}
  for i = 1, #list do
    local r = list[i]
    if type(r) == "table" and MUSIC_ACTION[r.action] and isInt(r.at) and r.at >= 0 then
      clean[#clean + 1] = { action = r.action, at = r.at }
    end
  end
  while #clean > ns.MUSIC_REQUESTS_MAX do table.remove(clean, 1) end
  m.requests = clean
end

-- The drop that was playing when the UI reloaded (Director, baseline finding ND-DIR-01).
local function normalizePlaying(c)
  local p = c.playing
  if p == nil then return end
  if type(p) ~= "table" or type(p.id) ~= "string" or type(p.endsAtWall) ~= "number" then c.playing = nil end
end

local function initDB()
  local fresh = type(NeedleDropperDB) ~= "table"
  if fresh then NeedleDropperDB = {} end
  local db = NeedleDropperDB
  -- A table without a usable schema predates schema numbers: treat it as v1.
  if type(db.schema) ~= "number" or db.schema ~= db.schema then db.schema = fresh and ns.SCHEMA or 1 end
  if db.schema > ns.SCHEMA then ns.newerSchema = true end
  ns.freshAccount = fresh
  -- Schema 1 -> 2 before the settings are validated (Soundtracks.MigrateAccount: a v1
  -- beaconCellPx of 4 becomes 3 and other values are clamped into 2..4 first).
  local S = ns.Soundtracks
  if S and S.MigrateAccount and not ns.newerSchema then
    local ok, from = pcall(S.MigrateAccount, db)
    if ok then ns.migratedFrom = from end
  end
  mergeDefaults(db, ACCOUNT_DEFAULTS)
  validateSettings(db.settings)
  normalizeDone(db.accountDone)
  if db.savedCVars ~= nil and type(db.savedCVars) ~= "table" then db.savedCVars = nil end
  if not isInt(db.lastSeq) or db.lastSeq < 0 or db.lastSeq > 255 then db.lastSeq = 0 end
  normalizeMusic(db.music)

  if type(NeedleDropperCharDB) ~= "table" then
    NeedleDropperCharDB = {}
    ns.charDBCreated = true
  end
  local c = NeedleDropperCharDB
  if type(c.schema) ~= "number" or c.schema ~= c.schema then c.schema = ns.charDBCreated and ns.SCHEMA or 1 end
  mergeDefaults(c, CHAR_DEFAULTS)
  normalizeDone(c.done)
  for id, v in pairs(c.scanned) do
    if type(id) ~= "string" or v ~= true then c.scanned[id] = nil end
  end
  normalizeJournal(c.journal)
  if type(c.lastFireAt) ~= "number" or c.lastFireAt ~= c.lastFireAt then c.lastFireAt = 0 end
  normalizePlaying(c)

  ns.db = db
  ns.cdb = c
  ns.settings = db.settings
end

local function nonEmpty(v)
  if type(v) == "string" and v ~= "" then return v end
  return nil
end

local function positiveInt(v)
  if isInt(v) and v > 0 then return v end
  return nil
end

-- Bare file name of an in-game drop file (map drops carry the full path in `file` and the bare
-- name in `fileName`).
local function bareFile(drop)
  local name = nonEmpty(drop.fileName)
  if name then return name end
  local path = nonEmpty(drop.file)
  if not path then return nil end
  return string.match(path, "([^\\/]+)$") or path
end

-- Journal entry for a drop that fired (DESIGN-v1.2 4.6): the place and the song itself, so a
-- replay still works after the drop was edited or deleted.
function ns.AddJournal(drop)
  if not ns.cdb or type(drop) ~= "table" then return end
  local j = ns.cdb.journal
  j[#j + 1] = {
    id = drop.id, t = time(), label = drop.label, title = nonEmpty(drop.title), artist = nonEmpty(drop.artist),
    spotify = nonEmpty(drop.spotify), youtube = nonEmpty(drop.youtube), file = bareFile(drop),
    startMs = positiveInt(drop.startMs), durationMs = positiveInt(drop.durationMs),
    volume = isInt(drop.volume) and drop.volume or nil, setId = nonEmpty(drop.setId),
  }
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

-- The map shape Soundtracks.BuildActiveMap produces (CONTRACTS-v1.2 2.5), empty. Used until the
-- soundtrack model has run, so nothing ever indexes a missing map.
function ns.EmptyMap()
  return {
    loaded = false, version = 2, hash = 0, generated = "", packs = {}, refs = {}, drops = {}, byId = {}, byN = {},
    zoneDrops = {}, subDrops = {}, looseSubDrops = {}, areaDrops = {}, instanceDrops = {}, problems = {},
    invalid = 0, overridden = 0, disabled = 0, drafts = 0,
  }
end

ns.Map = ns.EmptyMap()

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

-- The last error NeedleDropper's own handlers caught, for /needle report (D16): its text (at most
-- 300 bytes) and time(). Never sent anywhere.
function ns.NoteError(err)
  local text = tostring(err)
  if #text > 300 then text = string.sub(text, 1, 300) end
  ns.lastError = { text = text, at = (type(time) == "function" and time()) or 0 }
end

local function onError(err)
  pcall(ns.NoteError, err)
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

-- --------------------------------------------------------- in the world ----

-- Real-client finding 1 (2026-11-05): while the loading screen is up the unit APIs can answer
-- wrongly (the first HELLO after login said DEAD for a living character). Live flags are only
-- computed in the world: after PLAYER_ENTERING_WORLD once the loading screen is gone.
ns.world = { entered = false, loading = true, token = 0 }
local WORLD_FALLBACK = 3.0   -- seconds after PLAYER_ENTERING_WORLD if LOADING_SCREEN_DISABLED never comes

function ns.InWorld()
  return ns.world.entered and not ns.world.loading
end

ns.On("PLAYER_ENTERING_WORLD", function()
  local w = ns.world
  w.entered = true
  w.token = w.token + 1
  local mine = w.token
  if C_Timer and C_Timer.After then
    C_Timer.After(WORLD_FALLBACK, function()
      if w.token == mine and w.entered and w.loading then w.loading = false end
    end)
  end
end)
ns.On("PLAYER_LEAVING_WORLD", function() ns.world.entered = false end)
ns.On("LOADING_SCREEN_ENABLED", function() ns.world.loading = true end)
ns.On("LOADING_SCREEN_DISABLED", function() ns.world.loading = false end)

-- ---------------------------------------------------------------- startup ----

local function initialize()
  initDB()
  ns.initialized = true
  -- ns.Map comes from the soundtrack model from here on (idempotent; Soundtracks also listens
  -- to ND_INIT itself).
  local S = ns.Soundtracks
  if S and S.Init then
    local ok, err = pcall(S.Init)
    if not ok then onError(err) end
  end
  ns.Fire("ND_INIT")
end

ns.On("ADDON_LOADED", function(_, name)
  if name ~= ns.ADDON or ns.initialized then return end
  initialize()
end)

ns.On("PLAYER_LOGIN", function()
  if not ns.initialized then
    -- ADDON_LOADED was missed (should not happen); initialize anyway.
    initialize()
  end
  ns.RepairCVars()
  local S = ns.Soundtracks
  if ns.newerSchema or (S and S.newerData) then
    ns.Print(L["saved data comes from a newer NeedleDropper version; some of it may be ignored."])
  end
  local M = ns.Map
  if type(M) == "table" and type(M.invalid) == "number" and M.invalid > 0 then
    ns.Debug("map: %d drop%s of your soundtracks could not be used", M.invalid, M.invalid == 1 and "" or "s")
    for i = 1, #(M.problems or {}) do ns.Debug("map: %s", M.problems[i]) end
  end
  -- DESIGN-v1.2 4.8: one line on the first login with 1.2 (an upgraded or a fresh account).
  if (ns.migratedFrom or ns.freshAccount) and not ns.welcomed and S and S.WelcomeLine then
    ns.welcomed = true
    local ok, line = pcall(S.WelcomeLine)
    if ok and type(line) == "string" and line ~= "" then ns.PrintLine(line) end
  end
end)

-- DESIGN-v1.2 4.2: soundtracks the NeedleDropper app converted from its v1.1 packs are offered
-- once (Companion.Imports); /needle import opens the next one.
local importsAnnounced = false
ns.On("ND_COMPANION_DATA", function()
  if importsAnnounced then return end
  local C = ns.Companion
  if not (C and C.Imports) then return end
  local ok, list = pcall(C.Imports)
  if not ok or type(list) ~= "table" or #list == 0 then return end
  importsAnnounced = true
  if #list == 1 then
    ns.Print(L["1 soundtrack from your NeedleDropper app is ready to import: type /needle import to review it."])
  else
    ns.Print(L["%d soundtracks from your NeedleDropper app are ready to import: type /needle import to review them."], #list)
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
local ORDER = { "help", "add", "soundtracks", "journal", "import", "share", "music", "status", "where", "replay",
                "play", "test", "toast", "stop", "skip", "reset", "beacon", "chatlog", "selftest", "report", "debug",
                "set", "settings", "options" }

local USAGE = {
  help = "this list (/needle on its own opens or closes the window)",
  add = "[Artist - Title | link] - add a needle drop where you stand",
  soundtracks = "open the window on the Soundtracks tab",
  journal = "[print [count]] - open the Journal tab (print: list the songs you heard in chat)",
  import = "paste a share code to import a soundtrack",
  share = "[name] - share one of your soundtracks as a code",
  music = "[status|connect|disconnect|test spotify|test youtube|youtube] - Spotify and YouTube (opens Music services)",
  report = "a report of your setup for a bug report (press Ctrl+C to copy it)",
  status = "settings, transports, soundtracks, pending/playing drop, cooldown",
  where = "current zone key, uiMapID, subzone, instance and matching drop ids",
  replay = "[id] - replay the last (or given) drop",
  play = "<id|place name> - play a drop now (test, does not mark it heard)",
  test = "send a test signal to the NeedleDropper app and show the test card",
  toast = "[on|off] - preview the title card where you are (or turn title cards on/off)",
  stop = "stop the playing drop (or let the pending one go for now)",
  skip = "stop / skip the current drop and keep it marked as heard",
  reset = "char|account|all|settings - forget history (or reset settings)",
  beacon = "on|off - pixel beacon for the companion",
  chatlog = "on|off - chat-log channel for the companion",
  selftest = "[beacon|chatlog|discovery|music|share] - check your client setup",
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

-- The window (UI\*.lua); nil when it is not loaded.
local function ui()
  local U = ns.UI
  if type(U) == "table" and type(U.Open) == "function" then return U end
  return nil
end

local function printJournal(arg)
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
    ns.Print("  %s  %s - %s \194\183 %s  |cff8a8a8a[%s]|r", when, esc(tostring(e.label or e.id)),
      esc(tostring(e.artist or "?")), esc(tostring(e.title or "?")), esc(e.id))
  end
  ns.Print("replay one with /needle replay <id>.")
end

-- Soundtrack ref for a name or id typed after /needle share (case-insensitive; yours first).
local function findSoundtrack(name)
  local S = ns.Soundtracks
  if not (S and S.List) then return nil end
  local want = string.lower(ns.Trim(name))
  local list = S.List()
  local best
  for i = 1, #list do
    local e = list[i]
    if string.lower(e.name or "") == want or string.lower(e.id or "") == want or string.lower(e.ref or "") == want then
      if not e.builtin then return e.ref end
      best = best or e.ref
    end
  end
  return best
end

COMMANDS = {
  help = function()
    ns.Print("v%s commands (/needle or /nd; /needle on its own opens the window):", ns.VERSION)
    for i = 1, #ORDER do
      ns.Print("  |cffffffff/needle %s|r - %s", ORDER[i], USAGE[ORDER[i]])
    end
  end,
  add = function(arg)
    local text = ns.Trim(arg)
    local U = ui()
    if U and U.AddHere then
      U.AddHere(text ~= "" and text or nil)
      return
    end
    local S = needModule("Soundtracks")
    if not S then return end
    local fields, perr = S.ParseSongText(text)
    if not fields then
      ns.Print("%s", perr)
      return
    end
    local ref, id = S.AddHere(nil, fields)
    if not ref then
      ns.Print("%s", tostring(id))
      return
    end
    ns.Print("added %s to %s.", esc(id), esc(ref))
  end,
  soundtracks = function()
    local U = ui()
    if U then U.Open("soundtracks") else ns.Print("the NeedleDropper window is not available.") end
  end,
  journal = function(arg)
    local word, rest = string.match(ns.Trim(arg), "^(%S*)%s*(.-)$")
    word = ns.Lower(word or "")
    local U = ui()
    if word == "print" then return printJournal(rest) end
    if tonumber(word) or not U then return printJournal(word) end
    U.Open("journal")
  end,
  import = function()
    local U = ui()
    if not (U and U.OpenImport) then
      ns.Print("the NeedleDropper window is not available.")
      return
    end
    local C = ns.Companion
    local offers = (C and C.Imports) and C.Imports() or {}
    local offer = offers[1]
    if offer and type(offer.code) == "string" then
      -- Offered once (DESIGN-v1.2 4.2): handled as soon as its preview is on screen, not before.
      if not U.OpenImport(offer.code) then return end
      if C.MarkImportHandled then C.MarkImportHandled(offer) end
      if #offers > 1 then
        ns.Print("%d more soundtracks from your NeedleDropper app wait: type /needle import again.", #offers - 1)
      end
      return
    end
    U.OpenImport()
  end,
  share = function(arg)
    local U = ui()
    if not (U and U.OpenShare) then
      ns.Print("the NeedleDropper window is not available.")
      return
    end
    local name = ns.Trim(arg)
    if name == "" then
      U.OpenShare()
      return
    end
    local ref = findSoundtrack(name)
    if not ref then
      ns.Print("no soundtrack is called '%s'. Open the Soundtracks tab with /needle soundtracks.", esc(name))
      return
    end
    U.OpenShare(ref)
  end,
  -- DESIGN-v1.2.1 1.6: Music services (MusicServices.lua parses the rest).
  music = function(arg)
    local MS = needModule("MusicServices")
    if MS and MS.Slash then MS.Slash(arg) end
  end,
  -- D16: a plain-text report of the setup for bug reports (SelfTest.lua builds it).
  report = function()
    local S = needModule("SelfTest")
    if S and S.Report then S.Report() end
  end,
  status = function()
    local D = needModule("Director")
    if D then
      local lines = D.StatusLines()
      for i = 1, #lines do ns.Print(lines[i]) end
    end
    if ns.Toast and ns.Toast.StatusLine then ns.Print("%s", ns.Toast.StatusLine()) end
    local C = ns.Companion
    if C and C.StatusLine then ns.Print("app: %s", esc(C.StatusLine())) end
  end,
  where = function()
    local Loc = needModule("Location")
    if Loc then
      local lines = Loc.Describe()
      for i = 1, #lines do ns.Print(lines[i]) end
    end
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
    local place = esc(info.place or "")
    if info.drop then
      ns.Print("title card preview: %s |cff8a8a8a[%s]|r", place, esc(info.drop.id))
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
      ns.Print("history mode is 'respect': places this character already explored stay skipped for the built-in soundtracks. " ..
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
      ns.Print("unknown setting '%s' (see /needle settings).", esc(key))
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
  if cmd == "" then
    -- DESIGN-v1.2 4.8: bare /needle and /nd open and close the window.
    local U = ui()
    if U and U.Toggle then
      xpcall(function() U.Toggle() end, onError)
      return
    end
    cmd = "help"
  end
  if cmd == "?" then cmd = "help" end
  local fn = COMMANDS[cmd]
  if not fn then
    ns.Print("unknown command '%s'. Type /needle help.", esc(cmd))
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

-- ------------------------------------------------------- add-on compartment ----
-- Blizzard_Minimap AddonCompartment.lua (forever; the camelot file only changes the art) calls
-- _G[AddonCompartmentFunc](addonName, menuInputData.buttonName) on a click and
-- _G[...FuncOnEnter / ...FuncOnLeave](addonName, menuButton) on hover (DESIGN-v1.2 4.8).

-- Names of the active soundtracks, in priority order.
local function activeNames()
  local S = ns.Soundtracks
  local names = {}
  if not (S and S.List) then return names end
  local ok, list = pcall(S.List)
  if not ok or type(list) ~= "table" then return names end
  local byPrio = {}
  for i = 1, #list do
    local e = list[i]
    if e.active and e.priority then byPrio[#byPrio + 1] = e end
  end
  table.sort(byPrio, function(a, b) return a.priority < b.priority end)
  for i = 1, #byPrio do names[#names + 1] = byPrio[i].name or byPrio[i].id end
  return names
end
ns.ActiveSoundtrackNames = activeNames

-- One line about what the Director is doing (playing / waiting / cooldown / idle), plain text.
function ns.PlaybackSummary()
  local D = ns.Director
  if not ns.settings then return L["NeedleDropper is still loading."] end
  if not ns.settings.enabled then return L["Needle drops are off (Settings: Enable needle drops)."] end
  if type(D) ~= "table" then return "" end
  local Toast = ns.Toast
  local function song(d)
    local credit = Toast and Toast.Subtitle and Toast.Subtitle(d) or ""
    local place = (Toast and Toast.PlaceName and Toast.PlaceName(d)) or d.label or d.id or ""
    if credit ~= "" then return place .. " \194\183 " .. credit end
    return place
  end
  if D.active and D.active.drop then
    return safeFormat(L["Playing: %s"], song(D.active.drop))
  end
  if D.armed and D.armed.drop then
    local d = D.armed.drop
    local reasons = D.GateReasons and D.GateReasons(d) or {}
    local place = (Toast and Toast.PlaceName and Toast.PlaceName(d)) or d.label or d.id or ""
    if #reasons > 0 then
      return safeFormat(L["Waiting for a calm moment: %s (%s)"], place, reasons[1])
    end
    local cd = D.CooldownLeft and D.CooldownLeft() or 0
    if cd > 0 then return safeFormat(L["Next needle drop in %s: %s"], ns.FormatSeconds(cd), place) end
    return safeFormat(L["Arriving: %s"], place)
  end
  local cd = D.CooldownLeft and D.CooldownLeft() or 0
  if cd > 0 then return safeFormat(L["Next needle drop possible in %s"], ns.FormatSeconds(cd)) end
  return L["Nothing playing. Explore somewhere new!"]
end

function ns.FillCompartmentTooltip(tooltip)
  if type(tooltip) ~= "table" then return end
  local function line(text, r, g, b, wrap)
    tooltip:AddLine(esc(text), r, g, b, wrap)
  end
  if GameTooltip_SetTitle then
    GameTooltip_SetTitle(tooltip, "NeedleDropper")
  else
    tooltip:SetText("NeedleDropper")
  end
  line(ns.PlaybackSummary(), 1, 1, 1, true)
  local names = activeNames()
  if #names > 0 then
    line(safeFormat(L["Soundtracks: %s"], table.concat(names, ", ")), 1, 0.82, 0, true)
  else
    line(L["No soundtrack is on."], 1, 0.82, 0, true)
  end
  local C = ns.Companion
  if C and C.StatusLine then
    local ok, status = pcall(C.StatusLine)
    if ok and type(status) == "string" and status ~= "" then line(status, 0.75, 0.75, 0.75, true) end
  end
  local hint = L["Left-click: open \194\183 Right-click: settings"]
  if GameTooltip_AddInstructionLine then
    GameTooltip_AddInstructionLine(tooltip, hint)
  else
    line(hint, 0.1, 1, 0.1)
  end
end

function NeedleDropper_OnAddonCompartmentClick(addonName, buttonName)
  if buttonName == "RightButton" then
    ns.OpenOptions()
    return
  end
  local U = ui()
  if U and U.Toggle then
    U.Toggle()
  else
    ns.OpenOptions()
  end
end

function NeedleDropper_OnAddonCompartmentEnter(addonName, button)
  if not (GameTooltip and GameTooltip.SetOwner) then return end
  local ok = pcall(GameTooltip.SetOwner, GameTooltip, button, "ANCHOR_LEFT")
  if not ok then return end
  ns.FillCompartmentTooltip(GameTooltip)
  GameTooltip:Show()
end

function NeedleDropper_OnAddonCompartmentLeave(addonName, button)
  if GameTooltip and GameTooltip.Hide then GameTooltip:Hide() end
end
