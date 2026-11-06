local ADDON, ns = ...
-- UI/MusicDialog.lua: the Music services dialog (NeedleDropperMusicDialog, DESIGN-v1.2.1 1.3), built
-- the first time it opens (ns.UI.OpenMusic: Settings rows 1 and 2, the drum in the window's title
-- bar, /needle music). A ButtonFrameTemplate dialog like Import and Share (W.DialogFrame): strata
-- DIALOG, in UISpecialFrames, hidden in combat and back afterwards, and part of forever's gamepad UI
-- (W.ManageInCombat); nothing in it is focused by the addon (W.FocusBox rule), so a controller can
-- reach every button and box and close it with B.
--
-- Two columns, so everything fits without scrolling:
--   left   A. Status: the four status lines (MusicServices.StatusLines: escaped there),
--             [Send a test signal] [Refresh status (reloads UI)]
--          B. How songs play: Play music with, YouTube box position (WowStyle1DropdownTemplate radios),
--             [x] Fade songs in and out, [x] Resume my Spotify music afterwards, the "choices" line
--          D. YouTube: [Sign in to YouTube...] [Test YouTube], a note, a feedback line
--   right  C. Spotify: the developer-app steps with two read-only boxes (the dashboard address and the
--             Redirect URI), the Client ID box and its validation line, [Connect Spotify | Sign in
--             again] [Disconnect...] [Test Spotify], notes, a feedback line
--
-- Read-only boxes (the Cell pattern of DESIGN-v1.2 5.6): an edit puts the value back, selected, so
-- Ctrl+C copies it (CopyToClipboard is protected). The Client ID box normalises what is pasted
-- (MusicServices.NormalizeClientId) and stores a valid ID on Enter, on focus loss and on Connect;
-- emptying it forgets the stored one. Only what the player typed or pasted is stored, never the
-- prefill: a stored copy of the app's own ID would later shadow the one the Connect page owns
-- (review INGAME-1).

local UI = ns.UI
local W = UI._W
local L = W.L

local M = { frame = nil, choiceShown = false, edited = false }
W.Music = M

local DIALOG_W, DIALOG_H = 840, 590
local COL_W = 392
local GAP = 8
local ELLIPSIS = "\226\128\166"

local function MS() return ns.MusicServices end

-- ------------------------------------------------------------- widgets --

local function header(parent, text)
  return W.Label(parent, text, "GameFontNormal")
end

-- A wrapping line of the column's width below `anchor`.
local function line(parent, anchor, font, gap)
  local fs = W.Label(parent, nil, font or "GameFontHighlightSmall")
  fs:SetPoint("TOPLEFT", anchor, "BOTTOMLEFT", 0, -(gap or 4))
  fs:SetPoint("RIGHT", parent, "RIGHT", 0, 0)
  fs:SetWordWrap(true)
  return fs
end

-- A read-only box: it always shows `value`; an edit puts it back, selected again.
local function readOnlyBox(parent, value)
  local box = W.Input(parent, nil)
  box.ndValue = value or ""
  box:SetText(box.ndValue)
  box:HookScript("OnTextChanged", function(self, userInput)
    if userInput then
      self:SetText(self.ndValue)
      self:HighlightText()
    end
  end)
  box:HookScript("OnEditFocusGained", function(self) self:HighlightText() end)
  box:SetScript("OnEnterPressed", function(self) self:ClearFocus() end)
  box:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
  W.SetTooltip(box, W.SimpleTooltip(L["Copy"], L["Click the box, then press Ctrl+C to copy."]))
  return box
end

local function setReadOnly(box, value)
  box.ndValue = value or ""
  if box:GetText() ~= box.ndValue then box:SetText(box.ndValue) end
end

-- -------------------------------------------------------------- refresh --

local function appInfo()
  local m = MS()
  return m and m.App() or {}
end

-- The Client ID box's normalised text and whether it is one.
local function typedId()
  local f = M.frame
  local m = MS()
  if not (f and m) then return "", false end
  return m.NormalizeClientId(f.ClientId:GetText() or "")
end

-- Stores what the player typed in the Client ID box: a valid ID, or nothing for an emptied box. The
-- prefill (the stored ID, else the app's) is never stored.
function M.SaveClientId()
  local m = MS()
  if not (m and M.frame and M.edited) then return end
  local id, valid = typedId()
  if id == "" or valid then m.SetClientId(id) end
end

function M.ValidationText()
  local m = MS()
  local id, valid = typedId()
  if id == "" or not m then return "" end
  if not valid then
    return L["A Client ID is 32 characters: digits and the letters a-f. Copy the Client ID from your app's Settings page, not the Client secret."], false
  end
  local app = m.App()
  if app.state == "connected" and app.clientId and app.clientId ~= id then
    return W.Format(L["The app is connected with another Client ID (%s). Connect switches to this one after you approve it."],
      m.ShortId(app.clientId)), true
  end
  return W.CHECK .. " " .. L["That looks like a Client ID."], true
end

-- Why `action`'s button is disabled now, or nil.
local function blocked(action)
  local m = MS()
  if not m then return L["Music services are not available."] end
  local why = m.SendBlocker()
  if why then return why end
  if m.Cooling(action) then return L["Just sent: give it a moment."] end
  return nil
end

function M.RefreshButtons()
  local f = M.frame
  local m = MS()
  if not (f and m) then return end
  local app = m.App()
  local state = app.state
  -- Connect Spotify / Sign in again
  local again = state == "connected" or state == "error" or state == "needs-relogin"
  f.ConnectButton:SetText(again and L["Sign in again"] or L["Connect Spotify"])
  local id, valid = typedId()
  local why = blocked("connect-spotify")
  if not why and id ~= "" and not valid then why = L["The Client ID box doesn't hold a Client ID (step 3)."] end
  if not why and id == "" and not m.KnownClientId() then why = L["Paste your Client ID first (step 3)."] end
  W.SetEnabled(f.ConnectButton, why == nil, why)
  -- Disconnect...
  why = blocked("disconnect-spotify")
  if not why then
    local connected = state == "connected" or state == "error" or state == "premium-required" or state == "needs-relogin"
      or state == "no-player" or state == "stuck" or m.session.connectAt ~= nil
    if not connected then
      why = L["Spotify isn't connected."]
    elseif not (app.clientId or (m.session.connectAt and m.StoredClientId())) then
      why = L["Disconnect it on the app's Connect page."]
    end
  end
  W.SetEnabled(f.DisconnectButton, why == nil, why)
  W.SetEnabled(f.TestSpotifyButton, blocked("test-spotify") == nil, blocked("test-spotify"))
  W.SetEnabled(f.SignInButton, blocked("youtube-sign-in") == nil, blocked("youtube-sign-in"))
  W.SetEnabled(f.TestYouTubeButton, blocked("test-youtube") == nil, blocked("test-youtube"))
end

function M.RefreshValidation()
  local f = M.frame
  if not f then return end
  local text, ok = M.ValidationText()
  f.Validation:SetFontObject(ok == false and "GameFontRedSmall" or "GameFontHighlightSmall")
  f.Validation:SetText(text or "")
end

function M.RefreshStatus()
  local f = M.frame
  local m = MS()
  if not (f and m) then return end
  local lines = m.StatusLines()
  for i = 1, 4 do f.StatusLines[i]:SetText(lines[i] or "") end
end

function M.RefreshChoices()
  local f = M.frame
  if not f then return end
  local s = ns.settings or {}
  f.Fade:SetChecked(s.musicFade ~= false)
  f.Resume:SetChecked(s.resumeMyMusic ~= false)
  if f.Order.GenerateMenu then f.Order:GenerateMenu() end
  if f.Corner.GenerateMenu then f.Corner:GenerateMenu() end
  local m = MS()
  f.Choices:SetText((M.choiceShown and m) and m.ChoicesCopy() or "")
end

function M.Refresh()
  local f = M.frame
  if not f then return end
  local m = MS()
  local app = appInfo()
  setReadOnly(f.RedirectBox, app.redirectUri or (m and m.REDIRECT_URI) or "")
  if not f.ClientId:HasFocus() then
    local prefill = m and (m.StoredClientId() or m.AppClientId()) or ""
    if f.ClientId:GetText() ~= prefill then f.ClientId:SetText(prefill) end
    M.edited = false
  end
  M.RefreshStatus()
  M.RefreshChoices()
  M.RefreshValidation()
  M.RefreshButtons()
  W.GamepadRefresh(f)
end

-- ---------------------------------------------------------------- clicks --

function M.Connect()
  local m = MS()
  if not m then return end
  local id, valid = typedId()
  local opts = {}
  if valid and M.edited then
    m.SetClientId(id)
    opts.clientId = id
  end
  m.Send("connect-spotify", opts)
end

local function feedback(action, copy)
  local f = M.frame
  if not f then return end
  local text = copy .. "\n" .. L[MS().NO_ANSWER]
  if action == "test-youtube" or action == "youtube-sign-in" then
    f.YouTubeFeedback:SetText(text)
  else
    f.SpotifyFeedback:SetText(text)
  end
end

-- --------------------------------------------------------------- build --

local function dropdown(parent, key)
  local dd = CreateFrame("DropdownButton", nil, parent, "WowStyle1DropdownTemplate")
  dd:SetWidth(240)
  local meta = ns.SETTINGS_BY_KEY and ns.SETTINGS_BY_KEY[key]
  dd:SetupMenu(function(owner, root)
    local function isSel(value) return ns.settings and ns.settings[key] == value end
    local function setSel(value)
      ns.SetSetting(key, value)
    end
    for _, opt in ipairs(meta and meta.options or {}) do
      root:CreateRadio(L[opt[2]], isSel, setSel, opt[1])
    end
  end)
  return dd
end

local function build()
  if M.frame then return M.frame end
  local f = W.DialogFrame("NeedleDropperMusicDialog", L["Music services"], DIALOG_W, DIALOG_H)
  M.frame = f

  local left = CreateFrame("Frame", nil, f)
  left:SetPoint("TOPLEFT", f, "TOPLEFT", 16, -32)
  left:SetSize(COL_W, DIALOG_H - 64)
  local right = CreateFrame("Frame", nil, f)
  right:SetPoint("TOPRIGHT", f, "TOPRIGHT", -16, -32)
  right:SetSize(COL_W, DIALOG_H - 64)
  f.Left, f.Right = left, right

  -- A. Status
  f.StatusHeader = header(left, L["Status"])
  f.StatusHeader:SetPoint("TOPLEFT", left, "TOPLEFT", 0, 0)
  f.StatusLines = {}
  local anchor = f.StatusHeader
  for i = 1, 4 do
    f.StatusLines[i] = line(left, anchor, "GameFontHighlightSmall", i == 1 and 6 or 3)
    anchor = f.StatusLines[i]
  end
  f.SignalButton = W.Button(left, L["Send a test signal"], 150, function()
    if ns.SelfTest and ns.SelfTest.Signal then ns.SelfTest.Signal() end
  end, L["Send a test signal"], L["Sends a test signal through the beacon (and the chat log when it is on). Your NeedleDropper app shows a notification within 2 seconds if it can see the game."])
  f.SignalButton:SetPoint("TOPLEFT", anchor, "BOTTOMLEFT", 0, -GAP)
  f.RefreshButton = W.Button(left, L["Refresh status (reloads UI)"], 210, function() pcall(ReloadUI) end,
    L["Refresh status (reloads UI)"],
    L["The game reads the NeedleDropper app's report only when the interface loads. Reload now to see the latest status here. Your soundtrack edits are saved first."])
  f.RefreshButton:SetPoint("LEFT", f.SignalButton, "RIGHT", 6, 0)

  -- B. How songs play
  f.PlayHeader = header(left, L["How songs play"])
  f.PlayHeader:SetPoint("TOPLEFT", f.SignalButton, "BOTTOMLEFT", 0, -14)
  f.OrderLabel = W.Label(left, L["Play music with:"], "GameFontHighlightSmall")
  f.OrderLabel:SetPoint("TOPLEFT", f.PlayHeader, "BOTTOMLEFT", 0, -10)
  f.Order = dropdown(left, "musicOrder")
  f.Order:SetPoint("TOPLEFT", f.OrderLabel, "TOPLEFT", 140, 6)
  f.CornerLabel = W.Label(left, L["YouTube box position:"], "GameFontHighlightSmall")
  f.CornerLabel:SetPoint("TOPLEFT", f.OrderLabel, "BOTTOMLEFT", 0, -18)
  f.Corner = dropdown(left, "youtubeCorner")
  f.Corner:SetPoint("TOPLEFT", f.CornerLabel, "TOPLEFT", 140, 6)
  f.Fade = W.Check(left, L["Fade songs in and out"], function(self)
    ns.SetSetting("musicFade", self:GetChecked() and true or false)
  end)
  f.Fade:SetPoint("TOPLEFT", f.CornerLabel, "BOTTOMLEFT", -4, -12)
  W.SetTooltip(f.Fade, W.SimpleTooltip(L["Fade songs in and out"], L["Off: songs start and stop at once, like a real needle drop."]))
  f.Resume = W.Check(left, L["Resume my Spotify music afterwards"], function(self)
    ns.SetSetting("resumeMyMusic", self:GetChecked() and true or false)
  end)
  f.Resume:SetPoint("TOPLEFT", f.Fade, "BOTTOMLEFT", 0, -2)
  W.SetTooltip(f.Resume, W.SimpleTooltip(L["Resume my Spotify music afterwards"],
    L["When a Spotify needle drop ends, Spotify goes back to what you were listening to."]))
  f.Choices = W.Label(left, nil, "GameFontHighlightSmall")
  f.Choices:SetPoint("TOPLEFT", f.Resume, "BOTTOMLEFT", 4, -4)
  f.Choices:SetPoint("RIGHT", left, "RIGHT", 0, 0)
  f.Choices:SetWordWrap(true)

  -- D. YouTube
  f.YouTubeHeader = header(left, L["YouTube"])
  f.YouTubeHeader:SetPoint("TOPLEFT", f.Choices, "BOTTOMLEFT", 0, -14)
  f.YouTubeIntro = line(left, f.YouTubeHeader, "GameFontHighlightSmall", 6)
  f.YouTubeIntro:SetText(L["Nothing to set up. While a YouTube song plays, a small player box appears in the corner you chose (YouTube requires it to be visible)."])
  f.SignInButton = W.Button(left, L["Sign in to YouTube" .. ELLIPSIS], 170, function()
    local m = MS()
    if m then m.Confirm("NEEDLEDROPPER_YOUTUBE_SIGNIN") end
  end, L["Sign in to YouTube" .. ELLIPSIS],
    L["Optional. The NeedleDropper app opens YouTube's sign-in page in its player box's browser. Signed in with YouTube Premium, YouTube songs play without ads."])
  f.SignInButton:SetPoint("TOPLEFT", f.YouTubeIntro, "BOTTOMLEFT", 0, -GAP)
  f.TestYouTubeButton = W.Button(left, L["Test YouTube"], 120, function()
    local m = MS()
    if m then m.Send("test-youtube") end
  end, L["Test YouTube"], L["The NeedleDropper app opens the YouTube box in your corner and plays 15 seconds of Toto \226\128\147 Africa."])
  f.TestYouTubeButton:SetPoint("LEFT", f.SignInButton, "RIGHT", 6, 0)
  f.YouTubeNote = line(left, f.SignInButton, "GameFontNormalSmall", GAP)
  f.YouTubeNote:SetText(L["Optional: sign in with YouTube Premium to play without ads. The page opens in the player box's own browser, not your everyday one."])
  f.YouTubeFeedback = line(left, f.YouTubeNote, "GameFontHighlightSmall", 6)

  -- C. Spotify
  f.SpotifyHeader = header(right, L["Spotify"])
  f.SpotifyHeader:SetPoint("TOPLEFT", right, "TOPLEFT", 0, 0)
  f.SpotifyIntro = line(right, f.SpotifyHeader, "GameFontHighlightSmall", 6)
  f.SpotifyIntro:SetText(L["Spotify needs a free developer app of your own (about 5 minutes, once). The game can't open web pages, so copy these addresses into your browser."])
  f.Step1 = line(right, f.SpotifyIntro, "GameFontNormalSmall", GAP)
  f.Step1:SetText(L["1. Open the Spotify developer dashboard and log in with the Spotify account you listen with:"])
  f.DashboardBox = readOnlyBox(right, MS() and MS().DASHBOARD_URL or "")
  f.DashboardBox:SetPoint("TOPLEFT", f.Step1, "BOTTOMLEFT", 6, -4)
  f.DashboardBox:SetWidth(COL_W - 12)
  f.Step2 = line(right, f.DashboardBox, "GameFontNormalSmall", GAP)
  f.Step2:SetPoint("TOPLEFT", f.DashboardBox, "BOTTOMLEFT", -6, -GAP)
  f.Step2:SetText(L["2. Click Create app (any name, e.g. NeedleDropper), tick Web API and Web Playback SDK, add this exact Redirect URI and press Add, then Save. (An app from NeedleDropper 1.2.0: open it, Settings, Edit, tick Web Playback SDK, Save.)"])
  f.RedirectBox = readOnlyBox(right, MS() and MS().REDIRECT_URI or "")
  f.RedirectBox:SetPoint("TOPLEFT", f.Step2, "BOTTOMLEFT", 6, -4)
  f.RedirectBox:SetWidth(COL_W - 12)
  f.Step3 = line(right, f.RedirectBox, "GameFontNormalSmall", GAP)
  f.Step3:SetPoint("TOPLEFT", f.RedirectBox, "BOTTOMLEFT", -6, -GAP)
  f.Step3:SetText(L["3. Open your app's Settings, copy its Client ID (not the Client secret) and paste it here with Ctrl+V:"])
  f.ClientId = W.Input(right, L["Client ID: 32 characters"])
  f.ClientId:SetMaxLetters(80)
  f.ClientId:SetPoint("TOPLEFT", f.Step3, "BOTTOMLEFT", 6, -4)
  f.ClientId:SetWidth(COL_W - 12)
  f.ClientId:HookScript("OnTextChanged", function(self, userInput)
    if userInput then
      M.edited = true
      M.RefreshValidation()
      M.RefreshButtons()
    end
  end)
  f.ClientId:SetScript("OnEnterPressed", function(self)
    M.SaveClientId()
    self:ClearFocus()
  end)
  f.ClientId:HookScript("OnEditFocusLost", function()
    M.SaveClientId()
    M.RefreshButtons()
  end)
  W.SetTooltip(f.ClientId, W.SimpleTooltip(L["Client ID"],
    L["Paste the Client ID from your Spotify app's Settings page (Ctrl+V). It is not a secret; the Client secret is, so never paste that."]))
  f.Validation = line(right, f.ClientId, "GameFontHighlightSmall", 4)
  f.Validation:SetPoint("TOPLEFT", f.ClientId, "BOTTOMLEFT", -6, -4)
  f.ConnectButton = W.Button(right, L["Connect Spotify"], 130, function() M.Connect() end, L["Connect Spotify"],
    L["The NeedleDropper app opens Spotify's sign-in page in your web browser. Approve it there."])
  f.ConnectButton:SetPoint("TOPLEFT", f.Validation, "BOTTOMLEFT", 0, -GAP)
  f.DisconnectButton = W.Button(right, L["Disconnect" .. ELLIPSIS], 110, function()
    local m = MS()
    if m then m.Confirm("NEEDLEDROPPER_SPOTIFY_DISCONNECT") end
  end, L["Disconnect" .. ELLIPSIS], L["The NeedleDropper app forgets your Spotify sign-in. Your Client ID stays."])
  f.DisconnectButton:SetPoint("LEFT", f.ConnectButton, "RIGHT", 6, 0)
  f.TestSpotifyButton = W.Button(right, L["Test Spotify"], 120, function()
    local m = MS()
    if m then m.Send("test-spotify") end
  end, L["Test Spotify"], L["The NeedleDropper app plays 15 seconds of Toto \226\128\147 Africa in its own Spotify player (no Spotify app needed), or a Windows notification says why it can't."])
  f.TestSpotifyButton:SetPoint("LEFT", f.DisconnectButton, "RIGHT", 6, 0)
  f.SpotifyNote = line(right, f.ConnectButton, "GameFontNormalSmall", GAP)
  f.SpotifyNote:SetText(L["Playing Spotify songs needs Spotify Premium and Google Chrome (NeedleDropper plays them in its own hidden player). A development app works for its owner and up to 5 accounts added under User Management."])
  f.KeyboardNote = line(right, f.SpotifyNote, "GameFontNormalSmall", 4)
  f.KeyboardNote:SetText(L["No keyboard? Do these steps on the app's Connect page instead: tray icon \226\134\146 Open setup" .. ELLIPSIS])
  f.SpotifyFeedback = line(right, f.KeyboardNote, "GameFontHighlightSmall", 6)

  f.CloseButton2 = W.Button(f, CLOSE or L["Close"], 84, function() f:Hide() end, CLOSE or L["Close"])
  f.CloseButton2:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -8, 3)

  f:HookScript("OnShow", function() M.Refresh() end)
  f:HookScript("OnHide", function()
    M.SaveClientId()
    GameTooltip:Hide()
  end)
  return f
end

-- --------------------------------------------------------------- public --

function W.OpenMusic()
  if not (ns.db and MS()) then
    if ns.Print then ns.Print(L["NeedleDropper is still loading - try again in a moment."]) end
    return false
  end
  local f = build()
  if f:IsShown() then
    M.Refresh()
    f:Raise()
  elseif not W.InCombat() then
    f:Show()
    f:Raise()
  else
    W.OpenAfterCombat(f)          -- dialogs stay hidden in combat; this one opens when it ends
  end
  return true
end

local function shownFrame()
  local f = M.frame
  if f and f:IsShown() then return f end
  return nil
end

ns.On("ND_MUSIC", function(_, what, action, at, copy)
  if not M.frame then return end
  if what == "sent" and type(copy) == "string" then feedback(action, copy) end
  if shownFrame() then
    M.RefreshStatus()
    M.RefreshButtons()
  end
end)

ns.On("ND_SETTING", function(_, key)
  local m = MS()
  if not (m and m.KEYS[key]) then return end
  M.choiceShown = true
  if shownFrame() then
    M.RefreshChoices()
    M.RefreshStatus()
  end
end)

ns.On("ND_COMPANION_DATA", function()
  if shownFrame() then M.Refresh() end
end)
