#!/usr/bin/env bash
# Shared helper — source from any ilk-* bash script that reads a pidfile.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/../../ilk-loop/scripts/_ilk_pid.sh"
#   if ilk_pid_alive "$pid"; then ... fi

# True only when $1 is alive AND its command line is actually an ilk process.
#
# `kill -0` alone answers "does some process hold this PID", which is not the
# question: PIDs are recycled. Observed 2026-08-10 — a kira-cloudflare
# running.pid written 2026-07-21 named PID 23339, which by then belonged to an
# interactive `-zsh`. Every guard that reads a pidfile and asks only `kill -0`
# wedges on that: the scheduler skipped the project as `skip-busy` on every
# poll for 20 days. Mirrors pid_health.pid_command_alive, which exists for
# exactly this and is already used by status_progress/status_all.
#
# The patterns must cover the *pidfile writer*, not just the loop script:
# running.pid names the `bash -c` wrapper, whose command line embeds the
# run_ilk_loop_* invocation (verified against a live loop, 2026-08-10).
# Echo the PIDs of live runner processes for $1 (a project path), one per
# line. Derived from the process table, not from any pid file: running.pid
# named 1 of 10 live runners on 2026-08-12.
ilk_project_runners() {
  local project_path="$1"
  [[ -n "$project_path" ]] || return 1

  # Normalise: strip trailing slash, resolve symlinks via cd+pwd -P so
  # /tmp vs /private/tmp on macOS does not cause a miss.
  local norm="${project_path%/}"
  local resolved
  resolved="$(cd "$norm" 2>/dev/null && pwd -P)" || resolved="$norm"

  # Match both the bash -c wrapper and the run_ilk_loop_claude.sh process.
  # LITERAL substring via awk index(), never `$0 ~ pat`: `~` treats the path as
  # a REGEX, so a project path containing `.` (e.g. `tmp.EVYaXMrl92`, or any
  # dotted directory) would match a DIFFERENT project's runner and report a
  # false busy — the same wedge class v0.9.55 fixed, arriving by another route.
  # Verified 2026-08-12: with `~`, querying `/…/ilk.test` matched a live runner
  # whose real path was `/…/ilkAtest`.
  # Exclude self ($$), parent ($PPID), and grep/this function from results.
  ps -eo pid,command | awk -v pat="$norm" -v pat2="$resolved" '
    index($0, "run_ilk_loop_claude") > 0 &&
    (index($0, pat) > 0 || index($0, pat2) > 0) {
      # Skip lines containing our own grep or this function
      if (index($0, "ilk_project_runners") > 0) next
      if (index($0, "grep") > 0 && index($0, "run_ilk_loop") > 0) next
      print $1
    }
  ' | awk 'NF' | while read -r pid; do
    [[ "$pid" != "$$" && "$pid" != "$PPID" ]] && echo "$pid"
  done | grep .
}

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
