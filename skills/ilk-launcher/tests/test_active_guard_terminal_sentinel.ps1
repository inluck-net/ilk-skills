<#
.SYNOPSIS
  Red test: Test-RunningPid must treat a live PID whose sentinel is
  terminal (and pid-matched) as not-running — so a finished-but-lingering
  loop window doesn't block relaunch.

.NOTES
  Invoked by local_checks in sub-plan 2026-06-07-launcher-terminal-sentinel-guard.
  Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$scratch  = Join-Path $repoRoot "scratch\launcher-terminal-sentinel"

# Clean slate
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

# Set up isolated ILK_DATA_HOME so ilk_paths resolves to scratch
$origDataHome = $env:ILK_DATA_HOME
$env:ILK_DATA_HOME = Join-Path $scratch "ilk-data"
New-Item -ItemType Directory -Force -Path $env:ILK_DATA_HOME | Out-Null

$tempProj = Join-Path $scratch "tempproj"
New-Item -ItemType Directory -Force -Path $tempProj | Out-Null
# git init so ilk_paths finds a project root
& git init -q $tempProj 2>$null

# --- Dot-source launch.ps1 with guard ---
$env:ILK_DOTSOURCE_ONLY = '1'
$launcherPath = Join-Path $repoRoot "skills\ilk-launcher\scripts\launch.ps1"
try {
  . $launcherPath -ProjectPath $tempProj
} catch {
  Write-Error "Dot-sourcing launch.ps1 failed: $_"
  exit 1
} finally {
  $env:ILK_DOTSOURCE_ONLY = $null
}

# Verify the function exists
if (-not (Get-Command Test-RunningPid -ErrorAction SilentlyContinue)) {
  Write-Error "FAIL: Test-RunningPid function not found after dot-sourcing launch.ps1"
  exit 1
}

# --- Resolve paths via ilk_paths.py ---
$resolver = Join-Path $repoRoot "skills\ilk-loop\scripts\ilk_paths.py"
$json = & python $resolver --start $tempProj 2>$null
if ($LASTEXITCODE -ne 0 -or -not $json) {
  Write-Error "FAIL: ilk_paths.py failed to resolve for $tempProj"
  exit 1
}
$obj = $json | ConvertFrom-Json
$launcherDir = [string]$obj.external_launcher_dir
$runtimeDir  = [string]$obj.external_runtime_dir

if (-not $launcherDir -or -not $runtimeDir) {
  Write-Error "FAIL: ilk_paths.py did not return launcher_dir or runtime_dir"
  exit 1
}

# Ensure dirs exist
New-Item -ItemType Directory -Force -Path $launcherDir | Out-Null
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

$pidFile    = Join-Path $launcherDir 'running.pid'
$sentinelFile = Join-Path $runtimeDir 'last-exit.json'

$failures = @()
$alivePid = $PID  # test process's own PID — guaranteed alive

# ============================================================
# AC-1: alive PID + terminal sentinel (all-shipped, pid match)
#        → returns $null AND removes pid file
# ============================================================
Write-Host "--- AC-1: alive + all-shipped + pid match ---"
$alivePid | Out-File -FilePath $pidFile -Encoding ascii -NoNewline
@{ state = 'all-shipped'; pid = $alivePid } | ConvertTo-Json |
  Out-File -FilePath $sentinelFile -Encoding utf8

$result = Test-RunningPid -ProjectPath $tempProj
if ($null -ne $result) {
  $failures += "AC-1: expected `$null (allow relaunch), got '$result'"
}
if (Test-Path $pidFile) {
  $failures += "AC-1: pid file should be removed but still exists"
}

# ============================================================
# AC-2: alive PID + running sentinel (pid match)
#        → returns PID (blocks), pid file stays
# ============================================================
Write-Host "--- AC-2: alive + running + pid match ---"
$alivePid | Out-File -FilePath $pidFile -Encoding ascii -NoNewline
@{ state = 'running'; pid = $alivePid } | ConvertTo-Json |
  Out-File -FilePath $sentinelFile -Encoding utf8

$result = Test-RunningPid -ProjectPath $tempProj
if ($result -ne $alivePid) {
  $failures += "AC-2: expected PID $alivePid (block), got '$result'"
}
if (-not (Test-Path $pidFile)) {
  $failures += "AC-2: pid file should still exist but was removed"
}

# ============================================================
# AC-3: dead PID → returns $null, pid file removed
#        (existing behavior — no sentinel needed)
# ============================================================
Write-Host "--- AC-3: dead PID ---"
# Find a PID that is definitely dead
$deadPid = 2147483647  # max int32 — almost certainly unused
while (Get-Process -Id $deadPid -ErrorAction SilentlyContinue) {
  $deadPid--
  if ($deadPid -lt 1000) { Write-Error "FAIL: could not find a dead PID"; exit 1 }
}
$deadPid | Out-File -FilePath $pidFile -Encoding ascii -NoNewline
# No sentinel needed for dead-PID path

$result = Test-RunningPid -ProjectPath $tempProj
if ($null -ne $result) {
  $failures += "AC-3: expected `$null for dead PID, got '$result'"
}
if (Test-Path $pidFile) {
  $failures += "AC-3: pid file should be removed for dead PID"
}

# ============================================================
# AC-4a: alive PID + no sentinel → returns PID (conservative block)
# ============================================================
Write-Host "--- AC-4a: alive + no sentinel ---"
$alivePid | Out-File -FilePath $pidFile -Encoding ascii -NoNewline
if (Test-Path $sentinelFile) { Remove-Item $sentinelFile -Force }

$result = Test-RunningPid -ProjectPath $tempProj
if ($result -ne $alivePid) {
  $failures += "AC-4a: expected PID $alivePid (no sentinel → block), got '$result'"
}

# ============================================================
# AC-4b: alive PID + terminal sentinel but pid MISMATCH
#        → returns PID (conservative block — ambiguous)
# ============================================================
Write-Host "--- AC-4b: alive + terminal + pid mismatch ---"
$alivePid | Out-File -FilePath $pidFile -Encoding ascii -NoNewline
@{ state = 'all-shipped'; pid = ($alivePid + 1000) } | ConvertTo-Json |
  Out-File -FilePath $sentinelFile -Encoding utf8

$result = Test-RunningPid -ProjectPath $tempProj
if ($result -ne $alivePid) {
  $failures += "AC-4b: expected PID $alivePid (pid mismatch → block), got '$result'"
}

# ============================================================
# AC-4c: alive PID + non-terminal sentinel (e.g. interrupted)
#        → returns PID (conservative block)
# ============================================================
Write-Host "--- AC-4c: alive + non-terminal sentinel ---"
$alivePid | Out-File -FilePath $pidFile -Encoding ascii -NoNewline
@{ state = 'interrupted'; pid = $alivePid } | ConvertTo-Json |
  Out-File -FilePath $sentinelFile -Encoding utf8

$result = Test-RunningPid -ProjectPath $tempProj
if ($result -ne $alivePid) {
  $failures += "AC-4c: expected PID $alivePid (non-terminal → block), got '$result'"
}

# --- Clean up ---
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}
if ($origDataHome) { $env:ILK_DATA_HOME = $origDataHome } else { Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue }

if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: Test-RunningPid — all 6 AC cases correct" -ForegroundColor Green
exit 0
