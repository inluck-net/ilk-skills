#!/usr/bin/env python3
"""
Aggregate ilk-loop status across every project registered in
~/.cursor/skills/ilk-launcher/projects.json.

Two-state version: running / idle.
A future enhancement will add a `needs-review` state once gap-analysis P0-3
lands (ilk stops at staging push, expects human review of ship-report).

For each project, prints one row with:
  - project name
  - state           (running | idle)
  - plan-status     (output of loop_status.py, summarised)
  - window-pid      (PID of the spawned PowerShell window, or '-')

Exit code 0 always. Errors per project are reported in-line and do not stop
the iteration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


HOME = Path(os.path.expanduser("~"))
LAUNCHER_DIR = HOME / ".cursor" / "skills" / "ilk-launcher"
PROJECTS_JSON = LAUNCHER_DIR / "projects.json"
LOOP_STATUS_SCRIPT = HOME / ".cursor" / "skills" / "ilk-loop" / "scripts" / "loop_status.py"

_ILK_PATHS_DIR = HOME / ".cursor" / "skills" / "ilk-loop" / "scripts"
if _ILK_PATHS_DIR.is_dir():
    sys.path.insert(0, str(_ILK_PATHS_DIR))
try:
    from ilk_paths import external_launcher_dir, project_key  # type: ignore
except ImportError:
    external_launcher_dir = None  # type: ignore
    project_key = None  # type: ignore

from pid_health import pid_alive  # type: ignore


def _get_pid_file_path(project_path: Path) -> Path | None:
    if external_launcher_dir is None or project_key is None:
        return None
    key = project_key(project_path)
    return external_launcher_dir(key) / "running.pid"


def read_pid_file(project_path: Path) -> int | None:
    pid_file = _get_pid_file_path(project_path)
    if pid_file is None:
        return None
    if not pid_file.exists():
        return None
    try:
        raw = pid_file.read_text(encoding="ascii").strip()
        return int(raw) if raw else None
    except (ValueError, OSError):
        return None


def run_loop_status(project_path: Path) -> str:
    """Run loop_status.py against the project. Return a one-line summary."""
    if not LOOP_STATUS_SCRIPT.exists():
        return f"<loop_status.py missing at {LOOP_STATUS_SCRIPT}>"
    try:
        proc = subprocess.run(
            [sys.executable, str(LOOP_STATUS_SCRIPT)],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "<loop_status timed out>"
    except OSError as e:
        return f"<loop_status error: {e}>"

    output = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode == 0:
        return "all sub-plans shipped"
    if proc.returncode == 2:
        return "<no MASTER-*.md plans found>"

    # loop_status.py prints a "Next: <slug.md>  (status=..., step=N/M)" line
    # when there is a pending sub-plan. That single line is the entire summary
    # we want.
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("next:"):
            payload = stripped[len("next:"):].strip()
            payload = payload.replace(".md", "")
            return payload[:80]

    # fallback: first non-empty line
    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:80]
    return f"<loop_status exit {proc.returncode}, no output>"


def main() -> int:
    if not PROJECTS_JSON.exists():
        print(f"projects.json not found at {PROJECTS_JSON}", file=sys.stderr)
        print("Create it with at least one entry. See SKILL.md for the schema.", file=sys.stderr)
        return 0

    try:
        data = json.loads(PROJECTS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"projects.json is not valid JSON: {e}", file=sys.stderr)
        return 0

    projects = data.get("projects", [])
    if not projects:
        print("projects.json has no entries.")
        return 0

    rows = []
    for entry in projects:
        name = str(entry.get("name", "?"))
        raw_path = entry.get("path", "")
        path = Path(raw_path)
        if not path.exists():
            rows.append((name, "missing", f"<path does not exist: {raw_path}>", "-"))
            continue

        pid = read_pid_file(path)
        if pid is not None and pid_alive(pid):
            state = "running"
            pid_disp = str(pid)
        else:
            if pid is not None:
                stale = _get_pid_file_path(path)
                if stale:
                    try:
                        stale.unlink()
                    except OSError:
                        pass
            state = "idle"
            pid_disp = "-"

        plan_status = run_loop_status(path)
        rows.append((name, state, plan_status, pid_disp))

    # render
    name_w = max(len("project"), max(len(r[0]) for r in rows))
    state_w = max(len("state"), max(len(r[1]) for r in rows))
    plan_w = max(len("plan-status"), max(len(r[2]) for r in rows))
    pid_w = max(len("window-pid"), max(len(r[3]) for r in rows))

    header = (
        f"{'project':<{name_w}}  "
        f"{'state':<{state_w}}  "
        f"{'plan-status':<{plan_w}}  "
        f"{'window-pid':<{pid_w}}"
    )
    print(header)
    print("-" * len(header))
    for name, state, plan_status, pid_disp in rows:
        print(
            f"{name:<{name_w}}  "
            f"{state:<{state_w}}  "
            f"{plan_status:<{plan_w}}  "
            f"{pid_disp:<{pid_w}}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
