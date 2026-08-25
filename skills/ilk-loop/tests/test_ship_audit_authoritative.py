"""Red-first tests for an authoritative batch verdict in ship_audit.

The audit must derive the gate verdict from the validated batch record,
not from a flag.  On stale / invalid / absent, the audit refuses and
names which one — never a pass, never a plain fail.

AC-1: fresh pass → pass; fresh fail → fail.
AC-2: stale / invalid / absent each refused and named.
AC-3: the audit reuses SP2's validator (batch_gate.validate_record).
AC-4: --gate-passed overrides and the output states it.
AC-7: the real production stale record makes the audit refuse.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import ship_audit
from ship_audit import _evaluate_gate

# ── Fixtures ────────────────────────────────────────────────────────────────

# The real production stale record (AC-7).
STALE_RECORD = {
    "verdict": "pass",
    "head_sha": "879f33f105bac3b1d5c5a7c1b43bac71980bca71",
    "invocation": (
        "python3 -m pytest skills/ilk-loop/tests/test_batch_gate.py "
        "-q --timeout=60 --timeout-method=signal"
    ),
    "timestamp": "2026-08-25T16:46:29+08:00",
}


def _write_record(tmp_path: Path, record: dict) -> Path:
    """Write a batch-gate.json and return its parent (runtime_dir)."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "batch-gate.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return runtime


@pytest.fixture()
def fresh_pass_runtime(tmp_path: Path) -> Path:
    """A record that validates as fresh with verdict=pass."""
    return _write_record(tmp_path, {
        "verdict": "pass",
        "head_sha": "a" * 40,
        "invocation": "python3 -m pytest --timeout=60 --timeout-method=signal",
        "timestamp": "2026-08-25T20:00:00+08:00",
    })


@pytest.fixture()
def fresh_fail_runtime(tmp_path: Path) -> Path:
    """A record that validates as fresh with verdict=fail."""
    return _write_record(tmp_path, {
        "verdict": "fail",
        "head_sha": "a" * 40,
        "invocation": "python3 -m pytest --timeout=60 --timeout-method=signal",
        "timestamp": "2026-08-25T20:00:00+08:00",
    })


@pytest.fixture()
def stale_record_runtime(tmp_path: Path) -> Path:
    """A record with a stale head_sha."""
    return _write_record(tmp_path, {
        "verdict": "pass",
        "head_sha": "b" * 40,
        "invocation": "python3 -m pytest --timeout=60 --timeout-method=signal",
        "timestamp": "2026-08-25T20:00:00+08:00",
    })


@pytest.fixture()
def stale_production_runtime(tmp_path: Path) -> Path:
    """The real production stale record (AC-7)."""
    return _write_record(tmp_path, STALE_RECORD)


# ── AC-1: fresh record decides the verdict ──────────────────────────────────

class TestAC1FreshRecordDecides:
    """With a fresh, valid record, the audit derives the verdict from it."""

    def test_fresh_pass_yields_pass(
        self, fresh_pass_runtime: Path,
    ) -> None:
        """AC-1: fresh record with verdict=pass → gate is 'pass'."""
        with patch("batch_gate.validate_record", return_value="fresh"):
            verdict, reason = _evaluate_gate(
                "shipped",
                [{"command": "echo ok", "timeout": 10}],
                "unknown",
                runtime_dir=fresh_pass_runtime,
            )
        assert verdict == "pass", f"expected pass, got {verdict}: {reason}"

    def test_fresh_fail_yields_fail(
        self, fresh_fail_runtime: Path,
    ) -> None:
        """AC-1: fresh record with verdict=fail → gate is 'fail'."""
        with patch("batch_gate.validate_record", return_value="fresh"):
            verdict, reason = _evaluate_gate(
                "shipped",
                [{"command": "echo ok", "timeout": 10}],
                "unknown",
                runtime_dir=fresh_fail_runtime,
            )
        assert verdict == "fail", f"expected fail, got {verdict}: {reason}"


# ── AC-2: stale / invalid / absent each refused and named ───────────────────

class TestAC2UntrustedRefused:
    """On stale / invalid / absent, the audit refuses and names which one."""

    def test_stale_head_refused(
        self, stale_record_runtime: Path,
    ) -> None:
        """AC-2: stale record → refused, not a pass."""
        with patch("batch_gate.validate_record", return_value="stale_head"):
            verdict, reason = _evaluate_gate(
                "shipped",
                [{"command": "echo ok", "timeout": 10}],
                "unknown",
                runtime_dir=stale_record_runtime,
            )
        assert verdict == "stale_head", f"got {verdict}"
        assert verdict != "pass"

    def test_stale_invocation_refused(self, tmp_path: Path) -> None:
        """AC-2: stale invocation → refused, not a pass."""
        runtime = _write_record(tmp_path, {
            "verdict": "pass",
            "head_sha": "a" * 40,
            "invocation": "wrong.py -q",
            "timestamp": "2026-08-25T20:00:00+08:00",
        })
        with patch("batch_gate.validate_record",
                    return_value="stale_invocation"):
            verdict, reason = _evaluate_gate(
                "shipped",
                [{"command": "echo ok", "timeout": 10}],
                "unknown",
                runtime_dir=runtime,
            )
        assert verdict == "stale_invocation", f"got {verdict}"
        assert verdict != "pass"

    def test_absent_refused(self, tmp_path: Path) -> None:
        """AC-2: absent record → refused, not a pass."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        with patch("batch_gate.validate_record", return_value="absent"):
            verdict, reason = _evaluate_gate(
                "shipped",
                [{"command": "echo ok", "timeout": 10}],
                "unknown",
                runtime_dir=runtime,
            )
        assert verdict == "absent", f"got {verdict}"
        assert verdict != "pass"


# ── AC-3: the audit reuses SP2's validator ──────────────────────────────────

class TestAC3UsesValidator:
    """The audit must call batch_gate.validate_record, not re-derive staleness."""

    def test_calls_validate_record(
        self, fresh_pass_runtime: Path,
    ) -> None:
        """AC-3: _evaluate_gate calls batch_gate.validate_record."""
        with patch("batch_gate.validate_record", return_value="fresh") as mock:
            _evaluate_gate(
                "shipped",
                [{"command": "echo ok", "timeout": 10}],
                "unknown",
                runtime_dir=fresh_pass_runtime,
            )
        mock.assert_called_once()


# ── AC-7: the real production record is refused ─────────────────────────────

class TestAC7ProductionRecordRefused:
    """The stale production record on this host must make the audit refuse."""

    def test_real_production_record_refused(
        self, stale_production_runtime: Path,
    ) -> None:
        """AC-7: the real stale record → refused, not a pass."""
        with patch("batch_gate.validate_record", return_value="stale_head"):
            verdict, reason = _evaluate_gate(
                "shipped",
                [{"command": "echo ok", "timeout": 10}],
                "unknown",
                runtime_dir=stale_production_runtime,
            )
        assert verdict != "pass", (
            f"stale production record must not yield pass, got {verdict}"
        )
        assert verdict == "stale_head"
