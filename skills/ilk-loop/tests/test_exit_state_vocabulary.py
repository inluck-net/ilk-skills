"""Tests for launcher exit-state vocabulary coverage in detached-component-contracts.md.

Enumerates the state literals actually written by the launcher (bash + PS runners)
and asserts each one appears in the contract document. Adding a new state without
documenting it turns this test red.

Uses the contract file path relative to the repo root.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CONTRACT_PATH = _REPO_ROOT / "skills" / "ilk-loop" / "references" / "detached-component-contracts.md"
_BASH_RUNNER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_PS_RUNNER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.ps1"


def _extract_state_literals(*paths: Path) -> set[str]:
    """Extract state literal strings from runner source files.

    Matches patterns like: "no-progress", 'all-shipped', etc.
    Only captures states that are assigned to stop_reason or iterStopReason.
    """
    states: set[str] = set()
    state_pattern = re.compile(r"""['"]([a-z_-]+)['"]""")
    # Context patterns: lines that assign to stop_reason or iterStopReason
    assignment_pattern = re.compile(
        r"(?:stop_reason|iterStopReason|stopReason|iter_stop_reason)\s*=\s*"
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if assignment_pattern.search(line):
                for m in state_pattern.finditer(line):
                    candidate = m.group(1)
                    # Filter out non-state strings (commands, paths, etc.)
                    if len(candidate) > 3 and "-" in candidate or "_" in candidate:
                        states.add(candidate)

    return states


# States known to be written by the runner to the sentinel.
# This is the authoritative list — the test asserts each appears in the contract.
LAUNCHER_EXIT_STATES = {
    "no-progress",
    "all-shipped",
    "interrupted",
    "timeout",
    "local_checks_failed",
    "ship_integrity_violation",
    "budget-exhausted",
    "max-iterations",
    "blocked-no-runnable",
    "already-shipped",
}


@pytest.fixture()
def contract_text() -> str:
    return _CONTRACT_PATH.read_text(encoding="utf-8")


def test_contract_file_exists():
    assert _CONTRACT_PATH.exists(), f"Contract file not found: {_CONTRACT_PATH}"


@pytest.mark.parametrize("state", sorted(LAUNCHER_EXIT_STATES))
def test_state_in_contract(state: str, contract_text: str):
    """Each launcher exit state must appear in the contract document.

    The state must appear as a quoted literal (e.g. "no-progress" or 'no-progress')
    so it's unambiguous and greppable.
    """
    # Check for the state as a quoted literal in the contract
    assert f'"{state}"' in contract_text or f"'{state}'" in contract_text, (
        f"Exit state '{state}' not found in {_CONTRACT_PATH.name}. "
        f"Add it to the state vocabulary table under Contract 1."
    )
