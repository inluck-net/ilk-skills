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
  # 1. Explicit override — resolve to physical path.
  if [[ -n "${ILK_SKILL_HOME:-}" && -d "$ILK_SKILL_HOME" ]]; then
    cd -P "$ILK_SKILL_HOME" && pwd -P
    return
  fi

  # 2. Auto-detect from caller's path.
  #    Caller is at <skills_dir>/<skill>/scripts/<script>.sh
  #    Walk up to find the directory that contains ilk-* children.
  local caller="${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}"
  local cur
  cur="$(cd -P "$(dirname "$caller")" && pwd -P)"
  local i
  for i in 1 2 3 4 5 6; do
    if [[ "$(basename "$cur")" == "scripts" && "$(basename "$(dirname "$cur")")" == ilk-* ]]; then
      local skills_dir
      skills_dir="$(cd -P "$(dirname "$(dirname "$cur")")" && pwd -P)"
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

  # 3. Fallback candidates — resolve to physical path.
  local candidate
  for candidate in "$HOME/.codex/skills" "$HOME/.cursor/skills" "$HOME/.claude/skills"; do
    if [[ -d "$candidate" ]]; then
      cd -P "$candidate" && pwd -P
      return
    fi
  done

  echo "ilk_skill_root: cannot resolve skill root" >&2
  return 1
}

# ── release-layout / clone-run guard ──────────────────────────────────────

# Print "release" when the layout file exists and its content (whitespace-
# trimmed) is exactly "release"; else print "clone".  Same rule as
# install.sh:910-913.
ilk_host_layout() {
  local releases_root="${ILK_RELEASES_ROOT:-$HOME/.ilk/releases}"
  local releases_parent
  releases_parent="$(dirname "$releases_root")"
  local layout_file="$releases_parent/layout"
  if [[ -f "$layout_file" ]]; then
    local content
    content="$(cat "$layout_file")"
    # Trim leading/trailing whitespace (bash 3.2 compatible).
    content="${content#"${content%%[![:space:]]*}"}"
    content="${content%"${content##*[![:space:]]}"}"
    if [[ "$content" == "release" ]]; then
      echo "release"
      return
    fi
  fi
  echo "clone"
}

# Return 0 when walking up from <dir> finds a .git (file or dir) at a
# shallower level than any .ilk-release.json; 1 otherwise.
# The _ilk_toolkit_head.sh:47-66 rule.
ilk_dir_is_git_tree() {
  local start="$1"
  local cur
  cur="$(cd -P "$start" && pwd -P)" || return 1
  while true; do
    if [[ -e "$cur/.ilk-release.json" ]]; then
      return 1  # release marker found first → not a git tree
    fi
    if [[ -e "$cur/.git" ]]; then
      return 0  # git marker found first → is a git tree
    fi
    local parent
    parent="$(dirname "$cur")"
    [[ "$parent" == "$cur" ]] && break
    cur="$parent"
  done
  return 1  # reached / without finding either
}

# Return 0 (proceed) when ILK_ALLOW_CLONE_RUN=1, or when the layout is not
# "release", or when none of the given dirs is a git tree.
# Otherwise print to stderr and return 3.
ilk_refuse_clone_run_on_release_host() {
  local component="$1"
  shift
  if [[ "${ILK_ALLOW_CLONE_RUN:-}" == "1" ]]; then
    return 0
  fi
  if [[ "$(ilk_host_layout)" != "release" ]]; then
    return 0
  fi
  # Collect dirs to check: positional args + ILK_SKILL_HOME if set.
  local -a dirs=("$@")
  if [[ -n "${ILK_SKILL_HOME:-}" ]]; then
    dirs+=("$ILK_SKILL_HOME")
  fi
  local dir
  for dir in "${dirs[@]}"; do
    if [[ -d "$dir" ]] && ilk_dir_is_git_tree "$dir"; then
      local releases_root="${ILK_RELEASES_ROOT:-$HOME/.ilk/releases}"
      local releases_parent
      releases_parent="$(dirname "$releases_root")"
      echo "$component: refusing: clone-run-on-release-host: this host's ilk layout is release ($releases_parent/layout) but $dir is inside a git working tree; run it through ~/.ilk/current, or set ILK_ALLOW_CLONE_RUN=1" >&2
      return 3
    fi
  done
  return 0
}
