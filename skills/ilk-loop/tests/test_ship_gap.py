"""Tests for ship_gap.py — committed-vs-changed path accounting."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "scripts"))
from importlib import import_module
ship_gap = import_module("ship_gap")
compute_gap = ship_gap.compute_gap


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one committed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "init.txt").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _head(repo: Path) -> str:
    cp = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return cp.stdout.strip()


class TestComputeGap:
    """AC-1, AC-2, AC-3, AC-7: compute_gap correctness."""

    def test_observed_shape(self, tmp_path: Path) -> None:
        """AC-1: 2 committed, 168 dirty → gap=166, unexplained=True."""
        repo = _make_git_repo(tmp_path)
        head_before = _head(repo)

        # Commit 2 files
        (repo / "a.txt").write_text("a", encoding="utf-8")
        (repo / "b.txt").write_text("b", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt", "b.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "add 2"], cwd=repo, check=True)
        head_after = _head(repo)

        # Create 168 dirty files
        for i in range(168):
            (repo / f"dirty_{i:03d}.txt").write_text(f"content {i}", encoding="utf-8")

        result = compute_gap(repo, head_before, head_after)

        assert result["committed_paths"] == 2
        assert result["tree_paths"] == 168
        assert result["gap"] == 166
        assert result["unexplained"] is True

    def test_clean_tree_after_commit(self, tmp_path: Path) -> None:
        """AC-2: clean tree after commit → unexplained=False."""
        repo = _make_git_repo(tmp_path)
        head_before = _head(repo)

        (repo / "a.txt").write_text("a", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "add a"], cwd=repo, check=True)
        head_after = _head(repo)

        result = compute_gap(repo, head_before, head_after)

        assert result["committed_paths"] == 1
        assert result["tree_paths"] == 0
        assert result["unexplained"] is False

    def test_no_commits(self, tmp_path: Path) -> None:
        """AC-3: zero commits → unexplained=False regardless of tree state."""
        repo = _make_git_repo(tmp_path)
        head = _head(repo)

        # Dirty the tree
        (repo / "dirty.txt").write_text("dirty", encoding="utf-8")

        result = compute_gap(repo, head, head)

        assert result["committed_paths"] == 0
        assert result["unexplained"] is False

    def test_untracked_files_count(self, tmp_path: Path) -> None:
        """AC-7: untracked files count toward tree_paths."""
        repo = _make_git_repo(tmp_path)
        head_before = _head(repo)

        # Commit one file
        (repo / "tracked.txt").write_text("tracked", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "add tracked"], cwd=repo, check=True)
        head_after = _head(repo)

        # Add untracked files
        for i in range(5):
            (repo / f"untracked_{i}.txt").write_text(f"untracked {i}", encoding="utf-8")

        result = compute_gap(repo, head_before, head_after)

        assert result["tree_paths"] == 5
        assert result["unexplained"] is True

    def test_staged_changes_count(self, tmp_path: Path) -> None:
        """Staged (but uncommitted) changes count toward tree_paths."""
        repo = _make_git_repo(tmp_path)
        head_before = _head(repo)

        # Commit one file
        (repo / "a.txt").write_text("a", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "add a"], cwd=repo, check=True)
        head_after = _head(repo)

        # Stage a modification without committing
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)

        result = compute_gap(repo, head_before, head_after)

        assert result["tree_paths"] == 1
        assert result["unexplained"] is True


class TestCLI:
    """Test the CLI interface."""

    def test_json_output(self, tmp_path: Path) -> None:
        """--json flag produces valid JSON with expected keys."""
        repo = _make_git_repo(tmp_path)
        head = _head(repo)

        import json
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            ship_gap.main(["--repo", str(repo), "--head-before", head, "--head-after", head, "--json"])
        finally:
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout

        data = json.loads(output)
        assert "committed_paths" in data
        assert "tree_paths" in data
        assert "gap" in data
        assert "unexplained" in data


class TestDriverParses:
    """AC-8: bash -n on the driver exits 0 after every step."""

    def test_driver_parses(self) -> None:
        """The driver script has no syntax errors."""
        driver = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"
        if not driver.exists():
            pytest.skip("driver script not found")
        result = subprocess.run(
            ["bash", "-n", str(driver)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"bash -n failed on {driver}:\n{result.stderr}"
        )

    def test_no_echo_zero_fabrication(self) -> None:
        """Structural guard: no || echo 0 / || true on the ship-gap path."""
        driver = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"
        if not driver.exists():
            pytest.skip("driver script not found")
        text = driver.read_text(encoding="utf-8")
        # Check for fabrication patterns on ship_gap lines
        for line in text.splitlines():
            if "ship_gap" in line.lower() or "_SHIP_GAP" in line:
                assert "|| echo 0" not in line, (
                    f"ship-gap line has || echo 0 fabrication: {line.strip()}"
                )
                assert "|| true" not in line, (
                    f"ship-gap line has || true fabrication: {line.strip()}"
                )
