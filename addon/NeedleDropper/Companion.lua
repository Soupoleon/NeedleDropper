local ADDON, ns = ...
--[[ NeedleDropper Companion: what the NeedleDropper app (the optional Windows companion) told
the game (DESIGN-v1.2 section 2.6).

The app writes Interface\AddOns\NeedleDropper_Companion\Data.lua (companion gamedata.py), a
separate data addon that sets one global:

  NeedleDropper_CompanionData = {
    schema = 1, appVersion = "1.2.0", written = <unix time; 0 = nothing reported yet>,
    status = { spotify = "connected" | "not-connected" | "premium-required" | "error",
               youtube = bool, transports = { pixel = bool, chatlog = bool } },
    tracks = { ["q:<lower artist>\31<lower title>"] | ["sp:<id>"] =
               { spotify = "<22-char id>", durationMs =, title =, artist =, notFound = bool,
                 notEmbeddable = bool } },
    imports = { { code = "!NeedleDropper:1!...!", active = bool }, ... },   -- one-time migration
  }

The client loads that addon after this one, so the data is read at PLAYER_LOGIN. It is never
required: a missing or malformed table just means "no app data", and everything in it is
checked field by field (share codes in `imports` go through Share.Decode like a paste).

API
  Companion.Read()              re-reads the global (done at PLAYER_LOGIN), fires ND_COMPANION_DATA
  Companion.Data()              -> the checked data table (read-only) | nil
  Companion.Status()            -> { present, appVersion, written, spotify, youtube, transports,
                                     outdated }
  Companion.StatusLine()        -> one localized line for the window footer / compartment tooltip
  Companion.Track(drop)         -> the app's Spotify match / flags for a drop | nil
  Companion.TrackKeys(artist, title) -> the "q:" keys tried for a song
  Companion.Imports()           -> soundtracks the app offers once: { code, active, pack, warnings,
                                     key = "<pack id>@<revision>", name } not yet handled
  Companion.MarkImportHandled(entry | key)   records the answer in NeedleDropperDB.importsHandled
  Companion.IsOutdated(version) -> true for app versions before 1.2 (or unreadable ones)
  Companion.SpotifyConnected()  -> bool
]]

local C = {}
ns.Companion = C

local L = ns.L or setmetatable({}, { __index = function(_, k) return k end })

local type, pairs, tostring, tonumber = type, pairs, tostring, tonumber
local floor, huge = math.floor, math.huge
local byte, char, find, format, gsub, match = string.byte, string.char, string.find, string.format,
  string.gsub, string.match

C.MIN_APP_MAJOR, C.MIN_APP_MINOR = 1, 2
C.MAX_TRACKS = 5000
C.MAX_KEY = 512
C.MAX_TEXT = 1200          -- bytes (the app writes at most 300 characters)
C.MAX_IMPORTS = 50
C.MAX_DURATION = 86400000  -- 24 h, as gamedata.MAX_DURATION_MS

local SPOTIFY_STATES = { connected = true, ["not-connected"] = true, ["premium-required"] = true, error = true }

local data = nil
local loginTime = nil
local importsCache = nil

-- ================================================================ checking ==

local function finite(v)
  return type(v) == "number" and v == v and v ~= huge and v ~= -huge
end

local function text(v, maxBytes)
  if type(v) ~= "string" or #v > maxBytes then return nil end
  local St = ns.Soundtracks
  if St and St.ValidUtf8 and not St.ValidUtf8(v) then return nil end
  return v
end

local function checkTrack(raw)
  if type(raw) ~= "table" then return nil end
  local t = {}
  if type(raw.spotify) == "string" and #raw.spotify == 22 and find(raw.spotify, "^[0-9A-Za-z]+$") then
    t.spotify = raw.spotify
  end
  if finite(raw.durationMs) and floor(raw.durationMs) == raw.durationMs and raw.durationMs >= 0
     and raw.durationMs <= C.MAX_DURATION then
    t.durationMs = raw.durationMs
  end
  t.title = text(raw.title, C.MAX_TEXT)
  t.artist = text(raw.artist, C.MAX_TEXT)
  if raw.notFound == true then t.notFound = true end
  if raw.notEmbeddable == true then t.notEmbeddable = true end
  return t
end

-- Checked copy of the app's data, or nil when there is nothing usable.
local function check(raw)
  if type(raw) ~= "table" then return nil end
  local schema = raw.schema
  if schema == nil then schema = 1 end
  if not finite(schema) or schema < 1 then return nil end
  local out = { schema = floor(schema), written = 0, status = { youtube = false, transports = {} },
                tracks = {}, imports = {} }
  if type(raw.appVersion) == "string" and #raw.appVersion <= 64
     and find(raw.appVersion, "^[0-9A-Za-z][0-9A-Za-z%.%+_%-]*$") then
    out.appVersion = raw.appVersion
  end
  if finite(raw.written) and raw.written >= 0 then out.written = floor(raw.written) end
  local st = raw.status
  if type(st) == "table" then
    if SPOTIFY_STATES[st.spotify] then out.status.spotify = st.spotify end
    out.status.youtube = st.youtube == true
    if type(st.transports) == "table" then
      for _, name in pairs({ "pixel", "chatlog" }) do
        out.status.transports[name] = st.transports[name] == true
      end
    end
  end
  if type(raw.tracks) == "table" then
    local n = 0
    for key, entry in pairs(raw.tracks) do
      if n >= C.MAX_TRACKS then break end
      if type(key) == "string" and key ~= "" and #key <= C.MAX_KEY then
        local t = checkTrack(entry)
        if t then
          out.tracks[key] = t
          n = n + 1
        end
      end
    end
  end
  if type(raw.imports) == "table" then
    local seen = {}
    for i = 1, #raw.imports do
      if #out.imports >= C.MAX_IMPORTS then break end
      local e = raw.imports[i]
      if type(e) == "table" and type(e.code) == "string" and e.code ~= "" and #e.code <= 65536 and not seen[e.code] then
        seen[e.code] = true
        out.imports[#out.imports + 1] = { code = e.code, active = e.active == true }
      end
    end
  end
  return out
end

-- =================================================================== reading ==

function C.Read()
  data = check(NeedleDropper_CompanionData)
  loginTime = time()
  importsCache = nil
  if ns.Fire then ns.Fire("ND_COMPANION_DATA", data) end
  return data
end

function C.Data()
  return data
end

function C.IsOutdated(version)
  if type(version) ~= "string" then return true end
  local major, minor = match(version, "^([0-9]+)%.([0-9]+)")
  major, minor = tonumber(major), tonumber(minor)
  if not major then
    major = tonumber(match(version, "^([0-9]+)$"))
    minor = 0
  end
  if not major then return true end
  if major ~= C.MIN_APP_MAJOR then return major < C.MIN_APP_MAJOR end
  return minor < C.MIN_APP_MINOR
end

function C.Status()
  local d = data
  local transports = {}
  if d then
    for k, v in pairs(d.status.transports) do transports[k] = v end
  end
  return {
    present = d ~= nil,
    appVersion = d and d.appVersion or nil,
    written = d and d.written or 0,
    spotify = d and d.status.spotify or nil,
    youtube = d ~= nil and d.status.youtube or false,
    transports = transports,
    outdated = d ~= nil and C.IsOutdated(d.appVersion),
  }
end

function C.SpotifyConnected()
  return data ~= nil and data.status.spotify == "connected"
end

local SPOTIFY_TEXT = {
  connected = "Spotify connected",
  ["not-connected"] = "Spotify not connected",
  ["premium-required"] = "Spotify needs Premium",
  error = "Spotify needs you to sign in again",
}

local function seenText(age)
  if age < 60 then return L["App seen just before login"] end
  if age < 3600 then return format(L["App seen %d min before login"], floor(age / 60)) end
  if age < 172800 then return format(L["App seen %d h before login"], floor(age / 3600)) end
  return format(L["App seen %d days before login"], floor(age / 86400))
end

function C.StatusLine()
  local d = data
  if not d then return L["NeedleDropper app not found: Spotify and YouTube songs need it"] end
  if C.IsOutdated(d.appVersion) then return L["Update the NeedleDropper app to 1.2 to play Spotify and YouTube songs"] end
  if d.written <= 0 then return L["NeedleDropper app installed: it hasn't reported in yet"] end
  local age = (loginTime or time()) - d.written
  if age < 0 then age = 0 end
  local line = seenText(age)
  local sp = d.status.spotify and SPOTIFY_TEXT[d.status.spotify]
  if sp then line = line .. " \194\183 " .. L[sp] end
  return line
end

-- ==================================================================== tracks ==

local LOWER = {}
for c = 65, 90 do LOWER[char(c)] = char(c + 32) end

-- Lowercase code point for the 2-byte UTF-8 range (U+0080-U+07FF), as Python's str.lower()
-- does for Latin-1 Supplement, Latin Extended-A, Greek and basic Cyrillic. Returns the code
-- point, or nil plus a replacement string.
local function lowerCodepoint(cp)
  if (cp >= 0xC0 and cp <= 0xD6) or (cp >= 0xD8 and cp <= 0xDE) then return cp + 32 end
  if cp >= 0x100 and cp <= 0x17F then
    if cp == 0x130 then return nil, "i\204\135" end
    if cp == 0x131 or cp == 0x138 or cp == 0x149 or cp == 0x17F then return cp end
    if cp == 0x178 then return 0xFF end
    if (cp >= 0x139 and cp <= 0x148) or (cp >= 0x179 and cp <= 0x17E) then
      if cp % 2 == 1 then return cp + 1 end
      return cp
    end
    if cp % 2 == 0 then return cp + 1 end
    return cp
  end
  if cp == 0x386 then return 0x3AC end
  if cp >= 0x388 and cp <= 0x38A then return cp + 37 end
  if cp == 0x38C then return 0x3CC end
  if cp == 0x38E or cp == 0x38F then return cp + 63 end
  if (cp >= 0x391 and cp <= 0x3A1) or (cp >= 0x3A3 and cp <= 0x3AB) then return cp + 32 end
  if cp >= 0x400 and cp <= 0x40F then return cp + 80 end
  if cp >= 0x410 and cp <= 0x42F then return cp + 32 end
  return cp
end

local function lowerPair(b1, b2)
  local cp = (byte(b1) - 192) * 64 + (byte(b2) - 128)
  local lc, replacement = lowerCodepoint(cp)
  if replacement then return replacement end
  if lc == cp then return b1 .. b2 end
  return char(192 + floor(lc / 64), 128 + lc % 64)
end

-- Lowercase like the app's Python (str.lower) for ASCII and the 2-byte alphabets above.
function C.Lower(s)
  s = gsub(s, "[A-Z]", LOWER)
  if find(s, "[\195-\213]") then s = gsub(s, "([\195-\213])([\128-\191])", lowerPair) end
  return s
end

-- "q:" keys the app may have recorded a song under: its query (artist 0x1F title, cleaned as in
-- the beacon frame) lowercased like Python, then ASCII-only lowercased, then the raw text.
function C.TrackKeys(artist, title)
  local keys, seen = {}, {}
  if type(artist) ~= "string" or type(title) ~= "string" then return keys end
  local P2 = ns.Protocol2
  local bases = { P2 and P2.BuildQuery and P2.BuildQuery(artist, title) or nil, artist .. "\31" .. title }
  for i = 1, 2 do
    local b = bases[i]
    if b then
      for j = 1, 2 do
        local k = "q:" .. (j == 1 and C.Lower(b) or gsub(b, "[A-Z]", LOWER))
        if not seen[k] then
          seen[k] = true
          keys[#keys + 1] = k
        end
      end
    end
  end
  return keys
end

-- The app's record for a drop's song: by Spotify id ("sp:"), YouTube id ("yt:"), search query
-- ("q:") or, from older apps, drop id. nil when the app knows nothing about it.
function C.Track(drop)
  if not data or type(drop) ~= "table" then return nil end
  local t = data.tracks
  if type(drop.spotify) == "string" and drop.spotify ~= "" and t["sp:" .. drop.spotify] then
    return t["sp:" .. drop.spotify]
  end
  if type(drop.youtube) == "string" and drop.youtube ~= "" and t["yt:" .. drop.youtube] then
    return t["yt:" .. drop.youtube]
  end
  local keys = C.TrackKeys(drop.artist, drop.title)
  for i = 1, #keys do
    if t[keys[i]] then return t[keys[i]] end
  end
  if type(drop.id) == "string" and t[drop.id] then return t[drop.id] end
  return nil
end

-- =================================================================== imports ==

function C.ImportKey(pack)
  if type(pack) ~= "table" or type(pack.id) ~= "string" then return nil end
  return pack.id .. "@" .. tostring(pack.revision or 0)
end

function C.Imports()
  if importsCache then return importsCache end
  local out = {}
  local handled = (ns.db and type(ns.db.importsHandled) == "table") and ns.db.importsHandled or {}
  if data and ns.Share then
    local seen = {}
    for i = 1, #data.imports do
      local e = data.imports[i]
      local pack, warnings = ns.Share.Decode(e.code)
      local key = pack and C.ImportKey(pack)
      if key and not handled[key] and not seen[key] then
        seen[key] = true
        out[#out + 1] = { code = e.code, active = e.active, pack = pack, warnings = warnings, key = key,
                          name = pack.name }
      end
    end
  end
  importsCache = out
  return out
end

function C.MarkImportHandled(entry)
  local key = type(entry) == "table" and entry.key or entry
  if type(key) ~= "string" or key == "" or type(ns.db) ~= "table" then return false end
  if type(ns.db.importsHandled) ~= "table" then ns.db.importsHandled = {} end
  ns.db.importsHandled[key] = true
  importsCache = nil
  return true
end

if ns.On then
  ns.On("PLAYER_LOGIN", function() C.Read() end)
end

return C
