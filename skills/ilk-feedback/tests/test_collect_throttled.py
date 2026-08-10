"""Tests for rate-limit event counting in collect.py (AC-9).

Covers recording the rate-limit signal — the count appears in the JSONL
summary facts and the postmortem body.  This step is independently useful
even if the ``throttled`` label (Step 4) is reconsidered.

AC-9: rate-limit event counts appear in the JSONL summary and the postmortem.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E400


# -- Fixtures ----------------------------------------------------------------

def _make_rate_limit_event(
    session_id: str = "20260727-071103",
    rate_limit_type: str = "five_hour",
    status: str = "allowed",
) -> dict:
    """Build a synthetic rate-limit event record matching the real stream shape."""
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "resetsAt": 1779939600,
            "rateLimitType": rate_limit_type,
            "overageStatus": "rejected",
            "overageDisabledReason": "out_of_credits",
            "isUsingOverage": False,
        },
        "session_id": session_id,
    }


def _make_iter(
    run_id: str = "20260727-071103",
    iteration: int = 1,
    stop_reason: str | None = "no-progress",
    exit_code: int | None = 0,
    new_commits_total: int = 0,
    num_turns: int = 5,
    input_tokens: int = 2000,
    output_tokens: int = 500,
) -> dict:
    """Build a synthetic JSONL iteration record."""
    return {
        "run_id": run_id,
        "iteration": iteration,
        "stop_reason": stop_reason,
        "exit_code": exit_code,
        "new_commits_total": new_commits_total,
        "num_turns": num_turns,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


# -- AC-9: Rate-limit event count in facts ----------------------------------

class TestRateLimitEventCount:
    """Rate-limit event counts must appear in the classification facts."""

    def test_count_rate_limit_events_zero(self, tmp_path):
        """No rate-limit events → count is 0 (or absent from facts)."""
        # Write a normal iteration JSONL (no rate-limit events).
        jsonl = tmp_path / ".ilk-loop.log"
        rec = _make_iter()
        rec["project"] = str(tmp_path)
        jsonl.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        count = collect.count_rate_limit_events("20260727-071103", tmp_path)
        assert count == 0

    def test_count_rate_limit_events_nonzero(self, tmp_path):
        """Multiple rate-limit events → correct count."""
        jsonl = tmp_path / ".ilk-loop.log"
        lines = []
        # 5 rate-limit events for the target run
        for _ in range(5):
            evt = _make_rate_limit_event(session_id="20260727-071103")
            evt["project"] = str(tmp_path)
            lines.append(json.dumps(evt))
        # 1 rate-limit event for a different run (should not be counted)
        other_evt = _make_rate_limit_event(session_id="other-run")
        other_evt["project"] = str(tmp_path)
        lines.append(json.dumps(other_evt))
        # A normal iteration record (should not be counted)
        iter_rec = _make_iter()
        iter_rec["project"] = str(tmp_path)
        lines.append(json.dumps(iter_rec))

        jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Pass last_launch with jsonl_log hint so _jsonl_log_candidates finds it.
        last_launch = {"jsonl_log": str(jsonl)}
        count = collect.count_rate_limit_events("20260727-071103", tmp_path, last_launch)
        assert count == 5, f"Expected 5 rate-limit events, got {count}"

    def test_count_ignores_other_projects(self, tmp_path):
        """Rate-limit events from a different project are not counted."""
        jsonl = tmp_path / ".ilk-loop.log"
        evt = _make_rate_limit_event(session_id="20260727-071103")
        evt["project"] = "/some/other/project"
        jsonl.write_text(json.dumps(evt) + "\n", encoding="utf-8")

        count = collect.count_rate_limit_events("20260727-071103", tmp_path)
        assert count == 0


# -- AC-9: Rate-limit count in postmortem ------------------------------------

class TestRateLimitInPostmortem:
    """The rate-limit event count must appear in the rendered postmortem."""

    def test_render_report_includes_rate_limit_count(self, tmp_path):
        """When facts contain rate_limit_event_count, the postmortem shows it."""
        facts = {
            "iter_at_stop": 4,
            "rate_limit_event_count": 13,
        }
        report = collect.render_report(
            project_path=tmp_path,
            project_name="test-proj",
            run_id="20260727-071103",
            iters=[_make_iter(iteration=i) for i in range(1, 5)],
            last_launch=None,
            label="stuck-no-progress",
            facts=facts,
            rec_max=30,
            rec_to=30,
            rationale="test",
            tail=[],
        )
        assert "Rate-limit events" in report, (
            f"Postmortem must include rate-limit event count.\n{report[:1000]}"
        )
        assert "13" in report, (
            f"Postmortem must show the count value 13.\n{report[:1000]}"
        )

    def test_render_report_omits_when_zero(self, tmp_path):
        """When rate_limit_event_count is 0 or absent, the row is omitted."""
        facts = {"iter_at_stop": 1}
        report = collect.render_report(
            project_path=tmp_path,
            project_name="test-proj",
            run_id="20260727-071103",
            iters=[_make_iter()],
            last_launch=None,
            label="stuck-no-progress",
            facts=facts,
            rec_max=30,
            rec_to=30,
            rationale="test",
            tail=[],
        )
        assert "Rate-limit events" not in report, (
            f"Postmortem should NOT include rate-limit row when count is 0.\n{report[:1000]}"
        )
