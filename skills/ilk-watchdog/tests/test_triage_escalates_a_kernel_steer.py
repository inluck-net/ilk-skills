"""Red-first pins: triage escalates a decision that steers into the kernel.

Part of sub-plan ``an-unattended-build-cannot-edit-the-kernel``.

Guard 1 at triage time: ``triage_apply.validate`` returns a problem when the
decision's finding or basis names a kernel entry by full path or basename.
Under an auto-planned master, kernel-tier entries produce a problem; under any
master, rules-tier entries produce a problem.  ``apply`` then writes an
``escalated`` row and no sub-plan edit.

Five acceptance criteria:

  AC-4  validate on the toolkit with a decision whose finding says "fix by
        editing ship_audit.py" under an auto-planned master returns a problem
        naming skills/ilk-loop/scripts/ship_audit.py; one naming
        tests/invariants/mutations.json returns a rules-tier problem under any
        master; apply then writes an escalated row and no sub-plan edit.
        Red-first.
  AC-5  a clean decision under an auto-planned master validates with no problem,
        and the existing triage rail tests still pass (they are in the step-1
        gate).  Control.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# Ensure the scripts dirs are importable.
sys.path.insert(0, str(_REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _toolkit_env(tmp_path: Path):
    """Create a fake toolkit repo with safety-kernel.json and a plans dir.

    Layout::

        tmp/
          toolkit/               ← a fake toolkit repo with .git and kernel
            skills/ilk-loop/
              safety-kernel.json
              scripts/
                ship_audit.py
            tests/invariants/
              mutations.json
          plans/                 ← a fake plans dir
            MASTER-2026-10-03k.md
            2026-10-03k-sub.md
    """
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir()

    # Copy safety-kernel.json from the real repo
    real_kernel = _REPO_ROOT / "skills" / "ilk-loop" / "safety-kernel.json"
    kernel_dir = toolkit / "skills" / "ilk-loop"
    kernel_dir.mkdir(parents=True)
    (kernel_dir / "safety-kernel.json").write_text(
        real_kernel.read_text(encoding="utf-8"), encoding="utf-8"
    )

    # Create target files
    scripts_dir = kernel_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "ship_audit.py").write_text("# ship audit\n", encoding="utf-8")

    invariants_dir = toolkit / "tests" / "invariants"
    invariants_dir.mkdir(parents=True)
    (invariants_dir / "mutations.json").write_text("[]\n", encoding="utf-8")

    # Create plans dir
    plans = tmp_path / "plans"
    plans.mkdir()

    return {"toolkit": toolkit, "plans": plans, "tmp": tmp_path}


def _auto_planned_master(plans: Path) -> None:
    """Write an auto_planned: true master."""
    (plans / "MASTER-2026-10-03k.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-10-03k
        status: active
        auto_planned: true
        ---
        # auto-planned batch

        | # | Slug |
        |---|---|
        | 0 | 2026-10-03k-sub.md |
    """), encoding="utf-8")


def _session_master(plans: Path) -> None:
    """Write a session master (no auto_planned)."""
    (plans / "MASTER-2026-10-03k.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-10-03k
        status: active
        ---
        # session batch

        | # | Slug |
        |---|---|
        | 0 | 2026-10-03k-sub.md |
    """), encoding="utf-8")


def _make_decision(
    action: str = "park-and-escalate",
    *,
    finding: str = "test finding",
    basis: str = "test basis",
    falsifier: str = "test falsifier",
    slug: str | None = None,
    step: int | None = None,
) -> dict:
    """Build a decision dict matching ilk_triage.decide's output shape."""
    return {
        "action": action,
        "slug": slug,
        "step": step,
        "finding": finding,
        "basis": basis,
        "falsifier": falsifier,
        "model": "claude-opus-test",
    }


# ---------------------------------------------------------------------------
# AC-4: validate rejects decisions that steer into the kernel
# ---------------------------------------------------------------------------


class TestValidateRejectsKernelSteer:
    """validate must return problems when the finding or basis names a kernel entry."""

    def test_finding_naming_kernel_entry_returns_problem(self, _toolkit_env: dict) -> None:
        """AC-4: finding says "fix by editing ship_audit.py" under auto_planned master
        ⇒ problem naming skills/ilk-loop/scripts/ship_audit.py."""
        from triage_apply import validate

        plans = _toolkit_env["plans"]
        _auto_planned_master(plans)

        decision = _make_decision(
            finding="fix by editing skills/ilk-loop/scripts/ship_audit.py",
        )
        problems = validate(decision, plans, repo=_toolkit_env["toolkit"])
        assert any("ship_audit.py" in p for p in problems), \
            f"must reject kernel entry ship_audit.py, got: {problems}"

    def test_finding_naming_rules_entry_returns_problem(self, _toolkit_env: dict) -> None:
        """AC-4: finding says "edit tests/invariants/mutations.json" under any master
        ⇒ problem naming rules tier."""
        from triage_apply import validate

        plans = _toolkit_env["plans"]
        _session_master(plans)

        decision = _make_decision(
            finding="fix by editing tests/invariants/mutations.json",
        )
        problems = validate(decision, plans, repo=_toolkit_env["toolkit"])
        assert any("rules" in p.lower() for p in problems), \
            f"must reject rules-tier entry, got: {problems}"

    def test_apply_writes_escalated_row_and_no_plan_edit(self, _toolkit_env: dict) -> None:
        """AC-4: apply with a kernel-steering finding writes an escalated row and no sub-plan edit."""
        from triage_apply import apply

        plans = _toolkit_env["plans"]
        _auto_planned_master(plans)

        # Create a fake data dir with the plans
        tmp = _toolkit_env["tmp"]
        data_dir = tmp / "ilk-data"
        data_dir.mkdir()
        import shutil
        shutil.copytree(plans, data_dir / "plans")

        # Create a minimal active sub-plan
        active_plan = data_dir / "plans" / "2026-10-03k-sub.md"
        active_plan.write_text(textwrap.dedent("""\
            ---
            plan: sub
            status: in-progress
            current_step: 0
            estimated_steps: 2
            last_updated: 2026-10-03
            ---
            # Sub-plan

            ## Steps

            ### Step 0

            Pending.

            ## Findings

            Nothing yet.
        """), encoding="utf-8")

        # Stub ilk_notify
        import triage_apply
        notify_calls: list[dict] = []
        original_notify = getattr(triage_apply, "ilk_notify", None)

        def stub_notify(*, event: str, project: str, detail: str | None = None):
            notify_calls.append({"event": event, "project": project, "detail": detail})

        triage_apply.ilk_notify = stub_notify
        try:
            # Use amend action so that apply only escalates if validate finds problems
            decision = _make_decision(
                action="amend",
                slug="sub",
                finding="fix by editing skills/ilk-loop/scripts/ship_audit.py",
                basis="the gate failed",
                falsifier="rerun the gate",
            )
            result = apply(decision, data_dir, run_id="test-run-001", repo=_toolkit_env["toolkit"])
            assert result["action"] == "park-and-escalate", \
                f"must escalate due to kernel steer, got: {result}"

            # Verify escalated audit row
            assert result.get("reason"), "escalation must name a reason"

            # Verify no plan edit — the active plan's text should be unchanged
            plan_text = active_plan.read_text(encoding="utf-8")
            assert "fix by editing" not in plan_text, \
                "escalated decision must not write finding into the plan"

            # Verify notification was fired
            assert len(notify_calls) == 1, f"expected 1 notify call, got {len(notify_calls)}"
            assert notify_calls[0]["event"] == "triage-escalated"
        finally:
            triage_apply.ilk_notify = original_notify


# ---------------------------------------------------------------------------
# AC-5 (control): clean decision validates with no problem
# ---------------------------------------------------------------------------


class TestCleanDecisionValidates:
    """A clean decision under an auto-planned master must validate with no problem."""

    def test_clean_decision_no_problems(self, _toolkit_env: dict) -> None:
        """AC-5 (control): clean decision validates with no problem."""
        from triage_apply import validate

        plans = _toolkit_env["plans"]
        _auto_planned_master(plans)

        decision = _make_decision(
            finding="the gate failed because of a flaky test",
            basis="the test output shows a timeout",
            falsifier="rerun the test and it passes",
        )
        problems = validate(decision, plans)
        assert problems == [], f"clean decision should have no problems, got: {problems}"