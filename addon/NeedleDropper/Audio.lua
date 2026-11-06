local ADDON, ns = ...
-- Audio: duck the built-in zone music while a drop plays, restore it afterwards, and play
-- drops that ship an in-game file through PlaySoundFile on the Music channel.
--
-- Never sets Sound_EnableMusic=0 to mute zone music during a drop (that would also mute
-- in-game drops); it is only toggled 0 -> 1 for 0.1 s afterwards to restart zone music, and a
-- drop that starts meanwhile ends that window at once (FinishKick, baseline ND-DIR-02).
--
-- The "volume" method prefers C_CVar.SetTempCVar (a session-only value that never reaches
-- Config.wtf) and then undoes it with C_CVar.RemoveTempCVar, never with SetCVar: Blizzard's
-- own gamepad code (Blizzard_Gamepad/Core.lua) treats the two as a pair, and a SetCVar on top
-- of an active temp value is not documented to clear it (baseline ND-AUDIO-TEMPCVAR).

local A = {}
ns.Audio = A

local KICK_DELAY = 0.1
local LOCAL_FADE_MS = 3000
local VOLUME_CVAR = "Sound_MusicVolume"

A.ducked = nil          -- nil | "silence" | "volume"
A.tempCVar = false      -- the volume duck used SetTempCVar (undo with RemoveTempCVar)
A.kickPending = false
A.kickToken = 0

local function tempCVarAPI()
  return C_CVar and type(C_CVar.SetTempCVar) == "function" and type(C_CVar.RemoveTempCVar) == "function"
end

-- Fades out zone music. opts.forLocal: an in-game file will play on the Music channel, so
-- the "volume" method (which would silence it too) is replaced by "silence".
-- opts.method overrides the configured method. Returns the method used (or nil).
function A.Duck(opts)
  if A.ducked then return A.ducked end
  local method = (opts and opts.method) or (ns.settings and ns.settings.duckMethod) or "silence"
  if method == "volume" and opts and opts.forLocal then method = "silence" end
  if method == "silence" then
    local ok = pcall(PlayMusic, ns.SILENCE)
    if ok then A.ducked = "silence" end
  elseif method == "volume" then
    -- The saved original is kept for a crash restore (RepairCVars) on both paths.
    ns.SaveCVar(VOLUME_CVAR)
    A.tempCVar = false
    if tempCVarAPI() then
      local ok, res = pcall(C_CVar.SetTempCVar, VOLUME_CVAR, "0")
      A.tempCVar = ok and res ~= false
    end
    if not A.tempCVar then ns.SetCVar(VOLUME_CVAR, "0") end
    A.ducked = "volume"
  end
  ns.Debug("audio: duck (%s%s)", tostring(A.ducked or "none"), A.tempCVar and ", temp cvar" or "")
  return A.ducked
end

-- Undoes a volume duck: the temp value is removed (the saved value was never changed), or the
-- saved value is written back.
local function undoVolume()
  if A.tempCVar then
    A.tempCVar = false
    pcall(C_CVar.RemoveTempCVar, VOLUME_CVAR)
    ns.ForgetCVar(VOLUME_CVAR)
  else
    ns.RestoreCVar(VOLUME_CVAR)
  end
end

-- Ends a pending Sound_EnableMusic kick right away: music back on now.
function A.FinishKick()
  if not A.kickPending then return false end
  A.kickToken = A.kickToken + 1
  A.kickPending = false
  ns.SetCVar("Sound_EnableMusic", "1")
  ns.ForgetCVar("Sound_EnableMusic")
  return true
end

-- Toggles Sound_EnableMusic off and back on so the client restarts zone music now.
-- The original value is kept in savedCVars meanwhile (crash-safe).
function A.KickZoneMusic()
  if A.kickPending then return end
  if ns.GetCVar("Sound_EnableMusic") ~= "1" then return end
  A.kickPending = true
  A.kickToken = A.kickToken + 1
  local mine = A.kickToken
  ns.SaveCVar("Sound_EnableMusic", "1")
  ns.SetCVar("Sound_EnableMusic", "0")
  C_Timer.After(KICK_DELAY, function()
    if mine ~= A.kickToken then return end   -- FinishKick already turned it back on
    A.kickPending = false
    ns.SetCVar("Sound_EnableMusic", "1")
    ns.ForgetCVar("Sound_EnableMusic")
  end)
end

-- Undoes A.Duck.
function A.Restore()
  local how = A.ducked
  A.ducked = nil
  if how == "silence" then
    pcall(StopMusic)
    if ns.settings and ns.settings.kickZoneMusic then A.KickZoneMusic() end
  elseif how == "volume" then
    undoVolume()
  end
  if how then ns.Debug("audio: restore (%s)", how) end
end

function A.IsDucked() return A.ducked ~= nil end

-- Plays an in-game drop file. Returns playing (bool), handle (may be nil).
function A.PlayLocal(path)
  if type(path) ~= "string" or path == "" or type(PlaySoundFile) ~= "function" then return false, nil end
  local ok, willPlay, handle = pcall(PlaySoundFile, path, "Music")
  if not ok or ns.IsSecret(willPlay) or not willPlay then
    ns.Debug("audio: PlaySoundFile refused %s", path)
    return false, nil
  end
  if ns.IsSecret(handle) then handle = nil end
  return true, handle
end

function A.StopLocal(handle, fadeMs)
  if handle == nil or type(StopSound) ~= "function" then return end
  pcall(StopSound, handle, fadeMs or LOCAL_FADE_MS)
end

-- true / false when known, nil when the client cannot tell us.
function A.IsPlaying(handle)
  if handle == nil or not (C_Sound and C_Sound.IsPlaying) then return nil end
  local ok, playing = pcall(C_Sound.IsPlaying, handle)
  if not ok or ns.IsSecret(playing) then return nil end
  return playing and true or false
end

-- Logging out mid-drop: put the CVars back now so they are saved correctly.
ns.On("PLAYER_LOGOUT", function()
  if A.ducked == "volume" then undoVolume() end
  A.ducked = nil
  if A.kickPending then
    A.kickToken = A.kickToken + 1
    A.kickPending = false
    ns.SetCVar("Sound_EnableMusic", "1")
    ns.ForgetCVar("Sound_EnableMusic")
  end
end)
