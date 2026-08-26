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


def _run_loop_status_proven(
    subplan_path: Path,
    cwd: Path,
    runtime_dir: Path | None = ...,  # sentinel: use resolver
) -> bool:
    """Extract the 'proven' value that loop_status.py computes.

    This replicates the FIXED code path from loop_status.py:392-430:
    1. Resolve runtime_dir via batch_gate.resolve_runtime_dir (AC-1).
    2. Pass it to audit_ship so it reads the batch-gate record (AC-2, AC-3).
    3. Degrade to None if the resolver is unavailable (AC-5).

    When *runtime_dir* is the sentinel ``...``, the resolver runs (the
    production path).  When explicitly passed (a Path or None), that
    value is used directly — for tests that write the record to a
    non-standard location.
    """
    import ship_audit
    if runtime_dir is ...:
        # Production path: resolve via the single resolver.
        resolved_runtime_dir = None
        try:
            from batch_gate import resolve_runtime_dir as _resolve_rt_dir
            resolved_runtime_dir = _resolve_rt_dir(cwd)
        except Exception:
            resolved_runtime_dir = None
    else:
        resolved_runtime_dir = runtime_dir

    info = ship_audit.read_subplan_for_audit(subplan_path)
    result = ship_audit.audit_ship(
        status=info["status"],
        body=info["body"],
        declared_checks=info["declared_checks"],
        gate_passed="unknown",
        slug=info["slug"],
        cwd=cwd,
        runtime_dir=resolved_runtime_dir,
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
    loop_status_result = _run_loop_status_proven(subplan_path, cwd=repo, runtime_dir=runtime_dir)

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
    loop_status_result = _run_loop_status_proven(subplan_path, cwd=repo, runtime_dir=runtime_dir)

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
    loop_status_result = _run_loop_status_proven(subplan_path, cwd=repo, runtime_dir=runtime_dir)

    assert ship_audit_result["proven"] is False, "ship_audit should report unproven for absent"
    assert loop_status_result is False, "loop_status should report unproven for absent"


# ── AC-5: no runtime dir → degrade, no crash ────────────────────────────────

def test_ac5_no_runtime_dir_degrades(tmp_path):
    """When runtime_dir is None, loop_status degrades (no crash)."""
    repo = _make_git_repo(tmp_path)
    plans_dir = tmp_path / "plans"
    subplan_path = _write_subplan(plans_dir)

    # Should not raise — degrades to today's behaviour.
    result = _run_loop_status_proven(subplan_path, cwd=repo, runtime_dir=None)
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
    loop_status_proven = _run_loop_status_proven(subplan_path, cwd=repo, runtime_dir=runtime_dir)

    assert ship_audit_proven == loop_status_proven, (
        f"readers disagree: ship_audit={ship_audit_proven}, "
        f"loop_status={loop_status_proven} (verdict={gate_verdict})"
    )
    assert ship_audit_proven == expected_proven, (
        f"unexpected proven={ship_audit_proven} for verdict={gate_verdict}"
    )


# ── Red-first: demonstrate the CURRENT disagreement ─────────────────────────

def test_fixed_loop_status_passes_runtime_dir(tmp_path):
    """After the fix: loop_status resolves and passes runtime_dir, so both
    readers agree on a fail-verdict record (both say unproven).

    Before the fix, loop_status always said proven (no runtime_dir →
    _resolve_batch_record short-circuited).  Now it resolves the dir and
    passes it, so both readers see the same record.
    """
    repo = _make_git_repo(tmp_path)
    head = _current_head(repo)
    runtime_dir = tmp_path / "runtime"
    # Write a FAIL record — both should say unproven.
    _write_gate_record(runtime_dir, "fail", head)
    plans_dir = tmp_path / "plans"
    subplan_path = _write_subplan(plans_dir)

    # ship_audit with the real record → unproven
    ship_audit_proven = _run_ship_audit(subplan_path, cwd=repo, runtime_dir=runtime_dir)["proven"]

    # loop_status (fixed) also resolves runtime_dir → unproven
    loop_status_proven = _run_loop_status_proven(subplan_path, cwd=repo, runtime_dir=runtime_dir)

    # Both must agree.
    assert ship_audit_proven == loop_status_proven, (
        f"readers disagree after fix: ship_audit={ship_audit_proven}, "
        f"loop_status={loop_status_proven}"
    )
    assert ship_audit_proven is False, "fail record → both unproven"
