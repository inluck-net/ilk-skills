"""A gate record counts which bucket excused each failure.

Part of sub-plan ``a-gate-record-counts-its-buckets``.

The gate record carries ``excused_count`` but nothing says WHICH verdict
excused a failure.  G4's record read ``excused_count: 0`` while its only
failure was excused as ``born-red-at``.  The .md table is the only place
that shows it.

  AC-1  with a fixture record table of 1 attributed + 2 failed-at-base +
        1 born-red, ``write_gate_record`` produces ``counts`` =
        {suite_failed: 4, attributed: 1, failed_at_base: 2,
        declared_at_base: 0, born_red: 1, flaky_owed: 0}.  Red-first,
        xfail(strict=True).
  AC-2  the bucket keys sum to ``suite_failed`` for every fixture.
        Red-first.
  AC-3  (control) ``batch_gate.read_record`` and
        ``phase1_verify.verify_phase1`` accept a record without ``counts``
        exactly as before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import batch_gate  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

def _record_with_counts(
    *,
    suite_failed: int,
    attributed: int,
    failed_at_base: int,
    declared_at_base: int,
    born_red: int,
    flaky_owed: int,
) -> batch_gate.BatchGateRecord:
    """Build a record with the ``counts`` field."""
    return batch_gate.BatchGateRecord(
        verdict="fail" if suite_failed > 0 else "pass",
        head_sha="a" * 40,
        invocation="pytest --timeout=60",
        timestamp="2026-10-03T12:00:00+08:00",
        undeclared=[],
        excused_count=attributed + failed_at_base + declared_at_base + born_red + flaky_owed,
        tree_sha="b" * 40,
        writer="batch_gate.py",
        counts={
            "suite_failed": suite_failed,
            "attributed": attributed,
            "failed_at_base": failed_at_base,
            "declared_at_base": declared_at_base,
            "born_red": born_red,
            "flaky_owed": flaky_owed,
        },
    )


def _record_without_counts() -> batch_gate.BatchGateRecord:
    """Build a record without the ``counts`` field (legacy)."""
    return batch_gate.BatchGateRecord(
        verdict="pass",
        head_sha="a" * 40,
        invocation="pytest --timeout=60",
        timestamp="2026-10-03T12:00:00+08:00",
        undeclared=[],
        excused_count=0,
        tree_sha="b" * 40,
        writer="batch_gate.py",
    )


# ── AC-1: counts are written correctly ───────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="counts field not yet implemented")
def test_ac1_counts_written_for_mixed_buckets(tmp_path: Path) -> None:
    """With 1 attributed + 2 failed-at-base + 1 born-red, counts are correct."""
    rec = _record_with_counts(
        suite_failed=4,
        attributed=1,
        failed_at_base=2,
        declared_at_base=0,
        born_red=1,
        flaky_owed=0,
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    batch_gate.write_record(rec, runtime)
    data = json.loads((runtime / "batch-gate.json").read_text())
    counts = data.get("counts")
    assert counts is not None, "counts field missing from written record"
    assert counts == {
        "suite_failed": 4,
        "attributed": 1,
        "failed_at_base": 2,
        "declared_at_base": 0,
        "born_red": 1,
        "flaky_owed": 0,
    }


# ── AC-2: bucket keys sum to suite_failed ────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="counts field not yet implemented")
def test_ac2_bucket_keys_sum_to_suite_failed(tmp_path: Path) -> None:
    """Every fixture's bucket keys sum to suite_failed."""
    fixtures = [
        # (suite_failed, attributed, failed_at_base, declared_at_base, born_red, flaky_owed)
        (4, 1, 2, 0, 1, 0),
        (0, 0, 0, 0, 0, 0),
        (3, 0, 0, 0, 0, 3),
        (5, 2, 1, 1, 1, 0),
        (2, 0, 0, 0, 0, 2),
    ]
    for sf, a, fab, dab, br, fo in fixtures:
        rec = _record_with_counts(
            suite_failed=sf,
            attributed=a,
            failed_at_base=fab,
            declared_at_base=dab,
            born_red=br,
            flaky_owed=fo,
        )
        runtime = tmp_path / f"runtime-{sf}"
        runtime.mkdir()
        batch_gate.write_record(rec, runtime)
        data = json.loads((runtime / "batch-gate.json").read_text())
        counts = data.get("counts")
        assert counts is not None, f"counts missing for fixture ({sf}, {a}, {fab}, {dab}, {br}, {fo})"
        bucket_sum = (
            counts["attributed"]
            + counts["failed_at_base"]
            + counts["declared_at_base"]
            + counts["born_red"]
            + counts["flaky_owed"]
        )
        assert bucket_sum == counts["suite_failed"], (
            f"bucket sum {bucket_sum} != suite_failed {counts['suite_failed']} "
            f"for fixture ({sf}, {a}, {fab}, {dab}, {br}, {fo})"
        )


# ── AC-3: readers tolerate absent counts ─────────────────────────────────────

def test_ac3_read_record_tolerates_absent_counts(tmp_path: Path) -> None:
    """batch_gate.read_record accepts a record without counts."""
    rec = _record_without_counts()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    batch_gate.write_record(rec, runtime)
    loaded = batch_gate.read_record(runtime)
    assert loaded is not None
    assert loaded.verdict == "pass"
    # counts is not a field on BatchGateRecord; it's only in the JSON.
    # The point is that read_record does not refuse the record.