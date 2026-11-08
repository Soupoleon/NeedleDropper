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
    music = {                                                     -- 1.2.1 (DESIGN-v1.2.1 3.10, D17)
      spotify = { clientId = "<32 lowercase hex>", redirectUri = "http://127.0.0.1:8321/callback",
                  account = "Ethan", product = "premium", state = "connected", mode = "app",
                  problem = nil | "allowlist" | "no-chrome" | ... (C.SPOTIFY_PROBLEMS) },
      youtube = { enabled = true, player = "chrome" | "edge" | "none", signInOpened = <unix time> },
      prefs   = { order = "spotify-youtube", corner = "topright", fade = true, resume = true, fromGame = true },
      actions = { { id = <issuedAt>, action = "connect-spotify", result = "connected", text = "...", at = <time> } },
    },
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
  Companion.IsOutdated(version) -> true for app versions before 1.2.1 (or unreadable ones): a 1.2.0
                                   app reads no 1.2.1 frame (DESIGN-v1.2.1 2.11, decisions D1 / D2)
  Companion.SpotifyConnected()  -> bool
  Companion.Music()             -> the checked `music` block (1.2.1 apps) | nil
  Companion.AtLeast(version, major, minor, patch) -> bool (an unreadable version is false)
  Companion.Supports(feature)   -> bool; "config": the app takes music requests from the game (>= 1.2.1)
  Companion.SpotifyState()      -> music.spotify.state (D17) when reported, else status.spotify | nil
]]

local C = {}
ns.Companion = C

local L = ns.L or setmetatable({}, { __index = function(_, k) return k end })

local type, pairs, tostring, tonumber = type, pairs, tostring, tonumber
local floor, huge = math.floor, math.huge
local byte, char, find, format, gsub, match = string.byte, string.char, string.find, string.format,
  string.gsub, string.match

-- The oldest app this addon can talk to: 1.2.0 and 1.2.1 frames cannot read each other (the
-- 4-byte frame check, DESIGN-v1.2.1 2.11), so an older app counts as outdated.
C.MIN_APP_MAJOR, C.MIN_APP_MINOR, C.MIN_APP_PATCH = 1, 2, 1
C.MAX_TRACKS = 5000
C.MAX_KEY = 512
C.MAX_TEXT = 1200          -- bytes (the app writes at most 300 characters)
C.MAX_IMPORTS = 50
C.MAX_DURATION = 86400000  -- 24 h, as gamedata.MAX_DURATION_MS
C.MAX_ACTIONS = 8          -- music.actions entries kept (newest last)
C.MAX_ACTION_TEXT = 300    -- characters of an action's text
C.MAX_ACCOUNT = 64         -- characters of the Spotify display name
C.MAX_UINT32 = 4294967295
-- 2100-01-01 00:00 UTC: later time stamps (written, signInOpened, an action's at) are left out. date() gives
-- nothing for a time past the client's calendar (year 3000 on Windows), and a corrupt report must not break
-- the status lines, the dialog or /needle report (review INGAME-7; gamedata.MAX_TIME is the same).
C.MAX_TIME = 4102444800

local SPOTIFY_STATES = { connected = true, ["not-connected"] = true, ["premium-required"] = true, error = true }
-- music.spotify.state (D17): the four v1.2 values plus needs-relogin, no-player and stuck, which
-- status.spotify reports as "error" for older readers.
C.MUSIC_STATES = { "connected", "not-connected", "needs-relogin", "premium-required", "no-player", "stuck", "error" }
C.MUSIC_MODES = { "app", "connect" }
C.YOUTUBE_PLAYERS = { "chrome", "edge", "none" }
-- music.spotify.problem: what the game can say beyond the state. With "error" (review ON-3): "allowlist", the
-- account is not on the developer app's User Management list. With "no-player" (review ON-9): "no-chrome"
-- (no Google Chrome, or its Widevine component not there yet), "not-ready" (the player page never got
-- ready), "no-window" (no player window yet: none is made while the game is in front), "window-never"
-- (deck.autoOpen "never"), "sdk-load" (Spotify's player script didn't load: offline or a firewall). An
-- unknown code is left out; without one the game words the state neutrally.
C.SPOTIFY_PROBLEMS = { "allowlist", "no-chrome", "not-ready", "no-window", "window-never", "sdk-load" }
C.ORDERS = { "spotify-youtube", "youtube-spotify", "spotify", "youtube", "none" }
C.CORNERS = { "topright", "topleft", "bottomright", "bottomleft" }
C.ACTIONS = { "connect-spotify", "disconnect-spotify", "test-spotify", "test-youtube", "youtube-sign-in" }

local function set(list)
  local out = {}
  for i = 1, #list do out[list[i]] = true end
  return out
end
local MUSIC_STATE, MUSIC_MODE, YT_PLAYER = set(C.MUSIC_STATES), set(C.MUSIC_MODES), set(C.YOUTUBE_PLAYERS)
local SP_PROBLEM = set(C.SPOTIFY_PROBLEMS)
local ORDER, CORNER, ACTION = set(C.ORDERS), set(C.CORNERS), set(C.ACTIONS)

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

local function uint(v, maxV)
  if finite(v) and floor(v) == v and v >= 0 and (maxV == nil or v <= maxV) then return v end
  return nil
end

-- UTF-8 characters in `s` (continuation bytes 0x80-0xBF are not counted).
local function chars(s)
  local _, continuation = gsub(s, "[\128-\191]", "")
  return #s - continuation
end

-- Text from the app for display: control characters removed, valid UTF-8, 1..maxChars characters.
local function displayText(v, maxChars)
  if type(v) ~= "string" or #v > maxChars * 4 then return nil end
  v = gsub(v, "[%z\1-\31\127]", "")
  if v == "" or chars(v) > maxChars then return nil end
  local P2 = ns.Protocol2
  if P2 and P2.Utf8Valid and not P2.Utf8Valid(v) then return nil end
  return v
end

local function bool(v)
  if v == true or v == false then return v end
  return nil
end

-- The 1.2.1 `music` block (DESIGN-v1.2.1 3.10 with D17), field by field: an invalid field is left
-- out, an invalid action entry is skipped. The same rules as the app's gamedata._music.
local function checkMusic(raw)
  if type(raw) ~= "table" then return nil end
  local out = { spotify = {}, youtube = {}, prefs = {}, actions = {} }
  local sp = raw.spotify
  if type(sp) == "table" then
    local s = out.spotify
    if type(sp.clientId) == "string" and #sp.clientId == 32 and find(sp.clientId, "^[0-9a-f]+$") then
      s.clientId = sp.clientId
    end
    if type(sp.redirectUri) == "string" and #sp.redirectUri <= 40
       and find(sp.redirectUri, "^http://127%.0%.0%.1:[0-9][0-9]?[0-9]?[0-9]?[0-9]?/callback$") then
      s.redirectUri = sp.redirectUri
    end
    s.account = displayText(sp.account, C.MAX_ACCOUNT)
    if type(sp.product) == "string" and #sp.product <= 16 and find(sp.product, "^[a-z]+$") then s.product = sp.product end
    if MUSIC_STATE[sp.state] then s.state = sp.state end
    if MUSIC_MODE[sp.mode] then s.mode = sp.mode end
    if SP_PROBLEM[sp.problem] then s.problem = sp.problem end
  end
  local yt = raw.youtube
  if type(yt) == "table" then
    local y = out.youtube
    y.enabled = bool(yt.enabled)
    if YT_PLAYER[yt.player] then y.player = yt.player end
    y.signInOpened = uint(yt.signInOpened, C.MAX_TIME)
  end
  local pr = raw.prefs
  if type(pr) == "table" then
    local p = out.prefs
    if ORDER[pr.order] then p.order = pr.order end
    if CORNER[pr.corner] then p.corner = pr.corner end
    p.fade, p.resume, p.fromGame = bool(pr.fade), bool(pr.resume), bool(pr.fromGame)
    p.volume = uint(pr.volume, 100)
  end
  if type(raw.actions) == "table" then
    local list = {}
    for i = 1, #raw.actions do
      local e = raw.actions[i]
      if type(e) == "table" and ACTION[e.action] and uint(e.id, C.MAX_UINT32) and type(e.result) == "string"
         and #e.result <= 24 and find(e.result, "^[a-z][a-z%-]*$") then
        list[#list + 1] = { id = e.id, action = e.action, result = e.result,
                            text = displayText(e.text, C.MAX_ACTION_TEXT), at = uint(e.at, C.MAX_TIME) }
      end
    end
    for i = math.max(1, #list - C.MAX_ACTIONS + 1), #list do out.actions[#out.actions + 1] = list[i] end
  end
  return out
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
  if finite(raw.written) and raw.written >= 0 and raw.written <= C.MAX_TIME then out.written = floor(raw.written) end
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
  out.music = checkMusic(raw.music)
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

-- major, minor, patch of "1.2.1", "1.2", "1", "1.2.1-rc1", "1.2.1.dev0" ...; nil when unreadable.
local function versionParts(v)
  if type(v) ~= "string" then return nil end
  local a, b, c = match(v, "^([0-9]+)%.([0-9]+)%.([0-9]+)")
  if not a then a, b = match(v, "^([0-9]+)%.([0-9]+)") end
  if not a then a = match(v, "^([0-9]+)$") end
  if not a then return nil end
  return tonumber(a), tonumber(b) or 0, tonumber(c) or 0
end

-- True when `version` is major.minor.patch or newer; an unreadable version is false.
function C.AtLeast(version, major, minor, patch)
  local a, b, c = versionParts(version)
  if not a then return false end
  major, minor, patch = tonumber(major) or 0, tonumber(minor) or 0, tonumber(patch) or 0
  if a ~= major then return a > major end
  if b ~= minor then return b > minor end
  return c >= patch
end

function C.IsOutdated(version)
  return not C.AtLeast(version, C.MIN_APP_MAJOR, C.MIN_APP_MINOR, C.MIN_APP_PATCH)
end

-- What an app version can do. "config": music choices and requests from the game (CONFIG
-- frames, PREFS in HELLO; DESIGN-v1.2.1 1.1).
C.FEATURES = { config = { 1, 2, 1 } }

function C.Supports(feature)
  local need = C.FEATURES[feature]
  local d = data
  if not (need and d) then return false end
  return C.AtLeast(d.appVersion, need[1], need[2], need[3])
end

-- The checked `music` block, or nil (no app data, or an app older than 1.2.1).
function C.Music()
  return data and data.music or nil
end

-- Spotify's state as the app reported it: music.spotify.state (D17) when a 1.2.1 app wrote one,
-- else the v1.2 status.spotify; nil when unknown.
function C.SpotifyState()
  local d = data
  if not d then return nil end
  local m = d.music
  if m and m.spotify.state then return m.spotify.state end
  return d.status.spotify
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
  ["needs-relogin"] = "Spotify: sign in again",
  ["no-player"] = "Spotify player not ready",
  stuck = "Spotify didn't start",
  error = "Spotify needs you to sign in again",
}
-- "no-player" by the cause the app names (music.spotify.problem, review ON-9); only "no-chrome" names Chrome.
local NO_PLAYER_TEXT = {
  ["no-chrome"] = "Spotify needs Google Chrome",
  ["not-ready"] = "Spotify player not ready",
  ["no-window"] = "Spotify player not started yet",
  ["window-never"] = "Spotify player switched off",
  ["sdk-load"] = "Spotify player didn't load",
}

-- "Spotify: Ethan (Premium)" when a 1.2.1 app reported the account, else the state's words.
local function spotifyPart(d)
  local state = C.SpotifyState()
  if not state then return nil end
  local sp = d.music and d.music.spotify or {}
  if state == "connected" and sp.account then
    if sp.product == "premium" then return format(L["Spotify: %s (Premium)"], sp.account) end
    if sp.product then return format(L["Spotify: %s (%s)"], sp.account, sp.product) end
    return format(L["Spotify: %s"], sp.account)
  end
  if state == "error" and sp.problem == "allowlist" then return L["Spotify: account not allowed in your Spotify app"] end
  if state == "no-player" and NO_PLAYER_TEXT[sp.problem] then return L[NO_PLAYER_TEXT[sp.problem]] end
  return SPOTIFY_TEXT[state] and L[SPOTIFY_TEXT[state]] or nil
end

-- "YouTube ready" when the app plays YouTube songs, else why not.
local function youtubePart(d)
  local yt = d.music and d.music.youtube or {}
  if not d.status.youtube or yt.enabled == false then return L["YouTube off"] end
  if yt.player == "none" then return L["YouTube: no player browser"] end
  return L["YouTube ready"]
end

local function seenText(age)
  if age < 60 then return L["App seen just before login"] end
  if age < 3600 then return format(L["App seen %d min before login"], floor(age / 60)) end
  if age < 172800 then return format(L["App seen %d h before login"], floor(age / 3600)) end
  return format(L["App seen %d days before login"], floor(age / 86400))
end

-- Seconds between the app's report and the login (never negative), or nil without a report.
function C.ReportAge()
  local d = data
  if not d or d.written <= 0 then return nil end
  local age = (loginTime or time()) - d.written
  if age < 0 then age = 0 end
  return age
end

-- "App seen 2 min before login" style text for a report age.
C.SeenText = seenText

-- One line for the window footer, the compartment tooltip and /needle status, e.g. "App seen 2 min
-- before login · Spotify: Ethan (Premium) · YouTube ready". Raw text: callers escape it.
function C.StatusLine()
  local d = data
  if not d then return L["NeedleDropper app not found: Spotify and YouTube songs need it"] end
  if C.IsOutdated(d.appVersion) then return L["Update the NeedleDropper app to 1.2.1 to play Spotify and YouTube songs"] end
  if d.written <= 0 then return L["NeedleDropper app installed: it hasn't reported in yet"] end
  local line = seenText(C.ReportAge() or 0)
  local sp = spotifyPart(d)
  if sp then line = line .. " \194\183 " .. sp end
  line = line .. " \194\183 " .. youtubePart(d)
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
