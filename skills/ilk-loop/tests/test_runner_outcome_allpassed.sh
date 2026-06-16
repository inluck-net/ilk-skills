#!/usr/bin/env bash
# Red test: local_check_outcome must prefer all_passed over exit code
# when the helper JSON is available.
#
# Invoked by local_checks in sub-plan 2026-06-16-runner-trust-allpassed.
# Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SCRATCH="$REPO_ROOT/scratch/runner-outcome-allpassed"

# Clean slate
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"

# Source the runner with ILK_DOTSOURCE_ONLY=1
export ILK_DOTSOURCE_ONLY=1
RUNNER_PATH="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"
if ! source "$RUNNER_PATH"; then
  echo "FAIL: sourcing run_ilk_loop_claude.sh failed" >&2
  exit 1
fi
unset ILK_DOTSOURCE_ONLY

# Verify the helper exists
if ! type -t local_check_outcome >/dev/null 2>&1; then
  echo "FAIL: local_check_outcome function not found after sourcing runner" >&2
  exit 1
fi

# --- AC-2/3/4: reproduction matrix ---
failures=()

# Case 1: all_passed="true" + exit 0 → 'pass'
result=$(local_check_outcome "true" 0)
if [[ "$result" != "pass" ]]; then
  failures+=("Case 1: all_passed=true + exit=0: expected 'pass', got '$result'")
fi

# Case 2: all_passed="false" + exit 0 → 'fail' (AC-2: failing helper must be fail)
result=$(local_check_outcome "false" 0)
if [[ "$result" != "fail" ]]; then
  failures+=("Case 2: all_passed=false + exit=0: expected 'fail', got '$result'")
fi

# Case 3: all_passed="" + exit 0 → 'pass' (AC-3: fallback to exit code)
result=$(local_check_outcome "" 0)
if [[ "$result" != "pass" ]]; then
  failures+=("Case 3: all_passed='' + exit=0: expected 'pass', got '$result'")
fi

# Case 4: all_passed="" + exit 1 → 'fail' (AC-3: fallback to exit code)
result=$(local_check_outcome "" 1)
if [[ "$result" != "fail" ]]; then
  failures+=("Case 4: all_passed='' + exit=1: expected 'fail', got '$result'")
fi

# Case 5: all_passed="" + exit 7 → 'error' (AC-3: fallback to exit code)
result=$(local_check_outcome "" 7)
if [[ "$result" != "error" ]]; then
  failures+=("Case 5: all_passed='' + exit=7: expected 'error', got '$result'")
fi

# Clean up
rm -rf "$SCRATCH"

if [[ ${#failures[@]} -gt 0 ]]; then
  for f in "${failures[@]}"; do
    echo "FAIL: $f" >&2
  done
  exit 1
fi

echo "PASS: local_check_outcome — all 5 matrix cases correct"
exit 0
