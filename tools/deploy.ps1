<#
.SYNOPSIS
  Copy the NeedleDropper addon from this repo into WoW: Forever's AddOns folder.

.DESCRIPTION
  Copies addon\NeedleDropper into <WowRoot>\_forever_\Interface\AddOns\NeedleDropper.

  * Refuses while <WowRoot>\_forever_\Wow.exe is running (new addon files are only
    picked up after a client restart) unless -Force is given.
  * Never touches Interface\AddOns\NeedleDropper_Local (your own media files).
  * Keeps an installed Map.lua / Map.json (generated from your packs by
    `needledropper build-map`) unless -ReplaceMap is given; on a first install the
    bundled placeholder map is copied.
  * Never deletes anything: files removed from the repo stay in the installed copy
    until you remove them yourself.

.PARAMETER WowRoot
  World of Warcraft install folder (default: C:\Program Files (x86)\World of Warcraft).

.PARAMETER Force
  Copy even while the game is running.

.PARAMETER ReplaceMap
  Overwrite the installed Map.lua / Map.json with the repo's placeholder.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\deploy.ps1
.EXAMPLE
  tools\deploy.ps1 -WowRoot "D:\Games\World of Warcraft" -Force
#>
[CmdletBinding()]
param(
    [string]$WowRoot = "C:\Program Files (x86)\World of Warcraft",
    [switch]$Force,
    [switch]$ReplaceMap
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "addon\NeedleDropper"
$flavorRoot = Join-Path $WowRoot "_forever_"
$addonsDir = Join-Path $flavorRoot "Interface\AddOns"
$dest = Join-Path $addonsDir "NeedleDropper"
$gameExe = Join-Path $flavorRoot "Wow.exe"

if (-not (Test-Path -LiteralPath (Join-Path $source "NeedleDropper.toc") -PathType Leaf)) {
    Write-Error "Addon sources not found: $source (expected NeedleDropper.toc)."
    exit 1
}
if (-not (Test-Path -LiteralPath $flavorRoot -PathType Container)) {
    Write-Error "WoW: Forever not found at $flavorRoot. Pass -WowRoot <your WoW folder>."
    exit 1
}

# --- refuse while this install's Wow.exe runs ---------------------------------
$running = @()
try {
    $running = @(Get-Process -Name "Wow" -ErrorAction SilentlyContinue | Where-Object {
        $p = $null
        try { $p = $_.Path } catch { $p = $null }
        # Path unreadable (other user / elevation): count it, to be safe.
        (-not $p) -or ([System.IO.Path]::GetFullPath($p) -ieq [System.IO.Path]::GetFullPath($gameExe))
    })
} catch {
    $running = @()
}
if ($running.Count -gt 0 -and -not $Force) {
    Write-Warning "WoW is running ($gameExe). New addon files only load after a client restart."
    Write-Warning "Close the game, or re-run with -Force and restart the client afterwards."
    exit 2
}

# --- copy ----------------------------------------------------------------------
$preserve = @("Map.lua", "Map.json")
$skipNames = @("__pycache__", ".git", ".DS_Store", "Thumbs.db")
$copied = 0
$kept = 0

New-Item -ItemType Directory -Force -Path $dest | Out-Null
$sourceFull = (Resolve-Path -LiteralPath $source).Path.TrimEnd('\')

Get-ChildItem -LiteralPath $sourceFull -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($sourceFull.Length).TrimStart('\')
    $parts = $rel -split '\\'
    foreach ($part in $parts) {
        if ($skipNames -contains $part -or $part.StartsWith(".")) { return }
    }
    if ($_.Name -like "*.bak" -or $_.Name -like "*.tmp" -or $_.Name -like "*.orig") { return }

    $target = Join-Path $dest $rel
    if (($preserve -contains $rel) -and (Test-Path -LiteralPath $target) -and -not $ReplaceMap) {
        $script:kept++
        return
    }
    $targetDir = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $targetDir)) {
        New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    }
    Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    $script:copied++
}

Write-Host "Deployed NeedleDropper: $copied file(s) -> $dest"
if ($kept -gt 0) {
    Write-Host "Kept the installed Map.lua/Map.json (use -ReplaceMap to overwrite, or run 'needledropper build-map')."
}
if ($running.Count -gt 0) {
    Write-Host "WoW is running: type /reload in game (restart the client if you added new files)."
}
exit 0
