"""Red-first pins: a measured record is never weakened by a re-run.

Part of `a-measured-record-is-never-weakened` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 2 removes.

Drives ``verification_record.py`` in a temp git repo with a fake
``ship.suite`` invocation.  HOME and ILK_DATA_HOME are pinned together
so nothing reads the real ``~/.ilk-data``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verification_record as vr  # noqa: E402


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


def _make_suite_script(tmp_path: Path, *, stdout: str = "",
                        hang: bool = False) -> Path:
    """Create a fake suite script.

    *stdout* is printed then the script exits 0.
    *hang*=True makes it sleep forever (for timeout tests).
    """
    script = tmp_path / "fake-suite.sh"
    if hang:
        script.write_text("#!/bin/sh\nsleep 86400\n", encoding="utf-8")
    else:
        script.write_text(f"#!/bin/sh\necho {repr(stdout)}\n", encoding="utf-8")
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


# ── AC-1: a measured record for the same HEAD is never replaced ─────────────
# by an unmeasured outcome.  The #6612 shape.


class TestAC1MeasuredRecordSurvives:
    """A record for HEAD with ``suite_failed: 0`` exists.

    ``--run-suite`` is run again with an invocation whose output has no
    summary line.  The record is byte-identical afterwards, exit is
    nonzero, and stderr contains ``ILK-CHECK: unmeasured``.
    """

    def test_measured_record_not_overwritten(self, tmp_path: Path,
                                              monkeypatch) -> None:
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        suite = _make_suite_script(tmp_path, stdout="no summary here")
        _write_launch_json(project, str(suite))

        record = _record_path(tmp_path)
        _write_existing_record(record, head_sha, suite_failed=0)
        original = record.read_bytes()

        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(project),
             "--record", str(record),
             "--run-suite", "--base-sha", base_sha,
             "--suite-timeout", "10"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )

        assert res.returncode != 0, (
            f"expected nonzero exit for unmeasured; got {res.returncode}"
        )
        assert record.read_bytes() == original, (
            "the measured record was overwritten by an unmeasured outcome"
        )
        assert "ILK-CHECK: unmeasured" in res.stderr, (
            f"stderr missing ILK-CHECK marker: {res.stderr!r}"
        )


# ── AC-2: with no record, a timeout still leaves the stub ───────────────────
# Today's behaviour — must keep passing.


class TestAC2TimeoutLeavesStub:
    """With no record present, a suite that times out still leaves the stub."""

    def test_stub_written_on_timeout(self, tmp_path: Path,
                                     monkeypatch) -> None:
        project, base_sha, _ = _make_repo(tmp_path, monkeypatch)
        suite = _make_suite_script(tmp_path, hang=True)
        _write_launch_json(project, str(suite))

        record = _record_path(tmp_path)
        assert not record.exists(), "record should not exist yet"

        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(project),
             "--record", str(record),
             "--run-suite", "--base-sha", base_sha,
             "--suite-timeout", "2"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )

        assert res.returncode != 0
        assert record.exists(), "stub was not written after timeout"
        text = record.read_text(encoding="utf-8")
        assert "suite_failed: unmeasured" in text, (
            f"stub missing unmeasured marker: {text!r}"
        )


# ── AC-3: a record for a DIFFERENT verified_head is replaced ────────────────
# Today's behaviour — must keep passing.


class TestAC3DifferentHeadIsReplaced:
    """A record for a different ``verified_head`` is replaced by the stub,
    then by the final record."""

    def test_different_head_gets_replaced(self, tmp_path: Path,
                                          monkeypatch) -> None:
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        summary = "= 5 passed, 0 failed in 1.0s ="
        suite = _make_suite_script(tmp_path, stdout=summary)
        _write_launch_json(project, str(suite))

        record = _record_path(tmp_path)
        _write_existing_record(record, "deadbeef" * 5, suite_failed=0)

        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(project),
             "--record", str(record),
             "--run-suite", "--base-sha", base_sha,
             "--suite-timeout", "30"],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )

        assert res.returncode == 0, f"expected success: {res.stderr!r}"
        text = record.read_text(encoding="utf-8")
        assert f"verified_head: {head_sha}" in text
        assert "suite_failed: 0" in text


# ── AC-4: a successful measured run replaces an existing measured record ─────
# for the same head, and no .tmp file remains.


class TestAC4MeasuredReplacesMeasured:
    """A successful measured run over an existing measured record for the
    same head replaces it, and no ``.tmp`` file remains."""

    def test_measured_overwrites_measured(self, tmp_path: Path,
                                          monkeypatch) -> None:
        project, base_sha, head_sha = _make_repo(tmp_path, monkeypatch)
        summary = "= 10 passed, 0 failed in 2.0s ="
        suite = _make_suite_script(tmp_path, stdout=summary)
        _write_launch_json(project, str(suite))

        record = _record_path(tmp_path)
        _write_existing_record(record, head_sha, suite_failed=0)
        original = record.read_bytes()

        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
             "--project", str(project),
             "--record", str(record),
             "--run-suite", "--base-sha", base_sha,
             "--suite-timeout", "30"],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )

        assert res.returncode == 0, f"expected success: {res.stderr!r}"
        new_content = record.read_bytes()
        assert new_content != original, (
            "record was not updated by a successful measured run"
        )
        text = new_content.decode("utf-8")
        assert "suite_failed: 0" in text
        assert f"verified_head: {head_sha}" in text
        # No .tmp file left behind.
        assert not list(record.parent.glob("*.tmp")), (
            ".tmp files left behind after successful write"
        )


# ── AC-5: batch-gate.json is never weakened ─────────────────────────────────
# An existing measured batch-gate.json for the same head survives a gate
# run that errors, byte-identical.


class TestAC5BatchGateNeverWeakened:
    """An existing measured ``batch-gate.json`` for the same head survives
    a gate run that errors, byte-identical."""

    def test_batch_gate_survives_error(self, tmp_path: Path,
                                        monkeypatch) -> None:
        data_home = tmp_path / "data"
        home = tmp_path / "home"
        monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
        monkeypatch.setenv("HOME", str(home))

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
        _git(project, "commit", "-q", "--allow-empty", "-m", "init")
        head_sha = _git(project, "rev-parse", "HEAD")

        from ilk_paths import external_runtime_dir, resolve_project_key
        key = resolve_project_key(project)
        runtime_dir = external_runtime_dir(key)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        gate_path = runtime_dir / "batch-gate.json"

        existing = {
            "verdict": "pass",
            "head_sha": head_sha,
            "invocation": "echo ok",
            "timestamp": "2026-09-24T12:00:00+08:00",
            "tree_sha": _git(project, "rev-parse", "HEAD^{tree}"),
            "writer": "batch_gate.py",
        }
        gate_path.write_text(
            json.dumps(existing, indent=2) + "\n", encoding="utf-8"
        )
        original = gate_path.read_bytes()

        # Run batch_gate on the SAME runtime_dir.  A project with no suite
        # configured produces a "not_configured" verdict — weaker than the
        # existing "pass".  The existing measured record must survive.
        from batch_gate import run_batch_gate
        run_batch_gate(project, runtime_dir)

        # The test expectation: the existing measured record is not replaced
        # by a weaker one.  Today's code writes unconditionally, so this fails.
        assert gate_path.read_bytes() == original, (
            "batch-gate.json was overwritten by a weaker result"
        )