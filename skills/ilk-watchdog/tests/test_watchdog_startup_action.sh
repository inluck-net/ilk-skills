#!/usr/bin/env bash
# Parity test for watchdog.sh startup_sentinel_action().
# Mirrors the PS Get-StartupSentinelAction contract (AC-2/AC-3/AC-4).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG_SH="${SCRIPT_DIR}/../scripts/watchdog.sh"

fail=false

# Extract startup_sentinel_action function from watchdog.sh.
eval "$(sed -n '/^startup_sentinel_action()/,/^}/p' "$WATCHDOG_SH")"

assert_eq() {
  local expected="$1" actual="$2" msg="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: $msg (expected '$expected', got '$actual')"
    fail=true
  fi
}

# AC-2: non-success stale + loop_alive=true → stale-ignore
assert_eq "stale-ignore" \
  "$(startup_sentinel_action local_checks_failed 1000 2000 1 true)" \
  "AC-2: stale non-success + alive → stale-ignore"

# AC-3a: non-success stale + loop_alive=false → classify (dead loop, adjudicate)
assert_eq "classify" \
  "$(startup_sentinel_action local_checks_failed 1000 2000 1 false)" \
  "AC-3a: stale non-success + dead → classify"

# AC-3b: non-success fresh (ended ≥ launch) + alive → classify (this run's own terminal)
assert_eq "classify" \
  "$(startup_sentinel_action local_checks_failed 2000 1000 1 true)" \
  "AC-3b: fresh non-success + alive → classify"

# AC-3c: non-success ended_epoch=0 (unparseable) + alive → classify
assert_eq "classify" \
  "$(startup_sentinel_action local_checks_failed 0 2000 1 true)" \
  "AC-3c: non-success ended_epoch=0 → classify"

# AC-4a: success stale → stale-ignore
assert_eq "stale-ignore" \
  "$(startup_sentinel_action shipped 1000 2000 0 true)" \
  "AC-4a: stale success → stale-ignore"

# AC-4b: success fresh + loop_status_exit=0 → advance
assert_eq "advance" \
  "$(startup_sentinel_action shipped 2000 1000 0 true)" \
  "AC-4b: fresh success + exit=0 → advance"

# AC-4c: success fresh + loop_status_exit!=0 → work-pending
assert_eq "work-pending" \
  "$(startup_sentinel_action shipped 2000 1000 1 true)" \
  "AC-4c: fresh success + exit!=0 → work-pending"

# Additional success states
assert_eq "stale-ignore" \
  "$(startup_sentinel_action all-shipped 1000 2000 0 false)" \
  "AC-4d: stale all-shipped → stale-ignore"

assert_eq "advance" \
  "$(startup_sentinel_action already-shipped 2000 1000 0 false)" \
  "AC-4e: fresh already-shipped + exit=0 → advance"

# Edge: unknown non-stale state → classify (non-success, not stale)
assert_eq "classify" \
  "$(startup_sentinel_action some-unknown-state 2000 1000 1 true)" \
  "unknown non-stale state → classify"

if [[ "$fail" == true ]]; then
  echo "RED: startup_sentinel_action contract is broken"
  exit 1
fi
echo "PASS: startup_sentinel_action — all AC-2/AC-3/AC-4 cases correct (sh parity)"
exit 0
