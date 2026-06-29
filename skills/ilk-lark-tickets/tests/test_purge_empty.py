"""Tests for empty-record detection + the purge-empty selection logic.

Feishu auto-seeds a freshly-created bitable's default table with blank rows;
every new tracker therefore carried ~10 empty rows that polluted triage. The
fix purges all-empty rows on the init create path and via `cli.py purge-empty`.

These tests cover the pure predicates (`_value_is_nonempty`, `_is_empty_record`,
`_select_empty_records`) without a live/mocked client — same style as
test_pull_new_filter.py.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from cli import _is_empty_record, _select_empty_records, _value_is_nonempty  # noqa: E402


class TestValueIsNonempty:
    def test_none_blank_and_whitespace_are_empty(self):
        assert _value_is_nonempty(None) is False
        assert _value_is_nonempty("") is False
        assert _value_is_nonempty("   ") is False
        assert _value_is_nonempty([]) is False
        assert _value_is_nonempty({}) is False

    def test_text_segment_list_flattens(self):
        assert _value_is_nonempty([{"text": "", "type": "text"}]) is False
        assert _value_is_nonempty([{"text": "real", "type": "text"}]) is True

    def test_url_value(self):
        assert _value_is_nonempty({"link": "", "text": ""}) is False
        assert _value_is_nonempty({"link": "https://x", "text": "x"}) is True

    def test_present_scalars_are_nonempty(self):
        # Errs toward keeping rows that hold any real scalar.
        assert _value_is_nonempty(0) is True
        assert _value_is_nonempty(42) is True
        assert _value_is_nonempty(False) is True
        assert _value_is_nonempty("x") is True


class TestIsEmptyRecord:
    def test_no_fields_is_empty(self):
        assert _is_empty_record({"record_id": "rec1", "fields": {}}) is True
        assert _is_empty_record({"record_id": "rec1"}) is True

    def test_all_blank_fields_is_empty(self):
        rec = {"record_id": "rec1", "fields": {
            "标题": [{"text": "", "type": "text"}],
            "状态": None,
            "涉及模块": "",
        }}
        assert _is_empty_record(rec) is True

    def test_any_real_value_is_not_empty(self):
        rec = {"record_id": "rec1", "fields": {
            "标题": [{"text": "操作不了消除", "type": "text"}],
            "状态": None,
        }}
        assert _is_empty_record(rec) is False


class TestSelectEmptyRecords:
    def test_selects_only_empties_with_ids(self):
        records = [
            {"record_id": "recA", "fields": {}},                       # empty
            {"record_id": "recB", "fields": {"标题": "real"}},          # real
            {"record_id": "recC", "fields": {"状态": None}},            # empty
            {"fields": {}},                                            # empty but no id -> skip
        ]
        assert _select_empty_records(records) == ["recA", "recC"]

    def test_empty_input(self):
        assert _select_empty_records([]) == []
