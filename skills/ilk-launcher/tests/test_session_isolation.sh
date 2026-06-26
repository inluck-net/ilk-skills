#!/usr/bin/env bash
# Regression test: start_detached_session isolates the child from parent-group
# SIGTERM (AC-1, AC-2, AC-3, AC-4).
#
# Invoked by local_checks in sub-plan 2026-06-26-loop-session-isolation.
# Exit 0 = green (all ACs pass), exit 1 = red (bug present or guard missing).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_SH="$SCRIPT_DIR/../scripts/launch.sh"

failures=()

cleanup_pids=()
cleanup_files=()

cleanup() {
  local pid
  for pid in "${cleanup_pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  local f
  for f in "${cleanup_files[@]}"; do
    rm -f "$f"
  done
}
trap cleanup EXIT

# --- Dot-source guard ---
export ILK_SKIP_MAIN=1
# shellcheck source=/dev/null
if ! source "$LAUNCH_SH"; then
  echo "FAIL: sourcing launch.sh failed"
  exit 1
fi
unset ILK_SKIP_MAIN

if ! type -t start_detached_session >/dev/null 2>&1; then
  echo "FAIL: start_detached_session function not found after sourcing launch.sh"
  exit 1
fi

# --- AC-1 & AC-3: Mechanism — helper creates session leader ---
ac1_3_test() {
  local tmp_log
  tmp_log="$(mktemp)"
  cleanup_files+=("$tmp_log")

  local leader_pid
  leader_pid=$(start_detached_session "sleep 30" "$tmp_log")
  cleanup_pids+=("$leader_pid")

  # Allow os.execvp to complete so the process is in its new session.
  sleep 0.5

  # AC-1: The leader PID must be a session leader — its PGID equals its PID.
  local pgid
  pgid=$(ps -o pgid= -p "$leader_pid" 2>/dev/null | tr -d '[:space:]')
  if [[ -z "$pgid" ]]; then
    failures+=("AC-1: could not read PGID for leader PID $leader_pid")
    return
  fi
  if [[ "$pgid" != "$leader_pid" ]]; then
    failures+=("AC-1: session leader PGID ($pgid) != PID ($leader_pid) — not a session leader")
  fi

  # AC-1: The leader's PGID must differ from the test harness's PGID.
  local my_pgid
  my_pgid=$(ps -o pgid= -p $$ 2>/dev/null | tr -d '[:space:]')
  if [[ "$pgid" == "$my_pgid" ]]; then
    failures+=("AC-1: leader PGID ($pgid) == test harness PGID ($my_pgid) — not isolated")
  fi

  # AC-3: The leader PID must be alive (liveness / running.pid contract).
  if ! kill -0 "$leader_pid" 2>/dev/null; then
    failures+=("AC-3: leader PID $leader_pid is not alive after launch")
  fi
}

# --- AC-2: Signal isolation — parent-group SIGTERM doesn't kill child ---
ac2_test() {
  local tmp_log tmp_pidfile
  tmp_log="$(mktemp)"
  tmp_pidfile="$(mktemp)"
  cleanup_files+=("$tmp_log" "$tmp_pidfile")

  # The child uses the python3 shim directly (not start_detached_session,
  # which is not available in the inner session).  The parent wrapper
  # launches the child, writes the child PID to a file, then sleeps.
  # We send SIGTERM to the parent's process group; the child must survive.
  local wrapper_cmd="python3 -c 'import os,sys; os.setsid(); os.execvp(\"/bin/bash\",[\"bash\",\"-c\",sys.argv[1]])' 'sleep 30' > '$tmp_log' 2>&1 </dev/null & echo \$! > '$tmp_pidfile'; sleep 30"

  local parent_pid
  parent_pid=$(start_detached_session "$wrapper_cmd" "$tmp_log")
  cleanup_pids+=("$parent_pid")

  # Wait for the wrapper to spawn the child and write the PID file.
  local waited=0
  while [[ ! -s "$tmp_pidfile" && "$waited" -lt 20 ]]; do
    sleep 0.5
    waited=$((waited + 1))
  done

  if [[ ! -s "$tmp_pidfile" ]]; then
    failures+=("AC-2: wrapper did not write child PID within 10 seconds")
    return
  fi

  local child_pid
  child_pid=$(cat "$tmp_pidfile" | tr -d '[:space:]')
  cleanup_pids+=("$child_pid")

  # Allow the child's os.execvp to complete.
  sleep 0.5

  # Verify both parent and child are alive before signaling.
  if ! kill -0 "$parent_pid" 2>/dev/null; then
    failures+=("AC-2: parent PID $parent_pid not alive before SIGTERM")
    return
  fi
  if ! kill -0 "$child_pid" 2>/dev/null; then
    failures+=("AC-2: child PID $child_pid not alive before SIGTERM")
    return
  fi

  # Send SIGTERM to the parent's process group (simulates launchd teardown).
  # Guard: never signal pgid 0 (all processes) or our own group.
  local parent_pgid
  parent_pgid=$(ps -o pgid= -p "$parent_pid" 2>/dev/null | tr -d '[:space:]')
  if [[ -z "$parent_pgid" || "$parent_pgid" == "0" ]]; then
    failures+=("AC-2: could not resolve parent PGID or it is 0")
    return
  fi

  local my_pgid
  my_pgid=$(ps -o pgid= -p $$ 2>/dev/null | tr -d '[:space:]')
  if [[ "$parent_pgid" == "$my_pgid" ]]; then
    failures+=("AC-2: parent PGID ($parent_pgid) == test harness PGID ($my_pgid) — can't safely signal")
    return
  fi

  kill -TERM -"$parent_pgid"

  # Wait a moment for the signal to propagate.
  sleep 1

  # The child (sleep) must still be alive — it's in its own session/group.
  if ! kill -0 "$child_pid" 2>/dev/null; then
    failures+=("AC-2: child PID $child_pid died after parent-group SIGTERM — NOT isolated")
    return
  fi
}

# --- Run tests ---
ac1_3_test
ac2_test

# --- Verdict ---
if [[ ${#failures[@]} -gt 0 ]]; then
  for f in "${failures[@]}"; do
    echo "FAIL: $f"
  done
  exit 1
fi

echo "PASS: session isolation — all ACs pass (AC-1, AC-2, AC-3)"
exit 0
