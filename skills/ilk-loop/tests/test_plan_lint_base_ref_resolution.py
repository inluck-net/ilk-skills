"""Tests for the base-ref resolution in lint_scope_path_off_base_branch.

F5: the off-base check never sees the declared base.  These tests pin the
correct behaviour — threading the master's ``base_branch:`` into the per-path
check and resolving against ``origin/<base>`` when it exists.

Hermetic: builds temp git repos in tmp_path via subprocess.

Covers:
  AC-1  base_branch: dev → validates against dev, not main
  AC-2  origin/<base> preferred over local <base>
  AC-3  unresolvable base ref reports 'unknown' with the ref name
  AC-4  lint_one_batch_one_branch uses the same resolver
  AC-5  no base_branch → per-path check uses default 'main' and names it
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint
from plan_lint import lint_scope_path_off_base_branch, lint_one_batch_one_branch


# ── helpers ──────────────────────────────────────────────────────────────

def _git_init(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@test.local"],
                   cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=tmp_path, check=True, capture_output=True, text=True)


def _make_stale_local_repo(tmp_path: Path) -> Path:
    """Build a repo where local ``dev`` is stale but ``origin/dev`` has the file.

    Layout:
      origin/dev: has main.txt + feature.txt   (the "real" base)
      local dev:  behind by 1 commit, only main.txt  (stale)
    """
    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", str(bare)], check=True,
                   capture_output=True, text=True)

    work = tmp_path / "work"
    work.mkdir()
    _git_init(work)

    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=work,
                   check=True, capture_output=True, text=True)

    # Initial commit on main.
    (work / "main.txt").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=work, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "branch", "-M", "dev"], cwd=work, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "push", "-u", "origin", "dev"], cwd=work, check=True,
                   capture_output=True, text=True)

    # Push feature.txt to origin/dev only — local dev stays behind.
    (work / "feature.txt").write_text("on origin/dev only\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "add feature"], cwd=work, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "push", "origin", "dev"], cwd=work, check=True,
                   capture_output=True, text=True)

    # Move local dev back one commit so it's stale.
    subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=work, check=True,
                   capture_output=True, text=True)

    return work


def _make_repo_with_feature_branch(tmp_path: Path) -> Path:
    """Build a repo where main has a.txt and feature branch has b.txt."""
    _git_init(tmp_path)
    (tmp_path / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    (tmp_path / "b.txt").write_text("b\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "add b"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    return tmp_path


def _subplan_text(scope_paths: list[str]) -> str:
    paths_yaml = "\n".join(f'  - "{p}"' for p in scope_paths)
    return textwrap.dedent(f"""\
        ---
        plan: test-slug
        status: in-progress
        current_step: 0
        estimated_steps: 1
        scope_paths:
        {paths_yaml}
        ---

        # Test sub-plan
        Nothing here.
    """)


# ── AC-1: base_branch: dev → validates against dev, not main ────────────

def test_ac1_uses_declared_base_branch(tmp_path: Path) -> None:
    """With base_ref='dev', the check should validate against dev — not main.

    Current behaviour: always validates against the literal 'main', so
    feature.txt (on dev but not main) produces a HARD finding even when
    base_ref='dev' is passed.
    """
    work = _make_stale_local_repo(tmp_path)

    subplan = _subplan_text(["feature.txt"])
    with patch.object(plan_lint, "_GIT_CWD", work):
        findings = lint_scope_path_off_base_branch(subplan, "test-slug", base_ref="dev")
    assert findings == [], f"Expected 0 findings with base_ref='dev', got: {findings}"


# ── AC-2: origin/<base> preferred over local <base> ─────────────────────

def test_ac2_prefers_remote_tracking_ref(tmp_path: Path) -> None:
    """A stale local dev with the path present on origin/dev yields 0 findings.

    Current behaviour: cat-file checks the LOCAL ref, sees the path as absent,
    and fires a HARD finding — even though origin/dev has it.
    """
    work = _make_stale_local_repo(tmp_path)

    subplan = _subplan_text(["feature.txt"])
    with patch.object(plan_lint, "_GIT_CWD", work):
        findings = lint_scope_path_off_base_branch(subplan, "test-slug", base_ref="dev")
    assert findings == [], (
        f"Stale local branch with path on origin/dev should yield 0 findings. "
        f"Got: {findings}"
    )


# ── AC-3: unresolvable base ref reports 'unknown' with the ref name ────

def test_ac3_unresolvable_ref_names_the_ref(tmp_path: Path) -> None:
    """An unresolvable base ref must report 'unknown' AND name the ref.

    The fix resolves the ref before checking the path, so the finding text
    will name the resolved ref (e.g. 'origin/nonexistent').  Current code
    produces 'unknown — git cat-file failed (rc=128): fatal: invalid object
    name nonexistent' — which happens to contain the ref name by accident.
    The fix should make this explicit in the "base ref '…'" phrasing.
    """
    work = _make_repo_with_feature_branch(tmp_path)

    subplan = _subplan_text(["b.txt"])  # b.txt exists only on feature branch
    with patch.object(plan_lint, "_GIT_CWD", work):
        findings = lint_scope_path_off_base_branch(subplan, "test-slug", base_ref="nonexistent")
    assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"
    # The fix should name the ref in the standard "base ref '…'" phrasing.
    # Current code says "unknown — git cat-file failed (rc=128): fatal:
    # invalid object name 'nonexistent'" — no "base ref" phrasing.
    assert "base ref" in findings[0].lower(), (
        f"Finding should name the base ref in 'base ref …' phrasing. Got: {findings[0]}"
    )


# ── AC-4: lint_one_batch_one_branch uses the same resolver ──────────────

def test_ac4_one_batch_one_branch_uses_same_resolver(tmp_path: Path) -> None:
    """lint_one_batch_one_branch should resolve origin/<base> too.

    Current behaviour: uses rev-parse --verify on the LOCAL ref, so a stale
    local dev branch causes it to resolve a different commit than origin/dev.
    """
    work = _make_stale_local_repo(tmp_path)

    master_text = textwrap.dedent("""\
        ---
        master_plan: 2026-08-29-execution
        batch_date: 2026-08-29
        base_branch: dev
        status: active
        ---

        # MASTER plan

        ## Sub-plan registry

        | # | Slug | Status |
        |---|---|---|
        | 1 | test-slug | pending |
    """)

    subplan = _subplan_text(["feature.txt"])
    with patch.object(plan_lint, "_GIT_CWD", work):
        findings = lint_one_batch_one_branch(master_text, [("test-slug", subplan)])
    off_base = [f for f in findings if "absent" in f.lower() or "off-base" in f.lower()]
    assert off_base == [], (
        f"lint_one_batch_one_branch should resolve origin/dev. Got: {off_base}"
    )


# ── AC-5: no base_branch → per-path check uses default 'main' ──────────

def test_ac5_no_base_branch_names_default(tmp_path: Path) -> None:
    """Without a declared base_branch, the per-path check defaults to 'main'.

    The finding text must explicitly say it used the default, so the reader
    knows the check didn't silently pick a wrong ref.  Current behaviour:
    the finding says "absent on base ref 'main'" — which names the ref but
    does NOT say it was the default.  The fix should include "default" or
    similar phrasing when no base_branch was declared.
    """
    work = _make_repo_with_feature_branch(tmp_path)

    subplan = _subplan_text(["b.txt"])  # b.txt exists only on feature
    with patch.object(plan_lint, "_GIT_CWD", work):
        findings = lint_scope_path_off_base_branch(subplan, "test-slug")
    assert len(findings) == 1, f"Expected 1 finding, got {len(findings)}: {findings}"
    # The fix should explicitly say this is the default, not just name 'main'.
    # Current code says "absent on base ref 'main'" — no "default" keyword.
    assert "default" in findings[0].lower(), (
        f"Finding should say 'default' when no base_branch declared. Got: {findings[0]}"
    )
