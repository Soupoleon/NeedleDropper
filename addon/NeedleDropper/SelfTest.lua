local ADDON, ns = ...
-- SelfTest (/needle selftest [beacon|chatlog|discovery|music|share]): checks, on the user's real
-- client, the assumptions the design relies on but that could not be verified offline
-- (DESIGN-v1.2 6.4). Also the Settings panel's "Send a test signal" (S.Signal) and /needle report
-- (S.Report, decision D16: a plain-text report for bug reports).

local S = {}
ns.SelfTest = S

local P = ns.Protocol2
local L = ns.L or setmetatable({}, { __index = function(_, k) return k end })

local DISCOVERY_WINDOW = 120
local MUSIC_SECONDS = 8
local BEACON_SECONDS = 10
local SIGNAL_SECONDS = 5
local PAD_STEP_BYTES = 8192
local PAD_MAX_BYTES = 65536
local PAD_LINE = 900

-- A share code written by Python's zlib (tests/vectors/share/minimal.json, the DESIGN-v1.2 5.4
-- reference encoder): /needle selftest share decodes it with the client's own DecompressString.
S.GOLDEN_CODE = "!NeedleDropper:1!eNo9jjEOwjAMRa9SeWEpEiAWujIhYGFFDFZrikUSR04oElXvjgOI0e/Z338EfOSbKDQANXSUWuWYWcIPqMQEzXkE1MwpGz3IszoJqtnM2ZGh3cy5ak8Uq614Dr2plwQzy/VqM11quIp6LMeBqHNUUiPpPGJ7t13uzNgde3Q2BvQl9PgHSgOnT6WF/cS+FLLQgfRLl9Mb5x9AMA==!"
S.GOLDEN_PACK = { id = "minimal", name = "Minimal", zone = 1429, artist = "Low Roar", title = "I'll Keep Coming" }

S.discoveryUntil = 0
S.padTicker = nil

local function discoveryActive()
  return GetTime() < S.discoveryUntil
end

local function px(v)
  if type(v) ~= "number" then return "?" end
  return string.format("%.1f", v)
end

-- Settings panel "Send a test signal" (DESIGN-v1.2 4.7): PING with the TEST flag; the strip is
-- shown for a few seconds even when the beacon is off.
function S.Signal()
  local forced = not (ns.settings and ns.settings.beacon)
  if ns.Beacon and ns.Beacon.ForceShow then ns.Beacon.ForceShow(SIGNAL_SECONDS) end
  local ev = ns.Emit(P.EV.PING, nil, P.FLAG.TEST)
  ns.Print(L["test signal sent (seq %d)%s. Your NeedleDropper app shows a notification within 2 seconds if it can see the game."],
    ev and ev.seq or 0, forced and L[" (the beacon is off: shown for a few seconds anyway)"] or "")
  return ev
end
ns.SendTestSignal = S.Signal

-- The strip /needle selftest beacon shows for 10 s (DESIGN-v1.2 6.4 step 2): the largest song frame
-- this cell size allows, with nothing an app could play. It is a FIRE of the test drop with the
-- TEST and LOCAL_PLAYBACK flags (the app shows and logs a LOCAL_PLAYBACK song, never plays it) and
-- text only -- no Spotify or YouTube id -- whose title is trimmed until the whole frame fits the
-- 9 px rows unfitted (so the fit rule removes nothing). Never emitted: no ns.Events entry, no chat
-- line, no seq of its own.
local SAMPLE_TITLE = "Beacon self-test: the largest strip a song frame can draw on this screen, never played"

function S.SampleEvent(seq, px)
  local ev = { type = P.EV.FIRE, seq = seq or 0, flags = P.FLAG.TEST + P.FLAG.LOCAL_PLAYBACK, dropId = "test",
               placeKey = P.PlaceKey("test"), artist = "NeedleDropper", title = SAMPLE_TITLE,
               lengthMs = BEACON_SECONDS * 1000, label = "Beacon self-test" }
  local cap = P.Capacity(P.RowsCap(px or 3))
  while #P.EncodeFrame(ev) > cap and #ev.title > 1 do ev.title = string.sub(ev.title, 1, -2) end
  return ev
end

-- beacon: send a PING with the TEST flag (the app's "heard World of Warcraft"), then show the
-- largest song strip for 10 s, and report the geometry (DESIGN-v1.2 2.3 runtime guard): the
-- measured gap above the player frame, the cell size, the rows a song strip may use and the
-- largest strip's size.
function S.Beacon()
  local B = ns.Beacon
  if ns.Director and ns.Director.active then
    -- The app would let the self-test strip replace the song it is playing.
    ns.Print("a drop is playing; /needle stop it first, then run the beacon test again.")
    return nil
  end
  local forced = not ns.settings.beacon
  if B.Layout then B.Layout() end            -- measure the clearance now
  B.ForceShow(BEACON_SECONDS + 2)
  local ev = ns.Emit(P.EV.PING, nil, P.FLAG.TEST)
  local sample = S.SampleEvent(ev.seq, B.SettingCellPx and B.SettingCellPx() or ns.settings.beaconCellPx)
  if B.ShowSample then B.ShowSample(sample, BEACON_SECONDS) end
  local st = B.Status()
  ns.Print("beacon self-test: sent PING seq %d (test signal) in the %s corner%s.", ev.seq, string.lower(st.corner or "TOPLEFT"),
    forced and (" (the beacon is off in settings - shown for " .. BEACON_SECONDS .. " s for this test)") or "")
  if st.gap then
    local from = st.measuredFrom and #st.measuredFrom > 0 and table.concat(st.measuredFrom, ", ") or "?"
    ns.Print("gap above your player frame: %s px (measured from %s).", px(st.gap), from)
  else
    ns.Print("gap above your player frame: not measured (no player or target frame art near the corner).")
  end
  ns.Print("cells: %d px drawn (setting %d px); a song strip may use %d row%s; this strip: %s px (%d row%s).",
    st.cellPx or 0, st.settingCellPx or 0, st.maxRows or 0, (st.maxRows or 0) == 1 and "" or "s",
    tostring(st.size), st.rows or 0, (st.rows or 0) == 1 and "" or "s")
  local ok, worst = pcall(B.Choose, sample, st.settingCellPx, B.gap, B.Corner and B.Corner() or "TOPLEFT")
  if ok and type(worst) == "table" and worst.grid then
    ns.Print("largest song strip (shown for %d s after the PING): %dx%d px (%d row%s x %d columns of %d px, %d bytes).",
      BEACON_SECONDS, worst.grid.widthPx, worst.grid.heightPx, worst.grid.rows, worst.grid.rows == 1 and "" or "s",
      worst.grid.cols, worst.px, #worst.bytes)
  end
  local warnings = st.warnings or {}
  for i = 1, #warnings do ns.Print("|cffff6060warning:|r %s", warnings[i]) end
  if #warnings == 0 then ns.Print("the strip clears your player frame.") end
  ns.Print("the NeedleDropper app's |cffffffffdiag|r should show PING seq %d, then the test strip (FIRE seq %d, test, plays nothing).",
    ev.seq, ev.seq)
  return ev
end

-- chatlog: is logging on, does LoggingChat stick, how much padding flushes the file?
function S.Chatlog()
  local C = ns.ChatLink
  local before = C.IsLogging()
  local stuck = before
  if not before then stuck = C.EnsureLogging() end
  ns.Print("chat-log self-test: logging was %s; %s.", before == nil and "unknown" or (before and "on" or "off"),
    before and "nothing to change" or (stuck and "LoggingChat(true) turned it on" or "LoggingChat(true) did NOT stick"))
  if not stuck then
    ns.Print("type |cffffffff/chatlog|r in the chat box to turn logging on, then run /needle selftest chatlog again.")
  end
  local ev = ns.Emit(P.EV.PING, nil, 0, { noPad = true, forceChat = true })
  ns.Print("sent an unpadded PING seq %d. Padding now in 8 KB steps once per second up to 64 KB - " ..
    "watch the companion's diag to see at which size the PING reaches WoWChatLog.txt.", ev.seq)
  if S.padTicker then S.padTicker:Cancel() end
  local sent = 0
  local step = 0
  local steps = math.floor(PAD_MAX_BYTES / PAD_STEP_BYTES)
  S.padTicker = C_Timer.NewTicker(1, function(self)
    step = step + 1
    local target = step * PAD_STEP_BYTES
    local lines = {}
    while sent < target do
      lines[#lines + 1] = P.PadLine(ev.seq, PAD_LINE)
      sent = sent + PAD_LINE
    end
    C.SendLines(lines)
    ns.Print("chat-log padding after PING seq %d: %d bytes (%.1f KB).", ev.seq, sent, sent / 1024)
    if step >= steps then
      self:Cancel()
      S.padTicker = nil
      ns.Print("chat-log self-test done. Set chatlogPadBytes to a little more than the size at which the PING appeared.")
    end
  end, steps)
  return ev
end

-- discovery: print every relevant message / event for 120 s.
function S.Discovery()
  S.discoveryUntil = GetTime() + DISCOVERY_WINDOW
  ns.Print("discovery self-test for %d s: walk into a place you have never been. " ..
    "Every info message, exploration update and zone event will be printed.", DISCOVERY_WINDOW)
  C_Timer.After(DISCOVERY_WINDOW + 0.05, function()
    if not discoveryActive() then ns.Print("discovery self-test finished.") end
  end)
end

-- music: duck for 8 s, restore, ask whether zone music came back.
function S.Music()
  if ns.Director.active then
    ns.Print("a drop is playing; /needle stop it first, then run the music test again.")
    return false
  end
  if ns.Audio.IsDucked() then
    ns.Print("zone music is already ducked; try again in a moment.")
    return false
  end
  local method = ns.settings.duckMethod
  if method == "none" then method = "silence" end
  ns.Audio.Duck({ method = method })
  ns.Print("music self-test: zone music should fade out now (method '%s') and come back in %d s.", method, MUSIC_SECONDS)
  C_Timer.After(MUSIC_SECONDS, function()
    if ns.Director.active then return end -- a real drop took over the ducking
    ns.Audio.Restore()
    ns.Print("zone music restored. Did it come back within a few seconds? If not, try " ..
      "/needle set kickZoneMusic on, or /needle set duckMethod volume.")
  end)
  return true
end

-- share: the client's zlib (C_EncodingUtil, DESIGN-v1.2 5.3 and 6.4 step 1). Returns ok (bool).
--   1. CompressString's first byte (a zlib stream starts with 0x78);
--   2. a code written by Python decodes to the expected soundtrack;
--   3. a code written here decodes back to the same soundtrack.
function S.Share()
  local Share = ns.Share
  if not (Share and Share.Decode and Share.Encode) then
    ns.Print("share self-test: share codes are not available.")
    return false
  end
  local allOk = true
  local util = C_EncodingUtil
  local method = Enum and Enum.CompressionMethod and Enum.CompressionMethod.Zlib
  local level = Enum and Enum.CompressionLevel and Enum.CompressionLevel.OptimizeForSize
  if util and util.CompressString and method then
    local ok, z = pcall(util.CompressString, "NeedleDropper share self-test", method, level)
    if ok and type(z) == "string" and #z > 0 then
      local b = string.byte(z, 1)
      ns.Print("share self-test: CompressString's first byte is 0x%02X (%s).", b, b == 0x78 and "OK, zlib" or "expected 0x78")
      if b ~= 0x78 then allOk = false end
    else
      ns.Print("share self-test: CompressString failed (%s).", ok and "no output" or tostring(z))
      allOk = false
    end
  else
    ns.Print("share self-test: this client has no C_EncodingUtil.CompressString with zlib.")
    allOk = false
  end
  local g = S.GOLDEN_PACK
  local pack, why = Share.Decode(S.GOLDEN_CODE)
  local d = pack and pack.drops and pack.drops[1]
  if pack and pack.id == g.id and pack.name == g.name and #pack.drops == 1 and d.zone == g.zone
     and d.artist == g.artist and d.title == g.title then
    ns.Print("share self-test: a code made by the NeedleDropper app's encoder decodes: OK.")
  else
    ns.Print("share self-test: the reference code did not decode (%s).", tostring(pack and "wrong content" or why))
    allOk = false
  end
  if pack then
    local code, err = Share.Encode(pack)
    local back = code and Share.Decode(code)
    local bd = back and back.drops and back.drops[1]
    if back and back.id == pack.id and #back.drops == #pack.drops and bd and bd.artist == g.artist then
      ns.Print("share self-test: export and import round trip: OK (%d characters).", #code)
    else
      ns.Print("share self-test: the round trip failed (%s).", tostring(err or "the new code did not decode"))
      allOk = false
    end
  end
  ns.Print(allOk and "share self-test passed: share codes work on this client." or
    "share self-test FAILED: please report the lines above.")
  return allOk
end

-- ------------------------------------------------------------------ report --
-- /needle report (decision D16): what a bug report needs, as plain text the player copies with
-- Ctrl+C from a read-only box: the addon and client versions, the app's report (version, age,
-- Spotify and YouTube), the beacon (cell size, rows, clearance) and the chat log, the settings that
-- matter, the last 5 journal entries and the last error. Never an account name, a Client ID or a
-- token (the app's request texts can name the Spotify account, so only their result codes appear).

S.REPORT_SETTINGS = { "enabled", "historyMode", "scope", "cooldownMin", "dwellSec", "minDwellSec",
  "landmarkDwellSec", "instanceDrops", "suppressOnTaxi", "combatFadeSec", "maxDropSec", "duckMethod",
  "kickZoneMusic", "duckWithoutApp", "localPlayback", "toast", "chatlogPadBytes", "debug" }
S.REPORT_JOURNAL = 5

local function onOffWord(v) return v and "on" or "off" end

local function setting(key)
  local s = ns.settings or {}
  return ns.FormatSettingValue and ns.FormatSettingValue(key, s[key]) or tostring(s[key])
end

local function appLines(out)
  local C = ns.Companion
  local MS = ns.MusicServices
  local st = (C and C.Status) and C.Status() or {}
  if not st.present then
    out[#out + 1] = "App: not found (no NeedleDropper_Companion data)"
    return
  end
  local app = MS and MS.App and MS.App() or {}
  local version = tostring(st.appVersion or "?")
  if C.IsOutdated and C.IsOutdated(st.appVersion) then
    out[#out + 1] = "App: " .. version .. " (older than 1.2.1: it can't read this addon's frames)"
    return
  end
  local age = C.ReportAge and C.ReportAge()
  local when = "no report written yet"
  if age then
    when = string.format("report written %s (%s before login)", date("%Y-%m-%d %H:%M", st.written),
      ns.FormatSeconds and ns.FormatSeconds(age) or tostring(age))
  end
  out[#out + 1] = string.format("App: %s, %s", version, when)
  local sp = tostring(app.state or st.spotify or "unknown")
  if app.mode then sp = sp .. ", mode " .. app.mode end
  if app.product then sp = sp .. ", plan " .. app.product end
  if app.clientId or (MS and MS.StoredClientId and MS.StoredClientId()) then sp = sp .. ", Client ID set" end
  local yt = st.youtube and "on" or "off"
  if app.ytEnabled == false then yt = "switched off" end
  if app.ytPlayer then yt = yt .. ", player " .. app.ytPlayer end
  out[#out + 1] = string.format("  Spotify: %s; YouTube: %s; transports: pixel %s, chat log %s", sp, yt,
    onOffWord(st.transports and st.transports.pixel), onOffWord(st.transports and st.transports.chatlog))
  local r = MS and MS.LastRequest and MS.LastRequest()
  if r then
    local result = "no result reported"
    for _, e in ipairs(app.actions or {}) do
      if e.id == r.at and e.action == r.action then result = e.result end
    end
    out[#out + 1] = string.format("  Last request: %s at %s: %s", r.action, date("%H:%M", r.at), result)
  end
end

local function beaconLines(out)
  local B = ns.Beacon
  if not (B and B.Status) then
    out[#out + 1] = "Beacon: not loaded"
    return
  end
  local st = B.Status()
  local gap = st.gap and string.format("%.1f px", st.gap) or "not measured"
  local from = (st.measuredFrom and #st.measuredFrom > 0) and (" from " .. table.concat(st.measuredFrom, ", ")) or ""
  out[#out + 1] = string.format("Beacon: %s, %d px cells (setting %d), %d row%s, %s px, up to %d rows, clearance %s%s, corner %s",
    onOffWord(st.enabled), st.cellPx or 0, st.settingCellPx or 0, st.rows or 0, (st.rows or 0) == 1 and "" or "s",
    tostring(st.size), st.maxRows or 0, gap, from, string.lower(tostring(st.corner or "TOPLEFT")))
  local last = st.current
  if last then
    out[#out + 1] = string.format("  Drawn: %s seq %s (%d bytes, %s); queued %d, requests queued %d, dropped %d",
      P.EV_NAME[last.type] or "?", tostring(last.seq), st.lastFrameBytes or 0, tostring(st.variant), st.queued or 0,
      st.configQueued or 0, (st.dropped or 0) + (st.configDropped or 0))
  end
  for _, w in ipairs(st.warnings or {}) do out[#out + 1] = "  Warning: " .. w end
  local C = ns.ChatLink
  local c = (C and C.Status) and C.Status() or {}
  if c.enabled then
    out[#out + 1] = string.format("Chat log: on, logging %s, %d lines sent, %d queued, %d stale%s",
      c.logging == nil and "unknown" or onOffWord(c.logging), c.sent or 0, c.queued or 0, c.stale or 0,
      c.lockdown and ", chat locked down" or "")
  else
    out[#out + 1] = "Chat log: off"
  end
end

-- The report as plain text (lines joined with "\n"; no "|" anywhere, so the box shows it as is).
function S.ReportText()
  local out = {}
  out[#out + 1] = "NeedleDropper report, " .. date("%Y-%m-%d %H:%M")
  local version, build, _, toc = "?", "?", nil, "?"
  if type(GetBuildInfo) == "function" then
    local ok, v, b, d, t = pcall(GetBuildInfo)
    if ok then version, build, toc = v or "?", b or "?", t or "?" end
  end
  out[#out + 1] = string.format("Addon: %s; client %s (build %s, interface %s), locale %s", tostring(ns.VERSION),
    tostring(version), tostring(build), tostring(toc), tostring(ns.LOCALE or (GetLocale and GetLocale()) or "?"))
  appLines(out)
  beaconLines(out)
  local s = ns.settings or {}
  local MS = ns.MusicServices
  out[#out + 1] = string.format("Music: play with %s, YouTube box %s, fade %s, resume %s, chosen in game %s",
    tostring(s.musicOrder), tostring(s.youtubeCorner), onOffWord(s.musicFade ~= false),
    onOffWord(s.resumeMyMusic ~= false), (MS and MS.PrefsSet and MS.PrefsSet()) and "yes" or "no")
  local parts = {}
  for _, key in ipairs(S.REPORT_SETTINGS) do parts[#parts + 1] = key .. " " .. setting(key) end
  out[#out + 1] = "Settings: " .. table.concat(parts, ", ")
  local names = ns.ActiveSoundtrackNames and ns.ActiveSoundtrackNames() or {}
  local drops = (ns.Map and ns.Map.drops) and #ns.Map.drops or 0
  out[#out + 1] = string.format("Soundtracks: %s (%d drops)", #names > 0 and table.concat(names, ", ") or "none on", drops)
  if ns.PlaybackSummary then out[#out + 1] = "Now: " .. tostring(ns.PlaybackSummary()) end
  local j = ns.cdb and ns.cdb.journal or {}
  if #j == 0 then
    out[#out + 1] = "Journal: empty"
  else
    out[#out + 1] = string.format("Journal (last %d of %d):", math.min(S.REPORT_JOURNAL, #j), #j)
    for i = math.max(1, #j - S.REPORT_JOURNAL + 1), #j do
      local e = j[i]
      local when = (type(e.t) == "number" and e.t > 0) and date("%Y-%m-%d %H:%M", e.t) or "?"
      local via = e.spotify and "Spotify" or (e.youtube and "YouTube" or (e.file and "file" or "search"))
      out[#out + 1] = string.format("  %s  %s  %s - %s (%s)", when, tostring(e.id), tostring(e.artist or "?"),
        tostring(e.title or "?"), via)
    end
  end
  local err = ns.lastError
  if type(err) == "table" then
    out[#out + 1] = string.format("Last error (%s): %s", date("%H:%M", err.at), tostring(err.text))
  else
    out[#out + 1] = "Last error: none this session"
  end
  local text = table.concat(out, "\n")
  return (string.gsub(text, "|", "/"))
end

-- /needle report: the report in its dialog (selected for Ctrl+C) and one chat line; without the
-- window, the report goes to chat.
function S.Report()
  local text = S.ReportText()
  local U = ns.UI
  if type(U) == "table" and type(U.OpenReport) == "function" and U.OpenReport(text) then
    ns.Print(L["report ready: press Ctrl+C to copy it from the box, then paste it into your bug report. It holds no account names, Client IDs or tokens."])
    return text
  end
  for row in string.gmatch(text, "[^\n]+") do ns.Print("%s", ns.EscapeText and ns.EscapeText(row) or row) end
  return text
end

local TESTS = { beacon = S.Beacon, chatlog = S.Chatlog, discovery = S.Discovery, music = S.Music, share = S.Share }

function S.Run(which)
  which = which or ""
  if which == "" then
    ns.Print("self-tests: /needle selftest beacon | chatlog | discovery | music | share. Running 'beacon':")
    which = "beacon"
  end
  local fn = TESTS[which]
  if not fn then
    ns.Print("unknown self-test '%s' (beacon, chatlog, discovery, music, share).", ns.EscapeText and ns.EscapeText(which) or which)
    return
  end
  return fn()
end

-- ---------------------------------------------------- discovery printers ----

local function describeMap()
  local Loc = ns.Location
  local snap = Loc.Snapshot()
  return string.format("best map %s -> zone %s, subzone '%s'%s", tostring(snap.rawMapID),
    tostring(snap.zoneMapID or "none"), snap.subzone,
    snap.inInstance and (" (instance " .. tostring(snap.instanceID) .. ")") or "")
end

ns.On("UI_INFO_MESSAGE", function(_, errorType, message)
  if not discoveryActive() then return end
  local name = ns.Discovery.MessageName(errorType) or (ns.IsSecret(errorType) and "<secret>" or "?")
  local text = ns.SafeString(message) or (ns.IsSecret(message) and "<secret>" or "")
  ns.Print("[selftest] UI_INFO_MESSAGE %s: %s", name, text)
end)

ns.On("MAP_EXPLORATION_UPDATED", function()
  if not discoveryActive() then return end
  ns.Print("[selftest] MAP_EXPLORATION_UPDATED - %s", describeMap())
end)

for _, event in ipairs({ "ZONE_CHANGED", "ZONE_CHANGED_INDOORS", "ZONE_CHANGED_NEW_AREA", "PLAYER_MAP_CHANGED" }) do
  ns.On(event, function(ev)
    if not discoveryActive() then return end
    ns.Print("[selftest] %s - %s", ev, describeMap())
  end)
end
