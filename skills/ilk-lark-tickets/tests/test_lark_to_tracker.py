"""Tests for lark_to_tracker.sync — Lark → per-project tracker PULL adapter.

All Lark HTTP is mocked — zero network calls, zero real bases (AC-5).
Covers AC-1 (basic sync), AC-2 (upsert + Lark-owns-content), AC-3 (kind
mapping), AC-5 (injectable client, no real BitableClient constructed).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the scripts package is importable regardless of cwd.
_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import lark_to_tracker  # noqa: E402


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------

class FakeLarkClient:
    """Fake Lark client that returns canned records — no network."""

    def __init__(self, records: list[dict]):
        self._records = records
        self.list_calls: list[dict] = []

    def list_records(
        self,
        *,
        filter_expr: dict | None = None,
        max_records: int | None = None,
    ) -> list[dict]:
        self.list_calls.append(filter_expr)
        return self._records


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate ILK_DATA_HOME so tracker writes land in tmp."""
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))
    return {"data_home": tmp_path, "key": "test-project"}


# ---------------------------------------------------------------------------
# AC-1: basic sync — 2 records → 2 tracker entries
# ---------------------------------------------------------------------------

class TestSyncBasic:
    def test_creates_entries_from_records(self, isolated_env, monkeypatch):
        """AC-1: fake client returning 2 可执行 records → 2 tracker entries
        with source='lark' and source_id == each record's id."""
        records = [
            {
                "record_id": "rec_001",
                "fields": {
                    "标题": [{"text": "First ticket"}],
                    "紧急度": "高",
                },
            },
            {
                "record_id": "rec_002",
                "fields": {
                    "标题": [{"text": "Second ticket"}],
                    "紧急度": "低",
                },
            },
        ]
        client = FakeLarkClient(records)

        count = lark_to_tracker.sync(
            client,
            key=isolated_env["key"],
        )

        assert count == 2

        # Verify entries landed in the tracker
        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert len(entries) == 2

        by_source_id = {e.source_id: e for e in entries}
        assert "rec_001" in by_source_id
        assert "rec_002" in by_source_id

        for e in entries:
            assert e.source == "lark"

        assert by_source_id["rec_001"].title == "First ticket"
        assert by_source_id["rec_002"].title == "Second ticket"

    def test_list_filter_uses_status(self, isolated_env):
        """AC-1: sync passes a 状态 filter to list_records."""
        client = FakeLarkClient([])

        lark_to_tracker.sync(
            client,
            key=isolated_env["key"],
            status="可执行",
        )

        assert len(client.list_calls) == 1
        filt = client.list_calls[0]
        assert filt["conjunction"] == "and"
        cond = filt["conditions"][0]
        assert cond["field_name"] == "状态"
        assert cond["value"] == ["可执行"]


# ---------------------------------------------------------------------------
# AC-2: upsert — same records, changed titles → entries updated, not duplicated
# ---------------------------------------------------------------------------

class TestSyncUpsert:
    def test_upsert_refreshes_title(self, isolated_env):
        """AC-2: re-running sync with SAME record_ids but CHANGED titles
        → still 2 entries, titles refreshed, seen_count bumped."""
        records_v1 = [
            {
                "record_id": "rec_001",
                "fields": {"标题": [{"text": "Original title"}]},
            },
            {
                "record_id": "rec_002",
                "fields": {"标题": [{"text": "Other ticket"}]},
            },
        ]
        records_v2 = [
            {
                "record_id": "rec_001",
                "fields": {"标题": [{"text": "Updated title"}]},
            },
            {
                "record_id": "rec_002",
                "fields": {"标题": [{"text": "Other ticket v2"}]},
            },
        ]

        client_v1 = FakeLarkClient(records_v1)
        client_v2 = FakeLarkClient(records_v2)

        # First sync
        lark_to_tracker.sync(client_v1, key=isolated_env["key"])

        from project_tracker import load
        entries_v1 = load(key=isolated_env["key"])
        assert len(entries_v1) == 2
        seen_counts_v1 = {e.source_id: e.seen_count for e in entries_v1}

        # Second sync with changed titles
        lark_to_tracker.sync(client_v2, key=isolated_env["key"])

        entries_v2 = load(key=isolated_env["key"])
        assert len(entries_v2) == 2  # no duplicates

        by_source_id = {e.source_id: e for e in entries_v2}
        assert by_source_id["rec_001"].title == "Updated title"
        assert by_source_id["rec_002"].title == "Other ticket v2"

        # seen_count bumped
        assert by_source_id["rec_001"].seen_count > seen_counts_v1["rec_001"]
        assert by_source_id["rec_002"].seen_count > seen_counts_v1["rec_002"]


# ---------------------------------------------------------------------------
# AC-3: kind mapping from Lark type field
# ---------------------------------------------------------------------------

class TestKindMapping:
    def test_default_kind_is_feature(self, isolated_env):
        """AC-3: no type field → kind defaults to 'feature'."""
        records = [
            {
                "record_id": "rec_no_type",
                "fields": {"标题": [{"text": "No type"}]},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert len(entries) == 1
        assert entries[0].kind == "feature"

    def test_bug_type_maps_to_bug(self, isolated_env):
        """AC-3: 类型=bug → kind='bug'."""
        records = [
            {
                "record_id": "rec_bug",
                "fields": {"标题": [{"text": "A bug"}], "类型": "bug"},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert entries[0].kind == "bug"

    def test_requirement_type_maps_to_feature(self, isolated_env):
        """AC-3: 类型=需求 → kind='feature'."""
        records = [
            {
                "record_id": "rec_req",
                "fields": {"标题": [{"text": "A requirement"}], "类型": "需求"},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert entries[0].kind == "feature"

    def test_unknown_type_defaults_to_feature(self, isolated_env):
        """AC-3: unknown 类型 → kind='feature'."""
        records = [
            {
                "record_id": "rec_unknown",
                "fields": {"标题": [{"text": "Unknown type"}], "类型": "???"},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert entries[0].kind == "feature"


# ---------------------------------------------------------------------------
# AC-5: no live network — injectable client, no real BitableClient
# ---------------------------------------------------------------------------

class TestNoLiveNetwork:
    def test_injectable_client_used_directly(self, isolated_env):
        """AC-5: sync uses the injected client; never constructs a real
        BitableClient."""
        records = [
            {
                "record_id": "rec_001",
                "fields": {"标题": [{"text": "Test"}]},
            },
        ]
        client = FakeLarkClient(records)

        # If lark_to_tracker tried to construct a real BitableClient,
        # this would fail because there's no config.
        with mock.patch("lark_client.BitableClient") as MockBC:
            lark_to_tracker.sync(client, key=isolated_env["key"])
            MockBC.assert_not_called()

    def test_no_http_request_made(self, isolated_env):
        """AC-5: no _request calls during sync."""
        records = [
            {
                "record_id": "rec_001",
                "fields": {"标题": [{"text": "Test"}]},
            },
        ]
        client = FakeLarkClient(records)

        with mock.patch("lark_client._request") as mock_req:
            lark_to_tracker.sync(client, key=isolated_env["key"])
            mock_req.assert_not_called()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_records_returns_zero(self, isolated_env):
        """sync with zero records → returns 0, no tracker entries."""
        client = FakeLarkClient([])

        count = lark_to_tracker.sync(client, key=isolated_env["key"])

        assert count == 0

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert len(entries) == 0

    def test_text_field_as_plain_string(self, isolated_env):
        """Bitable sometimes returns title as a plain string, not segments."""
        records = [
            {
                "record_id": "rec_str",
                "fields": {"标题": "Plain string title"},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert len(entries) == 1
        assert entries[0].title == "Plain string title"

    def test_missing_title_uses_record_id(self, isolated_env):
        """Records with no title get a fallback title like 'lark:<id>'."""
        records = [
            {
                "record_id": "rec_notitle",
                "fields": {},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert len(entries) == 1
        assert entries[0].title == "lark:rec_notitle"


# ---------------------------------------------------------------------------
# Priority mapping
# ---------------------------------------------------------------------------

class TestPriorityMapping:
    def test_high_priority_mapped(self, isolated_env):
        """紧急 → severity='high'."""
        records = [
            {
                "record_id": "rec_high",
                "fields": {"标题": [{"text": "Urgent"}], "紧急度": "紧急"},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert entries[0].severity == "high"

    def test_low_priority_mapped(self, isolated_env):
        """低 → severity='low'."""
        records = [
            {
                "record_id": "rec_low",
                "fields": {"标题": [{"text": "Low"}], "紧急度": "低"},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert entries[0].severity == "low"

    def test_unknown_priority_defaults_to_medium(self, isolated_env):
        """Unknown priority string → severity='medium'."""
        records = [
            {
                "record_id": "rec_unknown",
                "fields": {"标题": [{"text": "Unknown"}], "紧急度": "???"},
            },
        ]
        client = FakeLarkClient(records)

        lark_to_tracker.sync(client, key=isolated_env["key"])

        from project_tracker import load
        entries = load(key=isolated_env["key"])
        assert entries[0].severity == "medium"
