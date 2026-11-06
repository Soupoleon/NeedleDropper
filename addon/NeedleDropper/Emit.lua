local ADDON, ns = ...
-- Emit: the single place events leave the addon (DESIGN-v1.2 2.2, CONTRACTS-v1.2 2.8).
--
--   ns.BuildEvent(evType, drop, extraFlags, opts) -> Protocol2 event table for a map drop
--   ns.Emit(evType, drop|nil, extraFlags, opts)   -> bumps and persists seq (ns.db.lastSeq),
--       computes the live flags, records ns.Events / ns.LastEvent and hands the event to
--       ns.Beacon.Show(ev) and ns.ChatLink.Send(ev); returns the event (with .seq).
--
-- The event carries the song itself (Protocol2.lua, docs/PROTOCOL-v2.md): dropId and its
-- placeKey, Spotify / YouTube ids, artist + title (the search query), start, play length,
-- volume, the place label localised by the client, and the soundtrack id. Each transport
-- encodes it with Protocol2: the chat line with every field, the beacon fitted to its rows.
--
-- Loaded after Protocol2.lua and Core.lua (ns.SafeBool, ns.AreaName, ns.db) and before
-- Location, Beacon, ChatLink and Director (CONTRACTS 2.1): those modules are only reached
-- lazily, at call time.

local P2 = ns.Protocol2
local EV, FLAG = P2.EV, P2.FLAG
local floor = math.floor

local MAX_EVENTS = 200           -- ns.Events keeps this many, oldest first
local TEST_LABEL = "NeedleDropper Test"
local FLAG_BITS = { FLAG.IN_COMBAT, FLAG.IN_INSTANCE, FLAG.ON_TAXI, FLAG.DEAD, FLAG.LOCAL_PLAYBACK,
                    FLAG.DROP_ACTIVE, FLAG.TEST }

ns.Events = ns.Events or {}      -- emitted events, oldest first (capped)

local E = { seq = 0 }            -- seq fallback while ns.db is not loaded yet
ns.EmitState = E

local function isnum(v)
  return type(v) == "number" and v == v and v ~= math.huge and v ~= -math.huge
end

local function nonEmpty(v)
  v = ns.SafeString(v)
  if v and v ~= "" then return v end
  return nil
end

local function call(fn, ...)
  if type(fn) ~= "function" then return false end
  return pcall(fn, ...)
end

-- Reports an error from a transport without stopping the other one.
local function report(err)
  local h = geterrorhandler and geterrorhandler()
  if h then h(err) end
end

-- ------------------------------------------------------------------ flags ----

local function addFlag(f, bit)
  if not P2.HasFlag(f, bit) then f = f + bit end
  return f
end

-- Live flags (as v1's Director.ComputeFlags): combat, instance, taxi, dead from the unit
-- APIs; DROP_ACTIVE / LOCAL_PLAYBACK / TEST from the Director's active drop; plus every
-- known bit of `extra`.
function ns.ComputeFlags(extra)
  local f = 0
  if ns.SafeBool(UnitAffectingCombat, "player") then f = addFlag(f, FLAG.IN_COMBAT) end
  if ns.SafeBool(IsInInstance) then f = addFlag(f, FLAG.IN_INSTANCE) end
  if ns.SafeBool(UnitOnTaxi, "player") then f = addFlag(f, FLAG.ON_TAXI) end
  if ns.SafeBool(UnitIsDeadOrGhost, "player") then f = addFlag(f, FLAG.DEAD) end
  local D = ns.Director
  local a = type(D) == "table" and D.active or nil
  if a then
    f = addFlag(f, FLAG.DROP_ACTIVE)
    if a.isLocal then f = addFlag(f, FLAG.LOCAL_PLAYBACK) end
    if a.isTest then f = addFlag(f, FLAG.TEST) end
  end
  if isnum(extra) then
    for i = 1, #FLAG_BITS do
      if P2.HasFlag(extra, FLAG_BITS[i]) then f = addFlag(f, FLAG_BITS[i]) end
    end
  end
  return f
end

-- ------------------------------------------------------- preferred provider ----

local function spotifyConnected(v)
  if v == true or v == "connected" then return true end
  if type(v) == "table" and not ns.IsSecret(v) then
    return v.connected == true or v.state == "connected" or v.status == "connected"
  end
  return false
end

-- "spotify" when the NeedleDropper app says Spotify is connected (ns.Companion status, else
-- the raw NeedleDropper_CompanionData written by the app), otherwise "youtube" (DESIGN 2.3
-- fit rule step 7: the provider id the beacon keeps when only one fits).
function ns.PreferredProvider()
  local C = ns.Companion
  if type(C) == "table" and type(C.Status) == "function" then
    local ok, st = pcall(C.Status)
    if ok and type(st) == "table" then
      return spotifyConnected(st.spotify) and "spotify" or "youtube"
    end
  end
  local data = NeedleDropper_CompanionData
  if type(data) == "table" and type(data.status) == "table" and spotifyConnected(data.status.spotify) then
    return "spotify"
  end
  return "youtube"
end

-- ------------------------------------------------------------- place label ----

local KIND_OF_PREFIX = { zone = "zone", area = "subzone", sub = "subzone", inst = "instance" }

local function kindOf(drop)
  local k = drop.kind
  if k == "zone" or k == "subzone" or k == "instance" or k == "test" then return k end
  local id = ns.SafeString(drop.id)
  if id == "test" then return "test" end
  return id and KIND_OF_PREFIX[string.match(id, "^(%a+):") or ""] or nil
end

local function isTestDrop(drop)
  return drop.id == "test" or drop.kind == "test"
end

local function mapName(mapID)
  if not (isnum(mapID) and C_Map and C_Map.GetMapInfo) then return nil end
  local ok, info = pcall(C_Map.GetMapInfo, mapID)
  if not ok or ns.IsSecret(info) or type(info) ~= "table" then return nil end
  return nonEmpty(info.name)
end

local function areaName(areaID)
  if not isnum(areaID) then return nil end
  if ns.AreaName then return nonEmpty(ns.AreaName(areaID)) end
  if not (C_Map and C_Map.GetAreaInfo) then return nil end
  local ok, name = pcall(C_Map.GetAreaInfo, areaID)
  return ok and nonEmpty(name) or nil
end

-- The live subzone text, but only while it names this landmark drop.
local function liveSubzoneFor(drop)
  local want = drop.subzoneLower or (nonEmpty(drop.subzone) and string.lower(drop.subzone))
  if not want then return nil end
  local ok, sub = call(GetSubZoneText)
  sub = ok and nonEmpty(sub) or nil
  if sub and string.lower(sub) == want then return sub end
  return nil
end

-- GetInstanceInfo() describes wherever the player is, so its name is used only inside the
-- drop's own instance.
local function instanceName(instanceID)
  if not isnum(instanceID) then return nil end
  local ok, inside = call(IsInInstance)
  if not ok or ns.IsSecret(inside) or not inside then return nil end
  local ok2, name, _, _, _, _, _, _, id = call(GetInstanceInfo)
  if not ok2 or ns.SafeNumber(id) ~= instanceID then return nil end
  return nonEmpty(name)
end

-- Place label for the companion's player card, localised by the client where it can be
-- (zone -> map name, landmark -> area name or the live subzone text naming it, instance ->
-- instance name while inside it), else the drop's own label / subzone. Never character,
-- realm or chat text (DESIGN 2.3 privacy).
function ns.PlaceLabel(drop)
  if type(drop) ~= "table" then return nil end
  local kind = kindOf(drop)
  if kind == "test" then return nonEmpty(drop.label) or TEST_LABEL end
  local name
  if kind == "zone" then
    name = mapName(drop.mapID)
  elseif kind == "subzone" then
    name = areaName(drop.areaID) or liveSubzoneFor(drop)
  elseif kind == "instance" then
    name = instanceName(drop.instanceID)
  end
  return name or nonEmpty(drop.label) or nonEmpty(drop.subzone)
end

-- ------------------------------------------------------------------ events ----

local function positive(v)
  if isnum(v) and v > 0 then return v end
  return nil
end

-- Protocol2 event table for event type `evType` and map drop `drop` (DESIGN 2.2 table):
--   id -> dropId (+ placeKey), spotify, youtube, artist + title -> query, startMs,
--   durationMs -> lengthMs (absent = whole song), volume, localised place -> label,
--   soundtrack id -> setId; prefer = the provider the beacon's fit rule keeps.
-- HELLO / PING / NONE carry no drop fields; CANCEL only the identity. The test drop (id
-- "test") carries its label but no song, so nothing plays. `flags` holds only extraFlags
-- (ns.Emit replaces them with the live flags); `seq` is opts.seq or 0.
-- opts: { seq = n, prefer = "spotify"|"youtube", label = text }.
function ns.BuildEvent(evType, drop, extraFlags, opts)
  opts = type(opts) == "table" and opts or {}
  local ev = {
    type = evType,
    seq = isnum(opts.seq) and floor(opts.seq) % 256 or 0,
    flags = isnum(extraFlags) and floor(extraFlags) % 256 or 0,
  }
  local withDrop = evType == EV.ARM or evType == EV.FIRE or evType == EV.CANCEL
  if withDrop and type(drop) == "table" then
    local id = nonEmpty(drop.id)
    if id then
      ev.dropId = id
      ev.placeKey = P2.PlaceKey(id)
    end
    if evType ~= EV.CANCEL then
      if isTestDrop(drop) then
        ev.label = nonEmpty(opts.label) or ns.PlaceLabel(drop)
      else
        ev.spotify = nonEmpty(drop.spotify)
        ev.youtube = nonEmpty(drop.youtube)
        ev.artist = nonEmpty(drop.artist)
        ev.title = nonEmpty(drop.title)
        ev.startMs = positive(drop.startMs)
        ev.lengthMs = positive(drop.durationMs)
        ev.volume = isnum(drop.volume) and drop.volume or nil
        ev.label = nonEmpty(opts.label) or ns.PlaceLabel(drop)
        ev.setId = nonEmpty(drop.setId)
      end
    end
  end
  local prefer = opts.prefer
  if prefer ~= "spotify" and prefer ~= "youtube" then prefer = ns.PreferredProvider() end
  ev.prefer = prefer
  return ev
end

-- Emits one event to every transport (the v1 signature and return value).
-- opts: { noPad = bool, forceChat = bool } plus the ns.BuildEvent options.
function ns.Emit(evType, drop, extraFlags, opts)
  local ev = ns.BuildEvent(evType, drop, extraFlags, opts)
  local db = ns.db
  local last = E.seq
  if type(db) == "table" and isnum(db.lastSeq) then last = db.lastSeq end
  local seq = (floor(last) + 1) % 256
  E.seq = seq
  if type(db) == "table" then db.lastSeq = seq end
  ev.seq = seq
  ev.flags = ns.ComputeFlags(extraFlags)
  ev.t = GetTime()
  ns.LastEvent = ev
  local evs = ns.Events
  evs[#evs + 1] = ev
  if #evs > MAX_EVENTS then table.remove(evs, 1) end
  ns.Debug("emit %s seq=%d id=%s flags=%d", P2.EV_NAME[evType] or tostring(evType), seq,
    tostring(ev.dropId or "-"), ev.flags)
  local B, C = ns.Beacon, ns.ChatLink
  if type(B) == "table" and type(B.Show) == "function" then
    local ok, err = pcall(B.Show, ev)
    if not ok then report(err) end
  end
  if type(C) == "table" and type(C.Send) == "function" then
    local ok, err = pcall(C.Send, ev, { noPad = opts and opts.noPad, force = opts and opts.forceChat })
    if not ok then report(err) end
  end
  return ev
end
