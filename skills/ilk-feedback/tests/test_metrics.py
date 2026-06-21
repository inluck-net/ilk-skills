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

import hashlib as _hashlib

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


# ── AC-5: Read-only boundary — input log file unchanged after run ─────────────


def test_readonly_boundary_input_log_unchanged(scratch_env):
    """The input JSONL file must be byte-identical after metrics.py runs.

    This mirrors test_readonly_boundary.py's pattern: metrics.py is pure
    read-only and must never mutate the log/postmortem trees.
    """
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

    # Snapshot hash before running metrics.py
    pre_hash = _hashlib.sha256(jsonl_path.read_bytes()).hexdigest()

    # Also snapshot all files under the data home
    pre_hashes: dict[Path, str] = {}
    for p in data_home.rglob("*"):
        if p.is_file():
            try:
                pre_hashes[p] = _hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                pass

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--json"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Verify JSONL file is byte-identical
    post_hash = _hashlib.sha256(jsonl_path.read_bytes()).hexdigest()
    assert post_hash == pre_hash, (
        "metrics.py mutated the input JSONL file — read-only boundary violated"
    )

    # Verify no files under data home were modified
    for p in data_home.rglob("*"):
        if p.is_file():
            try:
                post_hash_f = _hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                continue
            pre = pre_hashes.get(p)
            if pre is not None and post_hash_f != pre:
                pytest.fail(
                    f"metrics.py modified a file under ILK_DATA_HOME: {p}"
                )


# ── AC-6: --all aggregates across multiple projects ──────────────────────────


def test_all_aggregates_two_projects(tmp_path):
    """--all must aggregate records from multiple projects."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()

    env = {
        **os.environ,
        "ILK_DATA_HOME": str(data_home),
        "PYTHONIOENCODING": "utf-8",
    }

    # Create two fake projects with different classifications
    for i, (proj_name, label) in enumerate([("proj-a", "clean-success"), ("proj-b", "timeout-bound")]):
        proj_path = tmp_path / proj_name
        proj_path.mkdir()
        key = _project_key(proj_path)
        logs_dir = data_home / "projects" / key / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = logs_dir / ".ilk-loop.log"
        records = [
            {"run_id": f"run-{i}", "iteration": 1, "project": str(proj_path),
             "exit_code": 0, "classification": label},
        ]
        _write_jsonl(jsonl_path, records)

    result = subprocess.run(
        [sys.executable, str(_METRICS_PY), "--all", "--json",
         "--data-root", str(data_home)],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    data = json.loads(result.stdout)
    dist = data["classification_distribution"]
    assert dist["clean-success"] == 1, f"Expected 1 clean-success, got {dist['clean-success']}"
    assert dist["timeout-bound"] == 1, f"Expected 1 timeout-bound, got {dist['timeout-bound']}"
    assert data["total_runs"] == 2, f"Expected 2 total_runs, got {data['total_runs']}"
    assert data["total_iterations"] == 2, f"Expected 2 total_iterations, got {data['total_iterations']}"


# ── --text output ────────────────────────────────────────────────────────────


def test_text_output_contains_labels(scratch_env):
    """--text output must contain classification labels and KPI markers."""
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
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--text"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    output = result.stdout
    assert "clean-success" in output, f"--text missing clean-success:\n{output}"
    assert "needs_instrumentation" in output, f"--text missing needs_instrumentation:\n{output}"
    assert "=== ilk feedback metrics ===" in output, f"--text missing header:\n{output}"


# ── Comprehension debt proxy ─────────────────────────────────────────────────


def _make_git_log_runner(messages: list[str]):
    """Return an injectable git-log runner that yields *messages* verbatim."""
    def _runner(cwd: str, n: int = 100) -> list[str]:
        return messages[:n]
    return _runner


# ── AC-1: 7 loop-signed + 3 plain → ratio 0.7 ──────────────────────────────


def test_comprehension_debt_ac1_ratio(scratch_env):
    """7 loop-signed + 3 plain commits → loop_authored_ratio == 0.7."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
    ]
    _write_jsonl(jsonl_path, records)

    # Build synthetic commit messages: 7 with [plan: trailer, 3 plain
    loop_msgs = [
        f"feat(module): change {i}\n\n[plan:my-slug#step-{i}]"
        for i in range(7)
    ]
    plain_msgs = [
        f"fix(module): fix {i}"
        for i in range(3)
    ]
    all_msgs = loop_msgs + plain_msgs

    git_log_runner = _make_git_log_runner(all_msgs)

    # Call _compute_all_kpis directly with the injectable runner
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("_metrics", str(_METRICS_PY))
    metrics_mod = module_from_spec(spec)
    spec.loader.exec_module(metrics_mod)

    cd = metrics_mod.compute_comprehension_debt(project_path, git_log_runner)
    assert cd is not None, "comprehension_debt should not be None"
    assert cd["loop_authored_ratio"] == 0.7, (
        f"Expected 0.7, got {cd['loop_authored_ratio']}"
    )
    assert cd["loop_commits"] == 7, f"Expected 7 loop_commits, got {cd['loop_commits']}"
    assert cd["total_commits"] == 10, f"Expected 10 total_commits, got {cd['total_commits']}"


# ── AC-2: is_proxy + needs_signal present ────────────────────────────────────


def test_comprehension_debt_ac2_honest_proxy_labels(scratch_env):
    """Output must carry is_proxy: true and needs_signal note."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
    ]
    _write_jsonl(jsonl_path, records)

    git_log_runner = _make_git_log_runner([
        "feat(x): y\n\n[plan:a#step-0]",
        "fix(z): w",
    ])

    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("_metrics", str(_METRICS_PY))
    metrics_mod = module_from_spec(spec)
    spec.loader.exec_module(metrics_mod)

    cd = metrics_mod.compute_comprehension_debt(project_path, git_log_runner)
    assert cd is not None
    assert cd["is_proxy"] is True, f"Expected is_proxy=True, got {cd['is_proxy']}"
    assert cd["needs_signal"] == "explicit human-review record", (
        f"Expected needs_signal note, got {cd['needs_signal']}"
    )


# ── AC-3: empty/zero commits → ratio null, no divide-by-zero ────────────────


def test_comprehension_debt_ac3_empty_commits(scratch_env):
    """Zero commits → ratio null, total_commits 0, no crash."""
    project_path, env, key, data_home = scratch_env

    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"

    records = [
        {"run_id": "run-1", "iteration": 1, "project": str(project_path),
         "exit_code": 0, "classification": "clean-success"},
    ]
    _write_jsonl(jsonl_path, records)

    git_log_runner = _make_git_log_runner([])

    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("_metrics", str(_METRICS_PY))
    metrics_mod = module_from_spec(spec)
    spec.loader.exec_module(metrics_mod)

    cd = metrics_mod.compute_comprehension_debt(project_path, git_log_runner)
    assert cd is not None
    assert cd["loop_authored_ratio"] is None, (
        f"Expected None ratio for zero commits, got {cd['loop_authored_ratio']}"
    )
    assert cd["total_commits"] == 0, f"Expected 0 total, got {cd['total_commits']}"
    assert cd["loop_commits"] == 0, f"Expected 0 loop, got {cd['loop_commits']}"


# ── Comprehension debt in full JSON output ───────────────────────────────────


def test_comprehension_debt_in_json_output(scratch_env):
    """comprehension_debt block must appear in --json output."""
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
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    data = json.loads(result.stdout)
    assert "comprehension_debt" in data, (
        f"Missing comprehension_debt in output: {data.keys()}"
    )
    cd = data["comprehension_debt"]
    # With no real git history in the temp project, it may return None or
    # a dict with is_proxy. Either is acceptable for the integration check.
    if cd is not None:
        assert cd["is_proxy"] is True
        assert cd["needs_signal"] == "explicit human-review record"


# ── Comprehension debt in --text output ──────────────────────────────────────


def test_comprehension_debt_in_text_output(scratch_env):
    """--text output must mention comprehension debt."""
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
        [sys.executable, str(_METRICS_PY), "--project", str(project_path), "--text"],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"metrics.py exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    output = result.stdout
    assert "Comprehension debt" in output, (
        f"--text missing comprehension debt:\n{output}"
    )
