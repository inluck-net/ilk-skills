#!/usr/bin/env bash
set -euo pipefail

# Baseline test: the default engine's dry-run shows planner-home routing.
# Uses -DryRun only — no provider calls, no real ~/.claude mutation.

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LAUNCHER="${REPO_ROOT}/skills/ilk-launcher/scripts/launch.sh"

# On Windows (Git Bash), python3 may be a broken Microsoft Store stub;
# shim it to real python if it can't actually run.
SHIM_DIR="$(mktemp -d)"
trap 'rm -rf "$SHIM_DIR"' EXIT
if ! python3 --version &>/dev/null && command -v python &>/dev/null; then
  printf '#!/usr/bin/env bash\nexec python "$@"\n' > "$SHIM_DIR/python3"
  chmod +x "$SHIM_DIR/python3"
  export PATH="$SHIM_DIR:$PATH"
fi

# Create a minimal temp project so ilk_paths.py can resolve it.
TMPDIR_PROJ="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_PROJ" "$SHIM_DIR"' EXIT
mkdir -p "${TMPDIR_PROJ}/docs/plans"
echo "---" > "${TMPDIR_PROJ}/docs/plans/MASTER-test.md"
git init -q "$TMPDIR_PROJ"

# Run the bash launcher in dry-run with the default engine.
output=$(bash "$LAUNCHER" --project-path "$TMPDIR_PROJ" --dry-run 2>&1)

# Assert: output contains a ClaudeConfigDir line referencing the planner default.
if ! echo "$output" | grep -q "ClaudeConfigDir:.*default.*\.claude"; then
  echo "FAIL: dry-run output missing ClaudeConfigDir planner-default line" >&2
  echo "$output" >&2
  exit 1
fi

# Assert: the ClaudeConfigDir line does NOT reference .claude-worker.
configdir_line=$(echo "$output" | grep "ClaudeConfigDir:")
if echo "$configdir_line" | grep -q "\.claude-worker"; then
  echo "FAIL: default engine should NOT route to .claude-worker" >&2
  echo "$configdir_line" >&2
  exit 1
fi

echo "PASS"
