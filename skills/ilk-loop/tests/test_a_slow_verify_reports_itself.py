"""A slow verify reports itself — phase timings + SLO breach filed to backlog.

Part of MASTER-2026-10-07k.  The contract (pre-resolved):
1. Time each phase with ``time.monotonic()`` and write ``phase_seconds:`` to
   the record with keys ``suite``, ``at_base``, ``head_reruns``, ``total``.
2. When ``total > VERIFY_SLO_S`` (900 s), call
   ``improvement_backlog.add_candidate(source="supervisor", ...)`` and print
   ``[verify-slo]``.  Wrapped in try/except — a backlog failure must never
   change the verify's exit code or record verdict.
3. No new suite run, wait, poll, or timeout is added.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verification_record as vr  # noqa: E402


# ── AC-1: record contains phase_seconds with four keys ──────────────────────

def test_record_contains_phase_seconds() -> None:
    """render_record must accept and write ``phase_seconds:`` with four keys."""
    phase_seconds = {"suite": 100, "at_base": 50, "head_reruns": 30, "total": 180}
    record_text = vr.render_record(
        batch="test-batch",
        head="abc1234def56789012345678901234567890abcd",
        tree="def56789012345678901234567890abcdef567890",
        base_sha="1234567890abcdef1234567890abcdef12345678",
        invocation="python -m pytest",
        scope={"mode": "full", "reason": "test", "count": 10},
        results={
            "counts": {"total": 10, "passed": 8, "failed": 2, "errors": 0, "skipped": 0},
            "failing_nodes": [],
        },
        at_base={},
        base_red=[],
        head_red=[],
        phase_seconds=phase_seconds,
    )
    assert "phase_seconds:" in record_text
    assert "suite=100" in record_text
    assert "at_base=50" in record_text
    assert "head_reruns=30" in record_text
    assert "total=180" in record_text


# ── AC-2: SLO breach → add_candidate called with correct args ──────────────

def test_slo_breach_files_to_backlog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Total 1000 s with suite 800 s ⇒ one add_candidate call, source=supervisor."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))

    # Verify the SLO constant exists.
    assert hasattr(vr, "VERIFY_SLO_S")
    assert vr.VERIFY_SLO_S == 900

    # Patch improvement_backlog.add_candidate to record calls.
    calls: list[dict] = []

    def _fake_add_candidate(**kwargs):
        calls.append(kwargs)
        return MagicMock()

    import improvement_backlog
    monkeypatch.setattr(improvement_backlog, "add_candidate", _fake_add_candidate)

    # Call _check_slo_breach with total > 900.
    phase_seconds = {"suite": 800, "at_base": 100, "head_reruns": 100, "total": 1000}
    vr._check_slo_breach("test-batch", "abc1234", tmp_path / "record.md", phase_seconds)

    assert len(calls) == 1
    assert calls[0]["source"] == "supervisor"
    assert calls[0]["kind"] == "toolkit"
    assert "suite" in calls[0].get("gap", "")
    assert calls[0].get("severity") == "high"
    assert calls[0].get("leverage") == "high"


# ── AC-3: no breach → no add_candidate call ────────────────────────────────

def test_no_breach_no_backlog_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Total 600 s ⇒ no add_candidate call."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))

    calls: list[dict] = []

    def _fake_add_candidate(**kwargs):
        calls.append(kwargs)
        return MagicMock()

    import improvement_backlog
    monkeypatch.setattr(improvement_backlog, "add_candidate", _fake_add_candidate)

    # Call _check_slo_breach with total <= 900.
    phase_seconds = {"suite": 400, "at_base": 100, "head_reruns": 100, "total": 600}
    vr._check_slo_breach("test-batch", "abc1234", tmp_path / "record.md", phase_seconds)

    assert len(calls) == 0


# ── AC-4: backlog raising ⇒ verify still exits normally ────────────────────

def test_backlog_failure_does_not_change_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """add_candidate raising ⇒ _check_slo_breach still returns normally."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))

    # Make add_candidate raise.
    import improvement_backlog

    def _raising_add_candidate(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(improvement_backlog, "add_candidate", _raising_add_candidate)

    # Call _check_slo_breach with total > 900 — should not raise.
    phase_seconds = {"suite": 800, "at_base": 100, "head_reruns": 100, "total": 1000}
    vr._check_slo_breach("test-batch", "abc1234", tmp_path / "record.md", phase_seconds)

    # If we get here, the function did not raise — contract met.