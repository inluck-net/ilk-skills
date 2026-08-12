#!/usr/bin/env bash
# =============================================================================
# Test: the runner distinguishes "all shipped" from "blocked, no runnable".
#
# AC-1: all-shipped path prints the canonical message + stop_reason: already-shipped
# AC-2: blocked-only path prints a distinct message + stop_reason: blocked-no-runnable
# AC-3: both paths return 0
# AC-4: loop_status.py is not modified by this sub-plan
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RUNNER="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"
GOLDEN_DIR="$REPO_ROOT/skills/ilk-loop/tests/golden"
GOLDEN_FILE="$GOLDEN_DIR/all_shipped_stdout.txt"

failures=()

fail() { failures+=("$1"); }

# --- normaliser: strip run-specific noise for stable comparison -----------
normalise() {
  sed -E \
    -e '/^\[runner\] CLAUDE_CONFIG_DIR=/d' \
    -e '/^Detected .*settings\.json env block/d' \
    -e '/^Warning: ANTHROPIC_API_KEY not set/d' \
    -e 's/^([[:space:]]*Model:).*/\1 <MODEL>/' \
    -e 's/^([[:space:]]*API base:).*/\1 <API_BASE>/' \
    -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{4}/<TIMESTAMP>/g' \
    -e 's/run_id=[a-f0-9-]+/run_id=<RUN_ID>/g' \
    -e 's|/private/var/folders/[^ /]+|<TMP>|g' \
    -e 's|/var/folders/[^ /]+|<TMP>|g' \
    -e 's|/tmp/[^ /]+|<TMP>|g' \
    -e 's|tmp\.[A-Za-z0-9]+|tmp.<RAND>|g' \
    -e 's|private-var-folders-[a-z0-9-]+-t-tmp-[a-z0-9-]+|<PROJECT_KEY>|g' \
    -e 's|runs/[0-9]{8}-[0-9]{6}|runs/<RUN_TS>|g'
}

# --- helpers: build fixture project dirs -----------------------------------

# Compute the project key for a given path (matches ilk_paths.project_key).
project_key_for() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/skills/ilk-loop/scripts')
from ilk_paths import project_key
from pathlib import Path
print(project_key(Path('$1')))
" 2>/dev/null
}

build_fixture_all_shipped() {
  local dir="$1"
  local key
  key=$(project_key_for "$dir")
  local plans="$dir/.ilk-data/projects/$key/plans"
  mkdir -p "$plans"

  cat > "$plans/MASTER-2026-08-12-execution.md" << 'EOF'
---
title: MASTER-2026-08-12-execution
created: 2026-06-07T00:00:00+08:00
status: active
priority: 0
pause_after_ship: false
---

# MASTER-2026-08-12-execution

## Sub-plan registry

| # | Sub-plan | Status |
|---|---|---|
| 1 | [2026-08-12-task-a.md](./2026-08-12-task-a.md) | shipped |
| 2 | [2026-08-12-task-b.md](./2026-08-12-task-b.md) | shipped |
EOF

  for sp in task-a task-b; do
    cat > "$plans/2026-08-12-${sp}.md" << EOF
---
plan: 2026-08-12-${sp}
status: shipped
current_step: 3
estimated_steps: 3
last_updated: 2026-08-12
---

# 2026-08-12-${sp}
EOF
  done

  # The runner needs a .git dir to resolve the project root.
  cd "$dir" && git init -q && git commit -q --allow-empty -m "init"
}

build_fixture_blocked_only() {
  local dir="$1"
  local key
  key=$(project_key_for "$dir")
  local plans="$dir/.ilk-data/projects/$key/plans"
  mkdir -p "$plans"

  cat > "$plans/MASTER-2026-08-12-execution.md" << 'EOF'
---
title: MASTER-2026-08-12-execution
created: 2026-06-07T00:00:00+08:00
status: active
priority: 0
pause_after_ship: false
---

# MASTER-2026-08-12-execution

## Sub-plan registry

| # | Sub-plan | Status |
|---|---|---|
| 1 | [2026-08-12-task-a.md](./2026-08-12-task-a.md) | shipped |
| 2 | [2026-08-12-task-b.md](./2026-08-12-task-b.md) | blocked |
EOF

  cat > "$plans/2026-08-12-task-a.md" << 'EOF'
---
plan: 2026-08-12-task-a
status: shipped
current_step: 3
estimated_steps: 3
last_updated: 2026-08-12
---

# 2026-08-12-task-a
EOF

  cat > "$plans/2026-08-12-task-b.md" << 'EOF'
---
plan: 2026-08-12-task-b
status: blocked
current_step: 0
estimated_steps: 4
last_updated: 2026-08-12
---

# 2026-08-12-task-b
EOF

  cd "$dir" && git init -q && git commit -q --allow-empty -m "init"
}

# --- mode: --compare-golden (used by local_checks in step 2) ---------------
if [[ "${1:-}" == "--compare-golden" ]]; then
  if [[ ! -f "$GOLDEN_FILE" ]]; then
    echo "FAIL: golden file not found: $GOLDEN_FILE"
    exit 1
  fi
  tmp_dir=$(mktemp -d)
  trap 'rm -rf "$tmp_dir"' EXIT
  build_fixture_all_shipped "$tmp_dir/fixture-a"

  env HOME="$tmp_dir/fixture-a" ILK_SKILL_HOME="$REPO_ROOT/skills" ILK_DATA_HOME="$tmp_dir/fixture-a/.ilk-data" \
    bash "$RUNNER" --project-path "$tmp_dir/fixture-a" --max-iterations 1 2>&1 \
    | normalise > "$tmp_dir/actual.txt"

  if ! diff -q "$GOLDEN_FILE" "$tmp_dir/actual.txt" >/dev/null 2>&1; then
    echo "FAIL: all-shipped stdout differs from golden baseline"
    diff -u "$GOLDEN_FILE" "$tmp_dir/actual.txt" || true
    exit 1
  fi
  echo "OK — all-shipped stdout matches golden baseline."
  exit 0
fi

# --- mode: --record-golden (explicit re-recording only) --------------------
if [[ "${1:-}" == "--record-golden" ]]; then
  tmp_dir=$(mktemp -d)
  trap 'rm -rf "$tmp_dir"' EXIT
  build_fixture_all_shipped "$tmp_dir/fixture-a"

  env HOME="$tmp_dir/fixture-a" ILK_SKILL_HOME="$REPO_ROOT/skills" ILK_DATA_HOME="$tmp_dir/fixture-a/.ilk-data" \
    bash "$RUNNER" --project-path "$tmp_dir/fixture-a" --max-iterations 1 2>&1 \
    | normalise > "$GOLDEN_FILE"
  echo "Golden re-recorded: $GOLDEN_FILE"
  exit 0
fi

# --- main test body --------------------------------------------------------

TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# --- fixture A: all shipped ------------------------------------------------
echo "=== Fixture A: all shipped ==="
build_fixture_all_shipped "$TMPDIR_TEST/fixture-a"

out_a=$(HOME="$TMPDIR_TEST/fixture-a" ILK_SKILL_HOME="$REPO_ROOT/skills" ILK_DATA_HOME="$TMPDIR_TEST/fixture-a/.ilk-data" \
  bash "$RUNNER" --project-path "$TMPDIR_TEST/fixture-a" --max-iterations 1 2>&1) && rc_a=0 || rc_a=$?

echo "$out_a" | normalise > "$TMPDIR_TEST/fixture-a-stdout.txt"

if [[ "$rc_a" -ne 0 ]]; then
  fail "fixture A: expected exit 0, got exit $rc_a"
fi

if [[ "$out_a" != *"All sub-plans already shipped"* ]]; then
  fail "fixture A: expected 'All sub-plans already shipped' in output"
fi

if [[ "$out_a" != *"ALL SHIPPED"* ]]; then
  fail "fixture A: expected 'ALL SHIPPED' in output"
fi

# Check JSONL stop_reason
jsonl_a=$(find "$TMPDIR_TEST/fixture-a/.ilk-data" -name "*.jsonl" -type f 2>/dev/null | head -1)
if [[ -n "$jsonl_a" ]]; then
  if ! grep -q '"stop_reason":"already-shipped"' "$jsonl_a" 2>/dev/null; then
    fail "fixture A: expected stop_reason 'already-shipped' in JSONL"
  fi
fi

# Golden baseline is recorded explicitly via --record-golden; never overwrite
# here so that --compare-golden is a genuine before/after gate.
if [[ ! -f "$GOLDEN_FILE" ]]; then
  echo "  -> golden file missing; run with --record-golden first"
  fail "golden file not found: $GOLDEN_FILE"
fi

# --- fixture B: blocked only -----------------------------------------------
echo ""
echo "=== Fixture B: blocked only ==="
build_fixture_blocked_only "$TMPDIR_TEST/fixture-b"

out_b=$(HOME="$TMPDIR_TEST/fixture-b" ILK_SKILL_HOME="$REPO_ROOT/skills" ILK_DATA_HOME="$TMPDIR_TEST/fixture-b/.ilk-data" \
  bash "$RUNNER" --project-path "$TMPDIR_TEST/fixture-b" --max-iterations 1 2>&1) && rc_b=0 || rc_b=$?

echo "$out_b" | normalise > "$TMPDIR_TEST/fixture-b-stdout.txt"

if [[ "$rc_b" -ne 0 ]]; then
  fail "fixture B: expected exit 0, got exit $rc_b"
fi

# AC-2: fixture B must report "blocked", not "already-shipped".
if [[ "$out_b" == *"All sub-plans already shipped"* ]]; then
  fail "fixture B: reports 'already-shipped' — should report blocked"
fi

if [[ "$out_b" != *"BLOCKED"* ]]; then
  fail "fixture B: expected 'BLOCKED' in output"
fi

if [[ "$out_b" != *"2026-08-12-task-b.md"* ]]; then
  fail "fixture B: expected blocked sub-plan name in output"
fi

# AC-2: check JSONL stop_reason
jsonl_b=$(find "$TMPDIR_TEST/fixture-b/.ilk-data" -name "*.jsonl" -type f 2>/dev/null | head -1)
if [[ -n "$jsonl_b" ]]; then
  if ! grep -q '"stop_reason":"blocked-no-runnable"' "$jsonl_b" 2>/dev/null; then
    fail "fixture B: expected stop_reason 'blocked-no-runnable' in JSONL"
  fi
fi

# AC-3: both paths must return 0
if [[ "$rc_b" -ne 0 ]]; then
  fail "fixture B: expected exit 0, got exit $rc_b"
fi

# --- report ----------------------------------------------------------------
echo ""
if [[ "${#failures[@]}" -gt 0 ]]; then
  echo "FAIL — ${#failures[@]} failure(s):"
  for f in "${failures[@]}"; do
    echo "  - $f"
  done
  exit 1
else
  echo "OK — all assertions passed."
  exit 0
fi
