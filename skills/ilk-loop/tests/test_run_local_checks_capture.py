"""Regression: run_local_checks must not turn a PASSING check into a false
failure when subprocess output capture/decoding is imperfect.

Root cause (2026-06-17, zh-CN/GBK Windows): `subprocess.run(..., text=True)`
without an explicit encoding decodes child output via the locale codec (cp936).
On a UTF-8-emitting command (`npm test`) that produced `cp.stdout is None`;
`_tail(None)` then raised `TypeError: object of type 'NoneType' has no len()`,
which the caller's `except` swallowed — discarding the real exit code (0) and
reporting the check as FAILED. A whole `npm test` (210 pass / 0 fail) was
reported as a B2-confirmed gate failure and stalled the loop.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_local_checks as rlc  # noqa: E402


class TestTailGuard:
    def test_none_returns_empty(self) -> None:
        # the exact crash site: len(None) must not raise
        assert rlc._tail(None, 2000) == ""

    def test_empty_returns_empty(self) -> None:
        assert rlc._tail("", 2000) == ""

    def test_short_passthrough(self) -> None:
        assert rlc._tail("short", 2000) == "short"

    def test_long_truncates(self) -> None:
        out = rlc._tail("x" * 5000, 2000)
        assert out.startswith("...[truncated]...")
        assert out.endswith("x" * 10)  # tail preserved


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
class TestRunOneCapture:
    def test_passing_command_with_utf8_output_passes(self, tmp_path: Path) -> None:
        # A passing command whose output is UTF-8 (✓, Chinese) must be captured
        # and reported passed — not crash the decoder into a false failure.
        check = {"command": "echo '✓ 周长 rectanglePerimeter 测试通过'; exit 0", "timeout": 30}
        r = rlc.run_one(check, scope="subplan", project=tmp_path)
        assert r.exit_code == 0
        assert r.passed is True
        assert r.error in (None, "")

    def test_failing_command_still_fails(self, tmp_path: Path) -> None:
        # Guard against over-correction: a real non-zero exit must still fail.
        check = {"command": "echo nope; exit 1", "timeout": 30}
        r = rlc.run_one(check, scope="subplan", project=tmp_path)
        assert r.exit_code == 1
        assert r.passed is False
