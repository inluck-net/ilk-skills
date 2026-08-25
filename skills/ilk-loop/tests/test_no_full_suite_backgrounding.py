"""Red-first tests for the no-full-suite hook's backgrounding requirement.

AC-1: ILK_ALLOW_FULL_SUITE=1 foreground → denied (names wait_for_background_output.sh)
AC-2: ILK_ALLOW_FULL_SUITE=1 backgrounded → allowed

Both fail today: the hatch allows unconditionally (lines 75-76 of the hook).

These tests drive the hook script directly with a synthetic PreToolUse event,
following the same contract as test_hooks_install.py.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-full-suite.sh"


def _run_hook(command: str, env: dict[str, str] | None = None) -> dict:
    """Run the hook with a synthetic Bash event and return the parsed output.

    Returns {"allowed": True} when stdout is empty (no deny payload),
    or {"allowed": False, "payload": <parsed JSON>} when the hook denies.
    """
    event = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    run_env = os.environ.copy()
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
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


def _deny_reason(result: dict) -> str:
    """Extract the permissionDecisionReason string from a deny result."""
    assert not result["allowed"], "expected a deny result"
    return result["payload"]["hookSpecificOutput"]["permissionDecisionReason"]


# ── AC-1: foreground + hatch → denied ────────────────────────────────────────

class TestForegroundWithHatchDenied:
    """ILK_ALLOW_FULL_SUITE=1 in a foreground command must be denied.

    Today (before step 2) the hatch allows unconditionally, so these tests
    are expected-red: the hook produces no output → _run_hook returns
    {"allowed": True}.
    """

    def test_inline_hatch_foreground_denied(self) -> None:
        """AC-1: ILK_ALLOW_FULL_SUITE=1 python3 -m pytest -q → denied."""
        result = _run_hook("ILK_ALLOW_FULL_SUITE=1 python3 -m pytest -q")
        assert result["allowed"] is False, (
            "the hatch currently allows a foreground run — it should deny and "
            "name wait_for_background_output.sh"
        )

    def test_deny_reason_names_poll_helper(self) -> None:
        """AC-1: the deny reason must name wait_for_background_output.sh."""
        result = _run_hook("ILK_ALLOW_FULL_SUITE=1 python3 -m pytest -q")
        reason = _deny_reason(result)
        assert "wait_for_background_output.sh" in reason, (
            f"deny reason should name the poll helper; got: {reason}"
        )

    def test_deny_reason_names_redirect_invocation(self) -> None:
        """AC-1: the deny reason must include a concrete redirect-to-file invocation."""
        result = _run_hook("ILK_ALLOW_FULL_SUITE=1 python3 -m pytest -q")
        reason = _deny_reason(result)
        # The deny message should show the backgrounded form, e.g.
        # python3 -m pytest -q > /tmp/... 2>&1 &
        assert ">" in reason or "2>&1" in reason, (
            f"deny reason should show a redirect-to-file invocation; got: {reason}"
        )


# ── AC-2: backgrounded + hatch → allowed ─────────────────────────────────────

class TestBackgroundedWithHatchAllowed:
    """A backgrounded broad run with the escape hatch must be allowed.

    Today (before step 2) the hatch allows unconditionally, so these tests
    PASS — but only because the hatch doesn't check at all. After step 2,
    they must still pass because the command IS backgrounded.
    """

    def test_backgrounded_with_ampersand_allowed(self) -> None:
        """AC-2: command ending with & (backgrounded) + hatch → allowed."""
        result = _run_hook(
            "ILK_ALLOW_FULL_SUITE=1 python3 -m pytest -q > /tmp/pytest_gate.log 2>&1 &"
        )
        assert result["allowed"] is True

    def test_exported_hatch_backgrounded_allowed(self) -> None:
        """AC-2: exported ILK_ALLOW_FULL_SUITE=1 + backgrounded command → allowed."""
        result = _run_hook(
            "python3 -m pytest -q > /tmp/pytest_gate.log 2>&1 &",
            env={"ILK_ALLOW_FULL_SUITE": "1"},
        )
        assert result["allowed"] is True


# ── AC-3: scoped runs still allowed ──────────────────────────────────────────

class TestScopedRunAllowed:
    """A pytest run with a path argument is already cheap — allowed.

    These must be green before and after step 2. The hook must not become
    stricter for scoped work.
    """

    def test_scoped_with_path(self) -> None:
        """AC-3: pytest with a path argument → allowed."""
        result = _run_hook("pytest tests/test_foo.py -q")
        assert result["allowed"] is True

    def test_scoped_with_hatch(self) -> None:
        """AC-3: scoped run with the escape hatch → still allowed."""
        result = _run_hook("ILK_ALLOW_FULL_SUITE=1 pytest tests/test_foo.py -q")
        assert result["allowed"] is True

    def test_scoped_with_k_selector(self) -> None:
        """AC-3: pytest -k selector → allowed (already-cheap form)."""
        result = _run_hook("pytest -k test_foo -q")
        assert result["allowed"] is True

    def test_collect_only(self) -> None:
        """AC-3: --collect-only → allowed (already-cheap form)."""
        result = _run_hook("pytest --collect-only -q")
        assert result["allowed"] is True


# ── AC-4: false-positive guards preserved ─────────────────────────────────────

class TestFalsePositiveGuards:
    """Commands that merely MENTION a runner must not be denied.

    These are real incidents from 2026-08-10 recorded in the hook's own
    comments (lines 78-95). The hook strips quoted substrings and heredoc
    bodies before detecting runners.
    """

    def test_quoted_runner_mention_allowed(self) -> None:
        """AC-4: a runner name inside a quoted string → allowed.

        Real incident: `backlog add --gap "...19 separate pytest invocations..."`
        """
        result = _run_hook(
            'backlog add --gap "...19 separate pytest invocations..."'
        )
        assert result["allowed"] is True

    def test_heredoc_fixture_text_allowed(self) -> None:
        """AC-4: a heredoc body mentioning a runner → allowed.

        Real incident: `cat >> test_file <<'PYEOF'` writing pytest fixtures.
        """
        result = _run_hook(
            "cat >> test_file <<'PYEOF'\n@pytest.fixture\ndef foo(): pass\nPYEOF"
        )
        assert result["allowed"] is True


# ── AC-5: unscoped without hatch still denied ─────────────────────────────────

class TestUnscopedWithoutHatchDenied:
    """An unscoped run without the escape hatch is still denied.

    This is the existing guardrail behaviour that must not change.
    """

    def test_bare_pytest_denied(self) -> None:
        """AC-5: bare `pytest` → denied."""
        result = _run_hook("pytest")
        assert result["allowed"] is False
        reason = _deny_reason(result)
        assert "guardrail" in reason.lower()

    def test_python_m_pytest_denied(self) -> None:
        """AC-5: `python3 -m pytest` → denied."""
        result = _run_hook("python3 -m pytest -q")
        assert result["allowed"] is False
        reason = _deny_reason(result)
        assert "guardrail" in reason.lower()
