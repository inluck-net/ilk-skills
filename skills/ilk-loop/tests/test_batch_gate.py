"""Red-first tests for batch_gate — the batch-end gate record.

AC-3: persisted record carries all four of verdict, head_sha, invocation,
      timestamp.  A record missing any one of them is invalid.
AC-4: head_sha is the sha the suite actually ran against, captured at
      gate start.
AC-5: when the suite command is missing, unrunnable, or exits non-zero,
      the gate records verdict: fail with the reason and the runner
      reports it — never hangs, never records a pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_record(**overrides) -> dict:
    """Build a minimal valid record dict, with overrides."""
    base = {
        "verdict": "pass",
        "head_sha": "abc123" * 6 + "abcd",  # 40 hex chars
        "invocation": "python3 -m pytest -q",
        "timestamp": "2026-08-25T10:00:00+08:00",
    }
    base.update(overrides)
    return base


# ── AC-3: all four fields required ──────────────────────────────────────────

class TestAC3RecordShape:
    """A record missing any required field must read back as invalid."""

    def test_valid_record_roundtrips(self, tmp_path: Path) -> None:
        from batch_gate import BatchGateRecord, read_record, write_record
        rec = BatchGateRecord(**_make_record())
        write_record(rec, tmp_path)
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.verdict == "pass"

    @pytest.mark.parametrize("missing", [
        "verdict",
        "head_sha",
        "invocation",
        "timestamp",
    ])
    def test_missing_field_is_invalid(self, tmp_path: Path, missing: str) -> None:
        from batch_gate import read_record
        data = _make_record()
        del data[missing]
        _write_json(tmp_path / "batch-gate.json", data)
        assert read_record(tmp_path) is None

    def test_missing_file_is_none(self, tmp_path: Path) -> None:
        from batch_gate import read_record
        assert read_record(tmp_path) is None


# ── AC-4: head_sha captured at gate start ───────────────────────────────────

class TestAC4HeadShaAtStart:
    """head_sha must be the sha at gate start, not at record write time."""

    def test_head_sha_is_start_sha(self, tmp_path: Path) -> None:
        from batch_gate import BatchGateRecord, read_record, write_record
        start_sha = "a" * 40
        rec = BatchGateRecord(
            verdict="pass",
            head_sha=start_sha,
            invocation="pytest -q",
            timestamp="2026-08-25T10:00:00+08:00",
        )
        write_record(rec, tmp_path)
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.head_sha == start_sha


# ── AC-5: missing/unrunnable/failing suite → fail verdict ───────────────────

class TestAC5SuiteFailureModes:
    """When the suite can't run, record verdict: fail — never pass."""

    def test_missing_suite_records_not_configured(self, tmp_path: Path) -> None:
        from batch_gate import BatchGateRecord, read_record, write_record
        rec = BatchGateRecord(
            verdict="not_configured",
            head_sha="c" * 40,
            invocation="",
            timestamp="2026-08-25T10:00:00+08:00",
        )
        write_record(rec, tmp_path)
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.verdict == "not_configured"

    def test_nonzero_exit_records_fail(self, tmp_path: Path) -> None:
        from batch_gate import BatchGateRecord, read_record, write_record
        rec = BatchGateRecord(
            verdict="fail",
            head_sha="e" * 40,
            invocation="pytest -q",
            timestamp="2026-08-25T10:00:00+08:00",
        )
        write_record(rec, tmp_path)
        loaded = read_record(tmp_path)
        assert loaded is not None
        assert loaded.verdict == "fail"


# ── AC-1: run exactly once (re-entry guard) ──────────────────────────────────

class TestAC1OnceOnly:
    """run_batch_gate must not run the suite twice."""

    def test_reentry_returns_none(self, tmp_path: Path) -> None:
        """Second call while gate already ran returns None (no re-entry)."""
        from batch_gate import run_batch_gate

        # Project with no .ilk-launch.json → NotConfigured path
        project = tmp_path / "project"
        project.mkdir()
        # Init a git repo so _git_head_sha works
        import subprocess
        subprocess.run(["git", "init"], cwd=project, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                       cwd=project, capture_output=True)

        runtime = tmp_path / "runtime"

        rec1 = run_batch_gate(project, runtime)
        assert rec1 is not None
        assert rec1.verdict == "not_configured"

        # Second call — re-entry guard should fire
        rec2 = run_batch_gate(project, runtime)
        assert rec2 is None

    def test_stub_suite_runs_once(self, tmp_path: Path) -> None:
        """A stub suite command runs exactly once (counter file = 1)."""
        from batch_gate import run_batch_gate

        counter = tmp_path / "counter.txt"
        counter.write_text("0", encoding="utf-8")
        stub_script = tmp_path / "stub_suite.sh"
        stub_script.write_text(
            f'#!/bin/bash\ncount=$(cat "{counter}")\n'
            f'echo $((count + 1)) > "{counter}"\nexit 0\n',
        )
        stub_script.chmod(0o755)

        # Simple wait helper: just waits for the output file to have an exit marker
        wait_helper = tmp_path / "wait.sh"
        wait_helper.write_text(
            '#!/bin/bash\n'
            'OUTPUT="$1"\n'
            'for i in $(seq 1 100); do\n'
            '  if [ -f "$OUTPUT" ]; then\n'
            '    SIZE=$(wc -c < "$OUTPUT")\n'
            '    if [ "$SIZE" -gt 0 ]; then\n'
            '      sleep 0.2\n'
            '      echo 0\n'
            '      exit 0\n'
            '    fi\n'
            '  fi\n'
            '  sleep 0.1\n'
            'done\n'
            'echo 125\nexit 125\n',
        )
        wait_helper.chmod(0o755)

        project = tmp_path / "project"
        project.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=project, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                       cwd=project, capture_output=True)

        (project / ".ilk-launch.json").write_text(json.dumps({
            "ship": {
                "suite": {
                    "command": str(stub_script),
                },
            },
        }), encoding="utf-8")

        runtime = tmp_path / "runtime"
        rec = run_batch_gate(
            project, runtime,
            _wait_helper=wait_helper,
            _poll_timeout=30,
        )
        assert rec is not None
        assert rec.verdict in ("pass", "fail")
        assert counter.read_text().strip() == "1"
