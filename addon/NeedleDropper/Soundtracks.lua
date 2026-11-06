local ADDON, ns = ...
--[[ NeedleDropper Soundtracks: the soundtrack model (DESIGN-v1.2 sections 3 and 4.2).

What plays is decided in game, from three sources:
  * built-in soundtracks (Sets.lua, read-only),
  * the player's own soundtracks in NeedleDropperDB.soundtracks (by id, [a-z0-9-]{1,40}),
  * the active list: an ordered list of refs ("builtin:<id>" / "user:<id>"), account-wide in
    NeedleDropperDB.active or per character in NeedleDropperCharDB.active (nil = account list).
    Index 1 wins.

Merge rules (BuildActiveMap): walk the active refs in order; the first entry per drop id claims
it and later entries with that id are "overridden"; an entry with disabled = true claims the id
and plays nothing; drafts (no artist or no title) claim nothing and never play; refs to missing
soundtracks are skipped (and pruned when the game saves, at PLAYER_LOGOUT). A list whose refs
all point at missing soundtracks falls back to the default list; a list the player emptied on
purpose stays empty (nothing plays on that character or account).

Every edit goes through this API: it validates (the same caps as share codes, DESIGN 5.5),
bumps the soundtrack's revision, fires ND_SOUNDTRACKS_CHANGED at once and rebuilds ns.Map 0.25 s
later (debounced), then fires ND_MAP_CHANGED(map). No /reload is ever needed.

"My Drops" (user:my-drops) is the player's personal top layer: OverrideHere ("Change song
here"), TurnOffHere and Revert write to it, and it is moved to the top of the active list so the
change wins.

Undo: a session stack of the last 20 edits (add, update, remove, copy, move, override, turn off,
turn on, revert, delete soundtrack, import, update from a share code).

Limits (S.Limits): 50 soundtracks of your own, 300 drops per soundtrack, 3,000 drops in total.

Map (ns.Map, same shape as v1 so Location / Director keep working):
  { loaded = true, version = 2, hash = 0, generated = "", packs = {names}, refs = {refs},
    drops = {...}, byId, byN, zoneDrops[mapID], subDrops[mapID] = {...}, looseSubDrops = {...},
    areaDrops[areaID], instanceDrops[instanceID], problems = {...}, invalid = n,
    overridden = n, disabled = n, drafts = n }
  Map drops are copies: id, kind, mapID, areaID, subzone, subzoneLower, instanceID, label, title,
  artist, startMs, durationMs, spotify (bare id), youtube, volume, file (full in-game path,
  Interface\AddOns\NeedleDropper_Local\media\<name>), fileName (bare name), tags, note, setRef,
  setId, setName, builtin, n (index in this build, debug only), songKey (changes whenever the
  song, its timing, file or volume changes). Drops of higher soundtracks come first in drops and
  in every list, so a landmark drop of a higher soundtrack is found first.

History (integrator: Director): exploration history (historyMode = respect) marks places as
heard for built-in drops only. HistoryApplies(drop) says whether it may; DoneCounts(drop, rec)
says whether a done record counts for a drop (a "history" mark never hides a drop the player
made); HistoryZoneDrops() lists the drops the zone history scan should look at.
]]

local S = {}
ns.Soundtracks = S

local L = ns.L or setmetatable({}, { __index = function(_, k) return k end })

local type, pairs, ipairs, next, tostring, tonumber, select = type, pairs, ipairs, next, tostring, tonumber, select
local floor, huge = math.floor, math.huge
local byte, find, format, gsub, lower, match, sub = string.byte, string.find, string.format, string.gsub,
  string.lower, string.match, string.sub
local concat, tinsert, tremove, sort = table.concat, table.insert, table.remove, table.sort

S.SCHEMA = 2
S.Limits = { sets = 50, dropsPerSet = 300, dropsTotal = 3000, undo = 20 }
S.MY_DROPS_ID = "my-drops"
S.MY_DROPS_REF = "user:my-drops"
S.MEDIA_PATH = "Interface\\AddOns\\NeedleDropper_Local\\media\\"
S.REBUILD_DELAY = 0.25

-- Text caps in bytes (DESIGN 5.5).
S.MAX = {
  name = 64, author = 64, description = 1000, artist = 200, title = 200, subzone = 100, label = 100,
  note = 500, homepage = 200, tag = 24, tags = 8, file = 100,
}
local MAX_INT = 2147483647
S.MAX_INT = MAX_INT

local MY_DROPS, MY_DROPS_REF = S.MY_DROPS_ID, S.MY_DROPS_REF

-- ================================================================= helpers ==

local function copy(v)
  if type(v) ~= "table" then return v end
  local out = {}
  for k, x in pairs(v) do out[k] = copy(x) end
  return out
end
S.Copy = copy

local function now() return time() end

local LOWER = {}
for c = 65, 90 do LOWER[string.char(c)] = string.char(c + 32) end

-- Lowercases A-Z only (string.lower follows the C locale; display text must not depend on it).
local function asciiLower(s)
  return (gsub(s, "[A-Z]", LOWER))
end
S.AsciiLower = asciiLower

local function isInt(v)
  return type(v) == "number" and v == v and v ~= huge and v ~= -huge and floor(v) == v
end

local function nonEmpty(v)
  if type(v) == "string" and v ~= "" then return v end
  return nil
end

-- Byte-wise string order (Lua's "<" on strings follows the C locale's collation).
local function byteLess(a, b)
  local la, lb = #a, #b
  for i = 1, (la < lb and la or lb) do
    local x, y = byte(a, i), byte(b, i)
    if x ~= y then return x < y end
  end
  return la < lb
end

-- A value the caller actually gave: false and "" mean "clear this field".
local function given(v)
  return v ~= nil and v ~= false and v ~= ""
end

-- A number or a string of ASCII digits -> the integer, else nil (like packs._as_int).
local function intValue(v)
  if type(v) == "number" then
    if isInt(v) then return v end
    return nil
  end
  if type(v) == "string" then
    local s = match(v, "^[ \t]*([0-9]+)[ \t]*$")
    if s and #s <= 15 then return tonumber(s) end
  end
  return nil
end
S.IntValue = intValue

local function checkInt(v, lo, hi)
  local n = intValue(v)
  if n == nil or n < lo or n > hi then return nil end
  return n
end

-- --------------------------------------------------------------- UTF-8 text --

-- True when s is well-formed UTF-8 (no overlong forms, surrogates or code points > U+10FFFF).
local function validUtf8(s)
  local i = find(s, "[\128-\255]")
  while i do
    local c = byte(s, i)
    local n
    if c >= 194 and c <= 223 then
      local c2 = byte(s, i + 1)
      if not c2 or c2 < 128 or c2 > 191 then return false end
      n = 2
    elseif c >= 224 and c <= 239 then
      local c2, c3 = byte(s, i + 1, i + 2)
      local lo, hi = 128, 191
      if c == 224 then lo = 160 elseif c == 237 then hi = 159 end
      if not c3 or c2 < lo or c2 > hi or c3 < 128 or c3 > 191 then return false end
      n = 3
    elseif c >= 240 and c <= 244 then
      local c2, c3, c4 = byte(s, i + 1, i + 3)
      local lo, hi = 128, 191
      if c == 240 then lo = 144 elseif c == 244 then hi = 143 end
      if not c4 or c2 < lo or c2 > hi or c3 < 128 or c3 > 191 or c4 < 128 or c4 > 191 then return false end
      n = 4
    else
      return false
    end
    i = find(s, "[\128-\255]", i + n)
  end
  return true
end
S.ValidUtf8 = validUtf8

-- Removes control characters: C0 and DEL, keeping "\n" when multiline (the same set as the
-- companion's sharecode.py, so both sides normalize text identically).
local function stripControls(s, multiline)
  if multiline then
    if find(s, "[%z\1-\9\11-\31\127]") then s = gsub(s, "[%z\1-\9\11-\31\127]", "") end
  elseif find(s, "[%z\1-\31\127]") then
    s = gsub(s, "[%z\1-\31\127]", "")
  end
  return s
end

-- Length of the whitespace sequence starting at byte i (0 if none): space, newline and the
-- Unicode spaces Python's str.strip() removes (NEL, NBSP, U+1680, U+2000-U+200A, U+2028,
-- U+2029, U+202F, U+205F, U+3000). Other control characters are already gone when this runs.
local function spaceAt(s, i)
  local c = byte(s, i)
  if c == 32 or c == 10 then return 1 end
  if c == 194 then
    local c2 = byte(s, i + 1)
    if c2 == 160 or c2 == 133 then return 2 end
    return 0
  end
  if c == 225 then
    local c2, c3 = byte(s, i + 1, i + 2)
    if c2 == 154 and c3 == 128 then return 3 end
    return 0
  end
  if c == 226 then
    local c2, c3 = byte(s, i + 1, i + 2)
    if c2 == 128 and c3 and ((c3 >= 128 and c3 <= 138) or c3 == 168 or c3 == 169 or c3 == 175) then return 3 end
    if c2 == 129 and c3 == 159 then return 3 end
    return 0
  end
  if c == 227 then
    local c2, c3 = byte(s, i + 1, i + 2)
    if c2 == 128 and c3 == 128 then return 3 end
  end
  return 0
end

local function spaceBefore(s, e)
  local c = byte(s, e)
  if c == 32 or c == 10 then return 1 end
  if (c == 160 or c == 133) and byte(s, e - 1) == 194 then return 2 end
  if c >= 128 and e >= 3 and spaceAt(s, e - 2) == 3 then return 3 end
  return 0
end

local function trimText(s)
  local i, n = 1, #s
  while i <= n do
    local k = spaceAt(s, i)
    if k == 0 then break end
    i = i + k
  end
  local e = n
  while e >= i do
    local k = spaceBefore(s, e)
    if k == 0 then break end
    e = e - k
  end
  return sub(s, i, e)
end

-- Cleaned text (control characters stripped, trimmed), or nil plus an error code:
-- "type" (not a string), "utf8" (invalid UTF-8), "long" (over maxBytes after cleaning).
function S.CleanText(v, maxBytes, multiline)
  if type(v) ~= "string" then return nil, "type" end
  if not validUtf8(v) then return nil, "utf8" end
  v = trimText(stripControls(v, multiline))
  if maxBytes and #v > maxBytes then return nil, "long" end
  return v
end
local cleanText = S.CleanText

local FIELD_NAMES = {
  name = "Name", author = "Author", description = "Description", artist = "Artist", title = "Title",
  subzone = "Landmark", label = "Place name", note = "Note", homepage = "Homepage", tags = "Tags",
  spotify = "Spotify", youtube = "YouTube", file = "In-game file", startMs = "Start at",
  durationMs = "Play for", volume = "Volume", mapID = "Zone", areaID = "Area", instanceID = "Instance",
  disabled = "Turned off", id = "Id", revision = "Revision", zone = "Zone", version = "Version",
  format = "Format", drops = "Drops",
}

function S.FieldName(field)
  return L[FIELD_NAMES[field] or tostring(field)]
end

-- Player-facing message for a CleanText error code.
function S.TextError(field, code, maxBytes)
  local name = S.FieldName(field)
  if code == "utf8" then return format(L["%s is not valid UTF-8 text."], name) end
  if code == "long" then return format(L["%s is longer than %d bytes."], name, maxBytes or 0) end
  return format(L["%s must be text."], name)
end

-- ------------------------------------------------------------- ids and slugs --

local SLUG_SPECIAL = {
  -- Non-ASCII letters whose Python str.lower() contains a-z (models.slugify parity):
  { "\196\176", "i\204\135" },  -- U+0130 LATIN CAPITAL LETTER I WITH DOT ABOVE -> "i" + U+0307
  { "\226\132\170", "k" },      -- U+212A KELVIN SIGN -> "k"
}

-- SPEC 3.1 slug, equal to companion models.slugify: lowercase, every run of characters other
-- than a-z / 0-9 becomes "-", leading and trailing "-" removed. ("Lion's Pride Inn" ->
-- "lion-s-pride-inn"; a name with no a-z / 0-9 at all gives "".)
function S.Slugify(name)
  if type(name) ~= "string" then return "" end
  local s = asciiLower(name)
  for i = 1, #SLUG_SPECIAL do
    local from, to = SLUG_SPECIAL[i][1], SLUG_SPECIAL[i][2]
    if find(s, from, 1, true) then s = gsub(s, from, to) end
  end
  s = gsub(s, "[^a-z0-9]+", "-")
  s = gsub(s, "^%-+", "")
  s = gsub(s, "%-+$", "")
  return s
end

local function intText(v)
  local n = tonumber(v)
  if not n or n ~= n or n == huge or n == -huge then return tostring(v) end
  if n >= 0 then n = floor(n) else n = -floor(-n) end
  if n == 0 then n = 0 end
  return format("%.0f", n)
end

-- Drop id for a place {mapID, areaID, subzone, instanceID}, equal to companion
-- models.drop_id_for: inst:<instanceID> > area:<areaID> > sub:<mapID>:<slug> > zone:<mapID>.
-- nil plus a message when the place is incomplete. (A landmark name without a-z / 0-9 gives
-- "sub:<mapID>:" exactly like Python; Validate refuses such an id.)
function S.DropIdFor(place)
  if type(place) ~= "table" then return nil, L["A needle drop needs a place: a zone, a landmark or an instance."] end
  if place.instanceID ~= nil then return "inst:" .. intText(place.instanceID) end
  if place.areaID ~= nil then return "area:" .. intText(place.areaID) end
  local subzone = place.subzone
  if type(subzone) == "string" and subzone ~= "" then
    if place.mapID == nil then return nil, L["A landmark needs a zone (or an area id)."] end
    return "sub:" .. intText(place.mapID) .. ":" .. S.Slugify(subzone)
  end
  if place.mapID == nil then return nil, L["A needle drop needs a place: a zone, a landmark or an instance."] end
  return "zone:" .. intText(place.mapID)
end

local ID_KIND = { zone = "zone", area = "subzone", sub = "subzone", inst = "instance" }
function S.KindOfId(id)
  if type(id) ~= "string" then return nil end
  return ID_KIND[match(id, "^([a-z]+):") or ""]
end

-- ------------------------------------------------------------- song fields --

-- Links longer than this are refused before parsing (no real song link comes close, and the
-- parsers' trimming must never see a huge hostile string).
local MAX_LINK = 512

-- Bare 22-character Spotify track id from an id, a spotify:track: URI or an open.spotify.com
-- track link; nil plus "type" | "notatrack" (album, playlist, artist, show, episode) | "bad".
function S.SpotifyId(v)
  if type(v) ~= "string" then return nil, "type" end
  if #v > MAX_LINK then return nil, "bad" end
  local P2 = ns.Protocol2
  local id = P2 and P2.SpotifyIdFromLink(v)
  if id then return id end
  local l = asciiLower(v)
  if find(l, "spotify:album:", 1, true) or find(l, "spotify:playlist:", 1, true) or find(l, "spotify:artist:", 1, true)
     or (find(l, "spotify.com/", 1, true) and (find(l, "/album/", 1, true) or find(l, "/playlist/", 1, true)
     or find(l, "/artist/", 1, true) or find(l, "/show/", 1, true) or find(l, "/episode/", 1, true))) then
    return nil, "notatrack"
  end
  return nil, "bad"
end

-- 11-character YouTube video id from an id or a link; nil plus "type" | "playlist" | "bad".
function S.YouTubeId(v)
  if type(v) ~= "string" then return nil, "type" end
  if #v > MAX_LINK then return nil, "bad" end
  local P2 = ns.Protocol2
  local id = P2 and P2.YouTubeIdFromLink(v)
  if id then return id end
  if find(v, "[?&]list=") then return nil, "playlist" end
  return nil, "bad"
end

-- Start offset in ms from a YouTube link's t= / start= parameter ("83", "83s", "1m23s",
-- "1h2m3s"), or nil.
function S.YouTubeStartMs(link)
  if type(link) ~= "string" or #link > MAX_LINK then return nil end
  local v = match(link, "[?&#]t=([0-9hms]+)") or match(link, "[?&]start=([0-9]+)")
  if not v then return nil end
  local secs
  if find(v, "^[0-9]+s?$") then
    secs = tonumber(match(v, "^([0-9]+)"))
  else
    local h = tonumber(match(v, "([0-9]+)h") or "0")
    local m = tonumber(match(v, "([0-9]+)m") or "0")
    local s = tonumber(match(v, "([0-9]+)s") or "0")
    secs = h * 3600 + m * 60 + s
  end
  if not secs or secs <= 0 or secs > 86400 then return nil end
  return secs * 1000
end

-- A bare in-game file name (NeedleDropper_Local\media): letters, digits, space and . _ - ( ),
-- ending in .mp3 or .ogg, at most 100 bytes, no path parts. nil plus "type" | "bad".
function S.FileName(v)
  if type(v) ~= "string" then return nil, "type" end
  if #v > MAX_LINK then return nil, "bad" end
  local s = trimText(v)
  if #s < 5 or #s > S.MAX.file then return nil, "bad" end
  if not find(s, "^[A-Za-z0-9 %._%-%(%)]+$") or find(s, "..", 1, true) then return nil, "bad" end
  local ext = asciiLower(sub(s, -4))
  if ext ~= ".mp3" and ext ~= ".ogg" then return nil, "bad" end
  return s
end

-- Tags (as packs.check_tags + the share-code caps): a list of non-empty single-line texts,
-- trimmed, duplicates listed once, at most 8, each at most 24 bytes.
-- Returns tags (nil when empty), nil, duplicatesRemoved; or false plus an error message.
function S.CleanTags(v)
  if v == nil then return nil end
  local listError = format(L["%s must be a list of words."], S.FieldName("tags"))
  if type(v) ~= "table" then return false, listError end
  for k in pairs(v) do
    if type(k) ~= "number" or not isInt(k) or k < 1 or k > #v then return false, listError end
  end
  local out, seen, dup = {}, {}, false
  for i = 1, #v do
    local t = v[i]
    if type(t) ~= "string" or not validUtf8(t) or find(t, "[%z\1-\31\127]") then
      return false, L["A tag must be a single line of plain text."]
    end
    t = trimText(t)
    if t == "" then return false, L["A tag can't be empty."] end
    if #t > S.MAX.tag then return false, format(L["A tag can be at most %d bytes long."], S.MAX.tag) end
    if seen[t] then
      dup = true
    else
      seen[t] = true
      out[#out + 1] = t
    end
  end
  if #out > S.MAX.tags then return false, format(L["At most %d tags are allowed."], S.MAX.tags) end
  if #out == 0 then return nil, nil, dup end
  return out, nil, dup
end

-- Unicode whitespace that Python's \s matches inside a link (besides ASCII space and controls).
local UNICODE_SPACES = { "\194\133", "\194\160", "\225\154\128", "\226\128[\128-\138]", "\226\128[\168\169\175]",
                         "\226\129\159", "\227\128\128" }

-- Homepage: "" (none), or an http:// / https:// link without spaces or control characters, at
-- most 200 bytes (as packs.py: https?://[^\s\x00-\x1f\x7f]+). nil plus a message otherwise.
-- Trims everything Python's str.strip() removes, ASCII whitespace included.
local function pyStrip(s)
  local prev
  repeat
    prev = s
    s = trimText(s)
    local i = find(s, "[^\9-\13\28-\32]")
    if not i then return "" end
    local e = #s
    while e >= i do
      local b = byte(s, e)
      if (b >= 9 and b <= 13) or (b >= 28 and b <= 32) then e = e - 1 else break end
    end
    s = sub(s, i, e)
  until s == prev
  return s
end

function S.CleanHomepage(v)
  if v == nil or v == "" then return "" end
  local bad = L["The homepage must be an http:// or https:// link."]
  if type(v) ~= "string" or not validUtf8(v) then return nil, bad end
  local t = pyStrip(v)
  local rest = match(t, "^https?://(.+)$")
  if not rest or find(rest, "[%z\1-\32\127]") then return nil, bad end
  for i = 1, #UNICODE_SPACES do
    if find(rest, UNICODE_SPACES[i]) then return nil, bad end
  end
  if #t > S.MAX.homepage then return nil, S.TextError("homepage", "long", S.MAX.homepage) end
  return t
end

function S.IsSetId(id)
  return ns.Sets.IsValidId(id)
end

-- ================================================================ places ==

local function mapName(mapID)
  if not (C_Map and C_Map.GetMapInfo) then return nil end
  local ok, info = pcall(C_Map.GetMapInfo, mapID)
  if ok and type(info) == "table" then
    local name = ns.SafeString and ns.SafeString(info.name) or (type(info.name) == "string" and info.name or nil)
    return nonEmpty(name)
  end
  return nil
end
S.MapName = mapName

-- Title-card fallback name for a place: landmark name, area name, zone name or "Zone <id>".
function S.DefaultLabel(d)
  if d.instanceID then return format(L["Instance %s"], intText(d.instanceID)) end
  if d.subzone then return d.subzone end
  if d.areaID then
    local name = ns.AreaName and ns.AreaName(d.areaID)
    return nonEmpty(name) or format(L["Area %s"], intText(d.areaID))
  end
  if d.mapID then return mapName(d.mapID) or format(L["Zone %s"], intText(d.mapID)) end
  return L["Unknown place"]
end

-- ============================================================== validation ==

local SONG_KEYS = { "title", "artist", "spotify", "youtube", "file", "startMs", "durationMs", "volume", "tags", "note" }
local PLACE_KEYS = { "mapID", "areaID", "subzone", "instanceID" }
S.SONG_KEYS, S.PLACE_KEYS = SONG_KEYS, PLACE_KEYS

local function fieldFail(field, message)
  return nil, message, field
end

-- Normalized copy of a drop, or nil plus a player-facing message and the field name.
-- Accepts the SETS-FORMAT keys (place: mapID, areaID, subzone, instanceID; label; song: title,
-- artist, spotify (id, URI or link), youtube (id or link), startMs, durationMs, tags, note) and
-- the user keys (file, volume, disabled, basedOn, modified). Numbers may be digit strings.
-- false or "" leaves a field out. id and kind are always derived from the place.
-- opts.requireSong: a drop that is not turned off must have artist and title (share codes);
-- otherwise such a drop is a draft. opts.noLabel: do not invent a label when none is given.
function S.Validate(drop, opts)
  if type(drop) ~= "table" then return nil, L["A needle drop must be a table."], nil end
  opts = opts or {}
  local out = {}

  -- place
  for i = 1, 3 do
    local k = (i == 1 and "mapID") or (i == 2 and "areaID") or "instanceID"
    local v = drop[k]
    if given(v) then
      local n = checkInt(v, 1, MAX_INT)
      if not n then return fieldFail(k, format(L["%s must be a whole number from 1 to %d."], S.FieldName(k), MAX_INT)) end
      out[k] = n
    end
  end
  if given(drop.subzone) then
    local t, code = cleanText(drop.subzone, S.MAX.subzone, false)
    if not t then return fieldFail("subzone", S.TextError("subzone", code, S.MAX.subzone)) end
    if t ~= "" then out.subzone = t end
  end
  if not (out.mapID or out.areaID or out.instanceID) then
    if out.subzone then return fieldFail("mapID", L["A landmark needs a zone (or an area id)."]) end
    return fieldFail("mapID", L["A needle drop needs a place: a zone, a landmark or an instance."])
  end
  local id, iderr = S.DropIdFor(out)
  if not id then return fieldFail("mapID", iderr) end
  if sub(id, 1, 4) == "sub:" and sub(id, -1) == ":" then
    return fieldFail("subzone", L["This landmark name has no letters a-z or digits, so it can't be saved by name: stand in it so its area id is picked up, or use the whole zone."])
  end
  out.id = id
  out.kind = S.KindOfId(id)

  if given(drop.label) then
    local t, code = cleanText(drop.label, S.MAX.label, false)
    if not t then return fieldFail("label", S.TextError("label", code, S.MAX.label)) end
    if t ~= "" then out.label = t end
  end

  -- song
  local function text(k, multiline)
    local v = drop[k]
    if not given(v) then return true end
    local t, code = cleanText(v, S.MAX[k], multiline)
    if not t then return false, S.TextError(k, code, S.MAX[k]) end
    if t ~= "" then out[k] = t end
    return true
  end
  local ok, msg = text("title")
  if not ok then return fieldFail("title", msg) end
  ok, msg = text("artist")
  if not ok then return fieldFail("artist", msg) end
  ok, msg = text("note", true)
  if not ok then return fieldFail("note", msg) end

  if given(drop.spotify) then
    local sid, code = S.SpotifyId(drop.spotify)
    if not sid then
      if code == "notatrack" then
        return fieldFail("spotify", L["That's an album or playlist link: in Spotify right-click the song, then Share, then Copy Song Link."])
      end
      return fieldFail("spotify", L["That isn't a Spotify song link."])
    end
    out.spotify = sid
  end
  if given(drop.youtube) then
    local yid, code = S.YouTubeId(drop.youtube)
    if not yid then
      if code == "playlist" then return fieldFail("youtube", L["That's a YouTube playlist link: open the video and copy its own link."]) end
      return fieldFail("youtube", L["That isn't a YouTube video link."])
    end
    out.youtube = yid
  end
  if given(drop.file) then
    local f = S.FileName(drop.file)
    if not f then
      return fieldFail("file", L["Use a plain file name ending in .mp3 or .ogg (letters, digits, spaces and . _ - ( ) only), at most 100 bytes."])
    end
    out.file = f
  end
  for i = 1, 2 do
    local k = i == 1 and "startMs" or "durationMs"
    local v = drop[k]
    if v ~= nil and v ~= false and v ~= "" then
      local n = checkInt(v, 0, MAX_INT)
      if not n then return fieldFail(k, format(L["%s must be a whole number of milliseconds from 0 to %d."], S.FieldName(k), MAX_INT)) end
      out[k] = n
    end
  end
  if drop.volume ~= nil and drop.volume ~= false and drop.volume ~= "" then
    local n = checkInt(drop.volume, 0, 100)
    if not n then return fieldFail("volume", L["Volume must be a whole number from 0 to 100."]) end
    out.volume = n
  end
  if drop.tags ~= nil and drop.tags ~= false then
    local tags, terr = S.CleanTags(drop.tags)
    if tags == false then return fieldFail("tags", terr) end
    out.tags = tags
  end

  -- user state
  local disabled = drop.disabled
  if disabled == true then
    out.disabled = true
  elseif disabled ~= nil and disabled ~= false then
    return fieldFail("disabled", L["\"Turned off\" must be true or false."])
  end
  if S.IsSetId(drop.basedOn) then out.basedOn = drop.basedOn end
  if isInt(drop.modified) and drop.modified >= 0 then out.modified = drop.modified end

  if not out.disabled and not (out.artist and out.title) and opts.requireSong then
    if not out.artist then return fieldFail("artist", L["The artist is missing."]) end
    return fieldFail("title", L["The title is missing."])
  end
  if not out.label and not opts.noLabel then out.label = S.DefaultLabel(out) end
  return out
end

-- A draft: not turned off and missing its artist or title. Drafts never play.
function S.IsDraft(d)
  return type(d) == "table" and d.disabled ~= true and not (nonEmpty(d.artist) and nonEmpty(d.title))
end
local isDraft = S.IsDraft

local function posInt(v)
  if isInt(v) and v >= 1 then return v end
  return nil
end

-- Cheap structural check used while merging (full validation happens on every edit).
local function placeOk(d)
  if type(d) ~= "table" or type(d.id) ~= "string" or d.id == "" then return false end
  local k = d.kind
  if k == "zone" then return posInt(d.mapID) ~= nil end
  if k == "subzone" then
    return posInt(d.areaID) ~= nil or (posInt(d.mapID) ~= nil and type(d.subzone) == "string" and d.subzone ~= "")
  end
  if k == "instance" then return posInt(d.instanceID) ~= nil end
  return false
end

-- ==================================================================== refs ==

function S.ParseRef(ref)
  if type(ref) ~= "string" then return nil end
  local kind, id = match(ref, "^([a-z]+):(.+)$")
  if (kind == "builtin" or kind == "user") and ns.Sets.IsValidId(id) then return kind, id end
  return nil
end

function S.Ref(kind, id)
  return kind .. ":" .. id
end

local function userSets()
  local db = ns.db
  if type(db) == "table" and type(db.soundtracks) == "table" then return db.soundtracks end
  return nil
end

-- The stored table behind a ref (built-ins: the shared read-only table), or nil.
local function setTable(ref)
  local kind, id = S.ParseRef(ref)
  if kind == "builtin" then return ns.Sets.Internal(id), kind, id end
  if kind == "user" then
    local u = userSets()
    local set = u and u[id]
    if type(set) == "table" then return set, kind, id end
  end
  return nil
end

function S.Exists(ref)
  return setTable(ref) ~= nil
end

-- User soundtracks: the live SavedVariables table (do not change it; use this API).
-- Built-in soundtracks: a copy.
function S.Get(ref)
  local set, kind, id = setTable(ref)
  if not set then return nil end
  if kind == "builtin" then return ns.Sets.GetBuiltin(id) end
  return set
end

local function ready()
  return type(ns.db) == "table" and type(ns.cdb) == "table" and userSets() ~= nil
end

local NOT_READY = "NeedleDropper is still loading: try again in a moment."

local function findDrop(set, id)
  local drops = set and set.drops
  if type(drops) ~= "table" then return nil end
  for i = 1, #drops do
    local d = drops[i]
    if type(d) == "table" and d.id == id then return i, d end
  end
  return nil
end

function S.GetDrop(ref, id)
  local set, kind = setTable(ref)
  local _, d = findDrop(set, id)
  if not d then return nil end
  if kind == "builtin" then return copy(d) end
  return d
end

local function writableSet(ref)
  if not ready() then return nil, L[NOT_READY] end
  local kind = S.ParseRef(ref)
  if kind == "builtin" then return nil, L["Built-in soundtracks can't be changed: use Change song here, or Duplicate it."] end
  local set = setTable(ref)
  if not set then return nil, L["That soundtrack no longer exists."] end
  return set
end

local function countUserSets()
  local n = 0
  local u = userSets()
  if u then for _ in pairs(u) do n = n + 1 end end
  return n
end

local function countUserDrops()
  local n = 0
  local u = userSets()
  if u then
    for _, set in pairs(u) do
      if type(set) == "table" and type(set.drops) == "table" then n = n + #set.drops end
    end
  end
  return n
end
S.CountUserDrops = countUserDrops

-- Room for `extra` more drops in `set` (nil = a new soundtrack) and in total.
local function roomFor(set, extra)
  if set and #set.drops + extra > S.Limits.dropsPerSet then
    return nil, format(L["A soundtrack can hold up to %d drops."], S.Limits.dropsPerSet)
  end
  if countUserDrops() + extra > S.Limits.dropsTotal then
    return nil, format(L["You can have up to %d drops in your own soundtracks: delete some first."], S.Limits.dropsTotal)
  end
  return true
end

-- ============================================================= active list ==

local function defaultRefs()
  local id = ns.Sets.DefaultId()
  if id then return { "builtin:" .. id } end
  return {}
end
S.DefaultRefs = defaultRefs

-- Valid, de-duplicated refs of a stored list, and how many entries the list had.
local function filterRefs(list)
  local out, seen, listed = {}, {}, 0
  if type(list) == "table" then
    for i = 1, #list do
      local r = list[i]
      listed = listed + 1
      if type(r) == "string" and not seen[r] and S.Exists(r) then
        seen[r] = true
        out[#out + 1] = r
      end
    end
  end
  return out, listed
end

local function effectiveList(list)
  if type(list) ~= "table" then return filterRefs(defaultRefs()) end
  local out, listed = filterRefs(list)
  if #out == 0 and listed > 0 then return filterRefs(defaultRefs()) end
  return out
end

local function effectiveRefs()
  local cdb, db = ns.cdb, ns.db
  if type(cdb) == "table" and type(cdb.active) == "table" then return (effectiveList(cdb.active)) end
  return (effectiveList(type(db) == "table" and db.active or nil))
end

-- The active refs in priority order (a copy): the character's own list when it has one, else
-- the account list.
function S.ActiveRefs()
  return effectiveRefs()
end
ns.ActiveRefs = S.ActiveRefs

function S.UsesCharacterList()
  return type(ns.cdb) == "table" and type(ns.cdb.active) == "table"
end

local function writeActive(list)
  if S.UsesCharacterList() then ns.cdb.active = list else ns.db.active = list end
end

local function indexOf(list, v)
  for i = 1, #list do if list[i] == v then return i end end
  return nil
end

-- Puts ref into a list (a copy is returned): just below My Drops when My Drops is first,
-- else at the top.
local function insertOn(list, ref)
  local pos = 1
  if list[1] == MY_DROPS_REF and ref ~= MY_DROPS_REF then pos = 2 end
  tinsert(list, pos, ref)
  return list
end

-- Refs removed from a list; a list emptied by the removal falls back to the default list.
local function removeRef(list, ref)
  if type(list) ~= "table" then return list end
  local out, had = {}, false
  for i = 1, #list do
    if list[i] == ref then had = true else out[#out + 1] = list[i] end
  end
  if had and #out == 0 then out = defaultRefs() end
  return out
end

-- ================================================================== commits ==

local edits = 0
local mergedCache = nil
local listCache = nil
local rebuildToken = 0
local rebuildPending = false
local undoStack = {}
local migratedFrom = nil
local initializedFor = nil

local function touch(set)
  set.revision = (isInt(set.revision) and set.revision >= 0 and set.revision or 0) + 1
  set.modified = now()
end

local function commit(reason, ref)
  edits = edits + 1
  mergedCache = nil
  listCache = nil
  if ns.Fire then ns.Fire("ND_SOUNDTRACKS_CHANGED", reason, ref) end
  S.ScheduleRebuild()
end

-- Number of saved-data edits this session (the window offers "Save now" after 10).
function S.EditCount()
  return edits
end

-- ------------------------------------------------------------------- undo --

local function snapshot(id)
  local u = userSets()
  local s = u and u[id]
  if type(s) == "table" then return copy(s) end
  return false
end

-- Records the state of the soundtracks `ids` (and, with withActive, both active lists) before
-- an edit. full = restore the whole soundtrack on undo, not just its drops.
local function pushUndo(desc, ids, withActive, full)
  local rec = { desc = desc, sets = {}, full = full and true or false }
  for i = 1, #ids do
    local id = ids[i]
    if rec.sets[id] == nil then rec.sets[id] = snapshot(id) end
  end
  if withActive then
    rec.active = { account = copy(ns.db.active), character = (type(ns.cdb.active) == "table") and copy(ns.cdb.active) or false }
  end
  undoStack[#undoStack + 1] = rec
  while #undoStack > S.Limits.undo do tremove(undoStack, 1) end
  return rec
end

local function dropUndo(rec)
  if undoStack[#undoStack] == rec then undoStack[#undoStack] = nil end
end

-- A soundtrack id is being given to a new soundtrack: older undo steps about that id would
-- now restore the wrong thing, so they are forgotten.
local function purgeUndo(id)
  local keep = {}
  for i = 1, #undoStack do
    if undoStack[i].sets[id] == nil then keep[#keep + 1] = undoStack[i] end
  end
  undoStack = keep
end

function S.CanUndo()
  local rec = undoStack[#undoStack]
  if not rec then return false end
  return true, rec.desc
end

function S.Undo()
  if not ready() then return false, L[NOT_READY] end
  local rec = tremove(undoStack)
  if not rec then return false, L["Nothing to undo."] end
  local u = userSets()
  for id, snap in pairs(rec.sets) do
    local cur = u[id]
    if snap == false then
      u[id] = nil
    elseif rec.full or type(cur) ~= "table" then
      u[id] = snap
    else
      cur.drops = snap.drops
      touch(cur)
    end
  end
  if rec.active then
    ns.db.active = rec.active.account
    if rec.active.character then ns.cdb.active = rec.active.character else ns.cdb.active = nil end
  end
  commit("undo")
  return true, rec.desc
end

-- ================================================================ soundtracks ==

local function nameKey(name) return asciiLower(name) end

local function nameTaken(name, exceptRef)
  local key = nameKey(name)
  local ids = ns.Sets.Ids()
  for i = 1, #ids do
    local ref = "builtin:" .. ids[i]
    if ref ~= exceptRef and nameKey(ns.Sets.Internal(ids[i]).name) == key then return true end
  end
  local u = userSets()
  if u then
    for id, set in pairs(u) do
      if type(set) == "table" and "user:" .. id ~= exceptRef and type(set.name) == "string" and nameKey(set.name) == key then
        return true
      end
    end
  end
  return false
end

local function cutBytes(s, maxBytes)
  local P2 = ns.Protocol2
  if P2 and P2.Utf8Truncate then return P2.Utf8Truncate(s, maxBytes) end
  return sub(s, 1, maxBytes)
end

-- base, or "base (2)", "base (3)", ... that no soundtrack uses (within the 64-byte name cap).
local function uniqueName(base, exceptRef)
  if not nameTaken(base, exceptRef) then return base end
  for n = 2, 999 do
    local suffix = format(" (%d)", n)
    local cand = trimText(cutBytes(base, S.MAX.name - #suffix)) .. suffix
    if not nameTaken(cand, exceptRef) then return cand end
  end
  return base
end
S.UniqueName = uniqueName

local function idTaken(id, avoidMyDrops)
  if ns.Sets.Has(id) then return true end
  if avoidMyDrops and id == MY_DROPS then return true end
  local u = userSets()
  return u ~= nil and u[id] ~= nil
end

local function cutId(s, maxLen)
  s = sub(s, 1, maxLen)
  s = gsub(s, "%-+$", "")
  return s
end

-- A free soundtrack id built from `base` (a slug): base, base-2, base-3, ... (at most 40 chars).
-- Built-in ids are reserved; avoidMyDrops also reserves "my-drops" (imports never take it).
local function freeId(base, avoidMyDrops)
  base = cutId(S.Slugify(base or ""), ns.Sets.MAX_ID)
  if base == "" then base = "soundtrack" end
  if not idTaken(base, avoidMyDrops) then return base end
  for n = 2, 9999 do
    local suffix = "-" .. n
    local cand = cutId(base, ns.Sets.MAX_ID - #suffix) .. suffix
    if not idTaken(cand, avoidMyDrops) then return cand end
  end
  return nil
end
S.FreeId = freeId

local function checkName(name)
  local t, code = cleanText(name, S.MAX.name, false)
  if not t then return nil, S.TextError("name", code, S.MAX.name) end
  if t == "" then return nil, L["Give the soundtrack a name."] end
  return t
end

local function newSetTable(id, name)
  local t = now()
  return { id = id, name = name, author = "", description = "", tags = {}, revision = 0, created = t,
           modified = t, drops = {} }
end

-- Turns ref on in the active list in use (no commit).
local function turnOn(ref)
  local list = effectiveRefs()
  if indexOf(list, ref) then return false end
  writeActive(insertOn(list, ref))
  return true
end

-- New, empty soundtrack of your own, turned on (just below My Drops when My Drops is first).
-- Returns its ref, or nil plus a message.
function S.Create(name)
  if not ready() then return nil, L[NOT_READY] end
  local clean, err = checkName(name)
  if not clean then return nil, err end
  if nameTaken(clean) then return nil, format(L["You already have a soundtrack called %s."], clean) end
  if countUserSets() >= S.Limits.sets then
    return nil, format(L["You can have up to %d soundtracks of your own: delete one first."], S.Limits.sets)
  end
  local id = freeId(clean)
  if not id then return nil, L["No free soundtrack id is left for that name."] end
  purgeUndo(id)
  userSets()[id] = newSetTable(id, clean)
  local ref = "user:" .. id
  turnOn(ref)
  commit("create", ref)
  return ref
end

-- Full copy of a soundtrack (built-in: "<name> (my copy)"), turned on just above the others
-- (below My Drops). Built-in fixes in later addon versions do not reach a copy.
function S.Duplicate(ref, name)
  if not ready() then return nil, L[NOT_READY] end
  local src, kind = setTable(ref)
  if not src then return nil, L["That soundtrack no longer exists."] end
  local newName
  if name ~= nil and name ~= "" then
    local err
    newName, err = checkName(name)
    if not newName then return nil, err end
    if nameTaken(newName) then return nil, format(L["You already have a soundtrack called %s."], newName) end
  else
    local pattern = kind == "builtin" and L["%s (my copy)"] or L["%s (copy)"]
    local base = format(pattern, src.name or "")
    if #base > S.MAX.name then base = cutBytes(base, S.MAX.name) end
    newName = uniqueName(base)
  end
  if countUserSets() >= S.Limits.sets then
    return nil, format(L["You can have up to %d soundtracks of your own: delete one first."], S.Limits.sets)
  end
  local ok, err = roomFor(nil, #src.drops)
  if not ok then return nil, err end
  local id = freeId(newName)
  if not id then return nil, L["No free soundtrack id is left for that name."] end
  local set = newSetTable(id, newName)
  set.author = type(src.author) == "string" and src.author or ""
  set.description = type(src.description) == "string" and src.description or ""
  set.tags = copy(src.tags) or {}
  set.homepage = nonEmpty(src.homepage)
  for i = 1, #src.drops do
    local d = copy(src.drops[i])
    d.basedOn = nil
    set.drops[#set.drops + 1] = d
  end
  purgeUndo(id)
  userSets()[id] = set
  local newRef = "user:" .. id
  turnOn(newRef)
  commit("duplicate", newRef)
  return newRef
end

function S.Rename(ref, name)
  local set, err = writableSet(ref)
  if not set then return nil, err end
  local clean
  clean, err = checkName(name)
  if not clean then return nil, err end
  if nameTaken(clean, ref) then return nil, format(L["You already have a soundtrack called %s."], clean) end
  if clean == set.name then return true end
  set.name = clean
  touch(set)
  commit("rename", ref)
  return true
end

-- Author, description, tags and homepage of a soundtrack of your own (what a share code carries).
function S.SetDetails(ref, fields)
  local set, err = writableSet(ref)
  if not set then return nil, err end
  if type(fields) ~= "table" then return nil, L["Nothing to change."] end
  local new = {}
  if fields.author ~= nil then
    local t, code = cleanText(fields.author or "", S.MAX.author, false)
    if not t then return nil, S.TextError("author", code, S.MAX.author), "author" end
    new.author = t
  end
  if fields.description ~= nil then
    local t, code = cleanText(fields.description or "", S.MAX.description, true)
    if not t then return nil, S.TextError("description", code, S.MAX.description), "description" end
    new.description = t
  end
  if fields.tags ~= nil then
    local tags, terr = S.CleanTags(fields.tags or {})
    if tags == false then return nil, terr, "tags" end
    new.tags = tags or {}
  end
  if fields.homepage ~= nil then
    local h, herr = S.CleanHomepage(fields.homepage or "")
    if not h then return nil, herr, "homepage" end
    new.homepage = h
  end
  for k, v in pairs(new) do
    if k == "homepage" and v == "" then set.homepage = nil else set[k] = v end
  end
  touch(set)
  commit("details", ref)
  return true
end

-- Deletes a soundtrack of your own (undoable this session) and takes it out of the active
-- lists; a list it leaves empty falls back to the default soundtrack.
function S.Delete(ref)
  local set, err = writableSet(ref)
  if not set then return nil, err end
  local id = set.id
  pushUndo(format(L["Delete soundtrack %s"], set.name or id), { id }, true, true)
  userSets()[id] = nil
  ns.db.active = removeRef(ns.db.active, ref)
  if type(ns.cdb.active) == "table" then ns.cdb.active = removeRef(ns.cdb.active, ref) end
  commit("delete", ref)
  return true
end

-- Sets the active list in use (character list when the character has one). Every ref must
-- exist and appear once. An empty list is allowed: nothing plays.
function S.SetActive(refs)
  if not ready() then return nil, L[NOT_READY] end
  if type(refs) ~= "table" then return nil, L["The soundtrack list must be a list of soundtracks."] end
  local out, seen = {}, {}
  for i = 1, #refs do
    local r = refs[i]
    if not S.Exists(r) then return nil, format(L["There is no soundtrack %s."], tostring(r)) end
    if seen[r] then return nil, L["A soundtrack can only be in the list once."] end
    seen[r] = true
    out[#out + 1] = r
  end
  writeActive(out)
  commit("active")
  return true
end

-- Turns a soundtrack on (just below My Drops when My Drops is first, else at the top) or off.
function S.SetOn(ref, on)
  if not S.Exists(ref) then return nil, L["That soundtrack no longer exists."] end
  local list = effectiveRefs()
  local i = indexOf(list, ref)
  if on then
    if i then return true end
    insertOn(list, ref)
  else
    if not i then return true end
    tremove(list, i)
  end
  return S.SetActive(list)
end

-- Moves an active soundtrack up (delta < 0) or down (delta > 0) in the list.
function S.MoveActive(ref, delta)
  local list = effectiveRefs()
  local i = indexOf(list, ref)
  if not i then return nil, L["That soundtrack is not on."] end
  local j = i + (tonumber(delta) or 0)
  if j < 1 then j = 1 elseif j > #list then j = #list end
  if j == i then return true end
  tremove(list, i)
  tinsert(list, j, ref)
  return S.SetActive(list)
end

-- "Use different soundtracks on this character": on copies the account list to the
-- character; off goes back to the account list.
function S.SetUseCharacterList(on)
  if not ready() then return nil, L[NOT_READY] end
  if on then
    if S.UsesCharacterList() then return true end
    ns.cdb.active = effectiveList(ns.db.active)
  else
    if not S.UsesCharacterList() then return true end
    ns.cdb.active = nil
  end
  commit("character-list")
  return true
end

-- Drops refs to soundtracks that no longer exist from both stored lists (a list left with
-- nothing valid falls back to the default list). Runs at PLAYER_LOGOUT, before the game saves.
function S.Prune()
  if not ready() or S.newerData then return end
  local db, cdb = ns.db, ns.cdb
  db.active = effectiveList(db.active)
  if type(cdb.active) == "table" then cdb.active = effectiveList(cdb.active) end
end

-- ===================================================================== drops ==

function S.AddDrop(ref, drop)
  local set, err = writableSet(ref)
  if not set then return nil, err end
  local d, verr, field = S.Validate(drop)
  if not d then return nil, verr, field end
  if findDrop(set, d.id) then
    return nil, format(L["%s already has a needle drop for %s."], set.name, d.label), "mapID"
  end
  local ok, lerr = roomFor(set, 1)
  if not ok then return nil, lerr end
  pushUndo(format(L["Add %s"], d.label), { set.id })
  d.modified = now()
  set.drops[#set.drops + 1] = d
  touch(set)
  commit("add", ref)
  return d.id
end

local function placeChanged(fields)
  for i = 1, #PLACE_KEYS do
    if fields[PLACE_KEYS[i]] ~= nil then return true end
  end
  return false
end

-- Changes fields of a drop. fields[k] = false or "" clears k. Changing the place gives the
-- drop a new id (returned); heard-history stays with the old place. Returns true, id.
function S.UpdateDrop(ref, id, fields)
  local set, err = writableSet(ref)
  if not set then return nil, err end
  local i, old = findDrop(set, id)
  if not i then return nil, L["That needle drop no longer exists."] end
  if type(fields) ~= "table" then return nil, L["Nothing to change."] end
  local merged = copy(old)
  for k, v in pairs(fields) do
    if k ~= "id" and k ~= "kind" and k ~= "modified" then
      if v == false or v == "" then merged[k] = nil else merged[k] = v end
    end
  end
  local moved = placeChanged(fields)
  if moved and fields.label == nil then merged.label = nil end
  local d, verr, field = S.Validate(merged)
  if not d then return nil, verr, field end
  if d.id ~= old.id then
    if findDrop(set, d.id) then
      return nil, format(L["%s already has a needle drop for %s."], set.name, d.label), "mapID"
    end
    d.basedOn = nil
  end
  pushUndo(format(L["Change %s"], d.label), { set.id })
  d.modified = now()
  set.drops[i] = d
  touch(set)
  commit("update", ref)
  return true, d.id
end

function S.RemoveDrop(ref, id)
  local set, err = writableSet(ref)
  if not set then return nil, err end
  local i, d = findDrop(set, id)
  if not i then return nil, L["That needle drop no longer exists."] end
  pushUndo(format(L["Delete %s"], d.label or id), { set.id })
  tremove(set.drops, i)
  touch(set)
  commit("remove", ref)
  return true
end

-- Copies a drop (from any soundtrack) into a soundtrack of your own. Returns the new id.
function S.CopyDrop(fromRef, id, toRef)
  local target, err = writableSet(toRef)
  if not target then return nil, err end
  local src = setTable(fromRef)
  local _, d = findDrop(src, id)
  if not d then return nil, L["That needle drop no longer exists."] end
  local c = copy(d)
  c.basedOn = nil
  return S.AddDrop(toRef, c)
end

-- Moves a drop between two soundtracks of your own (one undo step).
function S.MoveDrop(fromRef, id, toRef)
  local from, err = writableSet(fromRef)
  if not from then return nil, err end
  local to
  to, err = writableSet(toRef)
  if not to then return nil, err end
  if from == to then return true, id end
  local i, d = findDrop(from, id)
  if not i then return nil, L["That needle drop no longer exists."] end
  if findDrop(to, id) then return nil, format(L["%s already has a needle drop for %s."], to.name, d.label or id) end
  if #to.drops + 1 > S.Limits.dropsPerSet then
    return nil, format(L["A soundtrack can hold up to %d drops."], S.Limits.dropsPerSet)
  end
  pushUndo(format(L["Move %s"], d.label or id), { from.id, to.id })
  tremove(from.drops, i)
  local m = copy(d)
  m.basedOn = nil
  m.modified = now()
  to.drops[#to.drops + 1] = m
  touch(from)
  touch(to)
  commit("move", toRef)
  return true, id
end

-- ================================================================= My Drops ==

-- The first drop for `id` among the active soundtracks (skipping skipRef) that plays a song,
-- else the first claim (a turned-off entry), else the first built-in that has it.
local function effectiveSource(id, skipRef, wantSong)
  local refs = effectiveRefs()
  local firstClaim, firstRef
  for r = 1, #refs do
    local ref = refs[r]
    if ref ~= skipRef then
      local _, d = findDrop(setTable(ref), id)
      if d and placeOk(d) and not isDraft(d) then
        if not d.disabled then return d, ref end
        if not firstClaim then firstClaim, firstRef = d, ref end
      end
    end
  end
  if firstClaim and not wantSong then return firstClaim, firstRef end
  local ids = ns.Sets.Ids()
  for i = 1, #ids do
    local _, d = findDrop(ns.Sets.Internal(ids[i]), id)
    if d then return d, "builtin:" .. ids[i] end
  end
  return firstClaim, firstRef
end

local function placeCopy(d)
  return { mapID = d.mapID, areaID = d.areaID, subzone = d.subzone, instanceID = d.instanceID, label = d.label }
end

local function songCopy(d, into)
  for i = 1, #SONG_KEYS do
    local k = SONG_KEYS[i]
    if d[k] ~= nil then into[k] = copy(d[k]) end
  end
  return into
end

local function applySongFields(into, fields)
  if type(fields) ~= "table" then return into end
  for i = 1, #SONG_KEYS do
    local k = SONG_KEYS[i]
    local v = fields[k]
    if v ~= nil then
      if v == false or v == "" then into[k] = nil else into[k] = v end
    end
  end
  return into
end

local function myDrops()
  local u = userSets()
  local set = u and u[MY_DROPS]
  if type(set) == "table" then return set end
  return nil
end

-- Creates My Drops (no commit). Call purgeUndo / pushUndo first.
local function createMyDrops()
  local set = newSetTable(MY_DROPS, uniqueName(L["My Drops"]))
  userSets()[MY_DROPS] = set
  return set
end

-- Moves My Drops to priority 1 of the list in use (no commit). True if the list changed.
local function myDropsOnTop()
  local list = effectiveRefs()
  if list[1] == MY_DROPS_REF then return false end
  local i = indexOf(list, MY_DROPS_REF)
  if i then tremove(list, i) end
  tinsert(list, 1, MY_DROPS_REF)
  writeActive(list)
  return true
end

local function needSetSlot()
  if not myDrops() and countUserSets() >= S.Limits.sets then
    return nil, format(L["You can have up to %d soundtracks of your own: delete one first."], S.Limits.sets)
  end
  return true
end

-- Writes `d` (validated) into My Drops, creating it and moving it to the top (one undo step).
local function writeMyDrop(desc, d, index)
  if not myDrops() then purgeUndo(MY_DROPS) end
  pushUndo(desc, { MY_DROPS }, true)
  local set = myDrops() or createMyDrops()
  myDropsOnTop()
  d.modified = now()
  if index then set.drops[index] = d else set.drops[#set.drops + 1] = d end
  touch(set)
  commit("mydrops", MY_DROPS_REF)
  return MY_DROPS_REF, d.id
end

-- "Change song here": copies the drop that plays at `id` into My Drops under the same id
-- (basedOn = the soundtrack it came from), applies the song `fields`, and makes sure My Drops
-- is on at priority 1 so the change wins. If My Drops already has the place, it just changes
-- that entry (and turns it back on). Returns "user:my-drops", id.
function S.OverrideHere(id, fields)
  if not ready() then return nil, L[NOT_READY] end
  local my = myDrops()
  local index, existing = findDrop(my, id)
  local merged
  if existing then
    merged = copy(existing)
  else
    local src, srcRef = effectiveSource(id, MY_DROPS_REF, true)
    if not src then return nil, L["There is no needle drop for that place."] end
    merged = songCopy(src, placeCopy(src))
    local _, srcId = S.ParseRef(srcRef)
    merged.basedOn = srcId
  end
  applySongFields(merged, fields)
  merged.disabled = nil
  local d, verr, field = S.Validate(merged)
  if not d then return nil, verr, field end
  if d.id ~= id then return nil, L["That needle drop no longer exists."] end
  if not existing then
    local ok, err = needSetSlot()
    if not ok then return nil, err end
    ok, err = roomFor(my, 1)
    if not ok then return nil, err end
  end
  return writeMyDrop(format(L["Change song here: %s"], d.label), d, index)
end

-- "Turn off here": writes { place, disabled = true } to My Drops (keeps the song of an
-- existing My Drops entry, so turning it on again restores it). Returns "user:my-drops", id.
function S.TurnOffHere(id)
  if not ready() then return nil, L[NOT_READY] end
  local my = myDrops()
  local index, existing = findDrop(my, id)
  local merged
  if existing then
    if existing.disabled then return MY_DROPS_REF, id end
    merged = copy(existing)
  else
    local src, srcRef = effectiveSource(id, MY_DROPS_REF, false)
    if not src then return nil, L["There is no needle drop for that place."] end
    merged = placeCopy(src)
    local _, srcId = S.ParseRef(srcRef)
    merged.basedOn = srcId
  end
  merged.disabled = true
  local d, verr, field = S.Validate(merged)
  if not d then return nil, verr, field end
  if not existing then
    local ok, err = needSetSlot()
    if not ok then return nil, err end
    ok, err = roomFor(my, 1)
    if not ok then return nil, err end
  end
  return writeMyDrop(format(L["Turn off %s"], d.label), d, index)
end

-- "Turn on": clears disabled on a drop of a soundtrack of your own (default My Drops). A My
-- Drops entry that only turned a place off (no song) is removed instead.
function S.TurnOnHere(id, ref)
  ref = ref or MY_DROPS_REF
  local set, err = writableSet(ref)
  if not set then return nil, err end
  local i, d = findDrop(set, id)
  if not i then return nil, L["That needle drop no longer exists."] end
  if not d.disabled then return true end
  pushUndo(format(L["Turn on %s"], d.label or id), { set.id })
  if ref == MY_DROPS_REF and not (nonEmpty(d.artist) and nonEmpty(d.title)) then
    tremove(set.drops, i)
  else
    local c = copy(d)
    c.disabled = nil
    c.modified = now()
    set.drops[i] = c
  end
  touch(set)
  commit("turnon", ref)
  return true
end

-- "Revert": removes the My Drops entry for `id`, so the soundtrack below plays again.
function S.Revert(id)
  local my = myDrops()
  if not my then return nil, L["My Drops has no change for that place."] end
  local i, d = findDrop(my, id)
  if not i then return nil, L["My Drops has no change for that place."] end
  pushUndo(format(L["Revert %s"], d.label or id), { MY_DROPS })
  tremove(my.drops, i)
  touch(my)
  commit("revert", MY_DROPS_REF)
  return true
end

-- What Revert would bring back for `id`: ref and name of the soundtrack below My Drops.
function S.RevertTarget(id)
  local src, ref = effectiveSource(id, MY_DROPS_REF, false)
  if not src then return nil end
  local set = setTable(ref)
  return ref, set and set.name
end

-- ============================================================= add drop here ==

-- Best-effort areaID of the landmark the player stands in: the explored areas at the player's
-- position whose name is the current subzone text (C_MapExplorationInfo /
-- C_Map.GetPlayerMapPosition / C_Map.GetAreaInfo, all guarded). nil when unknown.
function S.CurrentAreaID(mapID, subzone, rawMapID)
  local Loc = ns.Location
  if Loc and type(Loc.CurrentAreaID) == "function" and Loc.CurrentAreaID ~= S.CurrentAreaID then
    return Loc.CurrentAreaID(mapID, subzone, rawMapID)
  end
  if type(subzone) ~= "string" or subzone == "" then return nil end
  if not (C_Map and C_Map.GetPlayerMapPosition and C_MapExplorationInfo
          and C_MapExplorationInfo.GetExploredAreaIDsAtPosition) then
    return nil
  end
  local want = asciiLower(subzone)
  local maps = { mapID }
  if rawMapID and rawMapID ~= mapID then maps[2] = rawMapID end
  for m = 1, #maps do
    local okPos, pos = pcall(C_Map.GetPlayerMapPosition, maps[m], "player")
    if okPos and pos ~= nil and not (ns.IsSecret and ns.IsSecret(pos)) then
      local okIds, ids = pcall(C_MapExplorationInfo.GetExploredAreaIDsAtPosition, maps[m], pos)
      if okIds and type(ids) == "table" then
        for i = 1, #ids do
          local a = ns.SafeNumber and ns.SafeNumber(ids[i]) or (type(ids[i]) == "number" and ids[i] or nil)
          local name = a and ns.AreaName and ns.AreaName(a)
          if name and asciiLower(name) == want then return a end
        end
      end
    end
  end
  return nil
end

-- The place the player stands in, as a drop place:
--   { kind, id, mapID, areaID, subzone, instanceID, label, zoneName, byName = true (a landmark
--     saved by name), landmarkSkipped = "<name>" (a landmark that could only be saved as the
--     whole zone) }
-- or nil plus a message. Instance > landmark (subzone text other than the zone name) > zone.
function S.PlaceHere()
  local Loc = ns.Location
  if not (Loc and Loc.Snapshot) then return nil, L["Your location isn't known yet."] end
  local snap = Loc.Snapshot()
  if snap.inInstance then
    local inst = posInt(snap.instanceID)
    if not inst then return nil, L["NeedleDropper can't tell which instance this is."] end
    local place = { kind = "instance", instanceID = inst, mapID = posInt(snap.zoneMapID),
                    label = nonEmpty(snap.instanceName) or nonEmpty(snap.zoneName) or format(L["Instance %s"], intText(inst)) }
    place.id = S.DropIdFor(place)
    return place
  end
  local zone = posInt(snap.zoneMapID)
  if not zone then return nil, L["You aren't in a zone that can have a needle drop."] end
  local zoneName = nonEmpty(snap.zoneName) or mapName(zone) or format(L["Zone %s"], intText(zone))
  local subzone = nonEmpty(snap.subzone)
  if subzone and asciiLower(subzone) ~= asciiLower(snap.zoneName or "") then
    local areaID = S.CurrentAreaID(zone, subzone, snap.rawMapID)
    if areaID then
      local place = { kind = "subzone", mapID = zone, areaID = areaID, subzone = subzone, label = subzone, zoneName = zoneName }
      place.id = S.DropIdFor(place)
      return place
    end
    if S.Slugify(subzone) ~= "" then
      local place = { kind = "subzone", mapID = zone, subzone = subzone, label = subzone, zoneName = zoneName, byName = true }
      place.id = S.DropIdFor(place)
      return place
    end
    local place = { kind = "zone", mapID = zone, label = zoneName, zoneName = zoneName, landmarkSkipped = subzone }
    place.id = S.DropIdFor(place)
    return place
  end
  local place = { kind = "zone", mapID = zone, label = zoneName, zoneName = zoneName }
  place.id = S.DropIdFor(place)
  return place
end

-- Keeps a drop the player is creating where they stand from firing in the middle of editing,
-- for this visit only (the /needle stop mechanism: Director.suppress). Uses
-- Director.SuppressHere(id) when the Director provides it.
function S.Suppress(id, kind)
  local D = ns.Director
  if type(D) ~= "table" or type(id) ~= "string" then return false end
  if type(D.SuppressHere) == "function" then
    D.SuppressHere(id)
    return true
  end
  if type(D.suppress) == "table" and type(D.EntryTime) == "function" then
    D.suppress[id] = D.EntryTime({ id = id, kind = kind or S.KindOfId(id) })
    return true
  end
  return false
end

-- The drop that already covers `place` in the active mix (same id, or for a landmark one that
-- matches the landmark by name), from the current map.
local function coveringDrop(place)
  if rebuildPending then S.BuildActiveMap() end
  local M = ns.Map
  if type(M) ~= "table" or type(M.byId) ~= "table" then return nil end
  local d = M.byId[place.id]
  if d then return d end
  if place.kind == "subzone" and ns.FindSubzoneDrop and place.subzone then
    return ns.FindSubzoneDrop(place.mapID, lower(place.subzone))
  end
  return nil
end

-- "Add drop here" (button, /needle add, key binding). ref: the soundtrack of your own to add
-- to (default My Drops, created and turned on if needed); fields: optional song fields (see
-- ParseSongText). If the target soundtrack or the active mix already covers the place, that
-- drop is returned instead (and given the fields: a built-in drop through OverrideHere).
-- The drop is suppressed for this visit either way. Returns ref, id, existed (bool).
function S.AddHere(ref, fields)
  if not ready() then return nil, L[NOT_READY] end
  ref = ref or MY_DROPS_REF
  local kind = S.ParseRef(ref)
  if kind ~= "user" then return nil, L["Built-in soundtracks can't be changed: use Change song here, or Duplicate it."] end
  local target = setTable(ref)
  if not target and ref ~= MY_DROPS_REF then return nil, L["That soundtrack no longer exists."] end
  local place, perr = S.PlaceHere()
  if not place then return nil, perr end
  local hasSong = type(fields) == "table" and next(fields) ~= nil

  if target and findDrop(target, place.id) then
    if hasSong then
      local ok, err, field = S.UpdateDrop(ref, place.id, fields)
      if not ok then return nil, err, field end
    end
    S.Suppress(place.id, place.kind)
    return ref, place.id, true
  end
  local cover = coveringDrop(place)
  if cover then
    local coverRef, coverId = cover.setRef, cover.id
    if hasSong then
      if cover.builtin then
        local r, i, field = S.OverrideHere(coverId, fields)
        if not r then return nil, i, field end
        coverRef = r
      else
        local ok, err, field = S.UpdateDrop(coverRef, coverId, fields)
        if not ok then return nil, err, field end
      end
    end
    S.Suppress(coverId, cover.kind)
    return coverRef, coverId, true
  end

  local drop = placeCopy(place)
  applySongFields(drop, fields)
  local d, verr, field = S.Validate(drop)
  if not d then return nil, verr, field end
  if target then
    local id, err = S.AddDrop(ref, d)
    if not id then return nil, err end
    S.Suppress(id, d.kind)
    return ref, id, false
  end
  -- My Drops does not exist yet: create it, turned on at the top, in the same undo step.
  local ok, err = needSetSlot()
  if not ok then return nil, err end
  ok, err = roomFor(nil, 1)
  if not ok then return nil, err end
  purgeUndo(MY_DROPS)
  pushUndo(format(L["Add %s"], d.label), { MY_DROPS }, true)
  local set = createMyDrops()
  myDropsOnTop()
  d.modified = now()
  set.drops[1] = d
  touch(set)
  commit("add", MY_DROPS_REF)
  S.Suppress(d.id, d.kind)
  return MY_DROPS_REF, d.id, false
end

local SEPARATORS = { " - ", " \226\128\147 ", " \226\128\148 " } -- hyphen, en dash, em dash

-- Song fields from "/needle add" text: "Artist - Title", a Spotify song link / URI or a YouTube
-- link ({} for empty text). nil plus a message for anything else.
function S.ParseSongText(text)
  if type(text) ~= "string" then return {} end
  local t = cleanText(text, nil, false)
  if not t or t == "" then return {} end
  local l = asciiLower(t)
  if find(l, "spotify", 1, true) then
    local id, code = S.SpotifyId(t)
    if id then return { spotify = id } end
    if code == "notatrack" then
      return nil, L["That's an album or playlist link: in Spotify right-click the song, then Share, then Copy Song Link."]
    end
    return nil, L["That isn't a Spotify song link."]
  end
  if find(l, "youtu", 1, true) then
    local id, code = S.YouTubeId(t)
    if id then return { youtube = id, startMs = S.YouTubeStartMs(t) } end
    if code == "playlist" then return nil, L["That's a YouTube playlist link: open the video and copy its own link."] end
    return nil, L["That isn't a YouTube video link."]
  end
  -- Split at the earliest separator of any kind: "Sigur Rós — Hoppípolla - Live".
  local bestS, bestE
  for i = 1, #SEPARATORS do
    local s, e = find(t, SEPARATORS[i], 1, true)
    if s and (not bestS or s < bestS) then bestS, bestE = s, e end
  end
  if bestS then
    local artist, title = trimText(sub(t, 1, bestS - 1)), trimText(sub(t, bestE + 1))
    if artist ~= "" and title ~= "" then
      if #artist > S.MAX.artist then return nil, S.TextError("artist", "long", S.MAX.artist) end
      if #title > S.MAX.title then return nil, S.TextError("title", "long", S.MAX.title) end
      return { artist = artist, title = title }
    end
  end
  return nil, L["Type Artist - Title, or paste a Spotify or YouTube song link."]
end

-- ================================================================== merging ==

local function str(v)
  if type(v) == "string" and v ~= "" then return v end
  return nil
end

local function nonNegInt(v)
  if isInt(v) and v >= 0 then return v end
  return nil
end

local function songKey(m)
  return concat({ m.spotify or "", m.youtube or "", m.artist, m.title, m.startMs, m.durationMs,
                  m.fileName or "", m.volume or "" }, "\31")
end

local function mapDrop(d, ref, setId, setName, builtin)
  local subzone = str(d.subzone)
  local fileName = (not builtin) and str(d.file) or nil
  local volume = nil
  if not builtin and isInt(d.volume) and d.volume >= 0 and d.volume <= 100 then volume = d.volume end
  local m = {
    id = d.id, kind = d.kind,
    mapID = posInt(d.mapID), areaID = posInt(d.areaID), instanceID = posInt(d.instanceID),
    subzone = subzone, subzoneLower = subzone and lower(subzone) or nil,
    label = str(d.label) or subzone or d.id,
    title = str(d.title) or "", artist = str(d.artist) or "",
    startMs = nonNegInt(d.startMs) or 0, durationMs = nonNegInt(d.durationMs) or 0,
    spotify = str(d.spotify), youtube = str(d.youtube), volume = volume,
    file = fileName and (S.MEDIA_PATH .. fileName) or nil, fileName = fileName,
    tags = type(d.tags) == "table" and copy(d.tags) or nil, note = str(d.note),
    setRef = ref, setId = setId, setName = setName, builtin = builtin,
  }
  m.songKey = songKey(m)
  return m
end

local function index(M, m)
  if m.kind == "zone" then
    if m.mapID and not M.zoneDrops[m.mapID] then M.zoneDrops[m.mapID] = m end
  elseif m.kind == "subzone" then
    if m.mapID then
      local list = M.subDrops[m.mapID]
      if not list then
        list = {}
        M.subDrops[m.mapID] = list
      end
      list[#list + 1] = m
    else
      M.looseSubDrops[#M.looseSubDrops + 1] = m
    end
    if m.areaID and not M.areaDrops[m.areaID] then M.areaDrops[m.areaID] = m end
  elseif m.kind == "instance" then
    if m.instanceID and not M.instanceDrops[m.instanceID] then M.instanceDrops[m.instanceID] = m end
  end
end

local MAX_PROBLEMS = 20

-- Merges the active soundtracks into ns.Map (see the header), fires ND_MAP_CHANGED(map) and
-- returns the map. Cancels a pending debounced rebuild.
function S.BuildActiveMap()
  rebuildToken = rebuildToken + 1
  rebuildPending = false
  local M = {
    loaded = true, version = 2, hash = 0, generated = "", packs = {}, refs = {}, drops = {}, byId = {},
    byN = {}, zoneDrops = {}, subDrops = {}, looseSubDrops = {}, areaDrops = {}, instanceDrops = {},
    problems = {}, invalid = 0, overridden = 0, disabled = 0, drafts = 0,
  }
  local refs = effectiveRefs()
  local claimed = {}
  for r = 1, #refs do
    local ref = refs[r]
    local set, kind, setId = setTable(ref)
    M.refs[#M.refs + 1] = ref
    M.packs[#M.packs + 1] = str(set.name) or setId
    local builtin = kind == "builtin"
    local drops = type(set.drops) == "table" and set.drops or {}
    for i = 1, #drops do
      local d = drops[i]
      if not builtin and not placeOk(d) then
        M.invalid = M.invalid + 1
        if #M.problems < MAX_PROBLEMS then
          M.problems[#M.problems + 1] = format("%s drop %d: %s", ref, i,
            type(d) == "table" and tostring(d.id) or "not a table")
        end
      elseif isDraft(d) then
        M.drafts = M.drafts + 1
      elseif claimed[d.id] then
        M.overridden = M.overridden + 1
      else
        claimed[d.id] = ref
        if d.disabled == true then
          M.disabled = M.disabled + 1
        else
          local m = mapDrop(d, ref, setId, set.name, builtin)
          local n = #M.drops + 1
          m.n = n
          M.drops[n] = m
          M.byId[m.id] = m
          M.byN[n] = m
          index(M, m)
        end
      end
    end
  end
  ns.Map = M
  if ns.Fire then ns.Fire("ND_MAP_CHANGED", M) end
  return M
end
S.Rebuild = S.BuildActiveMap

-- Rebuilds ns.Map REBUILD_DELAY seconds after the last call (debounced).
function S.ScheduleRebuild()
  rebuildPending = true
  rebuildToken = rebuildToken + 1
  local token = rebuildToken
  if C_Timer and C_Timer.After then
    C_Timer.After(S.REBUILD_DELAY, function()
      if token == rebuildToken then S.BuildActiveMap() end
    end)
  else
    S.BuildActiveMap()
  end
end

function S.RebuildPending()
  return rebuildPending
end

local function shallow(list)
  local out = {}
  for i = 1, #list do out[i] = list[i] end
  return out
end

-- What plays, by place: one entry per place the active soundtracks claim -- the claiming
-- entry (a drop that plays, or a "turned off here" entry) -- in priority order, then each
-- soundtrack's own order. Drafts claim nothing and are not listed. A new array each call; the
-- entries are shared until the next edit, so treat them as read-only:
--   { drop = <copy>, id, setRef, setId, setName, builtin, priority (1 = top),
--     disabled = bool (turned off here: nothing plays), plays = not disabled,
--     overrides = { refs of lower active soundtracks whose drop for this place it hides },
--     overriddenBy = {} (what plays is never overridden; kept for the contract's shape) }
-- A soundtrack's drop is "overridden" when Merged() has its place from another soundtrack.
function S.Merged()
  if mergedCache then return shallow(mergedCache) end
  local refs = effectiveRefs()
  local out, byId = {}, {}
  for r = 1, #refs do
    local ref = refs[r]
    local set, kind, setId = setTable(ref)
    local drops = type(set.drops) == "table" and set.drops or {}
    local seen = {}
    for i = 1, #drops do
      local d = drops[i]
      if placeOk(d) and not isDraft(d) and not seen[d.id] then
        seen[d.id] = true
        local e = byId[d.id]
        if e then
          e.overrides[#e.overrides + 1] = ref
        else
          e = { drop = copy(d), id = d.id, setRef = ref, setId = setId, setName = set.name,
                builtin = kind == "builtin", priority = r, disabled = d.disabled == true,
                plays = d.disabled ~= true, overrides = {}, overriddenBy = {} }
          byId[d.id] = e
          out[#out + 1] = e
        end
      end
    end
  end
  mergedCache = out
  return shallow(out)
end

-- The entry that claims place `id` in the active mix (what plays, or a turned-off entry):
-- drop (read-only), ref; nil if no active soundtrack has the place.
function S.Winner(id)
  local refs = effectiveRefs()
  for r = 1, #refs do
    local _, d = findDrop(setTable(refs[r]), id)
    if d and placeOk(d) and not isDraft(d) then return d, refs[r] end
  end
  return nil
end

-- Every soundtrack, built-ins first (registration order), then your own by name:
--   { ref, id, name, author, description, tags, homepage, builtin, myDrops, active,
--     priority (1 = top, nil if off), dropCount (entries), places (entries that can play),
--     drafts, covered (places covered by a soundtrack above), revision }
function S.List()
  if listCache then return shallow(listCache) end
  local refs = effectiveRefs()
  local prio = {}
  for r = 1, #refs do prio[refs[r]] = r end
  local covered = {}
  if #refs > 0 then
    local merged = S.Merged()
    for i = 1, #merged do
      local lower = merged[i].overrides
      for k = 1, #lower do covered[lower[k]] = (covered[lower[k]] or 0) + 1 end
    end
  end
  local function entry(ref, set, builtin)
    local places, drafts = 0, 0
    for i = 1, #set.drops do
      local d = set.drops[i]
      if isDraft(d) then drafts = drafts + 1 elseif d.disabled ~= true then places = places + 1 end
    end
    return {
      ref = ref, id = set.id, name = set.name, author = set.author or "", description = set.description or "",
      tags = copy(set.tags) or {}, homepage = set.homepage, builtin = builtin, myDrops = ref == MY_DROPS_REF,
      active = prio[ref] ~= nil, priority = prio[ref], dropCount = #set.drops, places = places, drafts = drafts,
      covered = covered[ref] or 0, revision = set.revision or 0,
    }
  end
  local out = {}
  local ids = ns.Sets.Ids()
  for i = 1, #ids do
    out[#out + 1] = entry("builtin:" .. ids[i], ns.Sets.Internal(ids[i]), true)
  end
  local mine = {}
  local u = userSets()
  if u then
    for id, set in pairs(u) do
      if type(set) == "table" and type(set.drops) == "table" then mine[#mine + 1] = entry("user:" .. id, set, false) end
    end
  end
  sort(mine, function(a, b)
    local ka, kb = nameKey(a.name or ""), nameKey(b.name or "")
    if ka ~= kb then return byteLess(ka, kb) end
    return byteLess(a.id, b.id)
  end)
  for i = 1, #mine do out[#out + 1] = mine[i] end
  listCache = out
  return shallow(out)
end

-- =========================================================== history rules ==

-- Exploration history (historyMode = respect) may mark a drop as heard only when it comes
-- from a built-in soundtrack: a drop the player made plays on the next arrival even where the
-- character explored long ago.
function S.HistoryApplies(drop)
  if type(drop) ~= "table" then return false end
  if drop.builtin ~= nil then return drop.builtin == true end
  local M = ns.Map
  local m = type(M) == "table" and type(M.byId) == "table" and M.byId[drop.id] or nil
  return m ~= nil and m.builtin == true
end

-- Whether a done record (cdb.done[id] / db.accountDone[id]) counts for `drop`: records made
-- by the history scan (how = "history") only count for built-in drops.
function S.DoneCounts(drop, record)
  if record == nil or record == false then return false end
  if type(record) == "table" and record.how == "history" then return S.HistoryApplies(drop) end
  return true
end

-- Zone drops the history scan should look at: built-in ones only.
function S.HistoryZoneDrops()
  local out = {}
  local M = ns.Map
  local drops = type(M) == "table" and M.drops or {}
  for i = 1, #drops do
    local d = drops[i]
    if d.builtin and d.kind == "zone" then out[#out + 1] = d end
  end
  return out
end

-- ============================================================ init / migration ==

-- Schema 1 -> 2 for the account table (idempotent). Returns the schema it migrated from, or
-- nil. Settings are kept; beaconCellPx 4 (the v1 default) becomes 3 and values outside 2..4
-- are clamped. v1.1 kept no active list, so every account starts on the default soundtrack.
-- (Call it before Core's settings validation clamps beaconCellPx for exact v1 semantics.)
function S.MigrateAccount(db)
  if type(db) ~= "table" then return nil end
  local from = tonumber(db.schema) or 1
  if from > S.SCHEMA then
    S.newerData = true
    return nil
  end
  if from >= S.SCHEMA then return nil end
  local s = db.settings
  if type(s) == "table" and type(s.beaconCellPx) == "number" then
    local v = s.beaconCellPx
    if v ~= v then v = 3 end
    if v == 4 then v = 3 end
    v = floor(v + 0.5)
    if v < 2 then v = 2 elseif v > 4 then v = 4 end
    s.beaconCellPx = v
  end
  if type(db.active) ~= "table" then db.active = defaultRefs() end
  db.schema = S.SCHEMA
  return from
end

function S.MigrateCharacter(cdb)
  if type(cdb) ~= "table" then return nil end
  local from = tonumber(cdb.schema) or 1
  if from >= S.SCHEMA then return nil end
  cdb.schema = S.SCHEMA
  return from
end

local function stringList(list)
  local out, seen = {}, {}
  for i = 1, #list do
    local r = list[i]
    if type(r) == "string" and not seen[r] then
      seen[r] = true
      out[#out + 1] = r
    end
  end
  return out
end

local function normalizeSet(id, set)
  set.id = id
  if type(set.name) ~= "string" or set.name == "" then set.name = id end
  if type(set.author) ~= "string" then set.author = "" end
  if type(set.description) ~= "string" then set.description = "" end
  if type(set.tags) ~= "table" then set.tags = {} end
  if set.homepage ~= nil and type(set.homepage) ~= "string" then set.homepage = nil end
  if not (isInt(set.revision) and set.revision >= 0) then set.revision = 0 end
  if type(set.created) ~= "number" then set.created = 0 end
  if type(set.modified) ~= "number" then set.modified = 0 end
  if type(set.drops) ~= "table" then
    set.drops = {}
  else
    local clean = {}
    for i = 1, #set.drops do
      if type(set.drops[i]) == "table" then clean[#clean + 1] = set.drops[i] end
    end
    if #clean ~= #set.drops then set.drops = clean end
  end
end

local function normalize(db, cdb)
  if type(db.soundtracks) ~= "table" then db.soundtracks = {} end
  if type(db.importsHandled) ~= "table" then db.importsHandled = {} end
  if S.newerData then return end
  local bad = {}
  for id, set in pairs(db.soundtracks) do
    if not ns.Sets.IsValidId(id) or type(set) ~= "table" then bad[#bad + 1] = id else normalizeSet(id, set) end
  end
  for i = 1, #bad do db.soundtracks[bad[i]] = nil end
  if type(db.active) ~= "table" then db.active = defaultRefs() else db.active = stringList(db.active) end
  if cdb.active ~= nil then
    if type(cdb.active) ~= "table" then cdb.active = nil else cdb.active = stringList(cdb.active) end
  end
end

-- ND_INIT: migrates ns.db / ns.cdb to schema 2, repairs what is malformed, builds ns.Map.
-- Idempotent for the same SavedVariables tables.
function S.Init()
  local db, cdb = ns.db, ns.cdb
  if type(db) ~= "table" or type(cdb) ~= "table" then return false end
  if initializedFor == db then return true end
  initializedFor = db
  S.newerData = nil
  migratedFrom = S.MigrateAccount(db)
  S.MigrateCharacter(cdb)
  normalize(db, cdb)
  undoStack = {}
  edits = 0
  mergedCache = nil
  listCache = nil
  S.BuildActiveMap()
  return true
end

-- The schema this session migrated the account data from (1), or nil.
function S.MigratedFrom()
  return migratedFrom
end

-- The first-login line for 1.2 (DESIGN 4.8), naming the active soundtrack(s).
function S.WelcomeLine()
  local refs = effectiveRefs()
  local names = {}
  for i = 1, #refs do
    local set = setTable(refs[i])
    names[#names + 1] = set and set.name or refs[i]
  end
  if #names == 0 then
    return L["NeedleDropper 1.2: no soundtrack is on. Type /needle to pick one."]
  end
  return format(L["NeedleDropper 1.2: your soundtrack is %s. Type /needle to change a song or pick another soundtrack."],
    concat(names, ", "))
end

-- ------------------------------------------------- import support (Share.lua) --

-- New soundtrack of your own from already validated parts (Share.Import): fields = { name,
-- author, description, tags, homepage, revision, packId, packRevision, packName, imported },
-- drops = validated drops. Never overwrites (a taken id gets -2, -3 ...; "my-drops" and
-- built-in ids are reserved; a taken name gets " (2)" ...) and never turns it on.
function S.CreateFromParts(fields, drops)
  if not ready() then return nil, L[NOT_READY] end
  if countUserSets() >= S.Limits.sets then
    return nil, format(L["You can have up to %d soundtracks of your own: delete one first."], S.Limits.sets)
  end
  if #drops > S.Limits.dropsPerSet then return nil, format(L["A soundtrack can hold up to %d drops."], S.Limits.dropsPerSet) end
  local ok, err = roomFor(nil, #drops)
  if not ok then return nil, err end
  local id = freeId(fields.packId or fields.name, true)
  if not id then return nil, L["No free soundtrack id is left for that name."] end
  local set = newSetTable(id, uniqueName(fields.name))
  set.author = fields.author or ""
  set.description = fields.description or ""
  set.tags = copy(fields.tags) or {}
  set.homepage = nonEmpty(fields.homepage)
  set.revision = fields.revision or 0
  set.packId = fields.packId
  set.packRevision = fields.packRevision
  set.packName = fields.packName
  set.imported = fields.imported
  for i = 1, #drops do set.drops[i] = drops[i] end
  purgeUndo(id)
  pushUndo(format(L["Import %s"], set.name), { id })
  userSets()[id] = set
  local ref = "user:" .. id
  commit("import", ref)
  return ref
end

-- Replaces a soundtrack of your own with validated parts (Share.Update): keeps its id, its
-- place and on/off state in the active lists, and its name if you renamed it.
function S.ReplaceFromParts(ref, fields, drops)
  local set, err = writableSet(ref)
  if not set then return nil, err end
  if #drops > S.Limits.dropsPerSet then return nil, format(L["A soundtrack can hold up to %d drops."], S.Limits.dropsPerSet) end
  local ok, rerr = roomFor(nil, #drops - #set.drops)
  if not ok and #drops > #set.drops then return nil, rerr end
  pushUndo(format(L["Update %s"], set.name), { set.id }, false, true)
  if fields.name and (set.packName == nil or set.name == set.packName) then
    set.name = uniqueName(fields.name, ref)
  end
  set.author = fields.author or set.author
  set.description = fields.description or ""
  set.tags = copy(fields.tags) or {}
  set.homepage = nonEmpty(fields.homepage)
  set.packId = fields.packId or set.packId
  set.packRevision = fields.packRevision
  set.packName = fields.packName
  set.revision = fields.revision or set.revision
  set.modified = now()
  set.updated = fields.imported
  local list = {}
  for i = 1, #drops do list[i] = drops[i] end
  set.drops = list
  commit("update-import", ref)
  return true
end

-- ===================================================================== events ==

if ns.On then
  ns.On("ND_INIT", function() S.Init() end)
  ns.On("PLAYER_LOGOUT", function() S.Prune() end)
end

return S
