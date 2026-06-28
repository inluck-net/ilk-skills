"""Tests for ship_integrity.py — the shipped-vs-gate honesty validator.

Covers the three AC-1 cases plus edge cases:
  - shipped + gate-green → OK
  - shipped + gate-red → violation
  - no-gate sub-plan → OK (nothing to enforce)
  - non-shipped status → OK (not enforced)
  - gate declared but no result → violation
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Import the module under test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ship_integrity import ShipVerdict, evaluate_ship


# ── evaluate_ship unit tests ─────────────────────────────────────────────────

class TestEvaluateShip:
    """AC-1: shipped + gate-green → OK; shipped + gate-red → violation;
    no-gate sub-plan → OK."""

    def test_shipped_with_green_gate_is_ok(self):
        """AC-1 case 1: shipped + gate-green → OK."""
        checks = [{"command": "pytest -q", "timeout": 120}]
        gate = {"all_passed": True, "results": []}
        verdict = evaluate_ship("shipped", checks, gate)
        assert verdict.ok is True
        assert "honest" in verdict.reason.lower()

    def test_shipped_with_red_gate_is_violation(self):
        """AC-1 case 2: shipped + gate-red → violation."""
        checks = [{"command": "pytest -q", "timeout": 120}]
        gate = {
            "all_passed": False,
            "results": [
                {"command": "pytest -q", "passed": False, "exit_code": 1},
            ],
        }
        verdict = evaluate_ship("shipped", checks, gate)
        assert verdict.ok is False
        assert "red" in verdict.reason.lower() or "violation" in verdict.reason.lower()
        # Should name the failing check.
        assert "pytest -q" in verdict.reason

    def test_no_gate_subplan_is_ok(self):
        """AC-1 case 3: no-gate sub-plan → OK (nothing to enforce)."""
        verdict = evaluate_ship("shipped", [], None)
        assert verdict.ok is True
        assert "no gate" in verdict.reason.lower()

    def test_non_shipped_status_is_ok(self):
        """Non-shipped status — not enforced, always OK."""
        for status in ("pending", "in-progress", "blocked"):
            verdict = evaluate_ship(status, [{"command": "pytest -q"}], None)
            assert verdict.ok is True

    def test_gate_declared_but_no_result_is_violation(self):
        """Gate declared but runner never recorded a result → violation."""
        checks = [{"command": "pytest -q", "timeout": 120}]
        verdict = evaluate_ship("shipped", checks, None)
        assert verdict.ok is False
        assert "no gate result" in verdict.reason.lower()

    def test_gate_red_with_multiple_failing_checks(self):
        """Failing check names are listed in the reason."""
        checks = [
            {"command": "pytest -q", "timeout": 120},
            {"command": "mypy skills/", "timeout": 60},
        ]
        gate = {
            "all_passed": False,
            "results": [
                {"command": "pytest -q", "passed": False, "exit_code": 1},
                {"command": "mypy skills/", "passed": False, "exit_code": 2},
            ],
        }
        verdict = evaluate_ship("shipped", checks, gate)
        assert verdict.ok is False
        assert "pytest -q" in verdict.reason
        assert "mypy skills/" in verdict.reason


# ── CLI integration tests ────────────────────────────────────────────────────

_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "ship_integrity.py")


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _SCRIPT, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestCLI:
    """AC-2: CLI exits non-zero on violation, zero on clean ship."""

    def test_cli_clean_ship(self):
        """Exit 0 for shipped + green gate."""
        result = _run_cli(
            "--status", "shipped",
            "--checks-json", json.dumps([{"command": "pytest -q", "timeout": 120}]),
            "--gate-json", json.dumps({"all_passed": True, "results": []}),
        )
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_cli_violation(self):
        """Exit 1 for shipped + red gate."""
        gate = {
            "all_passed": False,
            "results": [{"command": "pytest -q", "passed": False, "exit_code": 1}],
        }
        result = _run_cli(
            "--status", "shipped",
            "--checks-json", json.dumps([{"command": "pytest -q", "timeout": 120}]),
            "--gate-json", json.dumps(gate),
        )
        assert result.returncode == 1
        assert "VIOLATION" in result.stderr

    def test_cli_no_gate(self):
        """Exit 0 for no-gate sub-plan."""
        result = _run_cli(
            "--status", "shipped",
            "--checks-json", "[]",
            "--gate-json", "null",
        )
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_cli_bad_json_exits_2(self):
        """Exit 2 on malformed JSON input."""
        result = _run_cli(
            "--status", "shipped",
            "--checks-json", "not-json",
        )
        assert result.returncode == 2
        assert "error" in result.stderr.lower()
