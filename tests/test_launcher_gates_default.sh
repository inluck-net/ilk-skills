#!/usr/bin/env bash
set -euo pipefail

# Verify that launch.sh defaults --run-local-checks ON when queued sub-plans
# declare local_checks, and that --no-local-checks suppresses it.
#
# Hermetic: uses a temp project dir with fixture plans. launch.sh --dry-run
# never spawns anything.
#
# Acceptance criteria:
#   AC-1: sub-plan with local_checks → dry-run shows LocalChecks: ON
#   AC-2: --no-local-checks suppresses → LocalChecks: OFF
#   AC-3: sub-plan without local_checks → LocalChecks: OFF (no false default-on)
#   AC-5: launch.ps1 has mirrored NoLocalChecks + RunLocalChecks (structural grep)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
LAUNCH_SH="${REPO_ROOT}/skills/ilk-launcher/scripts/launch.sh"
LAUNCH_PS1="${REPO_ROOT}/skills/ilk-launcher/scripts/launch.ps1"

PASS=0
FAIL=0

check() {
  # check "<description>" "<haystack>" contains|absent "<needle>"
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

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Fixture 1: sub-plan WITH local_checks ------------------------------------

PROJECT_GATES="${TMP}/project-gates"
PLANS_GATES="${PROJECT_GATES}/docs/plans"
mkdir -p "$PLANS_GATES"

cat > "${PLANS_GATES}/MASTER-2026-01-01-test.md" <<'EOF'
---
master_plan: 2026-01-01-test
batch_date: 2026-01-01
status: active
---

# Test master

| # | Sub-plan | Steps |
|---|---|---|
| 1 | [2026-01-01-with-gates.md](./2026-01-01-with-gates.md) | 3 |
EOF

cat > "${PLANS_GATES}/2026-01-01-with-gates.md" <<'EOF'
---
plan: 2026-01-01-with-gates
status: pending
current_step: 0
estimated_steps: 3
local_checks:
  - command: echo "gate1"
    timeout: 30
---

# Sub-plan with gates
EOF

# Initialize a git repo so ilk_paths can find the project root
cd "$PROJECT_GATES"
git init -q
git add .
git commit -q -m "init"

echo ""
echo "=== AC-1: sub-plan with local_checks → Gates: ON, LocalChecks: ON ==="
out_gates="$(bash "$LAUNCH_SH" --dry-run --project-path "$PROJECT_GATES" 2>&1)"
check "AC-1 banner shows Gates: ON"       "$out_gates" contains "Gates: ON"
check "AC-1 dry-run shows LocalChecks: ON" "$out_gates" contains "LocalChecks: ON"

echo ""
echo "=== AC-2: --no-local-checks suppresses → LocalChecks: OFF ==="
out_nocheck="$(bash "$LAUNCH_SH" --dry-run --project-path "$PROJECT_GATES" --no-local-checks 2>&1)"
check "AC-2 no Gates: ON banner"              "$out_nocheck" absent  "Gates: ON"
check "AC-2 dry-run shows LocalChecks: OFF"    "$out_nocheck" contains "LocalChecks: OFF"

# --- Fixture 2: sub-plan WITHOUT local_checks ---------------------------------

PROJECT_NOGATES="${TMP}/project-nogates"
PLANS_NOGATES="${PROJECT_NOGATES}/docs/plans"
mkdir -p "$PLANS_NOGATES"

cat > "${PLANS_NOGATES}/MASTER-2026-01-01-test.md" <<'EOF'
---
master_plan: 2026-01-01-test
batch_date: 2026-01-01
status: active
---

# Test master

| # | Sub-plan | Steps |
|---|---|---|
| 1 | [2026-01-01-no-gates.md](./2026-01-01-no-gates.md) | 2 |
EOF

cat > "${PLANS_NOGATES}/2026-01-01-no-gates.md" <<'EOF'
---
plan: 2026-01-01-no-gates
status: pending
current_step: 0
estimated_steps: 2
---

# Sub-plan without gates
EOF

cd "$PROJECT_NOGATES"
git init -q
git add .
git commit -q -m "init"

echo ""
echo "=== AC-3: sub-plan without local_checks → LocalChecks: OFF (no false default-on) ==="
out_nogates="$(bash "$LAUNCH_SH" --dry-run --project-path "$PROJECT_NOGATES" 2>&1)"
check "AC-3 no Gates: ON banner"              "$out_nogates" absent  "Gates: ON"
check "AC-3 dry-run shows LocalChecks: OFF"    "$out_nogates" contains "LocalChecks: OFF"

echo ""
echo "=== AC-5: launch.ps1 has mirrored NoLocalChecks + RunLocalChecks (structural) ==="
ps1="$(cat "$LAUNCH_PS1")"
check "AC-5 ps1 declares NoLocalChecks param"  "$ps1" contains 'NoLocalChecks'
check "AC-5 ps1 has RunLocalChecks param"      "$ps1" contains 'RunLocalChecks'
check "AC-5 ps1 has Test-QueuedSubplansDeclareLocalChecks" "$ps1" contains 'Test-QueuedSubplansDeclareLocalChecks'
check "AC-5 ps1 has Gates: ON banner"          "$ps1" contains 'Gates: ON'

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
