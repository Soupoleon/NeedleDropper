local ADDON, ns = ...
-- SelfTest (/needle selftest [beacon|chatlog|discovery|music]): checks, on the user's real
-- client, the assumptions the design relies on but that could not be verified offline.

local S = {}
ns.SelfTest = S

local P = ns.Protocol
local DISCOVERY_WINDOW = 120
local MUSIC_SECONDS = 8
local PAD_STEP_BYTES = 8192
local PAD_MAX_BYTES = 65536
local PAD_LINE = 900

S.discoveryUntil = 0
S.padTicker = nil

local function discoveryActive()
  return GetTime() < S.discoveryUntil
end

-- beacon: show the strip, emit PING.
function S.Beacon()
  local forced = not ns.settings.beacon
  ns.Beacon.ForceShow(60)
  local ev = ns.Emit(P.EV.PING, nil, 0)
  ns.Print("beacon self-test: sent PING seq %d in the %d px strip at the top-left corner%s.", ev.seq,
    ns.settings.beaconCellPx, forced and " (beacon is off in settings - shown for 60 s for this test)" or "")
  ns.Print("the companion's |cffffffffdiag|r should show PING seq %d.", ev.seq)
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

local TESTS = { beacon = S.Beacon, chatlog = S.Chatlog, discovery = S.Discovery, music = S.Music }

function S.Run(which)
  which = which or ""
  if which == "" then
    ns.Print("self-tests: /needle selftest beacon | chatlog | discovery | music. Running 'beacon':")
    which = "beacon"
  end
  local fn = TESTS[which]
  if not fn then
    ns.Print("unknown self-test '%s' (beacon, chatlog, discovery, music).", which)
    return
  end
  return fn()
end

-- ---------------------------------------------------- discovery printers ----

local function describeMap()
  local L = ns.Location
  local snap = L.Snapshot()
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
