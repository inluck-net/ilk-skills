#!/usr/bin/env bash
set -euo pipefail

# Parity test for upgrade.ps1 — static/structural checks only (no pwsh).
# Mirrors tests/test_install_targets.sh pattern: grep for markers that
# assert AC-1..AC-4 of the PowerShell engine.
#
# Also verifies that the ps1 flag set matches the bash engine's flags.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
UPGRADE_PS1="${REPO_ROOT}/skills/ilk-upgrade/scripts/upgrade.ps1"
UPGRADE_SH="${REPO_ROOT}/skills/ilk-upgrade/scripts/upgrade.sh"

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

# Load file contents
ps1="$(cat "$UPGRADE_PS1")"
sh="$(cat "$UPGRADE_SH")"

echo "=== AC-1: param block with Check, Apply, Force switches + comment-based help ==="
check "ps1 has param("                          "$ps1" contains "param("
check "ps1 has [switch]\$Check"                 "$ps1" contains '[switch]$Check'
check "ps1 has [switch]\$Apply"                 "$ps1" contains '[switch]$Apply'
check "ps1 has [switch]\$Force"                 "$ps1" contains '[switch]$Force'
check "ps1 has .SYNOPSIS"                       "$ps1" contains '.SYNOPSIS'
check "ps1 has .DESCRIPTION"                    "$ps1" contains '.DESCRIPTION'
check "ps1 has .PARAMETER"                      "$ps1" contains '.PARAMETER'
check "ps1 has .EXAMPLE"                        "$ps1" contains '.EXAMPLE'

echo ""
echo "=== AC-2: repo root resolution via Resolve-Path + PSScriptRoot ==="
check "ps1 uses \$PSScriptRoot"                 "$ps1" contains '$PSScriptRoot'
check "ps1 uses Resolve-Path"                   "$ps1" contains 'Resolve-Path'
check "ps1 walks up three levels (..\..\..)"    "$ps1" contains '..\..\..'

echo ""
echo "=== AC-3: references install.ps1, git pull --ff-only, copy-vs-symlink ==="
check "ps1 references install.ps1"              "$ps1" contains 'install.ps1'
check "ps1 does git pull --ff-only"             "$ps1" contains '--ff-only'
check "ps1 checks ReparsePoint"                 "$ps1" contains 'ReparsePoint'
check "ps1 checks LinkType"                     "$ps1" contains 'LinkType'

echo ""
echo "=== AC-4: live-loop PID guard ==="
check "ps1 scans running.pid"                   "$ps1" contains 'running.pid'
check "ps1 references launcher runtime"         "$ps1" contains 'launcher'
check "ps1 references watchdog runtime"         "$ps1" contains 'watchdog'

echo ""
echo "=== Parity: ps1 flag set matches bash engine ==="
# Bash engine uses double-dash: --check, --apply, --force, --help
# PowerShell uses single-dash: -Check, -Apply, -Force, -Help
# Both conventions are equivalent; verify each exists in the right form.

# Bash flags (double-dash)
sh_has_check="$(echo "$sh" | grep -c '\-\-check' || true)"
sh_has_apply="$(echo "$sh" | grep -c '\-\-apply' || true)"
sh_has_force="$(echo "$sh" | grep -c '\-\-force' || true)"
sh_has_help="$(echo "$sh" | grep -c '\-\-help' || true)"

# PowerShell flags (single-dash, in param block)
ps1_has_check="$(echo "$ps1" | grep -c '\[switch\]\$Check' || true)"
ps1_has_apply="$(echo "$ps1" | grep -c '\[switch\]\$Apply' || true)"
ps1_has_force="$(echo "$ps1" | grep -c '\[switch\]\$Force' || true)"
ps1_has_help="$(echo "$ps1" | grep -c '\[switch\]\$Help' || true)"

if [[ "$sh_has_check" -gt 0 && "$ps1_has_check" -gt 0 ]]; then
  PASS=$((PASS + 1))
  echo "  PASS: both engines have check mode"
else
  FAIL=$((FAIL + 1))
  echo "  FAIL: check mode missing (sh: $sh_has_check, ps1: $ps1_has_check)"
fi

if [[ "$sh_has_apply" -gt 0 && "$ps1_has_apply" -gt 0 ]]; then
  PASS=$((PASS + 1))
  echo "  PASS: both engines have apply mode"
else
  FAIL=$((FAIL + 1))
  echo "  FAIL: apply mode missing (sh: $sh_has_apply, ps1: $ps1_has_apply)"
fi

if [[ "$sh_has_force" -gt 0 && "$ps1_has_force" -gt 0 ]]; then
  PASS=$((PASS + 1))
  echo "  PASS: both engines have force flag"
else
  FAIL=$((FAIL + 1))
  echo "  FAIL: force flag missing (sh: $sh_has_force, ps1: $ps1_has_force)"
fi

if [[ "$sh_has_help" -gt 0 && "$ps1_has_help" -gt 0 ]]; then
  PASS=$((PASS + 1))
  echo "  PASS: both engines have help flag"
else
  FAIL=$((FAIL + 1))
  echo "  FAIL: help flag missing (sh: $sh_has_help, ps1: $ps1_has_help)"
fi

# --dry-run is bash-only (ps1 uses -Check as default, no explicit -DryRun)
# That's acceptable parity — the behavior is equivalent.
echo "  INFO: --dry-run is bash-only (ps1 defaults to -Check); acceptable parity"

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
