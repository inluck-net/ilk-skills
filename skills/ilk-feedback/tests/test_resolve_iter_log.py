"""Tests for resolve_iter_log path resolution.

Pins the defect (e8c2668a923459b4): resolve_iter_log only searches the legacy
``ilk-claude-<run_id>/iter-NN.log`` layout, missing the current
``runs/<run_id>/iter-NN.log`` layout the runner actually writes.

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root and skill-root for imports.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SKILL_ROOT = _REPO_ROOT / "skills" / "ilk-loop" / "scripts"
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"

# Same regex as ilk_paths.project_key — duplicated here to avoid import gymnastics.
_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def _project_key(project_path: Path) -> str:
    """Replicate ilk_paths.project_key logic (pure, no subprocess)."""
    abs_str = str(project_path.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


@pytest.fixture(autouse=True)
def _isolated_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Redirect ILK_DATA_HOME to tmp_path so nothing touches real ~/.ilk-data."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    # Ensure ilk_paths is importable.
    if str(_SKILL_ROOT) not in sys.path:
        sys.path.insert(0, str(_SKILL_ROOT))
    yield
    # Clean up sys.path so other test files are not affected.
    try:
        sys.path.remove(str(_SKILL_ROOT))
    except ValueError:
        pass


@pytest.fixture()
def project_path(tmp_path: Path) -> Path:
    """A temp project directory for computing the project key."""
    p = tmp_path / "my-proj"
    p.mkdir()
    return p


def _logs_dir_for(project_path: Path, data_home: Path | None = None) -> Path:
    """Return the external logs dir for project_path under ILK_DATA_HOME."""
    if data_home is None:
        data_home = Path(os.environ["ILK_DATA_HOME"])
    key = _project_key(project_path)
    return data_home / "projects" / key / "logs"


def _launcher_dir_for(project_path: Path, data_home: Path | None = None) -> Path:
    """Return the external launcher dir for project_path under ILK_DATA_HOME."""
    if data_home is None:
        data_home = Path(os.environ["ILK_DATA_HOME"])
    key = _project_key(project_path)
    return data_home / "projects" / key / "runtime" / "launcher"


def _write_summary_jsonl(
    logs_dir: Path, project_path: Path, run_id: str, iteration: int
) -> None:
    """Write a minimal JSONL summary record (no ``log`` key)."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"
    record = {
        "project": str(project_path),
        "run_id": run_id,
        "iteration": iteration,
        "exit_code": 0,
        "new_commits_total": 1,
        "stop_reason": "already-shipped",
        "duration_sec": 30,
    }
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ── AC-1: resolve_iter_log finds the current runs/ layout ──────────────────


def test_resolve_iter_log_finds_runs_layout(
    monkeypatch: pytest.MonkeyPatch, project_path: Path
):
    """resolve_iter_log must find ``logs/runs/<run_id>/iter-NN.log``.

    The runner writes iteration logs under this layout.
    """
    # Import AFTER monkeypatch sets ILK_DATA_HOME.
    from collect import resolve_iter_log

    run_id = "20260811-152855"
    iteration = 5
    marker = "EXPECTED_TAIL_TEXT_iter05"

    # Create the file at the path the runner actually writes.
    logs_dir = _logs_dir_for(project_path)
    iter_log = logs_dir / "runs" / run_id / f"iter-{iteration:02d}.log"
    iter_log.parent.mkdir(parents=True, exist_ok=True)
    iter_log.write_text(marker, encoding="utf-8")

    result = resolve_iter_log(run_id, iteration, project_path)
    assert result is not None, (
        f"resolve_iter_log returned None for {iter_log} — "
        "the runs/ layout is not searched"
    )
    assert result == iter_log
    assert marker in result.read_text(encoding="utf-8")


# ── AC-2: legacy layout still works (regression guard) ────────────────────


def test_resolve_iter_log_finds_legacy_layout(
    monkeypatch: pytest.MonkeyPatch, project_path: Path
):
    """resolve_iter_log must still find the legacy
    ``ilk-claude-<run_id>/iter-NN.log`` layout.

    This passes today — it is the guard that ensures the fix in step 1
    does not break old runs.
    """
    from collect import resolve_iter_log

    run_id = "20260811-152855"
    iteration = 5
    marker = "LEGACY_TAIL_TEXT"

    # Create the file at the legacy path.
    logs_dir = _logs_dir_for(project_path)
    iter_log = logs_dir / f"ilk-claude-{run_id}" / f"iter-{iteration:02d}.log"
    iter_log.parent.mkdir(parents=True, exist_ok=True)
    iter_log.write_text(marker, encoding="utf-8")

    result = resolve_iter_log(run_id, iteration, project_path)
    assert result is not None, (
        "resolve_iter_log returned None even though the legacy path exists"
    )
    assert result == iter_log
    assert marker in result.read_text(encoding="utf-8")


# ── AC-3: when both layouts exist, current (runs/) wins ────────────────────


def test_resolve_iter_log_current_wins_over_legacy(
    monkeypatch: pytest.MonkeyPatch, project_path: Path
):
    """When both ``runs/`` and ``ilk-claude-`` layouts exist for the same
    run_id + iteration, the current layout must win."""
    from collect import resolve_iter_log

    run_id = "20260811-152855"
    iteration = 5

    logs_dir = _logs_dir_for(project_path)

    # Create both layouts with different markers.
    current_log = logs_dir / "runs" / run_id / f"iter-{iteration:02d}.log"
    current_log.parent.mkdir(parents=True, exist_ok=True)
    current_log.write_text("CURRENT_MARKER", encoding="utf-8")

    legacy_log = logs_dir / f"ilk-claude-{run_id}" / f"iter-{iteration:02d}.log"
    legacy_log.parent.mkdir(parents=True, exist_ok=True)
    legacy_log.write_text("LEGACY_MARKER", encoding="utf-8")

    result = resolve_iter_log(run_id, iteration, project_path)
    assert result is not None
    assert result == current_log, (
        f"Expected current layout {current_log}, got {result}"
    )


# ── AC-4 / AC-5: end-to-end postmortem tail ───────────────────────────────


def test_e2e_postmortem_gains_tail_from_runs_layout(project_path: Path):
    """A rendered postmortem for a run with an on-disk iteration log under
    ``runs/`` must contain the tail text and name the resolved log path
    in the section header.  (AC-4)"""
    env = {
        **os.environ,
        "ILK_DATA_HOME": os.environ["ILK_DATA_HOME"],
        "PYTHONIOENCODING": "utf-8",
    }
    data_home = Path(env["ILK_DATA_HOME"])
    logs_dir = _logs_dir_for(project_path, data_home)
    run_id = "20260811-152855"
    iteration = 5
    marker = "UNIQUE_TAIL_MARKER_4821"

    # Write the JSONL summary record (no `log` key — forces resolve_iter_log).
    _write_summary_jsonl(logs_dir, project_path, run_id, iteration)

    # Write the iteration log at the current layout.
    iter_log = logs_dir / "runs" / run_id / f"iter-{iteration:02d}.log"
    iter_log.parent.mkdir(parents=True, exist_ok=True)
    iter_log.write_text(f"line1\n{marker}\nline3\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            run_id,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Find the generated postmortem.
    pm_dir = _launcher_dir_for(project_path, data_home) / "postmortems"
    pm_path = pm_dir / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert marker in text, (
        f"Postmortem should contain tail marker '{marker}'.\nHead:\n{text[:600]}"
    )
    assert "<no tail available>" not in text, (
        f"Postmortem should NOT say '<no tail available>'.\nHead:\n{text[:600]}"
    )
    # Section header must name the resolved log path.
    assert str(iter_log) in text, (
        f"Postmortem header should name the log path {iter_log}.\nHead:\n{text[:600]}"
    )


def test_e2e_postmortem_no_log_says_no_tail_available(project_path: Path):
    """When genuinely no iteration log exists, the postmortem must still
    emit ``<no tail available>`` — the fix must not fabricate a tail.  (AC-5)"""
    env = {
        **os.environ,
        "ILK_DATA_HOME": os.environ["ILK_DATA_HOME"],
        "PYTHONIOENCODING": "utf-8",
    }
    data_home = Path(env["ILK_DATA_HOME"])
    logs_dir = _logs_dir_for(project_path, data_home)
    run_id = "20260812-095202"
    iteration = 3

    # Write the JSONL summary record but NO iteration log on disk.
    _write_summary_jsonl(logs_dir, project_path, run_id, iteration)

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            run_id,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    pm_dir = _launcher_dir_for(project_path, data_home) / "postmortems"
    pm_path = pm_dir / f"{run_id}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert "<no tail available>" in text, (
        f"Postmortem should say '<no tail available>' when no log exists.\n"
        f"Head:\n{text[:600]}"
    )
