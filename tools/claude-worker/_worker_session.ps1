# Shared helper — dot-source from claude-worker.ps1 and bootstrap.ps1.
#
# Provides sentinel-based worker session tracking that survives PID reuse.
# The sentinel is a simple key=value text file (no JSON parser needed):
#
#   pid=82004
#   start=2026-06-06T05:46:49.1234567+08:00
#   kind=claude-worker
#
# Legacy bare-integer PID files are handled gracefully (conservative liveness).

function Write-WorkerSentinel {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [string]$PidFile
  )

  $proc = Get-Process -Id $PID -ErrorAction SilentlyContinue
  if (-not $proc) {
    Write-Warning "could not resolve current process StartTime; sentinel will lack start time."
    $startTime = ""
  } else {
    $startTime = $proc.StartTime.ToString("o")
  }

  $lines = @("pid=$PID", "start=$startTime", "kind=claude-worker")
  try {
    Set-Content -LiteralPath $PidFile -Value ($lines -join "`n") -Encoding ascii -NoNewline
  } catch {
    Write-Warning "could not write worker sentinel: $PidFile"
    Write-Warning "provider-switch guardrails may not detect this running session."
  }
}

function Remove-WorkerSentinel {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [string]$PidFile
  )

  if (Test-Path -LiteralPath $PidFile) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  }
}

function Test-WorkerSessionActive {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [string]$PidFile
  )

  if (-not (Test-Path -LiteralPath $PidFile)) {
    return $false
  }

  $content = $null
  try {
    $content = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction Stop).Trim()
  } catch {
    return $false
  }

  if ([string]::IsNullOrEmpty($content)) {
    return $false
  }

  # Parse key=value sentinel
  $targetPid = $null
  $startTime = $null
  $isKeyValue = $false

  foreach ($line in ($content -split "`n")) {
    $line = $line.Trim()
    if ($line -match '^pid=(.+)$') {
      $targetPid = [int]$Matches[1].Trim()
      $isKeyValue = $true
    } elseif ($line -match '^start=(.+)$') {
      $startTime = $Matches[1].Trim()
    }
  }

  # Legacy bare-integer file: no "start" line → conservative (alive = active)
  if (-not $isKeyValue) {
    try {
      $targetPid = [int]$content
    } catch {
      return $false
    }
    $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    return ($null -ne $proc)
  }

  if (-not $targetPid) {
    return $false
  }

  $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
  if (-not $proc) {
    return $false
  }

  # Key=value sentinel: active iff start time matches
  if ([string]::IsNullOrEmpty($startTime)) {
    # No start time recorded → cannot verify identity → conservative: treat as active
    return $true
  }

  try {
    $actualStart = $proc.StartTime.ToString("o")
    return ($actualStart -eq $startTime)
  } catch {
    # Could not read StartTime (permission issue, etc.) → conservative: treat as active
    return $true
  }
}
