#!/usr/bin/env bash
set -euo pipefail

# Verify tools/claude-worker/bootstrap.sh safety behavior:
#   * masks the provider token in all output (never prints the raw value);
#   * fails closed (exit 3, writes nothing) when provider env is incomplete;
#   * a dry-run is non-mutating even with --link-skills;
#   * --apply writes a valid settings.json (mode 600) + minimal .claude.json
#     without touching ~/.claude.
#
# Also statically checks bootstrap.ps1 for parity (no pwsh required on CI).
#
# Hermetic: HOME is redirected to a throwaway temp dir so nothing reads or
# mutates the operator's real ~/.claude / CCSwitch state.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
BOOT_SH="${REPO_ROOT}/tools/claude-worker/bootstrap.sh"
BOOT_PS1="${REPO_ROOT}/tools/claude-worker/bootstrap.ps1"

PASS=0
FAIL=0

# Resolve Python: prefer python3 (POSIX), fall back to python (Windows).
# On Windows, python3 may be a Microsoft Store stub — verify it actually works.
if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" 2>/dev/null; then
  PY=python3
elif command -v python >/dev/null 2>&1 && python -c "import sys" 2>/dev/null; then
  PY=python
else
  echo "SKIP: no working python found on PATH" >&2; exit 0
fi

check() {
  # check "<description>" "<haystack>" contains|absent "<needle>"
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
ok() { # ok "<desc>" <0-or-1-condition-exit>
  if [[ "$2" -eq 0 ]]; then PASS=$((PASS + 1)); echo "  PASS: $1"
  else FAIL=$((FAIL + 1)); echo "  FAIL: $1"; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME"
TOKEN="SECRET-dummy-0123456789"

echo "=== syntax + help ==="
bash -n "$BOOT_SH"; ok "bootstrap.sh parses (bash -n)" $?
HOME="$FAKE_HOME" bash "$BOOT_SH" --help >/dev/null; ok "--help exits 0" $?

echo ""
echo "=== dry-run masks the token ==="
dry="$(HOME="$FAKE_HOME" bash "$BOOT_SH" --home "$TMP/wk" \
  --base-url https://p.example/anthropic --auth-token "$TOKEN" --model m1 2>&1)"
check "dry-run never prints raw token" "$dry" absent "$TOKEN"
check "dry-run shows masked marker"    "$dry" contains "***set"

echo ""
echo "=== fail-closed: incomplete provider env ==="
set +e
HOME="$FAKE_HOME" bash "$BOOT_SH" --apply --home "$TMP/wk-fc" \
  --base-url https://p.example/anthropic --model m1 >/dev/null 2>&1
fc_rc=$?
set -e
ok "exit code 3 on missing token" "$([[ $fc_rc -eq 3 ]] && echo 0 || echo 1)"
ok "wrote nothing on fail-closed"  "$([[ ! -e "$TMP/wk-fc" ]] && echo 0 || echo 1)"

echo ""
echo "=== dry-run --link-skills is non-mutating ==="
HOME="$FAKE_HOME" bash "$BOOT_SH" --home "$TMP/wk-dry" \
  --base-url https://p.example/anthropic --auth-token "$TOKEN" --model m1 \
  --link-skills >/dev/null 2>&1
ok "dry-run --link-skills created no worker home" \
  "$([[ ! -e "$TMP/wk-dry" ]] && echo 0 || echo 1)"

echo ""
echo "=== --apply writes a safe worker home ==="
WK="$TMP/wk-apply"
HOME="$FAKE_HOME" bash "$BOOT_SH" --apply --home "$WK" \
  --base-url https://p.example/anthropic --auth-token "$TOKEN" --model worker-m1 >/dev/null 2>&1
ok "settings.json created"  "$([[ -f "$WK/settings.json" ]] && echo 0 || echo 1)"
ok ".claude.json created"   "$([[ -f "$WK/.claude.json" ]] && echo 0 || echo 1)"
ok "did not create real ~/.claude" "$([[ ! -e "$FAKE_HOME/.claude" ]] && echo 0 || echo 1)"
perms="$(ls -l "$WK/settings.json" | awk '{print $1}')"
check "settings.json is owner-only (rw-------)" "$perms" contains "rw-------"
settings="$(cat "$WK/settings.json")"
check "settings has base url"  "$settings" contains "https://p.example/anthropic"
check "settings has token"     "$settings" contains "$TOKEN"
check "settings has model"     "$settings" contains "worker-m1"
# Convert POSIX tmp path for Python on Windows (Git Bash /tmp -> real path).
if command -v cygpath >/dev/null 2>&1; then
  WK_PY="$(cygpath -w "$WK")"
else
  WK_PY="$WK"
fi
$PY -c "import json,sys; json.load(open(r'$WK_PY/settings.json'))"; ok "settings.json is valid JSON" $?
claude_json="$(cat "$WK/.claude.json")"
check ".claude.json has empty mcpServers" "$claude_json" contains '"mcpServers": {}'

echo ""
echo "=== --home with MiniMax-like values (drawing worker) ==="
DRAW_HOME="$TMP/draw-home-test"
HOME="$FAKE_HOME" bash "$BOOT_SH" --apply --home "$DRAW_HOME" \
  --base-url https://api.minimaxi.com/anthropic --auth-token "$TOKEN" --model MiniMax-M3 >/dev/null 2>&1
ok "draw-home: settings.json created" "$([[ -f "$DRAW_HOME/settings.json" ]] && echo 0 || echo 1)"
draw_settings="$(cat "$DRAW_HOME/settings.json")"
check "draw-home: ANTHROPIC_MODEL=MiniMax-M3"      "$draw_settings" contains "MiniMax-M3"
check "draw-home: ANTHROPIC_BASE_URL=minimaxi"      "$draw_settings" contains "https://api.minimaxi.com/anthropic"
check "draw-home: has token"                         "$draw_settings" contains "$TOKEN"
if command -v cygpath >/dev/null 2>&1; then
  DRAW_PY="$(cygpath -w "$DRAW_HOME")"
else
  DRAW_PY="$DRAW_HOME"
fi
$PY -c "import json,sys; json.load(open(r'$DRAW_PY/settings.json'))"; ok "draw-home: valid JSON" $?

echo ""
echo "=== bootstrap.ps1 parity (static) ==="
ps1="$(cat "$BOOT_PS1")"
check "ps1 reads ANTHROPIC_BASE_URL"   "$ps1" contains "ANTHROPIC_BASE_URL"
check "ps1 reads ANTHROPIC_AUTH_TOKEN" "$ps1" contains "ANTHROPIC_AUTH_TOKEN"
check "ps1 reads ANTHROPIC_MODEL"      "$ps1" contains "ANTHROPIC_MODEL"
check "ps1 has -LinkSkills param"      "$ps1" contains '[switch]$LinkSkills'
check "ps1 fails closed (exit 3)"      "$ps1" contains "exit 3"
check "ps1 masks the secret"           "$ps1" contains "Format-Secret"
check "ps1 invokes installer only on -Apply" "$ps1" contains "running installer (-Apply)"

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then exit 1; fi
