#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-watchdog (macOS bash port of watchdog.ps1)
# =============================================================================
# Polls ~/.ilk-data/projects/<key>/runtime/last-exit.json (preferred) and
# falls back to <project>/.ilk-launcher/running.pid every --poll-interval-sec.
# When the loop stops, classifies the run and either relaunches, promotes
# the next master, or blocks with a loud banner.
# =============================================================================

# ----- Defaults & globals ----------------------------------------------------

LAUNCHER_DIR="${HOME}/.cursor/skills/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"
LAUNCH_SCRIPT="${LAUNCHER_DIR}/scripts/launch.sh"
LOOP_STATUS_PY="${HOME}/.cursor/skills/ilk-loop/scripts/loop_status.py"
COLLECT_PY="${HOME}/.cursor/skills/ilk-feedback/scripts/collect.py"

POLL_INTERVAL_SEC=60
MAX_RESTARTS=5

# CLI overrides
CLI_PROJECT_PATH=""
CLI_PROJECT_NAME=""
CLI_POLL_INTERVAL_SEC=""
CLI_MAX_RESTARTS=""

# Resolved values
RESOLVED_PATH=""
RESOLVED_NAME=""

# ----- Helpers (project resolution, same pattern as launch.sh) ---------------

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
  resolver="${HOME}/.cursor/skills/ilk-loop/scripts/ilk_paths.py"
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

# ----- Sentinel / PID helpers ------------------------------------------------

test_process_alive() {
  local pid="$1"
  if [[ -z "$pid" || "$pid" -le 0 ]]; then
    return 1
  fi
  kill -0 "$pid" 2>/dev/null
}

read_ilk_pid() {
  local project="$1"
  local f="${project}/.ilk-launcher/running.pid"
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
except Exception:
    pass
"
}

classify_action() {
  local state="$1"
  case "$state" in
    running)
      echo "sleep"
      ;;
    all-shipped|already-shipped|shipped)
      echo "promote"
      ;;
    no-progress|timeout|budget-exhausted)
      echo "relaunch"
      ;;
    merge-conflict|local-checks-stuck)
      echo "blacklist"
      ;;
    *)
      # missing file / unknown state -> sleep (poll again)
      echo "sleep"
      ;;
  esac
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

  WATCHDOG_STATE_DIR="${project}/.ilk-watchdog"
  mkdir -p "$WATCHDOG_STATE_DIR"
  ACTIVITY_LOG="${WATCHDOG_STATE_DIR}/activity.log"
  local watchdog_pid_file="${WATCHDOG_STATE_DIR}/watchdog.pid"

  # Refuse to double-run
  if [[ -f "$watchdog_pid_file" ]]; then
    local existing_pid
    existing_pid=$(tr -d '[:space:]' < "$watchdog_pid_file")
    if [[ -n "$existing_pid" ]] && test_process_alive "$existing_pid"; then
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
    local sentinel_terminal=false

    # ---------- Sentinel fast-path -----------------------------------
    local sentinel_raw
    sentinel_raw=$(read_last_exit_state "$runtime_dir")
    if [[ -n "$sentinel_raw" ]]; then
      sentinel_state=$(echo "$sentinel_raw" | sed -n '1p')
      sentinel_iters=$(echo "$sentinel_raw" | sed -n '2p')
      sentinel_last_iter=$(echo "$sentinel_raw" | sed -n '3p')
    fi

    if [[ -n "$sentinel_state" ]]; then
      if [[ "$sentinel_state" == "running" ]]; then
        local loop_pid=""
        # third line of sentinel_raw is last_iter_at; pid is not stored there.
        # The PS1 checks sentinel.pid; our read_last_exit_state doesn't return pid.
        # For now, trust the running state and check the PID file.
        loop_pid=$(read_ilk_pid "$project")
        if [[ -n "$loop_pid" ]] && ! test_process_alive "$loop_pid"; then
          write_log "sentinel says running but pid $loop_pid is dead — treating as crash."
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
          sleep "$poll_sec"
          continue
        fi
      elif [[ " ${success_states[*]} " =~ [[:space:]]${sentinel_state}[[:space:]] ]]; then
        # Promote action — handled in step 4; for now log and sleep
        write_log "clean ship detected (state=$sentinel_state, iters=$sentinel_iters). Queue advance pending step 4."
        sleep "$poll_sec"
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
              "Project: $proj_name\nNo <project>/.ilk-launcher/running.pid found.\nIs ilk running? Start ilk first, then start watchdog." 31
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
    local action
    action=$(classify_action "$sentinel_state")

    if [[ "$action" == "sleep" ]]; then
      sleep "$poll_sec"
      continue
    fi

    if [[ "$action" == "promote" ]]; then
      # Placeholder — step 4 adds promote_next_master.py integration
      write_log "promote action: queue advancement coming in step 4."
      sleep "$poll_sec"
      continue
    fi

    if [[ "$action" == "blacklist" ]]; then
      write_banner "BLOCKED — ${sentinel_state^^}" \
"Project: $proj_name
Classification: $sentinel_state

Restart will not help this kind of stop. Human triage required.
Read the report tail and decide what to do, then relaunch ilk manually." 31
      return
    fi

    # action == relaunch
    if [[ "$last_restart_class" != "$sentinel_state" ]]; then
      restart_count=1
      last_restart_class="$sentinel_state"
    else
      restart_count=$((restart_count + 1))
    fi

    if [[ $restart_count -gt $max_restarts_cap ]]; then
      write_banner "MAX RESTARTS REACHED ($max_restarts_cap)" \
"Project: $proj_name
Last classification: $sentinel_state
Hard cap is in place to force human review when restarts pile up.
Inspect <project>/.ilk-launcher/postmortems/ to see the trend, then
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
      write_log "backoff: sleeping ${backoff}s before relaunch (consecutive $sentinel_state restarts: $restart_count/$max_restarts_cap)."
      sleep "$backoff"
    fi

    if [[ ! -f "$LAUNCH_SCRIPT" ]]; then
      write_banner "LAUNCH SCRIPT MISSING" \
        "Expected: $LAUNCH_SCRIPT\nWatchdog cannot relaunch." 31
      return
    fi

    write_log "WHITELIST hit ($sentinel_state). Restart $restart_count/$max_restarts_cap."

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

  run_watchdog_loop "$RESOLVED_PATH" "$RESOLVED_NAME" "$POLL_INTERVAL_SEC" "$MAX_RESTARTS"
}

main "$@"
