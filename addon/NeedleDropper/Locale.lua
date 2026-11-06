local ADDON, ns = ...
-- NeedleDropper Locale: the ns.L string table, key-binding names and the text escaper.
--
-- Loaded first. Every player-facing string in the addon goes through ns.L. The keys are the
-- English text itself, so ns.L["Add drop here"] is "Add drop here" unless a translation exists
-- for the client language: a missing key falls back to the key (= the English text).
-- Translations are injected by the BigWigs packager from the CurseForge localization app
-- (the @localization lines below are packager keywords; in a git checkout they are plain
-- comments and the client shows English).
--
-- Keep keys stable once released (a key that changes loses its translations). Use
-- string.format placeholders (%s, %d) for values and keep their order in translations.

ns = ns or {}

local L = setmetatable({}, {
  __index = function(_, key) return key end,
})
ns.L = L

local locale = type(GetLocale) == "function" and GetLocale() or "enUS"
if type(locale) ~= "string" or locale == "" then locale = "enUS" end
ns.LOCALE = locale

if locale == "deDE" then
  --@localization(locale="deDE", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "esES" then
  --@localization(locale="esES", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "esMX" then
  --@localization(locale="esMX", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "frFR" then
  --@localization(locale="frFR", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "itIT" then
  --@localization(locale="itIT", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "koKR" then
  --@localization(locale="koKR", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "ptBR" then
  --@localization(locale="ptBR", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "ruRU" then
  --@localization(locale="ruRU", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "zhCN" then
  --@localization(locale="zhCN", format="lua_additive_table", handle-unlocalized="ignore")@
elseif locale == "zhTW" then
  --@localization(locale="zhTW", format="lua_additive_table", handle-unlocalized="ignore")@
end

-- Key bindings (Bindings.xml). The keybinding settings page shows a binding's category as
-- _G[category] when that is a string (GetBindingCategoryName in
-- Blizzard_SettingsDefinitions_Frame/Keybindings.lua), and each binding as its
-- BINDING_NAME_<name> global.
BINDING_HEADER_NEEDLEDROPPER = "NeedleDropper"
BINDING_NAME_NEEDLEDROPPER_TOGGLE = L["Open or close the NeedleDropper window"]
BINDING_NAME_NEEDLEDROPPER_ADD_HERE = L["Add a needle drop where you stand"]
BINDING_NAME_NEEDLEDROPPER_STOP = L["Stop the needle drop"]

-- Text from players, imports or the companion before it reaches a FontString: "|" starts an
-- escape sequence (colours, textures, links), so every "|" is doubled and shows as itself.
-- Non-strings become "".
function ns.EscapeText(text)
  if type(text) ~= "string" then return "" end
  return (string.gsub(text, "|", "||"))
end

return L
