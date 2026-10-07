"""A verify records its contention — concurrent suites and machine load.

Part of MASTER-2026-10-07l.  The contract (pre-resolved):
1. ``measure_contention()`` returns ``{other_suites, load1, cpus}``.
   - ``other_suites`` = number of processes (from ``ps -axo pid,ppid,command``)
     whose command contains `` -m pytest`` and is not in this process's own
     tree.
   - ``load1`` = ``os.getloadavg()[0]``.
   - ``cpus`` = ``os.cpu_count()``.
2. At suite start and end, ``verification_record.py`` records
   ``contention: {start: {other_suites, load1, cpus}, end: {...}}`` in the
   record and the history row.
3. Rendered as one line ``contention: start <n> suites load <x>/<cpus>; end ...``
   next to ``phase_seconds``.
4. Pure observation: no behaviour, priority or timeout changes.  A ``ps``
   failure records ``other_suites: unmeasured``, never 0.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verification_record as vr  # noqa: E402


# ── AC-1a: measure_contention returns the right structure ─────────────────

@pytest.mark.xfail(strict=True, reason="measure_contention does not exist at base")
def test_measure_contention_returns_dict_with_three_keys() -> None:
    """measure_contention() must return {other_suites, load1, cpus}."""
    result = vr.measure_contention()
    assert isinstance(result, dict)
    assert "other_suites" in result
    assert "load1" in result
    assert "cpus" in result


@pytest.mark.xfail(strict=True, reason="measure_contention does not exist at base")
def test_measure_contention_load1_is_float() -> None:
    """load1 must be a float (from os.getloadavg)."""
    result = vr.measure_contention()
    assert isinstance(result["load1"], float)


@pytest.mark.xfail(strict=True, reason="measure_contention does not exist at base")
def test_measure_contention_cpus_is_int_or_none() -> None:
    """cpus must be an int (from os.cpu_count) or None."""
    result = vr.measure_contention()
    cpus = result["cpus"]
    assert cpus is None or isinstance(cpus, int)


@pytest.mark.xfail(strict=True, reason="measure_contention does not exist at base")
def test_measure_contention_other_suites_is_int_or_unmeasured() -> None:
    """other_suites must be an int or the string 'unmeasured'."""
    result = vr.measure_contention()
    os_val = result["other_suites"]
    assert isinstance(os_val, int) or os_val == "unmeasured"


# ── AC-1b: other_suites counts pytest processes, excluding own tree ───────

@pytest.mark.xfail(strict=True, reason="measure_contention / _ps_axo do not exist at base")
def test_other_suites_excludes_own_process() -> None:
    """A process in our own tree must not count as 'other'."""
    my_pid = str(os.getpid())
    # Simulate ps output where only our own process appears.
    fake_ps = f"  {my_pid}     1 python -m pytest tests/\n"
    with patch.object(vr, "_ps_axo", return_value=fake_ps):
        result = vr.measure_contention()
    # Our own process should be excluded.
    assert result["other_suites"] == 0


@pytest.mark.xfail(strict=True, reason="measure_contention / _ps_axo do not exist at base")
def test_other_suites_counts_foreign_pytest() -> None:
    """A pytest process not in our tree counts as 'other'."""
    fake_ps = "  99999     1 python -m pytest other_tests/\n"
    with patch.object(vr, "_ps_axo", return_value=fake_ps):
        result = vr.measure_contention()
    assert result["other_suites"] == 1


@pytest.mark.xfail(strict=True, reason="measure_contention / _ps_axo do not exist at base")
def test_other_suites_ignores_non_pytest() -> None:
    """A process whose command does not contain ' -m pytest' is ignored."""
    fake_ps = "  99999     1 python manage.py runserver\n"
    with patch.object(vr, "_ps_axo", return_value=fake_ps):
        result = vr.measure_contention()
    assert result["other_suites"] == 0


@pytest.mark.xfail(strict=True, reason="measure_contention / _ps_axo do not exist at base")
def test_other_suites_ps_failure_records_unmeasured() -> None:
    """A ps failure records other_suites: unmeasured, never 0."""
    with patch.object(vr, "_ps_axo", return_value=None):
        result = vr.measure_contention()
    assert result["other_suites"] == "unmeasured"


# ── AC-1c: render_record accepts contention and renders it ────────────────

@pytest.mark.xfail(strict=True, reason="render_record does not accept contention param at base")
def test_render_record_accepts_contention_param() -> None:
    """render_record must accept a contention keyword argument."""
    contention = {
        "start": {"other_suites": 0, "load1": 1.5, "cpus": 8},
        "end": {"other_suites": 1, "load1": 2.0, "cpus": 8},
    }
    record_text = vr.render_record(
        batch="test-batch",
        head="abc1234def56789012345678901234567890abcd",
        tree="def56789012345678901234567890abcdef567890",
        base_sha="1234567890abcdef1234567890abcdef12345678",
        invocation="python -m pytest",
        scope={"mode": "full", "reason": "test", "count": 10},
        results={
            "counts": {"total": 10, "passed": 8, "failed": 2, "errors": 0, "skipped": 0},
            "failing_nodes": [],
        },
        at_base={},
        base_red=[],
        head_red=[],
        contention=contention,
    )
    assert "contention:" in record_text


@pytest.mark.xfail(strict=True, reason="render_record does not accept contention param at base")
def test_render_record_contention_line_format() -> None:
    """The contention line must show start and end with suites and load."""
    contention = {
        "start": {"other_suites": 0, "load1": 1.5, "cpus": 8},
        "end": {"other_suites": 2, "load1": 3.0, "cpus": 8},
    }
    record_text = vr.render_record(
        batch="test-batch",
        head="abc1234def56789012345678901234567890abcd",
        tree="def56789012345678901234567890abcdef567890",
        base_sha="1234567890abcdef1234567890abcdef12345678",
        invocation="python -m pytest",
        scope={"mode": "full", "reason": "test", "count": 10},
        results={
            "counts": {"total": 10, "passed": 8, "failed": 2, "errors": 0, "skipped": 0},
            "failing_nodes": [],
        },
        at_base={},
        base_red=[],
        head_red=[],
        contention=contention,
    )
    assert "start 0 suites load 1.5/8" in record_text
    assert "end 2 suites load 3.0/8" in record_text


@pytest.mark.xfail(strict=True, reason="render_record does not accept contention param at base")
def test_render_record_contention_unmeasured() -> None:
    """When ps fails, other_suites is 'unmeasured' in the rendered line."""
    contention = {
        "start": {"other_suites": "unmeasured", "load1": 1.0, "cpus": 4},
        "end": {"other_suites": "unmeasured", "load1": 1.0, "cpus": 4},
    }
    record_text = vr.render_record(
        batch="test-batch",
        head="abc1234def56789012345678901234567890abcd",
        tree="def56789012345678901234567890abcdef567890",
        base_sha="1234567890abcdef1234567890abcdef12345678",
        invocation="python -m pytest",
        scope={"mode": "full", "reason": "test", "count": 10},
        results={
            "counts": {"total": 10, "passed": 8, "failed": 2, "errors": 0, "skipped": 0},
            "failing_nodes": [],
        },
        at_base={},
        base_red=[],
        head_red=[],
        contention=contention,
    )
    assert "unmeasured" in record_text


def test_render_record_contention_none_omits_line() -> None:
    """When contention is None, no contention line appears."""
    record_text = vr.render_record(
        batch="test-batch",
        head="abc1234def56789012345678901234567890abcd",
        tree="def56789012345678901234567890abcdef567890",
        base_sha="1234567890abcdef1234567890abcdef12345678",
        invocation="python -m pytest",
        scope={"mode": "full", "reason": "test", "count": 10},
        results={
            "counts": {"total": 10, "passed": 8, "failed": 2, "errors": 0, "skipped": 0},
            "failing_nodes": [],
        },
        at_base={},
        base_red=[],
        head_red=[],
    )
    assert "contention:" not in record_text


# ── AC-1d: history entry carries contention ───────────────────────────────

@pytest.mark.xfail(strict=True, reason="_append_history_entry does not accept contention param at base")
def test_history_entry_carries_contention(tmp_path: Path) -> None:
    """_append_history_entry must include contention when provided."""
    record = tmp_path / "record.md"
    record.write_text("# test\n", encoding="utf-8")
    contention = {
        "start": {"other_suites": 0, "load1": 1.0, "cpus": 4},
        "end": {"other_suites": 1, "load1": 2.0, "cpus": 4},
    }
    vr._append_history_entry(record, attempt=1, digest="abc123",
                              failing_nodes=[], contention=contention)
    hist_path = record.parent / f"{record.stem}.history.jsonl"
    entry = json.loads(hist_path.read_text(encoding="utf-8").strip())
    assert "contention" in entry
    assert entry["contention"]["start"]["other_suites"] == 0
    assert entry["contention"]["end"]["other_suites"] == 1