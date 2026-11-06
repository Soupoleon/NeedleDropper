local ADDON, ns = ...
--[[ NeedleDropper Json: deterministic, strict RFC 8259 JSON in pure Lua 5.1.

Used for in-game share codes (soundtrack sets as JSON, wrapped by ns.Base64) and for any
data that has to leave or enter the addon as text. Only the Lua 5.1 string/table/math
functions are used; text is never compiled or executed, so decoding untrusted input (a
share code pasted by another player) is safe.

API (nothing raises for bad data; failures return nil plus a message that says what and
where):

  ns.Json.Encode(value, opts) -> text | nil, err
    value: nil (-> null), boolean, finite number, string, ns.Json.null, or a table of those.
      * A table whose keys are exactly 1..n (n >= 1, no holes) is an array; any other
        non-empty table is an object. Object keys must be strings or finite numbers;
        number keys are written as JSON-formatted strings (1429 -> "1429"), like Python's
        json.dumps does for int keys. A string key that collides with a converted number
        key (["1"] and [1]) is an error.
      * Numbers: integral values up to 2^53 in magnitude are written as integers (no ".0",
        no exponent); everything else uses the shortest of %.14g .. %.17g that reads back
        to the identical double, so every number round-trips exactly. NaN and +/-inf are
        rejected (JSON cannot represent them). Negative zero is written as -0.
      * Strings must be valid UTF-8 and pass through unchanged, apart from the escapes
        JSON requires: \" \\ \b \f \n \r \t and \u00XX for the other control characters
        (the same spellings as Python's json.dumps).
      * Cycles are detected and reported; a table may still appear twice in the value as
        long as it never contains itself.
    opts (all optional):
      sortKeys    = true      object keys in byte order (= Unicode code point order, the
                              same order as Python's sort_keys=True), so equal data always
                              encodes to identical text. false keeps pairs() order.
      emptyTable  = "array"   how an empty table is written: "array" -> [], "object" -> {}.
      indent      = nil       nil/false: compact (",", ":"); a number of spaces or a string:
                              pretty-printed exactly like Python's json.dumps(indent=...),
                              i.e. one item per line and ": " after keys.
      asciiOnly   = false     true: escape every non-ASCII character as \uXXXX (surrogate
                              pairs above U+FFFF) and DEL as \u007f, like ensure_ascii=True.
      replaceInvalid = false  true: substitute U+FFFD for each maximal invalid UTF-8 byte
                              sequence (the same result as Python's errors="replace")
                              instead of failing.
      maxDepth    = 64        maximum nesting of arrays/objects (the top level counts as 1).

  ns.Json.Decode(text, opts) -> value | nil, err
    Strict RFC 8259: any JSON value at the top level, surrounded only by JSON whitespace
    (space, \t, \n, \r); no comments, trailing commas, single quotes, NaN/Infinity, leading
    zeros, "+1", ".5" or "1."; unescaped control characters, invalid escapes, unpaired
    surrogates and invalid UTF-8 are errors. A leading UTF-8 byte order mark is skipped
    (RFC 8259 section 8.1 allows ignoring it).
      * Objects and arrays become Lua tables (arrays with indices 1..n); numbers become
        Lua numbers (doubles: integers beyond 2^53 lose precision, and a number too large
        for a double is an error); \uXXXX escapes, surrogate pairs included, become UTF-8.
      * JSON null becomes opts.null, which is nil by default: null members are left out of
        their object, and a null array element leaves a hole at its index. Pass
        { null = ns.Json.null } to keep them (Encode writes ns.Json.null back as null).
        Decoding the text "null" therefore returns nil WITHOUT an error message.
      * Duplicate object keys: the last one wins (as in Python's json module).
      * Empty objects and empty arrays both become an empty table.
    opts (all optional):
      maxBytes = 1048576      inputs longer than this are rejected before parsing.
      maxDepth = 64           deeper nesting is rejected (the top level counts as 1).
      null     = nil          the value used for JSON null.
    Error messages look like
      Json.Decode: expected ',' or ']' after array element, found character '}' at line 3, column 7 (byte 41)
    (lines and columns are 1-based and counted in bytes). Encode errors name the place:
      Json.Encode: cannot encode NaN (JSON has no non-finite numbers) at $.songs[3].startMs
    (array indices in these paths are the Lua indices, 1-based).

  A WoW 12.x "secret value" (issecretvalue) is refused by both functions, never inspected.

  ns.Json.null   read-only sentinel standing for JSON null inside tables.

Lua 5.1 notes: no locale-dependent pattern classes are used (%a, %d, %s, %w follow the C
library's ctype tables; explicit byte ranges do not), string sorting does not use the
locale-aware "<" operator, and a client whose C locale writes decimal commas still gets
"1.5". "%d" is never used for values that may exceed 2^31 (it truncates on Windows).
]]
ns = ns or {}

local Json = {}
ns.Json = Json

local byte, char, find, format, gsub, match, rep, sub =
  string.byte, string.char, string.find, string.format, string.gsub, string.match, string.rep, string.sub
local concat, sort = table.concat, table.sort
local type, next, pcall, error, tostring, tonumber, rawequal, setmetatable =
  type, next, pcall, error, tostring, tonumber, rawequal, setmetatable
local floor, huge = math.floor, math.huge

local DEFAULT_MAX_BYTES = 1048576
local DEFAULT_MAX_DEPTH = 64
Json.DEFAULT_MAX_BYTES = DEFAULT_MAX_BYTES
Json.DEFAULT_MAX_DEPTH = DEFAULT_MAX_DEPTH

-- The JSON null sentinel: a unique, read-only, metatable-locked table.
local NULL = setmetatable({}, {
  __newindex = function() error("Json.null is read-only", 2) end,
  __tostring = function() return "Json.null" end,
  __metatable = "Json.null",
})
Json.null = NULL

-- The C library's decimal separator, normally "." (a "1,5" locale is undone below).
local DECIMAL = match(format("%.1f", 0.5), "^0(.-)5$") or "."
local DECIMAL_FIX = DECIMAL ~= "."
local DECIMAL_PATTERN = gsub(DECIMAL, "[%^%$%(%)%%%.%[%]%*%+%-%?]", "%%%0")

local MAX_EXACT_INTEGER = 9007199254740992 -- 2^53

local HIGH_BYTE = "[\128-\255]"
local REPLACEMENT_CHAR = "\239\191\189" -- U+FFFD

-- ------------------------------------------------------------------ UTF-8 ----

-- Length of the well-formed UTF-8 sequence starting at byte i of s (1 for ASCII), or nil
-- plus the length of the maximal ill-formed subpart there (Unicode 15, section 3.9, U+FFFD
-- substitution of maximal subparts -- the rule Python's decoder follows too).
local function sequenceLength(s, i)
  local c = byte(s, i)
  if c < 128 then return 1 end
  if c < 194 then return nil, 1 end -- continuation byte, or C0/C1 (always overlong)
  if c < 224 then
    local c2 = byte(s, i + 1)
    if c2 and c2 >= 128 and c2 <= 191 then return 2 end
    return nil, 1
  end
  if c < 240 then
    local lo, hi = 128, 191
    if c == 224 then lo = 160 elseif c == 237 then hi = 159 end -- overlongs / surrogates
    local c2 = byte(s, i + 1)
    if not c2 or c2 < lo or c2 > hi then return nil, 1 end
    local c3 = byte(s, i + 2)
    if not c3 or c3 < 128 or c3 > 191 then return nil, 2 end
    return 3
  end
  if c < 245 then
    local lo, hi = 128, 191
    if c == 240 then lo = 144 elseif c == 244 then hi = 143 end -- overlongs / > U+10FFFF
    local c2 = byte(s, i + 1)
    if not c2 or c2 < lo or c2 > hi then return nil, 1 end
    local c3 = byte(s, i + 2)
    if not c3 or c3 < 128 or c3 > 191 then return nil, 2 end
    local c4 = byte(s, i + 3)
    if not c4 or c4 < 128 or c4 > 191 then return nil, 3 end
    return 4
  end
  return nil, 1 -- F5..FF never appear in UTF-8
end

-- UTF-8 bytes for a Unicode scalar value (0..0x10FFFF, surrogates excluded by callers).
local function encodeCodepoint(cp)
  if cp < 128 then return char(cp) end
  if cp < 2048 then
    local c2 = cp % 64
    return char(192 + (cp - c2) / 64, 128 + c2)
  end
  if cp < 65536 then
    local c3 = cp % 64
    cp = (cp - c3) / 64
    local c2 = cp % 64
    return char(224 + (cp - c2) / 64, 128 + c2, 128 + c3)
  end
  local c4 = cp % 64
  cp = (cp - c4) / 64
  local c3 = cp % 64
  cp = (cp - c3) / 64
  local c2 = cp % 64
  return char(240 + (cp - c2) / 64, 128 + c2, 128 + c3, 128 + c4)
end

-- "\uXXXX" (or a surrogate pair) for one well-formed multi-byte UTF-8 sequence.
local function codepointEscape(seq)
  local a, b, c, d = byte(seq, 1, 4)
  local cp
  if a < 224 then
    cp = (a - 192) * 64 + (b - 128)
  elseif a < 240 then
    cp = ((a - 224) * 64 + (b - 128)) * 64 + (c - 128)
  else
    cp = (((a - 240) * 64 + (b - 128)) * 64 + (c - 128)) * 64 + (d - 128)
  end
  if cp < 65536 then return format("\\u%04x", cp) end
  cp = cp - 65536
  local low = cp % 1024
  return format("\\u%04x\\u%04x", 55296 + (cp - low) / 1024, 56320 + low)
end
local MULTIBYTE_SEQUENCE = "[\194-\244][\128-\191]*"

-- ---------------------------------------------------------------- numbers ----

-- JSON text for a finite number (nil for NaN/inf). See the header for the format.
local function numberToJson(x)
  if x ~= x or x == huge or x == -huge then return nil end
  if x % 1 == 0 and x <= MAX_EXACT_INTEGER and x >= -MAX_EXACT_INTEGER then
    if x == 0 then
      if 1 / x < 0 then return "-0" end
      return "0"
    end
    return format("%.0f", x)
  end
  local s = format("%.14g", x)
  if tonumber(s) ~= x then
    s = format("%.15g", x)
    if tonumber(s) ~= x then
      s = format("%.16g", x)
      if tonumber(s) ~= x then s = format("%.17g", x) end
    end
  end
  if DECIMAL_FIX then s = gsub(s, DECIMAL_PATTERN, ".") end
  return s
end

-- ---------------------------------------------------------------- encoder ----

-- Escapes JSON requires inside strings, spelled like Python's json.dumps.
local ESCAPE = {
  ['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f",
  ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t", ["\127"] = "\\u007f",
}
for c = 0, 31 do
  local ch = char(c)
  if not ESCAPE[ch] then ESCAPE[ch] = format("\\u%04x", c) end
end
local SPECIAL = '[%z\1-\31"\\]'                         -- must be escaped
local SPECIAL_ASCII = '[%z\1-\31"\\\127]'               -- ... in ASCII-only mode
local NEEDS_WORK = '[%z\1-\31"\\\128-\255]'             -- escapes or UTF-8 checks
local NEEDS_WORK_ASCII = '[%z\1-\31"\\\127-\255]'

-- Per-call encoder state. Encode never calls back into user code (no metamethods are
-- triggered: tables are walked with next() and indexed only at keys known to hold
-- values), so one module-level set of upvalues is safe and faster than a state table.
local buf, bn           -- output pieces and their count
local visiting          -- [table] = true while that table is being encoded
local pathKeys          -- pathKeys[level] = index/key being encoded at that nesting level
local gSort, gEmpty, gKeyEnd, gIndent, gNewlines, gAscii, gReplace, gMaxDepth, gSecret
local gSpecial, gNeedsWork

local IDENTIFIER = "^[A-Za-z_][A-Za-z0-9_]*$"

-- A string for an error message: escaped, quoted, cut (on a character boundary) at 60 bytes.
local function messageQuote(s)
  local cut = #s > 60
  if cut then
    local e = 60
    while e > 0 and (byte(s, e + 1) or 0) >= 128 and (byte(s, e + 1) or 0) < 192 do e = e - 1 end
    s = sub(s, 1, e)
  end
  return '"' .. gsub(s, SPECIAL, ESCAPE) .. (cut and '..."' or '"')
end

-- "$.songs[3].title"-style location of the value at nesting `depth` (array indices are the
-- Lua indices, 1-based).
local function pathString(depth)
  local parts = { "$" }
  for d = 1, depth do
    local key = pathKeys[d]
    if type(key) == "number" then
      parts[d + 1] = "[" .. (numberToJson(key) or tostring(key)) .. "]"
    elseif type(key) == "string" and find(key, IDENTIFIER) then
      parts[d + 1] = "." .. key
    elseif type(key) == "string" then
      parts[d + 1] = "[" .. messageQuote(key) .. "]"
    else
      parts[d + 1] = "[?]"
    end
  end
  return concat(parts)
end

local function encodeFail(depth, msg)
  error({ jsonError = msg .. " at " .. pathString(depth) }, 0)
end

-- Validates the UTF-8 in s; returns s, or (opts.replaceInvalid) a repaired copy.
local function checkStringEncoding(s, depth, what)
  local i = find(s, HIGH_BYTE)
  local out, k, last
  while i do
    local n, bad = sequenceLength(s, i)
    if n then
      i = find(s, HIGH_BYTE, i + n)
    else
      if not gReplace then
        encodeFail(depth, format("invalid UTF-8 at byte %d of %s", i, what))
      end
      if not out then out, k, last = {}, 0, 1 end
      out[k + 1] = sub(s, last, i - 1)
      out[k + 2] = REPLACEMENT_CHAR
      k = k + 2
      last = i + bad
      i = find(s, HIGH_BYTE, last)
    end
  end
  if out then
    out[k + 1] = sub(s, last)
    return concat(out, "", 1, k + 1)
  end
  return s
end

-- Escaped body of a JSON string (without the quotes).
local function escapeString(s, depth, what)
  if find(s, HIGH_BYTE) then s = checkStringEncoding(s, depth, what) end
  if find(s, gSpecial) then s = gsub(s, gSpecial, ESCAPE) end
  if gAscii and find(s, HIGH_BYTE) then s = gsub(s, MULTIBYTE_SEQUENCE, codepointEscape) end
  return s
end

local function newline(level)
  local s = gNewlines[level]
  if not s then
    s = "\n" .. rep(gIndent, level)
    gNewlines[level] = s
  end
  return s
end

-- Byte-wise string order (strcmp on unsigned bytes = Unicode code point order for UTF-8).
local function byteLess(a, b)
  local la, lb = #a, #b
  local n = la < lb and la or lb
  for i = 1, n do
    local x, y = byte(a, i), byte(b, i)
    if x ~= y then return x < y end
  end
  return la < lb
end

local encodeTable -- forward declaration (mutual recursion with encodeValue)

-- Appends the JSON for v. `depth` = number of containers enclosing v (0 at the top).
local function encodeValue(v, depth)
  if gSecret and gSecret(v) then encodeFail(depth, "cannot encode a secret value") end
  local tv = type(v)
  if tv == "string" then
    if find(v, gNeedsWork) then v = escapeString(v, depth, "a string") end
    buf[bn + 1] = '"'
    buf[bn + 2] = v
    buf[bn + 3] = '"'
    bn = bn + 3
  elseif tv == "number" then
    local s = numberToJson(v)
    if not s then
      encodeFail(depth, "cannot encode " .. (v ~= v and "NaN" or (v > 0 and "inf" or "-inf"))
        .. " (JSON has no non-finite numbers)")
    end
    bn = bn + 1
    buf[bn] = s
  elseif tv == "boolean" then
    bn = bn + 1
    buf[bn] = v and "true" or "false"
  elseif tv == "table" then
    if rawequal(v, NULL) then
      bn = bn + 1
      buf[bn] = "null"
    else
      encodeTable(v, depth)
    end
  elseif tv == "nil" then
    bn = bn + 1
    buf[bn] = "null"
  else
    encodeFail(depth, "cannot encode a value of type " .. tv)
  end
end

encodeTable = function(t, depth)
  local level = depth + 1
  if level > gMaxDepth then encodeFail(depth, format("nesting deeper than the %d-level limit", gMaxDepth)) end
  if visiting[t] then encodeFail(depth, "cycle detected (a table contains itself)") end

  -- Array iff the keys are exactly 1..n: n distinct positive integers whose maximum is n.
  local n, maxIndex, isArray = 0, 0, true
  for k in next, t do
    n = n + 1
    if isArray then
      if gSecret and gSecret(k) then encodeFail(depth, "cannot encode a secret table key") end
      if type(k) == "number" and k >= 1 and k % 1 == 0 then
        if k > maxIndex then maxIndex = k end
      else
        isArray = false
      end
    end
  end
  if n == 0 then
    bn = bn + 1
    buf[bn] = gEmpty
    return
  end

  visiting[t] = true
  local indent = gIndent
  if isArray and maxIndex == n then
    bn = bn + 1
    buf[bn] = "["
    for i = 1, n do
      if i > 1 then
        bn = bn + 1
        buf[bn] = ","
      end
      if indent then
        bn = bn + 1
        buf[bn] = newline(level)
      end
      pathKeys[level] = i
      encodeValue(t[i], level)
    end
    if indent then
      bn = bn + 1
      buf[bn] = newline(depth)
    end
    bn = bn + 1
    buf[bn] = "]"
  else
    -- Object: collect key strings (number keys converted), then emit in sorted order.
    local keys, numberKeys, kn = {}, nil, 0
    for k in next, t do
      if gSecret and gSecret(k) then encodeFail(depth, "cannot encode a secret table key") end
      local tk = type(k)
      if tk == "string" then
        kn = kn + 1
        keys[kn] = k
      elseif tk == "number" then
        local ks = numberToJson(k)
        if not ks then encodeFail(depth, "cannot encode a non-finite number as an object key") end
        numberKeys = numberKeys or {}
        numberKeys[ks] = k
        kn = kn + 1
        keys[kn] = ks
      else
        encodeFail(depth, "cannot encode an object key of type " .. tk .. " (keys must be strings or numbers)")
      end
    end
    if numberKeys then
      for k in next, t do
        if type(k) == "string" and numberKeys[k] ~= nil then
          encodeFail(depth, "duplicate object key " .. messageQuote(k)
            .. " (a string key and a number key both become this key)")
        end
      end
    end
    if gSort then sort(keys, byteLess) end

    local keyEnd = gKeyEnd -- closing quote of the key plus the name separator
    bn = bn + 1
    buf[bn] = "{"
    for i = 1, kn do
      local ks = keys[i]
      local k = ks
      if numberKeys then
        local original = numberKeys[ks]
        if original ~= nil then k = original end
      end
      pathKeys[level] = k
      if i > 1 then
        bn = bn + 1
        buf[bn] = ","
      end
      if indent then
        bn = bn + 1
        buf[bn] = newline(level)
      end
      if find(ks, gNeedsWork) then ks = escapeString(ks, level, "an object key") end
      buf[bn + 1] = '"'
      buf[bn + 2] = ks
      buf[bn + 3] = keyEnd
      bn = bn + 3
      encodeValue(t[k], level)
    end
    if indent then
      bn = bn + 1
      buf[bn] = newline(depth)
    end
    bn = bn + 1
    buf[bn] = "}"
  end
  visiting[t] = nil
end

local function encodeRoot(value)
  encodeValue(value, 0)
  return concat(buf, "", 1, bn)
end

local function errorText(prefix, err)
  if type(err) == "table" and err.jsonError then return prefix .. ": " .. err.jsonError end
  return prefix .. ": " .. tostring(err)
end

--- Encodes a Lua value as JSON text (see the header for the rules and options).
function Json.Encode(value, opts)
  local sortKeys, empty, indent, ascii, replace, maxDepth = true, "[]", nil, false, false, DEFAULT_MAX_DEPTH
  if opts ~= nil then
    if type(opts) ~= "table" then return nil, "Json.Encode: opts must be a table" end
    if opts.sortKeys == false then sortKeys = false end
    local e = opts.emptyTable
    if e == "object" then
      empty = "{}"
    elseif e ~= nil and e ~= "array" then
      return nil, 'Json.Encode: opts.emptyTable must be "array" or "object"'
    end
    local ind = opts.indent
    if type(ind) == "number" then
      indent = ind >= 1 and rep(" ", floor(ind)) or ""
    elseif type(ind) == "string" then
      indent = ind
    elseif ind == true then
      indent = "  "
    elseif ind ~= nil and ind ~= false then
      return nil, "Json.Encode: opts.indent must be a number or a string"
    end
    ascii = opts.asciiOnly and true or false
    replace = opts.replaceInvalid and true or false
    if opts.maxDepth ~= nil then
      if type(opts.maxDepth) ~= "number" or not (opts.maxDepth >= 1) then
        return nil, "Json.Encode: opts.maxDepth must be a number >= 1"
      end
      maxDepth = floor(opts.maxDepth)
    end
  end

  local probe = issecretvalue -- WoW 12.x; absent elsewhere
  gSecret = type(probe) == "function" and probe or nil
  gSort, gEmpty, gAscii, gReplace, gMaxDepth = sortKeys, empty, ascii, replace, maxDepth
  gIndent = indent
  gKeyEnd = indent and '": ' or '":'
  gNewlines = indent and {} or nil
  gSpecial = ascii and SPECIAL_ASCII or SPECIAL
  gNeedsWork = ascii and NEEDS_WORK_ASCII or NEEDS_WORK
  buf, bn, visiting, pathKeys = {}, 0, {}, {}

  local ok, result = pcall(encodeRoot, value)

  buf, visiting, pathKeys, gNewlines, gSecret = nil, nil, nil, nil, nil
  if not ok then return nil, errorText("Json.Encode", result) end
  return result
end

-- ---------------------------------------------------------------- decoder ----

-- Per-call decoder state (Decode never calls user code either).
local gText, gNull, gDepthLimit

local STRING_STOP = '["\\%z\1-\31]'   -- end of a run of plain string characters
local WHITESPACE_RUN = "^[ \t\n\r]+"  -- the four JSON whitespace characters only
local DIGITS = "^[0-9]+"
local INTEGER_PART = "^%-?[0-9]+"
local EXPONENT = "^[%+%-]?[0-9]+"
local HEX4 = "^[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]"
local UNESCAPE = {
  [34] = '"', [92] = "\\", [47] = "/", [98] = "\b", [102] = "\f", [110] = "\n", [114] = "\r", [116] = "\t",
}

local function decodeFail(pos, msg)
  error({ jsonError = msg, jsonPos = pos }, 0)
end

local function describeByte(c)
  if c == nil then return "end of input" end
  if c >= 33 and c <= 126 then return format("character '%s'", char(c)) end
  if c == 32 then return "space" end
  return format("byte 0x%02X", c)
end

local function skipWhitespace(pos)
  local c = byte(gText, pos)
  if c == 32 or c == 10 or c == 13 or c == 9 then
    local _, e = find(gText, WHITESPACE_RUN, pos)
    return e + 1
  end
  return pos
end

-- Fails unless s (which starts at byte `base` of the input) is well-formed UTF-8.
local function checkDecodedEncoding(s, base)
  local i = find(s, HIGH_BYTE)
  while i do
    local n = sequenceLength(s, i)
    if not n then decodeFail(base + i - 1, "invalid UTF-8 in string") end
    i = find(s, HIGH_BYTE, i + n)
  end
end

local function readHex4(at, escapePos)
  if not find(gText, HEX4, at) then
    decodeFail(escapePos, "invalid \\u escape (expected 4 hex digits)")
  end
  return tonumber(sub(gText, at, at + 3), 16)
end

-- Code point and next position for the \u escape whose backslash is at j.
local function readUnicodeEscape(j)
  local cp = readHex4(j + 2, j)
  if cp >= 55296 and cp <= 56319 then -- high surrogate: a low one must follow
    if sub(gText, j + 6, j + 7) ~= "\\u" then
      decodeFail(j, "unpaired surrogate in \\u escape (high surrogate not followed by a low one)")
    end
    local low = readHex4(j + 8, j + 6)
    if low < 56320 or low > 57343 then
      decodeFail(j + 6, "invalid surrogate pair in \\u escape (expected a low surrogate)")
    end
    return 65536 + (cp - 55296) * 1024 + (low - 56320), j + 12
  elseif cp >= 56320 and cp <= 57343 then
    decodeFail(j, "unpaired surrogate in \\u escape (low surrogate without a high one)")
  end
  return cp, j + 6
end

-- String starting with the quote at pos -> Lua string, position after the closing quote.
local function parseString(pos)
  local text = gText
  local i = pos + 1
  local j = find(text, STRING_STOP, i)
  if not j then decodeFail(pos, "unterminated string") end
  if byte(text, j) == 34 then -- fast path: no escapes
    local s = sub(text, i, j - 1)
    if find(s, HIGH_BYTE) then checkDecodedEncoding(s, i) end
    return s, j + 1
  end
  local parts, np = {}, 0
  while true do
    local c = byte(text, j)
    if j > i then
      local chunk = sub(text, i, j - 1)
      if find(chunk, HIGH_BYTE) then checkDecodedEncoding(chunk, i) end
      np = np + 1
      parts[np] = chunk
    end
    if c == 34 then
      return concat(parts, "", 1, np), j + 1
    elseif c == 92 then
      local e = byte(text, j + 1)
      if e == 117 then
        local cp
        cp, i = readUnicodeEscape(j)
        np = np + 1
        parts[np] = encodeCodepoint(cp)
      else
        local r = UNESCAPE[e]
        if not r then
          if e == nil then decodeFail(pos, "unterminated string") end
          decodeFail(j, "invalid escape sequence (backslash followed by " .. describeByte(e) .. ")")
        end
        np = np + 1
        parts[np] = r
        i = j + 2
      end
    else
      decodeFail(j, format("unescaped control character (byte 0x%02X) in string", c))
    end
    j = find(text, STRING_STOP, i)
    if not j then decodeFail(pos, "unterminated string") end
  end
end

local function parseNumber(pos)
  local text = gText
  local _, e = find(text, INTEGER_PART, pos)
  if not e then decodeFail(pos, "invalid number (expected a digit after '-')") end
  local first = byte(text, pos) == 45 and pos + 1 or pos
  if e > first and byte(text, first) == 48 then
    decodeFail(first, "invalid number (leading zeros are not allowed)")
  end
  local c = byte(text, e + 1)
  if c == 46 then -- fraction
    local _, f = find(text, DIGITS, e + 2)
    if not f then decodeFail(e + 2, "invalid number (expected a digit after '.')") end
    e = f
    c = byte(text, e + 1)
  end
  if c == 101 or c == 69 then -- exponent
    local _, f = find(text, EXPONENT, e + 2)
    if not f then decodeFail(e + 2, "invalid number (expected a digit in the exponent)") end
    e = f
  end
  local s = sub(text, pos, e)
  if DECIMAL_FIX then s = gsub(s, "%.", DECIMAL) end
  local v = tonumber(s)
  if v == nil or v ~= v or v == huge or v == -huge then
    decodeFail(pos, "number out of range (too large for a double)")
  end
  return v, e + 1
end

local parseValue -- forward declaration

local function parseArray(pos, depth)
  if depth > gDepthLimit then decodeFail(pos, format("nesting deeper than the %d-level limit", gDepthLimit)) end
  local arr, n = {}, 0
  pos = skipWhitespace(pos + 1)
  if byte(gText, pos) == 93 then return arr, pos + 1 end
  while true do
    local v
    v, pos = parseValue(pos, depth)
    n = n + 1
    arr[n] = v -- a nil (null) leaves a hole but keeps later elements at their index
    pos = skipWhitespace(pos)
    local c = byte(gText, pos)
    if c == 44 then
      pos = skipWhitespace(pos + 1)
      if byte(gText, pos) == 93 then decodeFail(pos, "trailing comma in array") end
    elseif c == 93 then
      return arr, pos + 1
    elseif c == nil then
      decodeFail(pos, "unexpected end of input (unclosed array)")
    else
      decodeFail(pos, "expected ',' or ']' after array element, found " .. describeByte(c))
    end
  end
end

local function parseObject(pos, depth)
  if depth > gDepthLimit then decodeFail(pos, format("nesting deeper than the %d-level limit", gDepthLimit)) end
  local obj = {}
  pos = skipWhitespace(pos + 1)
  local c = byte(gText, pos)
  if c == 125 then return obj, pos + 1 end
  while true do
    if c ~= 34 then
      if c == nil then decodeFail(pos, "unexpected end of input (unclosed object)") end
      decodeFail(pos, "expected a string key in object, found " .. describeByte(c))
    end
    local key, v
    key, pos = parseString(pos)
    pos = skipWhitespace(pos)
    c = byte(gText, pos)
    if c ~= 58 then decodeFail(pos, "expected ':' after object key, found " .. describeByte(c)) end
    pos = skipWhitespace(pos + 1)
    v, pos = parseValue(pos, depth)
    obj[key] = v
    pos = skipWhitespace(pos)
    c = byte(gText, pos)
    if c == 44 then
      pos = skipWhitespace(pos + 1)
      c = byte(gText, pos)
      if c == 125 then decodeFail(pos, "trailing comma in object") end
    elseif c == 125 then
      return obj, pos + 1
    elseif c == nil then
      decodeFail(pos, "unexpected end of input (unclosed object)")
    else
      decodeFail(pos, "expected ',' or '}' after object member, found " .. describeByte(c))
    end
  end
end

-- `depth` = number of containers enclosing the value (0 at the top level).
parseValue = function(pos, depth)
  local c = byte(gText, pos)
  if c == 34 then return parseString(pos) end
  if c == 123 then return parseObject(pos, depth + 1) end
  if c == 91 then return parseArray(pos, depth + 1) end
  if c == nil then decodeFail(pos, "unexpected end of input (expected a value)") end
  if c == 45 or (c >= 48 and c <= 57) then return parseNumber(pos) end
  if c == 116 then
    if sub(gText, pos, pos + 3) == "true" then return true, pos + 4 end
    decodeFail(pos, "invalid literal (expected true)")
  elseif c == 102 then
    if sub(gText, pos, pos + 4) == "false" then return false, pos + 5 end
    decodeFail(pos, "invalid literal (expected false)")
  elseif c == 110 then
    if sub(gText, pos, pos + 3) == "null" then return gNull, pos + 4 end
    decodeFail(pos, "invalid literal (expected null)")
  end
  decodeFail(pos, "unexpected " .. describeByte(c) .. " (expected a value)")
end

local function decodeRoot()
  local pos = 1
  if sub(gText, 1, 3) == "\239\187\191" then pos = 4 end -- UTF-8 byte order mark
  pos = skipWhitespace(pos)
  local value
  value, pos = parseValue(pos, 0)
  pos = skipWhitespace(pos)
  if pos <= #gText then
    decodeFail(pos, "unexpected " .. describeByte(byte(gText, pos)) .. " after the JSON value")
  end
  return value
end

-- 1-based line and column (in bytes) of byte `pos` in text.
local function lineAndColumn(text, pos)
  local line, lineStart = 1, 1
  local nl = find(text, "\n", 1, true)
  while nl and nl < pos do
    line = line + 1
    lineStart = nl + 1
    nl = find(text, "\n", lineStart, true)
  end
  return line, pos - lineStart + 1
end

--- Decodes JSON text into Lua values (see the header for the rules and options).
function Json.Decode(text, opts)
  if type(text) ~= "string" then return nil, "Json.Decode: expected a string, got " .. type(text) end
  local probe = issecretvalue -- WoW 12.x; absent elsewhere
  if type(probe) == "function" then
    local ok, secret = pcall(probe, text)
    if not ok or secret then return nil, "Json.Decode: refusing to decode a secret value" end
  end
  local maxBytes, maxDepth, null = DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH, nil
  if opts ~= nil then
    if type(opts) ~= "table" then return nil, "Json.Decode: opts must be a table" end
    -- (written as "not (x >= n)" so that NaN is rejected too)
    if opts.maxBytes ~= nil then
      if type(opts.maxBytes) ~= "number" or not (opts.maxBytes >= 0) then
        return nil, "Json.Decode: opts.maxBytes must be a number >= 0"
      end
      maxBytes = opts.maxBytes
    end
    if opts.maxDepth ~= nil then
      if type(opts.maxDepth) ~= "number" or not (opts.maxDepth >= 1) then
        return nil, "Json.Decode: opts.maxDepth must be a number >= 1"
      end
      maxDepth = floor(opts.maxDepth)
    end
    null = opts.null
  end
  if #text > maxBytes then
    return nil, format("Json.Decode: input is %d bytes, over the limit of %.0f bytes", #text, maxBytes)
  end

  gText, gNull, gDepthLimit = text, null, maxDepth
  local ok, result = pcall(decodeRoot)
  gText, gNull = nil, nil

  if ok then return result end
  if type(result) == "table" and result.jsonPos then
    local line, col = lineAndColumn(text, result.jsonPos)
    return nil, format("Json.Decode: %s at line %d, column %d (byte %d)", result.jsonError, line, col, result.jsonPos)
  end
  return nil, errorText("Json.Decode", result)
end

return Json
