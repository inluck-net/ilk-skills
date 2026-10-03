"""Red-first pins: a worker cannot write the batch-gate record.

Part of `a-worker-cannot-write-the-batch-gate-record` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1 removes.

Drives ``batch_gate.py`` and ``verify_attribution.py`` in a temp git repo.
HOME and ILK_DATA_HOME are pinned together so nothing reads the real
``~/.ilk-data``.

AC-1: with ILK_WORKER_SESSION=1, ``python3 batch_gate.py --project <tmp repo>
      --run`` exits 1, stderr carries the ``ILK-CHECK: unmeasured refused``
      marker, the fake suite never ran, and no record file exists.
AC-2: with ILK_WORKER_SESSION=1, ``write_record(record, runtime_dir, batch="b")``
      raises ``WorkerSessionRefused``, which is a ``PermissionError`` and an
      ``OSError``, and writes nothing under ``runtime_dir``.
AC-3: with ILK_WORKER_SESSION=1, ``verify_attribution.write_gate_record``
      returns ``(False, <detail naming the worker session>)`` and writes no
      record.
AC-4 (control): with the variable unset, ``write_record`` writes both files
      as today.
AC-5: inside a test, ``os.environ.get("ILK_WORKER_SESSION")`` is ``None``
      even when pytest was started with ``ILK_WORKER_SESSION=1``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import batch_gate  # noqa: E402
import verify_attribution as vat  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal temp git repo with one commit."""
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _runtime_dir(tmp_path: Path) -> Path:
    """Build a fake runtime dir under tmp_path."""
    rt = tmp_path / "runtime"
    rt.mkdir()
    return rt


def _make_record() -> batch_gate.BatchGateRecord:
    return batch_gate.BatchGateRecord(
        verdict="pass",
        head_sha="a" * 40,
        invocation="echo ok",
        timestamp="2026-10-03T12:00:00+08:00",
    )


# ── AC-1: batch_gate.py --run refuses in a worker session ───────────────────


@pytest.mark.xfail(strict=True, reason="batch_gate.py has no ILK_WORKER_SESSION guard yet")
def test_batch_gate_run_refuses_in_worker_session(tmp_path: Path, monkeypatch) -> None:
    """With ILK_WORKER_SESSION=1, batch_gate.py --run exits 1 and writes nothing."""
    repo = _make_repo(tmp_path)
    data_home = tmp_path / ".ilk-data"
    env = {
        **os.environ,
        "ILK_WORKER_SESSION": "1",
        "ILK_DATA_HOME": str(data_home),
        "HOME": str(tmp_path),
    }
    env.pop("ILK_DATA_DIR", None)

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "batch_gate.py"),
         "--project", str(repo), "--run"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "ILK-CHECK: unmeasured refused in a worker session" in proc.stderr, proc.stderr

    # No record file should exist.
    if data_home.exists():
        for p in data_home.rglob("batch-gate*.json"):
            assert False, f"unexpected record written: {p}"


# ── AC-2: write_record raises WorkerSessionRefused ──────────────────────────


@pytest.mark.xfail(strict=True, reason="batch_gate.write_record has no ILK_WORKER_SESSION guard yet")
def test_write_record_refuses_in_worker_session(tmp_path: Path, monkeypatch) -> None:
    """write_record raises WorkerSessionRefused inside a worker session."""
    rt = _runtime_dir(tmp_path)
    rec = _make_record()
    monkeypatch.setenv("ILK_WORKER_SESSION", "1")

    with pytest.raises(batch_gate.WorkerSessionRefused) as exc_info:
        batch_gate.write_record(rec, rt, batch="b")

    # Must be a PermissionError and an OSError.
    assert isinstance(exc_info.value, PermissionError)
    assert isinstance(exc_info.value, OSError)

    # Nothing written.
    assert list(rt.rglob("*.json")) == [], "record must not be written"


# ── AC-3: verify_attribution.write_gate_record refuses ──────────────────────


@pytest.mark.xfail(strict=True, reason="batch_gate has no WorkerSessionRefused yet")
def test_verify_attribution_refuses_in_worker_session(tmp_path: Path, monkeypatch) -> None:
    """write_gate_record returns (False, detail) in a worker session."""
    repo = _make_repo(tmp_path)
    data_home = tmp_path / ".ilk-data"
    monkeypatch.setenv("ILK_WORKER_SESSION", "1")
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)

    ok, detail = vat.write_gate_record(repo, excused=0)

    assert ok is False, "must not write"
    assert "worker session" in detail.lower(), f"detail should name the cause: {detail}"


# ── AC-4 (control): write_record works normally without the variable ─────────


def test_write_record_works_without_worker_session(tmp_path: Path, monkeypatch) -> None:
    """Control: write_record writes both batch and legacy records."""
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    rt = _runtime_dir(tmp_path)
    rec = _make_record()

    batch_gate.write_record(rec, rt, batch="test-batch")

    legacy = rt / "batch-gate.json"
    per_batch = rt / "batch-gates" / "test-batch.json"
    assert legacy.exists(), "legacy record missing"
    assert per_batch.exists(), "per-batch record missing"
    data = json.loads(per_batch.read_text(encoding="utf-8"))
    assert data["verdict"] == "pass"


# ── AC-5: conftest strips ILK_WORKER_SESSION ────────────────────────────────


@pytest.mark.xfail(strict=True, reason="conftest does not strip ILK_WORKER_SESSION yet")
def test_conftest_strips_worker_session(monkeypatch) -> None:
    """Inside a test, ILK_WORKER_SESSION must be None even when set externally.

    The conftest autouse fixture should strip it, as it already strips
    ILK_SHIPPED_MARKER.
    """
    # The autouse fixture already ran; the variable should be absent.
    assert os.environ.get("ILK_WORKER_SESSION") is None