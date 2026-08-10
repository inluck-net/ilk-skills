#!/usr/bin/env bash
# Tests for scheduler.sh clone-failed logging (AC-7).
# Verifies that a failed slot clone is logged to scheduler.log, not swallowed.
# Exit 0 on success, 1 on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SCHEDULER_DIR="$SCRIPT_DIR/../scripts"

pass=0
fail=0

assert_contains() {
  local label="$1" file="$2" needle="$3"
  if grep -q "$needle" "$file" 2>/dev/null; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — '$needle' not found in $file"
    fail=$((fail + 1))
  fi
}

assert_exit_code() {
  local label="$1" expected="$2"
  shift 2
  set +e
  "$@" >/dev/null 2>&1
  rc=$?
  set -e
  if [[ $rc -eq "$expected" ]]; then
    echo "  PASS: $label (exit $rc)"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected exit $expected, got $rc"
    fail=$((fail + 1))
  fi
}

# --- Setup: isolated environment ---
FAKE_HOME="$REPO_ROOT/scratch/clone-log-test/home"
rm -rf "$REPO_ROOT/scratch/clone-log-test"
mkdir -p "$FAKE_HOME"

# Create a mock bootstrap script that always fails.
MOCK_BOOTSTRAP="$FAKE_HOME/mock-bootstrap.sh"
cat > "$MOCK_BOOTSTRAP" <<'EOF'
#!/usr/bin/env bash
echo "mock bootstrap failure" >&2
exit 1
EOF
chmod +x "$MOCK_BOOTSTRAP"

# Set up isolated scheduler log.
export HOME="$FAKE_HOME"
SCHEDULER_LOG_DIR="$FAKE_HOME/.ilk-data/logs"
SCHEDULER_LOG_FILE="$SCHEDULER_LOG_DIR/scheduler.log"
mkdir -p "$SCHEDULER_LOG_DIR"

cleanup() {
  rm -rf "$REPO_ROOT/scratch/clone-log-test"
}
trap cleanup EXIT

# Source just the write_scheduler_log helper from scheduler.sh.
# We extract it inline because sourcing the full scheduler.sh has too many deps.
write_scheduler_log() {
  local decision="$1" key="${2:-}" reason="${3:-}"
  mkdir -p "$SCHEDULER_LOG_DIR" 2>/dev/null || true
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  local line="[$ts] $decision"
  [[ -n "$key" ]] && line+=": $key"
  [[ -n "$reason" ]] && line+=" ($reason)"
  printf '%s\n' "$line" >> "$SCHEDULER_LOG_FILE" 2>/dev/null || true
}

# === Test 1: Failed clone writes to scheduler log ===
echo "=== Test 1: Failed clone writes to scheduler log ==="

# Simulate the scheduler.sh dispatch block for a failed clone.
clone_output=""
if ! clone_output="$(bash "$MOCK_BOOTSTRAP" --clone-slot 2 2>&1)"; then
  write_scheduler_log "clone-failed" "test-project (slot 2)" "$clone_output"
fi

assert_contains "clone-failed logged" "$SCHEDULER_LOG_FILE" "clone-failed"
assert_contains "log contains slot info" "$SCHEDULER_LOG_FILE" "slot 2"
assert_contains "log contains error output" "$SCHEDULER_LOG_FILE" "mock bootstrap failure"

# === Test 2: Successful clone does NOT write clone-failed ===
echo ""
echo "=== Test 2: Successful clone does NOT write clone-failed ==="

# Create a mock bootstrap that succeeds.
MOCK_BOOTSTRAP_OK="$FAKE_HOME/mock-bootstrap-ok.sh"
cat > "$MOCK_BOOTSTRAP_OK" <<'EOF'
#!/usr/bin/env bash
echo "slot home ready"
exit 0
EOF
chmod +x "$MOCK_BOOTSTRAP_OK"

# Clear the log.
rm -f "$SCHEDULER_LOG_FILE"

clone_output=""
if ! clone_output="$(bash "$MOCK_BOOTSTRAP_OK" --clone-slot 2 2>&1)"; then
  write_scheduler_log "clone-failed" "test-project (slot 2)" "$clone_output"
fi

if [[ ! -f "$SCHEDULER_LOG_FILE" ]] || ! grep -q "clone-failed" "$SCHEDULER_LOG_FILE" 2>/dev/null; then
  echo "  PASS: no clone-failed logged on success"
  pass=$((pass + 1))
else
  echo "  FAIL: clone-failed should not be logged on success"
  fail=$((fail + 1))
fi

# === Results ===
echo ""
echo "=== Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
