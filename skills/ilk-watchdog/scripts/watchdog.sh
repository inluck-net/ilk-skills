#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-watchdog (macOS bash port of watchdog.ps1)
# =============================================================================
# Polls ~/.ilk-data/projects/<key>/runtime/last-exit.json (preferred) and
# falls back to ~/.ilk-data/projects/<key>/runtime/launcher/running.pid every --poll-interval-sec.
# When the loop stops, classifies the run and either relaunches, promotes
# the next master, or blocks with a loud banner.
# =============================================================================

# ----- Skill root resolution -------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# ----- Defaults & globals ----------------------------------------------------

LAUNCHER_DIR="${_SKILL_ROOT}/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"
LAUNCH_SCRIPT="${LAUNCHER_DIR}/scripts/launch.sh"
LOOP_STATUS_PY="${_SKILL_ROOT}/ilk-loop/scripts/loop_status.py"
COLLECT_PY="${_SKILL_ROOT}/ilk-feedback/scripts/collect.py"
NOTIFY_PY="${_SKILL_ROOT}/ilk-watchdog/scripts/ilk_notify.py"

POLL_INTERVAL_SEC=60
MAX_RESTARTS=5

# CLI overrides
CLI_PROJECT_PATH=""
CLI_PROJECT_NAME=""
CLI_POLL_INTERVAL_SEC=""
CLI_MAX_RESTARTS=""
CLI_DETACH=false

# Resolved values
RESOLVED_PATH=""
RESOLVED_NAME=""

# ----- Helpers (project resolution, same pattern as launch.sh) ---------------

# Fire-and-forget desktop notification. Failure is swallowed.
invoke_ilk_notify() {
  local event="$1" project="$2" detail="${3:-}"
  local args=("$NOTIFY_PY" --event "$event" --project "$project")
  [[ -n "$detail" ]] && args+=(--detail "$detail")
  python3 "${args[@]}" 2>/dev/null || true
}

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
  resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
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

get_ilk_launcher_dir() {
  local project="$1"
  local resolver
  resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    echo ""
    return
  fi
  local json_out
  if json_out=$(python3 "$resolver" --start "$project" 2>/dev/null) && [[ -n "$json_out" ]]; then
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_launcher_dir') or '')" <<<"$json_out"
  else
    echo ""
  fi
}

get_ilk_watchdog_dir() {
  local project="$1"
  local resolver
  resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    echo ""
    return
  fi
  local json_out
  if json_out=$(python3 "$resolver" --start "$project" 2>/dev/null) && [[ -n "$json_out" ]]; then
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_watchdog_dir') or '')" <<<"$json_out"
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

test_process_command_alive() {
  local pid="$1"
  local expected="$2"
  if ! test_process_alive "$pid"; then
    return 1
  fi
  local actual
  actual=$(ps -p "$pid" -o comm= 2>/dev/null | xargs basename 2>/dev/null || true)
  if [[ -z "$actual" ]]; then
    return 0  # can't determine; alive is sufficient
  fi
  [[ "$actual" == *"$expected"* ]]
}

read_ilk_pid() {
  local project="$1"
  local launcher_dir
  launcher_dir=$(get_ilk_launcher_dir "$project")
  if [[ -z "$launcher_dir" ]]; then
    echo ""
    return
  fi
  local f="${launcher_dir}/running.pid"
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
    print(d.get('pid',''))
    print(d.get('run_id',''))
except Exception:
    pass
"
}

classify_action() {
  local label="$1"
  case "$label" in
    running)
      echo "sleep"
      ;;
    all-shipped|already-shipped|shipped)
      echo "promote"
      ;;
    clean-success)
      # Job done — no relaunch, no red banner; scheduler promotes next cycle.
      echo "stop-clean"
      ;;
    shipped-unverified)
      # All sub-plans shipped but some need manual verification.
      echo "needs-human"
      ;;
    self-hosting-drift)
      # Toolkit self-edit drift; human review required.
      echo "needs-human"
      ;;
    no-evidence)
      # Run started but left no usable records — triage.
      echo "triage"
      ;;
    timeout-bound|max-iter-bound|api-flaky|interrupted)
      # Whitelist: transient failures safe to retry.
      echo "relaunch"
      ;;
    stuck-no-progress|api-blocked|budget-exhausted|local-checks-stuck|local-checks-broken|dependency-unreachable|merge-conflict)
      # Blacklist: structural failures where a restart won't help.
      echo "block"
      ;;
    "")
      # No classification AND no sentinel state — fail-safe block.
      # The relaunch path is reached earlier via raw-state fallback at ~696;
      # this explicit case ensures empty can never silently fall through.
      echo "block"
      ;;
    *)
      # Unknown terminal label — fail-safe BLOCK (matching ps1 behaviour).
      echo "block"
      ;;
  esac
}

# Startup sentinel freshness gate — mirrors PS Get-StartupSentinelAction.
# Determines what to do with a terminal sentinel on watchdog startup.
#
# Args (positional):
#   $1 = state           (sentinel state string)
#   $2 = ended_epoch     (sentinel ended_at as unix epoch; 0 if absent/unparseable)
#   $3 = launch_epoch    (watchdog launch time as unix epoch)
#   $4 = loop_status_exit (exit code of loop_status.py; unused for non-success)
#   $5 = loop_alive      ("true" if a live loop process is detected)
#
# Echoes one of: stale-ignore | work-pending | advance | classify
startup_sentinel_action() {
  local state="$1" ended_epoch="$2" launch_epoch="$3" loop_status_exit="$4" loop_alive="$5"
  local success_states=("all-shipped" "already-shipped" "shipped")
  local is_success=false
  local s
  for s in "${success_states[@]}"; do
    [[ "$state" == "$s" ]] && { is_success=true; break; }
  done

  # Staleness: both epochs must be positive and ended < launch
  local is_stale=false
  if [[ "$ended_epoch" -gt 0 && "$launch_epoch" -gt 0 && "$ended_epoch" -lt "$launch_epoch" ]]; then
    is_stale=true
  fi

  if [[ "$is_success" == false ]]; then
    # Non-success terminal
    if [[ "$is_stale" == true && "$loop_alive" == true ]]; then
      echo "stale-ignore"
    else
      echo "classify"
    fi
  else
    # Success terminal
    if [[ "$is_stale" == true ]]; then
      echo "stale-ignore"
    elif [[ "$loop_status_exit" -eq 0 ]]; then
      echo "advance"
    else
      echo "work-pending"
    fi
  fi
}

# Run collect.py to classify a terminal run. Prints the classification label
# (e.g. "stuck-no-progress") or empty string on failure.
invoke_postmortem_collect() {
  local project="$1"
  local run_id="$2"

  if [[ ! -f "$COLLECT_PY" ]]; then
    write_log "collect.py not found at $COLLECT_PY"
    echo ""
    return
  fi

  local collect_args=("-ProjectPath" "$project" "--quiet")
  [[ -n "$run_id" ]] && collect_args+=("--run-id" "$run_id")

  local report_path
  report_path=$(python3 "$COLLECT_PY" "${collect_args[@]}" 2>/dev/null) || true
  if [[ -z "$report_path" || ! -f "$report_path" ]]; then
    write_log "collect.py produced no valid report path: '$report_path'"
    echo ""
    return
  fi

  # Parse classification from postmortem frontmatter
  local klass
  klass=$(python3 -c "
import sys
in_fm = False
for line in open('$report_path', encoding='utf-8'):
    line = line.rstrip()
    if line.strip() == '---':
        if in_fm:
            break
        in_fm = True
        continue
    if in_fm and line.startswith('classification:'):
        print(line.split(':', 1)[1].strip().strip('\"'))
        break
" 2>/dev/null) || true

  echo "$klass"
}

# ----- Promotion helper ------------------------------------------------------

handle_promote() {
  local project="$1"
  local proj_name="$2"
  local poll_sec="$3"

  local script_path="${_SKILL_ROOT}/ilk-loop/scripts/promote_next_master.py"
  if [[ ! -f "$script_path" ]]; then
    write_log "promote_next_master.py not found at $script_path — cannot advance queue."
    return
  fi

  local json_out
  json_out=$(python3 "$script_path" --project "$project" 2>/dev/null) || true
  if [[ -z "$json_out" ]]; then
    write_log "promote_next_master.py produced no output."
    return
  fi

  local promoted
  promoted=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('promoted') or '')" <<<"$json_out")
  local demoted
  demoted=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('demoted') or '')" <<<"$json_out")
  local queue_remaining
  queue_remaining=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('queue_remaining',''))" <<<"$json_out")

  if [[ -n "$promoted" ]]; then
    write_log "queue advanced: demoted=$demoted, promoted=$promoted, queue_remaining=$queue_remaining"
    if [[ ! -f "$LAUNCH_SCRIPT" ]]; then
      write_banner "QUEUE ADVANCED — LAUNCHER MISSING" \
"Project: $proj_name
Promoted: $promoted
Expected launcher: $LAUNCH_SCRIPT

Cannot auto-relaunch. Run ilk-launcher manually." 33
      return
    fi
    if ! bash "$LAUNCH_SCRIPT" --project-path "$project" --force; then
      write_banner "QUEUE ADVANCED — RELAUNCH FAILED" \
"Project: $proj_name
Promoted: $promoted

Launch script exited non-zero. Watchdog blocking." 31
      return
    fi
    # Reset state so the next master starts fresh
    restart_count=0
    last_restart_class=""
    saw_alive_once=false
    write_log "next master launched: $promoted. Resuming polling."
    sleep "$poll_sec"
    return
  fi

  # No next master: queue drained
  local demoted_note=""
  if [[ -n "$demoted" ]]; then
    demoted_note="Marked $demoted as shipped."
  fi
  invoke_ilk_notify "queue-drained" "$proj_name"
  write_banner "ALL MASTERS SHIPPED — QUEUE DRAINED" \
"Project: $proj_name
State: clean ship
$demoted_note

Watchdog exiting cleanly. Job done." 32
  exit 0
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
  --detach                     Start watchdog in a detached screen session and exit.
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
      --detach)
        CLI_DETACH=true
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

# ----- Logging ---------------------------------------------------------------

WATCHDOG_STATE_DIR=""
ACTIVITY_LOG=""

# Uppercase a string. Portable across bash 3.2 (macOS /bin/bash), which has
# no `${var^^}` parameter expansion — that raises "bad substitution" and, in
# a banner path, crashes the watchdog before it can relaunch/block.
to_upper() {
  printf '%s' "$1" | tr '[:lower:]' '[:upper:]'
}

write_log() {
  local msg="$1"
  local ts
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  local line="[$ts] $msg"
  echo "$line"
  if [[ -n "$ACTIVITY_LOG" ]]; then
    echo "$line" >> "$ACTIVITY_LOG" 2>/dev/null || true
  fi
}

write_banner() {
  local title="$1"
  local body="${2:-}"
  local color="${3:-33}"  # default yellow
  local bar
  bar=$(printf '%78s' '' | tr ' ' '=')
  echo ""
  printf '\e[%sm%s\e[0m\n' "$color" "$bar"
  printf '\e[%sm %s\e[0m\n' "$color" "$title"
  printf '\e[%sm%s\e[0m\n' "$color" "$bar"
  if [[ -n "$body" ]]; then
    while IFS= read -r l; do
      printf '\e[%sm %s\e[0m\n' "$color" "$l"
    done <<<"$body"
  fi
  printf '\e[%sm%s\e[0m\n' "$color" "$bar"
  echo ""
}

# ----- Watchdog loop ---------------------------------------------------------

run_watchdog_loop() {
  local project="$1"
  local proj_name="$2"
  local poll_sec="$3"
  local max_restarts_cap="$4"

  local watchdog_dir
  watchdog_dir=$(get_ilk_watchdog_dir "$project")
  if [[ -z "$watchdog_dir" ]]; then
    write_log "ERROR: external watchdog dir not resolvable. Ensure ilk_paths.py is present or run the migration script."
    return
  fi
  WATCHDOG_STATE_DIR="$watchdog_dir"
  mkdir -p "$WATCHDOG_STATE_DIR"
  ACTIVITY_LOG="${WATCHDOG_STATE_DIR}/activity.log"
  watchdog_pid_file="${WATCHDOG_STATE_DIR}/watchdog.pid"

  # Refuse to double-run — but only when the recorded PID is a watchdog
  # still actively watching for this project. A lingering -NoExit host of a
  # finished watchdog (or any other process that grabbed the same PID) must
  # NOT block a fresh watchdog.
  if [[ -f "$watchdog_pid_file" ]]; then
    local existing_pid
    existing_pid=$(tr -d '[:space:]' < "$watchdog_pid_file")
    if [[ -n "$existing_pid" ]] && test_process_alive "$existing_pid" && \
       test_process_command_alive "$existing_pid" "watchdog"; then
      write_banner "WATCHDOG ALREADY RUNNING" \
        "Project: $proj_name\nExisting watchdog PID: $existing_pid\nRefusing to start a second one." 31
      return
    else
      rm -f "$watchdog_pid_file"
    fi
  fi

  echo "$$" > "$watchdog_pid_file"

  write_banner "ilk-watchdog started" \
"Project: $proj_name
ProjectPath: $project
PollIntervalSec: $poll_sec
MaxRestarts: $max_restarts_cap
Activity log: $ACTIVITY_LOG
Watchdog PID: $$" 36

  local restart_count=0
  local last_relaunch_at=""
  local saw_alive_once=false
  local last_restart_class=""
  local runtime_dir
  runtime_dir=$(get_ilk_runtime_dir "$project")
  if [[ -n "$runtime_dir" ]]; then
    write_log "sentinel runtime dir: $runtime_dir"
  else
    write_log "sentinel runtime dir not resolvable; falling back to PID-only mode"
  fi

  local success_states=("all-shipped" "already-shipped" "shipped")

  # Cleanup PID file on exit
  cleanup() {
    rm -f "$watchdog_pid_file"
    write_log "watchdog exiting."
  }
  trap cleanup EXIT

  while true; do
    local sentinel_state=""
    local sentinel_iters=""
    local sentinel_last_iter=""
    local sentinel_pid=""
    local sentinel_terminal=false

    # ---------- Sentinel fast-path -----------------------------------
    local sentinel_raw
    sentinel_raw=$(read_last_exit_state "$runtime_dir")
    local sentinel_run_id=""
    if [[ -n "$sentinel_raw" ]]; then
      sentinel_state=$(echo "$sentinel_raw" | sed -n '1p')
      sentinel_iters=$(echo "$sentinel_raw" | sed -n '2p')
      sentinel_last_iter=$(echo "$sentinel_raw" | sed -n '3p')
      sentinel_pid=$(echo "$sentinel_raw" | sed -n '4p')
      sentinel_run_id=$(echo "$sentinel_raw" | sed -n '5p')
    fi

    if [[ -n "$sentinel_state" ]]; then
      if [[ "$sentinel_state" == "running" ]]; then
        local loop_pid=""
        # Prefer PID file; fall back to sentinel's own pid field.
        loop_pid=$(read_ilk_pid "$project")
        if [[ -z "$loop_pid" && -n "$sentinel_pid" ]]; then
          loop_pid="$sentinel_pid"
        fi
        if [[ -n "$loop_pid" ]] && ! test_process_alive "$loop_pid"; then
          write_log "sentinel says running but pid $loop_pid is dead — treating as stale-running."
          sentinel_terminal=true
        else
          if [[ "$saw_alive_once" == false ]]; then
            saw_alive_once=true
            if [[ -n "$loop_pid" ]]; then
              write_log "ilk loop pid=$loop_pid state=running (via sentinel) — watching."
            else
              write_log "ilk loop state=running (via sentinel) — watching."
            fi
          fi
          # --- Hung-alive guard (loop_health.hung_alive contract) ---
          # state=running but NO progress for a long time = a wedged loop (e.g. a
          # pre-iter-1 hang). Progress = the JSONL summary mtime (advances per
          # iteration); fall back to last-exit.json mtime when the JSONL is absent.
          # Progress = MOST RECENT of the JSONL summary mtime (advances per
          # iteration) and the sentinel file mtime (written at run start). Taking
          # the max means a freshly-started run (sentinel just written, JSONL
          # still from the PREVIOUS run) is NOT mistaken for hung.
          local thr="${ILK_HUNG_THRESHOLD_MIN:-45}"
          local jsonl="$(dirname "$runtime_dir")/logs/.ilk-loop.log"
          local sentinel_file="${runtime_dir}/last-exit.json"
          local mtime=0 m f
          for f in "$jsonl" "$sentinel_file"; do
            if [[ -f "$f" ]]; then
              m=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)
              [[ "$m" -gt "$mtime" ]] && mtime="$m"
            fi
          done
          if [[ "$mtime" -gt 0 ]]; then
            local now_epoch
            now_epoch=$(date +%s)
            if (( now_epoch - mtime >= thr * 60 )); then
              local mins=$(( (now_epoch - mtime) / 60 ))
              write_banner "BLOCKED — HUNG-ALIVE" \
                "Project: $proj_name\nstate=running but NO progress for ${mins} min (threshold ${thr}).\nThe loop is wedged (e.g. a pre-iter-1 hang). Restart will not help —\ninspect the runner; fix the cause; relaunch with ilk-launcher." 31
              invoke_ilk_notify "blocked" "$proj_name" "hung-alive ${mins}m no progress"
              write_log "hung-alive: state=running, no progress for ${mins} min (threshold ${thr}) — BLOCKING."
              return
            fi
          fi
          sleep "$poll_sec"
          continue
        fi
      elif [[ " ${success_states[*]} " =~ [[:space:]]${sentinel_state}[[:space:]] ]]; then
        write_log "clean ship detected (state=$sentinel_state, iters=$sentinel_iters). Advancing master queue..."
        invoke_ilk_notify "ship" "$proj_name"
        handle_promote "$project" "$proj_name" "$poll_sec"
        continue
      else
        write_log "sentinel terminal state: $sentinel_state (iters=$sentinel_iters) — classifying."
        sentinel_terminal=true
      fi
    fi

    # ---------- Legacy PID path (no sentinel) ------------------------
    if [[ "$sentinel_terminal" == false ]]; then
      local ilk_pid
      ilk_pid=$(read_ilk_pid "$project")

      if [[ -z "$ilk_pid" ]]; then
        if [[ "$saw_alive_once" == false ]]; then
          write_log "no ilk PID file at start. Sleeping; will exit if not seen within 10 min."
          sleep "$poll_sec"
          ilk_pid=$(read_ilk_pid "$project")
          if [[ -z "$ilk_pid" ]]; then
            write_banner "NO ilk PID FILE" \
              "Project: $proj_name\nNo launcher PID file found.\nIs ilk running? Start ilk first, then start watchdog." 31
            return
          fi
        else
          write_banner "ilk PID FILE GONE" \
            "Project: $proj_name\nThe PID file was removed (probably stop.sh ran).\nWatchdog exiting — assume manual stop." 33
          return
        fi
      fi

      if test_process_alive "$ilk_pid"; then
        if [[ "$saw_alive_once" == false ]]; then
          saw_alive_once=true
          write_log "ilk PID $ilk_pid alive — watching."
        fi
        sleep "$poll_sec"
        continue
      fi

      # PID file exists but process is dead
      write_log "ilk PID $ilk_pid is dead. Investigating..."
    fi

    # ---------- Classification & action ------------------------------
    # When we have a terminal sentinel, run collect.py to get the
    # classification label (matching ps1 behaviour). Fall back to the
    # raw sentinel state only if collect.py produced no classification.
    local classification=""
    if [[ "$sentinel_terminal" == true ]]; then
      write_log "running collect.py to classify the run..."
      classification=$(invoke_postmortem_collect "$project" "$sentinel_run_id")
      if [[ -n "$classification" ]]; then
        write_log "classification: $classification"
      else
        write_log "collect.py produced no classification; falling back to raw sentinel state: $sentinel_state"
        classification="$sentinel_state"
      fi
    else
      # Legacy PID path: no sentinel, use raw state
      classification="$sentinel_state"
    fi

    local action
    action=$(classify_action "$classification")

    if [[ "$action" == "sleep" ]]; then
      sleep "$poll_sec"
      continue
    fi

    if [[ "$action" == "promote" ]]; then
      handle_promote "$project" "$proj_name" "$poll_sec"
      continue
    fi

    if [[ "$action" == "stop-clean" ]]; then
      write_log "clean-success: job done. No relaunch, no red banner."
      invoke_ilk_notify "ship" "$proj_name" "classification: $classification"
      write_banner "DONE — $(to_upper "$classification")" \
"Project: $proj_name
Classification: $classification

Job done. Watchdog exiting cleanly. The scheduler will promote the
next queued master on its next cycle (if any)." 32
      return
    fi

    if [[ "$action" == "needs-human" ]]; then
      local ev="needs-human"
      [[ "$classification" == "shipped-unverified" ]] && ev="needs-verification"
      write_log "$classification: needs human review. No relaunch."
      invoke_ilk_notify "$ev" "$proj_name" "classification: $classification"
      write_banner "NEEDS HUMAN — $(to_upper "$classification")" \
"Project: $proj_name
Classification: $classification

This outcome requires human review — no auto-relaunch.
Read the postmortem for details." 33
      return
    fi

    if [[ "$action" == "triage" ]]; then
      write_log "$classification: triage required. No relaunch."
      invoke_ilk_notify "triage" "$proj_name" "classification: $classification"
      write_banner "TRIAGE — $(to_upper "$classification")" \
"Project: $proj_name
Classification: $classification

This run needs manual triage — no auto-relaunch.
Check runner logs and sentinel state." 33
      return
    fi

    if [[ "$action" == "block" ]]; then
      invoke_ilk_notify "blocked" "$proj_name" "classification: $classification"
      write_banner "BLOCKED — $(to_upper "$classification")" \
"Project: $proj_name
Classification: $classification

Restart will not help this kind of stop. Human triage required.
Read the report tail and decide what to do, then relaunch ilk manually." 31
      return
    fi

    # action == relaunch
    if [[ "$last_restart_class" != "$classification" ]]; then
      restart_count=1
      last_restart_class="$classification"
    else
      restart_count=$((restart_count + 1))
    fi

    if [[ $restart_count -gt $max_restarts_cap ]]; then
      write_banner "MAX RESTARTS REACHED ($max_restarts_cap)" \
"Project: $proj_name
Last classification: $classification
Hard cap is in place to force human review when restarts pile up.
Inspect postmortems under the external launcher dir to see the trend, then
relaunch manually if it still makes sense." 31
      return
    fi

    # Exponential backoff: 60, 120, 240, 480, 960 ...
    local backoff=60
    local i
    for ((i=2; i<=restart_count; i++)); do
      backoff=$((backoff * 2))
      if [[ $backoff -gt 960 ]]; then
        backoff=960
        break
      fi
    done

    if [[ $backoff -gt $poll_sec ]]; then
      write_log "backoff: sleeping ${backoff}s before relaunch (consecutive $classification restarts: $restart_count/$max_restarts_cap)."
      sleep "$backoff"
    fi

    if [[ ! -f "$LAUNCH_SCRIPT" ]]; then
      write_banner "LAUNCH SCRIPT MISSING" \
        "Expected: $LAUNCH_SCRIPT\nWatchdog cannot relaunch." 31
      return
    fi

    write_log "WHITELIST hit ($classification). Restart $restart_count/$max_restarts_cap."
    invoke_ilk_notify "restart" "$proj_name" "classification: $classification"

    if ! bash "$LAUNCH_SCRIPT" --project-path "$project" --force; then
      write_banner "RELAUNCH FAILED" \
        "Project: $proj_name\nLaunch script exited non-zero.\nWatchdog blocking." 31
      return
    fi

    last_relaunch_at=$(date +%s)
    saw_alive_once=false
    write_log "relaunch issued. Resuming polling."
    sleep "$poll_sec"
  done
}

# ----- Detach helper ----------------------------------------------------------

detach_watchdog() {
  local project="$1"
  local proj_name="$2"

  if ! command -v screen &>/dev/null; then
    echo "ERROR: 'screen' is not installed. Install it (brew install screen) or run without --detach." >&2
    exit 1
  fi

  # Build the foreground command (re-invoke without --detach)
  local self="${BASH_SOURCE[0]}"
  local cmd="bash '$self' --project-path '$project'"
  if [[ -n "$CLI_POLL_INTERVAL_SEC" ]]; then
    cmd="$cmd --poll-interval-sec '$CLI_POLL_INTERVAL_SEC'"
  fi
  if [[ -n "$CLI_MAX_RESTARTS" ]]; then
    cmd="$cmd --max-restarts '$CLI_MAX_RESTARTS'"
  fi

  local session_name="ilk-watchdog-$(basename "$project")"

  # Resolve watchdog PID file path so we can wait for it
  local watchdog_dir
  watchdog_dir=$(get_ilk_watchdog_dir "$project")
  if [[ -z "$watchdog_dir" ]]; then
    echo "ERROR: cannot resolve external watchdog dir for $project." >&2
    exit 1
  fi
  mkdir -p "$watchdog_dir"
  local pid_file="${watchdog_dir}/watchdog.pid"
  local log_file="${watchdog_dir}/watchdog.log"

  # Remove stale PID file if the process is dead
  if [[ -f "$pid_file" ]]; then
    local existing_pid
    existing_pid=$(tr -d '[:space:]' < "$pid_file")
    if [[ -n "$existing_pid" ]] && ! test_process_alive "$existing_pid"; then
      rm -f "$pid_file"
    fi
  fi

  # Launch in screen
  screen -dmS "$session_name" bash -c "$cmd >> '$log_file' 2>&1"

  # Wait up to 10s for PID file to appear
  local waited=0
  while [[ $waited -lt 10 ]]; do
    if [[ -f "$pid_file" ]]; then
      local new_pid
      new_pid=$(tr -d '[:space:]' < "$pid_file")
      if [[ -n "$new_pid" ]] && test_process_alive "$new_pid"; then
        echo "Watchdog detached. screen session: $session_name"
        echo "PID: $new_pid"
        echo "Log: $log_file"
        echo "Activity log: ${watchdog_dir}/activity.log"
        exit 0
      fi
    fi
    sleep 1
    waited=$((waited + 1))
  done

  echo "ERROR: watchdog PID file not found after 10s. Check screen session: screen -r $session_name" >&2
  exit 1
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

  # Detach mode: launch in screen and exit
  if [[ "$CLI_DETACH" == true ]]; then
    detach_watchdog "$RESOLVED_PATH" "$RESOLVED_NAME"
    return
  fi

  run_watchdog_loop "$RESOLVED_PATH" "$RESOLVED_NAME" "$POLL_INTERVAL_SEC" "$MAX_RESTARTS"
}

main "$@"
