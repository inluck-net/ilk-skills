#!/usr/bin/env bash
# Tests for provider-alias preservation on bootstrap.sh's flags-only path.
#
# Defect D4: --from-ccswitch keeps every ANTHROPIC_* / CLAUDE_CODE_* key the
# provider carries (bootstrap.sh L363-372) so the DEFAULT_SONNET / HAIKU /
# OPUS aliases and per-provider harness settings survive. The flags-only path
# synthesizes exactly three keys and the merge replaces provider keys
# wholesale, so no alias can be supplied and none survives -- the failure the
# CCSwitch comment describes is still reachable by the other entrance.
#
# Covers:
#   AC-1: flags-only with alias values supplied writes them into the home's env
#         (this revision; reproduces D4)
# Exit 0 on success, 1 on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BOOTSTRAP="$SCRIPT_DIR/../bootstrap.sh"

if [[ ! -f "$BOOTSTRAP" ]]; then
  echo "FAIL: bootstrap.sh not found at $BOOTSTRAP" >&2
  exit 1
fi

pass=0
fail=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected '$expected', got '$actual'"
    fail=$((fail + 1))
  fi
}

assert_exit_ok() {
  local label="$1"
  shift
  local output rc
  set +e
  output="$("$@" 2>&1)"
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected exit 0, got $rc"
    echo "  output: $output"
    fail=$((fail + 1))
  fi
}

# env_get <settings.json> <key> -> value, or a diagnostic placeholder when the
# file or key is absent (so a failed write produces a readable assert mismatch
# instead of tripping set -e inside python).
env_get() {
  local settings="$1" key="$2"
  if [[ ! -f "$settings" ]]; then
    echo "(no settings.json)"
    return 0
  fi
  python3 -c "
import json, sys
try:
    env = json.load(open(sys.argv[1])).get('env') or {}
except (ValueError, OSError):
    print('(unparseable settings.json)')
    raise SystemExit(0)
print(env.get(sys.argv[2], '(absent)'))
" "$settings" "$key"
}

# --- Setup: fake home under repo-local scratch ---
SCRATCH="$REPO_ROOT/scratch/bootstrap-aliases-test"
rm -rf "$SCRATCH"
FAKE_HOME="$SCRATCH/home"
mkdir -p "$FAKE_HOME"

cleanup() {
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

# === Test 1 (AC-1): flags-only with alias values writes them into the home ===
echo "=== Test 1 (AC-1): flags-only carries --env provider aliases ==="

assert_exit_ok "bootstrap --apply with --env aliases succeeds" \
  bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "test-token-12345" \
    --model "glm-5.3" \
    --env "ANTHROPIC_DEFAULT_OPUS=glm-5.3" \
    --env "ANTHROPIC_DEFAULT_SONNET=glm-5.3" \
    --env "ANTHROPIC_DEFAULT_HAIKU=glm-5.3-fast" \
    --env "CLAUDE_CODE_MAX_CONTEXT_TOKENS=200000"

SETTINGS="$FAKE_HOME/settings.json"

# The three required values still land alongside the aliases.
assert_eq "ANTHROPIC_BASE_URL lands" \
  "https://glm.example.com/anthropic" "$(env_get "$SETTINGS" ANTHROPIC_BASE_URL)"
assert_eq "ANTHROPIC_AUTH_TOKEN lands" \
  "test-token-12345" "$(env_get "$SETTINGS" ANTHROPIC_AUTH_TOKEN)"
assert_eq "ANTHROPIC_MODEL lands" \
  "glm-5.3" "$(env_get "$SETTINGS" ANTHROPIC_MODEL)"

# The aliases and harness settings -- the actual subject of AC-1.
assert_eq "ANTHROPIC_DEFAULT_OPUS lands" \
  "glm-5.3" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_OPUS)"
assert_eq "ANTHROPIC_DEFAULT_SONNET lands" \
  "glm-5.3" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_SONNET)"
assert_eq "ANTHROPIC_DEFAULT_HAIKU lands" \
  "glm-5.3-fast" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_HAIKU)"
assert_eq "CLAUDE_CODE_MAX_CONTEXT_TOKENS lands" \
  "200000" "$(env_get "$SETTINGS" CLAUDE_CODE_MAX_CONTEXT_TOKENS)"

# === Results ===
echo ""
echo "=== Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
