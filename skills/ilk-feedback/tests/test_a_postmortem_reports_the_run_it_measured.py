"""A postmortem reports measured launch and gate state.

Regression for gh-resolve run 20261011-061345: the generated postmortem said
the canonical last-launch.json was missing, invented a 30-minute timeout for a
60-minute run, stamped the first iteration's completion as the launch start,
and reported the sum of iteration durations as if it were wall time.  The
run_exit record's synthetic iteration 999999 was listed as a sixth worker
iteration.

Step 0 delivers AC-1 (matching launch evidence, unknown stays unknown) and
AC-2 (measured time: wall vs iteration sum; run_exit is not an iteration).
Step 1 extends the narrative consumer cases.

Fixtures are minimal sanitized copies of the 20261011-061345 shape.  HOME,
ILK_DATA_HOME and ILK_DATA_DIR are pinned together in every spawned process;
nothing touches the operator's real ~/.ilk-data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"

# Same regex as ilk_paths.project_key — duplicated here to avoid import path gymnastics.
_KEY_PUNCT = re.compile(r"[^a-z0-9]+")
_KEY_HASH_LEN = 7
_KEY_SLUG_MAX = 80 - 1 - _KEY_HASH_LEN  # 72

# Fixture identity, mirroring run 20261011-061345.
RUN_ID = "20261011-061345"
LAUNCH_STAMP = "2026-10-11T06:13:45+0800"  # run_id clock: 06:13:45
RUN_END_STAMP = "2026-10-11T10:03:22+0800"  # sentinel ended_at
# 06:13:45 → 10:03:22 is 13777s = 3h49m37s of complete wall time.
WALL_HMS = "3h49m37s"
# Iteration durations sum to 12179s ≈ 203.0 min — deliberately NOT the wall time.
ITER_DURATIONS = (2837, 2321, 2648, 911, 3462)
assert sum(ITER_DURATIONS) == 12179


def _project_key(project_path: Path) -> str:
    """Mirror ilk_paths.project_key: always append a 7-char SHA1 suffix."""
    abs_str = str(project_path.resolve())
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:_KEY_HASH_LEN]
    slug = _KEY_PUNCT.sub("-", abs_str.lower()).strip("-")[:_KEY_SLUG_MAX].rstrip("-")
    return f"{slug}-{h}" if slug else h


@pytest.fixture()
def scratch_env(tmp_path: Path):
    """Isolated data home + temp project, with every path derived, not guessed."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    project_path = tmp_path / "my-proj"
    project_path.mkdir()

    env = {
        **os.environ,
        "HOME": str(home),
        "ILK_DATA_HOME": str(data_home),
        "ILK_DATA_DIR": str(data_home),
        "PYTHONIOENCODING": "utf-8",
    }
    # The data home's project registry is part of the external layout.  Writing
    # it keeps the sandbox home (no installed skills under it) sufficient —
    # collect must not need a host skill-root install to answer a query.
    (data_home / "projects.json").write_text(
        json.dumps({"projects": []}), encoding="utf-8"
    )
    key = _project_key(project_path)
    return project_path, env, key, data_home


def _logs_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "logs"


def _launcher_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "runtime" / "launcher"


def _selfmod_worktree_path(data_home: Path, key: str) -> Path:
    """The selfmod worktree path: <external_launcher_dir>/worktrees/selfmod-batch."""
    return _launcher_dir(data_home, key) / "worktrees" / "selfmod-batch"


def _write_jsonl(data_home: Path, key: str, records: list[dict]) -> None:
    logs_dir = _logs_dir(data_home, key)
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / ".ilk-loop.log"
    with jsonl_path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _write_last_launch(data_home: Path, key: str, launch: dict) -> None:
    launcher_dir = _launcher_dir(data_home, key)
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-launch.json").write_text(json.dumps(launch), encoding="utf-8")


def _write_launcher_log(data_home: Path, key: str, run_id: str, body: str) -> Path:
    launcher_dir = _logs_dir(data_home, key) / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    path = launcher_dir / f"{key}-{run_id}.log"
    path.write_text(body, encoding="utf-8")
    return path


def _write_sentinel(data_home: Path, key: str, sentinel: dict) -> None:
    launcher_dir = _launcher_dir(data_home, key)
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-exit.json").write_text(json.dumps(sentinel), encoding="utf-8")


def _fixture_records(project: Path, *, run_id: str = RUN_ID) -> list[dict]:
    """Five worker iterations plus the run_exit terminal record.

    Mirrors the 20261011-061345 shape: started+outcome pair per iteration,
    nine commits across five iterations, and a synthetic iteration 999999 that
    carries the stop_reason but is not a worker iteration.
    """
    opens = (
        "2026-10-11T06:13:53+0800",
        "2026-10-11T07:13:08+0800",
        "2026-10-11T07:53:50+0800",
        "2026-10-11T08:39:26+0800",
        "2026-10-11T08:56:03+0800",
    )
    closes = (
        "2026-10-11T07:11:40+0800",
        "2026-10-11T07:52:24+0800",
        "2026-10-11T08:37:58+0800",
        "2026-10-11T08:54:37+0800",
        "2026-10-11T10:02:01+0800",
    )
    commits = (2, 2, 2, 1, 2)  # 9 total
    records: list[dict] = []
    for i, (opened, closed, dur, new_commits) in enumerate(
        zip(opens, closes, ITER_DURATIONS, commits), start=1
    ):
        records.append({
            "run_id": run_id,
            "cli": "claude",
            "iteration": i,
            "timestamp": opened,
            "project": str(project),
            "model": "mimo-v2.6-pro",
            "status": "started",
        })
        # Four green checks then one red attribution gate (AC-3 asserts the
        # narrative in step 1; the classification stays L1 blocking here).
        checks = [
            {"command": f"gate-{n}", "outcome": "pass", "exit_code": 0}
            for n in range(1, 5)
        ]
        if i == 5:
            checks.append({
                "command": "verify_attribution.py --batch fixture",
                "outcome": "fail",
                "exit_code": 1,
                "stdout_tail": "recorded: 8085 passed, 4 failed, 0 errors of 8094",
                "stderr_tail": "ATTRIBUTION FAILED: 1 attributed regression(s)",
            })
        records.append({
            "run_id": run_id,
            "cli": "claude",
            "iteration": i,
            "timestamp": closed,
            "project": str(project),
            "model": "mimo-v2.6-pro",
            "duration_sec": dur,
            "exit_code": 0,
            "new_commits_total": new_commits,
            "stop_reason": "local_checks_failed" if i == 5 else None,
            "local_checks": checks,
        })
    records.append({
        "run_id": run_id,
        "cli": "claude",
        "iteration": 999999,
        "timestamp": RUN_END_STAMP,
        "project": str(project),
        "stop_reason": "local_checks_failed",
        "record_type": "run_exit",
        "iters": 5,
    })
    return records


def _run_collect(env: dict, project_path: Path, run_id: str = RUN_ID) -> str:
    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            run_id,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # AC-4: quiet stdout is exactly the report path.
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"--quiet must print only the report path, got {result.stdout!r}"
    report_path = Path(lines[0])
    assert report_path.exists(), f"collect printed a path with no file: {report_path}"
    return report_path.read_text(encoding="utf-8")


# ── AC-1: matching launch metadata is resolved from the external layout ──────


def test_ac1_matching_launch_metadata_reports_30_and_60(scratch_env):
    """Matching last-launch.json for THIS run reports max_iterations 30,
    iteration_timeout_min 60, and the launch stamp 06:13:45."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
        "worker_engine": "claude-worker",
    })

    text = _run_collect(env, project_path)

    assert "iterations_max_configured: 30" in text, (
        f"matching launch metadata must report max_iterations 30.\nHead:\n{text[:700]}"
    )
    assert "iteration_timeout_min_configured: 60" in text, (
        f"matching launch metadata must report iteration_timeout_min 60.\nHead:\n{text[:700]}"
    )
    assert "06:13:45" in text, (
        f"launch start 06:13:45 must appear (not an iteration's completion).\nHead:\n{text[:700]}"
    )
    assert 'launch_metadata_source: "last-launch.json"' in text, (
        f"the report must name where launch evidence came from.\nHead:\n{text[:700]}"
    )
    # The configured timeout is measured, so the display is not an "unknown".
    assert "configured timeout: 60 min" in text


def test_ac1_resolves_in_selfmod_and_worker_home_contexts(scratch_env):
    """Same resolution holds when the run's records name the selfmod worktree
    and the launch is a worker-home run — the metadata is keyed by the
    canonical project key, not by whichever path the record mentions."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()
    selfmod = _selfmod_worktree_path(data_home, key)

    _write_jsonl(data_home, key, _fixture_records(selfmod))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
        "worker_engine": "claude-worker",
        "worker_home": str(Path(env["HOME"]) / ".claude-worker"),
    })

    text = _run_collect(env, project_path)

    assert "iterations_max_configured: 30" in text
    assert "iteration_timeout_min_configured: 60" in text
    assert "06:13:45" in text


def test_ac1_does_not_borrow_a_newer_runs_settings(scratch_env):
    """A last-launch.json naming a LATER run is not this run's evidence:
    its settings must not appear as this run's configured caps."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": "20261012-101406",  # a later launch overwrote the pointer
        "max_iterations": 45,
        "iteration_timeout_min": 25,
        "started_at": "2026-10-12T10:14:07+0800",
    })

    text = _run_collect(env, project_path)

    assert "iterations_max_configured: 45" not in text, (
        f"a newer run's max_iterations must not be borrowed.\nHead:\n{text[:700]}"
    )
    assert "iteration_timeout_min_configured: 25" not in text, (
        f"a newer run's timeout must not be borrowed.\nHead:\n{text[:700]}"
    )
    assert "10:14:07" not in text, (
        f"a newer run's launch stamp must not be borrowed.\nHead:\n{text[:700]}"
    )


def test_ac1_recovers_matching_evidence_from_per_run_launcher_log(scratch_env):
    """When the latest-run pointer has moved on, the per-run launcher log
    still carries this run's caps, and the report reads them from there."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": "20261012-101406",
        "max_iterations": 45,
        "iteration_timeout_min": 25,
        "started_at": "2026-10-12T10:14:07+0800",
    })
    _write_launcher_log(data_home, key, RUN_ID, (
        "[runner] CLAUDE_CONFIG_DIR=/home/.claude-worker\n"
        "\n=== ilk-loop runner (Claude Code) ===\n"
        f"Project:        {project}\n"
        "Max iterations: 30\n"
        "Iter timeout:   60 min\n"
        "\n--- Iteration 1 / 30 ---\n"
    ))

    text = _run_collect(env, project_path)

    assert "iterations_max_configured: 30" in text, (
        f"per-run launcher log must supply this run's max_iterations.\nHead:\n{text[:700]}"
    )
    assert "iteration_timeout_min_configured: 60" in text, (
        f"per-run launcher log must supply this run's timeout.\nHead:\n{text[:700]}"
    )
    assert 'launch_metadata_source: "launcher-log"' in text
    # Still never the newer pointer's values.
    assert "iteration_timeout_min_configured: 25" not in text


def test_ac1_unknown_stays_unknown_and_no_invented_timeout(scratch_env):
    """With no matching launch evidence at all, configured caps stay unknown
    and the recommendation must not invent a timeout."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    # No last-launch.json, no launcher log, no archive copy.

    text = _run_collect(env, project_path)

    assert 'iterations_max_configured: "unknown"' in text, (
        f"unknown max_iterations must stay unknown.\nHead:\n{text[:700]}"
    )
    assert 'iteration_timeout_min_configured: "unknown"' in text, (
        f"unknown timeout must stay unknown.\nHead:\n{text[:700]}"
    )
    assert "configured timeout: unknown" in text
    # A number is a measurement claim.  None here means "no measurement".
    assert "recommended_iteration_timeout_min: null" in text, (
        f"no invented recommended timeout.\nHead:\n{text[:700]}"
    )
    assert "recommended_max_iterations: null" in text
    assert "-IterationTimeoutMin 30" not in text, (
        f"the resume line must not carry an invented 30-minute timeout.\nHead:\n{text[:700]}"
    )
    assert "-MaxIterations 30" not in text


# ── AC-2: measured time and the run_exit record ──────────────────────────────


def test_ac2_wall_time_is_distinct_from_iteration_sum(scratch_env):
    """Complete wall time 3h49m37s (launch → run end) is reported separately
    from the sum of iteration durations (203.0 min)."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
    })

    text = _run_collect(env, project_path)

    assert WALL_HMS in text, (
        f"complete wall time {WALL_HMS} must be reported.\nHead:\n{text[:700]}"
    )
    assert "wall_elapsed_sec: 13777" in text, (
        f"wall time must be measured in seconds (13777 = {WALL_HMS}).\nHead:\n{text[:700]}"
    )
    assert "total_elapsed_sec: 12179" in text, (
        f"iteration-duration sum must stay its own measurement.\nHead:\n{text[:700]}"
    )
    assert "203.0 min" in text, (
        f"the iteration sum is displayed as 203.0 min.\nHead:\n{text[:700]}"
    )
    # The two must not be presented as one number.
    assert f"| Wall time | {WALL_HMS} (launch → run end) |" in text
    assert "| Iteration time (sum) | 203.0 min |" in text


def test_ac2_wall_time_uses_matching_sentinel_end(scratch_env):
    """A matching sentinel's ended_at closes the wall clock when present."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
    })
    _write_sentinel(data_home, key, {
        "state": "local_checks_failed",
        "run_id": RUN_ID,
        "started_at": LAUNCH_STAMP,
        "ended_at": RUN_END_STAMP,
    })

    text = _run_collect(env, project_path)

    assert "wall_elapsed_sec: 13777" in text
    assert WALL_HMS in text


def test_ac2_run_exit_999999_is_not_a_worker_iteration(scratch_env):
    """The synthetic run_exit record is a terminal record, not a sixth
    worker iteration."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
    })

    text = _run_collect(env, project_path)

    assert "iterations: 5" in text, (
        f"five worker iterations, not six.\nHead:\n{text[:700]}"
    )
    assert "| 999999 |" not in text, (
        f"run_exit must not be listed as a worker iteration.\nHead:\n{text[:700]}"
    )
    # The five real rows are present.
    for n in range(1, 6):
        assert f"| {n} |" in text


# ── AC-3: narratives proportional to the measured gate history ───────────────


def _fixture_records_repeated_red(project: Path, *, run_id: str = RUN_ID) -> list[dict]:
    """The same shape, but the reds repeat instead of landing once at the stop.

    Used as the positive control for the terminal-failure narrative: a
    fixture that DID fail repeatedly must still be described as repetition.
    """
    opens = (
        "2026-10-11T06:13:53+0800",
        "2026-10-11T07:13:08+0800",
        "2026-10-11T07:53:50+0800",
        "2026-10-11T08:39:26+0800",
        "2026-10-11T08:56:03+0800",
    )
    closes = (
        "2026-10-11T07:11:40+0800",
        "2026-10-11T07:52:24+0800",
        "2026-10-11T08:37:58+0800",
        "2026-10-11T08:54:37+0800",
        "2026-10-11T10:02:01+0800",
    )
    commits = (2, 2, 2, 1, 2)
    records: list[dict] = []
    for i, (opened, closed, dur, new_commits) in enumerate(
        zip(opens, closes, ITER_DURATIONS, commits), start=1
    ):
        records.append({
            "run_id": run_id,
            "cli": "claude",
            "iteration": i,
            "timestamp": opened,
            "project": str(project),
            "model": "mimo-v2.6-pro",
            "status": "started",
        })
        # Iterations 3, 4 and 5 each fail the attribution gate: a pattern
        # that repeats, which is what the repetition narrative must report.
        checks = [
            {"command": f"gate-{n}", "outcome": "pass", "exit_code": 0}
            for n in range(1, 5)
        ]
        if i >= 3:
            checks.append({
                "command": "verify_attribution.py --batch fixture",
                "outcome": "fail",
                "exit_code": 1,
                "stderr_tail": "ATTRIBUTION FAILED: 1 attributed regression(s)",
            })
        records.append({
            "run_id": run_id,
            "cli": "claude",
            "iteration": i,
            "timestamp": closed,
            "project": str(project),
            "model": "mimo-v2.6-pro",
            "duration_sec": dur,
            "exit_code": 0,
            "new_commits_total": new_commits,
            "stop_reason": "local_checks_failed" if i == 5 else None,
            "local_checks": checks,
        })
    records.append({
        "run_id": run_id,
        "cli": "claude",
        "iteration": 999999,
        "timestamp": RUN_END_STAMP,
        "project": str(project),
        "stop_reason": "local_checks_failed",
        "record_type": "run_exit",
        "iters": 5,
    })
    return records


def test_ac3_terminal_red_is_not_narrated_as_repeated_convergence(scratch_env):
    """Four green checks then one red attribution gate: L1 still blocks on
    `local-checks-stuck`, but the narrative must describe a terminal gate
    failure — not a convergence loop the records do not show."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
    })

    text = _run_collect(env, project_path)

    assert 'classification: "local-checks-stuck"' in text, (
        f"L1 must keep classifying a terminal red gate as blocking "
        f"local-checks-stuck.\nHead:\n{text[:700]}"
    )
    assert "terminal gate failure" in text, (
        f"one red after four greens is a terminal gate failure and must be "
        f"narrated as one.\nWhat happened:\n{text[text.find('## What happened'):text.find('## What happened') + 900]}"
    )
    assert "repeated convergence failure" not in text, (
        f"the narrative must not assert a convergence failure the run did "
        f"not record.\nWhat happened:\n{text[text.find('## What happened'):text.find('## What happened') + 900]}"
    )
    assert "did not converge" not in text, (
        f"'did not converge' is a repetition claim; this run recorded one "
        f"red, at the stop.\nWhat happened:\n{text[text.find('## What happened'):text.find('## What happened') + 900]}"
    )
    # The measured counts are still reported — proportion is not omission.
    assert "failed in 1 of the last 5 iterations (passed in 4)" in text, (
        f"the gate history must be reported as measured.\nWhat happened:\n"
        f"{text[text.find('## What happened'):text.find('## What happened') + 900]}"
    )


def test_ac3_repeated_reds_still_describe_repetition(scratch_env):
    """Positive control: a fixture whose reds repeat must still read as a
    convergence failure. Without this the terminal-failure wording could
    be applied unconditionally and nothing would say so."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records_repeated_red(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
    })

    text = _run_collect(env, project_path)

    assert 'classification: "local-checks-stuck"' in text
    assert "terminal gate failure" not in text, (
        f"three failing iterations is not a terminal red.\nWhat happened:\n"
        f"{text[text.find('## What happened'):text.find('## What happened') + 900]}"
    )
    assert "did not converge" in text, (
        f"repeated reds must still be described as repetition.\nWhat happened:\n"
        f"{text[text.find('## What happened'):text.find('## What happened') + 900]}"
    )
    assert "failed in 3 of the last 5 iterations (passed in 2)" in text, (
        f"repetition must be reported as measured counts.\nWhat happened:\n"
        f"{text[text.find('## What happened'):text.find('## What happened') + 900]}"
    )


def test_ac3_local_checks_stuck_still_blocks(scratch_env):
    """The label is a blocking class: the watchdog must not relaunch it."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
    })

    text = _run_collect(env, project_path)
    assert 'classification: "local-checks-stuck"' in text

    watchdog = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"
    action = subprocess.run(
        ["bash", "-c",
         f"source '{watchdog}' >/dev/null 2>&1; classify_action local-checks-stuck"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    ).stdout.strip()
    assert action == "block", (
        f"watchdog.sh routes local-checks-stuck to {action!r}; it must stay "
        "'block' — a red gate is never auto-relaunched, whatever the "
        "narrative says about how it failed"
    )


def test_ac3_zero_transient_errors_is_a_recorded_count(scratch_env):
    """Zero logged transient errors is a recorded count, not proof of no
    retries. The report must say which it is."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    _write_last_launch(data_home, key, {
        "project_path": str(project),
        "run_id": RUN_ID,
        "max_iterations": 30,
        "iteration_timeout_min": 60,
        "started_at": LAUNCH_STAMP,
    })

    text = _run_collect(env, project_path)

    assert "transient_error_count: 0" in text, (
        f"the measured count itself must still be reported.\nHead:\n{text[:700]}"
    )
    assert "not proof of no retries" in text, (
        f"a zero must be labelled as a recorded count, not as a finding.\n"
        f"Quick stats:\n{text[text.find('## Quick stats'):text.find('## Quick stats') + 700]}"
    )
    assert "| Transient API errors | 0 |" not in text, (
        f"a bare 0 reads as 'there were none'.\nQuick stats:\n"
        f"{text[text.find('## Quick stats'):text.find('## Quick stats') + 700]}"
    )


# ── AC-4: quiet stdout, and no invented cap anywhere in the report ──────────


def test_ac4_quiet_stdout_is_exactly_the_report_path(scratch_env):
    """--quiet prints the report path and nothing else; the report itself
    must not carry an invented cap when the configuration is unknown."""
    project_path, env, key, data_home = scratch_env
    project = project_path.resolve()

    _write_jsonl(data_home, key, _fixture_records(project))
    # No matching launch evidence: configured caps are unknown.

    result = subprocess.run(
        [
            sys.executable,
            str(_COLLECT_PY),
            "-ProjectPath",
            str(project_path),
            "--run-id",
            RUN_ID,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"--quiet must print only the report path, got {result.stdout!r}"
    assert lines[0] == lines[0].strip()
    text = Path(lines[0]).read_text(encoding="utf-8")

    assert 'iterations_max_configured: "unknown"' in text
    assert 'iteration_timeout_min_configured: "unknown"' in text
    assert "recommended_iteration_timeout_min: null" in text
    assert "recommended_max_iterations: null" in text
    for invented in ("-IterationTimeoutMin 30", "-MaxIterations 30",
                     "-IterationTimeoutMin 45", "-MaxIterations 45"):
        assert invented not in text, (
            f"the resume line must not invent a cap; found {invented!r}.\n"
            f"Head:\n{text[:700]}"
        )
