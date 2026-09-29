"""Only a passing record proves a command already ran.

`/ilk-ship` Phase 1 subtracts "commands already run" (read_jsonl_commands)
from the release gate.  It counted records of ANY outcome, so a command whose
only record was a failure was skipped at release.  Since the record's label
became the failing check (gate_label_command, 2026-09-29), that would have
skipped precisely the command that was red.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gate_scope import read_jsonl_commands, subtract_complement  # noqa: E402

GREEN = "python3 -m pytest tests/test_a.py -q"
RED = "python3 -m pytest tests/test_b.py -q"


def _log(tmp_path: Path, *checks: dict) -> Path:
    p = tmp_path / ".ilk-loop.log"
    p.write_text("".join(json.dumps({"local_checks": [c]}) + "\n" for c in checks),
                 encoding="utf-8")
    return p


def test_a_failed_command_is_not_already_run(tmp_path):
    log = _log(tmp_path,
               {"slug": "s", "outcome": "pass", "command": GREEN},
               {"slug": "s", "outcome": "fail", "command": RED},
               {"slug": "s", "outcome": "inconclusive", "command": "bun run test"})
    assert read_jsonl_commands(log) == [GREEN]
    result = subtract_complement([GREEN, RED], read_jsonl_commands(log))
    assert RED in result.kept


def test_a_command_that_failed_then_passed_is_already_run(tmp_path):
    log = _log(tmp_path,
               {"slug": "s", "outcome": "fail", "command": RED},
               {"slug": "s", "outcome": "pass", "command": RED})
    assert read_jsonl_commands(log) == [RED]
