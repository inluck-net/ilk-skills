"""I3: a worker cannot write a proof or run the suite.

With ``ILK_WORKER_SESSION=1``: ``batch_gate.py`` refuses to run or write
its record, ``verification_record.py --run-suite`` refuses,
``verify_attribution.py`` refuses, ``suite_ledger.py`` refuses to measure.
Each with no file written.

Rail: ``batch_gate._refuse_in_worker_session``; ``verification_record.py:1638``;
``verify_attribution.py:479, :880``; ``suite_ledger.py:207``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"

pytestmark = pytest.mark.timeout(120)


def _run(script: str, argv: list[str], root: Path) -> subprocess.CompletedProcess:
    """Run a script with ILK_WORKER_SESSION=1 and a minimal project."""
    env = {
        **os.environ,
        "ILK_WORKER_SESSION": "1",
        "ILK_DATA_HOME": str(root / "ilk-data"),
        "HOME": str(root),
    }
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / script), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env, cwd=str(root),
    )


def _make_minimal_project(root: Path) -> Path:
    """Create a minimal git project for the scripts to operate on."""
    project = root / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(project)],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=str(project), check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return project


def test_batch_gate_refuses_in_worker_session(tmp_path: Path) -> None:
    project = _make_minimal_project(tmp_path)
    r = _run(
        "batch_gate.py",
        ["--project", str(project), "--run"],
        tmp_path,
    )
    # batch_gate raises WorkerSessionRefused → non-zero exit.
    assert r.returncode != 0, (
        f"batch_gate.py should refuse in a worker session, got exit {r.returncode}.\n"
        f"stdout={r.stdout[-300:]}\nstderr={r.stderr[-300:]}"
    )
    combined = r.stdout + r.stderr
    assert "refused" in combined.lower() or "worker" in combined.lower(), (
        f"batch_gate.py refusal should mention 'refused' or 'worker'.\n"
        f"stdout={r.stdout[-300:]}\nstderr={r.stderr[-300:]}"
    )


def test_verification_record_refuses_in_worker_session(tmp_path: Path) -> None:
    project = _make_minimal_project(tmp_path)
    # Get HEAD sha for --base-sha (required by --run-suite).
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(project), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()
    r = _run(
        "verification_record.py",
        ["--project", str(project), "--batch", "test-batch",
         "--base-sha", sha, "--run-suite"],
        tmp_path,
    )
    assert r.returncode != 0, (
        f"verification_record.py --run-suite should refuse in a worker session, "
        f"got exit {r.returncode}.\n"
        f"stdout={r.stdout[-300:]}\nstderr={r.stderr[-300:]}"
    )
    combined = r.stdout + r.stderr
    assert "refused" in combined.lower() or "worker" in combined.lower(), (
        f"verification_record.py refusal should mention 'refused' or 'worker'.\n"
        f"stdout={r.stdout[-300:]}\nstderr={r.stderr[-300:]}"
    )


def test_verify_attribution_refuses_in_worker_session(tmp_path: Path) -> None:
    project = _make_minimal_project(tmp_path)
    r = _run(
        "verify_attribution.py",
        ["--project", str(project), "--is-stale"],
        tmp_path,
    )
    # verify_attribution returns (False, "refused in a worker session") → exit 1
    assert r.returncode != 0, (
        f"verify_attribution.py should refuse in a worker session, "
        f"got exit {r.returncode}.\n"
        f"stdout={r.stdout[-300:]}\nstderr={r.stderr[-300:]}"
    )


def test_suite_ledger_refuses_in_worker_session(tmp_path: Path) -> None:
    project = _make_minimal_project(tmp_path)
    # suite_ledger needs a sha; use HEAD.
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(project), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()
    r = _run(
        "suite_ledger.py",
        ["measure", "--project", str(project), "--sha", sha],
        tmp_path,
    )
    assert r.returncode != 0, (
        f"suite_ledger.py measure should refuse in a worker session, "
        f"got exit {r.returncode}.\n"
        f"stdout={r.stdout[-300:]}\nstderr={r.stderr[-300:]}"
    )
    combined = r.stdout + r.stderr
    assert "refused" in combined.lower() or "worker" in combined.lower(), (
        f"suite_ledger.py refusal should mention 'refused' or 'worker'.\n"
        f"stdout={r.stdout[-300:]}\nstderr={r.stderr[-300:]}"
    )