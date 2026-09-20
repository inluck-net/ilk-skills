"""Red-first tests: the driver must isolate selfmod batches in a worktree.

AC-1 (falsifier): grep selfmod_worktree in run_ilk_loop_claude.sh.
AC-2 (predicate): selfmod_isolation_required returns true iff PROJECT_PATH
    resolves to the toolkit clone.
AC-3 (idempotency): a second create reuses an existing worktree.

All three tests are expected to FAIL (red-first) until steps 1–2 add
selfmod_isolation_required() and the worktree-creation wiring to the driver.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from test_selfmod_worktree import _create_throwaway_repo


# ── Helpers ──────────────────────────────────────────────────────────────────

_PATH = os.environ.get("PATH", "")


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside root (same pattern as ledger tests)."""
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _toolkit_clone_from_runner() -> Path:
    """Resolve the toolkit clone that _SKILL_ROOT points at."""
    env = _sandbox_env(Path("/tmp"))  # sandbox irrelevant here
    result = subprocess.run(
        ["bash", "-c", (
            f"source '{_SCRIPTS / '_ilk_skill_root.sh'}' && "
            f"_SKILL_ROOT=\"$(ilk_skill_root)\" && "
            f"readlink -f \"$_SKILL_ROOT/..\" 2>/dev/null || "
            f"realpath \"$_SKILL_ROOT/..\" 2>/dev/null || "
            f"echo \"$_SKILL_ROOT/..\""
        )],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, env=env,
    )
    assert result.returncode == 0, (
        f"failed to resolve toolkit clone: {result.stderr}"
    )
    return Path(result.stdout.strip())


def _run_driver_func(
    func_name: str,
    project: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess:
    """Source the driver and call a named function."""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
PROJECT_PATH='{project}'
{func_name}
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",  timeout=120, env=env,
        cwd=str(project),
    )


# ── Tests ────────────────────────────────────────────────────────────────────


class TestSelfmodFalsifier:
    """AC-1: the string 'selfmod_worktree' must appear in the driver script."""

    def test_falsifier_selfmod_worktree_in_driver(self) -> None:
        """AC-1: grep selfmod_worktree in the driver script.

        Before this sub-plan ships, the module is unreachable — this test
        asserts that the falsifier passes (string appears in the driver).
        Currently false (only module + its own test contain the string).
        """
        text = _RUNNER.read_text(encoding="utf-8")
        assert "selfmod_worktree" in text, (
            "selfmod_worktree not yet referenced in driver — falsifier is red"
        )


class TestSelfmodIsolationPredicate:
    """AC-2: predicate true/false on toolkit vs other."""

    def test_predicate_true_for_toolkit_clone(self, tmp_path: Path) -> None:
        """AC-2: selfmod_isolation_required exits 0 for the toolkit clone."""
        toolkit = _toolkit_clone_from_runner()
        assert toolkit.is_dir(), f"toolkit clone not found: {toolkit}"

        env = _sandbox_env(tmp_path)
        result = _run_driver_func("selfmod_isolation_required", toolkit, env)
        assert result.returncode == 0, (
            f"predicate should be true for toolkit clone "
            f"({toolkit}): rc={result.returncode} stderr={result.stderr}"
        )

    def test_predicate_false_for_other_repo(self, tmp_path: Path) -> None:
        """AC-2: selfmod_isolation_required exits non-zero for a non-toolkit repo."""
        other_repo = _create_throwaway_repo(tmp_path)

        env = _sandbox_env(tmp_path)
        result = _run_driver_func("selfmod_isolation_required", other_repo, env)
        # Guard against vacuous pass: the function must actually be defined.
        assert "command not found" not in result.stderr, (
            f"selfmod_isolation_required not yet defined in driver: "
            f"{result.stderr}"
        )
        assert result.returncode != 0, (
            f"predicate should be false for non-toolkit repo "
            f"({other_repo}): rc={result.returncode}"
        )


class TestSelfmodWorktreeIdempotency:
    """AC-3: a second create reuses an existing worktree."""

    def test_create_called_twice_reuses_worktree(self, tmp_path: Path) -> None:
        """Creating the worktree twice must not fork a second one.

        Drives the driver's worktree-creation entry point twice with the
        same PROJECT_PATH.  The second call must succeed (idempotent) and
        only one worktree path must exist.
        """
        toolkit = _toolkit_clone_from_runner()
        assert toolkit.is_dir()

        env = _sandbox_env(tmp_path)
        worktree_root = tmp_path / "worktrees"
        worktree_root.mkdir()

        script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
PROJECT_PATH='{toolkit}'
SELFMOD_WORKTREE_PATH='{worktree_root / "selfmod-batch"}'

# First create — should succeed.
create_selfmod_worktree
RC1=$?

# Second create — must succeed (idempotent).
create_selfmod_worktree
RC2=$?

echo "RC1=$RC1"
echo "RC2=$RC2"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",  timeout=120, env=env,
            cwd=str(toolkit),
        )
        assert result.returncode == 0, (
            f"dot-source or function call failed: {result.stderr}"
        )
        assert "RC1=0" in result.stdout, (
            f"first create failed: {result.stdout}"
        )
        assert "RC2=0" in result.stdout, (
            f"second create should be idempotent: {result.stdout}"
        )
