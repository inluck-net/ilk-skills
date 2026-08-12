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
# Full-runner helpers
# ===========================================================================
RUNNER="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"

project_key_for() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/skills/ilk-loop/scripts')
from ilk_paths import project_key
from pathlib import Path
print(project_key(Path('$1')))
" 2>/dev/null
}

# Build a fixture where every sub-plan is shipped (runner exits immediately).
# Plans go in $HOME/.ilk-data (external) so loop_status.py finds them.
# $2 is the HOME dir to use.
build_fixture_all_shipped() {
  local dir="$1"
  local home_dir="${2:-$HOME}"
  local key
  key=$(project_key_for "$dir")
  local plans="$home_dir/.ilk-data/projects/$key/plans"
  mkdir -p "$plans"

  cat > "$plans/MASTER-2026-08-12-execution.md" << 'FIXTURE'
---
title: MASTER-2026-08-12-execution
created: 2026-06-07T00:00:00+08:00
status: active
priority: 0
pause_after_ship: false
---

# MASTER-2026-08-12-execution

## Sub-plan registry

| # | Sub-plan | Status |
|---|---|---|
| 1 | [2026-08-12-task-a.md](./2026-08-12-task-a.md) | shipped |
FIXTURE

  cat > "$plans/2026-08-12-task-a.md" << 'FIXTURE'
---
plan: 2026-08-12-task-a
status: shipped
current_step: 3
estimated_steps: 3
last_updated: 2026-08-12
---

# 2026-08-12-task-a
FIXTURE

  cd "$dir" && git init -q && git commit -q --allow-empty -m "init"
}

# ===========================================================================
# Full-runner tests
# ===========================================================================

# AC: two runners for the SAME project both start (current buggy baseline).
# After step 2 adds the lock, this test flips to assert refusal.
test_same_project_double_start() {
  local tmpdir project_dir shared_home
  tmpdir="$(mktemp -d)"
  project_dir="$tmpdir/project-a"
  shared_home="$tmpdir/shared-home"
  mkdir -p "$project_dir" "$shared_home"

  local out1="$tmpdir/runner1.out"
  local out2="$tmpdir/runner2.out"

  # Build the fixture once under the shared HOME.  In production both the
  # scheduler and a manual /ilk-run share the same HOME, so the lock file
  # path is the same for both — which is the whole point.
  build_fixture_all_shipped "$project_dir" "$shared_home"

  # Launch both concurrently with the same HOME.
  HOME="$shared_home" bash "$RUNNER" \
    --project-path "$project_dir" --max-iterations 0 >"$out1" 2>&1 &
  local pid1=$!
  HOME="$shared_home" bash "$RUNNER" \
    --project-path "$project_dir" --max-iterations 0 >"$out2" 2>&1 &
  local pid2=$!

  local rc1 rc2
  wait "$pid1" || rc1=$?; rc1=${rc1:-0}
  wait "$pid2" || rc2=$?; rc2=${rc2:-0}

  # One must succeed (exit 0), the other must be refused (exit 3).
  local ok=0 refused=0
  if [[ $rc1 -eq 0 ]]; then ok=$((ok+1)); fi
  if [[ $rc2 -eq 0 ]]; then ok=$((ok+1)); fi
  if [[ $rc1 -eq 3 ]]; then refused=$((refused+1)); fi
  if [[ $rc2 -eq 3 ]]; then refused=$((refused+1)); fi
  if [[ $ok -ne 1 || $refused -ne 1 ]]; then
    fail "AC-same-project: expected 1 ok + 1 refused, got ok=$ok refused=$refused (rc1=$rc1 rc2=$rc2)"
    return
  fi
  pass "AC-same-project: one runner proceeds, one refused (lock working)"
}

# AC-6: two runners for DIFFERENT projects both succeed (always).
test_different_projects_independent() {
  local tmpdir dir_a dir_b
  tmpdir="$(mktemp -d)"
  dir_a="$tmpdir/project-a"
  dir_b="$tmpdir/project-b"
  mkdir -p "$dir_a" "$dir_b"

  local out_a="$tmpdir/runner-a.out"
  local out_b="$tmpdir/runner-b.out"

  build_fixture_all_shipped "$dir_a" "$tmpdir/home-a"
  build_fixture_all_shipped "$dir_b" "$tmpdir/home-b"

  HOME="$tmpdir/home-a" bash "$RUNNER" \
    --project-path "$dir_a" --max-iterations 0 >"$out_a" 2>&1 &
  local pid_a=$!
  HOME="$tmpdir/home-b" bash "$RUNNER" \
    --project-path "$dir_b" --max-iterations 0 >"$out_b" 2>&1 &
  local pid_b=$!

  local rc_a rc_b
  wait "$pid_a" || rc_a=$?; rc_a=${rc_a:-0}
  wait "$pid_b" || rc_b=$?; rc_b=${rc_b:-0}

  if [[ $rc_a -ne 0 || $rc_b -ne 0 ]]; then
    fail "AC-6: both runners for different projects should exit 0 (rc_a=$rc_a rc_b=$rc_b)"
    return
  fi
  pass "AC-6: runners for different projects both proceed"
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

if [[ "$mode" == "full-runner" ]]; then
  test_same_project_double_start
  test_different_projects_independent
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
