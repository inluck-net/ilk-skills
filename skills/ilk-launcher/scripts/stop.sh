#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-launcher stop (macOS bash port of stop.ps1)
# =============================================================================
# Reads the PID file from the external launcher dir (resolved via
# ilk_paths.py), kills the process group, and removes the PID file.
# =============================================================================

# ----- Skill root resolution -------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# ----- Globals ---------------------------------------------------------------

LAUNCHER_DIR="${_SKILL_ROOT}/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"

CLI_PROJECT_PATH=""
CLI_PROJECT_NAME=""
CLI_ALL=false

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

get_external_launcher_dir() {
  local project_path="$1"
  local resolver
  resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    echo ""
    return
  fi
  local json_out
  if json_out=$(python3 "$resolver" --start "$project_path" 2>/dev/null) && [[ -n "$json_out" ]]; then
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_launcher_dir') or '')" <<<"$json_out"
  else
    echo ""
  fi
}

get_pid_file_path() {
  local project_path="$1"
  local launcher_dir
  launcher_dir=$(get_external_launcher_dir "$project_path")
  if [[ -z "$launcher_dir" ]]; then
    echo ""
    return
  fi
  echo "${launcher_dir}/running.pid"
}

mark_sentinel_interrupted() {
  local project_path="$1"
  local stopped_pid="$2"
  local launcher_dir
  launcher_dir=$(get_external_launcher_dir "$project_path")
  if [[ -z "$launcher_dir" ]]; then
    return
  fi
  local runtime_dir
  runtime_dir="$(dirname "$launcher_dir")"
  local marker="${_SKILL_ROOT}/ilk-launcher/scripts/mark_sentinel_interrupted.sh"
  if [[ -f "$marker" ]]; then
    bash "$marker" "$runtime_dir" "$stopped_pid" 2>/dev/null || true
  fi
}

# ----- Stop logic ------------------------------------------------------------

stop_project() {
  local path="$1"
  local name="$2"
  local pid_file
  pid_file=$(get_pid_file_path "$path")

  if [[ ! -f "$pid_file" ]]; then
    echo "[$name] no running ilk for this project" >&2
    return 0
  fi

  local target_pid
  target_pid=$(cat "$pid_file" | tr -d '[:space:]')
  if [[ -z "$target_pid" ]]; then
    echo "[$name] PID file empty. Cleaning up." >&2
    rm -f "$pid_file"
    return 0
  fi

  if ! kill -0 "$target_pid" 2>/dev/null; then
    echo "[$name] PID $target_pid no longer alive. Cleaning stale PID file." >&2
    mark_sentinel_interrupted "$path" "$target_pid"
    rm -f "$pid_file"
    return 0
  fi

  echo "[$name] killing process group $target_pid..." >&2
  # Try the process group first (kills the whole tree if the launcher
  # spawned it as a pgrp leader), then the PID directly (recovers
  # orphans from older launches that weren't pgrp leaders).
  kill -- -"$target_pid" 2>/dev/null || true
  kill -- "$target_pid" 2>/dev/null || true

  # Wait up to 5s for the process group to exit
  local waited=0
  while kill -0 "$target_pid" 2>/dev/null && [[ "$waited" -lt 5 ]]; do
    sleep 1
    waited=$((waited + 1))
  done

  if kill -0 "$target_pid" 2>/dev/null; then
    echo "[$name] PID $target_pid still alive after SIGTERM. Sending SIGKILL..." >&2
    kill -9 -- -"$target_pid" 2>/dev/null || true
    kill -9 -- "$target_pid" 2>/dev/null || true
    sleep 1
  fi

  if kill -0 "$target_pid" 2>/dev/null; then
    echo "[$name] PID $target_pid still alive after SIGKILL. Investigate manually." >&2
  else
    echo "[$name] stopped." >&2
    mark_sentinel_interrupted "$path" "$target_pid"
    rm -f "$pid_file"
  fi
}

# ----- Argument parsing ------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: stop.sh [OPTIONS]

Stop a running ilk-launcher process for a project.

Options:
  --project-path PATH    Absolute path to project root.
  --project-name NAME    Look up path in projects.json.
  --all                  Stop every project in projects.json.
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
      --all)
        CLI_ALL=true
        shift
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

  if [[ "$CLI_ALL" == true ]]; then
    if [[ ! -f "$PROJECTS_JSON" ]]; then
      echo "projects.json not found." >&2
      exit 1
    fi
    local projects_json
    projects_json=$(read_projects_registry)
    local count
    count=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d))" <<<"$projects_json")
    if [[ "$count" -eq 0 ]]; then
      echo "projects.json has no projects." >&2
      exit 1
    fi
    python3 -c "
import json, sys
d = json.load(sys.stdin)
for p in d:
    print(p.get('path','') + '\t' + p.get('name',''))
" <<<"$projects_json" | while IFS=$'\t' read -r ppath pname; do
      stop_project "$ppath" "$pname"
    done
    return 0
  fi

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

  stop_project "$resolved_path" "$resolved_name"
}

main "$@"
