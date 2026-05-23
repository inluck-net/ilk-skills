<#
.SYNOPSIS
  Launch run_ilk_loop_claude.ps1 in a detached desktop PowerShell window.

.DESCRIPTION
  Wraps run_ilk_loop_claude.ps1 with:
    - Project resolution (cwd walk-up / -ProjectName lookup / -ProjectPath)
    - Per-project parameter resolution (<project>/docs/plans/.ilk-launch.json)
    - Detached window via Start-Process powershell -NoExit
    - PID file written to <project>/.ilk-launcher/running.pid
    - Concurrent-run protection (refuses to start if a live PID exists)

  After Start-Process returns, control returns to the caller (Cursor agent
  or interactive shell) immediately. The spawned window is independent and
  survives Cursor closing.

.PARAMETER ProjectPath
  Absolute path to the project root (the directory containing docs/plans/).

.PARAMETER ProjectName
  Look up the path in ~/.cursor/skills/ilk-launcher/projects.json.

.PARAMETER MaxIterations
  Override the per-project / default value.

.PARAMETER IterationTimeoutMin
  Override the per-project / default value.

.PARAMETER All
  Iterate projects.json and launch every project (skips projects already
  running). Mutually exclusive with -ProjectPath / -ProjectName.

.PARAMETER Force
  Skip the "already running" check.

.PARAMETER DryRun
  Print the resolved plan but do not spawn anything.

.EXAMPLE
  # From inside a project workspace, cwd walk-up resolves it:
  .\launch.ps1

.EXAMPLE
  .\launch.ps1 -ProjectName es_api

.EXAMPLE
  .\launch.ps1 -ProjectPath C:\path\to\your\project -MaxIterations 60

.EXAMPLE
  .\launch.ps1 -All
#>
[CmdletBinding(DefaultParameterSetName = 'ByCwd')]
param(
  [Parameter(ParameterSetName = 'ByPath', Mandatory)]
  [string]$ProjectPath,

  [Parameter(ParameterSetName = 'ByName', Mandatory)]
  [string]$ProjectName,

  [Parameter(ParameterSetName = 'All', Mandatory)]
  [switch]$All,

  [int]$MaxIterations = 0,
  [int]$IterationTimeoutMin = 0,
  [switch]$Force,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# --- constants ---------------------------------------------------------------
$LauncherDir   = Join-Path $HOME '.cursor\skills\ilk-launcher'
$ProjectsJson  = Join-Path $LauncherDir 'projects.json'
$LoopScript    = Join-Path $HOME '.cursor\skills\ilk-loop\scripts\run_ilk_loop_claude.ps1'
$DefaultMaxIter = 30
$DefaultTimeout = 30

if (-not (Test-Path $LoopScript)) {
  throw "run_ilk_loop_claude.ps1 not found at $LoopScript. Is ilk-loop skill installed?"
}

# --- helpers -----------------------------------------------------------------

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

function Resolve-ProjectByCwd {
  $dir = (Get-Location).Path
  while ($dir) {
    if (Test-Path (Join-Path $dir 'docs\plans')) {
      $masters = Get-ChildItem (Join-Path $dir 'docs\plans') -Filter 'MASTER-*.md' -ErrorAction SilentlyContinue
      if ($masters) { return $dir }
    }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
  }
  throw "No project found by walking up from $((Get-Location).Path). No docs/plans/MASTER-*.md anywhere on the path. Use -ProjectName or -ProjectPath, or cd into a project."
}

function Get-ProjectName {
  param([string]$Path)
  $projects = Read-ProjectsRegistry
  $match = $projects | Where-Object { $_.path -eq $Path }
  if ($match) { return [string]$match.name }
  return (Split-Path $Path -Leaf)
}

function Read-ProjectConfig {
  param([string]$ProjectPath)
  $cfgPath = Join-Path $ProjectPath 'docs\plans\.ilk-launch.json'
  if (-not (Test-Path $cfgPath)) { return @{} }
  return Get-Content $cfgPath -Raw | ConvertFrom-Json -AsHashtable
}

function Resolve-Params {
  param(
    [string]$ProjectPath,
    [int]$CliMaxIter,
    [int]$CliTimeout
  )
  $cfg = Read-ProjectConfig -ProjectPath $ProjectPath
  $maxIter = if ($CliMaxIter -gt 0) { $CliMaxIter }
             elseif ($cfg.ContainsKey('max_iterations')) { [int]$cfg.max_iterations }
             else { $DefaultMaxIter }
  $timeout = if ($CliTimeout -gt 0) { $CliTimeout }
             elseif ($cfg.ContainsKey('iteration_timeout_min')) { [int]$cfg.iteration_timeout_min }
             else { $DefaultTimeout }
  return @{ MaxIterations = $maxIter; IterationTimeoutMin = $timeout }
}

function Get-PidFilePath {
  param([string]$ProjectPath)
  return Join-Path $ProjectPath '.ilk-launcher\running.pid'
}

function Get-LaunchMetaPath {
  param([string]$ProjectPath)
  return Join-Path $ProjectPath '.ilk-launcher\last-launch.json'
}

function Test-RunningPid {
  param([string]$ProjectPath)
  $pidFile = Get-PidFilePath -ProjectPath $ProjectPath
  if (-not (Test-Path $pidFile)) { return $null }
  $rawPid = (Get-Content $pidFile -Raw).Trim()
  if (-not $rawPid) { return $null }
  $existingPid = [int]$rawPid
  $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
  if ($proc) { return $existingPid }
  Remove-Item $pidFile -Force
  return $null
}

function Start-ilkWindow {
  param(
    [string]$ProjectPath,
    [string]$ProjectName,
    [int]$MaxIterations,
    [int]$IterationTimeoutMin,
    [bool]$Force,
    [bool]$DryRun
  )

  $livePid = Test-RunningPid -ProjectPath $ProjectPath
  if ($livePid -and -not $Force) {
    Write-Host "[$ProjectName] already running (PID $livePid). Use -Force to launch anyway, or stop.ps1 to kill it." -ForegroundColor Yellow
    return $null
  }

  $stateDir = Join-Path $ProjectPath '.ilk-launcher'
  if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }

  $title = "ilk: $ProjectName"

  $inner = @"
`$Host.UI.RawUI.WindowTitle = '$title'
Write-Host '=== ilk-launcher ===' -ForegroundColor Cyan
Write-Host "Project: $ProjectPath"
Write-Host "MaxIterations: $MaxIterations    IterationTimeoutMin: $IterationTimeoutMin"
Write-Host "Started: `$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host '======================' -ForegroundColor Cyan
& '$LoopScript' -ProjectPath '$ProjectPath' -MaxIterations $MaxIterations -IterationTimeoutMin $IterationTimeoutMin
`$code = `$LASTEXITCODE
Write-Host ''
Write-Host '[ilk-launcher] run_ilk_loop_claude.ps1 exited with code:' `$code -ForegroundColor Yellow
Write-Host '[ilk-launcher] window left open for review. Close manually when done.' -ForegroundColor Yellow
"@

  if ($DryRun) {
    Write-Host "[$ProjectName] DRY RUN — would launch:" -ForegroundColor Cyan
    Write-Host "  Title: $title"
    Write-Host "  ProjectPath: $ProjectPath"
    Write-Host "  MaxIterations: $MaxIterations"
    Write-Host "  IterationTimeoutMin: $IterationTimeoutMin"
    return $null
  }

  $proc = Start-Process powershell -ArgumentList @('-NoExit', '-NoProfile', '-Command', $inner) -PassThru

  $pidFile = Get-PidFilePath -ProjectPath $ProjectPath
  $proc.Id | Out-File -FilePath $pidFile -Encoding ascii -NoNewline

  $meta = @{
    project_path           = $ProjectPath
    project_name           = $ProjectName
    pid                    = $proc.Id
    started_at             = (Get-Date -Format 's')
    max_iterations         = $MaxIterations
    iteration_timeout_min  = $IterationTimeoutMin
    loop_script            = $LoopScript
  }
  $meta | ConvertTo-Json | Out-File -FilePath (Get-LaunchMetaPath -ProjectPath $ProjectPath) -Encoding utf8

  Write-Host "[$ProjectName] launched. PID $($proc.Id). Title: '$title'." -ForegroundColor Green
  Write-Host "[$ProjectName] PID file: $pidFile"
  Write-Host "[$ProjectName] loop JSONL log: $HOME\.cursor\skills\ilk-loop\logs (see run_ilk_loop_claude.ps1 -LogDir)"
  return $proc.Id
}

# --- main --------------------------------------------------------------------

if ($All) {
  $projects = Read-ProjectsRegistry
  if (-not $projects -or $projects.Count -eq 0) {
    throw "projects.json has no projects. Add some before using -All."
  }
  foreach ($p in $projects) {
    $params = Resolve-Params -ProjectPath $p.path -CliMaxIter $MaxIterations -CliTimeout $IterationTimeoutMin
    Start-ilkWindow `
      -ProjectPath $p.path `
      -ProjectName $p.name `
      -MaxIterations $params.MaxIterations `
      -IterationTimeoutMin $params.IterationTimeoutMin `
      -Force:$Force.IsPresent `
      -DryRun:$DryRun.IsPresent | Out-Null
  }
  return
}

# single-project paths
$resolvedPath = switch ($PSCmdlet.ParameterSetName) {
  'ByPath' { $ProjectPath }
  'ByName' { Resolve-ProjectByName -Name $ProjectName }
  'ByCwd'  { Resolve-ProjectByCwd }
}

if (-not (Test-Path $resolvedPath)) {
  throw "ProjectPath '$resolvedPath' does not exist."
}
$resolvedPath = (Resolve-Path $resolvedPath).Path
$resolvedName = if ($ProjectName) { $ProjectName } else { Get-ProjectName -Path $resolvedPath }
$params = Resolve-Params -ProjectPath $resolvedPath -CliMaxIter $MaxIterations -CliTimeout $IterationTimeoutMin

Start-ilkWindow `
  -ProjectPath $resolvedPath `
  -ProjectName $resolvedName `
  -MaxIterations $params.MaxIterations `
  -IterationTimeoutMin $params.IterationTimeoutMin `
  -Force:$Force.IsPresent `
  -DryRun:$DryRun.IsPresent | Out-Null
