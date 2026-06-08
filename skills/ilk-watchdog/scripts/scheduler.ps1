<#
.SYNOPSIS
  Single cross-project scheduler (V1.1 — slot pool).

.DESCRIPTION
  Scans all projects for runnable masters, dispatches up to -MaxConcurrent
  ready projects per cycle (each routed to a distinct slot home), promotes
  a queued master if needed, and dispatches via launch.ps1 -Engine claude-worker.

  -DryRun prints the planned decision without executing anything.
  -Once runs a single scan cycle (for tests) instead of the daemon loop.

.PARAMETER PollMin
  Polling interval in minutes. Default 5.

.PARAMETER MaxConcurrent
  Maximum number of concurrent live loops across all projects. Default 5.
  Set to 1 for strict sequential (V1 behavior).

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
  .\scheduler.ps1 -PollMin 2 -MaxConcurrent 3 -MaxDispatches 5
#>
param(
  [int]$PollMin = 5,
  [int]$MaxConcurrent = 5,
  [int]$MaxDispatches = -1,
  [double]$MaxBudgetUsd = 0,
  [switch]$DryRun,
  [switch]$Once,
  [switch]$Detach,
  [switch]$NoLocalChecks
)

$ErrorActionPreference = 'Stop'

# --- skill root resolution ---------------------------------------------------
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

# --- constants ---------------------------------------------------------------
$ScanScript       = Join-Path $PSScriptRoot 'scheduler_scan.py'
$PromoteScript    = Join-Path $SkillRoot 'ilk-loop\scripts\promote_next_master.py'
$LaunchScript     = Join-Path $SkillRoot 'ilk-launcher\scripts\launch.ps1'
$BootstrapScript  = Join-Path $SkillRoot '..\tools\claude-worker\bootstrap.ps1'
$NotifyPy         = Join-Path $SkillRoot 'ilk-watchdog\scripts\ilk_notify.py'

# --- helpers -----------------------------------------------------------------

function Invoke-IlkNotify {
  <# Fire-and-forget desktop notification. Failure is swallowed. #>
  param([string]$Event, [string]$Project, [string]$Detail = "")
  try {
    $args = @($NotifyPy, '--event', $Event, '--project', $Project)
    if ($Detail) { $args += @('--detail', $Detail) }
    & python @args 2>$null | Out-Null
  } catch {}
}

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

function Get-LiveSentinelCount {
  <#
    Count how many projects in the given array currently have a live
    running.pid sentinel. Reuses Test-RunningPid.
  #>
  param([array]$Projects)
  $count = 0
  foreach ($proj in $Projects) {
    if (Test-RunningPid -ProjectDataPath $proj.path) {
      $count++
    }
  }
  return $count
}

function Get-SlotHome {
  <#
    Compute the worker home path for a given slot id.
    Slot 1 = base ~/.claude-worker; slot i>=2 = ~/.claude-worker-<i>.
  #>
  param([int]$SlotId)
  if ($SlotId -le 1) {
    return (Join-Path $HOME '.claude-worker')
  }
  return (Join-Path $HOME ".claude-worker-$SlotId")
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

  # Gate dispatches with -RunLocalChecks by default (AC-1/AC-5).
  # Opt-out: -NoLocalChecks switch or $env:ILK_SCHED_NO_GATES = '1'.
  $runLocalChecksFlag = (-not $NoLocalChecks -and $env:ILK_SCHED_NO_GATES -ne '1')

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

    # --- check concurrency capacity ---
    # Count live sentinels across all scanned projects.
    $liveCount = Get-LiveSentinelCount -Projects $queued
    if ($liveCount -ge $MaxConcurrent) {
      if ($DryRun -and $Once) {
        @{ decision = 'idle'; reason = 'capacity-full'; live = $liveCount; max_concurrent = $MaxConcurrent } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: capacity full ($liveCount/$MaxConcurrent live). Polling in $PollMin min."
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

    # --- iterate projects in FIFO order, fill free slots ---
    $remainingCapacity = $MaxConcurrent - $liveCount
    $toDispatch = @()

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

      # Fill free slots: dispatch while capacity remains.
      if ($toDispatch.Count -lt $remainingCapacity) {
        $toDispatch += $proj
      }
    }

    if ($toDispatch.Count -eq 0) {
      if ($DryRun -and $Once) {
        @{ decision = 'idle'; reason = 'no-dispatchable-project' } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: no dispatchable project (all busy/blacklisted/unresolved). Polling in $PollMin min."
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    # --- promote + dispatch each selected project into a slot ---
    $slotId = 0
    foreach ($proj in $toDispatch) {
      $slotId++
      $key = $proj.key
      $dataPath = $proj.path
      $repo = $proj.repo_path
      $slotHome = Get-SlotHome -SlotId $slotId

      # promote-before-dispatch (multi-master queue advancement)
      if (-not $proj.has_active_master) {
        $plansDir = Join-Path $dataPath 'plans'
        if ($DryRun -and $Once) {
          try {
            $promoJson = & python $PromoteScript --project $dataPath --plans-dir $plansDir --dry-run 2>$null
            if ($LASTEXITCODE -eq 0 -and $promoJson) {
              $promo = ($promoJson | Out-String).Trim() | ConvertFrom-Json
              if ($promo.promoted) {
                @{ decision = 'promote'; key = $key; promoted = $promo.promoted; demoted = $promo.demoted } | ConvertTo-Json -Compress
              }
            }
          } catch {}
        } else {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] promoting queued master for $key..."
          try {
            $promoJson = & python $PromoteScript --project $dataPath --plans-dir $plansDir 2>$null
            if ($LASTEXITCODE -eq 0 -and $promoJson) {
              $promo = ($promoJson | Out-String).Trim() | ConvertFrom-Json
              if ($promo.promoted) {
                Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] promoted $($promo.promoted) (demoted $($promo.demoted))"
              }
            }
          } catch {
            Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] promotion failed for $key`: $_"
          }
        }
      }

      # dispatch into slot home
      if ($DryRun -and $Once) {
        @{ decision = 'dispatch'; key = $key; slot = $slotId; command = "launch.ps1 -ProjectPath '$repo' -Engine claude-worker -WorkerHome '$slotHome'$(if ($runLocalChecksFlag) { ' -RunLocalChecks' })" } | ConvertTo-Json -Compress
      } elseif ($DryRun) {
        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] DRY-RUN: would dispatch $key (slot $slotId) via $LaunchScript -ProjectPath '$repo' -Engine claude-worker -WorkerHome '$slotHome'"
      } else {
        # Ensure slot home exists (lazy-clone from base worker home).
        try {
          & $BootstrapScript -CloneSlot $slotId 2>$null | Out-Null
        } catch {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] warning: slot $slotId bootstrap failed: $_"
        }
        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatching $key (slot $slotId)..."
        try {
          & $LaunchScript -ProjectPath $repo -Engine claude-worker -WorkerHome $slotHome -RunLocalChecks:$runLocalChecksFlag -Force
          $dispatchCount++
          Invoke-IlkNotify -Event 'dispatch' -Project $key -Detail "slot $slotId"
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatched $key (slot $slotId, total: $dispatchCount)"
        } catch {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatch failed for $key`: $_"
          $blacklistSkip[$key] = (Get-Date).AddMinutes(5)
        }
      }
    }

    if ($DryRun -and $Once) { return }

    Start-Sleep -Seconds ($PollMin * 60)
  }
}

# --- entry point -------------------------------------------------------------

if ($Detach) {
  $self = $PSCommandPath
  $inner = "& '$self' -PollMin $PollMin -MaxConcurrent $MaxConcurrent -MaxDispatches $MaxDispatches -MaxBudgetUsd $MaxBudgetUsd"
  if ($NoLocalChecks) { $inner += " -NoLocalChecks" }
  if ($DryRun) {
    Write-Host "[ilk-scheduler] (dry-run) would spawn detached: $inner"
    return
  }
  $proc = Start-Process powershell -ArgumentList @('-NoExit','-NoProfile','-Command',$inner) -PassThru
  Write-Host "[ilk-scheduler] detached window spawned. PID $($proc.Id)."
  return
}

Run-Scheduler
