<#
.SYNOPSIS
  Supervised ilk launch: resolve project, queue check, promote, launch, watchdog.

.DESCRIPTION
  Implements the /ilk-run workflow for Windows. Resolves skill root and Python
  automatically so agents do not need to call python3 or mix Bash/PowerShell.

.PARAMETER Start
  Directory to resolve the project from. Defaults to the current location.

.PARAMETER MaxIterations
  Override iteration cap (0 = let launch.ps1 pick defaults).

.PARAMETER IterationTimeoutMin
  Override per-iteration timeout in minutes (0 = let launch.ps1 pick defaults).

.PARAMETER DryRun
  Print the plan without launching.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File ilk-run.ps1
#>
[CmdletBinding()]
param(
  [string]$Start = "",
  [int]$MaxIterations = 0,
  [int]$IterationTimeoutMin = 0,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_resolve_python.ps1")

$SkillRoot = Get-IlkSkillRoot
$PathsPy = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
$LoopStatusPy = Join-Path $SkillRoot "ilk-loop\scripts\loop_status.py"
$PromotePy = Join-Path $SkillRoot "ilk-loop\scripts\promote_next_master.py"
$LaunchPs1 = Join-Path $SkillRoot "ilk-launcher\scripts\launch.ps1"
$WatchdogPs1 = Join-Path $SkillRoot "ilk-watchdog\scripts\watchdog.ps1"

if (-not $Start) { $Start = (Get-Location).Path }

function Invoke-LoopStatus {
  param([string]$ProjectRoot)
  $result = Invoke-IlkPythonCapture -WorkingDirectory $ProjectRoot -ArgumentList @($LoopStatusPy)
  return $result
}

function Invoke-PromoteDryRun {
  param([string]$ProjectRoot)
  $result = Invoke-IlkPythonCapture -ArgumentList @($PromotePy, "--project", $ProjectRoot, "--dry-run")
  if ($result.ExitCode -ne 0) {
    throw "promote_next_master.py failed: $($result.Output)"
  }
  return ($result.Output | ConvertFrom-Json)
}

function Invoke-Promote {
  param([string]$ProjectRoot)
  $result = Invoke-IlkPythonCapture -ArgumentList @($PromotePy, "--project", $ProjectRoot)
  if ($result.ExitCode -ne 0) {
    throw "promote_next_master.py failed: $($result.Output)"
  }
  return ($result.Output | ConvertFrom-Json)
}

function Get-DefaultLaunchParams {
  param(
    [string]$StatusOutput
  )
  $maxIter = 30
  $timeout = 30
  $totalRemaining = 0
  $pendingPlans = 0

  foreach ($line in ($StatusOutput -split "`n")) {
    if ($line -match '\[  \] pending|\[\.\.\] in-progress|\[>>\] ready') {
      $pendingPlans++
    }
    if ($line -match '\s(\d+)/(\d+)\s*$') {
      $cur = [int]$Matches[1]
      $est = [int]$Matches[2]
      $totalRemaining += [Math]::Max($est - $cur, 0)
    }
  }

  if ($totalRemaining -gt 0) {
    if ($pendingPlans -gt 1) {
      $maxIter = [Math]::Min([Math]::Max([int][Math]::Ceiling($totalRemaining * 1.5), 20), 60)
    } else {
      $maxIter = [Math]::Min([Math]::Max($totalRemaining * 2, 10), 60)
    }
  } elseif ($StatusOutput -match 'step=(\d+)/(\d+)') {
    $cur = [int]$Matches[1]
    $est = [int]$Matches[2]
    $remaining = [Math]::Max($est - $cur, 1)
    $maxIter = [Math]::Min([Math]::Max($remaining * 2, 10), 60)
  }

  # Step character heuristics from Next/path line or sub-plan names in table
  if ($StatusOutput -match 'playwright|e2e|chrome-devtools') { $timeout = 45 }
  elseif ($StatusOutput -match 'wait_ci|push and wait') { $timeout = 60 }

  return @{
    MaxIterations       = $maxIter
    IterationTimeoutMin = $timeout
  }
}

function Read-PostmortemFrontmatter {
  param([string]$FilePath)
  $text = Get-Content $FilePath -Raw -ErrorAction SilentlyContinue
  if (-not $text -or -not $text.StartsWith('---')) { return @{} }
  $end = $text.IndexOf("`n---", 3)
  if ($end -lt 0) { return @{} }
  $fm = @{}
  foreach ($raw in $text.Substring(3, $end - 3).Split("`n")) {
    $line = $raw.Trim()
    if ($line -and $line.Contains(':')) {
      $k, $v = $line.Split(':', 2)
      $fm[$k.Trim()] = $v.Trim().Trim('"')
    }
  }
  return $fm
}

function Get-LaunchParamsFromHistory {
  param(
    [string]$LauncherDir,
    [string]$StatusOutput,
    [int]$OverrideMax,
    [int]$OverrideTimeout
  )
  $params = Get-DefaultLaunchParams -StatusOutput $StatusOutput
  $maxIter = $params.MaxIterations
  $timeout = $params.IterationTimeoutMin

  $postmortemDir = Join-Path $LauncherDir 'postmortems'
  if (-not (Test-Path $postmortemDir)) {
    return @{ MaxIterations = $maxIter; IterationTimeoutMin = $timeout; Warnings = @() }
  }

  $recent = Get-ChildItem $postmortemDir -Filter '*.md' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 3

  $classifications = @()
  $recMax = 0
  $recTimeout = 0
  foreach ($file in $recent) {
    $fm = Read-PostmortemFrontmatter -FilePath $file.FullName
    if ($fm['classification']) { $classifications += $fm['classification'] }
    if ($fm['recommended_max_iterations'] -match '^\d+$') {
      $v = [int]$fm['recommended_max_iterations']
      if ($v -gt $recMax) { $recMax = $v }
    }
    if ($fm['recommended_iteration_timeout_min'] -match '^\d+$') {
      $v = [int]$fm['recommended_iteration_timeout_min']
      if ($v -gt $recTimeout) { $recTimeout = $v }
    }
  }

  $warnings = @()
  $timeoutBound = @($classifications | Where-Object { $_ -eq 'timeout-bound' }).Count
  $maxIterBound = @($classifications | Where-Object { $_ -eq 'max-iter-bound' }).Count
  $apiFlaky = @($classifications | Where-Object { $_ -in @('api-flaky', 'api-blocked') }).Count
  $stuck = @($classifications | Where-Object { $_ -eq 'stuck-no-progress' }).Count

  if ($timeoutBound -ge 2 -and $recTimeout -gt $timeout) {
    $timeout = [Math]::Min($recTimeout, 120)
    Write-Host "Postmortem adjust: IterationTimeoutMin -> $timeout ($timeoutBound recent timeout-bound)"
  }
  if ($maxIterBound -ge 2 -and $recMax -gt $maxIter) {
    $maxIter = [Math]::Min($recMax, 60)
    Write-Host "Postmortem adjust: MaxIterations -> $maxIter ($maxIterBound recent max-iter-bound)"
  }
  if ($apiFlaky -ge 2) {
    $warnings += "WARNING: $($apiFlaky) of last 3 runs were api-flaky/api-blocked — endpoint may be unstable."
  }
  if ($stuck -ge 2) {
    $warnings += "WARNING: $($stuck) of last 3 runs were stuck-no-progress — sub-plan may need restructuring."
  }

  if ($OverrideMax -gt 0) { $maxIter = $OverrideMax }
  if ($OverrideTimeout -gt 0) { $timeout = $OverrideTimeout }

  return @{
    MaxIterations       = $maxIter
    IterationTimeoutMin = $timeout
    Warnings            = $warnings
  }
}

function Test-SelfHostingProject {
  param([string]$ProjectRoot)
  $markers = @(
    (Join-Path $ProjectRoot 'skills\ilk-loop'),
    (Join-Path $ProjectRoot 'skills\ilk-launcher')
  )
  return ($markers | Where-Object { Test-Path $_ }).Count -ge 2
}

# --- 1. Resolve project ------------------------------------------------------
$pathsResult = Invoke-IlkPythonCapture -ArgumentList @($PathsPy, "--start", $Start)
if ($pathsResult.ExitCode -ne 0 -or -not $pathsResult.Output) {
  throw "ilk_paths.py failed: $($pathsResult.Output)"
}
$paths = $pathsResult.Output | ConvertFrom-Json
if (-not $paths.project_root) {
  Write-Host "No project_root resolved from '$Start'. cd into a project root (git repo or .ilk-meta.json) and retry."
  exit 2
}

$ProjectRoot = [string]$paths.project_root
$ProjectKey = [string]$paths.project_key
$LauncherDir = [string]$paths.external_launcher_dir
$WatchdogDir = [string]$paths.external_watchdog_dir

Write-Host "Project: $ProjectKey"
Write-Host "Root:    $ProjectRoot"

# --- 2. Queue check (+ promote when active master is fully shipped) ----------
$status = Invoke-LoopStatus -ProjectRoot $ProjectRoot
Write-Host ""
Write-Host $status.Output

if ($status.ExitCode -eq 2) {
  Write-Host "No plans directory resolved. cd into a project with external plans or run /ilk-plan."
  exit 2
}

if ($status.ExitCode -eq 0) {
  $plan = Invoke-PromoteDryRun -ProjectRoot $ProjectRoot
  if ($plan.active_count_before -gt 1) {
    Write-Host "Queue integrity issue: $($plan.active_count_before) masters are active. Fix manually before launching."
    exit 2
  }
  if ($plan.promoted) {
    Write-Host ""
    Write-Host "Active master fully shipped; promoting $($plan.promoted) ..."
    if (-not $DryRun) {
      $promoted = Invoke-Promote -ProjectRoot $ProjectRoot
      Write-Host ($promoted | ConvertTo-Json -Compress)
      $status = Invoke-LoopStatus -ProjectRoot $ProjectRoot
      Write-Host ""
      Write-Host $status.Output
    } else {
      Write-Host "(dry-run: would promote $($plan.promoted))"
      exit 0
    }
  }
  if ($status.ExitCode -eq 0) {
    Write-Host ""
    Write-Host "All sub-plans shipped — nothing to run."
    exit 0
  }
  if ($status.ExitCode -eq 2) {
    exit 2
  }
}

if ($status.ExitCode -ne 1) {
  throw "Unexpected loop_status exit code: $($status.ExitCode)"
}

# --- 3. Launch params --------------------------------------------------------
if (Test-SelfHostingProject -ProjectRoot $ProjectRoot) {
  Write-Host ""
  Write-Host "WARNING: Self-hosting detected — this project supplies the installed ilk skills."
  Write-Host "         A run may modify runner code mid-flight. See /ilk-run section S."
}

$paramPlan = Get-LaunchParamsFromHistory -LauncherDir $LauncherDir -StatusOutput $status.Output `
  -OverrideMax $MaxIterations -OverrideTimeout $IterationTimeoutMin
$MaxIterations = $paramPlan.MaxIterations
$IterationTimeoutMin = $paramPlan.IterationTimeoutMin
foreach ($warn in $paramPlan.Warnings) { Write-Host $warn }

if ($status.Output -match 'Next: (\S+)') {
  Write-Host "Next sub-plan: $($Matches[1])"
}
if ($status.Output -match 'Path: (.+)') {
  Write-Host "Sub-plan path: $($Matches[1].Trim())"
}

Write-Host ""
Write-Host "Launch params: MaxIterations=$MaxIterations IterationTimeoutMin=$IterationTimeoutMin"

if ($DryRun) {
  Write-Host "(dry-run: would launch ilk + watchdog)"
  exit 0
}

# --- 4. Launch ilk -----------------------------------------------------------
$launchArgs = @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $LaunchPs1,
  '-ProjectPath', $ProjectRoot,
  '-MaxIterations', $MaxIterations,
  '-IterationTimeoutMin', $IterationTimeoutMin
)
& powershell @launchArgs
if ($LASTEXITCODE -ne 0) {
  throw "launch.ps1 failed with exit code $LASTEXITCODE"
}

# --- 5. Start watchdog -------------------------------------------------------
$watchArgs = @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $WatchdogPs1,
  '-ProjectPath', $ProjectRoot,
  '-PollMin', '5',
  '-MaxRestarts', '5',
  '-Detach'
)
& powershell @watchArgs
if ($LASTEXITCODE -ne 0) {
  throw "watchdog.ps1 failed with exit code $LASTEXITCODE"
}

# --- 6. Summary --------------------------------------------------------------
$lastLaunch = Join-Path $LauncherDir "last-launch.json"
$summary = @{
  project_key = $ProjectKey
  max_iterations = $MaxIterations
  timeout_min = $IterationTimeoutMin
}
if (Test-Path $lastLaunch) {
  $launchInfo = Get-Content $lastLaunch -Raw | ConvertFrom-Json
  $summary.pid = $launchInfo.pid
  $summary.log_file = $launchInfo.log_file
  $summary.jsonl_log = $launchInfo.jsonl_log
}

Write-Host ""
Write-Host "ilk launched: $($summary.project_key)"
if ($summary.pid) { Write-Host "  PID:        $($summary.pid)" }
Write-Host "  Iterations: $($summary.max_iterations)"
Write-Host "  Timeout:    $($summary.timeout_min) min"
Write-Host "  Watchdog:   started (poll 5 min, max 5 restarts)"
if ($summary.log_file) { Write-Host "  Loop log:   $($summary.log_file)" }
Write-Host "  Watchdog:   $(Join-Path $WatchdogDir 'activity.log')"
Write-Host ""
Write-Host "Tail loop log:    Get-Content `"$($summary.log_file)`" -Wait"
Write-Host "Tail watchdog:    Get-Content `"$(Join-Path $WatchdogDir 'activity.log')`" -Wait"
