# =============================================================================
# Sathify - Windows build environment optimizer
# =============================================================================
# OPTIONAL, and Windows-only. Run it once, as Administrator, if your Flutter
# Android builds are painfully slow. Nothing in the project depends on it.
#
#   Right-click Start > "Terminal (Admin)" / "PowerShell (Admin)", then:
#     powershell -ExecutionPolicy Bypass -File <path-to-repo>\optimize-windows-build.ps1
#
# What it does and why:
#
#   1. Antivirus exclusions. Every file Gradle, the Dart compiler and the
#      Android build tools touch is opened, written and closed thousands of
#      times per build. A real-time AV scanner inspects each one of those
#      operations synchronously. On a Flutter/Android project this routinely
#      turns a 3-minute build into a 30-90 minute one. Excluding the build
#      paths is the single largest build-time win available on Windows.
#
#   2. Long path support. Windows' legacy 260-character MAX_PATH limit breaks
#      Gradle caches, the Android SDK's deeply-nested Maven layout and pub's
#      package cache. It has already caused a real extraction failure during
#      setup on at least one machine on this team.
#
# Nothing here is destructive: it only adds exclusions and flips one
# documented registry flag. It is safe to re-run.
#
# Every path below is DISCOVERED, not hardcoded, so this works on all four of
# our machines. Anything it cannot find is reported and skipped rather than
# silently ignored - if the list printed in step 1 looks short, that is the
# script telling you your tooling is somewhere it did not think to look, and
# you should add it by hand in your antivirus UI.
# =============================================================================

#Requires -RunAsAdministrator

$ErrorActionPreference = 'Continue'

Write-Host ''
Write-Host '=== Sathify Windows build optimizer ===' -ForegroundColor Cyan
Write-Host ''

# --- Work out where everything actually lives on THIS machine ----------------

# The repository is wherever this script is.
$repoRoot = $PSScriptRoot

# Flutter: from PATH. `flutter` on PATH is <sdk>\bin\flutter.bat, so the SDK
# root is two levels up.
$flutterSdk = $null
$flutterCmd = Get-Command flutter -ErrorAction SilentlyContinue
if ($flutterCmd) {
    $flutterSdk = Split-Path (Split-Path $flutterCmd.Source -Parent) -Parent
}

# Android SDK: the standard environment variables first, then the default
# install location Android Studio uses.
$androidSdk = $env:ANDROID_HOME
if (-not $androidSdk) { $androidSdk = $env:ANDROID_SDK_ROOT }
if (-not $androidSdk) { $androidSdk = Join-Path $env:LOCALAPPDATA 'Android\Sdk' }

# Gradle honours GRADLE_USER_HOME, and defaults to ~\.gradle.
$gradleHome = $env:GRADLE_USER_HOME
if (-not $gradleHome) { $gradleHome = Join-Path $env:USERPROFILE '.gradle' }

# pub honours PUB_CACHE, and defaults to %LOCALAPPDATA%\Pub\Cache on Windows.
$pubCache = $env:PUB_CACHE
if (-not $pubCache) { $pubCache = Join-Path $env:LOCALAPPDATA 'Pub\Cache' }

# The JDK the Android build uses. JAVA_HOME if set, otherwise resolve `java`.
# See README: this project's Gradle/AGP/Kotlin versions need JDK 21.
$javaHome = $env:JAVA_HOME
if (-not $javaHome) {
    $javaCmd = Get-Command java -ErrorAction SilentlyContinue
    if ($javaCmd) { $javaHome = Split-Path (Split-Path $javaCmd.Source -Parent) -Parent }
}

$paths = @($repoRoot, $flutterSdk, $androidSdk, $gradleHome, $pubCache, $javaHome) |
    Where-Object { $_ } | Select-Object -Unique

Write-Host 'Discovered build paths:' -ForegroundColor Cyan
Write-Host "      repo        $repoRoot"
Write-Host "      flutter     $(if ($flutterSdk) { $flutterSdk } else { 'NOT FOUND (is flutter on PATH?)' })"
Write-Host "      android sdk $androidSdk"
Write-Host "      gradle      $gradleHome"
Write-Host "      pub cache   $pubCache"
Write-Host "      jdk         $(if ($javaHome) { $javaHome } else { 'NOT FOUND (is java on PATH?)' })"
Write-Host ''

# --- Processes that do the hammering ----------------------------------------
$procs = @('java.exe', 'javaw.exe', 'dart.exe', 'flutter.bat', 'gradle.exe',
           'gradlew.bat', 'adb.exe', 'kotlin-daemon.exe', 'aapt2.exe',
           'python.exe')

# -----------------------------------------------------------------------------
# 1. Windows Defender exclusions
# -----------------------------------------------------------------------------
Write-Host '[1/3] Windows Defender exclusions...' -ForegroundColor Yellow
$defenderActive = $false
try {
    $null = Get-MpPreference -ErrorAction Stop
    $defenderActive = $true

    foreach ($p in $paths) {
        if (Test-Path $p) {
            Add-MpPreference -ExclusionPath $p -ErrorAction SilentlyContinue
            Write-Host "      + path    $p" -ForegroundColor Green
        } else {
            Write-Host "      ! missing  $p (skipped)" -ForegroundColor DarkGray
        }
    }
    foreach ($x in $procs) {
        Add-MpPreference -ExclusionProcess $x -ErrorAction SilentlyContinue
        Write-Host "      + process $x" -ForegroundColor Green
    }
    Write-Host '      Defender exclusions applied.' -ForegroundColor Green
}
catch {
    Write-Host '      Windows Defender is not the active scanner on this machine.' -ForegroundColor DarkGray
    Write-Host '      Add-MpPreference does nothing here - see step 3.' -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# 2. Enable Windows long path support
# -----------------------------------------------------------------------------
Write-Host ''
Write-Host '[2/3] Enabling long path support (MAX_PATH > 260)...' -ForegroundColor Yellow
try {
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
    $before = (Get-ItemProperty $key -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
    Set-ItemProperty -Path $key -Name LongPathsEnabled -Value 1 -Type DWord -ErrorAction Stop
    $after = (Get-ItemProperty $key -Name LongPathsEnabled).LongPathsEnabled
    Write-Host "      LongPathsEnabled: $before -> $after" -ForegroundColor Green
    Write-Host '      (takes effect for new processes; reboot to be thorough)' -ForegroundColor DarkGray
}
catch {
    Write-Host "      FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

# Git also needs telling, independently of Windows.
try {
    git config --system core.longpaths true 2>$null
    Write-Host '      git core.longpaths = true' -ForegroundColor Green
} catch { }

# -----------------------------------------------------------------------------
# 3. Third-party antivirus - must be done in its own GUI
# -----------------------------------------------------------------------------
Write-Host ''
Write-Host '[3/3] Third-party antivirus - MANUAL STEP' -ForegroundColor Yellow
Write-Host ''
if ($defenderActive) {
    Write-Host '  Defender handled the exclusions above. If you ALSO run another'
    Write-Host '  real-time scanner (McAfee, Norton, Avast, Kaspersky, ...), add the'
    Write-Host '  same folders in its own settings - none of them expose a supported'
    Write-Host '  PowerShell interface, so this part cannot be scripted.'
} else {
    Write-Host '  Something other than Defender is scanning this machine in real time,'
    Write-Host '  and consumer AV products expose no supported PowerShell/CLI interface,'
    Write-Host '  so this part cannot be scripted. Do it once in your antivirus UI:'
    Write-Host ''
    Write-Host '    Settings / Options  >  Real-Time Scanning  >  Excluded Files'
    Write-Host '      >  Add folder'
    Write-Host ''
    Write-Host '  (In McAfee, for example: gear icon > Real-Time Scanning >'
    Write-Host '   Excluded Files > Add file / Add folder.)'
}
Write-Host ''
Write-Host '  Folders to exclude:' -ForegroundColor Cyan
foreach ($p in $paths) { Write-Host "      $p" }
Write-Host ''
Write-Host '  This is expected to be the biggest single build-time improvement.' -ForegroundColor Cyan
Write-Host ''
Write-Host '=== Done. Close and reopen your terminal. ===' -ForegroundColor Cyan
Write-Host ''
