"""Red-first pins: a triage candidate lands in the root triage read.

Sub-plan: a-triage-candidate-lands-in-the-root-triage-read (step 0).
Drives run_triage / emit with a hermetic data dir and ambient pin.

Every test sets HOME=<tmp_path> and ILK_DATA_HOME=<tmp_path>/ambient,
strips ILK_DATA_DIR, and asserts against <tmp_path>/ambient — never
against ~/.ilk-data.

AC-1 (red at base): run_triage on the model path. The candidate and
    candidate-emitted audit row land in the pinned root, not ambient.

AC-2 (red at base): the plain-red three-run scenario replayed with the
    ambient pin. Neither candidates nor audit rows leak to ambient.

AC-3 (red at base): emit with explicit backlog_dir. The audit row lands
    under the derived root, not ambient.

AC-4 (control, green at base): emit with no backlog_dir. The candidate
    and audit row land in the production default (ambient).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(_HERE.parent / "scripts"))


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _write_plan(
    plans_dir: Path,
    filename: str,
    slug: str,
    status: str = "in-progress",
    current_step: int = 1,
) -> Path:
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / filename
    plan_path.write_text(
        f"---\n"
        f"plan: {slug}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: 3\n"
        f"last_updated: 2026-10-08\n"
        f"---\n\n"
        f"# {slug}\n\n"
        f"## Steps\n\n"
        f"### Step 0\n\n"
        f"Done.\n\n"
        f"## Findings\n\n"
        f"Nothing yet.\n",
        encoding="utf-8",
    )
    return plan_path


def _build_stuck_data_dir(
    tmp_path: Path,
    *,
    run_id: str = "20261008-100000",
    slug: str = "a-triage-candidate-lands-in-the-root-triage-read",
    current_step: int = 1,
) -> Path:
    """Build a hermetic data dir with sentinel state=stuck-no-progress.

    tmp_path IS the data dir — plans/ and runtime/ sit directly under it.
    """
    data_dir = tmp_path

    # Sentinel: stopped with stuck-no-progress (bypasses _try_rule_first)
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "stuck-no-progress",
        "run_id": run_id,
        "pid": 12345,
        "started_at": "2026-10-08T10:00:00+0800",
        "ended_at": "2026-10-08T10:15:00+0800",
        "iterations": 3,
        "project_path": str(data_dir),
        "failed_check": {
            "slug": slug,
            "step": current_step,
            "command": "python3 -m pytest ...",
        },
    })

    # Gate-history: a red row
    red_row = {
        "run_id": run_id,
        "slug": slug,
        "step": current_step,
        "outcome": "fail",
        "attribution": {"node_ids": ["tests/test_something.py::test_case"]},
    }
    gate_path = data_dir / "runtime" / "launcher" / "gate-history.jsonl"
    gate_path.write_text(json.dumps(red_row) + "\n", encoding="utf-8")

    # Sub-plan
    _write_plan(
        data_dir / "plans",
        f"2026-10-08-{slug}.md",
        slug,
        status="in-progress",
        current_step=current_step,
    )

    return data_dir


def _build_red_data_dir(
    tmp_path: Path,
    *,
    run_id: str = "20261008-100000",
    slug: str = "a-triage-candidate-lands-in-the-root-triage-read",
    current_step: int = 1,
    state: str = "local_checks_failed",
    red_node_ids: list[str] | None = None,
) -> Path:
    """Build a hermetic data dir with local_checks_failed state.

    tmp_path IS the data dir — plans/ and runtime/ sit directly under it.
    """
    if red_node_ids is None:
        red_node_ids = ["tests/test_something.py::test_case"]

    data_dir = tmp_path

    # Sentinel: stopped with local_checks_failed
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": state,
        "run_id": run_id,
        "pid": 12345,
        "started_at": "2026-10-08T10:00:00+0800",
        "ended_at": "2026-10-08T10:15:00+0800",
        "iterations": 2,
        "project_path": str(data_dir),
        "failed_check": {
            "slug": slug,
            "step": current_step,
            "command": "python3 -m pytest ...",
        },
    })

    # Gate-history: a red row for (slug, step) with node_ids
    red_row = {
        "run_id": run_id,
        "slug": slug,
        "step": current_step,
        "outcome": "fail",
        "attribution": {"node_ids": red_node_ids},
        "red_owner": (
            f"owned (base e7f0b51) — green at iteration base — "
            f"this iteration broke it"
        ),
    }
    gate_path = data_dir / "runtime" / "launcher" / "gate-history.jsonl"
    gate_path.write_text(json.dumps(red_row) + "\n", encoding="utf-8")

    # Sub-plan
    _write_plan(
        data_dir / "plans",
        f"2026-10-08-{slug}.md",
        slug,
        status="in-progress",
        current_step=current_step,
    )

    return data_dir


def _pin_ambient(tmp_path: Path, monkeypatch) -> None:
    """Pin HOME and ILK_DATA_HOME to tmp_path, strip ILK_DATA_DIR."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "ambient"))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)


def _dir_is_empty(path: Path) -> bool:
    """Check if a directory does not exist or is empty."""
    if not path.exists():
        return True
    return not any(path.iterdir())


# ── AC-1: run_triage on the model path ───────────────────────────────────────


def test_ac1_run_triage_leaks_to_ambient(tmp_path, monkeypatch):
    """AC-1: run_triage writes candidate and audit to pinned root, not ambient.

    Build a project data dir under <tmp_path>/pinned/projects/k with
    state:stuck-no-progress; patch ilk_data_root -> <tmp_path>/pinned,
    decide -> stub returning park-and-escalate.
    Assert: candidate in pinned, audit in pinned, ambient clean.
    """
    from ilk_triage import run_triage
    import ilk_triage
    import triage_apply

    _pin_ambient(tmp_path, monkeypatch)

    # Build data dir under pinned root
    pinned_root = tmp_path / "pinned"
    project_dir = pinned_root / "projects" / "k"
    _build_stuck_data_dir(project_dir, run_id="run-ac1", slug="test-slug-ac1")

    # Patch ilk_data_root to return pinned root
    monkeypatch.setattr("ilk_triage.ilk_data_root", lambda: pinned_root)

    # Patch decide to return a valid park-and-escalate decision
    monkeypatch.setattr(
        ilk_triage, "decide",
        lambda *a, **kw: {
            "action": "park-and-escalate",
            "slug": "test-slug-ac1",
            "step": 1,
            "finding": "Test finding for AC-1",
            "basis": "Test basis",
            "falsifier": "Test falsifier",
        },
    )

    # Patch notification and resume ack
    monkeypatch.setattr(triage_apply, "ilk_notify", lambda **kw: None)
    monkeypatch.setattr(triage_apply, "write_resume_ack", lambda dd: None)

    result = run_triage(
        project_key="k",
        run_id="run-ac1",
        home=None,
        timeout_s=5,
    )

    assert result["action"] == "park-and-escalate"

    # Assert: candidate in pinned root
    candidates_path = pinned_root / "ilk-skills-improvements" / "candidates.json"
    assert candidates_path.exists(), "candidates.json must exist in pinned root"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert len(candidates) == 1, f"expected 1 candidate, got {len(candidates)}"

    # Assert: audit row in pinned root
    audit_dir = pinned_root / "projects" / "k" / "audit"
    assert audit_dir.exists(), "audit dir must exist in pinned root"
    audit_files = list(audit_dir.glob("*.jsonl"))
    assert len(audit_files) >= 1, "at least one audit file expected in pinned root"

    # Assert: ambient is clean
    ambient_root = tmp_path / "ambient"
    assert _dir_is_empty(ambient_root / "ilk-skills-improvements"), (
        "ambient/ilk-skills-improvements must not exist"
    )
    assert _dir_is_empty(ambient_root / "projects"), (
        "ambient/projects must not exist"
    )


# ── AC-2: plain-red three-run scenario ──────────────────────────────────────


def test_ac2_plain_red_three_run_leaks_to_ambient(tmp_path, monkeypatch):
    """AC-2: the plain-red three-run scenario replayed with ambient pin.

    Keys run1..run3, same slug, decide stubbed to ack-and-relaunch.
    Assert: ambient contains neither candidates nor audit rows.
    This is the literal reproduction of 3733e534b21f5cd6 / 60a9a7b25ca5af7a.
    """
    from triage_backlog import emit, signature

    _pin_ambient(tmp_path, monkeypatch)

    # Build backlog dir under a pinned root (not ambient)
    pinned_root = tmp_path / "pinned"
    backlog_dir = pinned_root / "ilk-skills-improvements"
    backlog_dir.mkdir(parents=True, exist_ok=True)

    slug = "a-plain-red-is-relaunched-not-escalated"
    runs = ["run1", "run2", "run3"]

    for run_id in runs:
        evidence = {
            "last_exit": {
                "state": "local_checks_failed",
                "run_id": run_id,
                "failed_check": {
                    "slug": slug,
                    "step": 1,
                    "command": "python3 -m pytest ...",
                },
            },
            "gate_history": [
                {
                    "slug": slug,
                    "step": 1,
                    "outcome": "fail",
                    "exit_code": 1,
                    "head_sha": "abc123",
                    "run_id": run_id,
                    "attribution": {
                        "verdict": "unmeasured",
                        "nodes": [{"id": "tests/test_something.py::test_case"}],
                    },
                }
            ],
            "postmortem_classification": "test-classification",
            "project": "k",
            "head_sha": "abc123",
        }

        decision = {
            "action": "ack-and-relaunch",
            "slug": slug,
            "step": 1,
            "finding": f"Test finding for {run_id}",
            "basis": "Test basis",
            "falsifier": "Test falsifier",
        }

        emit(
            project_key="k",
            run_id=run_id,
            outcome="applied",
            decision=decision,
            evidence=evidence,
            backlog_dir=backlog_dir,
        )

    # Assert: candidates in pinned root
    candidates_path = backlog_dir / "candidates.json"
    assert candidates_path.exists(), "candidates.json must exist in pinned root"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert len(candidates) >= 1, "at least one candidate expected"

    # Assert: audit rows in pinned root
    audit_dir = pinned_root / "projects" / "k" / "audit"
    assert audit_dir.exists(), "audit dir must exist in pinned root"
    audit_files = list(audit_dir.glob("*.jsonl"))
    assert len(audit_files) >= 1, "at least one audit file expected in pinned root"

    # Assert: ambient is clean
    ambient_root = tmp_path / "ambient"
    assert _dir_is_empty(ambient_root / "ilk-skills-improvements"), (
        "ambient/ilk-skills-improvements must not exist"
    )
    assert _dir_is_empty(ambient_root / "projects"), (
        "ambient/projects must not exist"
    )


# ── AC-3: emit with explicit backlog_dir ─────────────────────────────────────


def test_ac3_emit_explicit_backlog_dir_leaks_audit(tmp_path, monkeypatch):
    """AC-3: emit with explicit backlog_dir.

    The candidate-emitted row should be under <tmp_path>/root/projects/k/audit/
    and <tmp_path>/ambient/projects must not exist.
    """
    from triage_backlog import emit

    _pin_ambient(tmp_path, monkeypatch)

    # Build backlog dir under a root
    root = tmp_path / "root"
    backlog_dir = root / "ilk-skills-improvements"
    backlog_dir.mkdir(parents=True, exist_ok=True)

    evidence = {
        "last_exit": {
            "state": "local_checks_failed",
            "run_id": "run-ac3",
            "failed_check": {
                "slug": "test-slug-ac3",
                "step": 1,
                "command": "python3 -m pytest ...",
            },
        },
        "gate_history": [
            {
                "slug": "test-slug-ac3",
                "step": 1,
                "outcome": "fail",
                "exit_code": 1,
                "head_sha": "abc123",
                "run_id": "run-ac3",
                "attribution": {
                    "verdict": "unmeasured",
                    "nodes": [{"id": "tests/test_something.py::test_case"}],
                },
            }
        ],
        "postmortem_classification": "test-classification",
        "project": "k",
        "head_sha": "abc123",
    }

    decision = {
        "action": "park-and-escalate",
        "slug": "test-slug-ac3",
        "step": 1,
        "finding": "Test finding for AC-3",
        "basis": "Test basis",
        "falsifier": "Test falsifier",
    }

    emit(
        project_key="k",
        run_id="run-ac3",
        outcome="escalated",
        decision=decision,
        evidence=evidence,
        backlog_dir=backlog_dir,
    )

    # Assert: audit row under root/projects/k/audit/
    audit_dir = root / "projects" / "k" / "audit"
    assert audit_dir.exists(), "audit dir must exist under derived root"
    audit_files = list(audit_dir.glob("*.jsonl"))
    assert len(audit_files) >= 1, "at least one audit file expected under derived root"

    # Assert: ambient/projects does not exist
    ambient_root = tmp_path / "ambient"
    assert _dir_is_empty(ambient_root / "projects"), (
        "ambient/projects must not exist when backlog_dir is explicit"
    )


# ── AC-4: control, green at base ────────────────────────────────────────────


def test_ac4_emit_default_backlog_dir_uses_ambient(tmp_path, monkeypatch):
    """AC-4 (control, green at base): emit with no backlog_dir.

    The candidate should be in <tmp_path>/ambient/ilk-skills-improvements/candidates.json
    and the audit row under <tmp_path>/ambient/projects/k/audit/ — the production
    default does not move.
    """
    from triage_backlog import emit

    _pin_ambient(tmp_path, monkeypatch)

    evidence = {
        "last_exit": {
            "state": "local_checks_failed",
            "run_id": "run-ac4",
            "failed_check": {
                "slug": "test-slug-ac4",
                "step": 1,
                "command": "python3 -m pytest ...",
            },
        },
        "gate_history": [
            {
                "slug": "test-slug-ac4",
                "step": 1,
                "outcome": "fail",
                "exit_code": 1,
                "head_sha": "abc123",
                "run_id": "run-ac4",
                "attribution": {
                    "verdict": "unmeasured",
                    "nodes": [{"id": "tests/test_something.py::test_case"}],
                },
            }
        ],
        "postmortem_classification": "test-classification",
        "project": "k",
        "head_sha": "abc123",
    }

    decision = {
        "action": "ack-and-relaunch",
        "slug": "test-slug-ac4",
        "step": 1,
        "finding": "Test finding for AC-4",
        "basis": "Test basis",
        "falsifier": "Test falsifier",
    }

    emit(
        project_key="k",
        run_id="run-ac4",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        # No backlog_dir — should use production default (ambient)
    )

    # Assert: candidate in ambient root
    ambient_root = tmp_path / "ambient"
    candidates_path = ambient_root / "ilk-skills-improvements" / "candidates.json"
    assert candidates_path.exists(), "candidates.json must exist in ambient root"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert len(candidates) == 1, f"expected 1 candidate, got {len(candidates)}"

    # Assert: audit row under ambient/projects/k/audit/
    audit_dir = ambient_root / "projects" / "k" / "audit"
    assert audit_dir.exists(), "audit dir must exist under ambient root"
    audit_files = list(audit_dir.glob("*.jsonl"))
    assert len(audit_files) >= 1, "at least one audit file expected under ambient root"
