#!/usr/bin/env bash
# bounce_daemons.sh — one implementation, two callers.
#
# Decides whether each platform-declared daemon is stale (recorded toolkit_head
# differs from the tree's HEAD) and bounces it via launchctl bootout+bootstrap.
#
# Exit codes:
#   0 — nothing to do (all daemons fresh)
#   1 — bounced at least one daemon (and verified it came back)
#   2 — could not reach at least one daemon (plist missing, not loaded,
#       bootstrap failed after retries, or bounced but daemon still absent)
#
# Retry: bootstrap is attempted up to 3 times with a 1s settle wait between
# attempts.  The wait exists because launchctl bootstrap can return exit 5
# immediately after bootout — the domain has not settled.  Observed on
# chad-mbp 2026-08-26; a manual retry seconds later succeeded.  The bound is
# not configurable — see design decisions in the sub-plan.
#
# Options:
#   --check   Detect-only: report staleness, bounce nothing.
#
# Environment:
#   ILK_BOUNCE_ALLOW_FOREIGN_HOME=1  Bypass the foreign-HOME refusal.
#                                     Set by test helpers that deliberately
#                                     run under a tmp HOME with a fake
#                                     launchctl on PATH.
#
# State file: ~/.ilk-data/scheduler.state.json
#   { "pid": <int>, "started_at": "<ISO-8601>", "toolkit_head": "<sha>" }
#
# Absent / empty / non-JSON / missing-key state file → treated as stale.
# Every daemon running today predates this change, so this is the first run.
#
# Portable: bash 3.2+ (macOS default).  No declare -A, no mapfile.

set -euo pipefail

# ── Options ────────────────────────────────────────────────────────────────

CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

# ── Platform daemon set ────────────────────────────────────────────────────
# Declared data, not hard-coded in bounce logic.  Adding a daemon is a
# one-row edit: append "name:label:plist_relpath" to the platform list.
#
# Uses parallel indexed arrays (bash 3.2 compatible — macOS ships 3.2).
# Three arrays: NAMES, LABELS, PLISTS — same index = same daemon.

PLATFORM="${ILK_BOUNCE_PLATFORM:-$(uname -s)}"

NAMES=""
LABELS=""
PLISTS=""

case "$PLATFORM" in
  Darwin)
    NAMES="scheduler"
    LABELS="net.inluck.ilk.scheduler"
    PLISTS="$HOME/Library/LaunchAgents/net.inluck.ilk.scheduler.plist"
    ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    NAMES="scheduler tray"
    LABELS="net.inluck.ilk.scheduler net.inluck.ilk-tray"
    PLISTS="$HOME/Library/LaunchAgents/net.inluck.ilk.scheduler.plist $HOME/Library/LaunchAgents/net.inluck.ilk-tray.plist"
    ;;
  *)
    echo "Unsupported platform: $PLATFORM" >&2
    exit 2
    ;;
esac

# ── Foreign HOME refusal ───────────────────────────────────────────────────
# A test that sets HOME to a tmp dir must not reach the real launchctl.
# Resolve the real home and refuse unless ILK_BOUNCE_ALLOW_FOREIGN_HOME=1.

real_home="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
if [[ -z "$real_home" ]]; then
  real_home="/Users/$(id -un)"
fi

if [[ "$HOME" != "$real_home" && "${ILK_BOUNCE_ALLOW_FOREIGN_HOME:-}" != "1" ]]; then
  echo "foreign HOME: $HOME (real home: $real_home)" >&2
  exit 2
fi

# ── Staleness decision ─────────────────────────────────────────────────────

ILK_DATA="${ILK_DATA_HOME:-${ILK_DATA_DIR:-$HOME/.ilk-data}}"
STATE_FILE="$ILK_DATA/scheduler.state.json"

stale=0
reason=""

if [[ ! -f "$STATE_FILE" ]]; then
  stale=1
  reason="state file absent"
elif [[ ! -s "$STATE_FILE" ]]; then
  stale=1
  reason="state file empty"
else
  # Try to parse toolkit_head from JSON (pure bash — no jq dependency).
  recorded_head=""
  if grep -q '"toolkit_head"' "$STATE_FILE" 2>/dev/null; then
    recorded_head=$(sed -n 's/.*"toolkit_head"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$STATE_FILE")
  fi

  if [[ -z "$recorded_head" ]]; then
    stale=1
    reason="state file missing toolkit_head or non-JSON"
  else
    # Get the tree's HEAD.  Use ILK_BOUNCE_TOOLKIT_PATH if set, else cwd.
    toolkit_path="${ILK_BOUNCE_TOOLKIT_PATH:-.}"
    current_head=$(git -C "$toolkit_path" rev-parse HEAD 2>/dev/null || echo "unknown")

    if [[ "$recorded_head" == "$current_head" ]]; then
      stale=0
      reason="fresh (toolkit_head matches HEAD)"
    else
      stale=1
      reason="stale (recorded $recorded_head, HEAD $current_head)"
    fi
  fi
fi

# ── Report ──────────────────────────────────────────────────────────────────

bounced=0
unreachable=0

# Iterate parallel arrays by index.
# Convert to indexed arrays for iteration (bash 3.2 compat).
i=0
for name in $NAMES; do
  # Extract the i-th label and plist from the space-separated strings.
  label=""
  plist=""
  j=0
  for l in $LABELS; do
    if [[ $j -eq $i ]]; then label="$l"; break; fi
    j=$((j + 1))
  done
  j=0
  for p in $PLISTS; do
    if [[ $j -eq $i ]]; then plist="$p"; break; fi
    j=$((j + 1))
  done

  gui_domain="gui/$(id -u)/${label}"

  # Check if the daemon is reachable (plist exists and daemon is loaded).
  plist_ok=0
  loaded=0

  if [[ -f "$plist" ]]; then
    plist_ok=1
  fi

  # In test mode, ILK_BOUNCE_DAEMON_LOADED overrides the load check.
  if [[ -n "${ILK_BOUNCE_DAEMON_LOADED:-}" ]]; then
    loaded="$ILK_BOUNCE_DAEMON_LOADED"
  else
    if launchctl print "$gui_domain" >/dev/null 2>&1; then
      loaded=1
    fi
  fi

  if [[ "$plist_ok" -eq 0 || "$loaded" -eq 0 ]]; then
    echo "unreachable: $name (plist=$plist_ok loaded=$loaded)"
    unreachable=1
    i=$((i + 1))
    continue
  fi

  if [[ "$stale" -eq 0 ]]; then
    echo "fresh: $name — $reason"
    i=$((i + 1))
    continue
  fi

  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "stale: $name — $reason (would bounce)"
    i=$((i + 1))
    continue
  fi

  # Bounce: bootout then bootstrap.  Never kill.
  echo "bouncing: $name — $reason"
  launchctl bootout "$gui_domain" 2>/dev/null || true

  # Retry bootstrap up to 3 times with a 1s settle wait between attempts.
  # Basis: observed failure recovered on first manual retry seconds later.
  # Three attempts with 1s spacing covers roughly that window with margin.
  # Wrong if bootstrap needs >~2s of settle time — then we report
  # unreachable early (pessimistic), never silently succeed.
  MAX_RETRIES=3
  bounce_ok=0
  attempt=0
  while [[ "$attempt" -lt "$MAX_RETRIES" ]]; do
    # Settle wait between attempts (not before the first).
    if [[ "$attempt" -gt 0 ]]; then
      sleep 1
    fi
    bootstrap_rc=0
    launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || bootstrap_rc=$?
    if [[ "$bootstrap_rc" -ne 0 ]]; then
      attempt=$((attempt + 1))
      continue
    fi
    # bootstrap returned 0 — verify the daemon actually appeared.
    if launchctl print "$gui_domain" >/dev/null 2>&1; then
      bounce_ok=1
      break
    fi
    # bootstrap said 0 but daemon still absent — retry.
    attempt=$((attempt + 1))
  done

  if [[ "$bounce_ok" -eq 1 ]]; then
    bounced=1
  elif [[ "$bootstrap_rc" -ne 0 ]]; then
    echo "unreachable: $name (bounce failed to restore: bootstrap exit $bootstrap_rc)"
    unreachable=1
  else
    echo "unreachable: $name (bounce failed to restore: daemon still absent after bootstrap)"
    unreachable=1
  fi
  i=$((i + 1))
done

# ── Exit status ─────────────────────────────────────────────────────────────

if [[ "$unreachable" -eq 1 ]]; then
  exit 2
elif [[ "$bounced" -eq 1 ]]; then
  exit 1
else
  exit 0
fi
