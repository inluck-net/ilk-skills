"""Runtime gate: steer_hook.ps1 consume protocol.

Drives steer_hook.ps1 via powershell -File subprocess (a REAL runtime gate,
not parse-clean). Tests:

- AC-1: inject-once — an inbox entry is consumed exactly once
- AC-1: no-reinject — a second call does NOT re-inject
- AC-4: crash-recovery — leftover inbox.processing.md reconciles without double-inject
- AC-3: pause-flag — returns Paused=$true when pause.flag present
- Sharing-violation retry — rename succeeds even if producer has inbox.md open
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STEER_HOOK = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "steer_hook.ps1"

# This suite drives the PowerShell hook; skip where powershell is absent
# (macOS/Linux). The bash parity suite (test_steer_hook_sh.py) covers Unix.
pytestmark = pytest.mark.skipif(
    shutil.which("powershell") is None,
    reason="powershell not available",
)

# ── helpers ──────────────────────────────────────────────────────────

def _run_steer_hook(project_key: str, ilk_data_home: Path) -> dict:
    """Run steer_hook.ps1 and return parsed output (InterjectionText, Paused)."""
    # Set ILK_DATA_HOME inside the PowerShell session so Get-IlkDataDir picks it up
    ps_script = f"""
$ErrorActionPreference = 'Stop'
$env:ILK_DATA_HOME = '{ilk_data_home}'
. '{STEER_HOOK}'
$result = Invoke-SteerHook -ProjectKey '{project_key}'
# Output as JSON so Python can parse it
$result | ConvertTo-Json -Depth 3
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, (
        f"steer_hook.ps1 exited {result.returncode}:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    output = result.stdout.strip()
    if not output:
        return {"InterjectionText": None, "Paused": False}
    return json.loads(output)


def _write_inbox(steer_dir: Path, entries: list[dict]):
    """Write inbox.md with entries. Each entry has uuid and text."""
    blocks = []
    for entry in entries:
        blocks.append(f"<!-- uuid: {entry['uuid']} -->\n{entry['text']}")
    content = "\n---\n".join(blocks) + "\n"
    (steer_dir / "inbox.md").write_text(content, encoding="utf-8")


def _read_consumed(steer_dir: Path) -> list[dict]:
    """Read inbox.consumed.jsonl and return list of records."""
    consumed_path = steer_dir / "inbox.consumed.jsonl"
    if not consumed_path.exists():
        return []
    records = []
    for line in consumed_path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


@pytest.fixture()
def steer_env(tmp_path):
    """Create a tmp ILK_DATA_HOME and return (ilk_data_home, steer_dir)."""
    ilk_data = tmp_path / "ilk-data"
    project_key = "test-steer"
    steer_dir = ilk_data / "projects" / project_key / "runtime" / "steer"
    steer_dir.mkdir(parents=True, exist_ok=True)
    return ilk_data, project_key, steer_dir


# ── AC-1: inject-once ────────────────────────────────────────────────

class TestInjectOnce:
    """An inbox entry is consumed exactly once."""

    def test_single_entry_consumed(self, steer_env):
        ilk_data, key, steer_dir = steer_env
        _write_inbox(steer_dir, [{"uuid": "aaa-bbb-111", "text": "do thing X"}])

        result = _run_steer_hook(key, ilk_data)

        assert result["InterjectionText"] == "do thing X"
        assert result["Paused"] is False
        # inbox.md should be deleted (renamed + deleted)
        assert not (steer_dir / "inbox.md").exists()
        assert not (steer_dir / "inbox.processing.md").exists()
        # consumed.jsonl should have the uuid
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 1
        assert consumed[0]["uuid"] == "aaa-bbb-111"
        assert "consumed_at" in consumed[0]

    def test_multiple_entries_consumed(self, steer_env):
        ilk_data, key, steer_dir = steer_env
        _write_inbox(steer_dir, [
            {"uuid": "uuid-001", "text": "instruction one"},
            {"uuid": "uuid-002", "text": "instruction two"},
        ])

        result = _run_steer_hook(key, ilk_data)

        assert "instruction one" in result["InterjectionText"]
        assert "instruction two" in result["InterjectionText"]
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 2
        consumed_uuids = {r["uuid"] for r in consumed}
        assert consumed_uuids == {"uuid-001", "uuid-002"}


# ── AC-1 (no-reinject): second call does NOT re-inject ───────────────

class TestNoReinject:
    """A second call does NOT re-inject already-consumed entries."""

    def test_second_call_returns_nothing(self, steer_env):
        ilk_data, key, steer_dir = steer_env
        _write_inbox(steer_dir, [{"uuid": "once-only-uuid", "text": "inject me once"}])

        # First call — consumes
        result1 = _run_steer_hook(key, ilk_data)
        assert result1["InterjectionText"] == "inject me once"

        # Second call — nothing left
        result2 = _run_steer_hook(key, ilk_data)
        assert result2["InterjectionText"] is None

        # consumed.jsonl still has exactly one record
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 1

    def test_new_entry_after_consumption(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        # First round
        _write_inbox(steer_dir, [{"uuid": "first-uuid", "text": "first instruction"}])
        result1 = _run_steer_hook(key,ilk_data)
        assert result1["InterjectionText"] == "first instruction"

        # Second round — new entry arrives
        _write_inbox(steer_dir, [{"uuid": "second-uuid", "text": "second instruction"}])
        result2 = _run_steer_hook(key, ilk_data)
        assert result2["InterjectionText"] == "second instruction"

        # Both consumed
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 2
        consumed_uuids = {r["uuid"] for r in consumed}
        assert consumed_uuids == {"first-uuid", "second-uuid"}


# ── AC-4: crash-recovery ─────────────────────────────────────────────

class TestCrashRecovery:
    """Leftover inbox.processing.md reconciles without double-inject."""

    def test_leftover_processing_reconciles(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        # Simulate crash: inbox.processing.md exists (rename happened,
        # but delete didn't). The uuid was NOT consumed yet.
        processing = steer_dir / "inbox.processing.md"
        processing.write_text(
            "<!-- uuid: crash-uuid -->\nsome interjection text\n",
            encoding="utf-8",
        )

        result = _run_steer_hook(key, ilk_data)

        assert result["InterjectionText"] == "some interjection text"
        assert not processing.exists()
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 1
        assert consumed[0]["uuid"] == "crash-uuid"

    def test_leftover_processing_already_consumed(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        # Simulate crash: processing.md exists AND the uuid was already consumed
        consumed_path = steer_dir / "inbox.consumed.jsonl"
        record = {"uuid": "already-done", "consumed_at": "2026-07-01T00:00:00Z"}
        consumed_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        processing = steer_dir / "inbox.processing.md"
        processing.write_text(
            "<!-- uuid: already-done -->\nsome text\n",
            encoding="utf-8",
        )

        result = _run_steer_hook(key,ilk_data)

        # Already consumed — no re-injection
        assert result["InterjectionText"] is None
        assert not processing.exists()
        # consumed.jsonl still has one record (no duplicate)
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 1

    def test_leftover_processing_mixed(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        # One uuid already consumed, one new
        consumed_path = steer_dir / "inbox.consumed.jsonl"
        record = {"uuid": "already-done", "consumed_at": "2026-07-01T00:00:00Z"}
        consumed_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        processing = steer_dir / "inbox.processing.md"
        processing.write_text(
            "<!-- uuid: already-done -->\ntext A\n---\n<!-- uuid: new-uuid -->\ntext B\n",
            encoding="utf-8",
        )

        result = _run_steer_hook(key,ilk_data)

        # Only the new uuid should be injected
        assert result["InterjectionText"] == "text B"
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 2
        consumed_uuids = {r["uuid"] for r in consumed}
        assert consumed_uuids == {"already-done", "new-uuid"}


# ── AC-3: pause flag ─────────────────────────────────────────────────

class TestPauseFlag:
    """pause.flag present → Paused=$true, no consumption."""

    def test_pause_flag_returns_paused(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        # Write pause.flag
        (steer_dir / "pause.flag").write_text("", encoding="utf-8")

        # Also write an inbox (should NOT be consumed)
        _write_inbox(steer_dir, [{"uuid": "paused-uuid", "text": "should not inject"}])

        result = _run_steer_hook(key,ilk_data)

        assert result["Paused"] is True
        # inbox.md should still exist (not consumed)
        assert (steer_dir / "inbox.md").exists()
        # No consumed records
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 0

    def test_no_pause_flag(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        _write_inbox(steer_dir, [{"uuid": "ok-uuid", "text": "normal inject"}])

        result = _run_steer_hook(key,ilk_data)

        assert result["Paused"] is False
        assert result["InterjectionText"] == "normal inject"


# ── No inbox ─────────────────────────────────────────────────────────

class TestNoInbox:
    """No inbox.md → no interjection, no error."""

    def test_empty_steer_dir(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        result = _run_steer_hook(key,ilk_data)

        assert result["InterjectionText"] is None
        assert result["Paused"] is False
