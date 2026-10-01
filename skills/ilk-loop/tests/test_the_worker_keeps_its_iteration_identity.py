"""Pin that the worker hook refuses changing the iteration identity.

AC-1 (deny): commands that unset / override ILK_ITERATION_SUBPLAN or
             ILK_WORKER_SESSION are refused.

AC-2 (allow): commands that only READ the variables, unset unrelated vars,
              or run the real ship_transition.py are allowed.

The hook is driven as a subprocess with PreToolUse JSON on stdin and
``CLAUDE_CONFIG_DIR`` pinned to ``tmp_path``, the same pattern as
``test_no_live_clone_edit_hook.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-live-clone-edit.py"


# ── helpers ──────────────────────────────────────────────────────────────────


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


@pytest.fixture()
def _home(tmp_path: Path):
    """Create a minimal fake home for hook invocation.

    The hook needs CLAUDE_CONFIG_DIR to resolve clone_root; with no
    skills/ilk-loop symlink it returns None (clone_root) and allows
    everything — exactly the state we want for testing the identity
    guard (which is independent of clone root).
    """
    home = tmp_path / "home"
    home.mkdir()
    return {"home": home}


def _deny_reason(result: dict) -> str | None:
    """Extract the permissionDecisionReason from a denied result."""
    if result.get("allowed"):
        return None
    payload = result.get("payload", {})
    return (payload.get("hookSpecificOutput", {})
                    .get("permissionDecisionReason", ""))


# ---------------------------------------------------------------------------
# AC-1: deny — unset / override ILK_ITERATION_SUBPLAN / ILK_WORKER_SESSION
# ---------------------------------------------------------------------------


class TestDenyIdentityChange:
    """Commands that unset or override the iteration identity must be refused."""

    def test_unset_subplan_and_run(self, _home: dict) -> None:
        """AC-1: unset ILK_ITERATION_SUBPLAN && python3 x.py ⇒ deny."""
        event = _event("Bash", {
            "command": "unset ILK_ITERATION_SUBPLAN && python3 x.py",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False
        reason = _deny_reason(result)
        assert "ILK_ITERATION_SUBPLAN" in reason

    def test_env_u_worker_session(self, _home: dict) -> None:
        """AC-1: env -u ILK_WORKER_SESSION python3 x.py ⇒ deny."""
        event = _event("Bash", {
            "command": "env -u ILK_WORKER_SESSION python3 x.py",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False
        reason = _deny_reason(result)
        assert "ILK_WORKER_SESSION" in reason

    def test_prefix_assignment_subplan(self, _home: dict) -> None:
        """AC-1: ILK_ITERATION_SUBPLAN=other python3 x.py ⇒ deny."""
        event = _event("Bash", {
            "command": "ILK_ITERATION_SUBPLAN=other python3 x.py",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False
        reason = _deny_reason(result)
        assert "ILK_ITERATION_SUBPLAN" in reason

    def test_export_worker_session_empty(self, _home: dict) -> None:
        """AC-1: export ILK_WORKER_SESSION= ⇒ deny."""
        event = _event("Bash", {
            "command": "export ILK_WORKER_SESSION=",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False
        reason = _deny_reason(result)
        assert "ILK_WORKER_SESSION" in reason

    def test_env_i_clears_all(self, _home: dict) -> None:
        """AC-1: env -i bash -c 'python3 x.py' ⇒ deny."""
        event = _event("Bash", {
            "command": "env -i bash -c 'python3 x.py'",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False
        reason = _deny_reason(result)
        # env -i clears everything; reason should mention at least one var
        assert ("ILK_ITERATION_SUBPLAN" in reason
                or "ILK_WORKER_SESSION" in reason)


# ---------------------------------------------------------------------------
# AC-2: allow — reading, unrelated unset, real ship command
# ---------------------------------------------------------------------------


class TestAllowIdentityRead:
    """Commands that only READ the iteration identity must be allowed."""

    def test_echo_subplan(self, _home: dict) -> None:
        """AC-2: echo $ILK_ITERATION_SUBPLAN ⇒ allow."""
        event = _event("Bash", {
            "command": "echo $ILK_ITERATION_SUBPLAN",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_env_grep_ilk(self, _home: dict) -> None:
        """AC-2: env | grep ILK_ ⇒ allow."""
        event = _event("Bash", {
            "command": "env | grep ILK_",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_prefix_assignment_unrelated(self, _home: dict) -> None:
        """AC-2: FOO=1 python3 x.py ⇒ allow."""
        event = _event("Bash", {
            "command": "FOO=1 python3 x.py",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_unset_unrelated(self, _home: dict) -> None:
        """AC-2: unset FOO ⇒ allow."""
        event = _event("Bash", {
            "command": "unset FOO",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_real_ship_command(self, _home: dict) -> None:
        """AC-2: python3 skills/ilk-loop/scripts/ship_transition.py --ship x
        --plans-dir p --repo . ⇒ allow."""
        event = _event("Bash", {
            "command": (
                "python3 skills/ilk-loop/scripts/ship_transition.py"
                " --ship x --plans-dir p --repo ."
            ),
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True