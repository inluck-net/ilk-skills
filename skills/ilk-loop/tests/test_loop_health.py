"""Tests for loop_health.py — startup-hang + hung-alive decisions (pure)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from loop_health import startup_hang_exceeded, hung_alive  # noqa: E402

LH = SCRIPTS_DIR / "loop_health.py"


# ── startup_hang_exceeded ───────────────────────────────────────────

class TestStartupHang:
    def test_before_threshold(self):
        # launched 5 min ago, threshold 30 -> not exceeded
        assert startup_hang_exceeded(1000.0, False, 1000.0 + 5 * 60, 30) is False

    def test_after_threshold_no_iter(self):
        # 31 min, no iteration -> hang
        assert startup_hang_exceeded(1000.0, False, 1000.0 + 31 * 60, 30) is True

    def test_after_threshold_with_iter(self):
        # 31 min but an iteration has started -> NOT a startup hang
        assert startup_hang_exceeded(1000.0, True, 1000.0 + 31 * 60, 30) is False

    def test_exactly_threshold(self):
        assert startup_hang_exceeded(1000.0, False, 1000.0 + 30 * 60, 30) is True


# ── hung_alive ──────────────────────────────────────────────────────

class TestHungAlive:
    def test_running_stale(self):
        assert hung_alive("running", 1000.0, 1000.0 + 31 * 60, 30) is True

    def test_running_recent(self):
        assert hung_alive("running", 1000.0, 1000.0 + 5 * 60, 30) is False

    def test_non_running_never_hung(self):
        # a terminal/other state is handled elsewhere; not "hung-alive"
        assert hung_alive("all-shipped", 1000.0, 1000.0 + 999 * 60, 30) is False
        assert hung_alive("no-progress", 1000.0, 1000.0 + 999 * 60, 30) is False

    def test_exactly_threshold(self):
        assert hung_alive("running", 1000.0, 1000.0 + 30 * 60, 30) is True


# ── CLI ─────────────────────────────────────────────────────────────

def _run(*args):
    return subprocess.run([sys.executable, str(LH), *args], capture_output=True, text=True)


def test_cli_startup_hang():
    r = _run("startup-hang", "--launch-ts", "1000", "--now", str(1000 + 31 * 60), "--threshold-min", "30")
    assert r.returncode == 0 and r.stdout.strip() == "1"
    r = _run("startup-hang", "--launch-ts", "1000", "--now", str(1000 + 31 * 60),
             "--threshold-min", "30", "--iter-seen")
    assert r.returncode == 0 and r.stdout.strip() == "0"


def test_cli_hung_alive():
    r = _run("hung-alive", "--state", "running", "--last-progress-ts", "1000",
             "--now", str(1000 + 31 * 60), "--threshold-min", "30")
    assert r.returncode == 0 and r.stdout.strip() == "1"
    r = _run("hung-alive", "--state", "all-shipped", "--last-progress-ts", "1000",
             "--now", str(1000 + 99 * 60), "--threshold-min", "30")
    assert r.returncode == 0 and r.stdout.strip() == "0"
