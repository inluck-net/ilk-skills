#!/usr/bin/env bash
# =============================================================================
# Test: scheduler.sh lock-contention exits 0 cleanly.
# =============================================================================
# With a pre-written scheduler.pid pointing at a live PID, running
# scheduler.sh must print "already running" and exit 0. This pins the
# contract that the autostart KeepAlive={SuccessfulExit:false} correctly
# suppresses relaunch on lock contention.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCHEDULER="$REPO_ROOT/skills/ilk-watchdog/scripts/scheduler.sh"

failures=()

fail() { failures+=("$1"); }

# --- setup: temp HOME so the pidfile lands in a controlled location -----------
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"; kill "$live_pid" 2>/dev/null || true' EXIT

# Write a live PID to the pidfile.
live_pid_file="$TMPDIR_TEST/.ilk-data/scheduler.pid"
mkdir -p "$(dirname "$live_pid_file")"
sleep 300 &
live_pid=$!
echo "$live_pid" > "$live_pid_file"

# --- test: lock-contention must exit 0 and print "already running" ------------
output=$(HOME="$TMPDIR_TEST" bash "$SCHEDULER" --once --dry-run 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  fail "expected exit 0 on lock contention, got exit $rc"
fi

if [[ "$output" != *"already running"* ]]; then
  fail "expected 'already running' in output, got: $output"
fi

# --- report -------------------------------------------------------------------
if [[ "${#failures[@]}" -gt 0 ]]; then
  echo "FAIL — ${#failures[@]} failure(s):"
  for f in "${failures[@]}"; do
    echo "  - $f"
  done
  exit 1
else
  echo "OK — lock-contention exits 0 cleanly."
  exit 0
fi
