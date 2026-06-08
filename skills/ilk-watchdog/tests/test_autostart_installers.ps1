<#
.SYNOPSIS
  Tests for the autostart installers (tray + scheduler).

.DESCRIPTION
  Runs each installer against a temp Startup directory (never touches the
  real Startup folder), asserts the .vbs exists + contains hidden mode,
  checks idempotency, and verifies -Uninstall removes the entry.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File skills/ilk-watchdog/tests/test_autostart_installers.ps1
#>
$ErrorActionPreference = "Stop"

$pass = 0
$fail = 0

function Assert($condition, $message) {
  if ($condition) {
    Write-Host "  PASS: $message"
    $script:pass++
  } else {
    Write-Host "  FAIL: $message"
    $script:fail++
  }
}

# ── Temp Startup dir (isolated from real system) ──────────────────────
$tempStartup = Join-Path ([System.IO.Path]::GetTempPath()) "ilk-autostart-test-$(Get-Random)"
New-Item -ItemType Directory -Path $tempStartup -Force | Out-Null

try {
  # ── Tray installer ────────────────────────────────────────────────────
  Write-Host "`n=== install-tray-autostart.ps1 ==="

  $trayInstaller = Join-Path $PSScriptRoot "..\..\..\tools\tray\install-tray-autostart.ps1"
  $trayInstaller = [System.IO.Path]::GetFullPath($trayInstaller)

  # Install
  & powershell -NoProfile -ExecutionPolicy Bypass -File $trayInstaller -StartupDir $tempStartup

  $trayVbs = Join-Path $tempStartup "ilk-tray.vbs"
  Assert (Test-Path $trayVbs) "ilk-tray.vbs created"

  if (Test-Path $trayVbs) {
    $content = Get-Content -Raw $trayVbs
    Assert ($content -match ',\s*0\s*,') "VBS contains hidden mode (, 0,)"
    Assert ($content -match 'WScript\.Shell') "VBS uses WScript.Shell"
  }

  # Idempotent: re-run should not duplicate
  & powershell -NoProfile -ExecutionPolicy Bypass -File $trayInstaller -StartupDir $tempStartup
  $vbsCount = (Get-ChildItem -Path $tempStartup -Filter "ilk-tray.vbs" -ErrorAction SilentlyContinue).Count
  Assert ($vbsCount -eq 1) "Idempotent: still exactly one ilk-tray.vbs after re-run"

  # Uninstall
  & powershell -NoProfile -ExecutionPolicy Bypass -File $trayInstaller -StartupDir $tempStartup -Uninstall
  Assert (-not (Test-Path $trayVbs)) "Uninstall removes ilk-tray.vbs"

  # ── Scheduler installer ───────────────────────────────────────────────
  Write-Host "`n=== install-scheduler-autostart.ps1 ==="

  $schedInstaller = Join-Path $PSScriptRoot "..\scripts\install-scheduler-autostart.ps1"
  $schedInstaller = [System.IO.Path]::GetFullPath($schedInstaller)

  # Install
  & powershell -NoProfile -ExecutionPolicy Bypass -File $schedInstaller -StartupDir $tempStartup

  $schedVbs = Join-Path $tempStartup "ilk-scheduler.vbs"
  Assert (Test-Path $schedVbs) "ilk-scheduler.vbs created"

  if (Test-Path $schedVbs) {
    $content = Get-Content -Raw $schedVbs
    Assert ($content -match ',\s*0\s*,') "VBS contains hidden mode (, 0,)"
    Assert ($content -match 'WScript\.Shell') "VBS uses WScript.Shell"
  }

  # Idempotent: re-run should not duplicate
  & powershell -NoProfile -ExecutionPolicy Bypass -File $schedInstaller -StartupDir $tempStartup
  $vbsCount = (Get-ChildItem -Path $tempStartup -Filter "ilk-scheduler.vbs" -ErrorAction SilentlyContinue).Count
  Assert ($vbsCount -eq 1) "Idempotent: still exactly one ilk-scheduler.vbs after re-run"

  # Uninstall
  & powershell -NoProfile -ExecutionPolicy Bypass -File $schedInstaller -StartupDir $tempStartup -Uninstall
  Assert (-not (Test-Path $schedVbs)) "Uninstall removes ilk-scheduler.vbs"

} finally {
  # Cleanup temp dir
  Remove-Item -LiteralPath $tempStartup -Recurse -Force -ErrorAction SilentlyContinue
}

# ── Summary ───────────────────────────────────────────────────────────
Write-Host "`n=== Summary ==="
Write-Host "Passed: $pass"
Write-Host "Failed: $fail"

if ($fail -gt 0) {
  exit 1
}
