#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# test_runner_exit_sentinel.sh — tests for finalize_sentinel (EXIT trap)
# =============================================================================
# Verifies that the runner's EXIT trap rewrites a stale state=running
# sentinel to a terminal state, and preserves clean terminal states.
#
# AC coverage:
#   AC-1: abnormal exit → terminal (state=running → interrupted, pid null,
#         stopped_reason present)
#   AC-2: clean exit preserved (all-shipped is NOT overwritten)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNNER="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"

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

assert_json_field() {
  local file="$1"
  local field="$2"
  local expected="$3"
  local msg="$4"
  local actual
  actual=$(python3 -c "import json; print(json.load(open('$file')).get('$field',''))" 2>/dev/null) || actual=""
  if [[ "$actual" == "$expected" ]]; then
    pass "$msg"
  else
    fail "$msg" "expected '$expected', got '$actual'"
  fi
}

assert_json_null() {
  local file="$1"
  local field="$2"
  local msg="$3"
  local is_null
  is_null=$(python3 -c "import json; v=json.load(open('$file')).get('$field','__MISSING__'); print('null' if v is None else 'not-null')" 2>/dev/null) || is_null="error"
  if [[ "$is_null" == "null" ]]; then
    pass "$msg"
  else
    fail "$msg" "expected null, got $is_null"
  fi
}

# ----- Test setup --------------------------------------------------------

TEST_TMPDIR=$(mktemp -d)

# Source the runner to get finalize_sentinel without running main()
ILK_DOTSOURCE_ONLY=1 source "$RUNNER"

# Set globals the function needs
RUN_ID="test-sentinel-run"
PROJECT_PATH="/tmp/test-sentinel"

# ----- Test 1: running sentinel → interrupted ----------------------------

echo "Test 1: running sentinel is finalized to interrupted"

runtime_dir="$TEST_TMPDIR/run1"
mkdir -p "$runtime_dir"
cat > "$runtime_dir/last-exit.json" <<'EOF'
{"state":"running","pid":12345,"run_id":"test-run","started_at":"2026-06-16T00:00:00+0800","project_path":"/tmp/test","cli":"claude"}
EOF

finalize_sentinel

assert_json_field "$runtime_dir/last-exit.json" "state" "interrupted" \
  "state changed from running to interrupted"
assert_json_null "$runtime_dir/last-exit.json" "pid" \
  "pid is null after finalization"
assert_json_field "$runtime_dir/last-exit.json" "stopped_reason" "runner exited without a terminal state" \
  "stopped_reason is set"

# ----- Test 2: all-shipped sentinel is NOT overwritten -------------------

echo "Test 2: all-shipped sentinel is preserved"

runtime_dir="$TEST_TMPDIR/run2"
mkdir -p "$runtime_dir"
cat > "$runtime_dir/last-exit.json" <<'EOF'
{"state":"all-shipped","pid":99999,"run_id":"test-run","started_at":"2026-06-16T00:00:00+0800","ended_at":"2026-06-16T01:00:00+0800","iterations":5,"project_path":"/tmp/test","cli":"claude"}
EOF

finalize_sentinel

assert_json_field "$runtime_dir/last-exit.json" "state" "all-shipped" \
  "all-shipped state preserved (not overwritten)"
assert_json_field "$runtime_dir/last-exit.json" "pid" "99999" \
  "pid preserved on all-shipped"

# ----- Test 3: idempotent — second call is no-op -------------------------

echo "Test 3: finalize_sentinel is idempotent"

# Use run1 (already finalized to interrupted)
runtime_dir="$TEST_TMPDIR/run1"
finalize_sentinel

assert_json_field "$runtime_dir/last-exit.json" "state" "interrupted" \
  "second call still shows interrupted (no-op)"

# ----- Test 4: missing sentinel file — no error --------------------------

echo "Test 4: missing sentinel file is handled gracefully"

runtime_dir="$TEST_TMPDIR/run3"
mkdir -p "$runtime_dir"
# No last-exit.json written

finalize_sentinel  # should not error

pass "missing sentinel file handled without error"

# ----- Test 5: no runtime_dir — no error ---------------------------------

echo "Test 5: empty runtime_dir is handled gracefully"

runtime_dir=""
finalize_sentinel  # should not error

pass "empty runtime_dir handled without error"

# ----- Report ------------------------------------------------------------

echo ""
echo "=== test_runner_exit_sentinel ==="
echo "Tests run: $TESTS_RUN"
echo "Passed:    $PASS"
echo "Failed:    $FAIL"

if [[ "$FAIL" -gt 0 ]]; then
  echo "FAILED"
  exit 1
fi
echo "ALL PASSED"
exit 0
