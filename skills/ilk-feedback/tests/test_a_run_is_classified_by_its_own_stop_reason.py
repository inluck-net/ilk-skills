"""A run classified from its records maps its stop reason like the sentinel does.

Sub-plan a-run-is-classified-by-its-own-stop-reason, step 0 (red-first pins).

When the sentinel names a different run, classify() falls through the
sentinel branch and delegates to _classify_core, which reads `last_stop`
from the run_exit record.  _classify_core handles only 9 stop reasons;
local_checks_failed, local_checks_failed_no_commits, merge-deferred,
blocked-no-runnable and the other _SENTINEL_FAILURE_MAP states fall through
to clean-success or interrupted.  interrupt is a watchdog relaunch-class, so
a red gate classified from records gets relaunched instead of blocked.

AC-1..AC-3 test the fix; AC-4 is the control (sentinel matches).

HOME and ILK_DATA_HOME are both pinned to tmp_path (§23 half-pinned trap).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
for _p in (
    _HERE.parent.parent / "scripts",
    _HERE.parent.parent.parent / "ilk-loop" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import collect  # noqa: E402
from ilk_paths import external_launcher_dir, project_key  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_pinned_env(monkeypatch):
    """Undo _setup_project's HOME / ILK_DATA_HOME pin at teardown.

    Without this the pin outlived the test: every later test's subprocess
    ran with a tmp HOME, lost the user-site pytest, and 45 tests went red
    in the full suite (I2 verify, 2026-10-03).  Recording the current
    values here makes monkeypatch restore them, however they are set later.
    """
    monkeypatch.setenv("HOME", os.environ.get("HOME", ""))
    if "ILK_DATA_HOME" in os.environ:
        monkeypatch.setenv("ILK_DATA_HOME", os.environ["ILK_DATA_HOME"])
    else:
        monkeypatch.delenv("ILK_DATA_HOME", raising=False)


def _setup_project(tmp_path) -> Path:
    """Create a fake project with pinned HOME and ILK_DATA_HOME."""
    (tmp_path / "home").mkdir()
    (tmp_path / "ilk-data").mkdir()
    os.environ["HOME"] = str(tmp_path / "home")
    os.environ["ILK_DATA_HOME"] = str(tmp_path / "ilk-data")
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def _write_sentinel(project_path: Path, state: str, run_id: str) -> None:
    """Write a minimal sentinel naming the given run_id."""
    launcher_dir = external_launcher_dir(project_key(project_path))
    launcher_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "state": state,
        "pid": 0,
        "run_id": run_id,
        "iteration": 0,
    }
    (launcher_dir / "last-exit.json").write_text(json.dumps(sentinel))


def _make_iters_with_run_exit(
    run_id: str,
    stop_reason: str,
    checks: list[dict] | None = None,
) -> list[dict]:
    """Build iterations ending with a run_exit record carrying stop_reason.

    The runner writes local_checks as a list of check dicts.
    The run_exit record has iteration=999999 per Contract 2.
    """
    iters: list[dict] = []
    if checks:
        iters.append({
            "run_id": run_id,
            "iteration": 1,
            "exit_code": 0,
            "new_commits_total": 2,
            "duration_sec": 120,
            "local_checks": checks,
        })
    iters.append({
        "run_id": run_id,
        "iteration": 999999,
        "record_type": "run_exit",
        "stop_reason": stop_reason,
        "iters": 1,
    })
    return iters


# ── AC-1: run_exit local_checks_failed (rc 1 assertion) → local-checks-stuck ──


def test_run_exit_local_checks_failed_is_stuck(tmp_path):
    """A run whose run_exit says local_checks_failed (rc 1 assertion),
    with the sentinel naming a different run, must classify as
    local-checks-stuck — never interrupted."""
    project = _setup_project(tmp_path)
    # Sentinel names a DIFFERENT run — classify must fall through to records.
    _write_sentinel(project, "running", "20990101-000000")

    red_check = {
        "slug": "my-slug",
        "step": 1,
        "outcome": "fail",
        "exit_code": 1,
        "command": "pytest tests/",
        "stderr_tail": "AssertionError: expected 3, got 0",
    }
    iters = _make_iters_with_run_exit(
        "20261002-123656", "local_checks_failed", checks=[red_check]
    )

    label, facts = collect.classify(iters, None, project)
    assert label == "local-checks-stuck", (
        f"run_exit local_checks_failed (rc 1) must be local-checks-stuck, "
        f"got {label}: {facts}"
    )


# ── AC-2: run_exit local_checks_failed (rc 127) → local-checks-broken ────────


def test_run_exit_local_checks_failed_rc127_is_broken(tmp_path):
    """A run whose run_exit says local_checks_failed (rc 127 command not found),
    with the sentinel naming a different run, must classify as
    local-checks-broken — never interrupted."""
    project = _setup_project(tmp_path)
    _write_sentinel(project, "running", "20990101-000000")

    broken_check = {
        "slug": "my-slug",
        "step": 1,
        "outcome": "fail",
        "exit_code": 127,
        "command": "bunx vitest run",
        "stderr_tail": "bunx: command not found",
    }
    iters = _make_iters_with_run_exit(
        "20261002-123656", "local_checks_failed", checks=[broken_check]
    )

    label, facts = collect.classify(iters, None, project)
    assert label == "local-checks-broken", (
        f"run_exit local_checks_failed (rc 127) must be local-checks-broken, "
        f"got {label}: {facts}"
    )


# ── AC-3: run_exit merge-deferred → same label as sentinel path ──────────────


def test_run_exit_merge_deferred_classifies_correctly(tmp_path):
    """A run whose run_exit says merge-deferred, with the sentinel naming a
    different run, must classify as merge-deferred — not clean-success."""
    project = _setup_project(tmp_path)
    _write_sentinel(project, "running", "20990101-000000")

    iters = _make_iters_with_run_exit("20261002-123656", "merge-deferred")

    label, facts = collect.classify(iters, None, project)
    assert label == "merge-deferred", (
        f"run_exit merge-deferred must classify as merge-deferred, "
        f"got {label}: {facts}"
    )


# ── AC-4 (control): sentinel matching the run → unchanged behaviour ──────────


def test_sentinel_matching_run_still_works(tmp_path):
    """Control: when the sentinel matches the run, the sentinel branch fires
    and classifies correctly.  This must not regress."""
    project = _setup_project(tmp_path)
    _write_sentinel(project, "local_checks_failed", "20261002-123656")

    red_check = {
        "slug": "my-slug",
        "step": 1,
        "outcome": "fail",
        "exit_code": 1,
        "command": "pytest tests/",
        "stderr_tail": "AssertionError: expected 3, got 0",
    }
    iters = _make_iters_with_run_exit(
        "20261002-123656", "local_checks_failed", checks=[red_check]
    )

    label, facts = collect.classify(iters, None, project)
    assert label == "local-checks-stuck", (
        f"sentinel-matching run_exit local_checks_failed must be "
        f"local-checks-stuck, got {label}: {facts}"
    )