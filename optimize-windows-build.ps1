# =============================================================================
# Sathify - Windows build environment optimizer (Dynamic Paths)
# =============================================================================
#Requires -RunAsAdministrator

$ErrorActionPreference = 'Continue'

Write-Host ''
Write-Host '=== Windows Build Optimizer (Auto-Detecting Paths) ===' -ForegroundColor Cyan
Write-Host ''

# --- Environment Variables (Auto-detect user profile & appdata) --------------
$userProfile  = $env:USERPROFILE
$localAppData = $env:LOCALAPPDATA
$currentFolder = $PSScriptRoot  # Auto-detects the folder where this script is saved

# --- Dynamic Paths -----------------------------------------------------------
$paths = @(
    $currentFolder,                          # Current Project Directory
    "$userProfile\.gradle",                  # Gradle cache
    "$localAppData\Pub\Cache",               # Flutter Pub Cache
    "$localAppData\Android\Sdk",             # Standard Android SDK location
    "C:\Android\Sdk",                        # Alternative Android SDK location
    "$userProfile\flutter",                  # Flutter SDK (User folder)
    "C:\src\flutter"                         # Flutter SDK (C:\src folder)
)

# --- Processes that build/compile --------------------------------------------
$procs = @('java.exe', 'javaw.exe', 'dart.exe', 'flutter.bat', 'gradle.exe',
           'gradlew.bat', 'adb.exe', 'kotlin-daemon.exe', 'aapt2.exe')

# -----------------------------------------------------------------------------
# 1. Windows Defender Exclusions
# -----------------------------------------------------------------------------
Write-Host '[1/3] Adding Windows Defender exclusions...' -ForegroundColor Yellow
try {
    $null = Get-MpPreference -ErrorAction Stop

    foreach ($p in $paths) {
        if (Test-Path $p) {
            Add-MpPreference -ExclusionPath $p -ErrorAction SilentlyContinue
            Write-Host "      + Excluded path:    $p" -ForegroundColor Green
        } else {
            Write-Host "      ! Folder missing:   $p (skipped)" -ForegroundColor DarkGray
        }
    }
    foreach ($x in $procs) {
        Add-MpPreference -ExclusionProcess $x -ErrorAction SilentlyContinue
        Write-Host "      + Excluded process: $x" -ForegroundColor Green
    }
    Write-Host '      Defender exclusions updated successfully.' -ForegroundColor Green
}
catch {
    Write-Host '      Defender service is not active (McAfee/Third-party AV active). Skipping.' -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# 2. Enable Windows Long Path Support
# -----------------------------------------------------------------------------
Write-Host ''
Write-Host '[2/3] Enabling Long Path Support (MAX_PATH > 260)...' -ForegroundColor Yellow
try {
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
    $before = (Get-ItemProperty $key -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
    Set-ItemProperty -Path $key -Name LongPathsEnabled -Value 1 -Type DWord -ErrorAction Stop
    $after = (Get-ItemProperty $key -Name LongPathsEnabled).LongPathsEnabled
    Write-Host "      LongPathsEnabled: $before -> $after" -ForegroundColor Green
}
catch {
    Write-Host "      FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

try {
    git config --system core.longpaths true 2>$null
    Write-Host '      git core.longpaths set to true.' -ForegroundColor Green
} catch { }

# -----------------------------------------------------------------------------
# 3. Print Active Paths for McAfee / Antivirus Manual Exclusion
# -----------------------------------------------------------------------------
Write-Host ''
Write-Host '[3/3] Folder Paths Found On Your PC:' -ForegroundColor Yellow
Write-Host ''
foreach ($p in $paths) {
    if (Test-Path $p) {
        Write-Host "  [EXISTS]  $p" -ForegroundColor Cyan
    }
}
Write-Host ''
Write-Host 'If using McAfee, open McAfee Settings > Real-Time Scanning > Excluded Files'
Write-Host 'and manually add the [EXISTS] folders listed above.'
Write-Host ''
Write-Host '=== Done! Close and reopen your terminal. ===' -ForegroundColor Cyan
Write-Host ''