<#
.SYNOPSIS
  Windows system-tray monitor for ilk-loop plan execution status.

.DESCRIPTION
  Runs as a NotifyIcon (system-tray icon) that refreshes every N seconds,
  piping status_all --json through render_tray.py to paint the icon,
  tooltip, and context-menu. Mirrors the macOS xbar host (ilk.10s.sh).

.PARAMETER IntervalSec
  Refresh interval in seconds. Default: 10.

.EXAMPLE
  powershell -NoProfile -File tools/tray/ilk-tray.ps1
  Launch with default 10s refresh.

.EXAMPLE
  powershell -NoProfile -File tools/tray/ilk-tray.ps1 -IntervalSec 30
  Launch with 30s refresh.
#>
param(
  [int]$IntervalSec = 10
)

$ErrorActionPreference = "Stop"

# ── Single-instance mutex ──────────────────────────────────────────────
$mutexName = "Global\ilk-tray-monitor"
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$createdNew)
if (-not $createdNew) {
  Write-Host "ilk-tray is already running (mutex held). Exiting."
  exit 0
}

# ── PID file ───────────────────────────────────────────────────────────
# Publish our PID so /ilk-upgrade can find and bounce the tray cleanly
# (the mutex alone can't be signalled). Symmetric with scheduler.pid.
$dataDir = if ($env:ILK_DATA_DIR) { $env:ILK_DATA_DIR } else { Join-Path $HOME ".ilk-data" }
$trayPidFile = Join-Path $dataDir "tray.pid"
try {
  if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir -Force | Out-Null }
  Set-Content -LiteralPath $trayPidFile -Value $PID -Encoding ascii
} catch {}

# Ensure the mutex is released and the PID file removed on exit (normal or abnormal).
$mutexHeld = $true
function Release-Mutex {
  if ($mutexHeld) {
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
    $mutexHeld = $false
  }
  # Remove our PID file only if it still points at us (avoid clobbering a
  # successor instance that may have already taken over).
  try {
    if (Test-Path $trayPidFile) {
      $recorded = (Get-Content -LiteralPath $trayPidFile -Raw -ErrorAction SilentlyContinue).Trim()
      if ($recorded -eq "$PID") { Remove-Item -LiteralPath $trayPidFile -Force -ErrorAction SilentlyContinue }
    }
  } catch {}
}

# ── Resolve repo root from this script's location (follow symlinks) ───
$src = $PSCommandPath
while ($true) {
  $item = Get-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue
  if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    $src = $item.Target
  } else {
    break
  }
}
$ScriptDir = Split-Path -Parent $src
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)

# ── Locate python and scripts ─────────────────────────────────────────
$PYTHON = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$StatusAll = Join-Path $RepoRoot "skills\ilk-loop\scripts\status_all.py"
$RenderTray = Join-Path $ScriptDir "render_tray.py"

if (-not (Test-Path $StatusAll)) {
  throw "status_all.py not found: $StatusAll"
}
if (-not (Test-Path $RenderTray)) {
  throw "render_tray.py not found: $RenderTray"
}

# ── Load assemblies ───────────────────────────────────────────────────
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ── Create the NotifyIcon ─────────────────────────────────────────────
$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Visible = $true

# ── Tray log ───────────────────────────────────────────────────────────
$trayLogDir = Join-Path $dataDir "logs"
$trayLogFile = Join-Path $trayLogDir "ilk-tray.log"

function Write-TrayLog {
  param([string]$Message)
  try {
    if (-not (Test-Path $trayLogDir)) { New-Item -ItemType Directory -Path $trayLogDir -Force | Out-Null }
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "$ts $Message"
    # BOM-free ASCII-safe append.
    [System.IO.File]::AppendAllText($trayLogFile, "$line`n", (New-Object System.Text.UTF8Encoding($false)))
  } catch {
    # Logging failure must never crash the tray.
  }
}

# ── Tick: pipe status_all --json through render_tray, paint ───────────
function Invoke-Tick {
  try {
    # Capture status_all --json, then hand it to render_tray via a BOM-free
    # temp file + --json-from. A PowerShell native-to-native pipe
    # (python | python) does NOT reliably deliver stdin to the second
    # process on Windows, and Set-Content -Encoding utf8 (PS 5.1) prepends a
    # BOM that breaks json.load — so write UTF-8 *without* BOM and read it
    # back via --json-from. (Found in the device-manual verification pass.)
    $jsonOut = & $PYTHON $StatusAll --json 2>$null
    if (-not $jsonOut) { return }
    $jsonText = ($jsonOut -join "`n")

    $tmp = [System.IO.Path]::GetTempFileName()
    try {
      [System.IO.File]::WriteAllText($tmp, $jsonText, (New-Object System.Text.UTF8Encoding($false)))
      $viewJson = & $PYTHON $RenderTray --json-from $tmp 2>$null
    } finally {
      Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
    if (-not $viewJson) { return }

    $view = ($viewJson -join "`n") | ConvertFrom-Json

    # Log per-tick row count for diagnostics.
    $rowCount = if ($view.rows) { $view.rows.Count } else { 0 }
    Write-TrayLog "tick rows=$rowCount icon=$($view.icon_state)"

    # ── Icon (colored dot, runtime-drawn) ──
    $state = $view.icon_state
    $bmp = New-Object System.Drawing.Bitmap(16, 16)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::Transparent)

    $color = switch ($state) {
      "running"  { [System.Drawing.Color]::FromArgb(0, 180, 0) }
      "attention" { [System.Drawing.Color]::FromArgb(220, 60, 60) }
      default    { [System.Drawing.Color]::FromArgb(140, 140, 140) }
    }
    $brush = New-Object System.Drawing.SolidBrush($color)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.FillEllipse($brush, 1, 1, 14, 14)
    $brush.Dispose()
    $g.Dispose()

    $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
    $notifyIcon.Icon = $icon
    # Dispose old icon after swap to avoid handle leak.
    $oldIcon = $notifyIcon.Icon
    if ($oldIcon -and $oldIcon.Handle -ne $icon.Handle) {
      # Don't dispose the one we just set; only if it was a previous icon.
      # Actually we just set it, so $oldIcon is the *previous* icon.
      try { $oldIcon.Dispose() } catch {}
    }

    # ── Tooltip (max 127 chars, enforced by render_tray.py) ──
    $notifyIcon.Text = [string]$view.tooltip

    # ── Context menu (guarded: build fully, then swap, then dispose old) ──
    $menu = New-Object System.Windows.Forms.ContextMenuStrip

    # Per-project rows
    foreach ($row in $view.rows) {
      $item = New-Object System.Windows.Forms.ToolStripMenuItem($row.label)
      $item.Tag = $row
      $item.Add_Click({
        param($sender, $e)
        $r = $sender.Tag
        if ($r.action.kind -eq "status") {
          $projKey = $r.project_key
          # If a postmortem report is available, open it so the operator can
          # read the block reason without pasting logs.  Fall back to the
          # project log dir when no report path is present.
          $reportPath = $r.action.report_path
          if ($reportPath -and (Test-Path $reportPath)) {
            Start-Process $reportPath
          } else {
            $logDir = Join-Path $env:USERPROFILE ".ilk-data\projects\$projKey\logs"
            if (Test-Path $logDir) {
              Start-Process "explorer.exe" $logDir
            }
          }
        }
      }.GetNewClosure())
      [void]$menu.Items.Add($item)
    }

    [void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))

    # Refresh now
    $refreshItem = New-Object System.Windows.Forms.ToolStripMenuItem("Refresh now")
    $refreshItem.Add_Click({ Invoke-Tick })
    [void]$menu.Items.Add($refreshItem)

    # Open status (all-projects text view)
    $openStatusItem = New-Object System.Windows.Forms.ToolStripMenuItem("Open status")
    $openStatusItem.Add_Click({
      & $PYTHON $StatusAll --text
    })
    [void]$menu.Items.Add($openStatusItem)

    [void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))

    # Exit
    $exitItem = New-Object System.Windows.Forms.ToolStripMenuItem("Exit")
    $exitItem.Add_Click({
      $notifyIcon.Visible = $false
      Release-Mutex
      $timer.Stop()
      $timer.Dispose()
      $notifyIcon.Dispose()
      [System.Windows.Forms.Application]::Exit()
    })
    [void]$menu.Items.Add($exitItem)

    # Swap menu: assign new, then dispose old.  If anything above threw,
    # the outer catch keeps the previous menu intact.
    $oldMenu = $notifyIcon.ContextMenuStrip
    $notifyIcon.ContextMenuStrip = $menu
    if ($oldMenu) { $oldMenu.Dispose() }

  } catch {
    # On error, log to tray log and keep previous menu (do not clear it).
    Write-TrayLog "tick error: $_"
    Write-Host "ilk-tray tick error: $_"
  }
}

# ── Timer ─────────────────────────────────────────────────────────────
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = ($IntervalSec * 1000)
$timer.Add_Tick({ Invoke-Tick })

# Initial tick (immediate first paint).
Invoke-Tick
$timer.Start()

# ── Keep alive: run the WinForms message loop ─────────────────────────
try {
  [System.Windows.Forms.Application]::Run()
} finally {
  $notifyIcon.Visible = $false
  Release-Mutex
  $timer.Dispose()
  $notifyIcon.Dispose()
}
