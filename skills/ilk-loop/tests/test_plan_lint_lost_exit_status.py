"""Tests for the exit-status-loss and broken-process-wait lints.

These lints enforce decomposition-principles.md §8 mechanically:
- lint_exit_status_discarded: flags `| tail`, `| head`, `| awk 'NR==1'` after
  a check command, which discards the upstream exit status.
- lint_broken_process_wait: flags `while ... | grep -q ... | grep -v grep`
  idioms where the loop condition tests the wrong command's exit status.

Positive fixtures use the REAL commands from gh-resolve run 20260818-154347.
Negative fixtures are correct patterns that must NOT fire.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from plan_lint import ALL_CHECKS, lint_exit_status_discarded, lint_broken_process_wait  # noqa: E402


# ── Positive fixtures: real commands that MUST trigger findings ───────────────

# The real command from gh-resolve 20260818-154347: pipes pytest output to
# tail, discarding pytest's exit status.
TAIL_DISCARD = """\
---
plan: fixture-tail-discard
status: pending
current_step: 0
---

# Sub-plan: fixture

## Steps

### Step 0 — run tests
```yaml
local_checks:
  - command: "ILK_ALLOW_FULL_SUITE=1 python3 -m pytest -q 2>&1 | tail -10"
    timeout: 300
```
- Run the tests.
"""

# The real broken process-wait from the same run.
BROKEN_WAIT = """\
---
plan: fixture-broken-wait
status: pending
current_step: 0
---

# Sub-plan: fixture

## Steps

### Step 0 — wait for tests
```yaml
local_checks:
  - command: "while ps aux | grep -q \\"pytest -q\\" | grep -v grep; do sleep 10; done; echo DONE"
    timeout: 300
```
- Wait for tests to finish.
"""

# head variant
HEAD_DISCARD = """\
---
plan: fixture-head-discard
status: pending
current_step: 0
---

# Sub-plan: fixture

## Steps

### Step 0 — check
```yaml
local_checks:
  - command: "python3 -m pytest -q 2>&1 | head -20"
    timeout: 300
```
- Check.
"""

# awk variant
AWK_DISCARD = """\
---
plan: fixture-awk-discard
status: pending
current_step: 0
---

# Sub-plan: fixture

## Steps

### Step 0 — check
```yaml
local_checks:
  - command: "python3 -m pytest -q 2>&1 | awk 'NR==1'"
    timeout: 300
```
- Check.
"""


# ── Negative fixtures: correct patterns that must NOT trigger ────────────────

# grep -q is the assertion itself — exit status is the point.
GREP_Q_OK = """\
---
plan: fixture-grep-q
status: pending
current_step: 0
---

# Sub-plan: fixture

## Steps

### Step 0 — check
```yaml
local_checks:
  - command: "grep -q 'PATTERN' file.txt"
    timeout: 30
```
- Check.
"""

# bash -o pipefail preserves upstream exit status.
PIPEFAIL_OK = """\
---
plan: fixture-pipefail
status: pending
current_step: 0
---

# Sub-plan: fixture

## Steps

### Step 0 — check
```yaml
local_checks:
  - command: "bash -o pipefail -c 'python3 -m pytest -q | head -20'"
    timeout: 300
```
- Check.
"""

# Pipeline ending in grep -q — the final command IS the assertion.
PIPE_TO_GREP_Q_OK = """\
---
plan: fixture-pipe-to-grep
status: pending
current_step: 0
---

# Sub-plan: fixture

## Steps

### Step 0 — check
```yaml
local_checks:
  - command: "python3 -m pytest -q 2>&1 | grep -q 'passed'"
    timeout: 300
```
- Check.
"""

# Simple command — no pipe at all.
SIMPLE_CMD_OK = """\
---
plan: fixture-simple
status: pending
current_step: 0
---

# Sub-plan: fixture

## Steps

### Step 0 — check
```yaml
local_checks:
  - command: "python3 -m pytest tests/test_foo.py -q"
    timeout: 300
```
- Check.
"""


# ── Tests: lint_exit_status_discarded ────────────────────────────────────────

class TestExitStatusDiscarded:
    """AC-1: flags commands whose exit status is discarded by trailing pipe."""

    def test_tail_discard_flagged(self) -> None:
        """The real command from run 20260818-154347 must be flagged."""
        findings = lint_exit_status_discarded(TAIL_DISCARD, "fixture-tail-discard")
        assert len(findings) >= 1, (
            f"Expected finding for '| tail -10' command: {findings}"
        )
        assert "tail" in findings[0].lower()

    def test_head_discard_flagged(self) -> None:
        """| head discards upstream exit status."""
        findings = lint_exit_status_discarded(HEAD_DISCARD, "fixture-head-discard")
        assert len(findings) >= 1
        assert "head" in findings[0].lower()

    def test_awk_nr1_discard_flagged(self) -> None:
        """| awk 'NR==1' discards upstream exit status."""
        findings = lint_exit_status_discarded(AWK_DISCARD, "fixture-awk-discard")
        assert len(findings) >= 1
        assert "awk" in findings[0].lower()


class TestExitStatusNegative:
    """AC-2: does NOT fire on correct patterns."""

    def test_grep_q_not_flagged(self) -> None:
        """grep -q is the assertion — exit status IS the contract."""
        findings = lint_exit_status_discarded(GREP_Q_OK, "fixture-grep-q")
        assert findings == [], f"grep -q should not be flagged: {findings}"

    def test_pipefail_not_flagged(self) -> None:
        """bash -o pipefail preserves upstream exit status."""
        findings = lint_exit_status_discarded(PIPEFAIL_OK, "fixture-pipefail")
        assert findings == [], f"pipefail wrapper should not be flagged: {findings}"

    def test_pipe_to_grep_q_not_flagged(self) -> None:
        """Pipeline ending in grep -q — the final command is the assertion."""
        findings = lint_exit_status_discarded(PIPE_TO_GREP_Q_OK, "fixture-pipe-to-grep")
        assert findings == [], f"pipe to grep -q should not be flagged: {findings}"

    def test_simple_command_not_flagged(self) -> None:
        """No pipe at all — nothing to flag."""
        findings = lint_exit_status_discarded(SIMPLE_CMD_OK, "fixture-simple")
        assert findings == [], f"Simple command should not be flagged: {findings}"


# ── Tests: lint_broken_process_wait ──────────────────────────────────────────

class TestBrokenProcessWait:
    """AC-3: flags the broken process-wait idiom."""

    def test_broken_wait_flagged(self) -> None:
        """The real command from run 20260818-154347 must be flagged."""
        findings = lint_broken_process_wait(BROKEN_WAIT, "fixture-broken-wait")
        assert len(findings) >= 1, (
            f"Expected finding for broken process-wait: {findings}"
        )


# ── Tests: registration ─────────────────────────────────────────────────────

class TestRegistration:
    """AC-4: both lints are registered in ALL_CHECKS."""

    def test_exit_status_discarded_registered(self) -> None:
        """lint_exit_status_discarded must be in ALL_CHECKS."""
        assert lint_exit_status_discarded in ALL_CHECKS, (
            "lint_exit_status_discarded not registered in ALL_CHECKS"
        )

    def test_broken_process_wait_registered(self) -> None:
        """lint_broken_process_wait must be in ALL_CHECKS."""
        assert lint_broken_process_wait in ALL_CHECKS, (
            "lint_broken_process_wait not registered in ALL_CHECKS"
        )
