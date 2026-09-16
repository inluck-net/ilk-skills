"""The step-commit check resolves the project root from the plans dir.

The real invocation from ``run_ilk_loop_claude.sh:3008`` is:

    test_ship_integrity "$(get_plans_dir)" "$local_checks_results"

where the plans dir lives under ``~/.ilk-data/projects/<key>/plans/`` —
deliberately not a git repo.  ``ship_integrity`` probes
``git rev-parse --is-inside-work-tree`` with ``cwd=Path.cwd()``, which
resolves from the process's working directory, not from the sub-plan's
location.  If cwd happens to be the plans dir (or anywhere outside the
repo), the probe fails and the check silently skips.

This test exercises the **real call shape**: sub-plan file in a plans
dir that is an ancestor of the repo, process cwd set to a directory
that is not a git work tree.  The step-commit check must **run** (not
skip) and report a violation for the missing step.

**AC-2** — the load-bearing test.  It must FAIL until
``_missing_step_reason`` resolves the project root via ``ilk_paths``
(``find_project_root`` / ``git_root``) instead of trusting
``Path.cwd()``.

**AC-3** — pinned here too: when the project root genuinely cannot be
resolved, behaviour is unchanged (warn + no violation).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CLI = SCRIPTS / "ship_integrity.py"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout.strip()


def _make_repo(
    tmp: Path,
    *,
    commits: list[str] | None = None,
    name: str = "repo",
) -> Path:
    """A git repo with an initial commit and optional additional commits."""
    repo = tmp / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                   capture_output=True)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    for msg in (commits or []):
        _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return repo


def _write_subplan(
    plans_dir: Path,
    slug: str,
    *,
    n_steps: int = 2,
    status: str = "shipped",
) -> Path:
    """Write a sub-plan with ``n_steps`` authored steps under ``## Steps``."""
    steps_section = "\n".join(
        textwrap.dedent(f"""\

            ### Step {n} — do thing {n}

            ```yaml
            local_checks:
              - command: python3 -c "pass"
                timeout: 60
            ```
            - work
        """)
        for n in range(n_steps)
    )
    body = (
        f"---\nplan: {slug}\nstatus: {status}\n"
        f"current_step: {n_steps}\nestimated_steps: {n_steps}\n"
        f"local_checks: []\n---\n\n"
        f"# {slug}\n\n## Steps\n{steps_section}\n"
    )
    sp = plans_dir / f"{slug}.md"
    sp.write_text(body, encoding="utf-8")
    return sp


def _run_cli(
    subplan: Path,
    *,
    cwd: Path,
    gate: str = "true",
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke ``ship_integrity.py --subplan`` with a controlled cwd."""
    env = os.environ.copy()
    # Isolate from the real user's ilk-data so resolution walks are bounded.
    env["ILK_DATA_HOME"] = str(cwd.parent / "ilk-data-isolated")
    env["ILK_SKILL_HOME"] = str(SCRIPTS.parent.parent)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(CLI), "--subplan", str(subplan),
         "--gate-passed", gate],
        capture_output=True, text=True, timeout=60, cwd=cwd,
        encoding="utf-8", env=env,
    )


# ── AC-2: the load-bearing test ──────────────────────────────────────────────

class TestAC2StepCommitCheckRunsOutsideRepo:
    """The step-commit check must RUN when the cwd is not the repo.

    The plans dir is an ancestor of the repo (in-tree layout:
    ``<repo>/docs/plans/``).  The fix resolves the project root by
    walking up from the sub-plan's parent via ``ilk_paths.git_root``.
    Today the check prints a warning and returns None because it trusts
    ``Path.cwd()``.
    """

    def test_missing_step_detected_when_cwd_is_not_the_repo(
        self, tmp_path: Path,
    ) -> None:
        """AC-2: argv = in-tree plans dir, cwd = a non-repo directory.

        A sub-plan with two authored steps where only step 0 has a commit.
        The check must report a violation naming step 1.
        """
        repo = _make_repo(
            tmp_path,
            commits=["feat: step 0 [plan:check-runs#step-0]"],
        )
        # In-tree plans dir: repo/docs/plans/ — an ancestor of .git.
        plans = repo / "docs" / "plans"
        plans.mkdir(parents=True)
        sp = _write_subplan(plans, "check-runs", n_steps=2)

        # cwd is a third directory, not a git repo.
        alien = tmp_path / "alien-cwd"
        alien.mkdir()

        r = _run_cli(sp, cwd=alien)
        combined = (r.stdout + r.stderr).lower()
        assert r.returncode != 0, (
            "the step-commit check skipped instead of reporting a violation. "
            "stdout={!r} stderr={!r}".format(r.stdout, r.stderr)
        )
        assert "step" in combined, (
            "the violation must name the missing step. Got: {!r}".format(combined)
        )
        assert "1" in combined, (
            "step 1 is missing but was not reported. Got: {!r}".format(combined)
        )


# ── AC-3: genuinely unresolvable root stays fail-open ────────────────────────

class TestAC3UnresolvableRootFailsOpen:
    """When no project root can be found, warn and return no violation.

    This is the fail-open path that must remain: the check cannot run,
    so it must not block.  A later change that flips this to fail-closed
    will break this test — that is the point of pinning it now.
    """

    def test_no_git_repo_anywhere_does_not_block(self, tmp_path: Path) -> None:
        """Plans dir + cwd are both outside any git repo.

        pytest's tmp_path has a .git at its root, and our pure-Python
        git_root() walks up ignoring GIT_CEILING_DIRECTORIES.  We place
        plans/cwd outside the pytest tree entirely (under /tmp) and set
        GIT_CEILING_DIRECTORIES to the cwd's parent so the subprocess
        probe also cannot walk into the pytest repo.
        """
        import tempfile
        iso = Path(tempfile.mkdtemp(prefix="ilk-ac3-"))
        try:
            plans = iso / "plans"
            plans.mkdir()
            sp = _write_subplan(plans, "orphan-plan", n_steps=2)

            cwd_dir = iso / "cwd"
            cwd_dir.mkdir()

            # GIT_CEILING_DIRECTORIES stops traversal ABOVE the ceiling,
            # but the cwd itself is still checked.  Setting the ceiling
            # to cwd's parent means git stops before cwd (and below) —
            # so it never finds any .git above the isolated tree.
            ceilings = str(cwd_dir.parent.resolve())
            r = _run_cli(sp, cwd=cwd_dir, env_overrides={
                "HOME": str(iso / "fake-home"),
                "GIT_CEILING_DIRECTORIES": ceilings,
            })
            assert r.returncode == 0, (
                "when no project root can be resolved, ship-integrity must "
                "fail open (return 0). Got stdout={!r} stderr={!r}".format(
                    r.stdout, r.stderr,
                )
            )
        finally:
            import shutil
            shutil.rmtree(iso, ignore_errors=True)

    def test_unresolvable_root_warns_could_not_resolve(self, tmp_path: Path) -> None:
        """AC-4: 'could not resolve a project root' when no root found."""
        import tempfile
        iso = Path(tempfile.mkdtemp(prefix="ilk-ac3-warn-"))
        try:
            plans = iso / "plans"
            plans.mkdir()
            sp = _write_subplan(plans, "quiet-orphan", n_steps=2)

            cwd_dir = iso / "cwd"
            cwd_dir.mkdir()

            ceilings = str(cwd_dir.parent.resolve())
            r = _run_cli(sp, cwd=cwd_dir, env_overrides={
                "HOME": str(iso / "fake-home"),
                "GIT_CEILING_DIRECTORIES": ceilings,
            })
            assert "could not resolve" in r.stderr.lower(), (
                "an unresolvable root must say 'could not resolve'. "
                "stderr={!r}".format(r.stderr)
            )
        finally:
            import shutil
            shutil.rmtree(iso, ignore_errors=True)


# ── AC-4: two distinct warning messages ──────────────────────────────────────

class TestAC4DistinctWarnings:
    """The warning text distinguishes 'could not resolve' from 'not a git
    work tree' — they send a reader to different places.

    The 'not a git work tree' message triggers when a resolution succeeds
    (a .git ancestor is found) but git rev-parse --is-inside-work-tree
    fails (bare repo, corrupted work tree, etc.).  In practice this is
    hard to trigger in tests because git's work-tree check searches
    upward and finds a valid work tree ancestor.  We verify the message
    strings exist in the code instead of constructing a fragile filesystem
    scenario.
    """

    def test_code_emits_both_distinct_messages(self) -> None:
        """AC-4: both warning strings are present and distinct in the source."""
        src = (SCRIPTS / "ship_integrity.py").read_text(encoding="utf-8")
        assert "could not resolve" in src.lower(), (
            "the 'could not resolve' message was removed from ship_integrity.py"
        )
        # The "not a git work tree" message spans a line break in the source,
        # so join lines before checking.
        joined = " ".join(src.lower().split())
        assert "not a git work tree" in joined, (
            "the 'not a git work tree' message was removed from ship_integrity.py"
        )


# ── sanity: when cwd IS the repo, it still works (regression guard) ──────────

class TestSanityCwdInRepo:
    """The happy path must not break.  This is the shape that works today."""

    def test_cwd_is_repo_still_detects_missing_step(
        self, tmp_path: Path,
    ) -> None:
        repo = _make_repo(
            tmp_path,
            commits=["feat: step 0 [plan:sanity#step-0]"],
        )
        plans = repo / "docs" / "plans"
        plans.mkdir(parents=True)
        sp = _write_subplan(plans, "sanity", n_steps=2)

        r = _run_cli(sp, cwd=repo)
        assert r.returncode != 0, (
            "when cwd IS the repo, the check must still detect the missing step. "
            "stdout={!r} stderr={!r}".format(r.stdout, r.stderr)
        )
