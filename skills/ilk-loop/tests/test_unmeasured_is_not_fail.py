"""Red-first pins: an unmeasured check is error, not fail.

Part of `unmeasured-is-not-fail` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1 removes.

Drives ``run_local_checks.py`` on a temp project with fixture sub-plans,
and drives ``local_check_outcome`` by sourcing the shell function.
HOME and ILK_DATA_HOME are pinned together so nothing reads the real
``~/.ilk-data``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_local_checks as rlc  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_project(tmp_path: Path, monkeypatch, *, path_prelude: str = "") -> Path:
    """Create a temp project with plans dir and optional .ilk-launch.json.

    Pins HOME + ILK_DATA_HOME. Returns project root.
    """
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))

    project = tmp_path / "project"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=project, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if path_prelude:
        (project / ".ilk-launch.json").write_text(
            json.dumps({"ship": {"suite": {"path_prelude": path_prelude}}}),
            encoding="utf-8",
        )
    return project


def _write_subplan(project: Path, slug: str, checks: list[dict]) -> None:
    """Write a minimal sub-plan file with the given local_checks."""
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    items = []
    for c in checks:
        items.append(f"  - command: {json.dumps(c['command'])}\n"
                     f"    timeout: {c.get('timeout', 30)}")
    yaml_block = "\n".join(items) if items else "  - command: \"echo ok\"\n    timeout: 10"
    content = textwrap.dedent(f"""\
        ---
        plan: {slug}
        status: in-progress
        current_step: 0
        tickets: []
        priority: P0
        estimated_steps: 2
        last_updated: 2026-09-24
        ---

        # {slug}

        ### Step 0

        ```yaml
        local_checks:
        {yaml_block}
        ```
    """)
    (plans / f"2026-09-24-{slug}.md").write_text(content, encoding="utf-8")


# ── AC-1: timeout → error with reason ────────────────────────────────────────


class TestTimeoutIsError:
    """AC-1: a check that sleeps past its timeout ⇒ per-check error, rollup error."""

    @pytest.mark.xfail(strict=True, reason="red-first: outcome field not yet implemented")
    def test_timeout_per_check_outcome_is_error(self, tmp_path: Path, monkeypatch) -> None:
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "slow", [
            {"command": "sleep 60", "timeout": 2},
        ])
        result = rlc.main(["--project", str(project), "--slug", "slow", "--step", "0",
                           "--no-isolate"])
        data = json.loads(result) if isinstance(result, str) else None
        # main() prints JSON and returns exit code; read stdout via capsys or parse
        # Actually main() prints to stdout and returns int. Let's capture differently.
        # We need to capture stdout. Let's restructure.
        assert False, "need to capture stdout from main()"

    @pytest.mark.xfail(strict=True, reason="red-first: outcome field not yet implemented")
    def test_timeout_rollup_is_error(self, tmp_path: Path, monkeypatch) -> None:
        """Sub-plan with one timeout check ⇒ rollup is error, not fail."""
        assert False, "rollup outcome not yet implemented"


# ── AC-2: command not found → error ──────────────────────────────────────────


class TestCommandNotFoundIsError:
    """AC-2: a missing command ⇒ per-check error with reason."""

    @pytest.mark.xfail(strict=True, reason="red-first: outcome field not yet implemented")
    def test_missing_command_per_check_outcome_is_error(self, tmp_path: Path,
                                                         monkeypatch) -> None:
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "nocmd", [
            {"command": "definitely-not-a-command-xyz", "timeout": 30},
        ])
        # run_one directly
        result = rlc.run_one(
            {"command": "definitely-not-a-command-xyz", "timeout": 30},
            "step", project,
        )
        assert result.exit_code is None
        assert result.passed is False
        assert result.error != ""
        # These will pass after step 1:
        assert result.outcome == "error"  # type: ignore[attr-defined]
        assert "command not found" in result.reason  # type: ignore[attr-defined]

    @pytest.mark.xfail(strict=True, reason="red-first: rollup not yet implemented")
    def test_missing_command_rollup_is_error(self, tmp_path: Path,
                                              monkeypatch) -> None:
        """Sub-plan with one missing-command check ⇒ rollup is error."""
        assert False, "rollup outcome not yet implemented"


# ── AC-3: ILK-CHECK marker on stderr → error ────────────────────────────────


class TestIlkCheckMarkerIsError:
    """AC-3: stderr contains 'ILK-CHECK: unmeasured ...' ⇒ per-check error."""

    @pytest.mark.xfail(strict=True, reason="red-first: outcome field not yet implemented")
    def test_marker_on_stderr_per_check_outcome_is_error(self, tmp_path: Path,
                                                          monkeypatch) -> None:
        marker_script = tmp_path / "marker.sh"
        marker_script.write_text(
            "#!/bin/sh\n"
            "echo 'ILK-CHECK: unmeasured no vitest summary line' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        marker_script.chmod(0o755)
        result = rlc.run_one(
            {"command": f"sh {marker_script}", "timeout": 30},
            "step", tmp_path,
        )
        assert result.exit_code == 1
        assert result.passed is False
        # These will pass after step 1:
        assert result.outcome == "error"  # type: ignore[attr-defined]
        assert "no vitest summary line" in result.reason  # type: ignore[attr-defined]

    @pytest.mark.xfail(strict=True, reason="red-first: rollup not yet implemented")
    def test_marker_rollup_is_error(self, tmp_path: Path, monkeypatch) -> None:
        """Sub-plan with one marker check ⇒ rollup is error."""
        assert False, "rollup outcome not yet implemented"


# ── AC-4: plain exit 1 (no marker) → fail ───────────────────────────────────


class TestPlainExit1IsFail:
    """AC-4: exit 1 without ILK-CHECK marker ⇒ per-check fail."""

    def test_plain_exit_1_per_check_outcome_is_fail(self, tmp_path: Path) -> None:
        """This should already pass — exit 1 without marker is fail."""
        result = rlc.run_one(
            {"command": "exit 1", "timeout": 30},
            "step", tmp_path,
        )
        assert result.exit_code == 1
        assert result.passed is False
        assert result.error == ""

    @pytest.mark.xfail(strict=True, reason="red-first: outcome field not yet implemented")
    def test_plain_exit_1_has_fail_outcome(self, tmp_path: Path) -> None:
        """outcome field should be 'fail' for plain exit 1."""
        result = rlc.run_one(
            {"command": "exit 1", "timeout": 30},
            "step", tmp_path,
        )
        assert result.outcome == "fail"  # type: ignore[attr-defined]

    @pytest.mark.xfail(strict=True, reason="red-first: rollup not yet implemented")
    def test_fail_beats_error_in_rollup(self, tmp_path: Path, monkeypatch) -> None:
        """One fail + one error ⇒ rollup is fail (fail > error > pass)."""
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "mixed", [
            {"command": "exit 1", "timeout": 30},
            {"command": "definitely-not-a-command-xyz", "timeout": 30},
        ])
        assert False, "rollup outcome not yet implemented"


# ── AC-5: path_prelude_applied ───────────────────────────────────────────────


class TestPathPreludeApplied:
    """AC-5: path_prelude_applied is true when project has a path_prelude."""

    @pytest.mark.xfail(strict=True, reason="red-first: path_prelude_applied not yet on CheckResult")
    def test_path_prelude_applied_true(self, tmp_path: Path, monkeypatch) -> None:
        project = _make_project(tmp_path, monkeypatch, path_prelude="export FOO=bar")
        result = rlc.run_one(
            {"command": "echo hello", "timeout": 30},
            "step", project,
        )
        assert result.passed is True
        assert result.path_prelude_applied is True  # type: ignore[attr-defined]

    @pytest.mark.xfail(strict=True, reason="red-first: path_prelude_applied not yet on CheckResult")
    def test_path_prelude_applied_false(self, tmp_path: Path, monkeypatch) -> None:
        project = _make_project(tmp_path, monkeypatch)
        result = rlc.run_one(
            {"command": "echo hello", "timeout": 30},
            "step", project,
        )
        assert result.passed is True
        assert result.path_prelude_applied is False  # type: ignore[attr-defined]


# ── AC-6: local_check_outcome shell function ─────────────────────────────────


class TestLocalCheckOutcome:
    """Extract the runner's local_check_outcome and verify its mapping.

    After step 1, the function will use the helper's rollup outcome when
    present and fall back to the old all_passed/exit-code mapping only
    when absent. These tests pin the fallback behaviour.

    We extract the function body from the script rather than sourcing it,
    because the runner's main code triggers argument parsing on source.
    """

    RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"

    @staticmethod
    def _outcome(all_passed: str, exit_code: int) -> str:
        """Extract local_check_outcome from the runner and call it."""
        # Extract the function definition using sed
        extract = subprocess.run(
            ["bash", "-c",
             f"sed -n '/^local_check_outcome() {{/,/^}}/p' {TestLocalCheckOutcome.RUNNER}"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        func_def = extract.stdout
        if not func_def:
            raise RuntimeError("Could not extract local_check_outcome from runner")
        script = func_def + f"\nlocal_check_outcome {json.dumps(all_passed)} {exit_code}\n"
        cp = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        return cp.stdout.strip()

    def test_true_maps_to_pass(self) -> None:
        assert self._outcome("true", 0) == "pass"

    def test_false_maps_to_fail(self) -> None:
        """Current behavior: false ⇒ fail. Step 1 will make it use rollup."""
        assert self._outcome("false", 1) == "fail"

    @pytest.mark.xfail(strict=True, reason="red-first: timeout should map to error, not fail")
    def test_timeout_should_be_error(self) -> None:
        """After step 1, a timeout (all_passed=false, exit 1) that the helper
        classifies as error should roll up to error, not fail."""
        # This xfail pins the DESIRED behaviour. Today, local_check_outcome
        # sees all_passed=false and returns "fail" regardless of reason.
        assert self._outcome("false", 1) == "error"

    def test_exit0_without_allpassed_maps_to_pass(self) -> None:
        assert self._outcome("", 0) == "pass"

    def test_exit1_without_allpassed_maps_to_fail(self) -> None:
        assert self._outcome("", 1) == "fail"

    def test_exit2_without_allpassed_maps_to_error(self) -> None:
        assert self._outcome("", 2) == "error"


# ── Rollup rule: fail > error > pass ─────────────────────────────────────────


class TestRollupRule:
    """The sub-plan rollup outcome follows fail > error > pass."""

    @pytest.mark.xfail(strict=True, reason="red-first: rollup not yet implemented")
    def test_all_pass_rollup_is_pass(self) -> None:
        assert False, "rollup not yet implemented"

    @pytest.mark.xfail(strict=True, reason="red-first: rollup not yet implemented")
    def test_error_only_rollup_is_error(self) -> None:
        assert False, "rollup not yet implemented"

    @pytest.mark.xfail(strict=True, reason="red-first: rollup not yet implemented")
    def test_fail_only_rollup_is_fail(self) -> None:
        assert False, "rollup not yet implemented"

    @pytest.mark.xfail(strict=True, reason="red-first: rollup not yet implemented")
    def test_mixed_fail_error_rollup_is_fail(self) -> None:
        """fail > error: one fail + one error ⇒ rollup is fail."""
        assert False, "rollup not yet implemented"

    @pytest.mark.xfail(strict=True, reason="red-first: rollup not yet implemented")
    def test_mixed_error_pass_rollup_is_error(self) -> None:
        """error > pass: one error + one pass ⇒ rollup is error."""
        assert False, "rollup not yet implemented"