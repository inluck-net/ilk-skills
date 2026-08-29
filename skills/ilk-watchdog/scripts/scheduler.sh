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

# Sourced here, not next to the skill-root resolution below: the lock is
# acquired at source time (before that block runs) and needs ilk_pid_alive.
source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_pid.sh"

SCHEDULER_PIDFILE="${HOME}/.ilk-data/scheduler.pid"
SCHEDULER_STATE_FILE="${HOME}/.ilk-data/scheduler.state.json"

write_scheduler_state() {
  # Write scheduler.state.json with {pid, started_at, toolkit_head}.
  # Fail open: a write failure never prevents startup (AC-5).
  local state_file="$SCHEDULER_STATE_FILE"
  local pid="$$"
  local started_at
  started_at="$(date -u '+%Y-%m-%dT%H:%M:%S+00:00')" || started_at=""

  # Resolve toolkit_head from the script's own location, not $PWD (AC-2).
  # launchd starts the job in an arbitrary directory.
  local toolkit_head=""
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || true
  if [[ -n "$script_dir" ]]; then
    # Walk up to find the repo root (the dir containing .git).
    local repo_dir="$script_dir"
    while [[ "$repo_dir" != "/" && ! -d "$repo_dir/.git" ]]; do
      repo_dir="$(dirname "$repo_dir")"
    done
    if [[ -d "$repo_dir/.git" ]]; then
      toolkit_head="$(git -C "$repo_dir" rev-parse HEAD 2>/dev/null)" || toolkit_head=""
    fi
  fi

  # Write the state file. If anything is missing, log and return (AC-5).
  if [[ -z "$started_at" ]]; then
    echo "[ilk-scheduler] WARNING: could not resolve timestamp for state file" >&2
    return 0
  fi
  if [[ -z "$toolkit_head" ]]; then
    echo "[ilk-scheduler] WARNING: could not resolve toolkit HEAD for state file (not a git clone?)" >&2
    return 0
  fi

  mkdir -p "$(dirname "$state_file")"
  if ! printf '{"pid":%d,"started_at":"%s","toolkit_head":"%s"}\n' \
       "$pid" "$started_at" "$toolkit_head" > "$state_file"; then
    echo "[ilk-scheduler] WARNING: could not write state file $state_file" >&2
  fi
}

acquire_scheduler_lock() {
  # Use a pidfile with liveness check. Portable (no flock dependency).
  local pidfile="$SCHEDULER_PIDFILE"
  if [[ -f "$pidfile" ]]; then
    local old_pid
    old_pid=$(tr -d '[:space:]' < "$pidfile" 2>/dev/null) || true
    # ilk_pid_alive, not bare `kill -0`: a recycled PID here is unrecoverable.
    # A false "already running" exits 0, and the LaunchAgent's KeepAlive is
    # SuccessfulExit=false, so launchd never restarts it — the scheduler stays
    # dead until someone notices by hand.
    if ilk_pid_alive "$old_pid"; then
      echo "[ilk-scheduler] already running (PID $old_pid). Exiting."
      exit 0
    fi
    # Stale pidfile — remove and proceed.
    rm -f "$pidfile"
  fi
  # Write our PID.
  mkdir -p "$(dirname "$pidfile")"
  echo $$ > "$pidfile"
  # Write the state file with toolkit head (AC-1, AC-4, AC-6).
  write_scheduler_state
}

release_scheduler_lock() {
  rm -f "$SCHEDULER_PIDFILE" 2>/dev/null || true
}

# Acquire lock immediately at source time.
acquire_scheduler_lock

# --- skill root resolution ---------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# --- defaults ----------------------------------------------------------------

SCAN_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/scheduler_scan.py"
# Holds the most recent scan's stderr so the idle branch can tell
# "no work" from "could not look". See invoke_scheduler_scan.
_SCAN_STDERR_FILE="$(mktemp "${TMPDIR:-/tmp}/ilk-scan-stderr-XXXXXX")"

# Combined cleanup: release the pidfile lock AND remove the scan-stderr tempfile.
# WARNING: a second `trap ... EXIT` silently REPLACES the first in bash.
# Register exactly once here; do not add another `trap ... EXIT` below.
_scheduler_cleanup() {
  rm -f "$SCHEDULER_PIDFILE" 2>/dev/null || true
  rm -f "$_SCAN_STDERR_FILE" 2>/dev/null || true
}
trap _scheduler_cleanup EXIT

# Signal handlers: log the signal, then exit 128+signo.
# The EXIT trap (_scheduler_cleanup) fires automatically on exit, so the
# pidfile is released and the tempfile removed without a second trap.
_scheduler_handle_sig() {
  local name="$1" num="$2"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] stopping: received ${name}" >&2
  type write_scheduler_log &>/dev/null && write_scheduler_log "signal" "" "$name"
  exit $((128 + num))
}
# SIGTERM(15), SIGINT(2), SIGHUP(1) — all three per AC-2/AC-3.
trap '_scheduler_handle_sig SIGTERM 15' TERM
trap '_scheduler_handle_sig SIGINT 2' INT
trap '_scheduler_handle_sig SIGHUP 1' HUP

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
  local project_repo_path="${2:-$project_data_path}"
  # Process-table check: a live runner makes the project busy even when
  # running.pid is absent or names a different PID.  running.pid tracked 1 of
  # 10 live runners on 2026-08-12.  Use the repo path (not data path) because
  # the runner's --project-path flag contains the repo path.
  # stdout suppressed: ilk_project_runners PRINTS the pids it finds, and all
  # three callers here use it as a boolean, so without this the pids leak into
  # the scheduler's stdout and interleave with its decision lines.
  if ilk_project_runners "$project_repo_path" >/dev/null 2>&1; then
    return 0  # busy — live runner in the process table
  fi
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
  # Command-verified, not bare `kill -0`: a recycled PID makes this return
  # "busy" on every poll forever, and the cross-check below cannot rescue it
  # because a sentinel abandoned mid-run still reads state="running".
  if ! ilk_pid_alive "$raw"; then
    return 1  # dead pid (or not an ilk process) — free
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

# --- no-progress dispatch bound (2026-08-29) ---------------------------------
#
# read_blacklist_from_postmortems below builds the blacklist from postmortem
# FILES.  No postmortem => no entry => dispatchable forever.  That is how three
# launches ran on rezmac on 2026-08-29 (12:01, 12:37, 13:12) with the scheduler
# log reading promote: / dispatch: / skip-busy throughout.
#
#   A bound that requires a successful postmortem is a bound that switches off
#   exactly when things are worst.
#
# This bound is ADDITIVE and deliberately depends on nothing but launch history:
# consecutive launches that each ended non-clean with no plan progress.
# read_blacklist_from_postmortems is unchanged (AC-7) and remains the richer
# signal when a postmortem does exist.
#
# GAP, recorded rather than left implied (AC-6): a project driven by a manual
# `/ilk-run` plus a watchdog, with NO scheduler, has no such bound.  The
# scheduler owns the dispatch decision, so that is where the bound lives; a
# second copy in the watchdog would mean two components with independent,
# drifting notions of "no progress".

#: Consecutive non-clean, no-progress launches before dispatch stops.
#: Three, on three grounds: the observed launch count on 2026-08-29;
#: run_ilk_loop_claude.sh:2193 already uses `no_progress_streak -ge 3`; and
#: collect.py splits its local_checks_failed narrowing on `iter_count < 3`.
#: A fourth threshold would be one more number to reconcile.
NO_PROGRESS_THRESHOLD="${ILK_NO_PROGRESS_THRESHOLD:-3}"

get_no_progress_verdict() {
  # Pure helper: decide whether to dispatch, given launch history.
  # Usage: read -r new_count decision <<< "$(get_no_progress_verdict ...)"
  #   <current_count> <progressed:true|false> <clean_exit:true|false> [threshold]
  # Echoes "<new_count> <decision>", decision = allow | block.
  #
  # Contract 6 (detached-component-contracts.md): this function's stdout is its
  # return value, so it must not log.  Diagnostics belong to the caller.
  local current_count="$1"
  local progressed="$2"
  local clean_exit="$3"
  local threshold="${4:-$NO_PROGRESS_THRESHOLD}"

  # Progress resets, whatever else happened: a slow batch that is advancing
  # must never be blocked by this.
  if [[ "$progressed" == "true" ]]; then
    echo "0 allow"
    return
  fi
  # A clean exit resets, whatever the plan state: a run can finish cleanly with
  # no step advance (everything already shipped).  That is success, not a stall.
  if [[ "$clean_exit" == "true" ]]; then
    echo "0 allow"
    return
  fi

  local n=$((current_count + 1))
  if [[ "$n" -ge "$threshold" ]]; then
    echo "$n block"
  else
    echo "$n allow"
  fi
}

no_progress_cleared_by_ack() {
  # Pure helper: has an operator acknowledged this bound?
  # Usage: no_progress_cleared_by_ack <counter_epoch> <ack_epoch>
  # Echoes "true" when ack_epoch >= counter_epoch.
  #
  # `>=`, not `>`, deliberately: blacklist_status.py clears a postmortem
  # blacklist on `cleared_at >= generated_at`, and an operator must have ONE
  # way to say "I looked at it, carry on" (AC-8).  Two bounds that disagree on
  # the boundary would mean /ilk-resume clears one and silently leaves the
  # other in force.
  local counter_epoch="${1:-0}"
  local ack_epoch="${2:-0}"
  [[ -z "$counter_epoch" ]] && counter_epoch=0
  [[ -z "$ack_epoch" ]] && ack_epoch=0
  if [[ "$ack_epoch" -ge "$counter_epoch" && "$ack_epoch" -gt 0 ]]; then
    echo "true"
  else
    echo "false"
  fi
}

no_progress_state_file() {
  # Pure helper: path to a project's no-progress counter sidecar.
  # Kept beside the sentinel, NOT in postmortems/ -- the whole point is that
  # this bound does not live in a directory that may not exist.
  local project_data_dir="$1"
  echo "${project_data_dir}/runtime/launcher/no-progress.json"
}

progress_signature_for_project() {
  # Echo a signature of the project's plan progress.  Two launches with the
  # same signature made no progress between them.
  #
  # Derived from every sub-plan's status + current_step, so a step advance OR a
  # ship changes it.  Reads the plans dir directly: no dependency on a
  # postmortem, a JSONL log, or a successful classification.
  local project_data_dir="$1"
  PLANS_DIR="${project_data_dir}/plans" $PYTHON -c "
import hashlib, os, re, pathlib
d = pathlib.Path(os.environ['PLANS_DIR'])
parts = []
if d.is_dir():
    for f in sorted(d.glob('*.md')):
        try:
            head = f.read_text(encoding='utf-8-sig', errors='replace')[:2000]
        except OSError:
            continue
        st = re.search(r'^status:\s*(\S+)', head, re.M)
        cs = re.search(r'^current_step:\s*(\S+)', head, re.M)
        parts.append(f'{f.name}:{st.group(1) if st else \"?\"}:{cs.group(1) if cs else \"?\"}')
print(hashlib.sha1('|'.join(parts).encode()).hexdigest()[:12] if parts else 'no-plans')
" 2>/dev/null || echo "unknown"
}

read_no_progress_state() {
  # Echo "<count> <signature> <updated_epoch>" for a project, or "0 none 0".
  local state_file="$1"
  [[ -f "$state_file" ]] || { echo "0 none 0"; return; }
  STATE_FILE="$state_file" $PYTHON -c "
import json, os
try:
    d = json.load(open(os.environ['STATE_FILE'], encoding='utf-8-sig'))
except Exception:
    print('0 none 0'); raise SystemExit
print(f\"{int(d.get('count', 0))} {d.get('signature', 'none')} {int(d.get('updated_epoch', 0))}\")
" 2>/dev/null || echo "0 none 0"
}

write_no_progress_state() {
  # Persist the counter.  Survives a scheduler restart -- launchd KeepAlive
  # bounces this daemon, and an in-memory counter would reset the bound every
  # time, which is the failure mode being fixed.
  local state_file="$1" count="$2" signature="$3" now_epoch="${4:-$(date +%s)}"
  mkdir -p "$(dirname "$state_file")" 2>/dev/null || true
  printf '{"count":%d,"signature":"%s","updated_epoch":%d}\n' \
    "$count" "$signature" "$now_epoch" > "$state_file" 2>/dev/null || true
}

write_no_progress_refusal() {
  # AC-5: the refusal must be LOUD.  The defining property of the observed
  # failure was a scheduler log that looked healthy throughout; a silent
  # refusal would repeat that in the other direction.
  local key="$1" count="$2" threshold="${3:-$NO_PROGRESS_THRESHOLD}"
  # Structured (decision, key, reason) form, matching every other decision line
  # so the log stays greppable by decision.
  write_scheduler_log "no-progress-bound" "$key" \
    "$count consecutive launches ended non-clean with no plan progress (threshold $threshold); dispatch stopped -- clear with /ilk-resume once triaged"
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
  local paths repo_paths line
  paths=()
  repo_paths=()
  while IFS= read -r line; do paths+=("$line"); done < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(p['path']) for p in d]" <<<"$scan_output" | tr -d '\r')
  while IFS= read -r line; do repo_paths+=("$line"); done < <($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); [print(p.get('repo_path') or '') for p in d]" <<<"$scan_output" | tr -d '\r')
  for i in "${!paths[@]}"; do
    local p="${paths[$i]}"
    local rp="${repo_paths[$i]}"
    if test_running_pid "$p" "$rp"; then
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
  #
  # stderr is captured to _SCAN_STDERR_FILE rather than inherited: the scan
  # names any project it could not read on stderr as `[scan-error] <key>: ...`,
  # and the idle branch below needs that to log a reason other than
  # `all-queues-empty`. A project that CANNOT BE SCANNED looked identical to a
  # project with NO WORK until 2026-08-20, when a TypeError silently retired a
  # project for three hours while every poll logged `all-queues-empty`.
  # stderr is still echoed through to the daemon log, so nothing is hidden.
  : > "$_SCAN_STDERR_FILE"
  $PYTHON "$SCAN_SCRIPT" 2>"$_SCAN_STDERR_FILE" | tr -d '\r'
  local rc=${PIPESTATUS[0]}
  if [[ -s "$_SCAN_STDERR_FILE" ]]; then
    cat "$_SCAN_STDERR_FILE" >&2
  fi
  return "$rc"
}

scan_error_keys() {
  # Echo a comma-separated list of project keys the last scan could not read,
  # or nothing when the scan was clean.
  [[ -s "$_SCAN_STDERR_FILE" ]] || return 0
  sed -n 's/^\[scan-error\] \([^:]*\):.*/\1/p' "$_SCAN_STDERR_FILE" \
    | sort -u | paste -sd, - | sed 's/,$//'
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
      # A CRASHED scan is a third flavour of the same silent failure: until
      # invoke_scheduler_scan started propagating python's exit status (the
      # `| tr` pipeline masked it, making this whole handler dead code), a
      # traceback was read as a valid empty project list and logged as
      # `all-queues-empty`. Record it in the journal the operator actually
      # reads, not only on stderr.
      local scan_fail_detail
      scan_fail_detail=$(tail -n 1 "$_SCAN_STDERR_FILE" 2>/dev/null | tr -d '\n')
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] scheduler_scan.py failed: ${scan_fail_detail}" >&2
      write_scheduler_log "idle" "" "scan-failed: ${scan_fail_detail:-unknown}"
      # --once means one cycle, including a failed one. Without this the
      # scheduler sleeps and retries forever, so a --once invocation whose
      # scan cannot start never returns (it hung the test suite).
      if [[ "$ONCE" == true ]]; then
        echo '{"decision":"idle","reason":"scan-failed"}'
        return
      fi
      sleep $((POLL_MIN * 60)) & wait $!
      continue
    }
    # Strip any remaining \r (Windows line endings)
    scan_output="${scan_output//$'\r'/}"

    local count
    count=$($PYTHON -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))" <<<"$scan_output" | tr -d '\r')

    if [[ "$count" == "0" ]]; then
      # Zero dispatchable is ambiguous: it can mean "every queue is empty" or
      # "the scan could not read some projects". Only the second needs a human,
      # so give it its own reason string rather than folding it into
      # all-queues-empty (the 2026-08-20 silent-retirement failure).
      local scan_err_keys idle_reason idle_msg
      scan_err_keys=$(scan_error_keys)
      if [[ -n "$scan_err_keys" ]]; then
        idle_reason="skip-scan-error: ${scan_err_keys}"
        idle_msg="idle: 0 dispatchable, but could not scan ${scan_err_keys}"
      else
        idle_reason="all-queues-empty"
        idle_msg="idle: all queues empty"
      fi
      if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
        write_scheduler_log "idle" "" "$idle_reason"
        echo "{\"decision\":\"idle\",\"reason\":\"${idle_reason}\"}"
        return
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${idle_msg}. Polling in ${POLL_MIN} min."
      write_scheduler_log "idle" "" "$idle_reason"
      sleep $((POLL_MIN * 60)) & wait $!
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
      sleep $((POLL_MIN * 60)) & wait $!
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
      sleep $((POLL_MIN * 60)) & wait $!
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
      if [[ "$(within_dispatch_cooldown "$last_dispatch" "$now_epoch" "$DISPATCH_COOLDOWN_SEC")" == "true" ]] && ! test_running_pid "$path" "$repo"; then
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
      if test_running_pid "$path" "$repo"; then
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

      # Resolved but absent: a registered path that no longer exists on disk
      # (worktree removed, or `git worktree add` failed and left the entry in
      # projects.json).  launch.sh does reject this with exit 1, but dispatch
      # runs inside a detached `tmux new-window`, so that status dies with the
      # window and the scheduler would re-dispatch every poll forever — a
      # no-op loop that masks the real breakage.  Skip loudly instead.
      if [[ ! -d "$repo" ]]; then
        if [[ "$DRY_RUN" == true && "$ONCE" == true ]]; then
          write_scheduler_log "skip-missing-path" "$key"
          echo "{\"decision\":\"skip-missing-path\",\"key\":\"$key\"}"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip-missing-path: $key (repo path '$repo' does not exist; recreate the worktree or drop it from projects.json)"
          write_scheduler_log "skip-missing-path" "$key"
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
      sleep $((POLL_MIN * 60)) & wait $!
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
        local clone_output
        if ! clone_output="$(bash "$BOOTSTRAP_SCRIPT" --clone-slot "$slot_id" 2>&1)"; then
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] slot $slot_id clone failed: $clone_output" >&2
          write_scheduler_log "clone-failed" "$dkey (slot $slot_id)" "$clone_output"
          continue
        fi
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

    sleep $((POLL_MIN * 60)) & wait $!
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
