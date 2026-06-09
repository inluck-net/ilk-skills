#!/usr/bin/env bash
set -euo pipefail

# Static parity test: assert both upgrade engines invoke the auto-plan
# reconcile entrypoint after every successful pull (not gated behind
# drift detection).
#
# Mirrors tests/test_ilk_upgrade_ps_parity.sh pattern.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
UPGRADE_SH="${REPO_ROOT}/skills/ilk-upgrade/scripts/upgrade.sh"
UPGRADE_PS1="${REPO_ROOT}/skills/ilk-upgrade/scripts/upgrade.ps1"

PASS=0
FAIL=0

check() {
  local desc="$1" result="$2"
  if [[ "$result" == "pass" ]]; then
    PASS=$((PASS + 1))
    echo "  PASS: ${desc}"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${desc}"
  fi
}

sh_src="$(cat "$UPGRADE_SH")"
ps1_src="$(cat "$UPGRADE_PS1")"

echo "=== upgrade.sh: reconcile entrypoint present ==="
if echo "$sh_src" | grep -q 'only-auto-plan'; then
  check "sh contains --only-auto-plan" "pass"
else
  check "sh contains --only-auto-plan" "fail"
fi

echo ""
echo "=== upgrade.ps1: reconcile entrypoint present ==="
if echo "$ps1_src" | grep -q 'OnlyAutoPlan'; then
  check "ps1 contains -OnlyAutoPlan" "pass"
else
  check "ps1 contains -OnlyAutoPlan" "fail"
fi

echo ""
echo "=== upgrade.sh: reconcile is NOT inside drift-only conditional ==="
# The reconcile call must appear AFTER the if/fi block for drift detection.
# Strategy: find the line number of "only-auto-plan" and the line number of
# the last "fi" before it. If the last "fi" is close (nested), fail.
sh_reconcile_line=$(grep -n 'only-auto-plan' "$UPGRADE_SH" | head -1 | cut -d: -f1)
sh_last_fi_before=$(head -n "$sh_reconcile_line" "$UPGRADE_SH" | grep -n '^\s*fi\s*$' | tail -1 | cut -d: -f1)

if [[ -n "$sh_reconcile_line" && -n "$sh_last_fi_before" ]]; then
  # The reconcile should be after the fi (not inside the if block)
  if [[ "$sh_reconcile_line" -gt "$sh_last_fi_before" ]]; then
    check "sh reconcile is outside drift conditional (line $sh_reconcile_line > fi at line $sh_last_fi_before)" "pass"
  else
    check "sh reconcile is outside drift conditional" "fail"
  fi
else
  check "sh could not locate reconcile and fi lines" "fail"
fi

echo ""
echo "=== upgrade.ps1: reconcile is NOT inside drift-only conditional ==="
# Same logic for ps1: find OnlyAutoPlan line and the closing } of the drift block.
ps1_reconcile_line=$(grep -n 'OnlyAutoPlan' "$UPGRADE_PS1" | head -1 | cut -d: -f1)
ps1_last_brace_before=$(head -n "$ps1_reconcile_line" "$UPGRADE_PS1" | grep -n '^\s*}\s*$' | tail -1 | cut -d: -f1)

if [[ -n "$ps1_reconcile_line" && -n "$ps1_last_brace_before" ]]; then
  if [[ "$ps1_reconcile_line" -gt "$ps1_last_brace_before" ]]; then
    check "ps1 reconcile is outside drift conditional (line $ps1_reconcile_line > brace at line $ps1_last_brace_before)" "pass"
  else
    check "ps1 reconcile is outside drift conditional" "fail"
  fi
else
  check "ps1 could not locate reconcile and brace lines" "fail"
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
