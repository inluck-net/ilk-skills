<#
.SYNOPSIS
  Install (or update) the ilk-skills suite on Windows by creating
  symlinks from the user's Cursor / Claude Code directories into this
  repository.

.DESCRIPTION
  Single source of truth lives in this repo. The install script makes
  Cursor and Claude Code see the latest version by linking:

    ~/.cursor/skills/<name>   ->  <repo>/skills/<name>
    ~/.cursor/commands/<file> ->  <repo>/commands/<file>
    ~/.claude/skills/<name>   ->  <repo>/skills/<name>
    ~/.claude/commands/<file> ->  <repo>/commands/<file>
    ~/.codex/skills/<name>    ->  <repo>/skills/<name>
    ~/.codex/commands/<file>  ->  <repo>/commands/<file>

  Each link is a junction (skills directories) or a symlink (single
  command files). Junctions do not require admin privileges; file
  symlinks DO unless Developer Mode is on. The script falls back to
  copy-with-warning for command files when symlink creation fails.

  Default mode is dry-run: prints what would happen but touches
  nothing. Pass -Apply to execute.

.PARAMETER Apply
  Perform the operation. Without this switch the script prints the
  plan only.

.PARAMETER OnlyCursor
  Install only to ~/.cursor/.

.PARAMETER OnlyClaude
  Install only to ~/.claude/.

.PARAMETER OnlyCodex
  Install only to ~/.codex/.

.PARAMETER ClaudeHome
  Use this directory as the Claude Code home instead of the default
  %USERPROFILE%\.claude (for example a worker home
  %USERPROFILE%\.claude-worker). Targets <dir>\skills, <dir>\commands,
  and <dir>\tools\migration. A leading ~ is expanded and relative paths
  are made absolute; the directory does not need to exist yet.

.PARAMETER Force
  Replace existing TARGETS that are already real directories or files
  (after backing them up to <target>.pre-ilk-<timestamp>). Without
  -Force the script refuses to clobber real content and reports it
  for human triage. Symlinks/junctions to other locations are always
  replaced (low-risk).

.PARAMETER InstallPath
  Also install a claude-worker.cmd shim onto PATH (in addition to the
  normal skill/command install). The shim is a generated .cmd that
  forwards to the repo's tools\claude-worker\claude-worker.ps1 via an
  absolute path.

.PARAMETER OnlyPath
  Install ONLY the claude-worker.cmd PATH entry; skip all skill/command
  linking.

.PARAMETER PathBinDir
  Target bin directory for the PATH entry. Default: $HOME\bin.

.PARAMETER AutoUseIlkPlan
  Set auto_use_ilk_plan: true in conventions\config.yml (the
  git-propagated opt-in for auto-plan routing). Implies the managed
  block will be reconciled in the same run.

.PARAMETER OnlyAutoPlan
  Reconcile ONLY the auto-plan managed block into host agent files
  (no skill/command linking). Used by /ilk-upgrade after git pull.

.EXAMPLE
  .\install.ps1
  Dry run from the repo root.

.EXAMPLE
  .\install.ps1 -Apply
  Install / refresh links.

.EXAMPLE
  .\install.ps1 -Apply -OnlyClaude
  Only link into ~/.claude/.

.EXAMPLE
  .\install.ps1 -OnlyCodex
  Dry run for Codex only.

.EXAMPLE
  .\install.ps1 -Apply -OnlyClaude -ClaudeHome "$HOME\.claude-worker"
  Install only into a custom worker Claude home
  (%USERPROFILE%\.claude-worker\skills, \commands, \tools\migration).

.EXAMPLE
  .\install.ps1 -Apply -OnlyPath
  Install only the claude-worker.cmd shim into $HOME\bin.

.EXAMPLE
  .\install.ps1 -Apply -InstallPath -PathBinDir "C:\tools"
  Install skills/commands AND the claude-worker shim into C:\tools.

.NOTES
  Idempotent. Re-running -Apply just re-points stale symlinks (e.g.
  if you moved the repo) and is otherwise a no-op.
#>
[CmdletBinding()]
param(
  [switch]$Apply,
  [switch]$OnlyCursor,
  [switch]$OnlyClaude,
  [switch]$OnlyCodex,
  [string]$ClaudeHome,
  [switch]$Force,
  [switch]$InstallPath,
  [switch]$OnlyPath,
  [string]$PathBinDir,
  [switch]$AutoUseIlkPlan,
  [switch]$OnlyAutoPlan
)

$ErrorActionPreference = "Stop"

# Normalize a custom Claude home: expand a leading ~ and make relative
# paths absolute. Conservative — does NOT require the directory to exist
# yet, so a dry-run can preview a not-yet-created worker home.
if ($ClaudeHome) {
  if ($ClaudeHome -eq '~') {
    $ClaudeHome = $HOME
  } elseif ($ClaudeHome -match '^~[\\/]') {
    $ClaudeHome = Join-Path $HOME $ClaudeHome.Substring(2)
  }
  if (-not [System.IO.Path]::IsPathRooted($ClaudeHome)) {
    $ClaudeHome = Join-Path (Get-Location).Path $ClaudeHome
  }
}

$RepoRoot = Split-Path -Parent $PSCommandPath
$SkillsSrc = Join-Path $RepoRoot "skills"
$CommandsSrc = Join-Path $RepoRoot "commands"

if (-not (Test-Path $SkillsSrc)) {
  throw "Cannot find skills/ under repo root: $RepoRoot"
}

# An -OnlyX flag selects exactly one target; when none are set, all
# targets are included.
$anyOnly = $OnlyCursor -or $OnlyClaude -or $OnlyCodex

$Targets = @()
if (-not $anyOnly -or $OnlyCursor) {
  $Targets += [PSCustomObject]@{
    Name = "Cursor"
    SkillsDir = (Join-Path $HOME ".cursor\skills")
    CommandsDir = (Join-Path $HOME ".cursor\commands")
  }
}
if (-not $anyOnly -or $OnlyClaude) {
  if ($ClaudeHome) {
    $Targets += [PSCustomObject]@{
      Name = "Claude Code [$ClaudeHome]"
      SkillsDir = (Join-Path $ClaudeHome "skills")
      CommandsDir = (Join-Path $ClaudeHome "commands")
    }
  } else {
    $Targets += [PSCustomObject]@{
      Name = "Claude Code"
      SkillsDir = (Join-Path $HOME ".claude\skills")
      CommandsDir = (Join-Path $HOME ".claude\commands")
    }
  }
}
if (-not $anyOnly -or $OnlyCodex) {
  $Targets += [PSCustomObject]@{
    Name = "Codex"
    SkillsDir = (Join-Path $HOME ".codex\skills")
    CommandsDir = (Join-Path $HOME ".codex\commands")
  }
}

function Test-IsLink {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return $false }
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  if (-not $item) { return $false }
  return ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq [IO.FileAttributes]::ReparsePoint
}

function Get-LinkTarget {
  param([string]$Path)
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  if (-not $item) { return $null }
  return $item.Target
}

function Plan-Link {
  <#
    Decide what to do for one (link, source) pair.
    Returns: action ∈ { skip-correct, replace-stale-link, replace-real, blocked-real, create }
  #>
  param([string]$Link, [string]$Source)
  if (-not (Test-Path $Link)) { return "create" }
  if (Test-IsLink -Path $Link) {
    $cur = Get-LinkTarget -Path $Link
    if ($cur -and ((Resolve-Path $cur -ErrorAction SilentlyContinue).Path -ieq (Resolve-Path $Source).Path)) {
      return "skip-correct"
    }
    return "replace-stale-link"
  }
  if ($Force) { return "replace-real" }
  return "blocked-real"
}

function Apply-Action {
  param(
    [string]$Action, [string]$Link, [string]$Source, [string]$Type
  )
  switch ($Action) {
    "skip-correct" { return "noop" }

    "replace-stale-link" {
      Remove-Item -LiteralPath $Link -Force -Recurse -ErrorAction Stop
      return New-Link -Link $Link -Source $Source -Type $Type
    }
    "replace-real" {
      $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss')
      $backup = "$Link.pre-ilk-$stamp"
      Move-Item -LiteralPath $Link -Destination $backup -Force -ErrorAction Stop
      $r = New-Link -Link $Link -Source $Source -Type $Type
      return ("backed-up:" + $backup + ";" + $r)
    }
    "blocked-real" {
      return "blocked"
    }
    "create" {
      return New-Link -Link $Link -Source $Source -Type $Type
    }
  }
}

function New-Link {
  param([string]$Link, [string]$Source, [string]$Type)
  $parent = Split-Path -Parent $Link
  if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  if ($Type -eq "junction") {
    # Directory junction — no admin needed
    New-Item -ItemType Junction -Path $Link -Target $Source -Force | Out-Null
    return "junction"
  }
  # File symlink — needs admin OR Developer Mode.
  #
  # `New-Item -ItemType SymbolicLink` (PowerShell cmdlet) does NOT pass
  # the SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE flag to the Win32
  # API, so it still requires admin even when Developer Mode is on. The
  # built-in `mklink` (cmd.exe) DOES pass the flag and works in Dev Mode
  # without admin. Use it directly via `cmd /c`.
  #
  # Background: https://github.com/PowerShell/PowerShell/issues/12858
  # ("New-Item -ItemType SymbolicLink does not work with Developer
  # Mode") — open since 2020, no fix in PS 7.x at time of writing.
  if (Test-Path -LiteralPath $Link) {
    Remove-Item -LiteralPath $Link -Force -ErrorAction SilentlyContinue
  }
  & cmd /c mklink "$Link" "$Source" 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0 -and (Test-IsLink -Path $Link)) {
    return "symlink"
  }
  # Last-resort fallback: plain copy. Triggered when neither admin nor
  # Developer Mode is available — the link won't auto-track repo edits,
  # so a re-run of `install.ps1 -Apply -Force` is needed after every
  # command-file change. Users hit by this should enable Developer Mode
  # (Settings → Privacy & Security → For developers → Developer Mode).
  Copy-Item -LiteralPath $Source -Destination $Link -Force -ErrorAction Stop
  return "copy-fallback"
}

# ---- PATH entry for claude-worker -------------------------------------------
# Generates a .cmd shim that forwards to the repo's claude-worker.ps1 via
# an absolute path. The shim must NOT use %~dp0 (see design note in the
# sub-plan). Written as ASCII so cmd.exe and bash grep can both read it.

$ClaudeWorkerSrc = Join-Path $RepoRoot "tools\claude-worker\claude-worker.ps1"

function Install-PathEntry {
  param([string]$BinDir)
  $link = Join-Path $BinDir "claude-worker.cmd"

  if (-not (Test-Path $ClaudeWorkerSrc)) {
    throw "claude-worker source not found: $ClaudeWorkerSrc"
  }

  $entryMode = if ($Apply) { "APPLY" } else { "DRY-RUN" }
  Write-Host "=== claude-worker PATH entry ($entryMode) ==="
  Write-Host "source:    $ClaudeWorkerSrc"
  Write-Host "target:    $link"

  if (-not $Apply) {
    Write-Host "(dry-run: not writing)"
    return
  }

  # Check current state: already correct?
  $needsWrite = $true
  if (Test-Path $link) {
    $existing = Get-Content -LiteralPath $link -Raw -ErrorAction SilentlyContinue
    # The shim body references the absolute .ps1 path; compare case-insensitively
    if ($existing -and ($existing -like "*$ClaudeWorkerSrc*")) {
      Write-Host "noop: $link already points to the correct source"
      $needsWrite = $false
    } else {
      if (-not $Force) {
        throw "BLOCKED: $link exists and does not reference the expected source (re-run with -Force to back up)"
      }
      $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
      $backup = "$link.pre-ilk-$stamp"
      Move-Item -LiteralPath $link -Destination $backup -Force
      Write-Host "backed up: $link -> $backup"
    }
  }

  if ($needsWrite) {
    if (-not (Test-Path $BinDir)) {
      New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    }
    # Generate the .cmd shim body. Uses the absolute repo path so %~dp0 is
    # never involved. Written as ASCII (no BOM, no null bytes).
    $shimBody = @"
@echo off
REM Generated by install.ps1 -InstallPath. Forwards to the repo launcher.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ClaudeWorkerSrc" %*
"@
    Set-Content -LiteralPath $link -Value $shimBody -Encoding ascii -NoNewline
    Write-Host "created: $link"
  }

  # Warn if BinDir is not on PATH
  $pathDirs = $env:PATH -split ';' | Where-Object { $_ }
  $onPath = $false
  foreach ($dir in $pathDirs) {
    if ($dir.TrimEnd('\') -ieq $BinDir.TrimEnd('\')) {
      $onPath = $true
      break
    }
  }
  if (-not $onPath) {
    Write-Host ""
    Write-Host "WARNING: $BinDir is not on your PATH."
    Write-Host "Add it by running:"
    Write-Host ""
    Write-Host "  setx PATH `"$env:PATH;$BinDir`""
    Write-Host ""
    Write-Host "To make it permanent, add that line to your system environment variables."
  }
}

# ---- auto-plan routing helpers -----------------------------------------------

function Read-AutoPlanPref {
  <# Read the auto_use_ilk_plan boolean from conventions\config.yml.
     Returns $true or $false; defaults to $false if the key is absent. #>
  $cfg = Join-Path $RepoRoot "conventions\config.yml"
  if (Test-Path $cfg) {
    $content = Get-Content -LiteralPath $cfg -Raw -ErrorAction SilentlyContinue
    if ($content -match 'auto_use_ilk_plan:\s*true') { return $true }
  }
  return $false
}

function Set-AutoPlanPref {
  <# Set auto_use_ilk_plan in conventions\config.yml (idempotent). #>
  param([bool]$Value)
  $cfg = Join-Path $RepoRoot "conventions\config.yml"
  if (-not (Test-Path $cfg)) {
    throw "conventions\config.yml not found"
  }
  $content = Get-Content -LiteralPath $cfg -Raw
  $content = $content -replace 'auto_use_ilk_plan:.*', "auto_use_ilk_plan: $Value"
  Set-Content -LiteralPath $cfg -Value $content -Encoding ascii -NoNewline
}

function Render-AutoPlanBlock {
  <# Render the managed block content: marker-wrapped contents of
     conventions\auto-plan-routing.md.  Returns a string. #>
  $snippet = Join-Path $RepoRoot "conventions\auto-plan-routing.md"
  if (-not (Test-Path $snippet)) {
    throw "conventions\auto-plan-routing.md not found"
  }
  $body = Get-Content -LiteralPath $snippet -Raw
  return "<!-- ilk:auto-plan:start -->`n$body<!-- ilk:auto-plan:end -->"
}

function Upsert-Block {
  <# Insert-or-replace a delimited block in a file (idempotent).
     If markers exist, replaces block between them. Otherwise appends. #>
  param([string]$File, [string]$StartMarker, [string]$EndMarker, [string]$Block)
  $parent = Split-Path -Parent $File
  if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  if (Test-Path $File) {
    $lines = Get-Content -LiteralPath $File -ErrorAction SilentlyContinue
    if ($lines -contains $StartMarker) {
      $out = @()
      $skip = $false
      foreach ($line in $lines) {
        if ($line -eq $StartMarker) { $skip = $true; $out += $Block; continue }
        if ($line -eq $EndMarker) { $skip = $false; continue }
        if (-not $skip) { $out += $line }
      }
      Set-Content -LiteralPath $File -Value ($out -join "`n") -Encoding ascii -NoNewline
      return
    }
    # No markers — append
    $existing = Get-Content -LiteralPath $File -Raw -ErrorAction SilentlyContinue
    if ($existing -and $existing.Trim().Length -gt 0) {
      Add-Content -LiteralPath $File -Value "`n$Block" -Encoding ascii
    } else {
      Set-Content -LiteralPath $File -Value $Block -Encoding ascii -NoNewline
    }
  } else {
    Set-Content -LiteralPath $File -Value $Block -Encoding ascii -NoNewline
  }
}

function Strip-Block {
  <# Strip a delimited block from a file (inclusive of markers).
     Leaves surrounding content byte-for-byte intact. #>
  param([string]$File, [string]$StartMarker, [string]$EndMarker)
  if (-not (Test-Path $File)) { return }
  $lines = Get-Content -LiteralPath $File -ErrorAction SilentlyContinue
  if (-not ($lines -contains $StartMarker)) { return }
  $out = @()
  $skip = $false
  foreach ($line in $lines) {
    if ($line -eq $StartMarker) { $skip = $true; continue }
    if ($line -eq $EndMarker) { $skip = $false; continue }
    if (-not $skip) { $out += $line }
  }
  Set-Content -LiteralPath $File -Value ($out -join "`n") -Encoding ascii -NoNewline
}

function Reconcile-AutoPlan {
  <# Reconcile the auto-plan managed block into each host agent's
     user-global instructions.  Respects -Apply, -OnlyCursor/Claude/Codex,
     and the committed preference. #>
  $pref = Read-AutoPlanPref
  $mode = if ($Apply) { "APPLY" } else { "DRY-RUN" }

  Write-Host "=== auto-plan reconcile ($mode) ==="
  Write-Host "preference: auto_use_ilk_plan=$pref"

  $block = $null
  if ($pref) {
    $block = Render-AutoPlanBlock
  }

  $startMarker = "<!-- ilk:auto-plan:start -->"
  $endMarker = "<!-- ilk:auto-plan:end -->"

  # Determine which homes to reconcile into
  $homes = @()
  $homeNames = @()
  if (-not $anyOnly -or $OnlyCursor) {
    $homes += $HOME; $homeNames += "Cursor"
  }
  if (-not $anyOnly -or $OnlyClaude) {
    if ($ClaudeHome) {
      $homes += $ClaudeHome; $homeNames += "Claude Code [$ClaudeHome]"
    } else {
      $homes += $HOME; $homeNames += "Claude Code"
    }
  }
  if (-not $anyOnly -or $OnlyCodex) {
    $homes += $HOME; $homeNames += "Codex"
  }

  # Reconcile shared files (CLAUDE.md, AGENTS.md)
  $sharedFiles = @()
  $sharedLabels = @()
  for ($i = 0; $i -lt $homes.Count; $i++) {
    $name = $homeNames[$i]
    switch -Wildcard ($name) {
      "Cursor*"   { } # Cursor uses .mdc, not a shared file
      "Claude*"   { $sharedFiles += (Join-Path $homes[$i] ".claude\CLAUDE.md"); $sharedLabels += $name }
      "Codex*"    { $sharedFiles += (Join-Path $homes[$i] ".codex\AGENTS.md"); $sharedLabels += $name }
    }
  }

  for ($i = 0; $i -lt $sharedFiles.Count; $i++) {
    $f = $sharedFiles[$i]
    $label = $sharedLabels[$i]
    if ($pref) {
      if ($Apply) {
        Upsert-Block -File $f -StartMarker $startMarker -EndMarker $endMarker -Block $block
        Write-Host "[ok] reconciled block -> $f ($label)" -ForegroundColor Green
      } else {
        Write-Host "(dry-run: would reconcile block -> $f ($label))"
      }
    } else {
      if ((Test-Path $f) -and (Get-Content -LiteralPath $f -ErrorAction SilentlyContinue) -contains $startMarker) {
        if ($Apply) {
          Strip-Block -File $f -StartMarker $startMarker -EndMarker $endMarker
          Write-Host "[ok] removed block from $f ($label)" -ForegroundColor Green
        } else {
          Write-Host "(dry-run: would remove block from $f ($label))"
        }
      }
    }
  }

  # Reconcile dedicated .mdc file for Cursor
  if (-not $anyOnly -or $OnlyCursor) {
    $mdc = Join-Path $HOME ".cursor\rules\ilk-auto-plan.mdc"
    if ($pref) {
      if ($Apply) {
        $mdcParent = Split-Path -Parent $mdc
        if (-not (Test-Path $mdcParent)) {
          New-Item -ItemType Directory -Path $mdcParent -Force | Out-Null
        }
        Copy-Item -LiteralPath (Join-Path $RepoRoot "conventions\auto-plan-routing.md") -Destination $mdc -Force
        Write-Host "[ok] wrote $mdc (Cursor)" -ForegroundColor Green
      } else {
        Write-Host "(dry-run: would write $mdc (Cursor))"
      }
    } else {
      if (Test-Path $mdc) {
        if ($Apply) {
          Remove-Item -LiteralPath $mdc -Force
          Write-Host "[ok] deleted $mdc (Cursor)" -ForegroundColor Green
        } else {
          Write-Host "(dry-run: would delete $mdc (Cursor))"
        }
      }
    }
  }
}

# Default bin dir for PATH entry
if (-not $PathBinDir) {
  $PathBinDir = Join-Path $HOME "bin"
}

# Normalize PathBinDir: expand ~ and make relative paths absolute
if ($PathBinDir -eq '~') {
  $PathBinDir = $HOME
} elseif ($PathBinDir -match '^~[\\/]') {
  $PathBinDir = Join-Path $HOME $PathBinDir.Substring(2)
}
if (-not [System.IO.Path]::IsPathRooted($PathBinDir)) {
  $PathBinDir = Join-Path (Get-Location).Path $PathBinDir
}

# --OnlyPath: install ONLY the PATH entry, skip all skill/command linking
if ($OnlyPath) {
  Install-PathEntry -BinDir $PathBinDir
  return
}

# --AutoUseIlkPlan: set the committed preference to true (then continue
# to the normal plan/apply flow so the block reconcile happens in the same run).
if ($AutoUseIlkPlan) {
  if ($Apply) {
    Set-AutoPlanPref -Value $true
    Write-Host "Set auto_use_ilk_plan: true in conventions\config.yml"
  } else {
    Write-Host "(dry-run: would set auto_use_ilk_plan: true in conventions\config.yml)"
  }
}

# --OnlyAutoPlan: reconcile ONLY the auto-plan managed block (skip all
# skill/command linking).  Used by /ilk-upgrade to refresh the block after
# a git pull without touching symlinks.
if ($OnlyAutoPlan) {
  Reconcile-AutoPlan
  return
}

# ---- build plan ------------------------------------------------------------

$skillNames = (Get-ChildItem -Directory $SkillsSrc | Where-Object { $_.Name -like "ilk-*" } | Select-Object -ExpandProperty Name)
$commandFiles = (Get-ChildItem -File $CommandsSrc | Where-Object { $_.Name -like "ilk*" } | Select-Object -ExpandProperty Name)

$rows = New-Object System.Collections.Generic.List[object]
foreach ($t in $Targets) {
  foreach ($name in $skillNames) {
    $rows.Add([PSCustomObject]@{
      Target = $t.Name
      Type = "junction"
      Link = (Join-Path $t.SkillsDir $name)
      Source = (Join-Path $SkillsSrc $name)
      Action = Plan-Link -Link (Join-Path $t.SkillsDir $name) -Source (Join-Path $SkillsSrc $name)
    })
  }
  foreach ($f in $commandFiles) {
    $rows.Add([PSCustomObject]@{
      Target = $t.Name
      Type = "symlink"
      Link = (Join-Path $t.CommandsDir $f)
      Source = (Join-Path $CommandsSrc $f)
      Action = Plan-Link -Link (Join-Path $t.CommandsDir $f) -Source (Join-Path $CommandsSrc $f)
    })
  }
}

# ---- print plan ------------------------------------------------------------

$counts = @{}
foreach ($r in $rows) {
  if (-not $counts.ContainsKey($r.Action)) { $counts[$r.Action] = 0 }
  $counts[$r.Action]++
}

$mode = if ($Apply) { "APPLY" } else { "DRY-RUN" }
Write-Host "=== ilk-skills install ($mode) ==="
Write-Host "repo:           $RepoRoot"
if ($ClaudeHome) { Write-Host "claude home:    $ClaudeHome (custom)" }
Write-Host "skills found:   $($skillNames.Count) (ilk-*)"
Write-Host "commands found: $($commandFiles.Count) (ilk*)"
Write-Host "targets:        $((@($Targets | ForEach-Object { $_.Name })) -join ', ')"
Write-Host "actions:        $((@($counts.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })) -join ' ')"
Write-Host ""
$rows | Format-Table -AutoSize Target, Type, Action, Link

$blocked = $rows | Where-Object { $_.Action -eq "blocked-real" }
if ($blocked) {
  Write-Host "BLOCKED on real content at these paths (would clobber non-symlink dirs/files):" -ForegroundColor Red
  $blocked | ForEach-Object { Write-Host "  - $($_.Link)" -ForegroundColor Red }
  Write-Host "Re-run with -Force to back them up to <link>.pre-ilk-<timestamp> before linking." -ForegroundColor Yellow
}

if (-not $Apply) {
  Write-Host ""
  Write-Host "Dry-run complete. Re-run with -Apply to install." -ForegroundColor Cyan
  return
}

if ($blocked -and -not $Force) {
  Write-Host ""
  Write-Host "Aborting: blocked entries above. Re-run with -Force or remove the targets manually." -ForegroundColor Red
  exit 4
}

# ---- execute ---------------------------------------------------------------

$results = @{}
foreach ($r in $rows) {
  if ($r.Action -eq "skip-correct") {
    $results["noop"] = ($results["noop"] + 1)
    continue
  }
  try {
    $outcome = Apply-Action -Action $r.Action -Link $r.Link -Source $r.Source -Type $r.Type
    $key = if ($outcome -like "backed-up:*") { "backed-up" } else { $outcome }
    $results[$key] = ($results[$key] + 1)
    Write-Host ("[ok] {0,-12} {1}" -f $outcome, $r.Link) -ForegroundColor Green
  } catch {
    $results["error"] = ($results["error"] + 1)
    Write-Host ("[ERR] {0}: {1}" -f $r.Link, $_.Exception.Message) -ForegroundColor Red
  }
}

Write-Host ""
Write-Host "Results: $((@($results.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })) -join ' ')"

# ---- symlink tools/migration (migrate_project_runtime_dirs.py et al.) ------
$toolsMigrationSrc = Join-Path $RepoRoot "tools\migration"
foreach ($t in $Targets) {
  $toolsParent = Split-Path -Parent $t.SkillsDir
  $toolsDir = Join-Path $toolsParent "tools"
  if (-not (Test-Path $toolsDir)) {
    New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
  }
  $toolLink = Join-Path $toolsDir "migration"
  if (Test-Path $toolLink) {
    Remove-Item -LiteralPath $toolLink -Force -Recurse -ErrorAction SilentlyContinue
  }
  New-Item -ItemType Junction -Path $toolLink -Target $toolsMigrationSrc -Force | Out-Null
  Write-Host ("[ok] {0,-12} {1}" -f "junction", $toolLink) -ForegroundColor Green
}

# ---- bootstrap projects.json from example ----------------------------------
# projects.json is gitignored (per-operator paths). Seed it from the
# example on first install so the launcher works out of the box; never
# overwrite an existing one.
$projectsJson = Join-Path $SkillsSrc "ilk-launcher\projects.json"
$projectsExample = Join-Path $SkillsSrc "ilk-launcher\projects.example.json"
if ((Test-Path $projectsExample) -and -not (Test-Path $projectsJson)) {
  Copy-Item $projectsExample $projectsJson
  Write-Host ""
  Write-Host "Created: $projectsJson (from projects.example.json)" -ForegroundColor Cyan
  Write-Host "Edit it to point at your real projects before using launch.ps1 -All." -ForegroundColor Cyan
}

# ---- PATH entry for --InstallPath (additive) --------------------------------
if ($InstallPath) {
  Write-Host ""
  Install-PathEntry -BinDir $PathBinDir
}

# Auto-plan managed block reconcile (always runs in the normal -Apply path;
# idempotent — no-op when the preference is off and no stale block exists).
Write-Host ""
Reconcile-AutoPlan

Write-Host ""
Write-Host "Done." -ForegroundColor Green
