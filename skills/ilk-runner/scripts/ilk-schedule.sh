#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Launch the cross-project scheduler (detached) or preview its planned run.
# =============================================================================
# Thin wrapper around scheduler.sh. Resolves the skill root, then either:
#   --dry-run: previews via scheduler.sh --dry-run --once (no session spawned)
#   default:   spawns scheduler.sh --detach in a screen session
#
# Mirrors how ilk-run.sh wraps the per-project launcher+watchdog.
# =============================================================================

source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

SCHEDULER_SH="${_SKILL_ROOT}/ilk-watchdog/scripts/scheduler.sh"

POLL_MIN=5
MAX_CONCURRENT=5
DRY_RUN=false

usage() {
  cat <<'EOF'
Usage: ilk-schedule.sh [OPTIONS]

Launch the cross-project scheduler (detached) or preview its planned run.

Options:
  --poll-min N          Polling interval in minutes. Default 5.
  --max-concurrent N    Max concurrent live loops. Default 5.
  --dry-run             Preview the scheduler invocation without spawning a session.
  -h, --help            Show this help and exit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --poll-min)
      POLL_MIN="$2"
      shift 2
      ;;
    --max-concurrent)
      MAX_CONCURRENT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$SCHEDULER_SH" ]]; then
  echo "ERROR: Scheduler not found at: $SCHEDULER_SH" >&2
  exit 1
fi

if [[ "$DRY_RUN" == true ]]; then
  echo "[ilk-scheduler] preview (dry-run, single cycle):"
  echo ""
  bash "$SCHEDULER_SH" --dry-run --once --poll-min "$POLL_MIN" --max-concurrent "$MAX_CONCURRENT"
  exit $?
fi

# Live launch: spawn scheduler in a detached screen session
bash "$SCHEDULER_SH" --detach --poll-min "$POLL_MIN" --max-concurrent "$MAX_CONCURRENT"
exit $?
