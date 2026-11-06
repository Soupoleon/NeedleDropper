local ADDON, ns = ...
-- Location: where is the player? Normalized zone uiMapID, subzone text, instance info and
-- the drops that match right now. Re-reads 0.5 s after any zone event (debounced) and
-- retries while the map API still returns nil right after login.

local L = {}
ns.Location = L

local DEBOUNCE = 0.5          -- seconds to wait after the last zone event before re-reading
local MAX_DEBOUNCE = 2.0      -- never postpone a read for longer than this under event spam
local RETRY_DELAY = 1.0       -- map API returned nil -> retry after this long
local MAX_TRIES = 8
local SUBZONE_RETRIES = 3     -- subzone text is often "" for a moment at login
local ZONE_TYPE = 3           -- Enum.UIMapType.Zone

L.state = {
  ready = false,              -- at least one successful read
  rawMapID = nil,             -- C_Map.GetBestMapForUnit("player")
  zoneMapID = nil,            -- normalized zone uiMapID (nil = no zone key)
  zoneKey = nil,              -- "zone:<uiMapID>"
  zoneName = "",
  subzone = "",
  subLower = "",
  inInstance = false,
  instanceType = "none",
  instanceID = nil,
  instanceName = nil,
  zoneEnteredAt = 0,
  subEnteredAt = 0,
  instanceEnteredAt = 0,
  readAt = 0,
}

local pendingToken = 0
local pendingSince = nil      -- time of the first zone event of the current burst
local firstEventAt = nil
local nilTries = 0
local subTries = 0
L.loginAt = nil

local function call(fn, ...)
  if type(fn) ~= "function" then return false end
  return pcall(fn, ...)
end

-- Walks parentMapID up while mapType > Zone. Returns zoneMapID, mapInfo or nil.
function L.NormalizeZone(mapID)
  if not (C_Map and C_Map.GetMapInfo) then return nil end
  local id = mapID
  local ok, info = call(C_Map.GetMapInfo, id)
  if not ok or type(info) ~= "table" then return nil end
  local guard = 0
  while info and type(info.mapType) == "number" and info.mapType > ZONE_TYPE
        and type(info.parentMapID) == "number" and info.parentMapID > 0 and guard < 16 do
    guard = guard + 1
    id = info.parentMapID
    ok, info = call(C_Map.GetMapInfo, id)
    if not ok or type(info) ~= "table" then return nil end
  end
  if not info or type(info.mapType) ~= "number" or info.mapType < ZONE_TYPE then return nil end
  return id, info
end

-- One raw read of every location API (all guarded).
function L.Snapshot()
  local snap = { inInstance = false, instanceType = "none" }
  local ok, inInst, instType = call(IsInInstance)
  if ok and not ns.IsSecret(inInst) then
    snap.inInstance = inInst and true or false
    snap.instanceType = ns.SafeString(instType) or (snap.inInstance and "party" or "none")
  end
  if snap.inInstance then
    local ok2, name, _, _, _, _, _, _, instanceID = call(GetInstanceInfo)
    if ok2 then
      snap.instanceName = ns.SafeString(name)
      snap.instanceID = ns.SafeNumber(instanceID)
    end
  end
  if C_Map and C_Map.GetBestMapForUnit then
    local ok3, id = call(C_Map.GetBestMapForUnit, "player")
    if ok3 then snap.rawMapID = ns.SafeNumber(id) end
  end
  if snap.rawMapID then
    local zid, info = L.NormalizeZone(snap.rawMapID)
    snap.zoneMapID = zid
    snap.zoneInfoName = info and ns.SafeString(info.name) or nil
  end
  local okz, zone = call(GetZoneText)
  snap.zoneName = (okz and ns.SafeString(zone)) or snap.zoneInfoName or ""
  local oks, sub = call(GetSubZoneText)
  snap.subzone = (oks and ns.SafeString(sub)) or ""
  snap.subLower = string.lower(snap.subzone)
  return snap
end

local function scheduleRetry(reason, since)
  C_Timer.After(RETRY_DELAY, function() L.Refresh(reason, since) end)
end

-- Reads the location now and fires ND_LOCATION(state, changes) when something changed.
function L.Refresh(reason, since)
  local now = GetTime()
  since = since or pendingSince or now
  pendingSince = nil
  local snap = L.Snapshot()
  local st = L.state

  -- The map API can return nil right after login / loading screens: keep the old
  -- location and retry a few times instead of dropping the zone key.
  if snap.rawMapID == nil and not snap.inInstance then
    if nilTries < MAX_TRIES then
      nilTries = nilTries + 1
      ns.Debug("map not available yet (%s), retry %d/%d", tostring(reason), nilTries, MAX_TRIES)
      scheduleRetry("retry", since)
      return
    end
  end
  nilTries = 0

  if snap.subzone == "" and not snap.inInstance and L.loginAt and now - L.loginAt < 10
     and subTries < SUBZONE_RETRIES then
    subTries = subTries + 1
    scheduleRetry("subzone-retry", since)
  end

  local first = not st.ready
  local zoneChanged = first or snap.zoneMapID ~= st.zoneMapID
  local instChanged = first or snap.inInstance ~= st.inInstance or snap.instanceID ~= st.instanceID
  local subChanged = zoneChanged or instChanged or snap.subLower ~= st.subLower

  st.rawMapID = snap.rawMapID
  st.zoneMapID = snap.zoneMapID
  st.zoneKey = snap.zoneMapID and ("zone:" .. snap.zoneMapID) or nil
  st.zoneName = snap.zoneName
  st.subzone = snap.subzone
  st.subLower = snap.subLower
  st.inInstance = snap.inInstance
  st.instanceType = snap.instanceType
  st.instanceID = snap.instanceID
  st.instanceName = snap.instanceName
  st.readAt = now
  if zoneChanged or instChanged then st.zoneEnteredAt = since end
  if instChanged then st.instanceEnteredAt = since end
  if subChanged then st.subEnteredAt = since end
  st.ready = true

  if zoneChanged or instChanged or subChanged then
    ns.Debug("location: %s / '%s'%s (%s)", tostring(st.zoneKey), st.subzone,
      st.inInstance and (" instance " .. tostring(st.instanceID)) or "", tostring(reason))
    ns.Fire("ND_LOCATION", st, { zone = zoneChanged, sub = subChanged, instance = instChanged,
                                 first = first, reason = reason })
  end
end

-- Debounced re-read: waits DEBOUNCE seconds after the last zone event.
function L.Schedule(reason)
  local now = GetTime()
  if not pendingSince then
    pendingSince = now
    firstEventAt = now
  end
  pendingToken = pendingToken + 1
  local token = pendingToken
  local delay = DEBOUNCE
  if firstEventAt and now - firstEventAt + DEBOUNCE > MAX_DEBOUNCE then
    delay = math.max(0, MAX_DEBOUNCE - (now - firstEventAt))
  end
  C_Timer.After(delay, function()
    if token == pendingToken then
      firstEventAt = nil
      L.Refresh(reason)
    end
  end)
end

-- Drops matching the current location, highest priority first:
-- instance drop > landmark drop for the subzone > zone drop.
function L.Candidates()
  local st = L.state
  local out = {}
  if not st.ready then return out end
  local M = ns.Map
  if st.inInstance then
    if st.instanceID and ns.settings and ns.settings.instanceDrops then
      local d = M.instanceDrops[st.instanceID]
      if d then out[#out + 1] = d end
    end
    return out
  end
  local sd = ns.FindSubzoneDrop(st.zoneMapID, st.subLower)
  if sd then out[#out + 1] = sd end
  if st.zoneMapID then
    local zd = M.zoneDrops[st.zoneMapID]
    if zd then out[#out + 1] = zd end
  end
  return out
end

-- Lines for /needle where (pack authoring helper).
function L.Describe()
  local snap = L.Snapshot()
  local lines = {}
  local mapType = ""
  if snap.rawMapID and C_Map and C_Map.GetMapInfo then
    local ok, info = call(C_Map.GetMapInfo, snap.rawMapID)
    if ok and type(info) == "table" then
      mapType = string.format(" (%s, mapType %s)", tostring(ns.SafeString(info.name) or "?"), tostring(info.mapType))
    end
  end
  lines[#lines + 1] = string.format("best map: %s%s", tostring(snap.rawMapID or "none"), mapType)
  if snap.zoneMapID then
    lines[#lines + 1] = string.format("zone key: zone:%d  (%s)", snap.zoneMapID, snap.zoneInfoName or snap.zoneName)
  else
    lines[#lines + 1] = "zone key: none (not inside a zone map)"
  end
  if snap.subzone ~= "" then
    local slug = string.gsub(string.lower(snap.subzone), "[^a-z0-9]+", "-")
    slug = string.gsub(string.gsub(slug, "^%-+", ""), "%-+$", "")
    lines[#lines + 1] = string.format("subzone: %s  (id by name: sub:%s:%s)", snap.subzone,
      tostring(snap.zoneMapID or "?"), slug)
  else
    lines[#lines + 1] = "subzone: none"
  end
  if snap.inInstance then
    lines[#lines + 1] = string.format("instance: %s (%s), instanceID %s -> id inst:%s",
      tostring(snap.instanceName or "?"), tostring(snap.instanceType), tostring(snap.instanceID),
      tostring(snap.instanceID))
  else
    lines[#lines + 1] = "instance: no"
  end
  if snap.zoneMapID and ns.Discovery and ns.Discovery.ExploredAreasHere then
    local areas = ns.Discovery.ExploredAreasHere(snap.zoneMapID)
    if areas then
      local list = {}
      for areaID in pairs(areas) do
        list[#list + 1] = string.format("%d %s", areaID, ns.AreaName(areaID) or "?")
      end
      table.sort(list)
      if #list > 0 then lines[#lines + 1] = "explored areas here: " .. table.concat(list, ", ") end
    end
  end
  local cands = L.Candidates()
  if #cands == 0 then
    lines[#lines + 1] = "matching drops: none"
  else
    local parts = {}
    for i = 1, #cands do
      local d = cands[i]
      local state = ""
      if ns.Director and ns.Director.IsDone and ns.Director.IsDone(d) then state = " (heard)" end
      parts[#parts + 1] = string.format("%s #%d \"%s\"%s", d.id, d.n, d.label, state)
    end
    lines[#lines + 1] = "matching drops: " .. table.concat(parts, ", ")
  end
  return lines
end

for _, event in ipairs({ "ZONE_CHANGED", "ZONE_CHANGED_INDOORS", "ZONE_CHANGED_NEW_AREA",
                         "PLAYER_MAP_CHANGED", "SHARD_TRANSFER", "LOADING_SCREEN_DISABLED" }) do
  ns.On(event, function(ev) L.Schedule(ev) end)
end

ns.On("PLAYER_ENTERING_WORLD", function(ev, isInitialLogin, isReloadingUi)
  if not L.loginAt then L.loginAt = GetTime() end
  L.Schedule(ev)
end)
