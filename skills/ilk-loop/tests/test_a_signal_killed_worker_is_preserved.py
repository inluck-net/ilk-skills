"""A worker killed by a signal is an incomplete iteration, like a timeout.

Only gtimeout's exit 124 used to set completed=0, and only completed=0
WIP-preserves the dirty tree.  A worker stopped with SIGTERM (exit 143)
counted as completed and lost its uncommitted work; on 2026-09-28
gh-resolve-59 had to send SIGALRM to gtimeout to keep a 4-file diff (run
20260928-185416, WIP 00d4f2e).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


def _classify(rc: int) -> str:
    return subprocess.run(
        ["bash", "-c", f"set -uo pipefail; export ILK_DOTSOURCE_ONLY=1; source '{_RUNNER}'; _classify_agent_exit {rc}"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def test_timeout_keeps_its_marker() -> None:
    assert _classify(124) == "0 -1"


def test_signal_kills_are_incomplete_and_keep_their_status() -> None:
    for rc in (130, 137, 139, 143):
        assert _classify(rc) == f"0 {rc}", rc


def test_ordinary_exits_are_complete() -> None:
    for rc in (0, 1, 2, 127):
        assert _classify(rc) == f"1 {rc}", rc


def test_the_iteration_uses_the_classifier() -> None:
    text = _RUNNER.read_text(encoding="utf-8")
    assert 'read -r completed exit_code <<< "$(_classify_agent_exit "$exit_code")"' in text
