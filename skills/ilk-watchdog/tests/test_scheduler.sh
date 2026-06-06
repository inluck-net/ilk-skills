#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# test_scheduler.sh — cross-platform test harness for the cross-project scheduler
#
# Subcommands:
#   scan — build a fake ILK_DATA_HOME with 2 projects (one all-shipped,
#          one with a queued sub-plan) and assert scheduler_scan.py lists
#          ONLY the queued one.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SCAN_SCRIPT="$SCRIPT_DIR/../scripts/scheduler_scan.py"
SCHEDULER_SCRIPT="$SCRIPT_DIR/../scripts/scheduler.sh"
SCRATCH="$REPO_ROOT/scratch/sched-test"

# Resolve python command (python3 preferred, python fallback).
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
[[ -n "$PYTHON" ]] || { echo "FAIL: no working python found on PATH" >&2; exit 1; }

# Absolute path to the fake ILK_DATA_HOME
FAKE_DATA="$SCRATCH/ilk-data"

# --- helpers ------------------------------------------------------------------

die() { echo "FAIL: $*" >&2; exit 1; }

cleanup() {
  rm -rf "$SCRATCH"
}

setup_fake_data() {
  cleanup
  mkdir -p "$FAKE_DATA/projects"

  # Project A: all-shipped (should be excluded from scan)
  local proj_a="$FAKE_DATA/projects/proj-a"
  mkdir -p "$proj_a/plans"
  cat > "$proj_a/plans/MASTER-2026-06-06-all-done.md" <<'EOF'
---
master_plan: 2026-06-06-all-done
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: All done

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-done-slug](./2026-06-06-done-slug.md) | 3 | shipped |
EOF

  cat > "$proj_a/plans/2026-06-06-done-slug.md" <<'EOF'
---
plan: done-slug
status: shipped
current_step: 3
estimated_steps: 3
last_updated: 2026-06-05
---

# Sub-plan: Done slug

All steps complete.
EOF

  # Project B: has a queued (pending) sub-plan — should appear in scan
  local proj_b="$FAKE_DATA/projects/proj-b"
  mkdir -p "$proj_b/plans"
  cat > "$proj_b/plans/MASTER-2026-06-06-has-work.md" <<'EOF'
---
master_plan: 2026-06-06-has-work
batch_date: 2026-06-06
status: active
---

# MASTER plan: Has work

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-queued-slug](./2026-06-06-queued-slug.md) | 5 | pending |
EOF

  cat > "$proj_b/plans/2026-06-06-queued-slug.md" <<'EOF'
---
plan: queued-slug
status: pending
current_step: 0
estimated_steps: 5
last_updated: 2026-06-06
---

# Sub-plan: Queued slug

Waiting to be executed.
EOF
}

# --- subcommands --------------------------------------------------------------

run_scan() {
  echo "=== test_scheduler.sh scan ==="
  setup_fake_data

  # Run scheduler_scan.py with the fake ILK_DATA_HOME
  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" "$PYTHON" "$SCAN_SCRIPT" 2>&1) || die "scheduler_scan.py exited non-zero: $output"

  # Assert: exactly one project returned
  local count
  count=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))" <<<"$output")
  [[ "$count" == "1" ]] || die "expected 1 project, got $count. Output: $output"

  # Assert: it is proj-b
  local key
  key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['key'])" <<<"$output")
  [[ "$key" == "proj-b" ]] || die "expected key 'proj-b', got '$key'. Output: $output"

  # Assert: oldest_queued_ts matches 2026-06-06
  local ts
  ts=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['oldest_queued_ts'])" <<<"$output")
  [[ "$ts" == "2026-06-06"* ]] || die "expected ts starting with '2026-06-06', got '$ts'. Output: $output"

  echo "PASS: scan subcommand"
  cleanup
}

setup_two_queued_projects() {
  cleanup
  mkdir -p "$FAKE_DATA/projects"

  # Project A: queued, older timestamp (should be dispatched first in FIFO)
  local proj_a="$FAKE_DATA/projects/proj-a"
  mkdir -p "$proj_a/plans"
  cat > "$proj_a/plans/MASTER-2026-06-01-batch.md" <<'EOF'
---
master_plan: 2026-06-01-batch
batch_date: 2026-06-01
status: active
---

# MASTER plan: Batch A

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-01-task-alpha](./2026-06-01-task-alpha.md) | 4 | pending |
EOF

  cat > "$proj_a/plans/2026-06-01-task-alpha.md" <<'EOF'
---
plan: task-alpha
status: pending
current_step: 0
estimated_steps: 4
last_updated: 2026-06-01
---

# Sub-plan: Task Alpha

Queued and waiting.
EOF

  # Project B: queued, newer timestamp (should be dispatched second)
  local proj_b="$FAKE_DATA/projects/proj-b"
  mkdir -p "$proj_b/plans"
  cat > "$proj_b/plans/MASTER-2026-06-03-batch.md" <<'EOF'
---
master_plan: 2026-06-03-batch
batch_date: 2026-06-03
status: active
---

# MASTER plan: Batch B

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-03-task-beta](./2026-06-03-task-beta.md) | 3 | pending |
EOF

  cat > "$proj_b/plans/2026-06-03-task-beta.md" <<'EOF'
---
plan: task-beta
status: pending
current_step: 0
estimated_steps: 3
last_updated: 2026-06-03
---

# Sub-plan: Task Beta

Queued and waiting.
EOF

  # Give each project a last-launch.json so repo_path resolves to a SOURCE
  # repo path that is DELIBERATELY DIFFERENT from the ~/.ilk-data data dir
  # (under scratch/repos/, not scratch/.../ilk-data/projects/). This is what
  # proves the scheduler dispatches the repo path, not the data dir.
  mkdir -p "$proj_a/runtime/launcher" "$proj_b/runtime/launcher"
  printf '{"project_path":"%s","worker_engine":"claude-worker"}\n' "$SCRATCH/repos/proj-a" > "$proj_a/runtime/launcher/last-launch.json"
  printf '{"project_path":"%s","worker_engine":"claude-worker"}\n' "$SCRATCH/repos/proj-b" > "$proj_b/runtime/launcher/last-launch.json"
}

run_select() {
  echo "=== test_scheduler.sh select ==="
  setup_two_queued_projects

  # Test 1: FIFO — with both projects free, proj-a (older) should be dispatched first
  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"  # strip Windows \r

  local decision key
  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$output")
  [[ "$decision" == "dispatch" ]] || die "expected 'dispatch', got '$decision'. Output: $output"
  [[ "$key" == "proj-a" ]] || die "expected FIFO dispatch of 'proj-a', got '$key'. Output: $output"
  echo "PASS: FIFO dispatch (proj-a first)"

  # Test 2: simulate a live running.pid for proj-a → skip-busy, dispatch proj-b
  local launcher_dir="$FAKE_DATA/projects/proj-a/runtime/launcher"
  mkdir -p "$launcher_dir"
  # Spawn a background sleep and use its PID as a definitely-alive process
  sleep 60 &
  local fake_pid=$!
  echo "$fake_pid" > "$launcher_dir/running.pid"

  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"  # strip Windows \r

  # The output may contain multiple JSON lines (skip-busy + dispatch). Parse the last one.
  local last_line
  last_line=$(echo "$output" | tail -1)

  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$last_line")
  key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$last_line")
  [[ "$decision" == "dispatch" ]] || die "expected 'dispatch', got '$decision'. Output: $output"
  [[ "$key" == "proj-b" ]] || die "expected dispatch of 'proj-b' after skip-busy, got '$key'. Output: $output"

  # Verify the first line was skip-busy for proj-a
  local first_line
  first_line=$(echo "$output" | head -1)
  local first_decision first_key
  first_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$first_line")
  first_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$first_line")
  [[ "$first_decision" == "skip-busy" ]] || die "expected first decision 'skip-busy', got '$first_decision'. Output: $output"
  [[ "$first_key" == "proj-a" ]] || die "expected skip-busy for 'proj-a', got '$first_key'. Output: $output"

  echo "PASS: skip-busy proj-a, dispatch proj-b"
  kill "$fake_pid" 2>/dev/null || true
  cleanup
}

run_dispatch() {
  echo "=== test_scheduler.sh dispatch ==="
  setup_two_queued_projects

  # Test 1: dispatch command contains -Engine claude-worker and selected project path
  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local decision key command
  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$output")
  command=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['command'])" <<<"$output")
  [[ "$decision" == "dispatch" ]] || die "expected 'dispatch', got '$decision'. Output: $output"
  [[ "$key" == "proj-a" ]] || die "expected dispatch of 'proj-a', got '$key'. Output: $output"
  [[ "$command" == *"claude-worker"* ]] || die "expected 'claude-worker' in command, got '$command'. Output: $output"
  # Must dispatch the SOURCE repo path (scratch/repos/proj-a), NOT the data dir.
  [[ "$command" == *"repos/proj-a"* ]] || die "expected SOURCE repo path 'repos/proj-a' in command, got '$command'. Output: $output"
  [[ "$command" != *"ilk-data"* ]] || die "command must NOT contain the data dir (ilk-data); got '$command'. Output: $output"
  echo "PASS: dispatch command uses the source repo path, not the data dir"

  # Test 2: -MaxDispatches 0 yields idle: budget ceiling
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-dispatches 0 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  local reason
  reason=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['reason'])" <<<"$output")
  [[ "$decision" == "idle" ]] || die "expected 'idle', got '$decision'. Output: $output"
  [[ "$reason" == *"budget"* ]] || die "expected 'budget' in reason, got '$reason'. Output: $output"
  echo "PASS: -MaxDispatches 0 yields idle: budget ceiling"

  cleanup
}

run_blacklist() {
  echo "=== test_scheduler.sh blacklist ==="
  setup_two_queued_projects

  # Create a postmortem for project A with blacklist classification
  local pm_dir="$FAKE_DATA/projects/proj-a/runtime/launcher/postmortems"
  mkdir -p "$pm_dir"
  local now
  now=$(date '+%Y-%m-%dT%H:%M:%S')
  cat > "$pm_dir/20260606-120000.md" <<EOF
---
project: proj-a
classification: stuck-no-progress
generated_at: $now
---

# Postmortem for proj-a
EOF

  # Test 1: DryRun+Once should report skip-blacklist for A, dispatch B
  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local first_line last_line
  first_line=$(echo "$output" | head -1)
  last_line=$(echo "$output" | tail -1)

  local first_decision first_key
  first_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$first_line")
  first_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$first_line")
  [[ "$first_decision" == "skip-blacklist" ]] || die "expected 'skip-blacklist', got '$first_decision'. Output: $output"
  [[ "$first_key" == "proj-a" ]] || die "expected skip-blacklist for 'proj-a', got '$first_key'. Output: $output"

  local last_decision last_key
  last_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$last_line")
  last_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$last_line")
  [[ "$last_decision" == "dispatch" ]] || die "expected 'dispatch', got '$last_decision'. Output: $output"
  [[ "$last_key" == "proj-b" ]] || die "expected dispatch of 'proj-b', got '$last_key'. Output: $output"

  echo "PASS: skip-blacklist proj-a, dispatch proj-b (non-starvation)"

  # Test 2: empty queues report idle (AC-5)
  cleanup
  mkdir -p "$FAKE_DATA/projects"

  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local decision
  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  [[ "$decision" == "idle" ]] || die "expected 'idle' for empty queues, got '$decision'. Output: $output"

  echo "PASS: empty queues report idle"
  cleanup
}

# --- main ---------------------------------------------------------------------

run_unresolved() {
  echo "=== test_scheduler.sh unresolved ==="
  cleanup
  mkdir -p "$FAKE_DATA/projects"

  # One queued project with NO last-launch.json and not in any registry →
  # repo_path cannot resolve → scheduler must skip-unresolved, not dispatch.
  local proj_c="$FAKE_DATA/projects/proj-c"
  mkdir -p "$proj_c/plans"
  cat > "$proj_c/plans/MASTER-2026-06-02-orphan.md" <<'EOF'
---
master_plan: 2026-06-02-orphan
status: active
---

# MASTER plan: Orphan

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-02-orphan-slug](./2026-06-02-orphan-slug.md) | 2 | pending |
EOF
  cat > "$proj_c/plans/2026-06-02-orphan-slug.md" <<'EOF'
---
plan: orphan-slug
status: pending
current_step: 0
estimated_steps: 2
last_updated: 2026-06-02
---

# Sub-plan: Orphan slug

No last-launch.json, so repo_path cannot resolve.
EOF

  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local first_line decision key
  first_line=$(echo "$output" | head -1)
  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$first_line")
  key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$first_line")
  [[ "$decision" == "skip-unresolved" ]] || die "expected 'skip-unresolved', got '$decision'. Output: $output"
  [[ "$key" == "proj-c" ]] || die "expected skip-unresolved for 'proj-c', got '$key'. Output: $output"

  # With the only project unresolved, the final decision is idle (not dispatch).
  local last_line last_decision
  last_line=$(echo "$output" | tail -1)
  last_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$last_line")
  [[ "$last_decision" == "idle" ]] || die "expected final 'idle', got '$last_decision'. Output: $output"

  echo "PASS: skip-unresolved when repo_path cannot resolve, then idle"
  cleanup
}

run_all() {
  run_scan
  run_select
  run_dispatch
  run_blacklist
  run_unresolved
  echo "ALL PASS"
}

case "${1:-all}" in
  scan)
    run_scan
    ;;
  select)
    run_select
    ;;
  dispatch)
    run_dispatch
    ;;
  blacklist)
    run_blacklist
    ;;
  unresolved)
    run_unresolved
    ;;
  all)
    run_all
    ;;
  *)
    echo "Usage: $0 {scan|select|dispatch|blacklist|unresolved|all}" >&2
    exit 1
    ;;
esac
