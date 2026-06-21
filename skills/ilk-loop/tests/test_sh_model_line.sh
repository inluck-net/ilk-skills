#!/usr/bin/env bash
# test_sh_model_line.sh — AC-4: verify the resolver path that .sh uses
# for the Model: display line + JSONL model field.
#
# This test exercises the same resolver call that run_ilk_loop_claude.sh
# makes in main() after preflight(), with a fake CLAUDE_CONFIG_DIR.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESOLVER="${SCRIPT_DIR}/../scripts/resolve_worker_model.py"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# --- Test 1: settings.json env block provides the model ---
echo "Test 1: settings.json env.ANTHROPIC_MODEL resolved"
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
cat > "$tmpdir/settings.json" <<'EOF'
{"env": {"ANTHROPIC_MODEL": "mimo-v2.5-pro"}}
EOF
result=$(python "$RESOLVER" "" "" "$tmpdir" 2>/dev/null)
model="${result%%|*}"
source="${result##*|}"
if [[ "$model" == "mimo-v2.5-pro" && "$source" == "settings" ]]; then
  pass "model=$model source=$source"
else
  fail "expected mimo-v2.5-pro|settings, got $result"
fi

# --- Test 2: flag wins over settings ---
echo "Test 2: explicit flag wins"
result=$(python "$RESOLVER" "claude-opus-4-8" "" "$tmpdir" 2>/dev/null)
model="${result%%|*}"
if [[ "$model" == "claude-opus-4-8" ]]; then
  pass "flag overrides settings"
else
  fail "expected claude-opus-4-8, got $model"
fi

# --- Test 3: env wins over settings ---
echo "Test 3: env var wins over settings"
result=$(python "$RESOLVER" "" "from-env-model" "$tmpdir" 2>/dev/null)
model="${result%%|*}"
if [[ "$model" == "from-env-model" ]]; then
  pass "env overrides settings"
else
  fail "expected from-env-model, got $model"
fi

# --- Test 4: missing settings.json → unknown ---
echo "Test 4: missing settings.json"
emptydir=$(mktemp -d)
result=$(python "$RESOLVER" "" "" "$emptydir" 2>/dev/null)
source="${result##*|}"
if [[ "$source" == "unknown" ]]; then
  pass "missing file → unknown"
else
  fail "expected unknown, got $result"
fi
rm -rf "$emptydir"

# --- Test 5: simulated display line ---
echo "Test 5: display line contains resolved model"
cat > "$tmpdir/settings.json" <<'EOF'
{"env": {"ANTHROPIC_MODEL": "deepseek-r1"}}
EOF
resolved=$(python "$RESOLVER" "" "" "$tmpdir" 2>/dev/null)
r_model="${resolved%%|*}"
r_source="${resolved##*|}"
display_line="Model:          $r_model (from $r_source)"
if [[ "$display_line" == *"deepseek-r1"* && "$display_line" == *"from settings"* ]]; then
  pass "display line: $display_line"
else
  fail "display line missing model: $display_line"
fi

# --- Summary ---
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
