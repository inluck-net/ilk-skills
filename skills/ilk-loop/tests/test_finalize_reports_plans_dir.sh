#!/usr/bin/env bash
# Regression test: the runner's finalize report must anchor its final
# loop_status call to the run's RECORDED project root, never to the
# mutable $PROJECT_PATH.
#
# Escaped bug (measured 2026-09-20, run 20260920-111204, log lines
# 304-305): during a selfmod batch a failed merge-back leaves PROJECT_PATH
# pointing at the isolated worktree. The finalize block then ran
# `(cd "$PROJECT_PATH" && python3 loop_status.py ...)`, which resolved the
# WORKTREE's project key — a key with no plans dir — and the loop-end report
# printed `no plans dir found` while the same run's startup had resolved the
# external plans dir fine. The misreport masked the real terminal state.
#
# Fix shape: the finalize invocation must derive its root from
# SELFMOD_ORIGINAL_PROJECT_PATH (recorded at selfmod isolation start, the
# same root merge_selfmod_worktree restores from), with a :-$PROJECT_PATH
# fallback so non-selfmod runs keep reporting.
#
# Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
RUNNER="$SCRIPT_DIR/../scripts/run_ilk_loop_claude.sh"

failures=()

# --- Extract the finalize report region: from the "Final loop_status:"
# marker to the first subsequent stop_reason branch. Comments are kept in
# the extraction but every assertion below ignores comment-only lines.
region="$(awk '
  /Final loop_status:/ {found=1}
  found && /stop_reason/ {exit}
  found {print}
' "$RUNNER")"

if [[ -z "$region" ]]; then
  echo "FAIL: could not locate the finalize report region (no 'Final loop_status:' marker followed by a stop_reason branch)" >&2
  exit 1
fi

# Non-comment lines of the region, for mechanism assertions.
region_code="$(echo "$region" | grep -v '^[[:space:]]*#')"

# --- AC-1: a loop_status invocation exists in the finalize region ---
invocations="$(echo "$region_code" | grep -n 'LOOP_STATUS_SCRIPT' || true)"
if [[ -z "$invocations" ]]; then
  failures+=("AC-1: no loop_status.py invocation found in the finalize report region")
fi

# --- AC-2: the invocation is anchored to the recorded original root ---
# The resolution path must consult SELFMOD_ORIGINAL_PROJECT_PATH (the
# recorded root), on a code line — a comment mentioning it pins nothing.
if ! echo "$region_code" | grep -q 'SELFMOD_ORIGINAL_PROJECT_PATH'; then
  failures+=("AC-2: finalize report does not consult SELFMOD_ORIGINAL_PROJECT_PATH — the loop_status call is anchored to the mutable \$PROJECT_PATH and reports 'no plans dir found' after a failed selfmod merge")
fi

# --- AC-3: non-selfmod runs keep reporting (the :- fallback survives) ---
if ! echo "$region_code" | grep -q 'SELFMOD_ORIGINAL_PROJECT_PATH:-'; then
  failures+=("AC-3: no \${SELFMOD_ORIGINAL_PROJECT_PATH:-...} fallback — non-selfmod runs would lose their final loop_status report")
fi

# --- AC-4: the invocation itself is not anchored to the bare mutable root ---
# After the fix the cd target must be the derived root, not "$PROJECT_PATH"
# directly (which is the worktree path while a selfmod merge has failed).
if echo "$invocations" | grep -q 'cd "\$PROJECT_PATH"'; then
  failures+=("AC-4: loop_status invocation still cd's to the mutable \$PROJECT_PATH — in a failed selfmod merge that is the worktree, whose project key has no plans dir")
fi

# --- Verdict ---
if [[ ${#failures[@]} -gt 0 ]]; then
  for f in "${failures[@]}"; do
    echo "FAIL: $f" >&2
  done
  exit 1
fi

echo "PASS: finalize loop_status is anchored to the recorded project root"
exit 0
