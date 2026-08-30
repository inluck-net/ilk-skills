"""Pin the defect: an unisolated gate whose command exits 0 reads as a pass.

SP2 step 0 — this test is RED today.  After step 1 wires the demotion
logic, it goes green.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (duplicated from test_gate_isolates_to_head to keep this file
# self-contained; the shared helpers live in conftest once both tests merge)
# ---------------------------------------------------------------------------

def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one committed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "marker.txt").write_text("committed", encoding="utf-8")
    subprocess.run(["git", "add", "marker.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _make_subplan(tmp_path: Path, repo: Path, slug: str, command: str) -> None:
    """Write a minimal sub-plan + MASTER marker into repo/docs/plans/."""
    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    master = plans_dir / "MASTER-test.md"
    if not master.exists():
        master.write_text(
            "---\nmaster_plan: test\nbatch_date: 2026-08-30\nstatus: active\n---\n\n# Test\n",
            encoding="utf-8",
        )
    subplan = plans_dir / f"{slug}.md"
    subplan.write_text(
        textwrap.dedent(f"""\
            ---
            plan: {slug}
            status: in-progress
            current_step: 0
            estimated_steps: 1
            last_updated: 2026-08-30
            ---

            # Test sub-plan

            ## Steps

            ### Step 0 — test

            ```yaml
            local_checks:
              - command: "{command}"
                timeout: 30
            ```
        """),
        encoding="utf-8",
    )


def _run_gate(repo: Path, slug: str) -> dict:
    """Run run_local_checks.py and return the parsed JSON output."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "scripts"))
    from importlib import import_module
    rlc = import_module("run_local_checks")
    # Capture stdout
    import io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rlc.main(["--project", str(repo), "--slug", slug, "--step", "0"])
    finally:
        output_text = sys.stdout.getvalue()
        sys.stdout = old_stdout
    import json
    return json.loads(output_text)


# ---------------------------------------------------------------------------
# The defect: an unisolated gate reads as a pass
# ---------------------------------------------------------------------------

class TestUnisolatedGateBlocksShip:
    """An unisolated gate whose command exits 0 must not report a pass."""

    def test_unisolated_dirty_tree_is_not_pass(self, tmp_path: Path) -> None:
        """AC-1: unisolated + dirty → all_passed=False, error names path count."""
        repo = _make_git_repo(tmp_path)
        # Make the tree dirty
        (repo / "marker.txt").write_text("dirty", encoding="utf-8")
        (repo / "extra.txt").write_text("extra", encoding="utf-8")

        slug = "test-unisolated"
        _make_subplan(tmp_path, repo, slug, "true")  # command exits 0

        # Monkeypatch stash push to fail — isolation cannot succeed
        real_run = subprocess.run

        def _patched_run(cmd, *args, **kwargs):
            if len(cmd) >= 3 and cmd[0] == "git" and cmd[1] == "stash" and cmd[2] == "push":
                # Simulate stash failure
                import subprocess as sp
                return sp.CompletedProcess(cmd, returncode=1, stdout="", stderr="fatal: cannot stash")
            return real_run(cmd, *args, **kwargs)

        with patch("subprocess.run", side_effect=_patched_run):
            output = _run_gate(repo, slug)

        # Today this is RED: all_passed is True (the defect)
        assert output["all_passed"] is False, (
            "Unisolated gate should not report a pass, but all_passed=True — the defect"
        )
        # Error must name the path count
        assert "error" in output, "output should carry an error for unisolated gate"
        assert "uncommitted" in output["error"].lower() or "unisolated" in output["error"].lower(), (
            f"error should mention uncommitted/unisolated paths, got: {output['error']}"
        )
