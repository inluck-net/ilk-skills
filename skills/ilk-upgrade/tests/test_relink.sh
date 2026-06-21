#!/usr/bin/env bash
# Reproduces the "already-current skips relink" bug (ticket 9ade6a178eca6929).
#
# When `behind == 0`, upgrade.sh currently prints "already current" and
# returns 0 BEFORE running the installer link step or the auto-plan reconcile.
# This means a newly-committed command never gets linked until you manually
# re-run install.sh.
#
# AC-1: with behind==0 and a missing link, upgrade.sh --apply runs install.sh --apply
# AC-2: the auto-plan reconcile (install.sh --only-auto-plan --apply) also runs
# AC-3: when already-current AND all links present, --apply is still exit 0
#
# Expected outcome BEFORE the fix: this test FAILS (install-calls.log is empty
# because do_apply short-circuits at the "already current" return 0).

set -euo pipefail

# --- helpers ------------------------------------------------------------------

pass() { echo "  PASS: $*"; }
fail() { echo "  FAIL: $*" >&2; EXIT_CODE=1; }

EXIT_CODE=0

# Capture real paths BEFORE any cd
REAL_UPGRADE_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)/scripts/upgrade.sh"
REAL_UPGRADE_PS1="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)/scripts/upgrade.ps1"
REAL_ILK_DATA_DIR_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop" && pwd -P)/scripts/_ilk_data_dir.sh"

# --- temp workspace -----------------------------------------------------------

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ORIGIN="$WORK/origin.git"
CLONE="$WORK/clone"
FAKE_HOME="$WORK/home"

mkdir -p "$FAKE_HOME/.claude/commands"

# --- scaffold bare origin -----------------------------------------------------

git init --bare "$ORIGIN" >/dev/null 2>&1

# --- scaffold working clone ---------------------------------------------------

# Create a temp seed repo so we can control the branch name
SEED="$WORK/seed"
git init -b main "$SEED" >/dev/null 2>&1
git -C "$SEED" config user.email "test@test.com"
git -C "$SEED" config user.name "Test"
git -C "$SEED" commit --allow-empty -m "init" >/dev/null 2>&1
git -C "$SEED" remote add origin "$ORIGIN"
git -C "$SEED" push origin main >/dev/null 2>&1
# Point the bare repo's HEAD at main (default was master from init --bare)
git -C "$ORIGIN" symbolic-ref HEAD refs/heads/main >/dev/null 2>&1

git clone "$ORIGIN" "$CLONE" >/dev/null 2>&1
cd "$CLONE"
git config user.email "test@test.com"
git config user.name "Test"

# Minimal repo structure: commands/ + skills/ilk-upgrade/scripts/ + skills/ilk-loop/scripts/
mkdir -p commands
mkdir -p skills/ilk-upgrade/scripts
mkdir -p skills/ilk-upgrade/tests
mkdir -p skills/ilk-loop/scripts

# A fake ilk command (what install.sh would link)
echo "# ilk command" > commands/ilk.md

# Stub install.sh — records its args so we can assert later
cat > install.sh << 'INSTALL_STUB'
#!/usr/bin/env bash
# Stub installer — records invocations for test assertions.
LOGFILE="${ILK_TEST_INSTALL_LOG:-/tmp/install-calls.log}"
echo "$*" >> "$LOGFILE"
INSTALL_STUB
chmod +x install.sh

# Copy the real upgrade.sh into the clone
cp "$REAL_UPGRADE_SH" skills/ilk-upgrade/scripts/upgrade.sh

# Copy the real _ilk_data_dir.sh
cp "$REAL_ILK_DATA_DIR_SH" skills/ilk-loop/scripts/_ilk_data_dir.sh

# Commit + push so the clone is at behind==0
git add -A
git commit -m "initial scaffold" >/dev/null 2>&1
git push origin main >/dev/null 2>&1

# --- verify scaffold ----------------------------------------------------------

CLONE_REV="$(git rev-parse HEAD)"
ORIGIN_MAIN="$(git rev-parse origin/main 2>/dev/null)"
if [[ "$CLONE_REV" != "$ORIGIN_MAIN" ]]; then
  echo "FATAL: scaffold push didn't land — clone is not at origin HEAD" >&2
  exit 2
fi

# --- test: behind==0, missing link, --apply should reconcile ------------------

INSTALL_LOG="$WORK/install-calls.log"
rm -f "$INSTALL_LOG"

export ILK_TEST_INSTALL_LOG="$INSTALL_LOG"
export HOME="$FAKE_HOME"
export ILK_DATA_HOME="$WORK/ilk-data"

# Verify the link is missing
if [[ -L "$FAKE_HOME/.claude/commands/ilk.md" ]]; then
  echo "FATAL: ilk.md link exists in fake home before test — scaffold bug" >&2
  exit 2
fi

echo ""
echo "=== Test: already-current with missing link ==="

# Run upgrade.sh --apply from the clone
UPGRADE_SCRIPT="$CLONE/skills/ilk-upgrade/scripts/upgrade.sh"
set +e
OUTPUT="$(bash "$UPGRADE_SCRIPT" --apply 2>&1)"
RC=$?
set -e

echo "  exit code: $RC"
echo "  output: $(echo "$OUTPUT" | tr '\n' ' ')"

# AC-1: install.sh --apply should have been called
if [[ -f "$INSTALL_LOG" ]] && grep -q "\-\-apply" "$INSTALL_LOG"; then
  pass "AC-1: install.sh --apply was called on already-current path"
else
  fail "AC-1: install.sh --apply was NOT called (missing link was not reconciled)"
  echo "  install-calls.log contents: $(cat "$INSTALL_LOG" 2>/dev/null || echo '<missing>')"
fi

# AC-2: install.sh --only-auto-plan --apply should have been called
if [[ -f "$INSTALL_LOG" ]] && grep -q "\-\-only-auto-plan" "$INSTALL_LOG"; then
  pass "AC-2: auto-plan reconcile ran on already-current path"
else
  fail "AC-2: auto-plan reconcile did NOT run"
fi

# AC-3: exit code should be 0
if [[ $RC -eq 0 ]]; then
  pass "AC-3: exit 0 on already-current with missing link"
else
  fail "AC-3: exit $RC (expected 0)"
fi

echo ""
echo "=== Test: already-current with all links present (idempotent no-op) ==="

# Now create the link so everything is "present"
mkdir -p "$FAKE_HOME/.claude/commands"
ln -sfn "$CLONE/commands/ilk.md" "$FAKE_HOME/.claude/commands/ilk.md"

rm -f "$INSTALL_LOG"

set +e
OUTPUT2="$(bash "$UPGRADE_SCRIPT" --apply 2>&1)"
RC2=$?
set -e

# AC-3 continued: exit 0 even when all links present
if [[ $RC2 -eq 0 ]]; then
  pass "AC-3: exit 0 when already-current and links present"
else
  fail "AC-3: exit $RC2 (expected 0)"
fi

# --- AC-4: upgrade.ps1 already-current branch reaches reconcile (static) ----

echo ""
echo "=== Test: upgrade.ps1 already-current branch reaches reconcile ==="

# The already-current block in upgrade.ps1 must be followed by an
# Invoke-ReconcileLinks call, not a bare `return`.  We check that the
# line immediately after "already current" is Invoke-ReconcileLinks.
PS1_AFTER_CURRENT="$(sed -n '/already current/,/^}/p' "$REAL_UPGRADE_PS1" 2>/dev/null | head -5)"

if echo "$PS1_AFTER_CURRENT" | grep -q "Invoke-ReconcileLinks"; then
  pass "AC-4: upgrade.ps1 already-current branch calls Invoke-ReconcileLinks"
else
  fail "AC-4: upgrade.ps1 already-current branch does NOT call Invoke-ReconcileLinks"
  echo "  context: $(echo "$PS1_AFTER_CURRENT" | tr '\n' ' ')"
fi

# --- summary ------------------------------------------------------------------

echo ""
if [[ $EXIT_CODE -eq 0 ]]; then
  echo "All checks passed."
else
  echo "Some checks FAILED (see above)."
fi
exit $EXIT_CODE
