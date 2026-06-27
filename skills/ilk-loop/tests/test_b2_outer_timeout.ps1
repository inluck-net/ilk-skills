<#
.SYNOPSIS
  Regression test: B2 outer-timeout must honor declared per-check timeout.
  Expected to FAIL until the fix in step 1 lands (step 0 only gates parsing).

.DESCRIPTION
  Verifies three acceptance criteria:
  - AC-1: outer cap >= declared per-check timeout (slow-but-passing check completes)
  - AC-2: real per-check timeout still fails (check exceeding its own timeout = fail)
  - AC-3: self-inflicted kill (outer cap fires) is inconclusive, not a blocking error

.NOTES
  Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).
  Mirrors style of test_runner_outcome_allpassed.ps1.
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$scratch  = Join-Path $repoRoot "scratch\b2-outer-timeout"

# ── Clean slate ──────────────────────────────────────────────────────
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

# ── Dot-source runner ────────────────────────────────────────────────
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

# Verify functions are available
if (-not (Get-Command Invoke-LocalChecks -ErrorAction SilentlyContinue)) {
  Write-Error "FAIL: Invoke-LocalChecks not found after dot-sourcing runner"
  exit 1
}
if (-not (Get-Command Get-LocalCheckOutcome -ErrorAction SilentlyContinue)) {
  Write-Error "FAIL: Get-LocalCheckOutcome not found after dot-sourcing runner"
  exit 1
}

# ── Fixture: temp project with external plans dir ────────────────────
# find_project_root walks up from the scratch dir to the real git root,
# so the project key is the real repo's key. We set ILK_DATA_HOME to
# redirect the external plans dir to our scratch area.
$helper = Join-Path $repoRoot "skills\ilk-loop\scripts\run_local_checks.py"
if (-not (Test-Path $helper)) {
  Write-Error "FAIL: helper not found at $helper"
  exit 1
}

$tempProj = Join-Path $scratch "tempproj"
New-Item -ItemType Directory -Force -Path $tempProj | Out-Null

# Redirect ILK_DATA_HOME so ilk_paths.py resolves plans under our scratch
$savedDataHome = $env:ILK_DATA_HOME
$env:ILK_DATA_HOME = Join-Path $scratch "ilk-data"

# Resolve the external plans dir that ilk_paths.py will use for this project
$resolver = Join-Path $repoRoot "skills\ilk-loop\scripts\ilk_paths.py"
try {
  $plansJson = & python $resolver --start $tempProj 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $plansJson) {
    Write-Error "FAIL: ilk_paths.py could not resolve plans dir for $tempProj"
    exit 1
  }
  $plansObj = $plansJson | ConvertFrom-Json
  $extPlansDir = $plansObj.external_plans_dir
} catch {
  Write-Error "FAIL: ilk_paths.py failed: $_"
  exit 1
}
# Create the external plans dir and populate it
New-Item -ItemType Directory -Force -Path $extPlansDir | Out-Null

# Minimal MASTER so find_plans_dir returns this dir
@'
---
master_plan: test-b2-outer-timeout
batch_date: 2026-06-28
status: active
---
# Test MASTER
'@ | Set-Content -Path (Join-Path $extPlansDir "MASTER-test.md") -Encoding utf8

# Sub-plan with three steps exercising different timeout scenarios.
# Uses single-quoted here-string (literal backticks) + __SLUG__ placeholder.
$slug = "test-b2-outer-timeout"
(@'
---
plan: __SLUG__
status: in-progress
current_step: 0
estimated_steps: 3
---

# Test sub-plan for B2 outer-timeout

## Steps

### Step 0 — slow check within declared timeout
```yaml
local_checks:
  - command: "python -c \"import time; time.sleep(2)\""
    timeout: 30
```

### Step 1 — check exceeds its own declared timeout
```yaml
local_checks:
  - command: "python -c \"import time; time.sleep(15)\""
    timeout: 10
```

### Step 2 — outer cap kills the helper
```yaml
local_checks:
  - command: "python -c \"import time; time.sleep(10)\""
    timeout: 30
```
'@).Replace('__SLUG__', $slug) | Set-Content -Path (Join-Path $extPlansDir "$slug.md") -Encoding utf8

# ── AC matrix ────────────────────────────────────────────────────────
$failures = @()

# --- AC-1: outer cap >= declared per-check timeout ---
# The 2s command declares timeout:30. After the fix, the outer cap is derived
# from the declared timeout (>=30s + margin), so the check completes and
# returns 'pass'. Before the fix, the 5s outer cap kills the helper → 'error'.
$ac1_targets = @(
  [PSCustomObject]@{ slug = $slug; step = 0 }
)
$ac1_results = Invoke-LocalChecks -Project $tempProj -Targets $ac1_targets `
  -HelperScript $helper -OuterTimeoutSec 5
$r = $ac1_results[0]
if ($r.outcome -ne 'pass') {
  $failures += "AC-1: outer cap should allow declared timeout to complete; got outcome='$($r.outcome)' (expected 'pass'). exit_code=$($r.exit_code) raw=$($r.raw)"
}

# --- AC-2: real per-check timeout still fails ---
# A check whose command sleeps 15s but declares timeout:10 must be caught by
# run_local_checks.py's own timeout enforcement → fail. The outer cap
# (derived from declared: >=70s) is generous, but the helper's own 10s
# timeout fires first.
$ac2_targets = @(
  [PSCustomObject]@{ slug = $slug; step = 1 }
)
$ac2_results = Invoke-LocalChecks -Project $tempProj -Targets $ac2_targets `
  -HelperScript $helper -OuterTimeoutSec 120
$r = $ac2_results[0]
if ($r.outcome -ne 'fail') {
  $failures += "AC-2: check exceeding its own declared timeout must be 'fail'; got '$($r.outcome)' (exit_code=$($r.exit_code))"
}

# --- AC-3: self-inflicted kill is inconclusive ---
# Declared timeout=10, so per-target cap = max(10+60, 5) = 70s. The command
# sleeps 75s (>70s per-target cap). The helper's own timeout is 120s (won't
# fire). The outer cap fires at ~70s, killing the helper. Outcome must be
# 'inconclusive' (not 'error' or 'fail').
$ac3_slug = "test-b2-outer-timeout-kill"
(@'
---
plan: __SLUG__
status: in-progress
current_step: 0
estimated_steps: 1
---

# Test sub-plan for AC-3 kill path

## Steps

### Step 0 — command exceeds outer cap but not helper timeout
```yaml
local_checks:
  - command: "python -c \"import time; time.sleep(75)\""
    timeout: 10
```
'@).Replace('__SLUG__', $ac3_slug) | Set-Content -Path (Join-Path $extPlansDir "$ac3_slug.md") -Encoding utf8

$ac3_targets = @(
  [PSCustomObject]@{ slug = $ac3_slug; step = 0 }
)
$ac3_results = Invoke-LocalChecks -Project $tempProj -Targets $ac3_targets `
  -HelperScript $helper -OuterTimeoutSec 5
$r = $ac3_results[0]
# When the outer cap kills the helper, exit_code and raw must be null
if ($null -ne $r.exit_code) {
  $failures += "AC-3: outer-cap kill must produce null exit_code; got $($r.exit_code)"
}
if ($null -ne $r.raw) {
  $failures += "AC-3: outer-cap kill must produce null raw; got $($r.raw)"
}
# After the fix, the outcome must be 'inconclusive', not 'error' or 'fail'.
if ($r.outcome -ne 'inconclusive') {
  $failures += "AC-3: self-inflicted kill must be 'inconclusive'; got '$($r.outcome)'"
}

# ── Teardown ─────────────────────────────────────────────────────────
$env:ILK_DATA_HOME = $savedDataHome
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: B2 outer-timeout — all 3 ACs correct" -ForegroundColor Green
exit 0
