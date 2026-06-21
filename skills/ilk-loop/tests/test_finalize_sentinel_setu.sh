#!/usr/bin/env bash
# Regression test: finalize_sentinel must be safe under `set -u` when
# runtime_dir is unset (the all-shipped quick-exit path).
#
# Escaped bug: commit 16ba51f fixed `runtime_dir: unbound variable` in the
# EXIT trap by default-expanding to `${runtime_dir:-}`.  This test guards
# against that fix being reverted.
#
# Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
RUNNER="$SCRIPT_DIR/../scripts/run_ilk_loop_claude.sh"

failures=()

# --- Dot-source the runner (defines functions, does not run main) ---
export ILK_DOTSOURCE_ONLY=1
# shellcheck source=/dev/null
set +e
source "$RUNNER" 2>/dev/null
source_rc=$?
set -e
if [[ "$source_rc" -ne 0 ]]; then
  echo "FAIL: sourcing run_ilk_loop_claude.sh exited $source_rc" >&2
  exit 1
fi
unset ILK_DOTSOURCE_ONLY

# --- AC-1: finalize_sentinel is defined as a function ---
if [[ "$(type -t finalize_sentinel)" != "function" ]]; then
  failures+=("AC-1: finalize_sentinel not defined after sourcing runner")
fi

# --- AC-2: finalize_sentinel is set -u safe when runtime_dir is unset ---
# Ensure runtime_dir is unset (simulates the post-main EXIT-trap scenario).
unset runtime_dir 2>/dev/null || true

# Invoke under set -u; capture stderr separately from stdout.
set +e
err="$( set -u; finalize_sentinel 2>&1 1>/dev/null )"
rc=$?
set -e

if echo "$err" | grep -qi "unbound variable"; then
  failures+=("AC-2: finalize_sentinel emitted 'unbound variable' under set -u (stderr: '$err')")
fi

if [[ "$rc" -ne 0 ]]; then
  failures+=("AC-2: finalize_sentinel returned $rc instead of 0 when runtime_dir is unset")
fi

# --- Verdict ---
if [[ ${#failures[@]} -gt 0 ]]; then
  for f in "${failures[@]}"; do
    echo "FAIL: $f" >&2
  done
  exit 1
fi

echo "PASS: finalize_sentinel set -u safe when runtime_dir is unset"
exit 0
