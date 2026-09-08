#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# test_iteration_outcome.sh — pin: a productive boundary kill is not a barren one
# =============================================================================
# RED-FIRST test for sub-plan a-productive-timeout-is-not-a-barren-one.
#
# Pins the expected behaviour of the stop-reason decision when
# ITER_COMPLETED=0 (gtimeout boundary kill):
#
#   - With new commits (total_new > 0) → stop reason is NOT the barren "timeout"
#   - With zero new commits (total_new = 0) → stop reason IS "timeout"
#
# The decision logic at run_ilk_loop_claude.sh:2271-2290 currently does NOT
# consult total_new in the boundary-kill branch, so test 1 (productive) FAILS.
#
# Once step 1 extracts the decision into a callable function and fixes it,
# this test will source that function directly instead of replicating the logic.
# =============================================================================

PASS=0
FAIL=0
TESTS_RUN=0

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

# ---------------------------------------------------------------------------
# Stop-reason decision logic
# ---------------------------------------------------------------------------
# This replicates the decision block at run_ilk_loop_claude.sh:2271-2290.
# After step 1, this will be replaced by sourcing the extracted function.
#
# Inputs: ITER_COMPLETED, total_new, ITER_BUDGET_EXHAUSTED, no_progress_streak
# Output: prints the decided stop reason (empty = continue)

decide_stop_reason() {
  local iter_stop_reason=""
  if [[ "$ITER_COMPLETED" -eq 0 ]]; then
    iter_stop_reason="timeout"
  elif [[ "$ITER_BUDGET_EXHAUSTED" -eq 1 ]]; then
    iter_stop_reason="budget-exhausted"
  elif [[ "$total_new" -eq 0 ]]; then
    no_progress_streak=$((no_progress_streak + 1))
    if [[ "$no_progress_streak" -ge 3 ]]; then
      iter_stop_reason="no-progress"
    fi
  else
    no_progress_streak=0
  fi
  echo "$iter_stop_reason"
}

# ===========================================================================
# Test 1: boundary kill WITH new commits → NOT the barren "timeout"
# ===========================================================================
# An iteration that finished its work and landed commits, but was killed by
# gtimeout at the boundary (ITER_COMPLETED=0), must not be recorded as a
# barren timeout. The evidence (total_new > 0) is already in scope.

echo "Test 1: productive boundary kill (total_new=4, ITER_COMPLETED=0)"

ITER_COMPLETED=0
total_new=4
ITER_BUDGET_EXHAUSTED=0
no_progress_streak=0

result=$(decide_stop_reason)

if [[ "$result" != "timeout" ]]; then
  pass "productive boundary kill recorded as '$result' (not 'timeout')"
else
  fail "productive boundary kill recorded as 'timeout'" \
    "expected something other than 'timeout' when total_new=$total_new, got 'timeout'"
fi

# ===========================================================================
# Test 2: barren boundary kill (zero new commits) → still "timeout"
# ===========================================================================
# A genuine barren timeout — the iteration produced nothing — must still
# report as "timeout". A fix that relabels every timeout is not a fix.

echo "Test 2: barren boundary kill (total_new=0, ITER_COMPLETED=0)"

ITER_COMPLETED=0
total_new=0
ITER_BUDGET_EXHAUSTED=0
no_progress_streak=0

result=$(decide_stop_reason)

if [[ "$result" == "timeout" ]]; then
  pass "barren boundary kill still recorded as 'timeout'"
else
  fail "barren boundary kill recorded as '$result'" \
    "expected 'timeout' when total_new=0, got '$result'"
fi

# ===========================================================================
# Test 3: non-timeout paths unchanged
# ===========================================================================
# Sanity: budget-exhausted and normal no-progress still work as before.

echo "Test 3: budget-exhausted path unchanged"

ITER_COMPLETED=1
total_new=0
ITER_BUDGET_EXHAUSTED=1
no_progress_streak=0

result=$(decide_stop_reason)

if [[ "$result" == "budget-exhausted" ]]; then
  pass "budget-exhausted path returns 'budget-exhausted'"
else
  fail "budget-exhausted path returned '$result'" \
    "expected 'budget-exhausted'"
fi

echo "Test 4: no-progress at streak=3 unchanged"

ITER_COMPLETED=1
total_new=0
ITER_BUDGET_EXHAUSTED=0
no_progress_streak=2  # will be incremented to 3

result=$(decide_stop_reason)

if [[ "$result" == "no-progress" ]]; then
  pass "no-progress at streak=3 returns 'no-progress'"
else
  fail "no-progress at streak=3 returned '$result'" \
    "expected 'no-progress'"
fi

echo "Test 5: normal iteration (total_new > 0, completed) returns empty"

ITER_COMPLETED=1
total_new=3
ITER_BUDGET_EXHAUSTED=0
no_progress_streak=0

result=$(decide_stop_reason)

if [[ -z "$result" ]]; then
  pass "normal productive iteration returns empty (continue)"
else
  fail "normal productive iteration returned '$result'" \
    "expected empty string"
fi

# ===========================================================================
# Report
# ===========================================================================

echo ""
echo "=== test_iteration_outcome ==="
echo "Tests run: $TESTS_RUN"
echo "Passed:    $PASS"
echo "Failed:    $FAIL"

if [[ "$FAIL" -gt 0 ]]; then
  echo "FAILED"
  exit 1
fi
echo "ALL PASSED"
exit 0
