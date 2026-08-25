"""Red-first tests: every way an unverified batch could look verified.

Sub-plan: the-audit-reads-the-recorded-verdict
Part of MASTER-2026-08-25b-the-full-suite-runs-once-per-batch

These tests assert that ship_audit reads the persisted batch-gate verdict
instead of taking it on the command line.  AC-1 through AC-4, all red at
step 0.

AC-1: with a batch record present, ship_audit reads the verdict from it
      — no --gate-passed needed.  Asserted for both pass and fail records.
AC-2: when the record's head_sha does NOT match the current HEAD, the audit
      reports STALE and refuses.  Stale is its own outcome.
AC-3: a missing record does not read as a pass.
AC-4: an incomplete record (any of verdict/head_sha/invocation/timestamp
      absent) is invalid, named as such, and does not read as a pass.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

import ship_audit
from batch_gate import BatchGateRecord, write_record


# ── helpers ──────────────────────────────────────────────────────────────────

def _init_repo(path: Path) -> str:
    """Create a git repo with an initial commit.  Returns the HEAD sha."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=path,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path,
        capture_output=True, check=True,
    )
    (path / ".gitkeep").write_text("")
    subprocess.run(
        ["git", "add", ".gitkeep"], cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path,
        capture_output=True, check=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _commit_with_message(path: Path, subject: str, body: str = "") -> None:
    """Create a commit with a specific subject and optional body."""
    (path / "marker.txt").write_text(subject)
    subprocess.run(
        ["git", "add", "marker.txt"], cwd=path, capture_output=True, check=True,
    )
    msg = subject if not body else f"{subject}\n\n{body}"
    subprocess.run(
        ["git", "commit", "-m", msg, "--allow-empty"],
        cwd=path, capture_output=True, check=True,
    )


def _make_shipped_subplan(
    tmp: Path,
    *,
    steps: list[int] = (0, 1),
    has_gate: bool = True,
    slug: str = "test-slug",
) -> Path:
    """Create a minimal shipped sub-plan file."""
    checks_yaml = textwrap.dedent("""\
        local_checks:
          - command: echo ok
            timeout: 10
    """) if has_gate else "local_checks: []\n"
    step_headings = "\n".join(f"### Step {n}" for n in steps)
    body = textwrap.dedent(f"""\
        ---
        plan: {slug}
        status: shipped
        current_step: {len(steps)}
        {checks_yaml}---
        {step_headings}
    """)
    subplan = tmp / f"2026-08-25-{slug}.md"
    subplan.write_text(body)
    return subplan


def _write_raw_record(runtime_dir: Path, data: dict) -> None:
    """Write a raw JSON dict as batch-gate.json (bypasses validation)."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "batch-gate.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8",
    )


# ── AC-1: reads the verdict from the record ─────────────────────────────────

class TestAC1ReadsVerdictFromRecord:
    """With a batch record present, ship_audit reads the verdict from it."""

    def test_pass_record_means_gate_passed(self, tmp_path: Path) -> None:
        """AC-1a: a pass record → gate passes, no --gate-passed needed."""
        head_sha = _init_repo(tmp_path)
        _commit_with_message(tmp_path, "feat(x): s0 [plan:test-slug#step-0]")
        _commit_with_message(tmp_path, "feat(x): s1 [plan:test-slug#step-1]")
        runtime = tmp_path / "runtime"
        write_record(
            BatchGateRecord(
                verdict="pass",
                head_sha=head_sha,
                invocation="python3 -m pytest -q",
                timestamp="2026-08-25T16:00:00+08:00",
            ),
            runtime,
        )
        subplan = _make_shipped_subplan(tmp_path)
        info = ship_audit.read_subplan_for_audit(subplan)
        result = ship_audit.audit_ship(
            status=info["status"],
            body=info["body"],
            declared_checks=info["declared_checks"],
            gate_passed="unknown",  # NOT supplied — record should be used
            slug=info["slug"],
            cwd=tmp_path,
            runtime_dir=runtime,
        )
        assert result["final_gate"] == "pass", (
            f"Pass record should yield final_gate='pass', got {result['final_gate']!r}"
        )
        assert result["proven"] is True

    def test_fail_record_means_gate_failed(self, tmp_path: Path) -> None:
        """AC-1b: a fail record → gate fails."""
        head_sha = _init_repo(tmp_path)
        _commit_with_message(tmp_path, "feat(x): s0 [plan:test-slug#step-0]")
        _commit_with_message(tmp_path, "feat(x): s1 [plan:test-slug#step-1]")
        runtime = tmp_path / "runtime"
        write_record(
            BatchGateRecord(
                verdict="fail",
                head_sha=head_sha,
                invocation="python3 -m pytest -q",
                timestamp="2026-08-25T16:00:00+08:00",
            ),
            runtime,
        )
        subplan = _make_shipped_subplan(tmp_path)
        info = ship_audit.read_subplan_for_audit(subplan)
        result = ship_audit.audit_ship(
            status=info["status"],
            body=info["body"],
            declared_checks=info["declared_checks"],
            gate_passed="unknown",
            slug=info["slug"],
            cwd=tmp_path,
            runtime_dir=runtime,
        )
        assert result["final_gate"] == "fail", (
            f"Fail record should yield final_gate='fail', got {result['final_gate']!r}"
        )
        assert result["proven"] is False


# ── AC-2: staleness — head_sha mismatch ─────────────────────────────────────

class TestAC2Staleness:
    """When the record's head_sha does not match current HEAD → stale."""

    def test_stale_record_is_own_outcome(self, tmp_path: Path) -> None:
        """AC-2: stale record → refused, distinguishable from pass and fail."""
        head_sha = _init_repo(tmp_path)
        _commit_with_message(tmp_path, "feat(x): s0 [plan:test-slug#step-0]")
        _commit_with_message(tmp_path, "feat(x): s1 [plan:test-slug#step-1]")
        runtime = tmp_path / "runtime"
        # Write a record with a DIFFERENT head_sha (simulating staleness)
        write_record(
            BatchGateRecord(
                verdict="pass",
                head_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                invocation="python3 -m pytest -q",
                timestamp="2026-08-25T16:00:00+08:00",
            ),
            runtime,
        )
        subplan = _make_shipped_subplan(tmp_path)
        info = ship_audit.read_subplan_for_audit(subplan)
        result = ship_audit.audit_ship(
            status=info["status"],
            body=info["body"],
            declared_checks=info["declared_checks"],
            gate_passed="unknown",
            slug=info["slug"],
            cwd=tmp_path,
            runtime_dir=runtime,
        )
        # Stale must be its own outcome — not "pass", not "fail"
        assert result["final_gate"] != "pass", "Stale record must not read as pass"
        assert result["final_gate"] != "fail", "Stale must be distinguishable from fail"
        assert result["proven"] is False, "Stale record must not be proven"
        # The reason should mention staleness
        assert any("stale" in r.lower() for r in result["reasons"]), (
            f"Expected a reason mentioning 'stale', got {result['reasons']}"
        )


# ── AC-3: missing record ≠ pass ─────────────────────────────────────────────

class TestAC3MissingRecord:
    """A missing record does not read as a pass."""

    def test_no_record_file_not_a_pass(self, tmp_path: Path) -> None:
        """AC-3: no batch-gate.json → must not be a pass."""
        _init_repo(tmp_path)
        _commit_with_message(tmp_path, "feat(x): s0 [plan:test-slug#step-0]")
        _commit_with_message(tmp_path, "feat(x): s1 [plan:test-slug#step-1]")
        runtime = tmp_path / "runtime"
        # No write_record — file does not exist
        subplan = _make_shipped_subplan(tmp_path)
        info = ship_audit.read_subplan_for_audit(subplan)
        result = ship_audit.audit_ship(
            status=info["status"],
            body=info["body"],
            declared_checks=info["declared_checks"],
            gate_passed="unknown",
            slug=info["slug"],
            cwd=tmp_path,
            runtime_dir=runtime,
        )
        assert result["final_gate"] != "pass", (
            "Missing record must not read as a pass"
        )
        assert result["proven"] is False


# ── AC-4: incomplete record — each missing field ────────────────────────────

class TestAC4IncompleteRecord:
    """An incomplete record is invalid, named as such, per missing field."""

    COMPLETE = {
        "verdict": "pass",
        "head_sha": "a" * 40,
        "invocation": "python3 -m pytest -q",
        "timestamp": "2026-08-25T16:00:00+08:00",
    }

    @pytest.mark.parametrize("field", ["verdict", "head_sha", "invocation", "timestamp"])
    def test_missing_field_not_a_pass(self, tmp_path: Path, field: str) -> None:
        """AC-4: record missing '{field}' is invalid, not a pass."""
        _init_repo(tmp_path)
        _commit_with_message(tmp_path, "feat(x): s0 [plan:test-slug#step-0]")
        _commit_with_message(tmp_path, "feat(x): s1 [plan:test-slug#step-1]")
        runtime = tmp_path / "runtime"
        data = {k: v for k, v in self.COMPLETE.items() if k != field}
        _write_raw_record(runtime, data)
        subplan = _make_shipped_subplan(tmp_path)
        info = ship_audit.read_subplan_for_audit(subplan)
        result = ship_audit.audit_ship(
            status=info["status"],
            body=info["body"],
            declared_checks=info["declared_checks"],
            gate_passed="unknown",
            slug=info["slug"],
            cwd=tmp_path,
            runtime_dir=runtime,
        )
        assert result["final_gate"] != "pass", (
            f"Record missing '{field}' must not read as a pass"
        )
        assert result["proven"] is False, (
            f"Record missing '{field}' must not be proven"
        )
        assert any("invalid" in r.lower() or "missing" in r.lower() or field in r
                    for r in result["reasons"]), (
            f"Expected reason naming the invalid/missing field, got {result['reasons']}"
        )
