<#
.SYNOPSIS
  Static wiring test — asserts ilk-tray.ps1 contains the click-opens-postmortem
  branch and retains the guarded menu-rebuild + diagnostics.

.DESCRIPTION
  Reads the tray script as raw text and checks for the patterns that wire up
  blocked-row click handling.  This is a RED test: the report_path / open-report
  branch does not exist yet; step 1 will make it GREEN.

  Mirrors the pattern of the L3 tray diagnostics test (test_ilk_tray_blocked.py).
#>

$ErrorActionPreference = "Stop"

# Resolve the tray script path relative to this test file.
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$TrayScript = Join-Path (Split-Path -Parent $ScriptDir) "ilk-tray.ps1"

if (-not (Test-Path $TrayScript)) {
  Write-Host "FAIL: tray script not found: $TrayScript"
  exit 1
}

$src = Get-Content -LiteralPath $TrayScript -Raw -Encoding UTF8
$failures = @()

# ── AC-1: click handler reads report_path and opens it ────────────────
# The Add_Click block should check for $r.action.report_path (or similar)
# and call Start-Process / Invoke-Item on it when present.

# Pattern: inside Add_Click, a branch that opens report_path
$hasReportPathOpen = $src -match 'report_path' -and $src -match 'Start-Process|Invoke-Item'
if (-not $hasReportPathOpen) {
  $failures += "AC-1: click handler does not open report_path via Start-Process/Invoke-Item"
}

# ── AC-2: guarded menu rebuild is retained ─────────────────────────────
$hasTryCatch = $src -match 'try\s*\{' -and $src -match 'catch\s*\{'
$hasOldMenuSwap = $src -match '\$oldMenu\s*=.*ContextMenuStrip' -or
                  $src -match 'ContextMenuStrip\s*=\s*\$menu'
if (-not $hasTryCatch) {
  $failures += "AC-2: try/catch guard missing from Invoke-Tick"
}
if (-not $hasOldMenuSwap) {
  $failures += "AC-2: guarded menu swap (oldMenu dispose) missing"
}

# ── AC-3: diagnostics — Write-TrayLog call retained ───────────────────
$hasDiagnostics = $src -match 'Write-TrayLog'
if (-not $hasDiagnostics) {
  $failures += "AC-3: Write-TrayLog diagnostics call missing"
}

# ── Report ─────────────────────────────────────────────────────────────
if ($failures.Count -gt 0) {
  Write-Host "FAIL ($($failures.Count) assertion(s)):"
  foreach ($f in $failures) {
    Write-Host "  - $f"
  }
  exit 1
}

Write-Host "PASS: all wiring assertions satisfied"
exit 0
