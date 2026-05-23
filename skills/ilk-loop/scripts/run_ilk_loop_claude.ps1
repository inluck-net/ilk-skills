<#
.SYNOPSIS
  Run the ilk-loop autonomously using Claude Code (`claude`) as the
  agent CLI, until all sub-plans ship, max iterations hit, or progress
  stalls.

.DESCRIPTION
  Sister script to `run_ilk_loop.ps1` (Cursor `agent` based). Each
  iteration:
    1. Calls loop_status.py to check if all sub-plans are shipped. Exit 0
       = done.
    2. Snapshots HEAD of every git repo under -ProjectPath.
    3. Runs `claude -p --dangerously-skip-permissions --output-format text "<prompt>"`
       with -ProjectPath as cwd, with a per-iteration timeout.
    4. Snapshots HEAD again, computes new commits per repo.
    5. If no new commits anywhere -> "no progress", stop.
    6. Appends a JSONL record to <LogDir>\.ilk-loop.log
       and writes per-iteration agent output to a separate file.

  Stop conditions:
    - all-shipped     : loop_status.py exits 0
    - max-iterations  : hit -MaxIterations
    - no-progress     : 3 consecutive iterations with zero new commits
    - timeout         : iteration exceeded -IterationTimeoutMin
    - already-shipped : nothing to do at start
    - budget-exhausted: claude reported running out of --max-budget-usd

  Transient upstream API errors (e.g. Kimi/MiniMax 5xx mid-stream) cause
  a non-zero claude exit code but do NOT stop the loop on their own --
  the run continues and only halts if zero-progress persists for 3 iters,
  which is the real stall signal.

.PARAMETER ProjectPath
  Project root containing docs/plans/MASTER-*.md and one or more git
  repos. claude's cwd is set to this path each iteration.

.PARAMETER MaxIterations
  Hard cap on iterations. Default 30.

.PARAMETER IterationTimeoutMin
  Per-iteration wall-clock timeout, in minutes. Default 30. When
  exceeded, the claude process tree is killed.

.PARAMETER LoopStatusScript
  Path to loop_status.py.
  Default: $HOME\.cursor\skills\ilk-loop\scripts\loop_status.py

.PARAMETER LogDir
  Where to write per-iteration logs and the JSONL summary.
  Default: $HOME\.cursor\skills\ilk-loop\logs

.PARAMETER Prompt
  The prompt sent to claude. Default invokes the /ilk slash command.

.PARAMETER MaxBudgetUsd
  Optional per-iteration --max-budget-usd cap passed to each `claude -p`
  call. claude exits early once the SDK's token-cost estimate reaches
  this amount, regardless of whether you're on a paid endpoint or a
  subscription -- it's a hard stop, not just a billing alert. Default 0
  means "no cap"; rely on -MaxIterations / -IterationTimeoutMin instead.
  Only set this if you're on a metered endpoint and want a hard $$ stop.

.PARAMETER Model
  Optional --model override. By default claude reads ANTHROPIC_MODEL
  from the environment (e.g. MiniMax-M2.7-highspeed).

.EXAMPLE
  # Smoke test on a project (subscription, no $$ cap)
  .\run_ilk_loop_claude.ps1 -ProjectPath C:\path\to\your\project -MaxIterations 1

.EXAMPLE
  # Overnight on subscription endpoint
  .\run_ilk_loop_claude.ps1 -ProjectPath C:\path\to\your\project `
      -MaxIterations 30 -IterationTimeoutMin 30

.EXAMPLE
  # Metered endpoint with $5 hard stop per iter
  .\run_ilk_loop_claude.ps1 -ProjectPath C:\path\to\your\project `
      -MaxIterations 30 -MaxBudgetUsd 5

.NOTES
  - Requires Claude Code `claude` on PATH.
  - For MiniMax (or any Anthropic-compatible endpoint) configure auth via
    User env vars: ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, ANTHROPIC_MODEL.
  - Slash commands (`/ilk`, `/ilk-plan`) and the ilk-loop skill
    must be discoverable via ~/.claude/commands and ~/.claude/skills
    respectively. See README — symlinks from ~/.cursor are recommended.
  - Streams agent output live via --output-format stream-json with
    partial-message deltas (text appears as the model types). Tool
    calls and results are summarized to one line each. Raw JSON is
    persisted to <LogFile>.jsonl for postmortem / replay.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$ProjectPath,

  [int]$MaxIterations = 30,

  [int]$IterationTimeoutMin = 30,

  [string]$LoopStatusScript = (Join-Path $HOME ".cursor\skills\ilk-loop\scripts\loop_status.py"),

  [string]$LogDir = (Join-Path $HOME ".cursor\skills\ilk-loop\logs"),

  [string]$Prompt = "/ilk please continue the active plan",

  [double]$MaxBudgetUsd = 0,

  [string]$Model = "",

  # When set, after each productive iteration the loop driver scans new
  # commit messages for [plan:<slug>#step-N] tags and runs the matching
  # sub-plan's local_checks via run_local_checks.py. Results are
  # observation-only (logged into the JSONL record); the driver does not
  # gate, revert, or stop based on outcomes — agents decide what to do.
  [switch]$RunLocalChecks,

  # Outer wall-clock cap (seconds) for local_checks invocation per
  # iteration. Helper has its own per-check timeouts; this is a
  # belt-and-suspenders cap so local_checks can never lengthen an
  # iteration unboundedly.
  [int]$LocalChecksTimeoutSec = 180,

  [string]$LocalChecksScript = (Join-Path $HOME ".cursor\skills\ilk-loop\scripts\run_local_checks.py")
)

$ErrorActionPreference = "Stop"

# ----- Pre-flight ---------------------------------------------------

if (-not (Test-Path $ProjectPath)) {
  throw "ProjectPath does not exist: $ProjectPath"
}
$ProjectPath = (Resolve-Path $ProjectPath).Path

if (-not (Test-Path $LoopStatusScript)) {
  throw "loop_status.py not found at: $LoopStatusScript"
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  throw "Claude Code 'claude' not on PATH. Install: winget install Anthropic.ClaudeCode"
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "python not on PATH (needed by loop_status.py)"
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "git not on PATH"
}

# Make sure env-stored auth/model overrides from User scope are visible
# to this process (Cursor / IDE-spawned shells sometimes have a stale env).
# Skipped entirely when ~/.claude/settings.json has an `env` block --
# that file is authoritative in CC Switch / non-Anthropic-endpoint setups,
# and copying stale User-scope vars on top would re-introduce the same
# 401 api_retry loop we're trying to avoid downstream.
$settingsHasEnv = $false
$settingsJsonPath = Join-Path $HOME ".claude\settings.json"
if (Test-Path $settingsJsonPath) {
  try {
    $settings = Get-Content $settingsJsonPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($settings.env) { $settingsHasEnv = $true }
  } catch {}
}
if (-not $settingsHasEnv) {
  foreach ($v in @('ANTHROPIC_API_KEY','ANTHROPIC_BASE_URL','ANTHROPIC_MODEL')) {
    $val = [System.Environment]::GetEnvironmentVariable($v, 'User')
    if ($val) { Set-Item "Env:$v" $val }
  }
  if (-not $env:ANTHROPIC_API_KEY) {
    Write-Warning "ANTHROPIC_API_KEY not set. claude will fall back to interactive auth."
  }
} else {
  Write-Host "Detected ~/.claude/settings.json env block -- it will be the sole auth source." -ForegroundColor DarkGray
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$RunId     = Get-Date -Format "yyyyMMdd-HHmmss"
$RunLogDir = Join-Path $LogDir "ilk-claude-$RunId"
New-Item -ItemType Directory -Path $RunLogDir -Force | Out-Null
$JsonlLog  = Join-Path $LogDir ".ilk-loop.log"

# ----- Helpers ------------------------------------------------------

function Get-GitRepos {
  param([string]$Root)
  $repos = New-Object System.Collections.Generic.List[string]
  if (Test-Path (Join-Path $Root ".git")) {
    $repos.Add($Root) | Out-Null
  }
  Get-ChildItem -Path $Root -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
    if (Test-Path (Join-Path $_.FullName ".git")) {
      $repos.Add($_.FullName) | Out-Null
    }
  }
  return ,$repos.ToArray()
}

function Get-RepoHeads {
  param([string[]]$Repos)
  $heads = @{}
  foreach ($r in $Repos) {
    $sha = & git -C $r rev-parse HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $sha) {
      $heads[$r] = $sha.Trim()
    } else {
      $heads[$r] = "(unknown)"
    }
  }
  return $heads
}

function Get-NewCommitCount {
  param([string]$Repo, [string]$Before, [string]$After)
  if ($Before -eq $After) { return 0 }
  if ($Before -eq "(unknown)" -or $After -eq "(unknown)") { return 0 }
  $count = & git -C $Repo rev-list --count "$Before..$After" 2>$null
  if ($LASTEXITCODE -eq 0) { return [int]$count }
  return 0
}

function Get-LocalCheckTargets {
  <#
    Scan new commits in $Repo from $Before..$After and pull out
    [plan:<slug>#step-<N>] tags. Returns one entry per unique slug,
    keyed to the maximum step seen (so we run checks for the latest
    step the agent claimed completion on).
  #>
  param([string]$Repo, [string]$Before, [string]$After)
  if ($Before -eq $After -or $Before -eq "(unknown)" -or $After -eq "(unknown)") {
    return @()
  }
  $msgs = & git -C $Repo log "$Before..$After" --pretty=format:"%s%n%b" 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $msgs) { return @() }
  $maxStepBySlug = @{}
  $rx = [regex]'\[plan:([^#\]]+)#step-(\d+)\]'
  foreach ($m in $rx.Matches($msgs -join "`n")) {
    $slug = $m.Groups[1].Value.Trim()
    $step = [int]$m.Groups[2].Value
    if (-not $maxStepBySlug.ContainsKey($slug) -or $maxStepBySlug[$slug] -lt $step) {
      $maxStepBySlug[$slug] = $step
    }
  }
  $targets = @()
  foreach ($k in $maxStepBySlug.Keys) {
    $targets += [PSCustomObject]@{ slug = $k; step = $maxStepBySlug[$k] }
  }
  return $targets
}

function Get-IlkRuntimeDir {
  <#
    Resolve the external runtime dir under ~/.ilk-data/projects/<key>/runtime/
    via ilk_paths.py. Returns $null if python or the helper is unavailable
    (in which case sentinel writes are silently skipped — the loop still
    runs, watchdog falls back to PID checking).
  #>
  param([string]$Project)
  $resolver = Join-Path (Split-Path $PSCommandPath -Parent) "ilk_paths.py"
  if (-not (Test-Path $resolver)) { return $null }
  try {
    $json = & python $resolver --start $Project 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { return $null }
    $obj = $json | ConvertFrom-Json -ErrorAction Stop
    if ($obj.external_runtime_dir) { return [string]$obj.external_runtime_dir }
  } catch {}
  return $null
}

function Write-IlkSentinel {
  <#
    Write last-exit.json atomically (write to temp, then move) so the
    watchdog never reads a half-written file. Best-effort: errors are
    swallowed because sentinel maintenance must never break the loop.
  #>
  param(
    [Parameter(Mandatory)] [string]$Dir,
    [Parameter(Mandatory)] [hashtable]$Data
  )
  try {
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Force -Path $Dir | Out-Null }
    $target = Join-Path $Dir "last-exit.json"
    $tmp = "$target.tmp"
    $Data | ConvertTo-Json -Depth 6 | Out-File -FilePath $tmp -Encoding utf8 -NoNewline
    Move-Item -Force $tmp $target
  } catch {
    Write-Host "  ! sentinel write failed: $($_.Exception.Message)" -ForegroundColor DarkYellow
  }
}

function Invoke-LocalChecks {
  <#
    Run run_local_checks.py for every target. Each invocation has the
    helper's own per-check timeouts; this function adds an outer wall
    clock so that misbehaving checks cannot stretch an iteration.

    Returns: array of @{ slug; step; outcome; raw } where outcome is
    one of pass | fail | error | skipped, raw is the helper's parsed
    JSON (or $null on error).
  #>
  param(
    [Parameter(Mandatory)] [string]$Project,
    [Parameter(Mandatory)] [object[]]$Targets,
    [Parameter(Mandatory)] [string]$HelperScript,
    [int]$OuterTimeoutSec = 180
  )
  $out = @()
  if (-not $Targets -or $Targets.Count -eq 0) { return $out }
  if (-not (Test-Path $HelperScript)) {
    foreach ($t in $Targets) {
      $out += [PSCustomObject]@{
        slug = $t.slug; step = $t.step; outcome = "error"; raw = $null
        error = "helper script not found at $HelperScript"
      }
    }
    return $out
  }
  $deadline = (Get-Date).AddSeconds($OuterTimeoutSec)
  foreach ($t in $Targets) {
    if ((Get-Date) -ge $deadline) {
      $out += [PSCustomObject]@{
        slug = $t.slug; step = $t.step; outcome = "skipped"; raw = $null
        error = "outer timeout reached before this target"
      }
      continue
    }
    $remainSec = [int](($deadline - (Get-Date)).TotalSeconds)
    if ($remainSec -lt 5) { $remainSec = 5 }
    $tmpOut = [IO.Path]::GetTempFileName()
    $args = @($HelperScript, "--project", $Project, "--slug", $t.slug, "--step", $t.step.ToString())
    try {
      $proc = Start-Process -FilePath "python" -ArgumentList $args `
        -NoNewWindow -PassThru -RedirectStandardOutput $tmpOut -RedirectStandardError "$tmpOut.err"
      if (-not $proc.WaitForExit($remainSec * 1000)) {
        try { $proc.Kill($true) } catch {}
        $out += [PSCustomObject]@{
          slug = $t.slug; step = $t.step; outcome = "error"; raw = $null
          error = "outer-timeout after ${remainSec}s"
        }
        continue
      }
      $exit = $proc.ExitCode
      $jsonText = ""
      if (Test-Path $tmpOut) { $jsonText = Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue }
      $parsed = $null
      try { if ($jsonText) { $parsed = $jsonText | ConvertFrom-Json -ErrorAction Stop } } catch { $parsed = $null }
      $outcome = switch ($exit) {
        0       { "pass" }
        1       { "fail" }
        default { "error" }
      }
      $out += [PSCustomObject]@{
        slug = $t.slug; step = $t.step; outcome = $outcome; exit_code = $exit; raw = $parsed
      }
    } finally {
      Remove-Item $tmpOut -ErrorAction SilentlyContinue
      Remove-Item "$tmpOut.err" -ErrorAction SilentlyContinue
    }
  }
  return $out
}

function Test-AllShipped {
  param([string]$Project)
  Push-Location $Project
  try {
    # Run in a child PS scope so $ErrorActionPreference=Stop in the
    # parent doesn't trip on python's stderr (it writes "no plans dir"
    # to stderr when exiting non-zero, which PS 7.3+ otherwise throws).
    $null = & cmd /c "python `"$LoopStatusScript`" 1>nul 2>nul"
    return ($LASTEXITCODE -eq 0)
  } finally {
    Pop-Location
  }
}

$QualityGateScripts = Join-Path $HOME ".cursor\skills\ilk-loop\scripts"

function Get-PlansDir {
  <#
    Resolves the active plans dir, preferring ~/.ilk-data over in-tree.
    Delegates to skills/ilk-loop/scripts/ilk_paths.py so the resolution
    rules stay in one place (Python is canonical). Falls back to the
    legacy in-tree walk-up if the helper is unavailable.
  #>
  param([string]$Project)
  $resolver = Join-Path (Split-Path $PSCommandPath -Parent) "ilk_paths.py"
  if (Test-Path $resolver) {
    try {
      $json = & python $resolver --start $Project 2>$null
      if ($LASTEXITCODE -eq 0 -and $json) {
        $obj = $json | ConvertFrom-Json -ErrorAction Stop
        if ($obj.resolved_plans_dir) { return [string]$obj.resolved_plans_dir }
      }
    } catch {
      # fall through to legacy lookup
    }
  }
  $cur = $Project
  while ($true) {
    $candidate = Join-Path $cur "docs\plans"
    if ((Test-Path $candidate) -and (Get-ChildItem $candidate -Filter "MASTER-*.md" -ErrorAction SilentlyContinue)) {
      return $candidate
    }
    $parent = Split-Path $cur -Parent
    if ($parent -eq $cur) { return $null }
    $cur = $parent
  }
}

function Get-SubPlanSlug {
  param([string]$SubPlanPath)
  $head = Get-Content $SubPlanPath -TotalCount 20 -ErrorAction SilentlyContinue
  $m = $head | Select-String -Pattern "^plan:\s*(.+)$" | Select-Object -First 1
  if ($m) { return $m.Matches.Groups[1].Value.Trim() }
  return [System.IO.Path]::GetFileNameWithoutExtension($SubPlanPath)
}

function Get-SubPlanCiTimeout {
  param([string]$SubPlanPath)
  $head = Get-Content $SubPlanPath -TotalCount 25 -ErrorAction SilentlyContinue
  $m = $head | Select-String -Pattern "^ci_timeout_minutes:\s*(\d+)" | Select-Object -First 1
  if ($m) { return [int]$m.Matches.Groups[1].Value }
  return 30
}

function Find-ShippedSubPlansPendingGates {
  param([string]$PlansDir)
  $shipDir = Join-Path $PlansDir "ship-reports"
  $pending = New-Object System.Collections.Generic.List[hashtable]
  Get-ChildItem $PlansDir -Filter "*.md" -File | Where-Object { $_.Name -notlike "MASTER*" } | ForEach-Object {
    $lines = Get-Content $_.FullName -TotalCount 15 -ErrorAction SilentlyContinue
    if ($lines -notmatch "status:\s*shipped") { return }
    $slug = Get-SubPlanSlug -SubPlanPath $_.FullName
    $hasReport = $false
    if (Test-Path $shipDir) {
      $hasReport = @(Get-ChildItem $shipDir -Filter "$slug-*.md" -ErrorAction SilentlyContinue).Count -gt 0
    }
    if (-not $hasReport) {
      $pending.Add(@{ Path = $_.FullName; Slug = $slug }) | Out-Null
    }
  }
  return ,$pending.ToArray()
}

function Invoke-QualityGatesForSubPlan {
  param(
    [string]$ProjectPath,
    [string]$Repo,
    [string]$SubPlanPath,
    [string]$BaseRef,
    [string]$HeadRef,
    [string]$Slug
  )

  $ts = Get-Date -Format "yyyy-MM-dd-HHmm"
  $reviewerDir = Join-Path $ProjectPath "docs\plans\reviewer-reports"
  $shipDir = Join-Path $ProjectPath "docs\plans\ship-reports"
  New-Item -ItemType Directory -Force -Path $reviewerDir, $shipDir | Out-Null
  $reviewerOut = Join-Path $reviewerDir "$Slug-$ts.md"
  $shipOut = Join-Path $shipDir "$Slug-$ts.md"
  $ciTimeout = Get-SubPlanCiTimeout -SubPlanPath $SubPlanPath

  $headSha = $HeadRef
  if ($headSha -eq "HEAD") {
    $headSha = (& git -C $Repo rev-parse HEAD).Trim()
  }

  # Gate 2 — wait for CI
  Write-Host "[gate 2] wait_ci.py ($Slug) ..." -ForegroundColor Yellow
  $ciState = "skipped"
  $ciUrl = ""
  $wcArgs = @(
    (Join-Path $QualityGateScripts "wait_ci.py"),
    "--project", $Repo,
    "--commit", $headSha,
    "--timeout", "$ciTimeout"
  )
  $wcLines = & python @wcArgs 2>&1 | ForEach-Object { "$_" }
  $wcExit = $LASTEXITCODE
  $wcLines | ForEach-Object { Write-Host $_ }
  if ($wcExit -eq 0) {
    try { $wcJson = $wcLines | Select-Object -Last 1 | ConvertFrom-Json; $ciState = $wcJson.state; if ($wcJson.ci_run_url) { $ciUrl = $wcJson.ci_run_url } } catch {}
  } elseif ($wcExit -eq 3) {
    Write-Host "[gate 2] skipped (no token / non-Gitee)" -ForegroundColor DarkYellow
    $ciState = "skipped"
  } elseif ($wcExit -eq 1) {
    return @{ Blocked = $true; Reason = "ci-failed"; ShipReport = $null }
  } elseif ($wcExit -eq 2) {
    return @{ Blocked = $true; Reason = "ci-timeout"; ShipReport = $null }
  }

  # Gate 3 — reviewer agent
  Write-Host "[gate 3] run_reviewer.py ($Slug) ..." -ForegroundColor Yellow
  $rrArgs = @(
    (Join-Path $QualityGateScripts "run_reviewer.py"),
    "--project", $Repo,
    "--sub-plan", $SubPlanPath,
    "--base", $BaseRef,
    "--head", $HeadRef,
    "--output", $reviewerOut,
    "--ci-state", $ciState,
    "--allow-same-vendor"
  )
  if ($ciUrl) { $rrArgs += @("--ci-url", $ciUrl) }
  $rrLines = & python @rrArgs 2>&1 | ForEach-Object { "$_" }
  $rrLines | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) {
    return @{ Blocked = $true; Reason = "reviewer-failed"; ShipReport = $null }
  }

  # Gate 4 — ship-report
  Write-Host "[gate 4] generate_ship_report.py ($Slug) ..." -ForegroundColor Yellow
  $gsArgs = @(
    (Join-Path $QualityGateScripts "generate_ship_report.py"),
    "--project", $Repo,
    "--sub-plan", $SubPlanPath,
    "--base", $BaseRef,
    "--head", $HeadRef,
    "--reviewer-report", $reviewerOut,
    "--ci-state", $ciState,
    "--output", $shipOut
  )
  if ($ciUrl) { $gsArgs += @("--ci-url", $ciUrl) }
  $gsLines = & python @gsArgs 2>&1 | ForEach-Object { "$_" }
  $gsLines | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) {
    return @{ Blocked = $true; Reason = "ship-report-failed"; ShipReport = $null }
  }

  $reportStatus = "YELLOW"
  if (Test-Path $shipOut) {
    $fm = Get-Content $shipOut -TotalCount 12 -ErrorAction SilentlyContinue
    $sm = $fm | Select-String -Pattern "^status:\s*(\w+)" | Select-Object -First 1
    if ($sm) { $reportStatus = $sm.Matches.Groups[1].Value.ToUpper() }
  }
  Write-Host "Ship report: $shipOut (status=$reportStatus)" -ForegroundColor Green
  if ($reportStatus -eq "RED") {
    return @{ Blocked = $true; Reason = "ship-report-red"; ShipReport = $shipOut }
  }
  return @{ Blocked = $false; Reason = $null; ShipReport = $shipOut; Status = $reportStatus }
}

function Invoke-QualityGatesIfNeeded {
  param(
    [string]$Project,
    [string[]]$Repos,
    [hashtable]$HeadsBefore,
    [hashtable]$HeadsAfter,
    [int]$TotalNew
  )
  if ($TotalNew -le 0) { return @{ Blocked = $false } }

  $plansDir = Get-PlansDir -Project $Project
  if (-not $plansDir) { return @{ Blocked = $false } }

  $pending = Find-ShippedSubPlansPendingGates -PlansDir $plansDir
  if ($pending.Count -eq 0) { return @{ Blocked = $false } }

  $repo = $Project
  if (-not (Test-Path (Join-Path $repo ".git"))) {
    $repo = ($Repos | Where-Object { $HeadsAfter[$_] -and $HeadsAfter[$_] -ne "(unknown)" } | Select-Object -First 1)
  }
  if (-not $repo) { return @{ Blocked = $false } }

  $headSha = $HeadsAfter[$repo]
  if (-not $headSha -or $headSha -eq "(unknown)") {
    $headSha = (& git -C $repo rev-parse HEAD).Trim()
  }
  $baseSha = $HeadsBefore[$repo]
  if (-not $baseSha -or $baseSha -eq "(unknown)" -or $baseSha -eq $headSha) {
    $baseSha = (& git -C $repo rev-parse "$headSha~$TotalNew").Trim()
  }

  foreach ($item in $pending) {
    Write-Host ""
    Write-Host "=== Quality gates: $($item.Slug) ===" -ForegroundColor Magenta
    $result = Invoke-QualityGatesForSubPlan `
      -ProjectPath $Project `
      -Repo $repo `
      -SubPlanPath $item.Path `
      -BaseRef $baseSha `
      -HeadRef $headSha `
      -Slug $item.Slug
    if ($result.Blocked) {
      return $result
    }
  }
  return @{ Blocked = $false }
}

function Write-JsonlRecord {
  param([hashtable]$Record)
  $json = $Record | ConvertTo-Json -Compress -Depth 10
  Add-Content -Path $JsonlLog -Value $json -Encoding utf8
}

function Format-ToolArgs {
  # One-line summary of a tool_use input block. Picks the most useful
  # field per known tool, falls back to first 1-2 string args otherwise.
  # NB: do NOT name the input parameter $Input -- that's an automatic
  # PowerShell variable and gets shadowed.
  param($ToolName, $ToolInput)
  if (-not $ToolInput) { return '' }
  switch ($ToolName) {
    'Bash' {
      $cmd = "$($ToolInput.command)"
      if ($cmd) {
        $cmd = ($cmd -replace '\s+', ' ').Trim()
        if ($cmd.Length -gt 100) { $cmd = $cmd.Substring(0,100) + '...' }
        return "`$ $cmd"
      }
    }
    'Read'      { if ($ToolInput.file_path) { return $ToolInput.file_path } }
    'Edit'      { if ($ToolInput.file_path) { return $ToolInput.file_path } }
    'Write'     { if ($ToolInput.file_path) { return $ToolInput.file_path } }
    'MultiEdit' { if ($ToolInput.file_path) { return $ToolInput.file_path } }
    'Glob'      { if ($ToolInput.pattern)   { return $ToolInput.pattern } }
    'Grep'      {
      $pat = "$($ToolInput.pattern)"
      if ($pat.Length -gt 60) { $pat = $pat.Substring(0,60) + '...' }
      $loc = if ($ToolInput.path) { " in $($ToolInput.path)" } else { '' }
      return "/$pat/$loc"
    }
    'Task'      { if ($ToolInput.description) { return $ToolInput.description } }
    'TodoWrite' {
      $n = if ($ToolInput.todos) { @($ToolInput.todos).Count } else { 0 }
      return "$n todos"
    }
  }
  $shown = @()
  foreach ($p in @($ToolInput.PSObject.Properties)) {
    if ($p.Value -is [string] -and $p.Value.Length -gt 0) {
      $v = $p.Value
      if ($v.Length -gt 60) { $v = $v.Substring(0,60) + '...' }
      $shown += "$($p.Name)=$v"
      if ($shown.Count -ge 2) { break }
    }
  }
  return ($shown -join ', ')
}

function Write-ClaudeStreamLine {
  # Renders one event from `claude --output-format stream-json
  # --include-partial-messages` to console + human log.
  #
  # Anthropic event shapes (Claude Code 2.x):
  #   {"type":"system","subtype":"init","model":..,"session_id":..,"tools":[..]}
  #   {"type":"assistant","message":{"content":[{type:text|tool_use,..}]}}   <- final per block
  #   {"type":"user","message":{"content":[{type:tool_result,content:..,is_error:..}]}}
  #   {"type":"stream_event","event":{type:content_block_delta,delta:{type:text_delta,text:..}}}
  #   {"type":"stream_event","event":{type:content_block_start,content_block:{type:tool_use,name:..,input:..}}}
  #   {"type":"result","subtype":"success","duration_ms":..,"total_cost_usd":..,"usage":{..}}
  param([string]$Line, [string]$HumanLog)

  if ([string]::IsNullOrWhiteSpace($Line)) { return }

  try { $obj = $Line | ConvertFrom-Json -ErrorAction Stop }
  catch {
    [Console]::Out.WriteLine($Line); [Console]::Out.Flush()
    try { Add-Content -LiteralPath $HumanLog -Value $Line -Encoding utf8 } catch {}
    return
  }

  $emit = $null
  $inline = $false  # if $true, write without trailing newline

  switch ($obj.type) {
    'system' {
      if ($obj.subtype -eq 'init') {
        $emit = "[init] model=$($obj.model) session=$($obj.session_id) cwd=$($obj.cwd)"
      } else {
        $emit = "[system] $($obj.subtype)"
      }
    }
    'stream_event' {
      $ev = $obj.event
      if (-not $ev) { return }
      switch ($ev.type) {
        'content_block_start' {
          # Tool args arrive in the consolidated 'assistant' event;
          # thinking content is intentionally suppressed (too noisy).
          return
        }
        'content_block_delta' {
          $d = $ev.delta
          if ($d.type -eq 'text_delta' -and $d.text) {
            $emit = $d.text; $inline = $true
          } elseif ($d.type -eq 'thinking_delta' -and $d.thinking) {
            return  # skip live thinking stream; too noisy
          } else {
            return  # input_json_delta etc.
          }
        }
        'content_block_stop' { return }
        'message_start'      { return }
        'message_delta'      { return }
        'message_stop'       { return }
        default              { return }
      }
    }
    'assistant' {
      $blocks = $obj.message.content
      foreach ($c in $blocks) {
        if ($c.type -eq 'tool_use') {
          $argSummary = Format-ToolArgs -ToolName $c.name -ToolInput $c.input
          $line2 = "`n[tool >] $($c.name)($argSummary)"
          [Console]::Out.WriteLine($line2); [Console]::Out.Flush()
          try { Add-Content -LiteralPath $HumanLog -Value $line2 -Encoding utf8 } catch {}
        }
        # text blocks already streamed via stream_event deltas
      }
      return
    }
    'user' {
      $blocks = $obj.message.content
      foreach ($c in $blocks) {
        if ($c.type -eq 'tool_result') {
          $preview = "$($c.content)"
          if ($preview -is [array]) { $preview = ($preview | ForEach-Object { "$_" }) -join ' ' }
          $preview = ($preview -replace '\s+', ' ').Trim()
          if ($preview.Length -gt 160) { $preview = $preview.Substring(0,160) + '...' }
          $tag = if ($c.is_error) { '[result !]' } else { '[result <]' }
          $line2 = "$tag $preview"
          [Console]::Out.WriteLine($line2); [Console]::Out.Flush()
          try { Add-Content -LiteralPath $HumanLog -Value $line2 -Encoding utf8 } catch {}
        }
      }
      return
    }
    'result' {
      $sec = [math]::Round($obj.duration_ms / 1000.0, 1)
      $cost = if ($null -ne $obj.total_cost_usd) { ' cost=$' + ([math]::Round($obj.total_cost_usd, 4)) } else { '' }
      $tok = ''
      if ($obj.usage) {
        $tok = " tokens(in=$($obj.usage.input_tokens) out=$($obj.usage.output_tokens))"
      }
      $emit = "`n[done] $($obj.subtype) in ${sec}s$cost$tok"
    }
    default { $emit = "[$($obj.type)]" }
  }

  if ($null -ne $emit) {
    if ($inline) {
      [Console]::Out.Write($emit); [Console]::Out.Flush()
      try { Add-Content -LiteralPath $HumanLog -Value $emit -NoNewline -Encoding utf8 } catch {}
    } else {
      [Console]::Out.WriteLine($emit); [Console]::Out.Flush()
      try { Add-Content -LiteralPath $HumanLog -Value $emit -Encoding utf8 } catch {}
    }
  }
}

function Invoke-ClaudeIteration {
  param(
    [string]$Cwd,
    [string]$LogFile,
    [string]$PromptText,
    [int]$TimeoutSec,
    [double]$BudgetUsd = 0,
    [string]$ModelOverride = ""
  )
  # Streams claude stdout line-by-line and tees to $LogFile.
  # Watchdog runspace kills the whole process tree at $TimeoutSec.
  #
  # We invoke via cmd.exe because `claude` on Windows is a launcher
  # binary that re-execs itself — calling it directly through PSI works
  # but cmd preserves the same console codepage handling we used for
  # cursor-agent and avoids surprises.

  $argList = @(
    '-p',
    '--dangerously-skip-permissions',
    '--output-format', 'stream-json',
    '--verbose',
    '--include-partial-messages'
  )
  if ($BudgetUsd -gt 0) { $argList += @('--max-budget-usd', $BudgetUsd.ToString([System.Globalization.CultureInfo]::InvariantCulture)) }
  if ($ModelOverride)   { $argList += @('--model', $ModelOverride) }
  $argList += $PromptText

  # Build a cmd /c command line. Prompt may contain spaces; quote it.
  $quoted = $argList | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"','\"') + '"' } else { $_ }
  }
  # If ~/.claude/settings.json has an `env` block (e.g. CC Switch / Kimi /
  # MiniMax / any non-Anthropic endpoint configured via settings file),
  # clear conflicting process-env vars so settings.json is the sole source
  # of auth. Cursor and some profile setups leak stale ANTHROPIC_API_KEY/
  # BASE_URL/MODEL into child processes, which claude -p picks up and uses
  # instead of settings.json, causing 401 api_retry loops.
  # ANTHROPIC_AUTH_TOKEN is preserved -- it's the canonical "non-Anthropic
  # endpoint" auth field and rarely leaks from other tools.
  $envClear = ""
  $settingsJson = Join-Path $HOME ".claude\settings.json"
  if (Test-Path $settingsJson) {
    try {
      $settings = Get-Content $settingsJson -Raw -Encoding utf8 | ConvertFrom-Json
      if ($settings.env) {
        $envClear = "set ANTHROPIC_API_KEY= && set ANTHROPIC_BASE_URL= && set ANTHROPIC_MODEL= && "
      }
    } catch {}
  }
  $cmdLine = "/c " + $envClear + "claude " + ($quoted -join ' ')

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName               = "cmd.exe"
  $psi.Arguments              = $cmdLine
  $psi.WorkingDirectory       = $Cwd
  $psi.UseShellExecute        = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError  = $true
  $psi.CreateNoWindow         = $true
  $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
  $psi.StandardErrorEncoding  = [System.Text.Encoding]::UTF8

  try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding  = [System.Text.Encoding]::UTF8
  } catch {}

  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  [void]$proc.Start()

  $watchdog = Start-Job -ArgumentList $proc.Id, $TimeoutSec -ScriptBlock {
    param($targetPid, $sec)
    Start-Sleep -Seconds $sec
    & taskkill /F /T /PID $targetPid 2>$null | Out-Null
  }

  $startTime = Get-Date
  $budgetExhausted = $false
  $claudeFinished = $false       # set true when stream-json emits {"type":"result",...}
  $claudeFinishedAt = $null      # timestamp of the result event
  $claudeFinishGraceSec = 2      # wait this long after result event for trailing lines
  $rawJsonl = "$LogFile.jsonl"
  $lines = [System.Collections.Concurrent.ConcurrentQueue[string]]::new()
  $stderrLines = [System.Collections.Concurrent.ConcurrentQueue[string]]::new()

  $stdoutHandler = {
    if ($null -ne $EventArgs.Data) { $event.MessageData.Enqueue($EventArgs.Data) }
  }
  $stderrHandler = {
    if ($null -ne $EventArgs.Data) { $event.MessageData.Enqueue($EventArgs.Data) }
  }

  $stdoutSub = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived `
    -Action $stdoutHandler -MessageData $lines
  $stderrSub = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived `
    -Action $stderrHandler -MessageData $stderrLines

  try {
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()

    while (-not $proc.HasExited -or $lines.Count -gt 0 -or $stderrLines.Count -gt 0) {
      $line = $null
      $drained = $false
      while ($lines.TryDequeue([ref]$line)) {
        try { Add-Content -LiteralPath $rawJsonl -Value $line -Encoding utf8 } catch {}
        Write-ClaudeStreamLine -Line $line -HumanLog $LogFile
        if ($line -match '(?i)max[- ]?budget|budget exhausted|budget limit reached') {
          $budgetExhausted = $true
        }
        # Detect claude's terminal result event. This is the LAST line claude
        # emits before its own process exits. But cmd.exe (our $proc) often
        # doesn't exit promptly because MCP child processes (figma, chrome-
        # devtools, playwright etc.) inherited stdio handles and keep them
        # open. We must proactively kill the tree after a brief grace, else
        # the iteration wastes wall-clock time until the watchdog fires.
        if (-not $claudeFinished -and $line -match '^\s*\{"type":"result"') {
          $claudeFinished   = $true
          $claudeFinishedAt = Get-Date
        }
        $drained = $true
      }
      $eline = $null
      while ($stderrLines.TryDequeue([ref]$eline)) {
        if (-not [string]::IsNullOrWhiteSpace($eline)) {
          [Console]::Error.WriteLine("[stderr] $eline")
          try { Add-Content -LiteralPath $LogFile -Value "[stderr] $eline" -Encoding utf8 } catch {}
          if ($eline -match '(?i)max[- ]?budget|budget exhausted|budget limit reached') {
            $budgetExhausted = $true
          }
        }
        $drained = $true
      }

      # Proactive kill: claude is done, grace expired, but $proc still alive.
      if ($claudeFinished -and -not $proc.HasExited `
          -and ((Get-Date) - $claudeFinishedAt).TotalSeconds -ge $claudeFinishGraceSec) {
        [Console]::Out.WriteLine("")
        [Console]::Out.WriteLine("  Claude finished -- reaping process tree (cmd + MCP children)")
        [Console]::Out.Flush()
        try { & taskkill /F /T /PID $proc.Id 2>&1 | Out-Null } catch {}
        # Loop continues to drain any final lines; HasExited will go true shortly.
      }

      if (-not $drained) { Start-Sleep -Milliseconds 50 }
    }

    $exitCode = $proc.ExitCode
    $elapsed = ((Get-Date) - $startTime).TotalSeconds
    $timedOut = ($elapsed -ge ($TimeoutSec - 1))

    # If we proactively killed because claude finished, treat as success
    # regardless of cmd's exit code (it'll be 1 because we taskkilled it).
    if ($claudeFinished) {
      return @{ Completed = $true; ExitCode = 0; BudgetExhausted = $budgetExhausted }
    }

    if ($timedOut -or $watchdog.State -eq 'Completed') {
      [Console]::Out.WriteLine("")
      [Console]::Out.WriteLine("  Iteration exceeded $($TimeoutSec)s -- claude process tree killed")
      [Console]::Out.Flush()
      return @{ Completed = $false; ExitCode = -1; BudgetExhausted = $budgetExhausted }
    }
    return @{ Completed = $true; ExitCode = $exitCode; BudgetExhausted = $budgetExhausted }
  } finally {
    Unregister-Event -SourceIdentifier $stdoutSub.Name -ErrorAction SilentlyContinue
    Unregister-Event -SourceIdentifier $stderrSub.Name -ErrorAction SilentlyContinue
    Remove-Job -Job $stdoutSub -Force -ErrorAction SilentlyContinue
    Remove-Job -Job $stderrSub -Force -ErrorAction SilentlyContinue

    Stop-Job   -Job $watchdog -ErrorAction SilentlyContinue
    Remove-Job -Job $watchdog -Force -ErrorAction SilentlyContinue

    # Kill cmd.exe tree (no-op if already gone)
    try { & taskkill /F /T /PID $proc.Id 2>&1 | Out-Null } catch {}

    # Belt-and-suspenders: explicitly reap any remaining descendants
    # that may have outlived cmd.exe (npx-launched MCP node procs).
    try {
      $descendants = Get-CimInstance Win32_Process `
        -Filter "ParentProcessId=$($proc.Id)" -ErrorAction SilentlyContinue
      foreach ($d in $descendants) {
        try { & taskkill /F /T /PID $d.ProcessId 2>&1 | Out-Null } catch {}
      }
    } catch {}
  }
}

# ----- Discovery ----------------------------------------------------

$repos = Get-GitRepos -Root $ProjectPath
if ($repos.Count -eq 0) {
  throw "No git repos found at or under $ProjectPath"
}

Write-Host ""
Write-Host "=== ilk-loop runner (Claude Code) ===" -ForegroundColor Cyan
Write-Host "Project:        $ProjectPath"
Write-Host "Repos found:    $($repos.Count)"
$repos | ForEach-Object { Write-Host "  - $_" }
Write-Host "Max iterations: $MaxIterations"
Write-Host "Iter timeout:   $IterationTimeoutMin min"
Write-Host "Model:          $(if ($Model) { $Model } else { $env:ANTHROPIC_MODEL + ' (from env)' })"
Write-Host "API base:       $($env:ANTHROPIC_BASE_URL)"
Write-Host "Per-iter budget: $(if ($MaxBudgetUsd -gt 0) { '$' + $MaxBudgetUsd } else { 'unlimited' })"
Write-Host "Run logs:       $RunLogDir"
Write-Host "JSONL summary:  $JsonlLog"
Write-Host ""

# ----- Sentinel setup ----------------------------------------------
# last-exit.json under ~/.ilk-data/projects/<key>/runtime/ is the
# authoritative signal the watchdog reads to decide whether the loop
# is alive, finished cleanly, or stalled. Written here at start
# (state=running) and again in the finally block at end (state=
# stop_reason). PowerShell wrapper PIDs survive past the loop's real
# exit (the launcher uses -NoExit), so PID-only watchdogs miss
# "shipped" — this sentinel is the fix.
$RuntimeDir    = Get-IlkRuntimeDir -Project $ProjectPath
$LoopStartedAt = (Get-Date).ToString("o")
$IterCounter   = 0
$stopReason    = $null

if ($RuntimeDir) {
  Write-IlkSentinel -Dir $RuntimeDir -Data @{
    state        = "running"
    pid          = $PID
    run_id       = $RunId
    started_at   = $LoopStartedAt
    project_path = $ProjectPath
    cli          = "claude"
  }
  Write-Host "Sentinel: $RuntimeDir\last-exit.json (state=running)" -ForegroundColor DarkGray
} else {
  Write-Host "Sentinel: skipped (no runtime dir resolved)" -ForegroundColor DarkYellow
}

try {

# ----- Initial check ------------------------------------------------

if (Test-AllShipped -Project $ProjectPath) {
  Write-Host "All sub-plans already shipped. Nothing to do." -ForegroundColor Green
  Write-JsonlRecord -Record @{
    run_id      = $RunId
    cli         = "claude"
    iteration   = 0
    timestamp   = (Get-Date).ToString("o")
    project     = $ProjectPath
    stop_reason = "already-shipped"
  }
  $stopReason = "already-shipped"
  return
}

# ----- Main loop ----------------------------------------------------

for ($i = 1; $i -le $MaxIterations; $i++) {
  $script:IterCounter = $i
  Write-Host ""
  Write-Host "--- Iteration $i / $MaxIterations ---" -ForegroundColor Yellow

  $iterStart    = Get-Date
  $headsBefore  = Get-RepoHeads -Repos $repos
  $iterLog      = Join-Path $RunLogDir ("iter-{0:D2}.log" -f $i)
  $timeoutSec   = $IterationTimeoutMin * 60

  $result = Invoke-ClaudeIteration `
    -Cwd $ProjectPath `
    -LogFile $iterLog `
    -PromptText $Prompt `
    -TimeoutSec $timeoutSec `
    -BudgetUsd $MaxBudgetUsd `
    -ModelOverride $Model

  $iterDurSec = [int]((Get-Date) - $iterStart).TotalSeconds
  $headsAfter = Get-RepoHeads -Repos $repos

  $newCommits = @{}
  $totalNew   = 0
  foreach ($r in $repos) {
    $count = Get-NewCommitCount -Repo $r -Before $headsBefore[$r] -After $headsAfter[$r]
    if ($count -gt 0) {
      $newCommits[$r] = $count
      $totalNew += $count
    }
  }

  Write-Host ""
  Write-Host ("  duration: {0}s  exit: {1}  new commits: {2}" -f $iterDurSec, $result.ExitCode, $totalNew) -ForegroundColor Cyan
  if ($newCommits.Count -gt 0) {
    foreach ($r in $newCommits.Keys) {
      Write-Host ("    $r : +$($newCommits[$r])")
    }
  }

  # Stall detection: only stop on REAL stalls, not transient upstream
  # hiccups. Third-party Anthropic-compatible endpoints (Kimi, MiniMax,
  # etc.) occasionally return 500/stop_sequence mid-stream; one such
  # failure should NOT end the whole batch when prior iters were
  # productive. We track consecutive zero-progress iters and only stop
  # after a configurable threshold (default 3).
  if ($null -eq $script:NoProgressStreak) { $script:NoProgressStreak = 0 }
  if ($totalNew -gt 0) { $script:NoProgressStreak = 0 }
  else                 { $script:NoProgressStreak++ }

  $iterStopReason = $null
  if (-not $result.Completed)        { $iterStopReason = "timeout" }
  elseif ($result.BudgetExhausted)   { $iterStopReason = "budget-exhausted" }
  elseif ($script:NoProgressStreak -ge 3) {
    $iterStopReason = "no-progress"  # 3 iters in a row, no commits — real stall
  }
  elseif ($result.ExitCode -ne 0) {
    # Likely a transient upstream error. Log loudly but continue; if it
    # repeats and produces no progress, the streak counter above will end
    # the loop on its own.
    Write-Host ("  ! agent exited {0} (likely transient upstream API error). Streak: {1}/3. Continuing." -f $result.ExitCode, $script:NoProgressStreak) -ForegroundColor DarkYellow
  }

  # Optional: run local_checks declared in sub-plan frontmatter / per-step
  # yaml fences. Observation only — never gates or stops the loop. Opt-in
  # via -RunLocalChecks. Targets are derived from [plan:<slug>#step-N]
  # tags in this iteration's new commit messages (highest step per slug).
  $localChecksRun = @()
  if ($RunLocalChecks -and $totalNew -gt 0) {
    $allTargets = @()
    foreach ($r in $repos) {
      if ($newCommits.ContainsKey($r) -and $newCommits[$r] -gt 0) {
        $allTargets += Get-LocalCheckTargets -Repo $r -Before $headsBefore[$r] -After $headsAfter[$r]
      }
    }
    # De-dup across repos by slug (max step wins)
    $merged = @{}
    foreach ($t in $allTargets) {
      if (-not $merged.ContainsKey($t.slug) -or $merged[$t.slug].step -lt $t.step) {
        $merged[$t.slug] = $t
      }
    }
    if ($merged.Count -gt 0) {
      $localChecksRun = Invoke-LocalChecks `
        -Project $ProjectPath `
        -Targets ($merged.Values) `
        -HelperScript $LocalChecksScript `
        -OuterTimeoutSec $LocalChecksTimeoutSec
      foreach ($r2 in $localChecksRun) {
        $tag = if ($r2.outcome -eq "pass") { "OK" } elseif ($r2.outcome -eq "fail") { "FAIL" } else { "ERR" }
        $color = if ($r2.outcome -eq "pass") { "Green" } elseif ($r2.outcome -eq "fail") { "Yellow" } else { "DarkYellow" }
        Write-Host ("  [local_checks {0}] {1} step {2} -> {3}" -f $tag, $r2.slug, $r2.step, $r2.outcome) -ForegroundColor $color
      }
    }
  }

  Write-JsonlRecord -Record @{
    run_id            = $RunId
    cli               = "claude"
    iteration         = $i
    timestamp         = (Get-Date).ToString("o")
    project           = $ProjectPath
    model             = if ($Model) { $Model } else { $env:ANTHROPIC_MODEL }
    base_url          = $env:ANTHROPIC_BASE_URL
    max_budget_usd    = $MaxBudgetUsd
    duration_sec      = $iterDurSec
    exit_code         = $result.ExitCode
    completed         = $result.Completed
    budget_exhausted  = $result.BudgetExhausted
    new_commits_total = $totalNew
    new_commits       = $newCommits
    log               = $iterLog
    stop_reason       = $iterStopReason
    local_checks      = $localChecksRun
  }

  if ($totalNew -gt 0) {
    $gateResult = Invoke-QualityGatesIfNeeded `
      -Project $ProjectPath `
      -Repos $repos `
      -HeadsBefore $headsBefore `
      -HeadsAfter $headsAfter `
      -TotalNew $totalNew
    if ($gateResult.Blocked) {
      $stopReason = $gateResult.Reason
      if ($gateResult.ShipReport) {
        Write-Host "See ship-report: $($gateResult.ShipReport)" -ForegroundColor Yellow
      }
      Write-Host "Loop stopped: quality gates ($stopReason)" -ForegroundColor Red
      break
    }
  }

  if ($iterStopReason) {
    $stopReason = $iterStopReason
    break
  }

  if (Test-AllShipped -Project $ProjectPath) {
    $stopReason = "all-shipped"
    break
  }
}

if (-not $stopReason) { $stopReason = "max-iterations" }

# ----- Final report -------------------------------------------------

Write-Host ""
Write-Host "=== Loop ended: $stopReason ===" -ForegroundColor Cyan
Write-Host "Run logs: $RunLogDir"
Write-Host "JSONL:    $JsonlLog"
Write-Host ""
Write-Host "Final loop_status:"
Push-Location $ProjectPath
try {
  # Merge stderr into stdout so the "no plans" message shows cleanly
  # without PS NativeCommandError noise.
  & cmd /c "python `"$LoopStatusScript`" 2>&1"
} finally {
  Pop-Location
}

} finally {
  # Always write the terminal sentinel — whether we exited cleanly,
  # fell out of the loop on max-iterations, or got interrupted. A
  # null $stopReason means we never hit a break path, which we treat
  # as "interrupted" (the watchdog will then read this and classify
  # via ilk-feedback).
  if ($RuntimeDir) {
    $finalReason = if ($stopReason) { $stopReason } else { "interrupted" }
    Write-IlkSentinel -Dir $RuntimeDir -Data @{
      state        = $finalReason
      pid          = $PID
      run_id       = $RunId
      started_at   = $LoopStartedAt
      ended_at     = (Get-Date).ToString("o")
      iterations   = [int]$script:IterCounter
      project_path = $ProjectPath
      cli          = "claude"
      jsonl_log    = $JsonlLog
    }
    Write-Host "Sentinel: $RuntimeDir\last-exit.json (state=$finalReason, iters=$script:IterCounter)" -ForegroundColor DarkGray
  }
}
