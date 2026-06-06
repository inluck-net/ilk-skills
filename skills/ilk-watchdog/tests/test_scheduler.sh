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
  output=$(ILK_DATA_HOME="$FAKE_DATA" python "$SCAN_SCRIPT" 2>&1) || die "scheduler_scan.py exited non-zero: $output"

  # Assert: exactly one project returned
  local count
  count=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))" <<<"$output")
  [[ "$count" == "1" ]] || die "expected 1 project, got $count. Output: $output"

  # Assert: it is proj-b
  local key
  key=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['key'])" <<<"$output")
  [[ "$key" == "proj-b" ]] || die "expected key 'proj-b', got '$key'. Output: $output"

  # Assert: oldest_queued_ts matches 2026-06-06
  local ts
  ts=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['oldest_queued_ts'])" <<<"$output")
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
}

run_select() {
  echo "=== test_scheduler.sh select ==="
  setup_two_queued_projects

  # Test 1: FIFO — with both projects free, proj-a (older) should be dispatched first
  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"  # strip Windows \r

  local decision key
  decision=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  key=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$output")
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

  decision=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$last_line")
  key=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$last_line")
  [[ "$decision" == "dispatch" ]] || die "expected 'dispatch', got '$decision'. Output: $output"
  [[ "$key" == "proj-b" ]] || die "expected dispatch of 'proj-b' after skip-busy, got '$key'. Output: $output"

  # Verify the first line was skip-busy for proj-a
  local first_line
  first_line=$(echo "$output" | head -1)
  local first_decision first_key
  first_decision=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$first_line")
  first_key=$(python -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$first_line")
  [[ "$first_decision" == "skip-busy" ]] || die "expected first decision 'skip-busy', got '$first_decision'. Output: $output"
  [[ "$first_key" == "proj-a" ]] || die "expected skip-busy for 'proj-a', got '$first_key'. Output: $output"

  echo "PASS: skip-busy proj-a, dispatch proj-b"
  kill "$fake_pid" 2>/dev/null || true
  cleanup
}

# --- main ---------------------------------------------------------------------

case "${1:-}" in
  scan)
    run_scan
    ;;
  select)
    run_select
    ;;
  *)
    echo "Usage: $0 {scan|select}" >&2
    exit 1
    ;;
esac
