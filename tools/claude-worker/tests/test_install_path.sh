#!/usr/bin/env bash
# Self-contained test for the bash installer's --only-path mode.
# Runs in throwaway temp sandboxes; never touches the real home.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
INSTALL="$REPO_ROOT/install.sh"
SOURCE="$REPO_ROOT/tools/claude-worker/claude-worker.sh"

pass=0
fail=0

ok() { pass=$((pass + 1)); echo "  OK: $1"; }
die() { fail=$((fail + 1)); echo "  FAIL: $1"; }

echo "=== test_install_path.sh ==="
echo "repo: $REPO_ROOT"

# --- Test 1: --only-path --apply creates the entry with correct content ---
echo
echo "Test 1: apply creates entry with correct content"
t=$(mktemp -d)
HOME="$t" bash "$INSTALL" --only-path --apply >/dev/null 2>&1
entry="$t/.local/bin/claude-worker"
if [[ -f "$entry" ]] && cmp -s "$SOURCE" "$entry"; then
  ok "entry exists and content matches source"
else
  die "entry missing or content mismatch"
fi
rm -rf "$t"

# --- Test 2: idempotent re-run ---
echo
echo "Test 2: idempotent re-run"
t=$(mktemp -d)
HOME="$t" bash "$INSTALL" --only-path --apply >/dev/null 2>&1
rc1=$?
HOME="$t" bash "$INSTALL" --only-path --apply >/dev/null 2>&1
rc2=$?
if [[ $rc1 -eq 0 && $rc2 -eq 0 ]]; then
  ok "both runs exit 0"
else
  die "exit codes: first=$rc1 second=$rc2"
fi
entry="$t/.local/bin/claude-worker"
if [[ -f "$entry" ]] && cmp -s "$SOURCE" "$entry"; then
  ok "entry still correct after re-run"
else
  die "entry corrupted after re-run"
fi
rm -rf "$t"

# --- Test 3: dry-run (no --apply) writes nothing ---
echo
echo "Test 3: dry-run writes nothing"
t=$(mktemp -d)
HOME="$t" bash "$INSTALL" --only-path >/dev/null 2>&1
if [[ ! -e "$t/.local/bin/claude-worker" ]]; then
  ok "no entry created in dry-run"
else
  die "entry was created during dry-run"
fi
rm -rf "$t"

# --- Test 4: --path-bin-dir override ---
echo
echo "Test 4: --path-bin-dir override"
t=$(mktemp -d)
bindir="$t/custom-bin"
HOME="$t" bash "$INSTALL" --only-path --apply --path-bin-dir "$bindir" >/dev/null 2>&1
entry="$bindir/claude-worker"
if [[ -f "$entry" ]] && cmp -s "$SOURCE" "$entry"; then
  ok "custom bin dir used"
else
  die "custom bin dir entry missing or wrong"
fi
rm -rf "$t"

# --- Summary ---
echo
echo "Results: pass=$pass fail=$fail"
if [[ $fail -gt 0 ]]; then
  echo "FAILED"
  exit 1
fi
echo "PASS"
exit 0
