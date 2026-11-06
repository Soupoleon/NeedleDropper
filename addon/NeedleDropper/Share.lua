local ADDON, ns = ...
--[[ NeedleDropper Share: soundtrack share codes (DESIGN-v1.2 section 5).

  code    = "!NeedleDropper:1!" .. Base64(zlib(JSON)) .. "!"
  JSON    = one needledropper-pack v1 object (the packs/*.json format), keys sorted
  Base64  = RFC 4648 standard alphabet, padded (safe to paste into Discord)

JSON and Base64 are pure Lua (Json.lua, Base64.lua): deterministic and never executed. Only zlib
is native: C_EncodingUtil.CompressString / DecompressString with Enum.CompressionMethod.Zlib
(Blizzard_APIDocumentationGenerated/EncodingUtilDocumentation.lua; both MayReturnNothing, so
every call is guarded and its result type-checked).

API
  Share.FromSet(ref)        -> pack, omittedCount   (drafts are left out and counted)
  Share.Encode(pack)        -> code | nil, message  (validates first; never writes a code the
                                                     reader would refuse)
  Share.Decode(text)        -> pack, warnings | nil, reason, detail
        reason: notcode, foreign:<Name>, newer, cutoff, damaged, toobig, invalid
        detail: a player-facing message for "invalid" (and some "damaged")
  Share.Validate(value)     -> pack, warnings | nil, reason, detail   (DESIGN 5.5)
  Share.Import(pack, opts)  -> ref | nil, message   (never overwrites, never turns it on;
                                                     opts.name overrides the name)
  Share.CanUpdate(pack)     -> ref | nil   (same pack id, same non-empty author, higher revision)
  Share.Update(ref, pack)   -> true | nil, message  (keeps on/off state and order)
  Share.Preview(pack)       -> info | nil, reason, detail   (for the Import dialog)
  Share.ReasonText(reason, detail) -> player-facing message
  Share.ForeignName(text)   -> "WeakAuras" | ... | nil

A decoded pack is the normalized JSON object (zone = uiMapID, spotify = "spotify:track:<id>",
file + inGameFile = true, ...): decoding the code of a normalized pack gives the same pack back.
Warnings are player-facing strings (fields that were dropped and why). Every string in a pack
is raw text: escape it (ns.EscapeText) before it reaches a FontString.
]]

local Share = {}
ns.Share = Share

local L = ns.L or setmetatable({}, { __index = function(_, k) return k end })

local type, pairs, tostring, tonumber, pcall = type, pairs, tostring, tonumber, pcall
local floor = math.floor
local byte, find, format, gsub, match, sub = string.byte, string.find, string.format, string.gsub,
  string.match, string.sub

Share.PREFIX = "!NeedleDropper:1!"
Share.CONTAINER_VERSION = 1
Share.PACK_FORMAT = "needledropper-pack"
Share.PACK_VERSION = 1
Share.MAX_TEXT = 65536      -- pasted characters
Share.MAX_BYTES = 49152     -- Base64-decoded (zlib) bytes
Share.MAX_JSON = 262144     -- inflated JSON bytes
Share.MAX_DEPTH = 6
Share.MAX_DROPS = 300
Share.PREVIEW_PLACES = 12

local ZERO_WIDTH_SPACE = "\226\128\139"   -- U+200B
local BYTE_ORDER_MARK = "\239\187\191"    -- U+FEFF
local NO_BREAK_SPACE = "\194\160"         -- U+00A0

local function zlibMethod()
  return (Enum and Enum.CompressionMethod and Enum.CompressionMethod.Zlib) or 1
end
local function sizeLevel()
  return (Enum and Enum.CompressionLevel and Enum.CompressionLevel.OptimizeForSize) or 2
end

local function S() return ns.Soundtracks end

local function copy(v)
  if type(v) ~= "table" then return v end
  local out = {}
  for k, x in pairs(v) do out[k] = copy(x) end
  return out
end

local function quote(v)
  local s = tostring(v)
  if #s > 40 then s = sub(s, 1, 40) .. "..." end
  return '"' .. s .. '"'
end

-- =========================================================== foreign strings ==

local FOREIGN_ANYWHERE = {
  { "!WA:", "WeakAuras" }, { "!E1!", "ElvUI" }, { "!E2!", "ElvUI" }, { "!PLATER:", "Plater" },
  { "!~MDT2~", "MDT" }, { "D!ProfileV2-", "Details" }, { "!CELL:", "Cell" },
}
-- These would match ordinary text, so only at the very start of the (trimmed) paste.
local FOREIGN_AT_START = { { "BW2:", "BigWigs" }, { "BWB1:", "BigWigs" }, { "1|", "Blizzard" } }

-- Name of the addon whose export string `text` is, or nil (prefixes checked in list order, as
-- the companion's sharecode.foreign_prefix does).
function Share.ForeignName(text)
  if type(text) ~= "string" then return nil end
  for i = 1, #FOREIGN_ANYWHERE do
    if find(text, FOREIGN_ANYWHERE[i][1], 1, true) then return FOREIGN_ANYWHERE[i][2] end
  end
  local start = find(text, "[^ \t\r\n]")
  if not start then return nil end
  for i = 1, #FOREIGN_AT_START do
    local p = FOREIGN_AT_START[i][1]
    if sub(text, start, start + #p - 1) == p then return FOREIGN_AT_START[i][2] end
  end
  return nil
end

-- ================================================================ messages ==

function Share.ReasonText(reason, detail)
  reason = tostring(reason or "")
  if reason == "notcode" then
    return L["That isn't a NeedleDropper soundtrack code: codes start with !NeedleDropper:1!"]
  elseif sub(reason, 1, 8) == "foreign:" then
    return format(L["This is a %s string, not a NeedleDropper soundtrack."], sub(reason, 9))
  elseif reason == "newer" then
    return L["This code was made with a newer NeedleDropper: update the addon."]
  elseif reason == "cutoff" then
    return L["The code was cut off: copy all of it, up to the ! at the end."]
  elseif reason == "damaged" then
    if detail then return detail end
    return L["The code is damaged: copy it again from the original message."]
  elseif reason == "toobig" then
    return L["This code is too big for NeedleDropper."]
  elseif reason == "invalid" then
    if detail then return format(L["The soundtrack in this code can't be used: %s"], detail) end
    return L["The soundtrack in this code can't be used."]
  end
  return L["That isn't a NeedleDropper soundtrack code: codes start with !NeedleDropper:1!"]
end

-- =============================================================== validation ==

local TOP_KEYS = { format = 1, version = 1, id = 1, name = 1, author = 1, description = 1, tags = 1,
                   homepage = 1, revision = 1, drops = 1 }
local DROP_KEYS = { zone = 1, subzone = 1, areaID = 1, instanceID = 1, label = 1, artist = 1, title = 1,
                    spotify = 1, youtube = 1, file = 1, inGameFile = 1, startMs = 1, durationMs = 1,
                    volume = 1, tags = 1, note = 1, disabled = 1, id = 1, appleMusic = 1 }
local TEXT_KEYS = { "label", "artist", "title", "note", "spotify", "youtube" }
local NUMBER_KEYS = { "startMs", "durationMs", "volume" }
local PLACE_KEYS = { "zone", "areaID", "instanceID" }

-- Accented Latin letters folded the way the companion derives a missing pack id
-- (packs.derive_pack_id: NFKD, combining marks removed), for U+00C0-U+024F.
local FOLD = {
  a = "ÀÁÂÃÄÅĀĂĄǍǞǠǺȀȂȦàáâãäåāăąǎǟǡǻȁȃȧ", c = "ÇĆĈĊČçćĉċč", d = "Ďď", dz = "ǄǱǅǲǆǳ",
  e = "ÈÉÊËĒĔĖĘĚȄȆȨèéêëēĕėęěȅȇȩ", g = "ĜĞĠĢǦǴĝğġģǧǵ", h = "ĤȞĥȟ", i = "ÌÍÎÏĨĪĬĮİǏȈȊìíîïĩīĭįǐȉȋ",
  ij = "Ĳĳ", j = "Ĵĵǰ", k = "ĶǨķǩ", l = "ĹĻĽĺļľ", lj = "Ǉǈǉ", n = "ÑŃŅŇǸñńņňǹ", nj = "Ǌǋǌ",
  o = "ÒÓÔÕÖŌŎŐƠǑǪǬȌȎȪȬȮȰòóôõöōŏőơǒǫǭȍȏȫȭȯȱ", r = "ŔŖŘȐȒŕŗřȑȓ", s = "ŚŜŞŠȘśŝşšſș", t = "ŢŤȚţťț",
  u = "ÙÚÛÜŨŪŬŮŰŲƯǓǕǗǙǛȔȖùúûüũūŭůűųưǔǖǘǚǜȕȗ", w = "Ŵŵ", y = "ÝŶŸȲýÿŷȳ", z = "ŹŻŽźżž",
}
local FOLD_MAP = {}
for to, chars in pairs(FOLD) do
  for ch in string.gmatch(chars, "[\192-\223][\128-\191]") do FOLD_MAP[ch] = to end
end

-- The id of a pack that has none: its name with accents folded, as a slug of at most 40
-- characters ("" when nothing is left).
function Share.DerivePackId(name)
  local St = S()
  local folded = gsub(tostring(name or ""), "[\195-\201][\128-\191]", FOLD_MAP)
  local id = sub(St.Slugify(folded), 1, ns.Sets.MAX_ID)
  return (gsub(id, "%-+$", ""))
end

local function isComment(k)
  return type(k) == "string" and (sub(k, 1, 1) == "_" or sub(k, 1, 1) == "$" or sub(k, 1, 2) == "//")
end

local function isArray(t)
  local n = #t
  for k in pairs(t) do
    if type(k) ~= "number" or k < 1 or k > n or floor(k) ~= k then return false end
  end
  return true
end

local function isWholeNumber(v)
  return type(v) == "number" and v == v and v ~= math.huge and v ~= -math.huge and floor(v) == v
end

-- Collects warnings; the same message from several drops is counted once.
local function newWarnings()
  local list, counts = {}, {}
  local w = {}
  function w.add(msg)
    if counts[msg] then
      counts[msg] = counts[msg] + 1
    else
      counts[msg] = 1
      list[#list + 1] = msg
    end
  end
  function w.result()
    local out = {}
    for i = 1, #list do
      local n = counts[list[i]]
      if n > 1 then out[i] = format(L["%s (%d drops)"], list[i], n) else out[i] = list[i] end
    end
    return out
  end
  return w
end

local function dropHint(d)
  local h = d.label or d.subzone or d.title
  if type(h) ~= "string" then h = d.zone end
  if h == nil then return "?" end
  h = tostring(h)
  if #h > 40 then h = sub(h, 1, 40) .. "..." end
  return h
end

local function dropFail(i, d, message)
  return nil, "invalid", format(L["drop %d (%s): %s"], i, dropHint(d), message)
end

-- zone / areaID / instanceID of a drop: whole numbers 1..2^31-1 (no zone names, no digit
-- strings: a share code is written by a program). Returns the numbers or nil plus a message.
local function placeNumbers(d)
  local St = S()
  local out = {}
  for n = 1, #PLACE_KEYS do
    local k = PLACE_KEYS[n]
    local v = d[k]
    if v ~= nil then
      if not isWholeNumber(v) or v < 1 or v > St.MAX_INT then
        if k == "zone" and type(v) == "string" then
          return nil, L["the zone must be a uiMapID number (zone names are not supported in share codes)."]
        end
        return nil, format(L["%s must be a whole number from 1 to %d."], St.FieldName(k), St.MAX_INT)
      end
      out[k] = v
    end
  end
  return out
end

local function landmarkName(d)
  local St = S()
  if d.subzone == nil then return true, nil end
  local t, code = St.CleanText(d.subzone, St.MAX.subzone, false)
  if not t then return false, St.TextError("subzone", code, St.MAX.subzone) end
  if t == "" then return true, nil end
  return true, t
end

-- A "disabled": true entry: only its place counts (like packs.parse_pack, which skips the rest).
-- Returns the JSON drop and model drop; false (left out, with a warning); or nil, reason, detail.
local function validateOffEntry(i, d, w)
  local St = S()
  local place, perr = placeNumbers(d)
  if not place then return dropFail(i, d, perr) end
  local ok, subzone = landmarkName(d)
  if not ok then return dropFail(i, d, subzone) end
  local model = { mapID = place.zone, areaID = place.areaID, instanceID = place.instanceID, subzone = subzone,
                  disabled = true }
  local nd = St.Validate(model, { noLabel = true })
  if not nd then
    w.add(L["A turned-off place without a usable place was left out."])
    return false
  end
  return { disabled = true, zone = place.zone, areaID = place.areaID, instanceID = place.instanceID,
           subzone = subzone }, nd
end

-- One pack drop -> normalized JSON drop and validated model drop; false (left out); or
-- nil, "invalid", detail. The rules are those of the companion's sharecode.validate.
local function validateDrop(i, d, w)
  local St = S()
  if type(d) ~= "table" or (next(d) ~= nil and isArray(d)) then
    return nil, "invalid", format(L["drop %d is not an object."], i)
  end
  local disabled = d.disabled
  if disabled ~= nil and disabled ~= true and disabled ~= false then
    return dropFail(i, d, L["\"disabled\" must be true or false."])
  end
  if disabled == true then return validateOffEntry(i, d, w) end
  for k in pairs(d) do
    if type(k) ~= "string" then return dropFail(i, d, L["it has a field without a name."]) end
    if not DROP_KEYS[k] and not isComment(k) then w.add(format(L["Unknown field %s was ignored."], quote(k))) end
  end
  local place, perr = placeNumbers(d)
  if not place then return dropFail(i, d, perr) end
  if d.subzone ~= nil then
    if type(d.subzone) ~= "string" then return dropFail(i, d, St.TextError("subzone", "type")) end
    if St.CleanText(d.subzone) == "" then return dropFail(i, d, L["the landmark name is empty."]) end
  end
  for n = 1, #TEXT_KEYS do
    local k = TEXT_KEYS[n]
    local v = d[k]
    if v ~= nil and type(v) ~= "string" then return dropFail(i, d, St.TextError(k, "type")) end
  end
  for n = 1, #NUMBER_KEYS do
    local k = NUMBER_KEYS[n]
    local v = d[k]
    if v ~= nil and St.IntValue(v) == nil then
      return dropFail(i, d, format(L["%s must be a whole number."], St.FieldName(k)))
    end
  end
  if d.tags ~= nil and type(d.tags) ~= "table" then
    return dropFail(i, d, format(L["%s must be a list of words."], St.FieldName("tags")))
  end
  local apple = d.appleMusic
  if apple ~= nil and apple ~= "" then
    if type(apple) ~= "string" or not find(St.CleanText(apple) or "", "^https://") then
      return dropFail(i, d, L["\"appleMusic\" must be an https:// link."])
    end
    w.add(L["Apple Music links are not used any more and were left out."])
  end
  local igf = d.inGameFile
  if igf ~= nil and igf ~= true and igf ~= false then
    return dropFail(i, d, L["\"inGameFile\" must be true or false."])
  end
  local file
  if d.file ~= nil and d.file ~= "" then
    if type(d.file) ~= "string" then return dropFail(i, d, L["\"file\" must be a file name."]) end
    file = St.FileName(d.file)
    if file then
      if igf ~= true then
        w.add(format(L["%s now plays in game: copy it into Interface\\AddOns\\NeedleDropper_Local\\media."], quote(file)))
      end
    else
      w.add(format(L["In-game file %s is not a plain .mp3 or .ogg file name and was left out."], quote(d.file)))
    end
  elseif igf == true then
    return dropFail(i, d, L["\"inGameFile\" needs a file name."])
  end
  local tags, terr, dup = St.CleanTags(d.tags)
  if tags == false then return dropFail(i, d, terr) end
  if dup then w.add(L["A tag listed twice was kept once."]) end
  local model = {
    mapID = place.zone, areaID = place.areaID, instanceID = place.instanceID, subzone = d.subzone, label = d.label,
    artist = d.artist, title = d.title, spotify = d.spotify, youtube = d.youtube, file = file,
    startMs = d.startMs, durationMs = d.durationMs, volume = d.volume, tags = tags, note = d.note,
  }
  local nd, err = St.Validate(model, { requireSong = true, noLabel = true })
  if not nd then return dropFail(i, d, err) end
  if d.id ~= nil and d.id ~= "" and d.id ~= nd.id then
    w.add(format(L["Drop id %s was ignored; the place gives %s."], quote(d.id), nd.id))
  end
  if nd.startMs == 0 then nd.startMs = nil end
  if nd.durationMs == 0 then nd.durationMs = nil end
  local j = {
    zone = nd.mapID, areaID = nd.areaID, instanceID = nd.instanceID, subzone = nd.subzone, label = nd.label,
    artist = nd.artist, title = nd.title, youtube = nd.youtube, startMs = nd.startMs, durationMs = nd.durationMs,
    volume = nd.volume, tags = nd.tags, note = nd.note,
  }
  if nd.spotify then j.spotify = "spotify:track:" .. nd.spotify end
  if nd.file then
    j.file = nd.file
    j.inGameFile = true
  end
  return j, nd
end

-- value -> normalized pack, warnings, model drops; or nil, reason, detail.
local function validatePack(value)
  local St = S()
  if type(value) ~= "table" or (next(value) ~= nil and isArray(value)) then
    return nil, "invalid", L["the code does not contain a soundtrack."]
  end
  local w = newWarnings()
  for k in pairs(value) do
    if type(k) ~= "string" then return nil, "invalid", L["the soundtrack has a field without a name."] end
    if not TOP_KEYS[k] and not isComment(k) then w.add(format(L["Unknown field %s was ignored."], quote(k))) end
  end
  local fmt = value.format
  if fmt == nil then
    w.add(L["The code does not say it is a NeedleDropper soundtrack; it was read as one."])
  elseif fmt ~= Share.PACK_FORMAT then
    return nil, "invalid", format(L["this is not a NeedleDropper soundtrack (format %s)."], quote(fmt))
  end
  local version = value.version
  if version == nil then version = Share.PACK_VERSION end
  version = St.IntValue(version)
  if not version then return nil, "invalid", L["the soundtrack format version is not a whole number."] end
  if version > Share.PACK_VERSION then return nil, "newer" end
  if version < Share.PACK_VERSION then
    return nil, "invalid", format(L["unsupported soundtrack format version %s."], tostring(version))
  end

  local pack = { format = Share.PACK_FORMAT, version = Share.PACK_VERSION }
  local texts = { { "name", false }, { "author", false }, { "description", true } }
  for n = 1, #texts do
    local k, multiline = texts[n][1], texts[n][2]
    local t, code = "", nil
    if value[k] ~= nil then
      t, code = St.CleanText(value[k], St.MAX[k], multiline)
      if not t then return nil, "invalid", St.TextError(k, code, St.MAX[k]) end
    end
    pack[k] = t
  end
  if pack.name == "" then return nil, "invalid", L["the soundtrack needs a name."] end
  local tags, terr, dup = St.CleanTags(value.tags)
  if tags == false then return nil, "invalid", terr end
  if dup then w.add(L["A tag listed twice was kept once."]) end
  pack.tags = tags or {}
  local homepage, herr = St.CleanHomepage(value.homepage)
  if not homepage then return nil, "invalid", herr end
  if homepage ~= "" then pack.homepage = homepage end
  if value.id == nil or value.id == "" then
    local id = Share.DerivePackId(pack.name)
    if id == "" then
      return nil, "invalid", L["the soundtrack has no id and none can be made from its name."]
    end
    pack.id = id
  elseif ns.Sets.IsValidId(value.id) then
    pack.id = value.id
  else
    return nil, "invalid", format(L["the soundtrack id %s is not valid (1-40 of a-z, 0-9 and -)."], quote(value.id))
  end
  if value.revision ~= nil then
    local r = value.revision
    if not isWholeNumber(r) or r < 0 then
      return nil, "invalid", L["the revision must be a whole number of 0 or more."]
    end
    pack.revision = r
  end
  local drops = value.drops
  if type(drops) ~= "table" or not isArray(drops) then return nil, "invalid", L["the soundtrack has no list of drops."] end
  if #drops == 0 then return nil, "invalid", L["the soundtrack has no drops."] end
  if #drops > Share.MAX_DROPS then
    return nil, "invalid", format(L["the soundtrack has %d drops; at most %d are allowed."], #drops, Share.MAX_DROPS)
  end
  local outDrops, models, seen = {}, {}, {}
  for i = 1, #drops do
    local j, nd, detail = validateDrop(i, drops[i], w)
    if j == nil then return nil, nd, detail end
    if j then
      if seen[nd.id] then
        return nil, "invalid", format(L["drops %d and %d are for the same place (%s)."], seen[nd.id], i, nd.id)
      end
      seen[nd.id] = i
      outDrops[#outDrops + 1] = j
      models[#models + 1] = nd
    end
  end
  if #outDrops == 0 then return nil, "invalid", L["the soundtrack has no drops."] end
  pack.drops = outDrops
  return pack, w.result(), models
end

function Share.Validate(value)
  local pack, a, b = validatePack(value)
  if not pack then return nil, a, b end
  return pack, a
end

-- ================================================================== decoding ==

function Share.Decode(text)
  if type(text) ~= "string" then return nil, "notcode" end
  if #text > Share.MAX_TEXT then return nil, "toobig" end
  if find(text, ZERO_WIDTH_SPACE, 1, true) then text = gsub(text, ZERO_WIDTH_SPACE, "") end
  if find(text, BYTE_ORDER_MARK, 1, true) then text = gsub(text, BYTE_ORDER_MARK, "") end
  if find(text, NO_BREAK_SPACE, 1, true) then text = gsub(text, NO_BREAK_SPACE, " ") end
  local s, e, ver = find(text, "!NeedleDropper:([0-9]+)!")
  if not s then
    local name = Share.ForeignName(text)
    if name then return nil, "foreign:" .. name end
    return nil, "notcode"
  end
  local v = tonumber(ver)
  if v ~= Share.CONTAINER_VERSION then
    if (v or 0) > Share.CONTAINER_VERSION then return nil, "newer" end
    return nil, "damaged"
  end
  -- Explicit byte ranges: %w / %s would follow the C locale.
  local body, term = match(text, "^([A-Za-z0-9+/=_ \t\r\n\f\v%-]*)(!?)", e + 1)
  -- A missing final "!" turns "damaged" into "the code was cut off".
  local broken = term == "" and "cutoff" or "damaged"
  local bytes = ns.Base64.Decode((gsub(body, "[ \t\r\n\f\v=]", "")))
  if not bytes then return nil, broken end
  if #bytes > Share.MAX_BYTES then return nil, "toobig" end
  local json = bytes
  if sub(bytes, 1, 1) ~= "{" then
    -- zlib header (RFC 1950): CM = 8 (deflate) and FCHECK.
    local c1, c2 = byte(bytes, 1, 2)
    if not c2 or c1 % 16 ~= 8 or (c1 * 256 + c2) % 31 ~= 0 then return nil, broken end
    local util = C_EncodingUtil
    if not (util and util.DecompressString) then
      return nil, "damaged", L["This game client can't unpack share codes."]
    end
    local ok, out = pcall(util.DecompressString, bytes, zlibMethod())
    if not ok or type(out) ~= "string" then return nil, broken end
    json = out
  end
  if #json > Share.MAX_JSON then return nil, "toobig" end
  local value, jerr = ns.Json.Decode(json, { maxBytes = Share.MAX_JSON, maxDepth = Share.MAX_DEPTH })
  if jerr then return nil, broken end
  -- JSON that is not an object (a list, a string, null) is not a pack at all.
  if type(value) ~= "table" or (next(value) ~= nil and value[1] ~= nil) then return nil, "damaged" end
  return Share.Validate(value)
end

-- ================================================================== encoding ==

-- JSON pack drop for a stored drop (DESIGN 5.2 writer rules: no null, no empty lists, zero
-- start and length left out, spotify as a URI, file + inGameFile). A turned-off place is
-- shared as just its place.
local function packDrop(d)
  if d.disabled == true then
    return { disabled = true, zone = d.mapID, areaID = d.areaID, instanceID = d.instanceID,
             subzone = (d.subzone ~= "" and d.subzone) or nil }
  end
  local j = {
    zone = d.mapID, areaID = d.areaID, instanceID = d.instanceID, subzone = d.subzone, label = d.label,
    artist = d.artist, title = d.title, youtube = d.youtube, volume = d.volume, note = d.note,
  }
  if type(d.spotify) == "string" and d.spotify ~= "" then j.spotify = "spotify:track:" .. d.spotify end
  if type(d.file) == "string" and d.file ~= "" then
    j.file = d.file
    j.inGameFile = true
  end
  if type(d.startMs) == "number" and d.startMs > 0 then j.startMs = d.startMs end
  if type(d.durationMs) == "number" and d.durationMs > 0 then j.durationMs = d.durationMs end
  if type(d.tags) == "table" and #d.tags > 0 then j.tags = copy(d.tags) end
  for k, v in pairs(j) do
    if v == "" then j[k] = nil end
  end
  return j
end

function Share.FromSet(ref)
  local St = S()
  local set = St and St.Get(ref)
  if not set then return nil, 0 end
  local pack = {
    format = Share.PACK_FORMAT, version = Share.PACK_VERSION, id = set.packId or set.id, name = set.name,
    author = set.author or "", description = set.description or "", tags = copy(set.tags) or {},
    revision = set.revision or 0, drops = {},
  }
  if type(set.homepage) == "string" and set.homepage ~= "" then pack.homepage = set.homepage end
  local omitted = 0
  for i = 1, #set.drops do
    local d = set.drops[i]
    if St.IsDraft(d) then omitted = omitted + 1 else pack.drops[#pack.drops + 1] = packDrop(d) end
  end
  return pack, omitted
end

local ENCODER_BROKEN = "Sharing doesn't work on this game client: its compressor failed NeedleDropper's self-check."
local TOO_BIG = "This soundtrack is too big for a share code: split it into smaller soundtracks."

function Share.Encode(pack)
  local norm, reason, detail = validatePack(pack)
  if not norm then
    if reason == "invalid" and type(pack) == "table" and type(pack.drops) == "table" and #pack.drops == 0 then
      return nil, L["There is nothing to share yet: finish a drop (artist and title) first."]
    end
    return nil, Share.ReasonText(reason, detail)
  end
  local json, jerr = ns.Json.Encode(norm, { sortKeys = true })
  if not json then return nil, tostring(jerr) end
  if #json > Share.MAX_JSON then return nil, L[TOO_BIG] end
  local util = C_EncodingUtil
  if not (util and util.CompressString and util.DecompressString) then return nil, L[ENCODER_BROKEN] end
  local ok, z = pcall(util.CompressString, json, zlibMethod(), sizeLevel())
  if not ok or type(z) ~= "string" or z == "" then return nil, L[ENCODER_BROKEN] end
  local ok2, back = pcall(util.DecompressString, z, zlibMethod())
  if not (ok2 and back == json) then return nil, L[ENCODER_BROKEN] end
  if #z > Share.MAX_BYTES then return nil, L[TOO_BIG] end
  local b64 = ns.Base64.Encode(z, false, true)
  local code = Share.PREFIX .. b64 .. "!"
  if #code > Share.MAX_TEXT then return nil, L[TOO_BIG] end
  return code
end

-- ================================================================= importing ==

-- Model drops ready to store: labels filled in for the player's client.
local function storedDrops(models)
  local St = S()
  local out = {}
  for i = 1, #models do
    local d = copy(models[i])
    if not d.label then d.label = St.DefaultLabel(d) end
    out[i] = d
  end
  return out
end

local function partsFor(norm, name)
  return {
    name = name or norm.name, author = norm.author, description = norm.description, tags = norm.tags,
    homepage = norm.homepage, revision = norm.revision or 0, packId = norm.id, packRevision = norm.revision or 0,
    packName = norm.name, imported = time(),
  }
end

-- Adds the pack as a new soundtrack of your own, turned off. Returns its ref.
function Share.Import(pack, opts)
  local St = S()
  local norm, reason, models = validatePack(pack)
  if not norm then return nil, Share.ReasonText(reason, models) end
  local name
  if opts and opts.name ~= nil and opts.name ~= "" then
    local code
    name, code = St.CleanText(opts.name, St.MAX.name, false)
    if not name then return nil, St.TextError("name", code, St.MAX.name) end
    if name == "" then name = nil end
  end
  return St.CreateFromParts(partsFor(norm, name), storedDrops(models))
end

-- The soundtrack of your own this pack is a newer revision of: same pack id, the same
-- non-empty author and a higher revision. nil when there is none.
function Share.CanUpdate(pack)
  local norm = validatePack(pack)
  if not norm or norm.author == "" then return nil end
  local u = ns.db and ns.db.soundtracks
  if type(u) ~= "table" then return nil end
  local best
  for id, set in pairs(u) do
    if type(set) == "table" and (set.packId or set.id) == norm.id and set.author == norm.author then
      local have = set.packRevision or set.revision or 0
      if (norm.revision or 0) > have and (not best or id < best) then best = id end
    end
  end
  return best and ("user:" .. best) or nil
end

-- Replaces ref's drops and details with the pack (one undo step); keeps on/off and order.
function Share.Update(ref, pack)
  local St = S()
  local norm, reason, models = validatePack(pack)
  if not norm then return nil, Share.ReasonText(reason, models) end
  local kind = St.ParseRef(ref)
  if kind ~= "user" or not St.Exists(ref) then return nil, L["That soundtrack no longer exists."] end
  return St.ReplaceFromParts(ref, partsFor(norm), storedDrops(models))
end

-- ================================================================== preview ==

local function sources(d)
  local s = ""
  if d.spotify then s = s .. "S" end
  if d.youtube then s = s .. "Y" end
  if d.file then s = s .. "F" end
  return s
end

-- For the Import dialog: { name, author, revision, id, dropCount, places = { first 12:
-- { id, label, sources = "SYF" letters, disabled } }, warnings = {...}, update = ref | nil,
-- overrides = number of places now played by your active soundtracks that this would take
-- over if turned on at the top, unknownZones, files, missingFiles, byName }.
function Share.Preview(pack)
  local St = S()
  local norm, reason, models = validatePack(pack)
  if not norm then return nil, reason, models end
  local warnings = reason
  local info = {
    name = norm.name, author = norm.author, revision = norm.revision or 0, id = norm.id, dropCount = #models,
    places = {}, warnings = {}, update = Share.CanUpdate(norm), overrides = 0, unknownZones = 0, files = 0,
    missingFiles = 0, byName = 0,
  }
  local unknown, zonesSeen = {}, {}
  for i = 1, #models do
    local d = models[i]
    if i <= Share.PREVIEW_PLACES then
      info.places[i] = { id = d.id, label = d.label or St.DefaultLabel(d), sources = sources(d), disabled = d.disabled == true }
    end
    if d.mapID and not zonesSeen[d.mapID] then
      zonesSeen[d.mapID] = true
      local known = true
      if C_Map and C_Map.GetMapInfo then
        local ok, mi = pcall(C_Map.GetMapInfo, d.mapID)
        known = ok and type(mi) == "table"
      end
      if not known then
        info.unknownZones = info.unknownZones + 1
        unknown[#unknown + 1] = tostring(d.mapID)
      end
    end
    if d.file then
      info.files = info.files + 1
      if C_UIFileAsset and C_UIFileAsset.IsKnownFile then
        local ok, known = pcall(C_UIFileAsset.IsKnownFile, St.MEDIA_PATH .. d.file)
        if ok and known == false then info.missingFiles = info.missingFiles + 1 end
      end
    end
    if string.sub(d.id, 1, 4) == "sub:" then info.byName = info.byName + 1 end
    local winner = St.Winner(d.id)
    if winner then info.overrides = info.overrides + 1 end
  end
  if info.unknownZones > 0 then
    info.warnings[#info.warnings + 1] = format(L["Zones this game doesn't know: %s."], table.concat(unknown, ", "))
  end
  if info.files > 0 then
    if info.missingFiles > 0 then
      info.warnings[#info.warnings + 1] = format(L["%d drops play in-game files you don't have: copy them into Interface\\AddOns\\NeedleDropper_Local\\media and restart the game."], info.missingFiles)
    else
      info.warnings[#info.warnings + 1] = format(L["%d drops play in-game files: they only play if you have the same files in NeedleDropper_Local\\media."], info.files)
    end
  end
  if info.byName > 0 then
    info.warnings[#info.warnings + 1] = format(L["%d landmarks are saved by name: they only work on clients in the language they were made in."], info.byName)
  end
  if info.overrides > 0 then
    info.warnings[#info.warnings + 1] = format(L["Turned on at the top, it would change the song at %d places your soundtracks already cover."], info.overrides)
  end
  for i = 1, #warnings do info.warnings[#info.warnings + 1] = warnings[i] end
  return info
end

return Share
