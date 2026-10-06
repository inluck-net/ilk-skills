"""Tests for triage evidence carrying integrity violations — AC-2.

Verifies that build_evidence returns an integrity_violations key with the
correct kind and slug when the launcher log contains a gate-edit VIOLATION
line.  The test builds a hermetic data dir with a launcher log holding the
incident lines from MASTER Goal (run 20261006-040012).

Import idiom: same as test_triage_evidence_is_the_stopped_runs.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# -- helpers ------------------------------------------------------------------

def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_json(path: Path, obj: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _write_plan(plans_dir: Path, filename: str, slug: str, status: str) -> None:
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / filename).write_text(
        f"---\nplan: {slug}\nstatus: {status}\n---\n",
        encoding="utf-8",
    )


# -- incident log lines (verbatim from MASTER Goal, run 20261006-040012) ------

INCIDENT_LOG_LINES = [
    "All 24 tests pass. Let me update the sub-plan's step 2 local_checks to reflect the actual test files, then commit:",
    "[04:09:21] [tool >] Edit(.../plans/2026-10-06-ticket-attachment-projector.md)",
    "[amended] plan file changed during the iteration (planner edit above ## Findings); ending the iteration so the next one reads it",
    "  ! [ship-integrity VIOLATION] the worker changed gates for ticket-attachment-projector; restored to the pre-dispatch snapshot (rc=3):",
    "! [gates] ticket-attachment-projector: gates changed; restored to snapshot",
    "[runner] plan-amended: plan file changed during the iteration — iteration terminated",
    "[local_checks] no commit trailers found in the last iteration's commits",
    "=== Loop ended: ship_integrity_violation ===",
]


# -- AC-2: build_evidence returns integrity_violations from the launcher log --

@pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
def test_ac2_triage_evidence_carries_violations(tmp_path: Path):
    """build_evidence extracts gates-edited violation from launcher log."""
    from ilk_triage import build_evidence

    data_dir = tmp_path / "fixture"
    plans_dir = data_dir / "plans"

    # Launcher log with the incident lines
    _write_log(
        data_dir / "logs" / "launcher" / "x-R.log",
        INCIDENT_LOG_LINES,
    )

    # Sentinel so build_evidence can find the run
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "ship_integrity_violation",
        "run_id": "R",
    })

    # A plan so slug resolution works
    _write_plan(
        plans_dir,
        "2026-10-06-ticket-attachment-projector.md",
        "ticket-attachment-projector",
        "in-progress",
    )

    evidence = build_evidence(data_dir, "R")

    # The violation line must be in driver_log
    assert any(
        "the worker changed gates for ticket-attachment-projector" in line
        for line in evidence["driver_log"]
    ), f"violation line missing from driver_log: {evidence['driver_log'][:5]}"

    # The [gates] marker must also be in driver_log
    assert any(
        "[gates]" in line for line in evidence["driver_log"]
    ), f"[gates] line missing from driver_log: {evidence['driver_log'][:5]}"

    # integrity_violations key exists and has the right entry
    assert "integrity_violations" in evidence, "integrity_violations key missing"
    assert evidence["integrity_violations"] == [
        {"kind": "gates-edited", "slug": "ticket-attachment-projector"},
    ]


@pytest.mark.xfail(strict=True, reason="violation kind not yet parsed")
def test_ac2_no_launcher_log_gives_empty_violations(tmp_path: Path):
    """When no launcher log exists, integrity_violations is []."""
    from ilk_triage import build_evidence

    data_dir = tmp_path / "fixture"
    plans_dir = data_dir / "plans"

    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "ship_integrity_violation",
        "run_id": "R",
    })
    _write_plan(plans_dir, "2026-01-01-s.md", "s", "in-progress")

    evidence = build_evidence(data_dir, "R")

    assert evidence.get("integrity_violations") == []