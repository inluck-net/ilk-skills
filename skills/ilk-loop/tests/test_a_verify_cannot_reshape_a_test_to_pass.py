"""Pins: a verify step cannot reshape or reach into another batch's tests.

Part of sub-plan ``a-verify-cannot-reshape-a-test-to-pass`` (step 0).

Three acceptance criteria.  AC-1 through AC-3 are red-first pins
(``xfail(strict=True)``): they test behaviour that does not exist yet.

Regression for gh-resolve G1's batch-verification step 1 (47e2cf21): a verify
commit reshaped a fixture, edited another batch's pin file, and synced a
contract count — all to reach green — and nothing in ilk refused it.

The mechanism under test (to be implemented in step 1):
- At the verify step's gate (``verify_attribution.py``, before it can pass),
  compute the verify sub-plan's own diff from commits carrying
  ``[plan:<verify-slug>#step-*]`` within ``batch_base..HEAD``.
- **Refuse** if that diff:
  (a) modifies any file named as a pin file by ANY sub-plan's
      ``pins_only``-style gate, or any ``test_*`` file another batch created;
  (b) adds a ``skip``, ``skipif``, ``xfail``, or ``importorskip`` to an
      existing test;
  (c) deletes an existing test function.
- **Declare and surface** any other edit to an existing test file: the commit
  body must carry ``[test-change: <node> — <why>]``.  A missing declaration
  ⇒ refuse.  Declared changes pass.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest


# ── git helpers ───────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in *repo* with fixed author identity."""
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )


def _init_repo(path: Path) -> None:
    """Create a git repo with an initial commit."""
    _git(path, "init", "-q")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")


def _head_sha(repo: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", check=True)
    return r.stdout.strip()


# ── project scaffolding ──────────────────────────────────────────────────────

def _scaffold_project(tmp_path: Path) -> dict[str, Path]:
    """Create a throwaway project with a pin file, an existing test, and a
    verify sub-plan.  Returns paths dict.

    Layout::

        project/
          tests/
            test_pin_target.py      ← owned by another batch (pin file)
            test_existing.py        ← an existing test file
          sub-plans/
            verify-slug.md          ← the verify sub-plan
            batch-slug.md           ← the batch sub-plan (owns test_pin_target)
    """
    project = tmp_path / "project"
    project.mkdir()
    _init_repo(project)

    tests = project / "tests"
    tests.mkdir()

    # Pin file: a test file owned by another batch.
    pin_target = tests / "test_pin_target.py"
    pin_target.write_text(textwrap.dedent("""\
        import pytest

        def test_pin_target():
            assert 1 + 1 == 2
    """), encoding="utf-8")

    # Existing test file with a fixture row.
    existing = tests / "test_existing.py"
    existing.write_text(textwrap.dedent("""\
        import pytest

        @pytest.mark.parametrize("row", [
            {"id": 1, "value": "a"},
            {"id": 2, "value": "b"},
            {"id": 3, "value": "c"},
        ])
        def test_with_fixtures(row):
            assert row["id"] > 0
    """), encoding="utf-8")

    # Sub-plans directory.
    sub_plans = project / "sub-plans"
    sub_plans.mkdir()

    # Batch sub-plan that owns test_pin_target.
    (sub_plans / "batch-slug.md").write_text(textwrap.dedent("""\
        ---
        plan: batch-slug
        status: shipped
        current_step: 2
        estimated_steps: 2
        ---
        # batch sub-plan
        ### Step 0
        - Commit: `feat(x): s0 [plan:batch-slug#step-0]`
        ### Step 1
        - Commit: `feat(x): s1 [plan:batch-slug#step-1]`
    """), encoding="utf-8")

    # Verify sub-plan.
    (sub_plans / "verify-slug.md").write_text(textwrap.dedent("""\
        ---
        plan: verify-slug
        status: in-progress
        current_step: 1
        estimated_steps: 2
        ---
        # verify sub-plan
        ### Step 0
        - Commit: `test(v): record [plan:verify-slug#step-0]`
        ### Step 1
        - Commit: `fix(v): resolve [plan:verify-slug#step-1]`
    """), encoding="utf-8")

    # Commit everything.
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "scaffold")

    return {"project": project, "tests": tests, "sub_plans": sub_plans}


def _make_verify_commit(
    project: Path, message: str, body: str = "",
) -> None:
    """Stage all changes and commit with the verify-slug trailer."""
    _git(project, "add", "-A")
    msg = message if not body else f"{message}\n\n{body}"
    _git(project, "commit", "-q", "-m", msg)


# ── import the function under test (step 1 will create it) ──────────────────

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
import sys as _sys
if str(_SCRIPTS) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS))

try:
    from verify_step_integrity import check_verify_commit, Violation  # type: ignore[import-untyped]
    _HAS_IMPL = True
except ImportError:
    _HAS_IMPL = False


# ── AC-1: edit another batch's pin file ⇒ refused ───────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="red-first: verify_step_integrity.check_verify_commit not implemented",
)
class TestAC1PinFileEdit:
    """A verify commit that modifies another sub-plan's pin file is refused."""

    def test_edit_pin_file_refused(self, tmp_path: Path) -> None:
        """AC-1: editing another batch's pin file ⇒ refused."""
        paths = _scaffold_project(tmp_path)
        project = paths["project"]

        # The verify commit edits the pin file.
        pin = paths["tests"] / "test_pin_target.py"
        pin.write_text(textwrap.dedent("""\
            import pytest

            def test_pin_target():
                assert 1 + 1 == 3  # changed by verify
        """), encoding="utf-8")
        _make_verify_commit(
            project,
            "fix(v): resolve attributed regressions "
            "[plan:verify-slug#step-1]",
        )

        base_sha = _head_sha(project)  # before the verify commit
        violations = check_verify_commit(
            project,
            verify_slug="verify-slug",
            batch_base=base_sha,
            pin_files={str(pin.relative_to(project))},
        )

        # At least one violation must refuse the pin file edit.
        pin_violations = [
            v for v in violations
            if "pin" in v.reason.lower() or "pin" in v.file.lower()
        ]
        assert pin_violations, (
            f"expected a pin-file violation, got: {violations}"
        )


# ── AC-2: add skip to existing test ⇒ refused ───────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="red-first: verify_step_integrity.check_verify_commit not implemented",
)
class TestAC2SkipAddition:
    """A verify commit that adds a skip marker to an existing test is refused."""

    def test_add_skip_refused(self, tmp_path: Path) -> None:
        """AC-2: adding @pytest.mark.skip to an existing test ⇒ refused."""
        paths = _scaffold_project(tmp_path)
        project = paths["project"]

        # The verify commit adds a skip marker to the existing test.
        existing = paths["tests"] / "test_existing.py"
        existing.write_text(textwrap.dedent("""\
            import pytest

            @pytest.mark.skip(reason="verify added this")
            @pytest.mark.parametrize("row", [
                {"id": 1, "value": "a"},
                {"id": 2, "value": "b"},
                {"id": 3, "value": "c"},
            ])
            def test_with_fixtures(row):
                assert row["id"] > 0
        """), encoding="utf-8")
        _make_verify_commit(
            project,
            "fix(v): resolve attributed regressions "
            "[plan:verify-slug#step-1]",
        )

        base_sha = _head_sha(project)
        violations = check_verify_commit(
            project,
            verify_slug="verify-slug",
            batch_base=base_sha,
            pin_files=set(),
        )

        skip_violations = [
            v for v in violations
            if "skip" in v.reason.lower() or "xfail" in v.reason.lower()
        ]
        assert skip_violations, (
            f"expected a skip-addition violation, got: {violations}"
        )


# ── AC-3: reshape fixture without declaration ⇒ refused ─────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="red-first: verify_step_integrity.check_verify_commit not implemented",
)
class TestAC3FixtureReshape:
    """A verify commit that changes a fixture value without a [test-change:]
    declaration is refused.  With the declaration, it passes and the record
    lists the node."""

    def test_reshape_without_declaration_refused(self, tmp_path: Path) -> None:
        """AC-3a: changing a fixture with no [test-change:] ⇒ refused."""
        paths = _scaffold_project(tmp_path)
        project = paths["project"]

        # The verify commit changes a fixture value.
        existing = paths["tests"] / "test_existing.py"
        existing.write_text(textwrap.dedent("""\
            import pytest

            @pytest.mark.parametrize("row", [
                {"id": 1, "value": "a"},
                {"id": 2, "value": "b"},
                {"id": 3, "value": "c"},
                {"id": 9999, "value": "verify-added"},  # reshaped
            ])
            def test_with_fixtures(row):
                assert row["id"] > 0
        """), encoding="utf-8")
        _make_verify_commit(
            project,
            "fix(v): resolve attributed regressions "
            "[plan:verify-slug#step-1]",
        )

        base_sha = _head_sha(project)
        violations = check_verify_commit(
            project,
            verify_slug="verify-slug",
            batch_base=base_sha,
            pin_files=set(),
        )

        undeclared = [
            v for v in violations
            if "test-change" in v.reason.lower()
            or "undeclared" in v.reason.lower()
        ]
        assert undeclared, (
            f"expected an undeclared-test-change violation, got: {violations}"
        )

    def test_reshape_with_declaration_passes(self, tmp_path: Path) -> None:
        """AC-3b: changing a fixture WITH [test-change:] ⇒ passes."""
        paths = _scaffold_project(tmp_path)
        project = paths["project"]

        # The verify commit changes a fixture AND declares it.
        existing = paths["tests"] / "test_existing.py"
        existing.write_text(textwrap.dedent("""\
            import pytest

            @pytest.mark.parametrize("row", [
                {"id": 1, "value": "a"},
                {"id": 2, "value": "b"},
                {"id": 3, "value": "c"},
                {"id": 9999, "value": "verify-added"},  # reshaped
            ])
            def test_with_fixtures(row):
                assert row["id"] > 0
        """), encoding="utf-8")
        _make_verify_commit(
            project,
            "fix(v): resolve attributed regressions "
            "[plan:verify-slug#step-1]",
            body=(
                "[test-change: tests/test_existing.py::test_with_fixtures — "
                "added fixture row to cover new skip]"
            ),
        )

        base_sha = _head_sha(project)
        violations = check_verify_commit(
            project,
            verify_slug="verify-slug",
            batch_base=base_sha,
            pin_files=set(),
        )

        # With the declaration, there should be no undeclared violations.
        undeclared = [
            v for v in violations
            if "test-change" in v.reason.lower()
            or "undeclared" in v.reason.lower()
        ]
        assert not undeclared, (
            f"declared change should pass, got violations: {undeclared}"
        )


# ── AC-4 (control): code-only change ⇒ no violation ─────────────────────────

class TestAC4Control:
    """A verify commit touching only non-test code ⇒ unchanged behaviour."""

    def test_code_only_change_clean(self, tmp_path: Path) -> None:
        """AC-4: a verify commit touching only code ⇒ no violations."""
        if not _HAS_IMPL:
            pytest.skip("verify_step_integrity not yet implemented")

        paths = _scaffold_project(tmp_path)
        project = paths["project"]

        # The verify commit changes only a non-test file.
        (project / "src.py").write_text("x = 1\n", encoding="utf-8")
        _make_verify_commit(
            project,
            "fix(v): resolve attributed regressions "
            "[plan:verify-slug#step-1]",
        )

        base_sha = _head_sha(project)
        violations = check_verify_commit(
            project,
            verify_slug="verify-slug",
            batch_base=base_sha,
            pin_files=set(),
        )

        assert not violations, (
            f"code-only change should have no violations, got: {violations}"
        )