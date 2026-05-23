<#
.SYNOPSIS
  v0 pipeline smoketest — runs gate 2/3/4 scripts on a shipped sub-plan.

.DESCRIPTION
  Not part of the production ilk-loop. Validates wait_ci.py, run_reviewer.py,
  and generate_ship_report.py end-to-end on historical data.

.EXAMPLE
  .\_pipeline_smoketest.ps1
  .\_pipeline_smoketest.ps1 -SkipWaitCi
#>
[CmdletBinding()]
param(
  [string]$ProjectPath = "",
  [string]$SubPlanPath = "",
  [string]$BaseRef = "",
  [string]$HeadRef = "HEAD",
  [switch]$SkipWaitCi,
  [string]$CiState = "success",
  [string]$CiUrl = "",
  [string]$ScriptsDir = "$env:USERPROFILE\.cursor\skills\ilk-loop\scripts"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-SubPlan {
  param([string]$Project, [string]$Explicit)
  if ($Explicit) { return (Resolve-Path $Explicit).Path }
  $plansDir = Join-Path $Project "docs\plans"
  $shipped = Get-ChildItem $plansDir -Filter "*.md" |
    Where-Object { $_.Name -notlike "MASTER*" } |
    Sort-Object LastWriteTime -Descending
  foreach ($f in $shipped) {
    $head = Get-Content $f.FullName -TotalCount 12 -ErrorAction SilentlyContinue
    if ($head -match "status:\s*shipped") { return $f.FullName }
  }
  throw "No shipped sub-plan found under $plansDir"
}

function Resolve-BaseRef {
  param([string]$Project, [string]$SubPlanFile, [string]$Explicit)
  if ($Explicit) { return $Explicit }
  $slug = (Select-String -Path $SubPlanFile -Pattern "^plan:\s*(.+)$" | Select-Object -First 1).Matches.Groups[1].Value.Trim()
  if (-not $slug) {
    $slug = [System.IO.Path]::GetFileNameWithoutExtension($SubPlanFile)
  }
  Push-Location $Project
  try {
    $first = git log --grep="plan:$slug" --reverse --format=%H | Select-Object -First 1
    if ($first) {
      $parent = git rev-parse "$first^"
      if ($LASTEXITCODE -eq 0) { return $parent }
    }
    return (git rev-parse "HEAD~11")
  } finally {
    Pop-Location
  }
}

if (-not $ProjectPath) {
  throw "ProjectPath is required. Pass -ProjectPath C:\path\to\your\project."
}
$ProjectPath = (Resolve-Path $ProjectPath).Path
$SubPlanPath = Resolve-SubPlan -Project $ProjectPath -Explicit $SubPlanPath
$BaseRef = Resolve-BaseRef -Project $ProjectPath -SubPlanFile $SubPlanPath -Explicit $BaseRef

$slug = (Select-String -Path $SubPlanPath -Pattern "^plan:\s*(.+)$" | Select-Object -First 1).Matches.Groups[1].Value.Trim()
if (-not $slug) { $slug = [System.IO.Path]::GetFileNameWithoutExtension($SubPlanPath) }

$ts = Get-Date -Format "yyyy-MM-dd-HHmm"
$reviewerDir = Join-Path $ProjectPath "docs\plans\reviewer-reports"
$shipDir = Join-Path $ProjectPath "docs\plans\ship-reports"
New-Item -ItemType Directory -Force -Path $reviewerDir, $shipDir | Out-Null

$reviewerOut = Join-Path $reviewerDir "$slug-$ts.md"
$shipOut = Join-Path $shipDir "$slug-$ts.md"

Push-Location $ProjectPath
try {
  $headSha = (git rev-parse $HeadRef).Trim()
} finally {
  Pop-Location
}

Write-Host "=== Pipeline smoketest ===" -ForegroundColor Cyan
Write-Host "Project:   $ProjectPath"
Write-Host "Sub-plan:  $SubPlanPath"
Write-Host "Base/Head: $BaseRef .. $HeadRef ($headSha)"
Write-Host ""

$waitJson = @{ state = $CiState; ci_run_url = $CiUrl; summary = "smoketest bypass"; elapsed_seconds = 0 }

if (-not $SkipWaitCi) {
  Write-Host "[gate 2] wait_ci.py ..." -ForegroundColor Yellow
  $wcArgs = @(
    "$ScriptsDir\wait_ci.py",
    "--project", $ProjectPath,
    "--commit", $headSha,
    "--timeout", "1"
  )
  $wcOut = & python @wcArgs 2>&1 | ForEach-Object { "$_" }
  $wcExit = $LASTEXITCODE
  Write-Host $wcOut
  if ($wcExit -eq 0) {
    try { $waitJson = $wcOut | Select-Object -Last 1 | ConvertFrom-Json } catch {}
    $CiState = $waitJson.state
    if ($waitJson.ci_run_url) { $CiUrl = $waitJson.ci_run_url }
  } elseif ($wcExit -eq 3) {
    Write-Host "[gate 2] skipped (no token / non-Gitee) - using -CiState $CiState" -ForegroundColor DarkYellow
  } else {
    Write-Warning "[gate 2] exit $wcExit - continuing smoketest with CiState=$CiState"
  }
} else {
  Write-Host "[gate 2] skipped (-SkipWaitCi)" -ForegroundColor DarkYellow
}

Write-Host "[gate 3] run_reviewer.py ..." -ForegroundColor Yellow
$reviewerArgs = @(
  "$ScriptsDir\run_reviewer.py",
  "--project", $ProjectPath,
  "--sub-plan", $SubPlanPath,
  "--base", $BaseRef,
  "--head", $HeadRef,
  "--output", $reviewerOut,
  "--ci-state", $CiState,
  "--allow-same-vendor"
)
if ($CiUrl) { $reviewerArgs += @("--ci-url", $CiUrl) }
$reviewerLog = & python @reviewerArgs 2>&1 | ForEach-Object { "$_" }
Write-Host ($reviewerLog -join "`n")
if ($LASTEXITCODE -ne 0) { throw "run_reviewer.py failed with exit $LASTEXITCODE" }
Write-Host "  -> $reviewerOut" -ForegroundColor Green

Write-Host "[gate 4] generate_ship_report.py ..." -ForegroundColor Yellow
$shipArgs = @(
  "$ScriptsDir\generate_ship_report.py",
  "--project", $ProjectPath,
  "--sub-plan", $SubPlanPath,
  "--base", $BaseRef,
  "--head", $HeadRef,
  "--reviewer-report", $reviewerOut,
  "--ci-state", $CiState,
  "--output", $shipOut
)
if ($CiUrl) { $shipArgs += @("--ci-url", $CiUrl) }
$shipLog = & python @shipArgs 2>&1 | ForEach-Object { "$_" }
Write-Host ($shipLog -join "`n")
if ($LASTEXITCODE -ne 0) { throw "generate_ship_report.py failed with exit $LASTEXITCODE" }
Write-Host "  -> $shipOut" -ForegroundColor Green

Write-Host ""
Write-Host "=== Smoketest complete ===" -ForegroundColor Cyan
Write-Host "Review ship-report layout (spec 4.2): $shipOut"
