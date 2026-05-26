#!/usr/bin/env bash
# Shared helper — source from any ilk-* bash script to resolve paths.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/_ilk_skill_root.sh"
#   LAUNCHER_DIR="$(ilk_skill_root)/ilk-launcher"
#
# Resolution order:
#   1. ILK_SKILL_HOME env var (absolute path, e.g. ~/.codex/skills)
#   2. Auto-detect from BASH_SOURCE (works for any host)
#   3. First existing of ~/.codex/skills, ~/.cursor/skills, ~/.claude/skills

ilk_skill_root() {
  # 1. Explicit override
  if [[ -n "${ILK_SKILL_HOME:-}" && -d "$ILK_SKILL_HOME" ]]; then
    echo "$ILK_SKILL_HOME"
    return
  fi

  # 2. Auto-detect from caller's path.
  #    Caller is at <skills_dir>/<skill>/scripts/<script>.sh
  #    Walk up to find the directory that contains ilk-* children.
  local caller="${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}"
  local cur
  cur="$(cd "$(dirname "$caller")" && pwd)"
  local i
  for i in 1 2 3 4 5 6; do
    if [[ "$(basename "$cur")" == "scripts" && "$(basename "$(dirname "$cur")")" == ilk-* ]]; then
      local skills_dir
      skills_dir="$(dirname "$(dirname "$cur")")"
      if [[ -d "$skills_dir" ]]; then
        echo "$skills_dir"
        return
      fi
    fi
    local parent
    parent="$(dirname "$cur")"
    [[ "$parent" == "$cur" ]] && break
    cur="$parent"
  done

  # 3. Fallback candidates
  local candidate
  for candidate in "$HOME/.codex/skills" "$HOME/.cursor/skills" "$HOME/.claude/skills"; do
    if [[ -d "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done

  echo "ilk_skill_root: cannot resolve skill root" >&2
  return 1
}
