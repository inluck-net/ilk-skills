"""Sub-plan ``a-slow-verify-row-outranks-features`` step 0 — red-first pins.

Part of MASTER-2026-10-10c (``a-verify-fits-inside-its-gate``), order 3,
row ``backlog-1f31971e202a9f00`` part 1.

The slow-verify row is filed by ``verification_record._check_slo_breach``
(``VERIFY_SLO_S = 900``), not by ``supervisor_emit.py``.  Today its ``gap``
carries the seconds (``"at_base=900 s"``), so ``stable_key(kind, title, gap)``
produces a fresh key per breach and every one of the 4 open rows has
``seen_count 1``.  And ``autoplan_rails.rank`` sorts source tier first
(triage 0, supervisor 1), so a feature row outranks the smoothness signal.

The contract:

1. ``_check_slo_breach`` files ``gap=f"largest phase: {largest_phase}"`` (no
   seconds) and passes ``relations={"smoothness": True, "phase": largest_phase}``.
   Same largest phase -> same key -> ``seen_count`` increments.  Seconds stay
   in ``evidence["phase_seconds"]`` (already there).
2. A different largest phase is a different key, hence a second row.
3. ``autoplan_rails.rank`` sorts a smoothness row first —
   ``relations.smoothness`` truthy OR title starting with
   ``"batch verify exceeded"`` (so the 4 legacy rows rank first with no data
   migration).  Eligibility filters are unchanged.
4. A backlog failure inside ``_check_slo_breach`` still never raises.

The pins:

AC-1 (dedupe)    — two breaches with the same largest phase (``at_base``) and
                   different seconds leave ONE row, ``seen_count == 2``, gap
                   free of the seconds, relations carrying smoothness/phase.
                   RED AT BASE (gap keys on the seconds: 2 rows x seen 1).
AC-2 (new phase) — after those two, a breach whose largest phase is ``suite``
                   makes a SECOND row (total 2).  RED AT BASE on exactly
                   ``len(rows) == 2``: the first two calls already split.
AC-3 (rank)      — ``rank()`` over [feature row (triage, seen 5), slow-verify
                   row (supervisor, seen 1)] puts the slow-verify row first;
                   a legacy row (no relations, title "batch verify exceeded
                   15 min") is also first.  RED AT BASE: source tier puts
                   triage ahead of supervisor.
AC-4 (contract)  — a backlog failure never raises.  PASSES AT BASE (the
                   try/except is already there) — left unpinned as the
                   design's falsifier; say so here rather than claim a red.

Each test pins HOME / ILK_DATA_HOME / ILK_DATA_DIR to a tmp dir: the backlog
resolves through ``ilk_paths.ilk_data_root()``, and an unpinned data home
would file into the real ``~/.ilk-data``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SELF_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_LOOP_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
_FEEDBACK_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
)
for _p in (_SELF_SCRIPTS, _LOOP_SCRIPTS, _FEEDBACK_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import autoplan_rails  # noqa: E402
import improvement_backlog  # noqa: E402
import verification_record as vr  # noqa: E402

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


# ── env pin: keep every write inside the tmp data home ──────────────────────


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME / ILK_DATA_HOME / ILK_DATA_DIR to tmp_path.

    ``improvement_backlog._backlog_dir()`` resolves through
    ``ilk_paths.ilk_data_root()``, which checks ``ILK_DATA_HOME`` first and
    ``Path.home()`` last — pin both, and drop the ``ILK_DATA_DIR`` alias so it
    cannot win.  ``ILK_WORKER_SESSION`` is cleared: nothing here should look
    like a live worker session.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_home = tmp_path / "data"
    data_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)


# ── helpers ──────────────────────────────────────────────────────────────────


def _file_breach(phase_seconds: dict[str, int], *, batch: str = "test-batch") -> None:
    """File one slow-verify row the way the verify does, into the pinned home."""
    vr._check_slo_breach(batch, "abc1234", Path("record.md"), phase_seconds)


def _rows() -> list:
    """Every backlog row written under the pinned data home."""
    return improvement_backlog.load()


def _phase_breach(phase: str, secs: int, *, total: int = 1100) -> dict[str, int]:
    """A breach whose largest phase is *phase* (well over VERIFY_SLO_S)."""
    phases = {"suite": 50, "at_base": 60, "head_reruns": 10}
    phases[phase] = secs
    phases["total"] = total
    return phases


def _make_rank_entry(
    *,
    cid: str,
    title: str,
    source: str,
    seen_count: int,
    relations: dict | None,
    first_seen: str | None = None,
) -> dict:
    """A minimal backlog-shaped entry for ``rank()``."""
    if first_seen is None:
        first_seen = (NOW - timedelta(days=2)).isoformat()
    entry = {
        "id": cid,
        "title": title,
        "gap": "g",
        "source": source,
        "status": "open",
        "seen_count": seen_count,
        "first_seen": first_seen,
        "evidence": {},
        "proposed_fix": "",
        "kind": "toolkit",
        "leverage": "high",
        "severity": "high",
    }
    if relations is not None:
        entry["relations"] = relations
    return entry


def _feature_row() -> dict:
    """A feature row that would outrank the slow-verify row on source tier."""
    return _make_rank_entry(
        cid="feat-1",
        title="Missing feature X",
        source="triage",
        seen_count=5,
        relations={"autoplan_attempts": 0},
    )


def _slow_verify_row() -> dict:
    """A freshly filed slow-verify row (relations.smoothness)."""
    return _make_rank_entry(
        cid="slo-1",
        title="batch verify exceeded 15 min",
        source="supervisor",
        seen_count=1,
        relations={"smoothness": True, "phase": "at_base"},
    )


def _legacy_slow_verify_row() -> dict:
    """One of the 4 open legacy rows: no relations, title prefix only."""
    return _make_rank_entry(
        cid="slo-legacy",
        title="batch verify exceeded 15 min",
        source="supervisor",
        seen_count=1,
        relations=None,
    )


# ── AC-1: same largest phase dedupes to one row ─────────────────────────────


def test_ac1_same_phase_breaches_leave_one_row() -> None:
    """Two breaches, largest phase ``at_base``, different seconds -> 1 row."""
    _file_breach(_phase_breach("at_base", 900))
    _file_breach(_phase_breach("at_base", 950))

    rows = _rows()
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
    assert rows[0].seen_count == 2, f"seen_count {rows[0].seen_count} != 2"
    # The key is the phase, not the seconds.
    assert "900" not in rows[0].gap and "950" not in rows[0].gap, rows[0].gap
    assert rows[0].relations.get("smoothness") is True
    assert rows[0].relations.get("phase") == "at_base"
    # The seconds are not lost — they stay in the evidence.
    assert rows[0].evidence.get("phase_seconds", {}).get("at_base") in (900, 950)


# ── AC-2: a different largest phase is a second row ─────────────────────────


def test_ac2_a_different_largest_phase_makes_a_second_row() -> None:
    """After the two same-phase breaches, ``suite`` is a distinct key."""
    _file_breach(_phase_breach("at_base", 900))
    _file_breach(_phase_breach("at_base", 950))
    _file_breach(_phase_breach("suite", 1200))

    rows = _rows()
    assert len(rows) == 2, f"expected 2 rows, got {len(rows)}"
    by_phase = {r.relations.get("phase"): r for r in rows}
    assert set(by_phase) == {"at_base", "suite"}, sorted(by_phase)
    assert by_phase["at_base"].seen_count == 2
    assert by_phase["suite"].seen_count == 1


# ── AC-3: a slow-verify row outranks a feature row ──────────────────────────


def test_ac3_a_slow_verify_row_outranks_features() -> None:
    """``rank()`` puts the smoothness row (and the legacy row) first."""
    ranked = autoplan_rails.rank([_feature_row(), _slow_verify_row()], now=NOW)
    assert [e["id"] for e in ranked] == ["slo-1", "feat-1"]

    ranked_legacy = autoplan_rails.rank(
        [_feature_row(), _legacy_slow_verify_row()], now=NOW
    )
    assert [e["id"] for e in ranked_legacy] == ["slo-legacy", "feat-1"]


# ── AC-4: a backlog failure never raises (existing contract) ────────────────


def test_ac4_a_backlog_failure_never_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``add_candidate`` raising must not escape ``_check_slo_breach``."""

    def _raising(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(improvement_backlog, "add_candidate", _raising)
    _file_breach(_phase_breach("at_base", 900))  # must not raise
