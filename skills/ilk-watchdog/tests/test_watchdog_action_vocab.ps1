<#
.SYNOPSIS
  Red test: the watchdog's label→action mapping must be TOTAL — every label
  collect.py can emit must resolve to a known action (never UNKNOWN STATUS).

.NOTES
  Invoked by local_checks in sub-plan 2026-06-17-watchdog-status-vocab.
  Exit 0 = green (all ACs pass), exit 1 = red (mapping incomplete or wrong).

  Dot-sources watchdog.ps1 with ILK_DOTSOURCE_ONLY=1 and drives the pure
  Resolve-WatchdogAction function. Modelled on test_scheduler_stateless_blacklist.ps1.
#>
$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WatchdogScript = Join-Path $ScriptDir '..\scripts\watchdog.ps1'

$fail = $false
function Assert($cond, $msg) {
  if (-not $cond) { Write-Host "FAIL: $msg" -ForegroundColor Red; $script:fail = $true }
}

# --- dot-source the watchdog (functions only; no poll loop) ---
# watchdog.ps1 has mandatory -ProjectName; pass a dummy so the param block
# binds successfully.  The ILK_DOTSOURCE_ONLY guard at ~line 760 skips the
# main poll loop, so only function definitions and constants are loaded.
$env:ILK_DOTSOURCE_ONLY = '1'
try {
  . $WatchdogScript -ProjectName 'test-dummy'
} finally {
  Remove-Item Env:\ILK_DOTSOURCE_ONLY -ErrorAction SilentlyContinue
}

# AC-1: Resolve-WatchdogAction must be defined
Assert (Get-Command Resolve-WatchdogAction -ErrorAction SilentlyContinue) `
  "AC-1: Resolve-WatchdogAction must be defined after dot-sourcing watchdog.ps1"

if (-not (Get-Command Resolve-WatchdogAction -ErrorAction SilentlyContinue)) {
  Write-Host "RED: Resolve-WatchdogAction does not exist — cannot test further" -ForegroundColor Red
  exit 1
}

# AC-2: clean-success => stop-clean (NOT block/unknown)
$rClean = Resolve-WatchdogAction -Class 'clean-success'
Assert ($rClean -eq 'stop-clean') "AC-2: clean-success => 'stop-clean' (got '$rClean')"

# AC-3: self-hosting-drift => needs-human (NOT unknown)
$rDrift = Resolve-WatchdogAction -Class 'self-hosting-drift'
Assert ($rDrift -eq 'needs-human') "AC-3: self-hosting-drift => 'needs-human' (got '$rDrift')"

# AC-4: Whitelist labels => relaunch
foreach ($lbl in @('timeout-bound', 'max-iter-bound', 'api-flaky', 'interrupted')) {
  $r = Resolve-WatchdogAction -Class $lbl
  Assert ($r -eq 'relaunch') "AC-4: whitelist '$lbl' => 'relaunch' (got '$r')"
}

# AC-4: Blacklist labels => block
foreach ($lbl in @('stuck-no-progress', 'api-blocked', 'budget-exhausted', 'local-checks-stuck', 'dependency-unreachable')) {
  $r = Resolve-WatchdogAction -Class $lbl
  Assert ($r -eq 'block') "AC-4: blacklist '$lbl' => 'block' (got '$r')"
}

# AC-4: shipped-unverified => needs-human
$rUnver = Resolve-WatchdogAction -Class 'shipped-unverified'
Assert ($rUnver -eq 'needs-human') "AC-4: shipped-unverified => 'needs-human' (got '$rUnver')"

# AC-4: no-evidence => triage
$rNoEv = Resolve-WatchdogAction -Class 'no-evidence'
Assert ($rNoEv -eq 'triage') "AC-4: no-evidence => 'triage' (got '$rNoEv')"

# AC-5 (totality): every label collect.py can emit must resolve to non-unknown.
# This list is the L2 table's full label set. Update BOTH this list AND the L2
# table in orchestration-collaboration.md when adding a new label.
$AllLabels = @(
  'timeout-bound',
  'max-iter-bound',
  'api-flaky',
  'interrupted',
  'stuck-no-progress',
  'api-blocked',
  'budget-exhausted',
  'local-checks-stuck',
  'dependency-unreachable',
  'clean-success',
  'shipped-unverified',
  'self-hosting-drift',
  'no-evidence'
)

foreach ($lbl in $AllLabels) {
  $r = Resolve-WatchdogAction -Class $lbl
  Assert ($r -ne 'unknown') "AC-5: totality — '$lbl' must not resolve to 'unknown' (got '$r')"
  Assert ($null -ne $r) "AC-5: totality — '$lbl' must resolve to a non-null action"
}

# Fail-closed: a genuinely unknown label must resolve to 'block' (safe default),
# never silently pass or return unknown.
$rUnknown = Resolve-WatchdogAction -Class 'some-future-label-nobody-has-seen'
Assert ($rUnknown -eq 'block') "fail-closed: unknown label => 'block' (got '$rUnknown')"

if ($fail) {
  Write-Host "RED: watchdog label-action mapping is not total or incorrect" -ForegroundColor Red
  exit 1
}
Write-Host "PASS: Resolve-WatchdogAction — all labels resolve correctly, mapping is total" -ForegroundColor Green
exit 0
