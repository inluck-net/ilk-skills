#!/usr/bin/env bash
set -euo pipefail

# Hermetic test for the auto-plan managed-block reconcile in install.sh.
#
# Uses a throwaway FAKE_HOME so the test never touches the operator's
# real ~/.cursor, ~/.claude, or ~/.codex.  The check() helper counts
# PASS/FAIL and exits non-zero when FAIL>0.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
INSTALL="${REPO_ROOT}/install.sh"

PASS=0
FAIL=0

check() {
  # check "<description>" "<haystack>" contains|absent "<needle>"
  local desc="$1" hay="$2" mode="$3" needle="$4"
  local found=0
  case "$hay" in *"$needle"*) found=1 ;; esac
  if { [[ "$mode" == "contains" && $found -eq 1 ]] || \
       [[ "$mode" == "absent"  && $found -eq 0 ]]; }; then
    PASS=$((PASS + 1))
    echo "  PASS: ${desc}"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${desc} (mode=${mode}, needle=${needle})"
  fi
}

TMP="$(mktemp -d)"
CONFIG="$REPO_ROOT/conventions/config.yml"
# Save original config so we can restore it after the test
cp "$CONFIG" "$CONFIG.test-backup"
trap 'cp "$CONFIG.test-backup" "$CONFIG" && rm -f "$CONFIG.test-backup" && rm -rf "$TMP"' EXIT
FAKE_HOME="$TMP/home"

# --- Test 1: pref ON + --apply → block present in shared files + .mdc exists ---
echo "=== Test 1: pref ON + --apply → block present ==="
HOME="$FAKE_HOME" bash "$INSTALL" --apply --auto-use-ilk-plan 2>&1

claude_md="$FAKE_HOME/.claude/CLAUDE.md"
codex_md="$FAKE_HOME/.codex/AGENTS.md"
cursor_mdc="$FAKE_HOME/.cursor/rules/ilk-auto-plan.mdc"

check "CLAUDE.md exists"            "$(cat "$claude_md" 2>/dev/null || echo)" contains "ilk:auto-plan:start"
check "CLAUDE.md has end marker"    "$(cat "$claude_md" 2>/dev/null || echo)" contains "ilk:auto-plan:end"
check "CLAUDE.md has heuristic"     "$(cat "$claude_md" 2>/dev/null || echo)" contains "Route to /ilk-plan"
check "AGENTS.md exists"            "$(cat "$codex_md" 2>/dev/null || echo)" contains "ilk:auto-plan:start"
check "AGENTS.md has end marker"    "$(cat "$codex_md" 2>/dev/null || echo)" contains "ilk:auto-plan:end"
check "Cursor .mdc exists"          "$(test -f "$cursor_mdc" && echo "yes" || echo "no")" contains "yes"
check "Cursor .mdc has heuristic"   "$(cat "$cursor_mdc" 2>/dev/null || echo)" contains "Route to /ilk-plan"

# --- Test 2: idempotency — run --apply again, still exactly one block ---
echo ""
echo "=== Test 2: idempotency — second --apply ==="
HOME="$FAKE_HOME" bash "$INSTALL" --apply --only-auto-plan 2>&1

claude_count="$(grep -c 'ilk:auto-plan:start' "$claude_md" || true)"
codex_count="$(grep -c 'ilk:auto-plan:start' "$codex_md" || true)"
check "CLAUDE.md has exactly one start marker (idempotent)" "$claude_count" contains "1"
check "AGENTS.md has exactly one start marker (idempotent)" "$codex_count" contains "1"

# --- Test 3: reversibility — pref OFF removes block, preserves surrounding ---
echo ""
echo "=== Test 3: reversibility — pref OFF ==="

# Seed CLAUDE.md with pre-existing content
mkdir -p "$FAKE_HOME/.claude"
printf "existing line 1\nexisting line 2\n" > "$claude_md"
# Ensure pref is ON for the reconcile-ON step
sed -i.bak 's/^auto_use_ilk_plan:.*/auto_use_ilk_plan: true/' "$REPO_ROOT/conventions/config.yml"
rm -f "$REPO_ROOT/conventions/config.yml.bak"
# Reconcile ON
HOME="$FAKE_HOME" bash "$INSTALL" --apply --only-auto-plan 2>&1 >/dev/null
# Verify block is present
check "pre-existing + block: CLAUDE.md has marker" "$(cat "$claude_md")" contains "ilk:auto-plan:start"
check "pre-existing + block: CLAUDE.md has existing" "$(cat "$claude_md")" contains "existing line 1"

# Now turn OFF
sed -i.bak 's/^auto_use_ilk_plan:.*/auto_use_ilk_plan: false/' "$REPO_ROOT/conventions/config.yml"
rm -f "$REPO_ROOT/conventions/config.yml.bak"
HOME="$FAKE_HOME" bash "$INSTALL" --apply --only-auto-plan 2>&1 >/dev/null
check "reversed: CLAUDE.md has no start marker" "$(cat "$claude_md")" absent "ilk:auto-plan:start"
check "reversed: CLAUDE.md has no end marker" "$(cat "$claude_md")" absent "ilk:auto-plan:end"
check "reversed: CLAUDE.md preserves existing line 1" "$(cat "$claude_md")" contains "existing line 1"
check "reversed: CLAUDE.md preserves existing line 2" "$(cat "$claude_md")" contains "existing line 2"
check "reversed: .mdc deleted" "$(test -f "$cursor_mdc" && echo "yes" || echo "no")" absent "yes"

# --- Results ---
echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
