local ADDON, ns = ...
-- A small, draggable launcher using Blizzard's normal minimap button textures.
-- The main window and Options retain their existing combat and focus handling.

local M = {}
ns.Minimap = M
local text = ns.MinimapText
local button, angle, dragging, ignoreClickUntil
local DEFAULT_ANGLE = 225

local function number(v)
  v = ns.SafeNumber(v)
  if v and v == v and v ~= math.huge and v ~= -math.huge then return v end
end

local function uiDB()
  if not ns.db then return end
  if type(ns.db.ui) ~= "table" then ns.db.ui = {} end
  return ns.db.ui
end

local function savePosition()
  local db = uiDB()
  if db and angle then db.minimapAngle = angle end
end

local function hideTooltip()
  if GameTooltip and GameTooltip:GetOwner() == button then GameTooltip:Hide() end
end

function M.Position()
  if not button then return end
  local width = number(Minimap:GetWidth()) or 140
  local height = number(Minimap:GetHeight()) or 140
  local x, y = math.cos(math.rad(angle)), math.sin(math.rad(angle))
  -- Common square-minimap replacements expose this optional convention.
  if type(GetMinimapShape) == "function" and GetMinimapShape() == "SQUARE" then
    local edge = math.max(math.abs(x), math.abs(y))
    x, y = x / edge, y / edge
  end
  button:ClearAllPoints()
  button:SetPoint("CENTER", Minimap, "CENTER", x * (width / 2 + 10), y * (height / 2 + 10))
end

local function dragPosition()
  local x, y = GetCursorPosition()
  local cx, cy = Minimap:GetCenter()
  local scale = number(Minimap:GetEffectiveScale())
  x, y, cx, cy = number(x), number(y), number(cx), number(cy)
  if not (x and y and cx and cy and scale and scale > 0) then return end
  x, y = x / scale - cx, y / scale - cy
  if x == 0 and y == 0 then return end
  angle = math.deg(math.atan2(y, x)) % 360
  M.Position()
end

local function stopDragging()
  if not dragging then return end
  dragging = false
  button:SetScript("OnUpdate", nil)
  savePosition()
  -- A drag's release must not also open the window; the next ordinary click works.
  ignoreClickUntil = GetTime() + 0.15
end

function M.Refresh()
  if not button then return end
  M.Position()
  if ns.settings and ns.settings.minimapIcon == false then
    button:Hide()
  else
    button:Show()
  end
end

function M.Init()
  if button then M.Refresh(); return end
  local db = uiDB()
  if not (db and Minimap) then return end
  angle = (number(db.minimapAngle) or DEFAULT_ANGLE) % 360
  savePosition()
  button = CreateFrame("Button", "NeedleDropperMinimapButton", Minimap)
  button:SetSize(31, 31)
  button:SetFrameStrata("MEDIUM")
  button:SetFrameLevel(Minimap:GetFrameLevel() + 5)
  button:SetClampedToScreen(true)
  button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  button:RegisterForDrag("LeftButton")

  local background = button:CreateTexture(nil, "BACKGROUND")
  background:SetSize(20, 20)
  background:SetPoint("TOPLEFT", button, "TOPLEFT", 7, -5)
  background:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
  local icon = button:CreateTexture(nil, "ARTWORK")
  icon:SetSize(20, 20)
  icon:SetPoint("TOPLEFT", button, "TOPLEFT", 7, -5)
  icon:SetTexture("Interface\\Icons\\INV_Misc_Drum_01")
  icon:SetTexCoord(0.05, 0.95, 0.05, 0.95)
  local border = button:CreateTexture(nil, "OVERLAY")
  border:SetSize(53, 53)
  border:SetPoint("TOPLEFT", button, "TOPLEFT", 0, 0)
  border:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
  button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")

  button:SetScript("OnClick", function(_, mouseButton)
    if dragging or (ignoreClickUntil and GetTime() < ignoreClickUntil) then return end
    NeedleDropper_OnAddonCompartmentClick(ADDON, mouseButton)
  end)
  button:SetScript("OnEnter", function(self)
    if dragging or not GameTooltip then return end
    GameTooltip:SetOwner(self, "ANCHOR_LEFT")
    ns.FillCompartmentTooltip(GameTooltip)
    GameTooltip:AddLine(text.drag, 0.1, 1, 0.1)
    GameTooltip:AddLine(text.hide, 0.75, 0.75, 0.75, true)
    GameTooltip:Show()
  end)
  button:SetScript("OnLeave", hideTooltip)
  button:SetScript("OnDragStart", function()
    dragging = true
    hideTooltip()
    button:SetScript("OnUpdate", dragPosition)
    dragPosition()
  end)
  button:SetScript("OnDragStop", function()
    if not dragging then return end
    dragPosition()
    stopDragging()
  end)
  button:SetScript("OnHide", function()
    stopDragging()
    hideTooltip()
  end)
  Minimap:HookScript("OnSizeChanged", M.Position)
  M.Refresh()
end

ns.On("ND_INIT", M.Init)
ns.On("PLAYER_LOGIN", M.Init)
ns.On("ND_SETTING", function(_, key)
  if key == "minimapIcon" then M.Refresh() end
end)
ns.On("ND_RESET", function(_, what)
  if what == "settings" then M.Refresh() end
end)
