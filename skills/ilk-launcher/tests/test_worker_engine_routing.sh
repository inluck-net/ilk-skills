#!/usr/bin/env bash
set -euo pipefail

# Routing test matrix for the ilk-launcher engine routing.
# All assertions use -DryRun only — no provider calls, no real ~/.claude mutation.

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LAUNCHER="${REPO_ROOT}/skills/ilk-launcher/scripts/launch.sh"

# On Windows (Git Bash), python3 may be a broken Microsoft Store stub;
# shim it to real python if it can't actually run.
SHIM_DIR="$(mktemp -d)"
if ! python3 --version &>/dev/null && command -v python &>/dev/null; then
  printf '#!/usr/bin/env bash\nexec python "$@"\n' > "$SHIM_DIR/python3"
  chmod +x "$SHIM_DIR/python3"
  export PATH="$SHIM_DIR:$PATH"
fi

# Create a minimal temp project so ilk_paths.py can resolve it.
TMPDIR_PROJ="$(mktemp -d)"
TMPDIR_OUT="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_PROJ" "$SHIM_DIR" "$TMPDIR_OUT"' EXIT
mkdir -p "${TMPDIR_PROJ}/docs/plans"
echo "---" > "${TMPDIR_PROJ}/docs/plans/MASTER-test.md"
git init -q "$TMPDIR_PROJ"

PASS_COUNT=0
FAIL_COUNT=0

assert_grep() {
  local name="$1" file="$2"
  shift 2
  if grep "$@" "$file" >/dev/null 2>&1; then
    echo "  PASS: $name"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "  FAIL: $name" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

assert_true() {
  local name="$1"
  shift
  if "$@"; then
    echo "  PASS: $name"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "  FAIL: $name" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

echo "=== test_worker_engine_routing.sh ==="

# --- AC-3: default engine → planner default, no .claude-worker ---
echo "--- AC-3: default engine dry-run ---"

# Unset ILK_DEFAULT_ENGINE so the test sees the true hardcoded default (claude),
# not a machine-wide override.
unset ILK_DEFAULT_ENGINE
bash "$LAUNCHER" --project-path "$TMPDIR_PROJ" --dry-run 2>&1 | tr -d '\r' > "$TMPDIR_OUT/default.txt" || true

assert_grep "default: ClaudeConfigDir line present" \
  "$TMPDIR_OUT/default.txt" -q 'ClaudeConfigDir:.*default.*\.claude'

# Ensure ClaudeConfigDir line does NOT contain .claude-worker
configdir_default=$(grep 'ClaudeConfigDir:' "$TMPDIR_OUT/default.txt" || true)
assert_true "default: no .claude-worker in ClaudeConfigDir" \
  bash -c 'case "'"${configdir_default}"'" in *.claude-worker*) exit 1 ;; *) exit 0 ;; esac'

# --- AC-2: claude-worker engine → worker home routing ---
echo "--- AC-2: claude-worker engine dry-run ---"

bash "$LAUNCHER" --project-path "$TMPDIR_PROJ" --engine claude-worker --dry-run 2>&1 | tr -d '\r' > "$TMPDIR_OUT/worker.txt" || true

assert_grep "claude-worker: ClaudeConfigDir contains .claude-worker" \
  "$TMPDIR_OUT/worker.txt" -q 'ClaudeConfigDir:.*\.claude-worker'

assert_grep "claude-worker: IlkSkillHome contains .claude-worker/skills" \
  "$TMPDIR_OUT/worker.txt" -q 'IlkSkillHome:.*\.claude-worker/skills'

# --- AC-1: invalid engine → non-zero exit + error message ---
echo "--- AC-1: invalid engine ---"

set +e
bash "$LAUNCHER" --project-path "$TMPDIR_PROJ" --engine bogus --dry-run 2>&1 | tr -d '\r' > "$TMPDIR_OUT/invalid.txt"
exit_invalid=$?
set -e

assert_true "invalid engine: exits non-zero" test "$exit_invalid" -ne 0

assert_grep "invalid engine: error mentions valid engines" \
  "$TMPDIR_OUT/invalid.txt" -qi 'valid.*engine'

# --- AC-4: ILK_DEFAULT_ENGINE machine-wide opt-in default ---
echo "--- AC-4: ILK_DEFAULT_ENGINE default ---"

ILK_DEFAULT_ENGINE=claude-worker bash "$LAUNCHER" --project-path "$TMPDIR_PROJ" --dry-run 2>&1 | tr -d '\r' > "$TMPDIR_OUT/envdef.txt" || true

assert_grep "env default: ClaudeConfigDir routes to .claude-worker" \
  "$TMPDIR_OUT/envdef.txt" -q 'ClaudeConfigDir:.*\.claude-worker'

# --- AC-5: explicit --engine overrides ILK_DEFAULT_ENGINE ---
echo "--- AC-5: CLI overrides env default ---"

ILK_DEFAULT_ENGINE=claude-worker bash "$LAUNCHER" --project-path "$TMPDIR_PROJ" --engine claude --dry-run 2>&1 | tr -d '\r' > "$TMPDIR_OUT/override.txt" || true

configdir_override=$(grep 'ClaudeConfigDir:' "$TMPDIR_OUT/override.txt" || true)
assert_true "CLI override: no .claude-worker in ClaudeConfigDir" \
  bash -c 'case "'"${configdir_override}"'" in *.claude-worker*) exit 1 ;; *) exit 0 ;; esac'

echo ""
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed"
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
echo "ALL PASS"
