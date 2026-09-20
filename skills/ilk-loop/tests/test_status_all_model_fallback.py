"""_latest_jsonl_model walks backwards past empty-model records.

The panel's ``worker model:`` line is fed by the most recent JSONL record
that actually names a model. A latest-record read blanks the line whenever
the newest record was written by an older runner that never populated the
field — measured 2026-09-20, when codex-era records masked a live worker's
model mid-iteration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from status_all import _latest_jsonl_model  # noqa: E402


def test_empty_latest_model_falls_back_to_prior_record(tmp_path):
    log = tmp_path / ".ilk-loop.log"
    log.write_text(
        json.dumps({"iteration": 1, "model": "mimo-v2.5-pro"}) + "\n"
        + json.dumps({"iteration": 2, "model": ""}) + "\n",
        encoding="utf-8",
    )
    assert _latest_jsonl_model(tmp_path) == "mimo-v2.5-pro"


def test_all_empty_models_yield_blank(tmp_path):
    (tmp_path / ".ilk-loop.log").write_text(
        json.dumps({"model": ""}) + "\n" + json.dumps({"model": ""}) + "\n",
        encoding="utf-8",
    )
    assert _latest_jsonl_model(tmp_path) == ""


def test_newest_named_model_wins_over_older_one(tmp_path):
    (tmp_path / ".ilk-loop.log").write_text(
        json.dumps({"model": "old-model"}) + "\n"
        + json.dumps({"model": ""}) + "\n"
        + json.dumps({"model": "new-model"}) + "\n",
        encoding="utf-8",
    )
    assert _latest_jsonl_model(tmp_path) == "new-model"
