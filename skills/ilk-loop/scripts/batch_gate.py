"""batch_gate — batch-end gate verdict persistence.

Persists the result of running the project's declared test suite once at
batch end. The runner calls this module; it does not own the suite
invocation or the background/poll lifecycle.

Record format (JSON):
{
  "verdict":    "pass" | "fail" | "not_configured" | "error",
  "head_sha":   "<40-char hex>",
  "invocation": "<the command that was run>",
  "timestamp":  "<ISO-8601>"
}

Contract governed by detached-component-contracts.md — this module is a
new *writer* of runtime state.  See the "Adding a new reader or writer"
checklist there.
"""
from __future__ import annotations

import json
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


# ── re-entry guard ───────────────────────────────────────────────────────────

def _gate_lock_path(runtime_dir: Path) -> Path:
    """Marker file that prevents re-entry."""
    return runtime_dir / "batch-gate.running"


def _acquire_gate_lock(runtime_dir: Path) -> bool:
    """Try to acquire the re-entry guard.  Returns True if acquired."""
    p = _gate_lock_path(runtime_dir)
    if p.is_file():
        return False
    runtime_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(str(time.time()), encoding="utf-8")
    return True


def _release_gate_lock(runtime_dir: Path) -> None:
    """Release the re-entry guard.  Idempotent."""
    p = _gate_lock_path(runtime_dir)
    p.unlink(missing_ok=True)


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
        return BatchGateRecord(
            verdict="not_configured",
            head_sha=head_sha,
            invocation="",
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
