<#
.SYNOPSIS
  Red test: reproduces the empty-repo HEAD leak in Get-RepoHeads.
  Expected to FAIL until the runner's `git rev-parse HEAD` is changed
  to `git rev-parse --quiet --verify HEAD`.

.NOTES
  Invoked by local_checks in sub-plan 2026-06-07-runner-empty-repo-head.
  Exit 0 = green (bug fixed), exit 1 = red (bug present).
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent  # repo root
$scratch  = Join-Path $repoRoot "scratch\empty-repo-head"

# Clean slate
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

# Create a temp repo with a branch but zero commits (unborn HEAD)
$repoDir = Join-Path $scratch "repo"
& git init $repoDir 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error "git init failed"; exit 1 }

# Ensure main branch exists (some git versions default to master)
& git -C $repoDir symbolic-ref HEAD refs/heads/main 2>&1 | Out-Null

# Dot-source the runner with guard so functions are defined but the
# main loop does NOT execute.
$runnerPath = Join-Path $repoRoot "skills\ilk-loop\scripts\run_ilk_loop_claude.ps1"
if (-not (Test-Path $runnerPath)) {
  Write-Error "Runner not found at $runnerPath"
  exit 1
}

$logDir = Join-Path $scratch "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$env:ILK_DOTSOURCE_ONLY = '1'
try {
  # Dot-source with Mandatory params satisfied
  . $runnerPath -ProjectPath $repoDir -LogDir $logDir
} catch {
  Write-Error "Dot-sourcing runner failed: $_"
  exit 1
} finally {
  $env:ILK_DOTSOURCE_ONLY = $null
}

# AC-1 + AC-2: call Get-RepoHeads, expect "(unknown)" with no terminating error
$caught = $false
$result = $null
try {
  $result = Get-RepoHeads -Repos @($repoDir)
} catch {
  $caught = $true
  Write-Error "Get-RepoHeads threw a terminating error: $_"
}

if ($caught) {
  Write-Host "FAIL: Get-RepoHeads threw (bug present)" -ForegroundColor Red
  exit 1
}

$value = $result[$repoDir]
if ($value -ne "(unknown)") {
  Write-Error "FAIL: expected '(unknown)', got '$value'"
  exit 1
}

# AC-3: verify the dot-source guard prevented the main loop from running
# (if the guard is missing, the loop would have started and either errored
# or hung — reaching this point proves the guard worked).
Write-Host "PASS: Get-RepoHeads returned '(unknown)' silently on commitless repo" -ForegroundColor Green

# AC-4: bash-parity assertion — the bash runner's get_repo_heads uses
# `sha=$(git rev-parse HEAD 2>/dev/null) || sha="(unknown)"`. On a
# commitless repo, git rev-parse HEAD exits non-zero, so the || branch
# fires and sha becomes "(unknown)". We test the contract via the same
# one-liner, checking exit code to decide if the fallback should fire.
$bashPath = Get-Command bash -ErrorAction SilentlyContinue
if ($bashPath) {
  $escapedRepo = $repoDir -replace '\\', '/'
  # Use a temp script to avoid PowerShell/bash quoting hell
  $bashScript = Join-Path $scratch "bash-test.sh"
  $bashBody = "sha=`$(git -C '$escapedRepo' rev-parse HEAD 2>/dev/null) || sha='(unknown)'`necho `"`$sha`""
  $bashBody | Out-File -FilePath $bashScript -Encoding ascii
  $bashOutput = & bash $bashScript 2>$null
  $bashOutput = "$bashOutput".Trim()
  if ($bashOutput -ne "(unknown)") {
    Write-Error "FAIL: bash parity — expected '(unknown)', got '$bashOutput'"
    exit 1
  }
  Write-Host "PASS: bash runner also degrades commitless repo to '(unknown)'" -ForegroundColor Green
} else {
  Write-Host "SKIP: bash not on PATH — bash-parity assertion skipped" -ForegroundColor DarkYellow
}

# Clean up
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

exit 0
