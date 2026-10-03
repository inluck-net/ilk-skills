"""Tests for "a test-infrastructure change runs the full suite".

AC-1: diff touching only tests/_helper.py gives mode=="full" with reason.
AC-2: same for root tests/_util.py and tests/__init__.py.
AC-3: diff touching conftest.py gives full with reason naming that path.
AC-4: render_record writes suite_scope_reason for full-by-infra scope.
AC-5: --scope full over computed full-by-infra records the override+computed.
AC-6 (control): a module-only diff stays scoped.
AC-7 (control): tests/helpers/_a.py (parent is helpers, not tests) is NOT infra.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verification_record as vr  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    """Create a tmp git repo with one commit so HEAD resolves."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "config", "user.email", "test@test"],
                   cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "config", "user.name", "test"],
                   cwd=repo, capture_output=True, timeout=30)
    (repo / "placeholder").write_text("init")
    subprocess.run(["git", "add", "placeholder"], cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "commit", "-q", "-m", "init"],
                   cwd=repo, capture_output=True, timeout=30)
    return repo


def _commit_all(repo: Path, message: str) -> None:
    """Stage all changes and commit."""
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, timeout=30)
    subprocess.run(["git", "commit", "-q", "-m", message, "--allow-empty"],
                   cwd=repo, capture_output=True, timeout=30)


def _base_sha(repo: Path) -> str:
    """Return the short HEAD~1 SHA (the base commit)."""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD~1"],
        cwd=repo, capture_output=True, text=True, timeout=30,
    )
    return r.stdout.strip()


def _make_project_with_helper(tmp_path: Path, helper_path: str) -> Path:
    """Build a tmp repo with a script, its test, and a helper file.

    Returns the repo Path. The base commit has the script+test; the HEAD
    commit adds/changes the helper.
    """
    repo = _init_repo(tmp_path)
    # Create a script and its test at the base commit.
    script_dir = repo / "skills" / "x" / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "m.py").write_text("def m(): return 1\n")
    test_dir = repo / "skills" / "x" / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_m.py").write_text("from m import m\nassert m() == 1\n")
    _commit_all(repo, "base: script and test")

    # Add/change the helper at HEAD.
    helper = repo / helper_path
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("# helper\n")
    _commit_all(repo, "change helper")
    return repo


# ── AC-1: tests/_helper.py forces full ───────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="_test_infra_change not yet implemented")
def test_helper_py_forces_full(tmp_path: Path) -> None:
    """AC-1: a diff touching only skills/x/tests/_helper.py gives full scope."""
    repo = _make_project_with_helper(tmp_path, "skills/x/tests/_helper.py")
    base = _base_sha(repo)
    scope = vr.compute_suite_scope(repo, base)
    assert scope["mode"] == "full"
    assert "test infrastructure changed" in scope["reason"]
    assert "skills/x/tests/_helper.py" in scope["reason"]


# ── AC-2: root tests/_util.py and tests/__init__.py ──────────────────────────


@pytest.mark.xfail(strict=True, reason="_test_infra_change not yet implemented")
def test_root_util_py_forces_full(tmp_path: Path) -> None:
    """AC-2a: a diff touching tests/_util.py gives full scope."""
    repo = _make_project_with_helper(tmp_path, "tests/_util.py")
    base = _base_sha(repo)
    scope = vr.compute_suite_scope(repo, base)
    assert scope["mode"] == "full"
    assert "test infrastructure changed" in scope["reason"]
    assert "tests/_util.py" in scope["reason"]


@pytest.mark.xfail(strict=True, reason="_test_infra_change not yet implemented")
def test_init_py_forces_full(tmp_path: Path) -> None:
    """AC-2b: a diff touching skills/x/tests/__init__.py gives full scope."""
    repo = _make_project_with_helper(tmp_path, "skills/x/tests/__init__.py")
    base = _base_sha(repo)
    scope = vr.compute_suite_scope(repo, base)
    assert scope["mode"] == "full"
    assert "test infrastructure changed" in scope["reason"]
    assert "skills/x/tests/__init__.py" in scope["reason"]


# ── AC-3: conftest.py reason names the path ──────────────────────────────────


@pytest.mark.xfail(strict=True, reason="_test_infra_change not yet implemented")
def test_conftest_reason_names_path(tmp_path: Path) -> None:
    """AC-3: diff touching conftest.py gives full with reason naming that path."""
    repo = _make_project_with_helper(tmp_path, "skills/x/tests/conftest.py")
    base = _base_sha(repo)
    scope = vr.compute_suite_scope(repo, base)
    assert scope["mode"] == "full"
    assert "skills/x/tests/conftest.py" in scope["reason"]


# ── AC-4: render_record writes suite_scope_reason ────────────────────────────


@pytest.mark.xfail(strict=True, reason="suite_scope_reason not yet written")
def test_render_record_writes_suite_scope_reason() -> None:
    """AC-4: render_record for full-by-infra scope carries suite_scope_reason."""
    scope = {"mode": "full", "count": 0,
             "reason": "test infrastructure changed: skills/x/tests/_helper.py"}
    record = vr.render_record(
        batch="test-batch", head="abc123", tree="def456", base_sha="789abc",
        invocation="python3 -m pytest", scope=scope,
        results={"counts": {"total": 10, "passed": 10, "failed": 0,
                             "errors": 0, "skipped": 0}},
        at_base={}, base_red=[], head_red=[],
    )
    assert "suite_scope_reason: test infrastructure changed: skills/x/tests/_helper.py" in record
    # The existing suite_scope reader must still work.
    import re
    m = re.search(r"^suite_scope:\s*(\S+)", record, re.MULTILINE)
    assert m is not None
    assert m.group(1) == "full"


# ── AC-5: --scope full over computed full-by-infra ───────────────────────────


@pytest.mark.xfail(strict=True, reason="override reason not yet implemented")
def test_scope_full_over_computed_full_records_override() -> None:
    """AC-5: --scope full over computed full-by-infra records override+computed."""
    scope = {"mode": "full", "count": 0,
             "reason": "test infrastructure changed: skills/x/tests/_helper.py"}
    # Simulate the --scope full override path:
    # When computed mode is already full, reason = "override: --scope full; computed: " + old_reason
    old_reason = scope["reason"]
    overridden_reason = f"override: --scope full; computed: {old_reason}"
    overridden_scope = {"mode": "full", "count": 0, "reason": overridden_reason}
    record = vr.render_record(
        batch="test-batch", head="abc123", tree="def456", base_sha="789abc",
        invocation="python3 -m pytest", scope=overridden_scope,
        results={"counts": {"total": 10, "passed": 10, "failed": 0,
                             "errors": 0, "skipped": 0}},
        at_base={}, base_red=[], head_red=[],
    )
    assert "suite_scope_reason: override: --scope full; computed: test infrastructure changed: skills/x/tests/_helper.py" in record


# ── AC-6 (control): module-only diff stays scoped ────────────────────────────


def test_module_only_diff_stays_scoped(tmp_path: Path) -> None:
    """AC-6: a diff touching only skills/x/scripts/m.py stays scoped."""
    repo = _init_repo(tmp_path)
    script_dir = repo / "skills" / "x" / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "m.py").write_text("def m(): return 0\n")
    test_dir = repo / "skills" / "x" / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_m.py").write_text("from m import m\nassert m() == 0\n")
    _commit_all(repo, "base: script and test")

    # Change the script only.
    (script_dir / "m.py").write_text("def m(): return 1\n")
    _commit_all(repo, "change script")

    base = _base_sha(repo)
    scope = vr.compute_suite_scope(repo, base)
    assert scope["mode"] == "scoped"


def test_test_file_change_alone_stays_scoped(tmp_path: Path) -> None:
    """AC-6b: a diff touching only test_m.py stays scoped."""
    repo = _init_repo(tmp_path)
    script_dir = repo / "skills" / "x" / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "m.py").write_text("def m(): return 0\n")
    test_dir = repo / "skills" / "x" / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_m.py").write_text("from m import m\nassert m() == 0\n")
    _commit_all(repo, "base: script and test")

    # Change the test only.
    (test_dir / "test_m.py").write_text("from m import m\nassert m() == 1\n")
    _commit_all(repo, "change test")

    base = _base_sha(repo)
    scope = vr.compute_suite_scope(repo, base)
    assert scope["mode"] == "scoped"


# ── AC-7 (control): tests/helpers/ is NOT infrastructure ─────────────────────


def test_helpers_subdir_is_not_infra(tmp_path: Path) -> None:
    """AC-7: skills/x/tests/helpers/_a.py (parent is helpers) is NOT infra."""
    repo = _make_project_with_helper(tmp_path, "skills/x/tests/helpers/_a.py")
    base = _base_sha(repo)
    scope = vr.compute_suite_scope(repo, base)
    # Should NOT be forced to full by the infra rule.
    # (It may be full for other reasons, e.g. no test maps to _a.py, but the
    # reason must NOT mention "test infrastructure changed".)
    assert "test infrastructure" not in scope.get("reason", "")