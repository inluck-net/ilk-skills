"""Runtime gate: steer_hook.sh consume protocol (bash parity of test_steer_hook.py).

Drives steer_hook.sh via a bash subprocess (a REAL runtime gate, not
parse-clean). Same acceptance criteria as the PowerShell gate:

- AC-1: inject-once — an inbox entry is consumed exactly once
- AC-1: no-reinject — a second call does NOT re-inject
- AC-4: crash-recovery — leftover inbox.processing.md reconciles without double-inject
- AC-3: pause-flag — returns Paused when pause.flag present
- Rename retry — rename succeeds on the normal path
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STEER_HOOK = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "steer_hook.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not available"
)

# ── helpers ──────────────────────────────────────────────────────────

def _run_steer_hook(project_key: str, ilk_data_home: Path) -> dict:
    """Run steer_hook.sh and return {InterjectionText, Paused} (PS-shaped)."""
    with tempfile.TemporaryDirectory() as td:
        text_file = Path(td) / "text.out"
        paused_file = Path(td) / "paused.out"
        script = f"""
set -euo pipefail
export ILK_DATA_HOME='{ilk_data_home}'
source '{STEER_HOOK}'
invoke_steer_hook '{project_key}'
printf '%s' "$STEER_PAUSED" > '{paused_file}'
printf '%s' "$STEER_INTERJECTION_TEXT" > '{text_file}'
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=30,
        )
        assert result.returncode == 0, (
            f"steer_hook.sh exited {result.returncode}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        text = text_file.read_text(encoding="utf-8")
        paused = paused_file.read_text(encoding="utf-8").strip()
    return {
        "InterjectionText": text if text != "" else None,
        "Paused": paused == "1",
    }


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
    """Create a tmp ILK_DATA_HOME and return (ilk_data_home, project_key, steer_dir)."""
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
        assert not (steer_dir / "inbox.md").exists()
        assert not (steer_dir / "inbox.processing.md").exists()
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
        assert {r["uuid"] for r in consumed} == {"uuid-001", "uuid-002"}


# ── AC-1 (no-reinject): second call does NOT re-inject ───────────────

class TestNoReinject:
    """A second call does NOT re-inject already-consumed entries."""

    def test_second_call_returns_nothing(self, steer_env):
        ilk_data, key, steer_dir = steer_env
        _write_inbox(steer_dir, [{"uuid": "once-only-uuid", "text": "inject me once"}])

        result1 = _run_steer_hook(key, ilk_data)
        assert result1["InterjectionText"] == "inject me once"

        result2 = _run_steer_hook(key, ilk_data)
        assert result2["InterjectionText"] is None

        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 1

    def test_new_entry_after_consumption(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        _write_inbox(steer_dir, [{"uuid": "first-uuid", "text": "first instruction"}])
        result1 = _run_steer_hook(key, ilk_data)
        assert result1["InterjectionText"] == "first instruction"

        _write_inbox(steer_dir, [{"uuid": "second-uuid", "text": "second instruction"}])
        result2 = _run_steer_hook(key, ilk_data)
        assert result2["InterjectionText"] == "second instruction"

        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 2
        assert {r["uuid"] for r in consumed} == {"first-uuid", "second-uuid"}


# ── AC-4: crash-recovery ─────────────────────────────────────────────

class TestCrashRecovery:
    """Leftover inbox.processing.md reconciles without double-inject."""

    def test_leftover_processing_reconciles(self, steer_env):
        ilk_data, key, steer_dir = steer_env

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

        consumed_path = steer_dir / "inbox.consumed.jsonl"
        record = {"uuid": "already-done", "consumed_at": "2026-07-01T00:00:00Z"}
        consumed_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        processing = steer_dir / "inbox.processing.md"
        processing.write_text(
            "<!-- uuid: already-done -->\nsome text\n",
            encoding="utf-8",
        )

        result = _run_steer_hook(key, ilk_data)

        assert result["InterjectionText"] is None
        assert not processing.exists()
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 1

    def test_leftover_processing_mixed(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        consumed_path = steer_dir / "inbox.consumed.jsonl"
        record = {"uuid": "already-done", "consumed_at": "2026-07-01T00:00:00Z"}
        consumed_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        processing = steer_dir / "inbox.processing.md"
        processing.write_text(
            "<!-- uuid: already-done -->\ntext A\n---\n<!-- uuid: new-uuid -->\ntext B\n",
            encoding="utf-8",
        )

        result = _run_steer_hook(key, ilk_data)

        assert result["InterjectionText"] == "text B"
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 2
        assert {r["uuid"] for r in consumed} == {"already-done", "new-uuid"}


# ── AC-3: pause flag ─────────────────────────────────────────────────

class TestPauseFlag:
    """pause.flag present → Paused, no consumption."""

    def test_pause_flag_returns_paused(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        (steer_dir / "pause.flag").write_text("", encoding="utf-8")
        _write_inbox(steer_dir, [{"uuid": "paused-uuid", "text": "should not inject"}])

        result = _run_steer_hook(key, ilk_data)

        assert result["Paused"] is True
        assert (steer_dir / "inbox.md").exists()
        consumed = _read_consumed(steer_dir)
        assert len(consumed) == 0

    def test_no_pause_flag(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        _write_inbox(steer_dir, [{"uuid": "ok-uuid", "text": "normal inject"}])

        result = _run_steer_hook(key, ilk_data)

        assert result["Paused"] is False
        assert result["InterjectionText"] == "normal inject"


# ── No inbox ─────────────────────────────────────────────────────────

class TestNoInbox:
    """No inbox.md → no interjection, no error."""

    def test_empty_steer_dir(self, steer_env):
        ilk_data, key, steer_dir = steer_env

        result = _run_steer_hook(key, ilk_data)

        assert result["InterjectionText"] is None
        assert result["Paused"] is False
