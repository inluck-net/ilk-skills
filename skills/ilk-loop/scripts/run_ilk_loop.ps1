<#
.SYNOPSIS
  Run the ilk-loop autonomously until all sub-plans ship,
  max iterations hit, or progress stalls.

.DESCRIPTION
  Wraps the Cursor CLI (`agent`) in a PowerShell loop. Each iteration:
    1. Calls loop_status.py (from cwd) to check if all sub-plans
       are shipped. Exit 0 = done.
    2. Snapshots HEAD of every git repo under -ProjectPath
       (the root, plus any sub-directory containing a .git folder).
    3. Runs `agent -p -f "/ilk please continue the active plan"`
       with -ProjectPath as cwd, with a per-iteration timeout.
    4. Snapshots HEAD again, computes new commits per repo.
    5. If no new commits anywhere → "no progress", stop.
    6. Appends a JSONL record to <LogDir>\.ilk-loop.log
       and writes per-iteration agent output to a separate file.

  Stop conditions:
    - all-shipped     : loop_status.py exits 0
    - max-iterations  : hit -MaxIterations
    - no-progress     : iteration completed but no new commits
    - agent-error     : agent exited non-zero
    - timeout         : iteration exceeded -IterationTimeoutMin
    - already-shipped : nothing to do at start

.PARAMETER ProjectPath
  Project root containing docs/plans/MASTER-*.md and one or more
  git repos. The agent's cwd is set to this path each iteration.

.PARAMETER MaxIterations
  Hard cap on iterations. Default 30.

.PARAMETER IterationTimeoutMin
  Per-iteration wall-clock timeout, in minutes. Default 30.
  When exceeded, the agent process tree is killed.

.PARAMETER LoopStatusScript
  Path to loop_status.py.
  Default: <skill-root>\ilk-loop\scripts\loop_status.py

.PARAMETER LogDir
  Per-run artifact directory (iter logs, heads files).
  Default: ~/.ilk-data/projects/<key>/logs/runs/<run-id>

.PARAMETER Prompt
  The prompt sent to the agent. Default invokes the /ilk command.

.PARAMETER Model
  Cursor CLI model id passed via --model. Default "auto" lets Cursor's
  router pick per iteration. Run `agent --list-models` to see available
  ids on this account (e.g. composer-2-fast, composer-2, composer-1.5,
  grok-4-20, grok-4-20-thinking, kimi-k2.5).
  Note: claude / gpt slugs are NOT exposed by the headless CLI even
  though `agent --help` lists them as examples.

.EXAMPLE
  # Smoke test with 2-iteration cap
  .\run_ilk_loop.ps1 -ProjectPath C:\path\to\your\project -MaxIterations 2

.NOTES
  - Requires Cursor CLI `agent` on PATH and `agent login` already done.
  - Must be run from a fresh PowerShell window, not nested inside
    another agent's chat terminal.
  - Cursor CLI requires interactive auth, so this script is laptop-
    bound. The `claude`-based variant (run_ilk_loop_claude.ps1) takes
    API-key auth and can run on headless servers.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [string]$ProjectPath,

  [int]$MaxIterations = 30,

  [int]$IterationTimeoutMin = 30,

  [string]$LoopStatusScript = "",

  [string]$LogDir = "",

  [string]$Prompt = "/ilk please continue the active plan",

  [string]$Model = "auto"
)

$ErrorActionPreference = "Stop"

# ----- Skill root resolution ------------------------------------------
. (Join-Path $PSScriptRoot "_ilk_skill_root.ps1")
$SkillRoot = Get-IlkSkillRoot

if (-not $LoopStatusScript) { $LoopStatusScript = Join-Path $SkillRoot "ilk-loop\scripts\loop_status.py" }
if (-not $LogDir)           { $LogDir = Join-Path $SkillRoot "ilk-loop\logs" }

# ----- Pre-flight ---------------------------------------------------

if (-not (Test-Path $ProjectPath)) {
  throw "ProjectPath does not exist: $ProjectPath"
}
$ProjectPath = (Resolve-Path $ProjectPath).Path

if (-not (Test-Path $LoopStatusScript)) {
  throw "loop_status.py not found at: $LoopStatusScript"
}

if (-not (Get-Command agent -ErrorAction SilentlyContinue)) {
  throw "Cursor CLI 'agent' not on PATH. Install with: irm 'https://cursor.com/install?win32=true' | iex"
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "python not on PATH (needed by loop_status.py)"
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "git not on PATH"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$RunId     = Get-Date -Format "yyyyMMdd-HHmmss"
$RunLogDir = Join-Path $LogDir "ilk-$RunId"
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

function Test-AllShipped {
  param([string]$Project)
  Push-Location $Project
  try {
    & python $LoopStatusScript *>$null
    return ($LASTEXITCODE -eq 0)
  } finally {
    Pop-Location
  }
}

function Write-JsonlRecord {
  param([hashtable]$Record)
  $json = $Record | ConvertTo-Json -Compress -Depth 10
  [System.IO.File]::AppendAllText($JsonlLog, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
}

function Format-AgentJsonLine {
  # Formats one JSON event from `agent --output-format stream-json` into
  # a short human-readable line. Returns $null for events we want to
  # silently drop (e.g. our own echoed prompt).
  #
  # Known event shapes (observed from cursor-agent v2026.x):
  #   {"type":"system","subtype":"init", ...}
  #   {"type":"user","message":{...}}                      <- echo, skip
  #   {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]},
  #    "timestamp_ms":...}                                 <- streaming delta
  #   {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
  #                                                        <- final consolidated, skip
  #   {"type":"assistant","message":{"content":[{"type":"tool_use","name":"X","input":{...}}]}}
  #   {"type":"tool_result", ...}
  #   {"type":"result","subtype":"success","duration_ms":...,"usage":{...}}
  param([string]$Line)

  if ([string]::IsNullOrWhiteSpace($Line)) { return $null }

  try {
    $obj = $Line | ConvertFrom-Json -ErrorAction Stop
  } catch {
    return $Line  # not JSON — pass through verbatim
  }

  switch ($obj.type) {
    'system' {
      if ($obj.subtype -eq 'init') {
        return "[init] model=$($obj.model) session=$($obj.session_id)"
      }
      return "[system] $($obj.subtype)"
    }
    'user'   { return $null }  # our own prompt echo
    'assistant' {
      $parts = @()
      foreach ($c in $obj.message.content) {
        if ($c.type -eq 'text') {
          if ($obj.timestamp_ms) {
            # streaming delta — print inline, no newline
            [Console]::Out.Write($c.text)
          }
          # else: final consolidated message, skip (already streamed)
        } elseif ($c.type -eq 'tool_use') {
          $argSummary = ''
          if ($c.input) {
            $keys = @($c.input.PSObject.Properties.Name) | Select-Object -First 2
            $argSummary = ($keys | ForEach-Object {
              $v = "$($c.input.$_)"
              if ($v.Length -gt 60) { $v = $v.Substring(0,60) + '...' }
              "$_=$v"
            }) -join ', '
          }
          $parts += "`n[tool] $($c.name)($argSummary)"
        }
      }
      if ($parts.Count -gt 0) { return ($parts -join '') }
      return $null
    }
    'tool_result' {
      $preview = ''
      if ($obj.content) {
        $preview = ("$($obj.content)" -replace '\s+',' ')
        if ($preview.Length -gt 120) { $preview = $preview.Substring(0,120) + '...' }
      }
      return "`n[result] $preview"
    }
    'result' {
      $sec = [math]::Round($obj.duration_ms / 1000.0, 1)
      $usage = $obj.usage
      $tok = if ($usage) { " tokens(in=$($usage.inputTokens) out=$($usage.outputTokens))" } else { '' }
      return "`n`n[done] $($obj.subtype) in ${sec}s$tok"
    }
    default { return "[$($obj.type)] $Line" }
  }
}

function Get-ToolCallSummary {
  # Extract a short, human-readable description of what a tool_call
  # is doing. cursor-agent wraps each tool kind under a different key
  # (shellToolCall, readToolCall, editToolCall, grepToolCall, etc.),
  # so we walk the structure and pull the most useful fields per kind.
  #
  # Observed arg field names in cursor-agent v2026.04 stream-json:
  #   shell : args.command
  #   read  : args.path
  #   edit  : args.path, args.streamContent
  #   grep  : args.pattern, args.path
  #   glob  : args.globPattern, args.targetDirectory
  #   write : args.path
  param($ToolCall)
  if (-not $ToolCall) { return 'tool' }

  $kindProp = $ToolCall.PSObject.Properties |
              Where-Object { $_.Name -like '*ToolCall' } |
              Select-Object -First 1
  if (-not $kindProp) { return ($ToolCall.description) -as [string] }

  $kindName = ($kindProp.Name -replace 'ToolCall$','')
  $inner    = $kindProp.Value
  $desc     = $inner.description
  if (-not $desc) { $desc = $ToolCall.description }

  # Per-kind detail: append the most informative arg.
  $detail = ''
  switch ($kindName) {
    'shell' {
      $cmd = $inner.args.command
      if ($cmd) {
        $cmd = ($cmd -replace '\s+',' ').Trim()
        if ($cmd.Length -gt 80) { $cmd = $cmd.Substring(0,80) + '...' }
        $detail = " `$ $cmd"
      }
    }
    'read' {
      $p = if ($inner.args.path) { $inner.args.path } else { $inner.args.target_file }
      if ($p) { $detail = " $p" }
    }
    'edit' {
      $p = if ($inner.args.path) { $inner.args.path }
           elseif ($inner.args.target_file) { $inner.args.target_file }
           else { $inner.args.file_path }
      if ($p) { $detail = " $p" }
    }
    'grep' {
      if ($inner.args.pattern) {
        $pat = $inner.args.pattern
        if ($pat.Length -gt 60) { $pat = $pat.Substring(0,60) + '...' }
        $detail = " /$pat/"
        if ($inner.args.path) { $detail += " in $($inner.args.path)" }
      }
    }
    'glob' {
      $g = if ($inner.args.globPattern) { $inner.args.globPattern } else { $inner.args.glob_pattern }
      if ($g) { $detail = " $g" }
    }
    'write'  { if ($inner.args.path) { $detail = " $($inner.args.path)" } }
    default  {
      # Generic fallback: show first 1-2 string-ish args.
      if ($inner.args) {
        $shown = @()
        foreach ($p in @($inner.args.PSObject.Properties)) {
          if ($p.Value -is [string] -and $p.Value.Length -gt 0) {
            $v = $p.Value
            if ($v.Length -gt 60) { $v = $v.Substring(0,60) + '...' }
            $shown += "$($p.Name)=$v"
            if ($shown.Count -ge 2) { break }
          }
        }
        if ($shown.Count -gt 0) { $detail = ' ' + ($shown -join ', ') }
      }
    }
  }

  $label = if ($desc) { $desc } else { $kindName }
  return "$kindName : $label$detail"
}

function Get-ToolCallOutcome {
  # Extract a short outcome string for a completed tool_call event,
  # e.g. "exit=0 in 7.7s" for shell, "+12 -3 lines" for edit, etc.
  # Shapes observed in cursor-agent v2026.04:
  #   shell  result.success.{exitCode, executionTime, ...}
  #          result.success.error / result.error.message
  #   read   result.success.{totalLines, fileSize}
  #   edit   result.success.{linesAdded, linesRemoved}
  #   grep   result.success.{workspaceResults: { <repo>: {content: {matches:[...]} } } }
  #   glob   result.success.{totalFiles, files}
  param($ToolCall)
  if (-not $ToolCall) { return '?' }

  $kindProp = $ToolCall.PSObject.Properties |
              Where-Object { $_.Name -like '*ToolCall' } |
              Select-Object -First 1
  if (-not $kindProp) { return '?' }
  $kindName = ($kindProp.Name -replace 'ToolCall$','')
  $r        = $kindProp.Value.result
  if (-not $r) { return '?' }

  if ($r.error) {
    $msg = "$($r.error.message)"
    if (-not $msg) { $msg = ($r.error | ConvertTo-Json -Compress -Depth 3) }
    if ($msg.Length -gt 80) { $msg = $msg.Substring(0,80) + '...' }
    return "error: $msg"
  }
  if (-not $r.success) { return '?' }
  $s = $r.success

  switch ($kindName) {
    'shell' {
      $bits = @()
      if ($null -ne $s.exitCode)      { $bits += "exit=$($s.exitCode)" }
      if ($s.executionTime)           { $bits += "in $([math]::Round($s.executionTime/1000.0,1))s" }
      if ($bits.Count -eq 0)          { return 'ok' }
      return ($bits -join ' ')
    }
    'read' {
      $bits = @()
      if ($s.totalLines) { $bits += "$($s.totalLines) lines" }
      if ($s.fileSize)   { $bits += "$($s.fileSize) bytes" }
      if ($bits.Count -eq 0) { return 'ok' }
      return ($bits -join ', ')
    }
    'edit' {
      $a = if ($s.linesAdded)   { $s.linesAdded }   else { 0 }
      $d = if ($s.linesRemoved) { $s.linesRemoved } else { 0 }
      return "+$a -$d lines"
    }
    'grep' {
      # Walk workspaceResults to count matches across files
      $matches = 0; $files = 0
      if ($s.workspaceResults) {
        foreach ($wsProp in @($s.workspaceResults.PSObject.Properties)) {
          $ws = $wsProp.Value
          if ($ws.content.matches) {
            $files   += @($ws.content.matches).Count
            foreach ($m in @($ws.content.matches)) {
              if ($m.matches) { $matches += @($m.matches).Count }
            }
          }
        }
      }
      if ($matches -gt 0 -or $files -gt 0) { return "$matches matches in $files files" }
      return 'ok'
    }
    'glob' {
      if ($null -ne $s.totalFiles) { return "$($s.totalFiles) files" }
      return 'ok'
    }
    'write' { return 'ok' }
    default { return 'ok' }
  }
}

function Write-AgentJsonLine {
  # Parse one JSON line from agent stream-json output, render it
  # human-readably to the console, and append to $HumanLog.
  # Designed to be called from the main thread (synchronous path).
  param(
    [string]$Line,
    [string]$HumanLog
  )
  if ([string]::IsNullOrWhiteSpace($Line)) { return }

  try {
    $obj = $Line | ConvertFrom-Json -ErrorAction Stop
  } catch {
    # Not JSON (could be a stray progress message). Pass through.
    [Console]::Out.WriteLine($Line)
    [Console]::Out.Flush()
    try { Add-Content -LiteralPath $HumanLog -Value $Line -Encoding utf8 } catch {}
    return
  }

  switch ($obj.type) {
    'system' {
      $emit = if ($obj.subtype -eq 'init') {
        "[init] model=$($obj.model) session=$($obj.session_id)"
      } else { "[system] $($obj.subtype)" }
      [Console]::Out.WriteLine($emit); [Console]::Out.Flush()
      try { Add-Content -LiteralPath $HumanLog -Value $emit -Encoding utf8 } catch {}
    }
    'user'     { return }   # echo of our own prompt
    'thinking' { return }   # internal monologue — too noisy; preserved in raw .jsonl
    'assistant' {
      foreach ($c in $obj.message.content) {
        if ($c.type -eq 'text') {
          if ($obj.timestamp_ms) {
            # streaming text delta — print without newline
            [Console]::Out.Write($c.text); [Console]::Out.Flush()
            try { Add-Content -LiteralPath $HumanLog -Value $c.text -NoNewline -Encoding utf8 } catch {}
          }
          # else: final consolidated message, already streamed
        } elseif ($c.type -eq 'tool_use') {
          # Older event shape (some models still emit this).
          $argSummary = ''
          if ($c.input) {
            $keys = @($c.input.PSObject.Properties.Name) | Select-Object -First 2
            $argSummary = ($keys | ForEach-Object {
              $v = "$($c.input.$_)"
              if ($v.Length -gt 60) { $v = $v.Substring(0,60) + '...' }
              "$_=$v"
            }) -join ', '
          }
          $tline = "`n[tool] $($c.name)($argSummary)"
          [Console]::Out.WriteLine($tline); [Console]::Out.Flush()
          try { Add-Content -LiteralPath $HumanLog -Value $tline -Encoding utf8 } catch {}
        }
      }
    }
    'tool_call' {
      $summary = Get-ToolCallSummary -ToolCall $obj.tool_call
      switch ($obj.subtype) {
        'started'   { $emit = "`n[tool >] $summary" }
        'completed' {
          $outcome = Get-ToolCallOutcome -ToolCall $obj.tool_call
          $emit = "[tool <] $summary  ($outcome)"
        }
        default     { $emit = "[tool $($obj.subtype)] $summary" }
      }
      [Console]::Out.WriteLine($emit); [Console]::Out.Flush()
      try { Add-Content -LiteralPath $HumanLog -Value $emit -Encoding utf8 } catch {}
    }
    'tool_result' {
      $preview = ''
      if ($obj.content) {
        $preview = ("$($obj.content)" -replace '\s+',' ')
        if ($preview.Length -gt 120) { $preview = $preview.Substring(0,120) + '...' }
      }
      $emit = "[result] $preview"
      [Console]::Out.WriteLine($emit); [Console]::Out.Flush()
      try { Add-Content -LiteralPath $HumanLog -Value $emit -Encoding utf8 } catch {}
    }
    'result' {
      $sec = [math]::Round($obj.duration_ms / 1000.0, 1)
      $usage = $obj.usage
      $tok = if ($usage) { " tokens(in=$($usage.inputTokens) out=$($usage.outputTokens))" } else { '' }
      $emit = "`n[done] $($obj.subtype) in ${sec}s$tok"
      [Console]::Out.WriteLine($emit); [Console]::Out.Flush()
      try { Add-Content -LiteralPath $HumanLog -Value $emit -Encoding utf8 } catch {}
    }
    default {
      $emit = "[$($obj.type)]"
      [Console]::Out.WriteLine($emit); [Console]::Out.Flush()
      try { Add-Content -LiteralPath $HumanLog -Value $emit -Encoding utf8 } catch {}
    }
  }
}

function Invoke-AgentIteration {
  param(
    [string]$Cwd,
    [string]$LogFile,        # human-readable log
    [string]$PromptText,
    [int]$TimeoutSec,
    [string]$ModelId = "auto"
  )
  # Streams agent output line-by-line via --output-format stream-json
  # using SYNCHRONOUS ReadLine() on the main thread, which prints each
  # line as it arrives (no event-pumping limitations).
  #
  # Timeout is enforced by a background watchdog job that taskkills
  # the agent process tree after $TimeoutSec. When the agent dies,
  # ReadLine returns $null (EOF) and our main loop exits cleanly.
  #
  # We invoke via cmd.exe because `agent` on Windows is a .cmd shim
  # that re-launches PowerShell.
  $rawJsonl = "$LogFile.jsonl"
  $escPrompt = '"' + ($PromptText -replace '"','\"') + '"'
  $modelArg  = if ($ModelId) { "--model $ModelId " } else { "" }
  $cmdLine = "/c agent -p $modelArg--output-format stream-json --stream-partial-output -f $escPrompt"

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName               = "cmd.exe"
  $psi.Arguments              = $cmdLine
  $psi.WorkingDirectory       = $Cwd
  $psi.UseShellExecute        = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError  = $true
  $psi.CreateNoWindow         = $true
  # Force UTF-8 decoding of stdout/stderr. cursor-agent emits UTF-8
  # (including emoji) but on Chinese Windows the default code page
  # is GB2312, which mangles multi-byte sequences and breaks JSON parsing.
  $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
  $psi.StandardErrorEncoding  = [System.Text.Encoding]::UTF8

  # Also make sure our own console can render the bytes we print.
  try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding  = [System.Text.Encoding]::UTF8
  } catch {}

  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  [void]$proc.Start()

  # Background watchdog: kills the entire process tree after timeout.
  # Runs in its own runspace so it doesn't depend on the main thread
  # pumping events.
  $watchdog = Start-Job -ArgumentList $proc.Id, $TimeoutSec -ScriptBlock {
    param($targetPid, $sec)
    Start-Sleep -Seconds $sec
    & taskkill /F /T /PID $targetPid 2>$null | Out-Null
  }

  $startTime = Get-Date
  try {
    # Synchronous line-by-line read on stdout. ReadLine() blocks per
    # line, returns $null on EOF (which happens after watchdog kills).
    while ($true) {
      $line = $proc.StandardOutput.ReadLine()
      if ($null -eq $line) { break }

      # Persist raw JSON for postmortem / replay.
      try { Add-Content -LiteralPath $rawJsonl -Value $line -Encoding utf8 } catch {}

      # Render to console + human log.
      Write-AgentJsonLine -Line $line -HumanLog $LogFile
    }

    # Drain stderr (small for cursor-agent; safe to read after EOF).
    $errOut = $proc.StandardError.ReadToEnd()
    if ($errOut) {
      foreach ($eline in ($errOut -split "`r?`n")) {
        if (-not [string]::IsNullOrWhiteSpace($eline)) {
          [Console]::Error.WriteLine("[stderr] $eline")
          try { Add-Content -LiteralPath $LogFile -Value "[stderr] $eline" -Encoding utf8 } catch {}
        }
      }
    }

    $proc.WaitForExit() | Out-Null
    $exitCode = $proc.ExitCode

    # If watchdog already completed, it killed us → timeout.
    $elapsed = ((Get-Date) - $startTime).TotalSeconds
    $timedOut = ($elapsed -ge ($TimeoutSec - 1))

    if ($timedOut -or $watchdog.State -eq 'Completed') {
      [Console]::Out.WriteLine("")
      [Console]::Out.WriteLine("  Iteration exceeded $($TimeoutSec)s -- agent process tree killed")
      [Console]::Out.Flush()
      return @{ Completed = $false; ExitCode = -1 }
    }
    return @{ Completed = $true; ExitCode = $exitCode }
  } finally {
    Stop-Job -Job $watchdog -ErrorAction SilentlyContinue
    Remove-Job -Job $watchdog -Force -ErrorAction SilentlyContinue
    if (-not $proc.HasExited) {
      try { & taskkill /F /T /PID $proc.Id 2>&1 | Out-Null } catch {}
    }
  }
}

# ----- Discovery ----------------------------------------------------

$repos = Get-GitRepos -Root $ProjectPath
if ($repos.Count -eq 0) {
  throw "No git repos found at or under $ProjectPath"
}

Write-Host ""
Write-Host "=== ilk-loop runner ===" -ForegroundColor Cyan
Write-Host "Project:        $ProjectPath"
Write-Host "Repos found:    $($repos.Count)"
$repos | ForEach-Object { Write-Host "  - $_" }
Write-Host "Max iterations: $MaxIterations"
Write-Host "Iter timeout:   $IterationTimeoutMin min"
Write-Host "Model:          $Model"
Write-Host "Run logs:       $RunLogDir"
Write-Host "JSONL summary:  $JsonlLog"
Write-Host ""

# ----- Initial check ------------------------------------------------

if (Test-AllShipped -Project $ProjectPath) {
  Write-Host "All sub-plans already shipped. Nothing to do." -ForegroundColor Green
  Write-JsonlRecord -Record @{
    run_id      = $RunId
    iteration   = 0
    timestamp   = (Get-Date).ToString("o")
    project     = $ProjectPath
    stop_reason = "already-shipped"
  }
  return
}

# ----- Main loop ----------------------------------------------------

$stopReason = $null
for ($i = 1; $i -le $MaxIterations; $i++) {
  Write-Host ""
  Write-Host "--- Iteration $i / $MaxIterations ---" -ForegroundColor Yellow

  $iterStart    = Get-Date
  $headsBefore  = Get-RepoHeads -Repos $repos
  $iterLog      = Join-Path $RunLogDir ("iter-{0:D2}.log" -f $i)
  $timeoutSec   = $IterationTimeoutMin * 60

  $result = Invoke-AgentIteration `
    -Cwd $ProjectPath `
    -LogFile $iterLog `
    -PromptText $Prompt `
    -TimeoutSec $timeoutSec `
    -ModelId $Model

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

  $iterStopReason = $null
  if (-not $result.Completed)        { $iterStopReason = "timeout" }
  elseif ($result.ExitCode -ne 0)    { $iterStopReason = "agent-error" }
  elseif ($totalNew -eq 0)           { $iterStopReason = "no-progress" }

  Write-JsonlRecord -Record @{
    run_id            = $RunId
    iteration         = $i
    timestamp         = (Get-Date).ToString("o")
    project           = $ProjectPath
    model             = $Model
    duration_sec      = $iterDurSec
    exit_code         = $result.ExitCode
    completed         = $result.Completed
    new_commits_total = $totalNew
    new_commits       = $newCommits
    log               = $iterLog
    stop_reason       = $iterStopReason
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
  & python $LoopStatusScript
} finally {
  Pop-Location
}
