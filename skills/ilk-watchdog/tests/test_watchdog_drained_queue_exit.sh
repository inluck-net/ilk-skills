#!/usr/bin/env bash
# Regression test: a watchdog must not outlive its loop on a drained queue.
#
# Root cause this pins (2026-08-10): watchdog.sh invoked
#   loop_status.py --project <path>
# but loop_status.py takes NO --project flag (usage: [-h] [--json]) -- it walks
# up from cwd, the way git does. argparse therefore exited 2 unconditionally,
# `loop_status_exit` was never 0, the `advance` branch was UNREACHABLE, and every
# drained queue fell through to `work-pending` -> sleep -> forever. Two
# ilk-pocket watchdogs spun that way for 15 days; two gh-resolve ones for 9h/14h.
#
# watchdog.ps1's Test-AllShipped has always done it correctly
# (-WorkingDirectory $Project, no flag), so this was a bash/PowerShell parity
# divergence rather than a design decision.
#
# AC-1: watchdog.sh must NOT pass --project to loop_status.py.
# AC-2: loop_status.py genuinely rejects --project (pins WHY AC-1 matters, so a
#       future reader cannot dismiss it as style).
# AC-3: loop_status.py run with cwd=<project> and no flag exits 0 on a drained
#       queue -- i.e. the invocation AC-1 mandates actually reaches `advance`.
# AC-4: an exit >= 2 ("I could not look") must not be laundered into
#       work-pending; the source must block instead.
# AC-5: a keep-alive backstop exists and is bounded.
# AC-6: the work-pending branch checks whether the loop is still alive.
# AC-7: the streak resets on progress, so "consecutive" means consecutive.
# AC-8: bash -n syntax check passes.
# AC-9: the stale-ignore path is bounded too -- a watchdog started AFTER its loop
#       finished sees a pre-launch sentinel and must not wait forever for a loop
#       that will never appear (verified live 2026-08-10: 10/10 polls spinning).
# AC-10: a live loop resets the stale-ignore streak (ignoring a stale sentinel is
#       legitimate while something is actually running).
# AC-11: write_banner expands \n so BLOCKED bodies are readable (all 20 banner
#       bodies in this file use \n separators, which bash leaves literal).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG_SH="${SCRIPT_DIR}/../scripts/watchdog.sh"
LOOP_STATUS_PY="${SCRIPT_DIR}/../../ilk-loop/scripts/loop_status.py"

PYTHON=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && { PYTHON="$c"; break; }
done
[[ -z "$PYTHON" ]] && { echo "SKIP: no python found"; exit 0; }

failures=()
fail() { failures+=("$1"); echo "  FAIL: $1"; }
pass() { echo "  PASS: $1"; }

echo "=== AC-1: watchdog.sh does not pass --project to loop_status.py ==="
if grep -qE 'LOOP_STATUS_PY"?[[:space:]]+--project' "$WATCHDOG_SH"; then
  fail "AC-1: watchdog.sh still passes --project to loop_status.py (argparse exits 2 -> advance unreachable -> immortal watchdog)"
else
  pass "AC-1: no --project passed to loop_status.py"
fi

echo "=== AC-2: loop_status.py really does reject --project ==="
"$PYTHON" "$LOOP_STATUS_PY" --project /tmp >/dev/null 2>&1
rc=$?
if [[ "$rc" -eq 2 ]]; then
  pass "AC-2: --project is a usage error (exit 2), confirming AC-1 is a real bug not a style choice"
else
  fail "AC-2: expected exit 2 from --project, got $rc -- if loop_status.py gained a --project flag, revisit AC-1"
fi

echo "=== AC-3: cwd-based invocation exits 0 on a drained queue ==="
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
plans="$tmp/docs/plans"
mkdir -p "$plans"
cat > "$plans/MASTER-2026-01-01-execution-plan.md" <<'EOF'
---
master_plan: 2026-01-01-drained
batch_date: 2026-01-01
status: active
supervised_only: false
---

# MASTER — drained

## Sub-plan registry

| # | Sub-plan | Steps |
|---|---|---|
| 1 | [2026-01-01-only.md](./2026-01-01-only.md) | 1 |
EOF
cat > "$plans/2026-01-01-only.md" <<'EOF'
---
plan: 2026-01-01-only
status: shipped
current_step: 1
estimated_steps: 1
verification_tier: loop-verified
---

# Sub-plan: already shipped
EOF
( cd "$tmp" && "$PYTHON" "$LOOP_STATUS_PY" >/dev/null 2>&1 )
rc=$?
if [[ "$rc" -eq 0 ]]; then
  pass "AC-3: drained queue via cwd -> exit 0 (advance is reachable)"
else
  fail "AC-3: drained queue via cwd gave exit $rc, expected 0 -- advance would still be unreachable"
fi

echo "=== AC-4: exit >= 2 blocks instead of becoming work-pending ==="
if grep -q 'loop_status_exit" -ge 2' "$WATCHDOG_SH" \
   && grep -q "LOOP_STATUS UNREADABLE" "$WATCHDOG_SH"; then
  pass "AC-4: an unreadable loop_status blocks loudly"
else
  fail "AC-4: no guard for loop_status exit >= 2 -- 'I could not look' can still be read as 'there is work'"
fi

echo "=== AC-5: bounded keep-alive backstop ==="
if grep -q "work_pending_limit" "$WATCHDOG_SH" \
   && grep -q "work_pending_streak >= work_pending_limit" "$WATCHDOG_SH"; then
  pass "AC-5: keep-alive is bounded by work_pending_limit"
else
  fail "AC-5: no bounded backstop -- a watchdog can still spin indefinitely"
fi

echo "=== AC-6: work-pending checks loop liveness ==="
if awk '/work-pending\)/,/;;/' "$WATCHDOG_SH" | grep -q 'loop_alive.*!=.*true'; then
  pass "AC-6: work-pending exits when the supervised loop is gone"
else
  fail "AC-6: work-pending does not check loop_alive -- watchdog can outlive a dead loop"
fi

echo "=== AC-7: streak resets on progress ==="
n="$(grep -c 'work_pending_streak=0' "$WATCHDOG_SH")"
if [[ "$n" -ge 3 ]]; then
  pass "AC-7: streak reset at $n sites (declaration + progress branches)"
else
  fail "AC-7: only $n reset site(s) -- 'consecutive' would not mean consecutive, and a healthy long-lived watchdog could trip the backstop"
fi

echo "=== AC-9: stale-ignore is bounded ==="
if grep -q "stale_ignore_limit" "$WATCHDOG_SH" \
   && grep -q "stale_ignore_streak >= stale_ignore_limit" "$WATCHDOG_SH"; then
  pass "AC-9: stale-ignore keep-watching is bounded"
else
  fail "AC-9: stale-ignore is unbounded -- a watchdog started after its loop finished waits forever"
fi

echo "=== AC-10: a live loop resets the stale-ignore streak ==="
if awk '/stale-ignore\)/,/;;/' "$WATCHDOG_SH" | grep -q 'loop_alive.*==.*true'; then
  pass "AC-10: stale-ignore streak resets while a loop is alive"
else
  fail "AC-10: stale-ignore does not distinguish a live loop -- would exit on a healthy watchdog"
fi

echo "=== AC-11: write_banner expands escape sequences ==="
if awk '/^write_banner\(\)/,/^}/' "$WATCHDOG_SH" | grep -q "printf '%b" ; then
  pass "AC-11: banner bodies expand \\n into real newlines"
else
  fail "AC-11: write_banner does not expand \\n -- every BLOCKED banner prints as one unreadable line"
fi

echo "=== AC-8: syntax ==="
if bash -n "$WATCHDOG_SH" 2>/dev/null; then
  pass "AC-8: bash -n clean"
else
  fail "AC-8: bash -n failed"
fi

echo
if [[ ${#failures[@]} -gt 0 ]]; then
  echo "=== Results: $(( 11 - ${#failures[@]} )) passed, ${#failures[@]} failed ==="
  exit 1
fi
echo "=== Results: 11 passed, 0 failed ==="
