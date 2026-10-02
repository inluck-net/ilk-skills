"""Tests for an-unmeasured-root-reverts-nothing.

AC-1: unresolvable root + shipped sub-plan + empty gate ⇒ stays shipped.
AC-2: unresolvable root + shipped sub-plan + FAIL gate ⇒ reverted (control).
AC-3: real git repo ⇒ unchanged behaviour (control).
AC-4: ship_integrity.py with unresolvable root + --gate-passed false ⇒ exit 1.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_plan(plans_dir: Path, slug: str, status: str, gated: bool = False) -> Path:
    """Write a minimal sub-plan file."""
    if gated:
        checks = "local_checks:\n  - command: pytest -q\n    timeout: 60"
    else:
        checks = "local_checks: []"
    fname = f"2026-01-01-{slug}.md"
    p = plans_dir / fname
    p.write_text(
        f"---\n"
        f"plan: {slug}\n"
        f"status: {status}\n"
        f"current_step: 2\n"
        f"estimated_steps: 2\n"
        f"{checks}\n"
        f"---\n"
        f"\n"
        f"# {slug}\n"
    )
    return p


def _write_master(plans_dir: Path, slugs: list[str]) -> Path:
    """Write a minimal MASTER plan referencing the given slugs."""
    rows = "\n".join(
        f"| {i} | [2026-01-01-{s}.md](./2026-01-01-{s}.md) | {s} | 2 | shipped |"
        for i, s in enumerate(slugs)
    )
    p = plans_dir / "MASTER-2026-01-01-execution-plan.md"
    p.write_text(
        f"---\n"
        f"master_plan: 2026-01-01-execution\n"
        f"batch_date: 2026-01-01\n"
        f"status: active\n"
        f"total_tickets: {len(slugs)}\n"
        f"current_subplan: 2026-01-01-{slugs[0]}\n"
        f"---\n"
        f"\n"
        f"# MASTER plan\n"
        f"\n"
        f"## Sub-plan registry\n"
        f"\n"
        f"| # | Slug | Items | Steps (est.) | Status |\n"
        f"|---|---|---|---|---|\n"
        f"{rows}\n"
    )
    return p


def _source_driver(env: dict[str, str]) -> str:
    """Return a bash snippet that sources the runner with ILK_DOTSOURCE_ONLY."""
    runner = SCRIPTS / "run_ilk_loop_claude.sh"
    # _SKILL_ROOT must be the skills/ directory (parent of ilk-loop/).
    skill_root = SCRIPTS.parent.parent
    return textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export HOME="{env['HOME']}"
        export ILK_DATA_HOME="{env.get('ILK_DATA_HOME', env['HOME'] + '/.ilk-data')}"
        source "{runner}" 2>/dev/null || exit 1
        unset ILK_DOTSOURCE_ONLY
        _SKILL_ROOT="{skill_root}"
    """)


# ── AC-1: unresolvable root + empty gate ⇒ stays shipped ────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_unresolvable_root_keeps_shipped_status(tmp_path: Path):
    """AC-1: PROJECT_PATH missing, shipped demo, empty gate ⇒ stays shipped."""
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "ILK_DATA_HOME": str(home / ".ilk-data")}

    plans = home / ".ilk-data" / "projects" / "test" / "plans"
    plans.mkdir(parents=True)
    _write_master(plans, ["demo"])
    plan_file = _write_plan(plans, "demo", "shipped", gated=True)

    lc_file = tmp_path / "lc.jsonl"
    lc_file.write_text("")  # empty — no gate ran

    # CWD must NOT be a git repo — _resolve_project_root falls back to CWD.
    non_git = tmp_path / "non-git-cwd"
    non_git.mkdir()

    script = _source_driver(env) + textwrap.dedent(f"""\
        set +eE +o pipefail
        test_ship_integrity "{plans}" "{lc_file}" "" "" 2>/dev/null
        echo "exit:$?"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(non_git),
    )

    status_after = plan_file.read_text().split("status:")[1].splitlines()[0].strip()
    assert status_after == "shipped", (
        f"AC-1: expected shipped, got {status_after}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ── AC-2: unresolvable root + FAIL gate ⇒ reverted (control) ────────────────

def test_unresolvable_root_reverts_on_red_gate(tmp_path: Path):
    """AC-2: control — a red gate must still revert even with unresolvable root."""
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "ILK_DATA_HOME": str(home / ".ilk-data")}

    plans = home / ".ilk-data" / "projects" / "test" / "plans"
    plans.mkdir(parents=True)
    _write_master(plans, ["demo"])
    plan_file = _write_plan(plans, "demo", "shipped", gated=True)

    lc_file = tmp_path / "lc.jsonl"
    lc_file.write_text('{"slug": "demo", "outcome": "fail"}\n')

    # CWD must NOT be a git repo — _resolve_project_root falls back to CWD.
    non_git = tmp_path / "non-git-cwd"
    non_git.mkdir()

    script = _source_driver(env) + textwrap.dedent(f"""\
        set +eE +o pipefail
        test_ship_integrity "{plans}" "{lc_file}" "" "" 2>/dev/null
        echo "exit:$?"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(non_git),
    )

    status_after = plan_file.read_text().split("status:")[1].splitlines()[0].strip()
    assert status_after == "in-progress", (
        f"AC-2: expected in-progress (reverted), got {status_after}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ── AC-3: real git repo ⇒ unchanged behaviour (control) ─────────────────────

def test_real_repo_reverts_on_red_gate(tmp_path: Path):
    """AC-3: with a real git repo as PROJECT_PATH, a red gate reverts as usual."""
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "ILK_DATA_HOME": str(home / ".ilk-data")}

    plans = home / ".ilk-data" / "projects" / "test" / "plans"
    plans.mkdir(parents=True)
    _write_master(plans, ["demo"])
    plan_file = _write_plan(plans, "demo", "shipped", gated=True)

    lc_file = tmp_path / "lc.jsonl"
    lc_file.write_text('{"slug": "demo", "outcome": "fail"}\n')

    # Create a real git repo for PROJECT_PATH
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, timeout=10)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo, capture_output=True, timeout=10,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )

    script = _source_driver(env) + textwrap.dedent(f"""\
        set +eE +o pipefail
        PROJECT_PATH="{repo}"
        test_ship_integrity "{plans}" "{lc_file}" "" "" 2>/dev/null
        echo "exit:$?"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(repo),
    )

    status_after = plan_file.read_text().split("status:")[1].splitlines()[0].strip()
    assert status_after == "in-progress", (
        f"AC-3: expected in-progress (reverted), got {status_after}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ── AC-4: ship_integrity.py exit 1 when root unresolvable + gate red ─────────

def test_ship_integrity_exit1_unresolvable_with_red_gate(tmp_path: Path):
    """AC-4: ship_integrity.py with unresolvable root + --gate-passed false ⇒ exit 1."""
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_plan(plans, "demo", "shipped", gated=True)

    # No git repo anywhere near plans dir — use non-git cwd
    non_git = tmp_path / "non-git"
    non_git.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "ship_integrity.py"),
         "--subplan", str(plans / "2026-01-01-demo.md"),
         "--gate-passed", "false"],
        capture_output=True, text=True, timeout=30,
        cwd=str(non_git),
    )
    assert result.returncode == 1, (
        f"AC-4: expected exit 1, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )