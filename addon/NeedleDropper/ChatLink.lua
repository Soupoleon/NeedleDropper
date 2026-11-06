local ADDON, ns = ...
-- ChatLink: the secondary, opt-in transport through WoWChatLog.txt (DESIGN-v1.2 2.1,
-- docs/PROTOCOL-v2.md section 3). Each event becomes one "NDROP2 <base64url frame>" line
-- (Protocol2.FormatChatLine: the same frame the beacon draws, with every field) sent with
-- SendSystemMessage; after FIRE / CANCEL / PING, "NDPAD" filler lines push the client's
-- ~48 KiB log buffer to disk. A CHAT_MSG_SYSTEM filter hides NDROP1 / NDROP2 / NDPAD lines
-- from the chat window. Lines wait in a queue while chat messaging is locked down or the
-- Chat addon restriction is active; a FIRE line (with its padding) that waited more than
-- 10 s is dropped, because the moment it was for has passed. Chat logging is only turned on
-- when settings.chatlog is (or when the chat-log self-test asks).
-- v1.2.1 (DESIGN-v1.2.1 2.8): a CONFIG with an ACTION (a request to the app) is padded like
-- FIRE / CANCEL / PING so it reaches the disk at once, and dropped with its padding after 20 s
-- in the queue (the app ignores requests older than 45 s anyway); a PREFS-only CONFIG is not
-- padded and is kept like other lines. HELLO lines carry PREFS once set (Emit.lua).

local C = {}
ns.ChatLink = C

local P2 = ns.Protocol2
local EV = P2.EV
local RETRY = 1.0           -- seconds between flush attempts while chat is restricted
local QUEUE_MAX = 512       -- queued records (each with its padding)
local FIRE_MAX_AGE = 10     -- seconds: an older queued FIRE line is dropped
local CONFIG_MAX_AGE = 20   -- seconds: an older queued request (CONFIG with an ACTION) is dropped
local PADDED = { [EV.FIRE] = true, [EV.CANCEL] = true, [EV.PING] = true }

C.FIRE_MAX_AGE = FIRE_MAX_AGE
C.CONFIG_MAX_AGE = CONFIG_MAX_AGE
C.queue = {}                -- { line =, type =, seq =, at =, pads =, action = bool } (raw lines: type nil)
C.sent = 0                  -- lines handed to SendSystemMessage
C.dropped = 0               -- records dropped because the queue was full
C.stale = 0                 -- FIRE and request records dropped because they waited too long
C.weEnabledLogging = false
C.filterInstalled = false
local retryScheduled = false

-- A CONFIG that carries a request (ACTION), as opposed to music choices only.
local function isRequest(ev)
  return ev.type == EV.CONFIG and ev.action ~= nil
end

-- Message filter: hides our own lines. Never looks inside a secret value.
function C.Filter(self, event, msg, ...)
  if ns.IsSecret(msg) or type(msg) ~= "string" then return false end
  local head = string.sub(msg, 1, 7)
  if head == "NDROP2 " or head == "NDROP1 " or string.sub(msg, 1, 6) == "NDPAD " then return true end
  return false
end

function C.InstallFilter()
  if C.filterInstalled then return true end
  local add = (ChatFrameUtil and ChatFrameUtil.AddMessageEventFilter) or ChatFrame_AddMessageEventFilter
  if type(add) ~= "function" then return false end
  local ok = pcall(add, "CHAT_MSG_SYSTEM", C.Filter)
  C.filterInstalled = ok
  return ok
end

-- true / false, or nil when the client cannot tell.
function C.IsLogging()
  if C_ChatInfo and C_ChatInfo.IsLoggingChat then
    local ok, v = pcall(C_ChatInfo.IsLoggingChat)
    if ok and not ns.IsSecret(v) then return v and true or false end
  end
  if type(LoggingChat) == "function" then
    local ok, v = pcall(LoggingChat)
    if ok and not ns.IsSecret(v) then return v and true or false end
  end
  return nil
end

-- Turns chat logging on (called when settings.chatlog is on, and by the chat-log self-test).
function C.EnsureLogging()
  if C.IsLogging() then return true end
  if type(LoggingChat) ~= "function" then return false end
  pcall(LoggingChat, true)
  local now = C.IsLogging()
  if now then C.weEnabledLogging = true end
  return now and true or false
end

-- Turns chat logging off again, but only if this addon turned it on.
function C.StopLogging()
  if C.weEnabledLogging and type(LoggingChat) == "function" then
    pcall(LoggingChat, false)
  end
  C.weEnabledLogging = false
end

-- True while lines must wait: chat messaging lockdown or the Chat addon restriction
-- (Enum.AddOnRestrictionType.Chat, forever 1.60.1). A secret answer counts as restricted.
function C.Restricted()
  if C_ChatInfo and C_ChatInfo.InChatMessagingLockdown then
    local ok, v = pcall(C_ChatInfo.InChatMessagingLockdown)
    if ok and (ns.IsSecret(v) or v) then return true end
  end
  local kinds = Enum and Enum.AddOnRestrictionType
  if kinds and kinds.Chat ~= nil and C_RestrictedActions and C_RestrictedActions.IsAddOnRestrictionActive then
    local ok, v = pcall(C_RestrictedActions.IsAddOnRestrictionActive, kinds.Chat)
    if ok and (ns.IsSecret(v) or v) then return true end
  end
  return false
end
C.InLockdown = C.Restricted   -- v1 name

local function enqueue(entry)
  local q = C.queue
  q[#q + 1] = entry
  if #q > QUEUE_MAX then
    table.remove(q, 1)
    C.dropped = C.dropped + 1
  end
end

local function send(line)
  if pcall(SendSystemMessage, line) then C.sent = C.sent + 1 end
end

-- Sends queued lines now, or retries every second while chat is restricted.
function C.Flush()
  if #C.queue == 0 then return end
  if C.Restricted() or type(SendSystemMessage) ~= "function" then
    if not retryScheduled then
      retryScheduled = true
      C_Timer.After(RETRY, function()
        retryScheduled = false
        C.Flush()
      end)
    end
    return
  end
  local q = C.queue
  C.queue = {}
  local now = GetTime()
  for i = 1, #q do
    local e = q[i]
    if (e.type == EV.FIRE and now - e.at > FIRE_MAX_AGE) or (e.action and now - e.at > CONFIG_MAX_AGE) then
      C.stale = C.stale + 1
    else
      send(e.line)
      for _ = 1, e.pads or 0 do send(P2.PadLine(e.seq)) end
    end
  end
end

-- Queues raw lines (self-test padding).
function C.SendLines(lines)
  for i = 1, #lines do enqueue({ line = lines[i], at = GetTime(), pads = 0 }) end
  C.Flush()
end

-- Number of NDPAD lines needed for `bytes` bytes of padding.
function C.PadCount(bytes)
  return P2.PadLineCount(bytes)
end

-- Sends Protocol2 event `ev` (from ns.Emit). opts.force: send even when settings.chatlog is
-- off (self-tests); opts.noPad: no padding. Returns true when the line was queued.
function C.Send(ev, opts)
  local s = ns.settings
  if not s or type(ev) ~= "table" then return false end
  if not s.chatlog and not (opts and opts.force) then return false end
  local pads = 0
  local request = isRequest(ev)
  if (PADDED[ev.type] or request) and not (opts and opts.noPad) then
    pads = P2.PadLineCount(s.chatlogPadBytes or 0)
  end
  local at = (type(ev.t) == "number") and ev.t or GetTime()
  enqueue({ line = P2.FormatChatLine(ev), type = ev.type, seq = ev.seq, at = at, pads = pads,
            action = request or nil })
  C.Flush()
  return true
end

function C.Status()
  local lines = 0
  for i = 1, #C.queue do lines = lines + 1 + (C.queue[i].pads or 0) end
  return {
    enabled = ns.settings and ns.settings.chatlog or false,
    logging = C.IsLogging(),
    queued = #C.queue,
    queuedLines = lines,
    sent = C.sent,
    dropped = C.dropped,
    stale = C.stale,
    lockdown = C.Restricted(),
  }
end

ns.On("ND_INIT", function()
  C.InstallFilter()
  if ns.settings.chatlog then C.EnsureLogging() end
end)

ns.On("PLAYER_LOGIN", function()
  C.InstallFilter()
  if ns.settings and ns.settings.chatlog then
    if not C.EnsureLogging() then
      ns.Print("could not turn on chat logging for the chat-log channel; type /chatlog once.")
    end
  end
end)

ns.On("ND_SETTING", function(_, key, value)
  if key ~= "chatlog" then return end
  if value then
    C.InstallFilter()
    if not C.EnsureLogging() then
      ns.Print("chat logging did not turn on; type /chatlog in the chat box.")
    end
  else
    C.StopLogging()
  end
end)
