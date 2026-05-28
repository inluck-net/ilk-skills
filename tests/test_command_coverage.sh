#!/usr/bin/env bash
set -euo pipefail

# Verify all expected command files exist in commands/.
# Fails if any expected command is missing — catches partial installs.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMANDS_DIR="${SCRIPT_DIR}/../commands"

EXPECTED=(
  ilk-stop.md
  ilk-run.md
  ilk-status.md
)

PASS=0
FAIL=0

for f in "${EXPECTED[@]}"; do
  if [[ -f "${COMMANDS_DIR}/${f}" ]]; then
    PASS=$((PASS + 1))
    echo "  PASS: ${f} exists"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: ${f} missing"
  fi
done

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
