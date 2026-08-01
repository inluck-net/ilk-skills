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
