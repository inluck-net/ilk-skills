#!/usr/bin/env python3
"""
ship_proof_ledger.py — write and read the ship-proof ledger sidecar.

The ledger is a JSONL file at ``<external_launcher_dir>/ship-proof.jsonl``.
Each record is one productive iteration's attribution of commits to a
sub-plan's step range.  Written by the bash runner, read by ``ship_audit.py``
when commit trailers are absent (the shared-remote case).

Contract: ``detached-component-contracts.md`` (new file contract for
``runtime/launcher/ship-proof.jsonl``).

Sub-plan: ``a-shared-remote-ship-can-be-proven`` (AC-1, AC-2, AC-5, AC-6).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_record(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON record to the ledger file.

    Compact separators (same contract as the local_checks JSONL —
    ``detached-component-contracts.md`` invariant 2b.2).  Creates the
    parent directory if it does not exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def read_records(path: Path) -> list[dict[str, Any]]:
    """Read all records from the ledger file.

    Skips unparseable lines rather than raising (AC-6 — an unreadable
    ledger must not turn a proven ship into an unproven one).  Returns
    an empty list when the file is absent, empty, or contains no valid
    records.
    """
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records
