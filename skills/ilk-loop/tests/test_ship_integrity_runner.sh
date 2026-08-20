#!/usr/bin/env bash
# Runtime regression gate for test_ship_integrity (run_ilk_loop_claude.sh).
#
# The bash counterpart of test_ship_integrity_runner.ps1. The PowerShell runner
# has carried the cross-run scoping guard since its own bug-2 fix; the bash
# runner never received the port, so on 2026-08-20 one red gate in a new batch
# rewrote 69 of 150 historical sub-plans from `status: shipped` to
# `status: in-progress` on a consumer project.
#
# Mechanism it guards: test_ship_integrity walks EVERY *.md in the plans dir,
# not just the current master's registry. A sub-plan shipped by a PRIOR batch is
# necessarily absent from THIS iteration's local_checks JSONL, so the gate
# lookup fell through to 'unknown' -- which ship_integrity.py:76 reports as
# "gate declared but no gate result recorded", exit 1 -- and the runner then
# reverted the file's status.
#
# Exit 0 = green, exit 1 = red.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

export ILK_DOTSOURCE_ONLY=1
RUNNER_PATH="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"
if ! source "$RUNNER_PATH"; then
  echo "FAIL: sourcing run_ilk_loop_claude.sh failed" >&2
  exit 1
fi
unset ILK_DOTSOURCE_ONLY

# The runner sets `set -Eeuo pipefail` when sourced. This test calls
# test_ship_integrity expecting a non-zero return, so errexit must be off.
set +eE +o pipefail

if ! type -t test_ship_integrity >/dev/null 2>&1; then
  echo "FAIL: test_ship_integrity function not found after sourcing runner" >&2
  exit 1
fi

# The function resolves ship_integrity.py under _SKILL_ROOT; point it at the repo.
_SKILL_ROOT="$REPO_ROOT/skills"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/ship-int-sh-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
PLANS="$TMP/plans"
mkdir -p "$PLANS"

fail=0
pass_count=0
assert() {
  local name="$1" cond="$2"
  if [[ "$cond" == "0" ]]; then
    echo "  PASS: $name"
    pass_count=$((pass_count + 1))
  else
    echo "  FAIL: $name" >&2
    fail=$((fail + 1))
  fi
}

write_plan() {
  # $1 = filename, $2 = slug, $3 = status, $4 = "gated"|"ungated"
  local checks="local_checks: []"
  if [[ "$4" == "gated" ]]; then
    checks=$'local_checks:\n  - command: pytest -q\n    timeout: 60'
  fi
  cat > "$PLANS/$1" <<PLAN
---
plan: $2
status: $3
current_step: 1
$checks
---

# $2
PLAN
}

status_of() { grep -m1 '^status:' "$PLANS/$1" | sed 's/^status:[[:space:]]*//'; }

reset_fixtures() {
  find "$PLANS" -maxdepth 1 -name "*.md" -delete
  # prior-batch ship: shipped + declares a frontmatter gate. THE VICTIM.
  write_plan "2026-01-01-prior-batch.md" "prior-batch" "shipped" "gated"
  # shipped this iteration, declares a gate
  write_plan "2026-02-02-alpha.md" "alpha" "shipped" "gated"
  # shipped but no declared gate -> never enforced
  write_plan "2026-02-02-beta.md" "beta" "shipped" "ungated"
  # not shipped -> never enforced
  write_plan "2026-02-02-gamma.md" "gamma" "in-progress" "gated"
}

# --- AC-1: a red gate on THIS iteration's sub-plan must not touch a prior batch.
reset_fixtures
LC="$TMP/lc-red.jsonl"
printf '%s\n' '{"slug": "alpha", "outcome": "fail"}' > "$LC"
test_ship_integrity "$PLANS" "$LC" >/dev/null 2>&1
rc=$?
assert "red gate on alpha -> violation reported (rc=1, got $rc)" "$([[ $rc -eq 1 ]] && echo 0 || echo 1)"
assert "alpha reverted to in-progress (got '$(status_of 2026-02-02-alpha.md)')" \
  "$([[ "$(status_of 2026-02-02-alpha.md)" == "in-progress" ]] && echo 0 || echo 1)"
assert "PRIOR BATCH untouched, still shipped (got '$(status_of 2026-01-01-prior-batch.md)')" \
  "$([[ "$(status_of 2026-01-01-prior-batch.md)" == "shipped" ]] && echo 0 || echo 1)"
assert "beta (no declared gate) untouched (got '$(status_of 2026-02-02-beta.md)')" \
  "$([[ "$(status_of 2026-02-02-beta.md)" == "shipped" ]] && echo 0 || echo 1)"

# --- AC-2: an empty gate map (nothing gated this iteration) reverts nothing.
reset_fixtures
LC="$TMP/lc-empty.jsonl"
: > "$LC"
test_ship_integrity "$PLANS" "$LC" >/dev/null 2>&1
rc=$?
assert "empty gate map -> no violations (rc=0, got $rc)" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
assert "empty gate map -> prior batch still shipped" \
  "$([[ "$(status_of 2026-01-01-prior-batch.md)" == "shipped" ]] && echo 0 || echo 1)"
assert "empty gate map -> alpha still shipped" \
  "$([[ "$(status_of 2026-02-02-alpha.md)" == "shipped" ]] && echo 0 || echo 1)"

# --- AC-3: a MISSING gate file (no gate ran at all) reverts nothing.
reset_fixtures
test_ship_integrity "$PLANS" "$TMP/does-not-exist.jsonl" >/dev/null 2>&1
rc=$?
assert "missing gate file -> no violations (rc=0, got $rc)" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
assert "missing gate file -> prior batch still shipped" \
  "$([[ "$(status_of 2026-01-01-prior-batch.md)" == "shipped" ]] && echo 0 || echo 1)"

# --- AC-4: a green gate is honest -> nothing reverted.
reset_fixtures
LC="$TMP/lc-green.jsonl"
printf '%s\n' '{"slug": "alpha", "outcome": "pass"}' > "$LC"
test_ship_integrity "$PLANS" "$LC" >/dev/null 2>&1
rc=$?
assert "green gate on alpha -> no violations (rc=0, got $rc)" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
assert "green gate -> alpha still shipped" \
  "$([[ "$(status_of 2026-02-02-alpha.md)" == "shipped" ]] && echo 0 || echo 1)"

# --- AC-5: a non-shipped sub-plan with a red gate is never rewritten.
reset_fixtures
LC="$TMP/lc-gamma.jsonl"
printf '%s\n' '{"slug": "gamma", "outcome": "fail"}' > "$LC"
test_ship_integrity "$PLANS" "$LC" >/dev/null 2>&1
rc=$?
assert "red gate on non-shipped gamma -> no violations (rc=0, got $rc)" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)"
assert "gamma still in-progress" \
  "$([[ "$(status_of 2026-02-02-gamma.md)" == "in-progress" ]] && echo 0 || echo 1)"

echo
if [[ $fail -eq 0 ]]; then
  echo "ALL PASS ($pass_count assertions)"
  exit 0
fi
echo "$fail FAILED of $((fail + pass_count)) assertions" >&2
exit 1
