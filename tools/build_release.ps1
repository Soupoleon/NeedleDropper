<#
.SYNOPSIS
  Build a NeedleDropper release: run the tests, check the bundled soundtrack sets, build the
  windowed NeedleDropper.exe (one file) and the one-folder variant, package the addon zip and
  the Windows zips, and write SHA256SUMS.txt.

.DESCRIPTION
  Steps (the script stops with exit code 1 at the first problem):

   1. Run the full test suite (skipped with -SkipTests).
   2. Check that addon\NeedleDropper\Sets\*.lua match packs\*.json (tools\gen_sets.py --check).
   3. Build the windowed app twice with PyInstaller from companion\NeedleDropper.spec:
        one file   (NEEDLEDROPPER_BUILD=onefile): NeedleDropper.exe, unpacks itself at every start
        one folder (NEEDLEDROPPER_BUILD=onedir):  NeedleDropper\NeedleDropper.exe + _internal\
      Why both: the one-file exe is the easiest download, but self-extracting executables are a
      classic antivirus false positive; the one-folder build unpacks nothing at runtime, starts
      faster and is flagged less often. Each build has its own PyInstaller work folder.
   4. Check both builds from the inside (the Connect page, the tray icons, the packs, the addon
      with its Sets\ folder and the dynamically loaded modules must be there; no __pycache__) and
      run the one-file exe once (--version and validate) with a throw-away NEEDLEDROPPER_HOME.
   5. Write into -OutDir:
        NeedleDropper-<version>-addon.zip         the NeedleDropper\ addon folder at the zip root
                                                  (plus the MIT LICENSE inside it; no
                                                  NeedleDropper_Local folder - the companion
                                                  creates that one on demand)
        NeedleDropper-<version>-win64.zip         NeedleDropper.exe (one file) + README.txt
                                                  (quick start) + LICENSE.txt
        NeedleDropper-<version>-win64-folder.zip  the NeedleDropper\ folder of the one-folder
                                                  build + README.txt + LICENSE.txt
        NeedleDropper.exe                         the one-file exe, for direct download
        SHA256SUMS.txt                            checksums of the four files above
                                                  (sha256sum -c compatible)

  <version> is needledropper.__version__ (companion\needledropper\__init__.py).

.PARAMETER SkipTests
  Do not run the test suite first.

.PARAMETER OutDir
  Where the release files go. Relative paths are resolved against the repository root.
  Default: dist

.PARAMETER WorkDir
  PyInstaller's work folders (build cache, warn-NeedleDropper.txt). Relative paths are
  resolved against the repository root. Default: build\pyinstaller

.PARAMETER Python
  Python interpreter that has PyInstaller installed. Default: .venv\Scripts\python.exe
  (install the build tools with: .venv\Scripts\python.exe -m pip install -r companion\build-requirements.txt)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\build_release.ps1

.EXAMPLE
  tools\build_release.ps1 -SkipTests -OutDir C:\Temp\nd-release
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [string]$OutDir = "dist",
    [string]$WorkDir = "build\pyinstaller",
    [string]$Python = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$AppName = "NeedleDropper"
$CompanionDir = Join-Path $RepoRoot "companion"
$SpecFile = Join-Path $CompanionDir "NeedleDropper.spec"
$InitFile = Join-Path $CompanionDir "needledropper\__init__.py"
$BuildRequirements = Join-Path $CompanionDir "build-requirements.txt"
$AddonSource = Join-Path $RepoRoot "addon\NeedleDropper"
$AddonToc = Join-Path $AddonSource "NeedleDropper.toc"
$SetsDir = Join-Path $AddonSource "Sets"
$PacksDir = Join-Path $RepoRoot "packs"
$StaticDir = Join-Path $CompanionDir "needledropper\deck\static"
$AssetsDir = Join-Path $CompanionDir "needledropper\assets"
$GenSets = Join-Path $RepoRoot "tools\gen_sets.py"
$LicenseFile = Join-Path $RepoRoot "LICENSE"
$TestsDir = Join-Path $RepoRoot "tests"

# Same skip rules as companion\NeedleDropper.spec and `needledropper install-addon`.
$SkipDirs = @("__pycache__", ".git", ".svn", ".hg", ".idea", ".vscode")
$SkipFiles = @(".DS_Store", "Thumbs.db", "desktop.ini")
$SkipSuffixes = @(".bak", ".tmp", ".orig", ".pyc", ".pyo", ".swp")

# What every build must contain (paths inside the bundle, '/' separators).
$RequiredData = @(
    "packs/azeroth-mixtape.json",
    "packs/azeroth-unbound.json",
    "addon/NeedleDropper/NeedleDropper.toc",
    "addon/NeedleDropper/Sets/AzerothMixtape.lua",
    "addon/NeedleDropper/Sets/AzerothUnbound.lua",
    "addon/NeedleDropper/media/silence.mp3",
    "needledropper/deck/static/connect.html",
    "needledropper/deck/static/connect.js",
    "needledropper/deck/static/player.html",
    "needledropper/assets/NeedleDropper.ico",
    "needledropper/assets/NeedleDropper-paused.ico"
)
# Modules loaded by name at runtime (the archive listing shows modules by dotted name).
$RequiredModules = @(
    "needledropper.cli",
    "needledropper.panel_api",
    "needledropper.tray",
    "needledropper.deck.server",
    "needledropper.transport.pixel",
    "needledropper.transport.chatlog",
    "needledropper.providers.spotify"
)

# ---------------------------------------------------------------- helpers ----

function Fail([string]$Message) {
    throw $Message
}

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Note([string]$Message) {
    Write-Host "    $Message"
}

function Write-Warn([string]$Message) {
    Write-Host "    WARNING: $Message" -ForegroundColor Yellow
}

function Resolve-RepoPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $Path))
}

function Assert-File([string]$Path, [string]$What) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Fail "$What not found: $Path"
    }
}

function Assert-Dir([string]$Path, [string]$What) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        Fail "$What not found: $Path"
    }
}

# Runs a native command; stderr is shown but never treated as failure, the exit code is.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        Fail "$What failed (exit code $code)."
    }
}

# Like Invoke-Native, but returns stdout as an array of lines. The output goes through a
# pipeline on purpose: PowerShell only waits for a windowed (GUI-subsystem) program such as
# NeedleDropper.exe - and only then sets $LASTEXITCODE - when its output is piped.
function Invoke-NativeOutput {
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = @(& $FilePath @Arguments | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        if ($lines.Count -gt 0) { $lines | ForEach-Object { Write-Host "    $_" } }
        Fail "$What failed (exit code $code)."
    }
    return , [string[]]@($lines | ForEach-Object { "$_" })
}

function Test-Shippable([string]$Name) {
    if ($Name.StartsWith(".")) { return $false }
    if ($SkipFiles -contains $Name) { return $false }
    foreach ($suffix in $SkipSuffixes) {
        if ($Name.EndsWith($suffix, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    return $true
}

# Source: the file to store, or $null / "" for a directory entry (Name ends with "/").
function New-ZipEntrySpec([string]$Name, $Source) {
    return [pscustomobject]@{ Name = $Name; Source = $Source }
}

# Adds a directory entry for $Prefix/ and every shippable file below $Dir (sorted, '/' separators).
function Add-TreeEntries {
    param(
        [Parameter(Mandatory = $true)][string]$Dir,
        [Parameter(Mandatory = $true)][string]$Prefix,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Into
    )
    $Into.Add((New-ZipEntrySpec "$Prefix/" $null))
    $items = @(Get-ChildItem -LiteralPath $Dir -Force | Sort-Object -Property Name)
    foreach ($item in $items) {
        if ($item.PSIsContainer) {
            if ($SkipDirs -contains $item.Name -or $item.Name.StartsWith(".")) { continue }
            Add-TreeEntries -Dir $item.FullName -Prefix "$Prefix/$($item.Name)" -Into $Into
        } elseif (Test-Shippable $item.Name) {
            $Into.Add((New-ZipEntrySpec "$Prefix/$($item.Name)" $item.FullName))
        }
    }
}

# Writes a zip with exactly the given entries (forward-slash names; Source $null = directory).
function New-ReleaseZip {
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][object[]]$Entries
    )
    if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
    $seen = @{}
    $stream = [System.IO.File]::Open($ZipPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite)
    try {
        $archive = New-Object System.IO.Compression.ZipArchive -ArgumentList @(
            $stream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
        try {
            foreach ($spec in $Entries) {
                $name = [string]$spec.Name
                if ($name.Contains("\") -or $name.StartsWith("/") -or $name.Contains("..")) {
                    Fail "bad zip entry name '$name'"
                }
                if ($seen.ContainsKey($name)) { Fail "duplicate zip entry '$name'" }
                $seen[$name] = $true
                if ([string]::IsNullOrEmpty([string]$spec.Source)) {
                    if (-not $name.EndsWith("/")) { Fail "zip entry '$name' has no source file" }
                    [void]$archive.CreateEntry($name)
                    continue
                }
                $entry = $archive.CreateEntry($name, [System.IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = [System.DateTimeOffset](Get-Item -LiteralPath $spec.Source).LastWriteTime
                $in = [System.IO.File]::OpenRead($spec.Source)
                try {
                    $out = $entry.Open()
                    try { $in.CopyTo($out) } finally { $out.Dispose() }
                } finally {
                    $in.Dispose()
                }
            }
        } finally {
            $archive.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

# Reads back a zip's entry names (to verify what was written).
function Get-ZipEntryNames([string]$ZipPath) {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        return , [string[]]@($zip.Entries | ForEach-Object { $_.FullName })
    } finally {
        $zip.Dispose()
    }
}

function Write-TextFile([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding -ArgumentList $false))
}

function Format-Size([long]$Bytes) {
    if ($Bytes -ge 1MB) { return ("{0:N1} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N1} KB" -f ($Bytes / 1KB)) }
    return "$Bytes bytes"
}

# Runs PyInstaller once for one layout ("onefile" / "onedir") with its own work folder.
function Invoke-PyInstaller {
    param(
        [Parameter(Mandatory = $true)][string]$Mode,
        [Parameter(Mandatory = $true)][string]$DistPath,
        [Parameter(Mandatory = $true)][string]$WorkPath
    )
    New-Item -ItemType Directory -Force -Path $DistPath | Out-Null
    New-Item -ItemType Directory -Force -Path $WorkPath | Out-Null
    $previousMode = $env:NEEDLEDROPPER_BUILD
    $env:NEEDLEDROPPER_BUILD = $Mode
    try {
        Invoke-Native -What "PyInstaller ($Mode)" -FilePath $Python -Arguments @(
            "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
            "--distpath", $DistPath, "--workpath", $WorkPath, $SpecFile)
    } finally {
        if ($null -eq $previousMode) { Remove-Item Env:NEEDLEDROPPER_BUILD -ErrorAction SilentlyContinue }
        else { $env:NEEDLEDROPPER_BUILD = $previousMode }
    }
    $warnFile = Join-Path $WorkPath "$AppName\warn-$AppName.txt"
    if (Test-Path -LiteralPath $warnFile) {
        # Missing standard-library modules are normal (other platforms); a missing module of
        # our own package means some code imports a file that does not exist: a runtime crash.
        $ownMissing = @()
        foreach ($line in [System.IO.File]::ReadAllLines($warnFile)) {
            if ($line -match '^missing module named ''?(needledropper(\.[\w.]+)?)''? - imported by (.+)$') {
                $ownMissing += "$($Matches[1]) (imported by $($Matches[3]))"
            }
        }
        if ($ownMissing.Count -gt 0) {
            Fail ("the companion imports modules that do not exist: " + ($ownMissing -join "; ") +
                  ". See $warnFile")
        }
        Write-Note "PyInstaller's list of unresolved optional imports (harmless): $warnFile"
    }
}

# Fails when a set of bundle paths lacks a required file or holds litter.
function Assert-BundleContents {
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][hashtable]$Names,
        [string[]]$Modules = @()
    )
    $missing = @($RequiredData | Where-Object { -not $Names.ContainsKey($_) })
    $missing += @($Modules | Where-Object { -not $Names.ContainsKey($_) })
    if ($missing.Count -gt 0) {
        Fail ("$What is missing bundled files or modules: " + ($missing -join ", "))
    }
    $litter = @($Names.Keys | Where-Object { $_ -like "*__pycache__*" -or $_ -like "*NeedleDropper_Local*" -or
                                             $_ -like "*panel.html" })
    if ($litter.Count -gt 0) {
        Fail ("$What bundles files it must not: " + (($litter | Select-Object -First 5) -join ", "))
    }
}

function Get-QuickStartText([string]$Version) {
    $title = "NeedleDropper $Version"
    $lines = @(
        $title,
        ("=" * $title.Length),
        "",
        "The first time your character arrives somewhere new in World of Warcraft: Forever,",
        "NeedleDropper plays one hand-picked song and fades in a quiet, cinematic title card:",
        "a Kojima-style ""needle drop"". Then the world goes quiet again.",
        "",
        "NeedleDropper.exe is the companion app. It plays the songs through Spotify Premium or",
        "YouTube. It needs no installation and no Python, and it has no window of its own: it",
        "waits in the tray, next to the clock. Everything else happens in game.",
        "",
        "QUICK START",
        "-----------",
        "1. Put NeedleDropper somewhere permanent (for example Documents\NeedleDropper) and",
        "   double-click NeedleDropper.exe.",
        "   If Windows says ""Windows protected your PC"", click ""More info"", then",
        "   ""Run anyway"". NeedleDropper is free and open source, but it is not code-signed.",
        "",
        "2. The Connect page opens in your web browser. It has four short steps:",
        "   - World of Warcraft: the game folder is found for you. Install the NeedleDropper",
        "     addon from CurseForge (or your addon manager); ""Install addon"" there copies the",
        "     addon that comes with this app if you have no addon manager.",
        "   - Spotify (optional, Premium): three steps to connect your own free Spotify app.",
        "   - YouTube: nothing to set up.",
        "   - Check the link: start WoW: Forever in ""Windowed"" or ""Windowed (Fullscreen)""",
        "     mode and type   /needle test   - the page shows a tick.",
        "   Leave ""Start with Windows"" ticked and click Done.",
        "",
        "3. Play. In game, type /needle to change songs, pick soundtracks or share them.",
        "",
        "GOOD TO KNOW",
        "------------",
        "- Right-click the NeedleDropper icon in the tray to open the Connect page again,",
        "  pause the needle drops, change ""Start with Windows"", open the log folder or quit.",
        "- Starting NeedleDropper.exe again while it runs simply opens the Connect page.",
        "- Your settings, logs and Spotify sign-in (encrypted with Windows DPAPI) are kept in",
        "  %APPDATA%\NeedleDropper. Nothing is sent anywhere except to Spotify and YouTube.",
        "- NeedleDropper-$Version-win64-folder.zip holds the same app as one folder instead of",
        "  one file: it starts faster and antivirus programs flag it less often. Keep the",
        "  folder together and start NeedleDropper.exe inside it.",
        "",
        "The full guides (install, music services, soundtrack packs, privacy and",
        "troubleshooting) are in the docs folder of the NeedleDropper project.",
        "",
        "NeedleDropper is free software under the MIT license (see LICENSE.txt). It is a",
        "fan project and is not affiliated with Blizzard Entertainment, Spotify or YouTube."
    )
    return (($lines -join "`r`n") + "`r`n")
}

# ------------------------------------------------------------------- main ----

$exitCode = 0
$tempRoot = $null
$homeBefore = $env:NEEDLEDROPPER_HOME
$homeWasSet = Test-Path Env:NEEDLEDROPPER_HOME
Push-Location -LiteralPath $RepoRoot
try {
    $started = Get-Date
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    # --- preflight -------------------------------------------------------------
    Write-Step "Checking the inputs"
    if ([string]::IsNullOrWhiteSpace($Python)) {
        $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    } else {
        $Python = Resolve-RepoPath $Python
    }
    Assert-File $Python "Python interpreter"
    Assert-File $SpecFile "PyInstaller spec"
    Assert-File $InitFile "needledropper\__init__.py"
    Assert-File $LicenseFile "LICENSE"
    Assert-File $AddonToc "Addon TOC"
    Assert-File $GenSets "tools\gen_sets.py"
    Assert-Dir $SetsDir "bundled soundtrack sets (addon\NeedleDropper\Sets)"
    Assert-Dir $PacksDir "packs folder"
    Assert-Dir $StaticDir "Connect page files (deck\static)"
    Assert-Dir $AssetsDir "tray icons (needledropper\assets)"
    if (@(Get-ChildItem -LiteralPath $PacksDir -Filter "*.json" -File).Count -eq 0) {
        Fail "no pack (*.json) in $PacksDir"
    }
    $OutDir = Resolve-RepoPath $OutDir
    $WorkDir = Resolve-RepoPath $WorkDir
    $companionFull = [System.IO.Path]::GetFullPath($CompanionDir).TrimEnd("\") + "\"
    foreach ($dir in @($OutDir, $WorkDir)) {
        if (($dir.TrimEnd("\") + "\").StartsWith($companionFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            Fail "output folders must not be inside companion\ (the tests scan it): $dir"
        }
    }

    $versionLines = Invoke-NativeOutput -What "Reading needledropper.__version__" -FilePath $Python -Arguments @(
        "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import needledropper; print(needledropper.__version__)",
        $CompanionDir)
    $Version = ($versionLines | Where-Object { $_.Trim() -ne "" } | Select-Object -Last 1)
    if ($null -eq $Version) { Fail "needledropper.__version__ is empty" }
    $Version = $Version.Trim()
    if ($Version -notmatch '^\d+\.\d+\.\d+([.+-][0-9A-Za-z.+-]+)?$') {
        Fail "needledropper.__version__ '$Version' is not a release version like 1.2.3"
    }
    Write-Note "Version:  $Version"
    Write-Note "Python:   $Python"
    Write-Note "Output:   $OutDir"
    $tocVersion = $null
    foreach ($line in [System.IO.File]::ReadAllLines($AddonToc)) {
        if ($line -match '^\s*##\s*Version:\s*(.+?)\s*$') { $tocVersion = $Matches[1] }
    }
    if ($tocVersion -ne $Version) {
        Write-Warn "the addon TOC says '## Version: $tocVersion' but the companion is $Version."
        Write-Warn "Set '## Version: $Version' in addon\NeedleDropper\NeedleDropper.toc before publishing."
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("nd-release-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

    # --- 1. tests --------------------------------------------------------------
    if ($SkipTests) {
        Write-Step "Skipping the test suite (-SkipTests)"
    } else {
        Write-Step "Running the test suite"
        Invoke-Native -What "The test suite" -FilePath $Python -Arguments @(
            "-m", "pytest", $TestsDir, "-q", "-p", "no:cacheprovider")
    }

    # --- 2. bundled soundtrack sets ----------------------------------------------
    Write-Step "Checking the bundled soundtrack sets (tools\gen_sets.py --check)"
    Invoke-Native -What "tools\gen_sets.py --check (run 'python tools\gen_sets.py' and commit the result)" `
        -FilePath $Python -Arguments @($GenSets, "--check")
    Write-Note ("Sets up to date: " + ((@(Get-ChildItem -LiteralPath $SetsDir -Filter "*.lua" -File) |
        ForEach-Object { $_.Name }) -join ", "))

    # --- 3. builds ---------------------------------------------------------------
    Write-Step "Building $AppName.exe with PyInstaller (one file and one folder)"
    $pyiVersionLines = @()
    try {
        $pyiVersionLines = Invoke-NativeOutput -What "PyInstaller" -FilePath $Python -Arguments @("-m", "PyInstaller", "--version")
    } catch {
        Fail ("PyInstaller is not installed for $Python. Install the build tools with:`n" +
              "  `"$Python`" -m pip install -r `"$BuildRequirements`"")
    }
    $pyiVersion = ($pyiVersionLines | Select-Object -Last 1).Trim()
    $pinned = $null
    if (Test-Path -LiteralPath $BuildRequirements) {
        foreach ($line in [System.IO.File]::ReadAllLines($BuildRequirements)) {
            if ($line -match '^\s*pyinstaller\s*==\s*([^\s#]+)') { $pinned = $Matches[1] }
        }
    }
    Write-Note "PyInstaller $pyiVersion"
    if ($pinned -and $pinned -ne $pyiVersion) {
        Write-Warn "companion\build-requirements.txt pins PyInstaller $pinned, but $pyiVersion is installed."
    }
    $exeDir = Join-Path $WorkDir "dist"
    $folderDistDir = Join-Path $WorkDir "dist-onedir"
    $builtExe = Join-Path $exeDir "$AppName.exe"
    $builtFolder = Join-Path $folderDistDir $AppName
    $builtFolderExe = Join-Path $builtFolder "$AppName.exe"
    if (Test-Path -LiteralPath $builtExe) { Remove-Item -LiteralPath $builtExe -Force }
    if (Test-Path -LiteralPath $builtFolder) { Remove-Item -LiteralPath $builtFolder -Recurse -Force }
    Invoke-PyInstaller -Mode "onefile" -DistPath $exeDir -WorkPath (Join-Path $WorkDir "onefile")
    Assert-File $builtExe "Built exe"
    Write-Note ("Built {0} ({1})" -f $builtExe, (Format-Size (Get-Item -LiteralPath $builtExe).Length))
    Invoke-PyInstaller -Mode "onedir" -DistPath $folderDistDir -WorkPath (Join-Path $WorkDir "onedir")
    Assert-File $builtFolderExe "Built one-folder exe"
    Write-Note ("Built {0}\ ({1} files)" -f $builtFolder, @(Get-ChildItem -LiteralPath $builtFolder -Recurse -File).Count)

    # --- 4. check the builds -------------------------------------------------------
    Write-Step "Checking what is inside the builds"
    $listing = Invoke-NativeOutput -What "Listing the exe archive" -FilePath $Python -Arguments @(
        "-m", "PyInstaller.utils.cliutils.archive_viewer", "--recursive", "--brief", $builtExe)
    $names = @{}
    foreach ($line in $listing) {
        $n = $line.Trim().Replace("\", "/")
        if ($n -ne "") { $names[$n] = $true }
    }
    Assert-BundleContents -What "$AppName.exe" -Names $names -Modules $RequiredModules
    $staticNames = @($names.Keys | Where-Object { $_.StartsWith("needledropper/deck/static/") })
    Write-Note ("One file OK: Connect page ({0} files), tray icons, packs, addon with Sets, companion modules." -f $staticNames.Count)

    $internal = Join-Path $builtFolder "_internal"
    Assert-Dir $internal "the one-folder build's _internal folder"
    $folderNames = @{}
    $internalFull = [System.IO.Path]::GetFullPath($internal).TrimEnd("\") + "\"
    foreach ($file in @(Get-ChildItem -LiteralPath $internal -Recurse -File)) {
        $folderNames[$file.FullName.Substring($internalFull.Length).Replace("\", "/")] = $true
    }
    Assert-BundleContents -What "the one-folder build" -Names $folderNames
    Write-Note "One folder OK: the same data files are in $AppName\_internal\."

    $smokeHome = Join-Path $tempRoot "home"
    New-Item -ItemType Directory -Force -Path $smokeHome | Out-Null
    $env:NEEDLEDROPPER_HOME = $smokeHome
    $versionOut = Invoke-NativeOutput -What "$AppName.exe --version" -FilePath $builtExe -Arguments @("--version")
    if (-not (($versionOut -join "`n") -match [regex]::Escape($Version))) {
        Fail "$AppName.exe --version printed '$($versionOut -join ' ')', expected version $Version"
    }
    Write-Note ("$AppName.exe --version: " + ($versionOut -join " ").Trim())
    $validateOut = Invoke-NativeOutput -What "$AppName.exe validate (bundled packs)" -FilePath $builtExe -Arguments @("validate")
    $validateSummary = @($validateOut | Where-Object { $_ -match '^(OK|FAILED)\s' -or $_ -match '^Total:' })
    foreach ($line in $validateSummary) { Write-Note "validate: $($line.Trim())" }
    $folderVersionOut = Invoke-NativeOutput -What "$AppName\$AppName.exe --version" -FilePath $builtFolderExe -Arguments @("--version")
    if (-not (($folderVersionOut -join "`n") -match [regex]::Escape($Version))) {
        Fail "the one-folder $AppName.exe --version printed '$($folderVersionOut -join ' ')', expected version $Version"
    }
    if ($homeWasSet) { $env:NEEDLEDROPPER_HOME = $homeBefore } else { Remove-Item Env:NEEDLEDROPPER_HOME -ErrorAction SilentlyContinue }

    # --- 5. release files ------------------------------------------------------
    Write-Step "Writing the release files"
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $addonZip = Join-Path $OutDir "$AppName-$Version-addon.zip"
    $winZip = Join-Path $OutDir "$AppName-$Version-win64.zip"
    $folderZip = Join-Path $OutDir "$AppName-$Version-win64-folder.zip"
    $outExe = Join-Path $OutDir "$AppName.exe"
    $sumsFile = Join-Path $OutDir "SHA256SUMS.txt"

    # addon zip: NeedleDropper/ at the root, exactly the addon folder (+ the license notice).
    $addonEntries = New-Object System.Collections.Generic.List[object]
    Add-TreeEntries -Dir $AddonSource -Prefix "NeedleDropper" -Into $addonEntries
    $addonEntries.Add((New-ZipEntrySpec "NeedleDropper/LICENSE" $LicenseFile))
    New-ReleaseZip -ZipPath $addonZip -Entries $addonEntries.ToArray()
    $addonNames = Get-ZipEntryNames $addonZip
    $roots = @($addonNames | ForEach-Object { ($_ -split "/")[0] } | Sort-Object -Unique)
    if ($roots.Count -ne 1 -or $roots[0] -ne "NeedleDropper") {
        Fail ("the addon zip must hold only the NeedleDropper/ folder, found: " + ($roots -join ", "))
    }
    if (-not ($addonNames -contains "NeedleDropper/NeedleDropper.toc")) { Fail "the addon zip has no NeedleDropper/NeedleDropper.toc" }
    if (@($addonNames | Where-Object { $_ -like "NeedleDropper/Sets/*.lua" }).Count -eq 0) { Fail "the addon zip has no Sets/*.lua" }
    Write-Note ("{0} ({1}, {2} files)" -f (Split-Path -Leaf $addonZip), (Format-Size (Get-Item -LiteralPath $addonZip).Length),
        @($addonNames | Where-Object { -not $_.EndsWith("/") }).Count)

    # win64 zip: the one-file exe, a plain-text quick start and the license.
    $readmeTxt = Join-Path $tempRoot "README.txt"
    Write-TextFile $readmeTxt (Get-QuickStartText $Version)
    $winEntries = @(
        (New-ZipEntrySpec "$AppName.exe" $builtExe),
        (New-ZipEntrySpec "README.txt" $readmeTxt),
        (New-ZipEntrySpec "LICENSE.txt" $LicenseFile)
    )
    New-ReleaseZip -ZipPath $winZip -Entries $winEntries
    Write-Note ("{0} ({1})" -f (Split-Path -Leaf $winZip), (Format-Size (Get-Item -LiteralPath $winZip).Length))

    # win64-folder zip: the NeedleDropper/ folder of the one-folder build (+ the same notes).
    $folderEntries = New-Object System.Collections.Generic.List[object]
    Add-TreeEntries -Dir $builtFolder -Prefix $AppName -Into $folderEntries
    $folderEntries.Add((New-ZipEntrySpec "$AppName/README.txt" $readmeTxt))
    $folderEntries.Add((New-ZipEntrySpec "$AppName/LICENSE.txt" $LicenseFile))
    New-ReleaseZip -ZipPath $folderZip -Entries $folderEntries.ToArray()
    $folderZipNames = Get-ZipEntryNames $folderZip
    if (-not ($folderZipNames -contains "$AppName/$AppName.exe")) { Fail "the folder zip has no $AppName/$AppName.exe" }
    Write-Note ("{0} ({1})" -f (Split-Path -Leaf $folderZip), (Format-Size (Get-Item -LiteralPath $folderZip).Length))

    Copy-Item -LiteralPath $builtExe -Destination $outExe -Force
    Write-Note ("{0} ({1})" -f (Split-Path -Leaf $outExe), (Format-Size (Get-Item -LiteralPath $outExe).Length))

    # checksums, "<sha256>  <file>" with LF line endings (sha256sum -c compatible)
    $sumLines = foreach ($file in @($addonZip, $winZip, $folderZip, $outExe)) {
        $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $(Split-Path -Leaf $file)"
    }
    Write-TextFile $sumsFile (($sumLines -join "`n") + "`n")
    Write-Note "SHA256SUMS.txt"
    foreach ($line in $sumLines) { Write-Note "  $line" }

    $elapsed = (Get-Date) - $started
    Write-Host ""
    Write-Host ("Release $Version built in {0:N0} s -> $OutDir" -f $elapsed.TotalSeconds) -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Host "BUILD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
} finally {
    if ($homeWasSet) { $env:NEEDLEDROPPER_HOME = $homeBefore } else { Remove-Item Env:NEEDLEDROPPER_HOME -ErrorAction SilentlyContinue }
    if ($tempRoot -and (Test-Path -LiteralPath $tempRoot)) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
}
exit $exitCode
