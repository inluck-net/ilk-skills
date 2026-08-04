#!/usr/bin/env bash
set -euo pipefail

# Verify tools/claude-worker/claude-worker.sh wrapper behavior:
#   * parses (bash -n) and --help exits 0 with no provider env;
#   * fails closed (exit 3) against an intentionally empty worker home, naming
#     every missing prerequisite;
#   * masks the provider token in all output (never prints the raw value);
#   * --preflight-only passes (exit 0) against a complete temp worker home
#     without launching claude;
#   * exports CLAUDE_CONFIG_DIR + ILK_SKILL_HOME in the launch path.
#
# Also statically checks claude-worker.ps1 for parity (no pwsh required on CI).
#
# Hermetic: HOME is redirected to a throwaway temp dir so nothing reads or
# mutates the operator's real ~/.claude / CCSwitch state.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
WRAP_SH="${REPO_ROOT}/tools/claude-worker/claude-worker.sh"
WRAP_PS1="${REPO_ROOT}/tools/claude-worker/claude-worker.ps1"

PASS=0
FAIL=0

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
bash -n "$WRAP_SH"; ok "claude-worker.sh parses (bash -n)" $?
HOME="$FAKE_HOME" bash "$WRAP_SH" --help >/dev/null; ok "--help exits 0" $?

echo ""
echo "=== fail-closed: empty worker home ==="
set +e
fc="$(HOME="$FAKE_HOME" bash "$WRAP_SH" --home "$TMP/empty" --preflight-only 2>&1)"
fc_rc=$?
set -e
ok "exit code 3 on empty worker home" "$([[ $fc_rc -eq 3 ]] && echo 0 || echo 1)"
check "names the missing worker home"  "$fc" contains "worker home does not exist"
check "names the missing settings"     "$fc" contains "settings.json missing"
check "names the missing ilk-runner"   "$fc" contains "ilk-runner skill not found"

echo ""
echo "=== complete worker home: preflight passes, token masked ==="
WK="$TMP/wk/.claude-worker"
mkdir -p "$WK/skills/ilk-runner"
cat > "$WK/settings.json" <<EOF
{ "env": { "ANTHROPIC_BASE_URL": "https://p.example/anthropic", "ANTHROPIC_AUTH_TOKEN": "$TOKEN", "ANTHROPIC_MODEL": "worker-m1" } }
EOF
set +e
good="$(HOME="$TMP/wk" bash "$WRAP_SH" --home "$WK" --preflight-only 2>&1)"
good_rc=$?
set -e
ok "exit code 0 on complete worker home" "$([[ $good_rc -eq 0 ]] && echo 0 || echo 1)"
check "preflight-only never prints raw token" "$good" absent "$TOKEN"
check "shows masked marker"                   "$good" contains "***set"
check "reports base url"                      "$good" contains "https://p.example/anthropic"
check "reports model"                         "$good" contains "worker-m1"
check "says it is not launching"              "$good" contains "not launching claude"

echo ""
echo "=== launch path wiring (static) ==="
sh="$(cat "$WRAP_SH")"
check "exports CLAUDE_CONFIG_DIR"  "$sh" contains "export CLAUDE_CONFIG_DIR="
check "exports ILK_SKILL_HOME"     "$sh" contains "export ILK_SKILL_HOME="
# The wrapper deliberately launches claude as a *child* rather than exec'ing
# it, so the EXIT trap fires and the worker sentinel is removed on exit
# (claude-worker.sh: "Launch as a child (not exec) so the EXIT trap fires").
# Assert both halves so reintroducing `exec` fails here.
check "launches the resolved claude bin" "$sh" contains '"$resolved_claude_bin"'
check "does not exec (EXIT trap must fire)" "$sh" absent "exec claude"

echo ""
echo "=== claude-worker.ps1 parity (static) ==="
ps1="$(cat "$WRAP_PS1")"
check "ps1 sets CLAUDE_CONFIG_DIR"  "$ps1" contains '$env:CLAUDE_CONFIG_DIR'
check "ps1 sets ILK_SKILL_HOME"     "$ps1" contains '$env:ILK_SKILL_HOME'
check "ps1 reads ANTHROPIC_AUTH_TOKEN" "$ps1" contains "ANTHROPIC_AUTH_TOKEN"
check "ps1 has -PreflightOnly switch"  "$ps1" contains '[switch]$PreflightOnly'
check "ps1 fails closed (exit 3)"      "$ps1" contains "exit 3"
check "ps1 masks the secret"           "$ps1" contains "Format-Secret"

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then exit 1; fi
