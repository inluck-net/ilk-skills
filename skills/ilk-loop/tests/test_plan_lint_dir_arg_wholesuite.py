"""A directory argument does not scope a suite — regression for b37ce4609f15cd21.

``_is_whole_suite_command`` treated ANY positional arg as scoping the run, so
``pytest tests/ -q`` escaped ``lint_wholesuite_gate_baseline`` entirely while a
bare ``pytest -q`` was caught.  Measured over 333 real sub-plans on 2026-08-12:
the lint judged 84 effectively-whole-suite gates and skipped 47 (35%), and the
skipped form was the *most common gate in the corpus* — ``python3 -m pytest
tests/ -q`` appeared 33 times and ``pytest tests/ -x -q`` 17 times.

The classification was also accidentally inconsistent: ``"tests"`` is listed in
``_NON_PATH_TOKENS``, so ``pytest tests -q`` already counted as whole-suite
while ``pytest tests/ -q`` did not.  A trailing slash decided it.

Why it matters: a directory gate runs a whole tree, so a collection error or a
baseline-red test in it fails the gate regardless of how "scoped" it looks — and
28 of those 47 sub-plans declare the gate in *frontmatter*, which re-runs at
every step, so the loop false-blocks every step and can reach
``stuck-no-progress``.  Live example: ``pytest skills/ilk-loop/tests/ -q`` exits
"Interrupted: 1 error during collection" under this host's Python 3.9.6.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint


class TestDirectoryArgIsWholeSuite:
    """A directory arg means "run this whole tree" — not a scope."""

    @pytest.mark.parametrize("cmd", [
        "python3 -m pytest tests/ -q",
        "pytest tests/ -x -q",
        "python3 -m pytest skills/ilk-loop/tests/ -q -p no:randomly",
        "python3 -m pytest skills/ilk-loop/tests -q",
        "python3 -m pytest tools/claude-worker/tests -q",
        "python3 -m pytest skills/ilk-watchdog -q",
        'cd "$ILK_REPO_ROOT" && python3 -m pytest skills/ilk-loop/tests/ -q',
        "python3 -m pytest apps/orders/ -q",
    ])
    def test_directory_arg_counts_as_whole_suite(self, cmd):
        assert plan_lint._is_whole_suite_command(cmd) is True, (
            f"directory gate treated as scoped: {cmd}"
        )

    @pytest.mark.parametrize("cmd", [
        "python3 -m pytest -q",
        "pytest -q",
        "python3 -m pytest tests -q",  # bare 'tests' — already whole-suite
    ])
    def test_bare_runner_still_whole_suite(self, cmd):
        assert plan_lint._is_whole_suite_command(cmd) is True, cmd


class TestRealScopingStillScopes:
    """A single file, a node id, or a -k selector genuinely narrows the run."""

    @pytest.mark.parametrize("cmd", [
        "python3 -m pytest tests/test_writeback.py -q",
        "python3 -m pytest skills/ilk-loop/tests/test_plan_lint.py -q",
        "python3 -m pytest tests/test_a.py tests/test_b.py -q",
        "python3 -m pytest tests/test_a.py::test_specific -q",
        "npx vitest run src/foo.spec.ts",
        "python3 -m pytest src/thing.py -q",
    ])
    def test_file_or_node_id_is_scoped(self, cmd):
        assert plan_lint._is_whole_suite_command(cmd) is False, (
            f"file-scoped gate misread as whole-suite: {cmd}"
        )

    @pytest.mark.parametrize("cmd", [
        "python3 -m pytest -k test_writeback -q",
        "python3 -m pytest --deselect tests/test_a.py::test_slow -q",
        "python3 -m pytest tests/ -k test_writeback -q",
    ])
    def test_selector_is_scoped(self, cmd):
        """Preserved behaviour: a -k/--deselect selector scopes the run.

        Kept deliberately unchanged — only the directory-arg classification is
        in scope for this fix.
        """
        assert plan_lint._is_whole_suite_command(cmd) is False, cmd

    def test_flag_values_are_not_mistaken_for_directories(self):
        """A value-taking flag's argument must not read as a path.

        e.g. ``--timeout-method thread`` — 'thread' has no extension, and if it
        were treated as a directory the gate would flip to whole-suite for the
        wrong reason.
        """
        cmd = "python3 -m pytest tests/test_a.py -q --timeout 60 --timeout-method thread"
        assert plan_lint._is_whole_suite_command(cmd) is False, cmd


class TestLintNowJudgesDirectoryGates:
    """End-to-end: the baseline lint must actually fire on a directory gate."""

    def _plan(self, gate: str, note: str = "") -> str:
        return (
            "---\n"
            "plan: demo\n"
            "status: pending\n"
            "local_checks:\n"
            f"  - command: {gate}\n"
            "    timeout: 600\n"
            "---\n"
            "\n# Sub-plan: demo\n"
            f"\n{note}\n"
            "\n### Step 0 — Edit\n"
            "- Edit `src/thing.py`.\n"
        )

    def test_directory_gate_without_baseline_note_is_flagged(self):
        f = plan_lint.lint_wholesuite_gate_baseline(
            self._plan("python3 -m pytest tests/ -q"), "demo"
        )
        assert len(f) == 1, f"directory gate not flagged: {f}"
        assert "baseline-green" in f[0]

    def test_scoped_file_gate_is_not_flagged(self):
        f = plan_lint.lint_wholesuite_gate_baseline(
            self._plan("python3 -m pytest tests/test_a.py -q"), "demo"
        )
        assert f == [], f"file-scoped gate flagged: {f}"
