"""Tests for ship_config.py callers and CLI exit codes.

Red-first tests: these are expected to FAIL on the current codebase.
- test_unconfigured_exit_code: --validate exits 0 for NotConfigured today;
  the test asserts exit 2, which is the target behavior (step 1).
- test_load_ship_config_has_runtime_caller: 0 non-test callers exist today;
  the test asserts >= 1, which is the target behavior (step 2).

These tests pin the two defects this sub-plan closes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
REPO_ROOT = SCRIPTS_DIR.parent.parent  # skills/ilk-ship → skills → repo root


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_project(tmp_path: Path) -> Path:
    """Create a minimal project root with .git."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _write_json(path: Path, data: dict) -> None:
    """Write a JSON file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Exit code tests ─────────────────────────────────────────────────────────

class TestExitCodes:
    """--validate must return distinct exit codes for each verdict."""

    def test_unconfigured_exit_code(self, tmp_path: Path) -> None:
        """NotConfigured must exit 2, not 0.

        Today (pre-fix): exits 0. The gate asserts exit 2, so this test
        is expected-red until step 1 changes main().
        """
        project = _make_project(tmp_path)
        # No .ilk-launch.json at all → NotConfigured
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "ship_config.py"),
             "--validate", "--project", str(project)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 2, (
            f"Expected exit 2 for NotConfigured, got {proc.returncode}. "
            f"stdout: {proc.stdout!r}  stderr: {proc.stderr!r}"
        )

    def test_unconfigured_file_exists_no_ship_key(self, tmp_path: Path) -> None:
        """File exists but has no 'ship' key → must exit 2.

        Today (pre-fix): exits 0. Expected-red until step 1.
        """
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "max_iterations": 5,
            "iteration_timeout_min": 30,
        })
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "ship_config.py"),
             "--validate", "--project", str(project)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 2, (
            f"Expected exit 2 for NotConfigured (no ship key), got {proc.returncode}. "
            f"stdout: {proc.stdout!r}  stderr: {proc.stderr!r}"
        )


# ── Caller orphan test ──────────────────────────────────────────────────────

class TestLoadShipConfigCallers:
    """load_ship_config must have at least one non-test runtime caller."""

    def test_load_ship_config_has_runtime_caller(self) -> None:
        """At least one non-test .py file under skills/ must import or
        call load_ship_config.

        Today (pre-fix): 0 callers. Expected-red until step 2 adds a
        doctor.py gate that calls load_ship_config.
        """
        import ast

        callers: list[str] = []
        skills_dir = REPO_ROOT / "skills"

        for py_file in skills_dir.rglob("*.py"):
            # Skip test files
            if "tests" in py_file.parts:
                continue
            # Skip the definition file itself
            if py_file == SCRIPTS_DIR / "ship_config.py":
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except (SyntaxError, OSError):
                continue

            for node in ast.walk(tree):
                # Check for: from ship_config import load_ship_config
                # or: from skills.ilk_ship.scripts.ship_config import load_ship_config
                if isinstance(node, ast.ImportFrom):
                    if node.names and any(
                        alias.name == "load_ship_config" for alias in node.names
                    ):
                        callers.append(str(py_file))
                        break
                # Check for: import ship_config  and then ship_config.load_ship_config(...)
                # or direct function call load_ship_config(...)
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == "load_ship_config":
                        callers.append(str(py_file))
                        break
                    if (isinstance(func, ast.Attribute)
                            and func.attr == "load_ship_config"):
                        callers.append(str(py_file))
                        break

        assert len(callers) >= 1, (
            "load_ship_config has 0 non-test callers under skills/. "
            "This is the orphan defect (SUITE-LOADER-ORPHANED). "
            "Expected: at least one runtime consumer (e.g. doctor.py)."
        )
