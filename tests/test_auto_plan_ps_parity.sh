#!/usr/bin/env bash
set -euo pipefail

# Static parity test for the auto-plan feature in install.ps1 vs install.sh.
# Verifies that the PowerShell installer declares the same flags, markers,
# and host-file targets as the bash installer.  No pwsh required.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
INSTALL_PS1="${REPO_ROOT}/install.ps1"
INSTALL_SH="${REPO_ROOT}/install.sh"

PASS=0
FAIL=0

check() {
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

ps1="$(cat "$INSTALL_PS1")"
sh="$(cat "$INSTALL_SH")"

echo "=== Flags: ps1 declares -AutoUseIlkPlan and -OnlyAutoPlan ==="
check "ps1 has [switch]\$AutoUseIlkPlan"    "$ps1" contains '[switch]$AutoUseIlkPlan'
check "ps1 has [switch]\$OnlyAutoPlan"      "$ps1" contains '[switch]$OnlyAutoPlan'

echo ""
echo "=== Parity: sh has matching flags ==="
sh_has_auto="$(echo "$sh" | grep -c '\-\-auto-use-ilk-plan' || true)"
sh_has_only="$(echo "$sh" | grep -c '\-\-only-auto-plan' || true)"
if [[ "$sh_has_auto" -gt 0 ]]; then
  PASS=$((PASS + 1)); echo "  PASS: sh has --auto-use-ilk-plan"
else
  FAIL=$((FAIL + 1)); echo "  FAIL: sh missing --auto-use-ilk-plan"
fi
if [[ "$sh_has_only" -gt 0 ]]; then
  PASS=$((PASS + 1)); echo "  PASS: sh has --only-auto-plan"
else
  FAIL=$((FAIL + 1)); echo "  FAIL: sh missing --only-auto-plan"
fi

echo ""
echo "=== Markers: ps1 uses the same start/end markers ==="
check "ps1 has ilk:auto-plan:start"         "$ps1" contains "ilk:auto-plan:start"
check "ps1 has ilk:auto-plan:end"           "$ps1" contains "ilk:auto-plan:end"

echo ""
echo "=== Targets: ps1 references the same three host files ==="
check "ps1 targets .claude\CLAUDE.md"       "$ps1" contains 'CLAUDE.md'
check "ps1 targets .codex\AGENTS.md"        "$ps1" contains 'AGENTS.md'
check "ps1 targets .cursor\rules\*.mdc"     "$ps1" contains 'ilk-auto-plan.mdc'

echo ""
echo "=== Functions: ps1 has reconcile + helpers ==="
check "ps1 has Reconcile-AutoPlan"          "$ps1" contains 'Reconcile-AutoPlan'
check "ps1 has Read-AutoPlanPref"           "$ps1" contains 'Read-AutoPlanPref'
check "ps1 has Render-AutoPlanBlock"        "$ps1" contains 'Render-AutoPlanBlock'

echo ""
echo "=== Config: ps1 references conventions\config.yml ==="
check "ps1 references config.yml"           "$ps1" contains 'config.yml'
check "ps1 references auto_use_ilk_plan"    "$ps1" contains 'auto_use_ilk_plan'

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
