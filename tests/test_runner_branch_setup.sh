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
#   AC-7: narrow remote.origin.fetch — explicit refspec updates tracking ref
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

# Build a multi-master external-plans fixture on top of setup_repos:
#   - an origin branch whose NAME contains a slash (codex/convex-rewrite)
#   - a STALE shipped master (sorts first) with a misleading branch block
#   - an ACTIVE master (sorts later) whose branch block targets the slash branch
# Sets EXT_PLANS_DIR. Requires ILK_DATA_HOME exported (isolated data root).
setup_multimaster_fixture() {
  setup_repos

  # Push a branch whose own name contains a slash to the bare remote.
  git -C "$TEST_TMPDIR/tmp_work" checkout -b codex/convex-rewrite >/dev/null 2>&1
  echo "convex" > "$TEST_TMPDIR/tmp_work/convex.txt"
  git -C "$TEST_TMPDIR/tmp_work" add convex.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "convex rewrite base" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin codex/convex-rewrite >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" checkout main >/dev/null 2>&1
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  # Resolve the external plans dir for this project (honours ILK_DATA_HOME).
  EXT_PLANS_DIR=$(python3 "$REPO_ROOT/skills/ilk-loop/scripts/ilk_paths.py" \
    --start "$CLONE" 2>/dev/null | jq -r '.external_plans_dir')
  mkdir -p "$EXT_PLANS_DIR"

  # Stale, fully-shipped master — sorts FIRST by filename (what the old
  # `find | head -n1` / `glob | first` would pick) and carries a misleading
  # branch block that must NOT be used.
  cat > "$EXT_PLANS_DIR/MASTER-2026-01-01-stale-shipped-plan.md" <<'EOF'
---
status: shipped
created: 2026-01-01
branch: {create_from: origin/main, name: feat/stale-should-not-be-picked, merge_back: false}
---
# Stale shipped master

Registry:
- 2026-01-01-stale-sub.md
EOF
  cat > "$EXT_PLANS_DIR/2026-01-01-stale-sub.md" <<'EOF'
---
status: shipped
current_step: 1
estimated_steps: 1
---
# stale sub
EOF

  # Active master — sorts LATER, references a still-pending sub-plan, and its
  # branch block targets the slash-named branch via the configured remote.
  cat > "$EXT_PLANS_DIR/MASTER-2026-09-01-active-windows-plan.md" <<'EOF'
---
status: active
created: 2026-09-01
branch: {create_from: origin/codex/convex-rewrite, name: feat/active-windows, merge_back: false}
---
# Active master

Registry:
- 2026-09-01-active-sub.md
EOF
  cat > "$EXT_PLANS_DIR/2026-09-01-active-sub.md" <<'EOF'
---
status: pending
current_step: 0
estimated_steps: 3
---
# active sub
EOF
}

# ===== Test 7: Bug #1 — branch block read from the ACTIVE master =====
test_active_master_selection() {
  echo ""
  echo "--- Test: Bug #1 — branch block resolved from the ACTIVE master ---"
  export ILK_DATA_HOME="$(mktemp -d)/ilk-data"
  setup_multimaster_fixture
  source_runner

  # Reset branch globals so we observe what the parser sets.
  BRANCH_CREATE_FROM=""
  BRANCH_NAME=""
  BRANCH_MERGE_BACK=false

  # Must run in the CURRENT shell (not a $() subshell) so the BRANCH_* globals
  # the parser sets are observable here.
  local output logf="$TEST_TMPDIR/parse.log"
  parse_master_branch_block >"$logf" 2>&1 || true
  output=$(cat "$logf")

  if [[ "$BRANCH_NAME" == "feat/active-windows" ]]; then
    pass "Bug #1: parsed the ACTIVE master's branch name"
  else
    fail "Bug #1: expected name 'feat/active-windows', got '$BRANCH_NAME'" "$output"
  fi

  if [[ "$BRANCH_CREATE_FROM" == "origin/codex/convex-rewrite" ]]; then
    pass "Bug #1: parsed the ACTIVE master's create_from"
  else
    fail "Bug #1: expected create_from 'origin/codex/convex-rewrite', got '$BRANCH_CREATE_FROM'"
  fi

  if [[ "$BRANCH_NAME" != "feat/stale-should-not-be-picked" ]]; then
    pass "Bug #1: did NOT pick the stale shipped master's branch block"
  else
    fail "Bug #1: regressed — picked the stale master's branch block"
  fi

  unset ILK_DATA_HOME
}

# ===== Test 8: Bug #2 — slash branch name fetches/checks out cleanly =====
test_slash_branch_name() {
  echo ""
  echo "--- Test: Bug #2 — create_from with a slash-named branch ---"
  export ILK_DATA_HOME="$(mktemp -d)/ilk-data"
  setup_multimaster_fixture
  source_runner

  BRANCH_CREATE_FROM="origin/codex/convex-rewrite"
  BRANCH_NAME="feat/active-windows"
  BRANCH_MERGE_BACK=false

  local output exit_code=0
  output=$(setup_branch 2>&1) || exit_code=$?

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "feat/active-windows" ]]; then
    pass "Bug #2: checked out the target branch off the slash-named base"
  else
    fail "Bug #2: expected branch 'feat/active-windows', got '$current'" "$output"
  fi

  # The base ref's commit must match origin/codex/convex-rewrite's tip.
  local head_sha base_sha
  head_sha=$(git -C "$CLONE" rev-parse HEAD)
  base_sha=$(git -C "$CLONE" rev-parse origin/codex/convex-rewrite)
  if [[ "$head_sha" == "$base_sha" ]]; then
    pass "Bug #2: new branch points at the slash-named base tip"
  else
    fail "Bug #2: HEAD ($head_sha) != origin/codex/convex-rewrite ($base_sha)" "$output"
  fi

  # The fatal mis-parse symptom must be absent.
  if echo "$output" | grep -q "fetch codex convex-rewrite"; then
    fail "Bug #2: regressed — split on first slash (fetch codex convex-rewrite)" "$output"
  else
    pass "Bug #2: did not mis-split the remote at the first slash"
  fi

  unset ILK_DATA_HOME
}

# ===== Test 9: Bug #2 — bare slash branch resolves as a local ref =====
test_bare_slash_local_ref() {
  echo ""
  echo "--- Test: Bug #2 — bare slash create_from => local ref (no fetch) ---"
  export ILK_DATA_HOME="$(mktemp -d)/ilk-data"
  setup_multimaster_fixture
  source_runner

  # Make a LOCAL branch literally named codex/convex-rewrite (no remote prefix).
  git -C "$CLONE" branch codex/convex-rewrite origin/codex/convex-rewrite >/dev/null 2>&1

  BRANCH_CREATE_FROM="codex/convex-rewrite"
  BRANCH_NAME="feat/from-local-slash"
  BRANCH_MERGE_BACK=false

  local output exit_code=0
  output=$(setup_branch 2>&1) || exit_code=$?

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "feat/from-local-slash" ]]; then
    pass "Bug #2: bare slash ref checked out from local branch (no fatal fetch)"
  else
    fail "Bug #2: expected 'feat/from-local-slash', got '$current'" "$output"
  fi

  if echo "$output" | grep -qi "fetching codex"; then
    fail "Bug #2: regressed — tried to fetch a non-remote 'codex'" "$output"
  else
    pass "Bug #2: skipped fetch for the local slash ref"
  fi

  unset ILK_DATA_HOME
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
  # Bug #1 fix: ps1 must resolve the active master via loop_status --json.
  if ! grep -q "LoopStatusScript --json" "$PS1"; then
    missing+=("LoopStatusScript --json")
  fi
  # Bug #2 fix: ps1 must validate the first segment against configured remotes.
  if ! grep -q "configuredRemotes" "$PS1"; then
    missing+=("configuredRemotes")
  fi

  if [[ ${#missing[@]} -eq 0 ]]; then
    pass "AC-6: ps1 contains create_from, checkout -B, ls-remote, loop_status --json, remote validation"
  else
    fail "AC-6: ps1 missing keywords: ${missing[*]}"
  fi
}

# ===== Test 10: benign post-checkout hook — checkout lands despite hook failure =====
test_postcheckout_hook_tolerance() {
  echo ""
  echo "--- Test: post-checkout hook tolerance (checkout lands despite hook failure) ---"
  setup_repos

  # Advance the remote so there's something to branch from
  echo "second" > "$TEST_TMPDIR/tmp_work/file2.txt"
  git -C "$TEST_TMPDIR/tmp_work" add file2.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "second commit" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  # Install a post-checkout hook that exits non-zero
  mkdir -p "$CLONE/.git/hooks"
  cat > "$CLONE/.git/hooks/post-checkout" <<'HOOKEOF'
#!/bin/sh
echo "post-checkout hook deliberately failing" >&2
exit 1
HOOKEOF
  chmod +x "$CLONE/.git/hooks/post-checkout"

  source_runner

  BRANCH_CREATE_FROM="origin/main"
  BRANCH_NAME="feat/hook-tolerance"
  BRANCH_MERGE_BACK=false

  local output exit_code=0
  output=$(setup_branch 2>&1) || exit_code=$?

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "feat/hook-tolerance" ]]; then
    pass "hook-tolerance: checkout landed on target branch despite hook failure"
  else
    fail "hook-tolerance: expected branch 'feat/hook-tolerance', got '$current'" "$output"
  fi

  if [[ "$exit_code" -eq 0 ]]; then
    pass "hook-tolerance: setup_branch succeeded (did not abort)"
  else
    fail "hook-tolerance: setup_branch should succeed, got exit $exit_code" "$output"
  fi

  if echo "$output" | grep -q "checkout landed despite"; then
    pass "hook-tolerance: warning message emitted"
  else
    fail "hook-tolerance: expected 'checkout landed despite' warning" "$output"
  fi

  # Verify HEAD is at the correct SHA
  local local_sha remote_sha
  local_sha=$(git -C "$CLONE" rev-parse HEAD)
  remote_sha=$(git -C "$CLONE" rev-parse origin/main)
  if [[ "$local_sha" == "$remote_sha" ]]; then
    pass "hook-tolerance: HEAD points to fresh base"
  else
    fail "hook-tolerance: HEAD ($local_sha) != origin/main ($remote_sha)"
  fi
}

# ===== Test 11: genuine checkout failure still aborts =====
test_genuine_checkout_failure() {
  echo ""
  echo "--- Test: genuine checkout failure still aborts ---"
  setup_repos

  # Advance the remote
  echo "second" > "$TEST_TMPDIR/tmp_work/file2.txt"
  git -C "$TEST_TMPDIR/tmp_work" add file2.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "second commit" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  # Install a post-checkout hook that:
  #   1. Switches HEAD back to main (simulating a genuine failure where HEAD didn't land)
  #   2. Exits non-zero
  mkdir -p "$CLONE/.git/hooks"
  cat > "$CLONE/.git/hooks/post-checkout" <<'HOOKEOF'
#!/bin/sh
# Simulate genuine failure: move HEAD away from the target branch
git checkout main 2>/dev/null
exit 1
HOOKEOF
  chmod +x "$CLONE/.git/hooks/post-checkout"

  source_runner

  BRANCH_CREATE_FROM="origin/main"
  BRANCH_NAME="feat/genuine-fail"
  BRANCH_MERGE_BACK=false

  local output exit_code=0
  output=$(setup_branch 2>&1) || exit_code=$?

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  # Should abort — HEAD is NOT on the target branch
  if [[ "$current" == "main" ]]; then
    pass "genuine-fail: stayed on main (aborted)"
  else
    fail "genuine-fail: expected to stay on main, but switched to '$current'" "$output"
  fi

  if [[ "$exit_code" -ne 0 ]]; then
    pass "genuine-fail: setup_branch failed as expected"
  else
    fail "genuine-fail: setup_branch should fail, got exit 0"
  fi

  if echo "$output" | grep -q "Error:.*failed"; then
    pass "genuine-fail: error message emitted"
  else
    fail "genuine-fail: expected error message" "$output"
  fi
}

# ===== Test 12: AC-1 reuse — branch ahead of base → reuses, HEAD at ahead tip =====
test_reuse_ahead_branch() {
  echo ""
  echo "--- Test: AC-1 reuse — branch ahead of base → reuses, HEAD at ahead tip ---"
  setup_repos

  # Advance the remote so there's a base to branch from
  echo "second" > "$TEST_TMPDIR/tmp_work/file2.txt"
  git -C "$TEST_TMPDIR/tmp_work" add file2.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "second commit" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  source_runner

  # Create the branch from origin/main
  BRANCH_CREATE_FROM="origin/main"
  BRANCH_NAME="feat/reuse-test"
  BRANCH_MERGE_BACK=false

  local output
  output=$(setup_branch 2>&1) || true

  # Add a commit AHEAD of base on the branch
  git -C "$CLONE" checkout feat/reuse-test >/dev/null 2>&1
  echo "ahead-commit" > "$CLONE/ahead.txt"
  git -C "$CLONE" add ahead.txt
  git -C "$CLONE" commit -m "commit ahead of base" >/dev/null 2>&1
  local ahead_sha
  ahead_sha=$(git -C "$CLONE" rev-parse HEAD)

  # Switch back to main to simulate a fresh run
  git -C "$CLONE" checkout main >/dev/null 2>&1

  # Now run setup_branch again — should REUSE the existing branch
  local output2 exit_code=0
  output2=$(setup_branch 2>&1) || exit_code=$?

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "feat/reuse-test" ]]; then
    pass "reuse-ahead: landed on existing branch"
  else
    fail "reuse-ahead: expected 'feat/reuse-test', got '$current'" "$output2"
  fi

  # HEAD must be at the AHEAD tip, NOT reset to base
  local post_sha
  post_sha=$(git -C "$CLONE" rev-parse HEAD)
  if [[ "$post_sha" == "$ahead_sha" ]]; then
    pass "reuse-ahead: HEAD at ahead tip (not reset to base)"
  else
    fail "reuse-ahead: HEAD ($post_sha) != ahead tip ($ahead_sha) — was reset?"
  fi

  if echo "$output2" | grep -q "reusing existing branch"; then
    pass "reuse-ahead: 'reusing existing branch' message emitted"
  else
    fail "reuse-ahead: expected reuse message" "$output2"
  fi
}

# ===== Test 13: AC-3 diverged — branch diverged from base → reuse + warn, no loss =====
test_diverged_branch_reuse() {
  echo ""
  echo "--- Test: AC-3 diverged — branch diverged from base → reuse + warn, no loss ---"
  setup_repos

  # Push a second commit to remote
  echo "second" > "$TEST_TMPDIR/tmp_work/file2.txt"
  git -C "$TEST_TMPDIR/tmp_work" add file2.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "second commit" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  source_runner

  # Create the branch from origin/main
  BRANCH_CREATE_FROM="origin/main"
  BRANCH_NAME="feat/diverge-test"
  BRANCH_MERGE_BACK=false

  local output
  output=$(setup_branch 2>&1) || true

  # Add a LOCAL commit on the branch (diverges from base)
  git -C "$CLONE" checkout feat/diverge-test >/dev/null 2>&1
  echo "local-only" > "$CLONE/local.txt"
  git -C "$CLONE" add local.txt
  git -C "$CLONE" commit -m "local commit diverging from base" >/dev/null 2>&1
  local diverged_sha
  diverged_sha=$(git -C "$CLONE" rev-parse HEAD)

  # Advance the remote base PAST the branch's ancestor (diverge)
  echo "third" > "$TEST_TMPDIR/tmp_work/file3.txt"
  git -C "$TEST_TMPDIR/tmp_work" add file3.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "third commit — moves base" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  # Switch back to main to simulate a fresh run
  git -C "$CLONE" checkout main >/dev/null 2>&1

  # Now run setup_branch — should REUSE the diverged branch (not abort)
  local output2 exit_code=0
  output2=$(setup_branch 2>&1) || exit_code=$?

  # Should succeed (reuse, not abort)
  if [[ "$exit_code" -eq 0 ]]; then
    pass "diverge-reuse: setup_branch succeeded (reused diverged branch)"
  else
    fail "diverge-reuse: setup_branch should succeed on diverged branch, got exit $exit_code" "$output2"
  fi

  # Should be on the diverged branch
  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)
  if [[ "$current" == "feat/diverge-test" ]]; then
    pass "diverge-reuse: landed on diverged branch"
  else
    fail "diverge-reuse: expected 'feat/diverge-test', got '$current'" "$output2"
  fi

  # HEAD must be at the diverged tip (no reset, no commit loss)
  local post_sha
  post_sha=$(git -C "$CLONE" rev-parse HEAD)
  if [[ "$post_sha" == "$diverged_sha" ]]; then
    pass "diverge-reuse: HEAD at diverged tip (no reset)"
  else
    fail "diverge-reuse: HEAD ($post_sha) != diverged tip ($diverged_sha) — was reset?"
  fi

  # Branch ref must NOT have moved (no commit loss)
  local branch_sha
  branch_sha=$(git -C "$CLONE" rev-parse feat/diverge-test 2>/dev/null) || branch_sha=""
  if [[ "$branch_sha" == "$diverged_sha" ]]; then
    pass "diverge-reuse: branch ref unchanged (no commit loss)"
  else
    fail "diverge-reuse: branch ref moved from $diverged_sha to $branch_sha"
  fi

  # Warning must mention "reusing diverged branch"
  if echo "$output2" | grep -q "reusing diverged branch"; then
    pass "diverge-reuse: warning mentions 'reusing diverged branch'"
  else
    fail "diverge-reuse: expected 'reusing diverged branch' warning" "$output2"
  fi

  # Warning should include ahead/behind counts
  if echo "$output2" | grep -q "ahead.*behind"; then
    pass "diverge-reuse: warning includes ahead/behind counts"
  else
    fail "diverge-reuse: warning should include ahead/behind counts" "$output2"
  fi

  # Should NOT abort or mention "reconcile manually"
  if echo "$output2" | grep -qi "abort\|reconcile manually"; then
    fail "diverge-reuse: should not abort or mention 'reconcile manually'" "$output2"
  else
    pass "diverge-reuse: no abort or 'reconcile manually' in output"
  fi
}

# ===== Test 14: AC-4 resume-dirty — already on target ahead + dirty tree → succeeds =====
test_resume_dirty_on_target() {
  echo ""
  echo "--- Test: AC-4 resume-dirty — already on target ahead + dirty tree → succeeds ---"
  setup_repos

  # Push a second commit to remote
  echo "second" > "$TEST_TMPDIR/tmp_work/file2.txt"
  git -C "$TEST_TMPDIR/tmp_work" add file2.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "second commit" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin main >/dev/null 2>&1
  git -C "$CLONE" fetch origin >/dev/null 2>&1

  source_runner

  # Create the branch from origin/main
  BRANCH_CREATE_FROM="origin/main"
  BRANCH_NAME="feat/dirty-resume"
  BRANCH_MERGE_BACK=false

  local output
  output=$(setup_branch 2>&1) || true

  # Add a commit AHEAD of base
  git -C "$CLONE" checkout feat/dirty-resume >/dev/null 2>&1
  echo "ahead-commit" > "$CLONE/ahead.txt"
  git -C "$CLONE" add ahead.txt
  git -C "$CLONE" commit -m "commit ahead of base" >/dev/null 2>&1
  local ahead_sha
  ahead_sha=$(git -C "$CLONE" rev-parse HEAD)

  # Make the tree DIRTY (simulate uncommitted WIP)
  echo "dirty-wip" > "$CLONE/wip.txt"

  # Repo is already on feat/dirty-resume, ahead of base, with dirty tree.
  # setup_branch should succeed (AC-4: resume-with-dirty-tree).
  local output2 exit_code=0
  output2=$(setup_branch 2>&1) || exit_code=$?

  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "feat/dirty-resume" ]]; then
    pass "resume-dirty: stayed on target branch"
  else
    fail "resume-dirty: expected 'feat/dirty-resume', got '$current'" "$output2"
  fi

  if [[ "$exit_code" -eq 0 ]]; then
    pass "resume-dirty: setup_branch succeeded with dirty tree"
  else
    fail "resume-dirty: setup_branch should succeed, got exit $exit_code" "$output2"
  fi

  # HEAD must still be at the ahead tip (not reset)
  local post_sha
  post_sha=$(git -C "$CLONE" rev-parse HEAD)
  if [[ "$post_sha" == "$ahead_sha" ]]; then
    pass "resume-dirty: HEAD at ahead tip (not reset)"
  else
    fail "resume-dirty: HEAD ($post_sha) != ahead tip ($ahead_sha)"
  fi

  if echo "$output2" | grep -q "reusing existing branch"; then
    pass "resume-dirty: reuse message emitted"
  else
    fail "resume-dirty: expected reuse message" "$output2"
  fi
}

# ===== Test 15: narrow remote.origin.fetch — explicit refspec updates tracking ref =====
test_narrow_refspec_base_fetch() {
  echo ""
  echo "--- Test: narrow remote.origin.fetch — explicit refspec updates tracking ref ---"
  setup_repos

  # Create a non-main branch on the remote and advance it.
  git -C "$TEST_TMPDIR/tmp_work" checkout -b feature-base >/dev/null 2>&1
  echo "feature-content" > "$TEST_TMPDIR/tmp_work/feature.txt"
  git -C "$TEST_TMPDIR/tmp_work" add feature.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "feature base commit" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin feature-base >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" checkout main >/dev/null 2>&1

  # Narrow the clone's remote.origin.fetch to main-only.
  git -C "$CLONE" config remote.origin.fetch "+refs/heads/main:refs/remotes/origin/main"

  # Clone now has NO tracking ref for feature-base.
  # Advance the remote feature-base so there's a freshness mismatch to detect.
  git -C "$TEST_TMPDIR/tmp_work" checkout feature-base >/dev/null 2>&1
  echo "more-content" > "$TEST_TMPDIR/tmp_work/more.txt"
  git -C "$TEST_TMPDIR/tmp_work" add more.txt
  git -C "$TEST_TMPDIR/tmp_work" commit -m "advance feature-base" >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" push origin feature-base >/dev/null 2>&1
  git -C "$TEST_TMPDIR/tmp_work" checkout main >/dev/null 2>&1

  # Get the true remote tip for feature-base.
  local expected_sha
  expected_sha=$(git -C "$TEST_TMPDIR/tmp_work" rev-parse feature-base)

  source_runner

  # Drive setup_branch targeting the non-main branch.
  BRANCH_CREATE_FROM="origin/feature-base"
  BRANCH_NAME="feat/narrow-refspec-test"
  BRANCH_MERGE_BACK=false

  local output exit_code=0
  output=$(setup_branch 2>&1) || exit_code=$?

  # AC-1: the local tracking ref for feature-base must equal the remote tip.
  local tracking_sha
  tracking_sha=$(git -C "$CLONE" rev-parse "refs/remotes/origin/feature-base" 2>/dev/null) || tracking_sha=""

  if [[ "$tracking_sha" == "$expected_sha" ]]; then
    pass "narrow-refspec: tracking ref updated to remote tip despite narrow fetch config"
  else
    fail "narrow-refspec: tracking ref ($tracking_sha) != remote tip ($expected_sha)" "$output"
  fi

  # AC-2: branch setup must have succeeded (freshness check passed).
  local current
  current=$(git -C "$CLONE" branch --show-current 2>/dev/null)

  if [[ "$current" == "feat/narrow-refspec-test" ]]; then
    pass "narrow-refspec: freshness check passed, branch created"
  else
    fail "narrow-refspec: expected branch 'feat/narrow-refspec-test', got '$current'" "$output"
  fi

  # HEAD must point to the remote tip.
  local head_sha
  head_sha=$(git -C "$CLONE" rev-parse HEAD)
  if [[ "$head_sha" == "$expected_sha" ]]; then
    pass "narrow-refspec: HEAD at remote tip"
  else
    fail "narrow-refspec: HEAD ($head_sha) != remote tip ($expected_sha)"
  fi

  # The "base-ref freshness check failed" error must be absent.
  if echo "$output" | grep -q "base-ref freshness check failed"; then
    fail "narrow-refspec: regressed — freshness check failed" "$output"
  else
    pass "narrow-refspec: no freshness-check failure"
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
test_active_master_selection
test_slash_branch_name
test_bare_slash_local_ref
test_ps1_structural
test_postcheckout_hook_tolerance
test_genuine_checkout_failure
test_reuse_ahead_branch
test_diverged_branch_reuse
test_resume_dirty_on_target
test_narrow_refspec_base_fetch

echo ""
echo "=== Results: $PASS passed, $FAIL failed, $TESTS_RUN total ==="

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
