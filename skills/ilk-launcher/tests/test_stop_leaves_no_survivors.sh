#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Test: stop.sh leaves no survivors (and does not kill itself)
# =============================================================================
# Reproduces the defect from ticket ebe0aebfc2ff4f82:
#   - stop.sh's orphan scan kills its own wrapper (exit 144)
#   - stop.sh reports "stopped." while descendant processes survive
#   - The match predicate catches any process whose argv contains the
#     project path, not just runner processes
#
# Gate: KNOWN_BAD=1 until step 2 flips it.
#
# Usage: bash test_stop_leaves_no_survivors.sh
# Requires: python3, lsof
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STOP_SCRIPT="${SCRIPT_DIR}/../scripts/stop.sh"
SKILL_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PASS=0
FAIL=0
TESTS=()

# Gate: set to 1 while stop.sh is known-broken.  Step 2 flips this to 0.
KNOWN_BAD="${KNOWN_BAD:-1}"

pass() { PASS=$((PASS + 1)); TESTS+=("PASS: $1"); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); TESTS+=("FAIL: $1"); echo "  FAIL: $1"; }

# ----- Setup -----------------------------------------------------------------

WORK_TMPDIR=$(mktemp -d)
trap 'rm -rf "$WORK_TMPDIR"' EXIT

PROJECT_DIR="${WORK_TMPDIR}/test-project-stop"
mkdir -p "$PROJECT_DIR"
(cd "$PROJECT_DIR" && git init -q && git commit -q --allow-empty -m "init")

# External runtime dirs
PROJECT_KEY="test-project-stop"
LAUNCHER_DIR="${WORK_TMPDIR}/ilk-data/projects/${PROJECT_KEY}/runtime/launcher"
RUNTIME_DIR="${WORK_TMPDIR}/ilk-data/projects/${PROJECT_KEY}/runtime"
mkdir -p "$LAUNCHER_DIR"

# Write sentinel
cat > "${RUNTIME_DIR}/last-exit.json" <<SENTINEL_JSON
{
  "state": "running",
  "pid": 99999,
  "run_id": "20260812-150000",
  "started_at": "2026-08-12T15:00:00+0800",
  "project_path": "${PROJECT_DIR}",
  "cli": "claude"
}
SENTINEL_JSON

# Write last-launch.json (run_id matches what we put in child argv)
cat > "${LAUNCHER_DIR}/last-launch.json" <<LAUNCH_JSON
{
  "project_path": "${PROJECT_DIR}",
  "project_name": "${PROJECT_KEY}",
  "pid": 99999,
  "started_at": "2026-08-12T15:00:00+0800",
  "log_file": "${WORK_TMPDIR}/logs/launcher/${PROJECT_KEY}-20260812-150000.log"
}
LAUNCH_JSON

# ----- Mock infrastructure ---------------------------------------------------
# Follows the same pattern as test_stop.sh: mock _ilk_skill_root.sh and
# ilk_paths.py so stop.sh resolves paths into our temp tree.

MOCK_SKILL_ROOT="${WORK_TMPDIR}/skill-root"
mkdir -p "${MOCK_SKILL_ROOT}/ilk-loop/scripts"
mkdir -p "${MOCK_SKILL_ROOT}/ilk-launcher/scripts"
mkdir -p "${MOCK_SKILL_ROOT}/ilk-watchdog/scripts"

cp "$STOP_SCRIPT" "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/stop.sh"

if [[ -f "${SCRIPT_DIR}/../scripts/mark_sentinel_interrupted.sh" ]]; then
  cp "${SCRIPT_DIR}/../scripts/mark_sentinel_interrupted.sh" \
    "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/"
fi

cat > "${MOCK_SKILL_ROOT}/ilk-loop/scripts/_ilk_skill_root.sh" <<'ROOT_SH'
ilk_skill_root() {
  echo "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
}
ROOT_SH

cat > "${MOCK_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py" <<'MOCK_PY'
import json, sys, os
args = sys.argv[1:]
data = {
    "project_root": os.environ.get("TEST_PROJECT_PATH", ""),
    "project_key": os.environ.get("TEST_PROJECT_KEY", ""),
    "external_launcher_dir": os.environ.get("TEST_LAUNCHER_DIR", ""),
    "external_watchdog_dir": os.environ.get("TEST_WATCHDOG_DIR", ""),
    "external_runtime_dir": os.environ.get("TEST_RUNTIME_DIR", ""),
}
if "--where" in args:
    for k, v in data.items():
        if v: print(f"{k}: {v}")
else:
    print(json.dumps(data))
MOCK_PY

cat > "${MOCK_SKILL_ROOT}/ilk-watchdog/scripts/stop_watchdog.sh" <<'WD_SH'
#!/usr/bin/env bash
echo "[watchdog-mock] stopped (no-op)" >&2
WD_SH

# ----- Export env for mock resolver ------------------------------------------

export TEST_PROJECT_PATH="$PROJECT_DIR"
export TEST_PROJECT_KEY="$PROJECT_KEY"
export TEST_LAUNCHER_DIR="$LAUNCHER_DIR"
export TEST_WATCHDOG_DIR="${WORK_TMPDIR}/ilk-data/projects/${PROJECT_KEY}/runtime/watchdog"
export TEST_RUNTIME_DIR="$RUNTIME_DIR"

# =============================================================================
# Test 1: stop.sh through a wrapper does not kill the wrapper (AC-1 / AC-7)
# =============================================================================
#
# The real bug: `timeout 120 bash stop.sh --project-path /Us...` had its
# wrapper killed because the orphan scan matched the project path in the
# wrapper's argv.  We reproduce by invoking stop.sh through bash -c.

echo "Test 1: wrapper survival — stop.sh must not kill its own wrapper"

# Spawn a long-lived child that mimics a gtimeout/claude survivor.
# Its argv will contain the run_id so the orphan scan finds it.
CHILD1_PID=""
bash -c "exec -a 'run_ilk_loop_claude 20260812-150000 ${PROJECT_DIR}' sleep 600" &
CHILD1_PID=$!

# Write a PID file pointing at a fake "launcher" PID.
# We use the wrapper's PID as the target so stop.sh tries to kill it.
echo "$CHILD1_PID" > "${LAUNCHER_DIR}/running.pid"

# Give the child a moment to start
sleep 0.3

# Invoke stop.sh through a wrapper whose argv contains the project path.
# This is exactly the shape that got killed: `bash -c 'bash stop.sh ...'`
WRAPPER_EXIT=0
bash -c "bash '${MOCK_SKILL_ROOT}/ilk-launcher/scripts/stop.sh' --project-path '${PROJECT_DIR}'" 2>&1 || WRAPPER_EXIT=$?

# The wrapper (this bash -c) must have survived.  If stop.sh's orphan scan
# killed it, we would not reach here at all — the test harness itself would
# be gone or WRAPPER_EXIT would be 144 (128+SIGTERM/SIGKILL).
# But since we ARE here, the wrapper survived.  Verify the exit was clean.
if [[ "$WRAPPER_EXIT" -eq 0 ]]; then
  pass "wrapper exited cleanly (not killed by orphan scan)"
elif [[ "$WRAPPER_EXIT" -ge 128 ]]; then
  fail "wrapper killed by signal ($WRAPPER_EXIT) — orphan scan hit the wrapper"
else
  # Non-zero but not signal — may be a stop.sh error; still counts as wrapper survived
  pass "wrapper exited $WRAPPER_EXIT (not killed by signal)"
fi

# Clean up child if still alive
kill "$CHILD1_PID" 2>/dev/null || true
wait "$CHILD1_PID" 2>/dev/null || true

# =============================================================================
# Test 2: survivor detection — stop.sh must not report success with live
#         descendants (AC-2 / AC-3)
# =============================================================================
#
# Spawn a tree: a parent process (matching run_ilk_loop_claude pattern)
# that holds run.lock via an open fd, plus a child.  Run stop.sh.
# Assert: either both die, or stop.sh reports failure listing survivors.

echo ""
echo "Test 2: survivor detection — stop.sh must not claim success while descendants live"

# Create a run.lock file
RUN_LOCK="${LAUNCHER_DIR}/run.lock"
touch "$RUN_LOCK"

# Spawn a parent process that:
#   1. Matches the runner pattern (argv contains run_ilk_loop_claude)
#   2. Holds fd 3 open on run.lock
#   3. Spawns a child that also holds fd 3
PARENT_PID=""
bash -c '
  exec 3<"'${RUN_LOCK}'"          # hold run.lock open
  # Spawn a child that also holds the fd
  bash -c "exec 3<\"'${RUN_LOCK}'\"; sleep 600" &
  CHILD_INNER=$!
  # Stay alive as the "runner"
  exec -a "run_ilk_loop_claude 20260812-150000 ${PROJECT_DIR}" sleep 600
' &
PARENT_PID=$!

sleep 0.5

# Write PID file
echo "$PARENT_PID" > "${LAUNCHER_DIR}/running.pid"

# Run stop.sh
STOP_OUTPUT=""
STOP_EXIT=0
STOP_OUTPUT=$(bash "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/stop.sh" \
  --project-path "$PROJECT_DIR" 2>&1) || STOP_EXIT=$?

echo "  stop.sh exit: $STOP_EXIT"
echo "  stop.sh output (last 10 lines):"
echo "$STOP_OUTPUT" | tail -10 | sed 's/^/    /'

# Check if descendants survived.
# lsof on run.lock: if any process still holds it open, survivors exist.
SURVIVORS=""
if command -v lsof &>/dev/null; then
  SURVIVORS=$(lsof -t "$RUN_LOCK" 2>/dev/null || true)
fi

# Also check by process pattern
RUNNER_PIDS=$(pgrep -f "run_ilk_loop_claude.*${PROJECT_KEY}" 2>/dev/null || true)

if [[ -n "$SURVIVORS" || -n "$RUNNER_PIDS" ]]; then
  # Survivors exist.  stop.sh must have reported failure.
  if echo "$STOP_OUTPUT" | grep -qi "surviv\|still alive\|failed\|could not stop"; then
    pass "stop.sh reported failure when survivors remain"
  elif echo "$STOP_OUTPUT" | grep -q "stopped\."; then
    if [[ "$KNOWN_BAD" == "1" ]]; then
      echo "  KNOWN_BAD: stop.sh reported 'stopped.' while survivors exist (expected broken)"
    else
      fail "stop.sh reported 'stopped.' while survivors still hold run.lock"
    fi
  else
    fail "stop.sh output unclear — survivors exist but no success/failure message found"
  fi
else
  pass "no survivors — all descendants killed"
fi

# Clean up
kill "$PARENT_PID" 2>/dev/null || true
wait "$PARENT_PID" 2>/dev/null || true
# Kill any remaining children by pattern
pkill -f "sleep 600.*${PROJECT_KEY}" 2>/dev/null || true

# =============================================================================
# Test 3: bystander survival — a shell whose argv merely contains the project
#         path must not be killed (AC-7)
# =============================================================================
#
# This is what killed the operator's own shell (exit 144): any process whose
# argv contained the project path was treated as a runner.

echo ""
echo "Test 3: bystander survival — a process mentioning the project path must survive"

# Create a fresh run.lock
rm -f "$RUN_LOCK"
touch "$RUN_LOCK"

# Spawn a "runner" that holds run.lock
RUNNER_PID=""
bash -c '
  exec 3<"'${RUN_LOCK}'"
  exec -a "run_ilk_loop_claude 20260812-150000 ${PROJECT_DIR}" sleep 600
' &
RUNNER_PID=$!

# Spawn a bystander: a shell whose argv contains the project path
# (exactly the shape that got killed: `zsh -c 'sleep 30 # /path/to/project'`)
BYSTANDER_PID=""
bash -c "sleep 600 # ${PROJECT_DIR}" &
BYSTANDER_PID=$!

sleep 0.5
echo "$RUNNER_PID" > "${LAUNCHER_DIR}/running.pid"

# Run stop.sh
STOP_OUTPUT2=$(bash "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/stop.sh" \
  --project-path "$PROJECT_DIR" 2>&1) || true

# Check bystander survived
if kill -0 "$BYSTANDER_PID" 2>/dev/null; then
  pass "bystander (argv contains project path) survived stop.sh"
else
  if [[ "$KNOWN_BAD" == "1" ]]; then
    echo "  KNOWN_BAD: bystander killed by stop.sh (expected broken)"
  else
    fail "bystander killed by stop.sh — match predicate too broad"
  fi
fi

# Clean up
kill "$BYSTANDER_PID" 2>/dev/null || true
kill "$RUNNER_PID" 2>/dev/null || true
wait "$BYSTANDER_PID" 2>/dev/null || true
wait "$RUNNER_PID" 2>/dev/null || true

# =============================================================================
# Test 4: idempotence — stop.sh on nothing running succeeds (AC-4)
# =============================================================================

echo ""
echo "Test 4: idempotence — stop with nothing running"

rm -f "${LAUNCHER_DIR}/running.pid"

IDEM_OUTPUT=""
IDEM_EXIT=0
IDEM_OUTPUT=$(bash "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/stop.sh" \
  --project-path "$PROJECT_DIR" 2>&1) || IDEM_EXIT=$?

if [[ "$IDEM_EXIT" -eq 0 ]]; then
  pass "stop.sh on nothing running exited 0"
else
  fail "stop.sh on nothing running exited $IDEM_EXIT"
fi

if echo "$IDEM_OUTPUT" | grep -qi "no running\|nothing\|not running\|no ilk"; then
  pass "stop.sh reported nothing to stop"
else
  fail "stop.sh did not report 'nothing to stop' — output: ${IDEM_OUTPUT:0:200}"
fi

# =============================================================================
# Summary
# =============================================================================

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
