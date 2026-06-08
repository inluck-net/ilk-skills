<#
.SYNOPSIS
  Install or uninstall logon auto-start for the ilk system-tray monitor.

.DESCRIPTION
  Creates a per-user Startup .vbs launcher that starts ilk-tray.ps1 truly
  hidden at logon (WScript.Shell.Run …, 0 — no console flash). Idempotent:
  re-running does not duplicate the entry.

  Also removes any stale ilk-tray.lnk left by the previous .lnk-based
  installer so there is no duplicate entry.

.PARAMETER Uninstall
  Remove the auto-start entry instead of installing it.

.PARAMETER IntervalSec
  Refresh interval in seconds passed to ilk-tray.ps1. Default: 10.

.PARAMETER StartupDir
  Override the Startup folder path (for testing). Default: per-user Startup.

.EXAMPLE
  powershell -NoProfile -File tools/tray/install-tray-autostart.ps1
  Install auto-start (idempotent).

.EXAMPLE
  powershell -NoProfile -File tools/tray/install-tray-autostart.ps1 -Uninstall
  Remove auto-start.
#>
param(
  [switch]$Uninstall,
  [int]$IntervalSec = 10,
  [string]$StartupDir
)

$ErrorActionPreference = "Stop"

# ── Resolve paths ─────────────────────────────────────────────────────
$src = $PSCommandPath
while ($true) {
  $item = Get-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue
  if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    $src = $item.Target
  } else {
    break
  }
}
$ScriptDir = Split-Path -Parent $src
$IlkTrayPath = Join-Path $ScriptDir "ilk-tray.ps1"

if (-not (Test-Path $IlkTrayPath)) {
  throw "ilk-tray.ps1 not found: $IlkTrayPath"
}

if (-not $StartupDir) {
  $StartupDir = [Environment]::GetFolderPath("Startup")
}
$VbsPath = Join-Path $StartupDir "ilk-tray.vbs"
$StaleLnk = Join-Path $StartupDir "ilk-tray.lnk"

# ── Uninstall ─────────────────────────────────────────────────────────
if ($Uninstall) {
  if (Test-Path $VbsPath) {
    Remove-Item -LiteralPath $VbsPath -Force
    Write-Host "Removed: $VbsPath"
  } else {
    Write-Host "No auto-start entry found (nothing to remove)."
  }
  return
}

# ── Install (idempotent) ──────────────────────────────────────────────

# Remove stale .lnk from the previous .lnk-based installer.
if (Test-Path $StaleLnk) {
  Remove-Item -LiteralPath $StaleLnk -Force
  Write-Host "Removed stale shortcut: $StaleLnk"
}

# Build the hidden .vbs launcher.
# Window style 0 = truly hidden (no flash, no taskbar entry).
$psExe = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
if (-not $psExe) {
  $psExe = "powershell.exe"
}
$psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$IlkTrayPath`" -IntervalSec $IntervalSec"

$vbsContent = @"
Set objShell = CreateObject("WScript.Shell")
objShell.Run """$psExe"" $psArgs", 0, False
"@

# Write UTF-8 without BOM (VBS doesn't need BOM and some engines choke on it).
[System.IO.File]::WriteAllText($VbsPath, $vbsContent, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Installed: $VbsPath"
Write-Host "Target:    $psExe $psArgs"
Write-Host ""
Write-Host "The tray will start automatically at next logon."
Write-Host "To remove: powershell -NoProfile -File tools/tray/install-tray-autostart.ps1 -Uninstall"
