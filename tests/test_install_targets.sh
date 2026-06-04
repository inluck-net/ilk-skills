#!/usr/bin/env bash
set -euo pipefail

# Verify install.sh builds the expected target list for the default case
# and for a custom Claude home (--claude-home / --only-claude).
#
# Hermetic: HOME is redirected to a throwaway temp dir so the dry-run
# never reads or mutates the operator's real ~/.cursor, ~/.claude, or
# ~/.codex. install.sh resolves its repo root from BASH_SOURCE, not HOME,
# so overriding HOME only moves the *targets*.
#
# Also statically checks that install.ps1 wires a custom Claude home into
# its Claude Code target (no pwsh required on macOS/Linux CI).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
INSTALL_SH="${REPO_ROOT}/install.sh"
INSTALL_PS1="${REPO_ROOT}/install.ps1"

PASS=0
FAIL=0

check() {
  # check "<description>" "<haystack>" contains|absent "<needle>"
  local desc="$1" hay="$2" mode="$3" needle="$4"
  local found=0
  case "$hay" in *"$needle"*) found=1 ;; esac
  if { [[ "$mode" == contains && $found -eq 1 ]] || \
       [[ "$mode" == absent  && $found -eq 0 ]]; }; then
    PASS=$((PASS + 1))
    echo "  PASS: ${desc}"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${desc} (mode=${mode}, needle=${needle})"
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAKE_HOME="$TMP/home"
WORKER="$TMP/worker"

echo "=== default dry-run targets Cursor + Claude Code + Codex ==="
default_out="$(HOME="$FAKE_HOME" bash "$INSTALL_SH" --dry-run 2>&1)"
check "default includes Cursor home"      "$default_out" contains "$FAKE_HOME/.cursor/skills"
check "default includes default Claude"   "$default_out" contains "$FAKE_HOME/.claude/skills"
check "default includes Codex home"       "$default_out" contains "$FAKE_HOME/.codex/skills"
check "default has no custom-home banner"  "$default_out" absent  "(custom)"

echo ""
echo "=== custom Claude home, --only-claude, targets only the worker home ==="
custom_out="$(HOME="$FAKE_HOME" bash "$INSTALL_SH" --dry-run --claude-home "$WORKER" --only-claude 2>&1)"
check "custom targets worker skills"      "$custom_out" contains "$WORKER/skills/"
check "custom targets worker commands"    "$custom_out" contains "$WORKER/commands/"
check "custom names the home (banner)"    "$custom_out" contains "$WORKER (custom)"
check "custom skips Cursor"               "$custom_out" absent  "/.cursor/"
check "custom skips Codex"                "$custom_out" absent  "/.codex/"
check "custom skips default Claude home"  "$custom_out" absent  "$FAKE_HOME/.claude/"

echo ""
echo "=== install.ps1 wires a custom Claude home (static) ==="
ps1="$(cat "$INSTALL_PS1")"
check "ps1 declares ClaudeHome param"     "$ps1" contains '[string]$ClaudeHome'
check "ps1 documents -ClaudeHome usage"   "$ps1" contains '-ClaudeHome'
check "ps1 targets custom skills dir"     "$ps1" contains 'Join-Path $ClaudeHome "skills"'
check "ps1 targets custom commands dir"   "$ps1" contains 'Join-Path $ClaudeHome "commands"'

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
