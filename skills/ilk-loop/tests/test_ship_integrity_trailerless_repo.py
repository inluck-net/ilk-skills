"""ship_integrity is the third reader of the ledger, and it was left out.

`SKILL.md:449-470`: when the remote is **shared**, the worker MUST omit the
`[plan:<slug>#step-N]` trailer, and an absent `.ilk-remote-type` classifies as
`shared`. So on a shared consumer repo there are no trailers by design, and the
ship-proof ledger is the only evidence a step was committed.

`load_ledger_records`'s docstring records this defect between the first two
readers: `main()` passed the records, `loop_status` did not, and "the
disagreement parked correct work". `ship_integrity._missing_step_reason` was
the third reader and also did not pass them, so the only verdict it could reach
on a trailerless repo was "0 of N authored steps committed" -- reverting correct
sub-plans to in-progress and exiting the loop `ship_integrity_violation`.

Measured 2026-09-18 on rezmac by a gh-resolve session: 26 of 149 `last-exit.json`
records in that state, 17 on or after 2026-09-16 -- the day the root-resolution
fix made this check fire instead of silently skip.

Invisible from ilk-skills, whose own `.ilk-remote-type` is `personal`.

AC-1  trailerless repo + ledger row covering the steps -> ships
AC-2  trailerless repo, NO ledger                      -> still a violation
      (fail-closed: absent evidence is not evidence of absence of work,
      but it is not evidence OF the work either -- documented residual)
AC-3  personal repo, missing trailer, ledger present   -> still a violation
      (the union applies only to slugs with no trailer at all)
AC-4  trailerless repo + ledger + RED gate             -> still blocked
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CLI = SCRIPTS / "ship_integrity.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout.strip()


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                   capture_output=True, encoding="utf-8", errors="replace")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    (repo / ".ilk-remote-type").write_text("shared\n", encoding="utf-8")
    return repo


def _subplan(tmp: Path, slug: str, *, steps: int, status: str = "shipped") -> Path:
    # Each step declares a gate: without one, `evaluate_ship` returns
    # "no gate declared -- nothing to enforce" and every assertion about a
    # RED gate is vacuous.
    body = "\n".join(
        f"### Step {n} - do thing {n}\n\n"
        "```yaml\nlocal_checks:\n  - command: python3 -c \"pass\"\n"
        "    timeout: 60\n```\n- work\n"
        for n in range(steps)
    )
    sp = tmp / f"{slug}.md"
    sp.write_text(
        f"---\nplan: {slug}\nstatus: {status}\ncurrent_step: {steps}\n"
        f"estimated_steps: {steps}\nlocal_checks: []\n---\n\n# {slug}\n\n{body}",
        encoding="utf-8",
    )
    return sp


def _ledger(repo: Path, slug: str, step_from: int, step_to: int) -> Path:
    """Write a loop-executed ship-proof row where the runner would put it."""
    # Resolve the path with the toolkit's own resolver, never by hand-rolling
    # the key -- a hand-written path proves the consumer, not the capture.
    sys.path.insert(0, str(SCRIPTS))
    from ilk_paths import external_launcher_dir, resolve_project_key  # noqa: E402

    d = external_launcher_dir(resolve_project_key(repo))
    d.mkdir(parents=True, exist_ok=True)
    f = d / "ship-proof.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "run_id": "20260918-000000", "iteration": 1, "slug": slug,
            "repo": str(repo), "step_from": step_from, "step_to": step_to,
            "commits": ["deadbee"], "provenance": "loop-executed",
        }) + "\n")
    return f


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin BOTH HOME and ILK_DATA_HOME, or neither."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(home / ".ilk-data"))
    return tmp_path


def _run(subplan: Path, repo: Path, gate: str = "true") -> subprocess.CompletedProcess:
    import os
    env = {**os.environ}
    return subprocess.run(
        [sys.executable, str(CLI), "--subplan", str(subplan), "--gate-passed", gate],
        capture_output=True, text=True, timeout=60, cwd=repo, encoding="utf-8", env=env,
    )


class TestLedgerIsRead:
    def test_trailerless_with_ledger_ships(self, sandbox: Path) -> None:
        repo = _repo(sandbox)
        sp = _subplan(sandbox, "trailerless-slug", steps=2)
        _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: step 0")
        _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: step 1")
        _ledger(repo, "trailerless-slug", 0, 2)

        r = _run(sp, repo, gate="true")

        assert "VIOLATION" not in r.stdout + r.stderr, (
            "a shared repo omits trailers by contract; the ledger is the "
            f"evidence and must be read.\nSTDOUT {r.stdout}\nSTDERR {r.stderr}")


class TestGuardStillHolds:
    def test_trailerless_without_ledger_is_still_a_violation(
        self, sandbox: Path
    ) -> None:
        """Documented residual: a hand-executed batch writes 0 ledger rows."""
        repo = _repo(sandbox)
        sp = _subplan(sandbox, "noledger-slug", steps=2)
        r = _run(sp, repo, gate="true")
        assert "VIOLATION" in r.stdout + r.stderr

    def test_personal_repo_missing_trailer_still_violates(
        self, sandbox: Path
    ) -> None:
        repo = _repo(sandbox)
        (repo / ".ilk-remote-type").write_text("personal\n", encoding="utf-8")
        sp = _subplan(sandbox, "personal-slug", steps=2)
        _git(repo, "commit", "-q", "--allow-empty",
             "-m", "feat: step 0 [plan:personal-slug#step-0]")
        _ledger(repo, "personal-slug", 0, 2)

        r = _run(sp, repo, gate="true")

        assert "VIOLATION" in r.stdout + r.stderr, (
            "the union applies only to slugs with NO trailer at all; a repo "
            "that carries trailers must not have gaps papered over by a "
            f"ledger row.\nSTDOUT {r.stdout}\nSTDERR {r.stderr}")

    def test_red_gate_still_blocks(self, sandbox: Path) -> None:
        repo = _repo(sandbox)
        sp = _subplan(sandbox, "redgate-slug", steps=2)
        _ledger(repo, "redgate-slug", 0, 2)
        r = _run(sp, repo, gate="false")
        assert "VIOLATION" in r.stdout + r.stderr
