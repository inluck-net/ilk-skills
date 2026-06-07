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
      local-checks-stuck / unknown) -> print a loud BLOCKED banner and
      exit, leaving ilk stopped for human triage

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

$WhitelistClasses = @('timeout-bound', 'max-iter-bound', 'api-flaky', 'interrupted')
$BlacklistClasses = @('stuck-no-progress', 'api-blocked', 'budget-exhausted', 'local-checks-stuck')

# Grace period after a relaunch before we trust the next "PID dead" signal,
# in case the new ilk hasn't fully started yet.
$RelaunchGraceSec = 90

# --- helpers ----------------------------------------------------------------

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
  $actual = $proc.ProcessName.ToLower()
  return $actual -like "*$($ExpectedCommand.ToLower())*"
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
  param([string]$Project, [string]$ProjName)
  if (-not (Test-Path $CollectPy)) {
    throw "ilk-feedback collect.py not found at $CollectPy"
  }
  $tmpOut = [System.IO.Path]::GetTempFileName()
  try {
    $proc = Start-Process -FilePath python `
      -ArgumentList @($CollectPy, '-ProjectPath', $Project, '--quiet') `
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
    - 'work-pending': sentinel says success but loop_status says work pending.
    - 'advance': sentinel says success and loop_status confirms all shipped.
    - 'classify': non-success terminal state — hand off to feedback path.
  #>
  param(
    [string]$State,
    [string]$EndedAt,
    [datetime]$LaunchTime,
    [int]$LoopStatusExit
  )

  $SuccessStates = @('all-shipped', 'already-shipped', 'shipped')

  # Non-success terminal → classify (staleness doesn't matter)
  if ($SuccessStates -notcontains $State) {
    return 'classify'
  }

  # Freshness check: if EndedAt parses and is earlier than LaunchTime, it's stale
  $ended = [datetime]::MinValue
  if ([datetime]::TryParse($EndedAt, [ref]$ended)) {
    if ($ended -lt $LaunchTime) {
      return 'stale-ignore'
    }
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
    try { Add-Content -LiteralPath $script:ActivityLog -Value $line -Encoding utf8 } catch {}
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

  # refuse to double-run
  if (Test-Path $watchdogPidFile) {
    $existingPid = (Get-Content $watchdogPidFile -Raw).Trim()
    if ($existingPid -and (Test-ProcessAlive -ProcessId ([int]$existingPid))) {
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
          # Terminal non-success state. Skip the wrapper-PID dance and
          # jump straight to classification below.
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
      $reportPath = Invoke-PostmortemCollect -Project $Project -ProjName $ProjName
      if (-not $reportPath) {
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

      if ($BlacklistClasses -contains $klass) {
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

      if (-not ($WhitelistClasses -contains $klass)) {
        Write-Banner -Title "BLOCKED — UNKNOWN STATUS '$klass'" -Body "Project: $ProjName`nReport: $reportPath`nNot in whitelist or blacklist; failing safe." -Color Red
        return
      }

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
    Remove-Item $watchdogPidFile -Force -ErrorAction SilentlyContinue
    Write-Log "watchdog exiting."
  }
}

# --- dot-source guard --------------------------------------------------------
# When ILK_DOTSOURCE_ONLY is set, skip the main execution block so tests can
# dot-source this file to access functions ($WhitelistClasses, Read-PostmortemFrontmatter,
# etc.) without starting the poller.

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
