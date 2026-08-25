"""batch_gate — batch-end gate verdict persistence and append freeze.

Persists the result of running the project's declared test suite once at
batch end.  The runner calls this module; it does not own the suite
invocation or the background/poll lifecycle.

Also enforces the append-freeze rule: once the batch gate starts running,
no new sub-plan may be appended to that batch (the verdict would not
cover it).  Appends during the freeze are deferred to the next batch.

Record format (JSON):
{
  "verdict":    "pass" | "fail" | "not_configured" | "error",
  "head_sha":   "<40-char hex>",
  "invocation": "<the command that was run>",
  "timestamp":  "<ISO-8601>"
}

Running-marker format (JSON):
{
  "pid":        <int>,
  "started_at": "<ISO-8601>"
}

Contract governed by detached-component-contracts.md — this module is a
new *writer* of runtime state.  See the "Adding a new reader or writer"
checklist there.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


REQUIRED_FIELDS = ("verdict", "head_sha", "invocation", "timestamp")


@dataclass(frozen=True)
class BatchGateRecord:
    """A validated batch-gate verdict record."""
    verdict: str
    head_sha: str
    invocation: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "head_sha": self.head_sha,
            "invocation": self.invocation,
            "timestamp": self.timestamp,
        }


def record_path(runtime_dir: Path) -> Path:
    """Return the path where the batch-gate record lives."""
    return runtime_dir / "batch-gate.json"


def write_record(record: BatchGateRecord, runtime_dir: Path) -> Path:
    """Write a batch-gate record to disk.  Returns the path written."""
    p = record_path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record.to_dict(), indent=2) + "\n",
                 encoding="utf-8")
    return p


def read_record(runtime_dir: Path) -> Optional[BatchGateRecord]:
    """Read and validate a batch-gate record.

    Returns None when the file is missing, has missing fields, or is
    unreadable.  A record missing any REQUIRED_FIELDS is invalid — the
    reader must say so rather than assume a pass.
    """
    p = record_path(runtime_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for field in REQUIRED_FIELDS:
        if field not in data:
            return None
    return BatchGateRecord(
        verdict=data["verdict"],
        head_sha=data["head_sha"],
        invocation=data["invocation"],
        timestamp=data["timestamp"],
    )


# ── record validation ───────────────────────────────────────────────────────

def validate_record(
    record_path: Path,
    expected_head_sha: str,
    expected_invocation: str,
) -> str:
    """Validate a batch-gate record against the project as it is now.

    Returns one of five outcome words:
      fresh            — head_sha and invocation both match
      stale_head       — head_sha differs from current HEAD
      stale_invocation — invocation differs from what ship.suite builds
      incomplete       — a required field is missing
      absent           — no record file exists
    """
    if not record_path.is_file():
        return "absent"
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "incomplete"
    if not isinstance(data, dict):
        return "incomplete"
    for field in REQUIRED_FIELDS:
        if field not in data:
            return "incomplete"
    if data["head_sha"] != expected_head_sha:
        return "stale_head"
    if data["invocation"] != expected_invocation:
        return "stale_invocation"
    return "fresh"


def validate_record_detail(
    record_path: Path,
    expected_head_sha: str,
    expected_invocation: str,
) -> str:
    """Validate and return a human-readable detail string.

    Same logic as validate_record, but the result names both sides of
    every mismatch so a reader can see what changed.
    """
    if not record_path.is_file():
        return "absent: no batch-gate record found"
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "incomplete: unreadable record"
    if not isinstance(data, dict):
        return "incomplete: record is not a JSON object"
    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        return f"incomplete: missing field(s): {', '.join(missing)}"
    if data["head_sha"] != expected_head_sha:
        return (
            f"stale_head: record sha {data['head_sha'][:7]} "
            f"!= current HEAD {expected_head_sha[:7]}"
        )
    if data["invocation"] != expected_invocation:
        return (
            f"stale_invocation: record invocation "
            f"'{data['invocation']}' != expected '{expected_invocation}'"
        )
    return "fresh"


# ── re-entry guard ───────────────────────────────────────────────────────────

def _gate_lock_path(runtime_dir: Path) -> Path:
    """Marker file that prevents re-entry."""
    return runtime_dir / "batch-gate.running"


def _acquire_gate_lock(runtime_dir: Path) -> bool:
    """Try to acquire the re-entry guard.  Returns True if acquired.

    The marker now carries pid and started_at so the append-freeze check
    can verify the gate process is still alive (AC-5: stale sentinel).
    """
    p = _gate_lock_path(runtime_dir)
    if p.is_file():
        return False
    runtime_dir.mkdir(parents=True, exist_ok=True)
    marker = {"pid": os.getpid(), "started_at": _now_iso()}
    p.write_text(json.dumps(marker), encoding="utf-8")
    return True


def _release_gate_lock(runtime_dir: Path) -> None:
    """Release the re-entry guard.  Idempotent."""
    p = _gate_lock_path(runtime_dir)
    p.unlink(missing_ok=True)


# ── append-freeze check ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class GateRunningState:
    """Whether the batch gate is currently running."""
    running: bool
    pid: Optional[int] = None
    started_at: Optional[str] = None


def read_gate_running_state(runtime_dir: Path) -> GateRunningState:
    """Check whether the batch gate is running.

    Reads the marker file and probes the pid for liveness.  A marker
    whose pid is gone is treated as stale (not running) — the same
    stale-sentinel class that kept dead watchdogs alive for 15 days.
    """
    p = _gate_lock_path(runtime_dir)
    if not p.is_file():
        return GateRunningState(running=False)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupt marker → treat as stale
        return GateRunningState(running=False)

    pid = data.get("pid") if isinstance(data, dict) else None
    started_at = data.get("started_at") if isinstance(data, dict) else None

    if pid is None:
        # Legacy format (bare timestamp) — no pid to check, treat as stale
        return GateRunningState(running=False)

    # Probe the pid for liveness (AC-5)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        # Pid is gone → stale marker
        return GateRunningState(running=False)
    except PermissionError:
        # Process exists but owned by another user → still alive
        pass

    return GateRunningState(running=True, pid=pid, started_at=started_at)


# ── append-freeze ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AppendResult:
    """Result of an attempt to append a sub-plan to a batch."""
    status: str            # "appended" | "deferred" | "rejected"
    message: str           # human-readable explanation
    deferred_to: Optional[str] = None  # next batch identifier, if deferred


def append_subplan_if_allowed(
    plans_dir: Path,
    runtime_dir: Path,
    slug: str,
    body: str,
    filename_prefix: str = "2026-08-25",
) -> AppendResult:
    """Append a sub-plan to the current batch, or defer if the gate is running.

    This is the real append path — the same entry point /ilk-plan workflow #3
    uses.  AC-1b requires the freeze to be exercised through here, not just
    in a predicate called in isolation.

    AC-1:  refused while running, with message naming batch, start time, reason.
    AC-2:  defers rather than discards — caller can distinguish "deferred".
    AC-3:  appending before the gate starts works exactly as today.
    AC-4:  freeze lifts once the gate completes (marker removed).
    AC-5:  stale running-state (pid gone) does not freeze forever.
    """
    state = read_gate_running_state(runtime_dir)

    if state.running:
        # AC-1: refuse with a message naming the batch, start time, reason
        msg = (
            f"Batch gate is running (pid={state.pid}, "
            f"started_at={state.started_at}). "
            f"Sub-plan '{slug}' cannot be appended — the verdict would not "
            f"cover it.  Deferred to the next batch."
        )
        return AppendResult(
            status="deferred",
            message=msg,
            deferred_to="next-batch",
        )

    # AC-3: no freeze → append as today
    plans_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{filename_prefix}-{slug}.md"
    target = plans_dir / filename
    target.write_text(body, encoding="utf-8")

    return AppendResult(
        status="appended",
        message=f"Sub-plan '{slug}' appended as {filename}.",
    )


# ── git helper ───────────────────────────────────────────────────────────────

def _git_head_sha(project_path: Path) -> str:
    """Capture HEAD sha.  Returns 'unknown' on failure."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if len(out) == 40 and all(c in "0123456789abcdef" for c in out):
            return out
    except (subprocess.CalledProcessError, OSError):
        pass
    return "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


# ── skill-root resolution ────────────────────────────────────────────────────

def _skill_root() -> Path:
    """Resolve the skill root directory.

    Walks up from this file to find the ``skills/`` directory that
    contains ``ilk-loop``, ``ilk-ship``, etc.  This works both in the
    project tree and in the installed tree.
    """
    cur = Path(__file__).resolve().parent  # scripts/
    for _ in range(6):
        if cur.name == "scripts" and cur.parent.name.startswith("ilk-"):
            candidate = cur.parent.parent  # skills/
            if (candidate / "ilk-ship" / "scripts" / "ship_config.py").is_file():
                return candidate
        cur = cur.parent
        if cur == cur.parent:
            break
    raise FileNotFoundError(
        "Cannot resolve skill root from batch_gate.py — "
        "expected skills/ilk-ship/scripts/ship_config.py to exist."
    )


# ── main entry point ─────────────────────────────────────────────────────────

def run_batch_gate(
    project_path: Path,
    runtime_dir: Path,
    *,
    _wait_helper: Optional[Path] = None,
    _poll_timeout: int = 600,
) -> Optional[BatchGateRecord]:
    """Run the batch-end gate: resolve suite, execute, persist verdict.

    Returns the record written, or None if re-entry was detected (the
    gate already ran).  The runner calls this at the ALL-SHIPPED point.

    AC-1: runs exactly once (guarded by a marker file).
    AC-5: missing/unrunnable/failing suite → fail, never pass.
    """
    # ── re-entry guard (AC-1) ────────────────────────────────────────────
    if not _acquire_gate_lock(runtime_dir):
        return None  # already ran

    try:
        return _run_gate_inner(
            project_path, runtime_dir,
            _wait_helper=_wait_helper,
            _poll_timeout=_poll_timeout,
        )
    except Exception as exc:
        # AC-6: gate code error → record error, never hang
        rec = BatchGateRecord(
            verdict="error",
            head_sha=_git_head_sha(project_path),
            invocation="<gate-code-error>",
            timestamp=_now_iso(),
        )
        write_record(rec, runtime_dir)
        return rec
    # Note: lock persists — re-entry guard is permanent per batch.


def _run_gate_inner(
    project_path: Path,
    runtime_dir: Path,
    *,
    _wait_helper: Optional[Path] = None,
    _poll_timeout: int = 600,
) -> BatchGateRecord:
    """Core gate logic — separated for clean error wrapping."""
    # ── resolve suite invocation ─────────────────────────────────────────
    # Import here to avoid hard dependency at module level
    sys_path_backup = list(__import__("sys").path)
    try:
        __import__("sys").path.insert(
            0, str(_skill_root() / "ilk-ship" / "scripts"))
        from ship_config import NotConfigured, load_ship_config  # type: ignore[import-untyped]
    finally:
        __import__("sys").path[:] = sys_path_backup

    config = load_ship_config(project_path)
    head_sha = _git_head_sha(project_path)

    if isinstance(config, NotConfigured):
        # AC-5: surface the config path so the runner reports what did not run.
        if config.resolved_path:
            inv = f"not_configured: no ship block in {config.resolved_path}"
        else:
            inv = "not_configured: no .ilk-launch.json found"
        return BatchGateRecord(
            verdict="not_configured",
            head_sha=head_sha,
            invocation=inv,
            timestamp=_now_iso(),
        )

    invocation = config.ship["suite"]["command"]
    flags = config.ship["suite"].get("flags", [])
    full_cmd = invocation if not flags else f"{invocation} {' '.join(flags)}"

    # ── run the suite backgrounded + polled ──────────────────────────────
    output_file = runtime_dir / "batch-gate-suite.output"
    wait = _wait_helper or (_skill_root() / "ilk-loop" / "scripts" /
                            "wait_for_background_output.sh")

    proc = subprocess.Popen(
        full_cmd,
        shell=True,
        stdout=open(output_file, "w"),  # noqa: SIM115
        stderr=subprocess.STDOUT,
        cwd=project_path,
    )

    # Poll with the shipped helper (AC-2: never foreground)
    try:
        result = subprocess.run(
            ["bash", str(wait), str(output_file),
             "--timeout", str(_poll_timeout)],
            capture_output=True, text=True,
            timeout=_poll_timeout + 30,
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        exit_code = 125  # wait_for_background_output's inconclusive code
    finally:
        proc.kill()  # ensure no zombie

    if exit_code == 0:
        verdict = "pass"
    else:
        verdict = "fail"

    return BatchGateRecord(
        verdict=verdict,
        head_sha=head_sha,
        invocation=full_cmd,
        timestamp=_now_iso(),
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Run the batch-end gate and persist the verdict.",
    )
    parser.add_argument("--project", type=Path, required=True,
                        help="Project root path")
    parser.add_argument("--runtime-dir", type=Path, default=None,
                        help="Runtime dir (default: resolve from ilk_paths)")
    parser.add_argument("--run", action="store_true", required=True,
                        help="Actually run the gate")
    parser.add_argument("--poll-timeout", type=int, default=600,
                        help="Timeout for polling the background suite (seconds)")
    args = parser.parse_args()

    if not args.run:
        parser.print_help()
        return

    project = args.project.resolve()
    if args.runtime_dir:
        runtime = args.runtime_dir.resolve()
    else:
        from ilk_paths import external_runtime_dir, resolve_project_key  # type: ignore[import-untyped]
        key = resolve_project_key(project)
        if key is None:
            print("[batch-gate] ERROR: cannot resolve project key", file=__import__("sys").stderr)
            raise SystemExit(1)
        runtime = external_runtime_dir(key)

    rec = run_batch_gate(project, runtime, _poll_timeout=args.poll_timeout)
    if rec is None:
        print("[batch-gate] Re-entry detected — gate already ran. Skipping.")
    else:
        print(f"[batch-gate] verdict={rec.verdict} head_sha={rec.head_sha[:12]}")


if __name__ == "__main__":
    main()
