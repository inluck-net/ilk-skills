"""Tests for the worker-model bench's grading and log parsing (no model calls)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import model_bench  # noqa: E402


def _stream(answer: str, served: str = "mimo-v2.6-pro") -> list[str]:
    return [
        "[claude-code:unrecognized_model] {\"model\":\"mimo-v2.6-pro\"}",
        json.dumps({"type": "system", "subtype": "init", "model": served}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "x" * 120}, {"type": "text", "text": "hi"}]}}),
        json.dumps({"type": "result", "is_error": False, "num_turns": 4,
                    "duration_api_ms": 20000, "result": answer,
                    "modelUsage": {served: {"outputTokens": 800}}}),
    ]


def test_answer_key_matches_its_source():
    assert model_bench.answer_key_drift() == []


def test_answer_key_drift_is_reported(tmp_path):
    src = tmp_path / "ilk_paths.py"
    src.write_text("def nothing_here(): pass\n")
    assert model_bench.answer_key_drift(src) == model_bench.ANSWER_KEY


def test_parse_counts_turns_thinking_and_skips_stderr_lines():
    answer = ("host-compat.json with linked_worktree_key == clone, read by "
              "_linked_worktree_keys_follow_clone (ilk_paths.py:168)")
    row = model_bench.parse_stream(_stream(answer))
    assert row["served_models"] == ["mimo-v2.6-pro"]
    assert row["s_per_turn"] == 5.0
    assert row["out_tok_per_turn"] == 200
    assert row["think_chars"] == 120
    assert row["correct"] is True


def test_partial_answer_is_not_correct():
    row = model_bench.parse_stream(_stream("it reads host-compat.json"))
    assert row["correct"] is False
    assert "_linked_worktree_keys_follow_clone" in row["missing"]


def test_missing_result_event_is_not_correct():
    row = model_bench.parse_stream(["not json"])
    assert row["correct"] is False
    assert row["s_per_turn"] is None
