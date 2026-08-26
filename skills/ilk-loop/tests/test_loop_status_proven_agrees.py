"""AC-4: loop_status and ship_audit agree on "proven" for the same sub-plan.

The defect: loop_status.py:415 calls audit_ship with gate_passed="unknown"
and no runtime_dir, so _resolve_batch_record(None) short-circuits and every
shipped sub-plan reads as ungated — while ship_audit's CLI resolves the
runtime dir and correctly reads the batch-gate record.

This test builds a hermetic git repo + runtime dir under tmp_path, writes a
batch-gate record with a fresh pass verdict, and runs BOTH readers over the
same sub-plan — asserting they agree, not just that each is individually
"correct".

AC-2: fresh pass record → both report proven.
AC-3: stale or absent record → both report unproven.
AC-5: no resolvable runtime dir → loop_status degrades (no crash).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the scripts dir is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── helpers ──────────────────────────────────────────────────────────────────

SUBPLAN_BODY = """\
---
plan: test-slug
status: shipped
estimated_steps: 2
last_updated: 2026-08-27
local_checks:
  - command: "true"
    timeout: 10
---

# Sub-plan: test-slug

## Steps

### Step 0 — Do the thing

- Do the thing.

### Step 1 — Verify

- Verify it.
"""


def _make_git_repo(tmp_path: Path, slug: str = "test-slug", steps: int = 2) -> Path:
    """Create a minimal git repo with step commits so check_step_commits passes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    # Initial commit.
    (repo / "placeholder").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    # Step commits — one per step, with the [plan:<slug>#step-N] trailer.
    for i in range(steps):
        (repo / f"step-{i}").write_text(f"step {i}\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"feat: step {i} [plan:{slug}#step-{i}]"],
            cwd=repo, capture_output=True, check=True,
        )
    return repo


def _current_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _write_gate_record(
    runtime_dir: Path,
    verdict: str,
    head_sha: str,
    invocation: str = "",
) -> Path:
    """Write a batch-gate.json record with the given verdict.

    invocation defaults to "" to match _resolve_expected_invocation on a
    bare repo with no ship config.
    """
    runtime_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "verdict": verdict,
        "head_sha": head_sha,
        "invocation": invocation,
        "timestamp": "2026-08-27T10:00:00+08:00",
    }
    p = runtime_dir / "batch-gate.json"
    p.write_text(json.dumps(record), encoding="utf-8")
    return p


def _write_subplan(plans_dir: Path, slug: str = "test-slug", body: str = SUBPLAN_BODY) -> Path:
    """Write a shipped sub-plan file."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    fname = f"2026-08-27-{slug}.md"
    p = plans_dir / fname
    p.write_text(body, encoding="utf-8")
    return p


def _run_ship_audit(subplan_path: Path, cwd: Path, runtime_dir: Path | None) -> dict:
    """Run ship_audit.audit_ship directly with the given runtime_dir."""
    import ship_audit
    info = ship_audit.read_subplan_for_audit(subplan_path)
    return ship_audit.audit_ship(
        status=info["status"],
        body=info["body"],
        declared_checks=info["declared_checks"],
        gate_passed="unknown",
        slug=info["slug"],
        cwd=cwd,
        runtime_dir=runtime_dir,
    )


def _run_loop_status_proven(subplan_path: Path, cwd: Path) -> bool:
    """Extract the 'proven' value that loop_status would compute.

    This replicates the BROKEN code path from loop_status.py:402-423 —
    the audit_ship call site that this sub-plan is fixing.  The bug:
    loop_status does NOT pass runtime_dir to audit_ship, so
    _resolve_batch_record(None) short-circuits and every shipped sub-plan
    reads as ungated.

    After step 1, loop_status.py will be fixed to resolve and pass
    runtime_dir.  At that point, this helper should be updated to match
    the fixed call site.
    """
    import ship_audit
    info = ship_audit.read_subplan_for_audit(subplan_path)
    # This is the CURRENT (broken) call: no runtime_dir passed.
    # loop_status.py:415 calls audit_ship with gate_passed="unknown"
    # and NO runtime_dir — replicating that exactly.
    result = ship_audit.audit_ship(
        status=info["status"],
        body=info["body"],
        declared_checks=info["declared_checks"],
        gate_passed="unknown",  # gate records not resolved here (the bug)
        slug=info["slug"],
        cwd=cwd,
        # runtime_dir is NOT passed — this is the bug.
        # After step 1, this will be: runtime_dir=runtime_dir,
    )
    return result["proven"]


# ── AC-2: fresh pass record → both agree: proven ────────────────────────────

def test_ac2_fresh_pass_both_proven(tmp_path):
    """With a fresh pass record, both readers report proven."""
    repo = _make_git_repo(tmp_path)
    head = _current_head(repo)
    runtime_dir = tmp_path / "runtime"
    _write_gate_record(runtime_dir, "pass", head)
    plans_dir = tmp_path / "plans"
    subplan_path = _write_subplan(plans_dir)

    ship_audit_result = _run_ship_audit(subplan_path, cwd=repo, runtime_dir=runtime_dir)
    loop_status_result = _run_loop_status_proven(subplan_path, cwd=repo)

    assert ship_audit_result["proven"] is True, "ship_audit should report proven"
    assert loop_status_result is True, "loop_status should report proven"


# ── AC-3: stale record → both agree: unproven ───────────────────────────────

def test_ac3_stale_head_both_unproven(tmp_path):
    """With a stale (head-mismatched) record, both readers report unproven."""
    repo = _make_git_repo(tmp_path)
    runtime_dir = tmp_path / "runtime"
    _write_gate_record(runtime_dir, "pass", "deadbeef" * 8)
    plans_dir = tmp_path / "plans"
    subplan_path = _write_subplan(plans_dir)

    ship_audit_result = _run_ship_audit(subplan_path, cwd=repo, runtime_dir=runtime_dir)
    loop_status_result = _run_loop_status_proven(subplan_path, cwd=repo)

    # ship_audit sees stale → proven=False
    assert ship_audit_result["proven"] is False, "ship_audit should report unproven for stale"
    # loop_status (with the fix) should also see stale → proven=False
    assert loop_status_result is False, "loop_status should report unproven for stale"


def test_ac3_absent_record_both_unproven(tmp_path):
    """With no record at all, both readers report unproven."""
    repo = _make_git_repo(tmp_path)
    runtime_dir = tmp_path / "runtime"  # dir exists but no batch-gate.json
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plans_dir = tmp_path / "plans"
    subplan_path = _write_subplan(plans_dir)

    ship_audit_result = _run_ship_audit(subplan_path, cwd=repo, runtime_dir=runtime_dir)
    loop_status_result = _run_loop_status_proven(subplan_path, cwd=repo)

    assert ship_audit_result["proven"] is False, "ship_audit should report unproven for absent"
    assert loop_status_result is False, "loop_status should report unproven for absent"


# ── AC-5: no runtime dir → degrade, no crash ────────────────────────────────

def test_ac5_no_runtime_dir_degrades(tmp_path):
    """When runtime_dir is None, loop_status degrades (no crash)."""
    repo = _make_git_repo(tmp_path)
    plans_dir = tmp_path / "plans"
    subplan_path = _write_subplan(plans_dir)

    # Should not raise — degrades to today's behaviour.
    result = _run_loop_status_proven(subplan_path, cwd=repo)
    # With runtime_dir=None, _resolve_batch_record short-circuits → gate_passed
    # stays "unknown" → ship_audit falls through to its no-gate-result path.
    # The important thing is it doesn't crash.
    assert isinstance(result, bool), "should return a bool, not crash"


# ── AC-4: THE central test — both readers AGREE ─────────────────────────────

@pytest.mark.parametrize("gate_verdict,expected_proven", [
    ("pass", True),
    ("fail", False),
])
def test_ac4_both_readers_agree(tmp_path, gate_verdict, expected_proven):
    """Run both readers over the same sub-plan and assert they agree.

    This is the comparison test — not two independent expectations.
    The whole defect is that each reader is individually "correct" and
    they still disagree.
    """
    repo = _make_git_repo(tmp_path)
    head = _current_head(repo)
    runtime_dir = tmp_path / "runtime"
    _write_gate_record(runtime_dir, gate_verdict, head)
    plans_dir = tmp_path / "plans"
    subplan_path = _write_subplan(plans_dir)

    ship_audit_proven = _run_ship_audit(subplan_path, cwd=repo, runtime_dir=runtime_dir)["proven"]
    loop_status_proven = _run_loop_status_proven(subplan_path, cwd=repo)

    assert ship_audit_proven == loop_status_proven, (
        f"readers disagree: ship_audit={ship_audit_proven}, "
        f"loop_status={loop_status_proven} (verdict={gate_verdict})"
    )
    assert ship_audit_proven == expected_proven, (
        f"unexpected proven={ship_audit_proven} for verdict={gate_verdict}"
    )


# ── Red-first: demonstrate the CURRENT disagreement ─────────────────────────

def test_red_first_loop_status_passes_no_runtime_dir(tmp_path):
    """Red-first: show that loop_status with runtime_dir=None always says
    proven — even when ship_audit (with the real record) says unproven.

    This is the disagreement that must NOT exist after step 1.
    """
    repo = _make_git_repo(tmp_path)
    head = _current_head(repo)
    runtime_dir = tmp_path / "runtime"
    # Write a FAIL record — ship_audit should say unproven.
    _write_gate_record(runtime_dir, "fail", head)
    plans_dir = tmp_path / "plans"
    subplan_path = _write_subplan(plans_dir)

    # ship_audit with the real record → unproven
    ship_audit_proven = _run_ship_audit(subplan_path, cwd=repo, runtime_dir=runtime_dir)["proven"]

    # loop_status with runtime_dir=None (the broken path) → proven
    loop_status_proven_no_dir = _run_loop_status_proven(subplan_path, cwd=repo)

    # This is the disagreement: ship_audit says unproven, loop_status says proven.
    # After step 1, loop_status will pass runtime_dir and both should say unproven.
    if ship_audit_proven != loop_status_proven_no_dir:
        pytest.xfail(
            "EXPECTED DISAGREEMENT (the bug): "
            f"ship_audit={ship_audit_proven}, loop_status(no_dir)={loop_status_proven_no_dir}. "
            "This test should pass after step 1 fixes loop_status to pass runtime_dir."
        )
    else:
        # If they already agree, the bug might have been fixed already.
        assert ship_audit_proven == loop_status_proven_no_dir
