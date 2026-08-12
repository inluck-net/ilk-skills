#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Smoke tests for stop.sh
# =============================================================================
# Creates a synthetic project environment and verifies stop.sh behavior:
#   1. Stops the loop PID
#   2. Removes the PID file
#   3. Marks sentinel as interrupted
#   4. Reports dirty tree state
#   5. Reset mode previews and cleans up
#
# Usage: bash test_stop.sh
# Requires: python3, git
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STOP_SCRIPT="${SCRIPT_DIR}/../scripts/stop.sh"
SKILL_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PASS=0
FAIL=0
TESTS=()

pass() { PASS=$((PASS + 1)); TESTS+=("PASS: $1"); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); TESTS+=("FAIL: $1"); echo "  FAIL: $1"; }

# ----- Setup -----------------------------------------------------------------

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

PROJECT_DIR="${TMPDIR}/test-project"
mkdir -p "$PROJECT_DIR"
(cd "$PROJECT_DIR" && git init -q && git commit -q --allow-empty -m "init")

# Create a fake untracked file to test dirty tree report
echo "worker-artifact" > "$PROJECT_DIR/worker-output.txt"

# Create a tracked file with modifications to test reset mode
echo "original" > "$PROJECT_DIR/tracked-file.txt"
(cd "$PROJECT_DIR" && git add tracked-file.txt && git commit -q -m "add tracked")
echo "modified-by-worker" > "$PROJECT_DIR/tracked-file.txt"

# Create external runtime dirs
PROJECT_KEY="test-project"
LAUNCHER_DIR="${TMPDIR}/ilk-data/projects/${PROJECT_KEY}/runtime/launcher"
RUNTIME_DIR="${TMPDIR}/ilk-data/projects/${PROJECT_KEY}/runtime"
mkdir -p "$LAUNCHER_DIR"

# Start a background process to act as the "loop"
sleep 600 &
FAKE_PID=$!

# Write PID file
echo "$FAKE_PID" > "${LAUNCHER_DIR}/running.pid"

# Write sentinel (last-exit.json) — normally created by the runner at loop start
cat > "${RUNTIME_DIR}/last-exit.json" <<SENTINEL_JSON
{
  "state": "running",
  "pid": ${FAKE_PID},
  "run_id": "20260528-100000",
  "started_at": "2026-05-28T10:00:00+0800",
  "project_path": "${PROJECT_DIR}",
  "cli": "claude"
}
SENTINEL_JSON

# Write last-launch.json
cat > "${LAUNCHER_DIR}/last-launch.json" <<LAUNCH_JSON
{
  "project_path": "${PROJECT_DIR}",
  "project_name": "test-project",
  "pid": ${FAKE_PID},
  "started_at": "2026-05-28T10:00:00+0800",
  "max_iterations": 30,
  "iteration_timeout_min": 30,
  "worker_engine": "claude",
  "loop_script": "${SKILL_ROOT}/ilk-loop/scripts/run_ilk_loop_claude.sh",
  "log_file": "${TMPDIR}/logs/launcher/test-project-20260528-100000.log"
}
LAUNCH_JSON

# Create a mock ilk_paths.py that returns our synthetic paths
MOCK_RESOLVER="${TMPDIR}/mock_ilk_paths.py"
cat > "$MOCK_RESOLVER" <<'MOCK_PY'
import json, sys
args = sys.argv[1:]
start = ""
for i, a in enumerate(args):
    if a == "--start" and i + 1 < len(args):
        start = args[i + 1]
        break
import os
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

# Override the resolver in the stop script by shadowing ilk_paths.py
# We do this by creating a mock _ilk_skill_root.sh that points to our mock
MOCK_SKILL_ROOT="${TMPDIR}/skill-root"
mkdir -p "${MOCK_SKILL_ROOT}/ilk-loop/scripts"
mkdir -p "${MOCK_SKILL_ROOT}/ilk-launcher/scripts"
mkdir -p "${MOCK_SKILL_ROOT}/ilk-watchdog/scripts"

# Copy the real stop script
cp "$STOP_SCRIPT" "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/stop.sh"

# Copy mark_sentinel_interrupted.sh if it exists
if [[ -f "${SCRIPT_DIR}/../scripts/mark_sentinel_interrupted.sh" ]]; then
  cp "${SCRIPT_DIR}/../scripts/mark_sentinel_interrupted.sh" "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/"
fi

# Copy _ilk_pid.sh for ilk_project_runners (used by stop.sh's post-kill verification)
if [[ -f "${SCRIPT_DIR}/../../ilk-loop/scripts/_ilk_pid.sh" ]]; then
  cp "${SCRIPT_DIR}/../../ilk-loop/scripts/_ilk_pid.sh" "${MOCK_SKILL_ROOT}/ilk-loop/scripts/"
fi

# Create a mock _ilk_skill_root.sh
cat > "${MOCK_SKILL_ROOT}/ilk-loop/scripts/_ilk_skill_root.sh" <<'ROOT_SH'
ilk_skill_root() {
  echo "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
}
ROOT_SH

# Create a mock ilk_paths.py
cp "$MOCK_RESOLVER" "${MOCK_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"

# Create a mock stop_watchdog.sh (no-op)
cat > "${MOCK_SKILL_ROOT}/ilk-watchdog/scripts/stop_watchdog.sh" <<'WD_SH'
#!/usr/bin/env bash
echo "[watchdog-mock] stopped (no-op)" >&2
WD_SH

# ----- Test 1: stop kills the PID and cleans up ------------------------------

echo "Test 1: stop kills PID and cleans up"

export TEST_PROJECT_PATH="$PROJECT_DIR"
export TEST_PROJECT_KEY="$PROJECT_KEY"
export TEST_LAUNCHER_DIR="$LAUNCHER_DIR"
export TEST_WATCHDOG_DIR="${TMPDIR}/ilk-data/projects/${PROJECT_KEY}/runtime/watchdog"
export TEST_RUNTIME_DIR="$RUNTIME_DIR"

# Verify PID is alive before stop
if kill -0 "$FAKE_PID" 2>/dev/null; then
  pass "pre-condition: fake PID $FAKE_PID is alive"
else
  fail "pre-condition: fake PID $FAKE_PID not alive"
fi

# Run stop
OUTPUT=$(bash "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/stop.sh" \
  --project-path "$PROJECT_DIR" 2>&1) || true

# Verify PID is dead after stop
if kill -0 "$FAKE_PID" 2>/dev/null; then
  fail "stop did not kill PID $FAKE_PID"
else
  pass "stop killed PID $FAKE_PID"
fi

# Verify PID file was removed
if [[ -f "${LAUNCHER_DIR}/running.pid" ]]; then
  fail "PID file was not removed"
else
  pass "PID file removed"
fi

# Verify sentinel was marked interrupted
if [[ -f "${RUNTIME_DIR}/last-exit.json" ]]; then
  STATE=$(python3 -c "import json; print(json.load(open('${RUNTIME_DIR}/last-exit.json')).get('state',''))")
  if [[ "$STATE" == "interrupted" ]]; then
    pass "sentinel marked interrupted"
  else
    fail "sentinel state is '$STATE', expected 'interrupted'"
  fi
else
  fail "sentinel file not created"
fi

# Verify dirty tree report appears in output
if echo "$OUTPUT" | grep -q "repo state:"; then
  pass "dirty tree report present"
else
  fail "dirty tree report missing"
fi

if echo "$OUTPUT" | grep -q "worker-output.txt"; then
  pass "untracked file in dirty tree report"
else
  fail "untracked file not in dirty tree report"
fi

# ----- Test 2: reset mode previews and cleans up -----------------------------

echo ""
echo "Test 2: reset mode previews and cleans up"

# Re-create the fake process and PID file
sleep 600 &
FAKE_PID2=$!
echo "$FAKE_PID2" > "${LAUNCHER_DIR}/running.pid"
cat > "${RUNTIME_DIR}/last-exit.json" <<SENTINEL_JSON2
{
  "state": "running",
  "pid": ${FAKE_PID2},
  "run_id": "20260528-100000",
  "started_at": "2026-05-28T10:00:00+0800",
  "project_path": "${PROJECT_DIR}",
  "cli": "claude"
}
SENTINEL_JSON2
cat > "${LAUNCHER_DIR}/last-launch.json" <<LAUNCH_JSON2
{
  "project_path": "${PROJECT_DIR}",
  "project_name": "test-project",
  "pid": ${FAKE_PID2},
  "started_at": "2026-05-28T10:00:00+0800",
  "log_file": "${TMPDIR}/logs/launcher/test-project-20260528-100000.log"
}
LAUNCH_JSON2

# Verify tracked file is dirty before reset
if [[ "$(cd "$PROJECT_DIR" && git diff --name-only tracked-file.txt)" == "tracked-file.txt" ]]; then
  pass "pre-condition: tracked file is dirty"
else
  fail "pre-condition: tracked file not dirty"
fi

# Run stop with reset
OUTPUT2=$(bash "${MOCK_SKILL_ROOT}/ilk-launcher/scripts/stop.sh" \
  --project-path "$PROJECT_DIR" --reset-worker-changes 2>&1) || true

# Verify reset preview appeared
if echo "$OUTPUT2" | grep -q "reset preview"; then
  pass "reset preview shown"
else
  fail "reset preview not shown"
fi

# Verify tracked file was restored
if [[ "$(cd "$PROJECT_DIR" && cat tracked-file.txt)" == "original" ]]; then
  pass "tracked file restored to original"
else
  fail "tracked file not restored"
fi

# Verify untracked file was removed
if [[ -f "$PROJECT_DIR/worker-output.txt" ]]; then
  fail "untracked file was not removed"
else
  pass "untracked file removed"
fi

# ----- Summary ----------------------------------------------------------------

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
