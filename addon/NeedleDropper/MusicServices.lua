local ADDON, ns = ...
--[[ MusicServices: the game's half of "Music services" (DESIGN-v1.2.1 sections 1, 2.7-2.10, 4).

What the game knows about the NeedleDropper app is what the app last wrote into
NeedleDropper_Companion (ns.Companion, read at login and /reload); what the game tells the app
travels in Protocol v2 frames:
  * the four music settings (musicOrder, youtubeCorner, musicFade, resumeMyMusic) as PREFS, in every
    HELLO (Emit.lua, and the beacon's own HELLOs through Beacon.SetPrefs) and every CONFIG, once the
    player has chosen one of them in game (NeedleDropperDB.music.prefsSet); until then the app keeps
    its own config, and at login the settings show the app's values (the echo);
  * the five requests (connect-spotify, disconnect-spotify, test-spotify, test-youtube,
    youtube-sign-in) as CONFIG frames with an ACTION (MS.Send). Every CONFIG is one strip row:
    connect and disconnect carry CLIENT_ID and never PREFS (35 bytes), the others PREFS and the
    ACTION (23 bytes); MS.Send refuses anything longer than Protocol2 Capacity(1) = 39 bytes.

"Play music with" (MS.Plan) feeds three decisions: the beacon fit rule's preferred id
(ns.PreferredProvider), the fields Emit leaves out of ARM / FIRE frames (MS.Strip: "Neither" never
plays, and the "only" modes are enforced for apps that can't read PREFS), and whether a drop fades the
zone music (Director.ShouldDuck: plan.canPlay).

Loaded right after Companion.lua; Emit, Beacon, Director, Options and the window reach it lazily and
must cope with it missing (the protocol test harness loads neither Companion nor this file).

Lines from StatusLines / AppLine / SpotifyLine / YouTubeLine / LastRequestLine are ready for a
FontString, a tooltip or chat: text from the app (account names, request results) is escaped here.

API
  MS.Prefs() / MS.WirePrefs() / MS.PrefsSet() / MS.Order()
  MS.NormalizeClientId(text) -> normalized, valid    MS.SetClientId(text) -> ok
  MS.StoredClientId() / MS.AppClientId() / MS.KnownClientId()
  MS.App() -> a plain summary of the app's report      MS.CanSendActions() -> bool   MS.SendBlocker() -> text | nil
  MS.Plan(drop) / MS.PreferredProvider() / MS.Strip(drop, flags, evType)
  MS.Send(action, opts) -> ev, copy | nil, reason      MS.SendPrefs()   MS.JustSent(action) -> seconds | nil
  MS.StatusWord() / MS.SpotifyButton() -> text, kind / MS.SpotifyButtonText() / MS.SpotifyButtonClicked()
  MS.StatusLines() -> { app, spotify, youtube, lastRequest }   MS.LastRequestLine()   MS.ChoicesCopy()
  MS.Confirm(popup) / MS.Slash(arg)
Internal message: ND_MUSIC ("sent", action, at, copy) after every request that left the game.
]]

local MS = {}
ns.MusicServices = MS

local L = ns.L or setmetatable({}, { __index = function(_, k) return k end })
local P2 = ns.Protocol2
local EV, FLAG = P2.EV, P2.FLAG
local floor = math.floor
local format, gsub, find, lower, match, sub = string.format, string.gsub, string.find, string.lower,
  string.match, string.sub

MS.ORDERS = { "spotify-youtube", "youtube-spotify", "spotify", "youtube", "none" }
MS.CORNERS = { "topright", "topleft", "bottomright", "bottomleft" }
MS.ACTIONS = ns.MUSIC_ACTIONS or { "connect-spotify", "disconnect-spotify", "test-spotify", "test-youtube",
                                   "youtube-sign-in" }
-- Setting key -> PREFS field.
MS.KEYS = { musicOrder = "order", youtubeCorner = "corner", musicFade = "fade", resumeMyMusic = "resume" }
-- Seconds: the same request is refused (and its button disabled) this long. The app's own limit
-- (music_config.ACTION_INTERVAL_S), so the game never announces a request the app turns away (INGAME-8).
MS.SEND_GAP = 10
MS.DEBOUNCE = 1.0          -- seconds after the last music setting change: the PREFS-only CONFIG
MS.CONNECT_WAIT = 300      -- seconds a connect may wait for the browser sign-in
MS.LATE_REPORT = 30        -- a report written this long after a request should already list it
MS.ONE_ROW = P2.Capacity(1)
MS.MIN_APP = { 1, 2, 1 }   -- requests and PREFS need this app version (Companion.Supports("config"))
MS.REDIRECT_URI = "http://127.0.0.1:8321/callback"
MS.DASHBOARD_URL = "https://developer.spotify.com/dashboard"

local ACTION = {}
for i = 1, #MS.ACTIONS do ACTION[MS.ACTIONS[i]] = true end
local ORDER, CORNER = {}, {}
for i = 1, #MS.ORDERS do ORDER[MS.ORDERS[i]] = true end
for i = 1, #MS.CORNERS do CORNER[MS.CORNERS[i]] = true end

MS.ACTION_LABELS = {
  ["connect-spotify"] = "Connect Spotify", ["disconnect-spotify"] = "Disconnect Spotify",
  ["test-spotify"] = "Test Spotify", ["test-youtube"] = "Test YouTube", ["youtube-sign-in"] = "Sign in to YouTube",
}
MS.CORNER_NAMES = { topright = "top right", topleft = "top left", bottomright = "bottom right",
                    bottomleft = "bottom left" }
MS.BROWSER_NAMES = { chrome = "Chrome", edge = "Edge" }

local DOT = " \194\183 "              -- " · "
local DASH = " \226\128\147 "          -- " – " (U+2013)
local ELLIPSIS = "\226\128\166"        -- "…"
local SONG = "Toto" .. DASH .. "Africa"

-- Session state (never saved): when each request was last sent (GetTime), the connect of this
-- session (time()), and the debounce generation of the PREFS-only CONFIG.
local session = { sentAt = {}, connectAt = nil, debounce = 0, typedClientId = false }
MS.session = session
local popupsReady = false

-- ------------------------------------------------------------------ helpers --

local function esc(s)
  if ns.EscapeText then return ns.EscapeText(s) end
  if type(s) ~= "string" then return "" end
  return (gsub(s, "|", "||"))
end

local function nonEmpty(v)
  if type(v) == "string" and v ~= "" then return v end
  return nil
end

local function trim(s)
  if type(s) ~= "string" then return "" end
  return (gsub(gsub(s, "^[ \t\r\n]+", ""), "[ \t\r\n]+$", ""))
end

local function now() return GetTime() end
local function wall() return time() end

-- date() for a time stamp, or "?": it gives nil (or may raise) for a time it can't show -- a corrupt
-- report (review INGAME-7; Companion's checker leaves out times after 2100 anyway).
local function safeDate(fmt, t)
  local ok, s = pcall(date, fmt, t)
  if ok and type(s) == "string" then return s end
  return "?"
end

local function hm(t)
  return safeDate("%H:%M", t)
end

local function companion()
  local C = ns.Companion
  if type(C) == "table" then return C end
  return nil
end

local function call(t, name, ...)
  if type(t) ~= "table" or type(t[name]) ~= "function" then return nil end
  local ok, v = pcall(t[name], ...)
  if ok then return v end
  return nil
end

-- major.minor.patch comparison (Companion.AtLeast when it is there; the same rule otherwise).
local function atLeast(version, a, b, c)
  local C = companion()
  if C and type(C.AtLeast) == "function" then return call(C, "AtLeast", version, a, b, c) == true end
  if type(version) ~= "string" then return false end
  local x, y, z = match(version, "^([0-9]+)%.([0-9]+)%.?([0-9]*)")
  x, y, z = tonumber(x), tonumber(y), tonumber(z) or 0
  if not x then return false end
  if x ~= a then return x > a end
  if y ~= b then return y > b end
  return z >= c
end

-- ------------------------------------------------------------- saved data --

-- NeedleDropperDB.music (Core.lua creates and validates it).
local function db()
  local d = ns.db
  if type(d) ~= "table" then return nil end
  if type(d.music) ~= "table" then d.music = { prefsSet = false, requests = {} } end
  local m = d.music
  if type(m.requests) ~= "table" then m.requests = {} end
  return m
end
MS.DB = db

function MS.PrefsSet()
  local m = db()
  return m ~= nil and m.prefsSet == true
end

-- Normalizes a pasted Client ID: ASCII spaces, tabs and line breaks removed, lowercased. Returns the
-- text and whether it is a Client ID (32 hex digits).
function MS.NormalizeClientId(text)
  if type(text) ~= "string" then return "", false end
  local s = lower((gsub(text, "[ \t\r\n]", "")))
  return s, (#s == 32 and find(s, "^[0-9a-f]+$") ~= nil)
end

-- The Client ID typed in game for the app to sign in with, or nil. The Connect page owns the ID once the
-- app reports one (DESIGN-v1.2.1 0): a stored copy is then used only when it was typed in this session
-- (the player means to switch); otherwise it is forgotten, so it never shadows a change made on the
-- Connect page (review INGAME-1).
function MS.StoredClientId()
  local m = db()
  local id = m and m.clientId or nil
  if id == nil then return nil end
  local appId = MS.AppClientId()
  if appId ~= nil and (id == appId or not session.typedClientId) then
    m.clientId = nil
    return nil
  end
  return id
end

-- Stores a valid Client ID (true), or forgets it for an empty text (true); false for anything else.
function MS.SetClientId(text)
  local m = db()
  if not m then return false end
  local id, valid = MS.NormalizeClientId(text or "")
  if id == "" then
    m.clientId = nil
    return true
  end
  if not valid then return false end
  m.clientId = id
  session.typedClientId = true
  return true
end

-- The Client ID the app signs in with (its report), or nil.
function MS.AppClientId()
  local music = call(companion(), "Music")
  local id = type(music) == "table" and type(music.spotify) == "table" and music.spotify.clientId or nil
  if type(id) == "string" and #id == 32 and find(id, "^[0-9a-f]+$") then return id end
  return nil
end

function MS.KnownClientId()
  return MS.StoredClientId() or MS.AppClientId()
end

-- "…cdef": the last four characters of a Client ID.
function MS.ShortId(id)
  if type(id) ~= "string" or #id < 4 then return "" end
  return ELLIPSIS .. sub(id, -4)
end

-- ----------------------------------------------------------------- prefs --

local function setting(key)
  local s = ns.settings
  if type(s) ~= "table" then return nil end
  return s[key]          -- (not "and s[key] or nil": that would turn false into nil)
end

function MS.Order()
  local v = setting("musicOrder")
  if ORDER[v] then return v end
  return "spotify-youtube"
end

function MS.Corner()
  local v = setting("youtubeCorner")
  if CORNER[v] then return v end
  return "topright"
end

-- The four music settings as a PREFS table.
function MS.Prefs()
  return { order = MS.Order(), corner = MS.Corner(), fade = setting("musicFade") ~= false,
           resume = setting("resumeMyMusic") ~= false }
end

-- PREFS for the wire: nil until the player has chosen a music setting in game (DESIGN-v1.2.1 1.4).
function MS.WirePrefs()
  if MS.PrefsSet() then return MS.Prefs() end
  return nil
end

-- Hands the current PREFS (or nil) to the beacon, whose HELLOs carry them.
function MS.PushPrefs()
  local B = ns.Beacon
  if type(B) == "table" and type(B.SetPrefs) == "function" then
    local ok, err = pcall(B.SetPrefs, MS.WirePrefs())
    if not ok then ns.Debug("music: SetPrefs failed: %s", tostring(err)) end
  end
end

-- The PREFS-only CONFIG (debounced): reaches the chat log and gives the change a fresh seq. Skipped
-- when the app's report says it is too old to read it.
function MS.SendPrefs()
  local prefs = MS.WirePrefs()
  if not prefs then return nil end
  local app = MS.App()
  if app.present and not app.supports then return nil end
  return ns.Emit(EV.CONFIG, nil, 0, { prefs = prefs })
end

function MS.SchedulePrefs()
  session.debounce = session.debounce + 1
  local mine = session.debounce
  if not (C_Timer and C_Timer.After) then return end
  C_Timer.After(MS.DEBOUNCE, function()
    if mine == session.debounce then MS.SendPrefs() end
  end)
end

-- A music setting changed in game (Settings, the dialog, /needle set) or the settings were reset:
-- from now on the game's choices travel to the app.
function MS.PrefsChanged()
  local m = db()
  if not m then return end
  m.prefsSet = true
  MS.PushPrefs()
  MS.SchedulePrefs()
end

-- At login, while prefsSet is false, the four settings show what the app actually does (its
-- music.prefs), assigned directly: no ND_SETTING, so this never marks them as set in game.
function MS.ApplyEcho()
  if MS.PrefsSet() or type(ns.settings) ~= "table" then return false end
  local music = call(companion(), "Music")
  local p = type(music) == "table" and music.prefs or nil
  if type(p) ~= "table" then return false end
  local changed = false
  for key, field in pairs(MS.KEYS) do
    local v = p[field]
    if v ~= nil then
      local ok = ns.CoerceSetting(key, v)
      if ok ~= nil and ns.settings[key] ~= ok then
        ns.settings[key] = ok
        changed = true
      end
    end
  end
  return changed
end

-- -------------------------------------------------------------- the app --

-- A plain summary of the app's report (all fields optional).
function MS.App()
  local C = companion()
  local st = call(C, "Status")
  if type(st) ~= "table" then st = {} end
  local music = call(C, "Music")
  if type(music) ~= "table" then music = nil end
  local sp = st.spotify
  if sp == true then sp = "connected" elseif type(sp) ~= "string" then sp = nil end
  local present = st.present == true
  local supports = false
  if present then
    if C and type(C.Supports) == "function" then
      supports = call(C, "Supports", "config") == true
    else
      supports = atLeast(st.appVersion, MS.MIN_APP[1], MS.MIN_APP[2], MS.MIN_APP[3])
    end
  end
  local msp = music and type(music.spotify) == "table" and music.spotify or {}
  local myt = music and type(music.youtube) == "table" and music.youtube or {}
  return {
    present = present, version = st.appVersion, written = tonumber(st.written) or 0, supports = supports,
    oldStyle = present and not atLeast(st.appVersion, 1, 2, 0),
    spotify = sp, state = msp.state or sp, mode = msp.mode, account = msp.account, product = msp.product,
    problem = msp.problem,
    clientId = MS.AppClientId(), redirectUri = msp.redirectUri,
    youtube = st.youtube == true, ytEnabled = myt.enabled, ytPlayer = myt.player, signInOpened = myt.signInOpened,
    prefs = music and music.prefs or nil, actions = (music and type(music.actions) == "table") and music.actions or {},
  }
end

function MS.SpotifyReady(app)
  app = app or MS.App()
  return app.spotify == "connected" and (app.product == nil or app.product == "premium")
end

function MS.YouTubeReady(app)
  app = app or MS.App()
  return app.youtube == true and app.ytEnabled ~= false and app.ytPlayer ~= "none"
end

-- Why the game can't send requests now (the §4.2 refusal text), or nil.
function MS.SendBlocker(app)
  app = app or MS.App()
  if not app.present then return L["Not sent: install the NeedleDropper app first (from the page you got NeedleDropper from)."] end
  if not app.supports then return L["Not sent: update the NeedleDropper app to 1.2.1 first."] end
  return nil
end

-- A plain boolean (a Settings modify predicate).
function MS.CanSendActions()
  return MS.SendBlocker() == nil
end

-- --------------------------------------------------------- what plays how --

local function hasSong(drop)
  return nonEmpty(drop.spotify) ~= nil or (nonEmpty(drop.artist) ~= nil and nonEmpty(drop.title) ~= nil)
end

-- The provider id the beacon's fit rule keeps last (DESIGN-v1.2.1 1.5 table 1).
function MS.PreferredProvider(app)
  local order = MS.Order()
  if order == "spotify" then return "spotify" end
  if order ~= "spotify-youtube" then return "youtube" end
  app = app or MS.App()
  return app.spotify == "connected" and "spotify" or "youtube"
end

-- What "Play music with" means for `drop` (DESIGN-v1.2.1 1.5): canPlay (something on this PC may
-- play it, so the zone music fades) and strip (the song fields Emit leaves out of ARM / FIRE).
function MS.Plan(drop)
  local d = type(drop) == "table" and drop or {}
  local order = MS.Order()
  local app = MS.App()
  local wantSp = order ~= "youtube" and order ~= "none"
  local wantYt = order ~= "spotify" and order ~= "none"
  local hasSp = hasSong(d)
  local hasYt = nonEmpty(d.youtube) ~= nil
  local spReady = not app.present or app.spotify == "connected" or session.connectAt ~= nil
  -- YouTube needs the app's player browser too (music.youtube.player "none": no Edge or Chrome), and not
  -- switched off on the Connect page (review INGAME-6)
  local ytReady = not app.present or MS.YouTubeReady(app)
  local canPlay = (wantSp and hasSp and spReady) or (wantYt and hasYt and ytReady)
  local strip = {}
  if order == "none" then
    strip.spotify, strip.youtube, strip.query = true, true, true
  elseif not app.supports then
    -- An app that can't read PREFS plays Spotify first, then YouTube: enforce "only" here.
    if order == "spotify" then strip.youtube = true end
    if order == "youtube" then strip.spotify, strip.query = true, true end
  end
  return { order = order, wantSpotify = wantSp, wantYouTube = wantYt, hasSpotify = hasSp, hasYouTube = hasYt,
           spotifyReady = spReady, youtubeReady = ytReady, canPlay = canPlay and true or false,
           preferred = MS.PreferredProvider(app), strip = strip }
end

-- The fields to leave out of an ARM / FIRE of `drop` with live flags `flags`, or nil. Never for the
-- test drop, nor for a drop the game plays itself: a FIRE with LOCAL_PLAYBACK, or the ARM of a drop
-- with an in-game file while localPlayback is on (ARM never carries that flag) -- except under
-- "Neither": the in-game file may not play at FIRE (WoW's music off, a missing file), and the app merges
-- the cached ARM into the stripped FIRE, so that ARM is stripped too (review INGAME-5; a FIRE with
-- LOCAL_PLAYBACK carries its own fields for the card).
function MS.Strip(drop, flags, evType)
  if type(drop) ~= "table" or drop.id == "test" or drop.kind == "test" then return nil end
  if P2.HasFlag(flags, FLAG.LOCAL_PLAYBACK) then return nil end
  if evType == EV.ARM and drop.file ~= nil and setting("localPlayback") and MS.Order() ~= "none" then
    return nil
  end
  local strip = MS.Plan(drop).strip
  if next(strip) == nil then return nil end
  return strip
end

-- -------------------------------------------------------------- requests --

function MS.Requests()
  local m = db()
  return m and m.requests or {}
end

function MS.LastRequest()
  local list = MS.Requests()
  return list[#list]
end

local function record(action, at)
  local m = db()
  if not m then return end
  local list = m.requests
  list[#list + 1] = { action = action, at = at }
  while #list > (ns.MUSIC_REQUESTS_MAX or 8) do table.remove(list, 1) end
end

-- Seconds since `action` was last sent in this session, or nil.
function MS.JustSent(action)
  local t = session.sentAt[action]
  if not t then return nil end
  return now() - t
end

-- True while `action` was sent less than SEND_GAP seconds ago.
function MS.Cooling(action)
  local s = MS.JustSent(action)
  return s ~= nil and s < MS.SEND_GAP
end

-- A connect sent less than CONNECT_WAIT seconds ago that the app's report doesn't cover yet.
function MS.WaitingForBrowser(app)
  app = app or MS.App()
  local list = MS.Requests()
  for i = #list, 1, -1 do
    local r = list[i]
    if r.action == "connect-spotify" then
      return wall() - r.at < MS.CONNECT_WAIT and app.written < r.at
    end
  end
  return false
end

-- The §4.2 copy after a request was sent at `at`.
function MS.SentCopy(action, at, app)
  local t = hm(at)
  if action == "connect-spotify" then
    return format(L["Sent to the NeedleDropper app (%s). Approve NeedleDropper on Spotify's page in your browser (Alt+Tab if it opened behind the game). A Windows notification confirms it; then press Refresh status to see it here."], t)
  elseif action == "disconnect-spotify" then
    return format(L["Sent (%s). The NeedleDropper app forgets your Spotify sign-in and confirms with a Windows notification."], t)
  elseif action == "test-spotify" then
    app = app or MS.App()
    if app.mode == "connect" then
      return format(L["Sent (%s). You should hear 15 seconds of %s from Spotify in a few seconds. If not, a Windows notification says why."], t, SONG)
    end
    return format(L["Sent (%s). In a few seconds you should hear 15 seconds of %s from the NeedleDropper app's own Spotify player. If not, a Windows notification says why."], t, SONG)
  elseif action == "test-youtube" then
    return format(L["Sent (%s). The YouTube box should open in the %s corner and play 15 seconds of %s. If not, a Windows notification says why."],
      t, L[MS.CORNER_NAMES[MS.Corner()]], SONG)
  end
  return format(L["Sent (%s). The NeedleDropper app opens YouTube's sign-in page in its player browser (Alt+Tab if it stays behind the game)."], t)
end

MS.NO_ANSWER = "Nothing after 10 seconds? Check that the NeedleDropper app is running (its tray icon), then press Send a test signal."

local function refuse(text, opts)
  if not (opts and opts.quiet) then ns.Print("%s", text) end
  return nil, text
end

-- Sends request `action` (one of MS.ACTIONS) as a CONFIG frame. opts: { clientId = <normalized id>
-- (connect: the one typed, else the stored one), quiet = true (no chat line) }. Returns the event and
-- the §4.2 copy, or nil and the refusal text. Prints one chat line either way unless quiet.
function MS.Send(action, opts)
  opts = type(opts) == "table" and opts or {}
  if not ACTION[action] then return refuse(L["NeedleDropper doesn't know that request."], opts) end
  local app = MS.App()
  local blocked = MS.SendBlocker(app)
  if blocked then return refuse(blocked, opts) end
  if MS.Cooling(action) then return refuse(L["Just sent: give it a moment."], opts) end
  local clientId
  if action == "connect-spotify" then
    clientId = opts.clientId or MS.StoredClientId()
    if not clientId and not app.clientId then return refuse(L["Paste your Client ID first (step 3)."], opts) end
  elseif action == "disconnect-spotify" then
    clientId = app.clientId or MS.StoredClientId()
    if not clientId then return refuse(L["Disconnect it on the app's Connect page."], opts) end
  end
  local at = wall()
  local cfg = { action = { name = action, at = at } }
  -- A frame with a CLIENT_ID carries no PREFS: all three would be two strip rows (DESIGN-v1.2.1 2.2).
  if clientId then cfg.clientId = clientId else cfg.prefs = MS.WirePrefs() end
  local probe = { type = EV.CONFIG, seq = 255, flags = 255, prefs = cfg.prefs, action = cfg.action,
                  clientId = cfg.clientId }
  local size = #P2.EncodeFrame(probe)
  if size > MS.ONE_ROW then
    ns.Debug("music: %s would be a %d-byte CONFIG, more than one strip row; not sent", action, size)
    return refuse(L["Not sent: the request did not fit the beacon. Please report this with /needle report."], opts)
  end
  session.sentAt[action] = now()
  if action == "connect-spotify" then session.connectAt = at end
  record(action, at)
  local ev = ns.Emit(EV.CONFIG, nil, 0, cfg)
  local copy = MS.SentCopy(action, at, app)
  if not opts.quiet then ns.Print("%s %s", copy, L[MS.NO_ANSWER]) end
  ns.Fire("ND_MUSIC", "sent", action, at, copy)
  if C_Timer and C_Timer.After then
    -- Buttons come back once SEND_GAP is over.
    C_Timer.After(MS.SEND_GAP + 0.05, function() ns.Fire("ND_MUSIC", "ready", action) end)
  end
  return ev, copy
end

-- -------------------------------------------------------------- popups --

-- StaticPopupDialogs with the 12.x (dialog, data) handlers (DESIGN-v1.2 4.5).
function MS.EnsurePopups()
  if popupsReady then return true end
  if type(StaticPopupDialogs) ~= "table" then return false end
  popupsReady = true
  StaticPopupDialogs["NEEDLEDROPPER_SPOTIFY_DISCONNECT"] = {
    text = L["Disconnect Spotify? The NeedleDropper app forgets your Spotify sign-in. Your Client ID stays, so Connect signs you in again."],
    button1 = L["Disconnect"],
    button2 = CANCEL or L["Cancel"],
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    OnAccept = function(dialog, data) MS.Send("disconnect-spotify") end,
  }
  StaticPopupDialogs["NEEDLEDROPPER_YOUTUBE_SIGNIN"] = {
    text = L["Sign in to YouTube? The NeedleDropper app opens YouTube's sign-in page in its player box's own browser window. This is optional: with YouTube Premium, YouTube songs then play without ads."],
    button1 = L["Open sign-in page"],
    button2 = CANCEL or L["Cancel"],
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    OnAccept = function(dialog, data) MS.Send("youtube-sign-in") end,
  }
  return true
end

-- Shows popup "NEEDLEDROPPER_SPOTIFY_DISCONNECT" or "NEEDLEDROPPER_YOUTUBE_SIGNIN".
function MS.Confirm(which)
  if not MS.EnsurePopups() or type(StaticPopup_Show) ~= "function" then return nil end
  local dialog = StaticPopup_Show(which)
  if not dialog then ns.Print("%s", L["Close the other dialog first, then try again."]) end
  return dialog
end

-- ------------------------------------------------------------ status text --

-- Row 1 of the Settings section (24 characters at most).
function MS.StatusWord()
  local app = MS.App()
  if not app.present then return L["App not found"] end
  if not app.supports then return L["Update the app"] end
  if app.written <= 0 then return L["Waiting for the app"] end
  local sp, yt = MS.SpotifyReady(app), MS.YouTubeReady(app)
  if sp and yt then return L["Spotify + YouTube ready"] end
  if sp then return L["Spotify ready"] end
  if yt then return L["YouTube ready"] end
  return L["Nothing ready yet"]
end

-- Row 2: its text and what a click does ("open" the dialog or "connect").
function MS.SpotifyButton()
  local app = MS.App()
  local state = app.state
  if MS.WaitingForBrowser(app) then return L["Waiting for browser" .. ELLIPSIS], "open" end
  if state == "connected" and (app.product == nil or app.product == "premium") then return L["Manage" .. ELLIPSIS], "open" end
  if state == "premium-required" or (state == "connected" and app.product ~= nil) then
    return L["Needs Premium" .. ELLIPSIS], "open"
  end
  if not MS.CanSendActions() then return L["Set up Spotify" .. ELLIPSIS], "open" end
  -- D17: a missing Chrome or a start that didn't progress isn't fixed by signing in again. Only the app's
  -- "no-chrome" names Chrome (review ON-9): the other causes, and an app that names none, "Check Spotify…".
  if state == "no-player" then
    if app.problem == "no-chrome" then return L["Needs Chrome" .. ELLIPSIS], "open" end
    return L["Check Spotify" .. ELLIPSIS], "open"
  end
  if state == "stuck" then return L["Check Spotify" .. ELLIPSIS], "open" end
  -- Not on the developer app's User Management list (ON-3): signing in again does not fix that.
  if state == "error" and app.problem == "allowlist" then return L["Check Spotify" .. ELLIPSIS], "open" end
  if state == "needs-relogin" or state == "error" then return L["Sign in again"], "connect" end
  if MS.KnownClientId() then return L["Connect Spotify"], "connect" end
  return L["Set up Spotify" .. ELLIPSIS], "open"
end

function MS.SpotifyButtonText()
  return (MS.SpotifyButton())
end

function MS.OpenDialog()
  local U = ns.UI
  if type(U) == "table" and type(U.OpenMusic) == "function" and U.OpenMusic() then return true end
  for _, line in ipairs(MS.StatusLines()) do
    if line ~= "" then ns.Print("%s", line) end
  end
  return false
end

function MS.SpotifyButtonClicked()
  local _, kind = MS.SpotifyButton()
  if kind == "connect" then return MS.Send("connect-spotify") end
  return MS.OpenDialog()
end

local function ageText(age)
  if age < 60 then return L["just before login"] end
  if age < 3600 then return format(L["%d min before login"], floor(age / 60)) end
  if age < 172800 then return format(L["%d h before login"], floor(age / 3600)) end
  return format(L["%d days before login"], floor(age / 86400))
end

-- Line 1: the app (DESIGN-v1.2.1 1.1).
function MS.AppLine(app)
  app = app or MS.App()
  if not app.present then
    return L["NeedleDropper app not found. Spotify and YouTube songs need it: get it where you got NeedleDropper."]
  end
  if not app.supports then
    if app.oldStyle then return L["Update the NeedleDropper app to 1.2.1 to play Spotify and YouTube songs."] end
    -- 1.2.0 and 1.2.1 frames can't read each other (DESIGN-v1.2.1 2.11).
    return format(L["NeedleDropper app %s can't read this addon's signals: update it to 1.2.1 to play Spotify and YouTube songs."],
      esc(tostring(app.version or "?")))
  end
  if app.written <= 0 then return L["NeedleDropper app installed: it hasn't reported in yet."] end
  local C = companion()
  local age = call(C, "ReportAge")
  if type(age) ~= "number" then age = 0 end
  return format(L["NeedleDropper app %s%sreport from %s (%s)"], esc(tostring(app.version or "?")), DOT,
    hm(app.written), ageText(age))
end

-- The Spotify line for state "no-player", by the cause the app names (music.spotify.problem, review ON-9).
-- An app that names none (older builds) or a cause this addon doesn't know gets the neutral line; only
-- "no-chrome" says to install Google Chrome.
MS.NO_PLAYER_LINE = "Spotify: NeedleDropper's player isn't ready (the app's Connect page says why)."
MS.NO_PLAYER_LINES = {
  ["no-chrome"] = "Spotify: install Google Chrome to play Spotify in NeedleDropper (if it is installed, wait a minute for its Widevine component).",
  ["not-ready"] = "Spotify: NeedleDropper's player didn't get ready. The app tries again by itself (its Connect page says more).",
  ["no-window"] = "Spotify: NeedleDropper's player isn't running yet. The app starts it when World of Warcraft isn't the window in front (start the app before the game).",
  ["window-never"] = "Spotify: NeedleDropper's player is switched off (deck.autoOpen is \"never\" in the app's config.json).",
  ["sdk-load"] = "Spotify: Spotify's player didn't load in NeedleDropper (no internet, or a firewall blocks it).",
}

local function notUsed(app, service)
  local order = MS.Order()
  if order == "none" then return DOT .. L["not used: Play music with is Neither"] end
  if service == "spotify" and order == "youtube" then return DOT .. L["not used: Play music with is YouTube only"] end
  if service == "youtube" and order == "spotify" then return DOT .. L["not used: Play music with is Spotify only"] end
  return ""
end

-- Line 2: Spotify, by music.spotify.state (D17) when the app reports it, else status.spotify.
function MS.SpotifyLine(app)
  app = app or MS.App()
  local state = app.state
  local line
  if state == "connected" then
    local who = app.account and esc(app.account) or nil
    if app.product and app.product ~= "premium" then
      line = format(L["Spotify: connected as %s, but Spotify %s can't be controlled: playback needs Premium."],
        who or L["your account"], esc(app.product))
    elseif who and app.product == "premium" then
      line = format(L["Spotify: connected as %s (Premium)"], who)
    elseif who then
      line = format(L["Spotify: connected as %s"], who)
    else
      line = L["Spotify: connected (no account name)"]
    end
    if app.mode == "connect" then line = line .. DOT .. L["plays on another Spotify device"] end
  elseif state == "premium-required" then
    line = L["Spotify: this account needs Premium to play songs."]
  elseif state == "needs-relogin" then
    line = L["Spotify: sign in again to allow in-app playback (NeedleDropper's Music services)."]
  elseif state == "no-player" then
    line = L[MS.NO_PLAYER_LINES[app.problem] or MS.NO_PLAYER_LINE]
  elseif state == "stuck" then
    line = L["Spotify: a song didn't start in NeedleDropper's player, so drops play YouTube until it works again (Test Spotify checks it)."]
  elseif state == "error" and app.problem == "allowlist" then
    line = L["Spotify: your Spotify app doesn't allow this account yet: add its e-mail address under User Management in the Spotify developer dashboard (the app's owner does this)."]
  elseif state == "error" then
    line = L["Spotify: sign in again (the sign-in stopped working)."]
  elseif MS.KnownClientId() then
    line = format(L["Spotify: not connected (Client ID %s)."], MS.ShortId(MS.KnownClientId()))
  else
    line = L["Spotify: not set up."]
  end
  return line .. notUsed(app, "spotify")
end

-- Line 3: YouTube.
function MS.YouTubeLine(app)
  app = app or MS.App()
  local line
  if not app.present then
    line = L["YouTube: plays through the NeedleDropper app."]
  elseif not app.youtube or app.ytEnabled == false then
    line = L["YouTube: switched off in the NeedleDropper app (its Connect page)."]
  elseif app.ytPlayer == "none" then
    line = L["YouTube: the NeedleDropper app found no browser for its player box: install Google Chrome."]
  else
    line = format(L["YouTube: ready%splayer box %s"], DOT, L[MS.CORNER_NAMES[MS.Corner()]])
    local b = MS.BROWSER_NAMES[app.ytPlayer]
    if b then line = line .. format(L[" (%s)"], b) end
  end
  if type(app.signInOpened) == "number" and app.signInOpened > 0 then
    line = line .. DOT .. format(L["sign-in page opened %s"], safeDate("%d %b", app.signInOpened))
  end
  return line .. notUsed(app, "youtube")
end

-- Line 4: the newest request and what became of it (DESIGN-v1.2.1 4.3); "" when none was sent.
function MS.LastRequestLine(app)
  local r = MS.LastRequest()
  if not r then return "" end
  app = app or MS.App()
  local label = L[MS.ACTION_LABELS[r.action] or r.action]
  local when = hm(r.at)
  for i = #app.actions, 1, -1 do
    local e = app.actions[i]
    if e.id == r.at and e.action == r.action then
      return format(L["%s (%s): %s"], label, when, esc(e.text or e.result or ""))
    end
  end
  if app.present and app.written >= r.at + MS.LATE_REPORT then
    return format(L["%s (%s): the NeedleDropper app didn't receive this request. Check the link with Send a test signal, then try again."], label, when)
  end
  return format(L["%s (%s): no report from the NeedleDropper app since. Press Refresh status after it confirms in Windows."], label, when)
end

function MS.StatusLines()
  local app = MS.App()
  return { MS.AppLine(app), MS.SpotifyLine(app), MS.YouTubeLine(app), MS.LastRequestLine(app) }
end

-- The copy shown after a change of choices (§4.2).
function MS.ChoicesCopy()
  local app = MS.App()
  if not app.present then return L["Saved. The NeedleDropper app picks it up when it sees the game."] end
  if not app.supports then
    return L["Saved. Update the NeedleDropper app to 1.2.1: the version you have can't read this addon's signals."]
  end
  return L["The NeedleDropper app follows this within a second while the game runs."]
end

-- ------------------------------------------------------------------ slash --

local function printLines()
  for _, line in ipairs(MS.StatusLines()) do
    if line ~= "" then ns.Print("%s", line) end
  end
end

MS.CLIENT_ID_HELP = "A Client ID is 32 characters: digits and the letters a-f. Copy the Client ID from your app's Settings page, not the Client secret."

-- /needle music [status | connect [clientId] | disconnect | test spotify | test youtube | youtube]
function MS.Slash(arg)
  local cmd, rest = match(trim(arg or ""), "^([^ \t\r\n]*)[ \t\r\n]*(.-)$")
  cmd = lower(cmd or "")
  rest = rest or ""
  if cmd == "" then
    MS.OpenDialog()
  elseif cmd == "status" then
    printLines()
  elseif cmd == "connect" then
    if trim(rest) ~= "" then
      local id, valid = MS.NormalizeClientId(rest)
      if not valid then
        ns.Print("%s", L[MS.CLIENT_ID_HELP])
        return nil
      end
      MS.SetClientId(id)
    end
    return MS.Send("connect-spotify")
  elseif cmd == "disconnect" then
    return MS.Send("disconnect-spotify")
  elseif cmd == "test" and lower(trim(rest)) == "spotify" then
    return MS.Send("test-spotify")
  elseif cmd == "test" and lower(trim(rest)) == "youtube" then
    return MS.Send("test-youtube")
  elseif cmd == "youtube" then
    return MS.Send("youtube-sign-in")
  else
    ns.Print("%s", L["usage: /needle music [status | connect [Client ID] | disconnect | test spotify | test youtube | youtube]"])
  end
end

-- ----------------------------------------------------------------- events --

ns.On("ND_INIT", function()
  db()
  MS.PushPrefs()
end)

ns.On("ND_SETTING", function(_, key)
  if MS.KEYS[key] then MS.PrefsChanged() end
end)

ns.On("ND_RESET", function(_, what)
  if what == "settings" then MS.PrefsChanged() end
end)

ns.On("ND_COMPANION_DATA", function()
  MS.ApplyEcho()
end)

return MS
