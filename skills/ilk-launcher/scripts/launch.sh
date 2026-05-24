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
  # Return JSON array of projects from projects.json, or empty array.
  :
}

resolve_project_by_name() {
  # $1 = name. Look up in projects.json, echo path or exit 1.
  :
}

resolve_project_by_cwd() {
  # Walk up from cwd looking for docs/plans/MASTER-*.md.
  # Prefer ilk_paths.py if available; fallback to manual walk-up.
  :
}

get_external_plans_dir() {
  # $1 = project path. Echo external plans dir via ilk_paths.py, or empty.
  :
}

get_project_name() {
  # $1 = path. Echo registered name, or basename of path.
  :
}

read_project_config() {
  # $1 = project path. Echo .ilk-launch.json as JSON object, or {}.
  :
}

resolve_params() {
  # Determine MaxIterations and IterationTimeoutMin.
  # Priority: CLI > .ilk-launch.json > defaults.
  :
}

resolve_mcp_filter() {
  # Determine MCP filtering mode and names.
  # Priority: CLI flags > .ilk-launch.json keys.
  # Error if both blacklist and whitelist specified at same level.
  # Outputs: mode (whitelist|blacklist|none) and names array.
  :
}

build_worker_mcp_config() {
  # $1 = project path, $2 = mode, $3 = space-separated names.
  # Reads ~/.claude.json mcpServers, filters, writes
  # <project>/.ilk-launcher/mcp-worker.json as UTF-8 no-BOM JSON.
  # Echoes path to temp file, or empty if no filtering.
  :
}

get_pid_file_path() {
  # $1 = project path. Echo path to .ilk-launcher/running.pid.
  :
}

get_launch_meta_path() {
  # $1 = project path. Echo path to .ilk-launcher/last-launch.json.
  :
}

test_running_pid() {
  # $1 = project path. If PID file exists and kill -0 succeeds, echo PID.
  # Otherwise remove stale PID file and echo empty.
  :
}

start_ilk_window() {
  # $1 = project path, $2 = project name, $3 = max_iter, $4 = timeout_min,
  # $5 = force, $6 = dry_run, $7 = mcp_config_path.
  # Spawns detached nohup + setsid process, writes PID file + last-launch.json.
  # Echoes spawned PID, or empty for dry-run / already-running.
  :
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
    # Iterate projects.json and launch each (skip running).
    :
  else
    # Resolve single project: --project-path > --project-name > cwd walk-up
    :

    # Resolve params, MCP filter, build config, then start window.
    :
  fi
}

main "$@"
