#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Single cross-project scheduler (V1 "global watchdog")
# =============================================================================
# Scans all projects for queued sub-plans, selects the FIFO-first project
# whose sentinel is free, and dispatches it via launch.sh -Engine claude-worker.
#
# Pool cap = 1 (V1): if ANY project is busy, no dispatch is planned.
#
# -DryRun prints the planned decision without executing anything.
# -Once runs a single scan cycle (for tests) instead of the daemon loop.
# =============================================================================

# --- skill root resolution ---------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# --- defaults ----------------------------------------------------------------

SCAN_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/scheduler_scan.py"
LAUNCH_SCRIPT="${_SKILL_ROOT}/ilk-launcher/scripts/launch.sh"

# Resolve python command (python3 preferred, python fallback for Windows).
# On Windows, `python3` may exist as a Microsoft Store alias that doesn't
# actually work, so we verify the command runs successfully.
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" &>/dev/null && "$candidate" --version &>/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "ERROR: no working python found on PATH" >&2
  exit 1
fi

POLL_MIN=5
MAX_DISPATCHES=-1
MAX_BUDGET_USD=0
DRY_RUN=false
ONCE=false

# --- argument parsing --------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: scheduler.sh [OPTIONS]

Single cross-project scheduler (V1).

Options:
  --poll-min N          Polling interval in minutes. Default 5.
  --max-dispatches N    Global dispatch ceiling. -1 = unlimited (default). 0 = no dispatches allowed.
  --max-budget-usd N    Global budget ceiling. Default 0 (unlimited).
  --dry-run             Print the planned decision without dispatching.
  --once                Run a single scan cycle and exit (for tests).
  -h, --help            Show this help and exit.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --poll-min)
        POLL_MIN="$2"
        shift 2
        ;;
      --max-dispatches)
        MAX_DISPATCHES="$2"
        shift 2
        ;;
      --max-budget-usd)
        MAX_BUDGET_USD="$2"
        shift 2
        ;;
      --dry-run)
        DRY_RUN=true
        shift
        ;;
      --once)
        ONCE=true
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

# --- helpers -----------------------------------------------------------------

test_running_pid() {
  # Check if a project has a live running.pid (sentinel mutex).
  # Returns 0 if busy, 1 if free.
  local project_data_path="$1"
  local pid_file="${project_data_path}/runtime/launcher/running.pid"
  if [[ ! -f "$pid_file" ]]; then
    return 1  # free
  fi
  local raw
  raw=$(tr -d '[:space:]' < "$pid_file" 2>/dev/null) || true
  if [[ -z "$raw" ]]; then
    rm -f "$pid_file"
    return 1  # free
  fi
  if ! [[ "$raw" =~ ^[0-9]+$ ]]; then
    return 1  # free
  fi
  kill -0 "$raw" 2>/dev/null
  # kill -0 returns 0 if alive (busy), 1 if dead (free)
}

invoke_scheduler_scan() {
  # Run scheduler_scan.py, output JSON to stdout
  # Strip \r for Windows compatibility
  $PYTHON "$SCAN_SCRIPT" | tr -d '\r'
}

# --- main loop ---------------------------------------------------------------

run_scheduler() {
  local dispatch_count=0
  # Associative array for blacklist backoff (key -> expiry epoch)
  declare -A blacklist_skip

  while true; do
    # --- scan for queued projects ---
    local scan_output
    scan_output=$(invoke_scheduler_scan) || {
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] scheduler_scan.py failed" >&2
      sleep $((POLL_MIN * 60))
      continue
    }
    # Strip any remaining \r (Windows line endings)
    scan_output="${scan_output//$'\r'/}"

    local count
    count=$($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))" <<<"$scan_output" | tr -d '\r')

    if [[ "$count" == "0" ]]; then
      if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
        echo '{"decision":"idle","reason":"all queues empty"}'
        return
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle: all queues empty. Polling in ${POLL_MIN} min."
      sleep $((POLL_MIN * 60))
      continue
    fi

    # --- check budget ceiling ---
    # MAX_DISPATCHES -1 = unlimited; >= 0 = hard ceiling.
    if [[ "$MAX_DISPATCHES" -ge 0 && "$dispatch_count" -ge "$MAX_DISPATCHES" ]]; then
      if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
        echo '{"decision":"idle","reason":"budget ceiling"}'
        return
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle: budget ceiling (dispatched ${dispatch_count}/${MAX_DISPATCHES}). Polling in ${POLL_MIN} min."
      sleep $((POLL_MIN * 60))
      continue
    fi

    # --- iterate projects in FIFO order ---
    local selected_key=""
    local selected_path=""
    local now_epoch
    now_epoch=$(date +%s)

    # Parse the JSON array and iterate
    local keys paths
    mapfile -t keys < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(p['key']) for p in d]" <<<"$scan_output" | tr -d '\r')
    mapfile -t paths < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(p['path']) for p in d]" <<<"$scan_output" | tr -d '\r')

    for i in "${!keys[@]}"; do
      local key="${keys[$i]}"
      local path="${paths[$i]}"

      # blacklist skip
      if [[ -n "${blacklist_skip[$key]:-}" ]]; then
        if [[ "$now_epoch" -lt "${blacklist_skip[$key]}" ]]; then
          if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
            echo "{\"decision\":\"skip-blacklist\",\"key\":\"$key\"}"
          else
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip-blacklist: $key"
          fi
          continue
        else
          unset "blacklist_skip[$key]"
        fi
      fi

      # Check if project is busy
      if test_running_pid "$path"; then
        if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
          echo "{\"decision\":\"skip-busy\",\"key\":\"$key\"}"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip-busy: $key"
        fi
        continue
      fi

      # First free project in FIFO order
      selected_key="$key"
      selected_path="$path"
      break
    done

    if [[ -z "$selected_key" ]]; then
      if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
        echo '{"decision":"idle","reason":"all queued projects blacklisted"}'
        return
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle: all queued projects blacklisted. Polling in ${POLL_MIN} min."
      sleep $((POLL_MIN * 60))
      continue
    fi

    # --- dispatch the selected project ---
    if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
      # Use forward slashes in paths for valid JSON (Windows backslashes are invalid escapes)
      local safe_path="${selected_path//\\//}"
      echo "{\"decision\":\"dispatch\",\"key\":\"$selected_key\",\"command\":\"launch.sh --project-path '$safe_path' --engine claude-worker\"}"
      return
    fi

    if [[ "$DRY_RUN" == true ]]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] DRY-RUN: would dispatch $selected_key via $LAUNCH_SCRIPT --project-path '$selected_path' --engine claude-worker"
    else
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] dispatching $selected_key..."
      if bash "$LAUNCH_SCRIPT" --project-path "$selected_path" --engine claude-worker --force; then
        dispatch_count=$((dispatch_count + 1))
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] dispatched $selected_key (total: $dispatch_count)"
      else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] dispatch failed for $selected_key"
        # Record in blacklist with 5-min backoff
        blacklist_skip[$key]=$(($(date +%s) + 300))
      fi
    fi

    sleep $((POLL_MIN * 60))
  done
}

# --- entry point -------------------------------------------------------------

parse_args "$@"
run_scheduler
