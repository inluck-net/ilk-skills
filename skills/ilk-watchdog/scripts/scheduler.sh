#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Single cross-project scheduler (V1.1 — slot pool)
# =============================================================================
# Scans all projects for runnable masters, dispatches up to --max-concurrent
# ready projects per cycle (each routed to a distinct slot home), promotes
# a queued master if needed, and dispatches via launch.sh -Engine claude-worker.
#
# -DryRun prints the planned decision without executing anything.
# -Once runs a single scan cycle (for tests) instead of the daemon loop.
# =============================================================================

# --- single-instance guard (pidfile) -----------------------------------------

SCHEDULER_PIDFILE="${HOME}/.ilk-data/scheduler.pid"

acquire_scheduler_lock() {
  # Use a pidfile with liveness check. Portable (no flock dependency).
  local pidfile="$SCHEDULER_PIDFILE"
  if [[ -f "$pidfile" ]]; then
    local old_pid
    old_pid=$(tr -d '[:space:]' < "$pidfile" 2>/dev/null) || true
    if [[ -n "$old_pid" && "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "[ilk-scheduler] already running (PID $old_pid). Exiting."
      exit 0
    fi
    # Stale pidfile — remove and proceed.
    rm -f "$pidfile"
  fi
  # Write our PID.
  mkdir -p "$(dirname "$pidfile")"
  echo $$ > "$pidfile"
}

release_scheduler_lock() {
  rm -f "$SCHEDULER_PIDFILE" 2>/dev/null || true
}

# Acquire lock immediately at source time.
acquire_scheduler_lock

# Release lock on exit (normal, error, or signal).
trap release_scheduler_lock EXIT

# --- skill root resolution ---------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# --- defaults ----------------------------------------------------------------

SCAN_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/scheduler_scan.py"
PROMOTE_SCRIPT="${_SKILL_ROOT}/ilk-loop/scripts/promote_next_master.py"
LAUNCH_SCRIPT="${_SKILL_ROOT}/ilk-launcher/scripts/launch.sh"
BOOTSTRAP_SCRIPT="${_SKILL_ROOT}/../tools/claude-worker/bootstrap.sh"
NOTIFY_PY="${_SKILL_ROOT}/ilk-watchdog/scripts/ilk_notify.py"
WATCHDOG_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/watchdog.sh"

SCHEDULER_LOG_DIR="${HOME}/.ilk-data/logs"
SCHEDULER_LOG_FILE="${SCHEDULER_LOG_DIR}/scheduler.log"

# Fire-and-forget desktop notification. Failure is swallowed.
invoke_ilk_notify() {
  local event="$1" project="$2" detail="${3:-}"
  local args=("$NOTIFY_PY" --event "$event" --project "$project")
  [[ -n "$detail" ]] && args+=(--detail "$detail")
  $PYTHON "${args[@]}" 2>/dev/null || true
}

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
MAX_CONCURRENT=5
MAX_DISPATCHES=-1
MAX_BUDGET_USD=0
DRY_RUN=false
ONCE=false
DETACH=false
NO_LOCAL_CHECKS=false
DISPATCH_COOLDOWN_SEC=120

# --- argument parsing --------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: scheduler.sh [OPTIONS]

Single cross-project scheduler (V1.1 — slot pool).

Options:
  --poll-min N          Polling interval in minutes. Default 5.
  --max-concurrent N    Maximum concurrent live loops. Default 5. Set to 1 for strict sequential.
  --max-dispatches N    Global dispatch ceiling. -1 = unlimited (default). 0 = no dispatches allowed.
  --max-budget-usd N    Global budget ceiling. Default 0 (unlimited).
  --dry-run             Print the planned decision without dispatching.
  --once                Run a single scan cycle and exit (for tests).
  --detach              Spawn this scheduler in a detached screen session and exit.
  --no-local-checks     Opt out of dispatching with --run-local-checks (default is gates ON).
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
      --max-concurrent)
        MAX_CONCURRENT="$2"
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
      --detach)
        DETACH=true
        shift
        ;;
      --no-local-checks)
        NO_LOCAL_CHECKS=true
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

write_scheduler_log() {
  # Append a decision line to scheduler.log (BOM-free, timestamped).
  # Usage: write_scheduler_log "decision" ["key"] ["reason"]
  local decision="$1" key="${2:-}" reason="${3:-}"
  mkdir -p "$SCHEDULER_LOG_DIR" 2>/dev/null || true
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  local line="[$ts] $decision"
  [[ -n "$key" ]] && line+=": $key"
  [[ -n "$reason" ]] && line+=" ($reason)"
  printf '%s\n' "$line" >> "$SCHEDULER_LOG_FILE" 2>/dev/null || true
}

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
  if ! kill -0 "$raw" 2>/dev/null; then
    return 1  # dead pid — free
  fi

  # Stale-sentinel cross-check: even if the pid is alive, a terminal
  # last-exit.json means the loop already finished.  The lingering
  # -NoExit shell keeps the pid alive past the loop's real exit.
  local sentinel_file="${project_data_path}/runtime/last-exit.json"
  if [[ -f "$sentinel_file" ]]; then
    local state
    # Parse "state" value — grep+sed fallback (no jq dependency).
    state=$(grep -o '"state"[[:space:]]*:[[:space:]]*"[^"]*"' "$sentinel_file" 2>/dev/null \
            | head -1 | sed 's/.*"state"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
    if [[ -n "$state" && "$state" != "running" ]]; then
      return 1  # terminal state — project is free
    fi
  fi
  return 0  # busy
}

blacklist_epoch_for_key() {
  # Bash 3.2 compatible lookup for newline-separated "key epoch" lists;
  # duplicate keys allowed, the maximum epoch wins. Echoes the max epoch
  # (empty if not present). $2 = haystack list (default: $blacklist_skip, the
  # transient backoff map). Pass the per-cycle postmortem list explicitly for
  # the stateless blacklist check.
  local target="$1"
  local haystack="${2-${blacklist_skip:-}}"
  local max_epoch=""
  local key epoch
  while IFS=' ' read -r key epoch; do
    [[ -z "$key" || "$key" != "$target" || -z "$epoch" ]] && continue
    if [[ -z "$max_epoch" || "$epoch" -gt "$max_epoch" ]]; then
      max_epoch="$epoch"
    fi
  done <<<"${haystack}"
  echo "$max_epoch"
}

file_mtime_epoch() {
  # Cross-platform file modification time as epoch seconds.
  # Linux: stat -c %Y; macOS/BSD: stat -f %m.
  local file="$1"
  if [[ "$(uname)" == "Darwin" ]]; then
    stat -f %m "$file" 2>/dev/null || echo 0
  else
    stat -c %Y "$file" 2>/dev/null || echo 0
  fi
}

dispatch_time_epoch_for_key() {
  # Lookup dispatch time epoch for a key from $dispatch_time.
  local target="$1"
  local result=""
  local key epoch
  while IFS=' ' read -r key epoch; do
    [[ -z "$key" || "$key" != "$target" || -z "$epoch" ]] && continue
    result="$epoch"
    break
  done <<<"${dispatch_time:-}"
  echo "$result"
}

set_dispatch_time() {
  # Set dispatch time epoch for a key in $dispatch_time (global).
  local target="$1" epoch="$2"
  local new_entries=""
  local key val found=false
  while IFS=' ' read -r key val; do
    if [[ -n "$key" && "$key" == "$target" ]]; then
      new_entries="${new_entries:+${new_entries}$'\n'}${key} ${epoch}"
      found=true
    elif [[ -n "$key" ]]; then
      new_entries="${new_entries:+${new_entries}$'\n'}${key} ${val}"
    fi
  done <<<"${dispatch_time:-}"
  if [[ "$found" == "false" ]]; then
    new_entries="${new_entries:+${new_entries}$'\n'}${target} ${epoch}"
  fi
  dispatch_time="$new_entries"
}

rapid_terminal_count_for_key() {
  # Lookup rapid-terminal count for a key from $rapid_terminal_count.
  local target="$1"
  local result=0
  local key count
  while IFS=' ' read -r key count; do
    [[ -z "$key" || "$key" != "$target" || -z "$count" ]] && continue
    result="$count"
    break
  done <<<"${rapid_terminal_count:-}"
  echo "$result"
}

set_rapid_terminal_count() {
  # Set rapid-terminal count for a key in $rapid_terminal_count (global).
  local target="$1" count="$2"
  local new_entries=""
  local key val found=false
  while IFS=' ' read -r key val; do
    if [[ -n "$key" && "$key" == "$target" ]]; then
      new_entries="${new_entries:+${new_entries}$'\n'}${key} ${count}"
      found=true
    elif [[ -n "$key" ]]; then
      new_entries="${new_entries:+${new_entries}$'\n'}${key} ${val}"
    fi
  done <<<"${rapid_terminal_count:-}"
  if [[ "$found" == "false" ]]; then
    new_entries="${new_entries:+${new_entries}$'\n'}${target} ${count}"
  fi
  rapid_terminal_count="$new_entries"
}

get_rapid_terminal_backoff() {
  # Pure helper: given the current rapid-terminal count and whether a fresh
  # rapid terminal was detected THIS cycle, output "count backoff_epoch" where
  # backoff_epoch is 0 if no backoff should be armed.  Arm-once-at-detection,
  # decay-on-expiry: the count resets to 0 when no fresh detection occurs.
  # Usage: read -r new_count backoff_epoch <<< "$(get_rapid_terminal_backoff ...)"
  local current_count="$1"
  local detected="$2"        # "true" or "false"
  local threshold="${3:-2}"
  local backoff_minutes="${4:-5}"
  local now_epoch="${5:-$(date +%s)}"

  if [[ "$detected" == "true" ]]; then
    local n=$((current_count + 1))
    if [[ "$n" -ge "$threshold" ]]; then
      local expiry=$((now_epoch + backoff_minutes * 60))
      echo "$n $expiry"
      return
    fi
    echo "$n 0"
    return
  fi
  # No fresh detection — decay the counter.
  echo "0 0"
}

within_dispatch_cooldown() {
  # Pure helper: check whether a project was dispatched recently enough to
  # skip re-dispatch (guards the window before running.pid appears).
  # Usage: within_dispatch_cooldown <last_dispatch_epoch> <now_epoch> <cooldown_sec>
  # Echoes "true" when now - last < cooldown (skip) and "false" otherwise.
  # Empty/absent last_dispatch_epoch → "false" (never block a first dispatch).
  local last_epoch="$1"
  local now_epoch="$2"
  local cooldown_sec="$3"

  if [[ -z "$last_epoch" ]]; then
    echo "false"
    return
  fi

  local elapsed=$(( now_epoch - last_epoch ))
  if [[ "$elapsed" -lt "$cooldown_sec" ]]; then
    echo "true"
  else
    echo "false"
  fi
}

count_live_sentinels() {
  # Count how many projects in the JSON array currently have a live
  # running.pid sentinel. Outputs the count to stdout.
  local scan_output="$1"
  local count=0
  local paths line
  paths=()
  while IFS= read -r line; do paths+=("$line"); done < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(p['path']) for p in d]" <<<"$scan_output" | tr -d '\r')
  for p in "${paths[@]}"; do
    if test_running_pid "$p"; then
      count=$((count + 1))
    fi
  done
  echo "$count"
}

get_slot_home() {
  # Compute the worker home path for a given slot id.
  # Slot 1 = base ~/.claude-worker; slot i>=2 = ~/.claude-worker-<i>.
  local slot_id="$1"
  if [[ "$slot_id" -le 1 ]]; then
    echo "$HOME/.claude-worker"
  else
    echo "$HOME/.claude-worker-${slot_id}"
  fi
}

invoke_scheduler_scan() {
  # Run scheduler_scan.py, output JSON to stdout
  # Strip \r for Windows compatibility
  $PYTHON "$SCAN_SCRIPT" | tr -d '\r'
}

read_blacklist_from_postmortems() {
  # Check queued projects for recent postmortem files with blacklist
  # classifications. Outputs one line per blacklisted project: "key epoch".
  local scan_output="$1"
  # Delegate the blacklist-vs-resolve-ack decision to blacklist_status.py (the
  # single source of truth shared with scheduler.ps1), so the cleared_at >=
  # generated_at ack-override lives in one place.
  local bl_dir="${_SKILL_ROOT}/ilk-watchdog/scripts"
  BL_DIR="$bl_dir" $PYTHON -c "
import json, os, sys
from datetime import datetime
sys.path.insert(0, os.environ['BL_DIR'])
import blacklist_status as bl

projects = json.loads(sys.stdin.read())
for proj in projects:
    r = bl.is_blacklisted(proj['path'])
    if r.get('blacklisted') and r.get('expiry'):
        try:
            epoch = int(datetime.fromisoformat(r['expiry']).timestamp())
        except (ValueError, TypeError):
            epoch = 0
        if epoch:
            print(f\"{proj['key']} {epoch}\")
" <<<"$scan_output" | tr -d '\r'
}

# --- multiplexer selection (tmux vs screen) -----------------------------------

# ILK_MULTIPLEXER: auto (default) = tmux if present, else screen;
# screen = force screen; tmux = require tmux.
resolve_multiplexer() {
  local mux="${ILK_MULTIPLEXER:-auto}"
  case "$mux" in
    screen)
      echo "screen"
      ;;
    tmux)
      if command -v tmux &>/dev/null; then
        echo "tmux"
      else
        echo "tmux-required-but-missing"
      fi
      ;;
    auto|*)
      if command -v tmux &>/dev/null; then
        echo "tmux"
      else
        echo "screen"
      fi
      ;;
  esac
}

# Ensure the ilk tmux session exists.
ensure_ilmux_session() {
  if ! tmux has-session -t ilk 2>/dev/null; then
    tmux new-session -d -s ilk -n "scheduler"
  fi
}

# --- main loop ---------------------------------------------------------------

run_scheduler() {
  local dispatch_count=0
  # Bash 3.2 compatible blacklist backoff state.
  # newline-separated entries: "project-key expiry-epoch" (max epoch wins).
  local blacklist_skip=""
  # Dispatch time tracking (newline-separated "key epoch").
  local dispatch_time=""
  # Rapid-terminal counter (newline-separated "key count").
  local rapid_terminal_count=""

  # Gate dispatches with --run-local-checks by default.
  # Opt-out: --no-local-checks or ILK_SCHED_NO_GATES=1.
  local run_local_checks_flag=false
  if [[ "$NO_LOCAL_CHECKS" != "true" && "${ILK_SCHED_NO_GATES:-}" != "1" ]]; then
    run_local_checks_flag=true
  fi

  local current_mux
  current_mux="$(resolve_multiplexer)"
  if [[ "$current_mux" == "tmux-required-but-missing" ]]; then
    echo "ERROR: ILK_MULTIPLEXER=tmux but tmux not found on PATH" >&2
    return 1
  fi

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
        write_scheduler_log "idle" "" "all-queues-empty"
        echo '{"decision":"idle","reason":"all queues empty"}'
        return
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle: all queues empty. Polling in ${POLL_MIN} min."
      write_scheduler_log "idle" "" "all-queues-empty"
      sleep $((POLL_MIN * 60))
      continue
    fi

    # --- check budget ceiling ---
    # MAX_DISPATCHES -1 = unlimited; >= 0 = hard ceiling.
    if [[ "$MAX_DISPATCHES" -ge 0 && "$dispatch_count" -ge "$MAX_DISPATCHES" ]]; then
      if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
        write_scheduler_log "idle" "" "budget-ceiling"
        echo '{"decision":"idle","reason":"budget ceiling"}'
        return
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle: budget ceiling (dispatched ${dispatch_count}/${MAX_DISPATCHES}). Polling in ${POLL_MIN} min."
      write_scheduler_log "idle" "" "budget-ceiling"
      sleep $((POLL_MIN * 60))
      continue
    fi

    # --- check concurrency capacity ---
    # Count live sentinels across all scanned projects.
    local live_count
    live_count=$(count_live_sentinels "$scan_output")
    if [[ "$live_count" -ge "$MAX_CONCURRENT" ]]; then
      if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
        write_scheduler_log "idle" "" "capacity-full ($live_count/$MAX_CONCURRENT)"
        echo "{\"decision\":\"idle\",\"reason\":\"capacity-full\",\"live\":$live_count,\"max_concurrent\":$MAX_CONCURRENT}"
        return
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle: capacity full ($live_count/$MAX_CONCURRENT live). Polling in ${POLL_MIN} min."
      write_scheduler_log "idle" "" "capacity-full ($live_count/$MAX_CONCURRENT)"
      sleep $((POLL_MIN * 60))
      continue
    fi

    # --- recompute postmortem blacklist FRESH each cycle (no accumulator) ---
    # A project is postmortem-blacklisted iff THIS cycle's on-disk decision
    # (blacklist_status.py) says so. Deliberately NOT appended to the persistent
    # $blacklist_skip: that accumulation wedged projects off the queue when a
    # stale entry outlived a not-blacklisted flip. Mirrors scheduler.ps1.
    local postmortem_blacklist=""
    while IFS=' ' read -r bl_key bl_epoch; do
      [[ -z "$bl_key" ]] && continue
      postmortem_blacklist="${postmortem_blacklist}"$'\n'"${bl_key} ${bl_epoch}"
    done < <(read_blacklist_from_postmortems "$scan_output")

    # --- iterate projects in FIFO order, fill free slots ---
    local remaining_capacity=$((MAX_CONCURRENT - live_count))
    local now_epoch
    now_epoch=$(date +%s)

    # Collect dispatchable projects (keys, paths, repos, has_actives).
    local -a disp_keys=() disp_paths=() disp_repos=() disp_actives=()

    # Parse the JSON array and iterate
    local keys paths repo_paths has_actives line
    keys=(); paths=(); repo_paths=(); has_actives=()
    while IFS= read -r line; do keys+=("$line"); done < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(p['key']) for p in d]" <<<"$scan_output" | tr -d '\r')
    while IFS= read -r line; do paths+=("$line"); done < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(p['path']) for p in d]" <<<"$scan_output" | tr -d '\r')
    while IFS= read -r line; do repo_paths+=("$line"); done < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(p.get('repo_path') or '') for p in d]" <<<"$scan_output" | tr -d '\r')
    while IFS= read -r line; do has_actives+=("$line"); done < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(str(p.get('has_active_master', True)).lower()) for p in d]" <<<"$scan_output" | tr -d '\r')

    for i in "${!keys[@]}"; do
      local key="${keys[$i]}"
      local path="${paths[$i]}"
      local repo="${repo_paths[$i]}"

      # blacklist / backoff skip — postmortem set is FRESH this cycle (never
      # accumulated); $blacklist_skip holds only transient backoffs.
      local pm_epoch bo_epoch skip_decision=""
      pm_epoch="$(blacklist_epoch_for_key "$key" "$postmortem_blacklist")"
      bo_epoch="$(blacklist_epoch_for_key "$key" "$blacklist_skip")"
      if [[ -n "$pm_epoch" && "$now_epoch" -lt "$pm_epoch" ]]; then
        skip_decision="skip-blacklist"
      elif [[ -n "$bo_epoch" && "$now_epoch" -lt "$bo_epoch" ]]; then
        skip_decision="skip-backoff"
      fi
      if [[ -n "$skip_decision" ]]; then
        if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
          write_scheduler_log "$skip_decision" "$key"
          echo "{\"decision\":\"$skip_decision\",\"key\":\"$key\"}"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${skip_decision}: $key"
          write_scheduler_log "$skip_decision" "$key"
        fi
        continue
      fi

      # Backoff window elapsed — decay the rapid-terminal counter so the
      # project re-enters with a fresh slate (mirrors scheduler.ps1 cleanup).
      if [[ -n "$bo_epoch" && "$now_epoch" -ge "$bo_epoch" ]]; then
        set_rapid_terminal_count "$key" 0
      fi

      # --- rapid-terminal check: project went terminal within ~60s of dispatch ---
      local dispatch_epoch
      dispatch_epoch="$(dispatch_time_epoch_for_key "$key")"
      if [[ -n "$dispatch_epoch" ]]; then
        local sentinel_file="${path}/runtime/last-exit.json"
        if [[ -f "$sentinel_file" ]]; then
          # Parse sentinel JSON fields via inline python (reuse utf-8-sig idiom).
          local started_at ended_at sentinel_state dur_sec
          started_at="$("$PYTHON" -c "
import json,sys
try:
    d=json.loads(open(sys.argv[1],encoding='utf-8-sig').read())
    print(d.get('started_at','') or '')
except: pass
" "$sentinel_file" 2>/dev/null)" || true
          ended_at="$("$PYTHON" -c "
import json,sys
try:
    d=json.loads(open(sys.argv[1],encoding='utf-8-sig').read())
    print(d.get('ended_at','') or '')
except: pass
" "$sentinel_file" 2>/dev/null)" || true
          sentinel_state="$("$PYTHON" -c "
import json,sys
try:
    d=json.loads(open(sys.argv[1],encoding='utf-8-sig').read())
    print(d.get('state','') or '')
except: pass
" "$sentinel_file" 2>/dev/null)" || true

          local is_rapid=false
          if [[ -n "$started_at" && -n "$ended_at" && "$sentinel_state" != "running" && -n "$sentinel_state" ]]; then
            # Correlation: sentinel must belong to THIS dispatch — started_at >= dispatch - 5s skew.
            is_rapid="$("$PYTHON" -c "
from datetime import datetime
sa=datetime.fromisoformat(sys.argv[1])
ea=datetime.fromisoformat(sys.argv[2])
de=float(sys.argv[3])
SKEW=5
started_epoch=sa.timestamp()
ended_epoch=ea.timestamp()
if started_epoch < de - SKEW: print('false')  # stale prior-run sentinel
else:
    dur=ended_epoch - started_epoch
    print('true' if 0 <= dur < 60 else 'false')
" "$started_at" "$ended_at" "$dispatch_epoch" 2>/dev/null)" || true
          fi

          local cur_rt_count
          cur_rt_count="$(rapid_terminal_count_for_key "$key")"
          local rt_result
          rt_result="$(get_rapid_terminal_backoff "$cur_rt_count" "$is_rapid" 2 5 "$now_epoch")"
          local new_rt_count rt_backoff_epoch
          read -r new_rt_count rt_backoff_epoch <<<"$rt_result"
          set_rapid_terminal_count "$key" "$new_rt_count"
          if [[ "$rt_backoff_epoch" -gt 0 ]]; then
            # Arm the backoff ONCE at detection (not re-armed every cycle).
            # Compute real duration for logging (ended_at - started_at).
            dur_sec="$("$PYTHON" -c "
from datetime import datetime
sa=datetime.fromisoformat(sys.argv[1])
ea=datetime.fromisoformat(sys.argv[2])
print(int((ea-sa).total_seconds()))
" "$started_at" "$ended_at" 2>/dev/null)" || dur_sec=0
            blacklist_skip="${blacklist_skip}"$'\n'"${key} ${rt_backoff_epoch}"
            if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
              write_scheduler_log "skip-rapid-terminal" "$key" "count=$new_rt_count elapsed=${dur_sec}s"
              echo "{\"decision\":\"skip-rapid-terminal\",\"key\":\"$key\",\"count\":$new_rt_count}"
            else
              echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip-rapid-terminal: $key (count=$new_rt_count, elapsed=${dur_sec}s)"
              write_scheduler_log "skip-rapid-terminal" "$key" "count=$new_rt_count elapsed=${dur_sec}s"
            fi
            continue
          fi
        fi
      fi

      # Dispatch cooldown: skip re-dispatch within DISPATCH_COOLDOWN_SEC of a
      # prior dispatch when no running.pid sentinel has appeared yet.  Guards
      # the window between launch and sentinel write.  If a sentinel IS live,
      # the skip-busy check below already covers it.
      local last_dispatch
      last_dispatch="$(dispatch_time_epoch_for_key "$key")"
      if [[ "$(within_dispatch_cooldown "$last_dispatch" "$now_epoch" "$DISPATCH_COOLDOWN_SEC")" == "true" ]] && ! test_running_pid "$path"; then
        if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
          write_scheduler_log "skip-cooldown" "$key"
          echo "{\"decision\":\"skip-cooldown\",\"key\":\"$key\"}"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip-cooldown: $key"
          write_scheduler_log "skip-cooldown" "$key"
        fi
        continue
      fi

      # Check if project is busy
      if test_running_pid "$path"; then
        if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
          write_scheduler_log "skip-busy" "$key"
          echo "{\"decision\":\"skip-busy\",\"key\":\"$key\"}"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip-busy: $key"
          write_scheduler_log "skip-busy" "$key"
        fi
        continue
      fi

      # Cannot dispatch a project whose source repo path is unknown
      # (never launched + not in projects.json). Skip, don't guess.
      if [[ -z "$repo" ]]; then
        if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
          write_scheduler_log "skip-unresolved" "$key"
          echo "{\"decision\":\"skip-unresolved\",\"key\":\"$key\"}"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip-unresolved: $key (no repo path; launch it once or add to projects.json)"
          write_scheduler_log "skip-unresolved" "$key"
        fi
        continue
      fi

      # Fill free slots: collect while capacity remains.
      if [[ ${#disp_keys[@]} -lt $remaining_capacity ]]; then
        disp_keys+=("$key")
        disp_paths+=("$path")
        disp_repos+=("$repo")
        disp_actives+=("${has_actives[$i]}")
      fi
    done

    if [[ ${#disp_keys[@]} -eq 0 ]]; then
      if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
        write_scheduler_log "idle" "" "no-dispatchable-project"
        echo '{"decision":"idle","reason":"no dispatchable project"}'
        return
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] idle: no dispatchable project (all busy/blacklisted/unresolved). Polling in ${POLL_MIN} min."
      write_scheduler_log "idle" "" "no-dispatchable-project"
      sleep $((POLL_MIN * 60))
      continue
    fi

    # --- promote + dispatch each selected project into a slot ---
    local slot_id=0
    for j in "${!disp_keys[@]}"; do
      slot_id=$((slot_id + 1))
      local dkey="${disp_keys[$j]}"
      local dpath="${disp_paths[$j]}"
      local drepo="${disp_repos[$j]}"
      local dactive="${disp_actives[$j]}"
      local slot_home
      slot_home="$(get_slot_home "$slot_id")"

      # promote-before-dispatch (multi-master queue advancement)
      if [[ "$dactive" == "false" ]]; then
        local plans_dir="${dpath}/plans"
        if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
          local promo_json=""
          promo_json=$($PYTHON "$PROMOTE_SCRIPT" --project "$dpath" --plans-dir "$plans_dir" --dry-run 2>/dev/null) || true
          if [[ -n "$promo_json" ]]; then
            local promoted_name
            promoted_name=$($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('promoted',''))" <<<"$promo_json" | tr -d '\r')
            if [[ -n "$promoted_name" ]]; then
              local demoted_name
              demoted_name=$($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('demoted','') or '')" <<<"$promo_json" | tr -d '\r')
              write_scheduler_log "promote" "$dkey -> $promoted_name"
              echo "{\"decision\":\"promote\",\"key\":\"$dkey\",\"promoted\":\"$promoted_name\",\"demoted\":\"$demoted_name\"}"
            fi
          fi
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] promoting queued master for $dkey..."
          local promo_json=""
          promo_json=$($PYTHON "$PROMOTE_SCRIPT" --project "$dpath" --plans-dir "$plans_dir" 2>/dev/null) || true
          if [[ -n "$promo_json" ]]; then
            local promoted_name
            promoted_name=$($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('promoted',''))" <<<"$promo_json" | tr -d '\r')
            if [[ -n "$promoted_name" ]]; then
              echo "[$(date '+%Y-%m-%d %H:%M:%S')] promoted $promoted_name"
              write_scheduler_log "promote" "$dkey -> $promoted_name"
            fi
          fi
        fi
      fi

      # dispatch into slot home
      local local_checks_flag=""
      if [[ "$run_local_checks_flag" == "true" ]]; then
        local_checks_flag=" --run-local-checks"
      fi
      if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
        # Use forward slashes in paths for valid JSON (Windows backslashes are invalid escapes)
        local safe_path="${drepo//\\//}"
        write_scheduler_log "dispatch" "$dkey (slot $slot_id)"
        if [[ "$current_mux" == "tmux" ]]; then
          local tmux_cmd="tmux new-window -t ilk -n '$dkey' 'launch.sh --project-path \\\"'$safe_path'\\\" --engine claude-worker --worker-home \\\"'$slot_home'\\\"${local_checks_flag}'"
          echo "{\"decision\":\"dispatch\",\"key\":\"$dkey\",\"slot\":$slot_id,\"multiplexer\":\"tmux\",\"command\":\"$tmux_cmd\",\"watchdog\":\"watchdog.sh --project-path '$safe_path' --detach\"}"
        else
          echo "{\"decision\":\"dispatch\",\"key\":\"$dkey\",\"slot\":$slot_id,\"multiplexer\":\"screen\",\"command\":\"launch.sh --project-path '$safe_path' --engine claude-worker --worker-home '$slot_home'${local_checks_flag}\",\"watchdog\":\"watchdog.sh --project-path '$safe_path' --detach\"}"
        fi
        set_dispatch_time "$dkey" "$(date +%s)"
      elif [[ "$DRY_RUN" == true ]]; then
        if [[ "$current_mux" == "tmux" ]]; then
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] DRY-RUN [tmux]: would dispatch $dkey (slot $slot_id) via tmux new-window -t ilk -n '$dkey' '$LAUNCH_SCRIPT --project-path $drepo --engine claude-worker --worker-home $slot_home'"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] DRY-RUN [screen]: would dispatch $dkey (slot $slot_id) via $LAUNCH_SCRIPT --project-path '$drepo' --engine claude-worker --worker-home '$slot_home'"
        fi
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] DRY-RUN: would attach watchdog via $WATCHDOG_SCRIPT --project-path '$drepo' --detach"
      else
        # Ensure slot home exists (lazy-clone from base worker home).
        bash "$BOOTSTRAP_SCRIPT" --clone-slot "$slot_id" >/dev/null 2>&1 || true
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] dispatching $dkey (slot $slot_id) [mux=$current_mux]..."
        local launch_cmd="bash $LAUNCH_SCRIPT --project-path '$drepo' --engine claude-worker --worker-home '$slot_home'${local_checks_flag} --force"
        if [[ "$current_mux" == "tmux" ]]; then
          ensure_ilmux_session
          if tmux new-window -t ilk -n "$dkey" "$launch_cmd"; then
            dispatch_count=$((dispatch_count + 1))
            set_dispatch_time "$dkey" "$(date +%s)"
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] dispatched $dkey (slot $slot_id, total: $dispatch_count) [tmux]"
            write_scheduler_log "dispatch" "$dkey (slot $slot_id)"
            invoke_ilk_notify "dispatch" "$dkey" "slot $slot_id"
            # Attach watchdog for this dispatch (supervises the run).
            # The watchdog has its own double-spawn guard (watchdog.pid).
            bash "$WATCHDOG_SCRIPT" --project-path "$drepo" --detach 2>/dev/null || true
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] watchdog attached for $dkey"
          else
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] tmux dispatch failed for $dkey"
            blacklist_skip="${blacklist_skip}"$'\n'"${dkey} $(($(date +%s) + 300))"
          fi
        elif bash "$LAUNCH_SCRIPT" --project-path "$drepo" --engine claude-worker --worker-home "$slot_home" ${local_checks_flag} --force; then
          dispatch_count=$((dispatch_count + 1))
          set_dispatch_time "$dkey" "$(date +%s)"
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] dispatched $dkey (slot $slot_id, total: $dispatch_count)"
          write_scheduler_log "dispatch" "$dkey (slot $slot_id)"
          invoke_ilk_notify "dispatch" "$dkey" "slot $slot_id"
          # Attach watchdog for this dispatch (supervises the run).
          # The watchdog has its own double-spawn guard (watchdog.pid).
          bash "$WATCHDOG_SCRIPT" --project-path "$drepo" --detach 2>/dev/null || true
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] watchdog attached for $dkey"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] dispatch failed for $dkey"
          blacklist_skip="${blacklist_skip}"$'\n'"${dkey} $(($(date +%s) + 300))"
        fi
      fi
    done

    if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
      return
    fi

    sleep $((POLL_MIN * 60))
  done
}

# --- detach helper -----------------------------------------------------------

detach_scheduler() {
  if ! command -v screen &>/dev/null; then
    echo "ERROR: 'screen' is not installed. Install it (apt install screen / brew install screen) or run without --detach." >&2
    release_scheduler_lock
    exit 1
  fi

  local self="${BASH_SOURCE[0]}"
  local cmd="bash '$self' --poll-min '$POLL_MIN' --max-concurrent '$MAX_CONCURRENT' --max-dispatches '$MAX_DISPATCHES' --max-budget-usd '$MAX_BUDGET_USD'"
  if [[ "$NO_LOCAL_CHECKS" == "true" ]]; then
    cmd="$cmd --no-local-checks"
  fi

  if [[ "$DRY_RUN" == true ]]; then
    echo "[ilk-scheduler] (dry-run) would spawn detached: screen -dmS ilk-scheduler $cmd"
    release_scheduler_lock
    exit 0
  fi

  local session_name="ilk-scheduler"

  # Release lock before spawning child — child acquires its own.
  release_scheduler_lock
  screen -dmS "$session_name" bash -c "$cmd"
  echo "[ilk-scheduler] detached screen session started: $session_name"
  echo "  Attach with: screen -r $session_name"
  exit 0
}

# --- entry point -------------------------------------------------------------

parse_args "$@"

if [[ "$DETACH" == true ]]; then
  detach_scheduler
fi

run_scheduler
