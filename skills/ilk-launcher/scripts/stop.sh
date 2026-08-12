#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-launcher stop (macOS bash port of stop.ps1)
# =============================================================================
# Reads the PID file from the external launcher dir (resolved via
# ilk_paths.py), kills the process group, and removes the PID file.
# =============================================================================

# ----- Skill root resolution -------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# Source _ilk_pid.sh for ilk_project_runners (used in post-kill verification)
source "${_SKILL_ROOT}/ilk-loop/scripts/_ilk_pid.sh"

# ----- Globals ---------------------------------------------------------------

LAUNCHER_DIR="${_SKILL_ROOT}/ilk-launcher"
PROJECTS_JSON="${LAUNCHER_DIR}/projects.json"

CLI_PROJECT_PATH=""
CLI_PROJECT_NAME=""
CLI_ALL=false
CLI_RESET=false

# ----- Helpers (mirrored from launch.sh) -------------------------------------

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
    echo "Project '$name' not in projects.json." >&2
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

mark_sentinel_interrupted() {
  local project_path="$1"
  local stopped_pid="$2"
  local launcher_dir
  launcher_dir=$(get_external_launcher_dir "$project_path")
  if [[ -z "$launcher_dir" ]]; then
    return
  fi
  local runtime_dir
  runtime_dir="$(dirname "$launcher_dir")"
  local marker="${_SKILL_ROOT}/ilk-launcher/scripts/mark_sentinel_interrupted.sh"
  if [[ -f "$marker" ]]; then
    bash "$marker" "$runtime_dir" "$stopped_pid" 2>/dev/null || true
  fi
}

# ----- Orphan scan -----------------------------------------------------------

kill_orphans() {
  local project_path="$1"
  local stopped_pid="$2"
  local launcher_dir="$3"
  local name="$4"

  # Read last-launch.json to get the log file path (contains run ID)
  local last_launch="${launcher_dir}/last-launch.json"
  local run_id=""
  if [[ -f "$last_launch" ]]; then
    run_id=$(python3 -c "
import json, re, sys
with open('$last_launch') as f:
    d = json.load(f)
logf = d.get('log_file', '')
m = re.search(r'(\d{8}-\d{6})', logf)
if m: print(m.group(1))
" 2>/dev/null) || run_id=""
  fi

  if [[ -z "$run_id" ]]; then
    echo "[$name] orphan scan: no run ID in last-launch.json — skipping." >&2
    return 0
  fi

  # Find candidate processes whose command line matches the run ID or
  # project path.  Exclude this shell, its parent, its process group,
  # and the stopped PID itself — the scan must never kill stop.sh or
  # its wrapper (AC-1).  Exclusion by identity, not by narrowing the
  # match pattern, so genuine orphans are still found (AC-2).
  local my_pid=$$
  local my_pgid=""
  my_pgid=$(ps -o pgid= -p "$$" 2>/dev/null | tr -d '[:space:]') || true
  local found=0
  while IFS= read -r line; do
    local cpid
    cpid=$(echo "$line" | awk '{print $1}')
    [[ -z "$cpid" ]] && continue
    [[ "$cpid" == "$stopped_pid" ]] && continue
    [[ "$cpid" == "$my_pid" ]] && continue
    [[ "$cpid" == "$PPID" ]] && continue

    # Skip processes in stop.sh's own process group (this shell and
    # any wrapper that spawned it).
    if [[ -n "$my_pgid" ]]; then
      local cpgid=""
      cpgid=$(ps -o pgid= -p "$cpid" 2>/dev/null | tr -d '[:space:]') || true
      [[ -n "$cpgid" && "$cpgid" == "$my_pgid" ]] && continue
    fi

    # Skip if this process is stop.sh itself (command contains stop.sh
    # or the kill_orphans function name).
    local cmd
    cmd=$(echo "$line" | sed 's/^[[:space:]]*[0-9]*[[:space:]]*//')
    case "$cmd" in
      *stop.sh*|*kill_orphans*) continue ;;
    esac

    echo "[$name] orphan scan: killing PID $cpid — ${cmd:0:120}" >&2
    kill "$cpid" 2>/dev/null || true
    found=$((found + 1))
  done < <(ps -ax -o pid=,command= 2>/dev/null | grep -E "$run_id|$project_path" | grep -v " grep " || true)

  if [[ "$found" -eq 0 ]]; then
    echo "[$name] orphan scan: no orphaned workers found." >&2
  else
    echo "[$name] orphan scan: terminated $found worker process(es)." >&2
  fi
}

# ----- Reset mode ------------------------------------------------------------

reset_worker_changes() {
  local path="$1"
  local name="$2"

  echo "[$name] --- reset preview (dry run) ---" >&2

  # Show tracked changes that would be restored
  local tracked
  tracked=$(cd "$path" && git diff --name-only 2>/dev/null) || tracked=""
  if [[ -n "$tracked" ]]; then
    echo "[$name] tracked files to restore:" >&2
    echo "$tracked" | while IFS= read -r f; do
      echo "[$name]   git restore: $f" >&2
    done
  else
    echo "[$name]   (no tracked changes)" >&2
  fi

  # Show untracked files that would be removed
  local untracked
  untracked=$(cd "$path" && git ls-files --others --exclude-standard 2>/dev/null) || untracked=""
  if [[ -n "$untracked" ]]; then
    echo "[$name] untracked files to remove:" >&2
    echo "$untracked" | while IFS= read -r f; do
      echo "[$name]   rm: $f" >&2
    done
  else
    echo "[$name]   (no untracked files)" >&2
  fi

  if [[ -z "$tracked" && -z "$untracked" ]]; then
    echo "[$name] nothing to reset." >&2
    return 0
  fi

  echo "[$name] --- applying reset ---" >&2

  if [[ -n "$tracked" ]]; then
    (cd "$path" && git restore . 2>&1) | while IFS= read -r line; do
      echo "[$name]   $line" >&2
    done
    echo "[$name] tracked files restored." >&2
  fi

  if [[ -n "$untracked" ]]; then
    (cd "$path" && git clean -fd 2>&1) | while IFS= read -r line; do
      echo "[$name]   $line" >&2
    done
    echo "[$name] untracked files removed." >&2
  fi
}

# ----- Watchdog integration --------------------------------------------------

stop_watchdog_for_project() {
  local path="$1"
  local name="$2"
  local watchdog_stop="${_SKILL_ROOT}/ilk-watchdog/scripts/stop_watchdog.sh"
  if [[ ! -f "$watchdog_stop" ]]; then
    return 0
  fi
  bash "$watchdog_stop" --project-path "$path" 2>&1 | sed "s/^/[$name] /" || true
}

# ----- Stop logic ------------------------------------------------------------

stop_project() {
  local path="$1"
  local name="$2"
  local pid_file

  # Stop watchdog first — it must not try to restart the loop after we kill it.
  stop_watchdog_for_project "$path" "$name"

  pid_file=$(get_pid_file_path "$path")

  if [[ ! -f "$pid_file" ]]; then
    echo "[$name] no running ilk for this project" >&2
    return 0
  fi

  local target_pid
  target_pid=$(cat "$pid_file" | tr -d '[:space:]')
  if [[ -z "$target_pid" ]]; then
    echo "[$name] PID file empty. Cleaning up." >&2
    rm -f "$pid_file"
    return 0
  fi

  if ! kill -0 "$target_pid" 2>/dev/null; then
    echo "[$name] PID $target_pid no longer alive. Cleaning stale PID file." >&2
    mark_sentinel_interrupted "$path" "$target_pid"
    rm -f "$pid_file"
    return 0
  fi

  echo "[$name] killing process group $target_pid..." >&2
  # Try the process group first (kills the whole tree if the launcher
  # spawned it as a pgrp leader), then the PID directly (recovers
  # orphans from older launches that weren't pgrp leaders).
  kill -- -"$target_pid" 2>/dev/null || true
  kill -- "$target_pid" 2>/dev/null || true

  # Wait up to 5s for the process group to exit
  local waited=0
  while kill -0 "$target_pid" 2>/dev/null && [[ "$waited" -lt 5 ]]; do
    sleep 1
    waited=$((waited + 1))
  done

  if kill -0 "$target_pid" 2>/dev/null; then
    echo "[$name] PID $target_pid still alive after SIGTERM. Sending SIGKILL..." >&2
    kill -9 -- -"$target_pid" 2>/dev/null || true
    kill -9 -- "$target_pid" 2>/dev/null || true
    sleep 1
  fi

  if kill -0 "$target_pid" 2>/dev/null; then
    echo "[$name] PID $target_pid still alive after SIGKILL. Investigate manually." >&2
  else
    echo "[$name] stopped." >&2
    mark_sentinel_interrupted "$path" "$target_pid"
    rm -f "$pid_file"
  fi

  # Scan for orphaned worker processes (claude, gtimeout, tee, renderer)
  local launcher_dir
  launcher_dir=$(get_external_launcher_dir "$path")
  if [[ -n "$launcher_dir" ]]; then
    kill_orphans "$path" "$target_pid" "$launcher_dir" "$name"
  fi

  # Verify the tree is actually gone before reporting success (AC-2 / AC-3).
  # Two independent checks: ilk_project_runners (process table) and lsof on
  # run.lock (file-descriptor holders).  Both must be empty.
  local survivors=""
  local runner_pids
  runner_pids=$(ilk_project_runners "$path" 2>/dev/null) || true
  if [[ -n "$runner_pids" ]]; then
    survivors="runner processes: $runner_pids"
  fi

  local run_lock="${launcher_dir}/run.lock"
  if [[ -f "$run_lock" ]]; then
    local lock_holders
    lock_holders=$(lsof -t "$run_lock" 2>/dev/null) || true
    if [[ -n "$lock_holders" ]]; then
      survivors="${survivors:+$survivors, }run.lock holders: $lock_holders"
    fi
  fi

  if [[ -n "$survivors" ]]; then
    echo "[$name] FAILED — survivors detected after stop: $survivors" >&2
    # Print each survivor's command line for diagnosis
    for spid in $runner_pids $lock_holders; do
      local scmd
      scmd=$(ps -p "$spid" -o command= 2>/dev/null) || scmd="(unknown)"
      echo "[$name]   PID $spid: ${scmd:0:120}" >&2
    done
    return 1
  fi

  # Report dirty tree state (read-only, does not mutate)
  echo "[$name] repo state:" >&2
  local dirty
  dirty=$(cd "$path" && git status --short 2>/dev/null) || dirty=""
  if [[ -z "$dirty" ]]; then
    echo "[$name]   (clean)" >&2
  else
    echo "$dirty" | while IFS= read -r dline; do
      echo "[$name]   $dline" >&2
    done
  fi

  # Optional: reset worker changes if explicitly requested
  if [[ "$CLI_RESET" == true ]]; then
    reset_worker_changes "$path" "$name"
  fi
}

# ----- Argument parsing ------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: stop.sh [OPTIONS]

Stop a running ilk-launcher process for a project.

IMPORTANT: If the project's master is still active/queued and a supervised
scheduler is running, de-queue the master first (set master status to draft
or paused), THEN stop. Otherwise the scheduler will re-dispatch behind you.

Options:
  --project-path PATH    Absolute path to project root.
  --project-name NAME    Look up path in projects.json.
  --all                  Stop every project in projects.json.
  --reset-worker-changes Preview and reset tracked/untracked worker artifacts.
                         Requires explicit opt-in. Logs and postmortems are
                         preserved.
  -h, --help             Show this help and exit.
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
      --reset-worker-changes)
        CLI_RESET=true
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
      echo "projects.json not found." >&2
      exit 1
    fi
    local projects_json
    projects_json=$(read_projects_registry)
    local count
    count=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d))" <<<"$projects_json")
    if [[ "$count" -eq 0 ]]; then
      echo "projects.json has no projects." >&2
      exit 1
    fi
    python3 -c "
import json, sys
d = json.load(sys.stdin)
for p in d:
    print(p.get('path','') + '\t' + p.get('name',''))
" <<<"$projects_json" | while IFS=$'\t' read -r ppath pname; do
      stop_project "$ppath" "$pname"
    done
    return 0
  fi

  local resolved_path resolved_name
  if [[ -n "$CLI_PROJECT_PATH" ]]; then
    resolved_path="$CLI_PROJECT_PATH"
  elif [[ -n "$CLI_PROJECT_NAME" ]]; then
    resolved_path=$(resolve_project_by_name "$CLI_PROJECT_NAME")
  else
    resolved_path=$(resolve_project_by_cwd)
  fi

  if [[ ! -d "$resolved_path" ]]; then
    echo "ProjectPath '$resolved_path' does not exist." >&2
    exit 1
  fi

  resolved_path="$(cd "$resolved_path" && pwd)"

  if [[ -n "$CLI_PROJECT_NAME" ]]; then
    resolved_name="$CLI_PROJECT_NAME"
  else
    resolved_name=$(basename "$resolved_path")
  fi

  stop_project "$resolved_path" "$resolved_name"
}

main "$@"
