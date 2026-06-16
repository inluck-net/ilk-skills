<#
.SYNOPSIS
  Red test: the scheduler's blacklist decision must be STATELESS per cycle —
  driven only by the current on-disk decision, never by a cross-cycle
  in-memory accumulator.

.NOTES
  Invoked by local_checks in sub-plan 2026-06-16-scheduler-stateless-blacklist.
  Exit 0 = green (all ACs pass), exit 1 = red (wedge present or guard missing).

  The wedge (scheduler-inmemory-blacklist-wedge) could never be reproduced by
  the existing -DryRun -Once harness, because each -Once invocation starts a
  fresh process with an empty accumulator. So we dot-source the scheduler with
  ILK_DOTSOURCE_ONLY=1 and drive the pure Test-SchedulerSkip decision across two
  simulated cycles, asserting cycle 2's "not blacklisted on disk" flip is
  honoured even though cycle 1 was blacklisted.
#>
$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SchedulerScript = Join-Path $ScriptDir '..\scripts\scheduler.ps1'

$fail = $false
function Assert($cond, $msg) {
  if (-not $cond) { Write-Host "FAIL: $msg" -ForegroundColor Red; $script:fail = $true }
}

# --- dot-source the scheduler (functions only; no mutex, no poll loop) ---
$env:ILK_DOTSOURCE_ONLY = '1'
try {
  . $SchedulerScript
} finally {
  Remove-Item Env:\ILK_DOTSOURCE_ONLY -ErrorAction SilentlyContinue
}

Assert (Get-Command Test-SchedulerSkip -ErrorAction SilentlyContinue) `
  "Test-SchedulerSkip must be defined (extract the pure skip decision)"

$now = Get-Date
$future = $now.AddMinutes(30)

# --- Cycle 1: project blacklisted on disk this cycle -> skip ('blacklist') ---
$pm1 = @{ 'proj-x' = $future }
$bo  = @{}
$r1 = Test-SchedulerSkip -Key 'proj-x' -PostmortemBlacklist $pm1 -BackoffSkip $bo -Now $now
Assert ($r1 -eq 'blacklist') "cycle 1: blacklisted-on-disk -> 'blacklist' (got '$r1')"

# --- Cycle 2: on-disk decision FLIPS to not-blacklisted (resolve-ack/expiry/
#     clean-success) -> the project must be dispatchable, NOT kept skipping by a
#     stale entry. This is the wedge regression. ---
$pm2 = @{}   # fresh on-disk decision this cycle: NOT blacklisted
$r2 = Test-SchedulerSkip -Key 'proj-x' -PostmortemBlacklist $pm2 -BackoffSkip $bo -Now $now
Assert ($null -eq $r2) "cycle 2: not-blacklisted-on-disk -> dispatchable (got '$r2') [WEDGE]"

# --- Backoff still works (transient cross-cycle mechanism is separate) ---
$boActive = @{ 'proj-y' = $future }
$rb = Test-SchedulerSkip -Key 'proj-y' -PostmortemBlacklist @{} -BackoffSkip $boActive -Now $now
Assert ($rb -eq 'backoff') "backoff: active backoff -> 'backoff' (got '$rb')"

# --- Expired blacklist entry is ignored (now >= expiry) ---
$past = $now.AddMinutes(-5)
$rExp = Test-SchedulerSkip -Key 'proj-z' -PostmortemBlacklist @{ 'proj-z' = $past } -BackoffSkip @{} -Now $now
Assert ($null -eq $rExp) "expired blacklist -> dispatchable (got '$rExp')"

if ($fail) { Write-Host "RED: scheduler blacklist is not stateless per cycle" -ForegroundColor Red; exit 1 }
Write-Host "PASS: Test-SchedulerSkip — stateless per-cycle decision correct" -ForegroundColor Green
exit 0
