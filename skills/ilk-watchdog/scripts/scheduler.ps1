<#
.SYNOPSIS
  Single cross-project scheduler (V1 "global watchdog").

.DESCRIPTION
  Scans all projects for queued sub-plans, selects the FIFO-first project
  whose sentinel is free, and dispatches it via launch.ps1 -Engine claude-worker.

  Pool cap = 1 (V1): if ANY project is busy, no dispatch is planned.

  -DryRun prints the planned decision without executing anything.
  -Once runs a single scan cycle (for tests) instead of the daemon loop.

.PARAMETER PollMin
  Polling interval in minutes. Default 5.

.PARAMETER MaxDispatches
  Global dispatch ceiling. -1 = unlimited (default). 0 = no dispatches allowed.

.PARAMETER MaxBudgetUsd
  Global budget ceiling (informational in V1). Default 0 (unlimited).

.PARAMETER DryRun
  Print the planned decision without dispatching.

.PARAMETER Once
  Run a single scan cycle and exit (for tests).

.EXAMPLE
  .\scheduler.ps1 -DryRun -Once

.EXAMPLE
  .\scheduler.ps1 -PollMin 2 -MaxDispatches 5
#>
param(
  [int]$PollMin = 5,
  [int]$MaxDispatches = -1,
  [double]$MaxBudgetUsd = 0,
  [switch]$DryRun,
  [switch]$Once
)

$ErrorActionPreference = 'Stop'

# --- skill root resolution ---------------------------------------------------
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

# --- constants ---------------------------------------------------------------
$ScanScript  = Join-Path $PSScriptRoot 'scheduler_scan.py'
$LaunchScript = Join-Path $SkillRoot 'ilk-launcher\scripts\launch.ps1'

# --- helpers -----------------------------------------------------------------

function Test-RunningPid {
  <#
    Check if a project has a live running.pid (sentinel mutex).
    Returns $true if the project is busy, $false if free.
  #>
  param([string]$ProjectDataPath)
  $pidFile = Join-Path $ProjectDataPath 'runtime\launcher\running.pid'
  if (-not (Test-Path $pidFile)) { return $false }
  $raw = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue)
  if (-not $raw) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    return $false
  }
  $raw = $raw.Trim()
  if (-not $raw) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    return $false
  }
  try {
    $procId = [int]$raw
  } catch {
    return $false
  }
  if ($procId -le 0) { return $false }
  return [bool](Get-Process -Id $procId -ErrorAction SilentlyContinue)
}

function Invoke-SchedulerScan {
  <# Run scheduler_scan.py with the current ILK_DATA_HOME. #>
  $output = & python $ScanScript 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "scheduler_scan.py exited $LASTEXITCODE. Output: $output"
  }
  return ($output | Out-String).Trim() | ConvertFrom-Json
}

# --- main loop ---------------------------------------------------------------

function Run-Scheduler {
  $dispatchCount = 0
  $blacklistSkip = @{}  # project key -> backoff expiry timestamp

  while ($true) {
    # --- scan for queued projects ---
    $queued = Invoke-SchedulerScan

    if (-not $queued -or $queued.Count -eq 0) {
      if ($DryRun -and $Once) {
        @{ decision = 'idle'; reason = 'all-queues-empty' } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: all queues empty. Polling in $PollMin min."
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    # --- check budget ceiling ---
    # MaxDispatches -1 = unlimited; >= 0 = hard ceiling.
    if ($MaxDispatches -ge 0 -and $dispatchCount -ge $MaxDispatches) {
      if ($DryRun -and $Once) {
        @{ decision = 'idle'; reason = 'budget-ceiling' } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: budget ceiling (dispatched $dispatchCount/$MaxDispatches). Polling in $PollMin min."
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    # --- iterate projects in FIFO order ---
    $selected = $null

    foreach ($proj in $queued) {
      $key = $proj.key
      $path = $proj.path

      # blacklist skip
      if ($blacklistSkip.ContainsKey($key)) {
        if ((Get-Date) -lt $blacklistSkip[$key]) {
          if ($DryRun -and $Once) {
            @{ decision = 'skip-blacklist'; key = $key } | ConvertTo-Json -Compress
          } else {
            Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] skip-blacklist: $key"
          }
          continue
        } else {
          $blacklistSkip.Remove($key)
        }
      }

      if (Test-RunningPid -ProjectDataPath $path) {
        if ($DryRun -and $Once) {
          @{ decision = 'skip-busy'; key = $key } | ConvertTo-Json -Compress
        } else {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] skip-busy: $key"
        }
        continue
      }

      # First free project in FIFO order
      $selected = $proj
      break
    }

    if ($null -eq $selected) {
      if ($DryRun -and $Once) {
        @{ decision = 'idle'; reason = 'all-queued-projects-blacklisted' } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: all queued projects blacklisted. Polling in $PollMin min."
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    # --- dispatch the selected project ---
    $key = $selected.key
    $path = $selected.path

    if ($DryRun -and $Once) {
      @{ decision = 'dispatch'; key = $key; command = "launch.ps1 -ProjectPath '$path' -Engine claude-worker" } | ConvertTo-Json -Compress
      return
    }

    if ($DryRun) {
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] DRY-RUN: would dispatch $key via $LaunchScript -ProjectPath '$path' -Engine claude-worker"
    } else {
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatching $key..."
      try {
        & $LaunchScript -ProjectPath $path -Engine claude-worker -Force
        $dispatchCount++
        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatched $key (total: $dispatchCount)"
      } catch {
        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatch failed for $key`: $_"
        # Record in blacklist with 5-min backoff
        $blacklistSkip[$key] = (Get-Date).AddMinutes(5)
      }
    }

    Start-Sleep -Seconds ($PollMin * 60)
  }
}

# --- entry point -------------------------------------------------------------

Run-Scheduler
