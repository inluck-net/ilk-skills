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

# Shared data-dir resolver (ILK_DATA_HOME → ILK_DATA_DIR → ~/.ilk-data)
source "$REPO_ROOT/skills/ilk-loop/scripts/_ilk_data_dir.sh"

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

# --- --check: fetch + ahead/behind report ------------------------------------

do_check() {
  # Fetch silently; tolerate offline gracefully
  if ! git -C "$REPO_ROOT" fetch --quiet origin 2>/dev/null; then
    echo "error: could not reach origin — check your network connection" >&2
    exit 1
  fi

  # Resolve upstream; fall back to origin/<branch>
  local branch upstream behind
  branch="$(git -C "$REPO_ROOT" symbolic-ref --short HEAD)"
  upstream="$(git -C "$REPO_ROOT" for-each-ref --format='%(upstream:short)' "refs/heads/$branch" 2>/dev/null || true)"
  if [[ -z "$upstream" ]]; then
    upstream="origin/$branch"
  fi

  behind="$(git -C "$REPO_ROOT" rev-list --count HEAD.."$upstream" 2>/dev/null || echo "0")"

  if [[ "$behind" -eq 0 ]]; then
    echo "up to date"
  else
    local plural=""
    [[ "$behind" -ne 1 ]] && plural="s"
    echo "behind by ${behind} commit${plural} — run with --apply"
  fi
}

# --- live-loop guard ---------------------------------------------------------

# True only when $1 is alive AND its command line is actually an ilk process.
#
# `kill -0` alone answers "does some process hold this PID", which is not the
# question: PIDs are recycled. Observed 2026-08-10 — a kira-cloudflare
# running.pid written 2026-07-21 named PID 23339, which by then belonged to an
# interactive `-zsh` running 21 hours. The upgrade was refused on a 20-day-stale
# file pointing at an unrelated shell. Mirrors pid_health.pid_command_alive,
# which exists for exactly this and is used by status_progress/status_all.
ilk_pid_alive() {
  local pid="$1"
  [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  local cmd
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  # Unreadable command (permissions) -> fall back to liveness, same as
  # pid_command_alive: better to over-block than to swap code under a live loop.
  [[ -z "$cmd" ]] && return 0
  case "$cmd" in
    *run_ilk_loop*|*watchdog.sh*|*watchdog.ps1*|*scheduler.sh*|*scheduler_scan*|*scheduler.ps1*)
      return 0 ;;
  esac
  return 1
}

check_live_pids() {
  local data_dir; data_dir="$(ilk_data_dir)"
  local projects_dir="$data_dir/projects"
  local active_pids=()

  # Also check the cross-project scheduler PID file (independent of projects dir)
  local scheduler_pidfile="$data_dir/scheduler.pid"
  if [[ -f "$scheduler_pidfile" ]]; then
    local scheduler_pid
    scheduler_pid="$(cat "$scheduler_pidfile" 2>/dev/null || true)"
    if ilk_pid_alive "$scheduler_pid"; then
      active_pids+=("scheduler (PID $scheduler_pid)")
    fi
  fi

  if [[ ! -d "$projects_dir" ]]; then
    if [[ ${#active_pids[@]} -gt 0 ]]; then
      echo "error: live loop/watchdog detected — refusing to update skill code:" >&2
      for p in "${active_pids[@]}"; do
        echo "  - $p" >&2
      done
      echo "Stop it cleanly first: bash $REPO_ROOT/skills/ilk-watchdog/scripts/stop_watchdog.sh --project-path <project>  (or /ilk-stop). Then re-run, or use --force to override." >&2
      return 1
    fi
    return 0
  fi

  # Scan launcher and watchdog PID files
  for pidfile in "$projects_dir"/*/runtime/launcher/running.pid \
                 "$projects_dir"/*/runtime/watchdog/*.pid; do
    [[ -f "$pidfile" ]] || continue
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    [[ -z "$pid" ]] && continue

    local project_dir project_name
    project_dir="$(dirname "$(dirname "$(dirname "$pidfile")")")"
    project_name="$(basename "$project_dir")"

    # Stale-sentinel guard (mirrors scheduler is_running, v0.9.1): a lingering
    # detached worker shell can keep a launcher PID alive after the loop exits.
    # When the project's last-exit.json is terminal (state != running) that PID
    # is a zombie, not a live loop. Scoped to launcher PIDs; a live watchdog
    # must still block.
    case "$pidfile" in
      */runtime/launcher/running.pid)
        local sentinel="$project_dir/runtime/last-exit.json"
        if [[ -f "$sentinel" ]]; then
          local state
          state="$(grep -oE '"state"[[:space:]]*:[[:space:]]*"[^"]*"' "$sentinel" 2>/dev/null | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)".*/\1/')"
          if [[ -n "$state" && "$state" != "running" ]]; then
            continue
          fi
        fi
        ;;
    esac

    if ilk_pid_alive "$pid"; then
      active_pids+=("$project_name (PID $pid)")
    fi
  done

  if [[ ${#active_pids[@]} -gt 0 ]]; then
    echo "error: live loop/watchdog detected — refusing to update skill code:" >&2
    for p in "${active_pids[@]}"; do
      echo "  - $p" >&2
    done
    echo "Stop it cleanly first: bash $REPO_ROOT/skills/ilk-watchdog/scripts/stop_watchdog.sh --project-path <project>  (or /ilk-stop). Then re-run, or use --force to override." >&2
    return 1
  fi
  return 0
}

# --- drift detection ---------------------------------------------------------

has_drift() {
  # Check if any installed command file under known homes is a regular file
  # (copy) rather than a symlink — indicates install was done with copy-fallback
  # or the link was replaced.
  local homes=()
  for candidate in "$HOME/.cursor" "$HOME/.claude" "$HOME/.codex"; do
    [[ -d "$candidate/commands" ]] && homes+=("$candidate")
  done

  for home in "${homes[@]+"${homes[@]}"}"; do
    for cmd_file in "$home"/commands/ilk*; do
      [[ -f "$cmd_file" ]] || continue
      if [[ ! -L "$cmd_file" ]]; then
        return 0  # drift found: regular file, not symlink
      fi
    done
  done

  # Check for missing links: an in-repo command/skill with no corresponding
  # link in any host commands/ or skills/ dir.
  local repo_commands="$REPO_ROOT/commands"
  if [[ -d "$repo_commands" ]]; then
    for repo_cmd in "$repo_commands"/ilk*; do
      [[ -f "$repo_cmd" ]] || continue
      local cmd_basename
      cmd_basename="$(basename "$repo_cmd")"
      local found_link=0
      for home in "${homes[@]+"${homes[@]}"}"; do
        if [[ -L "$home/commands/$cmd_basename" || -f "$home/commands/$cmd_basename" ]]; then
          found_link=1
          break
        fi
      done
      if [[ $found_link -eq 0 ]]; then
        return 0  # drift found: missing link for in-repo command
      fi
    done
  fi

  # Check for added/removed skills or commands between old and new rev
  # (called after pull, so we compare the pulled changes)
  return 1  # no drift
}

# --- reconcile links + auto-plan (idempotent, always safe to call) -----------

reconcile_links() {
  local diff_status="${1:-}"

  # Drift detection + conditional re-install
  local need_reinstall=0

  # Check for copy-installed command files or missing links
  if has_drift; then
    need_reinstall=1
  fi

  # Check if skills or commands were added/removed
  if [[ -n "$diff_status" ]]; then
    if echo "$diff_status" | grep -qE '^[AD]'; then
      need_reinstall=1
    fi
  fi

  if [[ $need_reinstall -eq 1 ]]; then
    echo ""
    echo "Drift detected — re-running installer..."
    bash "$REPO_ROOT/install.sh" --apply
    echo "Re-install complete. New code is effective next invocation."
  else
    echo ""
    echo "Links current, no re-install needed. New code is effective next invocation."
  fi

  # Reconcile auto-plan managed block (unconditional)
  bash "$REPO_ROOT/install.sh" --only-auto-plan --apply
  echo "Auto-plan block reconciled."
}

# --- --apply: ff-only pull + changelog + conditional re-install --------------

do_apply() {
  local old_rev new_rev

  # Self-update guard: refuse if a live loop/watchdog is running
  if [[ $force -eq 0 ]]; then
    if ! check_live_pids; then
      exit 1
    fi
  fi

  old_rev="$(git -C "$REPO_ROOT" rev-parse HEAD)"

  # Fetch silently
  if ! git -C "$REPO_ROOT" fetch --quiet origin 2>/dev/null; then
    echo "error: could not reach origin — check your network connection" >&2
    exit 1
  fi

  # Resolve upstream
  local branch upstream
  branch="$(git -C "$REPO_ROOT" symbolic-ref --short HEAD)"
  upstream="$(git -C "$REPO_ROOT" for-each-ref --format='%(upstream:short)' "refs/heads/$branch" 2>/dev/null || true)"
  if [[ -z "$upstream" ]]; then
    upstream="origin/$branch"
  fi

  # Already current?
  local behind
  behind="$(git -C "$REPO_ROOT" rev-list --count HEAD.."$upstream" 2>/dev/null || echo "0")"
  if [[ "$behind" -eq 0 ]]; then
    echo "already current"
    reconcile_links ""
    return 0
  fi

  # Fast-forward pull (suppress git's own output; we print our own summary)
  if ! git -C "$REPO_ROOT" pull --ff-only >/dev/null 2>&1; then
    echo "error: fast-forward pull failed — rebase or reset manually" >&2
    exit 1
  fi

  new_rev="$(git -C "$REPO_ROOT" rev-parse HEAD)"

  # Changelog
  echo ""
  echo "Changelog:"
  git -C "$REPO_ROOT" log --oneline "${old_rev}..${new_rev}"

  # Skill/command changes
  local diff_status
  diff_status="$(git -C "$REPO_ROOT" diff --name-status "${old_rev}" "${new_rev}" -- skills/ commands/ 2>/dev/null || true)"
  if [[ -n "$diff_status" ]]; then
    echo ""
    echo "Skill/command changes:"
    echo "$diff_status" | while IFS=$'\t' read -r status path; do
      case "$status" in
        A) echo "  added: $path" ;;
        D) echo "  removed: $path" ;;
        M) echo "  modified: $path" ;;
        R*) echo "  renamed: $path" ;;
        *) echo "  $status: $path" ;;
      esac
    done
  fi

  reconcile_links "$diff_status"
}

# --- mode dispatch -----------------------------------------------------------

case "$mode" in
  check)  do_check ;;
  apply)  do_apply ;;
esac
