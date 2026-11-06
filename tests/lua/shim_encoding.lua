-- shim_encoding.lua -- C_EncodingUtil for the NeedleDropper tests, backed by Python's zlib.
--
-- Loaded after wow_shim.lua (it extends that shim's Enum table and SHIM.log). The Python side
-- (tests/v12_model_harness.py) sets the global SHIM_ENCODING_BRIDGE before loading this file:
--
--   SHIM_ENCODING_BRIDGE = {
--     mode = "hex" | "bytes",
--     compress = function(data, method, level) -> output | nil, message,
--     decompress = function(data, method, limit) -> output | nil, message,
--   }
--
-- "bytes": a LuaRuntime(encoding=None); Lua strings cross into Python as bytes, unchanged.
-- "hex":   the UTF-8 AddonHarness runtime, where a binary Lua string cannot cross into Python
--          intact (lupa decodes it as UTF-8), so the Lua side sends lowercase hex. Python always
--          answers with a bytes object, which lupa hands to Lua as a raw string in both modes.
--
-- Mirrors Blizzard_APIDocumentationGenerated/EncodingUtilDocumentation.lua (forever 1.60.1.70790):
--   C_EncodingUtil.CompressString(source, method = Deflate, level = Default) -> output
--   C_EncodingUtil.DecompressString(source, method = Deflate) -> output
--   Enum.CompressionMethod = { Deflate = 0, Zlib = 1, Gzip = 2 }
--   Enum.CompressionLevel  = { Default = 0, OptimizeForSpeed = 1, OptimizeForSize = 2 }
-- Both functions are documented MayReturnNothing; the real client's failure behaviour (raise or
-- return nothing) is not documented, so SHIM.encoding.failMode selects it:
--   "error" (default)  raise an error, like a failing C call
--   "nil"              return nothing
-- EncodingUtilConstantsDocumentation.lua: EncodingDecompressSizeLimit = 104857600 bytes; output
-- beyond SHIM.encoding.decompressLimit (that value by default) is a failure.
--
-- SHIM.encoding.absent = true before the addon loads makes C_EncodingUtil look missing (nil).
-- SHIM.log.encoding records every call: { op =, method =, level =, inBytes =, outBytes =, ok = }.

local SHIM = _G.SHIM
local BRIDGE = _G.SHIM_ENCODING_BRIDGE
if type(SHIM) ~= "table" then error("shim_encoding.lua: load wow_shim.lua first") end
if type(BRIDGE) ~= "table" then error("shim_encoding.lua: SHIM_ENCODING_BRIDGE is not set") end

SHIM.encoding = SHIM.encoding or {}
local E = SHIM.encoding
if E.failMode == nil then E.failMode = "error" end
if E.decompressLimit == nil then E.decompressLimit = 104857600 end
SHIM.log.encoding = SHIM.log.encoding or {}

Enum = Enum or {}
Enum.CompressionMethod = { Deflate = 0, Zlib = 1, Gzip = 2 }
Enum.CompressionLevel = { Default = 0, OptimizeForSpeed = 1, OptimizeForSize = 2 }
Enum.Base64Variant = { Standard = 0, StandardUrlSafe = 1 }

local HEX = {}
for b = 0, 255 do HEX[string.char(b)] = string.format("%02x", b) end

local function toBridge(s)
  if BRIDGE.mode == "bytes" then return s end
  return (string.gsub(s, ".", HEX))
end

local VALID_METHOD = { [0] = true, [1] = true, [2] = true }
local VALID_LEVEL = { [0] = true, [1] = true, [2] = true }

local function record(op, method, level, inBytes, out)
  local log = SHIM.log.encoding
  log[#log + 1] = { op = op, method = method, level = level, inBytes = inBytes,
                    outBytes = type(out) == "string" and #out or nil, ok = type(out) == "string" }
end

-- Failure in the selected style. Returns nothing in "nil" mode.
local function fail(fname, message)
  if E.failMode == "nil" then return end
  error(fname .. ": " .. tostring(message or "failed"), 3)
end

local function checkSource(fname, source)
  if type(source) ~= "string" then
    error(fname .. ": bad argument #1 (string expected, got " .. type(source) .. ")", 3)
  end
end

local C = {}

function C.CompressString(source, method, level)
  checkSource("CompressString", source)
  if method == nil then method = 0 end
  if level == nil then level = 0 end
  if not VALID_METHOD[method] then error("CompressString: bad argument #2 (invalid CompressionMethod)", 2) end
  if not VALID_LEVEL[level] then error("CompressString: bad argument #3 (invalid CompressionLevel)", 2) end
  local out, message = BRIDGE.compress(toBridge(source), method, level)
  record("compress", method, level, #source, out)
  if type(out) ~= "string" then return fail("CompressString", message) end
  return out
end

function C.DecompressString(source, method)
  checkSource("DecompressString", source)
  if method == nil then method = 0 end
  if not VALID_METHOD[method] then error("DecompressString: bad argument #2 (invalid CompressionMethod)", 2) end
  local out, message = BRIDGE.decompress(toBridge(source), method, E.decompressLimit)
  record("decompress", method, nil, #source, out)
  if type(out) ~= "string" then return fail("DecompressString", message) end
  return out
end

if E.absent then
  C_EncodingUtil = nil
else
  C_EncodingUtil = C
end
SHIM.EncodingUtil = C

-- Re-installs (or removes) C_EncodingUtil after load, for tests that switch it at runtime.
function SHIM.SetEncodingAvailable(on)
  if on then C_EncodingUtil = C else C_EncodingUtil = nil end
end

return C
