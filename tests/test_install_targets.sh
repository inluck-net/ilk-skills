#!/usr/bin/env bash
set -euo pipefail

# Verify install.sh builds the expected target list for the default case
# and for a custom Claude home (--claude-home / --only-claude).
#
# Hermetic: HOME is redirected to a throwaway temp dir so the dry-run
# never reads or mutates the operator's real ~/.cursor, ~/.claude, or
# ~/.codex. install.sh resolves its repo root from BASH_SOURCE, not HOME,
# so overriding HOME only moves the *targets*.
#
# Also statically checks that install.ps1 wires a custom Claude home into
# its Claude Code target (no pwsh required on macOS/Linux CI).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
INSTALL_SH="${REPO_ROOT}/install.sh"
INSTALL_PS1="${REPO_ROOT}/install.ps1"

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

check_skill_planned() {
  # check_skill_planned "<haystack>" "<home>" "<skill-name>"
  # One PASS/FAIL covering the .claude + .codex + .cursor skills links for a
  # single skill folder, so a per-family list stays one line per name.
  local hay="$1" home="$2" name="$3" agent missing=""
  for agent in .claude .codex .cursor; do
    case "$hay" in
      *"${home}/${agent}/skills/${name}"*) ;;
      *) missing="${missing} ${agent}" ;;
    esac
  done
  if [[ -z "$missing" ]]; then
    PASS=$((PASS + 1))
    echo "  PASS: skills/${name} planned for .claude .codex .cursor"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: skills/${name} missing for:${missing}"
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAKE_HOME="$TMP/home"
WORKER="$TMP/worker"

echo "=== default dry-run targets Cursor + Claude Code + Codex ==="
default_out="$(HOME="$FAKE_HOME" bash "$INSTALL_SH" --dry-run 2>&1)"
check "default includes Cursor home"      "$default_out" contains "$FAKE_HOME/.cursor/skills"
check "default includes default Claude"   "$default_out" contains "$FAKE_HOME/.claude/skills"
check "default includes Codex home"       "$default_out" contains "$FAKE_HOME/.codex/skills"
check "default has no custom-home banner"  "$default_out" absent  "(custom)"

echo ""
echo "=== custom Claude home, --only-claude, targets only the worker home ==="
custom_out="$(HOME="$FAKE_HOME" bash "$INSTALL_SH" --dry-run --claude-home "$WORKER" --only-claude 2>&1)"
check "custom targets worker skills"      "$custom_out" contains "$WORKER/skills/"
check "custom targets worker commands"    "$custom_out" contains "$WORKER/commands/"
check "custom names the home (banner)"    "$custom_out" contains "$WORKER (custom)"
check "custom skips Cursor"               "$custom_out" absent  "/.cursor/"
check "custom skips Codex"                "$custom_out" absent  "/.codex/"
check "custom skips default Claude home"  "$custom_out" absent  "$FAKE_HOME/.claude/"

echo ""
echo "=== install.ps1 wires a custom Claude home (static) ==="
ps1="$(cat "$INSTALL_PS1")"
check "ps1 declares ClaudeHome param"     "$ps1" contains '[string]$ClaudeHome'
check "ps1 documents -ClaudeHome usage"   "$ps1" contains '-ClaudeHome'
check "ps1 targets custom skills dir"     "$ps1" contains 'Join-Path $ClaudeHome "skills"'
check "ps1 targets custom commands dir"   "$ps1" contains 'Join-Path $ClaudeHome "commands"'

echo ""
echo "=== install.ps1's skill filter admits both families (static) ==="
# install.ps1 cannot run here (no pwsh on macOS), so the filter is checked on
# the discovery line itself — not on the whole file, where a comment saying
# 'ilk2-*' would pass for the wrong reason.  Both spellings are asserted:
# widening must ADD ilk2-*, not swap it in for ilk-*.
ps1_skill_filter="$(grep -F 'Get-ChildItem -Directory $SkillsSrc' "$INSTALL_PS1" || true)"
check "ps1 skill filter is the dir scan"       "$ps1_skill_filter" contains 'Get-ChildItem -Directory $SkillsSrc'
check "ps1 skill filter still admits ilk-*"    "$ps1_skill_filter" contains 'ilk-*'
check "ps1 skill filter admits ilk2-*"         "$ps1_skill_filter" contains 'ilk2-*'

echo ""
echo "=== ilk2-* skill folders are planned for every agent ==="
# AC-4's fixture: a fresh temp HOME that already holds the three agent homes.
ILK2_HOME="$TMP/ilk2-home"
mkdir -p "$ILK2_HOME/.claude" "$ILK2_HOME/.codex" "$ILK2_HOME/.cursor"
ilk2_out="$(HOME="$ILK2_HOME" bash "$INSTALL_SH" --dry-run 2>&1)"

for name in ilk2-plan ilk2-feedback; do
  check_skill_planned "$ilk2_out" "$ILK2_HOME" "$name"
done

# Every skill folder that matched the pre-widening filter must still be
# planned.  The find below is that set — if it ever returns nothing the loop
# is vacuous, which reads exactly like a pass, so report the count first.
ILK_ONLY=()
while IFS= read -r line; do
  if [[ -n "$line" ]]; then
    ILK_ONLY+=("$line")
  fi
done < <(find "${REPO_ROOT}/skills" -maxdepth 1 -mindepth 1 -type d -name 'ilk-*' -exec basename {} \; | sort)
if [[ ${#ILK_ONLY[@]} -eq 0 ]]; then
  FAIL=$((FAIL + 1))
  echo "  FAIL: 0 skills/ilk-* folders found; the still-planned loop would be vacuous"
else
  PASS=$((PASS + 1))
  echo "  PASS: ${#ILK_ONLY[@]} skills/ilk-* folders still to plan"
  for name in "${ILK_ONLY[@]}"; do
    check_skill_planned "$ilk2_out" "$ILK2_HOME" "$name"
  done
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
