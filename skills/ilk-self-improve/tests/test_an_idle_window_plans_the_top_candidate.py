"""Tests: an idle window plans the top candidate.

Sub-plan: an-idle-window-plans-the-top-candidate (step 1).
Covers AC-1..AC-8: tick idle count, busy reset, threshold gates,
kernel escalation, plan draft-or-queued decision, consecutive drafts pause,
and stale-reproduce control.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
_LOOP_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts")
_SELF_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
_WATCHDOG_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-watchdog" / "scripts")
_FEEDBACK_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts")

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "autoplan"


# ── helpers ──────────────────────────────────────────────────────────────────


def _load_module():
    """Import autoplan (fails until step 1 ships)."""
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


def _load_candidates(backlog_dir: Path) -> list[dict]:
    """Load candidates from the backlog directory."""
    p = backlog_dir / "candidates.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


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
    # Create a fake plans dir in the data root
    plans_dir = data_root / "plans"
    plans_dir.mkdir(exist_ok=True)
    return toolkit


def _build_fake_manager_home(tmp_path) -> Path:
    """Create a fake manager home directory."""
    home = Path(tmp_path) / ".claude-manager"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _make_stub_claude(tmp_path, *, model: str = "claude-opus-test",
                       write_batch: bool = True,
                       modify_repo: bool = False,
                       write_nothing: bool = False,
                       stale_reason: str | None = None) -> Path:
    """Create a stub claude script that prints stream-json and optionally writes fixtures.

    Returns the path to the stub script.
    """
    stub = Path(tmp_path) / "stub-claude.py"

    # Build the init event
    init_event = json.dumps({
        "type": "system",
        "subtype": "init",
        "model": model,
    })

    # Build the output script
    lines = [
        "#!/usr/bin/env python3",
        "import json, os, sys, pathlib",
        "",
        "# Print init event",
        f"print({init_event!r})",
        "",
    ]

    if write_nothing and stale_reason:
        # Just print the AUTOPLAN line
        lines.append(f"print('AUTOPLAN: stale {stale_reason}')")
    elif write_batch:
        # Write the fixture batch to the plans dir
        lines.extend([
            "# Write fixture batch",
            "plans_dir = pathlib.Path(os.environ.get('ILK_PLANS_DIR', '.'))",
            "fixture_dir = pathlib.Path(os.environ.get('ILK_FIXTURE_DIR', ''))",
            "if not fixture_dir.is_dir():",
            "    fixture_dir = pathlib.Path(__file__).parent / 'fixtures' / 'autoplan'",
            "for f in fixture_dir.iterdir():",
            "    if f.suffix == '.md':",
            "        (plans_dir / f.name).write_text(f.read_text())",
        ])

    if modify_repo:
        # Also modify a tracked file in the repo
        lines.extend([
            "# Modify a tracked file",
            "repo = pathlib.Path(os.environ.get('ILK_REPO_DIR', '.'))",
            "(repo / 'modified.txt').write_text('dirty')",
        ])

    # Write a post-init marker so tests can verify the process ran past init
    # (only when we expect the process to run past init — skip for bad-model tests)
    if write_batch or write_nothing or modify_repo:
        lines.extend([
            "",
            "# Post-init marker",
            "marker = pathlib.Path(os.environ.get('ILK_MARKER', '/tmp/claude-marker.txt'))",
            "marker.write_text('ran')",
        ])

    stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def _make_stub_lint_preflight(tmp_path, *, lint_exit: int = 0,
                               preflight_exit: int = 0) -> tuple[Path, Path]:
    """Create stub lint and preflight scripts that exit with the given codes."""
    lint = Path(tmp_path) / "stub-plan-lint.py"
    lint.write_text(
        f"#!/usr/bin/env python3\nimport sys\nsys.exit({lint_exit})\n",
        encoding="utf-8",
    )
    lint.chmod(lint.stat().st_mode | stat.S_IEXEC)

    preflight = Path(tmp_path) / "stub-plan-preflight.py"
    preflight.write_text(
        f"#!/usr/bin/env python3\nimport sys\nsys.exit({preflight_exit})\n",
        encoding="utf-8",
    )
    preflight.chmod(preflight.stat().st_mode | stat.S_IEXEC)
    return lint, preflight


def _copy_fixture_batch(plans_dir: Path) -> None:
    """Copy the fixture batch to a plans dir."""
    for f in FIXTURE_DIR.iterdir():
        if f.suffix == ".md":
            shutil.copy2(f, plans_dir / f.name)


def _snapshot_data_root(data_root: Path) -> list[tuple[str, str]]:
    """Snapshot all files under data_root as (relative_path, sha256)."""
    entries = []
    for p in sorted(data_root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(data_root))
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            entries.append((rel, h))
    return entries


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


def _read_events(data_root: Path, event: str | None = None) -> list[dict]:
    """Read lifecycle events from the data root, optionally filtered by type."""
    events_file = data_root / "events.jsonl"
    if not events_file.exists():
        return []
    rows = []
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if event is None or row.get("event") == event:
                rows.append(row)
    return rows


# ── xfail marker ─────────────────────────────────────────────────────────────

# xfail markers removed — autoplan.py shipped in step 1


# ── AC-1: idle count and start ───────────────────────────────────────────────


class TestAC1:
    """Two idle ticks give idle 1/3..2/3; the third starts the planner."""

    def test_idle_count_increments(self, tmp_path):
        """tick returns idle <n>/<N> until the threshold."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create a project data dir that resolves to the toolkit
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Stub claude
        stub_claude = _make_stub_claude(tmp_path)
        marker = tmp_path / "claude-marker.txt"

        # Inject the spawner to capture the call
        calls = []
        def fake_popen(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.Popen(
                [sys.executable, str(stub_claude)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "ILK_PLANS_DIR": str(data_root / "plans"),
                     "ILK_REPO_DIR": str(toolkit),
                     "ILK_MARKER": str(marker)},
            )

        # Tick 2 times: should get idle 1/3 .. 2/3
        for i in range(1, 3):
            result = mod.tick(
                data_root=data_root,
                manager_home=str(manager_home),
                popen_fn=fake_popen,
            )
            assert result["decision"] == "idle"
            assert result["idle_cycles"] == i

        # Tick 3: should start
        result = mod.tick(
            data_root=data_root,
            manager_home=str(manager_home),
            popen_fn=fake_popen,
        )
        assert result["decision"] == "started"
        assert len(calls) == 1

        # inflight.json written
        inflight = data_root / "autoplan" / "inflight.json"
        assert inflight.exists()
        data = json.loads(inflight.read_text(encoding="utf-8"))
        assert "pid" in data
        assert data["candidate"] == "sig-abc123"

        # autoplan-started audit row
        rows = _read_audit_rows(data_root, "autoplan-started")
        assert len(rows) == 1


# ── AC-2: busy conditions reset count ────────────────────────────────────────


class TestAC2:
    """Each busy condition resets the idle count to 0."""

    def test_queued_runnable_master_resets(self, tmp_path):
        """A queued master with a runnable sub-plan gives busy."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a queued master with a runnable sub-plan
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        _copy_fixture_batch(plans_dir)
        master = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"
        text = master.read_text(encoding="utf-8")
        text = text.replace("status: queued", "status: active")
        master.write_text(text, encoding="utf-8")

        # Build up idle count to 3
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        # Tick: should get busy and reset
        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "busy"

        # State should be reset
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state.get("idle_cycles", 0) == 0

    def test_running_pid_resets(self, tmp_path):
        """A live running.pid gives busy."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Write a running.pid with our own PID (alive)
        pid_file = project_dir / "runtime" / "launcher" / "running.pid"
        pid_file.write_text(str(os.getpid()) + "\n", encoding="utf-8")

        # Build up idle count
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "busy"

    def test_train_lock_resets(self, tmp_path):
        """A live train.lock gives busy."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Write a train.lock with our own PID
        train_lock = project_dir / "runtime" / "release" / "train.lock"
        train_lock.parent.mkdir(parents=True, exist_ok=True)
        train_lock.write_text(str(os.getpid()) + "\n", encoding="utf-8")

        # Build up idle count
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "busy"

    def test_recent_master_resets(self, tmp_path):
        """A MASTER modified in the last RECENT_AUTHORING_MIN minutes gives busy (now 20 min, draft-only)."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a recently-modified master
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        _copy_fixture_batch(plans_dir)
        master = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"
        # Touch it now (recently modified)
        master.write_text(master.read_text(encoding="utf-8"), encoding="utf-8")

        # Build up idle count
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "busy"


# ── AC-3: threshold gates ────────────────────────────────────────────────────


class TestAC3:
    """At the threshold: kill switch, paused, rate-limited, auto-planned-in-flight."""

    def test_disabled_at_threshold(self, tmp_path):
        """Kill switch file gives disabled, no spawn."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Write kill switch
        (data_root / "autoplan.disabled").touch()

        # Set idle count at threshold
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "disabled"

        # No inflight.json
        assert not (data_root / "autoplan" / "inflight.json").exists()

    def test_paused_at_threshold(self, tmp_path):
        """paused.json gives paused, no spawn."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Write paused.json
        (data_root / "autoplan" / "paused.json").write_text(
            json.dumps({"since": "2026-10-03T12:00:00", "reason": "test"}) + "\n",
            encoding="utf-8",
        )

        # Set idle count at threshold
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "paused"

    def test_rate_limited_at_threshold(self, tmp_path):
        """A refused start less than REFUSED_BACKOFF_MIN ago gives rate-limited."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Write last_start 30 min ago with refused outcome
        state_file = data_root / "autoplan" / "state.json"
        thirty_min_ago = time.time() - 30 * 60
        state_file.write_text(json.dumps({
            "idle_cycles": 3,
            "last_start": thirty_min_ago,
            "last_outcome": "refused",
        }) + "\n", encoding="utf-8")

        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "rate-limited"

    def test_auto_planned_in_flight_at_threshold(self, tmp_path):
        """A queued auto-planned master gives auto-planned-in-flight."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create a queued auto-planned master
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        _copy_fixture_batch(plans_dir)
        master = plans_dir / "MASTER-2026-10-03-fixture-batch-execution-plan.md"
        text = master.read_text(encoding="utf-8")
        text = text.replace("status: queued", "status: queued\nauto_planned: true")
        master.write_text(text, encoding="utf-8")

        # Set idle count at threshold
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "auto-planned-in-flight"

    def test_consecutive_bad_home_writes_one_row(self, tmp_path):
        """Two consecutive bad-home ticks write one refusal row, not two."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Bad home: a worker home
        bad_home = tmp_path / ".claude-worker-xxx"
        bad_home.mkdir(parents=True, exist_ok=True)

        # Set idle count at threshold
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        # First tick: bad-home
        result = mod.tick(data_root=data_root, manager_home=str(bad_home))
        assert result["decision"] == "bad-home"

        # Second tick: bad-home again
        result = mod.tick(data_root=data_root, manager_home=str(bad_home))
        assert result["decision"] == "bad-home"

        # Only one refusal row
        rows = _read_audit_rows(data_root, "autoplan-refused")
        assert len(rows) == 1
        assert rows[0].get("reason") == "bad-home"

    def test_dry_run_no_state_change(self, tmp_path):
        """--dry-run at the threshold changes no byte under the data root."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Set idle count at threshold
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        # Snapshot before
        before = _snapshot_data_root(data_root)

        # Tick with --dry-run
        result = mod.tick(data_root=data_root, manager_home=str(manager_home),
                          dry_run=True)
        assert result["decision"] in ("disabled", "paused", "rate-limited",
                                       "auto-planned-in-flight", "no-candidate",
                                       "started")

        # Snapshot after
        after = _snapshot_data_root(data_root)
        assert before == after, "dry-run changed bytes under data root"


# ── AC-4: kernel escalation ──────────────────────────────────────────────────


class TestAC4:
    """A top-ranked candidate naming a kernel file is escalated, next is started."""

    def test_kernel_candidate_escalated(self, tmp_path):
        """Candidate mentioning ship_audit.py is escalated, next is started."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create candidates: one kernel-touching (top-ranked), one clean
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [
            _make_candidate(
                cid="sig-kernel",
                title="Fix ship_audit.py output",
                gap="ship_audit.py produces wrong output",
                seen_count=5,
            ),
            _make_candidate(
                cid="sig-clean",
                title="Fix collect.py metrics",
                gap="collect.py misses some metrics",
                seen_count=1,
            ),
        ])

        # Stub claude
        stub_claude = _make_stub_claude(tmp_path)
        marker = tmp_path / "claude-marker.txt"

        calls = []
        def fake_popen(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.Popen(
                [sys.executable, str(stub_claude)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "ILK_PLANS_DIR": str(data_root / "plans"),
                     "ILK_REPO_DIR": str(toolkit),
                     "ILK_MARKER": str(marker)},
            )

        # Set idle count at threshold
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        result = mod.tick(
            data_root=data_root,
            manager_home=str(manager_home),
            popen_fn=fake_popen,
        )

        # Should have escalated sig-kernel and started sig-clean
        assert result["decision"] == "started"

        # Verify escalation
        rows = _read_audit_rows(data_root, "escalated")
        assert len(rows) >= 1
        assert rows[0].get("source") == "autoplan"
        assert rows[0].get("reason") == "protected-kernel"

        # Verify candidate was updated
        candidates = _load_candidates(backlog_dir)
        kernel_cand = [c for c in candidates if c["id"] == "sig-kernel"][0]
        assert kernel_cand["relations"]["autoplan_blocked"] is True

        # Verify spawner was called with the clean candidate
        assert len(calls) == 1


# ── AC-5: plan draft-or-queued decision ──────────────────────────────────────


class TestAC5:
    """plan with a clean stub claude: master ends queued."""

    def test_clean_plan_queues(self, tmp_path):
        """Stub writes clean batch, lint/preflight exit 0: master queued."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes the fixture batch
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint and preflight
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        # Run plan
        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
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

        # Master should be queued
        from plan_status import parse_frontmatter
        masters = list(plans_dir.glob("MASTER-*.md"))
        assert len(masters) == 1
        fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
        assert fm.get("status") == "queued"
        assert fm.get("auto_planned") == "true"

        # Candidate should be planned
        candidates = _load_candidates(backlog_dir)
        cand = [c for c in candidates if c["id"] == "sig-abc123"][0]
        assert cand["status"] == "planned"

        # autoplan-queued row
        rows = _read_audit_rows(data_root, "autoplan-queued")
        assert len(rows) == 1

        # Stub saw CLAUDE_CONFIG_DIR = manager home
        assert marker.exists()


# ── AC-6: various failure modes ──────────────────────────────────────────────


class TestAC6:
    """Various failure modes leave no queued master and write the named row."""

    @pytest.mark.parametrize("model,expected_reason", [
        ("mimo-v2.5-pro", "model mimo-v2.5-pro"),
        ("glm-4.6", "model glm-4.6"),
    ])
    def test_bad_model_refused(self, tmp_path, model, expected_reason):
        """Bad init model kills the process and writes autoplan-refused."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude with bad model
        stub_claude = _make_stub_claude(tmp_path, model=model, write_batch=False)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint and preflight (shouldn't be called)
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
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

        # Should be refused
        rows = _read_audit_rows(data_root, "autoplan-refused")
        assert len(rows) >= 1
        assert expected_reason in rows[0].get("reason", "")

        # Marker should NOT exist (killed before post-init)
        assert not marker.exists()

        # No queued master
        masters = list(plans_dir.glob("MASTER-*.md"))
        for m in masters:
            from plan_status import parse_frontmatter
            fm = parse_frontmatter(m.read_text(encoding="utf-8-sig"))
            assert fm.get("status") != "queued"

    def test_lint_failure_drafts(self, tmp_path):
        """Lint exit 1: master stays draft, notify once, autoplan_attempts: 1."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes the batch
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint that fails, preflight that passes
        lint, preflight = _make_stub_lint_preflight(tmp_path, lint_exit=1)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
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

        # Master should be draft
        from plan_status import parse_frontmatter
        masters = list(plans_dir.glob("MASTER-*.md"))
        assert len(masters) == 1
        fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
        assert fm.get("status") == "draft"

        # autoplan-drafted row
        rows = _read_audit_rows(data_root, "autoplan-drafted")
        assert len(rows) >= 1

        # Candidate autoplan_attempts: 1
        candidates = _load_candidates(backlog_dir)
        cand = [c for c in candidates if c["id"] == "sig-abc123"][0]
        assert cand["relations"]["autoplan_attempts"] == 1

    def test_kernel_scope_path_drafts(self, tmp_path):
        """Fixture batch with kernel scope_path: master stays draft."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Stage a batch with a kernel scope_path OUTSIDE the plans dir, so
        # the stub's copy is what makes it new (a pre-seeded plans dir only
        # passed through the removed fallback to pre-existing masters).
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        staged = Path(tmp_path) / "staged-batch"
        staged.mkdir()
        _copy_fixture_batch(staged)
        # Modify the feature sub-plan to have a kernel scope_path
        feature = staged / "2026-10-03-fixture-feature.md"
        text = feature.read_text(encoding="utf-8")
        text = text.replace(
            '- "skills/ilk-feedback/scripts/helper.py"',
            '- "skills/ilk-loop/scripts/"',
        )
        feature.write_text(text, encoding="utf-8")

        # Stub claude that writes the modified batch
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=False)
        # Make the stub copy our modified batch
        marker = tmp_path / "claude-marker.txt"
        stub_script = Path(tmp_path) / "stub-claude-copy.py"
        stub_script.write_text(
            f"#!/usr/bin/env python3\n"
            f"import shutil, pathlib, os\n"
            f"print('{{\"type\":\"system\",\"subtype\":\"init\",\"model\":\"claude-opus-test\"}}')\n"
            f"src = pathlib.Path('{staged}')\n"
            f"dst = pathlib.Path(os.environ['ILK_PLANS_DIR'])\n"
            f"for f in src.glob('*.md'):\n"
            f"    shutil.copy2(f, dst / f.name)\n"
            f"pathlib.Path(os.environ['ILK_MARKER']).write_text('ran')\n",
            encoding="utf-8",
        )
        stub_script.chmod(stub_script.stat().st_mode | stat.S_IEXEC)

        # Stub lint and preflight that pass
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
            data_root=data_root,
            toolkit_repo=str(toolkit),
            manager_home=str(manager_home),
            claude_cmd=[sys.executable, str(stub_script)],
            lint_cmd=[sys.executable, str(lint)],
            preflight_cmd=[sys.executable, str(preflight)],
            env_overrides={
                "ILK_PLANS_DIR": str(plans_dir),
                "ILK_REPO_DIR": str(toolkit),
                "ILK_MARKER": str(marker),
                "ILK_FIXTURE_DIR": str(FIXTURE_DIR),
            },
        )

        # Master should be draft
        from plan_status import parse_frontmatter
        masters = list(plans_dir.glob("MASTER-*.md"))
        assert len(masters) == 1
        fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
        assert fm.get("status") == "draft"

    def test_clone_modified_escalated(self, tmp_path):
        """Stub that modifies a tracked file: escalated, severity critical."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Initialize git so clone check can run
        subprocess.run(["git", "init"], cwd=str(toolkit), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(toolkit), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(toolkit), capture_output=True)
        subprocess.run(["git", "add", "."], cwd=str(toolkit), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init", "--allow-empty"],
                       cwd=str(toolkit), capture_output=True)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes batch AND modifies repo
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True, modify_repo=True)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint and preflight
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
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

        # Should be escalated with severity critical
        rows = _read_audit_rows(data_root, "escalated")
        assert len(rows) >= 1
        assert rows[0].get("severity") == "critical"
        assert rows[0].get("reason") == "clone-modified"


# ── AC-7: consecutive drafts pause ───────────────────────────────────────────


class TestAC7:
    """Two consecutive drafted runs write paused.json."""

    def test_consecutive_drafts_pause(self, tmp_path):
        """Two drafts in a row: paused.json written, next tick returns paused."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes the batch
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint that fails
        lint, preflight = _make_stub_lint_preflight(tmp_path, lint_exit=1)

        # Run plan twice (consecutive drafts)
        # Clean plans dir between runs so each sees the batch as "new"
        for i in range(2):
            for f in plans_dir.glob("*.md"):
                f.unlink()
            result = mod.plan(
                candidate_id="sig-abc123",
                project_key="test-project",
                run_id=f"run-{i:03d}",
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

        # paused.json should exist
        paused_file = data_root / "autoplan" / "paused.json"
        assert paused_file.exists()
        paused_data = json.loads(paused_file.read_text(encoding="utf-8"))
        assert "since" in paused_data
        assert "run_ids" in paused_data

        # Next tick should return paused
        # First reset idle count
        state_file = data_root / "autoplan" / "state.json"
        state_file.write_text(json.dumps({"idle_cycles": 3}) + "\n", encoding="utf-8")

        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
        assert result["decision"] == "paused"


# ── AC-8: stale reproduce control ────────────────────────────────────────────


class TestAC8:
    """Stub that writes nothing and ends AUTOPLAN: stale gives refused."""

    def test_stale_reproduce_refused(self, tmp_path):
        """AUTOPLAN: stale line: refused, no master, candidate still open."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes nothing and says stale
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_nothing=True,
                                         stale_reason="fixed in abc123")
        marker = tmp_path / "claude-marker.txt"

        # Stub lint and preflight
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
            data_root=data_root,
            toolkit_repo=str(toolkit),
            manager_home=str(manager_home),
            claude_cmd=[sys.executable, str(stub_claude)],
            lint_cmd=[sys.executable, str(lint)],
            preflight_cmd=[sys.executable, str(preflight)],
            env_overrides={
                "ILK_PLANS_DIR": str(plans_dir),
                "ILK_REPO_DIR": str(toolkit),
                "ILK_MARKER": str(marker)},
        )

        # Should be refused with stale reason
        rows = _read_audit_rows(data_root, "autoplan-refused")
        assert len(rows) >= 1
        assert "stale fixed in abc123" in rows[0].get("reason", "")

        # No new master
        masters = list(plans_dir.glob("MASTER-*.md"))
        assert len(masters) == 0

        # Candidate still open with autoplan_attempts: 1
        candidates = _load_candidates(backlog_dir)
        cand = [c for c in candidates if c["id"] == "sig-abc123"][0]
        assert cand["status"] == "open"
        assert cand["relations"]["autoplan_attempts"] == 1

    def test_stale_with_existing_masters_touches_none(self, tmp_path):
        """A stale plan never re-drafts or re-marks a pre-existing master.

        2026-10-07 04:21: run autoplan-1791317991 wrote no master and the
        fallback treated all 122 existing masters as new, forcing each to
        draft and marking the first as auto-planned.
        """
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)

        project_dir = data_root / "projects" / "test-project"
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        shipped = plans_dir / "MASTER-2026-01-01-old-execution-plan.md"
        shipped.write_text("---\nstatus: shipped\n---\n\n# old\n", encoding="utf-8")
        paused = plans_dir / "MASTER-2026-01-02-held-execution-plan.md"
        paused.write_text("---\nstatus: paused\n---\n\n# held\n", encoding="utf-8")
        before = {p.name: p.read_bytes() for p in (shipped, paused)}

        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_nothing=True,
                                         stale_reason="fixed in abc123")
        marker = tmp_path / "claude-marker.txt"
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
            data_root=data_root,
            toolkit_repo=str(toolkit),
            manager_home=str(manager_home),
            claude_cmd=[sys.executable, str(stub_claude)],
            lint_cmd=[sys.executable, str(lint)],
            preflight_cmd=[sys.executable, str(preflight)],
            env_overrides={
                "ILK_PLANS_DIR": str(plans_dir),
                "ILK_REPO_DIR": str(toolkit),
                "ILK_MARKER": str(marker)},
        )

        assert result["decision"] == "refused", result
        assert result["reason"] == "stale fixed in abc123", result
        for p in (shipped, paused):
            assert p.read_bytes() == before[p.name], p.name
        assert not _read_audit_rows(data_root, "autoplan-drafted")


# ── AC-9: draft-only dry period ───────────────────────────────────────────


def _build_fake_toolkit_with_config(tmp_path, data_root, *, autoplan_config: dict) -> Path:
    """Create a fake toolkit repo with a custom .ilk-launch.json autoplan config."""
    toolkit = Path(tmp_path) / "toolkit"
    toolkit.mkdir(parents=True, exist_ok=True)
    (toolkit / "commands").mkdir(exist_ok=True)
    (toolkit / "commands" / "ilk-plan.md").write_text("# ilk-plan\n", encoding="utf-8")
    (toolkit / ".ilk-launch.json").write_text(
        json.dumps({"autoplan": autoplan_config}) + "\n",
        encoding="utf-8",
    )
    plans_dir = data_root / "plans"
    plans_dir.mkdir(exist_ok=True)
    return toolkit


class TestAC9:
    """Draft-only dry period: planner runs fully but master stays draft."""

    def test_draft_only_true_keeps_master_draft(self, tmp_path):
        """draft_only: true → full pipeline runs, master stays draft, event is dry-period-drafted."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit_with_config(
            tmp_path, data_root,
            autoplan_config={"enabled": True, "draft_only": True},
        )
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes the fixture batch
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint and preflight that pass
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
            data_root=data_root,
            toolkit_repo=str(toolkit),
            manager_home=str(manager_home),
            claude_cmd=[sys.executable, str(stub_claude)],
            lint_cmd=[sys.executable, str(lint)],
            preflight_cmd=[sys.executable, str(preflight)],
            draft_only=True,
            env_overrides={
                "ILK_PLANS_DIR": str(plans_dir),
                "ILK_REPO_DIR": str(toolkit),
                "ILK_MARKER": str(marker),
                "ILK_FIXTURE_DIR": str(FIXTURE_DIR),
            },
        )

        # Master should be draft (not queued)
        from plan_status import parse_frontmatter
        masters = list(plans_dir.glob("MASTER-*.md"))
        assert len(masters) == 1
        fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
        assert fm.get("status") == "draft", "draft_only master must stay draft"
        assert fm.get("auto_planned") == "true"

        # Full pipeline ran: lint and preflight were called
        # (lint/preflight stubs exit 0, so no problems)
        # The dry-period-drafted event should be written
        rows = _read_audit_rows(data_root, "dry-period-drafted")
        assert len(rows) == 1, "dry-period-drafted audit row must be written"
        assert rows[0].get("candidate") == "sig-abc123"
        assert rows[0].get("master") == masters[0].name

        # Candidate should be planned (not refused)
        candidates = _load_candidates(backlog_dir)
        cand = [c for c in candidates if c["id"] == "sig-abc123"][0]
        assert cand["status"] == "planned"

        # Consecutive drafts counter must NOT increment
        state = json.loads((data_root / "autoplan" / "state.json").read_text(encoding="utf-8"))
        assert state.get("consecutive_drafts", 0) == 0, (
            "draft_only must not count as a consecutive draft"
        )

        # No paused.json
        assert not (data_root / "autoplan" / "paused.json").exists()

    def test_draft_only_false_promotes_to_queued(self, tmp_path):
        """draft_only: false → normal behavior, master becomes queued."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit_with_config(
            tmp_path, data_root,
            autoplan_config={"enabled": True, "draft_only": False},
        )
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes the fixture batch
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint and preflight that pass
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
            data_root=data_root,
            toolkit_repo=str(toolkit),
            manager_home=str(manager_home),
            claude_cmd=[sys.executable, str(stub_claude)],
            lint_cmd=[sys.executable, str(lint)],
            preflight_cmd=[sys.executable, str(preflight)],
            draft_only=False,
            env_overrides={
                "ILK_PLANS_DIR": str(plans_dir),
                "ILK_REPO_DIR": str(toolkit),
                "ILK_MARKER": str(marker),
                "ILK_FIXTURE_DIR": str(FIXTURE_DIR),
            },
        )

        # Master should be queued (normal behavior)
        from plan_status import parse_frontmatter
        masters = list(plans_dir.glob("MASTER-*.md"))
        assert len(masters) == 1
        fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
        assert fm.get("status") == "queued", "draft_only: false must promote to queued"

        # autoplan-queued row (not dry-period-drafted)
        rows = _read_audit_rows(data_root, "autoplan-queued")
        assert len(rows) == 1
        assert _read_audit_rows(data_root, "dry-period-drafted") == [], (
            "dry-period-drafted must not fire when draft_only is false"
        )

    def test_draft_only_absent_promotes_to_queued(self, tmp_path):
        """No draft_only key → default false, master becomes queued."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit_with_config(
            tmp_path, data_root,
            autoplan_config={"enabled": True},  # no draft_only key
        )
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes the fixture batch
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint and preflight that pass
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
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

        # Master should be queued (default behavior)
        from plan_status import parse_frontmatter
        masters = list(plans_dir.glob("MASTER-*.md"))
        assert len(masters) == 1
        fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
        assert fm.get("status") == "queued", "absent draft_only must default to false"

    def test_draft_only_malformed_refuses(self, tmp_path):
        """draft_only: \"yes\" (string, not bool) → refuse, no master queued."""
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit_with_config(
            tmp_path, data_root,
            autoplan_config={"enabled": True, "draft_only": "yes"},  # malformed
        )
        manager_home = _build_fake_manager_home(tmp_path)

        # Create a project data dir
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Create plans dir
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)

        # Stub claude that writes the fixture batch
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True)
        marker = tmp_path / "claude-marker.txt"

        # Stub lint and preflight that pass
        lint, preflight = _make_stub_lint_preflight(tmp_path)

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-001",
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

        # Should be refused with config error
        rows = _read_audit_rows(data_root, "autoplan-refused")
        assert len(rows) >= 1
        assert "draft_only" in rows[0].get("reason", "").lower() or \
               "config" in rows[0].get("reason", "").lower(), (
            "malformed draft_only must refuse with a config error"
        )

        # No queued master
        masters = list(plans_dir.glob("MASTER-*.md"))
        for m in masters:
            from plan_status import parse_frontmatter
            fm = parse_frontmatter(m.read_text(encoding="utf-8-sig"))
            assert fm.get("status") != "queued", "malformed config must not queue"


# ── AC-1 / AC-2: CLI tick and gated spawn (step 0 pins) ─────────────────


class TestCliTickAndGatedSpawn:
    """Pin the two defects that block RSI: CLI tick args and real-mode spawn.

    Step 0 of the-scheduler-tick-reaches-the-gated-planner.
    Both tests are xfail(strict=True) until step 1 ships the fix.
    """

    def test_cli_tick_runs_and_prints_a_decision(self, tmp_path):
        """AC-1: ``python3 autoplan.py tick --dry-run --json`` exits 0 and prints JSON.

        Today it crashes with ``TypeError: tick() missing 1 required keyword-only
        argument: 'data_root'`` because the CLI ``tick`` branch calls
        ``tick(dry_run=args.dry_run)`` without passing ``data_root`` or
        ``manager_home``.
        """
        mod = _load_module()
        autoplan_py = Path(mod.__file__).resolve()

        # Build a minimal data root so the tick can compute a decision.
        data_root = _build_fake_data_root(tmp_path)
        manager_home = _build_fake_manager_home(tmp_path)

        # A project with a toolkit so the tick doesn't refuse early.
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Run the CLI tick as a subprocess, isolating the data home.
        env = {
            **os.environ,
            "ILK_DATA_HOME": str(data_root),
            "HOME": str(tmp_path),
            "CLAUDE_MANAGER_HOME": str(manager_home),
        }
        # Keep PYTHONUSERBASE so the child can import the same packages.

        result = subprocess.run(
            [sys.executable, str(autoplan_py), "tick", "--dry-run", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        # Must exit 0.
        assert result.returncode == 0, (
            f"CLI tick exited {result.returncode}\n"
            f"stderr: {result.stderr[-500:]}"
        )

        # stderr must have no traceback.
        assert "Traceback" not in result.stderr, (
            f"CLI tick produced a traceback on stderr:\n{result.stderr[-500:]}"
        )

        # stdout must be valid JSON with a "decision" key.
        parsed = json.loads(result.stdout.strip())
        assert "decision" in parsed, (
            f"CLI tick JSON missing 'decision' key: {parsed}"
        )

    def test_real_mode_tick_spawns_the_gated_plan_subcommand(self, tmp_path):
        """AC-2 + AC-3: real-mode tick spawns ``autoplan.py plan …``, not ``claude -p``.

        With ``popen_fn=None`` and ``dry_run=False``, the start branch must
        call ``subprocess.Popen`` exactly once with an argv that contains
        ``autoplan.py plan --candidate --project-key --run-id``, writes
        stdout/stderr to the run log (not PIPE), and uses
        ``start_new_session=True``.
        """
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        manager_home = _build_fake_manager_home(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)

        # Create a project so the tick can resolve the toolkit repo.
        project_dir = data_root / "projects" / "test-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n",
            encoding="utf-8",
        )

        # Create a candidate so the tick selects something.
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])

        # Record subprocess.Popen calls (monkeypatch on the module).
        popen_calls: list[tuple[tuple, dict]] = []

        class FakeProc:
            def __init__(self, pid: int = 99999):
                self.pid = pid

        original_popen = subprocess.Popen

        def recording_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            # Return a fake process with a pid so inflight.json is written.
            return FakeProc()

        # Run enough idle ticks to reach the start branch (threshold is 3).
        # dry_run=False is required so the idle_cycles counter persists.
        # The first 2 ticks use popen_fn (test-mode) since they never reach
        # the start branch. The 3rd tick uses popen_fn=None (real mode).
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(subprocess, "Popen", recording_popen)

            # Drive 2 idle ticks (dry_run=False to persist counter).
            for i in range(2):
                result = mod.tick(
                    data_root=data_root,
                    manager_home=str(manager_home),
                    popen_fn=recording_popen,  # test-mode stub for idle ticks
                    dry_run=False,
                )
                assert result["decision"] == "idle", (
                    f"Tick {i+1} expected 'idle', got {result['decision']}"
                )

            # The 3rd tick in real mode (popen_fn=None, dry_run=False).
            # This is the one that should spawn autoplan.py plan.
            popen_calls.clear()  # reset — only the real spawn counts
            result = mod.tick(
                data_root=data_root,
                manager_home=str(manager_home),
                popen_fn=None,  # real mode
                dry_run=False,
            )

        assert result["decision"] == "started", (
            f"Expected 'started', got {result['decision']}"
        )

        # Exactly one Popen call (the real-mode spawn).
        assert len(popen_calls) == 1, (
            f"Expected 1 Popen call, got {len(popen_calls)}"
        )

        args, kwargs = popen_calls[0]

        # argv checks
        argv = args[0] if args else kwargs.get("args", [])
        argv_str = " ".join(str(a) for a in argv)

        # Must contain autoplan.py (or its path) followed by "plan".
        assert "plan" in argv, (
            f"argv must contain 'plan', got: {argv}"
        )

        # Must have --candidate, --project-key, --run-id.
        assert "--candidate" in argv, f"argv missing --candidate: {argv}"
        assert "--project-key" in argv, f"argv missing --project-key: {argv}"
        assert "--run-id" in argv, f"argv missing --run-id: {argv}"

        # Must NOT contain "-p" (the raw claude prompt flag).
        assert "-p" not in argv, f"argv must not contain '-p': {argv}"

        # stdout must NOT be subprocess.PIPE (should go to log file).
        assert kwargs.get("stdout") is not subprocess.PIPE, (
            f"stdout should not be PIPE (goes to log file), got: {kwargs.get('stdout')}"
        )

        # stdin must be subprocess.DEVNULL.
        assert kwargs.get("stdin") is subprocess.DEVNULL, (
            f"stdin should be DEVNULL, got: {kwargs.get('stdin')}"
        )

        # start_new_session must be True.
        assert kwargs.get("start_new_session") is True, (
            f"start_new_session should be True, got: {kwargs.get('start_new_session')}"
        )

        # env must contain CLAUDE_MANAGER_HOME.
        spawn_env = kwargs.get("env", {})
        assert spawn_env.get("CLAUDE_MANAGER_HOME") == str(manager_home), (
            f"env CLAUDE_MANAGER_HOME mismatch: {spawn_env.get('CLAUDE_MANAGER_HOME')}"
        )

        # cwd must be the toolkit repo.
        assert kwargs.get("cwd") == str(toolkit), (
            f"cwd should be toolkit repo, got: {kwargs.get('cwd')}"
        )

        # AC-3: inflight.json must name the spawned pid.
        inflight_file = data_root / "autoplan" / "inflight.json"
        assert inflight_file.exists(), "inflight.json not written"
        inflight = json.loads(inflight_file.read_text(encoding="utf-8"))
        assert inflight["pid"] == 99999, (
            f"inflight pid mismatch: {inflight['pid']}"
        )

        # AC-3: the run log file must exist (stdout/stderr redirected there).
        run_id = result.get("run_id", "")
        log_file = data_root / "autoplan" / "runs" / f"{run_id}.log"
        assert log_file.exists(), (
            f"Run log {log_file} does not exist"
        )


class TestTheRealLintAcceptsThePlannersArgv:
    """plan() calls the REAL plan_lint.py with an argv it can parse.

    Every other plan() test stubs lint_cmd, so the argv was never checked:
    plan() passed ``--subplan <path>``, which plan_lint.py does not define,
    and argparse exited 2.  Every auto-planned draft therefore read
    ``lint-exit-2`` whatever it contained (manual run 2026-10-06
    manual-20261006-192151; the same batch linted clean by hand).
    plan_lint returns 0 or 1; only a usage error returns 2.
    """

    def test_real_plan_lint_parses_the_argv(self, tmp_path):
        mod = _load_module()
        data_root = _build_fake_data_root(tmp_path)
        toolkit = _build_fake_toolkit(tmp_path, data_root)
        manager_home = _build_fake_manager_home(tmp_path)
        project_dir = data_root / "projects" / "test-project"
        (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
        (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
            json.dumps({"project_path": str(toolkit)}) + "\n", encoding="utf-8",
        )
        backlog_dir = data_root / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        _save_candidates(backlog_dir, [_make_candidate()])
        plans_dir = data_root / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        stub_claude = _make_stub_claude(tmp_path, model="claude-opus-test",
                                         write_batch=True)
        _lint_stub, preflight = _make_stub_lint_preflight(tmp_path)
        real_lint = Path(_LOOP_SCRIPTS) / "plan_lint.py"

        result = mod.plan(
            candidate_id="sig-abc123",
            project_key="test-project",
            run_id="run-real-lint",
            data_root=data_root,
            toolkit_repo=str(toolkit),
            manager_home=str(manager_home),
            claude_cmd=[sys.executable, str(stub_claude)],
            lint_cmd=[sys.executable, str(real_lint)],
            preflight_cmd=[sys.executable, str(preflight)],
            env_overrides={
                "ILK_PLANS_DIR": str(plans_dir),
                "ILK_REPO_DIR": str(toolkit),
                "ILK_MARKER": str(tmp_path / "claude-marker.txt"),
                "ILK_FIXTURE_DIR": str(FIXTURE_DIR),
            },
        )

        assert "lint-exit-2" not in result.get("problems", []), result
