<#
.SYNOPSIS
  Verify automatable prerequisites declared in PREREQUISITES.md.

.DESCRIPTION
  Customise the checks below per project before launching the loop.
  The loop launcher / watchdog can call this script to fail fast when
  the environment is not ready.

  Exit codes:
    0  all checks pass
    1  one or more checks failed (details printed)
    2  script error (e.g. missing PowerShell module)
#>

$ErrorActionPreference = 'Stop'
$failed = @()

function Check-Tool {
  param([string]$Name, [string]$MinVersion = $null)
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $cmd) {
    Write-Host "[FAIL] tool not on PATH: $Name" -ForegroundColor Red
    return $false
  }
  Write-Host "[ ok ] $Name -> $($cmd.Source)"
  return $true
}

function Check-Port {
  param([int]$Port, [string]$Service)
  $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if (-not $listening) {
    Write-Host "[FAIL] expected $Service on port $Port — nothing listening" -ForegroundColor Red
    return $false
  }
  Write-Host "[ ok ] $Service on port $Port (PID $($listening.OwningProcess | Select-Object -First 1))"
  return $true
}

function Check-EnvVar {
  param([string]$Name)
  $val = [Environment]::GetEnvironmentVariable($Name)
  if ([string]::IsNullOrEmpty($val)) {
    Write-Host "[FAIL] env var not set: $Name" -ForegroundColor Red
    return $false
  }
  Write-Host "[ ok ] env var $Name is set"
  return $true
}

# ── Section B: tools on PATH ────────────────────────────────────────
# Uncomment / extend per project:
# if (-not (Check-Tool 'git'))    { $failed += 'git' }
# if (-not (Check-Tool 'node'))   { $failed += 'node' }
# if (-not (Check-Tool 'python')) { $failed += 'python' }

# ── Section A: services on expected ports ───────────────────────────
# if (-not (Check-Port 5173 'vite dev')) { $failed += 'vite' }

# ── Section C: env vars ─────────────────────────────────────────────
# if (-not (Check-EnvVar 'OPENAI_API_KEY')) { $failed += 'OPENAI_API_KEY' }

# ── Result ──────────────────────────────────────────────────────────
if ($failed.Count -gt 0) {
  Write-Host ""
  Write-Host "FAILED: $($failed.Count) prereq(s) missing: $($failed -join ', ')" -ForegroundColor Red
  exit 1
}
Write-Host ""
Write-Host "OK: all declared prereqs present" -ForegroundColor Green
exit 0
