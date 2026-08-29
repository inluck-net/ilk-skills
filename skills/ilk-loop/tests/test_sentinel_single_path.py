"""Tests for the sentinel-single-path sub-plan (the-sentinel-has-one-path).

Six tests, one per AC, all FAILING on this commit. They assert:
  AC-1  ilk_paths.sentinel_path(key) exists and returns <launcher_dir>/last-exit.json
  AC-2  status_progress reads the same path the accessor returns
  AC-3  status_progress no longer string-joins "runtime" for sentinel resolution
  AC-4  a stale runtime/last-exit.json beside a fresh runtime/launcher/last-exit.json
        does not change what status_progress reports
  AC-5  docs name runtime/launcher/last-exit.json (grep assertions)
  AC-6  no Python file under skills/ resolves last-exit.json by string-joining
        a "runtime" directory — the accessor is the only route
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── paths ────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]  # ilk-skills repo root
_SKILL_ROOT = _REPO_ROOT / "skills"
_RESOLVER = _SKILL_ROOT / "ilk-loop" / "scripts" / "ilk_paths.py"
_ILK_STATUS_DOC = _REPO_ROOT / "commands" / "ilk-status.md"
_SKILL_DOC = _SKILL_ROOT / "ilk-loop" / "SKILL.md"

# Import status_progress directly (avoids subprocess sys.path issues)
_SCRIPTS = _SKILL_ROOT / "ilk-launcher" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import status_progress  # noqa: E402


# ── AC-1: sentinel_path accessor exists and returns launcher dir ─────────────


def test_sentinel_path_accessor_exists():
    """AC-1: ilk_paths exposes sentinel_path(key).

    After the fix, running ilk_paths.py --start <project> --sentinel-path
    prints the path to <external_launcher_dir>/last-exit.json.
    On this commit, --sentinel-path does not exist yet → FAIL.
    """
    result = subprocess.run(
        [sys.executable, str(_RESOLVER),
         "--start", str(_REPO_ROOT), "--sentinel-path"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"ilk_paths.py --sentinel-path failed (exit {result.returncode}):\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    path = Path(result.stdout.strip())
    assert path.name == "last-exit.json", (
        f"Expected sentinel path to end with last-exit.json, got: {path}"
    )
    assert "launcher" in path.parts, (
        f"Expected sentinel under runtime/launcher/, got: {path}"
    )


def test_sentinel_path_in_json_payload():
    """AC-1: sentinel_path is included in the JSON payload.

    The --json output (default, no flags) should include a sentinel_path
    field so bash/PowerShell callers can extract it.
    On this commit, the field is missing → FAIL.
    """
    result = subprocess.run(
        [sys.executable, str(_RESOLVER), "--start", str(_REPO_ROOT)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"ilk_paths.py --start failed:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    data = json.loads(result.stdout)
    assert "sentinel_path" in data, (
        f"JSON payload missing 'sentinel_path' field. Keys: {sorted(data.keys())}"
    )
    path = Path(data["sentinel_path"])
    assert path.name == "last-exit.json"
    assert "launcher" in path.parts


# ── AC-2: status_progress reads what the accessor returns ────────────────────

MASTER_FM = """\
---
master_plan: 2026-08-29-test
batch_date: 2026-08-29
source_status: test
total_tickets: 1
status: active
current_subplan: 2026-08-29-example
---

# MASTER — test

## Sub-plan registry

| # | Sub-plan | Steps |
|---|---|---|
| 1 | [2026-08-29-example.md](./2026-08-29-example.md) | 2 |
"""

SUBPLAN_FM = """\
---
plan: 2026-08-29-example
status: shipped
current_step: 2
estimated_steps: 2
last_updated: 2026-08-29
---

# Sub-plan: example

## Steps

### Step 0 — Do the thing
- Did the thing.

### Step 1 — Do another
- Did another.
"""


def test_status_progress_uses_accessor_path(tmp_path: Path):
    """AC-2: status_progress reads the sentinel from the accessor path.

    Build a sandbox data home with a sentinel at runtime/launcher/last-exit.json,
    then call build_json and assert the reported sentinel path is under
    runtime/launcher/ — not a hardcoded join from plans_dir.parent / "runtime".

    On this commit, status_progress manually joins 'runtime' → FAIL.
    """
    import ilk_paths

    # Create a git repo for the project root
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), capture_output=True, check=True)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)

    key = ilk_paths.project_key(repo)
    data_home = tmp_path / ".ilk-data"
    plans_dir = data_home / "projects" / key / "plans"
    plans_dir.mkdir(parents=True)
    launcher_dir = data_home / "projects" / key / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True)

    # Write a minimal sentinel
    sentinel = {"state": "shipped", "run_id": "test-001"}
    (launcher_dir / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8"
    )

    # Write MASTER + sub-plan
    (plans_dir / "MASTER-2026-08-29-test.md").write_text(MASTER_FM, encoding="utf-8")
    (plans_dir / "2026-08-29-example.md").write_text(SUBPLAN_FM, encoding="utf-8")

    # Patch ILK_DATA_HOME and find_plans_dir
    import os
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}):
        with patch.object(status_progress, "find_plans_dir", return_value=plans_dir):
            result = status_progress.build_json(
                project_name="test",
                project_root=repo,
                plans_dir=plans_dir,
                master_name="MASTER-2026-08-29-test.md",
                rows=[],
                pace_min=None,
                repos=[repo],
                step_commit_count=0,
            )

    reported_path = Path(result["sentinel"]["last_exit_path"])
    assert "launcher" in reported_path.parts, (
        f"Sentinel path {reported_path} is not under runtime/launcher/. "
        f"status_progress must use the accessor."
    )


# ── AC-4: stale runtime/last-exit.json doesn't mislead ──────────────────────


def test_stale_orphan_does_not_change_report(tmp_path: Path):
    """AC-4: A stale runtime/last-exit.json sitting beside a fresh
    runtime/launcher/last-exit.json does not change what /ilk-status reports.

    This is the exact 2026-08-29 misdiagnosis scenario: following the
    documented path produced a confident, wrong "the sentinel is stale".

    On this commit, status_progress resolves via plans_dir.parent / "runtime"
    which IS runtime/ — so it reads the stale file → FAIL.
    """
    import ilk_paths

    # Create a git repo for the project root
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), capture_output=True, check=True)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)

    # Compute the key the way build_json does
    key = ilk_paths.project_key(repo)

    # Override ILK_DATA_HOME so sentinel_path resolves into our sandbox
    data_home = tmp_path / ".ilk-data"
    plans_dir = data_home / "projects" / key / "plans"
    plans_dir.mkdir(parents=True)

    # Stale orphan at runtime/last-exit.json
    runtime_dir = data_home / "projects" / key / "runtime"
    runtime_dir.mkdir(parents=True)
    stale = {"state": "local_checks_failed", "run_id": "20260813-182937"}
    (runtime_dir / "last-exit.json").write_text(
        json.dumps(stale), encoding="utf-8"
    )

    # Fresh sentinel at runtime/launcher/last-exit.json
    launcher_dir = runtime_dir / "launcher"
    launcher_dir.mkdir(parents=True)
    fresh = {"state": "shipped", "run_id": "20260828-211346"}
    (launcher_dir / "last-exit.json").write_text(
        json.dumps(fresh), encoding="utf-8"
    )

    # Write MASTER + sub-plan
    (plans_dir / "MASTER-2026-08-29-test.md").write_text(MASTER_FM, encoding="utf-8")
    (plans_dir / "2026-08-29-example.md").write_text(SUBPLAN_FM, encoding="utf-8")

    # Patch ILK_DATA_HOME so ilk_paths.sentinel_path resolves into sandbox
    import os
    env_patch = {"ILK_DATA_HOME": str(data_home)}
    with patch.dict(os.environ, env_patch):
        with patch.object(status_progress, "find_plans_dir", return_value=plans_dir):
            result = status_progress.build_json(
                project_name="test",
                project_root=repo,
                plans_dir=plans_dir,
                master_name="MASTER-2026-08-29-test.md",
                rows=[],
                pace_min=None,
                repos=[repo],
                step_commit_count=0,
            )

    sentinel_state = result["sentinel"]["state"]
    # The fresh sentinel says "shipped". If status_progress reads
    # the stale orphan, it would report "local_checks_failed".
    assert sentinel_state == "shipped", (
        f"Expected state='shipped' from fresh launcher sentinel, "
        f"got '{sentinel_state}' — likely reading the stale orphan "
        f"at runtime/last-exit.json instead of runtime/launcher/last-exit.json"
    )


# ── AC-5: docs name the correct path ────────────────────────────────────────


def test_docs_name_launcher_path():
    """AC-5: Both docs name runtime/launcher/last-exit.json, not the orphan.

    - commands/ilk-status.md:172 must reference runtime/launcher/last-exit.json
    - skills/ilk-loop/SKILL.md:123 must list last-exit.json under runtime/launcher/

    On this commit, ilk-status.md:172 says runtime/last-exit.json and
    SKILL.md:123 lists it under runtime/ → FAIL.
    """
    # -- ilk-status.md --
    status_content = _ILK_STATUS_DOC.read_text(encoding="utf-8")
    assert "runtime/launcher/last-exit.json" in status_content, (
        "commands/ilk-status.md must name runtime/launcher/last-exit.json "
        "in the stale sentinel detection section."
    )
    assert "runtime/last-exit.json" not in status_content, (
        "commands/ilk-status.md still references the orphan runtime/last-exit.json. "
        "It must be replaced with runtime/launcher/last-exit.json."
    )

    # -- SKILL.md --
    skill_content = _SKILL_DOC.read_text(encoding="utf-8")
    # The runtime layout block should list last-exit.json under runtime/launcher/
    assert "runtime/launcher/" in skill_content, (
        "SKILL.md must mention runtime/launcher/ in the runtime layout block."
    )
    # Check that the runtime/ line no longer claims last-exit.json
    for line in skill_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("runtime/") and "launcher" not in stripped:
            assert "last-exit.json" not in stripped, (
                f"SKILL.md still lists last-exit.json under plain runtime/:\n"
                f"  {stripped}\n"
                f"It must be under runtime/launcher/."
            )


# ── AC-6: no Python file string-joins runtime for sentinel ──────────────────


def test_no_python_string_join_for_sentinel():
    """AC-6: No Python file under skills/ resolves last-exit.json by
    string-joining a bare 'runtime' directory (without 'launcher' in the
    same expression).

    .ps1 files are excluded — the PowerShell sentinel path is out of scope
    (SP5 out-of-scope decision: PS runner is Windows-only, unverifiable
    on this host).

    Files that correctly build runtime/launcher/last-exit.json (with the
    intermediate 'launcher' component) are not violations — only bare
    runtime/last-exit.json joins are.

    On this commit, status_progress.py:457 does plans_dir.parent / "runtime"
    then derives the sentinel from that → FAIL.
    """
    skills_dir = _SKILL_ROOT
    violations = []

    for py_file in skills_dir.rglob("*.py"):
        # Skip test files and scripts that are test helpers
        rel = py_file.relative_to(skills_dir)
        if "tests" in rel.parts:
            continue
        if py_file.name.startswith("test_"):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            # Look for path construction involving "runtime" (bare, without
            # "launcher" in the same expression) near sentinel resolution.
            if '"runtime"' not in line and "'runtime'" not in line:
                continue
            # If the line also mentions "launcher", it's building the
            # correct runtime/launcher/ path — not a violation.
            if '"launcher"' in line or "'launcher'" in line:
                continue
            # Must be a path construction, not a variable name or docstring
            if not ("/" in line or "Path" in line or "parent" in line):
                continue
            # Check if this is near sentinel/last-exit.json resolution
            context_start = max(0, i - 10)
            context_end = min(len(lines), i + 10)
            context = "\n".join(lines[context_start:context_end])
            if "last-exit" in context or "sentinel" in context.lower():
                violations.append(f"{py_file}:{i+1}: {stripped}")

    assert not violations, (
        "AC-6 violation — Python files resolve last-exit.json by "
        "string-joining bare 'runtime' instead of using the accessor:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
