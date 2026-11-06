local ADDON, ns = ...
-- NeedleDropper wire protocol v2 (docs/PROTOCOL-v2.md): the frame that carries the SONG
-- IDENTITY from the addon to the companion, its TLV body, the multi-row pixel-beacon cell
-- layout and the chat-log line. Shared contract with companion/needledropper/protocol2.py;
-- tests/test_protocol2_contract.py checks both implementations byte for byte.
--
-- Pure Lua 5.1 arithmetic: no bit library, no utf8 library, no goto, no integer division.
-- Every function is total: bad input is dropped or clamped, never raised, because this code
-- runs inside the game client.
--
-- Frame bytes (docs/PROTOCOL-v2.md section 2):
--   [1] 0x4E 'N'   [2] 0x20 + type   [3] seq   [4] flags   [5] bodyLen
--   [6 .. 5+bodyLen] TLVs: tag (uint8), len (uint8), value -- ascending tags, absent omitted
--   then Fletcher-16 of everything before it: s2, s1
--
-- Event table accepted by EncodeFrame / EncodeCells / FormatChatLine:
--   { type = P2.EV.FIRE, seq = 7, flags = 0x20,
--     dropId = "zone:1429",            -- placeKey (Fletcher-16 of dropId) is derived from it
--     placeKey = nil,                  -- optional explicit uint16 override
--     spotify = "spotify:track:<id>" | "https://open.spotify.com/track/<id>?si=.." | "<id>",
--     youtube = "https://youtu.be/<id>" | "...watch?v=<id>" | "<id>",
--     artist = "Low Roar", title = "I'll Keep Coming",
--     startMs = 0 | startDs = 0,       -- start offset (ms, or deciseconds)
--     lengthMs = 0 | lengthDs = 0,     -- play length, 0 / absent = whole song
--     volume = 80,                     -- 0..100
--     label = "Elwynn Forest",         -- place label for the player card
--     setId = "azeroth-mixtape",       -- soundtrack set id
--     prefer = "spotify" | "youtube" } -- not sent: the id the fit rule keeps last (default youtube)
-- Numeric fields must be numbers; anything else counts as absent.
--
-- Fit rule (DESIGN-v1.2 2.3): EncodeFrame(ev, maxBytes, prefer) keeps the frame within
-- maxBytes (default and maximum MAX_FRAME = 262, i.e. a body of at most 255 bytes) by
-- removing, in this order and only while it is still too long: 1 setId, 2 label, 3 query
-- (when a Spotify or YouTube id is present), 4 dropId (placeKey stays), 5 the query's tail
-- (UTF-8 safe), 6 volume, 7 the provider id that is not preferred. placeKey, start, length,
-- the flags and the last provider id are never removed. The beacon's budget is the capacity
-- of its rows (Capacity / RowsCap / AdaptiveLayout and geom.maxRows below).

ns = ns or {}

local P2 = {}
ns.Protocol2 = P2

local floor, ceil, huge = math.floor, math.ceil, math.huge
local sbyte, schar, ssub, sfind, smatch, sgsub, sgmatch = string.byte, string.char, string.sub,
  string.find, string.match, string.gsub, string.gmatch
local slower, supper = string.lower, string.upper
local concat = table.concat

-- ------------------------------------------------------------------ constants --

P2.MAGIC = 0x4E
P2.VERSION = 2
P2.CHAT_PREFIX = "NDROP2"

P2.EV = { NONE = 0, HELLO = 1, ARM = 2, FIRE = 3, CANCEL = 4, PING = 5 }
P2.EV_NAME = { [0] = "NONE", "HELLO", "ARM", "FIRE", "CANCEL", "PING" }
P2.FLAG = { IN_COMBAT = 1, IN_INSTANCE = 2, ON_TAXI = 4, DEAD = 8, LOCAL_PLAYBACK = 16, DROP_ACTIVE = 32,
            TEST = 64 }

P2.TAG = { PLACE_KEY = 0x01, DROP_ID = 0x02, SPOTIFY = 0x03, YOUTUBE = 0x04, QUERY = 0x05,
           START_DS = 0x06, LENGTH_DS = 0x07, VOLUME = 0x08, LABEL = 0x09, SET_ID = 0x0A }
local MAX_TAG = 0x0A

P2.MAX_DROP_ID = 40      -- bytes, sanitized ASCII
P2.MAX_QUERY = 120       -- bytes, UTF-8 "artist" .. 0x1F .. "title"
P2.MAX_LABEL = 48        -- bytes, UTF-8
P2.MAX_SET_ID = 32       -- bytes, sanitized ASCII
P2.MAX_BODY = 255        -- bodyLen is one byte
P2.FRAME_OVERHEAD = 7    -- 5 head bytes + 2 checksum bytes
P2.MAX_FRAME = P2.MAX_BODY + P2.FRAME_OVERHEAD   -- 262
P2.QUERY_SEP = "\031"    -- 0x1F unit separator between artist and title

-- Pixel layout (docs/PROTOCOL-v2.md section 4).
P2.HEADER = { {1, 0, 1}, {0, 1, 0}, {1, 0, 1}, {0, 1, 0} } -- magenta, green, magenta, green
P2.HEADER_CELLS = 4
P2.GEOMETRY_CELLS = 3    -- cols/16, (rows-1) div 8, (rows-1) mod 8
P2.LEAD_CELLS = 7        -- header + geometry, never whitened
P2.COL_STEP = 16
P2.MIN_COLS = 16
P2.MAX_COLS = 112
P2.MAX_ROWS = 64
P2.MIN_CELL_PX = 1
P2.MAX_CELL_PX = 8
P2.MAX_STRIP_PX = 9      -- beacon strips: rows * cellPx <= 9 px (2 px -> 4 rows, 3 -> 3, 4 -> 2)
P2.DEFAULT_GEOMETRY = { cellsPerRow = 64, cellPx = 3, corner = "TOPLEFT" }
P2.CORNERS = { TOPLEFT = true, TOPRIGHT = true, BOTTOMLEFT = true, BOTTOMRIGHT = true }

-- Chat-log padding lines (SPEC 3.6, unchanged from v1): "NDPAD <seq> ....", 900 characters.
P2.PAD_PREFIX = "NDPAD"
P2.PAD_LINE_LEN = 900

-- Whitening keystream (Park-Miller minimal standard LCG; products stay below 2^53).
P2.WHITEN_SEED = 1313100338   -- 0x4E445232 "NDR2"
P2.WHITEN_MUL = 16807
P2.WHITEN_MOD = 2147483647
local WHITEN_TOP = 1073741824 -- 2^30: the keystream bit is the state's top bit

-- Fields each event type carries (HELLO / PING: none; CANCEL: identity only).
local ALL_TAGS = { true, true, true, true, true, true, true, true, true, true }
local PLACE_TAGS = { true, true }
local NO_TAGS = {}
P2.TYPE_TAGS = { [0] = NO_TAGS, [1] = NO_TAGS, [2] = ALL_TAGS, [3] = ALL_TAGS, [4] = PLACE_TAGS, [5] = NO_TAGS }

local POW2 = {}
for i = 0, 7 do POW2[i] = 2 ^ i end

-- ------------------------------------------------------------------ helpers --

local function isnum(v)
  return type(v) == "number" and v == v and v ~= huge and v ~= -huge
end

local function clamp(v, lo, hi)
  if v < lo then return lo elseif v > hi then return hi end
  return v
end

-- Fletcher-16 over the first `n` (default all) values of a byte array.
function P2.Fletcher16Bytes(bytes, n)
  local s1, s2 = 0, 0
  for i = 1, n or #bytes do
    s1 = (s1 + bytes[i]) % 255
    s2 = (s2 + s1) % 255
  end
  return s2 * 256 + s1
end

-- Fletcher-16 over a string's bytes.
function P2.Fletcher16String(s)
  local s1, s2 = 0, 0
  for i = 1, #s do
    s1 = (s1 + sbyte(s, i)) % 255
    s2 = (s2 + s1) % 255
  end
  return s2 * 256 + s1
end

-- placeKey of a drop id: Fletcher-16 of the full, unsanitized id string (nil if no id).
function P2.PlaceKey(dropId)
  if type(dropId) ~= "string" or dropId == "" then return nil end
  return P2.Fletcher16String(dropId)
end

-- True if flag bit `f` (a power of two) is set in `flags` (v1 Protocol.HasFlag).
function P2.HasFlag(flags, f)
  if not isnum(flags) or not isnum(f) or f <= 0 then return false end
  return floor(flags / f) % 2 == 1
end

-- Chat-log padding line of exactly `len` characters (default 900): "NDPAD <seq> ....".
function P2.PadLine(seq, len)
  len = isnum(len) and floor(len) or P2.PAD_LINE_LEN
  local head = string.format("%s %d ", P2.PAD_PREFIX, isnum(seq) and floor(seq) % 256 or 0)
  return head .. string.rep(".", len - #head)
end

-- Number of padding lines of `len` characters that make `padBytes` bytes (0 when <= 0).
function P2.PadLineCount(padBytes, len)
  len = isnum(len) and len > 0 and len or P2.PAD_LINE_LEN
  if not isnum(padBytes) or padBytes <= 0 then return 0 end
  return ceil(padBytes / len)
end

-- Byte array -> string, string -> byte array.
function P2.BytesToString(bytes)
  local parts, n = {}, #bytes
  for i = 1, n, 256 do
    local j = i + 255
    if j > n then j = n end
    parts[#parts + 1] = schar(unpack(bytes, i, j))
  end
  return concat(parts)
end

function P2.StringToBytes(s)
  local t = {}
  for i = 1, #s do t[i] = sbyte(s, i) end
  return t
end

-- ---------------------------------------------------------------- text / UTF-8 --

-- Text field cleaning: invalid UTF-8 bytes (bad lead/continuation, overlong forms, UTF-16
-- surrogates, > U+10FFFF) become "?", C0 controls and DEL become a space (so the 0x1F query
-- separator is unambiguous), and leading/trailing spaces are trimmed. Non-strings -> "".
function P2.Utf8Clean(s)
  if type(s) ~= "string" then return "" end
  local out, n = {}, 0
  local i, len = 1, #s
  while i <= len do
    local c = sbyte(s, i)
    if c < 0x80 then
      n = n + 1
      if c < 0x20 or c == 0x7F then out[n] = " " else out[n] = schar(c) end
      i = i + 1
    else
      local size = 0
      if c >= 0xC2 and c <= 0xDF then
        local c2 = sbyte(s, i + 1)
        if c2 and c2 >= 0x80 and c2 <= 0xBF then size = 2 end
      elseif c >= 0xE0 and c <= 0xEF then
        local c2, c3 = sbyte(s, i + 1, i + 2)
        local lo, hi = 0x80, 0xBF
        if c == 0xE0 then lo = 0xA0 elseif c == 0xED then hi = 0x9F end
        if c3 and c2 >= lo and c2 <= hi and c3 >= 0x80 and c3 <= 0xBF then size = 3 end
      elseif c >= 0xF0 and c <= 0xF4 then
        local c2, c3, c4 = sbyte(s, i + 1, i + 3)
        local lo, hi = 0x80, 0xBF
        if c == 0xF0 then lo = 0x90 elseif c == 0xF4 then hi = 0x8F end
        if c4 and c2 >= lo and c2 <= hi and c3 >= 0x80 and c3 <= 0xBF and c4 >= 0x80 and c4 <= 0xBF then
          size = 4
        end
      end
      n = n + 1
      if size == 0 then
        out[n] = "?"
        i = i + 1
      else
        out[n] = ssub(s, i, i + size - 1)
        i = i + size
      end
    end
  end
  return (smatch(concat(out), "^ *(.-) *$"))
end

-- Longest prefix of `s` with at most `maxBytes` bytes that does not end inside a UTF-8
-- sequence (code-point safe; a grapheme cluster may still be split).
function P2.Utf8Truncate(s, maxBytes)
  if type(s) ~= "string" then return "" end
  if not isnum(maxBytes) then return s end
  maxBytes = floor(maxBytes)
  if #s <= maxBytes then return s end
  if maxBytes <= 0 then return "" end
  local n = maxBytes
  while n > 0 do
    local b = sbyte(s, n + 1)
    if b < 0x80 or b >= 0xC0 then break end
    n = n - 1
  end
  return ssub(s, 1, n)
end

-- ASCII id sanitizer (as v1 chat ids, but byte-wise): every byte outside [A-Za-z0-9:_.-]
-- becomes "_", then the result is cut to `maxLen` bytes. Non-strings -> "".
function P2.SanitizeId(id, maxLen)
  if type(id) ~= "string" then return "" end
  local s = sgsub(id, "[^A-Za-z0-9:_%.%-]", "_")
  if maxLen and #s > maxLen then s = ssub(s, 1, maxLen) end
  return s
end

-- "artist" .. 0x1F .. "title", at most MAX_QUERY bytes. When both do not fit, the title keeps
-- at least 60 bytes and the artist at least 59 (each cut on a UTF-8 boundary), so the
-- separator always survives. Returns nil when both are empty.
function P2.BuildQuery(artist, title)
  local a, t = P2.Utf8Clean(artist), P2.Utf8Clean(title)
  if a == "" and t == "" then return nil end
  local budget = P2.MAX_QUERY - 1
  if #a + #t > budget then
    local half = floor(budget / 2)
    if #a <= half then
      t = P2.Utf8Truncate(t, budget - #a)
    elseif #t <= budget - half then
      a = P2.Utf8Truncate(a, budget - #t)
    else
      a = P2.Utf8Truncate(a, half)
      t = P2.Utf8Truncate(t, budget - #a)
    end
  end
  return a .. P2.QUERY_SEP .. t
end

-- --------------------------------------------------------------- link parsers --

local function trim(s)
  return (smatch(s, "^%s*(.-)%s*$"))
end

local function isBase62(s, n)
  return #s == n and sfind(s, "^[A-Za-z0-9]+$") ~= nil
end

-- Splits "scheme://host/rest" -> lowercased host, rest (rest starts at the first / ? or #).
local function splitUrl(s)
  local ls = slower(s)
  local start = 1
  if ssub(ls, 1, 8) == "https://" then start = 9 elseif ssub(ls, 1, 7) == "http://" then start = 8 end
  local cut = sfind(s, "[/?#]", start)
  if not cut then return ssub(ls, start), "" end
  return ssub(ls, start, cut - 1), ssub(s, cut)
end

-- Path part of `rest` (before ? or #) and its query string (between ? and #).
local function pathAndQuery(rest)
  local q = sfind(rest, "?", 1, true)
  local h = sfind(rest, "#", 1, true)
  if h and (not q or h < q) then return ssub(rest, 1, h - 1), "" end
  if not q then return rest, "" end
  if h then return ssub(rest, 1, q - 1), ssub(rest, q + 1, h - 1) end
  return ssub(rest, 1, q - 1), ssub(rest, q + 1)
end

-- 22-char Spotify track id from "spotify:track:ID", an open.spotify.com track link
-- (http(s) optional, /intl-xx/ and /embed/ prefixes allowed, ?si=... ignored) or a bare id.
function P2.SpotifyIdFromLink(text)
  if type(text) ~= "string" then return nil end
  local s = trim(text)
  if s == "" then return nil end
  if slower(ssub(s, 1, 14)) == "spotify:track:" then
    local id = ssub(s, 15)
    if isBase62(id, 22) then return id end
    return nil
  end
  local host, rest = splitUrl(s)
  if host == "open.spotify.com" or host == "play.spotify.com" then
    if ssub(rest, 1, 1) ~= "/" then return nil end
    local path = pathAndQuery(rest)
    local segs = {}
    for seg in sgmatch(path, "[^/]+") do segs[#segs + 1] = seg end
    local i = 1
    if segs[i] and sfind(slower(segs[i]), "^intl%-[a-z][a-z%-]*$") then i = i + 1 end
    if segs[i] and slower(segs[i]) == "embed" then i = i + 1 end
    if segs[i] and slower(segs[i]) == "track" and segs[i + 1] and isBase62(segs[i + 1], 22)
        and segs[i + 2] == nil then
      return segs[i + 1]
    end
    return nil
  end
  if isBase62(s, 22) then return s end
  return nil
end

local YT_HOSTS = { ["youtube.com"] = true, ["www.youtube.com"] = true, ["m.youtube.com"] = true,
                   ["music.youtube.com"] = true, ["youtube-nocookie.com"] = true,
                   ["www.youtube-nocookie.com"] = true }
local YT_PATHS = { shorts = true, embed = true, live = true, v = true }

-- `cand` must start with exactly 11 id chars followed by nothing or one of / ? & #.
local function ytId(cand)
  if not cand then return nil end
  local id = smatch(cand, "^[A-Za-z0-9_%-]*")
  if #id ~= 11 then return nil end
  local nextc = ssub(cand, 12, 12)
  if nextc ~= "" and not sfind(nextc, "^[/?&#]$") then return nil end
  return id
end

-- 11-char YouTube video id from a bare id, youtu.be/ID, youtube.com/watch?v=ID (any
-- parameter position), /shorts/ID, /embed/ID, /live/ID, /v/ID on youtube.com, www., m.,
-- music. and youtube-nocookie.com hosts (http(s) optional).
function P2.YouTubeIdFromLink(text)
  if type(text) ~= "string" then return nil end
  local s = trim(text)
  if s == "" then return nil end
  if #s == 11 and sfind(s, "^[A-Za-z0-9_%-]+$") then return s end
  local host, rest = splitUrl(s)
  if host == "youtu.be" or host == "www.youtu.be" then
    if ssub(rest, 1, 1) ~= "/" then return nil end
    return ytId(ssub(rest, 2))
  end
  if not YT_HOSTS[host] then return nil end
  local path, query = pathAndQuery(rest)
  local lpath = slower(path)
  if lpath == "/watch" or lpath == "/watch/" then
    for pair in sgmatch(query, "[^&]+") do
      local k, v = smatch(pair, "^([^=]*)=(.*)$")
      if k == "v" then return ytId(v) end
    end
    return nil
  end
  local seg, tail = smatch(path, "^/([^/]+)/(.*)$")
  if seg and YT_PATHS[slower(seg)] then return ytId(tail) end
  return nil
end

-- --------------------------------------------------------------------- frames --

local function u16(v)
  return schar(floor(v / 256), v % 256)
end

-- Deciseconds from a direct deciseconds value or from milliseconds; nil when absent or 0.
local function deciseconds(dsv, msv)
  local v
  if isnum(dsv) then
    v = floor(dsv + 0.5)
  elseif isnum(msv) then
    v = floor((msv + 50) / 100)
  else
    return nil
  end
  v = clamp(v, 0, 65535)
  if v == 0 then return nil end
  return v
end

local TAG = P2.TAG

-- Frame bytes (7 + body) of the TLV values in `vals`.
local function frameSize(vals)
  local n = P2.FRAME_OVERHEAD
  for tag = 1, MAX_TAG do
    local v = vals[tag]
    if v then n = n + 2 + #v end
  end
  return n
end

local function removeTag(vals, tag, size)
  local v = vals[tag]
  if not v then return size end
  vals[tag] = nil
  return size - 2 - #v
end

-- The fit rule (DESIGN-v1.2 2.3; header of this file): removes fields in order until the
-- frame is at most `budget` bytes. Returns the resulting frame size, which is still over the
-- budget only when the never-removed fields alone exceed it.
local function fitBody(vals, budget, prefer)
  local size = frameSize(vals)
  if size <= budget then return size end
  size = removeTag(vals, TAG.SET_ID, size)                           -- 1 setId
  if size <= budget then return size end
  size = removeTag(vals, TAG.LABEL, size)                            -- 2 label
  if size <= budget then return size end
  if vals[TAG.SPOTIFY] or vals[TAG.YOUTUBE] then                     -- 3 query, if an id is present
    size = removeTag(vals, TAG.QUERY, size)
    if size <= budget then return size end
  end
  size = removeTag(vals, TAG.DROP_ID, size)                          -- 4 dropId (placeKey stays)
  if size <= budget then return size end
  local q = vals[TAG.QUERY]                                          -- 5 shorten the query
  if q then
    local keep = #q - (size - budget)
    local nq = keep >= 1 and P2.Utf8Truncate(q, keep) or ""
    if nq == "" then
      size = removeTag(vals, TAG.QUERY, size)
    else
      vals[TAG.QUERY] = nq
      size = size - (#q - #nq)
    end
    if size <= budget then return size end
  end
  size = removeTag(vals, TAG.VOLUME, size)                           -- 6 volume
  if size <= budget then return size end
  if vals[TAG.SPOTIFY] and vals[TAG.YOUTUBE] then                    -- 7 keep the preferred id
    size = removeTag(vals, prefer == "spotify" and TAG.YOUTUBE or TAG.SPOTIFY, size)
  end
  return size
end

-- "spotify" or "youtube" (anything else -> "youtube").
local function preferOf(prefer)
  if type(prefer) == "string" and slower(prefer) == "spotify" then return "spotify" end
  return "youtube"
end

-- Frame bytes (array of numbers 0..255) for an event table (see the top of this file),
-- fitted to `maxBytes` (default MAX_FRAME; clamped to FRAME_OVERHEAD..MAX_FRAME) by the fit
-- rule, which keeps the `prefer` provider id last (default: ev.prefer, else "youtube").
function P2.EncodeFrame(ev, maxBytes, prefer)
  if type(ev) ~= "table" then ev = {} end
  local budget = isnum(maxBytes) and clamp(floor(maxBytes), P2.FRAME_OVERHEAD, P2.MAX_FRAME) or P2.MAX_FRAME
  if prefer == nil then prefer = ev.prefer end
  local t = isnum(ev.type) and floor(ev.type) % 16 or 0
  local seq = isnum(ev.seq) and floor(ev.seq) % 256 or 0
  local flags = isnum(ev.flags) and floor(ev.flags) % 256 or 0
  local allow = P2.TYPE_TAGS[t] or NO_TAGS
  local vals = {}
  local dropId = ev.dropId
  local hasId = type(dropId) == "string" and dropId ~= ""
  if allow[1] then
    if isnum(ev.placeKey) then
      vals[1] = u16(floor(ev.placeKey) % 65536)
    elseif hasId then
      vals[1] = u16(P2.Fletcher16String(dropId))
    end
  end
  if allow[2] and hasId then vals[2] = P2.SanitizeId(dropId, P2.MAX_DROP_ID) end
  if allow[3] then vals[3] = P2.SpotifyIdFromLink(ev.spotify) end
  if allow[4] then vals[4] = P2.YouTubeIdFromLink(ev.youtube) end
  if allow[5] then vals[5] = P2.BuildQuery(ev.artist, ev.title) end
  if allow[6] then
    local v = deciseconds(ev.startDs, ev.startMs)
    if v then vals[6] = u16(v) end
  end
  if allow[7] then
    local v = deciseconds(ev.lengthDs, ev.lengthMs)
    if v then vals[7] = u16(v) end
  end
  if allow[8] and isnum(ev.volume) then vals[8] = schar(clamp(floor(ev.volume + 0.5), 0, 100)) end
  if allow[9] then
    local label = P2.Utf8Truncate(P2.Utf8Clean(ev.label), P2.MAX_LABEL)
    if label ~= "" then vals[9] = label end
  end
  if allow[10] then
    local set = P2.SanitizeId(ev.setId, P2.MAX_SET_ID)
    if set ~= "" then vals[10] = set end
  end
  fitBody(vals, budget, preferOf(prefer))

  local bytes = { P2.MAGIC, P2.VERSION * 16 + t, seq, flags, 0 }
  local n = 5
  for tag = 1, MAX_TAG do
    local v = vals[tag]
    if v then
      bytes[n + 1] = tag
      bytes[n + 2] = #v
      n = n + 2
      for i = 1, #v do
        n = n + 1
        bytes[n] = sbyte(v, i)
      end
    end
  end
  bytes[5] = n - 5
  local f = P2.Fletcher16Bytes(bytes, n)
  bytes[n + 1] = floor(f / 256)
  bytes[n + 2] = f % 256
  return bytes
end

local function idOk(v, maxLen)
  return #v >= 1 and #v <= maxLen and sfind(v, "^[A-Za-z0-9:_%.%-]+$") ~= nil
end

-- Validates a frame (byte array) and returns its fields, or nil. Mirrors protocol2.decode_frame:
-- magic, version, known type, exact length, checksum, strictly ascending non-zero tags, and
-- known tags with valid lengths / characters. Unknown tags are skipped (forward compatible)
-- and listed in `unknown` as { tag = t, value = "<bytes>" }.
function P2.DecodeFrame(bytes)
  if type(bytes) ~= "table" then return nil end
  local n = #bytes
  if n < P2.FRAME_OVERHEAD or bytes[1] ~= P2.MAGIC or floor(bytes[2] / 16) ~= P2.VERSION then return nil end
  local t = bytes[2] % 16
  if t > 5 then return nil end
  local bodyLen = bytes[5]
  if n ~= P2.FRAME_OVERHEAD + bodyLen then return nil end
  if bytes[6 + bodyLen] * 256 + bytes[7 + bodyLen] ~= P2.Fletcher16Bytes(bytes, 5 + bodyLen) then return nil end
  local ev = { type = t, seq = bytes[3], flags = bytes[4], unknown = {} }
  local i, stop, last = 6, 5 + bodyLen, 0
  while i <= stop do
    if i + 1 > stop then return nil end
    local tag, len = bytes[i], bytes[i + 1]
    if tag == 0 or tag <= last or i + 1 + len > stop then return nil end
    local v = P2.BytesToString({ unpack(bytes, i + 2, i + 1 + len) })
    if tag == 1 then
      if len ~= 2 then return nil end
      ev.placeKey = bytes[i + 2] * 256 + bytes[i + 3]
    elseif tag == 2 then
      if not idOk(v, P2.MAX_DROP_ID) then return nil end
      ev.dropId = v
    elseif tag == 3 then
      if not isBase62(v, 22) then return nil end
      ev.spotify = v
    elseif tag == 4 then
      if len ~= 11 or not sfind(v, "^[A-Za-z0-9_%-]+$") then return nil end
      ev.youtube = v
    elseif tag == 5 then
      if len < 1 or len > P2.MAX_QUERY then return nil end
      ev.query = v
      local sep = sfind(v, P2.QUERY_SEP, 1, true)
      if sep then
        ev.artist, ev.title = ssub(v, 1, sep - 1), ssub(v, sep + 1)
      else
        ev.artist, ev.title = "", v
      end
    elseif tag == 6 or tag == 7 then
      if len ~= 2 then return nil end
      local val = bytes[i + 2] * 256 + bytes[i + 3]
      if tag == 6 then ev.startDs = val else ev.lengthDs = val end
    elseif tag == 8 then
      if len ~= 1 or bytes[i + 2] > 100 then return nil end
      ev.volume = bytes[i + 2]
    elseif tag == 9 then
      if len < 1 or len > P2.MAX_LABEL then return nil end
      ev.label = v
    elseif tag == 10 then
      if not idOk(v, P2.MAX_SET_ID) then return nil end
      ev.setId = v
    else
      ev.unknown[#ev.unknown + 1] = { tag = tag, value = v }
    end
    last = tag
    i = i + 2 + len
  end
  return ev
end

-- ------------------------------------------------------------------- base64url --

local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
local B64_INDEX = {}
for i = 1, 64 do B64_INDEX[sbyte(B64, i)] = i - 1 end

-- RFC 4648 section 5 alphabet, no padding.
function P2.Base64Url(bytes)
  local out, n, len = {}, 0, #bytes
  for i = 1, len, 3 do
    local b1 = bytes[i]
    local b2 = (i + 1 <= len) and bytes[i + 1] or nil
    local b3 = (i + 2 <= len) and bytes[i + 2] or nil
    local v = b1 * 65536 + (b2 or 0) * 256 + (b3 or 0)
    local c1, c2 = floor(v / 262144) % 64, floor(v / 4096) % 64
    n = n + 1; out[n] = ssub(B64, c1 + 1, c1 + 1)
    n = n + 1; out[n] = ssub(B64, c2 + 1, c2 + 1)
    if b2 then
      local c3 = floor(v / 64) % 64
      n = n + 1; out[n] = ssub(B64, c3 + 1, c3 + 1)
    end
    if b3 then
      local c4 = v % 64
      n = n + 1; out[n] = ssub(B64, c4 + 1, c4 + 1)
    end
  end
  return concat(out)
end

-- Byte array from unpadded base64url, or nil (bad character, impossible length, or
-- non-canonical trailing bits).
function P2.Base64UrlDecode(s)
  if type(s) ~= "string" then return nil end
  local len = #s
  if len % 4 == 1 then return nil end
  local out, n = {}, 0
  for i = 1, len, 4 do
    local v, cnt = 0, 0
    for j = i, i + 3 do
      if j <= len then
        local d = B64_INDEX[sbyte(s, j)]
        if not d then return nil end
        v = v * 64 + d
        cnt = cnt + 1
      else
        v = v * 64
      end
    end
    n = n + 1; out[n] = floor(v / 65536) % 256
    if cnt >= 3 then n = n + 1; out[n] = floor(v / 256) % 256 elseif v % 65536 ~= 0 then return nil end
    if cnt == 4 then n = n + 1; out[n] = v % 256 elseif cnt == 3 and v % 256 ~= 0 then return nil end
  end
  return out
end

-- ------------------------------------------------------------------- chat line --

-- "NDROP2 <base64url frame>" -- the chat-log channel carries exactly the beacon's bytes.
function P2.FormatChatLineBytes(bytes)
  return P2.CHAT_PREFIX .. " " .. P2.Base64Url(bytes)
end

function P2.FormatChatLine(ev)
  return P2.FormatChatLineBytes(P2.EncodeFrame(ev))
end

-- ----------------------------------------------------------------- pixel cells --

local function cellPxOf(v)
  return clamp(floor((isnum(v) and v or P2.DEFAULT_GEOMETRY.cellPx) + 0.5), P2.MIN_CELL_PX, P2.MAX_CELL_PX)
end

-- Rows a beacon strip of `cellPx` px cells may have under the 9 px cap: floor(9 / cellPx),
-- at least 1 (cellPx rounded and clamped like NormalizeGeometry): 2 -> 4, 3 -> 3, 4 -> 2.
function P2.RowsCap(cellPx)
  local n = floor(P2.MAX_STRIP_PX / cellPxOf(cellPx))
  if n < 1 then n = 1 end
  return n
end

-- Largest frame (bytes, 7-byte overhead included) that fits `rows` rows of MAX_COLS cells:
-- floor((rows * 112 - 7) * 3 / 8), at most MAX_FRAME. 2 px (4 rows) = 165, 3 px (3) = 123,
-- 4 px (2) = 81, one row = 39.
function P2.Capacity(rows)
  local r = isnum(rows) and floor(rows) or 1
  if r < 1 then r = 1 end
  local cap = floor((r * P2.MAX_COLS - P2.LEAD_CELLS) * 3 / 8)
  if cap > P2.MAX_FRAME then cap = P2.MAX_FRAME end
  return cap
end

-- Adaptive strip size for a frame of `frameLen` bytes (DESIGN-v1.2 2.3): with
-- cells = 7 + ceil(8 * frameLen / 3), rows = ceil(cells / 112) and
-- cols = 16 * ceil(ceil(cells / rows) / 16). Returns rows, cols. RowsFor(frameLen, cols) is
-- always exactly `rows`, so decoders read it like any fixed-width strip.
function P2.AdaptiveLayout(frameLen)
  local cells = P2.LEAD_CELLS + ceil(frameLen * 8 / 3)
  local rows = ceil(cells / P2.MAX_COLS)
  local cols = P2.COL_STEP * ceil(ceil(cells / rows) / P2.COL_STEP)
  return rows, cols
end

-- Clamps a geometry table to the supported set: cellsPerRow rounded to a multiple of 16 in
-- 16..112 (default 64), cellPx rounded into 1..8 (default 3), corner one of TOPLEFT,
-- TOPRIGHT, BOTTOMLEFT, BOTTOMRIGHT (case-insensitive, default TOPLEFT), and the optional
-- maxRows floored into 1..RowsCap(cellPx). With maxRows the strip is adaptive: the frame
-- is fitted to Capacity(maxRows) and the columns follow AdaptiveLayout (cellsPerRow unused).
function P2.NormalizeGeometry(geom)
  if type(geom) ~= "table" then geom = {} end
  local cols = isnum(geom.cellsPerRow) and geom.cellsPerRow or P2.DEFAULT_GEOMETRY.cellsPerRow
  cols = clamp(floor(cols / P2.COL_STEP + 0.5) * P2.COL_STEP, P2.MIN_COLS, P2.MAX_COLS)
  local px = cellPxOf(geom.cellPx)
  local corner = type(geom.corner) == "string" and supper(geom.corner) or P2.DEFAULT_GEOMETRY.corner
  if not P2.CORNERS[corner] then corner = P2.DEFAULT_GEOMETRY.corner end
  local maxRows
  if isnum(geom.maxRows) then maxRows = clamp(floor(geom.maxRows), 1, P2.RowsCap(px)) end
  return { cellsPerRow = cols, cellPx = px, corner = corner, maxRows = maxRows }
end

-- Number of strip rows for a frame of `frameLen` bytes at `cols` cells per row.
function P2.RowsFor(frameLen, cols)
  return ceil((P2.LEAD_CELLS + ceil(frameLen * 8 / 3)) / cols)
end

local KS, ksCount, ksState = {}, 0, P2.WHITEN_SEED

-- Keystream bits 1..n (shared cache; callers must not modify it).
local function keystream(n)
  while ksCount < n do
    ksState = (ksState * P2.WHITEN_MUL) % P2.WHITEN_MOD
    ksCount = ksCount + 1
    KS[ksCount] = (ksState >= WHITEN_TOP) and 1 or 0
  end
  return KS
end

-- Copy of the first `n` keystream bits (for tests and self-tests).
function P2.Keystream(n)
  local ks, out = keystream(n), {}
  for i = 1, n do out[i] = ks[i] end
  return out
end

local function code3(v)
  return { floor(v / 4) % 2, floor(v / 2) % 2, v % 2 }
end

-- Cell grid for frame bytes. Returns
--   { rows, cols, cellPx, corner, widthPx, heightPx, frameLen, maxRows, overflow,
--     cells = { {r,g,b}, ... } }
-- with channel values 0/1 (SetColorTexture-ready) and rows * cols cells in row-major order:
-- cells[1..4] header, [5..7] geometry, then the frame bits MSB-first, 3 per cell (R,G,B),
-- zero-padded to the end of the last row, all data/pad bits XOR the whitening keystream.
-- Cell (row r, col c), 0-based, is drawn at x = c * cellPx, y = r * cellPx from the strip's
-- top-left; the strip's `corner` sits on the same corner of the client area. Rows always
-- read left-to-right, top-to-bottom, whatever the corner. With geom.maxRows the size is
-- AdaptiveLayout's, and `overflow` is true when the frame needs more rows than maxRows (the
-- grid is still complete and valid: the beacon draws it). nil if the frame cannot fit at all.
function P2.EncodeCellsFromBytes(bytes, geom)
  local g = P2.NormalizeGeometry(geom)
  local nbytes = #bytes
  local rows, cols
  if g.maxRows then
    rows, cols = P2.AdaptiveLayout(nbytes)
  else
    cols = g.cellsPerRow
    rows = P2.RowsFor(nbytes, cols)
  end
  if rows > P2.MAX_ROWS then return nil end
  local nData = rows * cols - P2.LEAD_CELLS
  local nbits = nData * 3
  local ks = keystream(nbits)
  local bits, k = {}, 0
  for i = 1, nbytes do
    local v = bytes[i]
    for p = 7, 0, -1 do
      k = k + 1
      bits[k] = floor(v / POW2[p]) % 2
    end
  end
  for i = k + 1, nbits do bits[i] = 0 end
  local cells = {}
  for i = 1, P2.HEADER_CELLS do
    local h = P2.HEADER[i]
    cells[i] = { h[1], h[2], h[3] }
  end
  cells[5] = code3(cols / P2.COL_STEP)
  cells[6] = code3(floor((rows - 1) / 8))
  cells[7] = code3((rows - 1) % 8)
  for j = 0, nData - 1 do
    local b = 3 * j
    cells[P2.LEAD_CELLS + 1 + j] = { (bits[b + 1] + ks[b + 1]) % 2, (bits[b + 2] + ks[b + 2]) % 2,
                                     (bits[b + 3] + ks[b + 3]) % 2 }
  end
  return { rows = rows, cols = cols, cellPx = g.cellPx, corner = g.corner, widthPx = cols * g.cellPx,
           heightPx = rows * g.cellPx, frameLen = nbytes, cells = cells, maxRows = g.maxRows,
           overflow = (g.maxRows ~= nil and rows > g.maxRows) and true or false }
end

-- Cell grid for an event table: EncodeFrame fitted to Capacity(geom.maxRows) (or to
-- MAX_FRAME without maxRows; ev.prefer picks the id kept last), then EncodeCellsFromBytes.
function P2.EncodeCells(ev, geom)
  local g = P2.NormalizeGeometry(geom)
  local budget = g.maxRows and P2.Capacity(g.maxRows) or P2.MAX_FRAME
  return P2.EncodeCellsFromBytes(P2.EncodeFrame(ev, budget), g)
end

-- 0-based (row, col) of 1-based cell index `i` in a grid `cols` wide.
function P2.CellPosition(i, cols)
  return floor((i - 1) / cols), (i - 1) % cols
end

return P2
