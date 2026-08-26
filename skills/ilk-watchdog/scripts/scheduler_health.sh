#!/usr/bin/env bash
# scheduler_health.sh — detect and restore a dead scheduler agent.
#
# Reports one of three states:
#   loaded  (exit 0) — agent is present in the launchd domain
#   absent  (exit 1) — agent is not present; bootstrapped if no hold
#   held    (exit 2) — agent is absent but a hold file suppresses restore
#
# The hold sentinel is $ILK_DATA/scheduler.hold.  Present ⇒ the check
# reports held and does nothing.  This is the operator-intent case:
# the current master deliberately stops the scheduler for its window,
# so an auto-restore that ignored intent would fight the operator mid-run.
#
# Bootstrap retries: bounded 3 attempts with 1s settle wait between,
# matching bounce_daemons.sh's settle-retry semantics.
#
# Environment:
#   ILK_DATA_HOME / ILK_DATA_DIR — data directory (default ~/.ilk-data)
#
# See: sub-plan 2026-08-26c-a-dead-scheduler-gets-noticed

set -euo pipefail

LABEL="net.inluck.ilk.scheduler"
HEALTH_LABEL="net.inluck.ilk.scheduler-health"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

ILK_DATA="${ILK_DATA_HOME:-${ILK_DATA_DIR:-$HOME/.ilk-data}}"
HOLD_FILE="$ILK_DATA/scheduler.hold"
LOG_FILE="$ILK_DATA/logs/scheduler-health.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  local msg="[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
  echo "$msg" | tee -a "$LOG_FILE"
}

# ── Hold check ──────────────────────────────────────────────────────────────

if [[ -f "$HOLD_FILE" ]]; then
  log "held — hold file present at $HOLD_FILE"
  echo "held"
  exit 2
fi

# ── Liveness check ──────────────────────────────────────────────────────────

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  log "loaded"
  echo "loaded"
  exit 0
fi

# ── Agent absent — bootstrap ────────────────────────────────────────────────

log "absent — attempting restore"

if [[ ! -f "$PLIST" ]]; then
  log "absent — plist missing at $PLIST, cannot bootstrap"
  echo "absent"
  exit 1
fi

MAX_RETRIES=3
attempt=0
restored=0

while [[ "$attempt" -lt "$MAX_RETRIES" ]]; do
  if [[ "$attempt" -gt 0 ]]; then
    sleep 1
  fi

  bootstrap_rc=0
  launchctl bootstrap "${DOMAIN}" "$PLIST" 2>/dev/null || bootstrap_rc=$?

  if [[ "$bootstrap_rc" -eq 0 ]]; then
    # Verify the daemon actually appeared.
    if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
      restored=1
      break
    fi
    log "bootstrap succeeded but daemon still absent (attempt $((attempt + 1))/$MAX_RETRIES)"
  else
    log "bootstrap failed: exit $bootstrap_rc (attempt $((attempt + 1))/$MAX_RETRIES)"
  fi

  attempt=$((attempt + 1))
done

if [[ "$restored" -eq 1 ]]; then
  log "absent — restored after $((attempt + 1)) attempt(s)"
  echo "absent"
  exit 1
else
  log "absent — restore failed after $MAX_RETRIES attempts"
  echo "absent"
  exit 1
fi
