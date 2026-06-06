#!/usr/bin/env bash
# Tests for _worker_session.sh — AC-1, AC-2, AC-3 plus extras.
# Exit 0 on success, 1 on failure. No external deps beyond coreutils.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/../_worker_session.sh"

if [[ ! -f "$HELPER" ]]; then
  echo "FAIL: helper not found at $HELPER" >&2
  exit 1
fi

. "$HELPER"

pass=0
fail=0

assert_false() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "  FAIL: $label — expected false, got true"
    fail=$((fail + 1))
  else
    echo "  PASS: $label"
    pass=$((pass + 1))
  fi
}

assert_true() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected true, got false"
    fail=$((fail + 1))
  fi
}

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# --- AC-1: dead PID -> false ---
echo "=== AC-1: dead PID -> false ==="
printf "pid=999999\nstart=2000-01-01T00:00:00.0000000Z\nkind=claude-worker\n" > "$tmpdir/sentinel.pid"
assert_false "dead PID returns false" worker_session_active "$tmpdir/sentinel.pid"

# --- AC-2: reused PID (wrong start time) -> false ---
echo "=== AC-2: reused PID (wrong start time) -> false ==="
my_pid=$$
my_start="$(ps -o lstart= -p "$my_pid" 2>/dev/null || true)"
my_start="$(echo "$my_start" | xargs)"
printf "pid=%s\nstart=bogus-start-time\nkind=claude-worker\n" "$my_pid" > "$tmpdir/sentinel.pid"
assert_false "reused PID with wrong start time returns false" worker_session_active "$tmpdir/sentinel.pid"

# --- AC-3: matching PID + start time -> true ---
echo "=== AC-3: matching PID + start time -> true ==="
printf "pid=%s\nstart=%s\nkind=claude-worker\n" "$my_pid" "$my_start" > "$tmpdir/sentinel.pid"
assert_true "matching PID + start time returns true" worker_session_active "$tmpdir/sentinel.pid"

# --- Additional: missing file -> false ---
echo "=== Additional: missing file -> false ==="
rm -f "$tmpdir/missing.pid"
assert_false "missing file returns false" worker_session_active "$tmpdir/missing.pid"

# --- Additional: legacy bare-integer (alive) -> true ---
echo "=== Additional: legacy bare-integer (alive) -> true ==="
echo "$my_pid" > "$tmpdir/legacy.pid"
assert_true "legacy bare-integer (alive PID) returns true" worker_session_active "$tmpdir/legacy.pid"

# --- Additional: legacy bare-integer (dead) -> false ---
echo "=== Additional: legacy bare-integer (dead) -> false ==="
echo "999999" > "$tmpdir/legacy_dead.pid"
assert_false "legacy bare-integer (dead PID) returns false" worker_session_active "$tmpdir/legacy_dead.pid"

# --- Additional: worker_sentinel_remove idempotent ---
echo "=== Additional: worker_sentinel_remove idempotent ==="
echo "test" > "$tmpdir/remove_test.pid"
worker_sentinel_remove "$tmpdir/remove_test.pid"
if [[ ! -f "$tmpdir/remove_test.pid" ]]; then
  echo "  PASS: worker_sentinel_remove removes file"
  ((pass++))
else
  echo "  FAIL: worker_sentinel_remove did not remove file"
  ((fail++))
fi
worker_sentinel_remove "$tmpdir/remove_test.pid"
echo "  PASS: worker_sentinel_remove idempotent (no error on second call)"
((pass++))

# --- Results ---
echo
echo "=== Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
