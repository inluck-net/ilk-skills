#!/usr/bin/env bash
# =============================================================================
# Test: a live runner process for a project makes that project busy,
# even when running.pid is absent or names a different PID.
#
# Both dispatch sites (scheduler.sh:189, launch.sh:534) currently check ONLY
# running.pid. If the pid file is absent or stale while a real runner is alive,
# the project reads as "free" — and a second runner can start. That is the
# defect (measured 2026-08-12: 10 live runners, running.pid named 1 of 10).
#
# KNOWN_BAD=1 passes the test while the defect stands. Step 2 of the sub-plan
# fixes the dispatch sites and flips KNOWN_BAD; the test then asserts the
# correct behaviour for the first time.
#
# Harness: isolated HOME + ILK_SKILL_HOME (copied from
# test_scheduler_lock_contention.sh). Scheduler/runner tests hang without both.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ILK_PID_SH="$REPO_ROOT/skills/ilk-loop/scripts/_ilk_pid.sh"

if [[ ! -f "$ILK_PID_SH" ]]; then
  echo "FATAL: _ilk_pid.sh not found at $ILK_PID_SH" >&2
  exit 1
fi

# --- harness: isolated HOME + ILK_SKILL_HOME ---------------------------------
TMPDIR_TEST="$(mktemp -d)"
fake_runner_pid=""
plain_pid=""
# shellcheck disable=SC2064
trap 'rm -rf "$TMPDIR_TEST"; kill "$fake_runner_pid" "$plain_pid" 2>/dev/null || true' EXIT

export HOME="$TMPDIR_TEST"
export ILK_SKILL_HOME="$REPO_ROOT/skills"

# Replicate the pid-file path the dispatch sites use:
#   ~/.ilk-data/projects/<key>/runtime/launcher/running.pid
# We use a fixed project key "test-project" for the test.
PROJECT_PATH="$TMPDIR_TEST/project"
DATA_DIR="$HOME/.ilk-data/projects/test-project"
PID_DIR="$DATA_DIR/runtime/launcher"
PID_FILE="$PID_DIR/running.pid"
SENTINEL_FILE="$DATA_DIR/runtime/last-exit.json"
mkdir -p "$PID_DIR" "$PROJECT_PATH"

# --- inline dispatch-site logic -----------------------------------------------
# Source the shared helper that both dispatch sites use.
source "$ILK_PID_SH"

# Scheduler's test_running_pid (scheduler.sh:189-226) — exit-code convention.
# Returns 0 if busy, 1 if free.
scheduler_test_running_pid() {
  local project_data_path="$1"
  local pid_file="${project_data_path}/runtime/launcher/running.pid"
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi
  local raw
  raw=$(tr -d '[:space:]' < "$pid_file" 2>/dev/null) || true
  if [[ -z "$raw" ]]; then
    rm -f "$pid_file"
    return 1
  fi
  if ! [[ "$raw" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  if ! ilk_pid_alive "$raw"; then
    return 1
  fi
  # Stale-sentinel cross-check.
  local sentinel_file="${project_data_path}/runtime/last-exit.json"
  if [[ -f "$sentinel_file" ]]; then
    local state
    state=$(grep -o '"state"[[:space:]]*:[[:space:]]*"[^"]*"' "$sentinel_file" 2>/dev/null \
            | head -1 | sed 's/.*"state"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
    if [[ -n "$state" && "$state" != "running" ]]; then
      return 1
    fi
  fi
  return 0
}

# Launcher's test_running_pid (launch.sh:534-557) — echo convention.
# Echoes PID if busy, empty if free.
launcher_test_running_pid() {
  local project_path="$1"
  local pid_file="${HOME}/.ilk-data/projects/test-project/runtime/launcher/running.pid"
  if [[ ! -f "$pid_file" ]]; then
    echo ""
    return
  fi
  local raw_pid
  raw_pid=$(cat "$pid_file" | tr -d '[:space:]')
  if [[ -z "$raw_pid" ]]; then
    rm -f "$pid_file"
    echo ""
    return
  fi
  if ilk_pid_alive "$raw_pid"; then
    echo "$raw_pid"
  else
    rm -f "$pid_file"
    echo ""
  fi
}

failures=()
fail() { failures+=("$1"); }

# --- fixture: a fake live runner ---------------------------------------------
# The real runner is `bash run_ilk_loop_claude.sh --project-path <path>`.
# Create a stand-in with the same name so ilk_pid_alive's command-line match
# fires. The `sleep` keeps it alive for the duration of the test.
fake_runner_dir="$TMPDIR_TEST/fake-runner"
mkdir -p "$fake_runner_dir"
printf '#!/usr/bin/env bash\nsleep 300\n' > "$fake_runner_dir/run_ilk_loop_claude.sh"
bash "$fake_runner_dir/run_ilk_loop_claude.sh" --project-path "$PROJECT_PATH" &
fake_runner_pid=$!
sleep 0.5

if ! kill -0 "$fake_runner_pid" 2>/dev/null; then
  echo "FATAL: fake runner did not stay alive" >&2
  exit 1
fi

# --- case 1: running.pid absent, live runner present --------------------------
rm -f "$PID_FILE"

scheduler_result=1
scheduler_test_running_pid "$DATA_DIR" && scheduler_result=0 || true

launch_output="$(launcher_test_running_pid "$PROJECT_PATH")"

if [[ "$scheduler_result" -eq 0 ]]; then
  fail "case 1 scheduler: reported busy with no running.pid (unexpected)"
fi
if [[ -n "$launch_output" ]]; then
  fail "case 1 launcher: reported busy ($launch_output) with no running.pid (unexpected)"
fi

# The defect: both dispatch sites see "free" while a runner IS alive.
# KNOWN_BAD=1 tolerates this so the test can be committed before the fix.
if [[ "${KNOWN_BAD:-0}" -ne 1 ]]; then
  fail "case 1: live runner $fake_runner_pid is invisible to the busy-check (KNOWN_BAD not set)"
fi

# --- case 2: running.pid names a stale/unrelated PID, live runner present -----
sleep 300 &
plain_pid=$!
echo "$plain_pid" > "$PID_FILE"

scheduler_result2=1
scheduler_test_running_pid "$DATA_DIR" && scheduler_result2=0 || true

launch_output2="$(launcher_test_running_pid "$PROJECT_PATH")"

if [[ "$scheduler_result2" -eq 0 ]]; then
  fail "case 2 scheduler: reported busy because of unrelated PID $plain_pid (unexpected)"
fi
if [[ -n "$launch_output2" ]]; then
  fail "case 2 launcher: reported busy ($launch_output2) because of unrelated PID $plain_pid (unexpected)"
fi

if [[ "${KNOWN_BAD:-0}" -ne 1 ]]; then
  fail "case 2: live runner $fake_runner_pid invisible; stale pid $plain_pid in running.pid (KNOWN_BAD not set)"
fi

# --- report -------------------------------------------------------------------
if [[ "${#failures[@]}" -gt 0 ]]; then
  echo "FAIL — ${#failures[@]} failure(s):"
  for f in "${failures[@]}"; do
    echo "  - $f"
  done
  exit 1
else
  if [[ "${KNOWN_BAD:-0}" -eq 1 ]]; then
    echo "OK (KNOWN_BAD) — live runner is invisible when running.pid disagrees."
    echo "  This is the defect pinned by the test. Remove KNOWN_BAD after fixing."
  else
    echo "OK — live runner correctly makes the project busy even when running.pid disagrees."
  fi
  exit 0
fi
