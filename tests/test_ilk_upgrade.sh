#!/usr/bin/env bash
set -euo pipefail

# Hermetic test for upgrade.sh — no network, no mutation of the real repo.
#
# Creates a throwaway git repo + bare remote as its fixture, copies
# upgrade.sh into the right relative path, and exercises:
#   - --check reports "behind" when remote is ahead
#   - --apply fast-forwards and prints changelog
#   - PID guard refuses --apply when a live PID file exists
#   - dirty tree aborts --apply without --force
#
# HOME is redirected to a temp dir so the test never touches real
# ~/.cursor, ~/.claude, or ~/.codex.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
UPGRADE_SH="${REPO_ROOT}/skills/ilk-upgrade/scripts/upgrade.sh"

PASS=0
FAIL=0

check() {
  # check "<description>" "<haystack>" contains|absent "<needle>"
  local desc="$1" hay="$2" mode="$3" needle="$4"
  local found=0
  case "$hay" in *"$needle"*) found=1 ;; esac
  if { [[ "$mode" == contains && $found -eq 1 ]] || \
       [[ "$mode" == absent  && $found -eq 0 ]]; }; then
    PASS=$((PASS + 1))
    echo "  PASS: ${desc}"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${desc} (mode=${mode}, needle=${needle})"
  fi
}

check_exit() {
  # check_exit "<description>" <expected_exit> <actual_exit>
  local desc="$1" expected="$2" actual="$3"
  if [[ "$expected" -eq "$actual" ]]; then
    PASS=$((PASS + 1))
    echo "  PASS: ${desc}"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${desc} (expected exit ${expected}, got ${actual})"
  fi
}

# Helper: advance the remote by one commit, reset local to behind
advance_remote() {
  cd "$WORK"
  echo "content-$(date +%s%N)" > "file-$(date +%s%N).txt"
  git add -A
  git commit -m "advance-$(date +%s%N)" >/dev/null 2>&1
  git push origin main >/dev/null 2>&1
  git reset --hard HEAD~1 >/dev/null 2>&1
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAKE_HOME="$TMP/home"
mkdir -p "$FAKE_HOME"

# --- set up fixture repo -----------------------------------------------------

BARE="$TMP/bare.git"
WORK="$TMP/work"

# Create bare remote. Pin the initial branch to 'main' so the fixture is
# deterministic regardless of the operator's git init.defaultBranch (Windows
# defaults to 'master', which would break the `git push origin main` below).
git init -b main --bare "$BARE" >/dev/null 2>&1

# Create working clone
git clone "$BARE" "$WORK" >/dev/null 2>&1
cd "$WORK"
git config user.email "test@test.com"
git config user.name "Test"
# Ensure the working clone is on 'main' before the first commit, regardless of
# the operator's init.defaultBranch (the clone of the empty bare inherits the
# local default, which may be 'master').
git checkout -b main >/dev/null 2>&1 || git switch -c main >/dev/null 2>&1 || true

# Create minimal ilk-skills structure
mkdir -p skills/ilk-upgrade/scripts commands

# Stub install.sh — just echoes what it would do
cat > install.sh << 'INSTALL_EOF'
#!/usr/bin/env bash
echo "[stub] install.sh called with: $*"
INSTALL_EOF
chmod +x install.sh

# Copy upgrade.sh into the fixture
cp "$UPGRADE_SH" skills/ilk-upgrade/scripts/upgrade.sh
chmod +x skills/ilk-upgrade/scripts/upgrade.sh

# Initial commit
git add -A
git commit -m "initial" >/dev/null 2>&1
git push origin main >/dev/null 2>&1

UPGRADE="skills/ilk-upgrade/scripts/upgrade.sh"

# === Test 1: --check reports "up to date" when current =====================

echo ""
echo "=== Test 1: --check up to date ==="
out="$(HOME="$FAKE_HOME" bash "$UPGRADE" --check 2>&1 || true)"
check "up to date message" "$out" contains "up to date"

# === Test 2: --check reports "behind" when remote is ahead =================

echo ""
echo "=== Test 2: --check reports behind ==="
advance_remote

out="$(HOME="$FAKE_HOME" bash "$UPGRADE" --check 2>&1 || true)"
check "behind message" "$out" contains "behind by 1 commit"

# === Test 3: --apply fast-forwards and prints changelog ====================

echo ""
echo "=== Test 3: --apply fast-forward ==="
out="$(HOME="$FAKE_HOME" bash "$UPGRADE" --apply 2>&1 || true)"
check "apply prints changelog" "$out" contains "Changelog:"
check "apply shows links current or no reinstall" "$out" contains "Links current"

# Verify we're now at the latest
head_rev="$(git -C "$WORK" rev-parse HEAD)"
origin_rev="$(git -C "$WORK" rev-parse origin/main)"
if [[ "$head_rev" == "$origin_rev" ]]; then
  PASS=$((PASS + 1))
  echo "  PASS: HEAD matches origin/main after --apply"
else
  FAIL=$((FAIL + 1))
  echo "  FAIL: HEAD mismatch after --apply"
fi

# === Test 4: --apply reports "already current" =============================

echo ""
echo "=== Test 4: --apply already current ==="
out="$(HOME="$FAKE_HOME" bash "$UPGRADE" --apply 2>&1 || true)"
check "already current message" "$out" contains "already current"

# === Test 5: PID guard refuses --apply =====================================

echo ""
echo "=== Test 5: PID guard refuses --apply ==="
advance_remote

# Create a fake PID file with a live PID (use $$, which is always alive)
ILK_DATA_DIR="$FAKE_HOME/.ilk-data"
pid_dir="$ILK_DATA_DIR/projects/test-proj/runtime/launcher"
mkdir -p "$pid_dir"
echo "$$" > "$pid_dir/running.pid"

out="" exit_code=0
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" bash "$UPGRADE" --apply 2>&1)" || exit_code=$?
check_exit "PID guard exit code" 1 "$exit_code"
check "PID guard error message" "$out" contains "live loop/watchdog detected"

# === Test 6: --force overrides PID guard ===================================

echo ""
echo "=== Test 6: --force overrides PID guard ==="
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" bash "$UPGRADE" --apply --force 2>&1 || true)"
check "force overrides PID guard" "$out" contains "Changelog:"

# === Test 7: dirty tree aborts --apply =====================================

echo ""
echo "=== Test 7: dirty tree aborts --apply ==="
advance_remote

# Make the working tree dirty
cd "$WORK"
echo "dirty" > dirty_file.txt

# Remove PID guard for this test
rm -rf "$ILK_DATA_DIR"

out="" exit_code=0
out="$(HOME="$FAKE_HOME" bash "$UPGRADE" --apply 2>&1)" || exit_code=$?
check_exit "dirty tree exit code" 2 "$exit_code"
check "dirty tree error message" "$out" contains "dirty working tree"

# === Test 8: --force overrides dirty tree ==================================

echo ""
echo "=== Test 8: --force overrides dirty tree ==="
# Clean up dirty file so the pull can succeed
rm -f "$WORK/dirty_file.txt"

out="$(HOME="$FAKE_HOME" bash "$UPGRADE" --apply --force 2>&1 || true)"
check "force overrides dirty tree" "$out" contains "Changelog:"

# === Test 9: scheduler.pid guard refuses --apply ============================

echo ""
echo "=== Test 9: scheduler.pid guard refuses --apply ==="
advance_remote

# Clean up any leftover PID files from earlier tests
rm -rf "$ILK_DATA_DIR"

# Create a fake scheduler PID file with a live PID
mkdir -p "$ILK_DATA_DIR"
echo "$$" > "$ILK_DATA_DIR/scheduler.pid"

out="" exit_code=0
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" bash "$UPGRADE" --apply 2>&1)" || exit_code=$?
check_exit "scheduler.pid guard exit code" 1 "$exit_code"
check "scheduler.pid guard error message" "$out" contains "live loop/watchdog detected"
check "scheduler.pid listed in output" "$out" contains "scheduler (PID"

# Clean up
rm -f "$ILK_DATA_DIR/scheduler.pid"

# === Test 10: guard error names stop_watchdog.sh =============================

echo ""
echo "=== Test 10: guard error names stop_watchdog.sh ==="

# Re-create the scheduler PID to trigger the guard
echo "$$" > "$ILK_DATA_DIR/scheduler.pid"

out="" exit_code=0
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" bash "$UPGRADE" --apply 2>&1)" || exit_code=$?
check "guard error names stop_watchdog.sh" "$out" contains "stop_watchdog.sh"

# Clean up
rm -f "$ILK_DATA_DIR/scheduler.pid"

# === Results ================================================================

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
