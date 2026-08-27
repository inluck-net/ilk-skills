#!/usr/bin/env bash
# Shared helper — source from any shell test harness that drives scheduler.sh
# or any other script resolving paths via HOME / ILK_DATA_HOME.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/_ilk_test_sandbox.sh"
#   ilk_test_sandbox "$(mktemp -d)"
#
# What it does (AC-1, AC-2):
#   1. Sets HOME to the given root.
#   2. Sets ILK_DATA_HOME to $root/.ilk-data.
#   3. Sets ILK_SKILL_HOME to the repo's skills/ dir (required: without it the
#      skill-root fallback probes $HOME/.codex|.cursor|.claude under the temp
#      HOME, none exist, and the run hangs).
#   4. Unsets ILK_DATA_DIR so the back-compat alias cannot override.
#   5. Creates $root/.ilk-data/logs/ before returning.
#
# Contract matches conftest.py's scheduler_sandbox fixture (SP1).

ilk_test_sandbox() {
  local root="$1"
  if [[ -z "$root" ]]; then
    echo "ilk_test_sandbox: usage: ilk_test_sandbox <root>" >&2
    return 1
  fi

  export HOME="$root"
  export ILK_DATA_HOME="$root/.ilk-data"
  unset ILK_DATA_DIR

  # Resolve ILK_SKILL_HOME: prefer the caller's repo layout, fall back to
  # the skill-root resolver's own location.
  local helper_dir
  helper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local skills_dir
  skills_dir="$(cd "$helper_dir/../.." && pwd)"
  export ILK_SKILL_HOME="$skills_dir"

  mkdir -p "$ILK_DATA_HOME/logs"
}
