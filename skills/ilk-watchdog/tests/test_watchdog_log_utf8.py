"""Tests for watchdog activity.log UTF-8 encoding — AC-2 for detached-output-fixes.

Verifies that non-ASCII characters (em-dash, arrow) written to activity.log
round-trip as UTF-8 without mojibake. Covers both the PowerShell writer
(BOM-free StreamWriter) and the bash writer (shell >> redirect).

AC-2: a log line containing an em-dash/arrow round-trips as UTF-8 (no
mojibake) when read back as UTF-8.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


# Non-ASCII test string: em-dash (U+2014), right arrow (U+2192), and
# Chinese characters (common in zh-CN Windows log output).
_NON_ASCII_LINE = "iteration 3 — stopped → 重试中"


# -- AC-2a: BOM-free UTF-8 round-trip (the PowerShell writer contract) -------


class TestUtf8RoundTrip:
    """Non-ASCII content must survive a write-read cycle as UTF-8."""

    def test_write_read_utf8_no_bom(self, tmp_path):
        """Write non-ASCII via BOM-free UTF-8 StreamWriter, read back as
        utf-8-sig. Content must match and file must not have a BOM."""
        log_file = tmp_path / "activity.log"

        # Write using the same method as watchdog.ps1's Write-Log:
        # [IO.StreamWriter]::new($path, $true, $enc) with
        # $enc = New-Object System.Text.UTF8Encoding($false)
        ps_script = (
            f"$enc = New-Object System.Text.UTF8Encoding($false)\n"
            f"$sw = [IO.StreamWriter]::new('{log_file}', $false, $enc)\n"
            f"$sw.WriteLine('{_NON_ASCII_LINE}')\n"
            f"$sw.Close()"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            pytest.skip(f"PowerShell not available: {result.stderr}")

        # Verify no BOM
        raw = log_file.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), (
            "activity.log has a UTF-8 BOM — must be BOM-free"
        )

        # Verify content round-trips
        content = log_file.read_text(encoding="utf-8-sig")
        assert _NON_ASCII_LINE in content, (
            f"Non-ASCII content did not round-trip. Got: {content!r}"
        )

    def test_utf8_sig_strips_bom_if_present(self, tmp_path):
        """utf-8-sig reader strips a BOM if one is present (defensive)."""
        log_file = tmp_path / "activity.log"
        log_file.write_text(_NON_ASCII_LINE, encoding="utf-8-sig")

        # Read back with utf-8-sig — should match
        content = log_file.read_text(encoding="utf-8-sig")
        assert _NON_ASCII_LINE in content

    def test_raw_utf8_bytes_correct(self, tmp_path):
        """Em-dash (U+2014) must encode as 0xE2 0x80 0x94 in UTF-8."""
        log_file = tmp_path / "activity.log"
        log_file.write_text(_NON_ASCII_LINE, encoding="utf-8")

        raw = log_file.read_bytes()
        # Em-dash bytes
        assert b"\xe2\x80\x94" in raw, (
            "Em-dash (U+2014) not found as UTF-8 bytes"
        )
        # Arrow bytes (U+2192 = E2 86 92)
        assert b"\xe2\x86\x92" in raw, (
            "Right arrow (U+2192) not found as UTF-8 bytes"
        )


# -- AC-2b: bash parity (shell >> redirect) -----------------------------------


class TestBashParity:
    """Bash's shell >> redirect writes UTF-8 on modern systems."""

    @pytest.mark.skipif(
        sys.platform == "win32" and not shutil.which("bash"),
        reason="bash not available on Windows",
    )
    def test_bash_echo_utf8_roundtrip(self, tmp_path):
        """echo with non-ASCII via bash >> must round-trip as UTF-8."""
        import shutil
        log_file = tmp_path / "activity.log"
        log_file.touch()

        # Same method as watchdog.sh line 494: echo "$line" >> "$ACTIVITY_LOG"
        result = subprocess.run(
            ["bash", "-c", f"echo '{_NON_ASCII_LINE}' >> '{log_file}'"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            pytest.skip(f"bash not available: {result.stderr}")

        content = log_file.read_text(encoding="utf-8-sig")
        assert _NON_ASCII_LINE in content, (
            f"Bash echo did not write UTF-8 correctly. Got: {content!r}"
        )


# -- AC-2c: no regression on ASCII-only lines ---------------------------------


class TestAsciiRegression:
    """ASCII-only lines must still work (no regression from the UTF-8 fix)."""

    def test_ascii_line_roundtrips(self, tmp_path):
        """Plain ASCII in activity.log round-trips cleanly."""
        log_file = tmp_path / "activity.log"
        line = "[2026-06-28 12:00:00] watchdog started, PID=12345"
        log_file.write_text(line + "\n", encoding="utf-8")

        content = log_file.read_text(encoding="utf-8-sig")
        assert line in content
