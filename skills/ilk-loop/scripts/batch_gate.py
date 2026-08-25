"""batch_gate — batch-end gate verdict persistence.

STUB — tests are red-first.  Implementations arrive in step 1.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


REQUIRED_FIELDS = ("verdict", "head_sha", "invocation", "timestamp")


class BatchGateRecord:
    """Stub — not yet implemented."""
    pass


def record_path(runtime_dir: Path) -> Path:
    raise NotImplementedError


def write_record(record, runtime_dir: Path) -> Path:
    raise NotImplementedError


def read_record(runtime_dir: Path) -> Optional[dict]:
    raise NotImplementedError
