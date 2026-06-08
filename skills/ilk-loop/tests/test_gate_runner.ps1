<#
.SYNOPSIS
  Tests for Invoke-LocalCheck — the per-check gate runner extracted from
  run_ilk_loop_claude.ps1.

.DESCRIPTION
  Validates AC-2 (bash invocation) and AC-3 (error classification):
    (a) grep -q against a file that contains the pattern → pass
    (b) grep -q against a file lacking it               → fail
    (c) bogus command (definitely-not-a-cmd)             → error

  Run: powershell -NoProfile -ExecutionPolicy Bypass -File test_gate_runner.ps1
#>

$ErrorActionPreference = "Stop"
$script:FailCount = 0

function Assert-Equal {
  param([string]$Label, [object]$Expected, [object]$Actual)
  if ($Expected -ne $Actual) {
    Write-Host "  FAIL: $Label — expected '$Expected', got '$Actual'" -ForegroundColor Red
    $script:FailCount++
  } else {
    Write-Host "  OK: $Label" -ForegroundColor Green
  }
}

function Assert-True {
  param([string]$Label, [bool]$Condition)
  if (-not $Condition) {
    Write-Host "  FAIL: $Label — expected true" -ForegroundColor Red
    $script:FailCount++
  } else {
    Write-Host "  OK: $Label" -ForegroundColor Green
  }
}

# --- Setup ------------------------------------------------------------------

# Dot-source the runner to get Invoke-LocalCheck + Resolve-BashPath
$runnerPath = Join-Path $PSScriptRoot "..\scripts\run_ilk_loop_claude.ps1"
. $runnerPath

# Verify the functions are available
Assert-True "Invoke-LocalCheck is defined" ([bool](Get-Command Invoke-LocalCheck -ErrorAction SilentlyContinue))
Assert-True "Resolve-BashPath is defined"  ([bool](Get-Command Resolve-BashPath  -ErrorAction SilentlyContinue))

# Resolve bash once
$bashPath = Resolve-BashPath
Assert-True "bash is available" ([bool]$bashPath)

if (-not $bashPath) {
  Write-Host "Cannot run tests without bash. Aborting." -ForegroundColor Red
  exit 1
}

# Create temp fixtures
$tmpDir = Join-Path ([IO.Path]::GetTempPath()) "ilk-test-gates-$(Get-Date -Format 'yyyyMMddHHmmss')"
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

$matchFile    = Join-Path $tmpDir "match.txt"
$noMatchFile  = Join-Path $tmpDir "nomatch.txt"
"hello world" | Set-Content -Path $matchFile -Encoding utf8
"goodbye moon" | Set-Content -Path $noMatchFile -Encoding utf8

Write-Host ""
Write-Host "=== AC-2: bash invocation (grep runs, not cmd.exe) ===" -ForegroundColor Cyan

# (a) grep -q against file that contains the pattern -> pass
# Use a relative path + -Cwd (exactly how the loop runs a sub-plan's checks).
$r1 = Invoke-LocalCheck -Command "grep -q 'hello' match.txt" -Cwd $tmpDir -TimeoutSec 10
Assert-Equal "(a) grep match -> outcome=pass" "pass" $r1.outcome
Assert-Equal "(a) grep match -> exit_code=0"   0      $r1.exit_code

# (b) grep -q against file that lacks the pattern -> fail
$r2 = Invoke-LocalCheck -Command "grep -q 'hello' nomatch.txt" -Cwd $tmpDir -TimeoutSec 10
Assert-Equal "(b) grep no-match -> outcome=fail" "fail" $r2.outcome
Assert-Equal "(b) grep no-match -> exit_code=1"   1      $r2.exit_code

Write-Host ""
Write-Host "=== AC-3: error blocks (unrunnable command → error, not pass) ===" -ForegroundColor Cyan

# (c) bogus command -> error
$r3 = Invoke-LocalCheck -Command "definitely-not-a-cmd-xyzzy" -TimeoutSec 10
Assert-Equal "(c) bogus cmd -> outcome=error" "error" $r3.outcome
Assert-True  "(c) bogus cmd -> exit_code is not 0 (or null)" ($r3.exit_code -ne 0 -or $r3.exit_code -eq $null)

Write-Host ""
Write-Host "=== AC-5: JSONL shape unchanged ===" -ForegroundColor Cyan

# The function returns a PSCustomObject with the expected fields
$expectedFields = @("outcome", "exit_code", "stdout", "stderr", "error")
foreach ($f in $expectedFields) {
  Assert-True "result has field '$f'" ($r1.PSObject.Properties.Name -contains $f)
}

# --- Cleanup ----------------------------------------------------------------

Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue

# --- Summary ----------------------------------------------------------------

Write-Host ""
if ($script:FailCount -gt 0) {
  Write-Host "FAILED: $($script:FailCount) assertion(s) failed" -ForegroundColor Red
  exit 1
} else {
  Write-Host "ALL PASSED" -ForegroundColor Green
  exit 0
}
