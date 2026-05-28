"""Tests for collect.py's _jsonl_log_candidates resolution order."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add the scripts dir so we can import collect
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    return tmp_path / "my-project"


def test_jsonl_log_field_first(project_path: Path) -> None:
    """When last-launch.json has jsonl_log, it should be the first candidate."""
    last_launch = {
        "jsonl_log": "/data/projects/key/logs/.ilk-loop.log",
        "log_file": "/data/projects/key/logs/launcher/key-run.log",
        "log_dir": "/data/projects/key/logs/runs/run-123",
    }
    candidates = collect._jsonl_log_candidates(project_path, last_launch)
    assert candidates[0] == Path("/data/projects/key/logs/.ilk-loop.log")


def test_jsonl_log_field_only(project_path: Path) -> None:
    """jsonl_log alone is sufficient — no need for log_file/log_dir."""
    last_launch = {"jsonl_log": "/custom/path/.ilk-loop.log"}
    candidates = collect._jsonl_log_candidates(project_path, last_launch)
    assert candidates[0] == Path("/custom/path/.ilk-loop.log")


def test_legacy_log_file_fallback(project_path: Path) -> None:
    """Older last-launch.json with log_file but no jsonl_log still works."""
    last_launch = {"log_file": "/old/path/launcher/key-run.log"}
    candidates = collect._jsonl_log_candidates(project_path, last_launch)
    # log_file is not a JSONL path, but it's included as a hint
    assert any("key-run.log" in str(c) for c in candidates)


def test_legacy_log_dir_fallback(project_path: Path) -> None:
    """Older last-launch.json with log_dir still works."""
    last_launch = {"log_dir": "/old/path/runs/run-456"}
    candidates = collect._jsonl_log_candidates(project_path, last_launch)
    assert Path("/old/path/runs/run-456/.ilk-loop.log") in candidates


def test_no_duplicates(project_path: Path) -> None:
    """Same path shouldn't appear twice even if it matches multiple fields."""
    last_launch = {
        "jsonl_log": "/same/path/.ilk-loop.log",
        "log_dir": "/same/path",
    }
    candidates = collect._jsonl_log_candidates(project_path, last_launch)
    paths = [str(c) for c in candidates]
    assert len(paths) == len(set(paths))


def test_none_last_launch(project_path: Path) -> None:
    """When last_launch is None, only external + legacy candidates appear."""
    candidates = collect._jsonl_log_candidates(project_path, None)
    assert len(candidates) >= 1
    # All should end with .ilk-loop.log
    assert all(c.name == ".ilk-loop.log" for c in candidates)


def test_empty_last_launch(project_path: Path) -> None:
    """Empty dict is treated like None."""
    candidates = collect._jsonl_log_candidates(project_path, {})
    assert len(candidates) >= 1
    assert all(c.name == ".ilk-loop.log" for c in candidates)
