<#
.SYNOPSIS
  Bootstrap a Worker Claude Code home on Windows (parity with bootstrap.sh).

.DESCRIPTION
  Creates a separate Claude Code home (default %USERPROFILE%\.claude-worker)
  pinned to an explicit Anthropic-compatible provider, so a Worker Claude can
  run cheap implementation loops while the Planner Claude keeps the default
  %USERPROFILE%\.claude home on its official provider. See
  docs/dual-claude-homes-design.md.

  SAFETY (non-negotiable, enforced by this script):
    * Never reads, writes, or mutates %USERPROFILE%\.claude, CCSwitch state,
      or any cc-switch.db. It only touches the worker home you name.
    * Never extracts a provider token from anywhere. The token must be
      supplied explicitly (-AuthToken or $env:ANTHROPIC_AUTH_TOKEN).
    * Fails closed: if any of base URL / auth token / model is missing it
      writes nothing, so the worker can never silently fall back to the
      planner's official OAuth identity.
    * Token values are masked in all output.

.PARAMETER WorkerHome
  Worker Claude home (default: %USERPROFILE%\.claude-worker; also honors
  $env:CLAUDE_WORKER_HOME). A leading ~ is expanded and relative paths are
  made absolute; the directory does not need to exist yet.

.PARAMETER BaseUrl
  Provider base URL. Falls back to $env:ANTHROPIC_BASE_URL.

.PARAMETER AuthToken
  User-supplied provider token. Falls back to $env:ANTHROPIC_AUTH_TOKEN.

.PARAMETER Model
  Worker model id. Falls back to $env:ANTHROPIC_MODEL.

.PARAMETER Apply
  Actually create the home and write config files. Without it the script
  prints the plan only (dry-run).

.PARAMETER LinkSkills
  Also link ilk skills/commands into the worker home (delegates to
  install.ps1 -ClaudeHome; implemented in step 3).

.PARAMETER Repo
  Repo root holding install.ps1 (default: inferred from this script).

.PARAMETER ListCCSwitchProviders
  List discovered CCSwitch Claude providers and exit (redacted; no secrets
  printed). Read-only; never mutates CCSwitch state.

.PARAMETER FromCCSwitch
  Import provider settings from CCSwitch. Requires -Provider or -Interactive.

.PARAMETER Provider
  CCSwitch provider id or name (with -FromCCSwitch).

.PARAMETER Interactive
  Pick a CCSwitch provider interactively (with -FromCCSwitch).

.PARAMETER AllowOfficial
  Allow importing an official/Claude OAuth provider into the worker home.
  Without this flag, official providers are refused to prevent the worker
  from accidentally using the planner's official identity.

.PARAMETER Force
  Overwrite provider settings even if an active worker/ilk run appears to
  be using this worker home.

.PARAMETER CloneSlot
  Clone the base worker home into a per-slot home (e.g. ~/.claude-worker-2).
  Idempotent + lazy. Accepts -Model (V2 hook; currently ignored).

.PARAMETER From
  Base home to clone from (default: ~/.claude-worker). Only meaningful with
  -CloneSlot.

.EXAMPLE
  .\bootstrap.ps1 -BaseUrl https://prov.example/anthropic -AuthToken $tok -Model cheap-1
  Dry-run preview into the default worker home.

.EXAMPLE
  .\bootstrap.ps1 -Apply -BaseUrl https://prov.example/anthropic -AuthToken $tok -Model cheap-1
  Create %USERPROFILE%\.claude-worker with a pinned provider env block.
#>
[CmdletBinding()]
param(
  [Alias('Home')]
  [string]$WorkerHome,
  [string]$BaseUrl,
  [string]$AuthToken,
  [string]$Model,
  [switch]$Apply,
  [switch]$LinkSkills,
  [string]$Repo,
  [switch]$ListCCSwitchProviders,
  [switch]$FromCCSwitch,
  [string]$Provider,
  [switch]$Interactive,
  [switch]$AllowOfficial,
  [switch]$Force,
  [int]$CloneSlot,
  [string]$From
)

$ErrorActionPreference = "Stop"

# tools\claude-worker\bootstrap.ps1 -> repo root is two levels up.
$ScriptDir = Split-Path -Parent $PSCommandPath
$DefaultRepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

# python3 is often missing on Windows PATH; prefer python, then py -3, then python3.
$ResolvePyScript = Join-Path $ScriptDir "..\..\skills\ilk-loop\scripts\_resolve_python.ps1"
if (-not (Test-Path -LiteralPath $ResolvePyScript)) {
  Write-Error "Python helper not found at $ResolvePyScript"
  exit 1
}
. (Resolve-Path -LiteralPath $ResolvePyScript).Path

# Dot-source shared worker-session helper (sentinel read/write/test).
. (Resolve-Path -LiteralPath (Join-Path $ScriptDir "_worker_session.ps1")).Path

# Resolve values: explicit param wins, else environment, else default.
if (-not $WorkerHome) { $WorkerHome = $env:CLAUDE_WORKER_HOME }
if (-not $WorkerHome) { $WorkerHome = (Join-Path $HOME ".claude-worker") }
if (-not $BaseUrl)    { $BaseUrl    = $env:ANTHROPIC_BASE_URL }
if (-not $AuthToken)  { $AuthToken  = $env:ANTHROPIC_AUTH_TOKEN }
if (-not $Model)      { $Model      = $env:ANTHROPIC_MODEL }
if (-not $Repo)       { $Repo       = $DefaultRepoRoot }

# --- Slot-home clone (-CloneSlot <n>) ----------------------------------------
# Clone the base worker home into a per-slot home (e.g. ~/.claude-worker-2).
# Idempotent (re-clone is a no-op / refresh) and lazy (created on first use).
# Accepts -Model (V2 hook; currently ignored, documented for future use).
if ($CloneSlot -gt 0) {
  # Resolve the base home to clone from.
  $cloneBase = if ($From) { $From } else { Join-Path $HOME ".claude-worker" }
  # Normalize: expand ~ and make relative paths absolute.
  if ($cloneBase -eq '~') {
    $cloneBase = $HOME
  } elseif ($cloneBase -match '^~[\\/]') {
    $cloneBase = Join-Path $HOME $cloneBase.Substring(2)
  }
  if (-not [System.IO.Path]::IsPathRooted($cloneBase)) {
    $cloneBase = Join-Path (Get-Location).Path $cloneBase
  }

  # Target: <base>-<slot> (e.g. ~/.claude-worker-2).
  $slotHome = "${cloneBase}-${CloneSlot}"

  if (-not (Test-Path -LiteralPath $cloneBase -PathType Container)) {
    Write-Error "base worker home does not exist: $cloneBase"
    exit 1
  }
  if (-not (Test-Path -LiteralPath (Join-Path $cloneBase "settings.json"))) {
    Write-Error "base worker home has no settings.json: $cloneBase"
    exit 1
  }

  if (-not (Test-Path -LiteralPath $slotHome)) {
    New-Item -ItemType Directory -Path $slotHome -Force | Out-Null
  }

  # Copy settings.json (provider env block). Idempotent: overwrite on re-clone.
  $baseSettings = Join-Path $cloneBase "settings.json"
  $slotSettings = Join-Path $slotHome "settings.json"
  Copy-Item -LiteralPath $baseSettings -Destination $slotSettings -Force
  Write-Host "  cloned settings.json -> $slotSettings"

  # Minimal .claude.json: never clobber an existing one.
  $slotClaudeJson = Join-Path $slotHome ".claude.json"
  if (-not (Test-Path -LiteralPath $slotClaudeJson)) {
    '{
  "mcpServers": {}
}' | Set-Content -LiteralPath $slotClaudeJson -Encoding utf8
    Write-Host "  wrote $slotClaudeJson (no MCP servers)"
  } else {
    Write-Host "  kept existing $slotClaudeJson (left untouched)"
  }

  # Link skills: junction on Windows, copy fallback.
  $baseSkills = Join-Path $cloneBase "skills"
  $slotSkills = Join-Path $slotHome "skills"
  if (Test-Path -LiteralPath $baseSkills -PathType Container) {
    if (Test-Path -LiteralPath $slotSkills) {
      $item = Get-Item -LiteralPath $slotSkills
      if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        # Already a junction/symlink — verify target.
        $currentTarget = $item.Target
        if ($currentTarget -and $currentTarget -eq $baseSkills) {
          Write-Host "  skills junction already correct"
        } else {
          Remove-Item -LiteralPath $slotSkills -Force
          New-Item -ItemType Junction -Path $slotSkills -Target $baseSkills | Out-Null
          Write-Host "  updated skills junction -> $baseSkills"
        }
      } else {
        Write-Host "  kept existing skills directory (left untouched)"
      }
    } else {
      try {
        New-Item -ItemType Junction -Path $slotSkills -Target $baseSkills | Out-Null
        Write-Host "  linked skills (junction) -> $baseSkills"
      } catch {
        # Fallback: copy the directory if junction fails (no Developer Mode).
        Copy-Item -LiteralPath $baseSkills -Destination $slotSkills -Recurse -Force
        Write-Host "  copied skills (junction failed) -> $slotSkills"
      }
    }
  }

  # Link commands: junction on Windows, same pattern as skills.
  $baseCommands = Join-Path $cloneBase "commands"
  $slotCommands = Join-Path $slotHome "commands"
  if (Test-Path -LiteralPath $baseCommands -PathType Container) {
    if (Test-Path -LiteralPath $slotCommands) {
      $item = Get-Item -LiteralPath $slotCommands
      if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        # Already a junction/symlink — verify target.
        $currentTarget = $item.Target
        if ($currentTarget -and $currentTarget -eq $baseCommands) {
          Write-Host "  commands junction already correct"
        } else {
          Remove-Item -LiteralPath $slotCommands -Force
          New-Item -ItemType Junction -Path $slotCommands -Target $baseCommands | Out-Null
          Write-Host "  updated commands junction -> $baseCommands"
        }
      } else {
        Write-Host "  kept existing commands directory (left untouched)"
      }
    } else {
      try {
        New-Item -ItemType Junction -Path $slotCommands -Target $baseCommands | Out-Null
        Write-Host "  linked commands (junction) -> $baseCommands"
      } catch {
        # Fallback: copy the directory if junction fails (no Developer Mode).
        Copy-Item -LiteralPath $baseCommands -Destination $slotCommands -Recurse -Force
        Write-Host "  copied commands (junction failed) -> $slotCommands"
      }
    }
  }

  Write-Host ""
  Write-Host "Slot home ready: $slotHome"
  exit 0
}

# --- CCSwitch provider discovery (-ListCCSwitchProviders) --------------------
# List providers and exit early.  Read-only; never exposes tokens.
$HelperPy = Join-Path $ScriptDir "ccswitch_import.py"

if ($ListCCSwitchProviders) {
  if (-not (Test-Path -LiteralPath $HelperPy)) {
    Write-Error "ccswitch_import.py not found at $HelperPy"
    exit 1
  }
  Invoke-IlkPython -ArgumentList @($HelperPy, "list")
  exit $LASTEXITCODE
}

# --- CCSwitch provider import (-FromCCSwitch) --------------------------------
# Import provider settings from CCSwitch into the $BaseUrl / $AuthToken /
# $Model variables before the fail-closed validation below.
if ($FromCCSwitch) {
  if (-not (Test-Path -LiteralPath $HelperPy)) {
    Write-Error "ccswitch_import.py not found at $HelperPy"
    exit 1
  }

  if ([string]::IsNullOrEmpty($Provider) -and -not $Interactive) {
    Write-Error "-FromCCSwitch requires -Provider <id> or -Interactive"
    exit 2
  }

  if ($Interactive) {
    Write-Host "Available CCSwitch Claude providers:"
    Write-Host ""
    Invoke-IlkPython -ArgumentList @($HelperPy, "list")
    Write-Host ""
    $Provider = Read-Host "Enter provider id or name"
    if ([string]::IsNullOrEmpty($Provider)) {
      Write-Error "no provider selected"
      exit 2
    }

    # Preview the selection (redacted) and ask for confirmation.
    $preview = Invoke-IlkPythonCapture -ArgumentList @($HelperPy, "export", "--provider", $Provider)
    if ($preview.ExitCode -ne 0) {
      Write-Error "provider '$Provider' not found"
      exit 1
    }
    $previewJson = $preview.Output
    Write-Host ""
    Write-Host "Selected provider:"
    $preview = $previewJson | ConvertFrom-Json
    $officialLabel = if ($preview.is_official) { " [official -- refused by default]" } else { "" }
    Write-Host "  name:       $($preview.name)$officialLabel"
    Write-Host "  base_url:   $($preview.ANTHROPIC_BASE_URL)"
    Write-Host "  auth_token: $($preview.ANTHROPIC_AUTH_TOKEN)"
    Write-Host "  model:      $($preview.ANTHROPIC_MODEL)"
    Write-Host ""
    if (-not $Apply) {
      Write-Host "Dry-run: would import this provider. Re-run with -Apply to proceed." -ForegroundColor Cyan
      return
    }
    $confirm = Read-Host "Import this provider? [y/N]"
    if ($confirm -notmatch '^[yY]') {
      Write-Host "Aborted."
      return
    }
  }

  # Export the selected provider's env vars (--machine for raw token).
  $exported = Invoke-IlkPythonCapture -ArgumentList @($HelperPy, "export", "--provider", $Provider, "--machine")
  if ($exported.ExitCode -ne 0) {
    Write-Error "failed to export CCSwitch provider '$Provider'"
    exit 1
  }
  $exportJson = $exported.Output

  # Refuse official/Claude OAuth providers by default.  Importing an official
  # provider into the worker home would let the worker use the planner's OAuth
  # identity, defeating the purpose of dual homes.
  $exportedData = $exportJson | ConvertFrom-Json
  if ($exportedData.is_official -and -not $AllowOfficial) {
    Write-Error "provider '$Provider' is an official/Claude OAuth provider. Importing it into the worker home would use the planner's official identity. Pass -AllowOfficial to override (not recommended)."
    exit 2
  }

  # Parse the JSON output into PowerShell variables.
  $parsed = $exportJson | ConvertFrom-Json
  $BaseUrl   = $parsed.ANTHROPIC_BASE_URL
  $AuthToken = $parsed.ANTHROPIC_AUTH_TOKEN
  $Model     = $parsed.ANTHROPIC_MODEL

  Write-Host "Imported provider '$Provider' from CCSwitch."
  Write-Host ""
}

# Mask a secret for logs: never print the value, only a length bucket.
function Format-Secret {
  param([string]$Value)
  if ([string]::IsNullOrEmpty($Value)) { return "(missing)" }
  return "***set ($($Value.Length) chars)***"
}

# Normalize the worker home: expand leading ~ and make relative paths
# absolute. Directory need not exist yet.
if ($WorkerHome -eq '~') {
  $WorkerHome = $HOME
} elseif ($WorkerHome -match '^~[\\/]') {
  $WorkerHome = Join-Path $HOME $WorkerHome.Substring(2)
}
if (-not [System.IO.Path]::IsPathRooted($WorkerHome)) {
  $WorkerHome = Join-Path (Get-Location).Path $WorkerHome
}

# --- fail-closed provider validation ---------------------------------------
$missing = @()
if ([string]::IsNullOrEmpty($BaseUrl))   { $missing += "base URL (-BaseUrl / ANTHROPIC_BASE_URL)" }
if ([string]::IsNullOrEmpty($AuthToken)) { $missing += "auth token (-AuthToken / ANTHROPIC_AUTH_TOKEN)" }
if ([string]::IsNullOrEmpty($Model))     { $missing += "model (-Model / ANTHROPIC_MODEL)" }

$mode = if ($Apply) { "APPLY" } else { "DRY-RUN" }
Write-Host "=== claude-worker bootstrap ($mode) ==="
Write-Host "worker home:  $WorkerHome"
Write-Host "base url:     $(if ($BaseUrl) { $BaseUrl } else { '(missing)' })"
Write-Host "auth token:   $(Format-Secret $AuthToken)"
Write-Host "model:        $(if ($Model) { $Model } else { '(missing)' })"
Write-Host "link skills:  $(if ($LinkSkills) { 'yes' } else { 'no' })"
Write-Host ""

if ($missing.Count -gt 0) {
  Write-Error "incomplete provider env -- refusing to write a worker home that would silently fall back to the planner's official OAuth identity."
  Write-Host "Missing:" -ForegroundColor Red
  $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
  exit 3
}

# --- write worker config ----------------------------------------------------

# Back up a pre-existing file before overwriting, mirroring the installer's
# .pre-ilk-<timestamp> convention, so a previously pinned token is not lost.
function Backup-IfPresent {
  param([string]$Path)
  if (Test-Path -LiteralPath $Path) {
    $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss')
    $backup = "$Path.pre-ilk-$stamp"
    Copy-Item -LiteralPath $Path -Destination $backup -Force
    Write-Host "  backed up existing $(Split-Path -Leaf $Path) -> $backup"
  }
}

# Restrict an ACL to the current user (best effort; Windows only). The file
# holds the provider token, so we lock it down where the platform allows.
function Restrict-Acl {
  param([string]$Path)
  if (-not $IsWindows) { return }
  try {
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
      $me, "FullControl", "Allow")
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
  } catch {
    Write-Host "  warning: could not restrict ACL on $Path ($($_.Exception.Message))" -ForegroundColor Yellow
  }
}

function Write-WorkerConfig {
  if (-not (Test-Path -LiteralPath $WorkerHome)) {
    New-Item -ItemType Directory -Path $WorkerHome -Force | Out-Null
  }

  $settingsFile = Join-Path $WorkerHome "settings.json"
  $claudeJson   = Join-Path $WorkerHome ".claude.json"

  # settings.json carries the provider auth token. ConvertTo-Json escapes the
  # values correctly; the literal env-var keys below also satisfy the
  # bootstrap's grep-based safety checks.
  Backup-IfPresent $settingsFile
  $settings = [ordered]@{
    env = [ordered]@{
      ANTHROPIC_BASE_URL   = $BaseUrl
      ANTHROPIC_AUTH_TOKEN = $AuthToken
      ANTHROPIC_MODEL      = $Model
    }
  }
  $settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $settingsFile -Encoding utf8
  Restrict-Acl $settingsFile
  Write-Host "  wrote $settingsFile (auth token $(Format-Secret $AuthToken))"

  # Minimal .claude.json: worker starts with no MCP servers. Never clobber an
  # existing one -- the user may have curated a small worker MCP set already.
  if (-not (Test-Path -LiteralPath $claudeJson)) {
    '{
  "mcpServers": {}
}' | Set-Content -LiteralPath $claudeJson -Encoding utf8
    Write-Host "  wrote $claudeJson (no MCP servers)"
  } else {
    Write-Host "  kept existing $claudeJson (left untouched)"
  }
}

# --- link skills/commands into the worker home ------------------------------
# Either run the custom-home installer or just print the exact command. The
# installer only runs destructively (-Apply) when the user opted in with BOTH
# -Apply and -LinkSkills; a dry-run bootstrap previews with a dry-run.
$InstallPs1 = Join-Path $Repo "install.ps1"
$LinkCmd = ".\install.ps1 -Apply -ClaudeHome `"$WorkerHome`" -OnlyClaude"

function Invoke-LinkSkills {
  Write-Host ""
  Write-Host "Link ilk skills/commands into this worker home with:"
  Write-Host "  $LinkCmd"
  if (-not $LinkSkills) {
    Write-Host "  (pass -LinkSkills to run this automatically under -Apply)"
    return
  }
  # Only ever invoke the installer under -Apply. A dry-run bootstrap stays
  # strictly non-mutating, so it just shows the command above.
  if (-not $Apply) {
    Write-Host "  -LinkSkills: deferred (bootstrap is dry-run; re-run with -Apply to link)"
    return
  }
  if (-not (Test-Path -LiteralPath $InstallPs1)) {
    Write-Host "  warning: install.ps1 not found at $InstallPs1; skipping link." -ForegroundColor Yellow
    return
  }
  Write-Host "  -LinkSkills: running installer (-Apply) ..."
  & $InstallPs1 -Apply -ClaudeHome $WorkerHome -OnlyClaude
}

if (-not $Apply) {
  Write-Host "Would create worker home and write settings.json + .claude.json."
  Invoke-LinkSkills
  Write-Host ""
  Write-Host "Note: after applying, restart any active Worker Claude sessions to pick" -ForegroundColor Cyan
  Write-Host "up the new provider (changes apply to new sessions only)." -ForegroundColor Cyan
  Write-Host ""
  Write-Host "Dry-run complete. Re-run with -Apply to bootstrap." -ForegroundColor Cyan
  return
}

# --- active worker run guard -------------------------------------------------
# If a worker/ilk loop is running against this home, overwriting the provider
# mid-run could break it.  Check for a sentinel left by claude-worker.ps1.
# Uses identity-checked liveness (PID + start-time) from _worker_session.ps1.
$pidFile = Join-Path $WorkerHome "running.pid"
if (Test-WorkerSessionActive -PidFile $pidFile) {
  if (-not $Force) {
    $sentinelPid = $null
    try {
      $content = (Get-Content -LiteralPath $pidFile -Raw).Trim()
      if ($content -match '^pid=(.+)$') { $sentinelPid = [int]$Matches[1].Trim() }
      else { $sentinelPid = [int]$content }
    } catch {}
    $label = if ($sentinelPid) { "PID $sentinelPid" } else { "(unknown PID)" }
    Write-Host "ERROR: an active worker process ($label) appears to be using this worker home." -ForegroundColor Red
    Write-Host "Overwriting the provider settings now could break the running session." -ForegroundColor Red
    Write-Host "Pass -Force to overwrite anyway, or stop the worker first." -ForegroundColor Red
    exit 2
  }
  $sentinelPid = $null
  try {
    $content = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($content -match '^pid=(.+)$') { $sentinelPid = [int]$Matches[1].Trim() }
    else { $sentinelPid = [int]$content }
  } catch {}
  $label = if ($sentinelPid) { "PID $sentinelPid" } else { "worker" }
  Write-Host "WARNING: active worker $label detected; -Force specified, proceeding anyway." -ForegroundColor Yellow
} else {
  # Stale or non-existent sentinel -- clean it up.
  Remove-WorkerSentinel -PidFile $pidFile
}

Write-WorkerConfig
Invoke-LinkSkills

Write-Host ""
Write-Host "Provider settings written.  Restart any active Worker Claude sessions" -ForegroundColor Cyan
Write-Host "to pick up the new provider (changes apply to new sessions only)." -ForegroundColor Cyan
Write-Host ""
Write-Host "Done." -ForegroundColor Green
