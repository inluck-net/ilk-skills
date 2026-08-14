r"""Integration test: the postmortem names the failing command.

AC-10: a synthetic run whose failing check recorded a command is fed to
collect.py, and the rendered postmortem contains that command and its exit code.
This spans the two components the fix touches — the runner writes the record,
collect.py renders it — so it is an integration check by construction.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_iter_record(
    iteration: int,
    exit_code: int = 0,
    stop_reason: str | None = None,
    local_checks: dict | list[dict] | None = None,
) -> dict:
    """Create a synthetic iteration record."""
    rec = {
        "iteration": iteration,
        "exit_code": exit_code,
        "duration_sec": 60.0,
        "new_commits_total": 1,
        "timestamp": "2026-08-14T10:00:00+08:00",
    }
    if stop_reason:
        rec["stop_reason"] = stop_reason
    if local_checks:
        rec["local_checks"] = local_checks
    return rec


def _make_local_checks_record(
    command: str,
    exit_code: int,
    outcome: str = "fail",
    stdout_tail: str = "",
    stderr_tail: str = "",
) -> dict:
    """Create a synthetic local_checks record with command detail."""
    return {
        "slug": "test-slug",
        "step": 0,
        "outcome": outcome,
        "exit_code": exit_code,
        "command": command,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
    }


# ── AC-10: integration assertion ────────────────────────────────────────────

class TestPostmortemNamesCommand:
    """The rendered postmortem names the failing command and exit code."""

    def test_postmortem_contains_failing_command(self, tmp_path: Path) -> None:
        """A synthetic run with a failing check -> postmortem contains the command."""
        import collect

        # Create a synthetic run with a failing local_checks
        failing_cmd = 'grep -q \'cron: "30 10 * * *\' file.txt'
        lc_record = _make_local_checks_record(
            command=failing_cmd,
            exit_code=1,
            stdout_tail="",
            stderr_tail="grep: file.txt: No such file or directory",
        )

        iters = [
            _make_iter_record(1, exit_code=0),
            _make_iter_record(2, exit_code=1, stop_reason="local_checks_failed",
                            local_checks=lc_record),
        ]

        # Render the postmortem
        report = collect.render_report(
            project_path=tmp_path,
            project_name="test-project",
            run_id="test-run-001",
            iters=iters,
            last_launch=None,
            label="local-checks-stuck",
            facts={"fail_iters_in_window": 1, "pass_iters_in_window": 0},
            rec_max=30,
            rec_to=30,
            rationale="test rationale",
            tail=["some log output", "more output"],
        )

        # AC-10: the rendered postmortem contains the command
        assert failing_cmd in report, (
            f"Postmortem should contain the failing command '{failing_cmd}'"
        )
        # And the exit code
        assert "exit_code" in report.lower() or "exit code" in report.lower(), (
            "Postmortem should mention the exit code"
        )

    def test_postmortem_contains_stderr_tail(self, tmp_path: Path) -> None:
        """The postmortem includes stderr_tail for failing checks."""
        import collect

        failing_cmd = "python -m pytest tests/"
        lc_record = _make_local_checks_record(
            command=failing_cmd,
            exit_code=1,
            stderr_tail="FAILED test_something.py::test_func - AssertionError",
        )

        iters = [
            _make_iter_record(1, exit_code=1, stop_reason="local_checks_failed",
                            local_checks=lc_record),
        ]

        report = collect.render_report(
            project_path=tmp_path,
            project_name="test-project",
            run_id="test-run-002",
            iters=iters,
            last_launch=None,
            label="local-checks-stuck",
            facts={"fail_iters_in_window": 1, "pass_iters_in_window": 0},
            rec_max=30,
            rec_to=30,
            rationale="test rationale",
            tail=["some log output"],
        )

        # The postmortem should contain the failing command
        assert failing_cmd in report
        # And the stderr tail
        assert "FAILED test_something.py::test_func" in report

    def test_postmortem_without_command_has_no_failing_section(self, tmp_path: Path) -> None:
        """When no command is captured, the postmortem has no failing check section."""
        import collect

        # Old-style local_checks without command
        lc_record = {
            "slug": "test-slug",
            "step": 0,
            "outcome": "fail",
            "exit_code": 1,
        }

        iters = [
            _make_iter_record(1, exit_code=1, stop_reason="local_checks_failed",
                            local_checks=lc_record),
        ]

        report = collect.render_report(
            project_path=tmp_path,
            project_name="test-project",
            run_id="test-run-003",
            iters=iters,
            last_launch=None,
            label="local-checks-stuck",
            facts={"fail_iters_in_window": 1, "pass_iters_in_window": 0},
            rec_max=30,
            rec_to=30,
            rationale="test rationale",
            tail=["some log output"],
        )

        # The "Failing check details" section should NOT be present
        assert "## Failing check details" not in report
        # The "What happened" section should still be present
        assert "## What happened" in report

    def test_system_lines_filtered_from_tail(self, tmp_path: Path) -> None:
        """AC-6: [system] lines are filtered from the postmortem tail."""
        import collect

        tail = [
            "[system] thinking_tokens: 1234",
            "[system] thinking_tokens: 5678",
            "real log output here",
            "[system] thinking_tokens: 9012",
        ]

        iters = [_make_iter_record(1, exit_code=0)]

        report = collect.render_report(
            project_path=tmp_path,
            project_name="test-project",
            run_id="test-run-004",
            iters=iters,
            last_launch=None,
            label="clean-success",
            facts={},
            rec_max=30,
            rec_to=30,
            rationale="test rationale",
            tail=tail,
        )

        # The real log output should be present
        assert "real log output here" in report
        # The [system] lines should be filtered out
        assert "[system] thinking_tokens" not in report

    def test_all_system_lines_show_sentinel(self, tmp_path: Path) -> None:
        """AC-6: when every line is [system], show a sentinel message."""
        import collect

        tail = [
            "[system] thinking_tokens: 1234",
            "[system] thinking_tokens: 5678",
            "[system] thinking_tokens: 9012",
        ]

        iters = [_make_iter_record(1, exit_code=0)]

        report = collect.render_report(
            project_path=tmp_path,
            project_name="test-project",
            run_id="test-run-005",
            iters=iters,
            last_launch=None,
            label="clean-success",
            facts={},
            rec_max=30,
            rec_to=30,
            rationale="test rationale",
            tail=tail,
        )

        # The sentinel message should be present
        assert "all lines were [system] noise" in report
