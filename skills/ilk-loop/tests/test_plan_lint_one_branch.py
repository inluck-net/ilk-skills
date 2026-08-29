"""Tests for the master-level one-batch-one-branch gate.

Hermetic: builds temp git repos in tmp_path via subprocess.

Covers:
  AC-1  single-branch batch -> clean
  AC-2  two-branch batch -> HARD finding  (xfail until step 2)
  AC-3  no base_branch -> HARD finding    (xfail until step 2)
  AC-4  unresolvable ref -> HARD finding  (xfail until step 2)
  AC-5  master-template.md has base_branch (grep gate, step 1+)
  AC-6  reachable through CLI entrypoint
  AC-7  this batch's own master passes
  AC-8  supervised_only tests unaffected
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PLAN_LINT = _HERE.parent / "scripts" / "plan_lint.py"
_MASTER_TEMPLATE = _HERE.parent / "templates" / "master-template.md"


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


def _make_two_branch_repo(tmp_path: Path) -> None:
    """Build a repo where main has ``main.txt`` and branch ``feature``
    has ``feature.txt`` (absent from main)."""
    _git_init(tmp_path)
    (tmp_path / "main.txt").write_text("on main\n")
    subprocess.run(["git", "add", "main.txt"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "add main file"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    (tmp_path / "feature.txt").write_text("only on feature\n")
    subprocess.run(["git", "add", "feature.txt"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "add feature file"], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)


def _run_master(tmp_path: Path, master: str,
                subplans: dict[str, str]) -> subprocess.CompletedProcess:
    """Write a MASTER + sub-plans, run plan_lint with --master, return result.

    Passes ``--git-cwd tmp_path`` so git operations target the test's
    hermetic repo, not the real repo the test runner happens to be in.
    """
    mp = tmp_path / "MASTER-2026-08-13-execution-plan.md"
    mp.write_text(textwrap.dedent(master), encoding="utf-8")
    paths = []
    for name, content in subplans.items():
        sp = tmp_path / name
        sp.write_text(textwrap.dedent(content), encoding="utf-8")
        paths.append(str(sp))
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), "--git-cwd", str(tmp_path),
         "--master", str(mp), *paths],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )


def _findings(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


# ── master / sub-plan templates ───────────────────────────────────────

def _master(base_branch: str | None = None) -> str:
    bb = "" if base_branch is None else f"base_branch: {base_branch}\n"
    return (
        "---\n"
        "title: Test batch\n"
        "slug: test-one-branch\n"
        "status: queued\n"
        f"{bb}"
        "master_plan: 2026-08-13-master\n"
        "---\n"
        "\n"
        "# MASTER\n"
    )


_SUBPLAN_MAIN = """\
---
plan: main-work
scope_paths:
  - "main.txt"
---

# Sub-plan: main work

Touches main only.
"""

_SUBPLAN_FEATURE = """\
---
plan: feature-work
scope_paths:
  - "feature.txt"
---

# Sub-plan: feature work

Touches feature only.
"""


# ── AC-1: single-branch batch -> clean ────────────────────────────────

class TestSingleBranchClean:
    """AC-1: all sub-plans resolve to the declared base_branch."""

    @pytest.fixture(autouse=True)
    def _repo(self, tmp_path: Path):
        _make_two_branch_repo(tmp_path)
        self.tmp_path = tmp_path

    def test_single_branch_batch_is_clean(self) -> None:
        result = _run_master(
            self.tmp_path,
            _master("main"),
            {"main.md": _SUBPLAN_MAIN},
        )
        assert result.returncode == 0, (
            f"Expected clean for single-branch batch.\n{_findings(result)}"
        )


# ── AC-2: two-branch batch -> HARD ───────────────────────────────────

class TestTwoBranchHard:
    """AC-2: sub-plans resolve to more than one branch."""

    @pytest.fixture(autouse=True)
    def _repo(self, tmp_path: Path):
        _make_two_branch_repo(tmp_path)
        self.tmp_path = tmp_path

    def test_two_branch_batch_is_hard(self) -> None:
        result = _run_master(
            self.tmp_path,
            _master("main"),
            {"main.md": _SUBPLAN_MAIN, "feat.md": _SUBPLAN_FEATURE},
        )
        assert result.returncode == 1, (
            f"Expected HARD for two-branch batch.\n{_findings(result)}"
        )
        out = _findings(result)
        assert "HARD" in out, f"Expected HARD finding.\n{out}"
        assert "feature" in out and "main" in out, (
            f"Expected both branches named.\n{out}"
        )


# ── AC-3: no base_branch -> HARD ─────────────────────────────────────

class TestNoBaseBranchHard:
    """AC-3: master with no base_branch produces a HARD finding."""

    @pytest.fixture(autouse=True)
    def _repo(self, tmp_path: Path):
        _make_two_branch_repo(tmp_path)
        self.tmp_path = tmp_path

    def test_no_base_branch_is_hard(self) -> None:
        result = _run_master(
            self.tmp_path,
            _master(None),  # no base_branch
            {"main.md": _SUBPLAN_MAIN},
        )
        assert result.returncode == 1, (
            f"Expected HARD for missing base_branch.\n{_findings(result)}"
        )
        out = _findings(result)
        assert "HARD" in out, f"Expected HARD finding.\n{out}"
        assert "base_branch" in out, (
            f"Expected base_branch mentioned in finding.\n{out}"
        )


# ── AC-4: unresolvable ref -> HARD ───────────────────────────────────

class TestUnresolvableRefHard:
    """AC-4: base_branch naming an unresolvable ref produces HARD."""

    @pytest.fixture(autouse=True)
    def _repo(self, tmp_path: Path):
        _make_two_branch_repo(tmp_path)
        self.tmp_path = tmp_path

    def test_unresolvable_ref_is_hard(self) -> None:
        result = _run_master(
            self.tmp_path,
            _master("nonexistent-branch"),
            {"main.md": _SUBPLAN_MAIN},
        )
        assert result.returncode == 1, (
            f"Expected HARD for unresolvable ref.\n{_findings(result)}"
        )
        out = _findings(result)
        assert "HARD" in out, f"Expected HARD finding.\n{out}"
        assert "nonexistent-branch" in out, (
            f"Expected the bad ref named in finding.\n{out}"
        )


# ── AC-5: master-template.md has base_branch ─────────────────────────

class TestTemplateHasBaseBranch:
    """AC-5: master-template.md declares base_branch with comment."""

    def test_template_has_base_branch_field(self) -> None:
        text = _MASTER_TEMPLATE.read_text(encoding="utf-8-sig")
        assert "base_branch:" in text, (
            "master-template.md must contain base_branch: field"
        )

    def test_template_comment_distinguishes_from_branch(self) -> None:
        """The comment must explain base_branch is the validation ref,
        distinct from branch: (child-branch policy)."""
        text = _MASTER_TEMPLATE.read_text(encoding="utf-8-sig")
        # Find the base_branch line and check for an explanatory comment.
        for line in text.splitlines():
            if line.strip().startswith("base_branch:"):
                assert "#" in line or "validate" in line.lower() or "scope" in line.lower(), (
                    f"base_branch line should have a comment explaining its purpose.\n"
                    f"Line: {line}"
                )
                return
        pytest.fail("base_branch: not found in template")


# ── AC-6: CLI reachability ───────────────────────────────────────────

class TestCliReachability:
    """AC-6: the gate runs through the real CLI under --master."""

    @pytest.fixture(autouse=True)
    def _repo(self, tmp_path: Path):
        _make_two_branch_repo(tmp_path)
        self.tmp_path = tmp_path

    def test_cli_clean_for_single_branch(self) -> None:
        result = _run_master(
            self.tmp_path,
            _master("main"),
            {"main.md": _SUBPLAN_MAIN},
        )
        assert result.returncode == 0, (
            f"Expected clean via CLI.\n{_findings(result)}"
        )


# ── AC-7: this batch's own master passes ─────────────────────────────

class TestOwnMasterPasses:
    """AC-7: the batch's own master passes the new gate."""

    def test_own_master_has_base_branch(self) -> None:
        """The MASTER for this batch declares base_branch: main."""
        master_path = (
            Path.home()
            / ".ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills"
            / "plans/MASTER-2026-08-13-planner-gates-and-signal-fidelity-execution-plan.md"
        )
        if not master_path.exists():
            pytest.skip("MASTER not found in external plans dir")
        text = master_path.read_text(encoding="utf-8-sig")
        assert "base_branch:" in text, (
            "This batch's own master must declare base_branch"
        )


# ── AC-8: supervised_only tests unaffected ────────────────────────────

class TestSupervisedOnlyUnaffected:
    """AC-8: the existing supervised_only check still works."""

    def test_supervised_only_test_file_exists(self) -> None:
        p = _HERE / "test_plan_lint_supervised_only.py"
        assert p.exists(), "test_plan_lint_supervised_only.py must exist"

    @pytest.mark.timeout(180)
    def test_supervised_only_tests_still_pass(self) -> None:
        r = subprocess.run(
            [sys.executable, "-m", "pytest",
             str(_HERE / "test_plan_lint_supervised_only.py"), "-q"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        assert r.returncode == 0, (
            f"supervised_only tests regressed.\nstdout={r.stdout}\nstderr={r.stderr}"
        )
