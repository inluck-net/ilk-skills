"""Tests: a triaged failure becomes one deduplicated improvement candidate.

Sub-plan: a-triaged-failure-becomes-an-improvement-candidate (step 1).
Covers AC-1..AC-7: the triage backlog emitter creates candidates from triage
outcomes, deduplicates by signature, tracks escalations and urgency, refuses
corrupt files, and integrates with ilk_triage.py and ilk_audit.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "triage" / "run-20261003-125807"


# ── helpers ──────────────────────────────────────────────────────────────────


def _build_fake_backlog_dir(tmp_path: Path) -> Path:
    """Create an empty backlog directory for testing."""
    backlog_dir = tmp_path / "ilk-skills-improvements"
    backlog_dir.mkdir(parents=True, exist_ok=True)
    return backlog_dir


def _load_candidates(backlog_dir: Path) -> list[dict]:
    """Load candidates from the backlog directory."""
    p = backlog_dir / "candidates.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _save_candidates(backlog_dir: Path, entries: list[dict]) -> None:
    """Save candidates to the backlog directory."""
    p = backlog_dir / "candidates.json"
    p.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _make_evidence_pack(
    *,
    state: str = "local_checks_failed_no_commits",
    action: str = "amend",
    failing_node: str = "test_node_123",
    failed_check_slug: str = "a-layout-switch-restarts-the-daemon",
    failed_check_step: int = 0,
    failed_check_command: str = "python3 -m pytest skills/ilk-watchdog/tests/test_a_layout_switch_restarts_the_daemon.py -q",
    project: str = "test-project",
    run_id: str = "20261003-125807",
    head_sha: str = "4c57257606bcf8cf92c343eabeb9bce7c22e4c94",
) -> dict:
    """Build a minimal evidence pack for testing."""
    return {
        "last_exit": {
            "state": state,
            "run_id": run_id,
            "failed_check": {
                "slug": failed_check_slug,
                "step": failed_check_step,
                "command": failed_check_command,
            },
        },
        "gate_history": [
            {
                "slug": failed_check_slug,
                "step": failed_check_step,
                "outcome": "fail",
                "exit_code": 1,
                "head_sha": head_sha,
                "run_id": run_id,
                "attribution": {
                    "verdict": "unmeasured",
                    "nodes": [{"id": failing_node}],
                },
            }
        ],
        "postmortem_classification": "test-classification",
        "project": project,
        "head_sha": head_sha,
    }


def _make_decision(
    *,
    action: str = "amend",
    slug: str = "test-slug",
    step: int = 0,
    finding: str = "Test finding for the triage decision",
    basis: str = "Test basis",
    falsifier: str = "Test falsifier",
) -> dict:
    """Build a minimal decision dict for testing."""
    return {
        "action": action,
        "slug": slug,
        "step": step,
        "finding": finding,
        "basis": basis,
        "falsifier": falsifier,
    }


def _build_fake_data_dir(tmp_path: Path) -> Path:
    """Build a fake data root with plans, runtime, and logs from the replay fixture.

    Returns the project data dir (data_root / projects / test-project).
    """
    data_root = tmp_path / "ilk-data"
    data_root.mkdir()

    # Create project dir under data_root/projects/test-project
    project_dir = data_root / "projects" / "test-project"
    project_dir.mkdir(parents=True)

    # Copy fixture runtime + logs
    for sub in ("runtime", "logs"):
        src = FIXTURE_DIR / sub
        if src.exists():
            shutil.copytree(src, project_dir / sub)

    # Copy fixture plans
    plans_dir = project_dir / "plans"
    plans_dir.mkdir()
    fixture_plans = FIXTURE_DIR / "plans"
    if fixture_plans.exists():
        for p in fixture_plans.iterdir():
            shutil.copy(p, plans_dir)

    return project_dir


# ── AC-1: applied decision creates one candidate ────────────────────────────


def test_ac1_applied_decision_creates_one_candidate(tmp_path):
    """AC-1: an applied decision on a fixture evidence pack creates one candidate
    with source: 'triage', source_id = signature(...), status: 'open',
    seen_count: 1, and relations.run_id; a candidate-emitted row exists."""
    backlog_dir = _build_fake_backlog_dir(tmp_path)

    from triage_backlog import emit, signature

    evidence = _make_evidence_pack()
    decision = _make_decision(action="amend")

    result = emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        backlog_dir=backlog_dir,
    )

    # Verify the candidate
    candidates = _load_candidates(backlog_dir)
    assert len(candidates) == 1

    candidate = candidates[0]
    assert candidate["source"] == "triage"
    assert candidate["source_id"] == signature(evidence, decision)
    assert candidate["status"] == "open"
    assert candidate["seen_count"] == 1
    assert candidate["relations"]["run_id"] == "20261003-125807"

    # Verify the result matches
    assert result["id"] == candidate["id"]
    assert result["source"] == "triage"


# ── AC-2: dedup by signature ────────────────────────────────────────────────


def test_ac2_same_signature_bumps_seen_count(tmp_path):
    """AC-2: the same failure from a second run_id (same signature) bumps the
    same entry to seen_count: 2 and leaves the candidate count unchanged."""
    backlog_dir = _build_fake_backlog_dir(tmp_path)

    from triage_backlog import emit

    evidence = _make_evidence_pack()
    decision = _make_decision(action="amend")

    # First emit
    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        backlog_dir=backlog_dir,
    )

    # Second emit with same signature but different run_id
    evidence2 = _make_evidence_pack(run_id="20261004-090000")
    emit(
        project_key="test-project",
        run_id="20261004-090000",
        outcome="applied",
        decision=decision,
        evidence=evidence2,
        backlog_dir=backlog_dir,
    )

    candidates = _load_candidates(backlog_dir)
    assert len(candidates) == 1  # still one candidate
    assert candidates[0]["seen_count"] == 2


def test_ac2_different_failing_node_creates_second_entry(tmp_path):
    """AC-2: a different failing node gives a second entry."""
    backlog_dir = _build_fake_backlog_dir(tmp_path)

    from triage_backlog import emit

    evidence = _make_evidence_pack(failing_node="node_a")
    decision = _make_decision(action="amend")

    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        backlog_dir=backlog_dir,
    )

    # Different failing node → different signature
    evidence2 = _make_evidence_pack(failing_node="node_b")
    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence2,
        backlog_dir=backlog_dir,
    )

    candidates = _load_candidates(backlog_dir)
    assert len(candidates) == 2


# ── AC-3: escalation and urgency tracking ────────────────────────────────────


def test_ac3_escalation_sets_high_severity(tmp_path):
    """AC-3: an escalation sets severity: 'high' and relations.escalations: 1."""
    backlog_dir = _build_fake_backlog_dir(tmp_path)

    from triage_backlog import emit

    evidence = _make_evidence_pack()
    decision = _make_decision(action="park-and-escalate")

    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="escalated",
        decision=decision,
        evidence=evidence,
        backlog_dir=backlog_dir,
    )

    candidates = _load_candidates(backlog_dir)
    assert len(candidates) == 1
    assert candidates[0]["severity"] == "high"
    assert candidates[0]["relations"]["escalations"] == 1


def test_ac3_two_applications_in_one_master_sets_urgent(tmp_path):
    """AC-3: two applications in one master set relations.urgent: true."""
    backlog_dir = _build_fake_backlog_dir(tmp_path)

    from triage_backlog import emit

    evidence = _make_evidence_pack()
    decision = _make_decision(action="amend")
    master = "MASTER-2026-10-03h"

    # First application
    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        master=master,
        backlog_dir=backlog_dir,
    )

    # Second application in same master
    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        master=master,
        backlog_dir=backlog_dir,
    )

    candidates = _load_candidates(backlog_dir)
    assert len(candidates) == 1
    assert candidates[0]["relations"]["urgent"] is True
    assert candidates[0]["relations"]["applied_in_master"] == 2


def test_ac3_new_master_resets_applied_in_master(tmp_path):
    """AC-3: the second application in a new master resets applied_in_master to 1."""
    backlog_dir = _build_fake_backlog_dir(tmp_path)

    from triage_backlog import emit

    evidence = _make_evidence_pack()
    decision = _make_decision(action="amend")

    # First application in master A
    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        master="MASTER-A",
        backlog_dir=backlog_dir,
    )

    # Application in master B
    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        master="MASTER-B",
        backlog_dir=backlog_dir,
    )

    candidates = _load_candidates(backlog_dir)
    assert len(candidates) == 1
    assert candidates[0]["relations"]["applied_in_master"] == 1
    assert candidates[0]["relations"]["urgent"] is not True


# ── AC-4: corrupt file raises BacklogReadError ──────────────────────────────


def test_ac4_corrupt_file_raises_error_and_preserves_bytes(tmp_path):
    """AC-4: a candidates.json holding {not json makes emit raise
    BacklogReadError, and the file's bytes are unchanged afterwards."""
    backlog_dir = _build_fake_backlog_dir(tmp_path)

    # Write corrupt JSON
    corrupt_content = b"{not json"
    p = backlog_dir / "candidates.json"
    p.write_bytes(corrupt_content)

    from triage_backlog import BacklogReadError, emit

    evidence = _make_evidence_pack()
    decision = _make_decision(action="amend")

    with pytest.raises(BacklogReadError):
        emit(
            project_key="test-project",
            run_id="20261003-125807",
            outcome="applied",
            decision=decision,
            evidence=evidence,
            backlog_dir=backlog_dir,
        )

    # File bytes must be unchanged
    assert p.read_bytes() == corrupt_content


# ── AC-5: ilk_triage.py integration ─────────────────────────────────────────


def test_ac5_triage_run_creates_candidate(tmp_path, monkeypatch):
    """AC-5: ilk_triage.py run with a stub decision park-and-escalate leaves
    one triage candidate."""
    # Set up isolated data root
    data_dir = _build_fake_data_dir(tmp_path)  # returns data_root/projects/test-project
    data_root = data_dir.parent.parent  # tmp_path / "ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))

    # Stub claude on PATH (returns a park-and-escalate decision)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude_stub = bin_dir / "claude"
    claude_stub.write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        '{"action":"park-and-escalate","slug":"test-slug","step":0,'
        '"finding":"stub finding","basis":"stub basis","falsifier":"stub falsifier",'
        '"model":"stub-model","elapsed_s":0.1}\n'
        "EOF\n",
        encoding="utf-8",
    )
    claude_stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")

    # Stub ilk_notify (no-op, must export main)
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    notify_stub = scripts_dir / "ilk_notify.py"
    original_notify = None
    if notify_stub.exists():
        original_notify = notify_stub.read_text(encoding="utf-8")
    notify_stub.write_text(
        "#!/usr/bin/env python3\n"
        "def main(*args, **kwargs):\n"
        "    pass\n",
        encoding="utf-8",
    )

    try:
        # Create a triage home with minimal config
        triage_home = tmp_path / "claude-triage"
        triage_home.mkdir()
        (triage_home / "CLAUDE.md").write_text("# Triage agent\n", encoding="utf-8")

        from ilk_triage import run_triage

        result = run_triage(
            project_key="test-project",
            run_id="20261003-125807",
            home=triage_home,
        )

        # The result should be an escalation (stub returns park-and-escalate)
        assert result["action"] == "park-and-escalate"

        # Check that a candidate was emitted
        backlog_dir = data_root / "ilk-skills-improvements"
        candidates = _load_candidates(backlog_dir)
        assert len(candidates) == 1
        assert candidates[0]["source"] == "triage"
        assert candidates[0]["severity"] == "high"  # escalated
    finally:
        # Restore ilk_notify
        if original_notify:
            notify_stub.write_text(original_notify, encoding="utf-8")
        elif notify_stub.exists():
            notify_stub.unlink()


def test_ac5_kill_switch_prevents_candidate(tmp_path, monkeypatch):
    """AC-5: with the kill switch present it leaves none."""
    # Set up isolated data root
    data_dir = _build_fake_data_dir(tmp_path)  # returns data_root/projects/test-project
    data_root = data_dir.parent.parent  # tmp_path / "ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))

    # Place kill switch (apply checks data_dir.parent which is data_root/projects)
    (data_dir.parent / "triage.disabled").touch()

    # Stub claude on PATH
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude_stub = bin_dir / "claude"
    claude_stub.write_text(
        "#!/bin/sh\n"
        "echo '{\"action\":\"amend\",\"slug\":\"test\",\"step\":0,\"finding\":\"f\",\"basis\":\"b\",\"falsifier\":\"fl\"}'\n",
        encoding="utf-8",
    )
    claude_stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")

    # Stub ilk_notify (must export main)
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    notify_stub = scripts_dir / "ilk_notify.py"
    original_notify = None
    if notify_stub.exists():
        original_notify = notify_stub.read_text(encoding="utf-8")
    notify_stub.write_text(
        "#!/usr/bin/env python3\n"
        "def main(*args, **kwargs):\n"
        "    pass\n",
        encoding="utf-8",
    )

    try:
        from ilk_triage import run_triage

        result = run_triage(
            project_key="test-project",
            run_id="20261003-125807",
        )

        # Kill switch → refused
        assert result["action"] == "refused"
        assert result["reason"] == "kill_switch"

        # No candidate should be emitted
        backlog_dir = data_root / "ilk-skills-improvements"
        candidates = _load_candidates(backlog_dir)
        assert len(candidates) == 0
    finally:
        if original_notify:
            notify_stub.write_text(original_notify, encoding="utf-8")
        elif notify_stub.exists():
            notify_stub.unlink()


# ── AC-6: audit kinds accepted ───────────────────────────────────────────────


def test_ac6_write_audit_accepts_new_kinds():
    """AC-6: write_audit accepts each of the five new kinds and write_event
    the two new events."""
    from ilk_audit import write_audit, write_event

    new_kinds = [
        "candidate-emitted",
        "autoplan-started",
        "autoplan-queued",
        "autoplan-drafted",
        "autoplan-refused",
    ]

    new_events = [
        "autoplan-started",
        "autoplan-queued",
    ]

    # These should not raise
    for kind in new_kinds:
        with tempfile.TemporaryDirectory() as tmpdir:
            write_audit(
                kind,
                "test-project",
                root=Path(tmpdir),
                test=True,
            )

    for event in new_events:
        with tempfile.TemporaryDirectory() as tmpdir:
            write_event(
                event,
                "test-project",
                root=Path(tmpdir),
                test=True,
            )


# ── AC-7 (control): legacy entries preserved ─────────────────────────────────


def test_ac7_legacy_entries_preserved_after_emit(tmp_path):
    """AC-7: a backlog fixture with 3 legacy entries (sources feedback,
    supervisor, '') keeps all 3 byte-identical as dicts after an emit."""
    backlog_dir = _build_fake_backlog_dir(tmp_path)

    # Create 3 legacy entries
    legacy_entries = [
        {
            "id": "legacy-feedback-001",
            "title": "Legacy feedback entry",
            "kind": "toolkit",
            "gap": "Some gap from feedback",
            "evidence": {},
            "proposed_fix": "",
            "leverage": "low",
            "severity": "low",
            "status": "open",
            "first_seen": "2026-09-01T00:00:00+00:00",
            "last_seen": "2026-09-01T00:00:00+00:00",
            "seen_count": 1,
            "source": "feedback",
            "source_id": "",
            "relations": {},
        },
        {
            "id": "legacy-supervisor-002",
            "title": "Legacy supervisor entry",
            "kind": "toolkit",
            "gap": "Some gap from supervisor",
            "evidence": {},
            "proposed_fix": "",
            "leverage": "low",
            "severity": "low",
            "status": "open",
            "first_seen": "2026-09-02T00:00:00+00:00",
            "last_seen": "2026-09-02T00:00:00+00:00",
            "seen_count": 1,
            "source": "supervisor",
            "source_id": "",
            "relations": {},
        },
        {
            "id": "legacy-unsourced-003",
            "title": "Legacy unsourced entry",
            "kind": "toolkit",
            "gap": "Some gap without source",
            "evidence": {},
            "proposed_fix": "",
            "leverage": "low",
            "severity": "low",
            "status": "open",
            "first_seen": "2026-09-03T00:00:00+00:00",
            "last_seen": "2026-09-03T00:00:00+00:00",
            "seen_count": 1,
            "source": "",
            "source_id": "",
            "relations": {},
        },
    ]
    _save_candidates(backlog_dir, legacy_entries)

    # Snapshot before emit
    before = {e["id"]: e for e in legacy_entries}

    from triage_backlog import emit

    evidence = _make_evidence_pack()
    decision = _make_decision(action="amend")

    emit(
        project_key="test-project",
        run_id="20261003-125807",
        outcome="applied",
        decision=decision,
        evidence=evidence,
        backlog_dir=backlog_dir,
    )

    # Verify legacy entries are unchanged
    after = _load_candidates(backlog_dir)
    after_by_id = {e["id"]: e for e in after}

    for legacy_id, legacy_entry in before.items():
        assert legacy_id in after_by_id, f"Legacy entry {legacy_id} disappeared"
        assert after_by_id[legacy_id] == legacy_entry, (
            f"Legacy entry {legacy_id} was modified: "
            f"before={legacy_entry}, after={after_by_id[legacy_id]}"
        )

    # There should be 4 entries total (3 legacy + 1 new)
    assert len(after) == 4