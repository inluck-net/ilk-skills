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

# --- single-instance mutex (Global\ilk-scheduler) ----------------------------
$mutexName = "Global\ilk-scheduler"
$createdNew = $false
$mutexHeld = $false
# Skip the mutex (and its early-exit) when dot-sourced for testing — the test
# only needs the function definitions, not a running scheduler instance.
if ($env:ILK_DOTSOURCE_ONLY -ne '1') {
  $mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$createdNew)
  if (-not $createdNew) {
    Write-Host "[ilk-scheduler] already running (mutex held). Exiting."
    exit 0
  }
  $mutexHeld = $true
}
function Release-SchedulerMutex {
  if ($mutexHeld) {
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
    $mutexHeld = $false
  }
}

# --- skill root resolution ---------------------------------------------------
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

# --- data-home resolver (ILK_DATA_HOME → ILK_DATA_DIR → ~/.ilk-data) --------
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_data_dir.ps1")

# --- constants ---------------------------------------------------------------
$ScanScript       = Join-Path $PSScriptRoot 'scheduler_scan.py'
$PromoteScript    = Join-Path $SkillRoot 'ilk-loop\scripts\promote_next_master.py'
$LaunchScript     = Join-Path $SkillRoot 'ilk-launcher\scripts\launch.ps1'
# bootstrap.ps1 lives in the repo's tools/ (a SIBLING of skills/), NOT under the
# installed skills home. Each skill dir is an individual symlink/junction into
# the repo, so `$SkillRoot\..\tools` is LEXICAL and lands in ~/.claude\tools
# (no such dir) — that mis-resolution is what made every dispatch log
# "slot N bootstrap failed: cannot find bootstrap.ps1". Resolve THIS script's
# own real (symlink-followed) location up to the repo root instead, mirroring
# ilk-upgrade/scripts/upgrade.ps1. Fall back to the lexical join for a real
# (non-symlinked) clone where skills/ and tools/ are true siblings.
$BootstrapScript = $null
try {
  $watchdogSkillDir = Split-Path -Parent $PSScriptRoot          # ...\ilk-watchdog (maybe a junction)
  $wdItem = Get-Item -LiteralPath $watchdogSkillDir -Force
  $realWatchdogDir = if ($wdItem.Target) { @($wdItem.Target)[0] } else { $watchdogSkillDir }
  $repoRootFromLink = (Resolve-Path (Join-Path $realWatchdogDir '..\..')).Path
  $candidate = Join-Path $repoRootFromLink 'tools\claude-worker\bootstrap.ps1'
  if (Test-Path $candidate) { $BootstrapScript = $candidate }
} catch {}
if (-not $BootstrapScript) {
  $BootstrapScript = Join-Path $SkillRoot '..\tools\claude-worker\bootstrap.ps1'
}
$NotifyPy         = Join-Path $SkillRoot 'ilk-watchdog\scripts\ilk_notify.py'
$WatchdogPs1      = Join-Path $PSScriptRoot 'watchdog.ps1'
$SchedulerLogDir  = Join-Path (Get-IlkDataDir) 'logs'
$SchedulerLogFile = Join-Path $SchedulerLogDir 'scheduler.log'
# PID file — published by the long-running daemon so /ilk-upgrade can detect
# and bounce the scheduler (mirrors scheduler.sh's ${HOME}/.ilk-data/scheduler.pid;
# honors ILK_DATA_DIR like the upgrade guard does).
$SchedulerDataDir = Get-IlkDataDir
$SchedulerPidFile = Join-Path $SchedulerDataDir 'scheduler.pid'

# --- helpers -----------------------------------------------------------------

function Write-SchedulerLog {
  <# Append a decision line to scheduler.log (BOM-free, timestamped). #>
  param([string]$Decision, [string]$Key = "", [string]$Reason = "")
  try {
    if (-not (Test-Path $SchedulerLogDir)) {
      New-Item -ItemType Directory -Path $SchedulerLogDir -Force | Out-Null
    }
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = if ($Key) { "[$ts] $Decision`: $Key" } else { "[$ts] $Decision" }
    if ($Reason) { $line += " ($Reason)" }
    [System.IO.File]::AppendAllText($SchedulerLogFile, "$line`n", [System.Text.UTF8Encoding]::new($false))
  } catch {}
}

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
  $alive = [bool](Get-Process -Id $procId -ErrorAction SilentlyContinue)
  if (-not $alive) { return $false }

  # Stale-sentinel cross-check: even if the pid is alive, a terminal
  # last-exit.json means the loop already finished.  The lingering
  # -NoExit shell keeps the pid alive past the loop's real exit.
  $sentinelFile = Join-Path $ProjectDataPath 'runtime\last-exit.json'
  if (Test-Path $sentinelFile) {
    try {
      $sentinel = Get-Content $sentinelFile -Raw -ErrorAction Stop
      if ($sentinel) {
        $obj = $sentinel | ConvertFrom-Json -ErrorAction Stop
        if ($obj.state -and $obj.state -ne 'running') {
          return $false  # terminal state — project is free
        }
      }
    } catch {}
  }
  return $true
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
  <#
    Run scheduler_scan.py and parse its JSON stdout.

    Captures stdout and stderr to SEPARATE temp files (never `2>&1`): under
    this script's `$ErrorActionPreference='Stop'`, merging a native command's
    stderr makes PowerShell wrap each stderr line in a NativeCommandError and
    raise a TERMINATING error — so a single Python warning/traceback on stderr
    used to kill the whole daemon even on exit 0. Reading stdout back as UTF-8
    matches scheduler_scan's `sys.stdout.reconfigure(encoding="utf-8")`, so a
    non-ASCII path/title can no longer corrupt the JSON. Throws on non-zero
    exit or empty stdout; the caller is responsible for surviving the throw.
  #>
  $outFile = [System.IO.Path]::GetTempFileName()
  $errFile = [System.IO.Path]::GetTempFileName()
  try {
    $proc = Start-Process -FilePath 'python' -ArgumentList @($ScanScript) `
      -NoNewWindow -Wait -PassThru `
      -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    $exit = $proc.ExitCode
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $stdout = ''
    $stderr = ''
    try { $stdout = [System.IO.File]::ReadAllText($outFile, $utf8) } catch {}
    try { $stderr = [System.IO.File]::ReadAllText($errFile, $utf8) } catch {}
    if ($exit -ne 0) {
      throw "scheduler_scan.py exited $exit. stderr: $($stderr.Trim())"
    }
    if ([string]::IsNullOrWhiteSpace($stdout)) {
      throw "scheduler_scan.py produced no stdout. stderr: $($stderr.Trim())"
    }
    return ($stdout.Trim() | ConvertFrom-Json)
  } finally {
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
  }
}

function Read-BlacklistFromPostmortems {
  <#
    Scan queued projects for the blacklist-vs-resolve-ack decision. Delegates to
    blacklist_status.py (the single source of truth shared with scheduler.sh) so
    the cleared_at >= generated_at ack-override logic lives in exactly one place.
    Returns a hashtable of project key -> backoff expiry [datetime] for projects
    that are currently blacklisted (a resolve-ack or auto-expiry omits the key).
  #>
  param([array]$QueuedProjects)
  $result = @{}
  if (-not $QueuedProjects) { return $result }
  $blScript = Join-Path $PSScriptRoot 'blacklist_status.py'
  foreach ($proj in $QueuedProjects) {
    try {
      $raw = (& python $blScript check --project $proj.path 2>$null | Out-String).Trim()
      if (-not $raw) { continue }
      $obj = $raw | ConvertFrom-Json
      if ($obj.blacklisted) {
        if ($obj.expiry) {
          try { $result[$proj.key] = [datetime]::Parse($obj.expiry) }
          catch { $result[$proj.key] = (Get-Date).AddMinutes(60) }
        } else {
          $result[$proj.key] = (Get-Date).AddMinutes(60)
        }
      }
    } catch { }
  }
  return $result
}

function Test-SchedulerSkip {
  <#
    Decide whether to skip dispatching a project THIS cycle. PURE: depends only
    on the inputs passed, never on hidden cross-cycle accumulator state — that
    statelessness is the whole point (the old in-memory $blacklistSkip merge
    wedged a project off the queue when a stale entry outlived a not-blacklisted
    on-disk flip). Returns the skip reason ('blacklist' | 'backoff') or $null
    (dispatchable).

      PostmortemBlacklist : key -> expiry, recomputed FRESH each cycle from
                            blacklist_status.py (the on-disk source of truth).
                            NOT persisted across cycles.
      BackoffSkip         : key -> expiry for transient scheduler-observed
                            backoffs (rapid-terminal, dispatch-failure) that
                            legitimately need cross-cycle memory.
  #>
  param(
    [Parameter(Mandatory)][string]$Key,
    [hashtable]$PostmortemBlacklist,
    [hashtable]$BackoffSkip,
    [datetime]$Now = (Get-Date)
  )
  if ($PostmortemBlacklist -and $PostmortemBlacklist.ContainsKey($Key) -and $Now -lt $PostmortemBlacklist[$Key]) {
    return 'blacklist'
  }
  if ($BackoffSkip -and $BackoffSkip.ContainsKey($Key) -and $Now -lt $BackoffSkip[$Key]) {
    return 'backoff'
  }
  return $null
}

function Get-RapidTerminalBackoff {
  <#
    Pure helper: given the current rapid-terminal count and whether a fresh
    rapid terminal was detected THIS cycle, return the new count and an
    optional backoff expiry.  Arm-once-at-detection, decay-on-expiry: the
    count resets to 0 when no fresh detection occurs, so a stale >=2 count
    can never re-arm the backoff across cycles.
  #>
  param(
    [int]$CurrentCount,
    [bool]$DetectedThisCycle,
    [int]$Threshold = 2,
    [int]$BackoffMinutes = 5,
    [datetime]$Now = (Get-Date)
  )
  if ($DetectedThisCycle) {
    $n = $CurrentCount + 1
    if ($n -ge $Threshold) {
      return [pscustomobject]@{ Count = $n; BackoffUntil = $Now.AddMinutes($BackoffMinutes) }
    }
    return [pscustomobject]@{ Count = $n; BackoffUntil = $null }
  }
  # No fresh detection — decay the counter so a stale >=2 count cannot
  # re-arm backoff across cycles (the wedge that locked out math-blocks).
  return [pscustomobject]@{ Count = 0; BackoffUntil = $null }
}

function Test-RapidTerminal {
  <#
    Decide whether a terminal sentinel represents a "rapid terminal" — a run
    that ended in under $ThresholdSeconds. PURE: reads only the sentinel object
    and dispatch time passed in, no I/O.

    Correlation: the sentinel must belong to THIS dispatch — its started_at
    must be >= dispatchTime (minus small clock skew). A prior run's sentinel
    (started_at < dispatch) is stale and is never classified as rapid,
    regardless of what the file mtime shows.

    Returns $true if rapid, $false otherwise.
  #>
  param(
    $Sentinel,                                          # parsed last-exit.json (PSCustomObject) or $null
    [Parameter(Mandatory)][datetime]$DispatchTime,
    [int]$ThresholdSeconds = 60,
    [int]$SkewSeconds = 5
  )
  if (-not $Sentinel) { return $false }
  if (-not $Sentinel.state -or $Sentinel.state -eq 'running') { return $false }
  if (-not $Sentinel.started_at -or -not $Sentinel.ended_at)   { return $false }
  $started = [datetime]::Parse($Sentinel.started_at)
  $ended   = [datetime]::Parse($Sentinel.ended_at)
  # Correlation: the sentinel must belong to THIS dispatch — its run
  # started at/after we dispatched (minus small clock skew). A prior
  # run's sentinel (started_at < dispatch) is stale → not rapid.
  if ($started -lt $DispatchTime.AddSeconds(-$SkewSeconds)) { return $false }
  $dur = ($ended - $started).TotalSeconds
  return ($dur -ge 0 -and $dur -lt $ThresholdSeconds)
}

function Get-ReapableShells {
  <#
    Pure selector: given an enumerated candidate process list + per-project
    run state, returns exactly the pids that are safe to kill (terminal/orphaned
    ilk shells), excluding the live set.

    .PARAMETER Candidates
      Array of objects with ProcessId, CommandLine, and WindowTitle properties.
    .PARAMETER ProjectState
      Hashtable of project key -> @{RunningPid; State} where State is the
      sentinel state from last-exit.json.
    .PARAMETER SchedulerPid
      The live scheduler pid (from scheduler.pid file).
    .PARAMETER TrayPid
      The tray pid (from tray.pid file).
    .PARAMETER SelfPid
      The current process pid (to never reap self).
  #>
  param(
    [Parameter(Mandatory)][array]$Candidates,
    [Parameter(Mandatory)][hashtable]$ProjectState,
    [int]$SchedulerPid = 0,
    [int]$TrayPid = 0,
    [int]$SelfPid = 0
  )

  $reapable = @()

  foreach ($c in $Candidates) {
    $candidatePid = $c.ProcessId
    $cmdLine = $c.CommandLine
    $windowTitle = $c.WindowTitle

    # Never reap self, scheduler pid, or tray pid
    if ($candidatePid -eq $SelfPid -or $candidatePid -eq $SchedulerPid -or $candidatePid -eq $TrayPid) {
      continue
    }

    # Determine if this is an ilk shell and extract project key
    $isIlkShell = $false
    $shellType = $null
    $projectKey = $null

    if ($cmdLine -match 'scheduler\.ps1') {
      $isIlkShell = $true
      $shellType = 'scheduler'
    } elseif ($cmdLine -match 'watchdog\.ps1') {
      $isIlkShell = $true
      $shellType = 'watchdog'
      # Extract project key from -ProjectPath parameter
      if ($cmdLine -match '-ProjectPath\s+([^\s]+)') {
        $path = $matches[1].TrimEnd('\', '/')
        $projectKey = Split-Path -Leaf $path
      }
    } elseif ($cmdLine -match 'claude-worker') {
      # Loop shell: check for window title 'ilk: <name>'
      if ($windowTitle -match '^ilk:\s+(.+)$') {
        $isIlkShell = $true
        $shellType = 'loop'
        $projectKey = $matches[1]
      }
    }

    if (-not $isIlkShell) {
      continue
    }

    # Check if this shell is reapable based on its type
    $isReapable = $false

    switch ($shellType) {
      'scheduler' {
        # Scheduler shell is reapable if its pid ≠ the live scheduler pid
        # (the live daemon is NEVER reapable)
        if ($candidatePid -ne $SchedulerPid) {
          $isReapable = $true
        }
      }
      'loop' {
        # Loop shell is reapable if:
        # - Project state shows terminal (state ≠ 'running'), OR
        # - Shell's pid ≠ project's current running.pid
        if ($projectKey -and $ProjectState.ContainsKey($projectKey)) {
          $state = $ProjectState[$projectKey]
          if ($state.State -ne 'running' -or $candidatePid -ne $state.RunningPid) {
            $isReapable = $true
          }
        } else {
          # No project state found - treat as reapable (orphaned)
          $isReapable = $true
        }
      }
      'watchdog' {
        # Watchdog shell is reapable if the project has no live running.pid
        if ($projectKey -and $ProjectState.ContainsKey($projectKey)) {
          $state = $ProjectState[$projectKey]
          if (-not $state.RunningPid -or $state.RunningPid -eq 0) {
            $isReapable = $true
          }
        } else {
          # No project state found - treat as reapable (orphaned)
          $isReapable = $true
        }
      }
    }

    if ($isReapable) {
      $reapable += $candidatePid
    }
  }

  return $reapable
}

# --- main loop ---------------------------------------------------------------

function Run-Scheduler {
  $dispatchCount = 0
  $backoffSkip = @{}    # transient cross-cycle backoffs ONLY (rapid-terminal, dispatch-fail). Postmortem blacklist is recomputed fresh each cycle — never accumulated here.
  $dispatchTime = @{}   # project key -> [datetime] of last dispatch
  $rapidTerminalCount = @{}  # project key -> int consecutive rapid-terminal count

  # Gate dispatches with -RunLocalChecks by default (AC-1/AC-5).
  # Opt-out: -NoLocalChecks switch or $env:ILK_SCHED_NO_GATES = '1'.
  $runLocalChecksFlag = (-not $NoLocalChecks -and $env:ILK_SCHED_NO_GATES -ne '1')

  while ($true) {
    # --- scan for queued projects ---
    # A scan failure must NOT kill the daemon: the scanner reads plan files
    # that live loops are concurrently writing, so a transient read-race (or
    # any python hiccup) is expected. Log it, treat this cycle as idle, and
    # poll again. Before this guard the throw propagated out of the while loop
    # and the whole scheduler exited (the 2026-06-30 three-project crash).
    try {
      $queued = Invoke-SchedulerScan
    } catch {
      if ($DryRun -and $Once) {
        Write-SchedulerLog -Decision 'scan-error' -Reason "$_"
        @{ decision = 'scan-error'; reason = "$_" } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] warning: scan failed this cycle (daemon survives): $_"
      Write-SchedulerLog -Decision 'scan-error' -Reason "$_"
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    if (-not $queued -or $queued.Count -eq 0) {
      if ($DryRun -and $Once) {
        Write-SchedulerLog -Decision 'idle' -Reason 'all-queues-empty'
        @{ decision = 'idle'; reason = 'all-queues-empty' } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: all queues empty. Polling in $PollMin min."
      Write-SchedulerLog -Decision 'idle' -Reason 'all-queues-empty'
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    # --- check budget ceiling ---
    # MaxDispatches -1 = unlimited; >= 0 = hard ceiling.
    if ($MaxDispatches -ge 0 -and $dispatchCount -ge $MaxDispatches) {
      if ($DryRun -and $Once) {
        Write-SchedulerLog -Decision 'idle' -Reason 'budget-ceiling'
        @{ decision = 'idle'; reason = 'budget-ceiling' } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: budget ceiling (dispatched $dispatchCount/$MaxDispatches). Polling in $PollMin min."
      Write-SchedulerLog -Decision 'idle' -Reason 'budget-ceiling'
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    # --- check concurrency capacity ---
    # Count live sentinels across all scanned projects.
    $liveCount = Get-LiveSentinelCount -Projects $queued
    if ($liveCount -ge $MaxConcurrent) {
      if ($DryRun -and $Once) {
        Write-SchedulerLog -Decision 'idle' -Reason "capacity-full ($liveCount/$MaxConcurrent)"
        @{ decision = 'idle'; reason = 'capacity-full'; live = $liveCount; max_concurrent = $MaxConcurrent } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: capacity full ($liveCount/$MaxConcurrent live). Polling in $PollMin min."
      Write-SchedulerLog -Decision 'idle' -Reason "capacity-full ($liveCount/$MaxConcurrent)"
      Start-Sleep -Seconds ($PollMin * 60)
      continue
    }

    # --- recompute postmortem blacklist FRESH each cycle (no accumulator) ---
    # A project is postmortem-blacklisted iff THIS cycle's on-disk decision
    # (blacklist_status.py — encodes the 60-min expiry + resolve-ack) says so.
    # Deliberately NOT merged into a cross-cycle map: that merge wedged projects
    # off the queue when a stale entry outlived a not-blacklisted flip
    # (resolve-ack / expiry / clean-success). See scheduler-stateless-blacklist.
    $postmortemBlacklist = Read-BlacklistFromPostmortems -QueuedProjects $queued

    # --- iterate projects in FIFO order, fill free slots ---
    $remainingCapacity = $MaxConcurrent - $liveCount
    $toDispatch = @()

    foreach ($proj in $queued) {
      $key = $proj.key
      $path = $proj.path

      # blacklist / backoff skip — postmortem set is FRESH this cycle (never
      # accumulated); backoff is the transient cross-cycle map.
      $skipReason = Test-SchedulerSkip -Key $key -PostmortemBlacklist $postmortemBlacklist -BackoffSkip $backoffSkip
      if ($skipReason) {
        $decision = if ($skipReason -eq 'blacklist') { 'skip-blacklist' } else { 'skip-backoff' }
        if ($DryRun -and $Once) {
          Write-SchedulerLog -Decision $decision -Key $key
          @{ decision = $decision; key = $key } | ConvertTo-Json -Compress
        } else {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ${decision}: $key"
          Write-SchedulerLog -Decision $decision -Key $key
        }
        continue
      } elseif ($backoffSkip.ContainsKey($key)) {
        # backoff window elapsed — clean up the transient entry and decay
        # the rapid-terminal counter so the project re-enters with a fresh slate.
        $backoffSkip.Remove($key)
        $rapidTerminalCount[$key] = 0
      }

      # --- rapid-terminal check: project went terminal within ~60s of dispatch ---
      if ($dispatchTime.ContainsKey($key)) {
        $sentinelFile = Join-Path $proj.path 'runtime\last-exit.json'
        if (Test-Path $sentinelFile) {
          # Parse the sentinel JSON (guard parse errors → treat as $null).
          $sentinel = $null
          try {
            $sentinel = Get-Content $sentinelFile -Raw -Encoding utf8 | ConvertFrom-Json
          } catch {}

          $isRapid = Test-RapidTerminal -Sentinel $sentinel -DispatchTime $dispatchTime[$key]
          $curCount = if ($rapidTerminalCount.ContainsKey($key)) { $rapidTerminalCount[$key] } else { 0 }
          $rtb = Get-RapidTerminalBackoff -CurrentCount $curCount -DetectedThisCycle $isRapid
          $rapidTerminalCount[$key] = $rtb.Count
          if ($rtb.BackoffUntil) {
            # Arm the backoff ONCE at detection (not re-armed every cycle).
            $backoffSkip[$key] = $rtb.BackoffUntil
            # Compute real duration for logging (ended_at - started_at).
            $durSec = 0
            if ($sentinel.started_at -and $sentinel.ended_at) {
              $durSec = [int]([datetime]::Parse($sentinel.ended_at) - [datetime]::Parse($sentinel.started_at)).TotalSeconds
            }
            if ($DryRun -and $Once) {
              Write-SchedulerLog -Decision 'skip-rapid-terminal' -Key $key -Reason "count=$($rtb.Count) elapsed=${durSec}s"
              @{ decision = 'skip-rapid-terminal'; key = $key; count = $rtb.Count } | ConvertTo-Json -Compress
            } else {
              Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] skip-rapid-terminal: $key (count=$($rtb.Count), elapsed=${durSec}s)"
              Write-SchedulerLog -Decision 'skip-rapid-terminal' -Key $key -Reason "count=$($rtb.Count) elapsed=${durSec}s"
            }
            continue
          }
        }
      }

      if (Test-RunningPid -ProjectDataPath $path) {
        if ($DryRun -and $Once) {
          Write-SchedulerLog -Decision 'skip-busy' -Key $key
          @{ decision = 'skip-busy'; key = $key } | ConvertTo-Json -Compress
        } else {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] skip-busy: $key"
          Write-SchedulerLog -Decision 'skip-busy' -Key $key
        }
        continue
      }

      # Cannot dispatch a project whose source repo path is unknown
      # (never launched + not in projects.json). Skip, don't guess.
      if ([string]::IsNullOrWhiteSpace($proj.repo_path)) {
        if ($DryRun -and $Once) {
          Write-SchedulerLog -Decision 'skip-unresolved' -Key $key
          @{ decision = 'skip-unresolved'; key = $key } | ConvertTo-Json -Compress
        } else {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] skip-unresolved: $key (no repo path; launch it once or add to projects.json)"
          Write-SchedulerLog -Decision 'skip-unresolved' -Key $key
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
        Write-SchedulerLog -Decision 'idle' -Reason 'no-dispatchable-project'
        @{ decision = 'idle'; reason = 'no-dispatchable-project' } | ConvertTo-Json -Compress
        return
      }
      Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] idle: no dispatchable project (all busy/blacklisted/unresolved). Polling in $PollMin min."
      Write-SchedulerLog -Decision 'idle' -Reason 'no-dispatchable-project'
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
                Write-SchedulerLog -Decision 'promote' -Key "$key -> $($promo.promoted)"
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
                Write-SchedulerLog -Decision 'promote' -Key "$key -> $($promo.promoted)"
              }
            }
          } catch {
            Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] promotion failed for $key`: $_"
          }
        }
      }

      # dispatch into slot home
      if ($DryRun -and $Once) {
        Write-SchedulerLog -Decision 'dispatch' -Key "$key (slot $slotId)"
        $dispatchTime[$key] = Get-Date
        @{ decision = 'dispatch'; key = $key; slot = $slotId; command = "launch.ps1 -ProjectPath '$repo' -Engine claude-worker -WorkerHome '$slotHome'$(if ($runLocalChecksFlag) { ' -RunLocalChecks' })"; watchdog = "watchdog.ps1 -ProjectPath '$repo' -Detach" } | ConvertTo-Json -Compress
      } elseif ($DryRun) {
        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] DRY-RUN: would dispatch $key (slot $slotId) via $LaunchScript -ProjectPath '$repo' -Engine claude-worker -WorkerHome '$slotHome'"
        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] DRY-RUN: would attach watchdog via $WatchdogPs1 -ProjectPath '$repo' -Detach"
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
          $dispatchTime[$key] = Get-Date
          Invoke-IlkNotify -Event 'dispatch' -Project $key -Detail "slot $slotId"
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatched $key (slot $slotId, total: $dispatchCount)"
          Write-SchedulerLog -Decision 'dispatch' -Key "$key (slot $slotId)"
          # Attach watchdog for this dispatch (supervises the run: classify-on-stop,
          # resume whitelist / block blacklist).  The watchdog has its own
          # double-spawn guard (watchdog.pid) so this is idempotent.
          try {
            $watchArgs = @(
              '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $WatchdogPs1,
              '-ProjectPath', $repo,
              '-PollMin', '5',
              '-MaxRestarts', '5',
              '-Detach'
            )
            & powershell @watchArgs 2>$null | Out-Null
            Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] watchdog attached for $key"
          } catch {
            Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] warning: watchdog spawn failed for $key`: $_"
          }
        } catch {
          Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] dispatch failed for $key`: $_"
          $backoffSkip[$key] = (Get-Date).AddMinutes(5)
        }
      }
    }

    if ($DryRun -and $Once) { return }

    Start-Sleep -Seconds ($PollMin * 60)
  }
}

# --- entry point -------------------------------------------------------------

# Dot-source guard: tests dot-source this file with ILK_DOTSOURCE_ONLY=1 to
# reach Test-SchedulerSkip (and other functions) without starting the poll loop.
if ($env:ILK_DOTSOURCE_ONLY -eq '1') { return }

if ($Detach) {
  $self = $PSCommandPath
  $inner = "& '$self' -PollMin $PollMin -MaxConcurrent $MaxConcurrent -MaxDispatches $MaxDispatches -MaxBudgetUsd $MaxBudgetUsd"
  if ($NoLocalChecks) { $inner += " -NoLocalChecks" }
  if ($DryRun) {
    Write-Host "[ilk-scheduler] (dry-run) would spawn detached: $inner"
    Release-SchedulerMutex
    return
  }
  $proc = Start-Process powershell -ArgumentList @('-NoExit','-NoProfile','-Command',$inner) -PassThru
  Write-Host "[ilk-scheduler] detached window spawned. PID $($proc.Id)."
  Release-SchedulerMutex
  return
}

try {
  # Publish our PID (daemon path only — the -Detach parent above returns
  # without writing, so it never clobbers this child's file).
  try {
    if (-not (Test-Path $SchedulerDataDir)) { New-Item -ItemType Directory -Path $SchedulerDataDir -Force | Out-Null }
    Set-Content -LiteralPath $SchedulerPidFile -Value $PID -Encoding ascii
  } catch {}
  Run-Scheduler
} finally {
  # Remove our PID file only if it still points at us.
  try {
    if (Test-Path $SchedulerPidFile) {
      $rec = (Get-Content -LiteralPath $SchedulerPidFile -Raw -ErrorAction SilentlyContinue).Trim()
      if ($rec -eq "$PID") { Remove-Item -LiteralPath $SchedulerPidFile -Force -ErrorAction SilentlyContinue }
    }
  } catch {}
  Release-SchedulerMutex
}
