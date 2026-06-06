# Self-contained test for the PowerShell installer's -OnlyPath mode.
# Runs in throwaway temp sandboxes; never touches the real home.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $PSCommandPath
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..\..")
$Install = Join-Path $RepoRoot "install.ps1"
$Source = Join-Path $RepoRoot "tools\claude-worker\claude-worker.ps1"

$pass = 0
$fail = 0

function Ok([string]$msg) { $script:pass++; Write-Host "  OK: $msg" -ForegroundColor Green }
function Die([string]$msg) { $script:fail++; Write-Host "  FAIL: $msg" -ForegroundColor Red }

Write-Host "=== test_install_path.ps1 ==="
Write-Host "repo: $RepoRoot"

# --- Test 1: -OnlyPath -Apply creates .cmd with correct content ---
Write-Host ""
Write-Host "Test 1: apply creates .cmd with correct content"
$t = Join-Path $env:TEMP ("ilk-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $t -Force | Out-Null
try {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Install -OnlyPath -PathBinDir $t -Apply | Out-Null
  $cmd = Join-Path $t "claude-worker.cmd"
  if (Test-Path $cmd) {
    $content = Get-Content -LiteralPath $cmd -Raw
    if ($content -like "*claude-worker.ps1*") {
      Ok ".cmd exists and references .ps1"
    } else {
      Die ".cmd exists but does not reference .ps1"
    }
    if ($content -notlike "*dp0*") {
      Ok ".cmd does not use %~dp0"
    } else {
      Die ".cmd uses %~dp0 (should use absolute path)"
    }
  } else {
    Die ".cmd was not created"
  }
} finally {
  Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Test 2: idempotent re-run ---
Write-Host ""
Write-Host "Test 2: idempotent re-run"
$t = Join-Path $env:TEMP ("ilk-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $t -Force | Out-Null
try {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Install -OnlyPath -PathBinDir $t -Apply | Out-Null
  $rc1 = $LASTEXITCODE
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Install -OnlyPath -PathBinDir $t -Apply | Out-Null
  $rc2 = $LASTEXITCODE
  if ($rc1 -eq 0 -and $rc2 -eq 0) {
    Ok "both runs exit 0"
  } else {
    Die "exit codes: first=$rc1 second=$rc2"
  }
  $cmd = Join-Path $t "claude-worker.cmd"
  if (Test-Path $cmd) {
    $content = Get-Content -LiteralPath $cmd -Raw
    if ($content -like "*claude-worker.ps1*") {
      Ok ".cmd still correct after re-run"
    } else {
      Die ".cmd corrupted after re-run"
    }
  } else {
    Die ".cmd missing after re-run"
  }
} finally {
  Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Test 3: dry-run (no -Apply) writes nothing ---
Write-Host ""
Write-Host "Test 3: dry-run writes nothing"
$t = Join-Path $env:TEMP ("ilk-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $t -Force | Out-Null
try {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Install -OnlyPath -PathBinDir $t | Out-Null
  $cmd = Join-Path $t "claude-worker.cmd"
  if (-not (Test-Path $cmd)) {
    Ok "no .cmd created in dry-run"
  } else {
    Die ".cmd was created during dry-run"
  }
} finally {
  Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Test 4: -PathBinDir override ---
Write-Host ""
Write-Host "Test 4: -PathBinDir override"
$t = Join-Path $env:TEMP ("ilk-test-" + [guid]::NewGuid().ToString("N"))
$bindir = Join-Path $t "custom-bin"
New-Item -ItemType Directory -Path $t -Force | Out-Null
try {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Install -OnlyPath -PathBinDir $bindir -Apply | Out-Null
  $cmd = Join-Path $bindir "claude-worker.cmd"
  if (Test-Path $cmd) {
    $content = Get-Content -LiteralPath $cmd -Raw
    if ($content -like "*claude-worker.ps1*") {
      Ok "custom bin dir used"
    } else {
      Die ".cmd in custom dir does not reference .ps1"
    }
  } else {
    Die ".cmd not created in custom bin dir"
  }
} finally {
  Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Summary ---
Write-Host ""
Write-Host "Results: pass=$pass fail=$fail"
if ($fail -gt 0) {
  Write-Host "FAILED" -ForegroundColor Red
  throw "Test failures: $fail"
}
Write-Host "PASS" -ForegroundColor Green
exit 0
