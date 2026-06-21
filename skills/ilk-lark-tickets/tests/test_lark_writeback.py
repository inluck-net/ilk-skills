"""Tests for lark_to_tracker.writeback_status — tracker → Lark 状态 write-back.

All Lark HTTP is mocked — zero network calls, zero real bases.
Covers AC-1 (basic writeback), AC-2 (skip non-lark entries).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the scripts package is importable regardless of cwd.
_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
_FEEDBACK = str(
    Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
)
for _p in (_SCRIPTS, _FEEDBACK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lark_to_tracker  # noqa: E402
import project_tracker  # noqa: E402


# ---------------------------------------------------------------------------
# Fake client (extends the pull adapter's FakeLarkClient with get/update)
# ---------------------------------------------------------------------------

class FakeLarkClient:
    """Fake Lark client with get_record + update_record — no network."""

    def __init__(self, records: dict[str, dict] | None = None):
        """*records* maps record_id → {"fields": {"状态": "..."}}."""
        self._records = records or {}
        self.update_calls: list[tuple[str, dict]] = []
        self.list_calls: list[dict] = []

    def list_records(
        self,
        *,
        filter_expr: dict | None = None,
        max_records: int | None = None,
    ) -> list[dict]:
        self.list_calls.append(filter_expr)
        return [
            {"record_id": rid, "fields": info.get("fields", {})}
            for rid, info in self._records.items()
        ]

    def get_record(self, record_id: str) -> dict:
        info = self._records.get(record_id, {})
        return {"record": {"fields": info.get("fields", {})}}

    def update_record(self, record_id: str, fields: dict) -> dict:
        self.update_calls.append((record_id, fields))
        return {"updated": True}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate ILK_DATA_HOME so tracker writes land in tmp."""
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))
    return {"data_home": tmp_path, "key": "test-project"}


def _seed_tracker(key: str, entries: list[dict]):
    """Seed the tracker with pre-built entry dicts."""
    from project_tracker import tracker_dir
    import improvement_backlog

    td = tracker_dir(key=key)
    td.mkdir(parents=True, exist_ok=True)
    improvement_backlog._save_raw(td, entries)


def _make_entry_dict(
    source_id: str,
    status: str = "open",
    source: str = "lark",
    title: str = "test",
) -> dict:
    """Build a minimal entry dict for seeding."""
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return {
        "id": f"seeded-{source_id}",
        "title": title,
        "kind": "feature",
        "gap": title,
        "evidence": {},
        "proposed_fix": "",
        "leverage": "medium",
        "severity": "medium",
        "status": status,
        "first_seen": now,
        "last_seen": now,
        "seen_count": 1,
        "source": source,
        "source_id": source_id,
        "relations": {},
    }


# ---------------------------------------------------------------------------
# AC-1: basic writeback — tracker shipped → Lark 待验证
# ---------------------------------------------------------------------------

class TestWritebackBasic:
    def test_shipped_entry_writes_back(self, isolated_env):
        """AC-1: tracker entry status='shipped' + source='lark'
        → writeback calls update with 状态='待验证'."""
        _seed_tracker(isolated_env["key"], [
            _make_entry_dict("rec_001", status="shipped"),
        ])

        client = FakeLarkClient({
            "rec_001": {"fields": {"状态": "可执行"}},
        })

        count = lark_to_tracker.writeback_status(
            client,
            key=isolated_env["key"],
        )

        assert count == 1
        assert len(client.update_calls) == 1
        record_id, fields = client.update_calls[0]
        assert record_id == "rec_001"
        assert fields == {"状态": "待验证"}


# ---------------------------------------------------------------------------
# AC-2: skip non-lark entries
# ---------------------------------------------------------------------------

class TestWritebackSkipNonLark:
    def test_non_lark_entries_skipped(self, isolated_env):
        """AC-2: entries with source != 'lark' are skipped."""
        _seed_tracker(isolated_env["key"], [
            _make_entry_dict("rec_001", status="shipped", source="feedback"),
            _make_entry_dict("rec_002", status="shipped", source="github"),
        ])

        client = FakeLarkClient({})

        count = lark_to_tracker.writeback_status(
            client,
            key=isolated_env["key"],
        )

        assert count == 0
        assert len(client.update_calls) == 0

    def test_mixed_sources_only_writes_lark(self, isolated_env):
        """AC-2: mixed sources — only lark entries are written back."""
        _seed_tracker(isolated_env["key"], [
            _make_entry_dict("rec_001", status="shipped", source="lark"),
            _make_entry_dict("rec_002", status="shipped", source="feedback"),
        ])

        client = FakeLarkClient({
            "rec_001": {"fields": {"状态": "可执行"}},
        })

        count = lark_to_tracker.writeback_status(
            client,
            key=isolated_env["key"],
        )

        assert count == 1
        assert len(client.update_calls) == 1
        assert client.update_calls[0][0] == "rec_001"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestWritebackEdgeCases:
    def test_empty_tracker_returns_zero(self, isolated_env):
        """No tracker entries → returns 0, no update calls."""
        client = FakeLarkClient({})

        count = lark_to_tracker.writeback_status(
            client,
            key=isolated_env["key"],
        )

        assert count == 0
        assert len(client.update_calls) == 0
