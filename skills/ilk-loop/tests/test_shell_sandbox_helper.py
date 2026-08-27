"""Tests for the shell sandbox helper and its adoption by .sh harnesses.

Covers AC-1 through AC-4 and AC-7 of sub-plan
``a-shell-harness-cannot-either``.  These tests assert on the helper's
behaviour and on the harnesses' *text*, so the red run does not execute
``scheduler.sh`` at all.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]  # repo root
_HELPER = _ROOT / "skills" / "ilk-loop" / "scripts" / "_ilk_test_sandbox.sh"

_HARNESS_FILES = [
    _ROOT / "skills" / "ilk-watchdog" / "tests" / "test_scheduler_lock_contention.sh",
    _ROOT / "skills" / "ilk-watchdog" / "tests" / "test_scheduler_clone_logging.sh",
    _ROOT / "skills" / "ilk-loop" / "tests" / "test_project_runner_liveness.sh",
    _ROOT / "skills" / "ilk-launcher" / "tests" / "test_stop_leaves_no_survivors.sh",
]


# ---------------------------------------------------------------------------
# AC-1: helper is sourceable and sets the right env vars
# ---------------------------------------------------------------------------


class TestHelperBehaviour:
    """AC-1 + AC-2: the helper sets env vars and creates the logs dir."""

    def test_sourceable_and_sets_env(self, tmp_path: Path) -> None:
        """AC-1: sourcing the helper and calling its function sets HOME,
        ILK_DATA_HOME, ILK_SKILL_HOME and unsets ILK_DATA_DIR."""
        root = tmp_path / "sandbox"
        root.mkdir()
        # Pre-set ILK_DATA_DIR so the helper must actively unset it.
        script = (
            f'ILK_DATA_DIR="/stale/value"\n'
            f'source "{_HELPER}"\n'
            f'ilk_test_sandbox "{root}"\n'
            # Dump the vars so we can assert.
            f'echo "HOME=$HOME"\n'
            f'echo "ILK_DATA_HOME=$ILK_DATA_HOME"\n'
            f'echo "ILK_SKILL_HOME=$ILK_SKILL_HOME"\n'
            f'echo "ILK_DATA_DIR=${{ILK_DATA_DIR:-<unset>}}"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"helper failed to source: {result.stderr}"
        out = result.stdout
        assert f"HOME={root}" in out, f"HOME not set to root: {out}"
        assert f"ILK_DATA_HOME={root / '.ilk-data'}" in out, (
            f"ILK_DATA_HOME wrong: {out}"
        )
        assert "ILK_DATA_DIR=<unset>" in out, (
            f"ILK_DATA_DIR not unset: {out}"
        )
        # ILK_SKILL_HOME must be set to the repo's skills/ dir.
        assert "ILK_SKILL_HOME=" in out
        skills_dir = str(_ROOT / "skills")
        assert f"ILK_SKILL_HOME={skills_dir}" in out, (
            f"ILK_SKILL_HOME not pointing at skills/: {out}"
        )

    def test_creates_logs_dir(self, tmp_path: Path) -> None:
        """AC-2: the helper creates $root/.ilk-data/logs/ before returning."""
        root = tmp_path / "sandbox"
        root.mkdir()
        script = (
            f'source "{_HELPER}"\n'
            f'ilk_test_sandbox "{root}"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"helper failed: {result.stderr}"
        logs_dir = root / ".ilk-data" / "logs"
        assert logs_dir.is_dir(), (
            f"{logs_dir} was not created by the helper"
        )


# ---------------------------------------------------------------------------
# AC-3: harness text — all source the helper, none set HOME inline
# ---------------------------------------------------------------------------


class TestHarnessText:
    """AC-3 + AC-4: inspect the harness files' text, no execution."""

    @pytest.mark.parametrize("harness", _HARNESS_FILES, ids=lambda p: p.name)
    def test_sources_helper(self, harness: Path) -> None:
        """AC-3: each harness sources _ilk_test_sandbox.sh."""
        text = harness.read_text()
        assert "_ilk_test_sandbox.sh" in text, (
            f"{harness.name} does not source _ilk_test_sandbox.sh"
        )

    @pytest.mark.parametrize("harness", _HARNESS_FILES, ids=lambda p: p.name)
    def test_no_inline_home_assignment(self, harness: Path) -> None:
        """AC-3: no harness sets HOME="$TMPDIR..." inline any more."""
        text = harness.read_text()
        # Match the old pattern: HOME="$TMPDIR... or HOME="$FAKE_HOME etc.
        matches = re.findall(r'^\s*(?:export\s+)?HOME="\$', text, re.MULTILINE)
        assert len(matches) == 0, (
            f"{harness.name} still has inline HOME assignment(s): {matches}"
        )


# ---------------------------------------------------------------------------
# AC-4: case 1 timeout bound in test_scheduler_lock_contention.sh
# ---------------------------------------------------------------------------


class TestLockContentionTimeout:
    """AC-4: case 1's scheduler invocation must carry a timeout bound."""

    def test_case1_has_timeout(self) -> None:
        """AC-4: the case 1 scheduler call (the first `bash "$SCHEDULER"` line
        after the case-1 setup) must be wrapped in `timeout`."""
        harness = _HARNESS_FILES[0]  # test_scheduler_lock_contention.sh
        text = harness.read_text()
        # Find case 1 section and its scheduler invocation.
        # Case 1 ends at "--- case 2".
        case1_match = re.search(
            r"(?:case 1.*?)(?=--- case 2)", text, re.DOTALL | re.IGNORECASE
        )
        assert case1_match, "Could not find case 1 section"
        case1_text = case1_match.group()
        # The scheduler invocation must have a timeout wrapper.
        # Pattern: `timeout <N> bash "$SCHEDULER"` or `gtimeout <N> bash "$SCHEDULER"`
        # The existing case 2 already has `timeout 60`.
        has_timeout = re.search(
            r"(?:g?timeout)\s+\d+.*bash.*SCHEDULER", case1_text
        )
        assert has_timeout, (
            "case 1's scheduler invocation has no timeout bound — "
            "it is unbounded and can hang indefinitely"
        )


# ---------------------------------------------------------------------------
# AC-7: the four .sh files are uncollected by pytest
# ---------------------------------------------------------------------------


class TestNotCollected:
    """AC-7: shell harnesses must not be collected by pytest."""

    def test_shell_harnesses_not_collected(self) -> None:
        """AC-7: a collect-only check matches 0 node ids for the four .sh files."""
        result = subprocess.run(
            [
                "python3", "-m", "pytest",
                "--collect-only", "-q",
                "--rootdir", str(_ROOT),
            ],
            capture_output=True, text=True, timeout=60,
            cwd=str(_ROOT),
        )
        # The output lists collected node ids.  Filter to lines that look
        # like collected test paths (they contain "::" for pytest node ids,
        # or end with ".py" for module-level collection).  A .sh file
        # collected directly would appear as a bare path, not inside a
        # parametrized ID from *this* test module.
        collected_lines = [
            line for line in result.stdout.splitlines()
            if "::" not in line and line.strip().endswith(".sh")
        ]
        for harness in _HARNESS_FILES:
            for line in collected_lines:
                assert harness.name not in line, (
                    f"{harness.name} appears in pytest collection as a "
                    "collected test file — shell harnesses must not be collected"
                )
