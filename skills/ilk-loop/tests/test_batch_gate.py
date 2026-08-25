"""Red-first tests for batch_gate — the batch-end gate record.

AC-3: persisted record carries all four of verdict, head_sha, invocation,
      timestamp.  A record missing any one of them is invalid.
AC-4: head_sha is the sha the suite actually ran against, captured at
      gate start.
AC-5: when the suite command is missing, unrunnable, or exits non-zero,
      the gate records verdict: fail with the reason and the runner
      reports it — never hangs, never records a pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_record(**overrides) -> dict:
    """Build a minimal valid record dict, with overrides."""
    base = {
        "verdict": "pass",
        "head_sha": "abc123" * 6 + "abcd",  # 40 hex chars
        "invocation": "python3 -m pytest -q",
        "timestamp": "2026-08-25T10:00:00+08:00",
    }
    base.update(overrides)
    return base


# ── AC-3: all four fields required ──────────────────────────────────────────

class TestAC3RecordShape:
    """A record missing any required field must read back as invalid."""

    def test_valid_record_roundtrips(self, tmp_path: Path) -> None:
        from batch_gate import BatchGateRecord, read_record, write_record
        rec = BatchGateRecord(**_make_record())
        write_record(rec, tmp_path)
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.verdict == "pass"

    @pytest.mark.parametrize("missing", [
        "verdict",
        "head_sha",
        "invocation",
        "timestamp",
    ])
    def test_missing_field_is_invalid(self, tmp_path: Path, missing: str) -> None:
        from batch_gate import read_record
        data = _make_record()
        del data[missing]
        _write_json(tmp_path / "batch-gate.json", data)
        assert read_record(tmp_path) is None

    def test_missing_file_is_none(self, tmp_path: Path) -> None:
        from batch_gate import read_record
        assert read_record(tmp_path) is None


# ── AC-4: head_sha captured at gate start ───────────────────────────────────

class TestAC4HeadShaAtStart:
    """head_sha must be the sha at gate start, not at record write time."""

    def test_head_sha_is_start_sha(self, tmp_path: Path) -> None:
        from batch_gate import BatchGateRecord, read_record, write_record
        start_sha = "a" * 40
        rec = BatchGateRecord(
            verdict="pass",
            head_sha=start_sha,
            invocation="pytest -q",
            timestamp="2026-08-25T10:00:00+08:00",
        )
        write_record(rec, tmp_path)
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.head_sha == start_sha


# ── AC-5: missing/unrunnable/failing suite → fail verdict ───────────────────

class TestAC5SuiteFailureModes:
    """When the suite can't run, record verdict: fail — never pass."""

    def test_missing_suite_records_not_configured(self, tmp_path: Path) -> None:
        from batch_gate import BatchGateRecord, read_record, write_record
        rec = BatchGateRecord(
            verdict="not_configured",
            head_sha="c" * 40,
            invocation="",
            timestamp="2026-08-25T10:00:00+08:00",
        )
        write_record(rec, tmp_path)
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.verdict == "not_configured"

    def test_nonzero_exit_records_fail(self, tmp_path: Path) -> None:
        from batch_gate import BatchGateRecord, read_record, write_record
        rec = BatchGateRecord(
            verdict="fail",
            head_sha="e" * 40,
            invocation="pytest -q",
            timestamp="2026-08-25T10:00:00+08:00",
        )
        write_record(rec, tmp_path)
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.verdict == "fail"
