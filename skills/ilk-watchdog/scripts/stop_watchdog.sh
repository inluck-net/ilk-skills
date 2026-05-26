#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-watchdog stop (macOS bash port of stop_watchdog.ps1)
# =============================================================================
# Reads ~/.ilk-data/projects/<key>/runtime/watchdog/watchdog.pid and kills the
# process group. Does NOT touch ilk itself.
# =============================================================================

# ----- Skill root resolution -------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# ----- Globals ---------------------------------------------------------------

LAUNCHER_DIR="${_SKILL_ROOT}/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"

CLI_PROJECT_PATH=""
CLI_PROJECT_NAME=""

# ----- Helpers (mirrored from launch.sh) -------------------------------------

read_projects_registry() {
  if [[ ! -f "$PROJECTS_JSON" ]]; then
    echo '[]'
    return
  fi
  python3 -c "import json; data=json.load(open('$PROJECTS_JSON')); print(json.dumps(data.get('projects', [])))"
}

resolve_project_by_name() {
  local name="$1"
  local path
  path=$(python3 -c "
import json
with open('$PROJECTS_JSON') as f:
    data = json.load(f)
for p in data.get('projects', []):
    if p.get('name') == '$name':
        print(p.get('path',''))
        break
")
  if [[ -z "$path" ]]; then
    echo "Project '$name' not in projects.json." >&2
    exit 1
  fi
  echo "$path"
}

resolve_project_by_cwd() {
  local resolver
  resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ -f "$resolver" ]]; then
    local json_out
    if json_out=$(python3 "$resolver" --start "$(pwd)" 2>/dev/null) && [[ -n "$json_out" ]]; then
      local root
      root=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('project_root') or '')" <<<"$json_out")
      if [[ -n "$root" ]]; then
        echo "$root"
        return
      fi
    fi
  fi

  local dir
  dir="$(pwd)"
  while true; do
    if [[ -d "$dir/docs/plans" ]]; then
      local masters
      masters=$(find "$dir/docs/plans" -maxdepth 1 -name 'MASTER-*.md' -print -quit 2>/dev/null)
      if [[ -n "$masters" ]]; then
        echo "$dir"
        return
      fi
    fi
    local parent
    parent="$(dirname "$dir")"
    if [[ "$parent" == "$dir" ]]; then
      break
    fi
    dir="$parent"
  done
  echo "No project found by walking up from $(pwd). Use --project-name or --project-path, or cd into a project." >&2
  exit 1
}

get_ilk_watchdog_dir() {
  local project="$1"
  local resolver
  resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    echo ""
    return
  fi
  local json_out
  if json_out=$(python3 "$resolver" --start "$project" 2>/dev/null) && [[ -n "$json_out" ]]; then
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_watchdog_dir') or '')" <<<"$json_out"
  else
    echo ""
  fi
}

# ----- Stop logic ------------------------------------------------------------

stop_watchdog() {
  local path="$1"
  local name="$2"
  local watchdog_dir
  watchdog_dir=$(get_ilk_watchdog_dir "$path")
  if [[ -z "$watchdog_dir" ]]; then
    echo "[$name] external watchdog dir not resolvable. Ensure ilk_paths.py is present or run the migration script." >&2
    return 1
  fi
  local pid_file="${watchdog_dir}/watchdog.pid"

  if [[ ! -f "$pid_file" ]]; then
    echo "[$name] no watchdog.pid file — nothing to stop." >&2
    return 0
  fi

  local w_pid
  w_pid=$(tr -d '[:space:]' < "$pid_file")
  if [[ -z "$w_pid" ]]; then
    echo "[$name] watchdog PID file empty. Cleaning up." >&2
    rm -f "$pid_file"
    return 0
  fi

  if ! kill -0 "$w_pid" 2>/dev/null; then
    echo "[$name] watchdog PID $w_pid no longer alive. Cleaning stale PID file." >&2
    rm -f "$pid_file"
    return 0
  fi

  echo "[$name] killing watchdog process group $w_pid..." >&2
  # Try the process group first, then the PID directly — covers
  # detached launches where the watchdog isn't a pgrp leader
  # (e.g. plain `nohup ... &` from a non-interactive shell).
  kill -- -"$w_pid" 2>/dev/null || true
  kill -- "$w_pid" 2>/dev/null || true

  # Wait up to 3s for exit
  local waited=0
  while kill -0 "$w_pid" 2>/dev/null && [[ "$waited" -lt 3 ]]; do
    sleep 1
    waited=$((waited + 1))
  done

  if kill -0 "$w_pid" 2>/dev/null; then
    echo "[$name] PID $w_pid still alive after SIGTERM. Sending SIGKILL..." >&2
    kill -9 -- -"$w_pid" 2>/dev/null || true
    kill -9 -- "$w_pid" 2>/dev/null || true
    sleep 1
  fi

  if kill -0 "$w_pid" 2>/dev/null; then
    echo "[$name] PID $w_pid still alive after SIGKILL. Investigate manually." >&2
  else
    echo "[$name] watchdog stopped. ilk (if running) is unaffected." >&2
    rm -f "$pid_file"
  fi
}

# ----- Argument parsing ------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: stop_watchdog.sh [OPTIONS]

Stop the watchdog for a project. Does NOT touch ilk itself.

Options:
  --project-path PATH    Absolute path to project root.
  --project-name NAME    Look up path in projects.json.
  -h, --help             Show this help and exit.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --project-path)
        CLI_PROJECT_PATH="$2"
        shift 2
        ;;
      --project-name)
        CLI_PROJECT_NAME="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done
}

# ----- Main ------------------------------------------------------------------

main() {
  parse_args "$@"

  local resolved_path resolved_name
  if [[ -n "$CLI_PROJECT_PATH" ]]; then
    resolved_path="$CLI_PROJECT_PATH"
  elif [[ -n "$CLI_PROJECT_NAME" ]]; then
    resolved_path=$(resolve_project_by_name "$CLI_PROJECT_NAME")
  else
    resolved_path=$(resolve_project_by_cwd)
  fi

  if [[ ! -d "$resolved_path" ]]; then
    echo "ProjectPath '$resolved_path' does not exist." >&2
    exit 1
  fi

  resolved_path="$(cd "$resolved_path" && pwd)"

  if [[ -n "$CLI_PROJECT_NAME" ]]; then
    resolved_name="$CLI_PROJECT_NAME"
  else
    resolved_name=$(basename "$resolved_path")
  fi

  stop_watchdog "$resolved_path" "$resolved_name"
}

main "$@"
