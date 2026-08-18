"""Contract test: wait_for_background_output.sh polls for the exit marker.

A backgrounded command's `.output` file may be read before the command
finishes writing. The helper must:
  - wait for the `[exited with code` marker rather than trusting an early read
  - report the recorded exit code once the marker appears
  - report `inconclusive` (not pass/fail) when the bound expires without a marker
  - never treat an empty file as a finished result

Context: gh-resolve run `20260818-154347`, iter-01. The worker read the
`.output` file while pytest was still writing, got empty-because-early, and
re-launched the suite three times — burning ~24 of 43 iteration minutes.

Fixture shape captured from that run's output tail:
  `2825 passed, 2 skipped, 2 warnings in 725.16s (0:12:05)`
  `[exited with code 0]`
"""
import os
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

HELPER = Path(__file__).resolve().parent.parent / "scripts" / "wait_for_background_output.sh"


def _run_helper(file_path: Path, *, timeout_sec: int = 5, poll_ms: int = 100) -> subprocess.CompletedProcess:
    """Run the helper against a file, with a short bound for test speed."""
    return subprocess.run(
        ["bash", str(HELPER), str(file_path),
         "--timeout", str(timeout_sec),
         "--poll-ms", str(poll_ms)],
        capture_output=True, text=True, timeout=timeout_sec + 5,
    )


# ── fixtures ─────────────────────────────────────────────────────────────────

REAL_OUTPUT_TAIL = textwrap.dedent("""\
        warnings
      test_something.py::test_one
      test_something.py::test_two

    -------- generated html report --------
    -------- generated json report --------

    ======================== 2825 passed, 2 skipped, 2 warnings in 725.16s (0:12:05) ========================
    [exited with code 0]
""")

REAL_OUTPUT_TAIL_NONZERO = textwrap.dedent("""\
    ======================== 4 failed, 2821 passed, 2 skipped in 700.00s (0:11:40) ========================
    [exited with code 1]
""")


# ── AC-1: marker-present → reports the recorded exit code ────────────────────

class TestMarkerPresent:
    """When the file contains `[exited with code N]`, the helper reports N."""

    def test_marker_present_zero(self, tmp_path: Path) -> None:
        """AC-1a: file ends with `[exited with code 0]` → exit 0, stdout '0'."""
        out = tmp_path / "bri8ryggs.output"
        out.write_text(REAL_OUTPUT_TAIL, encoding="utf-8")
        result = _run_helper(out)
        assert result.returncode == 0, (
            f"expected exit 0, got {result.returncode}. "
            f"stderr: {result.stderr[:500]}"
        )
        assert result.stdout.strip() == "0", (
            f"expected stdout '0', got {result.stdout.strip()!r}"
        )

    def test_marker_present_nonzero(self, tmp_path: Path) -> None:
        """AC-1b: file ends with `[exited with code 1]` → exit 1, stdout '1'."""
        out = tmp_path / "bri8ryggs.output"
        out.write_text(REAL_OUTPUT_TAIL_NONZERO, encoding="utf-8")
        result = _run_helper(out)
        assert result.returncode == 1, (
            f"expected exit 1, got {result.returncode}. "
            f"stderr: {result.stderr[:500]}"
        )
        assert result.stdout.strip() == "1", (
            f"expected stdout '1', got {result.stdout.strip()!r}"
        )

    def test_marker_present_42(self, tmp_path: Path) -> None:
        """AC-1c: arbitrary non-zero exit code is preserved."""
        out = tmp_path / "test.output"
        out.write_text("some output\n[exited with code 42]\n", encoding="utf-8")
        result = _run_helper(out)
        assert result.returncode == 42
        assert result.stdout.strip() == "42"


# ── AC-2: present-but-empty → waits, does not report a result ────────────────

class TestEmptyFileWaits:
    """When the file exists but is empty, the helper waits for the marker.

    This is the exact case the 2026-08-18 worker misread: empty-because-early
    must not be the same outcome as empty-because-finished.
    """

    def test_empty_file_waits_then_succeeds(self, tmp_path: Path) -> None:
        """An empty file that later gains a marker → helper waits and reports."""
        out = tmp_path / "pending.output"
        out.write_text("", encoding="utf-8")

        # Write the marker after a short delay (simulating the command finishing)
        def _write_marker_later():
            time.sleep(0.5)
            out.write_text(REAL_OUTPUT_TAIL, encoding="utf-8")

        import threading
        t = threading.Thread(target=_write_marker_later)
        t.start()
        result = _run_helper(out, timeout_sec=5)
        t.join()
        assert result.returncode == 0, (
            f"expected exit 0 after marker appeared, got {result.returncode}. "
            f"stderr: {result.stderr[:500]}"
        )
        assert result.stdout.strip() == "0"

    def test_empty_file_no_marker_reports_inconclusive(self, tmp_path: Path) -> None:
        """An empty file that never gains a marker → inconclusive on bound."""
        out = tmp_path / "stuck.output"
        out.write_text("", encoding="utf-8")
        result = _run_helper(out, timeout_sec=1, poll_ms=100)
        # The helper should exit with a special code for inconclusive
        assert result.returncode != 0, (
            "expected non-zero exit for inconclusive, got 0"
        )
        assert "inconclusive" in result.stderr.lower() or "inconclusive" in result.stdout.lower(), (
            f"expected 'inconclusive' in output. stdout: {result.stdout[:300]}, "
            f"stderr: {result.stderr[:300]}"
        )


# ── AC-3: never-marked → reports inconclusive naming the bound ────────────────

class TestNeverMarked:
    """When the file has content but never gains the marker, report inconclusive."""

    def test_nonempty_no_marker_reports_inconclusive(self, tmp_path: Path) -> None:
        """A file with output but no exit marker → inconclusive, naming the bound."""
        out = tmp_path / "incomplete.output"
        out.write_text(
            "running tests...\n"
            "test_one PASSED\ntest_two PASSED\ntest_three FAILED\n",
            encoding="utf-8",
        )
        result = _run_helper(out, timeout_sec=1, poll_ms=100)
        assert result.returncode != 0, (
            "expected non-zero exit for inconclusive, got 0"
        )
        stderr_lower = result.stderr.lower()
        stdout_lower = result.stdout.lower()
        combined = stderr_lower + stdout_lower
        assert "inconclusive" in combined, (
            f"expected 'inconclusive' in output. stdout: {result.stdout[:300]}, "
            f"stderr: {result.stderr[:300]}"
        )

    def test_inconclusive_names_the_bound(self, tmp_path: Path) -> None:
        """The inconclusive message names the timeout bound that was hit."""
        out = tmp_path / "slow.output"
        out.write_text("still running...\n", encoding="utf-8")
        bound = 2
        result = _run_helper(out, timeout_sec=bound, poll_ms=100)
        combined = (result.stdout + result.stderr).lower()
        # The bound should be mentioned as a number (seconds)
        assert str(bound) in combined, (
            f"expected bound '{bound}' mentioned in output. "
            f"stdout: {result.stdout[:300]}, stderr: {result.stderr[:300]}"
        )


# ── AC-4: completed file → reports recorded exit code ────────────────────────

class TestCompletedFile:
    """A completed file (marker present) reports the exact recorded exit code."""

    def test_zero_exit_produces_zero(self, tmp_path: Path) -> None:
        """AC-4a: `[exited with code 0]` → helper exit 0."""
        out = tmp_path / "done.output"
        out.write_text(REAL_OUTPUT_TAIL, encoding="utf-8")
        result = _run_helper(out)
        assert result.returncode == 0

    def test_nonzero_exit_produces_nonzero(self, tmp_path: Path) -> None:
        """AC-4b: `[exited with code 1]` → helper exit 1."""
        out = tmp_path / "failed.output"
        out.write_text(REAL_OUTPUT_TAIL_NONZERO, encoding="utf-8")
        result = _run_helper(out)
        assert result.returncode == 1

    def test_exit_code_preserved_exactly(self, tmp_path: Path) -> None:
        """AC-4c: exit code 2 is preserved as 2, not clamped or mapped."""
        out = tmp_path / "partial.output"
        out.write_text("partial results\n[exited with code 2]\n", encoding="utf-8")
        result = _run_helper(out)
        assert result.returncode == 2
        assert result.stdout.strip() == "2"
