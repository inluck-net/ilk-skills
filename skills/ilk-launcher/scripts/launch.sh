#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-launcher (macOS bash port of launch.ps1)
# =============================================================================
# Spawns run_ilk_loop_claude.sh in a detached nohup process with PID tracking,
# per-project param resolution, and MCP whitelist/blacklist filtering.
# =============================================================================

# ----- Skill root resolution -------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# ----- Defaults & globals ----------------------------------------------------

LAUNCHER_DIR="${_SKILL_ROOT}/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"
LOOP_SCRIPT="${_SKILL_ROOT}/ilk-loop/scripts/run_ilk_loop_claude.sh"
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

# MCP filter state (populated by resolve_mcp_filter)
MCP_FILTER_MODE=""
MCP_FILTER_NAMES=""

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
  echo "No project found by walking up from $(pwd). No .ilk-meta.json, .git, or docs/plans/MASTER-*.md anywhere on the path. Use --project-name or --project-path, or cd into a project." >&2
  exit 1
}

get_external_plans_dir() {
  local project_path="$1"
  local resolver
  resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
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

get_project_name() {
  local path="$1"
  local name=""
  if [[ -f "$PROJECTS_JSON" ]]; then
    name=$(python3 -c "
import json
with open('$PROJECTS_JSON') as f:
    data = json.load(f)
for p in data.get('projects', []):
    if p.get('path') == '$path':
        print(p.get('name',''))
        break
")
  fi
  if [[ -n "$name" ]]; then
    echo "$name"
  else
    basename "$path"
  fi
}

get_project_key() {
  local project_path="$1"
  local resolver
  resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ -f "$resolver" ]]; then
    local json_out
    if json_out=$(python3 "$resolver" --start "$project_path" 2>/dev/null) && [[ -n "$json_out" ]]; then
      local key
      key=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('project_key') or '')" <<<"$json_out")
      if [[ -n "$key" ]]; then
        echo "$key"
        return
      fi
    fi
  fi
  basename "$project_path"
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
  local project_path="$1"

  # CLI flags trump per-project config; mutex check at CLI level
  if [[ -n "$CLI_ENABLE_MCP" && -n "$CLI_DISABLE_MCP" ]]; then
    echo "Error: Specify either --enable-mcp (whitelist) or --disable-mcp (blacklist), not both." >&2
    exit 1
  fi
  if [[ -n "$CLI_ENABLE_MCP" ]]; then
    MCP_FILTER_MODE="whitelist"
    MCP_FILTER_NAMES="$CLI_ENABLE_MCP"
    return
  fi
  if [[ -n "$CLI_DISABLE_MCP" ]]; then
    MCP_FILTER_MODE="blacklist"
    MCP_FILTER_NAMES="$CLI_DISABLE_MCP"
    return
  fi

  # Fall back to per-project config
  local cfg
  cfg=$(read_project_config "$project_path")

  local has_enable has_disable
  has_enable=$(python3 -c "import json,sys; d=json.load(sys.stdin); print('1' if 'worker_enable_mcp' in d else '0')" <<<"$cfg")
  has_disable=$(python3 -c "import json,sys; d=json.load(sys.stdin); print('1' if 'worker_disable_mcp' in d else '0')" <<<"$cfg")

  if [[ "$has_enable" == "1" && "$has_disable" == "1" ]]; then
    echo "Error: Specify either worker_disable_mcp or worker_enable_mcp in .ilk-launch.json, not both." >&2
    exit 1
  fi

  if [[ "$has_enable" == "1" ]]; then
    MCP_FILTER_MODE="whitelist"
    MCP_FILTER_NAMES=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(','.join(str(x) for x in d.get('worker_enable_mcp', [])))" <<<"$cfg")
    return
  fi
  if [[ "$has_disable" == "1" ]]; then
    MCP_FILTER_MODE="blacklist"
    MCP_FILTER_NAMES=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(','.join(str(x) for x in d.get('worker_disable_mcp', [])))" <<<"$cfg")
    return
  fi

  MCP_FILTER_MODE="none"
  MCP_FILTER_NAMES=""
}

build_worker_mcp_config() {
  local project_path="$1"
  local mode="$2"
  local names_csv="$3"

  if [[ "$mode" == "none" || -z "$names_csv" ]]; then
    echo ""
    return
  fi

  local claude_json="${HOME}/.claude.json"
  if [[ ! -f "$claude_json" ]]; then
    echo "[ilk] worker MCP filter requested but ~/.claude.json not found; skipping." >&2
    echo ""
    return
  fi

  local out_path
  out_path="$(get_external_launcher_dir "$project_path")/mcp-worker.json"
  if [[ -z "$out_path" || "$out_path" == "/mcp-worker.json" ]]; then
    echo "[ilk] could not resolve external launcher dir for $project_path" >&2
    echo ""
    return
  fi
  mkdir -p "$(dirname "$out_path")"

  python3 - "$mode" "$names_csv" "$claude_json" "$out_path" <<'PYEOF'
import json, sys

mode = sys.argv[1]
names = [n.strip() for n in sys.argv[2].split(',') if n.strip()]
claude_json_path = sys.argv[3]
out_path = sys.argv[4]

with open(claude_json_path, encoding='utf-8') as f:
    data = json.load(f)

if 'mcpServers' not in data:
    print('[ilk] worker MCP filter requested but ~/.claude.json has no mcpServers; skipping.', file=sys.stderr)
    sys.exit(0)

filtered = {}
kept = []
skipped = []
missing = []

if mode == 'whitelist':
    for want in names:
        if want in data['mcpServers']:
            filtered[want] = data['mcpServers'][want]
            kept.append(want)
        else:
            missing.append(want)
else:
    # blacklist
    for name, cfg in data['mcpServers'].items():
        if name in names:
            skipped.append(name)
        else:
            filtered[name] = cfg
            kept.append(name)

out = {'mcpServers': filtered}
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

if mode == 'whitelist':
    print(f'[ilk] worker MCP filter (whitelist): kept {", ".join(kept)}', file=sys.stderr)
    if missing:
        print(f'[ilk] note: {", ".join(missing)} not in ~/.claude.json mcpServers (typo? claude.ai-synced?)', file=sys.stderr)
else:
    if skipped:
        print(f'[ilk] worker MCP filter (blacklist): disabling {", ".join(skipped)} (kept {", ".join(kept)})', file=sys.stderr)

print(out_path)
PYEOF
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

get_launch_meta_path() {
  local project_path="$1"
  local launcher_dir
  launcher_dir=$(get_external_launcher_dir "$project_path")
  if [[ -z "$launcher_dir" ]]; then
    echo ""
    return
  fi
  echo "${launcher_dir}/last-launch.json"
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
  local project_path="$1"
  local project_name="$2"
  local max_iterations="$3"
  local timeout_min="$4"
  local force="$5"
  local dry_run="$6"
  local mcp_config_path="$7"

  # Concurrency guard
  local live_pid
  live_pid=$(test_running_pid "$project_path")
  if [[ -n "$live_pid" && "$force" != "true" ]]; then
    echo "[$project_name] already running (PID $live_pid). Use --force to launch anyway, or stop.sh to kill it." >&2
    return 1
  fi

  # Create state dir
  local state_dir
  state_dir=$(get_external_launcher_dir "$project_path")
  if [[ -z "$state_dir" ]]; then
    echo "[$project_name] could not resolve external launcher dir" >&2
    return 1
  fi
  mkdir -p "$state_dir"

  # Build runner command
  local runner_cmd
  runner_cmd="bash \"$LOOP_SCRIPT\" --project-path \"$project_path\" --max-iterations $max_iterations --iteration-timeout-min $timeout_min"
  if [[ -n "$mcp_config_path" ]]; then
    runner_cmd="$runner_cmd --mcp-config-path \"$mcp_config_path\""
  fi

  # Build log path
  local project_key run_id log_file log_dir
  project_key=$(get_project_key "$project_path")
  run_id="$(date +%Y%m%d-%H%M%S)"
  log_dir="${_SKILL_ROOT}/ilk-loop/logs/launcher"
  mkdir -p "$log_dir"
  log_file="${log_dir}/${project_key}-${run_id}.log"

  if [[ "$dry_run" == "true" ]]; then
    echo "[$project_name] DRY RUN — would launch:"
    echo "  ProjectPath: $project_path"
    echo "  MaxIterations: $max_iterations"
    echo "  IterationTimeoutMin: $timeout_min"
    if [[ -n "$mcp_config_path" ]]; then
      echo "  McpConfigPath: $mcp_config_path"
    fi
    echo "  LogFile: $log_file"
    local pid_file
    pid_file=$(get_pid_file_path "$project_path")
    echo "  PID file: $pid_file"
    return 0
  fi

  # Spawn detached process as its own process-group leader so stop.sh
  # can `kill -- -$pid` and reap the whole tree (runner + claude + git).
  # `set -m` inside a subshell enables job control, which is what causes
  # bash to put the backgrounded child into a new pgrp — without it,
  # non-interactive bash leaves the child in the launcher's pgrp, and
  # the launcher exits seconds later, leaving the child group-less.
  local pgid
  pgid=$(
    set -m
    nohup bash -c "$runner_cmd" > "$log_file" 2>&1 < /dev/null &
    echo "$!"
  )

  # Write PID file
  local pid_file
  pid_file=$(get_pid_file_path "$project_path")
  echo "$pgid" > "$pid_file"

  # Write last-launch.json
  local meta_path
  meta_path=$(get_launch_meta_path "$project_path")
  python3 -c "
import json
d = {
    'project_path': '$project_path',
    'project_name': '$project_name',
    'pid': $pgid,
    'started_at': '$(date +%Y-%m-%dT%H:%M:%S%z)',
    'max_iterations': $max_iterations,
    'iteration_timeout_min': $timeout_min,
    'loop_script': '$LOOP_SCRIPT',
    'mcp_config_path': '$mcp_config_path',
    'log_file': '$log_file',
}
print(json.dumps(d, indent=2))
" > "$meta_path"

  echo "[$project_name] launched. PID $pgid."
  echo "[$project_name] PID file: $pid_file"
  echo "[$project_name] Log file: $log_file"
  echo "[$project_name] loop JSONL log: ${_SKILL_ROOT}/ilk-loop/logs"
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
    if [[ ! -f "$PROJECTS_JSON" ]]; then
      echo "projects.json has no projects. Add some before using --all." >&2
      exit 1
    fi
    local projects_json
    projects_json=$(read_projects_registry)
    local count
    count=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d))" <<<"$projects_json")
    if [[ "$count" -eq 0 ]]; then
      echo "projects.json has no projects. Add some before using --all." >&2
      exit 1
    fi
    python3 -c "
import json, sys
d = json.load(sys.stdin)
for p in d:
    print(p.get('path','') + '\t' + p.get('name',''))
" <<<"$projects_json" | while IFS=$'\t' read -r ppath pname; do
      if [[ ! -d "$ppath" ]]; then
        echo "[$pname] path '$ppath' does not exist. Skipping." >&2
        continue
      fi
      local live_pid
      live_pid=$(test_running_pid "$ppath")
      if [[ -n "$live_pid" && "$CLI_FORCE" != true ]]; then
        echo "[$pname] already running (PID $live_pid). Skipping." >&2
        continue
      fi
      local params
      params=$(resolve_params "$ppath" "$CLI_MAX_ITERATIONS" "$CLI_ITERATION_TIMEOUT_MIN")
      local max_iter="${params%% *}"
      local timeout_min="${params##* }"
      resolve_mcp_filter "$ppath"
      local mcp_config_path=""
      mcp_config_path=$(build_worker_mcp_config "$ppath" "$MCP_FILTER_MODE" "$MCP_FILTER_NAMES")
      start_ilk_window "$ppath" "$pname" "$max_iter" "$timeout_min" "$CLI_FORCE" "$CLI_DRY_RUN" "$mcp_config_path"
    done
    return 0
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

  # MCP filtering
  resolve_mcp_filter "$RESOLVED_PATH"
  local mcp_config_path=""
  mcp_config_path=$(build_worker_mcp_config "$RESOLVED_PATH" "$MCP_FILTER_MODE" "$MCP_FILTER_NAMES")
  if [[ -n "$mcp_config_path" ]]; then
    echo "[$RESOLVED_NAME] MCP config: $mcp_config_path (strict -- worker sees only what's listed)"
  else
    echo "[$RESOLVED_NAME] MCP config: (default -- worker sees user's full MCP registry)"
  fi

  if [[ "$CLI_DRY_RUN" == true ]]; then
    echo "[$RESOLVED_NAME] DRY RUN — would launch with the above params."
    local pid_file
    pid_file=$(get_pid_file_path "$RESOLVED_PATH")
    echo "[$RESOLVED_NAME] PID file: $pid_file"
    return
  fi

  start_ilk_window "$RESOLVED_PATH" "$RESOLVED_NAME" "$max_iter" "$timeout_min" "$CLI_FORCE" "$CLI_DRY_RUN" "$mcp_config_path"
}

main "$@"
