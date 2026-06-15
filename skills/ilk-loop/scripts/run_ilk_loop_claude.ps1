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
  Default: <skill-root>\ilk-loop\scripts\loop_status.py

.PARAMETER LogDir
  Per-run artifact directory (iter logs, heads files).
  Default: ~/.ilk-data/projects/<key>/logs/runs/<run-id>

.PARAMETER JsonlLogPath
  Path to the stable project-level JSONL summary file.
  Default: ~/.ilk-data/projects/<key>/logs/.ilk-loop.log

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
  # Not Mandatory — when dot-sourced (tests), no params are passed.
  # Runtime guard below enforces ProjectPath when the script is run directly.
  [string]$ProjectPath = "",

  [int]$MaxIterations = 30,

  [int]$IterationTimeoutMin = 30,

  [string]$LoopStatusScript = "",

  [string]$LogDir = "",

  [string]$JsonlLogPath = "",

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

  [string]$LocalChecksScript = "",

  # Path to a JSON file with `{"mcpServers": {...}}` to pass to every
  # `claude -p` invocation via `--mcp-config <path> --strict-mcp-config`.
  # When set, the worker sees ONLY the MCPs listed here (claude.ai-synced
  # and registry-scope servers are also dropped). Used by `launch.ps1`'s
  # -DisableMcp / `worker_disable_mcp` config to skip cost-heavy MCPs
  # (e.g. chrome-devtools) on batches that don't need them.
  [string]$McpConfigPath = ""
)

$ErrorActionPreference = "Stop"

# ----- Skill root resolution ------------------------------------------
. (Join-Path $PSScriptRoot "_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

# Override defaults that were empty strings (param defaults can't call functions)
if (-not $LoopStatusScript) { $LoopStatusScript = Join-Path $SkillRoot "ilk-loop\scripts\loop_status.py" }
if (-not $LocalChecksScript){ $LocalChecksScript = Join-Path $SkillRoot "ilk-loop\scripts\run_local_checks.py" }

$RunId = Get-Date -Format "yyyyMMdd-HHmmss"

# Resolve external log paths via ilk_paths.py unless explicitly provided
$legacyLogDir = Join-Path $SkillRoot "ilk-loop\logs"
if (-not $LogDir -or -not $JsonlLogPath) {
  $extLogs = ""
  $resolver = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
  if (Test-Path $resolver) {
    try {
      $json = & python $resolver --start $ProjectPath 2>$null
      if ($LASTEXITCODE -eq 0 -and $json) {
        $obj = $json | ConvertFrom-Json -ErrorAction Stop
        if ($obj.external_logs_dir) { $extLogs = [string]$obj.external_logs_dir }
      }
    } catch {}
  }
  if (-not $LogDir) {
    $LogDir = if ($extLogs) { Join-Path $extLogs "runs\$RunId" } else { Join-Path $legacyLogDir "runs\$RunId" }
  }
  if (-not $JsonlLogPath) {
    $JsonlLogPath = if ($extLogs) { Join-Path $extLogs ".ilk-loop.log" } else { Join-Path $legacyLogDir ".ilk-loop.log" }
  }
}

# ----- Pre-flight ---------------------------------------------------
# Skip when ProjectPath is empty (dot-sourcing for function definitions).
if ($ProjectPath) {
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
}

# Make sure env-stored auth/model overrides from User scope are visible
# to this process (Cursor / IDE-spawned shells sometimes have a stale env).
# Skipped entirely when ~/.claude/settings.json has a NON-EMPTY `env`
# block -- that file is authoritative in CC Switch / non-Anthropic-endpoint
# setups, and copying stale User-scope vars on top would re-introduce the
# same 401 api_retry loop we're trying to avoid downstream.
# An empty `env: {}` (CC Switch's canonical "Claude Official" state) does
# NOT count -- we want OAuth / User-scope fallback in that case.
$settingsHasEnv = $false
$cfgDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$settingsJsonPath = Join-Path $cfgDir 'settings.json'
Write-Host "[runner] CLAUDE_CONFIG_DIR=$cfgDir" -ForegroundColor DarkGray
if (Test-Path $settingsJsonPath) {
  try {
    $settings = Get-Content $settingsJsonPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($settings.env -and @($settings.env.PSObject.Properties).Count -gt 0) {
      $settingsHasEnv = $true
    }
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
  Write-Host "Detected $settingsJsonPath env block -- it will be the sole auth source." -ForegroundColor DarkGray
}

# Only create log dirs when running directly (not dot-sourcing)
if ($LogDir) {
  New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
  $RunLogDir = $LogDir
  $JsonlLog  = $JsonlLogPath
  # Ensure JSONL parent dir exists
  $jsonlParent = Split-Path $JsonlLog -Parent
  if ($jsonlParent -and -not (Test-Path $jsonlParent)) { New-Item -ItemType Directory -Path $jsonlParent -Force | Out-Null }
}

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
    $sha = & git -C $r rev-parse --quiet --verify HEAD 2>$null
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

$QualityGateScripts = Join-Path $SkillRoot "ilk-loop\scripts"

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

function Get-SubPlanRepoName {
  # Read the `repo:` frontmatter field. Returns "" when absent. Used in
  # meta mode to route per-sub-plan commits/CI to the correct member
  # repo. In single mode the value is ignored.
  param([string]$SubPlanPath)
  $head = Get-Content $SubPlanPath -TotalCount 25 -ErrorAction SilentlyContinue
  $m = $head | Select-String -Pattern "^repo:\s*(.+)$" | Select-Object -First 1
  if ($m) { return $m.Matches.Groups[1].Value.Trim() }
  return ""
}

# Cached lookup of (kind, members) for the active project. Populated on
# first use per loop process; cleared via $script:_MetaInfo = $null in
# tests if needed. Avoids spawning python per sub-plan.
$script:_MetaInfo = $null

function Get-MetaInfo {
  param([string]$Project)
  if ($script:_MetaInfo -and $script:_MetaInfo.Project -eq $Project) {
    return $script:_MetaInfo
  }
  $info = [PSCustomObject]@{
    Project = $Project
    Kind    = "single"
    Members = @{}  # name -> absolute path
  }
  $resolver = Join-Path (Split-Path $PSCommandPath -Parent) "ilk_paths.py"
  if (Test-Path $resolver) {
    try {
      $json = & python $resolver --start $Project 2>$null
      if ($LASTEXITCODE -eq 0 -and $json) {
        $obj = $json | ConvertFrom-Json -ErrorAction Stop
        if ($obj.project_kind) { $info.Kind = [string]$obj.project_kind }
        if ($obj.meta_members) {
          foreach ($m in $obj.meta_members) {
            $info.Members[[string]$m.name] = [string]$m.path
          }
        }
      }
    } catch {
      # Fall through to defaults; meta resolution is best-effort.
    }
  }
  $script:_MetaInfo = $info
  return $info
}

function Resolve-SubPlanRepoDir {
  <#
    Returns the absolute working directory to use for git operations
    (CI wait, reviewer, ship-report) targeting $SubPlanPath.

    - Single mode: returns $Project unchanged.
    - Meta mode + valid `repo:` declared: returns the member's path.
    - Meta mode + missing/unknown `repo:`: returns "" and logs a warning;
      the caller MUST handle the empty string (we don't want to silently
      run gates against the wrong repo).
  #>
  param([string]$Project, [string]$SubPlanPath)
  $info = Get-MetaInfo -Project $Project
  if ($info.Kind -ne "meta") { return $Project }
  $repoName = Get-SubPlanRepoName -SubPlanPath $SubPlanPath
  if (-not $repoName) {
    Write-Host ("  ! meta project sub-plan {0} is missing `repo:` frontmatter — skipping gates" -f (Split-Path -Leaf $SubPlanPath)) -ForegroundColor DarkYellow
    return ""
  }
  if (-not $info.Members.ContainsKey($repoName)) {
    $known = ($info.Members.Keys | Sort-Object) -join ", "
    Write-Host ("  ! sub-plan {0} declares repo={1} which is not in .ilk-meta.json (known: {2}) — skipping gates" -f (Split-Path -Leaf $SubPlanPath), $repoName, $known) -ForegroundColor DarkYellow
    return ""
  }
  return $info.Members[$repoName]
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
  # Reports live next to the active plans dir. In meta mode that's the
  # external dir at ~/.ilk-data/projects/<meta-key>/plans/, so reports
  # never leak into any member sub-repo's working tree. In legacy
  # single-repo mode (in-tree plans) the reports stay in-tree as before.
  $reportsBase = Get-PlansDir -Project $ProjectPath
  if (-not $reportsBase) {
    $reportsBase = Join-Path $ProjectPath "docs\plans"
  }
  $reviewerDir = Join-Path $reportsBase "reviewer-reports"
  $shipDir = Join-Path $reportsBase "ship-reports"
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
    [int]$TotalNew,
    [hashtable]$NewCommits = $null
  )
  if ($TotalNew -le 0) { return @{ Blocked = $false } }

  $plansDir = Get-PlansDir -Project $Project
  if (-not $plansDir) { return @{ Blocked = $false } }

  $pending = Find-ShippedSubPlansPendingGates -PlansDir $plansDir
  if ($pending.Count -eq 0) { return @{ Blocked = $false } }

  $info = Get-MetaInfo -Project $Project
  $isMeta = ($info.Kind -eq "meta")

  # Single-mode default: pick the repo that owns this project. In meta
  # mode each sub-plan resolves its own repo from frontmatter, so this
  # fallback is only used as a safety net.
  $defaultRepo = $Project
  if (-not (Test-Path (Join-Path $defaultRepo ".git"))) {
    $defaultRepo = ($Repos | Where-Object { $HeadsAfter[$_] -and $HeadsAfter[$_] -ne "(unknown)" } | Select-Object -First 1)
  }

  foreach ($item in $pending) {
    Write-Host ""
    Write-Host "=== Quality gates: $($item.Slug) ===" -ForegroundColor Magenta

    if ($isMeta) {
      $repo = Resolve-SubPlanRepoDir -Project $Project -SubPlanPath $item.Path
      if (-not $repo) {
        # Resolution failed; helper already emitted a warning. Skip this
        # sub-plan's gates rather than running them against the wrong
        # repo — that would produce a misleading ship-report.
        continue
      }
    } else {
      $repo = $defaultRepo
    }
    if (-not $repo) { continue }

    $headSha = $HeadsAfter[$repo]
    if (-not $headSha -or $headSha -eq "(unknown)") {
      $headSha = (& git -C $repo rev-parse HEAD).Trim()
    }
    $baseSha = $HeadsBefore[$repo]
    $newInRepo = 1
    if ($NewCommits -and $NewCommits.ContainsKey($repo)) {
      $newInRepo = [int]$NewCommits[$repo]
    } elseif (-not $NewCommits) {
      # Legacy single-repo call site without -NewCommits: fall back to
      # $TotalNew (correct only when there's exactly one repo with new
      # commits, which is the single-repo case).
      $newInRepo = $TotalNew
    }
    if (-not $baseSha -or $baseSha -eq "(unknown)" -or $baseSha -eq $headSha) {
      $baseSha = (& git -C $repo rev-parse "$headSha~$newInRepo").Trim()
    }

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
  if ($script:McpConfigPath) {
    $argList += @('--mcp-config', $script:McpConfigPath, '--strict-mcp-config')
  }
  $argList += $PromptText

  # Build a cmd /c command line. Prompt may contain spaces; quote it.
  $quoted = $argList | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"','\"') + '"' } else { $_ }
  }
  # If ~/.claude/settings.json has a NON-EMPTY `env` block (e.g. CC Switch
  # currently routed to Kimi / MiniMax / any non-Anthropic endpoint), clear
  # conflicting process-env vars so settings.json is the sole source of
  # auth. Cursor and some profile setups leak stale ANTHROPIC_API_KEY/
  # BASE_URL/MODEL into child processes, which claude -p picks up and uses
  # instead of settings.json, causing 401 api_retry loops.
  # An empty `env: {}` (CC Switch's "Claude Official" state) is NOT
  # authoritative -- skip the clear so claude can find its OAuth token.
  # ANTHROPIC_AUTH_TOKEN is preserved -- it's the canonical "non-Anthropic
  # endpoint" auth field and rarely leaks from other tools.
  $envClear = ""
  $settingsJson = Join-Path $HOME ".claude\settings.json"
  if (Test-Path $settingsJson) {
    try {
      $settings = Get-Content $settingsJson -Raw -Encoding utf8 | ConvertFrom-Json
      if ($settings.env -and @($settings.env.PSObject.Properties).Count -gt 0) {
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
        # Only the terminal result's terminal_reason field is authoritative.
        # Any phrase-based check (e.g. "budget exhausted") fires on agent
        # thinking/output that *mentions* budget concepts — never use phrases.
        if ($line -match '"terminal_reason"\s*:\s*"budget_exhausted"') {
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
          if ($eline -match '"terminal_reason"\s*:\s*"budget_exhausted"') {
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

# ----- Branch setup (Gaps 2+3) ------------------------------------------

$script:BranchCreateFrom = ""
$script:BranchName = ""
$script:BranchMergeBack = $false

function Parse-MasterBranchBlock {
  <#
    Parse the branch: block from the active MASTER plan's YAML frontmatter.
    Sets $script:BranchCreateFrom, $script:BranchName, $script:BranchMergeBack.
    No-op (all stay empty/default) when no branch: block exists.
  #>
  param([string]$Project)
  $resolver = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
  if (-not (Test-Path $resolver)) { return }

  try {
    $json = & python $resolver --start $Project 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { return }
    $obj = $json | ConvertFrom-Json -ErrorAction Stop
    $plansDir = [string]$obj.external_plans_dir
    if (-not $plansDir -or -not (Test-Path $plansDir)) { return }
  } catch { return }

  # Resolve the ACTIVE master the same way the loop does — via loop_status.py
  # (registry/queue order), NOT filesystem-enumeration order. Get-ChildItem |
  # Select-Object -First 1 returns an arbitrary master and diverges from the
  # executing one whenever a project has >1 master, so branch setup would read a
  # stale, possibly-shipped master's branch block (handoff bug #1).
  $masterName = ""
  Push-Location $Project
  try {
    $statusJson = & python $LoopStatusScript --json 2>$null
  } finally {
    Pop-Location
  }
  if ($statusJson) {
    try {
      $statusObj = ($statusJson -join "`n") | ConvertFrom-Json -ErrorAction Stop
      $masterName = [string]$statusObj.master
      $statusPlansDir = [string]$statusObj.plans_dir
      # Prefer loop_status.py's plans_dir so master + dir come from one source.
      if ($statusPlansDir -and (Test-Path $statusPlansDir)) { $plansDir = $statusPlansDir }
    } catch { $masterName = "" }
  }
  if (-not $masterName) { return }
  $masterFile = Get-Item -LiteralPath (Join-Path $plansDir $masterName) -ErrorAction SilentlyContinue
  if (-not $masterFile) { return }

  # Parse branch: block via the standalone parser script. A real .py file
  # avoids the two failure modes the old inline `python -c @"..."@` here-string
  # hit on zh-CN Windows: (1) locale-encoding (GBK) crash reading UTF-8 masters,
  # and (2) the expandable here-string mangling the embedded quotes in
  # strip('"').strip("'") into a SyntaxError. See memory inline-python-open-needs-utf8.
  $branchScript = Join-Path $PSScriptRoot 'parse_branch_block.py'
  $parsed = & python $branchScript $masterFile.FullName 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $parsed) { return }

  try { $branchObj = $parsed | ConvertFrom-Json -ErrorAction Stop } catch { return }
  if (-not $branchObj -or -not $branchObj.create_from) {
    # Check if at least 'name' is present
    if (-not $branchObj -or -not $branchObj.name) { return }
  }

  $script:BranchCreateFrom = if ($branchObj.create_from) { [string]$branchObj.create_from } else { "" }
  $script:BranchName = if ($branchObj.name) { [string]$branchObj.name } else { "" }
  $script:BranchMergeBack = if ($branchObj.merge_back -eq $true) { $true } else { $false }

  # Default create_from to HEAD if branch block exists but create_from is missing
  if ($script:BranchName -and -not $script:BranchCreateFrom) {
    $script:BranchCreateFrom = "HEAD"
  }

  if ($script:BranchName) {
    Write-Host "[runner] branch block parsed: create_from=$($script:BranchCreateFrom) name=$($script:BranchName) merge_back=$($script:BranchMergeBack)"
  }
}

function Ensure-FreshBaseRef {
  <#
    Compares the local remote-tracking ref (<remote>/<branch>) against the true
    remote tip via `git ls-remote`. On mismatch, force-refreshes the local ref.
    Returns $true on success, throws on failure.
  #>
  param([string]$Remote, [string]$Branch, [string]$Repo)

  Write-Host "[runner] freshness preflight: ${Remote}/${Branch}"

  # 1. Get the local remote-tracking ref SHA
  $localSha = ""
  try { $localSha = (& git -C $Repo rev-parse "refs/remotes/${Remote}/${Branch}" 2>$null).Trim() } catch {}
  if (-not $localSha) {
    Write-Host "[runner]   local ref refs/remotes/${Remote}/${Branch} not found (will fetch)"
    $localSha = "(none)"
  } else {
    Write-Host "[runner]   local  $($localSha.Substring(0, [Math]::Min(12, $localSha.Length)))"
  }

  # 2. Get the true remote tip via ls-remote
  $lsOutput = ""
  try { $lsOutput = (& git -C $Repo ls-remote $Remote "refs/heads/${Branch}" 2>$null) } catch {}
  if ($LASTEXITCODE -ne 0) {
    throw "Error: git ls-remote ${Remote} refs/heads/${Branch} failed. Check that the remote is reachable."
  }
  if (-not $lsOutput) {
    throw "Error: branch '${Branch}' not found on remote '${Remote}'. ls-remote returned empty."
  }

  $remoteSha = ($lsOutput | Select-Object -First 1).Split("`t")[0]
  Write-Host "[runner]   remote $($remoteSha.Substring(0, [Math]::Min(12, $remoteSha.Length)))"

  # 3. Compare — if they match, done
  if ($localSha -eq $remoteSha) {
    Write-Host "[runner]   OK — local ref is up to date"
    return $true
  }

  # 4. Mismatch — force-refresh
  Write-Host "[runner]   STALE — local $($localSha.Substring(0, [Math]::Min(12, $localSha.Length))) != remote $($remoteSha.Substring(0, [Math]::Min(12, $remoteSha.Length)))"
  Write-Host "[runner]   force-refreshing ${Remote}/${Branch}..."

  & git -C $Repo fetch $Remote "${Branch}:refs/remotes/${Remote}/${Branch}" 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Error: force-refresh fetch failed for ${Remote}/${Branch}."
  }

  # Verify the refresh took effect
  $refreshedSha = ""
  try { $refreshedSha = (& git -C $Repo rev-parse "refs/remotes/${Remote}/${Branch}" 2>$null).Trim() } catch {}
  if ($refreshedSha -eq $remoteSha) {
    Write-Host "[runner]   refreshed OK — now at $($refreshedSha.Substring(0, [Math]::Min(12, $refreshedSha.Length)))"
    return $true
  } else {
    throw "Error: after fetch, local ref is $refreshedSha but expected $remoteSha."
  }
}

# ----- Remote classification (Gap 5) ------------------------------------------
#
# Classify-Remote -Remote REMOTE -Repos REPOS
#
# Classifies a git remote as "shared" or "personal" based on its URL.
# Used to decide whether commit trailers ([plan:…#step-N]) should be stripped.
#
# Heuristic:
#   - Personal: remote URL contains a personal namespace pattern
#     (e.g. inluck-net/*, github.com/inluck-net/*, gitee.com/inluck-net/*)
#   - Shared: everything else (organization repos, team repos, public repos)
#   - Default: "shared" when unsure (safer to strip trailers on shared repos)
#
# Returns: "shared" or "personal"

function Classify-Remote {
  param(
    [string]$Remote,
    [string[]]$Repos
  )

  if (-not $Remote -or -not $Repos -or -not $Repos[0]) {
    return "shared"
  }

  # Get the remote URL
  $url = ""
  try {
    $url = & git -C $Repos[0] remote get-url $Remote 2>$null
  } catch { $url = "" }
  if (-not $url) {
    return "shared"
  }

  # Personal namespace patterns (case-insensitive match)
  # Matches: inluck-net/* on any host (github.com, gitee.com, gitlab.com, etc.)
  # Also matches SSH-style: git@github.com:inluck-net/*
  $lowerUrl = $url.ToLower()

  # Check for personal namespace pattern: host/username or host:username
  # Pattern: (github.com|gitee.com|gitlab.com)[/:]inluck-net/
  if ($lowerUrl -match '(github\.com|gitee\.com|gitlab\.com)[/:]inluck-net/') {
    return "personal"
  }

  # Check for generic personal pattern: any host with /inluck-net/ in path
  if ($lowerUrl -match '/inluck-net/') {
    return "personal"
  }

  # Default to shared (safer: strip trailers)
  return "shared"
}

function Setup-BranchOneRepo {
  <#
    Create/checkout $script:BranchName from $script:BranchCreateFrom in ONE repo.
    Returns:
      'branched' — branch created/checked out successfully here
      'skip'     — this repo can't host the branch (not a git repo, dirty tree,
                   or base ref missing) — non-fatal in a multi-repo project
      'fail'     — hard error (merge/rebase in progress, fetch/checkout failed)
    Decisions are made on git EXIT CODES, never on whether git wrote to stderr
    (git's normal "Switched to a new branch" goes to stderr); git output is piped
    to Out-Null so it cannot surface as a fatal NativeCommandError.
  #>
  param([string]$Repo)

  # git writes normal status (e.g. "Switched to a new branch") to STDERR; under
  # an inherited $ErrorActionPreference='Stop' that surfaces as a terminating
  # NativeCommandError and can wedge the harness. Localize to 'Continue' and
  # decide on $LASTEXITCODE only. NEVER use 2>&1 here (it wraps stderr into the
  # success stream as ErrorRecords); use 2>$null to discard git's stderr.
  $ErrorActionPreference = 'Continue'

  $gitDir = (& git -C $Repo rev-parse --git-dir 2>$null)
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ! $Repo is not a git repo — skipping branch setup there" -ForegroundColor DarkYellow
    return 'skip'
  }
  $gitDir = "$gitDir".Trim()
  if ($gitDir -and (Test-Path (Join-Path $gitDir "MERGE_HEAD"))) {
    Write-Host "Error: a merge is in progress in $Repo (abort/commit before running)." -ForegroundColor Red
    return 'fail'
  }
  if ($gitDir -and ((Test-Path (Join-Path $gitDir "rebase-merge")) -or (Test-Path (Join-Path $gitDir "rebase-apply")))) {
    Write-Host "Error: a rebase is in progress in $Repo (abort/finish before running)." -ForegroundColor Red
    return 'fail'
  }

  # Dirty tree -> skip (non-fatal): only clean repos can host the branch. In a
  # single-repo project this makes the one repo skip -> branched==0 -> Setup-Branch
  # fails, preserving the original dirty-tree guard.
  & git -C $Repo diff --quiet 2>$null;        $diffCode = $LASTEXITCODE
  & git -C $Repo diff --cached --quiet 2>$null; $cachedCode = $LASTEXITCODE
  if ($diffCode -ne 0 -or $cachedCode -ne 0) {
    Write-Host "  ! working tree dirty in $Repo — skipping branch setup there" -ForegroundColor DarkYellow
    return 'skip'
  }

  # Parse create_from into remote/branch (per-repo: depends on configured remotes).
  # Only split off a remote when the first segment is an actually-configured remote
  # (a branch name can contain slashes, e.g. codex/convex-rewrite).
  $remote = ""
  $branch = $script:BranchCreateFrom
  if ($script:BranchCreateFrom -match '^([^/]+)/(.+)$') {
    $candidate = $Matches[1]
    $configuredRemotes = @(& git -C $Repo remote 2>$null)
    if ($configuredRemotes -contains $candidate) { $remote = $candidate; $branch = $Matches[2] }
  }

  if ($remote) {
    Write-Host "[runner] fetching ${remote} ${branch} in $Repo..."
    & git -C $Repo fetch $remote $branch 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
      Write-Host "Error: git fetch ${remote} ${branch} failed in $Repo." -ForegroundColor Red
      return 'fail'
    }
    try { Ensure-FreshBaseRef -Remote $remote -Branch $branch -Repo $Repo } catch {
      Write-Host "Error: base-ref freshness check failed in ${Repo}: $_" -ForegroundColor Red
      return 'fail'
    }
  }

  # Base ref must resolve in THIS repo; if not, this repo isn't a target -> skip.
  & git -C $Repo rev-parse $script:BranchCreateFrom 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ! base ref '$($script:BranchCreateFrom)' not found in $Repo — skipping" -ForegroundColor DarkYellow
    return 'skip'
  }

  & git -C $Repo checkout -B $script:BranchName $script:BranchCreateFrom 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    # Non-zero exit may be a benign post-checkout hook failure (lefthook/husky).
    # Verify the actual outcome before aborting: if HEAD is on the target branch
    # AND HEAD SHA equals the resolved base SHA, the checkout landed despite the
    # hook noise — warn and continue.
    $actualBranch = ""
    $actualSha = ""
    $targetSha = ""
    try { $actualBranch = (& git -C $Repo rev-parse --abbrev-ref HEAD 2>$null).Trim() } catch {}
    try { $actualSha = (& git -C $Repo rev-parse HEAD 2>$null).Trim() } catch {}
    try { $targetSha = (& git -C $Repo rev-parse $script:BranchCreateFrom 2>$null).Trim() } catch {}

    if ($actualBranch -eq $script:BranchName -and $actualSha -and $actualSha -eq $targetSha) {
      Write-Host "WARNING: git checkout -B $($script:BranchName) exited non-zero in $Repo, but checkout landed despite post-checkout hook failure (HEAD is on $($script:BranchName) at $($actualSha.Substring(0, [Math]::Min(12, $actualSha.Length)))). Continuing." -ForegroundColor DarkYellow
    } else {
      Write-Host "Error: git checkout -B failed in $Repo (HEAD=$actualBranch at $($actualSha.Substring(0, [Math]::Min(12, $actualSha.Length))), expected branch=$($script:BranchName) at $($targetSha.Substring(0, [Math]::Min(12, $targetSha.Length))))." -ForegroundColor Red
      return 'fail'
    }
  }
  $cur = (& git -C $Repo branch --show-current 2>$null).Trim()
  Write-Host "[runner] $Repo now on branch: $cur"
  return 'branched'
}

function Setup-Branch {
  <#
    If the MASTER has a branch: block, create/checkout the policy branch in EVERY
    discovered repo that can host it — so each sub-plan's `repo:` target lands on
    the feat branch, not just the first repo. Repos that can't host it (not a git
    repo, dirty, or missing the base ref) are skipped with a warning; a hard git
    error (merge/rebase/checkout) fails the run, as does branching zero repos.
    No-op when $script:BranchName is empty.
  #>
  param([string[]]$Repos)

  if (-not $script:BranchName) { return $true }
  if (-not $Repos -or @($Repos).Count -eq 0) {
    Write-Host "Error: no git repo resolved for branch setup." -ForegroundColor Red
    return $false
  }
  if (-not $script:BranchCreateFrom -or $script:BranchCreateFrom.Trim() -eq "") {
    Write-Host "Error: create_from is empty — cannot branch off nothing." -ForegroundColor Red
    return $false
  }

  Write-Host ""
  Write-Host "[runner] === Branch setup ===" -ForegroundColor Cyan
  Write-Host "[runner] target: checkout -B $($script:BranchName) from $($script:BranchCreateFrom) across $(@($Repos).Count) repo(s)"

  $branched = 0
  foreach ($repo in $Repos) {
    if (-not $repo) { continue }
    $r = Setup-BranchOneRepo -Repo $repo
    if ($r -eq 'fail') { return $false }
    if ($r -eq 'branched') { $branched++ }
  }

  if ($branched -eq 0) {
    Write-Host "Error: could not create '$($script:BranchName)' in any repo (base ref missing or all trees dirty)." -ForegroundColor Red
    return $false
  }
  Write-Host "[runner] branched $branched repo(s)."
  Write-Host "[runner] === Branch setup complete ===" -ForegroundColor Cyan
  Write-Host ""
  return $true
}

# ----- Dot-source guard ---------------------------------------------
# When dot-sourced (invocation name is '.'), or when ILK_DOTSOURCE_ONLY=1,
# functions are defined but the main loop does not execute.  Lets a test or
# tool dot-source this script to call internal functions without starting
# the iteration loop.
$__isDotSourced = $MyInvocation.InvocationName -eq '.'
if ($__isDotSourced -or $env:ILK_DOTSOURCE_ONLY -eq '1') { return }

# ----- Runtime validation (only when running directly) ---------------

if (-not $ProjectPath) {
  throw "ProjectPath is required. Usage: run_ilk_loop_claude.ps1 -ProjectPath <path>"
}

# ----- Discovery ----------------------------------------------------

$repos = Get-GitRepos -Root $ProjectPath
if ($repos.Count -eq 0) {
  throw "No git repos found at or under $ProjectPath"
}

# Parse MASTER branch: block (sets $script:BranchName etc.)
Parse-MasterBranchBlock -Project $ProjectPath

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
if ($McpConfigPath) {
  Write-Host "MCP config:     $McpConfigPath (strict — worker sees only what'`s listed)"
} else {
  Write-Host "MCP config:     (default — worker sees user's full MCP registry)"
}
if ($script:BranchName) {
  Write-Host "Branch policy:  checkout -B $($script:BranchName) from $($script:BranchCreateFrom) (merge_back=$($script:BranchMergeBack))"
}
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

# Branch setup (Gap 2+3): if MASTER has a branch: block, checkout -B from fresh base
$branchOk = Setup-Branch -Repos $repos
if (-not $branchOk) {
  throw "Branch setup failed. Fix the issue and retry."
}

# Determine remote type for commit trailer policy (Gap 5)
# Write to .ilk-remote-type so the agent knows whether to include trailers
$remoteType = "shared"  # default
$remoteForBranch = ""
if ($script:BranchName) {
  # Branch was just set up; get its upstream remote
  try {
    $remoteForBranch = & git -C $repos[0] config --get "branch.$($script:BranchName).remote" 2>$null
  } catch { $remoteForBranch = "" }
}
if ($remoteForBranch) {
  $remoteType = Classify-Remote -Remote $remoteForBranch -Repos $repos
} else {
  # No branch block or no upstream; check origin as fallback
  $remoteType = Classify-Remote -Remote "origin" -Repos $repos
}
Write-Host "[runner] remote type: $remoteType (remote: $($remoteForBranch -or 'origin'))"
Set-Content -Path (Join-Path $ProjectPath ".ilk-remote-type") -Value $remoteType

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
  $localChecksBlocked = $false
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

      # B2 enforcement: a gate that FAILED (assertion failed) or ERRORED
      # (command couldn't execute) must BLOCK — the loop must NOT advance/ship
      # on un-passed gates. (Previously only "error" blocked, so a failing test
      # gate still shipped.)
      # DEFER the break until AFTER Write-JsonlRecord below, so this failing
      # iteration is still recorded to .ilk-loop.log. Otherwise collect.py /
      # ilk-feedback never see a run that ended on a gate -> the classifier
      # goes blind and falls back to a stale run (the misclassification cascade).
      $blocking = $localChecksRun | Where-Object { $_.outcome -eq "error" -or $_.outcome -eq "fail" }
      if ($blocking) {
        $stopReason = "local_checks_failed"
        $iterStopReason = "local_checks_failed"
        $localChecksBlocked = $true
        $why = ($blocking | ForEach-Object { "$($_.slug)#$($_.step):$($_.outcome)" }) -join ", "
        Write-Host "Loop stopped: local_checks not passing (B2 enforcement) -> $why" -ForegroundColor Red
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

  # Deferred B2 break: the failing iteration is now recorded above (so the
  # classifier can see it); stop before running quality gates / shipping.
  if ($localChecksBlocked) { break }

  if ($totalNew -gt 0) {
    $gateResult = Invoke-QualityGatesIfNeeded `
      -Project $ProjectPath `
      -Repos $repos `
      -HeadsBefore $headsBefore `
      -HeadsAfter $headsAfter `
      -TotalNew $totalNew `
      -NewCommits $newCommits
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

    # Remove the launcher's running.pid so the scheduler does not see a
    # stale sentinel and log skip-busy forever.  The -NoExit shell keeps
    # its PID alive past the loop's real exit, but the loop is done —
    # the pid file is now misleading.  Best-effort + idempotent.
    $launcherDir = Join-Path $RuntimeDir 'launcher'
    Remove-Item (Join-Path $launcherDir 'running.pid') -ErrorAction SilentlyContinue
  }
}
