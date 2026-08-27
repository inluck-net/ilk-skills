#!/usr/bin/env bash
# =============================================================================
# Test: scheduler.sh lock-contention exits 0 cleanly — but only for a real
#       scheduler, not for whatever happens to hold a recycled PID.
# =============================================================================
# Case 1: scheduler.pid points at a live *scheduler* process -> "already
#   running", exit 0. This pins the contract that the autostart
#   KeepAlive={SuccessfulExit:false} correctly suppresses relaunch on
#   lock contention.
#
# Case 2: scheduler.pid points at a live process that is NOT an ilk process
#   (a recycled PID). This must NOT read as contention. Bare `kill -0` said
#   "busy" here, and because KeepAlive is SuccessfulExit=false the resulting
#   exit-0 meant launchd never relaunched — the scheduler stayed dead.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCHEDULER="$REPO_ROOT/skills/ilk-watchdog/scripts/scheduler.sh"

failures=()

fail() { failures+=("$1"); }

# --- setup: temp HOME so the pidfile lands in a controlled location -----------
TMPDIR_TEST="$(mktemp -d)"
sched_pid=""
plain_pid=""
# shellcheck disable=SC2064
trap 'rm -rf "$TMPDIR_TEST"; kill "$sched_pid" "$plain_pid" 2>/dev/null || true' EXIT

# Sandbox: pin HOME + ILK_DATA_HOME to a temp root (AC-1..AC-3).
source "$REPO_ROOT/skills/ilk-loop/scripts/_ilk_test_sandbox.sh"
ilk_test_sandbox "$TMPDIR_TEST"

pid_file="$TMPDIR_TEST/.ilk-data/scheduler.pid"
mkdir -p "$(dirname "$pid_file")"

# --- case 1: a genuine scheduler holds the lock -------------------------------
# The stand-in must be named scheduler.sh: the guard verifies the command line,
# so a bare `sleep` no longer counts as a live scheduler (that is case 2).
stub_dir="$TMPDIR_TEST/stub"
mkdir -p "$stub_dir"
printf '#!/usr/bin/env bash\nsleep 300\n' > "$stub_dir/scheduler.sh"
bash "$stub_dir/scheduler.sh" &
sched_pid=$!
echo "$sched_pid" > "$pid_file"

output=$(timeout 60 bash "$SCHEDULER" --once --dry-run 2>&1) && rc=0 || rc=$?

if [[ "$rc" -ne 0 ]]; then
  fail "case 1: expected exit 0 on lock contention, got exit $rc"
fi

if [[ "$output" != *"already running"* ]]; then
  fail "case 1: expected 'already running' in output, got: $output"
fi

# The holder's pidfile must survive — the contending instance must not clear it.
if [[ "$(cat "$pid_file" 2>/dev/null)" != "$sched_pid" ]]; then
  fail "case 1: pidfile was overwritten by the contending instance"
fi

# --- case 2: a recycled PID (live, but not an ilk process) --------------------
sleep 300 &
plain_pid=$!
echo "$plain_pid" > "$pid_file"

out2=$(timeout 60 bash "$SCHEDULER" --once --dry-run 2>&1) && rc2=0 || rc2=$?

if [[ "$out2" == *"already running"* ]]; then
  fail "case 2: recycled PID $plain_pid read as lock contention — the scheduler would stay dead (got: $out2)"
fi

if [[ "$rc2" -ne 0 ]]; then
  fail "case 2: expected the scheduler to proceed and exit 0, got exit $rc2 (output: $out2)"
fi

# Proceeding means it took the lock for itself, replacing the stale entry.
if [[ "$(cat "$pid_file" 2>/dev/null)" == "$plain_pid" ]]; then
  fail "case 2: stale pidfile still names the recycled PID $plain_pid"
fi

# --- report -------------------------------------------------------------------
if [[ "${#failures[@]}" -gt 0 ]]; then
  echo "FAIL — ${#failures[@]} failure(s):"
  for f in "${failures[@]}"; do
    echo "  - $f"
  done
  exit 1
else
  echo "OK — contention holds for a real scheduler; a recycled PID does not wedge it."
  exit 0
fi
