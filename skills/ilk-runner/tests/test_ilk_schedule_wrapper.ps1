<#
.SYNOPSIS
  Red test: /ilk-schedule wrapper + command doc. Verifies AC-1 through AC-4
  from sub-plan 2026-06-07-ilk-schedule-command.

.NOTES
  Invoked by local_checks in sub-plan 2026-06-07-ilk-schedule-command.
  Exit 0 = green (all ACs pass), exit 1 = red (wrapper or command missing).
#>

$ErrorActionPreference = "Stop"
$failures = @()

$repoRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$wrapperPs1  = Join-Path $repoRoot "skills\ilk-runner\scripts\ilk-schedule.ps1"
$wrapperSh   = Join-Path $repoRoot "skills\ilk-runner\scripts\ilk-schedule.sh"
$commandDoc  = Join-Path $repoRoot "commands\ilk-schedule.md"
$commandsDir = Join-Path $repoRoot "commands"

# --- AC-1: ilk-schedule.ps1 -DryRun exits 0, prints scheduler.ps1 + MaxConcurrent ---
Write-Host "=== AC-1: ilk-schedule.ps1 -DryRun ==="

if (-not (Test-Path $wrapperPs1)) {
  $failures += "AC-1: wrapper script not found at $wrapperPs1"
} else {
  $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wrapperPs1 -DryRun 2>&1
  $exitCode = $LASTEXITCODE
  $outputStr = ($output | Out-String).Trim()

  if ($exitCode -ne 0) {
    $failures += "AC-1: exit code $exitCode (expected 0). Output: $outputStr"
  }
  if ($outputStr -notmatch 'scheduler\.ps1') {
    $failures += "AC-1: output does not contain 'scheduler.ps1'. Output: $outputStr"
  }
  if ($outputStr -notmatch '-MaxConcurrent') {
    $failures += "AC-1: output does not contain '-MaxConcurrent'. Output: $outputStr"
  }
  # Must NOT have spawned a real detached process (dry-run)
  if ($outputStr -match '\bspawned\b') {
    $failures += "AC-1: output says 'spawned' — a real process was launched in dry-run. Output: $outputStr"
  }
}

# --- AC-2: ilk-schedule.ps1 -MaxConcurrent 3 -PollMin 2 -DryRun threads values ---
Write-Host "=== AC-2: ilk-schedule.ps1 -MaxConcurrent 3 -PollMin 2 -DryRun ==="

if (-not (Test-Path $wrapperPs1)) {
  $failures += "AC-2: wrapper script not found (skipped)"
} else {
  $output2 = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wrapperPs1 -MaxConcurrent 3 -PollMin 2 -DryRun 2>&1
  $exitCode2 = $LASTEXITCODE
  $outputStr2 = ($output2 | Out-String).Trim()

  if ($exitCode2 -ne 0) {
    $failures += "AC-2: exit code $exitCode2 (expected 0). Output: $outputStr2"
  }
  # Must thread -MaxConcurrent 3 into the previewed command
  if ($outputStr2 -notmatch '-MaxConcurrent\s+3') {
    $failures += "AC-2: output does not contain '-MaxConcurrent 3'. Output: $outputStr2"
  }
  # Must thread -PollMin 2 into the previewed command
  if ($outputStr2 -notmatch '-PollMin\s+2') {
    $failures += "AC-2: output does not contain '-PollMin 2'. Output: $outputStr2"
  }
}

# --- AC-3: commands/ilk-schedule.md exists and references the right terms ---
Write-Host "=== AC-3: commands/ilk-schedule.md ==="

if (-not (Test-Path $commandDoc)) {
  $failures += "AC-3: command doc not found at $commandDoc"
} else {
  $docContent = Get-Content $commandDoc -Raw

  if ($docContent -notmatch 'ilk-schedule\.ps1') {
    $failures += "AC-3: command doc does not reference 'ilk-schedule.ps1'"
  }
  if ($docContent -notmatch '/ilk-run') {
    $failures += "AC-3: command doc does not reference '/ilk-run'"
  }
  if ($docContent -notmatch '/ilk-schedule') {
    $failures += "AC-3: command doc does not reference '/ilk-schedule'"
  }
}

# --- AC-4: repo commands/ listing includes ilk-schedule.md (install pickup proxy) ---
Write-Host "=== AC-4: install pickup — commands/ listing ==="

$commandsList = Get-ChildItem $commandsDir -Filter '*.md' -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty Name

if ($commandsList -notcontains 'ilk-schedule.md') {
  $failures += "AC-4: 'ilk-schedule.md' not found in commands/ directory listing"
}

# --- verdict ---
if ($failures.Count -gt 0) {
  foreach ($f in $failures) { Write-Error "FAIL: $f" }
  exit 1
}

Write-Host "PASS: /ilk-schedule wrapper + command — all ACs satisfied" -ForegroundColor Green
exit 0
