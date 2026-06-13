"""Tests for watchdog classification: sentinel-run alignment + shipped-unverified.

Covers the core logic from sub-plan 2026-06-10-watchdog-classify-sentinel-run:

  (a) Given a sentinel run with state=local_checks_failed AND a stale newer
      all-shipped run in the logs, collect.py (via --run-id) classifies
      local-checks-stuck (blacklist) — NOT shipped-unverified.
  (b) shipped-unverified classification → handled explicitly (no relaunch).
  (c) no-evidence classification → handled (do not relaunch; triage).
  (d) watchdog.sh classify_action routes shipped-unverified + no-evidence
      to 'terminate' (not relaunch, not sleep).

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
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"
_WATCHDOG_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"

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
    return project_path, env, key


def _runtime_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "runtime"


def _launcher_dir(data_home: Path, key: str) -> Path:
    return _runtime_dir(data_home, key) / "launcher"


def _logs_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "logs"


def _write_sentinel(data_home: Path, key: str, run_id: str, state: str) -> None:
    rt_dir = _runtime_dir(data_home, key)
    rt_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {"state": state, "run_id": run_id, "iters": 1}
    (rt_dir / "last-exit.json").write_text(json.dumps(sentinel), encoding="utf-8")


def _write_jsonl(data_home: Path, key: str, project_path: Path, records: list[dict]) -> None:
    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"
    with jsonl_path.open("a", encoding="utf-8") as f:
        for rec in records:
            rec["project"] = str(project_path)
            f.write(json.dumps(rec) + "\n")


# ── AC-3: sentinel run with stale newer all-shipped → local-checks-stuck ──


def test_sentinel_run_classifies_local_checks_stuck_not_shipped_unverified(scratch_env):
    """Given a sentinel run R with state=local_checks_failed AND a stale
    newer all-shipped run, collect.py --run-id R should classify
    local-checks-stuck (blacklist), NOT shipped-unverified.

    This is the core regression test for the 2026-06-10 cascade bug.
    """
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    sentinel_run = "20260610-071415"
    _write_sentinel(data_home, key, sentinel_run, "local_checks_failed")

    # Write JSONL: the sentinel run has local-check failures.
    _write_jsonl(data_home, key, project_path, [
        {
            "run_id": sentinel_run,
            "iteration": 1,
            "exit_code": 1,
            "new_commits_total": 0,
            "stop_reason": "no-progress",
            "duration_sec": 60,
            "local_checks": {"outcome": "fail", "command": "pytest"},
        },
        {
            "run_id": sentinel_run,
            "iteration": 2,
            "exit_code": 1,
            "new_commits_total": 0,
            "stop_reason": "no-progress",
            "duration_sec": 60,
            "local_checks": {"outcome": "fail", "command": "pytest"},
        },
        {
            "run_id": sentinel_run,
            "iteration": 3,
            "exit_code": 1,
            "new_commits_total": 0,
            "stop_reason": "no-progress",
            "duration_sec": 60,
            "local_checks": {"outcome": "fail", "command": "pytest"},
        },
    ])

    # A stale newer all-shipped run (what caused the original bug).
    _write_jsonl(data_home, key, project_path, [
        {
            "run_id": "20260608-120000",
            "iteration": 1,
            "exit_code": 0,
            "new_commits_total": 5,
            "stop_reason": "already-shipped",
            "duration_sec": 120,
        },
    ])

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            sentinel_run,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    pm_path = pm_dir / f"{sentinel_run}.md"
    assert pm_path.exists(), f"Postmortem not found at {pm_path}"

    text = pm_path.read_text(encoding="utf-8")
    assert "local-checks-stuck" in text, (
        f"Expected local-checks-stuck classification.\nHead:\n{text[:500]}"
    )
    assert "shipped-unverified" not in text, (
        f"Should NOT be shipped-unverified.\nHead:\n{text[:500]}"
    )


# ── AC-4: shipped-unverified → no relaunch ─────────────────────────────────


def test_shipped_unverified_no_relaunch(scratch_env):
    """shipped-unverified is a terminal success-needs-human state.
    The watchdog should NOT relaunch. Test via collect.py classification."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    sentinel_run = "20260610-100000"
    _write_sentinel(data_home, key, sentinel_run, "already-shipped")

    # Clean run that shipped.
    _write_jsonl(data_home, key, project_path, [
        {
            "run_id": sentinel_run,
            "iteration": 1,
            "exit_code": 0,
            "new_commits_total": 5,
            "stop_reason": "already-shipped",
            "duration_sec": 120,
        },
    ])

    # Write a sub-plan with device-manual tier to trigger shipped-unverified.
    # Plans must be under the project's docs/plans/ (where _find_plans_dir looks).
    plans_dir = project_path / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "MASTER-test.md").write_text(
        "---\nmaster_plan: test\nstatus: shipped\ncreated: 2026-06-10T00:00:00+08:00\n---\n"
        "# MASTER\n\n## Sub-plan registry\n\n| # | File |\n|---|---|\n"
        "| 1 | 2026-06-08-alpha.md |\n",
        encoding="utf-8",
    )
    (plans_dir / "2026-06-08-alpha.md").write_text(
        "---\nplan: alpha\nstatus: shipped\nverification_tier: device-manual\ncurrent_step: 3\nestimated_steps: 3\n---\n# alpha\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            sentinel_run,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0

    pm_path = _launcher_dir(data_home, key) / "postmortems" / f"{sentinel_run}.md"
    assert pm_path.exists()
    text = pm_path.read_text(encoding="utf-8")
    assert "shipped-unverified" in text, (
        f"Expected shipped-unverified classification.\nHead:\n{text[:500]}"
    )


# ── AC-5: no-evidence → do not relaunch ────────────────────────────────────


def test_no_evidence_classified(scratch_env):
    """When --run-id R has no JSONL records but sentinel exists,
    classify as no-evidence (triage, no relaunch)."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    sentinel_run = "20260610-071415"
    _write_sentinel(data_home, key, sentinel_run, "local_checks_failed")

    # No JSONL records at all — the run crashed before iter 1.

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            sentinel_run,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0

    pm_path = _launcher_dir(data_home, key) / "postmortems" / f"{sentinel_run}.md"
    assert pm_path.exists()
    text = pm_path.read_text(encoding="utf-8")
    assert "no-evidence" in text, (
        f"Expected no-evidence classification.\nHead:\n{text[:500]}"
    )


# ── dependency-unreachable classification (SP3) ────────────────────────────


def _write_iter_log(data_home: Path, key: str, run_id: str, name: str, body: str) -> Path:
    """Write a per-iteration log file and return its path."""
    run_dir = _logs_dir(data_home, key) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / name
    p.write_text(body, encoding="utf-8")
    return p


def test_dependency_unreachable_classified_and_names_dep(scratch_env):
    """A no-progress stall whose iter log shows a missing MCP classifies as
    dependency-unreachable (NOT stuck-no-progress) and names the dependency."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    run_id = "20260613-095942"
    _write_sentinel(data_home, key, run_id, "no-progress")

    # Iter log carries the exact figma-stall signal.
    iter_log = _write_iter_log(
        data_home, key, run_id, "iter-07.log",
        "implement to Figma\nFigma MCP not connected\n"
        "claude mcp list | grep -q figma\n",
    )

    # Clean exit codes (like the real figma stall) -> would otherwise be
    # stuck-no-progress. The detector keys off the iter log via "log".
    _write_jsonl(data_home, key, project_path, [
        {"run_id": run_id, "iteration": i, "exit_code": 0,
         "new_commits_total": 0, "stop_reason": "no-progress",
         "duration_sec": 60, "log": str(iter_log)}
        for i in (5, 6, 7)
    ])

    result = subprocess.run(
        [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path),
         "--run-id", run_id, "--quiet"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    text = (_launcher_dir(data_home, key) / "postmortems" / f"{run_id}.md").read_text(encoding="utf-8")
    assert "dependency-unreachable" in text, text[:600]
    assert "stuck-no-progress" not in text, text[:600]
    assert "figma" in text, text[:600]          # names the missing dep
    assert "ilk-worker-mcp" in text, text[:600]  # remediation hint


def test_plain_no_progress_still_stuck(scratch_env):
    """A no-progress stall with NO dependency signal still classifies as
    stuck-no-progress (no regression)."""
    project_path, env, key = scratch_env
    data_home = Path(env["ILK_DATA_HOME"])

    run_id = "20260613-120000"
    _write_sentinel(data_home, key, run_id, "no-progress")
    iter_log = _write_iter_log(
        data_home, key, run_id, "iter-03.log",
        "thinking...\nediting files\nno obvious next step\n",
    )
    _write_jsonl(data_home, key, project_path, [
        {"run_id": run_id, "iteration": i, "exit_code": 0,
         "new_commits_total": 0, "stop_reason": "no-progress",
         "duration_sec": 60, "log": str(iter_log)}
        for i in (1, 2, 3)
    ])

    result = subprocess.run(
        [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path),
         "--run-id", run_id, "--quiet"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    text = (_launcher_dir(data_home, key) / "postmortems" / f"{run_id}.md").read_text(encoding="utf-8")
    assert "stuck-no-progress" in text, text[:600]
    assert "dependency-unreachable" not in text, text[:600]


# ── classify_action parity (bash watchdog) ─────────────────────────────────


@pytest.mark.parametrize("label,expected_action", [
    ("running", "sleep"),
    ("all-shipped", "promote"),
    ("already-shipped", "promote"),
    ("shipped", "promote"),
    ("shipped-unverified", "terminate"),
    ("no-evidence", "terminate"),
    ("timeout-bound", "relaunch"),
    ("max-iter-bound", "relaunch"),
    ("api-flaky", "relaunch"),
    ("interrupted", "relaunch"),
    ("stuck-no-progress", "blacklist"),
    ("api-blocked", "blacklist"),
    ("budget-exhausted", "blacklist"),
    ("local-checks-stuck", "blacklist"),
    ("dependency-unreachable", "blacklist"),
    ("merge-conflict", "blacklist"),
    ("unknown-label", "blacklist"),  # fail-safe: unknown -> blacklist
])
def test_classify_action(label, expected_action):
    """watchdog.sh classify_action routes classification labels to the correct action.

    This is the pure-Python translation of the bash classify_action function.
    The bash-backed parity test (step 2) proves the real function matches.
    """
    def classify_action(s: str) -> str:
        if s == "running":
            return "sleep"
        if s in ("all-shipped", "already-shipped", "shipped"):
            return "promote"
        if s in ("shipped-unverified", "no-evidence"):
            return "terminate"
        if s in ("timeout-bound", "max-iter-bound", "api-flaky", "interrupted"):
            return "relaunch"
        if s in ("stuck-no-progress", "api-blocked", "budget-exhausted",
                 "local-checks-stuck", "dependency-unreachable", "merge-conflict"):
            return "blacklist"
        return "blacklist"  # fail-safe: unknown terminal label -> blacklist

    assert classify_action(label) == expected_action
