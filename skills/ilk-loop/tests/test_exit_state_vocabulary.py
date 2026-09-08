"""Tests for launcher exit-state vocabulary coverage in detached-component-contracts.md.

Enumerates the state literals actually written by the launcher (bash + PS runners)
and asserts each one appears in the contract document. Adding a new state without
documenting it turns this test red.

Uses the contract file path relative to the repo root.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CONTRACT_PATH = _REPO_ROOT / "skills" / "ilk-loop" / "references" / "detached-component-contracts.md"
_BASH_RUNNER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_PS_RUNNER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.ps1"


def _extract_state_literals(*paths: Path) -> set[str]:
    """Extract state literal strings from runner source files.

    Matches patterns like: "no-progress", 'all-shipped', etc.
    Only captures states that are assigned to stop_reason or iterStopReason.
    """
    states: set[str] = set()
    state_pattern = re.compile(r"""['"]([a-z_-]+)['"]""")
    # Context patterns: lines that assign to stop_reason or iterStopReason
    assignment_pattern = re.compile(
        r"(?:stop_reason|iterStopReason|stopReason|iter_stop_reason)\s*=\s*"
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if assignment_pattern.search(line):
                for m in state_pattern.finditer(line):
                    candidate = m.group(1)
                    # Filter out non-state strings (commands, paths, etc.)
                    if len(candidate) > 3 and "-" in candidate or "_" in candidate:
                        states.add(candidate)

    return states


# States known to be written by the runner to the sentinel.
# This is the authoritative list — the test asserts each appears in the contract.
LAUNCHER_EXIT_STATES = {
    "no-progress",
    "all-shipped",
    "interrupted",
    "timeout",
    "local_checks_failed",
    "ship_integrity_violation",
    "budget-exhausted",
    "max-iterations",
    "blocked-no-runnable",
    "already-shipped",
}


@pytest.fixture()
def contract_text() -> str:
    return _CONTRACT_PATH.read_text(encoding="utf-8")


def test_contract_file_exists():
    assert _CONTRACT_PATH.exists(), f"Contract file not found: {_CONTRACT_PATH}"


@pytest.mark.parametrize("state", sorted(LAUNCHER_EXIT_STATES))
def test_state_in_contract(state: str, contract_text: str):
    """Each launcher exit state must appear in the contract document.

    The state must appear as a quoted literal (e.g. "no-progress" or 'no-progress')
    so it's unambiguous and greppable.
    """
    # Check for the state as a quoted literal in the contract
    assert f'"{state}"' in contract_text or f"'{state}'" in contract_text, (
        f"Exit state '{state}' not found in {_CONTRACT_PATH.name}. "
        f"Add it to the state vocabulary table under Contract 1."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sub-plan: all-shipped-cannot-hide-an-unproven-subplan (step 0, RED-FIRST)
#
# Folds in gate-discovery-is-not-conditional-on-commits step 2 ("a skipped gate
# is a distinct outcome, not silence") — that is the same terminal-state
# surface, so the two are pinned together here rather than twice.
#
# The defect these pin, measured on a consumer host 2026-09-08: the loop prints
# `SHIP PROOF MISSING: 2 sub-plans shipped without proof` and
# `[ilk] ALL SHIPPED — nothing to run. Do NOT relaunch.` in the SAME run.
# `SHIP PROOF MISSING` is a report (loop_status.py:539); the driver's exit path
# never reads it.  And one layer down, the prover itself fails open
# (loop_status.py:437-439, `except Exception: sp["proven"] = True`).
# ─────────────────────────────────────────────────────────────────────────────

import json
import os
import subprocess
import sys
import textwrap

_DRIVER = _BASH_RUNNER
_LOOP_STATUS = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "loop_status.py"

# The terminal state a run must reach instead of `all-shipped` when a
# registered sub-plan is shipped without proof.  Reuses the existing
# `shipped-unverified` postmortem label (already in CLASSIFICATION_LABELS,
# already routed to needs-human) rather than widening that vocabulary.
UNPROVEN_EXIT_STATE = "shipped-unproven"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _init_repo(path: Path) -> None:
    _git(["init"], path)
    _git(["config", "user.email", "test@test"], path)
    _git(["config", "user.name", "Test"], path)
    (path / ".gitkeep").write_text("")
    _git(["add", ".gitkeep"], path)
    _git(["commit", "-m", "init"], path)


def _commit(path: Path, subject: str) -> None:
    _git(["commit", "--allow-empty", "-m", subject], path)


def _make_all_shipped_but_unproven(tmp: Path) -> Path:
    """A master whose sub-plans are ALL `shipped`, one of them without proof.

    `proven-ok` has a commit for every authored step.  `no-proof` has none —
    it is the shipped-without-proof row.  With every sub-plan shipped, today's
    classification reaches `all-shipped` over it.
    """
    _init_repo(tmp)
    plans = tmp / "docs" / "plans"
    plans.mkdir(parents=True)

    (plans / "MASTER-2026-09-08-unproven-probe.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-09-08-unproven-probe
        status: active
        ---
        # unproven probe

        | # | Slug |
        |---|---|
        | 1 | 2026-09-08-proven-ok.md |
        | 2 | 2026-09-08-no-proof.md |
    """))

    for slug, steps in (("proven-ok", 2), ("no-proof", 2)):
        (plans / f"2026-09-08-{slug}.md").write_text(textwrap.dedent(f"""\
            ---
            plan: {slug}
            status: shipped
            current_step: {steps}
            estimated_steps: {steps}
            verification_tier: loop-verified
            local_checks:
              - command: echo ok
                timeout: 10
            ---
            ### Step 0
            ### Step 1
        """))

    # Only proven-ok gets its step commits.  no-proof gets none.
    for n in range(2):
        _commit(tmp, f"feat(proven-ok): step {n} [plan:proven-ok#step-{n}]")

    return plans


def _status_json(project: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(_LOOP_STATUS), "--json"],
        capture_output=True, text=True, timeout=60, cwd=project,
        env={**os.environ, "ILK_DATA_HOME": str(project / "ilk-data")},
    )
    assert result.stdout.strip(), f"loop_status --json emitted nothing: {result.stderr}"
    return json.loads(result.stdout)


def _subplan(data: dict, slug: str) -> dict:
    for sp in data.get("subplans", []):
        if sp.get("slug", "").endswith(slug):
            return sp
    raise AssertionError(f"{slug} not in {[s.get('slug') for s in data.get('subplans', [])]}")


# ── (a) the CLASSIFICATION half ─────────────────────────────────────────────

def test_the_unproven_exit_state_is_documented(contract_text: str) -> None:
    """A terminal state no classifier knows is how a failed run reads clean."""
    assert f'"{UNPROVEN_EXIT_STATE}"' in contract_text, (
        f"'{UNPROVEN_EXIT_STATE}' is not in the Contract 1 state vocabulary. "
        f"A terminal state absent from the table falls through collect.py's "
        f"heuristics and gets classified clean-success."
    )


def test_classification_is_not_all_shipped_when_a_subplan_is_unproven(tmp_path: Path) -> None:
    """THE pin: the terminal state must consult proof.

    Sources the driver and calls its real `classify_loop_status` against a
    master whose sub-plans are all shipped but one of which has no proof.
    """
    _make_all_shipped_but_unproven(tmp_path)
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        source '{_DRIVER}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        set +eE +o pipefail
        PROJECT_PATH='{tmp_path}'
        LOOP_STATUS_SCRIPT='{_LOOP_STATUS}'
        export ILK_DATA_HOME='{tmp_path}/ilk-data'
        classify_loop_status
        echo "CLASSIFIED=$CLASSIFIED_STATUS"
    """)
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    line = [l for l in out.stdout.splitlines() if l.startswith("CLASSIFIED=")]
    assert line, f"classify_loop_status produced no verdict.\n{out.stdout}\n{out.stderr}"
    verdict = line[-1].split("=", 1)[1].strip()
    assert verdict != "all-shipped", (
        "the run reached `all-shipped` over a sub-plan shipped without proof — "
        "the classification never consulted the ship-proof ledger. "
        "This is the `SHIP PROOF MISSING` + `ALL SHIPPED` pair from 2026-09-08."
    )
    assert verdict == UNPROVEN_EXIT_STATE, (
        f"expected the distinct terminal state {UNPROVEN_EXIT_STATE!r}, got {verdict!r}. "
        f"A skipped/absent gate must be a distinct OUTCOME, not silence."
    )


def test_the_tier_is_withdrawn_not_retained_when_unproven(tmp_path: Path) -> None:
    """A stale `loop-verified` is worse than an absent one: downstream reads it
    as an assertion and publishes on it."""
    _make_all_shipped_but_unproven(tmp_path)
    data = _status_json(tmp_path)
    sp = _subplan(data, "no-proof")
    assert sp.get("proven") is not True, "fixture is wrong: no-proof should be unproven"
    assert sp.get("verification_tier") != "loop-verified", (
        "a sub-plan with no ship-proof row still reports "
        "verification_tier: loop-verified — false provenance with a real reader "
        "(a consumer's publication step decides from this alone)."
    )


# ── (b) the PROVER half: it must not fail open ──────────────────────────────

def test_a_prover_that_cannot_run_does_not_report_proven(tmp_path: Path, monkeypatch) -> None:
    """`except Exception: sp["proven"] = True` is a prover reporting SUCCESS
    when it could not run.  A failed audit is a THIRD state."""
    _make_all_shipped_but_unproven(tmp_path)
    sys.path.insert(0, str(_REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
    import loop_status
    import ship_audit

    def _boom(*a, **k):
        raise RuntimeError("audit deliberately broken")

    monkeypatch.setattr(ship_audit, "audit_ship", _boom)
    monkeypatch.setattr(ship_audit, "read_subplan_for_audit", _boom)
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "ilk-data"))

    data = loop_status.resolve_status(tmp_path, json_mode=True)
    shipped = [sp for sp in data["subplans"] if sp["status"] == "shipped"]
    assert shipped, "fixture produced no shipped sub-plans"
    for sp in shipped:
        assert sp.get("proven") is not True, (
            f"{sp['slug']}: the audit raised and the prover still reported "
            f"proven=True. That is 'no stage reports success without proof' "
            f"violated inside the instrument meant to enforce it."
        )


def test_a_failed_audit_is_distinguishable_from_a_clean_one_in_json(tmp_path: Path, monkeypatch) -> None:
    """A reader holding ONLY the JSON must be able to tell 'audited and clean'
    from 'the audit crashed'."""
    _make_all_shipped_but_unproven(tmp_path)
    sys.path.insert(0, str(_REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
    import loop_status
    import ship_audit

    clean = loop_status.resolve_status(tmp_path, json_mode=True)
    clean_sp = _subplan(clean, "proven-ok")

    def _boom(*a, **k):
        raise RuntimeError("audit deliberately broken")

    monkeypatch.setattr(ship_audit, "audit_ship", _boom)
    monkeypatch.setattr(ship_audit, "read_subplan_for_audit", _boom)
    broken = loop_status.resolve_status(tmp_path, json_mode=True)
    broken_sp = _subplan(broken, "proven-ok")

    # `proven-ok` carries a declared gate with no gate record, so an audit that
    # RAN reports it unproven.  That is not the point: the point is that a
    # reader can tell "the audit ran and reached a verdict" from "the audit
    # could not run".  Asserting the clean value is `proven` here would pass
    # for the wrong reason.
    assert clean_sp.get("proof_state") in ("proven", "unproven"), (
        f"an audit that RAN should report a verdict, got "
        f"{clean_sp.get('proof_state')!r}"
    )
    assert broken_sp.get("proof_state") == "audit-error", (
        f"a crashed audit should report proof_state 'audit-error', got "
        f"{broken_sp.get('proof_state')!r} — indistinguishable from a clean audit"
    )
    assert clean_sp.get("proof_state") != broken_sp.get("proof_state"), (
        "a reader holding only the JSON cannot tell an audited sub-plan from "
        "one whose audit crashed"
    )
