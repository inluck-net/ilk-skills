#!/usr/bin/env python3
"""relaunch_guard — may the watchdog relaunch this project right now?

Asked immediately before every watchdog relaunch (design §7 D4).  On
2026-09-28 at 22:58:25 and 23:03:59 the watchdog relaunched ilk-skills with
``--force`` after operator stops, while both its masters were parked.  It
took its verdict from a postmortem (``source=postmortem`` ->
``max-iter-bound``, a whitelist label) instead of the sentinel, and nothing
between the verdict and the relaunch looked at the project again.

Checks, in order:

  alive          the pid recorded in ``run.lock`` is a live ilk runner, or
                 a launch newer
                 than the terminal sentinel is still inside its launch window
                 -- the gap between dispatch and the lock (``running.pid``
                 lands about 14 s after dispatch).  Keep watching.
  operator-stop  the sentinel says ``interrupted`` with ``stopped_by:
                 signal:*``: the runner was stopped by stop.sh or a human.
  held           a human park holds the project (``project_held_by``).

Usage:
  relaunch_guard.py --project <repo> --launcher-dir <runtime/launcher>

Prints one JSON object.  Exit 0 = relaunch allowed; 10 = alive; 11 =
operator stop; 12 = held; 2 = the question could not be answered, which the
watchdog treats as "do not relaunch".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HERE.parent.parent / "ilk-loop" / "scripts"
sys.path.insert(0, str(_LOOP_SCRIPTS))

from ilk_paths import find_plans_dir  # noqa: E402
from pid_health import ilk_pid_alive  # noqa: E402
from plan_status import project_held_by  # noqa: E402

#: Judgment call: how long a recorded launch counts as "launching" when it has
#: not yet taken run.lock.  The lock is taken before preflight, seconds after
#: dispatch; 300 s covers a slow start with a wide margin.  Wrong if a real
#: launch takes longer than this to reach the lock -- then the watchdog could
#: again relaunch over it.
LAUNCH_WINDOW_SEC = 300


def _lock_holder_alive(lock: Path) -> int | None:
    """Return the pid recorded in *lock* when it is a live ilk runner.

    Reads the holder metadata ilk_run_lock.py writes after acquiring; it
    execs the runner in place, so that pid IS the runner.  Deliberately not a
    trial flock: the runner's acquire is non-blocking (ilk_run_lock.py:61),
    so a probe that held the lock for an instant could refuse a real launch.
    ``ilk_pid_alive`` checks the command line too, so a recycled pid in stale
    metadata does not read as live.
    """
    meta = _read_json(lock)
    if meta is None:
        return None
    pid = meta.get("pid")
    if isinstance(pid, int) and pid > 0 and ilk_pid_alive(pid):
        return pid
    return None


def _epoch(raw: object) -> float | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def decide(project: Path, launcher_dir: Path, now: float | None = None) -> tuple[int, dict]:
    now = datetime.now().timestamp() if now is None else now
    sentinel = _read_json(launcher_dir / "last-exit.json") or {}

    holder = _lock_holder_alive(launcher_dir / "run.lock")
    if holder is not None:
        return 10, {"verdict": "alive",
                    "reason": f"runner pid {holder} holds run.lock"}

    launch = _read_json(launcher_dir / "last-launch.json")
    if launch is not None:
        started = _epoch(launch.get("started_at"))
        ended = _epoch(sentinel.get("ended_at"))
        if (started is not None and now - started < LAUNCH_WINDOW_SEC
                and (ended is None or started > ended)
                and _pid_alive(launch.get("pid"))):
            return 10, {"verdict": "alive",
                        "reason": (f"launched {int(now - started)}s ago "
                                   f"(pid {launch.get('pid')}), not yet locked")}

    stopped_by = str(sentinel.get("stopped_by") or "")
    if sentinel.get("state") == "interrupted" and stopped_by.startswith("signal:"):
        return 11, {"verdict": "operator-stop",
                    "reason": (f"run {sentinel.get('run_id', '?')} was stopped by "
                               f"{stopped_by} ({sentinel.get('stopped_reason', '')})")}

    plans_dir, _src = find_plans_dir(project)
    if not plans_dir:
        return 2, {"verdict": "unknown",
                   "reason": f"no plans dir resolves from {project}"}
    held = project_held_by(Path(plans_dir))
    if held is not None:
        return 12, {"verdict": "held", "held_by": held["master"],
                    "reason": f"held by {held['master']} (human park): {held['reason']}"}

    return 0, {"verdict": "ok"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--launcher-dir", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        rc, out = decide(a.project, a.launcher_dir)
    except Exception as e:  # noqa: BLE001 -- "could not tell" must not read as "ok"
        rc, out = 2, {"verdict": "unknown", "reason": f"{type(e).__name__}: {e}"}
    print(json.dumps(out))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
