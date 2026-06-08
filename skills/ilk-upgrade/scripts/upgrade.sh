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

# --- repo self-resolution (populated in step 1) ------------------------------

# SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
# REPO_ROOT="$(cd "$SELF/../../.." && pwd -P)"

# --- mode dispatch (populated in steps 2-4) ----------------------------------

# case "$mode" in
#   check)  ... ;;
#   apply)  ... ;;
# esac
