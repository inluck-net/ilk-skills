#!/usr/bin/env bash
# Minimal parity test for watchdog.sh classify_action().
# Mirrors test_watchdog_action_vocab.ps1 assertions for the bash port.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG_SH="${SCRIPT_DIR}/../scripts/watchdog.sh"

fail=false

# Extract classify_action function from watchdog.sh (it's defined before main).
# Source just the function definition.
eval "$(sed -n '/^classify_action()/,/^}/p' "$WATCHDOG_SH")"

assert_eq() {
  local expected="$1" actual="$2" msg="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: $msg (expected '$expected', got '$actual')"
    fail=true
  fi
}

# AC-2: clean-success => stop-clean
assert_eq "stop-clean" "$(classify_action clean-success)" "AC-2: clean-success => stop-clean"

# AC-3: self-hosting-drift => needs-human
assert_eq "needs-human" "$(classify_action self-hosting-drift)" "AC-3: self-hosting-drift => needs-human"

# AC-4: Whitelist => relaunch
for lbl in timeout-bound max-iter-bound api-flaky interrupted; do
  assert_eq "relaunch" "$(classify_action "$lbl")" "AC-4: whitelist '$lbl' => relaunch"
done

# AC-4: Blacklist => block
for lbl in stuck-no-progress api-blocked budget-exhausted local-checks-stuck dependency-unreachable; do
  assert_eq "block" "$(classify_action "$lbl")" "AC-4: blacklist '$lbl' => block"
done

# AC-4: shipped-unverified => needs-human
assert_eq "needs-human" "$(classify_action shipped-unverified)" "AC-4: shipped-unverified => needs-human"

# AC-4: no-evidence => triage
assert_eq "triage" "$(classify_action no-evidence)" "AC-4: no-evidence => triage"

# AC-5: Totality — every label must NOT return 'unknown'
ALL_LABELS=(
  timeout-bound max-iter-bound api-flaky interrupted
  stuck-no-progress api-blocked budget-exhausted local-checks-stuck dependency-unreachable
  clean-success shipped-unverified self-hosting-drift no-evidence
)
for lbl in "${ALL_LABELS[@]}"; do
  r="$(classify_action "$lbl")"
  if [[ "$r" == "unknown" || -z "$r" ]]; then
    echo "FAIL: AC-5 totality — '$lbl' resolved to '$r'"
    fail=true
  fi
done

# Fail-closed: unknown label => block
assert_eq "block" "$(classify_action some-future-label)" "fail-closed: unknown label => block"

if [[ "$fail" == true ]]; then
  echo "RED: watchdog.sh classify_action mapping is not total or incorrect"
  exit 1
fi
echo "PASS: classify_action — all labels resolve correctly, mapping is total (sh parity)"
exit 0
