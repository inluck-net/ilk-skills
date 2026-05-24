#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-launcher stop (macOS bash port of stop.ps1)
# =============================================================================
# Reads <project>/.ilk-launcher/running.pid and kills the process group.
# =============================================================================

# ----- Defaults & globals ----------------------------------------------------

LAUNCHER_DIR="${HOME}/.cursor/skills/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"

# CLI overrides
CLI_PROJECT_PATH=""
CLI_PROJECT_NAME=""
CLI_ALL=false

# ----- Helpers ---------------------------------------------------------------

read_projects_registry() {
  # Return JSON array of projects from projects.json, or empty array.
  :
}

resolve_project_by_name() {
  # $1 = name. Look up in projects.json, echo path or exit 1.
  :
}

stop_project() {
  # $1 = path, $2 = name.
  # Read PID file; if missing print message and return.
  # Kill process group (kill -- -$PGID), wait up to 5s, then kill -9 if needed.
  # Delete PID file on success.
  :
}

# ----- Argument parsing ------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: stop.sh [OPTIONS]

Stop a running ilk-launcher process for a project.

Options:
  --project-path PATH          Absolute path to project root.
  --project-name NAME          Look up path in projects.json.
  --all                        Stop every registered project.
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
    # Iterate projects.json and stop each.
    :
  else
    # Resolve single project: --project-path > --project-name
    # Then stop it.
    :
  fi
}

main "$@"
