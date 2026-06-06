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

# --- main ---------------------------------------------------------------------

case "${1:-}" in
  scan)
    run_scan
    ;;
  *)
    echo "Usage: $0 {scan}" >&2
    exit 1
    ;;
esac
