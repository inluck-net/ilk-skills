<#
.SYNOPSIS
  Mark last-exit.json as interrupted when the stopped PID matches the
  recorded running PID. Preserves run metadata; adds stopped_by + stopped_at.

.PARAMETER RuntimeDir
  The runtime directory containing last-exit.json.

.PARAMETER StoppedPid
  The PID of the process that was stopped.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string]$RuntimeDir,
  [Parameter(Mandatory)] [int]$StoppedPid
)

$ErrorActionPreference = 'Continue'

$sentinel = Join-Path $RuntimeDir "last-exit.json"
if (-not (Test-Path $sentinel)) { return }

try {
  $data = Get-Content $sentinel -Raw | ConvertFrom-Json -ErrorAction Stop
} catch { return }

if ($data.state -ne "running") { return }
if ([int]$data.pid -ne $StoppedPid) { return }

$data.state = "interrupted"
$data.stopped_by = "ilk-stop"
$data.stopped_at = (Get-Date).ToString("o")

try {
  $tmp = "$sentinel.tmp"
  $data | ConvertTo-Json -Depth 6 | Out-File -FilePath $tmp -Encoding utf8 -NoNewline
  Move-Item -Force $tmp $sentinel
  Write-Host "Sentinel marked interrupted (pid=$StoppedPid)" -ForegroundColor Green
} catch {
  Write-Host "  ! sentinel update failed: $($_.Exception.Message)" -ForegroundColor DarkYellow
  if (Test-Path "$sentinel.tmp") { Remove-Item "$sentinel.tmp" -Force -ErrorAction SilentlyContinue }
}
