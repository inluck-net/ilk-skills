#!/usr/bin/env python3
"""Pure progress-health decisions shared by the runner and the watchdog.

Two failure modes the loop must recover from (both seen on uccargo 2026-06-13):

- **startup hang** — the runner launched but no iteration has produced a JSONL
  record within the threshold (a pre-iter-1 hang, e.g. wedged branch setup). The
  runner's per-iteration timeout never arms, so without this it runs forever.
- **hung-alive** — the sentinel says ``state=running`` and the PID is alive, but
  no progress (sentinel/JSONL mtime) has advanced in longer than the threshold.
  The watchdog otherwise trusts ``running`` forever.

Both decisions are PURE (epoch-seconds in, bool out) so they unit-test
hermetically and both shells can call the CLI. Timestamps are epoch seconds
(PowerShell: ``[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()``; bash: ``date +%s``).
"""

from __future__ import annotations

import argparse
import sys


def startup_hang_exceeded(launch_ts: float, first_iter_seen: bool,
                          now: float, threshold_min: float) -> bool:
    """True iff no iteration has started AND now - launch_ts >= threshold."""
    if first_iter_seen:
        return False
    return (now - launch_ts) >= threshold_min * 60.0


def hung_alive(state: str, last_progress_ts: float,
               now: float, threshold_min: float) -> bool:
    """True iff state == 'running' AND progress has been stale >= threshold."""
    if state != "running":
        return False
    return (now - last_progress_ts) >= threshold_min * 60.0


def main() -> int:
    p = argparse.ArgumentParser(description="Loop progress-health decisions.")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("startup-hang", help="Has the runner hung before iter 1?")
    s.add_argument("--launch-ts", type=float, required=True)
    s.add_argument("--now", type=float, required=True)
    s.add_argument("--threshold-min", type=float, required=True)
    s.add_argument("--iter-seen", action="store_true",
                   help="An iteration JSONL record has appeared.")

    h = sub.add_parser("hung-alive", help="Is a state=running loop hung (stale)?")
    h.add_argument("--state", required=True)
    h.add_argument("--last-progress-ts", type=float, required=True)
    h.add_argument("--now", type=float, required=True)
    h.add_argument("--threshold-min", type=float, required=True)

    args = p.parse_args()
    if args.cmd == "startup-hang":
        hit = startup_hang_exceeded(args.launch_ts, args.iter_seen, args.now, args.threshold_min)
    elif args.cmd == "hung-alive":
        hit = hung_alive(args.state, args.last_progress_ts, args.now, args.threshold_min)
    else:
        p.print_help()
        return 2
    # Print 1/0 (shells read stdout); exit 0 always so the call itself is not an error.
    print("1" if hit else "0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
