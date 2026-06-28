#!/usr/bin/env bash
set -euo pipefail

# Verify bootstrap.ps1 -Home parity with bootstrap.sh --home.
#
# Runs bootstrap.ps1 via powershell.exe into a temp home under scratch/ and
# asserts settings.json has the expected MiniMax-like env values.
#
# Hermetic: uses a throwaway temp dir; never touches the real worker home.
# Requires: powershell.exe on PATH (Git Bash on Windows).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
BOOT_PS1="${REPO_ROOT}/tools/claude-worker/bootstrap.ps1"

PASS=0
FAIL=0

check() {
  local desc="$1" hay="$2" mode="$3" needle="$4"
  local found=0
  case "$hay" in *"$needle"*) found=1 ;; esac
  if { [[ "$mode" == contains && $found -eq 1 ]] || \
       [[ "$mode" == absent  && $found -eq 0 ]]; }; then
    PASS=$((PASS + 1)); echo "  PASS: ${desc}"
  else
    FAIL=$((FAIL + 1)); echo "  FAIL: ${desc} (mode=${mode}, needle=${needle})"
  fi
}
ok() {
  if [[ "$2" -eq 0 ]]; then PASS=$((PASS + 1)); echo "  PASS: $1"
  else FAIL=$((FAIL + 1)); echo "  FAIL: $1"; fi
}

# Skip if powershell.exe is not available.
if ! command -v powershell.exe >/dev/null 2>&1; then
  echo "SKIP: powershell.exe not found on PATH"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

TOKEN="FAKE-token-for-parity-test"
DRAW_HOME="$TMP/draw-home-ps"

echo "=== bootstrap.ps1 -Home with MiniMax-like values ==="
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File "$BOOT_PS1" -Apply \
  -Home "$DRAW_HOME" \
  -BaseUrl "https://api.minimaxi.com/anthropic" \
  -AuthToken "$TOKEN" \
  -Model "MiniMax-M3" >/dev/null 2>&1
ok "ps1: settings.json created" "$([[ -f "$DRAW_HOME/settings.json" ]] && echo 0 || echo 1)"

if [[ -f "$DRAW_HOME/settings.json" ]]; then
  # Convert path for Python (Git Bash /tmp -> Windows path).
  if command -v cygpath >/dev/null 2>&1; then
    DRAW_PY="$(cygpath -w "$DRAW_HOME")"
  else
    DRAW_PY="$DRAW_HOME"
  fi

  # Resolve Python.
  if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" 2>/dev/null; then
    PY=python3
  elif command -v python >/dev/null 2>&1 && python -c "import sys" 2>/dev/null; then
    PY=python
  else
    echo "SKIP: no working python found"; exit 0
  fi

  draw_settings="$(cat "$DRAW_HOME/settings.json")"
  check "ps1: ANTHROPIC_MODEL=MiniMax-M3"         "$draw_settings" contains "MiniMax-M3"
  check "ps1: ANTHROPIC_BASE_URL=minimaxi"         "$draw_settings" contains "https://api.minimaxi.com/anthropic"
  check "ps1: has token"                            "$draw_settings" contains "$TOKEN"
  # PowerShell 5.1 writes UTF-8 with BOM; use utf-8-sig to strip it.
  $PY -c "import json,sys; json.load(open(r'$DRAW_PY/settings.json', encoding='utf-8-sig'))"; ok "ps1: valid JSON" $?
else
  echo "  SKIP: settings.json not created (cannot check contents)"
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then exit 1; fi
