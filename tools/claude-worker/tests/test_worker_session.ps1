# Unit tests for _worker_session.ps1 — verifies AC-1/2/3.
# Run: powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/claude-worker/tests/test_worker_session.ps1

$ErrorActionPreference = "Stop"

# Resolve paths
$TestDir = $PSScriptRoot
$HelperPath = Join-Path $TestDir "..\_worker_session.ps1"
if (-not (Test-Path -LiteralPath $HelperPath)) {
  Write-Error "Helper not found at $HelperPath"
  exit 1
}
. $HelperPath

# Use a temp directory for test sentinels
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) "worker-session-test-$(Get-Random)"
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

$passed = 0
$failed = 0

function Assert-True($label, $result) {
  if ($result -eq $true) {
    Write-Host "  PASS: $label" -ForegroundColor Green
    $script:passed++
  } else {
    Write-Host "  FAIL: $label (expected true, got $result)" -ForegroundColor Red
    $script:failed++
  }
}

function Assert-False($label, $result) {
  if ($result -eq $false) {
    Write-Host "  PASS: $label" -ForegroundColor Green
    $script:passed++
  } else {
    Write-Host "  FAIL: $label (expected false, got $result)" -ForegroundColor Red
    $script:failed++
  }
}

try {
  Write-Host "=== AC-1: dead PID -> false ==="
  $sentinelFile = Join-Path $TempDir "ac1.pid"
  # Write a sentinel with a PID that almost certainly doesn't exist
  Set-Content -LiteralPath $sentinelFile -Value "pid=999999`nstart=2000-01-01T00:00:00.0000000Z`nkind=claude-worker" -Encoding ascii
  Assert-False "dead PID returns false" (Test-WorkerSessionActive -PidFile $sentinelFile)

  Write-Host ""
  Write-Host "=== AC-2: reused PID (wrong start time) -> false ==="
  $sentinelFile = Join-Path $TempDir "ac2.pid"
  # Write a sentinel with our own PID but a bogus start time
  Set-Content -LiteralPath $sentinelFile -Value "pid=$PID`nstart=2000-01-01T00:00:00.0000000Z`nkind=claude-worker" -Encoding ascii
  Assert-False "reused PID with wrong start time returns false" (Test-WorkerSessionActive -PidFile $sentinelFile)

  Write-Host ""
  Write-Host "=== AC-3: matching PID + start time -> true ==="
  $sentinelFile = Join-Path $TempDir "ac3.pid"
  # Write a sentinel with our own PID and its real start time
  $currentProc = Get-Process -Id $PID
  $realStart = $currentProc.StartTime.ToString("o")
  Set-Content -LiteralPath $sentinelFile -Value "pid=$PID`nstart=$realStart`nkind=claude-worker" -Encoding ascii
  Assert-True "matching PID + start time returns true" (Test-WorkerSessionActive -PidFile $sentinelFile)

  Write-Host ""
  Write-Host "=== Additional: missing file -> false ==="
  $sentinelFile = Join-Path $TempDir "nonexistent.pid"
  Assert-False "missing file returns false" (Test-WorkerSessionActive -PidFile $sentinelFile)

  Write-Host ""
  Write-Host "=== Additional: legacy bare-integer (alive) -> true ==="
  $sentinelFile = Join-Path $TempDir "legacy-alive.pid"
  Set-Content -LiteralPath $sentinelFile -Value "$PID" -Encoding ascii
  Assert-True "legacy bare-integer (alive PID) returns true" (Test-WorkerSessionActive -PidFile $sentinelFile)

  Write-Host ""
  Write-Host "=== Additional: legacy bare-integer (dead) -> false ==="
  $sentinelFile = Join-Path $TempDir "legacy-dead.pid"
  Set-Content -LiteralPath $sentinelFile -Value "999999" -Encoding ascii
  Assert-False "legacy bare-integer (dead PID) returns false" (Test-WorkerSessionActive -PidFile $sentinelFile)

  Write-Host ""
  Write-Host "=== Additional: Remove-WorkerSentinel idempotent ==="
  $sentinelFile = Join-Path $TempDir "remove-test.pid"
  Set-Content -LiteralPath $sentinelFile -Value "test" -Encoding ascii
  Remove-WorkerSentinel -PidFile $sentinelFile
  $removed1 = -not (Test-Path -LiteralPath $sentinelFile)
  Remove-WorkerSentinel -PidFile $sentinelFile
  $removed2 = $true  # second call should not throw
  if ($removed1) {
    Write-Host "  PASS: Remove-WorkerSentinel removes file" -ForegroundColor Green
    $script:passed++
  } else {
    Write-Host "  FAIL: Remove-WorkerSentinel did not remove file" -ForegroundColor Red
    $script:failed++
  }
  Write-Host "  PASS: Remove-WorkerSentinel idempotent (no throw on second call)" -ForegroundColor Green
  $script:passed++

} finally {
  # Cleanup temp directory
  Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== Results: $passed passed, $failed failed ==="
if ($failed -gt 0) {
  throw "Test failures detected."
}
exit 0
