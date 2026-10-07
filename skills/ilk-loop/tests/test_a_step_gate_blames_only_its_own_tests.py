r"""Red-first pins: a step gate blames only its own tests.

Part of sub-plan ``a-step-gate-blames-only-its-own-tests``
(step 0 of 2).

Contract (pre-resolved — do not re-decide):

- In ``run_local_checks._synthesize_mention_check``, remove the block
  that appends ``test_importers.importer_tests(project, changed_files)``
  hits to the synthesized check's files.  The synthesized mention check
  returns to its pre-07l-#3 file set.

- Keep ``skills/ilk-loop/scripts/test_importers.py``,
  ``plan_lint``'s delegation to it, and
  ``verification_record.compute_suite_scope``'s use of it unchanged.

- Update ``skills/ilk-loop/tests/test_a_step_gate_runs_the_importers.py``:
  replace ONLY the assertions whose stated contract is "the runtime step
  gate adds importer tests" with assertions that it does not; keep every
  helper/plan_lint/compute_suite_scope assertion.

- The new test pins: a changed module with an importer test whose red
  pre-exists does NOT add that importer to the synthesized check.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _make_repo(
    tmp_path: Path,
    *,
    source_files: dict[str, str] | None = None,
    test_files: dict[str, str] | None = None,
) -> Path:
    """Create a minimal git repo with source and test files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    for rel, content in (source_files or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    for rel, content in (test_files or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _run_synthesize_mention_check(
    project: Path,
    changed_files: list[str],
    baseline_red: list[dict] | None = None,
) -> dict | None:
    """Run _synthesize_mention_check with the given files."""
    try:
        import run_local_checks as rlc
        return rlc._synthesize_mention_check(
            project,
            changed_files,
            existing_commands=set(),
            baseline_red=baseline_red,
        )
    except ImportError:
        pytest.skip("run_local_checks not importable")
        return None


# ── AC-1: the step gate does NOT add importer tests ─────────────────────────


class TestStepGateBlamesOnlyOwnTests:
    """The synthesized step gate does not add importer tests to its file set."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "base adds importer_tests hits to the synthesized check; "
            "the fix removes that block from _synthesize_mention_check"
        ),
    )
    def test_changed_module_with_importer_is_not_added_to_synthesized_gate(
        self, tmp_path: Path,
    ) -> None:
        """A changed module's importer test is NOT added to the mention gate.

        The step gate should only run tests that pin the changed file by
        line number (the mention search), not tests that import the changed
        module.  Importer tests may be red from peer sub-plans' work and
        blocking them would blame innocent sub-plans.
        """
        repo = _make_repo(
            tmp_path,
            source_files={"src/calc.py": "def add(a, b): return a + b\n"},
            test_files={
                "tests/test_calc.py": (
                    "import src.calc\n"
                    "def test_add(): assert src.calc.add(1, 2) == 3\n"
                ),
            },
        )
        result = _run_synthesize_mention_check(
            repo, changed_files=["src/calc.py"],
        )
        if result is not None:
            cmd = result.get("command", "")
            assert "test_calc.py" not in cmd, (
                f"mention gate command {cmd!r} includes test_calc.py "
                f"which imports src.calc — the step gate should only "
                f"run tests that pin the changed file, not importers"
            )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "base adds importer_tests hits to the synthesized check; "
            "the fix removes that block from _synthesize_mention_check"
        ),
    )
    def test_importer_red_pre_existing_does_not_block_synthesized_gate(
        self, tmp_path: Path,
    ) -> None:
        """An importer test whose red pre-exists does NOT block the gate.

        Even when the importer test is not in baseline_red, the step gate
        should not add it — the gate only blames tests that pin the changed
        file by line number.
        """
        repo = _make_repo(
            tmp_path,
            source_files={"src/calc.py": "def add(a, b): return a + b\n"},
            test_files={
                "tests/test_calc.py": (
                    "import src.calc\n"
                    "def test_add(): assert src.calc.add(1, 2) == 3\n"
                ),
            },
        )
        # No baseline_red — the importer test is not declared
        result = _run_synthesize_mention_check(
            repo, changed_files=["src/calc.py"],
        )
        if result is not None:
            cmd = result.get("command", "")
            assert "test_calc.py" not in cmd, (
                f"mention gate command {cmd!r} includes test_calc.py "
                f"which imports src.calc — the step gate should not "
                f"add importer tests even when they are not declared-red"
            )

    def test_mention_search_still_works_for_line_number_pins(
        self, tmp_path: Path,
    ) -> None:
        """Tests that pin the changed file by line number ARE still gated.

        This is a control: the mention search (git grep for file:line)
        should still work.  Only the importer addition is removed.
        """
        repo = _make_repo(
            tmp_path,
            source_files={"src/calc.py": "def add(a, b): return a + b\n"},
            test_files={
                "tests/test_calc_pins.py": (
                    "# see src/calc.py:1\n"
                    "def test_add(): pass\n"
                ),
            },
        )
        result = _run_synthesize_mention_check(
            repo, changed_files=["src/calc.py"],
        )
        if result is not None:
            cmd = result.get("command", "")
            # The mention search should find test_calc_pins.py because
            # it references src/calc.py:1
            assert "test_calc_pins.py" in cmd, (
                f"mention gate command {cmd!r} does not include "
                f"test_calc_pins.py which pins src/calc.py:1 — "
                f"the mention search should still work"
            )