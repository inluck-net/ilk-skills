<#
.SYNOPSIS
  Read-only ilk status for the current project (/ilk-status on Windows).
#>
[CmdletBinding()]
param(
  [string]$Start = ""
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_resolve_python.ps1")

$SkillRoot = Get-IlkSkillRoot
$PathsPy = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
$LoopStatusPy = Join-Path $SkillRoot "ilk-loop\scripts\loop_status.py"
$StatusProgressPy = Join-Path $SkillRoot "ilk-launcher\scripts\status_progress.py"

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

$status = Invoke-IlkPythonCapture -WorkingDirectory $ProjectRoot -ArgumentList @($LoopStatusPy)
Write-Host $status.Output

if ($status.ExitCode -eq 0) {
  Write-Host ""
  Write-Host "All sub-plans shipped."
  exit 0
}
if ($status.ExitCode -eq 2) {
  exit 2
}

Write-Host ""
Write-Host "--- progress ---"
Invoke-IlkPython -WorkingDirectory $ProjectRoot -ArgumentList @($StatusProgressPy, "--project-path", $ProjectRoot) | Out-Null
exit $LASTEXITCODE
