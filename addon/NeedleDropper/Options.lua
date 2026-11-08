local ADDON, ns = ...
-- Options: a Settings API panel (Game Menu > Options > AddOns > NeedleDropper) for every
-- setting in ns.SETTINGS except the advanced ones (beaconCorner: /needle set only), grouped
-- under section headers (DESIGN-v1.2 4.7):
--   * an "Open NeedleDropper" row at the top (the window is where soundtracks are edited);
--   * a "Preview" button for the title card (its size / position sliders re-lay out a card
--     that is showing, so the effect is visible while dragging);
--   * "Send a test signal" under Companion link (PING + TEST: the app shows a notification);
--   * the Music services section (DESIGN-v1.2.1 1.2): rows 1-3 (the app's status, the Spotify
--     account, the YouTube sign-in), the five music settings, rows 9-11 (Test Spotify, Test
--     YouTube, Refresh status). Button texts and tooltips are functions (forever's
--     SettingsButtonControlMixin:EvaluateName and Settings.InitTooltip call them each time the row
--     is shown), each row adds its own search tags, and the rows that send a request to the app
--     are disabled (AddModifyPredicate) while the app can't take one.
-- Every Settings call is guarded; if the API is missing or changed, the addon stays fully
-- usable through /needle settings and /needle set, and a missing header or button never
-- costs a setting (without button initializers only the five music settings appear; /needle
-- music opens the Music services dialog).

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
  musicOrder = "Music services",
  combatFadeSec = "Playback",
  toast = "Title card",
  beacon = "Companion link",
  debug = "Troubleshooting",
}
local PREVIEW_AFTER = "toastSeconds"   -- the title card's Preview button follows this setting
local SIGNAL_AFTER = "chatlogPadBytes" -- "Send a test signal" ends the Companion link section
local MUSIC_BEFORE = "musicOrder"      -- Music services rows 1-3 sit between the header and this setting
local MUSIC_AFTER = "resumeMyMusic"    -- rows 9-11 follow this one
-- Search tags of the five music settings (their labels are searched anyway).
local SETTING_TAGS = {
  musicOrder = { "Spotify", "YouTube" },
  youtubeCorner = { "YouTube", "corner", "player" },
  musicFade = { "fade" },
  musicVolume = { "volume", "Spotify", "YouTube" },
  resumeMyMusic = { "Spotify", "resume" },
}

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
  if meta.percentValue and type(v) == "number" then return string.format("%d%%", math.floor(v + 0.5)) end
  if type(v) ~= "number" then return tostring(v) end
  if meta.percent then return string.format("%d%%", math.floor(v * 100 + 0.5)) end
  if not meta.integer and (meta.step or 1) < 1 then return string.format("%.2f", v) end
  return tostring(math.floor(v + 0.5))
end

-- Adds search tags to an initializer that takes them (Blizzard_Settings.lua: strings only).
local function addTags(initializer, tags)
  if type(initializer) == "table" and type(initializer.AddSearchTags) == "function" and type(tags) == "table" then
    pcall(initializer.AddSearchTags, initializer, unpack(tags))
  end
end

local function addControl(category, meta)
  local default = ns.DEFAULTS[meta.key]
  local variable = "NeedleDropper_" .. meta.key
  local setting = Settings.RegisterAddOnSetting(category, variable, meta.key, ns.settings,
    settingVarType(meta, default), L[meta.label], default)
  if type(setting) ~= "table" then error("RegisterAddOnSetting returned nothing for " .. meta.key) end
  if setting.SetValueChangedCallback then
    setting:SetValueChangedCallback(valueChanged(meta.key))
  elseif Settings.SetOnValueChangedCallback then
    Settings.SetOnValueChangedCallback(variable, valueChanged(meta.key))
  end
  local initializer
  if meta.type == "boolean" then
    initializer = Settings.CreateCheckbox(category, setting, L[meta.tip])
  elseif meta.type == "number" then
    local options = Settings.CreateSliderOptions(meta.min, meta.max, meta.step or 1)
    if options and options.SetLabelFormatter and MinimalSliderWithSteppersMixin
       and MinimalSliderWithSteppersMixin.Label then
      options:SetLabelFormatter(MinimalSliderWithSteppersMixin.Label.Right, function(v)
        return O.FormatSliderValue(meta, v)
      end)
    end
    initializer = Settings.CreateSlider(category, setting, options, L[meta.tip])
  elseif meta.options then
    local function getOptions()
      local container = Settings.CreateControlTextContainer()
      for i = 1, #meta.options do container:Add(meta.options[i][1], L[meta.options[i][2]]) end
      return container:GetData()
    end
    initializer = Settings.CreateDropdown(category, setting, getOptions, L[meta.tip])
  end
  addTags(initializer, SETTING_TAGS[meta.key])
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

-- A button row. `text` and `tip` may be functions (evaluated each time the row is shown); `tags`
-- are extra search strings; `predicate` (a function returning a plain boolean) disables the button
-- while it returns false.
local function addButton(what, name, text, onClick, tip, tags, predicate)
  if type(CreateSettingsButtonInitializer) ~= "function" then return false end
  return addExtra(what, function()
    local initializer = CreateSettingsButtonInitializer(name, text, onClick, tip, true)
    addTags(initializer, tags)
    if predicate and type(initializer) == "table" and type(initializer.AddModifyPredicate) == "function" then
      pcall(initializer.AddModifyPredicate, initializer, predicate)
    end
    return initializer
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
    L["Checks only the link and plays no music. Your NeedleDropper app shows a notification within 2 seconds if it can see the game."])
end

-- ------------------------------------------------------ Music services rows --
-- DESIGN-v1.2.1 1.2. Every text comes from ns.MusicServices at the time the row is shown; the
-- status lines are escaped there.

local DOT = " \194\183 "

local function music()
  local MS = ns.MusicServices
  if type(MS) == "table" then return MS end
  return nil
end

-- Calls MusicServices.<name>(...), or returns `fallback` when it is missing or fails.
local function ms(name, fallback, ...)
  local MS = music()
  if not (MS and type(MS[name]) == "function") then
    if fallback == nil then ns.Print(L["Music services could not open. Type /reload and try again."]) end
    return fallback
  end
  local ok, v = pcall(MS[name], ...)
  if not ok then ns.Print(L["Music services could not complete this request. Type /reload and try again."]) end
  if ok and v ~= nil then return v end
  return fallback
end

-- "<text>" plus, when the app can't take requests now, why not (the disabled rows' tooltips).
local function withReason(text)
  local why = ms("SendBlocker", nil)
  if type(why) == "string" and why ~= "" then return text .. "\n\n" .. why end
  return text
end

function O.MusicStatusText() return ms("StatusWord", L["Music services"]) end
function O.SpotifyText() return ms("SpotifyButtonText", L["Set up Spotify\226\128\166"]) end
function O.CanSendActions() return ms("CanSendActions", false) == true end

function O.MusicClicked() ms("OpenDialog") end
function O.SpotifyClicked() ms("SpotifyButtonClicked") end
function O.YouTubeSignInClicked() ms("Confirm", nil, "NEEDLEDROPPER_YOUTUBE_SIGNIN") end
function O.TestSpotifyClicked() ms("Send", nil, "test-spotify") end
function O.TestYouTubeClicked() ms("Send", nil, "test-youtube") end
-- DESIGN-v1.2 4.9: ReloadUI only from a click, inside pcall.
function O.RefreshClicked() pcall(ReloadUI) end

function O.MusicStatusTip()
  local lines = {}
  for _, line in ipairs(ms("StatusLines", {})) do
    if line ~= "" then lines[#lines + 1] = line end
  end
  lines[#lines + 1] = L["Click to open Music services."]
  return table.concat(lines, "\n")
end

function O.SpotifyTip()
  local MS = music()
  if not MS then return L["Music services are not available."] end
  local app = MS.App()
  local _, kind = MS.SpotifyButton()
  if app.state == "connected" then
    local text = string.format(L["Connected as %s"], app.account and ns.EscapeText(app.account) or L["your account"])
    if app.product then text = text .. string.format(L[" (%s)"], app.product == "premium" and L["Premium"] or ns.EscapeText(app.product)) end
    local id = app.clientId or MS.StoredClientId()
    if id then text = text .. DOT .. string.format(L["Client ID %s"], MS.ShortId(id)) end
    return text .. ". " .. L["Click for Disconnect and Test."]
  end
  if kind == "connect" then
    return L["The NeedleDropper app opens Spotify's sign-in page in your web browser. Approve it there."]
  end
  if not MS.KnownClientId() then
    return withReason(L["Create your own free Spotify developer app and paste its Client ID: about 5 minutes, once."])
  end
  return MS.SpotifyLine(app) .. "\n" .. L["Click to open Music services."]
end

function O.YouTubeTip()
  return withReason(L["Optional. The NeedleDropper app opens YouTube's sign-in page in its player box's browser. Signed in with YouTube Premium, YouTube songs play without ads."])
end

function O.TestSpotifyTip()
  local app = ms("App", {})
  if app.mode == "connect" then
    return withReason(L["The NeedleDropper app plays 15 seconds of Toto \226\128\147 Africa on Spotify, or a Windows notification says why it can't."])
  end
  return withReason(L["The NeedleDropper app plays 15 seconds of Toto \226\128\147 Africa in its own Spotify player (no Spotify app needed), or a Windows notification says why it can't."])
end

function O.TestYouTubeTip()
  return withReason(L["The NeedleDropper app opens the YouTube box in your corner and plays 15 seconds of Toto \226\128\147 Africa."])
end

-- Rows 1-3: between the section header and "Play music with".
local function addMusicHead()
  addButton("music status", L["NeedleDropper app"], O.MusicStatusText, O.MusicClicked, O.MusicStatusTip,
    { "app", "companion", "status", "music" })
  addButton("spotify account", L["Spotify account"], O.SpotifyText, O.SpotifyClicked, O.SpotifyTip,
    { "Spotify", "account", "Client ID", "connect", "sign in", "Premium" })
  addButton("youtube account", L["YouTube account"], L["Sign in to YouTube\226\128\166"], O.YouTubeSignInClicked,
    O.YouTubeTip, { "YouTube", "sign in", "Premium", "ads" }, O.CanSendActions)
end

-- Rows 8-10: after "Resume my Spotify music afterwards".
local function addMusicTail()
  addButton("test spotify", L["Test Spotify"], L["Test Spotify"], O.TestSpotifyClicked, O.TestSpotifyTip,
    { "test", "Spotify" }, O.CanSendActions)
  addButton("test youtube", L["Test YouTube"], L["Test YouTube"], O.TestYouTubeClicked, O.TestYouTubeTip,
    { "test", "YouTube" }, O.CanSendActions)
  addButton("refresh status", L["Refresh status"], L["Reload UI"], O.RefreshClicked,
    L["The game reads the NeedleDropper app's report only when the interface loads. Reload now to see the latest status here. Your soundtrack edits are saved first."],
    { "reload", "refresh", "status" })
end

local function build()
  local category, layout = Settings.RegisterVerticalLayoutCategory("NeedleDropper")
  if type(category) ~= "table" then error("RegisterVerticalLayoutCategory returned nothing") end
  O.layout = layout
  addOpenButton()
  for i = 1, #ns.SETTINGS do
    local meta = ns.SETTINGS[i]
    if not meta.advanced then
      if SECTIONS[meta.key] then addHeader(L[SECTIONS[meta.key]]) end
      if meta.key == MUSIC_BEFORE then addMusicHead() end
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
    if meta.key == MUSIC_AFTER then addMusicTail() end
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
