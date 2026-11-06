local ADDON, ns = ...
-- Director: decides whether / when a needle drop fires (SPEC 4.3, DESIGN-v1.2 4.2).
--
-- State machine, driven by a 1 Hz ticker plus event nudges:
--   candidate (instance > landmark > zone, from ns.Map, not done, enabled)
--     -> ARM once per entry
--     -> FIRE when dwell, gates, cooldown and "no active drop" all allow it
--     -> CANCEL if the armed key changes first (not marked done)
--   active drop ends at its duration (or maxDropSec, or when an in-game file stops)
--     -> Audio restore -> HELLO; CANCEL on long combat, death, entering an instance,
--        /needle stop or skip.
-- ns.Emit (Emit.lua) is the single place events leave the addon; this file only decides.
--
-- v1.2:
--   * What plays is ns.Map, built by the soundtrack model (Soundtracks.lua). ND_MAP_CHANGED
--     (an edit in game) disarms the armed drop when it is gone or its song changed, then
--     re-evaluates: no /reload.
--   * Exploration history (historyMode = respect) applies to built-in drops only
--     (Soundtracks.HistoryApplies / DoneCounts / HistoryZoneDrops); it is kept per character
--     even with scope = account (baseline finding ND-DIR-07).
--   * PlayTest(drop) plays any drop now, saved or not (a draft too), as a TEST; SuppressHere(id)
--     keeps a drop from firing for the rest of this visit (Add drop here).
--   * ND_PLAYBACK(state, drop) on every change: "armed", "disarmed", "active", "ended".
--   * Zone music is faded only when something will play the song: an in-game file, or a
--     Spotify / YouTube song while the NeedleDropper app is known (or settings.duckWithoutApp).
--   * The playing drop survives a /reload (cdb.playing, ND-DIR-01).

local D = {}
ns.Director = D

local P = ns.Protocol2
local EV, FLAG = P.EV, P.FLAG
local LANDMARK_CHECK_DELAY = 1.5   -- wait after entering a landmark before reading history
local FRESH_WINDOW = 3.0           -- a discovery this recent means "new", whatever the map says
local HISTORY_SCAN_DELAY = 3.0     -- zone history scan this long after login...
local HISTORY_SCAN_MAX_WAIT = 10.0 -- ...once MAP_EXPLORATION_UPDATED was seen, or after this
local FLAG_EXPIRY = 900            -- cinematic / movie / control flags expire (missed STOP)
local LOCAL_GRACE = 2.0            -- ignore C_Sound.IsPlaying()==false right after starting
local MEDIA_PATH = "Interface\\AddOns\\NeedleDropper_Local\\media\\"

D.armed = nil        -- { drop =, at = }
D.active = nil       -- { drop =, startedAt =, endsAt =, handle =, isLocal =, isTest =, ducked = }
D.scanDone = false
D.loginAt = nil
D.helloSent = false
D.combatSince = nil
D.suppress = {}      -- [dropId] = entry time: let this drop go for this visit (stop, Add drop here)
D.quiet = nil        -- { zone =, sub =, inst = } entry times: /needle stop asked for quiet here
D.lastPick = nil     -- what PickCandidate last reported ("checking" while a landmark check waits)
D.flags = { loading = nil, cinematic = nil, movie = nil, controlLost = nil } -- value = time set
local checkScheduledFor = nil
local ticker = nil

local function now() return GetTime() end

local function settings() return ns.settings end

local function nonEmpty(v)
  if type(v) == "string" and v ~= "" then return v end
  return nil
end

local function fireState(state, drop)
  if ns.Fire then ns.Fire("ND_PLAYBACK", state, drop) end
end

-- v1 name; Emit.lua's ns.ComputeFlags is the implementation.
function D.ComputeFlags(extra)
  return ns.ComputeFlags(extra)
end

-- --------------------------------------------------------------- history ----

-- Does done record `rec` count for `drop`? History marks only count for built-in drops.
local function counts(drop, rec)
  if rec == nil then return false end
  local St = ns.Soundtracks
  if St and St.DoneCounts then return St.DoneCounts(drop, rec) and true or false end
  return true
end

local function historyApplies(drop)
  local St = ns.Soundtracks
  if St and St.HistoryApplies then return St.HistoryApplies(drop) and true or false end
  return drop.builtin ~= false
end

-- A record a new one may replace: none, exploration history (it never counts against a drop
-- the player made, and a real play or skip says more), or a title card shown before the app
-- was there (cardOnly below) -- the play it gets once the app is there replaces it.
local function replaceable(rec, how)
  if rec == nil then return true end
  if type(rec) ~= "table" then return false end
  if rec.how == "history" then return how ~= "history" end
  if rec.how == "card" then return how ~= "history" and how ~= "card" end
  return false
end

function D.IsDone(drop)
  if not ns.db or not drop then return false end
  local id = drop.id
  local charRec = ns.cdb.done[id]
  if settings().scope == "account" then
    -- History stays per character (ND-DIR-07): one character's explored map never hides a
    -- drop from the others; old account-wide history marks are ignored.
    local acct = ns.db.accountDone[id]
    if acct and acct.how ~= "history" and counts(drop, acct) then return true end
    if charRec and charRec.how == "history" and counts(drop, charRec) then return true end
    return false
  end
  return counts(drop, charRec)
end

-- Records a drop as done for the character and (unless it is history) the account.
function D.MarkDone(drop, how)
  local t = time()
  if replaceable(ns.cdb.done[drop.id], how) then ns.cdb.done[drop.id] = { t = t, how = how } end
  if how ~= "history" and replaceable(ns.db.accountDone[drop.id], how) then
    ns.db.accountDone[drop.id] = { t = t, how = how }
  end
end

-- Brand-new characters (level 1) have their starting area pre-explored: no history.
function D.IsNewCharacter()
  local ok, level = pcall(UnitLevel, "player")
  return ok and ns.SafeNumber(level) ~= nil and level <= 1
end

-- Zone drops the history scan looks at: the built-in ones (DESIGN-v1.2 4.2).
local function historyZoneDrops()
  local St = ns.Soundtracks
  if St and St.HistoryZoneDrops then return St.HistoryZoneDrops() end
  local out = {}
  local drops = ns.Map and ns.Map.drops or {}
  for i = 1, #drops do
    if drops[i].kind == "zone" and historyApplies(drops[i]) then out[#out + 1] = drops[i] end
  end
  return out
end

-- Zone drops: once per character per drop id, mark explored zones as heard.
function D.RunHistoryScan()
  D.scanDone = true
  local scanned = ns.cdb.scanned
  local newChar = D.IsNewCharacter()
  local marked = 0
  local drops = historyZoneDrops()
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
     and not ns.cdb.scanned[d.id] and historyApplies(d)
end

-- Landmark with areaID: explored already (and not discovered just now) -> heard. The landmark
-- counts as checked only once the game answered (ND-DIR-08): with no position or no answer it
-- is new for this visit and checked again on the next evaluation or visit.
function D.RunLandmarkCheck(d)
  if D.IsNewCharacter() then
    ns.cdb.scanned[d.id] = true
    return false
  end
  local st = ns.Location.state
  if ns.Discovery.FreshFor(st.subLower, d, FRESH_WINDOW) then
    ns.cdb.scanned[d.id] = true
    return false
  end
  local areas = ns.Discovery.ExploredAreasHere(d.mapID or st.zoneMapID)
  if type(areas) ~= "table" then return false end
  ns.cdb.scanned[d.id] = true
  if areas[d.areaID] then
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

-- /needle stop asked for quiet: no drop arms until the player moves on (ND-DIR-04).
function D.SetQuiet()
  local st = ns.Location and ns.Location.state
  if not (st and st.ready) then return end
  D.quiet = { zone = st.zoneEnteredAt, sub = st.subEnteredAt, inst = st.instanceEnteredAt }
end

function D.IsQuiet()
  local q = D.quiet
  if not q then return false end
  local st = ns.Location.state
  if st.zoneEnteredAt == q.zone and st.subEnteredAt == q.sub and st.instanceEnteredAt == q.inst then return true end
  D.quiet = nil
  return false
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

-- Highest-priority eligible candidate. Returns drop or nil, plus "checking" while a landmark
-- history check is pending and "quiet" after /needle stop.
function D.PickCandidate()
  if D.IsQuiet() then
    D.lastPick = "quiet"
    return nil, "quiet"
  end
  D.lastPick = nil
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
          D.lastPick = "checking"
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
  fireState("armed", d)
end

function D.Disarm(reason)
  local a = D.armed
  if not a then return end
  D.armed = nil
  ns.Debug("cancel armed %s (%s)", a.drop.id, tostring(reason))
  -- The companion matches CANCEL by placeKey: never send the abandon-CANCEL for the drop that
  -- is playing right now (a manual play of the pending drop), or it would stop the song
  -- (ND-DIR-03).
  if not (D.active and D.active.drop and D.active.drop.id == a.drop.id) then
    ns.Emit(EV.CANCEL, a.drop)
  end
  fireState("disarmed", a.drop)
end

-- ------------------------------------------------------------ zone music ----

-- Something an app can play: a Spotify or YouTube id, or artist + title to search for. The test
-- drop never carries its song (Emit.lua sends only its label), so nothing would play it.
local function hasSong(drop)
  if drop.id == "test" or drop.kind == "test" then return false end
  return nonEmpty(drop.spotify) ~= nil or nonEmpty(drop.youtube) ~= nil
      or (nonEmpty(drop.artist) ~= nil and nonEmpty(drop.title) ~= nil)
end

-- True when the NeedleDropper app (1.2 or newer) has reported in (NeedleDropper_Companion).
function D.AppKnown()
  local C = ns.Companion
  if type(C) ~= "table" or type(C.Status) ~= "function" then return false end
  local ok, st = pcall(C.Status)
  return ok and type(st) == "table" and st.present == true and not st.outdated
end

-- Real-client finding 6: fading the zone music for a song nothing will play leaves silence.
-- Spotify / YouTube songs fade it only when the app is known (or settings.duckWithoutApp);
-- songless drops (the /needle test card) never do; in-game files always do.
function D.ShouldDuck(drop, isLocal)
  if isLocal then return true end
  if type(drop) ~= "table" or not hasSong(drop) then return false end
  if settings().duckWithoutApp then return true end
  return D.AppKnown()
end

local function applyDuck(drop)
  if D.ShouldDuck(drop, false) then
    ns.Audio.Duck({})
    return true
  end
  if ns.Audio.IsDucked() then ns.Audio.Restore() end
  return false
end

-- ------------------------------------------------------------- playback ----

-- What survives a /reload of the playing drop (ND-DIR-01): the song fields, not the map.
local SNAPSHOT_KEYS = { "id", "kind", "mapID", "areaID", "subzone", "subzoneLower", "instanceID", "label", "title",
                        "artist", "spotify", "youtube", "startMs", "durationMs", "volume", "setId", "setRef",
                        "builtin", "songKey", "fileName" }
local function snapshot(drop)
  local out = {}
  for i = 1, #SNAPSHOT_KEYS do
    local k = SNAPSHOT_KEYS[i]
    local v = drop[k]
    if type(v) == "string" or type(v) == "number" or type(v) == "boolean" then out[k] = v end
  end
  return out
end

local function remember(drop, isTest, isLocal, handle, length)
  if not ns.cdb then return end
  ns.cdb.playing = { id = drop.id, isTest = isTest or nil, isLocal = isLocal or nil,
                     handle = (isLocal and type(handle) == "number") and handle or nil,
                     endsAtWall = time() + length, drop = snapshot(drop) }
end

-- Starts playing `drop` (first-visit FIRE or a manual play). opts.isTest for manual plays.
function D.StartActive(drop, opts)
  local s = settings()
  local isTest = opts and opts.isTest or false
  local wantLocal = drop.file ~= nil and s.localPlayback and true or false
  -- Replacing a playing drop hands the fade over instead of restoring the zone music first.
  if D.active then D.StopActive(false) end
  -- Never start inside the 0.1 s Sound_EnableMusic kick of a restore (ND-DIR-02).
  ns.Audio.FinishKick()
  local isLocal, handle = false, nil
  if wantLocal then
    -- The in-game file plays on the Music channel: a volume fade would silence it too.
    if ns.Audio.ducked == "volume" then ns.Audio.Restore() end
    ns.Audio.Duck({ forLocal = true })
    isLocal, handle = ns.Audio.PlayLocal(drop.file)
    if not isLocal then
      ns.Debug("in-game file for %s did not play; leaving playback to the companion", tostring(drop.id))
    end
  end
  local ducked = isLocal
  if not isLocal then ducked = applyDuck(drop) end
  local length = (drop.durationMs and drop.durationMs > 0) and (drop.durationMs / 1000) or s.maxDropSec
  local t = now()
  D.active = { drop = drop, startedAt = t, endsAt = t + length, handle = handle, isLocal = isLocal,
               isTest = isTest, ducked = ducked }
  remember(drop, isTest, isLocal, handle, length)
  if ns.Toast then ns.Toast.Show(drop) end
  local ev = ns.Emit(EV.FIRE, drop, isTest and FLAG.TEST or 0)
  fireState("active", drop)
  return ev
end

-- A first-visit drop whose song nothing on this PC can play: no in-game file played and the
-- NeedleDropper app has never reported in (NeedleDropper_Companion). Only the title card showed,
-- so the place is marked "card", not "played": Soundtracks.DoneCounts lets it play again once the
-- app is there (journey review F4). The cooldown and the journal entry stay as for any drop.
local function cardOnly(d, charBefore, acctBefore)
  local c = ns.cdb.done[d.id]
  if c ~= charBefore and type(c) == "table" and c.how == "played" then c.how = "card" end
  local a = ns.db.accountDone[d.id]
  if a ~= acctBefore and type(a) == "table" and a.how == "played" then a.how = "card" end
  if not ns.db.noAppHint then
    ns.db.noAppHint = true
    ns.Print("only the title card showed: Spotify and YouTube songs play through the NeedleDropper app, which " ..
      "hasn't been seen on this PC. Places you visit before you install it play their song once it is there.")
  end
end

function D.Fire(d)
  D.armed = nil
  local charBefore, acctBefore = ns.cdb.done[d.id], ns.db.accountDone[d.id]
  D.MarkDone(d, "played")
  ns.AddJournal(d)
  ns.cdb.lastFireAt = time()
  ns.Debug("fire %s", d.id)
  local ev = D.StartActive(d, { isTest = false })
  local a = D.active
  if a and a.drop == d and not a.isLocal and hasSong(d) and not D.AppKnown() then cardOnly(d, charBefore, acctBefore) end
  return ev
end

-- Stops the active drop's audio without emitting (also used when a manual play replaces it).
function D.StopActive(restore)
  local a = D.active
  if not a then return nil end
  D.active = nil
  if ns.cdb then ns.cdb.playing = nil end
  if a.isLocal and a.handle then ns.Audio.StopLocal(a.handle, 3000) end
  if restore then ns.Audio.Restore() end
  fireState("ended", a.drop)
  return a
end

-- Active drop finished normally -> restore audio, beacon back to HELLO.
function D.EndActive()
  local a = D.StopActive(true)
  if not a then return end
  ns.Debug("drop %s ended", tostring(a.drop.id))
  ns.Emit(EV.HELLO)
end

-- Cancels the active drop (combat, death, instance, stop, skip). Done stays as it is.
function D.CancelActive(reason)
  local a = D.StopActive(true)
  if not a then return false end
  if ns.Toast then ns.Toast.Hide(true) end
  ns.Debug("cancel %s (%s)", tostring(a.drop.id), tostring(reason))
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

local function mapDrops()
  local M = ns.Map
  return (type(M) == "table" and type(M.drops) == "table") and M.drops or {}
end

-- Finds a drop by id, number, label or subzone name (case-insensitive, then prefix).
function D.FindDrop(query)
  query = ns.Trim(query)
  if query == "" then return nil end
  local M = ns.Map
  if query == "test" then return ns.TEST_DROP end
  if M.byId[query] then return M.byId[query] end
  local num = tonumber(query)
  if num and M.byN and M.byN[num] then return M.byN[num] end
  local q = string.lower(query)
  local drops = mapDrops()
  for i = 1, #drops do
    local d = drops[i]
    if string.lower(d.id) == q or string.lower(d.label or "") == q or (d.subzoneLower and d.subzoneLower == q) then
      return d
    end
  end
  for i = 1, #drops do
    local d = drops[i]
    if string.find(string.lower(d.label or ""), q, 1, true) == 1 then return d end
  end
  for i = 1, #drops do
    local d = drops[i]
    if string.find(string.lower(d.label or ""), q, 1, true) then return d end
  end
  return nil
end

local function esc(s)
  if ns.EscapeText then return ns.EscapeText(tostring(s or "")) end
  return (string.gsub(tostring(s or ""), "|", "||"))
end

local function describe(d)
  local sub = ns.Toast and ns.Toast.Subtitle(d) or ((d.artist or "") .. " - " .. (d.title or ""))
  return string.format("%s - %s", esc(d.label or d.id), esc(sub))
end

-- A playable drop rebuilt from a journal entry (DESIGN-v1.2 4.6): the song stored in the entry,
-- the place from its id (and from ns.Map when the drop still exists).
function D.DropFromJournal(e)
  if type(e) ~= "table" or type(e.id) ~= "string" then return nil end
  local d = { id = e.id, label = e.label or e.id, title = e.title or "", artist = e.artist or "",
              spotify = nonEmpty(e.spotify), youtube = nonEmpty(e.youtube), startMs = tonumber(e.startMs) or 0,
              durationMs = tonumber(e.durationMs) or 0, volume = tonumber(e.volume), setId = nonEmpty(e.setId) }
  local file = nonEmpty(e.file)
  if file then
    if string.find(file, "\\", 1, true) then
      d.file = file
    else
      d.fileName, d.file = file, MEDIA_PATH .. file
    end
  end
  local kind, a = string.match(e.id, "^(%a+):([^:]+)")
  if kind == "zone" then
    d.kind, d.mapID = "zone", tonumber(a)
  elseif kind == "area" then
    d.kind, d.areaID = "subzone", tonumber(a)
  elseif kind == "sub" then
    d.kind, d.mapID = "subzone", tonumber(a)
  elseif kind == "inst" then
    d.kind, d.instanceID = "instance", tonumber(a)
  else
    d.kind = "zone"
  end
  local live = ns.Map and ns.Map.byId and ns.Map.byId[e.id]
  if live then
    d.mapID = d.mapID or live.mapID
    d.areaID = d.areaID or live.areaID
    d.subzone = live.subzone
    d.subzoneLower = live.subzoneLower
  end
  return d
end

-- Manual play with flag TEST; never touches done / journal / cooldown. Playing the pending drop
-- by hand lets it go for this visit, so it does not fire again right after (ND-DIR-03).
-- quiet: no chat line (the window shows what plays in its footer).
function D.PlayManual(d, quiet)
  if D.armed and D.armed.drop.id == d.id then
    D.armed = nil
    if d.kind == "zone" or d.kind == "subzone" or d.kind == "instance" then D.suppress[d.id] = D.EntryTime(d) end
  end
  local ev = D.StartActive(d, { isTest = true })
  if not quiet then
    ns.Print("playing %s |cff8a8a8a[%s, test - not marked as heard]|r", describe(d), esc(d.id))
    if D.active and D.active.isLocal then
      ns.Print("playing the in-game file.")
    end
  end
  return ev
end

function D.PlayQuery(query)
  local d = D.FindDrop(query)
  if not d then
    ns.Print("no drop matches '%s'. Try /needle where, or an id like zone:1429.", esc(query))
    return nil
  end
  return D.PlayManual(d)
end

function D.Replay(query)
  local d
  local j = ns.cdb.journal
  if query and query ~= "" then
    d = D.FindDrop(query)
    if not d then
      for i = #j, 1, -1 do
        if j[i].id == query then d = D.DropFromJournal(j[i]) break end
      end
    end
    if not d then
      ns.Print("no drop matches '%s'.", esc(query))
      return nil
    end
  else
    -- The last song heard, as it was heard (the drop may have changed since).
    for i = #j, 1, -1 do
      d = D.DropFromJournal(j[i])
      if d and (hasSong(d) or d.file) then break end
      d = nil
    end
    if not d then
      ns.Print("nothing to replay yet - your journal is empty.")
      return nil
    end
  end
  return D.PlayManual(d)
end

-- Test: with no argument the test drop (/needle test: a TEST frame without a song, so the app
-- shows its "heard World of Warcraft" check and nothing plays). With a drop (the window's Test,
-- the Journal's Replay): that drop now, saved or not, a draft too, as a TEST that is never
-- marked as heard.
function D.PlayTest(drop)
  if drop == nil then
    local ev = D.StartActive(ns.TEST_DROP, { isTest = true })
    ns.Print("test drop fired (seq %d). The NeedleDropper app shows a notification if it can see the game; /needle stop ends it.",
      ev and ev.seq or 0)
    return ev
  end
  if type(drop) ~= "table" then return nil end
  local d = {}
  for k, v in pairs(drop) do d[k] = v end
  if type(d.id) ~= "string" or d.id == "" then d.id = "test" end
  if d.kind ~= "zone" and d.kind ~= "subzone" and d.kind ~= "instance" then
    local St = ns.Soundtracks
    d.kind = (St and St.KindOfId and St.KindOfId(d.id)) or d.kind or "zone"
  end
  if type(d.subzone) == "string" and d.subzone ~= "" and not d.subzoneLower then d.subzoneLower = string.lower(d.subzone) end
  d.label = nonEmpty(d.label) or nonEmpty(d.subzone) or d.id
  d.title = d.title or ""
  d.artist = d.artist or ""
  if type(d.startMs) ~= "number" then d.startMs = 0 end
  if type(d.durationMs) ~= "number" then d.durationMs = 0 end
  return D.PlayManual(d, true)
end

-- Keeps drop `id` from firing for the rest of this visit (Add drop here: the player is editing
-- it). Disarms it when it is pending.
function D.SuppressHere(id)
  if type(id) ~= "string" or id == "" then return false end
  local St = ns.Soundtracks
  local kind = (St and St.KindOfId and St.KindOfId(id)) or "zone"
  D.suppress[id] = D.EntryTime({ id = id, kind = kind })
  if D.armed and D.armed.drop.id == id then D.Disarm("suppressed") end
  return true
end

function D.Stop()
  if D.active then
    local d = D.active.drop
    D.CancelActive("stop")
    D.SetQuiet()
    ns.Print("stopped %s.", esc(d.label or d.id))
  elseif D.armed then
    local d = D.armed.drop
    D.suppress[d.id] = D.EntryTime(d)
    D.Disarm("stop")
    D.SetQuiet()
    ns.Print("let %s go for now; no needle drop plays here until you move on.", esc(d.label or d.id))
  elseif D.lastPick == "checking" then
    D.SetQuiet()
    ns.Print("no needle drop plays here until you move on.")
  else
    ns.Print("nothing is playing.")
  end
end

function D.Skip()
  if D.active then
    local a = D.active
    local d = a.drop
    D.CancelActive("skip")
    if d.id == "test" or d.kind == "test" then
      ns.Print("stopped the test drop.")
    elseif a.isTest and not D.IsDone(d) and ns.Map.byId[d.id] then
      -- A test play of a place not heard yet: skip means it never plays (ND-DIR-03).
      D.MarkDone(d, "skipped")
      D.suppress[d.id] = nil
      if D.armed and D.armed.drop.id == d.id then D.armed = nil end
      ns.Print("skipped %s; it will not play on this %s.", esc(d.label or d.id),
        settings().scope == "account" and "account" or "character")
    else
      ns.Print("skipped %s (it stays marked as heard).", esc(d.label or d.id))
    end
  elseif D.armed then
    local d = D.armed.drop
    D.MarkDone(d, "skipped")
    D.Disarm("skip")
    ns.Print("skipped %s; it will not play on this %s.", esc(d.label or d.id),
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
  local names = ns.ActiveSoundtrackNames and ns.ActiveSoundtrackNames() or (M.packs or {})
  local escaped = {}
  for i = 1, #names do escaped[i] = esc(names[i]) end
  local drops = mapDrops()
  lines[#lines + 1] = string.format("soundtracks: %s - %d drop%s%s",
    #escaped > 0 and table.concat(escaped, ", ") or "none on", #drops, #drops == 1 and "" or "s",
    (M.invalid or 0) > 0 and string.format(", %d unusable", M.invalid) or "")
  local b = ns.Beacon and ns.Beacon.Status() or {}
  local c = ns.ChatLink and ns.ChatLink.Status() or {}
  local last = ns.LastEvent
  lines[#lines + 1] = string.format("transports: beacon %s (%d px cells, %s%s) | chat log %s%s",
    b.enabled and "on" or "off", b.cellPx or 0, tostring(b.size or "0x0"),
    last and string.format(", last %s seq %d", EV_NAMES[last.type] or "?", last.seq) or "",
    c.enabled and "on" or "off",
    c.enabled and string.format(" (logging %s, %d lines sent%s)", c.logging and "yes" or "NO - type /chatlog",
      c.sent or 0, c.lockdown and ", chat locked down" or "") or "")
  local st = ns.Location.state
  lines[#lines + 1] = string.format("location: %s %s%s%s", tostring(st.zoneKey or "no zone"), esc(st.zoneName or ""),
    st.subzone ~= "" and (" / " .. esc(st.subzone)) or "",
    st.inInstance and string.format(" (instance %s)", tostring(st.instanceID)) or "")
  if D.armed then
    local d = D.armed.drop
    local ok, el, need = D.DwellState(d)
    local wait = D.GateReasons(d)
    if not ok then table.insert(wait, 1, string.format("arrival %d/%ds", math.floor(el), need)) end
    local cdl = D.CooldownLeft()
    if cdl > 0 then wait[#wait + 1] = "cooldown " .. ns.FormatSeconds(cdl) end
    if D.active then wait[#wait + 1] = "another drop playing" end
    lines[#lines + 1] = string.format("pending: %s \"%s\"%s", esc(d.id), esc(d.label or d.id),
      #wait > 0 and (" - waiting for: " .. table.concat(wait, ", ")) or " - firing")
  elseif D.quiet and D.IsQuiet() then
    lines[#lines + 1] = "pending: none (quiet here until you move on)"
  else
    lines[#lines + 1] = "pending: none"
  end
  if D.active then
    local a = D.active
    local how = a.isLocal and ", in-game" or (a.ducked and ", via the app" or ", card only (zone music left on)")
    lines[#lines + 1] = string.format("playing: %s - %s left%s%s", describe(a.drop),
      ns.FormatSeconds(a.endsAt - now()), how, a.isTest and ", test" or "")
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

-- ------------------------------------------------------- reload survival ----

-- After a /reload the companion would stop a song the addon forgot (a HELLO without
-- DROP_ACTIVE) while it stays marked heard (ND-DIR-01). Restore the playing drop from
-- cdb.playing before the first HELLO: no MarkDone, journal or cooldown, just the active state
-- (and the zone music fade, which the reload undid).
local function restoreAfterReload(isInitialLogin, isReloadingUi)
  local rec = ns.cdb and ns.cdb.playing
  if type(rec) ~= "table" then return false end
  if isInitialLogin or not isReloadingUi then
    ns.cdb.playing = nil
    return false
  end
  local left = (tonumber(rec.endsAtWall) or 0) - time()
  if rec.isLocal then
    -- The in-game file belongs to the old UI session: stop it (best effort) and let the zone
    -- music come back.
    if type(rec.handle) == "number" then ns.Audio.StopLocal(rec.handle, 3000) end
    ns.cdb.playing = nil
    return false
  end
  if left <= 1 then
    ns.cdb.playing = nil
    return false
  end
  local drop
  if rec.id == "test" then
    drop = ns.TEST_DROP
  else
    local live = ns.Map and ns.Map.byId and ns.Map.byId[rec.id]
    local snap = type(rec.drop) == "table" and rec.drop or nil
    if live and (not snap or snap.songKey == nil or live.songKey == snap.songKey) then
      drop = live
    elseif snap then
      drop = {}
      for k, v in pairs(snap) do drop[k] = v end
      drop.label = drop.label or drop.id
    end
  end
  if type(drop) ~= "table" then
    ns.cdb.playing = nil
    return false
  end
  local t = now()
  D.active = { drop = drop, startedAt = t, endsAt = t + left, isLocal = false, isTest = rec.isTest and true or false,
               restored = true }
  D.active.ducked = applyDuck(drop)
  ns.Debug("restored %s after the reload (%s left)", tostring(drop.id), ns.FormatSeconds(left))
  fireState("active", drop)
  return true
end

-- ------------------------------------------------------------------ events ----

ns.On("PLAYER_LOGIN", function()
  if not ticker then ticker = C_Timer.NewTicker(1, D.Tick) end
end)

ns.On("PLAYER_ENTERING_WORLD", function(_, isInitialLogin, isReloadingUi)
  D.flags.loading = nil
  D.flags.controlLost = nil
  if not D.loginAt then D.loginAt = now() end
  if not D.helloSent then
    D.helloSent = true
    restoreAfterReload(isInitialLogin, isReloadingUi)
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

-- An edit in game rebuilt the map: drop a pending drop that is gone or whose song changed (the
-- companion cached its old song at ARM), keep pointing at the fresh copy otherwise.
ns.On("ND_MAP_CHANGED", function(_, M)
  M = (type(M) == "table" and M) or ns.Map
  local a = D.armed
  if a then
    local fresh = type(M) == "table" and type(M.byId) == "table" and M.byId[a.drop.id] or nil
    if not fresh or fresh.songKey ~= a.drop.songKey then
      D.Disarm("map-changed")
    else
      a.drop = fresh
    end
  end
  if ns.db and ns.Location and ns.Location.state and ns.Location.state.ready then D.Evaluate("map-changed") end
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
  D.quiet = nil
  D.Evaluate("reset")
end)
