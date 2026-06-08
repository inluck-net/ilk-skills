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
    cap      — assert -MaxConcurrent capacity accounting: N busy projects fill
               slots, dispatches stop at the cap.
#>
param(
  [ValidateSet('scan', 'select', 'dispatch', 'promote', 'blacklist', 'unresolved', 'cap', 'fill', 'gates', 'mutex', 'all')]
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

  # --- Project C: M1 shipped + M2 queued(pending) — MUST be listed (promotable) ---
  $projC = Join-Path $projectsDir 'proj-c'
  $plansC = Join-Path $projC 'plans'
  New-Item -ItemType Directory -Path $plansC -Force | Out-Null

  @'
---
master_plan: 2026-06-06-multi-done
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: Multi done

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-multi-done-sub](./2026-06-06-multi-done-sub.md) | 2 | shipped |
'@ | Set-Content -Path (Join-Path $plansC 'MASTER-2026-06-06-multi-done.md') -Encoding utf8

  @'
---
plan: multi-done-sub
status: shipped
current_step: 2
estimated_steps: 2
last_updated: 2026-06-05
---

# Sub-plan: Multi done sub

All steps complete.
'@ | Set-Content -Path (Join-Path $plansC '2026-06-06-multi-done-sub.md') -Encoding utf8

  @'
---
master_plan: 2026-06-06-multi-queued
batch_date: 2026-06-06
status: queued
---

# MASTER plan: Multi queued

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-multi-queued-sub](./2026-06-06-multi-queued-sub.md) | 3 | pending |
'@ | Set-Content -Path (Join-Path $plansC 'MASTER-2026-06-06-multi-queued.md') -Encoding utf8

  @'
---
plan: multi-queued-sub
status: pending
current_step: 0
estimated_steps: 3
last_updated: 2026-06-06
---

# Sub-plan: Multi queued sub

Waiting for promotion.
'@ | Set-Content -Path (Join-Path $plansC '2026-06-06-multi-queued-sub.md') -Encoding utf8

  # --- Project D: all masters shipped — MUST be excluded ---
  $projD = Join-Path $projectsDir 'proj-d'
  $plansD = Join-Path $projD 'plans'
  New-Item -ItemType Directory -Path $plansD -Force | Out-Null

  @'
---
master_plan: 2026-06-06-all-shipped-1
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: All shipped 1

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-shipped-sub-1](./2026-06-06-shipped-sub-1.md) | 2 | shipped |
'@ | Set-Content -Path (Join-Path $plansD 'MASTER-2026-06-06-all-shipped-1.md') -Encoding utf8

  @'
---
plan: shipped-sub-1
status: shipped
current_step: 2
estimated_steps: 2
last_updated: 2026-06-04
---

# Sub-plan: Shipped sub 1

All steps complete.
'@ | Set-Content -Path (Join-Path $plansD '2026-06-06-shipped-sub-1.md') -Encoding utf8

  @'
---
master_plan: 2026-06-06-all-shipped-2
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: All shipped 2

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-shipped-sub-2](./2026-06-06-shipped-sub-2.md) | 1 | shipped |
'@ | Set-Content -Path (Join-Path $plansD 'MASTER-2026-06-06-all-shipped-2.md') -Encoding utf8

  @'
---
plan: shipped-sub-2
status: shipped
current_step: 1
estimated_steps: 1
last_updated: 2026-06-04
---

# Sub-plan: Shipped sub 2

All steps complete.
'@ | Set-Content -Path (Join-Path $plansD '2026-06-06-shipped-sub-2.md') -Encoding utf8
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

  # Assert: exactly 2 projects returned (proj-b active, proj-c promotable)
  # proj-a (all shipped) and proj-d (all masters shipped) are excluded.
  $count = $outputStr | & python -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))"
  if ($count -ne '2') {
    throw "Expected 2 projects, got $count. Output: $outputStr"
  }

  # Assert: first is proj-b (active master)
  $key0 = $outputStr | & python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['key'])"
  if ($key0 -ne 'proj-b') {
    throw "Expected first key 'proj-b', got '$key0'. Output: $outputStr"
  }

  # Assert: second is proj-c (queued master only, promotable)
  $key1 = $outputStr | & python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[1]['key'])"
  if ($key1 -ne 'proj-c') {
    throw "Expected second key 'proj-c', got '$key1'. Output: $outputStr"
  }

  # Assert: oldest_queued_ts starts with 2026-06-06 for both
  $ts0 = $outputStr | & python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['oldest_queued_ts'])"
  $ts1 = $outputStr | & python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[1]['oldest_queued_ts'])"
  if (-not ($ts0 -like '2026-06-06*')) {
    throw "Expected ts0 starting with '2026-06-06', got '$ts0'. Output: $outputStr"
  }
  if (-not ($ts1 -like '2026-06-06*')) {
    throw "Expected ts1 starting with '2026-06-06', got '$ts1'. Output: $outputStr"
  }

  Write-Host 'PASS: scan subcommand (runnable-master semantics)'
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

  # Test 1: FIFO — with both projects free, proj-a (older) should be dispatched first.
  # Use MaxConcurrent 1 so only one project is dispatched per cycle (strict sequential).
  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 1 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()

  # Parse the JSON output (single line with MaxConcurrent 1)
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

  # Test 1: dispatch command contains -Engine claude-worker and selected project path.
  # With fill-free-slots, both projects dispatch; parse the first line for proj-a.
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
  $json = $lines[0].Trim() | ConvertFrom-Json

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

function Setup-PromotableProject {
  # Project with M1 shipped + M2 queued (promotable) + source repo path resolved
  Cleanup
  $projectsDir = Join-Path $FakeData 'projects'
  New-Item -ItemType Directory -Path $projectsDir -Force | Out-Null

  $projP = Join-Path $projectsDir 'proj-promote'
  $plansP = Join-Path $projP 'plans'
  New-Item -ItemType Directory -Path $plansP -Force | Out-Null

  @'
---
master_plan: 2026-06-06-m1-done
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: M1 done

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-m1-sub](./2026-06-06-m1-sub.md) | 2 | shipped |
'@ | Set-Content -Path (Join-Path $plansP 'MASTER-2026-06-06-m1-done.md') -Encoding utf8

  @'
---
plan: m1-sub
status: shipped
current_step: 2
estimated_steps: 2
last_updated: 2026-06-05
---

# Sub-plan: M1 sub

All steps complete.
'@ | Set-Content -Path (Join-Path $plansP '2026-06-06-m1-sub.md') -Encoding utf8

  @'
---
master_plan: 2026-06-06-m2-queued
batch_date: 2026-06-06
status: queued
priority: 1
created: 2026-06-06T10:00:00+08:00
---

# MASTER plan: M2 queued

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-m2-sub](./2026-06-06-m2-sub.md) | 3 | pending |
'@ | Set-Content -Path (Join-Path $plansP 'MASTER-2026-06-06-m2-queued.md') -Encoding utf8

  @'
---
plan: m2-sub
status: pending
current_step: 0
estimated_steps: 3
last_updated: 2026-06-06
---

# Sub-plan: M2 sub

Waiting for promotion.
'@ | Set-Content -Path (Join-Path $plansP '2026-06-06-m2-sub.md') -Encoding utf8

  # last-launch.json so repo_path resolves
  $llDir = Join-Path $projP 'runtime\launcher'
  New-Item -ItemType Directory -Path $llDir -Force | Out-Null
  (@{ project_path = (Join-Path $Scratch 'repos\proj-promote'); worker_engine = 'claude-worker' } | ConvertTo-Json -Compress) |
    Set-Content -Path (Join-Path $llDir 'last-launch.json') -Encoding utf8
}

function Setup-ActiveMasterNoPromote {
  # Project with an active master that has pending sub-plans (no promotion needed)
  Cleanup
  $projectsDir = Join-Path $FakeData 'projects'
  New-Item -ItemType Directory -Path $projectsDir -Force | Out-Null

  $projA = Join-Path $projectsDir 'proj-active'
  $plansA = Join-Path $projA 'plans'
  New-Item -ItemType Directory -Path $plansA -Force | Out-Null

  @'
---
master_plan: 2026-06-06-active-batch
batch_date: 2026-06-06
status: active
---

# MASTER plan: Active batch

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-active-sub](./2026-06-06-active-sub.md) | 4 | pending |
'@ | Set-Content -Path (Join-Path $plansA 'MASTER-2026-06-06-active-batch.md') -Encoding utf8

  @'
---
plan: active-sub
status: pending
current_step: 0
estimated_steps: 4
last_updated: 2026-06-06
---

# Sub-plan: Active sub

Waiting to be executed.
'@ | Set-Content -Path (Join-Path $plansA '2026-06-06-active-sub.md') -Encoding utf8

  # last-launch.json so repo_path resolves
  $llDir = Join-Path $projA 'runtime\launcher'
  New-Item -ItemType Directory -Path $llDir -Force | Out-Null
  (@{ project_path = (Join-Path $Scratch 'repos\proj-active'); worker_engine = 'claude-worker' } | ConvertTo-Json -Compress) |
    Set-Content -Path (Join-Path $llDir 'last-launch.json') -Encoding utf8
}

function Run-Promote {
  Write-Host '=== test_scheduler.ps1 promote ==='

  # Test 1: M1 shipped + M2 queued → dry-run reports promote(M2) then dispatch (AC-1)
  Setup-PromotableProject
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

  # First line must be promote decision
  $promoteLine = $lines[0].Trim() | ConvertFrom-Json
  if ($promoteLine.decision -ne 'promote') {
    throw "Expected first decision 'promote', got '$($promoteLine.decision)'. Output: $outputStr"
  }
  if ($promoteLine.key -ne 'proj-promote') {
    throw "Expected promote key 'proj-promote', got '$($promoteLine.key)'. Output: $outputStr"
  }
  if ($promoteLine.promoted -notlike '*m2-queued*') {
    throw "Expected promoted to contain 'm2-queued', got '$($promoteLine.promoted)'. Output: $outputStr"
  }

  # Second line must be dispatch decision
  $dispatchLine = $lines[1].Trim() | ConvertFrom-Json
  if ($dispatchLine.decision -ne 'dispatch') {
    throw "Expected second decision 'dispatch', got '$($dispatchLine.decision)'. Output: $outputStr"
  }
  if ($dispatchLine.key -ne 'proj-promote') {
    throw "Expected dispatch key 'proj-promote', got '$($dispatchLine.key)'. Output: $outputStr"
  }

  Write-Host 'PASS: promote(M2) then dispatch (AC-1)'

  # Test 2: active master with pending sub-plans → dispatch with NO promote (AC-2)
  Setup-ActiveMasterNoPromote
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
    throw "Expected 'dispatch' (no promote needed), got '$($json.decision)'. Output: $outputStr"
  }
  if ($json.key -ne 'proj-active') {
    throw "Expected key 'proj-active', got '$($json.key)'. Output: $outputStr"
  }

  Write-Host 'PASS: dispatch with NO promote when active master exists (AC-2)'
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

function Setup-CapProjects {
  param([int]$NumProjects)
  Cleanup
  $projectsDir = Join-Path $FakeData 'projects'
  New-Item -ItemType Directory -Path $projectsDir -Force | Out-Null

  for ($i = 1; $i -le $NumProjects; $i++) {
    $proj = Join-Path $projectsDir "proj-cap-$i"
    $plans = Join-Path $proj 'plans'
    $llDir = Join-Path $proj 'runtime\launcher'
    New-Item -ItemType Directory -Path $plans, $llDir -Force | Out-Null

    @"
---
master_plan: 2026-06-06-cap-batch
batch_date: 2026-06-06
status: active
---

# MASTER plan: Cap batch $i

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-cap-sub](./2026-06-06-cap-sub.md) | 3 | pending |
"@ | Set-Content -Path (Join-Path $plans 'MASTER-2026-06-06-cap-batch.md') -Encoding utf8

    @"
---
plan: cap-sub
status: pending
current_step: 0
estimated_steps: 3
last_updated: 2026-06-06
---

# Sub-plan: Cap sub $i

Queued and waiting.
"@ | Set-Content -Path (Join-Path $plans '2026-06-06-cap-sub.md') -Encoding utf8

    (@{ project_path = (Join-Path $Scratch "repos\proj-cap-$i"); worker_engine = 'claude-worker' } | ConvertTo-Json -Compress) |
      Set-Content -Path (Join-Path $llDir 'last-launch.json') -Encoding utf8
  }
}

function Run-Cap {
  Write-Host '=== test_scheduler.ps1 cap ==='

  # Test 1: MaxConcurrent=2, 2 busy projects → capacity-full (idle)
  Setup-CapProjects -NumProjects 3

  # Mark first 2 projects as busy with live PIDs
  $launcherDir1 = Join-Path $FakeData 'projects\proj-cap-1\runtime\launcher'
  $launcherDir2 = Join-Path $FakeData 'projects\proj-cap-2\runtime\launcher'
  $PID | Out-File -FilePath (Join-Path $launcherDir1 'running.pid') -Encoding ascii -NoNewline
  $PID | Out-File -FilePath (Join-Path $launcherDir2 'running.pid') -Encoding ascii -NoNewline

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 2 2>&1
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
  if ($json.reason -ne 'capacity-full') {
    throw "Expected 'capacity-full', got '$($json.reason)'. Output: $outputStr"
  }
  if ($json.live -ne 2) {
    throw "Expected live=2, got '$($json.live)'. Output: $outputStr"
  }
  if ($json.max_concurrent -ne 2) {
    throw "Expected max_concurrent=2, got '$($json.max_concurrent)'. Output: $outputStr"
  }

  Write-Host 'PASS: MaxConcurrent=2, 2 busy → capacity-full'

  # Test 2: MaxConcurrent=3, 2 busy → capacity=1, dispatch one project
  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 3 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $lines = @($outputStr -split "`n" | Where-Object { $_.Trim() })
  $lastLine = $lines[-1].Trim()
  $lastJson = $lastLine | ConvertFrom-Json

  if ($lastJson.decision -ne 'dispatch') {
    throw "Expected 'dispatch', got '$($lastJson.decision)'. Output: $outputStr"
  }

  Write-Host 'PASS: MaxConcurrent=3, 2 busy → dispatch 1 free project'

  # Test 3: MaxConcurrent=1, 0 busy → dispatch one project (strict sequential)
  Remove-Item (Join-Path $launcherDir1 'running.pid') -Force -ErrorAction SilentlyContinue
  Remove-Item (Join-Path $launcherDir2 'running.pid') -Force -ErrorAction SilentlyContinue

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 1 2>&1
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

  Write-Host 'PASS: MaxConcurrent=1, 0 busy → dispatch one (strict sequential)'
  Cleanup
}

function Run-Gates {
  Write-Host '=== test_scheduler.ps1 gates ==='

  # Test 1: default dispatch carries -RunLocalChecks in the command
  Setup-TwoQueuedProjects

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 1 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $json = $outputStr | ConvertFrom-Json

  if ($json.command -notlike '*RunLocalChecks*') {
    throw "Expected '-RunLocalChecks' in default dispatch command, got '$($json.command)'. Output: $outputStr"
  }
  Write-Host 'PASS: default dispatch carries -RunLocalChecks'

  # Test 2: -NoLocalChecks opt-out removes the flag
  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 1 -NoLocalChecks 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $json = $outputStr | ConvertFrom-Json

  if ($json.command -like '*RunLocalChecks*') {
    throw "Expected NO '-RunLocalChecks' with -NoLocalChecks, got '$($json.command)'. Output: $outputStr"
  }
  Write-Host 'PASS: -NoLocalChecks removes the gate flag from dispatch'

  Cleanup
}

function Run-Fill {
  Write-Host '=== test_scheduler.ps1 fill ==='

  # AC-1: 2 ready projects + MaxConcurrent 5 → both dispatched in one cycle with distinct slots
  Setup-CapProjects -NumProjects 2

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 5 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $lines = @($outputStr -split "`n" | Where-Object { $_.Trim() })

  if ($lines.Count -ne 2) {
    throw "Expected 2 dispatch lines, got $($lines.Count). Output: $outputStr"
  }

  $d1 = $lines[0].Trim() | ConvertFrom-Json
  $d2 = $lines[1].Trim() | ConvertFrom-Json

  if ($d1.decision -ne 'dispatch') { throw "Expected dispatch, got $($d1.decision)" }
  if ($d1.key -ne 'proj-cap-1') { throw "Expected proj-cap-1, got $($d1.key)" }
  if ($d1.slot -ne 1) { throw "Expected slot 1, got $($d1.slot)" }
  if ($d1.command -notlike '*-WorkerHome*claude-worker*') { throw "Expected -WorkerHome in command, got $($d1.command)" }
  if ($d1.command -like '*claude-worker-*') { throw "Slot 1 home should be base (no suffix), got $($d1.command)" }

  if ($d2.decision -ne 'dispatch') { throw "Expected dispatch, got $($d2.decision)" }
  if ($d2.key -ne 'proj-cap-2') { throw "Expected proj-cap-2, got $($d2.key)" }
  if ($d2.slot -ne 2) { throw "Expected slot 2, got $($d2.slot)" }
  if ($d2.command -notlike '*claude-worker-2*') { throw "Expected slot 2 home in command, got $($d2.command)" }

  Write-Host 'PASS: AC-1 — 2 projects dispatched in one cycle with distinct slot homes'

  # AC-2: 3 ready + MaxConcurrent 2 → exactly 2 dispatched, 3rd not in output
  Setup-CapProjects -NumProjects 3

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 2 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $lines = @($outputStr -split "`n" | Where-Object { $_.Trim() })

  if ($lines.Count -ne 2) {
    throw "Expected 2 dispatch lines (MaxConcurrent 2), got $($lines.Count). Output: $outputStr"
  }

  $d1 = $lines[0].Trim() | ConvertFrom-Json
  $d2 = $lines[1].Trim() | ConvertFrom-Json
  if ($d1.key -ne 'proj-cap-1') { throw "Expected proj-cap-1, got $($d1.key)" }
  if ($d2.key -ne 'proj-cap-2') { throw "Expected proj-cap-2, got $($d2.key)" }
  if ($d1.slot -ne 1) { throw "Expected slot 1, got $($d1.slot)" }
  if ($d2.slot -ne 2) { throw "Expected slot 2, got $($d2.slot)" }

  Write-Host 'PASS: AC-2 — 3 ready + MaxConcurrent 2 → exactly 2 dispatched'

  # AC-3: 1 busy + MaxConcurrent 2 → 1 dispatched (slot 2 distinct home)
  Setup-CapProjects -NumProjects 2

  $launcherDir1 = Join-Path $FakeData 'projects\proj-cap-1\runtime\launcher'
  $PID | Out-File -FilePath (Join-Path $launcherDir1 'running.pid') -Encoding ascii -NoNewline

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 2 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $lines = @($outputStr -split "`n" | Where-Object { $_.Trim() })

  $busyLine = $lines[0].Trim() | ConvertFrom-Json
  if ($busyLine.decision -ne 'skip-busy') { throw "Expected skip-busy, got $($busyLine.decision)" }

  $dispatchLine = $lines[-1].Trim() | ConvertFrom-Json
  if ($dispatchLine.decision -ne 'dispatch') { throw "Expected dispatch, got $($dispatchLine.decision)" }
  if ($dispatchLine.key -ne 'proj-cap-2') { throw "Expected proj-cap-2, got $($dispatchLine.key)" }
  if ($dispatchLine.command -notlike '*claude-worker*') { throw "Expected -WorkerHome in command" }

  Write-Host 'PASS: AC-3 — 1 busy + MaxConcurrent 2 → 1 dispatched with slot home'

  # AC-4: MaxConcurrent 1 → strict sequential (1 dispatched)
  Setup-CapProjects -NumProjects 2

  $env:ILK_DATA_HOME = $FakeData
  try {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once -MaxConcurrent 1 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "scheduler.ps1 exited $LASTEXITCODE. Output: $output"
    }
  } finally {
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()
  $json = $outputStr | ConvertFrom-Json

  if ($json.decision -ne 'dispatch') { throw "Expected dispatch, got $($json.decision)" }
  if ($json.key -ne 'proj-cap-1') { throw "Expected proj-cap-1, got $($json.key)" }
  if ($json.slot -ne 1) { throw "Expected slot 1, got $($json.slot)" }

  Write-Host 'PASS: AC-4 — MaxConcurrent 1 → strict sequential (1 dispatched)'
  Cleanup
}

function Run-Mutex {
  Write-Host '=== test_scheduler.ps1 mutex ==='
  Setup-TwoQueuedProjects

  # Test: second scheduler.ps1 launch exits immediately with "already running"
  # while the first holds the Global\ilk-scheduler mutex.
  #
  # Strategy: launch the first instance in the background (it will block in the
  # poll loop). Then launch a second -DryRun -Once instance which should detect
  # the mutex, print "already running", and exit 0 without producing dispatch JSON.
  $env:ILK_DATA_HOME = $FakeData
  try {
    # Launch first instance in background (blocks in poll loop).
    $firstJob = Start-Job -ScriptBlock {
      param($SchedulerScript, $FakeData)
      $env:ILK_DATA_HOME = $FakeData
      & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript 2>&1
    } -ArgumentList $SchedulerScript, $FakeData

    # Give it time to acquire the mutex.
    Start-Sleep -Seconds 2

    # Launch second instance — should detect mutex and exit immediately.
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SchedulerScript -DryRun -Once 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    # Clean up the background job.
    Stop-Job -Job $firstJob -ErrorAction SilentlyContinue
    Remove-Job -Job $firstJob -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\ILK_DATA_HOME -ErrorAction SilentlyContinue
  }

  $outputStr = ($output | Out-String).Trim()

  # Assert: exit code 0 (not an error — graceful bail-out).
  if ($exitCode -ne 0) {
    throw "Expected exit code 0 for duplicate scheduler, got $exitCode. Output: $outputStr"
  }

  # Assert: output contains "already running".
  if ($outputStr -notlike '*already running*') {
    throw "Expected 'already running' in output, got: $outputStr"
  }

  Write-Host 'PASS: second scheduler exits with "already running" (mutex held)'
  Cleanup
}

# --- main ---------------------------------------------------------------------

switch ($Subcommand) {
  'scan'       { Run-Scan }
  'select'     { Run-Select }
  'dispatch'   { Run-Dispatch }
  'promote'    { Run-Promote }
  'blacklist'  { Run-Blacklist }
  'unresolved' { Run-Unresolved }
  'cap'        { Run-Cap }
  'fill'       { Run-Fill }
  'gates'      { Run-Gates }
  'mutex'      { Run-Mutex }
  'all'        { Run-Scan; Run-Select; Run-Dispatch; Run-Promote; Run-Blacklist; Run-Unresolved; Run-Cap; Run-Fill; Run-Gates; Run-Mutex; Write-Host 'ALL PASS' }
}
