#!/usr/bin/env bash
# =============================================================================
# Regression (2026-09-20): a runner for project B must not make project A
# busy merely because A's path appears in the runner's SCRIPT path.
#
# Every runner launched by this toolkit names the toolkit repo in its argv
# (the script lives there). The bare-substring matcher therefore read the
# toolkit's own project as busy whenever ANY project ran — measured live:
# the scheduler skip-busied ilk-skills for hours while kira ran, and
# ilk-run.sh refused with "already running (PID <kira's>)".
#
# The match must be on the path in its role as the --project-path VALUE.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ILK_PID_SH="$REPO_ROOT/skills/ilk-loop/scripts/_ilk_pid.sh"
# shellcheck disable=SC1090
source "$ILK_PID_SH"

TMP="$(mktemp -d)"
fake_pid=""
# Kill the wrapper AND its sleep child (killing the bash wrapper alone
# orphans `sleep 300`, which would hold this test's stdout pipe open and
# hang the harness for the full sleep duration).
trap 'kill "$fake_pid" 2>/dev/null; pkill -P "$fake_pid" 2>/dev/null; rm -rf "$TMP"' EXIT

# A runner SCRIPT living under a "toolkit repo" path, launched for a
# DIFFERENT project — exactly kira's run as seen from the ilk-skills repo.
# stdout/stderr to /dev/null so the spawned process tree never holds the
# harness's output pipe.
TOOLKIT="$TMP/toolkit-repo/skills/ilk-loop/scripts"
OTHER_PROJECT="$TMP/other-project"
mkdir -p "$TOOLKIT" "$OTHER_PROJECT"
printf '#!/usr/bin/env bash\nsleep 300\n' > "$TOOLKIT/run_ilk_loop_claude.sh"
bash "$TOOLKIT/run_ilk_loop_claude.sh" --project-path "$OTHER_PROJECT" \
  --max-iterations 1 >/dev/null 2>&1 &
fake_pid=$!
sleep 0.4

# 1. The toolkit path appears in the runner's argv (script location) but
#    NOT as --project-path: it must NOT be reported busy.
if ilk_project_runners "$TMP/toolkit-repo" >/dev/null 2>&1; then
  echo "FAIL: toolkit repo reported busy by another project's runner" >&2
  exit 1
fi

# 2. The runner's own project is still detected (the original purpose).
if ! ilk_project_runners "$OTHER_PROJECT" >/dev/null 2>&1; then
  echo "FAIL: the runner's own project not detected" >&2
  exit 1
fi

echo "PASS: runner busy-match is --project-path specific"
