#!/usr/bin/env bash
# Supervised ilk launch for macOS/Linux — implements /ilk-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../ilk-loop/scripts/_ilk_skill_root.sh"
source "$SCRIPT_DIR/../../ilk-loop/scripts/_resolve_python.sh"

SKILL_ROOT="$(ilk_skill_root)"
PATHS_PY="$SKILL_ROOT/ilk-loop/scripts/ilk_paths.py"
LOOP_STATUS_PY="$SKILL_ROOT/ilk-loop/scripts/loop_status.py"
PROMOTE_PY="$SKILL_ROOT/ilk-loop/scripts/promote_next_master.py"
LAUNCH_SH="$SKILL_ROOT/ilk-launcher/scripts/launch.sh"
WATCHDOG_SH="$SKILL_ROOT/ilk-watchdog/scripts/watchdog.sh"

START="${1:-.}"
MAX_ITER="${MAX_ITERATIONS:-0}"
TIMEOUT_MIN="${ITERATION_TIMEOUT_MIN:-0}"
DRY_RUN="${DRY_RUN:-0}"

paths_json="$(ilk_invoke_python "$PATHS_PY" --start "$START")"
PROJECT_ROOT="$(echo "$paths_json" | ilk_invoke_python -c "import json,sys; d=json.load(sys.stdin); print(d.get('project_root') or '')")"
PROJECT_KEY="$(echo "$paths_json" | ilk_invoke_python -c "import json,sys; d=json.load(sys.stdin); print(d.get('project_key') or '')")"
LAUNCHER_DIR="$(echo "$paths_json" | ilk_invoke_python -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_launcher_dir') or '')")"
WATCHDOG_DIR="$(echo "$paths_json" | ilk_invoke_python -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_watchdog_dir') or '')")"

if [[ -z "$PROJECT_ROOT" ]]; then
  echo "No project_root resolved from '$START'. cd into a project root and retry." >&2
  exit 2
fi

echo "Project: $PROJECT_KEY"
echo "Root:    $PROJECT_ROOT"

status_code=0
set +e
status_out="$(cd "$PROJECT_ROOT" && ilk_invoke_python "$LOOP_STATUS_PY")"
status_code=$?
set -e
echo ""
echo "$status_out"

if [[ "$status_code" -eq 2 ]]; then
  echo "No plans directory resolved." >&2
  exit 2
fi

if [[ "$status_code" -eq 0 ]]; then
  plan_json="$(ilk_invoke_python "$PROMOTE_PY" --project "$PROJECT_ROOT" --dry-run)"
  promoted="$(echo "$plan_json" | ilk_invoke_python -c "import json,sys; print(json.load(sys.stdin).get('promoted') or '')")"
  active_count="$(echo "$plan_json" | ilk_invoke_python -c "import json,sys; print(json.load(sys.stdin).get('active_count_before', 0))")"
  if [[ "$active_count" -gt 1 ]]; then
    echo "Queue integrity issue: $active_count masters are active." >&2
    exit 2
  fi
  if [[ -n "$promoted" ]]; then
    echo ""
    echo "Active master fully shipped; promoting $promoted ..."
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "(dry-run: would promote $promoted)"
      exit 0
    fi
    ilk_invoke_python "$PROMOTE_PY" --project "$PROJECT_ROOT"
    status_code=0
    set +e
    status_out="$(cd "$PROJECT_ROOT" && ilk_invoke_python "$LOOP_STATUS_PY")"
    status_code=$?
    set -e
    echo ""
    echo "$status_out"
  fi
  if [[ "$status_code" -eq 0 ]]; then
    echo ""
    echo "All sub-plans shipped — nothing to run."
    exit 0
  fi
fi

if [[ "$status_code" -ne 1 ]]; then
  echo "Unexpected loop_status exit code: $status_code" >&2
  exit 2
fi

if [[ "$MAX_ITER" -eq 0 ]]; then
  if [[ "$status_out" =~ step=([0-9]+)/([0-9]+) ]]; then
    cur="${BASH_REMATCH[1]}"
    est="${BASH_REMATCH[2]}"
    remaining=$((est - cur)); [[ "$remaining" -lt 1 ]] && remaining=1
    MAX_ITER=$((remaining * 2)); [[ "$MAX_ITER" -lt 10 ]] && MAX_ITER=10
    [[ "$MAX_ITER" -gt 60 ]] && MAX_ITER=60
  else
    MAX_ITER=30
  fi
fi
[[ "$TIMEOUT_MIN" -eq 0 ]] && TIMEOUT_MIN=30

echo ""
echo "Launch params: MaxIterations=$MAX_ITER IterationTimeoutMin=$TIMEOUT_MIN"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "(dry-run: would launch ilk + watchdog)"
  exit 0
fi

bash "$LAUNCH_SH" --project-path "$PROJECT_ROOT" \
  --max-iterations "$MAX_ITER" --iteration-timeout-min "$TIMEOUT_MIN"

bash "$WATCHDOG_SH" --project-path "$PROJECT_ROOT" \
  --poll-interval-sec 300 --max-restarts 5 --detach

echo ""
echo "ilk launched: $PROJECT_KEY"
echo "  Iterations: $MAX_ITER"
echo "  Timeout:    $TIMEOUT_MIN min"
echo "  Watchdog:   started (poll 5 min, max 5 restarts)"
if [[ -f "$LAUNCHER_DIR/last-launch.json" ]]; then
  log_file="$(ilk_invoke_python -c "import json; print(json.load(open('$LAUNCHER_DIR/last-launch.json')).get('log_file',''))")"
  [[ -n "$log_file" ]] && echo "  Loop log:   $log_file"
fi
echo "  Watchdog:   $WATCHDOG_DIR/activity.log"
