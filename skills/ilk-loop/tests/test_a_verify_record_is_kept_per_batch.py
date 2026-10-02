"""Red-first pins: a verify record is kept per batch.

Sub-plan: a-verify-record-is-kept-per-batch.

The batch_gate module writes one record per project at
``runtime/batch-gate.json``.  Each verify overwrites it.  When batch X
writes its record and then batch Y writes its own, X's sub-plans can no
longer audit fresh against X's record — the file now holds Y's verdict.

On 2026-10-01 a later batch's gate overwrote 10-01's record and blocked
that release.  gh-resolve backed up 10-01b, 10-01c and 10-01d by hand.

AC-1: batch X writes its record, then batch Y writes its own ⇒ X's
      sub-plans still audit fresh against X's record.
AC-3: ``phase1_verify`` for batch X reads X's record.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_record(batch: str, **overrides) -> dict:
    """Build a minimal valid record dict for a given batch."""
    base = {
        "verdict": "pass",
        "head_sha": "a" * 40,
        "invocation": "python3 -m pytest -q",
        "timestamp": "2026-10-01T10:00:00+08:00",
        "tree_sha": "b" * 40,
        "writer": "batch_gate.py",
    }
    base.update(overrides)
    return base


# ── AC-1: per-batch records survive each other ──────────────────────────────

class TestAC1PerBatchRecordSurvivesOverwrite:
    """Batch X's record must survive batch Y writing its own."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_two_batches_keep_separate_records(self, tmp_path: Path) -> None:
        """AC-1: batch X writes, batch Y writes, X's record is still there."""
        from batch_gate import BatchGateRecord, read_record, write_record

        runtime = tmp_path / "runtime"

        # Batch X writes its record.
        rec_x = BatchGateRecord(**_make_record("batch-x"))
        write_record(rec_x, runtime)

        # Batch Y writes its record — same path today, overwrites X.
        rec_y = BatchGateRecord(**_make_record("batch-y", head_sha="f" * 40))
        write_record(rec_y, runtime)

        # AC-1: X's record must still be readable.
        # Today this fails: read_record returns Y's record for any reader
        # that resolves the path without knowing which batch it belongs to.
        loaded_x = read_record(runtime, batch="batch-x")
        assert loaded_x is not None, (
            "batch X's record was overwritten by batch Y"
        )
        assert loaded_x.head_sha == "a" * 40, (
            "batch X's record returned Y's head_sha"
        )

    def test_legacy_fallback_when_no_per_batch_record(self, tmp_path: Path) -> None:
        """AC-2: no per-batch record, legacy file present ⇒ legacy behaviour."""
        from batch_gate import BatchGateRecord, read_record, write_record

        runtime = tmp_path / "runtime"

        # Write only the legacy single-file record.
        rec = BatchGateRecord(**_make_record("legacy"))
        write_record(rec, runtime)

        # read_record without a batch key falls back to legacy.
        loaded = read_record(runtime)
        assert loaded is not None
        assert loaded.verdict == "pass"


# ── AC-3: phase1_verify reads the batch's own record ────────────────────────

class TestAC3Phase1ReadsBatchRecord:
    """phase1_verify for batch X must read X's record, not Y's."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_phase1_reads_own_batch_record(self, tmp_path: Path) -> None:
        """AC-3: phase1_verify resolves the record for the sub-plan's batch."""
        from batch_gate import BatchGateRecord, write_record

        runtime = tmp_path / "runtime"

        # Batch X writes its record.
        rec_x = BatchGateRecord(**_make_record("batch-x"))
        write_record(rec_x, runtime)

        # Batch Y writes its record, overwriting X.
        rec_y = BatchGateRecord(**_make_record("batch-y", head_sha="f" * 40))
        write_record(rec_y, runtime)

        # phase1_verify for batch X should resolve X's record.
        # Today this is impossible: there's no per-batch path resolution.
        # The fix will add a batch_record_path(runtime_dir, batch) helper.
        from batch_gate import batch_record_path

        x_path = batch_record_path(runtime, "batch-x")
        assert x_path.is_file(), (
            "batch X's record file does not exist at the per-batch path"
        )

        data = json.loads(x_path.read_text(encoding="utf-8"))
        assert data["head_sha"] == "a" * 40, (
            "batch X's per-batch record has the wrong head_sha"
        )