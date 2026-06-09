#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# test_runner_branch_setup.sh — tests for Gaps 2+3 (branch-off-fresh-base)
# =============================================================================
# Creates temp git repos with fake remotes and exercises the branch-setup
# + freshness-preflight functions in run_ilk_loop_claude.sh.
#
# AC coverage:
#   AC-1: branch: block => checkout -B <name> <create_from>
#   AC-2: no branch: block => no-op (back-compat)
#   AC-3: dirty working tree => abort with clear error
#   AC-4: missing/unfetchable base => abort with clear error
#   AC-5: stale local ref => force-refresh (ls-remote detects mismatch)
#   AC-6: ps1 has structural keywords (grep)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNNER="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"
PS1="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.ps1"

PASS=0
FAIL=0
TESTS_RUN=0

cleanup() {
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

# Create a bare "remote" + a clone that tracks it. Sets REMOTE_BARE and
# CLONE vars. The clone has one commit on 'main'.
setup_repos() {
  TEST_TMPDIR=$(mktemp -d)
  REMOTE_BARE="$TEST_TMPDIR/remote.git"
  CLONE="$TEST_TMPDIR/clone"

  git init --bare "$REMOTE_BARE" >/dev/null 2>&1

  # Create a working repo, push to the bare
  local tmp_work="$TEST_TMPDIR/tmp_work"
  git init "$tmp_work" >/dev/null 2>&1
  git -C "$tmp_work" config user.email "test@test.com"
  git -C "$tmp_work" config user.name "Test"
  echo "initial" > "$tmp_work/file.txt"
  git -C "$tmp_work" add file.txt
  git -C "$tmp_work" commit -m "initial commit" >/dev/null 2>&1
  git -C "$tmp_work" remote add origin "$REMOTE_BARE"
  git -C "$tmp_work" push origin main >/dev/null 2>&1

  # Clone from the bare (this is our "project repo")
  git clone "$REMOTE_BARE" "$CLONE" >/dev/null 2>&1
  git -C "$CLONE" config user.email "test@test.com"
  git -C "$CLONE" config user.name "Test"
}

# Source the runner with ILK_DOTSOURCE_ONLY=1 to get functions without running main.
# Also sets up REPOS and PROJECT_PATH for the functions.
source_runner() {
  # We need to set up globals that the functions expect
  export ILK_DOTSOURCE_ONLY=1
  _SKILL_ROOT="$REPO_ROOT/skills"
  # shellcheck disable=SC1090
  source "$RUNNER"
  unset ILK_DOTSOURCE_ONLY

  # Override globals for test
  REPOS=("$CLONE")
  PROJECT_PATH="$CLONE"
}

# ===== Test 1: AC-1 — branch: block causes checkout -B =====
test_branch_checkout() {
  echo ""
  echo "--- Test: AC-1 — branch: block causes checkout -B ---"
  setup_repos

  # Advance the remote so there's something to branch from
  git -C "$TEST_TMPDIR/tmp_work" checkout -b feature-branch 2>/dev/null || true
  git -C "$TEST_TMPDIR/tmp_work" checkout main 2>/dev/null || true
  echo "second" > "$TEST_TMPDIR/tmp_work/file2.txt"
  git -C "$TEST_TMPDIR/tmp_work" add file2.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "second commit" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  source_runner

  # Set branch block globals
  BRANCH_CREATE_FROM="origin/main"
  BRANCH_NAME="feat/test-branch"
  BRANCH_MERGE_BACK=false

  # Run setup_branch
  local output
  output=$(setup_branch 2>&1) || true

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "feat/test-branch" ]]; then
    pass "AC-1: checkout -B landed on correct branch"
  else
    fail "AC-1: expected branch 'feat/test-branch', got '$current'" "$output"
  fi

  # Verify the branch points to the right commit
  local local_sha remote_sha
  local_sha=$(git -C "$CLONE" rev-parse HEAD)
  remote_sha=$(git -C "$CLONE" rev-parse origin/main)
  if [[ "$local_sha" == "$remote_sha" ]]; then
    pass "AC-1: branch points to fresh base"
  else
    fail "AC-1: branch HEAD ($local_sha) != origin/main ($remote_sha)"
  fi
}

# ===== Test 2: AC-2 — no branch: block => no-op =====
test_no_branch_block() {
  echo ""
  echo "--- Test: AC-2 — no branch: block => no-op ---"
  setup_repos
  source_runner

  # No branch block — all stay empty
  BRANCH_CREATE_FROM=""
  BRANCH_NAME=""
  BRANCH_MERGE_BACK=false

  local before_sha after_sha
  before_sha=$(git -C "$CLONE" rev-parse HEAD)

  local output
  output=$(setup_branch 2>&1) || true

  after_sha=$(git -C "$CLONE" rev-parse HEAD)
  local current_branch
  current_branch=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$before_sha" == "$after_sha" && "$current_branch" == "main" ]]; then
    pass "AC-2: no branch block => no change (stayed on main)"
  else
    fail "AC-2: expected no change, but HEAD moved or branch changed" "before=$before_sha after=$after_sha branch=$current_branch"
  fi
}

# ===== Test 3: AC-3 — dirty working tree => abort =====
test_dirty_tree() {
  echo ""
  echo "--- Test: AC-3 — dirty working tree => abort ---"
  setup_repos
  source_runner

  # Make the tree dirty
  echo "dirty" > "$CLONE/dirty.txt"
  git -C "$CLONE" add dirty.txt

  BRANCH_CREATE_FROM="origin/main"
  BRANCH_NAME="feat/dirty-test"
  BRANCH_MERGE_BACK=false

  local output exit_code=0
  output=$(setup_branch 2>&1) || exit_code=$?

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "main" ]]; then
    pass "AC-3: dirty tree => stayed on main (aborted)"
  else
    fail "AC-3: expected to stay on main, but switched to '$current'"
  fi

  if echo "$output" | grep -qi "dirty\|stash"; then
    pass "AC-3: error message mentions dirty/stash"
  else
    fail "AC-3: error message should mention dirty or stash" "$output"
  fi
}

# ===== Test 4: AC-4 — missing base => abort =====
test_missing_base() {
  echo ""
  echo "--- Test: AC-4 — missing/unfetchable base => abort ---"
  setup_repos
  source_runner

  BRANCH_CREATE_FROM="origin/nonexistent-branch"
  BRANCH_NAME="feat/bad-base"
  BRANCH_MERGE_BACK=false

  local output exit_code=0
  output=$(setup_branch 2>&1) || exit_code=$?

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "main" ]]; then
    pass "AC-4: missing base => stayed on main (aborted)"
  else
    fail "AC-4: expected to stay on main, but switched to '$current'"
  fi

  if echo "$output" | grep -qi "not found\|cannot resolve\|fetch.*failed\|does not exist\|Error:"; then
    pass "AC-4: error message describes the missing base"
  else
    fail "AC-4: error message should describe the missing base" "$output"
  fi
}

# ===== Test 5: AC-5 — stale ref => force-refresh =====
test_stale_ref() {
  echo ""
  echo "--- Test: AC-5 — stale local ref => force-refresh ---"
  setup_repos

  # Advance the remote
  echo "new-content" > "$TEST_TMPDIR/tmp_work/new-file.txt"
  git -C "$TEST_TMPDIR/tmp_work" add new-file.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "advance remote" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1

  # Fetch into clone so we have the ref...
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  # ...then advance the remote AGAIN so the clone's tracking ref is stale
  echo "even-newer" > "$TEST_TMPDIR/tmp_work/another.txt"
  git -C "$TEST_TMPDIR/tmp_work" add another.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "advance remote again" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1

  source_runner

  # Test ensure_fresh_base_ref directly
  local output exit_code=0
  output=$(ensure_fresh_base_ref "origin" "main" 2>&1) || exit_code=$?

  if [[ "$exit_code" -eq 0 ]]; then
    pass "AC-5: stale ref => force-refresh succeeded"
  else
    fail "AC-5: stale ref refresh should succeed" "$output"
  fi

  # Verify the clone now has the latest
  local clone_sha latest_sha
  clone_sha=$(git -C "$CLONE" rev-parse origin/main)
  latest_sha=$(git -C "$TEST_TMPDIR/tmp_work" rev-parse HEAD)
  if [[ "$clone_sha" == "$latest_sha" ]]; then
    pass "AC-5: after refresh, local tracking ref matches true remote tip"
  else
    fail "AC-5: local ref ($clone_sha) != remote tip ($latest_sha)"
  fi

  if echo "$output" | grep -qi "stale\|mismatch\|force-refresh\|refreshed"; then
    pass "AC-5: output describes the stale-ref detection and refresh"
  else
    fail "AC-5: output should mention stale/mismatch/refresh" "$output"
  fi
}

# ===== Test 6: AC-6 — ps1 has structural keywords =====
test_ps1_structural() {
  echo ""
  echo "--- Test: AC-6 — ps1 structural verification ---"

  local missing=()

  if ! grep -qi "create_from" "$PS1"; then
    missing+=("create_from")
  fi
  if ! grep -qi "checkout -B" "$PS1"; then
    missing+=("checkout -B")
  fi
  if ! grep -qi "ls-remote" "$PS1"; then
    missing+=("ls-remote")
  fi

  if [[ ${#missing[@]} -eq 0 ]]; then
    pass "AC-6: ps1 contains create_from, checkout -B, ls-remote"
  else
    fail "AC-6: ps1 missing keywords: ${missing[*]}"
  fi
}

# ===== Main =====

echo "=== test_runner_branch_setup.sh ==="
echo ""

test_branch_checkout
test_no_branch_block
test_dirty_tree
test_missing_base
test_stale_ref
test_ps1_structural

echo ""
echo "=== Results: $PASS passed, $FAIL failed, $TESTS_RUN total ==="

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
