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
import sys
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
    decoder = json.JSONDecoder()
    unparseable = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Decode CONCATENATED objects, not just one per line.
        #
        # A row appended after a neighbour that omitted its trailing newline
        # lands on the same line: `{...}{...}`.  `json.loads` raises "Extra
        # data" on that and the old code skipped the line, losing BOTH rows.
        #
        # Measured 2026-09-18 on gh-resolve run 12cd8693: ship-proof.jsonl was
        # 1131 bytes holding 4 rows with only 3 newlines, and the pair it ate
        # included the sole `loop-executed` row attributing the work sub-plan.
        # Since 027291d `ship_integrity` reads this ledger, so a row lost here
        # is a false `ship_integrity_violation` — the very defect that parked
        # 17 resolver runs.
        #
        # A glued line is recoverable, not garbage.  `raw_decode` consumes one
        # object and reports where it stopped; repeat to the end of the line.
        pos, n, progressed = 0, len(line), False
        while pos < n:
            try:
                obj, end = decoder.raw_decode(line, pos)
            except (json.JSONDecodeError, ValueError):
                break
            if isinstance(obj, dict):
                records.append(obj)
            progressed = True
            pos = end
            while pos < n and line[pos] in " \t":
                pos += 1
        if not progressed:
            # AC-6 preserved: genuinely unreadable input is SKIPPED, never
            # raised — an unreadable ledger must not turn a proven ship into
            # an unproven one.  But it is announced, because a silently
            # dropped row is indistinguishable from a row never written, and
            # that ambiguity is what made the 12cd8693 loss invisible.
            unparseable += 1
    if unparseable:
        print(
            f"warning: ship-proof ledger {path}: {unparseable} unparseable "
            f"line(s) skipped; attribution for those rows is missing, which "
            f"is NOT the same as work that was never committed",
            file=sys.stderr,
        )
    return records
