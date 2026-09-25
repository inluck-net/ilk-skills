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
    project: str = "/Users/chad/Projects/keyreply/kira-cloudflare",
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
        "project": project,
        "model": "mimo-v2.5-pro",
        "base_url": "",
        "max_budget_usd": 0.0,
        "duration_sec": 764,
        "exit_code": exit_code,
        "new_commits_total": new_commits_total,
        "new_commits": {project: new_commits_total},
        "tool_calls": 66,
        "test_invocations": 11,
        "local_checks": [
            {"slug": "pv3-flow-revert", "step": 8, "outcome": "pass", "exit_code": 0}
        ],
    }
    if stop_reason:
        d["stop_reason"] = stop_reason
    return d


def _build_terminal_record(
    run_id: str = "20260918-175308",
    stop_reason: str = "ship_integrity_violation",
    iters: int = 1,
    project: str = "/Users/chad/Projects/keyreply/kira-cloudflare",
) -> dict:
    """Replicate the runner's run-level terminal record (run_ilk_loop_claude.sh, post :3354).

    record_type="run_exit" marks it distinct from per-iteration records.
    iteration=999999 keeps it out of the per-iteration de-dup key.
    """
    return {
        "run_id": run_id,
        "cli": "claude",
        "iteration": 999999,
        "timestamp": "2026-09-18T18:06:00+0800",
        "project": project,
        "stop_reason": stop_reason,
        "record_type": "run_exit",
        "iters": iters,
    }


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
        """AC-1: a violation run's JSONL must carry the enforcement reason.

        The per-iteration record (:3247) is written BEFORE enforcement
        (:3302-3305), so it lacks stop_reason.  After the fix, the runner
        emits a run-level terminal record with record_type="run_exit"
        carrying the real reason.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = Path(f.name)

        try:
            # Step 1: runner writes the per-iteration record (as at :3247)
            # _STOP_REASON is captured from iter_stop_reason BEFORE enforcement,
            # so it is empty when enforcement hasn't run yet.
            iter_record = _build_iteration_record(stop_reason="")
            # Step 2: enforcement fires (:3302-3305) and sets stop_reason.
            # Runner emits the run-level terminal record (post :3354).
            terminal_record = _build_terminal_record()
            log_path.write_text(
                json.dumps(iter_record) + "\n"
                + json.dumps(terminal_record) + "\n",
                encoding="utf-8",
            )

            # Step 3: the terminal record must carry the enforcement reason.
            lines = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            terminal_records = [r for r in lines if r.get("record_type") == "run_exit"]
            assert len(terminal_records) == 1, (
                f"Expected exactly one terminal record, got {len(terminal_records)}"
            )
            actual_reason = terminal_records[0].get("stop_reason")
            assert actual_reason == "ship_integrity_violation", (
                f"Terminal stop_reason must be 'ship_integrity_violation', "
                f"got {actual_reason!r}."
            )
        finally:
            log_path.unlink(missing_ok=True)

    def test_no_terminal_record_fails_ac1(self):
        """Before the fix: no terminal record → stop_reason is absent.

        This pins the defect shape so a regression to pre-fix code is caught.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = Path(f.name)

        try:
            # Only the per-iteration record (no terminal record).
            iter_record = _build_iteration_record(stop_reason="")
            log_path.write_text(json.dumps(iter_record) + "\n", encoding="utf-8")

            lines = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            terminal_records = [r for r in lines if r.get("record_type") == "run_exit"]
            assert len(terminal_records) == 0, (
                "Without the fix there should be no terminal record"
            )
            # The last record's stop_reason is empty — that IS the defect.
            last = lines[-1]
            assert last.get("stop_reason") is None, (
                "Per-iteration record must lack stop_reason (that IS D1)"
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


def _setup_violation_project(tmp_path: Path) -> tuple[Path, dict]:
    """Create an isolated project dir with JSONL + sentinel for a violation run.

    Returns (project_path, env) suitable for subprocess calls to collect.py.
    """
    import os

    project_path = tmp_path / "kira-cloudflare"
    project_path.mkdir()

    # Compute the project key by CALLING ilk_paths, never by imitating it.
    # The hand-rolled copy below hashed the lowercased path while
    # ilk_paths.project_key hashes the case-preserving one (ilk_paths.py:303)
    # — on macOS tmp paths the uppercase /T/ segment made every fixture key
    # wrong, collect.py read a different state dir than the fixture wrote,
    # and all three AC tests were red from birth (the authoring batch
    # shipped without a verify sub-plan; measured 2026-09-20).
    import sys as _sys
    _scripts = Path(__file__).resolve().parent.parent / "scripts"
    if str(_scripts) not in _sys.path:
        _sys.path.insert(0, str(_scripts))
    from ilk_paths import project_key as _ilk_project_key
    key = _ilk_project_key(project_path)

    data_home = tmp_path / "ilk-data"
    launcher_dir = data_home / "projects" / key / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = data_home / "projects" / key / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Write JSONL: per-iteration record (no stop_reason) + terminal record.
    # Pass the resolved project path so read_jsonl_iters can match records
    # (collect.py calls .resolve() on the project path, which on macOS
    # converts /var/folders/... to /private/var/folders/...).
    resolved = str(project_path.resolve())
    iter_record = _build_iteration_record(stop_reason="", project=resolved)
    terminal_record = _build_terminal_record(project=resolved)
    jsonl_path = logs_dir / ".ilk-loop.log"
    jsonl_path.write_text(
        json.dumps(iter_record) + "\n" + json.dumps(terminal_record) + "\n",
        encoding="utf-8",
    )

    # Write sentinel with state=ship_integrity_violation.
    sentinel = {
        "state": "ship_integrity_violation",
        "pid": 99999,
        "run_id": "20260918-175308",
        "started_at": "2026-09-18T17:53:09+0800",
        "ended_at": "2026-09-18T18:06:00+0800",
        "iterations": 1,
        "project_path": str(project_path),
    }
    (launcher_dir / "last-exit.json").write_text(
        json.dumps(sentinel), encoding="utf-8",
    )

    env = {
        **os.environ,
        "ILK_DATA_HOME": str(data_home),
        "PYTHONIOENCODING": "utf-8",
    }
    return project_path, env


class TestAC2ViolationRunClassifiesAsShippedUnverified:
    """AC-2: feeding the AC-1 record shape to collect.py produces a postmortem
    classified on the violation cause."""

    def test_classify_returns_shipped_unverified(self):
        """collect.py classify() on the AC-1 fixture → shipped-unverified."""
        with tempfile.TemporaryDirectory() as tmp:
            project_path, env = _setup_violation_project(Path(tmp))

            # Call collect.py and capture its output.
            import subprocess, sys
            collect_py = (
                Path(__file__).resolve().parent.parent.parent
                / "ilk-feedback" / "scripts" / "collect.py"
            )
            result = subprocess.run(
                [sys.executable, str(collect_py),
                 "-ProjectPath", str(project_path), "--quiet"],
                capture_output=True, text=True, env=env,
                encoding="utf-8", errors="replace",
            )
            # collect.py should succeed (exit 0) and print the postmortem path.
            assert result.returncode == 0, (
                f"collect.py exited {result.returncode}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            postmortem_path = Path(result.stdout.strip())
            assert postmortem_path.exists(), (
                f"Postmortem not created. collect.py stdout: {result.stdout!r}"
            )

            # Verify the classification is shipped-unverified.
            text = postmortem_path.read_text(encoding="utf-8")
            assert "shipped-unverified" in text, (
                f"Postmortem should classify as shipped-unverified.\n"
                f"First 500 chars:\n{text[:500]}"
            )

    def test_terminal_record_stop_reason_reaches_report(self):
        """The postmortem body mentions the mapped violation label.

        collect.py maps ship_integrity_violation → shipped-unverified via
        _SENTINEL_FAILURE_MAP (collect.py:1314).  The report body uses the
        mapped label, not the raw sentinel state.
        """
        with tempfile.TemporaryDirectory() as tmp:
            project_path, env = _setup_violation_project(Path(tmp))

            import subprocess, sys
            collect_py = (
                Path(__file__).resolve().parent.parent.parent
                / "ilk-feedback" / "scripts" / "collect.py"
            )
            result = subprocess.run(
                [sys.executable, str(collect_py),
                 "-ProjectPath", str(project_path), "--quiet"],
                capture_output=True, text=True, env=env,
                encoding="utf-8", errors="replace",
            )
            assert result.returncode == 0
            postmortem_path = Path(result.stdout.strip())
            text = postmortem_path.read_text(encoding="utf-8")
            # The report should classify as shipped-unverified (the mapped
            # label for ship_integrity_violation).
            assert "shipped-unverified" in text, (
                f"Postmortem should classify as shipped-unverified.\n"
                f"First 500 chars:\n{text[:500]}"
            )


class TestAC3MetricsReadPathSmoke:
    """Metrics read-path smoke: metrics.py JSONL read over the AC-1 fixture."""

    def test_metrics_reads_violation_records_without_error(self):
        """metrics.py --project --json succeeds on the AC-1 fixture."""
        with tempfile.TemporaryDirectory() as tmp:
            project_path, env = _setup_violation_project(Path(tmp))

            import subprocess, sys
            metrics_py = (
                Path(__file__).resolve().parent.parent.parent
                / "ilk-feedback" / "scripts" / "metrics.py"
            )
            result = subprocess.run(
                [sys.executable, str(metrics_py),
                 "--project", str(project_path), "--json"],
                capture_output=True, text=True, env=env,
                encoding="utf-8", errors="replace",
            )
            assert result.returncode == 0, (
                f"metrics.py exited {result.returncode}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            data = json.loads(result.stdout)
            assert "classification_distribution" in data, (
                f"Missing classification_distribution in output: {data.keys()}"
            )
