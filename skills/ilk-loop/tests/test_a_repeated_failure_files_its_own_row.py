"""A repeated failure files its own backlog row.

Sub-plan a-repeated-failure-files-its-own-row, step 0 (red-first pins).

When the same failure ends two consecutive runs of the same slug at the
same step, the scheduler files ONE evidence-carrying ilk backlog row with
no agent involved.

AC-1..AC-7 pin the contract before ``repeat_failure.py`` is built.
Every test is xfail(strict=True) until step 1 removes the markers.

HOME and ILK_DATA_HOME are both pinned to tmp_path (§23 half-pinned trap).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
for _p in (
    _HERE.parent.parent / "scripts",
    _HERE.parent.parent.parent / "ilk-feedback" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def project_dir(tmp_path):
    """Build a minimal project data directory under tmp_path."""
    d = tmp_path / "project-data"
    (d / "runtime" / "launcher").mkdir(parents=True)
    (d / "logs" / "launcher").mkdir(parents=True)
    return d


@pytest.fixture()
def backlog_dir(tmp_path):
    """Isolated backlog directory."""
    d = tmp_path / "backlog"
    d.mkdir()
    return d


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


# ── AC-1: the real case ─────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="repeat_failure not built")
def test_ac1_two_consecutive_revert_rows_give_one_repeat(project_dir):
    """Two revert rows from runs 20261009-085114 and 20261009-085627
    (slug postmortem-labels-verify, from_step 2, ship_commit 416a1020,
    site integrity) give detect() exactly one repeat with kind revert."""
    import repeat_failure

    revert_rows = [
        {
            "slug": "postmortem-labels-verify",
            "ship_commit": "416a1020eef8089012ea5b303552e6db03e048f0",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "20261009-085114",
            "iteration": 1,
            "timestamp": "2026-10-09T08:51:14+08:00",
            "site": "integrity",
        },
        {
            "slug": "postmortem-labels-verify",
            "ship_commit": "416a1020eef8089012ea5b303552e6db03e048f0",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "20261009-085627",
            "iteration": 1,
            "timestamp": "2026-10-09T08:56:27+08:00",
            "site": "integrity",
        },
    ]
    reverts_path = project_dir / "runtime" / "launcher" / "ship-reverts.jsonl"
    _write_jsonl(reverts_path, revert_rows)

    gate_path = project_dir / "runtime" / "launcher" / "gate-history.jsonl"
    _write_jsonl(gate_path, [])

    repeats = repeat_failure.detect(
        gate_rows=[], revert_rows=revert_rows,
    )
    assert len(repeats) == 1, f"expected 1 repeat, got {len(repeats)}: {repeats}"
    r = repeats[0]
    assert r.kind == "revert", f"expected kind 'revert', got {r.kind!r}"
    assert r.slug == "postmortem-labels-verify"
    assert r.run_a == "20261009-085114"
    assert r.run_b == "20261009-085627"


# ── AC-2: filing ─────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="repeat_failure not built")
def test_ac2_file_repeats_adds_one_backlog_row(project_dir, backlog_dir):
    """file_repeats on AC-1's fixture plus a launcher log for run
    20261009-085627 containing the integrity violation adds exactly one
    backlog row with source 'repeat-detector' and evidence fields."""
    import repeat_failure

    revert_rows = [
        {
            "slug": "postmortem-labels-verify",
            "ship_commit": "416a1020eef8089012ea5b303552e6db03e048f0",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "20261009-085114",
            "iteration": 1,
            "timestamp": "2026-10-09T08:51:14+08:00",
            "site": "integrity",
        },
        {
            "slug": "postmortem-labels-verify",
            "ship_commit": "416a1020eef8089012ea5b303552e6db03e048f0",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "20261009-085627",
            "iteration": 1,
            "timestamp": "2026-10-09T08:56:27+08:00",
            "site": "integrity",
        },
    ]
    reverts_path = project_dir / "runtime" / "launcher" / "ship-reverts.jsonl"
    _write_jsonl(reverts_path, revert_rows)

    gate_path = project_dir / "runtime" / "launcher" / "gate-history.jsonl"
    _write_jsonl(gate_path, [])

    log_path = (
        project_dir / "logs" / "launcher" / "testkey-20261009-085627.log"
    )
    _write_log(log_path, [
        "some earlier line",
        "  [ship-integrity VIOLATION] postmortem-labels-verify: VIOLATION: missing commit for step 0 — 416a1020",
        "another line",
    ])

    result = repeat_failure.file_repeats(
        project_data_dir=project_dir,
        backlog_dir=backlog_dir,
        host="test-host",
    )
    assert result["status"] == "ok", f"status: {result['status']}"
    assert len(result["filed"]) == 1, f"filed: {result['filed']}"

    import improvement_backlog

    entries = improvement_backlog.load(backlog_dir=backlog_dir)
    assert len(entries) == 1, f"expected 1 entry, got {len(entries)}"
    e = entries[0]
    assert e.source == "repeat-detector"
    assert e.kind == "bug"
    assert e.severity == "high"
    assert e.leverage == "high"
    assert "runs" in e.evidence
    assert "rows" in e.evidence
    assert "integrity_message" in e.evidence
    assert e.evidence["host"] == "test-host"


# ── AC-3: dedupe ─────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="repeat_failure not built")
def test_ac3_second_call_adds_no_row_third_bumps_seen_count(
    project_dir, backlog_dir,
):
    """A second file_repeats call on the same files adds no row. Adding a
    third run C with the same revert adds no row and bumps seen_count
    by exactly 1."""
    import repeat_failure

    revert_rows_ab = [
        {
            "slug": "postmortem-labels-verify",
            "ship_commit": "416a1020eef8089012ea5b303552e6db03e048f0",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "20261009-085114",
            "iteration": 1,
            "timestamp": "2026-10-09T08:51:14+08:00",
            "site": "integrity",
        },
        {
            "slug": "postmortem-labels-verify",
            "ship_commit": "416a1020eef8089012ea5b303552e6db03e048f0",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "20261009-085627",
            "iteration": 1,
            "timestamp": "2026-10-09T08:56:27+08:00",
            "site": "integrity",
        },
    ]
    reverts_path = project_dir / "runtime" / "launcher" / "ship-reverts.jsonl"
    _write_jsonl(reverts_path, revert_rows_ab)

    gate_path = project_dir / "runtime" / "launcher" / "gate-history.jsonl"
    _write_jsonl(gate_path, [])

    # First call: files one row
    r1 = repeat_failure.file_repeats(
        project_data_dir=project_dir, backlog_dir=backlog_dir, host="h",
    )
    assert len(r1["filed"]) == 1

    # Second call: no new row
    r2 = repeat_failure.file_repeats(
        project_data_dir=project_dir, backlog_dir=backlog_dir, host="h",
    )
    assert len(r2["filed"]) == 0, f"second call filed: {r2['filed']}"
    assert len(r2["already_filed"]) >= 1

    import improvement_backlog

    entries = improvement_backlog.load(backlog_dir=backlog_dir)
    assert len(entries) == 1
    seen_after_two = entries[0].seen_count

    # Third run C with the same revert: adds pair (B, C) — same source_id
    revert_rows_abc = revert_rows_ab + [
        {
            "slug": "postmortem-labels-verify",
            "ship_commit": "416a1020eef8089012ea5b303552e6db03e048f0",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "20261009-090100",
            "iteration": 1,
            "timestamp": "2026-10-09T09:01:00+08:00",
            "site": "integrity",
        },
    ]
    _write_jsonl(reverts_path, revert_rows_abc)

    r3 = repeat_failure.file_repeats(
        project_data_dir=project_dir, backlog_dir=backlog_dir, host="h",
    )
    entries = improvement_backlog.load(backlog_dir=backlog_dir)
    assert len(entries) == 1, f"third call should not add a row, got {len(entries)}"
    assert entries[0].seen_count == seen_after_two + 1, (
        f"seen_count: expected {seen_after_two + 1}, got {entries[0].seen_count}"
    )


# ── AC-4: red gate ──────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="repeat_failure not built")
def test_ac4_red_gate_repeat(project_dir, backlog_dir):
    """Two runs whose last (slug, step 1) gate row is 'fail' at the same
    head_sha give one red-gate repeat."""
    import repeat_failure

    gate_rows = [
        {
            "slug": "my-sub",
            "step": 1,
            "outcome": "fail",
            "exit_code": 1,
            "head_sha": "abc123",
            "run_id": "run-100",
            "iteration": 1,
            "timestamp": "2026-10-09T10:00:00+08:00",
        },
        {
            "slug": "my-sub",
            "step": 1,
            "outcome": "fail",
            "exit_code": 1,
            "head_sha": "abc123",
            "run_id": "run-101",
            "iteration": 1,
            "timestamp": "2026-10-09T10:05:00+08:00",
        },
    ]
    gate_path = project_dir / "runtime" / "launcher" / "gate-history.jsonl"
    _write_jsonl(gate_path, gate_rows)

    reverts_path = project_dir / "runtime" / "launcher" / "ship-reverts.jsonl"
    _write_jsonl(reverts_path, [])

    repeats = repeat_failure.detect(gate_rows=gate_rows, revert_rows=[])
    assert len(repeats) == 1, f"expected 1 repeat, got {len(repeats)}"
    assert repeats[0].kind == "red-gate"
    assert repeats[0].step == 1


@pytest.mark.xfail(strict=True, reason="repeat_failure not built")
def test_ac4_no_red_gate_when_pass_or_different_sha(project_dir):
    """If the second run's last row at that step is 'pass', or head_sha
    differs, there is no red-gate repeat."""
    import repeat_failure

    # Case: second run passes
    gate_rows_pass = [
        {
            "slug": "my-sub", "step": 1, "outcome": "fail", "exit_code": 1,
            "head_sha": "abc123", "run_id": "run-100", "iteration": 1,
            "timestamp": "2026-10-09T10:00:00+08:00",
        },
        {
            "slug": "my-sub", "step": 1, "outcome": "pass", "exit_code": 0,
            "head_sha": "abc123", "run_id": "run-101", "iteration": 1,
            "timestamp": "2026-10-09T10:05:00+08:00",
        },
    ]
    repeats = repeat_failure.detect(gate_rows=gate_rows_pass, revert_rows=[])
    assert len(repeats) == 0, f"pass case: expected 0, got {len(repeats)}"

    # Case: different head_sha
    gate_rows_diff_sha = [
        {
            "slug": "my-sub", "step": 1, "outcome": "fail", "exit_code": 1,
            "head_sha": "abc123", "run_id": "run-100", "iteration": 1,
            "timestamp": "2026-10-09T10:00:00+08:00",
        },
        {
            "slug": "my-sub", "step": 1, "outcome": "fail", "exit_code": 1,
            "head_sha": "def456", "run_id": "run-101", "iteration": 1,
            "timestamp": "2026-10-09T10:05:00+08:00",
        },
    ]
    repeats = repeat_failure.detect(gate_rows=gate_rows_diff_sha, revert_rows=[])
    assert len(repeats) == 0, f"different sha case: expected 0, got {len(repeats)}"


# ── AC-5: not a repeat ──────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="repeat_failure not built")
def test_ac5_same_run_two_identical_reverts_not_a_repeat(project_dir):
    """One run with two identical reverts (same run_id) gives no repeat."""
    import repeat_failure

    revert_rows = [
        {
            "slug": "my-sub",
            "ship_commit": "abc123",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "run-100",
            "iteration": 1,
            "timestamp": "2026-10-09T10:00:00+08:00",
            "site": "integrity",
        },
        {
            "slug": "my-sub",
            "ship_commit": "abc123",
            "reason": "ship_integrity",
            "from_status": "shipped",
            "to_status": "reverted",
            "from_step": 2,
            "to_step": 0,
            "run_id": "run-100",
            "iteration": 2,
            "timestamp": "2026-10-09T10:01:00+08:00",
            "site": "integrity",
        },
    ]
    repeats = repeat_failure.detect(gate_rows=[], revert_rows=revert_rows)
    assert len(repeats) == 0, f"same-run reverts: expected 0, got {len(repeats)}"


# ── AC-6: fails loud ────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="repeat_failure not built")
def test_ac6_unreadable_json_line_exits_3(project_dir, backlog_dir):
    """A gate-history line that is not valid JSON makes the CLI exit 3
    with status 'unreadable', and the backlog file is unchanged."""
    import repeat_failure

    gate_path = project_dir / "runtime" / "launcher" / "gate-history.jsonl"
    _write_jsonl(gate_path, [
        {"slug": "x", "step": 0, "outcome": "pass", "exit_code": 0,
         "head_sha": "a", "run_id": "r1", "iteration": 1,
         "timestamp": "2026-10-09T10:00:00+08:00"},
    ])
    # Append a broken line
    with open(gate_path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    reverts_path = project_dir / "runtime" / "launcher" / "ship-reverts.jsonl"
    _write_jsonl(reverts_path, [])

    # Record backlog state before
    backlog_file = backlog_dir / "candidates.json"
    before_bytes = backlog_file.read_bytes() if backlog_file.exists() else b""

    with pytest.raises(SystemExit) as exc_info:
        repeat_failure.file_repeats(
            project_data_dir=project_dir, backlog_dir=backlog_dir, host="h",
        )
    assert exc_info.value.code == 3

    after_bytes = backlog_file.read_bytes() if backlog_file.exists() else b""
    assert before_bytes == after_bytes, "backlog changed after unreadable input"


# ── AC-7: scheduler hook ────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="repeat_failure not built")
def test_ac7_scheduler_hook_returns_0_on_stub_exit_3(project_dir, tmp_path):
    """The file_repeated_failures shell function, extracted from
    scheduler.sh, returns 0 when REPEAT_FAILURE_PY points at a stub
    that exits 3. It writes a repeat-detector-failed scheduler log line."""
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    scheduler_sh = repo_root / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"

    # Write a stub that exits 3
    stub = tmp_path / "stub_repeat_failure.py"
    stub.write_text("import sys; sys.exit(3)\n", encoding="utf-8")

    # Extract the function and run it
    body = (
        f'REPEAT_FAILURE_PY="{stub}"\n'
        f'write_scheduler_log() {{ echo "LOG: $@"; }}\n'
        f'file_repeated_failures "test-key" "{project_dir}"\n'
        f'echo "exit=$?"\n'
    )
    prelude = f'SCHEDULER_SH="{scheduler_sh}"\n'
    prelude += "eval \"$(sed -n '/^file_repeated_failures()/,/^}/p' \"$SCHEDULER_SH\")\"\n"

    proc = subprocess.run(
        ["/bin/bash", "-c", prelude + body],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"shell function returned {proc.returncode}: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "exit=0" in proc.stdout, f"expected exit=0 in output: {proc.stdout!r}"
    assert "repeat-detector-failed" in proc.stdout, (
        f"expected repeat-detector-failed log line: {proc.stdout!r}"
    )