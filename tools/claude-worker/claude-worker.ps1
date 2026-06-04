<#
.SYNOPSIS
  Launch Claude Code under a Worker home on Windows (parity with claude-worker.sh).

.DESCRIPTION
  Wraps `claude` so it runs against a separate Worker Claude home (default
  %USERPROFILE%\.claude-worker) pinned to an explicit Anthropic-compatible
  provider, while the Planner Claude keeps the default %USERPROFILE%\.claude
  home on its official provider. See docs/dual-claude-homes-design.md. Create
  the worker home first with tools/claude-worker/bootstrap.ps1.

  The wrapper sets two environment variables before launching:
    CLAUDE_CONFIG_DIR  -> the worker home (selects settings.json, .claude.json)
    ILK_SKILL_HOME     -> <worker home>\skills (selects ilk skills/commands)

  SAFETY (non-negotiable, enforced by this script):
    * Never reads, writes, or mutates %USERPROFILE%\.claude, CCSwitch state,
      or any cc-switch.db. It only reads the worker home you name.
    * Fails closed: refuses to launch unless the worker home, its
      settings.json, every required ANTHROPIC_* value, and the ilk-runner
      skill are all present — so the worker can never silently fall back to
      the planner's official OAuth identity.
    * Token values are masked in all output; the raw token is never printed.

.PARAMETER WorkerHome
  Worker Claude home (default: %USERPROFILE%\.claude-worker; also honors
  $env:CLAUDE_WORKER_HOME). A leading ~ is expanded and relative paths are
  made absolute.

.PARAMETER PreflightOnly
  Run all checks, print the active worker home, and exit 0 without launching
  claude.

.PARAMETER ClaudeArgs
  Remaining arguments are forwarded to `claude` verbatim.

.EXAMPLE
  .\claude-worker.ps1 --preflight-only
  Validate the default worker home without launching.

.EXAMPLE
  .\claude-worker.ps1 /ilk-run
  Launch Claude Code under the worker home and run /ilk-run.

  Exit codes: 0 ok / preflight ok, 2 usage error, 3 incomplete provider env or
  missing worker home / settings / skills.
#>
[CmdletBinding()]
param(
  [string]$WorkerHome,
  [switch]$PreflightOnly,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ClaudeArgs
)

$ErrorActionPreference = "Stop"

# Accept the bash-style long flags too so the same muscle memory works on both
# platforms: --home <dir> and --preflight-only are pulled out of the
# pass-through args before they would reach claude.
$forward = @()
if ($ClaudeArgs) {
  for ($i = 0; $i -lt $ClaudeArgs.Count; $i++) {
    switch ($ClaudeArgs[$i]) {
      '--preflight-only' { $PreflightOnly = $true }
      '--home' {
        if ($i + 1 -ge $ClaudeArgs.Count) { Write-Error "--home requires a directory argument"; exit 2 }
        $WorkerHome = $ClaudeArgs[$i + 1]; $i++
      }
      default { $forward += $ClaudeArgs[$i] }
    }
  }
}

# Resolve worker home: explicit param wins, else environment, else default.
if (-not $WorkerHome) { $WorkerHome = $env:CLAUDE_WORKER_HOME }
if (-not $WorkerHome) { $WorkerHome = (Join-Path $HOME ".claude-worker") }

# Mask a secret for logs: never print the value, only a length bucket.
function Format-Secret {
  param([string]$Value)
  if ([string]::IsNullOrEmpty($Value)) { return "(missing)" }
  return "***set ($($Value.Length) chars)***"
}

# Normalize the worker home: expand leading ~ and make relative paths absolute.
if ($WorkerHome -eq '~') {
  $WorkerHome = $HOME
} elseif ($WorkerHome -match '^~[\\/]') {
  $WorkerHome = Join-Path $HOME $WorkerHome.Substring(2)
}
if (-not [System.IO.Path]::IsPathRooted($WorkerHome)) {
  $WorkerHome = Join-Path (Get-Location).Path $WorkerHome
}

$SkillHome    = Join-Path $WorkerHome "skills"
$SettingsFile = Join-Path $WorkerHome "settings.json"

Write-Host "=== claude-worker ==="
Write-Host "worker home:     $WorkerHome"
Write-Host "ILK_SKILL_HOME:  $SkillHome"

# --- fail-closed preflight --------------------------------------------------
# Collect every problem so the operator sees them all at once.
$problems = @()

if (-not (Test-Path -LiteralPath $WorkerHome -PathType Container)) {
  $problems += "worker home does not exist: $WorkerHome (run tools/claude-worker/bootstrap.ps1 -Apply)"
}
if (-not (Test-Path -LiteralPath $SettingsFile -PathType Leaf)) {
  $problems += "worker settings.json missing: $SettingsFile"
}

$baseUrl = ""; $authToken = ""; $model = ""
if (Test-Path -LiteralPath $SettingsFile -PathType Leaf) {
  try {
    $settings = Get-Content -LiteralPath $SettingsFile -Raw | ConvertFrom-Json
    if ($settings.env) {
      $baseUrl   = [string]$settings.env.ANTHROPIC_BASE_URL
      $authToken = [string]$settings.env.ANTHROPIC_AUTH_TOKEN
      $model     = [string]$settings.env.ANTHROPIC_MODEL
    }
  } catch {
    $problems += "could not parse $SettingsFile as JSON ($($_.Exception.Message))"
  }
}

Write-Host "base url:        $(if ($baseUrl) { $baseUrl } else { '(missing)' })"
Write-Host "auth token:      $(Format-Secret $authToken)"
Write-Host "model:           $(if ($model) { $model } else { '(missing)' })"

if ([string]::IsNullOrEmpty($baseUrl))   { $problems += "ANTHROPIC_BASE_URL missing from $SettingsFile" }
if ([string]::IsNullOrEmpty($authToken)) { $problems += "ANTHROPIC_AUTH_TOKEN missing from $SettingsFile" }
if ([string]::IsNullOrEmpty($model))     { $problems += "ANTHROPIC_MODEL missing from $SettingsFile" }

if (-not (Test-Path -LiteralPath (Join-Path $SkillHome "ilk-runner") -PathType Container)) {
  $problems += "ilk-runner skill not found at $(Join-Path $SkillHome 'ilk-runner') (run install.ps1 -ClaudeHome `"$WorkerHome`" -OnlyClaude)"
}

Write-Host ""

if ($problems.Count -gt 0) {
  Write-Error "worker preflight failed — refusing to launch a worker that would silently fall back to the planner's official OAuth identity."
  Write-Host "Problems:" -ForegroundColor Red
  $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
  exit 3
}

Write-Host "Preflight OK: worker home, provider env, and ilk-runner all present."

if ($PreflightOnly) {
  Write-Host "(--preflight-only: not launching claude)"
  exit 0
}

# --- launch -----------------------------------------------------------------
# Point Claude Code at the worker home and the ilk skill root, then hand off.
# The provider token lives in the worker settings.json (which CLAUDE_CONFIG_DIR
# points Claude Code at) — never on the command line or in this environment.
$env:CLAUDE_CONFIG_DIR = $WorkerHome
$env:ILK_SKILL_HOME    = $SkillHome

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  Write-Error "'claude' not found on PATH; cannot launch the worker."
  exit 3
}

Write-Host "Launching claude with CLAUDE_CONFIG_DIR=$WorkerHome ..."
if ($forward.Count -gt 0) {
  & claude @forward
} else {
  & claude
}
exit $LASTEXITCODE
