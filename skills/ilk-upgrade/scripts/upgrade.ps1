<#
.SYNOPSIS
  Pull the latest ilk-skills and make it effective on Windows.

.DESCRIPTION
  Resolves the toolkit clone from the script's own real (symlink-resolved)
  path, pulls the latest, re-runs the installer when needed, and reports
  what changed.

  Modes:
    -Check    read-only staleness report (default)
    -Apply    pull + conditionally re-install
    -Force    override dirty-tree and live-loop guards
    -Help     print this help

  Exit codes:
    0  success (up to date, or applied cleanly)
    1  operational error (network, ff-only failure, etc.)
    2  usage / environment error (not a repo, unknown flag, etc.)

.PARAMETER Check
  Read-only staleness report. Fetches from origin and reports ahead/behind
  status without mutating the working tree or running the installer.
  This is the default mode.

.PARAMETER Apply
  Pull the latest changes with --ff-only, print a changelog, and
  conditionally re-run install.ps1 when drift is detected (copy-installed
  command files, or skills/commands added/removed).

.PARAMETER Force
  Override the dirty-tree abort and the live-loop PID guard. Use with
  caution — updating skill code while a loop is running can cause
  inconsistent behavior.

.PARAMETER NoRestart
  Do not stop/restart the bounceable daemons (tray, scheduler) around the
  pull. By default -Apply bounces them so their in-memory driver re-syncs
  with the freshly-pulled code; with -NoRestart they are left running and
  the command only warns that they hold stale code until restarted manually.

.EXAMPLE
  pwsh skills\ilk-upgrade\scripts\upgrade.ps1
  Read-only staleness report (default mode).

.EXAMPLE
  pwsh skills\ilk-upgrade\scripts\upgrade.ps1 -Check
  Explicit read-only staleness report.

.EXAMPLE
  pwsh skills\ilk-upgrade\scripts\upgrade.ps1 -Apply
  Pull latest and conditionally re-install.

.EXAMPLE
  pwsh skills\ilk-upgrade\scripts\upgrade.ps1 -Apply -Force
  Pull latest even with a dirty tree or live loop.

.NOTES
  Mirrors the behavior of upgrade.sh (the bash engine). See
  2026-06-08-ilk-upgrade-engine-pwsh.md for the contract.
#>
[CmdletBinding()]
param(
  [switch]$Check,
  [switch]$Apply,
  [switch]$Force,
  [switch]$NoRestart,
  [switch]$Help
)

$ErrorActionPreference = "Stop"

# --- data-home resolver (ILK_DATA_HOME → ILK_DATA_DIR → ~/.ilk-data) ---
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_data_dir.ps1")

# --- help ----------------------------------------------------------------------
if ($Help) {
  Get-Help $MyInvocation.MyCommand.Path -Full
  exit 0
}

# --- defaults ------------------------------------------------------------------
# Default mode is check (read-only) — a bare invocation is always safe.
$mode = "check"
if ($Apply) { $mode = "apply" }

# Installer invocation engine: prefer pwsh (PowerShell 7) when present, else
# fall back to the engine running THIS script. Hardcoding `pwsh` breaks on
# machines that only have Windows PowerShell 5.1 (pwsh not on PATH) — the
# installer/reconcile calls would die "pwsh is not recognized".
$InstallerPsExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $InstallerPsExe) { $InstallerPsExe = (Get-Process -Id $PID -ErrorAction SilentlyContinue).Path }
if (-not $InstallerPsExe) { $InstallerPsExe = "powershell.exe" }

# --- repo self-resolution ------------------------------------------------------
# Resolve from the script's own real path (symlink-resolved) up three levels:
#   scripts/ -> ilk-upgrade/ -> skills/ -> repo root
# This mirrors install.ps1's $RepoRoot = Split-Path -Parent $PSCommandPath
# but walks up three levels instead of one.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
  # Fallback for older PowerShell versions that don't set $PSScriptRoot
  $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

# The install puts a DIRECTORY JUNCTION at ~/.{claude,cursor,codex}/skills/ilk-upgrade
# -> <repo>/skills/ilk-upgrade. On Windows, "..\.." traversal and Resolve-Path
# are LEXICAL across a junction (they walk the .claude home's parents, NOT the
# junction target), so naively going up three levels lands in ~/.claude, not the
# repo. Resolve the skill-dir reparse point to its real target first, then go up
# two levels (ilk-upgrade -> skills -> repo). Falls back to the lexical path when
# the script is run from a real (non-junction) clone.
$SkillDir = Split-Path -Parent $ScriptDir            # ...\ilk-upgrade (maybe a junction)
$skillItem = Get-Item -LiteralPath $SkillDir -Force
$realSkillDir = if ($skillItem.Target) { @($skillItem.Target)[0] } else { $SkillDir }
$RepoRoot = (Resolve-Path (Join-Path $realSkillDir "..\..")).Path

if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
  Write-Error "not an ilk-skills clone (no .git): $RepoRoot"
  exit 2
}
if (-not (Test-Path (Join-Path $RepoRoot "install.ps1"))) {
  Write-Error "not an ilk-skills clone (no install.ps1): $RepoRoot"
  exit 2
}

# --- git state guards ----------------------------------------------------------
# Detached HEAD check
try {
  $headRef = git -C $RepoRoot symbolic-ref -q HEAD 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Error "detached HEAD in $RepoRoot — checkout a branch first"
    exit 2
  }
} catch {
  Write-Error "detached HEAD in $RepoRoot — checkout a branch first"
  exit 2
}

# Dirty tree check (relevant for -Apply; -Check just notes it)
$dirtyFiles = git -C $RepoRoot status --porcelain 2>&1
if ($dirtyFiles) {
  if ($mode -eq "apply" -and -not $Force) {
    Write-Error "dirty working tree in $RepoRoot — commit or stash first (or use -Force)"
    exit 2
  }
  Write-Warning "dirty working tree in $RepoRoot"
}

# --- --check: fetch + ahead/behind report --------------------------------------
function Invoke-Check {
  # Fetch silently; tolerate offline gracefully
  try {
    git -C $RepoRoot fetch --quiet origin 2>&1 | Out-Null
  } catch {
    Write-Error "could not reach origin — check your network connection"
    exit 1
  }
  if ($LASTEXITCODE -ne 0) {
    Write-Error "could not reach origin — check your network connection"
    exit 1
  }

  # Resolve upstream; fall back to origin/<branch>
  $branch = git -C $RepoRoot symbolic-ref --short HEAD 2>&1
  $upstream = git -C $RepoRoot for-each-ref --format='%(upstream:short)' "refs/heads/$branch" 2>&1
  if (-not $upstream -or $LASTEXITCODE -ne 0) {
    $upstream = "origin/$branch"
  }

  $behind = git -C $RepoRoot rev-list --count HEAD..$upstream 2>&1
  if ($LASTEXITCODE -ne 0) { $behind = 0 }

  if ([int]$behind -eq 0) {
    Write-Host "up to date"
  } else {
    $plural = if ([int]$behind -ne 1) { "s" } else { "" }
    Write-Host "behind by ${behind} commit${plural} — run with -Apply"
  }
}

# --- live-loop guard -----------------------------------------------------------
# Blocks -Apply when a per-project loop or watchdog is live — those carry
# in-flight work and must not have their code swapped underneath them. The
# cross-project scheduler is NOT checked here: it is a "bounceable" daemon
# (stopped before the pull and restarted after — see Stop/Restart-BounceableDaemons).
function Test-LivePids {
  $dataDir = Get-IlkDataDir
  $projectsDir = Join-Path $dataDir "projects"
  $activePids = @()

  if (-not (Test-Path $projectsDir)) {
    return $true  # no projects dir = no live loop/watchdog PIDs
  }

  # Scan launcher and watchdog PID files
  $pidFiles = @()
  $pidFiles += Get-ChildItem -Path $projectsDir -Filter "running.pid" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.DirectoryName -match "runtime[\\/]launcher$" }
  $pidFiles += Get-ChildItem -Path $projectsDir -Filter "*.pid" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.DirectoryName -match "runtime[\\/]watchdog$" }

  foreach ($pidFile in $pidFiles) {
    # NOTE: do NOT name this $pid — $PID is a read-only automatic variable.
    $procId = (Get-Content -LiteralPath $pidFile.FullName -Raw -ErrorAction SilentlyContinue).Trim()
    if (-not $procId) { continue }

    # Project dir is <projects>/<key>: pidFile lives in <key>/runtime/{launcher,watchdog}/.
    $projDir = $pidFile.Directory.Parent.Parent
    $projectName = $projDir.Name

    # Stale-sentinel guard (mirrors scheduler Test-RunningPid, v0.9.1): a
    # lingering -NoExit worker shell keeps a launcher PID alive after the loop
    # exits. When the project's last-exit.json is terminal (state != running),
    # that PID is a zombie shell, NOT a live loop — don't let it block -Apply.
    # Scoped to launcher PIDs only: a live watchdog must still block.
    if ($pidFile.Directory.Name -eq 'launcher') {
      $sentinel = Join-Path $projDir.FullName 'runtime\last-exit.json'
      if (Test-Path $sentinel) {
        try {
          $state = (Get-Content -LiteralPath $sentinel -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json).state
          if ($state -and $state -ne 'running') { continue }
        } catch { }
      }
    }

    # Check if PID is alive
    try {
      $proc = Get-Process -Id ([int]$procId) -ErrorAction SilentlyContinue
      if ($proc) {
        $activePids += "$projectName (PID $procId)"
      }
    } catch {
      # PID not alive — skip
    }
  }

  if ($activePids.Count -gt 0) {
    Write-Error "live loop/watchdog detected — refusing to update skill code:`n$($activePids | ForEach-Object { "  - $_" } | Out-String)Stop it cleanly first: bash <toolkit>/skills/ilk-watchdog/scripts/stop_watchdog.sh --project-path <project>  (or /ilk-stop). Then re-run, or use -Force to override."
    return $false
  }
  return $true
}

# --- bounceable daemons (tray + scheduler) -------------------------------------
# The system-tray monitor and the cross-project scheduler are long-running
# *observer* processes. A pull swaps the python/orchestration code underneath
# them while their in-memory driver keeps the OLD logic — e.g. the tray runs
# freshly-pulled render_tray.py through a stale tick loop and the tooltip goes
# blank. Unlike per-project loops/watchdogs (which carry in-flight work and so
# BLOCK the upgrade), these are idempotent and safe to bounce: stop before the
# pull, restart the same ones after, so their driver re-syncs with new code.

# Get-IlkDataDir provided by _ilk_data_dir.ps1 (dot-sourced above)

# Returns @{ Running = $bool; Pid = <int|null> } for a pid-file-backed daemon.
function Get-DaemonState {
  param([string]$PidFile)
  if (-not (Test-Path $PidFile)) { return @{ Running = $false; Pid = $null } }
  $procId = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue).Trim()
  if (-not ($procId -match '^\d+$')) { return @{ Running = $false; Pid = $null } }
  $proc = Get-Process -Id ([int]$procId) -ErrorAction SilentlyContinue
  if (-not $proc) {
    # Stale pid file — clean it up.
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    return @{ Running = $false; Pid = $null }
  }
  return @{ Running = $true; Pid = [int]$procId }
}

# Stop the tray + scheduler if running; return the list of names actually
# stopped (so the caller can restart exactly those).
function Stop-BounceableDaemons {
  $dataDir = Get-IlkDataDir
  $stopped = @()
  foreach ($d in @(
    @{ Name = "tray";      PidFile = (Join-Path $dataDir "tray.pid") },
    @{ Name = "scheduler"; PidFile = (Join-Path $dataDir "scheduler.pid") }
  )) {
    $state = Get-DaemonState $d.PidFile
    if (-not $state.Running) { continue }
    Write-Host "  stopping $($d.Name) (PID $($state.Pid))..."
    try {
      Stop-Process -Id $state.Pid -Force -ErrorAction Stop
      Remove-Item -LiteralPath $d.PidFile -Force -ErrorAction SilentlyContinue
      $stopped += $d.Name
    } catch {
      Write-Warning "could not stop $($d.Name) (PID $($state.Pid)): $_"
    }
  }
  # Return the names unrolled; callers wrap with @() so 0/1/2 elements all
  # land as a flat array. (A unary-comma `,$stopped` here would double-wrap
  # under the caller's @() into a nested array — caught in dogfood.)
  return $stopped
}

# Restart the named daemons truly hidden (Start-Process -WindowStyle Hidden,
# NOT -Detach — a -NoExit window would linger; see scheduler-inmemory note).
function Restart-BounceableDaemons {
  param([string[]]$Names)
  if (-not $Names -or $Names.Count -eq 0) { return }
  $psExe = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
  if (-not $psExe) { $psExe = "powershell.exe" }
  foreach ($name in $Names) {
    $script = switch ($name) {
      "tray"      { Join-Path $RepoRoot "tools\tray\ilk-tray.ps1" }
      "scheduler" { Join-Path $RepoRoot "skills\ilk-watchdog\scripts\scheduler.ps1" }
      default     { $null }
    }
    if (-not $script -or -not (Test-Path $script)) {
      Write-Warning "cannot restart $name — script not found"
      continue
    }
    # Restart with default args; a daemon launched with custom args (interval,
    # poll, concurrency) reverts to defaults — re-launch manually to preserve them.
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"")
    Start-Process -FilePath $psExe -ArgumentList $argList -WindowStyle Hidden | Out-Null
    Write-Host "  restarted $name"
  }
}

# --- drift detection -----------------------------------------------------------
# Detects copy-fallback staleness: command files that are plain files (copies)
# rather than symlinks/reparse points. This happens on Windows when Developer
# Mode is off and install.ps1 falls back to copying command files. Such copies
# do NOT auto-update on git pull — this is the exact footgun ilk-upgrade
# exists to fix.
#
# Also detects added/removed skills/commands after a pull (handled in
# Invoke-Apply's diff check).
function Test-Drift {
  $homes = @()
  foreach ($candidate in @((Join-Path $HOME ".cursor"), (Join-Path $HOME ".claude"), (Join-Path $HOME ".codex"))) {
    $cmdDir = Join-Path $candidate "commands"
    if (Test-Path $cmdDir) { $homes += $candidate }
  }

  # NOTE: do NOT name this $home — $HOME is a read-only automatic variable
  # (case-insensitive), so `foreach ($home ...)` throws "Cannot overwrite
  # variable HOME because it is read-only or constant."
  foreach ($agentHome in $homes) {
    $cmdDir = Join-Path $agentHome "commands"
    $cmdFiles = Get-ChildItem -Path $cmdDir -Filter "ilk*" -File -ErrorAction SilentlyContinue
    foreach ($cmdFile in $cmdFiles) {
      $item = Get-Item -LiteralPath $cmdFile.FullName -Force -ErrorAction SilentlyContinue
      if (-not $item) { continue }

      # Check ReparsePoint attribute (covers symlinks, junctions, etc.)
      $isLink = ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq [IO.FileAttributes]::ReparsePoint
      if (-not $isLink) {
        # Plain file = copy-fallback detected. This file won't auto-update.
        return $true
      }

      # Also check LinkType for additional clarity (symlink vs junction)
      # Get-Item with -Force exposes .LinkType on reparse points
      if ($item.LinkType -and $item.LinkType -ne "SymbolicLink" -and $item.LinkType -ne "Junction") {
        # Unexpected link type — treat as drift
        return $true
      }
    }
  }

  return $false  # no drift
}

# --- --apply: ff-only pull + changelog + conditional re-install ----------------
function Invoke-Apply {
  # Self-update guard: refuse if a live loop/watchdog is running
  if (-not $Force) {
    if (-not (Test-LivePids)) {
      exit 1
    }
  }

  $oldRev = git -C $RepoRoot rev-parse HEAD 2>&1

  # Fetch silently
  try {
    git -C $RepoRoot fetch --quiet origin 2>&1 | Out-Null
  } catch {
    Write-Error "could not reach origin — check your network connection"
    exit 1
  }
  if ($LASTEXITCODE -ne 0) {
    Write-Error "could not reach origin — check your network connection"
    exit 1
  }

  # Resolve upstream
  $branch = git -C $RepoRoot symbolic-ref --short HEAD 2>&1
  $upstream = git -C $RepoRoot for-each-ref --format='%(upstream:short)' "refs/heads/$branch" 2>&1
  if (-not $upstream -or $LASTEXITCODE -ne 0) {
    $upstream = "origin/$branch"
  }

  # Already current?
  $behind = git -C $RepoRoot rev-list --count HEAD..$upstream 2>&1
  if ($LASTEXITCODE -ne 0) { $behind = 0 }
  if ([int]$behind -eq 0) {
    Write-Host "already current"
    return
  }

  # Bounce: stop the tray + scheduler before swapping code underneath them.
  # -NoRestart opts out of managing daemons (we only warn if any are running).
  $stoppedDaemons = @()
  if ($NoRestart) {
    $dataDir = Get-IlkDataDir
    $stillUp = @()
    foreach ($d in @(@{ N = "tray"; F = "tray.pid" }, @{ N = "scheduler"; F = "scheduler.pid" })) {
      if ((Get-DaemonState (Join-Path $dataDir $d.F)).Running) { $stillUp += $d.N }
    }
    if ($stillUp.Count -gt 0) {
      Write-Warning "-NoRestart: leaving $($stillUp -join ', ') running — they keep stale in-memory code until you restart them manually."
    }
  } else {
    Write-Host ""
    Write-Host "Stopping bounceable daemons (tray, scheduler)..."
    $stoppedDaemons = @(Stop-BounceableDaemons)
    if ($stoppedDaemons.Count -eq 0) { Write-Host "  (none running)" }
  }

  # Fast-forward pull (suppress git's own output; we print our own summary)
  git -C $RepoRoot pull --ff-only 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "fast-forward pull failed — rebase or reset manually"
    # Restore the daemons we stopped — don't leave the user worse off after a no-op.
    if (-not $NoRestart -and $stoppedDaemons.Count -gt 0) {
      Write-Host "Restarting daemons stopped before the failed pull..."
      Restart-BounceableDaemons $stoppedDaemons
    }
    exit 1
  }

  $newRev = git -C $RepoRoot rev-parse HEAD 2>&1

  # Changelog
  Write-Host ""
  Write-Host "Changelog:"
  git -C $RepoRoot log --oneline "${oldRev}..${newRev}" 2>&1

  # Skill/command changes
  $diffStatus = git -C $RepoRoot diff --name-status $oldRev $newRev -- skills/ commands/ 2>&1
  if ($diffStatus) {
    Write-Host ""
    Write-Host "Skill/command changes:"
    foreach ($line in ($diffStatus -split "`n")) {
      if (-not $line.Trim()) { continue }
      $parts = $line -split "`t"
      $status = $parts[0]
      $path = $parts[1]
      switch -Wildcard ($status) {
        "A*" { Write-Host "  added: $path" }
        "D*" { Write-Host "  removed: $path" }
        "M*" { Write-Host "  modified: $path" }
        "R*" { Write-Host "  renamed: $path" }
        default { Write-Host "  $status`: $path" }
      }
    }
  }

  # Drift detection + conditional re-install
  $needReinstall = $false

  # Check for copy-installed command files
  if (Test-Drift) {
    $needReinstall = $true
  }

  # Check if skills or commands were added/removed
  if ($diffStatus) {
    if ($diffStatus -match '^[AD]') {
      $needReinstall = $true
    }
  }

  if ($needReinstall) {
    Write-Host ""
    Write-Host "Drift detected — re-running installer..."
    $installPs1 = Join-Path $RepoRoot "install.ps1"
    & $InstallerPsExe -NoProfile -ExecutionPolicy Bypass -File $installPs1 -Apply
    Write-Host "Re-install complete. New code is effective next invocation."
  } else {
    Write-Host ""
    Write-Host "Links current, no re-install needed. New code is effective next invocation."
  }

  # Reconcile auto-plan managed block (unconditional on every successful pull)
  $installPs1AutoPlan = Join-Path $RepoRoot "install.ps1"
  & $InstallerPsExe -NoProfile -ExecutionPolicy Bypass -File $installPs1AutoPlan -OnlyAutoPlan -Apply
  Write-Host "Auto-plan block reconciled."

  # Restart the bounceable daemons we stopped — now running the fresh code.
  if (-not $NoRestart -and $stoppedDaemons.Count -gt 0) {
    Write-Host ""
    Write-Host "Restarting bounceable daemons ($($stoppedDaemons -join ', '))..."
    Restart-BounceableDaemons $stoppedDaemons
  }
}

# --- mode dispatch -------------------------------------------------------------
switch ($mode) {
  "check" { Invoke-Check }
  "apply" { Invoke-Apply }
}
