"""Red-first pins: the suite runs only in the driver.

Part of `the-suite-runs-only-in-the-driver` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1/2 removes.

Drives ``verification_record.py`` and ``verify_attribution.py`` in a temp git
repo with a fake ``ship.suite`` invocation.  HOME and ILK_DATA_HOME are pinned
together so nothing reads the real ``~/.ilk-data``.

AC-1: with ILK_WORKER_SESSION=1, --run-suite refuses (worker-session guard).
AC-2: with the variable unset, --run-suite behaves as today (unmarked).
AC-3: a modified tracked file ⇒ --run-suite refuses (dirty tree); untracked ⇒ runs.
AC-4: stale record + --remeasure-if-stale ⇒ re-measures, updates verified_head.
AC-5: fresh record + --remeasure-if-stale ⇒ no re-run, attribution as today.
AC-6: --remeasure-if-stale with ILK_WORKER_SESSION=1 ⇒ refusal.
AC-7: driver sets ILK_WORKER_SESSION=1 on worker, not on gate-first.
AC-8: template step 1 contains --remeasure-if-stale and "end your turn".
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

import verification_record as vr  # noqa: E402
import verify_attribution as vat  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _make_repo(tmp_path: Path, monkeypatch) -> tuple[Path, str, str]:
    """Create a temp git repo with two commits, pin HOME + ILK_DATA_HOME.

    Returns (project, base_sha, head_sha).
    """
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    _git(project, "commit", "-q", "--allow-empty", "-m", "base")
    base_sha = _git(project, "rev-parse", "HEAD")
    _git(project, "commit", "-q", "--allow-empty", "-m", "head")
    head_sha = _git(project, "rev-parse", "HEAD")
    return project, base_sha, head_sha


def _write_launch_json(project: Path, suite_cmd: str) -> None:
    """Write .ilk-launch.json pointing ship.suite at *suite_cmd*."""
    (project / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"suite": {"command": suite_cmd}}}),
        encoding="utf-8",
    )


def _make_sentinel_script(tmp_path: Path) -> Path:
    """Create a fake suite script that touches a sentinel file and prints
    valid pytest output.

    The sentinel proves the suite actually ran.  Returns the script path;
    the sentinel path is tmp_path / "sentinel".
    """
    sentinel = tmp_path / "sentinel"
    script = tmp_path / "fake-suite.sh"
    # Print enough pytest-like output for parse_pytest_output to succeed.
    script.write_text(
        f"#!/bin/sh\ntouch {sentinel}\n"
        f"echo '====== 1 passed in 0.01s ======'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _record_path(tmp_path: Path) -> Path:
    """Deterministic record path inside tmp_path (avoids ilk_paths divergence)."""
    return tmp_path / "data" / "logs" / "verification" / "test-batch.md"


def _write_existing_record(record: Path, head: str, *,
                           suite_failed: int | str = 0) -> None:
    """Write a minimal verification record for *head*."""
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(
        f"# Batch verification record — test-batch\n\n"
        f"record_writer: verification_record.py\n"
        f"verified_head: {head}\n"
        f"base_sha: deadbeef\n"
        f"suite_invocation: echo ok\n"
        f"suite_scope: full\n"
        f"suite_failed: {suite_failed}\n\n"
        f"## At-base rerun\n\n_(no failures)_\n",
        encoding="utf-8",
    )


# ── AC-1: ILK_WORKER_SESSION=1 ⇒ --run-suite refuses ───────────────────────


class TestAC1WorkerSessionRefuses:
    """With ILK_WORKER_SESSION=1, ``--run-suite`` exits nonzero, prints both
    refusal lines, and leaves the record byte-identical.  No suite subprocess
    is spawned (sentinel absent).
    """

    @pytest.mark.xfail(strict=True, reason="red-first: worker-session guard not yet implemented")
    def test_run_suite_refuses_in_worker_session(self, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
        script = _make_sentinel_script(tmp_path)
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        _write_launch_json(project, str(script))
        record = _write_existing_record(
            _record_path(tmp_path), head_sha, suite_failed=0)
        record_before = record.read_bytes()

        monkeypatch.setenv("ILK_WORKER_SESSION", "1")
        ret = vr.main([
            "--project", str(project),
            "--batch", "test-batch",
            "--base-sha", base_sha,
            "--run-suite",
        ])
        assert ret != 0, "should refuse in a worker session"
        sentinel = tmp_path / "sentinel"
        assert not sentinel.exists(), "suite should not have been spawned"
        assert record.read_bytes() == record_before, "record should be unchanged"


# ── AC-2: unset ⇒ --run-suite behaves as today (unmarked) ───────────────────


class TestAC2UnsetBehavesAsToday:
    """With ILK_WORKER_SESSION unset, ``--run-suite`` runs normally."""

    def test_run_suite_runs_without_worker_env(self, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
        """A normal --run-suite invocation proceeds (exit 0, sentinel touched)."""
        script = _make_sentinel_script(tmp_path)
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        _write_launch_json(project, str(script))
        _record_path(tmp_path)  # ensure parent dir exists
        # Ensure ILK_WORKER_SESSION is NOT set.
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        ret = vr.main([
            "--project", str(project),
            "--batch", "test-batch",
            "--base-sha", base_sha,
            "--run-suite",
        ])
        assert ret == 0, "normal --run-suite should succeed"
        sentinel = tmp_path / "sentinel"
        assert sentinel.exists(), "suite should have been spawned"


# ── AC-3: dirty tree refuses; untracked alone runs ──────────────────────────


class TestAC3DirtyTreeRefuses:
    """A modified tracked file ⇒ ``--run-suite`` refuses with ``dirty tree``.
    An untracked file alone ⇒ it runs.
    """

    @pytest.mark.xfail(strict=True, reason="red-first: dirty-tree guard not yet implemented")
    def test_dirty_tracked_file_refuses(self, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
        script = _make_sentinel_script(tmp_path)
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        _write_launch_json(project, str(script))
        record = _write_existing_record(
            _record_path(tmp_path), head_sha, suite_failed=0)
        record_before = record.read_bytes()

        # Create a tracked file and modify it.
        tracked = project / "tracked.txt"
        tracked.write_text("original", encoding="utf-8")
        _git(project, "add", "tracked.txt")
        _git(project, "commit", "-m", "add tracked")
        tracked.write_text("modified", encoding="utf-8")

        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        ret = vr.main([
            "--project", str(project),
            "--batch", "test-batch",
            "--base-sha", base_sha,
            "--run-suite",
        ])
        assert ret != 0, "should refuse on dirty tracked tree"
        sentinel = tmp_path / "sentinel"
        assert not sentinel.exists(), "suite should not have been spawned"
        assert record.read_bytes() == record_before, "record should be unchanged"

    def test_untracked_file_alone_runs(self, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        script = _make_sentinel_script(tmp_path)
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        _write_launch_json(project, str(script))
        _record_path(tmp_path)

        # Create an untracked file only.
        untracked = project / "scratch.txt"
        untracked.write_text("scratch", encoding="utf-8")

        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        ret = vr.main([
            "--project", str(project),
            "--batch", "test-batch",
            "--base-sha", base_sha,
            "--run-suite",
        ])
        assert ret == 0, "untracked file alone should not block"
        sentinel = tmp_path / "sentinel"
        assert sentinel.exists(), "suite should have been spawned"


# ── AC-4: stale record + --remeasure-if-stale ⇒ re-measures ─────────────────


class TestAC4StaleRemesure:
    """A record whose verified_head is HEAD~1, a clean tree, and
    ``verify_attribution.py --remeasure-if-stale`` ⇒ the fake suite runs
    once, the record's verified_head is HEAD, and attribution runs on the
    new record.
    """

    @pytest.mark.xfail(strict=True, reason="red-first: --remeasure-if-stale not yet implemented")
    def test_stale_record_gets_remeasured(self, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
        script = _make_sentinel_script(tmp_path)
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        _write_launch_json(project, str(script))
        # Write a record pointing at base_sha (stale — HEAD is head_sha).
        record = _write_existing_record(
            _record_path(tmp_path), base_sha, suite_failed=0)

        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        # verify_attribution.py --remeasure-if-stale should re-measure.
        ret = vat.main([
            "--batch", "test-batch",
            "--project", str(project),
            "--remeasure-if-stale",
        ])
        sentinel = tmp_path / "sentinel"
        assert sentinel.exists(), "suite should have run once for re-measure"
        # The record should now point at HEAD.
        text = record.read_text(encoding="utf-8")
        assert head_sha in text, (
            f"record should have been updated to HEAD {head_sha[:12]}, "
            f"but verified_head not found in record"
        )


# ── AC-5: fresh record + --remeasure-if-stale ⇒ no re-run ───────────────────


class TestAC5FreshNoRerun:
    """A fresh record (verified_head == HEAD, numeric suite_failed) and
    ``--remeasure-if-stale`` ⇒ no suite run (sentinel absent); attribution
    as today.
    """

    @pytest.mark.xfail(strict=True, reason="red-first: --remeasure-if-stale not yet implemented")
    def test_fresh_record_skips_remeasure(self, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
        script = _make_sentinel_script(tmp_path)
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        _write_launch_json(project, str(script))
        _write_existing_record(
            _record_path(tmp_path), head_sha, suite_failed=0)

        sentinel = tmp_path / "sentinel"
        monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
        ret = vat.main([
            "--batch", "test-batch",
            "--project", str(project),
            "--remeasure-if-stale",
        ])
        # Attribution should succeed (0 failures, clean record).
        assert ret == 0, "attribution should pass on a clean record"
        assert not sentinel.exists(), "suite should NOT have been re-run"


# ── AC-6: --remeasure-if-stale + ILK_WORKER_SESSION=1 ⇒ refusal ─────────────


class TestAC6RemesureInWorkerRefuses:
    """``--remeasure-if-stale`` with ``ILK_WORKER_SESSION=1`` and a stale
    record ⇒ refusal, no suite run.
    """

    @pytest.mark.xfail(strict=True, reason="red-first: worker guard on remeasure not yet implemented")
    def test_remeasure_refuses_in_worker(self, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
        script = _make_sentinel_script(tmp_path)
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        _write_launch_json(project, str(script))
        record = _write_existing_record(
            _record_path(tmp_path), base_sha, suite_failed=0)
        record_before = record.read_bytes()

        monkeypatch.setenv("ILK_WORKER_SESSION", "1")
        ret = vat.main([
            "--batch", "test-batch",
            "--project", str(project),
            "--remeasure-if-stale",
        ])
        assert ret != 0, "should refuse in a worker session"
        sentinel = tmp_path / "sentinel"
        assert not sentinel.exists(), "suite should not have been spawned"
        assert record.read_bytes() == record_before, "record should be unchanged"


# ── AC-7: driver sets ILK_WORKER_SESSION=1 on worker, not gate-first ────────


class TestAC7DriverSetsWorkerEnv:
    """The claude command that ``invoke_claude_iteration`` builds carries
    ``ILK_WORKER_SESSION=1``, and the gate-first invocation of a step's
    local_checks does not.
    """

    @pytest.mark.xfail(strict=True, reason="red-first: driver env export not yet implemented")
    def test_invoke_claude_iteration_exports_worker_session(self) -> None:
        """Parse the runner script and assert invoke_claude_iteration's claude
        command line includes ILK_WORKER_SESSION=1.

        This is a source-level assertion (grep the script), not a runtime
        test — same pattern as existing runner tests.
        """
        runner = SCRIPTS_DIR / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        # Find the invoke_claude_iteration function body.
        marker = "invoke_claude_iteration()"
        idx = text.find(marker)
        assert idx != -1, "invoke_claude_iteration not found in runner"
        # Look for ILK_WORKER_SESSION in the function (within ~200 lines).
        chunk = text[idx:idx + 8000]
        assert "ILK_WORKER_SESSION" in chunk, (
            "invoke_claude_iteration does not set ILK_WORKER_SESSION"
        )

    def test_gate_first_does_not_export_worker_session(self) -> None:
        """The gate-first path (attempt_gate_first_fast_path or the
        local_checks invocation) must NOT carry ILK_WORKER_SESSION=1.
        """
        runner = SCRIPTS_DIR / "run_ilk_loop_claude.sh"
        text = runner.read_text(encoding="utf-8")
        # Find the gate-first function.
        marker = "attempt_gate_first_fast_path"
        idx = text.find(marker)
        assert idx != -1, "attempt_gate_first_fast_path not found in runner"
        chunk = text[idx:idx + 8000]
        assert "ILK_WORKER_SESSION" not in chunk, (
            "gate-first path must NOT export ILK_WORKER_SESSION"
        )


# ── AC-8: template step 1 contains --remeasure-if-stale and "end your turn" ─


class TestAC8TemplateStep1:
    """The template's step 1 contains ``--remeasure-if-stale`` and "end your
    turn", and does not contain "rerun step 0".
    """

    @pytest.mark.xfail(strict=True, reason="red-first: template update not yet done")
    def test_step1_has_remeasure_flag(self) -> None:
        template = (
            Path(__file__).resolve().parent.parent / "templates"
            / "batch-verification-subplan.md"
        )
        text = template.read_text(encoding="utf-8")
        # Find step 1.
        idx = text.find("### Step 1")
        assert idx != -1, "step 1 not found in template"
        step1 = text[idx:]
        assert "--remeasure-if-stale" in step1, (
            "step 1 should contain --remeasure-if-stale"
        )

    @pytest.mark.xfail(strict=True, reason="red-first: template update not yet done")
    def test_step1_says_end_your_turn(self) -> None:
        template = (
            Path(__file__).resolve().parent.parent / "templates"
            / "batch-verification-subplan.md"
        )
        text = template.read_text(encoding="utf-8")
        idx = text.find("### Step 1")
        assert idx != -1, "step 1 not found in template"
        step1 = text[idx:]
        assert "end your turn" in step1.lower(), (
            'step 1 should say "end your turn"'
        )

    def test_step1_no_rerun_step0(self) -> None:
        template = (
            Path(__file__).resolve().parent.parent / "templates"
            / "batch-verification-subplan.md"
        )
        text = template.read_text(encoding="utf-8")
        idx = text.find("### Step 1")
        assert idx != -1, "step 1 not found in template"
        step1 = text[idx:]
        # Stop at the next ### heading.
        next_heading = step1.find("\n### ", 1)
        if next_heading != -1:
            step1 = step1[:next_heading]
        assert "rerun step 0" not in step1.lower(), (
            'step 1 should NOT say "rerun step 0"'
        )
