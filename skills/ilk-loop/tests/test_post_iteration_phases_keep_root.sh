#!/usr/bin/env bash
# Regression test: the runner's post-iteration phases must resolve the PLANS
# side from the recorded pre-isolation root, never from the mutable
# $PROJECT_PATH — while the gate's TREE stays the worktree, because the
# clone's HEAD does not move until merge-back runs.
#
# Escaped bug (measured 2026-09-20, runs 20260920-131919, -132454, -133041):
# under selfmod isolation create_selfmod_worktree reassigns PROJECT_PATH to
# the worktree. Two post-iteration phases then resolved from it:
#   - write_ship_proof_records probed `(cd "$PROJECT_PATH" && loop_status
#     --json)` — the worktree's project key has no plans dir, the probe
#     exited 2, and the ledger got no rows ("probe did not answer (exit 2)
#     -- writing no ledger rows").
#   - invoke_local_checks "$PROJECT_PATH" passed the worktree to
#     run_local_checks.py --project, whose find_subplan derives the plans
#     dir from that root's project key — no plans dir, so the gate errored
#     `sub-plan not found ... -> error`. The error is deterministic, the B2
#     confirm-before-block rerun reproduced it, the loop stopped
#     `local_checks not passing (B2 confirmed)`, and ship-integrity reverted
#     the batch's shipped sub-plan with "gate record unreadable" — work that
#     was committed and genuinely green.
#
# Fix shape: plans resolution anchors to SELFMOD_ORIGINAL_PROJECT_PATH (the
# root recorded at isolation start, the same root merge_selfmod_worktree
# restores from) with a :-$PROJECT_PATH / :-$project fallback so non-selfmod
# runs are unchanged; the helper call passes --repo-root "$project" so
# isolation and check cwd still target the worktree tree — anchoring BOTH
# roots to the original would gate the pre-merge clone, a tree that does not
# contain the iteration's commits.
#
# Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
RUNNER="$SCRIPT_DIR/../scripts/run_ilk_loop_claude.sh"
HELPER="$SCRIPT_DIR/../scripts/run_local_checks.py"

failures=()

# Extract a top-level function body: from 'name() {' to the first column-0 '}'.
func_body() {
  awk -v fn="$1" '
    index($0, fn "() {") == 1 { found = 1 }
    found { print }
    found && /^}/ { exit }
  ' "$RUNNER"
}

# Code lines only — a comment naming the marker pins nothing.
code_only() { grep -v '^[[:space:]]*#' || true; }

# --- Region 1: write_ship_proof_records (the ledger probe) ---------------
probe_code="$(func_body write_ship_proof_records | code_only)"

if [[ -z "$probe_code" ]]; then
  failures+=("AC-0: could not locate the write_ship_proof_records function body in the runner")
fi

# AC-1: the probe consults the recorded original root on a code line.
if ! echo "$probe_code" | grep -q 'SELFMOD_ORIGINAL_PROJECT_PATH'; then
  failures+=("AC-1: ship-proof probe does not consult SELFMOD_ORIGINAL_PROJECT_PATH — under selfmod isolation it cd's to the worktree, whose project key has no plans dir, and the ledger gets no rows")
fi

# AC-2: the :- fallback survives, so non-selfmod runs keep their ledger.
if ! echo "$probe_code" | grep -q 'SELFMOD_ORIGINAL_PROJECT_PATH:-'; then
  failures+=("AC-2: no \${SELFMOD_ORIGINAL_PROJECT_PATH:-...} fallback in the probe — non-selfmod runs would lose their ledger rows")
fi

# AC-3: the probe is not anchored to the bare mutable root.
if echo "$probe_code" | grep -q 'cd "\$PROJECT_PATH"'; then
  failures+=("AC-3: ship-proof probe still cd's to the mutable \$PROJECT_PATH — under selfmod isolation that is the worktree and the probe exits 2")
fi

# --- Region 2: invoke_local_checks (the post-iteration gates) ------------
lc_code="$(func_body invoke_local_checks | code_only)"

if [[ -z "$lc_code" ]]; then
  failures+=("AC-0: could not locate the invoke_local_checks function body in the runner")
fi

# AC-4: plans resolution derives from the recorded root, not the tree root.
if ! echo "$lc_code" | grep -q 'SELFMOD_ORIGINAL_PROJECT_PATH'; then
  failures+=("AC-4: invoke_local_checks does not derive its plans root from SELFMOD_ORIGINAL_PROJECT_PATH — the gate harness receives the worktree as --project, find_subplan returns None, and every gate errors 'sub-plan not found'")
fi

# AC-5: the helper call passes --repo-root so the TREE stays the worktree.
if ! echo "$lc_code" | grep -q -- '--repo-root'; then
  failures+=("AC-5: invoke_local_checks does not pass --repo-root — with both roots anchored to the original the gate would isolate the pre-merge clone, a tree without the iteration's commits: a green gate over the wrong commit")
fi

# --- Region 3: the helper flag exists ------------------------------------
# AC-6: run_local_checks.py accepts --repo-root. A runner call to an unknown
# flag dies in argparse with usage text on stderr — the gate errors again.
if ! grep -q -- '--repo-root' "$HELPER"; then
  failures+=("AC-6: run_local_checks.py has no --repo-root flag — the runner's helper call cannot decouple the plans root from the gate's tree")
fi

# --- Verdict --------------------------------------------------------------
if [[ ${#failures[@]} -gt 0 ]]; then
  for f in "${failures[@]}"; do
    echo "FAIL: $f" >&2
  done
  exit 1
fi

echo "PASS: post-iteration phases resolve plans from the recorded root, tree stays the worktree"
exit 0
