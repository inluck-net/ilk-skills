<#
.SYNOPSIS
  Stop the watchdog window for a project. Does NOT touch ilk itself.

.DESCRIPTION
  Reads ~/.ilk-data/projects/<key>/runtime/watchdog/watchdog.pid and tree-kills it.
  ilk (the loop) is unaffected.

.EXAMPLE
  .\stop_watchdog.ps1 -ProjectName myproj
#>
[CmdletBinding(DefaultParameterSetName = 'ByName')]
param(
  [Parameter(ParameterSetName = 'ByPath', Mandatory)]
  [string]$ProjectPath,

  [Parameter(ParameterSetName = 'ByName', Mandatory)]
  [string]$ProjectName
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

$ProjectsJson = Join-Path $SkillRoot 'ilk-launcher\projects.json'

function Resolve-ByName {
  param([string]$Name)
  if (-not (Test-Path $ProjectsJson)) { throw "projects.json not found." }
  $raw = Get-Content $ProjectsJson -Raw | ConvertFrom-Json
  $match = $raw.projects | Where-Object { $_.name -eq $Name }
  if (-not $match) { throw "Project '$Name' not in projects.json." }
  return [string]$match.path
}

$resolvedPath = if ($ProjectPath) { $ProjectPath } else { Resolve-ByName -Name $ProjectName }
$resolvedName = if ($ProjectName) { $ProjectName } else { Split-Path $resolvedPath -Leaf }

function Get-IlkWatchdogDir {
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

$watchdogDir = Get-IlkWatchdogDir -Project $resolvedPath
if (-not $watchdogDir) {
  Write-Host "[$resolvedName] external watchdog dir not resolvable. Ensure ilk_paths.py is present or run the migration script." -ForegroundColor Red
  exit 1
}
$pidFile = Join-Path $watchdogDir 'watchdog.pid'
if (-not (Test-Path $pidFile)) {
  Write-Host "[$resolvedName] no watchdog.pid file — nothing to stop." -ForegroundColor Yellow
  return
}

$wPid = [int]((Get-Content $pidFile -Raw).Trim())
$proc = Get-Process -Id $wPid -ErrorAction SilentlyContinue
if (-not $proc) {
  Write-Host "[$resolvedName] watchdog PID $wPid no longer alive. Cleaning stale PID file." -ForegroundColor Yellow
  Remove-Item $pidFile -Force
  return
}

Write-Host "[$resolvedName] tree-killing watchdog PID $wPid..." -ForegroundColor Cyan
& taskkill /T /F /PID $wPid 2>&1 | Out-Host
Start-Sleep -Milliseconds 500
if (Get-Process -Id $wPid -ErrorAction SilentlyContinue) {
  Write-Warning "[$resolvedName] PID $wPid still alive after taskkill."
} else {
  Write-Host "[$resolvedName] watchdog stopped. ilk (if running) is unaffected." -ForegroundColor Green
  Remove-Item $pidFile -Force
}
