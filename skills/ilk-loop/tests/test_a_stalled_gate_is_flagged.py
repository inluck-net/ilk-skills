"""Sub-plan ``a-stalled-gate-is-flagged`` step 0 — pin the stall flag red-first.

AC-1 through AC-3 for the stall-detection feature.  All tests are marked
``xfail(strict=True)`` because ``bounded_run`` does not yet sample CPU
time or emit ``[gate-stalled]`` lines.

AC-1: a ``sleep 5`` command with ``_group_cpu_s`` stubbed to return a
      constant CPU → exactly one ``[gate-stalled]`` line on stderr and one
      ``gate-stalled`` event (patch ``write_event`` to record), and the
      command completes with ``returncode 0`` (not killed).
AC-2: a stub whose CPU rises 2.0 s per sample → no stall line, no event.
AC-3: a stub returning ``None`` every time → no stall line.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bounded_run  # noqa: E402


# ── AC-1: idle gate is flagged exactly once ──────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no stall detection yet")
def test_ac1_idle_gate_is_flagged_once(tmp_path: Path) -> None:
    """A sleeping command whose group CPU is flat gets one stall flag."""
    events: list[dict] = []

    def _fake_write_event(kind: str, project: str, **kwargs: object) -> None:
        events.append({"kind": kind, "project": project, **kwargs})

    # Stub _group_cpu_s to always return 0.0 (idle).
    with (
        patch.object(bounded_run, "_group_cpu_s", return_value=0.0),
        patch.object(bounded_run, "STALL_WINDOW_S", 2),
        patch.object(bounded_run, "STALL_SAMPLE_S", 0.5),
        patch.object(bounded_run, "STALL_CPU_S", 1.0),
        patch("ilk_audit.write_event", side_effect=_fake_write_event),
    ):
        rc, stdout, stderr, timed_out = bounded_run.run(
            "sleep 5", shell=True, timeout=10, cwd=str(tmp_path),
        )

    assert rc == 0, f"expected returncode 0, got {rc}"
    assert timed_out is False
    stall_lines = [l for l in stderr.splitlines() if "[gate-stalled]" in l]
    assert len(stall_lines) == 1, (
        f"expected 1 stall line, got {len(stall_lines)}: {stall_lines}"
    )
    assert len(events) == 1, (
        f"expected 1 stall event, got {len(events)}: {events}"
    )
    assert events[0]["kind"] == "gate-stalled"


# ── AC-2: busy gate is not flagged ──────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no stall detection yet")
def test_ac2_busy_gate_is_not_flagged(tmp_path: Path) -> None:
    """A command whose group CPU rises steadily gets no stall flag."""
    events: list[dict] = []
    sample_counter = {"n": 0}

    def _rising_cpu(pgid: int) -> float:
        sample_counter["n"] += 1
        return sample_counter["n"] * 2.0  # 2 s per sample

    def _fake_write_event(kind: str, project: str, **kwargs: object) -> None:
        events.append({"kind": kind, "project": project, **kwargs})

    with (
        patch.object(bounded_run, "_group_cpu_s", side_effect=_rising_cpu),
        patch.object(bounded_run, "STALL_WINDOW_S", 2),
        patch.object(bounded_run, "STALL_SAMPLE_S", 0.5),
        patch.object(bounded_run, "STALL_CPU_S", 1.0),
        patch("ilk_audit.write_event", side_effect=_fake_write_event),
    ):
        rc, stdout, stderr, timed_out = bounded_run.run(
            "sleep 5", shell=True, timeout=10, cwd=str(tmp_path),
        )

    assert rc == 0
    stall_lines = [l for l in stderr.splitlines() if "[gate-stalled]" in l]
    assert stall_lines == [], f"unexpected stall lines: {stall_lines}"
    assert events == [], f"unexpected stall events: {events}"


# ── AC-3: ps failure is not a stall ─────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="no stall detection yet")
def test_ac3_ps_failure_is_not_a_stall(tmp_path: Path) -> None:
    """When _group_cpu_s returns None (ps failed), no stall flag is emitted."""
    events: list[dict] = []

    def _fake_write_event(kind: str, project: str, **kwargs: object) -> None:
        events.append({"kind": kind, "project": project, **kwargs})

    with (
        patch.object(bounded_run, "_group_cpu_s", return_value=None),
        patch.object(bounded_run, "STALL_WINDOW_S", 2),
        patch.object(bounded_run, "STALL_SAMPLE_S", 0.5),
        patch.object(bounded_run, "STALL_CPU_S", 1.0),
        patch("ilk_audit.write_event", side_effect=_fake_write_event),
    ):
        rc, stdout, stderr, timed_out = bounded_run.run(
            "sleep 5", shell=True, timeout=10, cwd=str(tmp_path),
        )

    assert rc == 0
    stall_lines = [l for l in stderr.splitlines() if "[gate-stalled]" in l]
    assert stall_lines == [], f"unexpected stall lines: {stall_lines}"
    assert events == [], f"unexpected stall events: {events}"