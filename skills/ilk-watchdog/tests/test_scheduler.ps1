<#
.SYNOPSIS
  Cross-platform test harness for the cross-project scheduler.

.DESCRIPTION
  Subcommands:
    scan — build a fake ILK_DATA_HOME with 2 projects (one all-shipped,
           one with a queued sub-plan) and assert scheduler_scan.py lists
           ONLY the queued one.
#>
param(
  [Parameter(Mandatory)]
  [ValidateSet('scan')]
  [string]$Subcommand
)

$ErrorActionPreference = 'Stop'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Resolve-Path (Join-Path $ScriptDir '..\..\..')
$ScanScript = Join-Path $ScriptDir '..\scripts\scheduler_scan.py'
$Scratch    = Join-Path $RepoRoot 'scratch\sched-test'
$FakeData   = Join-Path $Scratch 'ilk-data'

function Cleanup {
  if (Test-Path $Scratch) {
    Remove-Item $Scratch -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Setup-FakeData {
  Cleanup

  $projectsDir = Join-Path $FakeData 'projects'
  New-Item -ItemType Directory -Path $projectsDir -Force | Out-Null

  # --- Project A: all-shipped (should be excluded from scan) ---
  $projA = Join-Path $projectsDir 'proj-a'
  $plansA = Join-Path $projA 'plans'
  New-Item -ItemType Directory -Path $plansA -Force | Out-Null

  @'
---
master_plan: 2026-06-06-all-done
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: All done

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-done-slug](./2026-06-06-done-slug.md) | 3 | shipped |
'@ | Set-Content -Path (Join-Path $plansA 'MASTER-2026-06-06-all-done.md') -Encoding utf8

  @'
---
plan: done-slug
status: shipped
current_step: 3
estimated_steps: 3
last_updated: 2026-06-05
---

# Sub-plan: Done slug

All steps complete.
'@ | Set-Content -Path (Join-Path $plansA '2026-06-06-done-slug.md') -Encoding utf8

  # --- Project B: has a queued (pending) sub-plan ---
  $projB = Join-Path $projectsDir 'proj-b'
  $plansB = Join-Path $projB 'plans'
  New-Item -ItemType Directory -Path $plansB -Force | Out-Null

  @'
---
master_plan: 2026-06-06-has-work
batch_date: 2026-06-06
status: active
---

# MASTER plan: Has work

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-queued-slug](./2026-06-06-queued-slug.md) | 5 | pending |
'@ | Set-Content -Path (Join-Path $plansB 'MASTER-2026-06-06-has-work.md') -Encoding utf8

  @'
---
plan: queued-slug
status: pending
current_step: 0
estimated_steps: 5
last_updated: 2026-06-06
---

# Sub-plan: Queued slug

Waiting to be executed.
'@ | Set-Content -Path (Join-Path $plansB '2026-06-06-queued-slug.md') -Encoding utf8
}

function Run-Scan {
  Write-Host '=== test_scheduler.ps1 scan ==='
  Setup-FakeData

  # Run scheduler_scan.py with the fake ILK_DATA_HOME
  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & python $ScanScript 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler_scan.py exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()

  # Assert: exactly one project returned
  $count = $outputStr | & python -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))"
  if ($count -ne '1') {
    throw "Expected 1 project, got $count. Output: $outputStr"
  }

  # Assert: it is proj-b
  $key = $outputStr | & python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['key'])"
  if ($key -ne 'proj-b') {
    throw "Expected key 'proj-b', got '$key'. Output: $outputStr"
  }

  # Assert: oldest_queued_ts starts with 2026-06-06
  $ts = $outputStr | & python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['oldest_queued_ts'])"
  if (-not ($ts -like '2026-06-06*')) {
    throw "Expected ts starting with '2026-06-06', got '$ts'. Output: $outputStr"
  }

  Write-Host 'PASS: scan subcommand'
  Cleanup
}

# --- main ---------------------------------------------------------------------

switch ($Subcommand) {
  'scan' { Run-Scan }
}
