<#
.SYNOPSIS
  Test Get-IlkDataDir precedence: ILK_DATA_HOME > ILK_DATA_DIR > ~/.ilk-data.

.DESCRIPTION
  Dot-sources _ilk_data_dir.ps1 and asserts the three precedence cases (AC-1):
    1. ILK_DATA_HOME set   → returns ILK_DATA_HOME
    2. Only ILK_DATA_DIR set → returns ILK_DATA_DIR
    3. Neither set          → returns ~/.ilk-data
    4. Both set             → returns ILK_DATA_HOME (home wins)
#>
param(
  [ValidateSet('precedence', 'all')]
  [string]$Subcommand = 'all'
)

$ErrorActionPreference = 'Stop'

$fail = $false
function Assert($cond, $msg) {
  if (-not $cond) { Write-Host "FAIL: $msg" -ForegroundColor Red; $script:fail = $true }
}

function Run-Precedence {
  Write-Host '=== test_ilk_data_dir.ps1 precedence ==='

  # Dot-source the helper (functions only, no side effects).
  $helperPath = Join-Path $PSScriptRoot '..\..\..\skills\ilk-loop\scripts\_ilk_data_dir.ps1'
  . $helperPath

  # AC-1: Get-IlkDataDir must be defined
  Assert (Get-Command Get-IlkDataDir -ErrorAction SilentlyContinue) `
    "AC-1: Get-IlkDataDir must be defined"

  if ($fail) {
    Write-Host "RED: Get-IlkDataDir not found" -ForegroundColor Red
    exit 1
  }

  # Save and clear env vars for clean testing.
  $savedHome = $env:ILK_DATA_HOME
  $savedDir  = $env:ILK_DATA_DIR
  try {
    # Case 1: ILK_DATA_HOME set → returns ILK_DATA_HOME
    $env:ILK_DATA_HOME = 'C:\test-home'
    $env:ILK_DATA_DIR  = $null
    $result = Get-IlkDataDir
    Assert ($result -eq 'C:\test-home') "case 1: ILK_DATA_HOME set → returns '$result' (expected 'C:\test-home')"

    # Case 2: Only ILK_DATA_DIR set → returns ILK_DATA_DIR
    $env:ILK_DATA_HOME = $null
    $env:ILK_DATA_DIR  = 'C:\test-dir'
    $result = Get-IlkDataDir
    Assert ($result -eq 'C:\test-dir') "case 2: only ILK_DATA_DIR set → returns '$result' (expected 'C:\test-dir')"

    # Case 3: Neither set → returns ~/.ilk-data
    $env:ILK_DATA_HOME = $null
    $env:ILK_DATA_DIR  = $null
    $result = Get-IlkDataDir
    $expected = Join-Path $HOME '.ilk-data'
    Assert ($result -eq $expected) "case 3: neither set → returns '$result' (expected '$expected')"

    # Case 4: Both set → ILK_DATA_HOME wins
    $env:ILK_DATA_HOME = 'C:\test-home'
    $env:ILK_DATA_DIR  = 'C:\test-dir'
    $result = Get-IlkDataDir
    Assert ($result -eq 'C:\test-home') "case 4: both set → returns '$result' (expected 'C:\test-home')"

  } finally {
    $env:ILK_DATA_HOME = $savedHome
    $env:ILK_DATA_DIR  = $savedDir
  }

  if ($fail) {
    Write-Host "RED: Get-IlkDataDir precedence is incorrect" -ForegroundColor Red
    exit 1
  }
  Write-Host "PASS: Get-IlkDataDir — all precedence cases correct (AC-1)" -ForegroundColor Green
  exit 0
}

switch ($Subcommand) {
  'precedence' { Run-Precedence }
  'all'        { Run-Precedence }
}
