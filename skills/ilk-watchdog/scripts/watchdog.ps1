<#
.SYNOPSIS
  Auto-restart ilk-loop based on ilk-feedback classification.

.DESCRIPTION
  Polls ~/.ilk-data/projects/<key>/runtime/last-exit.json (preferred)
  and falls back to ~/.ilk-data/projects/<key>/runtime/launcher/running.pid every
  -PollMin minutes.
  When the PID is dead and not all sub-plans are shipped, runs collect.py
  to classify the run, then:
    - WHITELIST (timeout-bound / max-iter-bound / api-flaky / interrupted)
      -> relaunch ilk via launch.ps1 with the postmortem's recommended
         MaxIterations / IterationTimeoutMin
    - BLACKLIST (stuck-no-progress / api-blocked / budget-exhausted /
      local-checks-stuck / dependency-unreachable / unknown) -> print a loud
      BLOCKED banner and exit, leaving ilk stopped for human triage

  -Detach makes this script Start-Process itself in a new desktop window
  (with -NoExit) so the polling loop survives the calling shell.

.PARAMETER ProjectPath
  Absolute path to the project root.

.PARAMETER ProjectName
  Look up the path in ~/.cursor/skills/ilk-launcher/projects.json.

.PARAMETER PollMin
  Polling interval in minutes. Default 5.

.PARAMETER MaxRestarts
  Hard cap on consecutive whitelist relaunches. Default 5.

.PARAMETER Detach
  Spawn this script in a detached PowerShell window and exit immediately.
  Used for unattended operation.

.EXAMPLE
  # Start watching an already-running ilk for myproj:
  .\watchdog.ps1 -ProjectName myproj -Detach

.EXAMPLE
  # Foreground (debug) mode, faster polling:
  .\watchdog.ps1 -ProjectName myproj -PollMin 1 -MaxRestarts 2
#>
[CmdletBinding(DefaultParameterSetName = 'ByName')]
param(
  [Parameter(ParameterSetName = 'ByPath', Mandatory)]
  [string]$ProjectPath,

  [Parameter(ParameterSetName = 'ByName', Mandatory)]
  [string]$ProjectName,

  [int]$PollMin = 5,
  [int]$MaxRestarts = 5,
  [switch]$Detach
)

$ErrorActionPreference = 'Stop'

# --- skill root resolution ---------------------------------------------------
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

# --- constants --------------------------------------------------------------

$LauncherDir   = Join-Path $SkillRoot 'ilk-launcher'
$ProjectsJson  = Join-Path $LauncherDir 'projects.json'
$LaunchScript  = Join-Path $LauncherDir 'scripts\launch.ps1'
$LoopStatusPy  = Join-Path $SkillRoot 'ilk-loop\scripts\loop_status.py'
$CollectPy     = Join-Path $SkillRoot 'ilk-feedback\scripts\collect.py'
$NotifyPy      = Join-Path $SkillRoot 'ilk-watchdog\scripts\ilk_notify.py'

$WhitelistClasses = @('timeout-bound', 'max-iter-bound', 'api-flaky', 'interrupted', 'throttled')
$BlacklistClasses = @('stuck-no-progress', 'api-blocked', 'budget-exhausted', 'local-checks-stuck', 'local-checks-broken', 'dependency-unreachable')

# Grace period after a relaunch before we trust the next "PID dead" signal,
# in case the new ilk hasn't fully started yet.
$RelaunchGraceSec = 90

# --- helpers ----------------------------------------------------------------

function Invoke-IlkNotify {
  <# Fire-and-forget desktop notification. Failure is swallowed. #>
  param([string]$Event, [string]$Project, [string]$Detail = "")
  try {
    $args = @($NotifyPy, '--event', $Event, '--project', $Project)
    if ($Detail) { $args += @('--detail', $Detail) }
    & python @args 2>$null | Out-Null
  } catch {}
}

function Read-ProjectsRegistry {
  if (-not (Test-Path $ProjectsJson)) { return @() }
  $raw = Get-Content $ProjectsJson -Raw | ConvertFrom-Json
  if ($null -eq $raw.projects) { return @() }
  return $raw.projects
}

function Resolve-ProjectByName {
  param([string]$Name)
  $projects = Read-ProjectsRegistry
  $match = $projects | Where-Object { $_.name -eq $Name }
  if (-not $match) {
    $known = ($projects | ForEach-Object { $_.name }) -join ', '
    throw "Project '$Name' not in projects.json. Known: $known"
  }
  return [string]$match.path
}

function Get-ProjectName {
  param([string]$Path)
  $projects = Read-ProjectsRegistry
  $match = $projects | Where-Object { $_.path -eq $Path }
  if ($match) { return [string]$match.name }
  return (Split-Path $Path -Leaf)
}

function Test-ProcessAlive {
  param([int]$ProcessId)
  if ($ProcessId -le 0) { return $false }
  return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Test-ProcessCommandAlive {
  param([int]$ProcessId, [string]$ExpectedCommand)
  if (-not (Test-ProcessAlive -ProcessId $ProcessId)) { return $false }
  $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if (-not $proc) { return $false }
  # Read the full command line, not the interpreter name (ProcessName returns
  # "pwsh"/"powershell", which can never contain "watchdog").
  # Prefer $proc.CommandLine (PowerShell 7+), fall back to CIM.
  $actual = $null
  try { $actual = $proc.CommandLine } catch {}
  if (-not $actual) {
    try {
      $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
      if ($cim) { $actual = $cim.CommandLine }
    } catch {}
  }
  if (-not $actual) { return $true }  # can't determine; alive is sufficient (D3)
  return $actual -like "*$ExpectedCommand*"
}

function Read-ilkPid {
  param([string]$Project)
  $launcherDir = Get-IlkLauncherDir -Project $Project
  if (-not $launcherDir) { return $null }
  $f = Join-Path $launcherDir 'running.pid'
  if (-not (Test-Path $f)) { return $null }
  $raw = (Get-Content $f -Raw).Trim()
  if (-not $raw) { return $null }
  try { return [int]$raw } catch { return $null }
}

function Get-IlkRuntimeDir {
  <#
    Shell out to ilk_paths.py to find ~/.ilk-data/projects/<key>/runtime/.
    Returns $null if the resolver is missing or python errors out, in
    which case we silently fall back to the legacy PID-only watchdog
    mode.
  #>
  param([string]$Project)
  $resolver = Join-Path $SkillRoot 'ilk-loop\scripts\ilk_paths.py'
  if (-not (Test-Path $resolver)) { return $null }
  try {
    $json = & python $resolver --start $Project 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { return $null }
    $obj = $json | ConvertFrom-Json -ErrorAction Stop
    if ($obj.external_runtime_dir) { return [string]$obj.external_runtime_dir }
  } catch {}
  return $null
}

function Get-IlkLauncherDir {
  <#
    Shell out to ilk_paths.py to find ~/.ilk-data/projects/<key>/runtime/launcher/.
    Returns $null if the resolver is missing or python errors out.
  #>
  param([string]$Project)
  $resolver = Join-Path $SkillRoot 'ilk-loop\scripts\ilk_paths.py'
  if (-not (Test-Path $resolver)) { return $null }
  try {
    $json = & python $resolver --start $Project 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { return $null }
    $obj = $json | ConvertFrom-Json -ErrorAction Stop
    if ($obj.external_launcher_dir) { return [string]$obj.external_launcher_dir }
  } catch {}
  return $null
}

function Get-IlkWatchdogDir {
  <#
    Shell out to ilk_paths.py to find ~/.ilk-data/projects/<key>/runtime/watchdog/.
    Returns $null if the resolver is missing or python errors out.
  #>
  param([string]$Project)
  $resolver = Join-Path $SkillRoot 'ilk-loop\scripts\ilk_paths.py'
  if (-not (Test-Path $resolver)) { return $null }
  try {
    $json = & python $resolver --start $Project 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { return $null }
    $obj = $json | ConvertFrom-Json -ErrorAction Stop
    if ($obj.external_watchdog_dir) { return [string]$obj.external_watchdog_dir }
  } catch {}
  return $null
}

function Invoke-PromoteNextMaster {
  <#
    Mark the just-shipped master as `status: shipped` and promote the
    highest-priority queued master to `status: active`. Returns a
    PSCustomObject mirroring the helper's JSON output (or $null on
    error). The mutation is the operator-side queue advancement —
    only call this after a confirmed clean ship.
  #>
  param([string]$Project)
  $script = Join-Path $SkillRoot 'ilk-loop\scripts\promote_next_master.py'
  if (-not (Test-Path $script)) {
    Write-Log "promote_next_master.py not found at $script — cannot advance queue."
    return $null
  }
  try {
    $json = & python $script --project $Project 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) {
      Write-Log "promote_next_master.py exited $LASTEXITCODE; output: $json"
      return $null
    }
    return ($json | ConvertFrom-Json -ErrorAction Stop)
  } catch {
    Write-Log "promote_next_master.py threw: $($_.Exception.Message)"
    return $null
  }
}

function Read-IlkSentinel {
  <#
    Read last-exit.json from the project's runtime dir. Returns a
    PSCustomObject when present and parseable, $null otherwise. The
    sentinel is the authoritative signal for loop state — when present
    it overrides PID-based heuristics.
  #>
  param([string]$RuntimeDir)
  if (-not $RuntimeDir) { return $null }
  $f = Join-Path $RuntimeDir 'last-exit.json'
  if (-not (Test-Path $f)) { return $null }
  try {
    $raw = Get-Content $f -Raw -ErrorAction Stop
    if (-not $raw) { return $null }
    return ($raw | ConvertFrom-Json -ErrorAction Stop)
  } catch { return $null }
}

function Test-AllShipped {
  param([string]$Project)
  if (-not (Test-Path $LoopStatusPy)) { return $false }
  $tmpOut = [IO.Path]::GetTempFileName()
  $tmpErr = [IO.Path]::GetTempFileName()
  try {
    $proc = Start-Process -FilePath python -ArgumentList @($LoopStatusPy) `
      -WorkingDirectory $Project -NoNewWindow -PassThru -Wait `
      -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
    return ($proc.ExitCode -eq 0)
  } finally {
    Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue
  }
}

function Invoke-PostmortemCollect {
  param([string]$Project, [string]$ProjName, [string]$RunId)
  if (-not (Test-Path $CollectPy)) {
    throw "ilk-feedback collect.py not found at $CollectPy"
  }
  $collectArgs = @($CollectPy, '-ProjectPath', $Project, '--quiet')
  if ($RunId) {
    $collectArgs += @('--run-id', $RunId)
  }
  $tmpOut = [System.IO.Path]::GetTempFileName()
  try {
    $proc = Start-Process -FilePath python `
      -ArgumentList $collectArgs `
      -NoNewWindow -PassThru -Wait `
      -RedirectStandardOutput $tmpOut -RedirectStandardError 'NUL'
    if ($proc.ExitCode -ne 0) {
      Write-Log "collect.py exited $($proc.ExitCode)"
      return $null
    }
    $reportPath = (Get-Content $tmpOut -Raw).Trim()
    if (-not $reportPath -or -not (Test-Path $reportPath)) {
      Write-Log "collect.py produced no valid report path: '$reportPath'"
      return $null
    }
    return $reportPath
  } finally {
    Remove-Item $tmpOut -Force -ErrorAction SilentlyContinue
  }
}

function Read-PostmortemFrontmatter {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return @{} }
  $lines = Get-Content $Path -TotalCount 60
  $fm = @{}
  $inFm = $false
  foreach ($line in $lines) {
    if ($line.Trim() -eq '---') {
      if ($inFm) { break }
      $inFm = $true
      continue
    }
    if ($inFm -and $line -match '^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.+)$') {
      $key = $matches[1]
      $val = $matches[2].Trim().Trim('"')
      $fm[$key] = $val
    }
  }
  return $fm
}

function Get-StartupSentinelAction {
  <#
    Pure helper: decide what to do with a terminal sentinel at startup.
    Returns one of: 'stale-ignore', 'work-pending', 'advance', 'classify'.

    - 'stale-ignore': sentinel ended before this watchdog launched — ignore it.
      For success states, always stale-ignore if ended < launch.
      For non-success states, stale-ignore only when a live loop PID is
      detected (LoopAlive $true) — a previous run's leftover sentinel that
      coincides with a fresh loop coming up.
    - 'work-pending': sentinel says success but loop_status says work pending.
    - 'advance': sentinel says success and loop_status confirms all shipped.
    - 'classify': terminal state to adjudicate — hand off to feedback path.
      For non-success: either not stale, or stale with no live loop.
  #>
  param(
    [string]$State,
    [string]$EndedAt,
    [datetime]$LaunchTime,
    [int]$LoopStatusExit,
    [bool]$LoopAlive = $false
  )

  $SuccessStates = @('all-shipped', 'already-shipped', 'shipped')

  # Compute staleness once: parseable EndedAt earlier than LaunchTime
  $ended = [datetime]::MinValue
  $isStale = [datetime]::TryParse($EndedAt, [ref]$ended) -and ($ended -lt $LaunchTime)

  # Non-success terminal
  if ($SuccessStates -notcontains $State) {
    if ($isStale -and $LoopAlive) {
      return 'stale-ignore'
    }
    return 'classify'
  }

  # Success branch (behaviour unchanged)
  if ($isStale) {
    return 'stale-ignore'
  }
  # If EndedAt doesn't parse, skip freshness and rely on cross-check

  # Cross-check: loop_status says work pending despite success sentinel
  if ($LoopStatusExit -ne 0) {
    return 'work-pending'
  }

  # All clear: advance the queue
  return 'advance'
}

function Invoke-LoopStatusExit {
  <#
    Run loop_status.py against the project and return its exit code.
    Returns -1 if the script can't be found or python fails.
  #>
  param([string]$Project)
  if (-not (Test-Path $LoopStatusPy)) { return -1 }
  $tmpOut = [IO.Path]::GetTempFileName()
  $tmpErr = [IO.Path]::GetTempFileName()
  try {
    $proc = Start-Process -FilePath python -ArgumentList @($LoopStatusPy) `
      -WorkingDirectory $Project -NoNewWindow -PassThru -Wait `
      -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
    return $proc.ExitCode
  } catch {
    return -1
  } finally {
    Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue
  }
}

# --- the polling loop -------------------------------------------------------

$script:ProjectPathResolved = $null
$script:ProjectNameResolved = $null
$script:WatchdogStateDir    = $null
$script:ActivityLog         = $null

function Write-Log {
  param([string]$Msg)
  $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  $line = "[$ts] $Msg"
  Write-Host $line
  if ($script:ActivityLog) {
    try {
      # BOM-free UTF-8 — PS 5.1's Add-Content -Encoding utf8 writes a BOM,
      # which causes mojibake for non-ASCII (em-dash, arrow) on zh-CN Windows.
      # [IO.StreamWriter]::new($path, $true, $enc) appends with explicit encoding.
      $enc = New-Object System.Text.UTF8Encoding($false)
      $sw = [IO.StreamWriter]::new($script:ActivityLog, $true, $enc)
      try { $sw.WriteLine($line) } finally { $sw.Close() }
    } catch {}
  }
}

function Write-Banner {
  param([string]$Title, [string]$Body, [ConsoleColor]$Color = 'Yellow')
  $bar = '=' * 78
  Write-Host ''
  Write-Host $bar -ForegroundColor $Color
  Write-Host (' ' + $Title) -ForegroundColor $Color
  Write-Host $bar -ForegroundColor $Color
  if ($Body) {
    foreach ($l in $Body -split "`n") {
      Write-Host (' ' + $l) -ForegroundColor $Color
    }
  }
  Write-Host $bar -ForegroundColor $Color
  Write-Host ''
}

function Run-WatchdogLoop {
  param(
    [string]$Project,
    [string]$ProjName,
    [int]$PollSec,
    [int]$MaxRestartsCap
  )

  $script:ProjectPathResolved = $Project
  $script:ProjectNameResolved = $ProjName
  $script:WatchdogStateDir = Get-IlkWatchdogDir -Project $Project
  if (-not $script:WatchdogStateDir) {
    Write-Banner -Title "WATCHDOG CONFIG ERROR" -Body "Could not resolve external watchdog dir.`nEnsure ilk_paths.py is present or run the migration script." -Color Red
    return
  }
  if (-not (Test-Path $script:WatchdogStateDir)) {
    New-Item -ItemType Directory -Path $script:WatchdogStateDir -Force | Out-Null
  }
  $script:ActivityLog = Join-Path $script:WatchdogStateDir 'activity.log'
  $watchdogPidFile   = Join-Path $script:WatchdogStateDir 'watchdog.pid'

  # refuse to double-run — but only when the recorded PID is a watchdog
  # still actively watching for this project. A lingering -NoExit host of a
  # finished watchdog (or any other process that grabbed the same PID) must
  # NOT block a fresh watchdog.
  if (Test-Path $watchdogPidFile) {
    $existingPid = (Get-Content $watchdogPidFile -Raw).Trim()
    if ($existingPid -and (Test-ProcessAlive -ProcessId ([int]$existingPid)) -and
        (Test-ProcessCommandAlive -ProcessId ([int]$existingPid) -ExpectedCommand 'watchdog.ps1')) {
      Write-Banner -Title "WATCHDOG ALREADY RUNNING" -Body "Project: $ProjName`nExisting watchdog PID: $existingPid`nRefusing to start a second one." -Color Red
      return
    } else {
      Remove-Item $watchdogPidFile -Force -ErrorAction SilentlyContinue
    }
  }

  $PID | Out-File -FilePath $watchdogPidFile -Encoding ascii -NoNewline

  $Host.UI.RawUI.WindowTitle = "watchdog: $ProjName"

  Write-Banner -Title "ilk-watchdog started" -Body @"
Project: $ProjName
ProjectPath: $Project
PollMin: $($PollSec/60)
MaxRestarts: $MaxRestartsCap
Activity log: $script:ActivityLog
Watchdog PID: $PID
Whitelist (auto-restart): $($WhitelistClasses -join ', ')
Blacklist (block): $($BlacklistClasses -join ', ')
"@ -Color Cyan

  $restartCount = 0
  $lastRelaunchAt = [datetime]::MinValue
  $sawAliveOnce = $false
  $LaunchTime = Get-Date
  $RuntimeDir = Get-IlkRuntimeDir -Project $Project
  if ($RuntimeDir) {
    Write-Log "sentinel runtime dir: $RuntimeDir"
  } else {
    Write-Log "sentinel runtime dir not resolvable; falling back to PID-only mode"
  }
  # Successful terminal states the watchdog treats as 'job done, exit
  # cleanly'. Everything else (timeout, no-progress, max-iterations,
  # interrupted, ...) goes through ilk-feedback classification.
  $SuccessStates = @('all-shipped', 'already-shipped', 'shipped')

  try {
    while ($true) {
      # ---------- Sentinel fast-path -----------------------------------
      # When run_ilk_loop_claude.ps1 exits, it writes
      # ~/.ilk-data/projects/<key>/runtime/last-exit.json with
      # state=<stop_reason>. The launcher's wrapper PID survives the
      # loop's real exit (Start-Process -NoExit), so PID-based watchdogs
      # used to miss "shipped" entirely. The sentinel gives us a
      # definitive signal independent of the wrapper.
      $sentinel = Read-IlkSentinel -RuntimeDir $RuntimeDir
      $sentinelTerminal = $false
      if ($sentinel -and $sentinel.state) {
        if ($sentinel.state -eq 'running') {
          # Loop is alive per sentinel. Verify pid if we have one.
          if ($sentinel.pid -and -not (Test-ProcessAlive -ProcessId ([int]$sentinel.pid))) {
            Write-Log ("sentinel says running but pid {0} is dead — treating as stale-running." -f $sentinel.pid)
            $sentinelTerminal = $true
          } else {
            if (-not $sawAliveOnce) {
              $sawAliveOnce = $true
              Write-Log ("ilk loop pid={0} state=running (via sentinel) — watching." -f $sentinel.pid)
            }
            # --- Hung-alive guard (loop_health.hung_alive contract) ---
            # state=running + PID alive, but NO progress for a long time = a
            # wedged loop (e.g. a pre-iter-1 hang). Progress = the JSONL summary
            # mtime (advances per iteration; the sentinel only updates at
            # start/end, so it would false-positive on a healthy long run).
            # Falls back to the sentinel's started_at when the JSONL is absent
            # (the run launched but never logged an iteration).
            $thr = if ($env:ILK_HUNG_THRESHOLD_MIN) { [int]$env:ILK_HUNG_THRESHOLD_MIN } else { 45 }
            # Progress = the MOST RECENT of the JSONL summary mtime (advances per
            # iteration) and the sentinel file mtime (written at run start). Taking
            # the max means a freshly-started run (sentinel just written, but no
            # iteration logged yet, so the JSONL is still from the PREVIOUS run) is
            # NOT mistaken for hung. Plain UTC DateTime subtraction — never
            # DateTimeOffset.ToUnixTimeSeconds (unavailable on some PS builds).
            $jsonl = Join-Path (Split-Path $RuntimeDir -Parent) 'logs\.ilk-loop.log'
            $sentinelFile = Join-Path $RuntimeDir 'last-exit.json'
            $progressTimes = @()
            if (Test-Path $jsonl)        { $progressTimes += (Get-Item $jsonl).LastWriteTimeUtc }
            if (Test-Path $sentinelFile) { $progressTimes += (Get-Item $sentinelFile).LastWriteTimeUtc }
            if ($progressTimes.Count -gt 0) {
              $progressUtc = ($progressTimes | Sort-Object -Descending | Select-Object -First 1)
              $staleSec = ([datetime]::UtcNow - $progressUtc).TotalSeconds
              if ($staleSec -ge ($thr * 60)) {
                $mins = [int]($staleSec / 60)
                Write-Banner -Title "BLOCKED — HUNG-ALIVE" -Body "Project: $ProjName`nstate=running but NO progress for ${mins} min (threshold ${thr}).`nThe loop is wedged (e.g. a pre-iter-1 hang). Restart will not help —`ninspect the runner; fix the cause; relaunch with ilk-launcher." -Color Red
                Invoke-IlkNotify -Event 'blocked' -Project $ProjName -Detail "hung-alive ${mins}m no progress"
                Write-Log ("hung-alive: state=running, no progress for {0} min (threshold {1}) — BLOCKING." -f $mins, $thr)
                return
              }
            }
            Start-Sleep -Seconds $PollSec
            continue
          }
        }
        elseif ($SuccessStates -contains $sentinel.state) {
          $sentinelAction = Get-StartupSentinelAction `
            -State $sentinel.state `
            -EndedAt $sentinel.ended_at `
            -LaunchTime $LaunchTime `
            -LoopStatusExit (Invoke-LoopStatusExit -Project $Project)

          if ($sentinelAction -eq 'stale-ignore') {
            Write-Log ("sentinel state={0} ended_at={1} is older than watchdog launch {2} — ignoring stale sentinel." -f $sentinel.state, $sentinel.ended_at, $LaunchTime)
            Start-Sleep -Seconds $PollSec
            continue
          }
          if ($sentinelAction -eq 'work-pending') {
            Write-Log ("sentinel state={0} but loop_status reports work pending — not draining." -f $sentinel.state)
            Start-Sleep -Seconds $PollSec
            continue
          }
          if ($sentinelAction -eq 'classify') {
            Write-Log ("sentinel terminal state: {0} (iters={1}) — classifying." -f $sentinel.state, $sentinel.iterations)
            $sentinelTerminal = $true
          }
          # 'advance' path: proceed with promote/relaunch as before
          if ($sentinelAction -eq 'advance') {
          Write-Log ("clean ship detected (state={0}, iters={1}). Advancing master queue..." -f $sentinel.state, $sentinel.iterations)
          Invoke-IlkNotify -Event 'ship' -Project $ProjName
          $advance = Invoke-PromoteNextMaster -Project $Project
          if ($advance -and $advance.promoted) {
            Write-Log ("queue advanced: demoted={0}, promoted={1}, queue_remaining={2}" -f $advance.demoted, $advance.promoted, $advance.queue_remaining)
            if (-not (Test-Path $LaunchScript)) {
              Write-Banner -Title "QUEUE ADVANCED — LAUNCHER MISSING" -Body @"
Project: $ProjName
Promoted: $($advance.promoted)
Expected launcher: $LaunchScript

Cannot auto-relaunch. Run ilk-launcher manually.
"@ -Color Yellow
              return
            }
            try {
              & $LaunchScript -ProjectPath $Project -Force
              $lastRelaunchAt = Get-Date
              # Reset state so the next master starts fresh
              $sawAliveOnce = $false
              $restartCount = 0
              Write-Log "next master launched: $($advance.promoted). Resuming polling."
            } catch {
              Write-Banner -Title "QUEUE ADVANCED — RELAUNCH THREW" -Body "Project: $ProjName`nPromoted: $($advance.promoted)`nError: $_" -Color Red
              return
            }
            Start-Sleep -Seconds $PollSec
            continue
          }
          # No next master: queue drained.
          $demotedNote = if ($advance -and $advance.demoted) { "Marked $($advance.demoted) as shipped." } else { "" }
          Invoke-IlkNotify -Event 'queue-drained' -Project $ProjName
          Write-Banner -Title "ALL MASTERS SHIPPED — QUEUE DRAINED" -Body @"
Project: $ProjName
Sentinel: $RuntimeDir\last-exit.json
State: $($sentinel.state)  iters: $($sentinel.iterations)
$demotedNote

Watchdog exiting cleanly. Job done.
"@ -Color Green
          return
          } # end: 'advance' path
        }
        else {
          # Terminal non-success state. Route through Get-StartupSentinelAction
          # so stale sentinels coinciding with a live loop are detected and
          # ignored (the stale non-success race fix).
          $rp = Read-ilkPid -Project $Project
          $loopAlive = ($rp -and (Test-ProcessAlive -ProcessId $rp))

          $sentinelAction = Get-StartupSentinelAction `
            -State $sentinel.state `
            -EndedAt $sentinel.ended_at `
            -LaunchTime $LaunchTime `
            -LoopStatusExit 0 `
            -LoopAlive $loopAlive

          if ($sentinelAction -eq 'stale-ignore') {
            Write-Log ("stale non-success sentinel {0} ended {1} < launch {2} but loop pid alive — ignoring, keep watching." -f $sentinel.state, $sentinel.ended_at, $LaunchTime)
            Start-Sleep -Seconds $PollSec
            continue
          }
          Write-Log ("sentinel terminal state: {0} (iters={1}) — classifying." -f $sentinel.state, $sentinel.iterations)
          $sentinelTerminal = $true
        }
      }

      # ---------- Legacy PID path (no sentinel) ------------------------
      # Skipped entirely when the sentinel already declared a terminal
      # state — going through the wrapper-pid grace dance there would
      # waste a poll cycle and risk false 'still alive' readings.
      if (-not $sentinelTerminal) {
        $ilkPid = Read-ilkPid -Project $Project

      if (-not $ilkPid) {
        if (-not $sawAliveOnce) {
          Write-Log "no ilk PID file at start. Sleeping; will exit if not seen within 10 min."
          Start-Sleep -Seconds $PollSec
          # second attempt
          $ilkPid = Read-ilkPid -Project $Project
          if (-not $ilkPid) {
            Write-Banner -Title "NO ilk PID FILE" -Body "Project: $ProjName`nNo launcher PID file found.`nIs ilk running? Start ilk first, then start watchdog." -Color Red
            return
          }
        } else {
          Write-Banner -Title "ilk PID FILE GONE" -Body "Project: $ProjName`nThe PID file was removed (probably stop.ps1 ran).`nWatchdog exiting — assume manual stop." -Color Yellow
          return
        }
      }

      if (Test-ProcessAlive -ProcessId $ilkPid) {
        if (-not $sawAliveOnce) { $sawAliveOnce = $true; Write-Log "ilk PID $ilkPid alive — watching." }
        Start-Sleep -Seconds $PollSec
        continue
      }

      # PID file exists but process is dead.
      $sinceRelaunch = ((Get-Date) - $lastRelaunchAt).TotalSeconds
      if ($sinceRelaunch -lt $RelaunchGraceSec) {
        Write-Log "ilk PID $ilkPid dead but within $RelaunchGraceSec s grace after relaunch — waiting one more poll."
        Start-Sleep -Seconds $PollSec
        continue
      }

      Write-Log "ilk PID $ilkPid is dead. Investigating..."
      } # end: if (-not $sentinelTerminal)

      if (Test-AllShipped -Project $Project) {
        Write-Banner -Title "ALL SUB-PLANS SHIPPED" -Body "Project: $ProjName`nWatchdog exiting cleanly. Job done." -Color Green
        return
      }

      Write-Log "running ilk-feedback collect.py to classify the run..."
      $sentinelRunId = if ($sentinel -and $sentinel.run_id) { $sentinel.run_id } else { '' }
      $reportPath = Invoke-PostmortemCollect -Project $Project -ProjName $ProjName -RunId $sentinelRunId
      if (-not $reportPath) {
        Invoke-IlkNotify -Event 'postmortem-failed' -Project $ProjName
        Write-Banner -Title "POSTMORTEM FAILED" -Body "Project: $ProjName`ncollect.py did not produce a usable report.`nWatchdog blocking; please triage manually.`nIf the target repo was just 'git init'd with no commits, run /ilk again after the first commit." -Color Red
        return
      }
      Write-Log "postmortem written: $reportPath"

      $fm = Read-PostmortemFrontmatter -Path $reportPath
      $klass = $fm['classification']
      Write-Log "classification: $klass"

      if (-not $klass) {
        Write-Banner -Title "BLOCKED — UNKNOWN CLASSIFICATION" -Body "Project: $ProjName`nReport: $reportPath`nFront-matter has no classification field. Refusing to relaunch blindly." -Color Red
        return
      }

      # L2: resolve label→action via the pure mapping function.
      # Every label collect.py can emit has an explicit action; unknown labels
      # resolve to 'block' (fail-closed, never silently pass).
      $action = Resolve-WatchdogAction -Class $klass
      Write-Log "label '$klass' => action '$action'"

      if ($action -eq 'stop-clean') {
        Write-Log "clean-success: job done. No relaunch, no red banner."
        Invoke-IlkNotify -Event 'ship' -Project $ProjName -Detail "classification: $klass"
        Write-Banner -Title "DONE — $klass" -Body @"
Project: $ProjName
Classification: $klass
Report: $reportPath

Job done. Watchdog exiting cleanly. The scheduler will promote the
next queued master on its next cycle (if any).
"@ -Color Green
        return
      }

      if ($action -eq 'needs-human') {
        $ev = if ($klass -eq 'shipped-unverified') { 'needs-verification' } else { 'needs-human' }
        Write-Log "$klass`: needs human review. No relaunch."
        Invoke-IlkNotify -Event $ev -Project $ProjName -Detail "classification: $klass"
        Write-Banner -Title "NEEDS HUMAN — $($klass.ToUpper())" -Body @"
Project: $ProjName
Classification: $klass
Report: $reportPath

This outcome requires human review — no auto-relaunch.
Read the postmortem for details.
"@ -Color Yellow
        return
      }

      if ($action -eq 'triage') {
        Write-Log "$klass`: triage required. No relaunch."
        Invoke-IlkNotify -Event 'triage' -Project $ProjName -Detail "classification: $klass"
        Write-Banner -Title "TRIAGE — $($klass.ToUpper())" -Body @"
Project: $ProjName
Classification: $klass
Report: $reportPath

This run needs manual triage — no auto-relaunch.
Check runner logs and sentinel state.
"@ -Color Yellow
        return
      }

      if ($action -eq 'block') {
        Invoke-IlkNotify -Event 'blocked' -Project $ProjName -Detail "classification: $klass"
        Write-Banner -Title "BLOCKED — $($klass.ToUpper())" -Body @"
Project: $ProjName
Classification: $klass
Report: $reportPath

Restart will not help this kind of stop. Human triage required.
Read the report tail and decide what to do (fix code / switch model /
raise budget / split sub-plan), then relaunch ilk manually with
ilk-launcher.
"@ -Color Red
        return
      }

      # action == 'relaunch' — fall through to restart logic below

      $restartCount++
      if ($restartCount -gt $MaxRestartsCap) {
        Write-Banner -Title "MAX RESTARTS REACHED ($MaxRestartsCap)" -Body @"
Project: $ProjName
Last classification: $klass
Hard cap is in place to force human review when restarts pile up.
Inspect postmortems under the external launcher dir to see the trend, then
relaunch manually if it still makes sense.
"@ -Color Red
        return
      }

      $recMax = $fm['recommended_max_iterations']
      $recTo  = $fm['recommended_iteration_timeout_min']
      if (-not $recMax -or -not $recTo) {
        Write-Banner -Title "BLOCKED — RECOMMENDATION MISSING" -Body "Project: $ProjName`nReport: $reportPath`nFront-matter lacks recommended params. Refusing to guess." -Color Red
        return
      }

      Write-Log "WHITELIST hit ($klass). Restart $restartCount/$MaxRestartsCap with MaxIterations=$recMax IterationTimeoutMin=$recTo."
      Invoke-IlkNotify -Event 'restart' -Project $ProjName -Detail "classification: $klass"

      if (-not (Test-Path $LaunchScript)) {
        Write-Banner -Title "LAUNCH SCRIPT MISSING" -Body "Expected: $LaunchScript`nWatchdog cannot relaunch." -Color Red
        return
      }

      try {
        & $LaunchScript -ProjectPath $Project -MaxIterations ([int]$recMax) -IterationTimeoutMin ([int]$recTo) -Force
        $lastRelaunchAt = Get-Date
        Write-Log "relaunch issued. Resuming polling."
      } catch {
        Write-Banner -Title "RELAUNCH THREW" -Body "Project: $ProjName`nError: $_`nWatchdog blocking." -Color Red
        return
      }

      Start-Sleep -Seconds $PollSec
    }
  } finally {
    # Only remove the pid file if it still belongs to this process.
    # A stale duplicate's death must not delete a live instance's pid file.
    if (Test-Path $watchdogPidFile) {
      $recordedPid = (Get-Content $watchdogPidFile -Raw).Trim()
      if ($recordedPid -eq "$PID") {
        Remove-Item $watchdogPidFile -Force -ErrorAction SilentlyContinue
      } else {
        Write-Log "watchdog exiting — pid file belongs to $recordedPid, not $PID; leaving it."
      }
    }
    Write-Log "watchdog exiting."
  }
}

# --- label→action resolution (pure, testable) --------------------------------
# Translates a collect.py classification label into a watchdog action.
# The mapping MUST be total — every label collect.py can emit gets an explicit
# action.  Unknown labels resolve to 'block' (fail-closed).
# Actions: relaunch | block | stop-clean | needs-human | triage
#
# L2 table: see skills/ilk-loop/references/orchestration-collaboration.md
function Resolve-WatchdogAction {
  param([string]$Class)

  switch ($Class) {
    # Whitelist — transient failures; relaunch capped by MaxRestarts
    { $WhitelistClasses -contains $_ } { return 'relaunch' }
    # Blacklist — persistent failures; park for human triage
    { $BlacklistClasses -contains $_ } { return 'block' }
    # Job done; no relaunch, no red banner; scheduler promotes next cycle
    'clean-success'       { return 'stop-clean' }
    # All sub-plans shipped but some need manual verification
    'shipped-unverified'  { return 'needs-human' }
    # Toolkit self-edit drift; human review required
    'self-hosting-drift'  { return 'needs-human' }
    # Run started but left no usable records, or never invoked the model
    'no-evidence'         { return 'triage' }
    'never-ran'           { return 'triage' }
    # Fail-closed: unknown label → block (never silently pass)
    default               { return 'block' }
  }
}

# --- dot-source guard --------------------------------------------------------
# When ILK_DOTSOURCE_ONLY is set, skip the main execution block so tests can
# dot-source this file to access functions ($WhitelistClasses, Read-PostmortemFrontmatter,
# Resolve-WatchdogAction, etc.) without starting the poller.

if ($env:ILK_DOTSOURCE_ONLY -eq '1') { return }

# --- main -------------------------------------------------------------------

# resolve project
$resolvedPath = if ($ProjectPath) {
  (Resolve-Path $ProjectPath).Path
} else {
  Resolve-ProjectByName -Name $ProjectName
}
if (-not (Test-Path $resolvedPath)) {
  throw "ProjectPath '$resolvedPath' does not exist."
}
$resolvedName = if ($ProjectName) { $ProjectName } else { Get-ProjectName -Path $resolvedPath }

if ($Detach) {
  $self = $PSCommandPath
  $inner = "& '$self' -ProjectPath '$resolvedPath' -PollMin $PollMin -MaxRestarts $MaxRestarts"
  $proc = Start-Process powershell `
    -ArgumentList @('-NoExit', '-NoProfile', '-Command', $inner) `
    -PassThru
  $detachedWatchdogDir = Get-IlkWatchdogDir -Project $resolvedPath
  $detachedActivityLog = if ($detachedWatchdogDir) { Join-Path $detachedWatchdogDir 'activity.log' } else { '(external dir not resolvable)' }
  Write-Host "[ilk-watchdog] detached window spawned. PID $($proc.Id). Title will be 'watchdog: $resolvedName'." -ForegroundColor Green
  Write-Host "[ilk-watchdog] activity log: $detachedActivityLog"
  return
}

Run-WatchdogLoop `
  -Project $resolvedPath `
  -ProjName $resolvedName `
  -PollSec ($PollMin * 60) `
  -MaxRestartsCap $MaxRestarts
