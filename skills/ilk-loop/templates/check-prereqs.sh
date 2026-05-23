#!/usr/bin/env bash
# Verify automatable prerequisites declared in PREREQUISITES.md.
#
# Customise the checks below per project before launching the loop.
# The loop launcher / watchdog can call this script to fail fast when
# the environment is not ready.
#
# Exit codes:
#   0  all checks pass
#   1  one or more checks failed (details printed)
#   2  script error

set -u

failed=()

check_tool() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "[FAIL] tool not on PATH: $name"
    failed+=("$name")
    return 1
  fi
  echo "[ ok ] $name -> $(command -v "$name")"
}

check_port() {
  local port="$1" service="$2"
  if ! lsof -iTCP:"$port" -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    echo "[FAIL] expected $service on port $port — nothing listening"
    failed+=("$service")
    return 1
  fi
  local pid
  pid=$(lsof -iTCP:"$port" -sTCP:LISTEN -P -n -t | head -n1)
  echo "[ ok ] $service on port $port (PID $pid)"
}

check_env_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "[FAIL] env var not set: $name"
    failed+=("$name")
    return 1
  fi
  echo "[ ok ] env var $name is set"
}

# ── Section B: tools on PATH ──────────────────────────────────────
# Uncomment / extend per project:
# check_tool git
# check_tool node
# check_tool python3

# ── Section A: services on expected ports ─────────────────────────
# check_port 5173 'vite dev'

# ── Section C: env vars ───────────────────────────────────────────
# check_env_var OPENAI_API_KEY

# ── Result ────────────────────────────────────────────────────────
if [[ ${#failed[@]} -gt 0 ]]; then
  echo ""
  echo "FAILED: ${#failed[@]} prereq(s) missing: ${failed[*]}"
  exit 1
fi
echo ""
echo "OK: all declared prereqs present"
exit 0
