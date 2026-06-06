#Requires -Version 5.1
<#
.SYNOPSIS
  Baseline test: the default engine's dry-run shows planner-home routing.
  Uses -DryRun only — no provider calls, no real ~/.claude mutation.
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

  # Run the launcher in dry-run with the default engine.
  $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher -ProjectPath $tmpDir -DryRun 2>&1
  $outputStr = $output -join "`n"

  # Assert: output contains a ClaudeConfigDir line referencing the planner default.
  if ($outputStr -notmatch 'ClaudeConfigDir:.*default.*\.claude') {
    throw "FAIL: dry-run output missing ClaudeConfigDir planner-default line`n$outputStr"
  }

  # Assert: the ClaudeConfigDir line does NOT reference .claude-worker.
  $configdirLine = ($output | Where-Object { $_ -match 'ClaudeConfigDir:' })
  if ($configdirLine -match '\.claude-worker') {
    throw "FAIL: default engine should NOT route to .claude-worker`n$configdirLine"
  }

  Write-Host "PASS"
} finally {
  Pop-Location
  Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}
