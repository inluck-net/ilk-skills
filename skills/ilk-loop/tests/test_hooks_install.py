"""Test the full-suite guardrail hook's core behaviours.

AC-7: ILK_ALLOW_FULL_SUITE=1 escape hatch works both inline and exported.
AC-8: A full-suite pytest command is denied with permissionDecision: deny.

These tests pin the behaviours that the rest of the sub-plan must not break.
They invoke the hook script directly with representative input.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-full-suite.sh"


def _run_hook(command: str, env: dict[str, str] | None = None) -> dict:
    """Run the hook with a synthetic Bash event and return the parsed JSON output."""
    event = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    run_env = os.environ.copy()
    # Clear the escape-hatch env var unless the caller explicitly sets it
    run_env.pop("ILK_ALLOW_FULL_SUITE", None)
    if env:
        run_env.update(env)
    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=event,
        capture_output=True,
        text=True,
        env=run_env,
        timeout=10,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}: {result.stderr}"
    # Empty stdout means the hook allowed (no deny payload)
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


# ── AC-8: deny payload ──────────────────────────────────────────────────────

class TestDenyPayload:
    """An unscoped full-suite pytest command is denied."""

    def test_unscoped_pytest_is_denied(self) -> None:
        """AC-8: bare `pytest` produces permissionDecision: deny."""
        result = _run_hook("pytest")
        assert result["allowed"] is False
        output = result["payload"]
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "guardrail" in output["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_unscoped_python_m_pytest_is_denied(self) -> None:
        """AC-8: `python3 -m pytest` (unscoped) is denied."""
        result = _run_hook("python3 -m pytest")
        assert result["allowed"] is False
        assert result["payload"]["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_scoped_pytest_is_allowed(self) -> None:
        """A pytest run with a path argument is already cheap — allowed."""
        result = _run_hook("pytest skills/ilk-loop/tests/test_foo.py -q")
        assert result["allowed"] is True

    def test_collect_only_is_allowed(self) -> None:
        """--collect-only is cheap — allowed."""
        result = _run_hook("pytest --collect-only -q")
        assert result["allowed"] is True


# ── AC-7: escape hatch ──────────────────────────────────────────────────────

class TestEscapeHatch:
    """ILK_ALLOW_FULL_SUITE=1 permits full-suite runs."""

    def test_inline_escape(self) -> None:
        """AC-7: ILK_ALLOW_FULL_SUITE=1 inline in the command."""
        result = _run_hook("ILK_ALLOW_FULL_SUITE=1 pytest")
        assert result["allowed"] is True

    def test_exported_escape(self) -> None:
        """AC-7: ILK_ALLOW_FULL_SUITE=1 exported in the environment."""
        result = _run_hook("pytest", env={"ILK_ALLOW_FULL_SUITE": "1"})
        assert result["allowed"] is True


# ── File integrity ───────────────────────────────────────────────────────────

class TestHookFileIntegrity:
    """The hook file exists and is executable."""

    def test_hook_exists(self) -> None:
        assert HOOK_PATH.exists(), f"hook not found at {HOOK_PATH}"

    def test_hook_is_executable(self) -> None:
        mode = HOOK_PATH.stat().st_mode
        assert mode & stat.S_IXUSR, "hook is not user-executable"

    def test_hook_sha256(self) -> None:
        """Pin the sha256 so a future diff is visible."""
        import hashlib
        digest = hashlib.sha256(HOOK_PATH.read_bytes()).hexdigest()
        # This is the sha256 of the imported file as of 2026-08-14
        assert digest == "c73ee1e8f611afc145dbc94ff164ccaa9b5d312dd634656d0e847f34282be4c4"
