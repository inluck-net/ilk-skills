#!/usr/bin/env bash
# test_scrub_github_artifact.sh — acceptance tests for the scrub gate + PR template.
#
# Covers AC-1..AC-6 from sub-plan 2026-06-09-gap5-gh-scrub-gate-and-pr-template.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GATE="$SCRIPT_DIR/skills/ilk-loop/scripts/scrub-github-artifact.sh"
TEMPLATE="$SCRIPT_DIR/skills/ilk-loop/templates/pr-body-template.md"
SKILL="$SCRIPT_DIR/skills/ilk-loop/SKILL.md"

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

# ── AC-1: denylist tokens are detected (case-insensitive, word-boundary) ─────
echo "AC-1: denylist token detection"

TOKENS=(
  "ilk"
  "ilk-plan"
  "ilk-skills"
  "ilk-loop"
  "ilk-launcher"
  "ilk-run"
  "MASTER plan"
  "sub-plan"
  "sub plan"
  "RunLocalChecks"
  "local_checks"
  "decomposition-principles"
)

for token in "${TOKENS[@]}"; do
  if echo "This contains $token here" | bash "$GATE" /dev/stdin >/dev/null 2>&1; then
    die "token '$token' not detected"
  else
    ok "token '$token' detected"
  fi
done

# Case-insensitive: uppercase variant
if echo "This contains ILK-LOOP here" | bash "$GATE" /dev/stdin >/dev/null 2>&1; then
  die "case-insensitive: 'ILK-LOOP' not detected"
else
  ok "case-insensitive: 'ILK-LOOP' detected"
fi

# ── AC-2: exit code + offending lines ────────────────────────────────────────
echo "AC-2: exit code and offending lines"

output=$(echo "This uses the ilk-loop runner" | bash "$GATE" /dev/stdin 2>&1 || true)
if echo "$output" | grep -q "FAIL"; then
  ok "dirty text exits non-zero with FAIL message"
else
  die "dirty text did not produce FAIL message"
fi

if echo "$output" | grep -q "ilk"; then
  ok "offending line printed"
else
  die "offending line not printed"
fi

if echo "clean text here" | bash "$GATE" /dev/stdin >/dev/null 2>&1; then
  ok "clean text exits 0"
else
  die "clean text exits non-zero"
fi

# ── AC-3: word-boundary + skip-list (false-positive control) ─────────────────
echo "AC-3: false-positive handling"

DECOYS=("silk" "milk" "bilk" "milks" "silks" "silky" "milky" "bilks")
for word in "${DECOYS[@]}"; do
  if echo "This is about $word fabric" | bash "$GATE" /dev/stdin >/dev/null 2>&1; then
    ok "decoy '$word' not flagged (correct)"
  else
    die "decoy '$word' incorrectly flagged"
  fi
done

# ── AC-4: trailer pattern ───────────────────────────────────────────────────
echo "AC-4: trailer pattern [plan:…]"

if echo "[plan:foo#step-0]" | bash "$GATE" /dev/stdin >/dev/null 2>&1; then
  die "trailer [plan:foo#step-0] not detected"
else
  ok "trailer [plan:foo#step-0] detected"
fi

if echo "feat(x): something [plan:my-slug#step-3]" | bash "$GATE" /dev/stdin >/dev/null 2>&1; then
  die "trailer in commit message not detected"
else
  ok "trailer in commit message detected"
fi

# ── AC-5: PR-body template has What/Why/Testing, no "How it was built" ──────
echo "AC-5: PR-body template structure"

if [[ -f "$TEMPLATE" ]]; then
  ok "template file exists"
else
  die "template file missing at $TEMPLATE"
fi

if grep -q "## What" "$TEMPLATE"; then
  ok "template has '## What' section"
else
  die "template missing '## What' section"
fi

if grep -q "## Why" "$TEMPLATE"; then
  ok "template has '## Why' section"
else
  die "template missing '## Why' section"
fi

if grep -q "## Testing" "$TEMPLATE"; then
  ok "template has '## Testing' section"
else
  die "template missing '## Testing' section"
fi

if grep -qi "How it was built" "$TEMPLATE"; then
  die "template contains 'How it was built' (should not)"
else
  ok "template does not contain 'How it was built'"
fi

# ── AC-6: SKILL.md wires gate + template ────────────────────────────────────
echo "AC-6: SKILL.md integration"

if grep -q "scrub-github-artifact" "$SKILL"; then
  ok "SKILL.md references scrub-github-artifact"
else
  die "SKILL.md missing scrub-github-artifact reference"
fi

if grep -q "pr-body-template" "$SKILL"; then
  ok "SKILL.md references pr-body-template"
else
  die "SKILL.md missing pr-body-template reference"
fi

# ── summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: ${pass} passed, ${fail} failed"
if [[ "$fail" -gt 0 ]]; then
  exit 1
else
  echo "All tests passed."
  exit 0
fi
