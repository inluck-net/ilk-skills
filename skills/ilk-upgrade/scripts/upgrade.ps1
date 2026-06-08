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

# Resolve-Path follows symlinks, giving us the real repo path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path

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

# --- mode dispatch -------------------------------------------------------------
# Step 0: scaffold only — mode dispatch will be filled in later steps.
# For now, just validate the repo resolution worked.
Write-Host "repo root: $RepoRoot"
Write-Host "mode: $mode"
Write-Host "(scaffold — full logic coming in later steps)"
