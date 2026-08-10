#!/usr/bin/env bash
# Tests for slot-home clone bootstrap (--clone-slot).
# AC-1: clone creates settings.json with matching env, skills link, commands link,
#        .claude.json; re-run is idempotent (no error, no duplicate).
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

assert_file_exists() {
  local label="$1" path="$2"
  if [[ -e "$path" ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — file does not exist: $path"
    fail=$((fail + 1))
  fi
}

assert_exit_ok() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected exit 0"
    fail=$((fail + 1))
  fi
}

# --- Setup: fake base home under repo-local scratch ---
FAKE_BASE="$REPO_ROOT/scratch/slot-test/base"
rm -rf "$REPO_ROOT/scratch/slot-test"
mkdir -p "$FAKE_BASE/skills/ilk-runner"
mkdir -p "$FAKE_BASE/commands"

# Write a dummy settings.json with provider env.
cat > "$FAKE_BASE/settings.json" <<'EOF'
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://test-provider.example.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "test-token-12345",
    "ANTHROPIC_MODEL": "test-model-v1"
  }
}
EOF

# Write a minimal .claude.json (base already has one).
cat > "$FAKE_BASE/.claude.json" <<'EOF'
{
  "mcpServers": {}
}
EOF

# Write a dummy file in skills/ so we can verify the link.
echo "skill-content" > "$FAKE_BASE/skills/ilk-runner/SKILL.md"

# Write a dummy commands/ilk.md so we can verify the commands link.
echo "# /ilk" > "$FAKE_BASE/commands/ilk.md"

SLOT_HOME="$FAKE_BASE-2"

cleanup() {
  rm -rf "$REPO_ROOT/scratch/slot-test"
}
trap cleanup EXIT

# === Test 1: Clone slot 2 from fake base ===
echo "=== Test 1: Clone slot 2 ==="

assert_exit_ok "clone slot 2 succeeds" \
  bash "$BOOTSTRAP" --clone-slot 2 --from "$FAKE_BASE"

assert_file_exists "slot home exists" "$SLOT_HOME"
assert_file_exists "settings.json exists" "$SLOT_HOME/settings.json"
assert_file_exists ".claude.json exists" "$SLOT_HOME/.claude.json"

# Verify settings.json env matches the base.
# Use cygpath on Windows (Git Bash) so Python can open the file.
if command -v cygpath >/dev/null 2>&1; then
  _slot_json="$(cygpath -w "$SLOT_HOME/settings.json")"
else
  _slot_json="$SLOT_HOME/settings.json"
fi
base_url=$(python3 -c "import json; print(json.load(open(r'$_slot_json'))['env']['ANTHROPIC_BASE_URL'])" 2>/dev/null || \
           python -c "import json; print(json.load(open(r'$_slot_json'))['env']['ANTHROPIC_BASE_URL'])")
auth_token=$(python3 -c "import json; print(json.load(open(r'$_slot_json'))['env']['ANTHROPIC_AUTH_TOKEN'])" 2>/dev/null || \
             python -c "import json; print(json.load(open(r'$_slot_json'))['env']['ANTHROPIC_AUTH_TOKEN'])")
model=$(python3 -c "import json; print(json.load(open(r'$_slot_json'))['env']['ANTHROPIC_MODEL'])" 2>/dev/null || \
        python -c "import json; print(json.load(open(r'$_slot_json'))['env']['ANTHROPIC_MODEL'])")

assert_eq "ANTHROPIC_BASE_URL matches" "https://test-provider.example.com/anthropic" "$base_url"
assert_eq "ANTHROPIC_AUTH_TOKEN matches" "test-token-12345" "$auth_token"
assert_eq "ANTHROPIC_MODEL matches" "test-model-v1" "$model"

# Verify skills link/dir exists and contains the expected file.
if [[ -e "$SLOT_HOME/skills/ilk-runner/SKILL.md" ]]; then
  echo "  PASS: skills link accessible"
  pass=$((pass + 1))
else
  echo "  FAIL: skills link not accessible"
  fail=$((fail + 1))
fi

# Verify commands link exists and commands/ilk.md is readable through the slot path.
# AC-1: must be readable, not merely symlinked — a dangling link is the failure mode.
if [[ -r "$SLOT_HOME/commands/ilk.md" ]]; then
  echo "  PASS: commands/ilk.md readable through slot home"
  pass=$((pass + 1))
else
  echo "  FAIL: commands/ilk.md not readable through slot home"
  fail=$((fail + 1))
fi

# === Test 2: Idempotent re-run ===
echo ""
echo "=== Test 2: Idempotent re-run ==="

assert_exit_ok "re-clone slot 2 succeeds (idempotent)" \
  bash "$BOOTSTRAP" --clone-slot 2 --from "$FAKE_BASE"

# Verify still correct after re-run.
base_url2=$(python3 -c "import json; print(json.load(open(r'$_slot_json'))['env']['ANTHROPIC_BASE_URL'])" 2>/dev/null || \
            python -c "import json; print(json.load(open(r'$_slot_json'))['env']['ANTHROPIC_BASE_URL'])")
assert_eq "env still correct after re-run" "https://test-provider.example.com/anthropic" "$base_url2"

# Verify skills still accessible.
if [[ -e "$SLOT_HOME/skills/ilk-runner/SKILL.md" ]]; then
  echo "  PASS: skills still accessible after re-run"
  pass=$((pass + 1))
else
  echo "  FAIL: skills not accessible after re-run"
  fail=$((fail + 1))
fi

# Verify commands still accessible after re-run (AC-2: idempotent).
if [[ -r "$SLOT_HOME/commands/ilk.md" ]]; then
  echo "  PASS: commands/ilk.md still readable after re-run"
  pass=$((pass + 1))
else
  echo "  FAIL: commands/ilk.md not readable after re-run"
  fail=$((fail + 1))
fi

# === Test 3: Missing base home ===
echo ""
echo "=== Test 3: Missing base home ==="

set +e
bash "$BOOTSTRAP" --clone-slot 3 --from "$REPO_ROOT/scratch/slot-test/nonexistent" >/dev/null 2>&1
exit_code=$?
set -e

if [[ $exit_code -ne 0 ]]; then
  echo "  PASS: missing base home fails (exit $exit_code)"
  pass=$((pass + 1))
else
  echo "  FAIL: missing base home should fail"
  fail=$((fail + 1))
fi

# === Results ===
echo ""
echo "=== Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
