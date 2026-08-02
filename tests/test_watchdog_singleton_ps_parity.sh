#!/usr/bin/env bash
set -euo pipefail

# Parity test for watchdog.ps1 singleton guard — static/structural checks only (no pwsh).
# Verifies that the .ps1 identity check and cleanup match the .sh fixes.
#
# AC coverage:
#   AC-5: watchdog.ps1 uses a command-line identity check, keeping .sh/.ps1 parity

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
WATCHDOG_PS1="${REPO_ROOT}/skills/ilk-watchdog/scripts/watchdog.ps1"
WATCHDOG_SH="${REPO_ROOT}/skills/ilk-watchdog/scripts/watchdog.sh"

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

# Load file contents
ps1="$(cat "$WATCHDOG_PS1")"
sh="$(cat "$WATCHDOG_SH")"

echo "=== AC-5a: ps1 uses CommandLine for identity check ==="
check "ps1 references CommandLine"              "$ps1" contains 'CommandLine'
check "ps1 does NOT gate on ProcessName"        "$ps1" absent  '$proc.ProcessName.ToLower()'
check "ps1 passes watchdog.ps1 as expected token" "$ps1" contains "'watchdog.ps1'"

echo ""
echo "=== AC-5b: ps1 has ownership check before pid file removal ==="
check "ps1 checks recorded pid before removal"  "$ps1" contains 'recordedPid'
check "ps1 compares pid to \$PID"               "$ps1" contains '$PID'
check "ps1 has Write-Log for foreign pid"       "$ps1" contains 'leaving it'

echo ""
echo "=== AC-5c: sh/ps1 structural parity ==="
# Both must use command-line identity check (not interpreter name)
check "sh uses args="                           "$sh" contains '-o args='
check "sh does NOT use comm="                   "$sh" absent  '-o comm='
check "sh passes watchdog.sh as expected token" "$sh" contains '"watchdog.sh"'

# Both must have ownership check in cleanup
check "sh has recorded_pid in cleanup"          "$sh" contains 'recorded_pid'
check "sh compares to \$\$"                     "$sh" contains '"$$"'

# Both guard else-branches still clear stale pid files (legitimate)
check "sh guard else-branch clears dead pid"    "$sh" contains 'rm -f "$watchdog_pid_file"'
check "ps1 guard else-branch clears dead pid"   "$ps1" contains 'Remove-Item $watchdogPidFile'

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
