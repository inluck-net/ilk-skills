<#
.SYNOPSIS
  Red test: Get-LocalCheckOutcome must prefer all_passed over ExitCode
  when the helper JSON is available.

.NOTES
  Invoked by local_checks in sub-plan 2026-06-16-runner-trust-allpassed.
  Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$scratch  = Join-Path $repoRoot "scratch\runner-outcome-allpassed"

# Clean slate
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

$tempProj = Join-Path $scratch "tempproj"
New-Item -ItemType Directory -Force -Path $tempProj | Out-Null

# --- AC-1: dot-source guard exposes Get-LocalCheckOutcome ---
$env:ILK_DOTSOURCE_ONLY = '1'
$runnerPath = Join-Path $repoRoot "skills\ilk-loop\scripts\run_ilk_loop_claude.ps1"
try {
  . $runnerPath
} catch {
  Write-Error "Dot-sourcing run_ilk_loop_claude.ps1 failed: $_"
  exit 1
} finally {
  $env:ILK_DOTSOURCE_ONLY = $null
}

# Verify the helper exists
if (-not (Get-Command Get-LocalCheckOutcome -ErrorAction SilentlyContinue)) {
  Write-Error "FAIL: Get-LocalCheckOutcome function not found after dot-sourcing runner"
  exit 1
}

# --- AC-2/3/4: reproduction matrix ---
$failures = @()

# Case 1: parsed all_passed=$true + ExitCode $null → 'pass'
# (AC-1: passing helper with null/anomalous exit must be pass, not error)
$parsed = [PSCustomObject]@{ all_passed = $true; exit_code = 0 }
$result = Get-LocalCheckOutcome -Parsed $parsed -ExitCode $null
if ($result -ne 'pass') {
  $failures += "Case 1: all_passed=true + ExitCode=`$null: expected 'pass', got '$result'"
}

# Case 2: parsed all_passed=$false + ExitCode 0 → 'fail'
# (AC-2: failing helper must be fail regardless of exit code)
$parsed = [PSCustomObject]@{ all_passed = $false; exit_code = 1 }
$result = Get-LocalCheckOutcome -Parsed $parsed -ExitCode 0
if ($result -ne 'fail') {
  $failures += "Case 2: all_passed=false + ExitCode=0: expected 'fail', got '$result'"
}

# Case 3: parsed $null + ExitCode 0 → 'pass' (fallback)
$parsed = $null
$result = Get-LocalCheckOutcome -Parsed $parsed -ExitCode 0
if ($result -ne 'pass') {
  $failures += "Case 3: parsed=`$null + ExitCode=0: expected 'pass', got '$result'"
}

# Case 4: parsed $null + ExitCode 1 → 'fail' (fallback)
$result = Get-LocalCheckOutcome -Parsed $parsed -ExitCode 1
if ($result -ne 'fail') {
  $failures += "Case 4: parsed=`$null + ExitCode=1: expected 'fail', got '$result'"
}

# Case 5: parsed $null + ExitCode 7 → 'error' (fallback)
$result = Get-LocalCheckOutcome -Parsed $parsed -ExitCode 7
if ($result -ne 'error') {
  $failures += "Case 5: parsed=`$null + ExitCode=7: expected 'error', got '$result'"
}

# Clean up
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: Get-LocalCheckOutcome — all 5 matrix cases correct" -ForegroundColor Green
exit 0
