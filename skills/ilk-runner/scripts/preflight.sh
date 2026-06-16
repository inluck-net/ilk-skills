#!/usr/bin/env bash
# Preflight gate for manual /ilk-run launches (macOS/Linux).
#
# Enforces three checks before the runner launches the loop:
#   (a) supervised_only master + live scheduler → HARD STOP
#   (b) queued master, none active → promote
#   (c) stale idle host windows + terminal sentinels → surface as warnings
#
# Exposes preflight_decision (pure) via ILK_DOTSOURCE_ONLY guard for testing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Dot-source guard: expose functions without running main ---
if [[ "${ILK_DOTSOURCE_ONLY:-}" == "1" ]]; then
  preflight_decision() {
    local master_status="$1" has_active="$2" supervised="$3" scheduler_alive="$4"

    # (a) supervised + scheduler alive → block
    if [[ "$supervised" == "true" && "$scheduler_alive" == "true" ]]; then
      printf "block=true\nreason=A cross-project scheduler is alive. Stop it before running a supervised_only master.\npromote=false\n"
      return
    fi
    # (b-i) draft → block (held deliberately)
    if [[ "$master_status" == "draft" ]]; then
      printf "block=true\nreason=Master is draft (held). Set it queued/active before launching.\npromote=false\n"
      return
    fi
    # (b-ii) queued + no active → promote
    if [[ "$master_status" == "queued" && "$has_active" == "false" ]]; then
      printf "block=false\nreason=\npromote=true\n"
      return
    fi
    # Safe to proceed
    printf "block=false\nreason=\npromote=false\n"
  }

  return 0 2>/dev/null || true
fi

# --- Resolve dependencies ---
source "$SCRIPT_DIR/../../ilk-loop/scripts/_ilk_skill_root.sh"
source "$SCRIPT_DIR/../../ilk-loop/scripts/_resolve_python.sh"

SKILL_ROOT="$(ilk_skill_root)"
LOOP_STATUS_PY="$SKILL_ROOT/ilk-loop/scripts/loop_status.py"
PROMOTE_PY="$SKILL_ROOT/ilk-loop/scripts/promote_next_master.py"

PROJECT_ROOT="${1:-}"

preflight_decision() {
  local master_status="$1" has_active="$2" supervised="$3" scheduler_alive="$4"

  if [[ "$supervised" == "true" && "$scheduler_alive" == "true" ]]; then
    printf "block=true\nreason=A cross-project scheduler is alive. Stop it before running a supervised_only master.\npromote=false\n"
    return
  fi
  if [[ "$master_status" == "draft" ]]; then
    printf "block=true\nreason=Master is draft (held). Set it queued/active before launching.\npromote=false\n"
    return
  fi
  if [[ "$master_status" == "queued" && "$has_active" == "false" ]]; then
    printf "block=false\nreason=\npromote=true\n"
    return
  fi
  printf "block=false\nreason=\npromote=false\n"
}

scheduler_alive() {
  if pgrep -f 'scheduler\.sh' >/dev/null 2>&1; then
    return 0
  fi
  # Fallback: check ps output
  if ps aux 2>/dev/null | grep -v grep | grep -q 'scheduler\.sh'; then
    return 0
  fi
  return 1
}

# --- Main ---
if [[ -z "$PROJECT_ROOT" ]]; then
  echo "ProjectRoot is required when not dot-sourcing." >&2
  exit 1
fi

# Resolve master state from loop_status
set +e
status_out="$(cd "$PROJECT_ROOT" && ilk_invoke_python "$LOOP_STATUS_PY")"
status_code=$?
set -e

# Parse master status
master_status="unknown"
has_active="false"
if echo "$status_out" | grep -qP 'status:\s*active'; then
  has_active="true"
fi
if [[ "$status_code" -eq 1 ]]; then
  [[ "$master_status" == "unknown" ]] && master_status="active"
fi

# Check supervised_only
supervised="false"
plans_dir="$HOME/.ilk-data/projects/$(basename "$PROJECT_ROOT")/plans"
master_file="$(find "$plans_dir" -maxdepth 1 -name 'MASTER-*.md' 2>/dev/null | head -1)"
if [[ -n "$master_file" ]] && grep -q 'supervised_only:\s*true' "$master_file" 2>/dev/null; then
  supervised="true"
fi

# Check scheduler
scheduler_alive_val="false"
if scheduler_alive; then
  scheduler_alive_val="true"
fi

# Stale warnings
# (c-i) Idle host windows
if command -v pgrep >/dev/null 2>&1; then
  for pid in $(pgrep -f 'run_ilk_loop|watchdog\.sh' 2>/dev/null || true); do
    if kill -0 "$pid" 2>/dev/null; then
      echo "WARNING: Idle host window detected — PID $pid"
    fi
  done
fi

# (c-ii) Terminal sentinel with live PID
il_data="$HOME/.ilk-data"
project_key="$(basename "$PROJECT_ROOT")"
sentinel_file="$il_data/projects/$project_key/runtime/last-exit.json"
if [[ -f "$sentinel_file" ]]; then
  sentinel_state="$(ilk_invoke_python -c "import json; d=json.load(open('$sentinel_file')); print(d.get('state',''))" 2>/dev/null || true)"
  sentinel_pid="$(ilk_invoke_python -c "import json; d=json.load(open('$sentinel_file')); print(d.get('pid',0))" 2>/dev/null || true)"
  if [[ -n "$sentinel_state" && "$sentinel_state" != "running" && -n "$sentinel_pid" && "$sentinel_pid" != "0" ]]; then
    if kill -0 "$sentinel_pid" 2>/dev/null; then
      echo "WARNING: Terminal sentinel with live PID $sentinel_pid (state=$sentinel_state). Consider cleaning stale state."
    fi
  fi
fi

# Make decision
decision="$(preflight_decision "$master_status" "$has_active" "$supervised" "$scheduler_alive_val")"
block="$(echo "$decision" | grep '^block=' | sed 's/^block=//')"
reason="$(echo "$decision" | grep '^reason=' | sed 's/^reason=//')"
promote="$(echo "$decision" | grep '^promote=' | sed 's/^promote=//')"

if [[ "$promote" == "true" ]]; then
  echo ""
  echo "Queued master found with no active master — promoting..."
  ilk_invoke_python "$PROMOTE_PY" --project "$PROJECT_ROOT"
fi

if [[ "$block" == "true" ]]; then
  echo ""
  echo "PREFLIGHT FAILED: $reason"
  exit 1
fi

echo "Preflight passed."
exit 0
