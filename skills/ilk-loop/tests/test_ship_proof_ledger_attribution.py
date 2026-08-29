"""Red-first: the audit must attribute steps from the ledger, not only trailers.

``ship_audit.check_step_commits`` matches a step's commit only by the
``[plan:<slug>#step-N]`` trailer.  ``SKILL.md``'s shared-remote policy strips
that trailer, so on a shared remote the audit reports
``0 of N authored steps committed`` for a sub-plan whose commits are all
present.  The cost is not the noise: an always-on warning is an ignored
warning, and on run ``20260828-211346`` the worker correctly reasoned the flag
away while 3 of 3 declared gates were red.

These three tests cover the READER half:

  AC-3  a step is committed if a trailer matches **or** a ledger record covers
        it — union semantics, trailer matching unchanged
  AC-4  a shared-remote sub-plan with real commits and no trailers audits
        PROVEN through ``ship_audit.py``'s **CLI**, which is the entry point
        the runner and ``/ilk-ship`` Phase 0 actually invoke
  AC-6  an absent / empty / truncated / malformed ledger degrades to
        trailer-only and never raises

Sub-plan: ``a-shared-remote-ship-can-be-proven`` (AC-3, AC-4, AC-6).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import ship_audit

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SHIP_AUDIT = SCRIPTS / "ship_audit.py"

_PATH = os.environ.get("PATH", "/usr/bin:/bin")


# ── fixture helpers ──────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _sandbox_env(root: Path) -> dict[str, str]:
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
    }


def _launcher_dir(project: Path, env: dict[str, str]) -> Path:
    proc = subprocess.run(
        ["python3", str(SCRIPTS / "ilk_paths.py"), "--start", str(project)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return Path(json.loads(proc.stdout)["external_launcher_dir"])


def _shipped_project(
    root: Path,
    *,
    slug: str = "gate-work",
    steps: int = 3,
    trailers: bool,
) -> tuple[Path, Path, list[str]]:
    """A shared-remote repo with a `shipped` sub-plan and one commit per step.

    Returns ``(project, subplan_path, shas)``.  With *trailers* false the
    commit messages carry nothing about the plan — the shared-remote case.
    """
    project = root / "proj"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-08-29-ledger.md").write_text(
        "---\n"
        "master_plan: 2026-08-29-ledger\n"
        "batch_date: 2026-08-29\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: ledger\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"| 1 | [2026-08-29-{slug}.md](./2026-08-29-{slug}.md) | shipped |\n",
        encoding="utf-8",
    )
    sp = plans / f"2026-08-29-{slug}.md"
    sp.write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: shipped\n"
        f"current_step: {steps}\n"
        f"estimated_steps: {steps}\n"
        "local_checks: []\n"
        "---\n\n"
        f"# Sub-plan: {slug}\n\n"
        + "".join(f"### Step {n} — work\n\nBody.\n\n" for n in range(steps)),
        encoding="utf-8",
    )
    (project / ".ilk-remote-type").write_text("shared\n", encoding="utf-8")

    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    shas: list[str] = []
    for n in range(steps):
        (project / f"file{n}.txt").write_text(f"step {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        msg = f"fix(app): step {n} work"
        if trailers:
            msg += f" [plan:{slug}#step-{n}]"
        _git(project, "commit", "-q", "-m", msg)
        shas.append(_git(project, "rev-parse", "HEAD"))
    return project, sp, shas


def _write_ledger(project: Path, env: dict[str, str], records: list[dict]) -> Path:
    d = _launcher_dir(project, env)
    d.mkdir(parents=True, exist_ok=True)
    ledger = d / "ship-proof.jsonl"
    ledger.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records),
        encoding="utf-8",
    )
    return ledger


def _audit_cli(project: Path, subplan: Path, env: dict[str, str]):
    return subprocess.run(
        ["python3", str(SHIP_AUDIT), "--subplan", str(subplan),
         "--project", str(project)],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(project),
    )


# ── AC-3 ─────────────────────────────────────────────────────────────────────

def test_ledger_record_attributes_steps_the_trailer_cannot(tmp_path: Path) -> None:
    """AC-3 — union semantics over the half-open ``[step_from, step_to)`` range.

    Trailer matching is unchanged; the ledger only ever *adds* attribution.
    Asserted at the function level so the range arithmetic (half-open, and
    slug-scoped) is pinned independently of the CLI wiring in AC-4.
    """
    project, _, _ = _shipped_project(tmp_path, steps=3, trailers=False)

    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1, 2], cwd=project,
    )
    assert missing == [0, 1, 2], (
        "precondition: without a ledger, a trailerless shared-remote history "
        f"attributes nothing. Got present={present} missing={missing}"
    )

    full = [{
        "run_id": "20260829-120000", "iteration": 1, "slug": "gate-work",
        "repo": str(project), "step_from": 0, "step_to": 3, "commits": ["abc1234"],
    }]
    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1, 2], cwd=project, ledger_records=full,
    )
    assert (present, missing) == ([0, 1, 2], []), (
        f"a record covering [0, 3) must attribute steps 0, 1 and 2; "
        f"got present={present} missing={missing}"
    )

    partial = [dict(full[0], step_to=2)]
    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1, 2], cwd=project, ledger_records=partial,
    )
    assert (present, missing) == ([0, 1], [2]), (
        "the range is half-open: [0, 2) covers steps 0 and 1 only. A closed "
        f"range would prove a step no iteration reached. Got {present} / {missing}"
    )

    other = [dict(full[0], slug="a-different-subplan")]
    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1, 2], cwd=project, ledger_records=other,
    )
    assert missing == [0, 1, 2], (
        "a record for another slug must attribute nothing to this one; "
        f"got present={present}"
    )


# ── AC-4 ─────────────────────────────────────────────────────────────────────

def test_shared_remote_subplan_audits_proven_through_the_cli(tmp_path: Path) -> None:
    """AC-4 — end-to-end through the entry point the runner actually calls.

    The ledger is only worth anything if it reaches ``ship_audit.py``'s CLI;
    a unit test on ``check_step_commits`` would leave that unproven, which is
    the same class of gap this batch exists to close.
    """
    env = _sandbox_env(tmp_path)
    project, subplan, shas = _shipped_project(tmp_path, steps=3, trailers=False)

    before = _audit_cli(project, subplan, env)
    assert before.returncode == 1, (
        "precondition: with no ledger and no trailers this sub-plan must audit "
        f"UNPROVEN.\nrc={before.returncode}\n{before.stdout}\n{before.stderr}"
    )
    assert "missing commit for steps 0, 1, 2" in (before.stdout + before.stderr), (
        "precondition: the unproven reason must be the missing step commits, "
        f"not something else.\n{before.stdout}\n{before.stderr}"
    )

    _write_ledger(project, env, [{
        "run_id": "20260829-120000", "iteration": 1, "slug": "gate-work",
        "repo": str(project), "step_from": 0, "step_to": 3, "commits": shas,
    }])

    after = _audit_cli(project, subplan, env)
    assert after.returncode == 0, (
        "a shipped sub-plan on a shared remote, with real commits and a ledger "
        "record covering every step, must audit PROVEN through the CLI.\n"
        f"rc={after.returncode}\nstdout: {after.stdout}\nstderr: {after.stderr}"
    )
    assert "PROVEN" in after.stdout


# ── AC-6 ─────────────────────────────────────────────────────────────────────

def test_unreadable_ledger_degrades_to_trailers_and_never_raises(
    tmp_path: Path,
) -> None:
    """AC-6 — a broken ledger must not turn a proven ship into an unproven one.

    Two halves, because either alone is satisfiable by doing nothing: the
    reader must actually tolerate each malformed shape (so a ledger feature
    exists at all), and the CLI must still prove a trailered sub-plan while
    such a ledger sits on disk.
    """
    import ship_proof_ledger  # imported here: absent until step 1 lands

    env = _sandbox_env(tmp_path)
    project, subplan, _ = _shipped_project(tmp_path, steps=3, trailers=True)
    ledger_dir = _launcher_dir(project, env)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger = ledger_dir / "ship-proof.jsonl"

    good = json.dumps({
        "run_id": "r", "iteration": 1, "slug": "gate-work", "repo": str(project),
        "step_from": 0, "step_to": 1, "commits": ["abc1234"],
    }, separators=(",", ":"))

    shapes = {
        "absent": None,
        "empty": "",
        "whitespace-only": "\n\n",
        "truncated mid-line": good[:len(good) // 2],
        "malformed json": "{not json at all}\n",
        "a valid line then a truncated one": good + "\n" + good[:12],
        "not an object": "[1, 2, 3]\n",
    }
    for name, content in shapes.items():
        if content is None:
            ledger.unlink(missing_ok=True)
        else:
            ledger.write_text(content, encoding="utf-8")

        records = ship_proof_ledger.read_records(ledger)
        assert isinstance(records, list), (
            f"read_records must return a list for the {name!r} ledger, got "
            f"{records!r} — an unreadable ledger degrades, it does not raise"
        )
        assert all(isinstance(r, dict) for r in records), (
            f"read_records leaked a non-record from the {name!r} ledger: {records!r}"
        )

        proc = _audit_cli(project, subplan, env)
        assert "Traceback" not in proc.stderr, (
            f"the {name!r} ledger produced a traceback from the audit CLI:\n"
            f"{proc.stderr}"
        )
        assert proc.returncode == 0, (
            f"the {name!r} ledger downgraded a sub-plan whose trailers already "
            f"prove every step.\nrc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        )
