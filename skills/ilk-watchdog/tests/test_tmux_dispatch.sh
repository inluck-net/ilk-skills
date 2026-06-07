#!/usr/bin/env bash
# Red test: tmux slot dispatch decision in scheduler.sh.
#
# AC-1: ILK_MULTIPLEXER=tmux + tmux present → dry-run prints tmux commands.
# AC-2: ILK_MULTIPLEXER=screen → dry-run prints screen command (no behavior change).
# AC-3: auto → tmux when present, screen when absent.
# AC-4: SKIP on hosts without bash command-v semantics.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SCHEDULER_SH="$REPO_ROOT/skills/ilk-watchdog/scripts/scheduler.sh"

# --- AC-4: skip if no bash command-v semantics ---
if ! command -v echo &>/dev/null; then
  echo "SKIP: bash lacks command -v semantics"
  exit 0
fi

# --- scratch dir ---
SCRATCH="$REPO_ROOT/scratch/tmux-test"
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"

# Create a minimal fake project structure for the scheduler to find.
# We need: a project key, a plans dir with a master, and a sentinel.
FAKE_KEY="test-tmux-proj"
FAKE_DATA="$SCRATCH/ilk-data/projects/$FAKE_KEY"
FAKE_PLANS="$FAKE_DATA/plans"
FAKE_RUNTIME="$FAKE_DATA/runtime"
mkdir -p "$FAKE_PLANS" "$FAKE_RUNTIME"

# Minimal master plan (active, with one pending sub-plan).
cat > "$FAKE_PLANS/MASTER-2026-06-07-test.md" <<'MASTER'
---
title: Test tmux
slug: test-tmux
created: 2026-06-07T00:00:00+08:00
status: active
priority: 5
pause_after_ship: false
branch: null
goal: test fixture
out_of_scope: []
cross_cutting_invariants: []
---

# Test tmux

## Sub-plan registry

| # | Order | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|---|
| 1 | 1 | [test-sub](./2026-06-07-test-sub.md) | test | 3 | pending |
MASTER

cat > "$FAKE_PLANS/2026-06-07-test-sub.md" <<'SUB'
---
plan: test-sub
status: pending
current_step: 0
tickets: []
priority: P2
estimated_steps: 3
last_updated: 2026-06-07
---

# Sub-plan for test
SUB

# Sentinel: running so scheduler sees it as dispatchable.
echo '{"state":"running","pid":99999999}' > "$FAKE_RUNTIME/last-exit.json"

# --- fake source repo ---
FAKE_REPO="$SCRATCH/projects/test-tmux-proj"
mkdir -p "$FAKE_REPO"
git init "$FAKE_REPO" >/dev/null 2>&1
git -C "$FAKE_REPO" commit --allow-empty -m "init" >/dev/null 2>&1

# --- last-launch.json so scheduler can resolve repo_path ---
FAKE_LAUNCHER="$FAKE_DATA/runtime/launcher"
mkdir -p "$FAKE_LAUNCHER"
cat > "$FAKE_LAUNCHER/last-launch.json" <<EOF
{"project_path": "$FAKE_REPO"}
EOF

# --- stub tmux on PATH ---
STUB_BIN="$SCRATCH/bin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/tmux" <<'TMUX_STUB'
#!/usr/bin/env bash
echo "TMUX_CALLED: $*"
TMUX_STUB
chmod +x "$STUB_BIN/tmux"

# --- helper: run scheduler dry-run once ---
run_dry_run() {
  local mux_env="$1"
  env \
    ILK_DATA_HOME="$SCRATCH/ilk-data" \
    ILK_MULTIPLEXER="$mux_env" \
    PATH="$STUB_BIN:$PATH" \
    bash "$SCHEDULER_SH" --dry-run --once 2>&1 || true
}

PASS=0
FAIL=0

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qi "$needle"; then
    echo "  PASS: $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $label (expected '$needle' in output)"
    FAIL=$((FAIL + 1))
  fi
}

assert_not_contains() {
  local label="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qi "$needle"; then
    echo "  FAIL: $label (unexpected '$needle' in output)"
    FAIL=$((FAIL + 1))
  else
    echo "  PASS: $label"
    PASS=$((PASS + 1))
  fi
}

# --- AC-1: ILK_MULTIPLEXER=tmux + tmux present → tmux commands ---
echo "=== AC-1: tmux mode ==="
OUT1=$(run_dry_run "tmux")
echo "$OUT1"
# The dispatch command should include tmux new-session or tmux new-window
assert_contains "AC-1: dispatch command uses tmux" "tmux.*new\|tmux.*session\|tmux.*window\|multiplexer.*tmux" "$OUT1"

# --- AC-2: ILK_MULTIPLEXER=screen → screen command (no tmux) ---
echo ""
echo "=== AC-2: screen mode ==="
OUT2=$(run_dry_run "screen")
echo "$OUT2"
# Should NOT mention tmux in the dispatch command when screen is forced.
assert_not_contains "AC-2: screen mode has no tmux" "tmux.*new\|tmux.*session" "$OUT2"
assert_contains "AC-2: dispatch command present" "dispatch\|launch" "$OUT2"

# --- AC-3: auto → tmux when present, screen when absent ---
echo ""
echo "=== AC-3a: auto + tmux present ==="
OUT3A=$(run_dry_run "auto")
echo "$OUT3A"
assert_contains "AC-3a: auto+tmux-present dispatches with tmux" "tmux.*new\|tmux.*session\|tmux.*window\|multiplexer.*tmux" "$OUT3A"

echo ""
echo "=== AC-3b: auto + tmux absent ==="
# Remove tmux stub from PATH (keep python).
OUT3B=$(env \
  ILK_DATA_HOME="$SCRATCH/ilk-data" \
  ILK_MULTIPLEXER="auto" \
  PATH="$(dirname "$(which python 2>/dev/null || which python3 2>/dev/null)"):/usr/bin:/bin" \
  bash "$SCHEDULER_SH" --dry-run --once 2>&1 || true)
echo "$OUT3B"
# When tmux absent, should NOT use tmux commands.
assert_not_contains "AC-3b: auto+tmux-absent has no tmux" "tmux.*new\|tmux.*session" "$OUT3B"

# --- summary ---
echo ""
echo "=== Summary ==="
echo "PASS: $PASS  FAIL: $FAIL"

# cleanup
rm -rf "$SCRATCH"

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
