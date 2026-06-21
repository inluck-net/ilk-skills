#!/usr/bin/env bash
# Red test: preflight_decision must block unsafe supervised-only launches,
# promote queued masters, and reject draft masters.
#
# Invoked by local_checks in sub-plan 2026-06-16-ilk-runner-preflight.
# Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFLIGHT="$SCRIPT_DIR/../scripts/preflight.sh"

failures=()

# Helper: extract value for a key from newline-separated "key=value" format
extract_key() {
  local text="$1" key="$2"
  echo "$text" | grep "^${key}=" | sed "s/^${key}=//"
}

# --- Dot-source guard ---
export ILK_DOTSOURCE_ONLY=1
# shellcheck source=/dev/null
if ! source "$PREFLIGHT"; then
  echo "FAIL: sourcing preflight.sh failed"
  exit 1
fi
unset ILK_DOTSOURCE_ONLY

if ! type -t preflight_decision >/dev/null 2>&1; then
  echo "FAIL: preflight_decision function not found after sourcing preflight.sh"
  exit 1
fi

# --- Decision matrix ---

# AC-1: supervised + scheduler alive → block
result=$(preflight_decision "active" "true" "true" "true")
block=$(extract_key "$result" "block")
reason=$(extract_key "$result" "reason")
if [[ "$block" != "true" ]]; then
  failures+=("AC-1a: supervised+alive: expected block=true, got block=$block")
fi
if [[ "$reason" != *"scheduler"* ]]; then
  failures+=("AC-1a: supervised+alive: reason should mention scheduler, got '$reason'")
fi

# AC-1: supervised + scheduler not alive → no block
result=$(preflight_decision "active" "true" "true" "false")
block=$(extract_key "$result" "block")
if [[ "$block" != "false" ]]; then
  failures+=("AC-1b: supervised+not-alive: expected block=false, got block=$block")
fi

# AC-2: queued + no active → promote
result=$(preflight_decision "queued" "false" "false" "false")
promote=$(extract_key "$result" "promote")
block=$(extract_key "$result" "block")
if [[ "$promote" != "true" ]]; then
  failures+=("AC-2a: queued+no-active: expected promote=true, got promote=$promote")
fi
if [[ "$block" != "false" ]]; then
  failures+=("AC-2a: queued+no-active: expected block=false, got block=$block")
fi

# AC-2: draft → block (held)
result=$(preflight_decision "draft" "false" "false" "false")
block=$(extract_key "$result" "block")
reason=$(extract_key "$result" "reason")
if [[ "$block" != "true" ]]; then
  failures+=("AC-2b: draft: expected block=true, got block=$block")
fi
if [[ "$reason" != *"draft"* ]]; then
  failures+=("AC-2b: draft: reason should mention draft, got '$reason'")
fi

# Not-supervised + scheduler alive → no block
result=$(preflight_decision "active" "true" "false" "true")
block=$(extract_key "$result" "block")
if [[ "$block" != "false" ]]; then
  failures+=("non-supervised+alive: expected block=false, got block=$block")
fi

# queued + already has active → no promote, no block
result=$(preflight_decision "queued" "true" "false" "false")
promote=$(extract_key "$result" "promote")
block=$(extract_key "$result" "block")
if [[ "$promote" != "false" ]]; then
  failures+=("queued+has-active: expected promote=false, got promote=$promote")
fi
if [[ "$block" != "false" ]]; then
  failures+=("queued+has-active: expected block=false, got block=$block")
fi

# --- macOS portability regression (escaped bug 086a533f) ---
# These assertions lock the three macOS fixes from commit 3f016d6:
#   1. No grep -P (BSD grep rejects it)
#   2. No basename-built plans_dir (wrong key for external plans)
#   3. No literal \s in grep (BSD treats it literally; use [[:space:]])

# Portability-1: no grep -P usage
if grep -nE 'grep +-[a-zA-Z]*P' "$PREFLIGHT" >/dev/null 2>&1; then
  failures+=("portability: grep -P found in preflight.sh — BSD/macOS grep rejects -P")
fi

# Portability-2: plans_dir must NOT be built from basename
if grep -nE 'plans_dir=.*basename' "$PREFLIGHT" >/dev/null 2>&1; then
  failures+=("portability: plans_dir derived from basename — wrong key for external plans")
fi

# Portability-2b: plans_dir MUST be derived from loop_status "Plans dir:" output
if ! grep -q 'Plans dir:' "$PREFLIGHT"; then
  failures+=("portability: preflight.sh does not derive plans_dir from loop_status 'Plans dir:' output")
fi

# Portability-3: no literal \s in supervised-detection grep patterns
if grep -nE 'supervised_only:\\s|status:\\s' "$PREFLIGHT" >/dev/null 2>&1; then
  failures+=("portability: literal \\s in grep pattern — BSD treats it literally; use [[:space:]]")
fi

# --- Verdict ---
if [[ ${#failures[@]} -gt 0 ]]; then
  for f in "${failures[@]}"; do
    echo "FAIL: $f"
  done
  exit 1
fi

echo "PASS: preflight_decision — all decision matrix cases correct"
exit 0
