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
from dataclasses import dataclass
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
