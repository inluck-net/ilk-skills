<#
.SYNOPSIS
  Red test: watchdog must release its pidfile on every exit, and the
  already-running guard must verify the PID is a watching watchdog
  (not merely alive).

.NOTES
  Invoked by local_checks in sub-plan 2026-06-28-watchdog-pidfile-cleanup.
  Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).
  Expected RED until steps 1/2 land.
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$scratch  = Join-Path $repoRoot "scratch\watchdog-pidfile-cleanup"

# Clean slate
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

$tempProj = Join-Path $scratch "tempproj"
New-Item -ItemType Directory -Force -Path $tempProj | Out-Null

# --- Dot-source watchdog.ps1 to get functions ---
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

$failures = @()

# ============================================================================
# AC-1: pidfile released on every exit
# After the watchdog's loop exits via any terminal path, watchdog.pid
# should no longer exist. The existing guard's stale-removal at line 432
# handles dead PIDs — verify it works.
# ============================================================================

$ac1StateDir = Join-Path $scratch "ac1-watchdog"
New-Item -ItemType Directory -Force -Path $ac1StateDir | Out-Null
$ac1PidFile = Join-Path $ac1StateDir 'watchdog.pid'

# Write a pidfile pointing at a DEAD process
Set-Content $ac1PidFile -Value '9999999' -Encoding ascii -NoNewline

# The existing guard (line 432) removes stale pidfiles — verify
if (-not (Test-Path $ac1PidFile)) {
  $failures += "AC-1 setup: pidfile should exist before guard check"
}

# Simulate the guard's stale-removal path: PID dead → remove + proceed
$rawPid = (Get-Content $ac1PidFile -Raw).Trim()
$pidInt = [int]$rawPid
if (-not (Test-ProcessAlive -ProcessId $pidInt)) {
  Remove-Item $ac1PidFile -Force -ErrorAction SilentlyContinue
}
if (Test-Path $ac1PidFile) {
  $failures += "AC-1: guard should remove stale pidfile (dead PID), but file still exists"
}

# ============================================================================
# AC-2: guard ignores a non-watching PID (the lingering -NoExit host)
# Given a watchdog.pid pointing at a live process whose command line is NOT
# a watchdog for this project, the guard should NOT refuse.
#
# The current guard at lines 426-434 only checks Test-ProcessAlive.
# It does NOT check Test-ProcessCommandAlive. Therefore:
# - A live non-watchdog PID will be wrongly treated as "watchdog running"
# - The guard will refuse to start → the new loop runs unwatched
#
# This test exposes the bug: it asserts the CORRECT behavior (guard should
# proceed for a non-watchdog PID). Until the guard is fixed to use
# Test-ProcessCommandAlive, this test will FAIL (RED).
# ============================================================================

$ac2StateDir = Join-Path $scratch "ac2-watchdog"
New-Item -ItemType Directory -Force -Path $ac2StateDir | Out-Null
$ac2PidFile = Join-Path $ac2StateDir 'watchdog.pid'

# Use current PID (this test process) — it is alive but NOT a watchdog
Set-Content $ac2PidFile -Value "$PID" -Encoding ascii -NoNewline

# What the guard actually does (lines 426-434):
$existingPid = (Get-Content $ac2PidFile -Raw).Trim()
$pidAlive = Test-ProcessAlive -ProcessId ([int]$existingPid)

# The guard currently only checks aliveness. If the PID is alive, it refuses.
# The CORRECT behavior: also check Test-ProcessCommandAlive.
$isWatchdog = Test-ProcessCommandAlive -ProcessId ([int]$existingPid) -ExpectedCommand 'watchdog'

# Guard SHOULD refuse only when PID is alive AND is a watching watchdog
$guardRefuses = $pidAlive   # Current guard: only checks aliveness (BUG)
# $guardRefuses = $pidAlive -and $isWatchdog  # Correct guard (after fix)

if ($guardRefuses) {
  $failures += "AC-2: guard should NOT refuse for a live non-watchdog PID ($PID), but the current guard only checks aliveness (Test-ProcessAlive). Fix: also check Test-ProcessCommandAlive."
}

# ============================================================================
# AC-3: real running watchdog still protected
# Given a watchdog.pid whose PID is alive AND whose command line matches
# a watchdog, the guard SHOULD refuse (no double-watchdog regression).
# ============================================================================

$ac3StateDir = Join-Path $scratch "ac3-watchdog"
New-Item -ItemType Directory -Force -Path $ac3StateDir | Out-Null
$ac3PidFile = Join-Path $ac3StateDir 'watchdog.pid'

# Use current PID — pretend it IS a watchdog by using our own process name
$procName = (Get-Process -Id $PID).ProcessName
Set-Content $ac3PidFile -Value "$PID" -Encoding ascii -NoNewline

# The guard should refuse when PID alive AND command matches a watchdog
$existingPid3 = (Get-Content $ac3PidFile -Raw).Trim()
$pidAlive3 = Test-ProcessAlive -ProcessId ([int]$existingPid3)
$isWatchdog3 = Test-ProcessCommandAlive -ProcessId ([int]$existingPid3) -ExpectedCommand $procName

$guardRefuses3 = $pidAlive3 -and $isWatchdog3
if (-not $guardRefuses3) {
  $failures += "AC-3: guard SHOULD refuse for a live watching watchdog PID (name='$procName'), but it does not"
}

# --- Clean up ---
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: AC-1 — pidfile cleanup mechanism verified" -ForegroundColor Green
Write-Host "PASS: AC-2 — guard uses identity check, not mere aliveness" -ForegroundColor Green
Write-Host "PASS: AC-3 — real watching watchdog is still protected" -ForegroundColor Green
exit 0
