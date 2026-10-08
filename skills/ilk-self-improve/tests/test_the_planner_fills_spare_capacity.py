"""Tests: the planner fills spare capacity.

Sub-plan: the-planner-fills-spare-capacity (step 0).
Covers AC-1..AC-7: busy scope, authoring filter, idle threshold,
outcome-based pacing, daily cap, yield priority, pause expiry.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
_LOOP_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts")
_SELF_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
_WATCHDOG_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-watchdog" / "scripts")
_FEEDBACK_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts")

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "autoplan"


# ── helpers ──────────────────────────────────────────────────────────────


def _load_module():
    """Import autoplan."""
    for d in (_LOOP_SCRIPTS, _SELF_SCRIPTS, _WATCHDOG_SCRIPTS, _FEEDBACK_SCRIPTS):
        if d not in sys.path:
            sys.path.insert(0, d)
    import autoplan
    return autoplan


def _make_candidate(
    *,
    cid: str = "sig-abc123",
    title: str = "Missing feature X",
    gap: str = "No support for X",
    source: str = "triage",
    status: str = "open",
    seen_count: int = 1,
    escalations: int = 0,
    urgent: bool = False,
    autoplan_attempts: int = 0,
    autoplan_blocked: bool = False,
    first_seen: str = "2026-10-03T12:00:00+0800",
) -> dict:
    """Build a minimal backlog entry."""
    return {
        "id": cid,
        "title": title,
        "gap": gap,
        "source": source,
        "status": status,
        "seen_count": seen_count,
        "first_seen": first_seen,
        "relations": {
            "escalations": escalations,
            "urgent": urgent,
            "autoplan_attempts": autoplan_attempts,
            "autoplan_blocked": autoplan_blocked,
        },
        "evidence": [],
        "proposed_fix": "",
        "kind": "toolkit",
        "leverage": "medium",
        "severity": "medium",
    }


def _save_candidates(backlog_dir: Path, entries: list[dict]) -> None:
    """Save candidates to the backlog directory."""
    p = backlog_dir / "candidates.json"
    p.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")


def _build_fake_data_root(tmp_path) -> Path:
    """Create a fake data root with projects/ and autoplan/ dirs."""
    data_root = Path(tmp_path) / "ilk-data"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "projects").mkdir(exist_ok=True)
    (data_root / "autoplan").mkdir(exist_ok=True)
    (data_root / "audit").mkdir(exist_ok=True)
    return data_root


def _build_fake_toolkit(tmp_path, data_root) -> Path:
    """Create a fake toolkit repo with commands/ilk-plan.md and .ilk-launch.json."""
    toolkit = Path(tmp_path) / "toolkit"
    toolkit.mkdir(parents=True, exist_ok=True)
    (toolkit / "commands").mkdir(exist_ok=True)
    (toolkit / "commands" / "ilk-plan.md").write_text("# ilk-plan\n", encoding="utf-8")
    (toolkit / ".ilk-launch.json").write_text(
        json.dumps({"autoplan": {"enabled": True}}) + "\n",
        encoding="utf-8",
    )
    plans_dir = data_root / "plans"
    plans_dir.mkdir(exist_ok=True)
    return toolkit


def _build_fake_manager_home(tmp_path) -> Path:
    """Create a fake manager home directory."""
    home = Path(tmp_path) / ".claude-manager"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _setup_toolkit_project(data_root: Path, toolkit: Path) -> None:
    """Create a project data dir that resolves to the toolkit."""
    project_dir = data_root / "projects" / "test-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
    (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
        json.dumps({"project_path": str(toolkit)}) + "\n",
        encoding="utf-8",
    )


def _setup_other_project(data_root: Path) -> Path:
    """Create a second project data dir (not the toolkit)."""
    project_dir = data_root / "projects" / "other-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
    return project_dir


def _make_master(plans_dir: Path, name: str, *, status: str = "queued",
                 auto_planned: bool = False) -> None:
    """Write a minimal MASTER frontmatter file."""
    auto = "\nauto_planned: true" if auto_planned else ""
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / name).write_text(
        f"---\nstatus: {status}{auto}\n---\n\n# {name}\n\n"
        "## Sub-plan registry\n\n| # | Slug |\n|---|---|\n"
        "| 1 | [sub.md](./sub.md) |\n",
        encoding="utf-8",
    )
    # Write the referenced sub-plan so master_has_runnable can find it
    sub_name = name.replace("MASTER-", "").replace("-execution-plan.md", ".md")
    sub_path = plans_dir / sub_name
    if not sub_path.exists():
        sub_path.write_text(
            "---\nplan: sub\nstatus: pending\ncurrent_step: 0\n---\n\n# sub\n",
            encoding="utf-8",
        )


def _read_audit_rows(data_root: Path, kind: str | None = None) -> list[dict]:
    """Read audit rows from the data root, optionally filtered by kind."""
    audit_dir = data_root / "audit"
    rows = []
    for f in audit_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if kind is None or row.get("kind") == kind:
                    rows.append(row)
    return rows


# ── AC-1: busy scope ─────────────────────────────────────────────────────


def test_ac1_running_pid_in_other_not_busy(tmp_path):
    """A live running.pid in `other` → _is_busy False."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)
    other = _setup_other_project(data_root)

    pid_file = other / "runtime" / "launcher" / "running.pid"
    pid_file.write_text(str(os.getpid()) + "\n", encoding="utf-8")

    assert mod._is_busy(data_root) is False


def test_ac1_running_pid_in_toolkit_busy(tmp_path):
    """A live running.pid in the toolkit → _is_busy True."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    project_dir = data_root / "projects" / "test-project"
    pid_file = project_dir / "runtime" / "launcher" / "running.pid"
    pid_file.write_text(str(os.getpid()) + "\n", encoding="utf-8")

    assert mod._is_busy(data_root) is True


def test_ac1_train_lock_in_other_busy(tmp_path):
    """A live train.lock in `other` → _is_busy True (fleet-wide)."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)
    other = _setup_other_project(data_root)

    train_lock = other / "runtime" / "release" / "train.lock"
    train_lock.parent.mkdir(parents=True, exist_ok=True)
    train_lock.write_text(str(os.getpid()) + "\n", encoding="utf-8")

    assert mod._is_busy(data_root) is True


# ── AC-2: authoring filter ───────────────────────────────────────────────


def test_ac2_shipped_master_recently_not_busy(tmp_path):
    """A toolkit `shipped` master with mtime now → not busy."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    plans_dir = data_root / "plans"
    _make_master(plans_dir, "MASTER-2026-10-07-shipped-execution-plan.md",
                 status="shipped")

    assert mod._is_busy(data_root) is False


def test_ac2_draft_master_10min_ago_busy(tmp_path):
    """A toolkit `draft` master with mtime 10 min ago → busy (RECENT_AUTHORING_MIN=20)."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    plans_dir = data_root / "plans"
    master = plans_dir / "MASTER-2026-10-07-draft-execution-plan.md"
    _make_master(plans_dir, master.name, status="draft")
    # Set mtime to 10 minutes ago
    ten_min_ago = time.time() - 10 * 60
    os.utime(master, (ten_min_ago, ten_min_ago))

    assert mod._is_busy(data_root) is True


def test_ac2_draft_master_30min_ago_not_busy(tmp_path):
    """A toolkit `draft` master with mtime 30 min ago → not busy."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    plans_dir = data_root / "plans"
    master = plans_dir / "MASTER-2026-10-07-draft-execution-plan.md"
    _make_master(plans_dir, master.name, status="draft")
    # Set mtime to 30 minutes ago
    thirty_min_ago = time.time() - 30 * 60
    os.utime(master, (thirty_min_ago, thirty_min_ago))

    assert mod._is_busy(data_root) is False


# ── AC-3: idle threshold ────────────────────────────────────────────────


def test_ac3_three_idle_ticks_reach_threshold(tmp_path):
    """Three idle ticks reach the threshold (IDLE_CYCLES_N=3)."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    result1 = mod.tick(data_root=data_root)
    assert result1["decision"] == "idle"
    assert result1["idle_cycles"] == 1

    result2 = mod.tick(data_root=data_root)
    assert result2["decision"] == "idle"
    assert result2["idle_cycles"] == 2

    # Third tick: at threshold, should attempt a start (or hit another gate).
    # With no candidate/backlog, expect backlog-unreadable or similar.
    result3 = mod.tick(data_root=data_root)
    assert result3["decision"] != "idle"


# ── AC-4: outcome-based pacing ──────────────────────────────────────────


def test_ac4_refused_30min_ago_rate_limited(tmp_path):
    """last_outcome: refused 30 min ago → rate-limited (REFUSED_BACKOFF_MIN=60)."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    state_file = data_root / "autoplan" / "state.json"
    state_file.write_text(json.dumps({
        "idle_cycles": 3,
        "last_start": time.time() - 30 * 60,
        "last_outcome": "refused",
    }) + "\n", encoding="utf-8")

    result = mod.tick(data_root=data_root)
    assert result["decision"] == "rate-limited"


def test_ac4_refused_70min_ago_not_rate_limited(tmp_path):
    """last_outcome: refused 70 min ago → not rate-limited."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    state_file = data_root / "autoplan" / "state.json"
    state_file.write_text(json.dumps({
        "idle_cycles": 3,
        "last_start": time.time() - 70 * 60,
        "last_outcome": "refused",
    }) + "\n", encoding="utf-8")

    result = mod.tick(data_root=data_root)
    assert result["decision"] != "rate-limited"


def test_ac4_queued_5min_ago_not_rate_limited(tmp_path):
    """last_outcome: queued 5 min ago → not rate-limited by time."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    state_file = data_root / "autoplan" / "state.json"
    state_file.write_text(json.dumps({
        "idle_cycles": 3,
        "last_start": time.time() - 5 * 60,
        "last_outcome": "queued",
    }) + "\n", encoding="utf-8")

    result = mod.tick(data_root=data_root)
    assert result["decision"] != "rate-limited"


# ── AC-5: daily cap ──────────────────────────────────────────────────────


def test_ac5_starts_today_8_rate_limited(tmp_path):
    """starts_today: 8 for today's date → rate-limited with daily-cap."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    state_file = data_root / "autoplan" / "state.json"
    state_file.write_text(json.dumps({
        "idle_cycles": 3,
        "starts_day": time.strftime("%Y-%m-%d"),
        "starts_today": 8,
    }) + "\n", encoding="utf-8")

    result = mod.tick(data_root=data_root)
    assert result["decision"] == "rate-limited"


def test_ac5_yesterdays_date_not_capped(tmp_path):
    """starts_day is yesterday's date → not capped."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    state_file = data_root / "autoplan" / "state.json"
    state_file.write_text(json.dumps({
        "idle_cycles": 3,
        "starts_day": yesterday,
        "starts_today": 8,
    }) + "\n", encoding="utf-8")

    result = mod.tick(data_root=data_root)
    assert result["decision"] != "rate-limited"


# ── AC-6: yield priority ────────────────────────────────────────────────


def test_ac6_yield_priority_0(tmp_path):
    """A stubbed plan that writes a master yields priority: 0."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    manager_home = _build_fake_manager_home(tmp_path)

    backlog_dir = data_root / "ilk-skills-improvements"
    backlog_dir.mkdir(parents=True, exist_ok=True)
    _save_candidates(backlog_dir, [_make_candidate()])

    plans_dir = data_root / "plans"
    plans_dir.mkdir(exist_ok=True)

    # Import the fixture batch copy helper
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_an_idle_window_plans_the_top_candidate import (
        _make_stub_claude, _make_stub_lint_preflight, FIXTURE_DIR,
    )
    import subprocess

    stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                     write_batch=True)
    marker = tmp_path / "claude-marker.txt"
    lint, preflight = _make_stub_lint_preflight(tmp_path)

    result = mod.plan(
        candidate_id="sig-abc123",
        project_key="test-project",
        run_id="run-yield-001",
        data_root=data_root,
        toolkit_repo=str(toolkit),
        manager_home=str(manager_home),
        claude_cmd=[sys.executable, str(stub_claude)],
        lint_cmd=[sys.executable, str(lint)],
        preflight_cmd=[sys.executable, str(preflight)],
        env_overrides={
            "ILK_PLANS_DIR": str(plans_dir),
            "ILK_REPO_DIR": str(toolkit),
            "ILK_MARKER": str(marker),
            "ILK_FIXTURE_DIR": str(FIXTURE_DIR),
        },
    )

    from plan_status import parse_frontmatter
    masters = list(plans_dir.glob("MASTER-*.md"))
    assert len(masters) == 1
    fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
    assert fm.get("priority") == "0", (
        f"Auto-planned master should yield with priority: 0, got: {fm.get('priority')}"
    )


# ── AC-7: pause expiry ──────────────────────────────────────────────────


def test_ac7_pause_expired_removed(tmp_path):
    """paused.json with `until` 1 min ago → removed, audit row, tick proceeds."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    paused_file = data_root / "autoplan" / "paused.json"
    one_min_ago = datetime.now(timezone.utc).isoformat()
    # Use a time that's definitely in the past
    paused_file.write_text(json.dumps({
        "by": "test",
        "reason": "test pause",
        "since": "2026-10-07T10:00:00+08:00",
        "until": "2020-01-01T00:00:00+00:00",
    }) + "\n", encoding="utf-8")

    result = mod.tick(data_root=data_root)
    assert result["decision"] != "paused"

    assert not paused_file.exists(), "Expired paused.json should be removed"

    rows = _read_audit_rows(data_root, "autoplan-resumed")
    assert len(rows) >= 1
    assert rows[0].get("reason") == "pause expired"


def test_ac7_pause_not_expired_stays_paused(tmp_path):
    """paused.json with `until` 1 h ahead → paused."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    from datetime import timedelta
    one_hour_ahead = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    paused_file = data_root / "autoplan" / "paused.json"
    paused_file.write_text(json.dumps({
        "by": "test",
        "reason": "test pause",
        "since": datetime.now(timezone.utc).isoformat(),
        "until": one_hour_ahead,
    }) + "\n", encoding="utf-8")

    result = mod.tick(data_root=data_root)
    assert result["decision"] == "paused"


def test_ac7_pause_unparseable_stays_paused(tmp_path):
    """paused.json with unparseable JSON → paused (fail closed)."""
    mod = _load_module()
    data_root = _build_fake_data_root(tmp_path)
    toolkit = _build_fake_toolkit(tmp_path, data_root)
    _setup_toolkit_project(data_root, toolkit)

    paused_file = data_root / "autoplan" / "paused.json"
    paused_file.write_text("not valid json {{{", encoding="utf-8")

    result = mod.tick(data_root=data_root)
    assert result["decision"] == "paused"