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
--   * transient frames, first in first out, each held at least: FIRE 3 s, CANCEL / PING 1 s;
--   * otherwise the steady state of the newest event: HELLO (idle), ARM (the full song for 3 s
--     of drawn time, then an ARM carrying only the placeKey), after a FIRE a HELLO with
--     DROP_ACTIVE, after a CANCEL a HELLO. Synthesized steady frames reuse their event's seq
--     (they are re-renderings, never emitted: no ns.Events entry, no chat line); TEST is
--     cleared in them, and LOCAL_PLAYBACK unless DROP_ACTIVE is set.
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
local CLEAR_MARGIN = 2                -- physical px kept free above the measured art
local CLEAR_LEFT = MAX_CELLS          -- art further right than 448 px is never under the strip
local REMEASURE_AFTER = 1.0           -- measure once more after the UI has settled
local FIFO_MAX = 64
local EPS = 0.001

local TRANSIENT_HOLD = { [EV.FIRE] = SONG_HOLD, [EV.CANCEL] = SHORT_HOLD, [EV.PING] = SHORT_HOLD }

B.SONG_HOLD, B.SHORT_HOLD, B.MAX_CELLS = SONG_HOLD, SHORT_HOLD, MAX_CELLS
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

-- HELLO with DROP_ACTIVE after a FIRE's hold.
local function playingHello(ev)
  local f = type(ev.flags) == "number" and floor(ev.flags) % 256 or 0
  if not P2.HasFlag(f, FLAG.DROP_ACTIVE) then f = f + FLAG.DROP_ACTIVE end
  return { type = EV.HELLO, seq = ev.seq, flags = quietFlags(f), synthesized = "playing", t = ev.t }
end

-- HELLO after a CANCEL's hold (DROP_ACTIVE stays when another drop is still playing).
local function idleHello(ev)
  return { type = EV.HELLO, seq = ev.seq, flags = quietFlags(ev.flags), synthesized = "idle", t = ev.t }
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

local function pauseSteady(now)
  local st = B.steady
  if st and st.fullUntil then
    st.fullLeft = max(0, st.fullUntil - now)
    st.fullUntil = nil
  end
end

-- Draws whatever is due now and schedules the next change.
function B.Pump()
  ensureFrame()
  local now = GetTime()
  local tr = B.transient
  if tr and now < tr.holdUntil - EPS then
    draw(tr.ev, "transient")
    schedule(tr.holdUntil)
    return
  end
  B.transient = nil
  local nextEv = table.remove(B.fifo, 1)
  if nextEv then
    pauseSteady(now)
    B.transient = { ev = nextEv, holdUntil = now + (nextEv.holdFor or TRANSIENT_HOLD[nextEv.type] or SHORT_HOLD) }
    draw(nextEv, "transient")
    schedule(B.transient.holdUntil)
    return
  end
  local st = B.steady
  if st then
    if st.fullLeft and st.fullLeft > EPS then
      if not st.fullUntil then st.fullUntil = now + st.fullLeft end
      if now < st.fullUntil - EPS then
        draw(st.ev, "full")
        schedule(st.fullUntil)
        return
      end
      st.fullLeft, st.fullUntil = 0, nil
    end
    if st.after then draw(st.after, "short") else draw(st.ev, "steady") end
  end
  gen = gen + 1  -- nothing left to wait for
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

-- Shows Protocol2 event `ev` (from ns.Emit): FIRE / CANCEL / PING join the transient queue;
-- every event but PING also sets the steady state.
function B.Show(ev)
  if type(ev) ~= "table" then return end
  -- A self-test sample (B.ShowSample) gives way to any real event at once.
  if B.transient and B.transient.ev.sample then B.transient = nil end
  for i = #B.fifo, 1, -1 do
    if B.fifo[i].sample then table.remove(B.fifo, i) end
  end
  local t = ev.type
  if TRANSIENT_HOLD[t] then
    local q = B.fifo
    q[#q + 1] = ev
    if #q > FIFO_MAX then trimFifo(q) end
  end
  if t == EV.ARM then
    B.steady = { ev = ev, fullLeft = SONG_HOLD, after = shortArm(ev) }
  elseif t == EV.FIRE then
    B.steady = { ev = playingHello(ev) }
  elseif t == EV.CANCEL then
    B.steady = { ev = idleHello(ev) }
  elseif t ~= EV.PING then
    B.steady = { ev = ev }
  end
  B.Pump()
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

-- Self-test: show the strip for `seconds` even if the beacon is disabled.
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
