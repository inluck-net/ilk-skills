#!/usr/bin/env python3
"""
Test preserve_active_run.py with synthetic fixtures.

Creates a temp project directory, fake last-launch.json, JSONL, and iter
logs, runs preservation twice, and asserts the archive is complete and
stable (idempotent).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Resolve sibling modules
sys.path.insert(0, str(Path(__file__).resolve().parent))

RUN_ID = "20260528-120000"
PROJECT_NAME = "test-project"


def _setup_fixture(base: Path) -> tuple[Path, str]:
    """Create a fake project with .git and fake log artifacts.

    Returns (project_path, project_key).
    """
    from ilk_paths import project_key as _project_key

    project = base / "proj"
    project.mkdir()

    # .git so ilk_paths finds a project root
    (project / ".git").mkdir()

    key = _project_key(project)

    # Fake last-launch.json
    launcher_dir = base / "ilk-data" / "projects" / key / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True)
    (launcher_dir / "last-launch.json").write_text(json.dumps({
        "project_path": str(project),
        "project_name": PROJECT_NAME,
        "run_id": RUN_ID,
        "max_iterations": 10,
        "iteration_timeout_min": 30,
        "started_at": "2026-05-28T12:00:00+0800",
    }))

    # Fake last-exit.json sentinel.  Lives in runtime/launcher/ — the path every
    # reader uses, and where preserve_active_run looks (external_launcher_dir).
    # Moved there by `the-sentinel-lands-where-readers-look` (736d6d5); this
    # fixture wrote the old runtime/ path, so the sentinel was never archived.
    runtime_dir = base / "ilk-data" / "projects" / key / "runtime" / "launcher"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "last-exit.json").write_text(json.dumps({
        "state": "running",
        "pid": 12345,
        "run_id": RUN_ID,
    }))

    # Fake per-iteration logs
    runs_dir = base / "ilk-data" / "projects" / key / "logs" / "runs" / RUN_ID
    runs_dir.mkdir(parents=True)
    (runs_dir / "iter-01.log").write_text("=== Iteration 1 ===\nAll good.\n")
    (runs_dir / "iter-01.log.jsonl").write_text(
        json.dumps({"type": "assistant", "message": "step 1 done"}) + "\n"
    )
    (runs_dir / "iter-02.log").write_text("=== Iteration 2 ===\nShipped.\n")
    (runs_dir / "iter-02.log.jsonl").write_text(
        json.dumps({"type": "assistant", "message": "step 2 done"}) + "\n"
    )

    # Fake JSONL summary (contains records for this and another run)
    logs_dir = base / "ilk-data" / "projects" / key / "logs"
    jsonl_records = [
        {"run_id": RUN_ID, "iteration": 1, "project": str(project), "exit_code": 0},
        {"run_id": RUN_ID, "iteration": 2, "project": str(project), "exit_code": 0},
        {"run_id": "20260527-999999", "iteration": 1, "project": str(project), "exit_code": 0},
    ]
    (logs_dir / ".ilk-loop.log").write_text(
        "\n".join(json.dumps(r) for r in jsonl_records) + "\n"
    )

    return project, key


def _override_data_dir(base: Path) -> None:
    """Point ilk_paths at our temp data dir."""
    os.environ["ILK_DATA_HOME"] = str(base / "ilk-data")


def test_preserve_complete_and_idempotent() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project, key = _setup_fixture(base)
        _override_data_dir(base)

        from preserve_active_run import preserve

        # First run
        archive = preserve(project, RUN_ID)
        assert archive.is_dir(), f"archive dir not created: {archive}"

        # Verify iter logs copied
        assert (archive / "iter-01.log").exists(), "iter-01.log missing"
        assert (archive / "iter-02.log").exists(), "iter-02.log missing"
        assert (archive / "iter-01.log.jsonl").exists(), "iter-01.log.jsonl missing"

        # Verify JSONL filtered (only this run_id)
        jsonl_content = (archive / ".ilk-loop.log").read_text()
        lines = [l for l in jsonl_content.strip().splitlines() if l]
        assert len(lines) == 2, f"expected 2 JSONL records, got {len(lines)}"
        for line in lines:
            rec = json.loads(line)
            assert rec["run_id"] == RUN_ID, f"wrong run_id in JSONL: {rec}"

        # Verify sentinel and launch metadata
        assert (archive / "last-exit.json").exists(), "sentinel missing"
        assert (archive / "last-launch.json").exists(), "launch metadata missing"

        # Record mtimes for idempotency check
        mtimes_before = {
            f.name: f.stat().st_mtime
            for f in sorted(archive.iterdir())
        }

        # Second run (idempotency)
        import time
        time.sleep(0.1)  # ensure mtime would differ if rewritten
        archive2 = preserve(project, RUN_ID)
        assert archive2 == archive, "archive path changed on second run"

        mtimes_after = {
            f.name: f.stat().st_mtime
            for f in sorted(archive.iterdir())
        }

        # Files should be overwritten (content same, mtime may differ)
        # The key assertion: same files exist with same content
        assert (archive / "iter-01.log").read_text() == "=== Iteration 1 ===\nAll good.\n"
        jsonl_after = (archive / ".ilk-loop.log").read_text()
        assert jsonl_after == jsonl_content, "JSONL content changed on second run"

    print("PASS: preserve_active_run is complete and idempotent")
    return 0


if __name__ == "__main__":
    raise SystemExit(test_preserve_complete_and_idempotent())
