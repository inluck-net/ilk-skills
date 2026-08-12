#!/usr/bin/env bash
# =============================================================================
# Test: the runner holds a per-project lock for its whole life.
#
# --helper-only  (default when no flag): test ilk_run_lock.py directly
#                covering AC-1 (acquire + exec), AC-2 (second fails),
#                AC-3 (SIGKILL releases lock).
#
# (no flag):     full-runner mode — two runners for the same project;
#                one must be refused.  (Step 1 adds this.)
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LOCK_HELPER="$REPO_ROOT/skills/ilk-loop/scripts/ilk_run_lock.py"

failures=()
fail() { failures+=("$1"); }
pass() { echo "  PASS: $1"; }

mode="helper-only"
if [[ "${1:-}" == "--full-runner" ]]; then
  mode="full-runner"
elif [[ "${1:-}" != "" && "${1:-}" != "--helper-only" ]]; then
  echo "usage: test_runner_single_instance_integration.sh [--helper-only|--full-runner]"
  exit 1
fi

# ===========================================================================
# AC-1: the helper acquires the lock and exec's the command
# ===========================================================================
test_ac1_acquire_and_exec() {
  local tmpdir lockfile
  tmpdir="$(mktemp -d)"
  lockfile="$tmpdir/run.lock"

  # Run a simple command under the lock — it should succeed and the command
  # should execute.
  local output
  output="$(python3 "$LOCK_HELPER" --lock "$lockfile" -- echo "ac1-hello" 2>&1)" || {
    fail "AC-1: helper exited non-zero"
    return
  }
  if [[ "$output" != *"ac1-hello"* ]]; then
    fail "AC-1: command did not execute (output: $output)"
    return
  fi
  # The lock file should exist and contain our metadata.
  if [[ ! -f "$lockfile" ]]; then
    fail "AC-1: lock file was not created"
    return
  fi
  pass "AC-1: acquire + exec"
}

# ===========================================================================
# AC-2: a second invocation while the first is alive fails with exit 3
# ===========================================================================
test_ac2_second_fails() {
  local tmpdir lockfile
  tmpdir="$(mktemp -d)"
  lockfile="$tmpdir/run.lock"

  # Start a long-lived holder in the background.
  python3 "$LOCK_HELPER" --lock "$lockfile" -- sleep 60 &
  local holder_pid=$!
  # Give it time to acquire.
  sleep 0.3

  # Try a second acquire — must fail with exit 3.
  local stderr_file="$tmpdir/stderr2.txt"
  set +e
  python3 "$LOCK_HELPER" --lock "$lockfile" -- echo "should-not-run" \
    2>"$stderr_file" >/dev/null
  local rc=$?
  set -e

  kill "$holder_pid" 2>/dev/null || true
  wait "$holder_pid" 2>/dev/null || true

  if [[ $rc -ne 3 ]]; then
    fail "AC-2: second invocation exited $rc (expected 3)"
    return
  fi
  if ! grep -q "another runner holds this lock" "$stderr_file"; then
    fail "AC-2: stderr missing holder message ($(cat "$stderr_file"))"
    return
  fi
  pass "AC-2: second invocation refused with exit 3"
}

# ===========================================================================
# AC-3: SIGKILL releases the lock; a subsequent invocation succeeds
# ===========================================================================
test_ac3_sigkill_releases() {
  local tmpdir lockfile
  tmpdir="$(mktemp -d)"
  lockfile="$tmpdir/run.lock"

  # Start a holder and SIGKILL it.
  python3 "$LOCK_HELPER" --lock "$lockfile" -- sleep 60 &
  local holder_pid=$!
  sleep 0.3

  kill -9 "$holder_pid" 2>/dev/null || true
  wait "$holder_pid" 2>/dev/null || true

  # Small delay for the kernel to release.
  sleep 0.2

  # A new acquire must succeed.
  local output
  output="$(python3 "$LOCK_HELPER" --lock "$lockfile" -- echo "ac3-post-kill" 2>&1)" || {
    fail "AC-3: could not acquire after SIGKILL (rc=$?, output: $output)"
    return
  }
  if [[ "$output" != *"ac3-post-kill"* ]]; then
    fail "AC-3: command did not execute after SIGKILL release"
    return
  fi
  pass "AC-3: SIGKILL releases lock, next acquire succeeds"
}

# ===========================================================================
# Main
# ===========================================================================
echo "=== test_runner_single_instance_integration.sh (mode=$mode) ==="

if [[ "$mode" == "helper-only" ]]; then
  test_ac1_acquire_and_exec
  test_ac2_second_fails
  test_ac3_sigkill_releases
fi

if [[ $mode == "full-runner" ]]; then
  echo "  (full-runner mode — steps 1+ add these tests)"
fi

echo ""
if [[ ${#failures[@]} -gt 0 ]]; then
  echo "FAILURES (${#failures[@]}):"
  for f in "${failures[@]}"; do
    echo "  - $f"
  done
  exit 1
else
  echo "All tests passed."
  exit 0
fi
