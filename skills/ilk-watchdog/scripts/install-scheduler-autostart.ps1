<#
.SYNOPSIS
  Install or uninstall logon auto-start for the ilk scheduler daemon.

.DESCRIPTION
  Creates a per-user Startup .vbs launcher that starts scheduler.ps1 truly
  hidden at logon (WScript.Shell.Run …, 0 — no console flash). Idempotent:
  re-running does not duplicate the entry.

  Also removes any stale ilk-scheduler.lnk left by a previous .lnk-based
  installer so there is no duplicate entry.

.PARAMETER Uninstall
  Remove the auto-start entry instead of installing it.

.PARAMETER StartupDir
  Override the Startup folder path (for testing). Default: per-user Startup.

.EXAMPLE
  powershell -NoProfile -File skills/ilk-watchdog/scripts/install-scheduler-autostart.ps1
  Install auto-start (idempotent).

.EXAMPLE
  powershell -NoProfile -File skills/ilk-watchdog/scripts/install-scheduler-autostart.ps1 -Uninstall
  Remove auto-start.
#>
param(
  [switch]$Uninstall,
  [string]$StartupDir
)

$ErrorActionPreference = "Stop"

# ── Resolve paths (follow reparse points / junctions) ─────────────────
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
$SchedulerPath = Join-Path $ScriptDir "scheduler.ps1"

if (-not (Test-Path $SchedulerPath)) {
  throw "scheduler.ps1 not found: $SchedulerPath"
}

if (-not $StartupDir) {
  $StartupDir = [Environment]::GetFolderPath("Startup")
}
$VbsPath = Join-Path $StartupDir "ilk-scheduler.vbs"
$StaleLnk = Join-Path $StartupDir "ilk-scheduler.lnk"

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

# Remove stale .lnk from a previous .lnk-based installer.
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
$psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$SchedulerPath`""

$vbsContent = @"
Set objShell = CreateObject("WScript.Shell")
objShell.Run """$psExe"" $psArgs", 0, False
"@

# Write UTF-8 without BOM.
[System.IO.File]::WriteAllText($VbsPath, $vbsContent, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Installed: $VbsPath"
Write-Host "Target:    $psExe $psArgs"
Write-Host ""
Write-Host "The scheduler will start automatically at next logon."
Write-Host "To remove: powershell -NoProfile -File skills/ilk-watchdog/scripts/install-scheduler-autostart.ps1 -Uninstall"
