#!/usr/bin/env bash
set -euo pipefail

# Test the launcher's toolkit-staleness notice (print_toolkit_staleness_notice).
#
# Hermetic: ILK_SKILL_HOME is redirected to a fixture directory containing a
# mock upgrade.sh that returns controlled output.  No network, no mutation of
# the real skill root.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/.."
LAUNCH_SH="${REPO_ROOT}/skills/ilk-launcher/scripts/launch.sh"

PASS=0
FAIL=0

check() {
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
    echo "    output was: $(echo "$hay" | head -5)"
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FIXTURE_SKILLS="$TMP/skills"
FIXTURE_UPGRADE_DIR="$FIXTURE_SKILLS/ilk-upgrade/scripts"
mkdir -p "$FIXTURE_UPGRADE_DIR"

# Backup and restore the real upgrade.sh if it exists
REAL_UPGRADE_SH="$FIXTURE_SKILLS/../_real_upgrade_sh_backup"
UPGRADE_SH="$FIXTURE_UPGRADE_DIR/upgrade.sh"

# --- helper: run the notice function with a given mock upgrade.sh output ------

run_notice() {
  local mock_output="$1"
  local mock_exit="${2:-0}"

  cat > "$UPGRADE_SH" <<MOCK
#!/usr/bin/env bash
if [[ "${mock_exit}" -ne 0 ]]; then
  echo "$mock_output" >&2
  exit ${mock_exit}
fi
echo "${mock_output}"
MOCK
  chmod +x "$UPGRADE_SH"

  ILK_SKILL_HOME="$FIXTURE_SKILLS" ILK_SKIP_MAIN=1 \
    bash -c 'source "'"$LAUNCH_SH"'"; print_toolkit_staleness_notice' 2>&1 || true
}

# --- Test scenarios -----------------------------------------------------------

echo "=== behind → notice appears ==="
out=$(run_notice "behind by 3 commit(s) — run with --apply")
check "behind: prints notice"            "$out" contains "[ilk-upgrade] toolkit behind by 3 commit(s)"
check "behind: mentions /ilk-upgrade"    "$out" contains "run /ilk-upgrade"

echo ""
echo "=== behind by 1 (singular) → notice appears ==="
out=$(run_notice "behind by 1 commit(s) — run with --apply")
check "behind-1: prints notice with count" "$out" contains "[ilk-upgrade] toolkit behind by 1 commit(s)"

echo ""
echo "=== up to date → silent ==="
out=$(run_notice "up to date")
check "current: no notice"              "$out" absent  "[ilk-upgrade]"
check "current: no error"               "$out" absent  "error"

echo ""
echo "=== upgrade.sh errors (offline) → silent, launcher proceeds ==="
out=$(run_notice "could not reach origin" 1)
check "error: no notice"                "$out" absent  "[ilk-upgrade]"
check "error: no crash"                 "$out" absent  "error"

echo ""
echo "=== upgrade.sh missing → silent, launcher proceeds ==="
rm -f "$UPGRADE_SH"
ILK_SKILL_HOME="$FIXTURE_SKILLS" ILK_SKIP_MAIN=1 \
  out=$(bash -c 'source "'"$LAUNCH_SH"'"; print_toolkit_staleness_notice' 2>&1) || true
check "missing: no notice"              "$out" absent  "[ilk-upgrade]"
check "missing: no crash"               "$out" absent  "error"

echo ""
echo "=== launch.ps1 mirrors the notice (static) ==="
ps1="$(cat "$REPO_ROOT/skills/ilk-launcher/scripts/launch.ps1")"
check "ps1 has Write-ToolkitStalenessNotice" "$ps1" contains "Write-ToolkitStalenessNotice"
check "ps1 references upgrade.ps1"           "$ps1" contains "upgrade.ps1"
check "ps1 has -Check flag"                  "$ps1" contains "-Check"

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
