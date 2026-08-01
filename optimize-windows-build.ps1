# =============================================================================
# Sathify - Windows build environment optimizer
# =============================================================================
# RUN THIS ONCE, AS ADMINISTRATOR.
#
#   Right-click Start > "Terminal (Admin)" / "PowerShell (Admin)", then:
#     powershell -ExecutionPolicy Bypass -File D:\sathify\optimize-windows-build.ps1
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
#      package cache. On this machine it is currently DISABLED and it already
#      caused a real extraction failure during setup.
#
# Nothing here is destructive: it only adds exclusions and flips one
# documented registry flag. It is safe to re-run.
# =============================================================================

#Requires -RunAsAdministrator

$ErrorActionPreference = 'Continue'

Write-Host ''
Write-Host '=== Sathify Windows build optimizer ===' -ForegroundColor Cyan
Write-Host ''

# --- Paths that the build machinery hammers ---------------------------------
$paths = @(
    'D:\sathify',
    'C:\Users\Pradnya\flutter',
    'C:\Android\Sdk',
    'C:\Users\Pradnya\.gradle',
    'C:\Users\Pradnya\AppData\Local\Pub\Cache',
    'C:\Program Files\Eclipse Adoptium'
)

# --- Processes that do the hammering ----------------------------------------
$procs = @('java.exe', 'javaw.exe', 'dart.exe', 'flutter.bat', 'gradle.exe',
           'gradlew.bat', 'adb.exe', 'kotlin-daemon.exe', 'aapt2.exe')

# -----------------------------------------------------------------------------
# 1. Windows Defender exclusions
# -----------------------------------------------------------------------------
Write-Host '[1/3] Windows Defender exclusions...' -ForegroundColor Yellow
try {
    $null = Get-MpPreference -ErrorAction Stop

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
    Write-Host '      Defender service is not active on this machine' -ForegroundColor DarkGray
    Write-Host '      (McAfee is the registered antivirus). Skipping - see step 3.' -ForegroundColor DarkGray
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
# 3. McAfee - must be done in the GUI, no supported CLI exists
# -----------------------------------------------------------------------------
Write-Host ''
Write-Host '[3/3] McAfee exclusions - MANUAL STEP REQUIRED' -ForegroundColor Yellow
Write-Host ''
Write-Host '  McAfee is the ACTIVE real-time scanner on this machine and it'
Write-Host '  exposes no supported PowerShell/CLI interface, so this part cannot'
Write-Host '  be scripted. Do it once in the McAfee UI:'
Write-Host ''
Write-Host '    Open McAfee  >  gear icon / Settings  >  Real-Time Scanning'
Write-Host '      >  Excluded Files  >  Add file / Add folder'
Write-Host ''
Write-Host '  Add each of these folders:' -ForegroundColor Cyan
foreach ($p in $paths) { Write-Host "      $p" }
Write-Host ''
Write-Host '  This is expected to be the biggest single build-time improvement.' -ForegroundColor Cyan
Write-Host ''
Write-Host '=== Done. Close and reopen your terminal. ===' -ForegroundColor Cyan
Write-Host ''
