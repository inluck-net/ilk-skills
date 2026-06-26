#!/usr/bin/env bash
# Regression test: record_err_context + finalize_sentinel enrich stopped_reason
# with the failing line + command on any set -e exit.
#
# AC-1: record_err_context sets _LAST_ERR_CONTEXT; finalize_sentinel includes it
#       in stopped_reason (line number and command appear in last-exit.json).
# AC-2: The runner source contains the non-fatal set +e / set -e guard around
#       the post-iteration bookkeeping section.
#
# Exit 0 = green (all ACs pass), exit 1 = red.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
RUNNER="$SCRIPT_DIR/../scripts/run_ilk_loop_claude.sh"

failures=()

# --- Dot-source the runner (defines functions, does not run main) ---
export ILK_DOTSOURCE_ONLY=1
# shellcheck source=/dev/null
set +e
source "$RUNNER" 2>/dev/null
source_rc=$?
set -e
if [[ "$source_rc" -ne 0 ]]; then
  echo "FAIL: sourcing run_ilk_loop_claude.sh exited $source_rc" >&2
  exit 1
fi
unset ILK_DOTSOURCE_ONLY

# --- AC-1: record_err_context + finalize_sentinel writes enriched stopped_reason ---
# Create a temp runtime_dir with a running sentinel.
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

RUN_ID="test-err-ctx-001"
PROJECT_PATH="/tmp/fake-project"
runtime_dir="$tmp_dir"

# Write a running sentinel.
python3 -c "import json; print(json.dumps({
  'state': 'running',
  'pid': 12345,
  'run_id': '$RUN_ID',
  'started_at': '2026-06-26T10:00:00+0800',
  'project_path': '$PROJECT_PATH',
  'cli': 'claude'
}))" > "$runtime_dir/last-exit.json"

# Call record_err_context then finalize_sentinel.
record_err_context 1407 'python3 -c bad'
finalize_sentinel

# Read the written sentinel and assert.
sentinel_state=$(python3 -c "import json; print(json.load(open('$runtime_dir/last-exit.json')).get('state',''))" 2>/dev/null)
sentinel_reason=$(python3 -c "import json; print(json.load(open('$runtime_dir/last-exit.json')).get('stopped_reason',''))" 2>/dev/null)

if [[ "$sentinel_state" != "interrupted" ]]; then
  failures+=("AC-1: expected state=interrupted, got '$sentinel_state'")
fi

if ! echo "$sentinel_reason" | grep -q "1407"; then
  failures+=("AC-1: stopped_reason missing line number 1407 (got: '$sentinel_reason')")
fi

if ! echo "$sentinel_reason" | grep -q "python3 -c bad"; then
  failures+=("AC-1: stopped_reason missing command 'python3 -c bad' (got: '$sentinel_reason')")
fi

# --- AC-2: Structural check — set +e / set -e guard around bookkeeping ---
# The runner must contain a set +e before the post-iteration bookkeeping
# (get_repo_heads after the claude call) and a set -e before the terminal
# decision (iter_stop_reason check).
bookkeeping_guard=$(grep -c 'set +e' "$RUNNER" 2>/dev/null || echo "0")
bookkeeping_restore=$(grep -c 'set -e' "$RUNNER" 2>/dev/null || echo "0")

# At minimum: the top-level set -Eeuo, plus the set +e/set -e guard pair.
if [[ "$bookkeeping_guard" -lt 1 ]]; then
  failures+=("AC-2: runner missing 'set +e' guard around bookkeeping section")
fi

# Verify the guard wraps the right section: set +e appears before get_repo_heads
# (the first non-essential bookkeeping command after the claude call) and set -e
# appears before the iter_stop_reason check.
set_plus_e_line=$(grep -n 'set +e' "$RUNNER" | tail -1 | cut -d: -f1)
set_minus_e_line=$(grep -n 'set -e$' "$RUNNER" | tail -1 | cut -d: -f1)
get_repo_line=$(grep -n 'get_repo_heads "$heads_after_file"' "$RUNNER" | head -1 | cut -d: -f1)
iter_stop_line=$(grep -n 'if \[\[ -n "$iter_stop_reason" \]\]' "$RUNNER" | head -1 | cut -d: -f1)

if [[ -n "$set_plus_e_line" && -n "$get_repo_line" ]]; then
  if [[ "$set_plus_e_line" -ge "$get_repo_line" ]]; then
    failures+=("AC-2: set +e (line $set_plus_e_line) should be before get_repo_heads (line $get_repo_line)")
  fi
fi

if [[ -n "$set_minus_e_line" && -n "$iter_stop_line" ]]; then
  if [[ "$set_minus_e_line" -ge "$iter_stop_line" ]]; then
    failures+=("AC-2: set -e (line $set_minus_e_line) should be before iter_stop_reason check (line $iter_stop_line)")
  fi
fi

# --- Verdict ---
if [[ ${#failures[@]} -gt 0 ]]; then
  for f in "${failures[@]}"; do
    echo "FAIL: $f" >&2
  done
  exit 1
fi

echo "PASS: record_err_context + finalize_sentinel enrich stopped_reason; bookkeeping guard present"
exit 0
