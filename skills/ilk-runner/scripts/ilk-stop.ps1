<#
.SYNOPSIS
  Stop ilk loop and watchdog for the current project (/ilk-stop on Windows).
#>
[CmdletBinding()]
param(
  [string]$Start = "",
  [switch]$ResetWorkerChanges
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_resolve_python.ps1")

$SkillRoot = Get-IlkSkillRoot
$PathsPy = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
$StopPs1 = Join-Path $SkillRoot "ilk-launcher\scripts\stop.ps1"

if (-not $Start) { $Start = (Get-Location).Path }

$pathsResult = Invoke-IlkPythonCapture -ArgumentList @($PathsPy, "--start", $Start)
if ($pathsResult.ExitCode -ne 0 -or -not $pathsResult.Output) {
  throw "ilk_paths.py failed: $($pathsResult.Output)"
}
$paths = $pathsResult.Output | ConvertFrom-Json
if (-not $paths.project_root) {
  Write-Host "No project_root resolved from '$Start'."
  exit 2
}

$ProjectRoot = [string]$paths.project_root
$ProjectKey = [string]$paths.project_key

$stopArgs = @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $StopPs1,
  '-ProjectPath', $ProjectRoot
)
if ($ResetWorkerChanges) {
  $stopArgs += '-ResetWorkerChanges'
}

& powershell @stopArgs
Write-Host ""
Write-Host "ilk stopped: $ProjectKey"
exit $LASTEXITCODE
