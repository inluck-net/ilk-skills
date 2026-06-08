#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# test_scheduler.sh — cross-platform test harness for the cross-project scheduler
#
# Subcommands:
#   scan — build a fake ILK_DATA_HOME with 2 projects (one all-shipped,
#          one with a queued sub-plan) and assert scheduler_scan.py lists
#          ONLY the queued one.
#   cap  — assert -MaxConcurrent capacity accounting: N busy projects fill
#          slots, dispatches stop at the cap.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SCAN_SCRIPT="$SCRIPT_DIR/../scripts/scheduler_scan.py"
SCHEDULER_SCRIPT="$SCRIPT_DIR/../scripts/scheduler.sh"
SCRATCH="$REPO_ROOT/scratch/sched-test"

# Absolute path to the fake ILK_DATA_HOME
FAKE_DATA="$SCRATCH/ilk-data"

# Resolve python (python3 preferred; macOS lacks a bare `python`).
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
[[ -n "$PYTHON" ]] || { echo "FAIL: no working python found on PATH" >&2; exit 1; }

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

  # Project C: M1 shipped + M2 queued(pending) — MUST be listed (promotable)
  local proj_c="$FAKE_DATA/projects/proj-c"
  mkdir -p "$proj_c/plans"
  cat > "$proj_c/plans/MASTER-2026-06-06-multi-done.md" <<'EOF'
---
master_plan: 2026-06-06-multi-done
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: Multi done

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-multi-done-sub](./2026-06-06-multi-done-sub.md) | 2 | shipped |
EOF
  cat > "$proj_c/plans/2026-06-06-multi-done-sub.md" <<'EOF'
---
plan: multi-done-sub
status: shipped
current_step: 2
estimated_steps: 2
last_updated: 2026-06-05
---

# Sub-plan: Multi done sub

All steps complete.
EOF

  cat > "$proj_c/plans/MASTER-2026-06-06-multi-queued.md" <<'EOF'
---
master_plan: 2026-06-06-multi-queued
batch_date: 2026-06-06
status: queued
---

# MASTER plan: Multi queued

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-multi-queued-sub](./2026-06-06-multi-queued-sub.md) | 3 | pending |
EOF
  cat > "$proj_c/plans/2026-06-06-multi-queued-sub.md" <<'EOF'
---
plan: multi-queued-sub
status: pending
current_step: 0
estimated_steps: 3
last_updated: 2026-06-06
---

# Sub-plan: Multi queued sub

Waiting for promotion.
EOF

  # Project D: all masters shipped — MUST be excluded
  local proj_d="$FAKE_DATA/projects/proj-d"
  mkdir -p "$proj_d/plans"
  cat > "$proj_d/plans/MASTER-2026-06-06-all-shipped-1.md" <<'EOF'
---
master_plan: 2026-06-06-all-shipped-1
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: All shipped 1

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-shipped-sub-1](./2026-06-06-shipped-sub-1.md) | 2 | shipped |
EOF
  cat > "$proj_d/plans/2026-06-06-shipped-sub-1.md" <<'EOF'
---
plan: shipped-sub-1
status: shipped
current_step: 2
estimated_steps: 2
last_updated: 2026-06-04
---

# Sub-plan: Shipped sub 1

All steps complete.
EOF

  cat > "$proj_d/plans/MASTER-2026-06-06-all-shipped-2.md" <<'EOF'
---
master_plan: 2026-06-06-all-shipped-2
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: All shipped 2

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-shipped-sub-2](./2026-06-06-shipped-sub-2.md) | 1 | shipped |
EOF
  cat > "$proj_d/plans/2026-06-06-shipped-sub-2.md" <<'EOF'
---
plan: shipped-sub-2
status: shipped
current_step: 1
estimated_steps: 1
last_updated: 2026-06-04
---

# Sub-plan: Shipped sub 2

All steps complete.
EOF
}

# --- subcommands --------------------------------------------------------------

run_scan() {
  echo "=== test_scheduler.sh scan ==="
  setup_fake_data

  # Run scheduler_scan.py with the fake ILK_DATA_HOME
  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" "$PYTHON" "$SCAN_SCRIPT" 2>&1) || die "scheduler_scan.py exited non-zero: $output"

  # Assert: exactly 2 projects returned (proj-b active, proj-c promotable)
  # proj-a (all shipped) and proj-d (all masters shipped) are excluded.
  local count
  count=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d))" <<<"$output")
  [[ "$count" == "2" ]] || die "expected 2 projects, got $count. Output: $output"

  # Assert: first is proj-b (active master, oldest_queued_ts from active)
  local key0
  key0=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['key'])" <<<"$output")
  [[ "$key0" == "proj-b" ]] || die "expected first key 'proj-b', got '$key0'. Output: $output"

  # Assert: second is proj-c (queued master only, promotable)
  local key1
  key1=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[1]['key'])" <<<"$output")
  [[ "$key1" == "proj-c" ]] || die "expected second key 'proj-c', got '$key1'. Output: $output"

  # Assert: oldest_queued_ts starts with 2026-06-06 for both
  local ts0 ts1
  ts0=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[0]['oldest_queued_ts'])" <<<"$output")
  ts1=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d[1]['oldest_queued_ts'])" <<<"$output")
  [[ "$ts0" == "2026-06-06"* ]] || die "expected ts0 starting with '2026-06-06', got '$ts0'. Output: $output"
  [[ "$ts1" == "2026-06-06"* ]] || die "expected ts1 starting with '2026-06-06', got '$ts1'. Output: $output"

  echo "PASS: scan subcommand (runnable-master semantics)"
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

  # Test 1: FIFO — with both projects free, proj-a (older) should be dispatched first.
  # Use --max-concurrent 1 so only one project is dispatched per cycle (strict sequential).
  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 1 2>&1) || die "scheduler exited non-zero: $output"
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

  # Test 1: dispatch command contains -Engine claude-worker and selected project path.
  # With fill-free-slots, both projects dispatch; parse the first line for proj-a.
  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local first_line decision key command
  first_line=$(echo "$output" | head -1)
  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$first_line")
  key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$first_line")
  command=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['command'])" <<<"$first_line")
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

setup_promotable_project() {
  # Project with M1 shipped + M2 queued (promotable) + source repo path resolved
  cleanup
  mkdir -p "$FAKE_DATA/projects"

  local proj_p="$FAKE_DATA/projects/proj-promote"
  mkdir -p "$proj_p/plans"
  cat > "$proj_p/plans/MASTER-2026-06-06-m1-done.md" <<'EOF'
---
master_plan: 2026-06-06-m1-done
batch_date: 2026-06-06
status: shipped
---

# MASTER plan: M1 done

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-m1-sub](./2026-06-06-m1-sub.md) | 2 | shipped |
EOF
  cat > "$proj_p/plans/2026-06-06-m1-sub.md" <<'EOF'
---
plan: m1-sub
status: shipped
current_step: 2
estimated_steps: 2
last_updated: 2026-06-05
---

# Sub-plan: M1 sub

All steps complete.
EOF

  cat > "$proj_p/plans/MASTER-2026-06-06-m2-queued.md" <<'EOF'
---
master_plan: 2026-06-06-m2-queued
batch_date: 2026-06-06
status: queued
priority: 1
created: 2026-06-06T10:00:00+08:00
---

# MASTER plan: M2 queued

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-m2-sub](./2026-06-06-m2-sub.md) | 3 | pending |
EOF
  cat > "$proj_p/plans/2026-06-06-m2-sub.md" <<'EOF'
---
plan: m2-sub
status: pending
current_step: 0
estimated_steps: 3
last_updated: 2026-06-06
---

# Sub-plan: M2 sub

Waiting for promotion.
EOF

  # last-launch.json so repo_path resolves
  mkdir -p "$proj_p/runtime/launcher"
  printf '{"project_path":"%s","worker_engine":"claude-worker"}\n' "$SCRATCH/repos/proj-promote" > "$proj_p/runtime/launcher/last-launch.json"
}

setup_active_master_no_promote() {
  # Project with an active master that has pending sub-plans (no promotion needed)
  cleanup
  mkdir -p "$FAKE_DATA/projects"

  local proj_a="$FAKE_DATA/projects/proj-active"
  mkdir -p "$proj_a/plans"
  cat > "$proj_a/plans/MASTER-2026-06-06-active-batch.md" <<'EOF'
---
master_plan: 2026-06-06-active-batch
batch_date: 2026-06-06
status: active
---

# MASTER plan: Active batch

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-active-sub](./2026-06-06-active-sub.md) | 4 | pending |
EOF
  cat > "$proj_a/plans/2026-06-06-active-sub.md" <<'EOF'
---
plan: active-sub
status: pending
current_step: 0
estimated_steps: 4
last_updated: 2026-06-06
---

# Sub-plan: Active sub

Waiting to be executed.
EOF

  # last-launch.json so repo_path resolves
  mkdir -p "$proj_a/runtime/launcher"
  printf '{"project_path":"%s","worker_engine":"claude-worker"}\n' "$SCRATCH/repos/proj-active" > "$proj_a/runtime/launcher/last-launch.json"
}

run_promote() {
  echo "=== test_scheduler.sh promote ==="

  # Test 1: M1 shipped + M2 queued → dry-run reports promote(M2) then dispatch (AC-1)
  setup_promotable_project

  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local first_line second_line
  first_line=$(echo "$output" | head -1)
  second_line=$(echo "$output" | sed -n '2p')

  local decision key promoted
  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$first_line")
  key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$first_line")
  promoted=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['promoted'])" <<<"$first_line")
  [[ "$decision" == "promote" ]] || die "expected first decision 'promote', got '$decision'. Output: $output"
  [[ "$key" == "proj-promote" ]] || die "expected promote key 'proj-promote', got '$key'. Output: $output"
  [[ "$promoted" == *"m2-queued"* ]] || die "expected promoted to contain 'm2-queued', got '$promoted'. Output: $output"

  local dispatch_decision dispatch_key
  dispatch_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$second_line")
  dispatch_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$second_line")
  [[ "$dispatch_decision" == "dispatch" ]] || die "expected second decision 'dispatch', got '$dispatch_decision'. Output: $output"
  [[ "$dispatch_key" == "proj-promote" ]] || die "expected dispatch key 'proj-promote', got '$dispatch_key'. Output: $output"

  echo "PASS: promote(M2) then dispatch (AC-1)"

  # Test 2: active master with pending sub-plans → dispatch with NO promote (AC-2)
  setup_active_master_no_promote

  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local single_decision single_key
  single_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  single_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$output")
  [[ "$single_decision" == "dispatch" ]] || die "expected 'dispatch' (no promote needed), got '$single_decision'. Output: $output"
  [[ "$single_key" == "proj-active" ]] || die "expected key 'proj-active', got '$single_key'. Output: $output"

  echo "PASS: dispatch with NO promote when active master exists (AC-2)"
  cleanup
}

setup_cap_projects() {
  # Create N queued projects, each with a last-launch.json so repo_path resolves.
  # Caller sets $NUM_PROJECTS before calling.
  cleanup
  mkdir -p "$FAKE_DATA/projects"
  for i in $(seq 1 "$NUM_PROJECTS"); do
    local proj="$FAKE_DATA/projects/proj-cap-$i"
    mkdir -p "$proj/plans" "$proj/runtime/launcher"
    cat > "$proj/plans/MASTER-2026-06-06-cap-batch.md" <<EOF
---
master_plan: 2026-06-06-cap-batch
batch_date: 2026-06-06
status: active
---

# MASTER plan: Cap batch $i

## Sub-plan registry

| # | Slug | Steps | Status |
|---|---|---|---|
| 1 | [2026-06-06-cap-sub](./2026-06-06-cap-sub.md) | 3 | pending |
EOF
    cat > "$proj/plans/2026-06-06-cap-sub.md" <<EOF
---
plan: cap-sub
status: pending
current_step: 0
estimated_steps: 3
last_updated: 2026-06-06
---

# Sub-plan: Cap sub $i

Queued and waiting.
EOF
    printf '{"project_path":"%s","worker_engine":"claude-worker"}\n' "$SCRATCH/repos/proj-cap-$i" > "$proj/runtime/launcher/last-launch.json"
  done
}

run_cap() {
  echo "=== test_scheduler.sh cap ==="

  # Test 1: MaxConcurrent=2, 2 busy projects → capacity-full (idle)
  NUM_PROJECTS=3
  setup_cap_projects

  # Mark first 2 projects as busy with live PIDs
  sleep 60 &
  local pid1=$!
  echo "$pid1" > "$FAKE_DATA/projects/proj-cap-1/runtime/launcher/running.pid"
  sleep 60 &
  local pid2=$!
  echo "$pid2" > "$FAKE_DATA/projects/proj-cap-2/runtime/launcher/running.pid"

  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 2 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local decision reason live maxc
  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  reason=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['reason'])" <<<"$output")
  live=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['live'])" <<<"$output")
  maxc=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['max_concurrent'])" <<<"$output")
  [[ "$decision" == "idle" ]] || die "expected 'idle', got '$decision'. Output: $output"
  [[ "$reason" == "capacity-full" ]] || die "expected 'capacity-full', got '$reason'. Output: $output"
  [[ "$live" == "2" ]] || die "expected live=2, got '$live'. Output: $output"
  [[ "$maxc" == "2" ]] || die "expected max_concurrent=2, got '$maxc'. Output: $output"

  echo "PASS: MaxConcurrent=2, 2 busy → capacity-full"
  kill "$pid1" "$pid2" 2>/dev/null || true

  # Test 2: MaxConcurrent=3, 2 busy → capacity=1, dispatch one project
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 3 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  # Output should be 2 lines: skip-busy + skip-busy + dispatch (the free one)
  # Actually, with 2 busy and 1 free, we get skip-busy, skip-busy, dispatch
  local last_line
  last_line=$(echo "$output" | tail -1)

  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$last_line")
  [[ "$decision" == "dispatch" ]] || die "expected 'dispatch', got '$decision'. Output: $output"

  echo "PASS: MaxConcurrent=3, 2 busy → dispatch 1 free project"

  # Test 3: MaxConcurrent=1, 0 busy → dispatch one project (strict sequential)
  # Remove the running.pid files
  rm -f "$FAKE_DATA/projects/proj-cap-1/runtime/launcher/running.pid"
  rm -f "$FAKE_DATA/projects/proj-cap-2/runtime/launcher/running.pid"

  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 1 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  [[ "$decision" == "dispatch" ]] || die "expected 'dispatch', got '$decision'. Output: $output"

  echo "PASS: MaxConcurrent=1, 0 busy → dispatch one (strict sequential)"

  cleanup
}

run_fill() {
  echo "=== test_scheduler.sh fill ==="

  # AC-1: 2 ready projects + MaxConcurrent 5 → both dispatched in one cycle with distinct slots
  NUM_PROJECTS=2
  setup_cap_projects

  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 5 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  # Should have exactly 2 dispatch lines
  local line_count
  line_count=$(echo "$output" | wc -l | tr -d ' ')
  [[ "$line_count" == "2" ]] || die "expected 2 dispatch lines, got $line_count. Output: $output"

  local d1_decision d1_key d1_slot d1_command
  local d2_decision d2_key d2_slot d2_command
  d1_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$(echo "$output" | head -1)")
  d1_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$(echo "$output" | head -1)")
  d1_slot=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['slot'])" <<<"$(echo "$output" | head -1)")
  d1_command=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['command'])" <<<"$(echo "$output" | head -1)")
  d2_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$(echo "$output" | tail -1)")
  d2_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$(echo "$output" | tail -1)")
  d2_slot=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['slot'])" <<<"$(echo "$output" | tail -1)")
  d2_command=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['command'])" <<<"$(echo "$output" | tail -1)")

  [[ "$d1_decision" == "dispatch" ]] || die "expected dispatch, got $d1_decision"
  [[ "$d1_key" == "proj-cap-1" ]] || die "expected proj-cap-1, got $d1_key"
  [[ "$d1_slot" == "1" ]] || die "expected slot 1, got $d1_slot"
  [[ "$d1_command" == *"worker-home"* ]] || die "expected --worker-home in command, got $d1_command"
  [[ "$d1_command" != *"claude-worker-"* ]] || die "slot 1 home should be base (no suffix), got $d1_command"

  [[ "$d2_decision" == "dispatch" ]] || die "expected dispatch, got $d2_decision"
  [[ "$d2_key" == "proj-cap-2" ]] || die "expected proj-cap-2, got $d2_key"
  [[ "$d2_slot" == "2" ]] || die "expected slot 2, got $d2_slot"
  [[ "$d2_command" == *"claude-worker-2"* ]] || die "expected slot 2 home in command, got $d2_command"

  echo "PASS: AC-1 — 2 projects dispatched in one cycle with distinct slot homes"

  # AC-2: 3 ready + MaxConcurrent 2 → exactly 2 dispatched, 3rd not in output
  NUM_PROJECTS=3
  setup_cap_projects

  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 2 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  line_count=$(echo "$output" | wc -l | tr -d ' ')
  [[ "$line_count" == "2" ]] || die "expected 2 dispatch lines (MaxConcurrent 2), got $line_count. Output: $output"

  d1_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$(echo "$output" | head -1)")
  d2_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$(echo "$output" | tail -1)")
  d1_slot=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['slot'])" <<<"$(echo "$output" | head -1)")
  d2_slot=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['slot'])" <<<"$(echo "$output" | tail -1)")

  [[ "$d1_key" == "proj-cap-1" ]] || die "expected proj-cap-1, got $d1_key"
  [[ "$d2_key" == "proj-cap-2" ]] || die "expected proj-cap-2, got $d2_key"
  [[ "$d1_slot" == "1" ]] || die "expected slot 1, got $d1_slot"
  [[ "$d2_slot" == "2" ]] || die "expected slot 2, got $d2_slot"

  echo "PASS: AC-2 — 3 ready + MaxConcurrent 2 → exactly 2 dispatched"

  # AC-3: 1 busy + MaxConcurrent 2 → 1 dispatched (slot 2 distinct home)
  NUM_PROJECTS=2
  setup_cap_projects

  sleep 60 &
  local busy_pid=$!
  echo "$busy_pid" > "$FAKE_DATA/projects/proj-cap-1/runtime/launcher/running.pid"

  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 2 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local first_line last_line
  first_line=$(echo "$output" | head -1)
  last_line=$(echo "$output" | tail -1)

  local busy_decision
  busy_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$first_line")
  [[ "$busy_decision" == "skip-busy" ]] || die "expected skip-busy, got $busy_decision"

  local dispatch_decision dispatch_key dispatch_command
  dispatch_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$last_line")
  dispatch_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$last_line")
  dispatch_command=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['command'])" <<<"$last_line")

  [[ "$dispatch_decision" == "dispatch" ]] || die "expected dispatch, got $dispatch_decision"
  [[ "$dispatch_key" == "proj-cap-2" ]] || die "expected proj-cap-2, got $dispatch_key"
  [[ "$dispatch_command" == *"worker-home"* ]] || die "expected --worker-home in command"

  echo "PASS: AC-3 — 1 busy + MaxConcurrent 2 → 1 dispatched with slot home"
  kill "$busy_pid" 2>/dev/null || true

  # AC-4: MaxConcurrent 1 → strict sequential (1 dispatched)
  NUM_PROJECTS=2
  setup_cap_projects

  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 1 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local single_decision single_key single_slot
  single_decision=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['decision'])" <<<"$output")
  single_key=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['key'])" <<<"$output")
  single_slot=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['slot'])" <<<"$output")

  [[ "$single_decision" == "dispatch" ]] || die "expected dispatch, got $single_decision"
  [[ "$single_key" == "proj-cap-1" ]] || die "expected proj-cap-1, got $single_key"
  [[ "$single_slot" == "1" ]] || die "expected slot 1, got $single_slot"

  echo "PASS: AC-4 — MaxConcurrent 1 → strict sequential (1 dispatched)"
  cleanup
}

run_gates() {
  echo "=== test_scheduler.sh gates ==="

  # Test 1: default dispatch carries --run-local-checks in the command
  setup_two_queued_projects

  local output
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 1 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  local command
  command=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['command'])" <<<"$output")
  [[ "$command" == *"--run-local-checks"* ]] || die "expected '--run-local-checks' in default dispatch command, got '$command'. Output: $output"
  echo "PASS: default dispatch carries --run-local-checks"

  # Test 2: --no-local-checks opt-out removes the flag
  output=$(ILK_DATA_HOME="$FAKE_DATA" bash "$SCHEDULER_SCRIPT" --dry-run --once --max-concurrent 1 --no-local-checks 2>&1) || die "scheduler exited non-zero: $output"
  output="${output//$'\r'/}"

  command=$("$PYTHON" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['command'])" <<<"$output")
  [[ "$command" != *"--run-local-checks"* ]] || die "expected NO '--run-local-checks' with --no-local-checks, got '$command'. Output: $output"
  echo "PASS: --no-local-checks removes the gate flag from dispatch"

  cleanup
}

run_compat() {
  # Bash 3.2 / macOS portability guards.
  echo "=== test_scheduler.sh compat ==="

  # Test 1: scheduler.sh must not contain Bash 4-only features.
  local bash4_features
  bash4_features=$(grep -n 'declare -A\|mapfile\|readarray' "$SCHEDULER_SCRIPT" || true)
  if [[ -n "$bash4_features" ]]; then
    die "Bash 4-only features found in scheduler.sh:\n$bash4_features"
  fi
  echo "PASS: no Bash 4-only features (declare -A, mapfile, readarray)"

  # Test 2: scheduler.sh parses cleanly under /bin/bash (bash 3.2 on macOS).
  if [[ -x /bin/bash ]]; then
    echo "  /bin/bash version: $(/bin/bash --version 2>&1 | head -1)"
    /bin/bash -n "$SCHEDULER_SCRIPT" || die "/bin/bash -n failed on scheduler.sh"
    echo "PASS: /bin/bash -n syntax check passes"
  else
    echo "  SKIP: /bin/bash not found (not macOS)"
  fi

  # Test 3: dry-run dispatches correctly under /bin/bash on macOS.
  if [[ -x /bin/bash ]]; then
    setup_two_queued_projects
    local output decision
    output=$(ILK_DATA_HOME="$FAKE_DATA" /bin/bash "$SCHEDULER_SCRIPT" --dry-run --once 2>&1) || die "/bin/bash scheduler exited non-zero: $output"
    output="${output//$'\r'/}"
    decision=$(echo "$output" | head -1 | "$PYTHON" -c "import json,sys; print(json.loads(sys.stdin.read())['decision'])")
    [[ "$decision" == "dispatch" ]] || die "expected 'dispatch' under /bin/bash, got '$decision'. Output: $output"
    echo "PASS: /bin/bash dry-run dispatches correctly"
    cleanup
  fi
}

run_all() {
  run_scan
  run_select
  run_dispatch
  run_promote
  run_blacklist
  run_unresolved
  run_cap
  run_fill
  run_gates
  run_compat
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
  promote)
    run_promote
    ;;
  blacklist)
    run_blacklist
    ;;
  unresolved)
    run_unresolved
    ;;
  cap)
    run_cap
    ;;
  fill)
    run_fill
    ;;
  gates)
    run_gates
    ;;
  compat)
    run_compat
    ;;
  all)
    run_all
    ;;
  *)
    echo "Usage: $0 {scan|select|dispatch|promote|blacklist|unresolved|cap|fill|gates|compat|all}" >&2
    exit 1
    ;;
esac
