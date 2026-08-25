"""Tests for gate_cost — the broad-gate cost instrument.

A measurement tool with no tests is how a wrong number gets trusted. These
pin the properties a before/after comparison depends on:

  * the cut point filters by each ITERATION's start, not by run id, so a run
    that straddles the change being measured is split correctly;
  * an iteration with no parseable timestamp is EXCLUDED and COUNTED, never
    silently folded into an after-window;
  * per-project figures stay separate, because the blend across projects is
    not a like-for-like number.

Fixtures are synthetic: pointing these at the real ~/.ilk-data corpus would
make them non-deterministic and would change meaning every time a loop runs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gate_cost  # noqa: E402


def _write_iter(run: Path, name: str, ts: str | None, commands: list[tuple[str, float]]) -> None:
    """Write an iteration log with *commands* as (command, duration_sec) pairs.

    A tool_use at T and its tool_result at T+duration is exactly the shape
    gate_cost pairs on.
    """
    from datetime import datetime, timedelta
    run.mkdir(parents=True, exist_ok=True)
    lines = []
    if ts is None:
        # Degenerate iteration: records with no timestamp at all (observed in
        # 8 real aborted runs, all under 7KB).
        lines.append(json.dumps({"type": "system", "subtype": "init"}))
    else:
        base = datetime.fromisoformat(ts)
        for i, (cmd, dur) in enumerate(commands):
            tid = f"call_{i}"
            lines.append(json.dumps({
                "timestamp": (base + timedelta(seconds=i)).isoformat(),
                "message": {"content": [
                    {"type": "tool_use", "id": tid, "name": "Bash",
                     "input": {"command": cmd}}]},
            }))
            lines.append(json.dumps({
                "timestamp": (base + timedelta(seconds=i + dur)).isoformat(),
                "message": {"content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": ""}]},
            }))
    (run / name).write_text("\n".join(lines) + "\n")


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A two-project corpus with one run that straddles a cut point."""
    root = tmp_path / "projects"
    # gh-resolve: one pre-cut iteration with a ceiling hit, one post-cut clean
    run = root / "gh-resolve" / "logs" / "runs" / "20260825-110000"
    _write_iter(run, "iter-01.log.jsonl", "2026-08-25T11:00:00+08:00",
                [("python3 -m pytest -q", 600.5)])
    _write_iter(run, "iter-02.log.jsonl", "2026-08-25T13:00:00+08:00",
                [("python3 -m pytest -q", 12.0)])
    # ilk-skills: one post-cut iteration, no broad command at all
    run2 = root / "ilk-skills" / "logs" / "runs" / "20260825-120500"
    _write_iter(run2, "iter-01.log.jsonl", "2026-08-25T12:05:00+08:00",
                [("echo hi", 0.2)])
    return root


def _runs(root: Path, since=None):
    return list(gate_cost._iter_runs(since, root=root))


# ── cut point ────────────────────────────────────────────────────────────────

def test_iterations_are_datable(corpus: Path) -> None:
    """Sanity: the fixture's iterations carry parseable start timestamps."""
    run = corpus / "gh-resolve" / "logs" / "runs" / "20260825-110000"
    assert gate_cost._iter_start_ts(run / "iter-01.log.jsonl") is not None
    assert gate_cost._iter_start_ts(run / "iter-02.log.jsonl") is not None


def test_cut_point_splits_a_straddling_run(corpus: Path) -> None:
    """One run, iterations on both sides of the cut — run id cannot express this.

    Run 20260825-110000 begins at 11:00 (pre-cut) but its iter-02 starts at
    13:00 (post-cut).  Filtering by run id would include or exclude both.
    """
    run = corpus / "gh-resolve" / "logs" / "runs" / "20260825-110000"
    from datetime import datetime
    cut = datetime.fromisoformat("2026-08-25T12:00:00+08:00")
    assert gate_cost._iter_start_ts(run / "iter-01.log.jsonl") <= cut
    assert gate_cost._iter_start_ts(run / "iter-02.log.jsonl") > cut


def test_undatable_iteration_returns_none(tmp_path: Path) -> None:
    """An iteration with no timestamped record must not masquerade as datable.

    It is excluded from an after-window and counted, never silently included —
    an undatable iteration folded into an after-window would let pre-cut data
    contaminate the comparison.
    """
    run = tmp_path / "p" / "logs" / "runs" / "20260825-000000"
    _write_iter(run, "iter-01.log.jsonl", None, [])
    assert gate_cost._iter_start_ts(run / "iter-01.log.jsonl") is None


# ── the metric ───────────────────────────────────────────────────────────────

def test_ceiling_hit_detected_at_the_boundary(corpus: Path) -> None:
    """A 600.5s broad call counts as a ceiling hit; a 12s one does not."""
    run = corpus / "gh-resolve" / "logs" / "runs" / "20260825-110000"
    pre = list(gate_cost._calls(run / "iter-01.log.jsonl"))
    post = list(gate_cost._calls(run / "iter-02.log.jsonl"))
    assert any(d >= gate_cost.CEILING_S for d, _ in pre)
    assert not any(d >= gate_cost.CEILING_S for d, _ in post)


def test_projects_are_reported_separately(corpus: Path) -> None:
    """Per-project separation is load-bearing: blending a cheap project into an
    expensive one drags the average down without anything improving."""
    names = {proj for proj, _ in _runs(corpus)}
    assert names == {"gh-resolve", "ilk-skills"}


def test_missing_root_is_named_not_reported_as_empty(tmp_path: Path) -> None:
    """A missing corpus is a fact about the environment, not zero findings."""
    with pytest.raises(SystemExit) as e:
        _runs(tmp_path / "does-not-exist")
    assert "no project data" in str(e.value)
