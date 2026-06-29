"""Tests for `cli.py update --fields-json` field collection.

Passing non-ASCII (Chinese) field values through PowerShell -> Python argv on a
zh-CN (cp936/GBK) console mangles them, which is why a prior triage session had
to hand-write a one-off helper. `--fields-json` reads values straight from a
UTF-8 file so they never touch argv. `_collect_raw_fields` is the pure merge
helper behind it.
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from cli import _collect_raw_fields  # noqa: E402


def _write_json(tmp_path: Path, obj) -> str:
    p = tmp_path / "fields.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


class TestCollectRawFields:
    def test_json_only_preserves_unicode(self, tmp_path):
        path = _write_json(tmp_path, {"功能模块": "操作不了消除", "状态": "可执行"})
        out = _collect_raw_fields(path, None)
        assert out == {"功能模块": "操作不了消除", "状态": "可执行"}

    def test_field_specs_only(self, tmp_path):
        out = _collect_raw_fields(None, ["状态=可执行", "类型=bug"])
        assert out == {"状态": "可执行", "类型": "bug"}

    def test_field_overrides_json(self, tmp_path):
        path = _write_json(tmp_path, {"状态": "新建", "类型": "bug"})
        out = _collect_raw_fields(path, ["状态=可执行"])
        assert out == {"状态": "可执行", "类型": "bug"}

    def test_json_list_value_kept_as_list(self, tmp_path):
        path = _write_json(tmp_path, {"影响仓库": ["api", "portal"]})
        out = _collect_raw_fields(path, None)
        assert out == {"影响仓库": ["api", "portal"]}

    def test_empty_inputs(self):
        assert _collect_raw_fields(None, None) == {}
        assert _collect_raw_fields(None, []) == {}

    def test_non_object_json_rejected(self, tmp_path):
        path = _write_json(tmp_path, ["not", "an", "object"])
        with pytest.raises(SystemExit):
            _collect_raw_fields(path, None)

    def test_bad_field_spec_rejected(self):
        with pytest.raises(SystemExit):
            _collect_raw_fields(None, ["no-equals-sign"])
