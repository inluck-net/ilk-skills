#!/usr/bin/env bash
# Tests for claude-worker.sh preflight — AC-4, AC-5, AC-6.
# Validates that a home missing commands/ilk.md is refused, and that
# existing provider-env checks are unaffected.
# Exit 0 on success, 1 on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
WORKER_SCRIPT="$SCRIPT_DIR/../claude-worker.sh"

if [[ ! -f "$WORKER_SCRIPT" ]]; then
  echo "FAIL: claude-worker.sh not found at $WORKER_SCRIPT" >&2
  exit 1
fi

pass=0
fail=0

assert_exit_code() {
  local label="$1" expected="$2"
  shift 2
  set +e
  output="$("$@" 2>&1)"
  rc=$?
  set -e
  if [[ $rc -eq "$expected" ]]; then
    echo "  PASS: $label (exit $rc)"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected exit $expected, got $rc"
    echo "  output: $output"
    fail=$((fail + 1))
  fi
}

assert_output_contains() {
  local label="$1" needle="$2"
  shift 2
  set +e
  output="$("$@" 2>&1)"
  rc=$?
  set -e
  if [[ "$output" == *"$needle"* ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — output does not contain '$needle'"
    echo "  output: $output"
    fail=$((fail + 1))
  fi
}

# --- Setup: fake worker home under repo-local scratch ---
FAKE_HOME="$REPO_ROOT/scratch/preflight-test/home"
rm -rf "$REPO_ROOT/scratch/preflight-test"
mkdir -p "$FAKE_HOME/skills/ilk-runner"
mkdir -p "$FAKE_HOME/commands"

# Write a minimal settings.json with all required env vars.
cat > "$FAKE_HOME/settings.json" <<'EOF'
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://test.example.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "test-token-12345",
    "ANTHROPIC_MODEL": "test-model-v1"
  }
}
EOF

# Write a minimal .claude.json.
cat > "$FAKE_HOME/.claude.json" <<'EOF'
{
  "mcpServers": {}
}
EOF

# Write dummy skill and command files.
echo "skill-content" > "$FAKE_HOME/skills/ilk-runner/SKILL.md"
echo "# /ilk" > "$FAKE_HOME/commands/ilk.md"

cleanup() {
  rm -rf "$REPO_ROOT/scratch/preflight-test"
}
trap cleanup EXIT

# === Test 1: Complete home passes preflight (AC-5) ===
echo "=== Test 1: Complete home passes preflight ==="

assert_exit_code "complete home → exit 0" 0 \
  bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# === Test 2: Missing commands/ilk.md fails, names the file (AC-4) ===
echo ""
echo "=== Test 2: Missing commands/ilk.md fails ==="

mv "$FAKE_HOME/commands/ilk.md" "$FAKE_HOME/commands/ilk.md.bak"

assert_output_contains "missing commands/ilk.md → names 'commands/ilk.md'" \
  "commands/ilk.md" \
  bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

assert_exit_code "missing commands/ilk.md → exit 3" 3 \
  bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# Restore.
mv "$FAKE_HOME/commands/ilk.md.bak" "$FAKE_HOME/commands/ilk.md"

# === Test 3: No commands/ directory at all ===
echo ""
echo "=== Test 3: No commands/ directory at all ==="

mv "$FAKE_HOME/commands" "$FAKE_HOME/commands.bak"

assert_output_contains "no commands/ dir → names 'commands/ilk.md'" \
  "commands/ilk.md" \
  bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

assert_exit_code "no commands/ dir → exit 3" 3 \
  bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# Restore.
mv "$FAKE_HOME/commands.bak" "$FAKE_HOME/commands"

# === Test 4: Missing ilk-runner still fails (AC-5: existing check unaffected) ===
echo ""
echo "=== Test 4: Missing ilk-runner still detected ==="

mv "$FAKE_HOME/skills/ilk-runner" "$FAKE_HOME/skills/ilk-runner.bak"

assert_output_contains "missing ilk-runner → names 'ilk-runner'" \
  "ilk-runner" \
  bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# Restore.
mv "$FAKE_HOME/skills/ilk-runner.bak" "$FAKE_HOME/skills/ilk-runner"

# === Test 5: Missing ANTHROPIC_BASE_URL still fails (AC-5) ===
echo ""
echo "=== Test 5: Missing ANTHROPIC_BASE_URL still detected ==="

cp "$FAKE_HOME/settings.json" "$FAKE_HOME/settings.json.bak"
cat > "$FAKE_HOME/settings.json" <<'EOF'
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "test-token-12345",
    "ANTHROPIC_MODEL": "test-model-v1"
  }
}
EOF

assert_output_contains "missing BASE_URL → names 'ANTHROPIC_BASE_URL'" \
  "ANTHROPIC_BASE_URL" \
  bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# Restore.
mv "$FAKE_HOME/settings.json.bak" "$FAKE_HOME/settings.json"

# === Results ===
echo ""
echo "=== Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
