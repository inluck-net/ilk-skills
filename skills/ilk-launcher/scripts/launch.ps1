<#
.SYNOPSIS
  Launch run_ilk_loop_claude.ps1 in a detached desktop PowerShell window.

.DESCRIPTION
  Wraps run_ilk_loop_claude.ps1 with:
    - Project resolution (cwd walk-up / -ProjectName lookup / -ProjectPath)
    - Per-project parameter resolution (<project>/docs/plans/.ilk-launch.json)
    - Detached window via Start-Process powershell -NoExit
    - PID file written to ~/.ilk-data/projects/<key>/runtime/launcher/running.pid
    - Concurrent-run protection (refuses to start if a live PID exists)

  After Start-Process returns, control returns to the caller (Cursor agent
  or interactive shell) immediately. The spawned window is independent and
  survives Cursor closing.

.PARAMETER ProjectPath
  Absolute path to the project root (the directory containing docs/plans/).

.PARAMETER ProjectName
  Look up the path in ~/.cursor/skills/ilk-launcher/projects.json.

.PARAMETER MaxIterations
  Override the per-project / default value.

.PARAMETER IterationTimeoutMin
  Override the per-project / default value.

.PARAMETER All
  Iterate projects.json and launch every project (skips projects already
  running). Mutually exclusive with -ProjectPath / -ProjectName.

.PARAMETER Force
  Skip the "already running" check.

.PARAMETER DryRun
  Print the resolved plan but do not spawn anything.

.EXAMPLE
  # From inside a project workspace, cwd walk-up resolves it:
  .\launch.ps1

.EXAMPLE
  .\launch.ps1 -ProjectName es_api

.EXAMPLE
  .\launch.ps1 -ProjectPath C:\path\to\your\project -MaxIterations 60

.EXAMPLE
  .\launch.ps1 -All
#>
[CmdletBinding(DefaultParameterSetName = 'ByCwd')]
param(
  [Parameter(ParameterSetName = 'ByPath', Mandatory)]
  [string]$ProjectPath,

  [Parameter(ParameterSetName = 'ByName', Mandatory)]
  [string]$ProjectName,

  [Parameter(ParameterSetName = 'All', Mandatory)]
  [switch]$All,

  [int]$MaxIterations = 0,
  [int]$IterationTimeoutMin = 0,
  [switch]$Force,
  [switch]$DryRun,

  # Comma-separated list of MCP server names to DISABLE for the spawned
  # worker. Blacklist mode — everything from ~/.claude.json's mcpServers
  # EXCEPT the listed ones is exposed to the worker. Mutually exclusive
  # with -EnableMcp.
  [string]$DisableMcp = "",

  # Comma-separated list of MCP server names to ENABLE for the spawned
  # worker. Whitelist mode — ONLY the listed ones are exposed. The
  # recommended default for loop workers, since 80% of iterations don't
  # need any MCP at all and 20% mostly need just `lark-tickets`.
  # Mutually exclusive with -DisableMcp.
  #
  # Common case: `worker_enable_mcp: ["lark-tickets"]` keeps ticket
  # state transitions on sub-plan ship without paying for chrome-devtools
  # snapshots (stay-resident) or figma context lookups (rarely useful in
  # execution — design-context happens during /ilk-plan).
  #
  # Resolution order for either mode:
  #   1. CLI flag (-DisableMcp or -EnableMcp)
  #   2. .ilk-launch.json's `worker_disable_mcp` or `worker_enable_mcp`
  #   3. nothing set → don't pass --mcp-config (worker sees full registry)
  #
  # Implementation: launcher filters ~/.claude.json's `mcpServers`
  # according to the selected mode, writes the result to the external
  # launcher dir (resolved via ilk_paths.py) as mcp-worker.json
  # and passes it via -McpConfigPath to run_ilk_loop_claude.ps1, which
  # appends `--mcp-config <path> --strict-mcp-config` to every `claude
  # -p` invocation. `--strict-mcp-config` also drops claude.ai-synced
  # servers (Gmail / Drive) for the worker.
  [string]$EnableMcp = ""
)

$ErrorActionPreference = 'Stop'

# --- skill root resolution ---------------------------------------------------
. (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

# --- constants ---------------------------------------------------------------
$LauncherDir   = Join-Path $SkillRoot 'ilk-launcher'
$ProjectsJson  = Join-Path $LauncherDir 'projects.json'
$LoopScript    = Join-Path $SkillRoot 'ilk-loop\scripts\run_ilk_loop_claude.ps1'
$DefaultMaxIter = 30
$DefaultTimeout = 30
$ValidEngines = @('claude', 'codex')
$DefaultEngine = 'claude'

if (-not (Test-Path $LoopScript)) {
  throw "run_ilk_loop_claude.ps1 not found at $LoopScript. Is ilk-loop skill installed?"
}

# --- helpers -----------------------------------------------------------------

function Read-ProjectsRegistry {
  if (-not (Test-Path $ProjectsJson)) { return @() }
  $raw = Get-Content $ProjectsJson -Raw | ConvertFrom-Json
  if ($null -eq $raw.projects) { return @() }
  return $raw.projects
}

function Resolve-ProjectByName {
  param([string]$Name)
  $projects = Read-ProjectsRegistry
  $match = $projects | Where-Object { $_.name -eq $Name }
  if (-not $match) {
    $known = ($projects | ForEach-Object { $_.name }) -join ', '
    throw "Project '$Name' not in projects.json. Known: $known"
  }
  return [string]$match.path
}

function Resolve-ProjectByCwd {
  # First try ilk_paths.py — it's authoritative for both single-repo
  # (.git ancestor) AND meta (.ilk-meta.json ancestor) projects. Falls
  # back to legacy walk-up only when the helper is unavailable.
  $resolver = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
  if (Test-Path $resolver) {
    try {
      $json = & python $resolver --start (Get-Location).Path 2>$null
      if ($LASTEXITCODE -eq 0 -and $json) {
        $obj = $json | ConvertFrom-Json -ErrorAction Stop
        if ($obj.project_root) { return [string]$obj.project_root }
      }
    } catch {
      # Fall through to legacy walk-up
    }
  }
  $dir = (Get-Location).Path
  while ($dir) {
    if (Test-Path (Join-Path $dir 'docs\plans')) {
      $masters = Get-ChildItem (Join-Path $dir 'docs\plans') -Filter 'MASTER-*.md' -ErrorAction SilentlyContinue
      if ($masters) { return $dir }
    }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
  }
  throw "No project found by walking up from $((Get-Location).Path). No .ilk-meta.json, .git, or docs/plans/MASTER-*.md anywhere on the path. Use -ProjectName or -ProjectPath, or cd into a project."
}

function Get-ExternalPlansDir {
  # Returns the external plans dir for $ProjectPath (meta-aware), or "".
  param([string]$ProjectPath)
  $resolver = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
  if (-not (Test-Path $resolver)) { return "" }
  try {
    $json = & python $resolver --start $ProjectPath 2>$null
    if ($LASTEXITCODE -eq 0 -and $json) {
      $obj = $json | ConvertFrom-Json -ErrorAction Stop
      if ($obj.external_plans_dir) { return [string]$obj.external_plans_dir }
    }
  } catch {}
  return ""
}

function Get-ExternalLauncherDir {
  param([string]$ProjectPath)
  $resolver = Join-Path $SkillRoot "ilk-loop\scripts\ilk_paths.py"
  if (-not (Test-Path $resolver)) { return "" }
  try {
    $json = & python $resolver --start $ProjectPath 2>$null
    if ($LASTEXITCODE -eq 0 -and $json) {
      $obj = $json | ConvertFrom-Json -ErrorAction Stop
      if ($obj.external_launcher_dir) { return [string]$obj.external_launcher_dir }
    }
  } catch {}
  return ""
}

function Get-ProjectName {
  param([string]$Path)
  $projects = Read-ProjectsRegistry
  $match = $projects | Where-Object { $_.path -eq $Path }
  if ($match) { return [string]$match.name }
  return (Split-Path $Path -Leaf)
}

function ConvertTo-IlkHashtable {
  # PS 5.1 lacks `ConvertFrom-Json -AsHashtable` (added in PS 6.0). Read-ProjectConfig
  # callers downstream expect a hashtable (`$cfg.ContainsKey(...)`, `$cfg['...']`),
  # not a PSCustomObject. Convert recursively so nested objects also become
  # hashtables; arrays stay as arrays of converted items; scalars pass through.
  param($InputObject)
  if ($null -eq $InputObject) { return $null }
  if ($InputObject -is [System.Management.Automation.PSCustomObject]) {
    $h = @{}
    foreach ($p in $InputObject.PSObject.Properties) {
      $h[$p.Name] = ConvertTo-IlkHashtable -InputObject $p.Value
    }
    return $h
  }
  if ($InputObject -is [System.Collections.IList] -and -not ($InputObject -is [string])) {
    return @($InputObject | ForEach-Object { ConvertTo-IlkHashtable -InputObject $_ })
  }
  return $InputObject
}

function Read-ProjectConfig {
  # Look in the external plans dir first (meta-friendly: this is the
  # single source of truth for both single and meta projects). Fall
  # back to the legacy in-tree location so single-repo projects that
  # haven't migrated yet still find their config.
  param([string]$ProjectPath)
  $extPlans = Get-ExternalPlansDir -ProjectPath $ProjectPath
  if ($extPlans) {
    $cfgPath = Join-Path $extPlans '.ilk-launch.json'
    if (Test-Path $cfgPath) {
      return ConvertTo-IlkHashtable (Get-Content $cfgPath -Raw | ConvertFrom-Json)
    }
  }
  $cfgPath = Join-Path $ProjectPath 'docs\plans\.ilk-launch.json'
  if (-not (Test-Path $cfgPath)) { return @{} }
  return ConvertTo-IlkHashtable (Get-Content $cfgPath -Raw | ConvertFrom-Json)
}

function Resolve-Params {
  param(
    [string]$ProjectPath,
    [int]$CliMaxIter,
    [int]$CliTimeout
  )
  $cfg = Read-ProjectConfig -ProjectPath $ProjectPath
  $maxIter = if ($CliMaxIter -gt 0) { $CliMaxIter }
             elseif ($cfg.ContainsKey('max_iterations')) { [int]$cfg.max_iterations }
             else { $DefaultMaxIter }
  $timeout = if ($CliTimeout -gt 0) { $CliTimeout }
             elseif ($cfg.ContainsKey('iteration_timeout_min')) { [int]$cfg.iteration_timeout_min }
             else { $DefaultTimeout }
  return @{ MaxIterations = $maxIter; IterationTimeoutMin = $timeout }
}

function Resolve-Engine {
  param([string]$ProjectPath)
  $cfg = Read-ProjectConfig -ProjectPath $ProjectPath
  $engine = if ($cfg.ContainsKey('worker_engine')) { [string]$cfg.worker_engine } else { $DefaultEngine }
  if ($ValidEngines -notcontains $engine) {
    throw "Invalid worker_engine '$engine'. Valid engines: $($ValidEngines -join ', ')"
  }
  return $engine
}

function Resolve-McpFilter {
  <#
    Decide how to filter MCP servers for the worker. Returns a hashtable
    @{ Mode = "blacklist"|"whitelist"|""; Names = @() }.
    Mode "" means "no filtering" — launcher won't pass --mcp-config.

    CLI flags trump per-project config; setting both blacklist and
    whitelist sources at the SAME level is an error (we don't try to
    guess which the user meant).
  #>
  param(
    [string]$ProjectPath,
    [string]$CliDisableMcp,
    [string]$CliEnableMcp
  )

  $cliDisable = -not [string]::IsNullOrWhiteSpace($CliDisableMcp)
  $cliEnable  = -not [string]::IsNullOrWhiteSpace($CliEnableMcp)
  if ($cliDisable -and $cliEnable) {
    throw "Specify either -DisableMcp (blacklist) or -EnableMcp (whitelist), not both."
  }
  if ($cliDisable) {
    $names = @($CliDisableMcp.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    return @{ Mode = "blacklist"; Names = $names }
  }
  if ($cliEnable) {
    $names = @($CliEnableMcp.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    return @{ Mode = "whitelist"; Names = $names }
  }

  $cfg = Read-ProjectConfig -ProjectPath $ProjectPath
  $hasDisable = $cfg.ContainsKey('worker_disable_mcp')
  $hasEnable  = $cfg.ContainsKey('worker_enable_mcp')
  if ($hasDisable -and $hasEnable) {
    throw "Specify either worker_disable_mcp or worker_enable_mcp in .ilk-launch.json, not both."
  }
  if ($hasDisable) {
    $raw = $cfg['worker_disable_mcp']
    if ($raw -is [System.Collections.IEnumerable] -and -not ($raw -is [string])) {
      $names = @($raw | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
      return @{ Mode = "blacklist"; Names = $names }
    }
  }
  if ($hasEnable) {
    $raw = $cfg['worker_enable_mcp']
    if ($raw -is [System.Collections.IEnumerable] -and -not ($raw -is [string])) {
      $names = @($raw | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
      return @{ Mode = "whitelist"; Names = $names }
    }
  }
  return @{ Mode = ""; Names = @() }
}

function Build-WorkerMcpConfig {
  <#
    Build a temp MCP config file at the external launcher dir
    (<ProjectPath>'s resolved ~/.ilk-data/projects/<key>/runtime/launcher/)
    as mcp-worker.json, containing the MCP servers selected by $Mode + $Names
    from ~/.claude.json's mcpServers:

      blacklist → all servers EXCEPT $Names
      whitelist → ONLY servers in $Names that exist in the registry

    Returns the absolute path of the temp file, or "" if no filtering
    should happen (empty mode/names, or ~/.claude.json absent / missing
    mcpServers).

    The runner appends `--mcp-config <path> --strict-mcp-config` so the
    worker only sees what we wrote. claude.ai-synced servers (Gmail /
    Drive / etc.) are also dropped for the worker — desired: workers
    almost never need email / drive access.
  #>
  param(
    [string]$ProjectPath,
    [string]$Mode,
    [string[]]$Names
  )
  if (-not $Mode -or -not $Names -or $Names.Count -eq 0) { return "" }
  $claudeJson = Join-Path $HOME ".claude.json"
  if (-not (Test-Path $claudeJson)) {
    Write-Host "[ilk] worker MCP filter requested but ~/.claude.json not found; skipping." -ForegroundColor DarkYellow
    return ""
  }
  try {
    $parsed = Get-Content $claudeJson -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
  } catch {
    Write-Host "[ilk] worker MCP filter requested but ~/.claude.json is malformed; skipping." -ForegroundColor DarkYellow
    return ""
  }
  if (-not $parsed.mcpServers) {
    Write-Host "[ilk] worker MCP filter requested but ~/.claude.json has no mcpServers; skipping." -ForegroundColor DarkYellow
    return ""
  }

  $filtered = [ordered]@{}
  $kept = @()
  $skipped = @()
  $missing = @()
  if ($Mode -eq "whitelist") {
    foreach ($want in $Names) {
      $prop = $parsed.mcpServers.PSObject.Properties[$want]
      if ($prop) {
        $filtered[$want] = $prop.Value
        $kept += $want
      } else {
        $missing += $want
      }
    }
  } else {
    # blacklist
    foreach ($prop in $parsed.mcpServers.PSObject.Properties) {
      if ($Names -contains $prop.Name) {
        $skipped += $prop.Name
        continue
      }
      $filtered[$prop.Name] = $prop.Value
      $kept += $prop.Name
    }
  }

  $out = [ordered]@{ mcpServers = $filtered }
  $stateDir = Get-ExternalLauncherDir -ProjectPath $ProjectPath
  if (-not $stateDir) {
    Write-Host "[ilk] could not resolve external launcher dir for $ProjectPath" -ForegroundColor Red
    return ""
  }
  if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
  $target = Join-Path $stateDir 'mcp-worker.json'
  # PS 5.1's `Out-File -Encoding utf8` writes a BOM. Some JSON parsers
  # don't tolerate that (and Claude's --mcp-config doesn't need it), so
  # write UTF-8 without BOM via .NET.
  $json = $out | ConvertTo-Json -Depth 10
  [System.IO.File]::WriteAllText($target, $json, [System.Text.UTF8Encoding]::new($false))

  if ($Mode -eq "whitelist") {
    Write-Host ("[ilk] worker MCP filter (whitelist): kept {0}" -f ($kept -join ', ')) -ForegroundColor DarkGray
    if ($missing.Count -gt 0) {
      Write-Host ("[ilk] note: {0} not in ~/.claude.json mcpServers (typo? claude.ai-synced?)" -f ($missing -join ', ')) -ForegroundColor DarkYellow
    }
  } else {
    if ($skipped.Count -gt 0) {
      Write-Host ("[ilk] worker MCP filter (blacklist): disabling {0} (kept {1})" -f ($skipped -join ', '), ($kept -join ', ')) -ForegroundColor DarkGray
    }
  }
  return $target
}

function Get-PidFilePath {
  param([string]$ProjectPath)
  $dir = Get-ExternalLauncherDir -ProjectPath $ProjectPath
  if (-not $dir) { throw "Could not resolve external launcher dir for $ProjectPath" }
  return Join-Path $dir 'running.pid'
}

function Get-LaunchMetaPath {
  param([string]$ProjectPath)
  $dir = Get-ExternalLauncherDir -ProjectPath $ProjectPath
  if (-not $dir) { throw "Could not resolve external launcher dir for $ProjectPath" }
  return Join-Path $dir 'last-launch.json'
}

function Test-RunningPid {
  param([string]$ProjectPath)
  $pidFile = Get-PidFilePath -ProjectPath $ProjectPath
  if (-not (Test-Path $pidFile)) { return $null }
  $rawPid = (Get-Content $pidFile -Raw).Trim()
  if (-not $rawPid) { return $null }
  $existingPid = [int]$rawPid
  $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
  if ($proc) { return $existingPid }
  Remove-Item $pidFile -Force
  return $null
}

function Start-ilkWindow {
  param(
    [string]$ProjectPath,
    [string]$ProjectName,
    [int]$MaxIterations,
    [int]$IterationTimeoutMin,
    [bool]$Force,
    [bool]$DryRun,
    [string]$McpConfigPath = ""
  )

  $livePid = Test-RunningPid -ProjectPath $ProjectPath
  if ($livePid -and -not $Force) {
    Write-Host "[$ProjectName] already running (PID $livePid). Use -Force to launch anyway, or stop.ps1 to kill it." -ForegroundColor Yellow
    return $null
  }

  $stateDir = Get-ExternalLauncherDir -ProjectPath $ProjectPath
  if (-not $stateDir) {
    Write-Host "[$ProjectName] could not resolve external launcher dir" -ForegroundColor Red
    return $null
  }
  if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }

  $title = "ilk: $ProjectName"

  $mcpArg = ""
  if ($McpConfigPath) {
    $mcpArg = " -McpConfigPath '$McpConfigPath'"
  }

  $inner = @"
`$Host.UI.RawUI.WindowTitle = '$title'
Write-Host '=== ilk-launcher ===' -ForegroundColor Cyan
Write-Host "Project: $ProjectPath"
Write-Host "MaxIterations: $MaxIterations    IterationTimeoutMin: $IterationTimeoutMin"
Write-Host "Started: `$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host '======================' -ForegroundColor Cyan
& '$LoopScript' -ProjectPath '$ProjectPath' -MaxIterations $MaxIterations -IterationTimeoutMin $IterationTimeoutMin$mcpArg
`$code = `$LASTEXITCODE
Write-Host ''
Write-Host '[ilk-launcher] run_ilk_loop_claude.ps1 exited with code:' `$code -ForegroundColor Yellow
Write-Host '[ilk-launcher] window left open for review. Close manually when done.' -ForegroundColor Yellow
"@

  if ($DryRun) {
    Write-Host "[$ProjectName] DRY RUN — would launch:" -ForegroundColor Cyan
    Write-Host "  Title: $title"
    Write-Host "  ProjectPath: $ProjectPath"
    Write-Host "  MaxIterations: $MaxIterations"
    Write-Host "  IterationTimeoutMin: $IterationTimeoutMin"
    if ($McpConfigPath) { Write-Host "  McpConfigPath: $McpConfigPath" }
    $pidFile = Get-PidFilePath -ProjectPath $ProjectPath
    Write-Host "  PID file: $pidFile"
    return $null
  }

  $proc = Start-Process powershell -ArgumentList @('-NoExit', '-NoProfile', '-Command', $inner) -PassThru

  $pidFile = Get-PidFilePath -ProjectPath $ProjectPath
  $proc.Id | Out-File -FilePath $pidFile -Encoding ascii -NoNewline

  $meta = @{
    project_path           = $ProjectPath
    project_name           = $ProjectName
    pid                    = $proc.Id
    started_at             = (Get-Date -Format 's')
    max_iterations         = $MaxIterations
    iteration_timeout_min  = $IterationTimeoutMin
    loop_script            = $LoopScript
    mcp_config_path        = $McpConfigPath
  }
  $meta | ConvertTo-Json | Out-File -FilePath (Get-LaunchMetaPath -ProjectPath $ProjectPath) -Encoding utf8

  Write-Host "[$ProjectName] launched. PID $($proc.Id). Title: '$title'." -ForegroundColor Green
  Write-Host "[$ProjectName] PID file: $pidFile"
  Write-Host "[$ProjectName] loop JSONL log: $SkillRoot\ilk-loop\logs (see run_ilk_loop_claude.ps1 -LogDir)"
  return $proc.Id
}

# --- main --------------------------------------------------------------------

if ($All) {
  $projects = Read-ProjectsRegistry
  if (-not $projects -or $projects.Count -eq 0) {
    throw "projects.json has no projects. Add some before using -All."
  }
  foreach ($p in $projects) {
    $params = Resolve-Params -ProjectPath $p.path -CliMaxIter $MaxIterations -CliTimeout $IterationTimeoutMin
    $mcpFilter = Resolve-McpFilter -ProjectPath $p.path -CliDisableMcp $DisableMcp -CliEnableMcp $EnableMcp
    $mcpCfg = Build-WorkerMcpConfig -ProjectPath $p.path -Mode $mcpFilter.Mode -Names $mcpFilter.Names
    Start-ilkWindow `
      -ProjectPath $p.path `
      -ProjectName $p.name `
      -MaxIterations $params.MaxIterations `
      -IterationTimeoutMin $params.IterationTimeoutMin `
      -Force:$Force.IsPresent `
      -DryRun:$DryRun.IsPresent `
      -McpConfigPath $mcpCfg | Out-Null
  }
  return
}

# single-project paths
$resolvedPath = switch ($PSCmdlet.ParameterSetName) {
  'ByPath' { $ProjectPath }
  'ByName' { Resolve-ProjectByName -Name $ProjectName }
  'ByCwd'  { Resolve-ProjectByCwd }
}

if (-not (Test-Path $resolvedPath)) {
  throw "ProjectPath '$resolvedPath' does not exist."
}
$resolvedPath = (Resolve-Path $resolvedPath).Path
$resolvedName = if ($ProjectName) { $ProjectName } else { Get-ProjectName -Path $resolvedPath }
$params = Resolve-Params -ProjectPath $resolvedPath -CliMaxIter $MaxIterations -CliTimeout $IterationTimeoutMin
$engine = Resolve-Engine -ProjectPath $resolvedPath
$mcpFilter = Resolve-McpFilter -ProjectPath $resolvedPath -CliDisableMcp $DisableMcp -CliEnableMcp $EnableMcp
$mcpCfg = Build-WorkerMcpConfig -ProjectPath $resolvedPath -Mode $mcpFilter.Mode -Names $mcpFilter.Names

Start-ilkWindow `
  -ProjectPath $resolvedPath `
  -ProjectName $resolvedName `
  -MaxIterations $params.MaxIterations `
  -IterationTimeoutMin $params.IterationTimeoutMin `
  -Force:$Force.IsPresent `
  -DryRun:$DryRun.IsPresent `
  -McpConfigPath $mcpCfg | Out-Null
