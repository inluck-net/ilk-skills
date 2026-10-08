"""A run_exit record is not an iteration.

The runner writes a per-iteration JSONL record for each iteration, then a
terminal ``record_type="run_exit"`` record carrying the enforcement-set
stop_reason (D1).  ``read_jsonl_iters`` returns both shapes in one list,
de-duplicated by ``(run_id, iteration)``.  The run_exit record uses
``iteration: 999999`` as a sentinel so it never collides with a real
iteration number.

``_classify_core`` counts iterations as ``len(iters)`` — which includes the
run_exit record.  A 3-iteration run with a terminal record reports
``iter_count: 4``, inflating the denominator for error-rate thresholds
(``err_rate >= 0.30 and iter_count >= 3``) and the max-iter-bound check
(``iter_count >= max_iter_configured``).

AC-1: ``_classify_core`` on [iter-1, iter-2, iter-3, run_exit] must return
``iters: 3``, not 4.

AC-2: ``read_jsonl_iters`` on a log containing iter-1, iter-2, iter-3, and
run_exit must return 4 records (the run_exit is still present — it carries
the stop_reason), but classification must count only 3.

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_iter(iteration: int, exit_code: int = 0,
               run_id: str = "20261009-120000",
               project: str = "/tmp/fake-project") -> dict:
    """Build a per-iteration record matching the runner's shape."""
    return {
        "run_id": run_id,
        "cli": "claude",
        "iteration": iteration,
        "timestamp": f"2026-10-09T12:0{iteration}:00+0800",
        "project": project,
        "model": "mimo-v2.5-pro",
        "duration_sec": 120,
        "exit_code": exit_code,
        "new_commits_total": 1,
        "tool_calls": 10,
    }


def _make_run_exit(stop_reason: str = "budget_exhausted",
                   iters: int = 3,
                   run_id: str = "20261009-120000",
                   project: str = "/tmp/fake-project") -> dict:
    """Build a run_exit terminal record (D1 shape)."""
    return {
        "run_id": run_id,
        "cli": "claude",
        "iteration": 999999,
        "timestamp": "2026-10-09T12:10:00+0800",
        "project": project,
        "stop_reason": stop_reason,
        "record_type": "run_exit",
        "iters": iters,
    }


# ── AC-1: _classify_core must not count run_exit as an iteration ─────────────


class TestRunExitIsNotAnIteration:
    """AC-1: _classify_core returns the real iteration count, excluding run_exit."""

    def test_three_iters_plus_run_exit_counts_as_three(self):
        """[iter-1, iter-2, iter-3, run_exit] → iters: 3 (not 4).

        The run_exit record is present (it carries stop_reason) but must not
        inflate the iteration count.  A 3-iteration run that reports
        ``iters: 4`` triggers max-iter-bound one iteration early and
        dilutes the error-rate denominator.
        """
        iters = [
            _make_iter(1),
            _make_iter(2),
            _make_iter(3),
            _make_run_exit(),
        ]
        label, facts = collect._classify_core(iters, last_launch=None,
                                              project_path=Path("/tmp/fake"))
        assert facts["iters"] == 3, (
            f"_classify_core counted {facts['iters']} iterations for "
            f"[iter-1, iter-2, iter-3, run_exit] — expected 3.  "
            f"The run_exit record (iteration=999999) must not be counted."
        )

    def test_two_iters_plus_run_exit_counts_as_two(self):
        """[iter-1, iter-2, run_exit] → iters: 2 (not 3)."""
        iters = [
            _make_iter(1),
            _make_iter(2),
            _make_run_exit(),
        ]
        label, facts = collect._classify_core(iters, last_launch=None,
                                              project_path=Path("/tmp/fake"))
        assert facts["iters"] == 2, (
            f"_classify_core counted {facts['iters']} iterations for "
            f"[iter-1, iter-2, run_exit] — expected 2."
        )

    def test_no_run_exit_unchanged(self):
        """[iter-1, iter-2, iter-3] → iters: 3 (control — no run_exit)."""
        iters = [
            _make_iter(1),
            _make_iter(2),
            _make_iter(3),
        ]
        label, facts = collect._classify_core(iters, last_launch=None,
                                              project_path=Path("/tmp/fake"))
        assert facts["iters"] == 3, (
            f"_classify_core counted {facts['iters']} iterations for "
            f"[iter-1, iter-2, iter-3] — expected 3."
        )

    def test_run_exit_alone_is_zero_iterations(self):
        """[run_exit] → iters: 0 (no real iterations)."""
        iters = [_make_run_exit()]
        label, facts = collect._classify_core(iters, last_launch=None,
                                              project_path=Path("/tmp/fake"))
        assert facts["iters"] == 0, (
            f"_classify_core counted {facts['iters']} iterations for "
            f"[run_exit] — expected 0."
        )


# ── AC-2: read_jsonl_iters still returns the run_exit record ─────────────────


class TestRunExitPreservedInReadJsonlIters:
    """AC-2: read_jsonl_iters returns run_exit records (they carry stop_reason)."""

    def test_run_exit_appears_in_records(self, tmp_path):
        """A log with 3 iters + run_exit yields 4 records (not 3)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Compute project key the same way ilk_paths does.
        from ilk_paths import project_key
        key = project_key(project_dir)

        data_home = tmp_path / "ilk-data"
        logs_dir = data_home / "projects" / key / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        records = [
            _make_iter(1, project=str(project_dir.resolve())),
            _make_iter(2, project=str(project_dir.resolve())),
            _make_iter(3, project=str(project_dir.resolve())),
            _make_run_exit(project=str(project_dir.resolve())),
        ]
        jsonl_path = logs_dir / ".ilk-loop.log"
        jsonl_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        env = {**os.environ, "ILK_DATA_HOME": str(data_home)}
        saved = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["ILK_DATA_HOME"] = str(data_home)
            iters = collect.read_jsonl_iters(project_dir)
        finally:
            if saved is not None:
                os.environ["ILK_DATA_HOME"] = saved
            elif "ILK_DATA_HOME" in os.environ:
                del os.environ["ILK_DATA_HOME"]

        # All 4 records must be present.
        assert len(iters) == 4, (
            f"read_jsonl_iters returned {len(iters)} records for "
            f"[iter-1, iter-2, iter-3, run_exit] — expected 4.  "
            f"The run_exit record carries stop_reason and must not be dropped."
        )
        # The run_exit record must be the last one.
        last = iters[-1]
        assert last.get("record_type") == "run_exit", (
            f"Last record has record_type={last.get('record_type')!r}, "
            f"expected 'run_exit'."
        )

    def test_classify_counts_only_real_iterations(self, tmp_path):
        """End-to-end: classify() on [3 iters + run_exit] reports iters: 3."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        from ilk_paths import project_key
        key = project_key(project_dir)

        data_home = tmp_path / "ilk-data"
        logs_dir = data_home / "projects" / key / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        records = [
            _make_iter(1, project=str(project_dir.resolve())),
            _make_iter(2, project=str(project_dir.resolve())),
            _make_iter(3, project=str(project_dir.resolve())),
            _make_run_exit(project=str(project_dir.resolve())),
        ]
        jsonl_path = logs_dir / ".ilk-loop.log"
        jsonl_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        env = {**os.environ, "ILK_DATA_HOME": str(data_home)}
        saved = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["ILK_DATA_HOME"] = str(data_home)
            iters = collect.read_jsonl_iters(project_dir)
            label, facts = collect.classify(iters, last_launch=None,
                                            project_path=project_dir)
        finally:
            if saved is not None:
                os.environ["ILK_DATA_HOME"] = saved
            elif "ILK_DATA_HOME" in os.environ:
                del os.environ["ILK_DATA_HOME"]

        assert facts.get("iters") == 3, (
            f"classify() reported iters={facts.get('iters')} for "
            f"[iter-1, iter-2, iter-3, run_exit] — expected 3."
        )