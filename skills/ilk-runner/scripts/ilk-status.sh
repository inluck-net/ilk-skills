#!/usr/bin/env bash
# Read-only ilk status for macOS/Linux — implements /ilk-status.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../ilk-loop/scripts/_ilk_skill_root.sh"
source "$SCRIPT_DIR/../../ilk-loop/scripts/_resolve_python.sh"

SKILL_ROOT="$(ilk_skill_root)"
PATHS_PY="$SKILL_ROOT/ilk-loop/scripts/ilk_paths.py"
LOOP_STATUS_PY="$SKILL_ROOT/ilk-loop/scripts/loop_status.py"
STATUS_PROGRESS_PY="$SKILL_ROOT/ilk-launcher/scripts/status_progress.py"
DASHBOARD_PY="$SKILL_ROOT/ilk-loop/scripts/ilk_dashboard.py"

START="${1:-.}"
WATCH=0
N=5

# Parse flags
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --watch) WATCH=1 ;;
    -n) N="$2"; shift ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

paths_json="$(ilk_invoke_python "$PATHS_PY" --start "$START")"
PROJECT_ROOT="$(echo "$paths_json" | ilk_invoke_python -c "import json,sys; d=json.load(sys.stdin); print(d.get('project_root') or '')")"

if [[ -z "$PROJECT_ROOT" ]]; then
  echo "No project_root resolved from '$START'." >&2
  exit 2
fi

status_code=0
set +e
status_out="$(cd "$PROJECT_ROOT" && ilk_invoke_python "$LOOP_STATUS_PY")"
status_code=$?
set -e
echo "$status_out"

if [[ "$status_code" -eq 0 ]]; then
  echo ""
  echo "All sub-plans shipped."
  exit 0
fi
if [[ "$status_code" -eq 2 ]]; then
  exit 2
fi

echo ""
echo "--- progress ---"
ilk_invoke_python "$STATUS_PROGRESS_PY" --project-path "$PROJECT_ROOT" || true

if [[ "$WATCH" -eq 1 ]]; then
  ilk_invoke_python "$DASHBOARD_PY" --watch -n "$N"
  exit $?
fi

exit "$status_code"
