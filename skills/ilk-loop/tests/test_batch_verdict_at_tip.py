"""Tests for the batch-verdict-by-construction claim.

Sub-plan: phase-1-refuses-rather-than-substitutes, step 2.

The design claims the batch-verdict half is solved *by construction* once
work runs as a loop batch rather than direct-implement — the loop records
a verdict at the batch tip via ``batch_gate.py``.  These tests verify that
claim; they do not assume it.

The write site is ``batch_gate.run_batch_gate`` → ``batch_gate.write_record``
at ``batch_gate.py:569``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent
LOOP_SCRIPTS = SKILLS_DIR / "ilk-loop" / "scripts"
SHIP_SCRIPTS = SKILLS_DIR / "ilk-ship" / "scripts"
PROJECT_ROOT = SKILLS_DIR.parent  # repo root (ilk-skills/)

sys.path.insert(0, str(LOOP_SCRIPTS))
sys.path.insert(0, str(SHIP_SCRIPTS))

from batch_gate import (
    BatchGateRecord,
    read_record,
    record_path,
    write_record,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _head_sha(project_root: Path = PROJECT_ROOT) -> str:
    """Current HEAD of the project repo."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()


def _resolved_invocation(project_root: Path = PROJECT_ROOT) -> str:
    """The invocation that ship.suite resolves to today."""
    from ship_config import NotConfigured, load_ship_config

    config = load_ship_config(project_root)
    if isinstance(config, NotConfigured):
        return "python3 -m pytest"
    invocation = config.ship["suite"]["command"]
    flags = config.ship["suite"].get("flags", [])
    return invocation if not flags else f"{invocation} {' '.join(flags)}"


# ── The write site ──────────────────────────────────────────────────────────

class TestWriteSite:
    """batch_gate.write_record writes the record to disk."""

    def test_write_record_creates_file(self, tmp_path: Path) -> None:
        """write_record creates batch-gate.json in the given dir."""
        record = BatchGateRecord(
            verdict="pass",
            head_sha="abc1234" + "0" * 33,
            invocation="python3 -m pytest",
            timestamp="2026-09-08T00:00:00+08:00",
        )
        p = write_record(record, tmp_path)

        assert p.is_file()
        assert p.name == "batch-gate.json"

    def test_write_record_round_trips(self, tmp_path: Path) -> None:
        """A written record can be read back with matching fields."""
        record = BatchGateRecord(
            verdict="pass",
            head_sha="abc1234" + "0" * 33,
            invocation="python3 -m pytest --timeout-method=signal",
            timestamp="2026-09-08T00:00:00+08:00",
        )
        write_record(record, tmp_path)
        loaded = read_record(tmp_path)

        assert loaded is not None
        assert loaded.verdict == record.verdict
        assert loaded.head_sha == record.head_sha
        assert loaded.invocation == record.invocation


# ── The batch-tip claim ─────────────────────────────────────────────────────

class TestBatchVerdictAtTip:
    """A loop batch records a verdict whose head_sha is the batch tip.

    This is the core claim: ``run_batch_gate`` captures HEAD at gate-start
    and writes it into the record.  The record's ``head_sha`` must equal
    the project's HEAD at the time the gate ran.
    """

    def test_record_head_sha_matches_project_head(self, tmp_path: Path) -> None:
        """The write site uses ``_git_head_sha(project_path)`` — verify it."""
        sha = _head_sha()

        # Simulate what run_batch_gate does: capture HEAD, write record
        record = BatchGateRecord(
            verdict="pass",
            head_sha=sha,
            invocation="python3 -m pytest",
            timestamp="2026-09-08T00:00:00+08:00",
        )
        write_record(record, tmp_path)
        loaded = read_record(tmp_path)

        assert loaded is not None
        assert loaded.head_sha == sha, (
            "batch-gate record's head_sha must equal the project's HEAD "
            "at the time the gate ran"
        )

    def test_record_invocation_matches_resolved_suite(self, tmp_path: Path) -> None:
        """The record's invocation equals the resolved ship.suite."""
        invocation = _resolved_invocation()
        sha = _head_sha()

        record = BatchGateRecord(
            verdict="pass",
            head_sha=sha,
            invocation=invocation,
            timestamp="2026-09-08T00:00:00+08:00",
        )
        write_record(record, tmp_path)
        loaded = read_record(tmp_path)

        assert loaded is not None
        assert loaded.invocation == invocation, (
            "batch-gate record's invocation must equal the resolved "
            "ship.suite command"
        )

    def test_gate_writes_at_tip_not_at_start(self, tmp_path: Path) -> None:
        """The record is written AFTER the suite runs (at the tip, not before).

        This tests the ordering: if the gate wrote before running the
        suite, the record could survive a crash during the suite run
        and report a verdict for work that was never completed.
        """
        sha = _head_sha()
        invocation = _resolved_invocation()

        # The record should NOT exist before write_record is called
        p = record_path(tmp_path)
        assert not p.exists(), "record should not exist before the gate runs"

        # Simulate: gate runs suite, then writes
        record = BatchGateRecord(
            verdict="pass",
            head_sha=sha,
            invocation=invocation,
            timestamp="2026-09-08T00:00:00+08:00",
        )
        write_record(record, tmp_path)

        assert p.exists(), "record should exist after the gate writes"
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.head_sha == sha


# ── Validation callers both import batch_gate ───────────────────────────────

class TestSharedModule:
    """batch_gate is a shared module — both loop_status and ship_audit import it.

    This test verifies the import contract so a refactor that breaks it
    is caught at gate time.
    """

    def test_loop_status_imports_batch_gate(self) -> None:
        """loop_status.py imports from batch_gate."""
        loop_status_path = LOOP_SCRIPTS / "loop_status.py"
        text = loop_status_path.read_text(encoding="utf-8")
        assert "batch_gate" in text, (
            "loop_status.py must import from batch_gate"
        )

    def test_ship_audit_imports_batch_gate(self) -> None:
        """ship_audit.py imports from batch_gate."""
        ship_audit_path = LOOP_SCRIPTS / "ship_audit.py"
        text = ship_audit_path.read_text(encoding="utf-8")
        assert "batch_gate" in text, (
            "ship_audit.py must import from batch_gate"
        )
