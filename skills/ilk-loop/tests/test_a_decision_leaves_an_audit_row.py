"""Red-first pins for the audit and event log contract.

All tests are xfail(strict=True) until ``ilk_audit`` exists. Each test
imports the module inside its body so the file can collect before the
module is written.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import textwrap
from datetime import date, timezone
from pathlib import Path

import pytest


# ── AC-1: write_audit then read_audit returns the exact row ──────────────


@pytest.mark.xfail(
    strict=True,
    reason="ilk_audit module does not exist yet; red-first pin for AC-1",
)
def test_write_audit_returns_row_with_expected_fields(tmp_path: Path):
    from skills.ilk_loop.scripts.ilk_audit import write_audit, read_audit

    row = write_audit("triage-decided", "k", root=tmp_path, action="amend")
    assert isinstance(row, dict)
    # Required fields from the contract
    assert row["kind"] == "triage-decided"
    assert row["project"] == "k"
    assert row["action"] == "amend"
    assert "ts" in row, "row must carry an ISO 8601 timestamp"
    assert "host" in row, "row must carry the hostname"

    rows = read_audit(root=tmp_path)
    assert len(rows) == 1, "exactly one row after one write"
    assert rows[0] == row, "read_audit must return the same dict"


# ── AC-2: unknown kind or event raises ValueError, writes nothing ────────


@pytest.mark.xfail(
    strict=True,
    reason="ilk_audit module does not exist yet; red-first pin for AC-2",
)
def test_unknown_audit_kind_raises_value_error(tmp_path: Path):
    from skills.ilk_loop.scripts.ilk_audit import write_audit, read_audit

    with pytest.raises(ValueError):
        write_audit("not-a-kind", "k", root=tmp_path)

    # Must not have written anything
    rows = read_audit(root=tmp_path)
    assert rows == [], "unknown kind must not produce a row"


@pytest.mark.xfail(
    strict=True,
    reason="ilk_audit module does not exist yet; red-first pin for AC-2",
)
def test_unknown_event_type_raises_value_error(tmp_path: Path):
    from skills.ilk_loop.scripts.ilk_audit import write_event, read_audit

    with pytest.raises(ValueError):
        write_event("not-an-event", "k", root=tmp_path)

    # events.jsonl must be absent or empty
    events_file = tmp_path / "events.jsonl"
    if events_file.exists():
        assert events_file.read_text() == "", "unknown event must not produce a row"


# ── AC-3: corrupt line raises AuditReadError naming the line number ──────


@pytest.mark.xfail(
    strict=True,
    reason="ilk_audit module does not exist yet; red-first pin for AC-3",
)
def test_corrupt_line_raises_audit_read_error(tmp_path: Path):
    from skills.ilk_loop.scripts.ilk_audit import read_audit, AuditReadError

    # Build a day file with one good line and one corrupt line
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    today = date.today().isoformat()
    day_file = audit_dir / f"{today}.jsonl"

    good = json.dumps({"ts": "2026-01-01T00:00:00+08:00", "host": "h",
                        "kind": "triage-decided", "project": "k"})
    bad = "this is not json\n"
    day_file.write_text(good + "\n" + bad, encoding="utf-8")

    with pytest.raises(AuditReadError) as exc_info:
        read_audit(root=tmp_path)

    err = exc_info.value
    assert "2" in str(err), "error must name line number 2"
    assert str(day_file) in str(err), "error must name the file"


# ── AC-4: 8 processes × 50 rows = 400 parseable lines ───────────────────


def _writer_task(args: tuple) -> None:
    """Worker function for the concurrent-write test."""
    root_str, project, proc_id = args
    # Each process imports independently
    from skills.ilk_loop.scripts.ilk_audit import write_audit

    for i in range(50):
        write_audit(
            "triage-decided",
            project,
            root=Path(root_str),
            proc=proc_id,
            seq=i,
        )


@pytest.mark.xfail(
    strict=True,
    reason="ilk_audit module does not exist yet; red-first pin for AC-4",
)
def test_concurrent_writes_produce_all_lines(tmp_path: Path):
    from skills.ilk_loop.scripts.ilk_audit import read_audit

    n_procs = 8
    rows_per_proc = 50

    tasks = [(str(tmp_path), "k", pid) for pid in range(n_procs)]
    with multiprocessing.Pool(n_procs) as pool:
        pool.map(_writer_task, tasks)

    rows = read_audit(root=tmp_path)
    assert len(rows) == n_procs * rows_per_proc, (
        f"expected {n_procs * rows_per_proc} rows, got {len(rows)}"
    )


# ── AC-5 (control): no day file means empty list ────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="ilk_audit module does not exist yet; red-first pin for AC-5",
)
def test_missing_day_file_returns_empty_list(tmp_path: Path):
    from skills.ilk_loop.scripts.ilk_audit import read_audit

    rows = read_audit(root=tmp_path)
    assert rows == [], "no day file must return []"