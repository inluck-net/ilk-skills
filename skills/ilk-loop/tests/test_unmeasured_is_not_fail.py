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
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_local_checks as rlc  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_project(tmp_path: Path, monkeypatch, *, path_prelude: str = "") -> Path:
    """Create a temp project with plans dir, MASTER file, and optional .ilk-launch.json.

    Pins HOME + ILK_DATA_HOME. Returns project root.
    """
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))

    project = tmp_path / "project"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    # MASTER file is required for plans dir resolution
    (plans / "MASTER-2026-09-24-execution-plan.md").write_text(
        "---\nmaster_plan: 2026-09-24-execution\nstatus: active\n---\n\n# Test master\n",
        encoding="utf-8",
    )
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
            json.dumps({"ship": {"suite": {
                "command": "echo suite",
                "path_prelude": path_prelude,
            }}}),
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
    yaml_block = "\n".join(items) if items else '  - command: "echo ok"\n    timeout: 10'
    fence = "```"
    content = (
        f"---\n"
        f"plan: {slug}\n"
        f"status: in-progress\n"
        f"current_step: 0\n"
        f"tickets: []\n"
        f"priority: P0\n"
        f"estimated_steps: 2\n"
        f"last_updated: 2026-09-24\n"
        f"---\n"
        f"\n"
        f"# {slug}\n"
        f"\n"
        f"### Step 0\n"
        f"\n"
        f"{fence}yaml\n"
        f"local_checks:\n"
        f"{yaml_block}\n"
        f"{fence}\n"
    )
    (plans / f"2026-09-24-{slug}.md").write_text(content, encoding="utf-8")


# ── AC-1: timeout → error with reason ────────────────────────────────────────


class TestTimeoutIsError:
    """AC-1: a check that sleeps past its timeout ⇒ per-check error, rollup error."""

    def test_timeout_per_check_outcome_is_error(self, tmp_path: Path) -> None:
        """A check that sleeps past its timeout has outcome=error with reason."""
        r = rlc.run_one(
            {"command": "sleep 60", "timeout": 2},
            "step", tmp_path,
        )
        assert r.exit_code is None
        assert r.passed is False
        assert "timeout" in r.error
        assert r.outcome == "error"
        assert r.reason is not None
        assert "timeout" in r.reason

    def test_timeout_rollup_is_error(self, tmp_path: Path, monkeypatch) -> None:
        """Sub-plan with one timeout check ⇒ rollup outcome is error, not fail."""
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "slow", [
            {"command": "sleep 60", "timeout": 2},
        ])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rlc.main(["--project", str(project), "--slug", "slow", "--step", "0",
                       "--no-isolate"])
        data = json.loads(buf.getvalue())
        assert data["outcome"] == "error"
        assert data["all_passed"] is False


# ── AC-2: command not found → error ──────────────────────────────────────────


class TestCommandNotFoundIsError:
    """AC-2: a missing command ⇒ per-check error with reason."""

    def test_missing_command_per_check_outcome_is_error(self, tmp_path: Path) -> None:
        """A missing command has outcome=error with reason containing 'command not found'."""
        r = rlc.run_one(
            {"command": "definitely-not-a-command-xyz", "timeout": 30},
            "step", tmp_path,
        )
        assert r.passed is False
        assert r.outcome == "error"
        assert r.reason is not None
        assert "command not found" in r.reason

    def test_missing_command_rollup_is_error(self, tmp_path: Path,
                                              monkeypatch) -> None:
        """Sub-plan with one missing-command check ⇒ rollup outcome is error."""
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "nocmd", [
            {"command": "definitely-not-a-command-xyz", "timeout": 30},
        ])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rlc.main(["--project", str(project), "--slug", "nocmd", "--step", "0",
                       "--no-isolate"])
        data = json.loads(buf.getvalue())
        assert data["outcome"] == "error"
        assert data["all_passed"] is False


# ── AC-3: ILK-CHECK marker on stderr → error ────────────────────────────────


class TestIlkCheckMarkerIsError:
    """AC-3: stderr contains 'ILK-CHECK: unmeasured ...' ⇒ per-check error."""

    def test_marker_on_stderr_per_check_outcome_is_error(self, tmp_path: Path) -> None:
        """ILK-CHECK: unmeasured on stderr ⇒ outcome=error with captured reason."""
        marker_script = tmp_path / "marker.sh"
        marker_script.write_text(
            "#!/bin/sh\n"
            "echo 'ILK-CHECK: unmeasured no vitest summary line' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        marker_script.chmod(0o755)
        r = rlc.run_one(
            {"command": f"sh {marker_script}", "timeout": 30},
            "step", tmp_path,
        )
        assert r.exit_code == 1
        assert r.passed is False
        assert r.outcome == "error"
        assert r.reason is not None
        assert "no vitest summary line" in r.reason

    def test_marker_rollup_is_error(self, tmp_path: Path, monkeypatch) -> None:
        """Sub-plan with one marker check ⇒ rollup outcome is error."""
        project = _make_project(tmp_path, monkeypatch)
        marker_script = tmp_path / "marker.sh"
        marker_script.write_text(
            "#!/bin/sh\n"
            "echo 'ILK-CHECK: unmeasured no vitest summary line' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        marker_script.chmod(0o755)
        _write_subplan(project, "marker", [
            {"command": f"sh {marker_script}", "timeout": 30},
        ])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rlc.main(["--project", str(project), "--slug", "marker", "--step", "0",
                       "--no-isolate"])
        data = json.loads(buf.getvalue())
        assert data["outcome"] == "error"
        assert data["all_passed"] is False


# ── AC-4: plain exit 1 (no marker) → fail ───────────────────────────────────


class TestPlainExit1IsFail:
    """AC-4: exit 1 without ILK-CHECK marker ⇒ per-check fail."""

    def test_plain_exit_1_per_check_outcome_is_fail(self, tmp_path: Path) -> None:
        """exit 1 without marker ⇒ outcome=fail."""
        r = rlc.run_one(
            {"command": "exit 1", "timeout": 30},
            "step", tmp_path,
        )
        assert r.exit_code == 1
        assert r.passed is False
        assert r.error == ""
        assert r.outcome == "fail"
        assert r.reason is None

    def test_fail_beats_error_in_rollup(self, tmp_path: Path, monkeypatch) -> None:
        """One fail + one error ⇒ rollup is fail (fail > error > pass)."""
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "mixed", [
            {"command": "exit 1", "timeout": 30},
            {"command": "definitely-not-a-command-xyz", "timeout": 30},
        ])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rlc.main(["--project", str(project), "--slug", "mixed", "--step", "0",
                       "--no-isolate"])
        data = json.loads(buf.getvalue())
        assert data["outcome"] == "fail"
        assert data["all_passed"] is False


# ── AC-5: path_prelude_applied ───────────────────────────────────────────────


class TestPathPreludeApplied:
    """AC-5: path_prelude_applied is true when project has a path_prelude."""

    def test_path_prelude_applied_true(self, tmp_path: Path, monkeypatch) -> None:
        """path_prelude_applied is true when project has a path_prelude."""
        project = _make_project(tmp_path, monkeypatch, path_prelude="export FOO=bar")
        r = rlc.run_one(
            {"command": "echo hello", "timeout": 30},
            "step", project,
        )
        assert r.passed is True
        assert r.path_prelude_applied is True

    def test_path_prelude_applied_false(self, tmp_path: Path, monkeypatch) -> None:
        """path_prelude_applied is false when project has no path_prelude."""
        project = _make_project(tmp_path, monkeypatch)
        r = rlc.run_one(
            {"command": "echo hello", "timeout": 30},
            "step", project,
        )
        assert r.passed is True
        assert r.path_prelude_applied is False


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

    @staticmethod
    def _run_and_get_outcome(project: Path, slug: str) -> dict:
        """Run local_checks and return the parsed JSON output."""
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rlc.main(["--project", str(project), "--slug", slug, "--step", "0",
                       "--no-isolate"])
        return json.loads(buf.getvalue())

    def test_all_pass_rollup_is_pass(self, tmp_path: Path, monkeypatch) -> None:
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "allpass", [
            {"command": "echo ok", "timeout": 10},
            {"command": "echo also ok", "timeout": 10},
        ])
        data = self._run_and_get_outcome(project, "allpass")
        assert data["outcome"] == "pass"
        assert data["all_passed"] is True

    def test_error_only_rollup_is_error(self, tmp_path: Path, monkeypatch) -> None:
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "erronly", [
            {"command": "definitely-not-a-command-xyz", "timeout": 10},
        ])
        data = self._run_and_get_outcome(project, "erronly")
        assert data["outcome"] == "error"
        assert data["all_passed"] is False

    def test_fail_only_rollup_is_fail(self, tmp_path: Path, monkeypatch) -> None:
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "failonly", [
            {"command": "exit 1", "timeout": 10},
        ])
        data = self._run_and_get_outcome(project, "failonly")
        assert data["outcome"] == "fail"
        assert data["all_passed"] is False

    def test_mixed_fail_error_rollup_is_fail(self, tmp_path: Path,
                                              monkeypatch) -> None:
        """fail > error: one fail + one error ⇒ rollup is fail."""
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "mixedfe", [
            {"command": "exit 1", "timeout": 10},
            {"command": "definitely-not-a-command-xyz", "timeout": 10},
        ])
        data = self._run_and_get_outcome(project, "mixedfe")
        assert data["outcome"] == "fail"
        assert data["all_passed"] is False

    def test_mixed_error_pass_rollup_is_error(self, tmp_path: Path,
                                               monkeypatch) -> None:
        """error > pass: one error + one pass ⇒ rollup is error."""
        project = _make_project(tmp_path, monkeypatch)
        _write_subplan(project, "mixedep", [
            {"command": "echo ok", "timeout": 10},
            {"command": "definitely-not-a-command-xyz", "timeout": 10},
        ])
        data = self._run_and_get_outcome(project, "mixedep")
        assert data["outcome"] == "error"
        assert data["all_passed"] is False