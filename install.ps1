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
  Skip ~/.claude/ even if Claude Code is installed.

.PARAMETER OnlyClaude
  Skip ~/.cursor/ even if Cursor is installed.

.PARAMETER Force
  Replace existing TARGETS that are already real directories or files
  (after backing them up to <target>.pre-ilk-<timestamp>). Without
  -Force the script refuses to clobber real content and reports it
  for human triage. Symlinks/junctions to other locations are always
  replaced (low-risk).

.EXAMPLE
  .\install.ps1
  Dry run from the repo root.

.EXAMPLE
  .\install.ps1 -Apply
  Install / refresh links.

.EXAMPLE
  .\install.ps1 -Apply -OnlyClaude
  Only link into ~/.claude/.

.NOTES
  Idempotent. Re-running -Apply just re-points stale symlinks (e.g.
  if you moved the repo) and is otherwise a no-op.
#>
[CmdletBinding()]
param(
  [switch]$Apply,
  [switch]$OnlyCursor,
  [switch]$OnlyClaude,
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSCommandPath
$SkillsSrc = Join-Path $RepoRoot "skills"
$CommandsSrc = Join-Path $RepoRoot "commands"

if (-not (Test-Path $SkillsSrc)) {
  throw "Cannot find skills/ under repo root: $RepoRoot"
}

# Targets in priority order. Filtered later by -OnlyCursor / -OnlyClaude.
$Targets = @()
if (-not $OnlyClaude) {
  $Targets += [PSCustomObject]@{
    Name = "Cursor"
    SkillsDir = (Join-Path $HOME ".cursor\skills")
    CommandsDir = (Join-Path $HOME ".cursor\commands")
  }
}
if (-not $OnlyCursor) {
  $Targets += [PSCustomObject]@{
    Name = "Claude Code"
    SkillsDir = (Join-Path $HOME ".claude\skills")
    CommandsDir = (Join-Path $HOME ".claude\commands")
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
  # File symlink — needs admin OR developer mode
  try {
    New-Item -ItemType SymbolicLink -Path $Link -Target $Source -Force -ErrorAction Stop | Out-Null
    return "symlink"
  } catch {
    Copy-Item -LiteralPath $Source -Destination $Link -Force -ErrorAction Stop
    return "copy-fallback"
  }
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

Write-Host "Done." -ForegroundColor Green
