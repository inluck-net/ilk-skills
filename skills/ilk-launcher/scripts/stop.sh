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

  # Read last-launch.json to get the log file path (contains the per-run token)
  local last_launch="${launcher_dir}/last-launch.json"
  local run_token=""
  if [[ -f "$last_launch" ]]; then
    # Extract the per-run token <key>-<run_id> from the log file basename.
    # The log path is <dir>/<key>-<run_id>.log — strip the directory and
    # extension to get the token.  This is the ONLY string we grep for;
    # a bare run_id or project_path is too broad (AC-1).
    run_token=$(python3 -c "
import json, os, re, sys
with open('$last_launch') as f:
    d = json.load(f)
logf = d.get('log_file', '')
base = os.path.basename(logf)
# Strip extension
base = re.sub(r'\.[^.]+$', '', base)
# Must contain a timestamp to be a valid token
if re.search(r'\d{8}-\d{6}', base):
    print(base)
" 2>/dev/null) || run_token=""
  fi

  if [[ -z "$run_token" ]]; then
    echo "[$name] orphan scan: no per-run token in last-launch.json — skipping." >&2
    return 0
  fi

  # Collect the kill set BEFORE killing anything:
  #   1. Descendants of the stopped PID (process tree walk)
  #   2. ilk_project_runners for this project (role-specific match)
  #   3. Orphans whose argv carries the per-run token
  # Identity exclusions: this shell, its parent, its process group,
  # and the stopped PID itself — the scan must never kill stop.sh or
  # its wrapper (AC-1).
  local my_pid=$$
  local my_pgid=""
  my_pgid=$(ps -o pgid= -p "$$" 2>/dev/null | tr -d '[:space:]') || true

  # Use a temp file for the kill set (bash 3.x has no associative arrays)
  local kill_set_file
  kill_set_file=$(mktemp) || kill_set_file="/tmp/stop-kill-set-$$"
  : > "$kill_set_file"

  # --- 1. Walk descendants of stopped_pid via ppid chain ---
  if [[ -n "$stopped_pid" ]]; then
    # Multi-pass: add direct children, then children of those, etc.
    # (bash 3.x can't do recursive functions with associative arrays)
    local prev_round="$stopped_pid"
    for _round in 1 2 3 4 5; do
      local next_round=""
      while IFS= read -r line; do
        local cpid cppid
        cpid=$(echo "$line" | awk '{print $1}')
        cppid=$(echo "$line" | awk '{print $2}')
        [[ -z "$cpid" || -z "$cppid" ]] && continue
        # Is the parent in the previous round?
        if echo "$prev_round" | grep -qw "$cppid"; then
          [[ "$cpid" == "$my_pid" || "$cpid" == "$PPID" ]] && continue
          if ! grep -qw "$cpid" "$kill_set_file" 2>/dev/null; then
            echo "$cpid" >> "$kill_set_file"
            next_round="${next_round:+$next_round }$cpid"
          fi
        fi
      done < <(ps -ax -o pid=,ppid= 2>/dev/null || true)
      [[ -z "$next_round" ]] && break
      prev_round="$next_round"
    done
  fi

  # --- 2. ilk_project_runners (role-specific: --project-path <path>) ---
  local runner_pids
  runner_pids=$(ilk_project_runners "$project_path" 2>/dev/null) || true
  if [[ -n "$runner_pids" ]]; then
    while IFS= read -r cpid; do
      [[ -z "$cpid" ]] && continue
      [[ "$cpid" == "$stopped_pid" || "$cpid" == "$my_pid" || "$cpid" == "$PPID" ]] && continue
      if ! grep -qw "$cpid" "$kill_set_file" 2>/dev/null; then
        echo "$cpid" >> "$kill_set_file"
      fi
    done <<< "$runner_pids"
  fi

  # --- 3. Orphans whose argv carries the per-run token ---
  while IFS= read -r line; do
    local cpid
    cpid=$(echo "$line" | awk '{print $1}')
    [[ -z "$cpid" ]] && continue
    [[ "$cpid" == "$stopped_pid" || "$cpid" == "$my_pid" || "$cpid" == "$PPID" ]] && continue

    # Skip processes in stop.sh's own process group
    if [[ -n "$my_pgid" ]]; then
      local cpgid=""
      cpgid=$(ps -o pgid= -p "$cpid" 2>/dev/null | tr -d '[:space:]') || true
      [[ -n "$cpgid" && "$cpgid" == "$my_pgid" ]] && continue
    fi

    # Skip stop.sh itself
    local cmd
    cmd=$(echo "$line" | sed 's/^[[:space:]]*[0-9]*[[:space:]]*//')
    case "$cmd" in
      *stop.sh*|*kill_orphans*) continue ;;
    esac

    if ! grep -qw "$cpid" "$kill_set_file" 2>/dev/null; then
      echo "$cpid" >> "$kill_set_file"
    fi
  done < <(ps -ax -o pid=,command= 2>/dev/null | grep -F "$run_token" | grep -v " grep " || true)

  # --- Kill the collected set ---
  local found=0
  while IFS= read -r cpid; do
    [[ -z "$cpid" ]] && continue
    # Skip already-dead processes
    kill -0 "$cpid" 2>/dev/null || continue
    local cmd
    cmd=$(ps -p "$cpid" -o command= 2>/dev/null) || cmd=""
    echo "[$name] orphan scan: killing PID $cpid — ${cmd:0:120}" >&2
    kill "$cpid" 2>/dev/null || true
    found=$((found + 1))
  done < "$kill_set_file"
  rm -f "$kill_set_file"

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
    return 1
  fi

  echo "[$name] stopped." >&2

  # Scan for orphaned worker processes BEFORE writing the sentinel.
  # The sentinel must not be written while descendants survive (#4 defect 2).
  local launcher_dir
  launcher_dir=$(get_external_launcher_dir "$path")
  if [[ -n "$launcher_dir" ]]; then
    kill_orphans "$path" "$target_pid" "$launcher_dir" "$name"
  fi

  # Verify the tree is actually gone before writing the sentinel (AC-2 / AC-3).
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

  # All processes verified gone — now safe to write the sentinel and
  # clean up the PID file.  Writing before verification would record
  # "interrupted" while descendants still live (#4 defect 2).
  mark_sentinel_interrupted "$path" "$target_pid"
  rm -f "$pid_file"

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
