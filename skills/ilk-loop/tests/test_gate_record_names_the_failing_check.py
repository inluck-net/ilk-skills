"""A red gate's record names the check that failed, not the first one declared.

Why this exists: `build_record` labelled every record with ``results[0]``, and
the runner's ``[local_checks FAIL]`` echo did the same.  ``ship_integrity.py``
builds its "Failing checks:" list from that label.  On gh-resolve 2026-09-29
(run 20260929-073402, root-area-typecheck-gate step 1) the fourth check — a
pytest neighbour set tripped by a conftest guard — failed, while the first, a
pin guard, passed.  All three surfaces named the pin guard as failing, and the
diagnosis started on a check that was green.

The rule: first failing check, else the first check (a passing gate still
names a command it counted — test_emit_jsonl_record_command.py AC-1).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import emit_jsonl_record as ejr  # noqa: E402

PIN = "python3 scripts/pins_only_lose_xfail.py 5102b93 tests/test_x.py"
SUITE = "python3 -m pytest tests/test_x.py -q"

RED_AT_FOURTH = {
    "results": [
        {"command": PIN, "passed": True, "exit_code": 0},
        {"command": "bash -c '! grep xfail tests/test_x.py'", "passed": True, "exit_code": 0},
        {"command": "git diff --quiet 5102b93 -- tests/fixtures", "passed": True, "exit_code": 0},
        {"command": SUITE, "passed": False, "exit_code": 1,
         "stderr_tail": "D-70 VIOLATION: the real ledger grew"},
    ]
}


def test_record_names_the_failing_check():
    failing = ejr.extract_failing_check(RED_AT_FOURTH)
    rec = ejr.build_record("s", 1, "fail", 1, failing, data=RED_AT_FOURTH)
    assert rec["command"] == SUITE
    # The tails already came from the failing check; label and output agree.
    assert "D-70" in rec["stderr_tail"]


def test_passing_gate_still_names_its_first_check():
    green = {"results": [dict(r, passed=True, exit_code=0) for r in RED_AT_FOURTH["results"]]}
    rec = ejr.build_record("s", 1, "pass", 0, None, data=green)
    assert rec["command"] == PIN


def test_label_cli_is_what_the_runner_echo_prints(tmp_path):
    """The runner's FAIL echo calls this instead of its own inline results[0]."""
    out = tmp_path / "gate.json"
    out.write_text(json.dumps(RED_AT_FOURTH), encoding="utf-8")
    cp = subprocess.run(
        [sys.executable, str(SCRIPTS / "emit_jsonl_record.py"), "--label", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.strip() == SUITE


def test_label_cli_is_silent_on_unreadable_output(tmp_path):
    """The echo falls back to the no-cmd form; the label must never invent one."""
    out = tmp_path / "gate.json"
    out.write_text("Traceback (most recent call last):\n", encoding="utf-8")
    cp = subprocess.run(
        [sys.executable, str(SCRIPTS / "emit_jsonl_record.py"), "--label", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cp.returncode == 0
    assert cp.stdout.strip() == ""


def test_runner_echo_does_not_pick_results_zero_itself():
    sh = (SCRIPTS / "run_ilk_loop_claude.sh").read_text(encoding="utf-8")
    assert "rs[0].get('command'" not in sh
    assert "emit_jsonl_record.py\" --label" in sh
