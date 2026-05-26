# Shared helper — dot-source from any ilk-* PowerShell script.
#
# Usage:
#   . (Join-Path $PSScriptRoot "..\..\ilk-loop\scripts\_ilk_skill_root.ps1")
#   $LauncherDir = Join-Path (Get-IlkSkillRoot) "ilk-launcher"
#
# Resolution order:
#   1. ILK_SKILL_HOME env var (absolute path, e.g. ~/.codex/skills)
#   2. Auto-detect from $PSScriptRoot (works for any host)
#   3. First existing of ~/.codex/skills, ~/.cursor/skills, ~/.claude/skills

function Get-IlkSkillRoot {
  # 1. Explicit override
  $envHome = [Environment]::GetEnvironmentVariable("ILK_SKILL_HOME", "Process")
  if (-not $envHome) { $envHome = [Environment]::GetEnvironmentVariable("ILK_SKILL_HOME", "User") }
  if (-not $envHome) { $envHome = [Environment]::GetEnvironmentVariable("ILK_SKILL_HOME", "Machine") }
  if ($envHome -and (Test-Path $envHome)) {
    return (Resolve-Path $envHome).Path
  }

  # 2. Auto-detect from caller's path.
  #    Caller is at <skills_dir>/<skill>/scripts/<script>.ps1
  #    Walk up to find the directory that contains ilk-* children.
  $cur = $PSScriptRoot
  for ($i = 0; $i -lt 6; $i++) {
    if ((Split-Path $cur -Leaf) -eq "scripts" -and (Split-Path (Split-Path $cur -Parent) -Leaf) -like "ilk-*") {
      $skillsDir = Split-Path (Split-Path $cur -Parent) -Parent
      if (Test-Path $skillsDir) {
        return $skillsDir
      }
    }
    $parent = Split-Path $cur -Parent
    if ($parent -eq $cur) { break }
    $cur = $parent
  }

  # 3. Fallback candidates
  $candidates = @(
    (Join-Path $HOME ".codex" "skills"),
    (Join-Path $HOME ".cursor" "skills"),
    (Join-Path $HOME ".claude" "skills")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  throw "Cannot resolve ilk skill root. Set ILK_SKILL_HOME or install skills to ~/.codex/skills, ~/.cursor/skills, or ~/.claude/skills."
}
