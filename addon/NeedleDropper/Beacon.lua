local ADDON, ns = ...
-- Beacon v2 (DESIGN-v1.2 2.3, docs/PROTOCOL-v2.md section 4): the pixel strip the companion
-- reads at 4 Hz from the top-left 512 x 48 px of the client area. Each event becomes a
-- Protocol2 frame drawn as rows of cellPx x cellPx physical-pixel cells; the strip's width
-- adapts to the frame (16..112 columns) and its height stays within rows x cellPx <= 9 px.
--
-- Frame: no parent (Alt+Z cannot hide it), anchored to WorldFrame's corner (TOPLEFT unless
-- the advanced beaconCorner setting says otherwise), strata TOOLTIP, level 9000, mouse off.
-- Cells are a lazily created texture pool (at most 448 = 112 x 4), sized from PixelUtil's
-- pixel-to-UI factor and the frame's effective scale, with pixel-grid snapping on.
--
-- What is on screen (B.Pump):
--   * transient frames (FIFO A), first in first out, each held at least: FIRE 3 s, CANCEL / PING 1 s;
--   * then an ARM's full-song window (below);
--   * then CONFIG frames (queue B, DESIGN-v1.2.1 2.7): at most 4 waiting, each held 3 s (with an
--     ACTION) or 2 s (PREFS only), shown only while FIFO A is empty and no ARM is in its full-song
--     window. A FIFO-A event or a new ARM takes the screen at once; the CONFIG goes back to the head
--     of queue B with its full hold. 8 s after an ACTION CONFIG first completes its hold, the same
--     event is queued once more for 2 s (a capture missed behind an overlay, finding F2). With the
--     beacon off, an ACTION CONFIG is shown anyway (ForceShow), like "Send a test signal";
--   * otherwise the steady state of the newest event: HELLO (idle), ARM (the full song for 3 s
--     of drawn time, then an ARM carrying only the placeKey), after a FIRE a HELLO with
--     DROP_ACTIVE, after a CANCEL a HELLO. Synthesized steady frames reuse their event's seq
--     (they are re-renderings, never emitted: no ns.Events entry, no chat line); TEST is
--     cleared in them, and LOCAL_PLAYBACK unless DROP_ACTIVE is set.
--   * Every HELLO drawn (emitted or synthesized) carries the music choices B.SetPrefs stored
--     (PREFS) and, while DROP_ACTIVE is set, the playing drop's placeKey (decision D3, finding F2:
--     if the FIRE's 3 s were covered, the app can still start the song it cached at ARM).
--
-- Clearance guard: the gap between the screen top and the player / target frame art is
-- measured on PLAYER_ENTERING_WORLD, UI_SCALE_CHANGED, DISPLAY_SIZE_CHANGED, Edit Mode exit and
-- PLAYER_TARGET_CHANGED (the target frame is hidden until something is targeted).
-- B.Choose draws the frame fitted to the 9 px cap at the configured cell size when it fits the
-- gap, else the same frame with 2 px cells, else a frame fitted to what fits at 2 px (at least
-- one row), and records a warning for /needle selftest beacon when the strip overlaps the art.

local B = {}
ns.Beacon = B

local P2 = ns.Protocol2
local EV, FLAG = P2.EV, P2.FLAG
local floor, max = math.floor, math.max

local FRAME_NAME = "NeedleDropperBeacon"
local STRATA, FRAME_LEVEL = "TOOLTIP", 9000
local MIN_PX, MAX_PX, DEFAULT_PX = 2, 4, 3   -- beaconCellPx range and default
local MAX_CELLS = P2.MAX_COLS * P2.RowsCap(MIN_PX)   -- 448 textures at most
local SONG_HOLD = 3.0                 -- ARM: full song this long (drawn time); FIRE: at least this long
local SHORT_HOLD = 1.0                -- CANCEL / PING: at least this long
local PLAYING_HOLD = 3.0              -- the playing HELLO (D3) before the next ARM's song window takes over
local CLEAR_MARGIN = 2                -- physical px kept free above the measured art
local CLEAR_LEFT = MAX_CELLS          -- art further right than 448 px is never under the strip
local REMEASURE_AFTER = 1.0           -- measure once more after the UI has settled
local FIFO_MAX = 64
local EPS = 0.001
local CONFIG_ACTION_HOLD = 3.0        -- queue B: a CONFIG with an ACTION
local CONFIG_PREFS_HOLD = 2.0         -- queue B: a PREFS-only CONFIG
local CONFIG_RESHOW_AFTER = 8.0       -- an ACTION CONFIG is shown once more this long after its hold
local CONFIG_RESHOW_HOLD = 2.0
local CONFIG_MAX = 4                  -- queue B holds this many
local CONFIG_FORCE_MARGIN = 0.5       -- beacon off: an ACTION CONFIG is forced visible for hold + this

local TRANSIENT_HOLD = { [EV.FIRE] = SONG_HOLD, [EV.CANCEL] = SHORT_HOLD, [EV.PING] = SHORT_HOLD }

B.SONG_HOLD, B.SHORT_HOLD, B.MAX_CELLS = SONG_HOLD, SHORT_HOLD, MAX_CELLS
B.CONFIG_ACTION_HOLD, B.CONFIG_PREFS_HOLD, B.CONFIG_RESHOW_AFTER, B.CONFIG_RESHOW_HOLD, B.CONFIG_MAX =
  CONFIG_ACTION_HOLD, CONFIG_PREFS_HOLD, CONFIG_RESHOW_AFTER, CONFIG_RESHOW_HOLD, CONFIG_MAX
B.CLEAR_MARGIN, B.CLEAR_LEFT, B.MIN_PX, B.MAX_PX, B.DEFAULT_PX = CLEAR_MARGIN, CLEAR_LEFT, MIN_PX, MAX_PX, DEFAULT_PX
-- Regions whose top edge limits the strip's height, each falling back to its frame.
B.CLEARANCE_REGIONS = {
  { "PlayerFrame", "PlayerFrameContainer", "FrameTexture" },
  { "TargetFrame", "TargetFrameContainer", "FrameTexture" },
}

B.frame = nil
B.textures = {}       -- the cell pool, created on demand
B.fifo = {}           -- transient events waiting for the screen
B.transient = nil     -- { ev =, holdUntil = } the transient on screen
B.steady = nil        -- { ev =, fullLeft =, fullUntil =, after = } shown when no transient is due
B.drawn = nil         -- what is drawn: B.Choose result + { ev, variant, layout, at }
B.gap = nil           -- measured clearance above the art (physical px), nil = not measured
B.measured = nil      -- { at =, regions = { "PlayerFrame.FrameTexture", ... } }
B.forceUntil = 0
B.dropped = 0         -- transients dropped because the queue was full
B.cfgQueue = {}       -- queue B: { ev =, hold =, action = bool, reshow = bool } waiting for the screen
B.cfg = nil           -- { entry =, holdUntil = } the CONFIG on screen
B.cfgReshows = {}     -- { entry =, at = } re-shows waiting for their time
B.configDropped = 0   -- CONFIGs dropped because queue B was full
B.prefs = nil         -- the music choices every drawn HELLO carries (B.SetPrefs), or nil
B.playingKey = nil    -- placeKey of the last FIRE shown, while a drop plays (fallback for D3)
local warnings = {}   -- [code] = text
local laid = nil      -- { cols, cell, corner, placed } of the textures' current positions
local gen = 0         -- wake-up generation: only the newest scheduled timer acts
local layoutVersion = 0

local function L(text)
  local t = ns.L
  return (type(t) == "table" and t[text]) or text
end

-- -------------------------------------------------------------- settings ----

-- Configured cell size: beaconCellPx rounded into 2..4 (default 3).
function B.SettingCellPx()
  local v = ns.settings and ns.settings.beaconCellPx
  if type(v) ~= "number" or v ~= v or v == math.huge or v == -math.huge then return DEFAULT_PX end
  v = floor(v + 0.5)
  if v < MIN_PX then return MIN_PX elseif v > MAX_PX then return MAX_PX end
  return v
end

-- Configured corner: beaconCorner (an advanced setting) if it is one of P2.CORNERS, else TOPLEFT.
function B.Corner()
  local v = ns.settings and ns.settings.beaconCorner
  if type(v) == "string" then
    v = string.upper(v)
    if P2.CORNERS[v] then return v end
  end
  return "TOPLEFT"
end

-- ---------------------------------------------------------------- pixels ----

-- Physical pixel -> UI unit factor (768 / physical screen height).
function B.PixelFactor()
  if PixelUtil and PixelUtil.GetPixelToUIUnitFactor then
    local ok, f = pcall(PixelUtil.GetPixelToUIUnitFactor)
    if ok and ns.SafeNumber(f) and f > 0 then return f end
  end
  if GetPhysicalScreenSize then
    local ok, _, h = pcall(GetPhysicalScreenSize)
    if ok and ns.SafeNumber(h) and h > 0 then return 768 / h end
  end
  return 768 / 1080
end

local function physicalHeight()
  if GetPhysicalScreenSize then
    local ok, _, h = pcall(GetPhysicalScreenSize)
    if ok and ns.SafeNumber(h) and h > 0 then return h end
  end
  return 768 / B.PixelFactor()
end

local function frameScale()
  local f = B.frame
  if f then
    local ok, s = pcall(f.GetEffectiveScale, f)
    if ok and ns.SafeNumber(s) and s > 0 then return s end
  end
  return 1
end

-- Size of a `px` px cell in the beacon frame's own units.
function B.CellSize(px)
  return (px or B.SettingCellPx()) * B.PixelFactor() / frameScale()
end

-- ------------------------------------------------------------- clearance ----

local function number(v)
  v = ns.SafeNumber(v)
  if v and v == v and v > -math.huge and v < math.huge then return v end
  return nil
end

local function callMethod(obj, name)
  if type(obj) ~= "table" then return nil end
  local fn = obj[name]
  if type(fn) ~= "function" then return nil end
  local ok, v = pcall(fn, obj)
  if not ok then return nil end
  return v
end

-- Gap (physical px) from the screen top down to `region`'s top edge; nil when the region is
-- missing, hidden or unmeasurable; false when it is shown but right of the strip's columns.
local function regionGap(region, physH, factor)
  if type(region) ~= "table" then return nil end
  local shown = callMethod(region, "IsVisible")
  if shown == nil then shown = callMethod(region, "IsShown") end
  if ns.IsSecret(shown) or not shown then return nil end
  local top = number(callMethod(region, "GetTop"))
  local es = number(callMethod(region, "GetEffectiveScale"))
  if not top or not es or es <= 0 then return nil end
  local left = number(callMethod(region, "GetLeft"))
  if left and left * es / factor > CLEAR_LEFT then return false end
  return physH - top * es / factor
end

-- Rows a strip of `px` px cells may use: the 9 px cap, lowered to floor((gap - 2) / px) by a
-- measured gap. May be 0 or less when not even one row fits.
function B.RowsCapFor(px, gap)
  local n = P2.RowsCap(px)
  if type(gap) == "number" then
    local c = floor((gap - CLEAR_MARGIN) / px)
    if c < n then n = c end
  end
  return n
end

local function setWarning(code, text)
  warnings[code] = text
end

-- Measures the clearance above PlayerFrame / TargetFrame art (TOPLEFT corner only) and sets
-- B.gap: gap = physicalHeight - top * effectiveScale / pixelFactor, the smallest over the
-- shown regions whose left edge is within 448 px. Returns the gap (nil = nothing measured).
function B.MeasureClearance()
  warnings.clearance = nil
  local regions = {}
  local gap = nil
  if B.Corner() == "TOPLEFT" then
    local factor, physH = B.PixelFactor(), physicalHeight()
    for i = 1, #B.CLEARANCE_REGIONS do
      local spec = B.CLEARANCE_REGIONS[i]
      local frame = _G[spec[1]]
      local art = frame
      for j = 2, #spec do
        if type(art) ~= "table" then break end
        local ok, child = pcall(function() return art[spec[j]] end)
        art = ok and child or nil
      end
      local g, from = nil, nil
      if art ~= frame then
        g, from = regionGap(art, physH, factor), spec[1] .. "." .. table.concat(spec, ".", 2)
      end
      if g == nil then g, from = regionGap(frame, physH, factor), spec[1] end
      if g then
        regions[#regions + 1] = from
        if gap == nil or g < gap then gap = g end
      end
    end
  end
  B.gap = gap
  B.measured = { at = GetTime(), regions = regions }
  if gap and B.RowsCapFor(MIN_PX, gap) < 1 then
    setWarning("clearance", L("the beacon overlaps your player frame: move the frame in Edit Mode or set beaconCorner"))
  end
  return gap
end

-- -------------------------------------------------------------- geometry ----

local function clampPx(px)
  if type(px) ~= "number" or px ~= px then return DEFAULT_PX end
  px = floor(px + 0.5)
  if px < MIN_PX then return MIN_PX elseif px > MAX_PX then return MAX_PX end
  return px
end

-- The strip for event `ev` (pure; DESIGN 2.3 runtime guard), given the cell size setting
-- `px` and the clearance `gap` (physical px, nil = not measured):
--   1. the frame fitted to the 9 px cap at `px` (the nominal frame), if its rows fit the gap;
--   2. else the same frame with 2 px cells, if it fits the gap there;
--   3. else the frame fitted to the rows that fit at 2 px (at least one row);
--   4. if even that needs more rows (placeKey, start, length and one id never go), it is
--      drawn with the rows it needs and `overflow` is set.
-- Returns { px, maxRows, bytes, grid, step, overflow }.
function B.Choose(ev, px, gap, corner)
  px = clampPx(px)
  local nominal = P2.EncodeFrame(ev, P2.Capacity(P2.RowsCap(px)))
  local rows = P2.AdaptiveLayout(#nominal)
  local step, bytes, usePx = 1, nominal, px
  local cap = B.RowsCapFor(px, gap)
  if rows > cap then
    local cap2 = B.RowsCapFor(MIN_PX, gap)
    if px ~= MIN_PX and rows <= cap2 then
      step, usePx, cap = 2, MIN_PX, cap2
    else
      step, usePx, cap = 3, MIN_PX, max(1, cap2)
      bytes = P2.EncodeFrame(ev, P2.Capacity(cap))
    end
  end
  local grid = P2.EncodeCellsFromBytes(bytes, { cellPx = usePx, maxRows = cap, corner = corner or "TOPLEFT" })
  return { px = usePx, maxRows = cap, bytes = bytes, grid = grid, step = step,
           overflow = grid and grid.overflow or false }
end

-- ----------------------------------------------------------------- frame ----

local function ensureFrame()
  if B.frame then return B.frame end
  local f = CreateFrame("Frame", FRAME_NAME, nil)
  f:SetFrameStrata(STRATA)
  f:SetFrameLevel(FRAME_LEVEL)
  if f.SetFixedFrameStrata then pcall(f.SetFixedFrameStrata, f, true) end
  if f.SetFixedFrameLevel then pcall(f.SetFixedFrameLevel, f, true) end
  f:SetScale(1)
  f:EnableMouse(false)
  if f.SetClampedToScreen then f:SetClampedToScreen(false) end
  f:SetPoint("TOPLEFT", WorldFrame, "TOPLEFT", 0, 0)
  f:SetSize(1, 1)
  f:Hide()
  B.frame = f
  return f
end

local function texture(i)
  local t = B.textures[i]
  if t then return t end
  t = B.frame:CreateTexture(nil, "OVERLAY")
  if t.SetSnapToPixelGrid then pcall(t.SetSnapToPixelGrid, t, true) end
  if t.SetTexelSnappingBias then pcall(t.SetTexelSnappingBias, t, 0) end
  t:SetColorTexture(0, 0, 0, 1)
  B.textures[i] = t
  return t
end

local function render(c)
  local f = ensureFrame()
  local grid = c.grid
  local n = grid.rows * grid.cols
  if n > MAX_CELLS then return false end
  local cell = c.px * B.PixelFactor() / frameScale()
  local corner = grid.corner
  if not laid or laid.cols ~= grid.cols or laid.cell ~= cell or laid.corner ~= corner then
    f:ClearAllPoints()
    f:SetPoint(corner, WorldFrame, corner, 0, 0)
    laid = { cols = grid.cols, cell = cell, corner = corner, placed = 0 }
  end
  f:SetSize(grid.cols * cell, grid.rows * cell)
  local cells = grid.cells
  for i = 1, n do
    local t = texture(i)
    if i > laid.placed then
      local r, col = P2.CellPosition(i, grid.cols)
      t:ClearAllPoints()
      t:SetSize(cell, cell)
      t:SetPoint("TOPLEFT", f, "TOPLEFT", col * cell, -r * cell)
    end
    local v = cells[i]
    t:SetColorTexture(v[1], v[2], v[3], 1)
    t:Show()
  end
  if n > laid.placed then laid.placed = n end
  for i = n + 1, #B.textures do B.textures[i]:Hide() end
  return true
end

function B.IsEnabled()
  return (ns.settings and ns.settings.beacon) or GetTime() < B.forceUntil
end

function B.UpdateVisibility()
  local f = B.frame
  if not f then return end
  if B.drawn and B.IsEnabled() then f:Show() else f:Hide() end
end

local function draw(ev, variant)
  local d = B.drawn
  if d and d.ev == ev and d.layout == layoutVersion then return end
  local c = B.Choose(ev, B.SettingCellPx(), B.gap, B.Corner())
  if not (c.grid and render(c)) then return end
  c.ev, c.variant, c.layout, c.at = ev, variant, layoutVersion, GetTime()
  B.drawn = c
  if c.overflow then
    setWarning("overflow", string.format(L("a %d-row song frame does not fit above your player frame and overlaps it: move the frame in Edit Mode or set beaconCorner"),
      c.grid.rows))
  end
  B.UpdateVisibility()
end

-- ------------------------------------------------------- what is on screen ----

local function quietFlags(flags)
  local f = type(flags) == "number" and floor(flags) % 256 or 0
  if P2.HasFlag(f, FLAG.TEST) then f = f - FLAG.TEST end
  if P2.HasFlag(f, FLAG.LOCAL_PLAYBACK) and not P2.HasFlag(f, FLAG.DROP_ACTIVE) then
    f = f - FLAG.LOCAL_PLAYBACK
  end
  return f
end

-- The placeKey-only ARM shown after the full song (same seq, flags and placeKey as the ARM,
-- so the companion's dedupe key does not change).
local function shortArm(ev)
  local pk = ev.placeKey
  if type(pk) ~= "number" then pk = P2.PlaceKey(ev.dropId) end
  return { type = EV.ARM, seq = ev.seq, flags = ev.flags, placeKey = pk, synthesized = "arm", t = ev.t }
end

-- placeKey of the drop that plays now: the Director's active drop, else the last FIRE shown.
local function playingKey()
  local D = ns.Director
  local a = type(D) == "table" and D.active or nil
  if type(a) == "table" and type(a.drop) == "table" then
    local pk = P2.PlaceKey(a.drop.id)
    if pk then return pk end
  end
  return B.playingKey
end

-- A HELLO as the beacon draws it (beacon-owned, never emitted): the music choices of B.SetPrefs and,
-- while DROP_ACTIVE is set, the playing drop's placeKey (D3).
local function hello(src, flags, placeKey, kind)
  local f = type(flags) == "number" and floor(flags) % 256 or 0
  if not P2.HasFlag(f, FLAG.DROP_ACTIVE) then placeKey = nil end
  return { type = EV.HELLO, seq = src.seq, flags = f, placeKey = placeKey, prefs = B.prefs, synthesized = kind,
           t = src.t }
end

-- HELLO with DROP_ACTIVE after a FIRE's hold, with the FIRE's placeKey.
local function playingHello(ev)
  local f = type(ev.flags) == "number" and floor(ev.flags) % 256 or 0
  if not P2.HasFlag(f, FLAG.DROP_ACTIVE) then f = f + FLAG.DROP_ACTIVE end
  local pk = ev.placeKey
  if type(pk) ~= "number" then pk = P2.PlaceKey(ev.dropId) end
  return hello(ev, quietFlags(f), pk, "playing")
end

-- LOCAL_PLAYBACK as the drop that plays now has it (the Director's active drop): a CANCEL of another
-- place carries DROP_ACTIVE but not the playing drop's LOCAL_PLAYBACK (Emit.ComputeFlags), yet the HELLO
-- drawn after it describes the playing drop -- without the flag a D3 late start would play its song
-- over the in-game file (DESIGN-v1.2.1 2.7; review INGAME-3).
local function playingLocal(flags)
  if not P2.HasFlag(flags, FLAG.DROP_ACTIVE) then return flags end
  local D = ns.Director
  local a = type(D) == "table" and D.active or nil
  if type(a) ~= "table" then return flags end
  local has = P2.HasFlag(flags, FLAG.LOCAL_PLAYBACK)
  if a.isLocal and not has then return flags + FLAG.LOCAL_PLAYBACK end
  if not a.isLocal and has then return flags - FLAG.LOCAL_PLAYBACK end
  return flags
end

-- HELLO after a CANCEL's hold. DROP_ACTIVE stays when another drop is still playing; then the HELLO
-- carries that drop's placeKey and LOCAL_PLAYBACK, not the cancelled one's.
local function idleHello(ev)
  return hello(ev, playingLocal(quietFlags(ev.flags)), playingKey(), "idle")
end

local function schedule(at)
  gen = gen + 1
  local mine = gen
  local delay = at - GetTime()
  if delay < 0 then delay = 0 end
  C_Timer.After(delay + 0.01, function()
    if mine == gen then B.Pump() end
  end)
end

-- Schedules the next B.Pump at `at` or at the first CONFIG re-show due before it; nothing to wait
-- for cancels the pending wake-up.
local function wake(at)
  local list = B.cfgReshows
  for i = 1, #list do
    if at == nil or list[i].at < at then at = list[i].at end
  end
  if at then schedule(at) else gen = gen + 1 end
end

local function pauseSteady(now)
  local st = B.steady
  if st and st.fullUntil then
    st.fullLeft = max(0, st.fullUntil - now)
    st.fullUntil = nil
  end
end

-- ----------------------------------------------------------------- queue B ----

-- Keeps queue B at CONFIG_MAX: the oldest CONFIG without an ACTION goes first, else the oldest.
local function trimConfigs()
  local q = B.cfgQueue
  while #q > CONFIG_MAX do
    local victim = 1
    for i = 1, #q do
      if not q[i].action then
        victim = i
        break
      end
    end
    table.remove(q, victim)
    B.configDropped = B.configDropped + 1
  end
end

local function queueConfig(entry, atHead)
  if atHead then table.insert(B.cfgQueue, 1, entry) else B.cfgQueue[#B.cfgQueue + 1] = entry end
  trimConfigs()
end

-- The CONFIG on screen gives way (to FIFO A or an ARM's song window): back to the head of queue B
-- with its full hold.
local function preemptConfig()
  local c = B.cfg
  if not c then return end
  B.cfg = nil
  queueConfig(c.entry, true)
end

-- A CONFIG completed its hold: an ACTION CONFIG's first showing earns one re-show.
local function finishConfig(c)
  B.cfg = nil
  local e = c.entry
  if e.action and not e.reshow and not e.reshowQueued then
    e.reshowQueued = true
    local again = { ev = e.ev, hold = CONFIG_RESHOW_HOLD, action = true, reshow = true }
    B.cfgReshows[#B.cfgReshows + 1] = { entry = again, at = c.holdUntil + CONFIG_RESHOW_AFTER }
  end
end

-- Re-shows whose time has come join queue B.
local function promoteReshows(now)
  local list = B.cfgReshows
  local i = 1
  while i <= #list do
    if list[i].at <= now + EPS then
      queueConfig(table.remove(list, i).entry)
    else
      i = i + 1
    end
  end
end

-- With the beacon setting off, an ACTION CONFIG is shown anyway (the "Send a test signal" pattern).
local function forceConfig(entry)
  if not entry.action or (ns.settings and ns.settings.beacon) then return end
  local seconds = entry.hold + CONFIG_FORCE_MARGIN
  if GetTime() + seconds > B.forceUntil then B.ForceShow(seconds) end
end

-- Draws whatever is due now and schedules the next change.
function B.Pump()
  ensureFrame()
  local now = GetTime()
  promoteReshows(now)
  -- 1. a transient within its hold
  local tr = B.transient
  if tr and now < tr.holdUntil - EPS then
    draw(tr.ev, "transient")
    return wake(tr.holdUntil)
  end
  B.transient = nil
  -- 2. the next transient (FIFO A) takes the screen, from a CONFIG too
  local nextEv = table.remove(B.fifo, 1)
  if nextEv then
    preemptConfig()
    pauseSteady(now)
    B.transient = { ev = nextEv, holdUntil = now + (nextEv.holdFor or TRANSIENT_HOLD[nextEv.type] or SHORT_HOLD) }
    draw(nextEv, "transient")
    return wake(B.transient.holdUntil)
  end
  -- 3. an ARM's full-song window comes before any CONFIG
  local st = B.steady
  if st and st.fullUntil and now >= st.fullUntil - EPS then st.fullLeft, st.fullUntil = 0, nil end
  if st and st.fullLeft and st.fullLeft > EPS then
    preemptConfig()
    if not st.fullUntil then st.fullUntil = now + st.fullLeft end
    draw(st.ev, "full")
    return wake(st.fullUntil)
  end
  -- 4. queue B: the CONFIG on screen until its hold is over, then the next one
  local c = B.cfg
  if c and now >= c.holdUntil - EPS then
    finishConfig(c)
    c = nil
  end
  if not c then
    local entry = table.remove(B.cfgQueue, 1)
    if entry then
      c = { entry = entry, holdUntil = now + entry.hold }
      B.cfg = c
      forceConfig(entry)
    end
  end
  if c then
    draw(c.entry.ev, "config")
    return wake(c.holdUntil)
  end
  -- 5. the steady state
  if st then
    if st.after then
      draw(st.after, "short")
    else
      draw(st.ev, "steady")
      if not st.drawnAt then st.drawnAt = now end
    end
  end
  return wake(nil)
end

-- Drops one queued transient when the queue is full: the oldest PING, else the oldest.
local function trimFifo(q)
  for i = 1, #q do
    if q[i].type == EV.PING then
      table.remove(q, i)
      B.dropped = B.dropped + 1
      return
    end
  end
  table.remove(q, 1)
  B.dropped = B.dropped + 1
end

-- Shows Protocol2 event `ev` (from ns.Emit): FIRE / CANCEL / PING join the transient queue (FIFO A),
-- CONFIG joins queue B (it never changes the steady state); every other event but PING sets the
-- steady state.
function B.Show(ev)
  if type(ev) ~= "table" then return end
  -- A self-test sample (B.ShowSample) gives way to any real event at once.
  if B.transient and B.transient.ev.sample then B.transient = nil end
  for i = #B.fifo, 1, -1 do
    if B.fifo[i].sample then table.remove(B.fifo, i) end
  end
  local t = ev.type
  if t == EV.CONFIG then
    local action = ev.action ~= nil
    queueConfig({ ev = ev, action = action, hold = action and CONFIG_ACTION_HOLD or CONFIG_PREFS_HOLD })
    B.Pump()
    return
  end
  if TRANSIENT_HOLD[t] then
    local q = B.fifo
    q[#q + 1] = ev
    if #q > FIFO_MAX then trimFifo(q) end
  end
  if t == EV.ARM then
    -- The Director arms the next place as soon as it fires one (a landmark, then the zone at the same
    -- spot): the playing HELLO the FIRE's hold leads to would never be drawn, and with it the D3 late
    -- start of a FIRE a cover hid. It gets its time on screen first, after the FIRE (review RP-3).
    local prev = B.steady
    if prev and type(prev.ev) == "table" and prev.ev.synthesized == "playing" then
      local need = PLAYING_HOLD - (prev.drawnAt and (GetTime() - prev.drawnAt) or 0)
      if need > EPS then
        local q = B.fifo
        local again = {}
        for k, v in pairs(prev.ev) do again[k] = v end
        again.holdFor = need
        q[#q + 1] = again
        if #q > FIFO_MAX then trimFifo(q) end
      end
    end
    B.steady = { ev = ev, fullLeft = SONG_HOLD, after = shortArm(ev) }
  elseif t == EV.FIRE then
    local h = playingHello(ev)
    B.playingKey = h.placeKey
    B.steady = { ev = h }
  elseif t == EV.CANCEL then
    if not P2.HasFlag(ev.flags, FLAG.DROP_ACTIVE) then B.playingKey = nil end
    B.steady = { ev = idleHello(ev) }
  elseif t == EV.HELLO then
    if not P2.HasFlag(ev.flags, FLAG.DROP_ACTIVE) then B.playingKey = nil end
    B.steady = { ev = hello(ev, ev.flags, playingKey(), nil) }
  elseif t ~= EV.PING then
    B.steady = { ev = ev }
  end
  B.Pump()
end

-- The music choices every HELLO the beacon draws carries (MusicServices: the four music settings
-- once set in game), or nil. The HELLO on screen changes at once.
function B.SetPrefs(prefs)
  if type(prefs) == "table" then
    B.prefs = { order = prefs.order, corner = prefs.corner, fade = prefs.fade, resume = prefs.resume }
  else
    B.prefs = nil
  end
  local st = B.steady
  if st and type(st.ev) == "table" and st.ev.type == EV.HELLO then st.ev.prefs = B.prefs end
  layoutVersion = layoutVersion + 1
  local d = B.drawn
  if d and B.frame then draw(d.ev, d.variant) end
end

-- /needle selftest beacon (DESIGN-v1.2 6.4 step 2): draws event `ev` -- never emitted, never part
-- of the steady state -- for `seconds` once what is on screen now has had its hold, so the app's
-- diag can decode the largest strip on the real screen. Any real event replaces it at once.
function B.ShowSample(ev, seconds)
  if type(ev) ~= "table" or not TRANSIENT_HOLD[ev.type] then return false end
  ev.sample = true
  ev.holdFor = (type(seconds) == "number" and seconds > 0) and seconds or SONG_HOLD
  local q = B.fifo
  q[#q + 1] = ev
  if #q > FIFO_MAX then trimFifo(q) end
  B.Pump()
  return true
end

-- Re-measures the clearance and redraws what is on screen at the current scale, screen
-- size and settings.
function B.Layout()
  ensureFrame()
  B.MeasureClearance()
  layoutVersion = layoutVersion + 1
  laid = nil
  local d = B.drawn
  if d then draw(d.ev, d.variant) end
  B.UpdateVisibility()
end

-- Self-test, test signal and music requests: show the strip for `seconds` even if the beacon is
-- disabled.
function B.ForceShow(seconds)
  seconds = (type(seconds) == "number" and seconds > 0) and seconds or 60
  B.forceUntil = GetTime() + seconds
  B.UpdateVisibility()
  C_Timer.After(seconds + 0.05, B.UpdateVisibility)
end

-- Warnings for /needle selftest beacon (strings; empty when the strip clears the art).
function B.Warnings()
  local out = {}
  if warnings.clearance then out[#out + 1] = warnings.clearance end
  if warnings.overflow then out[#out + 1] = warnings.overflow end
  return out
end

-- State for /needle status and /needle selftest beacon.
function B.Status()
  local d = B.drawn
  local f = B.frame
  local px = B.SettingCellPx()
  local grid = d and d.grid
  local cap = B.RowsCapFor(px, B.gap)
  return {
    enabled = (ns.settings and ns.settings.beacon) and true or false,
    shown = (f and f:IsShown()) and true or false,
    settingCellPx = px,
    cellPx = d and d.px or px,
    cellUnits = B.CellSize(d and d.px or px),
    rows = grid and grid.rows or 0,
    cols = grid and grid.cols or 0,
    widthPx = grid and grid.widthPx or 0,
    heightPx = grid and grid.heightPx or 0,
    size = grid and string.format("%dx%d", grid.widthPx, grid.heightPx) or "0x0",
    maxRows = cap < 1 and 1 or cap,
    gap = B.gap,
    corner = B.Corner(),
    measuredFrom = B.measured and B.measured.regions or {},
    lastFrameBytes = d and #d.bytes or 0,
    lastFrameType = d and d.ev.type or nil,
    lastFrameSeq = d and d.ev.seq or nil,
    variant = d and d.variant or nil,
    step = d and d.step or nil,
    overflow = d and d.overflow or false,
    current = d and d.ev or nil,
    queued = #B.fifo,
    dropped = B.dropped,
    configQueued = #B.cfgQueue,
    configReshows = #B.cfgReshows,
    configDropped = B.configDropped,
    configShown = B.cfg ~= nil,
    prefs = B.prefs,
    warnings = B.Warnings(),
  }
end

-- ---------------------------------------------------------------- events ----

local function relayoutSoon()
  B.Layout()
  C_Timer.After(REMEASURE_AFTER, B.Layout)
end

ns.On("ND_INIT", function()
  ensureFrame()
  B.Layout()
end)

for _, event in ipairs({ "PLAYER_ENTERING_WORLD", "UI_SCALE_CHANGED", "DISPLAY_SIZE_CHANGED" }) do
  ns.On(event, function() relayoutSoon() end)
end

if type(EventRegistry) == "table" and type(EventRegistry.RegisterCallback) == "function" then
  pcall(EventRegistry.RegisterCallback, EventRegistry, "EditMode.Exit", function() relayoutSoon() end, B)
end

-- TargetFrame is hidden until something is targeted, so the measurements above usually skip it:
-- measure again (next frame, after TargetFrame has shown itself) when the target changes, and
-- redraw only when the gap really changed -- the event fires constantly in combat.
local remeasurePending = false
local function remeasureTarget()
  remeasurePending = false
  local before = B.gap
  local ok, after = pcall(B.MeasureClearance)
  if ok and after ~= before then B.Layout() end
end
ns.On("PLAYER_TARGET_CHANGED", function()
  if remeasurePending then return end
  remeasurePending = true
  C_Timer.After(0, remeasureTarget)
end)

ns.On("ND_SETTING", function(_, key)
  if key == "beacon" then
    B.UpdateVisibility()
  elseif key == "beaconCellPx" or key == "beaconCorner" then
    B.Layout()
  end
end)
