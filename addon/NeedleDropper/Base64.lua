local ADDON, ns = ...
--[[ NeedleDropper Base64: RFC 4648 base64 and base64url in pure Lua 5.1 arithmetic.

Used for in-game share codes (JSON wrapped in a copy/paste-safe string) and anything else
that needs bytes as text. No library beyond the Lua 5.1 string/table functions is used, so
the file behaves identically in the WoW client, in lupa's Lua 5.1 and in a stock lua5.1.

API (both functions return nil plus an error message instead of raising):

  ns.Base64.Encode(bytes, urlsafe, pad) -> text | nil, err
      bytes    any Lua string (binary safe: NUL and bytes 128-255 are fine).
      urlsafe  true  -> URL/filename-safe alphabet  ( '-' and '_' for values 62 and 63 )
               false -> standard alphabet           ( '+' and '/' )        [default]
      pad      true -> append '=' padding to a multiple of 4 characters; false -> none.
               nil  -> padded for the standard alphabet, unpadded for urlsafe (the
                       usual base64url convention for tokens and share codes).
      The output never contains whitespace or line breaks.

  ns.Base64.Decode(text) -> bytes | nil, err
      Accepts the standard and the URL-safe alphabet (even mixed in one string), with or
      without padding, and ignores ASCII whitespace anywhere (space, \t, \n, \r, \f, \v), so
      text wrapped by chat boxes, mail or MIME line breaks decodes fine. Rejected, with a
      message saying why (and where, for bad characters):
        * any other character (including non-ASCII bytes);
        * '=' anywhere but at the very end, more than two '=', or padding that does not
          match the length (padding is optional, but when present it must be complete);
        * a length that leaves a single dangling character (no byte can come from it);
        * non-canonical input whose unused low bits in the final character are not zero
          (every conforming encoder writes zeros there; RFC 4648 section 3.5 allows a
          decoder to reject such input, and it catches some corrupted share codes).

  A WoW 12.x "secret value" (issecretvalue) is refused, never inspected.

Speed: whole 3-byte groups are converted with integer arithmetic ((x - x % m) / m instead
of math.floor, which would cost a C call) and 64-entry lookup tables; byte extraction and
string building work on 12-byte / 16-character blocks so the C-function overhead is
amortised, and pieces are joined once with table.concat. Decode validates with a single
pattern scan unless something other than alphabet characters is present. 64 KB encodes in
about 1.5 ms and decodes in about 3 ms under lupa's Lua 5.1 (budget: < 50 ms each,
enforced by tests/test_lua_codecs.py).
]]
ns = ns or {}

local Base64 = {}
ns.Base64 = Base64

local byte, char, find, format, gsub, sub = string.byte, string.char, string.find, string.format, string.gsub, string.sub
local concat = table.concat
local type, pcall = type, pcall

Base64.STANDARD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
Base64.URLSAFE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

-- Sextet value (0..63) -> character code, one table per alphabet.
local ENC_STD, ENC_URL = {}, {}
-- Character code -> sextet value for BOTH alphabets ('+' and '-' are 62, '/' and '_' are 63).
local DEC = {}
for v = 0, 63 do
  local s, u = byte(Base64.STANDARD_ALPHABET, v + 1), byte(Base64.URLSAFE_ALPHABET, v + 1)
  ENC_STD[v] = s
  ENC_URL[v] = u
  DEC[s] = v
  DEC[u] = v
end

-- Explicit byte ranges (not %w / %s, which follow the C locale's ctype tables).
local NON_ALPHABET = "[^A-Za-z0-9+/_%-]"
local INVALID_CHAR = "[^A-Za-z0-9+/_= \t\n\r\f\v%-]"
local WHITESPACE = "[ \t\n\r\f\v]"
local WHITESPACE_RUN = "[ \t\n\r\f\v]+"

-- True when `v` is a WoW "secret value" (12.x restricted data) that must not be touched.
-- The API is optional: absent outside the 12.x client, and any failure counts as secret.
local function isSecret(v)
  local probe = issecretvalue
  if type(probe) ~= "function" then return false end
  local ok, secret = pcall(probe, v)
  return (not ok) or (secret and true or false)
end

local function describeByte(b)
  if b >= 33 and b <= 126 then return format("'%s'", char(b)) end
  return format("byte 0x%02X", b)
end

--- Encodes a byte string as base64 (see the header for the arguments).
function Base64.Encode(bytes, urlsafe, pad)
  if type(bytes) ~= "string" then
    return nil, "Base64.Encode: expected a string, got " .. type(bytes)
  end
  if isSecret(bytes) then return nil, "Base64.Encode: refusing to encode a secret value" end
  local E = urlsafe and ENC_URL or ENC_STD
  if pad == nil then pad = not urlsafe end

  local len = #bytes
  local out, k = {}, 0
  local i = 1

  -- 12 input bytes -> 16 output characters per iteration. For each 3-byte group (a, b, c)
  -- the four sextets are: a div 4, (a mod 4)*16 + b div 16, (b mod 16)*4 + c div 64,
  -- c mod 64 -- where "n div m" is spelled (n - n % m) / m, an exact division.
  local last12 = len - 11
  while i <= last12 do
    local a1, b1, c1, a2, b2, c2, a3, b3, c3, a4, b4, c4 = byte(bytes, i, i + 11)
    local x1, y1, z1 = a1 % 4, b1 % 16, c1 % 64
    local x2, y2, z2 = a2 % 4, b2 % 16, c2 % 64
    local x3, y3, z3 = a3 % 4, b3 % 16, c3 % 64
    local x4, y4, z4 = a4 % 4, b4 % 16, c4 % 64
    k = k + 1
    out[k] = char(
      E[(a1 - x1) / 4], E[x1 * 16 + (b1 - y1) / 16], E[y1 * 4 + (c1 - z1) / 64], E[z1],
      E[(a2 - x2) / 4], E[x2 * 16 + (b2 - y2) / 16], E[y2 * 4 + (c2 - z2) / 64], E[z2],
      E[(a3 - x3) / 4], E[x3 * 16 + (b3 - y3) / 16], E[y3 * 4 + (c3 - z3) / 64], E[z3],
      E[(a4 - x4) / 4], E[x4 * 16 + (b4 - y4) / 16], E[y4 * 4 + (c4 - z4) / 64], E[z4])
    i = i + 12
  end

  -- Remaining whole 3-byte groups (at most three).
  local last3 = len - 2
  while i <= last3 do
    local a, b, c = byte(bytes, i, i + 2)
    local x, y, z = a % 4, b % 16, c % 64
    k = k + 1
    out[k] = char(E[(a - x) / 4], E[x * 16 + (b - y) / 16], E[y * 4 + (c - z) / 64], E[z])
    i = i + 3
  end

  -- Final partial group: 1 byte -> 2 characters, 2 bytes -> 3 characters (zero low bits).
  local rest = len - i + 1
  if rest == 1 then
    local a = byte(bytes, i)
    local x = a % 4
    k = k + 1
    out[k] = char(E[(a - x) / 4], E[x * 16])
    if pad then k = k + 1; out[k] = "==" end
  elseif rest == 2 then
    local a, b = byte(bytes, i, i + 1)
    local x, y = a % 4, b % 16
    k = k + 1
    out[k] = char(E[(a - x) / 4], E[x * 16 + (b - y) / 16], E[y * 4])
    if pad then k = k + 1; out[k] = "=" end
  end
  return concat(out, "", 1, k)
end

--- Decodes base64 or base64url text back to bytes (see the header for what is accepted).
function Base64.Decode(text)
  if type(text) ~= "string" then
    return nil, "Base64.Decode: expected a string, got " .. type(text)
  end
  if isSecret(text) then return nil, "Base64.Decode: refusing to decode a secret value" end

  -- One scan in the common case (nothing but alphabet characters); the slower checks for
  -- bad characters, whitespace and padding start at the first other character.
  local s = text
  local special = find(text, NON_ALPHABET)
  if special then
    local bad = find(text, INVALID_CHAR, special)
    if bad then
      return nil, format("Base64.Decode: invalid character %s at position %d", describeByte(byte(text, bad)), bad)
    end
    if find(text, WHITESPACE, special) then s = gsub(text, WHITESPACE_RUN, "") end
  end
  local len = #s

  local eq = special and find(s, "=", 1, true)
  if eq then
    if find(s, "[^=]", eq) then
      -- Report the offending character's position in the caller's (unstripped) text.
      local p = find(text, "=", 1, true)
      local q = find(text, "[^= \t\n\r\f\v]", p)
      return nil, format("Base64.Decode: unexpected character %s after '=' padding at position %d",
        describeByte(byte(text, q)), q)
    end
    -- Padding completes the last group: 2 data characters + "==" or 3 + "=" (this also
    -- implies at most two '=' and a total length that is a multiple of 4).
    local pads = len - eq + 1
    local body = eq - 1
    local r = body % 4
    if not ((r == 2 and pads == 2) or (r == 3 and pads == 1)) then
      return nil, format("Base64.Decode: incorrect padding (%d data characters followed by %d '=')", body, pads)
    end
    len = body
  end
  if len % 4 == 1 then
    return nil, format("Base64.Decode: invalid length %d (one character left over after the last 4-character group)", len)
  end

  local D = DEC
  local out, k = {}, 0
  local i = 1

  -- 16 characters -> 12 bytes per iteration. For each quad of sextets (v1, v2, v3, v4)
  -- the three bytes are: v1*4 + v2 div 16, (v2 mod 16)*16 + v3 div 4, (v3 mod 4)*64 + v4.
  local last16 = len - 15
  while i <= last16 do
    local c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12, c13, c14, c15, c16 = byte(s, i, i + 15)
    local v2, v3, v6, v7 = D[c2], D[c3], D[c6], D[c7]
    local v10, v11, v14, v15 = D[c10], D[c11], D[c14], D[c15]
    local p2, p3, p6, p7 = v2 % 16, v3 % 4, v6 % 16, v7 % 4
    local p10, p11, p14, p15 = v10 % 16, v11 % 4, v14 % 16, v15 % 4
    k = k + 1
    out[k] = char(
      D[c1] * 4 + (v2 - p2) / 16, p2 * 16 + (v3 - p3) / 4, p3 * 64 + D[c4],
      D[c5] * 4 + (v6 - p6) / 16, p6 * 16 + (v7 - p7) / 4, p7 * 64 + D[c8],
      D[c9] * 4 + (v10 - p10) / 16, p10 * 16 + (v11 - p11) / 4, p11 * 64 + D[c12],
      D[c13] * 4 + (v14 - p14) / 16, p14 * 16 + (v15 - p15) / 4, p15 * 64 + D[c16])
    i = i + 16
  end

  -- Remaining whole quads (at most three).
  local last4 = len - 3
  while i <= last4 do
    local c1, c2, c3, c4 = byte(s, i, i + 3)
    local v2, v3 = D[c2], D[c3]
    local p2, p3 = v2 % 16, v3 % 4
    k = k + 1
    out[k] = char(D[c1] * 4 + (v2 - p2) / 16, p2 * 16 + (v3 - p3) / 4, p3 * 64 + D[c4])
    i = i + 4
  end

  -- Final partial quad: 2 characters -> 1 byte, 3 characters -> 2 bytes. The unused low
  -- bits of the last character must be zero (canonical encoding).
  local rest = len - i + 1
  if rest == 2 then
    local c1, c2 = byte(s, i, i + 1)
    local v2 = D[c2]
    local p2 = v2 % 16
    if p2 ~= 0 then
      return nil, "Base64.Decode: non-canonical encoding (unused low bits of the last character are not zero)"
    end
    k = k + 1
    out[k] = char(D[c1] * 4 + (v2 - p2) / 16)
  elseif rest == 3 then
    local c1, c2, c3 = byte(s, i, i + 2)
    local v2, v3 = D[c2], D[c3]
    local p2, p3 = v2 % 16, v3 % 4
    if p3 ~= 0 then
      return nil, "Base64.Decode: non-canonical encoding (unused low bits of the last character are not zero)"
    end
    k = k + 1
    out[k] = char(D[c1] * 4 + (v2 - p2) / 16, p2 * 16 + (v3 - p3) / 4)
  end
  return concat(out, "", 1, k)
end

return Base64
