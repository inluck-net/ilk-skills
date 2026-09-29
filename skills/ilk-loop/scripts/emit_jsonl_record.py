#!/usr/bin/env python3
"""Emit a JSONL record for a local_checks result.

Reads the JSON output from run_local_checks.py and writes a JSONL record
to the results file. This replaces the hand-interpolated echo in
run_ilk_loop_claude.sh:1022.

Usage:
    python3 emit_jsonl_record.py <results_file> <tmp_out> <outcome> <check_exit> [<slug> <step>]

``<slug> <step>`` is the identity of the target the invoker chose to gate.
When supplied it is authoritative: the checked process's own stdout may fill
a blank but never override it (see main()).
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


def gate_label_command(data: dict) -> str:
    """The command a gate result is labelled with: first failing, else first.

    One rule for both surfaces that name a gate — this record's ``command``
    (which ship_integrity.py reads for "Failing checks:") and the runner's
    ``[local_checks FAIL] ... cmd:`` echo.  Both used ``results[0]``, so a red
    gate whose first check passed named a green check as the failure
    (gh-resolve root-area-typecheck-gate, 2026-09-29).
    """
    results = data.get("results", []) if isinstance(data, dict) else []
    failing = extract_failing_check(data) if results else None
    chosen = failing or (results[0] if results else None)
    return (chosen or {}).get("command", "") or ""


def build_record(
    slug: str,
    step: int | None,
    outcome: str,
    exit_code: int,
    failing_check: dict | None,
    data: dict | None = None,
) -> dict:
    """Build a JSONL record with command and output for failing checks.

    The ``command`` field is included for ALL outcomes (pass, fail, error,
    inconclusive) so that the gate's output names every command it counted.
    Previously it was only emitted for fail/error, which meant a passing gate
    was indistinguishable from a gate that never ran — both showed no command.
    """
    rec = {
        "slug": slug,
        "step": step,
        "outcome": outcome,
        "exit_code": exit_code,
    }

    # Always include the command so every gate outcome — pass or fail — is
    # auditable.  Source preference: data (full run_local_checks output, all
    # outcomes; labelled by gate_label_command) → failing_check (back-compat,
    # fail/error only).
    results = (data or {}).get("results", [])
    if results:
        cmd = gate_label_command(data or {})
        if cmd:
            rec["command"] = cmd
    elif failing_check and outcome in ("fail", "error"):
        # Back-compat: when data is not provided, extract from failing_check.
        cmd = failing_check.get("command", "")
        if cmd:
            rec["command"] = cmd

    if failing_check and outcome in ("fail", "error"):
        # Cap tails at 4KB each — enough for diagnosis, not enough to bloat
        stdout = failing_check.get("stdout_tail", "")
        stderr = failing_check.get("stderr_tail", "")
        error = failing_check.get("error", "")
        reason = failing_check.get("reason", "")
        if stdout:
            rec["stdout_tail"] = stdout[-4096:] if len(stdout) > 4096 else stdout
        if stderr:
            rec["stderr_tail"] = stderr[-4096:] if len(stderr) > 4096 else stderr
        if error:
            rec["error"] = error[-4096:] if len(error) > 4096 else error
        if reason:
            rec["reason"] = reason

    # head_sha: the commit the gate ran against.  Copied from run_local_checks
    # output (data["head_sha"]) when present.  A row without it is still
    # readable but is treated as "no proof" by the final-step gate invariant
    # (fail closed).  See sub-plan a-ship-needs-a-passing-final-gate AC-1.
    head_sha = (data or {}).get("head_sha")
    if head_sha:
        rec["head_sha"] = head_sha

    return rec


def append_gate_history(
    history_path: Path,
    row: dict,
    run_id: str,
    iteration: int,
    timestamp: str,
) -> None:
    """Append a gate row to the persistent gate history (``gate-history.jsonl``).

    The row is enriched with ``run_id``, ``iteration`` and ``timestamp``
    before writing.  The parent directory is created if it does not exist.
    The file is append-only.

    See sub-plan ``a-ship-needs-a-passing-final-gate`` AC-3.
    """
    enriched = dict(row)
    enriched["run_id"] = run_id
    enriched["iteration"] = iteration
    enriched["timestamp"] = timestamp
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(enriched, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    # `--label <tmp_out>`: print the gate's label command for the runner's
    # echo; empty when the helper output is not parseable JSON.
    if len(sys.argv) == 3 and sys.argv[1] == "--label":
        try:
            data = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError, ValueError):
            data = {}
        print(gate_label_command(data))
        return 0

    if len(sys.argv) < 5 or len(sys.argv) > 7:
        print(f"Usage: {sys.argv[0]} <results_file> <tmp_out> <outcome> <check_exit> [<slug> <step>]",
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

    # Identity comes from the invoker, never from the checked process's own
    # stdout (Contract 2b invariant 6). The runner knows the target it chose
    # to gate ($slug/$step at run_ilk_loop_claude.sh:1119); the helper's
    # stdout is absent exactly when the gate failed, so taking identity from
    # it produced the anonymous record behind the phantom B2 target " 0" ->
    # slug="0" (gh-resolve resolver run, kira-cloudflare launcher
    # 20260902-183120). Explicit argv is authoritative; a blank explicit
    # value may only fall back to the helper's, never the reverse.
    if len(sys.argv) >= 6 and sys.argv[5]:
        slug = sys.argv[5]
    if len(sys.argv) >= 7 and sys.argv[6]:
        try:
            step = int(sys.argv[6])
        except ValueError:
            pass

    failing_check = extract_failing_check(data)

    rec = build_record(slug, step, outcome, check_exit, failing_check, data=data)

    # Append to results file.
    #
    # `separators=(",", ":")` is the documented contract
    # (references/detached-component-contracts.md, "local_checks results
    # file"), and is what the hand-interpolated echo this script replaced
    # emitted.  Readers parse JSON now (blocking_checks.py), so the separator
    # cannot break them -- but a file that does not match its own contract is
    # a trap for the next reader, and this one already sprung once
    # (kira-cloudflare 20260828-211346).
    with open(results_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
