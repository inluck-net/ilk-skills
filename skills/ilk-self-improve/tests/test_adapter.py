"""Tests for the ilk-self-improve adapter (AC-1..AC-3)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_task.py"
FEEDBACK_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
)


def _seed_backlog(backlog_dir: Path, entries: list[dict]) -> None:
    """Seed the backlog with the given entries via improvement_backlog.

    ``backlog_dir`` is the ``ILK_DATA_HOME`` root — entries are stored
    under ``<backlog_dir>/ilk-skills-improvements/candidates.json``
    (matching the real ``_backlog_dir()`` layout).
    """
    sys.path.insert(0, str(FEEDBACK_SCRIPTS))
    import importlib
    import improvement_backlog as bl

    # Point the module's _backlog_dir at our temp root
    actual_backlog = backlog_dir / "ilk-skills-improvements"
    for e in entries:
        bl.add_candidate(
            title=e["title"],
            kind=e.get("kind", "toolkit"),
            gap=e["gap"],
            evidence=e.get("evidence", {}),
            proposed_fix=e.get("proposed_fix", ""),
            leverage=e.get("leverage", "medium"),
            severity=e.get("severity", "medium"),
            backlog_dir=str(actual_backlog),
        )
    sys.path.pop(0)


def _run_build_task(backlog_dir: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run build_task.py with the given backlog dir."""
    env = {**os.environ, "ILK_DATA_HOME": str(backlog_dir)}
    cmd = [sys.executable, str(SCRIPT, )] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# AC-1: reads open candidates → deterministic task description
# ---------------------------------------------------------------------------

class TestAC1:
    """build_task.py reads open candidates and emits a grouped task description."""

    def test_includes_title_gap_evidence_proposed_fix(self, tmp_path):
        _seed_backlog(tmp_path, [
            {
                "title": "Missing feature X",
                "gap": "No support for X in launcher",
                "evidence": {"file": "launch.sh", "line": "42", "run_id": "r1", "project": "p"},
                "proposed_fix": "Add --x flag to launcher",
                "leverage": "high",
                "severity": "medium",
            },
        ])
        result = _run_build_task(tmp_path, ["--dry-run"])
        assert result.returncode == 0
        assert "Missing feature X" in result.stdout
        assert "No support for X in launcher" in result.stdout
        assert "launch.sh" in result.stdout
        assert "Add --x flag to launcher" in result.stdout
        assert "high" in result.stdout

    def test_excludes_shipped_and_closed(self, tmp_path):
        sys.path.insert(0, str(FEEDBACK_SCRIPTS))
        import improvement_backlog as bl

        actual_backlog = tmp_path / "ilk-skills-improvements"
        # Add an open candidate
        bl.add_candidate(title="Open gap", gap="open gap", backlog_dir=str(actual_backlog))
        # Add a shipped candidate
        bl.add_candidate(title="Shipped gap", gap="shipped gap", backlog_dir=str(actual_backlog))
        # Manually set status to shipped
        entries = bl.load(str(actual_backlog))
        for e in entries:
            if e.title == "Shipped gap":
                e.status = "shipped"
        from dataclasses import asdict
        bl._save_raw(actual_backlog, [asdict(e) for e in entries])
        sys.path.pop(0)

        result = _run_build_task(tmp_path, ["--dry-run"])
        assert result.returncode == 0
        assert "Open gap" in result.stdout
        assert "Shipped gap" not in result.stdout

    def test_multiple_candidates_ordered(self, tmp_path):
        _seed_backlog(tmp_path, [
            {"title": "Alpha gap", "gap": "alpha"},
            {"title": "Beta gap", "gap": "beta"},
            {"title": "Gamma gap", "gap": "gamma"},
        ])
        result = _run_build_task(tmp_path, ["--dry-run"])
        assert result.returncode == 0
        # All three present
        for name in ["Alpha gap", "Beta gap", "Gamma gap"]:
            assert name in result.stdout
        # Ordering: Alpha before Beta before Gamma
        assert result.stdout.index("Alpha gap") < result.stdout.index("Beta gap")
        assert result.stdout.index("Beta gap") < result.stdout.index("Gamma gap")


# ---------------------------------------------------------------------------
# AC-2: --dry-run prints without side effects
# ---------------------------------------------------------------------------

class TestAC2:
    """--dry-run prints the task description without invoking /ilk-plan."""

    def test_dry_run_prints_description(self, tmp_path):
        _seed_backlog(tmp_path, [{"title": "Test gap", "gap": "test"}])
        result = _run_build_task(tmp_path, ["--dry-run"])
        assert result.returncode == 0
        assert "Test gap" in result.stdout
        # No side effects — no plan files written
        assert not (tmp_path / "plans").exists()

    def test_dry_run_no_plan_files_created(self, tmp_path):
        _seed_backlog(tmp_path, [{"title": "X", "gap": "y"}])
        _run_build_task(tmp_path, ["--dry-run"])
        # The backlog dir should only contain candidates.json
        files = list(tmp_path.iterdir())
        assert len(files) == 1  # only the backlog dir (ilk-skills-improvements)


# ---------------------------------------------------------------------------
# AC-3: empty backlog → clean message, exit 0
# ---------------------------------------------------------------------------

class TestAC3:
    """Empty backlog → 'nothing to improve', exit 0, no plan."""

    def test_empty_backlog_exit_zero(self, tmp_path):
        result = _run_build_task(tmp_path, ["--dry-run"])
        assert result.returncode == 0
        assert "Nothing to improve" in result.stdout

    def test_empty_backlog_no_error(self, tmp_path):
        result = _run_build_task(tmp_path, ["--dry-run"])
        assert result.stderr == ""
