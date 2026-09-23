"""Scope override — AC-1 through AC-4.

Pins the contract that a plan can demand a full suite via `--scope full`,
overriding `compute_suite_scope`'s auto-detection.

AC-1  `--scope full` runs the whole suite even when compute_suite_scope
      returns scoped, and the record reads `suite_scope: full`.
AC-2  `--scope auto` (and the flag omitted) reproduces today's behaviour.
AC-3  `--scope full` records a `suite_total` equal to the unscoped collection
      count, not the selection's.
AC-4  An unrecognised `--scope` value is refused with a message naming the
      accepted values.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

LOOP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(LOOP_SCRIPTS))

from verification_record import main as vr_main  # type: ignore[import-untyped]  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), *args],
        text=True, stderr=subprocess.DEVNULL,
    ).strip()


def _init_project(tmp_path: Path) -> Path:
    """Create a throwaway project with a .ilk-launch.json suite config."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "test@test.local")
    _git(project, "config", "user.name", "Test")
    # Seed commit so base_sha != HEAD.
    (project / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(project, "add", "seed.txt")
    _git(project, "commit", "-m", "seed")
    base_sha = _git(project, "rev-parse", "HEAD")
    # Second commit so there's a diff.
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(project, "add", "app.py")
    _git(project, "commit", "-m", "add app")
    # Write suite config.
    launch = {
        "ship": {
            "suite": {
                "command": "echo fake-pytest",
            },
        },
    }
    (project / ".ilk-launch.json").write_text(
        json.dumps(launch, indent=2) + "\n", encoding="utf-8",
    )
    return project


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create a throwaway project with pinned HOME."""
    p = _init_project(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(home / ".ilk-data"))
    return p


# Mock run_suite results (must include "counts" key for render_record).
_SCOPED_RESULT = {
    "exit_code": 0,
    "counts": {
        "passed": 100, "failed": 0, "errors": 0,
        "skipped": 0, "xfailed": 0, "xpassed": 0, "total": 100,
    },
    "failing_nodes": [],
}
_FULL_RESULT = {
    "exit_code": 0,
    "counts": {
        "passed": 3000, "failed": 0, "errors": 0,
        "skipped": 200, "xfailed": 0, "xpassed": 0, "total": 3200,
    },
    "failing_nodes": [],
}


def _scoped_scope(*_args, **_kwargs):
    return {"mode": "scoped", "count": 14, "reason": "test", "selection": ["a.py"]}


def _run_suite_scoped(project, invocation, timeout, selection=None):
    """Mock run_suite: returns full count when selection is None, scoped otherwise."""
    if selection is None:
        return dict(_FULL_RESULT)
    return dict(_SCOPED_RESULT)


# ── AC-1: --scope full overrides compute_suite_scope ────────────────────────

def test_ac1_scope_full_overrides_scoped(project, monkeypatch: pytest.MonkeyPatch):
    """AC-1: --scope full runs the whole suite even when compute_suite_scope
    returns scoped, and the record reads suite_scope: full."""
    record_path = project / "record.md"
    # Mock heavy deps.
    monkeypatch.setattr(
        "verification_record.compute_suite_scope", _scoped_scope,
    )
    monkeypatch.setattr(
        "verification_record.run_suite", _run_suite_scoped,
    )
    monkeypatch.setattr(
        "verification_record.run_at_base", lambda *a, **kw: {},
    )
    monkeypatch.setattr(
        "verification_record.read_baseline_red", lambda *a, **kw: {},
    )
    base_sha = _git(project, "rev-parse", "HEAD~1")
    rc = vr_main([
        "--project", str(project),
        "--record", str(record_path),
        "--run-suite",
        "--base-sha", base_sha,
        "--scope", "full",
    ])
    assert rc == 0, f"expected exit 0, got {rc}"
    text = record_path.read_text(encoding="utf-8")
    assert "suite_scope: full" in text, (
        f"expected suite_scope: full in record, got: {text}"
    )


# ── AC-2: --scope auto reproduces today's behaviour ─────────────────────────

def test_ac2_scope_auto_uses_computed(project, monkeypatch: pytest.MonkeyPatch):
    """AC-2: --scope auto (and the flag omitted) reproduces today's behaviour."""
    record_path = project / "record.md"
    monkeypatch.setattr(
        "verification_record.compute_suite_scope", _scoped_scope,
    )
    monkeypatch.setattr(
        "verification_record.run_suite", _run_suite_scoped,
    )
    monkeypatch.setattr(
        "verification_record.run_at_base", lambda *a, **kw: {},
    )
    monkeypatch.setattr(
        "verification_record.read_baseline_red", lambda *a, **kw: {},
    )
    base_sha = _git(project, "rev-parse", "HEAD~1")
    # --scope auto should behave like today: scoped scope → scoped suite.
    rc = vr_main([
        "--project", str(project),
        "--record", str(record_path),
        "--run-suite",
        "--base-sha", base_sha,
        "--scope", "auto",
    ])
    assert rc == 0
    text = record_path.read_text(encoding="utf-8")
    assert "suite_scope: scoped" in text, (
        f"expected suite_scope: scoped with --scope auto, got: {text}"
    )


def test_ac2_scope_omitted_uses_computed(project, monkeypatch: pytest.MonkeyPatch):
    """AC-2: flag omitted also uses computed scope."""
    record_path = project / "record.md"
    monkeypatch.setattr(
        "verification_record.compute_suite_scope", _scoped_scope,
    )
    monkeypatch.setattr(
        "verification_record.run_suite", _run_suite_scoped,
    )
    monkeypatch.setattr(
        "verification_record.run_at_base", lambda *a, **kw: {},
    )
    monkeypatch.setattr(
        "verification_record.read_baseline_red", lambda *a, **kw: {},
    )
    base_sha = _git(project, "rev-parse", "HEAD~1")
    rc = vr_main([
        "--project", str(project),
        "--record", str(record_path),
        "--run-suite",
        "--base-sha", base_sha,
    ])
    assert rc == 0
    text = record_path.read_text(encoding="utf-8")
    assert "suite_scope: scoped" in text, (
        f"expected suite_scope: scoped with no --scope flag, got: {text}"
    )


# ── AC-3: --scope full records full suite_total ─────────────────────────────

def test_ac3_scope_full_records_full_total(project, monkeypatch: pytest.MonkeyPatch):
    """AC-3: --scope full records a suite_total equal to the unscoped
    collection count, not the selection's."""
    record_path = project / "record.md"
    monkeypatch.setattr(
        "verification_record.compute_suite_scope", _scoped_scope,
    )
    monkeypatch.setattr(
        "verification_record.run_suite", _run_suite_scoped,
    )
    monkeypatch.setattr(
        "verification_record.run_at_base", lambda *a, **kw: {},
    )
    monkeypatch.setattr(
        "verification_record.read_baseline_red", lambda *a, **kw: {},
    )
    base_sha = _git(project, "rev-parse", "HEAD~1")
    rc = vr_main([
        "--project", str(project),
        "--record", str(record_path),
        "--run-suite",
        "--base-sha", base_sha,
        "--scope", "full",
    ])
    assert rc == 0
    text = record_path.read_text(encoding="utf-8")
    # The full suite returns 3200 tests, not the scoped 100.
    assert "3200" in text, (
        f"expected full suite total (3200) in record, got: {text}"
    )


# ── AC-4: unrecognised --scope is refused ────────────────────────────────────

def test_ac4_unrecognised_scope_refused(project, monkeypatch: pytest.MonkeyPatch):
    """AC-4: an unrecognised --scope value is refused with a message naming
    the accepted values."""
    record_path = project / "record.md"
    base_sha = _git(project, "rev-parse", "HEAD~1")
    # argparse calls sys.exit(2) for invalid choices; catch it and check the
    # error message.
    import io
    captured = io.StringIO()
    monkeypatch.setattr("sys.stderr", captured)
    with pytest.raises(SystemExit) as exc_info:
        vr_main([
            "--project", str(project),
            "--record", str(record_path),
            "--run-suite",
            "--base-sha", base_sha,
            "--scope", "bogus",
        ])
    assert exc_info.value.code == 2, f"expected exit code 2, got {exc_info.value.code}"
    err = captured.getvalue()
    assert "full" in err and "auto" in err, (
        f"expected error to name accepted values (full, auto), got: {err}"
    )