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

function Read-BlacklistFromPostmortems {
  <#
    Scan queued projects for recent postmortem files with blacklist
    classifications. Returns a hashtable of project key -> backoff expiry.
  #>
  param([array]$QueuedProjects)
  $BlacklistClasses = @('stuck-no-progress', 'api-blocked', 'budget-exhausted', 'local-checks-stuck')
  $result = @{}
  if (-not $QueuedProjects) { return $result }
  foreach ($proj in $QueuedProjects) {
    $pmDir = Join-Path $proj.path 'runtime\launcher\postmortems'
    if (-not (Test-Path $pmDir)) { continue }
    $latest = Get-ChildItem $pmDir -Filter '*.md' -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { continue }
    # Parse frontmatter
    $lines = Get-Content $latest.FullName -TotalCount 30 -ErrorAction SilentlyContinue
    if (-not $lines) { continue }
    $fm = @{}
    $inFm = $false
    foreach ($line in $lines) {
      if ($line.Trim() -eq '---') {
        if ($inFm) { break }
        $inFm = $true
        continue
      }
      if ($inFm -and $line -match '^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.+)$') {
        $fm[$matches[1]] = $matches[2].Trim().Trim('"')
      }
    }
    $klass = $fm['classification']
    if ($klass -and $BlacklistClasses -contains $klass) {
      $generated = $fm['generated_at']
      $backoffMin = 60
      if ($generated) {
        try {
          $genTime = [datetime]::Parse($generated)
          $expiry = $genTime.AddMinutes($backoffMin)
          if ((Get-Date) -lt $expiry) {
            $result[$proj.key] = $expiry
          }
        } catch {
          $result[$proj.key] = (Get-Date).AddMinutes($backoffMin)
        }
      } else {
        $result[$proj.key] = (Get-Date).AddMinutes($backoffMin)
      }
    }
  }
  return $result
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

    # --- merge postmortem-based blacklist entries ---
    $postmortemBlacklist = Read-BlacklistFromPostmortems -QueuedProjects $queued
    foreach ($key in $postmortemBlacklist.Keys) {
      if ($blacklistSkip.ContainsKey($key)) {
        if ($postmortemBlacklist[$key] -gt $blacklistSkip[$key]) {
          $blacklistSkip[$key] = $postmortemBlacklist[$key]
        }
      } else {
        $blacklistSkip[$key] = $postmortemBlacklist[$key]
      }
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

      # Cannot dispatch a project whose source repo path is unknown
      # (never launched + not in projects.json). Skip, don't guess.
      if ([string]::IsNullOrWhiteSpace($proj.repo_path)) {
        if ($DryRun -and $Once) {
          @{ decision = 'skip-unresolved'; key = $key } | ConvertTo-Json -Compress
        } else {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] skip-unresolved: $key (no repo path; launch it once or add to projects.json)"
        }
        continue
      }

      # First free, resolvable project in FIFO order
      $selected = $proj
      break
    }

    if ($null -eq $selected) {
      if ($DryRun -and $Once) {
        @{ decision = 'idle'; reason = 'no-dispatchable-project' } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: no dispatchable project (all busy/blacklisted/unresolved). Polling in $PollMin min."
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    # --- dispatch the selected project ---
    # Dispatch into the SOURCE repo (repo_path), NOT the ~/.ilk-data data dir.
    $key = $selected.key
    $repo = $selected.repo_path

    if ($DryRun -and $Once) {
      @{ decision = 'dispatch'; key = $key; command = "launch.ps1 -ProjectPath '$repo' -Engine claude-worker" } | ConvertTo-Json -Compress
      return
    }

    if ($DryRun) {
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] DRY-RUN: would dispatch $key via $LaunchScript -ProjectPath '$repo' -Engine claude-worker"
    } else {
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatching $key..."
      try {
        & $LaunchScript -ProjectPath $repo -Engine claude-worker -Force
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
