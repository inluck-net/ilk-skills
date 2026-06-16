#!/usr/bin/env bash
# Red test: preflight_decision must block unsafe supervised-only launches,
# promote queued masters, and reject draft masters.
#
# Invoked by local_checks in sub-plan 2026-06-16-ilk-runner-preflight.
# Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PREFLIGHT="$REPO_ROOT/skills/ilk-runner/scripts/preflight.sh"

failures=()

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
block=$(echo "$result" | grep -oP 'block=\K\w+')
reason=$(echo "$result" | grep -oP 'reason=\K.*' || true)
if [[ "$block" != "true" ]]; then
  failures+=("AC-1a: supervised+alive: expected block=true, got block=$block")
fi
if [[ "$reason" != *"scheduler"* ]]; then
  failures+=("AC-1a: supervised+alive: reason should mention scheduler, got '$reason'")
fi

# AC-1: supervised + scheduler not alive → no block
result=$(preflight_decision "active" "true" "true" "false")
block=$(echo "$result" | grep -oP 'block=\K\w+')
if [[ "$block" != "false" ]]; then
  failures+=("AC-1b: supervised+not-alive: expected block=false, got block=$block")
fi

# AC-2: queued + no active → promote
result=$(preflight_decision "queued" "false" "false" "false")
promote=$(echo "$result" | grep -oP 'promote=\K\w+')
block=$(echo "$result" | grep -oP 'block=\K\w+')
if [[ "$promote" != "true" ]]; then
  failures+=("AC-2a: queued+no-active: expected promote=true, got promote=$promote")
fi
if [[ "$block" != "false" ]]; then
  failures+=("AC-2a: queued+no-active: expected block=false, got block=$block")
fi

# AC-2: draft → block (held)
result=$(preflight_decision "draft" "false" "false" "false")
block=$(echo "$result" | grep -oP 'block=\K\w+')
reason=$(echo "$result" | grep -oP 'reason=\K.*' || true)
if [[ "$block" != "true" ]]; then
  failures+=("AC-2b: draft: expected block=true, got block=$block")
fi
if [[ "$reason" != *"draft"* ]]; then
  failures+=("AC-2b: draft: reason should mention draft, got '$reason'")
fi

# Not-supervised + scheduler alive → no block
result=$(preflight_decision "active" "true" "false" "true")
block=$(echo "$result" | grep -oP 'block=\K\w+')
if [[ "$block" != "false" ]]; then
  failures+=("non-supervised+alive: expected block=false, got block=$block")
fi

# queued + already has active → no promote, no block
result=$(preflight_decision "queued" "true" "false" "false")
promote=$(echo "$result" | grep -oP 'promote=\K\w+')
block=$(echo "$result" | grep -oP 'block=\K\w+')
if [[ "$promote" != "false" ]]; then
  failures+=("queued+has-active: expected promote=false, got promote=$promote")
fi
if [[ "$block" != "false" ]]; then
  failures+=("queued+has-active: expected block=false, got block=$block")
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
