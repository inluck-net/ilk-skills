#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-watchdog (macOS bash port of watchdog.ps1)
# =============================================================================
# Polls ~/.ilk-data/projects/<key>/runtime/last-exit.json (preferred) and
# falls back to <project>/.ilk-launcher/running.pid every --poll-interval-sec.
# When the loop stops, classifies the run and either relaunches, promotes
# the next master, or blocks with a loud banner.
# =============================================================================

# ----- Defaults & globals ----------------------------------------------------

LAUNCHER_DIR="${HOME}/.cursor/skills/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"
LAUNCH_SCRIPT="${LAUNCHER_DIR}/scripts/launch.sh"
LOOP_STATUS_PY="${HOME}/.cursor/skills/ilk-loop/scripts/loop_status.py"
COLLECT_PY="${HOME}/.cursor/skills/ilk-feedback/scripts/collect.py"

POLL_INTERVAL_SEC=60
MAX_RESTARTS=5

# CLI overrides
CLI_PROJECT_PATH=""
CLI_PROJECT_NAME=""
CLI_POLL_INTERVAL_SEC=""
CLI_MAX_RESTARTS=""

# Resolved values
RESOLVED_PATH=""
RESOLVED_NAME=""

# ----- Helpers (project resolution, same pattern as launch.sh) ---------------

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
    local known
    known=$(python3 -c "
import json
with open('$PROJECTS_JSON') as f:
    data = json.load(f)
print(', '.join(p.get('name','') for p in data.get('projects', []) if p.get('name')))
")
    echo "Project '$name' not in projects.json. Known: $known" >&2
    exit 1
  fi
  echo "$path"
}

resolve_project_by_cwd() {
  local resolver
  resolver="${HOME}/.cursor/skills/ilk-loop/scripts/ilk_paths.py"
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

get_project_name() {
  local path="$1"
  local name
  name=$(python3 -c "
import json
with open('$PROJECTS_JSON') as f:
    data = json.load(f)
for p in data.get('projects', []):
    if p.get('path') == '$path':
        print(p.get('name',''))
        break
")
  if [[ -n "$name" ]]; then
    echo "$name"
  else
    basename "$path"
  fi
}

get_ilk_runtime_dir() {
  local project="$1"
  local resolver
  resolver="${HOME}/.cursor/skills/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    echo ""
    return
  fi
  local json_out
  if json_out=$(python3 "$resolver" --start "$project" 2>/dev/null) && [[ -n "$json_out" ]]; then
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_runtime_dir') or '')" <<<"$json_out"
  else
    echo ""
  fi
}

# ----- Sentinel / PID helpers ------------------------------------------------

test_process_alive() {
  local pid="$1"
  if [[ -z "$pid" || "$pid" -le 0 ]]; then
    return 1
  fi
  kill -0 "$pid" 2>/dev/null
}

read_ilk_pid() {
  local project="$1"
  local f="${project}/.ilk-launcher/running.pid"
  if [[ ! -f "$f" ]]; then
    echo ""
    return
  fi
  local raw
  raw=$(tr -d '[:space:]' < "$f")
  if [[ -z "$raw" ]]; then
    rm -f "$f"
    echo ""
    return
  fi
  echo "$raw"
}

read_last_exit_state() {
  local runtime_dir="$1"
  if [[ -z "$runtime_dir" ]]; then
    echo ""
    return
  fi
  local f="${runtime_dir}/last-exit.json"
  if [[ ! -f "$f" ]]; then
    echo ""
    return
  fi
  python3 -c "
import json, sys
try:
    with open('$f', encoding='utf-8') as fh:
        d = json.load(fh)
    print(d.get('state',''))
    print(d.get('iterations',''))
    print(d.get('last_iter_at',''))
except Exception:
    pass
"
}

classify_action() {
  local state="$1"
  case "$state" in
    running)
      echo "sleep"
      ;;
    all-shipped|already-shipped|shipped)
      echo "promote"
      ;;
    no-progress|timeout|budget-exhausted)
      echo "relaunch"
      ;;
    merge-conflict|local-checks-stuck)
      echo "blacklist"
      ;;
    *)
      # missing file / unknown state -> sleep (poll again)
      echo "sleep"
      ;;
  esac
}

# ----- Argument parsing ------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: watchdog.sh [OPTIONS]

Auto-restart ilk-loop based on ilk-feedback classification.

Options:
  --project-path PATH          Absolute path to project root.
  --project-name NAME          Look up path in projects.json.
  --poll-interval-sec N        Polling interval in seconds. Default 60.
  --max-restarts N             Hard cap on consecutive relaunches. Default 5.
  -h, --help                   Show this help and exit.
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
      --poll-interval-sec)
        CLI_POLL_INTERVAL_SEC="$2"
        shift 2
        ;;
      --max-restarts)
        CLI_MAX_RESTARTS="$2"
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

  # Resolve project: --project-path > --project-name > cwd walk-up
  if [[ -n "$CLI_PROJECT_PATH" ]]; then
    RESOLVED_PATH="$CLI_PROJECT_PATH"
  elif [[ -n "$CLI_PROJECT_NAME" ]]; then
    RESOLVED_PATH=$(resolve_project_by_name "$CLI_PROJECT_NAME")
  else
    RESOLVED_PATH=$(resolve_project_by_cwd)
  fi

  if [[ ! -d "$RESOLVED_PATH" ]]; then
    echo "ProjectPath '$RESOLVED_PATH' does not exist." >&2
    exit 1
  fi

  # Normalize to absolute path
  RESOLVED_PATH="$(cd "$RESOLVED_PATH" && pwd)"

  if [[ -n "$CLI_PROJECT_NAME" ]]; then
    RESOLVED_NAME="$CLI_PROJECT_NAME"
  else
    RESOLVED_NAME=$(get_project_name "$RESOLVED_PATH")
  fi

  # Apply CLI overrides
  if [[ -n "$CLI_POLL_INTERVAL_SEC" ]]; then
    POLL_INTERVAL_SEC="$CLI_POLL_INTERVAL_SEC"
  fi
  if [[ -n "$CLI_MAX_RESTARTS" ]]; then
    MAX_RESTARTS="$CLI_MAX_RESTARTS"
  fi

  echo "[ilk-watchdog] Project: $RESOLVED_NAME"
  echo "[ilk-watchdog] ProjectPath: $RESOLVED_PATH"
  echo "[ilk-watchdog] PollIntervalSec: $POLL_INTERVAL_SEC"
  echo "[ilk-watchdog] MaxRestarts: $MAX_RESTARTS"
}

main "$@"
