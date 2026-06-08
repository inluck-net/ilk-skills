#!/usr/bin/env bash
# test_gate_runner.sh — tests for Invoke-LocalCheck bash parity
#
# Validates AC-2 (bash invocation) and AC-3 (error classification):
#   (a) grep -q against a file that contains the pattern → pass
#   (b) grep -q against a file lacking it               → fail
#   (c) bogus command (definitely-not-a-cmd)             → error
#
# Run: bash test_gate_runner.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_SCRIPT="$SCRIPT_DIR/../scripts/run_ilk_loop_claude.sh"

FAIL_COUNT=0

assert_equal() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" != "$actual" ]]; then
    echo "  FAIL: $label — expected '$expected', got '$actual'"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  else
    echo "  OK: $label"
  fi
}

# --- Setup ------------------------------------------------------------------

# Create temp fixtures
TMPDIR_TEST=$(mktemp -d)
MATCH_FILE="$TMPDIR_TEST/match.txt"
NOMATCH_FILE="$TMPDIR_TEST/nomatch.txt"
echo "hello world" > "$MATCH_FILE"
echo "goodbye moon" > "$NOMATCH_FILE"

# --- Test via run_local_checks.py ------------------------------------------
# The bash runner uses run_local_checks.py to execute local_checks.

# Find python command (python3 on Unix, python on Windows)
# On Windows, python3 might be an App Execution Alias that doesn't work
PYTHON_CMD="python3"
if ! python3 --version &>/dev/null; then
  PYTHON_CMD="python"
fi

HELPER_SCRIPT="$SCRIPT_DIR/../scripts/run_local_checks.py"

if [[ ! -f "$HELPER_SCRIPT" ]]; then
  echo "ERROR: run_local_checks.py not found at $HELPER_SCRIPT"
  exit 1
fi

echo ""
echo "=== AC-2: bash invocation (grep runs, not cmd.exe) ==="

# Create a temporary project structure
PROJECT_DIR="$TMPDIR_TEST/project"
PLANS_DIR="$PROJECT_DIR/docs/plans"
mkdir -p "$PLANS_DIR"
touch "$PLANS_DIR/MASTER-test.md"

# (a) grep -q against file that contains the pattern → pass
cat > "$PLANS_DIR/test-bash-parity.md" << 'EOF'
---
plan: test-bash-parity
status: in-progress
current_step: 0
---

# Test sub-plan for bash parity

### Step 0 — test grep
```yaml
local_checks:
  - command: grep -q 'hello' "MATCH_FILE_PLACEHOLDER"
    timeout: 10
```
EOF
sed -i "s|MATCH_FILE_PLACEHOLDER|$MATCH_FILE|" "$PLANS_DIR/test-bash-parity.md"

RESULT_FILE="$TMPDIR_TEST/result.json"
$PYTHON_CMD "$HELPER_SCRIPT" --project "$PROJECT_DIR" --slug test-bash-parity --step 0 > "$RESULT_FILE" 2>&1 || true
# Convert bash-style path to Windows-style for Python
WIN_RESULT_FILE=$(cygpath -w "$RESULT_FILE" 2>/dev/null || echo "$RESULT_FILE")
# Write a temporary Python script to extract the result
EXTRACT_SCRIPT="$TMPDIR_TEST/extract.py"
cat > "$EXTRACT_SCRIPT" << 'PYEOF'
import json
import sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print(d['results'][0]['passed'])
PYEOF
PASSED=$($PYTHON_CMD "$EXTRACT_SCRIPT" "$WIN_RESULT_FILE" 2>&1) || PASSED="error"

if [[ "$PASSED" == "True" ]]; then
  echo "  OK: (a) grep match → passed=True"
else
  echo "  FAIL: (a) grep match → expected True, got $PASSED"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# (b) grep -q against file that lacks the pattern → fail
cat > "$PLANS_DIR/test-bash-parity.md" << 'EOF'
---
plan: test-bash-parity
status: in-progress
current_step: 0
---

# Test sub-plan for bash parity

### Step 0 — test grep
```yaml
local_checks:
  - command: grep -q 'hello' "NOMATCH_FILE_PLACEHOLDER"
    timeout: 10
```
EOF
sed -i "s|NOMATCH_FILE_PLACEHOLDER|$NOMATCH_FILE|" "$PLANS_DIR/test-bash-parity.md"

$PYTHON_CMD "$HELPER_SCRIPT" --project "$PROJECT_DIR" --slug test-bash-parity --step 0 > "$RESULT_FILE" 2>&1 || true
WIN_RESULT_FILE=$(cygpath -w "$RESULT_FILE" 2>/dev/null || echo "$RESULT_FILE")
PASSED=$($PYTHON_CMD "$EXTRACT_SCRIPT" "$WIN_RESULT_FILE" 2>&1) || PASSED="error"

if [[ "$PASSED" == "False" ]]; then
  echo "  OK: (b) grep no-match → passed=False (blocks)"
else
  echo "  FAIL: (b) grep no-match → expected False, got $PASSED"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

echo ""
echo "=== AC-3: error blocks (unrunnable command → error, not pass) ==="

# (c) bogus command → error
cat > "$PLANS_DIR/test-bash-parity.md" << 'EOF'
---
plan: test-bash-parity
status: in-progress
current_step: 0
---

# Test sub-plan for bash parity

### Step 0 — test grep
```yaml
local_checks:
  - command: definitely-not-a-cmd-xyzzy
    timeout: 10
```
EOF

$PYTHON_CMD "$HELPER_SCRIPT" --project "$PROJECT_DIR" --slug test-bash-parity --step 0 > "$RESULT_FILE" 2>&1 || true
WIN_RESULT_FILE=$(cygpath -w "$RESULT_FILE" 2>/dev/null || echo "$RESULT_FILE")
# Write a different extraction script for error detection
# On Windows, a command not found returns exit code 1 (same as test failure),
# so we check stderr for error messages instead of exit code.
EXTRACT_ERROR_SCRIPT="$TMPDIR_TEST/extract_error.py"
cat > "$EXTRACT_ERROR_SCRIPT" << 'PYEOF'
import json
import sys
with open(sys.argv[1]) as f:
    d = json.load(f)
r = d['results'][0]
# A command that can't execute will have passed=False and stderr will contain
# an error message (not empty). A test failure (grep no match) will have
# passed=False but stderr may be empty or contain the command's own output.
is_error = not r['passed'] and (r.get('error') or r.get('stderr_tail'))
print('error' if is_error else 'other')
PYEOF
PASSED=$($PYTHON_CMD "$EXTRACT_ERROR_SCRIPT" "$WIN_RESULT_FILE" 2>&1) || PASSED="error"

if [[ "$PASSED" == "error" ]]; then
  echo "  OK: (c) bogus cmd → error (blocks, NOT pass)"
else
  echo "  FAIL: (c) bogus cmd → expected error, got $PASSED"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# --- Cleanup ----------------------------------------------------------------

rm -rf "$TMPDIR_TEST"

# --- Summary ----------------------------------------------------------------

echo ""
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo "FAILED: $FAIL_COUNT assertion(s) failed"
  exit 1
else
  echo "ALL PASSED"
  exit 0
fi
