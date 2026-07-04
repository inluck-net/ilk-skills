<#
.SYNOPSIS
  Red test: Get-StartupSentinelAction must ignore stale sentinels and
  cross-check loop_status before declaring "QUEUE DRAINED". Also locks in
  the stale non-success + live-loop race contract (sub-plan
  2026-07-03-watchdog-stale-nonsuccess-ps).

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

# Case 4: fresh non-success + LoopAlive $false → 'classify'
# (fresh terminal — this run's own terminal, not a leftover)
$action = Get-StartupSentinelAction -State 'timeout-bound' -EndedAt '2026-06-07T12:59:00' `
  -LaunchTime $launchTime -LoopStatusExit 1 -LoopAlive $false
if ($action -ne 'classify') {
  $failures += "fresh non-success + dead: expected 'classify', got '$action'"
}

# Case 5: stale non-success + LoopAlive $false → 'classify'
# (stale non-success but no live loop — adjudicate a genuinely-dead run)
$action = Get-StartupSentinelAction -State 'timeout-bound' -EndedAt '2026-06-06T23:24:24' `
  -LaunchTime $launchTime -LoopStatusExit 1 -LoopAlive $false
if ($action -ne 'classify') {
  $failures += "stale non-success + dead: expected 'classify', got '$action'"
}

# Case 8: stale non-success + LoopAlive $true → 'stale-ignore'
# (THE INCIDENT: previous run's leftover sentinel, fresh loop coming up)
$action = Get-StartupSentinelAction -State 'local_checks_failed' -EndedAt '2026-06-06T23:24:24' `
  -LaunchTime $launchTime -LoopStatusExit 1 -LoopAlive $true
if ($action -ne 'stale-ignore') {
  $failures += "stale non-success + alive: expected 'stale-ignore', got '$action'"
}

# Case 9: fresh non-success + LoopAlive $true → 'classify'
# (this run's own terminal — not a leftover, even though loop pid is alive)
$action = Get-StartupSentinelAction -State 'local_checks_failed' -EndedAt '2026-06-07T13:00:00' `
  -LaunchTime $launchTime -LoopStatusExit 1 -LoopAlive $true
if ($action -ne 'classify') {
  $failures += "fresh non-success + alive: expected 'classify', got '$action'"
}

# Case 10: stale non-success + default LoopAlive (absent) → 'classify'
# (back-compat: existing call sites that don't pass LoopAlive get $false default)
$action = Get-StartupSentinelAction -State 'local_checks_failed' -EndedAt '2026-06-06T23:24:24' `
  -LaunchTime $launchTime -LoopStatusExit 1
if ($action -ne 'classify') {
  $failures += "stale non-success + default LoopAlive: expected 'classify', got '$action'"
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

# --- AC-4: Test-RunningPid stale-sentinel cross-check (scheduler parity) ---
# Inline the function from scheduler.ps1 to avoid triggering its main script.
function Test-RunningPid {
  param([string]$ProjectDataPath)
  $pidFile = Join-Path $ProjectDataPath 'runtime\launcher\running.pid'
  if (-not (Test-Path $pidFile)) { return $false }
  $raw = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue)
  if (-not $raw) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    return $false
  }
  $raw = $raw.Trim()
  if (-not $raw) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    return $false
  }
  try {
    $procId = [int]$raw
  } catch {
    return $false
  }
  if ($procId -le 0) { return $false }
  $alive = [bool](Get-Process -Id $procId -ErrorAction SilentlyContinue)
  if (-not $alive) { return $false }

  # Stale-sentinel cross-check
  $sentinelFile = Join-Path $ProjectDataPath 'runtime\last-exit.json'
  if (Test-Path $sentinelFile) {
    try {
      $sentinel = Get-Content $sentinelFile -Raw -ErrorAction Stop
      if ($sentinel) {
        $obj = $sentinel | ConvertFrom-Json -ErrorAction Stop
        if ($obj.state -and $obj.state -ne 'running') {
          return $false  # terminal state — project is free
        }
      }
    } catch {}
  }
  return $true
}

# Use current PID as a definitely-alive process
$alivePid = $PID
$staleProj = Join-Path $scratch "stale-proj"
$staleLauncher = Join-Path $staleProj "runtime\launcher"
New-Item -ItemType Directory -Force -Path $staleLauncher | Out-Null

# Case A: live pid + terminal last-exit.json → not-busy ($false)
Set-Content (Join-Path $staleLauncher 'running.pid') -Value $alivePid
$sentinelDir = Join-Path $staleProj "runtime"
$sentinel = @{ state = "all-shipped"; pid = $alivePid; run_id = "test"; iterations = 3 }
($sentinel | ConvertTo-Json) | Set-Content (Join-Path $sentinelDir 'last-exit.json') -Encoding UTF8
$result = Test-RunningPid -ProjectDataPath $staleProj
if ($result -ne $false) {
  $failures += "Test-RunningPid: live pid + terminal last-exit.json expected `$false (free), got `$result"
}

# Case B: live pid + state=running → busy ($true) — no regression
$sentinelRunning = @{ state = "running"; pid = $alivePid; run_id = "test"; iterations = 1 }
($sentinelRunning | ConvertTo-Json) | Set-Content (Join-Path $sentinelDir 'last-exit.json') -Encoding UTF8
$result = Test-RunningPid -ProjectDataPath $staleProj
if ($result -ne $true) {
  $failures += "Test-RunningPid: live pid + state=running expected `$true (busy), got `$result"
}

# Case C: live pid + no last-exit.json → busy ($true)
Remove-Item (Join-Path $sentinelDir 'last-exit.json') -ErrorAction SilentlyContinue
$result = Test-RunningPid -ProjectDataPath $staleProj
if ($result -ne $true) {
  $failures += "Test-RunningPid: live pid + no sentinel expected `$true (busy), got `$result"
}

# Clean up
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: Get-StartupSentinelAction — all 10 matrix cases correct" -ForegroundColor Green
Write-Host "PASS: Test-RunningPid stale-sentinel cross-check — all 3 cases correct" -ForegroundColor Green
exit 0
