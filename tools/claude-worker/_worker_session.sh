# Shared helper — source from claude-worker.sh and bootstrap.sh.
#
# Provides sentinel-based worker session tracking that survives PID reuse.
# The sentinel is a simple key=value text file (no JSON parser needed):
#
#   pid=82004
#   start=2026-06-06T05:46:49.1234567+08:00
#   kind=claude-worker
#
# Legacy bare-integer PID files are handled gracefully (conservative liveness).

# Write a sentinel for the current shell process.
# Usage: worker_sentinel_write <pidfile>
worker_sentinel_write() {
  local pidfile="$1"
  local pid=$$
  local start_time=""

  # ps -o lstart= works on macOS and Linux; Git Bash on Windows may not have it.
  start_time="$(ps -o lstart= -p "$pid" 2>/dev/null || true)"
  start_time="$(echo "$start_time" | xargs)"  # trim whitespace

  {
    echo "pid=$pid"
    echo "start=$start_time"
    echo "kind=claude-worker"
  } > "$pidfile" 2>/dev/null || {
    echo "WARN: could not write worker sentinel: $pidfile" >&2
    echo "      provider-switch guardrails may not detect this running session." >&2
  }
}

# Remove a sentinel file (idempotent).
# Usage: worker_sentinel_remove <pidfile>
worker_sentinel_remove() {
  local pidfile="$1"
  rm -f "$pidfile" 2>/dev/null || true
}

# Check whether a sentinel indicates a genuinely active worker session.
# Returns 0 (true) if active, 1 (false) if stale or missing.
# Usage: worker_session_active <pidfile>
worker_session_active() {
  local pidfile="$1"

  if [[ ! -f "$pidfile" ]]; then
    return 1
  fi

  local content
  content="$(cat "$pidfile" 2>/dev/null || true)"

  if [[ -z "$(echo "$content" | tr -d '[:space:]')" ]]; then
    return 1
  fi

  local target_pid=""
  local start_time=""
  local is_key_value=0

  # Parse key=value sentinel
  while IFS= read -r line; do
    line="$(echo "$line" | xargs)"
    if [[ "$line" =~ ^pid=(.+)$ ]]; then
      target_pid="${BASH_REMATCH[1]}"
      target_pid="$(echo "$target_pid" | xargs)"
      is_key_value=1
    elif [[ "$line" =~ ^start=(.+)$ ]]; then
      start_time="${BASH_REMATCH[1]}"
      start_time="$(echo "$start_time" | xargs)"
    fi
  done <<< "$content"

  # Legacy bare-integer file: no "start" line -> conservative (alive = active)
  if [[ $is_key_value -eq 0 ]]; then
    target_pid="$(echo "$content" | grep -o '^[0-9]*' || true)"
    if [[ -z "$target_pid" ]]; then
      return 1
    fi
    if kill -0 "$target_pid" 2>/dev/null; then
      return 0
    else
      return 1
    fi
  fi

  if [[ -z "$target_pid" ]]; then
    return 1
  fi

  # Check PID liveness
  if ! kill -0 "$target_pid" 2>/dev/null; then
    return 1
  fi

  # Key=value sentinel: active iff start time matches
  if [[ -z "$start_time" ]]; then
    # No start time recorded -> cannot verify identity -> conservative: treat as active
    return 0
  fi

  local actual_start
  actual_start="$(ps -o lstart= -p "$target_pid" 2>/dev/null || true)"
  actual_start="$(echo "$actual_start" | xargs)"

  if [[ "$actual_start" == "$start_time" ]]; then
    return 0
  else
    return 1
  fi
}
