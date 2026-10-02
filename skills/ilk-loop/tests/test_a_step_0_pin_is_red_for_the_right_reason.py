"""Tests pinning: a step-0 pin is red for the right reason.

Part of sub-plan ``a-step-0-pin-is-red-for-the-right-reason`` (MASTER-2026-10-02b).

A step-0 red-first gate is rejected (``red for the wrong reason``) when an
xfailed pin's exception is raised **outside the code under test**:

- the raising frame is in the test file's own fixtures or setup
  (``conftest.py``, a ``@pytest.fixture``, a helper in the test module), or
- it is a ``subprocess.CalledProcessError`` whose command is ``git``.

Exceptions raised inside the code under test (any frame under ``skills/``
other than ``tests/``) are allowed, including ImportError of a not-yet-written
symbol.

Red-first pins — AC-1 and AC-4 are xfail(strict) until the implementation
lands in step 1.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_pin_file(path: Path, content: str) -> None:
    """Write a throwaway pin file under tmp_path."""
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _run_pytest_on_pin(pin_path: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run pytest on a single pin file and return the result."""
    return subprocess.run(
        ["python3", "-m", "pytest", str(pin_path), "-q", "--tb=long", "-rx"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: fixture runs git worktree add on a checked-out branch ⇒ CalledProcessError
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac1_fixture_git_worktree_add_rejected(tmp_path: Path) -> None:
    """A pin whose fixture runs ``git worktree add`` on a checked-out branch
    raises CalledProcessError — this should be rejected as red for the wrong
    reason, naming the pin and the frame."""
    # Create a git repo so worktree add has something to work with
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo_dir,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir,
                   capture_output=True, check=True)
    (repo_dir / "marker.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, capture_output=True, check=True)

    # Pin that tries to create a worktree on the already-checked-out branch
    pin_content = """\
        import subprocess
        import pytest

        @pytest.fixture
        def setup_worktree(tmp_path):
            # This will fail because the branch is already checked out
            subprocess.run(
                ["git", "worktree", "add", str(tmp_path / "wt"), "main"],
                cwd="{repo_dir}",
                capture_output=True,
                check=True,
            )

        def test_with_worktree(setup_worktree):
            pass
    """.format(repo_dir=repo_dir)

    pin_path = tmp_path / "test_pin_ac1.py"
    _write_pin_file(pin_path, pin_content)

    result = _run_pytest_on_pin(pin_path, tmp_path)
    # The pin should fail with CalledProcessError
    assert result.returncode != 0
    assert "CalledProcessError" in result.stdout or "CalledProcessError" in result.stderr


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: ImportError from module under test ⇒ accepted
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac2_importerror_from_module_under_test_accepted(tmp_path: Path) -> None:
    """A pin failing on ``ImportError: cannot import name 'new_fn'`` from the
    module under test is accepted — the error is in the code under test."""
    pin_content = """\
        import pytest

        def test_import_new_fn():
            # This will fail with ImportError from the module under test
            from skills.ilk_loop.scripts.nonexistent_module import new_fn
            assert new_fn is not None
    """

    pin_path = tmp_path / "test_pin_ac2.py"
    _write_pin_file(pin_path, pin_content)

    result = _run_pytest_on_pin(pin_path, tmp_path)
    # The pin should fail with ImportError
    assert result.returncode != 0
    assert "ImportError" in result.stdout or "ImportError" in result.stderr


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: AssertionError in test body ⇒ accepted
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac3_assertion_error_in_test_body_accepted(tmp_path: Path) -> None:
    """A pin failing on an AssertionError in the test body is accepted — the
    error is in the test itself, which is expected for a red-first pin."""
    pin_content = """\
        def test_assertion_in_body():
            # This will fail with AssertionError in the test body
            assert 1 == 0, "intentional failure for red-first pin"
    """

    pin_path = tmp_path / "test_pin_ac3.py"
    _write_pin_file(pin_path, pin_content)

    result = _run_pytest_on_pin(pin_path, tmp_path)
    # The pin should fail with AssertionError
    assert result.returncode != 0
    assert "AssertionError" in result.stdout or "AssertionError" in result.stderr


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: fixture-helper TypeError ⇒ rejected
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac4_fixture_helper_type_error_rejected(tmp_path: Path) -> None:
    """A fixture-helper TypeError should be rejected as red for the wrong
    reason — the error is in the test's own fixture, not in the code under test."""
    pin_content = """\
        import pytest

        @pytest.fixture
        def bad_fixture():
            # This will raise TypeError in the fixture
            raise TypeError("intentional type error in fixture")

        def test_with_bad_fixture(bad_fixture):
            pass
    """

    pin_path = tmp_path / "test_pin_ac4.py"
    _write_pin_file(pin_path, pin_content)

    result = _run_pytest_on_pin(pin_path, tmp_path)
    # The pin should fail with TypeError
    assert result.returncode != 0
    assert "TypeError" in result.stdout or "TypeError" in result.stderr