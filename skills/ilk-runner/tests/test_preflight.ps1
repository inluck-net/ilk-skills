<#
.SYNOPSIS
  Red test: Get-PreflightDecision must block unsafe supervised-only launches,
  promote queued masters, and reject draft masters.

.NOTES
  Invoked by local_checks in sub-plan 2026-06-16-ilk-runner-preflight.
  Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$scratch  = Join-Path $repoRoot "scratch\preflight-test"

# Clean slate
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

$tempProj = Join-Path $scratch "tempproj"
New-Item -ItemType Directory -Force -Path $tempProj | Out-Null

# --- AC-1: dot-source guard exposes Get-PreflightDecision ---
Write-Host "=== Dot-source guard ==="
$env:ILK_DOTSOURCE_ONLY = '1'
$preflightPath = Join-Path $repoRoot "skills\ilk-runner\scripts\preflight.ps1"
try {
  . $preflightPath -ProjectRoot $tempProj
} catch {
  Write-Error "FAIL: Dot-sourcing preflight.ps1 failed: $_"
  exit 1
} finally {
  $env:ILK_DOTSOURCE_ONLY = $null
}

if (-not (Get-Command Get-PreflightDecision -ErrorAction SilentlyContinue)) {
  Write-Error "FAIL: Get-PreflightDecision function not found after dot-sourcing preflight.ps1"
  exit 1
}

# --- Decision matrix ---
$failures = @()

# AC-1: supervised + scheduler alive → block
Write-Host "=== AC-1: supervised + scheduler alive ==="
$decision = Get-PreflightDecision -MasterStatus 'active' -HasActive $true -Supervised $true -SchedulerAlive $true
if (-not $decision.block) {
  $failures += "AC-1a: supervised+alive: expected block=true, got block=$($decision.block)"
}
if ($decision.reason -notmatch 'scheduler') {
  $failures += "AC-1a: supervised+alive: reason should mention scheduler, got '$($decision.reason)'"
}

# AC-1: supervised + scheduler not alive → no block
$decision = Get-PreflightDecision -MasterStatus 'active' -HasActive $true -Supervised $true -SchedulerAlive $false
if ($decision.block) {
  $failures += "AC-1b: supervised+not-alive: expected block=false, got block=$($decision.block)"
}

# AC-2: queued + no active → promote
Write-Host "=== AC-2: queued + no active ==="
$decision = Get-PreflightDecision -MasterStatus 'queued' -HasActive $false -Supervised $false -SchedulerAlive $false
if (-not $decision.promote) {
  $failures += "AC-2a: queued+no-active: expected promote=true, got promote=$($decision.promote)"
}
if ($decision.block) {
  $failures += "AC-2a: queued+no-active: expected block=false, got block=$($decision.block)"
}

# AC-2: draft → block (held)
Write-Host "=== AC-2: draft → block ==="
$decision = Get-PreflightDecision -MasterStatus 'draft' -HasActive $false -Supervised $false -SchedulerAlive $false
if (-not $decision.block) {
  $failures += "AC-2b: draft: expected block=true, got block=$($decision.block)"
}
if ($decision.reason -notmatch 'draft') {
  $failures += "AC-2b: draft: reason should mention draft, got '$($decision.reason)'"
}

# Not-supervised + scheduler alive → no block (scheduler doesn't gate non-supervised)
Write-Host "=== Non-supervised + scheduler alive ==="
$decision = Get-PreflightDecision -MasterStatus 'active' -HasActive $true -Supervised $false -SchedulerAlive $true
if ($decision.block) {
  $failures += "non-supervised+alive: expected block=false, got block=$($decision.block)"
}

# queued + already has active → no promote, no block
Write-Host "=== queued + already has active ==="
$decision = Get-PreflightDecision -MasterStatus 'queued' -HasActive $true -Supervised $false -SchedulerAlive $false
if ($decision.promote) {
  $failures += "queued+has-active: expected promote=false, got promote=$($decision.promote)"
}
if ($decision.block) {
  $failures += "queued+has-active: expected block=false, got block=$($decision.block)"
}

# Clean up
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: Get-PreflightDecision — all decision matrix cases correct" -ForegroundColor Green
exit 0
