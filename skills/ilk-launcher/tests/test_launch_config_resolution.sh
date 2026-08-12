#!/usr/bin/env bash
# =============================================================================
# Test: read_project_config finds .ilk-launch.json at the project root.
# =============================================================================
# Handoff F5 / backlog a01f3bed38ce3726. A project declaring
# `iteration_timeout_min` at its ROOT had that value silently dropped, because
# read_project_config only looked in <external_plans_dir>/ and
# <root>/docs/plans/. gh-resolve declared 60, every scheduler-driven launch
# used the 30-minute default, and on 2026-08-12 its last step was killed
# mid-suite at exactly 1799.8s.
#
# The root location is not an invention: /ilk-plan step 8a already documents
# and reads <project_root>/.ilk-launch.json for the `autoschedule` opt-out.
#
# Precedence is asserted too: the two pre-existing locations must still win,
# so this fix cannot change any project that already resolved.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LAUNCH="$REPO_ROOT/skills/ilk-launcher/scripts/launch.sh"

failures=()
fail() { failures+=("$1"); }
pass() { echo "  PASS: $1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Extract the two pure functions under test without executing launch.sh's
# top-level body (which resolves the skill root and parses args).
HARNESS="$TMP/harness.sh"
{
  sed -n '/^get_external_plans_dir()/,/^}/p' "$LAUNCH"
  sed -n '/^read_project_config()/,/^}/p' "$LAUNCH"
} > "$HARNESS"

read_cfg() {
  # $1 = project path.  Echoes the resolved JSON.
  ( set +u; source "$HARNESS"; _SKILL_ROOT=""; read_project_config "$1" )
}

get_timeout() {
  python3 -c "import json,sys; print(json.load(sys.stdin).get('iteration_timeout_min',''))" <<<"$1"
}

# --- case 1: config at the project ROOT is found (the F5 regression) ---------
proj="$TMP/rootcfg"
mkdir -p "$proj"
printf '{"iteration_timeout_min": 60}\n' > "$proj/.ilk-launch.json"
got="$(get_timeout "$(read_cfg "$proj")")"
if [[ "$got" == "60" ]]; then
  pass "root .ilk-launch.json resolved (iteration_timeout_min=60)"
else
  fail "case 1: expected 60 from <root>/.ilk-launch.json, got '${got:-<empty>}'"
fi

# --- case 2: docs/plans/ still wins over the root (precedence unchanged) -----
proj2="$TMP/bothcfg"
mkdir -p "$proj2/docs/plans"
printf '{"iteration_timeout_min": 45}\n' > "$proj2/docs/plans/.ilk-launch.json"
printf '{"iteration_timeout_min": 60}\n' > "$proj2/.ilk-launch.json"
got2="$(get_timeout "$(read_cfg "$proj2")")"
if [[ "$got2" == "45" ]]; then
  pass "docs/plans/ retains precedence over root (45 wins)"
else
  fail "case 2: expected 45 (docs/plans wins), got '${got2:-<empty>}'"
fi

# --- case 3: no config anywhere still yields {} ------------------------------
proj3="$TMP/nocfg"
mkdir -p "$proj3"
got3="$(read_cfg "$proj3")"
if [[ "$got3" == "{}" ]]; then
  pass "no config anywhere still returns {}"
else
  fail "case 3: expected '{}', got '$got3'"
fi

# --- report ------------------------------------------------------------------
if [[ "${#failures[@]}" -gt 0 ]]; then
  echo "FAIL — ${#failures[@]} failure(s):"
  for f in "${failures[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "OK — .ilk-launch.json resolves at the project root; existing precedence intact."
exit 0
