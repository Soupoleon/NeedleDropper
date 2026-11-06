local ADDON, ns = ...
-- Director: decides whether / when a needle drop fires (SPEC 4.3).
--
-- State machine, driven by a 1 Hz ticker plus event nudges:
--   candidate (instance > landmark > zone, mapped, not done, enabled)
--     -> ARM once per entry
--     -> FIRE when dwell, gates, cooldown and "no active drop" all allow it
--     -> CANCEL if the armed key changes first (not marked done)
--   active drop ends at its duration (or maxDropSec, or when an in-game file stops)
--     -> Audio restore -> HELLO; CANCEL on long combat, death, entering an instance,
--        /needle stop or skip.
-- ns.Emit(evType, drop, extraFlags) is the single place events leave the addon.

local D = {}
ns.Director = D

local P = ns.Protocol
local EV, FLAG = P.EV, P.FLAG
local MAX_EVENTS = 200
local LANDMARK_CHECK_DELAY = 1.5   -- wait after entering a landmark before reading history
local FRESH_WINDOW = 3.0           -- a discovery this recent means "new", whatever the map says
local HISTORY_SCAN_DELAY = 3.0     -- zone history scan this long after login...
local HISTORY_SCAN_MAX_WAIT = 10.0 -- ...once MAP_EXPLORATION_UPDATED was seen, or after this
local FLAG_EXPIRY = 900            -- cinematic / movie / control flags expire (missed STOP)
local LOCAL_GRACE = 2.0            -- ignore C_Sound.IsPlaying()==false right after starting

ns.Events = {}       -- emitted events, oldest first (capped)
ns.LastEvent = nil

D.armed = nil        -- { drop =, at = }
D.active = nil       -- { drop =, startedAt =, endsAt =, handle =, isLocal =, isTest = }
D.scanDone = false
D.loginAt = nil
D.helloSent = false
D.combatSince = nil
D.suppress = {}      -- [dropId] = entry time: /needle stop let this drop go for this visit
D.flags = { loading = nil, cinematic = nil, movie = nil, controlLost = nil } -- value = time set
local checkScheduledFor = nil
local ticker = nil

local function now() return GetTime() end

local function settings() return ns.settings end

local function addFlag(f, bit)
  if not P.HasFlag(f, bit) then f = f + bit end
  return f
end

-- ------------------------------------------------------------------ emit ----

-- Flags from live state (SPEC 3.3), plus any extra bits.
function D.ComputeFlags(extra)
  local f = 0
  if ns.SafeBool(UnitAffectingCombat, "player") then f = addFlag(f, FLAG.IN_COMBAT) end
  if ns.SafeBool(IsInInstance) then f = addFlag(f, FLAG.IN_INSTANCE) end
  if ns.SafeBool(UnitOnTaxi, "player") then f = addFlag(f, FLAG.ON_TAXI) end
  if ns.SafeBool(UnitIsDeadOrGhost, "player") then f = addFlag(f, FLAG.DEAD) end
  local a = D.active
  if a then
    f = addFlag(f, FLAG.DROP_ACTIVE)
    if a.isLocal then f = addFlag(f, FLAG.LOCAL_PLAYBACK) end
    if a.isTest then f = addFlag(f, FLAG.TEST) end
  end
  extra = extra or 0
  for _, bit in pairs(FLAG) do
    if P.HasFlag(extra, bit) then f = addFlag(f, bit) end
  end
  return f
end

-- Emits one event to every transport. opts: { noPad = bool, forceChat = bool }.
function ns.Emit(evType, drop, extraFlags, opts)
  local db = ns.db
  local seq = (((db and db.lastSeq) or D.seq or 0) + 1) % 256
  D.seq = seq
  if db then db.lastSeq = seq end
  local ev = {
    type = evType,
    seq = seq,
    n = drop and drop.n or 0,
    hash = ns.Map.hash or 0,
    flags = D.ComputeFlags(extraFlags),
    id = drop and drop.id or "",
    t = now(),
  }
  ns.LastEvent = ev
  local evs = ns.Events
  evs[#evs + 1] = ev
  if #evs > MAX_EVENTS then table.remove(evs, 1) end
  ns.Debug("emit %s seq=%d n=%d id=%s flags=%d", P.EV_NAME[evType] or tostring(evType), seq, ev.n, ev.id, ev.flags)
  if ns.Beacon then ns.Beacon.Show(ev) end
  if ns.ChatLink then
    ns.ChatLink.Send(ev, { noPad = opts and opts.noPad, force = opts and opts.forceChat })
  end
  return ev
end

-- --------------------------------------------------------------- history ----

function D.IsDone(drop)
  if not ns.db or not drop then return false end
  if settings().scope == "account" then
    return ns.db.accountDone[drop.id] ~= nil
  end
  return ns.cdb.done[drop.id] ~= nil
end

-- Records a drop as done for the character and the account (first record wins).
function D.MarkDone(drop, how)
  local t = time()
  if not ns.cdb.done[drop.id] then ns.cdb.done[drop.id] = { t = t, how = how } end
  if not ns.db.accountDone[drop.id] then ns.db.accountDone[drop.id] = { t = t, how = how } end
end

-- Brand-new characters (level 1) have their starting area pre-explored: no history.
function D.IsNewCharacter()
  local ok, level = pcall(UnitLevel, "player")
  return ok and ns.SafeNumber(level) ~= nil and level <= 1
end

-- Zone drops: once per character per drop id, mark explored zones as heard.
function D.RunHistoryScan()
  D.scanDone = true
  local scanned = ns.cdb.scanned
  local newChar = D.IsNewCharacter()
  local marked = 0
  local drops = ns.Map.drops
  for i = 1, #drops do
    local d = drops[i]
    if d.kind == "zone" and not scanned[d.id] then
      if newChar then
        scanned[d.id] = true
      else
        local explored = ns.Discovery.ExploredZone(d.mapID)
        if explored ~= nil then
          scanned[d.id] = true
          if explored and not ns.cdb.done[d.id] then
            D.MarkDone(d, "history")
            marked = marked + 1
          end
        end
      end
    end
  end
  if marked > 0 then
    ns.Debug("history: %d explored zone%s marked as heard", marked, marked == 1 and "" or "s")
  end
  return marked
end

local function maybeScan()
  if D.scanDone then return true end
  if settings().historyMode ~= "respect" then
    D.scanDone = true
    return true
  end
  if not D.loginAt then return false end
  local since = now() - D.loginAt
  if since < HISTORY_SCAN_DELAY then return false end
  if not ns.Discovery.explorationSeen and since < HISTORY_SCAN_MAX_WAIT then return false end
  D.RunHistoryScan()
  return true
end

local function needsLandmarkCheck(d)
  return d.kind == "subzone" and d.areaID ~= nil and settings().historyMode == "respect"
     and not ns.cdb.scanned[d.id]
end

-- Landmark with areaID: explored already (and not discovered just now) -> heard.
function D.RunLandmarkCheck(d)
  ns.cdb.scanned[d.id] = true
  if D.IsNewCharacter() then return false end
  local st = ns.Location.state
  if ns.Discovery.FreshFor(st.subLower, d, FRESH_WINDOW) then return false end
  local areas = ns.Discovery.ExploredAreasHere(d.mapID or st.zoneMapID)
  if areas and areas[d.areaID] then
    D.MarkDone(d, "history")
    ns.Debug("history: %s already explored, marked as heard", d.id)
    return true
  end
  return false
end

-- ----------------------------------------------------------------- gates ----

-- Entry time of the visit a drop belongs to.
function D.EntryTime(d)
  local st = ns.Location.state
  if d.kind == "instance" then return st.instanceEnteredAt end
  if d.kind == "subzone" then return st.subEnteredAt end
  return st.zoneEnteredAt
end

local function flagOn(name)
  local at = D.flags[name]
  if not at then return false end
  if now() - at > FLAG_EXPIRY then
    D.flags[name] = nil
    return false
  end
  return true
end

function D.InCinematic()
  if ns.SafeBool(InCinematic) then return true end
  if MovieFrame and MovieFrame.IsShown then
    local ok, shown = pcall(MovieFrame.IsShown, MovieFrame)
    if ok and shown and not ns.IsSecret(shown) then return true end
  end
  return flagOn("cinematic") or flagOn("movie")
end

function D.EncounterRestricted()
  if not (C_RestrictedActions and C_RestrictedActions.IsAddOnRestrictionActive) then return false end
  local kinds = Enum and Enum.AddOnRestrictionType
  if not kinds or kinds.Encounter == nil then return false end
  return ns.SafeBool(C_RestrictedActions.IsAddOnRestrictionActive, kinds.Encounter)
end

-- List of reasons the drop cannot fire right now (empty = all clear).
function D.GateReasons(d)
  local r = {}
  local s = settings()
  local onTaxi = ns.SafeBool(UnitOnTaxi, "player")
  if ns.SafeBool(UnitAffectingCombat, "player") then r[#r + 1] = "combat" end
  if ns.SafeBool(UnitIsDeadOrGhost, "player") then r[#r + 1] = "dead" end
  if s.suppressOnTaxi and onTaxi then r[#r + 1] = "taxi" end
  if D.InCinematic() then r[#r + 1] = "cinematic" end
  if flagOn("loading") then r[#r + 1] = "loading" end
  if flagOn("controlLost") and not onTaxi then r[#r + 1] = "no control" end
  if D.EncounterRestricted() then r[#r + 1] = "encounter" end
  if d and d.kind ~= "instance" and ns.Location.state.inInstance then r[#r + 1] = "instance" end
  return r
end

-- satisfied, elapsed, needed
function D.DwellState(d)
  local st = ns.Location.state
  local s = settings()
  local t = now()
  if d.kind == "instance" then
    local el = t - st.instanceEnteredAt
    return el >= s.minDwellSec, el, s.minDwellSec
  elseif d.kind == "subzone" then
    local el = t - st.subEnteredAt
    if ns.Discovery.ForSubzone(st.subLower, st.subEnteredAt, d) then return true, el, 0 end
    return el >= s.landmarkDwellSec, el, s.landmarkDwellSec
  end
  local el = t - st.zoneEnteredAt
  if ns.Discovery.SinceEntry(st.zoneEnteredAt) then return el >= s.minDwellSec, el, s.minDwellSec end
  return el >= s.dwellSec, el, s.dwellSec
end

function D.CooldownLeft()
  local cd = (settings().cooldownMin or 0) * 60
  if cd <= 0 then return 0 end
  local left = (ns.cdb.lastFireAt or 0) + cd - time()
  if left < 0 then left = 0 end
  if left > cd then left = cd end -- clock moved backwards
  return left
end

function D.CanFire(d)
  if D.active then return false end
  if not D.DwellState(d) then return false end
  if #D.GateReasons(d) > 0 then return false end
  return D.CooldownLeft() <= 0
end

-- ------------------------------------------------------------- the drops ----

-- Highest-priority eligible candidate. Returns drop or nil, plus "checking" while a
-- landmark history check is pending.
function D.PickCandidate()
  local cands = ns.Location.Candidates()
  for i = 1, #cands do
    local d = cands[i]
    if not D.IsDone(d) and D.suppress[d.id] ~= D.EntryTime(d) then
      if needsLandmarkCheck(d) then
        local due = ns.Location.state.subEnteredAt + LANDMARK_CHECK_DELAY
        if now() < due then
          if checkScheduledFor ~= due then
            checkScheduledFor = due
            C_Timer.After(due - now() + 0.01, function() D.Evaluate("landmark-check") end)
          end
          return nil, "checking"
        end
        D.RunLandmarkCheck(d)
      end
      if not D.IsDone(d) then return d end
    end
  end
  return nil
end

function D.Arm(d)
  D.armed = { drop = d, at = now() }
  ns.Debug("arm %s", d.id)
  ns.Emit(EV.ARM, d)
end

function D.Disarm(reason)
  local a = D.armed
  if not a then return end
  D.armed = nil
  ns.Debug("cancel armed %s (%s)", a.drop.id, tostring(reason))
  ns.Emit(EV.CANCEL, a.drop)
end

-- Starts playing `drop` (first-visit FIRE or a manual play). opts.isTest for manual plays.
function D.StartActive(drop, opts)
  local s = settings()
  local isTest = opts and opts.isTest or false
  local wantLocal = drop.file ~= nil and s.localPlayback
  -- replacing a playing drop: keep the music ducked unless the duck method must change
  if D.active then D.StopActive(wantLocal and ns.Audio.ducked == "volume") end
  ns.Audio.Duck({ forLocal = wantLocal })
  local isLocal, handle = false, nil
  if wantLocal then
    isLocal, handle = ns.Audio.PlayLocal(drop.file)
    if not isLocal then
      ns.Debug("in-game file for %s did not play; leaving playback to the companion", drop.id)
    end
  end
  local length = (drop.durationMs and drop.durationMs > 0) and (drop.durationMs / 1000) or s.maxDropSec
  local t = now()
  D.active = { drop = drop, startedAt = t, endsAt = t + length, handle = handle, isLocal = isLocal,
               isTest = isTest }
  if ns.Toast then ns.Toast.Show(drop) end
  return ns.Emit(EV.FIRE, drop, isTest and FLAG.TEST or 0)
end

function D.Fire(d)
  D.armed = nil
  D.MarkDone(d, "played")
  ns.AddJournal(d)
  ns.cdb.lastFireAt = time()
  ns.Debug("fire %s", d.id)
  return D.StartActive(d, { isTest = false })
end

-- Stops the active drop's audio without emitting (used when a manual play replaces it).
function D.StopActive(restore)
  local a = D.active
  if not a then return nil end
  D.active = nil
  if a.isLocal and a.handle then ns.Audio.StopLocal(a.handle, 3000) end
  if restore then ns.Audio.Restore() end
  return a
end

-- Active drop finished normally -> restore audio, beacon back to HELLO.
function D.EndActive()
  local a = D.StopActive(true)
  if not a then return end
  ns.Debug("drop %s ended", a.drop.id)
  ns.Emit(EV.HELLO)
end

-- Cancels the active drop (combat, death, instance, stop, skip). Done stays as it is.
function D.CancelActive(reason)
  local a = D.StopActive(true)
  if not a then return false end
  if ns.Toast then ns.Toast.Hide(true) end
  ns.Debug("cancel %s (%s)", a.drop.id, tostring(reason))
  local extra = 0
  if a.isTest then extra = extra + FLAG.TEST end
  if a.isLocal then extra = extra + FLAG.LOCAL_PLAYBACK end
  ns.Emit(EV.CANCEL, a.drop, extra)
  return true
end

function D.CheckActive()
  local a = D.active
  if not a then return end
  local t = now()
  local ended = t >= a.endsAt
  if not ended and a.isLocal and a.handle and t - a.startedAt >= LOCAL_GRACE then
    if ns.Audio.IsPlaying(a.handle) == false then ended = true end
  end
  if ended then
    D.EndActive()
    return
  end
  local fade = settings().combatFadeSec or 0
  if ns.SafeBool(UnitAffectingCombat, "player") then
    if not D.combatSince then D.combatSince = t end
    if fade > 0 and t - D.combatSince > fade then D.CancelActive("combat") end
  else
    D.combatSince = nil
  end
end

-- The heart of it: called every second and on every relevant event.
function D.Evaluate(reason)
  if not ns.db then return end
  D.CheckActive()
  if not maybeScan() then return end
  if not ns.Location.state.ready then return end
  if not settings().enabled then
    if D.armed then D.Disarm("disabled") end
    return
  end
  local cand, status = D.PickCandidate()
  if status == "checking" then return end
  if D.armed and (not cand or cand.id ~= D.armed.drop.id) then D.Disarm(reason or "moved") end
  if cand and not D.armed then D.Arm(cand) end
  if D.armed and D.CanFire(D.armed.drop) then D.Fire(D.armed.drop) end
end

function D.Tick()
  D.Evaluate("tick")
end

-- ------------------------------------------------------ manual entry points ----

-- Finds a drop by id, number, label or subzone name (case-insensitive, then prefix).
function D.FindDrop(query)
  query = ns.Trim(query)
  if query == "" then return nil end
  local M = ns.Map
  if query == "test" then return ns.TEST_DROP end
  if M.byId[query] then return M.byId[query] end
  local num = tonumber(query)
  if num and M.byN[num] then return M.byN[num] end
  local q = string.lower(query)
  for i = 1, #M.drops do
    local d = M.drops[i]
    if string.lower(d.id) == q or string.lower(d.label) == q or (d.subzoneLower and d.subzoneLower == q) then
      return d
    end
  end
  for i = 1, #M.drops do
    local d = M.drops[i]
    if string.find(string.lower(d.label), q, 1, true) == 1 then return d end
  end
  for i = 1, #M.drops do
    local d = M.drops[i]
    if string.find(string.lower(d.label), q, 1, true) then return d end
  end
  return nil
end

local function describe(d)
  local sub = ns.Toast and ns.Toast.Subtitle(d) or (d.artist .. " - " .. d.title)
  return string.format("%s - %s", d.label, sub)
end

-- Manual play with flag TEST; never touches done / journal / cooldown.
function D.PlayManual(d)
  local ev = D.StartActive(d, { isTest = true })
  ns.Print("playing %s |cff8a8a8a[%s, test - not marked as heard]|r", describe(d), d.id)
  if D.active and D.active.isLocal then
    ns.Print("playing the in-game file.")
  end
  return ev
end

function D.PlayQuery(query)
  local d = D.FindDrop(query)
  if not d then
    ns.Print("no drop matches '%s'. Try /needle where, or an id like zone:1429.", query)
    return nil
  end
  return D.PlayManual(d)
end

function D.Replay(query)
  local d
  if query and query ~= "" then
    d = D.FindDrop(query)
    if not d then
      ns.Print("no drop matches '%s'.", query)
      return nil
    end
  else
    local j = ns.cdb.journal
    for i = #j, 1, -1 do
      d = ns.Map.byId[j[i].id]
      if d then break end
    end
    if not d then
      ns.Print("nothing to replay yet - your journal is empty (or its songs are not in the current map).")
      return nil
    end
  end
  return D.PlayManual(d)
end

function D.PlayTest()
  local ev = D.StartActive(ns.TEST_DROP, { isTest = true })
  ns.Print("test drop fired (seq %d). The companion should show the test card; /needle stop ends it.", ev.seq)
  return ev
end

function D.Stop()
  if D.active then
    local d = D.active.drop
    D.CancelActive("stop")
    ns.Print("stopped %s.", d.label)
  elseif D.armed then
    local d = D.armed.drop
    D.suppress[d.id] = D.EntryTime(d)
    D.Disarm("stop")
    ns.Print("let %s go for now; it will be pending again on your next visit.", d.label)
  else
    ns.Print("nothing is playing.")
  end
end

function D.Skip()
  if D.active then
    local d = D.active.drop
    D.CancelActive("skip")
    ns.Print("skipped %s (it stays marked as heard).", d.label)
  elseif D.armed then
    local d = D.armed.drop
    D.MarkDone(d, "skipped")
    D.Disarm("skip")
    ns.Print("skipped %s; it will not play on this %s.", d.label,
      settings().scope == "account" and "account" or "character")
  else
    ns.Print("nothing to skip.")
  end
end

-- ------------------------------------------------------------------ status ----

local EV_NAMES = P.EV_NAME

function D.StatusLines()
  local s = settings()
  local lines = {}
  local M = ns.Map
  lines[#lines + 1] = string.format("v%s - %s | history %s | once per %s | cooldown %s min | dwell %ss/%ss/%ss",
    ns.VERSION, s.enabled and "enabled" or "|cffff6060disabled|r", s.historyMode, s.scope, tostring(s.cooldownMin),
    tostring(s.dwellSec), tostring(s.minDwellSec), tostring(s.landmarkDwellSec))
  if M.loaded then
    local packs = #M.packs > 0 and (" (" .. table.concat(M.packs, ", ") .. ")") or ""
    lines[#lines + 1] = string.format("map: hash %d, %d drop%s%s%s", M.hash, #M.drops, #M.drops == 1 and "" or "s",
      packs, M.invalid > 0 and string.format(", %d invalid entries ignored", M.invalid) or "")
  else
    lines[#lines + 1] = "map: none loaded - run the companion's build-map, then /reload"
  end
  local b = ns.Beacon and ns.Beacon.Status() or {}
  local c = ns.ChatLink and ns.ChatLink.Status() or {}
  local last = ns.LastEvent
  lines[#lines + 1] = string.format("transports: beacon %s (%d px cells%s) | chat log %s%s",
    b.enabled and "on" or "off", b.cellPx or 0,
    last and string.format(", last %s seq %d", EV_NAMES[last.type] or "?", last.seq) or "",
    c.enabled and "on" or "off",
    c.enabled and string.format(" (logging %s, %d lines sent%s)", c.logging and "yes" or "NO - type /chatlog",
      c.sent or 0, c.lockdown and ", chat locked down" or "") or "")
  local st = ns.Location.state
  lines[#lines + 1] = string.format("location: %s %s%s%s", tostring(st.zoneKey or "no zone"), st.zoneName or "",
    st.subzone ~= "" and (" / " .. st.subzone) or "",
    st.inInstance and string.format(" (instance %s)", tostring(st.instanceID)) or "")
  if D.armed then
    local d = D.armed.drop
    local ok, el, need = D.DwellState(d)
    local wait = D.GateReasons(d)
    if not ok then table.insert(wait, 1, string.format("arrival %d/%ds", math.floor(el), need)) end
    local cdl = D.CooldownLeft()
    if cdl > 0 then wait[#wait + 1] = "cooldown " .. ns.FormatSeconds(cdl) end
    if D.active then wait[#wait + 1] = "another drop playing" end
    lines[#lines + 1] = string.format("pending: %s \"%s\"%s", d.id, d.label,
      #wait > 0 and (" - waiting for: " .. table.concat(wait, ", ")) or " - firing")
  else
    lines[#lines + 1] = "pending: none"
  end
  if D.active then
    local a = D.active
    lines[#lines + 1] = string.format("playing: %s - %s left%s%s", describe(a.drop),
      ns.FormatSeconds(a.endsAt - now()), a.isLocal and ", in-game" or ", via companion", a.isTest and ", test" or "")
  else
    lines[#lines + 1] = "playing: nothing"
  end
  local cdl = D.CooldownLeft()
  lines[#lines + 1] = cdl > 0 and ("cooldown: " .. ns.FormatSeconds(cdl) .. " left") or "cooldown: ready"
  local nChar, nAcct = 0, 0
  for _ in pairs(ns.cdb.done) do nChar = nChar + 1 end
  for _ in pairs(ns.db.accountDone) do nAcct = nAcct + 1 end
  lines[#lines + 1] = string.format("heard: %d on this character, %d on the account", nChar, nAcct)
  return lines
end

-- ------------------------------------------------------------------ events ----

ns.On("PLAYER_LOGIN", function()
  if not ticker then ticker = C_Timer.NewTicker(1, D.Tick) end
end)

ns.On("PLAYER_ENTERING_WORLD", function()
  D.flags.loading = nil
  D.flags.controlLost = nil
  if not D.loginAt then D.loginAt = now() end
  if not D.helloSent then
    D.helloSent = true
    ns.Emit(EV.HELLO)
  end
end)

ns.On("ND_LOCATION", function(_, st, ch)
  if ch.instance and st.inInstance and D.active then
    local d = D.active.drop
    if not (d.kind == "instance" and d.instanceID == st.instanceID) then D.CancelActive("instance") end
  end
  D.Evaluate("location")
  if (ch.zone or ch.instance) and not ch.first and not D.armed and not D.active
     and ns.LastEvent and ns.LastEvent.type ~= EV.HELLO then
    ns.Emit(EV.HELLO)
  end
end)

ns.On("ND_DISCOVERY", function() D.Evaluate("discovery") end)

ns.On("PLAYER_REGEN_DISABLED", function()
  if not D.combatSince then D.combatSince = now() end
end)

ns.On("PLAYER_REGEN_ENABLED", function()
  D.combatSince = nil
  D.Evaluate("combat-end")
end)

ns.On("PLAYER_DEAD", function()
  if D.active then D.CancelActive("death") end
end)

for _, event in ipairs({ "PLAYER_ALIVE", "PLAYER_UNGHOST" }) do
  ns.On(event, function(ev) D.Evaluate(ev) end)
end

ns.On("PLAYER_CONTROL_LOST", function() D.flags.controlLost = now() end)
ns.On("PLAYER_CONTROL_GAINED", function()
  D.flags.controlLost = nil
  D.Evaluate("control")
end)
ns.On("CINEMATIC_START", function() D.flags.cinematic = now() end)
ns.On("CINEMATIC_STOP", function()
  D.flags.cinematic = nil
  D.Evaluate("cinematic-stop")
end)
ns.On("PLAY_MOVIE", function() D.flags.movie = now() end)
ns.On("STOP_MOVIE", function()
  D.flags.movie = nil
  D.Evaluate("movie-stop")
end)
ns.On("LOADING_SCREEN_ENABLED", function() D.flags.loading = now() end)
ns.On("LOADING_SCREEN_DISABLED", function()
  D.flags.loading = nil
  D.Evaluate("loading-done")
end)

ns.On("ND_SETTING", function(_, key, value)
  if key == "enabled" and not value then
    if D.active then D.CancelActive("disabled") end
    if D.armed then D.Disarm("disabled") end
  elseif key == "historyMode" and value == "respect" then
    D.scanDone = false
  end
  D.Evaluate("setting")
end)

ns.On("ND_RESET", function(_, what)
  if what == "char" or what == "all" then D.scanDone = false end
  D.suppress = {}
  D.Evaluate("reset")
end)
