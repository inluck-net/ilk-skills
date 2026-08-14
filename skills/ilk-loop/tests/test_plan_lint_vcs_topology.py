"""Tests for plan_lint scope_paths branch-topology lint.

Hermetic: builds temp git repos in tmp_path via subprocess.

Covers:
  AC-1  path on branch only -> HARD finding  (xfail until step 1)
  AC-2  path nowhere in history -> no finding (new-file case)
  AC-3  path on base -> no finding
  AC-4  non-git directory -> unknown finding  (xfail until step 2)
  AC-5  no 2>/dev/null or || echo in new code (grep gate, step 2+)
  AC-6  reachable through CLI entrypoint (step 3)
  AC-7  existing lint suite unaffected (step 4)
  AC-8  base-ref resolution is explicit (step 1)
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PLAN_LINT = _HERE.parent / "scripts" / "plan_lint.py"


# ── helpers ───────────────────────────────────────────────────────────

def _git_init(tmp_path: Path) -> None:
    """Initialise a hermetic git repo in *tmp_path*."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    )


def _make_repo_with_branch(tmp_path: Path) -> None:
    """Build a repo where base has ``base.txt`` and branch ``feature``
    has ``branch_only.txt`` (absent from base)."""
    _git_init(tmp_path)
    base_file = tmp_path / "base.txt"
    base_file.write_text("base content\n")
    subprocess.run(["git", "add", "base.txt"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "add base"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    branch_file = tmp_path / "branch_only.txt"
    branch_file.write_text("only on feature\n")
    subprocess.run(["git", "add", "branch_only.txt"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "add branch file"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)


def _make_non_git_dir(tmp_path: Path) -> Path:
    """Create a plain directory (not a git repo) for AC-4."""
    d = tmp_path / "not_a_repo"
    d.mkdir()
    (d / "some_file.txt").write_text("hello\n")
    return d


def _run_lint_on_fixture(tmp_path: Path, content: str,
                         filename: str = "test-plan.md") -> subprocess.CompletedProcess:
    """Write a temp sub-plan and run plan_lint.py against it."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(p)],
        cwd=tmp_path,
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── sub-plan fixtures ────────────────────────────────────────────────

def _subplan_with_scope(path: str) -> str:
    """Return a minimal sub-plan whose scope_paths contains *path*."""
    return textwrap.dedent(f"""\
        ---
        plan: test-vcs-topology
        status: in-progress
        scope_paths:
          - "{path}"
        ---

        # Sub-plan: test vcs topology

        Some work.
    """)


# ── the four discriminator cases ─────────────────────────────────────

class TestScopePathBranchTopology:
    """The three-way discriminator + non-git-directory unknown."""

    @pytest.fixture(autouse=True)
    def _repo(self, tmp_path: Path):
        _make_repo_with_branch(tmp_path)
        self.tmp_path = tmp_path

    # (a) path on branch only -> HARD finding
    def test_path_on_branch_only_is_hard_finding(self) -> None:
        r = _run_lint_on_fixture(
            self.tmp_path,
            _subplan_with_scope("branch_only.txt"),
        )
        assert r.returncode == 1, f"expected finding, got: {r.stdout}"
        assert "HARD" in r.stdout
        assert "branch_only.txt" in r.stdout

    # (b) path nowhere in history -> no finding (new-file case)
    def test_path_nowhere_in_history_is_ok(self) -> None:
        r = _run_lint_on_fixture(
            self.tmp_path,
            _subplan_with_scope("brand_new_file.py"),
        )
        assert r.returncode == 0, f"expected clean, got: {r.stdout}{r.stderr}"

    # (c) path on base -> no finding
    def test_path_on_base_is_ok(self) -> None:
        r = _run_lint_on_fixture(
            self.tmp_path,
            _subplan_with_scope("base.txt"),
        )
        assert r.returncode == 0, f"expected clean, got: {r.stdout}{r.stderr}"

    # (d) non-git directory -> unknown finding
    def test_non_git_dir_reports_unknown(self) -> None:
        non_git = _make_non_git_dir(self.tmp_path)
        r = _run_lint_on_fixture(
            non_git,
            _subplan_with_scope("some_file.txt"),
        )
        # The lint must report 'unknown', never a pass.
        assert r.returncode == 1, f"expected unknown finding, got: {r.stdout}"
        assert "unknown" in r.stdout.lower()


class TestProbeDiscipline:
    """AC-5: no 2>/dev/null or || echo in new code."""

    def test_no_stderr_suppression_in_plan_lint(self) -> None:
        """Grep for forbidden patterns in the lint source."""
        text = _PLAN_LINT.read_text(encoding="utf-8-sig")
        # Only check for these patterns in the new git-helper / lint code
        # (not in pre-existing comments or strings).
        bad_patterns = []
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # Skip comments and docstrings
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if "2>/dev/null" in line:
                bad_patterns.append(f"  line {i}: {stripped}")
            if "|| echo" in line:
                bad_patterns.append(f"  line {i}: {stripped}")
        assert not bad_patterns, (
            "Forbidden patterns found in plan_lint.py:\n"
            + "\n".join(bad_patterns)
        )


# ── AC-6: CLI reachability ───────────────────────────────────────────

class TestCliReachability:
    """AC-6: the lint is reachable through the real CLI entrypoint."""

    @pytest.fixture(autouse=True)
    def _repo(self, tmp_path: Path):
        _make_repo_with_branch(tmp_path)
        self.tmp_path = tmp_path

    def test_cli_emits_hard_finding_for_branch_only_path(self) -> None:
        """A fixture sub-plan with a branch-only scope_path produces a
        HARD finding via the CLI (not a direct function call)."""
        p = self.tmp_path / "test-plan.md"
        p.write_text(textwrap.dedent("""\
            ---
            plan: test-cli-reachability
            status: in-progress
            scope_paths:
              - "branch_only.txt"
            ---

            # Sub-plan: test cli reachability

            Some work.
        """), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(_PLAN_LINT), str(p)],
            cwd=self.tmp_path,
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        assert r.returncode == 1, f"expected finding via CLI, got: {r.stdout}"
        assert "HARD" in r.stdout
        assert "branch_only.txt" in r.stdout

    def test_cli_clean_for_base_path(self) -> None:
        """A fixture sub-plan with a base-branch scope_path is clean via CLI."""
        p = self.tmp_path / "test-plan-clean.md"
        p.write_text(textwrap.dedent("""\
            ---
            plan: test-cli-clean
            status: in-progress
            scope_paths:
              - "base.txt"
            ---

            # Sub-plan: test cli clean

            Some work.
        """), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(_PLAN_LINT), str(p)],
            cwd=self.tmp_path,
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        assert r.returncode == 0, f"expected clean, got: {r.stdout}{r.stderr}"
