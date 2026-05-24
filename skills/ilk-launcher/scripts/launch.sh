#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-launcher (macOS bash port of launch.ps1)
# =============================================================================
# Spawns run_ilk_loop_claude.sh in a detached nohup process with PID tracking,
# per-project param resolution, and MCP whitelist/blacklist filtering.
# =============================================================================

# ----- Defaults & globals ----------------------------------------------------

LAUNCHER_DIR="${HOME}/.cursor/skills/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"
LOOP_SCRIPT="${HOME}/.cursor/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"
DEFAULT_MAX_ITER=30
DEFAULT_TIMEOUT=30

# CLI overrides (populated by parse_args)
CLI_PROJECT_PATH=""
CLI_PROJECT_NAME=""
CLI_MAX_ITERATIONS=0
CLI_ITERATION_TIMEOUT_MIN=0
CLI_DISABLE_MCP=""
CLI_ENABLE_MCP=""
CLI_ALL=false
CLI_FORCE=false
CLI_DRY_RUN=false

# Resolved values
RESOLVED_PATH=""
RESOLVED_NAME=""

# ----- Helpers ---------------------------------------------------------------

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
  echo "No project found by walking up from $(pwd). No .ilk-meta.json, .git, or docs/plans/MASTER-*.md anywhere on the path. Use --project-name or --project-path, or cd into a project." >&2
  exit 1
}

get_external_plans_dir() {
  local project_path="$1"
  local resolver
  resolver="${HOME}/.cursor/skills/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    echo ""
    return
  fi
  local json_out
  if json_out=$(python3 "$resolver" --start "$project_path" 2>/dev/null) && [[ -n "$json_out" ]]; then
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_plans_dir') or '')" <<<"$json_out"
  else
    echo ""
  fi
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

read_project_config() {
  local project_path="$1"
  local ext_plans
  ext_plans=$(get_external_plans_dir "$project_path")
  if [[ -n "$ext_plans" ]]; then
    local cfg_path="${ext_plans}/.ilk-launch.json"
    if [[ -f "$cfg_path" ]]; then
      cat "$cfg_path"
      return
    fi
  fi
  local cfg_path="${project_path}/docs/plans/.ilk-launch.json"
  if [[ -f "$cfg_path" ]]; then
    cat "$cfg_path"
    return
  fi
  echo '{}'
}

resolve_params() {
  local project_path="$1"
  local cli_max_iter="$2"
  local cli_timeout="$3"
  local cfg
  cfg=$(read_project_config "$project_path")

  local max_iter="$DEFAULT_MAX_ITER"
  local timeout="$DEFAULT_TIMEOUT"

  local cfg_max_iter cfg_timeout
  cfg_max_iter=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('max_iterations',''))" <<<"$cfg")
  cfg_timeout=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('iteration_timeout_min',''))" <<<"$cfg")

  if [[ "$cli_max_iter" -gt 0 ]]; then
    max_iter="$cli_max_iter"
  elif [[ -n "$cfg_max_iter" ]]; then
    max_iter="$cfg_max_iter"
  fi

  if [[ "$cli_timeout" -gt 0 ]]; then
    timeout="$cli_timeout"
  elif [[ -n "$cfg_timeout" ]]; then
    timeout="$cfg_timeout"
  fi

  echo "${max_iter} ${timeout}"
}

resolve_mcp_filter() {
  # Step 2 will fill this in.
  echo "none"
}

build_worker_mcp_config() {
  # Step 2 will fill this in.
  echo ""
}

get_pid_file_path() {
  local project_path="$1"
  echo "${project_path}/.ilk-launcher/running.pid"
}

get_launch_meta_path() {
  local project_path="$1"
  echo "${project_path}/.ilk-launcher/last-launch.json"
}

test_running_pid() {
  local project_path="$1"
  local pid_file
  pid_file=$(get_pid_file_path "$project_path")
  if [[ ! -f "$pid_file" ]]; then
    echo ""
    return
  fi
  local raw_pid
  raw_pid=$(cat "$pid_file" | tr -d '[:space:]')
  if [[ -z "$raw_pid" ]]; then
    rm -f "$pid_file"
    echo ""
    return
  fi
  if kill -0 "$raw_pid" 2>/dev/null; then
    echo "$raw_pid"
  else
    rm -f "$pid_file"
    echo ""
  fi
}

start_ilk_window() {
  # Step 3 will fill this in.
  echo ""
}

# ----- Argument parsing ------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: launch.sh [OPTIONS]

Launch the ilk-loop runner in a detached nohup process.

Options:
  --project-path PATH          Absolute path to project root.
  --project-name NAME          Look up path in projects.json.
  --max-iterations N           Override per-project/default max iterations.
  --iteration-timeout-min N    Override per-project/default timeout.
  --disable-mcp NAMES          Comma-separated blacklist of MCP servers.
  --enable-mcp NAMES           Comma-separated whitelist of MCP servers.
  --all                        Launch every project in projects.json.
  --force                      Skip the "already running" check.
  --dry-run                    Print resolved plan but do not spawn.
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
      --max-iterations)
        CLI_MAX_ITERATIONS="$2"
        shift 2
        ;;
      --iteration-timeout-min)
        CLI_ITERATION_TIMEOUT_MIN="$2"
        shift 2
        ;;
      --disable-mcp)
        CLI_DISABLE_MCP="$2"
        shift 2
        ;;
      --enable-mcp)
        CLI_ENABLE_MCP="$2"
        shift 2
        ;;
      --all)
        CLI_ALL=true
        shift
        ;;
      --force)
        CLI_FORCE=true
        shift
        ;;
      --dry-run)
        CLI_DRY_RUN=true
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
    # Step 5: --all batch mode.
    echo "--all batch mode not yet implemented." >&2
    exit 1
  fi

  # Resolve single project: --project-path > --project-name > cwd walk-up
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

  local params
  params=$(resolve_params "$RESOLVED_PATH" "$CLI_MAX_ITERATIONS" "$CLI_ITERATION_TIMEOUT_MIN")
  local max_iter="${params%% *}"
  local timeout_min="${params##* }"

  echo "[$RESOLVED_NAME] Resolved path: $RESOLVED_PATH"
  echo "[$RESOLVED_NAME] MaxIterations: $max_iter    IterationTimeoutMin: $timeout_min"

  # Steps 2-3 will add MCP filtering and actual spawn here.
  if [[ "$CLI_DRY_RUN" == true ]]; then
    echo "[$RESOLVED_NAME] DRY RUN — would launch with the above params."
    return
  fi

  echo "[$RESOLVED_NAME] Launch logic not yet implemented (steps 2-3)."
}

main "$@"
