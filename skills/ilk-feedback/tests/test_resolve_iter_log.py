"""Tests for resolve_iter_log path resolution.

Pins the defect (e8c2668a923459b4): resolve_iter_log only searches the legacy
``ilk-claude-<run_id>/iter-NN.log`` layout, missing the current
``runs/<run_id>/iter-NN.log`` layout the runner actually writes.

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import pytest

# Repo root and skill-root for imports.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SKILL_ROOT = _REPO_ROOT / "skills" / "ilk-loop" / "scripts"

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
