"""status_all must not report a BLOCKED sub-plan as the next one.

Observed 2026-08-14 on gh-resolve: the xbar/SwiftBar tray displayed

    gh-resolve  2/4  shadow-mode-provisioning  running on mimo-v2.5-pro

while a loop (pid 57457, run 20260814-013546) was actually working
``first-party-means-our-repo`` at 1/5.  ``shadow-mode-provisioning`` was
``blocked`` at 2/4 — outstanding, but not runnable.

``loop_status.py`` got this right; ``status_all.py`` — which is what feeds the
tray — did not.  Its ``_resolve_next_subplan`` skipped only ``shipped``:

    if status == "shipped":
        continue

``plan_status.master_has_runnable`` already documents the correct semantics
(plan_status.py:177-181): *"a blocked sub-plan is outstanding work, but it is NOT
runnable — nothing the loop does will advance it until a human unblocks it."*
Two readers of one contract had diverged, so the tray narrated a stalled plan as
the live one and the operator could not tell which sub-plan was actually running.

Both call sites need "runnable": the active-master branch feeds the tray's
display, and the queued-master branch feeds ``manually_runnable``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import status_all  # noqa: E402


def _write_master(plans_dir: Path, name: str, subplans: list[str], *, status: str = "active") -> str:
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name}",
        "created: 2026-08-14T00:00:00+08:00",
        f"status: {status}",
        "priority: 1",
        "pause_after_ship: false",
        "---",
        "",
        f"# {name}",
        "",
        "## Sub-plan registry",
        "",
        "| # | Sub-plan | Status |",
        "|---|---|---|",
    ]
    lines += [f"| 1 | [{sp}](./{sp}) | pending |" for sp in subplans]
    lines.append("")
    text = "\n".join(lines)
    (plans_dir / name).write_text(text, encoding="utf-8")
    return text


def _write_subplan(plans_dir: Path, name: str, *, status: str, current_step: int,
                   estimated_steps: int) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / name).write_text(
        "---\n"
        f"plan: {name[:-3]}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {estimated_steps}\n"
        "---\n"
        f"\n# {name}\n",
        encoding="utf-8",
    )


def test_blocked_subplan_is_not_the_next_subplan(tmp_path):
    """The gh-resolve shape: shipped, then blocked, then the real pending one."""
    plans = tmp_path / "plans"
    master_text = _write_master(
        plans,
        "MASTER-2026-08-13d-execution-plan.md",
        [
            "2026-08-13d-the-translator-reads-the-culprit.md",
            "2026-08-13d-shadow-mode-provisioning.md",
            "2026-08-13d-first-party-means-our-repo.md",
        ],
    )
    _write_subplan(plans, "2026-08-13d-the-translator-reads-the-culprit.md",
                   status="shipped", current_step=6, estimated_steps=6)
    _write_subplan(plans, "2026-08-13d-shadow-mode-provisioning.md",
                   status="blocked", current_step=2, estimated_steps=4)
    _write_subplan(plans, "2026-08-13d-first-party-means-our-repo.md",
                   status="pending", current_step=1, estimated_steps=5)

    slug, step = status_all._resolve_next_subplan(plans, master_text)

    assert slug == "2026-08-13d-first-party-means-our-repo", (
        f"reported a non-runnable sub-plan as next: {slug!r} at {step!r}"
    )
    assert step == "1/5", step


def test_in_progress_subplan_is_runnable(tmp_path):
    """`in-progress` is runnable — the loop is mid-way through it."""
    plans = tmp_path / "plans"
    master_text = _write_master(plans, "MASTER-2026-08-14-execution-plan.md",
                                ["2026-08-14-a.md", "2026-08-14-b.md"])
    _write_subplan(plans, "2026-08-14-a.md", status="blocked", current_step=1, estimated_steps=3)
    _write_subplan(plans, "2026-08-14-b.md", status="in-progress", current_step=2, estimated_steps=4)

    slug, step = status_all._resolve_next_subplan(plans, master_text)
    assert slug == "2026-08-14-b"
    assert step == "2/4"


def test_all_blocked_reports_nothing_runnable(tmp_path):
    """A master whose only outstanding sub-plans are blocked has no next.

    This is what feeds `manually_runnable` for a queued master — reporting a
    blocked sub-plan there claims work the loop cannot advance.
    """
    plans = tmp_path / "plans"
    master_text = _write_master(plans, "MASTER-2026-08-14b-execution-plan.md",
                                ["2026-08-14b-a.md", "2026-08-14b-b.md"])
    _write_subplan(plans, "2026-08-14b-a.md", status="shipped", current_step=3, estimated_steps=3)
    _write_subplan(plans, "2026-08-14b-b.md", status="blocked", current_step=0, estimated_steps=2)

    slug, step = status_all._resolve_next_subplan(plans, master_text)
    assert slug == "", f"blocked-only master reported {slug!r} as runnable"
    assert step == ""


def test_all_shipped_reports_nothing(tmp_path):
    """Unchanged behaviour: a fully shipped master has no next sub-plan."""
    plans = tmp_path / "plans"
    master_text = _write_master(plans, "MASTER-2026-08-14c-execution-plan.md",
                                ["2026-08-14c-a.md"])
    _write_subplan(plans, "2026-08-14c-a.md", status="shipped", current_step=2, estimated_steps=2)

    slug, step = status_all._resolve_next_subplan(plans, master_text)
    assert slug == ""
    assert step == ""


def test_agrees_with_plan_status_runnable_definition():
    """status_all must not keep its own copy of "which statuses are runnable".

    Two readers of one contract diverging is the defect this file exists for.

    Asserted by VALUE plus a source check, not by object identity: under a
    full-suite run the two modules can be imported via different sys.path
    entries, so `is` compares two equal-but-distinct set objects and fails for
    a reason that has nothing to do with the contract. (Measured 2026-08-14:
    this test passed alone and failed in the 649-test run for exactly that.)
    """
    sys.path.insert(0, str(SCRIPTS_DIR))
    import plan_status

    assert hasattr(status_all, "_RUNNABLE_SUBPLAN_STATUSES"), (
        "status_all should reuse plan_status's runnable-status definition"
    )
    assert (
        status_all._RUNNABLE_SUBPLAN_STATUSES
        == plan_status._RUNNABLE_SUBPLAN_STATUSES
    ), "status_all disagrees with plan_status about which statuses are runnable"

    # The import must be the source of the value — a re-declared literal in
    # status_all.py is the drift this test exists to prevent.
    src = (SCRIPTS_DIR / "status_all.py").read_text(encoding="utf-8")
    assert "from plan_status import _RUNNABLE_SUBPLAN_STATUSES" in src, (
        "status_all should import the set, not re-declare it"
    )
    assert '_RUNNABLE_SUBPLAN_STATUSES = {' not in src, (
        "status_all re-declares the runnable-status set — it will drift"
    )
