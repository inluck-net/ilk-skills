#!/usr/bin/env bash
# =============================================================================
# Install or uninstall logon auto-start for the ilk scheduler daemon (macOS).
# =============================================================================
# Installs a per-user LaunchAgent that runs scheduler.sh as a long-lived poll
# loop in the GUI session, started at login and kept alive across crashes.
# This is the macOS counterpart to install-scheduler-autostart.ps1.
#
# scheduler.sh has its own single-instance pidfile guard and infinite poll
# loop, so launchd is used purely as a supervisor (RunAtLoad + KeepAlive).
# We do NOT pass --detach: launchd owns the process directly.
#
# Idempotent: re-running boots out any existing agent and re-bootstraps it.
#
# Usage:
#   install-scheduler-autostart.sh            # install (idempotent)
#   install-scheduler-autostart.sh --uninstall
#   install-scheduler-autostart.sh --status
# =============================================================================
set -euo pipefail

LABEL="net.inluck.ilk.scheduler"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

# --- resolve scheduler.sh next to this script, following symlinks ------------
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" != /* ]] && src="$dir/$src"
done
SCRIPT_DIR="$(cd -P "$(dirname "$src")" && pwd)"
SCHEDULER_SH="${SCRIPT_DIR}/scheduler.sh"

ACTION="install"
case "${1:-}" in
  --uninstall) ACTION="uninstall" ;;
  --status)    ACTION="status" ;;
  "")          ACTION="install" ;;
  *) echo "Unknown option: $1" >&2; exit 1 ;;
esac

if [[ "$ACTION" == "status" ]]; then
  if [[ -f "$PLIST" ]]; then
    echo "plist: $PLIST"
    launchctl print "${DOMAIN}/${LABEL}" 2>/dev/null | grep -E "state =|pid =|program =" || \
      echo "(agent not currently loaded)"
  else
    echo "(no LaunchAgent installed)"
  fi
  exit 0
fi

if [[ "$ACTION" == "uninstall" ]]; then
  if [[ "${ILK_AUTOSTART_NO_LOAD:-}" != "1" ]]; then
    launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
  fi
  rm -f "$PLIST"
  echo "[ilk-scheduler] auto-start removed."
  exit 0
fi

# --- install -----------------------------------------------------------------
if [[ ! -f "$SCHEDULER_SH" ]]; then
  echo "ERROR: scheduler.sh not found at $SCHEDULER_SH" >&2
  exit 1
fi

LOG_DIR="${HOME}/.ilk-data/logs"
mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

# launchd starts agents with a minimal PATH; the scheduler shells out to
# python3, screen/tmux, gh, and the claude CLI. Provide a sane superset that
# covers Homebrew (Apple Silicon + Intel) and the user-local bin.
AGENT_PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCHEDULER_SH}</string>
        <string>--poll-min</string>
        <string>5</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${AGENT_PATH}</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <!-- Restart on crash (non-zero exit) only. A clean exit-0 means lock
         contention (another scheduler instance is already running) and must
         NOT trigger a relaunch — otherwise the managed instance rapid-fire
         exit-0s until launchd throttles the agent out of existence. -->
    <key>KeepAlive</key>
    <dict><key>SuccessfulExit</key><false/></dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/scheduler-launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/scheduler-launchd.err.log</string>
</dict>
</plist>
PLIST_EOF

# Re-bootstrap idempotently. ILK_AUTOSTART_NO_LOAD=1 writes the plist but
# skips the launchctl calls (used by tests to validate plist generation
# without mutating the real per-user launchd domain).
if [[ "${ILK_AUTOSTART_NO_LOAD:-}" == "1" ]]; then
  echo "[ilk-scheduler] plist written (ILK_AUTOSTART_NO_LOAD=1; launchctl skipped):"
  echo "  $PLIST"
  exit 0
fi

launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "$PLIST"
launchctl enable "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl kickstart -k "${DOMAIN}/${LABEL}" 2>/dev/null || true

echo "[ilk-scheduler] auto-start installed and started."
echo "  Label:  ${LABEL}"
echo "  Plist:  ${PLIST}"
echo "  Logs:   ${LOG_DIR}/scheduler.log (decisions), scheduler-launchd.{out,err}.log"
echo "  Status: launchctl print ${DOMAIN}/${LABEL}"
echo "  Stop:   $(basename "$0") --uninstall"
