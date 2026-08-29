"""Tests for path_prelude application in run_local_checks and ship_config.

AC-1: A gate whose executable lives only in a prelude dir runs.
AC-2: No prelude ⇒ command unchanged (regression guard).
AC-3: ship_config validates path_prelude as optional string.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SHIP_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-ship" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SHIP_SCRIPTS_DIR))

import run_local_checks as rlc  # noqa: E402
from ship_config import (  # noqa: E402
    MalformedConfig,
    ShipConfig,
    load_ship_config,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_project(tmp_path: Path) -> Path:
    """Create a minimal project root with .git."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── AC-1: prelude makes a gate executable resolvable ────────────────────────

class TestPathPreludeApplied:
    """run_local_checks.run_one prepends path_prelude when configured."""

    def test_gate_with_prelude_resolves_executable(self, tmp_path: Path) -> None:
        """AC-1: A gate whose executable lives only in a prelude dir runs."""
        # Create a throwaway executable in a custom bin dir.
        custom_bin = tmp_path / "custom_bin"
        custom_bin.mkdir()
        helper = custom_bin / "my-helper"
        helper.write_text('#!/bin/sh\necho "hello from helper"\n', encoding="utf-8")
        helper.chmod(0o755)

        # Configure path_prelude to add that dir.
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {
                    "command": "python3 -m pytest",
                    "path_prelude": f'export PATH="{custom_bin}:$PATH"',
                },
            },
        })

        # The gate command uses the helper — should resolve via prelude.
        result = rlc.run_one(
            {"command": "my-helper", "timeout": 10},
            "step", project,
        )
        assert result.passed is True, f"expected pass, got: exit={result.exit_code} stderr={result.stderr_tail}"


# ── AC-2: prelude is read from config (regression guard) ────────────────────

class TestNoPreludeUnchanged:
    """When no prelude is configured, run_one reads the config and finds none."""

    def test_run_one_reads_prelude_from_config(self, tmp_path: Path) -> None:
        """AC-2: run_one reads path_prelude from .ilk-launch.json.

        Regression guard — run_one must consult the project config for
        path_prelude. Today it does not (no config reading at all), so a
        command whose executable is ONLY in the prelude dir fails.
        """
        # Create a custom executable that's NOT on the default PATH.
        custom_bin = tmp_path / "custom_bin"
        custom_bin.mkdir()
        helper = custom_bin / "prelude-cmd"
        helper.write_text('#!/bin/sh\necho "prelude works"\n', encoding="utf-8")
        helper.chmod(0o755)

        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {
                    "command": "python3 -m pytest",
                    "path_prelude": f'export PATH="{custom_bin}:$PATH"',
                },
            },
        })

        # This should pass once run_one reads the prelude; fails today.
        result = rlc.run_one(
            {"command": "prelude-cmd", "timeout": 10},
            "step", project,
        )
        assert result.passed is True, (
            f"expected prelude-cmd to resolve via path_prelude, "
            f"got exit={result.exit_code} stderr={result.stderr_tail}"
        )


# ── AC-3: ship_config validates path_prelude ────────────────────────────────

class TestPathPreludeSchema:
    """ship_config validates path_prelude as optional string."""

    def test_path_prelude_non_string_is_malformed(self, tmp_path: Path) -> None:
        """AC-3: A non-string path_prelude is a MalformedConfig.

        Fails today because ship_config does not validate path_prelude.
        Once validation is added, a non-string will be rejected.
        """
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {
                    "command": "pytest",
                    "path_prelude": 42,
                },
            },
        })
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig), (
            f"expected MalformedConfig for non-string path_prelude, got {type(result).__name__}"
        )
        assert "path_prelude" in result.detail
