"""ilk_watch — canonical loop-watch helper for ilk-loop projects.

Given a project path, resolves the **correct** active loop log and sentinel,
and prints:
  (a) a one-shot status token in {running, all-shipped, blocked, idle}
  (b) the resolved log path (newest run dir or .ilk-loop.log)
  (c) a ready-to-Monitor tail command for the right log

Stdlib only.  Injectable for tests: pass ``_read_sentinel``, ``_pid_alive``,
and ``_resolve_queue`` overrides to ``ProjectWatch.__init__``.

AC-1: status token matches sentinel/queue state (tested via fixtures).
AC-2: log path is the correct one (newest run dir / .ilk-loop.log), not a guess.
AC-3: on all-shipped, says all-shipped + "do not relaunch" hint.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Literal

# ── ilike-paths import (sibling module) ─────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
if _SCRIPTS_DIR.is_dir():
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from ilk_paths import (  # type: ignore
        external_logs_dir,
        external_runtime_dir,
        find_project_root,
        jsonl_summary_path,
        project_key,
    )
except ImportError:
    # Graceful degradation when ilk_paths is not on sys.path.
    external_logs_dir = None  # type: ignore
    external_runtime_dir = None  # type: ignore
    find_project_root = None  # type: ignore
    jsonl_summary_path = None  # type: ignore
    project_key = None  # type: ignore

try:
    from status_all import LIVE_SENTINEL_STATES, pid_alive  # type: ignore
except ImportError:
    LIVE_SENTINEL_STATES = {"running"}

    def pid_alive(pid: int) -> bool:  # type: ignore[misc]
        """Fallback PID liveness check (POSIX-only)."""
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False


StatusToken = Literal["running", "all-shipped", "blocked", "idle"]


def _read_sentinel_default(runtime_dir: Path | None) -> dict | None:
    """Read last-exit.json from *runtime_dir*.  Returns parsed dict or None."""
    if runtime_dir is None:
        return None
    f = runtime_dir / "last-exit.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_queue_default(project_path: Path) -> dict:
    """Run loop_status.py --json and return the parsed dict.

    Falls back to a minimal dict on error so the watch helper never crashes.
    """
    loop_status = _SCRIPTS_DIR.parent.parent / "ilk-loop" / "scripts" / "loop_status.py"
    if not loop_status.is_file():
        return {"queue_exit": 2, "subplans": [], "next": None}
    import subprocess

    try:
        result = subprocess.run(
            [sys.executable, str(loop_status), "--json"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return {"queue_exit": 2, "subplans": [], "next": None}


def _find_newest_run_dir(logs_dir: Path) -> Path | None:
    """Return the most recently modified run directory under ``logs/runs/``."""
    runs_dir = logs_dir / "runs"
    if not runs_dir.is_dir():
        return None
    candidates = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


class ProjectWatch:
    """Resolve loop state for a single project.

    Parameters
    ----------
    project_path : Path
        Root of the project (must contain .git or be covered by .ilk-meta.json).
    _read_sentinel : callable, optional
        Injectable: ``(runtime_dir: Path) -> dict | None``.
    _pid_alive : callable, optional
        Injectable: ``(pid: int) -> bool``.
    _resolve_queue : callable, optional
        Injectable: ``(project_path: Path) -> dict`` (loop_status --json output).
    """

    def __init__(
        self,
        project_path: Path,
        *,
        _read_sentinel: Callable[[Path], dict | None] | None = None,
        _pid_alive: Callable[[int], bool] | None = None,
        _resolve_queue: Callable[[Path], dict] | None = None,
    ) -> None:
        self.project_path = Path(project_path).resolve()
        self._read_sentinel = _read_sentinel or _read_sentinel_default
        self._pid_alive = _pid_alive or pid_alive
        self._resolve_queue = _resolve_queue or _resolve_queue_default

    # ── derived paths ───────────────────────────────────────────────────

    @property
    def _key(self) -> str | None:
        if project_key is None or find_project_root is None:
            return None
        root, _kind = find_project_root(self.project_path)
        if root is None:
            return None
        return project_key(root)

    @property
    def _runtime_dir(self) -> Path | None:
        key = self._key
        if key is None or external_runtime_dir is None:
            return None
        return external_runtime_dir(key)

    @property
    def _logs_dir(self) -> Path | None:
        key = self._key
        if key is None or external_logs_dir is None:
            return None
        return external_logs_dir(key)

    # ── core resolution ─────────────────────────────────────────────────

    def resolve(self) -> dict:
        """Return ``{"status": StatusToken, "log_path": str, "hint": str}``.

        ``hint`` is non-empty when extra context is useful (e.g. "do not
        relaunch" for all-shipped, or the dead-PID explanation for blocked).
        """
        sentinel = self._read_current_sentinel()
        queue = self._resolve_queue(self.project_path)

        # all-shipped check: loop_status exit 0 + no pending work.
        all_shipped = queue.get("queue_exit", 2) == 0
        non_shipped = [sp for sp in queue.get("subplans", []) if sp.get("status") != "shipped"]
        if all_shipped and not non_shipped:
            return {
                "status": "all-shipped",
                "log_path": str(self._resolve_log_path()),
                "hint": "All sub-plans shipped. Do NOT relaunch — nothing to run.",
            }

        # running / blocked / idle from sentinel.
        if sentinel is not None:
            state = sentinel.get("state", "unknown")
            pid = sentinel.get("pid", 0)
            if not isinstance(pid, (int, float)):
                pid = 0
            pid = int(pid)
            alive = state in LIVE_SENTINEL_STATES and self._pid_alive(pid)

            if alive:
                return {
                    "status": "running",
                    "log_path": str(self._resolve_log_path()),
                    "hint": "",
                }
            elif state in LIVE_SENTINEL_STATES and not alive:
                # Sentinel says running but PID is dead → stale/blocked.
                return {
                    "status": "blocked",
                    "log_path": str(self._resolve_log_path()),
                    "hint": f"Sentinel state={state!r} but PID {pid} is dead (stale).",
                }
            else:
                # Terminal state (shipped, local_checks_failed, interrupted, …)
                # with non-shipped sub-plans → idle (run ended, work remains).
                return {
                    "status": "idle",
                    "log_path": str(self._resolve_log_path()),
                    "hint": f"Sentinel state={state!r} (terminal). Run ended; work remains.",
                }

        # No sentinel at all.
        return {
            "status": "idle",
            "log_path": str(self._resolve_log_path()),
            "hint": "No sentinel found.",
        }

    def _read_current_sentinel(self) -> dict | None:
        """Read sentinel from the runtime dir, or None if unavailable.

        Always delegates to ``self._read_sentinel`` so injectable fakes in
        tests can return a value even when ``_runtime_dir`` is None (the
        default implementation handles None gracefully).
        """
        return self._read_sentinel(self._runtime_dir)

    def _resolve_log_path(self) -> Path:
        """Pick the best log path: newest run dir > .ilk-loop.log > logs dir."""
        logs = self._logs_dir
        if logs is not None:
            newest = _find_newest_run_dir(logs)
            if newest is not None:
                return newest
            if jsonl_summary_path is not None:
                summary = jsonl_summary_path(self._key or "")
                if summary.is_file():
                    return summary
            return logs
        return self.project_path

    def tail_command(self) -> str:
        """Return a ready-to-paste ``tail -f`` / ``Get-Content`` command."""
        log_path = self.resolve()["log_path"]
        if sys.platform == "win32":
            return f'Get-Content -Path "{log_path}" -Wait -Tail 50'
        return f'tail -f "{log_path}"'


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Canonical loop-watch helper: print loop status + tail command."
    )
    ap.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="Project root (default: cwd)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON",
    )
    ap.add_argument(
        "--tail-only",
        action="store_true",
        help="Print only the tail command (for piping into Monitor)",
    )
    args = ap.parse_args()

    watch = ProjectWatch(args.project)
    result = watch.resolve()

    if args.tail_only:
        print(watch.tail_command())
        return 0

    if args.as_json:
        out = {**result, "tail_command": watch.tail_command()}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    # Human-readable one-shot output.
    print(f"status: {result['status']}")
    print(f"log:    {result['log_path']}")
    if result["hint"]:
        print(f"hint:   {result['hint']}")
    print(f"tail:   {watch.tail_command()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
