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

  local scripts="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  local state runtime rc=0
  runtime=$(python3 "$scripts/ilk_paths.py" --start "$project_path" | python3 -c 'import json,sys;print(json.load(sys.stdin)["external_launcher_dir"])') || return 2
  state=$(python3 "$scripts/run_registry.py" status --runtime "$runtime" --project "$project_path") || rc=$?
  [[ "$rc" -eq 1 ]] && return 1
  [[ "$rc" -eq 0 ]] || return 2
  python3 -c 'import json,sys;d=json.loads(sys.argv[1]); rows=d["runs"]; print("\n".join(str(r["pid"]) for r in rows if r["liveness"]=="running"));sys.exit(0 if rows and all(r["liveness"]=="running" for r in rows) else 2)' "$state"
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
