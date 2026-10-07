"""A load timeout at base is recorded as "unmeasured-at-base", never "failed".

gh-resolve 07a's verify recorded test_plan_lint_findings_identical as failed at
base — a load timeout (passes alone in 0.26 s).  Its worker added it to
baseline_red.  A timeout under load is not a measurement; it must never excuse
a failure as pre-existing.

  AC-1  _classify_single_at_base_verdict(..., timed_out=True) → unmeasured-at-base
  AC-2  pytest output with `Timeout (>…` from pytest-timeout → unmeasured-at-base;
        output with AssertionError → failed
  AC-3  verify_attribution 5-column: unmeasured-at-base goes through red==runs
        (not skipped like failed)
  AC-4  rendered record with unmeasured-at-base row has no suggested baseline_red
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verification_record as vr  # noqa: E402
import verify_attribution as va  # noqa: E402


RUNNER = "python3 -m pytest"
NID = "tests/test_example.py::test_it"
WT = Path("/tmp/fake-worktree")


# ── AC-1: timed_out=True → unmeasured-at-base ────────────────────────────────


class TestTimedOutVerdict:
    """A whole-process timeout at base must never classify as "failed"."""

    def test_timed_out_is_unmeasured(self) -> None:
        """timed_out=True must return unmeasured-at-base, not failed."""
        result = vr._classify_single_at_base_verdict(
            rc=1, stdout="", stderr="", timed_out=True,
            runner=RUNNER, wt=WT, nid=NID,
        )
        assert result == "unmeasured-at-base"

    def test_not_timed_out_failure_stays_failed(self) -> None:
        """A non-timeout failure (rc!=0, no timeout flag) stays 'failed'."""
        result = vr._classify_single_at_base_verdict(
            rc=1, stdout="FAILED\n", stderr="", timed_out=False,
            runner=RUNNER, wt=WT, nid=NID,
        )
        assert result == "failed"

    def test_passing_run_stays_passed(self) -> None:
        """rc=0, not timed out → passed (control)."""
        result = vr._classify_single_at_base_verdict(
            rc=0, stdout="PASSED\n", stderr="", timed_out=False,
            runner=RUNNER, wt=WT, nid=NID,
        )
        assert result == "passed"


# ── AC-2: pytest-timeout kill in output → unmeasured-at-base ──────────────────


TIMEOUT_STDOUT = (
    "FAILED tests/test_example.py::test_it - Failed: Timeout (>17.0s) "
    "from pytest-timeout.\n"
    "1 failed in 17.15s\n"
)

ASSERT_STDOUT = (
    "FAILED tests/test_example.py::test_it - assert 1 == 2\n"
    "1 failed in 0.05s\n"
)


class TestTimeoutKillInOutput:
    """A pytest-timeout kill in the output is not a real failure."""

    def test_timeout_kill_in_output_is_unmeasured(self) -> None:
        """Output containing `Timeout (>…` from pytest-timeout is unmeasured."""
        result = vr._classify_single_at_base_verdict(
            rc=1, stdout=TIMEOUT_STDOUT, stderr="", timed_out=False,
            runner=RUNNER, wt=WT, nid=NID,
        )
        assert result == "unmeasured-at-base"

    def test_assertion_failure_stays_failed(self) -> None:
        """A real AssertionError failure stays 'failed' (control)."""
        result = vr._classify_single_at_base_verdict(
            rc=1, stdout=ASSERT_STDOUT, stderr="", timed_out=False,
            runner=RUNNER, wt=WT, nid=NID,
        )
        assert result == "failed"


# ── AC-3: verify_attribution treats unmeasured-at-base like passed ────────────


def _signed_5col_section(body_lines: list[str]) -> str:
    """Build a minimal signed 5-column at-base section for parse_rows."""
    header = (
        "| node id | at base | in baseline_red | head reruns | batch touched file |\n"
        "|---|---|---|---|---|\n"
    )
    return header + "\n".join(body_lines) + "\n"


class TestAttributionWithUnmeasuredAtBase:
    """unmeasured-at-base is NOT pre-existing — it goes through red==runs."""

    def test_unmeasured_red_all_attributed(self) -> None:
        """2/2 red + unmeasured-at-base → attributed (red every time)."""
        section = _signed_5col_section([
            "| tests/test_it.py::test_it | unmeasured-at-base | no | 2/2 | no |",
        ])
        rows = va.parse_rows(section)
        bad, flaky = va.derive_attributed(rows)
        assert bad, "2/2 red should be attributed"
        assert bad[0][0] == "tests/test_it.py::test_it"

    def test_unmeasured_intermittent_no_touch_flaky(self) -> None:
        """1/2 red, no batch touch → flaky_owed (intermittent, not attributed)."""
        section = _signed_5col_section([
            "| tests/test_it.py::test_it | unmeasured-at-base | no | 1/2 | no |",
        ])
        rows = va.parse_rows(section)
        bad, flaky = va.derive_attributed(rows)
        assert not bad, "intermittent without batch touch should be flaky, not bad"
        assert "tests/test_it.py::test_it" in flaky

    def test_unmeasured_no_reruns_batch_touched_attributed(self) -> None:
        """0/0 reruns but batch touched → attributed."""
        section = _signed_5col_section([
            "| tests/test_it.py::test_it | unmeasured-at-base | no | 0/0 | yes |",
        ])
        rows = va.parse_rows(section)
        bad, flaky = va.derive_attributed(rows)
        assert bad, "batch-touched with unmeasured-at-base should be attributed"

    def test_failed_at_base_is_skipped(self) -> None:
        """failed at base → pre-existing, not attributed (control)."""
        section = _signed_5col_section([
            "| tests/test_it.py::test_it | failed | no | — | — |",
        ])
        rows = va.parse_rows(section)
        bad, flaky = va.derive_attributed(rows)
        assert not bad, "failed at base is pre-existing, not attributed"
        assert "tests/test_it.py::test_it" not in flaky


# ── AC-4: unmeasured-at-base never suggested for baseline_red ────────────────


def _render_with_at_base(at_base_map: dict[str, str]) -> str:
    """Render a record with the given at_base verdicts."""
    n = len(at_base_map)
    results = {
        "counts": {"total": n, "passed": 0, "failed": n, "skipped": 0,
                   "errors": 0, "xfailed": 0, "xpassed": 0},
        "failing_nodes": list(at_base_map.keys()),
    }
    failed_at_base = [nid for nid, v in at_base_map.items() if v == "failed"]
    scope = {"mode": "targeted", "count": n, "reason": "test"}
    return vr.render_record(
        batch="test-batch", head="abc123", tree="tree456",
        base_sha="deadbee", invocation="python3 -m pytest -n 8",
        scope=scope, results=results,
        at_base=at_base_map, base_red=[], head_red=[],
        failed_at_base=failed_at_base or None,
    )


class TestNoSuggestedBaselineRedForUnmeasured:
    """unmeasured-at-base ids must never appear in suggested baseline_red."""

    def test_unmeasured_not_suggested(self) -> None:
        """A record with unmeasured-at-base has no suggested baseline_red."""
        record = _render_with_at_base({
            "tests/test_it.py::test_it": "unmeasured-at-base",
        })
        assert "suggested baseline_red" not in record

    def test_failed_is_suggested(self) -> None:
        """A record with failed at base DOES have suggested baseline_red (control)."""
        record = _render_with_at_base({
            "tests/test_it.py::test_it": "failed",
        })
        assert "suggested baseline_red" in record
        assert "tests/test_it.py::test_it" in record