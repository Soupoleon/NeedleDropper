-- shim_beacon2.lua -- additions to wow_shim.lua for the v1.2 protocol cutover tests (A3: Protocol2,
-- Emit, Beacon v2, ChatLink v2). Loaded by tests/v12_beacon_harness.py right after wow_shim.lua,
-- before any addon file. It only adds what the base shim lacks; nothing it defines replaces a
-- base-shim behaviour that other tests rely on.
--
--   SHIM.SetUnitFrameArt(name, opts)   fake PlayerFrame / TargetFrame art for the clearance guard
--   SHIM.TriggerRegistry(event, ...)   EventRegistry:TriggerEvent (e.g. "EditMode.Exit")
--   SHIM.state.chatRestricted          C_RestrictedActions Chat restriction (Enum ... .Chat = 5)
--
-- Unit frame art is placed in PHYSICAL pixels measured from the top-left of the screen, the
-- way a player would measure a screenshot; the regions' GetTop / GetLeft return the matching
-- UI-unit values for the configured effective scale (so the addon's formula
-- gap = physicalHeight - GetTop() * GetEffectiveScale() / PixelUtil.GetPixelToUIUnitFactor()
-- round-trips at every UI scale and screen size).

local SHIM = _G.SHIM
local tinsert = table.insert

SHIM.state.chatRestricted = false

-- ------------------------------------------------------- restricted actions ----

Enum.AddOnRestrictionType.PvPMatch = Enum.AddOnRestrictionType.PvPMatch or 3
Enum.AddOnRestrictionType.Map = Enum.AddOnRestrictionType.Map or 4
Enum.AddOnRestrictionType.Chat = Enum.AddOnRestrictionType.Chat or 5

do
  local base = C_RestrictedActions.IsAddOnRestrictionActive
  function C_RestrictedActions.IsAddOnRestrictionActive(kind)
    if kind == Enum.AddOnRestrictionType.Chat then return SHIM.state.chatRestricted == true end
    return base(kind)
  end
end

-- ------------------------------------------------------------ EventRegistry ----
-- CallbackRegistryMixin subset (Blizzard_SharedXMLBase/CallbackRegistry.lua on the forever
-- branch): RegisterCallback(event, func, owner) -> owner; func(owner, ...) on TriggerEvent.

SHIM.registryCalls = {}
if EventRegistry == nil then
  local callbacks = {}
  local ownerSeq = 0
  EventRegistry = {}
  function EventRegistry:RegisterCallback(event, func, owner)
    if type(event) ~= "string" then error("CallbackRegistryMixin::RegisterCallback 'event' requires string type.", 2) end
    if type(func) ~= "function" then error("CallbackRegistryMixin::RegisterCallback 'func' requires function type.", 2) end
    if owner == nil then
      ownerSeq = ownerSeq + 1
      owner = ownerSeq
    elseif type(owner) == "number" then
      error("CallbackRegistryMixin:RegisterCallback 'owner' as number is reserved internally.", 2)
    end
    local list = callbacks[event]
    if not list then list = {}; callbacks[event] = list end
    list[owner] = func
    tinsert(SHIM.registryCalls, event)
    return owner
  end
  function EventRegistry:UnregisterCallback(event, owner)
    local list = callbacks[event]
    if list then list[owner] = nil end
  end
  function EventRegistry:TriggerEvent(event, ...)
    local list = callbacks[event]
    if not list then return end
    local copy = {}
    for owner, func in pairs(list) do copy[#copy + 1] = { owner, func } end
    for i = 1, #copy do SHIM.protected(copy[i][2], copy[i][1], ...) end
  end
  function SHIM.RegistryHas(event)
    return callbacks[event] ~= nil and next(callbacks[event]) ~= nil
  end
end

function SHIM.TriggerRegistry(event, ...)
  EventRegistry:TriggerEvent(event, ...)
end

-- --------------------------------------------------------- unit frame art ----

local function factor() return 768.0 / SHIM.state.physH end

-- opts (all optional):
--   topPx, leftPx   physical px from the screen's top / left edge (default 14, 20)
--   scale           effective scale of the frame and its art (default UIParent's)
--   shown           the frame itself is shown (default true)
--   artShown        the art texture is shown (default true)
--   noArt           the frame has no Container.FrameTexture (the addon falls back to the frame)
--   noTop           GetTop() returns nothing (MayReturnNothing), noLeft likewise
--   secretTop       GetTop() returns a secret value (SecretWhenAnchoringSecret)
--   frameTopPx      physical top of the frame itself (default: topPx - 4)
function SHIM.SetUnitFrameArt(name, opts)
  opts = opts or {}
  local container = (name == "PlayerFrame") and "PlayerFrameContainer" or (string.gsub(name, "Frame$", "") .. "FrameContainer")
  local f = _G[name]
  if not f then
    f = CreateFrame("Button", name, UIParent)
    f[container] = CreateFrame("Frame", nil, f)
    f.__art = f[container]:CreateTexture(nil, "BORDER")
  end
  local scale = opts.scale
  local art = f.__art
  f[container].FrameTexture = (not opts.noArt) and art or nil
  local function place(region, topPx, leftPx)
    region.GetEffectiveScale = function() return scale or UIParent:GetEffectiveScale() end
    region.GetTop = function(self)
      if opts.noTop then return end
      if opts.secretTop then return SHIM.MakeSecret("GetTop") end
      local es = self:GetEffectiveScale()
      return (SHIM.state.physH - topPx) * factor() / es
    end
    region.GetLeft = function(self)
      if opts.noLeft then return end
      local es = self:GetEffectiveScale()
      return leftPx * factor() / es
    end
  end
  local topPx, leftPx = opts.topPx or 14, opts.leftPx or 20
  place(f, opts.frameTopPx or (topPx - 4), leftPx)
  if f[container] then place(f[container], opts.frameTopPx or (topPx - 4), leftPx) end
  if art then place(art, topPx, leftPx) end
  if opts.shown == false then f:Hide() else f:Show() end
  if art then
    if opts.artShown == false then art:Hide() else art:Show() end
  end
  return f
end

function SHIM.RemoveUnitFrameArt(name)
  _G[name] = nil
end

return SHIM
