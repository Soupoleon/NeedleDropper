local ADDON, ns = ...
-- Toast: the in-game title card (SPEC-v1.1 B), the product's only popup.
--
--              E L W Y N N   F O R E S T
--          ---------------------------------      1 physical px, grows from the centre
--             Low Roar  ·  I'll Keep Coming
--
-- Quiet and cinematic: no frame art, no background box, no mouse. NeedleDropperTitleCard is
-- a child of UIParent (Alt+Z hides it with the rest of the UI) in the FULLSCREEN_DIALOG
-- strata; its TOP sits toastY of the screen height below the top of UIParent.
--   line 1  the place, client-localized (C_Map map / area names, the live subzone text,
--           GetInstanceInfo), upper case with letter spacing (none for CJK / Hangul)
--   line 2  a thin rule that grows outward from the centre while the card fades in
--   line 3  "Artist  ·  Title" (U+00B7 middle dot)
-- The animation is OnUpdate-driven and computed from GetTime(), so it does not depend on the
-- frame rate and a card hidden by Alt+Z or a cinematic does not resume later: fade in 1.2 s
-- with an 8 px upward drift, hold settings.toastSeconds, fade out 2.0 s. A new card restarts
-- the timeline; combat start (or Hide(true)) fades it out in 0.5 s. The OnUpdate script only
-- exists while a card is on screen, so an idle card costs nothing per frame.
--
-- Text is handled byte-wise on purpose: string.upper / %s / %a follow the C locale outside
-- the client and would corrupt UTF-8 there.

local T = {}
ns.Toast = T

local FADE_IN, FADE_OUT, QUICK_OUT = 1.2, 2.0, 0.5
local DRIFT = 8                       -- upward drift while fading in (UI units, x toastScale)
local TITLE_SIZE, CREDIT_SIZE = 30, 15
local GAP_RULE, GAP_CREDIT = 10, 9    -- place -> rule and rule -> credit gaps (x toastScale)
local RULE_PAD, RULE_MIN = 24, 160    -- rule overhang on each side, minimum width (x toastScale)
local RULE_ALPHA = 0.4
local TEXT_R, TEXT_G, TEXT_B = 0.95, 0.93, 0.88
local SHADOW_ALPHA = 0.6
local MAX_WIDTH = 0.9                 -- share of the screen width one line may take
local MIN_FIT_SIZE = 10               -- long lines shrink to fit, but not below this size
local FALLBACK_FONT = "Fonts\\FRIZQT__.TTF"
local MIDDOT = "\194\183"
local UTF8_CHAR = "[%z\1-\127\194-\244][\128-\191]*"
local WIDE_LEAD = "[\227-\244]"       -- lead byte of a code point >= U+3000 (CJK, kana, Hangul)
local WORD = "[^ \t\r\n]+"            -- ASCII whitespace only (see the note above)

T.FADE_IN, T.FADE_OUT, T.QUICK_OUT, T.DRIFT = FADE_IN, FADE_OUT, QUICK_OUT, DRIFT
T.TITLE_SIZE, T.CREDIT_SIZE, T.RULE_ALPHA = TITLE_SIZE, CREDIT_SIZE, RULE_ALPHA
T.TEST_PLACE, T.TEST_CREDIT, T.PREVIEW_CREDIT = "NeedleDropper", "Test drop", "Preview"
T.card = nil      -- the card on screen (timeline + layout); nil while nothing is shown
T.frame = nil

local byte, char, find, gsub, gmatch = string.byte, string.char, string.find, string.gsub, string.gmatch
local floor, max, min = math.floor, math.max, math.min
local frame, placeFS, rule, creditFS

-- ------------------------------------------------------------------ text ----

-- Upper case of a code point in the 2-byte UTF-8 range: Latin-1, Latin Extended-A and
-- Cyrillic (every letter the Latin and Russian game locales use). Others are unchanged.
local function upperCodePoint(cp)
  if cp >= 0xE0 and cp <= 0xFE then
    if cp ~= 0xF7 then return cp - 32 end                     -- à..þ -> À..Þ (not ÷)
  elseif cp == 0xFF then
    return 0x178                                               -- ÿ -> Ÿ
  elseif cp >= 0x100 and cp <= 0x17F then
    if (cp >= 0x139 and cp <= 0x148) or (cp >= 0x179 and cp <= 0x17E) then
      if cp % 2 == 0 then return cp - 1 end                    -- Ĺ ĺ .. Ň ň, Ź ź .. Ž ž
    elseif cp <= 0x12F or (cp >= 0x132 and cp <= 0x137) or (cp >= 0x14A and cp <= 0x177) then
      if cp % 2 == 1 then return cp - 1 end                    -- Ā ā .. Į į, Ĳ ĳ .., Ŋ ŋ .. Ŷ ŷ
    end
  elseif cp >= 0x430 and cp <= 0x44F then
    return cp - 32                                             -- а..я -> А..Я
  elseif cp >= 0x450 and cp <= 0x45F then
    return cp - 80                                             -- ѐ..џ -> Ѐ..Џ
  end
  return cp
end

local function upperChar(ch)
  local n = #ch
  if n == 1 then
    local b = byte(ch)
    if b >= 97 and b <= 122 then return char(b - 32) end
  elseif n == 2 then
    local b1, b2 = byte(ch, 1, 2)
    if b1 >= 0xC2 and b1 <= 0xDF and b2 >= 0x80 and b2 <= 0xBF then
      local cp = (b1 - 0xC0) * 64 + (b2 - 0x80)
      local up = upperCodePoint(cp)
      if up ~= cp then return char(0xC0 + floor(up / 64), 0x80 + up % 64) end
    end
  end
  return ch
end

-- Upper case without the C library (ASCII, Latin-1, Latin Extended-A, Cyrillic); CJK,
-- Hangul and everything else pass through unchanged.
function T.Upper(s)
  if type(s) ~= "string" then return "" end
  return (gsub(s, UTF8_CHAR, upperChar))
end

local function trimSpaces(s)
  return (gsub(gsub(s, "^[ \t\r\n]+", ""), "[ \t\r\n]+$", ""))
end

-- "Elwynn Forest" -> "E L W Y N N   F O R E S T": upper case, one space between the
-- characters of a word, three between words. Text with any code point >= U+3000 (CJK, kana,
-- Hangul) is upper-cased and trimmed but not spaced.
function T.SpacedCaps(label)
  if type(label) ~= "string" then return "" end
  local up = T.Upper(label)
  if find(up, WIDE_LEAD) then return trimSpaces(up) end
  local words = {}
  for word in gmatch(up, WORD) do
    local chars = {}
    for ch in gmatch(word, UTF8_CHAR) do chars[#chars + 1] = ch end
    if #chars > 0 then words[#words + 1] = table.concat(chars, " ") end
  end
  return table.concat(words, "   ")
end

-- Song credit "Artist · Title" (or whichever part is known), as used in chat lines.
-- wide = true gives the card's airier "Artist  ·  Title".
function T.Subtitle(drop, wide)
  if type(drop) ~= "table" then return "" end
  local artist = type(drop.artist) == "string" and drop.artist or ""
  local title = type(drop.title) == "string" and drop.title or ""
  if artist ~= "" and title ~= "" then
    local sep = wide and ("  " .. MIDDOT .. "  ") or (" " .. MIDDOT .. " ")
    return artist .. sep .. title
  end
  if title ~= "" then return title end
  return artist
end

-- A literal "|" would start a UI escape sequence (colour, texture, link) in a font string.
local function escapeText(s)
  return (gsub(s, "|", "||"))
end

-- ------------------------------------------------------------- the place ----

local function nonEmpty(v)
  v = ns.SafeString(v)
  if v and v ~= "" then return v end
  return nil
end

local function call(fn, ...)
  if type(fn) ~= "function" then return false end
  return pcall(fn, ...)
end

local function mapName(mapID)
  if not (mapID and C_Map and C_Map.GetMapInfo) then return nil end
  local ok, info = pcall(C_Map.GetMapInfo, mapID)
  if not ok or ns.IsSecret(info) or type(info) ~= "table" then return nil end
  return nonEmpty(info.name)
end

local function areaName(areaID)
  if not areaID then return nil end
  if ns.AreaName then return nonEmpty(ns.AreaName(areaID)) end
  if not (C_Map and C_Map.GetAreaInfo) then return nil end
  local ok, name = pcall(C_Map.GetAreaInfo, areaID)
  return ok and nonEmpty(name) or nil
end

-- The live subzone text, but only while it names this landmark drop.
local function liveSubzoneFor(drop)
  if not drop.subzoneLower then return nil end
  local ok, sub = call(GetSubZoneText)
  sub = ok and nonEmpty(sub) or nil
  if sub and string.lower(sub) == drop.subzoneLower then return sub end
  return nil
end

-- GetInstanceInfo() describes wherever the player is (the continent outside instances), so
-- its name is only used inside the drop's own instance.
local function instanceName(instanceID)
  if not instanceID then return nil end
  local ok, inside = call(IsInInstance)
  if not ok or ns.IsSecret(inside) or not inside then return nil end
  local ok2, name, _, _, _, _, _, _, id = call(GetInstanceInfo)
  if not ok2 or ns.SafeNumber(id) ~= instanceID then return nil end
  return nonEmpty(name)
end

-- Line 1 source text, localized by the client where it can be: zone -> map name, landmark ->
-- area name (else the live subzone text when it is this landmark), instance -> instance name;
-- otherwise the pack label.
function T.PlaceName(drop)
  if type(drop) ~= "table" then return "" end
  if drop.kind == "test" or drop.id == "test" then return T.TEST_PLACE end
  local name
  if drop.kind == "zone" then
    name = mapName(drop.mapID)
  elseif drop.kind == "subzone" then
    name = areaName(drop.areaID) or liveSubzoneFor(drop)
  elseif drop.kind == "instance" then
    name = instanceName(drop.instanceID)
  end
  return name or nonEmpty(drop.label) or nonEmpty(drop.subzone) or nonEmpty(drop.id) or ""
end

-- --------------------------------------------------------------- layout ----

local function settingNumber(key, default)
  local v = ns.settings and ns.settings[key]
  if type(v) ~= "number" or v ~= v then v = default end
  local meta = ns.SETTINGS_BY_KEY and ns.SETTINGS_BY_KEY[key]
  if meta and type(meta.min) == "number" and v < meta.min then v = meta.min end
  if meta and type(meta.max) == "number" and v > meta.max then v = meta.max end
  return v
end

local function cardScale() return settingNumber("toastScale", 1.0) end
local function cardY() return settingNumber("toastY", 0.22) end
local function holdSeconds() return settingNumber("toastSeconds", 7) end

local function uiSize()
  local w, h = 1366, 768
  if UIParent then
    local ok, v = pcall(UIParent.GetWidth, UIParent)
    if ok and type(v) == "number" and v > 0 then w = v end
    ok, v = pcall(UIParent.GetHeight, UIParent)
    if ok and type(v) == "number" and v > 0 then h = v end
  end
  return w, h
end

-- Physical pixel -> UI unit factor (768 / physical screen height), like the beacon.
local function pixelFactor()
  if PixelUtil and PixelUtil.GetPixelToUIUnitFactor then
    local ok, f = pcall(PixelUtil.GetPixelToUIUnitFactor)
    if ok and type(f) == "number" and f > 0 then return f end
  end
  if GetPhysicalScreenSize then
    local ok, _, h = pcall(GetPhysicalScreenSize)
    if ok and type(h) == "number" and h > 0 then return 768 / h end
  end
  return 768 / 1080
end

-- One physical pixel in the card's own units.
function T.OnePixel()
  local scale = 1
  if frame then
    local ok, s = pcall(frame.GetEffectiveScale, frame)
    if ok and type(s) == "number" and s > 0 then scale = s end
  end
  return pixelFactor() / scale
end

local function fontPath()
  return nonEmpty(STANDARD_TEXT_FONT) or FALLBACK_FONT
end

-- STANDARD_TEXT_FONT (locale-correct), else FRIZQT, else the stock font object.
local function setFont(fs, size, fallbackObject)
  local path = fontPath()
  local ok, res = pcall(fs.SetFont, fs, path, size, "OUTLINE")
  if ok and res ~= false then return path end
  if path ~= FALLBACK_FONT then
    ok, res = pcall(fs.SetFont, fs, FALLBACK_FONT, size, "OUTLINE")
    if ok and res ~= false then return FALLBACK_FONT end
  end
  local obj = fallbackObject and _G[fallbackObject]
  if obj then pcall(fs.SetFontObject, fs, obj) end
  return nil
end

local function textWidth(fs)
  local ok, w = pcall(fs.GetStringWidth, fs)
  if ok and type(w) == "number" and w > 0 and w < math.huge then return w end
  return 0
end

-- Sets a line's font at `size`, shrinking it when the line is wider than `maxW`.
local function fitLine(fs, size, maxW, fallbackObject)
  setFont(fs, size, fallbackObject)
  local w = textWidth(fs)
  if w > maxW then
    local fitted = max(MIN_FIT_SIZE, floor(size * maxW / w))
    if fitted < size then
      setFont(fs, fitted, fallbackObject)
      size = fitted
    end
  end
  return size
end

local function styleLine(fs)
  fs:SetTextColor(TEXT_R, TEXT_G, TEXT_B, 1)
  fs:SetShadowColor(0, 0, 0, SHADOW_ALPHA)
  fs:SetShadowOffset(1, -1)
  fs:SetJustifyH("CENTER")
  if fs.SetWordWrap then fs:SetWordWrap(false) end
end

local function create()
  if frame then return frame end
  if type(CreateFrame) ~= "function" or not UIParent then return nil end
  local ok, f = pcall(CreateFrame, "Frame", "NeedleDropperTitleCard", UIParent)
  if not ok or type(f) ~= "table" then return nil end
  f:SetFrameStrata("FULLSCREEN_DIALOG")
  f:EnableMouse(false)
  f:SetAlpha(0)
  f:Hide()
  placeFS = f:CreateFontString(nil, "OVERLAY")
  rule = f:CreateTexture(nil, "ARTWORK")
  creditFS = f:CreateFontString(nil, "OVERLAY")
  styleLine(placeFS)
  styleLine(creditFS)
  setFont(placeFS, TITLE_SIZE, "GameFontNormalHuge")   -- a font before any SetText
  setFont(creditFS, CREDIT_SIZE, "GameFontHighlight")
  rule:SetColorTexture(1, 1, 1, RULE_ALPHA)
  rule:Hide()
  frame = f
  T.frame = f
  return f
end

-- Fonts, rule and resting position of the current card from the settings and screen size.
local function layout()
  local c = T.card
  if not (c and frame) then return end
  local s = cardScale()
  local uiW, uiH = uiSize()
  local maxW = uiW * MAX_WIDTH
  c.scale = s
  c.placeSize = fitLine(placeFS, TITLE_SIZE * s, maxW, "GameFontNormalHuge")
  c.creditSize = fitLine(creditFS, CREDIT_SIZE * s, maxW, "GameFontHighlight")
  c.pixel = T.OnePixel()
  rule:SetHeight(c.pixel)
  placeFS:ClearAllPoints()
  placeFS:SetPoint("TOP", frame, "TOP", 0, 0)
  rule:ClearAllPoints()
  rule:SetPoint("TOP", placeFS, "BOTTOM", 0, -GAP_RULE * s)
  creditFS:ClearAllPoints()
  creditFS:SetPoint("TOP", rule, "BOTTOM", 0, -GAP_CREDIT * s)
  c.ruleW = min(maxW, max(RULE_MIN * s, max(textWidth(placeFS), textWidth(creditFS)) + 2 * RULE_PAD * s))
  c.restY = -uiH * cardY()
  c.drift = DRIFT * s
  frame:SetSize(c.ruleW, c.placeSize + GAP_RULE * s + c.pixel + GAP_CREDIT * s + c.creditSize)
  frame:ClearAllPoints()
  c.appliedLift = nil   -- the next step re-anchors the frame and resizes the rule
end

-- ------------------------------------------------------------ animation ----

local function smooth(p)
  if p <= 0 then return 0 end
  if p >= 1 then return 1 end
  return p * p * (3 - 2 * p)
end

local function easeOut(p)
  if p <= 0 then return 0 end
  if p >= 1 then return 1 end
  local q = 1 - p
  return 1 - q * q * q
end

T.Smooth, T.EaseOut = smooth, easeOut

-- alpha, lift of card `c` at time `now` (lift 0 = DRIFT below its place with no rule yet,
-- 1 = at rest with the full rule), or nil once the card is over.
local function sample(c, now)
  local t = now - c.start
  local alpha, lift = 1, 1
  if t < FADE_IN then
    local p = t / FADE_IN
    alpha, lift = smooth(p), easeOut(p)
  end
  local outAt, outLen, outFrom = c.outAt, c.outLen, c.outFrom
  if outAt then
    lift = c.outLift          -- a forced fade freezes the drift and the rule where they were
  else
    outAt, outLen, outFrom = c.start + FADE_IN + c.hold, FADE_OUT, 1
  end
  if now >= outAt then
    local q = (now - outAt) / outLen
    if q >= 1 then return nil end
    alpha = outFrom * (1 - smooth(q))
  end
  return alpha, lift
end

-- alpha, lift of the card on screen at `now` (default GetTime()); nil when there is none.
function T.Sample(now)
  if not T.card then return nil end
  return sample(T.card, now or GetTime())
end

local function finish()
  T.card = nil
  if not frame then return end
  frame:SetScript("OnUpdate", nil)
  frame:SetAlpha(0)
  frame:Hide()
end

local function apply(c, alpha, lift)
  c.alpha, c.lift = alpha, lift
  frame:SetAlpha(alpha)
  if c.appliedLift ~= lift then
    c.appliedLift = lift
    c.y = c.restY - c.drift * (1 - lift)
    frame:SetPoint("TOP", UIParent, "TOP", 0, c.y)
    c.ruleWidth = c.ruleW * lift
    if c.ruleWidth >= 0.5 then
      rule:SetWidth(c.ruleWidth)
      rule:Show()
    else
      rule:Hide()
    end
  end
end

local function step(now)
  local c = T.card
  if not (c and frame) then return finish() end
  local alpha, lift = sample(c, now)
  if not alpha then return finish() end
  apply(c, alpha, lift)
end

local function onUpdate()
  step(GetTime())
end

-- Fades the card out over `len` seconds from where it is now (unless it ends sooner anyway).
local function fadeOut(len)
  local c = T.card
  if not (c and frame) then return end
  local now = GetTime()
  local alpha, lift = sample(c, now)
  if not alpha or alpha <= 0.001 then return finish() end
  local endsAt = c.outAt and (c.outAt + c.outLen) or (c.start + FADE_IN + c.hold + FADE_OUT)
  if now + len >= endsAt then return end
  c.outAt, c.outLen, c.outFrom, c.outLift = now, len, alpha, lift
  apply(c, alpha, lift)
end

-- ------------------------------------------------------------------ API ----

-- Shows the card for `drop` ({kind, label, title, artist, mapID / areaID / instanceID ...}),
-- replacing (restarting) a card that is showing. opts: { preview = true (show even while
-- title cards are off), line1 = place text, line3 = credit text }. Returns true if shown.
function T.Show(drop, opts)
  if type(drop) ~= "table" then return false end
  if type(opts) ~= "table" then opts = nil end
  local preview = (opts and opts.preview) and true or false
  if not preview and not (ns.settings and ns.settings.toast) then return false end
  if not create() then return false end
  local isTest = drop.kind == "test" or drop.id == "test"
  local place = (opts and type(opts.line1) == "string") and opts.line1 or T.PlaceName(drop)
  local credit = (opts and type(opts.line3) == "string") and opts.line3 or nil
  if credit == nil then credit = isTest and T.TEST_CREDIT or T.Subtitle(drop, true) end
  local now = GetTime()
  T.card = { start = now, hold = holdSeconds(), id = drop.id, kind = drop.kind, preview = preview,
             place = place, line1 = T.SpacedCaps(place), line3 = credit }
  placeFS:SetText(escapeText(T.card.line1))
  creditFS:SetText(escapeText(credit))
  layout()
  frame:Show()
  frame:SetScript("OnUpdate", onUpdate)
  step(now)
  return true
end

-- Fades the card away: 0.5 s when `quick` (cancel, combat), else the normal 2 s.
function T.Hide(quick)
  if not T.IsShown() then return end
  fadeOut(quick and QUICK_OUT or FADE_OUT)
end

function T.IsShown()
  return frame ~= nil and T.card ~= nil and frame:IsShown() and true or false
end

-- Re-applies the size / position settings and the screen size to the card on screen.
function T.Layout()
  if not (frame and T.card) then return false end
  layout()
  step(GetTime())
  return true
end

local function currentPlace()
  local L = ns.Location
  if L and L.Snapshot then
    local ok, snap = pcall(L.Snapshot)
    if ok and type(snap) == "table" then
      local name
      if snap.inInstance then name = nonEmpty(snap.instanceName) or nonEmpty(snap.zoneName) end
      name = name or nonEmpty(snap.zoneInfoName) or nonEmpty(snap.zoneName)
      if name then return name end
    end
  end
  local ok, zone = call(GetRealZoneText)
  return (ok and nonEmpty(zone)) or "NeedleDropper"
end

-- /needle toast: a preview card for where the player is - the drop that belongs here
-- (instance > landmark > zone, the Director's priority) or, when nothing is mapped, the place
-- name with "Preview" as line 3. Shown even while title cards are off; plays nothing and
-- emits nothing. Returns { drop = drop or nil, place = line 1 source text, shown = bool }.
function T.Preview()
  local drop
  local L = ns.Location
  if L and L.Candidates then
    local ok, cands = pcall(L.Candidates)
    if ok and type(cands) == "table" then drop = cands[1] end
  end
  if drop then
    local shown = T.Show(drop, { preview = true })
    return { drop = drop, place = T.card and T.card.place or T.PlaceName(drop), shown = shown }
  end
  local place = currentPlace()
  local shown = T.Show({ id = "preview", kind = "preview", label = place },
    { preview = true, line1 = place, line3 = T.PREVIEW_CREDIT })
  return { place = place, shown = shown }
end

-- One line for /needle status.
function T.StatusLine()
  if not (ns.settings and ns.settings.toast) then
    return "title card: off (/needle toast on turns it back on; /needle toast previews it)"
  end
  return string.format("title card: on - size %d%%, %d%% from the top, %d s (/needle toast previews it here)",
    floor(cardScale() * 100 + 0.5), floor(cardY() * 100 + 0.5), floor(holdSeconds() + 0.5))
end

-- --------------------------------------------------------------- events ----

ns.On("PLAYER_REGEN_DISABLED", function()
  if T.IsShown() then fadeOut(QUICK_OUT) end
end)

ns.On("ND_SETTING", function(_, key, value)
  if key == "toast" then
    if not value then T.Hide(true) end
  elseif key == "toastScale" or key == "toastY" then
    T.Layout()
  end
end)

ns.On("ND_RESET", function(_, what)
  if what == "settings" then T.Layout() end
end)

for _, event in ipairs({ "UI_SCALE_CHANGED", "DISPLAY_SIZE_CHANGED" }) do
  ns.On(event, function() T.Layout() end)
end
