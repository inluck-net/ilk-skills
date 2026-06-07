<#
.SYNOPSIS
  Red test: scheduler.ps1 -Detach -DryRun must print the planned spawn and
  return without entering the daemon loop or spawning a child process.

.NOTES
  Invoked by local_checks in sub-plan 2026-06-07-scheduler-detach.
  Exit 0 = green (all ACs pass), exit 1 = red (guard missing or wrong).
#>

$ErrorActionPreference = "Stop"
$failures = @()

$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$schedulerPs1 = Join-Path $repoRoot "skills\ilk-watchdog\scripts\scheduler.ps1"
$schedulerSh  = Join-Path $repoRoot "skills\ilk-watchdog\scripts\scheduler.sh"

# --- AC-1: -Detach -DryRun -Once prints planned spawn and exits 0 ---
Write-Host "=== AC-1: -Detach -DryRun -Once ==="

$output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $schedulerPs1 -Detach -DryRun -Once 2>&1
$exitCode = $LASTEXITCODE
$outputStr = ($output | Out-String).Trim()

if ($exitCode -ne 0) {
  $failures += "AC-1: exit code $exitCode (expected 0). Output: $outputStr"
}

# Must contain scheduler.ps1 in the inner command
if ($outputStr -notmatch 'scheduler\.ps1') {
  $failures += "AC-1: output does not contain 'scheduler.ps1'. Output: $outputStr"
}

# Must contain -MaxConcurrent in the inner command
if ($outputStr -notmatch '-MaxConcurrent') {
  $failures += "AC-1: output does not contain '-MaxConcurrent'. Output: $outputStr"
}

# Must NOT contain -Detach in the inner command (the respawn runs without it)
if ($outputStr -match '-Detach') {
  $failures += "AC-1: output contains '-Detach' (should be stripped in respawn). Output: $outputStr"
}

# Must not have spawned a lingering child powershell process
# (dry-run must not call Start-Process)
# Quick heuristic: the word "spawned" should NOT appear (only "would spawn")
if ($outputStr -match '\bspawned\b') {
  $failures += "AC-1: output says 'spawned' — a real process was launched in dry-run. Output: $outputStr"
}

# --- AC-2: -DryRun -Once (no -Detach) still works (regression guard) ---
Write-Host "=== AC-2: -DryRun -Once (no -Detach) ==="

$output2 = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $schedulerPs1 -DryRun -Once 2>&1
$exitCode2 = $LASTEXITCODE
$outputStr2 = ($output2 | Out-String).Trim()

if ($exitCode2 -ne 0) {
  $failures += "AC-2: exit code $exitCode2 (expected 0). Output: $outputStr2"
}

# Should produce a scan-decision line (dispatch, idle, skip-*, etc.)
if ($outputStr2 -notmatch '"decision"') {
  $failures += "AC-2: output does not contain a JSON decision line. Output: $outputStr2"
}

# --- AC-3: bash parity — scheduler.sh --detach --dry-run --once ---
Write-Host "=== AC-3: bash parity ==="

$hasBash   = [bool](Get-Command bash -ErrorAction SilentlyContinue)
$hasScreen = [bool](Get-Command screen -ErrorAction SilentlyContinue)

if ($hasBash -and $hasScreen) {
  $output3 = & bash $schedulerSh --detach --dry-run --once 2>&1
  $exitCode3 = $LASTEXITCODE
  $outputStr3 = ($output3 | Out-String).Trim()

  if ($exitCode3 -ne 0) {
    $failures += "AC-3: bash exit code $exitCode3 (expected 0). Output: $outputStr3"
  }

  # Must print the intended screen re-invocation (without --detach)
  if ($outputStr3 -notmatch 'screen') {
    $failures += "AC-3: output does not mention 'screen'. Output: $outputStr3"
  }
  if ($outputStr3 -notmatch 'scheduler\.sh') {
    $failures += "AC-3: output does not contain 'scheduler.sh'. Output: $outputStr3"
  }
  # The re-invocation must NOT contain --detach
  if ($outputStr3 -match '--detach') {
    $failures += "AC-3: output contains '--detach' (should be stripped in respawn). Output: $outputStr3"
  }
} else {
  Write-Host "SKIP AC-3: bash=$hasBash screen=$hasScreen (both required)" -ForegroundColor Yellow
}

# --- verdict ---
if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: scheduler -Detach dry-run — all ACs satisfied" -ForegroundColor Green
exit 0
