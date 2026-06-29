<#
.SYNOPSIS
  Runtime gate for the 2026-06-30 three-project scheduler crash fixes.

  Dot-sources the REAL scheduler.ps1 (ILK_DOTSOURCE_ONLY=1) and exercises:
   AC-1  $BootstrapScript resolves to an existing bootstrap.ps1 even through
         the symlinked/junctioned skills install (was ~/.claude\tools\... → 404).
   AC-2  Invoke-SchedulerScan returns parsed JSON when the scan writes to
         stderr AND exits 0 (the `2>&1` + $ErrorActionPreference='Stop' trap
         used to turn that stderr into a daemon-killing NativeCommandError).
   AC-3  Invoke-SchedulerScan throws on non-zero exit (so the caller can react).
   AC-4  Run-Scheduler SURVIVES a crashing scan: it emits decision=scan-error
         and returns cleanly instead of letting the throw kill the daemon.

  This is a RUNTIME gate, not a parse check — PS wiring has repeatedly shipped
  runtime-broken while parsing clean. Exit 0 = green, exit 1 = red.
#>

$ErrorActionPreference = 'Stop'
$repoRoot   = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$scheduler  = Join-Path $repoRoot 'skills\ilk-watchdog\scripts\scheduler.ps1'
$scratch    = Join-Path $repoRoot 'scratch\scheduler-resilience'

if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

function Fail($msg) { Write-Error "FAIL: $msg"; exit 1 }

# --- dot-source the scheduler (functions + constants only, no poll loop) ---
$env:ILK_DOTSOURCE_ONLY = '1'
try {
  . $scheduler -DryRun -Once
} catch {
  $env:ILK_DOTSOURCE_ONLY = $null
  Fail "dot-sourcing scheduler.ps1 threw: $_"
}

# --- AC-1: bootstrap path resolves to a real file ---
if (-not $BootstrapScript) { Fail "`$BootstrapScript is null/empty" }
if (-not (Test-Path $BootstrapScript)) {
  Fail "`$BootstrapScript does not exist: $BootstrapScript (symlink-resolution regressed)"
}
if ($BootstrapScript -notmatch 'claude-worker[\\/]bootstrap\.ps1$') {
  Fail "`$BootstrapScript points somewhere unexpected: $BootstrapScript"
}
Write-Host "  AC-1 ok: bootstrap -> $BootstrapScript"

# --- stub scan scripts ---
$stubOk = Join-Path $scratch 'scan_ok.py'
@'
import sys
sys.stderr.write("benign warning on stderr\n")
print('[{"key":"p","path":"x","repo_path":"r","oldest_queued_ts":"2026-06-30T00:00:00","has_active_master":true}]')
'@ | Out-File -FilePath $stubOk -Encoding ascii

$stubCrash = Join-Path $scratch 'scan_crash.py'
@'
import sys
sys.stderr.write("Traceback (most recent call last):\n  boom\n")
sys.exit(1)
'@ | Out-File -FilePath $stubCrash -Encoding ascii

# --- AC-2: stderr + exit 0 → parsed result, no throw ---
$ScanScript = $stubOk
try {
  $res = Invoke-SchedulerScan
} catch {
  Fail "Invoke-SchedulerScan threw on a stderr-emitting exit-0 scan: $_"
}
if (-not $res -or $res[0].key -ne 'p') {
  Fail "Invoke-SchedulerScan did not parse stub JSON (got: $($res | ConvertTo-Json -Compress))"
}
Write-Host "  AC-2 ok: stderr noise on exit 0 parsed cleanly"

# --- AC-3: non-zero exit → throws ---
$ScanScript = $stubCrash
$threw = $false
try { Invoke-SchedulerScan } catch { $threw = $true }
if (-not $threw) { Fail "Invoke-SchedulerScan did NOT throw on a non-zero-exit scan" }
Write-Host "  AC-3 ok: non-zero exit throws"

# --- AC-4: Run-Scheduler survives a crashing scan (daemon does not die) ---
# $DryRun + $Once are set from the dot-source args, so Run-Scheduler runs one
# cycle and returns. With a crashing scan it must hit the scan-error branch.
$ScanScript = $stubCrash
$out = Run-Scheduler 6>&1 | Out-String
if ($out -notmatch 'scan-error') {
  Fail "Run-Scheduler did not emit scan-error on a crashing scan (got: $out)"
}
Write-Host "  AC-4 ok: Run-Scheduler survived crashing scan -> scan-error"

# --- cleanup ---
$env:ILK_DOTSOURCE_ONLY = $null
try { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue } catch {}

Write-Host "PASS: scheduler resilience (bootstrap path + scan stderr/crash survival)" -ForegroundColor Green
exit 0
