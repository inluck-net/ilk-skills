#!/usr/bin/env bash
set -euo pipefail

# ilk-upgrade — pull the latest ilk-skills and make it effective.
#
# Resolves the toolkit clone from the script's own real (symlink-resolved)
# path, pulls the latest, re-runs the installer when needed, and reports
# what changed.
#
# Modes:
#   --check   read-only staleness report (default)
#   --apply   pull + conditionally re-install
#   --force   override dirty-tree and live-loop guards
#   --dry-run preview what --apply would do (alias for --check)
#   -h|--help print this help
#
# Exit codes:
#   0  success (up to date, or applied cleanly)
#   1  operational error (network, ff-only failure, etc.)
#   2  usage / environment error (not a repo, unknown flag, etc.)

usage() {
  sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- defaults ----------------------------------------------------------------

mode="check"
force=0

# --- arg parsing (mirrors install.sh style) ----------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      mode="check"
      ;;
    --apply)
      mode="apply"
      ;;
    --force)
      force=1
      ;;
    --dry-run)
      mode="check"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown flag: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

# --- repo self-resolution ----------------------------------------------------

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
REPO_ROOT="$(cd "$SELF/../../.." && pwd -P)"

if [[ ! -d "$REPO_ROOT/.git" ]]; then
  echo "error: not an ilk-skills clone (no .git): $REPO_ROOT" >&2
  exit 2
fi
if [[ ! -f "$REPO_ROOT/install.sh" ]]; then
  echo "error: not an ilk-skills clone (no install.sh): $REPO_ROOT" >&2
  exit 2
fi

# --- git state guards --------------------------------------------------------

# Detached HEAD check
if ! git -C "$REPO_ROOT" symbolic-ref -q HEAD >/dev/null 2>&1; then
  echo "error: detached HEAD in $REPO_ROOT — checkout a branch first" >&2
  exit 2
fi

# Dirty tree check (relevant for --apply; --check just notes it)
dirty_files="$(git -C "$REPO_ROOT" status --porcelain)"
if [[ -n "$dirty_files" ]]; then
  if [[ "$mode" == "apply" && $force -eq 0 ]]; then
    echo "error: dirty working tree in $REPO_ROOT — commit or stash first (or use --force)" >&2
    exit 2
  fi
  echo "warning: dirty working tree in $REPO_ROOT" >&2
fi

# --- mode dispatch (populated in steps 2-4) ----------------------------------

# case "$mode" in
#   check)  ... ;;
#   apply)  ... ;;
# esac
