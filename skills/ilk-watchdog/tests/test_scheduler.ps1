<#
.SYNOPSIS
  Cross-platform test harness for the cross-project scheduler.

.DESCRIPTION
  Subcommands:
    scan     — build a fake ILK_DATA_HOME with 2 projects (one all-shipped,
               one with a queued sub-plan) and assert scheduler_scan.py lists
               ONLY the queued one.
    select   — build 2 queued projects (A older than B), assert FIFO dispatch,
               then simulate running.pid for A and assert skip-busy → dispatch B.
    dispatch — assert the planned dispatch command contains -Engine claude-worker
               and the selected project path; assert -MaxDispatches 0 yields
               idle: budget ceiling.
#>
param(
  [ValidateSet('scan', 'select', 'dispatch', 'blacklist', 'unresolved', 'all')]
  [string]$Subcommand = 'all'
)

$ErrorActionPreference = 'Stop'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Resolve-Path (Join-Path $ScriptDir '..\..\..')
$ScanScript      = Join-Path $ScriptDir '..\scripts\scheduler_scan.py'
$SchedulerScript = Join-Path $ScriptDir '..\scripts\scheduler.ps1'
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

function Setup-TwoQueuedProjects {
  Cleanup

  $projectsDir = Join-Path $FakeData 'projects'
  New-Item -ItemType Directory -Path $projectsDir -Force | Out-Null

  # --- Project A: queued, older timestamp (should be dispatched first) ---
  $projA = Join-Path $projectsDir 'proj-a'
  $plansA = Join-Path $projA 'plans'
  New-Item -ItemType Directory -Path $plansA -Force | Out-Null

  @'
---
master_plan: 2026-06-01-batch
batch_date: 2026-06-01
status: active
---

# MASTER plan: Batch A

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-01-task-alpha](./2026-06-01-task-alpha.md) | 4 | pending |
'@ | Set-Content -Path (Join-Path $plansA 'MASTER-2026-06-01-batch.md') -Encoding utf8

  @'
---
plan: task-alpha
status: pending
current_step: 0
estimated_steps: 4
last_updated: 2026-06-01
---

# Sub-plan: Task Alpha

Queued and waiting.
'@ | Set-Content -Path (Join-Path $plansA '2026-06-01-task-alpha.md') -Encoding utf8

  # --- Project B: queued, newer timestamp (should be dispatched second) ---
  $projB = Join-Path $projectsDir 'proj-b'
  $plansB = Join-Path $projB 'plans'
  New-Item -ItemType Directory -Path $plansB -Force | Out-Null

  @'
---
master_plan: 2026-06-03-batch
batch_date: 2026-06-03
status: active
---

# MASTER plan: Batch B

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-03-task-beta](./2026-06-03-task-beta.md) | 3 | pending |
'@ | Set-Content -Path (Join-Path $plansB 'MASTER-2026-06-03-batch.md') -Encoding utf8

  @'
---
plan: task-beta
status: pending
current_step: 0
estimated_steps: 3
last_updated: 2026-06-03
---

# Sub-plan: Task Beta

Queued and waiting.
'@ | Set-Content -Path (Join-Path $plansB '2026-06-03-task-beta.md') -Encoding utf8

  # last-launch.json so repo_path resolves to a SOURCE repo path that is
  # DELIBERATELY different from the ~/.ilk-data data dir (under scratch\repos\,
  # not scratch\...\ilk-data\projects\). Proves dispatch uses the repo path.
  $llA = Join-Path $projA 'runtime\launcher'
  $llB = Join-Path $projB 'runtime\launcher'
  New-Item -ItemType Directory -Path $llA, $llB -Force | Out-Null
  (@{ project_path = (Join-Path $Scratch 'repos\proj-a'); worker_engine = 'claude-worker' } | ConvertTo-Json -Compress) |
    Set-Content -Path (Join-Path $llA 'last-launch.json') -Encoding utf8
  (@{ project_path = (Join-Path $Scratch 'repos\proj-b'); worker_engine = 'claude-worker' } | ConvertTo-Json -Compress) |
    Set-Content -Path (Join-Path $llB 'last-launch.json') -Encoding utf8
}

function Run-Select {
  Write-Host '=== test_scheduler.ps1 select ==='
  Setup-TwoQueuedProjects

  # Test 1: FIFO — with both projects free, proj-a (older) should be dispatched first
  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()

  # Parse the JSON output
  $json = $outputStr | ConvertFrom-Json
  if ($json.decision -ne 'dispatch') {
    throw "Expected 'dispatch', got '$($json.decision)'. Output: $outputStr"
  }
  if ($json.key -ne 'proj-a') {
    throw "Expected FIFO dispatch of 'proj-a', got '$($json.key)'. Output: $outputStr"
  }
  Write-Host 'PASS: FIFO dispatch (proj-a first)'

  # Test 2: simulate a live running.pid for proj-a → skip-busy, dispatch proj-b
  $launcherDir = Join-Path $FakeData 'projects\proj-a\runtime\launcher'
  New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null
  # Use current PID as a definitely-alive process
  $PID | Out-File -FilePath (Join-Path $launcherDir 'running.pid') -Encoding ascii -NoNewline

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()

  # The output may contain multiple lines. Parse each as JSON.
  $lines = @($outputStr -split "`n" | Where-Object { $_.Trim() })
  $lastLine = $lines[-1].Trim()
  $firstLine = $lines[0].Trim()

  $lastJson = $lastLine | ConvertFrom-Json
  if ($lastJson.decision -ne 'dispatch') {
    throw "Expected last decision 'dispatch', got '$($lastJson.decision)'. Output: $outputStr"
  }
  if ($lastJson.key -ne 'proj-b') {
    throw "Expected dispatch of 'proj-b' after skip-busy, got '$($lastJson.key)'. Output: $outputStr"
  }

  $firstJson = $firstLine | ConvertFrom-Json
  if ($firstJson.decision -ne 'skip-busy') {
    throw "Expected first decision 'skip-busy', got '$($firstJson.decision)'. Output: $outputStr"
  }
  if ($firstJson.key -ne 'proj-a') {
    throw "Expected skip-busy for 'proj-a', got '$($firstJson.key)'. Output: $outputStr"
  }

  Write-Host 'PASS: skip-busy proj-a, dispatch proj-b'
  Cleanup
}

function Run-Dispatch {
  Write-Host '=== test_scheduler.ps1 dispatch ==='
  Setup-TwoQueuedProjects

  # Test 1: dispatch command contains -Engine claude-worker and selected project path
  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $json = $outputStr | ConvertFrom-Json

  if ($json.decision -ne 'dispatch') {
    throw "Expected 'dispatch', got '$($json.decision)'. Output: $outputStr"
  }
  if ($json.key -ne 'proj-a') {
    throw "Expected dispatch of 'proj-a', got '$($json.key)'. Output: $outputStr"
  }
  if ($json.command -notlike '*-Engine claude-worker*') {
    throw "Expected '-Engine claude-worker' in command, got '$($json.command)'. Output: $outputStr"
  }
  # Must dispatch the SOURCE repo path (scratch\repos\proj-a), NOT the data dir.
  if ($json.command -notlike '*repos*proj-a*') {
    throw "Expected SOURCE repo path (repos\proj-a) in command, got '$($json.command)'. Output: $outputStr"
  }
  if ($json.command -like '*ilk-data*') {
    throw "Command must NOT contain the data dir (ilk-data); got '$($json.command)'. Output: $outputStr"
  }
  Write-Host 'PASS: dispatch command uses the source repo path, not the data dir'

  # Test 2: -MaxDispatches 0 yields idle: budget ceiling
  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxDispatches 0 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $json = $outputStr | ConvertFrom-Json

  if ($json.decision -ne 'idle') {
    throw "Expected 'idle', got '$($json.decision)'. Output: $outputStr"
  }
  if ($json.reason -notlike '*budget*') {
    throw "Expected 'budget' in reason, got '$($json.reason)'. Output: $outputStr"
  }
  Write-Host 'PASS: -MaxDispatches 0 yields idle: budget ceiling'

  Cleanup
}

function Run-Blacklist {
  Write-Host '=== test_scheduler.ps1 blacklist ==='
  Setup-TwoQueuedProjects

  # Create a postmortem for project A with blacklist classification
  $pmDir = Join-Path $FakeData 'projects\proj-a\runtime\launcher\postmortems'
  New-Item -ItemType Directory -Path $pmDir -Force | Out-Null

  $now = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'
  @(
    '---'
    'project: proj-a'
    'classification: stuck-no-progress'
    "generated_at: $now"
    '---'
    ''
    '# Postmortem for proj-a'
  ) | Set-Content -Path (Join-Path $pmDir '20260606-120000.md') -Encoding utf8

  # Test 1: DryRun+Once should report skip-blacklist for A, dispatch B
  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $lines = @($outputStr -split "`n" | Where-Object { $_.Trim() })
  $firstLine = $lines[0].Trim()
  $lastLine = $lines[-1].Trim()

  $firstJson = $firstLine | ConvertFrom-Json
  if ($firstJson.decision -ne 'skip-blacklist') {
    throw "Expected 'skip-blacklist', got '$($firstJson.decision)'. Output: $outputStr"
  }
  if ($firstJson.key -ne 'proj-a') {
    throw "Expected skip-blacklist for 'proj-a', got '$($firstJson.key)'. Output: $outputStr"
  }

  $lastJson = $lastLine | ConvertFrom-Json
  if ($lastJson.decision -ne 'dispatch') {
    throw "Expected 'dispatch', got '$($lastJson.decision)'. Output: $outputStr"
  }
  if ($lastJson.key -ne 'proj-b') {
    throw "Expected dispatch of 'proj-b', got '$($lastJson.key)'. Output: $outputStr"
  }

  Write-Host 'PASS: skip-blacklist proj-a, dispatch proj-b (non-starvation)'

  # Test 2: empty queues report idle (AC-5)
  Cleanup
  $projectsDir = Join-Path $FakeData 'projects'
  New-Item -ItemType Directory -Path $projectsDir -Force | Out-Null

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $json = $outputStr | ConvertFrom-Json
  if ($json.decision -ne 'idle') {
    throw "Expected 'idle' for empty queues, got '$($json.decision)'. Output: $outputStr"
  }

  Write-Host 'PASS: empty queues report idle'
  Cleanup
}

function Run-Unresolved {
  Write-Host '=== test_scheduler.ps1 unresolved ==='
  Cleanup
  $projectsDir = Join-Path $FakeData 'projects'
  New-Item -ItemType Directory -Path $projectsDir -Force | Out-Null

  # One queued project with NO last-launch.json and not in any registry →
  # repo_path cannot resolve → scheduler must skip-unresolved, not dispatch.
  $projC = Join-Path $projectsDir 'proj-c'
  $plansC = Join-Path $projC 'plans'
  New-Item -ItemType Directory -Path $plansC -Force | Out-Null

  @'
---
master_plan: 2026-06-02-orphan
status: active
---

# MASTER plan: Orphan

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-02-orphan-slug](./2026-06-02-orphan-slug.md) | 2 | pending |
'@ | Set-Content -Path (Join-Path $plansC 'MASTER-2026-06-02-orphan.md') -Encoding utf8

  @'
---
plan: orphan-slug
status: pending
current_step: 0
estimated_steps: 2
last_updated: 2026-06-02
---

# Sub-plan: Orphan slug

No last-launch.json, so repo_path cannot resolve.
'@ | Set-Content -Path (Join-Path $plansC '2026-06-02-orphan-slug.md') -Encoding utf8

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $lines = @($outputStr -split "`n" | Where-Object { $_.Trim() })
  $firstJson = $lines[0].Trim() | ConvertFrom-Json
  if ($firstJson.decision -ne 'skip-unresolved') {
    throw "Expected 'skip-unresolved', got '$($firstJson.decision)'. Output: $outputStr"
  }
  if ($firstJson.key -ne 'proj-c') {
    throw "Expected skip-unresolved for 'proj-c', got '$($firstJson.key)'. Output: $outputStr"
  }

  $lastJson = $lines[-1].Trim() | ConvertFrom-Json
  if ($lastJson.decision -ne 'idle') {
    throw "Expected final 'idle', got '$($lastJson.decision)'. Output: $outputStr"
  }

  Write-Host 'PASS: skip-unresolved when repo_path cannot resolve, then idle'
  Cleanup
}

# --- main ---------------------------------------------------------------------

switch ($Subcommand) {
  'scan'      { Run-Scan }
  'select'    { Run-Select }
  'dispatch'  { Run-Dispatch }
  'blacklist'  { Run-Blacklist }
  'unresolved' { Run-Unresolved }
  'all'        { Run-Scan; Run-Select; Run-Dispatch; Run-Blacklist; Run-Unresolved; Write-Host 'ALL PASS' }
}
