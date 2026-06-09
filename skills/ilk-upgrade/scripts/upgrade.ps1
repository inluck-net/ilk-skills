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
  [switch]$Help
)

$ErrorActionPreference = "Stop"

# --- help ----------------------------------------------------------------------
if ($Help) {
  Get-Help $MyInvocation.MyCommand.Path -Full
  exit 0
}

# --- defaults ------------------------------------------------------------------
# Default mode is check (read-only) — a bare invocation is always safe.
$mode = "check"
if ($Apply) { $mode = "apply" }

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
function Test-LivePids {
  $dataDir = if ($env:ILK_DATA_DIR) { $env:ILK_DATA_DIR } else { Join-Path $HOME ".ilk-data" }
  $projectsDir = Join-Path $dataDir "projects"

  if (-not (Test-Path $projectsDir)) {
    return $true  # no projects dir = no live PIDs
  }

  $activePids = @()

  # Scan launcher and watchdog PID files
  $pidFiles = @()
  $pidFiles += Get-ChildItem -Path $projectsDir -Filter "running.pid" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.DirectoryName -match "runtime[\\/]launcher$" }
  $pidFiles += Get-ChildItem -Path $projectsDir -Filter "*.pid" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.DirectoryName -match "runtime[\\/]watchdog$" }

  foreach ($pidFile in $pidFiles) {
    $pid = (Get-Content -LiteralPath $pidFile.FullName -Raw -ErrorAction SilentlyContinue).Trim()
    if (-not $pid) { continue }

    # Check if PID is alive
    try {
      $proc = Get-Process -Id [int]$pid -ErrorAction SilentlyContinue
      if ($proc) {
        # Extract project name from path
        $projectDir = $pidFile.Directory.Parent.Parent.Parent
        $projectName = $projectDir.Name
        $activePids += "$projectName (PID $pid)"
      }
    } catch {
      # PID not alive — skip
    }
  }

  if ($activePids.Count -gt 0) {
    Write-Error "live loop/watchdog detected — refusing to update skill code:`n$($activePids | ForEach-Object { "  - $_" } | Out-String)Stop the active loop first, or use -Force."
    return $false
  }
  return $true
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

  foreach ($home in $homes) {
    $cmdDir = Join-Path $home "commands"
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

  # Fast-forward pull (suppress git's own output; we print our own summary)
  git -C $RepoRoot pull --ff-only 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "fast-forward pull failed — rebase or reset manually"
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
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $installPs1 -Apply
    Write-Host "Re-install complete. New code is effective next invocation."
  } else {
    Write-Host ""
    Write-Host "Links current, no re-install needed. New code is effective next invocation."
  }

  # Reconcile auto-plan managed block (unconditional on every successful pull)
  $installPs1AutoPlan = Join-Path $RepoRoot "install.ps1"
  & pwsh -NoProfile -ExecutionPolicy Bypass -File $installPs1AutoPlan -OnlyAutoPlan -Apply
  Write-Host "Auto-plan block reconciled."
}

# --- mode dispatch -------------------------------------------------------------
switch ($mode) {
  "check" { Invoke-Check }
  "apply" { Invoke-Apply }
}
