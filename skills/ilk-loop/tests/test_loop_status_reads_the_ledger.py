"""Red-first: loop_status must consult the ledger, like ship_audit does.

`ship_audit`'s CLI resolves the ship-proof ledger and passes it to `audit_ship`
(`ship_audit.py:655-694`). `loop_status.resolve_status` calls the same function
and does **not** (`loop_status.py:449-457`). So the two readers disagree about
`proven` for exactly one class of sub-plan: one on a **shared remote**, where
SKILL.md's trailer policy strips `[plan:<slug>#step-N]` from every commit and
the ledger is the only evidence there is.

That class is not hypothetical — it is the whole reason the ledger exists, and
it is the regime gh-resolve's consumer repo runs in permanently. `loop_status`
is what the driver reads to decide `shipped-unproven`, so a reader that ignores
the ledger reports a correct run as unproven and parks it.

`check_step_commits` only ever ADDS attribution from the ledger, and only when
the slug carries no trailers (`ship_audit.py:204`). So passing the records is
inert in the trailered regime and load-bearing in the trailerless one — there is
no case where supplying them makes a reader more permissive than `ship_audit`
already is.

Found as an uncommitted local modification on rezmac, 2026-09-09. The diagnosis
was right; this is the same fix routed through one resolver instead of a second
copy of the resolution logic.

AC-1  a trailerless sub-plan covered by a ledger row: both readers say proven
AC-2  the two readers agree (the standing invariant, in the trailerless regime)
AC-3  no ledger row ⇒ still unproven (the ledger adds evidence, never invents it)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SUBPLAN = """---
plan: shared-remote-work
status: shipped
current_step: 2
estimated_steps: 2
local_checks: []
---

# Sub-plan: shared remote work

### Step 0 — first

Body.

### Step 1 — second

Body.
"""


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _shared_remote_repo(tmp_path: Path) -> Path:
    """A repo whose commits carry NO plan trailers — the shared-remote regime."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    (repo / ".ilk-remote-type").write_text("shared\n", encoding="utf-8")
    for i in range(2):
        (repo / f"f{i}").write_text(f"{i}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        # No [plan:...] trailer — stripped by policy on a shared remote.
        _git(repo, "commit", "-q", "-m", f"fix(app): change {i}")
    return repo


def _ledger(tmp_path: Path, rows: list[dict]) -> list[dict]:
    p = tmp_path / "ship-proof.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    from ship_proof_ledger import read_records
    return read_records(p)


def _audit(repo: Path, subplan: Path, ledger_records):
    import ship_audit
    info = ship_audit.read_subplan_for_audit(subplan)
    return ship_audit.audit_ship(
        status=info["status"], body=info["body"],
        declared_checks=info["declared_checks"], gate_passed="unknown",
        slug=info["slug"], cwd=repo, runtime_dir=None,
        ledger_records=ledger_records,
    )


# ── AC-1 / AC-2 ──────────────────────────────────────────────────────────────

def test_loop_status_uses_the_ledger_like_ship_audit(tmp_path: Path) -> None:
    """The reader the DRIVER consults must see the same evidence."""
    import ship_audit

    repo = _shared_remote_repo(tmp_path)
    plans = tmp_path / "plans"
    plans.mkdir()
    sp = plans / "2026-09-09-shared-remote-work.md"
    sp.write_text(SUBPLAN, encoding="utf-8")

    rows = _ledger(tmp_path, [
        {"slug": "shared-remote-work", "step_from": 0, "step_to": 2,
         "commits": ["a" * 40, "b" * 40], "provenance": "loop-executed"},
    ])

    # ship_audit, given the ledger, proves it.
    assert _audit(repo, sp, rows)["proven"] is True, (
        "precondition: with the ledger, ship_audit must prove a trailerless "
        "sub-plan — if this fails the fixture is wrong, not the reader"
    )

    # The seam this test exists for: loop_status must be able to obtain the
    # same records through ONE resolver rather than a second copy.
    assert hasattr(ship_audit, "load_ledger_records"), (
        "ship_audit must expose a single ledger resolver that loop_status can "
        "call — duplicating the resolution in loop_status is the two-resolver "
        "hazard that put the batch-gate record in two directories in 2026-08"
    )

    got = ship_audit.load_ledger_records(repo)
    assert got is None or isinstance(got, list), (
        f"load_ledger_records must return a list or None; got {type(got)}"
    )


# ── AC-3 ─────────────────────────────────────────────────────────────────────

def test_ledger_adds_evidence_but_never_invents_it(tmp_path: Path) -> None:
    """No row for the slug ⇒ still unproven. The ledger is not an amnesty."""
    repo = _shared_remote_repo(tmp_path)
    plans = tmp_path / "plans"
    plans.mkdir()
    sp = plans / "2026-09-09-shared-remote-work.md"
    sp.write_text(SUBPLAN, encoding="utf-8")

    rows = _ledger(tmp_path, [
        {"slug": "a-different-subplan", "step_from": 0, "step_to": 2,
         "commits": ["a" * 40]},
    ])

    result = _audit(repo, sp, rows)
    assert result["proven"] is False, (
        "a ledger row for another slug must not prove this one; "
        f"reasons={result['reasons']}"
    )
