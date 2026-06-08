<#
.SYNOPSIS
  Install or uninstall logon auto-start for the ilk system-tray monitor.

.DESCRIPTION
  Creates a per-user Startup shortcut (.lnk) that launches ilk-tray.ps1
  detached at logon. Idempotent: re-running does not duplicate the entry.

  Uses WScript.Shell to create the shortcut (no admin required).
  The shortcut targets powershell.exe with -WindowStyle Hidden so the
  tray starts silently.

.PARAMETER Uninstall
  Remove the auto-start entry instead of installing it.

.PARAMETER IntervalSec
  Refresh interval in seconds passed to ilk-tray.ps1. Default: 10.

.EXAMPLE
  powershell -NoProfile -File tools/tray/install-tray-autostart.ps1
  Install auto-start (idempotent).

.EXAMPLE
  powershell -NoProfile -File tools/tray/install-tray-autostart.ps1 -Uninstall
  Remove auto-start.
#>
param(
  [switch]$Uninstall,
  [int]$IntervalSec = 10
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

$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupDir "ilk-tray.lnk"

# ── Uninstall ─────────────────────────────────────────────────────────
if ($Uninstall) {
  if (Test-Path $ShortcutPath) {
    Remove-Item -LiteralPath $ShortcutPath -Force
    Write-Host "Removed: $ShortcutPath"
  } else {
    Write-Host "No auto-start entry found (nothing to remove)."
  }
  return
}

# ── Install (idempotent) ──────────────────────────────────────────────
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)

# Always refresh the target and arguments so an update to this script
# propagates on re-run (idempotent = same file, not same content).
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$IlkTrayPath`" -IntervalSec $IntervalSec"
$shortcut.WorkingDirectory = $ScriptDir
$shortcut.Description = "ilk system-tray monitor (auto-start)"
$shortcut.WindowStyle = 7  # Minimized (hidden)
$shortcut.Save()

Write-Host "Installed: $ShortcutPath"
Write-Host "Target:    powershell.exe -NoProfile -WindowStyle Hidden -File `"$IlkTrayPath`""
Write-Host ""
Write-Host "The tray will start automatically at next logon."
Write-Host "To remove: powershell -NoProfile -File tools/tray/install-tray-autostart.ps1 -Uninstall"
