#!/usr/bin/env bash
# test_qc_wholeproject_gate_lint.sh — acceptance tests for autonomy-tiering
# and gate-scoping docs/QC.
#
# Covers AC-1..AC-4 from sub-plan 2026-06-09-docs-autonomy-tiering-and-gate-scoping.
#
# The QC §7a whole-project-gate check is doc-only guidance (not an executable
# lint script). This test asserts the guidance text + example patterns exist.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DECOMP="$SCRIPT_DIR/skills/ilk-loop/references/decomposition-principles.md"
ILK_PLAN="$SCRIPT_DIR/commands/ilk-plan.md"

pass=0
fail=0

ok() {
  pass=$((pass + 1))
  echo "  PASS: $1"
}

die() {
  fail=$((fail + 1))
  echo "  FAIL: $1"
}

# ── AC-1: decomposition-principles.md gains an "Autonomy tiers" section ──────
echo "AC-1: Autonomy tiers section"

if grep -q "## 15. Autonomy tiers" "$DECOMP"; then
  ok "§15 'Autonomy tiers' heading exists"
else
  die "§15 'Autonomy tiers' heading missing"
fi

if grep -qi "agent-auto-apply" "$DECOMP" && grep -qi "agent-plans-human-approve" "$DECOMP" && grep -qi "human-only" "$DECOMP"; then
  ok "all 3 tiers named (auto-apply / plan-approve / human-only)"
else
  die "one or more tier names missing"
fi

# ── AC-2: gate-scoping rule documented ───────────────────────────────────────
echo "AC-2: Gate-scoping rule"

if grep -q "## 16. Gate-scoping" "$DECOMP"; then
  ok "§16 'Gate-scoping' heading exists"
else
  die "§16 'Gate-scoping' heading missing"
fi

if grep -qi "change-scoped" "$DECOMP"; then
  ok "'change-scoped' mentioned"
else
  die "'change-scoped' not found"
fi

if grep -qi "baseline" "$DECOMP"; then
  ok "'baseline' mentioned"
else
  die "'baseline' not found"
fi

if grep -qi "whole-project" "$DECOMP"; then
  ok "'whole-project' mentioned in decomposition-principles"
else
  die "'whole-project' not found in decomposition-principles"
fi

# ── AC-3: QC §7a warns on whole-project-only compile gate ───────────────────
echo "AC-3: QC §7a whole-project-only warning"

if grep -qi "whole-project" "$ILK_PLAN"; then
  ok "'whole-project' mentioned in ilk-plan.md QC §7a"
else
  die "'whole-project' not found in ilk-plan.md"
fi

# Verify the example compile patterns are listed
PATTERNS=("tsc" "mypy" "cargo build" "npm run build" "bun run typecheck")
missing=()
for p in "${PATTERNS[@]}"; do
  if ! grep -qF "$p" "$ILK_PLAN"; then
    missing+=("$p")
  fi
done

if [ ${#missing[@]} -eq 0 ]; then
  ok "all 5 example compile patterns listed in QC §7a"
else
  die "missing patterns in QC §7a: ${missing[*]}"
fi

# ── AC-4: guidance-text assertion (doc-only lint form) ───────────────────────
echo "AC-4: Guidance-text completeness"

# The decision from step 0: lint is doc-only guidance, not executable.
# Assert the guidance text mentions the baseline-verify requirement.
if grep -qi "baseline.*green\|green.*baseline\|baseline.*commit\|confirm.*baseline" "$DECOMP"; then
  ok "baseline-verify requirement documented"
else
  die "baseline-verify requirement not found"
fi

# Assert the QC warning text exists in ilk-plan.md
if grep -qi "baseline.*false-block\|false-block.*baseline\|pre-existing.*error" "$ILK_PLAN"; then
  ok "false-blocking rationale documented in QC §7a"
else
  die "false-blocking rationale not found in QC §7a"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $pass passed, $fail failed"

if [ "$fail" -gt 0 ]; then
  exit 1
fi
echo "All checks passed."
