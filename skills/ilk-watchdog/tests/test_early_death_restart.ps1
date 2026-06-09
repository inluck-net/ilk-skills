<#
.SYNOPSIS
  Red integration test: an early-death run (sentinel present, no JSONL records)
  should be classified "interrupted" by collect.py and land in the watchdog's
  $WhitelistClasses for auto-restart (not POSTMORTEM FAILED hard-block).

.NOTES
  Invoked by local_checks in sub-plan 2026-06-07-watchdog-no-hard-block.
  Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent  # repo root
$scratch  = Join-Path $repoRoot "scratch\watchdog-early-death"

# Clean slate
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

# Create a temp project dir (no .git needed — we use project_key directly)
$tempProj = Join-Path $scratch "tempproj"
New-Item -ItemType Directory -Force -Path $tempProj | Out-Null

# Set ILK_DATA_HOME to scratch so we don't touch real ~/.ilk-data
$env:ILK_DATA_HOME = Join-Path $scratch "ilk-data"

# Set PYTHONPATH so collect.py and ilk_paths are importable from source tree
$env:PYTHONPATH = Join-Path $repoRoot "skills\ilk-loop\scripts"

# Compute the project key the same way collect.py does — using project_key(tempProj)
# directly, NOT via ilk_paths.py --where (which would walk up to .git and use
# the repo root key, causing a mismatch).
$key = (python -c "from ilk_paths import project_key; from pathlib import Path; print(project_key(Path(r'$tempProj')))").Trim()
if ($LASTEXITCODE -ne 0 -or -not $key) {
  Write-Error "Failed to compute project key"
  exit 1
}

# Build runtime dir from ILK_DATA_HOME + key (same formula as ilk_paths.external_runtime_dir)
$runtimeDir = Join-Path $env:ILK_DATA_HOME "projects\$key\runtime"

# Create runtime dirs
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $runtimeDir "launcher\postmortems") | Out-Null

# Write early-death sentinel: the run started but died before iter 1
$sentinel = @{ state = "interrupted"; iters = 1; run_id = "20260607-124231" } | ConvertTo-Json -Compress
$sentinel | Out-File -FilePath (Join-Path $runtimeDir "last-exit.json") -Encoding ascii -NoNewline

# Run collect.py (patched by sub-plan #2) — should exit 0 and produce a postmortem
$collectPy = Join-Path $repoRoot "skills\ilk-feedback\scripts\collect.py"
$collectOut = & python $collectPy -ProjectPath $tempProj --quiet 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Error "collect.py exited $LASTEXITCODE (expected 0). Output: $collectOut"
  exit 1
}
$postmortemPath = "$collectOut".Trim().Split("`n")[-1].Trim()
if (-not (Test-Path $postmortemPath)) {
  Write-Error "collect.py did not produce a valid postmortem file. Output: $collectOut"
  exit 1
}

# --- AC-1: dot-source guard exposes functions without entering poll loop ---
$env:ILK_DOTSOURCE_ONLY = '1'
$watchdogPath = Join-Path $repoRoot "skills\ilk-watchdog\scripts\watchdog.ps1"
try {
  . $watchdogPath -ProjectPath $tempProj
} catch {
  Write-Error "Dot-sourcing watchdog.ps1 failed: $_"
  exit 1
} finally {
  $env:ILK_DOTSOURCE_ONLY = $null
}

# --- AC-2: postmortem classification is 'interrupted' AND whitelisted ---
$fm = Read-PostmortemFrontmatter -Path $postmortemPath
$klass = $fm['classification']
if ($klass -ne 'interrupted') {
  Write-Error "FAIL: expected classification 'interrupted', got '$klass'"
  exit 1
}
if (-not ($WhitelistClasses -contains $klass)) {
  Write-Error "FAIL: '$klass' not in WhitelistClasses: $($WhitelistClasses -join ', ')"
  exit 1
}

# --- AC-3: blacklist classification is NOT in WhitelistClasses ---
# Create a second postmortem with a blacklist classification (local-checks-stuck)
$blacklistPostmortemDir = Join-Path $runtimeDir "launcher\postmortems"
$blacklistPm = Join-Path $blacklistPostmortemDir "20260607-130000-blacklist.md"
$now = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'
@"
---
project: $key
classification: local-checks-stuck
generated_at: $now
---

# Postmortem for $key (blacklist test)
"@ | Out-File -FilePath $blacklistPm -Encoding ascii -NoNewline

# Verify local-checks-stuck is in BlacklistClasses (not WhitelistClasses)
if ($WhitelistClasses -contains 'local-checks-stuck') {
  Write-Error "FAIL: 'local-checks-stuck' must NOT be in WhitelistClasses"
  exit 1
}
if (-not ($BlacklistClasses -contains 'local-checks-stuck')) {
  Write-Error "FAIL: 'local-checks-stuck' must be in BlacklistClasses: $($BlacklistClasses -join ', ')"
  exit 1
}

# --- AC-4: POSTMORTEM FAILED banner includes empty-repo hint ---
$watchdogSrc = Get-Content $watchdogPath -Raw
$hintPhrase = "git init"
if (-not ($watchdogSrc -match [regex]::Escape($hintPhrase))) {
  Write-Error "FAIL: watchdog.ps1 POSTMORTEM FAILED banner does not contain hint '$hintPhrase'"
  exit 1
}

# Clean up
$env:ILK_DATA_HOME = $null
$env:PYTHONPATH = $null
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

Write-Host "PASS: early-death run classified 'interrupted', whitelisted, banner hardened" -ForegroundColor Green
exit 0
