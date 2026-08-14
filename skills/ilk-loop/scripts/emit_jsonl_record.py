#!/usr/bin/env python3
"""Emit a JSONL record for a local_checks result.

Reads the JSON output from run_local_checks.py and writes a JSONL record
to the results file. This replaces the hand-interpolated echo in
run_ilk_loop_claude.sh:1022.

Usage:
    python3 emit_jsonl_record.py <results_file> <tmp_out> <outcome> <check_exit>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def extract_failing_check(data: dict) -> dict | None:
    """Extract the first failing check from run_local_checks output."""
    results = data.get("results", [])
    for r in results:
        if not r.get("passed", True):
            return r
    return None


def build_record(
    slug: str,
    step: int | None,
    outcome: str,
    exit_code: int,
    failing_check: dict | None,
) -> dict:
    """Build a JSONL record with command and output for failing checks."""
    rec = {
        "slug": slug,
        "step": step,
        "outcome": outcome,
        "exit_code": exit_code,
    }

    if failing_check and outcome in ("fail", "error"):
        cmd = failing_check.get("command", "")
        if cmd:
            rec["command"] = cmd
        # Cap tails at 4KB each — enough for diagnosis, not enough to bloat
        stdout = failing_check.get("stdout_tail", "")
        stderr = failing_check.get("stderr_tail", "")
        error = failing_check.get("error", "")
        if stdout:
            rec["stdout_tail"] = stdout[-4096:] if len(stdout) > 4096 else stdout
        if stderr:
            rec["stderr_tail"] = stderr[-4096:] if len(stderr) > 4096 else stderr
        if error:
            rec["error"] = error[-4096:] if len(error) > 4096 else error

    return rec


def main() -> int:
    if len(sys.argv) < 5:
        print(f"Usage: {sys.argv[0]} <results_file> <tmp_out> <outcome> <check_exit>",
              file=sys.stderr)
        return 1

    results_file = sys.argv[1]
    tmp_out = sys.argv[2]
    outcome = sys.argv[3]
    try:
        check_exit = int(sys.argv[4])
    except ValueError:
        check_exit = 0

    # Read the JSON output from run_local_checks.py
    data = {}
    tmp_path = Path(tmp_out)
    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        try:
            data = json.loads(tmp_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            pass

    slug = data.get("slug", "")
    step = data.get("step")
    failing_check = extract_failing_check(data)

    rec = build_record(slug, step, outcome, check_exit, failing_check)

    # Append to results file
    with open(results_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
