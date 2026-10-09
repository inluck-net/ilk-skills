"""A release train refused for a missing permit asks the owner once per run.

Sub-plan: a-refused-train-asks-the-owner, step 0 (red-first pins).

Each test builds a throwaway ILK_DATA_HOME under ``tmp_path``.  All external
commands are imported or invoked via subprocess; no real daemon, no real
``~/.ilk-data``, and no real scheduler are touched.

The seven acceptance criteria:
  AC-1  record × 3 with the same run_id returns False, False, True.  A 4th
        call returns False.  The file has ``passes == 4``.
  AC-2  a new run_id after a request starts over: ``passes == 1``, no
        ``requested_at``.
  AC-3  ``clear`` removes the file, and ``pending`` then returns None.
  AC-4  (CLI) the 3rd ``record`` prints exactly ``request``; the others print
        nothing.  All exit 0.
  AC-5  (scheduler wiring, structural) ``scheduler.sh`` contains a
        ``permit_request.py" record`` call within 6 lines after each of the
        three ``skip-permits`` lines, and a ``permit_request.py" clear`` call
        within 6 lines after the ``release-train-started`` line.
  AC-6  (status_all) a project data dir with a requested record yields an
        entry with ``permit_request == <reason>``, and the key is absent
        without one.
  AC-7  (xbar) ``render_xbar`` on an entry with
        ``permit_request: "consumed permit for host chad-mbp"`` outputs a line
        starting ``--train waiting for a permit: consumed permit for host
        chad-mbp``.  Without the field, no such line appears.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WATCHDOG_SCRIPTS = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"
LOOP_SCRIPTS = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
XBAR_TOOLS = REPO_ROOT / "tools" / "xbar"

sys.path.insert(0, str(WATCHDOG_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))
sys.path.insert(0, str(XBAR_TOOLS))


# ── xfail pin ───────────────────────────────────────────────────────────────

_PIN = pytest.mark.xfail(
    strict=True,
    raises=(AssertionError, ImportError, AttributeError, TypeError),
    reason="not built yet",
)


# ── Isolation fixture ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME so no test touches the real data root."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("ILK_DATA_HOME", str(fake_home / ".ilk-data"))
    monkeypatch.setenv("ILK_DATA_DIR", str(fake_home / ".ilk-data"))


# ── AC-1: record × 3 → True on the 3rd, False on the 4th, passes == 4 ─────


@_PIN
def test_record_third_consecutive_refusal_triggers_request(tmp_path: Path) -> None:
    """AC-1: record × 3 with same run_id → False, False, True. 4th → False."""
    from permit_request import record

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    r1 = record(data_dir, "run-001", "missing permit for host chad-mbp")
    r2 = record(data_dir, "run-001", "missing permit for host chad-mbp")
    r3 = record(data_dir, "run-001", "missing permit for host chad-mbp")
    r4 = record(data_dir, "run-001", "missing permit for host chad-mbp")

    assert r1 is False
    assert r2 is False
    assert r3 is True
    assert r4 is False

    state_file = data_dir / "runtime" / "permit-request.json"
    data = json.loads(state_file.read_text())
    assert data["passes"] == 4
    assert data["run_id"] == "run-001"
    assert data["requested_at"] is not None


# ── AC-2: new run_id resets ─────────────────────────────────────────────────


@_PIN
def test_new_run_id_resets_state(tmp_path: Path) -> None:
    """AC-2: a new run_id after a request starts over."""
    from permit_request import record

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Trigger a request
    record(data_dir, "run-001", "reason-a")
    record(data_dir, "run-001", "reason-a")
    record(data_dir, "run-001", "reason-a")

    # New run_id
    result = record(data_dir, "run-002", "reason-b")

    assert result is False
    state_file = data_dir / "runtime" / "permit-request.json"
    data = json.loads(state_file.read_text())
    assert data["passes"] == 1
    assert data["run_id"] == "run-002"
    assert data.get("requested_at") is None


# ── AC-3: clear removes the file; pending then returns None ─────────────────


@_PIN
def test_clear_removes_file_and_pending_returns_none(tmp_path: Path) -> None:
    """AC-3: clear removes the file, pending then returns None."""
    from permit_request import clear, pending, record

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    record(data_dir, "run-001", "reason")
    record(data_dir, "run-001", "reason")
    record(data_dir, "run-001", "reason")

    assert pending(data_dir) is not None

    clear(data_dir)

    state_file = data_dir / "runtime" / "permit-request.json"
    assert not state_file.exists()
    assert pending(data_dir) is None


# ── AC-4: CLI output ────────────────────────────────────────────────────────


@_PIN
def test_cli_third_record_prints_request(tmp_path: Path) -> None:
    """AC-4: the 3rd record prints 'request'; others print nothing. All exit 0."""
    script = WATCHDOG_SCRIPTS / "permit_request.py"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    results = []
    for _ in range(4):
        r = subprocess.run(
            [sys.executable, str(script), "record",
             "--data-dir", str(data_dir),
             "--run-id", "run-001",
             "--reason", "missing permit for host chad-mbp"],
            capture_output=True, text=True, timeout=30,
        )
        results.append((r.returncode, r.stdout.strip()))

    assert results[0] == (0, "")
    assert results[1] == (0, "")
    assert results[2] == (0, "request")
    assert results[3] == (0, "")


# ── AC-5: structural wiring in scheduler.sh ─────────────────────────────────


@_PIN
def test_scheduler_sh_has_permit_request_calls() -> None:
    """AC-5: scheduler.sh has permit_request.py record after each skip-permits
    and clear after release-train-started."""
    scheduler = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
    lines = scheduler.read_text().splitlines()

    # Find every 'skip-permits' line
    skip_indices = [i for i, l in enumerate(lines) if "skip-permits" in l]
    assert len(skip_indices) >= 3, (
        f"expected >=3 skip-permits lines, found {len(skip_indices)}"
    )

    for idx in skip_indices:
        # Within 6 lines after, there should be a permit_request.py" record call
        window = "\n".join(lines[idx : idx + 7])
        assert 'permit_request.py" record' in window, (
            f"no permit_request.py record call within 6 lines of skip-permits at line {idx + 1}"
        )

    # Find the release-train-started line
    started_indices = [i for i, l in enumerate(lines) if "release-train-started" in l]
    assert len(started_indices) >= 1, "no release-train-started line found"

    for idx in started_indices:
        window = "\n".join(lines[idx : idx + 7])
        assert 'permit_request.py" clear' in window, (
            f"no permit_request.py clear call within 6 lines of release-train-started at line {idx + 1}"
        )


# ── AC-6: status_all field ──────────────────────────────────────────────────


@_PIN
def test_status_all_entry_has_permit_request_when_requested(tmp_path: Path) -> None:
    """AC-6: a project with a requested record yields entry.permit_request == reason."""
    from permit_request import record

    # Build a minimal project data dir under ILK_DATA_HOME (set by fixture)
    ilk_data = tmp_path / "home" / ".ilk-data"
    data_dir = ilk_data / "projects" / "test-proj"
    launcher_dir = data_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)

    # Write a sentinel so status_all picks up the project
    sentinel = {
        "state": "shipped",
        "pid": 99999999,
        "run_id": "test-run-001",
        "started_at": "2026-10-10T10:00:00+0800",
        "ended_at": "2026-10-10T10:30:00+0800",
        "iterations": 1,
        "project_path": str(tmp_path / "proj"),
        "cli": "claude",
    }
    (launcher_dir / "last-exit.json").write_text(json.dumps(sentinel))

    # Write a plans dir with a minimal master
    plans_dir = data_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    master = plans_dir / "MASTER-2026-10-10a-test.md"
    master.write_text(
        "---\nmaster_plan: 2026-10-10a-test\nbatch_date: 2026-10-10\nstatus: shipped\n---\n\n# Test\n"
    )

    # Record a permit request
    record(data_dir, "test-run-001", "consumed permit for host chad-mbp")

    # Run status_all — env inherits ILK_DATA_HOME from fixture
    status_all = LOOP_SCRIPTS / "status_all.py"
    r = subprocess.run(
        [sys.executable, "-I", str(status_all), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    entries = json.loads(r.stdout)
    entry = next((e for e in entries if e.get("project_key") == "test-proj"), None)
    assert entry is not None, f"test-proj not found in {entries}"
    assert entry.get("permit_request") == "consumed permit for host chad-mbp"


# ── AC-7: xbar line ─────────────────────────────────────────────────────────


@_PIN
def test_xbar_renders_permit_request_line() -> None:
    """AC-7: render_xbar on an entry with permit_request outputs the line."""
    from render_xbar import render_xbar

    entry = {
        "project_key": "proj",
        "path": "/fake/proj",
        "repo_path": None,
        "orphaned": False,
        "active_master": "MASTER-2026-10-10a.md",
        "next_subplan": "some-slug",
        "step": "0/2",
        "sentinel": {"pid": 4242, "state": "running", "alive": True},
        "last_class": None,
        "model": "glm-5.3",
        "runnable": False,
        "parked": False,
        "manually_runnable": False,
        "blocked": False,
        "blocked_reason": None,
        "permit_request": "consumed permit for host chad-mbp",
    }

    out = render_xbar([entry])
    assert "--train waiting for a permit: consumed permit for host chad-mbp" in out


