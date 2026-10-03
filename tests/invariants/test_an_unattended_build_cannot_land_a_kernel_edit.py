"""I7: an unattended build cannot land a kernel edit.

``safety_kernel.py check-range`` exits 1 naming ``kernel-edit-by-unattended-build``
for an ``auto_planned: true`` batch commit editing ``batch_gate.py``, and
``rules-edit-by-loop-build`` for a trailered commit editing
``tests/invariants/``.

Rail: ``check_range`` (sub-plan 0).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"
SAFETY_KERNEL = _SCRIPTS / "safety_kernel.py"

pytestmark = pytest.mark.timeout(120)


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.stdout.strip()


def _make_kernel(repo: Path) -> None:
    """Write a minimal safety-kernel.json with one kernel and one rules entry."""
    kernel = {
        "schema": 1,
        "rules": [
            {"path": "tests/invariants/", "why": "invariant suite"},
        ],
        "kernel": [
            {"path": "skills/ilk-loop/scripts/batch_gate.py",
             "why": "batch gate is kernel"},
        ],
    }
    sk_dir = repo / "skills" / "ilk-loop"
    sk_dir.mkdir(parents=True)
    (sk_dir / "safety-kernel.json").write_text(
        json.dumps(kernel, indent=2), encoding="utf-8",
    )


def _make_master(plans: Path, slug: str, *, auto_planned: bool = True) -> None:
    """Write a MASTER plan with auto_planned set."""
    stem = f"2026-10-04-{slug}"
    master = plans / "MASTER-2026-10-04-execution-plan.md"
    master.write_text(
        "---\n"
        "master_plan: 2026-10-04-execution\n"
        "batch_date: 2026-10-04\n"
        "status: active\n"
        f"auto_planned: {'true' if auto_planned else 'false'}\n"
        "supervised_only: false\n"
        "---\n\n# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{stem}](./{stem}.md) |\n",
        encoding="utf-8",
    )
    sub = plans / f"{stem}.md"
    sub.write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n\n# work\n\n### Step 0\n\nBody.\n",
        encoding="utf-8",
    )


def _make_repo(tmp_path: Path, *, auto_planned: bool = True) -> tuple[Path, Path]:
    """Repo with safety-kernel.json at base, then a kernel-edit commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    # Base: kernel list + README.
    _make_kernel(repo)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init: kernel list")

    # Plans dir (external).
    plans = tmp_path / "plans"
    plans.mkdir()
    slug = "kernel-edit-test"
    _make_master(plans, slug, auto_planned=auto_planned)

    # Commit: edit a kernel file (batch_gate.py) with plan trailer.
    bg = repo / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
    bg.parent.mkdir(parents=True, exist_ok=True)
    bg.write_text("# kernel file edited\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"feat: edit kernel [plan:{slug}#step-0]")

    return repo, plans


def _make_repo_rules(tmp_path: Path) -> tuple[Path, Path]:
    """Repo with a rules-edit commit (trailered, editing tests/invariants/)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    _make_kernel(repo)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init: kernel list")

    plans = tmp_path / "plans"
    plans.mkdir()
    slug = "rules-edit-test"
    _make_master(plans, slug, auto_planned=False)

    # Commit: edit a rules file (tests/invariants/) with plan trailer.
    inv = repo / "tests" / "invariants" / "test_new.py"
    inv.parent.mkdir(parents=True)
    inv.write_text("def test_new(): assert True\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"feat: edit rules [plan:{slug}#step-0]")

    return repo, plans


def _run_check_range(
    repo: Path, plans: Path, base: str, head: str,
) -> tuple[int, list[dict], str]:
    r = subprocess.run(
        [sys.executable, str(SAFETY_KERNEL),
         "check-range",
         "--repo", str(repo),
         "--base", base,
         "--head", head,
         "--plans-dir", str(plans),
         "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    try:
        violations = json.loads(r.stdout) if r.stdout.strip() else []
    except json.JSONDecodeError:
        violations = []
    return r.returncode, violations, r.stderr


def test_kernel_edit_by_unattended_build(tmp_path: Path) -> None:
    """auto_planned=true commit editing a kernel file is a violation."""
    repo, plans = _make_repo(tmp_path, auto_planned=True)
    base = _git(repo, "rev-parse", "HEAD~1")
    head = _git(repo, "rev-parse", "HEAD")

    rc, violations, stderr = _run_check_range(repo, plans, base, head)

    # check-range exits 1 when violations are found.
    assert rc == 1, f"check-range should exit 1 (violations found), got {rc}.\nstderr={stderr[-300:]}"
    assert len(violations) > 0, (
        "an auto_planned commit editing batch_gate.py should be a violation"
    )
    v = violations[0]
    assert v.get("reason") == "kernel-edit-by-unattended-build", (
        f"expected reason 'kernel-edit-by-unattended-build', got {v.get('reason')!r}.\n"
        f"violations={json.dumps(violations, indent=2)}"
    )


def test_rules_edit_by_loop_build(tmp_path: Path) -> None:
    """Trailered commit editing tests/invariants/ is a rules violation."""
    repo, plans = _make_repo_rules(tmp_path)
    base = _git(repo, "rev-parse", "HEAD~1")
    head = _git(repo, "rev-parse", "HEAD")

    rc, violations, stderr = _run_check_range(repo, plans, base, head)

    # check-range exits 1 when violations are found.
    assert rc == 1, f"check-range should exit 1 (violations found), got {rc}.\nstderr={stderr[-300:]}"
    assert len(violations) > 0, (
        "a trailered commit editing tests/invariants/ should be a rules violation"
    )
    v = violations[0]
    assert v.get("reason") == "rules-edit-by-loop-build", (
        f"expected reason 'rules-edit-by-loop-build', got {v.get('reason')!r}.\n"
        f"violations={json.dumps(violations, indent=2)}"
    )