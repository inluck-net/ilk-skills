"""Tests for metrics.py — cross-run KPI aggregator.

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

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_METRICS_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "metrics.py"
_COLLECT_SCRIPTS = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts"

# Import CLASSIFICATION_LABELS for assertions (avoid subprocess circular deps)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("_collect_mod", _COLLECT_SCRIPTS / "collect.py")
_collect_mod = _ilu.module_from_spec(_spec)
# Inject the ilk_paths import fallback so collect.py doesn't crash on import
_collect_scripts = str(_COLLECT_SCRIPTS)
if _collect_scripts not in sys.path:
    sys.path.insert(0, _collect_scripts)
_spec.loader.exec_module(_collect_mod)
CLASSIFICATION_LABELS = _collect_mod.CLASSIFICATION_LABELS

_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def _project_key(project_path: Path) -> str:
    abs_str = str(project_path.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


@pytest.fixture()
def scratch_env(tmp_path: Path):
    """Build an isolated ILK_DATA_HOME + temp project dir."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "my-proj"
    project_path.mkdir()

    env = {
        **os.environ,
        "ILK_DATA_HOME": str(data_home),
        "PYTHONIOENCODING": "utf-8",
    }
    key = _project_key(project_path)
    return project_path, env, key, data_home


def _write_jsonl(path: Path, records: list[dict], bom: bool = False):
    """Write records as JSONL, optionally with BOM."""
    content = "\n".join(json.dumps(r) for r in records) + "\n"
    if bom:
        with path.open("wb") as f:
            f.write(b"\xef\xbb\xbf")
            f.write(content.encode("utf-8"))
    else:
        path.write_text(content, encoding="utf-8")


# ── AC-1: --project --json prints valid JSON with classification_distribution ──


def test_ac1_json_output_has_classification_distribution(scratch_env):
    """--project --json must emit a classification_distribution map."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
        {"run_id": "run-1", "iteration": 2, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
        {"run_id": "run-2", "iteration": 1, "project": str(project_path),
         "exit_code": 1, "classification": "timeout-bound"},
    ]
    _write_jsonl(jsonl_path, records)

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    data = json.loads(result.stdout)
    assert "classification_distribution" in data, (
        f"Missing classification_distribution in output: {data.keys()}"
    )
    dist = data["classification_distribution"]
    assert isinstance(dist, dict), f"distribution should be dict, got {type(dist)}"

    # Every label from CLASSIFICATION_LABELS must be present
    for label in CLASSIFICATION_LABELS:
        assert label in dist, f"Missing label '{label}' in distribution"


# ── AC-2: synthetic fixture — exact count match ──────────────────────────────


def test_ac2_classification_distribution_counts_match(scratch_env):
    """Given a synthetic JSONL fixture, distribution counts must match exactly."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    # 3 runs: 2 clean-success, 1 timeout-bound
    # Each run has multiple iterations; the last iteration's label is used.
    records = [
        # Run 1: 3 iters, last is clean-success
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "timeout-bound"},
        {"run_id": "run-1", "iteration": 2, "project": str(project_path),
         "exit_code": 0, "classification": "timeout-bound"},
        {"run_id": "run-1", "iteration": 3, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
        # Run 2: 1 iter, clean-success
        {"run_id": "run-2", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
        # Run 3: 2 iters, last is timeout-bound
        {"run_id": "run-3", "iteration": 1, "project": str(project_path),
         "exit_code": 1, "classification": "timeout-bound"},
        {"run_id": "run-3", "iteration": 2, "project": str(project_path),
         "exit_code": 1, "classification": "timeout-bound"},
    ]
    _write_jsonl(jsonl_path, records)

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    data = json.loads(result.stdout)
    dist = data["classification_distribution"]

    # Expected: 2 clean-success, 1 timeout-bound, 0 for everything else
    assert dist["clean-success"] == 2, (
        f"Expected 2 clean-success, got {dist['clean-success']}"
    )
    assert dist["timeout-bound"] == 1, (
        f"Expected 1 timeout-bound, got {dist['timeout-bound']}"
    )

    # All other labels should be 0
    for label in CLASSIFICATION_LABELS:
        if label not in ("clean-success", "timeout-bound"):
            assert dist[label] == 0, (
                f"Expected 0 for '{label}', got {dist[label]}"
            )

    # Total runs should be 3
    assert data["total_runs"] == 3, (
        f"Expected total_runs=3, got {data['total_runs']}"
    )

    # Total iterations should be 6
    assert data["total_iterations"] == 6, (
        f"Expected total_iterations=6, got {data['total_iterations']}"
    )


# ── BOM tolerance ────────────────────────────────────────────────────────────


def test_bomd_jsonl_classifies(scratch_env):
    """BOM-prefixed JSONL must be parsed without errors."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
    ]
    _write_jsonl(jsonl_path, records, bom=True)

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    data = json.loads(result.stdout)
    assert data["classification_distribution"]["clean-success"] == 1


# ── Missing JSONL graceful handling ──────────────────────────────────────────


def test_missing_jsonl_exits_nonzero(scratch_env):
    """When no JSONL exists, exit with error."""
    project_path, env, key, data_home = scratch_env

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode != 0, "Should fail when no JSONL exists"


# ── No-classification records counted as no-evidence ─────────────────────────


def test_no_classification_counts_as_no_evidence(scratch_env):
    """Records without a classification field count as no-evidence."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0},
        # No classification field
    ]
    _write_jsonl(jsonl_path, records)

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert data["classification_distribution"]["no-evidence"] == 1, (
        f"Expected 1 no-evidence, got {data['classification_distribution']['no-evidence']}"
    )


# ── AC-3: time_to_ship_by_tier — null on missing data ────────────────────────


def test_time_to_ship_by_tier_null_on_missing_data(scratch_env):
    """time_to_ship_by_tier must be null when JSONL lacks verification_tier."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    # Records without verification_tier or started_at
    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "duration_sec": 120, "classification": "clean-success"},
        {"run_id": "run-1", "iteration": 2, "project": str(project_path),
         "exit_code": 0, "duration_sec": 90, "classification": "clean-success"},
    ]
    _write_jsonl(jsonl_path, records)

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    data = json.loads(result.stdout)
    assert data["time_to_ship_by_tier"] is None, (
        f"Expected null, got {data['time_to_ship_by_tier']}"
    )
    assert data["needs_instrumentation"]["time_to_ship_by_tier"] is True


# ── AC-3: blacklist_thrash_count — null on missing scheduler log ─────────────


def test_blacklist_thrash_count_null_on_missing_log(scratch_env):
    """blacklist_thrash_count must be null when no scheduler log exists."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
    ]
    _write_jsonl(jsonl_path, records)

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert data["blacklist_thrash_count"] is None, (
        f"Expected null, got {data['blacklist_thrash_count']}"
    )
    assert data["needs_instrumentation"]["blacklist_thrash_count"] is True


# ── AC-3: needs_instrumentation for human_touch_count + escaped_bug_rate ─────


def test_honest_null_kpis_present(scratch_env):
    """human_touch_count and escaped_bug_rate must be null with needs_instrumentation."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
    ]
    _write_jsonl(jsonl_path, records)

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert data["human_touch_count"] is None
    assert data["escaped_bug_rate"] is None
    assert data["needs_instrumentation"]["human_touch_count"] is True
    assert data["needs_instrumentation"]["escaped_bug_rate"] is True
