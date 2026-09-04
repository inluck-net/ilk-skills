"""Tests for YAML block-scalar gate commands.

A gate written as a YAML block scalar (>, >-, |, |-) must be folded into
its continuation lines by both the driver's parser and the lint extractor.
If a bare indicator reaches ``run_one``, the driver must refuse to execute
it rather than passing vacuously (``>-`` redirects to a file named ``-``
and exits 0).

Defect measured 2026-09-04 at 5cb36f7: both readers returned the literal
indicator string, and ``>-`` ran as ``bash -c '>-'`` which silently
succeeded, creating a junk file.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_local_checks as rlc
import plan_lint


# ---------------------------------------------------------------------------
# Fixture: a sub-plan whose gate uses a block scalar
# ---------------------------------------------------------------------------

_SUBPLAN_BLOCK_SCALAR = """\
---
plan: test-block-scalar
status: in-progress
current_step: 0
estimated_steps: 1
last_updated: 2026-09-04
local_checks:
  - command: >-
      bash -c '! grep -rn forbidden src/'
    timeout: 60
---

## Step 0

```yaml
local_checks:
  - command: >-
      python3 -m pytest tests/test_foo.py -q
    timeout: 120
```
"""

_EXPECTED_BODY = "bash -c '! grep -rn forbidden src/'"
_EXPECTED_STEP_CMD = "python3 -m pytest tests/test_foo.py -q"


# ---------------------------------------------------------------------------
# AC-1: parse_local_checks_block returns folded body for all four indicators
# ---------------------------------------------------------------------------

class TestParseLocalChecksBlockScalars:
    """AC-1: the driver parser folds block-scalar continuation lines."""

    @pytest.mark.parametrize("indicator", [">", ">-", "|", "|-"])
    def test_frontmatter_gate_returns_folded_body(self, indicator: str) -> None:
        yaml_text = (
            f"local_checks:\n"
            f"  - command: {indicator}\n"
            f"      echo hello\n"
            f"    timeout: 30\n"
        )
        result = rlc.parse_local_checks_block(yaml_text)
        assert len(result) == 1
        assert result[0]["command"] == "echo hello"

    @pytest.mark.parametrize("indicator", [">", ">-", "|", "|-"])
    def test_multiline_body_concatenated(self, indicator: str) -> None:
        yaml_text = (
            f"local_checks:\n"
            f"  - command: {indicator}\n"
            f"      bash -c '! grep -rn foo\n"
            f"        src/'\n"
            f"    timeout: 60\n"
        )
        result = rlc.parse_local_checks_block(yaml_text)
        assert len(result) == 1
        # The continuation lines should be folded into one command string.
        # Exact whitespace handling may vary, but the key content must appear.
        cmd = result[0]["command"]
        assert "grep" in cmd
        assert "foo" in cmd
        assert "src/" in cmd

    def test_non_block_scalar_command_unchanged(self) -> None:
        """A normal one-line command must still work."""
        yaml_text = (
            "local_checks:\n"
            "  - command: grep -q hello file.txt\n"
            "    timeout: 30\n"
        )
        result = rlc.parse_local_checks_block(yaml_text)
        assert len(result) == 1
        assert result[0]["command"] == "grep -q hello file.txt"


# ---------------------------------------------------------------------------
# AC-2: _extract_all_local_checks_commands returns the same folded body
# ---------------------------------------------------------------------------

class TestLintExtractorBlockScalars:
    """AC-2: the lint extractor must agree with the driver parser."""

    @pytest.mark.parametrize("indicator", [">", ">-", "|", "|-"])
    def test_frontmatter_gate_returns_folded_body(self, indicator: str) -> None:
        text = (
            "---\n"
            "plan: test\n"
            "status: in-progress\n"
            f"local_checks:\n"
            f"  - command: {indicator}\n"
            f"      echo hello\n"
            f"    timeout: 30\n"
            "---\n"
        )
        commands = plan_lint._extract_all_local_checks_commands(text)
        assert any("echo hello" in c for c in commands), (
            f"Expected 'echo hello' in {commands!r}"
        )

    @pytest.mark.parametrize("indicator", [">", ">-", "|", "|-"])
    def test_step_yaml_block_returns_folded_body(self, indicator: str) -> None:
        text = (
            "---\nplan: test\nstatus: in-progress\n---\n"
            "## Step 0\n\n"
            "```yaml\n"
            f"local_checks:\n"
            f"  - command: {indicator}\n"
            f"      echo hello\n"
            f"    timeout: 30\n"
            "```\n"
        )
        commands = plan_lint._extract_all_local_checks_commands(text)
        assert any("echo hello" in c for c in commands), (
            f"Expected 'echo hello' in {commands!r}"
        )


# ---------------------------------------------------------------------------
# AC-3: run_one refuses bare block-scalar indicators
# ---------------------------------------------------------------------------

_BARE_INDICATORS = [">", ">-", "|", "|-"]


class TestRunOneRefusesBareIndicators:
    """AC-3: a bare indicator must never reach subprocess.run."""

    @pytest.mark.parametrize("indicator", _BARE_INDICATORS)
    def test_bare_indicator_does_not_spawn_process(
        self, indicator: str, tmp_path: Path
    ) -> None:
        with patch("run_local_checks.subprocess") as mock_sub:
            result = rlc.run_one({"command": indicator}, "step", tmp_path)
        assert result.passed is False
        assert result.error is not None
        assert "block" in result.error.lower() or "parse" in result.error.lower()
        mock_sub.run.assert_not_called()


# ---------------------------------------------------------------------------
# AC-4: >- does not create a file named '-'
# ---------------------------------------------------------------------------

class TestNoJunkFileCreated:
    """AC-4: running a block-scalar indicator must not create a file named '-'."""

    def test_redirect_indicator_creates_no_junk_file(self, tmp_path: Path) -> None:
        rlc.run_one({"command": ">-"}, "step", tmp_path)
        assert not (tmp_path / "-").exists(), (
            "A file named '-' was created — the indicator was executed as a redirect"
        )

    @pytest.mark.parametrize("indicator", [">", "|", "|-"])
    def test_other_indicators_create_no_junk_file(
        self, indicator: str, tmp_path: Path
    ) -> None:
        rlc.run_one({"command": indicator}, "step", tmp_path)
        assert not (tmp_path / "-").exists()
