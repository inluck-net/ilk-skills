"""Red-first: the ledger must not fill a gap in a history that carries trailers.

Measured 2026-09-08 on batch ``2026-09-08c-write-path-integrity``.  Sub-plan
``a-productive-timeout-is-not-a-barren-one`` carried trailers for steps 0, 1
and 3, had **no** commit for step 2 and **no** ``#ship`` commit anywhere
(``git log --all --grep`` for both: 0 results), reported
``status: shipped`` — and ``ship_audit.py --subplan`` returned
``{'proven': True, 'missing_steps': [], 'final_gate': 'pass'}``.

Two independent causes, one per test below:

  1. The ledger union (``ship_audit.check_step_commits``) trusted a record
     claiming ``step_from: 0, step_to: 4`` whose own ``commits`` list held 3
     shas.  The record was not lying on its own terms — the driver derives
     that range from the worker's self-reported ``current_step`` — which is
     precisely why the range cannot serve as proof of a commit.  The ledger
     exists for the SHARED-REMOTE case, where SKILL.md's trailer policy
     strips ``[plan:<slug>#step-N]``; both of its authored ACs build their
     fixture with ``trailers=False``
     (``test_ship_proof_ledger_attribution.py``).  When a slug does carry
     trailers, a step without one is a real gap.

  2. A ``#ship`` trailer only ever *satisfied* ``max(authored)`` when
     present.  Nothing required a sub-plan reporting ``shipped`` to have one,
     so an absent ship marker cost nothing whenever the final authored step
     happened to be committed under its own trailer.

The third test pins the shared-remote path these fixes must not break.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import ship_audit


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _project(
    root: Path,
    *,
    slug: str = "gate-work",
    steps: int = 4,
    trailered_steps: "set[int] | None" = None,
    ship_marker: bool = False,
) -> "tuple[Path, list[str]]":
    """A repo with one commit per step; *trailered_steps* get a plan trailer.

    ``trailered_steps=None`` means every step carries one; an empty set means
    none do (the shared-remote shape).
    """
    if trailered_steps is None:
        trailered_steps = set(range(steps))
    project = root / "proj"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "README").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    shas: "list[str]" = []
    for n in range(steps):
        (project / f"file{n}.txt").write_text(f"step {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        msg = f"fix(app): step {n} work"
        if n in trailered_steps:
            msg += f" [plan:{slug}#step-{n}]"
        _git(project, "commit", "-q", "-m", msg)
        shas.append(_git(project, "rev-parse", "HEAD"))

    if ship_marker:
        _git(project, "commit", "-q", "--allow-empty", "-m",
             f"chore(plans): {slug} shipped [plan:{slug}#ship]")
        shas.append(_git(project, "rev-parse", "HEAD"))
    return project, shas


_BODY = "# Sub-plan\n\n" + "".join(
    f"### Step {n} — work\n\nBody.\n\n" for n in range(4)
)


def test_ledger_cannot_fill_a_gap_in_a_trailered_history(tmp_path: Path) -> None:
    """The real 2026-09-08 case: trailers for 0/1/3, none for 2, ledger [0,4)."""
    project, shas = _project(
        tmp_path, steps=4, trailered_steps={0, 1, 3}, ship_marker=False,
    )

    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1, 2, 3], cwd=project,
    )
    assert missing == [2], (
        "precondition: trailer matching alone must see exactly the step-2 gap. "
        f"Got present={present} missing={missing}"
    )

    # The record the driver actually wrote: a 4-step range, 3 commits.
    ledger = [{
        "run_id": "20260908-122425", "iteration": 1, "slug": "gate-work",
        "repo": str(project), "step_from": 0, "step_to": 4,
        "commits": [shas[0], shas[1], shas[2]],
    }]
    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1, 2, 3], cwd=project, ledger_records=ledger,
    )
    assert missing == [2], (
        "a ledger range must not attribute a step in a history that carries "
        "trailers for this slug — step 2 has no commit anywhere. "
        f"Got present={present} missing={missing}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN GAP, not yet fixed: `proven` means 'a commit per authored "
        "step', and a #ship trailer only satisfies max(authored) when it "
        "happens to be present. Requiring one is a CONTRACT CHANGE — 8 tests "
        "across test_ship_audit.py, test_ship_audit_reads_record.py and "
        "test_ship_proof_ledger_attribution.py assert PROVEN for a trailered "
        "sub-plan with no ship marker — so it is left for a human to decide "
        "rather than bundled into the ledger fix. Measured 2026-09-08: "
        "a-productive-timeout-is-not-a-barren-one reported status: shipped "
        "with 0 #ship commits across all refs."
    ),
)
def test_shipped_status_requires_a_ship_marker(tmp_path: Path) -> None:
    """Every authored step committed, no #ship commit — must not audit PROVEN."""
    project, _ = _project(
        tmp_path, steps=4, trailered_steps={0, 1, 2, 3}, ship_marker=False,
    )
    result = ship_audit.audit_ship(
        status="shipped", body=_BODY, declared_checks=[],
        gate_passed="unknown", slug="gate-work", cwd=project,
    )
    assert result["missing_steps"] == [], (
        "precondition: every authored step carries its own trailer. "
        f"Got {result['missing_steps']}"
    )
    assert not result["proven"], (
        "a sub-plan reporting `shipped` with no [plan:<slug>#ship] commit "
        f"anywhere must not audit PROVEN. Got {result}"
    )
    assert any("ship" in r for r in result["reasons"]), (
        f"the reason must name the absent ship marker. Got {result['reasons']}"
    )

    # And with the marker, the same sub-plan audits PROVEN.
    project2, _ = _project(
        tmp_path / "b", steps=4, trailered_steps={0, 1, 2, 3}, ship_marker=True,
    )
    ok = ship_audit.audit_ship(
        status="shipped", body=_BODY, declared_checks=[],
        gate_passed="unknown", slug="gate-work", cwd=project2,
    )
    assert ok["proven"], f"a template-conformant ship must stay PROVEN. Got {ok}"


def test_shared_remote_ledger_attribution_is_unchanged(tmp_path: Path) -> None:
    """Regression guard for the ledger's actual purpose (AC-3/AC-4).

    A trailerless history is the shared-remote case: trailer matching
    attributes nothing, so the ledger is the only evidence and must still
    attribute the whole range — including the 1-commit-covers-3-steps shape
    that ``test_ship_proof_ledger_attribution.py`` pins.
    """
    project, shas = _project(
        tmp_path, steps=4, trailered_steps=set(), ship_marker=False,
    )
    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1, 2, 3], cwd=project,
    )
    assert missing == [0, 1, 2, 3], (
        f"precondition: no trailers, so nothing is attributed. Got {present}"
    )

    ledger = [{
        "run_id": "20260829-120000", "iteration": 1, "slug": "gate-work",
        "repo": str(project), "step_from": 0, "step_to": 4,
        "commits": [shas[0]],
    }]
    present, missing = ship_audit.check_step_commits(
        "gate-work", [0, 1, 2, 3], cwd=project, ledger_records=ledger,
    )
    assert (present, missing) == ([0, 1, 2, 3], []), (
        "on a shared remote the ledger is the only proof there is and must "
        f"still cover its range. Got present={present} missing={missing}"
    )
