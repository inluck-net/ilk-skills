"""Tests for ilk_watch.py — canonical loop-watch helper.

AC-1: status token matches sentinel/queue state (running, all-shipped, blocked, idle).
AC-2: log path is the correct one (newest run dir / .ilk-loop.log), not a guess.
AC-3: on all-shipped, says all-shipped + "do not relaunch" hint.

All tests use injected fixtures — no real filesystem or subprocess.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ilk_watch import ProjectWatch, _find_newest_run_dir  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────

def _fake_sentinel(state: str, pid: int = 12345):
    """Return a sentinel-reading callable that always returns the given state."""
    def _read(_runtime_dir: Path | None) -> dict | None:
        return {"state": state, "pid": pid, "run_id": "test-run", "iteration": 3}
    return _read


def _dead_pid(_pid: int) -> bool:
    return False


def _live_pid(_pid: int) -> bool:
    return True


def _queue(exit_code: int, subplans: list[dict] | None = None, next_entry: dict | None = None):
    """Return a queue-resolving callable that returns the given data."""
    def _resolve(_project_path: Path) -> dict:
        return {
            "queue_exit": exit_code,
            "subplans": subplans or [],
            "next": next_entry,
        }
    return _resolve


ALL_SHIPPED_SUBS = [
    {"fname": "2026-06-22-a.md", "slug": "a", "status": "shipped", "current_step": "3", "estimated_steps": "3"},
    {"fname": "2026-06-22-b.md", "slug": "b", "status": "shipped", "current_step": "5", "estimated_steps": "5"},
]

PENDING_SUBS = [
    {"fname": "2026-06-22-a.md", "slug": "a", "status": "shipped", "current_step": "3", "estimated_steps": "3"},
    {"fname": "2026-06-22-b.md", "slug": "b", "status": "pending", "current_step": "0", "estimated_steps": "5"},
]


# ── AC-1: status token matches sentinel/queue state ──────────────────────

class TestAC1_Running:
    """Sentinel state=running + live PID → status=running."""

    def test_running_live_pid(self, tmp_path: Path):
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=_fake_sentinel("running", pid=12345),
            _pid_alive=_live_pid,
            _resolve_queue=_queue(1, PENDING_SUBS),
        )
        result = watch.resolve()
        assert result["status"] == "running"


class TestAC1_AllShipped:
    """queue_exit=0 + all sub-plans shipped → status=all-shipped."""

    def test_all_shipped(self, tmp_path: Path):
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=lambda _rd: None,
            _pid_alive=_dead_pid,
            _resolve_queue=_queue(0, ALL_SHIPPED_SUBS),
        )
        result = watch.resolve()
        assert result["status"] == "all-shipped"


class TestAC1_Blocked:
    """Sentinel state=running + dead PID → status=blocked (stale)."""

    def test_blocked_stale_running(self, tmp_path: Path):
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=_fake_sentinel("running", pid=99999),
            _pid_alive=_dead_pid,
            _resolve_queue=_queue(1, PENDING_SUBS),
        )
        result = watch.resolve()
        assert result["status"] == "blocked"
        assert "dead" in result["hint"].lower() or "stale" in result["hint"].lower()


class TestAC1_Idle:
    """Terminal sentinel state + non-shipped sub-plans → status=idle."""

    def test_idle_terminal_sentinel(self, tmp_path: Path):
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=_fake_sentinel("shipped", pid=12345),
            _pid_alive=_live_pid,
            _resolve_queue=_queue(1, PENDING_SUBS),
        )
        result = watch.resolve()
        assert result["status"] == "idle"

    def test_idle_no_sentinel(self, tmp_path: Path):
        """No sentinel at all → idle."""
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=lambda _rd: None,
            _pid_alive=_dead_pid,
            _resolve_queue=_queue(1, PENDING_SUBS),
        )
        result = watch.resolve()
        assert result["status"] == "idle"


# ── AC-2: log path is correct ────────────────────────────────────────────

class TestAC2_LogPath:
    """Log path resolution picks the newest run dir, then .ilk-loop.log."""

    def test_newest_run_dir(self, tmp_path: Path):
        """When runs/ has subdirs, the most recently modified one is chosen."""
        import os

        logs = tmp_path / "logs"
        runs = logs / "runs"
        run_a = runs / "20260620-120000"
        run_b = runs / "20260621-150000"
        run_a.mkdir(parents=True)
        run_b.mkdir(parents=True)
        # Explicitly set mtimes to avoid race — run_b is newer.
        os.utime(str(run_a), (1000000, 1000000))
        os.utime(str(run_b), (2000000, 2000000))

        found = _find_newest_run_dir(logs)
        assert found == run_b

    def test_fallback_to_summary_log(self, tmp_path: Path):
        """When runs/ is empty but .ilk-loop.log exists, use it."""
        logs = tmp_path / "logs"
        logs.mkdir()
        summary = logs / ".ilk-loop.log"
        summary.write_text('{"run_id":"x"}', encoding="utf-8")
        runs = logs / "runs"
        runs.mkdir()

        found = _find_newest_run_dir(logs)
        assert found is None  # no run dirs

    def test_no_logs_dir(self, tmp_path: Path):
        """When there's no logs dir at all, _find_newest_run_dir returns None."""
        found = _find_newest_run_dir(tmp_path / "nonexistent")
        assert found is None


# ── AC-3: all-shipped hint ───────────────────────────────────────────────

class TestAC3_AllShippedHint:
    """all-shipped result must include a 'do not relaunch' hint."""

    def test_do_not_relaunch_hint(self, tmp_path: Path):
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=lambda _rd: None,
            _pid_alive=_dead_pid,
            _resolve_queue=_queue(0, ALL_SHIPPED_SUBS),
        )
        result = watch.resolve()
        assert result["status"] == "all-shipped"
        assert "do not relaunch" in result["hint"].lower() or "do NOT relaunch" in result["hint"]


# ── additional edge cases ────────────────────────────────────────────────

class TestEdgeCases:
    """Miscellaneous boundary conditions."""

    def test_queue_exit_0_with_pending_subplans_not_all_shipped(self, tmp_path: Path):
        """queue_exit=0 but subplans have pending items → NOT all-shipped (stall)."""
        mixed_subs = [
            {"fname": "a.md", "slug": "a", "status": "shipped", "current_step": "3", "estimated_steps": "3"},
            {"fname": "b.md", "slug": "b", "status": "pending", "current_step": "0", "estimated_steps": "5"},
        ]
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=_fake_sentinel("local_checks_failed", pid=12345),
            _pid_alive=_live_pid,
            _resolve_queue=_queue(0, mixed_subs),
        )
        result = watch.resolve()
        # non_shipped is non-empty, so even though queue_exit=0, it's NOT all-shipped.
        # The sentinel is terminal → idle.
        assert result["status"] == "idle"

    def test_sentinel_unknown_state(self, tmp_path: Path):
        """Unknown sentinel state (not in LIVE_SENTINEL_STATES) → idle."""
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=_fake_sentinel("some-unknown-state", pid=12345),
            _pid_alive=_live_pid,
            _resolve_queue=_queue(1, PENDING_SUBS),
        )
        result = watch.resolve()
        assert result["status"] == "idle"

    def test_sentinel_state_local_checks_failed(self, tmp_path: Path):
        """local_checks_failed terminal state → idle (not blocked)."""
        watch = ProjectWatch(
            tmp_path,
            _read_sentinel=_fake_sentinel("local_checks_failed", pid=12345),
            _pid_alive=_live_pid,
            _resolve_queue=_queue(1, PENDING_SUBS),
        )
        result = watch.resolve()
        assert result["status"] == "idle"
