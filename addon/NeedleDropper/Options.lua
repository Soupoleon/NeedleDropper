local ADDON, ns = ...
-- Options: a Settings API panel (Game Menu > Options > AddOns > NeedleDropper) for every
-- setting in ns.SETTINGS except the advanced ones (beaconCorner: /needle set only), grouped
-- under section headers (DESIGN-v1.2 4.7):
--   * an "Open NeedleDropper" row at the top (the window is where soundtracks are edited);
--   * a "Preview" button for the title card (its size / position sliders re-lay out a card
--     that is showing, so the effect is visible while dragging);
--   * "Send a test signal" under Companion link (PING + TEST: the app shows a notification).
-- Every Settings call is guarded; if the API is missing or changed, the addon stays fully
-- usable through /needle settings and /needle set, and a missing header or button never
-- costs a setting.

local O = {}
ns.Options = O

O.category = nil
O.layout = nil
O.failed = nil
O.controls = 0
O.controlErrors = 0
O.extras = 0          -- section headers and buttons added to the panel

-- Section header shown above the setting with this key.
local SECTIONS = {
  enabled = "Needle drops",
  combatFadeSec = "Playback",
  toast = "Title card",
  beacon = "Companion link",
  debug = "Troubleshooting",
}
local PREVIEW_AFTER = "toastSeconds"   -- the title card's Preview button follows this setting
local SIGNAL_AFTER = "chatlogPadBytes" -- "Send a test signal" ends the Companion link section

local L = ns.L or setmetatable({}, { __index = function(_, k) return k end })

local function settingVarType(meta, default)
  local vt = Settings and Settings.VarType
  if vt then
    if meta.type == "boolean" and vt.Boolean then return vt.Boolean end
    if meta.type == "number" and vt.Number then return vt.Number end
    if meta.type == "string" and vt.String then return vt.String end
  end
  return type(default)
end

-- Settings callbacks pass (setting, value) or (owner, setting, value): the value is last.
local function valueChanged(key)
  return function(...)
    local n = select("#", ...)
    local value = select(n, ...)
    ns.OnSettingEdited(key, value)
  end
end

-- Slider value label: a percentage for percent settings, two decimals for fine steps, whole
-- numbers otherwise.
function O.FormatSliderValue(meta, v)
  if type(v) ~= "number" then return tostring(v) end
  if meta.percent then return string.format("%d%%", math.floor(v * 100 + 0.5)) end
  if not meta.integer and (meta.step or 1) < 1 then return string.format("%.2f", v) end
  return tostring(math.floor(v + 0.5))
end

local function addControl(category, meta)
  local default = ns.DEFAULTS[meta.key]
  local variable = "NeedleDropper_" .. meta.key
  local setting = Settings.RegisterAddOnSetting(category, variable, meta.key, ns.settings,
    settingVarType(meta, default), meta.label, default)
  if type(setting) ~= "table" then error("RegisterAddOnSetting returned nothing for " .. meta.key) end
  if setting.SetValueChangedCallback then
    setting:SetValueChangedCallback(valueChanged(meta.key))
  elseif Settings.SetOnValueChangedCallback then
    Settings.SetOnValueChangedCallback(variable, valueChanged(meta.key))
  end
  if meta.type == "boolean" then
    Settings.CreateCheckbox(category, setting, meta.tip)
  elseif meta.type == "number" then
    local options = Settings.CreateSliderOptions(meta.min, meta.max, meta.step or 1)
    if options and options.SetLabelFormatter and MinimalSliderWithSteppersMixin
       and MinimalSliderWithSteppersMixin.Label then
      options:SetLabelFormatter(MinimalSliderWithSteppersMixin.Label.Right, function(v)
        return O.FormatSliderValue(meta, v)
      end)
    end
    Settings.CreateSlider(category, setting, options, meta.tip)
  elseif meta.options then
    local function getOptions()
      local container = Settings.CreateControlTextContainer()
      for i = 1, #meta.options do container:Add(meta.options[i][1], meta.options[i][2]) end
      return container:GetData()
    end
    Settings.CreateDropdown(category, setting, getOptions, meta.tip)
  end
end

-- Adds a non-setting element (header, button) to the panel's layout; optional everywhere.
local function addExtra(what, makeInitializer)
  local layout = O.layout
  if not (type(layout) == "table" and type(layout.AddInitializer) == "function") then return false end
  local ok, err = pcall(function()
    local initializer = makeInitializer()
    if initializer == nil then error("no initializer") end
    layout:AddInitializer(initializer)
  end)
  if ok then
    O.extras = O.extras + 1
  else
    ns.Debug("options: %s: %s", what, tostring(err))
  end
  return ok
end

local function addHeader(text)
  if type(CreateSettingsListSectionHeaderInitializer) ~= "function" then return false end
  return addExtra("header " .. text, function() return CreateSettingsListSectionHeaderInitializer(text) end)
end

function O.PreviewClicked()
  if ns.Toast and ns.Toast.Preview then ns.Toast.Preview() end
end

local function addButton(what, name, text, onClick, tip)
  if type(CreateSettingsButtonInitializer) ~= "function" then return false end
  return addExtra(what, function()
    return CreateSettingsButtonInitializer(name, text, onClick, tip, true)
  end)
end

local function addPreviewButton()
  return addButton("preview button", L["Title card preview"], L["Preview"], O.PreviewClicked,
    L["Shows the title card for where you are now, so you can try the size, position and time settings."])
end

-- Opens the window from the Settings panel (DESIGN-v1.2 4.7: content is edited there, not here).
-- Settings stays open underneath: the window's DIALOG strata is above it.
function O.OpenWindowClicked()
  local U = ns.UI
  if type(U) == "table" and type(U.Open) == "function" then
    U.Open()
  else
    ns.Print(L["the NeedleDropper window is not available."])
  end
end

local function addOpenButton()
  return addButton("open button", L["NeedleDropper window"], L["Open NeedleDropper"], O.OpenWindowClicked,
    L["Your soundtracks and drops are edited in the NeedleDropper window (also /needle)."])
end

function O.SignalClicked()
  local S = ns.SelfTest
  if type(S) == "table" and type(S.Signal) == "function" then S.Signal() end
end

local function addSignalButton()
  return addButton("signal button", L["Check the link"], L["Send a test signal"], O.SignalClicked,
    L["Sends a test signal through the beacon (and the chat log when it is on). Your NeedleDropper app shows a notification within 2 seconds if it can see the game."])
end

local function build()
  local category, layout = Settings.RegisterVerticalLayoutCategory("NeedleDropper")
  if type(category) ~= "table" then error("RegisterVerticalLayoutCategory returned nothing") end
  O.layout = layout
  addOpenButton()
  for i = 1, #ns.SETTINGS do
    local meta = ns.SETTINGS[i]
    if not meta.advanced then
      if SECTIONS[meta.key] then addHeader(SECTIONS[meta.key]) end
      local ok, err = pcall(addControl, category, meta)
      if ok then
        O.controls = O.controls + 1
      else
        O.controlErrors = O.controlErrors + 1
        ns.Debug("options: %s: %s", meta.key, tostring(err))
      end
    end
    if meta.key == PREVIEW_AFTER then addPreviewButton() end
    if meta.key == SIGNAL_AFTER then addSignalButton() end
  end
  Settings.RegisterAddOnCategory(category)
  O.category = category
end

-- Builds the panel once (after SavedVariables are loaded).
function O.Build()
  if O.category or O.failed then return O.category ~= nil end
  if not (Settings and Settings.RegisterVerticalLayoutCategory and Settings.RegisterAddOnSetting
          and Settings.RegisterAddOnCategory) then
    O.failed = "Settings API not available"
    return false
  end
  local ok, err = pcall(build)
  if not ok then
    O.failed = tostring(err)
    O.category = nil
    ns.Debug("options panel unavailable: %s", O.failed)
    return false
  end
  return true
end

function O.CategoryID()
  local c = O.category
  if not c then return nil end
  if c.GetID then
    local ok, id = pcall(c.GetID, c)
    if ok and id ~= nil then return id end
  end
  return c.ID
end

function ns.OpenOptions()
  O.Build()
  local id = O.CategoryID()
  if id ~= nil and Settings and Settings.OpenToCategory then
    local ok = pcall(Settings.OpenToCategory, id)
    if ok then return true end
  end
  ns.Print("the options panel is not available here; use slash commands instead:")
  ns.Print("  /needle settings - list all settings")
  ns.Print("  /needle set <setting> <value> - e.g. /needle set cooldownMin 15")
  return false
end

ns.On("ND_INIT", function()
  O.Build()
end)
