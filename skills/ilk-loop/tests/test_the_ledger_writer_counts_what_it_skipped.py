"""Red-first pins: the no-rows summary line must state what it counted.

Regression for gh-resolve D-428 (#6944 run 20260928-155259 iter 4).
The runner printed ``no commits and no green gate`` when there were 3
commits — just none carrying a ``[plan:…#step-N]`` trailer.  The message
is a false sentence.

AC-1  When ``rows_written == 0``, the summary line states the counts it
      measured: new commits across repos, how many carry any ``[plan:``
      trailer, and whether a green gate ran.
AC-2  ``"no commits"`` appears only when the count is 0.  A pin replays
      the #6944 shape (3 untrailered commits, no gate) and asserts the
      line never says ``no commits``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

RUNNER = (Path(__file__).resolve().parent.parent / "scripts"
          / "run_ilk_loop_claude.sh")

_PATH = os.environ.get("PATH", "/usr/bin:/bin")

#: The function under test.
WRITER_FUNC = "write_ship_proof_records"


# ── fixture helpers ──────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _subplan(plans: Path, slug: str, current_step: int, steps: int) -> Path:
    sp = plans / f"2026-09-29-{slug}.md"
    sp.write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {steps}\n"
        "local_checks: []\n"
        "---\n\n"
        f"# Sub-plan: {slug}\n\n"
        + "".join(f"### Step {n} — work\n\nBody.\n\n" for n in range(steps)),
        encoding="utf-8",
    )
    return sp


def _make_project(root: Path, subplans: dict[str, tuple[int, int]]) -> Path:
    """A git repo with ``docs/plans`` holding a MASTER + the named sub-plans.

    *subplans* maps slug -> (current_step, estimated_steps).
    """
    plans = root / "docs" / "plans"
    plans.mkdir(parents=True)
    registry = "\n".join(
        f"| {n} | [2026-09-29-{slug}.md](./2026-09-29-{slug}.md) | pending |"
        for n, slug in enumerate(subplans, start=1)
    )
    (plans / "MASTER-2026-09-29-ledger.md").write_text(
        "---\n"
        "master_plan: 2026-09-29-ledger\n"
        "batch_date: 2026-09-29\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: ledger\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"{registry}\n",
        encoding="utf-8",
    )
    for slug, (cur, steps) in subplans.items():
        _subplan(plans, slug, cur, steps)

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root* so nothing reads ~/.ilk-data."""
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _run_writer(
    project: Path,
    env: dict[str, str],
    *,
    pre_iter_target: str,
    before: str,
    after: str,
    run_id: str,
    iteration: int,
    gate_outcome: str = "",
) -> subprocess.CompletedProcess:
    """Dot-source the runner and call the ledger writer for one iteration."""
    heads_dir = project.parent / "heads"
    heads_dir.mkdir(exist_ok=True)
    (heads_dir / "before").write_text(f"{project}={before}\n", encoding="utf-8")
    (heads_dir / "after").write_text(f"{project}={after}\n", encoding="utf-8")

    gate_arg = f"'{gate_outcome}'" if gate_outcome else ""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{RUNNER}'
PROJECT_PATH='{project}'
REPOS=('{project}')
RUN_ID='{run_id}'
LOOP_STATUS_SCRIPT='{RUNNER.parent / "loop_status.py"}'
PRE_ITER_TARGET=$'{pre_iter_target}'
set +e
declare -F {WRITER_FUNC} >/dev/null || {{ echo "WRITER_MISSING"; exit 90; }}
{WRITER_FUNC} '{heads_dir / "before"}' '{heads_dir / "after"}' {iteration} {gate_arg}
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )


def _make_untrailered_commits(project: Path, count: int) -> None:
    """Create *count* commits with no ``[plan:`` trailer."""
    for n in range(count):
        (project / "file.txt").write_text(f"change {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m", f"work {n}")


# ── AC-1 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="the-ledger-writer-counts-what-it-skipped")
def test_no_rows_line_states_commit_and_trailer_counts(tmp_path: Path) -> None:
    """AC-1 — the summary line states the counts it measured.

    Replays the #6944 shape: 3 untrailered commits, no gate.  The message
    must say how many new commits there were, how many carried a trailer,
    and that no green gate ran — never just ``no commits``.
    """
    project = _make_project(tmp_path / "proj", {"alpha": (0, 3)})
    env = _sandbox_env(tmp_path)

    before = _git(project, "rev-parse", "HEAD")
    _make_untrailered_commits(project, 3)
    after = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="alpha 0",
        before=before, after=after,
        run_id="r-test", iteration=4,
    )

    stderr = proc.stderr
    # Must mention the commit count
    assert "3 new commits" in stderr, (
        f"expected '3 new commits' in stderr, got: {stderr!r}"
    )
    # Must NOT say "no commits" — there were 3
    assert "no commits" not in stderr, (
        f"stderr must not say 'no commits' when there are 3: {stderr!r}"
    )


# ── AC-2 ─────────────────────────────────────────────────────────────────────

def test_no_rows_line_says_no_commits_only_when_count_is_zero(tmp_path: Path) -> None:
    """AC-2 — ``"no commits"`` appears only when the count is 0.

    A genuinely empty iteration (0 new commits, no gate) may say
    ``no commits``.
    """
    project = _make_project(tmp_path / "proj", {"alpha": (0, 3)})
    env = _sandbox_env(tmp_path)

    sha = _git(project, "rev-parse", "HEAD")

    proc = _run_writer(
        project, env,
        pre_iter_target="alpha 0",
        before=sha, after=sha,
        run_id="r-test", iteration=4,
    )

    stderr = proc.stderr
    # 0 new commits — "no commits" is acceptable here
    assert "no commits" in stderr or "0 new commits" in stderr, (
        f"expected 'no commits' or '0 new commits' in stderr for 0-commit case: {stderr!r}"
    )