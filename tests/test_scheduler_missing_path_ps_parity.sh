#!/usr/bin/env bash
set -euo pipefail

# Parity test for the scheduler's skip-missing-path guard — static/structural
# checks only (no pwsh on macOS/Linux CI).
#
# The guard stops the scheduler from dispatching a project whose repo_path
# resolves but no longer exists on disk. launch.sh/launch.ps1 do reject such a
# path, but dispatch runs in a detached window, so that failure never reaches
# the scheduler — without the guard it re-dispatches every poll forever.
#
# AC coverage:
#   AC-1: scheduler.sh guards a resolved-but-absent repo path
#   AC-2: scheduler.ps1 mirrors the guard, keeping .sh/.ps1 parity
#   AC-3: both emit the same 'skip-missing-path' decision vocabulary
#   AC-4: the guard sits after the skip-unresolved check (empty is not "absent")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
SCHED_SH="${REPO_ROOT}/skills/ilk-watchdog/scripts/scheduler.sh"
SCHED_PS1="${REPO_ROOT}/skills/ilk-watchdog/scripts/scheduler.ps1"

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

sh="$(cat "$SCHED_SH")"
ps1="$(cat "$SCHED_PS1")"

echo "=== AC-1: scheduler.sh guards a resolved-but-absent repo path ==="
check "sh tests the repo path is a directory" "$sh" contains '! -d "$repo"'
check "sh emits skip-missing-path decision"   "$sh" contains '"decision\":\"skip-missing-path\"'
check "sh logs skip-missing-path"             "$sh" contains 'write_scheduler_log "skip-missing-path"'

echo "=== AC-2: scheduler.ps1 mirrors the guard ==="
check "ps1 tests the repo path is a container" "$ps1" contains 'Test-Path -LiteralPath $proj.repo_path -PathType Container'
check "ps1 emits skip-missing-path decision"   "$ps1" contains "decision = 'skip-missing-path'"
check "ps1 logs skip-missing-path"             "$ps1" contains "Write-SchedulerLog -Decision 'skip-missing-path'"

echo "=== AC-3: shared decision vocabulary ==="
check "sh keeps skip-unresolved"  "$sh"  contains 'skip-unresolved'
check "ps1 keeps skip-unresolved" "$ps1" contains 'skip-unresolved'

echo "=== AC-4: missing-path guard comes after the unresolved guard ==="
# An unresolved (empty) repo path must still report skip-unresolved, so the
# emptiness check has to run first — an empty path is also "not a directory".
first_line_of() {
  # Echo the 1-based line number of the first match, or 0 when absent.
  # 0 keeps the ordering comparison arithmetic-safe under `set -e`.
  local needle="$1" file="$2" line
  line=$(grep -n -- "$needle" "$file" | head -1 | cut -d: -f1) || true
  echo "${line:-0}"
}

check_order() {
  # check_order <label> <file>
  local label="$1" file="$2" unresolved missing
  unresolved=$(first_line_of 'skip-unresolved' "$file")
  missing=$(first_line_of 'skip-missing-path' "$file")
  if [[ "$missing" -eq 0 ]]; then
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${label} has no skip-missing-path guard at all"
  elif [[ "$unresolved" -eq 0 ]]; then
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${label} has no skip-unresolved guard at all"
  elif [[ "$unresolved" -lt "$missing" ]]; then
    PASS=$((PASS + 1))
    echo "  PASS: ${label} checks unresolved (line ${unresolved}) before missing-path (line ${missing})"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${label} must check unresolved before missing-path (${unresolved} vs ${missing})"
  fi
}

check_order "sh"  "$SCHED_SH"
check_order "ps1" "$SCHED_PS1"

echo
echo "PASS: ${PASS}  FAIL: ${FAIL}"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "ALL PASS"
