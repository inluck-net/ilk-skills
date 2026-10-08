"""Tests: every shipped batch is mechanically audited before a release train.

Sub-plan: a-shipped-batch-is-audited (step 0).
Covers AC-1..AC-7: scope, gate, tamper, masters checks, unmeasured, and
scheduler wiring.

Each test builds a throwaway git repo and ILK_DATA_HOME under ``tmp_path``.
All external commands are local — no real scheduler, no real releases, and no
real ``~/.ilk-data`` are touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"
WATCHDOG_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-watchdog" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))
sys.path.insert(0, str(WATCHDOG_SCRIPTS))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    # Initial commit so HEAD exists
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def _write_master_plan(
    plans_dir: Path,
    *,
    slug: str = "test-sub",
    base_sha: str | None = "HEAD",
    scope_paths: list[str] | None = None,
) -> Path:
    """Write a MASTER plan with one sub-plan and the given scope_paths."""
    if scope_paths is None:
        scope_paths = ["skills/ilk-ship/"]

    # Resolve base_sha
    if base_sha == "HEAD":
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=plans_dir.parent.parent / "repo",
            capture_output=True, text=True, check=True,
        )
        base_sha = result.stdout.strip()

    master_content = f"""\
---
master_plan: test-batch
batch_date: "2026-10-08"
status: shipped
current_subplan: "{slug}"
base_sha: "{base_sha or ''}"
---

# MASTER plan: test batch

## Sub-plan registry

| # | Order | Slug | Items | Steps (est.) | Status |
|---|---:|---|---|---:|---|
| 1 | 0 | [{slug}.md](./{slug}.md) | test | 2 | shipped |
"""
    master_file = plans_dir / "MASTER-test-batch.md"
    master_file.write_text(master_content, encoding="utf-8")

    sub_content = f"""\
---
plan: {slug}
status: shipped
current_step: 2
estimated_steps: 2
priority: P1
last_updated: 2026-10-08
scope_paths:
"""
    for sp in scope_paths:
        sub_content += f'  - "{sp}"\n'

    sub_content += f"""\
---

# Sub-plan: {slug}

## Steps

### Step 0 — pin
### Step 1 — implement
"""
    sub_file = plans_dir / f"{slug}.md"
    sub_file.write_text(sub_content, encoding="utf-8")

    return master_file


def _write_batch_gate(
    data_dir: Path,
    *,
    verdict: str = "pass",
    attributed: int = 0,
    head_sha: str | None = None,
) -> Path:
    """Write a batch-gate.json record."""
    gate_dir = data_dir / "runtime"
    gate_dir.mkdir(parents=True, exist_ok=True)
    gate_file = gate_dir / "batch-gate.json"

    if head_sha is None:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=data_dir.parent / "repo",
            capture_output=True, text=True, check=True,
        )
        head_sha = result.stdout.strip()

    record = {
        "verdict": verdict,
        "head_sha": head_sha,
        "invocation": "pytest --timeout=60",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "counts": {"attributed": attributed},
    }
    gate_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return gate_file


def _write_iter_log(
    data_dir: Path,
    run_id: str,
    *,
    tool_use_commands: list[str] | None = None,
    tool_results: list[str] | None = None,
) -> Path:
    """Write a fake iter log with optional tool_use and tool_result records."""
    logs_dir = data_dir / "logs" / "runs" / run_id
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "iter-001.log.jsonl"

    records = []

    # Add tool_use records (Bash commands)
    if tool_use_commands:
        for cmd in tool_use_commands:
            records.append({
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": cmd},
            })

    # Add tool_result records (these should NOT trigger tamper)
    if tool_results:
        for content in tool_results:
            records.append({
                "type": "tool_result",
                "content": content,
            })

    with open(log_file, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    return log_file


def _write_sentinel(
    data_dir: Path,
    *,
    state: str = "all-shipped",
    run_id: str = "test-run-001",
    started_at: str = "2026-10-08T10:00:00+0800",
    ended_at: str = "2026-10-08T10:30:00+0800",
) -> Path:
    """Write a sentinel file."""
    launcher_dir = data_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": state,
        "pid": 99999999,
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "iterations": 3,
        "project_path": str(data_dir.parent / "repo"),
        "cli": "claude",
    }
    sentinel_file = launcher_dir / "last-exit.json"
    sentinel_file.write_text(json.dumps(sentinel, indent=2) + "\n", encoding="utf-8")
    return sentinel_file


def _touch_master_at_time(plans_dir: Path, name: str, mtime: float) -> Path:
    """Create a MASTER file with a specific mtime."""
    master = plans_dir / name
    master.write_text("---\nstatus: shipped\n---\n# other master\n")
    os.utime(master, (mtime, mtime))
    return master


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pin HOME and ILK_DATA_HOME so no test touches the real data root."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("ILK_DATA_HOME", str(fake_home / ".ilk-data"))


# ── AC-1: clean run → pass; JSON written under logs/audits/ ─────────────────


class TestAC1CleanPass:
    """AC-1: a clean run passes and writes the audit JSON."""

    def test_clean_run_passes(self, tmp_path: Path) -> None:
        """All checks green → verdict pass, JSON written."""
        repo = _make_git_repo(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        plans_dir = data_dir / "plans"
        plans_dir.mkdir(parents=True)

        run_id = "20261008-120000"
        _write_master_plan(plans_dir, base_sha="HEAD")
        _write_batch_gate(data_dir, verdict="pass", attributed=0)
        _write_sentinel(data_dir, run_id=run_id)
        _write_iter_log(data_dir, run_id)  # clean — no tamper

        # Import and call audit
        sys.path.insert(0, str(SHIP_SCRIPTS))
        from batch_audit import audit

        result = audit(project=repo, data_dir=data_dir, run_id=run_id)

        assert result["verdict"] == "pass", f"expected pass, got {result['verdict']}"
        # JSON written under logs/audits/
        audit_file = data_dir / "logs" / "audits" / f"{run_id}.json"
        assert audit_file.exists(), f"audit file not found at {audit_file}"
        saved = json.loads(audit_file.read_text(encoding="utf-8"))
        assert saved["verdict"] == "pass"


# ── AC-2: scope violation → fail ────────────────────────────────────────────


class TestAC2ScopeViolation:
    """AC-2: a commit touching a file outside scope_paths → fail (scope)."""

    def test_out_of_scope_commit_fails(self, tmp_path: Path) -> None:
        """Commit outside scope_paths → scope check fails."""
        repo = _make_git_repo(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        plans_dir = data_dir / "plans"
        plans_dir.mkdir(parents=True)

        run_id = "20261008-120001"
        # scope_paths is only skills/ilk-ship/
        _write_master_plan(plans_dir, base_sha="HEAD", scope_paths=["skills/ilk-ship/"])
        _write_batch_gate(data_dir, verdict="pass", attributed=0)
        _write_sentinel(data_dir, run_id=run_id)
        _write_iter_log(data_dir, run_id)

        # Commit a file OUTSIDE scope_paths
        (repo / "skills" / "ilk-watchdog" / "new_file.py").parent.mkdir(parents=True, exist_ok=True)
        (repo / "skills" / "ilk-watchdog" / "new_file.py").write_text("# new\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add watchdog file"],
            cwd=repo, check=True, capture_output=True,
        )

        sys.path.insert(0, str(SHIP_SCRIPTS))
        from batch_audit import audit

        result = audit(project=repo, data_dir=data_dir, run_id=run_id)

        assert result["verdict"] == "fail", f"expected fail, got {result['verdict']}"
        # The scope check should be the failing one
        scope_check = next(c for c in result["checks"] if c["name"] == "scope")
        assert scope_check["ok"] is False


# ── AC-3: batch-gate attributed > 0 → fail (gate) ───────────────────────────


class TestAC3GateFailure:
    """AC-3: batch-gate.json attributed > 0 → fail (gate)."""

    def test_attributed_failure_fails(self, tmp_path: Path) -> None:
        """Gate with attributed failures → gate check fails."""
        repo = _make_git_repo(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        plans_dir = data_dir / "plans"
        plans_dir.mkdir(parents=True)

        run_id = "20261008-120002"
        _write_master_plan(plans_dir, base_sha="HEAD")
        _write_batch_gate(data_dir, verdict="pass", attributed=1)
        _write_sentinel(data_dir, run_id=run_id)
        _write_iter_log(data_dir, run_id)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        from batch_audit import audit

        result = audit(project=repo, data_dir=data_dir, run_id=run_id)

        assert result["verdict"] == "fail", f"expected fail, got {result['verdict']}"
        gate_check = next(c for c in result["checks"] if c["name"] == "gate")
        assert gate_check["ok"] is False


# ── AC-4: tamper detection ──────────────────────────────────────────────────


class TestAC4TamperDetection:
    """AC-4: Bash tool_use tampering → fail; tool_result mentioning → pass."""

    def test_tamper_rm_verification_fails(self, tmp_path: Path) -> None:
        """Bash tool_use with rm logs/verification → tamper fails."""
        repo = _make_git_repo(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        plans_dir = data_dir / "plans"
        plans_dir.mkdir(parents=True)

        run_id = "20261008-120003"
        _write_master_plan(plans_dir, base_sha="HEAD")
        _write_batch_gate(data_dir, verdict="pass", attributed=0)
        _write_sentinel(data_dir, run_id=run_id)
        _write_iter_log(
            data_dir, run_id,
            tool_use_commands=["rm -rf logs/verification/x"],
        )

        sys.path.insert(0, str(SHIP_SCRIPTS))
        from batch_audit import audit

        result = audit(project=repo, data_dir=data_dir, run_id=run_id)

        assert result["verdict"] == "fail", f"expected fail, got {result['verdict']}"
        tamper_check = next(c for c in result["checks"] if c["name"] == "tamper")
        assert tamper_check["ok"] is False

    def test_tool_result_mentioning_batch_gate_passes(self, tmp_path: Path) -> None:
        """A tool RESULT containing 'batch-gate.json' is NOT tamper."""
        repo = _make_git_repo(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        plans_dir = data_dir / "plans"
        plans_dir.mkdir(parents=True)

        run_id = "20261008-120004"
        _write_master_plan(plans_dir, base_sha="HEAD")
        _write_batch_gate(data_dir, verdict="pass", attributed=0)
        _write_sentinel(data_dir, run_id=run_id)
        _write_iter_log(
            data_dir, run_id,
            tool_results=["reading batch-gate.json shows verdict: pass"],
        )

        sys.path.insert(0, str(SHIP_SCRIPTS))
        from batch_audit import audit

        result = audit(project=repo, data_dir=data_dir, run_id=run_id)

        tamper_check = next(c for c in result["checks"] if c["name"] == "tamper")
        assert tamper_check["ok"] is True, "tool_result mentioning batch-gate should not be tamper"


# ── AC-5: another master's mtime inside window → fail (masters) ─────────────


class TestAC5MastersCheck:
    """AC-5: another master's mtime inside the run window → fail (masters)."""

    def test_foreign_master_mtime_fails(self, tmp_path: Path) -> None:
        """Another MASTER modified during the run → masters check fails."""
        repo = _make_git_repo(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        plans_dir = data_dir / "plans"
        plans_dir.mkdir(parents=True)

        run_id = "20261008-120005"
        _write_master_plan(plans_dir, base_sha="HEAD")
        _write_batch_gate(data_dir, verdict="pass", attributed=0)

        # Sentinel: run started at 10:00, ended at 10:30
        _write_sentinel(
            data_dir, run_id=run_id,
            started_at="2026-10-08T10:00:00+0800",
            ended_at="2026-10-08T10:30:00+0800",
        )
        _write_iter_log(data_dir, run_id)

        # Create another master with mtime INSIDE the run window
        run_start = datetime(2026, 10, 8, 10, 0, 0).timestamp()
        run_end = datetime(2026, 10, 8, 10, 30, 0).timestamp()
        mid_run = run_start + (run_end - run_start) / 2
        _touch_master_at_time(plans_dir, "MASTER-foreign.md", mid_run)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        from batch_audit import audit

        result = audit(project=repo, data_dir=data_dir, run_id=run_id)

        assert result["verdict"] == "fail", f"expected fail, got {result['verdict']}"
        masters_check = next(c for c in result["checks"] if c["name"] == "masters")
        assert masters_check["ok"] is False


# ── AC-6: no base_sha → unmeasured ──────────────────────────────────────────


class TestAC6Unmeasured:
    """AC-6: no base_sha in the master → unmeasured."""

    def test_no_base_sha_unmeasured(self, tmp_path: Path) -> None:
        """Missing base_sha → scope check is unmeasured."""
        repo = _make_git_repo(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        plans_dir = data_dir / "plans"
        plans_dir.mkdir(parents=True)

        run_id = "20261008-120006"
        _write_master_plan(plans_dir, base_sha=None)  # no base_sha
        _write_batch_gate(data_dir, verdict="pass", attributed=0)
        _write_sentinel(data_dir, run_id=run_id)
        _write_iter_log(data_dir, run_id)

        sys.path.insert(0, str(SHIP_SCRIPTS))
        from batch_audit import audit

        result = audit(project=repo, data_dir=data_dir, run_id=run_id)

        assert result["verdict"] == "unmeasured", f"expected unmeasured, got {result['verdict']}"


# ── AC-7: scheduler wiring ──────────────────────────────────────────────────


class TestAC7SchedulerWiring:
    """AC-7: scheduler.sh maybe_start_release_train calls batch_audit.py."""

    def test_scheduler_calls_audit_after_marker(self, tmp_path: Path) -> None:
        """scheduler.sh's maybe_start_release_train calls batch_audit.py
        after the .started marker check and before the permit check."""
        scheduler = WATCHDOG_SCRIPTS / "scheduler.sh"
        content = scheduler.read_text(encoding="utf-8")

        # Extract maybe_start_release_train function body
        in_func = False
        brace_depth = 0
        func_lines = []
        for line in content.splitlines():
            if "maybe_start_release_train()" in line and "{" in line:
                in_func = True
                brace_depth = 1
                func_lines.append(line)
                continue
            if in_func:
                func_lines.append(line)
                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0:
                    break

        func_body = "\n".join(func_lines)

        # Must contain a call to batch_audit.py
        assert "batch_audit" in func_body, (
            "maybe_start_release_train must call batch_audit.py"
        )

        # The audit must come AFTER the marker check
        marker_pos = func_body.find("marker_file")
        audit_pos = func_body.find("batch_audit")
        assert marker_pos < audit_pos, (
            "batch_audit call must come after the .started marker check"
        )

        # The audit must come BEFORE the permit check
        permit_pos = func_body.find("permit")
        assert audit_pos < permit_pos, (
            "batch_audit call must come before the permit check"
        )

        # Must log skip-audit-failed on failure
        assert "skip-audit-failed" in func_body, (
            "must log skip-audit-failed when audit fails"
        )