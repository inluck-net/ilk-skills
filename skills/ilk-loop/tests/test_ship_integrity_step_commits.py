"""A `shipped` status must be backed by a commit for every authored step.

/ilk-ship Phase 0, 2026-08-26.  Three sub-plans in one batch were marked
``shipped`` with steps that never happened:

  every-hook-is-registered              steps 0,1 committed of 4  → shipped
  an-expensive-gate-is-flagged...       steps 0,1 committed of 4  → shipped
                                        (and no #ship commit at all)
  a-unit-test-cannot-touch-the-host     steps 0,1,2 committed of 5 → shipped

How they got past execution, traced rather than assumed:

1. Nothing in the runner ever WRITES ``status: shipped``.  Every occurrence
   in run_ilk_loop_claude.sh reads it; line 1285 writes shipped→in-progress
   on violation.  The worker agent edits its own plan file and declares
   itself done.
2. ``evaluate_ship`` asks only "was the gate green?".  Measured against the
   real sub-plan: ``evaluate_ship('shipped', <4 declared gates>,
   {'all_passed': True})`` → ``ok=True, 'gate green — ship is honest'``.
   ONE green gate authorises shipped no matter how many steps were authored.
3. Step-commit counting lived solely in ship_audit.py, which runs at
   /ilk-ship Phase 0 — after the batch is over.  ``check_step_commits`` was
   imported nowhere in the loop.

So the per-step gate is real, but it gates *the step the agent says it is
on*; nothing compared intent against evidence until release time.

This closes that gap in the loop, reusing ship_audit's counters rather than
reimplementing them — two readers of "which steps are done" that can drift
is the failure mode decomposition-principles §8 already documents.

**The hazard this must not reintroduce.**  On 2026-08-20 an over-broad
ship-integrity enforcement reverted 69 of 150 sub-plans in one run.  The
runner guards against that by skipping any sub-plan whose gate did not run
this iteration (run_ilk_loop_claude.sh, `continue` when gate_passed is
neither true nor false).  That scoping sits UPSTREAM of this check, so a
prior run's ship is never re-litigated — and AC-6 pins it.
"""
from __future__ import annotations

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


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    return repo


def _subplan(tmp: Path, slug: str, *, steps: int, status: str = "shipped") -> Path:
    """A sub-plan authoring `steps` steps, each with its own gate."""
    body = "\n".join(
        textwrap.dedent(f"""\
        ### Step {n} — do thing {n}

        ```yaml
        local_checks:
          - command: python3 -c "pass"
            timeout: 60
        ```
        - work
        """)
        for n in range(steps)
    )
    sp = tmp / f"{slug}.md"
    sp.write_text(
        f"---\nplan: {slug}\nstatus: {status}\ncurrent_step: {steps}\n"
        f"estimated_steps: {steps}\nlocal_checks: []\n---\n\n# {slug}\n\n{body}",
        encoding="utf-8",
    )
    return sp


def _commit_steps(repo: Path, slug: str, steps, *, ship: bool = False) -> None:
    for n in steps:
        _git(repo, "commit", "-q", "--allow-empty",
             "-m", f"feat: step {n} [plan:{slug}#step-{n}]")
    if ship:
        _git(repo, "commit", "-q", "--allow-empty",
             "-m", f"chore(plans): {slug} shipped [plan:{slug}#ship]")


def _run(subplan: Path, repo: Path, gate: str = "true") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), "--subplan", str(subplan), "--gate-passed", gate],
        capture_output=True, text=True, timeout=60, cwd=repo, encoding="utf-8",
    )


# ── AC-1: a complete sub-plan still ships ───────────────────────────────────

class TestAC1CompleteStillShips:
    """Regression guard first — this must not become a blanket refusal."""

    def test_all_steps_committed_and_gate_green_is_ok(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "complete-plan", steps=3)
        _commit_steps(repo, "complete-plan", range(3))
        r = _run(sp, repo)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_ship_trailer_satisfies_the_last_step(self, tmp_path: Path) -> None:
        """A #ship commit stands in for the final step — and only that one."""
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "ship-trailer", steps=3)
        _commit_steps(repo, "ship-trailer", [0, 1], ship=True)
        r = _run(sp, repo)
        assert r.returncode == 0, r.stdout + r.stderr


# ── AC-2: the actual defect ─────────────────────────────────────────────────

class TestAC2MissingStepBlocksShip:

    def test_missing_step_is_a_violation(self, tmp_path: Path) -> None:
        """The measured shape: 2 of 4 steps committed, gate green, shipped."""
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "half-done", steps=4)
        _commit_steps(repo, "half-done", [0, 1])
        r = _run(sp, repo)
        assert r.returncode != 0, (
            "a shipped sub-plan missing 2 of 4 step commits passed "
            f"ship-integrity.  stdout={r.stdout!r}"
        )

    def test_violation_names_the_missing_steps(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "named-gap", steps=4)
        _commit_steps(repo, "named-gap", [0, 1])
        out = (_run(sp, repo).stdout + _run(sp, repo).stderr).lower()
        assert "step" in out and ("2" in out and "3" in out), (
            f"the reason must name which steps are missing.  Got: {out!r}"
        )

    def test_a_ship_trailer_does_not_paper_over_an_earlier_gap(
        self, tmp_path: Path,
    ) -> None:
        """#ship satisfies the LAST step; a hole earlier is still a hole."""
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "early-gap", steps=4)
        _commit_steps(repo, "early-gap", [0], ship=True)   # missing 1 and 2
        r = _run(sp, repo)
        assert r.returncode != 0, (
            "a #ship trailer masked missing middle steps"
        )


# ── AC-3: gate and step failures stay distinguishable ───────────────────────

class TestAC3ReasonsAreDistinct:

    def test_red_gate_still_reported_as_a_gate_problem(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "red-gate", steps=2)
        _commit_steps(repo, "red-gate", range(2))
        out = _run(sp, repo, gate="false")
        assert out.returncode != 0
        assert "gate" in (out.stdout + out.stderr).lower()

    def test_missing_step_with_green_gate_is_not_called_a_gate_problem(
        self, tmp_path: Path,
    ) -> None:
        """Otherwise the operator fixes the wrong thing."""
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "step-only", steps=3)
        _commit_steps(repo, "step-only", [0])
        text = (_run(sp, repo).stdout + _run(sp, repo).stderr).lower()
        assert "step" in text, f"the step gap must be named.  Got: {text!r}"


# ── AC-4 / AC-5: scope — only shipped, only authored ────────────────────────

class TestAC4Scope:

    @pytest.mark.parametrize("status", ["pending", "in-progress"])
    def test_unshipped_subplan_is_not_enforced(
        self, tmp_path: Path, status: str,
    ) -> None:
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, f"not-shipped-{status}", steps=4, status=status)
        r = _run(sp, repo)
        assert r.returncode == 0, (
            f"a {status} sub-plan must not be enforced — it is still being worked on"
        )

    def test_subplan_with_no_authored_steps_is_ok(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "no-steps", steps=0)
        r = _run(sp, repo)
        assert r.returncode == 0


# ── AC-6: the 2026-08-20 mass-revert guard ──────────────────────────────────

class TestAC6NoMassRevert:
    """The enforcement must stay scoped to the current iteration.

    The runner skips any sub-plan whose gate did not run this iteration
    (`continue` when gate_passed is neither "true" nor "false").  That
    scoping is what stops a prior run's ships being re-litigated and
    reverted — 69 of 150 in one run on 2026-08-20.
    """

    def test_unknown_gate_is_not_a_step_violation(self, tmp_path: Path) -> None:
        """`unknown` must not become a new way to mass-revert.

        The runner never passes `unknown` down this path, but if it ever
        does, an incomplete sub-plan must not be reverted on that basis
        alone — that is precisely the over-broad enforcement that caused
        the mass revert.
        """
        repo = _repo(tmp_path)
        sp = _subplan(tmp_path, "unknown-gate", steps=4)
        _commit_steps(repo, "unknown-gate", [0, 1])
        r = _run(sp, repo, gate="unknown")
        combined = (r.stdout + r.stderr).lower()
        assert "step" not in combined or "gate" in combined, (
            "an unknown gate must not be reported as a step-commit violation"
        )

    def test_runner_still_skips_subplans_without_a_current_verdict(self) -> None:
        """Pin the runner-side scoping that makes AC-6 hold."""
        runner = (SCRIPTS / "run_ilk_loop_claude.sh").read_text(encoding="utf-8")
        assert 'if [[ "$gate_passed" != "true" && "$gate_passed" != "false" ]]; then' in runner, (
            "the runner's current-iteration scoping was removed; step-commit "
            "enforcement would now re-litigate every previously shipped "
            "sub-plan (2026-08-20: 69 of 150 reverted)"
        )


# ── AC-7: git must be able to answer before "missing" is believed ───────────

class TestAC7GitUnavailableFailsOpen:
    """`check_step_commits` reports "all missing" when git errors.

    That is right for the release audit, which always runs inside the repo.
    As a LOOP gate it would block every ship wherever git cannot answer —
    the same over-broad shape as the 2026-08-20 mass revert.
    """

    def test_outside_a_git_work_tree_does_not_block(self, tmp_path: Path) -> None:
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        sp = _subplan(tmp_path, "no-repo-here", steps=3)   # zero commits anywhere
        r = subprocess.run(
            [sys.executable, str(CLI), "--subplan", str(sp), "--gate-passed", "true"],
            capture_output=True, text=True, timeout=60, cwd=outside, encoding="utf-8",
        )
        assert r.returncode == 0, (
            "outside a git work tree every step reads as missing, which would "
            f"block every ship.  stdout={r.stdout!r} stderr={r.stderr!r}"
        )

    def test_the_degradation_is_announced_not_silent(self, tmp_path: Path) -> None:
        """Silent fail-open is the class of bug this whole batch is fixing."""
        outside = tmp_path / "not-a-repo2"
        outside.mkdir()
        sp = _subplan(tmp_path, "no-repo-quiet", steps=2)
        r = subprocess.run(
            [sys.executable, str(CLI), "--subplan", str(sp), "--gate-passed", "true"],
            capture_output=True, text=True, timeout=60, cwd=outside, encoding="utf-8",
        )
        assert "step-commit check" in r.stderr.lower(), (
            f"the skipped check must say so.  stderr={r.stderr!r}"
        )
