<#
.SYNOPSIS
  Launch the cross-project scheduler (detached) or preview its planned run.

.DESCRIPTION
  Thin wrapper around scheduler.ps1. Resolves the skill root, then either:
    -DryRun: previews via scheduler.ps1 -DryRun -Once (no window spawned)
    default: spawns scheduler.ps1 -Detach in a new desktop window

  Mirrors how ilk-run.ps1 wraps the per-project launcher+watchdog.

.PARAMETER PollMin
  Polling interval in minutes passed to the scheduler. Default 5.

.PARAMETER MaxConcurrent
  Max concurrent live loops across all projects. Default 5.

.PARAMETER DryRun
  Preview the scheduler invocation without spawning a window.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File ilk-schedule.ps1 -DryRun

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File ilk-schedule.ps1 -MaxConcurrent 3 -PollMin 2
#>
[CmdletBinding()]
param(
  [int]$PollMin = 5,
  [int]$MaxConcurrent = 5,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")

$SkillRoot = Get-IlkSkillRoot
$SchedulerPs1 = Join-Path $SkillRoot "ilk-watchdog\scripts\scheduler.ps1"

if (-not (Test-Path $SchedulerPs1)) {
  Write-Error "Scheduler not found at: $SchedulerPs1"
  exit 1
}

if ($DryRun) {
  Write-Host "[ilk-scheduler] preview (dry-run, single cycle):"
  Write-Host "  Would run: scheduler.ps1 -Detach -PollMin $PollMin -MaxConcurrent $MaxConcurrent"
  Write-Host ""
  & powershell -NoProfile -ExecutionPolicy Bypass -File $SchedulerPs1 `
    -DryRun -Once -PollMin $PollMin -MaxConcurrent $MaxConcurrent
  exit $LASTEXITCODE
}

# Live launch: spawn scheduler in a detached window
& powershell -NoProfile -ExecutionPolicy Bypass -File $SchedulerPs1 `
  -Detach -PollMin $PollMin -MaxConcurrent $MaxConcurrent
exit $LASTEXITCODE
