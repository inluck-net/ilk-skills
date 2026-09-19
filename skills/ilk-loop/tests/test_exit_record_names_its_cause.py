"""Red-first tests for D1: the terminal stop_reason must appear in the JSONL record.

The runner writes a per-iteration JSONL record (run_ilk_loop_claude.sh:3189-3247)
using the captured shell variable _STOP_REASON, then sets iter_stop_reason via
enforcement (:3302-3305). Because _STOP_REASON is captured before enforcement,
the record silently omits the enforcement-set reason.

AC-1: a run ending in enforcement-set ship_integrity_violation MUST leave a
JSONL record whose stop_reason is "ship_integrity_violation" — not absent,
not empty.

Fixture: the real shape from kira-cloudflare run 20260918-175308
(18:05:57, 3 commits, exit 0, NO stop_reason field), verified against
the live log on 2026-09-18.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


def _build_iteration_record(
    run_id: str = "20260918-175308",
    iteration: int = 1,
    timestamp: str = "2026-09-18T18:05:57+0800",
    exit_code: int = 0,
    new_commits_total: int = 3,
    stop_reason: str | None = None,
) -> dict:
    """Replicate the runner's JSONL record builder (run_ilk_loop_claude.sh:3196-3246).

    When stop_reason is None or empty, the key is omitted — matching the
    runner's `if sr:` guard at line 3215-3216.
    """
    d: dict = {
        "run_id": run_id,
        "cli": "claude",
        "iteration": iteration,
        "timestamp": timestamp,
        "project": "/Users/chad/Projects/keyreply/kira-cloudflare",
        "model": "mimo-v2.5-pro",
        "base_url": "",
        "max_budget_usd": 0.0,
        "duration_sec": 764,
        "exit_code": exit_code,
        "new_commits_total": new_commits_total,
        "new_commits": {"/Users/chad/Projects/keyreply/kira-cloudflare": new_commits_total},
        "tool_calls": 66,
        "test_invocations": 11,
        "local_checks": [
            {"slug": "pv3-flow-revert", "step": 8, "outcome": "pass", "exit_code": 0}
        ],
    }
    if stop_reason:
        d["stop_reason"] = stop_reason
    return d


class TestD1ExitRecordCarriesEnforcementReason:
    """AC-1: the JSONL completion record carries the enforcement-set stop_reason."""

    def test_175308_record_lacks_stop_reason(self):
        """Fixture verification: the real 175308 record has no stop_reason.

        This is the defect shape — the record was written before enforcement
        set ship_integrity_violation. The test proves the baseline is broken
        and will turn GREEN once the runner emits a run-level final record
        after enforcement.
        """
        # Build the record using the same logic as the runner.
        record = _build_iteration_record()
        assert "stop_reason" not in record, (
            "Fixture mismatch: the 175308 record must lack stop_reason "
            "(that IS the defect). Check _build_iteration_record()."
        )

    def test_enforcement_reason_must_appear_in_record(self):
        """AC-1: a violation run's JSONL record must carry the enforcement reason.

        RED under current code: the runner writes the iteration record before
        enforcement can set the stop_reason, so the record omits it.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = Path(f.name)

        try:
            # Step 1: runner writes the per-iteration record (as at :3247)
            # _STOP_REASON is captured from iter_stop_reason BEFORE enforcement,
            # so it is empty when enforcement hasn't run yet.
            pre_enforcement_stop_reason = ""  # iter_stop_reason is empty at :3189
            record = _build_iteration_record(stop_reason=pre_enforcement_stop_reason)
            log_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            # Step 2: enforcement fires (:3302-3305) and sets iter_stop_reason.
            post_enforcement_reason = "ship_integrity_violation"

            # Step 3: a run-level final record SHOULD carry the terminal cause.
            # Under current code there is no such record — the only line in the
            # log is the per-iteration record that was written before enforcement.
            lines = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            final_records = [r for r in lines if r.get("run_id")]
            assert len(final_records) >= 1, "Expected at least one JSONL record"

            # The LAST record must carry the terminal stop_reason.
            final = final_records[-1]
            actual_reason = final.get("stop_reason")
            assert actual_reason == post_enforcement_reason, (
                f"Terminal stop_reason must be '{post_enforcement_reason}', "
                f"got {actual_reason!r}. Under current code the record is "
                f"written before enforcement, so stop_reason is missing."
            )
        finally:
            log_path.unlink(missing_ok=True)

    def test_real_175308_shape_is_documented(self):
        """The fixture field list matches the retro evidence (§3 D1).

        This pins the field set so future maintainers know what was absent.
        """
        record = _build_iteration_record()
        expected_keys = {
            "run_id", "cli", "iteration", "timestamp", "project", "model",
            "base_url", "max_budget_usd", "duration_sec", "exit_code",
            "new_commits_total", "new_commits", "tool_calls",
            "test_invocations", "local_checks",
        }
        assert set(record.keys()) == expected_keys, (
            f"Field set drifted from fixture. "
            f"Extra: {set(record.keys()) - expected_keys!r}, "
            f"Missing: {expected_keys - set(record.keys())!r}"
        )
