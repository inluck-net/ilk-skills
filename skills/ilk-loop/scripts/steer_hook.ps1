# steer_hook.ps1 — crash-safe consume of operator interjections + pause gate.
#
# Usage (sourced from run_ilk_loop_claude.ps1):
#   . (Join-Path $PSScriptRoot "steer_hook.ps1")
#   $steerResult = Invoke-SteerHook -ProjectKey $ProjectKey
#   # $steerResult.InterjectionText  — text to prepend (or $null)
#   # $steerResult.Paused            — $true if pause.flag present
#
# Contract: see ilk-pocket handoff 2026-07-01-ilk-loop-steer-hook.md
# State root: ~/.ilk-data/projects/<key>/runtime/steer/
#   inbox.md             — append-only entries (uuid + timestamp)
#   pause.flag           — presence = pause
#   inbox.consumed.jsonl — hook appends {uuid, consumed_at} per consumed entry
#
# All reads/writes are utf-8. The rename retries on Windows sharing violations.

. (Join-Path $PSScriptRoot "_ilk_data_dir.ps1")

function Invoke-SteerHook {
  param(
    [Parameter(Mandatory)][string]$ProjectKey,
    [int]$MaxRetries = 10,
    [int]$RetryDelayMs = 100
  )

  $dataDir  = Get-IlkDataDir
  $steerDir = Join-Path (Join-Path (Join-Path (Join-Path $dataDir "projects") $ProjectKey) "runtime") "steer"

  # Ensure steer dir exists
  if (-not (Test-Path $steerDir)) {
    New-Item -ItemType Directory -Path $steerDir -Force | Out-Null
  }

  $inboxPath        = Join-Path $steerDir "inbox.md"
  $processingPath   = Join-Path $steerDir "inbox.processing.md"
  $consumedPath     = Join-Path $steerDir "inbox.consumed.jsonl"
  $pausePath        = Join-Path $steerDir "pause.flag"

  $result = @{
    InterjectionText = $null
    Paused           = $false
  }

  # ── Pause gate ──────────────────────────────────────────────────────
  if (Test-Path $pausePath) {
    $result.Paused = $true
    return $result
  }

  # ── Crash recovery: reconcile leftover inbox.processing.md ─────────
  if (Test-Path $processingPath) {
    # A previous run crashed between rename and delete. Reconcile:
    # the entries in processing.md may or may not have been consumed.
    # We re-parse and inject only uuids not yet in consumed.jsonl.
    $consumedUuids = _ReadConsumedUuids -ConsumedPath $consumedPath
    $entries = _ParseInboxEntries -FilePath $processingPath
    $newText = @()
    foreach ($entry in $entries) {
      if ($entry.uuid -and -not ($consumedUuids -contains $entry.uuid)) {
        $newText += $entry.text
        _AppendConsumed -ConsumedPath $consumedPath -Uuid $entry.uuid
      }
    }
    if ($newText.Count -gt 0) {
      $result.InterjectionText = ($newText -join "`n")
    }
    # Delete the leftover processing file
    try { Remove-Item -LiteralPath $processingPath -Force -ErrorAction SilentlyContinue } catch {}
    return $result
  }

  # ── Normal path: atomic rename inbox.md → inbox.processing.md ──────
  if (-not (Test-Path $inboxPath)) {
    return $result  # nothing to consume
  }

  # Retry rename on Windows sharing violation (producer may have file open)
  $renamed = $false
  for ($attempt = 0; $attempt -lt $MaxRetries; $attempt++) {
    try {
      [System.IO.File]::Move($inboxPath, $processingPath)
      $renamed = $true
      break
    } catch [System.IO.IOException] {
      Start-Sleep -Milliseconds $RetryDelayMs
    } catch {
      # Non-IO exception — don't retry
      break
    }
  }

  if (-not $renamed) {
    # Could not rename after retries — skip this cycle (don't lose data)
    return $result
  }

  # ── Parse and inject ────────────────────────────────────────────────
  $consumedUuids = _ReadConsumedUuids -ConsumedPath $consumedPath
  $entries = _ParseInboxEntries -FilePath $processingPath
  $newText = @()
  foreach ($entry in $entries) {
    if ($entry.uuid -and -not ($consumedUuids -contains $entry.uuid)) {
      $newText += $entry.text
      _AppendConsumed -ConsumedPath $consumedPath -Uuid $entry.uuid
    }
  }

  if ($newText.Count -gt 0) {
    $result.InterjectionText = ($newText -join "`n")
  }

  # ── Delete processing file ──────────────────────────────────────────
  try { Remove-Item -LiteralPath $processingPath -Force -ErrorAction SilentlyContinue } catch {}

  return $result
}

# ── Internal helpers ──────────────────────────────────────────────────

function _ReadConsumedUuids {
  param([string]$ConsumedPath)
  $uuids = @()
  if (-not (Test-Path $ConsumedPath)) { return $uuids }
  $lines = Get-Content -LiteralPath $ConsumedPath -Encoding utf8 -ErrorAction SilentlyContinue
  foreach ($line in $lines) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    try {
      $obj = $line | ConvertFrom-Json
      if ($obj.uuid) { $uuids += $obj.uuid }
    } catch {}
  }
  return $uuids
}

function _ParseInboxEntries {
  param([string]$FilePath)
  # Each entry in inbox.md is a markdown block with a uuid and text.
  # Format:
  #   <!-- uuid: <uuid> -->
  #   <text content>
  #   ---
  #
  # Or simpler: lines with uuid markers and the text between them.
  # We parse flexibly: find uuid markers, collect text until next marker or EOF.
  $entries = @()
  if (-not (Test-Path $FilePath)) { return $entries }

  $content = Get-Content -LiteralPath $FilePath -Raw -Encoding utf8 -ErrorAction SilentlyContinue
  if ([string]::IsNullOrWhiteSpace($content)) { return $entries }

  # Split on entry separators (--- on its own line)
  $blocks = $content -split '(?m)^---\s*$'
  foreach ($block in $blocks) {
    if ([string]::IsNullOrWhiteSpace($block)) { continue }
    $uuid = $null
    $text = $block.Trim()

    # Extract uuid from comment marker
    if ($block -match '<!--\s*uuid:\s*(\S+)\s*-->') {
      $uuid = $Matches[1]
      # Remove the uuid marker line from the text
      $text = ($block -replace '<!-- uuid:.*-->\r?\n?', '').Trim()
    } elseif ($block -match 'uuid:\s*(\S+)') {
      $uuid = $Matches[1]
    }

    if ($uuid -and -not [string]::IsNullOrWhiteSpace($text)) {
      $entries += @{ uuid = $uuid; text = $text }
    }
  }
  return $entries
}

function _AppendConsumed {
  param([string]$ConsumedPath, [string]$Uuid)
  $record = @{ uuid = $Uuid; consumed_at = (Get-Date).ToString("o") } | ConvertTo-Json -Compress
  # BOM-free append (UTF8Encoding(false))
  [System.IO.File]::AppendAllText(
    $ConsumedPath,
    $record + "`n",
    (New-Object System.Text.UTF8Encoding($false))
  )
}
