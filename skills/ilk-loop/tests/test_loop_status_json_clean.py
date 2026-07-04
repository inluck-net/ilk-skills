"""Tests for loop_status.py --json mode: stderr-clean + notices present.

Verifies AC-1, AC-2, AC-3, AC-4 from sub-plan 2026-07-04-loop-status-json-clean.
"""
import json
import subprocess
import sys
from pathlib import Path


def _write_master(plans_dir: Path, name: str, status: str, subplan_refs: list[str] | None = None) -> None:
    """Write a minimal MASTER plan with given status."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    refs = ""
    if subplan_refs:
        refs = "\n## Sub-plan registry\n\n| # | Slug | Status |\n|---|---|---|\n"
        for i, ref in enumerate(subplan_refs, 1):
            refs += f"| {i} | [{ref}](./{ref}) | pending |\n"
    plans_dir.joinpath(name).write_text(
        f"---\nmaster_plan: test\nstatus: {status}\n---\n\n"
        f"# Test master\n{refs}\n",
        encoding="utf-8",
    )


def _write_subplan(plans_dir: Path, name: str, status: str, cur: int = 0, est: int = 1) -> None:
    """Write a minimal sub-plan."""
    plans_dir.joinpath(name).write_text(
        f"---\nplan: test\nstatus: {status}\ncurrent_step: {cur}\nestimated_steps: {est}\n---\n\n"
        f"# Test sub-plan\n",
        encoding="utf-8",
    )


def test_no_active_master_json_clean(tmp_path: Path) -> None:
    """AC-1 + AC-2: one queued master, no active → valid JSON, empty stderr,
    notices contains 'previewing the next queued'."""
    plans_dir = tmp_path / "docs" / "plans"
    _write_master(plans_dir, "MASTER-2026-01-01-test.md", "queued", ["2026-01-01-alpha.md"])
    _write_subplan(plans_dir, "2026-01-01-alpha.md", "pending")

    # Create .git marker so ilk_paths finds the project root.
    (tmp_path / ".git").mkdir()

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "loop_status.py"), "--json"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 1, f"expected exit 1 (pending), got {result.returncode}"
    assert result.stderr == "", f"stderr should be empty, got: {result.stderr!r}"

    data = json.loads(result.stdout)
    assert "notices" in data, "JSON payload must contain 'notices' key"
    assert any("previewing the next queued" in n for n in data["notices"]), (
        f"notices should contain 'previewing the next queued', got: {data['notices']}"
    )


def test_multi_active_json_clean(tmp_path: Path) -> None:
    """AC-3: two active masters → valid JSON, empty stderr,
    notices contains '>1 active' warning."""
    plans_dir = tmp_path / "docs" / "plans"
    _write_master(plans_dir, "MASTER-2026-01-01-a.md", "active", ["2026-01-01-alpha.md"])
    _write_master(plans_dir, "MASTER-2026-01-02-b.md", "active", ["2026-01-01-alpha.md"])
    _write_subplan(plans_dir, "2026-01-01-alpha.md", "pending")

    (tmp_path / ".git").mkdir()

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "loop_status.py"), "--json"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 1, f"expected exit 1 (pending), got {result.returncode}"
    assert result.stderr == "", f"stderr should be empty, got: {result.stderr!r}"

    data = json.loads(result.stdout)
    assert "notices" in data
    assert any("masters have status: active" in n for n in data["notices"]), (
        f"notices should contain '>1 active' warning, got: {data['notices']}"
    )


def test_text_mode_unchanged(tmp_path: Path) -> None:
    """AC-4: text mode (no --json) still prints notices to stderr."""
    plans_dir = tmp_path / "docs" / "plans"
    _write_master(plans_dir, "MASTER-2026-01-01-test.md", "queued", ["2026-01-01-alpha.md"])
    _write_subplan(plans_dir, "2026-01-01-alpha.md", "pending")

    (tmp_path / ".git").mkdir()

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "loop_status.py")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # In text mode, notices go to stderr (original behaviour).
    assert "previewing the next queued" in result.stderr, (
        f"text mode stderr should contain notice, got: {result.stderr!r}"
    )
