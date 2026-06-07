<#
.SYNOPSIS
  Red test: Get-StartupSentinelAction must ignore stale sentinels and
  cross-check loop_status before declaring "QUEUE DRAINED".

.NOTES
  Invoked by local_checks in sub-plan 2026-06-07-watchdog-stale-sentinel-guard.
  Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$scratch  = Join-Path $repoRoot "scratch\watchdog-stale-sentinel"

# Clean slate
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

$tempProj = Join-Path $scratch "tempproj"
New-Item -ItemType Directory -Force -Path $tempProj | Out-Null

# --- AC-1: dot-source guard exposes Get-StartupSentinelAction ---
$env:ILK_DOTSOURCE_ONLY = '1'
$watchdogPath = Join-Path $repoRoot "skills\ilk-watchdog\scripts\watchdog.ps1"
try {
  . $watchdogPath -ProjectPath $tempProj
} catch {
  Write-Error "Dot-sourcing watchdog.ps1 failed: $_"
  exit 1
} finally {
  $env:ILK_DOTSOURCE_ONLY = $null
}

# Verify the helper exists
if (-not (Get-Command Get-StartupSentinelAction -ErrorAction SilentlyContinue)) {
  Write-Error "FAIL: Get-StartupSentinelAction function not found after dot-sourcing watchdog.ps1"
  exit 1
}

# --- AC-3: reproduction matrix ---
# Fixed launch time for deterministic tests
$launchTime = [datetime]'2026-06-07T12:58:00'
$SuccessStates = @('all-shipped', 'already-shipped', 'shipped')

$failures = @()

# Case 1: stale all-shipped + pending → 'stale-ignore'
# (sentinel ended before watchdog launched, loop_status says work pending)
$action = Get-StartupSentinelAction -State 'all-shipped' -EndedAt '2026-06-06T23:24:24' `
  -LaunchTime $launchTime -LoopStatusExit 1
if ($action -ne 'stale-ignore') {
  $failures += "stale all-shipped + pending: expected 'stale-ignore', got '$action'"
}

# Case 2: fresh all-shipped + all-shipped → 'advance'
# (sentinel ended after watchdog launched, loop_status confirms nothing pending)
$action = Get-StartupSentinelAction -State 'all-shipped' -EndedAt '2026-06-07T12:59:00' `
  -LaunchTime $launchTime -LoopStatusExit 0
if ($action -ne 'advance') {
  $failures += "fresh all-shipped + all-shipped: expected 'advance', got '$action'"
}

# Case 3: fresh all-shipped + pending → 'work-pending'
# (sentinel ended after watchdog launched, but loop_status says work pending)
$action = Get-StartupSentinelAction -State 'all-shipped' -EndedAt '2026-06-07T12:59:00' `
  -LaunchTime $launchTime -LoopStatusExit 1
if ($action -ne 'work-pending') {
  $failures += "fresh all-shipped + pending: expected 'work-pending', got '$action'"
}

# Case 4: non-success terminal state → 'classify'
# (regardless of freshness or loop_status, this goes to feedback classification)
$action = Get-StartupSentinelAction -State 'timeout-bound' -EndedAt '2026-06-07T12:59:00' `
  -LaunchTime $launchTime -LoopStatusExit 1
if ($action -ne 'classify') {
  $failures += "timeout state: expected 'classify', got '$action'"
}

# Case 5: stale + non-success → 'classify' (staleness only gates success states)
$action = Get-StartupSentinelAction -State 'timeout-bound' -EndedAt '2026-06-06T23:24:24' `
  -LaunchTime $launchTime -LoopStatusExit 1
if ($action -ne 'classify') {
  $failures += "stale + timeout: expected 'classify', got '$action'"
}

# Case 6: unparseable EndedAt + success + pending → 'work-pending'
# (skip freshness check, rely on cross-check)
$action = Get-StartupSentinelAction -State 'all-shipped' -EndedAt 'not-a-date' `
  -LaunchTime $launchTime -LoopStatusExit 1
if ($action -ne 'work-pending') {
  $failures += "unparseable EndedAt + pending: expected 'work-pending', got '$action'"
}

# Case 7: unparseable EndedAt + success + all-shipped → 'advance'
$action = Get-StartupSentinelAction -State 'all-shipped' -EndedAt 'not-a-date' `
  -LaunchTime $launchTime -LoopStatusExit 0
if ($action -ne 'advance') {
  $failures += "unparseable EndedAt + all-shipped: expected 'advance', got '$action'"
}

# Clean up
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: Get-StartupSentinelAction — all 7 matrix cases correct" -ForegroundColor Green
exit 0
