-- NeedleDropper wire protocol (docs/SPEC.md 3.4-3.6). Shared contract with
-- companion/needledropper/protocol.py -- keep both in lockstep.
-- Pure Lua 5.1 arithmetic: no bit library needed.
local ADDON, ns = ...
ns = ns or {}

local P = {}
ns.Protocol = P

P.MAGIC = 0x4E
P.VERSION = 1
P.PAYLOAD_CELLS = 27
P.HEADER = { {1, 0, 1}, {0, 1, 0}, {1, 0, 1}, {0, 1, 0} } -- magenta, green, magenta, green (0/1 per channel)
P.TOTAL_CELLS = #P.HEADER + P.PAYLOAD_CELLS -- 31

P.EV = { NONE = 0, HELLO = 1, ARM = 2, FIRE = 3, CANCEL = 4, PING = 5 }
P.EV_NAME = { [0] = "NONE", "HELLO", "ARM", "FIRE", "CANCEL", "PING" }
P.FLAG = { IN_COMBAT = 1, IN_INSTANCE = 2, ON_TAXI = 4, DEAD = 8, LOCAL_PLAYBACK = 16, DROP_ACTIVE = 32, TEST = 64 }

local floor = math.floor

-- Fletcher-16 over an array of byte values.
function P.Fletcher16Bytes(bytes)
  local s1, s2 = 0, 0
  for i = 1, #bytes do
    s1 = (s1 + bytes[i]) % 255
    s2 = (s2 + s1) % 255
  end
  return s2 * 256 + s1
end

-- Fletcher-16 over a string's bytes.
function P.Fletcher16String(s)
  local s1, s2 = 0, 0
  for i = 1, #s do
    s1 = (s1 + string.byte(s, i)) % 255
    s2 = (s2 + s1) % 255
  end
  return s2 * 256 + s1
end

-- Returns true if flag bit `f` (power of two) is set in `flags`.
function P.HasFlag(flags, f)
  return floor((flags or 0) / f) % 2 == 1
end

-- 10 payload bytes for an event table {type, seq, n, hash, flags}.
function P.EncodeBytes(ev)
  local n = floor(ev.n or 0) % 65536
  local h = floor(ev.hash or 0) % 65536
  local b = {
    P.MAGIC,
    P.VERSION * 16 + (ev.type % 16),
    floor(ev.seq or 0) % 256,
    floor(n / 256), n % 256,
    floor(h / 256), h % 256,
    floor(ev.flags or 0) % 256,
  }
  local f = P.Fletcher16Bytes(b)
  b[9] = floor(f / 256)
  b[10] = f % 256
  return b
end

-- Full strip as an array of {r,g,b} with channel values 0 or 1 (header first).
function P.EncodeCells(ev)
  local bytes = P.EncodeBytes(ev)
  local bits = {}
  for i = 1, #bytes do
    local v = bytes[i]
    for shift = 7, 0, -1 do
      local p = 2 ^ shift
      bits[#bits + 1] = floor(v / p) % 2
    end
  end
  bits[#bits + 1] = 0 -- pad to 81 bits
  local cells = {}
  for i = 1, #P.HEADER do
    cells[i] = { P.HEADER[i][1], P.HEADER[i][2], P.HEADER[i][3] }
  end
  for c = 0, P.PAYLOAD_CELLS - 1 do
    cells[#cells + 1] = { bits[c * 3 + 1], bits[c * 3 + 2], bits[c * 3 + 3] }
  end
  return cells
end

-- Chat-log id sanitizer: every char outside [A-Za-z0-9:_.-] -> "_".
function P.SanitizeId(id)
  if id == nil or id == "" then return "-" end
  local s = string.gsub(id, "[^%w:_%.%-]", "_")
  return s
end

-- "NDROP1 <seq> <type> <n> <hash> <flags> <id> <crc>"
function P.FormatChatLine(ev)
  local body = string.format("NDROP1 %d %d %d %d %d %s",
    floor(ev.seq or 0) % 256, ev.type, floor(ev.n or 0) % 65536, floor(ev.hash or 0) % 65536,
    floor(ev.flags or 0) % 256, P.SanitizeId(ev.id))
  return string.format("%s %04x", body, P.Fletcher16String(body))
end

-- Padding line of exactly `len` characters (default 900).
function P.PadLine(seq, len)
  len = len or 900
  local head = string.format("NDPAD %d ", floor(seq or 0) % 256)
  return head .. string.rep(".", len - #head)
end

function P.PadLineCount(padBytes, len)
  len = len or 900
  if not padBytes or padBytes <= 0 then return 0 end
  return math.ceil(padBytes / len)
end

return P
