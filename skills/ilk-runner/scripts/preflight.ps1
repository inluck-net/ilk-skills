<#
.SYNOPSIS
  Preflight gate for manual /ilk-run launches.

.DESCRIPTION
  Enforces three checks before the runner launches the loop:
    (a) supervised_only master + live scheduler → HARD STOP
    (b) queued master, none active → promote (reuse Invoke-Promote)
    (c) stale idle host windows + terminal sentinels → surface as warnings

  Exposes Get-PreflightDecision (pure) via ILK_DOTSOURCE_ONLY guard for testing.

.PARAMETER ProjectRoot
  The resolved project root path.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File preflight.ps1 -ProjectRoot C:\mywork\project
#>
[CmdletBinding()]
param(
  [string]$ProjectRoot = ""
)

$ErrorActionPreference = 'Stop'

# --- Dot-source guard: expose functions without running main ---
if ($env:ILK_DOTSOURCE_ONLY -eq '1') {
  function Get-PreflightDecision {
    param(
      [string]$MasterStatus,
      [bool]$HasActive,
      [bool]$Supervised,
      [bool]$SchedulerAlive
    )
    # (a) supervised + scheduler alive → block
    if ($Supervised -and $SchedulerAlive) {
      return @{
        block   = $true
        reason  = "A cross-project scheduler is alive. Stop it before running a supervised_only master."
        promote = $false
      }
    }
    # (b-i) draft → block (held deliberately)
    if ($MasterStatus -eq 'draft') {
      return @{
        block   = $true
        reason  = "Master is 'draft' (held). Set it queued/active before launching."
        promote = $false
      }
    }
    # (b-ii) queued + no active → promote
    if ($MasterStatus -eq 'queued' -and -not $HasActive) {
      return @{
        block   = $false
        reason  = ""
        promote = $true
      }
    }
    # Safe to proceed
    return @{
      block   = $false
      reason  = ""
      promote = $false
    }
  }

  function Test-SchedulerAlive {
    # Check for a running scheduler.ps1 or scheduler.sh process
    try {
      $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'powershell|pwsh' -and $_.CommandLine -match 'scheduler\.ps1' }
      if ($procs) { return $true }
    } catch {
      # Fallback to tasklist
      $lines = & tasklist /FI "IMAGENAME eq powershell.exe" /FO CSV 2>&1
      if ($lines -match 'scheduler\.ps1') { return $true }
      $lines = & tasklist /FI "IMAGENAME eq pwsh.exe" /FO CSV 2>&1
      if ($lines -match 'scheduler\.ps1') { return $true }
    }
    return $false
  }

  function Get-StaleWarnings {
    param([string]$ProjRoot)
    $warnings = @()

    # (c-i) Idle -NoExit host windows (powershell processes matching run_ilk_loop / watchdog)
    try {
      $hostProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
          ($_.Name -match 'powershell|pwsh') -and
          ($_.CommandLine -match 'run_ilk_loop|watchdog\.ps1')
        }
      if ($hostProcs) {
        foreach ($p in $hostProcs) {
          $procAlive = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
          if ($procAlive) {
            $warnings += "WARNING: Idle host window detected — PID $($p.ProcessId) ($($p.Name)): $($p.CommandLine)"
          }
        }
      }
    } catch {
      # Non-fatal; skip stale-window detection
    }

    # (c-ii) Terminal-state sentinels with live PIDs
    $ilDataPath = Join-Path $HOME ".ilk-data"
    $projectKey = Split-Path $ProjRoot -Leaf
    $sentinelFile = Join-Path $ilDataPath "projects\$projectKey\runtime\last-exit.json"
    if (Test-Path $sentinelFile) {
      try {
        $sentinel = Get-Content $sentinelFile -Raw | ConvertFrom-Json
        if ($sentinel.state -and $sentinel.state -ne 'running' -and $sentinel.pid) {
          $sentinelProc = Get-Process -Id $sentinel.pid -ErrorAction SilentlyContinue
          if ($sentinelProc) {
            $warnings += "WARNING: Terminal sentinel with live PID $($sentinel.pid) (state=$($sentinel.state)). Consider cleaning stale state."
          }
        }
      } catch {
        # Non-fatal
      }
    }

    return $warnings
  }

  return  # Exit early — don't run main block
}

# --- Resolve dependencies ---
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_resolve_python.ps1")

$SkillRoot = Get-IlkSkillRoot
$LoopStatusPy = Join-Path $SkillRoot "ilk-loop\scripts\loop_status.py"
$PromotePy = Join-Path $SkillRoot "ilk-loop\scripts\promote_next_master.py"

function Invoke-LoopStatus {
  param([string]$ProjectRoot)
  $result = Invoke-IlkPythonCapture -WorkingDirectory $ProjectRoot -ArgumentList @($LoopStatusPy)
  return $result
}

function Invoke-Promote {
  param([string]$ProjectRoot)
  $result = Invoke-IlkPythonCapture -ArgumentList @($PromotePy, "--project", $ProjectRoot)
  if ($result.ExitCode -ne 0) {
    throw "promote_next_master.py failed: $($result.Output)"
  }
  return ($result.Output | ConvertFrom-Json)
}

# Re-define the functions for main use (not guarded)
function Get-PreflightDecision {
  param(
    [string]$MasterStatus,
    [bool]$HasActive,
    [bool]$Supervised,
    [bool]$SchedulerAlive
  )
  if ($Supervised -and $SchedulerAlive) {
    return @{
      block   = $true
      reason  = "A cross-project scheduler is alive. Stop it before running a supervised_only master."
      promote = $false
    }
  }
  if ($MasterStatus -eq 'draft') {
    return @{
      block   = $true
      reason  = "Master is 'draft' (held). Set it queued/active before launching."
      promote = $false
    }
  }
  if ($MasterStatus -eq 'queued' -and -not $HasActive) {
    return @{
      block   = $false
      reason  = ""
      promote = $true
    }
  }
  return @{
    block   = $false
    reason  = ""
    promote = $false
  }
}

function Test-SchedulerAlive {
  try {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match 'powershell|pwsh' -and $_.CommandLine -match 'scheduler\.ps1' }
    if ($procs) { return $true }
  } catch {
    $lines = & tasklist /FI "IMAGENAME eq powershell.exe" /FO CSV 2>&1
    if ($lines -match 'scheduler\.ps1') { return $true }
    $lines = & tasklist /FI "IMAGENAME eq pwsh.exe" /FO CSV 2>&1
    if ($lines -match 'scheduler\.ps1') { return $true }
  }
  return $false
}

function Get-StaleWarnings {
  param([string]$ProjRoot)
  $warnings = @()
  try {
    $hostProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
        ($_.Name -match 'powershell|pwsh') -and
        ($_.CommandLine -match 'run_ilk_loop|watchdog\.ps1')
      }
    if ($hostProcs) {
      foreach ($p in $hostProcs) {
        $procAlive = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        if ($procAlive) {
          $warnings += "WARNING: Idle host window detected — PID $($p.ProcessId) ($($p.Name)): $($p.CommandLine)"
        }
      }
    }
  } catch {}

  $ilDataPath = Join-Path $HOME ".ilk-data"
  $projectKey = Split-Path $ProjRoot -Leaf
  $sentinelFile = Join-Path $ilDataPath "projects\$projectKey\runtime\last-exit.json"
  if (Test-Path $sentinelFile) {
    try {
      $sentinel = Get-Content $sentinelFile -Raw | ConvertFrom-Json
      if ($sentinel.state -and $sentinel.state -ne 'running' -and $sentinel.pid) {
        $sentinelProc = Get-Process -Id $sentinel.pid -ErrorAction SilentlyContinue
        if ($sentinelProc) {
          $warnings += "WARNING: Terminal sentinel with live PID $($sentinel.pid) (state=$($sentinel.state)). Consider cleaning stale state."
        }
      }
    } catch {}
  }
  return $warnings
}

# --- Main ---
if (-not $ProjectRoot) {
  Write-Error "ProjectRoot is required when not dot-sourcing."
  exit 1
}

# Resolve master state from loop_status
$status = Invoke-LoopStatus -ProjectRoot $ProjectRoot
$statusText = $status.Output

# Parse master status from the status output
$masterStatus = 'unknown'
$hasActive = $false

# Look for master status indicators in the output
if ($statusText -match 'Master:.*status:\s*(\S+)') {
  $masterStatus = $Matches[1]
}
if ($statusText -match 'status:\s*active') {
  $hasActive = $true
}
# Check for any active sub-plans
if ($status.ExitCode -eq 1) {
  # There's pending work — master is at least active
  if ($masterStatus -eq 'unknown') { $masterStatus = 'active' }
}

# Determine if master is supervised_only
$supervised = $false
# Look for the MASTER file in the plans dir to check supervised_only
$plansDir = Join-Path $HOME ".ilk-data\projects\$(Split-Path $ProjectRoot -Leaf)\plans"
$masterFile = Get-ChildItem $plansDir -Filter "MASTER-*.md" -ErrorAction SilentlyContinue |
  Select-Object -First 1
if ($masterFile) {
  $masterContent = Get-Content $masterFile.FullName -Raw
  if ($masterContent -match 'supervised_only:\s*true') {
    $supervised = $true
  }
}

# Check scheduler
$schedulerAlive = Test-SchedulerAlive

# Get stale warnings
$staleWarnings = Get-StaleWarnings -ProjRoot $ProjectRoot
foreach ($w in $staleWarnings) { Write-Host $w }

# Make decision
$decision = Get-PreflightDecision -MasterStatus $masterStatus -HasActive $hasActive `
  -Supervised $supervised -SchedulerAlive $schedulerAlive

if ($decision.promote) {
  Write-Host ""
  Write-Host "Queued master found with no active master — promoting..."
  $promoted = Invoke-Promote -ProjectRoot $ProjectRoot
  Write-Host ($promoted | ConvertTo-Json -Compress)
}

if ($decision.block) {
  Write-Host ""
  Write-Host "PREFLIGHT FAILED: $($decision.reason)" -ForegroundColor Red
  exit 1
}

Write-Host "Preflight passed."
exit 0
