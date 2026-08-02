#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# test_watchdog_singleton.sh — tests for the watchdog singleton guard
# =============================================================================
# Verifies that:
#   AC-0: sourcing watchdog.sh defines functions with no side effects;
#         executing it still reaches main.
#   AC-1: a live watchdog process is identified as a watchdog  (step 1)
#   AC-2: dead / recycled PIDs are not misidentified           (step 1)
#   AC-3: stale exit does not delete a live instance's pid     (step 2)
#   AC-4: second launch refuses with WATCHDOG ALREADY RUNNING  (step 3)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WATCHDOG="$REPO_ROOT/skills/ilk-watchdog/scripts/watchdog.sh"

PASS=0
FAIL=0
TESTS_RUN=0

cleanup() {
  # Kill any background processes we started
  local pids_to_kill="${BACKGROUND_PIDS:-}"
  if [[ -n "$pids_to_kill" ]]; then
    for pid in $pids_to_kill; do
      kill "$pid" 2>/dev/null || true
    done
  fi
  if [[ -n "${TEST_TMPDIR:-}" && -d "$TEST_TMPDIR" ]]; then
    rm -rf "$TEST_TMPDIR"
  fi
}
trap cleanup EXIT

pass() {
  PASS=$((PASS + 1))
  TESTS_RUN=$((TESTS_RUN + 1))
  echo "  PASS: $1"
}

fail() {
  FAIL=$((FAIL + 1))
  TESTS_RUN=$((TESTS_RUN + 1))
  echo "  FAIL: $1"
  if [[ -n "${2:-}" ]]; then
    echo "        $2"
  fi
}

BACKGROUND_PIDS=""

# ----- Test setup --------------------------------------------------------

TEST_TMPDIR=$(mktemp -d)

# =============================================================================
# AC-0: sourcing defines functions; executing reaches main
# =============================================================================

echo "AC-0: sourcing watchdog.sh defines functions with no side effects"

# Source the watchdog in a subshell to capture functions without polluting
# this script's namespace (set -euo pipefail from watchdog.sh would affect us).
source_output=$(bash -c '
  source "'"$WATCHDOG"'" 2>/dev/null
  if declare -f test_process_command_alive >/dev/null 2>&1; then
    echo "FUNCTION_DEFINED"
  else
    echo "FUNCTION_MISSING"
  fi
' 2>/dev/null) || source_output="SOURCE_FAILED"

if [[ "$source_output" == "FUNCTION_DEFINED" ]]; then
  pass "sourcing defines test_process_command_alive"
elif [[ "$source_output" == "SOURCE_FAILED" ]]; then
  fail "sourcing defines test_process_command_alive" "source command failed"
else
  fail "sourcing defines test_process_command_alive" "expected FUNCTION_DEFINED, got '$source_output'"
fi

# Verify sourcing does NOT start a watchdog process
# Use a temp dir so resolve_project_by_cwd fails and main would exit if called
source_pids_before=$(ps -eo pid= 2>/dev/null | sort)
bash -c '
  cd "'"$TEST_TMPDIR"'"
  source "'"$WATCHDOG"'" 2>/dev/null
  sleep 0.2
' &
source_bg=$!
BACKGROUND_PIDS="$BACKGROUND_PIDS $source_bg"
sleep 0.5

# Check no watchdog process was spawned by the sourcing
# A watchdog would show "watchdog.sh" in its args
watchdog_count=$(ps -eo args= 2>/dev/null | grep -c '[w]atchdog.sh' || true)
# Exclude this test script itself and the source subshell
if [[ "$watchdog_count" -eq 0 ]]; then
  pass "sourcing does not start a watchdog process"
else
  fail "sourcing does not start a watchdog process" "found $watchdog_count watchdog.sh processes"
fi
wait "$source_bg" 2>/dev/null || true

echo "AC-0: executing watchdog.sh with no args reaches main and exits non-zero"

# Run from a temp dir with no project — main should exit 1
set +e
exec_output=$(cd "$TEST_TMPDIR" && bash "$WATCHDOG" 2>&1)
exec_rc=$?
set -e

if [[ "$exec_rc" -ne 0 ]]; then
  pass "executing with no args exits non-zero (rc=$exec_rc)"
else
  fail "executing with no args exits non-zero" "expected non-zero, got rc=0"
fi

if [[ -n "$exec_output" ]]; then
  pass "executing with no args produces output (usage / error)"
else
  fail "executing with no args produces output" "no output captured"
fi

# =============================================================================
# AC-1 + AC-2: test_process_command_alive identity check
# =============================================================================

echo "AC-1: a live watchdog-shaped process is identified as a watchdog"

# Source the watchdog to get the function definitions
# Use a subshell to isolate set -euo pipefail
eval "$(bash -c '
  source "'"$WATCHDOG"'" 2>/dev/null
  declare -f test_process_alive
  declare -f test_process_command_alive
')"

# Create a fake watchdog.sh script in tmpdir and background it
cat > "$TEST_TMPDIR/watchdog.sh" <<'FAKE'
#!/usr/bin/env bash
sleep 120
FAKE
chmod +x "$TEST_TMPDIR/watchdog.sh"
bash "$TEST_TMPDIR/watchdog.sh" &
FAKE_PID=$!
BACKGROUND_PIDS="$BACKGROUND_PIDS $FAKE_PID"
sleep 0.3

# AC-1: the fake watchdog's args contain "watchdog.sh"
set +e
test_process_command_alive "$FAKE_PID" "watchdog.sh"
rc_live=$?
set -e

if [[ "$rc_live" -eq 0 ]]; then
  pass "live watchdog-shaped process identified (pid=$FAKE_PID)"
else
  fail "live watchdog-shaped process identified" "expected rc=0, got rc=$rc_live"
fi

echo "AC-2: a dead PID is not identified as a watchdog"

# Use a PID that is guaranteed to be dead
DEAD_PID=99999999
set +e
test_process_command_alive "$DEAD_PID" "watchdog.sh"
rc_dead=$?
set -e

if [[ "$rc_dead" -ne 0 ]]; then
  pass "dead PID correctly rejected (pid=$DEAD_PID)"
else
  fail "dead PID correctly rejected" "expected non-zero, got rc=0"
fi

echo "AC-2: a false-positive command is not identified as a watchdog"

# Background a 'tail -f' on a file named watchdog.log — must NOT match
touch "$TEST_TMPDIR/watchdog.log"
tail -f "$TEST_TMPDIR/watchdog.log" &
TAIL_PID=$!
BACKGROUND_PIDS="$BACKGROUND_PIDS $TAIL_PID"
sleep 0.3

set +e
test_process_command_alive "$TAIL_PID" "watchdog.sh"
rc_tail=$?
set -e

if [[ "$rc_tail" -ne 0 ]]; then
  pass "tail -f .../watchdog.log correctly rejected as false positive (pid=$TAIL_PID)"
else
  fail "tail -f .../watchdog.log correctly rejected" "expected non-zero, got rc=0"
fi

# =============================================================================
# AC-3: stale instance exiting does NOT delete a live instance's pid file
# =============================================================================

echo "AC-3: cleanup respects pid file ownership"

# The cleanup function is local to run_watchdog_loop, so we test the ownership
# logic directly. This replicates the fixed cleanup from watchdog.sh:
#   if pid file contains a foreign pid, leave it; if it contains $$, delete it.

# --- AC-3a: foreign pid file is preserved ---
touch "$TEST_TMPDIR/watchdog.log"  # cleanup writes to log

# Start a live process to act as the "real" watchdog
sleep 120 &
FOREIGN_PID=$!
BACKGROUND_PIDS="$BACKGROUND_PIDS $FOREIGN_PID"
sleep 0.2

PID_FILE="$TEST_TMPDIR/watchdog.pid"
echo "$FOREIGN_PID" > "$PID_FILE"

# Run cleanup from a different process (simulating a stale duplicate dying)
TEST_PID_FILE="$PID_FILE" bash -c '
  pid_file="$TEST_PID_FILE"
  my_pid=$$
  if [[ -f "$pid_file" ]]; then
    recorded_pid=$(tr -d "[:space:]" < "$pid_file")
    if [[ "$recorded_pid" == "$my_pid" ]]; then
      rm -f "$pid_file"
    fi
  fi
'

if [[ -f "$PID_FILE" ]]; then
  preserved_pid=$(tr -d '[:space:]' < "$PID_FILE")
  if [[ "$preserved_pid" == "$FOREIGN_PID" ]]; then
    pass "foreign pid file preserved after stale duplicate exits"
  else
    fail "foreign pid file preserved" "pid changed to $preserved_pid"
  fi
else
  fail "foreign pid file preserved" "pid file was deleted"
fi

# --- AC-3b: own pid file is deleted on exit ---
OWN_PID_FILE="$TEST_TMPDIR/watchdog_own.pid"
echo "$$" > "$OWN_PID_FILE"

# Run cleanup where the pid matches (simulating a normal exit)
TEST_PID_FILE="$OWN_PID_FILE" bash -c '
  pid_file="$TEST_PID_FILE"
  my_pid=$$
  if [[ -f "$pid_file" ]]; then
    recorded_pid=$(tr -d "[:space:]" < "$pid_file")
    if [[ "$recorded_pid" == "$my_pid" ]]; then
      rm -f "$pid_file"
    fi
  fi
'

# The file should still exist because $$ in the file is the test runner,
# not the subshell — demonstrating the guard works for non-matching pids
if [[ -f "$OWN_PID_FILE" ]]; then
  pass "non-matching pid does not delete the file (AC-3b guard works)"
else
  fail "non-matching pid does not delete the file" "file was deleted"
fi

# --- AC-3c: matching pid DOES delete the file ---
MATCH_PID_FILE="$TEST_TMPDIR/watchdog_match.pid"

result=$(TEST_PID_FILE="$MATCH_PID_FILE" bash -c '
  pid_file="$TEST_PID_FILE"
  echo "$$" > "$pid_file"
  if [[ -f "$pid_file" ]]; then
    recorded_pid=$(tr -d "[:space:]" < "$pid_file")
    if [[ "$recorded_pid" == "$$" ]]; then
      rm -f "$pid_file"
    fi
  fi
  if [[ -f "$pid_file" ]]; then
    echo "STILL_EXISTS"
  else
    echo "DELETED"
  fi
')

if [[ "$result" == "DELETED" ]]; then
  pass "own pid file deleted on exit (matching pid)"
else
  fail "own pid file deleted on exit" "expected DELETED, got $result"
fi

# =============================================================================
# AC-4: second watchdog launched against a live project refuses
# =============================================================================

echo "AC-4: second watchdog refuses to start alongside a live one"

# Create a temp project with .git (required for ilk_paths.py to resolve)
AC4_TMPDIR=$(mktemp -d)
AC4_PROJ="$AC4_TMPDIR/proj"
mkdir -p "$AC4_PROJ/.git"

# Set ILK_DATA_HOME to isolate from real ~/.ilk-data
# Verify the watchdog dir resolves under our tmpdir — if it falls back to
# ~/.ilk-data, this test would disturb a live watchdog.
export ILK_DATA_HOME="$AC4_TMPDIR/data"

# Probe the resolved watchdog dir
RESOLVED_DIR=$(bash -c '
  source "'"$WATCHDOG"'" 2>/dev/null
  get_ilk_watchdog_dir "'"$AC4_PROJ"'"
')

# Normalize paths for macOS where /var is a symlink to /private/var
AC4_TMPDIR_REAL=$(python3 -c "import os; print(os.path.realpath('$AC4_TMPDIR'))")

if [[ -z "$RESOLVED_DIR" ]]; then
  fail "AC-4: watchdog dir resolves under TEST_TMPDIR" "resolved to empty"
elif [[ "$RESOLVED_DIR" != "$AC4_TMPDIR_REAL"* ]]; then
  fail "AC-4: watchdog dir resolves under TEST_TMPDIR" "resolved to $RESOLVED_DIR (outside $AC4_TMPDIR_REAL)"
else
  pass "watchdog dir resolves under TEST_TMPDIR ($RESOLVED_DIR)"
fi

WATCHDOG_LOG="$RESOLVED_DIR/watchdog.log"
WATCHDOG_PID_FILE="$RESOLVED_DIR/watchdog.pid"

# Start the first watchdog in background
# Use exec so the background process IS the watchdog (not a wrapper shell)
bash -c 'exec bash "'"$WATCHDOG"'" --project-path "'"$AC4_PROJ"'"' &
WD1_PID=$!
BACKGROUND_PIDS="$BACKGROUND_PIDS $WD1_PID"

# Wait for the pid file to appear (up to 15s)
waited=0
while [[ $waited -lt 15 ]]; do
  if [[ -f "$WATCHDOG_PID_FILE" ]]; then
    break
  fi
  sleep 1
  waited=$((waited + 1))
done

if [[ ! -f "$WATCHDOG_PID_FILE" ]]; then
  fail "AC-4: first watchdog created pid file" "pid file not found after ${waited}s"
else
  WD1_RECORDED_PID=$(tr -d '[:space:]' < "$WATCHDOG_PID_FILE")
  pass "first watchdog created pid file (pid=$WD1_RECORDED_PID)"
fi

# Start the second watchdog — it should refuse and exit
WD2_OUTPUT=$(bash "$WATCHDOG" --project-path "$AC4_PROJ" 2>&1) || true

# Assert it printed WATCHDOG ALREADY RUNNING
if echo "$WD2_OUTPUT" | grep -q 'WATCHDOG ALREADY RUNNING'; then
  pass "second watchdog printed WATCHDOG ALREADY RUNNING"
else
  fail "second watchdog printed WATCHDOG ALREADY RUNNING" "output: $WD2_OUTPUT"
fi

# Assert the first watchdog is still alive
if test_process_alive "$WD1_RECORDED_PID"; then
  pass "first watchdog still alive after second refused"
else
  fail "first watchdog still alive after second refused" "pid $WD1_RECORDED_PID is dead"
fi

# Assert the pid file still has the first watchdog's pid
if [[ -f "$WATCHDOG_PID_FILE" ]]; then
  CURRENT_PID=$(tr -d '[:space:]' < "$WATCHDOG_PID_FILE")
  if [[ "$CURRENT_PID" == "$WD1_RECORDED_PID" ]]; then
    pass "pid file still holds first watchdog's pid"
  else
    fail "pid file still holds first watchdog's pid" "expected $WD1_RECORDED_PID, got $CURRENT_PID"
  fi
else
  fail "pid file still holds first watchdog's pid" "pid file was deleted"
fi

# Clean up the AC-4 temp dir separately (don't leave watchdogs running)
kill "$WD1_RECORDED_PID" 2>/dev/null || true
rm -rf "$AC4_TMPDIR"

# ----- Report ------------------------------------------------------------

echo ""
echo "=== test_watchdog_singleton ==="
echo "Tests run: $TESTS_RUN"
echo "Passed:    $PASS"
echo "Failed:    $FAIL"

if [[ "$FAIL" -gt 0 ]]; then
  echo "FAILED"
  exit 1
fi
echo "ALL PASSED"
exit 0
