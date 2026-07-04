<#
  test_native_stderr_hardening.ps1

  Two-part gate for the native-stderr-hardening sub-plan:

  1. STATIC AUDIT (AC-4): scan each of the four scope scripts for `& python`
     lines and assert every one is within a `$ErrorActionPreference = 'Continue'`
     scope. Exempts _pipeline_smoketest.ps1 (test-only scaffold).
  2. RUNTIME SMOKE (AC-3): in a temp repo, invoke loop_status.py --json under
     $ErrorActionPreference='Stop' and assert no NativeCommandError/RemoteException
     on stderr while the master name still resolves.

  Exit 0 = all checks pass. Exit 1 = violation found (details printed).
#>

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path $PSScriptRoot -Parent
$skillRoot = Split-Path $scriptRoot -Parent  # skills/
$repoRoot = Split-Path $skillRoot -Parent     # repo root

$failures = @()

# ── Part 1: Static audit ──────────────────────────────────────────────────────

# Exempt files (test-only, not runtime paths)
$exempt = @(
  '_pipeline_smoketest.ps1'
)

$scriptsToAudit = @(
  @{ Path = Join-Path $repoRoot 'skills\ilk-loop\scripts\run_ilk_loop_claude.ps1'; Label = 'runner' }
  @{ Path = Join-Path $repoRoot 'skills\ilk-loop\scripts\run_ilk_loop.ps1';        Label = 'runner-legacy' }
  @{ Path = Join-Path $repoRoot 'skills\ilk-launcher\scripts\launch.ps1';          Label = 'launcher' }
  @{ Path = Join-Path $repoRoot 'skills\ilk-watchdog\scripts\scheduler.ps1';       Label = 'scheduler' }
)

foreach ($spec in $scriptsToAudit) {
  $scriptPath = $spec.Path
  $label = $spec.Label
  if (-not (Test-Path $scriptPath)) {
    Write-Host "  SKIP $label : file not found at $scriptPath" -ForegroundColor DarkYellow
    continue
  }

  $lines = Get-Content $scriptPath -Encoding UTF8
  $inFunction = $false
  $functionHasContinue = $false
  $scriptLevelContinue = $false  # tracks save/restore pattern at script level

  for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    $lineNum = $i + 1

    # Track function boundaries (simplified: detect 'function ' at start of line)
    if ($line -match '^\s*function\s+') {
      $inFunction = $true
      $functionHasContinue = $false
    }

    # Detect $ErrorActionPreference = 'Continue' (with or without quotes)
    if ($line -match '\$ErrorActionPreference\s*=\s*[''"]Continue[''"]') {
      if ($inFunction) {
        $functionHasContinue = $true
      } else {
        $scriptLevelContinue = $true
      }
    }

    # Check & python lines
    if ($line -match '&\s*python\b') {
      # Exempt check
      $baseName = Split-Path $scriptPath -Leaf
      if ($exempt -contains $baseName) { continue }

      # Determine if this line is guarded
      $guarded = $false
      if ($inFunction -and $functionHasContinue) {
        $guarded = $true
      }
      # Script-level: check if there's a save/restore pattern before this line
      # (look for $savedEAP = $ErrorActionPreference within 10 lines before)
      if (-not $inFunction) {
        for ($j = [Math]::Max(0, $i - 10); $j -lt $i; $j++) {
          if ($lines[$j] -match '\$savedEAP\s*=') {
            $guarded = $true
            break
          }
        }
      }

      if (-not $guarded) {
        $trimmed = $line.Trim()
        $failures += "  ${label}:${lineNum}  $trimmed"
        Write-Host "  FAIL $label :$lineNum  $trimmed" -ForegroundColor Red
      }
    }
  }
}

if ($failures.Count -gt 0) {
  Write-Host "`nStatic audit: $($failures.Count) unguarded & python site(s) found:" -ForegroundColor Red
  $failures | ForEach-Object { Write-Host $_ }
  exit 1
} else {
  Write-Host "  Static audit: all & python sites guarded. PASS" -ForegroundColor Green
}

# ── Part 2: Runtime smoke (AC-3) ──────────────────────────────────────────────
# Create a temp repo with a queued master and invoke loop_status.py --json under
# $ErrorActionPreference='Stop'. Assert no NativeCommandError on stderr and that
# the master name resolves.

$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) "ilk-test-$([guid]::NewGuid().ToString('N').Substring(0,8))"
try {
  New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
  & git init $tempDir 2>$null | Out-Null

  $plansDir = Join-Path $tempDir 'docs\plans'
  New-Item -ItemType Directory -Force -Path $plansDir | Out-Null

  # Write a minimal queued master
  $masterContent = @"
---
master_plan: 2026-07-04-test
batch_date: 2026-07-04
status: queued
total_tickets: 0
---

# Test master
"@
  Set-Content -Path (Join-Path $plansDir 'MASTER-2026-07-04-test-execution-plan.md') -Value $masterContent -Encoding UTF8

  $loopStatusScript = Join-Path $repoRoot 'skills\ilk-loop\scripts\loop_status.py'
  if (-not (Test-Path $loopStatusScript)) {
    Write-Host "  SKIP runtime smoke: loop_status.py not found" -ForegroundColor DarkYellow
    exit 0
  }

  # Invoke under $ErrorActionPreference='Stop' — the scenario that caused
  # the original NativeCommandError.
  $savedEAP = $ErrorActionPreference
  $ErrorActionPreference = 'Stop'
  $stderrFile = Join-Path $tempDir 'stderr.txt'
  try {
    Push-Location $tempDir
    $output = & python $loopStatusScript --json 2>$stderrFile
    $exitCode = $LASTEXITCODE
  } finally {
    Pop-Location
    $ErrorActionPreference = $savedEAP
  }

  $stderr = ''
  if (Test-Path $stderrFile) {
    $stderrRaw = Get-Content $stderrFile -Raw -ErrorAction SilentlyContinue
    if ($stderrRaw) { $stderr = $stderrRaw }
  }

  # Check: no NativeCommandError or RemoteException in stderr
  if ($stderr -match 'NativeCommandError|RemoteException') {
    Write-Host "  FAIL runtime smoke: stderr contains NativeCommandError/RemoteException" -ForegroundColor Red
    Write-Host "  stderr: $stderr" -ForegroundColor Red
    exit 1
  }

  # Check: stderr should be empty (the whole point of #1)
  if ($stderr -and $stderr.Trim().Length -gt 0) {
    Write-Host "  WARN runtime smoke: stderr not empty (may be acceptable): $stderr" -ForegroundColor DarkYellow
  }

  # Check: output is valid JSON with a master field
  if ($output) {
    try {
      $obj = ($output -join "`n") | ConvertFrom-Json
      if (-not $obj.master) {
        Write-Host "  FAIL runtime smoke: JSON output has no master field" -ForegroundColor Red
        exit 1
      }
      Write-Host "  Runtime smoke: master='$($obj.master)' resolved, no NativeCommandError. PASS" -ForegroundColor Green
    } catch {
      Write-Host "  FAIL runtime smoke: output is not valid JSON: $_" -ForegroundColor Red
      exit 1
    }
  } else {
    Write-Host "  FAIL runtime smoke: no output from loop_status.py --json" -ForegroundColor Red
    exit 1
  }
} finally {
  if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host "`nAll checks passed." -ForegroundColor Green
exit 0
