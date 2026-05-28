<#
.SYNOPSIS
  Stop a ilk-launcher-spawned window for a project (tree-kill).

.DESCRIPTION
  Reads the PID file from the external launcher dir (resolved via
  ilk_paths.py), runs taskkill /T /F /PID <n> so the wrapper PowerShell,
  claude CLI, and any descendants all die together (no orphaned API
  consumers). Removes the PID file on success.

.PARAMETER ProjectPath
  Absolute project root path.

.PARAMETER ProjectName
  Look up the path in ~/.cursor/skills/ilk-launcher/projects.json.

.PARAMETER All
  Stop every project that has a live PID file.

.EXAMPLE
  .\stop.ps1 -ProjectName es_api

.EXAMPLE
  .\stop.ps1 -ProjectPath C:\path\to\your\project

.EXAMPLE
  .\stop.ps1 -All
#>
[CmdletBinding(DefaultParameterSetName = 'ByName')]
param(
  [Parameter(ParameterSetName = 'ByPath', Mandatory)]
  [string]$ProjectPath,

  [Parameter(ParameterSetName = 'ByName', Mandatory)]
  [string]$ProjectName,

  [Parameter(ParameterSetName = 'All', Mandatory)]
  [switch]$All
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

$LauncherDir  = Join-Path $SkillRoot 'ilk-launcher'
$ProjectsJson = Join-Path $LauncherDir 'projects.json'

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
  if (-not $match) { throw "Project '$Name' not in projects.json." }
  return [string]$match.path
}

function Get-ExternalLauncherDir {
  param([string]$ProjectPath)
  $resolver = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
  if (-not (Test-Path $resolver)) { return "" }
  try {
    $json = & python $resolver --start $ProjectPath 2>$null
    if ($LASTEXITCODE -eq 0 -and $json) {
      $obj = $json | ConvertFrom-Json -ErrorAction Stop
      if ($obj.external_launcher_dir) { return [string]$obj.external_launcher_dir }
    }
  } catch {}
  return ""
}

function Get-PidFilePath {
  param([string]$ProjectPath)
  $dir = Get-ExternalLauncherDir -ProjectPath $ProjectPath
  if (-not $dir) { return "" }
  return Join-Path $dir 'running.pid'
}

function Mark-SentinelInterrupted {
  param([string]$ProjectPath, [int]$StoppedPid)
  $launcherDir = Get-ExternalLauncherDir -ProjectPath $ProjectPath
  if (-not $launcherDir) { return }
  $runtimeDir = Split-Path $launcherDir -Parent
  $marker = Join-Path $SkillRoot "ilk-launcher\scripts\mark_sentinel_interrupted.ps1"
  if (Test-Path $marker) {
    try { & $marker -RuntimeDir $runtimeDir -StoppedPid $StoppedPid } catch {}
  }
}

function Kill-Orphans {
  param([string]$ProjectPath, [int]$StoppedPid, [string]$LauncherDir, [string]$Name)

  $lastLaunch = Join-Path $LauncherDir 'last-launch.json'
  if (-not (Test-Path $lastLaunch)) {
    Write-Host "[$Name] orphan scan: no last-launch.json — skipping." -ForegroundColor Yellow
    return
  }
  $launch = Get-Content $lastLaunch -Raw | ConvertFrom-Json
  $logFile = $launch.log_file
  if (-not $logFile) {
    Write-Host "[$Name] orphan scan: no log_file in last-launch.json — skipping." -ForegroundColor Yellow
    return
  }
  # Extract run ID (YYYYMMDD-HHMMSS) from the log file path
  $runMatch = [regex]::Match($logFile, '(\d{8}-\d{6})')
  if (-not $runMatch.Success) {
    Write-Host "[$Name] orphan scan: no run ID found in log path — skipping." -ForegroundColor Yellow
    return
  }
  $runId = $runMatch.Groups[1].Value

  $myPid = $PID
  $candidates = Get-CimInstance Win32_Process |
    Where-Object {
      $_.ProcessId -ne $StoppedPid -and
      $_.ProcessId -ne $myPid -and
      ($_.CommandLine -match [regex]::Escape($runId) -or $_.CommandLine -match [regex]::Escape($ProjectPath))
    }

  $found = 0
  foreach ($proc in $candidates) {
    $cmdLine = if ($proc.CommandLine) { $proc.CommandLine.Substring(0, [Math]::Min(120, $proc.CommandLine.Length)) } else { "(unknown)" }
    Write-Host "[$Name] orphan scan: killing PID $($proc.ProcessId) — $cmdLine" -ForegroundColor Yellow
    try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    $found++
  }
  if ($found -eq 0) {
    Write-Host "[$Name] orphan scan: no orphaned workers found." -ForegroundColor Gray
  } else {
    Write-Host "[$Name] orphan scan: terminated $found worker process(es)." -ForegroundColor Green
  }
}

function Stop-WatchdogForProject {
  param([string]$Path, [string]$Name)
  $watchdogStop = Join-Path $SkillRoot "ilk-watchdog\scripts\stop_watchdog.ps1"
  if (-not (Test-Path $watchdogStop)) { return }
  try {
    & $watchdogStop -ProjectPath $Path 2>&1 | ForEach-Object { Write-Host "[$Name] $_" }
  } catch {}
}

function Stop-Project {
  param([string]$Path, [string]$Name)

  # Stop watchdog first — it must not try to restart the loop after we kill it.
  Stop-WatchdogForProject -Path $Path -Name $Name

  $pidFile = Get-PidFilePath -ProjectPath $Path
  if (-not $pidFile) {
    Write-Host "[$Name] could not resolve external launcher dir — nothing to stop." -ForegroundColor Yellow
    return
  }
  if (-not (Test-Path $pidFile)) {
    Write-Host "[$Name] no PID file at $pidFile — nothing to stop." -ForegroundColor Yellow
    return
  }
  $targetPid = [int]((Get-Content $pidFile -Raw).Trim())
  $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
  if (-not $proc) {
    Write-Host "[$Name] PID $targetPid no longer alive. Cleaning stale PID file." -ForegroundColor Yellow
    Mark-SentinelInterrupted -ProjectPath $Path -StoppedPid $targetPid
    Remove-Item $pidFile -Force
    return
  }
  Write-Host "[$Name] tree-killing PID $targetPid (and descendants)..." -ForegroundColor Cyan
  & taskkill /T /F /PID $targetPid 2>&1 | Out-Host
  Start-Sleep -Milliseconds 500
  if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {
    Write-Warning "[$Name] PID $targetPid still alive after taskkill. Investigate manually."
  } else {
    Write-Host "[$Name] stopped." -ForegroundColor Green
    Mark-SentinelInterrupted -ProjectPath $Path -StoppedPid $targetPid
    Remove-Item $pidFile -Force
  }

  # Scan for orphaned worker processes (claude, gtimeout, tee, renderer)
  $launcherDir = Get-ExternalLauncherDir -ProjectPath $Path
  if ($launcherDir) {
    Kill-Orphans -ProjectPath $Path -StoppedPid $targetPid -LauncherDir $launcherDir -Name $Name
  }
}

if ($All) {
  $projects = Read-ProjectsRegistry
  if (-not $projects -or $projects.Count -eq 0) {
    throw "projects.json has no projects."
  }
  foreach ($p in $projects) { Stop-Project -Path $p.path -Name $p.name }
  return
}

$resolvedPath = if ($ProjectPath) { $ProjectPath } else { Resolve-ProjectByName -Name $ProjectName }
$resolvedName = if ($ProjectName) { $ProjectName } else { (Split-Path $resolvedPath -Leaf) }
Stop-Project -Path $resolvedPath -Name $resolvedName
