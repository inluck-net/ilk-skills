#Requires -Version 5.1
<#
.SYNOPSIS
  Routing test matrix for the ilk-launcher engine routing.
  All assertions use -DryRun only — no provider calls, no real ~/.claude mutation.
#>
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$launcher = Join-Path $repoRoot 'skills\ilk-launcher\scripts\launch.ps1'

# Create a minimal temp project so Resolve-ProjectByCwd can find it.
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) "ilk-test-$(Get-Random)"
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tmpDir 'docs\plans') -Force | Out-Null
Set-Content -Path (Join-Path $tmpDir 'docs\plans\MASTER-test.md') -Value '---'
Push-Location $tmpDir
try {
  git init -q 2>$null

  $passCount = 0
  $failCount = 0

  function Assert-Test {
    param([string]$Name, [scriptblock]$Test)
    if (& $Test) {
      Write-Host "  PASS: $Name"
      $script:passCount++
    } else {
      Write-Host "  FAIL: $Name" -ForegroundColor Red
      $script:failCount++
    }
  }

  Write-Host "=== test_worker_engine_routing.ps1 ==="

  # --- AC-3: default engine → planner default, no .claude-worker ---
  Write-Host "--- AC-3: default engine dry-run ---"

  $outDefault = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher -ProjectPath $tmpDir -DryRun 2>&1
  $outDefaultStr = $outDefault -join "`n"

  Assert-Test "default: ClaudeConfigDir line present" {
    $outDefaultStr -match 'ClaudeConfigDir:.*default.*\.claude'
  }

  Assert-Test "default: no .claude-worker in ClaudeConfigDir" {
    $configdirLine = ($outDefault | Where-Object { $_ -match 'ClaudeConfigDir:' })
    $configdirLine -notmatch '\.claude-worker'
  }

  # --- AC-2: claude-worker engine → worker home routing ---
  Write-Host "--- AC-2: claude-worker engine dry-run ---"

  $outWorker = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher -ProjectPath $tmpDir -Engine claude-worker -DryRun 2>&1
  $outWorkerStr = $outWorker -join "`n"

  Assert-Test "claude-worker: ClaudeConfigDir ends in .claude-worker" {
    $outWorkerStr -match 'ClaudeConfigDir:.*\.claude-worker'
  }

  Assert-Test "claude-worker: IlkSkillHome ends in .claude-worker.skills" {
    $outWorkerStr -match 'IlkSkillHome:.*\.claude-worker\\skills'
  }

  # --- AC-1: invalid engine → non-zero exit + error message ---
  Write-Host "--- AC-1: invalid engine ---"

  $exitInvalid = 0
  $outInvalid = ""
  try {
    $outInvalid = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher -ProjectPath $tmpDir -Engine bogus -DryRun 2>&1
  } catch {
    $exitInvalid = 1
    $outInvalid = $_.Exception.Message
  }
  # powershell.exe propagates the child's exit code
  if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { $exitInvalid = $LASTEXITCODE }
  $outInvalidStr = ($outInvalid | Out-String)

  Assert-Test "invalid engine: error mentions valid engines" {
    $outInvalidStr -match '(?i)valid.*engine'
  }

  Write-Host ""
  Write-Host "Results: $passCount passed, $failCount failed"
  if ($failCount -gt 0) {
    throw "$failCount test(s) failed"
  }
  Write-Host "ALL PASS"
} finally {
  Pop-Location
  Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}
