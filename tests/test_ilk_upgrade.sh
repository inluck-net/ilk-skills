#!/usr/bin/env bash
set -euo pipefail

# Hermetic test for upgrade.sh — no network, no mutation of the real repo.
#
# Creates a throwaway git repo + bare remote as its fixture, copies
# upgrade.sh into the right relative path, and exercises:
#   - --check reports "behind" when remote is ahead
#   - --apply fast-forwards and prints changelog
#   - PID guard refuses --apply when a live loop/watchdog PID file exists
#   - scheduler bounce via bounce_daemons.sh (not a refusal)
#   - dirty tree aborts --apply without --force
#
# HOME is redirected to a temp dir so the test never touches real
# ~/.cursor, ~/.claude, or ~/.codex.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
UPGRADE_SH="${REPO_ROOT}/skills/ilk-upgrade/scripts/upgrade.sh"
# upgrade.sh sources the shared data-dir resolver relative to its own repo
# root, so the fixture must carry it too or every assertion fails on a
# "No such file or directory" from the `source` line.
DATA_DIR_SH="${REPO_ROOT}/skills/ilk-loop/scripts/_ilk_data_dir.sh"

PASS=0
FAIL=0

check() {
  # check "<description>" "<haystack>" contains|absent "<needle>"
  local desc="$1" hay="$2" mode="$3" needle="$4"
  local found=0
  case "$hay" in *"$needle"*) found=1 ;; esac
  if { [[ "$mode" == "contains" && $found -eq 1 ]] || \
       [[ "$mode" == "absent"  && $found -eq 0 ]]; }; then
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
mkdir -p skills/ilk-upgrade/scripts skills/ilk-watchdog/scripts skills/ilk-loop/scripts commands

# Carry the sourced helpers into the fixture — upgrade.sh sources both
# _ilk_data_dir.sh and _ilk_pid.sh relative to its own location.
cp "$DATA_DIR_SH" skills/ilk-loop/scripts/_ilk_data_dir.sh
cp "$REPO_ROOT/skills/ilk-loop/scripts/_ilk_pid.sh" skills/ilk-loop/scripts/_ilk_pid.sh

# Stub install.sh — just echoes what it would do
cat > install.sh << 'INSTALL_EOF'
#!/usr/bin/env bash
echo "[stub] install.sh called with: $*"
INSTALL_EOF
chmod +x install.sh

# --- fake bounce_daemons.sh --------------------------------------------------
# Logs invocations to a file and simulates bounce behaviour.  The real script
# would call launchctl; our fake calls a fake launchctl on PATH so the test
# can assert the command sequence (AC-6 from SP1).

cat > skills/ilk-watchdog/scripts/bounce_daemons.sh << 'BOUNCE_EOF'
#!/usr/bin/env bash
# Fake bounce_daemons.sh for upgrade tests.
# Logs: "bounce_daemons called: <args>"
# Determines exit code from state file presence (simplified fake).

BOUNCE_LOG="${BOUNCE_LOG:-/dev/null}"
echo "bounce_daemons called: $*" >> "$BOUNCE_LOG"

# Caller can force an exit code (e.g. BOUNCE_EXIT_CODE=2 for unreachable).
if [[ -n "${BOUNCE_EXIT_CODE:-}" ]]; then
  exit "$BOUNCE_EXIT_CODE"
fi

# Simulate: if state file is absent → stale (exit 1), if present with
# matching head → fresh (exit 0).  We use ILK_BOUNCE_TOOLKIT_PATH to
# find the state file, same as the real script.
ILK_DATA="${ILK_DATA_HOME:-${ILK_DATA_DIR:-$HOME/.ilk-data}}"
STATE_FILE="$ILK_DATA/scheduler.state.json"

CHECK_ONLY=0
for arg in "$@"; do
  [[ "$arg" == "--check" ]] && CHECK_ONLY=1
done

if [[ ! -f "$STATE_FILE" ]]; then
  # Absent state → stale → would bounce
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "stale: scheduler — state file absent (would bounce)"
  else
    echo "bouncing: scheduler — state file absent"
    # Call fake launchctl to record the bounce
    if command -v launchctl >/dev/null 2>&1; then
      id_u=$(id -u)
      launchctl bootout "gui/$id_u/net.inluck.ilk.scheduler" 2>/dev/null || true
      launchctl bootstrap "gui/$id_u" "$HOME/Library/LaunchAgents/net.inluck.ilk.scheduler.plist"
    fi
  fi
  exit 1
fi

# Try to parse toolkit_head
recorded_head=""
if grep -q '"toolkit_head"' "$STATE_FILE" 2>/dev/null; then
  recorded_head=$(sed -n 's/.*"toolkit_head"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$STATE_FILE")
fi

toolkit_path="${ILK_BOUNCE_TOOLKIT_PATH:-.}"
current_head=$(git -C "$toolkit_path" rev-parse HEAD 2>/dev/null || echo "unknown")

if [[ "$recorded_head" == "$current_head" ]]; then
  echo "fresh: scheduler — toolkit_head matches HEAD"
  exit 0
fi

# Stale
if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "stale: scheduler — recorded $recorded_head, HEAD $current_head (would bounce)"
else
  echo "bouncing: scheduler — recorded $recorded_head, HEAD $current_head"
  if command -v launchctl >/dev/null 2>&1; then
    id_u=$(id -u)
    launchctl bootout "gui/$id_u/net.inluck.ilk.scheduler" 2>/dev/null || true
    launchctl bootstrap "gui/$id_u" "$HOME/Library/LaunchAgents/net.inluck.ilk.scheduler.plist"
  fi
fi
exit 1
BOUNCE_EOF
chmod +x skills/ilk-watchdog/scripts/bounce_daemons.sh

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

# Create a fake PID file with a live PID whose command matches an ilk process
# pattern (so ilk_pid_alive returns 0).  $$ won't work — its command is
# "bash tests/test_ilk_upgrade.sh", which doesn't match the ilk patterns.
# Spawn a background script whose name contains "run_ilk_loop" so the
# command-pattern check in ilk_pid_alive recognises it.
ILK_DATA_DIR="$FAKE_HOME/.ilk-data"
pid_dir="$ILK_DATA_DIR/projects/test-proj/runtime/launcher"
mkdir -p "$pid_dir"
fake_loop="$TMP/run_ilk_loop_claude.sh"
cat > "$fake_loop" << 'SLEEP_EOF'
#!/usr/bin/env bash
sleep 60
SLEEP_EOF
chmod +x "$fake_loop"
"$fake_loop" &
FAKE_PID=$!
echo "$FAKE_PID" > "$pid_dir/running.pid"

out="" exit_code=0
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" bash "$UPGRADE" --apply 2>&1)" || exit_code=$?
check_exit "PID guard exit code" 1 "$exit_code"
check "PID guard error message" "$out" contains "live loop/watchdog detected"
kill "$FAKE_PID" 2>/dev/null || true

# === Test 6: --force overrides PID guard ===================================

echo ""
echo "=== Test 6: --force overrides PID guard ==="
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" bash "$UPGRADE" --apply --force 2>&1 || true)"
check "force overrides PID guard" "$out" contains "Changelog:"
# Clean up PID files so they don't interfere with later tests
rm -rf "$ILK_DATA_DIR"

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

# === Test 9: scheduler bounce via bounce_daemons.sh on --apply =============

echo ""
echo "=== Test 9: scheduler bounce via bounce_daemons.sh ==="
advance_remote

# Set up bounce logging and fake launchctl
BOUNCE_LOG="$TMP/bounce.log"
: > "$BOUNCE_LOG"
FAKE_BIN="$TMP/fakebin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/launchctl" << 'LAUNCHCTL_EOF'
#!/usr/bin/env bash
echo "launchctl $*" >> "${LAUNCHCTL_LOG:-/dev/null}"
exit 0
LAUNCHCTL_EOF
chmod +x "$FAKE_BIN/launchctl"

LAUNCHCTL_LOG="$TMP/launchctl.log"
: > "$LAUNCHCTL_LOG"

# No scheduler.state.json in the fixture → bounce_daemons.sh treats as stale.
# upgrade.sh should call bounce_daemons.sh and still exit 0 (successful upgrade).
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" \
  BOUNCE_LOG="$BOUNCE_LOG" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  PATH="$FAKE_BIN:$PATH" \
  bash "$UPGRADE" --apply 2>&1 || true)"
check "upgrade prints changelog" "$out" contains "Changelog:"
check "upgrade calls bounce_daemons.sh" "$(cat "$BOUNCE_LOG")" contains "bounce_daemons called"
check "fake launchctl bootstrap called" "$(cat "$LAUNCHCTL_LOG")" contains "bootstrap"

# === Test 10: idempotent — second --apply does not bounce ==================

echo ""
echo "=== Test 10: idempotent — second --apply does not bounce ==="
: > "$BOUNCE_LOG"
: > "$LAUNCHCTL_LOG"

out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" \
  BOUNCE_LOG="$BOUNCE_LOG" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  PATH="$FAKE_BIN:$PATH" \
  bash "$UPGRADE" --apply 2>&1 || true)"
check "already current on second run" "$out" contains "already current"
check "bounce_daemons.sh not called" "$(cat "$BOUNCE_LOG")" absent "bounce_daemons called"

# === Test 11: upgrade.sh contains no launchctl bounce logic (AC-3) =========

echo ""
echo "=== Test 11: no launchctl in upgrade.sh source ==="
upgrade_src="$(cat "$UPGRADE")"
check "no bootout in upgrade.sh"  "$upgrade_src" absent "bootout"
check "no bootstrap in upgrade.sh" "$upgrade_src" absent "bootstrap"
check "no launchctl in upgrade.sh" "$upgrade_src" absent "launchctl"

# === Test 12: --check does not bounce (AC-5) ===============================

echo ""
echo "=== Test 12: --check does not bounce ==="
advance_remote
: > "$BOUNCE_LOG"
: > "$LAUNCHCTL_LOG"

out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" \
  BOUNCE_LOG="$BOUNCE_LOG" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  PATH="$FAKE_BIN:$PATH" \
  bash "$UPGRADE" --check 2>&1 || true)"
check "--check reports behind" "$out" contains "behind"
check "no bounce on --check" "$(cat "$BOUNCE_LOG")" absent "bounce_daemons called"
check "no launchctl on --check" "$(cat "$LAUNCHCTL_LOG")" absent "launchctl"

# === Test 13: --apply already-current calls bounce on stale daemon ===========

echo ""
echo "=== Test 13: already-current calls bounce on stale daemon ==="
# Reset to origin/main so the tree is "already current"
git -C "$WORK" reset --hard origin/main >/dev/null 2>&1
: > "$BOUNCE_LOG"
: > "$LAUNCHCTL_LOG"
rm -rf "$ILK_DATA_DIR"

out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" \
  BOUNCE_LOG="$BOUNCE_LOG" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  PATH="$FAKE_BIN:$PATH" \
  bash "$UPGRADE" --apply 2>&1 || true)"
check "already-current prints already current" "$out" contains "already current"
check "already-current calls bounce_daemons.sh" "$(cat "$BOUNCE_LOG")" contains "bounce_daemons called"
check "already-current stale output" "$out" contains "bouncing:"

# === Test 14: already-current with fresh daemon ==============================

echo ""
echo "=== Test 14: already-current with fresh daemon ==="
: > "$BOUNCE_LOG"
: > "$LAUNCHCTL_LOG"

# Write state file matching current HEAD so bounce reports fresh
current_head="$(git -C "$WORK" rev-parse HEAD)"
ILK_DATA="$ILK_DATA_DIR"
mkdir -p "$ILK_DATA"
cat > "$ILK_DATA/scheduler.state.json" << EOF
{"toolkit_head": "$current_head"}
EOF

out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" \
  BOUNCE_LOG="$BOUNCE_LOG" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  PATH="$FAKE_BIN:$PATH" \
  bash "$UPGRADE" --apply 2>&1 || true)"
check "already-current fresh still calls bounce" "$(cat "$BOUNCE_LOG")" contains "bounce_daemons called"
check "already-current fresh prints fresh" "$out" contains "fresh:"

# === Test 15: already-current + stale daemon still exits 0 ===================

echo ""
echo "=== Test 15: already-current + stale daemon exits 0 ==="
: > "$BOUNCE_LOG"
: > "$LAUNCHCTL_LOG"
rm -rf "$ILK_DATA_DIR"

exit_code=0
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" \
  BOUNCE_LOG="$BOUNCE_LOG" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  PATH="$FAKE_BIN:$PATH" \
  bash "$UPGRADE" --apply 2>&1)" || exit_code=$?
check_exit "already-current + stale daemon exit 0" 0 "$exit_code"
check "upgrade calls bounce on stale" "$(cat "$BOUNCE_LOG")" contains "bounce_daemons called"

# === Test 16: already-current + unreachable daemon exits 0 ===================

echo ""
echo "=== Test 16: already-current + unreachable daemon exits 0 ==="
: > "$BOUNCE_LOG"
: > "$LAUNCHCTL_LOG"
rm -rf "$ILK_DATA_DIR"

exit_code=0
out="$(HOME="$FAKE_HOME" ILK_DATA_DIR="$ILK_DATA_DIR" \
  BOUNCE_LOG="$BOUNCE_LOG" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  BOUNCE_EXIT_CODE=2 \
  PATH="$FAKE_BIN:$PATH" \
  bash "$UPGRADE" --apply 2>&1)" || exit_code=$?
check_exit "already-current + unreachable daemon exit 0" 0 "$exit_code"
check "unreachable warning on stderr" "$out" contains "could not be reached"
unset BOUNCE_EXIT_CODE

# === Results ================================================================

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
