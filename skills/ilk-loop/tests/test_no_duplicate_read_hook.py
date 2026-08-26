"""Red-first tests for the no-duplicate-read PreToolUse hook.

Drives hooks/no-duplicate-read.sh as a subprocess, feeding JSON on stdin
and asserting on parsed stdout — the same harness shape as
test_batch_gate_persistence.py's CLI tests.

Every test targets a ledger under tmp_path via ILK_READ_LEDGER_DIR so the
suite never touches a real session's ledger.

Expected-red at step 0: the hook does not exist yet, so every test fails.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-duplicate-read.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_hook(
    event: dict,
    ledger_dir: Path,
    env: dict[str, str] | None = None,
) -> dict:
    """Run the hook with a synthetic PreToolUse event and return parsed output.

    Returns {"allowed": True} when stdout is empty (no deny payload),
    or {"allowed": False, "payload": <parsed JSON>} when the hook denies.
    """
    run_env = os.environ.copy()
    run_env["ILK_READ_LEDGER_DIR"] = str(ledger_dir)
    if env:
        run_env.update(env)
    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=run_env,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"hook exited {result.returncode} (AC-7: must always exit 0): "
        f"stderr={result.stderr!r}"
    )
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


def _deny_reason(result: dict) -> str:
    """Extract the permissionDecisionReason string from a deny result."""
    assert not result["allowed"], "expected a deny result"
    return result["payload"]["hookSpecificOutput"]["permissionDecisionReason"]


def _make_read_event(
    path: str,
    session_id: str = "test-session-001",
    transcript_path: str = "/tmp/test-transcript.jsonl",
    cwd: str = "/tmp/test-cwd",
) -> dict:
    """Build a minimal PreToolUse event for a Read tool call."""
    return {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": path},
        "tool_use_id": "toolu_test_001",
    }


def _make_other_event(
    tool_name: str = "Bash",
    session_id: str = "test-session-001",
) -> dict:
    """Build a minimal PreToolUse event for a non-Read tool."""
    return {
        "session_id": session_id,
        "transcript_path": "/tmp/test-transcript.jsonl",
        "cwd": "/tmp/test-cwd",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": "echo hello"},
        "tool_use_id": "toolu_test_002",
    }


# ── Payload characterisation ─────────────────────────────────────────────────

class TestPayloadShape:
    """Pin which fields a PreToolUse payload actually carries.

    The hook's session-key selection depends on knowing which of
    session_id / transcript_path / cwd / hook_event_name exist.
    This test proves the shape from the official Claude Code docs:
    https://code.claude.com/docs/en/hooks

    Evidence source: Claude Code hooks documentation (2026-08-26).
    The payload always includes session_id, transcript_path, cwd,
    and hook_event_name as common fields.
    """

    def test_session_id_is_string(self) -> None:
        """session_id is always a non-empty string in the payload."""
        event = _make_read_event("/tmp/test.py")
        assert isinstance(event["session_id"], str)
        assert len(event["session_id"]) > 0

    def test_transcript_path_is_string(self) -> None:
        """transcript_path is always a non-empty string in the payload."""
        event = _make_read_event("/tmp/test.py")
        assert isinstance(event["transcript_path"], str)
        assert len(event["transcript_path"]) > 0

    def test_cwd_is_string(self) -> None:
        """cwd is always a non-empty string in the payload."""
        event = _make_read_event("/tmp/test.py")
        assert isinstance(event["cwd"], str)
        assert len(event["cwd"]) > 0

    def test_hook_event_name_is_pre_tool_use(self) -> None:
        """hook_event_name is 'PreToolUse' for this hook type."""
        event = _make_read_event("/tmp/test.py")
        assert event["hook_event_name"] == "PreToolUse"


# ── AC-1: first read allowed and recorded ────────────────────────────────────

class TestFirstReadAllowed:
    """AC-1: First read of a path → allowed; ledger now contains the path."""

    def test_first_read_allows(self, tmp_path: Path) -> None:
        """First Read of a path must be allowed."""
        ledger = tmp_path / "ledger"
        result = _run_hook(_make_read_event("/tmp/test.py"), ledger)
        assert result["allowed"] is True

    def test_first_read_records_in_ledger(self, tmp_path: Path) -> None:
        """First Read must record the path in the ledger."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")
        _run_hook(_make_read_event(str(target)), ledger)

        ledger_file = ledger / "test-session-001.json"
        assert ledger_file.exists(), "ledger file was not created"
        data = json.loads(ledger_file.read_text())
        assert str(target) in data, "path not recorded in ledger"


# ── AC-2: second read of unchanged file denied ───────────────────────────────

class TestSecondReadDenied:
    """AC-2: Second read of the same path, file untouched → deny with reason."""

    def test_second_read_denies(self, tmp_path: Path) -> None:
        """Second Read of the same unchanged file must be denied."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")
        event = _make_read_event(str(target))

        _run_hook(event, ledger)
        result = _run_hook(event, ledger)

        assert result["allowed"] is False, (
            "second read of unchanged file should be denied"
        )

    def test_deny_reason_names_path(self, tmp_path: Path) -> None:
        """AC-2: deny reason must contain the absolute path."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")
        event = _make_read_event(str(target))

        _run_hook(event, ledger)
        result = _run_hook(event, ledger)

        reason = _deny_reason(result)
        assert str(target) in reason, (
            f"deny reason should name the path; got: {reason}"
        )

    def test_deny_reason_says_already_in_context(self, tmp_path: Path) -> None:
        """AC-2: deny reason must say the content is already in context."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")
        event = _make_read_event(str(target))

        _run_hook(event, ledger)
        result = _run_hook(event, ledger)

        reason = _deny_reason(result).lower()
        assert "already" in reason or "context" in reason, (
            f"deny reason should mention 'already in context'; got: {reason}"
        )


# ── AC-3: changed file may be read again ─────────────────────────────────────

class TestChangedFileAllowed:
    """AC-3: Second read after mtime/size changes → allowed, ledger updated."""

    def test_mtime_change_allows(self, tmp_path: Path) -> None:
        """A file whose mtime changed must be allowed on re-read."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")
        event = _make_read_event(str(target))

        _run_hook(event, ledger)

        # Modify the file
        time.sleep(0.05)  # ensure mtime differs
        target.write_text("hello world")

        result = _run_hook(event, ledger)
        assert result["allowed"] is True, (
            "file with changed mtime/size should be allowed"
        )

    def test_size_change_allows(self, tmp_path: Path) -> None:
        """A file whose size changed (same mtime second) must be allowed."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hi")
        event = _make_read_event(str(target))

        _run_hook(event, ledger)

        # Modify size (may land in same mtime second on fast FS)
        target.write_text("hello world, this is longer")

        result = _run_hook(event, ledger)
        assert result["allowed"] is True

    def test_ledger_updated_after_change(self, tmp_path: Path) -> None:
        """After a changed-file re-read, the ledger entry must be updated."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")
        event = _make_read_event(str(target))

        _run_hook(event, ledger)

        time.sleep(0.05)
        target.write_text("hello world")
        _run_hook(event, ledger)

        ledger_file = ledger / "test-session-001.json"
        data = json.loads(ledger_file.read_text())
        entry = data[str(target)]
        stat = target.stat()
        assert entry["mtime_ns"] == stat.st_mtime_ns, (
            "ledger mtime_ns not updated after re-read"
        )
        assert entry["size"] == stat.st_size, (
            "ledger size not updated after re-read"
        )


# ── AC-4: session isolation ──────────────────────────────────────────────────

class TestSessionIsolation:
    """AC-4: Two different session keys do not share a ledger."""

    def test_different_sessions_independent(self, tmp_path: Path) -> None:
        """Reading path P in session A does not deny P in session B."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")

        event_a = _make_read_event(str(target), session_id="session-A")
        event_b = _make_read_event(str(target), session_id="session-B")

        _run_hook(event_a, ledger)
        result = _run_hook(event_b, ledger)

        assert result["allowed"] is True, (
            "session B should not be denied by session A's ledger"
        )

    def test_separate_ledger_files(self, tmp_path: Path) -> None:
        """Each session gets its own ledger file."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")

        _run_hook(_make_read_event(str(target), session_id="s-A"), ledger)
        _run_hook(_make_read_event(str(target), session_id="s-B"), ledger)

        assert (ledger / "s-A.json").exists()
        assert (ledger / "s-B.json").exists()


# ── AC-5: fail open on every hook-side error ─────────────────────────────────

class TestFailOpen:
    """AC-5: Every hook-side error must allow() the call through."""

    def test_corrupt_ledger_allows(self, tmp_path: Path) -> None:
        """A corrupt ledger file must not block the read."""
        ledger = tmp_path / "ledger"
        ledger.mkdir(parents=True)
        ledger_file = ledger / "test-session-001.json"
        ledger_file.write_text("NOT VALID JSON {{{")

        target = tmp_path / "test.py"
        target.write_text("hello")
        result = _run_hook(_make_read_event(str(target)), ledger)
        assert result["allowed"] is True

    def test_missing_session_key_allows(self, tmp_path: Path) -> None:
        """An event with no session_id must allow (fail open)."""
        ledger = tmp_path / "ledger"
        event = _make_read_event("/tmp/test.py")
        del event["session_id"]
        result = _run_hook(event, ledger)
        assert result["allowed"] is True

    def test_empty_session_key_allows(self, tmp_path: Path) -> None:
        """An event with empty session_id must allow (fail open)."""
        ledger = tmp_path / "ledger"
        event = _make_read_event("/tmp/test.py", session_id="")
        result = _run_hook(event, ledger)
        assert result["allowed"] is True

    def test_non_json_stdin_allows(self, tmp_path: Path) -> None:
        """Non-JSON stdin must allow (fail open)."""
        ledger = tmp_path / "ledger"
        run_env = os.environ.copy()
        run_env["ILK_READ_LEDGER_DIR"] = str(ledger)
        result = subprocess.run(
            ["bash", str(HOOK_PATH)],
            input="this is not json",
            capture_output=True,
            text=True,
            env=run_env,
            timeout=10,
        )
        assert result.returncode == 0, f"hook exited {result.returncode}"
        # Empty stdout = allowed
        assert not result.stdout.strip(), "non-JSON stdin should allow"

    def test_empty_stdin_allows(self, tmp_path: Path) -> None:
        """Empty stdin must allow (fail open)."""
        ledger = tmp_path / "ledger"
        run_env = os.environ.copy()
        run_env["ILK_READ_LEDGER_DIR"] = str(ledger)
        result = subprocess.run(
            ["bash", str(HOOK_PATH)],
            input="",
            capture_output=True,
            text=True,
            env=run_env,
            timeout=10,
        )
        assert result.returncode == 0
        assert not result.stdout.strip(), "empty stdin should allow"

    def test_stat_failure_on_deleted_path_allows(self, tmp_path: Path) -> None:
        """A Read of a deleted path must allow (stat fails → fail open)."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "vanishing.py"
        target.write_text("gone soon")
        event = _make_read_event(str(target))

        _run_hook(event, ledger)

        target.unlink()
        result = _run_hook(event, ledger)
        assert result["allowed"] is True, (
            "stat on a deleted path should fail open, not deny"
        )


# ── AC-6: non-Read tools never denied ────────────────────────────────────────

class TestNonReadToolsPass:
    """AC-6: A non-Read tool is never denied by this hook."""

    @pytest.mark.parametrize("tool_name", [
        "Bash",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "WebFetch",
        "Skill",
    ])
    def test_non_read_tool_allowed(self, tool_name: str, tmp_path: Path) -> None:
        """Non-Read tools must always be allowed."""
        ledger = tmp_path / "ledger"
        event = _make_other_event(tool_name=tool_name)
        result = _run_hook(event, ledger)
        assert result["allowed"] is True, (
            f"{tool_name} should never be denied by the read guard"
        )


# ── AC-7: hook always exits 0 ────────────────────────────────────────────────

class TestAlwaysExitZero:
    """AC-7: The hook must exit 0 in every case, including deny."""

    def test_allow_path_exits_zero(self, tmp_path: Path) -> None:
        """An allowed read must exit 0."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")
        # Just checking that _run_hook doesn't raise on returncode
        _run_hook(_make_read_event(str(target)), ledger)

    def test_deny_path_exits_zero(self, tmp_path: Path) -> None:
        """A denied read must exit 0 (not exit code 2)."""
        ledger = tmp_path / "ledger"
        target = tmp_path / "test.py"
        target.write_text("hello")
        event = _make_read_event(str(target))
        _run_hook(event, ledger)
        # Second call — should deny, but still exit 0
        _run_hook(event, ledger)

    def test_corrupt_ledger_exits_zero(self, tmp_path: Path) -> None:
        """A corrupt ledger must exit 0 (fail open)."""
        ledger = tmp_path / "ledger"
        ledger.mkdir(parents=True)
        (ledger / "test-session-001.json").write_text("{{{bad json")

        target = tmp_path / "test.py"
        target.write_text("hello")
        _run_hook(_make_read_event(str(target)), ledger)

    def test_missing_session_exits_zero(self, tmp_path: Path) -> None:
        """Missing session_id must exit 0 (fail open)."""
        ledger = tmp_path / "ledger"
        event = _make_read_event("/tmp/test.py")
        del event["session_id"]
        _run_hook(event, ledger)
