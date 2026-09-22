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

# === Test 6: a registry-declared official home needs NO provider env (AC1) ===
# The manager role runs on the official OAuth account deliberately, so the
# three provider-env checks must not refuse the configuration it is meant to
# have — while an unregistered env-less home (Test 5) still fails closed.
echo ""
echo "=== Test 6: registry-declared official home passes with no provider env ==="

REGISTRY="$REPO_ROOT/scratch/preflight-test/role-registry.json"
cat > "$REGISTRY" <<REG
{"version": 1, "roles": {
  "manager": {"tier": "manager", "home": "$FAKE_HOME",
              "provider": "Claude Official", "model": "opus",
              "auth": "official"}
}}
REG

cp "$FAKE_HOME/settings.json" "$FAKE_HOME/settings.json.bak"
cat > "$FAKE_HOME/settings.json" <<'SET'
{
  "env": {},
  "model": "opus"
}
SET

assert_exit_code "official home, no provider env -> exit 0" 0 \
  env ILK_ROLE_REGISTRY="$REGISTRY" bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

assert_output_contains "official home -> names the identity" \
  "Claude Official" \
  env ILK_ROLE_REGISTRY="$REGISTRY" bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# === Test 7: official home that still carries a provider env is refused (AC3) ===
# A leftover base url means the home is still on a third-party provider while
# the registry claims official — the silent-identity bug pointing the other way.
echo ""
echo "=== Test 7: official home with a leftover provider env is refused ==="

cat > "$FAKE_HOME/settings.json" <<'SET'
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://leftover.example.com/anthropic"
  },
  "model": "opus"
}
SET

assert_exit_code "official home + leftover BASE_URL -> exit 3" 3 \
  env ILK_ROLE_REGISTRY="$REGISTRY" bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

assert_output_contains "official home + leftover BASE_URL -> names auth=official" \
  "auth=official" \
  env ILK_ROLE_REGISTRY="$REGISTRY" bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# Restore.
mv "$FAKE_HOME/settings.json.bak" "$FAKE_HOME/settings.json"

# === Test 8: post-switch provider-backed manager pair is accepted (AC-7) ===
# The state `ilk-worker-model use --provider ...` leaves behind: the registry
# row names the provider and carries NO auth key, and the home has the full
# provider env.  claude-worker.sh must accept that pair — this is the shape
# the switch tool's success path produces, and Test 7 above already covers
# the disagreement shape it must never produce.
echo ""
echo "=== Test 8: post-switch provider-backed manager pair is accepted ==="

cat > "$REGISTRY" <<REG
{"version": 1, "roles": {
  "manager": {"tier": "manager", "home": "$FAKE_HOME",
              "provider": "Zhipu GLM", "model": "glm-5.3"}
}}
REG

cat > "$FAKE_HOME/settings.json" <<'SET'
{
  "model": "glm-5.3",
  "env": {
    "ANTHROPIC_BASE_URL": "https://glm.example/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "tok-glm",
    "ANTHROPIC_MODEL": "glm-5.3"
  }
}
SET

assert_exit_code "post-switch manager pair -> exit 0" 0 \
  env ILK_ROLE_REGISTRY="$REGISTRY" bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

assert_output_contains "post-switch manager pair -> Preflight OK" \
  "Preflight OK" \
  env ILK_ROLE_REGISTRY="$REGISTRY" bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# Same pair, but the registry still claims auth=official — the D1 leftover.
# Must land in the inverted branch and be refused (exit 3), which is what
# makes the exit 0 above a real constraint and not a tautology.
echo ""
echo "=== Test 9: the same home with a stale auth=official claim is refused ==="

cat > "$REGISTRY" <<REG
{"version": 1, "roles": {
  "manager": {"tier": "manager", "home": "$FAKE_HOME",
              "provider": "Zhipu GLM", "model": "glm-5.3",
              "auth": "official"}
}}
REG

assert_exit_code "stale auth=official over a provider env -> exit 3" 3 \
  env ILK_ROLE_REGISTRY="$REGISTRY" bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

assert_output_contains "stale auth=official -> names auth=official" \
  "auth=official" \
  env ILK_ROLE_REGISTRY="$REGISTRY" bash "$WORKER_SCRIPT" --preflight-only --home "$FAKE_HOME"

# === Results ===
echo ""
echo "=== Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
