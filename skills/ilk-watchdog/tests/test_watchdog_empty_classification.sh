#!/usr/bin/env bash
# Regression test for watchdog empty-classification handling.
# Part of sub-plan 2026-06-26-watchdog-empty-classification.
#
# AC-1: classify_action "" → block (explicit empty-string case).
# AC-2: With sentinel_state non-empty and collect.py yielding empty,
#        the effective classification is the raw state (interrupted → relaunch).
#        Also pins the structural fallback line in watchdog.sh source.
# AC-3: table-test every known label → expected action (no regression).
# AC-4: bash -n syntax check passes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG_SH="${SCRIPT_DIR}/../scripts/watchdog.sh"

failures=()

fail() { failures+=("$1"); echo "FAIL: $1"; }
pass() { echo "PASS: $1"; }

# ── Extract classify_action from watchdog.sh ──────────────────────────────────
eval "$(sed -n '/^classify_action()/,/^}/p' "$WATCHDOG_SH")"

# ── AC-1: empty string → block ───────────────────────────────────────────────
result="$(classify_action "")"
if [[ "$result" == "block" ]]; then
  pass "AC-1: classify_action \"\" → block"
else
  fail "AC-1: classify_action \"\" expected 'block', got '$result'"
fi

# ── AC-2: raw-state fallback structural pin ──────────────────────────────────
# When collect.py returns empty and sentinel_state is non-empty (e.g. interrupted),
# the watchdog falls back to classification="$sentinel_state". Pin this line
# so a future edit can't silently drop the fallback.
if grep -q 'classification="\$sentinel_state"' "$WATCHDOG_SH" 2>/dev/null \
   || grep -q "classification=\"\$sentinel_state\"" "$WATCHDOG_SH" 2>/dev/null; then
  pass "AC-2: source contains classification=\"\$sentinel_state\" fallback"
else
  fail "AC-2: structural pin — classification=\"\$sentinel_state\" fallback line missing from watchdog.sh"
fi

# AC-2: interrupted (the raw-state fallback's effective result) → relaunch
result="$(classify_action interrupted)"
if [[ "$result" == "relaunch" ]]; then
  pass "AC-2: classify_action interrupted → relaunch"
else
  fail "AC-2: classify_action interrupted expected 'relaunch', got '$result'"
fi

# ── AC-3: known-label table test ─────────────────────────────────────────────
# Running → sleep
result="$(classify_action running)"
if [[ "$result" == "sleep" ]]; then
  pass "AC-3: running → sleep"
else
  fail "AC-3: running expected 'sleep', got '$result'"
fi

# All-ship variants → promote
for lbl in all-shipped already-shipped shipped; do
  result="$(classify_action "$lbl")"
  if [[ "$result" == "promote" ]]; then
    pass "AC-3: $lbl → promote"
  else
    fail "AC-3: $lbl expected 'promote', got '$result'"
  fi
done

# clean-success → stop-clean
result="$(classify_action clean-success)"
if [[ "$result" == "stop-clean" ]]; then
  pass "AC-3: clean-success → stop-clean"
else
  fail "AC-3: clean-success expected 'stop-clean', got '$result'"
fi

# shipped-unverified → needs-human
result="$(classify_action shipped-unverified)"
if [[ "$result" == "needs-human" ]]; then
  pass "AC-3: shipped-unverified → needs-human"
else
  fail "AC-3: shipped-unverified expected 'needs-human', got '$result'"
fi

# self-hosting-drift → needs-human
result="$(classify_action self-hosting-drift)"
if [[ "$result" == "needs-human" ]]; then
  pass "AC-3: self-hosting-drift → needs-human"
else
  fail "AC-3: self-hosting-drift expected 'needs-human', got '$result'"
fi

# no-evidence → triage
result="$(classify_action no-evidence)"
if [[ "$result" == "triage" ]]; then
  pass "AC-3: no-evidence → triage"
else
  fail "AC-3: no-evidence expected 'triage', got '$result'"
fi

# Whitelist → relaunch
for lbl in timeout-bound max-iter-bound api-flaky interrupted; do
  result="$(classify_action "$lbl")"
  if [[ "$result" == "relaunch" ]]; then
    pass "AC-3: $lbl → relaunch"
  else
    fail "AC-3: $lbl expected 'relaunch', got '$result'"
  fi
done

# Blacklist → block
for lbl in stuck-no-progress api-blocked budget-exhausted local-checks-stuck local-checks-broken dependency-unreachable merge-conflict; do
  result="$(classify_action "$lbl")"
  if [[ "$result" == "block" ]]; then
    pass "AC-3: $lbl → block"
  else
    fail "AC-3: $lbl expected 'block', got '$result'"
  fi
done

# ── AC-4: bash -n syntax check ───────────────────────────────────────────────
if /bin/bash -n "$WATCHDOG_SH" 2>/dev/null; then
  pass "AC-4: /bin/bash -n watchdog.sh passes"
else
  fail "AC-4: /bin/bash -n watchdog.sh failed"
fi

# ── Report ────────────────────────────────────────────────────────────────────
if [[ ${#failures[@]} -gt 0 ]]; then
  echo ""
  echo "RED: ${#failures[@]} test(s) failed"
  exit 1
fi
echo ""
echo "PASS: watchdog empty-classification regression — all checks green"
exit 0
