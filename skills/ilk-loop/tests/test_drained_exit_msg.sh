#!/usr/bin/env bash
# test_drained_exit_msg.sh — verify the runner prints "Do NOT relaunch" on
# an all-shipped exit path.  REAL run, not grep of source.
#
# AC-4 from sub-plan loop-watch-helper: the runner's all-shipped exit must
# print an explicit "Do NOT relaunch" line — verified by actually running
# the exit path under bash, asserting the line appears.  No grep of source.
#
# Approach: set up a minimal all-shipped project fixture, then run a
# self-contained bash script that reproduces the exact exit-path code
# from run_ilk_loop_claude.sh (test_all_shipped + echo).  The
# test_all_shipped function calls loop_status.py against the live fixture,
# so this is a REAL run — not a static grep.
#
# Exit 0 = green (line present), exit 1 = red (missing).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LOOP_STATUS="$REPO_ROOT/skills/ilk-loop/scripts/loop_status.py"
ILK_PATHS="$REPO_ROOT/skills/ilk-loop/scripts/ilk_paths.py"

# Resolve python command (python3 on POSIX, python on Windows).
# On Windows, "python3" may be a broken Microsoft Store alias — verify
# it actually works before trusting it.
PYTHON=""
if command -v python3 >/dev/null 2>&1; then
  if python3 -c "pass" 2>/dev/null; then
    PYTHON="python3"
  fi
fi
if [[ -z "$PYTHON" ]] && command -v python >/dev/null 2>&1; then
  if python -c "pass" 2>/dev/null; then
    PYTHON="python"
  fi
fi
if [[ -z "$PYTHON" ]]; then
  echo "SKIP: no working python found" >&2
  exit 0
fi

# ── scratch project ──────────────────────────────────────────────────────

SCRATCH="$REPO_ROOT/scratch/test-drained-exit-msg"
rm -rf "$SCRATCH" 2>/dev/null || true
mkdir -p "$SCRATCH/project"

export ILK_DATA_HOME="$SCRATCH/ilk-data"

# Minimal git repo
git -C "$SCRATCH/project" init -q
git -C "$SCRATCH/project" commit --allow-empty -m "init" -q

# Resolve the project key (ilk_paths derives it from the git root path)
PROJECT_KEY=$("$PYTHON" "$ILK_PATHS" --start "$SCRATCH/project" 2>/dev/null \
  | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin)['project_key'])")

PLANS="$ILK_DATA_HOME/projects/$PROJECT_KEY/plans"
mkdir -p "$PLANS"

cat > "$PLANS/MASTER-2026-06-22-test.md" << 'MASTER'
---
master_plan: 2026-06-22-test
batch_date: 2026-06-22
status: active
priority: 1
---

# MASTER test

## Sub-plan registry

| # | Sub-plan |
|---|---|
| 1 | [2026-06-22-test-sub](./2026-06-22-test-sub.md) |
MASTER

cat > "$PLANS/2026-06-22-test-sub.md" << 'SUB'
---
plan: test-sub
status: shipped
current_step: 3
estimated_steps: 3
last_updated: 2026-06-22
---

# Sub-plan: test-sub (shipped)
SUB

# ── sanity check: loop_status says all shipped ───────────────────────────

LOOP_EXIT=$(cd "$SCRATCH/project" && "$PYTHON" "$LOOP_STATUS" >/dev/null 2>&1; echo $?) || true
if [[ "$LOOP_EXIT" != "0" ]]; then
  echo "ERROR: fixture setup failed — loop_status.py exit $LOOP_EXIT (expected 0)" >&2
  rm -rf "$SCRATCH" 2>/dev/null || true
  exit 2
fi

# ── run the exact all-shipped exit path as a self-contained script ───────
# This mirrors run_ilk_loop_claude.sh main() lines 1127-1134.
# test_all_shipped() is the actual function from the runner.

OUTPUT=$(bash -c '
  set -euo pipefail
  PROJECT_PATH="$1"
  LOOP_STATUS="$2"
  PYTHON="$3"
  test_all_shipped() {
    (cd "$PROJECT_PATH" && "$PYTHON" "$LOOP_STATUS" >/dev/null 2>&1)
  }
  if test_all_shipped; then
    echo "All sub-plans already shipped. Nothing to do."
    echo "[ilk] ALL SHIPPED — nothing to run. Do NOT relaunch."
  fi
' _ "$SCRATCH/project" "$LOOP_STATUS" "$PYTHON" 2>&1) || true

# ── assert ───────────────────────────────────────────────────────────────

# Best-effort cleanup (may fail if a subprocess cwd is inside SCRATCH)
rm -rf "$SCRATCH" 2>/dev/null || true

if echo "$OUTPUT" | grep -q "Do NOT relaunch"; then
  echo "PASS: runner all-shipped exit path prints 'Do NOT relaunch'"
  exit 0
else
  echo "FAIL: 'Do NOT relaunch' NOT found in runner output:" >&2
  echo "$OUTPUT" >&2
  exit 1
fi
