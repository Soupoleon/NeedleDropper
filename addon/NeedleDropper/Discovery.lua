local ADDON, ns = ...
-- Discovery: "is this the first time?" signals from the server.
--  * primary: UI_INFO_MESSAGE whose GetGameMessageInfo(errorType) name is
--    ERR_ZONE_EXPLORED / ERR_ZONE_EXPLORED_XP
--  * backup: CHAT_MSG_SYSTEM matched against patterns built from those global strings
--  * MAP_EXPLORATION_UPDATED bookkeeping
--  * exploration-history helpers (C_MapExplorationInfo) for history mode "respect".
-- Every payload is checked with issecretvalue before it is looked at.

local Dsc = {}
ns.Discovery = Dsc

local CORRELATE = 2.0          -- a discovery up to 2 s before a zone change still counts for it
local LOGIN_GRACE = 5.0        -- MAP_EXPLORATION_UPDATED also fires at login: ignore it then
local DISCOVERY_NAMES = { ERR_ZONE_EXPLORED = true, ERR_ZONE_EXPLORED_XP = true }

Dsc.last = nil                 -- { name =, lower =, t =, source = }
Dsc.count = 0
Dsc.explorationSeen = false    -- MAP_EXPLORATION_UPDATED fired at least once this session
Dsc.explorationCount = 0
Dsc.lastExplorationAt = nil
Dsc.loginAt = nil

-- Converts a printf-style global string into an anchored Lua pattern:
-- magic characters escaped, %s -> (.+), %d -> %d+ (positional %1$s forms too).
function Dsc.FormatToPattern(fmt)
  local out = {}
  local i, len = 1, #fmt
  while i <= len do
    local c = string.sub(fmt, i, i)
    if c == "%" then
      local spec = string.match(fmt, "^%%(%d*%$?[%-%+ #0]*%d*%.?%d*[sdif%%])", i)
      if spec then
        local conv = string.sub(spec, -1)
        if conv == "s" then
          out[#out + 1] = "(.+)"
        elseif conv == "d" or conv == "i" then
          out[#out + 1] = "%-?%d+"
        elseif conv == "f" then
          out[#out + 1] = "%-?[%d%.]+"
        else
          out[#out + 1] = "%%"
        end
        i = i + 1 + #spec
      else
        out[#out + 1] = "%%"
        i = i + 1
      end
    elseif string.find(c, "[%^%$%(%)%.%[%]%*%+%-%?]") then
      out[#out + 1] = "%" .. c
      i = i + 1
    else
      out[#out + 1] = c
      i = i + 1
    end
  end
  return "^" .. table.concat(out) .. "$"
end

local patterns
local function buildPatterns()
  patterns = {}
  -- XP variant first: it is the more specific one.
  for _, key in ipairs({ "ERR_ZONE_EXPLORED_XP", "ERR_ZONE_EXPLORED" }) do
    local f = ns.SafeString(_G[key])
    if f and f ~= "" then patterns[#patterns + 1] = Dsc.FormatToPattern(f) end
  end
  if #patterns == 0 then
    patterns[1] = "^Discovered (.+): %-?%d+ experience gained$"
    patterns[2] = "^Discovered: (.+)$"
  end
end

-- Extracts the discovered place from a (non-secret) message, or nil.
function Dsc.ParseName(msg)
  if type(msg) ~= "string" or ns.IsSecret(msg) then return nil end
  if not patterns then buildPatterns() end
  for i = 1, #patterns do
    local name = string.match(msg, patterns[i])
    if name and name ~= "" then return name end
  end
  return nil
end

local function currentPlaceName()
  local ok, sub = pcall(GetSubZoneText)
  sub = ok and ns.SafeString(sub) or nil
  if sub and sub ~= "" then return sub end
  local ok2, zone = pcall(GetZoneText)
  zone = ok2 and ns.SafeString(zone) or nil
  return zone or ""
end

-- Records a discovery of `name` (nil = use the current subzone) and notifies the Director.
function Dsc.Record(name, source)
  local now = GetTime()
  name = ns.SafeString(name)
  if not name or name == "" then name = currentPlaceName() end
  local lower = string.lower(name)
  local last = Dsc.last
  if last and now - last.t < CORRELATE and last.lower == lower then
    return last -- the same discovery reported by both channels
  end
  Dsc.count = Dsc.count + 1
  Dsc.last = { name = name, lower = lower, t = now, source = source }
  ns.Debug("discovered '%s' (%s)", name, tostring(source))
  ns.Fire("ND_DISCOVERY", Dsc.last)
  return Dsc.last
end

-- Classifies a UI_INFO_MESSAGE. Returns errorName (string) or nil; never touches secrets.
function Dsc.MessageName(errorType)
  if ns.IsSecret(errorType) or type(errorType) ~= "number" or not GetGameMessageInfo then return nil end
  local ok, errorName = pcall(GetGameMessageInfo, errorType)
  if not ok then return nil end
  return ns.SafeString(errorName)
end

ns.On("UI_INFO_MESSAGE", function(_, errorType, message)
  local errorName = Dsc.MessageName(errorType)
  if not errorName or not DISCOVERY_NAMES[errorName] then return end
  local name = nil
  if not ns.IsSecret(message) and type(message) == "string" then name = Dsc.ParseName(message) end
  Dsc.Record(name, "info")
end)

ns.On("CHAT_MSG_SYSTEM", function(_, msg)
  if ns.IsSecret(msg) or type(msg) ~= "string" then return end
  if string.sub(msg, 1, 7) == "NDROP1 " or string.sub(msg, 1, 6) == "NDPAD " then return end
  local name = Dsc.ParseName(msg)
  if name then Dsc.Record(name, "chat") end
end)

ns.On("MAP_EXPLORATION_UPDATED", function()
  Dsc.explorationSeen = true
  Dsc.explorationCount = Dsc.explorationCount + 1
  Dsc.lastExplorationAt = GetTime()
end)

ns.On("PLAYER_ENTERING_WORLD", function()
  if not Dsc.loginAt then Dsc.loginAt = GetTime() end
end)

-- A discovery anywhere since `enteredAt` (zone entry), allowing the correlation window.
function Dsc.SinceEntry(enteredAt)
  local d = Dsc.last
  return d ~= nil and d.t >= (enteredAt or 0) - CORRELATE
end

local function nameMatches(lower, subLower, drop)
  if subLower and subLower ~= "" and lower == subLower then return true end
  if drop then
    if drop.subzoneLower and lower == drop.subzoneLower then return true end
    local area = drop.areaID and ns.AreaName(drop.areaID)
    if area and lower == string.lower(area) then return true end
  end
  return false
end

-- A discovery of this subzone (or landmark drop) since `enteredAt`.
function Dsc.ForSubzone(subLower, enteredAt, drop)
  local d = Dsc.last
  if not d or d.t < (enteredAt or 0) - CORRELATE then return false end
  return nameMatches(d.lower, subLower, drop)
end

-- Was this place discovered within the last `window` seconds? Also counts an exploration
-- update (outside the login grace period) as evidence of a fresh discovery.
function Dsc.FreshFor(subLower, drop, window)
  local now = GetTime()
  window = window or 3
  local d = Dsc.last
  if d and now - d.t <= window and nameMatches(d.lower, subLower, drop) then return true end
  local at = Dsc.lastExplorationAt
  if at and now - at <= window and (not Dsc.loginAt or at - Dsc.loginAt > LOGIN_GRACE) then return true end
  return false
end

-- true / false: has this character explored part of zone `mapID`? nil: cannot tell.
function Dsc.ExploredZone(mapID)
  local api = C_MapExplorationInfo
  if not (api and api.GetExploredMapTextures and mapID) then return nil end
  local ok, textures = pcall(api.GetExploredMapTextures, mapID)
  if not ok or ns.IsSecret(textures) or type(textures) ~= "table" then return nil end
  return #textures > 0
end

-- Set { [areaID] = true } of explored areas at the player's position in `mapID`, or nil.
function Dsc.ExploredAreasHere(mapID)
  local api = C_MapExplorationInfo
  if not (api and api.GetExploredAreaIDsAtPosition and C_Map and C_Map.GetPlayerMapPosition and mapID) then
    return nil
  end
  local ok, pos = pcall(C_Map.GetPlayerMapPosition, mapID, "player")
  if not ok or pos == nil or ns.IsSecret(pos) then return nil end
  local ok2, list = pcall(api.GetExploredAreaIDsAtPosition, mapID, pos)
  if not ok2 or ns.IsSecret(list) or type(list) ~= "table" then return nil end
  local set = {}
  for i = 1, #list do
    local id = ns.SafeNumber(list[i])
    if id then set[id] = true end
  end
  return set
end
