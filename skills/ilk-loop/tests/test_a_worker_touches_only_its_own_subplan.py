"""Pin that a worker can edit and commit only its own sub-plan.

AC-1: with ILK_ITERATION_SUBPLAN=a, Edit of the plans dir's b.md ⇒ deny.
AC-2: with ILK_ITERATION_SUBPLAN=a, Bash git commit -m "x [plan:b#step-0]"
      ⇒ deny; git reset --soft HEAD~1 ⇒ deny.
AC-3 (control): Edit of a.md, commit with [plan:a#step-1], and any edit
      with ILK_ITERATION_SUBPLAN unset ⇒ allow.

The hook file does not exist yet; AC-1 and AC-2 are xfail(red-first).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-foreign-plan-edit.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _fake_env(tmp_path: Path):
    """Create a fake environment with a plans dir containing a.md and b.md.

    Layout::

        tmp/
          plans/              ← a fake plans dir
            a.md
            b.md
          home/               ← a fake CLAUDE_CONFIG_DIR
            skills/ilk-loop/scripts/
    """
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "a.md").write_text("---\nplan: a\nstatus: pending\n---\n# Plan A\n")
    (plans / "b.md").write_text("---\nplan: b\nstatus: in-progress\n---\n# Plan B\n")

    home = tmp_path / "home"
    (home / "skills" / "ilk-loop" / "scripts").mkdir(parents=True)

    return {"plans": plans, "home": home, "tmp": tmp_path}


def _event(tool_name: str, tool_input: dict | None = None) -> str:
    """Build a JSON event string as Claude Code would."""
    return json.dumps({"tool_name": tool_name,
                       "tool_input": tool_input or {}})


def _run_hook(event: str, env: dict[str, str]) -> dict:
    """Run the hook as a subprocess and return {allowed, payload?, stdout}.

    If the hook file is missing, returns {"allowed": True, "missing": True}.
    """
    if not HOOK_PATH.exists():
        return {"allowed": True, "missing": True}
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=event,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}: {result.stderr}"
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


# ---------------------------------------------------------------------------
# AC-1: Edit/Write of another sub-plan's file ⇒ deny
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="red-first")
class TestDenyForeignPlanEdit:
    """With ILK_ITERATION_SUBPLAN=a, editing b.md in the plans dir must be denied."""

    def test_edit_foreign_plan_file(self, _fake_env: dict) -> None:
        """AC-1: Edit plans/b.md when SUBPLAN=a ⇒ deny."""
        plans = _fake_env["plans"]
        event = _event("Edit", {
            "file_path": str(plans / "b.md"),
            "old_string": "status: in-progress",
            "new_string": "status: pending",
        })
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(_fake_env["home"]),
            "ILK_ITERATION_SUBPLAN": "a",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is False

    def test_write_foreign_plan_file(self, _fake_env: dict) -> None:
        """AC-1: Write plans/b.md when SUBPLAN=a ⇒ deny."""
        plans = _fake_env["plans"]
        event = _event("Write", {
            "file_path": str(plans / "b.md"),
            "content": "---\nplan: b\nstatus: shipped\n---\n",
        })
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(_fake_env["home"]),
            "ILK_ITERATION_SUBPLAN": "a",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is False


# ---------------------------------------------------------------------------
# AC-2: Bash git commit/reset with foreign slug or history rewrite ⇒ deny
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="red-first")
class TestDenyForeignPlanCommit:
    """With ILK_ITERATION_SUBPLAN=a, commits carrying [plan:b#…] and
    history-rewriting commands must be denied."""

    def test_commit_with_foreign_slug(self, _fake_env: dict) -> None:
        """AC-2: git commit -m "x [plan:b#step-0]" ⇒ deny."""
        event = _event("Bash", {
            "command": 'git commit -m "x [plan:b#step-0]"',
        })
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(_fake_env["home"]),
            "ILK_ITERATION_SUBPLAN": "a",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is False

    def test_git_reset_soft(self, _fake_env: dict) -> None:
        """AC-2: git reset --soft HEAD~1 ⇒ deny."""
        event = _event("Bash", {
            "command": "git reset --soft HEAD~1",
        })
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(_fake_env["home"]),
            "ILK_ITERATION_SUBPLAN": "a",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is False

    def test_git_rebase(self, _fake_env: dict) -> None:
        """AC-2: git rebase main ⇒ deny."""
        event = _event("Bash", {
            "command": "git rebase main",
        })
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(_fake_env["home"]),
            "ILK_ITERATION_SUBPLAN": "a",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is False


# ---------------------------------------------------------------------------
# AC-3 (control): own sub-plan edits and unset env ⇒ allow
# ---------------------------------------------------------------------------


class TestAllowOwnPlanAndUnset:
    """Edits to the iteration's own sub-plan and edits with
    ILK_ITERATION_SUBPLAN unset must be allowed."""

    def test_edit_own_plan_file(self, _fake_env: dict) -> None:
        """AC-3: Edit plans/a.md when SUBPLAN=a ⇒ allow."""
        plans = _fake_env["plans"]
        event = _event("Edit", {
            "file_path": str(plans / "a.md"),
            "old_string": "status: pending",
            "new_string": "status: in-progress",
        })
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(_fake_env["home"]),
            "ILK_ITERATION_SUBPLAN": "a",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_commit_with_own_slug(self, _fake_env: dict) -> None:
        """AC-3: git commit -m "x [plan:a#step-1]" ⇒ allow."""
        event = _event("Bash", {
            "command": 'git commit -m "x [plan:a#step-1]"',
        })
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(_fake_env["home"]),
            "ILK_ITERATION_SUBPLAN": "a",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_edit_with_subplan_unset(self, _fake_env: dict) -> None:
        """AC-3: Edit plans/b.md with ILK_ITERATION_SUBPLAN unset ⇒ allow."""
        plans = _fake_env["plans"]
        event = _event("Edit", {
            "file_path": str(plans / "b.md"),
            "old_string": "status: in-progress",
            "new_string": "status: pending",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_env["home"])}
        # Ensure the var is not inherited
        env.pop("ILK_ITERATION_SUBPLAN", None)
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_commit_with_subplan_unset(self, _fake_env: dict) -> None:
        """AC-3: git commit -m "x [plan:b#step-0]" with SUBPLAN unset ⇒ allow."""
        event = _event("Bash", {
            "command": 'git commit -m "x [plan:b#step-0]"',
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_env["home"])}
        env.pop("ILK_ITERATION_SUBPLAN", None)
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_edit_non_plan_file(self, _fake_env: dict) -> None:
        """AC-3: Edit a non-plan file with SUBPLAN=a ⇒ allow."""
        target = _fake_env["tmp"] / "code.py"
        target.write_text("x = 1\n")
        event = _event("Edit", {
            "file_path": str(target),
            "old_string": "x = 1",
            "new_string": "x = 2",
        })
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(_fake_env["home"]),
            "ILK_ITERATION_SUBPLAN": "a",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is True